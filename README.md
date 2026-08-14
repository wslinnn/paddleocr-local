# PaddleOCR Local

**语言 / Language**：简体中文 | [English](README.en.md)

一个可本地部署的多模型文档解析 WebUI，支持上传图片、PDF、PPT、Word，查看解析结果并导出 Markdown。

支持五个独立模型：

- PaddleOCR-VL 1.6
- PP-OCRv6
- **PP-OCRv6-Rapid（纯 CPU，无需 GPU）** — 基于 RapidOCR onnx + OpenVINO，Intel CPU 上较 ONNX Runtime 提速 20-45%
- Unlimited-OCR
- OvisOCR2

<img width="1920" height="945" alt="PaddleOCR Local WebUI" src="https://github.com/user-attachments/assets/85a247a0-c796-4a20-b596-1cc4148df964" />

## 一键部署

安装脚本会让你选择首次部署的模型，并且只下载和启动所选模型。其他模型不会下载权重，也不会占用内存或显存。

### Windows + NVIDIA

需要提前安装 NVIDIA 驱动和支持 GPU 的 Docker Desktop。

```powershell
.\windows-one-click.bat
```

直接部署 OvisOCR2：

```powershell
.\windows-one-click.bat -Model ovisocr2
```

只检查配置，不下载或启动：

```powershell
.\windows-one-click.bat -Model ovisocr2 -DryRun
```

### macOS Apple Silicon

支持 Apple M1、M2、M3、M4，OvisOCR2 默认使用 MLX。

```bash
./macos-one-click.command
```

直接部署 OvisOCR2：

```bash
./macos-one-click.command --model ovisocr2
```

只检查配置，不安装或启动：

```bash
./macos-one-click.command --model ovisocr2 --dry-run
```

Linux、手动 Docker 部署和高级参数请查看 [部署文档](DOCKER_DEPLOY.md)。

### Linux 纯 CPU 精简部署（推荐无 GPU 用户）

只需两个容器（Web 服务 + RapidOCR），无需 NVIDIA 驱动，镜像小、启动快：

```bash
docker compose -f docker-compose.rapidocr.yml up -d --build
```

打开 http://localhost:8000 ，模型选择 **PP-OCRv6 (RapidOCR·CPU)** 即可。默认使用 OpenVINO 推理引擎（Intel CPU 优化）；AMD CPU 请在 `.env` 设置 `RAPIDOCR_ENGINE_TYPE=onnxruntime`。

公网部署建议在 `.env` 设置 `PANDOCR_PASSWORD`（浏览器登录门禁）或 `PANDOCR_API_TOKEN`（API 访问令牌）。

## 开始使用

部署完成后打开：

- WebUI：http://localhost:8000
- PaddleOCR-VL：http://localhost:8081/health
- PP-OCRv6：http://localhost:8082/health
- Unlimited-OCR：http://localhost:8083/health
- OvisOCR2：http://localhost:8084/health
- PP-OCRv6-Rapid：http://localhost:8085/health

健康检查地址只会在对应模型运行时可用。单 GPU 环境默认只加载当前选择的模型，切换模型时会自动停止其他模型，避免同时占用显存。

## 主要功能

- 图片、PDF、PPT/PPTX、DOC/DOCX 解析
- 五模型自由选择和按需部署（含纯 CPU 方案）
- PDF 逐页解析、进度显示和历史任务保存
- Markdown、表格、公式和图片区域展示
- 原文件与解析结果左右对照、OCR 文字框定位与纠错
- Markdown、TXT、JSON、重排 PDF 下载
- 中文、英文界面切换

## 更多文档

- [快速开始](QUICKSTART.md)
- [OvisOCR2 部署与参数](OVISOCR2_DEPLOY.md)
- [Docker 手动部署](DOCKER_DEPLOY.md)
- [API 说明](api.md)

项目地址：[https://github.com/CHEN010325/paddleocr-local](https://github.com/CHEN010325/paddleocr-local)
