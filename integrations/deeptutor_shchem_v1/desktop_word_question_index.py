"""Candidate question boundaries over a frozen native Word preview.

No source documents, files, providers or settings are opened here. Question
blocks retain their source order and asset metadata. ``export_ready`` means
only that these boundaries can use whole source blocks; the export backend
must still validate structures and source identity. It is not teaching approval.
"""

from __future__ import annotations

import hashlib
import json
import re
from copy import deepcopy
from typing import Any

QUESTION_INDEX_REVISION = "20260910-word-answer-boundary-index-v4"
_GAP = re.compile(r"【(?:待查看原文：[\s\S]*?|未提取到文字)】")
_NUMBER = r"[0-9０-９一二三四五六七八九十百]+(?:[-－—][0-9０-９]+)?"
_KIND = r"(?:即学即练|同步练习|随堂练习|针对训练|典例|例题|例|变式(?:训练|练习)?|练习)"
_SUFFIX = r"(?:\s*[·•]\s*[^】\]）)\n]{0,24}|\s*变(?:载体|考法|题型))?"
_BRACKETED = re.compile(rf"^[【\[（(]\s*({_KIND}\s*{_NUMBER}){_SUFFIX}\s*[】\]）)]")
_BARE = re.compile(
    rf"^({_KIND}\s*{_NUMBER})(?=$|\s|[．.、:：]|[^0-9０-９一二三四五六七八九十百\-－—])"
)
_ANY_EXPLICIT = re.compile(rf"[【\[（(]\s*{_KIND}\s*{_NUMBER}{_SUFFIX}\s*[】\]）)]")
_BROKEN_VARIANT = re.compile(
    rf"^[【\[]\s*(变式(?:训练|练习)?\s*{_NUMBER})\s*[·•]?\s*变(?:载体|考法|题型)(?=[^】\]\s])"
)
_LEADING_TAG = re.compile(r"^【[^】\n]{1,32}】\s*(?=【|\[)")
# A full-width/list delimiter may be followed by a numerical condition (e.g.
# "24．25 ℃时"). Only an adjacent ASCII dot+digit denotes a decimal value.
_GENERIC = re.compile(r"^(\d{1,3})(?:\s*[．、]\s*|\s*\.(?!\d)\s*)(.*)$", re.DOTALL)
_QUESTION = re.compile(
    r"[?？]|_{2,}|下列.{0,80}(?:正确|错误|不正确|不合理|属于|的是|有)|"
    r"(?:^|请|试)(?:写出|回答|解答|判断|填空|填入|填写|选择|求出|计算出|计算下列|按要求)|"
    r"请[^。；;\n]{0,24}(?:回答|填空|填写|写出)|"
    r"(?<=\S)[ \t\u3000]{3,}(?=[。；;，,（(]|(?:kJ|mol|mL|L|g|K|℃)(?:\b|[·/])|$)|"
    r"(?:回答|完成)(?:下列|以下|有关)问题|"
    r"(?:为什么|如何|哪些|哪项|(?<!无论)多少|是否(?:正确|合理|成立)|的是\s*[（(])"
)
_OPTION = re.compile(r"(?:^|\n)\s*[A-HＡ-Ｈ]\s*[．.、]", re.MULTILINE)
_ANSWER = re.compile(
    r"【\s*(?:参考答案|答案|解析|详解|解答|分析)\s*】|(?:^|\n)\s*(?:参考答案|答案|解析|详解|解答)\s*[:：]"
)
_HEADING = re.compile(
    r"^(?:[►▶●■◆]\s*)?(?:知识点|考点|常见考法|必杀技|知识精讲|知识导学|"
    r"温馨提示|方法总结|规律总结|得分速记|思维建模|考向\s*\d+|题组[A-ZＡ-Ｚ一二三四五六七八九十]?|"
    r"问题[一二三四五六七八九十\d]+|第[一二三四五六七八九十\d]+[章节]|"
    r"[一二三四五六七八九十]+[、．.]|\d+(?:\.\d+)+\s+(?=[\u3400-\u9fff]))"
)
_PRACTICE = re.compile(
    r"^(?:题组|基础过关练|能力提升练|培优拔尖练|课堂练习|随堂练习|同步练习|分层训练|课后练习|效果检测|巩固练习|综合练习|练习题|练习[：:]?$)"
)
_KNOWLEDGE_NUMBER = re.compile(
    r"^(?:.{0,18}(?:定义|概念|特点|分类|的类型|的条件|的书写|性质|规律)|(?:强|弱|非)?电解质\s*[：:])"
)
_KNOWLEDGE_TITLE = re.compile(
    r"^[^？?。\n]{0,72}(?:方法|原则|注意事项|模板|含义|简介|关系|应用|影响|"
    r"判断|比较|命名|规则|思路|流程|技巧|思维建模|结论|运动状态|评价|式法|的选择|电子排布式)"
    r"(?:[：:].*|[（(][^？?\n]*[）)])?$"
)
_MATERIAL = re.compile(
    r"^(?:【\s*(?:共同材料|共享材料|公共材料|阅读材料|材料)\s*】|(?:共同材料|共享材料|公共材料|阅读材料|材料)\s*[:：]|阅读(?:下列|以下)材料.{0,35}回答)"
)
_SHARED = re.compile(
    r"共同材料|共享材料|公共材料|回答.{0,20}(?:[1-9一二三四五六七八九十].{0,8}[-—至到].{0,8}[1-9一二三四五六七八九十]|各题)"
)
_BACK_REFERENCE = re.compile(
    r"前文|上文|前题|上一题|上述材料|以上材料|根据上述|结合上述|根据上图|根据前图"
)
_SCORED_THEME = re.compile(r"^[一二三四五六七八九十]+[、．.]\s*\S.{0,100}[（(]\s*\d+(?:\.\d+)?\s*分\s*[）)]\s*$")
_PARENTHESIZED_PART = re.compile(r"^[（(]\s*\d{1,2}\s*[）)]")


