# Context, tool, and retry failures

Load this card only after compaction, truncation, ambiguous tool routing, a missing completion event, or a repeated tool failure. It supplements the selected workflow without changing its authority.

## Activate on evidence

- Context was compacted, a material tool result was truncated, or resumed state conflicts with the repository.
- The intended tool was not actually called, a similarly named tool was called, or a returned handle was replaced by a placeholder.
- A tool reports failure or times out but may still have produced a side effect.
- The same attempt repeats without new discriminating evidence.

## Minimum response

1. Reconstruct a small state capsule from authoritative inputs and current repository evidence: deliverable, authority sources, completed work with evidence, changed paths, pending acceptance, exact next action, live tool handles, failed attempts, and unresolved risks.
2. After compaction or resume, reread applicable repository policy and compare the capsule with `git status`, the actual diff, and durable tool results. Treat the narrative summary as a hint, not proof.
3. Call tools by the exact available identity. Bind waits, joins, polls, and cancellations to the handle returned by the successful creating call; never invent or transform a handle.
4. Before retrying any possibly state-changing call, inspect whether the first call took effect. Retry only when the operation is idempotent or the observed state proves that no side effect occurred.
5. On an identical repeated failure, stop that path and run the cheapest diagnostic that distinguishes model routing, client/runtime, permission, network, and target-system failure. Do not continue a blind loop.

## Completion evidence

A narration such as “I called,” “I waited,” or “the tool succeeded” is not evidence. Require the tool's terminal result plus the expected repository or external state. If the runtime omits a completion event or capability, record it as unavailable and downgrade or stop according to the selected workflow.

## Boundary

This card cannot repair Codex tool injection, MCP transport, compaction, or backend routing. It prevents an uncertain runtime result from becoming an unqualified completion claim.
