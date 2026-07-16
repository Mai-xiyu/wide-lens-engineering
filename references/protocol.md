# Wide-Lens Engineering Protocol v4

This document is the normative artifact protocol implemented by `scripts/diverge.py` and `scripts/check_delivery.py`. It separates four roles:

1. the pre-implementation authority contract;
2. the deterministic externally anchored packet;
3. controller-observed runtime and repository state;
4. the Agent-authored evidence report.

The report is evidence only. It never becomes task authority.

## 1. Trust model

The trusted caller/controller owns:

- the canonical target-repository identity and the OS/process/network sandbox;
- artifact locations outside the target repository;
- immutable or read-only storage for baseline, packet, prior packet, report, and receipt;
- the independently published current packet digest;
- the independently published prior packet digest for a revision;
- the independently published runtime-receipt digest for shared coordination;
- the trusted verifier-bundle digest and verifier launch path;
- any signing/authenticated-attestation key.

An embedded digest detects internal inconsistency but is not an external trust anchor. An unsigned `authority.kind`, `authority.locator`, `controller_ref`, or `evidence.ref` is an auditable claim, not identity authentication.

The verifier observes the target repository. It does not confine writes, network activity, credentials, or descendant processes outside that repository. Run the verifier and every frozen acceptance command inside a controller-enforced sandbox.

## 2. Canonical JSON

Canonical bytes are UTF-8 JSON with:

- object keys sorted;
- separators `,` and `:` without insignificant whitespace;
- non-ASCII characters preserved;
- `NaN`, `Infinity`, and `-Infinity` rejected;
- duplicate object keys rejected while loading;
- strict JSON type comparison (`1`, `1.0`, and `true` are different).

Unless explicitly described as a raw-file digest, every digest is lowercase SHA-256 over canonical bytes. Invalid Unicode, unsupported types, and excessively deep values fail closed.

## 3. Baseline state manifest v2

Capture must occur before any target-repository write:

```bash
python <trusted-skill>/scripts/check_delivery.py \
  --capture-baseline \
  --repo-root <canonical-target-repository> \
  --baseline-manifest <external-artifact-dir>/baseline.json
```

The verifier bundle and baseline path must be outside the target repository. Capture opens the baseline file with exclusive creation and refuses overwrite.

### 3.1 Exact shape

The manifest has exactly:

```json
{
  "version": 2,
  "repository_ref": "<canonical-absolute-root>",
  "root_metadata": {
    "type": "directory",
    "mode": 493,
    "attributes": 16,
    "nlink": 1,
    "file_id": "<device>:<inode-or-file-id>"
  },
  "entries": {},
  "named_streams": {}
}
```

A regular file entry has exactly:

```json
{
  "type": "file",
  "mode": 420,
  "attributes": 32,
  "nlink": 1,
  "file_id": "<device>:<inode-or-file-id>",
  "size": 123,
  "sha256": "<default-stream-raw-file-digest>"
}
```

A directory entry has exactly `type`, `mode`, `attributes`, `nlink`, and `file_id`. `.git` and empty directories are included. A regular `.git` file beginning case-insensitively with `gitdir: ` is rejected because its external Git directory is outside the observed repository. On Windows, `.git` name matching is filesystem-case-insensitive, so variants such as `.GIT` are also rejected.

On Windows, `named_streams[relative-path]` maps every non-default stream name such as `:audit:$DATA` to exactly `{size, sha256}`. Root-directory named streams are rejected.

The capture rejects symlinks, junctions, other reparse points, external Git directories, unsupported entry types, inaccessible directories, non-canonical roots, and unstable state. It builds two complete consecutive manifests and requires strict equality.

Timestamps, ACLs, ownership, and platform metadata not represented by mode/Windows attributes remain outside this manifest's guarantee.

### 3.2 Capture output mapping

The capture command prints controller data. Copy it into the contract using this exact mapping:

