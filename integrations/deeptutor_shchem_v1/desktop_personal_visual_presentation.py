"""Lossless, presentation-only folding of personal visual-question text.

The legacy row remains the identity and full-text authority. This module neither
changes its revision nor decides source relationships. Source paths are JSON
pointers rooted at the supplied ``/theme`` or ``/printed`` argument. Every source
text block, including folded blocks, remains available in ``source_blocks``.
There is deliberately no persistence, model call, chemistry normalization, or
cross-atomic answer deduplication here.
"""

from __future__ import annotations

from collections.abc import Mapping
from copy import deepcopy

FORMAT_VERSION = "personal-visual-presentation-v1"
_TEXT_FIELDS = ("question_text", "shared_text", "answer_text")
_PROSE_BOUNDARIES = frozenset(" \t\r\n。！？；;:：!?.,，、（）()【】[]\"“”‘’")
_CHEMICAL_CONTINUATION = frozenset(
    "ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789"
    "+-−_./()[]{}·↑↓^*×÷⋅°%‰℃⁰¹²³⁴⁵⁶⁷⁸⁹⁺⁻⁽⁾₀₁₂₃₄₅₆₇₈₉₊₋₍₎αβγδΔ"
)


def _text(value):
    return value.strip() if isinstance(value, str) else ""


def _refs(node):
    return [value for value in node.get("evidence_refs", []) if isinstance(value, str)]


def _conditions(node):
    return tuple(_text(item.get("raw")) for item in node.get("chemical_expressions", [])
                 if item.get("kind") == "condition" and _text(item.get("raw")))


def _covered_span(needle, haystack, *, chemical):
    """Only literal, case-sensitive spans; never match part of a chemical token."""
    if not needle:
        return None
    start = haystack.find(needle)
    while start >= 0:
        end = start + len(needle)
        before, after = haystack[start - 1:start], haystack[end:end + 1]
        if chemical:
            bounded = ((not before or before not in _CHEMICAL_CONTINUATION)
                       and (not after or after not in _CHEMICAL_CONTINUATION))
        else:
            bounded = ((not before or before in _PROSE_BOUNDARIES)
                       and (not after or after in _PROSE_BOUNDARIES))
        if bounded:
            return [start, end]
        start = haystack.find(needle, start + 1)
    return None


class _Blocks:
    def __init__(self):
        self.blocks = []

    def add(self, *, section, scope, kind, path, text, refs, rendered=None,
            candidates=(), chemical=False, status=None, conditions=(), match_key=None):
        if not isinstance(text, str) or not text.strip():
            return None
        clean = text.strip()
        block = {"block_id": f"block-{len(self.blocks) + 1:04d}", "section": section,
                 "scope_id": scope, "kind": kind, "source_path": path, "text": text,
                 "rendered_text": clean if rendered is None else rendered,
                 "evidence_refs": list(dict.fromkeys(refs)), "status": status,
                 "folded_into": None, "fold_reason": None, "covered_span": None}
        # Missing evidence or uncertain expressions are not evidence of a
        # duplicate. Conditions must also be explicitly covered by the target.
        if status != "uncertain" and refs:
            for target in candidates:
                if target is None or target["folded_into"] is not None:
                    continue
                if target["section"] != section or not set(refs).intersection(target["evidence_refs"]):
                    continue
                if target["status"] == "uncertain":
                    continue
                if match_key is not None and target.get("match_key") != match_key:
                    continue
                if conditions and any(_covered_span(value, target["text"].strip(), chemical=True) is None
                                      for value in conditions):
                    continue
                span = _covered_span(clean, target["text"].strip(), chemical=chemical)
                if span is not None:
                    offset = len(target["text"]) - len(target["text"].lstrip())
                    span = [value + offset for value in span]
                    block.update(folded_into=target["block_id"],
                                 fold_reason="exact_text_already_covered", covered_span=span)
                    break
        if match_key is not None:
            block["match_key"] = match_key
        self.blocks.append(block)
        return block

    def text(self, section):
        return "\n".join(block["rendered_text"] for block in self.blocks
                         if block["section"] == section and block["folded_into"] is None)


def _expressions(builder, node, path, section, scope, body_blocks, parent_blocks=(), prefix=""):
    conditions = _conditions(node)
    result = []
    for index, expression in enumerate(node.get("chemical_expressions", [])):
        # Only body/requirement/analysis blocks can cover an expression. Another
        # standalone expression is not enough to establish identical conditions.
        result.append(builder.add(section=section, scope=scope, kind="chemical_expression",
            path=f"{path}/chemical_expressions/{index}/raw", text=expression.get("raw", ""),
            refs=_refs(expression), candidates=[*body_blocks, *parent_blocks], chemical=True,
            status=expression.get("status"), conditions=conditions,
            rendered=prefix + _text(expression.get("raw"))))
    return [block for block in result if block is not None]


