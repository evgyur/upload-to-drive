---
name: upload-to-drive
description: Uploads a local file, inbound attachment, or direct media URL to Google Drive via gog and returns an anyone-with-link URL. Also accepts public YouTube/Instagram URLs as a best-effort download-first path via yt-dlp, with optional browser cookies and optional auth-maintenance hook integration. Use when the user says /upload, asks to make a public link from a file, or wants a YouTube/Instagram video downloaded first and then shared from Drive.
metadata:
  clawdbot:
    emoji: ☁️
    command: /upload
---

# /upload

Workflow skill for one job: turn an input into a Drive link.

This skill is a companion to:
- upstream `gog` skill — generic Drive CLI behavior
- an optional auth-maintenance hook — refresh/check auth before uploads when the operator has one

This skill does **not** replace `gog`. It wraps the missing workflow:
- accept a source
- download first when needed
- upload to Drive
- make the file public by link
- return the final Drive URL

## Trigger

Use this skill when the user says or implies:
- `/upload`
- "залей файл и дай ссылку"
- "сделай публичную ссылку"
- "загрузи это в Drive"
- "скачай YouTube и залей в Drive"
- "скачай Instagram reel и дай ссылку"

## Supported inputs

The source can be:
- a local file path
- an inbound attachment path from the current message
- a direct media URL
- a public YouTube URL (best-effort)
- a public Instagram post/reel URL (best-effort)

## Main command

```bash
python3 /opt/clawd-workspace/skills/public/upload-to-drive/scripts/upload_to_drive.py <source>
```

Optional custom name:

```bash
python3 /opt/clawd-workspace/skills/public/upload-to-drive/scripts/upload_to_drive.py <source> --name "Custom name.mp4"
```

Optional auth hook + account override:

```bash
python3 /opt/clawd-workspace/skills/public/upload-to-drive/scripts/upload_to_drive.py <source> \
  --account you@example.com \
  --auth-guard /path/to/your/auth_guard.sh
```

JSON output for automation:

```bash
python3 /opt/clawd-workspace/skills/public/upload-to-drive/scripts/upload_to_drive.py <source> --json
```

## Agent rules

1. If the user attached a file, prefer the attached local path as `<source>`.
2. If the user pasted a YouTube or Instagram URL, pass the URL directly.
3. Default result is a public Drive link (`anyone with link -> reader`).
4. If the source is private/login-gated/DRM-protected, stop and say so instead of pretending.
5. If the user wants a private Drive file instead of public sharing, do not use this default workflow without an explicit change.

## Config knobs

Environment variables:
- `GOG_ACCOUNT` — default Drive account
- `UPLOAD_TO_DRIVE_AUTH_GUARD` — optional auth-maintenance script path
- `UPLOAD_TO_DRIVE_YTDLP` — optional yt-dlp binary path
- `UPLOAD_TO_DRIVE_COOKIES_BROWSER` — optional browser name for yt-dlp cookies
- `UPLOAD_TO_DRIVE_COOKIES_PROFILE` — optional browser profile path for yt-dlp cookies
- `UPLOAD_TO_DRIVE_FFMPEG` — optional ffmpeg binary path

## Source handling

- **Local file / attachment** → upload directly
- **YouTube / Instagram** → best-effort download with `yt-dlp` and optional browser cookies, then upload
- **Direct media URL** → download with HTTP, then upload

## Output contract

Return:
1. final Drive link
2. short note about what was uploaded
3. honest failure reason when it breaks:
   - auth failed
   - download failed
   - unsupported/private URL
   - upload failed
   - public share verification failed

## Quick Test Checklist

- [ ] local file path uploads and returns a Drive URL
- [ ] inbound attachment path uploads and returns a Drive URL
- [ ] direct media URL downloads, uploads, and returns a Drive URL
- [ ] `--name` is preserved in Drive metadata
- [ ] optional `--account` works
- [ ] optional `--auth-guard` hook works when provided
- [ ] YouTube URL either uploads successfully or fails with an honest extractor/login reason
- [ ] Instagram URL either uploads successfully or fails with an honest extractor/login reason

## Done Criteria

- [ ] `/upload` is the command surface
- [ ] skill owns `source -> public Drive link` workflow, not generic `gog` behavior
- [ ] no hardcoded personal account, private browser profile, or private auth skill dependency remains
- [ ] local file and direct URL paths are verifiably working
- [ ] public sharing is verified, not assumed
- [ ] URL failures report the real blocker

## References

- `references/ops-and-inputs.md`
- `references/upstream-link.md`
