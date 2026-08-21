# deepseek-imagegen-plugin

为 Codex 中的 DeepSeek 等纯文本模型提供**图像生成**能力的插件（私有仓库）。

DeepSeek 本身无法直接生成图片；本插件提供图像生成桥接：由 DeepSeek 理解需求、撰写提示词，桥接脚本把提示词发给图像模型后端，拿到图片后保存并交付。

## 功能

- **主后端（本地 Vertex Proxy）**：自动读取代理端口 / 密钥 / 模型列表，选用最佳图像模型（`gemini-3-pro-image` 等），隐私可控、零云端依赖
- **备用后端（extra_backends）**：默认 Vertex 不变，可附加备用后端（如 DragToken `gpt-image-2`）；方向优先尺寸映射（竖版 1024x1536 / 横版 1536x1024 / 方图 1254x1254）、尺寸白名单（2K / 4K 超分 / 原生 4K）、`quality` 参数，出图时自动把尺寸写进提示词
- **提示词翻译官**：中文需求自动改写为结构化生图提示词（DeepSeek 默认，通道异常时本地 Gemini 自动兜底，`off` 直传）；兼容 `/v1` 端点（支持 opencode-go 等上游）
- **构图预设 + 真实尺寸校验**：`--composition full-body / half-body / portrait / landscape`，锁定画幅 + 取景规则；生成后实测尺寸，代理不守尺寸时自动画布优先兜底
- **参考图**：三段式提示词自动生成（类型识别 + 身份锚点清单 + 场景锚点丢弃）；支持最多 4 张多参考图，每张带用途标签，生成角色隔离简报；角色外观一致性以用户提供的参考图为准（角色卡功能已移除）
- **提示词词库**：MySQL + SiliconFlow Embedding / Rerank 向量检索（`prompt_library` 库），生成时喂示例给翻译官
- **网页界面（洛天依主题）**：`python scripts/webui.py` 启动（默认 http://127.0.0.1:8766，`--port` / `--no-browser` 可调）——生成页（提示词 / 参考图上传 / 尺寸 / 构图 / 模型 / 批量出图）、设置页（可视化编辑配置，密钥打码）、历史画廊（最近 50 张，可回填参数重新生成、复制生效提示词全文）
- **自动副本**：生成成功后自动在 `mirror_dir` 保留副本
- **诊断**：`doctor`（连通性 + 尺寸探针）、`config`（密钥打码）、`list-models`

## 仓库结构

```
.
├── .agents/plugins/marketplace.json      # Codex marketplace 清单
├── src/imagegen/                         # ImageGen Core（独立于 Codex）
│   ├── engine.py / models.py / errors.py # 编排、统一数据模型、通用错误
│   ├── config.py / http.py / image_utils.py
│   ├── composition.py / reference.py / translator.py
│   ├── library.py / doctor.py / cli.py
│   └── backends/                         # Backend API v1 + 注册表
│       ├── base.py / registry.py
│       ├── vertex.py                     # 本地 Vertex Proxy
│       └── openai_images.py              # OpenAI 兼容 / extra_backends
├── plugins/deepseek-imagegen/            # Codex 插件（仅 Adapter）
│   ├── .codex-plugin/plugin.json         # 插件清单
│   ├── skills/deepseek-imagegen/         # 技能（触发图像生成桥接）
│   ├── assets/icon.png                   # 插件图标
│   └── scripts/
│       ├── image_gen.py                  # 薄入口：加载 Core 并调用 CLI
│       ├── prompt_lib.py                 # 词库薄入口
│       ├── codex_adapter.py              # Codex 环境默认值注入（Core 不依赖）
│       ├── webui.py                      # 网页界面（洛天依主题）
│       └── config.example.json           # 配置示例（真实 Key 放本地）
└── tests/                                # 统一测试（python -m unittest）
    ├── run_smoke_test.py                 # 统一测试入口
    └── test_*.py                         # Core / Backend / 回归测试
```

## 安装

```bash
codex plugin marketplace add "D:\deepseek-imagegen-plugin"
```

然后在 Codex 应用中安装 `DeepSeek ImageGen` 插件，并参考 [插件 README](plugins/deepseek-imagegen/README.md) 使用与配置。

## 配置

配置位置：`~/.deepseek-imagegen/config.json`，模板见 `scripts/config.example.json`。

- `vertex.dir`：本地 Vertex Proxy 目录，自动读取端口 / 密钥 / 模型列表
- `extra_backends`：备用后端（如 DragToken），含尺寸白名单与 `quality` 参数
- `translator`：deepseek 地址 / 密钥 / 模型 + gemini 模型（留空自动选最佳文本模型）
- `prompt_library`：MySQL 连接、Embedding / Rerank（SiliconFlow）、分类置顶
- `size_policy`：`mode`（auto / strict / warn）、`retries`、`tolerance`、`probe_cache`
- `reference`：参考图自动分类与视觉识别脚本

## 测试

```bash
python tests/run_smoke_test.py
# 等价：python -m unittest
```

覆盖：配置合并与密钥打码、尺寸工具、模型挑选、构图预设、翻译官 off、参考图三段式（类型 / 避免项 / 简报）、出图编排（模拟后端）、输出路径与镜像副本、CLI JSON 输出、词库统计。
