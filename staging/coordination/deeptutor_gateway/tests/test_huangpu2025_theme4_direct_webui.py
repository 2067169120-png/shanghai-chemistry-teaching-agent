from __future__ import annotations

import json
import subprocess
from pathlib import Path

import pytest

from integrations.deeptutor_shchem_v1.master_direct_visual_scan import (
    MasterDirectVisualScanReader,
)

WORKSPACE = Path(__file__).resolve().parents[4]
OVERLAY = WORKSPACE / "runtime/deeptutor_shchem/overlay"
HUANGPU_PRODUCT = "QVS-HP2025-SECOND-MOCK-T4-CALCIUM-IODATE-V1"
QIBAO_PRODUCT = "QVS-QB2025-OPENING-T4-ELECTROLYTE-WASTEWATER-V1"
HONGKOU_PRODUCT = "QVS-HK2026-SECOND-MOCK-T4-NICKEL-RECOVERY-V1"


def _direct_product_constants_js() -> str:
    return "\n".join(
        (
            f'const candidateReviewHuangpuDirectProductId = "{HUANGPU_PRODUCT}";',
            f'const candidateReviewQibaoDirectProductId = "{QIBAO_PRODUCT}";',
            f'const candidateReviewHongkouDirectProductId = "{HONGKOU_PRODUCT}";',
        )
    )


def _extract_js_function(app: str, name: str) -> str:
    start = app.index(f"  function {name}(") + 2
    end = app.index("\n  function ", start)
    return app[start:end].strip()


def _run_js(tmp_path: Path, sources: list[str], expression: str):
    script = "\n".join(sources) + f"\nprocess.stdout.write(JSON.stringify({expression}));"
    script_path = tmp_path / "huangpu-webui-contract.js"
    script_path.write_text(script, encoding="utf-8")
    completed = subprocess.run(
        ["node", str(script_path)],
        check=True,
        capture_output=True,
        text=True,
        encoding="utf-8",
    )
    return json.loads(completed.stdout)


@pytest.fixture(scope="module")
def huangpu_public_data():
    reader = MasterDirectVisualScanReader(WORKSPACE / "sh-chem-db")
    catalog = reader.catalog()
    product = next(
        row for row in catalog["products"] if row["product_id"] == HUANGPU_PRODUCT
    )
    item = next(
        row for row in catalog["items"] if row["product_id"] == HUANGPU_PRODUCT
    )
    return catalog, product, item, reader.detail(item["master_node_id"])


@pytest.fixture(scope="module")
def qibao_hongkou_public_data():
    reader = MasterDirectVisualScanReader(WORKSPACE / "sh-chem-db")
    catalog = reader.catalog()
    fixtures = []
    for product_id, kind in (
        (QIBAO_PRODUCT, "qibao_theme"),
        (HONGKOU_PRODUCT, "hongkou_theme"),
    ):
        raw_product = next(
            row for row in catalog["products"] if row["product_id"] == product_id
        )
        item = next(
            row
            for row in catalog["items"]
            if row["product_id"] == product_id
            and (
                row["textbook_mapping_status"] == "partial_blocked"
                if product_id == QIBAO_PRODUCT
                else row["master_node_id"] == "HK2026-EM-S4-Q5-P1"
            )
        )
        fixtures.append(
            {
                "item": item,
                "product": {
                    **raw_product,
                    "scope": catalog["scope"],
                    "identity_profile": {
                        "kind": kind,
                        **raw_product["paper_identity_boundary"],
                    },
                },
                "detail": reader.detail(item["master_node_id"]),
            }
        )
    return fixtures


def test_huangpu_catalog_title_and_identity_are_explicit_and_fail_closed(
    tmp_path: Path, huangpu_public_data
):
    _, product, _, _ = huangpu_public_data
    app = (OVERLAY / "app.js").read_text(encoding="utf-8")
    result = _run_js(
        tmp_path,
        [
            _direct_product_constants_js(),
            _extract_js_function(app, "candidateReviewMasterDirectProductUiDescriptor"),
            _extract_js_function(app, "validateCandidateReviewMasterDirectCatalogIdentity"),
            f"const product = {json.dumps(product, ensure_ascii=False)};",
        ],
        """(() => {
          const descriptor = candidateReviewMasterDirectProductUiDescriptor(product);
          const profile = validateCandidateReviewMasterDirectCatalogIdentity(
            product.paper_identity_boundary,
            product.product_id,
          );
          const withExtra = { ...product.paper_identity_boundary, arbitrary_field: true };
          let extraRejected = false;
          let wrongProductRejected = false;
          try { validateCandidateReviewMasterDirectCatalogIdentity(withExtra, product.product_id); }
          catch (_) { extraRejected = true; }
          try { validateCandidateReviewMasterDirectCatalogIdentity(product.paper_identity_boundary, "OTHER-DIRECT-PRODUCT"); }
          catch (_) { wrongProductRejected = true; }
          return { descriptor, kind: profile.kind, extraRejected, wrongProductRejected };
        })()""",
    )
    assert result["descriptor"]["display_badge_zh"] == "黄浦区 2025 二模 · 主题四（碘酸钙的制备）"
    assert result["descriptor"]["option_label"] == (
        "逐图扫描·黄浦区 2025 二模 · 主题四（碘酸钙的制备）（11题）"
    )
    assert "非官方" in result["descriptor"]["source_boundary_zh"]
    assert "未经系统独立核验" in result["descriptor"]["source_boundary_zh"]
    assert result["kind"] == "huangpu_theme"
    assert result["extraRejected"] is True
    assert result["wrongProductRejected"] is True


