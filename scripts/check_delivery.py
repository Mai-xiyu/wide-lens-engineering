#!/usr/bin/env python3
"""Validate a Wide-Lens Engineering delivery report against its packet."""

from __future__ import annotations

import argparse
import json
import hashlib
import os
import stat
import subprocess
from collections import Counter
import re
from pathlib import Path
from typing import Any
from diverge import (
    build_packet,
    build_participant_prompts,
    contract_sha256,
    freeze_contract,
    packet_sha256,
    repo_path,
    runtime_identity,
    scope_path_key,
    strict_json_load,
)



VALID_LEVELS = {"E1", "E2", "E3"}
VALID_LANE_STATUSES = {"clear", "finding", "blocked"}
VALID_SEVERITIES = {"critical", "high", "medium", "low"}
VALID_DISPOSITIONS = {"fixed", "accepted", "not-applicable", "open"}
VALID_CHECK_STATUSES = {"passed", "failed", "not-run"}
VALID_RISKS = {"low", "medium", "high"}
VALID_PROFILES = {"light", "full"}
VALID_COORDINATION = {"independent", "shared"}
VALID_STANCES = {"support", "challenge", "uncertain"}
VALID_INTENTS = {"change", "debug", "review"}
CODING_INTENTS = {"change", "debug"}
VALID_IMPLEMENTATION_STATUS = {"changed", "no-change"}
VALID_MINIMALISM_SOURCES = {"ponytail", "built-in"}
VALID_MINIMALISM_RUNGS = {"not-needed", "reuse", "stdlib", "native", "existing-dependency", "minimal-custom"}
PLACEHOLDERS = {".", "-", "n/a", "na", "none", "unknown", "tbd"}

EXPECTED_DISCUSSION_BUDGET = {
    "max_turns_per_participant": 2,
    "max_round_seconds_per_participant": 600,
    "max_retries_per_participant": 1,
    "max_position_bytes_per_participant": 32768,
    "allow_nested_agents": False,
    "allow_writes": False,
}


def _nonempty_string(value: Any) -> bool:
    return isinstance(value, str) and bool(value.strip())


def _concrete_string(value: Any) -> bool:
    return _nonempty_string(value) and value.strip().casefold() not in PLACEHOLDERS

def _one_of(value: Any, allowed: set[str]) -> bool:
    return isinstance(value, str) and value in allowed



def _string_list(value: Any, minimum: int = 1) -> bool:
    return (
        isinstance(value, list)
        and len(value) >= minimum
        and all(_nonempty_string(item) for item in value)
    )


def _require_exact_keys(
    value: Any, expected: set[str], location: str, errors: list[str]
) -> None:
    if isinstance(value, dict) and set(value) != expected:
        errors.append(f"{location}: keys must equal {sorted(expected)}")


def _validate_evidence(value: Any, location: str, errors: list[str]) -> None:
    if not isinstance(value, list) or not value:
        errors.append(f"{location}: evidence must be a non-empty list")
        return
    for index, item in enumerate(value):
        item_location = f"{location}.evidence[{index}]"
        if not isinstance(item, dict):
            errors.append(f"{item_location}: must be an object")
            continue
        _require_exact_keys(item, {"level", "ref", "claim"}, item_location, errors)
        if not _one_of(item.get("level"), VALID_LEVELS):
            errors.append(f"{item_location}.level: expected one of {sorted(VALID_LEVELS)}")
        if not _concrete_string(item.get("ref")):
            errors.append(f"{item_location}.ref: must be concrete, not empty or a placeholder")
        if not _concrete_string(item.get("claim")):
            errors.append(f"{item_location}.claim: must be concrete, not empty or a placeholder")


def _canonical_json_bytes(value: Any) -> bytes:
    return json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        allow_nan=False,
    ).encode("utf-8")


def _strict_json_equal(left: Any, right: Any) -> bool:
    try:
        return _canonical_json_bytes(left) == _canonical_json_bytes(right)
    except (RecursionError, TypeError, UnicodeError, ValueError):
        return False


def _file_sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _repository_identity(path: Path, *, strict: bool = True) -> str:
    return os.path.normcase(os.path.normpath(str(path.resolve(strict=strict))))


def _is_reparse_point(path: Path) -> bool:
    attributes = getattr(os.lstat(path), "st_file_attributes", 0)
    return bool(attributes & getattr(stat, "FILE_ATTRIBUTE_REPARSE_POINT", 0))


def _entry_metadata(
    path: Path, entry_type: str, metadata: os.stat_result | None = None
) -> dict[str, Any]:
    metadata = metadata or path.stat(follow_symlinks=False)
    value = {
        "type": entry_type,
        "mode": stat.S_IMODE(metadata.st_mode),
        "attributes": int(getattr(metadata, "st_file_attributes", 0)),
        "nlink": int(metadata.st_nlink),
        "file_id": f"{metadata.st_dev}:{metadata.st_ino}",
    }
    if entry_type == "file":
        value["size"] = int(metadata.st_size)
    return value


def _root_metadata(path: Path) -> dict[str, Any]:
    metadata = path.stat(follow_symlinks=False)
    return {
        "type": "directory",
        "mode": stat.S_IMODE(metadata.st_mode),
        "attributes": int(getattr(metadata, "st_file_attributes", 0)),
        "nlink": int(metadata.st_nlink),
        "file_id": f"{metadata.st_dev}:{metadata.st_ino}",
    }


def _file_sha256_size(path: Path) -> tuple[str, int]:
    digest = hashlib.sha256()
    size = 0
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
            size += len(chunk)
    return digest.hexdigest(), size


def _windows_named_streams(path: Path) -> dict[str, dict[str, Any]]:
    """Return size and SHA-256 for every non-default NTFS data stream."""
    if os.name != "nt":
        return {}

    import ctypes
    from ctypes import wintypes

    class WIN32_FIND_STREAM_DATA(ctypes.Structure):
        _fields_ = [
            ("StreamSize", ctypes.c_longlong),
            ("cStreamName", wintypes.WCHAR * (260 + 36)),
        ]

    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    find_first = kernel32.FindFirstStreamW
    find_first.argtypes = [
        wintypes.LPCWSTR,
        ctypes.c_int,
        ctypes.POINTER(WIN32_FIND_STREAM_DATA),
        wintypes.DWORD,
    ]
    find_first.restype = wintypes.HANDLE
    find_next = kernel32.FindNextStreamW
    find_next.argtypes = [wintypes.HANDLE, ctypes.POINTER(WIN32_FIND_STREAM_DATA)]
    find_next.restype = wintypes.BOOL
    find_close = kernel32.FindClose
    find_close.argtypes = [wintypes.HANDLE]
    find_close.restype = wintypes.BOOL

    data = WIN32_FIND_STREAM_DATA()
    handle = find_first(str(path), 0, ctypes.byref(data), 0)
    invalid_handle = ctypes.c_void_p(-1).value
    if handle == invalid_handle:
        error = ctypes.get_last_error()
        if error in {38, 87}:
            return {}
        raise ctypes.WinError(error)

    streams: dict[str, dict[str, Any]] = {}
    try:
        while True:
            stream_name = data.cStreamName
            if stream_name != "::$DATA":
                if (
                    not stream_name.startswith(":")
                    or not stream_name.endswith(":$DATA")
                    or "\x00" in stream_name
                ):
                    raise ValueError(f"unsupported Windows stream name: {stream_name!r}")
                digest, size = _file_sha256_size(Path(str(path) + stream_name))
                if size != data.StreamSize:
                    raise ValueError(f"Windows stream changed during snapshot: {path}{stream_name}")
                streams[stream_name] = {"size": size, "sha256": digest}
            if find_next(handle, ctypes.byref(data)):
                continue
            error = ctypes.get_last_error()
            if error == 38:
                break
            raise ctypes.WinError(error)
    finally:
        find_close(handle)
    return streams


def _build_state_manifest_once(root: Path) -> dict[str, Any]:
    entries: dict[str, dict[str, Any]] = {}
    named_streams: dict[str, dict[str, dict[str, Any]]] = {}
    root_streams = _windows_named_streams(root)
    if root_streams:
        raise ValueError("repository root named streams are not supported")
    pending = [root]
    while pending:
        current = pending.pop()
        try:
            with os.scandir(current) as iterator:
                children = sorted(iterator, key=lambda item: (item.name.casefold(), item.name))
        except OSError as exc:
            raise ValueError(f"cannot enumerate repository directory {current}: {exc}") from exc
        for child in children:
            path = Path(child.path)
            relative = path.relative_to(root).as_posix()
            try:
                metadata = child.stat(follow_symlinks=False)
            except OSError as exc:
                raise ValueError(f"cannot stat repository entry {relative}: {exc}") from exc
            attributes = int(getattr(metadata, "st_file_attributes", 0))
            if stat.S_ISLNK(metadata.st_mode) or attributes & getattr(
                stat, "FILE_ATTRIBUTE_REPARSE_POINT", 0
            ):
                raise ValueError(f"repository links and reparse points are unsupported: {relative}")
            if stat.S_ISDIR(metadata.st_mode):
                entries[relative] = _entry_metadata(path, "directory", metadata)
                streams = _windows_named_streams(path)
                if streams:
                    named_streams[relative] = streams
                pending.append(path)
            elif stat.S_ISREG(metadata.st_mode):
                if os.path.normcase(path.name) == os.path.normcase(".git"):
                    with path.open("rb") as git_pointer:
                        if git_pointer.read(8).lower() == b"gitdir: ":
                            raise ValueError(
                                f"external Git directory indirection is unsupported: {relative}"
                            )
                entry = _entry_metadata(path, "file", metadata)
                digest, size = _file_sha256_size(path)
                if size != entry["size"]:
                    raise ValueError(f"repository file changed during snapshot: {relative}")
                entry["sha256"] = digest
                entries[relative] = entry
                streams = _windows_named_streams(path)
                if streams:
                    named_streams[relative] = streams
            else:
                raise ValueError(f"unsupported repository entry type: {relative}")
    return {
        "version": 2,
        "repository_ref": _repository_identity(root),
        "root_metadata": _root_metadata(root),
        "entries": entries,
        "named_streams": named_streams,
    }


def _canonical_repository_root(root: Path) -> Path:
    lexical_root = Path(os.path.abspath(root))
    metadata = os.lstat(lexical_root)
    if stat.S_ISLNK(metadata.st_mode) or int(
        getattr(metadata, "st_file_attributes", 0)
    ) & getattr(stat, "FILE_ATTRIBUTE_REPARSE_POINT", 0):
        raise ValueError("repository root cannot be a link or reparse point")
    resolved_root = lexical_root.resolve(strict=True)
    if os.path.normcase(os.path.normpath(str(lexical_root))) != _repository_identity(
        resolved_root
    ):
        raise ValueError("repository root must use its canonical filesystem path")
    if not resolved_root.is_dir():
        raise ValueError("repository root must be a directory")
    return resolved_root


def build_state_manifest(root: Path) -> dict[str, Any]:
    root = _canonical_repository_root(root)
    first = _build_state_manifest_once(root)
    second = _build_state_manifest_once(root)
    if not _strict_json_equal(first, second):
        raise ValueError("repository state changed during snapshot")
    return second


