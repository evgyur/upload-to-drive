# upload-to-drive

OpenClaw skill that turns an input into a public Google Drive link.

## What it does

- uploads a local file to Google Drive via `gog`
- uploads inbound attachment paths
- downloads a direct media URL first, then uploads it
- best-effort downloads public YouTube/Instagram URLs via `yt-dlp`, then uploads them
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
- `UPLOAD_TO_DRIVE_FFMPEG`

## Notes

- `gog` must already be installed and authorized for the target Drive account.
- YouTube/Instagram support is best-effort. Platforms may require login, cookies, or block extraction.
- This repo is intentionally separate from auth-specific automation. Auth refresh/cron logic should live in an environment-specific companion skill or operator hook.

## Files

- `SKILL.md`
- `scripts/upload_to_drive.py`
- `references/ops-and-inputs.md`
- `references/upstream-link.md`
