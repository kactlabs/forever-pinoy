import os
import uuid
import json
from pathlib import Path
from datetime import datetime
from contextlib import asynccontextmanager

import uvicorn
from fastapi import FastAPI, Request, Form, Depends, UploadFile, File, HTTPException
from fastapi.responses import HTMLResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from itsdangerous import URLSafeSerializer
from sqlalchemy.orm import Session

from database import init_db, get_db, User, RoleEnum, GenderEnum
from auth import hash_password, verify_password, create_access_token, get_current_user

# ── Constants ─────────────────────────────────────────────────────────────────
BASE_DIR     = Path(__file__).resolve().parent
IS_VERCEL    = bool(os.environ.get("VERCEL"))
UPLOAD_DIR   = Path("/tmp/uploads") if IS_VERCEL else BASE_DIR / "static" / "uploads"
UPLOAD_DIR.mkdir(parents=True, exist_ok=True)

ALLOWED_IMAGE_TYPES = {"image/jpeg", "image/png", "image/gif", "image/webp"}
MAX_PHOTO_SIZE      = 5 * 1024 * 1024   # 5 MB

_FLASH_SECRET = os.environ.get("SECRET_KEY", "pinay-cupid-secret-2024")
_signer       = URLSafeSerializer(_FLASH_SECRET, salt="flash")


# ── Lifespan ──────────────────────────────────────────────────────────────────
def _seed_admin():
    from database import SessionLocal
    db = SessionLocal()
    try:
        init_db()
        if not db.query(User).filter(User.role == RoleEnum.admin).first():
            db.add(User(
                username="admin",
                email="admin@pinaycupid.com",
                hashed_password=hash_password("admin123"),
                role=RoleEnum.admin,
                is_active=True,
                full_name="Site Administrator",
            ))
            db.commit()
    except Exception as exc:
        db.rollback()
        print(f"[startup] seed error: {exc}")
    finally:
        db.close()


@asynccontextmanager
async def lifespan(app: FastAPI):
    _seed_admin()
    yield


# ── App ───────────────────────────────────────────────────────────────────────
app = FastAPI(title="Pinay Cupid", lifespan=lifespan)
app.mount("/static", StaticFiles(directory=str(BASE_DIR / "static")), name="static")
templates = Jinja2Templates(directory=str(BASE_DIR / "templates"))


# ── Flash helpers (cookie-based — works on serverless) ────────────────────────
class FlashMessage:
    def __init__(self, text: str, type: str = "info"):
        self.text = text
        self.type = type


def _encode_flash(msgs: list[FlashMessage]) -> str:
    return _signer.dumps([{"text": m.text, "type": m.type} for m in msgs])


def _decode_flash(val: str) -> list[FlashMessage]:
    try:
        return [FlashMessage(d["text"], d["type"]) for d in _signer.loads(val)]
    except Exception:
        return []


def get_flashes(request: Request) -> list[FlashMessage]:
    raw = request.cookies.get("_flash")
    return _decode_flash(raw) if raw else []


def _redirect_with_flash(request: Request, url: str, text: str, ftype: str = "info") -> RedirectResponse:
    msgs = get_flashes(request) + [FlashMessage(text, ftype)]
    resp = RedirectResponse(url, status_code=302)
    resp.set_cookie("_flash", _encode_flash(msgs), httponly=True, max_age=60, samesite="lax")
    return resp


# ── Render helper ─────────────────────────────────────────────────────────────
def render(request: Request, template: str, context: dict,
           db: Session, status_code: int = 200) -> HTMLResponse:
    current_user = get_current_user(request, db)
    msgs = context.pop("messages", None) or get_flashes(request)
    ctx  = {"request": request, "current_user": current_user, "messages": msgs}
    ctx.update(context)
    resp = templates.TemplateResponse(template, ctx, status_code=status_code)
    resp.delete_cookie("_flash")
    return resp


def flash_error(request: Request, template: str, context: dict,
                db: Session, msg: str, code: int = 400) -> HTMLResponse:
    context["messages"] = [FlashMessage(msg, "error")]
    return render(request, template, context, db, code)


# ─────────────────────────────────────────────────────────────────────────────
# ROUTES
# ─────────────────────────────────────────────────────────────────────────────

# ── HOME ──────────────────────────────────────────────────────────────────────
@app.get("/", response_class=HTMLResponse)
def home(request: Request, db: Session = Depends(get_db)):
    members = (
        db.query(User)
        .filter(User.is_active == True, User.role == RoleEnum.user)
        .order_by(User.created_at.desc())
        .limit(8).all()
    )
    return render(request, "index.html", {"recent_members": members}, db)