def _validate_entry_metadata(entry: Any, path: str, entry_type: str) -> None:
    base_keys = {"type", "mode", "attributes", "nlink", "file_id"}
    expected_keys = base_keys | ({"size", "sha256"} if entry_type == "file" else set())
    if not isinstance(entry, dict) or set(entry) != expected_keys:
        raise ValueError(f"baseline manifest {entry_type} {path!r} has invalid metadata")
    if entry.get("type") != entry_type:
        raise ValueError(f"baseline manifest entry {path!r} has an invalid type")
    for field in ("mode", "attributes", "nlink"):
        if type(entry.get(field)) is not int or entry[field] < 0:
            raise ValueError(
                f"baseline manifest {entry_type} {path!r} has invalid {field}"
            )
    if not isinstance(entry.get("file_id"), str) or re.fullmatch(
        r"[0-9]+:[0-9]+", entry["file_id"]
    ) is None:
        raise ValueError(f"baseline manifest {entry_type} {path!r} has invalid file_id")
    if entry_type == "file" and (
        type(entry.get("size")) is not int
        or entry["size"] < 0
        or not isinstance(entry.get("sha256"), str)
        or re.fullmatch(r"[0-9a-f]{64}", entry["sha256"]) is None
    ):
        raise ValueError(f"baseline manifest file {path!r} has invalid content metadata")


def validate_state_manifest(value: Any) -> dict[str, Any]:
    expected_keys = {
        "version",
        "repository_ref",
        "root_metadata",
        "entries",
        "named_streams",
    }
    if not isinstance(value, dict) or set(value) != expected_keys:
        raise ValueError(f"baseline manifest keys must equal {sorted(expected_keys)}")
    if type(value.get("version")) is not int or value["version"] != 2:
        raise ValueError("baseline manifest version must be integer 2")
    repository_ref = value.get("repository_ref")
    if (
        not _nonempty_string(repository_ref)
        or not os.path.isabs(repository_ref)
        or repository_ref != os.path.normcase(os.path.normpath(repository_ref))
    ):
        raise ValueError("baseline manifest repository_ref must be a canonical absolute path")
    root_metadata = value.get("root_metadata")
    if (
        not isinstance(root_metadata, dict)
        or set(root_metadata) != {"type", "mode", "attributes", "nlink", "file_id"}
        or root_metadata.get("type") != "directory"
        or any(
            type(root_metadata.get(field)) is not int or root_metadata[field] < 0
            for field in ("mode", "attributes", "nlink")
        )
        or not isinstance(root_metadata.get("file_id"), str)
        or re.fullmatch(r"[0-9]+:[0-9]+", root_metadata["file_id"]) is None
    ):
        raise ValueError("baseline manifest root_metadata is invalid")
    entries = value.get("entries")
    if not isinstance(entries, dict):
        raise ValueError("baseline manifest entries must be an object")
    for path, entry in entries.items():
        if (
            not isinstance(path, str)
            or repo_path(path, "posix") != path
            or path == "."
        ):
            raise ValueError(f"baseline manifest contains unsafe path {path!r}")
        if not isinstance(entry, dict) or entry.get("type") not in {
            "directory",
            "file",
        }:
            raise ValueError(f"baseline manifest entry {path!r} has an invalid type")
        _validate_entry_metadata(entry, path, entry["type"])
    named_streams = value.get("named_streams")
    if not isinstance(named_streams, dict):
        raise ValueError("baseline manifest named_streams must be an object")
    for path, streams in named_streams.items():
        if (
            not isinstance(path, str)
            or repo_path(path, "posix") != path
            or path == "."
            or path not in entries
        ):
            raise ValueError(f"baseline manifest contains unsafe stream owner {path!r}")
        if not isinstance(streams, dict) or not streams:
            raise ValueError(f"baseline manifest streams for {path!r} must be non-empty")
        for name, stream in streams.items():
            if (
                not isinstance(name, str)
                or name == "::$DATA"
                or not name.startswith(":")
                or not name.endswith(":$DATA")
                or "\x00" in name
                or not isinstance(stream, dict)
                or set(stream) != {"size", "sha256"}
                or type(stream.get("size")) is not int
                or stream["size"] < 0
                or not isinstance(stream.get("sha256"), str)
                or re.fullmatch(r"[0-9a-f]{64}", stream["sha256"]) is None
            ):
                raise ValueError(
                    f"baseline manifest stream {name!r} for {path!r} is invalid"
                )
    canonical = {
        "version": 2,
        "repository_ref": repository_ref,
        "root_metadata": root_metadata,
        "entries": entries,
        "named_streams": named_streams,
    }
    _canonical_json_bytes(canonical)
    return canonical


def state_manifest_sha256(manifest: Any) -> str:
    return hashlib.sha256(_canonical_json_bytes(validate_state_manifest(manifest))).hexdigest()


def state_manifest_changed_paths(before: Any, after: Any) -> list[str]:
    old_manifest = validate_state_manifest(before)
    new_manifest = validate_state_manifest(after)
    if old_manifest["repository_ref"] != new_manifest["repository_ref"]:
        raise ValueError("baseline and current manifests refer to different repositories")
    old = old_manifest["entries"]
    new = new_manifest["entries"]
    old_streams = old_manifest["named_streams"]
    new_streams = new_manifest["named_streams"]
    changed = {
        path
        for path in old.keys() | new.keys() | old_streams.keys() | new_streams.keys()
        if old.get(path) != new.get(path)
        or old_streams.get(path) != new_streams.get(path)
    }
    root_state_fields = ("type", "mode", "attributes")
    if any(
        old_manifest["root_metadata"][field]
        != new_manifest["root_metadata"][field]
        for field in root_state_fields
    ):
        changed.add(".")
    return sorted(changed, key=lambda value: (value.casefold(), value))

def _windows_long_path(path: Path) -> Path:
    if os.name != "nt":
        return path

    import ctypes
    from ctypes import wintypes

    get_long = ctypes.WinDLL("kernel32", use_last_error=True).GetLongPathNameW
    get_long.argtypes = [wintypes.LPCWSTR, wintypes.LPWSTR, wintypes.DWORD]
    get_long.restype = wintypes.DWORD
    required = get_long(str(path), None, 0)
    if required == 0:
        raise ctypes.WinError(ctypes.get_last_error())
    buffer = ctypes.create_unicode_buffer(required)
    written = get_long(str(path), buffer, required)
    if written == 0 or written >= required:
        raise ctypes.WinError(ctypes.get_last_error())
    return Path(buffer.value)


def _canonical_existing_scope_path(root: Path, value: str) -> str:
    normalized = repo_path(value, "windows-win32")
    if normalized is None:
        raise ValueError(f"invalid Win32 scope path: {value!r}")
    if normalized == ".":
        return "."
    parts = normalized.split("/")
    for count in range(len(parts), -1, -1):
        candidate = root.joinpath(*parts[:count])
        try:
            os.lstat(candidate)
        except FileNotFoundError:
            continue
        long_candidate = _windows_long_path(candidate).resolve(strict=True)
        try:
            existing = long_candidate.relative_to(root).as_posix()
        except ValueError as exc:
            raise ValueError(f"scope path escapes repository: {value!r}") from exc
        prefix = [] if existing == "." else existing.split("/")
        return "/".join(prefix + parts[count:]) or "."
    raise ValueError(f"cannot resolve repository root for scope path: {value!r}")


def validate_scope_paths_against_repo(
    contract: dict[str, Any], repo_root: Path
) -> list[str]:
    scope = contract.get("scope", {})
    flavor = scope.get("path_flavor", {}).get("value")
    if flavor != "windows-win32":
        return []
    if os.name != "nt":
        return ["windows-win32 scope requires a Windows controller"]
    path_case = scope.get("path_case", {}).get("value", "insensitive")
    errors: list[str] = []
    for field in (
        "analysis_paths",
        "allowed_write_paths",
        "forbidden_write_paths",
    ):
        for index, item in enumerate(scope.get(field, [])):
            if not isinstance(item, dict) or not isinstance(item.get("path"), str):
                continue
            supplied = item["path"]
            try:
                canonical = _canonical_existing_scope_path(repo_root, supplied)
            except (OSError, ValueError) as exc:
                errors.append(f"contract.scope.{field}[{index}]: {exc}")
                continue
            if scope_path_key(
                supplied, path_case, flavor
            ) != scope_path_key(canonical, path_case, flavor):
                errors.append(
                    f"contract.scope.{field}[{index}]: filesystem alias is forbidden; "
                    f"use canonical path {canonical!r}"
                )
    return errors

def runtime_receipt_sha256(receipt: Any) -> str:
    return hashlib.sha256(_canonical_json_bytes(receipt)).hexdigest()


def validate_runtime_receipt(
    receipt: Any,
    packet: dict[str, Any],
    report: Any,
    expected_digest: Any,
) -> list[str]:
    errors: list[str] = []
    expected_keys = {
        "version",
        "packet_sha256",
        "controller_ref",
        "participants",
        "deliberation_sha256",
        "nested_agents_spawned",
        "subagent_writes_detected",
    }
    if not isinstance(receipt, dict) or set(receipt) != expected_keys:
        return [f"runtime receipt keys must equal {sorted(expected_keys)}"]
    if type(receipt.get("version")) is not int or receipt["version"] != 1:
        errors.append("runtime receipt version must be integer 1")
    if receipt.get("packet_sha256") != packet.get("packet_sha256"):
        errors.append("runtime receipt packet_sha256 must match the frozen packet")
    if not _concrete_string(receipt.get("controller_ref")):
        errors.append("runtime receipt controller_ref must be concrete")
    if (
        not isinstance(expected_digest, str)
        or re.fullmatch(r"[0-9a-f]{64}", expected_digest) is None
        or runtime_receipt_sha256(receipt) != expected_digest
    ):
        errors.append("runtime receipt does not match the trusted controller digest")

    deliberation = report.get("deliberation") if isinstance(report, dict) else None
    delegation = (
        deliberation.get("delegation") if isinstance(deliberation, dict) else None
    )
    report_participants = (
        delegation.get("participants") if isinstance(delegation, dict) else None
    )
    expected_participants = []
    if isinstance(report_participants, list):
        expected_participants = [
            {
                "id": participant.get("id"),
                "lane_ids": participant.get("lane_ids"),
            }
            for participant in report_participants
            if isinstance(participant, dict)
        ]
    if (
        not isinstance(receipt.get("participants"), list)
        or len(receipt["participants"]) < 2
        or not _strict_json_equal(receipt["participants"], expected_participants)
    ):
        errors.append(
            "runtime receipt participants must exactly match runtime-selected report participants"
        )
    expected_deliberation_digest = (
        hashlib.sha256(_canonical_json_bytes(deliberation)).hexdigest()
        if isinstance(deliberation, dict)
        else None
    )
    if receipt.get("deliberation_sha256") != expected_deliberation_digest:
        errors.append("runtime receipt must bind the complete deliberation")
    operation = (
        deliberation.get("operation") if isinstance(deliberation, dict) else None
    )
    if receipt.get("nested_agents_spawned") is not False or (
        isinstance(operation, dict)
        and operation.get("nested_agents_spawned") is not False
    ):
        errors.append("runtime receipt must attest that nested agents were not spawned")
    if receipt.get("subagent_writes_detected") is not False or (
        isinstance(operation, dict) and operation.get("writes_detected") is not False
    ):
        errors.append("runtime receipt must attest that subagents did not write")
    return errors

