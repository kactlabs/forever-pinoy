"""
database.py — MongoDB connection + helpers.

Set MONGODB_URI env var to your Atlas connection string.
Falls back to a local MongoDB instance (mongodb://localhost:27017) for dev.
"""
import os
from datetime import datetime
from bson import ObjectId
from pymongo import MongoClient, ASCENDING, DESCENDING
from pymongo.collection import Collection
from dotenv import load_dotenv
load_dotenv()

# ── Connection ────────────────────────────────────────────────────────────────
MONGODB_URI = os.environ.get("MONGODB_URI", "mongodb://localhost:27017")
_db_env     = os.environ.get("MONGODB_DB", "").strip()

# Extract DB name from URI path if MONGODB_DB not explicitly set
# e.g. mongodb+srv://user:pass@host/pinay_cupid?... → "pinay_cupid"
if not _db_env:
    try:
        _path = MONGODB_URI.split("?")[0]          # strip query params
        _name = _path.rsplit("/", 1)[-1].strip()   # take last path segment
        _db_env = _name if _name else "pinay_cupid"
    except Exception:
        _db_env = "pinay_cupid"

DB_NAME = _db_env or "pinay_cupid"

_client: MongoClient | None = None


def get_client() -> MongoClient:
    global _client
    if _client is None:
        _client = MongoClient(
            MONGODB_URI,
            serverSelectionTimeoutMS=8000,
            connectTimeoutMS=8000,
            socketTimeoutMS=10000,
            tlsAllowInvalidCertificates=False,
        )
    return _client


def get_db():
    """FastAPI dependency — yields the MongoDB database object."""
    client = get_client()
    db     = client[DB_NAME]
    try:
        yield db
    finally:
        pass   # PyMongo manages its own connection pool; nothing to close per-request


def get_users(db) -> Collection:
    return db["users"]


# ── Indexes (called once at startup) ─────────────────────────────────────────
def ensure_indexes(db):
    users = get_users(db)
    users.create_index("username", unique=True, background=True)
    users.create_index("email",    unique=True, background=True)
    users.create_index([("created_at", DESCENDING)], background=True)
    users.create_index("role",      background=True)
    users.create_index("is_active", background=True)
    users.create_index("gender",    background=True)


# ── User helper class ─────────────────────────────────────────────────────────
# Wraps a raw MongoDB document dict so templates can use dot-notation
# (e.g. user.username, user.role, user.gender.value)

class _StrEnum:
    """Mimics SQLAlchemy enum's .value attribute so templates don't break."""
    def __init__(self, val: str):
        self._val = val

    @property
    def value(self) -> str:
        return self._val

    def __str__(self) -> str:
        return self._val

    def __eq__(self, other):
        if isinstance(other, _StrEnum):
            return self._val == other._val
        return self._val == other


class User:
    """
    Thin wrapper around a MongoDB user document.
    Exposes all fields as attributes so Jinja templates work without changes.
    """
    def __init__(self, doc: dict):
        self._doc = doc

    # ── identity ──────────────────────────────────────────────────────────────
    @property
    def id(self) -> str:
        return str(self._doc["_id"])

    @property
    def username(self) -> str:
        return self._doc.get("username", "")

    @property
    def email(self) -> str:
        return self._doc.get("email", "")

    @property
    def hashed_password(self) -> str:
        return self._doc.get("hashed_password", "")

    @property
    def role(self) -> _StrEnum:
        return _StrEnum(self._doc.get("role", "user"))

    @property
    def is_active(self) -> bool:
        return self._doc.get("is_active", True)

    @property
    def is_verified(self) -> bool:
        return self._doc.get("is_verified", False)

    @property
    def created_at(self) -> datetime:
        return self._doc.get("created_at", datetime.utcnow())

    @property
    def last_login(self) -> datetime | None:
        return self._doc.get("last_login")

    # ── profile ───────────────────────────────────────────────────────────────
    @property
    def full_name(self) -> str | None:
        return self._doc.get("full_name")

    @property
    def age(self) -> int | None:
        return self._doc.get("age")

    @property
    def gender(self) -> _StrEnum | None:
        v = self._doc.get("gender")
        return _StrEnum(v) if v else None

    @property
    def location(self) -> str | None:
        return self._doc.get("location")

    @property
    def country(self) -> str | None:
        return self._doc.get("country")

    @property
    def state(self) -> str | None:
        return self._doc.get("state")

    @property
    def city(self) -> str | None:
        return self._doc.get("city")

    @property
    def bio(self) -> str | None:
        return self._doc.get("bio")

    @property
    def looking_for(self) -> str | None:
        return self._doc.get("looking_for")

    @property
    def religion(self) -> str | None:
        return self._doc.get("religion")

    @property
    def occupation(self) -> str | None:
        return self._doc.get("occupation")

    @property
    def profile_photo(self) -> str | None:
        return self._doc.get("profile_photo")

    @property
    def dob(self) -> str | None:
        return self._doc.get("dob")

    @property
    def intent(self) -> str | None:
        return self._doc.get("intent")

    @property
    def age_pref_min(self) -> int:
        return self._doc.get("age_pref_min", 18)

    @property
    def age_pref_max(self) -> int:
        return self._doc.get("age_pref_max", 45)

    # ── raw doc access ────────────────────────────────────────────────────────
    def raw(self) -> dict:
        return self._doc
