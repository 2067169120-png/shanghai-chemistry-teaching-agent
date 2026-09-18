"""Small offline check for one current guide and honest, resolvable local links."""
from __future__ import annotations

from collections import Counter
from pathlib import Path
import re
from urllib.parse import unquote, urlsplit

REQUIRED_ANCHORS = (
    'install', 'features', 'flows', 'home', 'library', 'paper', 'student',
    'preparation', 'exam', 'closure', 'lesson-design', 'templates', 'classroom',
    'works', 'import', 'settings', 'faq', 'verification', 'workflow-status',
    'visual-composition', 'documentation',
)
SNAPSHOT = re.compile(r'^readme[-_]\d+(?:\.\d+)+(?:[-_](?:archive|backup|history))?\.md$', re.I)
LINK = re.compile(r'(?<!!)\[[^\]\n]*\]\(([^)\n]+)\)')
IMAGE = re.compile(r'!\[[^\]\n]*\]\(([^)\n]+)\)')


def archived_readmes(root: Path) -> list[Path]:
    """Main-guide snapshots are banned in root/docs; module guides remain valid."""
    candidates = list(root.iterdir()) + list((root / "docs").rglob("*.md"))
    return sorted(p for p in candidates if p.is_file() and SNAPSHOT.fullmatch(p.name))


def local_target(owner: Path, target: str) -> Path | None:
    """Resolve a Markdown destination, skipping web, fragment and data URLs."""
    target = target.strip().strip('<>')
    parsed = urlsplit(target)
    if parsed.scheme or parsed.netloc or not parsed.path:
        return None
    return (owner.parent / unquote(parsed.path)).resolve()


def check_documentation(root: Path) -> list[str]:
    root = root.resolve()
    errors: list[str] = []
    for path in archived_readmes(root):
        errors.append('Use Git history instead of a versioned root README: ' + str(path.relative_to(root)))
    guide = root / 'README.md'
    if not guide.is_file():
        return errors + ['README.md is missing']
    text = guide.read_text(encoding='utf-8')
    # Code examples are not clickable Markdown links.
    visible = re.sub(r'^```[^\n]*\n.*?^```\s*$', '', text, flags=re.M | re.S)
    anchors = re.findall(r'<a\s+id="([^"]+)"\s*></a>', visible)
    for anchor, count in Counter(anchors).items():
        if count > 1:
            errors.append('Duplicate guide anchor: ' + anchor)
    for anchor in REQUIRED_ANCHORS:
        if anchor not in anchors or '(#' + anchor + ')' not in visible:
            errors.append('Missing guide section or navigation: ' + anchor)
    for target in LINK.findall(visible) + IMAGE.findall(visible):
        if target.startswith('#') and target[1:] not in anchors:
            errors.append('Unresolved guide anchor: ' + target)
        path = local_target(guide, target)
        if path is not None and not path.is_file():
            errors.append('Unresolved guide link: ' + target)
    if '{{VERIFICATION_SUMMARY}}' in text or '{{PERFORMANCE_RESULTS}}' in text:
        errors.append('Guide still contains unpublished result placeholders')
    # Check living status docs too; historical QA files remain unchanged evidence.
    for rel in ('docs/WORKFLOW_STATUS.md', 'docs/roadmaps/audit-followup.md'):
        f = root / rel
        if not f.is_file():
            errors.append('Missing current status: ' + rel)
            continue
        for target in LINK.findall(f.read_text(encoding='utf-8')):
            path = local_target(f, target)
            if path is not None and not path.is_file():
                errors.append('Unresolved status link in ' + rel + ': ' + target)
    # Prevent stale *local* links to removed root snapshots in any current doc.
    for f in [guide, root / 'CHANGELOG.md', *(root / 'docs').rglob('*.md')]:
        if not f.is_file():
            continue
        for target in LINK.findall(f.read_text(encoding='utf-8')):
            path = local_target(f, target)
            if path is not None and path.parent == root and SNAPSHOT.fullmatch(path.name):
                errors.append('Obsolete local history link in ' + str(f.relative_to(root)))
    return errors


def main() -> int:
    root = Path(__file__).resolve().parents[2]
    errors = check_documentation(root)
    if errors:
        print('\n'.join(errors))
        return 1
    print('Current guide, history policy, navigation, images and workflow links: OK')
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
