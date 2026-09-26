"""Emit, never directly write, derived report/manifest refresh patches."""

import argparse
import difflib
import importlib.util
import json
import sys

from audit_legacy_render_bindings import PRODUCT, pipeline


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "kind", choices=("report", "wave", "batch", "version", "product")
    )
    parser.add_argument("--batch")
    args = parser.parse_args()
    captured = {}
    module = pipeline()
    module.write_json = lambda path, value: captured.update({path: value})
    if args.kind == "report":
        result = module.validate_batch(args.batch)
        assert result["valid"], result
    elif args.kind == "wave":
        path = module._wave_report_path(args.batch)
        assert path.is_file()
        captured[path] = module.load_json(
            module.BATCH_ROOT / args.batch / "batch_summary.json"
        )
    elif args.kind == "batch":
        module.build_output_manifest(module.BATCH_ROOT / args.batch)
    elif args.kind == "version":
        module.build_version_manifest()
    else:
        path = PRODUCT / "scripts/handout_slice_pipeline.py"
        spec = importlib.util.spec_from_file_location("_handout_product_refresh", path)
        product_module = importlib.util.module_from_spec(spec)
        sys.modules[spec.name] = product_module
        spec.loader.exec_module(product_module)
        product_module.write_json = lambda path, value: captured.update({path: value})
        product_module.build_product_manifest()
    assert len(captured) == 1
    parts = ["*** Begin Patch"]
    for path, value in captured.items():
        old = path.read_text(encoding="utf-8")
        new = json.dumps(value, ensure_ascii=False, sort_keys=True, indent=2) + "\n"
        diff = list(
            difflib.unified_diff(old.splitlines(), new.splitlines(), n=2, lineterm="")
        )
        if diff:
            parts.append("*** Update File: " + str(path))
            parts.extend("@@" if line.startswith("@@") else line for line in diff[2:])
    parts.append("*** End Patch")
    print("\n".join(parts))


if __name__ == "__main__":
    main()
