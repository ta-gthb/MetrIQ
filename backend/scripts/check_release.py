"""Release hygiene gate: secrets, packaging and unsupported claims.

One command answers the questions the audit asks of a release artefact:

* does it contain a secret-bearing file (item 2)?
* does any file contain a credential-like string (items 1 and 2)?
* does a browser asset contain the demonstration password (item 3)?
* does the documentation or UI claim a digital signature that does not exist
  (item 5)?

    python backend/scripts/check_release.py             # the working tree
    python backend/scripts/check_release.py --archive release.zip
    python backend/scripts/check_release.py --git-history

Exits non-zero on the first category that fails, so it works as a CI gate. The
`--git-history` mode walks every commit, which is how the audit asks for the old
credential to be hunted down rather than just removed from the tip.

Deliberate exclusions, all of which would otherwise be false positives:
placeholder values (`<password>`, `change-me`, ...), this file's own pattern
list, test fixtures whose "password" is a two-character dummy, and the
`DEMO_PASSWORD` default, which is a configuration value rather than a secret -
it is exactly the value this check keeps *out* of the browser bundle.
"""

from __future__ import annotations

import argparse
import io
import os
import re
import subprocess
import sys
import tarfile
import zipfile
from dataclasses import dataclass
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
BACKEND_DIR = Path(__file__).resolve().parents[1]

# Directories that hold generated or vendored content, never release source.
SKIP_DIRS = {
    ".git", ".github-cache", "__pycache__", "node_modules", ".venv", "venv",
    ".pytest_cache", ".mypy_cache", ".ruff_cache", "htmlcov", "dist", "build",
    "var", ".idea", ".vscode", "site-packages",
}
# Binary formats whose bytes are not credentials we can read.
SKIP_SUFFIXES = {
    ".pdf", ".png", ".jpg", ".jpeg", ".gif", ".webp", ".tif", ".tiff", ".bmp",
    ".ico", ".svgz", ".woff", ".woff2", ".ttf", ".otf", ".eot", ".zip", ".gz",
    ".tar", ".tgz", ".db", ".sqlite3", ".docx", ".xlsx", ".pyc",
}
MAX_FILE_BYTES = 4 * 1024 * 1024

# Files that must never be part of a release (item 2).
FORBIDDEN_NAMES = {".env", ".npmrc", ".pypirc", "id_rsa", "id_ed25519", "credentials.json"}
FORBIDDEN_SUFFIXES = (".pem", ".key", ".p12", ".pfx", ".jks", ".keystore")
ENV_EXAMPLES = {".env.example", ".env.sample", ".env.template"}

# Values that are obviously not a secret.
PLACEHOLDER = re.compile(
    r"""^(?:
        <[^>]*>                      # <password>, <ref>
      | \*+ | x{3,} | -+ | \.{3,}    # masks
      | \$\{[^}]*\} | %\([^)]*\)s    # template variables
      | change[-_]?me.* | your[-_].* | example.* | placeholder.* | sample.*
      | dummy.* | fake.* | test.* | todo | none | null | nil | unset
      | password | passwd | secret | pass | pw | pwd | token | apikey | api[-_]key
      | postgres | admin | root | user | localhost
    )$""",
    re.IGNORECASE | re.VERBOSE,
)

CREDENTIAL_PATTERNS: list[tuple[str, re.Pattern[str]]] = [
    ("a private key block", re.compile(r"-----BEGIN (?:[A-Z0-9 ]+ )?PRIVATE KEY-----")),
    ("an AWS access key id", re.compile(r"\b(?:AKIA|ASIA)[0-9A-Z]{16}\b")),
    ("a GitHub token", re.compile(r"\b(?:ghp|gho|ghu|ghs|ghr)_[A-Za-z0-9]{36,}\b")),
    ("a Slack token", re.compile(r"\bxox[baprs]-[A-Za-z0-9-]{10,}\b")),
    ("an OpenAI key", re.compile(r"\bsk-(?:proj-|live-)?[A-Za-z0-9_-]{24,}\b")),
    ("a Google API key", re.compile(r"\bAIza[0-9A-Za-z_\-]{35}\b")),
    ("a Stripe live key", re.compile(r"\bsk_live_[0-9A-Za-z]{16,}\b")),
    ("a JWT-shaped token", re.compile(r"\beyJ[A-Za-z0-9_-]{8,}\.[A-Za-z0-9_-]{8,}\.[A-Za-z0-9_-]{8,}\b")),
]
# The password in a connection string, handled separately because a URL needs a
# placeholder check on the password itself rather than on the whole match.
DATABASE_URL = re.compile(
    r"\b(?:postgres(?:ql)?|mysql|mariadb|mongodb(?:\+srv)?|redis|amqp)://"
    r"([^:@/\s]+):([^@\s]{1,64})@",
    re.IGNORECASE,
)

