"""Explicit column presets; changed spreadsheet layouts never apply silently."""

from __future__ import annotations

from copy import deepcopy
from uuid import uuid4

from .desktop_exam_data import ExamError, build_exam, digest, display
from .desktop_state import utc_now

SCHEMA = "shchem.exam-mapping-profiles.v1"


def _layout(book, config):
    sheet = next(
        (row for row in book["sheets"] if row["name"] == config.get("sheet")), None
    )
    header = config.get("header_row")
    if (
        sheet is None
        or type(header) is not int
        or not 1 <= header <= len(sheet["rows"])
    ):
        raise ExamError("映射方案对应的工作表或表头行不存在，请重新核对。")
    return {
        "sheet": sheet["name"],
        "header_row": header,
        "headers": [display(value).strip() for value in sheet["rows"][header - 1]],
    }


class ExamMappingProfiles:
    def __init__(self, state):
        self.state = state

    @staticmethod
    def _record(value):
        record = value.get("exam_mapping_profiles", {"schema": SCHEMA, "profiles": {}})
        if (
            not isinstance(record, dict)
            or record.get("schema") != SCHEMA
            or not isinstance(record.get("profiles"), dict)
        ):
            raise ExamError("成绩列映射方案无法读取，请核对备份。")
        for key, row in record["profiles"].items():
            if (
                not isinstance(row, dict)
                or row.get("id") != key
                or row.get("layout_sha256") != digest(row.get("layout"))
            ):
                raise ExamError("成绩列映射方案版本不完整，请核对备份。")
        return record

    def profiles(self):
        return sorted(
            deepcopy(list(self._record(self.state.snapshot())["profiles"].values())),
            key=lambda row: row["updated_at"],
            reverse=True,
        )

    def save(self, label, book, config, *, profile_id=None, expected_revision=None):
        if not isinstance(label, str) or not label.strip() or len(label) > 120:
            raise ExamError("请填写不超过120字的映射方案名称。")
        build_exam(book, config)
        layout = _layout(book, config)
        keys = (
            "sheet",
            "header_row",
            "student_col",
            "class_col",
            "total_col",
            "status_col",
            "max_score",
            "pass_score",
            "excellent_score",
            "questions",
        )
        params = {key: deepcopy(config[key]) for key in keys}
        result = None

        def edit(value):
            nonlocal result
            record = self._record(value)
            old = record["profiles"].get(profile_id)
            if profile_id is not None and old is None:
                raise ExamError("映射方案已不存在，请刷新。")
            if (old or {}).get("revision") != expected_revision:
                raise ExamError("映射方案已在其他窗口修改，请重新读取后核对。")
            pid = profile_id or uuid4().hex
            result = {
                "id": pid,
                "label": label.strip(),
                "params": params,
                "layout": layout,
                "layout_sha256": digest(layout),
                "source_sha256": book["source_sha256"],
                "revision": uuid4().hex,
                "updated_at": utc_now(),
                "previous_revisions": deepcopy(
                    (old or {}).get("previous_revisions", [])
                ),
            }
            if old:
                result["previous_revisions"].append(
                    {
                        key: deepcopy(old[key])
                        for key in (
                            "params",
                            "layout",
                            "layout_sha256",
                            "revision",
                            "source_sha256",
                        )
                    }
                )
                result["previous_revisions"] = result["previous_revisions"][-50:]
            record["profiles"][pid] = result
            value["exam_mapping_profiles"] = record

        self.state._update(edit)
        return deepcopy(result)

    def preview(self, profile_id, revision, book):
        profile = next(
            (row for row in self.profiles() if row["id"] == profile_id), None
        )
        if profile is None or profile["revision"] != revision:
            raise ExamError("映射方案已变化，请刷新后重新选择。")
        try:
            current = _layout(book, profile["params"])
        except ExamError:
            return {
                "profile": profile,
                "expected": profile["layout"],
                "exact": False,
                "current": None,
                "differences": ["工作表或表头行不存在"],
                "config": None,
            }
        expected = profile["layout"]
        differences = [
            {
                "column": index,
                "expected": expected["headers"][index]
                if index < len(expected["headers"])
                else None,
                "current": current["headers"][index]
                if index < len(current["headers"])
                else None,
            }
            for index in range(max(len(expected["headers"]), len(current["headers"])))
            if (
                expected["headers"][index] if index < len(expected["headers"]) else None
            )
            != (current["headers"][index] if index < len(current["headers"]) else None)
        ]
        exact = current == expected
        return {
            "profile": profile,
            "expected": expected,
            "exact": exact,
            "current": current,
            "differences": differences,
            "config": deepcopy(profile["params"]) if exact else None,
        }