def _trusted_shell_executable() -> str:
    if os.name != "nt":
        return "/bin/sh"

    import ctypes
    from ctypes import wintypes

    get_system_directory = ctypes.WinDLL(
        "kernel32", use_last_error=True
    ).GetSystemDirectoryW
    get_system_directory.argtypes = [wintypes.LPWSTR, wintypes.UINT]
    get_system_directory.restype = wintypes.UINT
    buffer = ctypes.create_unicode_buffer(32768)
    written = get_system_directory(buffer, len(buffer))
    if written == 0 or written >= len(buffer):
        raise ctypes.WinError(ctypes.get_last_error())
    return str(Path(buffer.value) / "cmd.exe")

def _sanitized_check_environment(repo_root: Path) -> dict[str, str]:
    environment = dict(os.environ)
    for name in (
        "BASH_ENV",
        "CDPATH",
        "ENV",
        "NODE_OPTIONS",
        "PERL5OPT",
        "PYTHONHOME",
        "PYTHONINSPECT",
        "PYTHONPATH",
        "RUBYOPT",
    ):
        environment.pop(name, None)
    for name in tuple(environment):
        if name.upper().startswith("GIT_"):
            environment.pop(name, None)
    environment["GIT_CONFIG_GLOBAL"] = os.devnull
    environment["GIT_CONFIG_SYSTEM"] = os.devnull
    environment["GIT_CONFIG_NOSYSTEM"] = "1"
    environment["GIT_TERMINAL_PROMPT"] = "0"
    safe_path: list[str] = []
    for item in environment.get("PATH", "").split(os.pathsep):
        item = item.strip().strip('"')
        if not item or not Path(item).is_absolute():
            continue
        try:
            resolved = Path(item).resolve(strict=False)
        except OSError:
            continue
        if resolved == repo_root or repo_root in resolved.parents:
            continue
        safe_path.append(item)
    environment["PATH"] = os.pathsep.join(safe_path)
    environment["NoDefaultCurrentDirectoryInExePath"] = "1"
    environment["PYTHONDONTWRITEBYTECODE"] = "1"
    if os.name == "nt":
        environment["PATHEXT"] = ".COM;.EXE;.BAT;.CMD"
        environment["COMSPEC"] = _trusted_shell_executable()
    return environment

def run_frozen_checks(commands: list[str], repo_root: Path) -> list[dict[str, Any]]:
    observations: list[dict[str, Any]] = []
    for command in commands:
        try:
            completed = subprocess.run(
                command,
                cwd=repo_root,
                shell=True,
                executable=_trusted_shell_executable(),
                check=False,
                capture_output=True,
                timeout=600,
                env=_sanitized_check_environment(repo_root),
            )
            exit_code = completed.returncode
            stdout = completed.stdout
            stderr = completed.stderr
        except subprocess.TimeoutExpired as exc:
            exit_code = 124
            stdout = exc.stdout or b""
            stderr = exc.stderr or b""
        observations.append(
            {
                "command": command,
                "exit_code": exit_code,
                "stdout_sha256": hashlib.sha256(stdout).hexdigest(),
                "stderr_sha256": hashlib.sha256(stderr).hexdigest(),
            }
        )
    return observations


def _validate_discussion_policy(packet: dict[str, Any], errors: list[str]) -> None:
    coordination = packet.get("coordination")
    discussion = packet.get("discussion")
    if not _one_of(coordination, VALID_COORDINATION):
        errors.append(f"packet.coordination: expected one of {sorted(VALID_COORDINATION)}")
        return
    if coordination == "independent":
        if discussion is not None:
            errors.append("packet.discussion: independent coordination requires null")
        return
    if packet.get("profile") != "full":
        errors.append("packet.coordination: shared coordination requires the full profile")
    if not isinstance(discussion, dict):
        errors.append("packet.discussion: shared coordination requires an object")
        return
    expected_keys = {
        "mode", "sealed_round1", "rounds", "selection", "relay",
        "adjudicator", "decision_rule", "budget",
    }
    if set(discussion) != expected_keys:
        errors.append(f"packet.discussion: keys must equal {sorted(expected_keys)}")
    if discussion.get("mode") != "shared":
        errors.append("packet.discussion.mode: must be shared")
    if discussion.get("sealed_round1") is not True:
        errors.append("packet.discussion.sealed_round1: must be true")
    expected_rounds = ["independent-position", "peer-challenge", "evidence-adjudication"]
    if discussion.get("rounds") != expected_rounds:
        errors.append(f"packet.discussion.rounds: must equal {expected_rounds}")
    expected_selection = {
        "owner": "active-main-model",
        "decided_at_runtime": True,
        "skill_prescribes_count": False,
    }
    if not _strict_json_equal(discussion.get("selection"), expected_selection):
        errors.append("packet.discussion.selection: the active main model must choose runtime participants")
    if discussion.get("relay") != (
        "Main thread relays the complete structured peer board between the same participants."
    ):
        errors.append("packet.discussion.relay: must preserve the complete board for every participant")
    if discussion.get("adjudicator") != "main-thread":
        errors.append("packet.discussion.adjudicator: must be main-thread")
    if discussion.get("decision_rule") != "Resolve claims by discriminating evidence, never by vote or confidence.":
        errors.append("packet.discussion.decision_rule: must be evidence-based and non-voting")
    if not _strict_json_equal(discussion.get("budget"), EXPECTED_DISCUSSION_BUDGET):
        errors.append("packet.discussion.budget: must match the bounded execution policy")


def _validate_delegation(
    value: Any,
    packet_digest: str,
    expected_lanes: set[str],
    errors: list[str],
) -> dict[str, set[str]]:
    location = "report.deliberation.delegation"
    if not isinstance(value, dict):
        errors.append(f"{location}: must be an object")
        return {}
    expected_keys = {
        "selected_by", "sealed_before_round1", "packet_sha256", "participants"
    }
    if set(value) != expected_keys:
        errors.append(f"{location}: keys must equal {sorted(expected_keys)}")
    if value.get("selected_by") != "active-main-model":
        errors.append(f"{location}.selected_by: must be active-main-model")
    if value.get("sealed_before_round1") is not True:
        errors.append(f"{location}.sealed_before_round1: must be true")
    if value.get("packet_sha256") != packet_digest:
        errors.append(f"{location}.packet_sha256: must match the frozen packet")
    participants = value.get("participants")
    if not isinstance(participants, list) or len(participants) < 2:
        errors.append(f"{location}.participants: shared discussion needs at least two runtime-selected participants")
        return {}
    assignments: dict[str, set[str]] = {}
    assigned_lanes: list[str] = []
    for index, participant in enumerate(participants):
        participant_location = f"{location}.participants[{index}]"
        if not isinstance(participant, dict):
            errors.append(f"{participant_location}: must be an object")
            continue
        expected_participant_keys = {"id", "lane_ids", "round1_prompt", "round2_prompt"}
        if set(participant) != expected_participant_keys:
            errors.append(
                f"{participant_location}: keys must equal {sorted(expected_participant_keys)}"
            )
        participant_id = participant.get("id")
        if not runtime_identity(participant_id):
            errors.append(f"{participant_location}.id: must be a safe runtime identifier")
            continue
        if participant_id in assignments:
            errors.append(f"{participant_location}.id: duplicate participant id")
            continue
        lane_ids = participant.get("lane_ids")
        if not _string_list(lane_ids):
            errors.append(f"{participant_location}.lane_ids: must be a non-empty string list")
            assignments[participant_id] = set()
        else:
            lane_set = set(lane_ids)
            if len(lane_ids) != len(lane_set):
                errors.append(f"{participant_location}.lane_ids: duplicate lane ids")
            unknown = sorted(lane_set - expected_lanes)
            if unknown:
                errors.append(f"{participant_location}.lane_ids: unknown lanes {unknown}")
            assignments[participant_id] = lane_set
            assigned_lanes.extend(lane_ids)
        round1_prompt = participant.get("round1_prompt")
        round2_prompt = participant.get("round2_prompt")
        if _string_list(lane_ids):
            expected_round1, expected_round2 = build_participant_prompts(
                participant_id, lane_ids, packet_digest
            )
            if round1_prompt != expected_round1:
                errors.append(
                    f"{participant_location}.round1_prompt: must match the canonical assignment prompt"
                )
            if round2_prompt != expected_round2:
                errors.append(
                    f"{participant_location}.round2_prompt: must match the canonical relay prompt"
                )

    missing_lanes = sorted(expected_lanes - set(assigned_lanes))
    if missing_lanes:
        errors.append(f"{location}.participants: unassigned lanes {missing_lanes}")
    return assignments

def _validate_operation(
    value: Any, assignments: dict[str, set[str]], errors: list[str]
) -> None:
    location = "report.deliberation.operation"
    if not isinstance(value, dict):
        errors.append(f"{location}: must be an object")
        return
    expected_keys = {
        "round_seconds_by_participant",
        "turns_completed",
        "retries_by_participant",
        "timed_out_participants",
        "cancelled_after_timeout",
        "late_results_discarded",
        "nested_agents_spawned",
        "writes_detected",
    }
    if set(value) != expected_keys:
        errors.append(f"{location}: keys must equal {sorted(expected_keys)}")
    expected_rounds = {"independent-position", "peer-challenge"}
    round_seconds = value.get("round_seconds_by_participant")
    if not isinstance(round_seconds, dict) or set(round_seconds) != set(assignments):
        errors.append(
            f"{location}.round_seconds_by_participant: must cover every participant exactly once"
        )
    else:
        for participant_id, timings in round_seconds.items():
            if not isinstance(timings, dict) or set(timings) != expected_rounds:
                errors.append(
                    f"{location}.round_seconds_by_participant.{participant_id}: must record both rounds"
                )
                continue
            for round_name, seconds in timings.items():
                if (
                    not isinstance(seconds, int)
                    or isinstance(seconds, bool)
                    or not (
                        0
                        <= seconds
                        <= EXPECTED_DISCUSSION_BUDGET[
                            "max_round_seconds_per_participant"
                        ]
                    )
                ):
                    errors.append(
                        f"{location}.round_seconds_by_participant.{participant_id}.{round_name}: outside the budget"
                    )
    turns = value.get("turns_completed")
    if not isinstance(turns, dict) or set(turns) != set(assignments):
        errors.append(f"{location}.turns_completed: must cover every participant exactly once")
    else:
        for participant_id, count in turns.items():
            if (
                not isinstance(count, int)
                or isinstance(count, bool)
                or count != EXPECTED_DISCUSSION_BUDGET["max_turns_per_participant"]
            ):
                errors.append(f"{location}.turns_completed.{participant_id}: must equal integer 2")
    retries = value.get("retries_by_participant")
    if not isinstance(retries, dict) or set(retries) != set(assignments):
        errors.append(
            f"{location}.retries_by_participant: must cover every participant exactly once"
        )
    else:
        for participant_id, count in retries.items():
            if (
                not isinstance(count, int)
                or isinstance(count, bool)
                or not (
                    0
                    <= count
                    <= EXPECTED_DISCUSSION_BUDGET["max_retries_per_participant"]
                )
            ):
                errors.append(
                    f"{location}.retries_by_participant.{participant_id}: outside the budget"
                )

    participant_lists: dict[str, set[str]] = {}
    for field in (
        "timed_out_participants",
        "cancelled_after_timeout",
        "late_results_discarded",
    ):
        items = value.get(field)
        if not isinstance(items, list) or not all(
            _nonempty_string(item) and item in assignments for item in items
        ):
            errors.append(f"{location}.{field}: must be a participant-id list")
            participant_lists[field] = set()
        else:
            participant_lists[field] = set(items)
            if len(items) != len(participant_lists[field]):
                errors.append(f"{location}.{field}: duplicate participant ids")
    timed_out = participant_lists["timed_out_participants"]
    if participant_lists["cancelled_after_timeout"] != timed_out:
        errors.append(f"{location}.cancelled_after_timeout: must exactly match timed-out participants")
    if not participant_lists["late_results_discarded"] <= timed_out:
        errors.append(f"{location}.late_results_discarded: must reference timed-out participants")
    if value.get("nested_agents_spawned") is not False:
        errors.append(f"{location}.nested_agents_spawned: must be false")
    if value.get("writes_detected") is not False:
        errors.append(f"{location}.writes_detected: must be false")



