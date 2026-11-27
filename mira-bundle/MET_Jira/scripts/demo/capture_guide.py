#!/usr/bin/env python3
"""Capture fresh stills (and short demo clips) for the User Guide from staging UI.

Prefer the richer My Work dataset for houcheng.fan::

    METJIRA_DATA_DIR=~/.met_jira_staging METJIRA_DEPLOY_ENV=staging \\
      python3 scripts/demo/seed_my_work.py --user houcheng.fan --reset

    PLAYWRIGHT_BROWSERS_PATH="$HOME/.cache/ms-playwright" \\
      METJIRA_GUIDE_USER=houcheng.fan \\
      python3 scripts/demo/capture_guide.py
"""

from __future__ import annotations

import io
import os
import re
import shutil
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
OUT = ROOT / "web" / "public" / "guide"
BASE = os.environ.get("METJIRA_GUIDE_URL", "http://127.0.0.1:8801")
USER = os.environ.get("METJIRA_GUIDE_USER", "houcheng.fan")
PASSWORD = os.environ.get("METJIRA_GUIDE_PASSWORD", "MetJira123!")


def _load_dotenv() -> None:
    """Load staging .env so we can mint LDAP-user session cookies without AD password."""
    os.environ.setdefault("METJIRA_DATA_DIR", str(Path.home() / ".met_jira_staging"))
    os.environ.setdefault("METJIRA_DEPLOY_ENV", "staging")
    env_path = ROOT / ".env"
    if not env_path.is_file():
        return
    for line in env_path.read_text().splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, value = line.partition("=")
        os.environ.setdefault(key.strip(), value.strip().strip('"').strip("'"))
    if str(ROOT) not in sys.path:
        sys.path.insert(0, str(ROOT))


def _mint_session(username: str) -> tuple[str, str]:
    """Return (cookie_name, token) for username via HMAC signer (works for LDAP users)."""
    _load_dotenv()
    from sqlalchemy import select

    from backend.config import get_settings
    from backend.db import session_factory
    from backend.deps import signer
    from backend.models import User

    settings = get_settings()
    with session_factory()() as db:
        user = db.scalar(select(User).where(User.username == username))
        if user is None:
            raise RuntimeError(f"user {username!r} not found in staging DB")
        token = signer().issue(user.id)
    return settings.cookie_name, token


def _to_webp(png: bytes, quality: int = 82) -> bytes:
    from PIL import Image

    img = Image.open(io.BytesIO(png)).convert("RGB")
    buf = io.BytesIO()
    img.save(buf, format="WEBP", quality=quality, method=6)
    return buf.getvalue()


def _to_jpeg(png: bytes, quality: int = 85) -> bytes:
    from PIL import Image

    img = Image.open(io.BytesIO(png)).convert("RGB")
    buf = io.BytesIO()
    img.save(buf, format="JPEG", quality=quality, optimize=True)
    return buf.getvalue()


def _save(name: str, png: bytes, poster: str | None = None) -> None:
    (OUT / f"{name}.webp").write_bytes(_to_webp(png))
    if poster:
        (OUT / f"{poster}-poster.jpg").write_bytes(_to_jpeg(png))
    print(f"ok {name}")


def _probe_luma(path: Path, fps: int = 10) -> list[float]:
    """Sample mean luminance (~0–255) at ``fps`` for flash / black-frame detection."""
    from PIL import Image

    ffmpeg = shutil.which("ffmpeg")
    if not ffmpeg:
        return []
    tmp = path.parent / f"_luma_{path.stem}"
    if tmp.exists():
        shutil.rmtree(tmp)
    tmp.mkdir(parents=True, exist_ok=True)
    subprocess.run(
        [ffmpeg, "-y", "-i", str(path), "-vf", f"fps={fps},scale=160:-1", str(tmp / "f_%04d.png")],
        check=False, capture_output=True,
    )
    means: list[float] = []
    for frame in sorted(tmp.glob("f_*.png")):
        img = Image.open(frame).convert("RGB")
        data = img.getdata()
        means.append(sum(sum(px) / 3 for px in data) / len(data))
    shutil.rmtree(tmp, ignore_errors=True)
    return means


def _stable_trim(path: Path, fps: int = 10, thresh: float = 28.0) -> tuple[float, float] | None:
    """Return (start, end) seconds of the longest fully-painted stretch (≥1.2s).

    A frame counts as content only when mean luma ≥ ``thresh``. Dark-theme UI sits
    around ~35–50; the intentional black warm-up / loading flash is ~18–22, so the
    default 28 cuts the spinner without eating real board/home frames.
    """
    means = _probe_luma(path, fps=fps)
    if not means:
        return None
    # Fall back for intentionally dark close-ups (e.g. AI filter panel).
    if thresh > 20 and not any(m >= thresh for m in means):
        thresh = 16.0
    content = [m >= thresh for m in means]
    best: tuple[int, int] | None = None
    i = 0
    while i < len(content):
        if not content[i]:
            i += 1
            continue
        j = i
        while j < len(content) and content[j]:
            j += 1
        if best is None or (j - i) > (best[1] - best[0]):
            best = (i, j)
        i = j
    if best is None:
        return None
    start_i, end_i = best
    # Require three stable frames at the head so a half-painted frame isn't kept.
    while start_i + 2 < end_i and not all(content[start_i + k] for k in range(3)):
        start_i += 1
    if start_i + 2 < end_i:
        start_i += 0  # already aligned
    # Drop the last frame — often a teardown / navigation blank.
    end_i = max(start_i + 1, end_i - 1)
    if (end_i - start_i) < int(fps * 1.2):
        return None
    # Skip rewrite when the whole clip is already stable.
    if start_i == 0 and end_i >= len(means) - 1:
        return None
    return start_i / fps, end_i / fps


