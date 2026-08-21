# DeepSeek ImageGen

为 Codex 中的 DeepSeek 等纯文本模型提供**图像生成**能力的插件（私有仓库）。

DeepSeek 本身无法直接生成图片；本插件提供图像生成桥接：由 DeepSeek 理解需求、撰写提示词，桥接脚本把提示词发给**图像模型后端**（默认本地 Vertex Proxy，自动读取端口/密钥/模型列表并选用最佳图像模型如 `gemini-3-pro-image`；可附加备用后端如 DragToken），拿到图片后保存并交付。

## 功能

- **主后端（本地 Vertex Proxy）**：自动读取代理端口/密钥/模型列表，选用最佳图像模型（`gemini-3-pro-image` 等），隐私可控、零云端依赖
- **备用后端（extra_backends）**：默认 Vertex 不变，可附加备用后端（如 DragToken `gpt-image-2`）；方向优先尺寸映射（竖版 1024x1536 / 横版 1536x1024 / 方图 1254x1254）、尺寸白名单（2K / 4K 超分 / 原生 4K）、`quality` 参数，出图时自动把尺寸写进提示词
- **提示词翻译官**：中文需求自动改写为结构化生图提示词（DeepSeek 默认，通道异常时本地 Gemini 自动兜底，`off` 直传）；兼容 `/v1` 端点（支持 opencode-go 等上游）
- **构图预设**：`--composition full-body / half-body / portrait / landscape`，锁定画幅 + 取景规则
- **真实尺寸校验（画布优先兜底）**：生成后实测尺寸，代理不守尺寸时自动用目标画幅画布 + 图生图兜底
- **参考图**：三段式提示词自动生成（类型识别 + 身份锚点清单 + 场景锚点丢弃）；最多 4 张多参考图，每张带用途标签，生成角色隔离简报；角色外观一致性以参考图为准（角色卡功能已移除）
- **提示词词库**：MySQL + SiliconFlow Embedding/Rerank 向量检索（`prompt_library` 库），生成时喂示例给翻译官，`search` 只检索活跃词条，归档数据可恢复
- **单模型改图**：改图需求由 Codex 写编辑指令 + 原图参考，图像模型自己看图改图，单轮完成（删除三模型自动接力）
- **自动副本**：每次生成成功后在 `mirror_dir` 保留副本（失败不影响主文件）
- **诊断**：`doctor`（连通性 + 尺寸探针）、`config`（密钥打码）、`list-models`

## 仓库结构

```
plugins/deepseek-imagegen/
├── .codex-plugin/plugin.json     # 插件清单（v1.0.0 基线）
├── skills/deepseek-imagegen/     # 技能说明（触发图像生成桥接）
├── assets/icon.png               # 插件图标
├── scripts/
│   ├── image_gen.py              # 薄入口：加载 src/imagegen 并调用 CLI
│   ├── prompt_lib.py             # 词库薄入口（兼容旧命令）
│   ├── codex_adapter.py          # Codex 环境默认值注入（Core 不依赖 Codex）
│   ├── webui.py                  # 兼容 launcher：启动 standalone ImageGen WebUI
│   ├── config.example.json       # 配置示例（真实 Key 放本地）
└── （生图核心已迁移到仓库根 src/imagegen/，插件不再包含实现）
```

> 本插件目录只保留 Adapter（`.codex-plugin/`、`skills/`、`assets/`、薄入口与 WebUI）。
> 真正的生图核心位于仓库根 `src/imagegen/`，即使删除 `.codex-plugin/` 与 `skills/`，
> `python scripts/image_gen.py ...` 仍可正常出图。
>
> CLI 与 WebUI 统一通过 `src/imagegen` 的 Public API / Service 层消费 Core，
> 插件目录不再直接依赖生图内部实现。

## 安装

```bash
codex plugin marketplace add "D:\deepseek-imagegen-plugin"
```

然后在 Codex 应用中安装 `DeepSeek ImageGen` 插件。

