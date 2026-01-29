from __future__ import annotations

import os
import shutil
from dataclasses import dataclass
from pathlib import Path
from typing import Callable, Mapping

from .config import claude_cli_default, stream_to_text_script


@dataclass(frozen=True)
class AgentConfig:
    default_agent: str
    role_map: Mapping[str, str]
    prompt_map: Mapping[str, str]
    prompts_dir: Path


@dataclass(frozen=True)
class AgentSelection:
    agent: str
    cmd: list[str]


def normalize_agent_name(value: str | None) -> str:
    if not value:
        return "auto"
    return value.strip().lower()


def normalize_role_key(value: str) -> str:
    key = value.strip().lower()
    aliases = {
        "galph": "supervisor",
        "ralph": "loop",
    }
    return aliases.get(key, key)


def canonical_prompt_key(token: str, prompts_dir: Path) -> str:
    path = Path(token)
    if path.suffix != ".md":
        path = path.with_suffix(".md")
    if path.is_absolute():
        try:
            path = path.relative_to(prompts_dir)
        except ValueError:
            return path.as_posix()
    if path.parts and prompts_dir.name and path.parts[0] == prompts_dir.name:
        path = Path(*path.parts[1:])
    return path.as_posix()


def prompt_key_from_path(path: Path, prompts_dir: Path) -> str:
    try:
        rel = path.relative_to(prompts_dir)
        token = rel.as_posix()
    except ValueError:
        token = path.as_posix()
    return canonical_prompt_key(token, prompts_dir)


def parse_agent_map(raw: str, normalize_key: Callable[[str], str]) -> dict[str, str]:
    result: dict[str, str] = {}
    if not raw:
        return result
    for token in raw.split(","):
        token = token.strip()
        if not token:
            continue
        if "=" not in token:
            raise ValueError(f"Invalid agent mapping '{token}'. Expected key=value.")
        key, value = token.split("=", 1)
        key = normalize_key(key)
        value_norm = normalize_agent_name(value)
        if key:
            result[key] = value_norm
    return result


def normalize_role_map(raw_map: Mapping[str, str]) -> dict[str, str]:
    return {
        normalize_role_key(key): normalize_agent_name(value)
        for key, value in raw_map.items()
        if key and value
    }


def normalize_prompt_map(raw_map: Mapping[str, str], prompts_dir: Path) -> dict[str, str]:
    return {
        canonical_prompt_key(key, prompts_dir): normalize_agent_name(value)
        for key, value in raw_map.items()
        if key and value
    }


def resolve_agent(
    role: str,
    prompt_key: str,
    config: AgentConfig,
    cli_role_map: Mapping[str, str],
    cli_prompt_map: Mapping[str, str],
) -> str:
    role_key = normalize_role_key(role)
    prompt_key_norm = canonical_prompt_key(prompt_key, config.prompts_dir)
    if prompt_key_norm in cli_prompt_map:
        return cli_prompt_map[prompt_key_norm]
    if role_key in cli_role_map:
        return cli_role_map[role_key]
    if prompt_key_norm in config.prompt_map:
        return config.prompt_map[prompt_key_norm]
    if role_key in config.role_map:
        return config.role_map[role_key]
    return normalize_agent_name(config.default_agent)


