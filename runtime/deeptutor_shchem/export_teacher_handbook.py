"""Build a self-contained, printable scenario quick-reference from app guidance.

Standard-library only. No user data, model calls, JavaScript, remote assets or fonts.
The Markdown teacher chapters remain the detailed manual in the delivery bundle.
"""
from pathlib import Path
import sys

ROOT=Path(__file__).resolve().parents[2]
sys.path.insert(0,str(ROOT))
from html import escape
from integrations.deeptutor_shchem_v1.desktop_teacher_scenarios import SCENARIOS,scenario_html


def build():
    menu=''.join(f'<a href="#{escape(s.key)}">{escape(s.title)}</a>' for s in SCENARIOS)
    sections=''.join(f'<section id="{escape(s.key)}">{scenario_html(s)}</section>' for s in SCENARIOS)
    return '''<!doctype html><html lang="zh-CN"><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>沪上化学智研台 · 教师场景速查</title>
<style>
:root {color-scheme:light}*{box-sizing:border-box}body{margin:0;background:#f3f5f2;color:#243b32;font-family:system-ui,"Microsoft YaHei",sans-serif;font-size:17px;line-height:1.85}
main{max-width:980px;margin:0 auto;padding:36px 24px 64px}header{padding:24px 0}h1{font-size:32px;line-height:1.3;letter-spacing:-.02em}h2{font-size:25px;line-height:1.5}h3{font-size:19px;margin-bottom:8px;color:#24674f}p{margin:8px 0 16px}nav{display:grid;grid-template-columns:repeat(2,minmax(0,1fr));gap:10px;margin:22px 0}nav a{background:white;padding:14px 18px;border:1px solid #dbe4dd;border-radius:10px;color:#225b45;text-decoration:none}a:focus-visible{outline:3px solid #24674f;outline-offset:3px}section{background:white;border:1px solid #dbe4dd;border-radius:14px;padding:24px 28px;margin:24px 0;scroll-margin-top:18px}.notice{padding:16px 20px;border-left:4px solid #6c8369;background:#e8eee5}li{margin:7px 0}footer{margin-top:30px;color:#536458}
@media(max-width:600px){main{padding:18px 16px}h1{font-size:28px}nav{grid-template-columns:1fr}section{padding:18px}body{font-size:16px}}
@media print{body{background:white;color:black}main{max-width:none;padding:0}nav{display:none}section{border:0;border-top:1px solid #aaa;border-radius:0;padding:12px 0;margin:18px 0}h2,h3{break-after:avoid}li{break-inside:avoid}}
</style><main><header><p>沪上化学智研台 / 教师使用手册</p><h1>从今天的一项教学任务开始</h1>
<p>先准备材料，按入口操作，再检查真正保存或导出的结果。可以直接用浏览器打开本文件，也可用浏览器打印。</p>
<p class="notice">v0.1.101 是已发布试用包；“维护源码”步骤不在原 EXE 中。Excel 对比重导仍未完成界面验收，本手册不把它列为可用功能。此文件是场景速查，详细说明见同包 README 与 docs/teacher。</p></header>'''+f'<nav aria-label="场景导航">{menu}</nav>'+'''
<section><h2>第一次打开</h2><p>完整解压试用包，再运行沪上化学智研台.exe。不要只复制EXE、删除_internal或关闭Windows防护。程序不附带个人题库、教材、学生作答、密钥或Office。</p><p>先用一份已核对Word材料或一张同场考试xlsx完成离线流程。只有明确生成新AI内容时才配置模型并确认发送。查看旧结果不需要重新生成。</p><p>实际分页需要Office转换工具，授课前打开最终文件核对。自由文字、图片及PPT备注中的答案和个人信息，需要教师检查。</p></section>'''+sections+'''
<section><h2>保存与故障时的底线</h2><p>备课ZIP不是全业务备份，不包含学生作答、批次、批改暂存及原Word／公众号完整题库。恢复副本不是原图备份。迁移前保留唯一原件，在独立目录恢复核对。</p><p>题篮读失败、文件缺失或其他窗口保存冲突时，先保留材料与输入。不要通过清空资料、覆盖旧记录或反复调用模型来排错。Ctrl+K搜索功能，F1打开场景帮助。</p></section><footer>操作说明不等于模型质量证明、课堂效果评价或已完成验收。教师稿与学生稿分开发放。</footer></main></html>'''


def main():
    destination=Path(sys.argv[1] if len(sys.argv)>1 else 'teacher-qa')
    destination.mkdir(parents=True,exist_ok=True)
    (destination/'teacher-handbook.html').write_text(build(),encoding='utf-8')
    print('Built offline teacher scenario handbook; no private data read.')


if __name__=='__main__':main()
