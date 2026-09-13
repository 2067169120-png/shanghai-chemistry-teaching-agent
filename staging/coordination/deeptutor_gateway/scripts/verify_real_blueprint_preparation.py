"""One authorized live preparation invocation with isolated task state.

Reads the normally configured provider store; never serializes credentials or
raw HTTP requests/responses. Re-running creates a new paid invocation, so inspect
the existing receipt/task before deciding to run it again.
"""

from __future__ import annotations

import argparse
import base64
import hashlib
import json
import shutil
import subprocess
import sys
import tempfile
import time
from dataclasses import asdict
from pathlib import Path
from urllib.parse import urlsplit

ROOT = Path(__file__).resolve().parents[4]
sys.path.insert(0, str(ROOT))

from integrations.deeptutor_shchem_v1.desktop_facade import DesktopWorkbenchFacade
from integrations.deeptutor_shchem_v1.desktop_paths import (
    DesktopPaths,
    default_state_root,
)
from integrations.deeptutor_shchem_v1.desktop_preparation import (
    DesktopPreparationError,
    _reject_sensitive,
)
from integrations.deeptutor_shchem_v1.desktop_preparation_provider import (
    PREPARATION_PROMPT_REVISION,
    PREPARATION_REQUEST_POLICY_REVISION,
)
from integrations.deeptutor_shchem_v1.desktop_state import DesktopStateStore
from integrations.deeptutor_shchem_v1.intake_imports import PinnedVisualTransport
from integrations.deeptutor_shchem_v1.model_provider_settings import (
    ModelProviderSettingsStore,
)
from integrations.deeptutor_shchem_v1.visual_provider_runtime import (
    VisualProviderRuntimeError,
    parse_structured_visual_response,
)

SOURCE = Path("C:/Users/20671/AppData/Local/Temp/shchem-blueprint-prep-qa-77t2chl0")
DRAFT = "prep-6fde5769527149d68cd27e1312ba634d"


def tree(root):
    return {
        p.relative_to(root).as_posix(): hashlib.sha256(p.read_bytes()).hexdigest()
        for p in sorted(root.rglob("*"))
        if p.is_file()
    }


def response_diagnostics(parsed):
    """Allowlisted metadata only, never provider prose or reasoning text."""
    if not isinstance(parsed, dict):
        return {"diagnostics_unavailable": True}
    result = {}
    status = parsed.get("status")
    if status in {"completed", "incomplete", "failed", "cancelled", "in_progress"}:
        result["response_status"] = status
    details = parsed.get("incomplete_details")
    if isinstance(details, dict):
        reason = details.get("reason")
        result["incomplete_reason"] = (
            reason if reason in {"max_output_tokens", "content_filter"} else "other"
        )
    usage = parsed.get("usage")
    if isinstance(usage, dict):
        result["usage"] = {}
        for name in (
            "input_tokens",
            "output_tokens",
            "total_tokens",
            "prompt_tokens",
            "completion_tokens",
        ):
            value = usage.get(name)
            if type(value) is int and value >= 0:
                result["usage"][name] = value
        for name in ("output_tokens_details", "completion_tokens_details"):
            value = usage.get(name)
            if isinstance(value, dict) and type(value.get("reasoning_tokens")) is int:
                result["usage"][name] = {"reasoning_tokens": value["reasoning_tokens"]}
    text = parsed.get("output_text")
    if isinstance(text, str):
        result["output_text_characters"] = len(text)
    outputs = parsed.get("output")
    if isinstance(outputs, list):
        # Count only allowlisted shape labels. Never persist content, IDs,
        # unknown provider labels, tool arguments or reasoning prose.
        result["output_item_types"] = {}
        result["output_content_types"] = {}
        for item in outputs:
            if not isinstance(item, dict):
                continue
            kind = item.get("type")
            kind = (
                kind if kind in ("message", "reasoning", "function_call") else "other"
            )
            result["output_item_types"][kind] = (
                result["output_item_types"].get(kind, 0) + 1
            )
            parts = item.get("content")
            if not isinstance(parts, list):
                continue
            for part in parts:
                if not isinstance(part, dict):
                    continue
                kind = part.get("type")
                kind = (
                    kind
                    if kind in ("output_text", "reasoning_text", "refusal")
                    else "other"
                )
                result["output_content_types"][kind] = (
                    result["output_content_types"].get(kind, 0) + 1
                )
        result["output_message_text_characters"] = sum(
            len(part["text"])
            for item in outputs
            if isinstance(item, dict) and item.get("type") == "message"
            for part in (item.get("content") or [])
            if isinstance(part, dict)
            and part.get("type") == "output_text"
            and isinstance(part.get("text"), str)
        )
    choices = parsed.get("choices")
    if isinstance(choices, list):
        result["finish_reasons"] = [
            choice["finish_reason"]
            if choice.get("finish_reason")
            in {"stop", "length", "content_filter", "tool_calls"}
            else "other"
            for choice in choices
            if isinstance(choice, dict)
        ]
    return result