| Capture output | Contract field |
|---|---|
| `repository_ref` | `baseline.repository_ref` |
| `state_ref` | `baseline.state_ref` |
| `baseline_manifest_sha256` | `baseline.state_sha256` |

`state_ref` is the canonical external baseline-artifact path. `verifier_sha256` is the candidate verifier-bundle digest; it must be pinned through an independent trusted channel.

## 4. Complete contract v1

The contract has exactly:

```text
version, contract_id, revision, objective, intent, authorities,
non_goals, acceptance, scope, safety_constraints, assumptions,
baseline, approval, supersedes
```

`version` is integer `1`; `contract_id` is non-empty; `revision` is a non-negative integer.

### 4.1 Normative item shapes

- `objective`: exactly `{text, source_refs}`.
- `intent`: exactly `{value, source_refs}` where value is `change`, `debug`, or `review`.
- `non_goals`: list of `{text, source_refs}`; the list may be empty.
- `acceptance`: non-empty list of `{id, criterion, command, source_refs}`. IDs and commands are each unique.
- `safety_constraints`: list of `{text, source_refs}`; the list may be empty.
- `assumptions`: list of `{text, blocking, source_refs}`. `blocking` must be boolean `false`.
- `baseline`: exactly `{repository_ref, state_ref, state_sha256, captured_before_write, source_refs}`; `captured_before_write` is literal `true`.
- `approval`: exactly `{status, source_ref}`.
- `supersedes`: `null` at revision 0; otherwise the revision object in section 4.5.

Every `source_refs` value is a non-empty list of existing unique authority IDs.

`scope` has exactly:

```text
analysis_paths, allowed_write_paths, forbidden_write_paths,
path_case, path_flavor
```

- `path_case`: exactly `{value, source_refs}`, value `sensitive` or `insensitive`.
- `path_flavor`: exactly `{value, source_refs}`, value `posix` or `windows-win32`.
- each path list contains exact `{path, source_refs}` objects and has no duplicate semantic paths;
- `change` and `debug` require at least one allowed write path;
- `review` requires an empty allowed write list;
- analysis and forbidden lists may be empty.

### 4.2 Authority records

Allowed authority kinds are exactly:

```text
user, user-approval, repo-policy, environment,
repository-evidence, inference
```

An input authority is either `{id, kind, locator, content}` or that shape plus `sha256`. Freeze always writes the computed `sha256`, so every frozen packet authority has exactly five fields.

```json
{
  "id": "SRC-USER",
  "kind": "user",
  "locator": "user-message:<external-ref>",
  "content": "{\"grants\":[{\"item_sha256\":\"<digest>\",\"target\":\"contract.objective\"}],\"statement\":\"exact source statement\"}",
  "sha256": "<sha256-of-the-exact-content-string>"
}
```

`content` is a JSON string. Decoding it must produce exactly:

```json
{
  "statement": "exact source statement",
  "grants": [
    {
      "target": "contract.objective",
      "item_sha256": "<sha256-of-the-complete-canonical-item>"
    }
  ]
}
```

`statement` is non-empty; `grants` is non-empty; grant targets within one authority are unique. The item digest includes the item's own `source_refs`. Every cited source must grant that exact target/digest pair, and every supplied grant must be consumed. Missing, stale, unused, duplicated, or mismatched grants fail.

### 4.3 Grant targets

Valid indexed targets are:

```text
contract.objective
contract.intent
contract.non_goals[i]
contract.acceptance[i]
contract.scope.path_case
contract.scope.path_flavor
contract.scope.analysis_paths[i]
contract.scope.allowed_write_paths[i]
contract.scope.forbidden_write_paths[i]
contract.safety_constraints[i]
contract.assumptions[i]
contract.baseline
contract.approval
contract.supersedes
```

### 4.4 Authority capabilities

- objective, intent, acceptance, and allowed writes require `user` or `user-approval`;
- non-goals, safety constraints, and forbidden writes allow `user`, `user-approval`, `repo-policy`, or `environment`;
- path case/flavor additionally allow `repository-evidence`;
- analysis paths, assumptions, and baseline allow any recognized authority kind;
- an `inference` reference is valid only if that same item's `source_refs` also contains `approval.source_ref`, whose kind is `user-approval`.