def _validate_deliberation(
    value: Any,
    packet_digest: str,
    expected_lanes: set[str],
    errors: list[str],
) -> list[tuple[str, str]]:
    challenge_checks: list[tuple[str, str]] = []
    if not isinstance(value, dict):
        errors.append("report.deliberation: shared coordination requires an object")
        return challenge_checks
    if value.get("mode") != "shared":
        errors.append("report.deliberation.mode: must be shared")
    if value.get("sealed_before_exchange") is not True:
        errors.append("report.deliberation.sealed_before_exchange: must be true")
    expected_keys = {
        "mode",
        "sealed_before_exchange",
        "peer_board_sha256",
        "deliveries",
        "initial_positions",
        "challenges",
        "adjudications",
        "delegation",
        "operation",
    }
    if set(value) != expected_keys:
        errors.append(f"report.deliberation: keys must equal {sorted(expected_keys)}")
    assignments = _validate_delegation(
        value.get("delegation"), packet_digest, expected_lanes, errors
    )
    _validate_operation(value.get("operation"), assignments, errors)


    positions = value.get("initial_positions")
    if not isinstance(positions, list) or not positions:
        errors.append("report.deliberation.initial_positions: must be a non-empty list")
        positions = []
    board_bytes = _canonical_json_bytes({"initial_positions": positions})
    board_digest = hashlib.sha256(board_bytes).hexdigest()

    if value.get("peer_board_sha256") != board_digest:
        errors.append("report.deliberation.peer_board_sha256: must match the canonical position board")
    deliveries = value.get("deliveries")
    if not isinstance(deliveries, list):
        errors.append("report.deliberation.deliveries: must be a list")
        deliveries = []
    delivered_to: list[str] = []
    for index, delivery in enumerate(deliveries):
        location = f"report.deliberation.deliveries[{index}]"
        if not isinstance(delivery, dict):
            errors.append(f"{location}: must be an object")
            continue
        _require_exact_keys(
            delivery,
            {"participant_id", "peer_board_sha256"},
            location,
            errors,
        )
        participant_id = delivery.get("participant_id")
        if not _nonempty_string(participant_id) or participant_id not in assignments:
            errors.append(f"{location}.participant_id: must reference a discussion participant")
        else:
            delivered_to.append(participant_id)
        if delivery.get("peer_board_sha256") != board_digest:
            errors.append(f"{location}.peer_board_sha256: must match the canonical position board")
    duplicate_deliveries = sorted(
        item for item, count in Counter(delivered_to).items() if count > 1
    )
    if duplicate_deliveries:
        errors.append(f"report.deliberation.deliveries: duplicate participants {duplicate_deliveries}")
    missing_deliveries = sorted(set(assignments) - set(delivered_to))
    if missing_deliveries:
        errors.append(f"report.deliberation.deliveries: missing participants {missing_deliveries}")
    position_authors: dict[str, str] = {}
    position_lanes: set[str] = set()
    authors_with_positions: set[str] = set()
    position_bytes_by_author: Counter[str] = Counter()
    for index, position in enumerate(positions):
        location = f"report.deliberation.initial_positions[{index}]"
        if not isinstance(position, dict):
            errors.append(f"{location}: must be an object")
            continue
        _require_exact_keys(
            position, {"id", "author", "lens_ids", "claim", "evidence"}, location, errors
        )
        position_id = position.get("id")
        author = position.get("author")
        if not _nonempty_string(position_id):
            errors.append(f"{location}.id: must be non-empty")
        elif position_id in position_authors:
            errors.append(f"{location}.id: duplicate position id")
        elif _nonempty_string(author) and author in assignments:
            position_authors[position_id] = author
        if not _nonempty_string(author) or author not in assignments:
            errors.append(f"{location}.author: must reference a discussion participant")
        else:
            authors_with_positions.add(author)
            position_bytes_by_author[author] += len(_canonical_json_bytes(position))
        lens_ids = position.get("lens_ids")
        if not _string_list(lens_ids):
            errors.append(f"{location}.lens_ids: must be a non-empty string list")
        elif _nonempty_string(author) and author in assignments:
            lens_set = set(lens_ids)
            unowned = sorted(lens_set - assignments[author])
            if unowned:
                errors.append(f"{location}.lens_ids: author does not own lanes {unowned}")
            position_lanes.update(lens_set)
        if not _concrete_string(position.get("claim")):
            errors.append(f"{location}.claim: must be concrete")
        _validate_evidence(position.get("evidence"), location, errors)
    per_participant_position_limit = EXPECTED_DISCUSSION_BUDGET[
        "max_position_bytes_per_participant"
    ]
    for author, byte_count in position_bytes_by_author.items():
        if byte_count > per_participant_position_limit:
            errors.append(
                "report.deliberation.initial_positions: "
                f"{author} exceeds the per-participant position byte limit"
            )
    missing_authors = sorted(set(assignments) - authors_with_positions)
    if missing_authors:
        errors.append(f"report.deliberation.initial_positions: participants without a position {missing_authors}")
    missing_position_lanes = sorted(expected_lanes - position_lanes)
    if missing_position_lanes:
        errors.append(f"report.deliberation.initial_positions: uncovered lanes {missing_position_lanes}")

    challenges = value.get("challenges")
    if not isinstance(challenges, list) or not challenges:
        errors.append("report.deliberation.challenges: must be a non-empty list")
        challenges = []
    challenge_ids: set[str] = set()
    challenge_authors: set[str] = set()
    for index, challenge in enumerate(challenges):
        location = f"report.deliberation.challenges[{index}]"
        if not isinstance(challenge, dict):
            errors.append(f"{location}: must be an object")
            continue
        _require_exact_keys(
            challenge,
            {
                "id", "author", "target_position_id", "stance",
                "falsification_attempt", "reason", "evidence",
                "discriminating_check",
            },
            location,
            errors,
        )
        challenge_id = challenge.get("id")
        author = challenge.get("author")
        target_id = challenge.get("target_position_id")
        if not _nonempty_string(challenge_id):
            errors.append(f"{location}.id: must be non-empty")
        elif challenge_id in challenge_ids:
            errors.append(f"{location}.id: duplicate challenge id")
        else:
            challenge_ids.add(challenge_id)
        if not _nonempty_string(author) or author not in assignments:
            errors.append(f"{location}.author: must reference a discussion participant")
        else:
            challenge_authors.add(author)
        if not _nonempty_string(target_id) or target_id not in position_authors:
            errors.append(f"{location}.target_position_id: must reference an initial position")
        elif position_authors[target_id] == author:
            errors.append(f"{location}.target_position_id: must target a peer position")
        if not _one_of(challenge.get("stance"), VALID_STANCES):
            errors.append(f"{location}.stance: expected one of {sorted(VALID_STANCES)}")
        if not _concrete_string(challenge.get("reason")):
            errors.append(f"{location}.reason: must be concrete")
        if not _concrete_string(challenge.get("falsification_attempt")):
            errors.append(f"{location}.falsification_attempt: must be concrete")
        _validate_evidence(challenge.get("evidence"), location, errors)
        discriminating_check = challenge.get("discriminating_check")
        if not _concrete_string(discriminating_check):
            errors.append(f"{location}.discriminating_check: must be concrete")
        else:
            challenge_checks.append((location, discriminating_check))
    missing_challengers = sorted(set(assignments) - challenge_authors)
    if missing_challengers:
        errors.append(f"report.deliberation.challenges: participants without a peer challenge {missing_challengers}")

    adjudications = value.get("adjudications")
    if not isinstance(adjudications, list) or not adjudications:
        errors.append("report.deliberation.adjudications: must be a non-empty list")
        adjudications = []
    adjudicated: list[str] = []
    for index, adjudication in enumerate(adjudications):
        location = f"report.deliberation.adjudications[{index}]"
        if not isinstance(adjudication, dict):
            errors.append(f"{location}: must be an object")
            continue
        _require_exact_keys(
            adjudication, {"challenge_ids", "resolution", "evidence"}, location, errors
        )
        ids = adjudication.get("challenge_ids")
        if not _string_list(ids):
            errors.append(f"{location}.challenge_ids: must be a non-empty string list")
        else:
            adjudicated.extend(ids)
            unknown = sorted(set(ids) - challenge_ids)
            if unknown:
                errors.append(f"{location}.challenge_ids: unknown challenges {unknown}")
        if not _concrete_string(adjudication.get("resolution")):
            errors.append(f"{location}.resolution: must be concrete")
        _validate_evidence(adjudication.get("evidence"), location, errors)
    duplicate_adjudications = sorted(item for item, count in Counter(adjudicated).items() if count > 1)
    if duplicate_adjudications:
        errors.append(f"report.deliberation.adjudications: duplicate challenge resolutions {duplicate_adjudications}")
    unresolved = sorted(challenge_ids - set(adjudicated))
    if unresolved:
        errors.append(f"report.deliberation.adjudications: unresolved challenges {unresolved}")
    return challenge_checks


def _expected_execution_policy(intent: str) -> dict[str, Any]:
    return {
        "implementation_required": intent in CODING_INTENTS,
        "editing_owner": "main-thread",
        "analysis_agents_read_only": True,
        "write_scope_source": "frozen-contract",
        "acceptance_source": "frozen-contract",
        "ponytail_level": "full",
        "minimalism_ladder": [
            "not-needed",
            "reuse",
            "stdlib",
            "native",
            "existing-dependency",
            "minimal-custom",
        ],
    }


