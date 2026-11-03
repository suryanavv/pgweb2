"""Manual (Beekeeper-style) transactions.

A transaction pins one asyncpg connection from the saved connection's pool until it is committed,
rolled back, or reaped for idling. State lives in this process, so run a single worker.
"""

import asyncio
import contextlib
import logging
import re
import time
import uuid
from dataclasses import dataclass, field
from datetime import UTC, datetime

import asyncpg

from app.core.config import get_settings

log = logging.getLogger("pgweb2.tx")

# Transaction control belongs to the Commit/Rollback buttons; letting a script end the transaction
# underneath us would leave the pinned connection in a state the UI doesn't know about.
_CONTROL = re.compile(r"^\s*(begin|start\s+transaction|commit|end|rollback|abort|savepoint|release|prepare\s+transaction)\b", re.I)


class TxError(Exception):
    def __init__(self, status: int, message: str):
        super().__init__(message)
        self.status = status
        self.message = message


def reject_control_statement(sql: str) -> None:
    if _CONTROL.match(sql):
        raise TxError(400, "Transaction control statements are not allowed here. Use the Commit and Rollback buttons.")


@dataclass
class TxSession:
    id: str
    user_id: str
    conn_id: str
    pool: asyncpg.Pool
    conn: asyncpg.Connection
    tx: asyncpg.transaction.Transaction
    started_at: datetime
    last_used: float = field(default_factory=time.monotonic)
    last_used_at: datetime = field(default_factory=lambda: datetime.now(UTC))
    started_mono: float = field(default_factory=time.monotonic)
    statements: int = 0
    lock: asyncio.Lock = field(default_factory=asyncio.Lock)

    def touch(self) -> None:
        self.last_used = time.monotonic()
        self.last_used_at = datetime.now(UTC)

    def info(self) -> dict:
        s = get_settings()
        idle_left = s.tx_idle_timeout_seconds - (time.monotonic() - self.last_used)
        age_left = s.tx_max_age_seconds - (time.monotonic() - self.started_mono)
        return {
            "id": self.id,
            "connection_id": self.conn_id,
            "started_at": self.started_at,
            "last_used_at": self.last_used_at,
            "statements": self.statements,
            "expires_in": max(0, int(min(idle_left, age_left))),
        }


class TransactionManager:
    def __init__(self) -> None:
        self._open: dict[str, TxSession] = {}
        self._reaper: asyncio.Task | None = None
        self._reservation_lock = asyncio.Lock()
        self._starting_by_user: dict[str, int] = {}

    def start_reaper(self) -> None:
        self._reaper = asyncio.create_task(self._reap_loop())

    async def _reap_loop(self) -> None:
        while True:
            await asyncio.sleep(15)
            try:
                await self.reap_once()
            except Exception:  # never let the reaper die
                log.exception("tx reaper failed")

    async def reap_once(self) -> None:
        s = get_settings()
        now = time.monotonic()
        for ts in list(self._open.values()):
            if ts.lock.locked():
                continue  # a statement is running; its own touch() will refresh the clock
            if now - ts.last_used > s.tx_idle_timeout_seconds or now - ts.started_mono > s.tx_max_age_seconds:
                log.info("rolling back tx %s (idle/age limit)", ts.id)
                await self.finish(ts, commit=False)

    def list_for(self, user_id: str) -> list[TxSession]:
        return [t for t in self._open.values() if t.user_id == user_id]

    def get(self, tx_id: str, user_id: str) -> TxSession:
        ts = self._open.get(tx_id)
        # Same 404 whether it never existed, expired, or belongs to someone else.
        if ts is None or ts.user_id != user_id:
            raise TxError(404, "Transaction not found. It may have been rolled back after sitting idle.")
        return ts

    async def begin(self, user_id: str, conn_id: str, pool: asyncpg.Pool) -> TxSession:
        limit = max(1, get_settings().max_open_tx_per_user)
        async with self._reservation_lock:
            pending = self._starting_by_user.get(user_id, 0)
            if len(self.list_for(user_id)) + pending >= limit:
                raise TxError(409, f"You already have {limit} open transactions. Commit or roll one back first.")
            self._starting_by_user[user_id] = pending + 1
        try:
            try:
                conn = await pool.acquire(timeout=5)
            except (TimeoutError, asyncio.TimeoutError):
                raise TxError(503, "No free database connections right now. Try again shortly.")
            try:
                tx = conn.transaction()
                await tx.start()
            except BaseException:
                await pool.release(conn)
                raise
            ts = TxSession(uuid.uuid4().hex, user_id, conn_id, pool, conn, tx, datetime.now(UTC))
            self._open[ts.id] = ts
            return ts
        finally:
            async with self._reservation_lock:
                remaining = self._starting_by_user.get(user_id, 1) - 1
                if remaining:
                    self._starting_by_user[user_id] = remaining
                else:
                    self._starting_by_user.pop(user_id, None)

    async def finish(self, ts: TxSession, *, commit: bool) -> None:
        """Commit or roll back, then always hand the connection back. Safe to call twice."""
        if self._open.pop(ts.id, None) is None:
            return
        try:
            async with ts.lock:
                if commit:
                    await ts.tx.commit()
                else:
                    await ts.tx.rollback()
        finally:
            with contextlib.suppress(Exception):
                await ts.pool.release(ts.conn)

    async def drop_dead(self, ts: TxSession) -> None:
        """The server connection is gone: forget the session without trying to talk to it."""
        if self._open.pop(ts.id, None) is not None:
            with contextlib.suppress(Exception):
                await ts.pool.release(ts.conn)

    async def rollback_where(self, pred) -> None:
        for ts in [t for t in self._open.values() if pred(t)]:
            with contextlib.suppress(Exception):
                await self.finish(ts, commit=False)

    async def close_all(self) -> None:
        if self._reaper:
            self._reaper.cancel()
        await self.rollback_where(lambda _: True)


txs = TransactionManager()
