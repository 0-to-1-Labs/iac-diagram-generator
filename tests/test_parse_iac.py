"""
Parser regression tests: every format, every tier that is installed.

Run with the interpreter that has the tiers you want to exercise:

    python3 -m unittest discover -s tests -v                 # regex / PyYAML tiers only
    <venv>/bin/python -m unittest discover -s tests -v       # plus python-hcl2, tfparse, cfn-lint

Tiers whose library is missing are reported as skipped, never as passed.
The managed venv bootstrap is disabled (IAC_DIAGRAM_NO_VENV=1) so the tests
run in the interpreter that started them.
"""

import importlib
import io
import json
import os
import shutil
import subprocess
import sys
import tempfile
import unittest
from contextlib import redirect_stdout
from pathlib import Path

os.environ["IAC_DIAGRAM_NO_VENV"] = "1"

TESTS_DIR = Path(__file__).resolve().parent
FIXTURES = TESTS_DIR / "fixtures"
SCRIPTS = TESTS_DIR.parent / "skills" / "iac-diagram-generator" / "scripts"
sys.path.insert(0, str(SCRIPTS))

import parse_iac  # noqa: E402

HCL2 = parse_iac.HCL2_AVAILABLE
TFPARSE = parse_iac.TFPARSE_AVAILABLE
CFNLINT = parse_iac.CFNLINT_AVAILABLE


def version_of(package):
    try:
        from importlib.metadata import version
        return version(package)
    except Exception:
        return "?"


def quiet(func, *args):
    """Run a parser function and swallow its progress output."""
    with redirect_stdout(io.StringIO()):
        return func(*args)


def deps_of(result, key):
    return set(result["dependencies"].get(key, []))


class TerraformExpectations:
    """Shared assertions: every Terraform tier must agree on these facts."""

    EXPECTED = {
        "aws_vpc.main", "aws_subnet.private", "aws_security_group.DbSG",
        "aws_instance.web", "aws_db_instance.Main-DB",
    }

    def check(self, result, parser_name):
        self.assertNotIn("error", result, result.get("error"))
        self.assertEqual(result["parser"], parser_name)
        names = {r["full_name"] for r in result["resources"]}
        self.assertEqual(names, self.EXPECTED)
        self.assertEqual(result["total_resources"], 5)
        self.assertEqual(result["dependencies_source"], "references")
        # Real references only: web -> subnet + data source; never vpc via type-pair guessing
        web = deps_of(result, "aws_instance.web")
        self.assertIn("aws_subnet.private", web)
        self.assertNotIn("aws_vpc.main", web)
        self.assertEqual(deps_of(result, "aws_subnet.private"), {"aws_vpc.main"})
        self.assertEqual(deps_of(result, "aws_security_group.DbSG"), {"aws_vpc.main"})
        self.assertEqual(deps_of(result, "aws_db_instance.Main-DB"), {"aws_security_group.DbSG"})
        data_names = {d["full_name"] for d in result.get("data_sources", [])}
        self.assertIn("data.aws_ami.al2", data_names)
        # Secrets never reach the output
        text = json.dumps(result)
        self.assertNotIn("literal-secret", text)
        self.assertNotIn("hunter2-default", text)


class TestTerraformRegex(unittest.TestCase, TerraformExpectations):
    def test_regex_tier(self):
        result = quiet(parse_iac.parse_terraform_with_regex, str(FIXTURES / "terraform"))
        self.check(result, "regex")
        web = deps_of(result, "aws_instance.web")
        self.assertIn("data.aws_ami.al2", web)
        by_name = {r["full_name"]: r for r in result["resources"]}
        self.assertEqual(by_name["aws_subnet.private"]["count"], "2")
        self.assertIn("for_each", by_name["aws_db_instance.Main-DB"])