`approval.status` is exactly `approved` or `not-required`:

- `approved` requires a `user-approval` `source_ref`, and that authority must grant the complete approval object;
- `not-required` requires `source_ref: null`;
- any normative inference requires `approved`.

### 4.5 Revision lineage

Revision 0 requires `supersedes: null`. Revision greater than 0 requires exactly:

```json
{
  "packet_sha256": "<prior-externally-anchored-packet-digest>",
  "reason": "<concrete amendment reason>",
  "approval_ref": "<user-approval authority id>"
}
```

`approval_ref` must exactly grant this object. The gate additionally requires:

```bash
--supersedes-packet <external-prior-packet.json> \
--expect-supersedes-sha256 <trusted-prior-packet-digest>
```

The prior artifact must be outside the target repository and unchanged during checks. Its raw schema, embedded/canonical packet digest, canonical contract, and contract digest are checked. The current contract must preserve `contract_id` and use `prior.revision + 1`. The prior derived lanes are not reconstructed against the current lens catalog; this allows a previously accepted packet to remain a lineage parent after a catalog update.

## 5. Deterministic packet v4

`diverge.py` emits exactly:

```text
version, contract, contract_sha256, packet_sha256, risk, profile,
coordination, planner, independence, execution_policy, discussion,
lanes, synthesis_gate
```

The current gate reconstructs the entire packet before any acceptance command and compares all derived fields with the pinned local catalog.

- `version`: integer `4`.
- `risk`: `low`, `medium`, or `high`.
- `profile`: `light` or `full`; light is allowed only for low risk and cannot hide triggered lanes.
- `coordination`: `independent` or `shared`; shared requires full.
- `planner`: exactly `{seed, catalog_sha256}`; seed is non-empty and at most 256 characters.
- `independence`: exactly `{hide_proposed_solution:true, hide_peer_outputs:true, single_editing_owner:true}`.
- `discussion`: `null` for independent; exact shared policy below otherwise.
- every lane is deterministically generated with exactly `{id,title,mission,primary_question,required_challenge,evidence_requirement,write_scope,prompt}`, where `write_scope` is `read-only`.
- `synthesis_gate`: exactly `{require_all_lanes:true, require_evidence:true, reject_open_high_severity:true, resolve_disagreements_with_discriminating_evidence:true}`.

Execution policy is exactly:

```json
{
  "implementation_required": true,
  "editing_owner": "main-thread",
  "analysis_agents_read_only": true,
  "write_scope_source": "frozen-contract",
  "acceptance_source": "frozen-contract",
  "ponytail_level": "full",
  "minimalism_ladder": [
    "not-needed", "reuse", "stdlib", "native",
    "existing-dependency", "minimal-custom"
  ]
}
```

`implementation_required` is true for `change`/`debug` and false for `review`.

### 5.1 Shared policy and participant-count ownership

The packet never contains participant identities, participant count, a default count, or a maximum count.

- Before packet creation, the active main model selects independent or shared.
- After a shared packet is externally anchored, the active main model selects actual identities, count, and lane assignments.
- Shared requires at least two participants only because one participant is not deliberation.

Shared `discussion` has exactly:

```json
{
  "mode": "shared",
  "sealed_round1": true,
  "rounds": [
    "independent-position", "peer-challenge", "evidence-adjudication"
  ],
  "selection": {
    "owner": "active-main-model",
    "decided_at_runtime": true,
    "skill_prescribes_count": false
  },
  "relay": "Main thread relays the complete structured peer board between the same participants.",
  "adjudicator": "main-thread",
  "decision_rule": "Resolve claims by discriminating evidence, never by vote or confidence.",
  "budget": {
    "max_turns_per_participant": 2,
    "max_round_seconds_per_participant": 600,
    "max_retries_per_participant": 1,
    "max_position_bytes_per_participant": 32768,
    "allow_nested_agents": false,
    "allow_writes": false
  }
}
```