def _digest(value: Any) -> str:
    payload = json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        allow_nan=False,
    )
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def _clean(text: str) -> str:
    return _GAP.sub("", text).strip().lstrip("【").rstrip("】").strip()


def _marker(text: str) -> str | None:
    # Strip image markers, but retain the brackets of the actual question.
    text = _GAP.sub("", text).strip()
    # Editorial tags may precede the actual exercise label, but answer labels
    # must never be skipped to discover a question quoted inside its solution.
    while not (_BRACKETED.match(text) or _BARE.match(text) or _ANSWER.match(text)):
        tag = _LEADING_TAG.match(text)
        if tag is None:
            break
        text = text[tag.end():]
    match = _BRACKETED.match(text) or _BARE.match(text) or _BROKEN_VARIANT.match(text)
    return match.group(1).strip() if match else None


def _preview(preview: dict[str, Any]) -> tuple[list[dict[str, Any]], dict[int, int]]:
    if not isinstance(preview, dict):
        raise TypeError("Word预览必须是对象。")
    if not re.fullmatch(r"[0-9a-fA-F]{64}", str(preview.get("source_sha256", ""))):
        raise ValueError("Word预览缺少有效来源哈希。")
    if not isinstance(preview.get("revision"), str) or not preview["revision"]:
        raise ValueError("Word预览缺少版本。")
    blocks = preview.get("blocks")
    if not isinstance(blocks, list) or len(blocks) > 100_000:
        raise ValueError("Word预览区块无效或过多。")
    positions = {}
    previous = 0
    text_size = 0
    for position, block in enumerate(blocks):
        if not isinstance(block, dict):
            raise TypeError("Word预览区块必须是对象。")
        index = block.get("index")
        if type(index) is not int or index <= previous or index in positions:
            raise ValueError("Word区块编号必须唯一并按来源顺序递增。")
        if not isinstance(block.get("text", ""), str):
            raise TypeError("Word区块文字无效。")
        if not isinstance(block.get("warnings", []), list):
            raise TypeError("Word区块提示无效。")
        text_size += len(block.get("text", ""))
        if text_size > 8_000_000:
            raise ValueError("Word预览文字超出限制。")
        positions[index] = position
        previous = index
    assets = preview.get("assets", [])
    if not isinstance(assets, list) or len(assets) > 100_000:
        raise ValueError("Word图片引用无效。")
    asset_ids = set()
    for asset in assets:
        if not isinstance(asset, dict) or type(asset.get("block_index")) is not int:
            raise ValueError("Word图片引用缺少来源区块。")
        asset_id = asset.get("asset_id")
        if not isinstance(asset_id, str) or not asset_id or asset_id in asset_ids:
            raise ValueError("Word图片引用编号必须唯一。")
        if asset["block_index"] not in positions:
            raise ValueError("Word图片引用指向不存在的区块。")
        asset_ids.add(asset_id)
    return blocks, positions


