# ImageGen

**Standalone local image generation application with CLI, WebUI and Codex integration.**

ImageGen 是一个本地图像生成应用：提供 Python Core / Services、命令行（`imagegen`）、
Local HTTP API v1 与完整 WebUI。最终架构只保留两套独立的 OpenAI-Compatible API：
提示词 API（`/v1/chat/completions`，明确不支持时 fallback `/v1/responses`）与
图像 API（`/v1/images/generations` / `/v1/images/edits`），两者共用统一 OpenAIClient。

Codex 集成是可选的 Adapter：DeepSeek 等纯文本模型通过薄 CLI 调用 ImageGen Core 出图。

## 功能

- **提示词 OpenAI API**：独立 `base_url` / `api_key` / `model`，默认 `POST /v1/chat/completions`，仅在上游明确不支持（404 / 405 / 501）时 fallback `/v1/responses`；401 / 403 / 429 / 500 / 超时 / 网络错误直接返回原始错误
- **图像 OpenAI API**：独立 `base_url` / `api_key` / `model`；文生图 `POST /v1/images/generations`、图生图/参考图 `POST /v1/images/edits`（支持多参考图，兼容 `b64_json` / `image` / `url` / `data:image/...` 返回）
- **尺寸原样透传**：任意合法 `WxH`（如 1920x1080 / 1080x1920 / 3440x1440）原样发送上游，不存在白名单 / 自动归一化 / Canvas fallback / 尺寸失败自动重试；输出尺寸不符只产生 warning
- **构图预设 + 输出尺寸检查**：`--composition full-body / half-body / portrait / landscape` 锁定画幅与取景规则；生成后读取真实输出尺寸，`size_check` 可开启/关闭（不符时警告）
- **WebUI 推荐尺寸**：画幅 × 1K/2K/4K 预设（1:1 / 16:9 / 9:16 / 4:3 / 3:4 / 3:2 / 2:3），最终只发送 `WxH`，仍可自由手填自定义尺寸
- **参考图**：三段式提示词自动生成（类型识别 + 身份锚点清单 + 场景锚点丢弃）；支持最多 4 张多参考图，每张带用途标签，生成角色隔离简报；Reference Asset System 提供持久化素材库（上传 / 拖放 / 粘贴 / 本机导入 → managed asset → Asset API）
- **提示词词库**：MySQL + SiliconFlow Embedding / Rerank 向量检索（`prompt_library` 库），生成时喂示例给翻译官
- **Standalone WebUI（洛天依主题）**：`imagegen serve --open` 启动（默认 http://127.0.0.1:8765），
  与 HTTP API 同源运行；生成页（提示词 / 参考图 / 画幅×档位尺寸 / 构图 / 模型 / 批量出图）、
设置页（提示词与图像两组 OpenAI API，含「拉取模型」连接测试）、诊断页、持久化历史画廊（最近 50 条）；
前端只通过 `/api/v1/*` 与 Core 通信
- **自动副本**：生成成功后自动在 `mirror_dir` 保留副本
- **诊断**：`doctor`（连通性 + 尺寸探针）、`config`（密钥打码）、`list-models`

## 仓库结构

```
.
├── .agents/plugins/marketplace.json      # Codex marketplace 清单
├── pyproject.toml                        # 独立包（pip install -e . / imagegen 命令）
├── src/imagegen/                         # ImageGen Core（独立于 Codex）
│   ├── __init__.py                       # Public Core API（CORE_API_VERSION=1）
│   ├── engine.py / models.py / errors.py # 编排、统一数据模型、通用错误
│   ├── config.py / http.py / image_utils.py
│   ├── composition.py / reference.py / translator.py
│   ├── library.py / doctor.py / cli.py / __main__.py
│   ├── openai_client.py                 # 统一 OpenAI-Compatible Client（endpoint / 文本 / 图像 / 模型）
│   ├── api/                               # Local HTTP API v1（纯协议适配器）
│   │   ├── server.py / routes.py / responses.py / outputs.py
│   ├── web/                               # Standalone WebUI 静态资源
│   │   └── static/index.html / app.js / style.css
│   ├── services/                         # Application Service 层
│   │   ├── db.py                         # SQLite schema v2 迁移（user_version=2）
│   │   ├── assets.py                     # AssetService（Asset 记录 + managed 文件）
│   │   ├── references.py                 # ReferenceResolver（asset_id → 本地路径）
│   │   ├── generation.py                 # GenerationService
│   │   ├── models.py                     # ModelService
│   │   ├── config.py                     # ConfigService
│   │   └── diagnostics.py                # DiagnosticService
├── plugins/deepseek-imagegen/            # Codex 插件（仅 Adapter）
│   ├── .codex-plugin/plugin.json         # 插件清单
│   ├── skills/deepseek-imagegen/         # 技能（触发图像生成桥接）
│   ├── assets/icon.png                   # 插件图标
│   └── scripts/
│       ├── image_gen.py                  # 薄入口：加载 Core 并调用 CLI
│       ├── prompt_lib.py                 # 词库薄入口
│       ├── codex_adapter.py              # Codex 环境默认值注入（Core 不依赖）
│       ├── webui.py                      # 兼容 launcher：启动 standalone WebUI
│       └── config.example.json           # 配置示例（真实 Key 放本地）
└── tests/                                # 统一测试（python -m unittest）
    ├── run_smoke_test.py                 # 统一测试入口
    └── test_*.py                         # Core / Backend / 回归测试
```

