#!/usr/bin/env python3
"""Fail-closed gate for externally anchored Wide-Lens packet-v5 delivery."""

from __future__ import annotations

import argparse
import copy
import hashlib
import json
import os
import re
import stat
import sys
from pathlib import Path
from typing import Any, Iterable

from check_delivery import (
    _canonical_existing_scope_path,
    _canonical_repository_root,
    _file_sha256,
    _is_inside,
    _is_reparse_point,
    _repository_identity,
    build_state_manifest,
    evaluate as evaluate_v4,
    run_frozen_checks,
    state_manifest_changed_paths,
    state_manifest_sha256,
    validate_packet_preflight as validate_packet_v4,
    validate_scope_paths_against_repo,
    validate_state_manifest,
)
from diverge import (
    build_participant_prompts,
    canonical_json_bytes,
    contract_sha256,
    freeze_contract,
    packet_sha256,
    repo_path,
    runtime_identity,
    scope_path_key,
)
from diverge_v5 import (
    CAPABILITY_NAMES,
    PLAN_KEYS,
    packet_v4_projection,
    build_task_prompt_v5,
    sha256_json,
    validate_coordination_plan,
    validate_host_capabilities,
    validate_packet_v5,
)


MAX_JSON_BYTES = 16 * 1024 * 1024
MAX_JSON_DEPTH = 128
SHA256_RE = re.compile(r"[0-9a-f]{64}")
RESOURCE_KEYS = {"version", "limits"}
RESOURCE_LIMIT_KEYS = {
    "max_tokens",
    "max_tool_calls",
    "max_process_seconds",
    "max_artifact_bytes",
    "max_concurrency",
}
SANDBOX_KEYS = {
    "version",
    "isolation",
    "candidate_workspace_write",
    "candidate_network_access",
    "candidate_credential_access",
    "candidate_target_repository_mounted",
    "candidate_git_common_dir_mounted",
    "candidate_artifact_store_mounted",
    "verifier_write_access",
    "verifier_candidate_outputs_visible",
    "gate_network_access",
    "gate_credential_access",
    "orphan_detection",
    "canonical_repository_frozen",
}
ENVELOPE_KEYS = {
    "version",
    "packet_sha256",
    "controller_ref",
    "host_capabilities_ref",
    "host_capabilities_sha256",
    "task_graph_ref",
    "task_graph_sha256",
    "resource_envelope_ref",
    "resource_envelope_sha256",
    "sandbox_profile_ref",
    "sandbox_profile_sha256",
    "previous_envelope_sha256",
    "predecessor_execution_started",
    "sealed_before_first_spawn",
    "narrowing_attested",
}
EXECUTION_KEYS = {
    "version",
    "packet_sha256",
    "controller_ref",
    "orchestration_envelope_sha256",
    "task_graph_sha256",
    "deliberation_sha256",
    "actors",
    "leases",
    "candidates",
    "integrations",
    "events",
    "complete_event_capture",
    "orphan_processes_detected",
    "canonical_pre_acceptance",
    "resource_usage",
    "policy_violations",
}
ACTOR_KEYS = {"id", "parent_id", "kind", "task_ids", "workspace_ref"}
ACTOR_KINDS = {"analysis-worker", "candidate-worker", "main-integrator"}
LEASE_KEYS = {
    "id",
    "task_id",
    "actor_id",
    "grant_sequence",
    "terminal_sequence",
    "state",
    "task_prompt_sha256",
    "capabilities",
    "read_paths",
    "candidate_write_paths",
    "acceptance_ids",
}
LEASE_STATES = {"completed", "cancelled", "failed"}
CANDIDATE_KEYS = {
    "id",
    "task_id",
    "actor_id",
    "lease_id",
    "workspace_ref",
    "workspace_isolated",
    "canonical_write_blocked",
    "base_state_sha256",
    "bundle_ref",
    "bundle_sha256",
    "changed_paths",
    "local_checks_sha256",
    "target_repository_write_detected",
    "artifact_store_write_detected",
    "verifier_access_detected",
    "network_access",
    "credential_access",
    "shared_git_access",
}
INTEGRATION_KEYS = {
    "task_id",
    "candidate_id",
    "bundle_sha256",
    "integrator_id",
    "disposition",
    "reason",
}
CANDIDATE_DISPOSITIONS = {"selected", "rejected", "failed"}
EVENT_KEYS = {
    "sequence",
    "event",
    "task_id",
    "actor_id",
    "lease_id",
    "candidate_id",
    "artifact_sha256",
}
EVENT_TYPES = {
    "envelope-sealed",
    "actor-spawned",
    "claim-attempt",
    "claim-denied",
    "lease-granted",
    "lease-completed",
    "lease-cancelled",
    "lease-failed",
    "round1-sealed",
    "peer-message",
    "peer-board-relayed",
    "candidate-produced",
    "candidate-selected",
    "candidate-rejected",
    "candidate-failed",
    "integration-completed",
}
CANONICAL_KEYS = {
    "repository_ref",
    "baseline_state_sha256",
    "final_state_sha256",
    "diff_sha256",
    "changed_paths",
    "integrator_id",
    "non_integrator_writes_detected",
}
USAGE_KEYS = {"tokens", "tool_calls", "process_seconds", "artifact_bytes", "peak_concurrency"}
VERIFICATION_KEYS = {
    "version",
    "packet_sha256",
    "orchestration_envelope_sha256",
    "execution_receipt_sha256",
    "controller_ref",
    "verifier_id",
    "verifier_bundle_sha256",
    "repository_ref",
    "final_state_sha256",
    "diff_sha256",
    "fresh_context",
    "write_access",
    "candidate_outputs_visible",
    "checks",
    "verdict",
    "policy_violations",
}
VERIFICATION_CHECK_KEYS = {"criterion_id", "command", "exit_code"}
ORCHESTRATION_REPORT_KEYS = {
    "envelope_sha256",
    "execution_receipt_sha256",
    "verification_receipt_sha256",
    "candidates",
}
REPORT_CANDIDATE_KEYS = {"task_id", "candidate_id", "bundle_sha256", "disposition", "reason"}


class GateError(ValueError):
    """A v5 trust or schema invariant failed."""


def _nonempty(value: Any) -> bool:
    return isinstance(value, str) and bool(value.strip())


def _sha(value: Any) -> bool:
    return isinstance(value, str) and SHA256_RE.fullmatch(value) is not None


def _json_digest(value: Any) -> str:
    return hashlib.sha256(canonical_json_bytes(value)).hexdigest()


