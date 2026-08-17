<!-- Modifications Copyright (c) 2026 wslinnn
This file is part of the paddleocr-local fork, a derivative of
https://github.com/CHEN010325/paddleocr-local (Apache-2.0). -->

# Modifications

This repository is a fork of
[CHEN010325/paddleocr-local](https://github.com/CHEN010325/paddleocr-local)
(Apache License 2.0), forked at upstream commit `1e8fc9d`. It is repositioned
as a lightweight, CPU-only OCR webui (RapidOCR / PP-OCRv6) with a
server-side task queue and a visual proofreading layer. Per Apache-2.0
§4(b), files modified from the upstream project carry a notice at the top
of the file itself; this document is the aggregate view.

## Modified source / config files (in-file notice)

- `server.py`
- `static/app.js`, `static/i18n.js`, `static/index.html`, `static/style.css`
- `Dockerfile`, `docker-compose.yml`
- `.github/workflows/ci.yml`, `scripts/check-local.sh`
- `env.txt`, `requirements.txt`, `.gitignore`
- `tests/test_server.py`

## Files adapted from upstream commits made after the fork point

These did not exist at the fork commit; they were ported from upstream's
later commits and adapted for this fork (also carry in-file notices):

- `tests/test_frontend.mjs`, `tests/test_frontend_dom.mjs`
  (upstream `49a2f08`, adapted: fork-specific behavior, dropped
  HPD-Parsing and client-side batch-planning tests)
- `SECURITY.md`, `THIRD_PARTY_NOTICES.md`
  (upstream `3f8f865`, adapted for the CPU-only deployment)

## Modified files that cannot carry comments (covered here)

- `package.json` — JSON has no comments; trimmed from upstream's version
  (test toolchain only, jsdom dev dependency).
- `webui-openapi.json` — generated artifact; it is derived from
  `server.py`, which carries the notice.

## Modified documentation files (covered here, not in-file)

Markdown headers would pollute rendered documents; their modification
status is tracked here and in git history:

- `README.md`, `README.en.md`, `QUICKSTART.md`, `DOCKER_DEPLOY.md`, `api.md`

## Deleted upstream files

Deletion is not modification under §4(b); listed for transparency:

- `PROJECT_SUMMARY.md`, `README.zh-CN.md`,
  `static/vendor/highlight/*` (unused library, removed)

## Original files of this fork (not derived from upstream)

No §4(b) obligation applies; they are original contributions under the
same Apache-2.0 license: `rapidocr_adapter.py`, `Dockerfile.rapidocr`,
`docker-compose.rapidocr.yml`, `tests/test_rapidocr_adapter.py`,
`docs/architecture-audit.md`, `docs/roadmap.md`,
`docs/post-refactor-analysis.md`, and the LICENSE file (verbatim copy of
upstream's Apache-2.0 text).

## Maintenance rule

When you newly touch a file that originates from upstream and it does not
yet carry the notice, add the header once — it then remains satisfied; no
per-change updates are required (Apache-2.0 §4(b) requires stating that
files were changed, not dating each change).
