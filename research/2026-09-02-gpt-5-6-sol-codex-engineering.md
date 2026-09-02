# GPT-5.6 Sol and Codex engineering failure landscape

Evidence snapshot: 2026-09-02 (UTC+8)

## Scope and method

This is a representative current landscape, not a literal census of the web and not a model benchmark. It separates four evidence levels:

- **A — official contract:** current OpenAI or Git documentation;
- **B — controlled first-party report:** a public issue with environment, failure shape, and reproduction or controls;
- **C — concrete field report:** a user describes an observable outcome but provides no public minimal reproduction;
- **D — anecdote:** discussion or preference useful for hypothesis generation only.

GitHub issue counts below were obtained from the public `openai/codex` tracker with `is:issue created:>=2026-07-01` and one label at a time. On the snapshot date the discovery pools contained 315 `model-behavior`, 480 `subagent`, 283 `context`, 683 `tool-calls`, 3,080 `windows-os`, and 606 `sandbox` issues. Labels overlap, reports include duplicates and configuration mistakes, and counts measure tracker volume—not incidence, severity, confirmation, or model quality.

## Findings

### 1. Prompt mass and self-created process can displace the product

**Evidence:** A + B + C.

OpenAI's GPT-5.6 guidance says leaner prompts improved internal coding-agent eval scores while reducing tokens and cost, and warns that long sessions amplify repeated prompt and tool content. Two detailed open Codex reports describe a stronger failure shape: the Agent replaces the requested deliverable with plans, hashes, checkers, gates, or orchestration artifacts, then treats those artifacts as completion or authority. One report also documents cleanup scope reversing user and Agent ownership.

**Project response:** keep explicit opt-in and progressive disclosure; freeze a concrete deliverable; treat Agent-created process artifacts as non-authoritative support; require product evidence before test/report evidence; invalidate downstream work after a corrected premise.