@unittest.skipUnless(HCL2, "python-hcl2 not installed")
class TestTerraformHcl2(unittest.TestCase, TerraformExpectations):
    def test_hcl2_tier(self):
        result = quiet(parse_iac.parse_terraform_with_hcl2, str(FIXTURES / "terraform"))
        self.check(result, "hcl2")
        self.assertIn("data.aws_ami.al2", deps_of(result, "aws_instance.web"))
        by_name = {r["full_name"]: r for r in result["resources"]}
        # python-hcl2 >= 8 quotes literals; they must come back clean
        self.assertEqual(by_name["aws_vpc.main"]["attributes"]["cidr_block"], "10.0.0.0/16")
        self.assertNotIn("__is_block__", by_name["aws_vpc.main"]["attributes"])
        self.assertEqual(by_name["aws_db_instance.Main-DB"]["attributes"]["password"], parse_iac.REDACTED)

    def test_hcl2_is_selected_and_not_empty(self):
        result = quiet(parse_iac.parse_terraform, str(FIXTURES / "terraform"))
        self.assertEqual(result["parser"], "hcl2")
        self.assertEqual(result["total_resources"], 5)

    def test_hcl2_unparseable_file_falls_back(self):
        with tempfile.TemporaryDirectory() as tmp:
            Path(tmp, "broken.tf").write_text('resource "aws_vpc" "x" {\n  cidr_block = \n')
            result = quiet(parse_iac.parse_terraform_with_hcl2, tmp)
            self.assertIn("error", result)


@unittest.skipUnless(TFPARSE, "tfparse not installed")
class TestTerraformTfparse(unittest.TestCase, TerraformExpectations):
    def test_tfparse_tier(self):
        with tempfile.TemporaryDirectory() as tmp:
            shutil.copytree(FIXTURES / "terraform", tmp, dirs_exist_ok=True)
            os.makedirs(os.path.join(tmp, ".terraform"))
            result = quiet(parse_iac.parse_terraform, tmp)
        self.check(result, "tfparse")
        self.assertIn("data.aws_ami.al2", deps_of(result, "aws_instance.web"))
        by_name = {r["full_name"]: r for r in result["resources"]}
        self.assertEqual(by_name["aws_subnet.private"]["instances"], 2)


class CloudFormationExpectations:
    def check(self, result, parser_name):
        self.assertNotIn("error", result, result.get("error"))
        self.assertEqual(result["parser"], parser_name)
        ids = {r["logical_id"] for r in result["resources"]}
        self.assertEqual(ids, {"VPC", "Subnet", "SG", "Inst", "DB"})
        self.assertEqual(deps_of(result, "Subnet"), {"VPC"})
        self.assertEqual(deps_of(result, "SG"), {"VPC"})              # !GetAtt + !Sub short tags
        self.assertEqual(deps_of(result, "Inst"), {"Subnet", "SG"})   # !GetAtt [list] + nested Fn::Sub
        self.assertEqual(deps_of(result, "DB"), {"Subnet", "SG"})     # DependsOn + !GetAtt
        text = json.dumps(result)
        self.assertNotIn("literal-db-secret", text)
        self.assertNotIn("hunter2-param", text)


class TestCloudFormationYaml(unittest.TestCase, CloudFormationExpectations):
    def test_yaml_tier_short_tags(self):
        result = quiet(parse_iac.parse_cloudformation_with_yaml, str(FIXTURES / "cloudformation" / "short-tags.yaml"))
        self.check(result, "yaml")

    def test_yaml_tier_json(self):
        result = quiet(parse_iac.parse_cloudformation_with_yaml, str(FIXTURES / "cloudformation" / "short-tags.json"))
        self.assertEqual(deps_of(result, "Policy"), {"Bucket"})

    def test_directory_input_merges_templates(self):
        result = quiet(parse_iac.parse_cloudformation, str(FIXTURES / "cloudformation"))
        self.assertNotIn("error", result)
        self.assertEqual(result["total_resources"], 7)
        self.assertEqual(len(result["files"]), 2)


@unittest.skipUnless(CFNLINT, "cfn-lint not installed")
class TestCloudFormationCfnLint(unittest.TestCase, CloudFormationExpectations):
    def test_cfnlint_tier_yaml(self):
        result = quiet(parse_iac.parse_cloudformation_with_cfnlint, str(FIXTURES / "cloudformation" / "short-tags.yaml"))
        self.check(result, "cfn-lint")
        json.dumps(result)  # node subclasses must be plain types

    def test_cfnlint_tier_json(self):
        result = quiet(parse_iac.parse_cloudformation_with_cfnlint, str(FIXTURES / "cloudformation" / "short-tags.json"))
        self.assertNotIn("error", result, result.get("error"))
        self.assertEqual(deps_of(result, "Policy"), {"Bucket"})

    def test_cfnlint_is_selected(self):
        result = quiet(parse_iac.parse_cloudformation, str(FIXTURES / "cloudformation" / "short-tags.yaml"))
        self.assertEqual(result["parser"], "cfn-lint")