def _copied_blocks(
    preview: dict[str, Any], blocks: list[dict[str, Any]]
) -> list[dict[str, Any]]:
    by_block: dict[int, list[dict[str, Any]]] = {}
    for asset in preview.get("assets", []):
        by_block.setdefault(asset["block_index"], []).append(asset)
    copies = deepcopy(blocks)
    for block in copies:
        block["assets"] = deepcopy(by_block.get(block["index"], []))
    return copies


def _key(source_sha256: str, origin: int) -> str:
    return "word-question-" + _digest([source_sha256.lower(), origin])[:24]


def _heading(text: str) -> bool:
    if _marker(text):
        return False
    clean = _clean(text)
    if _HEADING.match(clean) or _PRACTICE.match(clean):
        return True
    numbered = _GENERIC.match(clean)
    if not numbered:
        return False
    body = numbered.group(2)
    # "判断方法" / "选择原则" are headings, not imperatives. Actual
    # question marks, answer spaces and explicit "请/试" prompts win.
    if not re.search(r"[?？]|_{2,}|\S[ \t\u3000]{3,}[。；;，,]|请|试(?:写|求|回答)|下列|已知|[（(]\s*(?:20\d{2}|\d{2}-\d{2})", body):
        if not re.match(r"^(?:写出|求出|回答|计算)", body) and _KNOWLEDGE_TITLE.fullmatch(body):
            return True
    return bool(not _QUESTION.search(body) and _KNOWLEDGE_NUMBER.match(body))


def _question_cue(text: str) -> bool:
    clean = _GAP.sub("", text).strip()
    clean = _PARENTHESIZED_PART.sub("", clean).lstrip()
    return bool(_QUESTION.search(clean))


def _question_label(
    blocks: list[dict[str, Any]], position: int, practice: bool
) -> str | None:
    text = blocks[position].get("text", "")
    explicit = _marker(text)
    if explicit:
        # A standalone editorial "例3" immediately before "【变式3-1】"
        # is not an extra empty exercise. An intervening image is not skipped.
        if _GAP.sub("", text).strip().strip("【】[]（）() .．、:：") == explicit:
            following = next((b.get("text", "") for b in blocks[position + 1:] if b.get("text", "").strip()), "")
            if _marker(following):
                return None
        return explicit
    match = _GENERIC.match(_GAP.sub("", text).strip())
    if match is None or _heading(text):
        return None
    if _question_cue(match.group(2)):
        return match.group(1)
    # An introductory stem can continue in the following blocks. Do not let
    # the evidence of the next numbered question turn a knowledge list into one.
    for block in blocks[position + 1 : position + 65]:
        following = _GAP.sub("", block.get("text", "")).strip()
        if (
            _marker(following)
            or _GENERIC.match(following)
            or _heading(following)
            or _ANSWER.search(following)
        ):
            break
        if _OPTION.search(following) or _question_cue(following):
            return match.group(1)
    return match.group(1) if practice else None


