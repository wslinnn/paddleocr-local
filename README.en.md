# PaddleOCR Local

**Language / 语言**: [简体中文](README.md) | English

An enhanced fork of [CHEN010325/paddleocr-local](https://github.com/CHEN010325/paddleocr-local): **CPU-first · background task queue · visual proofreading**. Runs PP-OCRv6 text recognition with nothing but Docker — no GPU required.

Five isolated models are supported:

- **PP-OCRv6-Rapid (pure CPU, added by this fork)** — RapidOCR onnx + OpenVINO, 20-45% faster than ONNX Runtime on Intel CPUs
- PaddleOCR-VL 1.6 / PP-OCRv6 (GPU)
- Unlimited-OCR / OvisOCR2

<img width="1920" height="945" alt="PaddleOCR Local WebUI" src="https://github.com/user-attachments/assets/85a247a0-c796-4a20-b596-1cc4148df964" />

## Fork Highlights

- [x] **Pure-CPU PP-OCRv6-Rapid model**: no GPU or PaddlePaddle needed; two-container one-command deployment via `docker-compose.rapidocr.yml`; tiny/small/medium tier switching from the UI (models pre-baked into the image); engine (OpenVINO/onnxruntime), threads, and batch size configurable
- [x] **Background task queue**: uploads return instantly with live progress and ETA, preemptive cancellation; parsing survives closing the tab; tasks queue automatically, resume after interruption and server restarts; pinned live section with per-task start/stop and pause-all
- [x] **PDF page-picker dialog**: pick pages to parse from a thumbnail grid, confirm per file, uploads run in parallel in the background
- [x] **Visual proofreading**: OCR text-box positioning, low-confidence lines marked in amber, result stats (pages/lines/chars/confidence), keyboard navigation with inline correction, click-to-locate in the source view
- [x] **Exports**: TXT, reflowed PDF (vector text), searchable PDF (original look + full-text search), DOCX (editable Word), Markdown/JSON, download format menu
- [x] **Security**: optional browser password gate, mandatory auth on Docker orchestration endpoints, full test suite in CI
- [x] **UX**: full-screen drag & drop and Ctrl/⌘+V screenshot pasting, task storage management, HiDPI-sharp rendering, Chinese/English UI

<details>
<summary>Complete change list vs upstream</summary>

See [docs/roadmap.md](docs/roadmap.md) (all 24 items with status, including the full P1 delivery).

</details>

## Quick Start

### Linux, pure CPU (recommended — no GPU)

```bash
docker compose -f docker-compose.rapidocr.yml up -d --build
```

Open http://localhost:8000 and pick **PP-OCRv6 (RapidOCR·CPU)**. OpenVINO is the default engine (Intel-optimized); on AMD CPUs set `RAPIDOCR_ENGINE_TYPE=onnxruntime` in `.env`. For public deployments set `PANDOCR_PASSWORD` (browser login gate) or `PANDOCR_API_TOKEN` (API token).

Health-check ports and advanced settings: see the [deployment guide](DOCKER_DEPLOY.md).

### GPU / other platforms

```powershell
# Windows + NVIDIA Docker Desktop
.\windows-one-click.bat
```

```bash
# macOS Apple Silicon (OvisOCR2 uses MLX by default)
./macos-one-click.command
```

The installer asks which model to deploy first and downloads only that model. For multi-model manual deployment see the [deployment guide](DOCKER_DEPLOY.md).

## Features

- Image, PDF, PPT/PPTX, and DOC/DOCX parsing; five models with on-demand deployment (including a pure-CPU option)
- Background task queue: progress with ETA, cancellation, tab-safe parsing, batch queuing, resumable work, restart recovery, pause-all
- Side-by-side source and result views with synchronized zoom
- OCR text-box positioning, low-confidence marking, keyboard navigation, click-to-correct, tier switching (tiny/small/medium)
- Markdown, table, formula, and visual-region rendering
- Export to Markdown, TXT, JSON, reflowed PDF, searchable PDF, and DOCX
- Task history with storage usage stats and cleanup
- Chinese and English UI

## Roadmap

- [x] Frontend model-tier switching (tiny/small/medium, runtime hot reload, pre-baked models)
- [ ] Refresh README screenshots (current ones show the old UI)
- [x] Searchable PDF export (image layer + invisible text layer)
- [x] DOCX export (one paragraph per line, editable)
- [ ] Proofreading: undo / batch replace
- [ ] Localized backend error messages
- [ ] API calling examples (curl / Python)

Explicit non-goals: table-structure recognition and field extraction (require layout models; outside the pure-CPU lightweight scope).

Full plan and status: [docs/roadmap.md](docs/roadmap.md).

## Documentation

- [Quick Start](QUICKSTART.md)
- [Manual Docker deployment](DOCKER_DEPLOY.md)
- [OvisOCR2 deployment and configuration](OVISOCR2_DEPLOY.md)
- [API reference](api.md)

## Acknowledgments

Built on top of [CHEN010325/paddleocr-local](https://github.com/CHEN010325/paddleocr-local) — many thanks to the original author. The upstream code remains the property of its author; this fork builds on it and keeps enhancing it.