The byte limit applies to the sum of one participant's canonical Round 1 position objects. No aggregate participant-count or board-size budget exists.

### 5.2 Runtime prompt materialization

After the main model chooses assignments, store a JSON list of exact `{id, lane_ids}` objects and run:

```bash
python <trusted-skill>/scripts/diverge.py \
  --packet <external-packet.json> \
  --runtime-assignments <external-assignments.json> \
  --output <external-runtime-prompts.json>
```

The command reconstructs the anchored shared packet, requires at least two safe unique IDs matching `[A-Za-z0-9][A-Za-z0-9._-]{0,63}`, requires non-empty unique known lanes per participant and complete lane coverage, then emits:

```text
packet_sha256, selected_by="active-main-model", participants
```

Each participant contains exactly `{id, lane_ids, round1_prompt, round2_prompt}`. The tool validates and materializes the main model's selection; it never chooses the number.

## 6. Shared runtime receipt v1

Shared coordination requires an external controller artifact after deliberation:

```json
{
  "version": 1,
  "packet_sha256": "<anchored-current-packet-digest>",
  "controller_ref": "<external-runtime-ledger-ref>",
  "participants": [
    {"id": "runtime-agent-a", "lane_ids": ["L-input"]},
    {"id": "runtime-agent-b", "lane_ids": ["L-failure"]}
  ],
  "deliberation_sha256": "<canonical-complete-deliberation-digest>",
  "nested_agents_spawned": false,
  "subagent_writes_detected": false
}
```

These are the exact keys. Participants must exactly match the report's runtime delegation. The receipt digest is supplied separately with `--expect-runtime-receipt-sha256`. The receipt must be outside the target repository, distinct from every other artifact/verifier file, and unchanged during checks.

This separates runtime observation from the Agent report. It authenticates identity only when the controller and digest channel are authenticated or the receipt is signed.

## 7. Evidence report

Every report has exactly:

```text
packet_sha256, coordination, risk, intent, implementation,
coverage, findings, disagreements, checks, residual_risks
```

Shared adds exactly `deliberation`; independent must not contain it. Packet digest, coordination, risk, and intent must exactly match the frozen packet. Contract fields are forbidden.

Evidence is a non-empty list of exact `{level, ref, claim}` objects. `level` is `E1`, `E2`, or `E3`; ref and claim are concrete strings.

### 7.1 Implementation

For `review`, `implementation` is `null` and controller-observed changed paths must be empty.

For `change` and `debug`, implementation has exactly:

```text
status, owner, changed_paths, no_change_reason, root_cause,
minimalism, acceptance_results
```

- `status`: `changed` or `no-change`;
- `owner`: literal `main-thread`;
- `changed_paths`: unique canonical repository paths exactly equal to controller observations; `.` is the repository-root marker;
- changed requires a non-empty observed diff and `no_change_reason:null`;
- no-change requires an empty observed diff and a concrete reason;
- change requires `root_cause:null`;
- debug requires exact `{claim,evidence,reproduction_command}`, and reproduction command must be frozen acceptance;
- every changed path must be allowed and not forbidden by the frozen scope.

`minimalism` has exactly:

```text
source, level, selected_rung, rejected_complexity, safety_preserved
```

- source: `ponytail` or `built-in`;
- level: literal `full`;
- selected rung: one of the six frozen ladder values;
- no-change requires `not-needed`; changed forbids `not-needed`;
- `rejected_complexity` is a string list and may be empty;
- `safety_preserved` is a non-empty string list.

`acceptance_results` is a non-empty list of exact `{criterion_id,evidence_ref}` objects. IDs are unique and cover every frozen acceptance ID exactly once.

### 7.2 Coverage, findings, disagreements, checks

Each coverage record has exactly:

```text
lens_id, status, summary, evidence, counterevidence_sought, unknowns
```

