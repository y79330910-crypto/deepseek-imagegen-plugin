# ImageGen

Standalone image generation application with WebUI, CLI and local HTTP API.

ImageGen 是一个独立的本地图像生成应用：Python Core、命令行（`imagegen`）、
Local HTTP API v2 与完整 WebUI。架构只保留两套独立的 OpenAI-Compatible 上游：

- **translator upstream**：提示词上游，独立 `base_url` / `api_key` / `model`
- **image upstream**：图像上游，独立 `base_url` / `api_key` / `model`

两者共用统一 `OpenAIClient`，配置互不借用、互不 fallback。

## 功能

- **提示词上游（text upstream）**：默认 `POST /v1/chat/completions`，仅当 Chat
  Completions 明确不支持（HTTP 404 / 405 / 501）时 fallback `POST /v1/responses`；
  401 / 403 / 429 / 其他 5xx / 超时 / 网络错误直接报错，不切换 endpoint。

  > Text upstream uses `/v1/chat/completions` by default and only falls back to
  > `/v1/responses` when Chat Completions is explicitly unsupported
  > (HTTP 404/405/501).

- **图像上游（image upstream）**：文生图 `POST /v1/images/generations`；
  带参考图 `POST /v1/images/edits`（支持最多 4 张，兼容
  `b64_json` / `image` / `url` / `data:image/...` 返回）；两条路径之间不存在自动 fallback
- **尺寸原样透传**：任意合法 `WxH`（如 1920x1080 / 1080x1920 / 1536x864）原样发送上游，
  不做归一化 / 预设匹配 / 尺寸重试 / 能力探测；输出尺寸不符只产生 warning
- **quality 行为**：请求 `quality` 为空时使用配置 `image.quality`；两者都为空时
  payload 完全省略 `quality` 字段
- **构图预设 + 尺寸检查**：`--composition full-body / half-body / portrait / landscape`
  锁定画幅与取景规则；生成后读取真实输出尺寸，`size_check` 可开关
- **参考图**：三段式提示词自动生成（类型识别 + 身份锚点清单）；Reference Asset System
  提供持久化素材库（上传 / 拖放 / 粘贴 / 本机导入 → managed asset → Asset API）
- **结构化提示词词库**：MySQL + SiliconFlow Embedding / Rerank 的 Prompt Case
  解析、Intent / Visual 双路检索、结构化重排与多样性选择；提示词优化支持
  `conservative` / `optimized`（默认）/ `creative`
- **Standalone WebUI**：`imagegen serve --open`（默认 http://127.0.0.1:8765），
  生成页 / 设置页（两套独立 OpenAI-Compatible API + 「拉取模型」）/ 诊断页 / 持久化历史画廊
- **自动副本**：生成成功后按 `mirror_dir` 保留副本
- **诊断**：`imagegen doctor`（两组上游连通性）、`imagegen config`（密钥打码）、
  `imagegen list-models`

## 仓库结构

```text
.
├── pyproject.toml                    # 独立包（pip install -e . / imagegen 命令）
├── LICENSE                           # MIT License
├── src/imagegen/                     # ImageGen Core（独立应用）
│   ├── __init__.py                   # Public Core API（CORE_API_VERSION=2）
│   ├── _version.py                   # 唯一版本源（__version__ = "2.1.1"）
│   ├── engine.py / models.py / errors.py
│   ├── config.py / http.py / image_utils.py
│   ├── composition.py / reference.py / translator.py
│   ├── library.py / doctor.py / cli.py / __main__.py
│   ├── openai_client.py              # 统一 OpenAI-Compatible Client
│   ├── api/                          # Local HTTP API v2（纯协议适配器）
│   │   ├── server.py / routes.py / responses.py / outputs.py
│   ├── web/                          # Standalone WebUI 静态资源
│   │   └── static/index.html / app.js / style.css
│   └── services/                     # Application Service 层
│       ├── db.py                     # ImageGen 2 SQLite（DB_SCHEMA_VERSION=2）
│       ├── assets.py / references.py / generation.py / previews.py
│       ├── models.py / config.py / diagnostics.py
└── tests/                            # 统一测试（python -m unittest）
    └── test_*.py
```

## 独立安装与运行

