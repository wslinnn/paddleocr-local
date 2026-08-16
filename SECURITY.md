# Security Policy

## Supported version

Security fixes are applied to the latest commit on the default branch. Older
releases should be upgraded before a report is evaluated.

## Reporting a vulnerability

Please use GitHub's private security-advisory reporting flow for this
repository. Do not open a public issue containing an exploit, malicious sample,
token, host path, or other sensitive information.

Include the affected commit, deployment mode, reproduction steps, impact, and
any suggested mitigation. Maintainers should acknowledge a report within seven
days and coordinate disclosure after a fix is available.

## Deployment boundary

This fork's recommended deployment is the pure-CPU compose file
(`docker-compose.rapidocr.yml`): two containers, no Docker socket inside the
web container, and model orchestration disabled (`PANDOCR_MODEL_CONTROL=none`).
Keep published ports bound to loopback behind a TLS reverse proxy, and set
`PANDOCR_PASSWORD` (login gate) and `PANDOCR_API_TOKEN` before exposing the
instance. Base images are pinned by digest in the Dockerfiles — re-pin
deliberately when updating them.

The inherited multi-model GPU deployment (`docker-compose.yml`, upstream) keeps
Docker control inside the web container; treat that setup as trusted-network
only.
