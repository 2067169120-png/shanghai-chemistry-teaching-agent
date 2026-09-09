from __future__ import annotations

import hashlib
import json
import zipfile
from collections.abc import Mapping
from copy import deepcopy
from pathlib import Path
from typing import Any

import pytest

from integrations.deeptutor_shchem_v1 import paper_export_workbench
from integrations.deeptutor_shchem_v1.desktop_facade import (
    DESKTOP_REGISTRY_SCHEMA,
    PERSONAL_HANDOUT_SCOPE,
    DesktopFacadeError,
    DesktopWorkbenchFacade,
    ProviderProfileInput,
)
from integrations.deeptutor_shchem_v1.desktop_paths import DesktopPaths
from integrations.deeptutor_shchem_v1.desktop_state import DesktopStateStore
from integrations.deeptutor_shchem_v1.model_provider_settings import (
    ModelProviderSettingsStore,
)
from integrations.deeptutor_shchem_v1.paper_export_renderer import (
    ARTIFACT_FILENAMES,
)
from integrations.deeptutor_shchem_v1.paper_export_workbench import (
    PaperExportJobManager,
)
from staging.coordination.deeptutor_gateway.tests import (
    test_paper_export_workbench_api as paper_export_fixture,
)


class FakeThemeReader:
    def __init__(self, *, fail_scope: str | None = None) -> None:
        self.fail_scope = fail_scope
        self.calls: list[str] = []

    def groups(self, scope: str) -> dict[str, Any]:
        self.calls.append(scope)
        if scope == self.fail_scope:
            error = RuntimeError("private diagnostics must not reach the desktop")
            error.code = "fixture_scope_unavailable"  # type: ignore[attr-defined]
            raise error
        counts = {
            "master": (20, 48, 470, 43),
            "wave1": (5, 25, 252, 0),
        }[scope]
        return {
            "scope": scope,
            "counts": {
                "papers": counts[0],
                "theme_groups": counts[1],
                "atomic_parts": counts[2],
                "unassigned_atomic_parts": counts[3],
            },
            "papers": [],
        }


class FakeSupplementalReader:
    def theme_groups(self) -> dict[str, Any]:
        return {
            "scope": "supplemental",
            "counts": {
                "papers": 7,
                "theme_groups": 10,
                "atomic_parts": 86,
                "unassigned_atomic_parts": 0,
            },
            "papers": [],
        }


class FakeCurriculumReader:
    def __init__(self, *, fail: bool = False) -> None:
        self.fail = fail

    def catalog(self) -> dict[str, Any]:
        if self.fail:
            raise RuntimeError("private path")
        return {
            "counts": {
                "volumes": 5,
                "chapters": 19,
                "sections": 60,
                "active_atomic_mappings": 87,
            }
        }


class FakeSearchReader:
    def search(self, payload: dict[str, Any], **_kwargs: Any) -> dict[str, Any]:
        query = payload.get("q")
        return {
            "counts": {"atomic_parts_matched": 3},
            "page": {
                "total_theme_cards": 1,
                "has_more": False,
            },
            "items": [
                {
                    "display_title_zh": "锂离子电池的充放电",
                    "paper": {"id": "paper-fixture", "title": "本地题库来源卷"},
                    "theme": {
                        "id": "theme-fixture",
                        "title": "锂离子电池",
                        "page_span": {"pages": [2, 3]},
                    },
                    "source_metadata": {
                        "year": "2025",
                        "region": "上海",
                        "paper_type": "区级调研",
                    },
                    "counts": {
                        "atomic_total": 7,
                        "atomic_matched": 3 if query else 7,
                    },
                    "shared_context": {
                        "context_summary_zh": "共享电池结构与充放电数据。"
                    },
                }
            ],
        }


class ExportThemeReader:
    def __init__(self) -> None:
        self.catalog = paper_export_fixture._catalog()

    def groups(self, scope: str) -> dict[str, Any]:
        assert scope == "master"
        return deepcopy(self.catalog)


class ExportMasterDirectReader:
    def __init__(self, *, fail: bool = False) -> None:
        self.fail = fail

    def detail(self, node_id: str) -> dict[str, Any]:
        if self.fail:
            error = RuntimeError("private fixture detail")
            error.status = 409  # type: ignore[attr-defined]
            error.code = "fixture_detail_failed"  # type: ignore[attr-defined]
            raise error
        return deepcopy(paper_export_fixture._details()[node_id])

    def question_crop(self, node_id: str, crop_id: str) -> Any:
        return paper_export_fixture._crop(node_id, crop_id)


