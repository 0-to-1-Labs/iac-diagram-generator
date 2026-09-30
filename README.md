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

## Requirements

- Claude Code CLI
- Python 3.7+ (PyYAML is required; `google-genai` is auto-installed on first render)
- A Gemini API key

Nano Banana Pro renders at roughly **$0.134/image** and embeds a **SynthID**
watermark marking output as AI-generated.

## Usage

Just ask Claude Code:

```
"Generate an architecture diagram from my Terraform code"
"Show me what this CloudFormation template deploys"
"Diagram our Kubernetes application in the k8s/ directory"
"Visualize my compose.yaml services"
"Diagram the infrastructure in https://github.com/user/repo"
```

Claude will detect and parse the IaC, extract resources/relationships/topology,
build an optimized prompt, and save a `iac_diagram_*.png` in your current directory.

## Supported formats (parsing)

| Format | Extensions | Notes |
|--------|-----------|-------|
| **Terraform** | `.tf`, `.tfvars` | Tiered: tfparse → python-hcl2 → regex |
| **CloudFormation** | `.yaml`, `.yml`, `.json`, `.template` | Tiered: cfn-lint → PyYAML; resolves `Ref`/`GetAtt`/`Sub` |
| **Kubernetes** | `.yaml`, `.yml` | 20+ kinds; selector/owner/reference/mount/network relationships |
| **Docker Compose** | `compose.yaml`, `docker-compose.yaml`, … | Services, networks, volumes, `depends_on` |

**Not yet supported:** Pulumi, Azure ARM/Bicep, GCP Deployment Manager. (The skill
will say so and offer to read the files manually instead of guessing.)

### Optional parser upgrades

```bash
pip install python-hcl2   # better Terraform parsing (no terraform init needed)
pip install tfparse       # best Terraform parsing (needs terraform init, Python 3.10+)
pip install cfn-lint      # accurate CloudFormation intrinsic-function parsing
```

## How it works

1. **Parse** — `scripts/parse_iac.py` scans your files (or a GitHub URL) into a JSON resource/dependency graph
2. **Analyze** — extracts hierarchy (VPC > subnets > resources / cluster > namespaces), dependencies, connection types, security boundaries
3. **Prompt** — Claude builds a structured Nano Banana Pro prompt following a consistent visual design system
4. **Render** — `scripts/generate_diagram.py` produces a PNG

## Manual install (legacy, no plugin)

```bash
git clone https://github.com/johnpsasser/iac-diagram-generator.git
cd iac-diagram-generator
./install.sh
```

This copies the skill into `~/.claude/skills/iac-diagram-generator/`.

## Editable vector output

Nano Banana Pro outputs PNG. To get editable SVG/PDF, run the PNG through a
vectorizer — see `skills/iac-diagram-generator/references/vectorization.md`.

## Limitations

- Terraform regex fallback won't capture complex HCL (nested modules, dynamic blocks); install `tfparse`/`python-hcl2` for accuracy
- Very large infrastructures (100+ resources) may produce cluttered diagrams — split into focused views
- Diagram accuracy depends on IaC completeness and AI interpretation

## Contributing

Focus areas: Pulumi / Azure ARM / GCP Deployment Manager parsers, richer
Terraform expression handling, and layout optimizations.

## License

MIT.

## Credits

Built for Claude Code. Uses Nano Banana Pro (Gemini 3 Pro Image) for rendering.
