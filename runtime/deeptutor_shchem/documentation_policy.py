"""Validate a compact teacher entry point and reachable, task-specific chapters.

Keep release/source boundaries, legacy bookmarks and no README snapshots. Size
floors and screenshot quotas are intentionally replaced by user-task coverage.
"""
from collections import Counter
from pathlib import Path
import re
from urllib.parse import unquote, urlsplit

REQUIRED_ANCHORS = ('install', 'features', 'flows', 'home', 'library', 'paper', 'student',
    'preparation', 'exam', 'closure', 'lesson-design', 'templates', 'classroom', 'works',
    'import', 'settings', 'faq', 'verification', 'workflow-status', 'visual-composition', 'documentation')
ENTRY_ANCHORS = ('preparation', 'paper', 'student', 'exam', 'closure', 'works')
TEACHER_CHAPTERS = {
    'docs/teacher/getting-started.md': ('install', 'materials', 'model', 'office', 'privacy'),
    'docs/teacher/lesson-and-paper.md': ('lesson', 'paper', 'classroom'),
    'docs/teacher/assessment.md': ('grading', 'exam', 'followup', 'correction'),
    'docs/teacher/save-and-support.md': ('save', 'backup', 'problems'),
}
SNAPSHOT = re.compile(r'^readme[-_]\d+(?:\.\d+)+(?:[-_](?:archive|backup|history))?\.md$', re.I)
LINK = re.compile(r'(?<!!)\[[^\]\n]*\]\(([^)\n]+)\)')
IMAGE = re.compile(r'!\[[^\]\n]*\]\(([^)\n]+)\)')
ANCHOR = re.compile(r'<a\s+id=[\"\']([^\"\']+)[\"\']\s*>', re.I)


def archived_readmes(root):
    root = Path(root)
    candidates = list(root.iterdir()) + list((root / 'docs').rglob('*.md'))
    return sorted(p for p in candidates if p.is_file() and SNAPSHOT.match(p.name))


def local_target(owner, target):
    parsed = urlsplit(target.strip().strip('<>'))
    if parsed.scheme or parsed.netloc or not parsed.path:
        return None
    return (Path(owner).parent / unquote(parsed.path)).resolve()


def _visible(text):
    return re.sub(r'```.*?```', '', text, flags=re.S)


def _anchors(text):
    result = set(ANCHOR.findall(_visible(text)))
    counts = Counter()
    for title in re.findall(r'^#{1,6}\s+(.+)$', _visible(text), re.M):
        slug = re.sub(r'[^\w\- ]', '', title.lower()).strip().replace(' ', '-')
        number = counts[slug]; counts[slug] += 1
        result.add(slug if not number else f'{slug}-{number}')
    return result


def _links(root, owner, text, errors):
    for target in LINK.findall(_visible(text)) + IMAGE.findall(_visible(text)):
        parsed = urlsplit(target.strip().strip('<>'))
        if parsed.scheme or parsed.netloc:
            continue
        local = local_target(owner, target) or owner
        try:
            local.relative_to(root)
        except ValueError:
            errors.append('Guide link escapes repository: ' + target); continue
        if not local.is_file():
            errors.append('Unresolved guide link: ' + target); continue
        if parsed.fragment and local.suffix.lower() == '.md':
            if unquote(parsed.fragment) not in _anchors(local.read_text(encoding='utf-8')):
                errors.append('Unresolved guide fragment: ' + target)


def check_documentation(root):
    root = Path(root).resolve()
    errors = ['Archived root README is not allowed: ' + str(p.relative_to(root))
              for p in archived_readmes(root)]
    readme = root / 'README.md'
    if not readme.is_file():
        return errors + ['Missing current README.md']
    text = readme.read_text(encoding='utf-8')
    visible = _visible(text)
    explicit = ANCHOR.findall(visible)
    for anchor, count in Counter(explicit).items():
        if count > 1: errors.append('Duplicate guide anchor: ' + anchor)
    for anchor in REQUIRED_ANCHORS:
        if anchor not in explicit: errors.append('Missing guide anchor: ' + anchor)
    for anchor in ENTRY_ANCHORS:
        if '(#' + anchor + ')' not in visible: errors.append('Missing teacher scenario entry: ' + anchor)
    if len(text) > 9000 or len(text.splitlines()) > 180:
        errors.append('Teacher README exceeds the compact entry-point budget')
    if len(IMAGE.findall(visible)) > 2:
        errors.append('Move detailed screenshots to the relevant teacher chapter')
    for token in ('维护源码', 'v0.1.101', '学生作答', '完整题库', '准备', '完成标志'):
        if token not in visible: errors.append('Missing teacher boundary or task guidance: ' + token)
    for placeholder in ('{{VERIFICATION_SUMMARY}}', '{{PERFORMANCE_RESULTS}}'):
        if placeholder in text: errors.append('Unexpanded release placeholder: ' + placeholder)
    _links(root, readme, text, errors)
    for filename, anchors in TEACHER_CHAPTERS.items():
        owner = root / filename
        if not owner.is_file():
            errors.append('Missing teacher chapter: ' + filename); continue
        if filename not in text:
            errors.append('Teacher chapter not reachable from README: ' + filename)
        body = owner.read_text(encoding='utf-8')
        for anchor in anchors:
            if anchor not in _anchors(body): errors.append(f'Missing teacher section: {filename}#{anchor}')
        if '../../README.md' not in body: errors.append('Teacher chapter lacks return link: ' + filename)
        _links(root, owner, body, errors)
    for filename in ('docs/WORKFLOW_STATUS.md', 'docs/roadmaps/audit-followup.md', 'docs/maintainer/README.md'):
        owner = root / filename
        if not owner.is_file():
            errors.append('Missing living documentation: ' + filename); continue
        _links(root, owner, owner.read_text(encoding='utf-8'), errors)
    # Links to historical versions hosted on GitHub remain valid; local copies do not.
    owners = [readme, root / 'CHANGELOG.md'] + list((root / 'docs').rglob('*.md'))
    for owner in owners:
        if not owner.is_file(): continue
        for target in LINK.findall(_visible(owner.read_text(encoding='utf-8'))):
            local = local_target(owner, target)
            if local is not None and SNAPSHOT.match(local.name):
                errors.append('Obsolete local README snapshot link: ' + target)
    return errors


if __name__ == '__main__':
    errors = check_documentation(Path(__file__).resolve().parents[2])
    if errors:
        print('\n'.join(errors)); raise SystemExit(1)
    print('Teacher scenarios, compact README, chapters, images, history and version boundaries: OK')
