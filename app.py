import os
import uuid
import shutil
from pathlib import Path
from datetime import datetime
from contextlib import asynccontextmanager

import uvicorn
from fastapi import FastAPI, Request, Form, Depends, UploadFile, File, HTTPException
from fastapi.responses import HTMLResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from sqlalchemy.orm import Session

from database import init_db, get_db, User, RoleEnum, GenderEnum
from auth import (
    hash_password, verify_password,
    create_access_token, get_current_user,
)

# ── App setup ────────────────────────────────────────────────────────────────
BASE_DIR = Path(__file__).resolve().parent

# On Vercel only /tmp is writable; use it for uploads too
IS_VERCEL = bool(os.environ.get("VERCEL"))
UPLOAD_DIR = Path("/tmp/uploads") if IS_VERCEL else BASE_DIR / "static" / "uploads"
UPLOAD_DIR.mkdir(parents=True, exist_ok=True)

ALLOWED_IMAGE_TYPES = {"image/jpeg", "image/png", "image/gif", "image/webp"}
MAX_PHOTO_SIZE = 5 * 1024 * 1024  # 5 MB


def _seed_admin():
    """Create the default admin account if it doesn't exist yet."""
    from database import SessionLocal
    db = SessionLocal()
    try:
        if not db.query(User).filter(User.role == RoleEnum.admin).first():
            admin = User(
                username="admin",
                email="admin@pinaycupid.com",
                hashed_password=hash_password("admin123"),
                role=RoleEnum.admin,
                is_active=True,
                full_name="Site Administrator",
            )
            db.add(admin)
            db.commit()
    except Exception:
        db.rollback()
    finally:
        db.close()


@asynccontextmanager
async def lifespan(app: FastAPI):
    init_db()
    _seed_admin()
    yield


app = FastAPI(title="Pinay Cupid", lifespan=lifespan)

app.mount("/static", StaticFiles(directory=str(BASE_DIR / "static")), name="static")
templates = Jinja2Templates(directory=str(BASE_DIR / "templates"))


# ── Flash message helpers ────────────────────────────────────────────────────
# Uses a signed cookie so flash works on stateless/serverless deployments too.
import json
from itsdangerous import URLSafeSerializer

_FLASH_SECRET = os.environ.get("SECRET_KEY", "pinay-cupid-flash-secret-2024")
_signer = URLSafeSerializer(_FLASH_SECRET, salt="flash")


class FlashMessage:
    def __init__(self, text: str, type: str = "info"):
        self.text = text
        self.type = type


def _encode_flash(messages: list[FlashMessage]) -> str:
    data = [{"text": m.text, "type": m.type} for m in messages]
    return _signer.dumps(data)


def _decode_flash(cookie_val: str) -> list[FlashMessage]:
    try:
        data = _signer.loads(cookie_val)
        return [FlashMessage(d["text"], d["type"]) for d in data]
    except Exception:
        return []


def get_flashes(request: Request) -> list[FlashMessage]:
    raw = request.cookies.get("_flash")
    if not raw:
        return []
    return _decode_flash(raw)


def _redirect_with_flash(request: Request, url: str, text: str, type: str = "info") -> RedirectResponse:
    resp = RedirectResponse(url, status_code=302)
    existing = get_flashes(request)
    existing.append(FlashMessage(text, type))
    resp.set_cookie("_flash", _encode_flash(existing), httponly=True, max_age=60, samesite="lax")
    return resp


def _clear_flash(response) -> None:
    response.delete_cookie("_flash")


def render(
    request: Request,
    template: str,
    context: dict,
    db: Session,
    status_code: int = 200,
):
    current_user = get_current_user(request, db)
    messages = get_flashes(request)
    ctx = {"request": request, "current_user": current_user, "messages": messages}
    ctx.update(context)
    resp = templates.TemplateResponse(template, ctx, status_code=status_code)
    # Clear flash cookie after reading
    resp.delete_cookie("_flash")
    return resp



# ─────────────────────────────────────────────────────────────────────────────
# PUBLIC ROUTES
# ─────────────────────────────────────────────────────────────────────────────

