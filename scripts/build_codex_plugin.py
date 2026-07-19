#!/usr/bin/env python3
"""Build a deterministic, self-contained Codex plugin marketplace archive."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import shutil
import stat
import subprocess
import sys
import tempfile
import zipfile
from pathlib import Path
from typing import Any, Iterable


SCRIPT_DIR = Path(__file__).resolve().parent
SKILL_DIR = SCRIPT_DIR.parent
PLUGIN_SOURCE = SKILL_DIR / "packaging" / "codex-plugin-src"
PLUGIN_NAME = "wide-lens-engineering"
RUNTIME_FILES = (
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
)
SEMVER_RE = re.compile(
    r"^(0|[1-9]\d*)\.(0|[1-9]\d*)\.(0|[1-9]\d*)"
    r"(?:-[0-9A-Za-z.-]+)?(?:\+[0-9A-Za-z.-]+)?$"
)
REPARSE_POINT = getattr(stat, "FILE_ATTRIBUTE_REPARSE_POINT", 0x400)
FIXED_TIME = (1980, 1, 1, 0, 0, 0)


class BuildError(RuntimeError):
    """Plugin packaging invariant failed."""


def is_link_or_reparse(path: Path) -> bool:
    if path.is_symlink():
        return True
    try:
        attributes = path.stat(follow_symlinks=False).st_file_attributes
    except (AttributeError, FileNotFoundError, OSError):
        return False
    return bool(attributes & REPARSE_POINT)


def strict_json(path: Path) -> Any:
    def unique(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
        value: dict[str, Any] = {}
        for key, item in pairs:
            if key in value:
                raise BuildError(f"duplicate JSON key in {path}: {key}")
            value[key] = item
        return value

    def reject(value: str) -> None:
        raise BuildError(f"non-finite JSON in {path}: {value}")

    try:
        return json.loads(
            path.read_text(encoding="utf-8"),
            object_pairs_hook=unique,
            parse_constant=reject,
        )
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise BuildError(f"invalid UTF-8 JSON in {path}: {exc}") from exc


def iter_plain_files(root: Path) -> Iterable[Path]:
    if not root.exists() or not root.is_dir() or is_link_or_reparse(root):
        raise BuildError(f"source tree is unavailable or linked: {root}")
    for path in sorted(root.rglob("*"), key=lambda item: item.as_posix()):
        if is_link_or_reparse(path):
            raise BuildError(f"linked source is not packageable: {path}")
        if path.is_dir():
            continue
        if not path.is_file():
            raise BuildError(f"non-regular source is not packageable: {path}")
        if "__pycache__" in path.parts or path.suffix in {".pyc", ".pyo"}:
            continue
        yield path


def copy_tree(source: Path, destination: Path) -> None:
    for path in iter_plain_files(source):
        target = destination / path.relative_to(source)
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(path, target)


def marketplace_document() -> dict[str, Any]:
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


def install_text() -> str:
    return (
        "# Install Wide-Lens Engineering\n\n"
        "This directory is a self-contained local Codex plugin marketplace.\n\n"
        "```bash\n"
        "codex plugin marketplace add /absolute/path/to/this/directory\n"
        "codex plugin marketplace list\n"
        "```\n\n"
        "Restart ChatGPT desktop, install `wide-lens-engineering` from Plugins, then review "
        "the plugin hooks with `/hooks` before explicitly trusting them. Do not use "
        "`--dangerously-bypass-hook-trust` for normal installation.\n\n"
        "The plugin does not install `.codex/agents`. Install the repository's Codex project "
        "adapter separately to create the `wide_lens_peer` profile; without that profile, the "
        "two hook matchers do not run. The hooks validate result shape only and do not prove "
        "read-only execution or workspace isolation.\n"
    )


def build_staging(marketplace_root: Path, version: str) -> Path:
    plugin_root = marketplace_root / "plugins" / PLUGIN_NAME
    copy_tree(PLUGIN_SOURCE, plugin_root)
    skill_root = plugin_root / "skills" / PLUGIN_NAME
    for relative_text in RUNTIME_FILES:
        relative = Path(relative_text)
        source = SKILL_DIR / relative
        if not source.is_file() or is_link_or_reparse(source):
            raise BuildError(f"canonical runtime file is unavailable: {source}")
        target = skill_root / relative
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(source, target)

    manifest_path = plugin_root / ".codex-plugin" / "plugin.json"
    manifest = strict_json(manifest_path)
    if not isinstance(manifest, dict) or manifest.get("version") != version:
        raise BuildError(
            f"requested version {version} does not match source manifest "
            f"{manifest.get('version') if isinstance(manifest, dict) else None}"
        )
    marketplace_path = marketplace_root / ".agents" / "plugins" / "marketplace.json"
    marketplace_path.parent.mkdir(parents=True, exist_ok=True)
    marketplace_path.write_text(
        json.dumps(marketplace_document(), ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
        newline="\n",
    )
    (marketplace_root / "INSTALL.md").write_text(
        install_text(), encoding="utf-8", newline="\n"
    )
    return plugin_root


def run_external_validator(validator: Path, target: Path, version: str) -> None:
    if not validator.is_file() or is_link_or_reparse(validator):
        raise BuildError(f"validator is unavailable: {validator}")
    completed = subprocess.run(
        [
            sys.executable,
            "-B",
            str(validator),
            str(target),
            "--expected-version",
            version,
        ],
        check=False,
        capture_output=True,
        text=True,
    )
    if completed.returncode != 0:
        detail = (completed.stdout + completed.stderr).strip()
        raise BuildError(f"external marketplace validation failed: {detail}")


def zip_staging(marketplace_root: Path, output: Path) -> str:
    handle = tempfile.NamedTemporaryFile(
        prefix=f".{output.name}.", suffix=".tmp", dir=output.parent, delete=False
    )
    temporary = Path(handle.name)
    handle.close()
    try:
        with zipfile.ZipFile(
            temporary, "w", compression=zipfile.ZIP_DEFLATED, compresslevel=9
        ) as archive:
            for path in iter_plain_files(marketplace_root):
                name = path.relative_to(marketplace_root).as_posix()
                parts = Path(name).parts
                if (
                    not name
                    or name.startswith("/")
                    or ".." in parts
                    or ":" in name
                    or "\\" in name
                ):
                    raise BuildError(f"unsafe archive path: {name}")
                info = zipfile.ZipInfo(name, FIXED_TIME)
                info.compress_type = zipfile.ZIP_DEFLATED
                mode = 0o755 if path.suffix == ".py" else 0o644
                info.external_attr = (stat.S_IFREG | mode) << 16
                info.create_system = 3
                archive.writestr(
                    info,
                    path.read_bytes(),
                    compress_type=zipfile.ZIP_DEFLATED,
                    compresslevel=9,
                )
        digest = hashlib.sha256(temporary.read_bytes()).hexdigest()
        os.replace(temporary, output)
        return digest
    finally:
        try:
            temporary.unlink()
        except FileNotFoundError:
            pass


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    source_manifest = strict_json(PLUGIN_SOURCE / ".codex-plugin" / "plugin.json")
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--version", default=source_manifest.get("version"))
    parser.add_argument("--output-dir", type=Path, default=Path("dist"))
    parser.add_argument("--validator", type=Path)
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--force", action="store_true")
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    try:
        args = parse_args(argv)
        if not isinstance(args.version, str) or SEMVER_RE.fullmatch(args.version) is None:
            raise BuildError("--version must be strict semver")
        output_dir = (
            (SKILL_DIR / args.output_dir).resolve()
            if not args.output_dir.is_absolute()
            else args.output_dir.resolve()
        )
        try:
            output_dir.relative_to(SKILL_DIR.resolve())
        except ValueError as exc:
            raise BuildError("output directory must stay inside the repository") from exc
        output = output_dir / f"{PLUGIN_NAME}-marketplace-{args.version}.zip"
        plan: dict[str, Any] = {
            "version": 1,
            "plugin_version": args.version,
            "output": str(output),
            "validator": str(args.validator.resolve()) if args.validator else None,
            "mode": "dry-run" if args.dry_run else "build",
        }
        if args.dry_run:
            plan["status"] = "ok"
            print(json.dumps(plan, ensure_ascii=False, sort_keys=True))
            return 0
        output_dir.mkdir(parents=True, exist_ok=True)
        if is_link_or_reparse(output_dir):
            raise BuildError("output directory is a link or reparse point")
        if output.exists() and not args.force:
            plan["status"] = "conflict"
            print(json.dumps(plan, ensure_ascii=False, sort_keys=True))
            return 2
        with tempfile.TemporaryDirectory(prefix="wide-lens-marketplace-") as temporary:
            marketplace_root = Path(temporary) / PLUGIN_NAME
            plugin_root = build_staging(marketplace_root, args.version)
            if args.validator:
                run_external_validator(args.validator.resolve(), marketplace_root, args.version)
            digest = zip_staging(marketplace_root, output)
        plan.update(
            {
                "status": "ok",
                "sha256": digest,
                "plugin_root": f"plugins/{plugin_root.name}",
            }
        )
        print(json.dumps(plan, ensure_ascii=False, sort_keys=True))
        return 0
    except (BuildError, OSError) as exc:
        print(
            json.dumps(
                {"version": 1, "status": "error", "error": str(exc)},
                ensure_ascii=False,
            )
        )
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
