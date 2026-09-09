import importlib.util
import json
from pathlib import Path
from types import SimpleNamespace


def _diagnostics(payload):
    script = Path(__file__).parents[1] / "scripts/verify_real_blueprint_preparation.py"
    spec = importlib.util.spec_from_file_location("live_preparation_qa", script)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module.response_diagnostics(payload)


def test_diagnostics_capture_incomplete_reason_without_content():
    result = _diagnostics(
        {
            "status": "incomplete",
            "incomplete_details": {
                "reason": "max_output_tokens",
                "message": "sensitive",
            },
            "output_text": "sensitive",
            "usage": {
                "input_tokens": 12,
                "output_tokens": 32000,
                "total_tokens": 32012,
                "output_tokens_details": {
                    "reasoning_tokens": 25000,
                    "text": "sensitive",
                },
                "unexpected": 999,
            },
            "output": [
                {
                    "type": "message",
                    "content": [{"type": "output_text", "text": "sensitive"}],
                }
            ],
        }
    )
    assert result["incomplete_reason"] == "max_output_tokens"
    assert result["usage"]["output_tokens_details"] == {"reasoning_tokens": 25000}
    assert result["output_text_characters"] == 9
    assert result["output_message_text_characters"] == 9
    assert "sensitive" not in json.dumps(result)
    assert "unexpected" not in result["usage"]


def test_diagnostics_reject_unknown_prose_and_invalid_usage():
    result = _diagnostics(
        {
            "status": "sensitive",
            "incomplete_details": {"reason": "sensitive"},
            "usage": {"input_tokens": True, "output_tokens": -1},
            "choices": [{"finish_reason": "length"}, {"finish_reason": "sensitive"}],
        }
    )
    assert result == {
        "incomplete_reason": "other",
        "usage": {},
        "finish_reasons": ["length", "other"],
    }
    assert _diagnostics([]) == {"diagnostics_unavailable": True}


def test_diagnostics_distinguish_reasoning_only_and_split_text_without_prose():
    result = _diagnostics(
        {
            "status": "completed",
            "output": [
                {
                    "type": "reasoning",
                    "content": [{"type": "reasoning_text", "text": "private"}],
                },
                {
                    "type": "message",
                    "content": [
                        {"type": "output_text", "text": "abc"},
                        {"type": "output_text", "text": "def"},
                    ],
                },
                {
                    "type": "private",
                    "content": [{"type": "private", "text": "private"}],
                },
            ],
        }
    )
    assert result["output_item_types"] == {"reasoning": 1, "message": 1, "other": 1}
    assert result["output_content_types"] == {
        "reasoning_text": 1,
        "output_text": 2,
        "other": 1,
    }
    assert result["output_message_text_characters"] == 6
    assert "private" not in json.dumps(result)


def test_bundled_renderer_forwards_image_bytes_only_to_local_child(
    monkeypatch, tmp_path
):
    import base64

    script = Path(__file__).parents[1] / "scripts/verify_real_blueprint_preparation.py"
    spec = importlib.util.spec_from_file_location("local_artifact_qa", script)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    renderer = object.__new__(module.BundledArtifactRenderer)
    renderer.executable = tmp_path / "bundled-python.exe"
    captured = []

    def run(command, **kwargs):
        captured.append((command, json.loads(kwargs["input"])))
        return SimpleNamespace(returncode=0, stdout='{"files": []}')

    monkeypatch.setattr(module.subprocess, "run", run)
    result = renderer.render(
        {"title": "candidate"},
        output_kind="joint",
        output_dir=tmp_path,
        report_progress=lambda *_: None,
        is_cancelled=lambda: False,
        image_data={"image-one": b"local-pixels"},
    )
    assert result == {"files": []}
    command, payload = captured[0]
    assert command[0] == str(renderer.executable)
    assert payload["candidate"] == {"title": "candidate"}
    assert base64.b64decode(payload["images"]["image-one"]) == b"local-pixels"
    assert "image_data=images" in command[5]
