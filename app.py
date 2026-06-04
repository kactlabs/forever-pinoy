import os
import uuid
import re
from pathlib import Path
from datetime import datetime
from contextlib import asynccontextmanager

import uvicorn
from bson import ObjectId
from fastapi import FastAPI, Request, Form, Depends, UploadFile, File, HTTPException
from fastapi.responses import HTMLResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from itsdangerous import URLSafeSerializer
from pymongo.database import Database

from database import get_db, get_users, ensure_indexes, User
from auth import hash_password, verify_password, create_access_token, get_current_user
import cloudinary_helper

# ── Constants ─────────────────────────────────────────────────────────────────
BASE_DIR   = Path(__file__).resolve().parent
IS_VERCEL  = bool(os.environ.get("VERCEL"))
# Local fallback dir — only used when Cloudinary env vars are not set
UPLOAD_DIR = Path("/tmp/uploads") if IS_VERCEL else BASE_DIR / "static" / "uploads"
UPLOAD_DIR.mkdir(parents=True, exist_ok=True)

ALLOWED_IMAGE_TYPES = {"image/jpeg", "image/png", "image/gif", "image/webp"}
MAX_PHOTO_SIZE      = 5 * 1024 * 1024   # 5 MB

_FLASH_SECRET = os.environ.get("SECRET_KEY", "pinay-cupid-secret-2024")
_signer       = URLSafeSerializer(_FLASH_SECRET, salt="flash")


# ── Lifespan ──────────────────────────────────────────────────────────────────
_seeded = False


def _seed_admin(db: Database):
    global _seeded
    if _seeded:
        return
    try:
        ensure_indexes(db)
        users = get_users(db)
        if not users.find_one({"role": "admin"}):
            users.insert_one({
                "username":        "admin",
                "email":           "admin@pinaycupid.com",
                "hashed_password": hash_password("admin123"),
                "role":            "admin",
                "is_active":       True,
                "is_verified":     False,
                "full_name":       "Site Administrator",
                "created_at":      datetime.utcnow(),
            })
            print("[startup] Admin user created.")
        _seeded = True
    except Exception as exc:
        print(f"[startup] seed error: {exc}")


@asynccontextmanager
async def lifespan(app: FastAPI):
    # Seed on startup (works for long-running server; lazy seed handles serverless)
    from database import get_client, DB_NAME
    try:
        db = get_client()[DB_NAME]
        _seed_admin(db)
    except Exception as exc:
        print(f"[lifespan] Could not connect at startup: {exc}")
    yield


# ── App ───────────────────────────────────────────────────────────────────────
app = FastAPI(title="Pinay Cupid", lifespan=lifespan)
app.mount("/static", StaticFiles(directory=str(BASE_DIR / "static")), name="static")
templates = Jinja2Templates(directory=str(BASE_DIR / "templates"))


# ── Flash helpers ─────────────────────────────────────────────────────────────
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
           db: Database, status_code: int = 200) -> HTMLResponse:
    current_user = get_current_user(request, db)
    msgs = context.pop("messages", None) or get_flashes(request)
    ctx  = {"request": request, "current_user": current_user, "messages": msgs}
    ctx.update(context)
    resp = templates.TemplateResponse(template, ctx, status_code=status_code)
    resp.delete_cookie("_flash")
    return resp


def flash_error(request: Request, template: str, context: dict,
                db: Database, msg: str, code: int = 400) -> HTMLResponse:
    context["messages"] = [FlashMessage(msg, "error")]
    return render(request, template, context, db, code)


# ─────────────────────────────────────────────────────────────────────────────
# ROUTES
# ─────────────────────────────────────────────────────────────────────────────

# ── HOME ──────────────────────────────────────────────────────────────────────
@app.get("/", response_class=HTMLResponse)
def home(request: Request, db: Database = Depends(get_db)):
    _seed_admin(db)
    users   = get_users(db)
    docs    = list(users.find({"is_active": True, "role": "user"})
                        .sort("created_at", -1).limit(8))
    members = [User(d) for d in docs]
    return render(request, "index.html", {"recent_members": members}, db)


