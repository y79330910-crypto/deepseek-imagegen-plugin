"""Structured Prompt Case primitives.

This module intentionally contains no database code.  It owns the small,
serializable objects used by the prompt library, the parser fallbacks and the
mode-aware selection policy so that the Engine and MySQL adapter stay thin.
"""

from __future__ import annotations

import json
import logging
import math
import re
from dataclasses import dataclass, field
from typing import Any, Callable, Mapping, Optional, Sequence

from .errors import ValidationError


logger = logging.getLogger(__name__)

PROMPT_CASE_PARSER_VERSION = 1
PROMPT_CASE_EMBEDDING_VERSION = 1
PROMPT_MODES = {"conservative", "optimized", "creative"}

FACET_FIELDS = (
    "subject",
    "appearance",
    "action",
    "interaction",
    "environment",
    "spatial_relationship",
    "composition",
    "camera",
    "lighting",
    "color",
    "mood",
    "style",
    "medium",
    "materials",
    "text",
    "constraints",
)

LOCKABLE_FIELDS = (
    "subject",
    "appearance",
    "action",
    "interaction",
    "environment",
    "spatial_relationship",
    "composition",
    "camera",
    "lighting",
    "color",
    "mood",
    "style",
    "medium",
    "materials",
    "text",
    "constraints",
)

MODE_PROFILES: dict[str, dict[str, Any]] = {
    "conservative": {
        "intent_top_k": 30,
        "visual_top_k": 10,
        "final_k": 3,
        "diversity_strength": 0.15,
    },
    "optimized": {
        "intent_top_k": 30,
        "visual_top_k": 20,
        "final_k": 4,
        "diversity_strength": 0.40,
    },
    "creative": {
        "intent_top_k": 35,
        "visual_top_k": 25,
        "final_k": 4,
        "diversity_strength": 0.70,
    },
}


def normalize_prompt_mode(value: str | None) -> str:
    mode = "optimized" if value is None else str(value).strip().lower()
    if mode not in PROMPT_MODES:
        raise ValidationError(
            f"prompt_mode 只允许 conservative / optimized / creative，当前值：{value!r}。"
        )
    return mode


def mode_profile(value: str | None) -> dict[str, Any]:
    """Return a copy so callers cannot mutate the central strategy table."""
    return dict(MODE_PROFILES[normalize_prompt_mode(value)])


def _empty_facets() -> dict[str, str]:
    return {key: "" for key in FACET_FIELDS}


def _clean_text(value: Any) -> str:
    if not isinstance(value, str):
        return ""
    return " ".join(value.replace("\x00", "").split()).strip()


def _clean_lessons(value: Any) -> list[str]:
    if not isinstance(value, list):
        return []
    result: list[str] = []
    forbidden = (
        "复制该人物", "复制人物", "复制该服装", "复制服装", "复制该地点", "复制地点",
        "复制该颜色", "复制颜色", "复制该具体道具", "复制该道具", "复制道具",
        "copy the character", "copy the outfit", "copy the location", "copy the prop",
    )
    for item in value:
        text = _clean_text(item)
        if text and not any(token.lower() in text.lower() for token in forbidden) and text not in result:
            result.append(text)
    return result


@dataclass
class PromptFacets:
    """The deliberately bounded facet schema used for cases and queries."""

    subject: str = ""
    appearance: str = ""
    action: str = ""
    interaction: str = ""
    environment: str = ""
    spatial_relationship: str = ""
    composition: str = ""
    camera: str = ""
    lighting: str = ""
    color: str = ""
    mood: str = ""
    style: str = ""
    medium: str = ""
    materials: str = ""
    text: str = ""
    constraints: str = ""

    @classmethod
    def from_dict(cls, value: Any) -> "PromptFacets":
        value = value if isinstance(value, Mapping) else {}
        return cls(**{key: _clean_text(value.get(key, "")) for key in FACET_FIELDS})

    def to_dict(self) -> dict[str, str]:
        return {key: _clean_text(getattr(self, key, "")) for key in FACET_FIELDS}