class EmptyProviderStore:
    def list_metadata(self) -> list[dict[str, Any]]:
        return []

    def upsert_metadata(
        self, value: Mapping[str, Any], *, expected_revision: str | None
    ) -> dict[str, Any]:
        raise AssertionError("not used")

    def put_credential(
        self, profile_id: str, secret: str, *, expected_revision: str
    ) -> dict[str, Any]:
        raise AssertionError("not used")

    def delete_credential(
        self, profile_id: str, *, expected_revision: str
    ) -> dict[str, Any]:
        raise AssertionError("not used")


class FakeCredentialBackend:
    def __init__(self) -> None:
        self.values: dict[str, str] = {}

    def available(self) -> bool:
        return True

    def write(self, target_name: str, secret: str) -> None:
        self.values[target_name] = secret

    def read(self, target_name: str) -> str | None:
        return self.values.get(target_name)

    def exists(self, target_name: str) -> bool:
        return target_name in self.values

    def delete(self, target_name: str) -> bool:
        return self.values.pop(target_name, None) is not None


@pytest.fixture
def desktop_paths(tmp_path: Path) -> DesktopPaths:
    workspace = tmp_path / "workspace"
    (workspace / "sh-chem-db").mkdir(parents=True)
    (workspace / "integrations" / "deeptutor_shchem_v1").mkdir(parents=True)
    state = tmp_path / "personal-state"
    return DesktopPaths.from_workspace(workspace, state_root=state)


def build_facade(
    paths: DesktopPaths,
    *,
    theme: Any | None = None,
    curriculum: Any | None = None,
    provider: Any | None = None,
) -> DesktopWorkbenchFacade:
    return DesktopWorkbenchFacade(
        paths,
        theme_reader=theme or FakeThemeReader(),
        supplemental_reader=FakeSupplementalReader(),
        curriculum_reader=curriculum or FakeCurriculumReader(),
        search_reader=FakeSearchReader(),
        provider_store=provider or EmptyProviderStore(),
        state_store=DesktopStateStore(paths.state_root),
    )


