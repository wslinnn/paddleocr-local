<!--
  Modifications Copyright (c) 2026 wslinnn
  This file has been modified from the upstream project
  https://github.com/CHEN010325/paddleocr-local (Apache-2.0).
-->

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
(`docker-compose.rapidocr.yml`): two containers, `PANDOCR_MODEL_CATALOG`
restricted to `pp-ocrv6-rapid`, and no Docker socket — model status uses HTTP
health probes (`PANDOCR_MODEL_CONTROL=none`), which is all a single-model CPU
setup needs. Keep published ports bound to loopback behind a TLS reverse
proxy, and set `PANDOCR_PASSWORD` (login gate) and `PANDOCR_API_TOKEN` before
exposing the instance. Base images are pinned by digest in the Dockerfiles —
re-pin deliberately when updating them.

The inherited multi-model GPU deployment (`docker-compose.yml`, upstream) does
mount the Docker socket into the web container for model orchestration; treat
that setup as trusted-network only.
