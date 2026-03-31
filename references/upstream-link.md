# Upstream linkage

`upload-to-drive` is a workflow layer, not a fork of `gog`.

Source of truth split:
- upstream `gog` skill/CLI = generic Drive operations
- `upload-to-drive` = user-facing workflow for `source -> uploaded public Drive link`
- optional auth-maintenance hook = operator-owned preflight auth refresh/check, when available

Why this split exists:
- `gog` already knows how to upload a local file
- it does not own the full user workflow for attachments + URL downloads + public link verification
- auth rotation/checking is environment-specific and should stay optional, not hardwired into the public skill
- this skill keeps the workflow separate so upstream `gog` can keep updating independently
