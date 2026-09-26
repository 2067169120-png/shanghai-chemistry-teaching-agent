from __future__ import annotations

from copy import deepcopy
from pathlib import Path

from .classification import validate_atomic_classification
from .content_metadata import apply_content_metadata_contract
from .explanations import DETAILED_EXPLANATIONS


VERSION_ID = "SHCHEM-GEN-V2-DEMO-20260813-R18"
PAPER_ID = "PAPER-GEN-V2-20260813-018"
FIGURE_ID = "FIG-GEN-V2-EWASTE-ELECTROLYSIS-008"
COMPONENT_REGISTRY_FILENAME = "component_registry_r18.json"
MCH_SOURCE_EXTRACT_ID = "SRCEX-MCH-SEKINE-HIGO-2021-P2-V1"
MCH_SOURCE_EXTRACT_PATH = (
    "staging/v1_generation/evidence/r12/"
    "SRCEX-MCH-SEKINE-HIGO-2021-P2-V1/source_extract.json"
)

SOURCE_CARD_PATH = (
    "sh-chem-db/05_命题热点素材/热点证据候选/"
    "wave1_2026-08-04/scientific_fact_cards.json"
)
STYLE_RULEBOOK_PATH = (
    "sh-chem-db/kb/paper_learning_v1/reports/evidence_backed_style_rulebook.json"
)
STRUCTURE_PROFILE_PATH = (
    "sh-chem-db/kb/exam_structure/current_shanghai_chemistry_profile.json"
)


def _difficulty(level: str = "D2", **overrides: str) -> dict:
    factors = {
        "information_conversion": "low",
        "reasoning_steps": "medium",
        "knowledge_span": "low",
        "representation_switch": "low",
        "calculation_load": "low",
        "experiment_load": "low",
        "openness": "low",
        "unfamiliarity": "medium",
        "language_load": "low",
        "prior_part_dependency": "none",
    }
    factors.update(overrides)
    return {
        "cognitive_prelabel": level,
        "evidence": factors,
        "measured_difficulty": None,
        "claim_boundary": "认知预标；无真实学生作答数据，不代表实测难度。",
    }


def _part(
    number: int,
    prompt: str,
    item_type: str,
    response: str,
    representation: list[str],
    knowledge: str,
    ability: str,
    chain_role: str,
    answer: dict,
    *,
    options: list[str] | None = None,
    solver: dict | None = None,
    equation: dict | None = None,
    evidence_refs: list[str] | None = None,
    difficulty: dict | None = None,
    semantic_signature: str,
    dependencies: list[str] | None = None,
) -> dict:
    pid = f"P{number:02d}"
    qid = "UNBOUND_PRINTED_QUESTION"
    is_choice = item_type == "embedded_single_choice"
    part = {
        "part_id": pid,
        "parent_printed_question_id": qid,
        "label": str(number),
        "score": 4,
        "item_type": item_type,
        "selection_rule": "single" if is_choice else "not_applicable",
        "response_R": response,
        "representation_RP": representation,
        "primary_knowledge_K": knowledge,
        "supporting_knowledge_K": [],
        "ability_A": [ability],
        "context_C": ["current_science_and_sustainability"],
        "primary_theme_chain_role": chain_role,
        "dependencies": dependencies or [],
        "prompt": prompt,
        "options": options or [],
        "answer": answer,
        "solver": solver,
        "equation_balance": equation,
        "evidence_refs": evidence_refs or [],
        "difficulty": difficulty or _difficulty(),
        "originality": {
            "semantic_signature": semantic_signature,
            "changed_dimensions": [
                "new_information_organization",
                "new_data_or_model_chain",
                "new_question_sequence",
            ],
            "source_pixels_reused": False,
        },
    }
    classification = validate_atomic_classification(part)
    if classification["status"] != "pass":
        raise ValueError(
            f"{pid}: invalid controlled classification: {classification['errors']}"
        )
    return part


def _eq(reactants: list[dict], products: list[dict]) -> dict:
    return {"reactants": reactants, "products": products}


def _species(
    name: str,
    coefficient: int,
    atoms: dict[str, int],
    charge: int = 0,
    *,
    chinese_names: list[str] | None = None,
) -> dict:
    value = {
        "name": name,
        "coefficient": coefficient,
        "atoms": atoms,
        "charge": charge,
    }
    if chinese_names:
        value["chinese_names"] = chinese_names
    return value


def _name_contract(*species: dict[str, object]) -> dict:
    return {
        "required": True,
        "source": "equation_balance_species_bidirectional_binding",
        "required_species_names": [
            {
                "species_id": item["species_id"],
                "accepted_chinese_names": item["accepted_chinese_names"],
            }
            for item in species
        ],
        "full_score_requires_all_species_names": True,
    }


def _attach_suggested_scoring(part: dict) -> None:
    """Attach explicit half-point weights whose sum is the atomic score."""
    criteria = list(part["answer"]["score_points"])
    total_half_points = int(part["score"]) * 2
    if not criteria or total_half_points < len(criteria):
        raise ValueError(
            f"{part['part_id']} cannot allocate at least 0.5 point to each criterion"
        )
    if part["item_type"] == "embedded_single_choice":
        if len(criteria) != 1:
            raise ValueError(
                f"{part['part_id']} single-choice rubric must have exactly one all-or-nothing criterion"
            )
        weights = [float(part["score"])]
        aggregation = "all_or_nothing_single_criterion"
    else:
        units = [1] * len(criteria)
        for index in range(total_half_points - len(criteria)):
            units[index % len(criteria)] += 1
        weights = [unit / 2 for unit in units]
        aggregation = "sum_satisfied_criteria_capped_at_max_score"
    part["answer"]["suggested_scoring"] = {
        "authority": "suggested_nonofficial",
        "max_score": part["score"],
        "aggregation": aggregation,
        "criterion_weights": [
            {
                "criterion": criterion,
                "points": weight,
                **(
                    {
                        "criterion_type": "required_major_substance_names",
                        "required_species_ids": [
                            row["species_id"]
                            for row in part["answer"]["major_substance_name_contract"][
                                "required_species_names"
                            ]
                        ],
                    }
                    if criterion == "必需物种名称集合完整且正确"
                    else {}
                ),
            }
            for criterion, weight in zip(criteria, weights)
        ],
        "equivalent_response_policy": (
            "接受化学意义等价、证据边界一致且满足相应判据的表述；"
            "不得把本建议权重称为官方采分点。"
        ),
    }


def _printed(number: int, parts: list[dict], *, dependency_relation: str | None = None) -> dict:
    qid = f"Q{number:02d}"
    for index, part in enumerate(parts, start=1):
        part["parent_printed_question_id"] = qid
        part["label"] = str(number) if len(parts) == 1 else f"{number}（{index}）"
    return {
        "printed_question_id": qid,
        "parent_theme_id": "UNBOUND_THEME",
        "display_number": number,
        "theme_order": 0,
        "dependency_relation": dependency_relation or (
            "uses_prior_result"
            if any(part.get("dependencies") for part in parts)
            else "shared_stimulus_only"
        ),
        "atomic_parts": parts,
    }


