#!/usr/bin/env python3
"""Run local Codex coding probes or validate externally anchored live results.

Local mode is intentionally never release-eligible.  Only an independently
anchored external controller result set can satisfy the release gate.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import math
import os
import re
import shutil
import stat
import subprocess
import sys
import tempfile
import time
import unicodedata
import zipfile
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timedelta, timezone
from pathlib import Path, PurePosixPath
from typing import Any


TEST_DIR = Path(__file__).resolve().parent
SKILL_DIR = TEST_DIR.parent
sys.path.insert(0, str(SKILL_DIR / "scripts"))

from build_codex_plugin import RUNTIME_FILES  # noqa: E402


SUITE_VERSION = "wide-lens-coding-suite/v1"
RESULTS_VERSION = "wide-lens-coding-results/v1"
ANCHOR_VERSION = "wide-lens-coding-anchor/v1"
ANCHOR_PRINCIPAL = "wide-lens-live-controller"
ANCHOR_NAMESPACE = "wide-lens-live-v1"
RELEASE_STRATA = ("local", "security", "concurrency", "data", "api", "distributed")
RELEASE_TASKS = 150
TASKS_PER_STRATUM = 25
MAX_JSON_BYTES = 16 * 1024 * 1024
MAX_JSON_DEPTH = 128
MAX_ZIP_FILES = 10_000
MAX_ZIP_BYTES = 256 * 1024 * 1024
SHA_RE = re.compile(r"^[0-9a-f]{64}$")
COMMIT_RE = re.compile(r"^(?:[0-9a-f]{40}|[0-9a-f]{64})$")
ID_RE = re.compile(r"^[a-z0-9][a-z0-9._-]{0,127}$")
SUITE_KEYS = {
    "version",
    "benchmark_id",
    "model_request",
    "reasoning_request",
    "skill_sha256",
    "cli_sha256",
    "strata",
    "cases",
}
CASE_KEYS = {"id", "stratum", "fixture", "task", "diff_policy", "resources", "oracle"}
FIXTURE_KEYS = {"ref", "sha256", "baseline_tree_sha256"}
TASK_KEYS = {
    "prompt",
    "assurance",
    "depth",
    "coordination",
    "allowed_write_paths",
    "non_goals",
}
DIFF_KEYS = {"must_change", "allowed_paths", "max_diff_bytes", "reject_links_special_files"}
RESOURCE_KEYS = {
    "wall_seconds",
    "max_input_tokens",
    "max_output_tokens",
    "max_reasoning_tokens",
    "max_tool_calls",
    "max_process_seconds",
    "max_artifact_bytes",
    "max_concurrency",
}
ORACLE_KEYS = {"external_ref", "sha256", "command", "timeout_seconds"}
EXTERNAL_KEYS = {
    "version",
    "release_commit",
    "suite_sha256",
    "controller_ref",
    "controller_bundle_sha256",
    "environment_sha256",
    "skill_sha256",
    "cli_sha256",
    "model_request",
    "reasoning_request",
    "assurances",
    "cases",
}
ANCHOR_KEYS = {
    "version",
    "repository",
    "candidate_commit_sha",
    "challenge_sha256",
    "suite_sha256",
    "results_sha256",
    "skill_sha256",
    "benchmark_id",
    "controller_ref",
    "controller_bundle_sha256",
    "controller_config_sha256",
    "environment_sha256",
    "cli_sha256",
    "model_request",
    "reasoning_request",
    "controller_run_id",
    "issued_at",
    "expires_at",
}
ASSURANCE_KEYS = {
    "controller_is_external",
    "suite_frozen_before_runs",
    "fresh_context_per_case",
    "single_attempt_per_case",
    "hidden_oracle_isolated",
    "reference_solution_prevalidated",
    "independent_verifier",
    "os_sandbox",
    "credentials_brokered",
    "complete_event_capture",
    "orphan_process_capture",
    "resource_observation_complete",
    "actual_model_route_attested",
}
EXTERNAL_CASE_KEYS = {
    "id",
    "stratum",
    "execution_actor_ids",
    "integrator_id",
    "verifier_id",
    "baseline_manifest",
    "final_manifest",
    "controller_observed",
    "resource_usage",
    "invariants",
    "oracle_result",
    "detail_sha256",
}
OBSERVATION_KEYS = {
    "baseline_tree_sha256",
    "final_tree_sha256",
    "diff_sha256",
    "changed_paths",
    "diff_bytes",
}
USAGE_KEYS = {
    "input_tokens",
    "output_tokens",
    "reasoning_tokens",
    "tool_calls",
    "process_seconds",
    "artifact_bytes",
    "peak_concurrency",
}
INVARIANT_KEYS = {
    "fresh_context",
    "single_attempt",
    "isolated_workspace",
    "canonical_write_blocked",
    "hidden_oracle_blind",
    "workspace_network_access_detected",
    "credential_access_detected",
    "non_integrator_writes_detected",
    "recursive_delegation_detected",
    "complete_event_capture",
    "orphan_processes_detected",
    "verifier_write_access",
    "verifier_candidate_outputs_visible",
    "actual_model_route_matches",
    "policy_violations",
}
EXPECTED_INVARIANTS = {
    "fresh_context": True,
    "single_attempt": True,
    "isolated_workspace": True,
    "canonical_write_blocked": True,
    "hidden_oracle_blind": True,
    "workspace_network_access_detected": False,
    "credential_access_detected": False,
    "non_integrator_writes_detected": False,
    "recursive_delegation_detected": False,
    "complete_event_capture": True,
    "orphan_processes_detected": False,
    "verifier_write_access": False,
    "verifier_candidate_outputs_visible": False,
    "actual_model_route_matches": True,
    "policy_violations": [],
}
ORACLE_RESULT_KEYS = {"definition_sha256", "exit_code"}


class LiveEvalError(RuntimeError):
    """The benchmark input or controller evidence is invalid."""


def canonical_json(value: Any) -> bytes:
    return json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        allow_nan=False,
    ).encode("utf-8")


def sha256_bytes(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


def sha256_json(value: Any) -> str:
    return sha256_bytes(canonical_json(value))


def _unique_object(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    value: dict[str, Any] = {}
    for key, item in pairs:
        if key in value:
            raise LiveEvalError(f"duplicate JSON key: {key}")
        value[key] = item
    return value


def _reject_constant(value: str) -> None:
    raise LiveEvalError(f"non-finite JSON number: {value}")


def _assert_depth(value: Any) -> None:
    pending = [(value, 1)]
    while pending:
        current, depth = pending.pop()
        if depth > MAX_JSON_DEPTH:
            raise LiveEvalError(f"JSON nesting exceeds {MAX_JSON_DEPTH}")
        if isinstance(current, dict):
            pending.extend((item, depth + 1) for item in current.values())
        elif isinstance(current, list):
            pending.extend((item, depth + 1) for item in current)


def load_plain_bytes(path: Path, *, label: str, max_bytes: int) -> bytes:
    lexical = Path(os.path.abspath(path))
    if os.name == "nt" and any(
        ":" in component and component.casefold() != lexical.anchor.casefold()
        for component in lexical.parts
    ):
        raise LiveEvalError(f"{label} contains an alternate data stream: {path}")
    for component in reversed([lexical, *lexical.parents]):
        try:
            os.lstat(component)
        except FileNotFoundError:
            continue
        if component.is_symlink() or is_reparse(component):
            raise LiveEvalError(f"{label} contains a link or reparse point: {path}")
    if not lexical.is_file() or lexical.is_symlink() or is_reparse(lexical):
        raise LiveEvalError(f"{label} must be a plain file: {path}")
    metadata = lexical.stat(follow_symlinks=False)
    if metadata.st_nlink != 1:
        raise LiveEvalError(f"{label} must not be hard-linked: {path}")
    if metadata.st_size > max_bytes:
        raise LiveEvalError(f"{label} exceeds {max_bytes} bytes: {path}")
    identity = (
        metadata.st_dev,
        metadata.st_ino,
        metadata.st_size,
        metadata.st_mtime_ns,
    )
    raw = lexical.read_bytes()
    final_metadata = lexical.stat(follow_symlinks=False)
    if (
        (
            final_metadata.st_dev,
            final_metadata.st_ino,
            final_metadata.st_size,
            final_metadata.st_mtime_ns,
        )
        != identity
        or lexical.read_bytes() != raw
    ):
        raise LiveEvalError(f"{label} changed during validation: {path}")
    return raw


def load_json(path: Path) -> Any:
    raw = load_plain_bytes(path, label="JSON input", max_bytes=MAX_JSON_BYTES)
    try:
        value = json.loads(
            raw.decode("utf-8", "strict"),
            object_pairs_hook=_unique_object,
            parse_constant=_reject_constant,
        )
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise LiveEvalError(f"invalid UTF-8 JSON: {path}: {exc}") from exc
    _assert_depth(value)
    return value


def parse_utc_timestamp(value: Any, label: str) -> datetime:
    if not isinstance(value, str) or not value.endswith("Z"):
        raise LiveEvalError(f"{label} must be a canonical UTC timestamp")
    try:
        parsed = datetime.strptime(value, "%Y-%m-%dT%H:%M:%SZ").replace(
            tzinfo=timezone.utc
        )
    except ValueError as exc:
        raise LiveEvalError(f"{label} must be a canonical UTC timestamp") from exc
    if parsed.strftime("%Y-%m-%dT%H:%M:%SZ") != value:
        raise LiveEvalError(f"{label} must be a canonical UTC timestamp")
    return parsed


def verify_anchor_signature(
    anchor: dict[str, Any], signature_path: Path, allowed_signers_path: Path
) -> None:
    signature = load_plain_bytes(
        signature_path, label="controller signature", max_bytes=64 * 1024
    )
    if not signature.startswith(b"-----BEGIN SSH SIGNATURE-----\n"):
        raise LiveEvalError("controller signature is not an SSHSIG document")
    load_plain_bytes(
        allowed_signers_path, label="controller allowed signers", max_bytes=64 * 1024
    )
    executable = shutil.which("ssh-keygen")
    if executable is None:
        raise LiveEvalError("ssh-keygen is required to verify the controller signature")
    try:
        completed = subprocess.run(
            [
                executable,
                "-Y",
                "verify",
                "-f",
                str(allowed_signers_path),
                "-I",
                ANCHOR_PRINCIPAL,
                "-n",
                ANCHOR_NAMESPACE,
                "-s",
                str(signature_path),
            ],
            input=canonical_json(anchor),
            capture_output=True,
            check=False,
            timeout=10,
        )
    except (OSError, subprocess.SubprocessError) as exc:
        raise LiveEvalError("controller signature verification could not run") from exc
    if completed.returncode != 0:
        raise LiveEvalError("controller SSHSIG verification failed")


def is_reparse(path: Path) -> bool:
    try:
        attributes = path.stat(follow_symlinks=False).st_file_attributes
    except (AttributeError, FileNotFoundError, OSError):
        return False
    return bool(attributes & getattr(stat, "FILE_ATTRIBUTE_REPARSE_POINT", 0x400))


def is_sha(value: Any) -> bool:
    return isinstance(value, str) and SHA_RE.fullmatch(value) is not None


def nonempty(value: Any) -> bool:
    return isinstance(value, str) and bool(value.strip())


def positive_int(value: Any) -> bool:
    return type(value) is int and value > 0


def canonical_repo_path(value: Any) -> str | None:
    if not nonempty(value) or "\\" in value or ":" in value or "\x00" in value:
        return None
    path = PurePosixPath(value)
    if path.is_absolute() or any(part in {"", ".", ".."} for part in path.parts):
        return None
    normalized = path.as_posix()
    return normalized if normalized == value else None


def external_ref(root: Path, value: Any, label: str) -> Path:
    relative = canonical_repo_path(value)
    if relative is None:
        raise LiveEvalError(f"{label} is not a canonical relative path")
    lexical = root.joinpath(*PurePosixPath(relative).parts)
    for component in [lexical, *lexical.parents]:
        if component == root.parent:
            break
        if component.exists() and (component.is_symlink() or is_reparse(component)):
            raise LiveEvalError(f"{label} contains a link or reparse point")
    try:
        resolved = lexical.resolve(strict=True)
        resolved.relative_to(root.resolve(strict=True))
    except (OSError, ValueError) as exc:
        raise LiveEvalError(f"{label} escapes or does not exist") from exc
    if not resolved.is_file() or resolved.stat(follow_symlinks=False).st_nlink != 1:
        raise LiveEvalError(f"{label} must be a plain non-hard-linked file")
    return resolved


def validate_suite(value: Any) -> dict[str, Any]:
    errors: list[str] = []
    if not isinstance(value, dict) or set(value) != SUITE_KEYS:
        raise LiveEvalError("suite has an invalid field set")
    if value.get("version") != SUITE_VERSION:
        errors.append(f"suite version must be {SUITE_VERSION}")
    if not nonempty(value.get("benchmark_id")):
        errors.append("benchmark_id must be non-empty")
    if not nonempty(value.get("model_request")):
        errors.append("model_request must be non-empty")
    if value.get("reasoning_request") not in {"low", "medium", "high", "xhigh", "max", "ultra"}:
        errors.append("reasoning_request is invalid")
    for field in ("skill_sha256", "cli_sha256"):
        if not is_sha(value.get(field)):
            errors.append(f"{field} must be a lowercase SHA-256")
    strata = value.get("strata")
    if (
        not isinstance(strata, list)
        or not strata
        or not all(isinstance(item, str) and ID_RE.fullmatch(item) for item in strata)
        or len(strata) != len(set(strata))
    ):
        errors.append("strata must be a non-empty unique ID array")
        strata = []
    cases = value.get("cases")
    if not isinstance(cases, list) or not cases:
        errors.append("cases must be a non-empty array")
        cases = []
    ids: set[str] = set()
    fingerprints: set[str] = set()
    for index, case in enumerate(cases):
        location = f"case {index}"
        if not isinstance(case, dict) or set(case) != CASE_KEYS:
            errors.append(f"{location} has an invalid field set")
            continue
        case_id = case.get("id")
        if not isinstance(case_id, str) or ID_RE.fullmatch(case_id) is None or case_id in ids:
            errors.append(f"{location} id is invalid or duplicate")
        else:
            ids.add(case_id)
        if case.get("stratum") not in strata:
            errors.append(f"{location} stratum is not declared")
        fixture = case.get("fixture")
        if not isinstance(fixture, dict) or set(fixture) != FIXTURE_KEYS:
            errors.append(f"{location} fixture has an invalid field set")
        else:
            if canonical_repo_path(fixture.get("ref")) is None:
                errors.append(f"{location} fixture ref is invalid")
            for field in ("sha256", "baseline_tree_sha256"):
                if not is_sha(fixture.get(field)):
                    errors.append(f"{location} fixture {field} is invalid")
        task = case.get("task")
        if not isinstance(task, dict) or set(task) != TASK_KEYS:
            errors.append(f"{location} task has an invalid field set")
        else:
            if not nonempty(task.get("prompt")):
                errors.append(f"{location} prompt must be non-empty")
            if task.get("assurance") not in {"practical", "assured"}:
                errors.append(f"{location} assurance is invalid")
            if task.get("depth") not in {"focused", "full"}:
                errors.append(f"{location} depth is invalid")
            if task.get("coordination") not in {"independent", "shared"}:
                errors.append(f"{location} coordination is invalid")
            for field in ("allowed_write_paths", "non_goals"):
                items = task.get(field)
                if not isinstance(items, list) or not all(nonempty(item) for item in items):
                    errors.append(f"{location} {field} must be a string array")
            paths = task.get("allowed_write_paths")
            if isinstance(paths, list):
                if any(canonical_repo_path(item) is None for item in paths):
                    errors.append(f"{location} allowed_write_paths contains an invalid path")
                if len(paths) != len(set(paths)):
                    errors.append(f"{location} allowed_write_paths contains duplicates")
        policy = case.get("diff_policy")
        if not isinstance(policy, dict) or set(policy) != DIFF_KEYS:
            errors.append(f"{location} diff_policy has an invalid field set")
        else:
            if type(policy.get("must_change")) is not bool or type(policy.get("reject_links_special_files")) is not bool:
                errors.append(f"{location} diff_policy booleans are invalid")
            if not positive_int(policy.get("max_diff_bytes")):
                errors.append(f"{location} max_diff_bytes must be positive")
            allowed = policy.get("allowed_paths")
            if not isinstance(allowed, list) or not allowed or any(canonical_repo_path(item) is None for item in allowed):
                errors.append(f"{location} diff allowed_paths is invalid")
            elif isinstance(task, dict) and allowed != task.get("allowed_write_paths"):
                errors.append(f"{location} task and diff allowed paths differ")
            if policy.get("reject_links_special_files") is not True:
                errors.append(f"{location} must reject links and special files")
        resources = case.get("resources")
        if not isinstance(resources, dict) or set(resources) != RESOURCE_KEYS:
            errors.append(f"{location} resources has an invalid field set")
        elif any(not positive_int(resources.get(field)) for field in RESOURCE_KEYS):
            errors.append(f"{location} resource limits must be positive integers")
        oracle = case.get("oracle")
        if not isinstance(oracle, dict) or set(oracle) != ORACLE_KEYS:
            errors.append(f"{location} oracle has an invalid field set")
        else:
            if canonical_repo_path(oracle.get("external_ref")) is None or not is_sha(oracle.get("sha256")):
                errors.append(f"{location} oracle ref or digest is invalid")
            command = oracle.get("command")
            if not isinstance(command, list) or not command or not all(nonempty(item) for item in command):
                errors.append(f"{location} oracle command is invalid")
            if not positive_int(oracle.get("timeout_seconds")):
                errors.append(f"{location} oracle timeout is invalid")
        fingerprint_source = dict(case)
        fingerprint_source.pop("id", None)
        fingerprint_source.pop("stratum", None)
        fingerprint = sha256_json(fingerprint_source)
        if fingerprint in fingerprints:
            errors.append(f"{location} is not semantically unique after removing id")
        fingerprints.add(fingerprint)
    if errors:
        raise LiveEvalError("; ".join(errors))
    return value


def skill_digest(root: Path) -> str:
    entries: list[dict[str, Any]] = []
    for relative_text in RUNTIME_FILES:
        relative = Path(relative_text)
        path = root / relative
        if not path.is_file() or path.is_symlink() or is_reparse(path):
            raise LiveEvalError(f"Skill runtime file is unavailable: {relative_text}")
        data = path.read_bytes()
        entries.append({"path": relative.as_posix(), "sha256": sha256_bytes(data), "size": len(data)})
    return sha256_json(entries)


def zip_member_path(name: str) -> PurePosixPath:
    if not name or "\\" in name or ":" in name or "\x00" in name:
        raise LiveEvalError(f"unsafe ZIP member: {name!r}")
    if unicodedata.normalize("NFC", name) != name:
        raise LiveEvalError(f"ZIP member is not NFC-normalized: {name!r}")
    path = PurePosixPath(name)
    if path.is_absolute() or any(part in {"", ".", ".."} for part in path.parts):
        raise LiveEvalError(f"unsafe ZIP member: {name!r}")
    reserved = {"con", "prn", "aux", "nul"} | {
        f"{prefix}{index}" for prefix in ("com", "lpt") for index in range(1, 10)
    }
    for part in path.parts:
        if part.endswith((".", " ")) or part.casefold() == ".git":
            raise LiveEvalError(f"non-portable ZIP member: {name!r}")
        if part.split(".", 1)[0].casefold() in reserved:
            raise LiveEvalError(f"reserved ZIP member: {name!r}")
    return path


def extract_fixture(archive_path: Path, destination: Path) -> None:
    if (
        not archive_path.is_file()
        or archive_path.is_symlink()
        or is_reparse(archive_path)
        or archive_path.stat(follow_symlinks=False).st_nlink != 1
    ):
        raise LiveEvalError("fixture ZIP must be a plain non-hard-linked file")
    initial_metadata = archive_path.stat(follow_symlinks=False)
    initial_identity = (
        initial_metadata.st_dev,
        initial_metadata.st_ino,
        initial_metadata.st_size,
    )
    initial_digest = sha256_bytes(archive_path.read_bytes())
    seen: set[str] = set()
    total = 0
    with zipfile.ZipFile(archive_path) as archive:
        infos = archive.infolist()
        if len(infos) > MAX_ZIP_FILES:
            raise LiveEvalError("fixture ZIP has too many entries")
        for info in infos:
            if info.filename.endswith("//"):
                raise LiveEvalError(f"unsafe ZIP directory member: {info.filename!r}")
            member_name = info.filename[:-1] if info.filename.endswith("/") else info.filename
            member = zip_member_path(member_name)
            alias_key = unicodedata.normalize("NFC", member.as_posix()).casefold()
            if alias_key in seen:
                raise LiveEvalError(f"duplicate or case-aliased ZIP member: {info.filename}")
            seen.add(alias_key)
            if info.flag_bits & 1:
                raise LiveEvalError(f"fixture ZIP contains an encrypted member: {info.filename}")
            if info.compress_type not in {zipfile.ZIP_STORED, zipfile.ZIP_DEFLATED}:
                raise LiveEvalError(f"fixture ZIP uses unsupported compression: {info.filename}")
            if info.file_size > 32 * 1024 * 1024:
                raise LiveEvalError(f"fixture ZIP member is too large: {info.filename}")
            if info.compress_size and info.file_size / info.compress_size > 1000:
                raise LiveEvalError(f"fixture ZIP member has a suspicious compression ratio: {info.filename}")
            mode = (info.external_attr >> 16) & 0o170000
            if mode not in {0, stat.S_IFREG, stat.S_IFDIR}:
                raise LiveEvalError(f"fixture ZIP contains a non-regular member: {info.filename}")
            target = destination.joinpath(*member.parts)
            if info.is_dir():
                target.mkdir(parents=True, exist_ok=True)
                continue
            target.parent.mkdir(parents=True, exist_ok=True)
            with archive.open(info) as source, target.open("xb") as output:
                written = 0
                while True:
                    chunk = source.read(1024 * 1024)
                    if not chunk:
                        break
                    written += len(chunk)
                    total += len(chunk)
                    if total > MAX_ZIP_BYTES:
                        raise LiveEvalError("fixture ZIP exceeds the extraction byte limit")
                    output.write(chunk)
                if written != info.file_size:
                    raise LiveEvalError(f"fixture ZIP member size changed: {info.filename}")
    final_metadata = archive_path.stat(follow_symlinks=False)
    final_identity = (final_metadata.st_dev, final_metadata.st_ino, final_metadata.st_size)
    if final_identity != initial_identity or sha256_bytes(archive_path.read_bytes()) != initial_digest:
        raise LiveEvalError("fixture ZIP changed during extraction")
    tree_manifest(destination)


def tree_manifest(root: Path, excluded_roots: set[str] | None = None) -> dict[str, dict[str, Any]]:
    excluded = excluded_roots or set()
    entries: dict[str, dict[str, Any]] = {}
    portable_keys: set[str] = set()
    for current, directories, files in os.walk(root, topdown=True, followlinks=False):
        current_path = Path(current)
        relative_current = current_path.relative_to(root)
        directories[:] = [
            name
            for name in directories
            if not (relative_current == Path(".") and name in excluded)
        ]
        for name in [*directories, *files]:
            path = current_path / name
            relative = path.relative_to(root).as_posix()
            zip_member_path(relative)
            portable_key = unicodedata.normalize("NFC", relative).casefold()
            if portable_key in portable_keys:
                raise LiveEvalError(f"tree contains a portable path collision: {relative}")
            portable_keys.add(portable_key)
            metadata = path.stat(follow_symlinks=False)
            if path.is_symlink() or is_reparse(path):
                raise LiveEvalError(f"tree contains a link or reparse point: {relative}")
            if name in directories:
                if not stat.S_ISDIR(metadata.st_mode):
                    raise LiveEvalError(f"tree contains a special directory entry: {relative}")
                continue
            if not stat.S_ISREG(metadata.st_mode) or metadata.st_nlink != 1:
                raise LiveEvalError(f"tree contains a special or hard-linked file: {relative}")
            data = path.read_bytes()
            entries[relative] = {"sha256": sha256_bytes(data), "size": len(data)}
    return dict(sorted(entries.items()))


def tree_digest(manifest: dict[str, dict[str, Any]]) -> str:
    return sha256_json(manifest)


def validate_manifest(value: Any, label: str) -> dict[str, dict[str, Any]]:
    if not isinstance(value, dict):
        raise LiveEvalError(f"{label} must be an object")
    result: dict[str, dict[str, Any]] = {}
    portable_keys: set[str] = set()
    for path, metadata in value.items():
        if canonical_repo_path(path) is None:
            raise LiveEvalError(f"{label} contains an invalid path")
        zip_member_path(path)
        portable_key = unicodedata.normalize("NFC", path).casefold()
        if portable_key in portable_keys:
            raise LiveEvalError(f"{label} contains a portable path collision")
        portable_keys.add(portable_key)
        if (
            not isinstance(metadata, dict)
            or set(metadata) != {"sha256", "size"}
            or not is_sha(metadata.get("sha256"))
            or type(metadata.get("size")) is not int
            or metadata.get("size") < 0
        ):
            raise LiveEvalError(f"{label} metadata is invalid for {path!r}")
        result[path] = {"sha256": metadata["sha256"], "size": metadata["size"]}
    return dict(sorted(result.items()))


def observe_manifest_diff(
    baseline: dict[str, dict[str, Any]], final: dict[str, dict[str, Any]]
) -> dict[str, Any]:
    paths = changed_paths(baseline, final)
    evidence = {
        "changed_paths": paths,
        "before": {path: baseline.get(path) for path in paths},
        "after": {path: final.get(path) for path in paths},
    }
    return {
        "baseline_tree_sha256": tree_digest(baseline),
        "final_tree_sha256": tree_digest(final),
        "diff_sha256": sha256_json(evidence),
        "changed_paths": paths,
        "diff_bytes": diff_bytes(baseline, final, paths),
        "minimum_artifact_bytes": len(canonical_json(evidence)),
    }


def changed_paths(
    baseline: dict[str, dict[str, Any]], final: dict[str, dict[str, Any]]
) -> list[str]:
    return sorted(
        path
        for path in set(baseline) | set(final)
        if baseline.get(path) != final.get(path)
    )


def path_covered(path: str, parents: list[str]) -> bool:
    return any(path == parent or path.startswith(parent + "/") for parent in parents)


def diff_bytes(
    baseline: dict[str, dict[str, Any]],
    final: dict[str, dict[str, Any]],
    paths: list[str],
) -> int:
    return sum(
        int((baseline.get(path) or {}).get("size", 0))
        + int((final.get(path) or {}).get("size", 0))
        for path in paths
    )


def copy_runtime_skill(source_root: Path, workspace: Path) -> dict[str, dict[str, Any]]:
    target_root = workspace / ".agents" / "skills" / "wide-lens-engineering"
    for relative_text in RUNTIME_FILES:
        source = source_root / relative_text
        target = target_root / relative_text
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(source, target)
    for relative_text in (".codex/config.toml", ".codex/agents/wide-lens-peer.toml"):
        source = source_root / relative_text
        if source.is_file():
            target = workspace / relative_text
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(source, target)
    return tree_manifest(workspace, excluded_roots={".git"})


def oracle_command(case: dict[str, Any], oracle_path: Path, workspace: Path) -> list[str]:
    replacements = {
        "{python}": sys.executable,
        "{oracle}": str(oracle_path),
        "{workspace}": str(workspace),
    }
    result: list[str] = []
    for item in case["oracle"]["command"]:
        value = item
        for marker, replacement in replacements.items():
            value = value.replace(marker, replacement)
        if "{" in value or "}" in value:
            raise LiveEvalError("oracle command contains an unknown placeholder")
        result.append(value)
    return result


def run_oracle(case: dict[str, Any], oracle_path: Path, workspace: Path) -> tuple[int, str]:
    completed = subprocess.run(
        oracle_command(case, oracle_path, workspace),
        cwd=workspace,
        check=False,
        capture_output=True,
        timeout=case["oracle"]["timeout_seconds"],
        env=os.environ.copy(),
    )
    output_digest = sha256_bytes(completed.stdout + completed.stderr)
    return completed.returncode, output_digest


def model_prompt(case: dict[str, Any]) -> str:
    task = case["task"]
    return (
        "Use $wide-lens-engineering for this frozen live benchmark task.\n"
        f"Assurance: {task['assurance']}\n"
        f"Depth: {task['depth']}\n"
        f"Coordination: {task['coordination']}\n"
        "The active main model decides whether delegation has marginal value and chooses every "
        "subagent identity, count, model, and assignment.\n"
        f"Allowed write paths: {json.dumps(task['allowed_write_paths'], ensure_ascii=False)}\n"
        f"Non-goals: {json.dumps(task['non_goals'], ensure_ascii=False)}\n"
        "Do not modify .agents, .codex, tests supplied by the benchmark, or anything outside the "
        "allowed paths. Run repository-visible tests, but do not search for a hidden oracle.\n\n"
        f"Task:\n{task['prompt']}\n"
    )


def recursively_find_usage(value: Any) -> list[dict[str, Any]]:
    found: list[dict[str, Any]] = []
    if isinstance(value, dict):
        event_type = value.get("type")
        usage = value.get("usage")
        if event_type in {"turn.completed", "turn_completed"} and isinstance(usage, dict):
            found.append(usage)
        for child in value.values():
            found.extend(recursively_find_usage(child))
    elif isinstance(value, list):
        for child in value:
            found.extend(recursively_find_usage(child))
    return found


def parse_jsonl_usage(stdout: bytes) -> dict[str, Any]:
    events: list[dict[str, Any]] = []
    for raw_line in stdout.splitlines():
        if not raw_line.strip():
            continue
        try:
            event = json.loads(raw_line)
        except (UnicodeDecodeError, json.JSONDecodeError):
            continue
        if isinstance(event, dict):
            events.append(event)
    usages = [usage for event in events for usage in recursively_find_usage(event)]
    usage = usages[-1] if usages else {}

    def first_int(*names: str) -> int | None:
        for name in names:
            value = usage.get(name)
            if type(value) is int and value >= 0:
                return value
        return None

    tool_markers = {
        "command_execution",
        "command-execution",
        "file_change",
        "file-change",
        "mcp_tool_call",
        "mcp-tool-call",
        "collab_tool_call",
        "collab-tool-call",
        "web_search",
        "web-search",
    }
    tool_ids: set[str] = set()
    anonymous_tools = 0
    for event in events:
        kinds = {event.get("type")}
        item = event.get("item")
        if isinstance(item, dict):
            kinds.add(item.get("type"))
        if any(kind in tool_markers for kind in kinds):
            item_id = item.get("id") if isinstance(item, dict) else event.get("id")
            if nonempty(item_id):
                tool_ids.add(item_id)
            else:
                anonymous_tools += 1
    event_types = [event.get("type") for event in events]
    thread_ids = {
        value
        for event in events
        for value in (
            event.get("thread_id"),
            event.get("threadId"),
            event.get("thread", {}).get("id") if isinstance(event.get("thread"), dict) else None,
        )
        if nonempty(value)
    }
    return {
        "input_tokens": first_int("input_tokens", "inputTokens"),
        "output_tokens": first_int("output_tokens", "outputTokens"),
        "reasoning_tokens": first_int(
            "reasoning_tokens", "reasoning_output_tokens", "reasoningTokens"
        ),
        "tool_calls": len(tool_ids) + anonymous_tools,
        "event_count": len(events),
        "thread_id": next(iter(thread_ids)) if len(thread_ids) == 1 else None,
        "protocol_complete": event_types.count("thread.started") == 1
        and event_types.count("turn.completed") == 1
        and not any(event_type in {"turn.failed", "error"} for event_type in event_types),
    }


def local_case(
    case: dict[str, Any],
    *,
    suite_root: Path,
    oracle_root: Path,
    skill_root: Path,
    codex_command: list[str],
    model: str,
    reasoning: str,
) -> dict[str, Any]:
    started = time.perf_counter()
    result: dict[str, Any] = {
        "id": case["id"],
        "stratum": case["stratum"],
        "functional_success": False,
        "task_success": False,
        "release_eligible": False,
    }
    try:
        fixture_path = external_ref(suite_root, case["fixture"]["ref"], "fixture")
        if sha256_bytes(fixture_path.read_bytes()) != case["fixture"]["sha256"]:
            raise LiveEvalError("fixture digest differs from the frozen suite")
        oracle_path = external_ref(oracle_root, case["oracle"]["external_ref"], "oracle")
        if sha256_bytes(oracle_path.read_bytes()) != case["oracle"]["sha256"]:
            raise LiveEvalError("oracle digest differs from the frozen suite")
        with tempfile.TemporaryDirectory(prefix=f"wide-lens-live-{case['id']}-") as temporary:
            workspace = Path(temporary) / "workspace"
            workspace.mkdir()
            extract_fixture(fixture_path, workspace)
            fixture_manifest = tree_manifest(workspace)
            if tree_digest(fixture_manifest) != case["fixture"]["baseline_tree_sha256"]:
                raise LiveEvalError("fixture baseline tree digest differs from the suite")
            baseline_exit, baseline_oracle_sha = run_oracle(case, oracle_path, workspace)
            support_manifest = copy_runtime_skill(skill_root, workspace)
            benchmark_manifest = tree_manifest(
                workspace, excluded_roots={".agents", ".codex", ".git"}
            )
            if benchmark_manifest != fixture_manifest:
                raise LiveEvalError("Skill injection changed benchmark source files")
            output_schema = SKILL_DIR / "benchmarks" / "codex-live-v1" / "model-output.schema.json"
            command = [
                *codex_command,
                "exec",
                "--ephemeral",
                "--json",
                "--strict-config",
                "--ignore-user-config",
                "--ignore-rules",
                "--sandbox",
                "workspace-write",
                "--skip-git-repo-check",
                "--cd",
                str(workspace),
                "--model",
                model,
                "--config",
                f'model_reasoning_effort="{reasoning}"',
                "--output-schema",
                str(output_schema),
                "-",
            ]
            timed_out = False
            try:
                completed = subprocess.run(
                    command,
                    cwd=workspace,
                    input=model_prompt(case).encode("utf-8"),
                    check=False,
                    capture_output=True,
                    timeout=case["resources"]["wall_seconds"],
                    env=os.environ.copy(),
                )
            except subprocess.TimeoutExpired as exc:
                timed_out = True
                completed = subprocess.CompletedProcess(
                    command,
                    124,
                    stdout=exc.stdout or b"",
                    stderr=exc.stderr or b"",
                )
            elapsed = time.perf_counter() - started
            final_manifest = tree_manifest(
                workspace, excluded_roots={".agents", ".codex", ".git"}
            )
            final_support = tree_manifest(workspace, excluded_roots={".git"})
            support_unchanged = all(
                final_support.get(path) == metadata
                for path, metadata in support_manifest.items()
                if path.startswith((".agents/", ".codex/"))
            ) and not any(
                path.startswith((".agents/", ".codex/")) and path not in support_manifest
                for path in final_support
            )
            paths = changed_paths(fixture_manifest, final_manifest)
            policy = case["diff_policy"]
            observed_diff_bytes = diff_bytes(fixture_manifest, final_manifest, paths)
            diff_correct = (
                (bool(paths) if policy["must_change"] else True)
                and all(path_covered(path, policy["allowed_paths"]) for path in paths)
                and observed_diff_bytes <= policy["max_diff_bytes"]
                and support_unchanged
            )
            final_exit, final_oracle_sha = run_oracle(case, oracle_path, workspace)
            usage = parse_jsonl_usage(completed.stdout)
            resources = case["resources"]
            observed_resource_within = (
                elapsed <= resources["wall_seconds"]
                and len(completed.stdout) + len(completed.stderr)
                <= resources["max_artifact_bytes"]
                and usage["tool_calls"] <= resources["max_tool_calls"]
                and usage["input_tokens"] is not None
                and usage["input_tokens"] <= resources["max_input_tokens"]
                and usage["output_tokens"] is not None
                and usage["output_tokens"] <= resources["max_output_tokens"]
                and usage["reasoning_tokens"] is not None
                and usage["reasoning_tokens"] <= resources["max_reasoning_tokens"]
            )
            functional_success = (
                baseline_exit != 0
                and completed.returncode == 0
                and not timed_out
                and usage["protocol_complete"] is True
                and final_exit == 0
                and diff_correct
            )
            result.update(
                {
                    "functional_success": functional_success,
                    "task_success": False,
                    "release_eligible": False,
                    "oracle_passed": baseline_exit != 0 and final_exit == 0,
                    "controller_observed_diff_correct": diff_correct,
                    "no_hard_invariant_violation": support_unchanged and not timed_out,
                    "observed_resource_dimensions_within": observed_resource_within,
                    "all_resource_dimensions_observed": False,
                    "blind_oracle_proven": False,
                    "assured_controller_present": False,
                    "changed_paths": paths,
                    "diff_bytes": observed_diff_bytes,
                    "elapsed_seconds": elapsed,
                    "codex_returncode": completed.returncode,
                    "usage": usage,
                    "baseline_oracle_output_sha256": baseline_oracle_sha,
                    "final_oracle_output_sha256": final_oracle_sha,
                    "codex_stdout_sha256": sha256_bytes(completed.stdout),
                    "codex_stderr_sha256": sha256_bytes(completed.stderr),
                }
            )
    except (LiveEvalError, OSError, subprocess.SubprocessError, zipfile.BadZipFile) as exc:
        result["infrastructure_error"] = f"{type(exc).__name__}: {exc}"
    return result


def exact_one_sided_lower(successes: int, total: int, alpha: float = 0.05) -> float:
    if total < 1 or not 0 <= successes <= total:
        raise ValueError("invalid binomial counts")
    if successes == 0:
        return 0.0
    if successes == total:
        return alpha ** (1.0 / total)

    def upper_tail(probability: float) -> float:
        return sum(
            math.comb(total, index)
            * probability**index
            * (1.0 - probability) ** (total - index)
            for index in range(successes, total + 1)
        )

    low, high = 0.0, 1.0
    for _ in range(120):
        middle = (low + high) / 2
        if upper_tail(middle) < alpha:
            low = middle
        else:
            high = middle
    return (low + high) / 2


def validate_external_anchor(
    suite: dict[str, Any],
    results: Any,
    anchor: Any,
    *,
    expected_repository: str,
    expected_commit: str,
    expected_challenge_sha256: str,
    expected_skill_sha256: str,
    now: datetime | None = None,
) -> dict[str, Any]:
    if not isinstance(anchor, dict) or set(anchor) != ANCHOR_KEYS:
        raise LiveEvalError("external anchor has an invalid field set")
    if anchor.get("version") != ANCHOR_VERSION:
        raise LiveEvalError("external anchor version is invalid")
    if not nonempty(expected_repository) or anchor.get("repository") != expected_repository:
        raise LiveEvalError("external anchor names a different repository")
    if COMMIT_RE.fullmatch(expected_commit) is None:
        raise LiveEvalError("expected release commit is invalid")
    if anchor.get("candidate_commit_sha") != expected_commit:
        raise LiveEvalError("external anchor names a different candidate commit")
    if not is_sha(expected_challenge_sha256):
        raise LiveEvalError("expected controller challenge digest is invalid")
    if anchor.get("challenge_sha256") != expected_challenge_sha256:
        raise LiveEvalError("external anchor names a different controller challenge")
    if not is_sha(expected_skill_sha256):
        raise LiveEvalError("expected Skill digest is invalid")

    suite_digest = sha256_json(suite)
    results_digest = sha256_json(results)
    exact_bindings = {
        "suite_sha256": suite_digest,
        "results_sha256": results_digest,
        "skill_sha256": expected_skill_sha256,
        "benchmark_id": suite["benchmark_id"],
        "controller_ref": results.get("controller_ref") if isinstance(results, dict) else None,
        "controller_bundle_sha256": (
            results.get("controller_bundle_sha256") if isinstance(results, dict) else None
        ),
        "environment_sha256": (
            results.get("environment_sha256") if isinstance(results, dict) else None
        ),
        "cli_sha256": suite["cli_sha256"],
        "model_request": suite["model_request"],
        "reasoning_request": suite["reasoning_request"],
    }
    for field, expected in exact_bindings.items():
        if anchor.get(field) != expected:
            raise LiveEvalError(f"external anchor {field} differs from its bound value")
    if suite.get("skill_sha256") != expected_skill_sha256:
        raise LiveEvalError("frozen suite Skill digest differs from the candidate checkout")
    if not is_sha(anchor.get("controller_config_sha256")):
        raise LiveEvalError("external anchor controller_config_sha256 is invalid")
    run_id = anchor.get("controller_run_id")
    if not isinstance(run_id, str) or ID_RE.fullmatch(run_id) is None:
        raise LiveEvalError("external anchor controller_run_id is invalid")

    issued_at = parse_utc_timestamp(anchor.get("issued_at"), "external anchor issued_at")
    expires_at = parse_utc_timestamp(anchor.get("expires_at"), "external anchor expires_at")
    observed_now = now or datetime.now(timezone.utc)
    if observed_now.tzinfo is None:
        raise LiveEvalError("anchor validation time must be timezone-aware")
    observed_now = observed_now.astimezone(timezone.utc)
    if issued_at > observed_now + timedelta(minutes=5):
        raise LiveEvalError("external anchor was issued too far in the future")
    if expires_at <= observed_now:
        raise LiveEvalError("external anchor has expired")
    if expires_at <= issued_at or expires_at - issued_at > timedelta(hours=24):
        raise LiveEvalError("external anchor validity window is invalid")
    return anchor


def validate_external_results(
    suite: dict[str, Any],
    results: Any,
    expected_digest: str,
    expected_release_commit: str,
) -> dict[str, Any]:
    if not is_sha(expected_digest) or sha256_json(results) != expected_digest:
        raise LiveEvalError("external result digest differs from the independent anchor")
    if not isinstance(results, dict) or set(results) != EXTERNAL_KEYS:
        raise LiveEvalError("external results have an invalid field set")
    suite_digest = sha256_json(suite)
    errors: list[str] = []
    if results.get("version") != RESULTS_VERSION:
        errors.append("external results version is invalid")
    if (
        COMMIT_RE.fullmatch(expected_release_commit) is None
        or results.get("release_commit") != expected_release_commit
    ):
        errors.append("external results release commit is invalid or mismatched")
    if results.get("suite_sha256") != suite_digest:
        errors.append("external results name a different suite")
    for field in ("controller_ref",):
        if not nonempty(results.get(field)):
            errors.append(f"{field} must be non-empty")
    for field in ("controller_bundle_sha256", "environment_sha256", "skill_sha256", "cli_sha256"):
        if not is_sha(results.get(field)):
            errors.append(f"{field} must be a SHA-256")
    for field in ("skill_sha256", "cli_sha256", "model_request", "reasoning_request"):
        if results.get(field) != suite.get(field):
            errors.append(f"external results {field} differs from the suite")
    assurances = results.get("assurances")
    if not isinstance(assurances, dict) or set(assurances) != ASSURANCE_KEYS:
        errors.append("external assurances have an invalid field set")
    elif any(assurances.get(field) is not True for field in ASSURANCE_KEYS):
        errors.append("every external assurance must be exactly true")
    rows = results.get("cases")
    suite_cases = {case["id"]: case for case in suite["cases"]}
    if not isinstance(rows, list) or len(rows) != len(suite_cases):
        errors.append("external result count differs from the suite")
        rows = []
    seen: set[str] = set()
    successful: set[str] = set()
    for index, row in enumerate(rows):
        location = f"external case {index}"
        if not isinstance(row, dict) or set(row) != EXTERNAL_CASE_KEYS:
            errors.append(f"{location} has an invalid field set")
            continue
        case_id = row.get("id")
        source = suite_cases.get(case_id)
        if source is None or case_id in seen:
            errors.append(f"{location} id is unknown or duplicate")
            continue
        seen.add(case_id)
        row_errors: list[str] = []
        if row.get("stratum") != source["stratum"]:
            row_errors.append("stratum differs from the suite")
        actors = row.get("execution_actor_ids")
        if (
            not isinstance(actors, list)
            or not actors
            or not all(isinstance(actor, str) and ID_RE.fullmatch(actor) for actor in actors)
            or len(actors) != len(set(actors))
        ):
            row_errors.append("execution_actor_ids is invalid")
            actors = []
        integrator = row.get("integrator_id")
        verifier = row.get("verifier_id")
        if integrator not in actors:
            row_errors.append("integrator is not an execution actor")
        if not isinstance(verifier, str) or ID_RE.fullmatch(verifier) is None or verifier in actors:
            row_errors.append("verifier identity is invalid or overlaps execution")
        try:
            baseline = validate_manifest(row.get("baseline_manifest"), f"{location} baseline")
            final = validate_manifest(row.get("final_manifest"), f"{location} final")
            observed = observe_manifest_diff(baseline, final)
        except LiveEvalError as exc:
            row_errors.append(str(exc))
            observed = {
                "baseline_tree_sha256": "",
                "final_tree_sha256": "",
                "diff_sha256": "",
                "changed_paths": [],
                "diff_bytes": 0,
                "minimum_artifact_bytes": 0,
            }
        if observed["baseline_tree_sha256"] != source["fixture"]["baseline_tree_sha256"]:
            row_errors.append("baseline tree differs from the frozen fixture")
        controller_observed = row.get("controller_observed")
        expected_observation = {
            field: observed[field] for field in OBSERVATION_KEYS
        }
        if (
            not isinstance(controller_observed, dict)
            or set(controller_observed) != OBSERVATION_KEYS
            or canonical_json(controller_observed) != canonical_json(expected_observation)
        ):
            row_errors.append("controller observation differs from recomputed manifests")
        policy = source["diff_policy"]
        observed_paths = observed["changed_paths"]
        if policy["must_change"] and not observed_paths:
            row_errors.append("frozen task required a change")
        if any(not path_covered(path, policy["allowed_paths"]) for path in observed_paths):
            row_errors.append("controller-observed changed path exceeds frozen scope")
        if observed["diff_bytes"] > policy["max_diff_bytes"]:
            row_errors.append("controller-observed diff exceeds the byte limit")
        invariants = row.get("invariants")
        if (
            not isinstance(invariants, dict)
            or set(invariants) != INVARIANT_KEYS
            or canonical_json(invariants) != canonical_json(EXPECTED_INVARIANTS)
        ):
            row_errors.append("hard invariants differ from the required values")
        usage = row.get("resource_usage")
        if not isinstance(usage, dict) or set(usage) != USAGE_KEYS:
            row_errors.append("resource usage has an invalid field set")
        elif any(type(usage.get(field)) is not int or usage.get(field) < 0 for field in USAGE_KEYS):
            row_errors.append("resource usage must contain non-negative integers")
        else:
            limits = source["resources"]
            comparisons = {
                "input_tokens": "max_input_tokens",
                "output_tokens": "max_output_tokens",
                "reasoning_tokens": "max_reasoning_tokens",
                "tool_calls": "max_tool_calls",
                "process_seconds": "max_process_seconds",
                "artifact_bytes": "max_artifact_bytes",
                "peak_concurrency": "max_concurrency",
            }
            if any(usage[field] > limits[limit] for field, limit in comparisons.items()):
                row_errors.append("resource usage exceeds the frozen envelope")
            if usage["artifact_bytes"] < observed["minimum_artifact_bytes"]:
                row_errors.append("artifact byte usage is lower than the observed diff evidence")
        oracle_result = row.get("oracle_result")
        if not isinstance(oracle_result, dict) or set(oracle_result) != ORACLE_RESULT_KEYS:
            row_errors.append("oracle result has an invalid field set")
        else:
            if oracle_result.get("definition_sha256") != sha256_json(source["oracle"]):
                row_errors.append("oracle definition digest differs from the frozen suite")
            if type(oracle_result.get("exit_code")) is not int or oracle_result.get("exit_code") != 0:
                row_errors.append("independent verifier oracle did not pass")
        if not is_sha(row.get("detail_sha256")):
            row_errors.append("detail_sha256 is invalid")
        if row_errors:
            errors.extend(f"{location} {error}" for error in row_errors)
        else:
            successful.add(case_id)
    if seen != set(suite_cases):
        errors.append("external results do not cover every suite case")
    stratum_counts = {
        stratum: sum(row.get("stratum") == stratum for row in rows if isinstance(row, dict))
        for stratum in RELEASE_STRATA
    }
    release_shape = (
        tuple(suite.get("strata", [])) == RELEASE_STRATA
        and len(suite_cases) == RELEASE_TASKS
        and all(stratum_counts[stratum] == TASKS_PER_STRATUM for stratum in RELEASE_STRATA)
    )
    if not release_shape:
        errors.append("suite does not have the frozen 6x25 release shape")
    successes = len(successful)
    lower = exact_one_sided_lower(successes, len(rows)) if rows else 0.0
    if successes != RELEASE_TASKS or lower < 0.98:
        errors.append("external live result does not satisfy 150/150 and the 98% lower bound")
    if errors:
        raise LiveEvalError("; ".join(errors))
    return {
        "passed": True,
        "external_receipt_valid": True,
        "evidence_level": "signed-controller-receipt-relative-to-supplied-trust-root",
        "claim_scope": "frozen 150-case live coding suite and signed controller configuration",
        "release_authority": "not decided by this process; protected external environment required",
        "suite_sha256": suite_digest,
        "external_results_sha256": expected_digest,
        "successes": successes,
        "total": len(rows),
        "exact_lower_bound": lower,
        "one_sided_confidence": 0.95,
        "strata": {stratum: {"successes": TASKS_PER_STRATUM, "total": TASKS_PER_STRATUM} for stratum in RELEASE_STRATA},
    }


def run_local(
    suite: dict[str, Any],
    *,
    suite_path: Path,
    oracle_root: Path,
    skill_root: Path,
    codex_command: list[str],
    jobs: int,
) -> dict[str, Any]:
    observed_skill_sha = skill_digest(skill_root)
    if observed_skill_sha != suite["skill_sha256"]:
        raise LiveEvalError("local Skill digest differs from the frozen suite")
    if any(case["task"]["assurance"] != "practical" for case in suite["cases"]):
        raise LiveEvalError("local provider only accepts practical cases")
    kwargs = {
        "suite_root": suite_path.parent,
        "oracle_root": oracle_root,
        "skill_root": skill_root,
        "codex_command": codex_command,
        "model": suite["model_request"],
        "reasoning": suite["reasoning_request"],
    }
    rows: list[dict[str, Any]] = []
    with ThreadPoolExecutor(max_workers=jobs) as executor:
        pending = {
            executor.submit(local_case, case, **kwargs): case["id"]
            for case in suite["cases"]
        }
        for future in as_completed(pending):
            try:
                rows.append(future.result())
            except Exception as exc:  # pragma: no cover - defensive process boundary
                rows.append(
                    {
                        "id": pending[future],
                        "stratum": "unknown",
                        "functional_success": False,
                        "task_success": False,
                        "release_eligible": False,
                        "infrastructure_error": f"{type(exc).__name__}: {exc}",
                    }
                )
    rows.sort(key=lambda row: row["id"])
    thread_ids = [
        row.get("usage", {}).get("thread_id")
        for row in rows
        if isinstance(row.get("usage"), dict)
    ]
    unique_fresh_threads = (
        len(thread_ids) == len(rows)
        and all(nonempty(thread_id) for thread_id in thread_ids)
        and len(set(thread_ids)) == len(thread_ids)
    )
    functional = sum(row.get("functional_success") is True for row in rows)
    formal = sum(row.get("task_success") is True for row in rows)
    return {
        "passed": functional == len(rows) and unique_fresh_threads,
        "release_eligible": False,
        "evidence_level": "practical-host-observed",
        "claim_scope": "local live coding functionality; not blind, assured, or release-qualifying",
        "suite_sha256": sha256_json(suite),
        "skill_sha256": observed_skill_sha,
        "functional_successes": functional,
        "formal_task_successes": formal,
        "total": len(rows),
        "functional_exact_lower_bound": exact_one_sided_lower(functional, len(rows)),
        "formal_exact_lower_bound": exact_one_sided_lower(formal, len(rows)),
        "blind_oracle_proven": False,
        "assured_controller_present": False,
        "all_resource_dimensions_observed": False,
        "fresh_thread_ids_unique": unique_fresh_threads,
        "cases": rows,
    }


def parse_command_json(value: str) -> list[str]:
    try:
        command = json.loads(value)
    except json.JSONDecodeError as exc:
        raise argparse.ArgumentTypeError("command must be a JSON string array") from exc
    if not isinstance(command, list) or not command or not all(nonempty(item) for item in command):
        raise argparse.ArgumentTypeError("command must be a non-empty JSON string array")
    return command


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--suite", type=Path, required=True)
    parser.add_argument("--provider", choices=("local", "external-results"), required=True)
    parser.add_argument("--skill-root", type=Path)
    parser.add_argument("--oracle-root", type=Path)
    parser.add_argument("--codex-command-json", type=parse_command_json)
    parser.add_argument("--jobs", type=int, default=1)
    parser.add_argument("--external-results", type=Path)
    parser.add_argument("--external-anchor", type=Path)
    parser.add_argument("--controller-signature", type=Path)
    parser.add_argument("--controller-allowed-signers", type=Path)
    parser.add_argument("--expected-repository")
    parser.add_argument("--expected-release-commit")
    parser.add_argument("--expect-controller-challenge-sha256")
    parser.add_argument("--results", type=Path)
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    try:
        suite = validate_suite(load_json(args.suite.resolve(strict=True)))
        if args.provider == "local":
            if args.jobs < 1:
                raise LiveEvalError("jobs must be positive")
            if args.skill_root is None or args.oracle_root is None or args.codex_command_json is None:
                raise LiveEvalError(
                    "local provider requires --skill-root, --oracle-root, and --codex-command-json"
                )
            external_only = (
                args.external_results,
                args.external_anchor,
                args.controller_signature,
                args.controller_allowed_signers,
                args.expected_repository,
                args.expected_release_commit,
                args.expect_controller_challenge_sha256,
            )
            if any(value is not None for value in external_only):
                raise LiveEvalError("local provider rejects external-result arguments")
            payload = run_local(
                suite,
                suite_path=args.suite.resolve(strict=True),
                oracle_root=args.oracle_root.resolve(strict=True),
                skill_root=args.skill_root.resolve(strict=True),
                codex_command=args.codex_command_json,
                jobs=args.jobs,
            )
        else:
            required = (
                args.external_results,
                args.external_anchor,
                args.controller_signature,
                args.controller_allowed_signers,
                args.expected_repository,
                args.expected_release_commit,
                args.expect_controller_challenge_sha256,
                args.skill_root,
            )
            if any(value is None for value in required):
                raise LiveEvalError(
                    "external-results provider requires signed anchor, protected trust-root, "
                    "candidate identity, and Skill checkout arguments"
                )
            if any(value is not None for value in (args.oracle_root, args.codex_command_json)):
                raise LiveEvalError("external-results provider rejects local execution arguments")
            external = load_json(args.external_results.resolve(strict=True))
            anchor = load_json(args.external_anchor.resolve(strict=True))
            observed_skill_sha = skill_digest(args.skill_root.resolve(strict=True))
            validate_external_anchor(
                suite,
                external,
                anchor,
                expected_repository=args.expected_repository,
                expected_commit=args.expected_release_commit,
                expected_challenge_sha256=args.expect_controller_challenge_sha256,
                expected_skill_sha256=observed_skill_sha,
            )
            verify_anchor_signature(
                anchor,
                args.controller_signature.resolve(strict=True),
                args.controller_allowed_signers.resolve(strict=True),
            )
            payload = validate_external_results(
                suite,
                external,
                anchor["results_sha256"],
                args.expected_release_commit,
            )
            payload["external_anchor_sha256"] = sha256_json(anchor)
    except (LiveEvalError, OSError, ValueError) as exc:
        payload = {
            "passed": False,
            "release_eligible": False,
            "error": f"{type(exc).__name__}: {exc}",
        }
    output = json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True)
    print(output)
    if args.results is not None:
        args.results.parent.mkdir(parents=True, exist_ok=True)
        args.results.write_text(output + "\n", encoding="utf-8", newline="\n")
    return 0 if payload.get("passed") is True else 1


if __name__ == "__main__":
    raise SystemExit(main())
