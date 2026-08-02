---
name: deepseek-imagegen
description: 当用户要求生成图片、插画、海报、图标、照片、概念图、横幅、游戏素材、UI 草图等位图视觉内容，或说"画一张 / 生成一张图片 / 做个图 / 帮我出图"时使用。DeepSeek 是纯文本模型，无法直接生成图片；本技能调用图像生成桥接脚本，把文字需求交给可配置的后端（默认本地 Vertex Proxy 并自动选用最佳图像模型；也可用免费 Pollinations、SiliconFlow FLUX、本地 Stable Diffusion WebUI / ComfyUI）渲染成图片后再交付。Use when the user asks to create, draw, or generate an image, illustration, poster, icon, photo, concept art, banner, game asset, or UI mockup and the backend must be an external image generation service.
---

# DeepSeek ImageGen —— 图像生成桥接

## 这个技能解决什么问题

DeepSeek（deepseek-chat / deepseek-reasoner 等）是纯文本模型，本身不能生成图片。本技能提供一条图像生成桥接通道：由你（DeepSeek）负责理解用户需求、撰写高质量提示词，再调用本插件的桥接脚本把提示词发给图像后端，拿到图片后保存并交付。图像后端负责"画"，你负责"想"。

## 操作步骤

### 1. 定位桥接脚本

- 脚本位于插件目录下的 `scripts/image_gen.py`（插件目录包含 `.codex-plugin/plugin.json`）。
- 如果不知道插件目录，先搜索 `image_gen.py` 或 `.codex-plugin` 目录；找不到再询问用户。
- 常见路径：`D:\deepseek-imagegen-plugin\plugins\deepseek-imagegen\scripts\image_gen.py`

### 2. 选择后端

- **vertex**（默认）：本地 Vertex Proxy。自动读取代理目录下的端口（`config\config.json`）、API Key（`config\api_keys.txt`）与模型列表（`config\models.json`），并自动选用其中**最好的图像模型**（如 `gemini-3-pro-image`：非预览、pro 优先、版本更高）。代理未启动时会报连接错误。
- **pollinations**：免费、无需 API Key，适合快速出图或代理不可用时兜底。
- **siliconflow**：国内可直连，FLUX 等模型，需要 `api_key`（配置见第 6 步）。
- **sd-webui**：本地 Stable Diffusion WebUI / Forge（`http://127.0.0.1:7860`），需本地服务已启动并带 `--api`。
- **comfyui**：本地 ComfyUI（`http://127.0.0.1:8188`），需本地服务已启动。

如果用户没说用哪个后端，优先用默认配置（`python "<脚本路径>" config` 可查看当前默认后端）。

### 3. 打开可视化设置页面（推荐）

```text
python "<脚本路径>" webui
```

默认地址 http://127.0.0.1:8766。页面支持一键从 Vertex Proxy 导入端口/密钥/模型列表（自动选中最佳图像模型）、修改各后端参数、测试连通性、试生成小图、保存配置。用户想修改后端、密钥或模型时优先引导使用该页面。

### 4. 执行生成命令（图片/文件路径含空格时加引号）

```text
python "<脚本路径>" generate "<提示词>" [选项]
```

常用选项：

- `--backend vertex|pollinations|siliconflow|sd-webui|comfyui`：指定后端
- `--out <输出文件路径>`：指定输出文件（项目相关图片必须保存到工作区）
- `--size 1024x1024`：分辨率（宽x高）
- `--seed 12345`：随机种子（复现同一张图）
- `--negative "不想出现的内容"`：负面提示词（sd-webui / comfyui）
- `--steps 28` / `--cfg 7`：采样步数 / 引导强度（sd-webui / comfyui）
- `--model <模型名>`：指定模型（vertex / pollinations / siliconflow）
- `--translator deepseek|gemini|off|auto`：提示词翻译官（默认跟随配置，deepseek 为默认引擎）
- `--auto-fix` / `--no-auto-fix`：开启/关闭自动看图改图（默认跟随配置，开启时生成后自动视觉检查并修正一次）
- `--json`：机器可读输出，读取 `path`、`seed`、`backend` 字段

**提示词翻译官（推荐保持开启）：** 用户给出中文需求时，脚本会先让翻译官按固定模板
（主体→环境→光影→风格→构图→画面文字）改写成结构化生图提示词，再交给图像模型，
能明显减少漏画、画错细节。默认引擎 DeepSeek；若 DeepSeek 通道异常会自动改用本地
Gemini 文本模型（`--json` 输出里 `translator.engine_used` 字段可看到实际使用的引擎）。
可用 `translate` 命令单独查看翻译结果：

