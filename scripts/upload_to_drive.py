#!/usr/bin/env python3
import argparse
import json
import mimetypes
import os
import re
import shutil
import subprocess
import sys
import tempfile
import urllib.parse
import urllib.request
from pathlib import Path
from typing import Optional, Tuple

DEFAULT_THRESHOLD_DAYS = "5"
DEFAULT_YTDLP_CANDIDATES = [
    "/opt/clawd-workspace/tools/yt-dlp-nightly/yt-dlp",
    shutil.which("yt-dlp") or "",
]
DEFAULT_GALLERY_DL_CANDIDATES = [
    shutil.which("gallery-dl") or "",
]
DEFAULT_COBALT_API_CANDIDATES = [
    "http://127.0.0.1:9469/",
]
DEFAULT_FFMPEG = shutil.which("ffmpeg") or "ffmpeg"

YOUTUBE_HOSTS = {
    "youtube.com",
    "www.youtube.com",
    "m.youtube.com",
    "music.youtube.com",
    "youtu.be",
    "www.youtu.be",
}
INSTAGRAM_HOSTS = {
    "instagram.com",
    "www.instagram.com",
}
DIRECT_MEDIA_EXTS = {
    ".mp4", ".mov", ".m4v", ".webm", ".mkv", ".mp3", ".m4a", ".wav",
    ".jpg", ".jpeg", ".png", ".gif", ".webp", ".pdf", ".zip",
}


class UploadError(RuntimeError):
    def __init__(self, stage: str, message: str):
        super().__init__(message)
        self.stage = stage
        self.message = message


def run(cmd, *, capture=True, check=True, text=True):
    return subprocess.run(cmd, capture_output=capture, check=check, text=text)


def account_args(account: str) -> list[str]:
    return ["-a", account] if account else []


def detect_binary(explicit: str | None, env_var: str, default_candidates: list[str]) -> str | None:
    candidates: list[str] = []
    if explicit:
        candidates.append(explicit)
    env_candidate = os.environ.get(env_var, "").strip()
    if env_candidate:
        candidates.append(env_candidate)
    candidates.extend([c for c in default_candidates if c])

    seen = set()
    for candidate in candidates:
        if candidate in seen:
            continue
        seen.add(candidate)
        if os.path.isabs(candidate) and os.path.exists(candidate):
            return candidate
        resolved = shutil.which(candidate)
        if resolved:
            return resolved
    return None


def detect_ytdlp(explicit: str | None) -> str | None:
    return detect_binary(explicit, "UPLOAD_TO_DRIVE_YTDLP", DEFAULT_YTDLP_CANDIDATES)


def detect_gallery_dl(explicit: str | None) -> str | None:
    return detect_binary(explicit, "UPLOAD_TO_DRIVE_GALLERY_DL", DEFAULT_GALLERY_DL_CANDIDATES)


def is_url(value: str) -> bool:
    p = urllib.parse.urlparse(value)
    return p.scheme in {"http", "https"} and bool(p.netloc)


def classify_url(url: str) -> str:
    host = urllib.parse.urlparse(url).netloc.lower()
    if host in YOUTUBE_HOSTS:
        return "youtube"
    if host in INSTAGRAM_HOSTS:
        return "instagram"
    return "direct_url"


def sanitize_name(name: str) -> str:
    name = name.strip().replace("\x00", "")
    name = re.sub(r"[\r\n\t]+", " ", name)
    name = re.sub(r"[/\\]+", "-", name)
    name = re.sub(r"\s+", " ", name).strip()
    return name[:180] or "upload"


def fetch_text(url: str, *, headers: Optional[dict[str, str]] = None, timeout: int = 60) -> str:
    req = urllib.request.Request(url, headers=headers or {"User-Agent": "Mozilla/5.0"})
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        return resp.read().decode("utf-8", "ignore")


def json_post(url: str, payload: dict, *, timeout: int = 120) -> dict:
    req = urllib.request.Request(
        url,
        data=json.dumps(payload).encode("utf-8"),
        headers={
            "Accept": "application/json",
            "Content-Type": "application/json",
            "User-Agent": "OpenClaw upload-to-drive/1.0",
        },
        method="POST",
    )
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        return json.loads(resp.read().decode("utf-8", "ignore"))


def normalize_base_url(url: str) -> str:
    return url if url.endswith("/") else url + "/"


