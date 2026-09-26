"""Local, optional question-structure distillation for the desktop prompt.

Reuse the verified product adapter, but send only an explicit structural
projection. Original identities and provenance stay in the saved local preview.
No question text, answer, images, K/A/C or difficulty labels are transferred.
"""

from __future__ import annotations

import hashlib
import importlib.util
from collections.abc import Callable, Mapping
from copy import deepcopy
from pathlib import Path
from typing import Any

REFERENCE_KEY = "fengxian_2025_disinfectant"
REFERENCE_LABEL = "2025 奉贤二模 · 消毒剂的制备（结构候选）"
ADAPTER_PATH = (
    "sh-chem-db/05_命题热点素材/hotspot_theme_pipeline_v1_2026-08-28/"
    "prompt_distillation_workbench_v1/scripts/read_theme_structure_adapter.py"
)
PRODUCT_PATH = (
    "sh-chem-db/kb/classification/"
    "question_visual_scan_fengxian_2025_second_mock_theme2_disinfectant_v2_2026-09-08"
)
RESPONSE_LABELS = {
    "chemical_equation_or_notation": "化学方程式或符号书写",
    "short_fill": "简短填空",
    "experiment_operation_apparatus_plan": "实验操作与仪器方案",
    "reasoned_explanation": "原因解释",
    "comparison_or_open_response": "比较与开放简答",
    "graph_read_draw_complete": "图表读取或补绘",
    "quantitative_calculation": "定量计算",
    "embedded_indeterminate_choice": "主题内选择（选择规则待核验）",
    "unknown": "unknown",
}


class StructureReferenceError(ValueError):
    pass


def load_reference(workspace: Path) -> dict[str, Any]:
    script = workspace / ADAPTER_PATH
    spec = importlib.util.spec_from_file_location("_desktop_theme_reference", script)
    if spec is None or spec.loader is None:
        raise StructureReferenceError("结构参考适配器不可用")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    digest = module.load_fengxian_disinfectant_structure(workspace / PRODUCT_PATH)
    digest["adapter_provenance"] = {
        "relative_path": ADAPTER_PATH,
        "sha256": hashlib.sha256(script.read_bytes()).hexdigest(),
    }
    return digest