# With credentials enabled, a report that is merely hashed must not be described
# as signed (item 5).
SIGNATURE_CLAIMS = [
    (re.compile(r"\bsigned\s+(?:pdf|report|document)s?\b", re.IGNORECASE), "'signed report'"),
    (re.compile(r"\bdigitally\s+signed\b", re.IGNORECASE), "'digitally signed'"),
]
CLAIM_SCOPE = ("README.md", "frontend", "docs")

# Browser assets must not carry the demonstration password (item 3).
BUNDLE_SCOPE = ("frontend",)

# Files that quote what this check looks for: its own pattern list, and the
# tests that prove each pattern fires. A real secret could hide in them, so keep
# this list to exactly those two names.
SELF_REFERENTIAL = ("check_release", "test_release_hygiene")


@dataclass(frozen=True)
class Finding:
    path: str
    line: int
    message: str

    def __str__(self) -> str:
        where = f"{self.path}:{self.line}" if self.line else self.path
        return f"{where}: {self.message}"


def _placeholder(value: str) -> bool:
    return bool(PLACEHOLDER.match(value.strip()))


def _demo_password() -> str:
    """The seeded demonstration password, read from the same place the app does."""
    try:
        sys.path.insert(0, str(BACKEND_DIR))
        from app.config import Settings

        return Settings().DEMO_PASSWORD or ""
    except Exception:  # pragma: no cover - the check still runs without the app
        return os.environ.get("DEMO_PASSWORD", "MetrIQ@2026")


def demo_password() -> str:
    return _demo_password()


def scan_text(path: str, text: str, *, demo: str | None = None) -> list[Finding]:
    """Credential-shaped strings, forbidden filenames and unsupported claims."""
    findings: list[Finding] = []
    name = Path(path).name

    # `deployment.env` is committed on purpose and holds no secret. Anything
    # else named *.env, plus the usual key material, must never ship (item 2).
    if name != "deployment.env" and (
        name in FORBIDDEN_NAMES
        or name.endswith(".env")
        or (name.startswith(".env.") and name not in ENV_EXAMPLES)
    ):
        findings.append(Finding(path, 0, f"a release must not carry {name!r}"))
    if path.endswith(FORBIDDEN_SUFFIXES):
        findings.append(Finding(path, 0, f"a release must not carry a {Path(path).suffix} file"))

    for number, raw_line in enumerate(text.splitlines(), start=1):
        if any(name in path for name in SELF_REFERENTIAL):
            continue
        for label, pattern in CREDENTIAL_PATTERNS:
            if pattern.search(raw_line):
                findings.append(Finding(path, number, f"looks like {label}"))
        match = DATABASE_URL.search(raw_line)
        if match and not _placeholder(match.group(2)):
            findings.append(Finding(path, number, "a database URL with a real-looking password"))

    if demo and any(path.startswith(prefix) for prefix in BUNDLE_SCOPE):
        for number, raw_line in enumerate(text.splitlines(), start=1):
            if demo in raw_line:
                findings.append(
                    Finding(path, number, "the demonstration password must not be in a browser asset")
                )

    if any(path.startswith(prefix) for prefix in CLAIM_SCOPE):
        for number, raw_line in enumerate(text.splitlines(), start=1):
            for pattern, label in SIGNATURE_CLAIMS:
                if pattern.search(raw_line):
                    findings.append(
                        Finding(path, number, f"{label} overstates the assurance this build provides")
                    )
    return findings


def _read(path: Path) -> str | None:
    try:
        if path.stat().st_size > MAX_FILE_BYTES:
            return None
        return io.open(path, encoding="utf-8", newline="").read()
    except (UnicodeDecodeError, OSError):
        return None


def ignored_paths(root: Path) -> tuple[set[str], set[str]]:
    """Files and directories git ignores, relative to `root`.

    A tree scan skips them because they cannot be committed. They can still
    travel in a ZIP or a Docker build context, which is why `--everything`
    exists - and why `.dockerignore` has to name them too.
    """
    try:
        completed = subprocess.run(
            ["git", "ls-files", "--others", "--ignored", "--exclude-standard", "--directory"],
            cwd=str(root), capture_output=True,
        )
    except OSError:  # pragma: no cover - git missing
        return set(), set()
    if completed.returncode != 0:
        return set(), set()
    files: set[str] = set()
    directories: set[str] = set()
    for line in completed.stdout.decode("utf-8", "replace").splitlines():
        entry = line.strip().strip("/")
        if not entry:
            continue
        (directories if line.rstrip().endswith("/") else files).add(entry)
    return files, directories


