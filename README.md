# deepseek-imagegen-plugin

为 Codex 中的 DeepSeek 等纯文本模型提供**图像生成**能力的插件（私有仓库）。

DeepSeek 本身无法直接生成图片；本插件提供图像生成桥接，把用户的文字需求交给可配置的后端渲染成 PNG：

- **Pollinations**：免费、免 API Key，开箱即用（默认后端）
- **SiliconFlow**：OpenAI 兼容图像接口，国内可直连，支持 FLUX 系列模型
- **Stable Diffusion WebUI / ComfyUI**：本地部署，完全离线、隐私可控

## 仓库结构

```
.
├── .agents/plugins/marketplace.json      # Codex marketplace 清单
└── plugins/deepseek-imagegen/            # 插件本体
    ├── .codex-plugin/plugin.json         # 插件清单
    ├── skills/                           # 技能（触发图像生成桥接）
    ├── scripts/image_gen.py              # 图像生成桥接核心（零第三方依赖）
    ├── scripts/config.example.json       # 配置示例（真实 Key 放本地）
    └── assets/                           # 图标
```

## 安装

```bash
codex plugin marketplace add "D:\deepseek-imagegen-plugin"
```

然后在 Codex 应用中安装 `DeepSeek ImageGen` 插件，并参考 [插件 README](plugins/deepseek-imagegen/README.md) 使用与配置后端（默认免费免密钥，开箱即用）。