def test_real_214_item_catalog_accepts_huangpu_only_with_its_extra_keys(
    tmp_path: Path, huangpu_public_data
):
    catalog, _, item, _ = huangpu_public_data
    qibao_item = next(row for row in catalog["items"] if row["product_id"] == QIBAO_PRODUCT)
    hongkou_item = next(
        row for row in catalog["items"] if row["product_id"] == HONGKOU_PRODUCT
    )
    app = (OVERLAY / "app.js").read_text(encoding="utf-8")
    result = _run_js(
        tmp_path,
        [
            _direct_product_constants_js(),
            """const candidateReviewMasterDirectScanAggregate = {
              product_id: "MASTER-DIRECT-VISUAL-SCAN-AGGREGATE-V1",
              scope: "candidate_only_read_only_master_direct_visual_scan",
            };""",
            """const candidateReviewVisualFieldLabels = {
              item_type: true, primary_K: true, supporting_K: true, A: true, C: true,
              R: true, RP: true, D: true, selection_rule: true,
            };""",
            _extract_js_function(app, "candidateReviewVisualArray"),
            _extract_js_function(app, "candidateReviewMasterDirectProductUiDescriptor"),
            _extract_js_function(app, "validateCandidateReviewVisualScanAuthority"),
            _extract_js_function(app, "validateCandidateReviewMasterDirectScanIntegrity"),
            _extract_js_function(app, "validateCandidateReviewMasterDirectCatalogIdentity"),
            _extract_js_function(app, "validateMasterCandidateReviewDirectScanCatalog"),
            f"const catalog = {json.dumps(catalog, ensure_ascii=False)};",
            f'const huangpuNodeId = {json.dumps(item["master_node_id"])};',
            f'const qibaoNodeId = {json.dumps(qibao_item["master_node_id"])};',
            f'const hongkouNodeId = {json.dumps(hongkou_item["master_node_id"])};',
        ],
        """(() => {
          const masterItems = catalog.master_node_ids.map((node_id) => ({ node_id }));
          const valid = validateMasterCandidateReviewDirectScanCatalog(
            catalog,
            masterItems,
            new Map(),
          );
          const mutated = structuredClone(catalog);
          const legacyItem = mutated.items.find((entry) => entry.product_id !== candidateReviewHuangpuDirectProductId);
          legacyItem.source_year = 2025;
          let legacyExtraRejected = false;
          try { validateMasterCandidateReviewDirectScanCatalog(mutated, masterItems, new Map()); }
          catch (_) { legacyExtraRejected = true; }
          return {
            accepted: valid.byNode.size,
            products: valid.productsById.size,
            huangpuAccepted: valid.byNode.get(huangpuNodeId)?.source_region_or_school === "黄浦区",
            qibaoAccepted: valid.byNode.get(qibaoNodeId)?.source_region_or_school === "上海市七宝中学（卷面直接身份）",
            hongkouAccepted: valid.byNode.get(hongkouNodeId)?.source_region_or_school === "虹口区（公众号标题；卷面未署地区）",
            legacyExtraRejected,
          };
        })()""",
    )
    assert result == {
        "accepted": 214,
        "products": 9,
        "huangpuAccepted": True,
        "qibaoAccepted": True,
        "hongkouAccepted": True,
        "legacyExtraRejected": True,
    }


