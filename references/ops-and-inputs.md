# Upload-to-Drive — ops and input rules

## Purpose

Convert one source into one public Google Drive link.

## Upstream dependencies

- `gog` CLI handles the Drive upload/share primitives
- an optional auth-maintenance script can refresh/check auth before upload
- `yt-dlp` handles YouTube/Instagram downloads when available
- `ffmpeg` supports yt-dlp merge flows when needed
- optional browser CDP can improve YouTube reliability when a live browser session exists

## Input matrix

| Input | Handling |
|------|----------|
| Local file path | Upload directly |
| Inbound attachment path | Upload directly |
| Direct media URL | HTTP download, then upload |
| Public Instagram reel/post URL | Embed-page video extraction first, then yt-dlp fallback |
| Public YouTube URL | yt-dlp first, then optional browser-CDP fallback |

## Boundaries

- Public URLs only
- No DRM bypass
- No "maybe it worked" claims without final Drive link
- Browser cookies and CDP are optional operator config, not built-in secret dependencies

## Operator steps

1. Resolve the user source.
2. Run the script:
   ```bash
   python3 /opt/clawd-workspace/skills/public/upload-to-drive/scripts/upload_to_drive.py <source>
   ```
3. Add optional flags/env only when needed:
   - `--account`
   - `--auth-guard`
   - `--ytdlp`
   - `--cookies-browser`
   - `--cookies-profile`
   - `--browser-cdp-base`
4. Return the printed Drive link.
5. If the script fails, report the real stage that failed.

## Failure map

- `auth failed` — auth probe or optional auth guard failed
- `download failed` — source URL could not be fetched or an extractor/fallback broke
- `unsupported/private URL` — page requires auth, is private, or is not a supported media source
- `upload failed` — `gog drive upload` failed
- `public share verification failed` — upload succeeded but public permission was not verified

## Reliability strategy

### Instagram
1. Try the public embed page and extract `video_url`
2. Download the exposed mp4 directly
3. If that fails, try `yt-dlp`
4. If both fail, report the actual blocker

### YouTube
1. Try `yt-dlp`
2. If `yt-dlp` is blocked and `--browser-cdp-base` is configured, open the video in the browser session
3. Read `ytInitialPlayerResponse` from the live page
4. Use the best directly exposed mp4 format URL
5. If no direct format exists, fail honestly instead of pretending success

## Quick test checklist

- [ ] local file path uploads and returns a Drive URL
- [ ] custom `--name` is preserved in Drive metadata
- [ ] `--json` returns parseable JSON with `link` and `file_id`
- [ ] public permission is verified, not assumed
- [ ] direct media URL works end-to-end
- [ ] Instagram reel/post works end-to-end via embed fallback
- [ ] YouTube works end-to-end when a compatible browser CDP session is supplied
- [ ] unsupported/private URL fails honestly

## Manual review checklist

- [ ] skill command is `/upload`
- [ ] no private account or private profile path is hardcoded as the public default
- [ ] no copied upstream `gog` skill logic/docs beyond thin integration notes
- [ ] temp files are cleaned unless `--keep` is used
- [ ] filenames are sanitized enough for Drive upload
- [ ] failure messages are concrete and operator-usable
- [ ] YouTube/Instagram fallbacks are clearly documented, not implied magic

## Operating spec

- cadence: on demand
- owner: `upload-to-drive`
- auth owner: operator-configured `gog` auth state, optionally with a custom auth hook
- transport: local file path or URL in, Drive URL out
- done criteria: final Drive link returned and public permission verified

## Assumptions + gaps

- assumes `gog` is installed and already authorized for the target Drive account
- assumes `yt-dlp` is available either at `/opt/clawd-workspace/tools/yt-dlp-nightly/yt-dlp` or in PATH
- YouTube browser fallback assumes a browser session that can load the target video without a login wall
- Platform anti-bot, rate-limit, or auth changes can still break extraction; the skill must fail honestly when that happens
