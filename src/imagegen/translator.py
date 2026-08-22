"""提示词翻译官：单一 OpenAI-Compatible 提示词上游（Chat 优先 / Responses fallback）。

模块本身只负责真正调用 translator upstream；是否启用由 Generation Engine
根据 request.translator（auto/off）与 config.translator.enabled 决定。
"""

from __future__ import annotations

from typing import Any, Optional

from .config import load_config
from .openai_client import OpenAIClient


def build_translator_system(
    lang: str = "zh",
    examples: Optional[list[str]] = None,
    reference_brief: str = "",
) -> str:
    """生成翻译官系统提示词。"""
    if lang == "en":
        base = (
            "You are a senior visual prompt engineer. Rewrite the user's image request "
            "into a well-structured prompt for modern image models.\n\n"
            "Hard rules:\n"
            "1. Use natural, descriptive full sentences. Never dump isolated keywords.\n"
            "2. Cover, in order: subject -> environment -> lighting -> style/medium -> "
            "composition -> on-image text if any (exact text in quotes, at most 2-3 phrases).\n"
            "3. Keep every detail the user mentioned; you may add reasonable details.\n"
            "4. Keep the prompt between 80 and 250 words.\n"
            "5. On-image text should be English and placed in quotes.\n"
            "6. Output only the prompt itself: no explanations, no Markdown, no surrounding quotes."
        )
    else:
        base = (
            "你是一位精通图像生成提示词的资深视觉设计师。请把用户用中文描述的画面需求，"
            "改写成适合新一代图像模型的生图提示词。\n\n"
            "硬性要求：\n"
            "1. 用自然连贯的完整句子描述，禁止堆砌孤立关键词；图像模型喜欢自然描述段落，讨厌关键词清单。\n"
            "2. 按顺序覆盖：主体（身份、外貌、服装、姿态、表情）→ 环境背景（地点、建筑、天气、景深层次）→ "
            "光影（光源、颜色、冷暖对比、轮廓光）→ 风格媒介（日系动漫插画、厚涂、水彩、写实摄影等）→ "
            "构图（画幅比例、机位、居中/三分法、前景中景背景）→ 画面文字（如有，用英文引号标出，"
            "例如 \"Merry Xmas ♡\"，并说明位置与字体风格，最多 2-3 条）。\n"
            "3. 用户提到的每一个细节都必须保留，不许遗漏；可以补充合理细节增强画面完成度。\n"
            "4. 提示词长度控制在 150-500 字之间；内容复杂时宁长勿短。\n"
            "5. 画面中的文字优先使用英文并放进引号。\n"
            "6. 只输出提示词正文：不要解释、不要 Markdown、不要编号列表、不要用引号包裹全文。"
        )
    if reference_brief:
        base += (
            "\n\n参考图模式硬性规则（本次有参考图时强制）：\n"
            "1. 必须按三段式结构输出，保持段在最前：第1段·保持 → 第2段·改变 → 第3段·场景。\n"
            "2. 第1段必须逐项点名身份锚点清单中的每一项（清单里有就全部写出，"
            "禁止只写“保持所有特征”这类笼统话）；同时明确“不保留参考图中的姿势、"
            "面部朝向、角度、构图与背景”。\n"
            "3. 第2段必须正向表达“参考图只提供身份与外观，姿势、表情、角度、构图"
            "全部全新创作”；用户要求移除的元素写成“不作为保留项”，"
            "不要用大段“不要X”堆砌；若用户没有指定新姿态，"
            "必须具体编一个自然的新姿态（例如重心放在一条腿上、微微侧身、侧头看向某处）。\n"
            "4. 第3段场景全新创作：只描述新环境、光线与构图；"
            "不得沿用参考图的背景、棚拍光或构图。\n"
        )
    if examples:
        base += (
            "\n\n参考示例：以下是来自已验证提示词库的优秀提示词（可能中英文混合）。"
            "请借鉴它们的结构、细节密度和用词风格，但不要照抄原文；"
            "把其中适用的写法自然融入你为用户需求撰写的提示词：\n"
            + "\n".join(f"- {str(ex)[:600]}" for ex in examples if str(ex).strip())
        )
    return base


def translate_prompt(
    user_text: str,
    cfg: Optional[dict[str, Any]] = None,
    feedback: str = "",
    max_tokens: int = 4096,
    examples: Optional[list[str]] = None,
    reference_brief: str = "",
) -> dict[str, Any]:
    """翻译官：config.translator.enabled 时调用统一提示词上游，否则直传原文。"""
    cfg = cfg if cfg is not None else load_config()
    tr = cfg.get("translator") or {}
    if not isinstance(tr, dict):
        tr = {}
    enabled = bool(tr.get("enabled", True))
    if not enabled:
        return {
            "ok": True,
            "model": "",
            "original": user_text,
            "rewritten": user_text,
        }

    lang = str(tr.get("output_lang") or "zh").lower()
    system = build_translator_system(lang, examples, reference_brief)
    user_msg = str(user_text).strip()
    if reference_brief and str(reference_brief).strip():
        user_msg += "\n\n【参考图简报】\n" + str(reference_brief).strip()
    if feedback and str(feedback).strip():
        user_msg += "\n\n【上次生成后发现的问题，请在重写时重点修正】\n" + str(feedback).strip()
    messages = [
        {"role": "system", "content": system},
        {"role": "user", "content": user_msg},
    ]
    model = str(tr.get("model") or "").strip()
    client = OpenAIClient(
        str(tr.get("base_url") or "").strip(),
        str(tr.get("api_key") or "").strip(),
    )
    text = client.generate_text(messages, model, max_tokens=max_tokens)
    return {
        "ok": True,
        "model": model,
        "original": user_text,
        "rewritten": text,
    }
