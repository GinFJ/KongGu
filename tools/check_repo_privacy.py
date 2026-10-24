"""Block tracked files that leak local machine paths, account names or contact details.

Run before every commit and inside the release build:

    .venv\\Scripts\\python.exe tools\\check_repo_privacy.py
    .venv\\Scripts\\python.exe tools\\check_repo_privacy.py --all-history

Exit code 1 means sensitive content was found; nothing must be committed in that state.
"""

from __future__ import annotations

import argparse
import getpass
import re
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SELF = Path(__file__).resolve()
BINARY_SUFFIXES = {
    ".pdf", ".png", ".jpg", ".jpeg", ".webp", ".gif", ".ico", ".exe", ".dll",
    ".zip", ".7z", ".woff", ".woff2", ".ttf", ".otf", ".mp4", ".webm",
    ".pdiparams", ".pdmodel", ".onnx", ".bin", ".param", ".npy", ".pkl",
    ".docx", ".doc", ".xlsx", ".xls", ".pptx", ".ppt", ".odt", ".ods",
}
# 版本号/包名也会出现 `name@1.2.3`，邮箱域名必须以字母开头，避免把依赖版本当邮箱。
EMAIL = re.compile(r"\b[\w.+-]+@[a-zA-Z][\w-]*\.[a-zA-Z]{2,}\b", re.IGNORECASE)
PLACEHOLDER_DOMAINS = ("example.com", "example.org", "example.net", "test.invalid")
# 占位用户/系统公共目录不算泄漏：<user>、%USERPROFILE%、example、user、member、Public。
PLACEHOLDER_USER = r"(?!<|%|\$|example|user\b|member\b|public\b|someone\b)"
PATTERNS = [
    ("local-user-path", re.compile(rf"(?i)[a-z]:[\\/]users[\\/]{PLACEHOLDER_USER}[^\\/\s'\"`]+")),
    ("home-path", re.compile(r"(?i)(?<![\w-])(/home/|/users/)(?!<|%|\$|example|member\b|user\b)[a-z0-9._-]+")),
    # 前后必须是非单词字符：避免把 sha256 校验和里的数字片段当成手机号。
    ("phone", re.compile(r"(?<![\w-])1[3-9]\d{9}(?![\w-])")),
    ("token", re.compile(r"(?i)\b(ghp_[a-z0-9]{20,}|github_pat_[a-z0-9_]{20,}|sk-[a-z0-9]{20,})\b")),
]
SKIP_PATHS = {
    # 只跳过模式定义自身；测试文件必须参与扫描，防止真实账户名混进测试数据。
    "tools/check_repo_privacy.py",
}


def _tracked_files() -> list[Path]:
    output = subprocess.run(
        ["git", "ls-files", "-z"], cwd=ROOT, capture_output=True, check=True
    ).stdout.decode("utf-8")
    return [ROOT / item for item in output.split("\0") if item]


def iter_lines(path: Path):
    if path.suffix.lower() in BINARY_SUFFIXES:
        return
    try:
        text = path.read_text(encoding="utf-8", errors="ignore")
    except OSError:
        return
    for number, line in enumerate(text.splitlines(), start=1):
        yield number, line


def _email_findings(line: str) -> bool:
    for match in EMAIL.finditer(line):
        domain = match.group(0).split("@", 1)[1].lower()
        if not domain.endswith(PLACEHOLDER_DOMAINS):
            return True
    return False


def scan(paths: list[Path], account: str | None = None) -> list[tuple[str, int, str, str]]:
    """Return (path, line, kind, sample) for every suspicious line."""

    if account is None:
        account = getpass.getuser() or ""
    findings: list[tuple[str, int, str, str]] = []
    for path in paths:
        try:
            relative = str(path.relative_to(ROOT))
        except ValueError:
            relative = str(path)
        if relative.replace("\\", "/") in SKIP_PATHS:
            continue
        for number, line in iter_lines(path):
            sample = line.strip()[:120]
            if account and len(account) >= 3 and re.search(
                rf"(?i)(?<![\w-]){re.escape(account)}(?![\w-])", line
            ):
                findings.append((relative, number, "account-name", sample))
            for kind, pattern in PATTERNS:
                if pattern.search(line):
                    findings.append((relative, number, kind, sample))
            if _email_findings(line):
                findings.append((relative, number, "email", sample))
    return findings


def scan_history() -> list[tuple[str, int, str, str]]:
    """Scan every commit's file content (slow, for release prep and history audits)."""

    revs = subprocess.run(
        ["git", "rev-list", "--all"], cwd=ROOT, capture_output=True, check=True
    ).stdout.decode("utf-8").split()
    if not revs:
        return []
    expression = "|".join(pattern.pattern for _, pattern in PATTERNS)
    result = subprocess.run(
        ["git", "grep", "-I", "-n", "-E", expression, *revs],
        cwd=ROOT,
        capture_output=True,
    )
    findings: list[tuple[str, int, str, str]] = []
    for line in result.stdout.decode("utf-8", errors="ignore").splitlines():
        findings.append(("history", 0, "pattern", line[:120]))
    return findings


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--all-history", action="store_true", help="also scan every commit")
    parser.add_argument("--account", default=None, help="account name to look for (defaults to current user)")
    args = parser.parse_args(argv)

    files = _tracked_files()
    findings = scan(files, args.account)
    if args.all_history:
        findings.extend(scan_history())
    for path, number, kind, sample in findings:
        print(f"[{kind}] {path}:{number}: {sample}")
    if findings:
        print(f"\n发现 {len(findings)} 处可能敏感内容，已阻止。", file=sys.stderr)
        return 1
    print(f"隐私检查通过：{len(files)} 个受跟踪文件未发现本机路径、账户名、联系方式或令牌。")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
