from fnmatch import fnmatch

import asyncpg
from fastapi import APIRouter, Depends, HTTPException, Request, Response, status
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core import audit
from app.core.auth import can_manage, can_use, current_user, usable_connection
from app.core.config import get_settings
from app.core.security import decrypt, encrypt
from app.db.connections import pools, verify
from app.db.models import Connection, User
from app.db.session import get_session
from app.models.schemas import ConnectionCreate, ConnectionRead, ConnectionUpdate, DatabaseSwitch

router = APIRouter(prefix="/api/connections", tags=["connections"])


async def get_pool_or_http(conn_id: str):
    """Resolve a pool, translating an unknown id into a 404."""
    try:
        return await pools.get(conn_id)
    except KeyError:
        raise HTTPException(404, "Connection not found")


def _check_host(host: str) -> None:
    allowed = get_settings().allowed_hosts
    if allowed and not any(fnmatch(host.lower(), p.lower()) for p in allowed):
        raise HTTPException(status.HTTP_403_FORBIDDEN, "Host is not on the allowlist")


async def _verify_or_400(cfg, password: str) -> None:
    try:
        await verify(cfg, password)
    except (OSError, TimeoutError, asyncpg.PostgresError, asyncpg.InterfaceError) as e:
        raise HTTPException(400, f"Could not connect: {e}")


def to_read(row: Connection, user: User) -> ConnectionRead:
    return ConnectionRead(
        id=row.id, name=row.name, host=row.host, port=row.port, database=row.database, user=row.user,
        ssl_mode=row.ssl_mode, read_only=row.read_only, shared=row.shared,
        owned=row.owner_id == user.id, can_manage=can_manage(row, user),
    )


@router.get("", response_model=list[ConnectionRead])
async def list_connections(user: User = Depends(current_user), db: AsyncSession = Depends(get_session)):
    rows = await db.scalars(
        select(Connection).where((Connection.owner_id == user.id) | Connection.shared).order_by(Connection.name)
    )
    return [to_read(r, user) for r in rows]


@router.post("", response_model=ConnectionRead, status_code=status.HTTP_201_CREATED)
async def create_connection(
    body: ConnectionCreate,
    request: Request,
    user: User = Depends(current_user),
    db: AsyncSession = Depends(get_session),
):
    """Verify the credentials against the target DB, then save them (encrypted) under the caller."""
    if body.shared and user.role != "admin":
        raise HTTPException(status.HTTP_403_FORBIDDEN, "Only admins can share a connection with the team")
    _check_host(body.host)
    await _verify_or_400(body, body.password)
    row = Connection(**body.model_dump(exclude={"password"}), password_enc=encrypt(body.password), owner_id=user.id)
    db.add(row)
    await db.commit()
    await audit.record(db, "connection_create", user=user, conn=row, request=request)
    return to_read(row, user)


@router.patch("/{conn_id}", response_model=ConnectionRead)
async def update_connection(
    body: ConnectionUpdate,
    request: Request,
    row: Connection = Depends(usable_connection),
    user: User = Depends(current_user),
    db: AsyncSession = Depends(get_session),
):
    if not can_manage(row, user):
        raise HTTPException(status.HTTP_403_FORBIDDEN, "Only the owner (or an admin, for shared connections) can change this")
    changes = body.model_dump(exclude_unset=True)
    if changes.get("shared") and user.role != "admin":
        raise HTTPException(status.HTTP_403_FORBIDDEN, "Only admins can share a connection with the team")

    if changes.get("password"):
        await _verify_or_400(row, changes["password"])
        row.password_enc = encrypt(changes["password"])
    if changes.get("name"):
        row.name = changes["name"]
    if changes.get("shared") is not None:
        row.shared = changes["shared"]
    if changes.get("read_only") is not None:
        row.read_only = changes["read_only"]
    await db.commit()
    if changes.get("password") or "read_only" in changes:
        await pools.evict(row.id)  # rebuild with the new credentials / session settings
    detail = ", ".join(k if k != "password" else "password" for k in changes)
    await audit.record(db, "connection_update", user=user, conn=row, detail=detail, request=request)
    return to_read(row, user)


@router.delete("/{conn_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_connection(
    request: Request,
    row: Connection = Depends(usable_connection),
    user: User = Depends(current_user),
    db: AsyncSession = Depends(get_session),
):
    if not can_manage(row, user):
        raise HTTPException(status.HTTP_403_FORBIDDEN, "Only the owner (or an admin, for shared connections) can delete this")
    await audit.record(db, "connection_delete", user=user, conn=row, request=request)
    await pools.evict(row.id)
    await db.delete(row)
    await db.commit()


@router.post("/{conn_id}/database", response_model=ConnectionRead)
async def switch_database(
    body: DatabaseSwitch,
    request: Request,
    base: Connection = Depends(usable_connection),
    user: User = Depends(current_user),
    db: AsyncSession = Depends(get_session),
):
    """Same host, user and password, different database. Returns the sibling connection (created if
    needed, with the same owner and sharing as the base) so each database keeps its own pool."""
    password = decrypt(base.password_enc)
    candidates = await db.scalars(
        select(Connection).where(
            Connection.host == base.host,
            Connection.port == base.port,
            Connection.user == base.user,
            Connection.ssl_mode == base.ssl_mode,
            Connection.read_only == base.read_only,
            Connection.database == body.database,
            Connection.owner_id == base.owner_id,
            Connection.shared == base.shared,
        )
    )
    row = next((c for c in candidates if can_use(c, user) and decrypt(c.password_enc) == password), None)
    if row is None:
        if not can_manage(base, user):
            await audit.record(
                db, "connection_db_switch_denied", user=user, conn=base,
                detail=body.database, error="A shared database sibling must be created by its owner or an admin.", request=request,
            )
            raise HTTPException(status.HTTP_403_FORBIDDEN, "Only the owner or an admin can add a database to a shared connection")
        target = ConnectionCreate(
            name=body.name or f"{base.name} / {body.database}"[:100],
            host=base.host, port=base.port, database=body.database, user=base.user,
            ssl_mode=base.ssl_mode, read_only=base.read_only, password=password,
        )
        try:
            _check_host(base.host)
            await _verify_or_400(target, password)
        except HTTPException as exc:
            await audit.record(
                db, "connection_db_switch_denied", user=user, conn=base,
                detail=body.database, error=str(exc.detail), request=request,
            )
            raise
        row = Connection(
            **target.model_dump(exclude={"password", "shared"}),
            password_enc=encrypt(password), owner_id=base.owner_id, shared=base.shared,
        )
        db.add(row)
        await db.commit()
    await audit.record(
        db, "connection_db_switch", user=user, conn=row,
        detail=f"from {base.database} to {body.database}", request=request,
    )
    return to_read(row, user)
