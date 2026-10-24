"""Privacy guard: tracked files must not leak local paths, account names or contacts."""

from pathlib import Path

from tools.check_repo_privacy import EMAIL, PATTERNS, iter_lines, scan


def test_flags_account_name_and_local_path(tmp_path: Path):
    # 敏感模式在运行时拼接，避免本文件自身被隐私扫描命中。
    user = "demo" + "-user"
    local_path = "C:" + "\\Users\\" + user + "\\Documents\\a.pdf"
    sample = tmp_path / "note.md"
    sample.write_text(f"| 路径 | {local_path} |\n| 账户 | {user} |\n", encoding="utf-8")

    findings = scan([sample], account=user)

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

    assert scan([sample], account="demo" + "-user") == []


def test_ignores_checksum_digits_and_version_emails():
    phone = dict(PATTERNS)["phone"]
    line = '"sha256": "6a4253210c316f5483080ac5b5abab7d4a9e7206a8ff27051f32a16138485004"'
    assert phone.search(line) is None
    assert EMAIL.search("tauri@2.11.5") is None


def test_iter_lines_skips_binary_suffixes(tmp_path: Path):
    binary = tmp_path / "model.pdiparams"
    binary.write_bytes(b"\x00\x01binary")

    assert list(iter_lines(binary)) == []
