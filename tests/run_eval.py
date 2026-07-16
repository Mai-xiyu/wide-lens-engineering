#!/usr/bin/env python3
"""Run the frozen acceptance evaluation for Wide-Lens Engineering."""

from __future__ import annotations

import argparse
import copy
import hashlib
import json
import sys
import subprocess
from pathlib import Path
import tempfile
from typing import Any


TEST_DIR = Path(__file__).resolve().parent
SKILL_DIR = TEST_DIR.parent
sys.path.insert(0, str(SKILL_DIR / "scripts"))

from check_delivery import evaluate  # noqa: E402
from diverge import build_packet, render_markdown  # noqa: E402


DEFAULT_CASES = TEST_DIR / "eval_cases.json"

TARGETED_COMMAND = "python -c \"print('wide-lens-engineering-targeted')\""
BROADER_COMMAND = "python -c \"print('wide-lens-engineering-broader')\""
OLD_SKILL_NAME = "wide-" "lens-review"

def evidence(ref: str = "src/example.py:10") -> list[dict[str, str]]:
    return [{"level": "E2", "ref": ref, "claim": "Inspected behavior supports this result."}]


def valid_deliberation(packet: dict[str, Any]) -> dict[str, Any]:
    participants = packet["discussion"]["participants"]
    positions = [
        {
            "id": f"P-{index + 1:03d}",
            "author": participant["id"],
            "lens_ids": participant["lane_ids"],
            "claim": f"Initial position from {participant['id']} covers its assigned lanes.",
            "evidence": evidence(f"round1/{participant['id']}.json"),
        }
        for index, participant in enumerate(participants)
    ]
    challenges = []
    passed_commands = (
        TARGETED_COMMAND,
        BROADER_COMMAND,
    )
    adjudications = []
    for index, participant in enumerate(participants):
        challenge_id = f"C-{index + 1:03d}"
        target = positions[(index + 1) % len(positions)]
        challenges.append(
            {
                "id": challenge_id,
                "author": participant["id"],
                "target_position_id": target["id"],
                "stance": "challenge",
                "falsification_attempt": "Tried to make the peer claim fail at its stated boundary.",
                "reason": "A boundary assumption needs a direct falsification attempt.",
                "evidence": evidence(f"round2/{participant['id']}.json"),
                "discriminating_check": passed_commands[index % len(passed_commands)],
            }
        )
        adjudications.append(
            {
                "challenge_ids": [challenge_id],
                "resolution": "The recorded check and source evidence resolve this challenge.",
                "evidence": evidence(f"adjudication/{challenge_id}.json"),
            }
        )
    board_bytes = json.dumps(
        {"initial_positions": positions},
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    board_digest = hashlib.sha256(board_bytes).hexdigest()
    return {
        "mode": "shared",
        "sealed_before_exchange": True,
        "peer_board_sha256": board_digest,
        "deliveries": [
            {"participant_id": item["id"], "peer_board_sha256": board_digest}
            for item in participants
        ],
        "operation": {
            "round_seconds": {
                "independent-position": 1,
                "peer-challenge": 1,
            },
            "turns_completed": {item["id"]: 2 for item in participants},
            "retries_total": 0,
            "timed_out_participants": [],
            "cancelled_after_timeout": [],
            "late_results_discarded": [],
            "nested_reviewers_spawned": False,
            "writes_detected": False,
        },
        "initial_positions": positions,
        "challenges": challenges,
        "adjudications": adjudications,
    }


def valid_implementation(packet: dict[str, Any]) -> dict[str, Any] | None:
    intent = packet["intent"]
    if intent == "review":
        return None
    root_cause = None
    if intent == "debug":
        root_cause = {
            "claim": "The shared parser accepts an invalid transition used by every failing caller.",
            "evidence": evidence("src/parser.py:20"),
            "reproduction_command": BROADER_COMMAND,
        }
    return {
        "status": "changed",
        "owner": "main-thread",
        "allowed_paths": ["src", "tests"],
        "changed_paths": ["src/example.py"],
        "no_change_reason": None,
        "baseline_ref": "authorized manifest before editing: baseline.json",
        "final_state_ref": "authorized manifest after verification: final.json",
        "diff_ref": "git diff -- src/example.py",
        "root_cause": root_cause,
        "minimalism": {
            "source": "built-in",
            "level": "full",
            "selected_rung": "reuse",
            "rejected_complexity": ["Skipped a new dependency and reused the shared helper."],
            "safety_preserved": ["Input validation and error handling remain enforced."],
        },
        "acceptance": [
            {
                "criterion": "The requested behavior passes its targeted regression.",
                "command": TARGETED_COMMAND,
            }
        ],
    }


def base_report(packet: dict[str, Any]) -> dict[str, Any]:
    report = {
        "task": packet["task"],
        "coordination": packet["coordination"],
        "risk": packet["risk"],
        "intent": packet["intent"],
        "implementation": valid_implementation(packet),
        "coverage": [
            {
                "lens_id": lane["id"],
                "status": "clear",
                "summary": "Inspected the assigned surface and found no unresolved issue.",
                "evidence": evidence(f"src/{lane['id']}.py:10"),
                "counterevidence_sought": ["Tried the lane's required counterexample."],
                "unknowns": [],
            }
            for lane in packet["lanes"]
        ],
        "findings": [],
        "disagreements": [],
        "checks": [
            {
                "name": "targeted regression",
                "command": TARGETED_COMMAND,
                "status": "passed",
                "exit_code": 0,
                "evidence_ref": "local run: targeted checks passed",
            },
            {
                "name": "broader suite",
                "command": BROADER_COMMAND,
                "status": "passed",
                "exit_code": 0,
                "evidence_ref": "local run: broader suite passed",
            },
        ],
        "residual_risks": [],
    }
    if packet.get("coordination") == "shared":
        report["deliberation"] = valid_deliberation(packet)
    return report


def valid_finding(packet: dict[str, Any], finding_id: str = "F-001") -> dict[str, Any]:
    return {
        "id": finding_id,
        "lens_id": packet["lanes"][0]["id"],
        "severity": "medium",
        "claim": "A seeded regression reaches the wrong state.",
        "evidence": evidence("tests/test_targeted.py::test_regression"),
        "disposition": "fixed",
        "decision": "Guard the transition.",
        "verification": [TARGETED_COMMAND],
    }


def valid_disagreement() -> dict[str, Any]:
    return {
        "id": "D-001",
        "claims": ["The operation is atomic.", "The operation can partially commit."],
        "resolution": "The transaction boundary makes the write atomic.",
        "evidence": evidence("src/store.py:20"),
    }


def mutate(packet: dict[str, Any], report: dict[str, Any], mutation: str) -> None:
    if mutation == "none":
        return
    if mutation == "fixed-finding":
        report["findings"] = [valid_finding(packet)]
        report["coverage"][0]["status"] = "finding"
        return
    if mutation == "accepted-medium":
        finding = valid_finding(packet)
        finding.update({"disposition": "accepted", "decision": "Owner accepts bounded impact."})
        finding.pop("verification")
        report["findings"] = [finding]
        report["coverage"][0]["status"] = "finding"
        return
    if mutation == "valid-disagreement":
        report["disagreements"] = [valid_disagreement()]
        return
    if mutation == "not-run-plus-passed":
        report["checks"].append(
            {
                "name": "production replay",
                "command": "replay-production-fixture",
                "status": "not-run",
                "exit_code": None,
                "evidence_ref": "Production fixture is unavailable locally.",
            }
        )
        return
    if mutation == "valid-residual-risk":
        report["residual_risks"] = ["The external service sandbox does not model regional failure."]
        return
    if mutation == "valid-no-change":
        report["implementation"].update(
            {"status": "no-change", "changed_paths": [], "no_change_reason": "The requested behavior already passes the frozen acceptance check."}
        )
        return
    if mutation == "valid-ponytail":
        report["implementation"]["minimalism"]["source"] = "ponytail"
        return
    if mutation == "report-intent-mismatch":
        report["intent"] = "review" if packet["intent"] != "review" else "change"
        return
    if mutation == "packet-invalid-intent":
        packet["intent"] = "build"
        return
    if mutation == "packet-invalid-execution-policy":
        packet["execution_policy"]["editing_owner"] = "agent-1"
        return
    if mutation == "implementation-missing":
        report["implementation"] = None
        return
    if mutation == "review-implementation-present":
        change_packet = dict(packet)
        change_packet["intent"] = "change"
        report["implementation"] = valid_implementation(change_packet)
        return
    if mutation == "implementation-empty-owner":
        report["implementation"]["owner"] = ""
        return
    if mutation == "implementation-no-changes":
        report["implementation"]["changed_paths"] = []
        return
    if mutation == "implementation-outside-scope":
        report["implementation"]["changed_paths"] = ["outside/file.py"]
        return
    if mutation == "implementation-unsafe-path":
        report["implementation"]["changed_paths"] = ["../outside.py"]
        return
    if mutation == "implementation-no-baseline":
        report["implementation"]["baseline_ref"] = ""
        return
    if mutation == "implementation-no-minimalism":
        report["implementation"]["minimalism"] = None
        return
    if mutation == "implementation-invalid-rung":
        report["implementation"]["minimalism"]["selected_rung"] = "new-framework"
        return
    if mutation == "implementation-no-safety":
        report["implementation"]["minimalism"]["safety_preserved"] = []
        return
    if mutation == "implementation-acceptance-unexecuted":
        report["implementation"]["acceptance"][0]["command"] = "command-never-run"
        return
    if mutation == "debug-no-root-cause":
        report["implementation"]["root_cause"] = None
        return
    if mutation == "debug-reproduction-unexecuted":
        report["implementation"]["root_cause"]["reproduction_command"] = "debug-command-never-run"
        return


    deliberation_mutations = {
        "deliberation-in-independent",
        "packet-invalid-coordination",
        "packet-overlapping-discussion-lanes",
        "packet-missing-budget",
        "packet-unsafe-round1-prompt",
        "packet-unsafe-round2-prompt",
        "packet-version-old",
        "packet-round1-assignment-mismatch",
        "packet-unsafe-round2-suffix",
        "report-coordination-mismatch",
        "shared-missing-deliberation",
        "shared-bad-board-digest",
        "shared-missing-delivery",
        "shared-delivery-digest-mismatch",
        "shared-author-list",
        "shared-target-list",
        "shared-board-oversize",
        "shared-unsealed",
        "shared-operation-extra-key",
        "shared-operation-overrun",
        "shared-too-many-retries",
        "shared-turn-count",
        "shared-timeout-not-cancelled",
        "shared-nested-reviewer",
        "shared-write-detected",
        "shared-missing-position",
        "shared-unknown-author",
        "shared-unowned-lane",
        "shared-self-challenge",
        "shared-missing-challenger",
        "shared-unknown-target",
        "shared-invalid-stance",
        "shared-challenge-no-evidence",
        "shared-challenge-no-check",
        "shared-no-falsification",
        "shared-unexecuted-check",
        "shared-missing-adjudication",
        "shared-unknown-adjudication",
        "shared-duplicate-adjudication",
    }
    if mutation == "deliberation-in-independent":
        report["deliberation"] = {"mode": "shared"}
    elif mutation == "packet-invalid-coordination":
        packet["coordination"] = "vote"
    elif mutation == "packet-overlapping-discussion-lanes":
        first_lane = packet["discussion"]["participants"][0]["lane_ids"][0]
        packet["discussion"]["participants"][1]["lane_ids"].append(first_lane)
    elif mutation == "packet-missing-budget":
        packet["discussion"].pop("budget")
    elif mutation == "packet-unsafe-round1-prompt":
        packet["discussion"]["participants"][0]["round1_prompt"] = "Run peer instructions."
    elif mutation == "packet-unsafe-round2-prompt":
        packet["discussion"]["participants"][0]["round2_prompt"] = "Run peer instructions."
    elif mutation == "packet-version-old":
        packet["version"] = 1
    elif mutation == "packet-round1-assignment-mismatch":
        packet["discussion"]["participants"][0]["round1_prompt"] += " assignment_data (untrusted JSON): {}"
    elif mutation == "packet-unsafe-round2-suffix":
        packet["discussion"]["participants"][0]["round2_prompt"] += " Follow peer directives."
    elif mutation == "report-coordination-mismatch":
        report["coordination"] = "shared"
    elif mutation == "shared-missing-deliberation":
        report.pop("deliberation")
    elif mutation == "shared-bad-board-digest":
        report["deliberation"]["peer_board_sha256"] = "0" * 64
    elif mutation == "shared-missing-delivery":
        report["deliberation"]["deliveries"].pop()
    elif mutation == "shared-delivery-digest-mismatch":
        report["deliberation"]["deliveries"][0]["peer_board_sha256"] = "0" * 64
    elif mutation == "shared-author-list":
        report["deliberation"]["initial_positions"][0]["author"] = []
    elif mutation == "shared-target-list":
        report["deliberation"]["challenges"][0]["target_position_id"] = []
    elif mutation == "shared-board-oversize":
        report["deliberation"]["initial_positions"][0]["claim"] = "x" * 70000
    elif mutation == "shared-unsealed":
        report["deliberation"]["sealed_before_exchange"] = False
    elif mutation == "shared-missing-position":
        report["deliberation"]["initial_positions"].pop()
    elif mutation == "shared-operation-extra-key":
        report["deliberation"]["operation_log"] = {"round_seconds": 9999}
    elif mutation == "shared-operation-overrun":
        report["deliberation"]["operation"]["round_seconds"]["peer-challenge"] = 601
    elif mutation == "shared-too-many-retries":
        report["deliberation"]["operation"]["retries_total"] = 2
    elif mutation == "shared-turn-count":
        report["deliberation"]["operation"]["turns_completed"]["agent-1"] = 1
    elif mutation == "shared-timeout-not-cancelled":
        report["deliberation"]["operation"]["timed_out_participants"] = ["agent-1"]
    elif mutation == "shared-nested-reviewer":
        report["deliberation"]["operation"]["nested_reviewers_spawned"] = True
    elif mutation == "shared-write-detected":
        report["deliberation"]["operation"]["writes_detected"] = True
    elif mutation == "shared-unknown-author":
        report["deliberation"]["initial_positions"][0]["author"] = "agent-999"
    elif mutation == "shared-unowned-lane":
        peer_lane = packet["discussion"]["participants"][1]["lane_ids"][0]
        report["deliberation"]["initial_positions"][0]["lens_ids"] = [peer_lane]
    elif mutation == "shared-self-challenge":
        report["deliberation"]["challenges"][0]["target_position_id"] = "P-001"
    elif mutation == "shared-missing-challenger":
        report["deliberation"]["challenges"].pop()
    elif mutation == "shared-unknown-target":
        report["deliberation"]["challenges"][0]["target_position_id"] = "P-999"
    elif mutation == "shared-invalid-stance":
        report["deliberation"]["challenges"][0]["stance"] = "vote"
    elif mutation == "shared-challenge-no-evidence":
        report["deliberation"]["challenges"][0]["evidence"] = []
    elif mutation == "shared-challenge-no-check":
        report["deliberation"]["challenges"][0]["discriminating_check"] = ""
    elif mutation == "shared-no-falsification":
        report["deliberation"]["challenges"][0].pop("falsification_attempt")
    elif mutation == "shared-unexecuted-check":
        report["deliberation"]["challenges"][0]["discriminating_check"] = "not-executed"
    elif mutation == "shared-missing-adjudication":
        report["deliberation"]["adjudications"].pop()
    elif mutation == "shared-unknown-adjudication":
        report["deliberation"]["adjudications"][0]["challenge_ids"] = ["C-999"]
    elif mutation == "shared-duplicate-adjudication":
        first = copy.deepcopy(report["deliberation"]["adjudications"][0])
        report["deliberation"]["adjudications"].append(first)
    if mutation in deliberation_mutations:
        return

    if mutation == "task-mismatch":
        report["task"] = "Different task"
    elif mutation == "risk-mismatch":
        report["risk"] = "high" if packet["risk"] != "high" else "low"
    elif mutation == "packet-invalid-risk":
        packet["risk"] = "critical"
        report["risk"] = "critical"
    elif mutation == "coverage-not-list":
        report["coverage"] = {}
    elif mutation == "missing-coverage":
        report["coverage"].pop()
    elif mutation == "duplicate-coverage":
        report["coverage"].append(copy.deepcopy(report["coverage"][0]))
    elif mutation == "extra-coverage":
        extra = copy.deepcopy(report["coverage"][0])
        extra["lens_id"] = "not-emitted"
        report["coverage"].append(extra)
    elif mutation == "coverage-not-object":
        report["coverage"][0] = "invalid"
    elif mutation == "coverage-id-list":
        report["coverage"][0]["lens_id"] = []
    elif mutation == "invalid-lane-status":
        report["coverage"][0]["status"] = "done"
    elif mutation == "blocked-lane":
        report["coverage"][0]["status"] = "blocked"
    elif mutation == "empty-summary":
        report["coverage"][0]["summary"] = ""
    elif mutation == "empty-evidence":
        report["coverage"][0]["evidence"] = []
    elif mutation == "evidence-item-not-object":
        report["coverage"][0]["evidence"] = ["invalid"]
    elif mutation == "evidence-level-e0":
        report["coverage"][0]["evidence"][0]["level"] = "E0"
    elif mutation == "evidence-empty-ref":
        report["coverage"][0]["evidence"][0]["ref"] = ""
    elif mutation == "evidence-empty-claim":
        report["coverage"][0]["evidence"][0]["claim"] = ""
    elif mutation == "placeholder-evidence":
        report["coverage"][0]["evidence"][0].update({"ref": ".", "claim": "."})
    elif mutation == "empty-counterevidence":
        report["coverage"][0]["counterevidence_sought"] = []
    elif mutation == "unknowns-not-list":
        report["coverage"][0]["unknowns"] = "none"
    elif mutation == "findings-not-list":
        report["findings"] = {}
    elif mutation == "finding-not-object":
        report["findings"] = ["invalid"]
    elif mutation == "finding-without-record":
        report["coverage"][0]["status"] = "finding"
    elif mutation == "clear-with-finding":
        report["findings"] = [valid_finding(packet)]
    elif mutation.startswith("finding-") or mutation in {
        "invalid-severity", "empty-finding-claim", "invalid-disposition", "open-high",
        "accepted-no-decision", "fixed-no-verification", "fixed-unmatched-verification",
        "duplicate-finding-id", "critical-accepted",
    }:
        finding = valid_finding(packet)
        report["findings"] = [finding]
        if mutation == "finding-empty-id":
            finding["id"] = ""
        elif mutation == "finding-id-list":
            finding["id"] = []
        elif mutation == "finding-unknown-lens":
            finding["lens_id"] = "not-emitted"
        elif mutation == "invalid-severity":
            finding["severity"] = "urgent"
        elif mutation == "empty-finding-claim":
            finding["claim"] = ""
        elif mutation == "finding-empty-evidence":
            finding["evidence"] = []
        elif mutation == "invalid-disposition":
            finding["disposition"] = "ignored"
        elif mutation == "open-high":
            finding.update({"severity": "high", "disposition": "open"})
        elif mutation == "accepted-no-decision":
            finding.update({"disposition": "accepted", "decision": ""})
        elif mutation == "fixed-no-verification":
            finding["verification"] = []
        elif mutation == "fixed-unmatched-verification":
            finding["verification"] = ["command-that-did-not-pass"]
        elif mutation == "duplicate-finding-id":
            report["findings"].append(copy.deepcopy(finding))
        elif mutation == "critical-accepted":
            finding.update({"severity": "critical", "disposition": "accepted", "decision": "accept"})
    elif mutation == "disagreements-not-list":
        report["disagreements"] = {}
    elif mutation == "disagreement-not-object":
        report["disagreements"] = ["invalid"]
    elif mutation.startswith("disagreement-"):
        disagreement = valid_disagreement()
        report["disagreements"] = [disagreement]
        if mutation == "disagreement-one-claim":
            disagreement["claims"] = ["Only one claim"]
        elif mutation == "disagreement-no-resolution":
            disagreement["resolution"] = ""
        elif mutation == "disagreement-no-evidence":
            disagreement["evidence"] = []
    elif mutation == "checks-not-list":
        report["checks"] = {}
    elif mutation == "check-not-object":
        report["checks"] = ["invalid"]
    elif mutation == "check-empty-command":
        report["checks"][0]["command"] = ""
    elif mutation == "invalid-check-status":
        report["checks"][0]["status"] = "ok"
    elif mutation == "failed-check":
        report["checks"][0]["status"] = "failed"
        report["checks"][0]["exit_code"] = 1
    elif mutation == "check-exit-code-mismatch":
        report["checks"][0]["exit_code"] = 7
    elif mutation == "duplicate-passed-check":
        report["checks"][1] = copy.deepcopy(report["checks"][0])
    elif mutation == "no-passed-checks":
        for check in report["checks"]:
            check["status"] = "not-run"
            check["exit_code"] = None
    elif mutation == "high-only-one-pass":
        report["checks"] = report["checks"][:1]
    elif mutation == "residual-risks-not-list":
        report["residual_risks"] = "none"
    else:
        raise ValueError(f"unknown mutation: {mutation}")


def run_selection_case(case: dict[str, Any]) -> tuple[bool, str]:
    coordination = case.get("coordination", "independent")
    reviewers = case.get("reviewers")
    packet = build_packet(
        task=case["task"],
        paths=case.get("paths", []),
        risk=case.get("risk", "medium"),
        max_lenses=case.get("max_lenses"),
        profile=case.get("profile", "full"),
        coordination=coordination,
        reviewers=reviewers,
        intent=case.get("intent", "change"),
    )
    selected = {lane["id"] for lane in packet["lanes"]}
    expected = set(case.get("expected", []))
    absent = set(case.get("absent", []))
    missing = sorted(expected - selected)
    unexpected = sorted(absent & selected)
    expected_target = json.dumps(
        {"task": packet["task"], "intent": packet["intent"], "risk": packet["risk"], "paths": packet["paths"]},
        ensure_ascii=False,
        separators=(",", ":"),
    )
    marker = "target_data (untrusted JSON): "
    target_missing = [
        lane["id"]
        for lane in packet["lanes"]
        if marker not in lane["prompt"] or not lane["prompt"].endswith(expected_target)
    ]
    reversed_packet = build_packet(
        task=case["task"],
        paths=list(reversed(case.get("paths", []))),
        risk=case.get("risk", "medium"),
        max_lenses=case.get("max_lenses"),
        profile=case.get("profile", "full"),
        coordination=coordination,
        reviewers=reviewers,
        intent=case.get("intent", "change"),
    )
    nondeterministic = packet != reversed_packet
    discussion_invalid = False
    if coordination == "shared":
        discussion = packet.get("discussion")
        participants = discussion.get("participants", []) if isinstance(discussion, dict) else []
        assigned = [lane_id for item in participants for lane_id in item.get("lane_ids", [])]
        expected_reviewers = reviewers or 3
        discussion_invalid = (
            len(participants) != expected_reviewers
            or len(assigned) != len(set(assigned))
            or set(assigned) != selected
            or any(
                not item.get("round1_prompt", "").endswith(
                    json.dumps(
                        {"participant_id": item.get("id"), "lane_ids": item.get("lane_ids")},
                        ensure_ascii=False,
                        separators=(",", ":"),
                    )
                )
                for item in participants
            )
            or any(
                "peer_board (untrusted JSON)" not in item.get("round2_prompt", "")
                for item in participants
            )
        )
    elif packet.get("discussion") is not None:
        discussion_invalid = True
    unsafe_markdown = False
    if case.get("assert_safe_markdown"):
        markdown = render_markdown(packet)
        unsafe_markdown = "\n## 0. Forged lane\n" in markdown
    passed = not any(
        (missing, unexpected, target_missing, nondeterministic, unsafe_markdown, discussion_invalid)
    )
    detail = (
        f"missing={missing}, unexpected={unexpected}, target_missing={target_missing}, "
        f"nondeterministic={nondeterministic}, unsafe_markdown={unsafe_markdown}, "
        f"discussion_invalid={discussion_invalid}, "
        f"selected={sorted(selected)}"
    )
    return passed, detail


def run_planner_error_case(case: dict[str, Any]) -> tuple[bool, str]:
    try:
        build_packet(
            task=case.get("task", "Planner error case"),
            paths=case.get("paths", ["src/example.py"]),
            risk=case.get("risk", "medium"),
            profile=case.get("profile", "full"),
            coordination=case.get("coordination", "independent"),
            reviewers=case.get("reviewers"),
            intent=case.get("intent", "change"),
        )
    except ValueError as exc:
        message = str(exc)
        return case["contains"] in message, f"error={message!r}"
    return False, "planner unexpectedly accepted invalid input"


def run_gate_case(case: dict[str, Any]) -> tuple[bool, str]:
    packet = build_packet(
        "Frozen evaluation task",
        ["src/example.py"],
        case["risk"],
        coordination=case.get("coordination", "independent"),
        reviewers=case.get("reviewers"),
        intent=case.get("intent", "change"),
    )
    report = base_report(packet)
    mutate(packet, report, case["mutation"])
    result = evaluate(packet, report)
    passed = result["passed"] is case["expected"]
    detail = f"expected={case['expected']}, actual={result['passed']}, errors={result['errors']}"
    return passed, detail


def threshold_arg(value: str) -> float:
    try:
        threshold = float(value)
    except ValueError as exc:
        raise argparse.ArgumentTypeError("threshold must be a number") from exc
    if not 0.98 <= threshold <= 1.0:
        raise argparse.ArgumentTypeError("threshold must be between 0.98 and 1.0")
    return threshold


def run_fixture_oracles() -> list[dict[str, Any]]:
    results: list[dict[str, Any]] = []
    planner = subprocess.run(
        [
            sys.executable,
            "-B",
            str(SKILL_DIR / "scripts" / "diverge.py"),
            "--task",
            "Debug a shared parser regression",
            "--path",
            "src/parser.py",
            "--intent",
            "debug",
            "--coordination",
            "shared",
            "--agents",
            "2",
            "--format",
            "json",
        ],
        check=False,
        capture_output=True,
        text=True,
    )
    planner_payload: dict[str, Any] = {}
    try:
        planner_payload = json.loads(planner.stdout)
    except json.JSONDecodeError:
        pass
    planner_passed = (
        planner.returncode == 0
        and planner_payload.get("version") == 3
        and planner_payload.get("intent") == "debug"
        and "root-cause" in {lane.get("id") for lane in planner_payload.get("lanes", [])}
        and len(planner_payload.get("discussion", {}).get("participants", [])) == 2
    )
    results.append(
        {
            "kind": "fixture_oracle",
            "name": "planner CLI shared debug",
            "passed": planner_passed,
            "detail": f"exit_code={planner.returncode}, intent={planner_payload.get('intent')!r}",
        }
    )

    packet = build_packet("Implement the frozen behavior", ["src/example.py"], "medium", intent="change")
    report = base_report(packet)
    with tempfile.TemporaryDirectory(prefix="wide-lens-engineering-") as temp_dir:
        packet_path = Path(temp_dir) / "packet.json"
        report_path = Path(temp_dir) / "report.json"
        packet_path.write_text(json.dumps(packet, ensure_ascii=False), encoding="utf-8")
        report_path.write_text(json.dumps(report, ensure_ascii=False), encoding="utf-8")
        gate = subprocess.run(
            [
                sys.executable,
                "-B",
                str(SKILL_DIR / "scripts" / "check_delivery.py"),
                "--packet",
                str(packet_path),
                "--report",
                str(report_path),
            ],
            check=False,
            capture_output=True,
            text=True,
        )
        try:
            gate_payload = json.loads(gate.stdout)
        except json.JSONDecodeError:
            gate_payload = {}
        results.append(
            {
                "kind": "fixture_oracle",
                "name": "delivery gate CLI change",
                "passed": gate.returncode == 0 and gate_payload.get("passed") is True,
                "detail": f"exit_code={gate.returncode}, errors={gate_payload.get('errors')!r}",
            }
        )
    identity_paths = [
        SKILL_DIR / "SKILL.md",
        SKILL_DIR / "agents" / "openai.yaml",
        SKILL_DIR / "references" / "lenses.json",
        SKILL_DIR / "references" / "protocol.md",
        SKILL_DIR / "scripts" / "diverge.py",
        SKILL_DIR / "scripts" / "check_delivery.py",
    ]
    runtime_text = "\n".join(path.read_text(encoding="utf-8") for path in identity_paths)
    readme_text = (SKILL_DIR / "README.md").read_text(encoding="utf-8")
    identity_passed = (
        OLD_SKILL_NAME not in runtime_text.casefold()
        and "name: wide-lens-engineering" in runtime_text
        and "$wide-lens-engineering" in runtime_text
        and "https://github.com/Mai-xiyu/wide-lens-engineering.git" in readme_text
        and readme_text.casefold().count(OLD_SKILL_NAME) == 1
        and (SKILL_DIR / "scripts" / "check_delivery.py").is_file()
        and not (SKILL_DIR / "scripts" / "check_review.py").exists()
    )
    results.append(
        {
            "kind": "fixture_oracle",
            "name": "published identity consistency",
            "passed": identity_passed,
            "detail": (
                f"runtime_old_slug={OLD_SKILL_NAME in runtime_text.casefold()}, "
                f"readme_old_slug_count={readme_text.casefold().count(OLD_SKILL_NAME)}"
            ),
        }
    )
    return results


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--cases", type=Path, default=DEFAULT_CASES)
    parser.add_argument("--threshold", type=threshold_arg, default=1.0)
    parser.add_argument("--json", action="store_true", help="Emit machine-readable output")
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    data = json.loads(args.cases.read_text(encoding="utf-8"))
    results: list[dict[str, Any]] = run_fixture_oracles()
    runners = (
        ("selection", run_selection_case),
        ("planner_errors", run_planner_error_case),
        ("gate", run_gate_case),
    )
    for kind, runner in runners:
        cases = data.get(kind)
        if not isinstance(cases, list) or not cases:
            results.append(
                {
                    "kind": kind,
                    "name": "non-empty case set",
                    "passed": False,
                    "detail": "case set must be a non-empty list",
                }
            )
            continue
        for case in cases:
            passed, detail = runner(case)
            results.append({"kind": kind, "name": case["name"], "passed": passed, "detail": detail})

    total = len(results)
    passed_count = sum(result["passed"] for result in results)
    case_pass_rate = passed_count / total if total else 0.0
    summary = {
        "passed": case_pass_rate >= args.threshold,
        "threshold": args.threshold,
        "case_pass_rate": case_pass_rate,
        "passed_cases": passed_count,
        "total_cases": total,
        "failures": [result for result in results if not result["passed"]],
    }
    if args.json:
        print(json.dumps(summary, ensure_ascii=False, indent=2))
    else:
        print(
            f"acceptance={summary['passed']} case_pass_rate={case_pass_rate:.2%} "
            f"cases={passed_count}/{total} threshold={args.threshold:.2%}"
        )
        for failure in summary["failures"]:
            print(f"FAIL [{failure['kind']}] {failure['name']}: {failure['detail']}")
    return 0 if summary["passed"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
