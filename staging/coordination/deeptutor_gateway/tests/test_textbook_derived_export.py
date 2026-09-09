"""Synthetic offline tests: derived references never promote central records."""

from __future__ import annotations

import copy
import hashlib
import importlib.util
import json
from pathlib import Path

import pytest

SCRIPT = (
    Path(__file__).resolve().parents[1]
    / "scripts/export_textbook_teaching_knowledge.py"
)
SPEC = importlib.util.spec_from_file_location("textbook_derived_export", SCRIPT)
assert SPEC and SPEC.loader
exporter = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(exporter)


def _write(path, value, *, jsonl=False):
    if jsonl:
        path.write_text(
            "".join(json.dumps(row, ensure_ascii=False) + "\n" for row in value),
            encoding="utf-8",
        )
    else:
        path.write_text(json.dumps(value, ensure_ascii=False), encoding="utf-8")


@pytest.fixture
def source(tmp_path):
    root = tmp_path / "central-candidate"
    root.mkdir()
    gates = dict.fromkeys(exporter.GATES, False)
    status = {"candidate_only": True, "review_status": "candidate-only", **gates}
    sha = "a" * 64
    concept = {
        "concept_id": "CON-001",
        "title": "水的电离",
        "statement": "水的电离吸热；升温后纯水仍呈中性",
        "volume_id": "TB-M1",
        "chapter_id": "TB-M1-C01",
        "section_key": "TB-M1-C01-S01",
        "source_sha256": sha,
        "pdf_pages": [2, 3],
        "tags": {"K": ["K001"], "A": [], "C": [], "R": [], "D": []},
        "evidence_refs": ["E001"],
        "verification_status": "legacy_candidate_claim",
        **status,
        # Non-allowlisted private metadata must never leak into the public copy.
        "original_path": "C:\\private\\original.pdf",
    }
    values = {
        "concepts.jsonl": [concept],
        "manifest.json": {
            "product_goal_path": "C:\\private\\goal.txt",
            "sources": [
                {
                    "source_id": "TB-M1",
                    "kind": "textbook",
                    "title": "化学必修第一册",
                    "sha256": sha,
                    "page_count": 10,
                    "path": "C:\\private\\original.pdf",
                    "source_status": "source_local_claim",
                },
                {
                    "source_id": "COURSE",
                    "kind": "course_standard",
                    "title": "不导出标准原文",
                },
            ],
        },
        "textbook_directory.json": {
            **status,
            "volumes": [
                {
                    "volume_id": "TB-M1",
                    "volume_title": "必修第一册",
                    "source_sha256": sha,
                    "chapters": [
                        {
                            "chapter_id": "TB-M1-C01",
                            "volume_id": "TB-M1",
                            "chapter_title": "第一章",
                            "sections": [
                                {
                                    "section_key": "TB-M1-C01-S01",
                                    "chapter_id": "TB-M1-C01",
                                    "section_title": "第一节",
                                },
                                {
                                    "section_key": "TB-M1-C01-S02",
                                    "chapter_id": "TB-M1-C01",
                                    "section_title": "第二节",
                                },
                            ],
                        },
                    ],
                },
            ],
        },
        "coverage_report.json": {"known_gaps": ["尚未教师复核"]},
        "blocked_items.jsonl": [
            {
                "blocker_id": "B001",
                "volume_id": "TB-M1",
                "issue": "待核验公式",
                "status": "blocked",
                "path": "C:\\private\\scan.png",
            }
        ],
    }
    for name, value in values.items():
        _write(root / name, value, jsonl=name.endswith("jsonl"))
    taxonomy = tmp_path / "taxonomy.json"
    _write(
        taxonomy,
        {"dimensions": {"knowledge_points": [{"id": "K001", "name": "水的电离"}]}},
    )
    return root, taxonomy, values


def _change(source, filename, mutate):
    root, _, values = source
    mutate(values[filename])
    _write(root / filename, values[filename], jsonl=filename.endswith("jsonl"))


def test_readable_deterministic_export_preserves_nonpromotion_and_conditions(
    source, tmp_path
):
    root, taxonomy, _ = source
    before = {p.name: p.read_bytes() for p in root.iterdir()}
    records, coverage, raw = exporter.build_export(root, taxonomy)
    assert exporter.build_export(root, taxonomy) == (records, coverage, raw)
    row = records[0]
    assert "电离吸热" in row["summary"] and "纯水仍呈中性" in row["summary"]
    assert row["tags"]["K"] == [{"id": "K001", "label": "水的电离"}]
    assert row["source"]["pdf_pages"] == [2, 3]
    assert row["source"]["printed_pages"] is None
    assert row["grade"] == "unknown"
    assert row["source_status"]["fresh_source_review_performed"] is False
    assert row["source_status"]["verification_status_claim"] == "legacy_candidate_claim"
    assert row["user_authorized_derived_export"] is True
    assert row["review_status"] == "candidate-only"
    assert all(row[gate] is False for gate in exporter.GATES)
    assert all(coverage[gate] is False for gate in exporter.GATES)
    assert "original_path" not in raw.decode() and "C:" not in raw.decode()
    assert "C:" not in json.dumps(coverage)
    assert coverage["coverage_gaps"]["numbered_sections_without_concepts"] == [
        "TB-M1-C01-S02"
    ]
    assert coverage["counts"]["upstream_blockers"] == 1
    assert coverage["knowledge_sha256"] == hashlib.sha256(raw).hexdigest()
    out = tmp_path / "derived"
    assert exporter.export(root, taxonomy, out) == coverage
    assert (out / "knowledge.jsonl").read_bytes() == raw
    assert {p.name: p.read_bytes() for p in root.iterdir()} == before