class TestKubernetes(unittest.TestCase):
    def test_objects_relationships_and_skips(self):
        result = quiet(parse_iac.parse_kubernetes, str(FIXTURES / "kubernetes"))
        self.assertNotIn("error", result)
        kinds = {(r["kind"], r["name"]) for r in result["resources"]}
        self.assertEqual(kinds, {("Deployment", "api"), ("Service", "api"),
                                 ("Secret", "api-secret"), ("ServiceAccount", "api-sa")})
        self.assertEqual(result["skipped_documents"], 3)  # values doc, Kustomization, CFN template
        deps = deps_of(result, "Deployment/api")
        for target in ("ConfigMap/api-config", "Secret/api-secret", "Secret/migrate-secret",
                       "Secret/db-cred", "ServiceAccount/api-sa"):
            self.assertIn(target, deps)
        self.assertIn("Deployment/api", deps_of(result, "Service/api"))
        self.assertNotIn("hunter2", json.dumps(result))

    def test_no_objects_is_an_error(self):
        result = quiet(parse_iac.parse_kubernetes, str(FIXTURES / "kubernetes" / "not-k8s.yaml"))
        self.assertIn("error", result)


class TestDockerCompose(unittest.TestCase):
    def test_file_and_directory(self):
        for target in (FIXTURES / "compose" / "docker-compose.yaml", FIXTURES / "compose"):
            result = quiet(parse_iac.parse_docker_compose, str(target))
            self.assertNotIn("error", result)
            self.assertEqual({s["name"] for s in result["services"]}, {"web", "db"})
            self.assertEqual(result["dependencies"]["web"]["depends_on"], ["db"])
            text = json.dumps(result)
            self.assertNotIn("sk-prod", text)
            self.assertNotIn("hunter2-prod", text)
            self.assertIn("APP_MODE=production", text)


class TestGitHubUrls(unittest.TestCase):
    def setUp(self):
        # Never hit the network: pretend the remote has these refs.
        self._orig = parse_iac.resolve_ref_and_subpath

        def fake(clone_url, ref_and_path):
            for ref in ("feature/foo", "main"):
                if ref_and_path == ref or ref_and_path.startswith(ref + "/"):
                    rest = ref_and_path[len(ref):].strip("/")
                    return ref, (rest or None)
            first, _, rest = ref_and_path.partition("/")
            return first, (rest or None)
        parse_iac.resolve_ref_and_subpath = fake

    def tearDown(self):
        parse_iac.resolve_ref_and_subpath = self._orig

    def test_branch_and_subpath(self):
        cases = {
            "https://github.com/u/r": ("https://github.com/u/r", None, None),
            "https://github.com/u/r/tree/main/infra": ("https://github.com/u/r", "main", "infra"),
            "https://github.com/u/r/tree/feature/foo/infra": ("https://github.com/u/r", "feature/foo", "infra"),
            "https://github.com/u/r/blob/main/infra/main.tf": ("https://github.com/u/r", "main", "infra/main.tf"),
            "https://github.com/u/r/tree/main/infra?ref=x#frag": ("https://github.com/u/r", "main", "infra"),
        }
        for url, expected in cases.items():
            self.assertEqual(parse_iac.extract_github_subpath(url), expected, url)

    def test_traversal_and_bad_refs_are_rejected(self):
        self.assertFalse(parse_iac.valid_subpath("../../../../etc"))
        self.assertFalse(parse_iac.valid_subpath("infra/../../x"))
        self.assertTrue(parse_iac.valid_subpath("infra/prod"))
        self.assertFalse(parse_iac.valid_git_ref("-x"))
        self.assertFalse(parse_iac.valid_git_ref("a..b"))
        self.assertTrue(parse_iac.valid_git_ref("release/1.2"))
        with redirect_stdout(io.StringIO()):
            self.assertEqual(parse_iac.clone_repository("https://github.com/u/r", "main", "../../etc"), (None, None))
            self.assertEqual(parse_iac.clone_repository("https://github.com/u/r", "--upload-pack=x", None), (None, None))


