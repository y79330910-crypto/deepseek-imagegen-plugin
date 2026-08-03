---
name: deepseek-imagegen
description: 当用户要求生成图片、插画、海报、图标、照片、概念图、横幅、游戏素材、UI 草图等位图视觉内容，或说"画一张 / 生成一张图片 / 做个图 / 帮我出图"时使用。DeepSeek 是纯文本模型，无法直接生成图片；本技能调用图像生成桥接脚本，把文字需求交给本地 Vertex Proxy（自动选用最佳图像模型）渲染成图片后再交付。Use when the user asks to create, draw, or generate an image, illustration, poster, icon, photo, concept art, banner, game asset, or UI mockup and the output should be a bitmap asset.
---

# DeepSeek ImageGen —— 图像生成桥接（v1.0）

## 这个技能解决什么问题

DeepSeek（deepseek-chat / deepseek-reasoner 等）是纯文本模型，本身不能生成图片。本技能提供一条图像生成桥接通道：由你（DeepSeek）负责理解用户需求、撰写高质量提示词，再调用本插件的桥接脚本把提示词发给本地 Vertex Proxy，拿到图片后保存并交付。图像后端负责"画"，你负责"想"。

## 操作步骤

### 1. 定位桥接脚本

- 脚本位于插件目录下的 `scripts/image_gen.py`（插件目录包含 `.codex-plugin/plugin.json`）。
- 常见路径：`D:\deepseek-imagegen-plugin\plugins\deepseek-imagegen\scripts\image_gen.py`

### 2. 执行生成命令（图片/文件路径含空格时加引号）

```text
python "<脚本路径>" generate "<提示词>" [选项]
```

常用选项：

- `--out <输出文件路径>`：指定输出文件（项目相关图片必须保存到工作区）
- `--size 1024x1024`：分辨率（宽x高）
- `--composition full-body|half-body|portrait|landscape|auto`：构图预设
- `--size-policy strict|auto|warn`：尺寸不符时的处理策略（默认 auto）
- `--seed 12345`：随机种子（复现同一张图）
- `--model <模型名>`：指定模型（默认自动选最佳图像模型）
- `--translator deepseek|gemini|off|auto`：提示词翻译官（默认跟随配置，deepseek 为默认引擎）
- `--library` / `--no-library`：生成时是否使用提示词词库检索（默认跟随配置）
- `--json`：机器可读输出，读取 `path`、`seed`、`size_check`、`translator.engine_used` 等字段

**构图预设：** 需要全身/半身/特写/横版构图时，尽量加 `--composition`。例如：

```text
python "<脚本路径>" generate "少女在湖边公园游玩全身" --composition full-body
```

full-body 预设会自动锁定竖版画幅（768x1408）、在提示词里强制"从头到脚、脚入画、头顶留白、非Q版"。**尺寸如实上报**：脚本会读取生成图的真实尺寸，`--json` 输出里的 `actual_size` / `size_check` 才是真值；若代理不遵守尺寸，会自动用"画布优先"兜底（Pillow 建画布 + 图生图）。

**提示词翻译官（推荐保持开启）：** 用户给出中文需求时，脚本会先让翻译官按固定模板（主体→环境→光影→风格→构图→画面文字）改写成结构化生图提示词，再交给图像模型。默认引擎 DeepSeek；若 DeepSeek 通道异常会自动改用本地 Gemini 文本模型（`--json` 输出里 `translator.engine_used` 可看到实际使用的引擎）。可用 `translate` 命令单独查看翻译结果：

```text
python "<脚本路径>" translate "<用户需求>" [--engine deepseek|gemini|off] [--json]
```

**图生图（编辑已有图片 / 换风格 / 局部修改）：** 用户给出参考图片时，加 `--image <图片路径或链接>`：

```text
python "<脚本路径>" generate "把这张图改成赛博朋克风格" --image D:\图片\原图.png [--size 1024x1024]
```

- 省略 `--size` 时自动保持原图尺寸。
- `--denoise` 去噪强度 0~1（默认 0.6），Gemini 由模型自行控制编辑强度。
- **v1.0 单模型改图流程**：需要"改图"时（如把裙子改成红色），把原图作为参考图，由 Codex 把用户意见整理成明确的编辑指令（保留什么、改什么、换成什么），让图像模型自己看图改图，单轮完成，不再有三模型接力。

### 3. 提示词词库（MySQL + 向量检索）

词库把已收集的提示词分类存入 MySQL，生成时用向量模型检索相近示例喂给翻译官。工具为 `scripts/prompt_lib.py`（薄入口）：

```text
python "<脚本路径>/prompt_lib.py" init
python "<脚本路径>/prompt_lib.py" import 文件.jsonl --category 插画艺术 --source 来源
python "<脚本路径>/prompt_lib.py" search "需求描述" [--k 6]
python "<脚本路径>/prompt_lib.py" stats
python "<脚本路径>/prompt_lib.py" add "提示词正文" [--category 自家精品]
python "<脚本路径>/prompt_lib.py" backup [输出文件]
```

- `search` 只检索 `archived=0` 的活跃词条；归档数据保留可恢复。
- 生成时词库检索正常喂给翻译官（`--json` 输出里的 `prompt_library.hits` 可看命中）。

### 4. 使用结果组织交付

- 默认输出到当前工作目录：`deepseek-imagegen_<时间戳>_<提示词摘要>.<ext>`。
- 每次生成成功后，脚本会自动在 `mirror_dir`（默认 `C:\Users\yjq\Pictures\codex`）保留一份副本；复制失败不影响主文件，`--json` 输出中的 `mirror_path` 字段可查看副本位置。
- 如果图片属于当前项目，务必用 `--out` 保存到项目内（例如 `outputs/imagegen/xxx.png`）并在最终回答中给出保存路径。
- 不要凭空编造"已生成"的图片；脚本报错或没有输出文件时，如实告知用户并给出诊断建议。

### 5. 配置与故障处理

- 配置位置：`~/.deepseek-imagegen/config.json`（即 `C:\Users\<用户名>\.deepseek-imagegen\config.json`），参考模板 `scripts/config.example.json`。API Key 只保存在本机，绝不写入仓库。
- 查看生效配置（密钥打码）：`python "<脚本路径>" config`。
- 查看代理可用模型：`python "<脚本路径>" list-models`。
- 代理未启动时的启动指引：先确认 `vertex-proxy.exe` 已启动（在 `vertex.dir` 指向的目录里双击 `启动.bat`），再运行 `python "<脚本路径>" doctor` 查看端口/密钥/模型是否读取成功。
- 尺寸不对时：`python "<脚本路径>" doctor --size-probe` 实测代理尺寸行为，结果缓存进配置；出图时优先使用 `--composition` 预设 + `--size-policy auto`。
- 修改配置后无需重启 Codex，脚本每次运行都会重新读取配置。

## 注意事项

- 提示词质量决定出图质量：把用户需求整理成"主体 + 场景 + 风格 + 构图 + 光线 + 色彩 + 禁止项"的结构化描述。
- 隐私：只使用本地 Vertex Proxy，提示词与参考图均不上传云端图像服务。
- 尺寸默认 1024x1024；生成失败时脚本返回非零退出码并给出中文 `error` 说明。
- 不要向用户索要 API Key 明文贴到聊天里；引导用户把 Key 写入本地配置文件。