@pytest.mark.parametrize("gate", exporter.GATES)
def test_refuses_silently_changing_source_authority(source, gate):
    _change(source, "concepts.jsonl", lambda rows: rows[0].update({gate: True}))
    with pytest.raises(ValueError, match="candidate status"):
        exporter.build_export(source[0], source[1])


@pytest.mark.parametrize(
    "field,value", [("candidate_only", False), ("review_status", "reviewed")]
)
def test_candidate_state_must_be_explicit(source, field, value):
    _change(source, "concepts.jsonl", lambda rows: rows[0].update({field: value}))
    with pytest.raises(ValueError, match="candidate status"):
        exporter.build_export(source[0], source[1])


@pytest.mark.parametrize("statement", ["原文" * 130, "第一章\n第二章", "", "   "])
def test_rejects_long_or_multiline_source_content(source, statement):
    _change(source, "concepts.jsonl", lambda rows: rows[0].update(statement=statement))
    with pytest.raises(ValueError, match="concise"):
        exporter.build_export(source[0], source[1])


@pytest.mark.parametrize(
    "private",
    [
        "C:\\Users\\someone\\private.txt",
        "/home/person/file",
        "file://private",
        "sk-" + "x" * 24,
        "Bearer " + "x" * 24,
    ],
)
def test_exported_text_rejects_private_paths_and_credentials(source, private):
    _change(source, "concepts.jsonl", lambda rows: rows[0].update(statement=private))
    with pytest.raises(ValueError, match="Private"):
        exporter.build_export(source[0], source[1])


@pytest.mark.parametrize("pages", [[], [0], [11], [True], [2.0], "2"])
def test_page_bounds_are_checked(source, pages):
    _change(source, "concepts.jsonl", lambda rows: rows[0].update(pdf_pages=pages))
    with pytest.raises(ValueError, match="pages"):
        exporter.build_export(source[0], source[1])


def test_invalid_section_or_digest_is_not_inferred(source):
    _change(
        source, "concepts.jsonl", lambda rows: rows[0].update(section_key="missing")
    )
    with pytest.raises(ValueError, match="section"):
        exporter.build_export(source[0], source[1])
    _change(
        source,
        "concepts.jsonl",
        lambda rows: rows[0].update(
            section_key="TB-M1-C01-S01", source_sha256="b" * 64
        ),
    )
    with pytest.raises(ValueError, match="mismatch"):
        exporter.build_export(source[0], source[1])


def test_duplicate_concept_is_rejected(source):
    _change(source, "concepts.jsonl", lambda rows: rows.append(copy.deepcopy(rows[0])))
    with pytest.raises(ValueError, match="Duplicate"):
        exporter.build_export(source[0], source[1])


def test_supplement_stays_separate_from_numbered_section(source):
    _change(
        source,
        "concepts.jsonl",
        lambda rows: rows[0].update(section_key=None, supplement_node_key="SUP-1"),
    )
    records, coverage, _ = exporter.build_export(source[0], source[1])
    assert records[0]["curriculum"]["section_title"] is None
    assert records[0]["curriculum"]["supplement_node_key"] == "SUP-1"
    assert coverage["coverage_gaps"]["concepts_without_numbered_section"] == ["CON-001"]


@pytest.mark.parametrize("relative", [".", "children", ".."])
def test_output_cannot_overwrite_source_tree_or_ancestor(source, relative):
    with pytest.raises(ValueError, match="separate"):
        exporter.export(source[0], source[1], source[0] / relative)


def test_bad_input_does_not_create_partial_output(source, tmp_path):
    _change(
        source, "concepts.jsonl", lambda rows: rows[0].update(statement="原文" * 130)
    )
    target = tmp_path / "bad-output"
    with pytest.raises(ValueError):
        exporter.export(source[0], source[1], target)
    assert not target.exists()


def test_checked_in_part_b_is_a_traceable_auxiliary_not_full_lesson():
    workspace = Path(__file__).resolve().parents[4]
    path = workspace / "knowledge/lectures/part-b.jsonl"
    cards = [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines()]
    assert len(cards) == 16
    assert {c["package_id"] for c in cards} == {f"PKG-{i:03}" for i in range(34, 65, 2)}
    for card in cards:
        assert card["id"] == "LECT-" + card["package_id"]
        assert card["review_status"] == "ai_distilled_pending_teacher_review"
        assert card["human_reviewed"] is False
        assert len(card["source_sha256"]) == 64
        assert set(card["source_sha256"]) <= set("0123456789abcdef")
        assert 4 <= len(card["knowledge"]) <= 8
        assert 1 <= len(card["methods"]) + len(card["pitfalls"]) <= 3
        for point in card["knowledge"] + card["methods"] + card["pitfalls"]:
            assert point["summary"] and "待查看原文" not in point["summary"]
            assert point["block_indices"] and all(
                type(n) is int and n > 0 for n in point["block_indices"]
            )
        exporter._safe(card)
