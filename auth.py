import os
import hashlib
from datetime import datetime, timedelta

from passlib.context import CryptContext
from jose import JWTError, jwt
from fastapi import Request
from bson import ObjectId

SECRET_KEY = os.environ.get("SECRET_KEY", "pinay-cupid-secret-key-2024-change-in-prod")
ALGORITHM  = "HS256"
ACCESS_TOKEN_EXPIRE_MINUTES = 60 * 24 * 7   # 7 days

pwd_context = CryptContext(schemes=["bcrypt"], deprecated="auto")


def _prepare_password(password: str) -> str:
    """
    bcrypt silently truncates at 72 bytes and newer versions raise an error.
    SHA-256 the password first so any length works safely.
    """
    return hashlib.sha256(password.encode("utf-8")).hexdigest()


def hash_password(password: str) -> str:
    return pwd_context.hash(_prepare_password(password))


def verify_password(plain: str, hashed: str) -> bool:
    return pwd_context.verify(_prepare_password(plain), hashed)


def create_access_token(data: dict, expires_delta: timedelta | None = None) -> str:
    to_encode = data.copy()
    expire    = datetime.utcnow() + (expires_delta or timedelta(minutes=ACCESS_TOKEN_EXPIRE_MINUTES))
    to_encode.update({"exp": expire})
    return jwt.encode(to_encode, SECRET_KEY, algorithm=ALGORITHM)


def decode_token(token: str) -> dict | None:
    try:
        return jwt.decode(token, SECRET_KEY, algorithms=[ALGORITHM])
    except JWTError:
        return None


def get_current_user(request: Request, db):
    """
    Reads the JWT from the access_token cookie, looks up the user in MongoDB,
    and returns a database.User wrapper or None.
    """
    from database import get_users, User

    token = request.cookies.get("access_token")
    if not token:
        return None
    payload = decode_token(token)
    if not payload:
        return None
    user_id = payload.get("sub")
    if not user_id:
        return None
    try:
        oid = ObjectId(user_id)
    except Exception:
        return None

    users = get_users(db)
    doc   = users.find_one({"_id": oid, "is_active": True})
    return User(doc) if doc else None
