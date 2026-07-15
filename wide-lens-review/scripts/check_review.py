#!/usr/bin/env python3
"""Validate a wide-lens review report against its emitted packet."""

from __future__ import annotations

import argparse
import json
from collections import Counter
from pathlib import Path
from typing import Any


VALID_LEVELS = {"E1", "E2", "E3"}
VALID_LANE_STATUSES = {"clear", "finding", "blocked"}
VALID_SEVERITIES = {"critical", "high", "medium", "low"}
VALID_DISPOSITIONS = {"fixed", "accepted", "not-applicable", "open"}
VALID_CHECK_STATUSES = {"passed", "failed", "not-run"}
VALID_RISKS = {"low", "medium", "high"}
VALID_PROFILES = {"light", "full"}
PLACEHOLDERS = {".", "-", "n/a", "na", "none", "unknown", "tbd"}


def _nonempty_string(value: Any) -> bool:
    return isinstance(value, str) and bool(value.strip())


def _concrete_string(value: Any) -> bool:
    return _nonempty_string(value) and value.strip().casefold() not in PLACEHOLDERS


def _string_list(value: Any, minimum: int = 1) -> bool:
    return (
        isinstance(value, list)
        and len(value) >= minimum
        and all(_nonempty_string(item) for item in value)
    )


def _validate_evidence(value: Any, location: str, errors: list[str]) -> None:
    if not isinstance(value, list) or not value:
        errors.append(f"{location}: evidence must be a non-empty list")
        return
    for index, item in enumerate(value):
        item_location = f"{location}.evidence[{index}]"
        if not isinstance(item, dict):
            errors.append(f"{item_location}: must be an object")
            continue
        if item.get("level") not in VALID_LEVELS:
            errors.append(f"{item_location}.level: expected one of {sorted(VALID_LEVELS)}")
        if not _concrete_string(item.get("ref")):
            errors.append(f"{item_location}.ref: must be concrete, not empty or a placeholder")
        if not _concrete_string(item.get("claim")):
            errors.append(f"{item_location}.claim: must be concrete, not empty or a placeholder")


def evaluate(packet: Any, report: Any) -> dict[str, Any]:
    errors: list[str] = []
    if not isinstance(packet, dict):
        return {"passed": False, "errors": ["packet: must be an object"]}
    if not isinstance(report, dict):
        return {"passed": False, "errors": ["report: must be an object"]}

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
    if packet_risk not in VALID_RISKS:
        errors.append(f"packet.risk: expected one of {sorted(VALID_RISKS)}")
    if packet.get("profile") not in VALID_PROFILES:
        errors.append(f"packet.profile: expected one of {sorted(VALID_PROFILES)}")
    if packet.get("profile") == "light" and packet_risk != "low":
        errors.append("packet.profile: light is allowed only for low risk")

    if report.get("task") != packet.get("task"):
        errors.append("report.task: must exactly match packet.task")
    if report.get("risk") != packet.get("risk"):
        errors.append("report.risk: must exactly match packet.risk")

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
        if not _nonempty_string(lane.get("lens_id")):
            errors.append(f"{location}.lens_id: must be a non-empty string")
        if lane.get("status") not in VALID_LANE_STATUSES:
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
        if _nonempty_string(finding.get("id")):
            finding_ids.append(finding["id"])
        if not _nonempty_string(finding.get("id")):
            errors.append(f"{location}.id: must be non-empty")
        if finding.get("lens_id") not in expected_lanes:
            errors.append(f"{location}.lens_id: must reference an emitted lane")
        else:
            findings_by_lens[finding["lens_id"]] += 1
        if finding.get("severity") not in VALID_SEVERITIES:
            errors.append(f"{location}.severity: expected one of {sorted(VALID_SEVERITIES)}")
        if not _nonempty_string(finding.get("claim")):
            errors.append(f"{location}.claim: must be non-empty")
        _validate_evidence(finding.get("evidence"), location, errors)
        disposition = finding.get("disposition")
        if disposition not in VALID_DISPOSITIONS:
            errors.append(f"{location}.disposition: expected one of {sorted(VALID_DISPOSITIONS)}")
        if disposition == "open" and finding.get("severity") in {"critical", "high"}:
            errors.append(f"{location}: critical/high findings cannot remain open")
        if disposition == "accepted" and finding.get("severity") == "critical":
            errors.append(f"{location}: critical findings cannot be accepted")
        if disposition == "accepted" and not _nonempty_string(finding.get("decision")):
            errors.append(f"{location}.decision: accepted risk needs a decision")
        if disposition == "not-applicable" and not _nonempty_string(finding.get("decision")):
            errors.append(f"{location}.decision: not-applicable finding needs an evidence-backed decision")
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
        if status not in VALID_CHECK_STATUSES:
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

    minimum_passed = 2 if packet.get("risk") == "high" else 1
    if len(passed_commands) < minimum_passed:
        errors.append(
            f"report.checks: need at least {minimum_passed} distinct passing check(s), "
            f"found {len(passed_commands)}"
        )

    for location, commands in fixed_verifications:
        if not set(commands) & passed_commands:
            errors.append(f"{location}.verification: must reference at least one passed check command")

    residual_risks = report.get("residual_risks")
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
        },
    }


def load_json(path: Path) -> Any:
    with path.open("r", encoding="utf-8") as handle:
        return json.load(handle)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--packet", type=Path, required=True)
    parser.add_argument("--report", type=Path, required=True)
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    try:
        result = evaluate(load_json(args.packet), load_json(args.report))
    except (OSError, json.JSONDecodeError) as exc:
        print(json.dumps({"passed": False, "errors": [str(exc)]}, ensure_ascii=False, indent=2))
        return 2
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0 if result["passed"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
