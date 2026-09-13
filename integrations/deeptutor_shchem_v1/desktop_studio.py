"""Offline teaching starters and classroom tools; no model or question-bank writes."""
from __future__ import annotations

import math
import random
import re
import time
from copy import deepcopy
from dataclasses import dataclass
from typing import Callable


@dataclass(frozen=True)
class TeachingTemplate:
    key: str
    title: str
    subtitle: str
    category: str
    route: str
    objective: str
    sequence: str
    tags: tuple[str, ...]
    periods: int = 1


TEMPLATES = (
    TeachingTemplate("concept", "概念新授课", "从现象出发，让学生建立解释工具", "新授", "新授",
        "用学科语言解释一个现象；根据证据区分相近概念；完成一次独立迁移。",
        "起点诊断 → 观察与追问 → 建立概念 → 完整例题 → 独立练习 → 返回起点解释。\n学生页先呈现材料与问题，结论和答案后置；教案记录预期误区、反馈和用时。",
        ("概念辨析", "证据推理", "讲练结合")),
    TeachingTemplate("experiment", "实验探究课", "观察、设计、解释，而不只是播放装置", "实验", "实验",
        "说明实验要解决的问题；识别变量与对照；用观察结果支持结论并指出限制。",
        "提出问题 → 预测与依据 → 设计与安全条件 → 观察记录 → 证据解释 → 改进与迁移。\n装置图保留仪器连接、液面、气流和必要条件。已有记录表、答题区不重复绘制。",
        ("实验设计", "变量控制", "装置读图")),
    TeachingTemplate("review", "专题复习课", "用对比和变式串联零散知识", "复习", "复习",
        "建立本专题的知识关系；区分模型适用条件；在改变条件的任务中迁移方法。",
        "短诊断 → 知识关系图 → 主讲题 → 条件对比 → 独立变式 → 错因归纳 → 出口检测。\n每道题解释选用理由，只引用实际题库身份，完整保留公共材料。",
        ("知识网络", "条件对比", "分层练习")),
    TeachingTemplate("feedback", "试卷讲评课", "先诊断错因，再选择值得讲的题", "讲评", "讲评",
        "从真实作答定位具体错因；修正条件、计算或表达；在同目标不同材料的题上复测。",
        "真实作答诊断 → 共性错因 → 完整原题讲评 → 学生订正 → 迁移练习 → 复测安排。\n没有学生作答时，只给诊断问题，不虚构班级得分或错误比例。",
        ("错因分析", "规范表达", "复练")),
    TeachingTemplate("evidence", "情境与数据课", "把材料变成学生可以推理的问题", "探究", "热点",
        "从实际材料中提取有效信息；区分已知、假设和结论；判断证据能支持到什么程度。",
        "材料来源与时间 → 阅读任务 → 图表取证 → 学科解释 → 反例或限制 → 迁移任务。\n不编造实验数据；热点需要实际来源，不把新材料等同于新知识负担。",
        ("图表分析", "情境命题", "信息提取")),
    TeachingTemplate("unit", "单元整体备课", "先定学习进阶，再编排两课时", "单元", "复习",
        "围绕单元核心问题形成连续学习进阶；让每课时的学习产出支持下一课时。",
        "课时一：起点诊断 → 核心概念与证据 → 主讲例题 → 形成性评价。\n课时二：回顾上课产出 → 条件变化与迁移 → 综合任务 → 单元评价与补练。\n逐课时列目标、任务、产出、反馈与时间，不把两课时压成一串题目。",
        ("两课时", "学习进阶", "教学评一致"), periods=2),
)


def get_template(key: str) -> TeachingTemplate:
    for template in TEMPLATES:
        if template.key == key:
            return template
    raise ValueError("未找到教学模板。")