def _validate_implementation(
    value: Any,
    intent: Any,
    contract: dict[str, Any],
    observed_changed_paths: Any,
    errors: list[str],
) -> list[str]:
    contract_acceptance = contract.get("acceptance", [])
    required_commands = [item.get("command") for item in contract_acceptance if isinstance(item, dict)]
    scope = contract.get("scope", {})
    path_case = scope.get("path_case", {}).get("value", "sensitive")
    path_flavor = scope.get("path_flavor", {}).get("value", "posix")
    observed = (
        [repo_path(item, path_flavor) for item in observed_changed_paths]
        if isinstance(observed_changed_paths, list)
        else []
    )
    observed_keys = [
        scope_path_key(item, path_case, path_flavor) for item in observed if item is not None
    ]
    if (
        not isinstance(observed_changed_paths, list)
        or any(item is None for item in observed)
        or len(observed_keys) != len(set(observed_keys))
    ):
        errors.append(
            "observed_changed_paths: trusted observation must be a unique relative path list"
        )
    if intent == "review":
        if value is not None:
            errors.append("report.implementation: review intent requires null")
        if observed:
            errors.append("observed_changed_paths: review intent requires no repository changes")
        return required_commands
    if not _one_of(intent, CODING_INTENTS):
        return required_commands
    location = "report.implementation"
    if not isinstance(value, dict):
        errors.append(f"{location}: {intent} intent requires an object")
        return required_commands
    expected_keys = {
        "status", "owner", "changed_paths", "no_change_reason",
        "root_cause",
        "minimalism", "acceptance_results",
    }
    if set(value) != expected_keys:
        errors.append(f"{location}: keys must equal {sorted(expected_keys)}")
    status = value.get("status")
    if not _one_of(status, VALID_IMPLEMENTATION_STATUS):
        errors.append(f"{location}.status: expected one of {sorted(VALID_IMPLEMENTATION_STATUS)}")
    if value.get("owner") != "main-thread":
        errors.append(f"{location}.owner: must be main-thread")

    changed_raw = value.get("changed_paths")
    changed = (
        [repo_path(item, path_flavor) for item in changed_raw]
        if isinstance(changed_raw, list)
        else []
    )
    changed_keys = [scope_path_key(item, path_case, path_flavor) for item in changed if item is not None]
    if not isinstance(changed_raw, list) or any(
        item is None for item in changed
    ):
        errors.append(
            f"{location}.changed_paths: must be concrete relative repository paths"
        )
    if len(changed_keys) != len(set(changed_keys)):
        errors.append(f"{location}.changed_paths: duplicate paths")
    if sorted(changed_keys) != sorted(observed_keys):
        errors.append(
            f"{location}.changed_paths: must equal the controller-observed repository diff"
        )
    if status == "changed" and not observed:
        errors.append(f"{location}.status: changed requires an observed repository change")
    if status == "no-change" and observed:
        errors.append(f"{location}.status: no-change contradicts the observed repository diff")
    reason = value.get("no_change_reason")
    if status == "no-change" and not _concrete_string(reason):
        errors.append(f"{location}.no_change_reason: no-change status needs a concrete reason")
    if status == "changed" and reason is not None:
        errors.append(f"{location}.no_change_reason: changed status requires null")
    allowed_values = [
        item.get("path")
        for item in scope.get("allowed_write_paths", [])
        if isinstance(item, dict)
    ]
    forbidden_values = [
        item.get("path")
        for item in scope.get("forbidden_write_paths", [])
        if isinstance(item, dict)
    ]
    for path in (item for item in observed if item is not None):
        path_key = scope_path_key(path, path_case, path_flavor)
        allowed = any(
            (root_key := scope_path_key(root, path_case, path_flavor)) == "."
            or path_key == root_key
            or path_key.startswith(root_key.rstrip("/") + "/")
            for root in allowed_values
        )
        forbidden = any(
            (root_key := scope_path_key(root, path_case, path_flavor)) == "."
            or path_key == root_key
            or path_key.startswith(root_key.rstrip("/") + "/")
            for root in forbidden_values
        )
        if not allowed or forbidden:
            errors.append(
                f"{location}.changed_paths: path outside frozen contract scope {path!r}"
            )

    root_cause = value.get("root_cause")
    if intent == "change" and root_cause is not None:
        errors.append(f"{location}.root_cause: change intent requires null")
    if intent == "debug":
        if not isinstance(root_cause, dict) or set(root_cause) != {"claim", "evidence", "reproduction_command"}:
            errors.append(f"{location}.root_cause: debug intent needs claim, evidence, and reproduction_command")
        else:
            if not _concrete_string(root_cause.get("claim")):
                errors.append(f"{location}.root_cause.claim: must be concrete")
            _validate_evidence(root_cause.get("evidence"), f"{location}.root_cause", errors)
            command = root_cause.get("reproduction_command")
            if not _concrete_string(command):
                errors.append(f"{location}.root_cause.reproduction_command: must be concrete")
            elif command not in required_commands:
                errors.append(
                    f"{location}.root_cause.reproduction_command: must be frozen in acceptance"
                )

    minimalism = value.get("minimalism")
    if not isinstance(minimalism, dict) or set(minimalism) != {
        "source", "level", "selected_rung", "rejected_complexity", "safety_preserved"
    }:
        errors.append(f"{location}.minimalism: must contain the complete minimalism decision")
    else:
        if not _one_of(minimalism.get("source"), VALID_MINIMALISM_SOURCES):
            errors.append(f"{location}.minimalism.source: expected one of {sorted(VALID_MINIMALISM_SOURCES)}")
        if minimalism.get("level") != "full":
            errors.append(f"{location}.minimalism.level: must match frozen full intensity")
        if not _one_of(minimalism.get("selected_rung"), VALID_MINIMALISM_RUNGS):
            errors.append(f"{location}.minimalism.selected_rung: expected one of {sorted(VALID_MINIMALISM_RUNGS)}")
        if status == "no-change" and minimalism.get("selected_rung") != "not-needed":
            errors.append(f"{location}.minimalism.selected_rung: no-change requires not-needed")
        if status == "changed" and minimalism.get("selected_rung") == "not-needed":
            errors.append(f"{location}.minimalism.selected_rung: changed cannot use not-needed")
        rejected = minimalism.get("rejected_complexity")
        if not isinstance(rejected, list) or not all(_nonempty_string(item) for item in rejected):
            errors.append(f"{location}.minimalism.rejected_complexity: must be a string list")
        if not _string_list(minimalism.get("safety_preserved")):
            errors.append(f"{location}.minimalism.safety_preserved: must be a non-empty string list")

    acceptance_results = value.get("acceptance_results")
    expected_ids = {
        item.get("id")
        for item in contract_acceptance
        if isinstance(item, dict) and _nonempty_string(item.get("id"))
    }
    if not isinstance(acceptance_results, list) or not acceptance_results:
        errors.append(f"{location}.acceptance_results: must be a non-empty list")
    else:
        result_ids: list[str] = []
        for index, item in enumerate(acceptance_results):
            item_location = f"{location}.acceptance_results[{index}]"
            if not isinstance(item, dict) or set(item) != {"criterion_id", "evidence_ref"}:
                errors.append(f"{item_location}: must contain criterion_id and evidence_ref")
                continue
            criterion_id = item.get("criterion_id")
            if not _nonempty_string(criterion_id) or criterion_id not in expected_ids:
                errors.append(f"{item_location}.criterion_id: must reference frozen acceptance")
            else:
                result_ids.append(criterion_id)
            if not _concrete_string(item.get("evidence_ref")):
                errors.append(f"{item_location}.evidence_ref: must be concrete")
        if len(result_ids) != len(set(result_ids)):
            errors.append(f"{location}.acceptance_results: duplicate criterion ids")
        missing_ids = sorted(expected_ids - set(result_ids))
        if missing_ids:
            errors.append(f"{location}.acceptance_results: missing frozen criteria {missing_ids}")
    if any(not _concrete_string(command) for command in required_commands):
        errors.append("contract.acceptance: every frozen command must be concrete")
    return required_commands


def _validate_observed_check_results(
    value: Any,
    required_commands: list[str],
    errors: list[str],
) -> dict[str, dict[str, Any]]:
    location = "observed_check_results"
    if not isinstance(value, list):
        errors.append(f"{location}: trusted controller observations are required")
        return {}
    observations: dict[str, dict[str, Any]] = {}
    for index, item in enumerate(value):
        item_location = f"{location}[{index}]"
        if not isinstance(item, dict) or set(item) != {
            "command", "exit_code", "stdout_sha256", "stderr_sha256"
        }:
            errors.append(f"{item_location}: invalid observation shape")
            continue
        command = item.get("command")
        exit_code = item.get("exit_code")
        if not _concrete_string(command):
            errors.append(f"{item_location}.command: must be concrete")
            continue
        if command in observations:
            errors.append(f"{item_location}.command: duplicate observed command")
            continue
        if not isinstance(exit_code, int) or isinstance(exit_code, bool):
            errors.append(f"{item_location}.exit_code: must be an integer")
        for field in ("stdout_sha256", "stderr_sha256"):
            digest = item.get(field)
            if not isinstance(digest, str) or re.fullmatch(r"[0-9a-f]{64}", digest) is None:
                errors.append(f"{item_location}.{field}: must be a SHA-256 digest")
        observations[command] = item
    required = set(required_commands)
    observed = set(observations)
    missing = sorted(required - observed)
    extra = sorted(observed - required)
    if missing:
        errors.append(f"{location}: missing frozen commands {missing}")
    if extra:
        errors.append(f"{location}: contains non-frozen commands {extra}")
    return observations