def probe_cobalt_api(api_base: str, provider: Optional[str] = None) -> bool:
    try:
        base = normalize_base_url(api_base)
        req = urllib.request.Request(base, headers={"Accept": "application/json", "User-Agent": "OpenClaw upload-to-drive/1.0"})
        with urllib.request.urlopen(req, timeout=3) as resp:
            data = json.loads(resp.read().decode("utf-8", "ignore"))
        services = (data.get("cobalt") or {}).get("services") or []
        if provider and provider not in services:
            return False
        return "cobalt" in data
    except Exception:
        return False


def detect_cobalt_api(explicit: str | None, provider: Optional[str] = None) -> str | None:
    candidates: list[str] = []
    if explicit:
        candidates.append(explicit)
    env_candidate = os.environ.get("UPLOAD_TO_DRIVE_COBALT_API", "").strip()
    if env_candidate:
        candidates.append(env_candidate)
    candidates.extend(DEFAULT_COBALT_API_CANDIDATES)

    seen = set()
    for candidate in candidates:
        candidate = normalize_base_url(candidate)
        if candidate in seen:
            continue
        seen.add(candidate)
        if probe_cobalt_api(candidate, provider=provider):
            return candidate
    return None


def ensure_auth(account: str, guard_path: str | None) -> None:
    if guard_path:
        if not os.path.exists(guard_path):
            raise UploadError("auth failed", f"auth guard not found: {guard_path}")
        cmd = [guard_path]
        if account:
            cmd.extend(["--account", account, "--threshold-days", DEFAULT_THRESHOLD_DAYS])
        try:
            run(cmd, capture=True, check=True)
        except subprocess.CalledProcessError as exc:
            stderr = (exc.stderr or "").strip()
            stdout = (exc.stdout or "").strip()
            detail = stderr or stdout or "Drive auth guard failed"
            raise UploadError("auth failed", detail)

    probe_cmd = [
        "gog", "drive", "ls", *account_args(account),
        "--max", "1",
        "--no-input",
        "-j", "--results-only",
    ]
    try:
        run(probe_cmd, capture=True, check=True)
    except subprocess.CalledProcessError as exc:
        stderr = (exc.stderr or "").strip()
        stdout = (exc.stdout or "").strip()
        detail = stderr or stdout or "Drive auth probe failed"
        raise UploadError("auth failed", detail)


def ensure_tools_for_video_download(ytdlp_path: str | None, ffmpeg_path: str) -> None:
    if not ytdlp_path:
        raise UploadError("download failed", "yt-dlp not found; set UPLOAD_TO_DRIVE_YTDLP or install yt-dlp")
    ffmpeg_resolved = shutil.which(ffmpeg_path) if ffmpeg_path == "ffmpeg" else ffmpeg_path
    if not ffmpeg_resolved or not os.path.exists(ffmpeg_resolved):
        raise UploadError("download failed", f"ffmpeg not found: {ffmpeg_path}")


def direct_url_filename(url: str, headers) -> str:
    cd = headers.get("Content-Disposition", "")
    match = re.search(r"filename\*=UTF-8''([^;]+)", cd)
    if match:
        return urllib.parse.unquote(match.group(1))
    match = re.search(r'filename="?([^";]+)"?', cd)
    if match:
        return match.group(1)
    path_name = Path(urllib.parse.unquote(urllib.parse.urlparse(url).path)).name
    if path_name:
        return path_name
    ctype = headers.get_content_type() if hasattr(headers, "get_content_type") else headers.get("Content-Type", "application/octet-stream")
    ext = mimetypes.guess_extension(str(ctype).split(";")[0].strip()) or ".bin"
    return f"download{ext}"


def ensure_extension(filename: str, content_type: str | None) -> str:
    filename = sanitize_name(filename)
    if Path(filename).suffix:
        return filename
    if content_type:
        ext = mimetypes.guess_extension(content_type.split(";")[0].strip())
        if ext:
            return filename + ext
    return filename


