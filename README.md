# upload-to-drive

OpenClaw skill that turns an input into a public Google Drive link.

## What it does

- uploads a local file to Google Drive via `gog`
- uploads inbound attachment paths
- downloads a direct media URL first, then uploads it
- uses self-hosted `cobalt` as the preferred downloader backend for supported public sources
- uses `gallery-dl` as an Instagram fallback backend
- uses `yt-dlp` as another fallback backend
- verifies that `anyone with link -> reader` is actually applied before returning the final URL

## Command

```bash
/upload
```

## Main script

```bash
python3 /opt/clawd-workspace/skills/public/upload-to-drive/scripts/upload_to_drive.py <source>
```

## Examples

Upload a local file:

```bash
python3 /opt/clawd-workspace/skills/public/upload-to-drive/scripts/upload_to_drive.py ./video.mp4
```

Upload with custom Drive filename:

```bash
python3 /opt/clawd-workspace/skills/public/upload-to-drive/scripts/upload_to_drive.py ./video.mp4 --name "Workshop clip.mp4"
```

Use a specific Drive account and optional auth hook:

```bash
python3 /opt/clawd-workspace/skills/public/upload-to-drive/scripts/upload_to_drive.py ./video.mp4 \
  --account you@example.com \
  --auth-guard /path/to/your/auth_guard.sh
```

Use a self-hosted cobalt API:

```bash
python3 /opt/clawd-workspace/skills/public/upload-to-drive/scripts/upload_to_drive.py 'https://www.instagram.com/reel/...' \
  --cobalt-api http://127.0.0.1:9469/
```

Use browser-CDP fallback for YouTube:

```bash
python3 /opt/clawd-workspace/skills/public/upload-to-drive/scripts/upload_to_drive.py 'https://www.youtube.com/watch?v=...' \
  --browser-cdp-base http://127.0.0.1:18800
```

JSON output:

```bash
python3 /opt/clawd-workspace/skills/public/upload-to-drive/scripts/upload_to_drive.py ./video.mp4 --json
```

## Config

Environment variables:

- `GOG_ACCOUNT`
- `UPLOAD_TO_DRIVE_AUTH_GUARD`
- `UPLOAD_TO_DRIVE_COBALT_API`
- `UPLOAD_TO_DRIVE_GALLERY_DL`
- `UPLOAD_TO_DRIVE_YTDLP`
- `UPLOAD_TO_DRIVE_COOKIES_BROWSER`
- `UPLOAD_TO_DRIVE_COOKIES_PROFILE`
- `UPLOAD_TO_DRIVE_BROWSER_CDP_BASE`
- `UPLOAD_TO_DRIVE_FFMPEG`

## Reliability notes

### Instagram
The best current path is a self-hosted `cobalt` instance with `alwaysProxy=true`. This avoids brittle direct media URL scraping and works well for public reels/posts in practice.

### YouTube
The script tries a self-hosted `cobalt` instance first, then `yt-dlp`, then optional browser-CDP fallback. A session-capable cobalt setup is the cleanest long-term route for YouTube.

## Notes

- `gog` must already be installed and authorized for the target Drive account.
- `cobalt` is recommended if you want better Instagram reliability.
- Some YouTube/Instagram URLs may still hit login walls, anti-bot checks, or rate limits.
- This repo is intentionally separate from auth-specific automation. Auth refresh/cron logic should live in an environment-specific companion skill or operator hook.

## Files

- `SKILL.md`
- `scripts/upload_to_drive.py`
- `scripts/browser_cdp_youtube.mjs`
- `references/ops-and-inputs.md`
- `references/upstream-link.md`