def _nested_numbered_parent(
    blocks: list[dict[str, Any]], position: int
) -> tuple[int, list[int]] | None:
    """Recognize numbered sections only when the original combined key agrees.

    E.g. question 27 introduces sections 1/2, followed by 【答案】1．...
    and 2．... . Keep that parent intact instead of inventing independent tasks.
    This does not attempt general distant-answer matching.
    """
    parent = _GENERIC.match(_GAP.sub("", blocks[position].get("text", "")).strip())
    if parent is None or int(parent.group(1)) < 2 or len(parent.group(2)) < 12:
        return None
    sections: list[tuple[int, int]] = []
    answer_at = None
    for offset in range(position + 1, min(len(blocks), position + 256)):
        text = _GAP.sub("", blocks[offset].get("text", "")).strip()
        answer = _ANSWER.match(text)
        if answer:
            key = _GENERIC.match(text[answer.end():].strip())
            if len(sections) >= 2 and key and int(key.group(1)) == sections[0][1]:
                answer_at = offset
            break
        if _marker(text) or _HEADING.match(_clean(text)):
            break
        number = _GENERIC.match(text)
        if number:
            label = int(number.group(1))
            if not sections:
                # Embedded original numbers need not restart at 1 (a source
                # excerpt labelled 7 may retain printed subparts 10 and 11).
                # Do not consume the ordinary next outer question N+1.
                if offset > position + 5 or label in (int(parent.group(1)), int(parent.group(1)) + 1):
                    return None
            elif label != sections[-1][1] + 1:
                return None
            sections.append((offset, label))
    if answer_at is None:
        return None
    child_numbers = {number for _, number in sections}
    end = len(blocks)
    for offset in range(answer_at + 1, len(blocks)):
        text = _GAP.sub("", blocks[offset].get("text", "")).strip()
        number = _GENERIC.match(text)
        if (_marker(text) or _HEADING.match(_clean(text)) or
                (number and int(number.group(1)) not in child_numbers)):
            end = offset
            break
    return end, [blocks[offset]["index"] for offset, _ in sections]


def _unmarked_choice_answer_start(blocks: list[dict[str, Any]]) -> int | None:
    """An adjacent repeated subpart label is a reviewable answer lead, not AI text."""
    options_seen = False
    for position, block in enumerate(blocks):
        text = block.get("text", "").strip()
        if _ANSWER.search(text):
            return None
        if _OPTION.search(text):
            options_seen = True
        choice = re.fullmatch(r"[（(](\d{1,2})[）)]\s*([A-HＡ-Ｈ]{1,8})", text)
        if not options_seen or choice is None or position + 1 == len(blocks):
            continue
        explanation = blocks[position + 1].get("text", "").strip()
        prefix = re.match(r"[（(](\d{1,2})[）)]\s*(.+)", explanation, re.DOTALL)
        if (prefix and prefix.group(1) == choice.group(1) and
                len(prefix.group(2)) >= 12 and not _question_cue(prefix.group(2))):
            return position
    return None


