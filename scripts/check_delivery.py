#!/usr/bin/env python3
"""Validate a Wide-Lens Engineering delivery report against its packet."""

from __future__ import annotations

import argparse
import json
import hashlib
from collections import Counter
from pathlib import Path
from typing import Any
from diverge import build_participant_prompts



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
VALID_MINIMALISM_RUNGS = {"reuse", "stdlib", "native", "existing-dependency", "minimal-custom"}
PLACEHOLDERS = {".", "-", "n/a", "na", "none", "unknown", "tbd"}

EXPECTED_DISCUSSION_BUDGET = {
    "max_participants": 3,
    "max_turns_per_participant": 2,
    "max_round_seconds": 600,
    "max_retries_total": 1,
    "max_peer_board_bytes": 65536,
    "allow_nested_reviewers": False,
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


def _validate_evidence(value: Any, location: str, errors: list[str]) -> None:
    if not isinstance(value, list) or not value:
        errors.append(f"{location}: evidence must be a non-empty list")
        return
    for index, item in enumerate(value):
        item_location = f"{location}.evidence[{index}]"
        if not isinstance(item, dict):
            errors.append(f"{item_location}: must be an object")
            continue
        if not _one_of(item.get("level"), VALID_LEVELS):
            errors.append(f"{item_location}.level: expected one of {sorted(VALID_LEVELS)}")
        if not _concrete_string(item.get("ref")):
            errors.append(f"{item_location}.ref: must be concrete, not empty or a placeholder")
        if not _concrete_string(item.get("claim")):
            errors.append(f"{item_location}.claim: must be concrete, not empty or a placeholder")


def _canonical_json_bytes(value: Any) -> bytes:
    return json.dumps(
        value, ensure_ascii=False, sort_keys=True, separators=(",", ":")
    ).encode("utf-8")


def _packet_participants(
    packet: dict[str, Any], expected_lanes: set[str], errors: list[str]
) -> dict[str, set[str]]:
    coordination = packet.get("coordination")
    discussion = packet.get("discussion")
    if not _one_of(coordination, VALID_COORDINATION):
        errors.append(f"packet.coordination: expected one of {sorted(VALID_COORDINATION)}")
        return {}
    if coordination == "independent":
        if discussion is not None:
            errors.append("packet.discussion: independent coordination requires null")
        return {}
    if packet.get("profile") != "full":
        errors.append("packet.coordination: shared coordination requires the full profile")
    if not isinstance(discussion, dict):
        errors.append("packet.discussion: shared coordination requires an object")
        return {}
    if discussion.get("mode") != "shared":
        errors.append("packet.discussion.mode: must be shared")
    if discussion.get("sealed_round1") is not True:
        errors.append("packet.discussion.sealed_round1: must be true")
    expected_rounds = ["independent-position", "peer-challenge", "evidence-adjudication"]
    if discussion.get("rounds") != expected_rounds:
        errors.append(f"packet.discussion.rounds: must equal {expected_rounds}")
    if discussion.get("relay") != (
        "Main thread relays the complete structured peer board between the same participants."
    ):
        errors.append("packet.discussion.relay: must preserve the complete board for every participant")
    if discussion.get("adjudicator") != "main-thread":
        errors.append("packet.discussion.adjudicator: must be main-thread")
    if discussion.get("decision_rule") != "Resolve claims by discriminating evidence, never by vote or confidence.":
        errors.append("packet.discussion.decision_rule: must be evidence-based and non-voting")
    if discussion.get("budget") != EXPECTED_DISCUSSION_BUDGET:
        errors.append("packet.discussion.budget: must match the bounded execution policy")
    participants = discussion.get("participants")
    if not isinstance(participants, list) or not 2 <= len(participants) <= 3:
        errors.append("packet.discussion.participants: must contain 2 or 3 participants")
        return {}
    assignments: dict[str, set[str]] = {}
    assigned_lanes: list[str] = []
    for index, participant in enumerate(participants):
        location = f"packet.discussion.participants[{index}]"
        if not isinstance(participant, dict):
            errors.append(f"{location}: must be an object")
            continue
        participant_id = participant.get("id")
        if not _nonempty_string(participant_id):
            errors.append(f"{location}.id: must be non-empty")
            continue
        if participant_id in assignments:
            errors.append(f"{location}.id: duplicate participant id")
            continue
        lane_ids = participant.get("lane_ids")
        if not _string_list(lane_ids):
            errors.append(f"{location}.lane_ids: must be a non-empty string list")
            assignments[participant_id] = set()
        else:
            lane_set = set(lane_ids)
            if len(lane_ids) != len(lane_set):
                errors.append(f"{location}.lane_ids: duplicate lane ids")
            unknown = sorted(lane_set - expected_lanes)
            if unknown:
                errors.append(f"{location}.lane_ids: unknown lanes {unknown}")
            assignments[participant_id] = lane_set
            assigned_lanes.extend(lane_ids)
        round1_prompt = participant.get("round1_prompt")
        round2_prompt = participant.get("round2_prompt")
        if _string_list(lane_ids):
            expected_round1, expected_round2 = build_participant_prompts(participant_id, lane_ids)
            if round1_prompt != expected_round1:
                errors.append(f"{location}.round1_prompt: must match the canonical assignment prompt")
            if round2_prompt != expected_round2:
                errors.append(f"{location}.round2_prompt: must match the canonical relay prompt")
    duplicate_lanes = sorted(item for item, count in Counter(assigned_lanes).items() if count > 1)
    if duplicate_lanes:
        errors.append(f"packet.discussion.participants: lanes assigned more than once {duplicate_lanes}")
    missing_lanes = sorted(expected_lanes - set(assigned_lanes))
    if missing_lanes:
        errors.append(f"packet.discussion.participants: unassigned lanes {missing_lanes}")
    return assignments

def _validate_operation(
    value: Any, assignments: dict[str, set[str]], errors: list[str]
) -> None:
    location = "report.deliberation.operation"
    if not isinstance(value, dict):
        errors.append(f"{location}: must be an object")
        return
    expected_keys = {
        "round_seconds",
        "turns_completed",
        "retries_total",
        "timed_out_participants",
        "cancelled_after_timeout",
        "late_results_discarded",
        "nested_reviewers_spawned",
        "writes_detected",
    }
    if set(value) != expected_keys:
        errors.append(f"{location}: keys must equal {sorted(expected_keys)}")
    round_seconds = value.get("round_seconds")
    expected_rounds = {"independent-position", "peer-challenge"}
    if not isinstance(round_seconds, dict) or set(round_seconds) != expected_rounds:
        errors.append(f"{location}.round_seconds: must record both reviewer rounds")
    else:
        for round_name, seconds in round_seconds.items():
            if (
                not isinstance(seconds, int)
                or isinstance(seconds, bool)
                or not 0 <= seconds <= EXPECTED_DISCUSSION_BUDGET["max_round_seconds"]
            ):
                errors.append(f"{location}.round_seconds.{round_name}: outside the budget")
    turns = value.get("turns_completed")
    if not isinstance(turns, dict) or set(turns) != set(assignments):
        errors.append(f"{location}.turns_completed: must cover every participant exactly once")
    else:
        for participant_id, count in turns.items():
            if count != EXPECTED_DISCUSSION_BUDGET["max_turns_per_participant"]:
                errors.append(f"{location}.turns_completed.{participant_id}: must equal 2")
    retries = value.get("retries_total")
    if (
        not isinstance(retries, int)
        or isinstance(retries, bool)
        or not 0 <= retries <= EXPECTED_DISCUSSION_BUDGET["max_retries_total"]
    ):
        errors.append(f"{location}.retries_total: outside the budget")

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
    if value.get("nested_reviewers_spawned") is not False:
        errors.append(f"{location}.nested_reviewers_spawned: must be false")
    if value.get("writes_detected") is not False:
        errors.append(f"{location}.writes_detected: must be false")



def _validate_deliberation(
    value: Any,
    assignments: dict[str, set[str]],
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
        "operation",
    }
    if set(value) != expected_keys:
        errors.append(f"report.deliberation: keys must equal {sorted(expected_keys)}")
    _validate_operation(value.get("operation"), assignments, errors)


    positions = value.get("initial_positions")
    if not isinstance(positions, list) or not positions:
        errors.append("report.deliberation.initial_positions: must be a non-empty list")
        positions = []
    board_bytes = _canonical_json_bytes({"initial_positions": positions})
    board_digest = hashlib.sha256(board_bytes).hexdigest()
    if len(board_bytes) > EXPECTED_DISCUSSION_BUDGET["max_peer_board_bytes"]:
        errors.append("report.deliberation.initial_positions: peer board exceeds the byte limit")
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
    for index, position in enumerate(positions):
        location = f"report.deliberation.initial_positions[{index}]"
        if not isinstance(position, dict):
            errors.append(f"{location}: must be an object")
            continue
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
        "ponytail_level": "full",
        "minimalism_ladder": [
            "reuse",
            "stdlib",
            "native",
            "existing-dependency",
            "minimal-custom",
        ],
    }


