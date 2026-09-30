#!/usr/bin/env python3
"""
IaC Parser
Parses Infrastructure as Code files and extracts resource information.
Supports: Terraform, CloudFormation, Kubernetes, Docker Compose
Accepts: Local paths or GitHub repository URLs

Dependencies are installed on first run into a private virtual environment
(see plugin_env.py). Optional parser tiers (python-hcl2, tfparse, cfn-lint)
are installed with `--install-optional`.
"""

import argparse
import os
import sys
import json
import glob as file_glob
import tempfile
import shutil
import subprocess
import re
from pathlib import Path

import plugin_env


def bootstrap(argv=None):
    """
    Parse the arguments that control the runtime, then re-run inside the
    managed venv when needed. Returns the parsed arguments.
    """
    args = parse_args(argv)
    if args.install_optional:
        plugin_env.check_python_version()
        python = plugin_env.ensure_venv(args.data_dir, optional=True)
        print(f"Optional parsers installed. Interpreter: {python}")
        sys.exit(0)
    plugin_env.reexec_in_venv(args.data_dir)
    return args


def parse_args(argv=None):
    parser = argparse.ArgumentParser(
        description="Parse IaC files into a JSON resource graph.",
        epilog=(
            "Path can be a local file or directory, or a GitHub URL such as\n"
            "  https://github.com/user/repo\n"
            "  https://github.com/user/repo/tree/<branch>/<subdir>\n"
            "  https://github.com/user/repo/blob/<branch>/<file>\n"
            "  github.com/user/repo, git@github.com:user/repo"
        ),
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument("format", nargs="?",
                        help="terraform | cloudformation | kubernetes | docker-compose")
    parser.add_argument("path", nargs="?", help="Local path or GitHub URL")
    parser.add_argument(
        "--data-dir", default=None, metavar="DIR",
        help="Plugin data directory for the managed Python environment "
             "(the skill passes ${CLAUDE_PLUGIN_DATA}).",
    )
    parser.add_argument(
        "--install-optional", action="store_true",
        help="Install the optional parser tiers (python-hcl2, tfparse, cfn-lint) "
             "into the managed environment and exit.",
    )
    return parser.parse_args(argv)


if __name__ == "__main__":
    ARGS = bootstrap()

try:
    import yaml
except ImportError:
    print("ERROR: PyYAML is not importable in this interpreter.")
    print(f"  Interpreter: {sys.executable}")
    print(f"  Install it with: {sys.executable} -m pip install -r {plugin_env.REQUIREMENTS}")
    sys.exit(1)

# Optional: tfparse for accurate Terraform parsing (requires terraform init)
try:
    from tfparse import load_from_path as tfparse_load
    TFPARSE_AVAILABLE = True
except ImportError:
    TFPARSE_AVAILABLE = False

# Optional: python-hcl2 for HCL2 parsing without terraform init
try:
    import hcl2
    HCL2_AVAILABLE = True
except ImportError:
    HCL2_AVAILABLE = False

# Optional: cfn-lint for accurate CloudFormation parsing
try:
    from cfnlint.decode import decode as cfnlint_decode
    CFNLINT_AVAILABLE = True
except ImportError:
    CFNLINT_AVAILABLE = False


# Keys whose values are redacted from parser output (IaC often carries secrets,
# and the JSON goes into the model context).
SECRET_KEY_PATTERN = re.compile(
    r'(?i)(password|passwd|pwd|secret|token|credential|connection[_-]?string|\w[_-]?key$)'
)
# Keys that name or point at a secret rather than hold one (secretName,
# secretKeyRef, KeyName, kms_key_id, data_keys, ...) stay visible.
NOT_SECRET_SUFFIX = re.compile(r'(?i)(name|ref|id|ids|arn|keys|type|version|length|policy|enabled)$')
REDACTED = "[REDACTED]"


def is_secret_key(key):
    return isinstance(key, str) and bool(SECRET_KEY_PATTERN.search(key)) \
        and not NOT_SECRET_SUFFIX.search(key)

# Clone limits
CLONE_TIMEOUT = 120
GIT_ENV = dict(os.environ, GIT_TERMINAL_PROMPT="0")

GITHUB_OWNER_REPO = r'[\w\-\.]+/[\w\-\.]+'
GIT_REF_PATTERN = re.compile(r'^[\w][\w\-\./]*$')


def is_github_url(path):
    """Check if the path is a GitHub URL."""
    github_patterns = [
        r'^https?://github\.com/' + GITHUB_OWNER_REPO,
        r'^git@github\.com:' + GITHUB_OWNER_REPO,
        r'^github\.com/' + GITHUB_OWNER_REPO,
    ]
    for pattern in github_patterns:
        if re.match(pattern, path):
            return True
    return False


def normalize_github_url(url):
    """Normalize GitHub URL to HTTPS clone format."""
    # Remove trailing slashes, then a single trailing ".git" suffix.
    # (rstrip('.git') would strip any trailing '.', 'g', 'i', 't' chars,
    # corrupting repo names like "...config" or "...integration".)
    url = url.rstrip('/')
    if url.endswith('.git'):
        url = url[:-len('.git')]

    # Handle different formats
    if url.startswith('git@github.com:'):
        # git@github.com:user/repo -> https://github.com/user/repo
        url = url.replace('git@github.com:', 'https://github.com/')
    elif url.startswith('github.com/'):
        # github.com/user/repo -> https://github.com/user/repo
        url = 'https://' + url
    elif not url.startswith('http'):
        url = 'https://' + url

    return url + '.git'


def valid_git_ref(ref):
    """Accept only plain branch/tag names: no '..', no leading '-', no control chars."""
    return bool(ref) and bool(GIT_REF_PATTERN.match(ref)) and '..' not in ref


def valid_subpath(subpath):
    """Reject any subpath that could escape the clone directory."""
    if not subpath:
        return True
    parts = subpath.split('/')
    return all(part not in ('', '.', '..') for part in parts) and not subpath.startswith('-')


def resolve_ref_and_subpath(clone_url, ref_and_path):
    """
    Split the text after /tree/ or /blob/ into (ref, subpath).

    Branch names may contain '/', so the first segment is not always the
    whole ref. One `git ls-remote` call lists the real refs; the longest
    ref that prefixes the path wins. Falls back to the first segment.
    """
    segments = ref_and_path.split('/')
    if len(segments) == 1:
        return segments[0], None

    candidates = ['/'.join(segments[:i]) for i in range(len(segments), 0, -1)]
    try:
        result = subprocess.run(
            ['git', 'ls-remote', '--heads', '--tags', clone_url],
            capture_output=True, text=True, timeout=30, env=GIT_ENV,
            stdin=subprocess.DEVNULL,
        )
        if result.returncode == 0:
            remote_refs = set()
            for line in result.stdout.splitlines():
                parts = line.split('\t')
                if len(parts) == 2:
                    name = parts[1]
                    for prefix in ('refs/heads/', 'refs/tags/'):
                        if name.startswith(prefix):
                            remote_refs.add(name[len(prefix):])
            for candidate in candidates:
                if candidate in remote_refs:
                    rest = ref_and_path[len(candidate):].strip('/')
                    return candidate, (rest or None)
    except (subprocess.TimeoutExpired, FileNotFoundError, OSError):
        pass

    rest = '/'.join(segments[1:]).strip('/')
    return segments[0], (rest or None)


def clone_repository(url, ref=None, subpath=None):
    """
    Clone a GitHub repository to a temporary directory.

    Args:
        url: GitHub repository URL
        ref: Optional branch or tag to clone
        subpath: Optional path within the repo to use

    Returns:
        tuple: (temp_dir, target_path) where target_path is the path to parse
    """
    normalized_url = normalize_github_url(url)

    if ref is not None and not valid_git_ref(ref):
        print(f"ERROR: Invalid git ref in URL: {ref!r}")
        return None, None
    if not valid_subpath(subpath):
        print(f"ERROR: Invalid subpath in URL (must stay inside the repository): {subpath!r}")
        return None, None

    # Create temp directory
    temp_dir = tempfile.mkdtemp(prefix='iac_parser_')

    print(f"Cloning repository: {normalized_url}")
    if ref:
        print(f"  Ref: {ref}")
    print(f"  Temp directory: {temp_dir}")

    try:
        # Shallow, single-branch clone; never wait on a credential prompt.
        cmd = ['git', 'clone', '--depth', '1', '--single-branch']
        if ref:
            cmd += ['--branch', ref]
        cmd += ['--', normalized_url, temp_dir]
        result = subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            timeout=CLONE_TIMEOUT,
            env=GIT_ENV,
            stdin=subprocess.DEVNULL,
        )

        if result.returncode != 0:
            print(f"ERROR: Git clone failed: {result.stderr.strip()}")
            if ref:
                print(f"  Check that the branch or tag '{ref}' exists.")
            print("  Private repositories are not supported (no credential prompt).")
            shutil.rmtree(temp_dir, ignore_errors=True)
            return None, None

        print("  Clone successful!")

        # Determine target path, and confirm it stays inside the clone.
        target_path = temp_dir
        if subpath:
            root = Path(temp_dir).resolve()
            target = (root / subpath).resolve()
            if root != target and root not in target.parents:
                print(f"ERROR: Subpath escapes the repository: {subpath}")
                shutil.rmtree(temp_dir, ignore_errors=True)
                return None, None
            if not target.exists():
                print(f"ERROR: Subpath does not exist in repo: {subpath}")
                shutil.rmtree(temp_dir, ignore_errors=True)
                return None, None
            target_path = str(target)

        return temp_dir, target_path

    except subprocess.TimeoutExpired:
        print(f"ERROR: Git clone timed out ({CLONE_TIMEOUT}s). "
              "Large repositories may exceed the limit; clone it locally and pass the path.")
        shutil.rmtree(temp_dir, ignore_errors=True)
        return None, None
    except FileNotFoundError:
        print("ERROR: Git is not installed or not in PATH")
        shutil.rmtree(temp_dir, ignore_errors=True)
        return None, None
    except Exception as e:
        print(f"ERROR: Failed to clone repository: {str(e)}")
        shutil.rmtree(temp_dir, ignore_errors=True)
        return None, None