def _build(
    preview: dict[str, Any],
    selected: list[dict[str, Any]],
    context: list[dict[str, Any]],
    *,
    chapter: str,
    origin: int,
    manual: bool = False,
    question_end: int | None = None,
    answer_start: int | None = None,
    source_answer_indexes: set[int] | None = None,
) -> dict[str, Any]:
    copied = _copied_blocks(preview, selected)
    context_blocks = _copied_blocks(preview, context)
    warnings = {
        str(warning)
        for block in copied + context_blocks
        for warning in block.get("warnings", [])
    }
    question_blocks, answer_blocks = [], []
    split = False
    inferred_answer_at = _unmarked_choice_answer_start(copied)
    if manual:
        question_blocks = [block for block in copied if block["index"] <= question_end]
        answer_blocks = [
            block
            for block in copied
            if answer_start is not None and block["index"] >= answer_start
        ]
    else:
        in_answer = False
        for position, block in enumerate(copied):
            match = _ANSWER.search(block.get("text", "")) if not in_answer else None
            if not in_answer and position == inferred_answer_at:
                answer_blocks.append(block)
                in_answer = True
            elif match is not None:
                prefix = block["text"][: match.start()]
                if prefix.strip():
                    split = True
                    question_piece = deepcopy(block)
                    answer_piece = deepcopy(block)
                    for piece, start, end in (
                        (question_piece, 0, match.start()),
                        (answer_piece, match.start(), len(block["text"])),
                    ):
                        piece["source_text"] = block["text"]
                        piece["text"] = block["text"][start:end]
                        piece["text_range"] = [start, end]
                        piece["display_only_split"] = True
                    question_blocks.append(question_piece)
                    answer_blocks.append(answer_piece)
                else:
                    answer_blocks.append(block)
                in_answer = True
            elif in_answer:
                answer_blocks.append(block)
            else:
                question_blocks.append(block)
    if not question_blocks:
        raise ValueError("逐题范围没有题目区块。")
    question_text = "\n".join(block.get("text", "") for block in question_blocks)
    full_text = "\n".join(block.get("text", "") for block in copied)
    needs_review = split
    if inferred_answer_at is not None:
        warnings.add("原文未使用答案标记；已按选项后的独立选项字母及同号说明列出答案候选，须核对后使用。")
        needs_review = True
    if _BROKEN_VARIANT.match(_GAP.sub("", question_blocks[0].get("text", "")).strip()):
        warnings.add("原文变式题标签缺少闭括号，已保留原文并识别为独立题目候选，请核对题目边界。")
        needs_review = True
    if split or any(_ANSWER.search(block.get("text", "")) for block in question_blocks):
        warnings.add(
            "题目与答案处于同一来源区块，或题目范围仍含答案标记；须核对范围或内容后才能导出学生版。"
        )
        needs_review = True
    if any(
        _ANSWER.search(block.get("text", ""))
        or (
            source_answer_indexes is not None
            and block["index"] in source_answer_indexes
        )
        for block in context_blocks
    ):
        warnings.add(
            "共同材料范围包含答案或解析标记，或与来源中已识别的答案解析区块重叠；须核对范围或内容后才能导出学生版。"
        )
        needs_review = True
    if len(_ANY_EXPLICIT.findall(full_text)) > 1:
        warnings.add("所选范围包含多个明确题目标记，需核对是否应拆为多题。")
        needs_review = True
    if not context_blocks and (
        _BACK_REFERENCE.search(question_blocks[0].get("text", ""))
        or re.search(r"前文|上一题|前题", question_text)
    ):
        warnings.add("题目引用前文或前题，但未关联明确的共同材料；请核对材料范围。")
        needs_review = True
    label = _marker(question_blocks[0].get("text", ""))
    generic = _GENERIC.match(_GAP.sub("", question_blocks[0].get("text", "")).strip())
    label = label or (generic.group(1) if generic else "题目")
    title = re.sub(r"\s+", " ", question_blocks[0].get("text", "")).strip()
    if not _clean(title):
        title = label + "（原图或原表）"
    source_sha = preview["source_sha256"].lower()
    item = {
        "key": _key(source_sha, origin),
        "source_label": str(preview.get("source_name", "Word来源")),
        "source_sha256": source_sha,
        "source_revision": preview["revision"],
        "extraction_revision": preview.get("extraction_revision", "unknown"),
        "index_revision": QUESTION_INDEX_REVISION,
        "origin_block_start": origin,
        "chapter": chapter,
        "title": title[:180],
        "question_blocks": question_blocks,
        "answer_blocks": answer_blocks,
        "context_blocks": context_blocks,
        "warnings": sorted(warnings),
        "boundary_status": "needs_review"
        if needs_review
        else ("manual_range" if manual else "auto_detected"),
        "block_start": selected[0]["index"],
        "question_end": question_blocks[-1]["index"],
        "answer_start": answer_blocks[0]["index"] if answer_blocks else None,
        "block_end": selected[-1]["index"],
        "context_start": context_blocks[0]["index"] if context_blocks else None,
        "context_end": context_blocks[-1]["index"] if context_blocks else None,
        "selection_ready": True,
        "export_ready": not needs_review,
    }
    item["revision"] = _digest(item)
    return item