```text
python "<脚本路径>" translate "<用户需求>" [--engine deepseek|gemini|off] [--json]
```

需要翻译官对照生成的图片自查并自动修正时，生成命令加 `--auto-fix`（或保持配置里的
自动看图改图开启）；脚本会调用视觉插件的 `vision_bridge.py` 检查图片。默认「局部小修」模式：
把当前图片原样喂回图像模型，只按最小改动指令修正发现的问题（其余内容一律保持原样），
并自动复查——修正版更差时自动退回上一版；`--fix-mode redraw` 可切回旧的整图重画方式，
背景小细节在重画模式下只提示、不重画。

**图生图（编辑已有图片 / 换风格 / 局部修改）：** 用户给出参考图片时，加 `--image <图片路径或链接>` 启用图生图：

```text
python "<脚本路径>" generate "把这张图改成赛博朋克风格" --image D:\图片\原图.png [--denoise 0.6] [--size 1024x1024]
```

- `--denoise`：去噪强度 0~1（默认 0.6），数值越高对原图的改动越大。
- 省略 `--size` 时自动保持原图尺寸。
- 支持的后端：`vertex`（本地代理 `/images/edits`）、`sd-webui`（`/sdapi/v1/img2img`）、`comfyui`（自动上传图片后生成）。pollinations / siliconflow 不支持，传 `--image` 会直接报错提示。
- `--denoise` 对 sd-webui / comfyui 生效；vertex 后端由 Gemini 自行控制编辑强度。
- 如果用户想"改图"但没说怎么改，帮用户把需求整理成明确的修改指令（保留什么、改什么、换成什么风格）。

需要机器可读结果时加 `--json`。生成失败时脚本会返回非零退出码并给出 `error` 说明。

### 5. 使用结果组织交付

- 默认输出到当前工作目录：`deepseek-imagegen_<时间戳>_<提示词摘要>.<ext>`。
- 每次生成成功后，脚本会自动在 `mirror_dir`（默认 `C:\Users\yjq\Pictures\codex`）保留一份副本；复制失败不影响主文件，`--json` 输出中的 `mirror_path` 字段可查看副本位置。
- 如果图片属于当前项目，务必用 `--out` 保存到项目内（例如 `outputs/imagegen/xxx.png`）并在最终回答中给出保存路径。
- 如果只是预览/头脑风暴，可以保存在临时目录，并在回答里内联展示图片。
- 不要凭空编造"已生成"的图片；脚本报错或没有输出文件时，如实告知用户并给出诊断建议。

### 6. 配置后端（修改默认后端 / 首次使用 siliconflow 时）

- 配置位置：`~/.deepseek-imagegen/config.json`（即 `C:\Users\<用户名>\.deepseek-imagegen\config.json`），推荐用设置页面修改。
- 参考模板：`scripts/config.example.json`。API Key 只保存在本机，绝不写入仓库。
- vertex 后端默认自动发现：目录 `vertex.dir`（默认 `C:\Users\yjq\Documents\Codex\2026-07-31\new-chat\outputs\vertex-proxy\dist`）→ 读取端口、第一个 Key、模型列表，并自动选择最佳图像模型；也可在配置里用 `vertex.base_url / api_key / model` 手动覆盖。

### 7. 故障处理

- 脚本报错时，先运行 `python "<脚本路径>" doctor` 查看各后端连通性诊断。
- 提示 vertex 代理连不上：先确认 `vertex-proxy.exe` 已启动（`dist\启动.bat`），再运行 doctor 查看端口/密钥是否读取成功。
- 提示未配置 siliconflow 密钥：需要用户在 `config.json` 里填写 `siliconflow.api_key`（在 https://cloud.siliconflow.cn 申请）。
- 提示本地 SD / ComfyUI 连不上：需要用户先启动本地服务；SD WebUI 需带 `--api` 参数启动。
- 查看后端可用模型：`python "<脚本路径>" list-models`（vertex 会列出图像模型并标注最佳）。
- 修改配置后无需重启 Codex，脚本每次运行都会重新读取配置。

## 注意事项

- 提示词质量决定出图质量：把用户需求整理成"主体 + 场景 + 风格 + 构图 + 光线 + 色彩 + 禁止项"的结构化描述。
- 隐私：云端后端（pollinations / siliconflow）会收到提示词内容；敏感内容建议使用本地 vertex / sd-webui / comfyui。
- 尺寸默认 1024x1024；不同后端支持的分辨率不同，出错时按后端限制调整。
- 不要向用户索要 API Key 明文贴到聊天里；引导用户把 Key 写入本地配置文件或使用设置页面。