def _export_theme_identity() -> str:
    raw = json.dumps(
        {"scope": "master", "paper": "PAPER-1", "theme": "T1"},
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    return hashlib.sha256(raw).hexdigest()


def _build_export_facade(
    paths: DesktopPaths,
    manager: PaperExportJobManager,
    *,
    direct: ExportMasterDirectReader | None = None,
) -> tuple[DesktopWorkbenchFacade, DesktopStateStore]:
    state = DesktopStateStore(paths.state_root)
    identity = _export_theme_identity()
    state.add_to_basket(
        {
            "key": identity,
            "scope": "master",
            "title_zh": "教师可改标题，不用于解析主题 ID",
            "paper_title_zh": "教师可改来源名",
            "source_identity_sha256": identity,
            "data_snapshot_id": paper_export_fixture.SNAPSHOT,
        }
    )
    state.save_draft(
        "paper-current",
        {
            "kind": "paper",
            "payload": {
                "themes": [
                    {
                        "source_identity_sha256": identity,
                        "source_ref": {
                            "scope": "master",
                            "paper_id": "PAPER-1",
                            "theme_id": "T1",
                            "data_snapshot_id": paper_export_fixture.SNAPSHOT,
                        },
                    }
                ]
            },
        },
    )
    unused_reader = object()
    return (
        DesktopWorkbenchFacade(
            paths,
            theme_reader=ExportThemeReader(),
            supplemental_reader=FakeSupplementalReader(),
            curriculum_reader=FakeCurriculumReader(),
            search_reader=FakeSearchReader(),
            provider_store=EmptyProviderStore(),
            state_store=state,
            paper_export_jobs=manager,
            wave_visual_reader=unused_reader,
            wave_crop_reader=unused_reader,
            master_workbench_reader=unused_reader,
            master_direct_reader=direct or ExportMasterDirectReader(),
        ),
        state,
    )


def _create_export_preview(
    facade: DesktopWorkbenchFacade, title: str = "阶段练习"
) -> Any:
    return facade.create_paper_preview(
        {
            "mode": "daily_practice",
            "title": title,
            "subtitle": "本机教师工作台",
            "duration_minutes": 20,
            "assembly": {
                "schema_version": "shchem.desktop-paper-preview.v1",
                "mode": "daily_practice",
                "title": title,
                "themes": [
                    {
                        "title": "此显示标题不参与主题 ID 解析",
                        "questions": [
                            {"score": 2, "answer_space": 3},
                            {"score": 2, "answer_space": 3},
                        ],
                    }
                ],
            },
        }
    )


def test_direct_registry_projection_uses_real_reader_contract_without_gui(
    desktop_paths: DesktopPaths,
) -> None:
    import sys

    theme = FakeThemeReader()
    facade = build_facade(desktop_paths, theme=theme)
    registry = facade.load_desktop_registry()

    assert registry.schema_version == DESKTOP_REGISTRY_SCHEMA
    assert registry.fully_loaded is True
    assert [product.product_id for product in registry.products] == [
        "master",
        "wave1",
        "supplemental",
    ]
    assert [product.atomic_parts for product in registry.products] == [470, 252, 86]
    assert registry.curriculum.sections == 60
    assert registry.as_dict()["combined_atomic_total"] is None
    assert registry.as_dict()["cross_scope_sum_allowed"] is False
    assert sorted(theme.calls) == ["master", "wave1"]
    # A combined suite may already have imported Qt for unrelated UI tests.
    # Verify the facade's import boundary in a fresh process instead of making
    # this contract depend on pytest collection order.
    import subprocess

    check = subprocess.run(
        [
            sys.executable,
            "-c",
            (
                "import sys; import integrations.deeptutor_shchem_v1.desktop_facade; "
                "assert not any(n == 'PySide6' or n.startswith('PySide6.') "
                "for n in sys.modules)"
            ),
        ],
        cwd=Path(__file__).resolve().parents[4],
        capture_output=True,
        text=True,
        timeout=30,
        check=False,
    )
    assert check.returncode == 0, check.stderr


def test_registry_cache_makes_repeat_startup_fast_but_force_refresh_reads_live_sources(
    desktop_paths: DesktopPaths,
) -> None:
    first = build_facade(desktop_paths)
    live = first.load_desktop_registry(force_refresh=True)
    assert live.fully_loaded is True
    assert (desktop_paths.state_root / "desktop-registry-cache.v1.json").is_file()

    stale_reader = FakeThemeReader(fail_scope="master")
    cached = build_facade(desktop_paths, theme=stale_reader).load_desktop_registry()
    assert cached.fully_loaded is True
    assert stale_reader.calls == []

    refreshed = build_facade(desktop_paths, theme=stale_reader).load_desktop_registry(
        force_refresh=True
    )
    assert refreshed.products[0].loaded is False
    assert refreshed.products[0].error_code == "fixture_scope_unavailable"


def test_one_reader_failure_is_visible_without_hiding_other_products(
    desktop_paths: DesktopPaths,
) -> None:
    registry = build_facade(
        desktop_paths,
        theme=FakeThemeReader(fail_scope="master"),
    ).load_desktop_registry()
    master, wave1, supplemental = registry.products
    assert master.loaded is False
    assert master.error_code == "fixture_scope_unavailable"
    assert "private diagnostics" not in master.message_zh
    assert wave1.loaded is True
    assert supplemental.loaded is True


def test_theme_search_and_basket_persist_complete_theme_summary(
    desktop_paths: DesktopPaths,
) -> None:
    facade = build_facade(desktop_paths)
    result = facade.search_themes(scope="master", query="电池")
    assert result.total_themes == 1
    assert result.cards[0].atomic_total == 7
    assert result.cards[0].page_zh == "第 2、3 页"
    assert facade.add_theme_to_basket(result.cards[0]) == 1
    basket = facade.basket()
    assert len(basket) == 1
    assert basket[0]["title_zh"] == "锂离子电池的充放电"
    assert "theme-fixture" not in json.dumps(basket, ensure_ascii=False)


def _write_docx(path: Path, *, with_visual: bool = False) -> None:
    document = """<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
<w:document xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main">
  <w:body><w:p><w:r><w:t>第1题 氯气与水反应</w:t></w:r></w:p></w:body>
</w:document>"""
    with zipfile.ZipFile(path, "w") as package:
        package.writestr("word/document.xml", document)
        if with_visual:
            package.writestr("word/media/image1.png", b"fixture-image")


def test_docx_native_text_lane_does_not_require_visual_profile(
    desktop_paths: DesktopPaths,
) -> None:
    source = desktop_paths.workspace_root / "native.docx"
    _write_docx(source)
    facade = build_facade(desktop_paths)
    receipt = facade.create_import_draft(files=[source], source_type="教师讲义")
    assert receipt.state == "native_text_complete"
    assert "原生可编辑文字" in receipt.message_zh
    state = facade.state_store.snapshot()
    draft = state["drafts"][receipt.draft_id]
    assert draft["files"][0]["xml_locator"] == "word/document.xml"
    assert draft["files"][0]["native_text_characters"] > 0
    assert draft["visual_profile_ready"] is False


def test_docx_with_media_and_image_only_material_keep_visual_requirements(
    desktop_paths: DesktopPaths,
) -> None:
    hybrid = desktop_paths.workspace_root / "hybrid.docx"
    _write_docx(hybrid, with_visual=True)
    image = desktop_paths.workspace_root / "page.png"
    image.write_bytes(b"not-a-real-image-but-a-local-fixture")
    facade = build_facade(desktop_paths)

    hybrid_receipt = facade.create_import_draft(
        files=[hybrid], source_type="试卷与答案"
    )
    visual_receipt = facade.create_import_draft(files=[image], source_type="试卷与答案")
    assert hybrid_receipt.state == "hybrid_visual_required"
    assert "可编辑文字" in hybrid_receipt.message_zh
    assert visual_receipt.state == "visual_only_required"
    assert "配置具备视觉能力的模型" in visual_receipt.message_zh


def test_provider_facade_uses_secure_store_and_never_returns_or_writes_key(
    desktop_paths: DesktopPaths,
) -> None:
    backend = FakeCredentialBackend()
    store = ModelProviderSettingsStore(
        desktop_paths.settings_root,
        project_root=desktop_paths.workspace_root,
        credential_backend=backend,
    )
    facade = build_facade(desktop_paths, provider=store)
    secret = "sk-desktop-test-only-123456789"
    request = ProviderProfileInput(
        provider_name="任意兼容服务",
        base_url="https://models.example/v1",
        model_id="vendor/experimental-model:v4",
        vision_enabled=True,
        key_value=secret,
    )
    assert secret not in repr(request)
    summary = facade.save_provider_profile(request)
    assert summary.provider_name == "任意兼容服务"
    assert summary.model_id == "vendor/experimental-model:v4"
    assert summary.key_saved is True
    assert secret not in repr(summary)
    assert facade.has_visual_profile() is True
    for path in desktop_paths.settings_root.iterdir():
        if path.is_file():
            assert secret.encode() not in path.read_bytes()


def test_paper_and_preparation_drafts_have_honest_blockers(
    desktop_paths: DesktopPaths,
) -> None:
    facade = build_facade(desktop_paths)
    with pytest.raises(DesktopFacadeError, match="题篮为空|名称"):
        facade.create_paper_preview({"mode": "daily_practice", "title": ""})
    preview = facade.create_paper_preview(
        {"mode": "daily_practice", "title": "阶段练习", "keywords": "平衡"}
    )
    assert preview.export_ready is False
    assert any("题篮为空" in blocker for blocker in preview.blockers)

    receipt = facade.create_preparation_draft(
        {
            "output_kind": "joint",
            "topic": "化学平衡",
            "audience": "高二3班",
            "lesson_route": "复习",
            "lesson_timing": "1课时×40分钟",
            "objective": "能用证据判断平衡移动方向",
            "materials": "教材章节与完整主题题",
            "advanced": {},
        }
    )
    assert receipt.state == "draft"
    assert "Lesson Blueprint" in receipt.message_zh
    availability = facade.preparation_availability()
    assert availability.provider_ready is False
    assert "保存备课草稿" in availability.message_zh


def test_approved_preview_waits_for_existing_four_file_export_chain(
    desktop_paths: DesktopPaths, monkeypatch: pytest.MonkeyPatch
) -> None:
    manager = PaperExportJobManager(desktop_paths.state_root)
    monkeypatch.setattr(
        paper_export_workbench,
        "render_export_bundle",
        paper_export_fixture._fake_render_factory({}),
    )
    monkeypatch.setattr(paper_export_workbench, "_locate_toolchain", lambda: None)
    facade, state = _build_export_facade(desktop_paths, manager)
    try:
        preview = _create_export_preview(facade)
        facade.approve_paper_preview(preview.preview_id, preview.preview_hash)
        # PaperPage saves its editable model immediately after approval.  That
        # ordinary draft must not erase the exact facade approval record.
        state.save_draft("paper-current", {"kind": "paper", "payload": {"themes": []}})

        result = facade.export_paper_preview(
            preview.preview_id,
            preview.preview_hash,
            {"client_supplied_values_are_not_export_authority": True},
        )

        assert result["status"] == "completed"
        assert result["job_id"].startswith("WBEXP-")
        assert "四个文件已生成" in result["message_zh"]
        assert [row["artifact_id"] for row in result["artifacts"]] == list(
            ARTIFACT_FILENAMES
        )
        for artifact in result["artifacts"]:
            path = Path(artifact["path"])
            assert path.is_absolute()
            assert path.is_file()
            assert artifact["filename"] == ARTIFACT_FILENAMES[artifact["artifact_id"]]
            assert hashlib.sha256(path.read_bytes()).hexdigest() == artifact["sha256"]
    finally:
        manager.shutdown()


def test_unapproved_preview_cannot_start_four_file_export(
    desktop_paths: DesktopPaths,
) -> None:
    manager = PaperExportJobManager(desktop_paths.state_root)
    facade, _state = _build_export_facade(desktop_paths, manager)
    try:
        preview = _create_export_preview(facade)
        with pytest.raises(DesktopFacadeError) as captured:
            facade.export_paper_preview(preview.preview_id, preview.preview_hash)
        assert captured.value.code == "paper_export_not_ready"
        assert "确认" in captured.value.message_zh
        assert list(manager.root.glob("WBEXP-*")) == []
    finally:
        manager.shutdown()


def test_new_preview_expires_previous_approval(
    desktop_paths: DesktopPaths,
) -> None:
    manager = PaperExportJobManager(desktop_paths.state_root)
    facade, _state = _build_export_facade(desktop_paths, manager)
    try:
        first = _create_export_preview(facade, "第一版")
        facade.approve_paper_preview(first.preview_id, first.preview_hash)
        second = _create_export_preview(facade, "第二版")
        assert second.preview_id != first.preview_id

        with pytest.raises(DesktopFacadeError) as captured:
            facade.export_paper_preview(first.preview_id, first.preview_hash)
        assert captured.value.code == "paper_preview_stale"
        assert "重新" in captured.value.message_zh
        assert list(manager.root.glob("WBEXP-*")) == []
    finally:
        manager.shutdown()


def test_background_export_failure_becomes_chinese_desktop_error(
    desktop_paths: DesktopPaths,
) -> None:
    manager = PaperExportJobManager(desktop_paths.state_root)
    facade, _state = _build_export_facade(
        desktop_paths,
        manager,
        direct=ExportMasterDirectReader(fail=True),
    )
    try:
        preview = _create_export_preview(facade)
        facade.approve_paper_preview(preview.preview_id, preview.preview_hash)

        with pytest.raises(DesktopFacadeError) as captured:
            facade.export_paper_preview(preview.preview_id, preview.preview_hash)
        assert captured.value.code == "fixture_detail_failed"
        assert "逐图详情" in captured.value.message_zh
        assert "private fixture" not in captured.value.message_zh
    finally:
        manager.shutdown()


def test_window_state_round_trip_uses_personal_state_root(
    desktop_paths: DesktopPaths,
) -> None:
    store = DesktopStateStore(desktop_paths.state_root)
    store.save_window_state(geometry="Z2VvbWV0cnk=", layout="bGF5b3V0")
    assert store.window_state() == {
        "geometry": "Z2VvbWV0cnk=",
        "layout": "bGF5b3V0",
    }
    assert store.path.is_relative_to(desktop_paths.state_root)
    assert not store.path.is_relative_to(desktop_paths.workspace_root)


def test_personal_handout_search_projects_word_candidates_as_separate_groups(
    desktop_paths: DesktopPaths,
) -> None:
    facade = build_facade(desktop_paths)
    inventory = {
        "scope": "personal_handouts",
        "page": {"has_more": False},
        "items": [
            {
                "package_id": "PKG-001",
                "source_document_name": "原卷.docx",
                "section_title": "物质的分离",
                "quick_import_eligible": True,
                "pairing_status": "paired",
            },
            {
                "package_id": "PKG-001",
                "source_document_name": "原卷.docx",
                "section_title": "物质的分离",
                "quick_import_eligible": False,
                "pairing_status": "unpaired",
            },
        ],
    }
    facade.personal_handout_inventory = lambda **_kwargs: inventory  # type: ignore[method-assign]
    result = facade.search_personal_handouts(query="分离", limit=40)
    assert result.scope == PERSONAL_HANDOUT_SCOPE
    assert result.total_themes == 1
    assert len(result.cards) == 1
    card = result.cards[0]
    assert card.title_zh == "物质的分离"
    assert card.atomic_total == 2
    assert "可直接入库 1" in card.source_zh
    assert "待视觉补全 1" in card.source_zh
    assert "已配对解析 1" in card.source_zh
