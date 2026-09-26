from __future__ import annotations

import re
import shutil
import subprocess
from html.parser import HTMLParser
from pathlib import Path
from tempfile import TemporaryDirectory

import pytest
import yaml
from jsonschema import Draft202012Validator
from referencing import Registry, Resource
from referencing.jsonschema import DRAFT202012

from integrations.deeptutor_shchem_v1.public_kb import PublicKBReader
from integrations.deeptutor_shchem_v1.theme_review_workbench import (
    ThemeReviewGateway,
    ThemeReviewTaskCatalog,
)
from integrations.deeptutor_shchem_v1.theme_workbench import ThemeWorkbenchReader

WORKSPACE = Path(__file__).resolve().parents[4]
OVERLAY = WORKSPACE / "runtime/deeptutor_shchem/overlay"
INDEX = OVERLAY / "index.html"
APP = OVERLAY / "app.js"
STYLES = OVERLAY / "styles.css"
OPENAPI = WORKSPACE / "staging/coordination/deeptutor_gateway/contracts/gateway_openapi_v1.yaml"


@pytest.fixture(scope="module")
def live_review_catalog() -> ThemeReviewTaskCatalog:
    root = WORKSPACE / "sh-chem-db"
    return ThemeReviewTaskCatalog.build_live(
        PublicKBReader(root), ThemeWorkbenchReader(root)
    )


def _slice(source: str, start: str, end: str) -> str:
    begin = source.index(start)
    return source[begin : source.index(end, begin)]


class _ButtonNestingParser(HTMLParser):
    def __init__(self) -> None:
        super().__init__()
        self.button_depth = 0
        self.nested_button = False

    def handle_starttag(self, tag: str, attrs) -> None:
        if tag == "button":
            if self.button_depth:
                self.nested_button = True
            self.button_depth += 1

    def handle_endtag(self, tag: str) -> None:
        if tag == "button":
            self.button_depth -= 1


def test_review_workspace_stays_inside_question_bank_and_uses_chinese_teacher_copy() -> None:
    html = INDEX.read_text(encoding="utf-8")
    panel = html.split('id="panel-candidate-review"', 1)[1].split(
        'id="panel-taxonomy"', 1
    )[0]
    assert 'id="themeReviewOpen"' in panel
    assert 'id="themeReviewWorkspace"' in panel
    assert 'aria-controls="themeReviewWorkspace"' in panel
    assert 'id="tab-theme-review"' not in html
    assert "整主题复核" in panel
    assert "共享材料" in panel
    assert "完整主题大题" in panel
    assert "不写死题量" in panel
    assert "不会改写 Master" in panel
    assert "不等于 human reviewed、正式题库、教学可用、自动判分、命题可用或发布" in panel
    launcher = panel.split('class="card theme-review-launcher"', 1)[1].split(
        'class="candidate-scan-overview"', 1
    )[0]
    workspace = panel.split('id="themeReviewWorkspace"', 1)[1].split(
        'id="candidateReviewForm"', 1
    )[0]
    review_surface = launcher + workspace
    for stale_count in ("135", "43", "22", "81", "33", "191"):
        assert stale_count not in review_surface


def test_theme_cards_use_sibling_actions_instead_of_nested_buttons() -> None:
    app = APP.read_text(encoding="utf-8")
    card = _slice(
        app,
        "function candidateReviewThemeCard(entry)",
        "function renderCandidateReviewThemeOverview",
    )
    assert 'document.createElement("article")' in card
    assert 'actions.className = "candidate-theme-card-actions"' in card
    assert 'browse.textContent = "查看完整题链"' in card
    assert 'addTheme.textContent = "加入整主题"' in card
    assert 'favorite.dataset.personalFavoriteKind = "theme"' in card
    assert 'review.textContent = "整理标签"' in card
    assert "openCandidateReviewThemeChain(theme.id)" in card
    assert "openThemeReviewWorkspace(task?.task_id || null)" in card

    parser = _ButtonNestingParser()
    parser.feed(INDEX.read_text(encoding="utf-8"))
    assert parser.button_depth == 0
    assert parser.nested_button is False


