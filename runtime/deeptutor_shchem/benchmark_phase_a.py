"""Repeatable synthetic benchmark against the audited source; not a device SLA."""
from __future__ import annotations
import argparse
import hashlib
import json
import math
import os
import platform
import random
import statistics
import subprocess
import sys
import time
import types
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path[:0] = [str(ROOT), str(ROOT / "staging/coordination/deeptutor_gateway/tests")]
BASE = "b6102d5f93f67cf4e7008b1a524eeae383c6d5d5"
PACKAGE = "integrations.deeptutor_shchem_v1"


def source_at(path, local_baseline=None):
    revision = local_baseline or BASE
    return subprocess.check_output(["git", "show", revision + ":" + path], cwd=ROOT).decode("utf-8")


def measure(function, catalog, rounds):
    durations = []
    output = None
    for _ in range(rounds):
        start = time.perf_counter()
        output = function(catalog, "word_native", {}, "")
        durations.append((time.perf_counter() - start) * 1000)
    return output, {"samples_ms": durations, "p50_ms": statistics.median(durations),
                    "p95_ms": sorted(durations)[math.ceil(.95 * len(durations)) - 1]}


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--baseline-ref")
    parser.add_argument("--rounds", type=int, default=5)
    parser.add_argument("--output", default="phase-a-qa/performance.json")
    args = parser.parse_args()
    from test_word_question_filters import _catalog, _row
    from integrations.deeptutor_shchem_v1.desktop_question_explorer import personal_results
    from integrations.deeptutor_shchem_v1.desktop_word_question_filters import compile_question_matcher
    legacy = types.ModuleType("legacy_filters")
    legacy_source = source_at("integrations/deeptutor_shchem_v1/desktop_word_question_filters.py", args.baseline_ref)
    exec(compile(legacy_source, "audited_filters.py", "exec"), legacy.__dict__)
    adapter = types.ModuleType("legacy_adapter")
    adapter.__package__ = PACKAGE
    exec(compile(source_at("integrations/deeptutor_shchem_v1/desktop_question_explorer.py", args.baseline_ref), "audited_explorer.py", "exec"), adapter.__dict__)
    adapter.matches_question = legacy.matches_question
    catalog = _catalog()
    for i in range(380):
        catalog["nodes"].append(dict(node_key=f"EXTRA{i}", volume_id="VX", volume_title="合成扩展册",
            chapter_id=f"C{i//10}", chapter_title="扩展章", section_title=f"扩展节{i}"))
    report = {"source_commit": os.environ.get("SHCHEM_SOURCE_SHA", "local"), "baseline_commit": BASE,
              "baseline_filter_sha256": hashlib.sha256(legacy_source.encode()).hexdigest(),
              "platform": platform.platform(), "python": platform.python_version(), "cpu_count": os.cpu_count(),
              "rounds": args.rounds, "catalog_nodes": len(catalog["nodes"]), "measurements": [],
              "scope": "In-memory synthetic personal_results request including facet options, not image IO, Qt painting, network or end-to-end teacher latency. p95 is nearest-rank over the recorded small sample."}
    for n in (1000, 5000, 10000):
        rows = [_row(key=f"Q{i}") for i in range(n)]
        current = {"items": rows, "attribute_catalog": catalog, "warnings": []}
        before, old_timing = measure(adapter.personal_results, current, args.rounds)
        after, new_timing = measure(personal_results, current, args.rounds)
        assert before == after
        report["measurements"].append({"rows": n, "old": old_timing, "new": new_timing, "outputs_equal": True})
    rng = random.Random(86)
    rows = [_row(key=f"M{i}", mappings=("S1",) if i % 2 else ("S2",),
                 exam="first_mock" if i % 3 else "second_mock") for i in range(100)]
    for _ in range(100):
        selection = {"book": rng.choice([[], ["V1"], ["V2"], ["unknown"]]),
                     "exam": rng.choice([[], ["first_mock"], ["second_mock"], ["unknown"]]),
                     "knowledge": rng.choice([[], ["K09"], ["K11"], ["K09", "K11"]]),
                     "knowledge_mode": rng.choice(["any", "all"]),
                     "query": rng.choice(["", "alpha", "CONTEXT", "OMEGA"])}
        matcher = compile_question_matcher(selection, catalog)
        assert [r["key"] for r in rows if matcher(r)] == [r["key"] for r in rows if legacy.matches_question(r, selection, catalog)]
    report["legacy_equivalence_queries"] = 100
    target = Path(args.output)
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps(report, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
