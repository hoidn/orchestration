#!/bin/bash
# init_project.sh — Initialize project structure from templates
#
# Creates the full project documentation and operational structure from
# ~/Documents/project-templates (or specified templates directory).
#
# Prerequisites:
#   - scripts/orchestration/ submodule already cloned (this is where this script lives)
#   - prompts/ directory already populated with agent prompts
#
# Usage: ./scripts/orchestration/init_project.sh [OPTIONS] [templates_dir]
#
# Options:
#   --force           Overwrite existing files (default: skip existing)
#   --dry-run         Show what would be done without doing it
#   --spec-bootstrap  Also initialize spec bootstrap state file
#   -v, --verbose     Show all files including skipped ones
#   -h, --help        Show this help message
#
# The templates_dir defaults to:
#   1. $PROJECT_TEMPLATES_DIR environment variable (if set)
#   2. ~/Documents/project-templates
#
# What gets created:
#
#   Root files:
#     CLAUDE.md, orchestration.yaml, README.md, PROJECT_STATUS.md, input.md,
#     galph_memory.md
#
#   Directories:
#     docs/, docs/architecture/, docs/architecture/contracts/, docs/debugging/,
#     docs/development/, docs/spec-shards/, plans/, plans/active/,
#     plans/templates/, inbox/, outbox/, sync/
#
#   Documentation:
#     docs/index.md, docs/fix_plan.md, docs/findings.md
#     docs/architecture/README.md, docs/architecture/component.md
#     docs/architecture/contracts/module.idl.md
#     docs/debugging/*.md (troubleshooting guides)
#     docs/development/*.md (CONTRIBUTING.md, testing_strategy.md)
#     docs/spec-shards/spec-*.md (spec shard templates)
#     plans/templates/plan.md
#
# What is NOT touched:
#   prompts/         — User-provided, must already exist
#   scripts/         — User-provided (orchestration submodule)
#   src/, tests/     — Project implementation code
#
# After running, user should either:
#
#   A. Run interactive initialization (recommended):
#      claude "Follow prompts/init_project_interactive.md to initialize this project"
#      This will guide you through populating templates with project-specific content.
#
#   B. Manually customize:
#      1. Customize CLAUDE.md with project-specific rules
#      2. Customize orchestration.yaml (set implementation.dirs)
#      3. Replace [PLACEHOLDERS] in README.md, PROJECT_STATUS.md, docs/
#      4. Run with --spec-bootstrap if extracting specs from existing code

set -e

# ============================================================================
# Configuration
# ============================================================================

TEMPLATES_DIR="${PROJECT_TEMPLATES_DIR:-$HOME/Documents/project-templates}"
FORCE=false
DRY_RUN=false
SPEC_BOOTSTRAP=false
VERBOSE=false

# Counters for summary
DIRS_CREATED=0
FILES_CREATED=0
FILES_SKIPPED=0

# ============================================================================
# Helper Functions
# ============================================================================

show_help() {
    sed -n '2,/^set -e/p' "$0" | grep '^#' | sed 's/^# \?//'
}

log() {
    echo "$@"
}

log_verbose() {
    [[ "$VERBOSE" == "true" ]] && echo "$@" || true
}

die() {
    echo "ERROR: $*" >&2
    exit 1
}

ensure_dir() {
    local dir="$1"

    if [[ -d "$dir" ]]; then
        log_verbose "  Exists:  $dir/"
        return 0
    fi

    if [[ "$DRY_RUN" == "true" ]]; then
        log "  Would create: $dir/"
        ((DIRS_CREATED++)) || true
        return 0
    fi

    mkdir -p "$dir"
    log "  Created: $dir/"
    ((DIRS_CREATED++)) || true
}

copy_file() {
    local src="$1"
    local dst="$2"

    # Check if source exists
    if [[ ! -f "$src" ]]; then
        log_verbose "  Missing template: $src"
        return 0
    fi

    # Check if destination exists
    if [[ -f "$dst" ]] && [[ "$FORCE" != "true" ]]; then
        log_verbose "  Exists:  $dst (skipped)"
        ((FILES_SKIPPED++)) || true
        return 0
    fi

    # Dry run mode
    if [[ "$DRY_RUN" == "true" ]]; then
        if [[ -f "$dst" ]]; then
            log "  Would overwrite: $dst"
        else
            log "  Would create: $dst"
        fi
        ((FILES_CREATED++)) || true
        return 0
    fi

    # Ensure parent directory exists
    mkdir -p "$(dirname "$dst")"

    # Copy the file
    cp "$src" "$dst"

    if [[ "$FORCE" == "true" ]] && [[ -f "$dst" ]]; then
        log "  Overwrote: $dst"
    else
        log "  Created: $dst"
    fi
    ((FILES_CREATED++)) || true
}