def download_http(
    url: str,
    temp_dir: str,
    *,
    filename_hint: str | None = None,
    headers: Optional[dict[str, str]] = None,
    media_only: bool = False,
    timeout: int = 120,
) -> Tuple[str, str]:
    req_headers = {"User-Agent": "OpenClaw upload-to-drive/1.0"}
    if headers:
        req_headers.update(headers)
    req = urllib.request.Request(url, headers=req_headers)
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            resp_headers = resp.headers
            ctype = resp_headers.get_content_type() if hasattr(resp_headers, "get_content_type") else resp_headers.get("Content-Type", "")
            final_url = resp.geturl()
            path = urllib.parse.urlparse(final_url).path.lower()
            ext = Path(path).suffix.lower()
            if media_only and not (str(ctype).startswith(("video/", "audio/", "image/", "application/pdf")) or ext in DIRECT_MEDIA_EXTS):
                raise UploadError("unsupported/private URL", f"URL is not an obvious media file (content-type={ctype or 'unknown'})")
            filename = filename_hint or direct_url_filename(final_url, resp_headers)
            filename = ensure_extension(filename, ctype)
            local_path = os.path.join(temp_dir, filename)
            with open(local_path, "wb") as fh:
                shutil.copyfileobj(resp, fh)
    except UploadError:
        raise
    except Exception as exc:
        raise UploadError("download failed", str(exc))

    if not os.path.exists(local_path) or os.path.getsize(local_path) == 0:
        raise UploadError("download failed", "download produced no file")
    return local_path, Path(local_path).name


def download_direct_url(url: str, temp_dir: str) -> Tuple[str, str]:
    return download_http(url, temp_dir, media_only=True)


def download_any_url(url: str, temp_dir: str, *, filename_hint: str | None = None, headers: Optional[dict[str, str]] = None) -> Tuple[str, str]:
    return download_http(url, temp_dir, filename_hint=filename_hint, headers=headers, media_only=False)


def youtube_video_id(url: str) -> str | None:
    parsed = urllib.parse.urlparse(url)
    host = parsed.netloc.lower()
    if host in {"youtu.be", "www.youtu.be"}:
        return parsed.path.strip("/") or None
    qs = urllib.parse.parse_qs(parsed.query)
    if "v" in qs and qs["v"]:
        return qs["v"][0]
    m = re.search(r"/(shorts|live|embed)/([A-Za-z0-9_-]{11})", parsed.path)
    if m:
        return m.group(2)
    return None


def instagram_shortcode(url: str) -> str | None:
    parsed = urllib.parse.urlparse(url)
    m = re.search(r"/(?:reel|p)/([^/?#]+)/?", parsed.path)
    return m.group(1) if m else None


def cobalt_error_stage(code: str) -> str:
    lowered = code.lower()
    if any(token in lowered for token in ["login", "private", "unsupported", "auth"]):
        return "unsupported/private URL"
    return "download failed"


def download_via_cobalt(url: str, temp_dir: str, *, provider: str, cobalt_api: str) -> Tuple[str, str]:
    payload = {
        "url": url,
        "downloadMode": "auto",
        "filenameStyle": "basic",
        "alwaysProxy": True,
        "localProcessing": "disabled",
    }
    if provider == "youtube":
        payload.update({
            "videoQuality": "720",
            "youtubeVideoCodec": "h264",
            "youtubeVideoContainer": "mp4",
        })
    try:
        data = json_post(normalize_base_url(cobalt_api), payload, timeout=180)
    except Exception as exc:
        raise UploadError("download failed", f"cobalt request failed: {exc}")

    status = data.get("status")
    if status in {"tunnel", "redirect"}:
        return download_any_url(data["url"], temp_dir, filename_hint=data.get("filename"))
    if status == "picker":
        items = data.get("picker") or []
        chosen = next((item for item in items if item.get("type") == "video"), None) or (items[0] if items else None)
        if not chosen or not chosen.get("url"):
            raise UploadError("download failed", "cobalt returned a picker response without a downloadable item")
        filename = data.get("audioFilename") or f"{provider}_picker_item"
        return download_any_url(chosen["url"], temp_dir, filename_hint=filename)
    if status == "local-processing":
        output = data.get("output") or {}
        raise UploadError("download failed", f"cobalt requested local-processing for {output.get('filename') or provider}; that path is not implemented yet")
    if status == "error":
        err = data.get("error") or {}
        code = err.get("code", "error.api.unknown")
        raise UploadError(cobalt_error_stage(code), f"cobalt error: {code}")
    raise UploadError("download failed", f"unexpected cobalt response status: {status!r}")


def gallery_dl_cookie_arg(browser: str, profile: str | None, domain: str) -> str:
    if profile:
        return f"{browser}/{domain}:{profile}"
    return f"{browser}/{domain}"


