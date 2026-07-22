"""Fail when publishable project files contain common credential shapes."""

from __future__ import annotations

import re
import subprocess
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
TEXT_SUFFIXES = {
    ".css",
    ".example",
    ".html",
    ".js",
    ".json",
    ".lock",
    ".md",
    ".mjs",
    ".py",
    ".svg",
    ".toml",
    ".ts",
    ".tsx",
    ".txt",
    ".yaml",
    ".yml",
}

SECRET_PATTERNS = {
    "provider secret": re.compile(r"\bsk-(?:lf-)?[A-Za-z0-9_-]{20,}\b"),
    "GitHub token": re.compile(r"\b(?:ghp|github_pat)_[A-Za-z0-9_]{20,}\b"),
    "bearer token": re.compile(r"Bearer\s+[A-Za-z0-9._~+/=-]{20,}", re.IGNORECASE),
    "private key": re.compile(r"BEGIN (?:RSA |EC |OPENSSH )?PRIVATE KEY"),
}

ENV_ASSIGNMENT = re.compile(
    r"^[ \t]*(MISTRAL_API_KEY|LANGFUSE_PUBLIC_KEY|LANGFUSE_SECRET_KEY)[ \t]*="
    r"[ \t]*([^\s#]+)",
    re.MULTILINE,
)
SAFE_PLACEHOLDERS = {"", "...", "changeme", "example", "placeholder"}


def publishable_files() -> list[Path]:
    """Return files Git would publish, falling back to a filesystem walk."""
    result = subprocess.run(
        ["git", "ls-files", "--cached", "--others", "--exclude-standard", "-z"],
        cwd=ROOT,
        check=False,
        capture_output=True,
    )
    if result.returncode == 0:
        names = [name for name in result.stdout.decode("utf-8").split("\0") if name]
        return [ROOT / name for name in names]
    return [path for path in ROOT.rglob("*") if path.is_file() and ".git" not in path.parts]


def main() -> int:
    findings: list[tuple[str, int, str]] = []
    for path in publishable_files():
        if path.suffix.lower() not in TEXT_SUFFIXES:
            continue
        try:
            text = path.read_text(encoding="utf-8")
        except UnicodeDecodeError:
            continue

        relative = path.relative_to(ROOT).as_posix()
        for label, pattern in SECRET_PATTERNS.items():
            for match in pattern.finditer(text):
                findings.append((relative, text.count("\n", 0, match.start()) + 1, label))

        for match in ENV_ASSIGNMENT.finditer(text):
            value = match.group(2).strip().lower()
            is_template = value in SAFE_PLACEHOLDERS or value.startswith("${")
            if not is_template:
                line = text.count("\n", 0, match.start()) + 1
                findings.append((relative, line, f"non-empty {match.group(1)}"))

    if findings:
        for relative, line, label in sorted(set(findings)):
            print(f"{relative}:{line}: {label}")
        print(f"Secret scan failed with {len(set(findings))} finding(s).")
        return 1

    print("Secret scan passed: no credential-shaped values found.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
