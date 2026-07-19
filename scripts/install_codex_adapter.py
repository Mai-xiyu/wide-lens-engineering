#!/usr/bin/env python3
"""Preview or install the project-scoped Codex adapter without merging config."""

from __future__ import annotations

import argparse
import json
import os
import stat
import sys
import tempfile
from pathlib import Path
from typing import Any


SCRIPT_DIR = Path(__file__).resolve().parent
SKILL_DIR = SCRIPT_DIR.parent
SOURCE_FILES = (
    Path(".codex/config.toml"),
    Path(".codex/agents/wide-lens-peer.toml"),
)
REPARSE_POINT = getattr(stat, "FILE_ATTRIBUTE_REPARSE_POINT", 0x400)


class InstallError(RuntimeError):
    """Installation cannot proceed safely."""


def is_link_or_reparse(path: Path) -> bool:
    if path.is_symlink():
        return True
    try:
        attributes = path.stat(follow_symlinks=False).st_file_attributes
    except (AttributeError, FileNotFoundError, OSError):
        return False
    return bool(attributes & REPARSE_POINT)


def ensure_plain_ancestors(root: Path, destination: Path) -> None:
    current = root
    if is_link_or_reparse(current):
        raise InstallError(f"project root is a link or reparse point: {current}")
    for part in destination.relative_to(root).parts[:-1]:
        current = current / part
        if current.exists() and is_link_or_reparse(current):
            raise InstallError(f"destination ancestor is a link or reparse point: {current}")


def atomic_write(path: Path, payload: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    handle = tempfile.NamedTemporaryFile(
        mode="wb", prefix=f".{path.name}.", suffix=".tmp", dir=path.parent, delete=False
    )
    temporary = Path(handle.name)
    try:
        with handle:
            handle.write(payload)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, path)
    finally:
        try:
            temporary.unlink()
        except FileNotFoundError:
            pass


def build_plan(target: Path, force: bool) -> tuple[list[dict[str, Any]], int]:
    if not target.exists() or not target.is_dir():
        raise InstallError(f"target must be an existing directory: {target}")
    if is_link_or_reparse(target):
        raise InstallError(f"target is a link or reparse point: {target}")
    root = target.resolve(strict=True)
    entries: list[dict[str, Any]] = []
    conflict = False
    for relative in SOURCE_FILES:
        source = SKILL_DIR / relative
        if not source.is_file() or is_link_or_reparse(source):
            raise InstallError(f"canonical adapter source is unavailable: {source}")
        destination = root / relative
        ensure_plain_ancestors(root, destination)
        try:
            destination.resolve(strict=False).relative_to(root)
        except ValueError as exc:
            raise InstallError(f"destination escapes project: {destination}") from exc
        payload = source.read_bytes()
        if destination.exists():
            if not destination.is_file() or is_link_or_reparse(destination):
                action = "conflict"
                reason = "destination is not a plain file"
                conflict = True
            elif destination.read_bytes() == payload:
                action = "unchanged"
                reason = "identical"
            elif relative.as_posix() == ".codex/config.toml":
                action = "conflict"
                reason = "config must be merged manually; it is never overwritten"
                conflict = True
            elif force:
                action = "replace"
                reason = "explicit --force for peer profile"
            else:
                action = "conflict"
                reason = "peer profile differs; review it or pass --force"
                conflict = True
        else:
            action = "create"
            reason = "missing"
        entries.append(
            {
                "path": relative.as_posix(),
                "action": action,
                "reason": reason,
                "bytes": len(payload),
            }
        )
    return entries, 2 if conflict else 0


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--target", "--project", dest="target", type=Path, required=True,
        help="existing project root",
    )
    parser.add_argument(
        "--apply", action="store_true",
        help="write after a successful full preflight; the default is a dry run",
    )
    parser.add_argument(
        "--force", action="store_true",
        help="replace only a different peer profile; config.toml is never overwritten",
    )
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    try:
        plan, status = build_plan(args.target, args.force)
        result: dict[str, Any] = {
            "version": 1,
            "target": str(args.target.resolve()),
            "mode": "apply" if args.apply else "dry-run",
            "files": plan,
        }
        if status:
            result["status"] = "conflict"
            print(json.dumps(result, ensure_ascii=False, sort_keys=True))
            return status
        if args.apply:
            for entry, relative in zip(plan, SOURCE_FILES):
                if entry["action"] not in {"create", "replace"}:
                    continue
                atomic_write(
                    args.target.resolve(strict=True) / relative,
                    (SKILL_DIR / relative).read_bytes(),
                )
        result["status"] = "ok"
        print(json.dumps(result, ensure_ascii=False, sort_keys=True))
        return 0
    except (InstallError, OSError) as exc:
        print(json.dumps({"version": 1, "status": "error", "error": str(exc)}, ensure_ascii=False))
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