def redact_secrets(obj, key=None):
    """
    Return a copy of `obj` with values under secret-looking keys replaced.

    `key` is the name the value sits under (a scalar under a secret-looking
    key is redacted; dicts and lists under it are walked so names and
    references stay visible). Also handles Compose-style "KEY=value" strings
    and Kubernetes / ECS {"name": "DB_PASSWORD", "value": "..."} pairs.
    """
    if isinstance(obj, dict):
        out = {}
        env_name = obj.get('name') if 'value' in obj else None
        for k, v in obj.items():
            if k == 'value' and is_secret_key(env_name) and not isinstance(v, (dict, list)):
                out[k] = REDACTED
            else:
                out[k] = redact_secrets(v, k)
        return out
    if isinstance(obj, list):
        return [redact_secrets(item, key) for item in obj]
    if is_secret_key(key) and obj not in (None, ""):
        return REDACTED
    if isinstance(obj, str) and '=' in obj:
        name, _, value = obj.partition('=')
        if is_secret_key(name) and value:
            return f"{name}={REDACTED}"
    return obj


def cleanup_temp_dir(temp_dir):
    """Clean up temporary directory."""
    if temp_dir and os.path.exists(temp_dir):
        print(f"\nCleaning up temp directory: {temp_dir}")
        shutil.rmtree(temp_dir, ignore_errors=True)


# CloudFormation YAML intrinsic function constructors
def cloudformation_constructor(loader, tag_suffix, node):
    """
    Generic constructor for CloudFormation short-form intrinsic functions.

    Normalises every short tag to its long form so `!GetAtt VPC.VpcId`
    becomes {"Fn::GetAtt": ["VPC", "VpcId"]} and `!Sub "..."` becomes
    {"Fn::Sub": "..."}; the dependency scanner then sees one shape.
    """
    key = 'Ref' if tag_suffix == 'Ref' else f'Fn::{tag_suffix}'

    if isinstance(node, yaml.ScalarNode):
        value = loader.construct_scalar(node)
        if tag_suffix == 'GetAtt' and isinstance(value, str):
            value = value.split('.', 1)
        return {key: value}
    elif isinstance(node, yaml.SequenceNode):
        return {key: loader.construct_sequence(node, deep=True)}
    elif isinstance(node, yaml.MappingNode):
        return {key: loader.construct_mapping(node, deep=True)}
    else:
        return {key: None}


# Register CloudFormation intrinsic functions
yaml.add_multi_constructor('!', cloudformation_constructor, Loader=yaml.SafeLoader)


def parse_terraform(path):
    """
    Parse Terraform files (.tf) to extract resources.

    Uses a tiered approach:
    1. tfparse (most accurate, requires terraform init)
    2. python-hcl2 (good accuracy, no init required)
    3. regex fallback (basic extraction)
    """
    print(f"Parsing Terraform files in: {path}")

    # Check if .terraform directory exists (needed for tfparse)
    terraform_dir = os.path.join(path, ".terraform") if os.path.isdir(path) else None
    has_terraform_init = terraform_dir and os.path.exists(terraform_dir)

    # Try tfparse first (most accurate)
    if TFPARSE_AVAILABLE and has_terraform_init:
        print("  Using tfparse (terraform init detected)")
        result = parse_terraform_with_tfparse(path)
        if "error" not in result:
            return result
        print(f"  tfparse failed: {result.get('error')}, falling back...")
    elif TFPARSE_AVAILABLE and not has_terraform_init:
        print("  tfparse available but no .terraform/ directory (run 'terraform init' for best results)")

    # Try python-hcl2 next
    if HCL2_AVAILABLE:
        print("  Using python-hcl2")
        result = parse_terraform_with_hcl2(path)
        if "error" not in result:
            return result
        print(f"  hcl2 failed: {result.get('error')}, falling back...")

    # Fall back to regex
    print("  Using regex fallback (basic extraction)")
    return parse_terraform_with_regex(path)


def strip_instance_index(path):
    """aws_s3_bucket.each["alpha"] -> aws_s3_bucket.each ; aws_instance.web[0] -> aws_instance.web"""
    return re.sub(r'\[[^\]]*\]', '', path)


def parse_terraform_with_tfparse(path):
    """
    Parse Terraform using tfparse (Cloud Custodian).
    Provides full expression evaluation and accurate dependency tracking.
    Requires 'terraform init' to have been run.

    tfparse output shape (0.6.x): top-level keys are resource types (plus
    `module`, `variable`, `output`, `locals`, `provider`, `terraform`);
    each value is a list of instances carrying `__tfmeta` with `type`
    ("resource" or "data"), `path` (the full address), and `references`.
    """
    try:
        parsed = tfparse_load(path)

        resources = []
        seen = {}
        dependencies = {}
        modules = []
        data_sources = []
        raw_refs = {}

        for block_type, instances in parsed.items():
            if block_type in ('variable', 'output', 'locals', 'terraform', 'provider', 'moved', 'check'):
                continue
            if not isinstance(instances, list):
                continue

            if block_type == 'module':
                for instance in instances:
                    meta = instance.get('__tfmeta', {})
                    modules.append({
                        "name": meta.get('label', 'unknown'),
                        "address": meta.get('path', ''),
                        "source": instance.get('source', ''),
                        "file": meta.get('filename'),
                    })
                continue

            for instance in instances:
                if not isinstance(instance, dict):
                    continue
                meta = instance.get('__tfmeta', {})
                address = strip_instance_index(meta.get('path', ''))
                kind = meta.get('type', 'resource')
                # Address parts: [module.<m>.]<type>.<name>
                parts = address.split('.')
                if kind == 'data' and parts and parts[0] == 'data':
                    parts = parts[1:]
                if len(parts) >= 2:
                    resource_type, resource_name = parts[-2], parts[-1]
                else:
                    resource_type, resource_name = block_type, meta.get('label', 'unknown')
                resource_type = resource_type or block_type
                full_name = f"{resource_type}.{resource_name}"
                module_path = '.'.join(parts[:-2]) if len(parts) > 2 else None

                if kind == 'data':
                    key = f"data.{full_name}"
                    if key not in seen:
                        seen[key] = True
                        data_sources.append({
                            "type": resource_type,
                            "name": resource_name,
                            "full_name": key,
                            "module": module_path,
                            "file": meta.get('filename'),
                        })
                    continue

                provider = resource_type.split("_")[0] if "_" in resource_type else "unknown"
                attributes = {k: v for k, v in instance.items() if not k.startswith('__')}

                if full_name in seen:
                    # count / for_each produce one entry per instance
                    seen[full_name]["instances"] += 1
                else:
                    resource_data = {
                        "type": resource_type,
                        "name": resource_name,
                        "full_name": full_name,
                        "provider": provider,
                        "module": module_path,
                        "file": meta.get('filename'),
                        "instances": 1,
                        "attributes": redact_secrets(attributes),
                    }
                    seen[full_name] = resource_data
                    resources.append(resource_data)

                refs = raw_refs.setdefault(full_name, set())
                for ref in meta.get('references', []) or []:
                    label = ref.get('label')
                    name = ref.get('name')
                    if label and name:
                        refs.add(f"{label}.{strip_instance_index(name)}")

        known = {r["full_name"] for r in resources}
        known_data = {d["full_name"] for d in data_sources}
        for full_name, refs in raw_refs.items():
            deps = []
            for ref in sorted(refs):
                if ref in known and ref != full_name:
                    deps.append(ref)
                elif f"data.{ref}" in known_data:
                    deps.append(f"data.{ref}")
            if deps:
                dependencies[full_name] = deps

        if not resources:
            return {"error": "tfparse found no resources"}

        return {
            "format": "terraform",
            "parser": "tfparse",
            "resources": resources,
            "modules": modules,
            "data_sources": data_sources,
            "total_resources": len(resources),
            "dependencies": dependencies,
            "dependencies_source": "references",
        }

    except Exception as e:
        return {"error": f"tfparse failed: {str(e)}"}