def search_templates(query: str = "", category: str = "全部") -> tuple[TeachingTemplate, ...]:
    words = query.casefold().split()
    return tuple(item for item in TEMPLATES
                 if (category == "全部" or item.category == category)
                 and all(word in " ".join((item.title, item.subtitle, *item.tags)).casefold()
                         for word in words))


def template_brief(payload: dict, key: str, *, topic: str = "", audience: str = "") -> dict:
    """Append a reusable starter without replacing source material or prior work."""
    template = get_template(key)
    result = deepcopy(payload)
    result["topic"] = result.get("topic") or topic.strip()
    result["audience"] = result.get("audience") or audience.strip()
    advanced = result.setdefault("advanced", {})
    marker = f"【教学模板：{template.title}】"
    old = advanced.get("template_and_delivery", "")
    if marker not in old:
        advanced["template_and_delivery"] = "\n\n".join(filter(None, (
            old, marker + "\n" + template.sequence,
        )))
    result["objective"] = result.get("objective") or template.objective
    if not payload.get("topic") and not payload.get("materials"):
        result["lesson_route"] = template.route
        result["timing"] = {"periods": template.periods, "minutes_per_period": 40}
    return result


def parse_roster(text: str) -> list[str]:
    entries = [item.strip() for item in re.split(r"[\n,，;；]+", text) if item.strip()]
    if not entries or len(entries) > 300:
        raise ValueError("请输入1至300个学号或代号，每行一个；同名请用不同学号。")
    if any(len(entry) > 60 for entry in entries) or len(set(entries)) != len(entries):
        raise ValueError("代号重复或过长，请用唯一学号区分。")
    return entries


def make_groups(entries: list[str], count: int, rng=None) -> list[list[str]]:
    if not 1 <= count <= len(entries) or len(set(entries)) != len(entries):
        raise ValueError("分组数应在1与人数之间，学生代号不能重复。")
    shuffled = list(entries)
    (rng or random.SystemRandom()).shuffle(shuffled)
    return [shuffled[index::count] for index in range(count)]


class NoRepeatPicker:
    def __init__(self, entries: list[str], rng=None):
        self.remaining = list(entries)
        self.rng = rng or random.SystemRandom()

    def draw(self) -> str | None:
        if not self.remaining:
            return None
        return self.remaining.pop(self.rng.randrange(len(self.remaining)))


class Countdown:
    def __init__(self, seconds: float = 300, clock: Callable[[], float] = time.monotonic):
        self.clock = clock
        self.deadline: float | None = None
        self.saved = 0.
        self.reset(float(seconds))

    @property
    def seconds(self) -> float:
        return max(0., self.deadline - self.clock()) if self.deadline is not None else self.saved

    def start(self) -> None:
        if self.deadline is None and self.saved > 0:
            self.deadline = self.clock() + self.saved

    def pause(self) -> None:
        self.saved = self.seconds
        self.deadline = None

    def reset(self, seconds: float) -> None:
        if not math.isfinite(seconds) or seconds < 0:
            raise ValueError("计时时长必须是非负数。")
        self.saved = seconds
        self.deadline = None

    def label(self) -> str:
        value = math.ceil(self.seconds)
        return f"{value // 60:02d}:{value % 60:02d}"


def equilibrium_step(a: float, total: float, k_forward: float, k_reverse: float, dt: float) -> tuple[float, float]:
    """Exact step for a closed, first-order A <=> B teaching model.

    Parameters are arbitrary model units, not measured chemical constants.
    da/dt = -kf*a + kr*(total-a). Total concentration is conserved.
    """
    values = (a, total, k_forward, k_reverse, dt)
    if any(not math.isfinite(v) or v < 0 for v in values) or a > total:
        raise ValueError("模型参数必须非负有限，且A不能超过总量。")
    rate = k_forward + k_reverse
    if rate == 0:
        return a, total - a
    equilibrium = total * k_reverse / rate
    next_a = equilibrium + (a - equilibrium) * math.exp(-rate * dt)
    return next_a, total - next_a
