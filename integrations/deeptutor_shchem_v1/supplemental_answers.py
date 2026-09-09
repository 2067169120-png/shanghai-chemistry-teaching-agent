"""Source-bound, explicitly attributed solutions for source-absent answers.

These are assistant-authored worked solutions, not source reference answers.
The original scan records and their source-answer availability are unchanged.
"""

from __future__ import annotations

from collections.abc import Mapping
from copy import deepcopy
from typing import Any

REVISION_ID = "datong-h1-chlorine-solutions-20260909-r1"
ANSWER_LABEL = "补充解答（AI，非官方）"
SOURCE_LABEL = "依据已核对题面与上海高中化学知识补写；原归档未附答案。"
_PREFIX = "W1-DT2025-H1-MID-AP-DT2025-H1-"
_QUESTION_HASHES = {
    "Q01": "42cac2ea9f6eee05c8be61fbed19f5f039be079e9fc8f2b192d4d394756dd529",
    "Q02": "337ba2cd048a982adc8e657e5e5dbf2e56e5e2e5730f557f7be0667f705d4349",
    "Q03": "fb61d2e0244fa7fb79b1f8785c35a47edacaa5d859938f205c7dbb70d09dc4ea",
    "Q04": "cad523a6c7f3ad0c04256808a95f3e2c1eddd3da828334d42a0c8a4c0c9b5953",
    "Q05": "0c9c7bc3b33d2f19f4d4b2c9ddfa8aa88ab09baca06c71904f53e67437e23ed2",
    "Q06": "bea2b6844c9d44f33fb00fdf6837fb991973ba1e6e5f61d3429fa838b6553902",
    "Q07": "82b3ce9bd7a844a2ebca467d7ebcb1ed5040befc8aba736d5842ce9f9771ca58",
    "Q08": "4439be4a14dfa128431b06caf58c5ad7c0a8bf46cd88b25f4cab0cacfa090098",
    "Q09": "96937902407da606a1ce3961a720d9fd7b569a3429aacccc68ab0c0f06e7ec68",
}


class SupplementalAnswerError(ValueError):
    """An answer is malformed or no longer matches its grounded revision."""


# Exact atomic IDs, never a guessed mapping from a displayed question number.
_SOLUTIONS = {
    "Q01-P01": (
        "C。",
        "A：氯气呈黄绿色，有毒。B：干燥氯气没有漂白性，湿布褪色是因为氯气与水反应生成次氯酸。C：常温、干燥条件下，铁与氯气不反应，可以用钢瓶储运液氯。D：图示容器倒置，不适合收集密度比空气大的氯气。",
        None,
    ),
    "Q02-P01": (
        "A（长颈漏斗）。",
        "粗盐提纯的主要操作是溶解、过滤、蒸发，需要烧杯、玻璃棒、普通漏斗和蒸发皿；不需要长颈漏斗。",
        None,
    ),
    "Q03-P01-S01": (
        "核电荷数为 17，核外电子分三层排布：2、8、7。结构示意图见下图。",
        "氯的原子序数为 17；中性氯原子有 17 个电子，三层电子数之和为 2 + 8 + 7 = 17。",
        "chlorine_atom_shells",
    ),
    "Q03-P01-S02": (
        "Cl⁻ 的电子式见下图：Cl 周围画 8 个电子，整体加方括号，右上角标 −。",
        "氯原子得到 1 个电子形成氯离子，最外层达到 8 电子稳定结构。电子式只表示最外层电子，不画 18 个电子。",
        "chloride_lewis",
    ),
    "Q04-P01-S01": (
        "阳极（与电源正极相连）。",
        "氯离子在阳极失去电子，发生氧化反应：2Cl⁻ → Cl₂↑ + 2e⁻。物质：氯离子生成氯气。",
        None,
    ),
    "Q04-P01-S02": (
        "湿润的淀粉碘化钾试纸。",
        "氯气将碘离子氧化为碘，碘遇淀粉呈蓝色：Cl₂ + 2I⁻ → 2Cl⁻ + I₂。物质：氯气、碘离子、氯离子、碘。",
        None,
    ),
    "Q04-P01-S03": (
        "酚酞试液。",
        "阴极附近生成 OH⁻，溶液呈碱性，滴加酚酞变红。阴极反应：2H₂O + 2e⁻ → H₂↑ + 2OH⁻。物质：水生成氢气和氢氧根离子。",
        None,
    ),
    "Q05-P01-S01": (
        "苍白色。",
        "氢气在氯气中安静燃烧，发出苍白色火焰，生成氯化氢。",
        None,
    ),
    "Q05-P01-S02": (
        "盐酸（先合成氯化氢，再用水吸收）。",
        "H₂ + Cl₂ →（点燃）2HCl。物质：氢气与氯气生成氯化氢；工业上再用水吸收氯化氢，制得盐酸。",
        None,
    ),
    "Q06-P01-S01": (
        "MnO₂ + 4HCl（浓）→（加热）MnCl₂ + Cl₂↑ + 2H₂O。",
        "物质：二氧化锰与浓盐酸在加热条件下反应，生成氯化锰、氯气和水。方程式需保留浓盐酸、加热条件和气体符号。",
        None,
    ),
    "Q06-P01-S02": (
        "锰（Mn）。",
        "Mn 在 MnO₂ 中为 +4 价，在 MnCl₂ 中为 +2 价，化合价降低、得到电子，因此锰元素被还原。",
        None,
    ),
    "Q06-P01-S03": (
        "0.040N_A（即 0.04N_A）。",
        "n(MnO₂) = 1.74 g ÷ 87 g·mol⁻¹ = 0.020 mol。每 1 mol MnO₂ 中的 Mn 从 +4 价降至 +2 价，转移 2 mol 电子；所以 n(e⁻) = 0.040 mol，电子数为 0.040N_A。浓盐酸过量，按二氧化锰计算。",
        None,
    ),
    "Q07-P01": (
        "C、D。",
        "氯气与铝、镁分别生成 AlCl₃、MgCl₂；与铁、铜分别生成 FeCl₃、CuCl₂。因此 FeCl₂、CuCl 不是题述直接反应的产物。物质名称：氯化铝、氯化镁、氯化铁、氯化铜。",
        None,
    ),
    "Q08-P01": (
        "B。",
        "A：二氧化锰与浓盐酸制氯气需要加热，图中缺少加热装置。B：先用饱和食盐水除去氯化氢，再用浓硫酸干燥，正确。C：收集氯气应长管进、短管出，图中气流方向相反。D：尾气应使用氢氧化钠溶液等吸收，不能只用水。",
        None,
    ),
    "Q09-P01": (
        "除去氯气中混有的氯化氢，并减少氯气的溶解损失。",
        "浓盐酸具有挥发性，制得的氯气混有氯化氢。氯化氢极易溶于水；使用饱和食盐水既能除杂，又能减少氯气的损失。水蒸气由后续浓硫酸除去。",
        None,
    ),
}


