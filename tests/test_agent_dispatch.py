from __future__ import annotations

from pathlib import Path

from scripts.orchestration import agent_dispatch
from scripts.orchestration.agent_dispatch import (
    AgentConfig,
    canonical_prompt_key,
    normalize_prompt_map,
    normalize_role_key,
    normalize_role_map,
    parse_agent_map,
    resolve_agent,
    select_agent_cmd,
)
from scripts.orchestration.config import load_config


def test_agent_prompt_override_precedence(tmp_path: Path) -> None:
    cfg = AgentConfig(
        default_agent="auto",
        role_map=normalize_role_map({"supervisor": "codex"}),
        prompt_map=normalize_prompt_map({"supervisor.md": "claude"}, tmp_path),
        prompts_dir=tmp_path,
    )
    cli_role_map = parse_agent_map("supervisor=claude", normalize_role_key)
    cli_prompt_map = parse_agent_map(
        "supervisor.md=codex",
        lambda k: canonical_prompt_key(k, tmp_path),
    )

    agent = resolve_agent("supervisor", "supervisor.md", cfg, cli_role_map, cli_prompt_map)

    assert agent == "codex"


def test_agent_config_parsing(tmp_path: Path) -> None:
    config_path = tmp_path / "orchestration.yaml"
    config_path.write_text(
        "\n".join(
            [
                "agent:",
                "  default: codex",
                "  roles:",
                "    supervisor: claude",
                "    loop: codex",
                "  prompts:",
                "    supervisor.md: codex",
                "    main.md: claude",
            ]
        ),
        encoding="utf-8",
    )

    cfg = load_config(config_path=config_path, warn_missing=False)

    assert cfg.agent_default == "codex"
    assert cfg.agent_roles == {"supervisor": "claude", "loop": "codex"}
    assert cfg.agent_prompts == {"supervisor.md": "codex", "main.md": "claude"}


def test_agent_role_fallback(tmp_path: Path) -> None:
    cfg = AgentConfig(
        default_agent="auto",
        role_map=normalize_role_map({"loop": "claude"}),
        prompt_map=normalize_prompt_map({}, tmp_path),
        prompts_dir=tmp_path,
    )

    agent = resolve_agent("loop", "main.md", cfg, {}, {})

    assert agent == "claude"

def test_role_aliases_map_to_supervisor_loop(tmp_path: Path) -> None:
    cfg = AgentConfig(
        default_agent="auto",
        role_map=normalize_role_map({"supervisor": "claude", "loop": "codex"}),
        prompt_map=normalize_prompt_map({}, tmp_path),
        prompts_dir=tmp_path,
    )
    cli_role_map = parse_agent_map("galph=codex,ralph=claude", normalize_role_key)

    agent = resolve_agent("galph", "main.md", cfg, cli_role_map, {})
    assert agent == "codex"

    agent = resolve_agent("ralph", "main.md", cfg, cli_role_map, {})
    assert agent == "claude"


def test_router_prompt_drives_agent(tmp_path: Path) -> None:
    cfg = AgentConfig(
        default_agent="auto",
        role_map=normalize_role_map({"supervisor": "codex"}),
        prompt_map=normalize_prompt_map({"reviewer.md": "claude"}, tmp_path),
        prompts_dir=tmp_path,
    )

    agent = resolve_agent("supervisor", "reviewer.md", cfg, {}, {})

    assert agent == "claude"


def test_combined_uses_prompt_agent(monkeypatch, tmp_path: Path) -> None:
    cfg = AgentConfig(
        default_agent="auto",
        role_map=normalize_role_map({"supervisor": "codex"}),
        prompt_map=normalize_prompt_map({"reviewer.md": "claude"}, tmp_path),
        prompts_dir=tmp_path,
    )

    def _fake_resolve_cmd(agent: str, claude_cmd: str, codex_cmd: str) -> list[str]:
        return ["echo", agent]

    monkeypatch.setattr(agent_dispatch, "resolve_cmd", _fake_resolve_cmd)

    selection = select_agent_cmd(
        "supervisor",
        "reviewer.md",
        cfg,
        {},
        {},
        "",
        "",
    )

    assert selection.agent == "claude"
    assert selection.cmd == ["echo", "claude"]


def test_claude_stream_json_uses_runner(tmp_path: Path, monkeypatch) -> None:
    fake_claude = tmp_path / "claude"
    fake_claude.write_text("#!/bin/sh\nexit 0\n", encoding="utf-8")
    fake_claude.chmod(0o755)
    monkeypatch.setenv("ORCHESTRATION_CLAUDE_STREAM_JSON", "1")
    monkeypatch.setenv("ORCHESTRATION_PYTHONUNBUFFERED", "0")
    monkeypatch.setenv("ORCHESTRATION_USE_STDBUF", "0")

    cmd = agent_dispatch.resolve_cmd("claude", str(fake_claude), "codex")

    assert cmd[0] == "python"
    assert cmd[1].endswith("claude_stream_runner.py")