def hcl2_clean(value):
    """
    Normalise python-hcl2 output across versions.

    python-hcl2 >= 8 wraps literal strings (and block labels) in quotes,
    e.g. '"10.0.0.0/16"', and adds `__is_block__` / `__comments__` entries.
    Older versions return bare strings. Strip all of it so the rest of the
    parser sees one shape (and comments cannot create references).
    """
    if isinstance(value, dict):
        return {hcl2_clean(k): hcl2_clean(v) for k, v in value.items()
                if not (isinstance(k, str) and k.startswith('__'))}
    if isinstance(value, list):
        return [hcl2_clean(v) for v in value]
    if isinstance(value, str) and len(value) >= 2 and value[0] == '"' and value[-1] == '"':
        return value[1:-1]
    return value


def hcl2_labeled_blocks(parsed, block_type):
    """
    Yield (label, body) for every block of `block_type`.

    python-hcl2 returns `resource`, `data`, `variable`, `module`, `output`
    as a list of single-key dicts: [{"aws_vpc": {"main": {...}}}, ...]
    (dicts of dicts, not lists). Confirmed on 2.0.3, 4.3.5, 7.3.1, 8.1.4.
    """
    for block in parsed.get(block_type, []) or []:
        if not isinstance(block, dict):
            continue
        for label, body in block.items():
            yield label, body


def parse_terraform_with_hcl2(path):
    """
    Parse Terraform using python-hcl2.
    Good for syntax parsing without terraform init.
    """
    try:
        # Find all .tf files
        if os.path.isfile(path):
            tf_files = [path]
        else:
            tf_files = file_glob.glob(os.path.join(path, "**/*.tf"), recursive=True)

        if not tf_files:
            return {"error": "No Terraform files found", "resources": [], "dependencies": {}}

        resources = []
        data_sources = []
        variables = {}
        modules = []
        locals_block = {}
        outputs = {}
        failed_files = []

        for tf_file in tf_files:
            print(f"    Reading: {tf_file}")
            try:
                with open(tf_file, 'r') as f:
                    parsed = hcl2_clean(hcl2.load(f))

                # Extract resources: {"aws_vpc": {"main": {...attrs...}}}
                for resource_type, instances in hcl2_labeled_blocks(parsed, 'resource'):
                    if not isinstance(instances, dict):
                        continue
                    for resource_name, attrs in instances.items():
                        attrs = attrs if isinstance(attrs, dict) else {}
                        full_name = f"{resource_type}.{resource_name}"
                        provider = resource_type.split("_")[0] if "_" in resource_type else "unknown"
                        resource = {
                            "type": resource_type,
                            "name": resource_name,
                            "full_name": full_name,
                            "provider": provider,
                            "file": tf_file,
                            "attributes": redact_secrets(attrs),
                        }
                        if "count" in attrs:
                            resource["count"] = attrs["count"]
                        if "for_each" in attrs:
                            resource["for_each"] = attrs["for_each"]
                        resources.append(resource)

                # Extract data sources: {"aws_ami": {"al2": {...}}}
                for data_type, instances in hcl2_labeled_blocks(parsed, 'data'):
                    if not isinstance(instances, dict):
                        continue
                    for data_name, attrs in instances.items():
                        data_sources.append({
                            "type": data_type,
                            "name": data_name,
                            "full_name": f"data.{data_type}.{data_name}",
                            "file": tf_file,
                            "attributes": redact_secrets(attrs if isinstance(attrs, dict) else {}),
                        })

                # Extract variables
                for var_name, var_config in hcl2_labeled_blocks(parsed, 'variable'):
                    var_config = var_config if isinstance(var_config, dict) else {}
                    variables[var_name] = {
                        "name": var_name,
                        "file": tf_file,
                        "default": redact_secrets(var_config.get('default'), var_name),
                        "type": var_config.get('type'),
                        "description": var_config.get('description'),
                    }

                # Extract modules
                for module_name, module_config in hcl2_labeled_blocks(parsed, 'module'):
                    module_config = module_config if isinstance(module_config, dict) else {}
                    modules.append({
                        "name": module_name,
                        "source": module_config.get('source', ''),
                        "file": tf_file,
                    })

                # Extract locals
                for locals_block_item in parsed.get('locals', []) or []:
                    if isinstance(locals_block_item, dict):
                        locals_block.update(redact_secrets(locals_block_item))

                # Extract outputs
                for output_name, output_config in hcl2_labeled_blocks(parsed, 'output'):
                    output_config = output_config if isinstance(output_config, dict) else {}
                    outputs[output_name] = {
                        "name": output_name,
                        "value": redact_secrets(output_config.get('value'), output_name),
                        "file": tf_file,
                    }

            except Exception as e:
                print(f"    Warning: Error parsing {tf_file}: {e}")
                failed_files.append(tf_file)
                continue

        if not resources and failed_files:
            # Every file failed (or the ones with resources did): let the
            # regex tier try instead of reporting an empty architecture.
            return {"error": f"hcl2 could not parse {len(failed_files)} file(s) and found no resources"}

        # Extract dependencies from resource attributes
        dependencies = extract_hcl2_dependencies(resources, data_sources)

        return {
            "format": "terraform",
            "parser": "hcl2",
            "resources": resources,
            "data_sources": data_sources,
            "variables": variables,
            "modules": modules,
            "locals": locals_block,
            "outputs": outputs,
            "total_resources": len(resources),
            "dependencies": dependencies,
            "dependencies_source": "references",
            "unparsed_files": failed_files,
        }

    except Exception as e:
        return {"error": f"hcl2 parsing failed: {str(e)}"}


# resource_type.name or data.type.name, optionally followed by attributes
TF_REFERENCE_PATTERN = re.compile(r'\b(data\.)?([a-z][a-z0-9_]*)\.([A-Za-z0-9_-]+)\b')


def find_terraform_references(value, known_resources, known_data):
    """Find references to known resources / data sources anywhere in a value."""
    refs = set()
    if isinstance(value, str):
        for match in TF_REFERENCE_PATTERN.finditer(value):
            is_data, r_type, r_name = match.groups()
            ref = f"{r_type}.{r_name}"
            if is_data:
                if f"data.{ref}" in known_data:
                    refs.add(f"data.{ref}")
            elif ref in known_resources:
                refs.add(ref)
    elif isinstance(value, dict):
        for k, v in value.items():
            refs.update(find_terraform_references(v, known_resources, known_data))
    elif isinstance(value, list):
        for item in value:
            refs.update(find_terraform_references(item, known_resources, known_data))
    return refs


def extract_hcl2_dependencies(resources, data_sources=()):
    """
    Extract dependencies from HCL2 parsed resources by analyzing attribute
    references (implicit) and depends_on (explicit).
    """
    dependencies = {}
    known_resources = {r["full_name"] for r in resources}
    known_data = {d["full_name"] for d in data_sources}

    for resource in resources:
        full_name = resource["full_name"]
        attrs = resource.get("attributes", {})
        refs = find_terraform_references(attrs, known_resources, known_data)
        refs.discard(full_name)
        if refs:
            dependencies[full_name] = sorted(refs)

    return dependencies


