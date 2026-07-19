#!/usr/bin/env python3
"""Run the frozen 150-task, fresh-process v5 protocol benchmark.

The benchmark exercises complete command-line gates.  It measures only this
frozen protocol/controller task distribution, never general model accuracy.
"""

from __future__ import annotations

import argparse
import copy
import hashlib
import json
import math
import os
import secrets
import shutil
import subprocess
import sys
import tempfile
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path
from typing import Any


TEST_DIR = Path(__file__).resolve().parent
SKILL_DIR = TEST_DIR.parent
sys.path.insert(0, str(SKILL_DIR / "scripts"))
sys.path.insert(0, str(TEST_DIR))

from check_delivery import build_state_manifest, state_manifest_sha256  # noqa: E402
from check_delivery_v5 import _diff_digest, _json_digest, verifier_bundle_sha256  # noqa: E402
from diverge_v5 import build_task_prompt_v5, normalize_host_capabilities  # noqa: E402
from run_v5_eval import (  # noqa: E402
    Fixture,
    make_fixture,
    make_isolated_fixture,
    make_shared_fixture,
)


BENCHMARK_ID = "wide-lens-v5-full-gate-benchmark/v1"
CLAIM_SCOPE = "frozen v5 protocol/controller benchmark; not model or coding-task accuracy"
FROZEN_SUITE_SHA256 = "c3ebeb3b032a69a4b43ce7a042a20ed666183d3994386d51b4a97a535b772404"
RELEASE_TASKS = 150
RELEASE_JOBS = 6
WORKER_TIMEOUT_SECONDS = 45
WORKER_OUTPUT_LIMIT = 1024 * 1024
STRATA = (
    "authority-packet-lineage",
    "capabilities-dag-envelope",
    "resources-sandbox-events",
    "candidate-isolation-conflict",
    "verifier-report-gate",
    "compatibility-path-artifact",
)
WORKER_KEYS = {
    "id",
    "stratum",
    "suite_sha256",
    "challenge",
    "task_success",
    "oracle_passed",
    "controller_observed_diff_correct",
    "no_hard_invariant_violation",
    "within_resource_envelope",
    "acceptance_observed",
    "gate_returncode",
    "detail",
}


def canonical_json(value: Any) -> bytes:
    return json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")


def sha256_json(value: Any) -> str:
    return hashlib.sha256(canonical_json(value)).hexdigest()


def case(
    case_id: str,
    stratum: str,
    fixture: str,
    operation: dict[str, Any],
    *,
    passed: bool,
    errors: list[str] | None = None,
    acceptance: bool | None = None,
) -> dict[str, Any]:
    return {
        "id": case_id,
        "stratum": stratum,
        "fixture": fixture,
        "variant": case_id.rsplit("-", 1)[-1],
        "operation": operation,
        "expected": {
            "passed": passed,
            "error_any": errors or [],
            "acceptance_observed": passed if acceptance is None else acceptance,
            "repository_unchanged_during_gate": True,
        },
    }


def _set(artifact: str, path: list[Any], value: Any) -> dict[str, Any]:
    return {"kind": "set", "artifact": artifact, "path": path, "value": value}


def _delete(artifact: str, path: list[Any]) -> dict[str, Any]:
    return {"kind": "delete", "artifact": artifact, "path": path}


def _add(artifact: str, path: list[Any], key: str, value: Any) -> dict[str, Any]:
    return {"kind": "add", "artifact": artifact, "path": path, "key": key, "value": value}


def _positive_cases(
    stratum: str, fixtures: list[tuple[str, str]]
) -> list[dict[str, Any]]:
    return [
        case(
            f"{stratum}-positive-{index:02d}",
            stratum,
            fixture,
            {"kind": "valid-profile", "name": profile},
            passed=True,
        )
        for index, (fixture, profile) in enumerate(fixtures, start=1)
    ]


