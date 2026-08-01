# DeepSeek ImageGen 插件

为 Codex 中的 DeepSeek 等纯文本模型提供**图像生成**能力的桥接插件。DeepSeek 负责理解需求、撰写提示词，图像后端负责渲染，最终交付图片。**默认绑定本地 Vertex Proxy**：自动读取它的端口、API Key 与模型列表，并选用其中最好的图像模型（如 `gemini-3-pro-image`）。

## 快速开始（默认本地代理，零配置）

确认 `vertex-proxy.exe` 已启动（`dist\启动.bat`，默认端口 2156），然后在 Codex 对话里直接说：

```text
画一张赛博朋克风格的东京夜景，霓虹灯，雨天，电影感构图
```

插件会通过本地代理用最佳图像模型生成图片，保存到当前目录并展示。代理未运行时可用免费后端兜底（`--backend pollinations`）。

## 可视化设置页面

```bash
python image_gen.py webui
```

默认地址 http://127.0.0.1:8766，支持：

- 一键从 Vertex Proxy 导入端口、密钥和模型列表，自动选中最佳图像模型
- 修改默认后端与各后端参数（vertex / pollinations / siliconflow / sd-webui / comfyui）
- 测试后端连通性、试生成一张小图、运行 doctor 诊断
- 试生成结果直接在页面内预览（支持文生图 / 图生图）
- 保存到 `~/.deepseek-imagegen/config.json`

## 命令参考

脚本位置：`plugins/deepseek-imagegen/scripts/image_gen.py`

```bash
# 生成图片（默认后端=vertex）
python image_gen.py generate "一只戴着宇航员头盔的柴犬，写实风格" --size 1024x1024 --seed 42

# 指定后端与负面提示词
python image_gen.py generate "山水画风格海报" --backend sd-webui --negative "文字, 水印, 低质量" --out poster.png

# 图生图：以一张图片为底图进行编辑 / 换风格（vertex / sd-webui / comfyui）
python image_gen.py generate "把这只柴犬换成赛博朋克风格" --image D:\图片\dog.png --denoise 0.6
python image_gen.py generate "保持构图，改成水彩画风格" --backend sd-webui --image sketch.jpg --size 1024x1024

# 诊断各后端连通性
python image_gen.py doctor

# 查看当前生效配置（密钥自动打码）与可用模型
python image_gen.py config
python image_gen.py list-models
```

`generate` 常用参数：

| 参数 | 说明 |
| --- | --- |
| `--backend` | `vertex`（默认）/ `pollinations` / `siliconflow` / `sd-webui` / `comfyui` |
| `--out` | 输出文件路径或目录 |
| `--size` | 分辨率，如 `1024x1024`、`1536x1024` |
| `--seed` | 随机种子，同一提示词+种子可复现 |
| `--negative` | 负面提示词（sd-webui / comfyui） |
| `--steps` / `--cfg` | 采样步数 / 引导强度（sd-webui / comfyui） |
| `--model` | 指定模型（vertex / pollinations / siliconflow） |
| `--image` | 参考图片路径或 http(s) 链接，启用图生图（vertex / sd-webui / comfyui） |
| `--denoise` | 去噪强度 0~1，默认 0.6（图生图；数值越高改动越大） |
| `--json` | 机器可读输出 |

图生图（`--image`）说明：不传 `--size` 时自动保持原图尺寸；三个后端均支持——
vertex 走本地代理的 `/images/edits` 编辑接口，SD WebUI 走 `/sdapi/v1/img2img`，ComfyUI 自动上传图片后以 VaeEncode + KSampler 生成。pollinations / siliconflow 暂不支持图生图，传入 `--image` 会给出明确提示。
`--denoise` 主要作用于 sd-webui / comfyui；vertex 后端由 Gemini 根据提示词自行控制编辑强度，`--denoise` 仅作记录不参与调用。

## 后端配置

配置文件：`~/.deepseek-imagegen/config.json`（参考 `scripts/config.example.json`，API Key 只保存在本机）。

### Vertex Proxy（默认，本地代理）

无需手动填写端口和密钥：插件自动读取 `vertex.dir`（默认 `C:\Users\yjq\Documents\Codex\2026-07-31\new-chat\outputs\vertex-proxy\dist`）下的：

