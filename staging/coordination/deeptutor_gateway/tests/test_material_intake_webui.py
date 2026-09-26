from __future__ import annotations

import hashlib
import json
import re
from pathlib import Path

WORKSPACE = Path(__file__).resolve().parents[4]
OVERLAY = WORKSPACE / "runtime/deeptutor_shchem/overlay"
LEDGER_ROOT = WORKSPACE / "sh-chem-db/kb/workbench/material_intake_ledger_v1"


def source(name: str) -> str:
    return (OVERLAY / name).read_text(encoding="utf-8")


def test_material_intake_task_desk_is_chinese_read_only_and_hierarchical() -> None:
    html = source("index.html")
    section = html[
        html.index('class="card inset full-bank-readiness-card') : html.index(
            '<section id="panel-taxonomy"'
        )
    ]

    for marker in (
        "统一材料摄入 · 只读任务台",
        "材料处理总账",
        "九阶段处理链",
        "全局阻断原因",
        "批次下钻",
        "无需配置模型 API Key 即可查看",
        "不能相加成材料总数",
        'id="materialIntakeSourceCount"',
        'id="fullBankPaperCount"',
        'id="fullBankTeachingCount"',
        'id="materialIntakeDocumentCount"',
        'id="materialIntakeOleAggregateCount"',
        'id="materialIntakeOleIndexedCount"',
        'id="materialIntakePipeline"',
        'id="materialIntakeGlobalBlockers"',
        'id="materialIntakeBatch"',
        'id="materialIntakeBatchDetail"',
        'id="materialIntakeRecordStatus"',
    ):
        assert marker in section

    for kind in (
        "catalog_source",
        "paper_processing_view",
        "teaching_package",
        "teaching_document",
    ):
        assert f'value="{kind}"' in section

    assert section.count('type="submit"') == 1
    assert 'type="file"' not in section
    assert "上传材料" not in section
    assert "开始处理" not in section
    assert "应用修改" not in section


def test_material_intake_ui_uses_all_read_endpoints_and_keeps_legacy_fallback() -> None:
    app = source("app.js")
    ui = app[
        app.index("function materialIntakeCount") : app.index(
            "async function searchKb", app.index("function materialIntakeCount")
        )
    ]

    for endpoint in (
        'api("/api/v1/intake/status")',
        'api("/api/v1/intake/batches")',
        "/api/v1/intake/batches/${encodeURIComponent(batchId)}",
        "/api/v1/intake/records?${params}",
    ):
        assert endpoint in ui

    assert 'api("/api/v1/kb/full-bank-readiness/status")' in ui
    assert "/api/v1/kb/full-bank-readiness/records" in ui
    assert (
        'error.status === 404 && error.payload?.error?.code === "route_not_found"' in ui
    )
    assert 'method: "POST"' not in ui
    assert "innerHTML" not in ui
    assert "source_paths_exposed: value.source_paths_exposed" in ui
    assert "content_exposed: value.content_exposed" in ui
    assert "没有用旧队列掩盖完整性错误" in ui


def test_material_intake_ui_renders_dynamic_counts_pipeline_gaps_and_filters() -> None:
    app = source("app.js")
    styles = source("styles.css")

    for count_key in (
        "catalog_source_records",
        "paper_processing_records",
        "teaching_package_records",
        "teaching_document_records",
        "ole_objects_aggregate_registered",
        "ole_objects_individually_indexed",
        "formal_question_ready_records",
        "entity_record_count_non_additive",
    ):
        assert count_key in app

    for marker in (
        "value.pipeline.length !== 9",
        "pipeline_stage_counts",
        "catalog_container_kind_counts",
        "paper_independence_counts",
        'params.set("kind", kind)',
        'params.set("stage", stage)',
        'params.set("status", status)',
        'params.set("q", query)',
        "renderMaterialIntakeBlockers(value.global_blockers)",
    ):
        assert marker in app

    for selector in (
        ".material-intake-metric-grid",
        ".material-intake-pipeline",
        ".material-intake-blockers",
        ".material-intake-batch-panel",
        ".material-intake-record",
    ):
        assert selector in styles

    # Guard against hard-coded assignment of the six headline counts in JS.
    assignments = re.findall(
        r'\$\("(?:materialIntakeSourceCount|fullBankPaperCount|fullBankTeachingCount|materialIntakeDocumentCount|materialIntakeOleAggregateCount|materialIntakeOleIndexedCount)"\)\.textContent\s*=\s*([^;]+);',
        app,
    )
    assert assignments
    assert all(
        "materialIntakeCount(" in value or value.strip() == '"—"'
        for value in assignments
    )


def test_material_intake_dom_ids_are_unique() -> None:
    html = source("index.html")
    ids = re.findall(r'\bid="([^"]+)"', html)
    assert len(ids) == len(set(ids))


def test_material_intake_manifest_binds_static_ledger_schema_and_closed_authority() -> (
    None
):
    inner = json.loads(source("overlay.manifest.json"))
    outer = json.loads(
        (WORKSPACE / "runtime/deeptutor_shchem/overlay.manifest.json").read_text(
            encoding="utf-8"
        )
    )
    for filename, descriptor in inner["files"].items():
        raw = (OVERLAY / filename).read_bytes()
        assert descriptor == {
            "sha256": hashlib.sha256(raw).hexdigest(),
            "bytes": len(raw),
        }
    assert inner["material_intake"] == outer["material_intake"]
    intake = inner["material_intake"]
    ledger_raw = (LEDGER_ROOT / "material_intake_ledger.json").read_bytes()
    schema_raw = (LEDGER_ROOT / "material_intake_ledger.schema.json").read_bytes()
    ledger = json.loads(ledger_raw.decode("utf-8"))
    assert intake["ledger_file_sha256"] == hashlib.sha256(ledger_raw).hexdigest()
    assert intake["ledger_schema_sha256"] == hashlib.sha256(schema_raw).hexdigest()
    assert intake["ledger_self_hash"] == ledger["self_hash"]
    assert intake["input_set_sha256"] == ledger["input_set_sha256"]
    assert intake["snapshot_counts"] == {
        key: ledger["counts"][key] for key in intake["snapshot_counts"]
    }
    assert intake["hierarchy_counts_non_additive"] is True
    assert intake["batch_create_endpoint_present"] is False
    assert intake["worker_execution_present"] is False
    for gate in (
        "human_reviewed",
        "retrieval_ready",
        "recommendation_allowed",
        "manual_scoring_allowed",
        "ai_scoring_allowed",
        "auto_scoring_allowed",
        "generation_allowed",
        "export_allowed",
        "publication_allowed",
        "official_claim_allowed",
    ):
        assert intake[gate] is False
