#!/usr/bin/env bash
# IaC Diagram Generator — legacy manual installer for Claude Code.
# Installs the skill standalone into ~/.claude/skills/.
#
# RECOMMENDED instead: install as a plugin via the marketplace —
#   /plugin marketplace add 0-to-1-Labs/claude-marketplace
#   /plugin install iac-diagram-generator@0-to-1-labs

set -e

SKILL_NAME="iac-diagram-generator"
SKILL_DIR="$HOME/.claude/skills/$SKILL_NAME"
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

echo "=========================================="
echo "IaC Diagram Generator Installer"
echo "=========================================="
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

# Copy skill files (the skill lives under skills/iac-diagram-generator/)
echo "Installing skill files to $SKILL_DIR..."
mkdir -p "$SKILL_DIR"
cp -r "$SCRIPT_DIR/skills/$SKILL_NAME/." "$SKILL_DIR/"

# Make scripts executable
chmod +x "$SKILL_DIR"/scripts/*.py

# Check Python version
echo
echo "Checking Python installation..."
if ! command -v python3 &> /dev/null; then
    echo "ERROR: python3 is not installed."
    echo "Please install Python 3 and try again."
    exit 1
fi

PYTHON_VERSION=$(python3 --version | cut -d' ' -f2)
echo "Found Python $PYTHON_VERSION"

# Install Python dependencies
echo
echo "Installing Python dependencies..."
echo "Required: pyyaml"
echo "Optional: python-hcl2 (better Terraform parsing)"
echo "Optional: tfparse (best Terraform parsing, requires terraform init)"
echo "Optional: cfn-lint (better CloudFormation parsing)"

# Function to install a package
install_package() {
    local package=$1
    local optional=$2

    if python3 -m pip install "$package" --quiet 2>/dev/null; then
        echo "  ✓ $package installed"
        return 0
    elif python3 -m pip install "$package" --user --quiet 2>/dev/null; then
        echo "  ✓ $package installed (user)"
        return 0
    elif python3 -m pip install "$package" --break-system-packages --quiet 2>/dev/null; then
        echo "  ✓ $package installed (system)"
        return 0
    else
        if [ "$optional" = "optional" ]; then
            echo "  ⚠ $package not installed (optional)"
        else
            echo "  ✗ $package failed to install"
        fi
        return 1
    fi
}

echo
# Required
install_package "pyyaml" "required" || {
    echo "WARNING: Could not install pyyaml automatically."
    echo "Please install manually: pip install pyyaml"
}

# Optional - better Terraform parsing
install_package "python-hcl2" "optional"

# Optional - best Terraform parsing (requires Python 3.10+)
PYTHON_MINOR=$(python3 -c 'import sys; print(sys.version_info.minor)')
if [ "$PYTHON_MINOR" -ge 10 ]; then
    install_package "tfparse" "optional"
else
    echo "  ⚠ tfparse skipped (requires Python 3.10+, you have 3.$PYTHON_MINOR)"
fi

# Optional - better CloudFormation parsing
install_package "cfn-lint" "optional"

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