def validate_packet_preflight(
    packet: Any, expected_packet_sha256: str | None
) -> list[str]:
    """Reject an invalid frozen packet before any acceptance command can run."""
    errors: list[str] = []
    if not isinstance(packet, dict):
        return ["packet: must be an object"]
    try:
        _canonical_json_bytes(packet)
    except (RecursionError, TypeError, UnicodeError, ValueError) as exc:
        return [f"packet: must be canonicalizable JSON ({exc})"]

    expected_keys = {
        "version",
        "contract",
        "contract_sha256",
        "packet_sha256",
        "risk",
        "profile",
        "coordination",
        "planner",
        "independence",
        "execution_policy",
        "discussion",
        "lanes",
        "synthesis_gate",
    }
    if set(packet) != expected_keys:
        errors.append(f"packet: keys must equal {sorted(expected_keys)}")
    if type(packet.get("version")) is not int or packet["version"] != 4:
        errors.append("packet.version: must be integer 4")
    actual_digest = packet_sha256(packet)
    if packet.get("packet_sha256") != actual_digest:
        errors.append("packet.packet_sha256: must match the canonical packet")
    if (
        not isinstance(expected_packet_sha256, str)
        or re.fullmatch(r"[0-9a-f]{64}", expected_packet_sha256) is None
        or expected_packet_sha256 != actual_digest
    ):
        errors.append("expected_packet_sha256: must match the trusted frozen packet")

    contract: dict[str, Any] = {}
    try:
        contract = freeze_contract(packet.get("contract"))
    except ValueError as exc:
        errors.append(f"packet.contract: {exc}")
    if contract and not _strict_json_equal(packet.get("contract"), contract):
        errors.append("packet.contract: must equal the canonical frozen contract")
    if contract and packet.get("contract_sha256") != contract_sha256(contract):
        errors.append("packet.contract_sha256: must match the canonical contract")

    risk = packet.get("risk")
    profile = packet.get("profile")
    coordination = packet.get("coordination")
    if not _one_of(risk, VALID_RISKS):
        errors.append(f"packet.risk: expected one of {sorted(VALID_RISKS)}")
    if not _one_of(profile, VALID_PROFILES):
        errors.append(f"packet.profile: expected one of {sorted(VALID_PROFILES)}")
    if not _one_of(coordination, VALID_COORDINATION):
        errors.append(
            f"packet.coordination: expected one of {sorted(VALID_COORDINATION)}"
        )
    planner = packet.get("planner")
    planner_valid = isinstance(planner, dict) and set(planner) == {
        "seed",
        "catalog_sha256",
    }
    if not planner_valid:
        errors.append("packet.planner: keys must equal ['catalog_sha256', 'seed']")
    else:
        seed = planner.get("seed")
        catalog_digest = planner.get("catalog_sha256")
        if not isinstance(seed, str) or not seed or len(seed) > 256:
            errors.append(
                "packet.planner.seed: must be a non-empty string of at most 256 characters"
            )
            planner_valid = False
        if (
            not isinstance(catalog_digest, str)
            or re.fullmatch(r"[0-9a-f]{64}", catalog_digest) is None
        ):
            errors.append("packet.planner.catalog_sha256: must be a SHA-256 digest")
            planner_valid = False

    if (
        contract
        and planner_valid
        and _one_of(risk, VALID_RISKS)
        and _one_of(profile, VALID_PROFILES)
        and _one_of(coordination, VALID_COORDINATION)
    ):
        try:
            expected_packet = build_packet(
                contract,
                risk=risk,
                seed=planner["seed"],
                profile=profile,
                coordination=coordination,
            )
        except ValueError as exc:
            errors.append(f"packet.planner: cannot reconstruct packet: {exc}")
        else:
            messages = {
                "planner": "must match deterministic planner inputs and catalog",
                "independence": "must match required policy",
                "execution_policy": "must match deterministic execution policy",
                "discussion": "must match deterministic coordination policy",
                "lanes": "must match deterministic lane output",
                "synthesis_gate": "must match required policy",
            }
            for field, message in messages.items():
                if not _strict_json_equal(
                    packet.get(field), expected_packet.get(field)
                ):
                    errors.append(f"packet.{field}: {message}")
    _validate_discussion_policy(packet, errors)
    return errors