# ── REGISTER ──────────────────────────────────────────────────────────────────
@app.get("/register", response_class=HTMLResponse)
def register_get(request: Request, db: Database = Depends(get_db)):
    _seed_admin(db)
    return render(request, "register.html", {"form": {}}, db)


@app.post("/register", response_class=HTMLResponse)
def register_post(
    request:          Request,
    db:               Database = Depends(get_db),
    username:         str = Form(...),
    email:            str = Form(...),
    password:         str = Form(...),
    confirm_password: str = Form(...),
    full_name:        str = Form(""),
    age:              str = Form(""),
    gender:           str = Form(""),
    location:         str = Form(""),
):
    _seed_admin(db)
    form = {"username": username, "email": email,
            "full_name": full_name, "age": age,
            "gender": gender, "location": location}

    if password != confirm_password:
        return flash_error(request, "register.html", {"form": form}, db, "Passwords do not match.")
    if len(password) < 6:
        return flash_error(request, "register.html", {"form": form}, db, "Password must be at least 6 characters.")

    users = get_users(db)

    if users.find_one({"username": username}):
        return flash_error(request, "register.html", {"form": form}, db, "Username already taken.")
    if users.find_one({"email": email}):
        return flash_error(request, "register.html", {"form": form}, db, "Email already registered. Try logging in.")

    age_int = None
    if age.strip():
        try:
            age_int = int(age.strip())
            if age_int < 18:
                return flash_error(request, "register.html", {"form": form}, db,
                                   "You must be 18 or older to register.")
        except ValueError:
            age_int = None

    gender_val = gender if gender in ("female", "male", "other") else None

    try:
        doc = {
            "username":        username,
            "email":           email,
            "hashed_password": hash_password(password),
            "role":            "user",
            "is_active":       True,
            "is_verified":     False,
            "created_at":      datetime.utcnow(),
            "full_name":       full_name or None,
            "age":             age_int,
            "gender":          gender_val,
            "location":        location or None,
            "bio":             None,
            "looking_for":     None,
            "religion":        None,
            "occupation":      None,
            "profile_photo":   None,
            "last_login":      None,
        }
        result = users.insert_one(doc)
        user_id = str(result.inserted_id)
    except Exception as exc:
        import traceback; traceback.print_exc()
        return flash_error(request, "register.html", {"form": form}, db,
                           "Registration failed due to a server error. Please try again.", 500)

    token = create_access_token({"sub": user_id})
    resp  = _redirect_with_flash(request, "/profile/edit",
                                 f"Welcome to Pinay Cupid, {username}! Complete your profile.", "success")
    resp.set_cookie("access_token", token, httponly=True, max_age=86400 * 7, samesite="lax")
    return resp


# ── LOGIN ─────────────────────────────────────────────────────────────────────
@app.get("/login", response_class=HTMLResponse)
def login_get(request: Request, db: Database = Depends(get_db)):
    return render(request, "login.html", {"form": {}, "is_admin": False}, db)