def test_review_api_routes_and_exact_cas_payloads_are_wired_without_user_json() -> None:
    app = APP.read_text(encoding="utf-8")
    html = INDEX.read_text(encoding="utf-8")
    for route in (
        'api("/api/v1/review/tasks?limit=200&offset=0")',
        "/api/v1/review/tasks/${encodeURIComponent(taskId)}",
        "/claim",
        "/release",
        "/change-sets",
        "/change-sets/${encodeURIComponent(changeSetId)}/preview",
        "/decisions",
    ):
        assert route in app
    for field in (
        "expected_revision",
        "idempotency_key",
        "base_task_input_sha256",
        "tag_replacements",
        "hierarchy_replacements",
        "atomic_boundary_candidates",
        "dependency_replacements",
        "change_set_id",
        "verdict",
        "reason_zh",
        "evidence_ids",
    ):
        assert field in app
    assert "theme_review_${" in app
    workspace = html.split('id="themeReviewWorkspace"', 1)[1].split(
        'id="candidateReviewForm"', 1
    )[0]
    assert "JSON object" not in workspace
    assert "受控 JSON" not in workspace
    assert 'id="themeReviewAtomicRows"' in workspace
    assert 'id="themeReviewHierarchyPrinted"' in workspace
    assert 'id="themeReviewDependencyAtomic"' in workspace
    assert 'id="themeReviewBoundaryRows"' in workspace


def test_atomic_editor_is_a_native_accessible_table_with_controlled_decisions() -> None:
    html = INDEX.read_text(encoding="utf-8")
    workspace = html.split('id="themeReviewWorkspace"', 1)[1].split(
        'id="candidateReviewForm"', 1
    )[0]
    assert '<table class="theme-review-atomic-table">' in workspace
    assert '<caption id="themeReviewAtomicCaption">' in workspace
    assert workspace.count('scope="col"') == 6
    assert 'role="region"' in workspace
    assert 'tabindex="0"' in workspace
    assert "选择新标签" in workspace
    assert "题图" in workspace
    assert "证据</th>" not in workspace
    for verdict, label in (
        ("accept_candidate_overlay", "接受候选层"),
        ("reject", "驳回"),
        ("request_changes", "退回修改"),
        ("blocked", "阻断"),
    ):
        assert f'data-theme-review-verdict="{verdict}"' in workspace
        assert label in workspace
    assert "接受只表示候选覆盖层" in workspace


def test_client_rejects_authority_elevation_and_accept_requires_valid_preview() -> None:
    app = APP.read_text(encoding="utf-8")
    authority = _slice(
        app,
        "function themeReviewAuthority(value)",
        "function themeReviewTaskSummary",
    )
    controls = _slice(
        app,
        "function updateThemeReviewControls",
        "function themeReviewTaskMatchesFilters",
    )
    assert "value.candidate_only !== true" in authority
    for field in (
        "human_reviewed",
        "master_mutated",
        "central_master_mutated",
        "retrieval_ready",
        "teaching_use_allowed",
        "auto_score_allowed",
        "generation_allowed",
        "publication_allowed",
        "official",
        "external_release_allowed",
    ):
        assert f'"{field}"' in authority
    assert "state.themeReviewPreview?.validation_pass !== true" in controls
    assert "accept_candidate_overlay" in app
    assert "Master、human reviewed、教学、判分、命题和发布状态均未改变" in app


def test_dependency_vocab_and_change_rows_are_closed_and_theme_local() -> None:
    app = APP.read_text(encoding="utf-8")
    for kind in (
        "uses_prior_answer",
        "uses_prior_calculated_value",
        "uses_prior_identified_substance",
        "uses_prior_structure",
        "uses_prior_experimental_conclusion",
    ):
        assert f'"{kind}"' in app
    dependency = _slice(
        app,
        "function themeReviewDependencyReplacements",
        "function buildThemeReviewChangeSetRequest",
    )
    assert "themeReviewAtomicOrder(row) < themeReviewAtomicOrder(dependent)" in dependency
    assert "依赖边只能指向同主题中卷面顺序更早的 atomic" in dependency
    assert "shared_material" not in dependency
    boundary = _slice(
        app,
        "function themeReviewBoundaryCandidates",
        "function themeReviewDependencyReplacements",
    )
    assert 'operation: "split_into_atomic_parts"' in boundary
    assert "candidate_atomic_parts: candidateAtomicParts" in boundary
    assert "至少需要两个作答单元" in boundary