def index_word_questions(preview: dict[str, Any]) -> list[dict[str, Any]]:
    """Detect candidate questions; never turn bare knowledge numbering into one."""
    blocks, _ = _preview(preview)
    # Shanghai theme papers often number only their embedded tasks with (1),
    # (2), etc. Keep the whole theme as a selectable unit: all shared materials,
    # intermediate information and answers remain attached, never flattened
    # into apparently independent questions. Require scored printed headings
    # plus actual subparts/answer evidence, not a filename or lesson heading.
    starts = [i for i, block in enumerate(blocks) if _SCORED_THEME.fullmatch(_clean(block.get("text", "")))]
    if starts and any("考试时间" in b.get("text", "") and "满分" in b.get("text", "") for b in blocks[:starts[0]]):
        themes = []
        for number, start in enumerate(starts):
            end = starts[number + 1] if number + 1 < len(starts) else len(blocks)
            selected = blocks[start:end]
            answer_at = next((i for i, b in enumerate(selected) if _ANSWER.search(b.get("text", ""))), len(selected))
            subparts = [b["index"] for b in selected[1:answer_at] if _PARENTHESIZED_PART.match(_clean(b.get("text", "")))]
            if not subparts or answer_at == len(selected):
                themes = []
                break
            global_material = [b for b in blocks[:starts[0]] if "相对原子质量" in b.get("text", "") or "选择类试题" in b.get("text", "")]
            item = _build(preview, selected, global_material, chapter=_clean(blocks[start]["text"]), origin=blocks[start]["index"])
            item.update(selection_unit="theme_big_question", printed_subpart_starts=subparts,
                        shared_material_policy="whole_theme_preserved")
            item["revision"] = _digest({k: v for k, v in item.items() if k != "revision"})
            themes.append(item)
        if themes:
            return themes
    items = []
    active = None
    chapter = ""
    practice = False
    material_start = None
    shared_material = False
    context = []
    nested_until = 0

    def finish(end: int) -> None:
        nonlocal active
        if active is None:
            return
        start, heading, material = active
        while (
            end >= start
            and not blocks[end].get("text", "").strip()
            and not any(
                asset["block_index"] == blocks[end]["index"]
                for asset in preview.get("assets", [])
            )
        ):
            end -= 1
        if end >= start:
            items.append(
                _build(
                    preview,
                    blocks[start : end + 1],
                    material,
                    chapter=heading,
                    origin=blocks[start]["index"],
                )
            )
        active = None

    for position, block in enumerate(blocks):
        if position < nested_until:
            continue
        text = block.get("text", "")
        clean = _clean(text)
        nested = _nested_numbered_parent(blocks, position)
        if nested is not None:
            finish(position - 1)
            nested_until, section_starts = nested
            material = blocks[material_start:position] if material_start is not None else context
            item = _build(preview, blocks[position:nested_until], material,
                          chapter=chapter, origin=block["index"])
            item.update(nested_section_starts=section_starts,
                        shared_material_policy="whole_numbered_parent_preserved")
            item["revision"] = _digest({k: v for k, v in item.items() if k != "revision"})
            items.append(item)
            material_start = None
            if not shared_material:
                context = []
            continue
        if _MATERIAL.match(text.strip()):
            finish(position - 1)
            material_start = position
            shared_material = bool(_SHARED.search(text))
            context = []
            continue
        if _heading(text):
            finish(position - 1)
            practice = bool(_PRACTICE.match(clean))
            if (
                _HEADING.match(clean) or _PRACTICE.match(clean)
            ) and not clean.startswith(("必杀技", "温馨提示", "方法总结", "规律总结")):
                chapter = clean
            material_start, context = None, []
            continue
        label = _question_label(blocks, position, practice)
        if label is None:
            continue
        finish(position - 1)
        if material_start is not None:
            context = blocks[material_start:position]
            material_start = None
        active = (position, chapter, context)
        if not shared_material:
            context = []
    finish(len(blocks) - 1)
    return items


