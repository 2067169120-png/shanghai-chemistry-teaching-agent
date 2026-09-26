"""Read actual library sources into an isolated paper-to-preparation preview.

No provider, credential store, central writes or document generation are used.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from copy import deepcopy
from pathlib import Path

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
ROOT = Path(__file__).resolve().parents[4]
sys.path.insert(0, str(ROOT))


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--scope", default="master")
    parser.add_argument("--query", default="银镜")
    args = parser.parse_args()
    output = args.output.resolve()
    output.relative_to(ROOT / "runtime/deeptutor_shchem/qa")
    output.mkdir(parents=True, exist_ok=False)

    from PySide6.QtWidgets import QApplication

    from integrations.deeptutor_shchem_v1.desktop_facade import DesktopWorkbenchFacade
    from integrations.deeptutor_shchem_v1.desktop_paper_preparation import (
        paper_preparation_reference,
    )
    from integrations.deeptutor_shchem_v1.desktop_paths import DesktopPaths
    from integrations.deeptutor_shchem_v1.desktop_workbench.main_window import (
        WORKBENCH_STYLE,
        install_font_fallbacks,
    )
    from integrations.deeptutor_shchem_v1.desktop_workbench.paper_composer import (
        PaperComposerModel,
    )
    from integrations.deeptutor_shchem_v1.desktop_workbench.paper_preparation_dialog import (
        PaperPreparationDialog,
    )

    app = QApplication.instance() or QApplication([])
    app.setStyleSheet(WORKBENCH_STYLE)
    install_font_fallbacks()
    facade = DesktopWorkbenchFacade(
        DesktopPaths.from_workspace(
            ROOT, state_root=output / "isolated-personal-state"
        ),
        provider_store=object(),
    )
    try:
        results = facade.search_themes(scope=args.scope, query=args.query, limit=1)
        if not results.cards:
            raise RuntimeError("No matching real theme; no substitute fixture used")
        card = results.cards[0]
        facade.add_theme_to_basket(card)
        model = PaperComposerModel.from_basket(
            facade.basket(), catalog=facade.paper_theme_catalog(args.scope)
        )
        snapshot = model.draft_payload()
        before = deepcopy(snapshot)
        enriched = facade.preparation_paper_source_snapshot(snapshot)
        assert snapshot == before and model.draft_payload() == before
        reference = paper_preparation_reference(
            enriched, list(range(len(enriched["themes"])))
        )
        (output / "reference.txt").write_text(reference["materials"], encoding="utf-8")
        sources = [
            q.get("source_detail", {})
            for theme in enriched["themes"]
            for q in theme["questions"]
        ]
        screenshots = []
        for width in (420, 900):
            dialog = PaperPreparationDialog(enriched)
            dialog.resize(width, 800)
            dialog.show()
            app.processEvents()
            assert dialog.reference == reference
            assert dialog.minimumSizeHint().width() <= width
            assert dialog.preview.horizontalScrollBar().maximum() == 0
            for position in ("top", "bottom"):
                bar = dialog.preview.verticalScrollBar()
                bar.setValue(0 if position == "top" else bar.maximum())
                app.processEvents()
                path = output / f"reference-{width}-{position}.png"
                assert dialog.grab().save(str(path))
                screenshots.append(str(path))
            dialog.close()
        report = {
            "source_title": card.title_zh,
            "scope": args.scope,
            "theme_count": reference["theme_count"],
            "question_count": reference["question_count"],
            "source_parts_matched": sum(bool(part) for part in sources),
            "source_answers_with_text": sum(
                bool(part.get("reference_answer")) for part in sources
            ),
            "source_analyses_with_text": sum(
                bool(part.get("analysis")) for part in sources
            ),
            "warnings": reference["warnings"],
            "materials_characters": len(reference["materials"]),
            "source_revision": reference["source_revision"],
            "input_model_unchanged": True,
            "model_calls": 0,
            "credential_store_accessed": False,
            "screenshots": screenshots,
            "visual_review": "pending",
        }
        (output / "verification.json").write_text(
            json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8"
        )
        print(json.dumps(report, ensure_ascii=False))
    finally:
        facade.shutdown()


if __name__ == "__main__":
    main()