@app.get("/", response_class=HTMLResponse)
def home(request: Request, db: Session = Depends(get_db)):
    recent_members = (
        db.query(User)
        .filter(User.is_active == True, User.role == RoleEnum.user)
        .order_by(User.created_at.desc())
        .limit(8)
        .all()
    )
    return render(request, "index.html", {"recent_members": recent_members}, db)


# ── REGISTER ─────────────────────────────────────────────────────────────────

@app.get("/register", response_class=HTMLResponse)
def register_get(request: Request, db: Session = Depends(get_db)):
    return render(request, "register.html", {"form": {}}, db)


@app.post("/register", response_class=HTMLResponse)
def register_post(
    request: Request,
    db: Session = Depends(get_db),
    username: str = Form(...),
    email: str = Form(...),
    password: str = Form(...),
    confirm_password: str = Form(...),
    full_name: str = Form(""),
    age: str = Form(""),
    gender: str = Form(""),
    location: str = Form(""),
):
    form_data = {
        "username": username, "email": email,
        "full_name": full_name, "age": age,
        "gender": gender, "location": location,
    }

    if password != confirm_password:
        return render(request, "register.html", {"form": form_data, "messages": [FlashMessage("Passwords do not match.", "error")]}, db, 400)

    if len(password) < 6:
        return render(request, "register.html", {"form": form_data, "messages": [FlashMessage("Password must be at least 6 characters.", "error")]}, db, 400)

    if db.query(User).filter(User.username == username).first():
        return render(request, "register.html", {"form": form_data, "messages": [FlashMessage("Username already taken. Please choose another.", "error")]}, db, 400)

    if db.query(User).filter(User.email == email).first():
        return render(request, "register.html", {"form": form_data, "messages": [FlashMessage("Email already registered. Try logging in.", "error")]}, db, 400)

    age_int = None
    if age:
        try:
            age_int = int(age)
            if age_int < 18:
                return render(request, "register.html", {"form": form_data, "messages": [FlashMessage("You must be 18 or older to register.", "error")]}, db, 400)
        except ValueError:
            age_int = None

    gender_enum = None
    if gender in ("female", "male", "other"):
        gender_enum = GenderEnum(gender)

    user = User(
        username=username,
        email=email,
        hashed_password=hash_password(password),
        role=RoleEnum.user,
        full_name=full_name or None,
        age=age_int,
        gender=gender_enum,
        location=location or None,
    )
    db.add(user)
    db.commit()
    db.refresh(user)

    token = create_access_token({"sub": str(user.id)})
    resp = _redirect_with_flash(
        request, "/profile/edit",
        f"Welcome to Pinay Cupid, {user.username}! Complete your profile to attract matches. 💕",
        "success",
    )
    resp.set_cookie("access_token", token, httponly=True, max_age=86400 * 7)
    return resp


# ── LOGIN ─────────────────────────────────────────────────────────────────────

@app.get("/login", response_class=HTMLResponse)
def login_get(request: Request, db: Session = Depends(get_db)):
    return render(request, "login.html", {"form": {}, "is_admin": False}, db)


@app.post("/login", response_class=HTMLResponse)
def login_post(
    request: Request,
    db: Session = Depends(get_db),
    username: str = Form(...),
    password: str = Form(...),
    role: str = Form("user"),
):
    user = (
        db.query(User)
        .filter((User.username == username) | (User.email == username))
        .first()
    )

    if not user or not verify_password(password, user.hashed_password):
        return render(
            request, "login.html",
            {"form": {"username": username}, "is_admin": role == "admin",
             "messages": [FlashMessage("Invalid username or password.", "error")]},
            db, 400,
        )

    if not user.is_active:
        return render(request, "login.html",
                      {"form": {}, "is_admin": False,
                       "messages": [FlashMessage("Your account has been deactivated. Contact support.", "error")]},
                      db, 400)

    if role == "admin" and user.role != RoleEnum.admin:
        return render(
            request, "login.html",
            {"form": {"username": username}, "is_admin": True,
             "messages": [FlashMessage("You do not have admin privileges.", "error")]},
            db, 403,
        )

    # Update last login
    user.last_login = datetime.utcnow()
    db.commit()

    token = create_access_token({"sub": str(user.id)})
    redirect_url = "/admin" if user.role == RoleEnum.admin else "/"
    resp = _redirect_with_flash(
        request, redirect_url,
        f"Welcome back, {user.username}! 💕", "success",
    )
    resp.set_cookie("access_token", token, httponly=True, max_age=86400 * 7)
    return resp


