# deepseek-imagegen-plugin

为 Codex 中的 DeepSeek 等纯文本模型提供**图像生成**能力的插件（私有仓库）。

DeepSeek 本身无法直接生成图片；本插件提供图像生成桥接，把用户的文字需求交给可配置的后端渲染成图片：

- **Vertex Proxy**：默认后端，自动读取本地代理的端口/密钥/模型列表并选用最佳图像模型（gemini-3-pro-image 等）
- **Pollinations**：免费、免 API Key，开箱即用，可作兜底
- **SiliconFlow**：OpenAI 兼容图像接口，国内可直连，支持 FLUX 系列模型
- **Stable Diffusion WebUI / ComfyUI**：本地部署，完全离线、隐私可控
- **图生图**：支持以已有图片为底图进行编辑 / 换风格（vertex / sd-webui / comfyui，`--image` + `--denoise`）
- **提示词翻译官**：中文需求自动改写为结构化生图提示词（DeepSeek 默认 / Gemini 可选），并支持自动看图改图
- **构图预设（v0.7）**：`--composition full-body / half-body / portrait / landscape`，锁定画幅 + 取景规则 + 视觉检查清单
- **真实尺寸校验（v0.7）**：生成后读取文件头实测尺寸（`actual_size`），代理不遵守尺寸时自动用画布优先兜底
- **分类自动修复（v0.7）**：构图问题与细节问题分开处理，修复轮校验输出尺寸，keep-best 按检查清单加权
- **健壮性（v0.7）**：空数据自动重试、后端降级、超时 240s、PowerShell 中文不乱码
- **角色卡（v2 预览）**：本机 MySQL 存已核实角色设定（洛天依 V4 公式服等），`--character` 自动注入
- **外扩画布（v2 预览）**：`--expand WxH` 把已有图扩成目标画幅
- **自动副本**：每次生成成功后自动在 `C:\Users\yjq\Pictures\codex` 保留一份副本，方便管理（可在配置中修改）

内置本地可视化设置页面（`python image_gen.py webui`，默认 http://127.0.0.1:8766），可一键导入代理配置、修改后端参数、测试连通性与试生成。

## 仓库结构

```
.
├── .agents/plugins/marketplace.json      # Codex marketplace 清单
└── plugins/deepseek-imagegen/            # 插件本体
    ├── .codex-plugin/plugin.json         # 插件清单
    ├── skills/                           # 技能（触发图像生成桥接）
    ├── scripts/image_gen.py              # 图像生成桥接核心（零第三方依赖）
    ├── scripts/webui.py                  # 本地可视化设置页面
    ├── scripts/character_lib.py          # 角色卡（v2，本机 MySQL）
    ├── scripts/config.example.json       # 配置示例（真实 Key 放本地）
    ├── scripts/tests/                    # 冒烟测试（run_smoke_test.py + v0.7 专项测试）
    └── assets/                           # 图标
```

## 安装

```bash
codex plugin marketplace add "D:\deepseek-imagegen-plugin"
```

然后在 Codex 应用中安装 `DeepSeek ImageGen` 插件，并参考 [插件 README](plugins/deepseek-imagegen/README.md) 使用与配置后端（默认绑定本地 Vertex Proxy，开箱即用）。
