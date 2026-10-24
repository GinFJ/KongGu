"""Privacy guard: tracked files must not leak local paths, account names or contacts."""

from pathlib import Path

from tools.check_repo_privacy import EMAIL, iter_lines, scan


def test_flags_local_user_path_and_account_name(tmp_path: Path):
    sample = tmp_path / "note.md"
    sample.write_text(
        "| 本机 | C:\\Users\\已脱敏\\Documents\\课表.pdf |\n"
        "| 账户 | 已脱敏 |\n",
        encoding="utf-8",
    )

    findings = scan([sample], account="已脱敏")

    kinds = {kind for _, _, kind, _ in findings}
    assert "account-name" in kinds
    assert "local-user-path" in kinds


def test_accepts_placeholders_and_clean_files(tmp_path: Path):
    sample = tmp_path / "clean.md"
    sample.write_text(
        "C:\\Users\\<user>\\Documents\\a.pdf\n"
        "/home/member/a.pdf\n"
        "C:/Users/member/example.pdf\n"
        "依赖 tauri@2.11.5 与 @tauri-apps/api@2\n"
        "用户 example@example.com\n",
        encoding="utf-8",
    )

    assert scan([sample], account="已脱敏") == []


def test_ignores_checksum_digits_and_version_emails():
    line = '"sha256": "6a4253210c316f5483080ac5b5abab7d4a9e7206a8ff27051f32a16138485004"'
    assert not any(
        pattern.search(line) for kind, pattern in __import__("tools.check_repo_privacy", fromlist=["PATTERNS"]).PATTERNS if kind == "phone"
    )
    assert EMAIL.search("tauri@2.11.5") is None


def test_iter_lines_skips_binary_suffixes(tmp_path: Path):
    binary = tmp_path / "model.pdiparams"
    binary.write_bytes(b"\x00\x01binary")

    assert list(iter_lines(binary)) == []