def compile_structure_reference(
    workspace: Path,
    *,
    loader: Callable[[Path], Mapping[str, Any]] | None = None,
) -> dict[str, Any]:
    digest = (loader or load_reference)(workspace)
    if (
        digest.get("candidate_only") is not True
        or digest.get("purpose") != "original_question_prompt_structure_reference"
        or not digest.get("authority_gates")
        or any(value is not False for value in digest["authority_gates"].values())
        or not digest.get("boundaries")
        or any(value is not False for value in digest["boundaries"].values())
    ):
        raise StructureReferenceError("来源不符合候选结构参考范围")
    theme = digest["hierarchy"]["theme"]
    printed = theme["printed_questions"]
    if not isinstance(printed, list) or not 1 <= len(printed) <= 40:
        raise StructureReferenceError("来源缺少完整主题内小题")
    atomics = [a for question in printed for a in question["atomic_parts"]]
    atomic_ids = [a["atomic_part_id"] for a in atomics]
    printed_ids = [p["printed_question_id"] for p in printed]
    materials = digest["shared_materials"]
    material_ids = [m["shared_material_id"] for m in materials]
    if any(
        not values
        or len(set(values)) != len(values)
        or any(not isinstance(v, str) or not v or v == "unknown" for v in values)
        for values in (atomic_ids, printed_ids, material_ids)
    ):
        raise StructureReferenceError("来源身份缺失或重复")
    # These are local reference aliases, never inferred source paper numbers.
    amap = {key: f"A{i}" for i, key in enumerate(atomic_ids, 1)}
    pmap = {key: f"P{i}" for i, key in enumerate(printed_ids, 1)}
    mmap = {key: f"S{i}" for i, key in enumerate(material_ids, 1)}
    lines = [
        "来源：2025 年奉贤区二模，主题《消毒剂的制备》。非官方转载整理的结构候选，未经人工化学核验。",
        "仅参考一个主题下的共同材料复用、混合作答方式和前序结论依赖，不代表整卷固定结构。",
        f"本样本含 {len(printed)} 个印刷小题、{len(atomics)} 个最小作答单元、{len(materials)} 组共同材料。",
        f"卷内主题序号：{theme.get('theme_order', 'unknown')}；不得从主题标题或内部编号补猜。",
        "以下 P/A/S 为本次结构摘要的局部别名，依次表示印刷小题、最小作答单元、共同材料，不是原卷题号。",
    ]
    used: dict[str, list[str]] = {key: [] for key in material_ids}
    seen: set[str] = set()
    edge_count = 0
    for question in printed:
        qid = question["printed_question_id"]
        for atomic in question["atomic_parts"]:
            aid = atomic["atomic_part_id"]
            if atomic["parent_printed_question_id"] != qid:
                raise StructureReferenceError("原子单元与印刷小题父节点不一致")
            prior = atomic.get("prior_atomic_part_ids", "unknown")
            if prior != "unknown" and (
                not isinstance(prior, list) or any(p not in seen for p in prior)
            ):
                raise StructureReferenceError("来源前序依赖未闭合")
            prior_label = (
                "unknown"
                if prior == "unknown"
                else "、".join(amap[p] for p in prior) or "无"
            )
            edge_count += len(prior) if isinstance(prior, list) else 0
            material_id = atomic.get("shared_material_id", "unknown")
            if material_id != "unknown" and material_id not in mmap:
                raise StructureReferenceError("来源材料引用未闭合")
            if material_id in used:
                used[material_id].append(amap[aid])
            # Transfer task form only, not chemistry or difficulty classifications.
            response_form = atomic["response_form"].get("item_type", "unknown")
            response_label = RESPONSE_LABELS.get(
                response_form, "未识别作答形态（unknown）"
            )
            lines.append(
                f"{pmap[qid]} / {amap[aid]}：作答形态候选 {response_label}；"
                f"共同材料 {mmap.get(material_id, 'unknown')}；前序作答依赖 {prior_label}。"
            )
            seen.add(aid)
    for material_id, users in used.items():
        lines.append(
            f"{mmap[material_id]} 由 {'、'.join(users) or 'unknown'} 共同使用。"
        )
    lines.extend(
        [
            "迁移方式：先围绕所选教材目标规划材料，再把作答任务嵌入同一主题；明确哪些问题直接读材料，哪些接续前题结论。",
            "可以缩减或重组任务链，不机械复制本样本的小题数、作答方式比例或依赖图。",
            "不可据此新增超出所选章节的消毒剂知识、实验事实或数据；化学内容仍须取自本次教材证据。",
            "原题题干、答案、图片、知识编码、认知难度和实测难度未作为本次参考提供；新题必须独立确定，不得继承来源标签。",
        ]
    )
    return {
        "reference_key": REFERENCE_KEY,
        "label": REFERENCE_LABEL,
        "evidence": {
            "scope": REFERENCE_LABEL,
            "source_type": "question_structure_candidate",
            "supports": lines,
        },
        "counts": {
            "printed_questions": len(printed),
            "atomic_parts": len(atomics),
            "shared_materials": len(materials),
            "prior_dependencies": edge_count,
        },
        "local_provenance": {
            "source": deepcopy(digest["source"]),
            "adapter": deepcopy(digest.get("adapter_provenance", {})),
            "paper": deepcopy(digest["hierarchy"]["paper"]),
            "theme_id": theme["theme_id"],
            "alias_mapping": {"printed": pmap, "atomic": amap, "materials": mmap},
            "unknowns": deepcopy(digest.get("unknowns", [])),
        },
    }
