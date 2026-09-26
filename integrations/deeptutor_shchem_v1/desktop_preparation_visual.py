"""Explicit, editable slide structures; no chemistry or layout inference."""

from collections.abc import Mapping
from typing import Any


class ComparisonRowCountError(ValueError):
    """Safe, specific diagnostic without echoing model-authored cell text."""

    def __init__(self, row_number: int, expected: int, actual: int) -> None:
        self.message_zh = (
            f"比较表第{row_number}行有{actual}格内容，但表头有{expected}个数据列。"
            "行标题另占一列，不计入数据列；请核对表头与单元格的一一对应，"
            "不要直接补空格或截掉内容。"
        )
        super().__init__(self.message_zh)


def _object(properties: dict[str, Any]) -> dict[str, Any]:
    return {
        "type": "object",
        "properties": properties,
        "required": list(properties),
        "additionalProperties": False,
    }


def slide_visual_schema() -> dict[str, Any]:
    """Use the conservative strict-output vocabulary; bound sizes locally."""
    text = {"type": "string"}
    comparison = _object(
        {
            "dimension_label": text,
            "columns": {"type": "array", "items": text},
            "rows": {
                "type": "array",
                "items": _object(
                    {"label": text, "values": {"type": "array", "items": text}}
                ),
            },
        }
    )
    return {
        "anyOf": [
            {"type": "null"},
            _object(
                {
                    "kind": {"type": "string", "enum": ["comparison", "process"]},
                    "comparison": {"anyOf": [{"type": "null"}, comparison]},
                    "steps": {
                        "type": "array",
                        "items": _object({"label": text, "detail": text}),
                    },
                }
            ),
        ]
    }


def _keys(value: Any, expected: set[str]) -> Mapping[str, Any]:
    if not isinstance(value, Mapping) or set(value) != expected:
        raise ValueError("invalid visual object fields")
    return value


def _text(value: Any, limit: int = 32) -> str:
    if not isinstance(value, str) or not 1 <= len(value.strip()) <= limit:
        raise ValueError("invalid visual text length")
    return value.strip()


def _list(value: Any, minimum: int, maximum: int) -> list[Any]:
    if not isinstance(value, list) or not minimum <= len(value) <= maximum:
        raise ValueError("invalid visual item count")
    return value


def normalize_slide_visual(value: Any) -> dict[str, Any] | None:
    """Validate relationships without truncation, inferring facts or mutation.

    None is the legacy text layout. Comparisons share row dimensions; process
    nodes are an explicitly ordered reasoning/teaching sequence, not apparatus
    drawings or chemical reaction arrows. Long material must be split by the
    author rather than silently disappearing from a cell.
    """
    if value is None:
        return None
    value = _keys(value, {"kind", "comparison", "steps"})
    if value["kind"] == "comparison":
        _list(value["steps"], 0, 0)
        table = _keys(value["comparison"], {"dimension_label", "columns", "rows"})
        columns = [_text(item) for item in _list(table["columns"], 2, 3)]
        rows = []
        for row_number, row in enumerate(_list(table["rows"], 2, 4), 1):
            row = _keys(row, {"label", "values"})
            if isinstance(row["values"], list) and len(row["values"]) != len(columns):
                raise ComparisonRowCountError(
                    row_number, len(columns), len(row["values"])
                )
            rows.append(
                {
                    "label": _text(row["label"]),
                    "values": [
                        _text(item, 120)
                        for item in _list(row["values"], len(columns), len(columns))
                    ],
                }
            )
        return {
            "kind": "comparison",
            "comparison": {
                "dimension_label": _text(table["dimension_label"]),
                "columns": columns,
                "rows": rows,
            },
            "steps": [],
        }
    if value["kind"] == "process":
        if value["comparison"] is not None:
            raise ValueError("process cannot also contain a comparison")
        steps = []
        for step in _list(value["steps"], 2, 4):
            step = _keys(step, {"label", "detail"})
            steps.append(
                {"label": _text(step["label"]), "detail": _text(step["detail"], 120)}
            )
        return {"kind": "process", "comparison": None, "steps": steps}
    raise ValueError("unknown visual kind")
