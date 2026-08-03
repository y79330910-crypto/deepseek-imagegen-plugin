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

# 实测代理是否遵守尺寸参数（生成小图核对，结果缓存进配置）
python image_gen.py doctor --size-probe

# 查看当前生效配置（密钥自动打码）与可用模型
python image_gen.py config
python image_gen.py list-models

# 洛天依 V4 公式服全身演唱会（竖版 + 自动看图修正）
python image_gen.py generate "洛天依V4公式服演唱会全身" --composition full-body --auto-fix --character 洛天依-V4公式服
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
| `--composition` | 构图预设：`full-body` 全身竖版 / `half-body` 半身 / `portrait` 特写 / `landscape` 横版（v0.7） |
| `--size-policy` | 尺寸不符策略：`auto` 自动兜底（默认）/ `strict` 严格报错 / `warn` 仅警告 |
| `--max-fix-rounds` | 自动修复最大轮数（默认跟随配置，v0.7 起默认 2） |
| `--fallback-backends` | 主后端失败时的降级顺序，如 `vertex,pollinations` |
| `--character` | 角色卡名称（v2）：从本机 MySQL 读取已核实设定并自动注入 |
| `--expand` | 外扩画布（v2，仅 vertex）：把参考图扩到目标尺寸，如 `--expand 768x1408` |
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

> **v0.7 实测提示**：本地代理的文生图接口目前只接受 `1024x1536` 这一个尺寸字符串，且输出固定为 `1408x768`（尺寸参数被忽略）。插件已内置「画布优先」兜底：需要竖版/方形/指定画幅时，先用 Pillow 建目标画幅的空白画布，再走图生图让模型在画布上作画（实测 `768x1408` / `1408x768` / `1024x1024` 画布均原样返回）。`doctor --size-probe` 可随时重新实测并缓存结论。

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

## 提示词翻译官（v0.4.0 新增）

用户的中文需求会先由「翻译官」按固定模板改写成结构化生图提示词（主体→环境→光影→风格→构图→画面文字），再交给图像模型，能明显减少漏画、画错细节。三种引擎：

- **DeepSeek（默认）**：中文理解好；地址/密钥留空时自动读取 Codex 配置（`~/.codex/config.toml` 的 `[model_providers.deepseek]`），密钥不会写入本插件配置。
- **Gemini**：走本地 Vertex Proxy 的最佳文本模型（自动挑选，如 `gemini-3.6-flash`），稳定支持中文。
- **off**：关闭翻译，原文直传。

若 DeepSeek 通道异常（例如返回问号/空回复），脚本会自动改用本地 Gemini，出图不会中断；`--json` 输出里的 `translator.engine_used` 与 `fallback` 字段可看到实际使用的引擎。

单独查看翻译结果：

```text
python image_gen.py translate "一只戴宇航员头盔的柴犬，火星背景，写实" [--engine deepseek|gemini|off]
```

### 自动看图改图（--auto-fix，v0.6.0 起默认关闭）

**默认关闭**：生成后不做自动看图修正，第一版就是交付结果；想用的时候由你决定——在设置页「试生成」预览区点「🔍 看图修正当前图」，或命令行加 `--auto-fix`。

**v0.7 重构**：问题按「构图类 / 细节类」分类处理——

- **构图类**（脚被裁掉、半身、头顶没留白、Q版比例等）：不再写“保持整体布局”，直接升级为带反馈的重绘（或画布优先），并逐项核对构图清单；
- **细节类**（发色、服装、背景小物件等）：继续用局部小修，最小改动、其余保持原样；
- **修复轮尺寸校验**：编辑输出尺寸与输入不一致直接判失败并退回；
- **保留最佳升级**：不再只看问题条数，构图清单通过项、尺寸匹配都参与加权评分。

- **分级检查**：视觉检查把问题分成"人物级"与"背景细节"两类。局部小修模式下两类都会修（改动成本低）；整图重画模式下背景细节只提示、不重画。
- **保留最佳**：修正版生成后会复查一次，如果比原图更差（例如引入了新的人物错误），自动退回上一版，结果里的 `auto_fix.reverted` 会标记为 true。
- 可配置项：`auto_fix.max_rounds`（默认 2）、`auto_fix.edit_redraw_threshold`（构图问题达到几条就升级重绘，默认 1）、`auto_fix.check_size`（是否严格校验尺寸，默认开）；向后兼容 `translator.fix_mode` / `translator.fix_keep_best`，也可用 `--fix-mode` / `--no-keep-best` / `--max-fix-rounds` 临时指定；设置页有对应开关。

