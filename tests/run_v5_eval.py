#!/usr/bin/env python3
"""Run deterministic packet-v5, DAG, receipt, and fail-closed gate tests."""

from __future__ import annotations

import argparse
import copy
import hashlib
import io
import json
import math
import os
import subprocess
import sys
import tempfile
from unittest import mock
from contextlib import redirect_stdout
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable


TEST_DIR = Path(__file__).resolve().parent
SKILL_DIR = TEST_DIR.parent
sys.path.insert(0, str(SKILL_DIR / "scripts"))
sys.path.insert(0, str(TEST_DIR))

from check_delivery import (  # noqa: E402
    build_state_manifest,
    state_manifest_changed_paths,
    state_manifest_sha256,
    validate_packet_preflight as validate_packet_v4,
)
from check_delivery_v5 import (  # noqa: E402
    GateError,
    _diff_digest,
    _json_digest,
    load_json,
    main as gate_main,
    validate_execution_receipt,
    validate_orchestration_envelope,
    validate_resource_envelope,
    validate_sandbox_profile,
    validate_verification_receipt,
    validate_packet_lineage,
    validate_v5_report,
    verifier_bundle_sha256,
)
from diverge import build_packet, packet_sha256, repo_path  # noqa: E402
from diverge_v5 import (  # noqa: E402
    CAPABILITY_NAMES,
    PACKET_V5_POLICY,
    build_packet_v5,
    build_runtime_delegation_v5,
    build_task_prompt_v5,
    normalize_host_capabilities,
    sha256_json,
    validate_coordination_plan,
    validate_host_capabilities,
    validate_packet_v5,
)
from run_eval import (  # noqa: E402
    BROADER_COMMAND,
    TARGETED_COMMAND,
    base_report,
    bind_authority_grants,
    valid_contract,
)


def write_json(path: Path, value: Any) -> None:
    path.write_text(
        json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":")),
        encoding="utf-8",
        newline="\n",
    )


def no_change_report(packet: dict[str, Any]) -> dict[str, Any]:
    report = base_report(packet)
    implementation = report["implementation"]
    implementation["status"] = "no-change"
    implementation["changed_paths"] = []
    implementation["no_change_reason"] = "Frozen acceptance is already satisfied."
    implementation["minimalism"]["selected_rung"] = "not-needed"
    return report


@dataclass
class Fixture:
    root: Path
    repo: Path
    paths: dict[str, Path]
    baseline: dict[str, Any]
    packet: dict[str, Any]
    capabilities: dict[str, Any]
    plan: dict[str, Any]
    resources: dict[str, Any]
    sandbox: dict[str, Any]
    envelope: dict[str, Any]
    execution: dict[str, Any]
    verification: dict[str, Any]
    report: dict[str, Any]

    def write_all(self) -> None:
        for name in (
            "baseline",
            "packet",
            "capabilities",
            "plan",
            "resources",
            "sandbox",
            "envelope",
            "execution",
            "verification",
            "report",
        ):
            write_json(self.paths[name], getattr(self, name))

    def gate_argv(self) -> list[str]:
        return [
            "--repo-root",
            str(self.repo),
            "--baseline-manifest",
            str(self.paths["baseline"]),
            "--packet",
            str(self.paths["packet"]),
            "--report",
            str(self.paths["report"]),
            "--host-capabilities",
            str(self.paths["capabilities"]),
            "--coordination-plan",
            str(self.paths["plan"]),
            "--resource-envelope",
            str(self.paths["resources"]),
            "--sandbox-profile",
            str(self.paths["sandbox"]),
            "--orchestration-envelope",
            str(self.paths["envelope"]),
            "--execution-receipt",
            str(self.paths["execution"]),
            "--verification-receipt",
            str(self.paths["verification"]),
            "--expect-packet-sha256",
            self.packet["packet_sha256"],
            "--expect-verifier-sha256",
            verifier_bundle_sha256(),
            "--expect-orchestration-envelope-sha256",
            _json_digest(self.envelope),
            "--expect-execution-receipt-sha256",
            _json_digest(self.execution),
            "--expect-verification-receipt-sha256",
            _json_digest(self.verification),
        ]


