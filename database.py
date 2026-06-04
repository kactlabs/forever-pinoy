from sqlalchemy import create_engine, Column, Integer, String, Boolean, DateTime, Text, Enum
from sqlalchemy.orm import declarative_base, sessionmaker
from datetime import datetime
import enum
import os

# On Vercel the filesystem is read-only except /tmp
_DB_PATH = "/tmp/pinay_cupid.db" if os.environ.get("VERCEL") else "./pinay_cupid.db"
DATABASE_URL = f"sqlite:///{_DB_PATH}"

engine = create_engine(DATABASE_URL, connect_args={"check_same_thread": False})
SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)
Base = declarative_base()


class GenderEnum(str, enum.Enum):
    female = "female"
    male = "male"
    other = "other"


class RoleEnum(str, enum.Enum):
    user = "user"
    admin = "admin"


class User(Base):
    __tablename__ = "users"

    id = Column(Integer, primary_key=True, index=True)
    username = Column(String(50), unique=True, index=True, nullable=False)
    email = Column(String(120), unique=True, index=True, nullable=False)
    hashed_password = Column(String(256), nullable=False)
    role = Column(Enum(RoleEnum), default=RoleEnum.user, nullable=False)
    is_active = Column(Boolean, default=True)
    is_verified = Column(Boolean, default=False)
    created_at = Column(DateTime, default=datetime.utcnow)

    # Profile fields
    full_name = Column(String(100), nullable=True)
    age = Column(Integer, nullable=True)
    gender = Column(Enum(GenderEnum), nullable=True)
    location = Column(String(100), nullable=True)
    bio = Column(Text, nullable=True)
    looking_for = Column(String(200), nullable=True)
    religion = Column(String(50), nullable=True)
    occupation = Column(String(100), nullable=True)
    profile_photo = Column(String(255), nullable=True)
    last_login = Column(DateTime, nullable=True)


def get_db():
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()


def init_db():
    Base.metadata.create_all(bind=engine)