## 配置

配置位置：`~/.deepseek-imagegen/config.json`，模板见 `scripts/config.example.json`。

- `vertex.dir`：本地 Vertex Proxy 目录（默认 `C:\Users\yjq\Documents\Codex\2026-07-31\new-chat\outputs\vertex-proxy\dist`），自动读取 `config\config.json`（端口）、`config\api_keys.txt`（密钥）、`config\models.json`（模型列表）
- `extra_backends`：备用后端（如 DragToken `gpt-image-2`），含 `sizes` 尺寸白名单、`quality` 与可选模型列表
- `translator`：deepseek 的地址/密钥/模型 + gemini 模型（留空自动选最佳文本模型）
- `prompt_library`：MySQL 连接、Embedding / Rerank（SiliconFlow）、分类置顶
- `reference`：参考图自动分类与视觉识别（`auto_classify` / `vision_script` / `classify_timeout`）
- `size_policy`：`mode`（auto / aspect / exact；`strict` 已弃用等价 `aspect`，`warn` 已弃用等价 `auto`）、`retries`、`tolerance`、`probe_cache`

## 词库整理说明（v1.0 已执行）

- 单一 `prompt_library` 库；`prompts` 表新增 `archived` 列与分类索引
- `source=DrawingSpells(MIT)` 的 2000 条已标记 `archived=1`（数据保留可恢复，不参与检索）
- 分类已按映射合并：二次元角色 / 插画艺术 / 设计品牌 / 电商产品 / 摄影 / 海报排版 / 3D渲染 / 创意生活 / 其他；自家精品置顶逻辑不变
- 内容规范化去重（保留最早一条）；整理后总数 2936，活跃 936，归档 2000
- 角色卡功能已整体移除：不再内置角色设定与参考图，也不再有 `--character` / `--character-image` 参数；
  角色外观一致性由用户提供参考图（`--image` + 参考图类型识别）保证

## 测试

```bash
python ../../tests/run_smoke_test.py
```

覆盖：配置合并与密钥打码、尺寸工具、模型挑选、构图预设、翻译官 off、参考图三段式（类型/避免项/简报）、出图编排（模拟后端）、输出路径与镜像副本、CLI JSON 输出、词库统计。

## 改图流程

不再有"视觉模型检查 → DeepSeek 改词 → 重画"的三模型接力。需要改图时：

1. Codex 把用户修改意见写成明确的编辑指令（保留什么、改什么、换成什么）
2. 原图作为参考图（`--image`）
3. 图像模型自己看图改图，单轮完成

参考图类型由用户显式指定或视觉自动分类，角色外观一致性以用户提供的参考图为准。

## 网页界面（Web UI）

- **启动**：运行 `python scripts/webui.py`（默认打开 http://127.0.0.1:8765；`--port` 可改端口，
  `--no-browser` 不自动打开浏览器）。这是兼容 launcher：实际 WebUI 由独立 ImageGen
  Server（`imagegen serve`）提供，前端只通过 `/api/v1/*` 通信，不再由插件内部实现。
- **生成页**：提示词 + 参考图路径（每行一张，最多 4 张）+ 后端选择 / 快捷尺寸 / 构图预设 /
  模型下拉 / 批量出图 / 种子 / 翻译官 / 词库开关 → 经 `/api/v1/generate` 生成，
  预览与下载使用 `output_url`。
- **设置页**：可视化编辑配置（翻译官、默认出图参数、尺寸策略 auto/aspect/exact、词库、
  MySQL、Vertex、备用后端、参考图识别），密钥打码显示，保存走 `PATCH /api/v1/config`。
- **会话画廊**：仅当前页面会话内的生成结果（不持久化，Phase 5 提供 History API）。
- **提示**：当前 WebUI 为 v1 本地版：参考图使用本机路径（same-machine），历史为当前页面会话；
  浏览器文件上传与持久化历史将在后续版本提供。
