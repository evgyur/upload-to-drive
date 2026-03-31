# Upload-to-Drive — ops and input rules

## Purpose

Convert one source into one public Google Drive link.

## Upstream dependencies

- `gog` CLI handles the Drive upload/share primitives
- an optional auth-maintenance script can refresh/check auth before upload
- `yt-dlp` handles public YouTube/Instagram downloads when available
- `ffmpeg` merges media streams when needed

## Input matrix

| Input | Handling |
|------|----------|
| Local file path | Upload directly |
| Inbound attachment path | Upload directly |
| Direct media URL | HTTP download, then upload |
| Public YouTube URL | best-effort `yt-dlp`, then upload |
| Public Instagram reel/post URL | best-effort `yt-dlp`, then upload |

## Boundaries

- Public URLs only
- No DRM bypass
- No "maybe it worked" claims without final Drive link
- Browser cookies are optional operator config, not a built-in secret dependency

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
4. Return the printed Drive link.
5. If the script fails, report the real stage that failed.

## Failure map

- `auth failed` — auth probe or optional auth guard failed
- `download failed` — source URL could not be fetched or extractor broke
- `unsupported/private URL` — page requires auth, is private, or is not a supported media source
- `upload failed` — `gog drive upload` failed
- `public share verification failed` — upload succeeded but public permission was not verified

## Quick test checklist

- [ ] local file path uploads and returns a Drive URL
- [ ] custom `--name` is preserved in Drive metadata
- [ ] `--json` returns parseable JSON with `link` and `file_id`
- [ ] public permission is verified, not assumed
- [ ] direct media URL works end-to-end
- [ ] unsupported/private URL fails honestly
- [ ] YouTube/Instagram failures surface real extractor/login blockers

## Manual review checklist

- [ ] skill command is `/upload`
- [ ] no private account or private profile path is hardcoded as the public default
- [ ] no copied upstream `gog` skill logic/docs beyond thin integration notes
- [ ] temp files are cleaned unless `--keep` is used
- [ ] filenames are sanitized enough for Drive upload
- [ ] failure messages are concrete and operator-usable

## Operating spec

- cadence: on demand
- owner: `upload-to-drive`
- auth owner: operator-configured `gog` auth state, optionally with a custom auth hook
- transport: local file path or URL in, Drive URL out
- done criteria: final Drive link returned and public permission verified

## Assumptions + gaps

- assumes `gog` is installed and already authorized for the target Drive account
- assumes `yt-dlp` is available either at `/opt/clawd-workspace/tools/yt-dlp-nightly/yt-dlp` or in PATH
- YouTube/Instagram may still hit anti-bot, rate-limit, or login walls; the skill must fail honestly when that happens