def parse_terraform_with_regex(path):
    """
    Parse Terraform using regex (fallback).
    Basic extraction without full HCL understanding.
    """
    # Find all .tf files
    if os.path.isfile(path):
        tf_files = [path]
    else:
        tf_files = file_glob.glob(os.path.join(path, "**/*.tf"), recursive=True)

    if not tf_files:
        return {"error": "No Terraform files found", "resources": [], "dependencies": {}}

    resources = []
    data_sources = []
    variables = {}
    modules = []
    bodies = {}  # full_name -> block body text, for reference scanning

    block_pattern = re.compile(r'^\s*(resource|data)\s+"([^"]+)"\s+"([^"]+)"\s*\{', re.MULTILINE)

    for tf_file in tf_files:
        print(f"    Reading: {tf_file}")
        try:
            with open(tf_file, 'r') as f:
                content = f.read()

            # Extract resource and data blocks with their bodies
            for match in block_pattern.finditer(content):
                block_kind, block_type, block_name = match.groups()
                body = extract_brace_block(content, match.end() - 1)
                provider = block_type.split("_")[0] if "_" in block_type else "unknown"

                if block_kind == "data":
                    full_name = f"data.{block_type}.{block_name}"
                    data_sources.append({
                        "type": block_type,
                        "name": block_name,
                        "full_name": full_name,
                        "file": tf_file,
                    })
                    continue

                full_name = f"{block_type}.{block_name}"
                resource = {
                    "type": block_type,
                    "name": block_name,
                    "full_name": full_name,
                    "file": tf_file,
                    "provider": provider,
                }
                count = re.search(r'^\s*count\s*=\s*(.+?)\s*$', body, re.MULTILINE)
                for_each = re.search(r'^\s*for_each\s*=\s*(.+?)\s*$', body, re.MULTILINE)
                if count:
                    resource["count"] = count.group(1)
                if for_each:
                    resource["for_each"] = for_each.group(1)
                resources.append(resource)
                bodies[full_name] = body

            # Extract variables
            var_pattern = r'variable\s+"([^"]+)"\s+\{'
            for match in re.finditer(var_pattern, content):
                var_name = match.group(1)
                variables[var_name] = {"name": var_name, "file": tf_file}

            # Extract modules
            module_pattern = r'module\s+"([^"]+)"\s+\{'
            for match in re.finditer(module_pattern, content):
                module_name = match.group(1)
                modules.append({"name": module_name, "file": tf_file})

        except Exception as e:
            print(f"    Warning: Error reading {tf_file}: {e}")
            continue

    return {
        "format": "terraform",
        "parser": "regex",
        "resources": resources,
        "data_sources": data_sources,
        "variables": variables,
        "modules": modules,
        "total_resources": len(resources),
        "dependencies": extract_regex_dependencies(resources, data_sources, bodies),
        "dependencies_source": "references",
    }


def extract_brace_block(content, open_index):
    """
    Return the text between the brace at `open_index` and its matching close,
    with `#` and `//` line comments removed so they cannot create references.
    """
    depth = 0
    in_string = False
    i = open_index
    kept = []
    start = open_index + 1
    while i < len(content):
        ch = content[i]
        if in_string:
            if ch == '\\':
                i += 1
            elif ch == '"':
                in_string = False
        elif ch == '"':
            in_string = True
        elif ch == '#' or content.startswith('//', i):
            # drop the line comment
            kept.append(content[start:i])
            newline = content.find('\n', i)
            i = len(content) if newline == -1 else newline
            start = i
            continue
        elif ch == '{':
            depth += 1
        elif ch == '}':
            depth -= 1
            if depth == 0:
                kept.append(content[start:i])
                return ''.join(kept)
        i += 1
    kept.append(content[start:])
    return ''.join(kept)


def extract_regex_dependencies(resources, data_sources, bodies):
    """
    Extract dependencies by scanning each resource body for references to
    known resources and data sources (`aws_subnet.main.id`, `data.aws_ami.al2`)
    and for explicit `depends_on` lists. No type-pair guessing.
    """
    dependencies = {}
    known_resources = {r["full_name"] for r in resources}
    known_data = {d["full_name"] for d in data_sources}

    for resource in resources:
        full_name = resource["full_name"]
        body = bodies.get(full_name, "")
        refs = find_terraform_references(body, known_resources, known_data)
        refs.discard(full_name)
        if refs:
            dependencies[full_name] = sorted(refs)

    return dependencies


CFN_TEMPLATE_EXTENSIONS = ('.yaml', '.yml', '.json', '.template')


def looks_like_cloudformation(file_path):
    """Cheap text check so a directory scan does not pick up every YAML file."""
    try:
        with open(file_path, 'r', errors='ignore') as f:
            head = f.read(65536)
    except OSError:
        return False
    return 'AWSTemplateFormatVersion' in head or re.search(r'^\s*"?Resources"?\s*:', head, re.MULTILINE) is not None


def find_cloudformation_templates(path):
    """Return template files under `path` (or [path] when it is a file)."""
    if os.path.isfile(path):
        return [path]
    candidates = []
    for ext in CFN_TEMPLATE_EXTENSIONS:
        candidates.extend(file_glob.glob(os.path.join(path, f"**/*{ext}"), recursive=True))
    return sorted(c for c in set(candidates) if looks_like_cloudformation(c))


def merge_results(results, list_keys, total_key):
    """Combine per-file parse results into one; each item is tagged with `file`."""
    merged = dict(results[0])
    merged["files"] = [r["file"] for r in results]
    for key in list_keys:
        merged[key] = []
    merged["dependencies"] = {}
    for r in results:
        for key in list_keys:
            for item in r.get(key, []):
                if isinstance(item, dict):
                    item = dict(item, file=r["file"])
                merged[key].append(item)
        for k, v in r.get("dependencies", {}).items():
            if k in merged["dependencies"]:
                print(f"  Warning: '{k}' is defined in more than one file; dependencies merged")
                merged["dependencies"][k] = sorted(set(merged["dependencies"][k]) | set(v))
            else:
                merged["dependencies"][k] = v
    merged[total_key] = len(merged[list_keys[0]])
    return merged


def parse_cloudformation(path):
    """
    Parse CloudFormation template(s) (YAML or JSON).

    Accepts a template file or a directory (every template found is parsed
    and the results are merged). Uses a tiered approach per file:
    1. cfn-lint (most accurate, resolves intrinsic functions)
    2. PyYAML fallback (basic parsing)
    """
    templates = find_cloudformation_templates(path)
    if not templates:
        return {"error": f"No CloudFormation templates found under {path} "
                         "(looked for *.yaml, *.yml, *.json, *.template with a Resources section)"}

    results = []
    for template in templates:
        result = parse_cloudformation_file(template)
        if "error" in result:
            if len(templates) == 1:
                return result
            print(f"  Warning: {result['error']}")
            continue
        result["file"] = template
        results.append(result)

    if not results:
        return {"error": f"None of the {len(templates)} template(s) under {path} could be parsed"}
    if len(results) == 1:
        return results[0]
    return merge_results(results, ["resources"], "total_resources")


def parse_cloudformation_file(path):
    """Parse one CloudFormation template through the tiers."""
    print(f"Parsing CloudFormation template: {path}")

    # Try cfn-lint first (most accurate)
    if CFNLINT_AVAILABLE:
        print("  Using cfn-lint")
        result = parse_cloudformation_with_cfnlint(path)
        if "error" not in result:
            return result
        print(f"  cfn-lint failed: {result.get('error')}, falling back...")

    # Fall back to basic YAML parsing
    print("  Using PyYAML fallback")
    return parse_cloudformation_with_yaml(path)


def plain_data(obj):
    """Convert cfn-lint node subclasses (dict_node, list_node, str_node) to plain types."""
    if isinstance(obj, dict):
        return {str(k): plain_data(v) for k, v in obj.items()}
    if isinstance(obj, list):
        return [plain_data(v) for v in obj]
    if isinstance(obj, str):
        return str(obj)
    return obj


