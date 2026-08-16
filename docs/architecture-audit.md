# 前后端架构审计报告

> 2026-08 · 基于当前 main 分支（~12,400 行：server.py 3542 / app.js 6013 / adapter 426 / CSS 1973）
> 性质：分析记录，非修改任务。

---

## 〇、结论摘要

当前架构是一个**功能完备的原型在持续交付压力下演化的结果**——每个具体功能都是对的，但整体结构存在一个**系统性根因**和多个**由此衍生的数据问题**。不需要推倒重来，但应在下一个大版本中做一次「schema 归位」重构。

---

## 一、致命级：前端拥有领域模型

### 现状

```
前端分配任务 ID（UUID）
前端设计批次计划（哪些页、每批几页、startPage/endPage/label/id）
前端构造完整 task JSON（含 schema、解析设置、时间戳）
前端 PUT 全量对象到后端
后端原样存储（仅剥 ephemeral 字段）
后端 worker 读取前端定义的 batch 结构去执行
```

### 为什么不是最佳实践

**数据 schema 的所有权在错误的层**。持久层（后端）应该是 schema 的定义者和守护者；前端是消费者和意图发送者。当前反过来。

具体后果：

1. **schema 隐式定义在两侧**——task.json 的字段由「前端发了什么」决定，后端处理中又往里加字段（ocrResults、batch.status），没有共享的类型契约
2. **已因此产生的 bug**：
   - ephemeral 字段泄漏（jobState 持久化到磁盘）——后端不知道哪些是前端 UI 态
   - markdown 快照与 ocrLines 脱节——两侧各自维护派生数据
   - parser 白名单漏 rapid——前端知道新模型，后端映射没同步
3. **未来风险**：前端重构 schema → 旧 task.json 不兼容；后端加校验 → 前端发送的对象不通过

### 最佳实践形态

```
前端: POST /api/tasks  { file, model, pageRange, settings }
后端: 生成 ID、切批、创建 schema → 201 { taskId }
前端: GET /api/tasks/{id}   （消费后端拥有的 schema）
```

---

## 二、严重级：数据链路的三重冗余

### 2.1 文本的三份表示（同一 OCR 结果存三处）

| 存储 | 用途 | 维护者 |
|---|---|---|
| `ocrLines[].text` | 可视化层文字框 | 前端 `updateStoredPPOCRLineText` |
| `prunedResult.rec_texts[]` | 导出/批量替换 | 同上 |
| `markdown.text` / `task.markdown` | Markdown 导出 | 前端重建快照 + 后端 batch 追加 |

三处必须手动同步。**已造成的 bug**：纠正文字后 markdown 快照不同步（修复方式是「纠正时重建」——治标不治本，快照本身仍存在）。

**正确做法**：只存一份源数据（`ocrLines[].text`），markdown 在导出时按需生成。

### 2.2 页面图片的 base64 内嵌

每个页面的完整 JPEG（~300-800KB）以 base64 内嵌在 `result.json` 的 `ocrResults[].pageImage` 字段。20 页 PDF → result.json ≈ 6-16MB 纯文本。

问题：
- **与 source.bin 重复**（源文件已在磁盘，页面图是它的渲染结果）
- **JSON 读写开销**：每次拉详情都要 parse MB 级 base64 字符串
- **不利于增量更新**：改一行文字 → 重写整个 result.json（含所有图片）

**正确做法**：页面图片存独立文件（`pages/001.jpg`），result.json 只存引用路径。

> **交叉验证修正**：原文写「从 source.bin 按需渲染（后端已有 `/source/pages` 端点）」——经核实，`/source/pages` 返回的是 PDF 分页切片（`extract_pdf_pages`），不是页面 JPEG 图片。可视化层需要的是与 OCR 坐标空间对齐的 JPEG（当前由 adapter 在推理时生成）。按需渲染需要后端新增一个页面 JPEG 渲染端点（fitz 可做但属于新功能），不是复用现有能力。**首选方案：adapter 推理时把 pageImage 写入独立文件而非内嵌 JSON**——改动最小，两端只改引用方式。

### 2.3 批次计划的双端镜像

前端 `ensureBatchPayload` 和后端 `build_job_batch_payload` 是**同一段业务逻辑的两份实现**。改一边不改另一边 = 静默不一致。