# ── LOGOUT ────────────────────────────────────────────────────────────────────

@app.get("/logout")
def logout(request: Request):
    resp = RedirectResponse("/", status_code=302)
    resp.delete_cookie("access_token")
    return resp


# ── BROWSE ────────────────────────────────────────────────────────────────────

@app.get("/browse", response_class=HTMLResponse)
def browse(
    request: Request,
    db: Session = Depends(get_db),
    q: str = "",
    gender: str = "",
    age_min: str = "",
    age_max: str = "",
    location: str = "",
    page: int = 1,
):
    PAGE_SIZE = 20
    query = db.query(User).filter(User.is_active == True, User.role == RoleEnum.user)

    if q:
        like = f"%{q}%"
        query = query.filter((User.username.ilike(like)) | (User.full_name.ilike(like)))
    if gender in ("female", "male", "other"):
        query = query.filter(User.gender == GenderEnum(gender))
    if age_min:
        try:
            query = query.filter(User.age >= int(age_min))
        except ValueError:
            pass
    if age_max:
        try:
            query = query.filter(User.age <= int(age_max))
        except ValueError:
            pass
    if location:
        query = query.filter(User.location.ilike(f"%{location}%"))

    total = query.count()
    total_pages = max(1, (total + PAGE_SIZE - 1) // PAGE_SIZE)
    page = max(1, min(page, total_pages))
    members = query.order_by(User.created_at.desc()).offset((page - 1) * PAGE_SIZE).limit(PAGE_SIZE).all()

    # Build query string without 'page'
    params = []
    if q:        params.append(f"q={q}")
    if gender:   params.append(f"gender={gender}")
    if age_min:  params.append(f"age_min={age_min}")
    if age_max:  params.append(f"age_max={age_max}")
    if location: params.append(f"location={location}")
    query_string = "&".join(params)

    return render(request, "browse.html", {
        "members": members,
        "total": total,
        "page": page,
        "total_pages": total_pages,
        "query_string": query_string,
        "filters": {"q": q, "gender": gender, "age_min": age_min, "age_max": age_max, "location": location},
    }, db)


# ── PROFILE VIEW ───────────────────────────────────────────────────────────────

@app.get("/profile", response_class=HTMLResponse)
def my_profile(request: Request, db: Session = Depends(get_db)):
    current_user = get_current_user(request, db)
    if not current_user:
        return RedirectResponse("/login", status_code=302)
    return RedirectResponse(f"/profile/{current_user.id}", status_code=302)


# ── PROFILE EDIT ───────────────────────────────────────────────────────────────

@app.get("/profile/edit", response_class=HTMLResponse)
def edit_profile_get(request: Request, db: Session = Depends(get_db)):
    current_user = get_current_user(request, db)
    if not current_user:
        return RedirectResponse("/login", status_code=302)
    return render(request, "profile_edit.html", {}, db)


# ── PROFILE VIEW ── (must come AFTER /profile/edit to avoid routing conflict) ──

@app.get("/profile/{user_id}", response_class=HTMLResponse)
def view_profile(user_id: int, request: Request, db: Session = Depends(get_db)):
    user = db.query(User).filter(User.id == user_id, User.is_active == True).first()
    if not user:
        raise HTTPException(status_code=404, detail="Profile not found")
    return render(request, "profile_view.html", {"user": user}, db)


@app.post("/profile/edit", response_class=HTMLResponse)
def edit_profile_post(
    request: Request,
    db: Session = Depends(get_db),
    full_name: str = Form(""),
    age: str = Form(""),
    gender: str = Form(""),
    location: str = Form(""),
    religion: str = Form(""),
    occupation: str = Form(""),
    bio: str = Form(""),
    looking_for: str = Form(""),
):
    current_user = get_current_user(request, db)
    if not current_user:
        return RedirectResponse("/login", status_code=302)

    current_user.full_name = full_name or None
    current_user.location = location or None
    current_user.religion = religion or None
    current_user.occupation = occupation or None
    current_user.bio = bio or None
    current_user.looking_for = looking_for or None

    if age:
        try:
            current_user.age = int(age)
        except ValueError:
            pass
    else:
        current_user.age = None

    if gender in ("female", "male", "other"):
        current_user.gender = GenderEnum(gender)
    elif not gender:
        current_user.gender = None

    db.commit()
    return _redirect_with_flash(
        request, f"/profile/{current_user.id}",
        "Profile updated successfully! 💕", "success"
    )


# ── PHOTO UPLOAD ───────────────────────────────────────────────────────────────

@app.post("/profile/photo")
async def upload_photo(
    request: Request,
    db: Session = Depends(get_db),
    photo: UploadFile = File(...),
):
    current_user = get_current_user(request, db)
    if not current_user:
        return RedirectResponse("/login", status_code=302)

    if photo.content_type not in ALLOWED_IMAGE_TYPES:
        return _redirect_with_flash(
            request, "/profile/edit",
            "Invalid file type. Please upload a JPG, PNG, GIF, or WEBP image.", "error"
        )

    contents = await photo.read()
    if len(contents) > MAX_PHOTO_SIZE:
        return _redirect_with_flash(
            request, "/profile/edit",
            "Image too large. Maximum allowed size is 5MB.", "error"
        )

    ext = Path(photo.filename).suffix.lower() or ".jpg"
    filename = f"user_{current_user.id}_{uuid.uuid4().hex[:8]}{ext}"
    filepath = UPLOAD_DIR / filename

    # Remove old photo
    if current_user.profile_photo:
        old = UPLOAD_DIR / current_user.profile_photo
        if old.exists():
            old.unlink()

    with open(filepath, "wb") as f:
        f.write(contents)

    current_user.profile_photo = filename
    db.commit()

    return _redirect_with_flash(
        request, "/profile/edit",
        "Profile photo updated! 📸", "success"
    )


# ─────────────────────────────────────────────────────────────────────────────
# ADMIN ROUTES
# ─────────────────────────────────────────────────────────────────────────────

def _admin_render(request: Request, template: str, context: dict, db: Session, status_code: int = 200):
    admin = get_current_user(request, db)
    if not admin or admin.role != RoleEnum.admin:
        raise HTTPException(status_code=302, headers={"Location": "/login"})
    messages = get_flashes(request)
    ctx = {"request": request, "admin": admin, "messages": messages}
    ctx.update(context)
    return templates.TemplateResponse(template, ctx, status_code=status_code)


@app.get("/admin", response_class=HTMLResponse)
def admin_dashboard(request: Request, db: Session = Depends(get_db)):
    admin = get_current_user(request, db)
    if not admin or admin.role != RoleEnum.admin:
        return RedirectResponse("/login", status_code=302)

    total_users  = db.query(User).filter(User.role == RoleEnum.user).count()
    active_users = db.query(User).filter(User.role == RoleEnum.user, User.is_active == True).count()
    female_users = db.query(User).filter(User.role == RoleEnum.user, User.gender == GenderEnum.female).count()
    male_users   = db.query(User).filter(User.role == RoleEnum.user, User.gender == GenderEnum.male).count()

    today = datetime.utcnow().date()
    new_today = db.query(User).filter(
        User.role == RoleEnum.user,
        User.created_at >= datetime(today.year, today.month, today.day),
    ).count()

    recent_users = (
        db.query(User)
        .order_by(User.created_at.desc())
        .limit(10)
        .all()
    )

    return _admin_render(request, "admin/dashboard.html", {
        "stats": {
            "total_users": total_users,
            "active_users": active_users,
            "female_users": female_users,
            "male_users": male_users,
            "new_today": new_today,
        },
        "recent_users": recent_users,
    }, db)


@app.get("/admin/users", response_class=HTMLResponse)
def admin_users(
    request: Request,
    db: Session = Depends(get_db),
    q: str = "",
    filter: str = "",
    role: str = "",
    page: int = 1,
):
    admin = get_current_user(request, db)
    if not admin or admin.role != RoleEnum.admin:
        return RedirectResponse("/login", status_code=302)

    PAGE_SIZE = 25
    query = db.query(User)

    if q:
        like = f"%{q}%"
        query = query.filter(
            (User.username.ilike(like)) | (User.email.ilike(like)) | (User.full_name.ilike(like))
        )
    if filter == "active":
        query = query.filter(User.is_active == True)
    elif filter == "inactive":
        query = query.filter(User.is_active == False)
    if role == "user":
        query = query.filter(User.role == RoleEnum.user)
    elif role == "admin":
        query = query.filter(User.role == RoleEnum.admin)

    total = query.count()
    total_pages = max(1, (total + PAGE_SIZE - 1) // PAGE_SIZE)
    page = max(1, min(page, total_pages))
    users = query.order_by(User.created_at.desc()).offset((page - 1) * PAGE_SIZE).limit(PAGE_SIZE).all()

    params = []
    if q:      params.append(f"q={q}")
    if filter: params.append(f"filter={filter}")
    if role:   params.append(f"role={role}")
    query_string = "&".join(params)

    return _admin_render(request, "admin/users.html", {
        "users": users,
        "total": total,
        "page": page,
        "total_pages": total_pages,
        "query_string": query_string,
        "q": q,
        "filter": filter,
        "role_filter": role,
    }, db)


@app.get("/admin/users/{user_id}/toggle")
def admin_toggle_user(user_id: int, request: Request, db: Session = Depends(get_db)):
    admin = get_current_user(request, db)
    if not admin or admin.role != RoleEnum.admin:
        return RedirectResponse("/login", status_code=302)

    user = db.query(User).filter(User.id == user_id).first()
    if not user:
        return _redirect_with_flash(request, "/admin/users", "User not found.", "error")
    if user.id == admin.id:
        return _redirect_with_flash(request, "/admin/users", "You cannot deactivate your own account.", "error")

    user.is_active = not user.is_active
    db.commit()
    action = "activated" if user.is_active else "deactivated"
    return _redirect_with_flash(
        request, "/admin/users",
        f"User {user.username} has been {action}.", "success"
    )


@app.get("/admin/users/{user_id}/delete")
def admin_delete_user(user_id: int, request: Request, db: Session = Depends(get_db)):
    admin = get_current_user(request, db)
    if not admin or admin.role != RoleEnum.admin:
        return RedirectResponse("/login", status_code=302)

    user = db.query(User).filter(User.id == user_id).first()
    if not user:
        return _redirect_with_flash(request, "/admin/users", "User not found.", "error")
    if user.id == admin.id:
        return _redirect_with_flash(request, "/admin/users", "You cannot delete your own account.", "error")

    # Remove profile photo if exists
    if user.profile_photo:
        photo_path = UPLOAD_DIR / user.profile_photo
        if photo_path.exists():
            photo_path.unlink()

    username = user.username
    db.delete(user)
    db.commit()
    return _redirect_with_flash(
        request, "/admin/users",
        f"User {username} has been permanently deleted.", "success"
    )


@app.get("/admin/users/{user_id}/make-admin")
def admin_promote(user_id: int, request: Request, db: Session = Depends(get_db)):
    admin = get_current_user(request, db)
    if not admin or admin.role != RoleEnum.admin:
        return RedirectResponse("/login", status_code=302)

    user = db.query(User).filter(User.id == user_id).first()
    if not user:
        return _redirect_with_flash(request, "/admin/users", "User not found.", "error")

    user.role = RoleEnum.admin
    db.commit()
    return _redirect_with_flash(
        request, "/admin/users",
        f"{user.username} has been promoted to admin.", "success"
    )


# ─────────────────────────────────────────────────────────────────────────────
if __name__ == "__main__":
    uvicorn.run("app:app", host="127.0.0.1", port=8000, reload=True)
