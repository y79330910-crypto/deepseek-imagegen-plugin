# DeepSeek ImageGen v1.0.0

为 Codex 中的 DeepSeek 等纯文本模型提供**图像生成**能力的插件（私有仓库）。

DeepSeek 本身无法直接生成图片；本插件提供图像生成桥接：由 DeepSeek 理解需求、撰写提示词，桥接脚本把提示词发给**本地 Vertex Proxy**（自动读取端口/密钥/模型列表，选用最佳图像模型如 `gemini-3-pro-image`），拿到图片后保存并交付。

## 功能

- **单一本地后端**：只保留 Vertex Proxy，删除其余 4 个后端（pollinations / siliconflow / sd-webui / comfyui），隐私可控、零云端依赖
- **提示词翻译官**：中文需求自动改写为结构化生图提示词（DeepSeek 默认，通道异常时本地 Gemini 自动兜底，`off` 直传）
- **构图预设**：`--composition full-body / half-body / portrait / landscape`，锁定画幅 + 取景规则
- **真实尺寸校验（画布优先兜底）**：生成后实测尺寸，代理不守尺寸时自动用目标画幅画布 + 图生图兜底
- **提示词词库**：MySQL + SiliconFlow Embedding/Rerank 向量检索（`prompt_library` 库），`search` 只检索活跃词条，归档数据可恢复
- **单模型改图**：改图需求由 Codex 写编辑指令 + 原图参考，图像模型自己看图改图，单轮完成（删除三模型自动接力）
- **自动副本**：每次生成成功后在 `mirror_dir` 保留副本（失败不影响主文件）
- **诊断**：`doctor`（连通性 + 尺寸探针）、`config`（密钥打码）、`list-models`

## 仓库结构

```
plugins/deepseek-imagegen/
├── .codex-plugin/plugin.json     # 插件清单（v1.0.0）
├── skills/deepseek-imagegen/     # 技能说明（触发图像生成桥接）
├── scripts/
│   ├── image_gen.py              # 薄入口（命令从这里进）
│   ├── prompt_lib.py             # 词库薄入口（兼容旧命令）
│   ├── config.example.json       # 配置示例（真实 Key 放本地）
│   ├── imagegen/                 # v1.0 模块化包
│   │   ├── cli.py                # 命令行
│   │   ├── config.py             # 配置读取/保存/密钥掩码
│   │   ├── http.py               # HTTP（超时 240s、429 退避、空数据重试）
│   │   ├── image_utils.py        # 尺寸解析/探测、画布兜底、参考图、输出路径
│   │   ├── vertex.py             # 代理发现与最佳模型挑选、文生图/图生图
│   │   ├── translator.py         # 提示词翻译官
│   │   ├── composition.py        # 构图预设
│   │   ├── library.py            # 词库（MySQL + 向量检索）
│   │   ├── generate.py           # 出图编排
│   │   └── doctor.py             # 诊断与尺寸探针
│   └── tests/run_smoke_test.py   # 单文件冒烟测试
└── assets/icon.png               # 插件图标
```

## 安装

```bash
codex plugin marketplace add "D:\deepseek-imagegen-plugin"
```

然后在 Codex 应用中安装 `DeepSeek ImageGen` 插件。

## 配置

配置位置：`~/.deepseek-imagegen/config.json`，模板见 `scripts/config.example.json`。

- `vertex.dir`：本地 Vertex Proxy 目录（默认 `C:\Users\yjq\Documents\Codex\2026-07-31\new-chat\outputs\vertex-proxy\dist`），自动读取 `config\config.json`（端口）、`config\api_keys.txt`（密钥）、`config\models.json`（模型列表）
- `translator`：deepseek 的地址/密钥/模型 + gemini 模型（留空自动选最佳文本模型）
- `prompt_library`：MySQL 连接、Embedding / Rerank（SiliconFlow）、分类置顶
- `size_policy`：`mode`（auto / strict / warn）、`retries`、`tolerance`、`probe_cache`

## 词库整理说明（v1.0 已执行）

- 单一 `prompt_library` 库；`prompts` 表新增 `archived` 列与分类索引
- `source=DrawingSpells(MIT)` 的 2000 条已标记 `archived=1`（数据保留可恢复，不参与检索）
- 分类已按映射合并：二次元角色 / 插画艺术 / 设计品牌 / 电商产品 / 摄影 / 海报排版 / 3D渲染 / 创意生活 / 其他；自家精品置顶逻辑不变
- 内容规范化去重（保留最早一条）；整理后总数 2936，活跃 936，归档 2000
- 角色卡功能已整体移除：不再内置角色设定与参考图，也不再有 `--character` / `--character-image` 参数；
  角色外观一致性由用户提供参考图（`--image` + 参考图类型识别）保证

## 测试

```bash
python scripts/tests/run_smoke_test.py
```

覆盖：配置合并与密钥打码、尺寸工具、模型挑选、构图预设、翻译官 off、参考图三段式（类型/避免项/简报）、出图编排（模拟后端）、输出路径与镜像副本、CLI JSON 输出、词库统计。

## 改图流程（v1.0）

不再有"视觉模型检查 → DeepSeek 改词 → 重画"的三模型接力。需要改图时：

1. Codex 把用户修改意见写成明确的编辑指令（保留什么、改什么、换成什么）
2. 原图作为参考图（`--image`）
3. 图像模型自己看图改图，单轮完成

参考图类型由用户显式指定或视觉自动分类，角色外观一致性以用户提供的参考图为准。

## 网页界面（Web UI）

- **启动**：双击桌面快捷方式「洛天依生图网页」，或运行 `python scripts/webui.py`（默认打开 http://127.0.0.1:8766；`--port` 可改端口，`--no-browser` 不自动打开浏览器）。
- **生成页**：提示词 + 参考图上传（拖拽/点选，PNG/JPG/WebP ≤20MB）+ 尺寸 / 构图预设 / 参考图类型 / 去噪强度 / 种子 / 模型 / 翻译官 / 词库开关 → 一键生图，预览、下载、显示种子与真实尺寸。
- **设置页**：可视化编辑 `~/.deepseek-imagegen/config.json`（翻译官、默认出图参数、尺寸策略、词库、MySQL、Vertex、参考图识别、壁纸），密钥打码显示；未改动的密钥不会被覆盖。
- **历史画廊**：最近 50 张生成记录，可回填参数重新生成、下载。
- **壁纸**：首次启动自动使用本机洛天依壁纸（`~/.deepseek-imagegen/webui/wallpaper.png`），设置页可随时更换。壁纸、历史记录、上传的参考图均只存本机，不进入仓库。
- **提示**：想严格保留参考图的角色身份时，请在生成页把「参考图类型」选为「角色人物」。