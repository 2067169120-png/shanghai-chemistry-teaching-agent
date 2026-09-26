"""Offline regression tests for the v15 live teacher brief.

These tests exercise only the brief-building script with a fake facade.  They
intentionally do not read the real Word/PDF/image sources, call a provider, or
write a live-run receipt.
"""

from __future__ import annotations

import copy
import importlib.util
from pathlib import Path


def _module(monkeypatch):
    scripts = Path(__file__).parents[1] / "scripts"
    monkeypatch.syspath_prepend(str(scripts))
    spec = importlib.util.spec_from_file_location(
        "verify_real_source_preparation_live_excerpt",
        scripts / "verify_real_source_preparation.py",
    )
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(module)
    return module


class _OfflineFacade:
    """Small source/reference seam; no filesystem or provider access."""

    excerpt_text = "电解质在水溶液中或熔融状态下，形成可以自由移动离子的过程称为电离。"

    def __init__(self, *, include_excerpt=False):
        self.include_excerpt = include_excerpt
        self.reference_calls = []
        self.image_calls = []

    def preparation_word_preview(self, path):
        self.word_path = path
        return {"source_sha256": "word-sha-locked"}

    def preparation_concept_options(self, query):
        assert query == "TB-M1-C2-S22-C0"
        return [
            {
                "concept_id": concept_id,
                "revision": revision,
            }
            for concept_id, revision in (
                ("TB-M1-C2-S22-C05", "revision-c05"),
                ("TB-M1-C2-S22-C06", "revision-c06"),
                ("TB-M1-C2-S22-C07", "revision-c07"),
            )
        ]

    def preparation_source_reference(
        self, word_path, word_sha, block_start, block_end, selected, **kwargs
    ):
        self.reference_calls.append(
            {
                "word_path": word_path,
                "word_sha": word_sha,
                "block_start": block_start,
                "block_end": block_end,
                "selected": copy.deepcopy(selected),
                "kwargs": copy.deepcopy(kwargs),
            }
        )
        materials = (
            "SOURCE-OVERRIDE\nWord区块3缺口：公式未读取；保留来源警告和教师选择。"
        )
        if self.include_excerpt:
            materials += "\n" + self.excerpt_text
        return {
            "materials": materials,
            "warnings": ["Word区块3：公式未读取"],
        }

    def import_preparation_image(self, *args):
        self.image_calls.append(args)
        return {
            "asset_id": "asset-textbook-2-14",
            "caption": args[1],
            "source": args[2],
            "use": args[3],
        }


def _offline_teacher_payload(monkeypatch, *, textbook_excerpts):
    module = _module(monkeypatch)
    # Avoid reading even the fixed source paths in this offline seam.
    monkeypatch.setattr(module, "digest", lambda path: module.EXPECTED[path])
    facade = _OfflineFacade(include_excerpt=textbook_excerpts)
    payload, reference = module.teacher_payload(
        facade, textbook_excerpts=textbook_excerpts
    )
    return module, facade, payload, reference


def test_v15_passes_exact_c06_excerpt_revision_page_and_source_hash(monkeypatch):
    module, facade, payload, reference = _offline_teacher_payload(
        monkeypatch, textbook_excerpts=True
    )

    assert len(facade.reference_calls) == 1
    call = facade.reference_calls[0]
    assert call["word_sha"] == "word-sha-locked"
    assert (call["block_start"], call["block_end"]) == (42, 63)
    assert call["selected"] == [
        {"concept_id": "TB-M1-C2-S22-C05", "revision": "revision-c05"},
        {"concept_id": "TB-M1-C2-S22-C06", "revision": "revision-c06"},
        {"concept_id": "TB-M1-C2-S22-C07", "revision": "revision-c07"},
    ]
    assert call["kwargs"] == {
        "textbook_excerpts": [
            {
                "concept_id": "TB-M1-C2-S22-C06",
                "revision": "revision-c06",
                "source_sha256": module.EXPECTED[module.BOOK],
                "pdf_page": 62,
                "text": (
                    "电解质在水溶液中或熔融状态下，形成可以自由移动离子的过程称为电离。"
                ),
                "confirmed": True,
            }
        ]
    }
    assert reference["warnings"] == ["Word区块3：公式未读取"]
    assert payload["image_assets"] == [
        {
            "asset_id": "asset-textbook-2-14",
            "caption": "图2.14 氯化钠电离过程示意图",
            "source": "沪科技化学必修第一册 印刷第57页（PDF第62页）图2.14",
            "use": "观察晶体、溶于水、熔融两条路径；比较离子存在与能否自由移动。图有水分子、水合离子、钠离子和氯离子图例，不是实验录像。",
        }
    ]


def test_new_brief_keeps_chapter_name_and_does_not_duplicate_manual_excerpt(
    monkeypatch,
):
    module, _, payload, _ = _offline_teacher_payload(
        monkeypatch, textbook_excerpts=True
    )
    before = copy.deepcopy(payload)
    revised = module.chapter_notes_payload(payload, include_legacy_quote=False)

    quote = "电解质在水溶液中或熔融状态下，形成可以自由移动离子的过程称为电离。"
    assert payload == before
    assert revised["topic"] == "电解质的电离"
    assert revised["materials"].count(quote) == 1
    assert "本轮核对的教材原文" not in revised["materials"]
    assert (
        "首页标题为教材小节名‘电解质的电离’"
        in revised["advanced"]["template_and_delivery"]
    )
    assert "SOURCE-OVERRIDE" in revised["materials"]
    assert "Word区块3缺口：公式未读取" in revised["materials"]


def test_legacy_quote_default_remains_immutable_and_preserves_existing_overrides(
    monkeypatch,
):
    module, _, payload, reference = _offline_teacher_payload(
        monkeypatch, textbook_excerpts=False
    )
    before = copy.deepcopy(payload)
    revised = module.chapter_notes_payload(payload)

    quote = "电解质在水溶液中或熔融状态下，形成可以自由移动离子的过程称为电离。"
    assert payload == before
    assert revised["topic"] == "电解质的电离"
    assert revised["materials"].count(quote) == 1
    assert "SOURCE-OVERRIDE" in revised["materials"]
    assert reference["warnings"] == ["Word区块3：公式未读取"]