def _mp4_to_demo_anim(mp4_name: str, *, width: int = 960, fps: int | None = None, quality: int = 72) -> None:
    """Turn a trimmed mp4 into a looping animated WebP (GIF-like, smaller and sharper than GIF)."""
    mp4 = OUT / f"{mp4_name}.mp4"
    if not mp4.is_file():
        print(f"skip {mp4_name}-demo.webp (missing mp4)", file=sys.stderr)
        return
    ffmpeg = shutil.which("ffmpeg")
    if not ffmpeg:
        print(f"skip {mp4_name}-demo.webp (no ffmpeg)", file=sys.stderr)
        return
    # Board drag needs a higher frame rate so the card motion stays readable.
    if fps is None:
        fps = 12 if mp4_name in {"board", "workflow"} else 8
    out = OUT / f"{mp4_name}-demo.webp"
    cmd = [
        ffmpeg, "-y", "-i", str(mp4),
        "-vf", f"fps={fps},scale={width}:-1:flags=lanczos",
        "-an", "-loop", "0",
        "-c:v", "libwebp_anim", "-quality", str(quality),
        "-compression_level", "4",
        str(out),
    ]
    try:
        subprocess.run(cmd, check=True, capture_output=True)
        print(f"ok {mp4_name}-demo.webp ({out.stat().st_size // 1024} KB)")
    except subprocess.CalledProcessError as exc:
        err = (exc.stderr or b"")[-320:].decode("utf-8", "ignore")
        print(f"ffmpeg {mp4_name}-demo.webp: {err}", file=sys.stderr)


def _webm_to_mp4(webm: Path, mp4_name: str, poster_png: bytes | None = None) -> None:
    mp4 = OUT / f"{mp4_name}.mp4"
    ffmpeg = shutil.which("ffmpeg")
    if not ffmpeg:
        print(f"skip {mp4_name}.mp4 (no ffmpeg)", file=sys.stderr)
        return
    window = _stable_trim(webm)
    cmd = [ffmpeg, "-y", "-i", str(webm)]
    if window:
        start, end = window
        # Output-side seek after decode so we don't keep a black keyframe.
        cmd += ["-ss", f"{start:.3f}", "-to", f"{end:.3f}"]
        print(f"trim {mp4_name}: {start:.2f}s–{end:.2f}s")
    cmd += [
        "-c:v", "libx264", "-pix_fmt", "yuv420p", "-an",
        "-movflags", "+faststart", str(mp4),
    ]
    try:
        subprocess.run(cmd, check=True, capture_output=True)
        print(f"ok {mp4_name}.mp4")
        _mp4_to_demo_anim(mp4_name)
    except subprocess.CalledProcessError as exc:
        err = (exc.stderr or b"")[-240:].decode("utf-8", "ignore")
        print(f"ffmpeg {mp4_name}: {err}", file=sys.stderr)
    if poster_png:
        (OUT / f"{mp4_name}-poster.jpg").write_bytes(_to_jpeg(poster_png))


def _trim_existing_mp4(mp4_name: str) -> None:
    """Re-encode an on-disk demo clip, dropping leading/mid black flashes."""
    src = OUT / f"{mp4_name}.mp4"
    if not src.is_file():
        print(f"skip trim {mp4_name}: missing", file=sys.stderr)
        return
    window = _stable_trim(src)
    if not window:
        print(f"keep {mp4_name}.mp4 (no flash stretch found)")
        return
    start, end = window
    ffmpeg = shutil.which("ffmpeg")
    if not ffmpeg:
        return
    tmp = OUT / f"_{mp4_name}_trim.mp4"
    cmd = [
        ffmpeg, "-y", "-ss", f"{start:.3f}", "-to", f"{end:.3f}",
        "-i", str(src),
        "-c:v", "libx264", "-pix_fmt", "yuv420p", "-an",
        "-movflags", "+faststart", str(tmp),
    ]
    try:
        subprocess.run(cmd, check=True, capture_output=True)
        tmp.replace(src)
        print(f"trimmed {mp4_name}.mp4 → {start:.2f}s–{end:.2f}s")
        _mp4_to_demo_anim(mp4_name)
    except subprocess.CalledProcessError as exc:
        err = (exc.stderr or b"")[-240:].decode("utf-8", "ignore")
        print(f"trim fail {mp4_name}: {err}", file=sys.stderr)
        tmp.unlink(missing_ok=True)