def _payload(suffix: str) -> dict[str, Any]:
    text, explanation, diagram = _SOLUTIONS[suffix]
    question = suffix.split("-", 1)[0]
    return {
        "answer_id": f"DT-H1-CL-{suffix}-20260909",
        "node_id": _PREFIX + suffix,
        "revision_id": REVISION_ID,
        "source_kind": "assistant_supplement",
        "label_zh": ANSWER_LABEL,
        "source_label_zh": SOURCE_LABEL,
        "source_question_sha256": _QUESTION_HASHES[question],
        "text_zh": text,
        "explanation_zh": explanation,
        "pitfalls_zh": {
            "Q01-P01": ["区分常温干燥的储运条件与铁在氯气中燃烧的条件。"],
            "Q04-P01-S01": ["电解池阳极与电源正极相连。"],
            "Q04-P01-S02": ["观察湿润淀粉碘化钾试纸变蓝，不以继续褪色为必要现象。"],
        }.get(suffix, []),
        "diagram_key": diagram,
        "independently_verified": False,
    }


def all_supplemental_answers() -> tuple[dict[str, Any], ...]:
    return tuple(_payload(suffix) for suffix in _SOLUTIONS)


def validate_supplemental_answer(value: Any) -> dict[str, Any]:
    if not isinstance(value, Mapping):
        raise SupplementalAnswerError("补充解答格式不正确。")
    node = value.get("node_id")
    suffix = node.removeprefix(_PREFIX) if isinstance(node, str) else ""
    if suffix not in _SOLUTIONS or dict(value) != _payload(suffix):
        raise SupplementalAnswerError("补充解答内容或来源绑定与当前修订不一致。")
    return deepcopy(dict(value))


def answer_for_scan(scan: Mapping[str, Any]) -> dict[str, Any] | None:
    node = scan.get("node_id")
    if not isinstance(node, str) or not node.startswith(_PREFIX):
        return None
    suffix = node[len(_PREFIX) :]
    if suffix not in _SOLUTIONS:
        return None
    source_answer = scan.get("reference_answer", {})
    if source_answer.get("availability") != "absent":
        return None  # Never replace an existing source answer.
    question = suffix.split("-", 1)[0]
    expected = (f"DT2025-H1-{question}-E1", _QUESTION_HASHES[question])
    actual = {
        (row.get("crop_id"), row.get("sha256"))
        for row in scan.get("evidence_descriptors", [])
        if isinstance(row, Mapping) and row.get("evidence_role") == "question"
    }
    if scan.get("paper_id") != "W1-DT2025-H1-MID" or actual != {expected}:
        raise SupplementalAnswerError("补充解答对应的题图版本不一致，请重新核对。")
    return _payload(suffix)
