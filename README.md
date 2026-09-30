# IaC Diagram Generator

A Claude Code plugin that generates professional cloud architecture diagrams from
Infrastructure as Code. Parses Terraform, CloudFormation, Kubernetes, and Docker
Compose into a resource graph, then renders a polished diagram with Nano Banana Pro
(Gemini 3 Pro Image).

## Install (recommended — plugin marketplace)

In Claude Code:

```
/plugin marketplace add 0-to-1-Labs/claude-marketplace
/plugin install iac-diagram-generator@0-to-1-labs
```

Then set a Gemini API key (used for diagram rendering):

```bash
export GEMINI_API_KEY='your-key-here'   # https://aistudio.google.com/apikey
```

That's it — diagram generation is bundled, no separate image skill required.
Python dependencies are installed on first run into a private virtual
environment under the plugin's data directory; nothing touches your system Python.

## Keep the plugin updated

Claude Code can update this plugin automatically. Auto-update is off by default for third-party marketplaces, so turn it on once:

1. Run `/plugin`.
2. Open the **Marketplaces** tab and select `0-to-1-labs`.
3. Choose **Enable auto-update**.

Claude Code then checks for new versions after each session start and installs them. Restart Claude Code to load an update.

To update by hand:

```
claude plugin marketplace update 0-to-1-labs
claude plugin update iac-diagram-generator@0-to-1-labs
```

## Requirements

- Claude Code CLI
- Python 3.10+ with the `venv` module (`pyyaml` and `google-genai` are installed automatically on first run)
- `git`, for GitHub URLs
- A Gemini API key

Nano Banana Pro renders at roughly **$0.134/image** (1K or 2K) and embeds a **SynthID**
watermark marking output as AI-generated. `--fast` switches to Nano Banana 2 for
cheaper drafts.

## Usage

Just ask Claude Code:

```
"Generate an architecture diagram from my Terraform code"
"Show me what this CloudFormation template deploys"
"Diagram our Kubernetes application in the k8s/ directory"
"Visualize my compose.yaml services"
"Diagram the infrastructure in https://github.com/user/repo/tree/staging/infra"
```

Claude will detect and parse the IaC, extract resources/relationships/topology,
build an optimized prompt, and save a `iac_diagram_*.png` in your current directory.
Diagrams default to 16:9 at 2K resolution.

## Supported formats (parsing)

| Format | Extensions | Notes |
|--------|-----------|-------|
| **Terraform** | `.tf` | Tiered: tfparse → python-hcl2 → regex; references, `depends_on`, data sources, `count`/`for_each` |
| **CloudFormation** | `.yaml`, `.yml`, `.json`, `.template` | Tiered: cfn-lint → PyYAML; resolves `!Ref`/`!GetAtt`/`!Sub` short and long forms; directories are scanned |
| **Kubernetes** | `.yaml`, `.yml` | 20+ kinds; selector/owner/reference/mount/network relationships incl. `envFrom`, `secretKeyRef`, `serviceAccountName` |
| **Docker Compose** | `compose.yaml`, `docker-compose.yaml`, … | Services, networks, volumes, `depends_on`; directories are scanned |

Values under secret-looking keys (`password`, `secret`, `token`, `*_key`,
`credential`, ...) are redacted before the JSON reaches the model.

**Not yet supported:** Pulumi, Azure ARM/Bicep, GCP Deployment Manager, Terraform
JSON syntax (`*.tf.json`). (The skill will say so and offer to read the files
manually instead of guessing.)

### Optional parser upgrades

Install the upgrade tiers into the plugin's own environment (never the system Python):

```bash
python3 <plugin>/skills/iac-diagram-generator/scripts/parse_iac.py --install-optional
```

That installs `python-hcl2` (better Terraform parsing, no `terraform init`
needed), `tfparse` (best Terraform parsing, needs `terraform init`) and
`cfn-lint` (CloudFormation intrinsic-function resolution). The tiers are tested
against python-hcl2 8.1.4, tfparse 0.6.22 and cfn-lint 1.57.1; see `tests/`.

## How it works

1. **Parse** — `scripts/parse_iac.py` scans your files (or a GitHub URL, including `/tree/<branch>/<path>`) into a JSON resource/dependency graph
2. **Analyze** — extracts hierarchy (VPC > subnets > resources / cluster > namespaces), dependencies, connection types, security boundaries
3. **Prompt** — Claude builds a structured Nano Banana Pro prompt following a consistent visual design system
4. **Render** — `scripts/generate_diagram.py` produces a PNG (`--fast`, `--resolution`, `--aspect-ratio`, `--output-dir`)

## Manual install (legacy, no plugin)

```bash
git clone https://github.com/0-to-1-Labs/iac-diagram-generator.git
cd iac-diagram-generator
./install.sh                      # add --optional-parsers for the upgrade tiers
```

This copies the skill into `~/.claude/skills/iac-diagram-generator/`. Dependencies
go to `~/.cache/claude-iac-diagram-generator/venv` on first run.

## Tests

```bash
python3 -m venv .venv && .venv/bin/pip install -r requirements.txt -r skills/iac-diagram-generator/requirements-optional.txt
.venv/bin/python tests/test_parse_iac.py -v
```

Tiers whose library is missing are reported as skipped. No Gemini API call is made.

## Editable vector output

Nano Banana Pro outputs PNG. To get editable SVG/PDF, run the PNG through a
vectorizer — see `skills/iac-diagram-generator/references/vectorization.md`.

## Limitations

- The Terraform regex fallback won't capture complex HCL (nested modules, dynamic blocks); install the optional parsers for accuracy
- Very large infrastructures (100+ resources) may produce cluttered diagrams — split into focused views
- Diagram accuracy depends on IaC completeness and AI interpretation

## Contributing

Focus areas: Pulumi / Azure ARM / GCP Deployment Manager parsers, richer
Terraform expression handling, and layout optimizations.

## License

MIT.

## Credits

Built for Claude Code. Uses Nano Banana Pro (Gemini 3 Pro Image) for rendering.