def test_source_version_card_separates_evidence_layers_without_paths_or_urls() -> None:
    html = INDEX.read_text(encoding="utf-8")
    workspace = html.split('id="themeReviewWorkspace"', 1)[1].split(
        'id="candidateReviewForm"', 1
    )[0]
    for element_id in (
        "themeReviewSourceStatus",
        "themeReviewPaperFace",
        "themeReviewArticleAttribution",
        "themeReviewSourceVersion",
        "themeReviewSourceAuthority",
        "themeReviewSourceBlockers",
        "themeReviewSourceFieldset",
        "themeReviewSourceCandidates",
    ):
        assert f'id="{element_id}"' in workspace
    for copy in (
        "来源与版本",
        "需要核对出处时展开",
        "卷面直接身份",
        "公众号标题归因",
        "来源与冻结版本",
        "权威与使用边界",
        "不显示本地路径或网址",
    ):
        assert copy in workspace
    assert '<details class="theme-review-source"' in workspace
    assert "来源绑定候选（需要时展开）" in workspace
    assert "不需要手填哈希或证据编号" in workspace


def test_source_binding_actions_are_closed_hash_bound_and_fail_closed() -> None:
    app = APP.read_text(encoding="utf-8")
    validation = _slice(
        app,
        "function themeReviewSourceCandidate(value)",
        "function themeReviewSourceBindingStateLabel",
    )
    for field in (
        "candidate_id",
        "candidate_sha256",
        "source_id",
        "source_version_id",
        "binding_state",
        "accept_allowed",
        "evidence_binding_ids",
    ):
        assert f'"{field}"' in validation
    assert 'value.accept_allowed !== (value.binding_state === "exact_content_set_candidate")' in validation
    for action in (
        "accept_binding_candidate",
        "reject_binding_candidate",
        "request_source_evidence",
        "block_identity_binding",
    ):
        assert f'"{action}"' in app
    rows = _slice(
        app,
        "function themeReviewSourceBindingCandidates",
        "function buildThemeReviewChangeSetRequest",
    )
    assert 'candidate.binding_state !== "exact_content_set_candidate"' in rows
    assert "只有精确内容集候选可以接受来源绑定" in rows
    assert "candidate_sha256: candidate.candidate_sha256" in rows
    assert "source_version_id: candidate.source_version_id" in rows
    request = _slice(
        app,
        "function buildThemeReviewChangeSetRequest",
        "async function submitThemeReviewChangeSet",
    )
    assert 'if (task.source_binding !== null)' in request
    assert "payload.source_binding_candidates = themeReviewSourceBindingCandidates(task, reason)" in request
    assert 'Object.hasOwn(payload, "source_binding_candidates")' in request


def test_source_binding_projection_is_exact_nullable_and_safe_to_render() -> None:
    app = APP.read_text(encoding="utf-8")
    source = _slice(
        app,
        "function themeReviewAssertSafeSourceProjection",
        "function themeReviewSourceBindingStateLabel",
    )
    for field in (
        "schema_version",
        "paper_id",
        "master_source_id",
        "source_version_id",
        "source_version_sha256",
        "source_layer",
        "package_id",
        "paper_face",
        "article_attribution",
        "catalog_binding",
        "authority",
        "blockers",
        "evidence_ids",
    ):
        assert f'"{field}"' in source
    assert "含路径或网址字段" in source
    assert "含本地路径或网址内容" in source
    assert 'value.source_version_id !== `SHCHEM-SV-${value.source_version_sha256}`' in source
    assert "authority.human_source_reviewed !== false" in source
    assert "authority.rights_cleared !== false" in source
    assert "authority.no_authority_elevation !== true" in source
    source_task = _slice(
        app,
        "function themeReviewTaskSourceProjection",
        "function themeReviewAuthority",
    )
    assert 'value.source_binding === null' in source_task
    assert "旧 v1 任务不得携带来源动作候选" in source_task
    assert 'sourceBinding.source_version_id === null' in source_task
    assert 'sourceBinding.catalog_binding.match_basis !== "no_frozen_crosswalk_candidate"' in source_task
    render = _slice(
        app,
        "function renderThemeReviewSourceBinding",
        "function renderThemeReviewAtomicRows",
    )
    assert "尚未建立冻结版本候选" in render
    assert "学校名仅来自公众号标题" not in render  # blocker message is server-bound, not inferred here
    assert "不提升官方、人审、教学、判分、命题或发布资格" in render


def test_source_binding_preview_keeps_candidate_only_authority_boundary() -> None:
    app = APP.read_text(encoding="utf-8")
    preview = _slice(
        app,
        "function validateThemeReviewPreview(value)",
        "async function loadThemeReviewPreview",
    )
    assert "themeReviewAuthority(value.authority)" in preview
    assert "接受仍只进入 candidate overlay" in preview
    assert "接受候选层" in preview


