"""Canonical teacher-selected Word intervals without widening across gaps."""

from __future__ import annotations

MAX_WORD_RANGES = 32


def normalize_word_ranges(value):
    """Sort and merge only overlapping/adjacent intervals; never fill a gap."""
    if not isinstance(value, list) or not 1 <= len(value) <= MAX_WORD_RANGES * 4:
        raise ValueError(f"请加入1至{MAX_WORD_RANGES}段原文，再预览。")
    pairs = []
    for row in value:
        if (
            not isinstance(row, dict)
            or set(row) != {"start", "end"}
            or type(row["start"]) is not int
            or type(row["end"]) is not int
            or not 1 <= row["start"] <= row["end"]
        ):
            raise ValueError("原文选段范围不正确，请重新选择。")
        pairs.append((row["start"], row["end"]))
    merged = []
    for start, end in sorted(pairs):
        if merged and start <= merged[-1]["end"] + 1:
            merged[-1]["end"] = max(merged[-1]["end"], end)
        else:
            merged.append({"start": start, "end": end})
    if len(merged) > MAX_WORD_RANGES:
        raise ValueError(
            f"本次最多选择{MAX_WORD_RANGES}段不连续原文，请先移除不需要的段落。"
        )
    return merged


def word_range_label(ranges):
    return "；".join(
        str(row["start"])
        if row["start"] == row["end"]
        else f"{row['start']}—{row['end']}"
        for row in ranges
    )