def _theme(theme_no: int, title: str, context: str, source_cards: list[str], questions: list[dict]) -> dict:
    theme_id = f"T{theme_no:02d}"
    for order, question in enumerate(questions, start=1):
        question["parent_theme_id"] = theme_id
        question["theme_order"] = order
    return {
        "theme_id": theme_id,
        "parent_paper_id": PAPER_ID,
        "order": theme_no,
        "title": title,
        "score": sum(
            part["score"] for question in questions for part in question["atomic_parts"]
        ),
        "context": context,
        "shared_material": context,
        "source_card_refs": source_cards,
        "printed_questions": questions,
    }


def build_paper() -> dict:
    suggested = "suggested"
    t1 = [
        _part(
            1,
            "某报告估计2022年全球产生620亿 kg电子废弃物，其中138亿 kg被正式收集并以环境无害方式回收。下列说法正确的是",
            "embedded_single_choice",
            "single_choice",
            ["text", "quantitative_data"],
            "K01",
            "A01",
            "theme_entry_and_orientation",
            {"label": suggested, "key": "B", "value": "正式回收比例约为22.3%，该比例不能直接代表任一国家或某类电池。", "score_points": ["选择B；只有比例计算与统计口径边界同时正确才得本题全部分数"]},
            options=[
                "A. 2030年电子废弃物一定为820亿 kg",
                "B. 正式回收比例约为22.3%，且不能直接外推到某一国家或产品",
                "C. 620亿 kg是单件电子产品的平均质量",
                "D. 138亿 kg可直接作为某湿法冶金工艺的产率分母",
            ],
            evidence_refs=["HOT-W1-MET-003"],
            semantic_signature="ewaste-global-flow-boundary-single-choice",
        ),
        _part(
            2,
            "图1为处理模拟含CuSO4废液的低压直流电解装置。写出阴极反应式，并注明物质名称。",
            "chemical_equation_or_notation",
            "chemical_equation",
            ["apparatus", "chemical_symbols"],
            "K11",
            "A05",
            "concept_or_relation_establishment",
            {"label": suggested, "value": "Cu²⁺（铜离子）+ 2e⁻ → Cu（铜）", "score_points": ["离子与电子数正确", "产物为铜单质", "必需物种名称集合完整且正确"], "major_substance_name_contract": _name_contract({"species_id": "Cu2+", "accepted_chinese_names": ["铜离子", "二价铜离子"]}, {"species_id": "Cu", "accepted_chinese_names": ["铜", "铜单质"]})},
            equation=_eq(
                [_species("Cu2+", 1, {"Cu": 1}, 2, chinese_names=["铜离子", "二价铜离子"]), _species("e-", 2, {}, -1)],
                [_species("Cu", 1, {"Cu": 1}, 0, chinese_names=["铜", "铜单质"])],
            ),
            semantic_signature="copper-electrowinning-cathode-half-reaction",
        ),
        _part(
            3,
            "在酸性介质中，惰性阳极上水被氧化。写出用H⁺配平的阳极反应式，并注明物质名称。",
            "chemical_equation_or_notation",
            "chemical_equation",
            ["chemical_symbols"],
            "K11",
            "A05",
            "representation_conversion",
            {"label": suggested, "value": "2H₂O（水）→ O₂（氧气）+ 4H⁺（氢离子）+ 4e⁻", "score_points": ["元素守恒", "电荷守恒", "电子位于生成物一侧", "必需物种名称集合完整且正确"], "major_substance_name_contract": _name_contract({"species_id": "H2O", "accepted_chinese_names": ["水"]}, {"species_id": "O2", "accepted_chinese_names": ["氧气", "氧"]}, {"species_id": "H+", "accepted_chinese_names": ["氢离子"]})},
            equation=_eq(
                [_species("H2O", 2, {"H": 2, "O": 1}, 0, chinese_names=["水"])],
                [
                    _species("O2", 1, {"O": 2}, 0, chinese_names=["氧气", "氧"]),
                    _species("H+", 4, {"H": 1}, 1, chinese_names=["氢离子"]),
                    _species("e-", 4, {}, -1),
                ],
            ),
            semantic_signature="copper-electrowinning-water-oxidation-half-reaction",
        ),
        _part(
            4,
            "结合图1，说明装置设置柔性收集袋且要求满袋前停止，同时禁止密闭加热的安全理由。",
            "experiment_operation_apparatus_plan",
            "short_explanation",
            ["apparatus"],
            "K01",
            "A06",
            "experiment_or_process_decision",
            {"label": suggested, "value": "阳极产生氧气；柔性收集袋在额定容量内随气体进入而膨胀，避免把产气端接成刚性密闭空间，但它不是可无限持续使用的泄压口，充满前必须停止。电解槽保持常压连通且禁止加热，可避免气体膨胀导致压强升高。", "score_points": ["识别氧气来源", "指出柔性袋只在额定容量内提供可变体积", "说明槽体常压连通及禁止密闭加热", "指出充满前停止"]},
            semantic_signature="electrolysis-flexible-vent-safety-explanation",
            difficulty=_difficulty("D2", experiment_load="medium", reasoning_steps="medium"),
            dependencies=["P03"],
        ),
        _part(
            5,
            "为单独校准该电源与称量流程，另用一种二电子还原体系：每生成1 mol沉积物需2 mol电子，沉积物摩尔质量为65.4 g·mol⁻¹。电流为2.00 A，通电1930 s，电流效率为80.0%，取F=9.65×10⁴ C·mol⁻¹，计算实际沉积质量。",
            "quantitative_calculation",
            "calculation",
            ["quantitative_data", "chemical_equation"],
            "K11",
            "A04",
            "model_application_or_quantitative_derivation",
            {"label": suggested, "value": "1.05 g", "score_points": ["Q=It", "按2 mol电子生成1 mol沉积物", "乘以电流效率并保留3位有效数字"]},
            solver={"type": "faraday_mass", "params": {"current_A": 2.00, "time_s": 1930, "efficiency": 0.800, "molar_mass_g_mol": 65.4, "electron_number": 2, "faraday_C_mol": 96500}, "expected": 1.0464, "unit": "g", "tolerance": 0.006},
            semantic_signature="independent-two-electron-calibration-faraday-mass",
            difficulty=_difficulty("D3", calculation_load="medium", reasoning_steps="medium", prior_part_dependency="conceptual_only"),
            dependencies=[],
        ),
        _part(
            6,
            "已知Cu的原子序数为29。下列关于Cu²⁺的说法正确的是",
            "embedded_single_choice",
            "single_choice",
            ["text", "particle_model"],
            "K12",
            "A03",
            "concept_or_relation_establishment",
            {"label": suggested, "key": "C", "value": "Cu²⁺核外有27个电子，核内质子数仍为29。", "score_points": ["区分原子序数、质子数和离子电子数"]},
            options=[
                "A. Cu²⁺核内有27个质子",
                "B. Cu²⁺核外有29个电子",
                "C. Cu²⁺核外有27个电子，核内质子数仍为29",
                "D. Cu²⁺与Cu原子的质量数一定不同",
            ],
            semantic_signature="copper-ion-atomic-number-electron-count",
        ),
        _part(
            7,
            "说明CuSO₄水溶液能够导电的微观原因，并写出两种主要导电微粒。",
            "reasoned_explanation",
            "short_explanation",
            ["particle_model", "chemical_symbols"],
            "K10",
            "A05",
            "representation_conversion",
            {"label": suggested, "value": "CuSO₄是电解质，在水中电离形成可自由移动的Cu²⁺（铜离子）和SO₄²⁻（硫酸根离子），离子的定向移动形成电流。", "score_points": ["指出电解质电离", "写出Cu²⁺和SO₄²⁻", "说明离子定向移动"]},
            semantic_signature="copper-sulfate-electrolyte-conduction-particles",
        ),
        _part(
            8,
            "为准确测定电解后阴极质量变化，写出取出阴极后的两项必要处理，并说明称量前为何要达到恒重。",
            "experiment_operation_apparatus_plan",
            "short_explanation",
            ["apparatus", "text"],
            "K01",
            "A06",
            "experiment_or_process_decision",
            {"label": suggested, "value": "用蒸馏水洗去附着电解液，再用乙醇淋洗或适当方法干燥；冷却后反复称量至恒重，以避免残留液体或未稳定含水量造成系统称量误差。", "score_points": ["洗涤去除电解液", "干燥并冷却", "恒重控制称量误差"]},
            semantic_signature="copper-cathode-wash-dry-constant-mass-verification",
            dependencies=[],
        ),
    ]

    t2 = [
        _part(
            9,
            "某2026年研究以100 mg空气处理的1T′-MoS₂/C为催化剂、5 mL DMF为溶剂，在室温下向批式反应器初始充入0.55 MPa CO₂和2.75 MPa H₂，再升温至210 ℃反应5 h。下列解读正确的是",
            "embedded_single_choice",
            "single_choice",
            ["text", "quantitative_data"],
            "K09",
            "A01",
            "theme_entry_and_orientation",
            {"label": suggested, "key": "C", "value": "3.30 MPa是室温初始充气总压，不能写成210 ℃下的实测压力。", "score_points": ["识别温度与压力的条件边界"]},
            options=[
                "A. H₂与CO₂初始分压之比为3∶1",
                "B. 3.30 MPa是210 ℃下的实测总压",
                "C. 3.30 MPa是室温初始充气总压",
                "D. 5 h数据可证明工业化经济性",
            ],
            evidence_refs=["HOT-W1-CGW-002"],
            semantic_signature="co2-methanol-condition-boundary-choice",
        ),
        _part(
            10,
            "本题仅采用教材理想气相平衡模型：在题设反应温度下将CO₂、H₂、CH₃OH和H₂O均按气体处理。写出二氧化碳加氢生成甲醇和水的化学方程式，并注明物质名称与物态。",
            "chemical_equation_or_notation",
            "chemical_equation",
            ["chemical_symbols"],
            "K11",
            "A05",
            "concept_or_relation_establishment",
            {"label": suggested, "value": "CO₂(g)（二氧化碳）+ 3H₂(g)（氢气） ⇌ CH₃OH(g)（甲醇）+ H₂O(g)（水蒸气）", "score_points": ["配平正确", "题设理想气相模型的物态与可逆号完整", "必需物种名称集合完整且正确"], "major_substance_name_contract": _name_contract({"species_id": "CO2", "accepted_chinese_names": ["二氧化碳"]}, {"species_id": "H2", "accepted_chinese_names": ["氢气", "氢"]}, {"species_id": "CH3OH", "accepted_chinese_names": ["甲醇"]}, {"species_id": "H2O", "accepted_chinese_names": ["水蒸气", "水"]})},
            equation=_eq(
                [_species("CO2", 1, {"C": 1, "O": 2}, chinese_names=["二氧化碳"]), _species("H2", 3, {"H": 2}, chinese_names=["氢气", "氢"])],
                [_species("CH3OH", 1, {"C": 1, "H": 4, "O": 1}, chinese_names=["甲醇"]), _species("H2O", 1, {"H": 2, "O": 1}, chinese_names=["水蒸气", "水"])],
            ),
            semantic_signature="co2-hydrogenation-methanol-balanced-equation",
        ),
        _part(
            11,
            "以1.00 mol初始CO₂为基准，若CO₂转化率为23.0%、甲醇选择性为99.2%（按转化碳计），计算生成甲醇的质量。M(CH₃OH)=32.0 g·mol⁻¹。",
            "quantitative_calculation",
            "calculation",
            ["quantitative_data", "chemical_equation"],
            "K11",
            "A04",
            "model_application_or_quantitative_derivation",
            {"label": suggested, "value": "7.30 g", "score_points": ["先算转化CO₂", "再乘甲醇选择性", "按1∶1碳计量关系换算质量"]},
            solver={"type": "sequential_yield_mass", "params": {"initial_mol": 1.00, "conversion": 0.230, "selectivity": 0.992, "stoich_ratio": 1.0, "molar_mass_g_mol": 32.0}, "expected": 7.30112, "unit": "g", "tolerance": 0.015},
            evidence_refs=["HOT-W1-CGW-002"],
            semantic_signature="co2-conversion-selectivity-methanol-mass",
            difficulty=_difficulty("D3", calculation_load="medium", reasoning_steps="medium"),
            dependencies=["P10"],
        ),
        _part(
            12,
            "沿用第10题的教材理想气相平衡模型，且已知该反应ΔH<0。仅从化学平衡角度，分别说明恒温压缩、减小体积使压强增大和升高温度对甲醇平衡产率的影响，并指出该模型的一项边界。",
            "reasoned_explanation",
            "short_explanation",
            ["chemical_equation", "text"],
            "K09",
            "A05",
            "evidence_based_explanation_or_evaluation",
            {"label": suggested, "value": "理想气相模型中气体物质的量由4 mol减为2 mol；恒温压缩、减小体积使压强增大时，平衡向生成物方向移动，甲醇平衡产率增大。升高温度使放热反应平衡向逆反应方向移动，甲醇平衡产率减小。真实批式体系的相态更复杂，本判断只用于题设模型的定性分析。", "score_points": ["按题设气相模型比较4→2", "由恒温压缩判断平衡移动", "放热方向判断", "说明模型边界"]},
            semantic_signature="methanol-equilibrium-pressure-temperature-explanation",
            dependencies=["P10"],
        ),
        _part(
            13,
            "研究还报告在210 ℃、CO₂转化率低于10%的速率测量中，甲醇比反应速率为0.91±0.01 g·g⁻¹·h⁻¹；该速率测量的催化剂分母、取样时间窗口和反应状态等细节未给出，不能认定其与上述5 h批式条件相同。说明为何不能把该速率与23.0%转化率直接拼成同一质量衡算。",
            "comparison_or_open_response",
            "short_explanation",
            ["text", "quantitative_data"],
            "K01",
            "A09",
            "integrated_transfer_or_open_synthesis",
            {"label": suggested, "value": "两组数据对应的转化率与测量条件不同；速率数据的分母、时间窗口和反应状态不能与典型5 h批式结果无条件视作同一实验。", "score_points": ["指出条件不同", "指出分母/时间口径不同", "拒绝无条件拼接"]},
            evidence_refs=["HOT-W1-CGW-002"],
            semantic_signature="methanol-rate-conversion-data-boundary-evaluation",
            difficulty=_difficulty("D3", openness="medium", reasoning_steps="medium"),
        ),
        _part(
            14,
            "关于催化剂对该可逆反应的影响，下列说法正确的是",
            "embedded_single_choice",
            "single_choice",
            ["text", "energy_model"],
            "K09",
            "A05",
            "concept_or_relation_establishment",
            {"label": suggested, "key": "A", "value": "催化剂可同时降低正、逆反应活化能，加快达到平衡，但不改变平衡常数和平衡组成。", "score_points": ["区分速率与平衡"]},
            options=[
                "A. 催化剂加快达到平衡，但不改变该温度下的平衡常数",
                "B. 催化剂只降低正反应活化能",
                "C. 催化剂使放热反应的ΔH变小",
                "D. 催化剂必然使CO₂平衡转化率增大",
            ],
            semantic_signature="methanol-catalyst-rate-equilibrium-boundary",
        ),
        _part(
            15,
            "若要比较两种催化剂在同一动力学区间的甲醇生成速率，请在以下三类中各写一项必须保持一致或明确记录的具体条件：①热力学/投料条件；②催化剂口径；③速率测量口径。并说明三类条件如何共同保证比较公平。",
            "experiment_operation_apparatus_plan",
            "short_explanation",
            ["text", "quantitative_data"],
            "K01",
            "A06",
            "experiment_or_process_decision",
            {"label": suggested, "value": "示例：①热力学/投料条件保持反应温度及CO₂/H₂初始分压比一致；②催化剂口径统一催化剂质量、活性组分含量或活性位点计量；③速率测量口径统一低转化率取样窗口、分析方法及归一化分母。这样可把速率差异归因于被比较的催化剂因素，而不是投料、催化剂用量或测量方法差异。各类中等价且合理的具体条件均可。", "score_points": ["热力学或投料条件具体且可比", "催化剂质量或活性口径具体且可比", "速率测量窗口、方法或归一化口径具体且可比", "说明控制变量与公平比较关系"]},
            evidence_refs=["HOT-W1-CGW-002"],
            semantic_signature="methanol-catalyst-controlled-rate-comparison-design",
            dependencies=["P13"],
        ),
    ]

    t3 = [
        _part(
            16,
            "DOE某技术目标把完整车载储氢系统的可用氢质量容量写为0.055 kg H₂/kg system。若系统需提供5.00 kg可用氢，按该目标计算系统质量。",
            "quantitative_calculation",
            "calculation",
            ["quantitative_data"],
            "K01",
            "A04",
            "information_or_representation_extraction",
            {"label": suggested, "value": "9.1×10¹ kg（完整系统，按0.055保留2位有效数字）", "score_points": ["识别分母为完整系统", "5.00/0.055", "按限制数据保留2位有效数字"]},
            solver={"type": "ratio_division", "params": {"numerator": 5.00, "ratio": 0.055}, "expected": 90.9090909, "unit": "kg system", "tolerance": 0.06, "significant_figures": 2, "accepted_display_strings": ["9.1×10¹"], "display_acceptance": "two_significant_figures"},
            evidence_refs=["HOT-W1-HYD-001"],
            semantic_signature="hydrogen-system-gravimetric-target-mass",
            difficulty=_difficulty("D2", calculation_load="low"),
        ),
        _part(
            17,
            "同一技术目标给出0.040 kg H₂/L system。计算容纳5.00 kg可用氢所对应的系统体积。",
            "quantitative_calculation",
            "calculation",
            ["quantitative_data"],
            "K01",
            "A04",
            "model_application_or_quantitative_derivation",
            {"label": suggested, "value": "1.3×10² L system（按0.040保留2位有效数字）", "score_points": ["5.00/0.040", "单位为完整系统体积", "按限制数据保留2位有效数字"]},
            solver={"type": "ratio_division", "params": {"numerator": 5.00, "ratio": 0.040}, "expected": 125.0, "unit": "L system", "tolerance": 0.05, "significant_figures": 2, "accepted_display_strings": ["1.3×10²"], "display_acceptance": "two_significant_figures"},
            evidence_refs=["HOT-W1-HYD-001"],
            semantic_signature="hydrogen-system-volumetric-target-volume",
        ),
        _part(
            18,
            "某综述在物理页2用结构简式表示甲基环己烷（MCH，记作c-C₆H₁₁—CH₃）与甲苯（C₆H₅—CH₃）的储氢—释氢循环。按本题教材口径，官能团指决定有机化合物特征性质的原子或原子团，不把苯环或环烷基单独列作官能团。下列说法正确的是",
            "embedded_single_choice",
            "single_choice",
            ["text", "organic_structure"],
            "K16",
            "A03",
            "concept_or_relation_establishment",
            {"label": suggested, "key": "B", "value": "甲苯含苯环，属于芳香烃；MCH属于环烷烃；按题定口径二者均为烃，不另列官能团。", "score_points": ["选择B；整项结构、类别与题定官能团口径均正确才得本题全部分数"]},
            options=[
                "A. MCH分子中含碳碳双键",
                "B. 甲苯含苯环，属于芳香烃；MCH属于环烷烃；按题定口径二者均不另列官能团",
                "C. 甲苯与MCH互为同分异构体",
                "D. 仅由两个结构简式即可判定完整储氢系统达到DOE目标",
            ],
            evidence_refs=[MCH_SOURCE_EXTRACT_ID],
            semantic_signature="methylcyclohexane-toluene-structure-classification",
        ),
        _part(
            19,
            "用结构简式写出甲苯吸氢生成MCH的可逆反应，并分别写出储氢方向和释氢方向的反应类型（催化剂写在箭头条件中）。",
            "chemical_equation_or_notation",
            "chemical_equation",
            ["chemical_symbols", "organic_structure", "reaction_route"],
            "K16",
            "A08",
            "representation_conversion",
            {"label": suggested, "value": "C₆H₅—CH₃（甲苯）+3H₂（氢气） —催化剂→ c-C₆H₁₁—CH₃（甲基环己烷，MCH）；c-C₆H₁₁—CH₃（MCH） —催化剂→ C₆H₅—CH₃（甲苯）+3H₂（氢气）。两式合起来表示可逆储氢—释氢关系；正向为催化加氢/加成（还原），逆向为催化脱氢（氧化）。", "score_points": ["结构简式和3H₂计量正确", "正逆两式的箭头本体均含催化剂条件", "储氢方向类型正确", "释氢方向类型正确"]},
            equation=_eq(
                [_species("C7H8", 1, {"C": 7, "H": 8}), _species("H2", 3, {"H": 2})],
                [_species("C7H14", 1, {"C": 7, "H": 14})],
            ),
            evidence_refs=[MCH_SOURCE_EXTRACT_ID],
            semantic_signature="mch-toluene-hydrogenation-dehydrogenation-route",
            dependencies=["P18"],
        ),
        _part(
            20,
            "该综述表1给出MCH载体层质量储氢密度6.16 wt%，DOE资料给出完整车载系统目标0.055 kg H₂/kg system。说明为何不能只比较6.16%与5.5%就宣布MCH完整系统达标，并列出两项还需核验的系统变量。",
            "comparison_or_open_response",
            "short_explanation",
            ["text", "quantitative_data", "reaction_route"],
            "K01",
            "A09",
            "integrated_transfer_or_open_synthesis",
            {"label": suggested, "value": "6.16 wt%以MCH载体质量为分母，5.5%以包含容器、催化反应器、换热、阀门和管路等的完整系统质量为分母，分母不同不能直接宣布达标。还需核验例如实际脱氢转化率/可用氢量、催化剂与反应器质量、释氢能耗与换热、循环寿命、安全监测等。", "score_points": ["指出载体层与完整系统分母不同", "拒绝直接宣布达标", "列出两项系统变量"]},
            evidence_refs=["HOT-W1-HYD-001", MCH_SOURCE_EXTRACT_ID],
            semantic_signature="lohc-carrier-versus-system-boundary-evaluation",
            difficulty=_difficulty("D3", openness="medium"),
            dependencies=["P19"],
        ),
        _part(
            21,
            "按综述表1的6.16 wt%载体层质量储氢密度，计算98.2 g MCH理论对应的氢质量（结果保留3位有效数字）。",
            "quantitative_calculation",
            "calculation",
            ["quantitative_data", "reaction_route"],
            "K01",
            "A04",
            "model_application_or_quantitative_derivation",
            {"label": suggested, "value": "6.05 g H₂（载体层文献汇编口径）", "score_points": ["98.2×6.16%", "保留载体层分母", "3位有效数字"]},
            solver={"type": "ratio_product", "params": {"numerator": 98.2, "ratio": 0.0616}, "expected": 6.04912, "unit": "g H2", "tolerance": 0.006},
            evidence_refs=[MCH_SOURCE_EXTRACT_ID],
            semantic_signature="mch-carrier-layer-hydrogen-mass-calculation",
            dependencies=["P19"],
        ),
        _part(
            22,
            "综述表1给出载体层循环的焓变指标59.4 kJ·mol⁻¹-H₂。说明为何不能把这一热化学数值直接当作完整储氢系统一次循环的实际能耗，并列出两类还需测量的能量或运行量。",
            "comparison_or_open_response",
            "short_explanation",
            ["quantitative_data", "text"],
            "K01",
            "A09",
            "evidence_based_explanation_or_evaluation",
            {"label": suggested, "value": "59.4 kJ·mol⁻¹-H₂是载体层汇编的热化学指标，不包含完整系统中换热损失、催化剂与反应器热容、泵送/压缩功和辅助设备耗能，不能直接当作一次循环的实际能耗。还需测量例如实际供热与回热量、泵送或压缩电功、不同负荷下的转化率与循环时间等。", "score_points": ["区分热化学指标与系统实耗", "列出两类需测量的能量或运行量"]},
            evidence_refs=[MCH_SOURCE_EXTRACT_ID],
            semantic_signature="mch-carrier-enthalpy-versus-system-energy-boundary",
            dependencies=["P20"],
        ),
        _part(
            23,
            "综述物理页2举出一项623 K催化脱氢研究。评价该高温产氢实验是否适合作为中学学生实验，并提出一项可替代的学习活动。",
            "experiment_operation_apparatus_plan",
            "short_explanation",
            ["reaction_route", "text"],
            "K01",
            "A06",
            "experiment_or_process_decision",
            {"label": suggested, "value": "高温催化脱氢会产生可燃H₂并涉及热源、气密和压力控制，不宜由学生直接复现；可改为分析已核验的结构式、平衡数据与载体/系统分母，或使用不产气的安全模型演示。", "score_points": ["识别高温与可燃氢风险", "给出不复现的安全替代"]},
            evidence_refs=[MCH_SOURCE_EXTRACT_ID],
            semantic_signature="lohc-high-temperature-hydrogen-safety-substitution",
        ),
    ]

    t4 = [
        _part(
            24,
            "WHO 2026版指南在同一表中列出：游离氯5 mg·L⁻¹指南值；pH<8.0且接触至少30 min后余氯≥0.5 mg·L⁻¹的有效消毒条件；交付点最小余氯0.2 mg·L⁻¹。下列说法正确的是",
            "embedded_single_choice",
            "single_choice",
            ["text", "quantitative_data"],
            "K10",
            "A09",
            "theme_entry_and_orientation",
            {"label": suggested, "key": "A", "value": "三个数值的角色不同，5 mg·L⁻¹不能直接写成普适推荐投加量。", "score_points": ["区分指南值、操作条件和末梢余量"]},
            options=[
                "A. 三个数值角色不同，5 mg·L⁻¹不是普适推荐投加量",
                "B. 只要余氯达到0.2 mg·L⁻¹即可断言所有病原体均被灭活",
                "C. 这些数值是上海地方强制标准",
                "D. pH越高，有效消毒条件越容易满足",
            ],
            evidence_refs=["HOT-W1-CGW-004"],
            semantic_signature="chlorine-guideline-operational-boundary-choice",
        ),
        _part(
            25,
            "测定游离氯时，酸性条件下ClO⁻将I⁻氧化为I₂。写出离子方程式，并注明主要物质名称。",
            "chemical_equation_or_notation",
            "chemical_equation",
            ["chemical_symbols"],
            "K11",
            "A05",
            "concept_or_relation_establishment",
            {"label": suggested, "value": "ClO⁻（次氯酸根）+2I⁻（碘离子）+2H⁺（氢离子）→I₂（碘）+Cl⁻（氯离子）+H₂O（水）", "score_points": ["元素守恒", "电荷守恒", "酸性条件完整", "必需物种名称集合完整且正确"], "major_substance_name_contract": _name_contract({"species_id": "ClO-", "accepted_chinese_names": ["次氯酸根", "次氯酸根离子"]}, {"species_id": "I-", "accepted_chinese_names": ["碘离子"]}, {"species_id": "H+", "accepted_chinese_names": ["氢离子"]}, {"species_id": "I2", "accepted_chinese_names": ["碘", "碘单质"]}, {"species_id": "Cl-", "accepted_chinese_names": ["氯离子"]}, {"species_id": "H2O", "accepted_chinese_names": ["水"]})},
            equation=_eq(
                [
                    _species("ClO-", 1, {"Cl": 1, "O": 1}, -1, chinese_names=["次氯酸根", "次氯酸根离子"]),
                    _species("I-", 2, {"I": 1}, -1, chinese_names=["碘离子"]),
                    _species("H+", 2, {"H": 1}, 1, chinese_names=["氢离子"]),
                ],
                [
                    _species("I2", 1, {"I": 2}, chinese_names=["碘", "碘单质"]),
                    _species("Cl-", 1, {"Cl": 1}, -1, chinese_names=["氯离子"]),
                    _species("H2O", 1, {"H": 2, "O": 1}, chinese_names=["水"]),
                ],
            ),
            semantic_signature="free-chlorine-iodide-redox-equation",
        ),
        _part(
            26,
            "生成的I₂用S₂O₃²⁻滴定。写出离子方程式，并注明含硫离子的名称。",
            "chemical_equation_or_notation",
            "chemical_equation",
            ["chemical_symbols"],
            "K11",
            "A05",
            "representation_conversion",
            {"label": suggested, "value": "I₂（碘）+2S₂O₃²⁻（硫代硫酸根）→2I⁻（碘离子）+S₄O₆²⁻（连四硫酸根）", "score_points": ["配平正确", "离子名称正确"]},
            equation=_eq(
                [_species("I2", 1, {"I": 2}), _species("S2O3--", 2, {"S": 2, "O": 3}, -2)],
                [_species("I-", 2, {"I": 1}, -1), _species("S4O6--", 1, {"S": 4, "O": 6}, -2)],
            ),
            semantic_signature="iodometric-thiosulfate-titration-equation",
        ),
        _part(
            27,
            "取100.0 mL水样，忽略结合氯及其他可氧化I⁻的干扰物，加入过量KI并酸化，生成的I₂消耗0.001000 mol·L⁻¹ Na₂S₂O₃溶液10.00 mL。按Cl₂的摩尔质量71.0 g·mol⁻¹折算，计算游离氯浓度。",
            "quantitative_calculation",
            "calculation",
            ["quantitative_data", "chemical_equation"],
            "K11",
            "A04",
            "model_application_or_quantitative_derivation",
            {"label": suggested, "value": "3.55 mg·L⁻¹（以Cl₂计）", "score_points": ["n(S₂O₃²⁻)=1.000×10⁻⁵ mol", "n(ClO⁻)=其一半", "换算到100.0 mL并转为mg·L⁻¹"]},
            solver={"type": "iodometric_chlorine", "params": {"thiosulfate_mol_L": 0.001000, "thiosulfate_mL": 10.00, "sample_mL": 100.0, "chlorine_molar_mass_g_mol": 71.0}, "expected": 3.55, "unit": "mg/L as Cl2", "tolerance": 0.006},
            semantic_signature="iodometric-free-chlorine-concentration-calculation",
            difficulty=_difficulty("D3", calculation_load="medium", prior_part_dependency="uses_stoichiometry"),
            dependencies=["P25", "P26"],
        ),
        _part(
            28,
            "仅按第24题所给WHO表中三个数值，分别评价上一小问所得3.55 mg·L⁻¹相对于5、0.5和0.2 mg·L⁻¹各自代表的意义；再写出一项不能据此推出的结论。",
            "comparison_or_open_response",
            "short_explanation",
            ["quantitative_data", "text"],
            "K01",
            "A09",
            "evidence_based_explanation_or_evaluation",
            {"label": suggested, "value": "3.55 mg·L⁻¹低于5 mg·L⁻¹指南值。它虽高于0.5 mg·L⁻¹，但题干未给pH<8.0及接触至少30 min，不能据此判定有效消毒；它也高于0.2 mg·L⁻¹，但水样是否取自交付点未知，不能据此判定交付点最低余氯已满足。还不能把WHO数值称作上海强制标准。", "score_points": ["与5 mg·L⁻¹指南值正确比较", "结合pH和接触时间评价0.5 mg·L⁻¹条件", "结合采样位置评价0.2 mg·L⁻¹交付点条件", "给出禁止外推"]},
            evidence_refs=["HOT-W1-CGW-004"],
            semantic_signature="free-chlorine-threshold-bounded-evaluation",
            dependencies=["P24", "P27"],
            difficulty=_difficulty("D3", reasoning_steps="medium", openness="medium", prior_part_dependency="numeric_result"),
        ),
        _part(
            29,
            "碘量滴定接近终点时才加入淀粉指示剂。说明过早加入可能造成的测量风险，并写出终点附近应采用的滴加操作。",
            "experiment_operation_apparatus_plan",
            "short_explanation",
            ["experiment_process", "text"],
            "K01",
            "A06",
            "experiment_or_process_decision",
            {"label": suggested, "value": "碘浓度较高时过早加入淀粉会形成较稳定的深色吸附/络合体系，使碘释放和终点判断滞后；接近终点时应逐滴加入滴定剂并充分振荡，待蓝色恰好褪去且短时间不恢复。", "score_points": ["指出过早加淀粉的终点滞后风险", "逐滴滴加", "充分振荡并按颜色褪去判断"]},
            semantic_signature="iodometric-starch-endpoint-operation-error",
            dependencies=["P26"],
        ),
        _part(
            30,
            "某同学平行测定三次，消耗Na₂S₂O₃溶液分别为10.00、10.02、10.88 mL。说明数据处理方法，并指出能否只删去10.88 mL后直接报告结果。",
            "comparison_or_open_response",
            "short_explanation",
            ["quantitative_data", "experiment_process"],
            "K01",
            "A06",
            "integrated_transfer_or_open_synthesis",
            {"label": suggested, "value": "10.00与10.02 mL接近，10.88 mL明显偏离，应先检查终点过滴、读数或样品处理等可归因错误；不能仅凭结果不顺眼就删值。若确认操作异常应重做该次并用满足平行性要求的数据求平均，同时保留排除理由。", "score_points": ["识别异常值", "要求查找可归因原因而非任意删值", "提出重做与合格平行数据平均"]},
            semantic_signature="iodometric-parallel-titration-outlier-retest",
            dependencies=["P29"],
        ),
    ]

    t5 = [
        _part(
            31,
            "研究同一组成CdSe量子点的尺寸效应时，下列比较方案最有利于把发射差异归因于粒径变化的是",
            "embedded_single_choice",
            "single_choice",
            ["text", "energy_model"],
            "K14",
            "A05",
            "theme_entry_and_orientation",
            {"label": suggested, "key": "B", "value": "保持组成、溶剂和测量温度一致，独立测定粒径后再比较发射光谱。", "score_points": ["选择B；同时满足控制组成与测量条件、独立测粒径后比较光谱才得本题全部分数"]},
            options=[
                "A. 改变材料组成，同时比较两个样品的发射峰",
                "B. 保持组成、溶剂和测量温度一致，独立测定粒径后比较发射光谱",
                "C. 只改变表面配体而不测粒径，再把峰位差全部归因于尺寸",
                "D. 只测一条发射光谱，不记录粒径和测量条件",
            ],
            evidence_refs=["HOT-W1-QDP-001"],
            semantic_signature="quantum-dot-size-effect-controlled-comparison-design",
        ),
        _part(
            32,
            "用E(eV)≈1240/λ(nm)估算：带隙为3 eV与1.8 eV时对应光子的波长各约为多少？分别按限制数据保留1位和2位有效数字。",
            "quantitative_calculation",
            "calculation",
            ["equation", "quantitative_data"],
            "K12",
            "A04",
            "model_application_or_quantitative_derivation",
            {"label": suggested, "value": "约4×10² nm与6.9×10² nm（即400 nm与690 nm）", "score_points": ["1240/3", "1240/1.8", "第一结果按1位、第二结果按2位有效数字并写单位nm"]},
            solver={"type": "photon_wavelength_pair", "params": {"constant_eV_nm": 1240.0, "energies_eV": [3, 1.8]}, "expected": [413.333333, 688.888889], "unit": "nm", "tolerance": 0.6, "significant_figures_by_value": [1, 2], "display_acceptance": "normalized_numeric_unit_pair_with_mixed_significant_figures"},
            evidence_refs=["HOT-W1-QDP-001"],
            semantic_signature="quantum-dot-bandgap-wavelength-pair-calculation",
            difficulty=_difficulty("D2", calculation_load="low"),
        ),
        _part(
            33,
            "某历史研究摘要报告纳米晶直径约12～115 Å。换算为nm，并说明该范围不能直接推出什么。",
            "short_fill",
            "fill_blank",
            ["quantitative_data", "text"],
            "K14",
            "A09",
            "representation_conversion",
            {"label": suggested, "value": "约1.2～11.5 nm；不能由该范围直接推出任意粒径唯一对应的发射颜色，也不能推广为所有量子点工艺的通用范围。", "score_points": ["Å到nm换算正确", "给出一项禁止外推"]},
            solver={"type": "angstrom_range_to_nm", "params": {"values_A": [12.0, 115.0]}, "expected": [1.2, 11.5], "unit": "nm", "tolerance": 0.001},
            evidence_refs=["HOT-W1-QDP-003"],
            semantic_signature="quantum-dot-size-range-unit-and-boundary",
        ),
        _part(
            34,
            "历史热注入路线涉及含镉前体、高温和惰性气氛。评价该路线是否适合作为中学学生实验，并提出一项可替代的学习活动。",
            "experiment_operation_apparatus_plan",
            "short_explanation",
            ["text"],
            "K01",
            "A06",
            "experiment_or_process_decision",
            {"label": suggested, "value": "该路线不适合作为中学学生实验：含镉物质具有毒性与废弃物风险，高温注入和惰性气氛操作还涉及烫伤、失火及压力控制。可改用已核验文献数据分析、安全结构模型或由具备条件的人员完成的合规演示。", "score_points": ["明确不适合学生直接实验", "识别含镉与高温/气氛操作风险", "提出安全替代"]},
            evidence_refs=["HOT-W1-QDP-003"],
            semantic_signature="quantum-dot-historical-method-school-safety-boundary",
        ),
        _part(
            35,
            "用“组成—结构—性质—用途/风险”链条，解释量子点尺寸调控为何既有材料价值又需要证据边界。",
            "comparison_or_open_response",
            "short_explanation",
            ["text", "energy_model"],
            "K14",
            "A09",
            "integrated_transfer_or_open_synthesis",
            {"label": suggested, "value": "同一半导体组成在纳米尺度下因尺寸改变能级/带隙，进而改变吸收和发射，可用于光电材料设计；但带隙示例不能直接证明具体器件效率、寿命或环境安全，含镉体系还需单独评价毒性与处置。", "score_points": ["尺寸影响能级/带隙", "带隙影响光学性质", "用途与风险分别评价", "不越过证据边界"]},
            evidence_refs=["HOT-W1-QDP-001", "HOT-W1-QDP-003"],
            semantic_signature="quantum-dot-composition-structure-property-risk-chain",
            difficulty=_difficulty("D4", reasoning_steps="high", knowledge_span="medium", openness="medium", representation_switch="medium"),
        ),
        _part(
            36,
            "含Cd²⁺的量子点废液不得直接排放。若用S²⁻将Cd²⁺转化为难溶CdS，写出离子方程式，并说明沉淀分离后仍需怎样处置。",
            "chemical_equation_or_notation",
            "short_explanation",
            ["chemical_symbols", "process_flow"],
            "K10",
            "A06",
            "integrated_transfer_or_open_synthesis",
            {"label": suggested, "value": "Cd²⁺（镉离子）+S²⁻（硫离子）→CdS↓（硫化镉）；过滤得到的含镉沉淀、滤材和相关化学废物应单独收集、标识，并交学校或有资质渠道按适用规范处置，不能因形成沉淀而称作无毒。", "score_points": ["离子方程式与沉淀符号正确", "含镉化学废物单独收集并标识", "交学校或有资质渠道按适用规范处置且不把沉淀等同无毒"]},
            equation=_eq(
                [_species("Cd2+", 1, {"Cd": 1}, 2), _species("S2-", 1, {"S": 1}, -2)],
                [_species("CdS", 1, {"Cd": 1, "S": 1})],
            ),
            evidence_refs=["HOT-W1-QDP-003"],
            semantic_signature="cadmium-sulfide-precipitation-waste-boundary",
            dependencies=["P34"],
        ),
    ]

    score_by_part = {
        **dict(zip((f"P{i:02d}" for i in range(1, 9)), [2, 3, 3, 3, 4, 2, 2, 3])),
        **dict(zip((f"P{i:02d}" for i in range(9, 16)), [2, 3, 4, 3, 3, 2, 5])),
        **dict(zip((f"P{i:02d}" for i in range(16, 24)), [3, 3, 2, 3, 3, 2, 2, 2])),
        **dict(zip((f"P{i:02d}" for i in range(24, 31)), [2, 3, 3, 4, 3, 2, 3])),
        **dict(zip((f"P{i:02d}" for i in range(31, 37)), [2, 3, 3, 2, 3, 3])),
    }
    for part in [*t1, *t2, *t3, *t4, *t5]:
        part["score"] = score_by_part[part["part_id"]]
        _attach_suggested_scoring(part)

    q1 = [_printed(index, [part]) for index, part in enumerate(t1, start=1)]
    q2 = [_printed(index, [part]) for index, part in enumerate(t2, start=9)]
    q3 = [_printed(index, [part]) for index, part in enumerate(t3, start=16)]
    q4 = [_printed(24, t4, dependency_relation="uses_prior_result")]
    q5 = [_printed(index, [part]) for index, part in enumerate(t5, start=25)]
    themes = [
        _theme(1, "电子废弃物中的铜循环", "以全球电子废弃物质量流、铜的微观结构和模拟含铜废液电解回收为共同情境。", ["HOT-W1-MET-003"], q1),
        _theme(2, "二氧化碳加氢制甲醇", "以一项2026年实验室批式催化研究为共同情境：典型条件为100 mg空气处理的1T′-MoS₂/C、5 mL DMF、室温初始0.55 MPa CO₂与2.75 MPa H₂（H₂/CO₂=5∶1），随后210 ℃反应5 h；典型数据为CO₂转化率23.0%、甲醇选择性99.2%，并有少量CO和CH₄。", ["HOT-W1-CGW-002"], q2),
        _theme(3, "液体有机储氢与系统尺度", "以本地物理页2机器核验证据摘录中的甲基环己烷—甲苯可逆储氢路线，以及完整车载储氢系统技术目标为共同情境；综述表值只作载体层汇编数据，不冒充原始测量或系统性能。", ["HOT-W1-HYD-001", MCH_SOURCE_EXTRACT_ID], q3),
        _theme(4, "饮用水余氯的测定与解释", "以一个连续印刷流程题组织WHO 2026版指南数值角色、碘量法反应、计算、终点操作和异常数据处理；七个最小作答单元共享材料并形成前序依赖。", ["HOT-W1-CGW-004"], q4),
        _theme(5, "量子点的尺寸与光学性质", "以CdSe量子点带隙示例、历史纳米晶研究边界及含镉废液处置为共同情境。", ["HOT-W1-QDP-001", "HOT-W1-QDP-003"], q5),
    ]
    printed_question_count = sum(len(theme["printed_questions"]) for theme in themes)
    atomic_part_count = sum(
        len(question["atomic_parts"])
        for theme in themes
        for question in theme["printed_questions"]
    )
    for theme in themes:
        for printed in theme["printed_questions"]:
            for part in printed["atomic_parts"]:
                explanation = DETAILED_EXPLANATIONS.get(part["part_id"])
                if explanation:
                    part["answer"]["detailed_explanation"] = explanation
    paper = {
        "schema_version": "2.0.0",
        "version_id": VERSION_ID,
        "paper_id": PAPER_ID,
        "content_status": "machine_review_pending",
        "claim_boundary": "原创机器候选；项目模板，不是官方试卷、官方答案或官方评分细则。",
        "answer_authority": "suggested",
        "rubric_authority": "suggested",
        "human_reviewed": False,
        "teaching_use_allowed": False,
        "publication_allowed": False,
        "task_card": {
            "version_id": VERSION_ID,
            "paper_id": PAPER_ID,
            "target": "2026_shanghai_level_exam_style_project_template",
            "duration_minutes": {
                "value": 60,
                "value_status": "project_template",
                "evidence_refs": [],
            },
            "total_score": {
                "value": 100,
                "value_status": "project_template",
                "evidence_refs": [],
            },
            "theme_count": {
                "value": 5,
                "value_status": "project_template",
                "evidence_refs": [],
            },
            "theme_scores": [22, 22, 20, 20, 16],
            "theme_score_design_basis": "project_template：总分100来自主观察profile；主题分值与原子作答单元分值由本地三套逐页核验区模的混合题型粒度作对抗校准，但回忆卷未提供可逐题核验的官方分值，故不冒充exact-profile或官方评分。",
            "numbering_mode": {
                "value": "continuous_across_paper",
                "value_status": "project_template",
                "evidence_refs": [],
            },
            "selection_rule": "project_template：主题内单选原子单元2分，填空/方程式/解释/计算/实验原子单元2～5分；回忆卷逐题分值未知，不声称为官方规则。",
            "standalone_choice_section": False,
            "hierarchy": "paper -> theme_big_question -> printed_question -> atomic_part",
            "hierarchy_counts": {
                "printed_question_count": printed_question_count,
                "atomic_part_count": atomic_part_count,
            },
        },
        "observed_profile_contract": {
            "adapter_version": "observed-profile-adapter/1.0.0",
            "upstream_profile_available": False,
            "fixture_id": "OBSERVED-PROFILE-V1-LOCAL-20260813",
            "preflight_status": "EVIDENCE_PACKAGE_READY_DRAFT_ONLY",
            "publication_allowed": False,
            "source_paths": [STYLE_RULEBOOK_PATH, STRUCTURE_PROFILE_PATH],
        },
        "evidence_sources": [
            {
                "path": SOURCE_CARD_PATH,
                "card_ids": [
                    "HOT-W1-MET-003",
                    "HOT-W1-CGW-002",
                    "HOT-W1-HYD-001",
                    "HOT-W1-CGW-004",
                    "HOT-W1-QDP-001",
                    "HOT-W1-QDP-003",
                ],
                "use": "verified_fact_and_quantitative_boundary_only",
                "prohibited_use": "not_a_formal_question_or_official_rubric",
                "source_kind": "fact_card_collection",
            },
            {
                "path": MCH_SOURCE_EXTRACT_PATH,
                "evidence_ids": [MCH_SOURCE_EXTRACT_ID],
                "use": "machine_verified_claims_and_locator_boundary_only",
                "prohibited_use": "not_primary_measurement_system_performance_formal_question_or_official_rubric",
                "source_kind": "machine_verified_local_pdf_extract",
            }
        ],
        "figure_refs": [FIGURE_ID],
        "themes": themes,
    }
    apply_content_metadata_contract(paper, Path(__file__).resolve().parents[2])
    return paper


