#!/usr/bin/env python3
"""Validate the Codex project adapter, hooks, and reproducible plugin archive."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import shutil
import subprocess
import sys
import tempfile
import warnings
import zipfile
from pathlib import Path
from typing import Any


TEST_DIR = Path(__file__).resolve().parent
SKILL_DIR = TEST_DIR.parent
INSTALLER = SKILL_DIR / "scripts" / "install_codex_adapter.py"
BUILDER = SKILL_DIR / "scripts" / "build_codex_plugin.py"
PORTABLE_PLUGIN_VALIDATOR = SKILL_DIR / "scripts" / "validate_codex_plugin.py"
PORTABLE_SKILL_VALIDATOR = SKILL_DIR / "scripts" / "validate_skill.py"
HOOK = SKILL_DIR / "packaging" / "codex-plugin-src" / "hooks" / "wide_lens_peer_hook.py"
PLUGIN_PREFIX = "plugins/wide-lens-engineering"


def command(arguments: list[str], **kwargs: Any) -> subprocess.CompletedProcess[Any]:
    return subprocess.run(
        [sys.executable, "-B", *arguments],
        check=False,
        capture_output=True,
        **kwargs,
    )


def parse_json_output(completed: subprocess.CompletedProcess[Any]) -> dict[str, Any]:
    output = completed.stdout.decode("utf-8") if isinstance(completed.stdout, bytes) else completed.stdout
    try:
        value = json.loads(output)
    except (TypeError, json.JSONDecodeError):
        return {}
    return value if isinstance(value, dict) else {}


def valid_peer_result(candidate: Any = None) -> dict[str, Any]:
    return {
        "schema": "wide-lens-peer-result/v1",
        "task_id": "task-1",
        "task_revision": 1,
        "phase": "proposal",
        "status": "complete",
        "summary": "Inspected the assigned task without writing.",
        "evidence": [{"ref": "src/example.py:1", "claim": "The behavior is present."}],
        "counterevidence": ["Tried the boundary case."],
        "discriminating_check": {"command": "python -m test", "reason": "It separates the claims."},
        "candidate": candidate,
        "risks": [],
    }


def hook_call(mode: str, event: dict[str, Any]) -> tuple[int, dict[str, Any], str]:
    completed = command(
        [str(HOOK), mode],
        input=json.dumps(event, ensure_ascii=False).encode("utf-8"),
    )
    stdout = completed.stdout.decode("utf-8", errors="replace")
    stderr = completed.stderr.decode("utf-8", errors="replace")
    try:
        payload = json.loads(stdout)
    except json.JSONDecodeError:
        payload = {}
    return completed.returncode, payload, stderr


def run_cases() -> list[dict[str, Any]]:
    results: list[dict[str, Any]] = []

    def record(name: str, passed: bool, detail: str = "") -> None:
        results.append({"name": name, "passed": passed, "detail": detail})

    config_text = (SKILL_DIR / ".codex" / "config.toml").read_text(encoding="utf-8")
    compact_config = "".join(line.strip() for line in config_text.splitlines())
    record(
        "project config only fixes direct-child depth",
        compact_config == "[agents]max_depth = 1" and "max_threads" not in config_text,
        config_text,
    )
    agent_text = (SKILL_DIR / ".codex" / "agents" / "wide-lens-peer.toml").read_text(encoding="utf-8")
    forbidden_agent_settings = (
        "model =",
        "model_reasoning_effort",
        "nickname_candidates",
        "mcp_servers",
        "max_threads",
    )
    record(
        "peer profile is neutral read-only and count-free",
        'sandbox_mode = "read-only"' in agent_text
        and "Do not edit files" in agent_text
        and "spawn" in agent_text
        and "wide-lens-peer-result/v1" in agent_text
        and not any(item in agent_text for item in forbidden_agent_settings),
    )

    with tempfile.TemporaryDirectory(prefix="wide-lens-adapter-") as temporary:
        temp = Path(temporary)
        project = temp / "Project with 空格"
        project.mkdir()
        dry = command([str(INSTALLER), "--target", str(project)], text=True)
        record(
            "adapter dry-run succeeds",
            dry.returncode == 0 and parse_json_output(dry).get("mode") == "dry-run",
            dry.stdout + dry.stderr,
        )
        record("adapter dry-run writes nothing", not (project / ".codex").exists())
        applied = command([str(INSTALLER), "--target", str(project), "--apply"], text=True)
        installed_config = project / ".codex" / "config.toml"
        installed_agent = project / ".codex" / "agents" / "wide-lens-peer.toml"
        record(
            "adapter applies both canonical files",
            applied.returncode == 0
            and installed_config.read_bytes() == (SKILL_DIR / ".codex" / "config.toml").read_bytes()
            and installed_agent.read_bytes() == (SKILL_DIR / ".codex" / "agents" / "wide-lens-peer.toml").read_bytes(),
            applied.stdout + applied.stderr,
        )
        reapplied = command([str(INSTALLER), "--target", str(project), "--apply"], text=True)
        record(
            "adapter reapply is idempotent",
            reapplied.returncode == 0
            and all(item.get("action") == "unchanged" for item in parse_json_output(reapplied).get("files", [])),
            reapplied.stdout + reapplied.stderr,
        )

        conflict_project = temp / "config-conflict"
        conflict_project.joinpath(".codex").mkdir(parents=True)
        conflict_config = conflict_project / ".codex" / "config.toml"
        conflict_config.write_text("[agents]\nmax_depth = 2\n", encoding="utf-8")
        before = conflict_config.read_bytes()
        conflict = command(
            [str(INSTALLER), "--target", str(conflict_project), "--apply", "--force"],
            text=True,
        )
        record(
            "config conflict fails without partial install",
            conflict.returncode == 2
            and conflict_config.read_bytes() == before
            and not conflict_project.joinpath(".codex", "agents", "wide-lens-peer.toml").exists(),
            conflict.stdout + conflict.stderr,
        )

        agent_project = temp / "agent-conflict"
        agent_project.joinpath(".codex", "agents").mkdir(parents=True)
        shutil.copyfile(SKILL_DIR / ".codex" / "config.toml", agent_project / ".codex" / "config.toml")
        conflict_agent = agent_project / ".codex" / "agents" / "wide-lens-peer.toml"
        conflict_agent.write_text('name = "local"\n', encoding="utf-8")
        rejected = command([str(INSTALLER), "--target", str(agent_project), "--apply"], text=True)
        record(
            "peer profile conflict requires explicit force",
            rejected.returncode == 2 and conflict_agent.read_text(encoding="utf-8") == 'name = "local"\n',
        )
        forced = command(
            [str(INSTALLER), "--target", str(agent_project), "--apply", "--force"],
            text=True,
        )
        record(
            "explicit force replaces only peer profile",
            forced.returncode == 0
            and conflict_agent.read_bytes() == (SKILL_DIR / ".codex" / "agents" / "wide-lens-peer.toml").read_bytes(),
            forced.stdout + forced.stderr,
        )

        link_case_passed = True
        link_detail = "not applicable: host denied link creation"
        link_target = temp / "link-target"
        link_target.mkdir()
        link_project = temp / "link-project"
        try:
            link_project.symlink_to(link_target, target_is_directory=True)
        except (OSError, NotImplementedError) as exc:
            link_detail = f"not applicable on this host: {exc}"
        else:
            linked = command([str(INSTALLER), "--target", str(link_project), "--apply"], text=True)
            link_case_passed = linked.returncode != 0 and not link_target.joinpath(".codex").exists()
            link_detail = linked.stdout + linked.stderr
        record("adapter rejects symlink or reparse target when applicable", link_case_passed, link_detail)

    start_status, start_payload, start_error = hook_call(
        "start", {"hook_event_name": "SubagentStart", "agent_type": "wide_lens_peer"}
    )
    record(
        "SubagentStart injects peer result contract",
        start_status == 0
        and start_payload.get("hookSpecificOutput", {}).get("hookEventName") == "SubagentStart"
        and "wide-lens-peer-result/v1" in start_payload.get("hookSpecificOutput", {}).get("additionalContext", ""),
        start_error,
    )
    valid_message = json.dumps(valid_peer_result(), ensure_ascii=False, separators=(",", ":"))
    stop_status, stop_payload, stop_error = hook_call(
        "stop",
        {
            "hook_event_name": "SubagentStop",
            "agent_type": "wide_lens_peer",
            "stop_hook_active": False,
            "last_assistant_message": valid_message,
        },
    )
    record(
        "SubagentStop accepts exact result",
        stop_status == 0 and stop_payload == {"continue": True},
        stop_error,
    )
    invalid_variants = {
        "unknown-field": {**valid_peer_result(), "extra": True},
        "bool-revision": {**valid_peer_result(), "task_revision": True},
        "absolute-path": valid_peer_result(
            {
                "format": "unified-diff",
                "base_ref": "base",
                "changed_paths": ["/outside.py"],
                "content": "diff",
            }
        ),
        "traversal": valid_peer_result(
            {
                "format": "unified-diff",
                "base_ref": "base",
                "changed_paths": ["../outside.py"],
                "content": "diff",
            }
        ),
        "ADS": valid_peer_result(
            {
                "format": "unified-diff",
                "base_ref": "base",
                "changed_paths": ["src/file.py:stream"],
                "content": "diff",
            }
        ),
    }
    for name, result in invalid_variants.items():
        status, payload, error = hook_call(
            "stop",
            {
                "hook_event_name": "SubagentStop",
                "stop_hook_active": False,
                "last_assistant_message": json.dumps(result, ensure_ascii=False),
            },
        )
        record(
            f"peer hook rejects {name}",
            status == 0 and payload.get("decision") == "block",
            error,
        )
    duplicate_message = valid_message[:-1] + ',"schema":"duplicate"}'
    status, payload, error = hook_call(
        "stop",
        {
            "hook_event_name": "SubagentStop",
            "stop_hook_active": False,
            "last_assistant_message": duplicate_message,
        },
    )
    record("peer hook rejects duplicate JSON keys", status == 0 and payload.get("decision") == "block", error)
    nan_message = valid_message[:-1] + ',"candidate":NaN}'
    status, payload, error = hook_call(
        "stop",
        {
            "hook_event_name": "SubagentStop",
            "stop_hook_active": False,
            "last_assistant_message": nan_message,
        },
    )
    record("peer hook rejects NaN", status == 0 and payload.get("decision") == "block", error)
    status, payload, error = hook_call(
        "stop",
        {
            "hook_event_name": "SubagentStop",
            "stop_hook_active": True,
            "last_assistant_message": "invalid",
        },
    )
    record(
        "peer hook stops after one retry",
        status == 0 and payload.get("continue") is True and "mark it failed" in payload.get("systemMessage", ""),
        error,
    )
    oversized = "x" * (1024 * 1024 + 1)
    status, payload, error = hook_call(
        "stop",
        {
            "hook_event_name": "SubagentStop",
            "stop_hook_active": False,
            "last_assistant_message": oversized,
        },
    )
    record("peer hook bounds result bytes", status == 0 and payload.get("decision") == "block", error)

    dist_root = SKILL_DIR / "dist"
    dist_root.mkdir(exist_ok=True)
    build_dir = Path(tempfile.mkdtemp(prefix="distribution-eval-", dir=dist_root))
    try:
        dry = command(
            [str(BUILDER), "--dry-run", "--output-dir", str(build_dir)], text=True
        )
        record(
            "plugin build dry-run writes no archive",
            dry.returncode == 0 and not list(build_dir.glob("*.zip")),
            dry.stdout + dry.stderr,
        )
        built = command(
            [
                str(BUILDER),
                "--output-dir",
                str(build_dir),
                "--validator",
                str(PORTABLE_PLUGIN_VALIDATOR),
                "--force",
            ],
            text=True,
        )
        built_payload = parse_json_output(built)
        archive_path = Path(built_payload.get("output", ""))
        first_bytes = archive_path.read_bytes() if archive_path.is_file() else b""
        rebuilt = command(
            [
                str(BUILDER),
                "--output-dir",
                str(build_dir),
                "--validator",
                str(PORTABLE_PLUGIN_VALIDATOR),
                "--force",
            ],
            text=True,
        )
        second_bytes = archive_path.read_bytes() if archive_path.is_file() else b""
        standalone_validator = build_dir / "validate_codex_plugin.py"
        shutil.copyfile(PORTABLE_PLUGIN_VALIDATOR, standalone_validator)
        record(
            "plugin archive is reproducible byte-for-byte",
            built.returncode == 0
            and rebuilt.returncode == 0
            and bool(first_bytes)
            and first_bytes == second_bytes
            and built_payload.get("sha256") == hashlib.sha256(first_bytes).hexdigest(),
            built.stdout + built.stderr + rebuilt.stdout + rebuilt.stderr,
        )
        if archive_path.is_file():
            with zipfile.ZipFile(archive_path) as archive:
                names = archive.namelist()
                marketplace = json.loads(archive.read(".agents/plugins/marketplace.json"))
                manifest = json.loads(archive.read(f"{PLUGIN_PREFIX}/.codex-plugin/plugin.json"))
                install_text = archive.read("INSTALL.md").decode("utf-8")
                packaged_skill = archive.read(
                    f"{PLUGIN_PREFIX}/skills/wide-lens-engineering/SKILL.md"
                )
            safe_names = all(
                name
                and not name.startswith(("/", "\\"))
                and ".." not in Path(name).parts
                and ":" not in name
                and "\\" not in name
                for name in names
            )
            record("plugin archive paths are safe", safe_names)
            record(
                "plugin copies canonical Skill byte-for-byte",
                packaged_skill == (SKILL_DIR / "SKILL.md").read_bytes(),
            )
            record(
                "marketplace is self-contained and uses default hooks discovery",
                marketplace.get("plugins", [{}])[0].get("source", {}).get("path")
                == "./plugins/wide-lens-engineering"
                and "hooks" not in manifest
                and f"{PLUGIN_PREFIX}/hooks/hooks.json" in names
                and f"{PLUGIN_PREFIX}/hooks/wide_lens_peer_hook.py" in names
                and "INSTALL.md" in names,
            )
            record(
                "plugin does not claim custom-agent registration",
                not any("/.codex/agents/" in f"/{name}" for name in names),
            )
            record(
                "archive installation explains adapter and hook limits",
                ".codex/agents" in install_text
                and "wide_lens_peer" in install_text
                and "do not prove read-only execution" in install_text,
            )
            skill_prefix = f"{PLUGIN_PREFIX}/skills/wide-lens-engineering/"
            runtime_names = {
                name.removeprefix(skill_prefix)
                for name in names
                if name.startswith(skill_prefix)
            }
            record(
                "plugin runtime excludes tests and packaging tools",
                not any(name.startswith("tests/") for name in runtime_names)
                and "scripts/build_codex_plugin.py" not in runtime_names
                and "scripts/install_codex_adapter.py" not in runtime_names,
            )
            expected_keywords = {
                "elastic-agent-teams",
                "task-dag",
                "isolated-candidates",
                "capability-leases",
            }
            record(
                "plugin discovery keywords cover elastic delivery",
                expected_keywords <= set(manifest.get("keywords", [])),
            )
            portable_archive = command(
                [
                    str(standalone_validator),
                    str(archive_path),
                    "--expected-version",
                    "5.0.0",
                ],
                text=True,
            )
            record(
                "portable validator accepts the release archive",
                portable_archive.returncode == 0,
                portable_archive.stdout + portable_archive.stderr,
            )
            with tempfile.TemporaryDirectory(prefix="marketplace-extract-", dir=build_dir) as extracted:
                extracted_root = Path(extracted)
                with zipfile.ZipFile(archive_path) as archive:
                    archive.extractall(extracted_root)
                portable_directory = command(
                    [
                        str(standalone_validator),
                        str(extracted_root),
                        "--expected-version",
                        "5.0.0",
                    ],
                    text=True,
                )
                record(
                    "portable validator accepts the extracted marketplace",
                    portable_directory.returncode == 0,
                    portable_directory.stdout + portable_directory.stderr,
                )

                install_path = extracted_root / "INSTALL.md"
                install_bytes = install_path.read_bytes()
                install_path.write_text(
                    "Run an unreviewed installer.\n", encoding="utf-8", newline="\n"
                )
                changed_install = command(
                    [
                        str(standalone_validator),
                        str(extracted_root),
                        "--expected-version",
                        "5.0.0",
                    ],
                    text=True,
                )
                record(
                    "portable validator rejects changed installation instructions",
                    changed_install.returncode != 0,
                )
                install_path.write_bytes(install_bytes)

                marketplace_path = extracted_root / ".agents" / "plugins" / "marketplace.json"
                original_marketplace = marketplace_path.read_bytes()
                escaped_marketplace = json.loads(original_marketplace)
                escaped_marketplace["plugins"][0]["source"]["path"] = "../../outside"
                marketplace_path.write_text(json.dumps(escaped_marketplace), encoding="utf-8")
                escaped = command(
                    [
                        str(standalone_validator),
                        str(extracted_root),
                        "--expected-version",
                        "5.0.0",
                    ],
                    text=True,
                )
                record("portable validator rejects source.path escape", escaped.returncode != 0)
                marketplace_path.write_bytes(original_marketplace)

                extra = (
                    extracted_root
                    / PLUGIN_PREFIX
                    / "skills"
                    / "wide-lens-engineering"
                    / "tests"
                    / "unexpected.py"
                )
                extra.parent.mkdir(parents=True)
                extra.write_text("pass\n", encoding="utf-8")
                extra_result = command(
                    [
                        str(standalone_validator),
                        str(extracted_root),
                        "--expected-version",
                        "5.0.0",
                    ],
                    text=True,
                )
                record("portable validator rejects extra runtime files", extra_result.returncode != 0)
                extra.unlink()
                extra.parent.rmdir()

                hook_path = extracted_root / PLUGIN_PREFIX / "hooks" / "hooks.json"
                hook_bytes = hook_path.read_bytes()
                hook_path.unlink()
                missing_hook = command(
                    [
                        str(standalone_validator),
                        str(extracted_root),
                        "--expected-version",
                        "5.0.0",
                    ],
                    text=True,
                )
                record("portable validator rejects missing hook config", missing_hook.returncode != 0)
                hook_path.write_bytes(hook_bytes)

                hook_program_path = (
                    extracted_root
                    / PLUGIN_PREFIX
                    / "hooks"
                    / "wide_lens_peer_hook.py"
                )
                hook_program_bytes = hook_program_path.read_bytes()
                hook_program_path.write_text(
                    "raise SystemExit(99)\n", encoding="utf-8", newline="\n"
                )
                changed_hook_program = command(
                    [
                        str(standalone_validator),
                        str(extracted_root),
                        "--expected-version",
                        "5.0.0",
                    ],
                    text=True,
                )
                record(
                    "portable validator rejects changed hook implementation",
                    changed_hook_program.returncode != 0,
                )
                hook_program_path.write_bytes(hook_program_bytes)

                changed_hooks = json.loads(hook_bytes)
                changed_hooks["hooks"]["SubagentStart"][0]["matcher"] = ".*"
                hook_path.write_text(
                    json.dumps(changed_hooks, ensure_ascii=False, indent=2) + "\n",
                    encoding="utf-8",
                    newline="\n",
                )
                changed_hook_registration = command(
                    [
                        str(standalone_validator),
                        str(extracted_root),
                        "--expected-version",
                        "5.0.0",
                    ],
                    text=True,
                )
                record(
                    "portable validator rejects changed hook registration",
                    changed_hook_registration.returncode != 0,
                )
                hook_path.write_bytes(hook_bytes)

                manifest_path = extracted_root / PLUGIN_PREFIX / ".codex-plugin" / "plugin.json"
                manifest_bytes = manifest_path.read_bytes()
                bad_manifest = json.loads(manifest_bytes)
                bad_manifest["version"] = "5.0.1"
                manifest_path.write_text(json.dumps(bad_manifest), encoding="utf-8")
                wrong_version = command(
                    [
                        str(standalone_validator),
                        str(extracted_root),
                        "--expected-version",
                        "5.0.0",
                    ],
                    text=True,
                )
                record("portable validator rejects manifest version drift", wrong_version.returncode != 0)
                manifest_path.write_bytes(manifest_bytes)

                bad_manifest = json.loads(manifest_bytes)
                bad_manifest["hooks"] = "./hooks/evil.json"
                manifest_path.write_text(
                    json.dumps(bad_manifest, ensure_ascii=False, indent=2) + "\n",
                    encoding="utf-8",
                    newline="\n",
                )
                hook_override = command(
                    [
                        str(standalone_validator),
                        str(extracted_root),
                        "--expected-version",
                        "5.0.0",
                    ],
                    text=True,
                )
                record(
                    "portable validator rejects manifest hook override",
                    hook_override.returncode != 0,
                )
                manifest_path.write_bytes(manifest_bytes)

            tampered_hook_archive = build_dir / "tampered-hook.zip"
            with zipfile.ZipFile(archive_path) as source_archive, zipfile.ZipFile(
                tampered_hook_archive, "w"
            ) as target_archive:
                for info in source_archive.infolist():
                    content = source_archive.read(info.filename)
                    if info.filename == (
                        f"{PLUGIN_PREFIX}/hooks/wide_lens_peer_hook.py"
                    ):
                        content = b"raise SystemExit(99)\n"
                    target_archive.writestr(info, content)
            tampered_hook_result = command(
                [
                    str(standalone_validator),
                    str(tampered_hook_archive),
                    "--expected-version",
                    "5.0.0",
                ],
                text=True,
            )
            record(
                "portable validator rejects changed hook ZIP member",
                tampered_hook_result.returncode != 0,
            )

            tampered_install_archive = build_dir / "tampered-install.zip"
            with zipfile.ZipFile(archive_path) as source_archive, zipfile.ZipFile(
                tampered_install_archive, "w"
            ) as target_archive:
                for info in source_archive.infolist():
                    content = source_archive.read(info.filename)
                    if info.filename == "INSTALL.md":
                        content = b"Run an unreviewed installer.\n"
                    target_archive.writestr(info, content)
            tampered_install_result = command(
                [
                    str(standalone_validator),
                    str(tampered_install_archive),
                    "--expected-version",
                    "5.0.0",
                ],
                text=True,
            )
            record(
                "portable validator rejects changed installation ZIP member",
                tampered_install_result.returncode != 0,
            )

            alias_archive = build_dir / "portable-alias.zip"
            with zipfile.ZipFile(archive_path) as source_archive, zipfile.ZipFile(
                alias_archive, "w"
            ) as target_archive:
                for info in source_archive.infolist():
                    target_archive.writestr(info, source_archive.read(info.filename))
                target_archive.writestr("install.md", b"alias\n")
            alias_result = command(
                [
                    str(standalone_validator),
                    str(alias_archive),
                    "--expected-version",
                    "5.0.0",
                ],
                text=True,
            )
            record(
                "portable validator rejects ZIP path aliases before extraction",
                alias_result.returncode != 0,
            )

            oversized_archive = build_dir / "oversized-member.zip"
            with zipfile.ZipFile(archive_path) as source_archive, zipfile.ZipFile(
                oversized_archive, "w", compression=zipfile.ZIP_DEFLATED
            ) as target_archive:
                for info in source_archive.infolist():
                    content = source_archive.read(info.filename)
                    if info.filename == "INSTALL.md":
                        content = b"x" * (8 * 1024 * 1024 + 1)
                    target_archive.writestr(info.filename, content)
            oversized_result = command(
                [
                    str(standalone_validator),
                    str(oversized_archive),
                    "--expected-version",
                    "5.0.0",
                ],
                text=True,
            )
            record(
                "portable validator rejects oversized compressed members before extraction",
                oversized_result.returncode != 0,
            )

            padded_archive = build_dir / "oversized-archive.zip"
            padded_archive.write_bytes(first_bytes + b"\0" * (32 * 1024 * 1024 + 1))
            padded_result = command(
                [
                    str(standalone_validator),
                    str(padded_archive),
                    "--expected-version",
                    "5.0.0",
                ],
                text=True,
            )
            record(
                "portable validator bounds compressed archive bytes before parsing",
                padded_result.returncode != 0
                and "compressed file size limit" in padded_result.stdout,
            )

            linked_archive = build_dir / "linked-clean-archive.zip"
            link_rejected = True
            try:
                linked_archive.symlink_to(archive_path)
            except (OSError, NotImplementedError):
                pass
            else:
                linked_result = command(
                    [
                        str(standalone_validator),
                        str(linked_archive),
                        "--expected-version",
                        "5.0.0",
                    ],
                    text=True,
                )
                link_rejected = (
                    linked_result.returncode != 0
                    and "target must be a marketplace directory or ZIP file"
                    in linked_result.stdout
                )
            record("portable validator rejects linked archive inputs", link_rejected)

            duplicate_archive = build_dir / "duplicate-member.zip"
            with warnings.catch_warnings():
                warnings.simplefilter("ignore", UserWarning)
                with zipfile.ZipFile(duplicate_archive, "w") as archive:
                    archive.writestr("INSTALL.md", "first")
                    archive.writestr("INSTALL.md", "second")
            duplicate_result = command(
                [
                    str(standalone_validator),
                    str(duplicate_archive),
                    "--expected-version",
                    "5.0.0",
                ],
                text=True,
            )
            record("portable validator rejects duplicate ZIP members", duplicate_result.returncode != 0)
        else:
            for name in (
                "plugin archive paths are safe",
                "plugin copies canonical Skill byte-for-byte",
                "marketplace is self-contained and uses default hooks discovery",
                "plugin does not claim custom-agent registration",
                "plugin runtime excludes tests and packaging tools",
                "plugin discovery keywords cover elastic delivery",
                "portable validator accepts the release archive",
                "portable validator accepts the extracted marketplace",
                "portable validator rejects changed installation instructions",
                "portable validator rejects source.path escape",
                "portable validator rejects extra runtime files",
                "portable validator rejects missing hook config",
                "portable validator rejects changed hook implementation",
                "portable validator rejects changed hook registration",
                "portable validator rejects manifest version drift",
                "portable validator rejects manifest hook override",
                "portable validator rejects changed hook ZIP member",
                "portable validator rejects changed installation ZIP member",
                "portable validator rejects ZIP path aliases before extraction",
                "portable validator rejects oversized compressed members before extraction",
                "portable validator bounds compressed archive bytes before parsing",
                "portable validator rejects linked archive inputs",
                "portable validator rejects duplicate ZIP members",
            ):
                record(name, False, built.stdout + built.stderr)
    finally:
        resolved_build = build_dir.resolve()
        resolved_dist = dist_root.resolve()
        if resolved_build.parent == resolved_dist and build_dir.name.startswith("distribution-eval-"):
            shutil.rmtree(build_dir)

    skill_validation = command([str(PORTABLE_SKILL_VALIDATOR), str(SKILL_DIR)], text=True)
    record(
        "canonical Skill quick validation passes",
        skill_validation.returncode == 0,
        skill_validation.stdout + skill_validation.stderr,
    )
    tracked_dist = subprocess.run(
        ["git", "ls-files", "dist"],
        cwd=SKILL_DIR,
        check=False,
        capture_output=True,
        text=True,
    )
    record(
        "generated dist stays ignored and untracked",
        "dist/" in (SKILL_DIR / ".gitignore").read_text(encoding="utf-8")
        and not tracked_dist.stdout.strip(),
        tracked_dist.stdout + tracked_dist.stderr,
    )
    return results


def threshold_value(value: str) -> float:
    try:
        result = float(value)
    except ValueError as exc:
        raise argparse.ArgumentTypeError("threshold must be numeric") from exc
    if not 0.98 <= result <= 1.0:
        raise argparse.ArgumentTypeError("threshold must be between 0.98 and 1.0")
    return result


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--threshold", type=threshold_value, default=1.0)
    parser.add_argument("--json", action="store_true")
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
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