# ── REGISTER ──────────────────────────────────────────────────────────────────
@app.get("/register", response_class=HTMLResponse)
def register_get(request: Request, db: Session = Depends(get_db)):
    return render(request, "register.html", {"form": {}}, db)


@app.post("/register", response_class=HTMLResponse)
def register_post(
    request: Request,
    db: Session = Depends(get_db),
    username:         str = Form(...),
    email:            str = Form(...),
    password:         str = Form(...),
    confirm_password: str = Form(...),
    full_name:        str = Form(""),
    age:              str = Form(""),
    gender:           str = Form(""),
    location:         str = Form(""),
):
    form = {"username": username, "email": email,
            "full_name": full_name, "age": age,
            "gender": gender, "location": location}

    if password != confirm_password:
        return flash_error(request, "register.html", {"form": form}, db, "Passwords do not match.")

    if len(password) < 6:
        return flash_error(request, "register.html", {"form": form}, db, "Password must be at least 6 characters.")

    if db.query(User).filter(User.username == username).first():
        return flash_error(request, "register.html", {"form": form}, db, "Username already taken. Please choose another.")

    if db.query(User).filter(User.email == email).first():
        return flash_error(request, "register.html", {"form": form}, db, "Email already registered. Try logging in.")

    age_int = None
    if age.strip():
        try:
            age_int = int(age.strip())
            if age_int < 18:
                return flash_error(request, "register.html", {"form": form}, db, "You must be 18 or older to register.")
        except ValueError:
            age_int = None

    gender_enum = GenderEnum(gender) if gender in ("female", "male", "other") else None

    try:
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
    except Exception as exc:
        db.rollback()
        print(f"[register] DB error: {exc}")
        return flash_error(request, "register.html", {"form": form}, db,
                           "Registration failed due to a server error. Please try again.", 500)

    token = create_access_token({"sub": str(user.id)})
    resp = _redirect_with_flash(request, "/profile/edit",
                                f"Welcome to Pinay Cupid, {user.username}! Complete your profile.", "success")
    resp.set_cookie("access_token", token, httponly=True, max_age=86400 * 7, samesite="lax")
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
    role:     str = Form("user"),
):
    user = db.query(User).filter(
        (User.username == username) | (User.email == username)
    ).first()

    if not user or not verify_password(password, user.hashed_password):
        return flash_error(request, "login.html",
                           {"form": {"username": username}, "is_admin": role == "admin"},
                           db, "Invalid username or password.")

    if not user.is_active:
        return flash_error(request, "login.html", {"form": {}, "is_admin": False},
                           db, "Your account has been deactivated. Contact support.")

    if role == "admin" and user.role != RoleEnum.admin:
        return flash_error(request, "login.html",
                           {"form": {"username": username}, "is_admin": True},
                           db, "You do not have admin privileges.", 403)

    user.last_login = datetime.utcnow()
    db.commit()

    token = create_access_token({"sub": str(user.id)})
    dest  = "/admin" if user.role == RoleEnum.admin else "/"
    resp  = _redirect_with_flash(request, dest, f"Welcome back, {user.username}!", "success")
    resp.set_cookie("access_token", token, httponly=True, max_age=86400 * 7, samesite="lax")
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
    request:  Request,
    db:       Session = Depends(get_db),
    q:        str = "",
    gender:   str = "",
    age_min:  str = "",
    age_max:  str = "",
    location: str = "",
    page:     int = 1,
):
    PAGE_SIZE = 20
    query = db.query(User).filter(User.is_active == True, User.role == RoleEnum.user)

    if q:
        like = f"%{q}%"
        query = query.filter((User.username.ilike(like)) | (User.full_name.ilike(like)))
    if gender in ("female", "male", "other"):
        query = query.filter(User.gender == GenderEnum(gender))
    if age_min:
        try: query = query.filter(User.age >= int(age_min))
        except ValueError: pass
    if age_max:
        try: query = query.filter(User.age <= int(age_max))
        except ValueError: pass
    if location:
        query = query.filter(User.location.ilike(f"%{location}%"))

    total       = query.count()
    total_pages = max(1, (total + PAGE_SIZE - 1) // PAGE_SIZE)
    page        = max(1, min(page, total_pages))
    members     = query.order_by(User.created_at.desc()) \
                       .offset((page - 1) * PAGE_SIZE).limit(PAGE_SIZE).all()

    params = []
    if q:        params.append(f"q={q}")
    if gender:   params.append(f"gender={gender}")
    if age_min:  params.append(f"age_min={age_min}")
    if age_max:  params.append(f"age_max={age_max}")
    if location: params.append(f"location={location}")

    return render(request, "browse.html", {
        "members": members, "total": total,
        "page": page, "total_pages": total_pages,
        "query_string": "&".join(params),
        "filters": {"q": q, "gender": gender, "age_min": age_min,
                    "age_max": age_max, "location": location},
    }, db)


# ── PROFILE — specific routes BEFORE /{user_id} to avoid int-parse collision ──

@app.get("/profile", response_class=HTMLResponse)
def my_profile(request: Request, db: Session = Depends(get_db)):
    cu = get_current_user(request, db)
    if not cu:
        return RedirectResponse("/login", status_code=302)
    return RedirectResponse(f"/profile/{cu.id}", status_code=302)


@app.get("/profile/edit", response_class=HTMLResponse)
def edit_profile_get(request: Request, db: Session = Depends(get_db)):
    cu = get_current_user(request, db)
    if not cu:
        return RedirectResponse("/login", status_code=302)
    return render(request, "profile_edit.html", {}, db)


@app.post("/profile/edit", response_class=HTMLResponse)
def edit_profile_post(
    request:     Request,
    db:          Session = Depends(get_db),
    full_name:   str = Form(""),
    age:         str = Form(""),
    gender:      str = Form(""),
    location:    str = Form(""),
    religion:    str = Form(""),
    occupation:  str = Form(""),
    bio:         str = Form(""),
    looking_for: str = Form(""),
):
    cu = get_current_user(request, db)
    if not cu:
        return RedirectResponse("/login", status_code=302)

    cu.full_name   = full_name   or None
    cu.location    = location    or None
    cu.religion    = religion    or None
    cu.occupation  = occupation  or None
    cu.bio         = bio         or None
    cu.looking_for = looking_for or None

    if age.strip():
        try:   cu.age = int(age.strip())
        except ValueError: pass
    else:
        cu.age = None

    if gender in ("female", "male", "other"):
        cu.gender = GenderEnum(gender)
    elif not gender:
        cu.gender = None

    db.commit()
    return _redirect_with_flash(request, f"/profile/{cu.id}",
                                "Profile updated successfully!", "success")


@app.post("/profile/photo")
async def upload_photo(
    request: Request,
    db:      Session = Depends(get_db),
    photo:   UploadFile = File(...),
):
    cu = get_current_user(request, db)
    if not cu:
        return RedirectResponse("/login", status_code=302)

    if photo.content_type not in ALLOWED_IMAGE_TYPES:
        return _redirect_with_flash(request, "/profile/edit",
                                    "Invalid file type. Upload JPG, PNG, GIF, or WEBP.", "error")

    contents = await photo.read()
    if len(contents) > MAX_PHOTO_SIZE:
        return _redirect_with_flash(request, "/profile/edit",
                                    "Image too large. Maximum 5 MB.", "error")

    ext      = Path(photo.filename).suffix.lower() or ".jpg"
    filename = f"user_{cu.id}_{uuid.uuid4().hex[:8]}{ext}"
    filepath = UPLOAD_DIR / filename

    if cu.profile_photo:
        old = UPLOAD_DIR / cu.profile_photo
        if old.exists():
            old.unlink(missing_ok=True)

    filepath.write_bytes(contents)
    cu.profile_photo = filename
    db.commit()

    return _redirect_with_flash(request, "/profile/edit",
                                "Profile photo updated!", "success")


# ── PROFILE VIEW — must come after /profile/edit ──────────────────────────────
@app.get("/profile/{user_id}", response_class=HTMLResponse)
def view_profile(user_id: int, request: Request, db: Session = Depends(get_db)):
    user = db.query(User).filter(User.id == user_id, User.is_active == True).first()
    if not user:
        raise HTTPException(status_code=404, detail="Profile not found")
    return render(request, "profile_view.html", {"user": user}, db)


# ── ADMIN ─────────────────────────────────────────────────────────────────────
def _admin_ctx(request: Request, db: Session) -> User:
    admin = get_current_user(request, db)
    if not admin or admin.role != RoleEnum.admin:
        raise HTTPException(status_code=302, headers={"Location": "/login"})
    return admin


def admin_render(request: Request, template: str, context: dict, db: Session) -> HTMLResponse:
    admin = _admin_ctx(request, db)
    msgs  = get_flashes(request)
    ctx   = {"request": request, "admin": admin, "messages": msgs}
    ctx.update(context)
    resp  = templates.TemplateResponse(template, ctx)
    resp.delete_cookie("_flash")
    return resp


@app.get("/admin", response_class=HTMLResponse)
def admin_dashboard(request: Request, db: Session = Depends(get_db)):
    admin = get_current_user(request, db)
    if not admin or admin.role != RoleEnum.admin:
        return RedirectResponse("/login", status_code=302)

    today     = datetime.utcnow().date()
    new_today = db.query(User).filter(
        User.role == RoleEnum.user,
        User.created_at >= datetime(today.year, today.month, today.day),
    ).count()

    return admin_render(request, "admin/dashboard.html", {
        "stats": {
            "total_users":  db.query(User).filter(User.role == RoleEnum.user).count(),
            "active_users": db.query(User).filter(User.role == RoleEnum.user, User.is_active == True).count(),
            "female_users": db.query(User).filter(User.role == RoleEnum.user, User.gender == GenderEnum.female).count(),
            "male_users":   db.query(User).filter(User.role == RoleEnum.user, User.gender == GenderEnum.male).count(),
            "new_today":    new_today,
        },
        "recent_users": db.query(User).order_by(User.created_at.desc()).limit(10).all(),
    }, db)


@app.get("/admin/users", response_class=HTMLResponse)
def admin_users(
    request: Request,
    db:      Session = Depends(get_db),
    q:       str = "",
    filter:  str = "",
    role:    str = "",
    page:    int = 1,
):
    admin = get_current_user(request, db)
    if not admin or admin.role != RoleEnum.admin:
        return RedirectResponse("/login", status_code=302)

    PAGE_SIZE = 25
    query = db.query(User)
    if q:
        like  = f"%{q}%"
        query = query.filter(
            (User.username.ilike(like)) | (User.email.ilike(like)) | (User.full_name.ilike(like))
        )
    if filter == "active":   query = query.filter(User.is_active == True)
    elif filter == "inactive": query = query.filter(User.is_active == False)
    if role == "user":  query = query.filter(User.role == RoleEnum.user)
    elif role == "admin": query = query.filter(User.role == RoleEnum.admin)

    total       = query.count()
    total_pages = max(1, (total + PAGE_SIZE - 1) // PAGE_SIZE)
    page        = max(1, min(page, total_pages))
    users       = query.order_by(User.created_at.desc()) \
                       .offset((page - 1) * PAGE_SIZE).limit(PAGE_SIZE).all()

    params = []
    if q:      params.append(f"q={q}")
    if filter: params.append(f"filter={filter}")
    if role:   params.append(f"role={role}")

    return admin_render(request, "admin/users.html", {
        "users": users, "total": total,
        "page": page, "total_pages": total_pages,
        "query_string": "&".join(params),
        "q": q, "filter": filter, "role_filter": role,
    }, db)


@app.get("/admin/users/{user_id}/toggle")
def admin_toggle(user_id: int, request: Request, db: Session = Depends(get_db)):
    admin = get_current_user(request, db)
    if not admin or admin.role != RoleEnum.admin:
        return RedirectResponse("/login", status_code=302)
    user = db.query(User).filter(User.id == user_id).first()
    if not user:
        return _redirect_with_flash(request, "/admin/users", "User not found.", "error")
    if user.id == admin.id:
        return _redirect_with_flash(request, "/admin/users", "Cannot deactivate your own account.", "error")
    user.is_active = not user.is_active
    db.commit()
    return _redirect_with_flash(request, "/admin/users",
                                f"{user.username} {'activated' if user.is_active else 'deactivated'}.", "success")


@app.get("/admin/users/{user_id}/delete")
def admin_delete(user_id: int, request: Request, db: Session = Depends(get_db)):
    admin = get_current_user(request, db)
    if not admin or admin.role != RoleEnum.admin:
        return RedirectResponse("/login", status_code=302)
    user = db.query(User).filter(User.id == user_id).first()
    if not user:
        return _redirect_with_flash(request, "/admin/users", "User not found.", "error")
    if user.id == admin.id:
        return _redirect_with_flash(request, "/admin/users", "Cannot delete your own account.", "error")
    if user.profile_photo:
        (UPLOAD_DIR / user.profile_photo).unlink(missing_ok=True)
    name = user.username
    db.delete(user)
    db.commit()
    return _redirect_with_flash(request, "/admin/users", f"{name} deleted.", "success")


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
    return _redirect_with_flash(request, "/admin/users", f"{user.username} promoted to admin.", "success")


# ─────────────────────────────────────────────────────────────────────────────
if __name__ == "__main__":
    uvicorn.run("app:app", host="127.0.0.1", port=8000, reload=True)
