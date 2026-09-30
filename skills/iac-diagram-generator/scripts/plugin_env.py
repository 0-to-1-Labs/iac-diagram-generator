"""
Managed Python environment for the iac-diagram-generator scripts.

On first run the scripts create a virtual environment under the plugin's
persistent data directory, install requirements.txt into it, and re-run
themselves with that interpreter. Nothing is installed into the user's
system Python, and no `--break-system-packages` flag is ever used.

Location of the environment, in order of precedence:
  1. --data-dir <dir>              (the skill passes ${CLAUDE_PLUGIN_DATA})
  2. $CLAUDE_PLUGIN_DATA           (exported to hook processes)
  3. ~/.cache/claude-iac-diagram-generator

Set IAC_DIAGRAM_NO_VENV=1 to skip the managed environment and run with the
current interpreter (used by the test suite and by users who manage their
own environment).
"""

import hashlib
import os
import subprocess
import sys
from pathlib import Path

MIN_PYTHON = (3, 10)
SKILL_DIR = Path(__file__).resolve().parent.parent
REQUIREMENTS = SKILL_DIR / "requirements.txt"
REQUIREMENTS_OPTIONAL = SKILL_DIR / "requirements-optional.txt"
FALLBACK_DATA_DIR = Path.home() / ".cache" / "claude-iac-diagram-generator"
ACTIVE_MARKER = "IAC_DIAGRAM_VENV_ACTIVE"
SKIP_MARKER = "IAC_DIAGRAM_NO_VENV"


def check_python_version():
    """Exit with a clear message when the interpreter is too old."""
    if sys.version_info < MIN_PYTHON:
        want = ".".join(str(n) for n in MIN_PYTHON)
        have = ".".join(str(n) for n in sys.version_info[:3])
        print(f"ERROR: Python {want}+ is required (google-genai and tfparse need it). "
              f"Found Python {have} at {sys.executable}.")
        sys.exit(1)


def data_dir(explicit=None):
    """Return the persistent data directory for this plugin."""
    for candidate in (explicit, os.environ.get("CLAUDE_PLUGIN_DATA")):
        if candidate and candidate.strip():
            return Path(candidate).expanduser()
    return FALLBACK_DATA_DIR


def venv_dir(explicit=None):
    return data_dir(explicit) / "venv"


def venv_python(venv):
    if os.name == "nt":
        return venv / "Scripts" / "python.exe"
    return venv / "bin" / "python"


def _requirements_hash(paths):
    digest = hashlib.sha256()
    for path in paths:
        digest.update(path.read_bytes())
    return digest.hexdigest()


def _pip_install(python, args):
    """Run pip inside the venv. Returns True on success."""
    cmd = [str(python), "-m", "pip", "install", "--disable-pip-version-check", "-q", *args]
    result = subprocess.run(cmd, capture_output=True, text=True)
    if result.returncode != 0:
        print(result.stdout)
        print(result.stderr)
        return False
    return True


def ensure_venv(explicit_data_dir=None, optional=False):
    """
    Create or update the managed venv. Returns the path to its interpreter.

    `optional=True` also installs requirements-optional.txt (the parser
    upgrade tiers: python-hcl2, tfparse, cfn-lint).
    """
    check_python_version()
    venv = venv_dir(explicit_data_dir)
    python = venv_python(venv)

    if not python.exists():
        print(f"Creating Python environment: {venv}")
        venv.parent.mkdir(parents=True, exist_ok=True)
        try:
            subprocess.run([sys.executable, "-m", "venv", str(venv)], check=True,
                           capture_output=True, text=True)
        except subprocess.CalledProcessError as e:
            print("ERROR: Could not create a virtual environment.")
            print(e.stderr)
            print("On Debian/Ubuntu install the venv module: sudo apt install python3-venv")
            sys.exit(1)

    req_files = [REQUIREMENTS]
    stamp = venv / ".requirements.sha256"
    if optional:
        req_files.append(REQUIREMENTS_OPTIONAL)
        stamp = venv / ".requirements-optional.sha256"

    wanted = _requirements_hash(req_files)
    current = stamp.read_text().strip() if stamp.exists() else None
    if current != wanted:
        names = ", ".join(p.name for p in req_files)
        print(f"Installing dependencies ({names}) into {venv} ...")
        args = []
        for path in req_files:
            args += ["-r", str(path)]
        if not _pip_install(python, args):
            print(f"\nERROR: Failed to install dependencies into {venv}.")
            print("Check your network connection, or install manually with:")
            print(f"  {python} -m pip install " + " ".join(args))
            sys.exit(1)
        stamp.write_text(wanted)
        print("Dependencies installed.")

    return python


def reexec_in_venv(explicit_data_dir=None):
    """
    Re-run the current script inside the managed venv, unless we are already
    there (or the user opted out). Never returns when it re-executes; the
    child's exit code becomes ours.
    """
    if os.environ.get(ACTIVE_MARKER) or os.environ.get(SKIP_MARKER):
        return
    python = ensure_venv(explicit_data_dir)
    venv = python.parent.parent
    # Compare the environment, not the binary: a venv's bin/python is a
    # symlink to the base interpreter, so resolving executables would match.
    if Path(sys.prefix).resolve() == venv.resolve():
        return
    env = dict(os.environ, **{ACTIVE_MARKER: "1"})
    # subprocess instead of os.execv so the exit code propagates on every OS.
    result = subprocess.run([str(python), *sys.argv], env=env)
    sys.exit(result.returncode)


def running_in_managed_venv():
    return bool(os.environ.get(ACTIVE_MARKER))
