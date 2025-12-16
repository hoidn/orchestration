#!/bin/bash
# Initialize spec bootstrapping for a project.
#
# This script:
# 1. Copies spec shard templates from the templates directory
# 2. Creates the docs structure if needed
# 3. Initializes the state file
# 4. Copies the prompts if they don't exist
#
# Usage: ./scripts/orchestration/init_spec_bootstrap.sh [templates_dir]
#
# The templates_dir defaults to ~/Documents/project-templates

set -e

TEMPLATES_DIR="${1:-$HOME/Documents/project-templates}"
SPECS_DIR="docs/spec-shards"
PROMPTS_DIR="prompts"
STATE_FILE="sync/spec_bootstrap_state.json"

echo "Initializing spec bootstrap..."
echo "Templates: $TEMPLATES_DIR"

# Validate templates directory
if [[ ! -d "$TEMPLATES_DIR" ]]; then
    echo "ERROR: Templates directory not found: $TEMPLATES_DIR"
    exit 1
fi

TEMPLATE_SPECS="$TEMPLATES_DIR/docs/spec-shards"
if [[ ! -d "$TEMPLATE_SPECS" ]]; then
    echo "ERROR: No spec-shards directory in templates: $TEMPLATE_SPECS"
    exit 1
fi

# Create directories
mkdir -p "$SPECS_DIR"
mkdir -p "$PROMPTS_DIR"
mkdir -p "$(dirname "$STATE_FILE")"
mkdir -p "docs"

# Copy spec shard templates
echo "Copying spec shard templates..."
for shard in "$TEMPLATE_SPECS"/spec-*.md; do
    if [[ -f "$shard" ]]; then
        name=$(basename "$shard")
        if [[ ! -f "$SPECS_DIR/$name" ]]; then
            cp "$shard" "$SPECS_DIR/$name"
            echo "  Created: $SPECS_DIR/$name"
        else
            echo "  Exists:  $SPECS_DIR/$name (skipped)"
        fi
    fi
done

# Copy index.md template if it exists and local doesn't
if [[ -f "$TEMPLATES_DIR/docs/index.md" ]] && [[ ! -f "docs/index.md" ]]; then
    cp "$TEMPLATES_DIR/docs/index.md" "docs/index.md"
    echo "  Created: docs/index.md"
fi

# Copy findings.md template if it exists and local doesn't
if [[ -f "$TEMPLATES_DIR/docs/findings.md" ]] && [[ ! -f "docs/findings.md" ]]; then
    cp "$TEMPLATES_DIR/docs/findings.md" "docs/findings.md"
    echo "  Created: docs/findings.md"
fi

# Initialize state file
if [[ ! -f "$STATE_FILE" ]]; then
    cat > "$STATE_FILE" << 'EOF'
{
  "iteration": 0,
  "scores": {
    "coverage": 0,
    "accuracy": 100,
    "consistency": 100
  },
  "modules_done": [],
  "task": null,
  "notes": "Initialized. Run spec_reviewer first to inventory implementation."
}
EOF
    echo "  Created: $STATE_FILE"
else
    echo "  Exists:  $STATE_FILE (skipped)"
fi

# Note about prompts
echo ""
echo "Prompts:"
if [[ -f "$PROMPTS_DIR/spec_reviewer.md" ]]; then
    echo "  Exists:  $PROMPTS_DIR/spec_reviewer.md"
else
    echo "  Missing: $PROMPTS_DIR/spec_reviewer.md"
    echo "           Copy from the orchestration submodule or another project"
fi
if [[ -f "$PROMPTS_DIR/spec_writer.md" ]]; then
    echo "  Exists:  $PROMPTS_DIR/spec_writer.md"
else
    echo "  Missing: $PROMPTS_DIR/spec_writer.md"
    echo "           Copy from the orchestration submodule or another project"
fi

echo ""
echo "Spec bootstrap initialized."
echo ""
echo "Next steps:"
echo "1. Review/customize orchestration.yaml spec_bootstrap section"
echo "2. Ensure prompts/spec_reviewer.md and prompts/spec_writer.md exist"
echo "3. Run the reviewer to inventory your implementation"
echo ""
echo "Discovered shards from templates:"
for shard in "$TEMPLATE_SPECS"/spec-*.md; do
    if [[ -f "$shard" ]]; then
        echo "  - $(basename "$shard")"
    fi
done