@dataclass
class PromptCase:
    id: int | None = None
    requirement: str = ""
    inferred_requirement: str = ""
    requirement_source: str = "none"
    content: str = ""
    task_type: str = "text_to_image"
    facets: PromptFacets = field(default_factory=PromptFacets)
    intent_text: str = ""
    visual_text: str = ""
    transferable_lessons: list[str] = field(default_factory=list)
    parser_version: int = PROMPT_CASE_PARSER_VERSION

    @classmethod
    def from_dict(cls, value: Mapping[str, Any]) -> "PromptCase":
        requirement = _clean_text(value.get("requirement", ""))
        inferred = _clean_text(value.get("inferred_requirement", ""))
        source = _clean_text(value.get("requirement_source", "none"))
        if source not in {"user", "inferred", "none"}:
            source = "none"
        if requirement:
            source = "user"
        elif inferred:
            source = "inferred"
        return cls(
            id=value.get("id"),
            requirement=requirement,
            inferred_requirement=inferred,
            requirement_source=source,
            content=_clean_text(value.get("content", "")),
            task_type=_clean_text(value.get("task_type", "text_to_image")) or "text_to_image",
            facets=PromptFacets.from_dict(value.get("facets")),
            intent_text=_clean_text(value.get("intent_text", "")),
            visual_text=_clean_text(value.get("visual_text", "")),
            transferable_lessons=_clean_lessons(value.get("transferable_lessons")),
            parser_version=int(value.get("parser_version") or 0),
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "requirement": self.requirement,
            "inferred_requirement": self.inferred_requirement,
            "requirement_source": self.requirement_source,
            "content": self.content,
            "task_type": self.task_type,
            "facets": self.facets.to_dict(),
            "intent_text": self.intent_text,
            "visual_text": self.visual_text,
            "transferable_lessons": list(self.transferable_lessons),
            "parser_version": self.parser_version,
        }


@dataclass
class QueryAnalysis:
    query: str = ""
    facets: PromptFacets = field(default_factory=PromptFacets)
    locked: dict[str, bool] = field(default_factory=lambda: {key: False for key in LOCKABLE_FIELDS})
    parser_version: int = PROMPT_CASE_PARSER_VERSION

    def to_dict(self) -> dict[str, Any]:
        return {
            "query": self.query,
            "facets": self.facets.to_dict(),
            "locked": {key: bool(self.locked.get(key, False)) for key in LOCKABLE_FIELDS},
            "parser_version": self.parser_version,
        }


@dataclass(frozen=True)
class PromptModeProfile:
    name: str
    intent_top_k: int
    visual_top_k: int
    final_k: int
    diversity_strength: float

    @classmethod
    def for_mode(cls, mode: str | None) -> "PromptModeProfile":
        name = normalize_prompt_mode(mode)
        values = MODE_PROFILES[name]
        return cls(name=name, **values)


def _strip_code_fence(text: str) -> str:
    value = str(text or "").strip()
    value = re.sub(r"^\s*```(?:json)?\s*", "", value, flags=re.IGNORECASE)
    value = re.sub(r"\s*```\s*$", "", value)
    return value.strip()


def _decode_json_object(text: str) -> dict[str, Any]:
    raw = _strip_code_fence(text)
    try:
        value = json.loads(raw)
    except json.JSONDecodeError:
        # Some compatible gateways add a short preamble.  Only attempt the
        # bounded object slice; never execute or heuristically rewrite values.
        start, end = raw.find("{"), raw.rfind("}")
        if start < 0 or end <= start:
            raise
        value = json.loads(raw[start : end + 1])
    if not isinstance(value, dict):
        raise ValueError("parser JSON 顶层必须是对象")
    return value


def _warn(message: str, warning: Optional[Callable[[str], None]] = None) -> None:
    logger.warning(message)
    if warning is not None:
        warning(message)


def _parser_config(cfg: Optional[dict[str, Any]]) -> tuple[dict[str, Any], str, bool]:
    config = cfg if isinstance(cfg, dict) else {}
    pl = config.get("prompt_library") if isinstance(config.get("prompt_library"), dict) else {}
    parser_cfg = pl.get("parser") if isinstance(pl.get("parser"), dict) else {}
    enabled_value = parser_cfg.get("enabled", True)
    enabled = (
        enabled_value.strip().lower() not in {"false", "0", "off", "no"}
        if isinstance(enabled_value, str)
        else bool(enabled_value)
    )
    translator = config.get("translator") if isinstance(config.get("translator"), dict) else {}
    model = _clean_text(parser_cfg.get("model", "")) or _clean_text(translator.get("model", ""))
    return translator, model, enabled


