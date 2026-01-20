"""
Orchestration configuration loader.

Provides portable config lookup for galph/ralph orchestration scripts.
Config is loaded from orchestration.yaml found by searching upward from CWD,
with sensible defaults when no config file exists.
"""
from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional

# Optional YAML support — fall back to defaults if pyyaml not available
try:
    import yaml
    _HAS_YAML = True
except ImportError:
    _HAS_YAML = False


CONFIG_FILENAME = "orchestration.yaml"


@dataclass
class OrchConfig:
    """Configuration for orchestration scripts."""

    # Prompt paths
    prompts_dir: Path = field(default_factory=lambda: Path("prompts"))
    supervisor_prompt: str = "supervisor.md"
    main_prompt: str = "main.md"
    reviewer_prompt: str = "reviewer.md"

    # Router configuration
    router_enabled: bool = False
    router_prompt: Optional[str] = None
    router_review_every_n: int = 0
    router_allowlist: list[str] = field(default_factory=list)
    router_mode: str = "router_default"

    # State management
    state_file: Path = field(default_factory=lambda: Path("sync/state.json"))

    # Doc/meta auto-commit whitelist (glob patterns)
    doc_whitelist: list[str] = field(default_factory=lambda: [
        "input.md",
        "galph_memory.md",
        "docs/fix_plan.md",
        "plans/**/*.md",
        "prompts/**/*.md",
        ".gitignore",
        ".gitmodules",
        ".gitattributes",
    ])

    # Tracked output globs for auto-commit (e.g., regenerated fixtures)
    tracked_output_globs: list[str] = field(default_factory=lambda: [
        "tests/fixtures/**/*.npy",
        "tests/fixtures/**/*.npz",
        "tests/fixtures/**/*.json",
        "tests/fixtures/**/*.pkl",
    ])

    # Key file paths
    findings_file: Path = field(default_factory=lambda: Path("docs/findings.md"))
    input_file: Path = field(default_factory=lambda: Path("input.md"))

    # Directories
    logs_dir: Path = field(default_factory=lambda: Path("logs"))
    tmp_dir: Path = field(default_factory=lambda: Path("tmp"))

    # Report extensions for auto-commit
    report_extensions: list[str] = field(default_factory=lambda: [
        ".png", ".jpeg", ".npy", ".txt", ".md", ".json", ".log", ".py", ".c", ".h", ".sh"
    ])

    # Tracked output extensions
    tracked_output_extensions: list[str] = field(default_factory=lambda: [
        ".npy", ".npz", ".json", ".pkl"
    ])

    # Project root (set by find_config or load_config)
    project_root: Optional[Path] = None

    # Spec bootstrap configuration (optional, for bootstrapping specs from impl)
    spec_bootstrap: Optional["SpecBootstrapConfig"] = None


@dataclass
class SpecBootstrapConfig:
    """Configuration for spec bootstrapping process."""

    # External templates directory — the single source of truth for spec structure.
    # Shards are discovered from {templates_dir}/docs/spec-shards/*.md
    templates_dir: Path = field(default_factory=lambda: Path("~/Documents/project-templates").expanduser())

    # Local spec directory (where specs are written)
    specs_dir: Path = field(default_factory=lambda: Path("docs/spec-shards"))

    # Implementation source
    impl_dirs: list[str] = field(default_factory=lambda: ["src/"])
    impl_entry_points: list[str] = field(default_factory=list)
    impl_exclude: list[str] = field(default_factory=lambda: [
        "**/__pycache__/**",
        "**/tests/**",
        "**/*.pyc",
    ])

    # Scoring thresholds
    coverage_threshold: int = 80
    accuracy_threshold: int = 85
    consistency_threshold: int = 90

    # State file
    state_file: Path = field(default_factory=lambda: Path("sync/spec_bootstrap_state.json"))

    # Prompts
    reviewer_prompt: str = "spec_reviewer.md"
    writer_prompt: str = "spec_writer.md"

    def discover_shards(self) -> list[str]:
        """
        Discover spec shards from the templates directory.

        Returns list of shard filenames (e.g., ['spec-db-core.md', 'spec-db-workflow.md']).
        """
        template_specs_dir = self.templates_dir / "docs" / "spec-shards"
        if not template_specs_dir.is_dir():
            return []
        return sorted([
            f.name for f in template_specs_dir.iterdir()
            if f.is_file() and f.suffix == ".md" and f.name.startswith("spec-")
        ])


def find_config(start_dir: Optional[Path] = None) -> Optional[Path]:
    """
    Search upward from start_dir (default: CWD) for orchestration.yaml.

    Returns the path to the config file if found, None otherwise.
    """
    if start_dir is None:
        start_dir = Path.cwd()

    current = start_dir.resolve()

    # Search upward until we hit the filesystem root
    while current != current.parent:
        candidate = current / CONFIG_FILENAME
        if candidate.is_file():
            return candidate
        current = current.parent

    # Check root as well
    candidate = current / CONFIG_FILENAME
    if candidate.is_file():
        return candidate

    return None


