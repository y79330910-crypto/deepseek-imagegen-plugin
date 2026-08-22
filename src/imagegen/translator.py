"""提示词翻译官：单一 OpenAI-Compatible 提示词上游（Chat 优先 / Responses fallback）。

模块本身只负责真正调用 translator upstream；是否启用由 Generation Engine
根据 request.translator（auto/off）与 config.translator.enabled 决定。
"""

from __future__ import annotations

from typing import Any, Optional

from .config import load_config
from .openai_client import OpenAIClient
from .prompt_case import (
    QueryAnalysis,
    PromptCase,
    format_prompt_case,
    normalize_prompt_mode,
)


def build_translator_system(
    lang: str = "zh",
    examples: Optional[list[str]] = None,
    reference_brief: str = "",
    prompt_mode: str = "optimized",
    query_analysis: Optional[QueryAnalysis] = None,
    cases: Optional[list[PromptCase | dict[str, Any]]] = None,
) -> str:
    """生成翻译官系统提示词。"""
    prompt_mode = normalize_prompt_mode(prompt_mode)
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
    mode_rules = {
        "conservative": (
            "保守模式：最大程度保持原始需求，只补足必要的视觉描述；尽量不新增环境、剧情、道具或动作。"
        ),
        "optimized": (
            "优化模式（默认）：保持用户需求和主题，主动完善姿势、构图、镜头、空间层次、光影、材质、"
            "环境互动、色彩与氛围，但不得改变明确内容。"
        ),
        "creative": (
            "创新模式：保持所有 Locked 内容不变，只能在 Open 字段进行更大胆但合理的视觉设计，"
            "可加强动态姿态、特殊构图、镜头语言、前景遮挡、空间关系、戏剧性光影和配色。"
        ),
    }
    base += "\n\n提示词优化强度：" + mode_rules[prompt_mode]
    base += (
        "\n优先级：用户硬约束 / 参考图 preserve > 优化模式策略 > 案例 > 你的自由发挥。"
        "案例用于学习视觉设计和 Prompt 写法，案例不是当前画面的素材来源。"
        "禁止迁移案例人物、服装、地点、颜色、道具、文字、事件或其他独有元素；"
        "当前用户需求永远优先于案例。"
    )
    if query_analysis is not None:
        locked = query_analysis.locked or {}
        locked_fields = [key for key, value in locked.items() if value]
        if locked_fields:
            base += (
                "\n\n本次 Query Parser 标记为 Locked 的字段（必须原样保持，不得改写）："
                + "、".join(locked_fields)
                + "。其余字段为 Open；创新只能发生在 Open 字段。"
            )
        if query_analysis.facets.to_dict():
            base += "\n当前需求结构化字段：\n" + "\n".join(
                f"{key}：{value}" for key, value in query_analysis.facets.to_dict().items() if value
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
    if cases:
        rendered = []
        for index, case in enumerate(cases, 1):
            try:
                rendered.append(format_prompt_case(case, index=index, max_chars=2800))
            except Exception:
                continue
        if rendered:
            base += (
                "\n\n结构化 Prompt Case（只学习表达方式，不得搬运案例独有内容）：\n"
                + "\n\n".join(rendered)
            )
    return base


def translate_prompt(
    user_text: str,
    cfg: Optional[dict[str, Any]] = None,
    feedback: str = "",
    max_tokens: int = 4096,
    examples: Optional[list[str]] = None,
    reference_brief: str = "",
    prompt_mode: str = "optimized",
    query_analysis: Optional[QueryAnalysis] = None,
    cases: Optional[list[PromptCase | dict[str, Any]]] = None,
) -> dict[str, Any]:
    """翻译官：config.translator.enabled 时调用统一提示词上游，否则直传原文。"""
    cfg = cfg if cfg is not None else load_config()
    prompt_mode = normalize_prompt_mode(prompt_mode)
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
            "prompt_mode": prompt_mode,
        }

    lang = str(tr.get("output_lang") or "zh").lower()
    system = build_translator_system(
        lang,
        examples,
        reference_brief,
        prompt_mode=prompt_mode,
        query_analysis=query_analysis,
        cases=cases,
    )
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
        "prompt_mode": prompt_mode,
    }
