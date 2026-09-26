from __future__ import annotations

import hashlib
import json
import shutil
import tempfile
from pathlib import Path

import pytest
from jsonschema import Draft202012Validator

from integrations.deeptutor_shchem_v1.config import Principal, token_digest
from integrations.deeptutor_shchem_v1.material_intake_workbench import (
    AUTHORITY,
    LEDGER_RELATIVE,
    MaterialIntakeError,
    MaterialIntakeWorkbenchReader,
)
from staging.coordination.deeptutor_gateway.tests.test_full_bank_readiness_webui import (
    SOURCE_PATHS,
)
from staging.coordination.deeptutor_gateway.tests.test_full_bank_readiness_webui import (
    isolated_root as readiness_isolated_root,
)
from staging.coordination.deeptutor_gateway.tests.test_gateway import (
    build_config,
    request,
    running_server,
)

WORKSPACE = Path(__file__).resolve().parents[4]
SHCHEM_ROOT = WORKSPACE / "sh-chem-db"
SCHEMA = (
    SHCHEM_ROOT
    / "kb/workbench/material_intake_ledger_v1/material_intake_ledger.schema.json"
)
CATALOG = SHCHEM_ROOT / "catalog.csv"
STUDENT_TOKEN = "material-intake-student-0123456789"


def canonical_sha256(value: object) -> str:
    raw = json.dumps(
        value, ensure_ascii=False, sort_keys=True, separators=(",", ":")
    ).encode("utf-8")
    return hashlib.sha256(raw).hexdigest()