class ObservedTransport:
    def __init__(self):
        # The provider passes its own deadline. Do not shorten that budget in
        # the observation wrapper; production requests retain their own limit.
        self.delegate = PinnedVisualTransport(total_timeout_seconds=600)
        self.receipts = []
        self.structured_candidate = None

    def send(self, request, **kwargs):
        start = time.monotonic()
        receipt = {
            "attempt": len(self.receipts) + 1,
            "prompt_revision": PREPARATION_PROMPT_REVISION,
            "request_policy_revision": PREPARATION_REQUEST_POLICY_REVISION,
            "request_body_sha256": hashlib.sha256(request.body).hexdigest(),
        }
        request_body = json.loads(request.body)
        receipt["api_style"] = request.api_style
        receipt["requested_output_tokens"] = request_body.get(
            "max_output_tokens", request_body.get("max_tokens")
        )
        receipt["requested_timeout_seconds"] = round(
            kwargs["deadline_monotonic"] - start
        )
        self.receipts.append(receipt)
        try:
            response = self.delegate.send(request, **kwargs)
            receipt.update(
                http_status=response.http_status, model_invoked=response.model_invoked
            )
            if 200 <= response.http_status < 300:
                try:
                    parsed = json.loads(response.body)
                    receipt.update(response_diagnostics(parsed))
                except (ValueError, TypeError):
                    receipt["usage_unavailable"] = True
                try:
                    candidate, _ = parse_structured_visual_response(
                        request.api_style, response.body
                    )
                    _reject_sensitive(candidate)
                    self.structured_candidate = candidate
                except (
                    VisualProviderRuntimeError,
                    DesktopPreparationError,
                    ValueError,
                ):
                    pass
            return response
        except Exception as exc:
            receipt["error_type"] = type(exc).__name__
            code = getattr(exc, "code", "")
            if isinstance(code, str) and code.replace("_", "").isalnum():
                receipt["error_code"] = code
            for key in ("model_invoked", "http_status"):
                value = getattr(exc, key, None)
                if isinstance(value, (bool, int)):
                    receipt[key] = value
            raise
        finally:
            receipt["elapsed_seconds"] = round(time.monotonic() - start, 2)


