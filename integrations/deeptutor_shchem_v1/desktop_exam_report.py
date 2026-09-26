"""Escaped, offline report; no remote scripts, model-authored code or chart numbers."""
from __future__ import annotations
from html import escape
import json
from .desktop_exam_ai import advice_text
from .desktop_exam_data import local_student_advice


def number(v, suffix=''):
    return '—' if v is None else f'{v:.2f}'.rstrip('0').rstrip('.') + suffix


def percent(v):
    return '—' if v is None else number(v*100, '%')


def chart_svg(title, rows, ceiling, unit=''):
    w, h, left = 960, max(160, 56 + len(rows)*34), 190
    lines = [f'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 {w} {h}" role="img" aria-label="{escape(title, quote=True)}">',
             '<style>text{font-family:system-ui,Microsoft YaHei,sans-serif;font-size:15px;fill:#24372f}</style>',
             f'<text x="16" y="23">{escape(title)}</text>']
    for i, (label, value) in enumerate(rows):
        y = 42 + i*34
        lines.append(f'<text x="12" y="{y+19}">{escape(label[:19])}</text>')
        if value is not None:
            length = max(0, value) / max(1, ceiling) * 630
            lines += [f'<rect x="{left}" y="{y}" width="630" height="24" rx="4" fill="#edf3ef"/>',
                      f'<rect x="{left}" y="{y}" width="{length:.2f}" height="24" rx="4" fill="#397d69"/>']
        lines.append(f'<text x="838" y="{y+19}">{escape(number(value,unit))}</text>')
    lines.append('</svg>')
    return ''.join(lines)


def brief(report):
    s=report['overall']
    return (f"考试分析：{report['title']}\n统计范围：{report['scope']}；导入{s['enrolled']}人，有效总分{s['n']}人，"
            f"缺考{s['absent']}人，总分缺失/冲突{s['unavailable']}人。\n"
            f"满分{number(report['maximum'])}，均分{number(s['mean'])}，中位数{number(s['median'])}，"
            f"总体标准差{number(s['sd'])}；自定达标线{number(report['pass_score'])}，达标率{percent(s['pass_rate'])}。\n"
            '以上为本次已提供数据，不据此判断已提分或确定错因。')


def html_report(report, result=None, *, include_names=False):
    def table(headers, rows):
        return '<table><thead><tr>'+''.join('<th>'+escape(str(x))+'</th>' for x in headers)+'</tr></thead><tbody>'+''.join(
            '<tr>'+''.join('<td>'+escape(str(x))+'</td>' for x in row)+'</tr>' for row in rows)+'</tbody></table>'
    pieces=['<!doctype html><html lang="zh-CN"><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">',
            '<title>考试数据分析</title><style>body{font:16px/1.7 system-ui,"Microsoft YaHei",sans-serif;max-width:1100px;margin:30px auto;padding:0 24px;color:#24372f}h1{font-size:28px}h2{margin-top:30px}svg{width:100%;display:block}pre{white-space:pre-wrap;font:inherit;background:#f4f7f5;padding:18px}table{border-collapse:collapse;width:100%;font-size:14px}td,th{padding:9px;border-bottom:1px solid #dce5df;text-align:left}th{background:#edf3ef} .warning{background:#fff7e3;padding:16px} @media print{table{page-break-inside:auto}tr{page-break-inside:avoid}}</style>',
            '<h1>'+escape(report['title'])+'</h1><p>本地计算的可视化面板 · API建议与统计分开展示</p><pre>'+escape(brief(report if include_names else dict(report,scope='当前所选范围（匿名导出）')))+'</pre>']
    pieces.append(chart_svg('成绩分布（满分百分比，区间左闭右开，最后一区间包含满分）',[(d['label'],d['n']) for d in report['distribution']],max((d['n'] for d in report['distribution']),default=1),'人'))
    pieces.append('<h2>班级汇总</h2>'+table(['班级','有效总分人数','均分','中位数'],
                 [(c['class'] if include_names else f'班级{i+1}',c['n'],number(c['mean']),number(c['median'])) for i,c in enumerate(report['classes'])]))
    if report['items']:
        items=sorted(report['items'],key=lambda q:(q['rate'] is None,q['rate'] or 0))
        pieces.append('<h2>逐题表现</h2>'+chart_svg('低得分率题目（最多20项；完整明细见表）',[(q['question'],None if q['rate'] is None else q['rate']*100) for q in items[:20]],100,'%'))
        pieces.append(table(['题号','满分','有效人数','缺失人数（不含缺考）','均分','得分率','主知识点'],
                       [(q['question'],number(q['max_score']),q['n'],q['missing'],number(q['mean']),percent(q['rate']),q['knowledge'] or '待映射') for q in report['items']]))
        pieces.append('<h2>知识点表现</h2><p>按有效作答分值加权：合计得分÷有效作答对应满分；不是心理测量意义的能力值。</p>'+table(
            ['知识点','对应题','有效作答次数','综合得分率'],[(k['label'],'、'.join(k['questions']),k['responses'],percent(k['rate'])) for k in report['knowledge']]))
    pieces.append('<h2>学生复核与练习注意点</h2>'+table(['学生','总分','状态','复核行动'],[
        (s['local_label'] if include_names else s['id'],number(s['total']),'缺考' if s['absent'] else '总分待核对' if s['total'] is None else '有总分',local_student_advice(s,report)) for s in report['students']]))
    pieces += ['<h2>API讲评建议</h2><pre>'+escape(advice_text(result))+'</pre>',
               '<h2>数据问题</h2>'+table(['Excel行','字段','问题'],[(i['row'],i['field'],i['detail']) for i in report['issues']]),
               '<div class="warning">'+ '<br>'.join(escape(w) for w in report['warnings'])+'</div>',
               '<p>文件未加密。'+('包含本机学生/班级标识，请勿公开传播。' if include_names else '学生用临时代号展示；自由文字与试卷内容仍请自行核对隐私。')+'</p></html>']
    return ''.join(pieces)