INIT_SCRIPT = (
    "localStorage.setItem('metjira.theme','dark');"
    "localStorage.setItem('metjira.lang','zh');"
    # Paint dark before React hydrates to avoid a white flash in recordings.
    "document.documentElement.setAttribute('data-theme','dark');"
    "document.documentElement.style.background='#000';"
    "document.documentElement.style.colorScheme='dark';"
)


def _apply_session(context) -> None:
    cookie_name, token = _mint_session(USER)
    context.add_cookies([{
        "name": cookie_name,
        "value": token,
        "url": BASE,
        "httpOnly": True,
        "sameSite": "Lax",
    }])


def _login(page, context) -> None:
    page.add_init_script(INIT_SCRIPT)
    try:
        _apply_session(context)
        page.goto(f"{BASE}/", wait_until="domcontentloaded")
        page.wait_for_selector("button.btn.primary, .home, .page", timeout=15000)
        if "/login" in page.url:
            raise RuntimeError("minted session still on login")
        page.wait_for_timeout(600)
        return
    except Exception as exc:  # noqa: BLE001
        print(f"session mint failed ({exc}); trying password login", file=sys.stderr)

    page.goto(f"{BASE}/login", wait_until="domcontentloaded")
    page.locator('input[autocomplete="username"]').fill(USER)
    page.locator('input[type="password"]').fill(PASSWORD)
    page.locator("button[type=submit], .login-submit").first.click()
    page.wait_for_function("() => !location.pathname.includes('/login')", timeout=20000)
    page.wait_for_timeout(900)


def _browser(p):
    chrome = Path.home() / ".cache/ms-playwright/chromium-1248/chrome-linux64/chrome"
    launch_kwargs: dict = {"headless": True}
    if chrome.is_file():
        launch_kwargs["executable_path"] = str(chrome)
    return p.chromium.launch(**launch_kwargs)


def _context(browser, *, record_dir: Path | None = None):
    kwargs: dict = {
        "viewport": {"width": 1440, "height": 900},
        "device_scale_factor": 1.25,
        "locale": "zh-CN",
        "color_scheme": "dark",
    }
    if record_dir is not None:
        record_dir.mkdir(parents=True, exist_ok=True)
        kwargs["record_video_dir"] = str(record_dir)
        kwargs["record_video_size"] = {"width": 1440, "height": 900}
    return browser.new_context(**kwargs)


def _capture_login_still(browser) -> None:
    """Capture the unauthenticated Mira cover in the same theme as the guide set."""
    context = _context(browser)
    page = context.new_page()
    page.add_init_script(INIT_SCRIPT)
    try:
        page.goto(f"{BASE}/login", wait_until="networkidle")
        page.wait_for_selector(".mira-login", timeout=15000)
        page.wait_for_timeout(1200)
        _save("login", page.screenshot(type="png"))
    except Exception as exc:  # noqa: BLE001
        print(f"fail login: {exc}", file=sys.stderr)
    finally:
        page.close()
        context.close()


