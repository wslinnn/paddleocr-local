# PaddleOCR Local

**语言 / Language**：简体中文 | [English](README.en.md)

基于 [CHEN010325/paddleocr-local](https://github.com/CHEN010325/paddleocr-local) 的增强 fork：**纯 CPU 优先 · 后台任务队列 · 可视化校对**。只需 Docker，无需 GPU 即可跑通 PP-OCRv6 文字识别。

支持五个独立模型：

- **PP-OCRv6-Rapid（纯 CPU，本 fork 新增）** — RapidOCR onnx + OpenVINO，Intel CPU 上较 ONNX Runtime 提速 20-45%
- PaddleOCR-VL 1.6 / PP-OCRv6（GPU）
- Unlimited-OCR / OvisOCR2

<img width="1920" height="945" alt="PaddleOCR Local WebUI" src="https://github.com/user-attachments/assets/85a247a0-c796-4a20-b596-1cc4148df964" />

## Fork 亮点

- [x] **纯 CPU 模型 PP-OCRv6-Rapid**：无需 GPU 与 PaddlePaddle，`docker-compose.rapidocr.yml` 两容器一键部署；引擎（OpenVINO/onnxruntime）、线程、批大小、语种均可配置，启动预热消除首次推理开销
- [x] **后台任务队列**：上传即时返回，进度与预计耗时实时显示，可取消；关闭页面解析不中断，多任务自动排队，断点可续传
- [x] **PDF 选页弹窗**：缩略图网格勾选要解析的页面、逐文件确认，多文件后台并行上传
- [x] **可视化校对**：OCR 文字框定位、低置信度行琥珀标示、结果统计（页/行/字符/置信度）、键盘导航与点击纠错、左右联动定位
- [x] **导出**：TXT、重排 PDF（内嵌中文字体，可选中可搜索）、Markdown/JSON，下载格式菜单
- [x] **安全**：可选浏览器密码门禁、Docker 编排端点强制鉴权、CI 全量测试
- [x] **体验**：全屏拖拽与 Ctrl/⌘+V 粘贴截图直接解析、任务存储管理、HiDPI 清晰渲染、中英双语

<details>
<summary>相对上游的完整变更清单</summary>

见 [docs/roadmap.md](docs/roadmap.md)（24 项全状态表，含 P1 全部交付明细）。

</details>

## 快速开始

### Linux 纯 CPU（推荐，无需 GPU）

```bash
docker compose -f docker-compose.rapidocr.yml up -d --build
```

打开 http://localhost:8000 ，选择 **PP-OCRv6 (RapidOCR·CPU)** 即可。默认 OpenVINO 引擎（Intel CPU 优化）；AMD CPU 在 `.env` 设置 `RAPIDOCR_ENGINE_TYPE=onnxruntime`。公网部署建议设置 `PANDOCR_PASSWORD`（浏览器登录门禁）或 `PANDOCR_API_TOKEN`（API 令牌）。

各模型健康检查端口与高级参数见 [部署文档](DOCKER_DEPLOY.md)。

### GPU / 其他平台

```powershell
# Windows + NVIDIA Docker Desktop
.\windows-one-click.bat
```

```bash
# macOS Apple Silicon（OvisOCR2 默认 MLX）
./macos-one-click.command
```

安装脚本会让你选择首次部署的模型，只下载并启动所选模型；多模型手动部署见 [部署文档](DOCKER_DEPLOY.md)。

## 功能总览

- 图片、PDF、PPT/PPTX、DOC/DOCX 解析；五模型按需部署（含纯 CPU 方案）
- 后台任务队列：进度/预计耗时、取消、关页面不中断、批量排队、断点续传
- 原文件与解析结果左右对照、同步缩放
- OCR 文字框定位、低置信度标示、键盘导航、点击纠错
- Markdown、表格、公式和图片区域渲染
- 导出 Markdown、TXT、JSON、重排 PDF
- 任务历史、存储占用统计与清理
- 中文、英文界面切换

## Roadmap

- [ ] 模型档位（tiny/small/medium）与语种的前端切换（运行时热重载）
- [ ] 隐私控制：源文件即用即删、保留期可配
- [ ] API 调用示例（curl / Python）
- [ ] 导出扩展：DOCX、可搜索 PDF（原图层 + 文字层）
- [ ] 多用户数据隔离
- [ ] 体验打磨：骨架屏 / Toast / 空状态引导
- [ ] 移动端整体验证

明确不做：表格结构识别、字段提取（需布局大模型，超出纯 CPU 轻量定位）。

完整计划与状态见 [docs/roadmap.md](docs/roadmap.md)。

## 文档

- [快速开始](QUICKSTART.md)
- [Docker 手动部署](DOCKER_DEPLOY.md)
- [OvisOCR2 部署与参数](OVISOCR2_DEPLOY.md)
- [API 说明](api.md)

## 致谢

本仓库基于 [CHEN010325/paddleocr-local](https://github.com/CHEN010325/paddleocr-local) 构建，感谢原作者的工作。上游原始代码归其作者所有；本仓库在其基础上持续增强。