def _call_parser_model(
    system: str,
    user: str,
    *,
    cfg: Optional[dict[str, Any]],
    model: str = "",
    client: Any = None,
) -> str:
    translator, inherited_model, enabled = _parser_config(cfg)
    if not enabled:
        raise RuntimeError("parser disabled")
    model = _clean_text(model) or inherited_model
    if client is None:
        from .openai_client import OpenAIClient

        client = OpenAIClient(
            _clean_text(translator.get("base_url", "")),
            _clean_text(translator.get("api_key", "")),
        )
    if callable(client) and not hasattr(client, "generate_text"):
        return str(client(system, user, model))
    return str(
        client.generate_text(
            [{"role": "system", "content": system}, {"role": "user", "content": user}],
            model,
            max_tokens=1800,
            temperature=0.1,
        )
    )


def _case_parser_system() -> str:
    fields = ", ".join(FACET_FIELDS)
    return (
        "你是 Prompt Case 结构化解析器。只输出一个 JSON 对象，不要 Markdown。"
        f"facets 只能包含这些字段：{fields}。缺失字段使用空字符串。"
        "不要补充原文没有的事实。inferred_requirement 只用于没有真实 requirement 的外部 Prompt。"
        "transferable_lessons 只能写视觉表达、构图、光影、空间、动作、材质或提示词组织技巧，"
        "禁止复制人物、服装、地点、颜色、道具或事件。"
    )


def parse_prompt_case(
    requirement: str = "",
    content: str = "",
    *,
    cfg: Optional[dict[str, Any]] = None,
    model: str = "",
    client: Any = None,
    warning: Optional[Callable[[str], None]] = None,
    strict: bool = False,
) -> PromptCase:
    """Parse one prompt into a case.

    Runtime prompt processing deliberately keeps the safe fallback behavior,
    but migration callers can set ``strict=True`` so a malformed parser
    response is reported to the per-record rebuild loop instead of being
    written as a partially parsed case.
    """
    content = str(content or "").strip()
    if not content:
        raise ValueError("content 不能为空")
    original_requirement = _clean_text(requirement)
    try:
        output = _call_parser_model(
            _case_parser_system(),
            json.dumps({"requirement": original_requirement, "content": content}, ensure_ascii=False),
            cfg=cfg,
            model=model,
            client=client,
        )
        data = _decode_json_object(output)
        facets = PromptFacets.from_dict(data.get("facets"))
        inferred = _clean_text(data.get("inferred_requirement", ""))
        lessons = _clean_lessons(data.get("transferable_lessons"))
        effective_req = original_requirement or inferred
        source = "user" if original_requirement else ("inferred" if inferred else "none")
        case = PromptCase(
            requirement=original_requirement,
            inferred_requirement=inferred if not original_requirement else "",
            requirement_source=source,
            content=content,
            task_type=_clean_text(data.get("task_type", "text_to_image")) or "text_to_image",
            facets=facets,
            transferable_lessons=lessons,
            parser_version=PROMPT_CASE_PARSER_VERSION,
        )
        case.intent_text = build_intent_text(case)
        case.visual_text = build_visual_text(case)
        if not effective_req and not case.intent_text:
            case.intent_text = content
        return case
    except Exception as exc:  # noqa: BLE001 - parser must not block generation
        if strict:
            raise
        _warn(f"Prompt Case Parser 失败，使用降级检索：{exc}", warning)
        fallback = PromptCase(
            requirement=original_requirement,
            inferred_requirement="",
            requirement_source="user" if original_requirement else "none",
            content=content,
            facets=PromptFacets(),
            intent_text=original_requirement or content,
            visual_text="",
            transferable_lessons=[],
            parser_version=0,
        )
        return fallback


def _query_parser_system() -> str:
    return (
        "你是用户生图需求 Query Parser。只输出 JSON，不要 Markdown。"
        "facets 必须使用固定 Prompt Case schema；locked 是对象，字段值必须是布尔值。"
        "locked=true 只表示用户明确指定，不能把合理推断当成锁定。"
        "不要从示例或知识库补充用户没有说的主体、地点、服装、颜色、道具或事件。"
    )