def parse_cloudformation_with_cfnlint(path):
    """
    Parse CloudFormation using cfn-lint.
    Provides intrinsic function resolution and accurate dependency tracking.

    cfn-lint 1.x: `cfnlint.decode.decode(filename)` returns (template, matches);
    template is None when decoding failed and `matches` explains why.
    """
    try:
        template_data, matches = cfnlint_decode(path)

        if template_data is None:
            reasons = "; ".join(str(m) for m in (matches or [])[:3])
            return {"error": f"Failed to decode template: {reasons or 'unknown error'}"}

        template_data = plain_data(template_data)

        resources = []
        cfn_resources = template_data.get('Resources', {})
        parameters = template_data.get('Parameters', {})
        outputs = template_data.get('Outputs', {})
        conditions = template_data.get('Conditions', {})

        for logical_id, resource in cfn_resources.items():
            resource_type = resource.get('Type', 'Unknown')
            properties = resource.get('Properties', {})

            # Extract provider and service from type (AWS::EC2::Instance -> AWS, EC2)
            provider_parts = resource_type.split('::')
            provider = provider_parts[0] if len(provider_parts) > 0 else 'Unknown'
            service = provider_parts[1] if len(provider_parts) > 1 else 'Unknown'
            resource_name = provider_parts[2] if len(provider_parts) > 2 else 'Unknown'

            # Get condition if present
            condition = resource.get('Condition')

            resources.append({
                "logical_id": logical_id,
                "type": resource_type,
                "provider": provider,
                "service": service,
                "resource_name": resource_name,
                "properties": redact_secrets(properties),
                "condition": condition,
                "depends_on": resource.get('DependsOn', []),
                "metadata": redact_secrets(resource.get('Metadata', {})),
            })

        # Extract dependencies using cfn-lint's graph capabilities
        dependencies = extract_cfnlint_dependencies(cfn_resources, parameters)

        return {
            "format": "cloudformation",
            "parser": "cfn-lint",
            "resources": resources,
            "parameters": {k: {
                "type": v.get('Type', 'String'),
                "default": redact_secrets(v.get('Default'), k) if v.get('NoEcho') is not True else REDACTED,
                "description": v.get('Description'),
                "allowed_values": v.get('AllowedValues'),
            } for k, v in parameters.items()},
            "outputs": {k: {
                "value": v.get('Value'),
                "description": v.get('Description'),
                "export": v.get('Export', {}).get('Name'),
            } for k, v in outputs.items()},
            "conditions": list(conditions.keys()),
            "total_resources": len(resources),
            "dependencies": dependencies,
        }

    except Exception as e:
        return {"error": f"cfn-lint parsing failed: {str(e)}"}


def extract_cfnlint_dependencies(resources, parameters):
    """
    Extract dependencies from CloudFormation resources using deep intrinsic function analysis.
    """
    dependencies = {}
    resource_ids = set(resources.keys())
    parameter_ids = set(parameters.keys())

    for logical_id, resource in resources.items():
        deps = set()

        # Explicit dependencies
        depends_on = resource.get('DependsOn', [])
        if isinstance(depends_on, str):
            deps.add(depends_on)
        elif isinstance(depends_on, list):
            deps.update(depends_on)

        # Deep scan for Ref and GetAtt in all properties
        refs = extract_cloudformation_refs_deep(resource, resource_ids, parameter_ids)
        deps.update(refs)

        # Filter to only include resource dependencies (not parameters)
        resource_deps = [d for d in deps if d in resource_ids]

        if resource_deps:
            dependencies[logical_id] = resource_deps

    return dependencies


def extract_cloudformation_refs_deep(obj, resource_ids, parameter_ids, refs=None):
    """
    Recursively extract Ref and GetAtt references from CloudFormation template.
    Handles all intrinsic function formats including short and long forms.
    """
    if refs is None:
        refs = set()

    if isinstance(obj, dict):
        # Handle Ref
        if 'Ref' in obj:
            ref_value = obj['Ref']
            if isinstance(ref_value, str) and ref_value in resource_ids:
                refs.add(ref_value)

        # Handle Fn::GetAtt (long form)
        elif 'Fn::GetAtt' in obj:
            get_att = obj['Fn::GetAtt']
            if isinstance(get_att, list) and len(get_att) > 0:
                if get_att[0] in resource_ids:
                    refs.add(get_att[0])
            elif isinstance(get_att, str):
                # Format: "LogicalId.AttributeName"
                logical_id = get_att.split('.')[0]
                if logical_id in resource_ids:
                    refs.add(logical_id)

        # Handle !GetAtt short form (already parsed as Fn::GetAtt by cfn-lint)

        # Handle Fn::Sub - extract ${Resource} and ${Resource.Attr} references
        elif 'Fn::Sub' in obj:
            sub_value = obj['Fn::Sub']
            if isinstance(sub_value, str):
                # Find ${LogicalId} or ${LogicalId.Attribute} patterns
                for match in re.finditer(r'\$\{([^}!]+?)(?:\.[^}]+)?\}', sub_value):
                    ref = match.group(1)
                    if ref in resource_ids:
                        refs.add(ref)
            elif isinstance(sub_value, list) and len(sub_value) >= 1:
                # [string, {var: value}] format
                if isinstance(sub_value[0], str):
                    for match in re.finditer(r'\$\{([^}!]+?)(?:\.[^}]+)?\}', sub_value[0]):
                        ref = match.group(1)
                        if ref in resource_ids:
                            refs.add(ref)

        # Handle Fn::If - scan condition branches
        elif 'Fn::If' in obj:
            if_value = obj['Fn::If']
            if isinstance(if_value, list):
                for item in if_value[1:]:  # Skip condition name
                    extract_cloudformation_refs_deep(item, resource_ids, parameter_ids, refs)

        # Handle other Fn:: functions that might contain refs
        else:
            for key, value in obj.items():
                if key.startswith('Fn::'):
                    extract_cloudformation_refs_deep(value, resource_ids, parameter_ids, refs)
                elif not key.startswith('!'):
                    extract_cloudformation_refs_deep(value, resource_ids, parameter_ids, refs)

    elif isinstance(obj, list):
        for item in obj:
            extract_cloudformation_refs_deep(item, resource_ids, parameter_ids, refs)

    return refs


def parse_cloudformation_with_yaml(path):
    """
    Parse CloudFormation using PyYAML (fallback).
    Basic parsing without intrinsic function resolution.
    """
    try:
        with open(path, 'r') as f:
            if path.endswith('.json'):
                template = json.load(f)
            else:
                template = yaml.safe_load(f)

        if not isinstance(template, dict) or not isinstance(template.get('Resources'), dict):
            return {"error": f"Not a CloudFormation template (no Resources section): {path}"}

        resources = []
        parameters = template.get('Parameters', {})
        outputs = template.get('Outputs', {})
        cfn_resources = template.get('Resources', {})

        for logical_id, resource in cfn_resources.items():
            resource_type = resource.get('Type', 'Unknown')
            properties = resource.get('Properties', {})

            # Extract provider from type (AWS::EC2::Instance -> AWS)
            provider_parts = resource_type.split('::')
            provider = provider_parts[0] if len(provider_parts) > 0 else 'Unknown'
            service = provider_parts[1] if len(provider_parts) > 1 else 'Unknown'

            resources.append({
                "logical_id": logical_id,
                "type": resource_type,
                "provider": provider,
                "service": service,
                "properties": redact_secrets(properties),
                "depends_on": resource.get('DependsOn', [])
            })

        # Extract dependencies from Ref and GetAtt
        dependencies = {}
        resource_ids = set(cfn_resources.keys())
        parameter_ids = set(parameters.keys()) if parameters else set()

        for logical_id, resource in cfn_resources.items():
            deps = set()

            # Explicit dependencies
            depends_on = resource.get('DependsOn', [])
            if isinstance(depends_on, str):
                deps.add(depends_on)
            elif isinstance(depends_on, list):
                deps.update(depends_on)

            # Implicit dependencies from references
            refs = extract_cloudformation_refs_deep(resource, resource_ids, parameter_ids)
            deps.update(refs)

            if deps:
                dependencies[logical_id] = list(deps)

        return {
            "format": "cloudformation",
            "parser": "yaml",
            "resources": resources,
            "parameters": list(parameters.keys()) if parameters else [],
            "outputs": list(outputs.keys()) if outputs else [],
            "total_resources": len(resources),
            "dependencies": dependencies
        }

    except Exception as e:
        return {"error": f"Failed to parse CloudFormation template: {str(e)}"}


def extract_cloudformation_refs(obj, refs=None):
    """Recursively extract Ref and GetAtt references from CloudFormation template."""
    if refs is None:
        refs = set()

    if isinstance(obj, dict):
        if 'Ref' in obj:
            ref_value = obj['Ref']
            # Filter out pseudo-parameters
            if not ref_value.startswith('AWS::'):
                refs.add(ref_value)
        elif 'Fn::GetAtt' in obj:
            get_att = obj['Fn::GetAtt']
            if isinstance(get_att, list) and len(get_att) > 0:
                refs.add(get_att[0])
            elif isinstance(get_att, str):
                # Format: "LogicalId.AttributeName"
                refs.add(get_att.split('.')[0])
        else:
            for value in obj.values():
                extract_cloudformation_refs(value, refs)
    elif isinstance(obj, list):
        for item in obj:
            extract_cloudformation_refs(item, refs)

    return refs