def apply_question_range(
    preview: dict[str, Any],
    item: dict[str, Any],
    *,
    block_start: int,
    question_end: int,
    answer_start: int | None,
    block_end: int,
    context_start: int | None = None,
    context_end: int | None = None,
) -> dict[str, Any]:
    """Rebuild an explicitly chosen whole-block range without changing its key.

    Question and answer ranges must be adjacent and non-overlapping. A context
    range must be disjoint from both. Display-only slices cannot authorize a
    student export of an original paragraph that still contains answer text.
    """
    blocks, positions = _preview(preview)
    if (
        not isinstance(item, dict)
        or item.get("source_sha256") != preview["source_sha256"].lower()
    ):
        raise ValueError("题目与预览不属于同一来源。")
    if item.get("source_revision") != preview["revision"] or item.get(
        "extraction_revision"
    ) != preview.get("extraction_revision", "unknown"):
        raise ValueError("Word预览已变化，请重新读取逐题列表。")
    origin = item.get("origin_block_start")
    if (
        type(origin) is not int
        or origin not in positions
        or item.get("key") != _key(item["source_sha256"], origin)
    ):
        raise ValueError("题目来源标识不一致。")
    saved = {key: value for key, value in item.items() if key != "revision"}
    if item.get("revision") != _digest(saved):
        raise ValueError("题目内容或边界版本不一致，请重新读取。")
    for value in (block_start, question_end, block_end):
        if type(value) is not int or value not in positions:
            raise ValueError("范围必须使用当前来源中存在的区块编号。")
    start, qend, end = (
        positions[value] for value in (block_start, question_end, block_end)
    )
    if not start <= qend <= end:
        raise ValueError("题目起止区块顺序无效。")
    if answer_start is None:
        if qend != end:
            raise ValueError("没有答案范围时，题目末尾必须等于整个范围末尾。")
    elif (
        type(answer_start) is not int
        or answer_start not in positions
        or positions[answer_start] != qend + 1
        or positions[answer_start] > end
    ):
        raise ValueError("题目与答案范围必须相邻且不能重叠。")
    if (context_start is None) != (context_end is None):
        raise ValueError("共同材料必须同时指定起止区块。")
    context = []
    if context_start is not None:
        if any(
            type(value) is not int or value not in positions
            for value in (context_start, context_end)
        ):
            raise ValueError("共同材料区块不存在。")
        cstart, cend = positions[context_start], positions[context_end]
        if cstart > cend or not (cend < start or cstart > end):
            raise ValueError("共同材料不能与题目或答案区块重叠。")
        context = blocks[cstart : cend + 1]
    # A continuation paragraph may have no answer marker of its own. Do not
    # allow relabelling a known answer block as student-facing shared material.
    source_answer_indexes = (
        {
            block["index"]
            for source_item in index_word_questions(preview)
            for block in source_item["answer_blocks"]
        }
        if context
        else set()
    )
    result = _build(
        preview,
        blocks[start : end + 1],
        context,
        chapter=str(item.get("chapter", "")),
        origin=origin,
        manual=True,
        question_end=question_end,
        answer_start=answer_start,
        source_answer_indexes=source_answer_indexes,
    )
    if item.get("selection_unit") == "theme_big_question":
        result.update(selection_unit="theme_big_question",
                      printed_subpart_starts=[b["index"] for b in result["question_blocks"] if _PARENTHESIZED_PART.match(_clean(b.get("text", "")))],
                      shared_material_policy="teacher_adjusted_theme_range")
        result["revision"] = _digest({k: v for k, v in result.items() if k != "revision"})
    return result
