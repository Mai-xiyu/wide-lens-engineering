#!/usr/bin/env python3
"""Validate selective reliability cards, evidence boundaries, and frozen v4 bytes."""

from __future__ import annotations

import argparse
import hashlib
import json
import re
from pathlib import Path
from typing import Any


ROOT = Path(__file__).resolve().parent.parent
CARDS = {
    "goal": ROOT / "references" / "failures" / "goal-drift.md",
    "context": ROOT / "references" / "failures" / "context-and-tools.md",
    "delegation": ROOT / "references" / "failures" / "delegation-and-cost.md",
    "platform": ROOT / "references" / "failures" / "platform-and-freshness.md",
}
RESEARCH = ROOT / "research" / "2026-09-02-gpt-5-6-sol-codex-engineering.md"
FROZEN_V4 = {
    "references/protocol.md": "775ad630a92b91009506314fc63747c4b1d9395e746f0a102ad41b4934edf639",
    "references/lenses.json": "19b776e9d74c35dd6b5004aa0447db840b0c4c2f1aafa3b4fd1c38f4a8f58518",
    "scripts/diverge.py": "b34d33923f6750dd5e41bcb27da830956506ad962562b4cdf281e146571a8f47",
    "scripts/check_delivery.py": "ecd2a3754bf93371351d8c436e8c670d022210bc48ae9d644f05ebd35d784a2d",
}


def threshold_value(value: str) -> float:
    try:
        result = float(value)
    except ValueError as exc:
        raise argparse.ArgumentTypeError("threshold must be numeric") from exc
    if not 0.98 <= result <= 1.0:
        raise argparse.ArgumentTypeError("threshold must be between 0.98 and 1.0")
    return result


