"""提示词翻译官：DeepSeek 默认 + 本地 Gemini 自动兜底 + off 直传。"""

from __future__ import annotations

import json
import os
import re
from pathlib import Path
from typing import Any, Optional

from .config import APP_NAME, load_config
from .http import GenError, http
from .vertex import discover_vertex, pick_best_text_model


def _read_deepseek_credential_from_codex() -> tuple[str, str]:
    """从环境变量或 ~/.codex/config.toml 读取 DeepSeek 地址与密钥（不回显密钥）。"""
    base_url = os.environ.get("DEEPSEEK_BASE_URL", "").strip()
    api_key = os.environ.get("DEEPSEEK_API_KEY", "").strip()
    codex_cfg = Path.home() / ".codex" / "config.toml"
    if codex_cfg.is_file():
        try:
            text = codex_cfg.read_text(encoding="utf-8")
        except OSError:
            return base_url, api_key
        match = re.search(
            r'\[model_providers\.deepseek\][^\[]*?base_url\s*=\s*"([^"]+)"[^\[]*?'
            r'experimental_bearer_token\s*=\s*"([^"]+)"',
            text,
            re.S,
        )
        if match:
            if not base_url:
                base_url = match.group(1).strip().rstrip("/")
            if not api_key:
                api_key = match.group(2).strip()
    return base_url, api_key


def _chat_text(
    base_url: str,
    api_key: str,
    model: str,
    messages: list[dict[str, Any]],
    max_tokens: int = 4096,
    timeout: int = 120,
) -> str:
    """调用 OpenAI 兼容 /chat/completions；推理模型思考过长时加大上限重试。"""

    def call(tokens: int) -> str:
        payload = {
            "model": model,
            "messages": messages,
            "max_tokens": tokens,
            "temperature": 0.8,
            "stream": False,
        }
        _status, body, _ctype = http(
            f"{base_url.rstrip('/')}/chat/completions",
            method="POST",
            headers={
                "Authorization": f"Bearer {api_key}",
                "Content-Type": "application/json",
                "User-Agent": f"{APP_NAME}/1.0",
            },
            payload=payload,
            timeout=timeout,
        )
        data = json.loads(body.decode("utf-8", errors="replace"))
        try:
            content = data["choices"][0]["message"].get("content")
        except (KeyError, IndexError) as exc:
            raise GenError(f"聊天接口返回异常：{str(data)[:300]}") from exc
        return str(content or "").strip()

    text = call(max_tokens)
    if not text:
        text = call(max_tokens * 2)
    return text


def _deepseek_text(
    base_url: str,
    api_key: str,
    model: str,
    messages: list[dict[str, Any]],
    max_tokens: int = 4096,
) -> str:
    """调用 DeepSeek（优先 /v1/responses，失败回退 /v1/chat/completions）。"""
    system = "\n\n".join(
        str(m.get("content") or "") for m in messages if m.get("role") == "system"
    )
    user = "\n\n".join(
        str(m.get("content") or "") for m in messages if m.get("role") == "user"
    )
    headers = {
        "Authorization": f"Bearer {api_key}",
        "Content-Type": "application/json",
        "User-Agent": f"{APP_NAME}/1.0",
    }
    payload = {
        "model": model,
        "instructions": system,
        "input": [
            {
                "type": "message",
                "role": "user",
                "content": [{"type": "input_text", "text": user}],
            }
        ],
        "max_output_tokens": max_tokens,
    }
    try:
        _status, body, _ctype = http(
            f"{base_url.rstrip('/')}/v1/responses",
            method="POST",
            headers=headers,
            payload=payload,
            timeout=120,
        )
        data = json.loads(body.decode("utf-8", errors="replace"))
        parts: list[str] = []
        for item in data.get("output", []):
            if item.get("type") == "message":
                for chunk in item.get("content", []):
                    if chunk.get("type") == "output_text" and chunk.get("text"):
                        parts.append(str(chunk["text"]))
        if parts:
            return "\n".join(parts).strip()
    except GenError:
        pass

    payload = {
        "model": model,
        "messages": messages,
        "max_tokens": max_tokens,
        "temperature": 0.8,
        "stream": False,
    }
    _status, body, _ctype = http(
        f"{base_url.rstrip('/')}/v1/chat/completions",
        method="POST",
        headers=headers,
        payload=payload,
        timeout=120,
    )
    data = json.loads(body.decode("utf-8", errors="replace"))
    try:
        return str(data["choices"][0]["message"].get("content") or "").strip()
    except (KeyError, IndexError) as exc:
        raise GenError(f"DeepSeek 接口返回异常：{str(data)[:300]}") from exc