class TestCli(unittest.TestCase):
    def run_cli(self, *args):
        env = dict(os.environ, IAC_DIAGRAM_NO_VENV="1")
        return subprocess.run([sys.executable, str(SCRIPTS / "parse_iac.py"), *args],
                              capture_output=True, text=True, env=env)

    def test_zero_resources_warns(self):
        with tempfile.TemporaryDirectory() as tmp:
            Path(tmp, "empty.tf").write_text('variable "x" {}\n')
            proc = self.run_cli("terraform", tmp)
        self.assertEqual(proc.returncode, 0)
        self.assertIn("WARNING", proc.stdout)
        self.assertIn('"warning"', proc.stdout)

    def test_missing_path_fails(self):
        proc = self.run_cli("terraform", "/nonexistent/path")
        self.assertEqual(proc.returncode, 1)

    def test_generator_checks_key_before_anything(self):
        env = dict(os.environ, IAC_DIAGRAM_NO_VENV="1")
        env.pop("GEMINI_API_KEY", None)
        proc = subprocess.run([sys.executable, str(SCRIPTS / "generate_diagram.py"), "x"],
                              capture_output=True, text=True, env=env)
        self.assertEqual(proc.returncode, 1)
        self.assertIn("GEMINI_API_KEY", proc.stdout)


class TestGeneratorHelpers(unittest.TestCase):
    """Offline checks of generate_diagram.py; no API call is made."""

    def setUp(self):
        self.gen = importlib.import_module("generate_diagram")

    def test_defaults_and_flags(self):
        args = self.gen.parse_args(["hello", "world"])
        self.assertEqual(args.aspect_ratio, "16:9")
        self.assertEqual(args.resolution, "2K")
        self.assertEqual(self.gen.read_prompt(args), "hello world")
        self.assertEqual(self.gen.DEFAULT_MODEL, "gemini-3-pro-image")
        self.assertEqual(self.gen.FAST_MODEL, "gemini-3.1-flash-image")
        fast = self.gen.parse_args(["--fast", "--resolution", "4K", "x"])
        self.assertTrue(fast.fast)
        self.assertEqual(fast.resolution, "4K")

    def test_prompt_file_keeps_shell_characters(self):
        with tempfile.NamedTemporaryFile("w", suffix=".txt", delete=False) as f:
            f.write("Title 'X' costs $0.134 and uses `backticks` ${env}-db")
            name = f.name
        try:
            args = self.gen.parse_args(["--prompt-file", name])
            self.assertIn("${env}-db", self.gen.read_prompt(args))
        finally:
            os.unlink(name)

    def test_empty_response_is_explained(self):
        class Feedback:
            block_reason = "SAFETY"

        class Candidate:
            finish_reason = "SAFETY"

        class Response:
            parts = None
            prompt_feedback = Feedback()
            candidates = [Candidate()]

        out = io.StringIO()
        with redirect_stdout(out):
            self.gen.describe_empty_response(Response())
        self.assertIn("SAFETY", out.getvalue())
        # The guard used in generate_image: never iterate a None `parts`
        self.assertEqual(list(getattr(Response(), "parts", None) or []), [])


def tier_matrix():
    """Print which tiers this interpreter exercised and with which versions."""
    rows = [
        ("python", ".".join(map(str, sys.version_info[:3])), True),
        ("pyyaml", version_of("pyyaml"), True),
        ("python-hcl2", version_of("python-hcl2"), HCL2),
        ("tfparse", version_of("tfparse"), TFPARSE),
        ("cfn-lint", version_of("cfn-lint"), CFNLINT),
    ]
    print("\nTier matrix for", sys.executable)
    for name, ver, present in rows:
        print(f"  {name:12} {ver:10} {'tested' if present else 'SKIPPED (not installed)'}")


if __name__ == "__main__":
    tier_matrix()
    unittest.main()