def frozen_tasks() -> list[dict[str, Any]]:
    tasks: list[dict[str, Any]] = []

    authority = STRATA[0]
    tasks += _positive_cases(
        authority,
        [
            ("read-only", "authority-default"),
            ("read-only", "authority-controller-ref"),
            ("read-only", "authority-task-objective"),
            ("read-only", "authority-agent-profile"),
            ("read-only", "authority-resource-observation"),
        ],
    )
    authority_ops = [
        (_add("packet", [], "smuggled", True), ["field set", "packet"]),
        (_delete("packet", ["orchestration_policy"]), ["field set", "policy"]),
        (_set("packet", ["version"], True), ["version", "packet"]),
        (_set("packet", ["profile"], "focused"), ["profile", "packet"]),
        (_set("packet", ["coordination"], "team"), ["coordination", "packet"]),
        (_set("packet", ["risk"], "critical"), ["risk", "packet"]),
        (_set("packet", ["lanes"], {}), ["lane", "packet"]),
        (_set("packet", ["lanes"], []), ["lane", "packet"]),
        (_set("packet", ["discussion"], {}), ["discussion", "packet"]),
        (_set("packet", ["prompts"], []), ["prompt", "packet"]),
        (_set("packet", ["contract_sha256"], "0" * 64), ["contract", "packet"]),
        (_set("packet", ["packet_sha256"], "0" * 64), ["packet"]),
        (_set("packet", ["orchestration_policy", "selection_owner"], "controller"), ["policy", "packet"]),
        (_set("packet", ["orchestration_policy", "participant_count_prescribed"], True), ["policy", "packet"]),
        (_set("packet", ["orchestration_policy", "runtime_may_narrow_only"], False), ["policy", "packet"]),
        (_set("packet", ["orchestration_policy", "analysis_worker"], "writer"), ["policy", "packet"]),
        (_set("packet", ["orchestration_policy", "candidate_worker"], "canonical"), ["policy", "packet"]),
        (_set("packet", ["orchestration_policy", "canonical_writer"], "worker"), ["policy", "packet"]),
        (_set("packet", ["orchestration_policy", "recursive_delegation"], True), ["policy", "packet"]),
        (_set("packet", ["orchestration_policy", "verification_owner"], "integrator"), ["policy", "packet"]),
    ]
    tasks += [
        case(
            f"{authority}-negative-{index:02d}",
            authority,
            "read-only",
            operation,
            passed=False,
            errors=errors,
        )
        for index, (operation, errors) in enumerate(authority_ops, start=1)
    ]

    capability = STRATA[1]
    tasks += _positive_cases(
        capability,
        [
            ("main-only", "capability-main-only"),
            ("read-only", "capability-root-assign"),
            ("shared-relay", "capability-shared-root-relay"),
            ("shared-peer", "capability-shared-peer-message"),
            ("isolated", "capability-isolated-candidate"),
        ],
    )
    capability_ops = [
        (_add("capabilities", ["capabilities"], "unknown_capability", False), ["capabil"]),
        (_set("capabilities", ["capabilities", "spawn"], None), ["capabil"]),
        (_set("capabilities", ["capabilities", "independent_verifier"], False), ["independent_verifier"]),
        (_set("capabilities", ["capabilities", "max_depth_control"], False), ["max_depth_control"]),
        (_set("plan", ["execution"], "parallel"), ["execution mode"]),
        (_set("plan", ["dispatch"], "atomic-claim"), ["atomic-claim"]),
        (_set("plan", ["communication"], "peer-message"), ["peer-message"]),
        (_add("plan", ["tasks", 0], "unexpected", True), ["invalid field set"]),
        (_set("plan", ["tasks", 0, "id"], ""), ["invalid", "id"]),
        (_set("plan", ["tasks", 0, "dependencies"], ["missing-task"]), ["unknown depend"]),
        ({"kind": "plan-cycle"}, ["cycle"]),
        (_set("plan", ["tasks", 0, "read_paths"], ["outside/file.py"]), ["analysis scope"]),
        (_set("plan", ["tasks", 0, "candidate_write_paths"], ["outside/file.py"]), ["allowed writes"]),
        (_set("plan", ["tasks", 0, "acceptance_ids"], ["AC-X"]), ["acceptance"]),
        (_set("plan", ["tasks", 0, "output_contract", "kind"], "workspace-write"), ["output_contract kind"]),
        (_set("plan", ["assignments", 0, "task_id"], "missing-task"), ["unknown", "task_id"]),
        (_set("plan", ["assignments", 0, "model"], "model-x"), ["per_spawn_model"]),
        (_set("plan", ["revision"], 1), ["initial coordination plan"]),
        (_set("envelope", ["sealed_before_first_spawn"], False), ["sealed"]),
        (_set("envelope", ["narrowing_attested"], False), ["narrowing"]),
    ]
    tasks += [
        case(
            f"{capability}-negative-{index:02d}",
            capability,
            "read-only",
            operation,
            passed=False,
            errors=errors,
        )
        for index, (operation, errors) in enumerate(capability_ops, start=1)
    ]

    resources = STRATA[2]
    tasks += _positive_cases(
        resources,
        [
            ("resource-boundary", "resource-boundary-tokens"),
            ("resource-boundary", "resource-boundary-tool-calls"),
            ("resource-boundary", "resource-boundary-process-seconds"),
            ("resource-boundary", "resource-boundary-artifact-bytes"),
            ("resource-boundary", "resource-boundary-concurrency"),
        ],
    )
    resource_ops: list[tuple[dict[str, Any], list[str]]] = []
    for name in (
        "max_tokens",
        "max_tool_calls",
        "max_process_seconds",
        "max_artifact_bytes",
        "max_concurrency",
    ):
        resource_ops.append((_set("resources", ["limits", name], 0), ["resource envelope"]))
    resource_ops += [
        (_set("sandbox", ["isolation"], "process-like"), ["sandbox"]),
        (_set("sandbox", ["candidate_workspace_write"], False), ["sandbox"]),
        (_set("sandbox", ["candidate_network_access"], True), ["sandbox"]),
        (_set("sandbox", ["candidate_credential_access"], True), ["sandbox"]),
        (_set("sandbox", ["candidate_target_repository_mounted"], True), ["sandbox"]),
        (_set("sandbox", ["candidate_git_common_dir_mounted"], True), ["sandbox"]),
        (_set("sandbox", ["candidate_artifact_store_mounted"], True), ["sandbox"]),
        (_set("sandbox", ["verifier_write_access"], True), ["sandbox"]),
        (_set("sandbox", ["verifier_candidate_outputs_visible"], True), ["sandbox"]),
        (_set("sandbox", ["orphan_detection"], False), ["sandbox"]),
        (_set("execution", ["complete_event_capture"], False), ["event capture"]),
        (_set("execution", ["orphan_processes_detected"], True), ["orphan"]),
        (_set("execution", ["policy_violations"], ["violation"]), ["policy violation"]),
        ({"kind": "drop-terminal-event"}, ["terminal event", "event"]),
        ({"kind": "duplicate-event-sequence"}, ["sequence"]),
    ]
    tasks += [
        case(
            f"{resources}-negative-{index:02d}",
            resources,
            "read-only",
            operation,
            passed=False,
            errors=errors,
        )
        for index, (operation, errors) in enumerate(resource_ops, start=1)
    ]

    candidate = STRATA[3]
    tasks += _positive_cases(
        candidate,
        [
            ("isolated", "candidate-selected"),
            ("isolated", "candidate-rejected"),
            ("isolated", "candidate-failed"),
            ("isolated", "candidate-binary-bundle"),
            ("isolated", "candidate-local-check-proof"),
        ],
    )
    candidate_ops = [
        ({"kind": "tamper-candidate-bundle"}, ["bundle digest", "candidate"]),
        ({"kind": "delete-candidate-bundle"}, ["candidate bundle", "does not exist"]),
        (_set("execution", ["candidates", 0, "bundle_sha256"], "0" * 64), ["bundle"]),
        (_set("execution", ["candidates", 0, "base_state_sha256"], "0" * 64), ["base"]),
        ({"kind": "candidate-bundle-inside-repo"}, ["bundle", "target repository"]),
        ({"kind": "candidate-workspace-is-repo"}, ["workspace", "protected"]),
        (_set("execution", ["candidates", 0, "workspace_isolated"], False), ["isolat"]),
        (_set("execution", ["candidates", 0, "canonical_write_blocked"], False), ["workspace isolation"]),
        (_set("execution", ["candidates", 0, "target_repository_write_detected"], True), ["forbidden access"]),
        (_set("execution", ["candidates", 0, "artifact_store_write_detected"], True), ["forbidden access"]),
        (_set("execution", ["candidates", 0, "verifier_access_detected"], True), ["forbidden access"]),
        (_set("execution", ["candidates", 0, "network_access"], True), ["forbidden access"]),
        (_set("execution", ["candidates", 0, "credential_access"], True), ["forbidden access"]),
        (_set("execution", ["candidates", 0, "shared_git_access"], True), ["forbidden access"]),
        (_set("execution", ["candidates", 0, "changed_paths"], ["outside/file.py"]), ["write scope", "changed_paths"]),
        (_set("execution", ["integrations", 0, "integrator_id"], "worker-1"), ["integrator"]),
        (_set("execution", ["canonical_pre_acceptance", "non_integrator_writes_detected"], True), ["canonical_pre_acceptance"]),
        (_set("execution", ["canonical_pre_acceptance", "final_state_sha256"], "0" * 64), ["canonical_pre_acceptance"]),
        (_set("execution", ["canonical_pre_acceptance", "diff_sha256"], "0" * 64), ["canonical_pre_acceptance"]),
        (_set("execution", ["resource_usage", "artifact_bytes"], 0), ["artifact byte"]),
    ]
    tasks += [
        case(
            f"{candidate}-negative-{index:02d}",
            candidate,
            "isolated",
            operation,
            passed=False,
            errors=errors,
        )
        for index, (operation, errors) in enumerate(candidate_ops, start=1)
    ]

    verifier = STRATA[4]
    tasks += _positive_cases(
        verifier,
        [
            ("read-only", "verifier-read-only"),
            ("main-only", "verifier-main-only"),
            ("shared-relay", "verifier-shared-relay"),
            ("shared-peer", "verifier-shared-peer"),
            ("isolated", "verifier-isolated"),
        ],
    )
    verifier_ops = [
        (_set("verification", ["verifier_id"], "main-thread"), ["identity", "disjoint"]),
        (_set("verification", ["fresh_context"], False), ["fresh context"]),
        (_set("verification", ["write_access"], True), ["write access"]),
        (_set("verification", ["candidate_outputs_visible"], True), ["candidate outputs"]),
        (_set("verification", ["verdict"], "failed"), ["verdict"]),
        (_set("verification", ["policy_violations"], ["violation"]), ["policy violation"]),
        (_set("verification", ["checks"], []), ["checks"]),
        (_set("verification", ["checks", 0, "command"], "python -c \"print('extra')\""), ["frozen acceptance", "check"]),
        (_set("verification", ["checks", 0, "exit_code"], 1), ["failed", "check"]),
        (_set("verification", ["verifier_bundle_sha256"], "0" * 64), ["verifier_bundle"]),
        (_set("report", ["packet_sha256"], "0" * 64), ["report", "packet"]),
        (_set("report", ["orchestration", "envelope_sha256"], "0" * 64), ["report orchestration"]),
        (_set("report", ["orchestration", "execution_receipt_sha256"], "0" * 64), ["report orchestration"]),
        (_set("report", ["orchestration", "verification_receipt_sha256"], "0" * 64), ["report orchestration"]),
        (_set("report", ["orchestration", "candidates"], {}), ["candidate dispositions"]),
        (_add("report", [], "acceptance", []), ["report", "field set"]),
        (_set("report", ["implementation", "changed_paths"], ["outside/file.py"]), ["changed path", "v4"]),
        (_set("report", ["implementation", "acceptance_results"], []), ["acceptance"]),
        ({"kind": "append-report-check"}, ["check", "frozen"]),
        (_set("report", ["implementation", "minimalism", "selected_rung"], "magic"), ["minimalism", "v4"]),
    ]
    tasks += [
        case(
            f"{verifier}-negative-{index:02d}",
            verifier,
            "read-only",
            operation,
            passed=False,
            errors=errors,
        )
        for index, (operation, errors) in enumerate(verifier_ops, start=1)
    ]

    compatibility = STRATA[5]
    path_fixtures = ["path-ascii", "path-space", "path-unicode", "path-mixed", "path-long"]
    tasks += _positive_cases(
        compatibility,
        [(fixture, f"compatibility-{fixture}") for fixture in path_fixtures],
    )
    compatibility_ops = [
        ({"kind": "raw-json", "artifact": "packet", "payload": "duplicate"}, ["duplicate json key"]),
        ({"kind": "raw-json", "artifact": "packet", "payload": "nan"}, ["non-finite"]),
        ({"kind": "raw-json", "artifact": "packet", "payload": "invalid-utf8"}, ["utf-8"]),
        ({"kind": "raw-json", "artifact": "packet", "payload": "deep-160"}, ["nesting exceeds"]),
        ({"kind": "missing-artifact", "artifact": "report"}, ["does not exist", "report"]),
        ({"kind": "artifact-collision"}, ["distinct"]),
        ({"kind": "artifact-inside-repo"}, ["outside", "target repository"]),
        ({"kind": "wrong-anchor", "anchor": "packet"}, ["packet digest"]),
        ({"kind": "wrong-anchor", "anchor": "verifier"}, ["verifier bundle digest"]),
        ({"kind": "wrong-anchor", "anchor": "envelope"}, ["orchestration envelope digest"]),
        ({"kind": "wrong-anchor", "anchor": "execution"}, ["execution receipt digest"]),
        ({"kind": "wrong-anchor", "anchor": "verification"}, ["verification receipt digest"]),
        ({"kind": "unknown-v5-argument"}, ["unrecognized arguments"]),
        ({"kind": "v4-rejects-v5-argument"}, ["unrecognized arguments"]),
        (_set("plan", ["tasks", 0, "read_paths"], ["../outside.py"]), ["canonical"]),
        (_set("plan", ["tasks", 0, "read_paths"], ["src/example.py:stream"]), ["canonical", "analysis scope"]),
        (_set("plan", ["tasks", 0, "read_paths"], ["/absolute/file.py"]), ["canonical"]),
        (_set("plan", ["tasks", 0, "read_paths"], ["src/example.py", "src/example.py"]), ["unique canonical"]),
        (_set("envelope", ["host_capabilities_ref"], "wrong-capabilities.json"), ["ref"]),
        ({"kind": "orphan-previous-envelope-anchor"}, ["previous envelope digest"]),
    ]
    tasks += [
        case(
            f"{compatibility}-negative-{index:02d}",
            compatibility,
            "read-only",
            operation,
            passed=False,
            errors=errors,
        )
        for index, (operation, errors) in enumerate(compatibility_ops, start=1)
    ]

    if len(tasks) != RELEASE_TASKS:
        raise AssertionError(f"expected {RELEASE_TASKS} tasks, got {len(tasks)}")
    counts = {stratum: 0 for stratum in STRATA}
    for task in tasks:
        counts[task["stratum"]] += 1
    if any(value != 25 for value in counts.values()):
        raise AssertionError(f"each stratum must contain 25 tasks: {counts}")
    if len({task["id"] for task in tasks}) != len(tasks):
        raise AssertionError("task IDs must be unique")
    semantic = []
    for task in tasks:
        stripped = {
            key: value
            for key, value in task.items()
            if key not in {"id", "stratum", "variant"}
        }
        semantic.append(sha256_json(stripped))
    if len(set(semantic)) != len(tasks):
        raise AssertionError("semantic task fingerprints must be unique without task IDs")
    return tasks