def evaluate(
    packet: Any,
    report: Any,
    expected_packet_sha256: str | None = None,
    observed_changed_paths: Any = None,
    observed_check_results: Any = None,
) -> dict[str, Any]:
    errors: list[str] = []
    if not isinstance(packet, dict):
        return {"passed": False, "errors": ["packet: must be an object"]}
    if not isinstance(report, dict):
        return {"passed": False, "errors": ["report: must be an object"]}
    try:
        _canonical_json_bytes(packet)
        _canonical_json_bytes(report)
    except (RecursionError, TypeError, UnicodeError, ValueError) as exc:
        return {
            "passed": False,
            "errors": [f"packet/report: must be canonicalizable JSON ({exc})"],
        }

    expected_packet_keys = {
        "version", "contract", "contract_sha256", "packet_sha256", "risk",
        "profile", "coordination", "planner", "independence",
        "execution_policy", "discussion", "lanes", "synthesis_gate",
    }
    if set(packet) != expected_packet_keys:
        errors.append(f"packet: keys must equal {sorted(expected_packet_keys)}")
    packet_version = packet.get("version")
    if (
        not isinstance(packet_version, int)
        or isinstance(packet_version, bool)
        or packet_version != 4
    ):
        errors.append("packet.version: must be integer 4")
    actual_packet_digest = packet_sha256(packet)
    embedded_packet_digest = packet.get("packet_sha256")
    if embedded_packet_digest != actual_packet_digest:
        errors.append("packet.packet_sha256: must match the canonical packet")
    if (
        not isinstance(expected_packet_sha256, str)
        or re.fullmatch(r"[0-9a-f]{64}", expected_packet_sha256) is None
    ):
        errors.append("expected_packet_sha256: trusted preflight digest is required")
    elif expected_packet_sha256 != actual_packet_digest:
        errors.append("expected_packet_sha256: does not match the frozen packet")

    contract: dict[str, Any] = {}
    try:
        contract = freeze_contract(packet.get("contract"))
    except ValueError as exc:
        errors.append(f"packet.contract: {exc}")
    if contract and not _strict_json_equal(packet.get("contract"), contract):
        errors.append("packet.contract: must equal the canonical frozen contract")
    if contract and packet.get("contract_sha256") != contract_sha256(contract):
        errors.append("packet.contract_sha256: must match the canonical contract")
    packet_intent = contract.get("intent", {}).get("value") if contract else None

    packet_lanes = packet.get("lanes")
    if not isinstance(packet_lanes, list) or not packet_lanes:
        return {"passed": False, "errors": ["packet.lanes: must be a non-empty list"]}
    lane_ids = [lane.get("id") for lane in packet_lanes if isinstance(lane, dict)]
    if len(lane_ids) != len(packet_lanes) or not all(_nonempty_string(item) for item in lane_ids):
        return {"passed": False, "errors": ["packet.lanes: every lane needs a non-empty id"]}
    if len(lane_ids) != len(set(lane_ids)):
        return {"passed": False, "errors": ["packet.lanes: duplicate lane ids"]}
    expected_lanes = set(lane_ids)

    packet_risk = packet.get("risk")
    if not _one_of(packet_risk, VALID_RISKS):
        errors.append(f"packet.risk: expected one of {sorted(VALID_RISKS)}")
    if not _one_of(packet.get("profile"), VALID_PROFILES):
        errors.append(f"packet.profile: expected one of {sorted(VALID_PROFILES)}")
    if packet.get("profile") == "light" and packet_risk != "low":
        errors.append("packet.profile: light is allowed only for low risk")
    if not _one_of(packet_intent, VALID_INTENTS):
        errors.append(f"packet.contract.intent.value: expected one of {sorted(VALID_INTENTS)}")
    elif not _strict_json_equal(
        packet.get("execution_policy"), _expected_execution_policy(packet_intent)
    ):
        errors.append("packet.execution_policy: must match the intent execution policy")

    planner = packet.get("planner")
    planner_valid = isinstance(planner, dict) and set(planner) == {
        "seed", "catalog_sha256"
    }
    if not planner_valid:
        errors.append("packet.planner: keys must equal ['catalog_sha256', 'seed']")
    else:
        seed = planner.get("seed")
        if not isinstance(seed, str) or not seed or len(seed) > 256:
            errors.append("packet.planner.seed: must be a non-empty string of at most 256 characters")
            planner_valid = False
        catalog_digest = planner.get("catalog_sha256")
        if not isinstance(catalog_digest, str) or re.fullmatch(r"[0-9a-f]{64}", catalog_digest) is None:
            errors.append("packet.planner.catalog_sha256: must be a SHA-256 digest")
            planner_valid = False

    if (
        contract
        and planner_valid
        and _one_of(packet_risk, VALID_RISKS)
        and _one_of(packet.get("profile"), VALID_PROFILES)
        and _one_of(packet.get("coordination"), VALID_COORDINATION)
    ):
        try:
            expected_packet = build_packet(
                contract,
                risk=packet_risk,
                seed=planner["seed"],
                profile=packet["profile"],
                coordination=packet["coordination"],
            )
        except ValueError as exc:
            errors.append(f"packet.planner: cannot reconstruct packet: {exc}")
        else:
            derived_messages = {
                "planner": "must match deterministic planner inputs and catalog",
                "independence": "must match required policy",
                "execution_policy": "must match deterministic execution policy",
                "discussion": "must match deterministic coordination policy",
                "lanes": "must match deterministic lane output",
                "synthesis_gate": "must match required policy",
            }
            for field, message in derived_messages.items():
                if not _strict_json_equal(packet.get(field), expected_packet.get(field)):
                    errors.append(f"packet.{field}: {message}")

    _validate_discussion_policy(packet, errors)
    deliberation_checks: list[tuple[str, str]] = []
    if packet.get("coordination") == "shared":
        deliberation_checks = _validate_deliberation(
            report.get("deliberation"), actual_packet_digest, expected_lanes, errors
        )
    elif report.get("deliberation") is not None:
        errors.append(
            "report.deliberation: allowed only when packet coordination is shared"
        )

    expected_report_keys = {
        "packet_sha256", "coordination", "risk", "intent", "implementation",
        "coverage", "findings", "disagreements", "checks", "residual_risks",
    }
    if packet.get("coordination") == "shared":
        expected_report_keys.add("deliberation")
    if set(report) != expected_report_keys:
        errors.append(f"report: keys must equal {sorted(expected_report_keys)}")
    if report.get("packet_sha256") != actual_packet_digest:
        errors.append("report.packet_sha256: must match the frozen packet")
    if not _strict_json_equal(report.get("coordination"), packet.get("coordination")):
        errors.append("report.coordination: must exactly match packet.coordination")
    if not _strict_json_equal(report.get("risk"), packet.get("risk")):
        errors.append("report.risk: must exactly match packet.risk")

    if not _strict_json_equal(report.get("intent"), packet_intent):
        errors.append("report.intent: must exactly match frozen contract intent")
    implementation_commands = _validate_implementation(
        report.get("implementation"),
        packet_intent,
        contract,
        observed_changed_paths,
        errors,
    )
    observed_checks = _validate_observed_check_results(
        observed_check_results, implementation_commands, errors
    )
    coverage = report.get("coverage")
    if not isinstance(coverage, list):
        errors.append("report.coverage: must be a list")
        coverage = []
    coverage_ids = [
        item.get("lens_id")
        for item in coverage
        if isinstance(item, dict) and _nonempty_string(item.get("lens_id"))
    ]
    counts = Counter(coverage_ids)
    duplicates = sorted(str(item) for item, count in counts.items() if count > 1)
    if duplicates:
        errors.append(f"report.coverage: duplicate lens ids {duplicates}")
    missing = sorted(expected_lanes - set(coverage_ids))
    extra = sorted(set(coverage_ids) - expected_lanes, key=str)
    if missing:
        errors.append(f"report.coverage: missing lanes {missing}")
    if extra:
        errors.append(f"report.coverage: unknown lanes {extra}")

    for index, lane in enumerate(coverage):
        location = f"report.coverage[{index}]"
        if not isinstance(lane, dict):
            errors.append(f"{location}: must be an object")
            continue
        _require_exact_keys(
            lane,
            {
                "lens_id", "status", "summary", "evidence",
                "counterevidence_sought", "unknowns",
            },
            location,
            errors,
        )
        if not _nonempty_string(lane.get("lens_id")):
            errors.append(f"{location}.lens_id: must be a non-empty string")
        if not _one_of(lane.get("status"), VALID_LANE_STATUSES):
            errors.append(f"{location}.status: expected one of {sorted(VALID_LANE_STATUSES)}")
        elif lane["status"] == "blocked":
            errors.append(f"{location}.status: blocked lanes cannot pass")
        if not _nonempty_string(lane.get("summary")):
            errors.append(f"{location}.summary: must be non-empty")
        _validate_evidence(lane.get("evidence"), location, errors)
        if not _string_list(lane.get("counterevidence_sought")):
            errors.append(f"{location}.counterevidence_sought: must contain at least one challenge")
        unknowns = lane.get("unknowns")
        if not isinstance(unknowns, list) or not all(_nonempty_string(item) for item in unknowns):
            errors.append(f"{location}.unknowns: must be a list of non-empty strings")
        elif unknowns:
            errors.append(f"{location}.unknowns: unresolved unknowns require blocked status and cannot pass")

    findings = report.get("findings")
    if not isinstance(findings, list):
        errors.append("report.findings: must be a list")
        findings = []
    finding_ids: list[str] = []
    findings_by_lens: Counter[str] = Counter()
    fixed_verifications: list[tuple[str, list[str]]] = []
    for index, finding in enumerate(findings):
        location = f"report.findings[{index}]"
        if not isinstance(finding, dict):
            errors.append(f"{location}: must be an object")
            continue
        expected_finding_keys = {
            "id", "lens_id", "severity", "claim", "evidence",
            "disposition", "decision",
        }
        if finding.get("disposition") == "fixed":
            expected_finding_keys.add("verification")
        _require_exact_keys(
            finding,
            expected_finding_keys,
            location,
            errors,
        )
        if _nonempty_string(finding.get("id")):
            finding_ids.append(finding["id"])
        if not _nonempty_string(finding.get("id")):
            errors.append(f"{location}.id: must be non-empty")
        if not _nonempty_string(finding.get("lens_id")) or finding.get("lens_id") not in expected_lanes:
            errors.append(f"{location}.lens_id: must reference an emitted lane")
        else:
            findings_by_lens[finding["lens_id"]] += 1
        if not _one_of(finding.get("severity"), VALID_SEVERITIES):
            errors.append(f"{location}.severity: expected one of {sorted(VALID_SEVERITIES)}")
        if not _nonempty_string(finding.get("claim")):
            errors.append(f"{location}.claim: must be non-empty")
        _validate_evidence(finding.get("evidence"), location, errors)
        disposition = finding.get("disposition")
        if not _one_of(disposition, VALID_DISPOSITIONS):
            errors.append(f"{location}.disposition: expected one of {sorted(VALID_DISPOSITIONS)}")
        if disposition == "open" and finding.get("severity") in ("critical", "high"):
            errors.append(f"{location}: critical/high findings cannot remain open")
        if disposition == "accepted" and finding.get("severity") == "critical":
            errors.append(f"{location}: critical findings cannot be accepted")
        if not _concrete_string(finding.get("decision")):
            errors.append(f"{location}.decision: must be a concrete string")
        if disposition == "fixed":
            if not _string_list(finding.get("verification")):
                errors.append(f"{location}.verification: fixed finding needs a verification command")
            else:
                fixed_verifications.append((location, finding["verification"]))
    duplicate_findings = sorted(str(item) for item, count in Counter(finding_ids).items() if count > 1)
    if duplicate_findings:
        errors.append(f"report.findings: duplicate ids {duplicate_findings}")

    for index, lane in enumerate(coverage):
        if not isinstance(lane, dict) or not _nonempty_string(lane.get("lens_id")):
            continue
        count = findings_by_lens[lane["lens_id"]]
        if lane.get("status") == "finding" and count == 0:
            errors.append(f"report.coverage[{index}]: finding status needs at least one finding")
        if lane.get("status") == "clear" and count:
            errors.append(f"report.coverage[{index}]: clear status contradicts {count} finding(s)")

    disagreements = report.get("disagreements")
    if not isinstance(disagreements, list):
        errors.append("report.disagreements: must be a list")
        disagreements = []
    disagreement_ids: list[str] = []
    for index, disagreement in enumerate(disagreements):
        location = f"report.disagreements[{index}]"
        if not isinstance(disagreement, dict):
            errors.append(f"{location}: must be an object")
            continue
        _require_exact_keys(
            disagreement, {"id", "claims", "resolution", "evidence"}, location, errors
        )
        if _nonempty_string(disagreement.get("id")):
            disagreement_ids.append(disagreement["id"])
        if not _nonempty_string(disagreement.get("id")):
            errors.append(f"{location}.id: must be non-empty")
        if not _string_list(disagreement.get("claims"), minimum=2):
            errors.append(f"{location}.claims: must contain at least two claims")
        if not _nonempty_string(disagreement.get("resolution")):
            errors.append(f"{location}.resolution: must be non-empty")
        _validate_evidence(disagreement.get("evidence"), location, errors)
    duplicate_disagreements = sorted(
        str(item) for item, count in Counter(disagreement_ids).items() if count > 1
    )
    if duplicate_disagreements:
        errors.append(f"report.disagreements: duplicate ids {duplicate_disagreements}")

    checks = report.get("checks")
    if not isinstance(checks, list):
        errors.append("report.checks: must be a list")
        checks = []
    passed_commands: set[str] = set()
    seen_check_commands: Counter[str] = Counter()
    for index, check in enumerate(checks):
        location = f"report.checks[{index}]"
        if not isinstance(check, dict):
            errors.append(f"{location}: must be an object")
            continue
        _require_exact_keys(
            check,
            {"name", "command", "status", "exit_code", "evidence_ref"},
            location,
            errors,
        )
        if not _nonempty_string(check.get("name")):
            errors.append(f"{location}.name: must be non-empty")
        if not _nonempty_string(check.get("command")):
            errors.append(f"{location}.command: must be non-empty")
        else:
            seen_check_commands[check["command"]] += 1
        status = check.get("status")
        exit_code = check.get("exit_code")
        valid_exit_code = exit_code is None or (
            isinstance(exit_code, int) and not isinstance(exit_code, bool)
        )
        if not valid_exit_code:
            errors.append(f"{location}.exit_code: must be an integer or null")
        if not _one_of(status, VALID_CHECK_STATUSES):
            errors.append(f"{location}.status: expected one of {sorted(VALID_CHECK_STATUSES)}")
        elif status == "passed":
            if exit_code != 0:
                errors.append(f"{location}: passed status requires exit_code 0")
            elif _nonempty_string(check.get("command")):
                passed_commands.add(check["command"])
        elif status == "failed":
            errors.append(f"{location}.status: failed checks cannot pass")
            if exit_code in (None, 0):
                errors.append(f"{location}: failed status requires a non-zero exit_code")
        elif status == "not-run" and exit_code is not None:
            errors.append(f"{location}: not-run status requires null exit_code")
        if not _concrete_string(check.get("evidence_ref")):
            errors.append(f"{location}.evidence_ref: must be concrete, not empty or a placeholder")

    duplicate_commands = sorted(command for command, count in seen_check_commands.items() if count > 1)
    if duplicate_commands:
        errors.append(f"report.checks: duplicate commands do not count as independent checks {duplicate_commands}")

    required_check_commands = set(implementation_commands)
    reported_check_commands = set(seen_check_commands)
    missing_reported = sorted(required_check_commands - reported_check_commands)
    extra_reported = sorted(reported_check_commands - required_check_commands)
    if missing_reported:
        errors.append(f"report.checks: missing frozen commands {missing_reported}")
    if extra_reported:
        errors.append(f"report.checks: contains non-frozen commands {extra_reported}")

    reported_by_command = {
        item.get("command"): item
        for item in checks
        if isinstance(item, dict) and _nonempty_string(item.get("command"))
    }
    passed_commands.clear()
    for command in implementation_commands:
        observation = observed_checks.get(command)
        reported_check = reported_by_command.get(command)
        if observation is None:
            continue
        exit_code = observation.get("exit_code")
        if exit_code == 0:
            passed_commands.add(command)
        else:
            errors.append(
                f"observed_check_results: frozen command failed with exit code {exit_code}: {command}"
            )
        if isinstance(reported_check, dict):
            expected_status = "passed" if exit_code == 0 else "failed"
            if reported_check.get("status") != expected_status:
                errors.append(
                    f"report.checks: status contradicts controller observation for {command}"
                )
            if reported_check.get("exit_code") != exit_code:
                errors.append(
                    f"report.checks: exit code contradicts controller observation for {command}"
                )

    for location, command in deliberation_checks:
        if command not in passed_commands:
            errors.append(f"{location}.discriminating_check: must reference a passed check command")

    for location, commands in fixed_verifications:
        if not set(commands) & passed_commands:
            errors.append(f"{location}.verification: must reference at least one passed check command")

    residual_risks = report.get("residual_risks")
    for command in implementation_commands:
        if command not in passed_commands:
            errors.append("report.implementation: every reproduction and acceptance command must be a passed check")

    if not isinstance(residual_risks, list) or not all(
        _nonempty_string(item) for item in residual_risks
    ):
        errors.append("report.residual_risks: must be a list of non-empty strings")

    return {
        "passed": not errors,
        "errors": errors,
        "summary": {
            "expected_lanes": len(expected_lanes),
            "covered_lanes": len(set(coverage_ids) & expected_lanes),
            "findings": len(findings),
            "disagreements": len(disagreements),
            "passing_checks": len(passed_commands),
            "intent": packet_intent,
            "implementation_status": report.get("implementation", {}).get("status") if isinstance(report.get("implementation"), dict) else None,
        },
    }


def load_json(path: Path) -> Any:
    with path.open("r", encoding="utf-8") as handle:
        return strict_json_load(handle)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--packet", type=Path)
    parser.add_argument("--report", type=Path)
    parser.add_argument("--repo-root", type=Path, required=True)
    parser.add_argument("--baseline-manifest", type=Path, required=True)
    parser.add_argument(
        "--capture-baseline",
        action="store_true",
        help="Create a pre-implementation state manifest without overwriting an existing file",
    )
    parser.add_argument(
        "--expect-packet-sha256",
        help="Trusted preflight packet digest supplied by the user or controller",
    )
    parser.add_argument(
        "--expect-verifier-sha256",
        help="Digest of the complete verifier bundle pinned by a trusted controller or release",
    )
    parser.add_argument(
        "--supersedes-packet",
        type=Path,
        help="External prior packet artifact required when the contract is a revision",
    )
    parser.add_argument(
        "--expect-supersedes-sha256",
        help="Trusted digest of the external prior packet when the contract is a revision",
    )
    parser.add_argument(
        "--runtime-receipt",
        type=Path,
        help="External controller ledger required for shared coordination",
    )
    parser.add_argument(
        "--expect-runtime-receipt-sha256",
        help="Trusted post-run digest of the shared runtime receipt",
    )
    return parser.parse_args()


def _print_failure(message: str, exit_code: int) -> int:
    print(
        json.dumps(
            {"passed": False, "errors": [message]},
            ensure_ascii=True,
            indent=2,
        )
    )
    return exit_code


def _is_inside(path: Path, root: Path) -> bool:
    try:
        path.relative_to(root)
    except ValueError:
        return False
    return True


def _verifier_bundle_paths() -> tuple[Path, ...]:
    skill_root = Path(__file__).resolve(strict=True).parent.parent
    return (
        skill_root / "scripts" / "check_delivery.py",
        skill_root / "scripts" / "diverge.py",
        skill_root / "references" / "lenses.json",
    )