- `config\config.json` → 端口（`port_api`，默认 2156）
- `config\api_keys.txt` → 第一个 API Key
- `config\models.json` → 模型列表，自动选出**最佳图像模型**（非预览、pro 优先、版本更高）

也可以在 `config.json` 里用 `vertex.base_url / vertex.api_key / vertex.model` 手动覆盖。需要代理先启动（`dist\启动.bat`）。
图生图时走代理的 `/images/edits` 接口（模型不变）。

### Pollinations（免费免密钥）

无需任何配置，可作兜底。可选的 `model` 字段留空即用默认模型。

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

## 自动副本

每次生成成功后，脚本会自动在 `mirror_dir`（默认 `C:\Users\yjq\Pictures\codex`）保留一份副本，方便集中管理；复制失败不影响主文件。可在 `config.json` 或设置页面里修改 `mirror_dir`（留空=不复制）。`--json` 输出的 `mirror_path` 字段显示副本位置。

### 本地 Stable Diffusion WebUI / Forge

1. 启动 WebUI 时带上 API 参数：`webui-user.bat --api`（默认监听 `http://127.0.0.1:7860`）。
2. 无需修改配置即可使用；采样器、步数、CFG 可在 `config.json` 的 `sd_webui` 里调整。
3. 图生图默认去噪强度 0.6，可在 `sd_webui.denoising_strength` 调整（或用 `--denoise` 临时指定）。

### 本地 ComfyUI

1. 正常启动 ComfyUI（默认监听 `http://127.0.0.1:8188`）。
2. 在 `config.json` 的 `comfyui.checkpoint` 里填一个已安装的 checkpoint 名称（运行 `list-models` 可查看）。
3. 未填写时脚本会自动选择第一个可用 checkpoint。
4. 图生图会自动把参考图上传到 ComfyUI 再生成，默认去噪强度 0.6（`comfyui.denoise` 或 `--denoise`）。

## 更新日志

### v0.3.4

- 界面改为深色毛玻璃风格（参考 Vertex Proxy 控制台）：背景壁纸清晰可见且不刺眼
- 壁纸降低亮度/饱和度、隐去左下角水印、四角暗角柔化；面板加厚模糊以保证文字可读

### v0.3.3

- 背景壁纸更换为用户提供的「哲风壁纸·洛天依」，高斯模糊柔化 + 白色蒙层调整，保证前景可读
- 次要文字与占位符对比度优化

### v0.3.2

- 设置页面改为浅色系洛天依主题，参考 Vertex Proxy 控制台风格：全屏角色背景 + 毛玻璃面板 + 左侧导航布局
- 试生成、图生图预览、诊断卡片等功能保持不变，并支持 `#page-xxx` 锚点直达分区

### v0.3.1

- 设置页面全新界面：洛天依青色主题、横幅装饰、卡片式分区导航
- 试生成支持页面内图片预览与图生图参数（参考图、去噪强度）
- 诊断结果以状态卡片形式展示

### v0.3.0

- 新增图生图（`--image` + `--denoise`）：支持 vertex（`/images/edits`）、sd-webui（`/sdapi/v1/img2img`）、comfyui（上传图片 + VaeEncode）
- 图生图省略 `--size` 时自动沿用原图尺寸
- 设置页面新增两个后端的默认去噪强度配置项

## 隐私说明

- pollinations / siliconflow 等云端后端会收到你的提示词内容，敏感内容建议用本地 vertex / sd-webui / comfyui。
- API Key 只存在 `~/.deepseek-imagegen/config.json`，该路径已在仓库 `.gitignore` 中排除，不会上传。

## 故障排查

- 先运行 `python image_gen.py doctor` 查看各后端诊断结果。
- vertex 报连接失败时，确认 `vertex-proxy.exe` 正在运行（`dist\启动.bat`），或运行 `doctor` 查看端口/密钥是否读取成功。
- 生成失败时看脚本输出的 `error` 字段；SD WebUI 需要带 `--api` 启动，ComfyUI 需要 checkpoint 存在。
- 任何情况下都不要编造"已生成"的图片，如实汇报错误即可。