def resolve_cmd(agent: str, claude_cmd: str, codex_cmd: str) -> list[str]:
    agent_norm = normalize_agent_name(agent)

    def _truthy_env(name: str, default: str = "0") -> bool:
        return os.getenv(name, default).strip().lower() in {"1", "true", "yes", "on"}

    def _use_unbuffered() -> bool:
        return os.getenv("ORCHESTRATION_PYTHONUNBUFFERED", "1").strip().lower() not in {"0", "false", "no"}

    def _use_stdbuf() -> bool:
        if os.getenv("ORCHESTRATION_USE_STDBUF", "1").strip().lower() in {"0", "false", "no"}:
            return False
        return shutil.which("stdbuf") is not None

    def _claude_stream_json() -> bool:
        return _truthy_env("ORCHESTRATION_CLAUDE_STREAM_JSON", "0")

    def _claude_force_tty() -> bool:
        if not _claude_stream_json():
            return False
        if os.getenv("ORCHESTRATION_CLAUDE_FORCE_TTY", "1").strip().lower() in {"0", "false", "no"}:
            return False
        return shutil.which("script") is not None

    def _codex_json() -> bool:
        return _truthy_env("ORCHESTRATION_CODEX_JSON", "0")

    def _wrap_cmd_list(cmd: list[str]) -> list[str]:
        prefix: list[str] = []
        if _use_unbuffered():
            prefix += ["env", "PYTHONUNBUFFERED=1"]
        if _use_stdbuf():
            prefix += ["stdbuf", "-oL", "-eL"]
        if prefix:
            return prefix + cmd
        return cmd

    def _shell_preamble() -> str:
        parts: list[str] = []
        if _use_unbuffered():
            parts.append("PYTHONUNBUFFERED=1")
        if _use_stdbuf():
            parts.append("stdbuf -oL -eL")
        if not parts:
            return ""
        return " ".join(parts) + " "

    def _claude_cmd() -> list[str] | None:
        def _fmt(path: Path | str) -> list[str]:
            quoted = str(path).replace('"', '\\"')
            if _claude_stream_json():
                stream_script = str(stream_to_text_script()).replace('"', '\\"')
                cmd_core = (
                    f'{_shell_preamble()}"{quoted}" -p --dangerously-skip-permissions --verbose '
                    f'--output-format stream-json --include-partial-messages'
                )
                if _claude_force_tty():
                    cmd_core = f"script -q /dev/null -c '{cmd_core}'"
                cmd_str = f'{_shell_preamble()}{cmd_core} | {_shell_preamble()}"python" "{stream_script}"'
            else:
                cmd_str = f'{_shell_preamble()}"{quoted}" -p --dangerously-skip-permissions --verbose --output-format text'
            return ["/bin/bash", "-lc", cmd_str]

        if claude_cmd:
            path = Path(claude_cmd)
            if path.is_file() and os.access(str(path), os.X_OK):
                return _fmt(path)
            which = shutil.which(claude_cmd)
            if which:
                return _fmt(which)

        default_cli = claude_cli_default()
        if default_cli:
            return _fmt(default_cli)
        return None

    def _codex_cmd() -> list[str] | None:
        codex_bin = shutil.which(codex_cmd) or codex_cmd
        if not codex_bin:
            return None
        cmd = [
            codex_bin,
            "exec",
            "-m",
            "gpt-5-codex",
            "-c",
            "model_reasoning_effort=high",
            "--dangerously-bypass-approvals-and-sandbox",
        ]
        if _codex_json():
            cmd.append("--json")
        return _wrap_cmd_list(cmd)

    if agent_norm == "claude":
        cmd = _claude_cmd()
        if not cmd:
            raise RuntimeError("Claude CLI not found; set --claude-cmd or choose --agent=codex.")
        return cmd
    if agent_norm == "codex":
        cmd = _codex_cmd()
        if not cmd:
            raise RuntimeError("Codex CLI not found; set --codex-cmd or choose --agent=claude.")
        return cmd
    if agent_norm != "auto":
        raise ValueError(f"Unsupported agent '{agent}'. Use auto, claude, or codex.")

    cmd = _claude_cmd()
    if cmd:
        return cmd
    cmd = _codex_cmd()
    if cmd:
        return cmd
    raise RuntimeError("Neither Claude nor Codex CLI could be resolved; configure --claude-cmd/--codex-cmd.")


def select_agent_cmd(
    role: str,
    prompt_key: str,
    config: AgentConfig,
    cli_role_map: Mapping[str, str],
    cli_prompt_map: Mapping[str, str],
    claude_cmd: str,
    codex_cmd: str,
) -> AgentSelection:
    agent = resolve_agent(role, prompt_key, config, cli_role_map, cli_prompt_map)
    cmd = resolve_cmd(agent, claude_cmd, codex_cmd)
    return AgentSelection(agent=agent, cmd=cmd)