```bash
pip install -e .
```

启动 Standalone WebUI（同时提供本地 HTTP API v2）：

```bash
imagegen serve --open
# 浏览器打开 http://127.0.0.1:8765/
```

命令行方式：

```bash
imagegen generate "..." --composition full-body --prompt-mode creative
imagegen translate "..."        # 使用当前 translator 配置
imagegen config
imagegen doctor
imagegen list-models
# 或
python -m imagegen ...
```

## Local HTTP API v2

```bash
imagegen serve
```

本地接口全部位于 `/api/v2/`：

```text
GET    /api/v2/health
POST   /api/v2/generate
POST   /api/v2/models                      # {target: "translator" | "image"}
GET    /api/v2/config
PATCH  /api/v2/config
POST   /api/v2/doctor
GET    /api/v2/outputs/{generation_id}
GET    /api/v2/history
GET    /api/v2/history/{generation_id}
DELETE /api/v2/history/{generation_id}
POST   /api/v2/assets                      # multipart 上传（file + kind=reference）
POST   /api/v2/assets/import               # 服务器本机路径导入 {path, kind}
GET    /api/v2/assets                      # ?kind=&q=&limit=&offset=
GET    /api/v2/assets/{asset_id}
GET    /api/v2/assets/{asset_id}/content
DELETE /api/v2/assets/{asset_id}           # 被历史引用时返回 409 asset_in_use
```

旧本地路径 `/api/v1/*` 已删除（直接 404，无 redirect / alias）。

> 注意：这里升级的是 ImageGen **本地** API。上游 OpenAI-Compatible 接口仍然是
> `/v1/chat/completions`、`/v1/responses`、`/v1/models`、
> `/v1/images/generations`、`/v1/images/edits`，与本地 `/api/v2` 没有版本关联。

示例：

```bash
curl http://127.0.0.1:8765/api/v2/health

curl -X POST http://127.0.0.1:8765/api/v2/generate \
  -H "Content-Type: application/json" \
  -d '{"prompt":"a cat","size":"1024x1024"}'
```

`POST /api/v2/generate` 请求体直接使用 `GenerateRequest` JSON contract；响应基于
`GenerateResult` 并附加 `output_url`（`/api/v2/outputs/{generation_id}`），
不暴露服务器本地文件路径。`GET /api/v2/config` 只返回打码后的 effective config，
`PATCH` 复用 `ConfigService.update`（env secret 不会被写入 config.json）。

`prompt_mode` 可选 `conservative`、`optimized`、`creative`，默认是 `optimized`，
会贯穿 Query Parser、词库案例选择和 Translator；旧历史记录没有该字段时按
`optimized` 复用。

词库升级后可用 `prompt_lib rebuild-cases [--limit N] [--force]`
（或 `python -m imagegen.library rebuild-cases`）为旧 Prompt 补齐 Facets、
Intent / Visual 文本与双向量。迁移按当前 Parser / Embedding 版本增量执行，
单条失败会继续处理其他记录；旧 `embedding` / `requirement_embedding` 字段保留，
不会被新向量覆盖。

### 模型拉取（非强依赖）

`POST /api/v2/models` 按 target 分别请求两组上游的 `/v1/models`，各自使用自己的
`base_url` / `api_key`：

```json
{"target": "translator"}
{"target": "image"}
```

模型列表只是 UI / CLI 辅助能力；拉取失败不影响手工填写 `translator.model` /
`image.model`，生成流程不依赖 `/models` 成功。

### Reference Asset System

默认数据目录：

```text
~/.imagegen/
├── config.json
├── imagegen.db          # SQLite（generations / assets / generation_assets）
└── assets/
    └── references/      # <asset_id>.png/.jpg/...（不登记原始路径）
```

`POST /api/v2/generate` 支持 `references` transport 协议：

```json
{
  "prompt": "…",
  "references": [
    {"asset_id": "…", "role": "character"},
    {"asset_id": "…", "role": "style"}
  ]
}
```

HTTP 层通过 `ReferenceResolver` 把 `asset_id` 解析成 managed 本地路径，再转换为
Core 契约 `images` + `reference_roles`；`GenerateRequest` / Engine 不知道 asset_id。
生成成功后 best-effort 记录 `generation_assets`（generation → asset、role、position），
写入失败只追加 warning。历史详情返回 `references`，HTTP 任何位置都不暴露
managed `file_path`。