Every packet lane occurs exactly once. Status is `clear`, `finding`, or `blocked`; blocked cannot pass. `counterevidence_sought` is non-empty. `unknowns` must be an empty string list to pass. Clear/finding status must agree with findings for that lane.

Each finding normally has exactly:

```text
id, lens_id, severity, claim, evidence, disposition, decision
```

A `fixed` finding additionally and exclusively requires `verification`, a non-empty command list. IDs are unique; lane IDs are known. Severity is `critical`, `high`, `medium`, or `low`; disposition is `fixed`, `accepted`, `not-applicable`, or `open`. Critical/high cannot remain open; critical cannot be accepted. A fixed finding must reference at least one controller-observed passing frozen command.

Each disagreement has exactly `{id, claims, resolution, evidence}`. IDs are unique; claims contains at least two non-empty strings.

Each check has exactly `{name, command, status, exit_code, evidence_ref}`. Status is `passed`, `failed`, or `not-run`; exit code is integer or null, with passed→0, failed→nonzero, not-run→null. Commands cover the frozen acceptance commands exactly once with no extras and must match controller observations. Every frozen command must ultimately pass.

`residual_risks` is a list of non-empty strings and may be empty.

### 7.3 Shared deliberation

`deliberation` has exactly:

```text
mode, sealed_before_exchange, peer_board_sha256, deliveries,
initial_positions, challenges, adjudications, delegation, operation
```

`mode` is `shared`; `sealed_before_exchange` is true.

`delegation` has exactly `{selected_by,sealed_before_round1,packet_sha256,participants}`. The first two values are `active-main-model` and `true`. Each participant has exactly `{id,lane_ids,round1_prompt,round2_prompt}`; IDs are safe/unique, lanes are known/non-empty/unique, all packet lanes are covered, and prompts equal the deterministic runtime prompt builder byte-for-byte.

`deliveries[i]` has exactly `{participant_id,peer_board_sha256}`. Every participant occurs once and receives the same board digest.

`initial_positions[i]` has exactly `{id,author,lens_ids,claim,evidence}`. IDs are unique; author is a participant; lens IDs are a non-empty subset of that author's lanes. Every participant contributes at least one position and all lanes are covered. Per-author canonical bytes stay within the frozen budget. `peer_board_sha256` equals SHA-256 of canonical `{"initial_positions": positions}`.

`challenges[i]` has exactly `{id,author,target_position_id,stance,falsification_attempt,reason,evidence,discriminating_check}`. IDs are unique; every participant authors at least one; targets are peer positions; stance is `support`, `challenge`, or `uncertain`; the discriminating command is frozen and controller-observed passing.

`adjudications[i]` has exactly `{challenge_ids,resolution,evidence}`. Challenge IDs are non-empty, and every challenge is adjudicated exactly once.

`operation` has exactly:

```text
round_seconds_by_participant, turns_completed, retries_by_participant,
timed_out_participants, cancelled_after_timeout, late_results_discarded,
nested_agents_spawned, writes_detected
```

The first three maps cover the participant set exactly. Each timing value is exact `{independent-position,peer-challenge}` with integer seconds 0..600; turns are strict integer 2; retries are integer 0..1. Timeout lists contain unique participant IDs; cancelled equals timed-out; late results are a subset of timed-out. Nested agents and writes are both false.

`final_state_ref` and `diff_ref` are deliberately absent from the Agent report.

## 8. Gate semantics

```bash
python <trusted-skill>/scripts/check_delivery.py \
  --repo-root <canonical-target-repository> \
  --baseline-manifest <external-artifact-dir>/baseline.json \
  --packet <external-artifact-dir>/packet.json \
  --report <external-artifact-dir>/report.json \
  --expect-packet-sha256 <trusted-current-packet-digest> \
  --expect-verifier-sha256 <trusted-verifier-bundle-digest> \
  [--runtime-receipt <external-artifact-dir>/runtime-receipt.json \
   --expect-runtime-receipt-sha256 <trusted-receipt-digest>] \
  [--supersedes-packet <external-artifact-dir>/prior-packet.json \
   --expect-supersedes-sha256 <trusted-prior-packet-digest>]
```