def suite_digest(tasks: list[dict[str, Any]]) -> str:
    return sha256_json(tasks)


def _container(root: Any, path: list[Any]) -> tuple[Any, Any]:
    cursor = root
    for key in path[:-1]:
        cursor = cursor[key]
    return cursor, path[-1]


def deep_set(root: Any, path: list[Any], value: Any) -> None:
    container, key = _container(root, path)
    container[key] = copy.deepcopy(value)


def deep_delete(root: Any, path: list[Any]) -> None:
    container, key = _container(root, path)
    del container[key]


def rebind_fixture(fixture: Fixture) -> None:
    fixture.envelope["packet_sha256"] = fixture.packet["packet_sha256"]
    fixture.envelope["host_capabilities_sha256"] = _json_digest(fixture.capabilities)
    fixture.envelope["task_graph_sha256"] = _json_digest(fixture.plan)
    fixture.envelope["resource_envelope_sha256"] = _json_digest(fixture.resources)
    fixture.envelope["sandbox_profile_sha256"] = _json_digest(fixture.sandbox)
    envelope_digest = _json_digest(fixture.envelope)
    fixture.execution["packet_sha256"] = fixture.packet["packet_sha256"]
    fixture.execution["controller_ref"] = fixture.envelope["controller_ref"]
    fixture.execution["orchestration_envelope_sha256"] = envelope_digest
    fixture.execution["task_graph_sha256"] = _json_digest(fixture.plan)
    task_by_id = {
        task["id"]: task for task in fixture.plan.get("tasks", []) if isinstance(task, dict)
    }
    for event in fixture.execution.get("events", []):
        if event.get("event") in {"envelope-sealed", "actor-spawned"}:
            event["artifact_sha256"] = envelope_digest
    for lease in fixture.execution.get("leases", []):
        task = task_by_id.get(lease.get("task_id"))
        actor_id = lease.get("actor_id")
        if not isinstance(task, dict) or not isinstance(actor_id, str):
            continue
        prompt, _, _ = build_task_prompt_v5(
            fixture.packet,
            fixture.plan,
            task,
            actor_id,
            envelope_digest,
        )
        prompt_digest = hashlib.sha256(prompt.encode("utf-8")).hexdigest()
        lease["task_prompt_sha256"] = prompt_digest
        for event in fixture.execution.get("events", []):
            if event.get("event") == "lease-granted" and event.get("lease_id") == lease.get("id"):
                event["artifact_sha256"] = prompt_digest
    execution_digest = _json_digest(fixture.execution)
    fixture.verification["packet_sha256"] = fixture.packet["packet_sha256"]
    fixture.verification["controller_ref"] = fixture.envelope["controller_ref"]
    fixture.verification["orchestration_envelope_sha256"] = envelope_digest
    fixture.verification["execution_receipt_sha256"] = execution_digest
    fixture.verification["verifier_bundle_sha256"] = verifier_bundle_sha256()
    fixture.report["packet_sha256"] = fixture.packet["packet_sha256"]
    fixture.report["orchestration"]["envelope_sha256"] = envelope_digest
    fixture.report["orchestration"]["execution_receipt_sha256"] = execution_digest
    fixture.report["orchestration"]["verification_receipt_sha256"] = _json_digest(
        fixture.verification
    )
    fixture.write_all()


