"""Offline teacher tasks, shared by the help renderer and its search.

These are instructions, never completion state or automated teaching decisions.
No private records, model configuration, filesystem reads or network calls.
"""
from dataclasses import dataclass
from html import escape


@dataclass(frozen=True)
class TeacherScenario:
    key: str
    title: str
    route: str
    destination: str
    materials: str
    steps: tuple[str, ...]
    output: str
    checks: tuple[str, ...]
    caution: str
    guide: str
    keywords: str = ''
    availability: str = 'v0.1.101已有操作；具体内容仍需教师核对。'


SCENARIOS = (
    TeacherScenario('lesson', '明天要讲一节新课', 'preparation', '备课 → 教学环节',
        '课题、授课对象、课时、学习目标，以及实际要用的教材节选或完整例题。',
        ('填写教学要求与材料，先保存草稿；需要结构参考时预览教学模板。',
         '进入教学环节，逐项安排学生任务、预计用时、预期产出与评价依据。',
         '已有完成初稿可从“从已有初稿加入…”预览并勾选；不覆盖手写环节。',
         '保存后生成教案、PPT和学习单，核对实际文件再导出所选版本。'),
        '可重开的备课草稿，以及教案DOCX、可编辑PPTX、学生学习单DOCX。',
        ('每个目标都有相应任务和检查依据。', '学生学习单没有教师答案。', '实际课件、公式与图片可以阅读。'),
        'PPT讲者备注可能含答案；初稿和材料引用不保证复杂原版面无损。新生成AI稿仍需确认发送。',
        'docs/teacher/lesson-and-paper.md#lesson', '教案 备课 课件 PPT 学习单 教学设计 模板 初稿'),
    TeacherScenario('paper', '给班级出一份练习卷', 'library', '题库 → 选题篮 → 组卷',
        '已导入并核对的完整题目、练习目标、预计用时和分值安排。',
        ('按来源、教材章节或知识点找题，打开题面与答案后再加入题篮。',
         '核对公共材料与依赖条件；从题篮进入组卷安排题序和配分。',
         '逐页检查学生版、教师版实际分页，再确认并导出。'),
        '学生版和教师版DOCX、PDF；原题文件保留。',
        ('两版题号对应，图中旧号与跨题引用已核对。', '公共材料、公式和作答区域完整。', '学生版不含答案，最终PDF可以打印。'),
        '实际分页需要本机Office转换工具；快速预览不等于最终文件已验收。',
        'docs/teacher/lesson-and-paper.md#paper', '打印 试卷 出卷 组卷 练习 作业 布置 题目 答案'),
    TeacherScenario('grading', '复核一份作答或一批作业', 'student', '学生分析 → 同屏批改／作业批次',
        '学生原作答、对应原题与公共材料、参考答案和真实满分。',
        ('打开已有分析，进入“打开作答与评分（同屏批改）”。',
         '对照原页填写分数与理由，点击“记录本题评分”；诊断另行核对。',
         '多人同一作业可建作业批次，明确选择已有作答，逐人复核。',
         '返回或换人前处理暂存；缺页、未做等实际情况独立记录。'),
        '教师正式评分、诊断与实际情况记录；未完成编辑可在批次暂存。',
        ('已点击正式记录并确认成功。', '暂存与AI建议未混入正式成绩。', '缺失、未批和零分没有混淆。'),
        '批次是已有作答的复核，不是一键自动分析全班。异常退出前尚未落盘的输入可能丢失。',
        'docs/teacher/assessment.md#grading', '批改 评分 学生 错题 作业批次 同屏 暂存'),
    TeacherScenario('exam', '月考后决定先讲什么', 'student', '学生分析 → 考试分析（Excel）',
        '同场考试的普通xlsx成绩表；有逐题成绩时准备真实题号、满分及知识点映射。',
        ('打开考试分析，导入并核对工作表、表头、学生、总分及逐题列。',
         '先处理数据核对中的缺失、重复标识和总分冲突。',
         '确认班级范围与有效人数，结合原题和原作答安排讲评顺序。',
         '需要AI讲评时再添加试卷并确认发送；保存分析并导出离线报告。'),
        '本地统计快照与图表、待核对问题、离线HTML报告，以及可选讲评草稿。',
        ('有效人数、范围与分母正确。', '没有把空白、缺考当作零分。', '结论能回到原作答核实。'),
        '只有总分不能定位逐题失分。匿名统计不会擦除自由文字或图片里的个人信息。',
        'docs/teacher/assessment.md#exam', '月考 考试 Excel 成绩 讲评 分析 统计 xlsx'),
    TeacherScenario('followup', '安排复练，记录真实结果', 'student', '学生分析 → 考试分析 → 复练与复测',
        '可核对的考试得分、明确学生、一个可检查目标、计划日期和真实题库中的题目。',
        ('选择原考试题目与学生，写出复练目标和日期。',
         '核对真实题库的完整题目，再回任务关联、预览并导出练习卷。',
         '维护源码可保存独立题集，再用“整理题集与目标”调整顺序和目标。',
         '维护源码改分后先“核对改分影响”；实际完成后记录日期、满分与得分。'),
        '有来源、有目标的任务、练习卷与实际复测记录。',
        ('任务和记录能重开，原题文件仍保留。', '变化后的题目或成绩依据已重新核对。', '未复测不写成已提高。'),
        '不同试卷不直接相减证明提分。旧试用版任务仍依赖题篮；Excel对比重导尚非可用功能。',
        'docs/teacher/assessment.md#followup', '复练 复测 改分 更正 过期 独立题集',
        '基础复练在v0.1.101；独立题集和改分核对仅在后续维护源码。'),
    TeacherScenario('save', '继续工作，交付或备份', 'mywork', '我的备课 → 作品、版本与备份',
        '要保留的草稿和明确输出版本；可写目录及另行保管的原始资料。',
        ('保存草稿，在“我的备课”按作品名或课题找到并重开。',
         '交同事或学生时导出明确版本的独立成品，分开教师稿与学生稿。',
         '迁移前在备份窗口逐项核对范围和缺失清单，先恢复到独立目录。'),
        '可重开的草稿、所选版本成品与明确范围的备份，而不是全业务自动迁移。',
        ('草稿重开后材料和版本正确。', '成品逐个打开，分发前核对答案与隐私。', '原题、学生资料和唯一原件仍妥善保管。'),
        '备课ZIP不包含学生作答、批次、批改暂存及原Word／公众号完整题库；Office副本修改不自动回写。',
        'docs/teacher/save-and-support.md#save', '保存 找回 备份 恢复 迁移 同事 导出 草稿'),
)
ALLOWED_ROUTES = frozenset({'preparation', 'library', 'student', 'mywork'})


