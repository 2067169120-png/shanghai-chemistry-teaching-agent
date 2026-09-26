"""Explicit one-call smoke using the saved profile, never a command-line key.

Without --execute this only compiles a local preview. Failed calls are not
automatically retried. Reuse --preview-id to inspect or explicitly retry a
known saved preview, including cached successful results (zero new calls).
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path

from integrations.deeptutor_shchem_v1.desktop_facade import (
    DesktopFacadeError,
    DesktopWorkbenchFacade,
)
from integrations.deeptutor_shchem_v1.desktop_paths import DesktopPaths
from integrations.deeptutor_shchem_v1.desktop_workbench.app import (
    _acquire_desktop_instance_lock,
)


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--execute", action="store_true")
    parser.add_argument("--preview-id")
    args = parser.parse_args()
    paths = DesktopPaths.from_workspace(Path.cwd())
    instance_lock = _acquire_desktop_instance_lock(paths.state_root)
    facade = DesktopWorkbenchFacade(paths)
    try:
        profile = next(
            (
                p
                for p in facade.preparation_profiles()
                if p.profile_id == "desktop-default"
            ),
            None,
        )
        if profile is None:
            print(json.dumps({"status": "no_configured_text_profile"}))
            return 2
        if args.preview_id:
            record = facade.state_store.snapshot()["drafts"].get(args.preview_id)
            if (
                not isinstance(record, dict)
                or record.get("kind") != "textbook_prompt_blueprint"
            ):
                print(json.dumps({"status": "preview_not_found"}))
                return 2
            preview = record["preview"]
        else:
            preview = facade.compile_prompt_blueprint(
                {
                    "title": "物质的量：配制溶液的主题设计",
                    "grade": 10,
                    "section_keys": ["TB-M1-C1:1.2"],
                    "learning_goal": "规划一个围绕溶液配制的共同情境，依次考查物质的量关系、信息提取和操作理由；具体题面、数值及参考解答留待下一步核验完善。",
                }
            )
        print(
            json.dumps(
                {
                    "stage": "preview_ready",
                    "preview_id": preview["preview_id"],
                    "sections": preview["section_labels"],
                    "evidence_count": len(preview["evidence"]),
                    "model_id": profile.model_id,
                    "api_style": profile.api_style,
                    "execute": args.execute,
                },
                ensure_ascii=False,
            ),
            flush=True,
        )
        if not args.execute:
            return 0
        result = facade.generate_prompt_blueprint(
            preview["preview_id"],
            profile.profile_id,
            profile.revision,
            teacher_confirmed=True,
        )
        print(
            json.dumps(
                {
                    "stage": "completed",
                    "preview_id": result["preview_id"],
                    "model_id": result["model_id"],
                    "result_kind": result["result_kind"],
                    "latency_ms": result["latency_ms"],
                    "usage": result["usage"],
                    "material_count": len(result["candidate"]["shared_material_plan"]),
                    "atomic_count": len(result["candidate"]["question_chain"]),
                    "unknowns": result["candidate"]["unknowns"],
                },
                ensure_ascii=False,
            ),
            flush=True,
        )
        return 0
    except DesktopFacadeError as exc:
        saved = (
            facade.state_store.snapshot()["drafts"].get(preview["preview_id"], {})
            if "preview" in locals()
            else {}
        )
        print(
            json.dumps(
                {
                    "stage": "failed",
                    "code": exc.code,
                    "message": exc.message_zh,
                    "response_summary": saved.get("response_summary", {}),
                },
                ensure_ascii=False,
            )
        )
        return 1
    finally:
        facade.shutdown()
        instance_lock.unlock()


if __name__ == "__main__":
    raise SystemExit(main())
