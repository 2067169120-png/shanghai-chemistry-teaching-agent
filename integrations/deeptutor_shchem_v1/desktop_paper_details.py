"""Read-only statistics of the current ordered draft, never inferred from text.

Source scores only come from the visual compiler's explicit source_scores.
The core catalogue and Word selection do not expose verified original scores.
Counts describe registered source structure, not human chemical review.
"""
from __future__ import annotations

from copy import deepcopy
import math

from .paper_export_alias_projection import project_explicit_alias_units

SCHEMA = "shchem.desktop-paper-details.v1"
KINDS = {"core_theme": "原卷完整主题", "word_question": "Word 完整题段",
         "personal_visual_theme": "图片完整主题"}


def _id(value):
    return isinstance(value, str) and bool(value.strip()) and value not in {"unknown", "待核验"}


def _score(value):
    return value if type(value) in (int, float) and math.isfinite(value) and value > 0 else None


def score_summary(values):
    known = [value for value in values if value is not None]
    missing = len(values) - len(known)
    subtotal = sum(known) if known else None
    return {"known": subtotal, "known_count": len(known), "missing_count": missing,
            "total": subtotal if not missing else None}


def _chain(item):
    content = item["content"]
    chain = content.get("atomic_chain")
    if not isinstance(chain, list) or not chain:
        return None
    if item["source_ref"].get("scope") == "master" and any(
        isinstance(row, dict) and row.get("alias_units") for row in chain
    ):
        projected = project_explicit_alias_units(
            {"papers": [{"paper": content.get("paper", {}), "theme_groups": [content]}]},
            [], scope="master",
        )
        chain = projected.catalog["papers"][0]["theme_groups"][0]["atomic_chain"]
    ids = [row.get("atomic_part_id") if isinstance(row, dict) else None for row in chain]
    if not all(map(_id, ids)) or len(ids) != len(set(ids)):
        raise ValueError("原卷作答单元身份缺失或重复，不能统计。")
    return chain


def _blocks(value):
    if not isinstance(value, list):
        return None
    indices = [row.get("index") if isinstance(row, dict) else None for row in value]
    if any(type(index) is not int or index < 1 for index in indices):
        return None
    # These are DOCX block positions, not original printed question numbers.
    return indices