def _pad_frame(png: bytes, size: tuple[int, int] = (1440, 900)) -> bytes:
    """Center a screenshot on a dark 16:10 canvas so Guide clips never letterbox oddly."""
    from PIL import Image

    canvas = Image.new("RGB", size, (0, 0, 0))
    img = Image.open(io.BytesIO(png)).convert("RGB")
    # Fit inside canvas preserving aspect ratio.
    scale = min(size[0] / img.width, size[1] / img.height)
    new_size = (max(1, int(img.width * scale)), max(1, int(img.height * scale)))
    fitted = img.resize(new_size, Image.Resampling.LANCZOS)
    origin = ((size[0] - fitted.width) // 2, (size[1] - fitted.height) // 2)
    canvas.paste(fitted, origin)
    buf = io.BytesIO()
    canvas.save(buf, format="PNG")
    return buf.getvalue()


def _frames_to_mp4(
    frames: list[bytes] | list[tuple[bytes, float]],
    mp4_name: str,
    *,
    hold_ms: int = 900,
    poster_index: int = 0,
) -> None:
    """Build a loop-safe mp4 from still frames (optional per-frame duration in seconds)."""
    if not frames:
        raise RuntimeError(f"no frames for {mp4_name}")
    ffmpeg = shutil.which("ffmpeg")
    if not ffmpeg:
        print(f"skip {mp4_name}.mp4 (no ffmpeg)", file=sys.stderr)
        return
    timed: list[tuple[bytes, float]] = []
    for item in frames:
        if isinstance(item, tuple):
            timed.append((item[0], float(item[1])))
        else:
            timed.append((item, hold_ms / 1000))
    tmp = OUT / f"_frames_{mp4_name}"
    if tmp.exists():
        shutil.rmtree(tmp)
    tmp.mkdir(parents=True, exist_ok=True)
    padded = [(_pad_frame(png), dur) for png, dur in timed]
    list_path = tmp / "concat.txt"
    lines: list[str] = []
    for i, (png, dur) in enumerate(padded):
        frame_path = tmp / f"f_{i:04d}.png"
        frame_path.write_bytes(png)
        lines.append(f"file '{frame_path.name}'")
        lines.append(f"duration {dur:.3f}")
    # ffmpeg concat quirk: last file must be listed again without duration
    lines.append(f"file 'f_{len(padded) - 1:04d}.png'")
    list_path.write_text("\n".join(lines) + "\n")
    mp4 = OUT / f"{mp4_name}.mp4"
    cmd = [
        ffmpeg, "-y",
        "-f", "concat", "-safe", "0",
        "-i", str(list_path),
        "-vsync", "vfr",
        "-pix_fmt", "yuv420p",
        "-c:v", "libx264", "-an",
        "-movflags", "+faststart", str(mp4),
    ]
    try:
        subprocess.run(cmd, check=True, capture_output=True, cwd=str(tmp))
        idx = min(max(poster_index, 0), len(padded) - 1)
        (OUT / f"{mp4_name}-poster.jpg").write_bytes(_to_jpeg(padded[idx][0]))
        total = sum(d for _, d in padded)
        print(f"ok {mp4_name}.mp4 ({len(padded)} steps, ~{total:.1f}s)")
    except subprocess.CalledProcessError as exc:
        err = (exc.stderr or b"")[-400:].decode("utf-8", "ignore")
        print(f"ffmpeg {mp4_name}: {err}", file=sys.stderr)
    shutil.rmtree(tmp, ignore_errors=True)


def _record_clip(browser, name: str, act) -> None:
    """Record only the demo action — session is injected before the first paint."""
    tmp = OUT / f"_vid_{name}"
    if tmp.exists():
        shutil.rmtree(tmp)
    context = _context(browser, record_dir=tmp)
    try:
        _apply_session(context)
    except Exception as exc:  # noqa: BLE001
        print(f"fail clip {name}: session {exc}", file=sys.stderr)
        context.close()
        shutil.rmtree(tmp, ignore_errors=True)
        return
    page = context.new_page()
    page.add_init_script(INIT_SCRIPT)
    poster = None
    try:
        # Warm dark blank so the encoder never starts on a white document.
        page.set_content(
            "<!doctype html><html data-theme=dark style='background:#000'>"
            "<body style='margin:0;background:#000;height:100vh'></body></html>"
        )
        page.wait_for_timeout(200)
        poster = act(page)
        page.wait_for_timeout(500)
    except Exception as exc:  # noqa: BLE001
        print(f"fail clip {name}: {exc}", file=sys.stderr)
    page.close()
    context.close()
    videos = sorted(tmp.glob("*.webm"))
    if videos:
        _webm_to_mp4(videos[0], name, poster)
    shutil.rmtree(tmp, ignore_errors=True)


def _record_create_clip(browser, create_re: re.Pattern[str]) -> None:
    """Full create flow: open → split → describe → generate → review draft → create.

    Uses full-page stills (modal over the app) so the clip matches real operation, and never
    includes the pre-modal home navigation flash.
    """
    context = _context(browser)
    try:
        _apply_session(context)
    except Exception as exc:  # noqa: BLE001
        print(f"fail create clip: session {exc}", file=sys.stderr)
        context.close()
        return
    page = context.new_page()
    page.add_init_script(INIT_SCRIPT)
    # (png, hold_seconds)
    frames: list[tuple[bytes, float]] = []

    def snap(hold: float = 1.0, *, modal_only: bool = False) -> None:
        target = page.locator(".modal").first if modal_only else page
        frames.append((target.screenshot(type="png"), hold))

    try:
        page.goto(f"{BASE}/", wait_until="networkidle")
        page.wait_for_timeout(1200)

        # 1) Click Create on My Work
        btn = page.locator("button.btn.primary").filter(has_text=create_re)
        if btn.count():
            btn.first.click(timeout=5000)
        else:
            page.keyboard.press("c")
        page.wait_for_selector(".modal", timeout=8000)
        page.wait_for_timeout(450)
        modal = page.locator(".modal").first
        snap(1.2)  # empty create modal

        # 2) Check「拆分为史诗和子任务」
        split = modal.locator("label").filter(has_text=re.compile(r"拆分为史诗|Split into"))
        if split.count():
            split.locator("input[type=checkbox]").first.check()
            page.wait_for_timeout(350)
            snap(1.0)

        # 3) Type natural-language description in chunks (reads as real typing)
        ai_box = modal.locator(".ai-box textarea, textarea").first
        ai_box.click()
        typed = ""
        for chunk, pause in [
            ("CMB-2 上料机械手在高速模式下偶发定位偏移，", 0.55),
            ("需要电气组排查伺服参数并给出整改方案，", 0.55),
            ("拆成可并行的子任务", 0.9),
        ]:
            typed += chunk
            ai_box.fill(typed)
            page.wait_for_timeout(200)
            snap(pause)

        # 4) Click ✦ 生成 and capture busy + result
        gen = modal.locator("button.ai-draft-btn, button").filter(
            has_text=re.compile(r"生成|Generate")
        ).first
        gen.click()
        page.wait_for_timeout(400)
        snap(0.8)  # busy / spinner if visible

        draft_ready = False
        try:
            # Split mode → draft tree; single draft → form autofill
            page.wait_for_selector(
                ".modal >> text=/AI 已生成以下草稿|已生成|史诗|返回表单/",
                timeout=55000,
            )
            draft_ready = True
        except Exception:  # noqa: BLE001
            # Wait until title field is filled or generate finishes
            try:
                page.wait_for_function(
                    """() => {
                      const busy = document.querySelector('.ai-draft-btn.busy');
                      const title = document.querySelector('.modal input.input');
                      const drafts = document.querySelector('.modal .card.card-pad');
                      return (!busy) && !!(
                        drafts || (title && title.value && title.value.trim().length > 2)
                      );
                    }""",
                    timeout=55000,
                )
                draft_ready = True
            except Exception as exc:  # noqa: BLE001
                print(f"create clip: AI generate wait failed ({exc})", file=sys.stderr)

        page.wait_for_timeout(600)
        snap(1.6)  # draft / filled form

        if draft_ready:
            # 5) If draft tree is showing, scroll it into view and hold
            draft_cards = modal.locator(".card.card-pad")
            if draft_cards.count():
                draft_cards.first.scroll_into_view_if_needed()
                page.wait_for_timeout(300)
                snap(1.8, modal_only=True)

            # 6) Complete: commit drafts or submit the filled form
            footer_primary = page.locator(".modal-footer button.btn.primary").last
            if footer_primary.count() and footer_primary.is_enabled():
                label = (footer_primary.inner_text() or "").strip()
                footer_primary.click()
                page.wait_for_timeout(900)
                snap(1.4)
                try:
                    page.wait_for_function(
                        "() => !document.querySelector('.modal') "
                        "|| document.querySelector('.toast, .issue-detail, .drawer, .sheet')",
                        timeout=15000,
                    )
                except Exception:  # noqa: BLE001
                    pass
                page.wait_for_timeout(800)
                snap(2.0)
                print(f"create clip: clicked «{label}»")
            else:
                snap(1.2)

        if not frames:
            print("fail create clip: no frames", file=sys.stderr)
            return

        # Still = best mid/late frame (prefer draft or filled form, not the final toast)
        still_idx = min(len(frames) - 1, max(3, len(frames) - 2))
        _save("create-still", frames[still_idx][0])
        # Poster = filled/draft step so the guide thumbnail isn't an empty form
        poster_idx = min(len(frames) - 1, max(4, len(frames) // 2))
        _frames_to_mp4(frames, "create", poster_index=poster_idx)
    except Exception as exc:  # noqa: BLE001
        print(f"fail create clip: {exc}", file=sys.stderr)
        if frames:
            try:
                _save("create-still", frames[-1][0])
                _frames_to_mp4(frames, "create", poster_index=len(frames) - 1)
            except Exception as exc2:  # noqa: BLE001
                print(f"fail create clip save: {exc2}", file=sys.stderr)
    finally:
        page.close()
        context.close()


CLIP_NAMES = ("my-work", "board", "list", "filters", "create", "ai-filter", "workflow")


def _capture_stills(page, create_re) -> None:
    page.goto(f"{BASE}/", wait_until="networkidle")
    page.wait_for_timeout(1600)
    home_png = page.screenshot(type="png")
    _save("home", home_png, poster="my-work")
    (OUT / "my-work.webp").write_bytes((OUT / "home.webp").read_bytes())

    try:
        opened = False
        btn = page.locator("button.btn.primary").filter(has_text=create_re)
        if btn.count():
            btn.first.click(timeout=5000)
            opened = True
        if not opened:
            page.keyboard.press("c")
        page.wait_for_selector(".modal", timeout=8000)
        page.wait_for_timeout(600)
        create_png = page.locator(".modal").first.screenshot(type="png")
        _save("create-still", create_png, poster="create")
        page.keyboard.press("Escape")
        page.wait_for_timeout(300)
    except Exception as exc:  # noqa: BLE001
        print(f"fail create-still: {exc}", file=sys.stderr)
        try:
            page.keyboard.press("Escape")
        except Exception:  # noqa: BLE001
            pass

    routes = [
        ("board-still", "/projects/ELEC/board", "board"),
        ("list-still", "/projects/ELEC/list", "list"),
        ("backlog", "/projects/ELEC/backlog", None),
        ("timeline", "/projects/ELEC/timeline", None),
        ("summary", "/projects/ELEC/summary", None),
        ("reports", "/projects/ELEC/reports", None),
        ("settings-workflow", "/projects/ELEC/settings?section=workflow", None),
        ("user-settings", "/settings", None),
        ("admin", "/admin", None),
        ("filters-still", "/search", "filters"),
        ("org", "/org", None),
    ]
    for name, path_, poster in routes:
        try:
            page.goto(f"{BASE}{path_}", wait_until="networkidle", timeout=30000)
            page.wait_for_timeout(1100)
            if name == "filters-still":
                try:
                    common = page.locator(".menu-item").filter(
                        has_text=re.compile(r"我负责|My open")
                    ).first
                    if common.count():
                        common.click()
                        page.wait_for_timeout(1000)
                except Exception:  # noqa: BLE001
                    pass
            png = page.screenshot(type="png")
            _save(name, png, poster=poster)
        except Exception as exc:  # noqa: BLE001
            print(f"fail {name}: {exc}", file=sys.stderr)

    try:
        page.goto(f"{BASE}/projects/ELEC/board", wait_until="networkidle")
        page.wait_for_timeout(800)
        card = page.locator(".issue-card").first
        if card.count():
            card.click()
            page.wait_for_timeout(800)
            sheet = page.locator(".sheet, .drawer, .issue-detail, .issue-panel").first
            png = sheet.screenshot(type="png") if sheet.count() else page.screenshot(type="png")
            _save("issue", png, poster="workflow")
            page.keyboard.press("Escape")
    except Exception as exc:  # noqa: BLE001
        print(f"fail issue: {exc}", file=sys.stderr)

    try:
        page.goto(f"{BASE}/", wait_until="networkidle")
        page.wait_for_timeout(500)
        bell = page.locator("button").filter(has_text=re.compile(r"通知"))
        if not bell.count():
            bell = page.locator(".notify-btn, [data-notif-toggle], [class*='bell']")
        if bell.count():
            bell.first.click()
            page.wait_for_selector(".notify-btn + .menu", timeout=5000)
            page.wait_for_timeout(600)
        _save("notifications", page.screenshot(type="png"))
    except Exception as exc:  # noqa: BLE001
        print(f"fail notifications: {exc}", file=sys.stderr)

    try:
        page.keyboard.press("Control+K")
        page.wait_for_timeout(700)
        _save("palette-still", page.screenshot(type="png"), poster="palette")
        page.keyboard.press("Escape")
    except Exception as exc:  # noqa: BLE001
        print(f"fail palette: {exc}", file=sys.stderr)


def _record_filters_clips(browser) -> None:
    """Close-up filter demos so Guide feature frames stay readable at full width."""
    context = _context(browser)
    try:
        _apply_session(context)
    except Exception as exc:  # noqa: BLE001
        print(f"fail filters clips: session {exc}", file=sys.stderr)
        context.close()
        return
    page = context.new_page()
    page.add_init_script(INIT_SCRIPT)
    try:
        page.goto(f"{BASE}/search", wait_until="networkidle")
        page.wait_for_timeout(1100)

        # AI filter: expand the persisted collapsed panel before recording.
        ai_frames: list[tuple[bytes, float]] = []
        panel = page.locator(".ai-filter").first
        if "is-collapsed" in (panel.get_attribute("class") or ""):
            panel.locator(".ai-filter-toggle").click()
            page.wait_for_timeout(350)
        panel.scroll_into_view_if_needed()

        def snap_ai(hold: float = 1.1) -> None:
            ai_frames.append((panel.screenshot(type="png"), hold))

        snap_ai(1.3)
        area = panel.locator("textarea.ai-filter-input").first
        typed = ""
        for chunk, hold in [("我负责的、", 0.6), ("本周到期还没完成的", 1.0)]:
            typed += chunk
            area.fill(typed)
            page.wait_for_timeout(180)
            snap_ai(hold)
        send = panel.locator("button.ai-filter-send").first
        if send.count():
            send.click()
            page.wait_for_timeout(500)
            snap_ai(0.9)
            try:
                page.wait_for_selector(
                    ".ai-filter-thread .bubble, .ai-filter-result code", timeout=45000
                )
            except Exception:  # noqa: BLE001
                pass
            page.wait_for_timeout(700)
            snap_ai(1.8)
        if ai_frames:
            _frames_to_mp4(ai_frames, "ai-filter", poster_index=min(2, len(ai_frames) - 1))
            _save("ai-filter-still", ai_frames[-1][0])

        # Table + column picker / header funnel.
        filter_frames: list[tuple[bytes, float]] = []
        try:
            common = page.locator(".menu-item").filter(has_text=re.compile(r"我负责|My open")).first
            if common.count():
                common.click()
                page.wait_for_timeout(900)
        except Exception:  # noqa: BLE001
            pass

        def snap_table(hold: float = 1.2) -> None:
            # Include toolbar above the table when possible
            box = page.locator(".page").first
            filter_frames.append((box.screenshot(type="png"), hold))

        # Prefer a tighter crop of the results card
        results = page.locator(".card.search-table-wrap, .search-table-wrap").first
        if results.count():
            # Expand crop: parent col that has toolbar + table
            parent = page.locator(".page > .grid > .col, .page .col").last

            def snap_results(hold: float = 1.2) -> None:
                filter_frames.append((parent.screenshot(type="png"), hold))

            snap_results(1.4)
            cols = page.get_by_role("button", name=re.compile(r"列|Columns"))
            if cols.count():
                cols.first.click()
                page.wait_for_timeout(500)
                snap_results(1.6)
                page.keyboard.press("Escape")
                page.wait_for_timeout(300)
            # Open a header funnel if present
            funnel = page.locator(".th-filter-btn, button.th-filter-btn").first
            if funnel.count():
                funnel.click()
                page.wait_for_timeout(500)
                snap_results(1.8)
                page.keyboard.press("Escape")
            else:
                snap_results(1.2)
        else:
            snap_table(1.4)

        if filter_frames:
            _frames_to_mp4(filter_frames, "filters", poster_index=0)
            _save("filters-still", filter_frames[0][0], poster="filters")
    except Exception as exc:  # noqa: BLE001
        print(f"fail filters clips: {exc}", file=sys.stderr)
    finally:
        page.close()
        context.close()


def _mouse_drag(
    page,
    start: tuple[float, float],
    end: tuple[float, float],
    *,
    steps: int = 28,
    pause_ms: int = 70,
) -> None:
    """Slow arcing pointer drag so the DragOverlay reads clearly in the looping demo."""
    sx, sy = start
    ex, ey = end
    # Lift the card above the columns so the ghost crosses headers, not just neighbor cards.
    mid = ((sx + ex) / 2, min(sy, ey) - 70)
    page.mouse.move(sx, sy)
    page.wait_for_timeout(280)
    page.mouse.down()
    page.wait_for_timeout(220)

    def lerp(a: float, b: float, t: float) -> float:
        return a + (b - a) * t

    # Two-segment path: pick up → crest → drop. Hold briefly at the crest.
    for segment, (p0, p1, n) in enumerate(
        (
            (start, mid, max(8, steps // 3)),
            (mid, end, max(12, steps - steps // 3)),
        )
    ):
        for i in range(1, n + 1):
            t = i / n
            ease = t * t * (3 - 2 * t)
            page.mouse.move(lerp(p0[0], p1[0], ease), lerp(p0[1], p1[1], ease))
            page.wait_for_timeout(pause_ms)
        if segment == 0:
            page.wait_for_timeout(550)
    page.wait_for_timeout(500)
    page.mouse.up()
    page.wait_for_timeout(900)


def _demo_board_drag(page) -> bool:
    """Drag the first movable card into the next column. Returns True if a drag ran."""
    page.evaluate(
        """() => {
          const scroll = document.querySelector('.board-scroll');
          if (scroll) scroll.scrollLeft = 0;
        }"""
    )
    page.wait_for_timeout(300)
    columns = page.locator(".board-scroll .column")
    col_count = columns.count()
    if col_count < 2:
        print("board drag: fewer than 2 columns", file=sys.stderr)
        return False

    for src_i in range(col_count - 1):
        src = columns.nth(src_i)
        # Listeners live on .sortable-card (dnd-kit), not the inner .issue-card.
        card = src.locator(".sortable-card").first
        if card.count() == 0:
            continue
        dst_body = columns.nth(src_i + 1).locator(".column-body").first
        if dst_body.count() == 0:
            continue
        card.scroll_into_view_if_needed()
        page.wait_for_timeout(450)
        cbox = card.bounding_box()
        dbox = dst_body.bounding_box()
        if not cbox or not dbox:
            continue
        start = (cbox["x"] + cbox["width"] * 0.45, cbox["y"] + min(28.0, cbox["height"] * 0.35))
        # Drop near the top of the next column so the insert line / overlay is obvious.
        end = (dbox["x"] + dbox["width"] * 0.55, dbox["y"] + min(56.0, max(28.0, dbox["height"] * 0.18)))
        print(f"board drag: col {src_i} → {src_i + 1}")
        _mouse_drag(page, start, end)
        page.wait_for_timeout(800)
        return True

    print("board drag: no card found to drag", file=sys.stderr)
    return False


def _act_board_clip(page) -> bytes:
    """Show a real card drag across columns (Guide copy: drag to change status)."""
    page.goto(f"{BASE}/projects/ELEC/board", wait_until="networkidle")
    page.wait_for_timeout(1800)
    page.wait_for_selector(".column .sortable-card, .column .issue-card", timeout=20000)
    page.wait_for_timeout(900)
    png = page.screenshot(type="png")
    _demo_board_drag(page)
    page.wait_for_timeout(900)
    return png


def _capture_clips(browser, create_re) -> None:
    def settle(pg, url: str, wait_ms: int = 1400):
        pg.goto(url, wait_until="networkidle")
        pg.wait_for_timeout(wait_ms)

    def clip_my_work(pg):
        settle(pg, f"{BASE}/", wait_ms=1800)
        pg.wait_for_selector(".home-page, .home-greet, .page-header", timeout=20000)
        pg.wait_for_timeout(800)
        png = pg.screenshot(type="png")
        for _ in range(3):
            pg.mouse.wheel(0, 220)
            pg.wait_for_timeout(450)
        pg.mouse.wheel(0, -500)
        pg.wait_for_timeout(500)
        try:
            chip = pg.locator("button, .chip, .filter-chip").filter(
                has_text=re.compile(r"需关注|逾期|待测|分配")
            ).first
            if chip.count():
                chip.click()
                pg.wait_for_timeout(800)
        except Exception:  # noqa: BLE001
            pass
        pg.wait_for_timeout(600)
        return png

    def clip_list(pg):
        settle(pg, f"{BASE}/projects/ELEC/list", wait_ms=1800)
        pg.wait_for_selector(".sheet, .table, .list-row, tr", timeout=20000)
        pg.wait_for_timeout(800)
        png = pg.screenshot(type="png")
        pg.mouse.wheel(0, 200)
        pg.wait_for_timeout(900)
        return png

    def clip_palette(pg):
        settle(pg, f"{BASE}/")
        pg.keyboard.press("Control+K")
        pg.wait_for_selector(".palette", timeout=5000)
        pg.wait_for_timeout(500)
        png = pg.screenshot(type="png")
        search = pg.locator(".palette-search input").first
        search.fill("CMB")
        pg.wait_for_timeout(900)
        pg.keyboard.press("ArrowDown")
        pg.wait_for_timeout(500)
        return png

    def clip_ai_assistant(pg):
        settle(pg, f"{BASE}/")
        pg.locator("[data-ai-toggle]").first.click()
        pg.wait_for_selector(".ai-panel.open", timeout=5000)
        pg.wait_for_timeout(600)
        png = pg.screenshot(type="png")
        composer = pg.locator(".ai-panel.open .ai-input textarea").first
        composer.fill("总结我本周最需要关注的工作")
        pg.wait_for_timeout(900)
        composer.fill("")
        suggestion = pg.locator(".ai-panel.open .suggestion").first
        if suggestion.count():
            suggestion.hover()
            pg.wait_for_timeout(700)
        return png

    _record_create_clip(browser, create_re)
    _record_filters_clips(browser)

    for clip_name, fn in [
        ("my-work", clip_my_work),
        ("board", _act_board_clip),
        ("list", clip_list),
        ("workflow", _act_board_clip),
        ("palette", clip_palette),
        ("ai-assistant", clip_ai_assistant),
    ]:
        _record_clip(browser, clip_name, fn)


def main() -> int:
    import argparse

    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument(
        "--clips-only", action="store_true", help="only re-record demo mp4 clips"
    )
    parser.add_argument(
        "--create-only", action="store_true", help="only rebuild the create-issue demo"
    )
    parser.add_argument(
        "--board-only", action="store_true", help="only rebuild board/workflow drag demos"
    )
    parser.add_argument("--filters-only", action="store_true", help="only rebuild filter demos")
    parser.add_argument(
        "--trim-only", action="store_true", help="trim flash frames from existing mp4s"
    )
    parser.add_argument(
        "--anims-only",
        action="store_true",
        help="rebuild looping *-demo.webp from existing mp4s (no browser)",
    )
    parser.add_argument("--stills-only", action="store_true", help="only refresh webp stills")
    args = parser.parse_args()

    OUT.mkdir(parents=True, exist_ok=True)

    demo_names = (*CLIP_NAMES, "ai-assistant", "palette")

    if args.trim_only:
        for name in demo_names:
            if (OUT / f"{name}.mp4").is_file():
                _trim_existing_mp4(name)
        return 0

    if args.anims_only:
        for name in demo_names:
            if (OUT / f"{name}.mp4").is_file():
                _mp4_to_demo_anim(name)
        return 0

    try:
        from playwright.sync_api import sync_playwright
    except ImportError:
        print("playwright is required", file=sys.stderr)
        return 1

    create_re = re.compile(r"创建|Create")

    with sync_playwright() as p:
        browser = _browser(p)
        if args.create_only:
            _record_create_clip(browser, create_re)
            browser.close()
            print(f"wrote create demo to {OUT} (user={USER})")
            return 0
        if args.board_only:
            for name in ("board", "workflow"):
                _record_clip(browser, name, _act_board_clip)
            browser.close()
            print(f"wrote board/workflow demos to {OUT} (user={USER})")
            return 0
        if args.filters_only:
            _record_filters_clips(browser)
            browser.close()
            print(f"wrote filter demos to {OUT} (user={USER})")
            return 0
        if not args.clips_only:
            _capture_login_still(browser)
            context = _context(browser)
            page = context.new_page()
            _login(page, context)
            _capture_stills(page, create_re)
            page.close()
            context.close()
        if not args.stills_only:
            _capture_clips(browser, create_re)
        browser.close()

    print(f"wrote stills/clips to {OUT} (user={USER})")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