### 构图预设（v0.7 新增）

`--composition full-body` 会把画幅、取景规则与视觉检查清单一起锁定：

| 预设 | 默认画幅 | 检查清单（视觉模型逐项核对） |
| --- | --- | --- |
| `full-body` | 768x1408 竖版 | 双脚完整入画 / 头顶留白 / 全身从头到脚完整 / 非Q版人体比例 |
| `half-body` | 1024x1024 | 腰部以上完整入画 / 头顶留白 / 非Q版 |
| `portrait` | 1024x1024 | 面部完整清晰 / 头顶留白 / 面部特写为主 |
| `landscape` | 1408x768 横版 | 主体完整入画 / 横向广角 / 背景层次清晰 |

预设可在设置页或 `config.json` 的 `composition.presets` 里改；未指定 `--size` 时自动采用预设画幅。

### 真实尺寸校验（v0.7 新增）

生成后脚本会读取文件头里的真实宽高，不再拿“请求尺寸”冒充结果：

- `--json` 输出新增 `actual_size`（真实尺寸）与 `size_check`（请求/实际/是否匹配/是否用了画布优先）；
- 尺寸不符时按 `size_policy.mode` 处理：`auto` 重试 → 画布优先兜底 → 警告保留（默认）；`strict` 直接报错；`warn` 只警告；
- `doctor --size-probe` 实测后端对尺寸的遵守情况，结果缓存进 `size_policy.probe_cache`。

### 健壮性（v0.7 新增）

- 上游返回 HTTP 200 + 空 `data`（限流/吞错）时自动重试（`robustness.empty_data_retries`，默认 2 次），重试失败给出中文提示；
- 主后端失败自动按 `robustness.fallback_backends` 顺序降级（如 `vertex,pollinations`），`--fallback-backends` 可临时指定；
- 默认超时提高到 240 秒（`robustness.timeout`）；
- Windows PowerShell 下中文输出不再乱码（脚本自动把控制台切到 UTF-8）。

### 角色卡（v2，本机 MySQL）

把已核实的角色设定存进本机 MySQL，出图时自动注入，不用每次重写：

```bash
python image_gen.py character init            # 建表 + 写入默认角色卡（洛天依 V4 公式服，FactGuard 已核实）
python image_gen.py character list            # 列出角色卡
python image_gen.py character add --name 初音未来 --version V4 --hair-color 青色 --eye-color 青色 --outfit "制服" --verified
python image_gen.py generate "洛天依演唱会" --character 洛天依-V4公式服
```

数据只存本机 MySQL（默认库 `deepseek_imagegen`，可在设置页改），**不会**同步到 GitHub。生成时会注入灰发绿瞳、蓝白公式服、腰部中国结等已核实设定，并禁止 Q 版/改色等。

### 外扩画布（v2，仅 vertex）

把已经生成的半身图就地扩成全身竖版：

```bash
python image_gen.py generate "把这张图扩展成全身演唱会场景，保持人物设定" --image 半身图.png --expand 768x1408
```

实现方式：Pillow 把原图放在竖版画布底部，走 `/images/edits` 让模型补全背景与下半身（无蒙版，模型可能重绘局部，属于尽力而为方案）。SD WebUI / ComfyUI 的外扩方案见社区节点（如 tuki0918/comfyui-image-expand-nodes），后续版本接入。

### 交付规范化（v2）

开启自动修复并指定 `--out xxx.png` 时，最终交付文件固定命名为 `xxx_final.png`，中间轮次的 `-fix1`/`-fix2` 版本自动清理，镜像副本同步为最终文件，避免拿错版本。

结果里的 `auto_fix.rounds`、`auto_fix.fix_mode`、`auto_fix.reverted` 与 `auto_fix.history`（每一轮的问题、修正指令、判定结果）可查看全过程。关闭：`--no-auto-fix` 或设置页关闭开关。

### 提示词词库（v0.6.0 新增）

把 GitHub/网上收集的热门图像提示词分类存进 MySQL，生成时用向量模型检索最相近的几条，作为参考示例喂给提示词翻译官，让第一版就站在成熟提示词的肩膀上。

