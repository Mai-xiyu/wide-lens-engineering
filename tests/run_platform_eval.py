#!/usr/bin/env python3
"""Run the common plus native path/applicability manifest with zero skips."""

from __future__ import annotations

import argparse
import json
import os
import platform
import sys
import tempfile
from pathlib import Path
from typing import Callable


TEST_DIR = Path(__file__).resolve().parent
SKILL_DIR = TEST_DIR.parent
sys.path.insert(0, str(SKILL_DIR / "scripts"))

from check_delivery_v5 import GateError, load_json  # noqa: E402
from diverge import repo_path, scope_path_key  # noqa: E402
from diverge_v5 import load_json as load_planner_json  # noqa: E402


def rejects_json(payload: bytes, expected: str) -> bool:
    with tempfile.TemporaryDirectory(prefix="wide-lens-platform-") as temporary:
        path = Path(temporary) / "artifact.json"
        path.write_bytes(payload)
        try:
            load_json(path)
        except GateError as exc:
            return expected.casefold() in str(exc).casefold()
        return False


def rejects_planner_json_alias(kind: str, expected: str) -> bool:
    """Exercise the planner's real filesystem-object checks, not only strings."""

    with tempfile.TemporaryDirectory(prefix="wide-lens-platform-alias-") as temporary:
        root = Path(temporary)
        target = root / "target.json"
        target.write_text('{"ok":true}\n', encoding="utf-8", newline="\n")
        if kind == "hardlink":
            alias = root / "hardlink.json"
            os.link(target, alias)
        elif kind == "symlink":
            alias = root / "symlink.json"
            alias.symlink_to(target)
        elif kind == "linked-component":
            real = root / "real"
            real.mkdir()
            target = real / "target.json"
            target.write_text('{"ok":true}\n', encoding="utf-8", newline="\n")
            alias_directory = root / "alias"
            alias_directory.symlink_to(real, target_is_directory=True)
            alias = alias_directory / target.name
        else:  # pragma: no cover - caller owns the fixed manifest
            raise ValueError(f"unknown alias kind: {kind}")
        try:
            load_planner_json(alias)
        except (GateError, ValueError) as exc:
            return expected.casefold() in str(exc).casefold()
        return False


def native_case_model_matches() -> bool:
    """Bind the declared case model to an observed filesystem alias behavior."""

    with tempfile.TemporaryDirectory(prefix="wide-lens-platform-case-") as temporary:
        root = Path(temporary)
        upper = root / "WideLensCase.json"
        lower = root / "widelenscase.json"
        upper.write_text("{}\n", encoding="utf-8", newline="\n")
        aliases = lower.exists() and os.path.samefile(upper, lower)
        path_case = "insensitive" if aliases else "sensitive"
        path_flavor = "windows-win32" if os.name == "nt" else "posix"
        keys_alias = scope_path_key(
            "WideLensCase.json", path_case, path_flavor
        ) == scope_path_key("widelenscase.json", path_case, path_flavor)
        return keys_alias is aliases


def windows_ads_is_rejected() -> bool:
    with tempfile.TemporaryDirectory(prefix="wide-lens-platform-ads-") as temporary:
        base = Path(temporary) / "artifact.json"
        base.write_text("{}\n", encoding="utf-8", newline="\n")
        stream = Path(str(base) + ":wide-lens")
        stream.write_text('{"stream":true}\n', encoding="utf-8", newline="\n")
        try:
            load_planner_json(stream)
        except (GateError, ValueError) as exc:
            return "alternate data stream" in str(exc).casefold()
        return False


