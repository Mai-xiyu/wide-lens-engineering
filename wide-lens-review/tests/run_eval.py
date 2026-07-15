#!/usr/bin/env python3
"""Run the frozen 100-plus-case acceptance evaluation for wide-lens-review."""

from __future__ import annotations

import argparse
import copy
import json
import sys
from pathlib import Path
from typing import Any


TEST_DIR = Path(__file__).resolve().parent
SKILL_DIR = TEST_DIR.parent
sys.path.insert(0, str(SKILL_DIR / "scripts"))

from check_review import evaluate  # noqa: E402
from diverge import build_packet, render_markdown  # noqa: E402


DEFAULT_CASES = TEST_DIR / "eval_cases.json"


def evidence(ref: str = "src/example.py:10") -> list[dict[str, str]]:
    return [{"level": "E2", "ref": ref, "claim": "Inspected behavior supports this result."}]


def base_report(packet: dict[str, Any]) -> dict[str, Any]:
    return {
        "task": packet["task"],
        "risk": packet["risk"],
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
                "command": "python -m unittest tests.test_targeted",
                "status": "passed",
                "exit_code": 0,
                "evidence_ref": "local run: targeted checks passed",
            },
            {
                "name": "broader suite",
                "command": "python -m unittest discover",
                "status": "passed",
                "exit_code": 0,
                "evidence_ref": "local run: broader suite passed",
            },
        ],
        "residual_risks": [],
    }


def valid_finding(packet: dict[str, Any], finding_id: str = "F-001") -> dict[str, Any]:
    return {
        "id": finding_id,
        "lens_id": packet["lanes"][0]["id"],
        "severity": "medium",
        "claim": "A seeded regression reaches the wrong state.",
        "evidence": evidence("tests/test_targeted.py::test_regression"),
        "disposition": "fixed",
        "decision": "Guard the transition.",
        "verification": ["python -m unittest tests.test_targeted"],
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
    packet = build_packet(
        task=case["task"],
        paths=case.get("paths", []),
        risk=case.get("risk", "medium"),
        max_lenses=case.get("max_lenses"),
        profile=case.get("profile", "full"),
    )
    selected = {lane["id"] for lane in packet["lanes"]}
    expected = set(case.get("expected", []))
    absent = set(case.get("absent", []))
    missing = sorted(expected - selected)
    unexpected = sorted(absent & selected)
    expected_target = json.dumps(
        {"task": packet["task"], "risk": packet["risk"], "paths": packet["paths"]},
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
    )
    nondeterministic = packet != reversed_packet
    unsafe_markdown = False
    if case.get("assert_safe_markdown"):
        markdown = render_markdown(packet)
        unsafe_markdown = "\n## 0. Forged lane\n" in markdown
    passed = not missing and not unexpected and not target_missing and not nondeterministic and not unsafe_markdown
    detail = (
        f"missing={missing}, unexpected={unexpected}, target_missing={target_missing}, "
        f"nondeterministic={nondeterministic}, unsafe_markdown={unsafe_markdown}, "
        f"selected={sorted(selected)}"
    )
    return passed, detail


def run_gate_case(case: dict[str, Any]) -> tuple[bool, str]:
    packet = build_packet("Frozen evaluation task", ["src/example.py"], case["risk"])
    report = base_report(packet)
    mutate(packet, report, case["mutation"])
    result = evaluate(packet, report)
    passed = result["passed"] is case["expected"]
    detail = f"expected={case['expected']}, actual={result['passed']}, errors={result['errors']}"
    return passed, detail


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--cases", type=Path, default=DEFAULT_CASES)
    parser.add_argument("--threshold", type=float, default=0.98)
    parser.add_argument("--json", action="store_true", help="Emit machine-readable output")
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    data = json.loads(args.cases.read_text(encoding="utf-8"))
    results: list[dict[str, Any]] = []
    for kind, runner in (("selection", run_selection_case), ("gate", run_gate_case)):
        for case in data[kind]:
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
