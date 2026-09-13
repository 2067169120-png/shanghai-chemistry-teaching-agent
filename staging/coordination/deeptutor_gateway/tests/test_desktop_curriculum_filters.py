from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest

from integrations.deeptutor_shchem_v1.desktop_facade import (
    DesktopFacadeError,
    DesktopWorkbenchFacade,
    PERSONAL_HANDOUT_SCOPE,
)
from integrations.deeptutor_shchem_v1.desktop_paths import DesktopPaths
from integrations.deeptutor_shchem_v1.desktop_state import DesktopStateStore


class _ThemeReader:
    def groups(self, scope: str) -> dict[str, Any]:
        return {
            "scope": scope,
            "counts": {
                "papers": 1,
                "theme_groups": 1,
                "atomic_parts": 1,
                "unassigned_atomic_parts": 0,
            },
            "papers": [],
        }


class _SupplementalReader:
    def theme_groups(self) -> dict[str, Any]:
        return {
            "scope": "supplemental",
            "counts": {
                "papers": 1,
                "theme_groups": 1,
                "atomic_parts": 1,
                "unassigned_atomic_parts": 0,
            },
            "papers": [],
        }


class _CurriculumReader:
    def __init__(self) -> None:
        self.catalog_calls = 0
        self.search_calls: list[dict[str, str]] = []

    def catalog(self) -> dict[str, Any]:
        self.catalog_calls += 1
        return {
            "schema_version": "fixture",
            "volumes": [
                {
                    "volume_id": "TB-M1",
                    "volume_title": "必修第一册",
                    "mapping_counts": {"mapped_atomic_count": 2},
                    "chapters": [
                        {
                            "chapter_id": "TB-M1-C1",
                            "chapter_title": "第1章 化学研究",
                            "mapping_counts": {"mapped_atomic_count": 2},
                            "sections": [
                                {
                                    "section_key": "TB-M1-C1:1.2",
                                    "section_id": "TB-M1-C1-S1.2",
                                    "display_label_zh": "1.2 物质的量",
                                    "mapping_counts": {"mapped_atomic_count": 2},
                                }
                            ],
                        }
                    ],
                }
            ],
        }

    def search(self, **selector: str) -> dict[str, Any]:
        self.search_calls.append(dict(selector))
        return {
            "query": dict(selector),
            "items": [
                {
                    "atomic_id": "M1-A1",
                    "source_layer": "master_direct_active",
                }
            ],
            "atomic_ids": ["M1-A1"],
        }


class _SearchReader:
    def __init__(self) -> None:
        self.payloads: list[dict[str, Any]] = []
        self.curriculum_projection: dict[str, Any] | None = None

    def search(self, payload: dict[str, Any], **kwargs: Any) -> dict[str, Any]:
        self.payloads.append(payload)
        if "curriculum" in payload:
            loader = kwargs.get("curriculum_loader")
            assert callable(loader)
            self.curriculum_projection = loader(payload["curriculum"])
        return {
            "counts": {"atomic_parts_matched": 1},
            "page": {"total_theme_cards": 1, "has_more": False},
            "items": [
                {
                    "display_title_zh": "物质的量",
                    "paper": {"id": "P1", "title": "来源卷"},
                    "theme": {
                        "id": "T1",
                        "title": "主题",
                        "page_span": {"pages": [1]},
                    },
                    "counts": {"atomic_total": 1, "atomic_matched": 1},
                    "shared_context": {"context_summary_zh": "共同材料"},
                }
            ],
        }


class _ProviderStore:
    def list_metadata(self) -> list[dict[str, Any]]:
        return []


@pytest.fixture
def paths(tmp_path: Path) -> DesktopPaths:
    workspace = tmp_path / "workspace"
    (workspace / "sh-chem-db").mkdir(parents=True)
    (workspace / "integrations" / "deeptutor_shchem_v1").mkdir(parents=True)
    return DesktopPaths.from_workspace(workspace, state_root=tmp_path / "state")


def _facade(
    paths: DesktopPaths,
    curriculum: _CurriculumReader,
    search: _SearchReader,
) -> DesktopWorkbenchFacade:
    return DesktopWorkbenchFacade(
        paths,
        theme_reader=_ThemeReader(),
        supplemental_reader=_SupplementalReader(),
        curriculum_reader=curriculum,
        search_reader=search,
        provider_store=_ProviderStore(),
        state_store=DesktopStateStore(paths.state_root),
    )


def test_facade_exposes_cached_explicit_curriculum_tree(paths: DesktopPaths) -> None:
    curriculum = _CurriculumReader()
    facade = _facade(paths, curriculum, _SearchReader())

    first = facade.curriculum_catalog()
    first["volumes"][0]["volume_title"] = "不应写回"
    second = facade.curriculum_catalog()

    assert curriculum.catalog_calls == 1
    assert second["volumes"][0]["volume_title"] == "必修第一册"
    assert second["volumes"][0]["chapters"][0]["sections"][0]["section_key"] == (
        "TB-M1-C1:1.2"
    )


def test_facade_passes_cascade_as_explicit_curriculum_selector(paths: DesktopPaths) -> None:
    curriculum = _CurriculumReader()
    search = _SearchReader()
    facade = _facade(paths, curriculum, search)

    result = facade.search_themes(
        scope="master",
        query="物质的量",
        volume_id="TB-M1",
        chapter_id="TB-M1-C1",
        section="TB-M1-C1:1.2",
    )

    assert result.total_themes == 1
    assert search.payloads[-1]["curriculum"] == {
        "volume_id": "TB-M1",
        "chapter_id": "TB-M1-C1",
        "section": "TB-M1-C1:1.2",
    }
    assert curriculum.search_calls == [
        {
            "volume_id": "TB-M1",
            "chapter_id": "TB-M1-C1",
            "section": "TB-M1-C1:1.2",
        }
    ]
    assert search.curriculum_projection is not None


def test_facade_accepts_reader_shaped_curriculum_mapping_argument(paths: DesktopPaths) -> None:
    curriculum = _CurriculumReader()
    search = _SearchReader()
    facade = _facade(paths, curriculum, search)

    facade.search_themes(
        scope="master",
        curriculum={"volume_id": "TB-M1", "mapping_status": "complete"},
    )
    assert search.payloads[-1]["curriculum"] == {
        "volume_id": "TB-M1",
        "mapping_status": "complete",
    }
    assert curriculum.search_calls[-1] == {
        "volume_id": "TB-M1",
        "mapping_status": "complete",
    }


def test_personal_handout_scope_cannot_receive_textbook_selector(paths: DesktopPaths) -> None:
    facade = _facade(paths, _CurriculumReader(), _SearchReader())
    with pytest.raises(
        DesktopFacadeError,
        match="我的讲义尚未建立教材目录映射",
    ):
        facade.search_themes(
            scope=PERSONAL_HANDOUT_SCOPE,
            volume_id="TB-M1",
        )
