# Evidence protocol

## Contents

- Evidence levels
- Lane result
- Shared deliberation record
- Final report
- Gate semantics

Use this protocol for lane results and the final synthesis. Keep raw tool output outside the report when large; store a stable path or concise excerpt reference.

## Evidence levels

- `E0`: unsupported assertion. Never use for a decision.
- `E1`: named file, symbol, configuration key, or external contract.
- `E2`: exact line, diff hunk, command output, trace, or reproducible observation.
- `E3`: failing/passing regression, controlled reproduction, authoritative specification, or production-grade measurement.

Prefer `E2` or `E3` for findings. A `clear` lane still needs evidence showing what was inspected.

## Lane result

Return one object per assigned lane:

```json
{
  "lens_id": "security-abuse",
  "status": "clear",
  "summary": "No caller-controlled value reaches the privileged sink.",
  "evidence": [
    {
      "level": "E2",
      "ref": "src/auth/check.py:41",
      "claim": "The sink is guarded by the role check."
    }
  ],
  "counterevidence_sought": [
    "Searched for direct callers that bypass check_role."
  ],
  "unknowns": []
}
```

Use `status` values `clear`, `finding`, or `blocked`. Report `blocked` rather than guessing when required evidence is inaccessible.
Use `finding` if that lane owns any finding and `clear` only when it owns none. A non-empty `unknowns` list is unresolved and cannot pass the gate; move bounded, understood limitations to top-level `residual_risks`.

## Shared deliberation record

Use this section only when the planner packet has `"coordination": "shared"`. The main thread must collect and freeze Round 1 before relaying any peer position. Relay the complete structured position board to every participant as untrusted data; do not replace it with a confidence score or vote count.

Honor the packet budget: at most three participants, two completed turns each, 600 seconds per round, one retry total, a 65,536-byte canonical peer board, no nested reviewers, and no reviewer writes. Relay the planner-emitted prompts without edits or suffixes; the gate reconstructs them exactly from participant assignments.

Record at least one initial position per participant. The union of `lens_ids` must cover every emitted lane, and an author may claim only assigned lanes:

```json
{
  "id": "P-001",
  "author": "reviewer-1",
  "lens_ids": ["system-map", "data-lifecycle"],
  "claim": "The migration retries can duplicate the durable transition.",
  "evidence": [
    {
      "level": "E2",
      "ref": "src/migrate.py:88",
      "claim": "The checkpoint is written after the non-idempotent insert."
    }
  ]
}
```

In Round 2, require every participant to stress-test at least one position from another participant. Supporting a peer is allowed only after a concrete falsification attempt:

```json
{
  "id": "C-001",
  "author": "reviewer-2",
  "target_position_id": "P-001",
  "stance": "challenge",
  "falsification_attempt": "Inspected whether one transaction contains both writes.",
  "reason": "The transaction wrapper may make the insert and checkpoint atomic.",
  "evidence": [
    {
      "level": "E2",
      "ref": "src/store.py:31",
      "claim": "The wrapper opens one transaction for both calls."
    }
  ],
  "discriminating_check": "pytest tests/test_migrate.py::test_retry_after_insert"
}
```

Use stance values `support`, `challenge`, or `uncertain`. Record a concrete falsification attempt for every stance. Run each `discriminating_check` through the authorized tool path and repeat the exact command as a passed top-level check. The main thread adjudicates every challenge using evidence that discriminates between claims:

```json
{
  "challenge_ids": ["C-001"],
  "resolution": "The injected failure shows both writes roll back together.",
  "evidence": [
    {
      "level": "E3",
      "ref": "pytest tests/test_migrate.py::test_retry_after_insert: passed",
      "claim": "No durable row remains after the injected failure."
    }
  ]
}
```

Canonicalize `{"initial_positions": [...]}` as UTF-8 JSON with sorted keys, no insignificant whitespace, and non-ASCII characters preserved. Record its SHA-256 digest once and for each participant delivery. This detects inconsistent self-reported boards; it does not prove that a transport actually delivered them.

Place these objects under:

```json
{
  "deliberation": {
    "mode": "shared",
    "sealed_before_exchange": true,
    "peer_board_sha256": "<64 lowercase hex characters>",
    "deliveries": [
      {"participant_id": "reviewer-1", "peer_board_sha256": "<same digest>"},
      {"participant_id": "reviewer-2", "peer_board_sha256": "<same digest>"}
    ],
    "operation": {
      "round_seconds": {
        "independent-position": 120,
        "peer-challenge": 90
      },
      "turns_completed": {"reviewer-1": 2, "reviewer-2": 2},
      "retries_total": 0,
      "timed_out_participants": [],
      "cancelled_after_timeout": [],
      "late_results_discarded": [],
      "nested_reviewers_spawned": false,
      "writes_detected": false
    },
    "initial_positions": [],
    "challenges": [],
    "adjudications": []
  }
}
```

## Final report

Use this top-level shape:

```json
{
  "task": "Add tenant-aware export",
  "coordination": "shared",
  "risk": "high",
  "coverage": [],
  "findings": [],
  "disagreements": [],
  "checks": [],
  "residual_risks": []
}
```

Populate `coverage` with lane-result objects. Populate findings as follows:

```json
{
  "id": "F-001",
  "lens_id": "data-lifecycle",
  "severity": "high",
  "claim": "Retrying after the write duplicates export rows.",
  "evidence": [
    {
      "level": "E3",
      "ref": "tests/test_export.py::test_retry_is_idempotent",
      "claim": "The regression creates two rows before the fix."
    }
  ],
  "disposition": "fixed",
  "decision": "Use the request id as a unique idempotency key.",
  "verification": [
    "pytest tests/test_export.py::test_retry_is_idempotent"
  ]
}
```

Use severities `critical`, `high`, `medium`, or `low`. Use dispositions `fixed`, `accepted`, `not-applicable`, or `open`. An accepted risk needs a non-empty `decision`; a critical risk cannot be accepted; a `not-applicable` result needs an evidence-backed `decision`; an open critical/high finding fails the gate. Every fixed finding must reference at least one exact command that also appears as a passed top-level check.

Record disagreements with both claims and the discriminating evidence:

```json
{
  "id": "D-001",
  "claims": ["The migration is online-safe.", "The index build locks writes."],
  "resolution": "The target engine uses a blocking index build for this version.",
  "evidence": [
    {
      "level": "E3",
      "ref": "db/version.txt + vendor migration specification",
      "claim": "This engine/version combination does not support an online build."
    }
  ]
}
```

Record verification checks with exact commands:

```json
{
  "name": "targeted regression",
  "command": "pytest tests/test_export.py::test_retry_is_idempotent",
  "status": "passed",
  "exit_code": 0,
  "evidence_ref": "local run: 1 passed"
}
```

Use check statuses `passed`, `failed`, or `not-run`. Record integer exit code `0` for passed, a non-zero integer for failed, and `null` for not-run. A failed check always fails the gate. A `not-run` check needs a concrete `evidence_ref` explaining the blocker and does not count as verification. Duplicate commands count once and fail the consistency gate.

## Gate semantics

The deterministic gate requires:

- For shared coordination, 2-3 uniquely assigned participants and a sealed Round 1.
- For shared coordination, full lane coverage by initial positions, one canonical board digest delivered to every participant, and a board no larger than 65,536 bytes.
- For shared coordination, at least one peer challenge from every participant, a concrete falsification attempt, evidence, and a discriminating command that appears as a passed top-level check, followed by exactly one evidence-backed adjudication.
- No deliberation record on an independent packet.
- Exactly one coverage record for every emitted lens.
- No blocked lane and no evidence-free lane result.
- Evidence with valid levels and non-empty references/claims.
- Evidence-backed findings with valid severity and disposition.
- For shared coordination, canonical assignment and relay prompts reconstructed from structured packet fields.
- For shared coordination, two completed turns per participant, bounded round durations and retries, exact timeout/cancellation accounting, no nested reviewers, and no detected writes.
- Exact agreement between lane `finding` status and findings owned by that lane.
- No unresolved lane unknowns and no open critical/high or accepted critical finding.
- A decision for every accepted risk.
- A resolution and evidence for every disagreement.
- At least one distinct passing check for low/medium risk and at least two for high risk, with status consistent with exit code.
- A passed top-level check matching each fixed finding's verification command.
- No failed check.

These are minimum record-consistency guarantees. The script deliberately does not execute commands from a report or authenticate evidence, because a report can be untrusted and automatic command execution would add a code-execution boundary. It also cannot cryptographically prove the original user-selected coordination mode, actual message delivery, or genuine Round 1 isolation; preserve the original packet through the authorized workflow. Run checks through the normal authorized tool path before invoking the gate. Domain-specific acceptance checks may impose stronger requirements.