@app.post("/login", response_class=HTMLResponse)
def login_post(
    request:  Request,
    db:       Database = Depends(get_db),
    username: str = Form(...),
    password: str = Form(...),
    role:     str = Form("user"),
):
    users = get_users(db)
    doc   = users.find_one({"$or": [{"username": username}, {"email": username}]})

    if not doc or not verify_password(password, doc.get("hashed_password", "")):
        return flash_error(request, "login.html",
                           {"form": {"username": username}, "is_admin": role == "admin"},
                           db, "Invalid username or password.")

    if not doc.get("is_active", True):
        return flash_error(request, "login.html", {"form": {}, "is_admin": False},
                           db, "Your account has been deactivated. Contact support.")

    if role == "admin" and doc.get("role") != "admin":
        return flash_error(request, "login.html",
                           {"form": {"username": username}, "is_admin": True},
                           db, "You do not have admin privileges.", 403)

    users.update_one({"_id": doc["_id"]}, {"$set": {"last_login": datetime.utcnow()}})

    user_id  = str(doc["_id"])
    token    = create_access_token({"sub": user_id})
    dest     = "/admin" if doc.get("role") == "admin" else "/"
    resp     = _redirect_with_flash(request, dest,
                                    f"Welcome back, {doc['username']}!", "success")
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
    db:       Database = Depends(get_db),
    gender:   str = "",
    age_min:  str = "",
    age_max:  str = "",
    location: str = "",
    page:     int = 1,
):
    PAGE_SIZE = 20
    filt: dict = {"is_active": True, "role": "user"}

    if gender in ("female", "male", "other"):
        filt["gender"] = gender
    age_q: dict = {}
    if age_min:
        try: age_q["$gte"] = int(age_min)
        except ValueError: pass
    if age_max:
        try: age_q["$lte"] = int(age_max)
        except ValueError: pass
    if age_q:
        filt["age"] = age_q
    if location:
        filt["location"] = {"$regex": re.escape(location), "$options": "i"}

    users       = get_users(db)
    total       = users.count_documents(filt)
    total_pages = max(1, (total + PAGE_SIZE - 1) // PAGE_SIZE)
    page        = max(1, min(page, total_pages))
    docs        = list(users.find(filt).sort("created_at", -1)
                             .skip((page - 1) * PAGE_SIZE).limit(PAGE_SIZE))
    members     = [User(d) for d in docs]

    params = []
    if gender:   params.append(f"gender={gender}")
    if age_min:  params.append(f"age_min={age_min}")
    if age_max:  params.append(f"age_max={age_max}")
    if location: params.append(f"location={location}")

    return render(request, "browse.html", {
        "members": members, "total": total,
        "page": page, "total_pages": total_pages,
        "query_string": "&".join(params),
        "filters": {"q": "", "gender": gender, "age_min": age_min,
                    "age_max": age_max, "location": location},
    }, db)


# ── PROFILE ───────────────────────────────────────────────────────────────────
@app.get("/profile", response_class=HTMLResponse)
def my_profile(request: Request, db: Database = Depends(get_db)):
    cu = get_current_user(request, db)
    if not cu:
        return RedirectResponse("/login", status_code=302)
    return RedirectResponse(f"/profile/{cu.id}", status_code=302)


@app.get("/profile/edit", response_class=HTMLResponse)
def edit_profile_get(request: Request, db: Database = Depends(get_db)):
    cu = get_current_user(request, db)
    if not cu:
        return RedirectResponse("/login", status_code=302)
    return render(request, "profile_edit.html", {}, db)


@app.post("/profile/edit", response_class=HTMLResponse)
def edit_profile_post(
    request:     Request,
    db:          Database = Depends(get_db),
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

    age_int = None
    if age.strip():
        try:   age_int = int(age.strip())
        except ValueError: pass

    gender_val = gender if gender in ("female", "male", "other") else None

    updates = {
        "full_name":   full_name   or None,
        "location":    location    or None,
        "religion":    religion    or None,
        "occupation":  occupation  or None,
        "bio":         bio         or None,
        "looking_for": looking_for or None,
        "age":         age_int,
        "gender":      gender_val,
    }
    get_users(db).update_one({"_id": ObjectId(cu.id)}, {"$set": updates})
    return _redirect_with_flash(request, f"/profile/{cu.id}",
                                "Profile updated successfully!", "success")


@app.post("/profile/photo")
async def upload_photo(
    request: Request,
    db:      Database = Depends(get_db),
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

    try:
        if cloudinary_helper.is_configured():
            # ── Cloudinary path ───────────────────────────────────────────────
            # Delete old Cloudinary image if it exists
            if cu.profile_photo and "cloudinary.com" in (cu.profile_photo or ""):
                old_pid = cloudinary_helper.extract_public_id(cu.profile_photo)
                if old_pid:
                    try:
                        cloudinary_helper.delete_photo(old_pid)
                    except Exception:
                        pass   # non-fatal — old image cleanup failure is OK

            public_id  = f"user_{cu.id}_{uuid.uuid4().hex[:8]}"
            photo_url  = cloudinary_helper.upload_photo(contents, public_id)
        else:
            # ── Local fallback (dev without Cloudinary credentials) ───────────
            if cu.profile_photo and not cu.profile_photo.startswith("http"):
                (UPLOAD_DIR / cu.profile_photo).unlink(missing_ok=True)

            ext       = Path(photo.filename).suffix.lower() or ".jpg"
            filename  = f"user_{cu.id}_{uuid.uuid4().hex[:8]}{ext}"
            (UPLOAD_DIR / filename).write_bytes(contents)
            photo_url = f"/static/uploads/{filename}"

    except RuntimeError as exc:
        # Cloudinary not configured and we're on Vercel — shouldn't happen if env vars are set
        print(f"[upload_photo] error: {exc}")
        return _redirect_with_flash(request, "/profile/edit", str(exc), "error")
    except Exception as exc:
        import traceback; traceback.print_exc()
        return _redirect_with_flash(request, "/profile/edit",
                                    "Photo upload failed. Please try again.", "error")

    get_users(db).update_one({"_id": ObjectId(cu.id)}, {"$set": {"profile_photo": photo_url}})
    return _redirect_with_flash(request, "/profile/edit", "Profile photo updated!", "success")


# ── PROFILE VIEW — MUST come after /profile/edit ──────────────────────────────
@app.get("/profile/{user_id}", response_class=HTMLResponse)
def view_profile(user_id: str, request: Request, db: Database = Depends(get_db)):
    try:
        oid = ObjectId(user_id)
    except Exception:
        raise HTTPException(status_code=404, detail="Profile not found")
    doc = get_users(db).find_one({"_id": oid, "is_active": True})
    if not doc:
        raise HTTPException(status_code=404, detail="Profile not found")
    return render(request, "profile_view.html", {"user": User(doc)}, db)


# ── ADMIN ─────────────────────────────────────────────────────────────────────
def _require_admin(request: Request, db: Database):
    cu = get_current_user(request, db)
    if not cu or cu.role.value != "admin":
        raise HTTPException(status_code=302, headers={"Location": "/login"})
    return cu


def admin_render(request: Request, template: str, context: dict, db: Database) -> HTMLResponse:
    admin = _require_admin(request, db)
    msgs  = get_flashes(request)
    ctx   = {"request": request, "admin": admin, "messages": msgs}
    ctx.update(context)
    resp  = templates.TemplateResponse(template, ctx)
    resp.delete_cookie("_flash")
    return resp


@app.get("/admin", response_class=HTMLResponse)
def admin_dashboard(request: Request, db: Database = Depends(get_db)):
    cu = get_current_user(request, db)
    if not cu or cu.role.value != "admin":
        return RedirectResponse("/login", status_code=302)

    users = get_users(db)
    today = datetime.utcnow().replace(hour=0, minute=0, second=0, microsecond=0)

    stats = {
        "total_users":  users.count_documents({"role": "user"}),
        "active_users": users.count_documents({"role": "user", "is_active": True}),
        "female_users": users.count_documents({"role": "user", "gender": "female"}),
        "male_users":   users.count_documents({"role": "user", "gender": "male"}),
        "new_today":    users.count_documents({"role": "user", "created_at": {"$gte": today}}),
    }
    recent_docs  = list(users.find().sort("created_at", -1).limit(10))
    recent_users = [User(d) for d in recent_docs]

    return admin_render(request, "admin/dashboard.html",
                        {"stats": stats, "recent_users": recent_users}, db)


@app.get("/admin/users", response_class=HTMLResponse)
def admin_users(
    request: Request,
    db:      Database = Depends(get_db),
    q:       str = "",
    filter:  str = "",
    role:    str = "",
    page:    int = 1,
):
    cu = get_current_user(request, db)
    if not cu or cu.role.value != "admin":
        return RedirectResponse("/login", status_code=302)

    PAGE_SIZE = 25
    filt: dict = {}

    if q:
        pattern = {"$regex": re.escape(q), "$options": "i"}
        filt["$or"] = [{"username": pattern}, {"email": pattern}, {"full_name": pattern}]
    if filter == "active":   filt["is_active"] = True
    elif filter == "inactive": filt["is_active"] = False
    if role in ("user", "admin"):
        filt["role"] = role

    users       = get_users(db)
    total       = users.count_documents(filt)
    total_pages = max(1, (total + PAGE_SIZE - 1) // PAGE_SIZE)
    page        = max(1, min(page, total_pages))
    docs        = list(users.find(filt).sort("created_at", -1)
                             .skip((page - 1) * PAGE_SIZE).limit(PAGE_SIZE))
    user_objs   = [User(d) for d in docs]

    params = []
    if q:      params.append(f"q={q}")
    if filter: params.append(f"filter={filter}")
    if role:   params.append(f"role={role}")

    return admin_render(request, "admin/users.html", {
        "users": user_objs, "total": total,
        "page": page, "total_pages": total_pages,
        "query_string": "&".join(params),
        "q": q, "filter": filter, "role_filter": role,
    }, db)


@app.get("/admin/users/{user_id}/toggle")
def admin_toggle(user_id: str, request: Request, db: Database = Depends(get_db)):
    cu = get_current_user(request, db)
    if not cu or cu.role.value != "admin":
        return RedirectResponse("/login", status_code=302)
    try:
        oid = ObjectId(user_id)
    except Exception:
        return _redirect_with_flash(request, "/admin/users", "Invalid user ID.", "error")

    users = get_users(db)
    doc   = users.find_one({"_id": oid})
    if not doc:
        return _redirect_with_flash(request, "/admin/users", "User not found.", "error")
    if str(doc["_id"]) == cu.id:
        return _redirect_with_flash(request, "/admin/users", "Cannot deactivate your own account.", "error")

    new_status = not doc.get("is_active", True)
    users.update_one({"_id": oid}, {"$set": {"is_active": new_status}})
    action = "activated" if new_status else "deactivated"
    return _redirect_with_flash(request, "/admin/users",
                                f"{doc['username']} {action}.", "success")


@app.get("/admin/users/{user_id}/delete")
def admin_delete(user_id: str, request: Request, db: Database = Depends(get_db)):
    cu = get_current_user(request, db)
    if not cu or cu.role.value != "admin":
        return RedirectResponse("/login", status_code=302)
    try:
        oid = ObjectId(user_id)
    except Exception:
        return _redirect_with_flash(request, "/admin/users", "Invalid user ID.", "error")

    users = get_users(db)
    doc   = users.find_one({"_id": oid})
    if not doc:
        return _redirect_with_flash(request, "/admin/users", "User not found.", "error")
    if str(doc["_id"]) == cu.id:
        return _redirect_with_flash(request, "/admin/users", "Cannot delete your own account.", "error")

    if doc.get("profile_photo"):
        photo = doc["profile_photo"]
        if cloudinary_helper.is_configured() and "cloudinary.com" in photo:
            pid = cloudinary_helper.extract_public_id(photo)
            if pid:
                try: cloudinary_helper.delete_photo(pid)
                except Exception: pass
        else:
            # local fallback
            if not photo.startswith("http"):
                (UPLOAD_DIR / photo).unlink(missing_ok=True)

    users.delete_one({"_id": oid})
    return _redirect_with_flash(request, "/admin/users",
                                f"{doc['username']} deleted.", "success")


@app.get("/admin/users/{user_id}/make-admin")
def admin_promote(user_id: str, request: Request, db: Database = Depends(get_db)):
    cu = get_current_user(request, db)
    if not cu or cu.role.value != "admin":
        return RedirectResponse("/login", status_code=302)
    try:
        oid = ObjectId(user_id)
    except Exception:
        return _redirect_with_flash(request, "/admin/users", "Invalid user ID.", "error")

    users = get_users(db)
    doc   = users.find_one({"_id": oid})
    if not doc:
        return _redirect_with_flash(request, "/admin/users", "User not found.", "error")

    users.update_one({"_id": oid}, {"$set": {"role": "admin"}})
    return _redirect_with_flash(request, "/admin/users",
                                f"{doc['username']} promoted to admin.", "success")


# ─────────────────────────────────────────────────────────────────────────────
if __name__ == "__main__":
    uvicorn.run("app:app", host="127.0.0.1", port=8000, reload=True)
