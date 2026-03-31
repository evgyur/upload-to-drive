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


def detect_ytdlp(explicit: str | None) -> str | None:
    candidates = []
    if explicit:
        candidates.append(explicit)
    env_candidate = os.environ.get("UPLOAD_TO_DRIVE_YTDLP", "").strip()
    if env_candidate:
        candidates.append(env_candidate)
    candidates.extend([c for c in DEFAULT_YTDLP_CANDIDATES if c])
    for candidate in candidates:
        if os.path.isabs(candidate) and os.path.exists(candidate):
            return candidate
        resolved = shutil.which(candidate)
        if resolved:
            return resolved
    return None


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


def fetch_text(url: str, *, headers: Optional[dict[str, str]] = None, timeout: int = 60) -> str:
    req = urllib.request.Request(url, headers=headers or {"User-Agent": "Mozilla/5.0"})
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        return resp.read().decode("utf-8", "ignore")


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
    ext = mimetypes.guess_extension(ctype.split(";")[0].strip()) or ".bin"
    return f"download{ext}"


def download_direct_url(url: str, temp_dir: str) -> Tuple[str, str]:
    req = urllib.request.Request(url, headers={"User-Agent": "OpenClaw upload-to-drive/1.0"})
    try:
        with urllib.request.urlopen(req, timeout=60) as resp:
            headers = resp.headers
            ctype = headers.get_content_type() if hasattr(headers, "get_content_type") else headers.get("Content-Type", "")
            path = urllib.parse.urlparse(url).path.lower()
            ext = Path(path).suffix.lower()
            if not (str(ctype).startswith(("video/", "audio/", "image/", "application/pdf")) or ext in DIRECT_MEDIA_EXTS):
                raise UploadError("unsupported/private URL", f"direct URL is not an obvious media file (content-type={ctype or 'unknown'})")
            filename = sanitize_name(direct_url_filename(url, headers))
            local_path = os.path.join(temp_dir, filename)
            with open(local_path, "wb") as fh:
                shutil.copyfileobj(resp, fh)
    except UploadError:
        raise
    except Exception as exc:
        raise UploadError("download failed", str(exc))

    if not os.path.exists(local_path) or os.path.getsize(local_path) == 0:
        raise UploadError("download failed", "direct URL download produced no file")
    return local_path, filename


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
    local_path, _ = download_direct_url(media_url, temp_dir)
    filename = sanitize_name(f"instagram_{code}.mp4")
    final_path = os.path.join(temp_dir, filename)
    if local_path != final_path:
        os.replace(local_path, final_path)
    return final_path, filename


def browser_youtube_media_url(url: str, browser_cdp_base: str) -> Tuple[str, str]:
    video_id = youtube_video_id(url)
    helper = os.path.join(os.path.dirname(__file__), "browser_cdp_youtube.mjs")
    if not os.path.exists(helper):
        raise UploadError("download failed", f"browser helper not found: {helper}")
    node = shutil.which("node")
    if not node:
        raise UploadError("download failed", "browser fallback requires node in PATH")
    try:
        proc = run([node, helper, browser_cdp_base, url], capture=True, check=True)
        payload = json.loads(proc.stdout)
    except subprocess.CalledProcessError as exc:
        detail = (exc.stderr or exc.stdout or "browser CDP helper failed").strip()
        raise UploadError("download failed", detail)
    except Exception as exc:
        raise UploadError("download failed", f"browser helper parse failed: {exc}")

    if not payload:
        raise UploadError("download failed", "browser fallback did not produce any page payload")
    usable = [x for x in payload.get("formats") or [] if x.get("url") and "video/mp4" in (x.get("mimeType") or "")]
    if not usable:
        status = (payload.get("playability") or {}).get("status")
        reason = (payload.get("playability") or {}).get("reason") or "no usable mp4 format exposed"
        if status and status != "OK":
            raise UploadError("unsupported/private URL", f"YouTube browser fallback blocked: {status} ({reason})")
        raise UploadError("download failed", f"YouTube browser fallback found no direct mp4 format ({reason})")
    usable.sort(key=lambda x: (x.get("height") or 0, x.get("bitrate") or 0))
    best = usable[-1]
    title = sanitize_name(payload.get("title") or (video_id or "youtube_video"))
    return best["url"], f"{title}.mp4"


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
    ytdlp_path: str | None,
    ffmpeg_path: str,
    cookies_browser: str | None,
    cookies_profile: str | None,
    browser_cdp_base: str | None,
) -> Tuple[str, str]:
    first_error: Optional[UploadError] = None
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
            first_error = exc

    if browser_cdp_base:
        try:
            media_url, filename = browser_youtube_media_url(url, browser_cdp_base)
            local_path, _ = download_direct_url(media_url, temp_dir)
            final_path = os.path.join(temp_dir, sanitize_name(filename))
            if local_path != final_path:
                os.replace(local_path, final_path)
            return final_path, Path(final_path).name
        except UploadError as exc:
            if first_error:
                raise UploadError(exc.stage, f"yt-dlp failed: {first_error.message} | browser fallback failed: {exc.message}")
            raise

    if first_error:
        raise first_error
    raise UploadError("download failed", "no YouTube downloader path available")


def download_instagram(
    url: str,
    temp_dir: str,
    *,
    ytdlp_path: str | None,
    ffmpeg_path: str,
    cookies_browser: str | None,
    cookies_profile: str | None,
) -> Tuple[str, str]:
    try:
        return download_instagram_embed(url, temp_dir)
    except UploadError as embed_error:
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
            except UploadError as ytdlp_error:
                raise UploadError(ytdlp_error.stage, f"embed fallback failed: {embed_error.message} | yt-dlp failed: {ytdlp_error.message}")
        raise


def resolve_source(
    source: str,
    *,
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
    parser.add_argument("--ytdlp", default=os.environ.get("UPLOAD_TO_DRIVE_YTDLP", ""), help="yt-dlp binary path for YouTube/Instagram downloads")
    parser.add_argument("--cookies-browser", default=os.environ.get("UPLOAD_TO_DRIVE_COOKIES_BROWSER", ""), help="optional browser name for yt-dlp cookies-from-browser")
    parser.add_argument("--cookies-profile", default=os.environ.get("UPLOAD_TO_DRIVE_COOKIES_PROFILE", ""), help="optional browser profile path for yt-dlp cookies-from-browser")
    parser.add_argument("--browser-cdp-base", default=os.environ.get("UPLOAD_TO_DRIVE_BROWSER_CDP_BASE", ""), help="optional browser CDP base URL for YouTube fallback, e.g. http://127.0.0.1:18800")
    parser.add_argument("--ffmpeg", default=os.environ.get("UPLOAD_TO_DRIVE_FFMPEG", DEFAULT_FFMPEG), help="ffmpeg binary path")
    args = parser.parse_args()

    temp_dir = None
    source_was_file = os.path.isfile(args.source)
    source_type = "file" if source_was_file else (classify_url(args.source) if is_url(args.source) else "unknown")
    ytdlp_path = detect_ytdlp(args.ytdlp or None)

    try:
        ensure_auth(args.account, args.auth_guard or None)
        local_path, suggested_name, temp_dir = resolve_source(
            args.source,
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