def test_real_huangpu_compact_detail_validates_and_rejects_cross_product_keys(
    tmp_path: Path, huangpu_public_data
):
    catalog, raw_product, item, detail = huangpu_public_data
    app = (OVERLAY / "app.js").read_text(encoding="utf-8")
    product = {
        **raw_product,
        "scope": catalog["scope"],
        "identity_profile": {
            "kind": "huangpu_theme",
            **raw_product["paper_identity_boundary"],
        },
    }
    state = {
        "item": item,
        "product": product,
        "detail": detail,
    }
    result = _run_js(
        tmp_path,
        [
            _direct_product_constants_js(),
            """const candidateReviewVisualDimensionLabels = {
              information_transformations: true, reasoning_chain_steps: true,
              knowledge_module_span: true, representation_switches: true,
              calculation_load: true, experiment_load: true, openness: true,
              unfamiliarity: true, language_load: true, dependency_on_prior_parts: true,
            };""",
            _extract_js_function(app, "candidateReviewVisualArray"),
            _extract_js_function(app, "validateCandidateReviewReferenceAnswer"),
            _extract_js_function(app, "validateCandidateReviewVisualScanAuthority"),
            _extract_js_function(app, "validateCandidateReviewMasterDirectScanIntegrity"),
            _extract_js_function(app, "validateCandidateReviewMasterDirectDetailIdentity"),
            _extract_js_function(app, "validateCandidateReviewHuangpuSourceIdentity"),
            _extract_js_function(app, "validateCandidateReviewHuangpuTextbookDirectoryMapping"),
            _extract_js_function(app, "validateCandidateReviewHuangpuDirectFields"),
            _extract_js_function(app, "validateCandidateReviewMasterDirectScanDetail"),
            f"const fixture = {json.dumps(state, ensure_ascii=False)};",
            """const state = {
              candidateReviewMasterDirectScanByNode: new Map([[fixture.item.master_node_id, fixture.item]]),
              candidateReviewMasterDirectProductById: new Map([[fixture.product.product_id, fixture.product]]),
            };""",
        ],
        """(() => {
          const valid = validateCandidateReviewMasterDirectScanDetail(
            fixture.detail,
            fixture.item.master_node_id,
          );
          const attempts = [];
          const addedLegacyField = structuredClone(fixture.detail);
          addedLegacyField.candidate_analysis = { candidate_only: true, correctness_verified: false, solution_path_zh: ["x"] };
          attempts.push(addedLegacyField);
          const addedSourceHash = structuredClone(fixture.detail);
          addedSourceHash.source_identity.sha256 = "0".repeat(64);
          attempts.push(addedSourceHash);
          const addedTextbookPath = structuredClone(fixture.detail);
          addedTextbookPath.textbook_directory_mapping.entries[0].path = "hidden";
          attempts.push(addedTextbookPath);
          const weakenedAnswerBoundary = structuredClone(fixture.detail);
          delete weakenedAnswerBoundary.answer_boundary.official_answer_claim_allowed;
          attempts.push(weakenedAnswerBoundary);
          const rejected = attempts.map((candidate) => {
            try { validateCandidateReviewMasterDirectScanDetail(candidate, fixture.item.master_node_id); return false; }
            catch (_) { return true; }
          });
          return {
            huangpu: valid.huangpu,
            factors: valid.factors.length,
            mapped: valid.textbookMapping.mapping_status,
            sourceYear: valid.sourceIdentity.year,
            rejected,
          };
        })()""",
    )
    assert result == {
        "huangpu": True,
        "factors": 10,
        "mapped": "complete_directory_level_unit_unknown",
        "sourceYear": 2025,
        "rejected": [True, True, True, True],
    }


def test_real_qibao_and_hongkou_compact_details_validate_with_strict_new_fields(
    tmp_path: Path, qibao_hongkou_public_data
):
    app = (OVERLAY / "app.js").read_text(encoding="utf-8")
    result = _run_js(
        tmp_path,
        [
            _direct_product_constants_js(),
            _extract_js_function(app, "candidateReviewVisualArray"),
            _extract_js_function(app, "validateCandidateReviewReferenceAnswer"),
            _extract_js_function(app, "validateCandidateReviewVisualScanAuthority"),
            _extract_js_function(app, "validateCandidateReviewMasterDirectScanIntegrity"),
            _extract_js_function(app, "validateCandidateReviewMasterDirectDetailIdentity"),
            _extract_js_function(app, "validateCandidateReviewHuangpuSourceIdentity"),
            _extract_js_function(app, "validateCandidateReviewHuangpuTextbookDirectoryMapping"),
            _extract_js_function(app, "validateCandidateReviewHuangpuDirectFields"),
            _extract_js_function(app, "validateCandidateReviewMasterDirectScanDetail"),
            f"const fixtures = {json.dumps(qibao_hongkou_public_data, ensure_ascii=False)};",
            """const state = {
              candidateReviewMasterDirectScanByNode: new Map(fixtures.map((row) => [row.item.master_node_id, row.item])),
              candidateReviewMasterDirectProductById: new Map(fixtures.map((row) => [row.product.product_id, row.product])),
            };""",
        ],
        """fixtures.map((fixture) => {
          const valid = validateCandidateReviewMasterDirectScanDetail(
            fixture.detail,
            fixture.item.master_node_id,
          );
          const attempts = [];
          const missingParentGate = structuredClone(fixture.detail);
          delete missingParentGate.integrity.four_level_parent_chain_verified_on_read;
          attempts.push(missingParentGate);
          const addedIdentityField = structuredClone(fixture.detail);
          addedIdentityField.identity_boundary.invented_identity = "forbidden";
          attempts.push(addedIdentityField);
          const addedTextbookPath = structuredClone(fixture.detail);
          addedTextbookPath.textbook_directory_mapping.entries[0].source_path = "hidden";
          attempts.push(addedTextbookPath);
          const weakenedAnswerBoundary = structuredClone(fixture.detail);
          delete weakenedAnswerBoundary.answer_boundary.official_scoring_claim_allowed;
          attempts.push(weakenedAnswerBoundary);
          return {
            productId: fixture.product.product_id,
            structuredDirect: valid.structuredDirect,
            qibao: valid.qibao,
            hongkou: valid.hongkou,
            sourceYear: valid.sourceIdentity.year,
            textbookStatus: valid.textbookMapping.mapping_status,
            rejected: attempts.map((candidate) => {
              try { validateCandidateReviewMasterDirectScanDetail(candidate, fixture.item.master_node_id); return false; }
              catch (_) { return true; }
            }),
          };
        })""",
    )
    assert result == [
        {
            "productId": QIBAO_PRODUCT,
            "structuredDirect": True,
            "qibao": True,
            "hongkou": False,
            "sourceYear": 2025,
            "textbookStatus": "partial_blocked",
            "rejected": [True, True, True, True],
        },
        {
            "productId": HONGKOU_PRODUCT,
            "structuredDirect": True,
            "qibao": False,
            "hongkou": True,
            "sourceYear": 2026,
            "textbookStatus": "complete_directory_level_unit_unknown",
            "rejected": [True, True, True, True],
        },
    ]


