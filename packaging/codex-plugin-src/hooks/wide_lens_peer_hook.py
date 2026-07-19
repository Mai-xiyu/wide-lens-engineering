#!/usr/bin/env python3
"""Inject and validate the neutral Wide-Lens peer result contract."""

from __future__ import annotations

import json
import re
import sys
from typing import Any


MAX_RESULT_BYTES = 1024 * 1024
MAX_EVENT_BYTES = MAX_RESULT_BYTES + 64 * 1024
RESULT_KEYS = {
    "schema",
    "task_id",
    "task_revision",
    "phase",
    "status",
    "summary",
    "evidence",
    "counterevidence",
    "discriminating_check",
    "candidate",
    "risks",
}
REPO_PATH_RE = re.compile(r"^(?!/)(?![A-Za-z]:)(?!.*\\)(?!.*:)(?!.*(?:^|/)\.\.?(?:/|$))[^/]+(?:/[^/]+)*$")


class ContractError(ValueError):
    """Peer result or hook input violates the format contract."""


def _reject_constant(value: str) -> None:
    raise ContractError(f"non-finite JSON value: {value}")


def _unique_object(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            raise ContractError(f"duplicate JSON key: {key}")
        result[key] = value
    return result


def strict_json_bytes(payload: bytes, *, limit: int, label: str) -> Any:
    if len(payload) > limit:
        raise ContractError(f"{label} exceeds {limit} bytes")
    try:
        text = payload.decode("utf-8")
    except UnicodeDecodeError as exc:
        raise ContractError("JSON input is not UTF-8") from exc
    try:
        return json.loads(
            text,
            object_pairs_hook=_unique_object,
            parse_constant=_reject_constant,
        )
    except json.JSONDecodeError as exc:
        raise ContractError(f"invalid JSON: {exc.msg}") from exc


def read_event() -> dict[str, Any]:
    value = strict_json_bytes(
        sys.stdin.buffer.read(MAX_EVENT_BYTES + 1),
        limit=MAX_EVENT_BYTES,
        label="hook event",
    )
    if not isinstance(value, dict):
        raise ContractError("hook input must be a JSON object")
    return value


def _non_empty(value: Any, field: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise ContractError(f"{field} must be a non-empty string")
    return value


def _string_list(value: Any, field: str) -> None:
    if not isinstance(value, list):
        raise ContractError(f"{field} must be an array")
    for index, item in enumerate(value):
        _non_empty(item, f"{field}[{index}]")


def validate_result(value: Any) -> None:
    if not isinstance(value, dict) or set(value) != RESULT_KEYS:
        raise ContractError("peer result has missing or unknown fields")
    if value["schema"] != "wide-lens-peer-result/v1":
        raise ContractError("schema must be wide-lens-peer-result/v1")
    _non_empty(value["task_id"], "task_id")
    if type(value["task_revision"]) is not int or value["task_revision"] < 1:
        raise ContractError("task_revision must be a positive integer")
    if value["phase"] not in {"sealed-position", "peer-challenge", "proposal"}:
        raise ContractError("invalid phase")
    if value["status"] not in {"complete", "blocked"}:
        raise ContractError("invalid status")
    _non_empty(value["summary"], "summary")

    evidence = value["evidence"]
    if not isinstance(evidence, list):
        raise ContractError("evidence must be an array")
    for index, item in enumerate(evidence):
        if not isinstance(item, dict) or set(item) != {"ref", "claim"}:
            raise ContractError(f"evidence[{index}] has invalid fields")
        _non_empty(item["ref"], f"evidence[{index}].ref")
        _non_empty(item["claim"], f"evidence[{index}].claim")

    _string_list(value["counterevidence"], "counterevidence")
    _string_list(value["risks"], "risks")

    check = value["discriminating_check"]
    if check is not None:
        if not isinstance(check, dict) or set(check) != {"command", "reason"}:
            raise ContractError("discriminating_check has invalid fields")
        _non_empty(check["command"], "discriminating_check.command")
        _non_empty(check["reason"], "discriminating_check.reason")

    candidate = value["candidate"]
    if candidate is not None:
        required = {"format", "base_ref", "changed_paths", "content"}
        if not isinstance(candidate, dict) or set(candidate) != required:
            raise ContractError("candidate has invalid fields")
        if candidate["format"] != "unified-diff":
            raise ContractError("candidate.format must be unified-diff")
        _non_empty(candidate["base_ref"], "candidate.base_ref")
        _non_empty(candidate["content"], "candidate.content")
        paths = candidate["changed_paths"]
        if not isinstance(paths, list) or not paths:
            raise ContractError("candidate.changed_paths must be a non-empty array")
        for index, path in enumerate(paths):
            if not isinstance(path, str) or REPO_PATH_RE.fullmatch(path) is None:
                raise ContractError(f"candidate.changed_paths[{index}] is not canonical")


def start_output() -> dict[str, Any]:
    schema = (
        '{"schema":"wide-lens-peer-result/v1","task_id":"...",'
        '"task_revision":1,"phase":"sealed-position|peer-challenge|proposal",'
        '"status":"complete|blocked","summary":"...",'
        '"evidence":[{"ref":"path:line or URL","claim":"..."}],'
        '"counterevidence":[],"discriminating_check":null,"candidate":null,'
        '"risks":[]}'
    )
    return {
        "hookSpecificOutput": {
            "hookEventName": "SubagentStart",
            "additionalContext": (
                "Remain read-only, do not delegate, and return only one raw JSON object "
                f"matching this exact shape: {schema}"
            ),
        }
    }


def stop_output(event: dict[str, Any]) -> dict[str, Any]:
    message = event.get("last_assistant_message")
    try:
        if not isinstance(message, str):
            raise ContractError("last assistant message is missing")
        validate_result(
            strict_json_bytes(
                message.encode("utf-8"),
                limit=MAX_RESULT_BYTES,
                label="peer result",
            )
        )
    except ContractError as exc:
        if event.get("stop_hook_active") is True:
            return {
                "continue": True,
                "systemMessage": f"Wide-Lens peer result remained invalid; mark it failed: {exc}",
            }
        return {
            "decision": "block",
            "reason": f"Return one corrected wide-lens-peer-result/v1 object: {exc}",
        }
    return {"continue": True}


def main(argv: list[str]) -> int:
    if len(argv) != 2 or argv[1] not in {"start", "stop"}:
        print("usage: wide_lens_peer_hook.py start|stop", file=sys.stderr)
        return 2
    try:
        event = read_event()
        expected = "SubagentStart" if argv[1] == "start" else "SubagentStop"
        if event.get("hook_event_name") != expected:
            raise ContractError(f"expected {expected} hook input")
        result = start_output() if argv[1] == "start" else stop_output(event)
    except ContractError as exc:
        print(str(exc), file=sys.stderr)
        return 2
    json.dump(result, sys.stdout, ensure_ascii=False, separators=(",", ":"))
    sys.stdout.write("\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