## 独立安装（不依赖 Codex 插件）

```bash
pip install -e .
```

启动 Standalone WebUI（同时提供 HTTP API）：

```bash
imagegen serve --open
# 浏览器打开 http://127.0.0.1:8765/
```

命令行方式：

```bash
imagegen generate "..." --composition full-body
imagegen config
imagegen doctor
imagegen list-models
# 或
python -m imagegen ...
```

## Codex Integration

Codex 插件目录 `plugins/deepseek-imagegen/` 只是 Adapter：薄 CLI 入口加载独立 Core，
`webui.py` 是兼容 launcher（启动 standalone ImageGen WebUI），不再包含第二套 WebUI 实现。

```bash
codex plugin marketplace add "D:\deepseek-imagegen-plugin"
python plugins/deepseek-imagegen/scripts/image_gen.py generate "..." --json
python plugins/deepseek-imagegen/scripts/webui.py          # 旧入口，等价 imagegen serve
```

CLI、WebUI 与 Codex Adapter 统一通过 `src/imagegen` 的 Public API（`imagegen` 根模块）
与 Service 层（`imagegen.services`）消费 Core；`import imagegen` 提供
`CORE_API_VERSION`、`ImageGenEngine`、`GenerateRequest`、`GenerateResult`
与错误类型等稳定公共接口。

## Local HTTP API v1

在 Services 之上提供本地 HTTP 适配器（纯协议层，不包含生图业务规则）：

```bash
imagegen serve
# 默认监听 http://127.0.0.1:8765；或 python -m imagegen serve
```

接口全部位于 `/api/v1/`：

```text
GET   /api/v1/health
POST  /api/v1/generate
POST  /api/v1/models                      # 模型拉取 {target: "translator" | "image"}
GET   /api/v1/config
PATCH /api/v1/config
POST  /api/v1/doctor
GET   /api/v1/outputs/{generation_id}
GET   /api/v1/history
GET   /api/v1/history/{generation_id}
DELETE /api/v1/history/{generation_id}
POST  /api/v1/assets                       # multipart 文件上传（file + kind=reference）
POST  /api/v1/assets/import                # 服务器本机路径导入 {path, kind}
GET   /api/v1/assets                       # ?kind=&q=&limit=&offset=
GET   /api/v1/assets/{asset_id}
GET   /api/v1/assets/{asset_id}/content    # 预览 / 缩略图
DELETE /api/v1/assets/{asset_id}           # 被历史引用时返回 409 asset_in_use
```

示例：

```bash
curl http://127.0.0.1:8765/api/v1/health

curl -X POST http://127.0.0.1:8765/api/v1/generate \
  -H "Content-Type: application/json" \
  -d '{"prompt":"a cat","size":"1024x1024"}'
```

PowerShell 示例：

```powershell
Invoke-RestMethod http://127.0.0.1:8765/api/v1/health
Invoke-RestMethod -Method Post http://127.0.0.1:8765/api/v1/generate `
  -ContentType "application/json" `
  -Body '{"prompt":"a cat","size":"1024x1024"}'
```

`POST /api/v1/generate` 的请求体直接使用 `GenerateRequest` JSON contract，响应基于
`GenerateResult.to_dict()` 并附加 `output_url`；生成结果通过 `generation_id`
经 `/api/v1/outputs/{generation_id}` 读取（进程内注册表，不提供任意文件读取）。
`GET /api/v1/config` 只返回打码后的配置，`PATCH` 复用 `ConfigService.update`。

### 模型拉取（非强依赖）

`POST /api/v1/models` 按 target 分别请求两组上游的 `/v1/models`，各自使用自己的 API Key：

```json
{"target": "image"}
```

```json
{"target": "translator"}
```

返回 `{"models": ["model-a", "model-b"]}`。`/v1/models` 不可用时不影响实际调用——
`translator.model` / `image.model` 始终允许手工填写。WebUI 的「拉取模型」同时承担连接测试。

