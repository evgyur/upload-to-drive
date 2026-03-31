# Upload-to-Drive — ops and input rules

## Purpose

Convert one source into one public Google Drive link.

## Upstream dependencies

- `gog` CLI handles the Drive upload/share primitives
- an optional auth-maintenance script can refresh/check auth before upload
- self-hosted `cobalt` is the preferred downloader backend for supported public sources
- `gallery-dl` is an Instagram-focused fallback backend
- `yt-dlp` remains a fallback backend for YouTube/Instagram
- `ffmpeg` supports yt-dlp merge flows when needed
- optional browser CDP can provide a relay-backed YouTube capture fallback

## Input matrix

| Input | Handling |
|------|----------|
| Local file path | Upload directly |
| Inbound attachment path | Upload directly |
| Direct media URL | HTTP download, then upload |
| Public Instagram reel/post URL | cobalt first, then gallery-dl, then embed fallback, then yt-dlp |
| Public YouTube URL | cobalt first, then yt-dlp, then optional browser-CDP relay capture fallback |

## Boundaries

- Public URLs only
- No DRM bypass
- No "maybe it worked" claims without final Drive link
- Browser cookies and CDP are optional operator config, not built-in secret dependencies
- A self-hosted cobalt API is recommended for serious Instagram reliability

## Operator steps

1. Resolve the user source.
2. Run the script:
   ```bash
   python3 /opt/clawd-workspace/skills/public/upload-to-drive/scripts/upload_to_drive.py <source>
   ```
3. Add optional flags/env only when needed:
   - `--account`
   - `--auth-guard`
   - `--cobalt-api`
   - `--gallery-dl`
   - `--ytdlp`
   - `--cookies-browser`
   - `--cookies-profile`
   - `--browser-cdp-base`
4. Return the printed Drive link.
5. If the script fails, report the real stage that failed.

## Reliability strategy

### Instagram
1. Try self-hosted cobalt with `alwaysProxy=true`
2. If cobalt is unavailable or fails, try `gallery-dl`
3. If that fails, try embed-page extraction
4. If that fails, try `yt-dlp`
5. If all fail, report the real blocker

### YouTube
1. Try self-hosted cobalt
2. If cobalt is blocked by login/session checks, try `yt-dlp`
3. If `yt-dlp` is blocked and `--browser-cdp-base` is configured, record the video inside the browser session via `captureStream()` + `MediaRecorder`
4. Transfer the captured blob back over CDP and upload it
5. If all fail, report the real blocker

## Failure map

- `auth failed` — auth probe or optional auth guard failed
- `download failed` — source URL could not be fetched or a downloader/fallback broke
- `unsupported/private URL` — page requires auth, is private, or is not a supported media source
- `upload failed` — `gog drive upload` failed
- `public share verification failed` — upload succeeded but public permission was not verified

## Quick test checklist

- [ ] local file path uploads and returns a Drive URL
- [ ] custom `--name` is preserved in Drive metadata
- [ ] `--json` returns parseable JSON with `link` and `file_id`
- [ ] public permission is verified, not assumed
- [ ] direct media URL works end-to-end
- [ ] Instagram reel/post works end-to-end via cobalt when cobalt is available
- [ ] YouTube works end-to-end via relay capture when a compatible browser CDP session is supplied
- [ ] unsupported/private URL fails honestly

## Manual review checklist

- [ ] skill command is `/upload`
- [ ] no private account or private profile path is hardcoded as the public default
- [ ] no copied upstream `gog` skill logic/docs beyond thin integration notes
- [ ] temp files are cleaned unless `--keep` is used
- [ ] filenames are sanitized enough for Drive upload
- [ ] failure messages are concrete and operator-usable
- [ ] cobalt/gallery-dl/yt-dlp ordering is clearly documented, not implied magic

## Operating spec

- cadence: on demand
- owner: `upload-to-drive`
- auth owner: operator-configured `gog` auth state, optionally with a custom auth hook
- transport: local file path or URL in, Drive URL out
- done criteria: final Drive link returned and public permission verified

## Assumptions + gaps

- assumes `gog` is installed and already authorized for the target Drive account
- assumes `cobalt` is available either via `UPLOAD_TO_DRIVE_COBALT_API` or a detected local instance
- assumes `gallery-dl` / `yt-dlp` are installed when those fallback layers are desired
- YouTube may still require a working cobalt YouTube session stack or browser-backed fallback
- platform anti-bot, rate-limit, or auth changes can still break extraction; the skill must fail honestly when that happens
