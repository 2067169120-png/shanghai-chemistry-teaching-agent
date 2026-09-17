"""Every release keeps an actual complete guide instead of an incremental note."""
from pathlib import Path
import re
ROOT=Path(__file__).resolve().parents[4]

def test_full_guide_keeps_all_daily_pages_and_operating_instructions():
    text=(ROOT/'README.md').read_text(encoding='utf-8')
    for anchor in ('install','features','flows','home','library','paper','student','preparation',
                   'templates','classroom','works','import','settings','faq','verification'):
        assert f'<a id="{anchor}"></a>' in text
        assert f'(#{anchor})' in text
    assert len(text)>8000
    assert '不包含学生业务' in text or '排除学生目录' in text or '学生作答/批次/批改暂存' in text

def test_static_screenshots_exist_and_new_screenshots_have_a_verifier():
    text=(ROOT/'README.md').read_text(encoding='utf-8')
    planned={'work-batch-entry.png','work-batch-members.png','work-batch-wide.png',
             'work-batch-restored.png','work-batch-condition.png','work-batch-compact.png'}
    images=re.findall(r'!\[[^\]]*\]\(([^)]+)\)',text)
    assert len(set(images))>=16
    for image in images:
        assert (ROOT/image).is_file() or (image.startswith('docs/screenshots/v0.1.97/') and Path(image).name in planned)
    assert (ROOT/'README-0.1.96-archive.md').is_file()