def _row(item, settings, number):
    kind, content, ref = item["kind"], item["content"], item["source_ref"]
    counts = {"themes": None, "printed": None, "atomic": None}
    selected = {}
    source_values, current_values = [None], [None]
    score_basis = "本次设置"
    if kind == "word_question":
        selected = {name: _blocks(content.get(name)) for name in
                    ("question_blocks", "context_blocks", "answer_blocks")}
        current_values = [_score(settings.get("points"))]
        scope = "一个已选完整题段；不据文字题号推定主题、小题或作答单元。"
        evidence = "题段范围已按来源读取；未登记题内三级层级。"
    elif kind == "core_theme":
        theme_id = content.get("theme", {}).get("id")
        if _id(theme_id) and _id(ref.get("theme_id")) and theme_id != ref["theme_id"]:
            raise ValueError("原卷主题身份与来源不一致。")
        if _id(theme_id) and theme_id == ref.get("theme_id"):
            counts["themes"] = 1
        chain = _chain(item)
        if chain is not None:
            atomic_ids = [row["atomic_part_id"] for row in chain]
            printed_ids = [row.get("printed_question_id") for row in chain]
            counts["atomic"] = len(atomic_ids)
            if all(map(_id, printed_ids)):
                counts["printed"] = len(set(printed_ids))
            selected = {"theme_id": theme_id, "printed_ids": list(dict.fromkeys(
                value for value in printed_ids if _id(value))), "atomic_ids": atomic_ids}
            source_values = [None] * len(chain)
            atomic_settings = settings.get("atomic_settings", {})
            if atomic_settings:
                # Match the export rule: a per-unit override must cover the
                # final explicit IDs exactly; never silently apply defaults.
                if not isinstance(atomic_settings, dict) or set(atomic_settings) != set(atomic_ids):
                    raise ValueError("逐作答单元配分与当前来源不一致，请重新核对。")
                current_values = [_score(atomic_settings[node].get("score")) for node in atomic_ids]
            else:
                current_values = [_score(settings.get("score_per_atomic"))] * len(chain)
        scope = "完整主题；共同材料与已登记作答单元整体保留。"
        evidence = "依据来源目录的显式身份；拆分单元沿用导出规则。"
    else:
        theme = content.get("theme", {})
        printed = theme.get("printed_questions")
        theme_id = theme.get("theme_big_question_id")
        if _id(theme_id) and _id(ref.get("theme_id")) and theme_id != ref["theme_id"]:
            raise ValueError("图片主题身份与来源不一致。")
        if _id(theme_id) and theme_id == ref.get("theme_id"):
            counts["themes"] = 1
        if isinstance(printed, list) and printed:
            printed_ids, atomic_ids, pairs = [], [], []
            for question in printed:
                node = question.get("printed_question_id")
                atoms = question.get("atomic_parts")
                if not _id(node) or not isinstance(atoms, list) or not atoms:
                    raise ValueError("图片主题层级不完整，不能统计。")
                printed_ids.append(node)
                for atom in atoms:
                    atomic = atom.get("atomic_part_id")
                    if not _id(atomic):
                        raise ValueError("图片作答单元身份缺失。")
                    atomic_ids.append(atomic)
                    pairs.append((node, atomic))
            if len(set(printed_ids)) != len(printed_ids) or len(set(atomic_ids)) != len(atomic_ids):
                raise ValueError("图片主题层级身份重复，不能统计。")
            counts.update(printed=len(printed_ids), atomic=len(atomic_ids))
            selected = {"theme_id": theme_id, "printed_ids": printed_ids, "atomic_ids": atomic_ids}
            scores = content.get("source_scores")
            if not isinstance(scores, list):
                scores = []
            score_map = {}
            for row in scores:
                pair = (row.get("printed_question_id"), row.get("atomic_part_id"))
                if pair not in pairs or pair in score_map:
                    raise ValueError("来源分值与图片作答单元身份不一致。")
                score_map[pair] = None if row.get("status") == "missing" else _score(row.get("max_score"))
            source_values = [score_map.get(pair) for pair in pairs]
            current_values = source_values
        scope = "完整图片主题；包含共同材料及全部已登记小题。"
        score_basis = "沿用来源分值（未核验权威性）"
        evidence = "图片候选的显式层级；不表示教师已完成人工复核。"
    return {"key": item["key"], "number": number, "kind": kind, "kind_zh": KINDS[kind],
            "title": item.get("title_zh") or "标题待核对", "source": item.get("source_zh") or "来源待核对",
            "source_ref": deepcopy(ref), "scope": scope, "selected": selected, "counts": counts,
            "structure_evidence": evidence, "current_score": score_summary(current_values),
            "source_score": score_summary(source_values), "score_basis": score_basis}


def build_paper_details(items, order, excluded, settings, *, duration_minutes):
    """Project the saved current draft; no disk, provider, Office, or mutations."""
    if (not isinstance(items, dict) or not isinstance(order, list) or len(items) > 100
            or len(set(order)) != len(order) or set(order) & set(excluded)
            or set(order) | set(excluded) != set(items) or set(settings) != set(items)):
        raise ValueError("当前卷顺序、移除项或配分与来源不一致。")
    rows = []
    for number, key in enumerate(order, 1):
        item = items[key]
        if (item.get("key") != key or item.get("kind") not in KINDS
                or not isinstance(item.get("content"), dict) or not isinstance(item.get("source_ref"), dict)
                or not isinstance(settings[key], dict)):
            raise ValueError("当前卷来源资料不完整。")
        rows.append(_row(item, settings[key], number))
    counts = {}
    for name in ("themes", "printed", "atomic"):
        values = [row["counts"][name] for row in rows]
        known = [value for value in values if value is not None]
        counts[name] = {"known": sum(known) if known else None,
                        "unknown_sections": len(values) - len(known),
                        "total": sum(known) if known and len(known) == len(values) else None}
    scores = {}
    for name in ("current_score", "source_score"):
        known = [row[name]["known"] for row in rows if row[name]["known"] is not None]
        missing = sum(row[name]["missing_count"] for row in rows)
        scores[name] = {"known": sum(known) if known else None, "missing_count": missing,
                        "known_count": sum(row[name]["known_count"] for row in rows),
                        "total": sum(known) if known and not missing else None}
    return {"schema_version": SCHEMA, "rows": rows, "counts": counts, **scores,
            "section_count": len(rows), "excluded_count": len(excluded),
            "duration_minutes": duration_minutes, "target_coverage": None, "estimated_minutes": None}