### 2.4 sourceDataUrl 的内存驻留（新发现）

图片任务在 `createImageTask` 时把**整个文件的 base64** 存入 `task.sourceDataUrl` + `batch.payloadDataUrl`。虽然持久化时 `taskForPersistence` 会删掉 `sourceDataUrl`（当 `sourceUrl` 存在时），但在内存中这个 MB 级字符串会活到页面刷新。多图片批量上传时，N 个任务 × MB 级 sourceDataUrl 同时驻留。

---

## 三、中等严重：前端状态管理

### 3.1 25+ 个模块级可变全局变量

```js
let tasks = [];             // 全部任务的内存镜像
let activeTaskId = null;
let currentPdf = null;       // pdf.js 实例
let sourceRenderToken = 0;   // 渲染竞态 token
let isProcessing = false;    // 遗留锁(队列时代语义已过时)
let renderedMarkdownKey = '';
let renderedPPOCRVisualContext = '';
let cachedJsonLines = [];
// ... 还有 15+ 个
```

没有变更通知机制、没有访问约束。**已造成**：`isProcessing` 在队列时代语义漂移（前端锁不再代表「正在解析」）。

### 3.2 replaceTask 的浅合并陷阱

```js
tasks[index] = { ...tasks[index], ...task, detailLoaded: true };
```

新对象覆盖同名键，旧对象独有的键保留——**前端无法清空一个字段**（后端删了某字段，合并后仍留旧值）。这是内存态与磁盘态漂移的温床。

### 3.3 无任务级隔离

`lastCorrection`（撤销状态）是全局单例——切任务即废。说明缺乏任务级的状态容器。

### 3.4 渲染缓存 key 的脆弱性

`renderedMarkdownKey`、`renderedPPOCRVisualContext`、`renderedJsonKey` 等手动拼接的字符串 key 决定是否重渲染——key 构造稍有遗漏就会缓存失效或该刷新时没刷新。这类 key 应由统一函数派生而非散落各处的字符串拼接。

---

## 四、中等：API 设计

| 问题 | 现状 | 最佳实践 |
|---|---|---|
| ID 生成 | 前端生成 UUID | 后端生成（`POST /api/tasks` 返回 `{id}`） |
| 任务创建 | 隐式（首次 PUT 时创建） | 显式 `POST /api/tasks` + 201 |
| 更新方式 | 全量 PUT（前端发送完整 task） | PATCH 或命令式端点（`POST .../correct`） |
| 批次计划 | 前端设计并存入 task | 后端根据 pageRange 自动切批 |
| 状态获取 | 1.5s 轮询 GET /status | SSE 可选（单用户轮询可接受） |
| 模型代理 | 5 对 `run_*_request` + `proxy_*` 函数近乎逐行重复 | 配置表驱动 + 单个通用代理函数 |

---

## 五、中等：后端单文件

`server.py` 3542 行，包含：

- 配置解析（~120 行）
- Docker Engine 客户端 + 容器编排（~300 行）
- 模型运行时管理 + 健康检查（~500 行）
- 任务 CRUD + JSON 持久化（~250 行）
- 后台任务队列 + worker（~200 行）
- 5 个模型的请求构建/响应解析（~500 行）
- 3 个 PDF 生成器 + DOCX（~200 行）
- 认证 + 中间件 + 错误码（~150 行）
- Office 转换（~60 行）
- 存储管理（~50 行）
- 20+ API 端点（~300 行）

**至少应为 5-6 个模块**：`config.py` / `docker_control.py` / `task_store.py` / `job_queue.py` / `exporters/` / `model_proxies/`。不紧急，但每次改动都在 3500 行里搜索定位的时间成本在增长。

---

## 六、低严重但值得记录

### 6.1 unlimited-ocr 的双路径

unlimited-ocr 不走后台队列（保留浏览器流式 SSE），导致 `processTask` 存在两条分支（队列路径 vs 本地循环），前端必须维护两套状态逻辑。长期应统一或明确隔离。

### 6.2 OpenAPI snapshot 的手工维护

`webui-openapi.json` 需要在每次改 API 后手动重新生成。CI 检查它是否 stale，但生成步骤不在 pre-commit hook 里——容易忘记。建议加 pre-commit 或 CI 自动修复。

### 6.3 前端零测试

