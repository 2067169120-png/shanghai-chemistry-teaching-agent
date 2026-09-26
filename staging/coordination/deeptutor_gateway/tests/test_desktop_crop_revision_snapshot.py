"""Synthetic native snapshot checks; no source files, provider or renderer run."""

from copy import deepcopy
from dataclasses import replace

import pytest

from integrations.deeptutor_shchem_v1 import desktop_facade, paper_export_workbench
from integrations.deeptutor_shchem_v1.desktop_facade import (
    DesktopFacadeError,
    DesktopWorkbenchFacade,
)
from integrations.deeptutor_shchem_v1.desktop_paths import DesktopPaths
from integrations.deeptutor_shchem_v1.paper_export_workbench import (
    PaperExportJobManager,
)
from staging.coordination.deeptutor_gateway.tests import (
    test_desktop_facade as native_fixture,
)
from staging.coordination.deeptutor_gateway.tests import (
    test_paper_export_workbench_api as export_fixture,
)


def test_unaffected_catalog_and_existing_snapshot_are_unchanged(monkeypatch):
    monkeypatch.setattr(desktop_facade, "presentation_fingerprint", lambda _: None)
    for catalog in ({"scope": "wave1", "papers": []}, export_fixture._catalog()):
        before = deepcopy(catalog)
        old_snapshot = DesktopWorkbenchFacade._paper_catalog_snapshot_id(catalog)
        result = DesktopWorkbenchFacade._with_presentation_snapshot(catalog)
        assert result is catalog
        assert result == before
        assert DesktopWorkbenchFacade._paper_catalog_snapshot_id(result) == old_snapshot


def test_presentation_snapshot_is_stable_idempotent_and_nonmutating(monkeypatch):
    monkeypatch.setattr(desktop_facade, "presentation_fingerprint", lambda _: "1" * 64)
    source = export_fixture._catalog()
    before = deepcopy(source)
    revised = DesktopWorkbenchFacade._with_presentation_snapshot(source)
    assert revised is not source
    assert source == before
    assert revised["data_snapshot_id"] != source["data_snapshot_id"]
    assert DesktopWorkbenchFacade._with_presentation_snapshot(revised) == revised
    prepared = paper_export_workbench._prepare_catalog(
        revised, scope="master", snapshot_id=revised["data_snapshot_id"]
    )
    assert prepared["data_snapshot_id"] == revised["data_snapshot_id"]
    assert "data_snapshot_id" not in prepared["catalog"]
    assert revised["data_snapshot_id"] != source["data_snapshot_id"]


def test_recipe_and_source_catalog_each_change_the_snapshot(monkeypatch):
    revision = {"fingerprint": "1" * 64}
    monkeypatch.setattr(
        desktop_facade, "presentation_fingerprint", lambda _: revision["fingerprint"]
    )
    source = export_fixture._catalog()
    first = DesktopWorkbenchFacade._with_presentation_snapshot(source)
    revision["fingerprint"] = "2" * 64
    second = DesktopWorkbenchFacade._with_presentation_snapshot(first)
    assert second["data_snapshot_id"] != first["data_snapshot_id"]
    changed = deepcopy(source)
    changed["papers"][0]["theme_groups"][0]["atomic_chain"][0]["visible_summary_zh"] = (
        "来源摘要发生变化"
    )
    third = DesktopWorkbenchFacade._with_presentation_snapshot(changed)
    assert third["data_snapshot_id"] not in {
        first["data_snapshot_id"],
        second["data_snapshot_id"],
    }


class _SearchThroughLoader:
    def search(self, payload, *, theme_loader, complete_themes_only):
        assert complete_themes_only is True
        catalog = theme_loader(payload["scope"])
        paper = catalog["papers"][0]
        group = paper["theme_groups"][0]
        return {
            # The facade must bind its source catalog, not this search response ID.
            "data_snapshot_id": "f" * 64,
            "counts": {"atomic_parts_matched": 2},
            "page": {"total_theme_cards": 1, "has_more": False},
            "items": [
                {
                    "paper": paper["paper"],
                    "theme": group["theme"],
                    "shared_context": group["shared_context"],
                    "counts": {"atomic_total": 2, "atomic_matched": 2},
                }
            ],
        }


@pytest.fixture
def native(tmp_path, monkeypatch):
    workspace = tmp_path / "workspace"
    (workspace / "sh-chem-db").mkdir(parents=True)
    (workspace / "integrations" / "deeptutor_shchem_v1").mkdir(parents=True)
    paths = DesktopPaths.from_workspace(workspace, state_root=tmp_path / "state")
    revision = {"fingerprint": "1" * 64}
    monkeypatch.setattr(
        desktop_facade, "presentation_fingerprint", lambda _: revision["fingerprint"]
    )
    captured = {}
    monkeypatch.setattr(
        paper_export_workbench,
        "render_export_bundle",
        export_fixture._fake_render_factory(captured),
    )
    monkeypatch.setattr(paper_export_workbench, "_locate_toolchain", lambda: None)
    manager = PaperExportJobManager(paths.state_root)
    facade, state = native_fixture._build_export_facade(paths, manager)
    facade._search = _SearchThroughLoader()
    try:
        yield facade, state, manager, captured, revision
    finally:
        facade.shutdown()
        manager.shutdown()