def parse_kubernetes(path):
    """
    Parse Kubernetes manifests (YAML) with enhanced relationship detection.

    Identifies four relationship types (inspired by KubeDiagrams):
    - REFERENCE: Direct resource references
    - SELECTOR: Label-based selection (Service -> Pod)
    - OWNER: Ownership hierarchies (Deployment -> ReplicaSet -> Pod)
    - COMMUNICATION: Network policies between pods
    """
    print(f"Parsing Kubernetes manifests in: {path}")

    # Find all YAML files
    if os.path.isfile(path):
        yaml_files = [path]
    else:
        yaml_files = file_glob.glob(os.path.join(path, "**/*.yaml"), recursive=True)
        yaml_files.extend(file_glob.glob(os.path.join(path, "**/*.yml"), recursive=True))

    if not yaml_files:
        return {"error": "No Kubernetes manifest files found", "resources": []}

    resources = []
    skipped = 0
    unreadable = []

    for yaml_file in sorted(set(yaml_files)):
        print(f"  Reading: {yaml_file}")
        try:
            with open(yaml_file, 'r') as f:
                # Handle multi-document YAML (--- separator)
                documents = list(yaml.safe_load_all(f))
        except Exception as e:
            print(f"  Warning: Error reading {yaml_file}: {e}")
            unreadable.append(yaml_file)
            continue

        for doc in iter_kubernetes_documents(documents):
            if not is_kubernetes_object(doc):
                skipped += 1
                continue

            kind = doc['kind']
            if kind == 'Kustomization':
                skipped += 1
                continue

            api_version = doc['apiVersion']
            metadata = doc.get('metadata') or {}
            spec = doc.get('spec') or {}

            name = metadata.get('name', 'unnamed')
            namespace = metadata.get('namespace', 'default')
            labels = metadata.get('labels') or {}
            annotations = metadata.get('annotations') or {}
            owner_refs = metadata.get('ownerReferences', [])

            resource = {
                "kind": kind,
                "apiVersion": api_version,
                "name": name,
                "namespace": namespace,
                "labels": labels,
                "annotations": annotations,
                "owner_references": owner_refs,
                "file": yaml_file,
                "spec": redact_secrets(spec),  # Keep full spec for relationship analysis
            }

            # Extract kind-specific fields
            resource.update(extract_kubernetes_kind_fields(kind, spec, metadata, doc))

            resources.append(resource)

    if not resources:
        return {
            "error": f"No Kubernetes objects found under {path} "
                     f"({skipped} YAML document(s) without apiVersion/kind skipped, "
                     f"{len(unreadable)} file(s) unreadable)",
            "resources": [],
        }
    if skipped:
        print(f"  Skipped {skipped} YAML document(s) that are not Kubernetes objects")

    # Extract relationships using enhanced detection
    relationships = extract_kubernetes_relationships_enhanced(resources)

    # Group resources by namespace and kind
    by_namespace = {}
    by_kind = {}
    for r in resources:
        ns = r["namespace"]
        kind = r["kind"]
        if ns not in by_namespace:
            by_namespace[ns] = []
        by_namespace[ns].append(f"{kind}/{r['name']}")
        if kind not in by_kind:
            by_kind[kind] = []
        by_kind[kind].append(r["name"])

    return {
        "format": "kubernetes",
        "parser": "enhanced",
        "resources": resources,
        "total_resources": len(resources),
        "namespaces": list(set(r["namespace"] for r in resources)),
        "by_namespace": by_namespace,
        "by_kind": by_kind,
        "relationships": relationships,
        "dependencies": convert_relationships_to_dependencies(relationships),
        "skipped_documents": skipped,
        "unreadable_files": unreadable,
    }


def is_kubernetes_object(doc):
    """A Kubernetes object has both apiVersion and kind."""
    return isinstance(doc, dict) and bool(doc.get('apiVersion')) and bool(doc.get('kind'))


def iter_kubernetes_documents(documents):
    """Yield documents, expanding `kind: List` into its items."""
    for doc in documents:
        if not isinstance(doc, dict):
            continue
        if doc.get('kind') == 'List' and isinstance(doc.get('items'), list):
            for item in doc['items']:
                if isinstance(item, dict):
                    yield item
            continue
        yield doc


def extract_kubernetes_kind_fields(kind, spec, metadata, doc=None):
    """Extract kind-specific fields for Kubernetes resources.

    `doc` is the full manifest document; some fields (ConfigMap `data`,
    Secret `data`/`type`) live at the document top level, not under spec
    or metadata.
    """
    fields = {}
    if doc is None:
        doc = {}

    if kind == 'Service':
        fields["selector"] = spec.get('selector', {})
        fields["ports"] = spec.get('ports', [])
        fields["type"] = spec.get('type', 'ClusterIP')
        fields["cluster_ip"] = spec.get('clusterIP')

    elif kind in ('Deployment', 'StatefulSet', 'DaemonSet', 'ReplicaSet'):
        fields["replicas"] = spec.get('replicas', 1)
        fields["selector"] = spec.get('selector', {})
        # Extract pod template labels
        template = spec.get('template', {})
        template_metadata = template.get('metadata', {})
        fields["pod_labels"] = template_metadata.get('labels', {})
        # Extract container info
        pod_spec = template.get('spec', {})
        containers = pod_spec.get('containers', [])
        fields["containers"] = [{
            "name": c.get('name'),
            "image": c.get('image'),
            "ports": c.get('ports', []),
        } for c in containers]
        # Extract volume claims
        fields["volume_claims"] = spec.get('volumeClaimTemplates', [])

    elif kind == 'Ingress':
        fields["rules"] = spec.get('rules', [])
        fields["tls"] = spec.get('tls', [])
        fields["ingress_class"] = spec.get('ingressClassName')

    elif kind == 'ConfigMap':
        # `data` and `binaryData` are top-level keys on the document.
        data_keys = list(doc.get('data', {}).keys())
        data_keys += list(doc.get('binaryData', {}).keys())
        fields["data_keys"] = data_keys

    elif kind == 'Secret':
        # `type`, `data`, and `stringData` are top-level keys on the document.
        fields["type"] = doc.get('type', 'Opaque')
        data_keys = list(doc.get('data', {}).keys())
        data_keys += list(doc.get('stringData', {}).keys())
        fields["data_keys"] = data_keys

    elif kind == 'PersistentVolumeClaim':
        fields["storage_class"] = spec.get('storageClassName')
        fields["access_modes"] = spec.get('accessModes', [])
        resources_spec = spec.get('resources', {})
        requests = resources_spec.get('requests', {})
        fields["storage"] = requests.get('storage')

    elif kind == 'PersistentVolume':
        fields["storage_class"] = spec.get('storageClassName')
        fields["capacity"] = spec.get('capacity', {}).get('storage')
        fields["access_modes"] = spec.get('accessModes', [])

    elif kind == 'NetworkPolicy':
        fields["pod_selector"] = spec.get('podSelector', {})
        fields["ingress_rules"] = spec.get('ingress', [])
        fields["egress_rules"] = spec.get('egress', [])
        fields["policy_types"] = spec.get('policyTypes', [])

    elif kind == 'ServiceAccount':
        fields["secrets"] = spec.get('secrets', []) if spec else []

    elif kind == 'Role' or kind == 'ClusterRole':
        fields["rules"] = spec.get('rules', []) if spec else []

    elif kind == 'RoleBinding' or kind == 'ClusterRoleBinding':
        fields["role_ref"] = spec.get('roleRef', {}) if spec else {}
        fields["subjects"] = spec.get('subjects', []) if spec else []

    elif kind == 'Job':
        fields["completions"] = spec.get('completions', 1)
        fields["parallelism"] = spec.get('parallelism', 1)
        template = spec.get('template', {})
        pod_spec = template.get('spec', {})
        containers = pod_spec.get('containers', [])
        fields["containers"] = [{"name": c.get('name'), "image": c.get('image')} for c in containers]

    elif kind == 'CronJob':
        fields["schedule"] = spec.get('schedule')
        job_template = spec.get('jobTemplate', {})
        job_spec = job_template.get('spec', {})
        template = job_spec.get('template', {})
        pod_spec = template.get('spec', {})
        containers = pod_spec.get('containers', [])
        fields["containers"] = [{"name": c.get('name'), "image": c.get('image')} for c in containers]

    return fields


