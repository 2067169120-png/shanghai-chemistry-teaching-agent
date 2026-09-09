"""Read-only pre-push checks. Report locations, never potential secret values."""
from __future__ import annotations

import json
import re
import subprocess
from pathlib import Path

ROOT = Path(__file__).resolve().parents[4]
PATTERNS = {
    "provider_key": re.compile(r"\bsk-(?:proj-)?[A-Za-z0-9_-]{24,}"),
    "github_token": re.compile(r"\b(?:ghp_|github_pat_)[A-Za-z0-9_]{25,}"),
    "private_key": re.compile(r"-----BEGIN (?:RSA |EC |OPENSSH )?PRIVATE KEY-----"),
    "literal_api_key": re.compile(r'''["'](?:api_key|apiKey)["']\s*:\s*["']([^"'\r\n]{24,})["']'''),
}
ALLOWED_SUFFIXES = {".py", ".pyw", ".json", ".yaml", ".yml", ".md", ".txt", ".mjs", ".cjs", ".ps1", ".vbs", ".gitignore", ".gitattributes"}
PUBLIC_DEMO_IMAGES = {"docs/screenshots/word-questions.png", "docs/screenshots/word-answers.png", "docs/screenshots/import-preview.png"}


def main():
    result = subprocess.run(["git", "ls-files", "-z", "--cached", "--others", "--exclude-standard"],
                            cwd=ROOT, capture_output=True, check=True)
    paths = sorted(set(result.stdout.decode("utf-8").strip("\0").split("\0")))
    issues = []
    total = 0
    for relative in paths:
        path = ROOT / relative
        if path.is_symlink() or path.is_junction() or not path.is_file():
            issues.append({"file": relative, "kind": "unexpected_file"})
            continue
        raw = path.read_bytes()
        total += len(raw)
        if relative in PUBLIC_DEMO_IMAGES:
            # Only these visually reviewed, synthetic-data UI captures are public.
            # This does not permit uploading source papers or arbitrary images.
            if not raw.startswith(b"\x89PNG\r\n\x1a\n") or len(raw) > 2_000_000:
                issues.append({"file": relative, "kind": "invalid_demo_screenshot"})
            continue
        derived_jsonl = relative.startswith(("knowledge/textbook/", "knowledge/lectures/")) and path.suffix.lower() == ".jsonl"
        if not derived_jsonl and path.suffix.lower() not in ALLOWED_SUFFIXES and path.name not in {".gitignore", ".gitattributes"}:
            issues.append({"file": relative, "kind": "unexpected_type"})
        utf16_script = path.suffix.lower() == ".vbs" and raw.startswith((b"\xff\xfe", b"\xfe\xff"))
        if len(raw) > 2_000_000 or (b"\0" in raw and not utf16_script):
            issues.append({"file": relative, "kind": "large_or_binary"})
        value = raw.decode("utf-16" if utf16_script else "utf-8-sig", errors="replace")
        for kind, pattern in PATTERNS.items():
            for match in pattern.finditer(value):
                token = match.group(1) if kind == "literal_api_key" else match.group()
                synthetic = any(word in token.lower() for word in ("fake", "dummy", "test", "example", "placeholder", "redacted"))
                if synthetic:
                    continue
                if "/tests/" in relative and re.fullmatch(r"(?:sk-)?(?:e2e|[a-z]+)(?:-(?:e2e|[a-z]+))+(?:-[0-9]+|-)?", token, re.IGNORECASE):
                    continue  # Explicit English-word sentinel literals in tests.
                # Constant strings that construct synthetic token fixtures.
                if kind == "literal_api_key" and ("*" in token or " + " in token):
                    continue
                issues.append({"file": relative, "line": value[:match.start()].count("\n") + 1, "kind": kind})
    print(json.dumps({"file_count": len(paths), "total_bytes": total, "issues": issues}, ensure_ascii=False, indent=2))
    return bool(issues)


if __name__ == "__main__":
    raise SystemExit(main())
