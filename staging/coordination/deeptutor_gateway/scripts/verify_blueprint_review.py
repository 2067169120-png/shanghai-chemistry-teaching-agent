"""One explicit real review of a known saved blueprint, in isolated state."""

from __future__ import annotations

import argparse
import json
import sys
import tempfile
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[4]
sys.path.insert(0, str(ROOT))

from integrations.deeptutor_shchem_v1.desktop_blueprint_review import (
    candidate_revision,
    review_prompt,
)
from integrations.deeptutor_shchem_v1.desktop_facade import DesktopWorkbenchFacade
from integrations.deeptutor_shchem_v1.desktop_paths import DesktopPaths
from integrations.deeptutor_shchem_v1.desktop_state import DesktopStateStore
from integrations.deeptutor_shchem_v1.model_provider_settings import (
    ModelProviderSettingsStore,
)


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--source-state", type=Path, required=True)
    parser.add_argument("--preview-id", required=True)
    parser.add_argument("--execute", action="store_true")
    parser.add_argument("--ui", action="store_true")
    parser.add_argument(
        "--review-state",
        type=Path,
        help="Resume an isolated QA state, never the default user state",
    )
    parser.add_argument("--resume-review-id")
    parser.add_argument(
        "--capture-output",
        action="store_true",
        help="Save generated text only for QA, never credentials or raw response headers",
    )
    args = parser.parse_args()
    source = DesktopStateStore(args.source_state).snapshot()["drafts"][args.preview_id]
    assert (
        source["kind"] == "textbook_prompt_blueprint"
        and source["status"] == "completed"
    )
    revision = candidate_revision(source["result"]["candidate"])
    if bool(args.review_state) != bool(args.resume_review_id):
        parser.error("--review-state and --resume-review-id must be supplied together")
    if args.review_state:
        assert args.review_state.name.startswith("shchem-blueprint-review-qa-")
        state = DesktopStateStore(args.review_state)
        assert state.snapshot()["drafts"][args.preview_id] == source
    else:
        state = DesktopStateStore(
            Path(tempfile.mkdtemp(prefix="shchem-blueprint-review-qa-"))
        )
        state.save_draft(args.preview_id, source)
    output = (
        ROOT
        / "runtime/deeptutor_shchem/qa"
        / time.strftime("blueprint-review-%Y%m%d-%H%M%S")
    )
    output.mkdir(parents=True, exist_ok=False)
    (output / "original-record.json").write_text(
        json.dumps(source, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    text = review_prompt(
        source["preview"],
        source["result"]["candidate"],
        "",
        teacher_request=source.get("input", {}),
    )
    assert "local_provenance" not in text and "expanded/" not in text
    report = {
        "source_preview_id": args.preview_id,
        "source_candidate_revision": revision,
        "prompt_chars": len(text),
        "state_root": str(state.root),
        "model_call_attempted": False,
        "model_invoked": False,
    }
    print(
        json.dumps(
            {"stage": "ready", "output": str(output), **report}, ensure_ascii=False
        ),
        flush=True,
    )
    paths = DesktopPaths.from_workspace(ROOT, state_root=state.root)
    providers = ModelProviderSettingsStore(
        DesktopPaths.from_workspace(ROOT).settings_root, project_root=ROOT
    )
    facade = DesktopWorkbenchFacade(paths, provider_store=providers, state_store=state)
    if args.capture_output:
        from integrations.deeptutor_shchem_v1.intake_imports import (
            PinnedVisualTransport,
        )
        from integrations.deeptutor_shchem_v1.visual_provider_runtime import (
            strict_json_loads,
        )

        class CaptureTransport:
            def send(self, request, **kwargs):
                response = PinnedVisualTransport(total_timeout_seconds=300).send(
                    request, **kwargs
                )
                try:
                    envelope = strict_json_loads(response.body)
                    texts = [
                        item["text"]
                        for block in envelope.get("output", [])
                        for item in block.get("content", [])
                        if item.get("type") == "output_text"
                        and isinstance(item.get("text"), str)
                    ]
                    if not texts and isinstance(envelope.get("output_text"), str):
                        texts = [envelope["output_text"]]
                    # The generated content is teacher-owned QA material. Do not
                    # persist context, request headers, keys, or provider error text.
                    import re

                    rendered = re.sub(
                        r"sk-[A-Za-z0-9_-]{16,}", "[redacted]", "\n".join(texts)
                    )
                    name = json.loads(request.body)["text"]["format"]["name"]
                    stage = (
                        "diagnosis"
                        if name == "shchem_blueprint_diagnosis_v1"
                        else "revision"
                    )
                    (output / f"generated-{stage}.txt").write_text(
                        rendered, encoding="utf-8"
                    )
                except (ValueError, KeyError, TypeError):
                    pass
                return response

        facade._blueprint_review_transport = CaptureTransport()
    try:
        profile = next(
            p
            for p in facade.preparation_profiles()
            if p.profile_id == "desktop-default"
        )
        if args.execute:
            report["model_call_attempted"] = True
            result = facade.review_prompt_blueprint(
                args.preview_id,
                revision,
                profile.profile_id,
                profile.revision,
                teacher_confirmed=True,
                resume_review_id=args.resume_review_id,
                on_progress=lambda value: print(
                    json.dumps(
                        {"stage": value["stage"], "review_id": value["review_id"]}
                    ),
                    flush=True,
                ),
            )
            assert state.snapshot()["drafts"][args.preview_id] == source
            assert len(facade.prompt_blueprint_reviews(args.preview_id, revision)) == 1
            (output / "review-result.json").write_text(
                json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8"
            )
            report.update(
                model_invoked=True,
                review_id=result["review_id"],
                model_id=result["model_id"],
                latency_ms=result["latency_ms"],
                usage=result["usage"],
                issue_count=len(result["report"]["issues"]),
                original_unchanged=True,
            )
        if args.ui:
            from PySide6.QtWidgets import QApplication

            from integrations.deeptutor_shchem_v1.desktop_workbench.blueprint_review_dialog import (
                BlueprintReviewDialog,
            )
            from integrations.deeptutor_shchem_v1.desktop_workbench.main_window import (
                WORKBENCH_STYLE,
                install_font_fallbacks,
            )

            class Tasks:
                def submit(self, label, operation, *, on_success, on_failure):
                    on_success(operation())

            app = QApplication.instance() or QApplication([])
            install_font_fallbacks()
            app.setStyleSheet(WORKBENCH_STYLE)
            dialog = BlueprintReviewDialog(
                facade, Tasks(), args.preview_id, source["result"], None
            )
            if facade.prompt_blueprint_reviews(args.preview_id, revision):
                dialog.history.setCurrentIndex(1)
                assert "AI审校" in dialog.findings.toPlainText()
                assert not dialog.start.isEnabled()
            dialog.show()
            captures = []
            for width in (900, 420):
                dialog.resize(width, 800)
                for _ in range(4):
                    app.processEvents()
                assert dialog.width() == width and dialog.height() == 800
                for index, label in enumerate(("original", "findings", "revised")):
                    dialog.tabs.setCurrentIndex(index)
                    app.processEvents()
                    path = output / f"{label}-{width}.png"
                    assert dialog.grab().save(str(path))
                    captures.append(str(path))
            dialog.close()
            report["captures"] = captures
    except Exception as exc:
        report.update(status="failed", error_code=getattr(exc, "code", "qa_failed"))
        records = [
            record
            for record in state.snapshot()["drafts"].values()
            if record.get("kind") == "textbook_blueprint_review"
        ]
        if records:
            record = records[-1]
            report["review_id"] = record["review_id"]
            report["response_summary"] = record.get("response_summary", {})
            report["model_invoked"] = report["response_summary"].get("model_invoked")
    finally:
        records = facade.prompt_blueprint_reviews(args.preview_id, revision)
        if records:
            checkpoint = records[0]
            report["diagnosis_saved"] = bool(checkpoint.get("diagnosis_result"))
            report["review_status"] = checkpoint.get("status")
            if checkpoint.get("diagnosis_result"):
                (output / "diagnosis-result.json").write_text(
                    json.dumps(
                        checkpoint["diagnosis_result"], ensure_ascii=False, indent=2
                    ),
                    encoding="utf-8",
                )
        facade.shutdown()
    (output / "verification.json").write_text(
        json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    print(
        json.dumps(
            {"stage": "finished", "output": str(output), **report}, ensure_ascii=False
        ),
        flush=True,
    )
    return 1 if report.get("status") == "failed" else 0


if __name__ == "__main__":
    raise SystemExit(main())
