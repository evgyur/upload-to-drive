# upload-to-drive

OpenClaw skill that turns an input into a public Google Drive link.

## What it does

- uploads a local file to Google Drive via `gog`
- uploads inbound attachment paths
- downloads a direct media URL first, then uploads it
- handles Instagram with a direct embed-page video fallback
- handles YouTube with `yt-dlp` first and an optional browser-CDP fallback
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
- `UPLOAD_TO_DRIVE_YTDLP`
- `UPLOAD_TO_DRIVE_COOKIES_BROWSER`
- `UPLOAD_TO_DRIVE_COOKIES_PROFILE`
- `UPLOAD_TO_DRIVE_BROWSER_CDP_BASE`
- `UPLOAD_TO_DRIVE_FFMPEG`

## Reliability notes

### Instagram
The script first tries the public embed page and extracts a direct `video_url`. This is often more reliable than relying on `yt-dlp` alone.

### YouTube
The script tries `yt-dlp` first. If that is blocked and a browser CDP endpoint is supplied, it opens the live page in the browser session, reads `ytInitialPlayerResponse`, and uses the best directly exposed mp4 format URL.

## Notes

- `gog` must already be installed and authorized for the target Drive account.
- Some YouTube/Instagram URLs may still hit login walls, anti-bot checks, or rate limits.
- This repo is intentionally separate from auth-specific automation. Auth refresh/cron logic should live in an environment-specific companion skill or operator hook.

## Files

- `SKILL.md`
- `scripts/upload_to_drive.py`
- `references/ops-and-inputs.md`
- `references/upstream-link.md`