65+ 个 Python 测试 vs 0 个前端测试。`classList.add()` 空格 SyntaxError 这类 bug 只能靠真机发现。最低成本的改进：Playwright smoke test（打开页面上传文件到看到结果，一个 E2E 就能抓住大部分 JS 语法/运行时错误）。

### 6.4 adapter 的 ENGINE_CACHE 无失效机制

三档引擎缓存（LRU 3）不会主动释放——如果用户在三个档位间来回切换，三个引擎常驻内存。onnx 模型文件总计 ~50-150MB，但 OpenVINO 编译后运行时图可能膨胀，实际内存占用**未实测**（原文 ~300MB 为估算，无 benchmark 支撑）。在内存受限环境建议实测后再决定是否加 TTL 失效。

### 6.5 batch.markdown 的持久化冗余

每个 batch 对象上存 `markdown` 字段（该批次的文本），但 `task.markdown` 已包含全部批次文本，`batchMarkdown` 又在 result.json 里存了一份映射——同一文本最多三处。

---

## 七、做对了的部分（避免误伤）

| 方面 | 评价 |
|---|---|
| FIFO 队列 + 每批落盘 + 重启恢复 | ✅ 持久性设计正确 |
| adapter 模式（推理隔离，独立服务） | ✅ 架构正确 |
| 终态磁盘权威（三层对账防幻影状态） | ✅ 防御到位 |
| 错误码本地化（stable code + frontend i18n） | ✅ 层次分离干净 |
| 导出管线（3 PDF + DOCX，glyf 子集化） | ✅ 功能完备 |
| 队列管理面（置顶分区 + 抢占取消 + 暂停） | ✅ UX 正确 |
| Windows 文件锁重试（读+写双侧） | ✅ 环境适配 |

---

## 八、重构优先级（如要执行）

| 优先级 | 改什么 | 为什么 | 预估工作量 |
|---|---|---|---|
| **1** | 后端接管任务创建与 schema | 消除根因——后续所有数据问题的源头 | 2-3 天 |
| **2** | 文本单一来源（删 markdown 快照） | 消除最易出错的三重冗余 | 1 天 |
| **3** | 页面图片出 JSON（adapter 写独立文件 + 引用路径） | result.json 从 MB 降到 KB | 1 天 |
| 4 | server.py 拆模块（先梳理跨函数全局状态归属） | 可维护性；拆分本身不难，但 3542 行里有隐式全局态（model_runtime_task、ocr_active_count、TASK_QUEUE 等跨函数共享）需要先理清 | 1-2 天 |
| 5 | 前端状态容器化 | 减少 25 个全局变量的认知负担；约 1/3 有跨模块依赖（sourceRenderToken 等被渲染和导航两侧读写），需重设计通知机制 | 2-3 天 |
| 6 | 模型代理配置表化 | 消除 5 对重复函数 | 0.5 天 |

**1-3 是一次 breaking-change 重构的三个步骤，应在同一个版本内完成。4-6 是独立的代码卫生工作。**

---

## 九、交叉验证记录

对报告声明逐条对照代码事实（10 项），结果：

| # | 声明 | 结论 |
|---|---|---|
| 1 | 前端拥有领域模型 | ✅ 成立 |
| 2 | 文本三重冗余 | ✅ 成立 |
| 3 | 批次计划双端镜像 | ✅ 成立（docstring 自述 "mirroring the browser's payload logic"） |
| 4 | sourceDataUrl 内存驻留 | ✅ 成立 |
| 5 | replaceTask 浅合并陷阱 | ✅ 成立 |
| 6 | 5 对代理函数逐行重复 | ✅ 成立（diff 骨架 100% 相同，差异仅常量名） |
| 7 | 前端零测试 | ✅ 成立 |
| 8 | unlimited-ocr 双路径 | ✅ 成立 |
| 9 | 页面图片可从 source.bin 按需渲染 | ⚠️ 修正——`/source/pages` 返回 PDF 分页切片，非页面 JPEG；需新增渲染端点或改用 adapter 写文件方案 |
| 10 | 引擎缓存 ~300MB 可接受 | ⚠️ 修正——数字未实测，onnx 文件 ~50-150MB 但 OpenVINO 运行时图大小未 benchmark |

**结论：10 项声明中 8 项完全成立，2 项经修正后成立（方案可行性或数字精度问题，非方向错误）。**