## 缩略图 / Preview 缓存（2.1.0）

- **懒缩略图**：`GET /api/v2/outputs/{generation_id}/thumbnail` 与
  `GET /api/v2/assets/{asset_id}/thumbnail` 按需生成 WebP（max side 512、
  quality 82），不请求不会预先生成。
- **Preview 缓存**：`~/.imagegen/cache/previews/{generations,assets}/`；
  cache identity = logical id + 源文件 size + mtime_ns + profile version，
  源文件变化 / profile 变化自动失效；写入使用临时文件 + atomic replace。
- **浏览器缓存**：缩略图与 managed asset 使用
  `private, max-age=31536000, immutable`；generation 原图使用
  `private, max-age=3600`；全部响应带 `ETag` / `Last-Modified`，
  `If-None-Match` 命中返回空 body 的 `304 Not Modified`。
- **删除联动**：删除历史只清理对应 generation preview（原图保留）；
  删除 asset 同时删除 managed 文件与 preview；`asset_in_use → 409` 语义不变。
- **分页**：`GET /api/v2/history` 与 `GET /api/v2/assets` 支持
  `limit` / `offset`，响应含 `has_more` / `next_offset`（limit+1 探测）；
  WebUI 默认每页 24，卡片使用 `thumbnail_url` + `loading="lazy"` +
  `decoding="async"`，加载失败自动回退原图。
- **历史复用**：Gallery 卡片「复用参数」→ `GET /api/v2/history/{id}` →
  恢复 prompt / size / model / quality / composition / translator /
  library_enabled 与 references（role 顺序保持）。
  History Detail 的 `request` 只公开 allow-list 字段，本机路径字段
  （images / out / path / output_path / mirror_path / file_path）绝不外泄。
- **生效提示词查看**：Gallery 卡片「查看提示词」→ `GET /api/v2/history/{id}` →
  生成详情 Modal 显示「原始提示词」（用户输入）与「生效提示词」（`prompt_used`），
  长文本可展开 / 收起，支持一键复制完整 `prompt_used`；记录未保存生效提示词时
  明确提示，不冒充原始提示词。

### 安全说明

- 默认仅监听 `127.0.0.1`；绑定非 loopback 地址必须显式 `--allow-remote`。
- `--allow-remote` 无身份验证，只应在可信网络环境使用。
- 错误统一为 `{"error": {"type": "...", "message": "..."}}`；
  上游失败类型为 `upstream_error`。

## 配置

配置位置：`~/.imagegen/config.json`。生效优先级固定为：

```text
DEFAULT_CONFIG < config.json < IMAGEGEN_*
```

核心配置结构：

```json
{
  "translator": {
    "enabled": true,
    "base_url": "",
    "api_key": "",
    "model": "",
    "output_lang": "zh"
  },
  "image": {
    "base_url": "",
    "api_key": "",
    "model": "",
    "quality": ""
  },
  "default_size": "1024x1024",
  "size_check": {
    "enabled": true,
    "tolerance": 0.06
  }
}
```

同时保留 `composition` / `reference` / `prompt_library` / `save_dir` /
`mirror_dir` 等子系统配置。完整模板见仓库根目录 `config.example.json`。

### IMAGEGEN_* 环境变量

```text
IMAGEGEN_TRANSLATOR_BASE_URL
IMAGEGEN_TRANSLATOR_API_KEY
IMAGEGEN_TRANSLATOR_MODEL
IMAGEGEN_TRANSLATOR_OUTPUT_LANG

IMAGEGEN_IMAGE_BASE_URL
IMAGEGEN_IMAGE_API_KEY
IMAGEGEN_IMAGE_MODEL
IMAGEGEN_IMAGE_QUALITY

IMAGEGEN_DEFAULT_SIZE
IMAGEGEN_SAVE_DIR
IMAGEGEN_MIRROR_DIR

IMAGEGEN_SIZE_CHECK_ENABLED
IMAGEGEN_SIZE_CHECK_TOLERANCE
```

规则：