def _unique_object(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            raise GateError(f"duplicate JSON key: {key}")
        result[key] = value
    return result


def _reject_constant(value: str) -> None:
    raise GateError(f"non-finite JSON number: {value}")


def _assert_json_depth(value: Any, maximum: int = MAX_JSON_DEPTH) -> None:
    pending: list[tuple[Any, int]] = [(value, 1)]
    while pending:
        current, depth = pending.pop()
        if depth > maximum:
            raise GateError(f"JSON nesting exceeds {maximum}")
        if isinstance(current, dict):
            pending.extend((item, depth + 1) for item in current.values())
        elif isinstance(current, list):
            pending.extend((item, depth + 1) for item in current)


def load_json(path: Path) -> Any:
    if not path.is_file() or path.is_symlink() or _is_reparse_point(path):
        raise GateError(f"artifact must be a plain file: {path}")
    size = path.stat().st_size
    if size > MAX_JSON_BYTES:
        raise GateError(f"JSON artifact exceeds {MAX_JSON_BYTES} bytes: {path}")
    try:
        value = json.loads(
            path.read_text(encoding="utf-8"),
            object_pairs_hook=_unique_object,
            parse_constant=_reject_constant,
        )
    except UnicodeDecodeError as exc:
        raise GateError(f"JSON artifact is not UTF-8: {path}") from exc
    except json.JSONDecodeError as exc:
        raise GateError(f"invalid JSON in {path}: {exc}") from exc
    _assert_json_depth(value)
    return value


def _plain_external_file(path: Path, repository: Path, label: str) -> Path:
    lexical = Path(os.path.abspath(path))
    _reject_windows_stream_components(lexical, label)
    _reject_linked_components(lexical, label)
    try:
        resolved = lexical.resolve(strict=True)
    except OSError as exc:
        raise GateError(f"{label} does not exist or cannot be resolved") from exc
    if _is_inside(resolved, repository):
        raise GateError(f"{label} must be outside the target repository")
    if not resolved.is_file() or resolved.is_symlink() or _is_reparse_point(resolved):
        raise GateError(f"{label} must be a plain external file")
    if resolved.stat(follow_symlinks=False).st_nlink != 1:
        raise GateError(f"{label} must not be a hard-linked file")
    return resolved


def _plain_external_directory(path_value: Any, repository: Path, label: str) -> Path:
    if not _nonempty(path_value):
        raise GateError(f"{label} must be a non-empty absolute path")
    raw = Path(path_value)
    if not raw.is_absolute():
        raise GateError(f"{label} must be absolute")
    _reject_windows_stream_components(raw, label)
    _reject_linked_components(raw, label)
    try:
        resolved = raw.resolve(strict=True)
    except OSError as exc:
        raise GateError(f"{label} does not exist or cannot be resolved") from exc
    if _is_inside(resolved, repository) or _is_inside(repository, resolved):
        raise GateError(f"{label} overlaps the target repository")
    if not resolved.is_dir() or resolved.is_symlink() or _is_reparse_point(resolved):
        raise GateError(f"{label} must be a plain external directory")
    if (resolved / ".git").exists():
        raise GateError(f"{label} must not expose Git metadata")
    return resolved


def _reject_linked_components(path: Path, label: str) -> None:
    """Reject links/reparse points before resolve() can erase the evidence."""

    absolute = Path(os.path.abspath(path))
    components = [absolute, *absolute.parents]
    for component in reversed(components):
        try:
            os.lstat(component)
        except FileNotFoundError:
            continue
        except OSError as exc:
            raise GateError(f"{label} path components cannot be inspected") from exc
        if component.is_symlink() or _is_reparse_point(component):
            raise GateError(f"{label} path contains a link or reparse point")


def _reject_windows_stream_components(path: Path, label: str) -> None:
    """Reject NTFS alternate data stream syntax without rejecting the drive prefix."""

    if os.name != "nt":
        return
    anchor = path.anchor.casefold()
    for component in path.parts:
        if component.casefold() == anchor:
            continue
        if ":" in component:
            raise GateError(f"{label} path contains an alternate data stream")


def _file_object_identity(path: Path) -> str:
    metadata = path.stat(follow_symlinks=False)
    return f"{metadata.st_dev}:{metadata.st_ino}"


def _scan_plain_tree(root: Path, label: str) -> tuple[set[str], list[str]]:
    """Return file-object IDs and hard-link errors; reject links and Git metadata."""

    identities: set[str] = set()
    errors: set[str] = set()
    for current, directory_names, file_names in os.walk(root, topdown=True, followlinks=False):
        current_path = Path(current)
        for name in [*directory_names, *file_names]:
            path = current_path / name
            try:
                os.lstat(path)
            except OSError as exc:
                raise GateError(f"{label} contains an unreadable entry") from exc
            if path.is_symlink() or _is_reparse_point(path):
                raise GateError(f"{label} contains a link or reparse point: {path}")
            if name.casefold() == ".git":
                raise GateError(f"{label} contains nested Git metadata")
            if path.is_file():
                observed = path.stat(follow_symlinks=False)
                identity = f"{observed.st_dev}:{observed.st_ino}"
                if observed.st_nlink != 1:
                    errors.add(f"{label} contains a hard-linked file")
                if identity in identities:
                    errors.add(f"{label} contains hard-linked files")
                identities.add(identity)
            elif not path.is_dir():
                raise GateError(f"{label} contains a non-regular filesystem object: {path}")
    return identities, sorted(errors)


def _repository_file_objects(
    repository: Path, manifest: dict[str, Any]
) -> tuple[set[str], list[str]]:
    """Observe current canonical file objects without trusting manifest file IDs.

    ``os.DirEntry.stat`` reports ``0:0`` file identities on some Windows Python
    builds, so the frozen v4 manifest cannot be used as a hard-link oracle there.
    The v5 isolation gate instead stats every file named by the freshly observed
    pre-acceptance manifest through ``Path.stat`` and compares those live object
    identities with candidate workspaces and bundles.
    """

    identities: set[str] = set()
    errors: set[str] = set()
    entries = manifest.get("entries", {})
    for relative, metadata in entries.items():
        if not isinstance(metadata, dict) or metadata.get("type") != "file":
            continue
        path = repository.joinpath(*relative.split("/"))
        try:
            observed = path.stat(follow_symlinks=False)
        except OSError as exc:
            raise GateError(
                f"target repository changed during file-object isolation scan: {relative}"
            ) from exc
        if path.is_symlink() or _is_reparse_point(path) or not stat.S_ISREG(observed.st_mode):
            raise GateError(
                f"target repository contains a linked or non-regular file: {relative}"
            )
        identity = f"{observed.st_dev}:{observed.st_ino}"
        if observed.st_nlink != 1 or identity in identities:
            errors.add("target repository contains hard-linked files")
        identities.add(identity)
    return identities, sorted(errors)


def _manifest_file_objects(manifest: dict[str, Any]) -> set[str]:
    """Retain trustworthy historical IDs where the platform supplied them."""

    return {
        metadata["file_id"]
        for metadata in manifest.get("entries", {}).values()
        if isinstance(metadata, dict) and metadata.get("type") == "file"
    }


def _path_identity(value: Any) -> str | None:
    if not _nonempty(value):
        return None
    raw = Path(value)
    if not raw.is_absolute():
        return None
    try:
        _reject_linked_components(raw, "artifact ref")
        return _repository_identity(raw, strict=True)
    except (GateError, OSError, ValueError):
        return None


def _artifact_ref_matches(value: Any, path: Path) -> bool:
    return _path_identity(value) == _repository_identity(path)


def _strict_json_equal(left: Any, right: Any) -> bool:
    try:
        return canonical_json_bytes(left) == canonical_json_bytes(right)
    except (RecursionError, TypeError, UnicodeError, ValueError):
        return False


def _paths_overlap(left: str, right: str, path_case: str, path_flavor: str) -> bool:
    left_key = scope_path_key(left, path_case, path_flavor)
    right_key = scope_path_key(right, path_case, path_flavor)
    return (
        left_key == "."
        or right_key == "."
        or left_key == right_key
        or left_key.startswith(right_key + "/")
        or right_key.startswith(left_key + "/")
    )


def _canonical_paths(
    value: Any,
    path_case: str,
    path_flavor: str,
    *,
    allow_empty: bool = True,
) -> list[str] | None:
    if not isinstance(value, list) or (not allow_empty and not value):
        return None
    if not all(isinstance(item, str) for item in value):
        return None
    normalized = [repo_path(item, path_flavor) for item in value]
    if any(item is None for item in normalized):
        return None
    result = [item for item in normalized if item is not None]
    keys = [scope_path_key(item, path_case, path_flavor) for item in result]
    if result != value or len(keys) != len(set(keys)):
        return None
    return result


def _validate_paths_against_repository(
    paths: Iterable[str],
    repository: Path,
    *,
    path_case: str,
    path_flavor: str,
    label: str,
) -> list[str]:
    if path_flavor != "windows-win32":
        return []
    if os.name != "nt":
        return [f"{label}: windows-win32 paths require a Windows controller"]
    errors: list[str] = []
    for index, supplied in enumerate(paths):
        try:
            canonical = _canonical_existing_scope_path(repository, supplied)
        except (OSError, ValueError) as exc:
            errors.append(f"{label}[{index}]: {exc}")
            continue
        if scope_path_key(supplied, path_case, path_flavor) != scope_path_key(
            canonical, path_case, path_flavor
        ) or supplied != canonical:
            errors.append(
                f"{label}[{index}]: filesystem alias is forbidden; use {canonical!r}"
            )
    return errors


def _diff_digest(
    repository_ref: str,
    baseline_state_sha256: str,
    final_state_sha256: str,
    changed_paths: list[str],
) -> str:
    return _json_digest(
        {
            "repository_ref": repository_ref,
            "baseline_state_sha256": baseline_state_sha256,
            "final_state_sha256": final_state_sha256,
            "changed_paths": changed_paths,
        }
    )


def validate_resource_envelope(value: Any) -> list[str]:
    errors: list[str] = []
    if not isinstance(value, dict) or set(value) != RESOURCE_KEYS:
        return ["resource envelope has an invalid field set"]
    if type(value.get("version")) is not int or value.get("version") != 1:
        errors.append("resource envelope version must be integer 1")
    limits = value.get("limits")
    if not isinstance(limits, dict) or set(limits) != RESOURCE_LIMIT_KEYS:
        errors.append("resource envelope limits have an invalid field set")
    elif any(type(limits[name]) is not int or limits[name] < 1 for name in RESOURCE_LIMIT_KEYS):
        errors.append("every resource envelope limit must be a positive integer")
    return errors


def validate_sandbox_profile(value: Any) -> list[str]:
    errors: list[str] = []
    if not isinstance(value, dict) or set(value) != SANDBOX_KEYS:
        return ["sandbox profile has an invalid field set"]
    if type(value.get("version")) is not int or value.get("version") != 1:
        errors.append("sandbox profile version must be integer 1")
    if value.get("isolation") != "os-process":
        errors.append("sandbox profile must attest OS process isolation")
    expected = {
        "candidate_workspace_write": True,
        "candidate_network_access": False,
        "candidate_credential_access": False,
        "candidate_target_repository_mounted": False,
        "candidate_git_common_dir_mounted": False,
        "candidate_artifact_store_mounted": False,
        "verifier_write_access": False,
        "verifier_candidate_outputs_visible": False,
        "gate_network_access": False,
        "gate_credential_access": False,
        "orphan_detection": True,
        "canonical_repository_frozen": True,
    }
    for field, expected_value in expected.items():
        if value.get(field) is not expected_value:
            errors.append(f"sandbox profile {field} must be {str(expected_value).lower()}")
    return errors


def validate_orchestration_envelope(
    value: Any,
    *,
    packet: dict[str, Any],
    host_capabilities: dict[str, Any],
    host_capabilities_path: Path,
    task_graph: dict[str, Any],
    task_graph_path: Path,
    resource_envelope: dict[str, Any],
    resource_envelope_path: Path,
    sandbox_profile: dict[str, Any],
    sandbox_profile_path: Path,
    previous_envelope: Any | None = None,
    prior_task_graph: Any | None = None,
    prior_task_graph_path: Path | None = None,
    repository: Path | None = None,
) -> list[str]:
    errors: list[str] = []
    if not isinstance(value, dict) or set(value) != ENVELOPE_KEYS:
        return ["orchestration envelope has an invalid field set"]
    if type(value.get("version")) is not int or value.get("version") != 1:
        errors.append("orchestration envelope version must be integer 1")
    if value.get("packet_sha256") != packet.get("packet_sha256"):
        errors.append("orchestration envelope is bound to the wrong packet")
    if not _nonempty(value.get("controller_ref")):
        errors.append("orchestration envelope controller_ref must be non-empty")
    nested = (
        (
            "host capabilities",
            "host_capabilities_ref",
            "host_capabilities_sha256",
            host_capabilities,
            host_capabilities_path,
        ),
        ("task graph", "task_graph_ref", "task_graph_sha256", task_graph, task_graph_path),
        (
            "resource envelope",
            "resource_envelope_ref",
            "resource_envelope_sha256",
            resource_envelope,
            resource_envelope_path,
        ),
        (
            "sandbox profile",
            "sandbox_profile_ref",
            "sandbox_profile_sha256",
            sandbox_profile,
            sandbox_profile_path,
        ),
    )
    for label, ref_field, digest_field, artifact, path in nested:
        if not _artifact_ref_matches(value.get(ref_field), path):
            errors.append(f"orchestration envelope {label} ref is invalid")
        if value.get(digest_field) != _json_digest(artifact):
            errors.append(f"orchestration envelope {label} digest is invalid")
    if value.get("sealed_before_first_spawn") is not True:
        errors.append("orchestration envelope must be sealed before first spawn")
    if value.get("narrowing_attested") is not True:
        errors.append("orchestration envelope must attest child authority narrowing")
    revision = task_graph.get("revision")
    previous_digest = value.get("previous_envelope_sha256")
    if revision == 0:
        if (
            previous_digest is not None
            or previous_envelope is not None
            or value.get("predecessor_execution_started") is not None
        ):
            errors.append("initial orchestration envelope must not reference a predecessor")
    else:
        if not isinstance(previous_envelope, dict):
            errors.append("revised orchestration envelope requires its predecessor")
        elif previous_digest != _json_digest(previous_envelope):
            errors.append("orchestration envelope predecessor digest is invalid")
        else:
            if value.get("predecessor_execution_started") is not False:
                errors.append(
                    "assured DAG revision must be sealed before the predecessor starts execution"
                )
            if set(previous_envelope) != ENVELOPE_KEYS:
                errors.append("previous orchestration envelope has an invalid field set")
                return errors
            if type(previous_envelope.get("version")) is not int or previous_envelope.get("version") != 1:
                errors.append("previous orchestration envelope version must be integer 1")
            if previous_envelope.get("packet_sha256") != packet.get("packet_sha256"):
                errors.append("orchestration envelope lineage changed packet authority")
            if previous_envelope.get("task_graph_sha256") != task_graph.get("supersedes_sha256"):
                errors.append("orchestration envelope lineage does not match the prior task graph")
            if previous_envelope.get("controller_ref") != value.get("controller_ref"):
                errors.append("orchestration envelope lineage changed controller identity")
            if previous_envelope.get("sealed_before_first_spawn") is not True:
                errors.append("previous orchestration envelope lacks pre-spawn sealing")
            if previous_envelope.get("narrowing_attested") is not True:
                errors.append("previous orchestration envelope lacks narrowing attestation")
            predecessor = previous_envelope.get("previous_envelope_sha256")
            if predecessor is not None and not _sha(predecessor):
                errors.append("previous orchestration envelope predecessor digest is invalid")
            if not isinstance(prior_task_graph, dict) or prior_task_graph_path is None:
                errors.append("revised orchestration envelope requires the prior task graph artifact")
            else:
                prior_revision = prior_task_graph.get("revision")
                expected_started = None if prior_revision == 0 else False
                if not _strict_json_equal(
                    previous_envelope.get("predecessor_execution_started"),
                    expected_started,
                ):
                    errors.append(
                        "previous orchestration envelope execution-start lineage is invalid"
                    )
                if previous_envelope.get("task_graph_sha256") != _json_digest(prior_task_graph):
                    errors.append("previous orchestration envelope does not bind the supplied prior task graph")
                if not _artifact_ref_matches(
                    previous_envelope.get("task_graph_ref"), prior_task_graph_path
                ):
                    errors.append("previous orchestration envelope prior task graph ref is invalid")
            if repository is None:
                errors.append("previous orchestration envelope validation requires the target repository")
            else:
                previous_artifacts: dict[str, Any] = {}
                for label, ref_field, digest_field, validator in (
                    (
                        "host capabilities",
                        "host_capabilities_ref",
                        "host_capabilities_sha256",
                        validate_host_capabilities,
                    ),
                    (
                        "resource envelope",
                        "resource_envelope_ref",
                        "resource_envelope_sha256",
                        validate_resource_envelope,
                    ),
                    (
                        "sandbox profile",
                        "sandbox_profile_ref",
                        "sandbox_profile_sha256",
                        validate_sandbox_profile,
                    ),
                ):
                    try:
                        prior_path = _plain_external_file(
                            Path(previous_envelope.get(ref_field, "")),
                            repository,
                            f"previous envelope {label}",
                        )
                        prior_artifact = load_json(prior_path)
                    except (GateError, OSError, TypeError, ValueError) as exc:
                        errors.append(str(exc))
                        continue
                    if not _artifact_ref_matches(previous_envelope.get(ref_field), prior_path):
                        errors.append(f"previous orchestration envelope {label} ref is invalid")
                    if previous_envelope.get(digest_field) != _json_digest(prior_artifact):
                        errors.append(f"previous orchestration envelope {label} digest is invalid")
                    previous_artifacts[label] = prior_artifact
                    errors.extend(
                        f"previous orchestration envelope {error}"
                        for error in validator(prior_artifact)
                    )
                prior_capabilities = previous_artifacts.get("host capabilities", {}).get(
                    "capabilities", {}
                )
                current_capabilities = host_capabilities.get("capabilities", {})
                if any(
                    current_capabilities.get(name) is True
                    and prior_capabilities.get(name) is not True
                    for name in CAPABILITY_NAMES
                ):
                    errors.append("orchestration revision expands the host capability ceiling")
                prior_limits = previous_artifacts.get("resource envelope", {}).get(
                    "limits", {}
                )
                current_limits = resource_envelope.get("limits", {})
                if any(
                    type(current_limits.get(name)) is int
                    and type(prior_limits.get(name)) is int
                    and current_limits[name] > prior_limits[name]
                    for name in RESOURCE_LIMIT_KEYS
                ):
                    errors.append("orchestration revision expands the resource envelope")
                prior_sandbox = previous_artifacts.get("sandbox profile")
                if not _strict_json_equal(prior_sandbox, sandbox_profile):
                    errors.append("orchestration revision changes the assured sandbox profile")
    return errors


def project_v5_to_v4(
    packet_v5: dict[str, Any], report_v5: dict[str, Any]
) -> tuple[dict[str, Any], dict[str, Any]]:
    """Reuse the frozen v4 report oracle after removing v5-only authority fields."""

    packet_v4 = packet_v4_projection(packet_v5)
    report_v4 = copy.deepcopy(report_v5)
    report_v4.pop("orchestration", None)
    report_v4["packet_sha256"] = packet_v4["packet_sha256"]
    deliberation = report_v4.get("deliberation")
    if isinstance(deliberation, dict):
        delegation = deliberation.get("delegation")
        if isinstance(delegation, dict):
            delegation["packet_sha256"] = packet_v4["packet_sha256"]
            participants = delegation.get("participants")
            if isinstance(participants, list):
                for participant in participants:
                    if not isinstance(participant, dict):
                        continue
                    participant_id = participant.get("id")
                    lane_ids = participant.get("lane_ids")
                    if runtime_identity(participant_id) and isinstance(lane_ids, list):
                        round1, round2 = build_participant_prompts(
                            participant_id, lane_ids, packet_v4["packet_sha256"]
                        )
                        participant["round1_prompt"] = round1
                        participant["round2_prompt"] = round2
    return packet_v4, report_v4


def validate_v5_report(
    report: Any,
    *,
    packet: dict[str, Any],
    envelope_sha256: str,
    execution_receipt_sha256: str,
    verification_receipt_sha256: str,
    integrations: list[dict[str, Any]],
) -> list[str]:
    if not isinstance(report, dict):
        return ["v5 report must be an object"]
    expected_v4 = {
        "packet_sha256",
        "coordination",
        "risk",
        "intent",
        "implementation",
        "coverage",
        "findings",
        "disagreements",
        "checks",
        "residual_risks",
    }
    if packet.get("coordination") == "shared":
        expected_v4.add("deliberation")
    if set(report) != expected_v4 | {"orchestration"}:
        return ["v5 report has an invalid field set"]
    errors: list[str] = []
    if report.get("packet_sha256") != packet.get("packet_sha256"):
        errors.append("v5 report is bound to the wrong packet")
    if packet.get("coordination") == "shared":
        deliberation = report.get("deliberation")
        delegation = (
            deliberation.get("delegation") if isinstance(deliberation, dict) else None
        )
        if not isinstance(delegation, dict):
            errors.append("v5 shared report lacks delegation authority")
        else:
            if delegation.get("packet_sha256") != packet.get("packet_sha256"):
                errors.append("v5 delegation digest is not bound to the v5 packet")
            participants = delegation.get("participants")
            if not isinstance(participants, list):
                errors.append("v5 delegation participants must be an array")
            else:
                for index, participant in enumerate(participants):
                    if not isinstance(participant, dict):
                        errors.append(f"v5 delegation participant {index} must be an object")
                        continue
                    participant_id = participant.get("id")
                    lane_ids = participant.get("lane_ids")
                    if not runtime_identity(participant_id) or not isinstance(lane_ids, list):
                        errors.append(f"v5 delegation participant {index} identity or lanes are invalid")
                        continue
                    expected_round1, expected_round2 = build_participant_prompts(
                        participant_id, lane_ids, packet["packet_sha256"]
                    )
                    if participant.get("round1_prompt") != expected_round1:
                        errors.append(f"v5 delegation participant {index} Round 1 prompt is unbound")
                    if participant.get("round2_prompt") != expected_round2:
                        errors.append(f"v5 delegation participant {index} Round 2 prompt is unbound")
    orchestration = report.get("orchestration")
    if not isinstance(orchestration, dict) or set(orchestration) != ORCHESTRATION_REPORT_KEYS:
        return errors + ["v5 report orchestration has an invalid field set"]
    expected_digests = {
        "envelope_sha256": envelope_sha256,
        "execution_receipt_sha256": execution_receipt_sha256,
        "verification_receipt_sha256": verification_receipt_sha256,
    }
    for field, digest in expected_digests.items():
        if orchestration.get(field) != digest:
            errors.append(f"v5 report orchestration {field} is invalid")
    candidates = orchestration.get("candidates")
    if not isinstance(candidates, list):
        return errors + ["v5 report candidate dispositions must be an array"]
    expected_candidates = [
        {
            "task_id": item.get("task_id"),
            "candidate_id": item.get("candidate_id"),
            "bundle_sha256": item.get("bundle_sha256"),
            "disposition": item.get("disposition"),
            "reason": item.get("reason"),
        }
        for item in integrations
    ]
    for index, item in enumerate(candidates):
        if not isinstance(item, dict) or set(item) != REPORT_CANDIDATE_KEYS:
            errors.append(f"v5 report candidate {index} has an invalid field set")
    if not _strict_json_equal(candidates, expected_candidates):
        errors.append("v5 report candidate dispositions differ from the execution receipt")
    return errors


def validate_execution_receipt(
    value: Any,
    *,
    packet: dict[str, Any],
    host_capabilities: dict[str, Any],
    task_graph: dict[str, Any],
    resource_envelope: dict[str, Any],
    envelope_sha256: str,
    repository: Path,
    baseline_manifest: dict[str, Any],
    pre_acceptance_manifest: dict[str, Any],
    protected_paths: Iterable[Path],
    report: dict[str, Any] | None = None,
) -> tuple[list[str], list[Path]]:
    errors: list[str] = []
    candidate_bundle_paths: list[Path] = []
    if not isinstance(value, dict) or set(value) != EXECUTION_KEYS:
        return ["execution receipt has an invalid field set"], candidate_bundle_paths
    if type(value.get("version")) is not int or value.get("version") != 2:
        errors.append("execution receipt version must be integer 2")
    if value.get("packet_sha256") != packet.get("packet_sha256"):
        errors.append("execution receipt is bound to the wrong packet")
    if value.get("orchestration_envelope_sha256") != envelope_sha256:
        errors.append("execution receipt is bound to the wrong orchestration envelope")
    if value.get("task_graph_sha256") != _json_digest(task_graph):
        errors.append("execution receipt is bound to the wrong task graph")
    deliberation = report.get("deliberation") if isinstance(report, dict) else None
    expected_deliberation_digest = (
        _json_digest(deliberation) if packet.get("coordination") == "shared" and isinstance(deliberation, dict) else None
    )
    if value.get("deliberation_sha256") != expected_deliberation_digest:
        errors.append("execution receipt is not bound to the complete deliberation")
    if not _nonempty(value.get("controller_ref")):
        errors.append("execution receipt controller_ref must be non-empty")
    if value.get("complete_event_capture") is not True:
        errors.append("execution receipt requires complete event capture")
    if value.get("orphan_processes_detected") is not False:
        errors.append("execution receipt cannot pass with orphan processes")
    if value.get("policy_violations") != []:
        errors.append("execution receipt cannot pass with policy violations")

    task_by_id = {
        task["id"]: task
        for task in task_graph.get("tasks", [])
        if isinstance(task, dict) and runtime_identity(task.get("id"))
    }
    assignment_by_task = {
        assignment["task_id"]: assignment
        for assignment in task_graph.get("assignments", [])
        if isinstance(assignment, dict) and isinstance(assignment.get("task_id"), str)
    }
    scope = packet["contract"]["scope"]
    path_case = scope["path_case"]["value"]
    path_flavor = scope["path_flavor"]["value"]
    for task_id, task in task_by_id.items():
        for field in ("read_paths", "candidate_write_paths"):
            errors.extend(
                _validate_paths_against_repository(
                    task.get(field, []),
                    repository,
                    path_case=path_case,
                    path_flavor=path_flavor,
                    label=f"coordination task {task_id} {field}",
                )
            )

    actors = value.get("actors")
    if not isinstance(actors, list):
        errors.append("execution receipt actors must be an array")
        actors = []
    actor_by_id: dict[str, dict[str, Any]] = {}
    candidate_workspace_paths: dict[str, Path] = {}
    main_actors: list[str] = []
    for index, actor in enumerate(actors):
        location = f"execution actor {index}"
        if not isinstance(actor, dict) or set(actor) != ACTOR_KEYS:
            errors.append(f"{location} has an invalid field set")
            continue
        actor_id = actor.get("id")
        if not runtime_identity(actor_id) or actor_id in actor_by_id:
            errors.append(f"{location} has an invalid or duplicate id")
            continue
        actor_by_id[actor_id] = actor
        kind = actor.get("kind")
        if kind not in ACTOR_KINDS:
            errors.append(f"{location} kind is invalid")
        task_ids = actor.get("task_ids")
        if (
            not isinstance(task_ids, list)
            or not all(isinstance(item, str) and item in task_by_id for item in task_ids)
            or len(task_ids) != len(set(task_ids))
        ):
            errors.append(f"{location} task_ids are invalid")
        if kind == "main-integrator":
            main_actors.append(actor_id)
            if actor_id != "main-thread" or actor.get("parent_id") is not None:
                errors.append("the sole main integrator must be root identity main-thread")
            if actor.get("workspace_ref") is not None:
                errors.append("main integrator workspace_ref must be null")
            if actor.get("task_ids") != []:
                errors.append("main integrator task_ids must be empty")
        else:
            if actor.get("parent_id") != "main-thread":
                errors.append(f"{location} must be directly parented by main-thread")
            if kind == "analysis-worker":
                if actor.get("workspace_ref") is not None:
                    errors.append(f"{location} analysis workspace_ref must be null")
            elif kind == "candidate-worker":
                try:
                    workspace = _plain_external_directory(
                        actor.get("workspace_ref"), repository, f"{location} workspace"
                    )
                except (GateError, OSError) as exc:
                    errors.append(str(exc))
                else:
                    candidate_workspace_paths[actor_id] = workspace
    if main_actors != ["main-thread"]:
        errors.append("execution receipt must contain exactly one main integrator")
    if task_graph.get("execution") == "read-only-proposals" and any(
        actor.get("kind") == "candidate-worker" for actor in actors if isinstance(actor, dict)
    ):
        errors.append("read-only-proposals cannot use candidate workers")
    if task_graph.get("execution") != "isolated-candidates" and candidate_workspace_paths:
        errors.append("candidate workers require isolated-candidates execution")
    all_worker_ids = {
        actor_id
        for actor_id, actor in actor_by_id.items()
        if actor.get("kind") != "main-integrator"
    }
    observed_actor_tasks: list[str] = []
    for actor_id in all_worker_ids:
        observed_actor_tasks.extend(actor_by_id[actor_id].get("task_ids", []))
    if sorted(observed_actor_tasks) != sorted(task_by_id):
        errors.append("worker task ownership must cover each task exactly once")

    protected = [path.resolve(strict=True) for path in protected_paths]
    protected_objects = [_file_object_identity(path) for path in protected]
    if len(protected_objects) != len(set(protected_objects)):
        errors.append("protected artifacts must be distinct file objects, not hard links")
    try:
        live_repository_objects, repository_object_errors = _repository_file_objects(
            repository, pre_acceptance_manifest
        )
        repository_objects = (
            _manifest_file_objects(baseline_manifest) | live_repository_objects
        )
        errors.extend(repository_object_errors)
    except GateError as exc:
        errors.append(str(exc))
        repository_objects = set()
    workspace_items = list(candidate_workspace_paths.items())
    workspace_object_sets: dict[str, set[str]] = {}
    for index, (actor_id, workspace) in enumerate(workspace_items):
        for other_id, other in workspace_items[index + 1 :]:
            if _is_inside(workspace, other) or _is_inside(other, workspace):
                errors.append(f"candidate workspaces overlap: {actor_id}, {other_id}")
        for path in protected:
            if _is_inside(path, workspace) or _is_inside(workspace, path):
                errors.append(f"candidate workspace {actor_id} overlaps a protected artifact")
        try:
            workspace_objects, workspace_errors = _scan_plain_tree(
                workspace, f"candidate workspace {actor_id}"
            )
            errors.extend(workspace_errors)
        except GateError as exc:
            errors.append(str(exc))
            workspace_objects = set()
        workspace_object_sets[actor_id] = workspace_objects
        if workspace_objects & set(protected_objects):
            errors.append(f"candidate workspace {actor_id} hard-links a protected artifact")
        if workspace_objects & repository_objects:
            errors.append(f"candidate workspace {actor_id} hard-links the target repository")
    for index, (actor_id, objects) in enumerate(workspace_object_sets.items()):
        for other_id, other_objects in list(workspace_object_sets.items())[index + 1 :]:
            if objects & other_objects:
                errors.append(f"candidate workspaces share file objects: {actor_id}, {other_id}")

    leases = value.get("leases")
    if not isinstance(leases, list):
        errors.append("execution receipt leases must be an array")
        leases = []
    lease_by_id: dict[str, dict[str, Any]] = {}
    lease_by_task: dict[str, dict[str, Any]] = {}
    grant_sequences: set[int] = set()
    terminal_sequences: set[int] = set()
    prompt_digest_by_lease: dict[str, str] = {}
    true_caps = {
        name for name, enabled in host_capabilities["capabilities"].items() if enabled
    }
    for index, lease in enumerate(leases):
        location = f"execution lease {index}"
        if not isinstance(lease, dict) or set(lease) != LEASE_KEYS:
            errors.append(f"{location} has an invalid field set")
            continue
        lease_id = lease.get("id")
        task_id = lease.get("task_id")
        actor_id = lease.get("actor_id")
        if not runtime_identity(lease_id) or lease_id in lease_by_id:
            errors.append(f"{location} has an invalid or duplicate id")
            continue
        lease_by_id[lease_id] = lease
        if task_id not in task_by_id or task_id in lease_by_task:
            errors.append(f"{location} has an unknown or multiply leased task")
        else:
            lease_by_task[task_id] = lease
        actor = actor_by_id.get(actor_id)
        if actor is None or actor.get("kind") == "main-integrator":
            errors.append(f"{location} actor must be a known worker")
        elif task_id not in actor.get("task_ids", []):
            errors.append(f"{location} task is not assigned to its actor")
        if task_graph.get("dispatch") == "root-assign":
            assignment = assignment_by_task.get(task_id)
            if not isinstance(assignment, dict) or assignment.get("runtime_identity") != actor_id:
                errors.append(f"{location} contradicts root assignment")
        grant = lease.get("grant_sequence")
        terminal = lease.get("terminal_sequence")
        if (
            type(grant) is not int
            or type(terminal) is not int
            or grant < 1
            or terminal <= grant
            or grant in grant_sequences
            or terminal in terminal_sequences
        ):
            errors.append(f"{location} sequence bounds or uniqueness are invalid")
        else:
            grant_sequences.add(grant)
            terminal_sequences.add(terminal)
        if lease.get("state") not in LEASE_STATES:
            errors.append(f"{location} state is invalid")
        capabilities = lease.get("capabilities")
        if (
            not isinstance(capabilities, list)
            or not all(isinstance(item, str) for item in capabilities)
            or len(capabilities) != len(set(capabilities))
            or not set(capabilities) <= true_caps
            or set(capabilities) & {"spawn", "join", "steer_child"}
        ):
            errors.append(f"{location} capabilities exceed the controller ceiling or allow recursion")
        task = task_by_id.get(task_id, {})
        expected_kind = (
            "candidate-worker"
            if task.get("output_contract", {}).get("kind") == "candidate-bundle"
            else "analysis-worker"
        )
        if isinstance(actor, dict) and actor.get("kind") != expected_kind:
            errors.append(f"{location} actor kind does not match the task output contract")
        if isinstance(task, dict) and runtime_identity(actor_id):
            try:
                task_prompt, _, _ = build_task_prompt_v5(
                    packet,
                    task_graph,
                    task,
                    actor_id,
                    envelope_sha256,
                )
                expected_prompt_digest = hashlib.sha256(task_prompt.encode("utf-8")).hexdigest()
            except (KeyError, TypeError, ValueError) as exc:
                errors.append(f"{location} task prompt cannot be reconstructed: {exc}")
            else:
                prompt_digest_by_lease[lease_id] = expected_prompt_digest
                if lease.get("task_prompt_sha256") != expected_prompt_digest:
                    errors.append(f"{location} task prompt digest is not controller-bound")
        for field in ("read_paths", "candidate_write_paths", "acceptance_ids"):
            if not _strict_json_equal(lease.get(field), task.get(field)):
                errors.append(f"{location} {field} differs from the frozen task")
    if set(lease_by_task) != set(task_by_id):
        errors.append("every delegated task must have exactly one controller lease")
    leased_participant_ids = {
        lease.get("actor_id")
        for lease in lease_by_id.values()
        if lease.get("actor_id") in all_worker_ids
    }
    if packet.get("coordination") == "shared" and len(leased_participant_ids) < 2:
        errors.append("shared execution receipt needs at least two leased participant identities")

    candidates = value.get("candidates")
    if not isinstance(candidates, list):
        errors.append("execution receipt candidates must be an array")
        candidates = []
    candidate_by_id: dict[str, dict[str, Any]] = {}
    candidate_changed_paths: dict[str, list[str]] = {}
    for index, candidate in enumerate(candidates):
        location = f"execution candidate {index}"
        if not isinstance(candidate, dict) or set(candidate) != CANDIDATE_KEYS:
            errors.append(f"{location} has an invalid field set")
            continue
        candidate_id = candidate.get("id")
        task_id = candidate.get("task_id")
        actor_id = candidate.get("actor_id")
        lease_id = candidate.get("lease_id")
        if not runtime_identity(candidate_id) or candidate_id in candidate_by_id:
            errors.append(f"{location} has an invalid or duplicate id")
            continue
        candidate_by_id[candidate_id] = candidate
        task = task_by_id.get(task_id)
        actor = actor_by_id.get(actor_id)
        lease = lease_by_id.get(lease_id)
        if task is None or actor is None or actor.get("kind") != "candidate-worker":
            errors.append(f"{location} must reference a candidate task and worker")
        elif task.get("output_contract", {}).get("kind") != "candidate-bundle":
            errors.append(f"{location} task output contract is not candidate-bundle")
        if lease is None or lease.get("task_id") != task_id or lease.get("actor_id") != actor_id:
            errors.append(f"{location} lease binding is invalid")
        if candidate.get("workspace_ref") != (actor or {}).get("workspace_ref"):
            errors.append(f"{location} workspace differs from its actor")
        if candidate.get("workspace_isolated") is not True or candidate.get("canonical_write_blocked") is not True:
            errors.append(f"{location} lacks required workspace isolation proof")
        false_fields = (
            "target_repository_write_detected",
            "artifact_store_write_detected",
            "verifier_access_detected",
            "network_access",
            "credential_access",
            "shared_git_access",
        )
        if any(candidate.get(field) is not False for field in false_fields):
            errors.append(f"{location} reports a forbidden access or write")
        if candidate.get("base_state_sha256") != state_manifest_sha256(baseline_manifest):
            errors.append(f"{location} base state is stale or unanchored")
        if not _sha(candidate.get("local_checks_sha256")):
            errors.append(f"{location} local_checks_sha256 is invalid")
        changed_paths = _canonical_paths(
            candidate.get("changed_paths"),
            path_case,
            path_flavor,
            allow_empty=False,
        )
        if changed_paths is None:
            errors.append(f"{location} changed_paths are invalid")
            changed_paths = []
        else:
            errors.extend(
                _validate_paths_against_repository(
                    changed_paths,
                    repository,
                    path_case=path_case,
                    path_flavor=path_flavor,
                    label=f"{location} changed_paths",
                )
            )
            task_write_paths = task.get("candidate_write_paths", []) if isinstance(task, dict) else []
            for path in changed_paths:
                if not any(
                    _paths_overlap(path, allowed, path_case, path_flavor)
                    and (
                        scope_path_key(path, path_case, path_flavor)
                        == scope_path_key(allowed, path_case, path_flavor)
                        or scope_path_key(allowed, path_case, path_flavor) == "."
                        or scope_path_key(path, path_case, path_flavor).startswith(
                            scope_path_key(allowed, path_case, path_flavor) + "/"
                        )
                    )
                    for allowed in task_write_paths
                ):
                    errors.append(f"{location} changed path exceeds its task write scope: {path}")
            candidate_changed_paths[candidate_id] = changed_paths
        try:
            bundle = _plain_external_file(Path(candidate.get("bundle_ref", "")), repository, f"{location} bundle")
        except (GateError, OSError) as exc:
            errors.append(str(exc))
        else:
            if not _artifact_ref_matches(candidate.get("bundle_ref"), bundle):
                errors.append(f"{location} bundle ref is invalid")
            if candidate.get("bundle_sha256") != _file_sha256(bundle):
                errors.append(f"{location} bundle digest is invalid")
            bundle_object = _file_object_identity(bundle)
            known_bundle_objects = {
                _file_object_identity(path) for path in candidate_bundle_paths
            }
            if (
                bundle in protected
                or bundle in candidate_bundle_paths
                or bundle_object in set(protected_objects)
                or bundle_object in known_bundle_objects
                or bundle_object in repository_objects
                or any(
                    bundle_object in workspace_objects
                    for workspace_objects in workspace_object_sets.values()
                )
            ):
                errors.append(f"{location} bundle must be a distinct external artifact")
            for workspace in candidate_workspace_paths.values():
                if _is_inside(bundle, workspace) or _is_inside(workspace, bundle):
                    errors.append(f"{location} bundle overlaps a candidate workspace")
            candidate_bundle_paths.append(bundle)
    if candidates and task_graph.get("execution") != "isolated-candidates":
        errors.append("candidate artifacts require isolated-candidates execution")
    candidates_by_task: dict[str, list[dict[str, Any]]] = {}
    for candidate in candidates:
        if isinstance(candidate, dict):
            candidates_by_task.setdefault(candidate.get("task_id"), []).append(candidate)
    for task_id, task in task_by_id.items():
        task_candidates = candidates_by_task.get(task_id, [])
        lease = lease_by_task.get(task_id)
        output_kind = task.get("output_contract", {}).get("kind")
        if len(task_candidates) > 1:
            errors.append(f"task {task_id!r} produced more than one v5 candidate")
        if output_kind == "candidate-bundle" and isinstance(lease, dict):
            expected_count = 1 if lease.get("state") == "completed" else 0
            if len(task_candidates) != expected_count:
                errors.append(
                    f"candidate-bundle task {task_id!r} produced {len(task_candidates)} candidates; expected {expected_count}"
                )
        elif task_candidates:
            errors.append(f"non-bundle task {task_id!r} cannot produce candidate artifacts")

    integrations = value.get("integrations")
    if not isinstance(integrations, list):
        errors.append("execution receipt integrations must be an array")
        integrations = []
    integrated_candidates: set[str] = set()
    selected_by_task: dict[str, str] = {}
    selected_ids: list[str] = []
    for index, integration in enumerate(integrations):
        location = f"execution integration {index}"
        if not isinstance(integration, dict) or set(integration) != INTEGRATION_KEYS:
            errors.append(f"{location} has an invalid field set")
            continue
        candidate_id = integration.get("candidate_id")
        candidate = candidate_by_id.get(candidate_id)
        if candidate is None or candidate_id in integrated_candidates:
            errors.append(f"{location} candidate is unknown or duplicated")
            continue
        integrated_candidates.add(candidate_id)
        if integration.get("task_id") != candidate.get("task_id"):
            errors.append(f"{location} task differs from its candidate")
        if integration.get("bundle_sha256") != candidate.get("bundle_sha256"):
            errors.append(f"{location} bundle digest differs from its candidate")
        if integration.get("integrator_id") != "main-thread":
            errors.append(f"{location} canonical integrator must be main-thread")
        disposition = integration.get("disposition")
        if disposition not in CANDIDATE_DISPOSITIONS:
            errors.append(f"{location} disposition is invalid")
        if not _nonempty(integration.get("reason")):
            errors.append(f"{location} reason must be non-empty")
        if disposition == "selected":
            task_id = integration.get("task_id")
            if task_id in selected_by_task:
                errors.append(f"task {task_id!r} selected more than one candidate")
            else:
                selected_by_task[task_id] = candidate_id
                selected_ids.append(candidate_id)
            lease = lease_by_id.get(candidate.get("lease_id"))
            if not isinstance(lease, dict) or lease.get("state") != "completed":
                errors.append(f"{location} selected candidate lease did not complete")
    if integrated_candidates != set(candidate_by_id):
        errors.append("every candidate must have exactly one integration disposition")
    for index, candidate_id in enumerate(selected_ids):
        for other_id in selected_ids[index + 1 :]:
            if any(
                _paths_overlap(left, right, path_case, path_flavor)
                for left in candidate_changed_paths.get(candidate_id, [])
                for right in candidate_changed_paths.get(other_id, [])
            ):
                errors.append(f"selected candidate paths conflict: {candidate_id}, {other_id}")

    events = value.get("events")
    if not isinstance(events, list):
        errors.append("execution receipt events must be an array")
        events = []
    event_by_sequence: dict[int, dict[str, Any]] = {}
    for index, event in enumerate(events):
        location = f"execution event {index}"
        if not isinstance(event, dict) or set(event) != EVENT_KEYS:
            errors.append(f"{location} has an invalid field set")
            continue
        sequence = event.get("sequence")
        if type(sequence) is not int or sequence < 1 or sequence in event_by_sequence:
            errors.append(f"{location} sequence is invalid or duplicate")
            continue
        event_by_sequence[sequence] = event
        if event.get("event") not in EVENT_TYPES:
            errors.append(f"{location} type is invalid")
        for field in ("task_id", "actor_id", "lease_id", "candidate_id"):
            if event.get(field) is not None and not runtime_identity(event.get(field)):
                errors.append(f"{location} {field} is invalid")
        if event.get("artifact_sha256") is not None and not _sha(event.get("artifact_sha256")):
            errors.append(f"{location} artifact_sha256 is invalid")
    if sorted(event_by_sequence) != list(range(1, len(events) + 1)):
        errors.append("execution event sequence must be contiguous from 1")

    consumed_sequences: set[int] = set()

    def exact_events(
        event_type: str,
        *,
        task_id: Any,
        actor_id: Any,
        lease_id: Any,
        candidate_id: Any,
        artifact_sha256: Any,
    ) -> list[dict[str, Any]]:
        return [
            event
            for event in event_by_sequence.values()
            if _strict_json_equal(
                {key: event.get(key) for key in EVENT_KEYS - {"sequence"}},
                {
                    "event": event_type,
                    "task_id": task_id,
                    "actor_id": actor_id,
                    "lease_id": lease_id,
                    "candidate_id": candidate_id,
                    "artifact_sha256": artifact_sha256,
                },
            )
        ]

    def consume_one(matches: list[dict[str, Any]], error: str) -> dict[str, Any] | None:
        if len(matches) != 1:
            errors.append(error)
            return None
        consumed_sequences.add(matches[0]["sequence"])
        return matches[0]

    envelope_seal = consume_one(
        exact_events(
            "envelope-sealed",
            task_id=None,
            actor_id="main-thread",
            lease_id=None,
            candidate_id=None,
            artifact_sha256=envelope_sha256,
        ),
        "execution lacks one controller envelope seal event",
    )
    spawn_event_by_actor: dict[str, dict[str, Any]] = {}
    for actor_id in sorted(all_worker_ids):
        spawned = consume_one(
            exact_events(
                "actor-spawned",
                task_id=None,
                actor_id=actor_id,
                lease_id=None,
                candidate_id=None,
                artifact_sha256=envelope_sha256,
            ),
            f"worker {actor_id!r} lacks one spawn event bound to the orchestration envelope",
        )
        if isinstance(spawned, dict):
            spawn_event_by_actor[actor_id] = spawned
            if not isinstance(envelope_seal, dict) or spawned["sequence"] <= envelope_seal["sequence"]:
                errors.append(f"worker {actor_id!r} spawned before the envelope was sealed")

    terminal_event = {
        "completed": "lease-completed",
        "cancelled": "lease-cancelled",
        "failed": "lease-failed",
    }
    grant_event_by_lease: dict[str, dict[str, Any]] = {}
    terminal_event_by_lease: dict[str, dict[str, Any]] = {}
    for lease_id, lease in lease_by_id.items():
        grant = consume_one(
            exact_events(
                "lease-granted",
                task_id=lease.get("task_id"),
                actor_id=lease.get("actor_id"),
                lease_id=lease_id,
                candidate_id=None,
                artifact_sha256=prompt_digest_by_lease.get(lease_id),
            ),
            f"lease {lease_id!r} lacks one exact bound grant event",
        )
        terminal = consume_one(
            exact_events(
                terminal_event.get(lease.get("state"), ""),
                task_id=lease.get("task_id"),
                actor_id=lease.get("actor_id"),
                lease_id=lease_id,
                candidate_id=None,
                artifact_sha256=None,
            ),
            f"lease {lease_id!r} lacks one exact bound terminal event",
        )
        if isinstance(grant, dict):
            grant_event_by_lease[lease_id] = grant
            if grant.get("sequence") != lease.get("grant_sequence"):
                errors.append(f"lease {lease_id!r} grant_sequence differs from the event log")
            spawned = spawn_event_by_actor.get(lease.get("actor_id"))
            if not isinstance(spawned, dict) or grant["sequence"] <= spawned["sequence"]:
                errors.append(f"lease {lease_id!r} was granted before its actor spawned")
        if isinstance(terminal, dict):
            terminal_event_by_lease[lease_id] = terminal
            if terminal.get("sequence") != lease.get("terminal_sequence"):
                errors.append(f"lease {lease_id!r} terminal_sequence differs from the event log")
        if isinstance(grant, dict) and isinstance(terminal, dict) and grant["sequence"] >= terminal["sequence"]:
            errors.append(f"lease {lease_id!r} terminal event does not follow its grant")
    for task_id, lease in lease_by_task.items():
        grant = grant_event_by_lease.get(lease.get("id"))
        for dependency_id in task_by_id.get(task_id, {}).get("dependencies", []):
            dependency_lease = lease_by_task.get(dependency_id)
            if not isinstance(dependency_lease, dict) or dependency_lease.get("state") != "completed":
                errors.append(
                    f"task {task_id!r} depends on non-completed task {dependency_id!r}"
                )
            dependency_terminal = (
                terminal_event_by_lease.get(dependency_lease.get("id"))
                if isinstance(dependency_lease, dict)
                else None
            )
            if (
                not isinstance(grant, dict)
                or not isinstance(dependency_terminal, dict)
                or grant["sequence"] <= dependency_terminal["sequence"]
            ):
                errors.append(
                    f"task {task_id!r} was granted before dependency {dependency_id!r} reached a terminal state"
                )
    for lease_id in lease_by_id:
        terminal_matches = [
            event
            for event in event_by_sequence.values()
            if event.get("lease_id") == lease_id
            and event.get("event") in {"lease-completed", "lease-cancelled", "lease-failed"}
        ]
        if len(terminal_matches) != 1:
            errors.append(f"lease {lease_id!r} has competing terminal events")

    claim_events = [
        event
        for event in event_by_sequence.values()
        if event.get("event") in {"claim-attempt", "claim-denied"}
    ]
    if task_graph.get("dispatch") == "root-assign":
        if claim_events:
            errors.append("root-assign execution cannot contain atomic claim events")
    else:
        attempts = [event for event in claim_events if event.get("event") == "claim-attempt"]
        for attempt in attempts:
            valid_shape = exact_events(
                "claim-attempt",
                task_id=attempt.get("task_id"),
                actor_id=attempt.get("actor_id"),
                lease_id=None,
                candidate_id=None,
                artifact_sha256=None,
            )
            if attempt not in valid_shape or attempt.get("task_id") not in task_by_id or attempt.get("actor_id") not in all_worker_ids:
                errors.append("atomic claim attempt contains an unknown task/actor or non-null authority")
                continue
            same_attempts = [
                item
                for item in attempts
                if item.get("task_id") == attempt.get("task_id")
                and item.get("actor_id") == attempt.get("actor_id")
            ]
            if len(same_attempts) != 1:
                errors.append("atomic claim actor/task pair must have one attempt")
                continue
            consumed_sequences.add(attempt["sequence"])
            spawned = spawn_event_by_actor.get(attempt.get("actor_id"))
            if not isinstance(spawned, dict) or attempt["sequence"] <= spawned["sequence"]:
                errors.append("atomic claim attempt occurred before its actor spawned")
            winner = lease_by_task.get(attempt.get("task_id"))
            if isinstance(winner, dict) and winner.get("actor_id") == attempt.get("actor_id"):
                grant = grant_event_by_lease.get(winner.get("id"))
                if not isinstance(grant, dict) or attempt["sequence"] >= grant["sequence"]:
                    errors.append("atomic claim grant does not follow its winning attempt")
                denied = exact_events(
                    "claim-denied",
                    task_id=attempt.get("task_id"),
                    actor_id=attempt.get("actor_id"),
                    lease_id=None,
                    candidate_id=None,
                    artifact_sha256=None,
                )
                if denied:
                    errors.append("winning atomic claim cannot also be denied")
            else:
                denied = consume_one(
                    exact_events(
                        "claim-denied",
                        task_id=attempt.get("task_id"),
                        actor_id=attempt.get("actor_id"),
                        lease_id=None,
                        candidate_id=None,
                        artifact_sha256=None,
                    ),
                    "losing atomic claim lacks one exact denial event",
                )
                if isinstance(denied, dict) and denied["sequence"] <= attempt["sequence"]:
                    errors.append("atomic claim denial does not follow its attempt")
        for lease in lease_by_id.values():
            if not any(
                attempt.get("task_id") == lease.get("task_id")
                and attempt.get("actor_id") == lease.get("actor_id")
                for attempt in attempts
            ):
                errors.append(f"lease {lease.get('id')!r} lacks a controller-observed winning claim")

    if packet.get("coordination") == "shared":
        board_digest = deliberation.get("peer_board_sha256") if isinstance(deliberation, dict) else None
        seal = consume_one(
            exact_events(
                "round1-sealed",
                task_id=None,
                actor_id="main-thread",
                lease_id=None,
                candidate_id=None,
                artifact_sha256=board_digest,
            ),
            "shared execution lacks one Round 1 seal bound to the peer board",
        )
        expected_exchange = (
            "peer-message"
            if task_graph.get("communication") == "peer-message"
            else "peer-board-relayed"
        )
        exchanges = [
            event
            for event in event_by_sequence.values()
            if event.get("event") == expected_exchange
        ]
        if not exchanges:
            errors.append("shared execution lacks its declared post-seal evidence exchange")
        if expected_exchange == "peer-board-relayed" and len(exchanges) != 1:
            errors.append("root-relay shared execution must record exactly one board relay")
        for exchange in exchanges:
            expected_actor = exchange.get("actor_id")
            actor_valid = (
                expected_actor in leased_participant_ids
                if expected_exchange == "peer-message"
                else expected_actor == "main-thread"
            )
            exact = exact_events(
                expected_exchange,
                task_id=None,
                actor_id=expected_actor,
                lease_id=None,
                candidate_id=None,
                artifact_sha256=board_digest,
            )
            if exchange not in exact or not actor_valid:
                errors.append("shared evidence exchange is not bound to the peer board and a valid actor")
                continue
            consumed_sequences.add(exchange["sequence"])
            if not isinstance(seal, dict) or exchange["sequence"] <= seal["sequence"]:
                errors.append("shared evidence exchange occurred before sealed Round 1")
        if isinstance(seal, dict):
            if any(
                grant["sequence"] >= seal["sequence"]
                for grant in grant_event_by_lease.values()
            ):
                errors.append("shared Round 1 was sealed before every participant lease was granted")
            final_exchange_sequence = max(
                (exchange["sequence"] for exchange in exchanges), default=seal["sequence"]
            )
            if expected_exchange == "peer-message" and any(
                terminal["sequence"] <= final_exchange_sequence
                for terminal in terminal_event_by_lease.values()
            ):
                errors.append("peer-message worker lease terminated before the evidence exchange completed")
            if expected_exchange == "peer-board-relayed" and exchanges:
                relay_sequence = exchanges[0]["sequence"]
                active_at_seal = [
                    lease_id
                    for lease_id in lease_by_id
                    if isinstance(grant_event_by_lease.get(lease_id), dict)
                    and isinstance(terminal_event_by_lease.get(lease_id), dict)
                    and grant_event_by_lease[lease_id]["sequence"]
                    < seal["sequence"]
                    < terminal_event_by_lease[lease_id]["sequence"]
                ]
                if not active_at_seal:
                    errors.append(
                        "root-relay shared execution had no active lease at Round 1 seal"
                    )
                elif any(
                    terminal_event_by_lease[lease_id]["sequence"] <= relay_sequence
                    for lease_id in active_at_seal
                ):
                    errors.append(
                        "root-relay participant active at Round 1 seal terminated before board relay"
                    )
        if expected_exchange == "peer-message":
            message_actors = {exchange.get("actor_id") for exchange in exchanges}
            if message_actors != leased_participant_ids:
                errors.append("peer-message exchange must include every leased shared participant")
            for exchange in exchanges:
                active = any(
                    lease.get("actor_id") == exchange.get("actor_id")
                    and isinstance(grant_event_by_lease.get(lease_id), dict)
                    and isinstance(terminal_event_by_lease.get(lease_id), dict)
                    and grant_event_by_lease[lease_id]["sequence"]
                    < exchange["sequence"]
                    < terminal_event_by_lease[lease_id]["sequence"]
                    for lease_id, lease in lease_by_id.items()
                )
                if not active:
                    errors.append("peer-message actor had no active controller lease")

    integration_by_candidate = {
        integration.get("candidate_id"): integration
        for integration in integrations
        if isinstance(integration, dict)
    }
    event_for_disposition = {
        "selected": "candidate-selected",
        "rejected": "candidate-rejected",
        "failed": "candidate-failed",
    }
    integration_event_by_candidate: dict[str, dict[str, Any]] = {}
    for candidate_id, candidate in candidate_by_id.items():
        lease = lease_by_id.get(candidate.get("lease_id"), {})
        produced = consume_one(
            exact_events(
                "candidate-produced",
                task_id=candidate.get("task_id"),
                actor_id=candidate.get("actor_id"),
                lease_id=candidate.get("lease_id"),
                candidate_id=candidate_id,
                artifact_sha256=candidate.get("bundle_sha256"),
            ),
            f"candidate {candidate_id!r} lacks one exact bound production event",
        )
        grant = grant_event_by_lease.get(candidate.get("lease_id"))
        terminal = terminal_event_by_lease.get(candidate.get("lease_id"))
        if (
            not isinstance(produced, dict)
            or not isinstance(grant, dict)
            or not isinstance(terminal, dict)
            or not (grant["sequence"] < produced["sequence"] < terminal["sequence"])
        ):
            errors.append(f"candidate {candidate_id!r} production is outside its active lease")
        integration = integration_by_candidate.get(candidate_id)
        if not isinstance(integration, dict):
            continue
        disposition = consume_one(
            exact_events(
                event_for_disposition.get(integration.get("disposition"), ""),
                task_id=candidate.get("task_id"),
                actor_id="main-thread",
                lease_id=candidate.get("lease_id"),
                candidate_id=candidate_id,
                artifact_sha256=candidate.get("bundle_sha256"),
            ),
            f"candidate {candidate_id!r} lacks one exact bound disposition event",
        )
        if not isinstance(disposition, dict) or not isinstance(terminal, dict) or disposition["sequence"] <= terminal["sequence"]:
            errors.append(f"candidate {candidate_id!r} disposition precedes lease termination")
        if integration.get("disposition") == "selected":
            integrated = consume_one(
                exact_events(
                    "integration-completed",
                    task_id=candidate.get("task_id"),
                    actor_id="main-thread",
                    lease_id=candidate.get("lease_id"),
                    candidate_id=candidate_id,
                    artifact_sha256=candidate.get("bundle_sha256"),
                ),
                f"selected candidate {candidate_id!r} lacks one exact integration event",
            )
            if not isinstance(integrated, dict) or not isinstance(disposition, dict) or integrated["sequence"] <= disposition["sequence"]:
                errors.append(f"selected candidate {candidate_id!r} integration precedes selection")
            if isinstance(integrated, dict):
                integration_event_by_candidate[candidate_id] = integrated

    for task_id, task in task_by_id.items():
        grant = grant_event_by_lease.get(lease_by_task.get(task_id, {}).get("id"))
        for dependency_id in task.get("dependencies", []):
            selected_candidate_id = selected_by_task.get(dependency_id)
            integration_event = integration_event_by_candidate.get(selected_candidate_id)
            if (
                selected_candidate_id is not None
                and (
                    not isinstance(grant, dict)
                    or not isinstance(integration_event, dict)
                    or grant["sequence"] <= integration_event["sequence"]
                )
            ):
                errors.append(
                    f"task {task_id!r} was granted before dependency {dependency_id!r} integration completed"
                )

    unconsumed = sorted(set(event_by_sequence) - consumed_sequences)
    if unconsumed:
        errors.append(f"execution event log contains unbound or ghost events at sequences {unconsumed}")

    canonical = value.get("canonical_pre_acceptance")
    if not isinstance(canonical, dict) or set(canonical) != CANONICAL_KEYS:
        errors.append("canonical_pre_acceptance has an invalid field set")
    else:
        baseline_digest = state_manifest_sha256(baseline_manifest)
        final_digest = state_manifest_sha256(pre_acceptance_manifest)
        changed_paths = state_manifest_changed_paths(baseline_manifest, pre_acceptance_manifest)
        expected_diff = _diff_digest(
            pre_acceptance_manifest["repository_ref"],
            baseline_digest,
            final_digest,
            changed_paths,
        )
        expected = {
            "repository_ref": pre_acceptance_manifest["repository_ref"],
            "baseline_state_sha256": baseline_digest,
            "final_state_sha256": final_digest,
            "diff_sha256": expected_diff,
            "changed_paths": changed_paths,
            "integrator_id": "main-thread",
            "non_integrator_writes_detected": False,
        }
        if not _strict_json_equal(canonical, expected):
            errors.append("canonical_pre_acceptance differs from the re-observed repository state")
        selected_changed_paths = {
            scope_path_key(path, path_case, path_flavor)
            for candidate_id in selected_ids
            for path in candidate_changed_paths.get(candidate_id, [])
        }
        canonical_changed_path_keys = {
            scope_path_key(path, path_case, path_flavor) for path in changed_paths
        }
        if not selected_changed_paths <= canonical_changed_path_keys:
            errors.append("selected candidate paths are absent from the canonical diff")

    usage = value.get("resource_usage")
    if not isinstance(usage, dict) or set(usage) != USAGE_KEYS:
        errors.append("execution resource_usage has an invalid field set")
    else:
        if any(type(usage[name]) is not int or usage[name] < 0 for name in USAGE_KEYS):
            errors.append("execution resource usage values must be non-negative integers")
        else:
            limits = resource_envelope["limits"]
            comparisons = {
                "tokens": "max_tokens",
                "tool_calls": "max_tool_calls",
                "process_seconds": "max_process_seconds",
                "artifact_bytes": "max_artifact_bytes",
                "peak_concurrency": "max_concurrency",
            }
            for used, limit in comparisons.items():
                if usage[used] > limits[limit]:
                    errors.append(f"execution resource usage exceeds {limit}")
            candidate_bytes = sum(path.stat().st_size for path in candidate_bundle_paths)
            if usage["artifact_bytes"] < candidate_bytes:
                errors.append("execution artifact byte usage is lower than observed candidate bundles")
            boundaries: list[tuple[int, int]] = []
            for lease_id in lease_by_id:
                grant = grant_event_by_lease.get(lease_id)
                terminal = terminal_event_by_lease.get(lease_id)
                if isinstance(grant, dict) and isinstance(terminal, dict):
                    boundaries.extend(
                        ((grant["sequence"], 1), (terminal["sequence"], -1))
                    )
            active = 0
            observed_lease_peak = 0
            for _, delta in sorted(boundaries, key=lambda item: (item[0], item[1])):
                active += delta
                observed_lease_peak = max(observed_lease_peak, active)
            if usage["peak_concurrency"] < observed_lease_peak:
                errors.append(
                    "execution peak_concurrency is lower than the event-observed active-lease peak"
                )
    return errors, candidate_bundle_paths


def validate_verification_receipt(
    value: Any,
    *,
    packet: dict[str, Any],
    execution_receipt: dict[str, Any],
    envelope_sha256: str,
    execution_receipt_sha256: str,
    verifier_bundle_sha256: str,
    repository_ref: str,
) -> list[str]:
    errors: list[str] = []
    if not isinstance(value, dict) or set(value) != VERIFICATION_KEYS:
        return ["verification receipt has an invalid field set"]
    if type(value.get("version")) is not int or value.get("version") != 1:
        errors.append("verification receipt version must be integer 1")
    expected_bindings = {
        "packet_sha256": packet.get("packet_sha256"),
        "orchestration_envelope_sha256": envelope_sha256,
        "execution_receipt_sha256": execution_receipt_sha256,
        "controller_ref": execution_receipt.get("controller_ref"),
        "verifier_bundle_sha256": verifier_bundle_sha256,
        "repository_ref": repository_ref,
    }
    for field, expected in expected_bindings.items():
        if value.get(field) != expected:
            errors.append(f"verification receipt {field} is invalid")
    verifier_id = value.get("verifier_id")
    actor_ids = {
        actor.get("id")
        for actor in execution_receipt.get("actors", [])
        if isinstance(actor, dict)
    }
    if not runtime_identity(verifier_id) or verifier_id in actor_ids:
        errors.append("verification identity must be valid and disjoint from every execution actor")
    if value.get("fresh_context") is not True:
        errors.append("verification receipt must attest fresh context")
    if value.get("write_access") is not False:
        errors.append("independent verifier must have no write access")
    if value.get("candidate_outputs_visible") is not False:
        errors.append("independent verifier must not see candidate outputs")
    if value.get("verdict") != "passed":
        errors.append("verification verdict must be passed")
    if value.get("policy_violations") != []:
        errors.append("verification receipt cannot pass with policy violations")
    canonical = execution_receipt.get("canonical_pre_acceptance")
    if isinstance(canonical, dict):
        if value.get("final_state_sha256") != canonical.get("final_state_sha256"):
            errors.append("verification receipt final state differs from execution receipt")
        if value.get("diff_sha256") != canonical.get("diff_sha256"):
            errors.append("verification receipt diff differs from execution receipt")
    checks = value.get("checks")
    acceptance = packet["contract"]["acceptance"]
    if not isinstance(checks, list) or len(checks) != len(acceptance):
        errors.append("verification receipt checks must exactly cover frozen acceptance")
        checks = []
    for index, (check, criterion) in enumerate(zip(checks, acceptance)):
        if not isinstance(check, dict) or set(check) != VERIFICATION_CHECK_KEYS:
            errors.append(f"verification check {index} has an invalid field set")
            continue
        expected = {
            "criterion_id": criterion["id"],
            "command": criterion["command"],
            "exit_code": 0,
        }
        if not _strict_json_equal(check, expected):
            errors.append(f"verification check {index} differs from frozen acceptance or failed")
    return errors


def validate_packet_lineage(
    packet: dict[str, Any], prior_packet: Any | None, expected_prior_sha256: str | None
) -> list[str]:
    contract = packet["contract"]
    supersedes = contract.get("supersedes")
    if supersedes is None:
        if prior_packet is not None or expected_prior_sha256 is not None:
            return ["initial packet must not receive a supersedes artifact"]
        return []
    if not isinstance(prior_packet, dict) or not _sha(expected_prior_sha256):
        return ["revised v5 packet requires an externally anchored prior packet"]
    prior_digest = packet_sha256(prior_packet)
    errors: list[str] = []
    if (
        prior_digest != expected_prior_sha256
        or prior_packet.get("packet_sha256") != prior_digest
        or supersedes.get("packet_sha256") != prior_digest
    ):
        errors.append("prior packet digest does not match the frozen lineage")
    version = prior_packet.get("version")
    if version == 4:
        errors.extend(validate_packet_v4(prior_packet, expected_prior_sha256))
    elif version == 5:
        errors.extend(validate_packet_v5(prior_packet, expected_prior_sha256))
    else:
        errors.append("prior packet must be v4 or v5")
    try:
        prior_contract = freeze_contract(prior_packet.get("contract"))
    except (TypeError, ValueError) as exc:
        errors.append(f"prior packet contract is invalid: {exc}")
        return errors
    if prior_packet.get("contract_sha256") != contract_sha256(prior_contract):
        errors.append("prior packet contract digest is invalid")
    if contract.get("contract_id") != prior_contract.get("contract_id"):
        errors.append("packet lineage changed contract_id")
    if contract.get("revision") != prior_contract.get("revision", -2) + 1:
        errors.append("packet contract revision is not prior revision plus one")
    return errors


def verifier_bundle_paths() -> tuple[Path, ...]:
    skill_root = Path(__file__).resolve(strict=True).parent.parent
    return (
        skill_root / "scripts" / "check_delivery_v5.py",
        skill_root / "scripts" / "diverge_v5.py",
        skill_root / "scripts" / "check_delivery.py",
        skill_root / "scripts" / "diverge.py",
        skill_root / "references" / "lenses.json",
        skill_root / "references" / "protocol-v5.md",
    )


def verifier_bundle_sha256() -> str:
    skill_root = Path(__file__).resolve(strict=True).parent.parent
    payload = {
        path.relative_to(skill_root).as_posix(): _file_sha256(path)
        for path in verifier_bundle_paths()
    }
    return hashlib.sha256(canonical_json_bytes(payload)).hexdigest()


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--repo-root", type=Path, required=True)
    parser.add_argument("--baseline-manifest", type=Path, required=True)
    parser.add_argument("--capture-baseline", action="store_true")
    parser.add_argument("--packet", type=Path)
    parser.add_argument("--report", type=Path)
    parser.add_argument("--host-capabilities", type=Path)
    parser.add_argument("--coordination-plan", type=Path)
    parser.add_argument("--resource-envelope", type=Path)
    parser.add_argument("--sandbox-profile", type=Path)
    parser.add_argument("--orchestration-envelope", type=Path)
    parser.add_argument("--execution-receipt", type=Path)
    parser.add_argument("--verification-receipt", type=Path)
    parser.add_argument("--expect-packet-sha256")
    parser.add_argument("--expect-verifier-sha256")
    parser.add_argument("--expect-orchestration-envelope-sha256")
    parser.add_argument("--expect-execution-receipt-sha256")
    parser.add_argument("--expect-verification-receipt-sha256")
    parser.add_argument("--supersedes-packet", type=Path)
    parser.add_argument("--expect-supersedes-sha256")
    parser.add_argument("--prior-coordination-plan", type=Path)
    parser.add_argument("--expect-prior-coordination-plan-sha256")
    parser.add_argument("--previous-orchestration-envelope", type=Path)
    parser.add_argument("--expect-previous-orchestration-envelope-sha256")
    return parser.parse_args(argv)


def _print_result(passed: bool, errors: list[str], **extra: Any) -> int:
    payload: dict[str, Any] = {"passed": passed, "errors": errors}
    payload.update(extra)
    print(json.dumps(payload, ensure_ascii=True, indent=2))
    return 0 if passed else 1


def _required_gate_paths(args: argparse.Namespace) -> dict[str, Path]:
    names = (
        "packet",
        "report",
        "host_capabilities",
        "coordination_plan",
        "resource_envelope",
        "sandbox_profile",
        "orchestration_envelope",
        "execution_receipt",
        "verification_receipt",
    )
    missing = [name for name in names if getattr(args, name) is None]
    if missing:
        raise GateError(f"gate mode is missing required artifacts: {missing}")
    return {name: getattr(args, name) for name in names}


def _check_external_digest(label: str, actual: str, expected: Any) -> None:
    if not _sha(expected) or expected != actual:
        raise GateError(f"trusted {label} digest is missing, malformed, or mismatched")


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    try:
        repository = _canonical_repository_root(args.repo_root)
        baseline_path = Path(os.path.abspath(args.baseline_manifest)).resolve(strict=False)
        if _is_inside(baseline_path, repository):
            raise GateError("baseline manifest must be outside the target repository")
        verifier_paths = [
            _plain_external_file(path, repository, "pinned verifier bundle file")
            for path in verifier_bundle_paths()
        ]
        verifier_digest = verifier_bundle_sha256()

        if args.capture_baseline:
            manifest = build_state_manifest(repository)
            _, repository_object_errors = _repository_file_objects(
                repository, manifest
            )
            if repository_object_errors:
                raise GateError("; ".join(repository_object_errors))
            baseline_path.parent.mkdir(parents=True, exist_ok=True)
            with baseline_path.open("x", encoding="utf-8", newline="\n") as handle:
                handle.write(json.dumps(manifest, ensure_ascii=False, indent=2) + "\n")
            return _print_result(
                True,
                [],
                repository_ref=manifest["repository_ref"],
                state_ref=_repository_identity(baseline_path),
                baseline_manifest_sha256=state_manifest_sha256(manifest),
                verifier_sha256=verifier_digest,
            )

        path_args = _required_gate_paths(args)
        expected_names = (
            "expect_packet_sha256",
            "expect_verifier_sha256",
            "expect_orchestration_envelope_sha256",
            "expect_execution_receipt_sha256",
            "expect_verification_receipt_sha256",
        )
        missing_expected = [name for name in expected_names if getattr(args, name) is None]
        if missing_expected:
            raise GateError(f"gate mode is missing trusted digest anchors: {missing_expected}")
        _check_external_digest("verifier bundle", verifier_digest, args.expect_verifier_sha256)

        artifact_paths: dict[str, Path] = {
            name: _plain_external_file(path, repository, name.replace("_", " "))
            for name, path in path_args.items()
        }
        baseline_path = _plain_external_file(baseline_path, repository, "baseline manifest")
        artifact_paths["baseline_manifest"] = baseline_path
        optional_paths: dict[str, Path] = {}
        for name in (
            "supersedes_packet",
            "prior_coordination_plan",
            "previous_orchestration_envelope",
        ):
            raw = getattr(args, name)
            if raw is not None:
                optional_paths[name] = _plain_external_file(raw, repository, name.replace("_", " "))
        previous_envelope_path = optional_paths.get("previous_orchestration_envelope")
        if previous_envelope_path is not None:
            previous_envelope_preview = load_json(previous_envelope_path)
            if not isinstance(previous_envelope_preview, dict):
                raise GateError("previous orchestration envelope must be an object")
            for name, ref_field in (
                ("previous_host_capabilities", "host_capabilities_ref"),
                ("previous_resource_envelope", "resource_envelope_ref"),
                ("previous_sandbox_profile", "sandbox_profile_ref"),
            ):
                ref = previous_envelope_preview.get(ref_field)
                if not isinstance(ref, str) or not ref:
                    raise GateError(f"previous orchestration envelope {ref_field} is invalid")
                optional_paths[name] = _plain_external_file(
                    Path(ref), repository, name.replace("_", " ")
                )
        protected_roles = [
            *artifact_paths.items(),
            *optional_paths.items(),
            *((f"verifier_{index}", path) for index, path in enumerate(verifier_paths)),
        ]
        allowed_reuse = {
            frozenset(("host_capabilities", "previous_host_capabilities")),
            frozenset(("resource_envelope", "previous_resource_envelope")),
            frozenset(("sandbox_profile", "previous_sandbox_profile")),
        }
        observed_objects: dict[str, tuple[str, Path]] = {}
        all_protected: list[Path] = []
        for role, path in protected_roles:
            object_identity = _file_object_identity(path)
            prior = observed_objects.get(object_identity)
            if prior is None:
                observed_objects[object_identity] = (role, path)
                all_protected.append(path)
                continue
            prior_role, prior_path = prior
            if frozenset((prior_role, role)) not in allowed_reuse or prior_path != path:
                raise GateError(
                    "audit artifacts and verifier files must be distinct, except unchanged "
                    "predecessor capability/resource/sandbox artifacts"
                )
        raw_digests = {path: _file_sha256(path) for path in all_protected}
        raw_objects = {path: _file_object_identity(path) for path in all_protected}

        loaded = {name: load_json(path) for name, path in artifact_paths.items()}
        optional_loaded = {name: load_json(path) for name, path in optional_paths.items()}
        packet = loaded["packet"]
        report = loaded["report"]
        host_capabilities = loaded["host_capabilities"]
        task_graph = loaded["coordination_plan"]
        resource_envelope = loaded["resource_envelope"]
        sandbox_profile = loaded["sandbox_profile"]
        envelope = loaded["orchestration_envelope"]
        execution_receipt = loaded["execution_receipt"]
        verification_receipt = loaded["verification_receipt"]
        baseline_manifest = validate_state_manifest(loaded["baseline_manifest"])

        packet_errors = validate_packet_v5(packet, args.expect_packet_sha256)
        if packet_errors:
            raise GateError("; ".join(packet_errors))
        packet_digest = packet_sha256(packet)
        _check_external_digest("packet", packet_digest, args.expect_packet_sha256)
        resource_errors = validate_resource_envelope(resource_envelope)
        sandbox_errors = validate_sandbox_profile(sandbox_profile)
        capability_errors = validate_host_capabilities(host_capabilities)
        if resource_errors or sandbox_errors or capability_errors:
            raise GateError("; ".join(resource_errors + sandbox_errors + capability_errors))

        prior_plan = optional_loaded.get("prior_coordination_plan")
        if prior_plan is not None:
            _check_external_digest(
                "prior coordination plan",
                _json_digest(prior_plan),
                args.expect_prior_coordination_plan_sha256,
            )
        elif args.expect_prior_coordination_plan_sha256 is not None:
            raise GateError("prior coordination plan digest was supplied without its artifact")
        plan_errors = validate_coordination_plan(packet, host_capabilities, task_graph, prior_plan)
        if plan_errors:
            raise GateError("; ".join(plan_errors))

        prior_packet = optional_loaded.get("supersedes_packet")
        lineage_errors = validate_packet_lineage(packet, prior_packet, args.expect_supersedes_sha256)
        if lineage_errors:
            raise GateError("; ".join(lineage_errors))
        previous_envelope = optional_loaded.get("previous_orchestration_envelope")
        if previous_envelope is not None:
            _check_external_digest(
                "previous orchestration envelope",
                _json_digest(previous_envelope),
                args.expect_previous_orchestration_envelope_sha256,
            )
        elif args.expect_previous_orchestration_envelope_sha256 is not None:
            raise GateError("previous envelope digest was supplied without its artifact")

        envelope_digest = _json_digest(envelope)
        _check_external_digest(
            "orchestration envelope", envelope_digest, args.expect_orchestration_envelope_sha256
        )
        envelope_errors = validate_orchestration_envelope(
            envelope,
            packet=packet,
            host_capabilities=host_capabilities,
            host_capabilities_path=artifact_paths["host_capabilities"],
            task_graph=task_graph,
            task_graph_path=artifact_paths["coordination_plan"],
            resource_envelope=resource_envelope,
            resource_envelope_path=artifact_paths["resource_envelope"],
            sandbox_profile=sandbox_profile,
            sandbox_profile_path=artifact_paths["sandbox_profile"],
            previous_envelope=previous_envelope,
            prior_task_graph=prior_plan,
            prior_task_graph_path=optional_paths.get("prior_coordination_plan"),
            repository=repository,
        )
        if envelope_errors:
            raise GateError("; ".join(envelope_errors))
        controller_ref = envelope.get("controller_ref")
        if execution_receipt.get("controller_ref") != controller_ref or verification_receipt.get("controller_ref") != controller_ref:
            raise GateError("controller identity differs across anchored receipts")

        frozen_contract = freeze_contract(packet["contract"])
        baseline_digest = state_manifest_sha256(baseline_manifest)
        if frozen_contract["baseline"]["state_sha256"] != baseline_digest:
            raise GateError("baseline manifest differs from the frozen contract")
        if frozen_contract["baseline"]["repository_ref"] != baseline_manifest["repository_ref"]:
            raise GateError("baseline repository identity differs from the frozen contract")
        if frozen_contract["baseline"]["state_ref"] != _repository_identity(baseline_path):
            raise GateError("baseline state_ref differs from the external baseline path")
        if baseline_manifest["repository_ref"] != _repository_identity(repository):
            raise GateError("target repository differs from the captured baseline identity")
        scope_errors = validate_scope_paths_against_repo(frozen_contract, repository)
        if scope_errors:
            raise GateError("; ".join(scope_errors))
        pre_acceptance_manifest = build_state_manifest(repository)
        if pre_acceptance_manifest["root_metadata"]["file_id"] != baseline_manifest["root_metadata"]["file_id"]:
            raise GateError("target repository root object differs from the baseline")

        execution_digest = _json_digest(execution_receipt)
        _check_external_digest(
            "execution receipt", execution_digest, args.expect_execution_receipt_sha256
        )
        execution_errors, candidate_bundles = validate_execution_receipt(
            execution_receipt,
            packet=packet,
            host_capabilities=host_capabilities,
            task_graph=task_graph,
            resource_envelope=resource_envelope,
            envelope_sha256=envelope_digest,
            repository=repository,
            baseline_manifest=baseline_manifest,
            pre_acceptance_manifest=pre_acceptance_manifest,
            protected_paths=all_protected,
            report=report,
        )
        if execution_errors:
            raise GateError("; ".join(execution_errors))
        for bundle in candidate_bundles:
            raw_digests[bundle] = _file_sha256(bundle)
            raw_objects[bundle] = _file_object_identity(bundle)

        verification_digest = _json_digest(verification_receipt)
        _check_external_digest(
            "verification receipt", verification_digest, args.expect_verification_receipt_sha256
        )
        verification_errors = validate_verification_receipt(
            verification_receipt,
            packet=packet,
            execution_receipt=execution_receipt,
            envelope_sha256=envelope_digest,
            execution_receipt_sha256=execution_digest,
            verifier_bundle_sha256=verifier_digest,
            repository_ref=pre_acceptance_manifest["repository_ref"],
        )
        if verification_errors:
            raise GateError("; ".join(verification_errors))
        report_errors = validate_v5_report(
            report,
            packet=packet,
            envelope_sha256=envelope_digest,
            execution_receipt_sha256=execution_digest,
            verification_receipt_sha256=verification_digest,
            integrations=execution_receipt["integrations"],
        )
        if report_errors:
            raise GateError("; ".join(report_errors))
        if packet.get("coordination") == "shared":
            report_ids = {
                item.get("id")
                for item in report.get("deliberation", {}).get("delegation", {}).get("participants", [])
                if isinstance(item, dict)
            }
            receipt_ids = {
                item.get("actor_id")
                for item in execution_receipt.get("leases", [])
                if isinstance(item, dict)
            }
            if report_ids != receipt_ids:
                raise GateError("report participants differ from controller-observed actors")
            report_participants = {
                item.get("id"): item
                for item in report.get("deliberation", {})
                .get("delegation", {})
                .get("participants", [])
                if isinstance(item, dict)
            }
            task_by_id_for_lanes = {
                item.get("id"): item
                for item in task_graph.get("tasks", [])
                if isinstance(item, dict)
            }
            for actor in execution_receipt.get("actors", []):
                if (
                    not isinstance(actor, dict)
                    or actor.get("kind") == "main-integrator"
                    or actor.get("id") not in receipt_ids
                ):
                    continue
                observed_lanes = {
                    lane_id
                    for task_id in actor.get("task_ids", [])
                    for lane_id in task_by_id_for_lanes.get(task_id, {})
                    .get("output_contract", {})
                    .get("lane_ids", [])
                }
                participant = report_participants.get(actor.get("id"), {})
                if set(participant.get("lane_ids", [])) != observed_lanes:
                    raise GateError(
                        f"report participant {actor.get('id')!r} lanes differ from controller-observed tasks"
                    )

        # Validate the complete legacy report semantics before any acceptance
        # command can execute.  The independent verification receipt already
        # binds the exact frozen commands and zero exit codes; synthetic stream
        # digests are sufficient for the v4 structural oracle at this stage.
        observed_changed_paths = state_manifest_changed_paths(
            baseline_manifest, pre_acceptance_manifest
        )
        packet_v4, report_v4 = project_v5_to_v4(packet, report)
        preflight_checks = [
            {
                "command": check["command"],
                "exit_code": check["exit_code"],
                "stdout_sha256": "0" * 64,
                "stderr_sha256": "0" * 64,
            }
            for check in verification_receipt["checks"]
        ]
        preflight_v4 = evaluate_v4(
            packet_v4,
            report_v4,
            packet_v4["packet_sha256"],
            observed_changed_paths=observed_changed_paths,
            observed_check_results=preflight_checks,
        )
        if not preflight_v4.get("passed"):
            raise GateError(
                "frozen v4 report preflight rejected the v5 projection: "
                + "; ".join(preflight_v4.get("errors", []))
            )

        if any(
            _file_sha256(path) != digest
            or _file_object_identity(path) != raw_objects[path]
            for path, digest in raw_digests.items()
        ):
            raise GateError("an anchored artifact changed before acceptance commands")
        immediate_pre_command_manifest = build_state_manifest(repository)
        if not _strict_json_equal(immediate_pre_command_manifest, pre_acceptance_manifest):
            raise GateError("canonical repository changed after receipt validation and before acceptance")
        commands = [item["command"] for item in frozen_contract["acceptance"]]
        observed_checks = run_frozen_checks(commands, repository)
        if any(
            _file_sha256(path) != digest
            or _file_object_identity(path) != raw_objects[path]
            for path, digest in raw_digests.items()
        ):
            raise GateError("an anchored artifact changed during acceptance commands")
        post_acceptance_manifest = build_state_manifest(repository)
        if canonical_json_bytes(post_acceptance_manifest) != canonical_json_bytes(pre_acceptance_manifest):
            raise GateError("acceptance commands changed the frozen delivery state")
        receipt_checks = verification_receipt["checks"]
        for observed, recorded in zip(observed_checks, receipt_checks):
            if observed.get("command") != recorded.get("command") or observed.get("exit_code") != recorded.get("exit_code"):
                raise GateError("fresh acceptance observation differs from the verification receipt")

        v4_result = evaluate_v4(
            packet_v4,
            report_v4,
            packet_v4["packet_sha256"],
            observed_changed_paths=observed_changed_paths,
            observed_check_results=observed_checks,
        )
        if not v4_result.get("passed"):
            raise GateError("frozen v4 delivery oracle rejected the v5 projection: " + "; ".join(v4_result.get("errors", [])))
        final_digest = state_manifest_sha256(pre_acceptance_manifest)
        diff_digest = _diff_digest(
            pre_acceptance_manifest["repository_ref"],
            baseline_digest,
            final_digest,
            observed_changed_paths,
        )
        observations = {
            "repository_ref": pre_acceptance_manifest["repository_ref"],
            "baseline_state_sha256": baseline_digest,
            "final_state_sha256": final_digest,
            "diff_sha256": diff_digest,
            "changed_paths": observed_changed_paths,
            "checks": observed_checks,
            "packet_sha256": packet_digest,
            "verifier_sha256": verifier_digest,
            "orchestration_envelope_sha256": envelope_digest,
            "execution_receipt_sha256": execution_digest,
            "verification_receipt_sha256": verification_digest,
        }
        return _print_result(True, [], summary=v4_result.get("summary", {}), observations=observations)
    except FileExistsError:
        return _print_result(False, ["baseline manifest already exists; capture refuses overwrite"])
    except (GateError, KeyError, OSError, RecursionError, TypeError, UnicodeError, ValueError) as exc:
        return _print_result(False, [str(exc)])


if __name__ == "__main__":
    raise SystemExit(main())
