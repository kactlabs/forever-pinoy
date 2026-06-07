# 💕 Forever Pinoy

A Filipino dating web app built with FastAPI + Jinja2. Red & cream love-themed UI.

## Features

- **User registration & login** — full profile with photo upload
- **Browse & search** — filter by gender, age, location
- **Admin panel** — manage members, activate/deactivate/promote/delete
- **Separate admin login tab** on the login page

## Quick Start

```bash
pip install -r requirements.txt
python app.py
```

Open: **http://localhost:8000**

## Default Admin

| Field    | Value                   |
|----------|-------------------------|
| Username | `admin`                 |
| Password | `admin123`              |
| Panel    | http://localhost:8000/admin |

> Change the admin password after first login.

## Project Structure

```
pinay-cupid/
├── app.py            # Main FastAPI app (entry point)
├── database.py       # SQLAlchemy models + DB setup
├── auth.py           # Auth helpers (JWT, password hashing)
├── requirements.txt
├── templates/
│   ├── base.html
│   ├── index.html
│   ├── login.html
│   ├── register.html
│   ├── browse.html
│   ├── profile_view.html
│   ├── profile_edit.html
│   └── admin/
│       ├── base_admin.html
│       ├── dashboard.html
│       └── users.html
└── static/
    └── uploads/      # User profile photos
```