def make_main_only(fixture: Fixture) -> None:
    fixture.capabilities = normalize_host_capabilities(
        {"independent_verifier": True, "max_depth_control": True}
    )
    fixture.plan["execution"] = "main-only"
    fixture.plan["tasks"] = []
    fixture.plan["assignments"] = []
    fixture.execution["actors"] = [fixture.execution["actors"][0]]
    fixture.execution["leases"] = []
    fixture.execution["candidates"] = []
    fixture.execution["integrations"] = []
    fixture.execution["events"] = [
        {
            "sequence": 1,
            "event": "envelope-sealed",
            "task_id": None,
            "actor_id": "main-thread",
            "lease_id": None,
            "candidate_id": None,
            "artifact_sha256": None,
        }
    ]
    fixture.execution["resource_usage"] = {
        "tokens": 0,
        "tool_calls": 0,
        "process_seconds": 0,
        "artifact_bytes": 0,
        "peak_concurrency": 0,
    }
    rebind_fixture(fixture)


def make_benchmark_fixture(root: Path, task: dict[str, Any]) -> Fixture:
    variant_number = int(hashlib.sha256(task["id"].encode()).hexdigest()[:4], 16)
    options = {
        "task": f"Frozen {task['stratum']} benchmark {task['variant']}",
        "contract_id": "bench-" + hashlib.sha256(task["id"].encode()).hexdigest()[:16],
        "source_text": f"BASELINE = {variant_number}\n",
    }
    fixture_kind = task["fixture"]
    if fixture_kind in {"isolated"}:
        fixture = make_isolated_fixture(root, **options)
    elif fixture_kind == "shared-relay":
        fixture = make_shared_fixture(root, peer_message=False, **options)
    elif fixture_kind == "shared-peer":
        fixture = make_shared_fixture(root, peer_message=True, **options)
    else:
        fixture = make_fixture(root, **options)
    if fixture_kind == "main-only":
        make_main_only(fixture)
    elif fixture_kind == "resource-boundary":
        usage_names = ["tokens", "tool_calls", "process_seconds", "artifact_bytes", "peak_concurrency"]
        limit_names = ["max_tokens", "max_tool_calls", "max_process_seconds", "max_artifact_bytes", "max_concurrency"]
        profile_order = [
            "resource-boundary-tokens",
            "resource-boundary-tool-calls",
            "resource-boundary-process-seconds",
            "resource-boundary-artifact-bytes",
            "resource-boundary-concurrency",
        ]
        index = profile_order.index(task["operation"]["name"])
        fixture.resources["limits"][limit_names[index]] = fixture.execution["resource_usage"][usage_names[index]] or 1
        rebind_fixture(fixture)
    return fixture