def parse_query(
    user_text: str,
    *,
    cfg: Optional[dict[str, Any]] = None,
    composition: str = "",
    hard_constraints: str = "",
    model: str = "",
    client: Any = None,
    warning: Optional[Callable[[str], None]] = None,
) -> QueryAnalysis:
    """Parse only the current user request, never library cases."""
    query = str(user_text or "").strip()
    if not query:
        return QueryAnalysis(query="", facets=PromptFacets(), locked={}, parser_version=0)
    request = {"query": query}
    if _clean_text(composition):
        request["composition"] = _clean_text(composition)
    if _clean_text(hard_constraints):
        request["hard_constraints"] = _clean_text(hard_constraints)
    try:
        output = _call_parser_model(
            _query_parser_system(),
            json.dumps(request, ensure_ascii=False),
            cfg=cfg,
            model=model,
            client=client,
        )
        data = _decode_json_object(output)
        raw_locked = data.get("locked") if isinstance(data.get("locked"), Mapping) else {}
        locked = {
            key: raw_locked.get(key) is True
            for key in LOCKABLE_FIELDS
        }
        analysis = QueryAnalysis(
            query=query,
            facets=PromptFacets.from_dict(data.get("facets")),
            locked=locked,
            parser_version=PROMPT_CASE_PARSER_VERSION,
        )
        if _clean_text(composition) and not analysis.facets.composition:
            analysis.facets.composition = _clean_text(composition)
            analysis.locked["composition"] = True
        if _clean_text(hard_constraints):
            analysis.facets.constraints = _clean_text(hard_constraints)
            analysis.locked["constraints"] = True
        return analysis
    except Exception as exc:  # noqa: BLE001
        _warn(f"Query Parser 失败，使用原始需求降级检索：{exc}", warning)
        return QueryAnalysis(
            query=query,
            facets=PromptFacets(),
            locked={key: False for key in LOCKABLE_FIELDS},
            parser_version=0,
        )


def _as_facets(value: Any) -> PromptFacets:
    if isinstance(value, PromptCase):
        return value.facets
    if isinstance(value, QueryAnalysis):
        return value.facets
    if isinstance(value, PromptFacets):
        return value
    if isinstance(value, Mapping):
        return PromptFacets.from_dict(value.get("facets", value))
    return PromptFacets()


def _effective_requirement(value: Any) -> str:
    if isinstance(value, PromptCase):
        return value.requirement or value.inferred_requirement
    if isinstance(value, Mapping):
        return _clean_text(value.get("requirement") or value.get("inferred_requirement"))
    return ""


def build_intent_text(case_or_query: Any) -> str:
    facets = _as_facets(case_or_query)
    labels = (
        ("需求", _effective_requirement(case_or_query)),
        ("主体", facets.subject),
        ("外观", facets.appearance),
        ("动作", facets.action),
        ("互动", facets.interaction),
        ("环境", facets.environment),
        ("空间关系", facets.spatial_relationship),
        ("约束", facets.constraints),
    )
    if isinstance(case_or_query, QueryAnalysis) and case_or_query.query:
        labels = (("需求", case_or_query.query),) + labels[1:]
    return "\n".join(f"{label}：{text}" for label, text in labels if _clean_text(text))


def build_visual_text(case_or_query: Any) -> str:
    facets = _as_facets(case_or_query)
    labels = (
        ("构图", facets.composition),
        ("镜头", facets.camera),
        ("光线", facets.lighting),
        ("色彩", facets.color),
        ("氛围", facets.mood),
        ("风格", facets.style),
        ("媒介", facets.medium),
        ("材质", facets.materials),
    )
    return "\n".join(f"{label}：{text}" for label, text in labels if _clean_text(text))