def test_noncanonical_theme_routes_source_actions_to_one_paper_task() -> None:
    app = APP.read_text(encoding="utf-8")
    renderer = _slice(
        app,
        "function renderThemeReviewSourceCandidates(task)",
        "function themeReviewPaperFaceStatusLabel",
    )
    assert "row.paper_id === task.paper_id" in renderer
    assert "row.source_binding_candidates.length === 1" in renderer
    assert "本主题只读显示本卷来源上下文" in renderer
    assert "请到本卷指定来源复核任务处理" in renderer
    assert "selectThemeReviewTask(canonical.task_id)" in renderer
    assert "不会自行生成 source/version 或来源动作" in renderer
    controls = _slice(
        app,
        "function updateThemeReviewControls",
        "function themeReviewTaskMatchesFilters",
    )
    assert "task?.source_binding !== null" in controls
    assert "task?.source_binding_candidates?.length === 1" in controls
    assert '$("themeReviewSourceFieldset").disabled = !sourceActionWritable' in controls


def test_live_catalog_has_three_canonical_source_tasks_one_acceptable_and_no_locations(
    live_review_catalog: ThemeReviewTaskCatalog,
) -> None:
    catalog = live_review_catalog.as_dict()
    tasks = catalog["tasks"]
    canonical = [row for row in tasks if row["source_binding_candidates"]]
    assert len(canonical) == 3
    assert len({row["paper_id"] for row in canonical}) == 3
    assert all(len(row["source_binding_candidates"]) == 1 for row in canonical)
    assert sum(
        row["source_binding_candidates"][0]["accept_allowed"] is True
        for row in canonical
    ) == 1
    canonical_ids_by_paper = {row["paper_id"]: row["task_id"] for row in canonical}
    for row in tasks:
        if row["paper_id"] in canonical_ids_by_paper and row["task_id"] != canonical_ids_by_paper[row["paper_id"]]:
            assert row["source_binding_candidates"] == []
            blocker_codes = {
                blocker["code"] for blocker in row["source_binding"]["blockers"]
            }
            assert "source_binding_action_delegated_to_canonical_task" in blocker_codes

    forbidden_keys = {
        "path",
        "relative_path",
        "absolute_path",
        "source_path",
        "url",
        "uri",
        "source_url",
        "file_url",
    }
    location = re.compile(
        r"(?:[A-Za-z]:[\\/]|\\\\[^\\\s]+\\|(?:https?|ftp|file|data|javascript):/{0,2})",
        re.IGNORECASE,
    )

    def assert_safe(value) -> None:
        if isinstance(value, dict):
            assert not forbidden_keys.intersection(value)
            for child in value.values():
                assert_safe(child)
        elif isinstance(value, list):
            for child in value:
                assert_safe(child)
        elif isinstance(value, str):
            assert location.search(value) is None

    for row in tasks:
        assert_safe(row["source_binding"])
        assert_safe(row["source_binding_candidates"])


def test_openapi_validates_every_live_and_legacy_source_task_projection(
    live_review_catalog: ThemeReviewTaskCatalog,
) -> None:
    document = yaml.safe_load(OPENAPI.read_text(encoding="utf-8"))
    registry = Registry().with_resource(
        "urn:shchem:gateway-openapi", Resource(document, DRAFT202012)
    )
    schema = {
        "$ref": "urn:shchem:gateway-openapi#/components/schemas/ThemeReviewTaskProjection"
    }
    validator = Draft202012Validator(schema, registry=registry)
    with TemporaryDirectory() as state_root:
        gateway = ThemeReviewGateway(
            catalog=live_review_catalog,
            state_root=Path(state_root),
            release_context={"serving_release_id": "WBREL-openapi-task-projection"},
        )
        rows = gateway.list(limit=200)["items"]
    assert len(rows) == len(live_review_catalog.tasks)
    for row in rows:
        errors = list(validator.iter_errors(row))
        assert not errors, "\n".join(
            f"{row['task_id']} {list(error.path)}: {error.message}"
            for error in errors
        )

    legacy = dict(rows[0])
    legacy["source_binding"] = None
    legacy["source_binding_candidates"] = []
    errors = list(validator.iter_errors(legacy))
    assert not errors, "\n".join(
        f"legacy {list(error.path)}: {error.message}" for error in errors
    )