The verifier bundle is exactly:

```text
scripts/check_delivery.py
scripts/diverge.py
references/lenses.json
```

Every artifact, prior packet, and verifier file must resolve to a distinct path outside the target repository.

Before executing any acceptance command, the gate:

1. canonicalizes the lexical repository root and rejects root links/reparse/aliases;
2. verifies verifier and current packet external digests;
3. reconstructs the complete current contract and all derived packet fields;
4. validates the shared receipt when applicable;
5. validates the baseline digest, artifact path, repository path, and root file object;
6. performs a stable current-state scan, rejecting links/reparse/unsupported entries already introduced during implementation;
7. validates Win32 scope aliases;
8. validates prior packet lineage when applicable.

Only then does it execute the unique frozen acceptance commands sequentially. It never executes report-added commands.

The command environment:

- uses `/bin/sh` on POSIX and the system-directory `cmd.exe` on Windows;
- removes relative and repository-contained PATH entries;
- pins Windows `PATHEXT` and sets `NoDefaultCurrentDirectoryInExePath`;
- clears high-risk language/tool hooks;
- removes every inherited environment variable whose name begins case-insensitively with `GIT_`;
- then points global/system Git config at the null device, disables system config, and disables interactive Git prompts.

The environment sanitizer is not an OS sandbox.

After commands, the gate:

1. re-hashes every artifact and verifier file and rejects persistent mutation;
2. creates another stable repository snapshot;
3. requires the final repository root file object to equal the baseline root object;
4. derives actual changed paths from baseline versus final state;
5. validates the report against controller-observed diff and command results;
6. emits controller observations.

The observation hashes are:

```text
baseline_state_sha256 = canonical validated baseline manifest digest
final_state_sha256    = canonical validated final manifest digest
diff_sha256           = sha256(canonical({
  repository_ref,
  baseline_state_sha256,
  final_state_sha256,
  changed_paths
}))
```

Observed checks contain exact command, integer exit code, and raw stdout/stderr SHA-256 digests.

Because frozen commands execute code under test, they may attempt repository-external writes, network use, credential access, artifact swap-and-restore, or background processes. Enforce those boundaries outside this Python verifier.

## 9. Path semantics

All scope/report paths are repository-relative. Absolute paths, NUL, empty components, internal `.` components, and `..` are invalid. The sole root marker is `.`.

`windows-win32` additionally rejects control characters, `<>:"|?*`, colon/ADS syntax, trailing dot/space, and reserved device names. On a Windows controller:

- insensitive comparison uses invariant Win32 ordinal-style uppercase mapping rather than Python `casefold()`;
- existing scope prefixes are expanded through `GetLongPathNameW`;
- a supplied short/alternate alias is rejected rather than silently canonicalized;
- actual changed paths come from controller enumeration.

A `windows-win32` contract requires a Windows controller.

## 10. Complexity and residual limits

One stable snapshot performs two complete scans. Its time is `O(F + B + S)` for entry count `F`, default-stream bytes `B`, and named-stream bytes `S`; manifest space is `O(F + A)` for named-stream count `A`. Baseline capture performs one stable snapshot. Gate mode performs one stable pre-command snapshot and one stable post-command snapshot.

This protocol does not independently:

- authenticate unsigned authority or receipt identities;
- make a self-reported verifier digest an independent trust anchor;
- prove which OS actor performed a write;
- prevent concurrent artifact/repository swap-and-restore TOCTOU;
- enforce writes, process creation, credential use, or networking outside the target repository;
- terminate every descendant/background process after timeout;
- capture every ACL, owner, timestamp, extended attribute, or device-specific metadata;
- prove real-world correctness or universal defect recall.

Use signed controller attestations, read-only verifier/artifact storage, an independently pinned release digest, and OS process/filesystem/network isolation when those guarantees matter.