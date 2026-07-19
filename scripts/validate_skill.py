#!/usr/bin/env python3
"""Portable quick validation for the canonical Wide-Lens Skill source."""

from __future__ import annotations

import argparse
import json
import re
from pathlib import Path


NAME_RE = re.compile(r"^[a-z0-9]+(?:-[a-z0-9]+)*$")
MARKDOWN_LINK_RE = re.compile(r"\[[^\]]*\]\(([^)]+)\)")
REQUIRED = {
    "SKILL.md",
    "LICENSE",
    "agents/openai.yaml",
    "references/practical.md",
    "references/protocol.md",
    "references/protocol-v5.md",
    "references/lenses.json",
    "scripts/diverge.py",
    "scripts/diverge_v5.py",
    "scripts/check_delivery.py",
    "scripts/check_delivery_v5.py",
}


class SkillError(RuntimeError):
    """Canonical Skill source is incomplete or malformed."""


def parse_openai_yaml(text: str) -> dict[str, str | bool]:
    """Parse the intentionally tiny openai.yaml subset without YAML dependencies."""
    lines = text.splitlines()
    if len(lines) != 6 or lines[0] != "interface:" or lines[4] != "policy:":
        raise SkillError("agents/openai.yaml must use the exact interface/policy shape")

    values: dict[str, str | bool] = {}
    scalar_fields = (
        (1, "display_name"),
        (2, "short_description"),
        (3, "default_prompt"),
    )
    for index, name in scalar_fields:
        prefix = f"  {name}: "
        if not lines[index].startswith(prefix):
            raise SkillError(f"agents/openai.yaml is missing canonical {name}")
        raw = lines[index][len(prefix) :]
        try:
            value = json.loads(raw)
        except json.JSONDecodeError as exc:
            raise SkillError(f"agents/openai.yaml {name} must be one JSON-quoted string") from exc
        if not isinstance(value, str) or not value:
            raise SkillError(f"agents/openai.yaml {name} must be a non-empty string")
        values[name] = value

    if lines[5] != "  allow_implicit_invocation: false":
        raise SkillError("agents/openai.yaml must set boolean allow_implicit_invocation to false")
    if "$wide-lens-engineering" not in str(values["default_prompt"]):
        raise SkillError("agents/openai.yaml must preserve explicit Skill invocation")
    values["allow_implicit_invocation"] = False
    return values


def parse_frontmatter(text: str) -> tuple[dict[str, str], str]:
    lines = text.splitlines()
    if not lines or lines[0] != "---":
        raise SkillError("SKILL.md must begin with YAML frontmatter")
    try:
        end = lines.index("---", 1)
    except ValueError as exc:
        raise SkillError("SKILL.md frontmatter is not closed") from exc
    metadata: dict[str, str] = {}
    for line in lines[1:end]:
        if not line.strip() or line.lstrip().startswith("#"):
            continue
        if ":" not in line:
            raise SkillError(f"unsupported frontmatter line: {line}")
        key, value = line.split(":", 1)
        key = key.strip()
        if key in metadata:
            raise SkillError(f"duplicate frontmatter key: {key}")
        metadata[key] = value.strip().strip('"\'')
    return metadata, "\n".join(lines[end + 1 :])


def validate(root: Path) -> dict[str, object]:
    root = root.resolve(strict=True)
    skill_path = root / "SKILL.md"
    text = skill_path.read_text(encoding="utf-8")
    metadata, body = parse_frontmatter(text)
    if set(metadata) != {"name", "description"}:
        raise SkillError("frontmatter must contain exactly name and description")
    if metadata["name"] != "wide-lens-engineering" or NAME_RE.fullmatch(metadata["name"]) is None:
        raise SkillError("Skill name is invalid")
    description = metadata["description"]
    if not description or len(description) > 1024 or "<" in description or ">" in description:
        raise SkillError("Skill description is empty, too long, or contains angle brackets")
    if not description.startswith("Opt-in ") or "Do not invoke implicitly" not in description:
        raise SkillError("Skill description must declare explicit opt-in activation")
    if not body.strip():
        raise SkillError("Skill body is empty")
    if len(text.encode("utf-8")) > 8_000:
        raise SkillError("SKILL.md must remain a compact progressive-disclosure router")
    maintainer_tokens = ("tests/run_", "build_codex_plugin.py", "validate_codex_plugin.py")
    if any(token in body for token in maintainer_tokens):
        raise SkillError("SKILL.md must not embed maintainer test or release commands")
    missing = sorted(name for name in REQUIRED if not (root / name).is_file())
    if missing:
        raise SkillError(f"required Skill files are missing: {missing}")
    openai_yaml = (root / "agents" / "openai.yaml").read_text(encoding="utf-8")
    openai_metadata = parse_openai_yaml(openai_yaml)
    checked_links = 0
    for target in MARKDOWN_LINK_RE.findall(body):
        if "://" in target or target.startswith(("#", "mailto:")):
            continue
        relative = target.split("#", 1)[0]
        if not relative:
            continue
        resolved = (root / relative).resolve(strict=False)
        try:
            resolved.relative_to(root)
        except ValueError as exc:
            raise SkillError(f"Skill link escapes its root: {target}") from exc
        if not resolved.exists():
            raise SkillError(f"Skill link target is missing: {target}")
        checked_links += 1
    return {
        "name": metadata["name"],
        "description_chars": len(description),
        "router_bytes": len(text.encode("utf-8")),
        "implicit_invocation": openai_metadata["allow_implicit_invocation"],
        "required_files": len(REQUIRED),
        "checked_links": checked_links,
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("skill_root", type=Path)
    args = parser.parse_args(argv)
    try:
        result = validate(args.skill_root)
        print(json.dumps({"passed": True, **result}, ensure_ascii=False, sort_keys=True))
        return 0
    except (OSError, SkillError, UnicodeError) as exc:
        print(json.dumps({"passed": False, "error": str(exc)}, ensure_ascii=False, sort_keys=True))
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