def make_fixture(
    root: Path,
    *,
    task: str = "Verify an already-correct implementation",
    contract_id: str = "v5-eval",
    source_text: str = "BASELINE = True\n",
    contract_paths: list[str] | None = None,
    extra_sources: dict[str, str] | None = None,
) -> Fixture:
    repo = root / "repo"
    source = repo / "src" / "example.py"
    source.parent.mkdir(parents=True)
    source.write_text(source_text, encoding="utf-8", newline="\n")
    for relative, content in (extra_sources or {}).items():
        extra_source = repo / relative
        extra_source.parent.mkdir(parents=True, exist_ok=True)
        extra_source.write_text(content, encoding="utf-8", newline="\n")
    paths = {
        name: root / f"{name}.json"
        for name in (
            "baseline",
            "packet",
            "capabilities",
            "plan",
            "resources",
            "sandbox",
            "envelope",
            "execution",
            "verification",
            "report",
        )
    }
    baseline = build_state_manifest(repo)
    write_json(paths["baseline"], baseline)
    contract = valid_contract(
        task, contract_paths or ["src/example.py"], "change", contract_id
    )
    contract["baseline"]["repository_ref"] = baseline["repository_ref"]
    contract["baseline"]["state_ref"] = os.path.normcase(
        os.path.normpath(str(paths["baseline"].resolve()))
    )
    contract["baseline"]["state_sha256"] = state_manifest_sha256(baseline)
    bind_authority_grants(contract)
    packet = build_packet_v5(contract, risk="medium", coordination="independent")
    capabilities = normalize_host_capabilities(
        {
            "spawn": True,
            "join": True,
            "steer_child": True,
            "enforced_readonly": True,
            "independent_verifier": True,
            "max_depth_control": True,
        }
    )
    plan = {
        "version": 1,
        "packet_sha256": packet["packet_sha256"],
        "revision": 0,
        "supersedes_sha256": None,
        "mode": "independent",
        "execution": "read-only-proposals",
        "dispatch": "root-assign",
        "communication": "root-relay",
        "tasks": [
            {
                "id": "task-1",
                "objective": "Check the frozen implementation without writing.",
                "dependencies": [],
                "read_paths": ["src/example.py"],
                "candidate_write_paths": ["src/example.py"],
                "acceptance_ids": ["AC-001", "AC-002"],
                "output_contract": {
                    "version": 1,
                    "kind": "candidate-proposal",
                    "lane_ids": [lane["id"] for lane in packet["lanes"]],
                },
            }
        ],
        "assignments": [
            {
                "task_id": "task-1",
                "runtime_identity": "worker-1",
                "agent_profile": None,
                "model": None,
                "reasoning": None,
            }
        ],
    }
    resources = {
        "version": 1,
        "limits": {
            "max_tokens": 100000,
            "max_tool_calls": 1000,
            "max_process_seconds": 3600,
            "max_artifact_bytes": 1000000,
            "max_concurrency": 8,
        },
    }
    sandbox = {
        "version": 1,
        "isolation": "os-process",
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
    write_json(paths["capabilities"], capabilities)
    write_json(paths["plan"], plan)
    write_json(paths["resources"], resources)
    write_json(paths["sandbox"], sandbox)
    envelope = {
        "version": 1,
        "packet_sha256": packet["packet_sha256"],
        "controller_ref": "controller://v5-eval",
        "host_capabilities_ref": str(paths["capabilities"].resolve()),
        "host_capabilities_sha256": _json_digest(capabilities),
        "task_graph_ref": str(paths["plan"].resolve()),
        "task_graph_sha256": _json_digest(plan),
        "resource_envelope_ref": str(paths["resources"].resolve()),
        "resource_envelope_sha256": _json_digest(resources),
        "sandbox_profile_ref": str(paths["sandbox"].resolve()),
        "sandbox_profile_sha256": _json_digest(sandbox),
        "previous_envelope_sha256": None,
        "predecessor_execution_started": None,
        "sealed_before_first_spawn": True,
        "narrowing_attested": True,
    }
    baseline_digest = state_manifest_sha256(baseline)
    final_digest = baseline_digest
    changed_paths: list[str] = []
    diff_digest = _diff_digest(
        baseline["repository_ref"], baseline_digest, final_digest, changed_paths
    )
    task_prompt, _, _ = build_task_prompt_v5(
        packet, plan, plan["tasks"][0], "worker-1", _json_digest(envelope)
    )
    task_prompt_digest = hashlib.sha256(task_prompt.encode("utf-8")).hexdigest()
    execution = {
        "version": 2,
        "packet_sha256": packet["packet_sha256"],
        "controller_ref": envelope["controller_ref"],
        "orchestration_envelope_sha256": _json_digest(envelope),
        "task_graph_sha256": _json_digest(plan),
        "deliberation_sha256": None,
        "actors": [
            {
                "id": "main-thread",
                "parent_id": None,
                "kind": "main-integrator",
                "task_ids": [],
                "workspace_ref": None,
            },
            {
                "id": "worker-1",
                "parent_id": "main-thread",
                "kind": "analysis-worker",
                "task_ids": ["task-1"],
                "workspace_ref": None,
            },
        ],
        "leases": [
            {
                "id": "lease-1",
                "task_id": "task-1",
                "actor_id": "worker-1",
                "grant_sequence": 3,
                "terminal_sequence": 4,
                "state": "completed",
                "task_prompt_sha256": task_prompt_digest,
                "capabilities": ["enforced_readonly"],
                "read_paths": ["src/example.py"],
                "candidate_write_paths": ["src/example.py"],
                "acceptance_ids": ["AC-001", "AC-002"],
            }
        ],
        "candidates": [],
        "integrations": [],
        "events": [
            {
                "sequence": 1,
                "event": "envelope-sealed",
                "task_id": None,
                "actor_id": "main-thread",
                "lease_id": None,
                "candidate_id": None,
                "artifact_sha256": _json_digest(envelope),
            },
            {
                "sequence": 2,
                "event": "actor-spawned",
                "task_id": None,
                "actor_id": "worker-1",
                "lease_id": None,
                "candidate_id": None,
                "artifact_sha256": _json_digest(envelope),
            },
            {
                "sequence": 3,
                "event": "lease-granted",
                "task_id": "task-1",
                "actor_id": "worker-1",
                "lease_id": "lease-1",
                "candidate_id": None,
                "artifact_sha256": task_prompt_digest,
            },
            {
                "sequence": 4,
                "event": "lease-completed",
                "task_id": "task-1",
                "actor_id": "worker-1",
                "lease_id": "lease-1",
                "candidate_id": None,
                "artifact_sha256": None,
            },
        ],
        "complete_event_capture": True,
        "orphan_processes_detected": False,
        "canonical_pre_acceptance": {
            "repository_ref": baseline["repository_ref"],
            "baseline_state_sha256": baseline_digest,
            "final_state_sha256": final_digest,
            "diff_sha256": diff_digest,
            "changed_paths": changed_paths,
            "integrator_id": "main-thread",
            "non_integrator_writes_detected": False,
        },
        "resource_usage": {
            "tokens": 100,
            "tool_calls": 2,
            "process_seconds": 1,
            "artifact_bytes": 0,
            "peak_concurrency": 1,
        },
        "policy_violations": [],
    }
    verification = {
        "version": 1,
        "packet_sha256": packet["packet_sha256"],
        "orchestration_envelope_sha256": _json_digest(envelope),
        "execution_receipt_sha256": _json_digest(execution),
        "controller_ref": envelope["controller_ref"],
        "verifier_id": "independent-verifier-1",
        "verifier_bundle_sha256": verifier_bundle_sha256(),
        "repository_ref": baseline["repository_ref"],
        "final_state_sha256": final_digest,
        "diff_sha256": diff_digest,
        "fresh_context": True,
        "write_access": False,
        "candidate_outputs_visible": False,
        "checks": [
            {
                "criterion_id": criterion["id"],
                "command": criterion["command"],
                "exit_code": 0,
            }
            for criterion in packet["contract"]["acceptance"]
        ],
        "verdict": "passed",
        "policy_violations": [],
    }
    report = no_change_report(packet)
    report["orchestration"] = {
        "envelope_sha256": _json_digest(envelope),
        "execution_receipt_sha256": _json_digest(execution),
        "verification_receipt_sha256": _json_digest(verification),
        "candidates": [],
    }
    fixture = Fixture(
        root,
        repo,
        paths,
        baseline,
        packet,
        capabilities,
        plan,
        resources,
        sandbox,
        envelope,
        execution,
        verification,
        report,
    )
    fixture.write_all()
    return fixture


def make_isolated_fixture(root: Path, **fixture_options: Any) -> Fixture:
    fixture = make_fixture(root, **fixture_options)
    workspace = root / "candidate-workspace-1"
    workspace.mkdir()
    bundle = root / "candidate-1.bundle"
    bundle.write_bytes(b"inert candidate bundle\n")
    fixture.paths["candidate_bundle"] = bundle
    fixture.capabilities["capabilities"]["isolated_candidate_workspace"] = True
    fixture.capabilities["capabilities"]["canonical_write_block"] = True
    fixture.plan["execution"] = "isolated-candidates"
    fixture.plan["tasks"][0]["output_contract"]["kind"] = "candidate-bundle"
    fixture.repo.joinpath("src", "example.py").write_text(
        "BASELINE = False\n", encoding="utf-8", newline="\n"
    )
    final_manifest = build_state_manifest(fixture.repo)
    baseline_digest = state_manifest_sha256(fixture.baseline)
    final_digest = state_manifest_sha256(final_manifest)
    changed_paths = ["src/example.py"]
    diff_digest = _diff_digest(
        final_manifest["repository_ref"], baseline_digest, final_digest, changed_paths
    )
    write_json(fixture.paths["capabilities"], fixture.capabilities)
    write_json(fixture.paths["plan"], fixture.plan)
    fixture.envelope["host_capabilities_sha256"] = _json_digest(fixture.capabilities)
    fixture.envelope["task_graph_sha256"] = _json_digest(fixture.plan)
    bundle_digest = hashlib.sha256(bundle.read_bytes()).hexdigest()
    task_prompt, _, _ = build_task_prompt_v5(
        fixture.packet,
        fixture.plan,
        fixture.plan["tasks"][0],
        "worker-1",
        _json_digest(fixture.envelope),
    )
    task_prompt_digest = hashlib.sha256(task_prompt.encode("utf-8")).hexdigest()
    fixture.execution = {
        "version": 2,
        "packet_sha256": fixture.packet["packet_sha256"],
        "controller_ref": fixture.envelope["controller_ref"],
        "orchestration_envelope_sha256": _json_digest(fixture.envelope),
        "task_graph_sha256": _json_digest(fixture.plan),
        "deliberation_sha256": None,
        "actors": [
            {
                "id": "main-thread",
                "parent_id": None,
                "kind": "main-integrator",
                "task_ids": [],
                "workspace_ref": None,
            },
            {
                "id": "worker-1",
                "parent_id": "main-thread",
                "kind": "candidate-worker",
                "task_ids": ["task-1"],
                "workspace_ref": str(workspace.resolve()),
            },
        ],
        "leases": [
            {
                "id": "lease-1",
                "task_id": "task-1",
                "actor_id": "worker-1",
                "grant_sequence": 3,
                "terminal_sequence": 5,
                "state": "completed",
                "task_prompt_sha256": task_prompt_digest,
                "capabilities": [
                    "isolated_candidate_workspace",
                    "canonical_write_block",
                ],
                "read_paths": ["src/example.py"],
                "candidate_write_paths": ["src/example.py"],
                "acceptance_ids": ["AC-001", "AC-002"],
            }
        ],
        "candidates": [
            {
                "id": "candidate-1",
                "task_id": "task-1",
                "actor_id": "worker-1",
                "lease_id": "lease-1",
                "workspace_ref": str(workspace.resolve()),
                "workspace_isolated": True,
                "canonical_write_blocked": True,
                "base_state_sha256": baseline_digest,
                "bundle_ref": str(bundle.resolve()),
                "bundle_sha256": bundle_digest,
                "changed_paths": changed_paths,
                "local_checks_sha256": "1" * 64,
                "target_repository_write_detected": False,
                "artifact_store_write_detected": False,
                "verifier_access_detected": False,
                "network_access": False,
                "credential_access": False,
                "shared_git_access": False,
            }
        ],
        "integrations": [
            {
                "task_id": "task-1",
                "candidate_id": "candidate-1",
                "bundle_sha256": bundle_digest,
                "integrator_id": "main-thread",
                "disposition": "selected",
                "reason": "The scoped candidate matches the frozen base and acceptance.",
            }
        ],
        "events": [
            {
                "sequence": 1,
                "event": "envelope-sealed",
                "task_id": None,
                "actor_id": "main-thread",
                "lease_id": None,
                "candidate_id": None,
                "artifact_sha256": _json_digest(fixture.envelope),
            },
            {
                "sequence": 2,
                "event": "actor-spawned",
                "task_id": None,
                "actor_id": "worker-1",
                "lease_id": None,
                "candidate_id": None,
                "artifact_sha256": _json_digest(fixture.envelope),
            },
            {
                "sequence": 3,
                "event": "lease-granted",
                "task_id": "task-1",
                "actor_id": "worker-1",
                "lease_id": "lease-1",
                "candidate_id": None,
                "artifact_sha256": task_prompt_digest,
            },
            {
                "sequence": 4,
                "event": "candidate-produced",
                "task_id": "task-1",
                "actor_id": "worker-1",
                "lease_id": "lease-1",
                "candidate_id": "candidate-1",
                "artifact_sha256": bundle_digest,
            },
            {
                "sequence": 5,
                "event": "lease-completed",
                "task_id": "task-1",
                "actor_id": "worker-1",
                "lease_id": "lease-1",
                "candidate_id": None,
                "artifact_sha256": None,
            },
            {
                "sequence": 6,
                "event": "candidate-selected",
                "task_id": "task-1",
                "actor_id": "main-thread",
                "lease_id": "lease-1",
                "candidate_id": "candidate-1",
                "artifact_sha256": bundle_digest,
            },
            {
                "sequence": 7,
                "event": "integration-completed",
                "task_id": "task-1",
                "actor_id": "main-thread",
                "lease_id": "lease-1",
                "candidate_id": "candidate-1",
                "artifact_sha256": bundle_digest,
            },
        ],
        "complete_event_capture": True,
        "orphan_processes_detected": False,
        "canonical_pre_acceptance": {
            "repository_ref": final_manifest["repository_ref"],
            "baseline_state_sha256": baseline_digest,
            "final_state_sha256": final_digest,
            "diff_sha256": diff_digest,
            "changed_paths": changed_paths,
            "integrator_id": "main-thread",
            "non_integrator_writes_detected": False,
        },
        "resource_usage": {
            "tokens": 200,
            "tool_calls": 5,
            "process_seconds": 2,
            "artifact_bytes": bundle.stat().st_size,
            "peak_concurrency": 1,
        },
        "policy_violations": [],
    }
    fixture.verification = {
        "version": 1,
        "packet_sha256": fixture.packet["packet_sha256"],
        "orchestration_envelope_sha256": _json_digest(fixture.envelope),
        "execution_receipt_sha256": _json_digest(fixture.execution),
        "controller_ref": fixture.envelope["controller_ref"],
        "verifier_id": "independent-verifier-1",
        "verifier_bundle_sha256": verifier_bundle_sha256(),
        "repository_ref": final_manifest["repository_ref"],
        "final_state_sha256": final_digest,
        "diff_sha256": diff_digest,
        "fresh_context": True,
        "write_access": False,
        "candidate_outputs_visible": False,
        "checks": [
            {"criterion_id": "AC-001", "command": TARGETED_COMMAND, "exit_code": 0},
            {"criterion_id": "AC-002", "command": BROADER_COMMAND, "exit_code": 0},
        ],
        "verdict": "passed",
        "policy_violations": [],
    }
    fixture.report = base_report(fixture.packet)
    fixture.report["orchestration"] = {
        "envelope_sha256": _json_digest(fixture.envelope),
        "execution_receipt_sha256": _json_digest(fixture.execution),
        "verification_receipt_sha256": _json_digest(fixture.verification),
        "candidates": [
            {
                "task_id": "task-1",
                "candidate_id": "candidate-1",
                "bundle_sha256": bundle_digest,
                "disposition": "selected",
                "reason": "The scoped candidate matches the frozen base and acceptance.",
            }
        ],
    }
    fixture.write_all()
    return fixture


def make_shared_fixture(
    root: Path, *, peer_message: bool = False, **fixture_options: Any
) -> Fixture:
    fixture = make_fixture(root, **fixture_options)
    fixture.packet = build_packet_v5(
        fixture.packet["contract"], risk="medium", coordination="shared"
    )
    participants = ("agent-1", "agent-2")
    lane_groups = [
        [
            lane["id"]
            for lane_index, lane in enumerate(fixture.packet["lanes"])
            if lane_index % len(participants) == participant_index
        ]
        for participant_index in range(len(participants))
    ]
    fixture.plan = {
        "version": 1,
        "packet_sha256": fixture.packet["packet_sha256"],
        "revision": 0,
        "supersedes_sha256": None,
        "mode": "shared",
        "execution": "read-only-proposals",
        "dispatch": "root-assign",
        "communication": "peer-message" if peer_message else "root-relay",
        "tasks": [
            {
                "id": f"task-{index + 1}",
                "objective": f"Analyze assigned sealed lanes for {identity}.",
                "dependencies": [],
                "read_paths": ["src/example.py"],
                "candidate_write_paths": ["src/example.py"],
                "acceptance_ids": ["AC-001", "AC-002"],
                "output_contract": {
                    "version": 1,
                    "kind": "candidate-proposal",
                    "lane_ids": lane_groups[index],
                },
            }
            for index, identity in enumerate(participants)
        ],
        "assignments": [
            {
                "task_id": f"task-{index + 1}",
                "runtime_identity": identity,
                "agent_profile": None,
                "model": None,
                "reasoning": None,
            }
            for index, identity in enumerate(participants)
        ],
    }
    fixture.capabilities["capabilities"]["peer_message"] = peer_message
    write_json(fixture.paths["packet"], fixture.packet)
    write_json(fixture.paths["capabilities"], fixture.capabilities)
    write_json(fixture.paths["plan"], fixture.plan)
    fixture.envelope["packet_sha256"] = fixture.packet["packet_sha256"]
    fixture.envelope["host_capabilities_sha256"] = _json_digest(fixture.capabilities)
    fixture.envelope["task_graph_sha256"] = _json_digest(fixture.plan)
    baseline_digest = state_manifest_sha256(fixture.baseline)
    diff_digest = _diff_digest(
        fixture.baseline["repository_ref"], baseline_digest, baseline_digest, []
    )
    shared_report = no_change_report(fixture.packet)
    board_digest = shared_report["deliberation"]["peer_board_sha256"]
    deliberation_digest = _json_digest(shared_report["deliberation"])
    envelope_digest = _json_digest(fixture.envelope)
    events: list[dict[str, Any]] = [
        {
            "sequence": 1,
            "event": "envelope-sealed",
            "task_id": None,
            "actor_id": "main-thread",
            "lease_id": None,
            "candidate_id": None,
            "artifact_sha256": envelope_digest,
        },
        *[
            {
                "sequence": index + 2,
                "event": "actor-spawned",
                "task_id": None,
                "actor_id": identity,
                "lease_id": None,
                "candidate_id": None,
                "artifact_sha256": envelope_digest,
            }
            for index, identity in enumerate(participants)
        ],
    ]
    leases: list[dict[str, Any]] = []
    exchange_count = len(participants) if peer_message else 1
    terminal_start = 7 + exchange_count
    for index, identity in enumerate(participants):
        grant_sequence = index + 4
        terminal_sequence = terminal_start + index
        task_id = f"task-{index + 1}"
        lease_id = f"lease-{index + 1}"
        task_prompt, _, _ = build_task_prompt_v5(
            fixture.packet,
            fixture.plan,
            fixture.plan["tasks"][index],
            identity,
            envelope_digest,
        )
        task_prompt_digest = hashlib.sha256(task_prompt.encode("utf-8")).hexdigest()
        leases.append(
            {
                "id": lease_id,
                "task_id": task_id,
                "actor_id": identity,
                "grant_sequence": grant_sequence,
                "terminal_sequence": terminal_sequence,
                "state": "completed",
                "task_prompt_sha256": task_prompt_digest,
                "capabilities": ["enforced_readonly"],
                "read_paths": ["src/example.py"],
                "candidate_write_paths": ["src/example.py"],
                "acceptance_ids": ["AC-001", "AC-002"],
            }
        )
        events.append(
            {
                "sequence": grant_sequence,
                "event": "lease-granted",
                "task_id": task_id,
                "actor_id": identity,
                "lease_id": lease_id,
                "candidate_id": None,
                "artifact_sha256": task_prompt_digest,
            }
        )
    events.append(
        {
            "sequence": 6,
            "event": "round1-sealed",
            "task_id": None,
            "actor_id": "main-thread",
            "lease_id": None,
            "candidate_id": None,
            "artifact_sha256": board_digest,
        }
    )
    if peer_message:
        events.extend(
            {
                "sequence": index + 7,
                "event": "peer-message",
                "task_id": None,
                "actor_id": identity,
                "lease_id": None,
                "candidate_id": None,
                "artifact_sha256": board_digest,
            }
            for index, identity in enumerate(participants)
        )
    else:
        events.append(
            {
                "sequence": 7,
                "event": "peer-board-relayed",
                "task_id": None,
                "actor_id": "main-thread",
                "lease_id": None,
                "candidate_id": None,
                "artifact_sha256": board_digest,
            }
        )
    events.extend(
        {
            "sequence": terminal_start + index,
            "event": "lease-completed",
            "task_id": f"task-{index + 1}",
            "actor_id": identity,
            "lease_id": f"lease-{index + 1}",
            "candidate_id": None,
            "artifact_sha256": None,
        }
        for index, identity in enumerate(participants)
    )
    fixture.execution = {
        "version": 2,
        "packet_sha256": fixture.packet["packet_sha256"],
        "controller_ref": fixture.envelope["controller_ref"],
        "orchestration_envelope_sha256": _json_digest(fixture.envelope),
        "task_graph_sha256": _json_digest(fixture.plan),
        "deliberation_sha256": deliberation_digest,
        "actors": [
            {
                "id": "main-thread",
                "parent_id": None,
                "kind": "main-integrator",
                "task_ids": [],
                "workspace_ref": None,
            },
            *[
                {
                    "id": identity,
                    "parent_id": "main-thread",
                    "kind": "analysis-worker",
                    "task_ids": [f"task-{index + 1}"],
                    "workspace_ref": None,
                }
                for index, identity in enumerate(participants)
            ],
        ],
        "leases": leases,
        "candidates": [],
        "integrations": [],
        "events": events,
        "complete_event_capture": True,
        "orphan_processes_detected": False,
        "canonical_pre_acceptance": {
            "repository_ref": fixture.baseline["repository_ref"],
            "baseline_state_sha256": baseline_digest,
            "final_state_sha256": baseline_digest,
            "diff_sha256": diff_digest,
            "changed_paths": [],
            "integrator_id": "main-thread",
            "non_integrator_writes_detected": False,
        },
        "resource_usage": {
            "tokens": 200,
            "tool_calls": 4,
            "process_seconds": 2,
            "artifact_bytes": 0,
            "peak_concurrency": 2,
        },
        "policy_violations": [],
    }
    fixture.verification["packet_sha256"] = fixture.packet["packet_sha256"]
    fixture.verification["orchestration_envelope_sha256"] = _json_digest(fixture.envelope)
    fixture.verification["execution_receipt_sha256"] = _json_digest(fixture.execution)
    fixture.verification["final_state_sha256"] = baseline_digest
    fixture.verification["diff_sha256"] = diff_digest
    fixture.report = shared_report
    fixture.report["orchestration"] = {
        "envelope_sha256": _json_digest(fixture.envelope),
        "execution_receipt_sha256": _json_digest(fixture.execution),
        "verification_receipt_sha256": _json_digest(fixture.verification),
        "candidates": [],
    }
    fixture.write_all()
    return fixture


def make_shared_dependency_fixture(root: Path) -> Fixture:
    """Build a root-relay shared chain whose second task depends on the first."""

    fixture = make_shared_fixture(root)
    fixture.plan["tasks"][1]["dependencies"] = ["task-1"]
    board_digest = fixture.report["deliberation"]["peer_board_sha256"]
    first_lease, second_lease = fixture.execution["leases"]
    first_lease["grant_sequence"] = 4
    first_lease["terminal_sequence"] = 5
    second_lease["grant_sequence"] = 6
    second_lease["terminal_sequence"] = 9
    fixture.execution["events"] = [
        receipt_event(1, "envelope-sealed", actor_id="main-thread"),
        receipt_event(2, "actor-spawned", actor_id="agent-1"),
        receipt_event(3, "actor-spawned", actor_id="agent-2"),
        receipt_event(
            4,
            "lease-granted",
            task_id="task-1",
            actor_id="agent-1",
            lease_id="lease-1",
        ),
        receipt_event(
            5,
            "lease-completed",
            task_id="task-1",
            actor_id="agent-1",
            lease_id="lease-1",
        ),
        receipt_event(
            6,
            "lease-granted",
            task_id="task-2",
            actor_id="agent-2",
            lease_id="lease-2",
        ),
        receipt_event(
            7,
            "round1-sealed",
            actor_id="main-thread",
            artifact_sha256=board_digest,
        ),
        receipt_event(
            8,
            "peer-board-relayed",
            actor_id="main-thread",
            artifact_sha256=board_digest,
        ),
        receipt_event(
            9,
            "lease-completed",
            task_id="task-2",
            actor_id="agent-2",
            lease_id="lease-2",
        ),
    ]
    fixture.execution["resource_usage"]["peak_concurrency"] = 1
    rebind_fixture_chain(fixture)
    return fixture


def call_gate(fixture: Fixture, argv: list[str] | None = None) -> tuple[int, dict[str, Any]]:
    output = io.StringIO()
    with redirect_stdout(output):
        status = gate_main(argv or fixture.gate_argv())
    try:
        payload = json.loads(output.getvalue())
    except json.JSONDecodeError:
        payload = {"passed": False, "errors": [output.getvalue()]}
    return status, payload


def clone(value: Any) -> Any:
    return copy.deepcopy(value)


def refresh_receipt_chain(fixture: Fixture) -> None:
    deliberation = fixture.report.get("deliberation")
    fixture.execution["deliberation_sha256"] = (
        _json_digest(deliberation) if isinstance(deliberation, dict) else None
    )
    fixture.verification["execution_receipt_sha256"] = _json_digest(fixture.execution)
    fixture.report["orchestration"]["execution_receipt_sha256"] = _json_digest(
        fixture.execution
    )
    fixture.report["orchestration"]["verification_receipt_sha256"] = _json_digest(
        fixture.verification
    )
    fixture.write_all()


def receipt_event(
    sequence: int,
    event: str,
    *,
    task_id: str | None = None,
    actor_id: str | None = None,
    lease_id: str | None = None,
    candidate_id: str | None = None,
    artifact_sha256: str | None = None,
) -> dict[str, Any]:
    return {
        "sequence": sequence,
        "event": event,
        "task_id": task_id,
        "actor_id": actor_id,
        "lease_id": lease_id,
        "candidate_id": candidate_id,
        "artifact_sha256": artifact_sha256,
    }


def rebind_fixture_chain(fixture: Fixture) -> None:
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
    deliberation = fixture.report.get("deliberation")
    fixture.execution["deliberation_sha256"] = (
        _json_digest(deliberation) if isinstance(deliberation, dict) else None
    )
    tasks = {task["id"]: task for task in fixture.plan["tasks"]}
    for event in fixture.execution["events"]:
        if event["event"] in {"envelope-sealed", "actor-spawned"}:
            event["artifact_sha256"] = envelope_digest
    for lease in fixture.execution["leases"]:
        prompt, _, _ = build_task_prompt_v5(
            fixture.packet,
            fixture.plan,
            tasks[lease["task_id"]],
            lease["actor_id"],
            envelope_digest,
        )
        prompt_digest = hashlib.sha256(prompt.encode("utf-8")).hexdigest()
        lease["task_prompt_sha256"] = prompt_digest
        for event in fixture.execution["events"]:
            if event["event"] == "lease-granted" and event["lease_id"] == lease["id"]:
                event["artifact_sha256"] = prompt_digest

    execution_digest = _json_digest(fixture.execution)
    fixture.verification["packet_sha256"] = fixture.packet["packet_sha256"]
    fixture.verification["controller_ref"] = fixture.envelope["controller_ref"]
    fixture.verification["orchestration_envelope_sha256"] = envelope_digest
    fixture.verification["execution_receipt_sha256"] = execution_digest
    fixture.verification["verifier_bundle_sha256"] = verifier_bundle_sha256()
    report_candidates = [
        {
            field: integration[field]
            for field in (
                "task_id",
                "candidate_id",
                "bundle_sha256",
                "disposition",
                "reason",
            )
        }
        for integration in fixture.execution["integrations"]
    ]
    fixture.report["packet_sha256"] = fixture.packet["packet_sha256"]
    fixture.report["orchestration"] = {
        "envelope_sha256": envelope_digest,
        "execution_receipt_sha256": execution_digest,
        "verification_receipt_sha256": _json_digest(fixture.verification),
        "candidates": report_candidates,
    }
    fixture.write_all()


def rebind_final_repository_state(fixture: Fixture) -> None:
    final_manifest = build_state_manifest(fixture.repo)
    baseline_digest = state_manifest_sha256(fixture.baseline)
    final_digest = state_manifest_sha256(final_manifest)
    changed_paths = state_manifest_changed_paths(fixture.baseline, final_manifest)
    diff_digest = _diff_digest(
        final_manifest["repository_ref"],
        baseline_digest,
        final_digest,
        changed_paths,
    )
    fixture.execution["canonical_pre_acceptance"].update(
        repository_ref=final_manifest["repository_ref"],
        baseline_state_sha256=baseline_digest,
        final_state_sha256=final_digest,
        diff_sha256=diff_digest,
        changed_paths=changed_paths,
    )
    fixture.verification.update(
        repository_ref=final_manifest["repository_ref"],
        final_state_sha256=final_digest,
        diff_sha256=diff_digest,
    )
    fixture.report["implementation"]["changed_paths"] = changed_paths
    rebind_fixture_chain(fixture)


def make_two_candidate_fixture(
    root: Path,
    *,
    contract_paths: list[str],
    extra_sources: dict[str, str],
    apply_final: Callable[[Path], None],
    left_paths: list[str],
    right_paths: list[str],
    left_operation: str,
    right_operation: str,
) -> Fixture:
    fixture = make_isolated_fixture(
        root,
        contract_paths=contract_paths,
        extra_sources=extra_sources,
    )
    fixture.repo.joinpath("src", "example.py").write_text(
        "BASELINE = True\n", encoding="utf-8", newline="\n"
    )
    apply_final(fixture.repo)

    task1 = fixture.plan["tasks"][0]
    task1.update(
        objective=f"Exercise the {left_operation} candidate.",
        dependencies=[],
        read_paths=left_paths,
        candidate_write_paths=left_paths,
    )
    task2 = clone(task1)
    task2.update(
        id="task-2",
        objective=f"Exercise the {right_operation} candidate.",
        read_paths=right_paths,
        candidate_write_paths=right_paths,
    )
    fixture.plan["tasks"] = [task1, task2]
    assignment1 = fixture.plan["assignments"][0]
    assignment1.update(task_id="task-1", runtime_identity="worker-1")
    assignment2 = clone(assignment1)
    assignment2.update(task_id="task-2", runtime_identity="worker-2")
    fixture.plan["assignments"] = [assignment1, assignment2]

    bundle1 = fixture.paths["candidate_bundle"]
    bundle2 = root / "candidate-2.bundle"
    for bundle, operation, paths in (
        (bundle1, left_operation, left_paths),
        (bundle2, right_operation, right_paths),
    ):
        bundle.write_text(
            json.dumps(
                {"operation": operation, "paths": paths},
                sort_keys=True,
                separators=(",", ":"),
            )
            + "\n",
            encoding="utf-8",
            newline="\n",
        )
    bundle1_digest = hashlib.sha256(bundle1.read_bytes()).hexdigest()
    bundle2_digest = hashlib.sha256(bundle2.read_bytes()).hexdigest()
    workspace1 = Path(fixture.execution["actors"][1]["workspace_ref"])
    workspace2 = root / "candidate-workspace-2"
    workspace2.mkdir()

    fixture.execution["actors"] = [
        fixture.execution["actors"][0],
        {
            "id": "worker-1",
            "parent_id": "main-thread",
            "kind": "candidate-worker",
            "task_ids": ["task-1"],
            "workspace_ref": str(workspace1.resolve()),
        },
        {
            "id": "worker-2",
            "parent_id": "main-thread",
            "kind": "candidate-worker",
            "task_ids": ["task-2"],
            "workspace_ref": str(workspace2.resolve()),
        },
    ]
    lease1 = fixture.execution["leases"][0]
    lease1.update(
        id="lease-1",
        task_id="task-1",
        actor_id="worker-1",
        grant_sequence=3,
        terminal_sequence=5,
        read_paths=left_paths,
        candidate_write_paths=left_paths,
    )
    lease2 = clone(lease1)
    lease2.update(
        id="lease-2",
        task_id="task-2",
        actor_id="worker-2",
        grant_sequence=9,
        terminal_sequence=11,
        read_paths=right_paths,
        candidate_write_paths=right_paths,
    )
    fixture.execution["leases"] = [lease1, lease2]

    candidate1 = fixture.execution["candidates"][0]
    candidate1.update(
        id="candidate-1",
        task_id="task-1",
        actor_id="worker-1",
        lease_id="lease-1",
        workspace_ref=str(workspace1.resolve()),
        bundle_ref=str(bundle1.resolve()),
        bundle_sha256=bundle1_digest,
        changed_paths=left_paths,
    )
    candidate2 = clone(candidate1)
    candidate2.update(
        id="candidate-2",
        task_id="task-2",
        actor_id="worker-2",
        lease_id="lease-2",
        workspace_ref=str(workspace2.resolve()),
        bundle_ref=str(bundle2.resolve()),
        bundle_sha256=bundle2_digest,
        changed_paths=right_paths,
        local_checks_sha256="2" * 64,
    )
    fixture.execution["candidates"] = [candidate1, candidate2]
    integration1 = fixture.execution["integrations"][0]
    integration1.update(
        task_id="task-1",
        candidate_id="candidate-1",
        bundle_sha256=bundle1_digest,
        reason="Select the first scoped candidate.",
    )
    integration2 = clone(integration1)
    integration2.update(
        task_id="task-2",
        candidate_id="candidate-2",
        bundle_sha256=bundle2_digest,
        reason="Select the second scoped candidate.",
    )
    fixture.execution["integrations"] = [integration1, integration2]

    placeholder = "0" * 64
    fixture.execution["events"] = [
        receipt_event(1, "envelope-sealed", actor_id="main-thread", artifact_sha256=placeholder),
        receipt_event(2, "actor-spawned", actor_id="worker-1", artifact_sha256=placeholder),
        receipt_event(3, "lease-granted", task_id="task-1", actor_id="worker-1", lease_id="lease-1", artifact_sha256=placeholder),
        receipt_event(4, "candidate-produced", task_id="task-1", actor_id="worker-1", lease_id="lease-1", candidate_id="candidate-1", artifact_sha256=bundle1_digest),
        receipt_event(5, "lease-completed", task_id="task-1", actor_id="worker-1", lease_id="lease-1"),
        receipt_event(6, "candidate-selected", task_id="task-1", actor_id="main-thread", lease_id="lease-1", candidate_id="candidate-1", artifact_sha256=bundle1_digest),
        receipt_event(7, "integration-completed", task_id="task-1", actor_id="main-thread", lease_id="lease-1", candidate_id="candidate-1", artifact_sha256=bundle1_digest),
        receipt_event(8, "actor-spawned", actor_id="worker-2", artifact_sha256=placeholder),
        receipt_event(9, "lease-granted", task_id="task-2", actor_id="worker-2", lease_id="lease-2", artifact_sha256=placeholder),
        receipt_event(10, "candidate-produced", task_id="task-2", actor_id="worker-2", lease_id="lease-2", candidate_id="candidate-2", artifact_sha256=bundle2_digest),
        receipt_event(11, "lease-completed", task_id="task-2", actor_id="worker-2", lease_id="lease-2"),
        receipt_event(12, "candidate-selected", task_id="task-2", actor_id="main-thread", lease_id="lease-2", candidate_id="candidate-2", artifact_sha256=bundle2_digest),
        receipt_event(13, "integration-completed", task_id="task-2", actor_id="main-thread", lease_id="lease-2", candidate_id="candidate-2", artifact_sha256=bundle2_digest),
    ]

    final_manifest = build_state_manifest(fixture.repo)
    baseline_digest = state_manifest_sha256(fixture.baseline)
    final_digest = state_manifest_sha256(final_manifest)
    changed_paths = state_manifest_changed_paths(fixture.baseline, final_manifest)
    diff_digest = _diff_digest(
        final_manifest["repository_ref"], baseline_digest, final_digest, changed_paths
    )
    fixture.execution["canonical_pre_acceptance"] = {
        "repository_ref": final_manifest["repository_ref"],
        "baseline_state_sha256": baseline_digest,
        "final_state_sha256": final_digest,
        "diff_sha256": diff_digest,
        "changed_paths": changed_paths,
        "integrator_id": "main-thread",
        "non_integrator_writes_detected": False,
    }
    fixture.execution["resource_usage"].update(
        tokens=400,
        tool_calls=10,
        process_seconds=4,
        artifact_bytes=bundle1.stat().st_size + bundle2.stat().st_size,
        peak_concurrency=1,
    )
    fixture.verification.update(
        repository_ref=final_manifest["repository_ref"],
        final_state_sha256=final_digest,
        diff_sha256=diff_digest,
    )
    fixture.report["implementation"]["changed_paths"] = changed_paths
    rebind_fixture_chain(fixture)
    return fixture


def run_cases() -> list[dict[str, Any]]:
    results: list[dict[str, Any]] = []

    def record(name: str, passed: bool, detail: str = "") -> None:
        results.append({"name": name, "passed": passed, "detail": detail})

    with tempfile.TemporaryDirectory(prefix="wide-lens-v5-eval-") as temporary:
        fixture = make_fixture(Path(temporary))
        status, payload = call_gate(fixture)
        record("valid complete v5 chain passes", status == 0 and payload.get("passed") is True, str(payload.get("errors", []))[:500])

        capture_root = Path(temporary) / "capture-hardlink"
        capture_repo = capture_root / "repo"
        capture_repo.mkdir(parents=True)
        capture_left = capture_repo / "left.txt"
        capture_right = capture_repo / "right.txt"
        capture_left.write_text("same\n", encoding="utf-8", newline="\n")
        os.link(capture_left, capture_right)
        captured_baseline = capture_root / "baseline.json"
        capture_output = io.StringIO()
        with redirect_stdout(capture_output):
            capture_status = gate_main(
                [
                    "--repo-root",
                    str(capture_repo),
                    "--baseline-manifest",
                    str(captured_baseline),
                    "--capture-baseline",
                ]
            )
        capture_payload = json.loads(capture_output.getvalue())
        record(
            "v5 baseline capture rejects canonical hard links before writing",
            capture_status != 0
            and captured_baseline.exists() is False
            and any(
                "target repository contains hard-linked files" in error
                for error in capture_payload.get("errors", [])
            ),
            str(capture_payload.get("errors", []))[:500],
        )

        frozen_hashes = {
            "scripts/diverge.py": "b34d33923f6750dd5e41bcb27da830956506ad962562b4cdf281e146571a8f47",
            "scripts/check_delivery.py": "ecd2a3754bf93371351d8c436e8c670d022210bc48ae9d644f05ebd35d784a2d",
            "references/lenses.json": "10668c9e1154a54ee753865a456b50fa92d79d24a7139e621ce6afe5f7aacb33",
        }
        for relative, expected_digest in frozen_hashes.items():
            observed = hashlib.sha256(SKILL_DIR.joinpath(relative).read_bytes()).hexdigest()
            record(f"v4 frozen bytes {relative}", observed == expected_digest, observed)
        record(
            "v4 verifier rejects packet v5",
            bool(validate_packet_v4(fixture.packet, fixture.packet["packet_sha256"])),
        )
        legacy_cli = subprocess.run(
            [
                sys.executable,
                "-B",
                str(SKILL_DIR / "scripts" / "diverge.py"),
                "--contract",
                str(fixture.paths["packet"]),
                "--execution-mode",
                "isolated-candidates",
            ],
            check=False,
            capture_output=True,
            text=True,
        )
        record("v4 planner rejects v5-only CLI parameter", legacy_cli.returncode == 2)

        strict_inputs = {
            "duplicate-key": '{"a":1,"a":2}',
            "nan": '{"value":NaN}',
            "deep": "[" * 130 + "0" + "]" * 130,
        }
        for name, payload_text in strict_inputs.items():
            path = Path(temporary) / f"strict-{name}.json"
            path.write_text(payload_text, encoding="utf-8")
            try:
                load_json(path)
            except (GateError, RecursionError, ValueError):
                rejected = True
            else:
                rejected = False
            record(f"strict JSON rejects {name}", rejected)
        invalid_utf8 = Path(temporary) / "strict-invalid-utf8.json"
        invalid_utf8.write_bytes(b"{\"x\":\xff}")
        try:
            load_json(invalid_utf8)
        except (GateError, UnicodeError, ValueError):
            rejected = True
        else:
            rejected = False
        record("strict JSON rejects invalid Unicode", rejected)
        for name, path_value, flavor, expected_value in (
            ("Windows reserved alias", "CON", "windows-win32", None),
            ("Windows ADS", "src/file.py:secret", "windows-win32", None),
            ("Windows traversal", "..\\outside.py", "windows-win32", None),
            ("Unicode POSIX path", "源/文件.py", "posix", "源/文件.py"),
        ):
            record(f"path normalization {name}", repo_path(path_value, flavor) == expected_value)

        record("packet v5 validates", not validate_packet_v5(fixture.packet), str(validate_packet_v5(fixture.packet)))
        record("packet v5 policy exact", fixture.packet["orchestration_policy"] == PACKET_V5_POLICY)
        record("packet v5 digest valid", fixture.packet["packet_sha256"] == packet_sha256(fixture.packet))
        invalid_packet = clone(fixture.packet)
        invalid_packet["version"] = True
        record("packet rejects bool version", bool(validate_packet_v5(invalid_packet)))
        invalid_packet = clone(fixture.packet)
        invalid_packet["orchestration_policy"]["recursive_delegation"] = True
        invalid_packet["packet_sha256"] = packet_sha256(invalid_packet)
        record("packet rejects recursive delegation", bool(validate_packet_v5(invalid_packet)))
        invalid_packet = clone(fixture.packet)
        invalid_packet["independence"]["single_editing_owner"] = 1
        invalid_packet["packet_sha256"] = packet_sha256(invalid_packet)
        record(
            "packet rejects integer substituted for frozen boolean",
            bool(validate_packet_v5(invalid_packet)),
        )
        invalid_packet = build_packet_v5(
            fixture.packet["contract"], risk="medium", coordination="shared"
        )
        invalid_packet["discussion"]["budget"]["max_retries_per_participant"] = True
        invalid_packet["packet_sha256"] = packet_sha256(invalid_packet)
        record(
            "packet rejects boolean substituted for frozen integer",
            bool(validate_packet_v5(invalid_packet)),
        )
        invalid_packet = clone(fixture.packet)
        invalid_packet["extra"] = "smuggled"
        invalid_packet["packet_sha256"] = packet_sha256(invalid_packet)
        record("packet rejects unknown field", bool(validate_packet_v5(invalid_packet)))

        def amended_contract(prior_packet: dict[str, Any], approval_id: str) -> dict[str, Any]:
            contract = clone(prior_packet["contract"])
            contract["revision"] += 1
            contract["authorities"].append(
                {
                    "id": approval_id,
                    "kind": "user-approval",
                    "locator": f"controller://approval/{approval_id}",
                    "content": "Approved the exact frozen contract revision.",
                }
            )
            contract["supersedes"] = {
                "packet_sha256": prior_packet["packet_sha256"],
                "reason": "Exercise externally approved packet lineage.",
                "approval_ref": approval_id,
            }
            return bind_authority_grants(contract)

        lineage_contract = valid_contract(
            "Exercise packet lineage", ["src/example.py"], "change", "lineage-eval"
        )
        prior_v4 = build_packet(lineage_contract, risk="medium", coordination="independent")
        v4_to_v5 = build_packet_v5(
            amended_contract(prior_v4, "SRC-V4-V5"),
            risk="medium",
            coordination="independent",
        )
        v4_lineage_errors = validate_packet_lineage(
            v4_to_v5, prior_v4, prior_v4["packet_sha256"]
        )
        record(
            "packet lineage accepts externally anchored v4-to-v5 amendment",
            not v4_lineage_errors,
            str(v4_lineage_errors),
        )
        prior_v5 = build_packet_v5(
            lineage_contract, risk="medium", coordination="independent"
        )
        v5_to_v5 = build_packet_v5(
            amended_contract(prior_v5, "SRC-V5-V5"),
            risk="medium",
            coordination="independent",
        )
        v5_lineage_errors = validate_packet_lineage(
            v5_to_v5, prior_v5, prior_v5["packet_sha256"]
        )
        record(
            "packet lineage accepts externally anchored v5-to-v5 amendment",
            not v5_lineage_errors,
            str(v5_lineage_errors),
        )
        replayed_prior = clone(prior_v5)
        replayed_prior["packet_sha256"] = "0" * 64
        record(
            "packet lineage rejects predecessor digest replay",
            bool(
                validate_packet_lineage(
                    v5_to_v5, replayed_prior, prior_v5["packet_sha256"]
                )
            ),
        )

        partial = normalize_host_capabilities({"spawn": True})
        record(
            "unknown capabilities normalize false",
            partial["capabilities"]["spawn"] is True
            and all(partial["capabilities"][name] is False for name in CAPABILITY_NAMES if name != "spawn"),
        )
        record("anchored host capabilities exact", not validate_host_capabilities(fixture.capabilities))
        bad_caps = clone(fixture.capabilities)
        bad_caps["capabilities"]["spawn"] = 1
        record("host capability rejects integer boolean", bool(validate_host_capabilities(bad_caps)))
        bad_caps = clone(fixture.capabilities)
        bad_caps["capabilities"]["unknown"] = False
        record("host capability rejects unknown key", bool(validate_host_capabilities(bad_caps)))

        plan_errors = validate_coordination_plan(fixture.packet, fixture.capabilities, fixture.plan)
        record("valid dynamic task DAG passes", not plan_errors, str(plan_errors))
        delegation = build_runtime_delegation_v5(
            fixture.packet,
            fixture.capabilities,
            fixture.plan,
            orchestration_envelope_sha256=_json_digest(fixture.envelope),
        )
        record(
            "runtime delegation leaves count to active model",
            delegation.get("selected_by") == "active-main-model"
            and "participant_count" not in json.dumps(delegation),
        )
        assignment_prompt = delegation["assignments"][0]["prompt"]
        record(
            "runtime delegation binds exact prompt to the sealed envelope",
            delegation["assignments"][0]["prompt_sha256"]
            == hashlib.sha256(assignment_prompt.encode("utf-8")).hexdigest()
            and fixture.execution["leases"][0]["task_prompt_sha256"]
            == delegation["assignments"][0]["prompt_sha256"],
        )
        planner_base = [
            sys.executable,
            "-B",
            str(SKILL_DIR / "scripts" / "diverge_v5.py"),
            "--packet",
            str(fixture.paths["packet"]),
            "--host-capabilities",
            str(fixture.paths["capabilities"]),
            "--coordination-plan",
            str(fixture.paths["plan"]),
            "--resource-envelope",
            str(fixture.paths["resources"]),
            "--sandbox-profile",
            str(fixture.paths["sandbox"]),
            "--orchestration-envelope",
            str(fixture.paths["envelope"]),
            "--expect-packet-sha256",
            fixture.packet["packet_sha256"],
            "--expect-orchestration-envelope-sha256",
            _json_digest(fixture.envelope),
        ]
        planner_ok = subprocess.run(
            planner_base, check=False, capture_output=True, text=True
        )
        record(
            "runtime planner emits prompts only after all pre-spawn anchors validate",
            planner_ok.returncode == 0,
            planner_ok.stderr[:500],
        )
        unanchored = planner_base[:]
        anchor_index = unanchored.index("--expect-orchestration-envelope-sha256")
        del unanchored[anchor_index : anchor_index + 2]
        planner_rejected = subprocess.run(
            unanchored, check=False, capture_output=True, text=True
        )
        record(
            "runtime planner refuses an unanchored orchestration envelope",
            planner_rejected.returncode != 0,
        )
        revised_plan = clone(fixture.plan)
        revised_plan["revision"] = 1
        revised_plan["supersedes_sha256"] = sha256_json(fixture.plan)
        task2 = clone(revised_plan["tasks"][0])
        task2["id"] = "task-2"
        task2["dependencies"] = ["task-1"]
        revised_plan["tasks"].append(task2)
        assignment2 = clone(revised_plan["assignments"][0])
        assignment2["task_id"] = "task-2"
        revised_plan["assignments"].append(assignment2)
        revision_errors = validate_coordination_plan(
            fixture.packet, fixture.capabilities, revised_plan, fixture.plan
        )
        record(
            "DAG revision appends a node without prescribing another agent",
            not revision_errors
            and len(revised_plan["tasks"]) == 2
            and len({item["runtime_identity"] for item in revised_plan["assignments"]}) == 1,
            str(revision_errors),
        )
        rewritten_revision = clone(revised_plan)
        rewritten_revision["tasks"][0]["objective"] = "Rewritten authority"
        record(
            "DAG revision cannot rewrite a prior node",
            bool(
                validate_coordination_plan(
                    fixture.packet,
                    fixture.capabilities,
                    rewritten_revision,
                    fixture.plan,
                )
            ),
        )
        reassigned_revision = clone(revised_plan)
        reassigned_revision["assignments"][0]["runtime_identity"] = "different-worker"
        record(
            "DAG revision cannot rewrite a prior assignment",
            any(
                "not rewrite prior assignments" in error
                for error in validate_coordination_plan(
                    fixture.packet,
                    fixture.capabilities,
                    reassigned_revision,
                    fixture.plan,
                )
            ),
        )

        prior_envelope_path = fixture.root / "prior-envelope.json"
        revised_plan_path = fixture.root / "revised-plan.json"
        revised_envelope_path = fixture.root / "revised-envelope.json"
        write_json(prior_envelope_path, fixture.envelope)
        write_json(revised_plan_path, revised_plan)
        revised_envelope = clone(fixture.envelope)
        revised_envelope.update(
            {
                "task_graph_ref": str(revised_plan_path.resolve()),
                "task_graph_sha256": _json_digest(revised_plan),
                "previous_envelope_sha256": _json_digest(fixture.envelope),
                "predecessor_execution_started": False,
            }
        )
        write_json(revised_envelope_path, revised_envelope)
        revision_planner = [
            sys.executable,
            "-B",
            str(SKILL_DIR / "scripts" / "diverge_v5.py"),
            "--packet",
            str(fixture.paths["packet"]),
            "--host-capabilities",
            str(fixture.paths["capabilities"]),
            "--coordination-plan",
            str(revised_plan_path),
            "--prior-coordination-plan",
            str(fixture.paths["plan"]),
            "--resource-envelope",
            str(fixture.paths["resources"]),
            "--sandbox-profile",
            str(fixture.paths["sandbox"]),
            "--orchestration-envelope",
            str(revised_envelope_path),
            "--previous-orchestration-envelope",
            str(prior_envelope_path),
            "--expect-packet-sha256",
            fixture.packet["packet_sha256"],
            "--expect-orchestration-envelope-sha256",
            _json_digest(revised_envelope),
            "--expect-previous-orchestration-envelope-sha256",
            _json_digest(fixture.envelope),
        ]
        valid_revision = subprocess.run(
            revision_planner, check=False, capture_output=True, text=True
        )
        record(
            "pre-spawn DAG revision validates complete predecessor lineage",
            valid_revision.returncode == 0,
            valid_revision.stdout + valid_revision.stderr,
        )

        started_envelope = clone(revised_envelope)
        started_envelope["predecessor_execution_started"] = True
        write_json(revised_envelope_path, started_envelope)
        started_args = revision_planner[:]
        started_args[
            started_args.index("--expect-orchestration-envelope-sha256") + 1
        ] = _json_digest(started_envelope)
        started_revision = subprocess.run(
            started_args, check=False, capture_output=True, text=True
        )
        record(
            "assured DAG revision after predecessor execution is rejected",
            started_revision.returncode != 0
            and "before the predecessor starts execution" in started_revision.stderr,
            started_revision.stdout + started_revision.stderr,
        )
        write_json(revised_envelope_path, revised_envelope)

        malformed_previous = clone(fixture.envelope)
        malformed_previous["version"] = True
        write_json(prior_envelope_path, malformed_previous)
        malformed_current = clone(revised_envelope)
        malformed_current["previous_envelope_sha256"] = _json_digest(malformed_previous)
        write_json(revised_envelope_path, malformed_current)
        malformed_args = revision_planner[:]
        malformed_args[
            malformed_args.index("--expect-orchestration-envelope-sha256") + 1
        ] = _json_digest(malformed_current)
        malformed_args[
            malformed_args.index("--expect-previous-orchestration-envelope-sha256") + 1
        ] = _json_digest(malformed_previous)
        malformed_result = subprocess.run(
            malformed_args, check=False, capture_output=True, text=True
        )
        record(
            "pre-spawn planner rejects malformed predecessor envelope",
            malformed_result.returncode != 0
            and "version must be integer 1" in malformed_result.stderr,
            malformed_result.stdout + malformed_result.stderr,
        )
        write_json(prior_envelope_path, fixture.envelope)
        write_json(revised_envelope_path, revised_envelope)

        expanded_capabilities = clone(fixture.capabilities)
        expanded_capabilities["capabilities"]["atomic_task_claim"] = True
        expanded_capabilities_path = fixture.root / "expanded-capabilities.json"
        write_json(expanded_capabilities_path, expanded_capabilities)
        expanded_envelope = clone(revised_envelope)
        expanded_envelope["host_capabilities_ref"] = str(
            expanded_capabilities_path.resolve()
        )
        expanded_envelope["host_capabilities_sha256"] = _json_digest(
            expanded_capabilities
        )
        write_json(revised_envelope_path, expanded_envelope)
        expanded_args = revision_planner[:]
        expanded_args[expanded_args.index("--host-capabilities") + 1] = str(
            expanded_capabilities_path
        )
        expanded_args[
            expanded_args.index("--expect-orchestration-envelope-sha256") + 1
        ] = _json_digest(expanded_envelope)
        expanded_result = subprocess.run(
            expanded_args, check=False, capture_output=True, text=True
        )
        record(
            "pre-spawn planner rejects capability expansion across revisions",
            expanded_result.returncode != 0
            and "expands the host capability ceiling" in expanded_result.stderr,
            expanded_result.stdout + expanded_result.stderr,
        )
        write_json(revised_envelope_path, revised_envelope)

        packet_link = fixture.root / "packet-link.json"
        packet_link.symlink_to(fixture.paths["packet"])
        linked_args = planner_base[:]
        linked_args[linked_args.index("--packet") + 1] = str(packet_link)
        linked_result = subprocess.run(
            linked_args, check=False, capture_output=True, text=True
        )
        record(
            "pre-spawn planner rejects linked JSON artifacts",
            linked_result.returncode != 0
            and "link or reparse" in linked_result.stderr,
            linked_result.stdout + linked_result.stderr,
        )

        revised_gate = make_fixture(Path(temporary) / "revised-full-gate")
        prior_gate_plan = clone(revised_gate.plan)
        prior_gate_plan_path = revised_gate.root / "prior-plan.json"
        write_json(prior_gate_plan_path, prior_gate_plan)
        prior_gate_envelope = clone(revised_gate.envelope)
        prior_gate_envelope["task_graph_ref"] = str(prior_gate_plan_path.resolve())
        prior_gate_envelope["task_graph_sha256"] = _json_digest(prior_gate_plan)
        prior_gate_envelope_path = revised_gate.root / "prior-envelope.json"
        write_json(prior_gate_envelope_path, prior_gate_envelope)

        revised_gate.plan["revision"] = 1
        revised_gate.plan["supersedes_sha256"] = _json_digest(prior_gate_plan)
        appended_task = clone(revised_gate.plan["tasks"][0])
        appended_task.update(
            {
                "id": "task-2",
                "objective": "Recheck the frozen state after task-1 completes.",
                "dependencies": ["task-1"],
            }
        )
        revised_gate.plan["tasks"].append(appended_task)
        appended_assignment = clone(revised_gate.plan["assignments"][0])
        appended_assignment["task_id"] = "task-2"
        revised_gate.plan["assignments"].append(appended_assignment)
        revised_gate.envelope.update(
            {
                "task_graph_sha256": _json_digest(revised_gate.plan),
                "previous_envelope_sha256": _json_digest(prior_gate_envelope),
                "predecessor_execution_started": False,
            }
        )
        revised_envelope_digest = _json_digest(revised_gate.envelope)
        revised_gate.execution["orchestration_envelope_sha256"] = revised_envelope_digest
        revised_gate.execution["task_graph_sha256"] = _json_digest(revised_gate.plan)
        revised_gate.execution["actors"][1]["task_ids"] = ["task-1", "task-2"]
        revised_gate.execution["leases"] = []
        revised_gate.execution["events"] = [
            {
                "sequence": 1,
                "event": "envelope-sealed",
                "task_id": None,
                "actor_id": "main-thread",
                "lease_id": None,
                "candidate_id": None,
                "artifact_sha256": revised_envelope_digest,
            },
            {
                "sequence": 2,
                "event": "actor-spawned",
                "task_id": None,
                "actor_id": "worker-1",
                "lease_id": None,
                "candidate_id": None,
                "artifact_sha256": revised_envelope_digest,
            },
        ]
        for index, task in enumerate(revised_gate.plan["tasks"]):
            lease_id = f"lease-{index + 1}"
            grant_sequence = 3 + index * 2
            terminal_sequence = grant_sequence + 1
            prompt, _, _ = build_task_prompt_v5(
                revised_gate.packet,
                revised_gate.plan,
                task,
                "worker-1",
                revised_envelope_digest,
            )
            prompt_digest = hashlib.sha256(prompt.encode("utf-8")).hexdigest()
            revised_gate.execution["leases"].append(
                {
                    "id": lease_id,
                    "task_id": task["id"],
                    "actor_id": "worker-1",
                    "grant_sequence": grant_sequence,
                    "terminal_sequence": terminal_sequence,
                    "state": "completed",
                    "task_prompt_sha256": prompt_digest,
                    "capabilities": ["enforced_readonly"],
                    "read_paths": task["read_paths"],
                    "candidate_write_paths": task["candidate_write_paths"],
                    "acceptance_ids": task["acceptance_ids"],
                }
            )
            revised_gate.execution["events"].extend(
                [
                    {
                        "sequence": grant_sequence,
                        "event": "lease-granted",
                        "task_id": task["id"],
                        "actor_id": "worker-1",
                        "lease_id": lease_id,
                        "candidate_id": None,
                        "artifact_sha256": prompt_digest,
                    },
                    {
                        "sequence": terminal_sequence,
                        "event": "lease-completed",
                        "task_id": task["id"],
                        "actor_id": "worker-1",
                        "lease_id": lease_id,
                        "candidate_id": None,
                        "artifact_sha256": None,
                    },
                ]
            )
        revised_gate.execution["resource_usage"]["tool_calls"] = 4
        revised_gate.execution["resource_usage"]["process_seconds"] = 2
        revised_gate.verification["orchestration_envelope_sha256"] = revised_envelope_digest
        revised_gate.report["orchestration"]["envelope_sha256"] = revised_envelope_digest
        refresh_receipt_chain(revised_gate)
        revised_gate_args = [
            *revised_gate.gate_argv(),
            "--prior-coordination-plan",
            str(prior_gate_plan_path),
            "--expect-prior-coordination-plan-sha256",
            _json_digest(prior_gate_plan),
            "--previous-orchestration-envelope",
            str(prior_gate_envelope_path),
            "--expect-previous-orchestration-envelope-sha256",
            _json_digest(prior_gate_envelope),
        ]
        revised_status, revised_payload = call_gate(revised_gate, revised_gate_args)
        record(
            "full gate accepts a pre-dispatch v5-to-v5 DAG revision",
            revised_status == 0 and revised_payload.get("passed") is True,
            str(revised_payload.get("errors", []))[:500],
        )
        replay_args = revised_gate_args[:]
        replay_args[
            replay_args.index("--expect-previous-orchestration-envelope-sha256") + 1
        ] = "0" * 64
        replay_status, replay_payload = call_gate(revised_gate, replay_args)
        record(
            "full gate rejects predecessor envelope digest replay",
            replay_status != 0 and replay_payload.get("passed") is False,
            str(replay_payload.get("errors", []))[:500],
        )
        for name, mutation in (
            ("DAG rejects duplicate id", lambda plan: plan["tasks"].append(clone(plan["tasks"][0]))),
            ("DAG rejects unknown dependency", lambda plan: plan["tasks"][0]["dependencies"].append("missing")),
            ("DAG rejects read scope expansion", lambda plan: plan["tasks"][0]["read_paths"].__setitem__(0, "outside")),
            ("DAG rejects write scope expansion", lambda plan: plan["tasks"][0]["candidate_write_paths"].__setitem__(0, "outside")),
            ("DAG rejects acceptance expansion", lambda plan: plan["tasks"][0]["acceptance_ids"].append("AC-X")),
            ("DAG rejects duplicate assignment", lambda plan: plan["assignments"].append(clone(plan["assignments"][0]))),
        ):
            value = clone(fixture.plan)
            mutation(value)
            record(name, bool(validate_coordination_plan(fixture.packet, fixture.capabilities, value)))
        insensitive_contract = clone(fixture.packet["contract"])
        insensitive_contract["scope"]["path_case"]["value"] = "insensitive"
        insensitive_packet = build_packet_v5(
            bind_authority_grants(insensitive_contract),
            risk="medium",
            coordination="independent",
        )
        insensitive_plan = clone(fixture.plan)
        insensitive_plan["packet_sha256"] = insensitive_packet["packet_sha256"]
        insensitive_plan["tasks"][0]["read_paths"] = [
            "src/example.py",
            "SRC/EXAMPLE.PY",
        ]
        record(
            "DAG rejects case-insensitive duplicate path aliases",
            any(
                "unique canonical paths" in error
                for error in validate_coordination_plan(
                    insensitive_packet, fixture.capabilities, insensitive_plan
                )
            ),
        )
        cycle = clone(fixture.plan)
        task2 = clone(cycle["tasks"][0])
        task2["id"] = "task-2"
        task2["dependencies"] = ["task-1"]
        cycle["tasks"][0]["dependencies"] = ["task-2"]
        assignment2 = clone(cycle["assignments"][0])
        assignment2["task_id"] = "task-2"
        cycle["tasks"].append(task2)
        cycle["assignments"].append(assignment2)
        record("DAG rejects cycles", any("cycle" in error for error in validate_coordination_plan(fixture.packet, fixture.capabilities, cycle)))
        atomic = clone(fixture.plan)
        atomic["dispatch"] = "atomic-claim"
        atomic["assignments"] = []
        record("atomic claim is capability gated", bool(validate_coordination_plan(fixture.packet, fixture.capabilities, atomic)))
        atomic_capabilities = clone(fixture.capabilities)
        atomic_capabilities["capabilities"]["atomic_task_claim"] = True
        record(
            "atomic claim plan validates only when the host exposes the capability",
            not validate_coordination_plan(fixture.packet, atomic_capabilities, atomic),
        )
        peer = clone(fixture.plan)
        peer["communication"] = "peer-message"
        record("peer messaging is capability gated", bool(validate_coordination_plan(fixture.packet, fixture.capabilities, peer)))
        shared_peer_dependency = make_shared_fixture(
            Path(temporary) / "shared-peer-dependent-plan", peer_message=True
        )
        shared_peer_dependency.plan["tasks"][1]["dependencies"] = ["task-1"]
        record(
            "peer-message rejects dependent shared rounds",
            any(
                "cannot represent dependent shared rounds" in error
                for error in validate_coordination_plan(
                    shared_peer_dependency.packet,
                    shared_peer_dependency.capabilities,
                    shared_peer_dependency.plan,
                )
            ),
        )
        no_readonly = clone(fixture.capabilities)
        no_readonly["capabilities"]["enforced_readonly"] = False
        record("assured read-only does not silently downgrade", bool(validate_coordination_plan(fixture.packet, no_readonly, fixture.plan)))
        isolated = clone(fixture.plan)
        isolated["execution"] = "isolated-candidates"
        isolated["tasks"][0]["output_contract"]["kind"] = "candidate-bundle"
        record("isolated candidate capability missing fails", bool(validate_coordination_plan(fixture.packet, fixture.capabilities, isolated)))

        record("resource envelope validates", not validate_resource_envelope(fixture.resources))
        bad_resource = clone(fixture.resources)
        bad_resource["limits"]["max_tokens"] = True
        record("resource envelope rejects bool limit", bool(validate_resource_envelope(bad_resource)))
        bad_resource = clone(fixture.resources)
        bad_resource["limits"]["unknown"] = 1
        record("resource envelope rejects unknown field", bool(validate_resource_envelope(bad_resource)))
        record("sandbox profile validates", not validate_sandbox_profile(fixture.sandbox))
        for field in (
            "candidate_network_access",
            "candidate_credential_access",
            "candidate_target_repository_mounted",
            "candidate_git_common_dir_mounted",
            "candidate_artifact_store_mounted",
            "verifier_write_access",
            "verifier_candidate_outputs_visible",
            "gate_network_access",
            "gate_credential_access",
        ):
            bad = clone(fixture.sandbox)
            bad[field] = True
            record(f"sandbox rejects {field}", bool(validate_sandbox_profile(bad)))
        bad = clone(fixture.sandbox)
        bad["orphan_detection"] = False
        record("sandbox requires orphan detection", bool(validate_sandbox_profile(bad)))

        envelope_errors = validate_orchestration_envelope(
            fixture.envelope,
            packet=fixture.packet,
            host_capabilities=fixture.capabilities,
            host_capabilities_path=fixture.paths["capabilities"],
            task_graph=fixture.plan,
            task_graph_path=fixture.paths["plan"],
            resource_envelope=fixture.resources,
            resource_envelope_path=fixture.paths["resources"],
            sandbox_profile=fixture.sandbox,
            sandbox_profile_path=fixture.paths["sandbox"],
        )
        record("orchestration envelope validates", not envelope_errors, str(envelope_errors))
        for field, replacement in (
            ("controller_ref", ""),
            ("sealed_before_first_spawn", False),
            ("narrowing_attested", False),
            ("host_capabilities_sha256", "0" * 64),
            ("task_graph_sha256", "0" * 64),
            ("resource_envelope_sha256", "0" * 64),
            ("sandbox_profile_sha256", "0" * 64),
        ):
            bad = clone(fixture.envelope)
            bad[field] = replacement
            errors = validate_orchestration_envelope(
                bad,
                packet=fixture.packet,
                host_capabilities=fixture.capabilities,
                host_capabilities_path=fixture.paths["capabilities"],
                task_graph=fixture.plan,
                task_graph_path=fixture.paths["plan"],
                resource_envelope=fixture.resources,
                resource_envelope_path=fixture.paths["resources"],
                sandbox_profile=fixture.sandbox,
                sandbox_profile_path=fixture.paths["sandbox"],
            )
            record(f"envelope rejects {field}", bool(errors))

        execution_errors, _ = validate_execution_receipt(
            fixture.execution,
            packet=fixture.packet,
            host_capabilities=fixture.capabilities,
            task_graph=fixture.plan,
            resource_envelope=fixture.resources,
            envelope_sha256=_json_digest(fixture.envelope),
            repository=fixture.repo,
            baseline_manifest=fixture.baseline,
            pre_acceptance_manifest=build_state_manifest(fixture.repo),
            protected_paths=fixture.paths.values(),
        )
        record("execution receipt validates", not execution_errors, str(execution_errors))

        def replace_event_type(value: dict[str, Any], old: str, new: str) -> None:
            next(event for event in value["events"] if event["event"] == old)["event"] = new

        execution_mutations: tuple[tuple[str, Callable[[dict[str, Any]], None]], ...] = (
            ("execution rejects incomplete event capture", lambda value: value.__setitem__("complete_event_capture", False)),
            ("execution rejects orphan process", lambda value: value.__setitem__("orphan_processes_detected", True)),
            ("execution rejects policy violation", lambda value: value["policy_violations"].append("violation")),
            ("execution rejects second integrator", lambda value: value["actors"].append(clone(value["actors"][0]))),
            ("execution rejects nested actor", lambda value: value["actors"][1].__setitem__("parent_id", "worker-0")),
            ("execution rejects recursive capability", lambda value: value["leases"][0]["capabilities"].append("spawn")),
            ("execution rejects task scope change", lambda value: value["leases"][0]["read_paths"].append("outside")),
            ("execution rejects duplicate terminal sequence", lambda value: value["leases"][0].__setitem__("terminal_sequence", 1)),
            ("execution rejects missing grant event", lambda value: replace_event_type(value, "lease-granted", "claim-attempt")),
            ("execution rejects terminal race", lambda value: replace_event_type(value, "lease-completed", "lease-failed")),
            ("execution rejects canonical writer mismatch", lambda value: value["canonical_pre_acceptance"].__setitem__("integrator_id", "worker-1")),
            ("execution rejects non-integrator write", lambda value: value["canonical_pre_acceptance"].__setitem__("non_integrator_writes_detected", True)),
            ("execution rejects integer substituted for canonical boolean", lambda value: value["canonical_pre_acceptance"].__setitem__("non_integrator_writes_detected", 0)),
            ("execution rejects resource overrun", lambda value: value["resource_usage"].__setitem__("tokens", 100001)),
            ("execution rejects bool usage", lambda value: value["resource_usage"].__setitem__("tokens", True)),
        )
        for name, mutate in execution_mutations:
            bad = clone(fixture.execution)
            mutate(bad)
            errors, _ = validate_execution_receipt(
                bad,
                packet=fixture.packet,
                host_capabilities=fixture.capabilities,
                task_graph=fixture.plan,
                resource_envelope=fixture.resources,
                envelope_sha256=_json_digest(fixture.envelope),
                repository=fixture.repo,
                baseline_manifest=fixture.baseline,
                pre_acceptance_manifest=build_state_manifest(fixture.repo),
                protected_paths=fixture.paths.values(),
            )
            record(name, bool(errors))

        def base_execution_errors(execution: dict[str, Any]) -> list[str]:
            observed, _ = validate_execution_receipt(
                execution,
                packet=fixture.packet,
                host_capabilities=fixture.capabilities,
                task_graph=fixture.plan,
                resource_envelope=fixture.resources,
                envelope_sha256=_json_digest(fixture.envelope),
                repository=fixture.repo,
                baseline_manifest=fixture.baseline,
                pre_acceptance_manifest=build_state_manifest(fixture.repo),
                protected_paths=fixture.paths.values(),
                report=fixture.report,
            )
            return observed

        ghost = clone(fixture.execution)
        ghost["events"].append(
            {
                "sequence": len(ghost["events"]) + 1,
                "event": "lease-granted",
                "task_id": "ghost-task",
                "actor_id": "ghost-actor",
                "lease_id": "ghost-lease",
                "candidate_id": None,
                "artifact_sha256": "0" * 64,
            }
        )
        record(
            "execution rejects ghost lease event",
            any("ghost" in error or "unbound" in error for error in base_execution_errors(ghost)),
        )
        wrong_binding = clone(fixture.execution)
        next(
            event for event in wrong_binding["events"] if event["event"] == "lease-granted"
        )["task_id"] = "ghost-task"
        record(
            "execution rejects grant bound to wrong task actor or lease",
            bool(base_execution_errors(wrong_binding)),
        )

        atomic_envelope = clone(fixture.envelope)
        atomic_envelope["host_capabilities_sha256"] = _json_digest(atomic_capabilities)
        atomic_envelope["task_graph_sha256"] = _json_digest(atomic)
        atomic_envelope_digest = _json_digest(atomic_envelope)
        atomic_prompt, _, _ = build_task_prompt_v5(
            fixture.packet,
            atomic,
            atomic["tasks"][0],
            "worker-1",
            atomic_envelope_digest,
        )
        atomic_prompt_digest = hashlib.sha256(atomic_prompt.encode("utf-8")).hexdigest()
        atomic_execution = clone(fixture.execution)
        atomic_execution["orchestration_envelope_sha256"] = atomic_envelope_digest
        atomic_execution["task_graph_sha256"] = _json_digest(atomic)
        atomic_execution["actors"].append(
            {
                "id": "worker-2",
                "parent_id": "main-thread",
                "kind": "analysis-worker",
                "task_ids": [],
                "workspace_ref": None,
            }
        )
        atomic_execution["leases"][0].update(
            {
                "grant_sequence": 5,
                "terminal_sequence": 8,
                "task_prompt_sha256": atomic_prompt_digest,
            }
        )
        atomic_execution["events"] = [
            {
                "sequence": 1,
                "event": "envelope-sealed",
                "task_id": None,
                "actor_id": "main-thread",
                "lease_id": None,
                "candidate_id": None,
                "artifact_sha256": atomic_envelope_digest,
            },
            {
                "sequence": 2,
                "event": "actor-spawned",
                "task_id": None,
                "actor_id": "worker-1",
                "lease_id": None,
                "candidate_id": None,
                "artifact_sha256": atomic_envelope_digest,
            },
            {
                "sequence": 3,
                "event": "actor-spawned",
                "task_id": None,
                "actor_id": "worker-2",
                "lease_id": None,
                "candidate_id": None,
                "artifact_sha256": atomic_envelope_digest,
            },
            {
                "sequence": 4,
                "event": "claim-attempt",
                "task_id": "task-1",
                "actor_id": "worker-1",
                "lease_id": None,
                "candidate_id": None,
                "artifact_sha256": None,
            },
            {
                "sequence": 5,
                "event": "lease-granted",
                "task_id": "task-1",
                "actor_id": "worker-1",
                "lease_id": "lease-1",
                "candidate_id": None,
                "artifact_sha256": atomic_prompt_digest,
            },
            {
                "sequence": 6,
                "event": "claim-attempt",
                "task_id": "task-1",
                "actor_id": "worker-2",
                "lease_id": None,
                "candidate_id": None,
                "artifact_sha256": None,
            },
            {
                "sequence": 7,
                "event": "claim-denied",
                "task_id": "task-1",
                "actor_id": "worker-2",
                "lease_id": None,
                "candidate_id": None,
                "artifact_sha256": None,
            },
            {
                "sequence": 8,
                "event": "lease-completed",
                "task_id": "task-1",
                "actor_id": "worker-1",
                "lease_id": "lease-1",
                "candidate_id": None,
                "artifact_sha256": None,
            },
        ]
        atomic_execution["resource_usage"]["peak_concurrency"] = 2

        def atomic_errors(value: dict[str, Any]) -> list[str]:
            observed, _ = validate_execution_receipt(
                value,
                packet=fixture.packet,
                host_capabilities=atomic_capabilities,
                task_graph=atomic,
                resource_envelope=fixture.resources,
                envelope_sha256=atomic_envelope_digest,
                repository=fixture.repo,
                baseline_manifest=fixture.baseline,
                pre_acceptance_manifest=build_state_manifest(fixture.repo),
                protected_paths=fixture.paths.values(),
                report=fixture.report,
            )
            return observed

        record(
            "atomic claim replay grants at most one lease and denies the loser",
            not atomic_errors(atomic_execution),
            str(atomic_errors(atomic_execution))[:500],
        )
        claim_before_spawn = clone(atomic_execution)
        next(
            event
            for event in claim_before_spawn["events"]
            if event["event"] == "actor-spawned" and event["actor_id"] == "worker-2"
        )["sequence"] = 6
        next(
            event
            for event in claim_before_spawn["events"]
            if event["event"] == "claim-attempt" and event["actor_id"] == "worker-2"
        )["sequence"] = 3
        record(
            "atomic claim attempt before actor spawn is rejected",
            any("before its actor spawned" in error for error in atomic_errors(claim_before_spawn)),
        )
        double_lease = clone(atomic_execution)
        competing_lease = clone(double_lease["leases"][0])
        competing_lease.update(
            {
                "id": "lease-2",
                "actor_id": "worker-2",
                "grant_sequence": 6,
                "terminal_sequence": 7,
            }
        )
        double_lease["leases"].append(competing_lease)
        record(
            "two atomic claimants cannot receive leases for the same task",
            any("multiply leased" in error for error in atomic_errors(double_lease)),
        )
        terminal_race = clone(atomic_execution)
        terminal_race["events"].append(
            {
                "sequence": 9,
                "event": "lease-cancelled",
                "task_id": "task-1",
                "actor_id": "worker-1",
                "lease_id": "lease-1",
                "candidate_id": None,
                "artifact_sha256": None,
            }
        )
        record(
            "completion and cancellation race has only one accepted terminal state",
            any("competing terminal" in error for error in atomic_errors(terminal_race)),
        )
        reordered = clone(fixture.execution)
        sealed = next(event for event in reordered["events"] if event["event"] == "envelope-sealed")
        spawned = next(event for event in reordered["events"] if event["event"] == "actor-spawned")
        sealed["sequence"], spawned["sequence"] = spawned["sequence"], sealed["sequence"]
        record(
            "execution rejects actor spawn before sealed envelope",
            any("spawned before" in error for error in base_execution_errors(reordered)),
        )
        unbound_prompt = clone(fixture.execution)
        unbound_prompt["leases"][0]["task_prompt_sha256"] = "0" * 64
        next(
            event for event in unbound_prompt["events"] if event["event"] == "lease-granted"
        )["artifact_sha256"] = "0" * 64
        record(
            "execution rejects self-consistent but unanchored worker prompt digest",
            any("prompt digest" in error for error in base_execution_errors(unbound_prompt)),
        )

        verification_errors = validate_verification_receipt(
            fixture.verification,
            packet=fixture.packet,
            execution_receipt=fixture.execution,
            envelope_sha256=_json_digest(fixture.envelope),
            execution_receipt_sha256=_json_digest(fixture.execution),
            verifier_bundle_sha256=verifier_bundle_sha256(),
            repository_ref=fixture.baseline["repository_ref"],
        )
        record("verification receipt validates", not verification_errors, str(verification_errors))
        for field, replacement in (
            ("verifier_id", "worker-1"),
            ("fresh_context", False),
            ("write_access", True),
            ("candidate_outputs_visible", True),
            ("verdict", "failed"),
            ("verifier_bundle_sha256", "0" * 64),
            ("final_state_sha256", "0" * 64),
            ("diff_sha256", "0" * 64),
        ):
            bad = clone(fixture.verification)
            bad[field] = replacement
            errors = validate_verification_receipt(
                bad,
                packet=fixture.packet,
                execution_receipt=fixture.execution,
                envelope_sha256=_json_digest(fixture.envelope),
                execution_receipt_sha256=_json_digest(fixture.execution),
                verifier_bundle_sha256=verifier_bundle_sha256(),
                repository_ref=fixture.baseline["repository_ref"],
            )
            record(f"verification rejects {field}", bool(errors))
        bad = clone(fixture.verification)
        bad["checks"].pop()
        record(
            "verification rejects missing command",
            bool(
                validate_verification_receipt(
                    bad,
                    packet=fixture.packet,
                    execution_receipt=fixture.execution,
                    envelope_sha256=_json_digest(fixture.envelope),
                    execution_receipt_sha256=_json_digest(fixture.execution),
                    verifier_bundle_sha256=verifier_bundle_sha256(),
                    repository_ref=fixture.baseline["repository_ref"],
                )
            ),
        )
        bad = clone(fixture.verification)
        bad["checks"][0]["exit_code"] = 1
        record(
            "verification rejects failed command",
            bool(
                validate_verification_receipt(
                    bad,
                    packet=fixture.packet,
                    execution_receipt=fixture.execution,
                    envelope_sha256=_json_digest(fixture.envelope),
                    execution_receipt_sha256=_json_digest(fixture.execution),
                    verifier_bundle_sha256=verifier_bundle_sha256(),
                    repository_ref=fixture.baseline["repository_ref"],
                )
            ),
        )
        bad = clone(fixture.verification)
        bad["checks"][0]["exit_code"] = False
        record(
            "verification rejects boolean substituted for exit code",
            bool(
                validate_verification_receipt(
                    bad,
                    packet=fixture.packet,
                    execution_receipt=fixture.execution,
                    envelope_sha256=_json_digest(fixture.envelope),
                    execution_receipt_sha256=_json_digest(fixture.execution),
                    verifier_bundle_sha256=verifier_bundle_sha256(),
                    repository_ref=fixture.baseline["repository_ref"],
                )
            ),
        )

        report_errors = validate_v5_report(
            fixture.report,
            packet=fixture.packet,
            envelope_sha256=_json_digest(fixture.envelope),
            execution_receipt_sha256=_json_digest(fixture.execution),
            verification_receipt_sha256=_json_digest(fixture.verification),
            integrations=fixture.execution["integrations"],
        )
        record("v5 report validates orchestration refs", not report_errors, str(report_errors))
        for field in (
            "envelope_sha256",
            "execution_receipt_sha256",
            "verification_receipt_sha256",
        ):
            bad = clone(fixture.report)
            bad["orchestration"][field] = "0" * 64
            errors = validate_v5_report(
                bad,
                packet=fixture.packet,
                envelope_sha256=_json_digest(fixture.envelope),
                execution_receipt_sha256=_json_digest(fixture.execution),
                verification_receipt_sha256=_json_digest(fixture.verification),
                integrations=fixture.execution["integrations"],
            )
            record(f"report rejects {field}", bool(errors))
        bad = clone(fixture.report)
        bad["acceptance"] = []
        record(
            "report cannot smuggle acceptance",
            bool(
                validate_v5_report(
                    bad,
                    packet=fixture.packet,
                    envelope_sha256=_json_digest(fixture.envelope),
                    execution_receipt_sha256=_json_digest(fixture.execution),
                    verification_receipt_sha256=_json_digest(fixture.verification),
                    integrations=fixture.execution["integrations"],
                )
            ),
        )

        for argument in (
            "--orchestration-envelope",
            "--execution-receipt",
            "--verification-receipt",
            "--sandbox-profile",
        ):
            argv = fixture.gate_argv()
            index = argv.index(argument)
            del argv[index : index + 2]
            status, payload = call_gate(fixture, argv)
            record(f"gate fails closed without {argument}", status != 0 and payload.get("passed") is False)
        argv = fixture.gate_argv()
        missing_index = argv.index("--execution-receipt")
        del argv[missing_index : missing_index + 2]
        with mock.patch(
            "check_delivery_v5.run_frozen_checks",
            side_effect=AssertionError("acceptance command ran before the gate"),
        ):
            status, payload = call_gate(fixture, argv)
        report_preflight = make_fixture(Path(temporary) / "report-preflight")
        report_preflight.report["implementation"]["acceptance_results"] = []
        report_preflight.write_all()
        with mock.patch(
            "check_delivery_v5.run_frozen_checks",
            side_effect=AssertionError("invalid report reached acceptance commands"),
        ):
            report_status, report_payload = call_gate(report_preflight)
        record(
            "pre-gate failure executes no acceptance command",
            status != 0
            and payload.get("passed") is False
            and report_status != 0
            and report_payload.get("passed") is False,
        )

        toctou = make_fixture(Path(temporary) / "repository-toctou")
        stable_manifest = build_state_manifest(toctou.repo)
        changed_manifest = clone(stable_manifest)
        changed_manifest["entries"]["src/example.py"]["sha256"] = "0" * 64
        with mock.patch(
            "check_delivery_v5.build_state_manifest",
            side_effect=[stable_manifest, changed_manifest],
        ), mock.patch(
            "check_delivery_v5.run_frozen_checks",
            side_effect=AssertionError("TOCTOU state reached acceptance commands"),
        ):
            toctou_status, toctou_payload = call_gate(toctou)
        record(
            "repository change after receipt validation is rejected before commands",
            toctou_status != 0
            and any(
                "changed after receipt validation" in error
                for error in toctou_payload.get("errors", [])
            ),
            str(toctou_payload.get("errors", []))[:500],
        )

        shared = make_shared_fixture(Path(temporary) / "shared-root-relay")
        status, payload = call_gate(shared)
        record(
            "shared multi-agent root-relay chain passes",
            status == 0 and payload.get("passed") is True,
            str(payload.get("errors", []))[:500],
        )
        no_steering = make_shared_fixture(
            Path(temporary) / "shared-root-relay-without-steering"
        )
        no_steering.capabilities["capabilities"]["steer_child"] = False
        rebind_fixture_chain(no_steering)
        status, payload = call_gate(no_steering)
        record(
            "shared root-relay requires an observed child-steering channel",
            status != 0
            and any(
                "shared root-relay requires steer_child" in error
                for error in payload.get("errors", [])
            ),
            str(payload.get("errors", []))[:500],
        )
        shared_peer = make_shared_fixture(
            Path(temporary) / "shared-peer-message", peer_message=True
        )
        status, payload = call_gate(shared_peer)
        record(
            "shared multi-agent peer-message chain passes",
            status == 0 and payload.get("passed") is True,
            str(payload.get("errors", []))[:500],
        )
        shared_dependency = make_shared_dependency_fixture(
            Path(temporary) / "shared-root-relay-dependency"
        )
        status, payload = call_gate(shared_dependency)
        record(
            "shared root-relay dependency chain passes",
            status == 0 and payload.get("passed") is True,
            str(payload.get("errors", []))[:500],
        )
        understated_peak = make_shared_fixture(
            Path(temporary) / "shared-understated-peak"
        )
        understated_peak.execution["resource_usage"]["peak_concurrency"] = 1
        refresh_receipt_chain(understated_peak)
        status, payload = call_gate(understated_peak)
        record(
            "receipt cannot understate event-observed active-lease peak",
            status != 0
            and any(
                "event-observed active-lease peak" in error
                for error in payload.get("errors", [])
            ),
            str(payload.get("errors", []))[:500],
        )
        posthoc_relay = make_shared_fixture(
            Path(temporary) / "shared-posthoc-root-relay"
        )
        relay_event = next(
            event
            for event in posthoc_relay.execution["events"]
            if event["event"] == "peer-board-relayed"
        )
        terminal_events = [
            event
            for event in posthoc_relay.execution["events"]
            if event["event"] == "lease-completed"
        ]
        terminal_events[0]["sequence"] = 7
        terminal_events[1]["sequence"] = 8
        relay_event["sequence"] = 9
        posthoc_relay.execution["leases"][0]["terminal_sequence"] = 7
        posthoc_relay.execution["leases"][1]["terminal_sequence"] = 8
        refresh_receipt_chain(posthoc_relay)
        status, payload = call_gate(posthoc_relay)
        record(
            "root relay cannot be fabricated after every active participant terminates",
            status != 0
            and any(
                "terminated before board relay" in error
                for error in payload.get("errors", [])
            ),
            str(payload.get("errors", []))[:500],
        )
        lane_mismatch = make_shared_fixture(
            Path(temporary) / "shared-lane-mismatch", peer_message=True
        )
        lane_mismatch.plan["assignments"][0]["runtime_identity"] = "agent-2"
        lane_mismatch.plan["assignments"][1]["runtime_identity"] = "agent-1"
        write_json(lane_mismatch.paths["plan"], lane_mismatch.plan)
        lane_mismatch.envelope["task_graph_sha256"] = _json_digest(lane_mismatch.plan)
        lane_envelope_digest = _json_digest(lane_mismatch.envelope)
        lane_mismatch.execution["orchestration_envelope_sha256"] = lane_envelope_digest
        lane_mismatch.execution["task_graph_sha256"] = _json_digest(lane_mismatch.plan)
        lane_mismatch.execution["actors"][1]["task_ids"] = ["task-2"]
        lane_mismatch.execution["actors"][2]["task_ids"] = ["task-1"]
        for index, lease in enumerate(lane_mismatch.execution["leases"]):
            actor_id = "agent-2" if index == 0 else "agent-1"
            lease["actor_id"] = actor_id
            prompt, _, _ = build_task_prompt_v5(
                lane_mismatch.packet,
                lane_mismatch.plan,
                lane_mismatch.plan["tasks"][index],
                actor_id,
                lane_envelope_digest,
            )
            prompt_digest = hashlib.sha256(prompt.encode("utf-8")).hexdigest()
            lease["task_prompt_sha256"] = prompt_digest
            for event in lane_mismatch.execution["events"]:
                if event.get("lease_id") == lease["id"]:
                    event["actor_id"] = actor_id
                    if event["event"] == "lease-granted":
                        event["artifact_sha256"] = prompt_digest
        for event in lane_mismatch.execution["events"]:
            if event["event"] in {"envelope-sealed", "actor-spawned"}:
                event["artifact_sha256"] = lane_envelope_digest
        lane_mismatch.verification[
            "orchestration_envelope_sha256"
        ] = lane_envelope_digest
        lane_mismatch.report["orchestration"][
            "envelope_sha256"
        ] = lane_envelope_digest
        refresh_receipt_chain(lane_mismatch)
        with mock.patch(
            "check_delivery_v5.run_frozen_checks",
            side_effect=AssertionError("lane mismatch reached acceptance commands"),
        ):
            lane_status, lane_payload = call_gate(lane_mismatch)
        record(
            "full gate rejects participant lanes that differ from leased tasks",
            lane_status != 0
            and any("lanes differ" in error for error in lane_payload.get("errors", [])),
            str(lane_payload.get("errors", []))[:500],
        )
        shared_atomic = make_shared_fixture(
            Path(temporary) / "shared-atomic-claim", peer_message=True
        )
        shared_atomic.capabilities["capabilities"]["atomic_task_claim"] = True
        shared_atomic.plan["dispatch"] = "atomic-claim"
        shared_atomic.plan["assignments"] = []
        write_json(shared_atomic.paths["capabilities"], shared_atomic.capabilities)
        write_json(shared_atomic.paths["plan"], shared_atomic.plan)
        shared_atomic.envelope["host_capabilities_sha256"] = _json_digest(
            shared_atomic.capabilities
        )
        shared_atomic.envelope["task_graph_sha256"] = _json_digest(shared_atomic.plan)
        envelope_digest = _json_digest(shared_atomic.envelope)
        board_digest = shared_atomic.report["deliberation"]["peer_board_sha256"]
        shared_atomic.execution["orchestration_envelope_sha256"] = envelope_digest
        shared_atomic.execution["task_graph_sha256"] = _json_digest(shared_atomic.plan)
        shared_atomic.execution["actors"].append(
            {
                "id": "claimant-loser",
                "parent_id": "main-thread",
                "kind": "analysis-worker",
                "task_ids": [],
                "workspace_ref": None,
            }
        )
        for index, lease in enumerate(shared_atomic.execution["leases"]):
            actor_id = lease["actor_id"]
            task = shared_atomic.plan["tasks"][index]
            prompt, _, _ = build_task_prompt_v5(
                shared_atomic.packet,
                shared_atomic.plan,
                task,
                actor_id,
                envelope_digest,
            )
            lease["task_prompt_sha256"] = hashlib.sha256(
                prompt.encode("utf-8")
            ).hexdigest()
        shared_atomic.execution["leases"][0].update(
            {"grant_sequence": 6, "terminal_sequence": 14}
        )
        shared_atomic.execution["leases"][1].update(
            {"grant_sequence": 10, "terminal_sequence": 15}
        )
        shared_atomic.execution["events"] = [
            {
                "sequence": 1,
                "event": "envelope-sealed",
                "task_id": None,
                "actor_id": "main-thread",
                "lease_id": None,
                "candidate_id": None,
                "artifact_sha256": envelope_digest,
            },
            *[
                {
                    "sequence": index + 2,
                    "event": "actor-spawned",
                    "task_id": None,
                    "actor_id": actor_id,
                    "lease_id": None,
                    "candidate_id": None,
                    "artifact_sha256": envelope_digest,
                }
                for index, actor_id in enumerate(
                    ("agent-1", "agent-2", "claimant-loser")
                )
            ],
            {
                "sequence": 5,
                "event": "claim-attempt",
                "task_id": "task-1",
                "actor_id": "agent-1",
                "lease_id": None,
                "candidate_id": None,
                "artifact_sha256": None,
            },
            {
                "sequence": 6,
                "event": "lease-granted",
                "task_id": "task-1",
                "actor_id": "agent-1",
                "lease_id": "lease-1",
                "candidate_id": None,
                "artifact_sha256": shared_atomic.execution["leases"][0][
                    "task_prompt_sha256"
                ],
            },
            {
                "sequence": 7,
                "event": "claim-attempt",
                "task_id": "task-1",
                "actor_id": "claimant-loser",
                "lease_id": None,
                "candidate_id": None,
                "artifact_sha256": None,
            },
            {
                "sequence": 8,
                "event": "claim-denied",
                "task_id": "task-1",
                "actor_id": "claimant-loser",
                "lease_id": None,
                "candidate_id": None,
                "artifact_sha256": None,
            },
            {
                "sequence": 9,
                "event": "claim-attempt",
                "task_id": "task-2",
                "actor_id": "agent-2",
                "lease_id": None,
                "candidate_id": None,
                "artifact_sha256": None,
            },
            {
                "sequence": 10,
                "event": "lease-granted",
                "task_id": "task-2",
                "actor_id": "agent-2",
                "lease_id": "lease-2",
                "candidate_id": None,
                "artifact_sha256": shared_atomic.execution["leases"][1][
                    "task_prompt_sha256"
                ],
            },
            {
                "sequence": 11,
                "event": "round1-sealed",
                "task_id": None,
                "actor_id": "main-thread",
                "lease_id": None,
                "candidate_id": None,
                "artifact_sha256": board_digest,
            },
            *[
                {
                    "sequence": index + 12,
                    "event": "peer-message",
                    "task_id": None,
                    "actor_id": actor_id,
                    "lease_id": None,
                    "candidate_id": None,
                    "artifact_sha256": board_digest,
                }
                for index, actor_id in enumerate(("agent-1", "agent-2"))
            ],
            *[
                {
                    "sequence": index + 14,
                    "event": "lease-completed",
                    "task_id": f"task-{index + 1}",
                    "actor_id": f"agent-{index + 1}",
                    "lease_id": f"lease-{index + 1}",
                    "candidate_id": None,
                    "artifact_sha256": None,
                }
                for index in range(2)
            ],
        ]
        shared_atomic.execution["resource_usage"]["peak_concurrency"] = 3
        shared_atomic.verification["orchestration_envelope_sha256"] = envelope_digest
        shared_atomic.report["orchestration"]["envelope_sha256"] = envelope_digest
        refresh_receipt_chain(shared_atomic)
        status, payload = call_gate(shared_atomic)
        record(
            "shared atomic claim permits unleased losers without making them participants",
            status == 0 and payload.get("passed") is True,
            str(payload.get("errors", []))[:500],
        )
        tampered_shared = make_shared_fixture(
            Path(temporary) / "shared-tampered-prompts"
        )
        tampered_delegation = tampered_shared.report["deliberation"]["delegation"]
        tampered_delegation["packet_sha256"] = "0" * 64
        for participant in tampered_delegation["participants"]:
            participant["round1_prompt"] = "MALICIOUS UNBOUND PROMPT"
            participant["round2_prompt"] = "MALICIOUS UNBOUND PROMPT"
        refresh_receipt_chain(tampered_shared)
        with mock.patch(
            "check_delivery_v5.run_frozen_checks",
            side_effect=AssertionError("tampered prompts reached acceptance commands"),
        ):
            tampered_status, tampered_payload = call_gate(tampered_shared)
        record(
            "full gate rejects re-anchored malicious v5 delegation prompts before acceptance",
            tampered_status != 0 and tampered_payload.get("passed") is False,
            str(tampered_payload.get("errors", []))[:500],
        )
        wrong_deliberation = clone(shared.execution)
        wrong_deliberation["deliberation_sha256"] = "0" * 64
        wrong_deliberation_errors, _ = validate_execution_receipt(
            wrong_deliberation,
            packet=shared.packet,
            host_capabilities=shared.capabilities,
            task_graph=shared.plan,
            resource_envelope=shared.resources,
            envelope_sha256=_json_digest(shared.envelope),
            repository=shared.repo,
            baseline_manifest=shared.baseline,
            pre_acceptance_manifest=build_state_manifest(shared.repo),
            protected_paths=shared.paths.values(),
            report=shared.report,
        )
        record(
            "execution receipt rejects deliberation digest mismatch",
            any("complete deliberation" in error for error in wrong_deliberation_errors),
        )
        missing_peer = clone(shared_peer.execution)
        removed_sequence = next(
            event["sequence"]
            for event in missing_peer["events"]
            if event["event"] == "peer-message" and event["actor_id"] == "agent-2"
        )
        missing_peer["events"] = [
            event
            for event in missing_peer["events"]
            if event["sequence"] != removed_sequence
        ]
        for event in missing_peer["events"]:
            if event["sequence"] > removed_sequence:
                event["sequence"] -= 1
        for lease in missing_peer["leases"]:
            if lease["terminal_sequence"] > removed_sequence:
                lease["terminal_sequence"] -= 1
        missing_peer_errors, _ = validate_execution_receipt(
            missing_peer,
            packet=shared_peer.packet,
            host_capabilities=shared_peer.capabilities,
            task_graph=shared_peer.plan,
            resource_envelope=shared_peer.resources,
            envelope_sha256=_json_digest(shared_peer.envelope),
            repository=shared_peer.repo,
            baseline_manifest=shared_peer.baseline,
            pre_acceptance_manifest=build_state_manifest(shared_peer.repo),
            protected_paths=shared_peer.paths.values(),
            report=shared_peer.report,
        )
        record(
            "peer-message exchange must include every shared worker",
            any("every leased shared participant" in error for error in missing_peer_errors),
            str(missing_peer_errors)[:500],
        )
        relay_before_seal = clone(shared.execution)
        relay_seal = next(
            event for event in relay_before_seal["events"] if event["event"] == "round1-sealed"
        )
        relay_event = next(
            event
            for event in relay_before_seal["events"]
            if event["event"] == "peer-board-relayed"
        )
        relay_seal["sequence"], relay_event["sequence"] = (
            relay_event["sequence"],
            relay_seal["sequence"],
        )
        relay_errors, _ = validate_execution_receipt(
            relay_before_seal,
            packet=shared.packet,
            host_capabilities=shared.capabilities,
            task_graph=shared.plan,
            resource_envelope=shared.resources,
            envelope_sha256=_json_digest(shared.envelope),
            repository=shared.repo,
            baseline_manifest=shared.baseline,
            pre_acceptance_manifest=build_state_manifest(shared.repo),
            protected_paths=shared.paths.values(),
            report=shared.report,
        )
        record(
            "root relay before sealed Round 1 is rejected",
            any("before sealed Round 1" in error for error in relay_errors),
            str(relay_errors)[:500],
        )
        peer_before_seal = clone(shared_peer.execution)
        seal_event = next(
            event for event in peer_before_seal["events"] if event["event"] == "round1-sealed"
        )
        peer_event = next(
            event for event in peer_before_seal["events"] if event["event"] == "peer-message"
        )
        seal_event["sequence"], peer_event["sequence"] = (
            peer_event["sequence"],
            seal_event["sequence"],
        )
        peer_errors, _ = validate_execution_receipt(
            peer_before_seal,
            packet=shared_peer.packet,
            host_capabilities=shared_peer.capabilities,
            task_graph=shared_peer.plan,
            resource_envelope=shared_peer.resources,
            envelope_sha256=_json_digest(shared_peer.envelope),
            repository=shared_peer.repo,
            baseline_manifest=shared_peer.baseline,
            pre_acceptance_manifest=build_state_manifest(shared_peer.repo),
            protected_paths=shared_peer.paths.values(),
            report=shared_peer.report,
        )
        record(
            "peer message before sealed Round 1 is rejected",
            any("before sealed Round 1" in error for error in peer_errors),
            str(peer_errors)[:500],
        )

        duplicate_lease = clone(fixture.execution)
        second_lease = clone(duplicate_lease["leases"][0])
        second_lease["id"] = "lease-2"
        second_lease["grant_sequence"] = 3
        second_lease["terminal_sequence"] = 4
        duplicate_lease["leases"].append(second_lease)
        errors, _ = validate_execution_receipt(
            duplicate_lease,
            packet=fixture.packet,
            host_capabilities=fixture.capabilities,
            task_graph=fixture.plan,
            resource_envelope=fixture.resources,
            envelope_sha256=_json_digest(fixture.envelope),
            repository=fixture.repo,
            baseline_manifest=fixture.baseline,
            pre_acceptance_manifest=build_state_manifest(fixture.repo),
            protected_paths=fixture.paths.values(),
        )
        record(
            "two workers cannot receive leases for one task",
            any("multiply leased" in error for error in errors),
            str(errors)[:500],
        )
        terminal_race = clone(fixture.execution)
        terminal_race["events"].append(
            {
                "sequence": len(terminal_race["events"]) + 1,
                "event": "lease-failed",
                "task_id": "task-1",
                "actor_id": "worker-1",
                "lease_id": "lease-1",
                "candidate_id": None,
                "artifact_sha256": None,
            }
        )
        errors, _ = validate_execution_receipt(
            terminal_race,
            packet=fixture.packet,
            host_capabilities=fixture.capabilities,
            task_graph=fixture.plan,
            resource_envelope=fixture.resources,
            envelope_sha256=_json_digest(fixture.envelope),
            repository=fixture.repo,
            baseline_manifest=fixture.baseline,
            pre_acceptance_manifest=build_state_manifest(fixture.repo),
            protected_paths=fixture.paths.values(),
        )
        record(
            "completion and cancellation race has one terminal state",
            any("competing terminal events" in error for error in errors),
            str(errors)[:500],
        )
        dependency_plan = clone(fixture.plan)
        dependency_task = clone(dependency_plan["tasks"][0])
        dependency_task["id"] = "task-2"
        dependency_task["objective"] = "Consume only a successfully completed predecessor."
        dependency_task["dependencies"] = ["task-1"]
        dependency_plan["tasks"].append(dependency_task)
        dependency_assignment = clone(dependency_plan["assignments"][0])
        dependency_assignment["task_id"] = "task-2"
        dependency_assignment["runtime_identity"] = "worker-2"
        dependency_plan["assignments"].append(dependency_assignment)
        dependency_execution = clone(fixture.execution)
        dependency_execution["task_graph_sha256"] = _json_digest(dependency_plan)
        dependency_execution["actors"].append(
            {
                "id": "worker-2",
                "parent_id": "main-thread",
                "kind": "analysis-worker",
                "task_ids": ["task-2"],
                "workspace_ref": None,
            }
        )
        dependency_execution["leases"] = []
        dependency_execution["events"] = [
            {
                "sequence": 1,
                "event": "envelope-sealed",
                "task_id": None,
                "actor_id": "main-thread",
                "lease_id": None,
                "candidate_id": None,
                "artifact_sha256": _json_digest(fixture.envelope),
            },
            *[
                {
                    "sequence": index + 2,
                    "event": "actor-spawned",
                    "task_id": None,
                    "actor_id": identity,
                    "lease_id": None,
                    "candidate_id": None,
                    "artifact_sha256": _json_digest(fixture.envelope),
                }
                for index, identity in enumerate(("worker-1", "worker-2"))
            ],
        ]
        for index, (task_id, actor_id, state) in enumerate(
            (("task-1", "worker-1", "failed"), ("task-2", "worker-2", "completed"))
        ):
            task = dependency_plan["tasks"][index]
            prompt, _, _ = build_task_prompt_v5(
                fixture.packet,
                dependency_plan,
                task,
                actor_id,
                _json_digest(fixture.envelope),
            )
            prompt_digest = hashlib.sha256(prompt.encode("utf-8")).hexdigest()
            grant_sequence = 4 + index * 2
            terminal_sequence = grant_sequence + 1
            lease_id = f"dependency-lease-{index + 1}"
            dependency_execution["leases"].append(
                {
                    "id": lease_id,
                    "task_id": task_id,
                    "actor_id": actor_id,
                    "grant_sequence": grant_sequence,
                    "terminal_sequence": terminal_sequence,
                    "state": state,
                    "task_prompt_sha256": prompt_digest,
                    "capabilities": ["enforced_readonly"],
                    "read_paths": task["read_paths"],
                    "candidate_write_paths": task["candidate_write_paths"],
                    "acceptance_ids": task["acceptance_ids"],
                }
            )
            dependency_execution["events"].extend(
                [
                    {
                        "sequence": grant_sequence,
                        "event": "lease-granted",
                        "task_id": task_id,
                        "actor_id": actor_id,
                        "lease_id": lease_id,
                        "candidate_id": None,
                        "artifact_sha256": prompt_digest,
                    },
                    {
                        "sequence": terminal_sequence,
                        "event": f"lease-{state}",
                        "task_id": task_id,
                        "actor_id": actor_id,
                        "lease_id": lease_id,
                        "candidate_id": None,
                        "artifact_sha256": None,
                    },
                ]
            )
        dependency_execution["resource_usage"]["peak_concurrency"] = 2
        dependency_errors, _ = validate_execution_receipt(
            dependency_execution,
            packet=fixture.packet,
            host_capabilities=fixture.capabilities,
            task_graph=dependency_plan,
            resource_envelope=fixture.resources,
            envelope_sha256=_json_digest(fixture.envelope),
            repository=fixture.repo,
            baseline_manifest=fixture.baseline,
            pre_acceptance_manifest=build_state_manifest(fixture.repo),
            protected_paths=fixture.paths.values(),
            report=fixture.report,
        )
        record(
            "successor cannot run after a failed dependency",
            any("non-completed" in error for error in dependency_errors),
            str(dependency_errors)[:500],
        )

        isolated = make_isolated_fixture(Path(temporary) / "isolated")
        status, payload = call_gate(isolated)
        record(
            "valid isolated candidate chain passes",
            status == 0 and payload.get("passed") is True,
            str(payload.get("errors", []))[:500],
        )

        mixed_isolated = make_isolated_fixture(
            Path(temporary) / "isolated-with-analysis-task"
        )
        analysis_task = clone(mixed_isolated.plan["tasks"][0])
        analysis_task["id"] = "task-2"
        analysis_task["objective"] = "Provide a read-only analysis proposal."
        analysis_task["output_contract"]["kind"] = "candidate-proposal"
        analysis_assignment = clone(mixed_isolated.plan["assignments"][0])
        analysis_assignment["task_id"] = "task-2"
        analysis_assignment["runtime_identity"] = "worker-2"
        mixed_isolated.plan["tasks"].append(analysis_task)
        mixed_isolated.plan["assignments"].append(analysis_assignment)
        mixed_isolated.capabilities["capabilities"]["enforced_readonly"] = False
        rebind_fixture_chain(mixed_isolated)
        status, payload = call_gate(mixed_isolated)
        record(
            "isolated graph analysis tasks still require enforced read-only",
            status != 0
            and any(
                "delegated analysis tasks require enforced_readonly" in error
                for error in payload.get("errors", [])
            ),
            str(payload.get("errors", []))[:500],
        )

        workspace_hardlink = make_isolated_fixture(
            Path(temporary) / "isolated-workspace-hardlink"
        )
        workspace_path = Path(
            workspace_hardlink.execution["actors"][1]["workspace_ref"]
        )
        canonical_alias = workspace_path / "canonical-alias.py"
        os.link(workspace_hardlink.repo / "src" / "example.py", canonical_alias)
        rebind_final_repository_state(workspace_hardlink)
        try:
            status, payload = call_gate(workspace_hardlink)
            record(
                "candidate workspace cannot hard-link the target repository",
                status != 0
                and any(
                    "candidate workspace worker-1 hard-links the target repository"
                    in error
                    for error in payload.get("errors", [])
                ),
                str(payload.get("errors", []))[:500],
            )
        finally:
            canonical_alias.unlink()

        external_hardlink = make_isolated_fixture(
            Path(temporary) / "isolated-external-hardlink"
        )
        external_object = external_hardlink.root / "unprotected-external-object"
        external_object.write_bytes(b"external object\n")
        external_workspace = Path(
            external_hardlink.execution["actors"][1]["workspace_ref"]
        )
        external_alias = external_workspace / "external-alias"
        os.link(external_object, external_alias)
        try:
            status, payload = call_gate(external_hardlink)
            record(
                "candidate workspace rejects any external hard-linked file",
                status != 0
                and any(
                    "candidate workspace worker-1 contains a hard-linked file"
                    in error
                    for error in payload.get("errors", [])
                ),
                str(payload.get("errors", []))[:500],
            )
        finally:
            external_alias.unlink()

        canonical_hardlink = make_isolated_fixture(
            Path(temporary) / "canonical-hardlink",
            contract_paths=["src/example.py", "src/a.txt", "src/b.txt"],
            extra_sources={"src/a.txt": "same\n", "src/b.txt": "same\n"},
        )
        left = canonical_hardlink.repo / "src" / "a.txt"
        right = canonical_hardlink.repo / "src" / "b.txt"
        right.unlink()
        os.link(left, right)
        rebind_final_repository_state(canonical_hardlink)
        status, payload = call_gate(canonical_hardlink)
        record(
            "canonical repository rejects hard-linked working-tree files",
            status != 0
            and any(
                "target repository contains hard-linked files" in error
                for error in payload.get("errors", [])
            ),
            str(payload.get("errors", []))[:500],
        )

        def isolated_errors(
            execution: dict[str, Any], plan: dict[str, Any] | None = None
        ) -> list[str]:
            errors, _ = validate_execution_receipt(
                execution,
                packet=isolated.packet,
                host_capabilities=isolated.capabilities,
                task_graph=plan or isolated.plan,
                resource_envelope=isolated.resources,
                envelope_sha256=_json_digest(isolated.envelope),
                repository=isolated.repo,
                baseline_manifest=isolated.baseline,
                pre_acceptance_manifest=build_state_manifest(isolated.repo),
                protected_paths=(
                    path
                    for name, path in isolated.paths.items()
                    if name != "candidate_bundle"
                ),
            )
            return errors

        record("isolated execution receipt validates", not isolated_errors(isolated.execution))
        missing_candidate = clone(isolated.execution)
        missing_candidate["candidates"] = []
        missing_candidate["integrations"] = []
        missing_candidate["events"] = [
            event
            for event in missing_candidate["events"]
            if event["event"]
            in {"envelope-sealed", "actor-spawned", "lease-granted", "lease-completed"}
        ]
        for sequence, event in enumerate(missing_candidate["events"], start=1):
            event["sequence"] = sequence
        missing_candidate["leases"][0]["terminal_sequence"] = 4
        record(
            "completed candidate-bundle task must produce exactly one candidate",
            any("expected 1" in error for error in isolated_errors(missing_candidate)),
        )
        produced_after_terminal = clone(isolated.execution)
        produced_event = next(
            event
            for event in produced_after_terminal["events"]
            if event["event"] == "candidate-produced"
        )
        terminal_event = next(
            event
            for event in produced_after_terminal["events"]
            if event["event"] == "lease-completed"
        )
        produced_event["sequence"], terminal_event["sequence"] = (
            terminal_event["sequence"],
            produced_event["sequence"],
        )
        produced_after_terminal["leases"][0]["terminal_sequence"] = terminal_event["sequence"]
        record(
            "candidate production after lease terminal is rejected",
            any("outside its active lease" in error for error in isolated_errors(produced_after_terminal)),
        )
        integration_before_selection = clone(isolated.execution)
        selected_event = next(
            event
            for event in integration_before_selection["events"]
            if event["event"] == "candidate-selected"
        )
        integrated_event = next(
            event
            for event in integration_before_selection["events"]
            if event["event"] == "integration-completed"
        )
        selected_event["sequence"], integrated_event["sequence"] = (
            integrated_event["sequence"],
            selected_event["sequence"],
        )
        record(
            "integration before candidate selection is rejected",
            any("integration precedes selection" in error for error in isolated_errors(integration_before_selection)),
        )
        ghost_candidate_event = clone(isolated.execution)
        next(
            event
            for event in ghost_candidate_event["events"]
            if event["event"] == "candidate-produced"
        )["actor_id"] = "ghost-actor"
        record(
            "candidate event rejects ghost task actor or lease binding",
            bool(isolated_errors(ghost_candidate_event)),
        )
        candidate_mutations: tuple[tuple[str, Callable[[dict[str, Any]], None]], ...] = (
            ("candidate rejects missing workspace isolation", lambda value: value["candidates"][0].__setitem__("workspace_isolated", False)),
            ("candidate rejects missing canonical block", lambda value: value["candidates"][0].__setitem__("canonical_write_blocked", False)),
            ("candidate rejects stale base", lambda value: value["candidates"][0].__setitem__("base_state_sha256", "0" * 64)),
            ("candidate rejects bundle substitution", lambda value: value["candidates"][0].__setitem__("bundle_sha256", "0" * 64)),
            ("candidate rejects out-of-scope path", lambda value: value["candidates"][0].__setitem__("changed_paths", ["outside.py"])),
            ("candidate rejects target write", lambda value: value["candidates"][0].__setitem__("target_repository_write_detected", True)),
            ("candidate rejects artifact-store write", lambda value: value["candidates"][0].__setitem__("artifact_store_write_detected", True)),
            ("candidate rejects verifier access", lambda value: value["candidates"][0].__setitem__("verifier_access_detected", True)),
            ("candidate rejects network access", lambda value: value["candidates"][0].__setitem__("network_access", True)),
            ("candidate rejects credential access", lambda value: value["candidates"][0].__setitem__("credential_access", True)),
            ("candidate rejects shared Git access", lambda value: value["candidates"][0].__setitem__("shared_git_access", True)),
            ("candidate rejects non-main integrator", lambda value: value["integrations"][0].__setitem__("integrator_id", "worker-1")),
            ("candidate rejects selected failed lease", lambda value: value["leases"][0].__setitem__("state", "failed")),
            ("candidate rejects missing production event", lambda value: replace_event_type(value, "candidate-produced", "claim-attempt")),
            ("candidate rejects missing disposition event", lambda value: replace_event_type(value, "candidate-selected", "claim-attempt")),
            ("candidate rejects missing integration event", lambda value: replace_event_type(value, "integration-completed", "claim-attempt")),
            ("candidate rejects selected path absent from diff", lambda value: value["canonical_pre_acceptance"].__setitem__("changed_paths", [])),
        )
        for name, mutate in candidate_mutations:
            bad = clone(isolated.execution)
            mutate(bad)
            record(name, bool(isolated_errors(bad)))

        bad = clone(isolated.execution)
        bad["actors"][1]["workspace_ref"] = str(isolated.repo.resolve())
        bad["candidates"][0]["workspace_ref"] = str(isolated.repo.resolve())
        record("candidate workspace cannot overlap target", bool(isolated_errors(bad)))
        workspace_path = Path(isolated.execution["actors"][1]["workspace_ref"])
        workspace_path.joinpath(".git").mkdir()
        try:
            record("candidate workspace cannot expose Git metadata", bool(isolated_errors(isolated.execution)))
        finally:
            workspace_path.joinpath(".git").rmdir()

        original_bundle = isolated.paths["candidate_bundle"].read_bytes()
        isolated.paths["candidate_bundle"].write_bytes(b"substituted")
        try:
            record("candidate bundle replacement is detected", bool(isolated_errors(isolated.execution)))
        finally:
            isolated.paths["candidate_bundle"].write_bytes(original_bundle)

        def point_candidate_at_bundle(execution: dict[str, Any], bundle: Path) -> None:
            digest = hashlib.sha256(bundle.read_bytes()).hexdigest()
            execution["candidates"][0]["bundle_ref"] = str(bundle.resolve())
            execution["candidates"][0]["bundle_sha256"] = digest
            execution["integrations"][0]["bundle_sha256"] = digest
            for event in execution["events"]:
                if event["event"] in {
                    "candidate-produced",
                    "candidate-selected",
                    "integration-completed",
                }:
                    event["artifact_sha256"] = digest
            execution["resource_usage"]["artifact_bytes"] = bundle.stat().st_size

        repository_hardlink = isolated.root / "repository-hardlink.bundle"
        os.link(isolated.repo / "src" / "example.py", repository_hardlink)
        hardlinked_execution = clone(isolated.execution)
        point_candidate_at_bundle(hardlinked_execution, repository_hardlink)
        repository_hardlink_errors = isolated_errors(hardlinked_execution)
        record(
            "candidate bundle cannot hard-link the target repository",
            any(
                "hard-linked file" in error
                for error in repository_hardlink_errors
            ),
            str(repository_hardlink_errors)[:500],
        )

        protected_hardlink = isolated.root / "protected-hardlink.bundle"
        os.link(isolated.paths["packet"], protected_hardlink)
        protected_execution = clone(isolated.execution)
        point_candidate_at_bundle(protected_execution, protected_hardlink)
        record(
            "candidate bundle cannot hard-link a protected artifact",
            any(
                "hard-linked file" in error
                for error in isolated_errors(protected_execution)
            ),
        )

        workspace_link = workspace_path / "repository-link"
        workspace_link.symlink_to(isolated.repo, target_is_directory=True)
        try:
            record(
                "candidate workspace rejects nested symlink or reparse aliases",
                any(
                    "link or reparse" in error
                    for error in isolated_errors(isolated.execution)
                ),
            )
        finally:
            workspace_link.unlink()

        if os.name == "nt":
            ads_base = isolated.root / "ads-base.bundle"
            ads_base.write_bytes(b"base")
            ads_path = Path(str(ads_base) + ":candidate")
            ads_path.write_bytes(b"alternate stream")
            ads_execution = clone(isolated.execution)
            point_candidate_at_bundle(ads_execution, ads_path)
            record(
                "candidate bundle rejects Windows alternate data streams",
                any("alternate data stream" in error for error in isolated_errors(ads_execution)),
            )

        duplicate = clone(isolated.execution)
        second_candidate = clone(duplicate["candidates"][0])
        second_candidate["id"] = "candidate-2"
        duplicate["candidates"].append(second_candidate)
        second_integration = clone(duplicate["integrations"][0])
        second_integration["candidate_id"] = "candidate-2"
        duplicate["integrations"].append(second_integration)
        record(
            "one DAG node cannot select multiple candidates",
            any("selected more than one" in error for error in isolated_errors(duplicate)),
        )

        def mutate_two_files(repository: Path) -> None:
            repository.joinpath("src", "left.py").write_text(
                "LEFT = 2\n", encoding="utf-8", newline="\n"
            )
            repository.joinpath("src", "right.py").write_text(
                "RIGHT = 2\n", encoding="utf-8", newline="\n"
            )

        two_candidates = make_two_candidate_fixture(
            Path(temporary) / "two-candidate-positive",
            contract_paths=["src/left.py", "src/right.py"],
            extra_sources={"src/left.py": "LEFT = 1\n", "src/right.py": "RIGHT = 1\n"},
            apply_final=mutate_two_files,
            left_paths=["src/left.py"],
            right_paths=["src/right.py"],
            left_operation="modify",
            right_operation="modify",
        )
        status, payload = call_gate(two_candidates)
        record(
            "valid non-overlapping two-candidate chain passes",
            status == 0
            and payload.get("passed") is True
            and payload.get("errors") == [],
            str(payload.get("errors", []))[:500],
        )

        shared_candidates = make_two_candidate_fixture(
            Path(temporary) / "shared-isolated-candidates",
            contract_paths=["src/left.py", "src/right.py"],
            extra_sources={"src/left.py": "LEFT = 1\n", "src/right.py": "RIGHT = 1\n"},
            apply_final=mutate_two_files,
            left_paths=["src/left.py"],
            right_paths=["src/right.py"],
            left_operation="modify",
            right_operation="modify",
        )
        prior_implementation = clone(shared_candidates.report["implementation"])
        shared_candidates.packet = build_packet_v5(
            shared_candidates.packet["contract"],
            risk="medium",
            coordination="shared",
        )
        shared_candidates.plan["packet_sha256"] = shared_candidates.packet[
            "packet_sha256"
        ]
        shared_candidates.plan["mode"] = "shared"
        shared_candidates.plan["communication"] = "root-relay"
        analysis_task = clone(shared_candidates.plan["tasks"][1])
        analysis_task.update(
            id="task-3",
            objective="Challenge both candidate proposals without writing.",
            read_paths=["src/left.py", "src/right.py"],
            candidate_write_paths=[],
        )
        analysis_task["output_contract"]["kind"] = "candidate-proposal"
        shared_candidates.plan["tasks"].append(analysis_task)
        analysis_assignment = clone(shared_candidates.plan["assignments"][1])
        analysis_assignment.update(task_id="task-3", runtime_identity="agent-3")
        shared_candidates.plan["assignments"].append(analysis_assignment)
        for index, assignment in enumerate(
            shared_candidates.plan["assignments"], start=1
        ):
            assignment["runtime_identity"] = f"agent-{index}"

        shared_report = base_report(shared_candidates.packet, participant_count=3)
        shared_report["implementation"] = prior_implementation
        participants = shared_report["deliberation"]["delegation"]["participants"]
        for task, participant in zip(
            shared_candidates.plan["tasks"], participants, strict=True
        ):
            task["output_contract"]["lane_ids"] = participant["lane_ids"]
        shared_candidates.report = shared_report

        for index, actor in enumerate(
            shared_candidates.execution["actors"][1:], start=1
        ):
            actor["id"] = f"agent-{index}"
        for index, lease in enumerate(
            shared_candidates.execution["leases"], start=1
        ):
            lease["actor_id"] = f"agent-{index}"
        for index, candidate in enumerate(
            shared_candidates.execution["candidates"], start=1
        ):
            candidate["actor_id"] = f"agent-{index}"
        for event in shared_candidates.execution["events"]:
            if event["actor_id"] in {"worker-1", "worker-2"}:
                event["actor_id"] = {
                    "worker-1": "agent-1",
                    "worker-2": "agent-2",
                }[event["actor_id"]]

        shared_candidates.execution["actors"].append(
            {
                "id": "agent-3",
                "parent_id": "main-thread",
                "kind": "analysis-worker",
                "task_ids": ["task-3"],
                "workspace_ref": None,
            }
        )
        analysis_lease = clone(shared_candidates.execution["leases"][1])
        analysis_lease.update(
            id="lease-3",
            task_id="task-3",
            actor_id="agent-3",
            capabilities=["enforced_readonly"],
            read_paths=["src/left.py", "src/right.py"],
            candidate_write_paths=[],
        )
        shared_candidates.execution["leases"].append(analysis_lease)
        lease_sequences = ((5, 12), (6, 13), (7, 14))
        for lease, (grant_sequence, terminal_sequence) in zip(
            shared_candidates.execution["leases"], lease_sequences, strict=True
        ):
            lease["grant_sequence"] = grant_sequence
            lease["terminal_sequence"] = terminal_sequence
        bundle1_digest = shared_candidates.execution["candidates"][0][
            "bundle_sha256"
        ]
        bundle2_digest = shared_candidates.execution["candidates"][1][
            "bundle_sha256"
        ]
        board_digest = shared_report["deliberation"]["peer_board_sha256"]
        placeholder = "0" * 64
        shared_candidates.execution["events"] = [
            receipt_event(1, "envelope-sealed", actor_id="main-thread", artifact_sha256=placeholder),
            receipt_event(2, "actor-spawned", actor_id="agent-1", artifact_sha256=placeholder),
            receipt_event(3, "actor-spawned", actor_id="agent-2", artifact_sha256=placeholder),
            receipt_event(4, "actor-spawned", actor_id="agent-3", artifact_sha256=placeholder),
            receipt_event(5, "lease-granted", task_id="task-1", actor_id="agent-1", lease_id="lease-1", artifact_sha256=placeholder),
            receipt_event(6, "lease-granted", task_id="task-2", actor_id="agent-2", lease_id="lease-2", artifact_sha256=placeholder),
            receipt_event(7, "lease-granted", task_id="task-3", actor_id="agent-3", lease_id="lease-3", artifact_sha256=placeholder),
            receipt_event(8, "round1-sealed", actor_id="main-thread", artifact_sha256=board_digest),
            receipt_event(9, "peer-board-relayed", actor_id="main-thread", artifact_sha256=board_digest),
            receipt_event(10, "candidate-produced", task_id="task-1", actor_id="agent-1", lease_id="lease-1", candidate_id="candidate-1", artifact_sha256=bundle1_digest),
            receipt_event(11, "candidate-produced", task_id="task-2", actor_id="agent-2", lease_id="lease-2", candidate_id="candidate-2", artifact_sha256=bundle2_digest),
            receipt_event(12, "lease-completed", task_id="task-1", actor_id="agent-1", lease_id="lease-1"),
            receipt_event(13, "lease-completed", task_id="task-2", actor_id="agent-2", lease_id="lease-2"),
            receipt_event(14, "lease-completed", task_id="task-3", actor_id="agent-3", lease_id="lease-3"),
            receipt_event(15, "candidate-selected", task_id="task-1", actor_id="main-thread", lease_id="lease-1", candidate_id="candidate-1", artifact_sha256=bundle1_digest),
            receipt_event(16, "integration-completed", task_id="task-1", actor_id="main-thread", lease_id="lease-1", candidate_id="candidate-1", artifact_sha256=bundle1_digest),
            receipt_event(17, "candidate-selected", task_id="task-2", actor_id="main-thread", lease_id="lease-2", candidate_id="candidate-2", artifact_sha256=bundle2_digest),
            receipt_event(18, "integration-completed", task_id="task-2", actor_id="main-thread", lease_id="lease-2", candidate_id="candidate-2", artifact_sha256=bundle2_digest),
        ]
        shared_candidates.execution["resource_usage"]["peak_concurrency"] = 3
        rebind_fixture_chain(shared_candidates)
        status, payload = call_gate(shared_candidates)
        record(
            "shared deliberation with isolated candidates and read-only analysis passes",
            status == 0
            and payload.get("passed") is True
            and payload.get("errors") == [],
            str(payload.get("errors", []))[:500],
        )

        def mutate_same_file(repository: Path) -> None:
            repository.joinpath("src", "same.py").write_text(
                "VALUE = 2\n", encoding="utf-8", newline="\n"
            )

        def mutate_modify_delete(repository: Path) -> None:
            repository.joinpath("src", "item.py").unlink()

        def mutate_rename_delete(repository: Path) -> None:
            repository.joinpath("src", "old.py").rename(
                repository.joinpath("src", "new.py")
            )

        def mutate_binary(repository: Path) -> None:
            repository.joinpath("assets", "blob.bin").write_bytes(
                b"\x00\xff\x00changed"
            )

        def mutate_file_directory(repository: Path) -> None:
            path = repository.joinpath("src", "node")
            path.unlink()
            path.mkdir()
            path.joinpath("child.py").write_text(
                "CHILD = True\n", encoding="utf-8", newline="\n"
            )

        conflict_cases = (
            (
                "same-file",
                ["src/same.py"],
                {"src/same.py": "VALUE = 1\n"},
                mutate_same_file,
                ["src/same.py"],
                ["src/same.py"],
                "modify-a",
                "modify-b",
            ),
            (
                "modify-delete",
                ["src/item.py"],
                {"src/item.py": "VALUE = 1\n"},
                mutate_modify_delete,
                ["src/item.py"],
                ["src/item.py"],
                "modify",
                "delete",
            ),
            (
                "rename-delete",
                ["src/new.py", "src/old.py"],
                {"src/old.py": "VALUE = 1\n"},
                mutate_rename_delete,
                ["src/new.py", "src/old.py"],
                ["src/old.py"],
                "rename",
                "delete",
            ),
            (
                "binary",
                ["assets/blob.bin"],
                {"assets/blob.bin": "baseline\n"},
                mutate_binary,
                ["assets/blob.bin"],
                ["assets/blob.bin"],
                "binary-write-a",
                "binary-write-b",
            ),
            (
                "file-directory",
                ["src/node"],
                {"src/node": "file\n"},
                mutate_file_directory,
                ["src/node"],
                ["src/node/child.py"],
                "file-write",
                "directory-write",
            ),
        )
        expected_conflict = (
            "selected candidate paths conflict: candidate-1, candidate-2"
        )
        for (
            name,
            contract_paths,
            extra_sources,
            mutation,
            left_paths,
            right_paths,
            left_operation,
            right_operation,
        ) in conflict_cases:
            conflict = make_two_candidate_fixture(
                Path(temporary) / f"candidate-conflict-{name}",
                contract_paths=contract_paths,
                extra_sources=extra_sources,
                apply_final=mutation,
                left_paths=left_paths,
                right_paths=right_paths,
                left_operation=left_operation,
                right_operation=right_operation,
            )
            status, payload = call_gate(conflict)
            errors = payload.get("errors")
            record(
                f"candidate conflict rejects only {name}",
                status == 1
                and payload.get("passed") is False
                and errors == [expected_conflict],
                str(errors)[:500],
            )

    return results


def threshold_value(value: str) -> float:
    try:
        result = float(value)
    except ValueError as exc:
        raise argparse.ArgumentTypeError("threshold must be numeric") from exc
    if not 0.98 <= result <= 1.0:
        raise argparse.ArgumentTypeError("threshold must be between 0.98 and 1.0")
    return result


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--threshold", type=threshold_value, default=1.0)
    parser.add_argument("--json", action="store_true")
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    results = run_cases()
    passed = sum(1 for item in results if item["passed"])
    rate = passed / len(results) if results else 0.0
    payload = {
        "passed": rate >= args.threshold,
        "threshold": args.threshold,
        "case_pass_rate": rate,
        "passed_cases": passed,
        "total_cases": len(results),
        "failures": [item for item in results if not item["passed"]],
    }
    if args.json:
        print(json.dumps(payload, ensure_ascii=False, indent=2))
    else:
        for item in results:
            print(("PASS" if item["passed"] else "FAIL") + " " + item["name"])
        print(f"{passed}/{len(results)} ({rate:.2%})")
    return 0 if payload["passed"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