def apply_operation(fixture: Fixture, operation: dict[str, Any]) -> None:
    kind = operation["kind"]
    if kind == "valid-profile":
        profile = operation["name"]
        if profile == "authority-controller-ref":
            fixture.envelope["controller_ref"] = "controller://v5-eval/alternate"
        elif profile == "authority-task-objective":
            fixture.plan["tasks"][0]["objective"] = (
                "Check the same frozen authority through an alternate scoped objective."
            )
        elif profile == "authority-agent-profile":
            fixture.plan["assignments"][0]["agent_profile"] = "wide-lens-peer"
        elif profile == "authority-resource-observation":
            fixture.execution["resource_usage"]["tokens"] += 1
        elif profile == "candidate-rejected":
            fixture.execution["integrations"][0].update(
                {
                    "disposition": "rejected",
                    "reason": "Controller rejected the inert candidate after inspection.",
                }
            )
            fixture.execution["events"] = [
                event
                for event in fixture.execution["events"]
                if event["event"] != "integration-completed"
            ]
            next(
                event
                for event in fixture.execution["events"]
                if event["event"] == "candidate-selected"
            )["event"] = "candidate-rejected"
            fixture.report["orchestration"]["candidates"][0].update(
                {
                    "disposition": "rejected",
                    "reason": "Controller rejected the inert candidate after inspection.",
                }
            )
        elif profile == "candidate-failed":
            fixture.execution["integrations"][0].update(
                {
                    "disposition": "failed",
                    "reason": "Controller recorded the candidate as unusable.",
                }
            )
            fixture.execution["events"] = [
                event
                for event in fixture.execution["events"]
                if event["event"] != "integration-completed"
            ]
            next(
                event
                for event in fixture.execution["events"]
                if event["event"] == "candidate-selected"
            )["event"] = "candidate-failed"
            fixture.report["orchestration"]["candidates"][0].update(
                {
                    "disposition": "failed",
                    "reason": "Controller recorded the candidate as unusable.",
                }
            )
        elif profile == "candidate-binary-bundle":
            bundle = fixture.paths["candidate_bundle"]
            bundle.write_bytes(b"\x00wide-lens-candidate\xff")
            digest = hashlib.sha256(bundle.read_bytes()).hexdigest()
            fixture.execution["candidates"][0]["bundle_sha256"] = digest
            fixture.execution["integrations"][0]["bundle_sha256"] = digest
            fixture.report["orchestration"]["candidates"][0]["bundle_sha256"] = digest
            for event in fixture.execution["events"]:
                if event["event"] in {
                    "candidate-produced",
                    "candidate-selected",
                    "integration-completed",
                }:
                    event["artifact_sha256"] = digest
            fixture.execution["resource_usage"]["artifact_bytes"] = bundle.stat().st_size
        elif profile == "candidate-local-check-proof":
            fixture.execution["candidates"][0]["local_checks_sha256"] = "2" * 64
        elif profile.startswith("verifier-"):
            fixture.verification["verifier_id"] = f"independent-{profile}"
        elif profile in {
            "authority-default",
            "capability-main-only",
            "capability-root-assign",
            "capability-shared-root-relay",
            "capability-shared-peer-message",
            "capability-isolated-candidate",
            "candidate-selected",
            "resource-boundary-tokens",
            "resource-boundary-tool-calls",
            "resource-boundary-process-seconds",
            "resource-boundary-artifact-bytes",
            "resource-boundary-concurrency",
        } or profile.startswith("compatibility-"):
            pass
        else:
            raise AssertionError(f"unsupported valid benchmark profile: {profile}")
        rebind_fixture(fixture)
        return
    if kind in {"set", "delete", "add"}:
        artifact = getattr(fixture, operation["artifact"])
        if kind == "set":
            deep_set(artifact, operation["path"], operation["value"])
        elif kind == "delete":
            deep_delete(artifact, operation["path"])
        else:
            cursor = artifact
            for key in operation["path"]:
                cursor = cursor[key]
            cursor[operation["key"]] = copy.deepcopy(operation["value"])
        fixture.write_all()
        return
    if kind == "plan-cycle":
        second = copy.deepcopy(fixture.plan["tasks"][0])
        second["id"] = "task-2"
        second["dependencies"] = ["task-1"]
        fixture.plan["tasks"][0]["dependencies"] = ["task-2"]
        fixture.plan["tasks"].append(second)
        fixture.plan["assignments"].append(
            {
                "task_id": "task-2",
                "runtime_identity": "worker-1",
                "agent_profile": None,
                "model": None,
                "reasoning": None,
            }
        )
        fixture.write_all()
        return
    if kind == "drop-terminal-event":
        fixture.execution["events"] = [
            item for item in fixture.execution["events"] if item["event"] != "lease-completed"
        ]
        fixture.write_all()
        return
    if kind == "duplicate-event-sequence":
        fixture.execution["events"][1]["sequence"] = fixture.execution["events"][0]["sequence"]
        fixture.write_all()
        return
    if kind == "tamper-candidate-bundle":
        fixture.paths["candidate_bundle"].write_bytes(b"replaced inert bundle\n")
        return
    if kind == "delete-candidate-bundle":
        fixture.paths["candidate_bundle"].unlink()
        return
    if kind == "candidate-bundle-inside-repo":
        target = fixture.repo / "candidate.bundle"
        target.write_bytes(fixture.paths["candidate_bundle"].read_bytes())
        fixture.execution["candidates"][0]["bundle_ref"] = str(target.resolve())
        fixture.write_all()
        return
    if kind == "candidate-workspace-is-repo":
        path = str(fixture.repo.resolve())
        fixture.execution["actors"][1]["workspace_ref"] = path
        fixture.execution["candidates"][0]["workspace_ref"] = path
        fixture.write_all()
        return
    if kind == "append-report-check":
        fixture.report["checks"].append(
            {
                "name": "smuggled",
                "command": "python -c \"print('smuggled')\"",
                "status": "passed",
                "evidence_ref": "none",
            }
        )
        fixture.write_all()
        return
    if kind == "raw-json":
        target = fixture.paths[operation["artifact"]]
        payload = operation["payload"]
        if payload == "duplicate":
            target.write_bytes(b'{"version":5,"version":5}')
        elif payload == "nan":
            target.write_bytes(b'{"version":NaN}')
        elif payload == "invalid-utf8":
            target.write_bytes(b'{"version":"\xff"}')
        else:
            target.write_text("[" * 160 + "0" + "]" * 160, encoding="utf-8")
        return
    if kind == "missing-artifact":
        fixture.paths[operation["artifact"]].unlink()
        return
    if kind == "artifact-collision":
        fixture.paths["report"] = fixture.paths["packet"]
        return
    if kind == "artifact-inside-repo":
        target = fixture.repo / "coordination-plan.json"
        shutil.copyfile(fixture.paths["plan"], target)
        fixture.paths["plan"] = target
        return
    if kind == "wrong-anchor":
        return
    if kind in {"unknown-v5-argument", "v4-rejects-v5-argument", "orphan-previous-envelope-anchor"}:
        return
    raise AssertionError(f"unsupported benchmark operation: {kind}")


