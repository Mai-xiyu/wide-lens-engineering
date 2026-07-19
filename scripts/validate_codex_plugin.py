#!/usr/bin/env python3
"""Validate a Wide-Lens Codex marketplace directory or release ZIP."""

from __future__ import annotations

import argparse
import hashlib
import json
import re
import stat
import sys
import tempfile
import zipfile
from pathlib import Path
from typing import Any


PLUGIN_NAME = "wide-lens-engineering"
RUNTIME_FILES = {
    "SKILL.md",
    "LICENSE",
    "agents/openai.yaml",
    "references/practical.md",
    "references/protocol.md",
    "references/protocol-v5.md",
    "references/lenses.json",
    "scripts/diverge.py",
    "scripts/diverge_v5.py",
    "scripts/check_delivery.py",
    "scripts/check_delivery_v5.py",
}
RUNTIME_SHA256 = {
    "SKILL.md": "9e4b960236a6f8ee784c9adc5df2114bd203b3d1f90fd2afeaafdfd4eb5027a1",
    "LICENSE": "6bb2dd9d8f7849c4385ae95d83fccbfd7e719a59ddbc9d396fafde31a9cd5b9a",
    "agents/openai.yaml": "da0014061e2c219abf88b6e3611c80100d9ad53af1d4e2a04095dd5d6c00c43b",
    "references/practical.md": "56caa2513aca4148b5864cbef6cb028d01d1f14ff9196367f5ca9c801f2e2d44",
    "references/protocol.md": "775ad630a92b91009506314fc63747c4b1d9395e746f0a102ad41b4934edf639",
    "references/protocol-v5.md": "a8bf68d2d3e95900a4910610a29c50b510153645afd9d8ac21ebcf8e85b07e8a",
    "references/lenses.json": "19b776e9d74c35dd6b5004aa0447db840b0c4c2f1aafa3b4fd1c38f4a8f58518",
    "scripts/diverge.py": "b34d33923f6750dd5e41bcb27da830956506ad962562b4cdf281e146571a8f47",
    "scripts/diverge_v5.py": "cd342f7259671c471ede89db30b95be7aabfd9b0ca6577c32477a55d6715457b",
    "scripts/check_delivery.py": "ecd2a3754bf93371351d8c436e8c670d022210bc48ae9d644f05ebd35d784a2d",
    "scripts/check_delivery_v5.py": "3e7ac2dae87da9dc5580a82d3fbd1ac6fc1dc86310a7cbbacc581cb4bf32a02b",
}
PEER_HOOK_SHA256 = "28db7623d7d39d8d31f76aa145ec5e09f65373470b8fb9db70e0b53596c88427"
CONTROL_SHA256 = {
    "5.0.0": {
        ".codex-plugin/plugin.json": (
            "6bcd92b85648d90e9e230bdae0d042dc7203d87d2997a84e2ee93904d69ebd33"
        ),
        "hooks/hooks.json": (
            "69cd2c39c44c623ba9e39428ba33914b66df48b251f84b0851ec08e804a36847"
        ),
        "hooks/wide_lens_peer_hook.py": PEER_HOOK_SHA256,
    }
}
INSTALL_SHA256 = {
    "5.0.0": "75ddc8d510cc28c71a12e4ddabe13d5fd5fafa9c25282e1f14cfb0a4d5eb2f61"
}
SEMVER_RE = re.compile(
    r"^(0|[1-9]\d*)\.(0|[1-9]\d*)\.(0|[1-9]\d*)"
    r"(?:-[0-9A-Za-z.-]+)?(?:\+[0-9A-Za-z.-]+)?$"
)
REPARSE_POINT = getattr(stat, "FILE_ATTRIBUTE_REPARSE_POINT", 0x400)
MAX_MEMBER_BYTES = 8 * 1024 * 1024
MAX_ARCHIVE_BYTES = 32 * 1024 * 1024
MAX_ARCHIVE_FILE_BYTES = 32 * 1024 * 1024
MAX_COMPRESSION_RATIO = 200


class ValidationError(RuntimeError):
    """Marketplace archive violates its release contract."""