def download_with_gallery_dl(
    url: str,
    temp_dir: str,
    *,
    gallery_dl_path: str | None,
    cookies_browser: str | None,
    cookies_profile: str | None,
) -> Tuple[str, str]:
    if not gallery_dl_path:
        raise UploadError("download failed", "gallery-dl not found")
    cmd = [gallery_dl_path, "--no-input", "--no-part", "-D", temp_dir]
    if cookies_browser:
        cmd.extend(["--cookies-from-browser", gallery_dl_cookie_arg(cookies_browser, cookies_profile, "instagram.com")])
    cmd.append(url)
    try:
        run(cmd, capture=True, check=True)
    except subprocess.CalledProcessError as exc:
        stderr = (exc.stderr or "").strip()
        stdout = (exc.stdout or "").strip()
        detail = stderr or stdout or "gallery-dl failed"
        if any(token in detail.lower() for token in ["login", "redirect to login", "private", "cookie"]):
            raise UploadError("unsupported/private URL", detail)
        raise UploadError("download failed", detail)

    candidates = [p for p in Path(temp_dir).rglob("*") if p.is_file() and not p.name.endswith((".part", ".json"))]
    if not candidates:
        raise UploadError("download failed", "gallery-dl finished but no output file was found")
    local_path = str(max(candidates, key=lambda p: p.stat().st_mtime))
    return local_path, Path(local_path).name


def download_instagram_embed(url: str, temp_dir: str) -> Tuple[str, str]:
    code = instagram_shortcode(url)
    if not code:
        raise UploadError("download failed", "could not extract Instagram shortcode")
    embed_url = f"https://www.instagram.com/reel/{code}/embed/"
    try:
        text = fetch_text(embed_url)
    except Exception as exc:
        raise UploadError("download failed", f"could not fetch Instagram embed page: {exc}")

    m = re.search(r'video_url\\":\\"(https:.*?\.mp4[^"\\]*)', text)
    if not m:
        raise UploadError("unsupported/private URL", "Instagram embed page did not expose a direct video URL")
    media_url = m.group(1)
    while "\\/" in media_url:
        media_url = media_url.replace("\\/", "/")
    local_path, _ = download_http(
        media_url,
        temp_dir,
        filename_hint=f"instagram_{code}.mp4",
        headers={"Referer": embed_url},
        media_only=True,
    )
    return local_path, Path(local_path).name


def download_youtube_via_browser_capture(url: str, temp_dir: str, browser_cdp_base: str) -> Tuple[str, str]:
    video_id = youtube_video_id(url) or "youtube"
    helper = os.path.join(os.path.dirname(__file__), "browser_cdp_youtube_capture.mjs")
    if not os.path.exists(helper):
        raise UploadError("download failed", f"browser capture helper not found: {helper}")
    node = shutil.which("node")
    if not node:
        raise UploadError("download failed", "browser fallback requires node in PATH")

    output_path = os.path.join(temp_dir, sanitize_name(f"youtube_{video_id}.webm"))
    try:
        proc = run([node, helper, browser_cdp_base, url, output_path], capture=True, check=True)
        payload = json.loads(proc.stdout)
    except subprocess.CalledProcessError as exc:
        detail = (exc.stderr or exc.stdout or "browser CDP capture helper failed").strip()
        raise UploadError("download failed", detail)
    except Exception as exc:
        raise UploadError("download failed", f"browser capture helper parse failed: {exc}")

    local_path = payload.get("outputPath") or output_path
    if not os.path.exists(local_path) or os.path.getsize(local_path) == 0:
        raise UploadError("download failed", "browser capture helper finished without a file")
    filename = payload.get("filename") or Path(local_path).name
    filename = sanitize_name(filename)
    final_path = os.path.join(temp_dir, filename)
    if local_path != final_path:
        os.replace(local_path, final_path)
    return final_path, Path(final_path).name