- 字符串变量只要存在就视为显式 override（允许空字符串作为明确覆盖值）。
- Boolean（`IMAGEGEN_SIZE_CHECK_ENABLED`）接受 `true/false`、`1/0`、`on/off`、
  `yes/no`，大小写不敏感；非法值（含空值）抛 `ConfigurationError`。
- Float（`IMAGEGEN_SIZE_CHECK_TOLERANCE`）显式转换并校验；非法值抛
  `ConfigurationError`，不静默使用默认值。

### raw config 与 effective config

- **raw config**：磁盘上的 `~/.imagegen/config.json`，不包含环境变量。
- **effective config**：`DEFAULT + config.json + IMAGEGEN_*`，运行时真正生效的配置。

WebUI / Runtime 读取 effective config；配置文件写入只修改 raw config，禁止把
effective config 整体写回磁盘。环境变量中的 API Key 会影响运行时，但不会被一次
普通 WebUI 保存操作“物化”到 `config.json`。

## 请求 contract（Core `GenerateRequest`）

```text
prompt
prompt_mode
size
model
quality
composition
translator          # 仅 auto / off
images
reference_roles
library_enabled
out
```

已删除的旧字段（`width` / `height` / `ref_type` / `denoise`）收到时**明确报
validation error，而不是静默忽略**。`translator` 只接受 `auto`（跟随配置）与
`off`（本次强制直传）。HTTP `references` 是 transport 字段，由 Resolver 消费后
转换为 Core 的 `images` / `reference_roles`，不受 strict validation 影响。

## 结果 / 错误 contract

- `GenerateResult` 公共字段：`ok` / `generation_id` / `image_model_used` /
  `quality` / `requested_size` / `actual_size` / `size_match` /
  `size_check` / `prompt_used` / `composition` / `composition_preset` /
  `translator` / `reference` / `prompt_library` / `warnings` / `bytes` /
  `init_images` / `mirror_path`；Core / CLI 另有本地 `path`。
- HTTP generate 响应只使用 `output_url`，不暴露服务器本地 `path` / `mirror_path`。
- 错误层级：`ImageGenError` → `ConfigurationError` / `ValidationError` /
  `UpstreamError`（→ `HTTPStatusError` / `EmptyImageError`）/
  `AssetError` / `IncompatibleDatabaseError`。

## SQLite

ImageGen 2.x 数据库 schema lineage 第二版（`DB_SCHEMA_VERSION = 2`）：

- `generations`：无 `backend` 列
- `assets`：保持现有结构
- `generation_assets`：加入真正的外键（`generation_id → generations(id)`
  `ON DELETE CASCADE`，`asset_id → assets(id)` `ON DELETE RESTRICT`）

schema v1（2.0 / 2.1.0）数据库会在启动时自动安全迁移到 v2：只重建
`generation_assets`，**不删除任何 `generations` / `assets` 数据**；仅保留
generation 与 asset 均仍存在的 relation，孤儿 relation 自动清理。迁移全程
事务化（all-or-nothing），结束后执行 `foreign_key_check`。

旧的随机性控制参数已从公共契约删除（上游并不消费该值）；数据库中的兼容列
保留但不进入业务对象，新记录统一写入 NULL。

`initialize_db()` 只创建新库、打开当前 schema 或执行 v1 → v2 迁移；已存在且
无法识别的 `~/.imagegen/imagegen.db` 不会被静默删除或重建，而是明确报
schema incompatibility。

## 测试

```bash
python tests/run_smoke_test.py
# 等价：python -m unittest
```

覆盖：legacy removal 扫描、GenerateRequest strict contract、配置优先级与 env 解析、
secret 安全、旧路径忽略、DB 初始化与不兼容保护、HTTP v2 路由、CLI contract、
Chat-first / Responses fallback（仅 404/405/501）、两组 models 独立、
generations / edits 选择、尺寸原样透传、quality 省略、response parsing、
endpoint 归一化、打包与 WebUI 前端契约；2.1.0 另覆盖 Preview 管线
（横/竖/方图、不放大、透明、EXIF、动画首帧、缓存命中与失效）、thumbnail
HTTP 端点（ETag / 304 / 404）、History / Asset 分页、Detail 安全字段与
参数复用前端契约。