- 向量模型：默认硅基流动国际版 `Qwen/Qwen3-Embedding-8B`（Embedding）+ `Qwen/Qwen3-Reranker-8B`（Rerank 精排，可选），可在设置页或 `config.json` 修改
- 存储：本机 MySQL（默认库名 `prompt_library`，设置页填写账号密码；Navicat 可直接查看）
- 用法：
  ```bash
  python scripts/prompt_lib.py init                 # 建表
  python scripts/prompt_lib.py import 提示词.json --source 仓库名 --category 插画
  python scripts/prompt_lib.py search "一只可爱的洛天依Q版" --k 8   # 试检索
  python scripts/prompt_lib.py stats                # 词库统计
  ```
- 生成时自动生效：翻译官改写提示词前会检索词库，结果里 `prompt_library.hits` 记录这次参考了哪些条目；`--no-library` 可临时关闭

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

### v0.7.0

- **构图预设**：`--composition full-body / half-body / portrait / landscape`，锁定画幅 + 取景规则 + 视觉检查清单
- **真实尺寸校验**：生成后探测文件头真实尺寸，JSON 新增 `actual_size` / `size_check`，不再用请求尺寸冒充
- **画布优先兜底**：代理文生图不遵守尺寸时，自动用 Pillow 建目标画幅画布走图生图（实测 768x1408 / 1408x768 / 1024x1024 原样返回）
- **自动修复重构**：问题分构图类/细节类；构图问题升级为带反馈重绘（不写“保持整体布局”）；修复轮校验输出尺寸；keep-best 按检查清单加权
- **健壮性**：空 data 自动重试、后端降级（`--fallback-backends`）、超时 240s、中文报错、PowerShell UTF-8 输出
- **doctor --size-probe**：实测后端尺寸行为并缓存进配置
- **设置页**：新增构图/尺寸策略/修复/健壮性/角色卡配置区
- **角色卡（v2 预览）**：本机 MySQL 建表 + 洛天依 V4 公式服默认角色卡，`--character` 自动注入
- **外扩画布（v2 预览）**：`--expand WxH` 把已有图扩成目标画幅
- **交付规范化**：修复后最终文件固定 `xxx_final.png`，中间版本自动清理

### v0.6.2

- 词库新增 `add` 命令：单条入库（自家精品），支持"翻译后提示词 + 原始需求"双向量
- 词库新增 `backup` 命令：一键导出全部提示词为 JSONL 备份
- 检索支持分类过滤（`categories`）与优先分类（`priority_category`，默认自家精品，保证占位）
- 参数精调：初选 50→30、最终参考 8→6；修复中文配置写入乱码问题

### v0.6.1

- 词库导入支持文件内部去重；Rerank 接口异常时自动退回余弦排序，不影响出图

### v0.6.0

- 自动看图改图默认关闭，改由用户生成后手动触发（设置页「看图修正」按钮 / `--auto-fix`）
- 新增提示词词库：MySQL 存储 + SiliconFlow Embedding/Rerank 向量检索，检索结果自动喂给翻译官
- 设置页新增「提示词词库」配置区（Embedding / Rerank / MySQL / 参数）；命令新增 `--library` / `--no-library`
- 新增 `prompt_lib.py` 命令行工具：init / import / search / stats

### v0.5.1

- 保留最佳判定优化：修正版只要有实际改善（人物级错误减少或总问题减少）就保留，不再误退回
- 局部修正指令不再混入"人物问题/背景问题"小标题，指令更干净

### v0.5.0

- 自动看图改图升级为"图生图局部修正"：用原图 + 最小改动指令，不再整图重画，人物一致性大幅提升
- 新增"保留最佳"保护：修正版更差时自动退回上一版
- 视觉检查分级：人物级问题自动修，背景小细节只提示不折腾（整图重画模式）
- 设置页新增"自动改图方式"与"保留最佳"开关；生成命令新增 `--fix-mode` / `--keep-best` / `--no-keep-best`

### v0.4.0

- 新增提示词翻译官：DeepSeek（默认）/ Gemini / 关闭三种引擎，中文需求自动改写为结构化生图提示词
- 新增自动看图改图：生成后调用视觉插件检查缺失细节，自动重写提示词并重试
- 设置页面新增翻译官配置与「先翻译」按钮；生成命令新增 `--translator` / `--auto-fix` 选项

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