def extract_kubernetes_relationships_enhanced(resources):
    """
    Extract relationships between Kubernetes resources using enhanced detection.

    Relationship types:
    - SELECTOR: Label-based selection (Service -> Pods)
    - OWNER: Ownership hierarchy (Deployment -> ReplicaSet -> Pod)
    - REFERENCE: Direct resource references (Ingress -> Service)
    - COMMUNICATION: Network policies
    - MOUNT: Volume/ConfigMap/Secret mounts
    """
    relationships = []

    # Build indexes for efficient lookup
    by_kind_namespace = {}  # {(kind, namespace): [resources]}
    by_labels = {}  # {namespace: {label_key: {label_value: [resources]}}}

    for resource in resources:
        key = (resource["kind"], resource["namespace"])
        if key not in by_kind_namespace:
            by_kind_namespace[key] = []
        by_kind_namespace[key].append(resource)

        # Index by labels
        ns = resource["namespace"]
        if ns not in by_labels:
            by_labels[ns] = {}
        for label_key, label_value in resource.get("labels", {}).items():
            if label_key not in by_labels[ns]:
                by_labels[ns][label_key] = {}
            if label_value not in by_labels[ns][label_key]:
                by_labels[ns][label_key][label_value] = []
            by_labels[ns][label_key][label_value].append(resource)

    for resource in resources:
        kind = resource["kind"]
        name = resource["name"]
        namespace = resource["namespace"]
        spec = resource.get("spec", {})

        # === SELECTOR relationships ===

        # Service -> Pods (via selector)
        if kind == "Service":
            selector = resource.get("selector", {})
            if selector:
                matching_pods = find_resources_by_selector(
                    selector, namespace, ["Pod", "Deployment", "StatefulSet", "DaemonSet"],
                    by_kind_namespace, resources
                )
                for target in matching_pods:
                    relationships.append({
                        "from": f"Service/{name}",
                        "to": f"{target['kind']}/{target['name']}",
                        "type": "SELECTOR",
                        "namespace": namespace,
                        "selector": selector,
                    })

        # === OWNER relationships ===

        # Deployment/StatefulSet/DaemonSet -> ReplicaSet/Pods (implicit)
        if kind in ("Deployment", "StatefulSet", "DaemonSet"):
            relationships.append({
                "from": f"{kind}/{name}",
                "to": f"Pod/{name}-*",
                "type": "OWNER",
                "namespace": namespace,
                "description": f"{kind} manages Pod replicas",
            })

        # CronJob -> Job
        if kind == "CronJob":
            relationships.append({
                "from": f"CronJob/{name}",
                "to": f"Job/{name}-*",
                "type": "OWNER",
                "namespace": namespace,
            })

        # === REFERENCE relationships ===

        # Ingress -> Service
        if kind == "Ingress":
            rules = resource.get("rules", [])
            for rule in rules:
                host = rule.get("host", "*")
                http = rule.get("http", {})
                for path_config in http.get("paths", []):
                    backend = path_config.get("backend", {})
                    service_name = None
                    service_port = None

                    # Handle different API versions
                    if "serviceName" in backend:  # networking.k8s.io/v1beta1
                        service_name = backend["serviceName"]
                        service_port = backend.get("servicePort")
                    elif "service" in backend:  # networking.k8s.io/v1
                        service_name = backend["service"].get("name")
                        port_info = backend["service"].get("port", {})
                        service_port = port_info.get("number") or port_info.get("name")

                    if service_name:
                        relationships.append({
                            "from": f"Ingress/{name}",
                            "to": f"Service/{service_name}",
                            "type": "REFERENCE",
                            "namespace": namespace,
                            "host": host,
                            "path": path_config.get("path", "/"),
                            "port": service_port,
                        })

        # RoleBinding/ClusterRoleBinding -> Role/ClusterRole
        if kind in ("RoleBinding", "ClusterRoleBinding"):
            role_ref = resource.get("role_ref", {})
            if role_ref:
                role_kind = role_ref.get("kind", "Role")
                role_name = role_ref.get("name")
                if role_name:
                    relationships.append({
                        "from": f"{kind}/{name}",
                        "to": f"{role_kind}/{role_name}",
                        "type": "REFERENCE",
                        "namespace": namespace if kind == "RoleBinding" else "cluster",
                    })

            # Also link to subjects
            subjects = resource.get("subjects", [])
            for subject in subjects:
                subj_kind = subject.get("kind")
                subj_name = subject.get("name")
                subj_ns = subject.get("namespace", namespace)
                if subj_kind and subj_name:
                    relationships.append({
                        "from": f"{kind}/{name}",
                        "to": f"{subj_kind}/{subj_name}",
                        "type": "REFERENCE",
                        "namespace": subj_ns,
                        "description": "grants permissions to",
                    })

        # === MOUNT relationships (ConfigMap, Secret, PVC references) ===

        # Extract volume mounts from workload resources
        if kind in ("Deployment", "StatefulSet", "DaemonSet", "ReplicaSet", "Pod", "Job", "CronJob"):
            pod_spec = kubernetes_pod_spec(kind, spec)
            volumes = pod_spec.get("volumes") or []

            # ConfigMap/Secret consumed through env and envFrom; ServiceAccount
            for target, via in kubernetes_env_references(pod_spec):
                relationships.append({
                    "from": f"{kind}/{name}",
                    "to": target,
                    "type": "REFERENCE",
                    "namespace": namespace,
                    "via": via,
                })

            sa_name = pod_spec.get("serviceAccountName") or pod_spec.get("serviceAccount")
            if sa_name and sa_name != "default":
                relationships.append({
                    "from": f"{kind}/{name}",
                    "to": f"ServiceAccount/{sa_name}",
                    "type": "REFERENCE",
                    "namespace": namespace,
                    "via": "serviceAccountName",
                })

            for volume in volumes:
                vol_name = volume.get("name")

                # ConfigMap volume
                if "configMap" in volume:
                    cm_name = volume["configMap"].get("name")
                    if cm_name:
                        relationships.append({
                            "from": f"{kind}/{name}",
                            "to": f"ConfigMap/{cm_name}",
                            "type": "MOUNT",
                            "namespace": namespace,
                            "volume": vol_name,
                        })

                # Secret volume
                if "secret" in volume:
                    secret_name = volume["secret"].get("secretName")
                    if secret_name:
                        relationships.append({
                            "from": f"{kind}/{name}",
                            "to": f"Secret/{secret_name}",
                            "type": "MOUNT",
                            "namespace": namespace,
                            "volume": vol_name,
                        })

                # PVC volume
                if "persistentVolumeClaim" in volume:
                    pvc_name = volume["persistentVolumeClaim"].get("claimName")
                    if pvc_name:
                        relationships.append({
                            "from": f"{kind}/{name}",
                            "to": f"PersistentVolumeClaim/{pvc_name}",
                            "type": "MOUNT",
                            "namespace": namespace,
                            "volume": vol_name,
                        })

        # === COMMUNICATION relationships (NetworkPolicy) ===

        if kind == "NetworkPolicy":
            pod_selector = resource.get("pod_selector", {})
            ingress_rules = resource.get("ingress_rules", [])
            egress_rules = resource.get("egress_rules", [])

            # NetworkPolicy applies to pods matching selector
            relationships.append({
                "from": f"NetworkPolicy/{name}",
                "to": f"Pods matching {pod_selector}",
                "type": "COMMUNICATION",
                "namespace": namespace,
                "description": "applies network rules to",
            })

            # Ingress rules (who can talk to these pods)
            for rule in ingress_rules:
                from_selectors = rule.get("from", [])
                for from_sel in from_selectors:
                    if "podSelector" in from_sel:
                        relationships.append({
                            "from": f"Pods matching {from_sel['podSelector']}",
                            "to": f"Pods matching {pod_selector}",
                            "type": "COMMUNICATION",
                            "namespace": namespace,
                            "direction": "ingress",
                        })

            # Egress rules (who these pods can talk to)
            for rule in egress_rules:
                to_selectors = rule.get("to", [])
                for to_sel in to_selectors:
                    if "podSelector" in to_sel:
                        relationships.append({
                            "from": f"Pods matching {pod_selector}",
                            "to": f"Pods matching {to_sel['podSelector']}",
                            "type": "COMMUNICATION",
                            "namespace": namespace,
                            "direction": "egress",
                        })

    return relationships


def kubernetes_pod_spec(kind, spec):
    """Return the pod spec for a workload kind (Pod, template kinds, CronJob)."""
    if kind == "Pod":
        return spec or {}
    if kind == "CronJob":
        job_spec = (spec.get("jobTemplate") or {}).get("spec") or {}
        return (job_spec.get("template") or {}).get("spec") or {}
    return (spec.get("template") or {}).get("spec") or {}