def test_openapi_validates_live_source_change_preview_and_decision_responses(
    live_review_catalog: ThemeReviewTaskCatalog,
) -> None:
    canonical = [
        row for row in live_review_catalog.tasks if row["source_binding_candidates"]
    ]
    selected = next(
        row
        for row in canonical
        if row["source_binding_candidates"][0]["accept_allowed"] is True
    )
    candidate = selected["source_binding_candidates"][0]
    with TemporaryDirectory() as state_root:
        gateway = ThemeReviewGateway(
            catalog=live_review_catalog,
            state_root=Path(state_root),
            release_context={"serving_release_id": "WBREL-openapi-source-test"},
        )
        gateway.claim(
            selected["task_id"],
            {
                "expected_revision": None,
                "idempotency_key": "source_openapi_claim_0001",
            },
            principal_id="teacher-source-openapi",
        )
        detail = gateway.get(selected["task_id"])
        change = gateway.change_set(
            selected["task_id"],
            {
                "expected_revision": detail["revision"],
                "idempotency_key": "source_openapi_change_0001",
                "base_task_input_sha256": detail["base"]["task_input_sha256"],
                "tag_replacements": [],
                "hierarchy_replacements": [],
                "atomic_boundary_candidates": [],
                "dependency_replacements": [],
                "source_binding_candidates": [
                    {
                        "candidate_id": candidate["candidate_id"],
                        "candidate_sha256": candidate["candidate_sha256"],
                        "source_version_id": candidate["source_version_id"],
                        "action": "accept_binding_candidate",
                        "reason_zh": "页面哈希闭集与中央来源记录精确一致，仅记录来源版本候选。",
                        "evidence_ids": candidate["evidence_binding_ids"],
                    }
                ],
            },
            principal_id="teacher-source-openapi",
        )
        preview = gateway.preview(selected["task_id"], change["change_set_id"])
        decision = gateway.decision(
            selected["task_id"],
            {
                "expected_revision": change["state"]["revision"],
                "idempotency_key": "source_openapi_decision_0001",
                "change_set_id": change["change_set_id"],
                "verdict": "accept_candidate_overlay",
                "reason_zh": "确认只接受来源版本候选层，不提升人审、教学、判分或发布资格。",
                "evidence_ids": candidate["evidence_binding_ids"],
            },
            principal_id="teacher-source-openapi",
        )

    document = yaml.safe_load(OPENAPI.read_text(encoding="utf-8"))
    registry = Registry().with_resource(
        "urn:shchem:gateway-openapi", Resource(document, DRAFT202012)
    )
    for schema_name, data in (
        ("ThemeReviewChangeSetMutationEnvelope", change),
        ("ThemeReviewPreviewEnvelope", preview),
        ("ThemeReviewDecisionMutationEnvelope", decision),
    ):
        envelope = {
            "contract_version": "shchem.gateway.v1",
            "request_id": "req-source-openapi",
            "data": data,
        }
        schema = {
            "$ref": f"urn:shchem:gateway-openapi#/components/schemas/{schema_name}"
        }
        errors = list(Draft202012Validator(schema, registry=registry).iter_errors(envelope))
        assert not errors, "\n".join(
            f"{list(error.path)}: {error.message}" for error in errors
        )