def search_scenarios(query: str) -> tuple[TeacherScenario, ...]:
    words = str(query).casefold().split()
    return tuple(s for s in SCENARIOS if all(word in (
        s.title + ' ' + s.keywords + ' ' + s.destination).casefold() for word in words))


def checklist_text(s: TeacherScenario) -> str:
    return '\n'.join((s.title, '入口：' + s.destination, '适用范围：' + s.availability,
        '\n准备什么：' + s.materials, '\n怎么做：',
        *(f'{i + 1}. {line}' for i, line in enumerate(s.steps)),
        '\n得到什么：' + s.output, '\n完成前逐项核对：',
        *('[ ] ' + line for line in s.checks), '\n注意：' + s.caution,
        '\n这是操作清单，不是自动验收结果或已保存的工作记录。'))


def scenario_html(s: TeacherScenario) -> str:
    """Only escaped static guidance, no images, remote resources or raw HTML input."""
    esc = escape
    steps = ''.join('<li>' + esc(t) + '</li>' for t in s.steps)
    checks = ''.join('<li>' + esc(t) + '</li>' for t in s.checks)
    return (f'<h2>{esc(s.title)}</h2><p><b>入口：</b>{esc(s.destination)}</p>'
        f'<p>{esc(s.availability)}</p><h3>准备什么</h3><p>{esc(s.materials)}</p>'
        f'<h3>怎么做</h3><ol>{steps}</ol><h3>得到什么</h3><p>{esc(s.output)}</p>'
        f'<h3>完成前核对</h3><ul>{checks}</ul><p><b>注意：</b>{esc(s.caution)}</p>')