def format_prompt_case(case: PromptCase | Mapping[str, Any], index: int = 1, max_chars: int = 2600) -> str:
    """Format a case for Translator without turning it into an unbounded blob."""
    if not isinstance(case, PromptCase):
        case = PromptCase.from_dict(case)
    facets = case.facets.to_dict()
    lines = [
        f"【案例 {index}】",
        "",
        "原始需求：",
        case.requirement or case.inferred_requirement or "（无）",
        "",
        "视觉结构：",
    ]
    for key, label in (
        ("subject", "主体"), ("appearance", "外观"), ("action", "动作"),
        ("interaction", "互动"), ("environment", "环境"),
        ("spatial_relationship", "空间关系"), ("composition", "构图"),
        ("camera", "镜头"), ("lighting", "光线"), ("color", "色彩"),
        ("mood", "氛围"), ("style", "风格"), ("medium", "媒介"),
        ("materials", "材质"), ("text", "画面文字"), ("constraints", "约束"),
    ):
        if facets.get(key):
            lines.append(f"{label}：{facets[key]}")
    lines.extend(["", "可迁移技巧："])
    lines.extend(f"- {lesson}" for lesson in case.transferable_lessons[:8])
    lines.extend(["", "优秀提示词：", case.content])
    result = "\n".join(lines).strip()
    if len(result) <= max_chars:
        return result
    # Preserve structure and the beginning of the original prompt; the cap is
    # applied to the formatted case, not by blindly appending a fixed slice.
    return result[: max_chars - 1].rstrip() + "…"


def _cosine(a: Any, b: Any) -> float:
    try:
        av, bv = list(a), list(b)
        if not av or not bv or len(av) != len(bv):
            return 0.0
        dot = sum(float(x) * float(y) for x, y in zip(av, bv))
        an = math.sqrt(sum(float(x) * float(x) for x in av))
        bn = math.sqrt(sum(float(y) * float(y) for y in bv))
        return dot / (an * bn) if an and bn else 0.0
    except (TypeError, ValueError):
        return 0.0


def _text_similarity(a: Mapping[str, Any], b: Mapping[str, Any]) -> float:
    af = _as_facets(a).to_dict()
    bf = _as_facets(b).to_dict()
    at = {token for value in af.values() for token in re.findall(r"\w+|[\u4e00-\u9fff]", value)}
    bt = {token for value in bf.values() for token in re.findall(r"\w+|[\u4e00-\u9fff]", value)}
    if at and bt:
        return len(at & bt) / max(1, len(at | bt))
    return 1.0 if _clean_text(a.get("visual_text")) == _clean_text(b.get("visual_text")) and _clean_text(a.get("visual_text")) else 0.0


def select_diverse_cases(
    candidates: Sequence[Mapping[str, Any]],
    scores: Optional[Sequence[float]] = None,
    visual_vectors: Optional[Sequence[Sequence[float]]] = None,
    final_k: Optional[int] = None,
    prompt_mode: str = "optimized",
    diversity_strength: Optional[float] = None,
) -> list[dict[str, Any]]:
    """Deterministic MMR-style selection over already ranked candidates."""
    profile = mode_profile(prompt_mode)
    limit = int(final_k or profile["final_k"])
    strength = profile["diversity_strength"] if diversity_strength is None else float(diversity_strength)
    remaining = [dict(item) for item in candidates]
    if scores is not None:
        for index, score in enumerate(scores):
            if index < len(remaining):
                remaining[index]["rerank_score"] = float(score)
    if visual_vectors is not None:
        for index, vector in enumerate(visual_vectors):
            if index < len(remaining):
                remaining[index]["_visual_vector"] = list(vector)
    selected: list[dict[str, Any]] = []
    while remaining and len(selected) < max(0, limit):
        best_index, best_score = 0, -float("inf")
        for index, item in enumerate(remaining):
            relevance = float(item.get("rerank_score", item.get("relevance_score", 0.0)) or 0.0)
            if not relevance:
                relevance = 0.65 * float(item.get("intent_score", 0.0) or 0.0) + 0.35 * float(item.get("visual_score", 0.0) or 0.0)
            vector = item.get("_visual_vector") or item.get("visual_vector")
            penalty = 0.0
            for old in selected:
                old_vector = old.get("_visual_vector") or old.get("visual_vector")
                similarity = _cosine(vector, old_vector) if vector and old_vector else _text_similarity(item, old)
                penalty = max(penalty, similarity)
            score = relevance - strength * penalty
            if score > best_score:
                best_index, best_score = index, score
        selected.append(remaining.pop(best_index))
    for item in selected:
        item.pop("_visual_vector", None)
        item.pop("visual_vector", None)
    return selected
