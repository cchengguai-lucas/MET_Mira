"""User avatars: uploaded images are normalized to a square 256px WebP under ``data/avatars``."""

from __future__ import annotations

import io
import re
import secrets
from pathlib import Path

from ..config import get_settings
from ..models import User
from .storage import atomic_write

MAX_BYTES = 5 * 1024 * 1024
MAX_PIXELS = 40_000_000
SIZE = 256
COLOR_RE = re.compile(r"^#[0-9a-fA-F]{6}$")


class AvatarError(ValueError):
    pass


def avatar_path(user_id: int, key: str) -> Path:
    return get_settings().avatars_dir / f"{user_id}-{key}.webp"


def normalize(data: bytes) -> bytes:
    """Decode any common image, fix EXIF rotation, center-crop to a square and re-encode."""
    from PIL import Image, ImageOps, UnidentifiedImageError

    if not data:
        raise AvatarError("请选择图片")
    if len(data) > MAX_BYTES:
        raise AvatarError("图片不能超过 5 MB")
    try:
        with Image.open(io.BytesIO(data)) as probe:
            if probe.width * probe.height > MAX_PIXELS:
                raise AvatarError("图片尺寸过大")
        with Image.open(io.BytesIO(data)) as image:
            image.seek(0)
            image = ImageOps.exif_transpose(image)
            image = image.convert("RGBA" if "A" in image.getbands() else "RGB")
            image = ImageOps.fit(image, (SIZE, SIZE), Image.Resampling.LANCZOS)
            out = io.BytesIO()
            image.save(out, "WEBP", quality=88, method=4)
    except AvatarError:
        raise
    except (UnidentifiedImageError, OSError, ValueError, Image.DecompressionBombError) as exc:
        raise AvatarError("无法识别的图片格式，请上传 PNG、JPG、WebP 或 GIF") from exc
    return out.getvalue()


def stage(user_id: int, data: bytes) -> str:
    """Normalize and atomically store a new avatar without touching the database or old file."""
    encoded = normalize(data)
    key = secrets.token_hex(8)
    atomic_write(avatar_path(user_id, key), encoded)
    return key


def save(user: User, data: bytes) -> None:
    key = stage(user.id, data)
    remove_file(user)
    user.avatar_key = key


def remove_file(user: User) -> None:
    if user.avatar_key:
        avatar_path(user.id, user.avatar_key).unlink(missing_ok=True)


def clear(user: User) -> None:
    remove_file(user)
    user.avatar_key = None


def set_color(user: User, color: str | None) -> None:
    if color and not COLOR_RE.match(color):
        raise AvatarError("颜色格式应为 #RRGGBB")
    user.avatar_color = color.lower() if color else None
