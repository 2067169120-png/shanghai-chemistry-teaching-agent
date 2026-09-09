"""Local navigation from derived lecture indexes to intact imported sources.

Summaries are search aids, never a replacement body or a source authority.
The original reader revalidates bytes when a teacher opens a result.
"""

from __future__ import annotations

import json
import re
from pathlib import Path

INDEX_FILES = ("part-a.jsonl", "part-b.jsonl", "part-c.jsonl")
GROUP_LABELS = {"knowledge": "知识线索", "methods": "解题方法", "pitfalls": "易错提醒"}


def _index_cards(workspace: Path):
    cards, warnings = {}, []
    for name in INDEX_FILES:
        path = workspace / "knowledge" / "lectures" / name
        if not path.is_file():
            warnings.append(f"{name} 知识索引未找到；仍可按原教案名称查找。")
            continue
        try:
            if path.stat().st_size > 4 * 1024 * 1024:
                raise ValueError("oversized index")
            rows = [
                json.loads(line)
                for line in path.read_text(encoding="utf-8-sig").splitlines()
                if line.strip()
            ]
            # Validate a file before using any of its rows; invalid data cannot
            # accidentally attach an index to an unrelated original document.
            checked = {}
            for row in rows:
                sha = row["source_sha256"]
                if (
                    not isinstance(sha, str)
                    or not re.fullmatch(r"[0-9a-f]{64}", sha)
                    or row.get("human_reviewed") is not False
                    or row.get("review_status") != "ai_distilled_pending_teacher_review"
                    or any(
                        not isinstance(row.get(key), str) or not row[key].strip()
                        for key in ("id", "title", "source_name")
                    )
                    or sha in checked
                    or sha in cards
                ):
                    raise ValueError("invalid source identity")
                for group in GROUP_LABELS:
                    if not isinstance(row.get(group), list):
                        raise TypeError("invalid claims")
                    for claim in row[group]:
                        if (
                            not isinstance(claim, dict)
                            or not isinstance(claim.get("summary"), str)
                            or not claim["summary"].strip()
                            or not isinstance(claim.get("block_indices"), list)
                            or not claim["block_indices"]
                            or any(
                                type(n) is not int or n < 1
                                for n in claim["block_indices"]
                            )
                        ):
                            raise ValueError("invalid block locator")
                checked[sha] = row
            cards.update(checked)
        except (OSError, ValueError, TypeError, KeyError):
            warnings.append(
                f"{name} 知识索引无法核对；本文件摘要未用于查找，原教案仍可打开。"
            )
    return cards, warnings


def lecture_catalog(facade):
    """List all imported originals; an index is optional and SHA/name-bound."""
    cards, warnings = _index_cards(Path(facade.paths.workspace_root))
    result = []
    for batch in facade.list_imported_word_batches():
        try:
            sources = facade.imported_word_sources(batch.batch_id)
        except (OSError, ValueError, RuntimeError):
            warnings.append(
                "有一批原教案目录暂时不可读取，请到导入历史核对；其他批次仍可使用。"
            )
            continue
        for source in sources:
            card = cards.get(source.get("source_sha256"))
            if card and card["source_name"] != source["source_name"]:
                card = None
            title = card["title"] if card else source["source_name"]
            lines = [
                f"原文件：{source['source_name']}",
                "打开后会重新核对原 Word；这里的索引只帮助定位，不替代原教案。",
            ]
            if card:
                lines.append("\nAI 整理的知识线索（待教师核对；不是教材原句）：")
                for group, label in GROUP_LABELS.items():
                    for claim in card[group]:
                        positions = "、".join(str(n) for n in claim["block_indices"])
                        lines.append(
                            f"\n{label} · 原文区块 {positions}\n{claim['summary']}"
                        )
            else:
                lines.append(
                    "\n这份资料没有知识摘要索引；完整原文、表格与内嵌图仍可预览。"
                )
            body = "\n".join(lines)
            result.append(
                {
                    "batch_id": batch.batch_id,
                    "source_id": source["source_id"],
                    "source_name": source["source_name"],
                    "title": title,
                    "indexed": card is not None,
                    "preview": body,
                    "search_text": (title + "\n" + body).casefold(),
                }
            )
    return {"items": result, "warnings": list(dict.fromkeys(warnings))}


def search_lectures(items, query):
    """All explicit search tokens must match; no semantic claim or label write."""
    terms = query.casefold().split()
    return [row for row in items if all(term in row["search_text"] for term in terms)]
