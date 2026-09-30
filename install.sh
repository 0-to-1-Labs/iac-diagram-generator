#!/usr/bin/env bash
# IaC Diagram Generator — legacy manual installer for Claude Code.
# Installs the skill standalone into ~/.claude/skills/.
#
# RECOMMENDED instead: install as a plugin via the marketplace —
#   /plugin marketplace add 0-to-1-Labs/claude-marketplace
#   /plugin install iac-diagram-generator@0-to-1-labs
#
# Source: https://github.com/0-to-1-Labs/iac-diagram-generator
#
# Usage: ./install.sh [--optional-parsers]
#   --optional-parsers  also install python-hcl2, tfparse and cfn-lint into
#                       the plugin's private Python environment.

set -e

SKILL_NAME="iac-diagram-generator"
SKILL_DIR="$HOME/.claude/skills/$SKILL_NAME"
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
WITH_OPTIONAL=0

for arg in "$@"; do
    case "$arg" in
        --optional-parsers) WITH_OPTIONAL=1 ;;
        -h|--help)
            sed -n '2,13p' "$0" | sed 's/^# \{0,1\}//'
            exit 0
            ;;
        *) echo "Unknown option: $arg"; exit 1 ;;
    esac
done

echo "=========================================="
echo "IaC Diagram Generator Installer"
echo "=========================================="
echo

# Check Python version first: nothing is copied if it cannot run.
echo "Checking Python installation..."
if ! command -v python3 &> /dev/null; then
    echo "ERROR: python3 is not installed."
    echo "Please install Python 3.10 or newer and try again."
    exit 1
fi

PYTHON_VERSION=$(python3 --version | cut -d' ' -f2)
if ! python3 -c 'import sys; sys.exit(0 if sys.version_info >= (3, 10) else 1)'; then
    echo "ERROR: Python 3.10+ is required (google-genai and tfparse need it). Found $PYTHON_VERSION."
    exit 1
fi
if ! python3 -c 'import venv' 2>/dev/null; then
    echo "ERROR: the Python 'venv' module is missing (on Debian/Ubuntu: sudo apt install python3-venv)."
    exit 1
fi
echo "Found Python $PYTHON_VERSION"
echo

# Check if Claude Code skills directory exists
if [ ! -d "$HOME/.claude/skills" ]; then
    echo "Creating Claude Code skills directory..."
    mkdir -p "$HOME/.claude/skills"
fi

# Check if skill already exists
if [ -d "$SKILL_DIR" ]; then
    echo "Skill already exists at $SKILL_DIR"
    read -p "Overwrite existing installation? (y/N) " -n 1 -r
    echo
    if [[ ! $REPLY =~ ^[Yy]$ ]]; then
        echo "Installation cancelled."
        exit 0
    fi
    echo "Removing existing installation..."
    rm -rf "$SKILL_DIR"
fi

# Copy skill files (the skill lives under skills/iac-diagram-generator/).
# SKILL.md references its scripts through ${CLAUDE_SKILL_DIR}, which Claude
# Code substitutes for personal skills too, so the copied layout just works.
echo "Installing skill files to $SKILL_DIR..."
mkdir -p "$SKILL_DIR"
cp -r "$SCRIPT_DIR/skills/$SKILL_NAME/." "$SKILL_DIR/"

# Make scripts executable
chmod +x "$SKILL_DIR"/scripts/*.py

# Python dependencies live in a private virtual environment, created by the
# scripts on first run. Without a plugin data directory they use
# ~/.cache/claude-iac-diagram-generator/venv. Nothing touches the system Python.
echo
if [ "$WITH_OPTIONAL" -eq 1 ]; then
    echo "Creating the Python environment with the optional parser tiers..."
    python3 "$SKILL_DIR/scripts/parse_iac.py" --install-optional
else
    echo "Python dependencies (pyyaml, google-genai) are installed on first run into"
    echo "  $HOME/.cache/claude-iac-diagram-generator/venv"
    echo "Re-run with --optional-parsers to add python-hcl2, tfparse and cfn-lint now."
fi
echo

# Check for GEMINI_API_KEY
if [ -z "$GEMINI_API_KEY" ]; then
    echo
    echo "=========================================="
    echo "GEMINI_API_KEY not set"
    echo "=========================================="
    echo
    echo "To generate diagrams, you need a Gemini API key."
    echo "Get one at: https://aistudio.google.com/apikey"
    echo
    echo "Then add to your shell configuration:"
    echo "  export GEMINI_API_KEY='your-api-key-here'"
    echo
fi

# Success message
echo
echo "=========================================="
echo "Installation complete!"
echo "=========================================="
echo
echo "The $SKILL_NAME skill is now installed."
echo "Claude Code will automatically use it when you ask to:"
echo "  - Analyze infrastructure code"
echo "  - Generate architecture diagrams"
echo "  - Visualize IaC resources"
echo
echo "Example prompts:"
echo "  'Generate an architecture diagram from my Terraform code'"
echo "  'Show me what this CloudFormation template deploys'"
echo "  'Diagram our Kubernetes application'"
echo
echo "Files installed to: $SKILL_DIR"
echo
