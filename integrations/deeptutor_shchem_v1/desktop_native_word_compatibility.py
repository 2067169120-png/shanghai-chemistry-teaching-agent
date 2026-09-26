"""Read-only, source-bound availability of native Word object selection.

This is an early structural check, never a substitute for the later Word range
proof. It neither starts Office nor caches a permissive result for selection.
"""

from __future__ import annotations

import hashlib

from .desktop_preparation_sources import PreparationSourceError

SCHEMA = "shchem.native-word-compatibility.v1"
_READY = "原文件结构预检通过。打开后仍须核对 Word 内容与实际选区。"
_FALLBACK = "可继续查看原图，或复制位置与附近文字到原 Word 核对。"
_GENERIC = "这份文件的结构暂不能可靠地自动定位。"
_INSPECTION_SCHEMA = "shchem.native-word-mapping.v1"
_OBJECT_KINDS = {"image": "inline", "omml": "math"}


def _validated_objects(inspection, source_sha256):
    """Reject stale or malformed internal projections before using any target."""
    if (not isinstance(inspection, dict)
            or inspection.get("schema") != _INSPECTION_SCHEMA
            or inspection.get("source_sha256") != source_sha256
            or not isinstance(inspection.get("objects"), list)):
        raise ValueError("Invalid inspection")
    projected = {}
    for obj in inspection["objects"]:
        if not isinstance(obj, dict):
            raise ValueError("Invalid object")
        locator = obj.get("xml_locator")
        if (not isinstance(locator, str) or not locator or locator in projected
                or obj.get("kind") not in _OBJECT_KINDS.values()
                or "table_start" not in obj
                or (obj["table_start"] is not None
                    and (type(obj["table_start"]) is not int or obj["table_start"] < 0))):
            raise ValueError("Invalid object identity")
        projected[locator] = obj
    return projected


def issue_message(code, detail=""):
    """Only trusted application wording enters the UI; never echo raw XML."""
    if code == "unsupported_active_or_revision_content":
        if detail in {"pict", "object", "txbxContent"}:
            message = "文件含旧式公式、图形或文本框，暂不能自动选中。"
        elif detail in {"fldChar", "fldSimple", "instrText", "delInstrText"}:
            message = "文件包含域，尚不能可靠地核对完整对象范围。"
        else:
            message = "文件含修订或其他复杂结构，暂不能自动选中。"
    else:
        message = {
            "active_package_part": "文件含嵌入对象或附加部件，暂不支持自动选中。",
            "unsupported_legacy_drawing": "文件含旧式图形，暂不支持自动选中。",
            "unsupported_object_or_compatibility_branch": "文件含浮动对象或兼容显示结构，暂不支持自动选中。",
            "unsupported_binary_part": "文件含尚未支持的图片或二进制部件，暂不能自动选中。",
            "unsupported_image_part": "此图片格式尚不支持在 Word 中自动选中。",
            "external_or_active_relationship": "文件含外部引用或尚未支持的关联部件，暂不能自动选中。",
            "xml_non_element_node": "文件含尚未支持的附加 XML 节点，暂不能自动选中。",
            "xml_processing_instruction": "文件含尚未支持的 XML 处理指令，暂不能自动选中。",
            "hidden_content": "文件含隐藏内容，尚不能可靠地核对可见对象范围。",
            "table_math_has_no_unique_range_identity": "表格内公式暂不能可靠地自动选中，请按单元格位置核对。",
            "locator_is_not_supported_object": "此对象尚不在 Word 自动选中的支持范围内。",
            "object_display_state": "此对象的隐藏、修订或兼容显示状态仍需核对。",
            "unsupported_object_kind": "此类对象暂不能在 Word 中直接选中。",
            "unverified_source_binding": "文件版本与预检结果尚未对应，请重新打开原文。",
            "preflight_unavailable": "自动定位预检暂时无法完成，请重新打开原文。",
        }.get(code, _GENERIC)
    if isinstance(code, str) and code.endswith("budget"):
        message = "文件的大小或结构超过当前自动定位的处理范围。"
    return message + _FALLBACK


def _state(payload, location, status, code, detail=""):
    return {
        "schema": SCHEMA,
        "source_sha256": payload.get("source_sha256"),
        "source_revision": payload.get("source_revision"),
        "xml_locator": location.get("xml_locator"),
        "status": status,
        "code": code,
        "message_zh": _READY if status == "available" else issue_message(code, detail),
    }


def attach_native_selection_support(payload, data):
    """Enrich one fresh location response; inputs are already source-verified.

    Even an unexpected preflight failure must leave the original context/image
    viewer usable. The selector independently revalidates its plan before COM.
    """
    from .desktop_native_word_mapping import inspect_source

    locations = [loc for block in payload["blocks"] for loc in block["locations"]]
    code, detail, projected = "", "", {}
    source_verified = (
        isinstance(data, bytes)
        and hashlib.sha256(data).hexdigest() == payload.get("source_sha256")
        and isinstance(payload.get("source_revision"), str)
        and bool(payload["source_revision"])
    )
    if not source_verified:
        code = "unverified_source_binding"
    elif locations:
        try:
            projected = _validated_objects(inspect_source(data), payload["source_sha256"])
        except PreparationSourceError as exc:
            code = getattr(exc, "code", "preflight_unavailable")
            detail = getattr(exc, "detail", "")
        except Exception:
            code = "preflight_unavailable"
    eligible = 0
    for location in locations:
        local_code, local_detail = code, detail
        if not local_code:
            if location.get("kind") not in {"image", "omml"}:
                local_code = "unsupported_object_kind"
            elif not isinstance(location.get("source_states"), list) or location["source_states"]:
                local_code = "object_display_state"
            else:
                obj = projected.get(location.get("xml_locator"))
                if obj is None:
                    local_code = "locator_is_not_supported_object"
                elif obj["kind"] != _OBJECT_KINDS[location["kind"]]:
                    local_code = "preflight_unavailable"
                elif obj["kind"] == "math" and obj["table_start"] is not None:
                    local_code = "table_math_has_no_unique_range_identity"
        status = "blocked" if local_code else "available"
        location["native_selection"] = _state(
            payload, location, status, local_code or "source_preflight_passed", local_detail,
        )
        eligible += status == "available"
    payload["native_selection_summary"] = {
        "schema": SCHEMA,
        "source_sha256": payload.get("source_sha256"),
        "source_revision": payload.get("source_revision"),
        "locations_checked": len(locations),
        "available_locations": eligible,
        "blocked_locations": len(locations) - eligible,
        "first_source_issue": code or None,
        "word_opened": False,
        "native_range_verified": False,
    }
    return payload


def location_availability(payload, location):
    """Fail closed if a UI receives missing, old or mis-bound availability."""
    state = location.get("native_selection")
    if not isinstance(state, dict) or any(
        state.get(key) != expected for key, expected in (
            ("schema", SCHEMA), ("source_sha256", payload.get("source_sha256")),
            ("source_revision", payload.get("source_revision")),
            ("xml_locator", location.get("xml_locator")),
        )
    ) or not all(isinstance(state.get(key), str) and state[key] for key in (
        "source_sha256", "source_revision", "xml_locator", "code",
    )):
        return False, issue_message("unverified_source_binding")
    if state.get("status") == "available" and state["code"] == "source_preflight_passed":
        return True, _READY
    if state.get("status") != "blocked":
        return False, issue_message("preflight_unavailable")
    # The bound service response contains only application-defined wording.
    message = state.get("message_zh")
    return False, message if isinstance(message, str) and message else issue_message(state["code"])