def download_with_ytdlp(
    url: str,
    temp_dir: str,
    *,
    ytdlp_path: str | None,
    ffmpeg_path: str,
    cookies_browser: str | None,
    cookies_profile: str | None,
) -> Tuple[str, str]:
    ensure_tools_for_video_download(ytdlp_path, ffmpeg_path)
    template = os.path.join(temp_dir, "%(title).180B [%(id)s].%(ext)s")
    cmd = [
        ytdlp_path,
        "--no-playlist",
        "--restrict-filenames",
        "--no-progress",
        "--newline",
        "--js-runtimes",
        "node",
    ]
    if cookies_browser and cookies_profile and os.path.exists(cookies_profile):
        cmd.extend(["--cookies-from-browser", f"{cookies_browser}:{cookies_profile}"])
    cmd.extend([
        "--merge-output-format",
        "mp4",
        "--ffmpeg-location",
        ffmpeg_path,
        "-o",
        template,
        "--print",
        "after_move:filepath",
        url,
    ])
    try:
        proc = run(cmd, capture=True, check=True)
    except subprocess.CalledProcessError as exc:
        stderr = (exc.stderr or "").strip()
        stdout = (exc.stdout or "").strip()
        detail = stderr or stdout or "yt-dlp download failed"
        if any(token in detail.lower() for token in ["login", "private", "members only", "sign in", "cookie", "bot"]):
            raise UploadError("unsupported/private URL", detail)
        raise UploadError("download failed", detail)

    lines = [line.strip() for line in (proc.stdout or "").splitlines() if line.strip()]
    candidates = [line for line in lines if os.path.exists(line)]
    if not candidates:
        candidates = [str(p) for p in Path(temp_dir).glob("*") if p.is_file()]
    if not candidates:
        raise UploadError("download failed", "yt-dlp finished but no output file was found")
    local_path = max(candidates, key=lambda p: Path(p).stat().st_mtime)
    return local_path, Path(local_path).name


def download_youtube(
    url: str,
    temp_dir: str,
    *,
    cobalt_api: str | None,
    ytdlp_path: str | None,
    ffmpeg_path: str,
    cookies_browser: str | None,
    cookies_profile: str | None,
    browser_cdp_base: str | None,
) -> Tuple[str, str]:
    errors: list[str] = []

    if cobalt_api:
        try:
            return download_via_cobalt(url, temp_dir, provider="youtube", cobalt_api=cobalt_api)
        except UploadError as exc:
            errors.append(f"cobalt failed: {exc.message}")

    if ytdlp_path:
        try:
            return download_with_ytdlp(
                url,
                temp_dir,
                ytdlp_path=ytdlp_path,
                ffmpeg_path=ffmpeg_path,
                cookies_browser=cookies_browser,
                cookies_profile=cookies_profile,
            )
        except UploadError as exc:
            errors.append(f"yt-dlp failed: {exc.message}")

    if browser_cdp_base:
        try:
            return download_youtube_via_browser_capture(url, temp_dir, browser_cdp_base)
        except UploadError as exc:
            errors.append(f"browser fallback failed: {exc.message}")

    detail = " | ".join(errors) if errors else "no YouTube downloader path available"
    raise UploadError("download failed", detail)


def download_instagram(
    url: str,
    temp_dir: str,
    *,
    cobalt_api: str | None,
    gallery_dl_path: str | None,
    ytdlp_path: str | None,
    ffmpeg_path: str,
    cookies_browser: str | None,
    cookies_profile: str | None,
) -> Tuple[str, str]:
    errors: list[str] = []

    if cobalt_api:
        try:
            return download_via_cobalt(url, temp_dir, provider="instagram", cobalt_api=cobalt_api)
        except UploadError as exc:
            errors.append(f"cobalt failed: {exc.message}")

    if gallery_dl_path:
        try:
            return download_with_gallery_dl(
                url,
                temp_dir,
                gallery_dl_path=gallery_dl_path,
                cookies_browser=cookies_browser,
                cookies_profile=cookies_profile,
            )
        except UploadError as exc:
            errors.append(f"gallery-dl failed: {exc.message}")

    try:
        return download_instagram_embed(url, temp_dir)
    except UploadError as exc:
        errors.append(f"embed fallback failed: {exc.message}")

    if ytdlp_path:
        try:
            return download_with_ytdlp(
                url,
                temp_dir,
                ytdlp_path=ytdlp_path,
                ffmpeg_path=ffmpeg_path,
                cookies_browser=cookies_browser,
                cookies_profile=cookies_profile,
            )
        except UploadError as exc:
            errors.append(f"yt-dlp failed: {exc.message}")

    detail = " | ".join(errors) if errors else "no Instagram downloader path available"
    raise UploadError("download failed", detail)