def test_openapi_120_keeps_v1_compatible_and_makes_source_contracts_strict() -> None:
    document = yaml.safe_load(OPENAPI.read_text(encoding="utf-8"))
    assert document["info"]["version"] == "1.24.0"
    schemas = document["components"]["schemas"]
    request = schemas["ThemeReviewChangeSetRequest"]
    assert request["oneOf"] == [
        {"$ref": "#/components/schemas/ThemeReviewChangeSetRequestV1"},
        {"$ref": "#/components/schemas/ThemeReviewChangeSetRequestV2"},
    ]
    assert "source_binding_candidates" not in schemas["ThemeReviewChangeSetRequestV1"]["required"]
    assert "source_binding_candidates" in schemas["ThemeReviewChangeSetRequestV2"]["required"]
    assert schemas["ThemeReviewChangeSetRequestV1"]["additionalProperties"] is False
    assert schemas["ThemeReviewChangeSetRequestV2"]["additionalProperties"] is False
    for name in (
        "ThemeReviewSourceBindingCandidate",
        "ThemeReviewSourceBindingAction",
        "ThemeReviewSourcePaperFace",
        "ThemeReviewSourceArticleAttribution",
        "ThemeReviewSourceCatalogBinding",
        "ThemeReviewSourceAuthority",
        "ThemeReviewSourceBlocker",
        "ThemeReviewSourceBinding",
        "ThemeReviewTaskProjectionBase",
        "ThemeReviewTaskListEnvelope",
        "ThemeReviewTaskDetailEnvelope",
        "ThemeReviewChangeSetMutationData",
        "ThemeReviewDecisionMutationData",
        "ThemeReviewChangeSetMutationEnvelope",
        "ThemeReviewPreviewData",
        "ThemeReviewPreviewEnvelope",
        "ThemeReviewDecisionMutationEnvelope",
    ):
        assert schemas[name]["additionalProperties"] is False
    assert schemas["ThemeReviewTaskProjection"]["oneOf"] == [
        {"$ref": "#/components/schemas/ThemeReviewTaskProjectionV1"},
        {"$ref": "#/components/schemas/ThemeReviewTaskProjectionV2"},
    ]
    v1_source = schemas["ThemeReviewTaskProjectionV1"]["allOf"][1]["properties"]
    v2_source = schemas["ThemeReviewTaskProjectionV2"]["allOf"][1]["properties"]
    assert v1_source["source_binding"] == {"type": "null"}
    assert v1_source["source_binding_candidates"]["maxItems"] == 0
    assert v2_source["source_binding"] == {
        "$ref": "#/components/schemas/ThemeReviewSourceBinding"
    }
    assert v2_source["source_binding_candidates"]["maxItems"] == 1
    action_enum = schemas["ThemeReviewSourceBindingAction"]["properties"]["action"]["enum"]
    assert action_enum == [
        "accept_binding_candidate",
        "reject_binding_candidate",
        "request_source_evidence",
        "block_identity_binding",
    ]
    source = schemas["ThemeReviewSourceBinding"]
    assert source["properties"]["source_version_id"]["oneOf"][-1] == {"type": "null"}
    assert source["properties"]["source_version_sha256"]["oneOf"][-1] == {"type": "null"}
    assert schemas["ThemeReviewTaskProjectionBase"]["properties"]["source_binding"]["oneOf"][-1] == {
        "type": "null"
    }
    paths = document["paths"]
    base = "/api/v1/review/tasks/{task_id}"
    assert paths[f"{base}/change-sets"]["post"]["responses"]["201"]["content"]["application/json"]["schema"] == {
        "$ref": "#/components/schemas/ThemeReviewChangeSetMutationEnvelope"
    }
    assert paths[f"{base}/change-sets/{{change_set_id}}/preview"]["get"]["responses"]["200"]["content"]["application/json"]["schema"] == {
        "$ref": "#/components/schemas/ThemeReviewPreviewEnvelope"
    }
    assert paths[f"{base}/decisions"]["post"]["responses"]["201"]["content"]["application/json"]["schema"] == {
        "$ref": "#/components/schemas/ThemeReviewDecisionMutationEnvelope"
    }


def test_review_layout_supports_narrow_zoom_keyboard_and_system_preferences() -> None:
    css = STYLES.read_text(encoding="utf-8")
    assert ".theme-review-table-scroll" in css
    assert "overflow-x: auto" in css
    assert ".theme-review-atomic-table" in css
    assert "min-width: 920px" in css
    narrow = css[css.index("@media (max-width: 760px)") :]
    assert ".theme-review-launcher" in narrow
    assert ".theme-review-filter" in narrow
    assert ".theme-review-structure-grid" in narrow
    assert ".theme-review-boundary-row" in narrow
    assert ".theme-review-source-grid" in narrow
    assert ".theme-review-source-candidate-controls" in narrow
    assert "grid-template-columns: minmax(0, 1fr)" in narrow
    assert "@media (prefers-reduced-motion: reduce)" in css
    assert "@media (forced-colors: active)" in css
    assert "textarea:focus-visible" in css
    assert "[tabindex]:focus-visible" in css


def test_review_javascript_has_valid_syntax() -> None:
    node = shutil.which("node")
    if not node:
        pytest.skip("Node.js is unavailable")
    completed = subprocess.run(
        [node, "--check", str(APP)],
        cwd=WORKSPACE,
        capture_output=True,
        text=True,
        encoding="utf-8",
        check=False,
    )
    assert completed.returncode == 0, completed.stdout + completed.stderr
