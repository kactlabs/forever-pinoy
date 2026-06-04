"""
cloudinary_helper.py

Thin wrapper around the Cloudinary SDK.
Requires three env vars:
  CLOUDINARY_CLOUD_NAME
  CLOUDINARY_API_KEY
  CLOUDINARY_API_SECRET

All three are shown on your Cloudinary dashboard home page.
Free tier: 25 GB storage, 25 GB bandwidth/month — plenty for a dating app.
"""
import os
import cloudinary
import cloudinary.uploader

_configured = False


def _configure():
    global _configured
    if _configured:
        return
    cloud_name = os.environ.get("CLOUDINARY_CLOUD_NAME", "")
    api_key    = os.environ.get("CLOUDINARY_API_KEY",    "")
    api_secret = os.environ.get("CLOUDINARY_API_SECRET", "")

    if not all([cloud_name, api_key, api_secret]):
        raise RuntimeError(
            "Cloudinary is not configured. "
            "Set CLOUDINARY_CLOUD_NAME, CLOUDINARY_API_KEY, "
            "and CLOUDINARY_API_SECRET environment variables."
        )

    cloudinary.config(
        cloud_name=cloud_name,
        api_key=api_key,
        api_secret=api_secret,
        secure=True,          # always use HTTPS URLs
    )
    _configured = True


def is_configured() -> bool:
    """Return True if all three Cloudinary env vars are present."""
    return all([
        os.environ.get("CLOUDINARY_CLOUD_NAME"),
        os.environ.get("CLOUDINARY_API_KEY"),
        os.environ.get("CLOUDINARY_API_SECRET"),
    ])


def upload_photo(image_bytes: bytes, public_id: str) -> str:
    """
    Upload image bytes to Cloudinary.
    Returns the secure HTTPS URL of the uploaded image.

    public_id: unique identifier for this image in Cloudinary
                e.g. "pinaycupid/users/user_abc123"
    """
    _configure()
    result = cloudinary.uploader.upload(
        image_bytes,
        public_id=public_id,
        folder="pinaycupid/users",
        overwrite=True,
        resource_type="image",
        transformation=[
            # Auto-crop to a square, max 800px, decent quality — keeps size small
            {"width": 800, "height": 800, "crop": "fill", "gravity": "face"},
            {"quality": "auto", "fetch_format": "auto"},
        ],
    )
    return result["secure_url"]


def delete_photo(public_id: str) -> None:
    """Delete a photo from Cloudinary by its public_id."""
    _configure()
    # public_id here should NOT include the folder prefix
    # cloudinary.uploader.destroy handles the full path
    cloudinary.uploader.destroy(public_id, resource_type="image")


def extract_public_id(url: str) -> str | None:
    """
    Extract the Cloudinary public_id from a secure_url so we can delete it later.
    e.g. "https://res.cloudinary.com/demo/image/upload/v1/pinaycupid/users/user_abc123.jpg"
         → "pinaycupid/users/user_abc123"
    Returns None if the URL doesn't look like a Cloudinary URL.
    """
    if not url or "cloudinary.com" not in url:
        return None
    try:
        # The public_id is everything after /upload/v{version}/ (without extension)
        parts = url.split("/upload/")
        after = parts[1]                      # "v1234/pinaycupid/users/user_abc.jpg"
        # strip version prefix if present
        if after.startswith("v") and "/" in after:
            after = after.split("/", 1)[1]    # "pinaycupid/users/user_abc.jpg"
        # strip file extension
        public_id = after.rsplit(".", 1)[0]   # "pinaycupid/users/user_abc"
        return public_id
    except Exception:
        return None