def verifier_bundle_sha256() -> str:
    skill_root = Path(__file__).resolve(strict=True).parent.parent
    payload = {
        path.relative_to(skill_root).as_posix(): _file_sha256(path)
        for path in _verifier_bundle_paths()
    }
    return hashlib.sha256(_canonical_json_bytes(payload)).hexdigest()


def main() -> int:
    args = parse_args()
    try:
        repo_root = _canonical_repository_root(Path(args.repo_root))
        containment_root = repo_root
        baseline_path = Path(os.path.abspath(args.baseline_manifest)).resolve(strict=False)
        if _is_inside(baseline_path, containment_root):
            return _print_failure(
                "baseline manifest must be stored outside the target repository", 2
            )
        verifier_paths = _verifier_bundle_paths()
        verifier_digest = verifier_bundle_sha256()
        for verifier_path in verifier_paths:
            if _is_inside(verifier_path, containment_root):
                return _print_failure(
                    "trusted verifier bundle must be stored outside the target repository",
                    2,
                )
        if args.capture_baseline:
            manifest = build_state_manifest(repo_root)
            baseline_path.parent.mkdir(parents=True, exist_ok=True)
            with baseline_path.open("x", encoding="utf-8") as handle:
                handle.write(json.dumps(manifest, ensure_ascii=False, indent=2) + "\n")
            print(
                json.dumps(
                    {
                        "passed": True,
                        "repository_ref": manifest["repository_ref"],
                        "state_ref": _repository_identity(baseline_path),
                        "baseline_manifest_sha256": state_manifest_sha256(manifest),
                        "verifier_sha256": verifier_digest,
                    },
                    ensure_ascii=True,
                    indent=2,
                )
            )
            return 0

        if (
            args.packet is None
            or args.report is None
            or args.expect_packet_sha256 is None
            or args.expect_verifier_sha256 is None
        ):
            return _print_failure(
                "gate mode requires --packet, --report, --expect-packet-sha256, "
                "and --expect-verifier-sha256",
                2,
            )
        if (
            re.fullmatch(r"[0-9a-f]{64}", args.expect_verifier_sha256) is None
            or args.expect_verifier_sha256 != verifier_digest
        ):
            return _print_failure("trusted verifier digest is malformed or mismatched", 1)

        packet_path = Path(os.path.abspath(args.packet)).resolve(strict=True)
        report_path = Path(os.path.abspath(args.report)).resolve(strict=True)
        baseline_path = baseline_path.resolve(strict=True)
        runtime_receipt_path = (
            Path(os.path.abspath(args.runtime_receipt)).resolve(strict=True)
            if args.runtime_receipt is not None
            else None
        )
        supersedes_packet_path = (
            Path(os.path.abspath(args.supersedes_packet)).resolve(strict=True)
            if args.supersedes_packet is not None
            else None
        )
        labeled_artifacts = [
            ("packet", packet_path),
            ("report", report_path),
            ("baseline manifest", baseline_path),
        ]
        if runtime_receipt_path is not None:
            labeled_artifacts.append(("runtime receipt", runtime_receipt_path))
        if supersedes_packet_path is not None:
            labeled_artifacts.append(("supersedes packet", supersedes_packet_path))
        for label, artifact_path in labeled_artifacts:
            if _is_inside(artifact_path, containment_root):
                return _print_failure(
                    f"{label} must be stored outside the target repository", 2
                )
        artifact_paths = tuple(
            [path for _, path in labeled_artifacts] + list(verifier_paths)
        )
        if len(artifact_paths) != len(set(artifact_paths)):
            return _print_failure("audit artifacts and verifier files must be distinct", 2)
        artifact_digests = {path: _file_sha256(path) for path in artifact_paths}

        packet = load_json(packet_path)
        report = load_json(report_path)
        baseline_manifest = load_json(baseline_path)
        runtime_receipt = (
            load_json(runtime_receipt_path)
            if runtime_receipt_path is not None
            else None
        )
        supersedes_packet = (
            load_json(supersedes_packet_path)
            if supersedes_packet_path is not None
            else None
        )
        if not isinstance(packet, dict):
            return _print_failure("packet must be an object", 1)
        actual_packet_digest = packet_sha256(packet)
        if (
            re.fullmatch(r"[0-9a-f]{64}", args.expect_packet_sha256) is None
            or args.expect_packet_sha256 != actual_packet_digest
            or packet.get("packet_sha256") != actual_packet_digest
        ):
            return _print_failure(
                "trusted preflight packet digest is missing, malformed, or mismatched",
                1,
            )
        packet_errors = validate_packet_preflight(
            packet, args.expect_packet_sha256
        )
        if packet_errors:
            return _print_failure("; ".join(packet_errors), 1)
        frozen_contract = freeze_contract(packet.get("contract"))
        if packet.get("coordination") == "shared":
            if runtime_receipt is None or args.expect_runtime_receipt_sha256 is None:
                return _print_failure(
                    "shared coordination requires an external runtime receipt and trusted digest",
                    1,
                )
            receipt_errors = validate_runtime_receipt(
                runtime_receipt,
                packet,
                report,
                args.expect_runtime_receipt_sha256,
            )
            if receipt_errors:
                return _print_failure("; ".join(receipt_errors), 1)
        elif (
            runtime_receipt is not None
            or args.expect_runtime_receipt_sha256 is not None
        ):
            return _print_failure(
                "runtime receipt is allowed only for shared coordination", 1
            )
        validated_baseline = validate_state_manifest(baseline_manifest)
        expected_baseline_digest = frozen_contract["baseline"]["state_sha256"]
        actual_baseline_digest = state_manifest_sha256(validated_baseline)
        if actual_baseline_digest != expected_baseline_digest:
            return _print_failure(
                "baseline manifest does not match the externally anchored contract",
                1,
            )
        if frozen_contract["baseline"]["repository_ref"] != validated_baseline[
            "repository_ref"
        ]:
            return _print_failure(
                "contract repository_ref does not match the captured repository", 1
            )
        if _repository_identity(containment_root) != validated_baseline["repository_ref"]:
            return _print_failure(
                "target repository identity does not match the captured repository", 1
            )
        precheck_manifest = build_state_manifest(containment_root)
        if precheck_manifest["root_metadata"]["file_id"] != validated_baseline[
            "root_metadata"
        ]["file_id"]:
            return _print_failure(
                "target repository root object does not match the captured repository", 1
            )
        if frozen_contract["baseline"]["state_ref"] != _repository_identity(
            baseline_path
        ):
            return _print_failure(
                "contract state_ref does not match the external baseline artifact", 1
            )
        scope_errors = validate_scope_paths_against_repo(
            frozen_contract, containment_root
        )
        if scope_errors:
            return _print_failure("; ".join(scope_errors), 1)

        supersedes = frozen_contract.get("supersedes")
        if supersedes is None:
            if (
                args.expect_supersedes_sha256 is not None
                or supersedes_packet is not None
            ):
                return _print_failure(
                    "unexpected supersedes artifact or anchor for an initial contract", 1
                )
        else:
            if (
                supersedes_packet is None
                or not isinstance(args.expect_supersedes_sha256, str)
                or re.fullmatch(r"[0-9a-f]{64}", args.expect_supersedes_sha256)
                is None
            ):
                return _print_failure(
                    "revised contract requires the external prior packet and its trusted digest",
                    1,
                )
            prior_digest = packet_sha256(supersedes_packet)
            if (
                prior_digest != args.expect_supersedes_sha256
                or prior_digest != supersedes["packet_sha256"]
                or supersedes_packet.get("packet_sha256") != prior_digest
            ):
                return _print_failure(
                    "revised contract does not match the exact prior externally anchored packet",
                    1,
                )
            if (
                type(supersedes_packet.get("version")) is not int
                or supersedes_packet["version"] != 4
                or set(supersedes_packet)
                != {
                    "version",
                    "contract",
                    "contract_sha256",
                    "packet_sha256",
                    "risk",
                    "profile",
                    "coordination",
                    "planner",
                    "independence",
                    "execution_policy",
                    "discussion",
                    "lanes",
                    "synthesis_gate",
                }
            ):
                return _print_failure("prior packet schema is invalid", 1)
            prior_contract = freeze_contract(supersedes_packet.get("contract"))
            if (
                not _strict_json_equal(supersedes_packet.get("contract"), prior_contract)
                or supersedes_packet.get("contract_sha256")
                != contract_sha256(prior_contract)
            ):
                return _print_failure("prior packet contract is invalid", 1)
            if frozen_contract["contract_id"] != prior_contract["contract_id"]:
                return _print_failure(
                    "revised contract must preserve the prior contract_id", 1
                )
            if frozen_contract["revision"] != prior_contract["revision"] + 1:
                return _print_failure(
                    "revised contract revision must equal prior revision plus one", 1
                )

        commands = [item["command"] for item in frozen_contract["acceptance"]]
        observed_checks = run_frozen_checks(commands, containment_root)
        if any(_file_sha256(path) != digest for path, digest in artifact_digests.items()):
            return _print_failure(
                "a frozen audit artifact or the pinned verifier changed during checks", 1
            )
        current_manifest = build_state_manifest(repo_root)
        if current_manifest["root_metadata"]["file_id"] != validated_baseline[
            "root_metadata"
        ]["file_id"]:
            return _print_failure(
                "target repository root object changed during acceptance checks", 1
            )
        observed_changed_paths = state_manifest_changed_paths(
            validated_baseline, current_manifest
        )
        result = evaluate(
            packet,
            report,
            args.expect_packet_sha256,
            observed_changed_paths=observed_changed_paths,
            observed_check_results=observed_checks,
        )
        final_state_digest = state_manifest_sha256(current_manifest)
        diff_digest = hashlib.sha256(
            _canonical_json_bytes(
                {
                    "repository_ref": current_manifest["repository_ref"],
                    "baseline_state_sha256": actual_baseline_digest,
                    "final_state_sha256": final_state_digest,
                    "changed_paths": observed_changed_paths,
                }
            )
        ).hexdigest()
        result["observations"] = {
            "repository_ref": current_manifest["repository_ref"],
            "baseline_state_sha256": actual_baseline_digest,
            "final_state_sha256": final_state_digest,
            "diff_sha256": diff_digest,
            "changed_paths": observed_changed_paths,
            "checks": observed_checks,
            "verifier_sha256": verifier_digest,
            "runtime_receipt_sha256": (
                runtime_receipt_sha256(runtime_receipt)
                if runtime_receipt is not None
                else None
            ),
        }
    except FileExistsError:
        return _print_failure(
            "baseline manifest already exists; immutable capture refuses overwrite", 2
        )
    except (OSError, json.JSONDecodeError) as exc:
        return _print_failure(str(exc), 2)
    except (KeyError, RecursionError, TypeError, UnicodeError, ValueError) as exc:
        return _print_failure(f"validation error: {exc}", 1)
    print(json.dumps(result, ensure_ascii=True, indent=2))
    return 0 if result["passed"] else 1

if __name__ == "__main__":
    raise SystemExit(main())