def run_cases() -> list[dict[str, Any]]:
    results: list[dict[str, Any]] = []

    def record(name: str, passed: bool, detail: str = "") -> None:
        results.append({"name": name, "passed": passed, "detail": detail})

    skill = (ROOT / "SKILL.md").read_text(encoding="utf-8")
    practical = (ROOT / "references" / "practical.md").read_text(encoding="utf-8")
    protocol_v5 = (ROOT / "references" / "protocol-v5.md").read_text(encoding="utf-8")
    host = (ROOT / "references" / "hosts" / "codex.md").read_text(encoding="utf-8")
    readme = (ROOT / "README.md").read_text(encoding="utf-8")
    readme_cn = (ROOT / "README_CN.md").read_text(encoding="utf-8")
    builder = (ROOT / "scripts" / "build_codex_plugin.py").read_text(encoding="utf-8")
    validator = (ROOT / "scripts" / "validate_codex_plugin.py").read_text(encoding="utf-8")
    manifest = json.loads(
        (ROOT / "packaging" / "codex-plugin-src" / ".codex-plugin" / "plugin.json").read_text(
            encoding="utf-8"
        )
    )

    record("canonical Skill remains explicit opt-in", "Do not invoke implicitly" in skill)
    record("router remains below 8 KB", len(skill.encode("utf-8")) <= 8_000)
    record(
        "router forbids eager failure-card loading",
        "never preload them" in skill and "Do not load the dated research report" in skill,
    )
    for path in CARDS.values():
        relative = path.relative_to(ROOT).as_posix()
        record(f"router links {relative}", f"]({relative})" in skill)

    card_text = {name: path.read_text(encoding="utf-8") for name, path in CARDS.items()}
    for name, text in card_text.items():
        record(f"{name} card is compact", len(text.encode("utf-8")) <= 3_000)
        record(
            f"{name} card has activation, response, evidence, and boundary",
            all(
                heading in text
                for heading in (
                    "## Activate on evidence",
                    "## Minimum response",
                    "## Completion evidence",
                    "## Boundary",
                )
            ),
        )
        fixed_roster = re.search(r"\b(?:spawn|use|create)\s+\d+\s+(?:agents?|subagents?)\b", text, re.I)
        record(
            f"{name} card has no fixed team or model policy",
            fixed_roster is None
            and "max_threads" not in text
            and "gpt-5.6-sol" not in text.lower(),
        )

    goal = card_text["goal"]
    record(
        "goal card separates product from support and authority",
        "supporting" in goal and "has no authority" in goal and "product outcome first" in goal,
    )
    record(
        "goal card propagates corrected premises and bounds cleanup",
        "corrects a material premise" in goal and "only Agent-created work" in goal,
    )
    context = card_text["context"]
    record(
        "context card restores durable state after compaction",
        "state capsule" in context and "narrative summary as a hint, not proof" in context,
    )
    record(
        "context card binds handles and checks side effects before retry",
        "handle returned" in context and "inspect whether the first call took effect" in context,
    )
    record(
        "context card breaks blind repeated paths",
        "stop that path" in context and "cheapest diagnostic" in context,
    )
    delegation = card_text["delegation"]
    record(
        "delegation card preserves main-model selection authority",
        "active main model" in delegation and "Do not derive a count" in delegation,
    )
    record(
        "delegation card distinguishes intended from effective routing",
        "Intended configuration is not execution evidence" in delegation and "unknown" in delegation,
    )
    record(
        "delegation card rejects recursion and non-discriminating loops",
        "Stop recursive delegation" in delegation and "new counterevidence" in delegation,
    )
    platform = card_text["platform"]
    record(
        "platform card probes effective boundaries",
        "configured value is not proof" in platform and "effective client/runtime version" in platform,
    )
    record(
        "platform card does not mistake worktrees for assured isolation",
        "Git common metadata is shared" in platform and "assured security boundary" in platform,
    )
    record(
        "platform card prevents silent permission widening and stale facts",
        "do not silently widen" in platform and "retrieval date" in platform,
    )

    record(
        "practical checkpoint freezes the observable deliverable",
        "deliverable: exact observable product outcome" in practical,
    )
    record(
        "practical workflow denies self-authored authority",
        "Agent-created support has no authority" in practical
        and "supporting artifacts and command success do not compensate" in practical.lower(),
    )
    record(
        "assured protocol separates product and protocol evidence",
        "requested product remains distinct from protocol support" in protocol_v5
        and "checker cannot infer product semantics" in protocol_v5,
    )
    record(
        "Codex adapter verifies effective child routing and exact identity",
        "Configured routing is not proof of effective routing" in host
        and "exact identity" in host
        and "inspect whether the first call took effect" in host,
    )

    research = RESEARCH.read_text(encoding="utf-8")
    record(
        "research snapshot defines evidence levels and count caveats",
        "A — official contract" in research
        and "counts measure tracker volume—not incidence" in research,
    )
    record(
        "research snapshot contains counterevidence",
        "contradictory field reports" in research and "positive field report" in research,
    )
    record(
        "research snapshot distinguishes project and external defects",
        "cannot patch the Codex client" in research and "universal model-accuracy claim" in research,
    )
    for issue in ("37278", "38989", "41222", "37121", "31374", "31888", "40258"):
        record(f"research snapshot retains primary issue {issue}", f"/issues/{issue}" in research)

    for relative, expected in FROZEN_V4.items():
        observed = hashlib.sha256((ROOT / relative).read_bytes()).hexdigest()
        record(f"frozen v4 bytes unchanged: {relative}", observed == expected, observed)

    card_relatives = {path.relative_to(ROOT).as_posix() for path in CARDS.values()}
    record(
        "plugin builder packages every reactive card",
        all(f'"{relative}"' in builder for relative in card_relatives),
    )
    record(
        "plugin validator pins every reactive card",
        all(f'"{relative}"' in validator for relative in card_relatives),
    )
    record(
        "dated research stays outside runtime package",
        "research/" not in builder and "research/" not in validator,
    )
    record("plugin package version is 0.2.0", manifest.get("version") == "0.2.0")
    record(
        "bilingual READMEs expose research and reactive controls",
        str(RESEARCH.relative_to(ROOT)).replace("\\", "/") in readme
        and str(RESEARCH.relative_to(ROOT)).replace("\\", "/") in readme_cn
        and "current-failure-controls" in readme
        and "current-failure-controls" in readme_cn,
    )
    return results


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--threshold", type=threshold_value, default=1.0)
    parser.add_argument("--json", action="store_true")
    args = parser.parse_args(argv)
    results = run_cases()
    passed = sum(1 for item in results if item["passed"])
    rate = passed / len(results) if results else 0.0
    payload = {
        "passed": rate >= args.threshold,
        "threshold": args.threshold,
        "case_pass_rate": rate,
        "passed_cases": passed,
        "total_cases": len(results),
        "failures": [item for item in results if not item["passed"]],
    }
    if args.json:
        print(json.dumps(payload, ensure_ascii=False, indent=2))
    else:
        for item in results:
            print(("PASS" if item["passed"] else "FAIL") + " " + item["name"])
        print(f"{passed}/{len(results)} ({rate:.2%})")
    return 0 if payload["passed"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