### Reference Asset System

参考图走持久化 Asset（浏览器上传 / 拖放 / Ctrl+V 粘贴 / 服务器本机路径导入都会先
转换为 managed asset），默认位置：

```text
~/.deepseek-imagegen/
├ imagegen.db        # SQLite schema v2（generations / assets / generation_assets）
└ assets/references/ # <asset_id>.png/.jpg/...（不登记原始路径）
```

`POST /api/v1/generate` 新增 `references` 协议（与旧 `images` 路径兼容并存）：

```json
{
  "prompt": "…",
  "references": [
    {"asset_id": "…", "role": "character"},
    {"asset_id": "…", "role": "style"}
  ]
}
```

HTTP 层通过 `ReferenceResolver` 把 `asset_id` 解析成 managed 本地路径，再转换成现有
Core 契约 `images` + `reference_roles`；`GenerateRequest` / Engine 不知道 asset_id。
生成成功后 best-effort 记录 `generation_assets`（generation → asset、role、position），
写入失败只追加 warning，不影响生成成功。`GET /api/v1/history/{generation_id}` 返回
`references`（asset_id / role / position / content_url），列表接口不塞完整引用数据；
HTTP 任何位置都不暴露 managed `file_path`。

### Persistent History

每次成功生成（`GenerateResult` 构造完成）都会 best-effort 写入本地历史数据库：

```text
默认 ~/.deepseek-imagegen/imagegen.db（可自定义 Path 注入）
```

HTTP 层通过 `/api/v1/history` 读取/删除；历史记录不暴露 `output_path`，而是返回
`output_url`。`/api/v1/outputs/{generation_id}` 优先读取进程内注册表，未命中时回退到
历史记录里的输出文件，因此 Server 重启后旧图片仍可访问。历史写入失败只追加 warning，
不影响生成成功；持久化历史自 Phase 5A 后开始记录（不导入旧 history.json）。

### 安全说明

- 默认仅监听 `127.0.0.1`；绑定非 loopback 地址必须显式 `--allow-remote`。
- `--allow-remote` 无身份验证，只应在可信网络环境使用；当前 API 不适合公开互联网部署。
- 默认不发送全局 CORS 头，不提供认证系统（后续按需设计）。
- v1 Local API 的 `images` 仍指向运行 ImageGen Server 的本机可访问路径
  （same-machine API，CLI / Codex 兼容）；参考图推荐使用 Asset API / `references`。
- 所有错误统一为 `{"error": {"type": "...", "message": "..."}}`。

## 安装

```bash
codex plugin marketplace add "D:\deepseek-imagegen-plugin"
```

然后在 Codex 应用中安装 `DeepSeek ImageGen` 插件，并参考 [插件 README](plugins/deepseek-imagegen/README.md) 使用与配置。

## 配置（双 OpenAI-Compatible API）

配置位置：`~/.deepseek-imagegen/config.json`，模板见 `scripts/config.example.json`。

- `translator`：提示词上游 `enabled` / `base_url` / `api_key` / `model` / `output_lang`
- `image`：图像上游 `base_url` / `api_key` / `model` / `quality`（`quality` 留空则向上游省略该字段）
- `size_check`：`enabled`（默认 true）+ `tolerance`（默认 0.06）；输出尺寸不符只加 warning
- `prompt_library`：MySQL 连接、Embedding / Rerank（SiliconFlow）、分类置顶
- `reference`：参考图自动分类与视觉识别脚本

旧配置迁移（简单、确定）：`translator.deepseek.*` 值明确时自动升级为 `translator.*`；
`translator.engine=off` 迁移为 `enabled=false`；`vertex` 与 `extra_backends.*` 不猜测，
`image.*` 保持为空，由用户重新配置；`size_policy.tolerance` 迁移为 `size_check.tolerance`。

## 测试

```bash
python tests/run_smoke_test.py
# 等价：python -m unittest
```

覆盖：配置合并与密钥打码、尺寸工具、构图预设、翻译官 off、参考图三段式（类型 / 避免项 / 简报）、
出图编排（模拟 OpenAI 上游）、输出路径与镜像副本、CLI JSON 输出、词库统计、
SQLite schema v2 迁移、AssetService / Asset API、references → ReferenceResolver 集成、
OpenAIClient（endpoint 归一化 / Chat→Responses fallback / generations / edits / 模型拉取）、
尺寸原样透传与不符警告、WebUI 双 API 与参考面板前端契约。

另含：Public API / Service 层（Generation / Model / Config / Diagnostic）测试、
CLI / WebUI 依赖边界（AST 扫描）、独立打包入口（`python -m imagegen`）与
无 Codex 插件目录的 Core 独立导入测试。
