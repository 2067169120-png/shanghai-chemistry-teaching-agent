"""Local, explicit roster identities and frozen assignment/exam references."""

from __future__ import annotations

import csv
import io
import json
import re
from copy import deepcopy
from hashlib import sha256
from uuid import uuid4

from .desktop_state import DesktopStateError, utc_now
from .desktop_work_batches import WorkBatchStore, _signature

SCHEMA = "shchem.classroom-registry.v1"
STATUS_LABELS = {
    "unrecorded": "未录入",
    "submitted": "已交",
    "missing": "未交",
    "absent": "缺席",
    "exempt": "免交",
}


class ClassroomError(DesktopStateError):
    @property
    def message_zh(self):
        return str(self)


def _text(value, label, limit=120, required=False):
    if (
        not isinstance(value, str)
        or len(value) > limit
        or any(ord(c) < 32 for c in value)
        or (required and not value.strip())
    ):
        raise ClassroomError(label + "为空、过长或格式不正确。")
    return value.strip()


def _hash(value):
    return sha256(
        json.dumps(value, ensure_ascii=False, sort_keys=True, allow_nan=False).encode()
    ).hexdigest()


def exam_identity_anchor(exam):
    """Score corrections do not change identity; source/row changes do."""
    return _hash(
        {
            "id": exam["id"],
            "source": exam["source"],
            "config": exam["config"],
            "students": [
                {
                    key: row.get(key)
                    for key in ("id", "local_label", "class", "excel_row")
                }
                for row in exam["students"]
            ],
        }
    )


def preview_roster_csv(raw):
    if not isinstance(raw, bytes) or len(raw) > 1024 * 1024:
        raise ClassroomError("名册CSV需小于1MB。")
    try:
        reader = csv.DictReader(io.StringIO(raw.decode("utf-8-sig"), newline=""))
        headings = reader.fieldnames
        if headings not in (["label", "external_ref"], ["姓名或代号", "班内编号"]):
            raise ValueError()
        records = list(reader)
        if any(None in row for row in records):
            raise ValueError("CSV row has extra columns")
        rows = [
            {
                "label": _text(row[headings[0]], "学生姓名或代号", required=True),
                "external_ref": _text(row[headings[1]], "本班唯一编号"),
                "profile_id": "",
                "member_id": None,
            }
            for row in records
        ]
    except (UnicodeError, ValueError, TypeError, KeyError, csv.Error):
        raise ClassroomError(
            "请使用UTF-8 CSV，表头为“姓名或代号,班内编号”，每行一名学生。"
        ) from None
    refs = [row["external_ref"] for row in rows if row["external_ref"]]
    if not rows or len(rows) > 500 or len(set(refs)) != len(refs):
        raise ClassroomError(
            "名册需1至500人，填写的本班唯一编号不能重复；同名学生不会合并。"
        )
    return rows


