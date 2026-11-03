from datetime import UTC, datetime

from fastapi import APIRouter, Depends, HTTPException, Request, Response, status
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core import audit
from app.core.auth import COOKIE_NAME, client_ip, current_user, end_session, revoke_user_sessions, start_session
from app.core.login_rate_limit import login_limiter
from app.core.security import hash_password, verify_password
from app.db.models import User
from app.db.session import get_session
from app.models.schemas import LoginRequest, PasswordChange, UserRead

router = APIRouter(prefix="/api/auth", tags=["auth"])

_BAD = "Invalid email or password"


@router.post("/login", response_model=UserRead)
async def login(body: LoginRequest, request: Request, response: Response, db: AsyncSession = Depends(get_session)):
    now = datetime.now(UTC)
    ip = client_ip(request) or "unknown"
    if not login_limiter.reserve(ip, body.email):
        verify_password(None, body.password)  # retain the same response timing and avoid email enumeration
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, _BAD)

    reserved = True
    try:
        user = (await db.execute(select(User).where(User.email == body.email))).scalar_one_or_none()

        # Always run the hash check, even for unknown emails, to keep timing uniform.
        ok = verify_password(user.password_hash if user else None, body.password)
        if not (ok and user and user.is_active):
            login_limiter.finish_failure(ip, body.email)
            reserved = False
            if user:
                # Clear any previous account-wide lockout state; new attempts are IP-scoped.
                user.failed_logins = 0
                user.locked_until = None
                await db.commit()
            await audit.record(db, "login_failed", user=user, email=body.email, request=request)
            raise HTTPException(status.HTTP_401_UNAUTHORIZED, _BAD)

        login_limiter.finish_success(ip, body.email)
        reserved = False
        user.failed_logins = 0
        user.locked_until = None
        user.last_login_at = now
        await db.commit()
        await start_session(db, response, request, user)
        await audit.record(db, "login", user=user, request=request)
        return user
    finally:
        if reserved:
            login_limiter.release(ip, body.email)


@router.post("/logout", status_code=status.HTTP_204_NO_CONTENT)
async def logout(request: Request, response: Response, db: AsyncSession = Depends(get_session)):
    raw = request.cookies.get(COOKIE_NAME)
    await end_session(db, response, raw)


@router.get("/me", response_model=UserRead)
async def me(user: User = Depends(current_user)):
    return user


@router.post("/password", status_code=status.HTTP_204_NO_CONTENT)
async def change_password(
    body: PasswordChange, request: Request, user: User = Depends(current_user), db: AsyncSession = Depends(get_session)
):
    if not verify_password(user.password_hash, body.current_password):
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "Current password is incorrect")
    user.password_hash = hash_password(body.new_password)
    await db.commit()
    # Sign out every other device; keep this one.
    await revoke_user_sessions(db, user.id, keep_raw=request.cookies.get(COOKIE_NAME))
    await audit.record(db, "password_change", user=user, request=request)