def rehash_ledger(path: Path) -> None:
    value = json.loads(path.read_text(encoding="utf-8"))
    value.pop("self_hash", None)
    value["self_hash"] = canonical_sha256(value)
    path.write_text(
        json.dumps(value, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )


def browser_headers(server) -> dict[str, str]:
    origin = f"http://127.0.0.1:{server.server_address[1]}"
    return {"Origin": origin, "Sec-Fetch-Site": "same-origin"}


def isolated_root(temp: Path) -> Path:
    root = readiness_isolated_root(temp, include_sources=True)
    ledger_target = root / LEDGER_RELATIVE
    ledger_target.parent.mkdir(parents=True, exist_ok=True)
    shutil.copy2(SHCHEM_ROOT / LEDGER_RELATIVE, ledger_target)
    shutil.copy2(CATALOG, root / "catalog.csv")
    return root


def source_snapshot() -> dict[str, tuple[int, int, str]]:
    paths = [
        SHCHEM_ROOT / LEDGER_RELATIVE,
        CATALOG,
        *(SHCHEM_ROOT / item for item in SOURCE_PATHS),
    ]
    return {
        path.relative_to(SHCHEM_ROOT).as_posix(): (
            path.stat().st_size,
            path.stat().st_mtime_ns,
            hashlib.sha256(path.read_bytes()).hexdigest(),
        )
        for path in paths
    }


def test_live_ledger_schema_counts_and_non_additive_boundaries():
    ledger = json.loads((SHCHEM_ROOT / LEDGER_RELATIVE).read_text(encoding="utf-8"))
    schema = json.loads(SCHEMA.read_text(encoding="utf-8"))
    assert list(Draft202012Validator(schema).iter_errors(ledger)) == []

    status = MaterialIntakeWorkbenchReader(SHCHEM_ROOT).status()
    counts = status["counts"]
    assert counts["catalog_source_records"] == 108
    assert counts["paper_processing_records"] == 80
    assert counts["teaching_package_records"] == 98
    assert counts["teaching_document_records"] == 196
    assert counts["ole_objects_aggregate_registered"] == 13_296
    assert counts["ole_objects_individually_indexed"] == 0
    assert counts["page_state_records"] == 0
    assert counts["formal_question_ready_records"] == 0
    assert "不能相加" in status["non_additive_boundary_zh"]
    assert "逐对象索引为 0" in status["ole_boundary_zh"]
    assert status["authority"] == AUTHORITY
    assert status["endpoints"] == {
        "read_only_get": True,
        "batch_create": False,
        "worker_execution": False,
        "mutation": False,
    }


def test_records_are_safe_filterable_and_keep_parent_child_crosswalks():
    reader = MaterialIntakeWorkbenchReader(SHCHEM_ROOT)
    papers = reader.list_records(
        kind="paper_processing_view",
        stage=None,
        status=None,
        query=None,
        limit=200,
        offset=0,
    )
    documents = reader.list_records(
        kind="teaching_document",
        stage="registered",
        status=None,
        query="原卷版",
        limit=200,
        offset=0,
    )
    assert papers["total"] == 80
    assert documents["total"] == 98
    assert all(item["parent_record_id"] for item in papers["items"])
    assert all(item["parent_record_id"] for item in documents["items"])
    assert all(item["authority_gates"] == AUTHORITY for item in papers["items"])
    serialized = json.dumps(
        {"papers": papers, "documents": documents}, ensure_ascii=False
    )
    for forbidden in (
        '"path":',
        '"source_path":',
        '"local_path":',
        '"url":',
        ".docx",
        "C:\\Users",
        "question_text",
        "answer_text",
    ):
        assert forbidden not in serialized


def test_source_version_identity_does_not_launder_same_blob_across_sources():
    from sh_chem_material_intake_builder import source_version_id

    common = {
        "role": "question",
        "raw_sha256": "a" * 64,
        "bytes_value": 123,
        "authority": "nonofficial",
        "rights_status": "local_reference_only_pending_review",
    }
    first = source_version_id(source_id="source-a", **common)
    second = source_version_id(source_id="source-b", **common)
    official = source_version_id(
        source_id="source-a", **{**common, "authority": "official"}
    )
    assert first != second
    assert first != official


def test_self_source_authority_path_and_honest_zero_mutations_fail_closed():
    with tempfile.TemporaryDirectory() as temp_name:
        root = isolated_root(Path(temp_name))
        ledger = root / LEDGER_RELATIVE
        value = json.loads(ledger.read_text(encoding="utf-8"))
        value["counts"]["ole_objects_individually_indexed"] = 1
        value.pop("self_hash")
        value["self_hash"] = canonical_sha256(value)
        ledger.write_text(json.dumps(value, ensure_ascii=False), encoding="utf-8")
        with pytest.raises(MaterialIntakeError) as captured:
            MaterialIntakeWorkbenchReader(root).status()
        assert captured.value.code == "material_intake_progress_mismatch"

    with tempfile.TemporaryDirectory() as temp_name:
        root = isolated_root(Path(temp_name))
        ledger = root / LEDGER_RELATIVE
        value = json.loads(ledger.read_text(encoding="utf-8"))
        value["catalog_sources"][0]["authority_gates"]["recommendation_allowed"] = True
        value.pop("self_hash")
        value["self_hash"] = canonical_sha256(value)
        ledger.write_text(json.dumps(value, ensure_ascii=False), encoding="utf-8")
        with pytest.raises(MaterialIntakeError) as captured:
            MaterialIntakeWorkbenchReader(root).status()
        assert captured.value.code == "material_intake_record_contract_invalid"

    with tempfile.TemporaryDirectory() as temp_name:
        root = isolated_root(Path(temp_name))
        ledger = root / LEDGER_RELATIVE
        value = json.loads(ledger.read_text(encoding="utf-8"))
        value["teaching_documents"][0]["source_path"] = "C:/Users/leak/source.docx"
        value.pop("self_hash")
        value["self_hash"] = canonical_sha256(value)
        ledger.write_text(json.dumps(value, ensure_ascii=False), encoding="utf-8")
        with pytest.raises(MaterialIntakeError) as captured:
            MaterialIntakeWorkbenchReader(root).status()
        assert captured.value.code == "material_intake_record_contract_invalid"

    with tempfile.TemporaryDirectory() as temp_name:
        root = isolated_root(Path(temp_name))
        catalog = root / "catalog.csv"
        catalog.write_bytes(catalog.read_bytes() + b"\n")
        with pytest.raises(MaterialIntakeError) as captured:
            MaterialIntakeWorkbenchReader(root).status()
        assert captured.value.code == "material_intake_source_hash_mismatch"


def test_http_is_teacher_origin_scoped_read_only_and_strictly_filtered():
    with tempfile.TemporaryDirectory() as temp_name:
        config = build_config(Path(temp_name))
        config.principals.append(
            Principal(
                "material-intake-student", "student", token_digest(STUDENT_TOKEN), ()
            )
        )
        before = source_snapshot()
        with running_server(config) as server:
            origin = browser_headers(server)
            status, body, _ = request(
                server, "GET", "/api/v1/intake/status", token=None
            )
            assert status == 401
            assert body["error"]["code"] == "authentication_required"
            status, body, _ = request(
                server,
                "GET",
                "/api/v1/intake/status",
                token=STUDENT_TOKEN,
                headers=origin,
            )
            assert status == 403
            assert body["error"]["code"] == "teacher_scope_required"
            status, body, _ = request(server, "GET", "/api/v1/intake/status")
            assert status == 403
            assert body["error"]["code"] == "origin_required"

            status, body, _ = request(
                server, "GET", "/api/v1/intake/status", headers=origin
            )
            assert status == 200
            assert body["data"]["counts"]["catalog_source_records"] == 108
            status, body, _ = request(
                server, "GET", "/api/v1/intake/batches", headers=origin
            )
            assert status == 200
            assert body["data"]["total"] == 3
            batch_id = body["data"]["items"][0]["batch_id"]
            status, body, _ = request(
                server,
                "GET",
                f"/api/v1/intake/batches/{batch_id}",
                headers=origin,
            )
            assert status == 200
            assert body["data"]["content_exposed"] is False

            records = "/api/v1/intake/records?kind=teaching_document&q=%E5%8E%9F%E5%8D%B7%E7%89%88&limit=3"
            status, body, _ = request(server, "GET", records, headers=origin)
            assert status == 200
            assert body["data"]["count"] == 3
            assert body["data"]["total"] == 98
            status, body, _ = request(
                server,
                "GET",
                "/api/v1/intake/records?kind=catalog_source&kind=teaching_document",
                headers=origin,
            )
            assert status == 400
            assert body["error"]["code"] == "material_intake_query_ambiguous"
            status, body, _ = request(
                server,
                "GET",
                "/api/v1/intake/records?source_path=x",
                headers=origin,
            )
            assert status == 400
            assert body["error"]["code"] == "material_intake_query_unsupported"
            status, body, _ = request(
                server,
                "POST",
                "/api/v1/intake/batches",
                headers=origin,
                payload={"source_path": "C:/Users/leak"},
            )
            assert status == 404
            assert body["error"]["code"] == "route_not_found"
        assert source_snapshot() == before


# The builder lives under sh-chem-db rather than an import package.  Load only
# its pure identity helper for the source-laundering regression above.
import importlib.util

_BUILDER_PATH = (
    SHCHEM_ROOT
    / "kb/workbench/material_intake_ledger_v1/build_material_intake_ledger.py"
)
_SPEC = importlib.util.spec_from_file_location(
    "sh_chem_material_intake_builder", _BUILDER_PATH
)
assert _SPEC is not None and _SPEC.loader is not None
_MODULE = importlib.util.module_from_spec(_SPEC)
_SPEC.loader.exec_module(_MODULE)
import sys

sys.modules["sh_chem_material_intake_builder"] = _MODULE