def run_cases() -> list[dict[str, object]]:
    system = platform.system().casefold()
    native = "windows" if system == "windows" else "macos" if system == "darwin" else "linux"
    cases: list[tuple[str, str, Callable[[], bool]]] = [
        ("common-posix-traversal", "common", lambda: repo_path("../outside.py", "posix") is None),
        ("common-windows-traversal", "common", lambda: repo_path("../outside.py", "windows-win32") is None),
        ("common-posix-absolute", "common", lambda: repo_path("/outside.py", "posix") is None),
        ("common-windows-drive", "common", lambda: repo_path("C:/outside.py", "windows-win32") is None),
        ("common-windows-unc", "common", lambda: repo_path("//server/share/file.py", "windows-win32") is None),
        ("common-canonical-forward-slash", "common", lambda: repo_path("src/file.py", "windows-win32") == "src/file.py"),
        ("common-dot-normalization-rejected", "common", lambda: repo_path("src/./file.py", "posix") is None),
        ("common-case-key", "common", lambda: scope_path_key("SRC/File.py", "insensitive", "windows-win32") == scope_path_key("src/file.py", "insensitive", "windows-win32")),
        ("common-duplicate-json", "common", lambda: rejects_json(b'{"x":1,"x":2}', "duplicate")),
        ("common-nan-json", "common", lambda: rejects_json(b'{"x":NaN}', "non-finite")),
        ("common-deep-json", "common", lambda: rejects_json(("[" * 160 + "0" + "]" * 160).encode(), "nesting exceeds")),
        ("common-invalid-utf8", "common", lambda: rejects_json(b'{"x":"\xff"}', "utf-8")),
        ("common-hardlink-object", "common", lambda: rejects_planner_json_alias("hardlink", "non-hard-linked")),
        ("common-symlink-object", "common", lambda: rejects_planner_json_alias("symlink", "link or reparse")),
        ("common-linked-directory-component", "common", lambda: rejects_planner_json_alias("linked-component", "link or reparse")),
        ("native-filesystem-case-model", f"native-{native}", native_case_model_matches),
    ]
    if native == "windows":
        cases += [
            ("windows-ads", "native-windows", lambda: repo_path("src/file.py:stream", "windows-win32") is None),
            ("windows-reserved-con", "native-windows", lambda: repo_path("CON", "windows-win32") is None),
            ("windows-reserved-with-extension", "native-windows", lambda: repo_path("src/con.txt", "windows-win32") is None),
            ("windows-trailing-dot", "native-windows", lambda: repo_path("src/file.", "windows-win32") is None),
            ("windows-trailing-space", "native-windows", lambda: repo_path("src/file ", "windows-win32") is None),
            ("windows-backslash-alias", "native-windows", lambda: repo_path("src\\file.py", "windows-win32") == "src/file.py"),
            ("windows-case-insensitive-key", "native-windows", lambda: scope_path_key("Src/File.py", "insensitive", "windows-win32") == scope_path_key("src/file.py", "insensitive", "windows-win32")),
            ("windows-safe-unicode", "native-windows", lambda: repo_path("src/\u6587\u4ef6.py", "windows-win32") == "src/\u6587\u4ef6.py"),
            ("windows-actual-ads", "native-windows", windows_ads_is_rejected),
        ]
    else:
        label = f"native-{native}"
        cases += [
            (f"{native}-colon-is-filename", label, lambda: repo_path("src/file.py:stream", "posix") == "src/file.py:stream"),
            (f"{native}-con-is-filename", label, lambda: repo_path("CON", "posix") == "CON"),
            (f"{native}-trailing-dot-is-filename", label, lambda: repo_path("src/file.", "posix") == "src/file."),
            (f"{native}-trailing-space-is-filename", label, lambda: repo_path("src/file ", "posix") == "src/file "),
            (f"{native}-backslash-is-literal", label, lambda: repo_path("src\\file.py", "posix") == "src\\file.py"),
            (f"{native}-case-sensitive-key", label, lambda: scope_path_key("Src/File.py", "sensitive", "posix") != scope_path_key("src/file.py", "sensitive", "posix")),
            (f"{native}-safe-unicode", label, lambda: repo_path("src/\u6587\u4ef6.py", "posix") == "src/\u6587\u4ef6.py"),
            (f"{native}-nul-rejected", label, lambda: repo_path("src/file\x00.py", "posix") is None),
        ]
    results: list[dict[str, object]] = []
    for name, applicability, check in cases:
        try:
            passed = check()
            detail = ""
        except Exception as exc:
            passed = False
            detail = f"{type(exc).__name__}: {exc}"
        results.append(
            {
                "name": name,
                "applicability": applicability,
                "applicable": True,
                "skipped": False,
                "passed": passed,
                "detail": detail,
            }
        )
    return results


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--json", action="store_true")
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    results = run_cases()
    passed = sum(item["passed"] is True for item in results)
    skipped = sum(item["skipped"] is True for item in results)
    payload = {
        "passed": passed == len(results) and skipped == 0,
        "platform": platform.platform(),
        "python": platform.python_version(),
        "applicability_manifest": {
            "common": [item["name"] for item in results if item["applicability"] == "common"],
            "native": [item["name"] for item in results if item["applicability"] != "common"],
        },
        "passed_cases": passed,
        "total_cases": len(results),
        "skipped_cases": skipped,
        "failures": [item for item in results if item["passed"] is not True],
    }
    print(json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True))
    return 0 if payload["passed"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