def strict_json(path: Path) -> Any:
    def unique(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
        result: dict[str, Any] = {}
        for key, value in pairs:
            if key in result:
                raise ValidationError(f"duplicate JSON key in {path}: {key}")
            result[key] = value
        return result

    def reject(value: str) -> None:
        raise ValidationError(f"non-finite JSON value in {path}: {value}")

    try:
        return json.loads(
            path.read_text(encoding="utf-8"),
            object_pairs_hook=unique,
            parse_constant=reject,
        )
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise ValidationError(f"invalid UTF-8 JSON in {path}: {exc}") from exc


def is_link_or_reparse(path: Path) -> bool:
    if path.is_symlink():
        return True
    try:
        attributes = path.stat(follow_symlinks=False).st_file_attributes
    except (AttributeError, FileNotFoundError, OSError):
        return False
    return bool(attributes & REPARSE_POINT)


def safe_member_name(name: str) -> bool:
    path = Path(name)
    return bool(
        name
        and not name.startswith(("/", "\\"))
        and "\\" not in name
        and ":" not in name
        and ".." not in path.parts
        and not path.is_absolute()
    )


def validate_archive_shape(archive: zipfile.ZipFile) -> None:
    infos = archive.infolist()
    names = [item.filename for item in infos]
    if len(names) != len(set(names)):
        raise ValidationError("archive contains duplicate members")
    expected = required_files()
    if set(names) != expected or len(names) != len(expected):
        raise ValidationError("archive members differ from the frozen file allowlist")
    total_size = 0
    for info in infos:
        if not safe_member_name(info.filename) or info.is_dir():
            raise ValidationError(f"archive member is unsafe or non-file: {info.filename}")
        mode = (info.external_attr >> 16) & 0o170000
        if mode not in {0, stat.S_IFREG}:
            raise ValidationError(f"archive member is not a regular file: {info.filename}")
        if info.file_size > MAX_MEMBER_BYTES:
            raise ValidationError(f"archive member exceeds the size limit: {info.filename}")
        total_size += info.file_size
        if total_size > MAX_ARCHIVE_BYTES:
            raise ValidationError("archive exceeds the total uncompressed size limit")
        if info.file_size and info.file_size > MAX_COMPRESSION_RATIO * max(info.compress_size, 1):
            raise ValidationError(f"archive member exceeds the compression-ratio limit: {info.filename}")


def expected_marketplace() -> dict[str, Any]:
    return {
        "name": PLUGIN_NAME,
        "interface": {"displayName": "Wide-Lens Engineering"},
        "plugins": [
            {
                "name": PLUGIN_NAME,
                "source": {"source": "local", "path": f"./plugins/{PLUGIN_NAME}"},
                "policy": {"installation": "AVAILABLE", "authentication": "ON_INSTALL"},
                "category": "Developer Tools",
            }
        ],
    }


def required_files() -> set[str]:
    return {
        "INSTALL.md",
        ".agents/plugins/marketplace.json",
        f"plugins/{PLUGIN_NAME}/.codex-plugin/plugin.json",
        f"plugins/{PLUGIN_NAME}/hooks/hooks.json",
        f"plugins/{PLUGIN_NAME}/hooks/wide_lens_peer_hook.py",
        *{
            f"plugins/{PLUGIN_NAME}/skills/{PLUGIN_NAME}/{name}"
            for name in RUNTIME_FILES
        },
    }


def expected_plugin_manifest(version: str) -> dict[str, Any]:
    return {
        "name": PLUGIN_NAME,
        "version": version,
        "description": (
            "Practical and externally anchored software delivery with elastic "
            "task-DAG coordination."
        ),
        "author": {
            "name": "Mai-xiyu",
            "url": "https://github.com/Mai-xiyu",
        },
        "homepage": "https://github.com/Mai-xiyu/wide-lens-engineering",
        "repository": "https://github.com/Mai-xiyu/wide-lens-engineering",
        "license": "MIT",
        "keywords": [
            "codex-skill",
            "software-engineering",
            "elastic-agent-teams",
            "task-dag",
            "isolated-candidates",
            "capability-leases",
            "assured-delivery",
        ],
        "skills": "./skills/",
        "interface": {
            "displayName": "Wide-Lens Engineering",
            "shortDescription": "Elastic engineering with evidence-gated delivery",
            "longDescription": (
                "Code, debug, refactor, and review through a practical workflow or an "
                "externally anchored assured protocol with elastic task-DAG delegation."
            ),
            "developerName": "Mai-xiyu",
            "category": "Developer Tools",
            "capabilities": [
                "Software engineering",
                "Elastic subagents",
                "Assured delivery",
            ],
            "websiteURL": "https://github.com/Mai-xiyu/wide-lens-engineering",
            "defaultPrompt": [
                "Fix this bug with the smallest verified change.",
                "Use elastic read-only peers where they add evidence.",
                "Plan an assured delivery for this high-risk change.",
            ],
            "brandColor": "#2563EB",
        },
    }


def expected_hooks() -> dict[str, Any]:
    return {
        "description": (
            "Optional Wide-Lens result-contract guardrails for the neutral peer profile."
        ),
        "hooks": {
            "SubagentStart": [
                {
                    "matcher": "^wide_lens_peer$",
                    "hooks": [
                        {
                            "type": "command",
                            "command": (
                                'python3 "$PLUGIN_ROOT/hooks/wide_lens_peer_hook.py" start'
                            ),
                            "commandWindows": (
                                'py -3 "%PLUGIN_ROOT%\\hooks\\wide_lens_peer_hook.py" start'
                            ),
                            "timeout": 10,
                            "statusMessage": "Loading Wide-Lens peer contract",
                        }
                    ],
                }
            ],
            "SubagentStop": [
                {
                    "matcher": "^wide_lens_peer$",
                    "hooks": [
                        {
                            "type": "command",
                            "command": (
                                'python3 "$PLUGIN_ROOT/hooks/wide_lens_peer_hook.py" stop'
                            ),
                            "commandWindows": (
                                'py -3 "%PLUGIN_ROOT%\\hooks\\wide_lens_peer_hook.py" stop'
                            ),
                            "timeout": 10,
                            "statusMessage": "Checking Wide-Lens peer result",
                        }
                    ],
                }
            ],
        },
    }


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def plain_files(root: Path) -> set[str]:
    if not root.is_dir() or is_link_or_reparse(root):
        raise ValidationError("marketplace root must be a plain directory")
    result: set[str] = set()
    for path in root.rglob("*"):
        if is_link_or_reparse(path):
            raise ValidationError(f"marketplace contains a link or reparse point: {path}")
        if path.is_dir():
            continue
        if not path.is_file():
            raise ValidationError(f"marketplace contains a non-regular object: {path}")
        result.add(path.relative_to(root).as_posix())
    return result


def validate_directory(root: Path, expected_version: str) -> dict[str, Any]:
    if expected_version not in CONTROL_SHA256:
        raise ValidationError("requested plugin version has no pinned release controls")
    files = plain_files(root)
    marketplace_path = root / ".agents" / "plugins" / "marketplace.json"
    if strict_json(marketplace_path) != expected_marketplace():
        raise ValidationError("marketplace.json differs from the frozen local marketplace schema")
    if sha256_file(root / "INSTALL.md") != INSTALL_SHA256[expected_version]:
        raise ValidationError("INSTALL.md differs from its pinned release bytes")
    plugin_root = root / "plugins" / PLUGIN_NAME
    plugin_root_resolved = plugin_root.resolve(strict=True)
    try:
        plugin_root_resolved.relative_to(root.resolve(strict=True))
    except ValueError as exc:
        raise ValidationError("marketplace plugin source escapes the marketplace root") from exc

    manifest_path = plugin_root / ".codex-plugin" / "plugin.json"
    manifest = strict_json(manifest_path)
    if manifest != expected_plugin_manifest(expected_version):
        raise ValidationError("plugin manifest differs from the pinned release schema")

    hooks_path = plugin_root / "hooks" / "hooks.json"
    hook_program_path = plugin_root / "hooks" / "wide_lens_peer_hook.py"
    if strict_json(hooks_path) != expected_hooks():
        raise ValidationError("hooks/hooks.json differs from the pinned hook schema")
    for relative, expected_digest in CONTROL_SHA256[expected_version].items():
        observed = sha256_file(plugin_root / relative)
        if observed != expected_digest:
            raise ValidationError(
                f"plugin control file differs from its pinned bytes: {relative}"
            )
    skill_root = plugin_root / "skills" / PLUGIN_NAME
    observed_runtime = {
        path.relative_to(skill_root).as_posix()
        for path in skill_root.rglob("*")
        if path.is_file()
    }
    if observed_runtime != RUNTIME_FILES:
        missing = sorted(RUNTIME_FILES - observed_runtime)
        extra = sorted(observed_runtime - RUNTIME_FILES)
        raise ValidationError(f"plugin runtime allowlist mismatch; missing={missing}, extra={extra}")
    for relative_text in sorted(RUNTIME_FILES):
        relative = Path(relative_text)
        observed = sha256_file(skill_root / relative)
        if observed != RUNTIME_SHA256[relative_text]:
            raise ValidationError(
                f"packaged runtime differs from its pinned release bytes: {relative_text}"
            )

    required = required_files()
    if files != required:
        raise ValidationError(
            f"marketplace file allowlist mismatch; missing={sorted(required - files)}, "
            f"extra={sorted(files - required)}"
        )
    return {
        "plugin": PLUGIN_NAME,
        "version": expected_version,
        "files": len(files),
        "runtime_files": len(observed_runtime),
    }


def validate_target(path: Path, expected_version: str) -> dict[str, Any]:
    if path.is_dir():
        return validate_directory(path.resolve(strict=True), expected_version)
    if not path.is_file() or is_link_or_reparse(path):
        raise ValidationError("target must be a marketplace directory or ZIP file")
    if path.stat().st_size > MAX_ARCHIVE_FILE_BYTES:
        raise ValidationError("archive exceeds the compressed file size limit")
    with zipfile.ZipFile(path) as archive:
        validate_archive_shape(archive)
        with tempfile.TemporaryDirectory(prefix="wide-lens-plugin-validate-") as temporary:
            root = Path(temporary)
            archive.extractall(root)
            result = validate_directory(root, expected_version)
    result["archive"] = str(path.resolve())
    return result


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("target", type=Path)
    parser.add_argument("--expected-version", required=True)
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    try:
        if SEMVER_RE.fullmatch(args.expected_version) is None:
            raise ValidationError("--expected-version must be strict semver")
        result = validate_target(args.target, args.expected_version)
        print(json.dumps({"passed": True, **result}, ensure_ascii=False, sort_keys=True))
        return 0
    except (OSError, ValidationError, zipfile.BadZipFile) as exc:
        print(json.dumps({"passed": False, "error": str(exc)}, ensure_ascii=False, sort_keys=True))
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
