# DeepSeek ImageGen 插件

为 Codex 中的 DeepSeek 等纯文本模型提供**图像生成**能力的桥接插件。DeepSeek 负责理解需求、撰写提示词，图像后端负责渲染，最终交付 PNG 图片。

## 快速开始（默认免费，零配置）

在 Codex 对话里直接说：

```text
画一张赛博朋克风格的东京夜景，霓虹灯，雨天，电影感构图
```

DeepSeek 会调用本插件的桥接脚本，默认走免费免密钥的 Pollinations 后端，把图片保存到当前目录并展示给你。

## 命令参考

脚本位置：`plugins/deepseek-imagegen/scripts/image_gen.py`

```bash
# 生成图片
python image_gen.py generate "一只戴着宇航员头盔的柴犬，写实风格" --size 1024x1024 --seed 42

# 指定后端与负面提示词
python image_gen.py generate "山水画风格海报" --backend sd-webui --negative "文字, 水印, 低质量" --out poster.png

# 诊断各后端连通性
python image_gen.py doctor

# 查看当前生效配置（密钥自动打码）
python image_gen.py config

# 查看本地后端的可用模型/checkpoint
python image_gen.py list-models
```

`generate` 常用参数：

| 参数 | 说明 |
| --- | --- |
| `--backend` | `pollinations`（默认）/ `siliconflow` / `sd-webui` / `comfyui` |
| `--out` | 输出文件路径 |
| `--size` | 分辨率，如 `1024x1024`、`1536x1024` |
| `--seed` | 随机种子，同一提示词+种子可复现 |
| `--negative` | 负面提示词（sd-webui / comfyui） |
| `--steps` / `--cfg` | 采样步数 / 引导强度（sd-webui / comfyui） |
| `--model` | 指定模型（pollinations / siliconflow） |
| `--json` | 机器可读输出 |

## 后端配置

配置文件：`~/.deepseek-imagegen/config.json`（参考 `scripts/config.example.json`，API Key 只保存在本机）。

### Pollinations（默认，免费免密钥）

无需任何配置。可选的 `model` 字段留空即用默认模型。

### SiliconFlow（国内直连，FLUX 系列）

在 [SiliconFlow 控制台](https://cloud.siliconflow.cn) 申请 API Key 后：

```json
{
  "siliconflow": {
    "api_key": "sk-你的密钥",
    "model": "black-forest-labs/FLUX.1-schnell"
  }
}
```

`base_url` 默认 `https://api.siliconflow.cn/v1/images/generations`，兼容任何 OpenAI 风格图像接口，可改成其他提供方。

### 本地 Stable Diffusion WebUI / Forge

1. 启动 WebUI 时带上 API 参数：`webui-user.bat --api`（默认监听 `http://127.0.0.1:7860`）。
2. 无需修改配置即可使用；采样器、步数、CFG 可在 `config.json` 的 `sd_webui` 里调整。

### 本地 ComfyUI

1. 正常启动 ComfyUI（默认监听 `http://127.0.0.1:8188`）。
2. 在 `config.json` 的 `comfyui.checkpoint` 里填一个已安装的 checkpoint 名称（运行 `list-models` 可查看）。
3. 未填写时脚本会自动选择第一个可用 checkpoint。

## 隐私说明

- pollinations / siliconflow 等云端后端会收到你的提示词内容，敏感内容建议用本地 sd-webui / comfyui。
- API Key 只存在 `~/.deepseek-imagegen/config.json`，该路径已在仓库 `.gitignore` 中排除，不会上传。

## 故障排查

- 先运行 `python image_gen.py doctor` 查看各后端诊断结果。
- 生成失败时看脚本输出的 `error` 字段；SD WebUI 需要带 `--api` 启动，ComfyUI 需要 checkpoint 存在。
- 任何情况下都不要编造"已生成"的图片，如实汇报错误即可。