def scan_tree(root: Path = REPO_ROOT, *, include_ignored: bool = False) -> list[Finding]:
    demo = demo_password()
    findings: list[Finding] = []
    ignored_files, ignored_dirs = (set(), set()) if include_ignored else ignored_paths(root)
    for directory, dirnames, filenames in os.walk(root):
        relative_dir = Path(directory).relative_to(root).as_posix()
        prefix = "" if relative_dir == "." else relative_dir + "/"
        dirnames[:] = [
            name for name in dirnames
            if name not in SKIP_DIRS and prefix + name not in ignored_dirs
        ]
        for filename in filenames:
            path = Path(directory) / filename
            relative = path.relative_to(root).as_posix()
            if relative in ignored_files or path.suffix.lower() in SKIP_SUFFIXES:
                continue
            text = _read(path)
            if text is None:
                continue
            findings.extend(scan_text(relative, text, demo=demo))
    return findings


def scan_archive(archive: Path) -> list[Finding]:
    demo = demo_password()
    findings: list[Finding] = []
    if not archive.is_file():
        return [Finding(str(archive), 0, "archive not found")]
    if zipfile.is_zipfile(archive):
        with zipfile.ZipFile(archive) as bundle:
            for info in bundle.infolist():
                if info.is_dir():
                    continue
                text = _member_text(bundle.read(info), info.filename)
                if text is None:
                    continue
                findings.extend(scan_text(info.filename, text, demo=demo))
    elif tarfile.is_tarfile(archive):
        with tarfile.open(archive) as bundle:
            for info in bundle.getmembers():
                if not info.isfile():
                    continue
                handle = bundle.extractfile(info)
                text = _member_text(handle.read() if handle else b"", info.name)
                if text is None:
                    continue
                findings.extend(scan_text(info.name, text, demo=demo))
    else:
        findings.append(Finding(str(archive), 0, "not a zip or tar archive"))
    return findings


def _member_text(payload: bytes, name: str) -> str | None:
    if len(payload) > MAX_FILE_BYTES or Path(name).suffix.lower() in SKIP_SUFFIXES:
        return None
    try:
        return payload.decode("utf-8")
    except UnicodeDecodeError:
        return None


def scan_git_history(limit_bytes: int = 96 * 1024 * 1024) -> list[Finding]:
    """Every line every commit ever added.

    A credential that was committed and later deleted is still in the objects
    until history is rewritten, so the tip is not enough (audit item 1).
    """
    try:
        completed = subprocess.run(
            ["git", "log", "-p", "--all", "--no-color", "--unified=0"],
            cwd=str(REPO_ROOT), capture_output=True,
        )
    except OSError as exc:  # pragma: no cover - git missing
        return [Finding("git history", 0, f"could not read git history: {exc}")]
    if completed.returncode != 0:
        return [Finding("git history", 0, completed.stderr.decode("utf-8", "replace").strip())]
    blob = completed.stdout.decode("utf-8", "replace")
    if len(blob) > limit_bytes:
        blob = blob[:limit_bytes]

    findings: list[Finding] = []
    commit = "?"
    for raw_line in blob.splitlines():
        if raw_line.startswith("commit "):
            commit = raw_line.split(" ", 1)[1].strip()[:12]
            continue
        if not raw_line.startswith("+"):
            continue
        body = raw_line[1:]
        for label, pattern in CREDENTIAL_PATTERNS:
            if pattern.search(body):
                findings.append(Finding(f"git history @{commit}", 0, f"looks like {label}"))
        match = DATABASE_URL.search(body)
        if match and not _placeholder(match.group(2)):
            findings.append(Finding(f"git history @{commit}", 0, "a database URL with a real-looking password"))
    return findings


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Fail if a release would ship a secret or an unsupported claim")
    parser.add_argument("--root", default=str(REPO_ROOT), help="directory to scan (default: the repository)")
    parser.add_argument("--archive", help="scan a release archive instead of a directory")
    parser.add_argument("--git-history", action="store_true", help="also scan every commit")
    parser.add_argument("--quiet", action="store_true", help="only print the outcome")
    parser.add_argument(
        "--everything", action="store_true",
        help="include files git ignores; use it to check a build context, not a checkout",
    )
    args = parser.parse_args(sys.argv[1:] if argv is None else argv)

    findings: list[Finding] = []
    scope = f"archive {args.archive}" if args.archive else f"tree {args.root}"
    if args.archive:
        findings.extend(scan_archive(Path(args.archive)))
    else:
        findings.extend(scan_tree(Path(args.root), include_ignored=args.everything))
    if args.git_history:
        findings.extend(scan_git_history())

    if not args.quiet:
        print(f"  scanned {scope}" + (" and git history" if args.git_history else ""))
    if findings:
        print(f"  [!!] {len(findings)} problem(s) would ship:")
        for finding in findings[:40]:
            print(f"        - {finding}")
        if len(findings) > 40:
            print(f"        ... and {len(findings) - 40} more")
        return 1
    print("  [ok] no secret, no credential-like string and no unsupported signature claim")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())