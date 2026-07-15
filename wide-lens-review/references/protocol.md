# Evidence protocol

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

## Final report

Use this top-level shape:

```json
{
  "task": "Add tenant-aware export",
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

- Exactly one coverage record for every emitted lens.
- No blocked lane and no evidence-free lane result.
- Evidence with valid levels and non-empty references/claims.
- Evidence-backed findings with valid severity and disposition.
- Exact agreement between lane `finding` status and findings owned by that lane.
- No unresolved lane unknowns and no open critical/high or accepted critical finding.
- A decision for every accepted risk.
- A resolution and evidence for every disagreement.
- At least one distinct passing check for low/medium risk and at least two for high risk, with status consistent with exit code.
- A passed top-level check matching each fixed finding's verification command.
- No failed check.

These are minimum record-consistency guarantees. The script deliberately does not execute commands from a report or authenticate evidence, because a report can be untrusted and automatic command execution would add a code-execution boundary. Run checks through the normal authorized tool path before invoking the gate. Domain-specific acceptance checks may impose stronger requirements.
