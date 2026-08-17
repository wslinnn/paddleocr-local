<!--
  Modifications Copyright (c) 2026 wslinnn
  This file has been modified from the upstream project
  https://github.com/CHEN010325/paddleocr-local (Apache-2.0).
-->

# Third-party notices

Browser assets under `static/vendor/` are vendored copies of upstream npm
packages (marked, pdfjs-dist, pdf-lib, dompurify, jszip, katex).
Each vendor directory contains its upstream license. Upstream manages these via
`package.json` + `scripts/sync-vendor.mjs`; this fork vendors them directly —
when upgrading, copy the new dist files plus their license into `static/vendor/`
and bump the cache-busting version in `static/index.html`.

The project integrates with RapidOCR, PaddleOCR, PaddleX, Unlimited-OCR,
OvisOCR2, SGLang, vLLM, PyTorch, Hugging Face, ONNX Runtime, OpenVINO,
LibreOffice and their transitive dependencies. Their own licenses and model
terms continue to apply; this project's Apache-2.0 license does not relicense
them or downloaded model weights.
