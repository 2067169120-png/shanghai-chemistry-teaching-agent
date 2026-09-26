from dataclasses import replace
from pathlib import Path
import pytest
from integrations.deeptutor_shchem_v1.desktop_teacher_scenarios import (
    SCENARIOS, ALLOWED_ROUTES, search_scenarios, checklist_text, scenario_html)

ROOT=Path(__file__).resolve().parents[4]


def test_six_complete_tasks_only_route_to_existing_workspaces():
    assert len(SCENARIOS)==6 and len({s.key for s in SCENARIOS})==6
    for s in SCENARIOS:
        assert s.route in ALLOWED_ROUTES and s.materials and s.steps and s.output and s.checks
        assert '准备什么' in scenario_html(s) and '得到什么' in scenario_html(s)
        path,anchor=s.guide.split('#')
        assert f'id="{anchor}"' in (ROOT/path).read_text(encoding='utf-8')


@pytest.mark.parametrize('query,key',[('打印','paper'),('讲评','exam'),('改分','followup'),
    ('备份','save'),('批改','grading'),('PPT','lesson'),('月考 Excel','exam')])
def test_teacher_words_find_the_right_task(query,key):
    assert key in {s.key for s in search_scenarios(query)}


def test_empty_search_and_no_match_are_explicit():
    assert search_scenarios('')==SCENARIOS
    assert search_scenarios('a-scenario-that-does-not-exist')==()


def test_copy_is_an_instruction_not_a_fake_completion_record():
    for s in SCENARIOS:
        text=checklist_text(s)
        assert '[ ] ' in text and '不是自动验收结果' in text
        assert s.availability in text and s.caution in text


def test_help_html_does_not_interpret_markup_or_create_remote_resources():
    s=replace(SCENARIOS[0],title='<img src="https://invalid.example/tracker">',
              materials='<script>bad()</script>')
    text=scenario_html(s)
    assert '<img' not in text and '<script' not in text
    assert '&lt;script&gt;' in text


def test_unreleased_work_is_not_misrepresented_as_available_in_old_exe():
    s=next(s for s in SCENARIOS if s.key=='followup')
    assert '维护源码' in s.availability and '对比重导尚非可用' in s.caution
    assert '题篮' in s.caution