def create_python_observer(directory: Path, marker: Path) -> Path:
    directory.mkdir()
    if os.name == "nt":
        wrapper = directory / "python.cmd"
        wrapper.write_text(
            "@echo off\r\n"
            f'> "{marker}" echo acceptance-ran\r\n'
            f'"{sys.executable}" %*\r\n',
            encoding="utf-8",
        )
    else:
        wrapper = directory / "python"
        wrapper.write_text(
            "#!/bin/sh\n"
            f": > '{marker}'\n"
            f"exec '{sys.executable}' \"$@\"\n",
            encoding="utf-8",
        )
        wrapper.chmod(0o755)
    return wrapper


def gate_command(fixture: Fixture, operation: dict[str, Any]) -> list[str]:
    kind = operation["kind"]
    if kind == "v4-rejects-v5-argument":
        return [
            sys.executable,
            "-B",
            str(SKILL_DIR / "scripts" / "check_delivery.py"),
            "--repo-root",
            str(fixture.repo),
            "--baseline-manifest",
            str(fixture.paths["baseline"]),
            "--host-capabilities",
            str(fixture.paths["capabilities"]),
        ]
    command = [
        sys.executable,
        "-B",
        str(SKILL_DIR / "scripts" / "check_delivery_v5.py"),
        *fixture.gate_argv(),
    ]
    if kind == "wrong-anchor":
        names = {
            "packet": "--expect-packet-sha256",
            "verifier": "--expect-verifier-sha256",
            "envelope": "--expect-orchestration-envelope-sha256",
            "execution": "--expect-execution-receipt-sha256",
            "verification": "--expect-verification-receipt-sha256",
        }
        flag = names[operation["anchor"]]
        command[command.index(flag) + 1] = "0" * 64
    elif kind == "unknown-v5-argument":
        command.append("--v5-smuggled-argument")
    elif kind == "orphan-previous-envelope-anchor":
        command.extend(["--expect-previous-orchestration-envelope-sha256", "0" * 64])
    return command