def _repo_path(value: Any) -> str | None:
    if not _nonempty_string(value):
        return None
    normalized = value.strip().replace("\\", "/")
    parts = normalized.split("/")
    if (
        normalized.startswith("/")
        or ":" in parts[0]
        or any(part in {"", ".", ".."} for part in parts)
    ):
        return None
    return "/".join(parts)


def _validate_implementation(value: Any, intent: Any, errors: list[str]) -> list[str]:
    required_commands: list[str] = []
    if intent == "review":
        if value is not None:
            errors.append("report.implementation: review intent requires null")
        return required_commands
    if intent not in CODING_INTENTS:
        return required_commands
    location = "report.implementation"
    if not isinstance(value, dict):
        errors.append(f"{location}: {intent} intent requires an object")
        return required_commands
    expected_keys = {
        "status", "owner", "allowed_paths", "changed_paths", "no_change_reason",
        "baseline_ref", "final_state_ref", "diff_ref", "root_cause",
        "minimalism", "acceptance",
    }
    if set(value) != expected_keys:
        errors.append(f"{location}: keys must equal {sorted(expected_keys)}")
    status = value.get("status")
    if not _one_of(status, VALID_IMPLEMENTATION_STATUS):
        errors.append(f"{location}.status: expected one of {sorted(VALID_IMPLEMENTATION_STATUS)}")
    if value.get("owner") != "main-thread":
        errors.append(f"{location}.owner: must be main-thread")

    allowed_raw = value.get("allowed_paths")
    changed_raw = value.get("changed_paths")
    allowed = [_repo_path(item) for item in allowed_raw] if isinstance(allowed_raw, list) else []
    changed = [_repo_path(item) for item in changed_raw] if isinstance(changed_raw, list) else []
    if not allowed_raw or not isinstance(allowed_raw, list) or any(item is None for item in allowed):
        errors.append(f"{location}.allowed_paths: must be a non-empty list of relative repository paths")
    if not isinstance(changed_raw, list) or any(item is None for item in changed):
        errors.append(f"{location}.changed_paths: must be a list of relative repository paths")
    if len(allowed) != len(set(allowed)):
        errors.append(f"{location}.allowed_paths: duplicate paths")
    if len(changed) != len(set(changed)):
        errors.append(f"{location}.changed_paths: duplicate paths")
    if status == "changed" and not changed_raw:
        errors.append(f"{location}.changed_paths: changed status requires at least one path")
    if status == "no-change" and changed_raw:
        errors.append(f"{location}.changed_paths: no-change status requires an empty list")
    reason = value.get("no_change_reason")
    if status == "no-change" and not _concrete_string(reason):
        errors.append(f"{location}.no_change_reason: no-change status needs a concrete reason")
    if status == "changed" and reason is not None:
        errors.append(f"{location}.no_change_reason: changed status requires null")
    allowed_values = [item for item in allowed if item is not None]
    for path in (item for item in changed if item is not None):
        if not any(path == root or path.startswith(root.rstrip("/") + "/") for root in allowed_values):
            errors.append(f"{location}.changed_paths: path outside allowed scope {path!r}")
    for field in ("baseline_ref", "final_state_ref", "diff_ref"):
        if not _concrete_string(value.get(field)):
            errors.append(f"{location}.{field}: must be concrete")

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
            else:
                required_commands.append(command)

    minimalism = value.get("minimalism")
    if not isinstance(minimalism, dict) or set(minimalism) != {
        "source", "level", "selected_rung", "rejected_complexity", "safety_preserved"
    }:
        errors.append(f"{location}.minimalism: must contain the complete minimalism decision")
    else:
        if not _one_of(minimalism.get("source"), VALID_MINIMALISM_SOURCES):
            errors.append(f"{location}.minimalism.source: expected one of {sorted(VALID_MINIMALISM_SOURCES)}")
        if minimalism.get("level") not in {"lite", "full", "ultra"}:
            errors.append(f"{location}.minimalism.level: expected lite, full, or ultra")
        if not _one_of(minimalism.get("selected_rung"), VALID_MINIMALISM_RUNGS):
            errors.append(f"{location}.minimalism.selected_rung: expected one of {sorted(VALID_MINIMALISM_RUNGS)}")
        rejected = minimalism.get("rejected_complexity")
        if not isinstance(rejected, list) or not all(_nonempty_string(item) for item in rejected):
            errors.append(f"{location}.minimalism.rejected_complexity: must be a string list")
        if not _string_list(minimalism.get("safety_preserved")):
            errors.append(f"{location}.minimalism.safety_preserved: must be a non-empty string list")

    acceptance = value.get("acceptance")
    if not isinstance(acceptance, list) or not acceptance:
        errors.append(f"{location}.acceptance: must be a non-empty list")
    else:
        for index, item in enumerate(acceptance):
            item_location = f"{location}.acceptance[{index}]"
            if not isinstance(item, dict) or set(item) != {"criterion", "command"}:
                errors.append(f"{item_location}: must contain criterion and command")
                continue
            if not _concrete_string(item.get("criterion")):
                errors.append(f"{item_location}.criterion: must be concrete")
            if not _concrete_string(item.get("command")):
                errors.append(f"{item_location}.command: must be concrete")
            else:
                required_commands.append(item["command"])
    if len(required_commands) != len(set(required_commands)):
        errors.append(f"{location}: reproduction and acceptance commands must be distinct")
    return required_commands


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

    if packet.get("version") != 3:
        errors.append("packet.version: must be 3")
    packet_risk = packet.get("risk")
    if not _one_of(packet_risk, VALID_RISKS):
        errors.append(f"packet.risk: expected one of {sorted(VALID_RISKS)}")
    if not _one_of(packet.get("profile"), VALID_PROFILES):
        errors.append(f"packet.profile: expected one of {sorted(VALID_PROFILES)}")
    if packet.get("profile") == "light" and packet_risk != "low":
        errors.append("packet.profile: light is allowed only for low risk")
    packet_intent = packet.get("intent")
    if not _one_of(packet_intent, VALID_INTENTS):
        errors.append(f"packet.intent: expected one of {sorted(VALID_INTENTS)}")
    elif packet.get("execution_policy") != _expected_execution_policy(packet_intent):
        errors.append("packet.execution_policy: must match the intent execution policy")


    assignments = _packet_participants(packet, expected_lanes, errors)
    deliberation_checks: list[tuple[str, str]] = []
    if packet.get("coordination") == "shared":
        deliberation_checks = _validate_deliberation(report.get("deliberation"), assignments, expected_lanes, errors)
    elif report.get("deliberation") is not None:
        errors.append(
            "report.deliberation: allowed only when packet coordination is shared"
        )

    if report.get("coordination") != packet.get("coordination"):
        errors.append("report.coordination: must exactly match packet.coordination")
    if report.get("task") != packet.get("task"):
        errors.append("report.task: must exactly match packet.task")
    if report.get("risk") != packet.get("risk"):
        errors.append("report.risk: must exactly match packet.risk")

    if report.get("intent") != packet_intent:
        errors.append("report.intent: must exactly match packet.intent")
    implementation_commands = _validate_implementation(
        report.get("implementation"), packet_intent, errors
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

    minimum_passed = 2 if packet.get("risk") == "high" else 1
    if len(passed_commands) < minimum_passed:
        errors.append(
            f"report.checks: need at least {minimum_passed} distinct passing check(s), "
            f"found {len(passed_commands)}"
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
