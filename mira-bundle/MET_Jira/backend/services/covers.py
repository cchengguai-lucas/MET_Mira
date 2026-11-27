"""Project cover images: landscape WebP under ``data/covers``, plus built-in presets."""

from __future__ import annotations

import io
import secrets
from pathlib import Path

from ..config import get_settings
from ..models import Project
from .storage import atomic_write

MAX_BYTES = 8 * 1024 * 1024
MAX_PIXELS = 40_000_000
# Wide banner used on All Projects cards (settings preview uses the same asset).
SIZE = (1600, 480)

# Bundled semiconductor-style defaults (id → label for admin UI / API).
PRESETS: dict[str, str] = {
    "wafer": "晶圆",
    "traces": "电路",
    "layers": "叠层",
    "fab": "洁净间",
}

_PRESET_DIR = Path(__file__).resolve().parent.parent / "assets" / "cover_presets"


class CoverError(ValueError):
    pass


def cover_path(project_id: int, key: str) -> Path:
    return get_settings().covers_dir / f"{project_id}-{key}.webp"


def preset_path(preset_id: str) -> Path:
    return _PRESET_DIR / f"{preset_id}.jpg"


def list_presets() -> list[dict[str, str]]:
    return [
        {"id": pid, "label": label, "url": f"/api/cover-presets/{pid}"}
        for pid, label in PRESETS.items()
        if preset_path(pid).is_file()
    ]


def preset_id_of(project: Project) -> str | None:
    key = project.cover_key or ""
    if key.startswith("preset-") and key.removeprefix("preset-") in PRESETS:
        return key.removeprefix("preset-")
    return None


def normalize(data: bytes) -> bytes:
    """Decode, fix EXIF, center-crop to landscape and re-encode as WebP."""
    from PIL import Image, ImageOps, UnidentifiedImageError

    if not data:
        raise CoverError("请选择图片")
    if len(data) > MAX_BYTES:
        raise CoverError("封面图片不能超过 8 MB")
    try:
        with Image.open(io.BytesIO(data)) as probe:
            if probe.width * probe.height > MAX_PIXELS:
                raise CoverError("图片尺寸过大")
        with Image.open(io.BytesIO(data)) as image:
            image.seek(0)
            image = ImageOps.exif_transpose(image)
            image = image.convert("RGBA" if "A" in image.getbands() else "RGB")
            image = ImageOps.fit(image, SIZE, Image.Resampling.LANCZOS)
            out = io.BytesIO()
            image.save(out, "WEBP", quality=86, method=4)
    except CoverError:
        raise
    except (UnidentifiedImageError, OSError, ValueError, Image.DecompressionBombError) as exc:
        raise CoverError("无法识别的图片格式，请上传 PNG、JPG、WebP 或 GIF") from exc
    return out.getvalue()


def _write(project: Project, encoded: bytes, key: str) -> None:
    target = cover_path(project.id, key)
    atomic_write(target, encoded)
    remove_file(project)
    project.cover_key = key


def stage(project_id: int, data: bytes) -> str:
    """Normalize and atomically store a cover without changing ORM state."""
    key = secrets.token_hex(8)
    atomic_write(cover_path(project_id, key), normalize(data))
    return key


def save(project: Project, data: bytes) -> None:
    _write(project, normalize(data), secrets.token_hex(8))


def apply_preset(project: Project, preset_id: str) -> None:
    if preset_id not in PRESETS:
        raise CoverError("未知的默认封面")
    path = preset_path(preset_id)
    if not path.is_file():
        raise CoverError("默认封面文件缺失")
    _write(project, normalize(path.read_bytes()), f"preset-{preset_id}")


def remove_file(project: Project) -> None:
    if project.cover_key:
        cover_path(project.id, project.cover_key).unlink(missing_ok=True)


def clear(project: Project) -> None:
    remove_file(project)
    project.cover_key = None