def parse_gate_output(completed: subprocess.CompletedProcess[str]) -> dict[str, Any]:
    stdout = completed.stdout.strip()
    try:
        value = json.loads(stdout)
    except json.JSONDecodeError:
        return {
            "passed": False,
            "errors": [completed.stderr.strip() or stdout or "gate emitted no result"],
        }
    if not isinstance(value, dict):
        return {"passed": False, "errors": ["gate result is not an object"]}
    return value


def actual_usage_within_limit(elapsed: float, completed: subprocess.CompletedProcess[str]) -> bool:
    output_bytes = len(completed.stdout.encode("utf-8")) + len(completed.stderr.encode("utf-8"))
    return elapsed <= WORKER_TIMEOUT_SECONDS and output_bytes <= WORKER_OUTPUT_LIMIT


def run_worker(task: dict[str, Any], digest: str, challenge: str) -> dict[str, Any]:
    profile_names = {
        "path-ascii": "ascii",
        "path-space": "space profile",
        "path-unicode": "路径-π",
        "path-mixed": "MiXeD-Case",
        "path-long": "long-" + "x" * 72,
    }
    with tempfile.TemporaryDirectory(prefix="wle-full-gate-") as temporary:
        base = Path(temporary)
        root = base / profile_names.get(task["fixture"], "fixture")
        root.mkdir()
        fixture = make_benchmark_fixture(root, task)
        apply_operation(fixture, task["operation"])
        marker = base / "acceptance.marker"
        wrapper_dir = base / "observer-bin"
        create_python_observer(wrapper_dir, marker)
        before = state_manifest_sha256(build_state_manifest(fixture.repo))
        environment = os.environ.copy()
        environment["PATH"] = str(wrapper_dir) + os.pathsep + environment.get("PATH", "")
        started = time.monotonic()
        completed = subprocess.run(
            gate_command(fixture, task["operation"]),
            cwd=SKILL_DIR,
            check=False,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            env=environment,
            timeout=WORKER_TIMEOUT_SECONDS,
        )
        elapsed = time.monotonic() - started
        result = parse_gate_output(completed)
        after = state_manifest_sha256(build_state_manifest(fixture.repo))
        acceptance_observed = marker.exists()
        expected = task["expected"]
        observed_pass = completed.returncode == 0 and result.get("passed") is True
        observed_errors = " ".join(str(item) for item in result.get("errors", [])).casefold()
        expected_error = (
            not expected["error_any"]
            or any(token.casefold() in observed_errors for token in expected["error_any"])
        )
        oracle_passed = (
            observed_pass is expected["passed"]
            and expected_error
            and acceptance_observed is expected["acceptance_observed"]
        )
        repository_unchanged = before == after
        if expected["passed"]:
            canonical = fixture.execution["canonical_pre_acceptance"]
            observations = result.get("observations", {})
            controller_diff_correct = (
                observations.get("changed_paths") == canonical["changed_paths"]
                and observations.get("final_state_sha256") == canonical["final_state_sha256"]
                and observations.get("diff_sha256") == canonical["diff_sha256"]
            )
        else:
            controller_diff_correct = repository_unchanged and not acceptance_observed
        no_hard_violation = repository_unchanged and (
            observed_pass if expected["passed"] else not acceptance_observed
        )
        within = actual_usage_within_limit(elapsed, completed)
        task_success = oracle_passed and controller_diff_correct and no_hard_violation and within
        return {
            "id": task["id"],
            "stratum": task["stratum"],
            "suite_sha256": digest,
            "challenge": challenge,
            "task_success": task_success,
            "oracle_passed": oracle_passed,
            "controller_observed_diff_correct": controller_diff_correct,
            "no_hard_invariant_violation": no_hard_violation,
            "within_resource_envelope": within,
            "acceptance_observed": acceptance_observed,
            "gate_returncode": completed.returncode,
            "detail": {
                "expected": expected,
                "errors": result.get("errors", []),
                "elapsed_seconds": elapsed,
                "stdout_sha256": hashlib.sha256(completed.stdout.encode()).hexdigest(),
                "stderr": completed.stderr[-1000:],
            },
        }


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


def validate_statistics() -> None:
    if exact_one_sided_lower(0, 10) != 0.0:
        raise AssertionError("zero-success exact limit failed")
    closed = 0.05 ** (1 / 150)
    if not math.isclose(exact_one_sided_lower(150, 150), closed, rel_tol=1e-14):
        raise AssertionError("all-success exact limit failed")
    values = [exact_one_sided_lower(successes, 20) for successes in range(21)]
    if any(left > right for left, right in zip(values, values[1:])):
        raise AssertionError("exact lower bound is not monotonic")


def clean_worker_environment() -> dict[str, str]:
    keep = {
        "PATH",
        "SYSTEMROOT",
        "WINDIR",
        "COMSPEC",
        "PATHEXT",
        "TEMP",
        "TMP",
        "TMPDIR",
        "LANG",
        "LC_ALL",
    }
    environment = {key: value for key, value in os.environ.items() if key.upper() in keep}
    environment["PYTHONIOENCODING"] = "utf-8"
    return environment