copy_dir_files() {
    local src_dir="$1"
    local dst_dir="$2"
    local pattern="${3:-*.md}"

    if [[ ! -d "$src_dir" ]]; then
        log_verbose "  Missing template dir: $src_dir"
        return 0
    fi

    # Use nullglob to handle no matches gracefully
    local files=()
    while IFS= read -r -d '' f; do
        files+=("$f")
    done < <(find "$src_dir" -maxdepth 1 -name "$pattern" -type f -print0 2>/dev/null | sort -z)

    if [[ ${#files[@]} -eq 0 ]]; then
        log_verbose "  No files matching $pattern in $src_dir"
        return 0
    fi

    for f in "${files[@]}"; do
        copy_file "$f" "$dst_dir/$(basename "$f")"
    done
}

# ============================================================================
# Validation
# ============================================================================

validate_templates_dir() {
    if [[ ! -d "$TEMPLATES_DIR" ]]; then
        die "Templates directory not found: $TEMPLATES_DIR"
    fi

    if [[ ! -d "$TEMPLATES_DIR/docs" ]]; then
        die "Invalid templates directory (missing docs/): $TEMPLATES_DIR"
    fi

    log_verbose "Templates directory validated: $TEMPLATES_DIR"
}

validate_project_root() {
    # Must have .git or scripts/orchestration to be a plausible project root
    if [[ ! -d ".git" ]] && [[ ! -d "scripts/orchestration" ]]; then
        die "Not in a project root directory (no .git or scripts/orchestration found)"
    fi

    log_verbose "Project root validated: $(pwd)"
}

# ============================================================================
# Initialization Functions
# ============================================================================

init_directories() {
    log "Creating directories..."

    # Core docs structure
    ensure_dir "docs"
    ensure_dir "docs/architecture"
    ensure_dir "docs/architecture/contracts"
    ensure_dir "docs/debugging"
    ensure_dir "docs/development"
    ensure_dir "docs/spec-shards"

    # Plans structure
    ensure_dir "plans"
    ensure_dir "plans/active"
    ensure_dir "plans/templates"

    # Communication directories
    ensure_dir "inbox"
    ensure_dir "outbox"

    # State directory
    ensure_dir "sync"
}

init_root_files() {
    log ""
    log "Copying root files..."

    copy_file "$TEMPLATES_DIR/CLAUDE.md" "CLAUDE.md"
    copy_file "$TEMPLATES_DIR/orchestration.yaml" "orchestration.yaml"
    copy_file "$TEMPLATES_DIR/README.md" "README.md"
    copy_file "$TEMPLATES_DIR/PROJECT_STATUS.md" "PROJECT_STATUS.md"
    copy_file "$TEMPLATES_DIR/input.md" "input.md"
    copy_file "$TEMPLATES_DIR/galph_memory.md" "galph_memory.md"
}

init_docs() {
    log ""
    log "Copying docs..."

    copy_file "$TEMPLATES_DIR/docs/index.md" "docs/index.md"
    copy_file "$TEMPLATES_DIR/docs/fix_plan.md" "docs/fix_plan.md"
    copy_file "$TEMPLATES_DIR/docs/findings.md" "docs/findings.md"
}

init_architecture() {
    log ""
    log "Copying architecture templates..."

    copy_file "$TEMPLATES_DIR/docs/architecture/README.md" "docs/architecture/README.md"
    copy_file "$TEMPLATES_DIR/docs/architecture/component.md" "docs/architecture/component.md"
    copy_file "$TEMPLATES_DIR/docs/architecture/contracts/module.idl.md" \
              "docs/architecture/contracts/module.idl.md"
}

init_debugging() {
    log ""
    log "Copying debugging docs..."

    copy_dir_files "$TEMPLATES_DIR/docs/debugging" "docs/debugging" "*.md"
}

init_development() {
    log ""
    log "Copying development docs..."

    copy_dir_files "$TEMPLATES_DIR/docs/development" "docs/development" "*.md"
}

init_spec_shards() {
    log ""
    log "Copying spec shard templates..."

    copy_dir_files "$TEMPLATES_DIR/docs/spec-shards" "docs/spec-shards" "spec-*.md"
}

init_plans() {
    log ""
    log "Copying plan templates..."

    copy_file "$TEMPLATES_DIR/plans/templates/plan.md" "plans/templates/plan.md"
}

init_spec_bootstrap_state() {
    local state_file="sync/spec_bootstrap_state.json"

    log ""
    log "Initializing spec bootstrap state..."

    if [[ -f "$state_file" ]] && [[ "$FORCE" != "true" ]]; then
        log_verbose "  Exists:  $state_file (skipped)"
        ((FILES_SKIPPED++)) || true
        return 0
    fi

    if [[ "$DRY_RUN" == "true" ]]; then
        if [[ -f "$state_file" ]]; then
            log "  Would overwrite: $state_file"
        else
            log "  Would create: $state_file"
        fi
        ((FILES_CREATED++)) || true
        return 0
    fi

    cat > "$state_file" << 'EOF'
{
  "iteration": 0,
  "phase": "inventory",
  "scores": {
    "coverage": 0,
    "accuracy": 100,
    "consistency": 100,
    "domain_completeness": 0
  },
  "inventory": {
    "total_modules": 0,
    "total_behaviors": 0,
    "spec_statements": 0,
    "domain_concepts": 0,
    "domain_concepts_specified": 0
  },
  "modules": {},
  "modules_done": [],
  "task": null,
  "domain_checklist": [],
  "threshold_checklist": [],
  "notes": "Initialized. Run spec_reviewer to inventory implementation."
}
EOF

    log "  Created: $state_file"
    ((FILES_CREATED++)) || true
}

# ============================================================================
# Summary
# ============================================================================

print_summary() {
    log ""
    log "=========================================="

    if [[ "$DRY_RUN" == "true" ]]; then
        log "DRY RUN COMPLETE"
        log ""
        log "Would create: $DIRS_CREATED directories, $FILES_CREATED files"
        [[ $FILES_SKIPPED -gt 0 ]] && log "Would skip: $FILES_SKIPPED existing files"
    else
        log "PROJECT STRUCTURE INITIALIZED"
        log ""
        log "Created: $DIRS_CREATED directories, $FILES_CREATED files"
        [[ $FILES_SKIPPED -gt 0 ]] && log "Skipped: $FILES_SKIPPED existing files"
    fi

    log ""
    log "Next steps:"
    log ""
    log "RECOMMENDED: Run interactive initialization to populate templates:"
    log ""
    log "  claude \"Follow prompts/init_project_interactive.md to initialize this project\""
    log ""
    log "This will guide you through setting project name, paths, domain concepts,"
    log "and workflow stages — then populate all the template files automatically."
    log ""
    log "ALTERNATIVE: Manually edit the template files:"
    log "  1. CLAUDE.md — Set Quick Router paths, dependencies, exceptions"
    log "  2. orchestration.yaml — Set spec_bootstrap.implementation.dirs"
    log "  3. README.md, PROJECT_STATUS.md — Replace [PLACEHOLDERS]"
    log ""

    if [[ "$SPEC_BOOTSTRAP" != "true" ]]; then
        log "After initialization, if you have existing code, run:"
        log "  ./scripts/orchestration/init_project.sh --spec-bootstrap"
        log ""
    else
        log "Spec bootstrap state initialized. Run spec_reviewer to inventory"
        log "your implementation and generate detailed specifications."
        log ""
    fi

    log "=========================================="
}

# ============================================================================
# Argument Parsing
# ============================================================================

parse_args() {
    while [[ $# -gt 0 ]]; do
        case "$1" in
            --force)
                FORCE=true
                shift
                ;;
            --dry-run)
                DRY_RUN=true
                shift
                ;;
            --spec-bootstrap)
                SPEC_BOOTSTRAP=true
                shift
                ;;
            -v|--verbose)
                VERBOSE=true
                shift
                ;;
            -h|--help)
                show_help
                exit 0
                ;;
            -*)
                die "Unknown option: $1 (use --help for usage)"
                ;;
            *)
                TEMPLATES_DIR="$1"
                shift
                ;;
        esac
    done
}

# ============================================================================
# Main
# ============================================================================

main() {
    parse_args "$@"

    # Validation
    validate_templates_dir
    validate_project_root

    # Header
    log "Initializing project structure..."
    log "Templates: $TEMPLATES_DIR"
    log "Target:    $(pwd)"
    [[ "$FORCE" == "true" ]] && log "Mode:      --force (overwriting existing files)"
    [[ "$DRY_RUN" == "true" ]] && log "Mode:      --dry-run (no changes will be made)"
    log ""

    # Create structure
    init_directories
    init_root_files
    init_docs
    init_architecture
    init_debugging
    init_development
    init_spec_shards
    init_plans

    # Optional: spec bootstrap state
    if [[ "$SPEC_BOOTSTRAP" == "true" ]]; then
        init_spec_bootstrap_state
    fi

    # Summary
    print_summary
}

main "$@"
