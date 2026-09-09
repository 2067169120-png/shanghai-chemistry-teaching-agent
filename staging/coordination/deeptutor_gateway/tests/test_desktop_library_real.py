from __future__ import annotations

import os
import re
from pathlib import Path

import pytest

from integrations.deeptutor_shchem_v1.desktop_facade import (
    DesktopFacadeError,
    DesktopWorkbenchFacade,
)
from integrations.deeptutor_shchem_v1.desktop_paths import DesktopPaths

RUN_REAL_LIBRARY = os.environ.get("SHCHEM_RUN_REAL_LIBRARY_TESTS") == "1"
pytestmark = pytest.mark.skipif(
    not RUN_REAL_LIBRARY,
    reason="set SHCHEM_RUN_REAL_LIBRARY_TESTS=1 to read the pinned local library",
)


def _workspace_root() -> Path:
    for candidate in Path(__file__).resolve().parents:
        if (candidate / "sh-chem-db").is_dir() and (
            candidate / "integrations" / "deeptutor_shchem_v1"
        ).is_dir():
            return candidate
    raise AssertionError("real Shanghai chemistry workspace is unavailable")


def test_real_three_scope_theme_details_and_images(tmp_path: Path) -> None:
    """Exercise the real immutable readers without changing central state."""

    paths = DesktopPaths.from_workspace(
        _workspace_root(), state_root=tmp_path / "personal-state"
    )
    facade = DesktopWorkbenchFacade(paths)
    first_image = None
    try:
        for scope in ("master", "wave1", "supplemental"):
            result = facade.search_themes(scope=scope, limit=1)
            assert result.cards, scope
            card = result.cards[0]
            assert re.fullmatch(r"[0-9a-f]{64}", card.data_snapshot_id)

            detail = facade.library_theme_detail(card)
            images = [
                image
                for part in detail.parts
                for image in part.question_images
                if image.role == "question"
            ]
            assert detail.parts, scope
            assert images, scope
            assert all(image.role != "answer" for image in detail.shared_images)

            payload = facade.library_image(images[0])
            assert payload.startswith(b"\x89PNG\r\n\x1a\n")
            if first_image is None:
                first_image = images[0]

        # The facade intentionally retains only the two newest theme views.
        assert first_image is not None
        with pytest.raises(DesktopFacadeError, match="阅读会话已结束"):
            facade.library_image(first_image)
    finally:
        facade.shutdown()


def test_real_fengxian_grouped_units_survive_desktop_and_composer(
    tmp_path: Path,
) -> None:
    from integrations.deeptutor_shchem_v1.desktop_facade import _canonical_digest
    from integrations.deeptutor_shchem_v1.desktop_workbench.paper_composer import (
        PaperComposerModel,
    )
    from integrations.deeptutor_shchem_v1.fengxian2025_theme2_direct_visual_scan import (
        Fengxian2025Theme2DirectVisualScanReader,
    )

    root = _workspace_root()
    facade = DesktopWorkbenchFacade(
        DesktopPaths.from_workspace(root, state_root=tmp_path / "fengxian-state")
    )
    identity = _canonical_digest(
        {
            "scope": "master",
            "paper": "PAPER-ec0cd44bdbda483b6778",
            "theme": "THEME-a2dfa24386a5adb67e5b",
        }
    )
    source = Fengxian2025Theme2DirectVisualScanReader(root / "sh-chem-db")
    try:
        cards = facade.search_themes(scope="master", query="消毒剂", limit=20).cards
        card = next(card for card in cards if card.source_identity_sha256 == identity)
        detail = facade.library_theme_detail(card)
        assert card.atomic_total == 10  # Canonical Master inventory is not rewritten.
        assert card.display_atomic_units == len(detail.parts) == 13
        assert len({part.key for part in detail.parts}) == 13
        assert all(part.question_images for part in detail.parts)
        assert all(not part.availability_zh for part in detail.parts)
        q2 = source.detail("FX2025-EM-S2-Q2-P1")["minimal_atomic_units"]
        displayed = {part.key: part for part in detail.parts}
        for unit in q2:
            part = displayed[unit["atomic_part_id"]]
            assert part.summary_zh == unit["visible_summary_zh"]
            assert (
                part.reference_answer_zh
                == unit["reference_answer"]["reference_answer_text"]
            )
            image = part.question_images[0]
            assert image.node_id == "FX2025-EM-S2-Q2-P1"
            assert facade.library_image(image).startswith(b"\x89PNG\r\n\x1a\n")
        assert (
            displayed[q2[0]["atomic_part_id"]].reference_answer_zh
            != displayed[q2[1]["atomic_part_id"]].reference_answer_zh
        )

        facade.add_theme_to_basket(card)
        composer = PaperComposerModel.from_basket(
            facade.basket(),
            catalog=facade.paper_theme_catalog("master"),
            mode="daily_practice",
            title="奉贤分项接入验证",
        )
        questions = composer.themes[0].questions
        assert len(questions) == 13
        for index, question in enumerate(questions):
            question.score = index % 4 + 1
            question.answer_space = (0, 1, 4, 6)[index % 4]
        model = composer.make_preview()
        request = facade._paper_export_request(
            {"payload": {}, "preview_model": model}, []
        )
        assert len(request["atomic_settings"]) == 13
        assert list(request["atomic_settings"].values()) == [
            {"score": question.score, "answer_space_lines": question.answer_space}
            for question in questions
        ]
        assert len({question.key for question in questions}) == 13
        refs = [question["source_ref"] for question in model["themes"][0]["questions"]]
        assert len({ref["source_atomic_part_id"] for ref in refs}) == 13
    finally:
        facade.shutdown()