class BundledArtifactRenderer:
    """Keep model orchestration separate from the bundled artifact runtime."""

    def __init__(self, executable: Path):
        self.executable = executable.resolve(strict=True)
        probe = subprocess.run(
            [str(self.executable), "-B", "-c", "import docx,pptx,PIL"],
            check=False,
            capture_output=True,
            timeout=20,
        )
        if probe.returncode:
            raise RuntimeError("Artifact runtime dependencies unavailable")

    def render(
        self,
        candidate,
        *,
        output_kind,
        output_dir,
        report_progress,
        is_cancelled,
        image_data=None,
    ):
        if is_cancelled():
            raise RuntimeError("QA rendering cancelled")
        code = (
            "import base64,json,sys; from pathlib import Path; "
            "sys.path.insert(0,sys.argv[1]); "
            "from integrations.deeptutor_shchem_v1.desktop_preparation_renderer import NativePreparationRenderer; "
            "payload=json.load(sys.stdin); "
            "images={k:base64.b64decode(v) for k,v in payload['images'].items()}; "
            "result=NativePreparationRenderer().render(payload['candidate'],output_kind=sys.argv[2],output_dir=Path(sys.argv[3]),image_data=images); "
            "print(json.dumps(result,ensure_ascii=False))"
        )
        result = subprocess.run(
            [
                str(self.executable),
                "-X",
                "utf8",
                "-B",
                "-c",
                code,
                str(ROOT),
                output_kind,
                str(output_dir),
            ],
            input=json.dumps(
                {
                    "candidate": candidate,
                    "images": {
                        k: base64.b64encode(v).decode("ascii")
                        for k, v in (image_data or {}).items()
                    },
                },
                ensure_ascii=False,
            ),
            check=False,
            capture_output=True,
            text=True,
            encoding="utf-8",
            timeout=120,
        )
        if result.returncode:
            # No candidate/provider prose or raw traceback in live diagnostics.
            raise RuntimeError("Bundled artifact rendering failed")
        return json.loads(result.stdout)


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--artifact-python",
        type=Path,
        required=True,
        help="Bundled Python path returned by the workspace dependency loader",
    )
    parser.add_argument(
        "--resume",
        type=Path,
        help="Existing failed QA output directory; one manual retry",
    )
    args = parser.parse_args()
    renderer = BundledArtifactRenderer(args.artifact_python)
    before_source, before_user = tree(SOURCE), tree(default_state_root())
    original = DesktopStateStore(SOURCE).snapshot()["drafts"][DRAFT]
    payload = {
        "output_kind": "joint",
        **original["core_fields"],
        "advanced": dict(original["advanced"]),
    }
    payload.update(
        topic="电解质与非电解质概念辨析复习",
        audience="高二化学复习；实际班级学情尚未提供",
        lesson_route="复习",
        lesson_timing="1课时×40分钟",
        objective="形成定义条件、导电现象和微观解释的辨析框架；能说明哪些结论需要补充实验条件。",
    )
    payload["advanced"].update(
        learning_and_experiment="本次仅生成课堂教学设计与概念复习候选，不新编完整试题或试卷。实验部分只作教师演示规划，不安排学生制备或接触有害气体。参考蓝图中的实验控制、终点与推理唯一性仍待核验，应作为教师备课待办保留。",
        template_and_delivery="用现有工作台模板生成可编辑课件与教案；每页短句，详细解释放教师备注。练习位置只描述拟使用的任务和教师选题动作，不补写原题、数值或答案。",
        homework_and_strategy="作业只给选题方向与检查标准，教师核验来源后选用；不声称有真实学生统计。",
    )
    previous = None
    if args.resume:
        output = args.resume.resolve()
        assert output.is_relative_to(ROOT / "runtime/deeptutor_shchem/qa")
        previous = json.loads(
            (output / "verification.json").read_text(encoding="utf-8")
        )
        assert previous["result"]["status"] == "failed"
        payload = json.loads(
            (output / "teacher-brief.json").read_text(encoding="utf-8")
        )
        state_root = previous["isolated_state"]
        assert Path(state_root).name.startswith("shchem-live-preparation-")
    else:
        output = (
            ROOT
            / "runtime/deeptutor_shchem/qa"
            / time.strftime("real-blueprint-preparation-%Y%m%d-%H%M%S")
        )
        output.mkdir(parents=True, exist_ok=False)
        state_root = tempfile.mkdtemp(prefix="shchem-live-preparation-")
    paths = DesktopPaths.from_workspace(ROOT, state_root=state_root)
    transport = ObservedTransport()
    store = ModelProviderSettingsStore(
        default_state_root() / "model-settings", project_root=ROOT
    )
    facade = DesktopWorkbenchFacade(
        paths,
        provider_store=store,
        preparation_transport=transport,
        preparation_renderer=renderer,
    )
    profiles = [
        p
        for p in facade.preparation_profiles()
        if urlsplit(p.base_url).hostname == "api.deepseek.com"
    ]
    if len(profiles) != 1:
        raise RuntimeError(
            "Expected exactly one configured DeepSeek preparation profile"
        )
    profile = profiles[0]
    receipt = {
        "output": str(output),
        "isolated_state": str(paths.state_root),
        "source_draft": DRAFT,
        "materials_characters": len(payload["materials"]),
        "model_id": profile.model_id,
        "artifact_python": str(renderer.executable),
        "candidate_only": True,
        "publication_allowed": False,
        "transport": transport.receipts,
    }

    def save():
        (output / "verification.json").write_text(
            json.dumps(receipt, ensure_ascii=False, indent=2), encoding="utf-8"
        )

    if previous:
        receipt["previous_attempt"] = previous
        task = facade.retry_preparation(previous["task_id"])
    else:
        (output / "teacher-brief.json").write_text(
            json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8"
        )
        task = facade.prepare_preparation(payload, profile.profile_id, profile.revision)
    receipt["task_id"] = task.task_id
    save()
    print(
        json.dumps(
            {"output": str(output), "task_id": task.task_id, "model": profile.model_id},
            ensure_ascii=False,
        ),
        flush=True,
    )

    def progress(value):
        print(json.dumps({"progress": value}, ensure_ascii=False), flush=True)

    try:
        result = facade.generate_preparation(
            task.task_id, teacher_confirmed=True, progress_callback=progress
        )
        receipt["result"] = asdict(result)
        if result.status == "completed":
            artifacts = {}
            for artifact_id in result.artifact_ids:
                source = facade.preparation_artifact_path(task.task_id, artifact_id)
                target = output / source.name
                shutil.copy2(source, target)
                artifacts[artifact_id] = {
                    "path": str(target),
                    "sha256": hashlib.sha256(target.read_bytes()).hexdigest(),
                }
            receipt["artifacts"] = artifacts
    except Exception as exc:  # noqa: BLE001 - persist only sanitized live-test diagnostics
        receipt["error_type"] = type(exc).__name__
        code = getattr(exc, "code", "")
        receipt["error_code"] = (
            code
            if isinstance(code, str) and code.replace("_", "").isalnum()
            else "unknown"
        )
    finally:
        if transport.structured_candidate is not None:
            candidate_path = output / f"returned-candidate-{time.time_ns()}.json"
            candidate_path.write_text(
                json.dumps(
                    transport.structured_candidate, ensure_ascii=False, indent=2
                ),
                encoding="utf-8",
            )
            receipt["returned_candidate"] = {
                "path": str(candidate_path),
                "sha256": hashlib.sha256(candidate_path.read_bytes()).hexdigest(),
                "manager_acceptance": receipt.get("result", {}).get("status")
                == "completed",
                "teacher_review_required": True,
            }
        receipt["source_state_unchanged"] = before_source == tree(SOURCE)
        receipt["user_state_unchanged"] = before_user == tree(default_state_root())
        save()
    print(json.dumps(receipt, ensure_ascii=False), flush=True)
    return 0 if receipt.get("result", {}).get("status") == "completed" else 1


if __name__ == "__main__":
    raise SystemExit(main())