def test_huangpu_detail_ui_is_readable_safe_and_mobile_bounded():
    app = (OVERLAY / "app.js").read_text(encoding="utf-8")
    html = (OVERLAY / "index.html").read_text(encoding="utf-8")
    css = (OVERLAY / "styles.css").read_text(encoding="utf-8")

    direct_validator = app[
        app.index("function validateCandidateReviewMasterDirectScanDetail") :
        app.index("function appendCandidateReviewVisualList")
    ]
    assert "item.source_kind" not in direct_validator
    assert 'value.scan_status !== catalogItem.scan_status' in direct_validator
    assert 'product?.product_id === candidateReviewHuangpuDirectProductId' in direct_validator
    assert '"source_identity", "textbook_directory_mapping"' in direct_validator
    assert '"candidate_analysis", "chemistry_observations"' in direct_validator

    for marker in (
        "年份与来源",
        "教材目录定位",
        "该批次尚未接入教材目录",
        "册：",
        "→ 章：",
        "→ 节：",
        "主知识点",
        "辅助知识点",
        "单元：暂无可信单元级节点",
        "版次：尚未外部核验",
        "非官方参考答案",
        "系统未独立核验",
    ):
        assert marker in app + html

    source_renderer = app[
        app.index("function renderCandidateReviewVisualSourceAndTextbook") :
        app.index("function validateCandidateReviewSupplementalScanDetail")
    ]
    for forbidden in (
        "sha256",
        "source_path",
        "local_path",
        "file_path",
        "volume_id",
        "chapter_id",
        "section_id",
        "blocker_or_note",
        "http://",
        "https://",
    ):
        assert forbidden not in source_renderer

    render_block = app[
        app.index("function renderCandidateReviewVisualScan") :
        app.index("function renderCandidateReviewDetail")
    ]
    assert "validation.structuredDirect" in render_block
    assert "value.candidate_analysis" in render_block
    assert "?.solution_path_zh" in render_block
    assert "value.chemistry_observations || {}" in render_block
    assert "value.risks_and_limits || {}" in render_block
    assert "renderCandidateReviewHuangpuMasterComparison(validation.masterComparison)" in render_block

    assert ".visual-scan-source-grid { display: grid; grid-template-columns: repeat(2, minmax(0, 1fr));" in css
    assert ".visual-textbook-list article" in css
    assert "overflow-wrap: anywhere" in css
    assert "overflow: hidden" in css
    mobile = css[css.index("@media (max-width: 760px)") : css.index("@media (min-width: 761px)")]
    assert ".visual-scan-source-grid" in mobile
    assert "grid-template-columns: minmax(0, 1fr)" in mobile

    assert "答案单元逐题对齐（与作答单元总数分开统计）" in html
    assert "`答案单元 ${questionProcessingRatio(answers.aligned, answers.answer_unit_total)}`" in app


def test_overlay_javascript_parses():
    subprocess.run(
        ["node", "--check", str(OVERLAY / "app.js")],
        check=True,
        capture_output=True,
        text=True,
        encoding="utf-8",
    )