Sources: [GPT-5.6 model guidance](https://developers.openai.com/api/docs/guides/latest-model), [deliverable replaced by meta-work #37278](https://github.com/openai/codex/issues/37278), [authority reversal and cleanup drift #41222](https://github.com/openai/codex/issues/41222).

### 2. Strong local activity does not guarantee trajectory completion

**Evidence:** B + C + D, with contradictory field reports.

Reports describe premature completion, repeated analysis without a product, large diffs, and extended review/fix loops. A particularly instrumented issue reconstructed 74 descendants, three levels of nesting, and repeated review activity in one task. A separate user report describes an eight-hour loop that produced no requested change. Conversely, other users report strong one-shot completion and improved edge-case handling. The defensible conclusion is variance by task, runtime, prompt, and route—not a universal GPT-5.6 regression.

**Project response:** progress must be measured against the deliverable and frozen acceptance; repeated attempts need new discriminating evidence; recursive delegation stays disabled; no model or reasoning setting is globally prescribed.

Sources: [runaway delegation #38989](https://github.com/openai/codex/issues/38989), [runaway process field report](https://community.openai.com/t/refund-runaway-process-5-6-sol-codex-desktop/1389531), [negative comparative report #36538](https://github.com/openai/codex/issues/36538), [positive field report](https://www.reddit.com/r/codex/comments/1uihemr/my_experience_with_gpt_56_sol/).

### 3. Tool intent and emitted tool calls can diverge

**Evidence:** B.

Open issues document Sol emitting an unrelated exec/wait tool while intending to spawn another child, and confusing the child-agent wait operation with the wait operation for an exec cell. Similar names and hidden runtime schemas make narrative intent insufficient evidence.

**Project response:** bind follow-up operations to the exact identity and handle returned by the successful tool call; confirm the runtime event before continuing; stop placeholder or blind retry loops; inspect side effects before retrying a state-changing call.

Sources: [spawn routing regression #35620](https://github.com/openai/codex/issues/35620), [agent wait routed to the wrong tool #37113](https://github.com/openai/codex/issues/37113).

### 4. Requested and effective subagent topology can differ

**Evidence:** A + B.

The current Codex documentation supports custom agents, inherited settings, and model/reasoning configuration, while warning that each child consumes its own model and tool tokens. Public reports have nevertheless observed hidden routing fields, children silently inheriting Sol/Ultra, missing effective identity telemetry, and recursive topology growth. Some adjacent reports are closed, so these are regression hazards to observe—not permanent product contracts.

**Project response:** participant selection remains with the active main model; intended model/role/permission is never treated as execution evidence; report effective values only when observable; task DAG size is not Agent count; stop recursion and repeated non-discriminating lanes.

Sources: [Codex subagents documentation](https://learn.chatgpt.com/docs/agent-configuration/subagents), [silent child inheritance #32587](https://github.com/openai/codex/issues/32587), [missing activity identity #32504](https://github.com/openai/codex/issues/32504), [runaway delegation #38989](https://github.com/openai/codex/issues/38989).

### 5. Compaction can weaken operational state

**Evidence:** A + B.

Official model guidance recommends intentional compaction and preservation of completed actions, assumptions, IDs, tool outcomes, blockers, and the next goal. Open issues report loss of recoverable tool state after truncation and request visible or additive compaction guidance because constraints, decisions, changed files, and pending validation can become uncertain.

**Project response:** after an observed compaction or truncation, reconstruct a compact state capsule from durable authority and current repository state; reread applicable policy; never accept the compacted narrative as proof of changes or tool outcomes.

Sources: [OpenAI compaction guidance](https://developers.openai.com/api/docs/guides/latest-model?model=gpt-5.5), [tool state loss #37121](https://github.com/openai/codex/issues/37121), [additive compaction guidance #29816](https://github.com/openai/codex/issues/29816).

### 6. MCP and tool lifecycle state can be inconsistent

**Evidence:** B.

Reports cover tools enumerated by the runtime but absent from the live thread, malformed calls, review-child approval hangs, missing completion events, and an HTTP MCP call that works in one CLI build but fails in the Desktop-bundled build.

**Project response:** separate configured, discovered, injected, invoked, completed, and side-effect-observed states; impose an explicit diagnostic/timeout path; do not report completion from a start event; downgrade a missing capability instead of guessing.

Sources: [MCP state mismatch #31374](https://github.com/openai/codex/issues/31374), [delegated approval hang #31565](https://github.com/openai/codex/issues/31565), [Desktop/CLI MCP regression control #38794](https://github.com/openai/codex/issues/38794), [lost MCP completion event #32470](https://github.com/openai/codex/issues/32470).

### 7. Windows sandbox and linked-worktree behavior remains a material edge

**Evidence:** A + B.

Official Codex worktree documentation states that worktrees have separate checkout files but share Git metadata. Open reports cover `apply_patch` failing under Windows `workspace-write`, repeated approvals for Git metadata, and linked-worktree index locks becoming unwritable. This creates both reliability and false-isolation risks.

**Project response:** a worktree is practical conflict isolation, never assured confinement; observe Git topology and effective writable roots; never silently widen to full access; reproduce on the target OS/client or label the platform untested.

Sources: [Codex worktrees](https://learn.chatgpt.com/docs/environments/git-worktrees), [Windows apply_patch EPERM #31888](https://github.com/openai/codex/issues/31888), [Windows worktree approval friction #19315](https://github.com/openai/codex/issues/19315), [linked-worktree index lock #23661](https://github.com/openai/codex/issues/23661).

### 8. API capability is not the same as effective Codex surface capability

**Evidence:** A + B.

The API model page currently lists GPT-5.6 Sol with a 1,050,000-token context window, a February 16, 2026 knowledge cutoff, tool support, and tiered token pricing. A Codex issue reports different effective model-catalog context ceilings by client originator. Other reports show model availability and tool schemas varying across clients and builds. Therefore neither the API maximum nor a config value should be copied into a host capability record without observation.

**Project response:** capability negotiation remains fail-closed on unknowns; current facts require authoritative search; configured and effective model/context/tool values are recorded separately when they differ.

Sources: [GPT-5.6 Sol model page](https://developers.openai.com/api/docs/models/gpt-5.6-sol), [originator-dependent context report #40258](https://github.com/openai/codex/issues/40258), [code-mode host timeout control #38816](https://github.com/openai/codex/issues/38816).

### 9. Safety checks can look like latency or tool failure in dual-use work

**Evidence:** A.

OpenAI documents that real-time cyber and biology safeguards may block legitimate work or pause generation for synchronous review. A defensive task therefore needs to distinguish a safeguard intervention from a repository, shell, or network defect.

**Project response:** classify safeguard, permission, runtime, target-system, and model failures separately before changing code or widening privileges.

Source: [GPT-5.6 safeguards](https://developers.openai.com/api/docs/guides/latest-model#safeguards).

## What this project can and cannot improve

The Skill can improve goal preservation, authority separation, proportional verification, retry discipline, context recovery, delegation stopping, capability observation, and final evidence. It cannot patch the Codex client, backend model routing, MCP transport, sandbox implementation, billing, service availability, or the underlying model. Those remain external defects and must be reported as such.

The operational response is intentionally model-agnostic. GPT-5.6 Sol motivated this snapshot, but hard-coding the model name, participant count, or reasoning effort would turn transient reports into a brittle policy. The short cards under `references/failures/` load only after matching evidence; ordinary sessions and ordinary explicit Skill runs do not preload this report.

## Maintenance rule

For the next refresh, preserve old issue links but update their state, add confirmed fixes or regressions, include counterevidence, and record a new dated snapshot. Do not convert issue volume, anecdotes, or deterministic protocol tests into a universal model-accuracy claim.