class ClassroomRegistry:
    def __init__(self, facade):
        self.facade = facade
        self.state = facade._state

    @staticmethod
    def _record(value):
        record = value.get(
            "classroom_registry",
            {"schema": SCHEMA, "classes": {}, "works": {}, "exam_bindings": {}},
        )
        if (
            not isinstance(record, dict)
            or record.get("schema") != SCHEMA
            or any(
                not isinstance(record.get(key), dict)
                for key in ("classes", "works", "exam_bindings")
            )
        ):
            raise ClassroomError("班级与收交记录无法读取，请核对备份。")
        for section in ("classes", "works", "exam_bindings"):
            for key, item in record[section].items():
                if (
                    not isinstance(item, dict)
                    or item.get("id") != key
                    or not re.fullmatch(r"[0-9a-f]{32}", key)
                    or not isinstance(item.get("revision"), str)
                ):
                    raise ClassroomError("班级或任务身份记录损坏，未修改原记录。")
                if section in {"classes", "works"}:
                    members = item.get("members")
                    if (
                        not isinstance(members, list)
                        or not 1 <= len(members) <= 500
                        or any(
                            not isinstance(row, dict)
                            or not isinstance(row.get("member_id"), str)
                            or not re.fullmatch(r"[0-9a-f]{32}", row["member_id"])
                            or any(
                                not isinstance(row.get(field), str)
                                for field in ("label", "external_ref", "profile_id")
                            )
                            for row in members
                        )
                        or len({row["member_id"] for row in members}) != len(members)
                    ):
                        raise ClassroomError("班级名单身份不完整，未修改原记录。")
                    if section == "works":
                        entries = item.get("entries")
                        if (
                            not isinstance(entries, dict)
                            or set(entries) != {row["member_id"] for row in members}
                            or any(
                                not isinstance(row, dict)
                                or row.get("status") not in STATUS_LABELS
                                or any(
                                    not isinstance(row.get(field), str)
                                    for field in (
                                        "submission_id",
                                        "note",
                                        "source_signature",
                                    )
                                )
                                for row in entries.values()
                            )
                        ):
                            raise ClassroomError("作业收交记录不完整，未修改原记录。")
                elif (
                    not isinstance(item.get("links"), dict)
                    or not isinstance(item.get("class_revisions"), dict)
                    or any(
                        not isinstance(row, dict)
                        or not isinstance(row.get("class_id"), str)
                        or not isinstance(row.get("member_id"), str)
                        for row in item["links"].values()
                    )
                ):
                    raise ClassroomError("考试与名册的关联记录不完整，未修改原记录。")
        return record

    def snapshot(self):
        return deepcopy(self._record(self.state.snapshot()))

    def classes(self):
        return sorted(
            self.snapshot()["classes"].values(),
            key=lambda row: row["updated_at"],
            reverse=True,
        )

    @staticmethod
    def _expect(current, expected):
        if (current or {}).get("revision") != expected:
            raise ClassroomError(
                "记录已被其他窗口修改，请重新读取后核对；当前输入保留。"
            )

    def save_class(
        self, label, term, members, *, class_id=None, expected_revision=None
    ):
        label, term = _text(label, "班级名称", 100, required=True), _text(term, "学期")
        if not isinstance(members, list) or not 1 <= len(members) <= 500:
            raise ClassroomError("每个班级需1至500名学生。")
        known_profiles = {row.student_id for row in self.facade.student_profiles()}
        result = None

        def edit(value):
            nonlocal result
            registry = self._record(value)
            previous = registry["classes"].get(class_id)
            if class_id is not None and previous is None:
                raise ClassroomError("班级记录已不存在，请刷新。")
            self._expect(previous, expected_revision)
            old_ids = {row["member_id"] for row in (previous or {}).get("members", [])}
            selected, refs, ids, profiles = [], set(), set(), set()
            for raw in members:
                if not isinstance(raw, dict):
                    raise ClassroomError("学生记录格式不正确。")
                mid = raw.get("member_id")
                if mid is not None and mid not in old_ids:
                    raise ClassroomError("不能把其他班级的成员身份写入本班。")
                mid = mid or uuid4().hex
                name = _text(raw.get("label"), "学生姓名或代号", required=True)
                ref = _text(raw.get("external_ref", ""), "本班唯一编号")
                profile = _text(raw.get("profile_id", ""), "匿名档案标识")
                if (
                    mid in ids
                    or (ref and ref in refs)
                    or (profile and profile in profiles)
                    or (profile and profile not in known_profiles)
                ):
                    raise ClassroomError(
                        "成员身份、本班编号或匿名档案重复/失效，请明确核对；同名不自动合并。"
                    )
                ids.add(mid)
                if ref:
                    refs.add(ref)
                if profile:
                    profiles.add(profile)
                selected.append(
                    {
                        "member_id": mid,
                        "label": name,
                        "external_ref": ref,
                        "profile_id": profile,
                    }
                )
            cid = class_id or uuid4().hex
            history = deepcopy((previous or {}).get("history", []))
            if previous:
                history.append(
                    {
                        key: deepcopy(previous[key])
                        for key in (
                            "label",
                            "term",
                            "members",
                            "revision",
                            "updated_at",
                        )
                    }
                )
            result = {
                "id": cid,
                "label": label,
                "term": term,
                "members": selected,
                "revision": uuid4().hex,
                "updated_at": utc_now(),
                "history": history[-50:],
            }
            registry["classes"][cid] = result
            value["classroom_registry"] = registry

        self.state._update(edit)
        return deepcopy(result)

    def works(self, class_id):
        return sorted(
            (
                row
                for row in self.snapshot()["works"].values()
                if row["class_id"] == class_id
            ),
            key=lambda row: row["updated_at"],
            reverse=True,
        )

    def create_work(self, class_id, title, due_date="", *, expected_class_revision):
        title = _text(title, "作业名称", required=True)
        due_date = _text(due_date, "截止日期", 10)
        if due_date:
            from datetime import date

            try:
                date.fromisoformat(due_date)
            except ValueError:
                raise ClassroomError("截止日期请使用YYYY-MM-DD。") from None
        result = None

        def edit(value):
            nonlocal result
            registry = self._record(value)
            classroom = registry["classes"].get(class_id)
            if classroom is None:
                raise ClassroomError("请先保存班级名册。")
            self._expect(classroom, expected_class_revision)
            wid = uuid4().hex
            result = {
                "id": wid,
                "class_id": class_id,
                "class_revision": classroom["revision"],
                "class_label": classroom["label"],
                "title": title,
                "due_date": due_date,
                "members": deepcopy(classroom["members"]),
                "entries": {
                    row["member_id"]: {
                        "status": "unrecorded",
                        "submission_id": "",
                        "source_signature": "",
                        "note": "",
                    }
                    for row in classroom["members"]
                },
                "revision": uuid4().hex,
                "updated_at": utc_now(),
                "history": [],
            }
            registry["works"][wid] = result
            value["classroom_registry"] = registry

        self.state._update(edit)
        return deepcopy(result)

    def save_work(self, work_id, entries, *, expected_revision):
        current = self.snapshot()["works"].get(work_id)
        if (
            current is None
            or not isinstance(entries, dict)
            or set(entries) != set(current["entries"])
        ):
            raise ClassroomError("收交记录需对应本任务建单时的全部学生。")
        self._expect(current, expected_revision)
        selected = {}
        for member in current["members"]:
            entry = entries[member["member_id"]]
            if not isinstance(entry, dict) or entry.get("status") not in STATUS_LABELS:
                raise ClassroomError("请选择有效收交状态。")
            status = entry["status"]
            sub = _text(entry.get("submission_id", ""), "作答标识")
            note = _text(entry.get("note", ""), "状态依据", 1000)
            signature = ""
            if status == "submitted":
                if not sub or not member["profile_id"]:
                    raise ClassroomError(
                        "已交状态必须先关联该学生的匿名档案和实际作答。"
                    )
                try:
                    summary = self.facade.student_submission(
                        student_id=member["profile_id"], submission_id=sub
                    )
                except (ValueError, RuntimeError, OSError, KeyError, TypeError) as exc:
                    raise ClassroomError(
                        getattr(
                            exc, "message_zh", "该作答不能归入当前学生，请核对归属。"
                        )
                    ) from exc
                if (summary.student_id, summary.submission_id) != (
                    member["profile_id"],
                    sub,
                ):
                    raise ClassroomError("作答归属不一致。")
                signature = _signature(summary)
            elif sub:
                raise ClassroomError(
                    "未交/缺席/免交等状态不得保留已交作答引用，请明确重新选择。"
                )
            if status in {"missing", "absent", "exempt"} and not note:
                raise ClassroomError("未交、缺席和免交请填写简要依据。")
            selected[member["member_id"]] = {
                "status": status,
                "submission_id": sub,
                "source_signature": signature,
                "note": note,
            }
        result = None

        def edit(value):
            nonlocal result
            registry = self._record(value)
            work = registry["works"].get(work_id)
            self._expect(work, expected_revision)
            history = work.setdefault("history", [])
            history.append(
                {
                    "entries": deepcopy(work["entries"]),
                    "revision": work["revision"],
                    "updated_at": work["updated_at"],
                }
            )
            work.update(entries=selected, revision=uuid4().hex, updated_at=utc_now())
            work["history"] = history[-100:]
            result = deepcopy(work)
            value["classroom_registry"] = registry

        self.state._update(edit)
        return result

    def work_overview(self, work_id):
        registry = self.snapshot()
        work = registry["works"].get(work_id)
        if work is None:
            raise ClassroomError("作业记录不存在，请刷新。")
        counts = dict.fromkeys(STATUS_LABELS, 0)
        rows, invalid = [], 0
        for member in work["members"]:
            entry = work["entries"][member["member_id"]]
            valid, issue = True, ""
            if entry["status"] == "submitted":
                try:
                    live = self.facade.student_submission(
                        student_id=member["profile_id"],
                        submission_id=entry["submission_id"],
                    )
                    valid = _signature(live) == entry["source_signature"]
                except (ValueError, RuntimeError, OSError, KeyError, TypeError):
                    valid = False
                if not valid:
                    issue = "原作答引用已变化或不可读，请重新核对"
                    invalid += 1
            if valid:
                counts[entry["status"]] += 1
            rows.append({**member, **entry, "binding_valid": valid, "issue": issue})
        classroom = registry["classes"].get(work["class_id"])
        return {
            "work": work,
            "rows": rows,
            "counts": counts,
            "roster_total": len(rows),
            "expected": len(rows) - counts["exempt"],
            "invalid_references": invalid,
            "roster_changed": classroom is None
            or classroom["revision"] != work["class_revision"],
        }

    def make_review_batch(self, work_id, expected_revision):
        overview = self.work_overview(work_id)
        self._expect(overview["work"], expected_revision)
        if overview["invalid_references"]:
            raise ClassroomError("有作答引用失效，请先核对，未建立批改批次。")
        members = [
            {"student_id": row["profile_id"], "submission_id": row["submission_id"]}
            for row in overview["rows"]
            if row["status"] == "submitted"
        ]
        if not members:
            raise ClassroomError("目前没有可进入批改的已交作答。")
        scope = _hash(members)
        store = WorkBatchStore(self.facade)
        existing = store.get_batch(overview["work"].get("review_batch_id"))
        if (
            existing
            and existing["members"] == members
            and overview["work"].get("review_scope_sha256") == scope
        ):
            return existing
        batch = store.save_batch(
            overview["work"]["title"], overview["work"]["class_label"], members
        )

        def edit(value):
            registry = self._record(value)
            work = registry["works"].get(work_id)
            self._expect(work, expected_revision)
            work.update(review_batch_id=batch["batch_id"], review_scope_sha256=scope)
            value["classroom_registry"] = registry

        # If the roster desk changed meanwhile, do not claim the new batch is
        # current. Its explicitly selected snapshot remains in batch history.
        self.state._update(edit)
        return batch

    def bind_exam(
        self,
        exam,
        class_id,
        pairs,
        *,
        expected_class_revision,
        expected_revision=None,
        clear_existing=False,
    ):
        anchor = exam_identity_anchor(exam)
        if (
            not re.fullmatch(r"[0-9a-f]{32}", exam["id"])
            or not isinstance(pairs, dict)
            or any(
                not isinstance(k, str) or not isinstance(v, str)
                for k, v in pairs.items()
            )
            or set(pairs) - {row["id"] for row in exam["students"]}
        ):
            raise ClassroomError("请选择本次考试中的学生行与明确班级成员。")
        result = None

        def edit(value):
            nonlocal result
            registry = self._record(value)
            classroom = registry["classes"].get(class_id)
            if classroom is None:
                raise ClassroomError("班级已不存在。")
            self._expect(classroom, expected_class_revision)
            old = registry["exam_bindings"].get(exam["id"])
            self._expect(old, expected_revision)
            if old and old["exam_anchor"] != anchor and not clear_existing:
                raise ClassroomError(
                    "考试来源或学生行已变化；请明确勾选清除旧关联后重新核对。"
                )
            allowed = {row["member_id"] for row in classroom["members"]}
            if len(set(pairs.values())) != len(pairs) or set(pairs.values()) - allowed:
                raise ClassroomError(
                    "同一考试不能把两行关联为同一学生，也不能使用其他班级成员。"
                )
            links = (
                {}
                if clear_existing
                else {
                    key: deepcopy(link)
                    for key, link in (old or {}).get("links", {}).items()
                    if link["class_id"] != class_id
                }
            )
            if set(links).intersection(pairs):
                raise ClassroomError(
                    "所选考试行已关联其他班级；先在原班级取消该关联再重新选择。"
                )
            links.update(
                {
                    key: {"class_id": class_id, "member_id": mid}
                    for key, mid in pairs.items()
                }
            )
            revisions = (
                {}
                if clear_existing
                else deepcopy((old or {}).get("class_revisions", {}))
            )
            revisions[class_id] = classroom["revision"]
            revisions = {
                key: rev
                for key, rev in revisions.items()
                if any(link["class_id"] == key for link in links.values())
            }
            result = {
                "id": exam["id"],
                "class_revisions": revisions,
                "exam_anchor": anchor,
                "links": links,
                "revision": uuid4().hex,
                "updated_at": utc_now(),
                "history": deepcopy((old or {}).get("history", [])),
            }
            if old:
                result["history"].append(
                    {
                        key: deepcopy(old[key])
                        for key in (
                            "class_revisions",
                            "exam_anchor",
                            "links",
                            "revision",
                        )
                    }
                )
                result["history"] = result["history"][-50:]
            registry["exam_bindings"][exam["id"]] = result
            value["classroom_registry"] = registry

        self.state._update(edit)
        return deepcopy(result)

    def exam_binding(self, exam):
        registry = self.snapshot()
        binding = registry["exam_bindings"].get(exam["id"])
        if binding is None:
            return {
                "binding": None,
                "valid": False,
                "reason": "本次考试尚未明确关联名册",
            }
        anchor_valid = binding["exam_anchor"] == exam_identity_anchor(exam)
        rows = {}
        for key, link in binding["links"].items():
            classroom = registry["classes"].get(link["class_id"])
            member = next(
                (
                    row
                    for row in (classroom or {}).get("members", [])
                    if row["member_id"] == link["member_id"]
                ),
                None,
            )
            valid = (
                anchor_valid
                and member is not None
                and classroom["revision"]
                == binding["class_revisions"].get(link["class_id"])
            )
            rows[key] = {
                **link,
                "valid": valid,
                "member": member if valid else None,
                "class_label": classroom["label"] if valid else "",
            }
        valid = bool(rows) and all(row["valid"] for row in rows.values())
        return {
            "binding": binding,
            "valid": valid,
            "reason": (
                "已由教师明确关联"
                if valid
                else "来源/名册版本已变化，需重新核对"
                if rows
                else "本次考试尚未明确关联名册"
            ),
            "rows": rows,
            "anchor_valid": anchor_valid,
        }
