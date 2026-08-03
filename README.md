# deepseek-imagegen-plugin

为 Codex 中的 DeepSeek 等纯文本模型提供**图像生成**能力的插件（私有仓库）。

DeepSeek 本身无法直接生成图片；本插件提供图像生成桥接，把用户需求交给**本地 Vertex Proxy** 渲染成图片：

- **单一本地后端（v1.0 精简）**：自动读取代理端口/密钥/模型列表，选用最佳图像模型（gemini-3-pro-image 等）；删除 pollinations / siliconflow / sd-webui / comfyui 与可视化设置页
- **提示词翻译官**：中文需求自动改写为结构化生图提示词（DeepSeek 默认 / Gemini 兜底 / off 直传）
- **构图预设 + 真实尺寸校验（v1.0）**：`--composition full-body / half-body / portrait / landscape`，生成后实测尺寸，代理不守尺寸时自动画布优先兜底
- **单模型改图（v1.0）**：编辑指令 + 原图参考，图像模型自己看图改图，单轮完成
- **提示词词库**：MySQL + SiliconFlow Embedding/Rerank 向量检索（`prompt_library` 库，2000 条 DrawingSpells 已归档保留）
- **自动副本**：生成成功后自动在 `mirror_dir` 保留副本
- **诊断**：`doctor`（连通性 + 尺寸探针）、`config`（密钥打码）、`list-models`

## 仓库结构

```
.
├── .agents/plugins/marketplace.json      # Codex marketplace 清单
└── plugins/deepseek-imagegen/            # 插件本体（v1.0.0）
    ├── .codex-plugin/plugin.json         # 插件清单
    ├── skills/deepseek-imagegen/         # 技能（触发图像生成桥接）
    ├── scripts/image_gen.py              # 薄入口
    ├── scripts/prompt_lib.py             # 词库薄入口
    ├── scripts/imagegen/                 # v1.0 模块化包（cli/config/http/image_utils/
    │                                     #   vertex/translator/composition/reference/
    │                                     #   library/generate/doctor）
    ├── scripts/tests/run_smoke_test.py   # 单文件冒烟测试
    └── assets/icon.png                   # 插件图标
```

## 安装

```bash
codex plugin marketplace add "D:\deepseek-imagegen-plugin"
```

然后在 Codex 应用中安装 `DeepSeek ImageGen` 插件，并参考 [插件 README](plugins/deepseek-imagegen/README.md) 使用与配置。