def _question_node(builder, node, path, scope, parent=None):
    parent = parent or {"body": [], "options": {}, "conditions": ()}
    # Parent/child folding is allowed; siblings never enter each other's pool.
    inherited = parent["body"] if _conditions(node) == parent["conditions"] else []
    body = []
    stem = builder.add(section="question", scope=scope, kind="stem", path=path + "/stem",
                       text=node.get("stem", ""), refs=_refs(node), candidates=inherited)
    if stem is not None:
        body.append(stem)
    options = {}
    for index, option in enumerate(node.get("options", [])):
        label = _text(option.get("label"))
        option_path = f"{path}/options/{index}"
        parent_option = parent["options"].get(label, []) if _conditions(node) == parent["conditions"] else []
        content = builder.add(section="question", scope=scope, kind="option",
            path=option_path + "/content", text=option.get("content", ""), refs=_refs(option),
            rendered=(label + "  " + _text(option.get("content"))).strip(),
            candidates=parent_option, match_key=label, conditions=_conditions(option))
        option_bodies = [content] if content is not None else []
        _expressions(builder, option, option_path, "question", scope, option_bodies, parent_option,
                     prefix=label + "  " if label else "")
        options[label] = [*option_bodies, *parent_option]
    requirement = builder.add(section="question", scope=scope, kind="response_requirements",
        path=path + "/response_requirements", text=node.get("response_requirements", ""), refs=_refs(node),
        rendered="作答要求：" + _text(node.get("response_requirements")), candidates=[*body, *inherited])
    if requirement is not None:
        body.append(requirement)
    _expressions(builder, node, path, "question", scope, body, inherited)
    return {"body": [*body, *inherited], "options": options, "conditions": _conditions(node)}


def _answer(builder, atomic, path, scope):
    answer = atomic.get("answer", {})
    label = _text(atomic.get("part_label"))
    if label:
        builder.add(section="answer", scope=scope, kind="part_label", path=path + "/part_label",
                    text=atomic["part_label"], refs=_refs(atomic))
    answer_path = path + "/answer"
    if answer.get("status") == "missing":
        builder.add(section="answer", scope=scope, kind="answer_status", path=answer_path + "/status",
                    text="missing", rendered="参考答案待补充", refs=_refs(answer))
        return
    body = builder.add(section="answer", scope=scope, kind="answer_body", path=answer_path + "/answer_body",
                       text=answer.get("answer_body", ""), refs=_refs(answer))
    bodies = [body] if body is not None else []
    analysis = builder.add(section="answer", scope=scope, kind="analysis", path=answer_path + "/analysis",
        text=answer.get("analysis", ""), refs=_refs(answer), candidates=bodies)
    if analysis is not None:
        bodies.append(analysis)
    _expressions(builder, answer, answer_path, "answer", scope, bodies)
    score = answer.get("max_score", 0)
    positive = isinstance(score, (float, int)) and not isinstance(score, bool) and score > 0
    rendered = f"来源参考分值：{score:g}分（AI识别，待对照原页）" if positive else "来源分值待核对"
    builder.add(section="answer", scope=scope, kind="max_score", path=answer_path + "/max_score",
                text=str(score), rendered=rendered, refs=_refs(answer))
    for index, point in enumerate(answer.get("scoring_points", [])):
        builder.add(section="answer", scope=scope, kind="scoring_point",
            path=f"{answer_path}/scoring_points/{index}/description", text=point.get("description", ""),
            rendered=f"{point.get('score', '')}分：{point.get('description', '')}", refs=_refs(point))


def build_personal_visual_presentation(theme, printed, row):
    """Return additive display data; never mutate or replace any legacy field.

    ``binding_revision`` must equal ``row['revision']`` before a caller uses the
    display. ``full_text`` is the exact legacy text, without even stripping it.
    Empty shared refs mean only ``unlinked``; no automatic not-required decision.
    """
    if not all(isinstance(value, Mapping) for value in (theme, printed, row)):
        raise ValueError("Presentation inputs must be mappings")
    if not isinstance(row.get("revision"), str) or not row["revision"]:
        raise ValueError("Presentation requires the original row revision")
    if any(not isinstance(row.get(field), str) for field in _TEXT_FIELDS):
        raise ValueError("Presentation requires all three original text fields")
    builder = _Blocks()
    scope = printed.get("printed_question_id", "printed")
    parent = _question_node(builder, printed, "/printed", scope)
    atomics = printed.get("atomic_parts", [])
    for index, atomic in enumerate(atomics):
        path = f"/printed/atomic_parts/{index}"
        atomic_scope = atomic.get("atomic_part_id", path)
        if _text(atomic.get("part_label")):
            builder.add(section="question", scope=atomic_scope, kind="part_label", path=path + "/part_label",
                        text=atomic["part_label"], refs=_refs(atomic))
        _question_node(builder, atomic, path, atomic_scope, parent)
        _answer(builder, atomic, path, atomic_scope)
    requested = list(dict.fromkeys(ref for node in [printed, *atomics]
                                  for ref in node.get("shared_material_refs", [])))
    materials = theme.get("shared_materials", [])
    known = {material.get("shared_material_id") for material in materials}
    for index, material in enumerate(materials):
        if material.get("shared_material_id") not in requested:
            continue
        material_scope = material["shared_material_id"]
        path = f"/theme/shared_materials/{index}"
        body = builder.add(section="shared", scope=material_scope, kind="shared_content", path=path + "/content",
                           text=material.get("content", ""), refs=_refs(material))
        _expressions(builder, material, path, "shared", material_scope, [body] if body is not None else [])
    unresolved = [ref for ref in requested if ref not in known]
    status = "invalid_reference" if unresolved else "linked" if requested else "unlinked"
    blocks = deepcopy(builder.blocks)
    return {"format_version": FORMAT_VERSION, "binding_revision": row["revision"],
            "question_text": builder.text("question"), "shared_text": builder.text("shared"),
            "answer_text": builder.text("answer"),
            "full_text": {field: row[field] for field in _TEXT_FIELDS}, "source_blocks": blocks,
            "folded_items": [{"block_id": block["block_id"], "into_block_id": block["folded_into"],
                              "reason": block["fold_reason"], "covered_span": deepcopy(block["covered_span"])}
                             for block in blocks if block["folded_into"] is not None],
            "shared_scope": {"status": status, "requested_material_ids": requested,
                             "unresolved_material_ids": unresolved, "confirmed_not_required": False}}
