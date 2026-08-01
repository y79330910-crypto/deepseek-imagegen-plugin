---
name: deepseek-imagegen
description: 当用户要求生成图片、插画、海报、图标、照片、概念图、横幅、游戏素材、UI 草图等位图视觉内容，或说"画一张 / 生成一张图片 / 做个图 / 帮我出图"时使用。DeepSeek 是纯文本模型，无法直接生成图片；本技能调用图像生成桥接脚本，把文字需求交给可配置的后端（免费 Pollinations、SiliconFlow FLUX、本地 Stable Diffusion WebUI / ComfyUI）渲染成 PNG 后再交付。Use when the user asks to create, draw, or generate an image, illustration, poster, icon, photo, concept art, banner, game asset, or UI mockup and the backend must be an external image generation service.
---

# DeepSeek ImageGen —— 图像生成桥接

## 这个技能解决什么问题

DeepSeek（deepseek-chat / deepseek-reasoner 等）是纯文本模型，本身不能生成图片。本技能提供一条图像生成桥接通道：由你（DeepSeek）负责理解用户需求、撰写高质量提示词，再调用本插件的桥接脚本把提示词发给图像后端，拿到 PNG 后保存并交付。图像后端负责"画"，你负责"想"。

## 操作步骤

### 1. 定位桥接脚本

- 脚本位于插件目录下的 `scripts/image_gen.py`（插件目录包含 `.codex-plugin/plugin.json`）。
- 如果不知道插件目录，先搜索 `image_gen.py` 或 `.codex-plugin` 目录；找不到再询问用户。
- 常见路径：`D:\deepseek-imagegen-plugin\plugins\deepseek-imagegen\scripts\image_gen.py`

### 2. 选择后端（默认免费免密钥，开箱即用）

- **pollinations**（默认）：免费、无需 API Key，适合快速出图。
- **siliconflow**：国内可直连，FLUX 等模型，需要 `api_key`（配置见第 5 步）。
- **sd-webui**：本地 Stable Diffusion WebUI / Forge（`http://127.0.0.1:7860`），需本地服务已启动并带 `--api`。
- **comfyui**：本地 ComfyUI（`http://127.0.0.1:8188`），需本地服务已启动。

如果用户没说用哪个后端，优先用默认配置（`python "<脚本路径>" config` 可查看当前默认后端）。

### 3. 执行生成命令（图片/文件路径含空格时加引号）

```text
python "<脚本路径>" generate "<提示词>" [选项]
```

常用选项：

- `--backend pollinations|siliconflow|sd-webui|comfyui`：指定后端
- `--out <输出文件路径>`：指定输出文件（项目相关图片必须保存到工作区）
- `--size 1024x1024`：分辨率（宽x高）
- `--seed 12345`：随机种子（复现同一张图）
- `--negative "不想出现的内容"`：负面提示词（sd-webui / comfyui）
- `--steps 28` / `--cfg 7`：采样步数 / 引导强度（sd-webui / comfyui）
- `--model <模型名>`：指定模型（pollinations / siliconflow）
- `--json`：机器可读输出，读取 `path`、`seed`、`backend` 字段

需要机器可读结果时加 `--json`。生成失败时脚本会返回非零退出码并给出 `error` 说明。

### 4. 使用结果组织交付

- 默认输出到当前工作目录：`deepseek-imagegen_<时间戳>_<提示词摘要>.png`。
- 如果图片属于当前项目，务必用 `--out` 保存到项目内（例如 `outputs/imagegen/xxx.png`）并在最终回答中给出保存路径。
- 如果只是预览/头脑风暴，可以保存在临时目录，并在回答里内联展示图片。
- 不要凭空编造"已生成"的图片；脚本报错或没有输出文件时，如实告知用户并给出诊断建议。

### 5. 配置后端（首次使用 siliconflow / 修改默认后端时）

- 配置位置：`~/.deepseek-imagegen/config.json`（即 `C:\Users\<用户名>\.deepseek-imagegen\config.json`）。
- 参考模板：`scripts/config.example.json`。API Key 只保存在本机，绝不写入仓库。
- 用户想修改地址、密钥或模型时，先运行 `python "<脚本路径>" doctor` 查看诊断，再引导用户编辑配置文件（或直接代为创建）。

### 6. 故障处理

- 脚本报错时，先运行 `python "<脚本路径>" doctor` 查看各后端连通性诊断。
- 提示未配置 siliconflow 密钥：需要用户在 `config.json` 里填写 `siliconflow.api_key`（在 https://cloud.siliconflow.cn 申请）。
- 提示本地 SD / ComfyUI 连不上：需要用户先启动本地服务；SD WebUI 需带 `--api` 参数启动。
- 查看后端可用模型：`python "<脚本路径>" list-models`。
- 修改配置后无需重启 Codex，脚本每次运行都会重新读取配置。

## 注意事项

- 提示词质量决定出图质量：把用户需求整理成"主体 + 场景 + 风格 + 构图 + 光线 + 色彩 + 禁止项"的结构化描述。
- 隐私：云端后端（pollinations / siliconflow）会收到提示词内容；敏感内容建议使用本地 sd-webui / comfyui。
- 尺寸默认 1024x1024；不同后端支持的分辨率不同，出错时按后端限制调整。
- 不要向用户索要 API Key 明文贴到聊天里；引导用户把 Key 写入本地配置文件。