def resolve_source(
    source: str,
    *,
    cobalt_api: str | None,
    gallery_dl_path: str | None,
    ytdlp_path: str | None,
    ffmpeg_path: str,
    cookies_browser: str | None,
    cookies_profile: str | None,
    browser_cdp_base: str | None,
) -> Tuple[str, str, Optional[str]]:
    if os.path.isfile(source):
        return source, Path(source).name, None
    if not is_url(source):
        raise UploadError("download failed", f"source is neither a file path nor an http(s) URL: {source}")

    temp_dir = tempfile.mkdtemp(prefix="upload-to-drive-")
    provider = classify_url(source)
    if provider == "youtube":
        local_path, suggested_name = download_youtube(
            source,
            temp_dir,
            cobalt_api=cobalt_api,
            ytdlp_path=ytdlp_path,
            ffmpeg_path=ffmpeg_path,
            cookies_browser=cookies_browser,
            cookies_profile=cookies_profile,
            browser_cdp_base=browser_cdp_base,
        )
    elif provider == "instagram":
        local_path, suggested_name = download_instagram(
            source,
            temp_dir,
            cobalt_api=cobalt_api,
            gallery_dl_path=gallery_dl_path,
            ytdlp_path=ytdlp_path,
            ffmpeg_path=ffmpeg_path,
            cookies_browser=cookies_browser,
            cookies_profile=cookies_profile,
        )
    else:
        local_path, suggested_name = download_direct_url(source, temp_dir)
    return local_path, suggested_name, temp_dir


def upload_file(local_path: str, drive_name: str, account: str) -> Tuple[str, str]:
    try:
        proc = run(
            [
                "gog", "drive", "upload", local_path,
                *account_args(account),
                "--name", drive_name,
                "--no-input",
                "-j", "--results-only",
            ],
            capture=True,
            check=True,
        )
    except subprocess.CalledProcessError as exc:
        stderr = (exc.stderr or "").strip()
        stdout = (exc.stdout or "").strip()
        detail = stderr or stdout or "gog drive upload failed"
        raise UploadError("upload failed", detail)

    try:
        data = json.loads(proc.stdout)
    except Exception as exc:
        raise UploadError("upload failed", f"could not parse gog upload output: {exc}")

    file_id = data.get("id")
    link = data.get("webViewLink")
    if not file_id:
        raise UploadError("upload failed", "gog upload returned no file id")
    return file_id, link or ""


def share_public(file_id: str, account: str) -> None:
    try:
        run(
            [
                "gog", "drive", "share", file_id,
                *account_args(account),
                "--to", "anyone",
                "--role", "reader",
                "--force",
                "--no-input",
                "-j", "--results-only",
            ],
            capture=True,
            check=True,
        )
    except subprocess.CalledProcessError as exc:
        stderr = (exc.stderr or "").strip()
        stdout = (exc.stdout or "").strip()
        detail = stderr or stdout or "gog drive share failed"
        raise UploadError("public share verification failed", detail)

    try:
        proc = run(
            [
                "gog", "drive", "permissions", file_id,
                *account_args(account),
                "--no-input",
                "-j", "--results-only",
            ],
            capture=True,
            check=True,
        )
        perms = json.loads(proc.stdout)
    except subprocess.CalledProcessError as exc:
        stderr = (exc.stderr or "").strip()
        stdout = (exc.stdout or "").strip()
        detail = stderr or stdout or "could not read permissions"
        raise UploadError("public share verification failed", detail)
    except Exception as exc:
        raise UploadError("public share verification failed", f"could not parse permissions output: {exc}")

    if isinstance(perms, dict):
        perms = [perms]
    ok = any(perm.get("type") == "anyone" and perm.get("role") in {"reader", "writer", "owner"} for perm in perms or [])
    if not ok:
        raise UploadError("public share verification failed", "uploaded file but anyone-with-link permission was not verified")


def fill_link(file_id: str, current_link: str, account: str) -> str:
    if current_link:
        return current_link
    try:
        proc = run(
            ["gog", "drive", "get", file_id, *account_args(account), "--no-input", "-j", "--results-only"],
            capture=True,
            check=True,
        )
        data = json.loads(proc.stdout)
        return data.get("webViewLink") or f"https://drive.google.com/file/d/{file_id}/view?usp=drivesdk"
    except Exception:
        return f"https://drive.google.com/file/d/{file_id}/view?usp=drivesdk"