def _looks_broken(text: str, had_cjk: bool) -> bool:
    """判断翻译结果是否因通道问题变成“问号/没收到消息”之类的废回复。"""
    if not text or not text.strip():
        return True
    if not had_cjk:
        return False
    low = text.lower()
    markers = (
        "问号", "没有看到", "没看到", "无法理解", "没有收到", "没有显示",
        "question mark", "question marks", "didn't understand", "did not understand",
        "didn't come through", "did not come through", "not display", "not come through",
        "only question", "only got question", "string of question",
    )
    return any(m in low for m in markers)


def build_translator_system(
    lang: str = "zh",
    examples: Optional[list[str]] = None,
    reference_brief: str = "",
) -> str:
    """生成翻译官系统提示词。"""
    if lang == "en":
        base = (
            "You are a senior visual prompt engineer. Rewrite the user's image request "
            "into a well-structured prompt for modern image models such as gemini-3-pro-image.\n\n"
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
            "改写成适合 gemini-3-pro-image 等新一代图像模型的生图提示词。\n\n"
            "硬性要求：\n"
            "1. 用自然连贯的完整句子描述，禁止堆砌孤立关键词；Gemini 图像模型喜欢自然描述段落，讨厌关键词清单。\n"
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
    engine: str = "auto",
    feedback: str = "",
    max_tokens: int = 4096,
    examples: Optional[list[str]] = None,
    reference_brief: str = "",
) -> dict[str, Any]:
    """翻译官：deepseek 默认、gemini 走本地代理、off 直传。"""
    cfg = cfg if cfg is not None else load_config()
    tr = cfg.get("translator") or {}
    if not isinstance(tr, dict):
        tr = {}
    if engine in ("", "auto"):
        engine = str(tr.get("engine") or "deepseek").strip().lower()
    engine = engine.strip().lower()
    if engine in ("off", "none", "direct", "直传"):
        return {
            "ok": True,
            "engine": "off",
            "engine_used": "off",
            "model": "",
            "original": user_text,
            "rewritten": user_text,
            "fallback": False,
        }
    if engine not in ("deepseek", "gemini"):
        engine = "deepseek"

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
    had_cjk = any("\u4e00" <= ch <= "\u9fff" for ch in user_text)

    if engine == "deepseek":
        ds = tr.get("deepseek") or {}
        if not isinstance(ds, dict):
            ds = {}
        base_url = str(ds.get("base_url") or "").strip().rstrip("/")
        api_key = str(ds.get("api_key") or "").strip()
        model = str(ds.get("model") or "deepseek-v4-flash").strip() or "deepseek-v4-flash"
        if not base_url or not api_key:
            codex_base, codex_key = _read_deepseek_credential_from_codex()
            base_url = base_url or codex_base
            api_key = api_key or codex_key
        if not base_url or not api_key:
            raise GenError(
                "翻译官(deepseek)未配置地址/密钥：请在 config.json 填写，或改用 gemini 引擎。"
            )
        fallback_reason = ""
        try:
            text = _deepseek_text(base_url, api_key, model, messages, max_tokens)
        except GenError as exc:
            text = ""
            fallback_reason = str(exc)
        if _looks_broken(text, had_cjk):
            info = discover_vertex(cfg)
            gm_model = str((tr.get("gemini") or {}).get("model") or "").strip()
            if not gm_model:
                gm_model = pick_best_text_model(info["models"])
            if not gm_model:
                raise GenError("DeepSeek 通道异常且未找到可用的 Gemini 文本模型，请检查配置。")
            gm_text = _chat_text(info["base_url"], info["api_key"], gm_model, messages, max_tokens)
            return {
                "ok": True,
                "engine": "deepseek",
                "engine_used": "gemini",
                "model": gm_model,
                "original": user_text,
                "rewritten": gm_text,
                "fallback": True,
                "fallback_reason": fallback_reason or "DeepSeek 通道未返回有效中文回复，已自动改用本地 Gemini",
            }
        return {
            "ok": True,
            "engine": "deepseek",
            "engine_used": "deepseek",
            "model": model,
            "original": user_text,
            "rewritten": text,
            "fallback": False,
        }

    info = discover_vertex(cfg)
    gm_model = str((tr.get("gemini") or {}).get("model") or "").strip()
    if not gm_model:
        gm_model = pick_best_text_model(info["models"])
    if not gm_model:
        raise GenError("未找到可用的 Gemini 文本模型，请检查 Vertex Proxy 模型列表。")
    text = _chat_text(info["base_url"], info["api_key"], gm_model, messages, max_tokens)
    return {
        "ok": True,
        "engine": "gemini",
        "engine_used": "gemini",
        "model": gm_model,
        "original": user_text,
        "rewritten": text,
        "fallback": False,
    }