def kubernetes_env_references(pod_spec):
    """
    Yield (target, via) for ConfigMaps and Secrets consumed through
    containers[].envFrom, containers[].env[].valueFrom, and initContainers.
    """
    seen = set()
    containers = (pod_spec.get("containers") or []) + (pod_spec.get("initContainers") or [])
    for container in containers:
        if not isinstance(container, dict):
            continue
        for source in container.get("envFrom") or []:
            if not isinstance(source, dict):
                continue
            cm = (source.get("configMapRef") or {}).get("name")
            secret = (source.get("secretRef") or {}).get("name")
            if cm:
                seen.add((f"ConfigMap/{cm}", "envFrom"))
            if secret:
                seen.add((f"Secret/{secret}", "envFrom"))
        for env in container.get("env") or []:
            if not isinstance(env, dict):
                continue
            value_from = env.get("valueFrom") or {}
            cm = (value_from.get("configMapKeyRef") or {}).get("name")
            secret = (value_from.get("secretKeyRef") or {}).get("name")
            if cm:
                seen.add((f"ConfigMap/{cm}", "configMapKeyRef"))
            if secret:
                seen.add((f"Secret/{secret}", "secretKeyRef"))
    return sorted(seen)


def find_resources_by_selector(selector, namespace, target_kinds, by_kind_namespace, all_resources):
    """Find resources that match a label selector."""
    matching = []

    for target_kind in target_kinds:
        key = (target_kind, namespace)
        candidates = by_kind_namespace.get(key, [])

        for candidate in candidates:
            # Get the labels to match against
            if target_kind in ("Deployment", "StatefulSet", "DaemonSet"):
                # Match against pod template labels
                labels_to_check = candidate.get("pod_labels", {})
            else:
                labels_to_check = candidate.get("labels", {})

            # Check if all selector labels match
            if selector and labels_to_check:
                match = all(
                    labels_to_check.get(k) == v
                    for k, v in selector.items()
                )
                if match:
                    matching.append(candidate)

    return matching


def convert_relationships_to_dependencies(relationships):
    """Convert relationships list to a dependencies dict for diagram generation."""
    dependencies = {}

    for rel in relationships:
        from_resource = rel["from"]
        to_resource = rel["to"]

        if from_resource not in dependencies:
            dependencies[from_resource] = []

        # Only add concrete resource references (not wildcards), once each
        if "*" not in to_resource and "matching" not in to_resource \
                and to_resource not in dependencies[from_resource]:
            dependencies[from_resource].append(to_resource)

    return dependencies


COMPOSE_FILE_NAMES = ('compose.yaml', 'compose.yml', 'docker-compose.yaml', 'docker-compose.yml')


def find_compose_files(path):
    """Return Compose files under `path` (or [path] when it is a file)."""
    if os.path.isfile(path):
        return [path]
    found = []
    for name in COMPOSE_FILE_NAMES:
        found.extend(file_glob.glob(os.path.join(path, f"**/{name}"), recursive=True))
    return sorted(set(found))


def parse_docker_compose(path):
    """Parse Docker Compose file(s) (YAML). Accepts a file or a directory."""
    compose_files = find_compose_files(path)
    if not compose_files:
        return {"error": f"No Compose files found under {path} "
                         f"(looked for {', '.join(COMPOSE_FILE_NAMES)})"}

    results = []
    for compose_file in compose_files:
        result = parse_docker_compose_file(compose_file)
        if "error" in result:
            if len(compose_files) == 1:
                return result
            print(f"  Warning: {result['error']}")
            continue
        result["file"] = compose_file
        results.append(result)

    if not results:
        return {"error": f"None of the {len(compose_files)} Compose file(s) under {path} could be parsed"}
    if len(results) == 1:
        return results[0]
    merged = merge_results(results, ["services"], "total_services")
    merged["networks"] = sorted({n for r in results for n in r["networks"]})
    merged["volumes"] = sorted({v for r in results for v in r["volumes"]})
    return merged


def parse_docker_compose_file(path):
    """Parse one Docker Compose file (YAML)."""
    print(f"Parsing Docker Compose file: {path}")

    try:
        with open(path, 'r') as f:
            compose = yaml.safe_load(f)

        if not isinstance(compose, dict) or not isinstance(compose.get('services'), dict):
            return {"error": f"Not a Compose file (no services section): {path}"}

        services = compose.get('services', {})
        networks = compose.get('networks') or {}
        volumes = compose.get('volumes') or {}

        service_list = []
        dependencies = {}

        for service_name, service_config in services.items():
            service_config = service_config or {}
            depends_on = service_config.get('depends_on', [])

            # depends_on can be a list or a dict
            if isinstance(depends_on, dict):
                depends_on = list(depends_on.keys())

            service_networks = service_config.get('networks', [])
            if isinstance(service_networks, dict):
                service_networks = list(service_networks.keys())

            service_volumes = service_config.get('volumes', [])

            service_list.append({
                "name": service_name,
                "image": service_config.get('image'),
                "build": service_config.get('build'),
                "ports": service_config.get('ports', []),
                "environment": redact_secrets(service_config.get('environment', {})),
                "networks": service_networks,
                "volumes": service_volumes
            })

            dependencies[service_name] = {
                "depends_on": depends_on,
                "networks": service_networks
            }

        return {
            "format": "docker-compose",
            "parser": "yaml",
            "services": service_list,
            "networks": list(networks.keys()),
            "volumes": list(volumes.keys()),
            "total_services": len(service_list),
            "dependencies": dependencies
        }

    except Exception as e:
        return {"error": f"Failed to parse Docker Compose file: {str(e)}"}


def extract_github_subpath(url):
    """
    Split a GitHub URL into (base_url, ref, subpath).

    Examples:
        https://github.com/user/repo/tree/main/terraform -> ('https://github.com/user/repo', 'main', 'terraform')
        https://github.com/user/repo/blob/v1.2/infra/main.tf -> (..., 'v1.2', 'infra/main.tf')
        https://github.com/user/repo -> ('https://github.com/user/repo', None, None)

    Query strings and fragments are dropped. Branch names that contain '/'
    are resolved against the remote in clone_repository.
    """
    url = url.split('#', 1)[0].split('?', 1)[0].rstrip('/')
    match = re.match(r'^(https?://github\.com/' + GITHUB_OWNER_REPO + r')(?:/(?:tree|blob)/(.+))?$', url)
    if not match:
        return url, None, None
    base_url = match.group(1)
    ref_and_path = match.group(2)
    if not ref_and_path:
        return base_url, None, None
    ref, subpath = resolve_ref_and_subpath(normalize_github_url(base_url), ref_and_path)
    return base_url, ref, subpath


def resource_count(result):
    """Number of top-level items the diagram would be built from."""
    return len(result.get("resources") or result.get("services") or [])


def main(args=None):
    """Main entry point for the IaC parser."""
    if args is None:
        args = ARGS if 'ARGS' in globals() else parse_args()

    if not args.format or not args.path:
        parse_args(["--help"])
        sys.exit(1)

    iac_format = args.format.lower()
    path = args.path

    if iac_format not in ("terraform", "cloudformation", "kubernetes", "docker-compose"):
        print(f"ERROR: Unsupported format: {iac_format}")
        print("Supported formats: terraform, cloudformation, kubernetes, docker-compose")
        sys.exit(1)

    temp_dir = None  # Track temp directory for cleanup

    # Check if path is a GitHub URL
    if is_github_url(path):
        if path.startswith('git@'):
            base_url, ref, subpath = path, None, None
        else:
            base_url, ref, subpath = extract_github_subpath(
                path if path.startswith('http') else 'https://' + path)

        # Clone the repository
        temp_dir, path = clone_repository(base_url, ref, subpath)
        if not path:
            sys.exit(1)
    else:
        # Validate local path
        if not os.path.exists(path):
            print(f"ERROR: Path does not exist: {path}")
            sys.exit(1)

    try:
        # Parse based on format
        if iac_format == "terraform":
            result = parse_terraform(path)
        elif iac_format == "cloudformation":
            result = parse_cloudformation(path)
        elif iac_format == "kubernetes":
            result = parse_kubernetes(path)
        else:
            result = parse_docker_compose(path)

        if "error" not in result and resource_count(result) == 0:
            result["warning"] = "Parsed successfully but found zero resources"
            print(f"\nWARNING: {result['warning']}. Check the format and path before diagramming.")

        # Output JSON result
        print("\n" + "="*60)
        print("PARSE RESULT:")
        print("="*60)
        print(json.dumps(result, indent=2, default=str))

        # Check for errors
        if "error" in result:
            sys.exit(1)

    finally:
        # Always clean up temp directory
        if temp_dir:
            cleanup_temp_dir(temp_dir)


if __name__ == "__main__":
    main()