def main() -> int:
    parser = argparse.ArgumentParser(description="Upload a file or public media URL to Drive and return a public link")
    parser.add_argument("source", help="local file path or public URL")
    parser.add_argument("--name", help="override Drive filename")
    parser.add_argument("--json", action="store_true", help="print JSON instead of plain link")
    parser.add_argument("--keep", action="store_true", help="keep downloaded temp files on disk")
    parser.add_argument("--account", default=os.environ.get("GOG_ACCOUNT", ""), help="Drive account email; defaults to GOG_ACCOUNT or gog default account")
    parser.add_argument("--auth-guard", default=os.environ.get("UPLOAD_TO_DRIVE_AUTH_GUARD", ""), help="optional auth-maintenance script to run before upload")
    parser.add_argument("--cobalt-api", default=os.environ.get("UPLOAD_TO_DRIVE_COBALT_API", ""), help="optional self-hosted cobalt API base URL")
    parser.add_argument("--gallery-dl", default=os.environ.get("UPLOAD_TO_DRIVE_GALLERY_DL", ""), help="gallery-dl binary path for Instagram fallback")
    parser.add_argument("--ytdlp", default=os.environ.get("UPLOAD_TO_DRIVE_YTDLP", ""), help="yt-dlp binary path for YouTube/Instagram fallbacks")
    parser.add_argument("--cookies-browser", default=os.environ.get("UPLOAD_TO_DRIVE_COOKIES_BROWSER", ""), help="optional browser name for yt-dlp/gallery-dl cookies-from-browser")
    parser.add_argument("--cookies-profile", default=os.environ.get("UPLOAD_TO_DRIVE_COOKIES_PROFILE", ""), help="optional browser profile path for cookies-from-browser")
    parser.add_argument("--browser-cdp-base", default=os.environ.get("UPLOAD_TO_DRIVE_BROWSER_CDP_BASE", ""), help="optional browser CDP base URL for YouTube fallback, e.g. http://127.0.0.1:18800")
    parser.add_argument("--ffmpeg", default=os.environ.get("UPLOAD_TO_DRIVE_FFMPEG", DEFAULT_FFMPEG), help="ffmpeg binary path")
    args = parser.parse_args()

    temp_dir = None
    source_was_file = os.path.isfile(args.source)
    source_type = "file" if source_was_file else (classify_url(args.source) if is_url(args.source) else "unknown")
    ytdlp_path = detect_ytdlp(args.ytdlp or None)
    gallery_dl_path = detect_gallery_dl(args.gallery_dl or None)
    cobalt_api = detect_cobalt_api(args.cobalt_api or None, provider=source_type) if source_type in {"youtube", "instagram"} else None

    try:
        ensure_auth(args.account, args.auth_guard or None)
        local_path, suggested_name, temp_dir = resolve_source(
            args.source,
            cobalt_api=cobalt_api,
            gallery_dl_path=gallery_dl_path,
            ytdlp_path=ytdlp_path,
            ffmpeg_path=args.ffmpeg,
            cookies_browser=args.cookies_browser or None,
            cookies_profile=args.cookies_profile or None,
            browser_cdp_base=args.browser_cdp_base or None,
        )
        drive_name = sanitize_name(args.name or suggested_name)
        file_id, link = upload_file(local_path, drive_name, args.account)
        share_public(file_id, args.account)
        link = fill_link(file_id, link, args.account)

        result = {
            "source": args.source,
            "source_type": source_type,
            "downloaded": not source_was_file,
            "local_path": local_path if (args.keep or source_was_file) else None,
            "drive_name": drive_name,
            "file_id": file_id,
            "link": link,
            "account": args.account or None,
            "auth_guard": args.auth_guard or None,
            "cobalt_api": cobalt_api,
            "gallery_dl": gallery_dl_path,
            "browser_cdp_base": args.browser_cdp_base or None,
        }
        if args.json:
            print(json.dumps(result, ensure_ascii=False, indent=2))
        else:
            print(link)
        return 0
    except UploadError as exc:
        payload = {"error": exc.stage, "message": exc.message, "source": args.source}
        if args.json:
            print(json.dumps(payload, ensure_ascii=False, indent=2), file=sys.stderr)
        else:
            print(f"{exc.stage}: {exc.message}", file=sys.stderr)
        return 1
    finally:
        if temp_dir and not args.keep:
            shutil.rmtree(temp_dir, ignore_errors=True)


if __name__ == "__main__":
    raise SystemExit(main())