def _refresh_basket_and_source_ref(facade, state):
    card = facade.search_themes(scope="master").cards[0]
    state.clear_basket()
    facade.add_theme_to_basket(card)
    state.save_draft(
        "paper-current",
        {
            "kind": "paper",
            "payload": {
                "themes": [
                    {
                        "source_identity_sha256": card.source_identity_sha256,
                        "source_ref": {
                            "scope": "master",
                            "paper_id": "PAPER-1",
                            "theme_id": "T1",
                            "data_snapshot_id": card.data_snapshot_id,
                        },
                    }
                ]
            },
        },
    )
    return card


def _approved_preview(facade):
    preview = native_fixture._create_export_preview(facade)
    facade.approve_paper_preview(preview.preview_id, preview.preview_hash)
    return preview


def test_search_and_detail_share_revision_and_reject_stale_card(native):
    facade, _state, _manager, _captured, revision = native
    card = facade.search_themes(scope="master").cards[0]
    assert (
        card.data_snapshot_id
        == facade.paper_theme_catalog("master")["data_snapshot_id"]
    )
    assert len(facade.library_theme_detail(card).parts) == 2
    for stale in (
        replace(card, data_snapshot_id=export_fixture.SNAPSHOT),
        card,
    ):
        revision["fingerprint"] = "2" * 64
        with pytest.raises(DesktopFacadeError) as exc:
            facade.library_theme_detail(stale)
        assert exc.value.code == "theme_snapshot_stale"
    fresh = facade.search_themes(scope="master").cards[0]
    assert fresh.data_snapshot_id != card.data_snapshot_id
    assert len(facade.library_theme_detail(fresh).parts) == 2


def test_legacy_basket_snapshot_is_rejected_before_export_job(native):
    facade, _state, manager, captured, _revision = native
    preview = _approved_preview(facade)
    with pytest.raises(DesktopFacadeError) as exc:
        facade.export_paper_preview(preview.preview_id, preview.preview_hash)
    assert exc.value.code == "theme_snapshot_stale"
    assert not captured
    assert list(manager.root.glob("WBEXP-*")) == []


def test_refreshed_preview_exports_with_the_same_revision_snapshot(native):
    facade, state, _manager, captured, _revision = native
    card = _refresh_basket_and_source_ref(facade, state)
    assert len(facade.library_theme_detail(card).parts) == 2
    preview = _approved_preview(facade)
    result = facade.export_paper_preview(preview.preview_id, preview.preview_hash)
    assert result["status"] == "completed"
    assert len(result["artifacts"]) == 4
    assert captured["bundle"]["student_plan"]
    assert captured["bundle"]["teacher_plan"]


def test_revision_changed_after_approval_requires_refresh_then_succeeds(native):
    facade, state, manager, captured, revision = native
    old_card = _refresh_basket_and_source_ref(facade, state)
    first = _approved_preview(facade)
    revision["fingerprint"] = "2" * 64
    with pytest.raises(DesktopFacadeError) as exc:
        facade.export_paper_preview(first.preview_id, first.preview_hash)
    assert exc.value.code == "theme_snapshot_stale"
    assert not captured
    assert list(manager.root.glob("WBEXP-*")) == []
    fresh_card = _refresh_basket_and_source_ref(facade, state)
    assert fresh_card.data_snapshot_id != old_card.data_snapshot_id
    second = _approved_preview(facade)
    # The visible settings are identical; the preview identity also binds the
    # newly selected source snapshot, unlike the visible-content-only hash.
    assert second.preview_id != first.preview_id
    result = facade.export_paper_preview(second.preview_id, second.preview_hash)
    assert result["status"] == "completed"


def test_refreshed_basket_cannot_reuse_an_old_source_reference(native):
    facade, state, manager, captured, _revision = native
    _refresh_basket_and_source_ref(facade, state)
    draft = state.snapshot()["drafts"]["paper-current"]
    draft["payload"]["themes"][0]["source_ref"]["data_snapshot_id"] = (
        export_fixture.SNAPSHOT
    )
    state.save_draft("paper-current", draft)
    preview = _approved_preview(facade)
    with pytest.raises(DesktopFacadeError) as exc:
        facade.export_paper_preview(preview.preview_id, preview.preview_hash)
    assert exc.value.code == "theme_snapshot_stale"
    assert not captured
    assert list(manager.root.glob("WBEXP-*")) == []