def load_config(config_path: Optional[Path] = None, warn_missing: bool = True) -> OrchConfig:
    """
    Load orchestration config from YAML file.

    Args:
        config_path: Explicit path to config file. If None, searches upward from CWD.
        warn_missing: If True, print warning when no config file found.

    Returns:
        OrchConfig with values from file merged over defaults.
    """
    cfg = OrchConfig()

    # Find config file if not provided
    if config_path is None:
        config_path = find_config()

    if config_path is None:
        if warn_missing:
            print("[orchestration] WARNING: No orchestration.yaml found; using defaults")
        return cfg

    # Set project root to the directory containing the config file
    cfg.project_root = config_path.parent

    # Read config file
    if not _HAS_YAML:
        print("[orchestration] WARNING: pyyaml not installed; using defaults")
        return cfg

    try:
        with open(config_path, "r", encoding="utf-8") as f:
            data = yaml.safe_load(f) or {}
    except Exception as e:
        print(f"[orchestration] WARNING: Failed to load {config_path}: {e}")
        return cfg

    # Merge loaded values over defaults
    if "prompts_dir" in data:
        cfg.prompts_dir = Path(data["prompts_dir"])
    if "supervisor_prompt" in data:
        cfg.supervisor_prompt = data["supervisor_prompt"]
    if "main_prompt" in data:
        cfg.main_prompt = data["main_prompt"]
    if "reviewer_prompt" in data:
        cfg.reviewer_prompt = data["reviewer_prompt"]
    if "state_file" in data:
        cfg.state_file = Path(data["state_file"])
    if "doc_whitelist" in data:
        cfg.doc_whitelist = list(data["doc_whitelist"])
    if "tracked_output_globs" in data:
        cfg.tracked_output_globs = list(data["tracked_output_globs"])
    if "findings_file" in data:
        cfg.findings_file = Path(data["findings_file"])
    if "input_file" in data:
        cfg.input_file = Path(data["input_file"])
    if "logs_dir" in data:
        cfg.logs_dir = Path(data["logs_dir"])
    if "tmp_dir" in data:
        cfg.tmp_dir = Path(data["tmp_dir"])
    if "report_extensions" in data:
        cfg.report_extensions = list(data["report_extensions"])
    if "tracked_output_extensions" in data:
        cfg.tracked_output_extensions = list(data["tracked_output_extensions"])

    # Router settings (top-level)
    if "router_enabled" in data:
        cfg.router_enabled = bool(data["router_enabled"])
    if "router_prompt" in data:
        cfg.router_prompt = data["router_prompt"] or None
    if "router_review_every_n" in data:
        cfg.router_review_every_n = int(data["router_review_every_n"])
    if "router_allowlist" in data:
        cfg.router_allowlist = list(data["router_allowlist"])
    if "router_mode" in data:
        cfg.router_mode = str(data["router_mode"])

    # Router settings (nested section)
    if "router" in data:
        router = data["router"] or {}
        if "enabled" in router:
            cfg.router_enabled = bool(router["enabled"])
        if "prompt" in router:
            cfg.router_prompt = router["prompt"] or None
        if "review_every_n" in router:
            cfg.router_review_every_n = int(router["review_every_n"])
        if "allowlist" in router:
            cfg.router_allowlist = list(router["allowlist"])
        if "mode" in router:
            cfg.router_mode = str(router["mode"])

    # Parse spec_bootstrap section if present
    if "spec_bootstrap" in data:
        sb_data = data["spec_bootstrap"]
        sb_cfg = SpecBootstrapConfig()

        if "templates_dir" in sb_data:
            sb_cfg.templates_dir = Path(sb_data["templates_dir"]).expanduser()

        # Specs section
        if "specs" in sb_data:
            specs = sb_data["specs"]
            if "dir" in specs:
                sb_cfg.specs_dir = Path(specs["dir"])
            # Note: shards are discovered from templates_dir, not configured

        # Implementation section
        if "implementation" in sb_data:
            impl = sb_data["implementation"]
            if "dirs" in impl:
                sb_cfg.impl_dirs = list(impl["dirs"])
            if "entry_points" in impl:
                sb_cfg.impl_entry_points = list(impl["entry_points"])
            if "exclude" in impl:
                sb_cfg.impl_exclude = list(impl["exclude"])

        # Scoring section
        if "scoring" in sb_data:
            scoring = sb_data["scoring"]
            if "coverage" in scoring:
                sb_cfg.coverage_threshold = int(scoring["coverage"])
            if "accuracy" in scoring:
                sb_cfg.accuracy_threshold = int(scoring["accuracy"])
            if "consistency" in scoring:
                sb_cfg.consistency_threshold = int(scoring["consistency"])

        if "state_file" in sb_data:
            sb_cfg.state_file = Path(sb_data["state_file"])

        # Prompts section
        if "prompts" in sb_data:
            prompts = sb_data["prompts"]
            if "reviewer" in prompts:
                sb_cfg.reviewer_prompt = prompts["reviewer"]
            if "writer" in prompts:
                sb_cfg.writer_prompt = prompts["writer"]

        cfg.spec_bootstrap = sb_cfg

    return cfg


def stream_to_text_script() -> Path:
    """
    Return path to claude_stream_to_text.py relative to this module.

    Uses Path(__file__) to locate the script portably, rather than
    hardcoding absolute or project-relative paths.
    """
    return Path(__file__).parent / "claude_stream_to_text.py"


def claude_cli_default() -> Optional[Path]:
    """
    Return the default Claude CLI path, searching in order:
    1. Repo-local .claude/local/claude
    2. User home ~/.claude/local/claude
    3. 'claude' on PATH

    Returns None if not found.
    """
    import shutil

    # Repo-local
    repo_local = Path(".claude") / "local" / "claude"
    if repo_local.is_file() and os.access(str(repo_local), os.X_OK):
        return repo_local

    # User home
    home_local = Path.home() / ".claude" / "local" / "claude"
    if home_local.is_file() and os.access(str(home_local), os.X_OK):
        return home_local

    # PATH
    which = shutil.which("claude")
    if which:
        return Path(which)

    return None