def invoke_worker(task: dict[str, Any], digest: str) -> dict[str, Any]:
    challenge = secrets.token_hex(16)
    command = [
        sys.executable,
        "-I",
        "-B",
        str(Path(__file__).resolve()),
        "--worker",
        task["id"],
        "--suite-sha256",
        digest,
        "--challenge",
        challenge,
    ]
    try:
        completed = subprocess.run(
            command,
            cwd=SKILL_DIR,
            check=False,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            env=clean_worker_environment(),
            timeout=WORKER_TIMEOUT_SECONDS,
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        return {
            "id": task["id"],
            "stratum": task["stratum"],
            "task_success": False,
            "worker_error": f"worker launch/timeout: {exc}",
        }
    if completed.returncode != 0:
        return {
            "id": task["id"],
            "stratum": task["stratum"],
            "task_success": False,
            "worker_error": completed.stderr.strip() or completed.stdout.strip(),
        }
    if len(completed.stdout.encode("utf-8")) > WORKER_OUTPUT_LIMIT:
        return {
            "id": task["id"],
            "stratum": task["stratum"],
            "task_success": False,
            "worker_error": "worker output exceeded limit",
        }
    try:
        value = json.loads(completed.stdout)
    except json.JSONDecodeError as exc:
        return {
            "id": task["id"],
            "stratum": task["stratum"],
            "task_success": False,
            "worker_error": f"invalid worker JSON: {exc}",
        }
    bindings = (
        isinstance(value, dict)
        and set(value) == WORKER_KEYS
        and value.get("id") == task["id"]
        and value.get("stratum") == task["stratum"]
        and value.get("suite_sha256") == digest
        and value.get("challenge") == challenge
        and type(value.get("task_success")) is bool
    )
    if not bindings:
        return {
            "id": task["id"],
            "stratum": task["stratum"],
            "task_success": False,
            "worker_error": "worker result schema or bindings are invalid",
        }
    return value


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--require-all", action="store_true")
    parser.add_argument("--case")
    parser.add_argument("--json", action="store_true")
    parser.add_argument("--worker")
    parser.add_argument("--suite-sha256")
    parser.add_argument("--challenge")
    parser.add_argument("--print-suite-digest", action="store_true")
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8", errors="backslashreplace")
    if hasattr(sys.stderr, "reconfigure"):
        sys.stderr.reconfigure(encoding="utf-8", errors="backslashreplace")
    args = parse_args(argv)
    tasks = frozen_tasks()
    digest = suite_digest(tasks)
    if args.print_suite_digest:
        print(digest)
        return 0
    if FROZEN_SUITE_SHA256 != "TO_BE_PINNED" and digest != FROZEN_SUITE_SHA256:
        print("frozen suite digest mismatch", file=sys.stderr)
        return 2
    validate_statistics()
    if args.worker:
        if args.suite_sha256 != digest or not args.challenge:
            print("worker suite digest and challenge are required", file=sys.stderr)
            return 2
        task = next((item for item in tasks if item["id"] == args.worker), None)
        if task is None:
            print("unknown worker task", file=sys.stderr)
            return 2
        try:
            result = run_worker(task, digest, args.challenge)
        except (OSError, RuntimeError, subprocess.TimeoutExpired, ValueError) as exc:
            print(f"worker failed closed: {exc}", file=sys.stderr)
            return 1
        print(json.dumps(result, ensure_ascii=False, separators=(",", ":")))
        return 0

    if args.require_all and args.case:
        print("--require-all cannot be combined with --case", file=sys.stderr)
        return 2
    selected = tasks
    if args.case:
        selected = [task for task in tasks if task["id"] == args.case]
        if not selected:
            print("unknown --case", file=sys.stderr)
            return 2
    results: list[dict[str, Any]] = []
    with ThreadPoolExecutor(max_workers=RELEASE_JOBS if len(selected) > 1 else 1) as executor:
        futures = {executor.submit(invoke_worker, task, digest): task for task in selected}
        for future in as_completed(futures):
            try:
                results.append(future.result())
            except Exception as exc:  # fail one trial; never lose the whole benchmark report
                task = futures[future]
                results.append(
                    {
                        "id": task["id"],
                        "stratum": task["stratum"],
                        "task_success": False,
                        "worker_error": f"unexpected parent exception: {exc}",
                    }
                )
    results.sort(key=lambda item: item["id"])
    successes = sum(item.get("task_success") is True for item in results)
    lower = exact_one_sided_lower(successes, len(results))
    strata: dict[str, dict[str, int]] = {}
    for result in results:
        bucket = strata.setdefault(result["stratum"], {"successes": 0, "total": 0})
        bucket["total"] += 1
        bucket["successes"] += int(result.get("task_success") is True)
    every_stratum = len(strata) == 6 and all(
        values == {"successes": 25, "total": 25} for values in strata.values()
    )
    release_shape = (
        len(results) == RELEASE_TASKS
        and successes == RELEASE_TASKS
        and every_stratum
        and lower > 0.98
    )
    passed = release_shape if args.require_all else successes == len(results)
    payload = {
        "passed": passed,
        "release_shape_satisfied": release_shape,
        "benchmark_id": BENCHMARK_ID,
        "claim_scope": CLAIM_SCOPE,
        "suite_sha256": digest,
        "fresh_isolated_python_process_per_task": True,
        "full_cli_gate_per_task": True,
        "semantic_fingerprints_unique_without_ids": True,
        "fixed_release_jobs": RELEASE_JOBS,
        "successes": successes,
        "total": len(results),
        "one_sided_confidence": 0.95,
        "exact_lower_bound": lower,
        "strata": strata,
        "failures": [result for result in results if result.get("task_success") is not True],
    }
    print(json.dumps(payload, ensure_ascii=True, indent=2, sort_keys=True))
    return 0 if passed else 1


if __name__ == "__main__":
    raise SystemExit(main())