def build_week_plan() -> dict:
    return {
        "schema_version": "1.0.0",
        "version_id": VERSION_ID,
        "weekpack_id": "WEEKPACK-GEN-V2-20260813-018",
        "status": "automated_verified_candidate_pending",
        "claim_boundary": "机器候选周包；无真实学生作答，不作个体诊断或实测难度声明。",
        "days": [
            {"day": 1, "focus": "资源循环与电化学", "question_refs": [f"Q{i:02d}" for i in range(1, 9)], "target_minutes": 45, "self_check": "守恒、电极极性、单位"},
            {"day": 2, "focus": "CO₂资源化与数据边界", "question_refs": [f"Q{i:02d}" for i in range(9, 16)], "target_minutes": 45, "self_check": "相态模型、条件、转化率、选择性"},
            {"day": 3, "focus": "有机储氢结构与路线", "question_refs": [f"Q{i:02d}" for i in range(16, 24)], "target_minutes": 45, "self_check": "结构简式、反应类型、载体/系统分母"},
            {"day": 4, "focus": "余氯共享流程题", "question_refs": ["Q24"], "target_minutes": 50, "self_check": "依赖链、滴定计量、条件阈值"},
            {"day": 5, "focus": "量子点结构—性质与废物边界", "question_refs": [f"Q{i:02d}" for i in range(25, 31)], "target_minutes": 40, "self_check": "单位、能量模型、安全边界"},
            {"day": 6, "focus": "跨主题回看", "question_refs": ["Q05", "Q08", "Q11", "Q15", "Q19", "Q24", "Q30"], "target_minutes": 40, "self_check": "只重做错因，不抄答案"},
            {"day": 7, "focus": "60分钟整卷计时", "question_refs": [f"Q{i:02d}" for i in range(1, 31)], "target_minutes": 60, "self_check": "留5分钟核对单位、方程式、选项"},
        ],
    }


def clone_paper() -> dict:
    return deepcopy(build_paper())
