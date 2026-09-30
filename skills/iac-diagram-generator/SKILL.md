---
name: iac-diagram-generator
description: Analyzes Infrastructure as Code files (Terraform, CloudFormation, Kubernetes, Docker Compose) and generates visual architecture diagrams. Use when analyzing infrastructure code, designing cloud architectures, or when the user requests architecture diagrams from IaC.
allowed-tools: Read Glob Grep Bash(python3 ${CLAUDE_SKILL_DIR}/scripts/parse_iac.py *) Bash(python3 ${CLAUDE_SKILL_DIR}/scripts/generate_diagram.py *)
---

# IaC Architecture Diagram Generator

Analyzes Infrastructure as Code and generates professional architecture diagrams
using Nano Banana Pro (Gemini 3 Pro Image). Parses IaC into a resource/dependency
graph, then renders a polished diagram from a structured prompt.

**Parsing is supported for:** Terraform, CloudFormation, Kubernetes, Docker Compose.

Both scripts manage their own Python environment: on first run they create a
virtual environment under the plugin data directory, install
`requirements.txt` into it, and re-run themselves there. Nothing is installed
into the system Python. Always pass `--data-dir "${CLAUDE_PLUGIN_DATA}"` so the
environment survives plugin updates.

## Workflow

### Step 1: Discover IaC files

Use Glob to find IaC files in the target directory:

- **Terraform**: `*.tf` (JSON-syntax `*.tf.json` is not supported)
- **CloudFormation**: `*.yaml`, `*.yml`, `*.json`, `*.template`
- **Kubernetes**: `*.yaml`, `*.yml` (often under `manifests/`, `k8s/`, `kube/`)
- **Docker Compose**: `compose.yaml`, `compose.yml`, `docker-compose.yaml`, `docker-compose.yml`

If no file is mentioned, search the current directory recursively. Pick the format
that matches what you find before parsing.

### Step 2: Parse the files

Run the parser. It accepts **local paths or GitHub URLs** and prints a JSON
resource graph. The format argument is one of `terraform`, `cloudformation`,
`kubernetes`, `docker-compose`. Every format accepts a file or a directory.

```bash
python3 ${CLAUDE_SKILL_DIR}/scripts/parse_iac.py --data-dir "${CLAUDE_PLUGIN_DATA}" <format> <path-or-github-url>
```

Examples:

```bash
# Local
python3 ${CLAUDE_SKILL_DIR}/scripts/parse_iac.py --data-dir "${CLAUDE_PLUGIN_DATA}" terraform ./infrastructure
python3 ${CLAUDE_SKILL_DIR}/scripts/parse_iac.py --data-dir "${CLAUDE_PLUGIN_DATA}" cloudformation template.yaml
python3 ${CLAUDE_SKILL_DIR}/scripts/parse_iac.py --data-dir "${CLAUDE_PLUGIN_DATA}" kubernetes k8s/
python3 ${CLAUDE_SKILL_DIR}/scripts/parse_iac.py --data-dir "${CLAUDE_PLUGIN_DATA}" docker-compose compose.yaml

# GitHub (shallow clone of the named branch into a temp dir, cleaned up afterwards)
python3 ${CLAUDE_SKILL_DIR}/scripts/parse_iac.py --data-dir "${CLAUDE_PLUGIN_DATA}" terraform https://github.com/user/repo
python3 ${CLAUDE_SKILL_DIR}/scripts/parse_iac.py --data-dir "${CLAUDE_PLUGIN_DATA}" terraform https://github.com/user/repo/tree/feature-x/infrastructure
python3 ${CLAUDE_SKILL_DIR}/scripts/parse_iac.py --data-dir "${CLAUDE_PLUGIN_DATA}" cloudformation https://github.com/user/repo/blob/main/stack.yaml
```

Supported GitHub URL forms: `https://github.com/user/repo`,
`.../tree/<branch-or-tag>/<path>`, `.../blob/<branch-or-tag>/<file>`,
`github.com/user/repo`, `git@github.com:user/repo`. Branch names that contain
`/` are resolved against the remote. Public repositories only; the clone never
prompts for credentials and times out after 120 s.

The JSON contains resources, dependencies/relationships, hierarchical structure
(VPCs, subnets, namespaces), and connection types. Secret-looking values
(`password`, `secret`, `token`, `*_key`, `credential`, ...) are replaced with
`[REDACTED]`; never try to recover them.

If the output contains a `"warning"` key ("found zero resources"), stop and
tell the user; do not diagram an empty architecture. A non-zero exit means a
parse error: surface the message instead of guessing.

### Step 3: Analyze the resource graph

From the JSON, understand:

- **Hierarchy**: VPC > Availability Zones > Subnets > Resources (or cluster > namespaces > workloads)
- **Resource types**: compute, networking, storage, database, security, analytics
- **Dependencies**: explicit (`depends_on`, `DependsOn`) and implicit (references).
  Terraform output carries `dependencies_source: "references"`; `count` /
  `for_each` / `instances` mark multi-instance resources.
- **Connections**: how resources communicate (HTTP, DB, queues)
- **Security boundaries**: VPCs, subnets, security groups, network policies

### Step 4: Build the Nano Banana Pro prompt

Write a detailed, structured, natural-language prompt describing the architecture.
**Follow the visual design system** so output is consistent and professional:

- Read `${CLAUDE_SKILL_DIR}/references/visual-style.md` for the full template
  (canvas, frame, header, zones, icon style, color palette, connection styling,
  labels, layout, and the DO/DON'T list).
- Read `${CLAUDE_SKILL_DIR}/references/example-prompts.md` for two complete
  worked examples (three-tier web app, Kubernetes microservices), a
  fill-in-the-blanks template, and per-provider header colors.

Key rules: 16:9 landscape, framed border with margins, gradient header with title +
subtitle, isometric 3D icons (never flat official icons), tinted zone backgrounds,
curved bezier arrows with pill-shaped protocol/port labels, left-to-right flow.
If there are more than ~15 resources, split into multiple focused diagrams.

### Step 5: Generate the diagram

Pass the prompt through stdin with a quoted heredoc so `$`, backticks and quotes
in the prompt are never expanded by the shell:

```bash
python3 ${CLAUDE_SKILL_DIR}/scripts/generate_diagram.py --data-dir "${CLAUDE_PLUGIN_DATA}" --prompt-file - <<'PROMPT'
ENHANCED_PROMPT_HERE
PROMPT
```

Defaults: `gemini-3-pro-image`, `--aspect-ratio 16:9`, `--resolution 2K` (same
price as 1K on the Pro model). Options:

| Flag | Use |
|------|-----|
| `--fast` | Nano Banana 2 (`gemini-3.1-flash-image`), cheaper and faster; good for a draft or when the user asks for speed |
| `--resolution 1K\|2K\|4K` | 4K for very dense diagrams; 1K for quick previews |
| `--aspect-ratio 16:9\|4:3\|1:1\|...` | Only when the user asks for a different shape |
| `--output-dir DIR` | Save somewhere other than the current directory |
| `--prompt-file PATH` | Read the prompt from a file instead of stdin |

This requires the `GEMINI_API_KEY` environment variable (see Setup); the key is
checked before anything is installed. It saves a timestamped PNG
(`iac_diagram_*.png`) in the current directory. A blocked or text-only response
exits non-zero and prints the finish reason.

### Step 6: Report back

Give the user: the diagram filename/location, a summary of the architecture
components, notable patterns or best practices observed, and any improvement
suggestions.

For editable vector output (SVG/PDF), see `${CLAUDE_SKILL_DIR}/references/vectorization.md`.

## Supported IaC formats

### Terraform (`.tf`)

Tiered parser, auto-selected best-first. Every tier reads resources, data
sources, modules, `depends_on` and attribute references:

| Parser | Accuracy | Requirements | What it does |
|--------|----------|--------------|--------------|
| **tfparse** | Best | `terraform init` run, Python 3.10+ | Full expression evaluation; references from `__tfmeta` |
| **python-hcl2** | Good | none | Proper HCL2 parsing, reference extraction |
| **regex** | Basic | none | Block matching, reference scanning inside each body |

For best results: `cd` into the Terraform dir and run `terraform init` first so
`tfparse` can resolve modules and expressions. A tier that finds zero resources
falls through to the next one.

### AWS CloudFormation (`.yaml`, `.yml`, `.json`, `.template`)

Tiered parser: **cfn-lint** (best — resolves intrinsic functions) → **PyYAML**
(basic). Both extract dependencies from `!Ref`/`Ref`, `!GetAtt`/`Fn::GetAtt`,
`!Sub`/`Fn::Sub`, `DependsOn`, and `Fn::If` branches. A directory is scanned
for templates (files with a `Resources` section) and the results are merged.

### Kubernetes (`.yaml`, `.yml` — manifests, Helm output)

Relationship detection (inspired by
[KubeDiagrams](https://github.com/philippemerle/KubeDiagrams)):

| Relationship | Example | Detection |
|--------------|---------|-----------|
| **SELECTOR** | Service → Deployment | Label-selector matching |
| **OWNER** | Deployment → Pod | Ownership hierarchy |
| **REFERENCE** | Ingress → Service, Deployment → Secret/ConfigMap/ServiceAccount | Backend references, `envFrom`, `secretKeyRef`, `configMapKeyRef`, `serviceAccountName` |
| **MOUNT** | Deployment → ConfigMap/Secret/PVC | Volume mounts |
| **COMMUNICATION** | NetworkPolicy | Ingress/egress selectors |

Handles 20+ kinds across Workloads, Networking, Config, Storage, and RBAC.
Documents without `apiVersion` and `kind` (Helm `values.yaml`, Kustomization
files, unrelated YAML) are skipped; `kind: List` is expanded.

### Docker Compose (`compose.yaml`, `docker-compose.yaml`, …)

Extracts services, networks, volumes, and dependencies (`depends_on`, network
membership, volume sharing). A directory is scanned for Compose files.

## Not yet supported

The parser does **not** currently handle these formats — do not claim diagrams
for them without first parsing the resources by hand:

- **Pulumi** (`.ts`/`.py`/`.go`) — requires language-specific AST analysis
- **Azure ARM / Bicep**
- **GCP Deployment Manager**
- **Terraform JSON syntax** (`*.tf.json`) and `.tfvars` values

If a user asks for one of these, say it isn't supported yet and offer to read the
files directly and build the graph manually, or to diagram a supported format.

## Common architecture patterns

- **Three-tier web app**: public subnet (LBs, NAT) → private subnet (app servers,
  workers) → database subnet (RDS, ElastiCache, no direct internet).
- **Microservices**: API gateway / ingress at the edge, service mesh for
  inter-service calls, namespaces/VPCs per domain, shared data stores and queues.
- **Serverless**: API Gateway → Lambda → DynamoDB/S3, EventBridge/SQS for async,
  CloudFront for delivery.

## Setup

**Required:** Python 3.10+ with the `venv` module, and `git` for GitHub URLs.
Dependencies (`pyyaml`, `google-genai`) are installed automatically on first run
into `${CLAUDE_PLUGIN_DATA}/venv` (fallback: `~/.cache/claude-iac-diagram-generator/venv`).

**Optional (better parsing):** install the upgrade tiers into the same environment:

```bash
python3 ${CLAUDE_SKILL_DIR}/scripts/parse_iac.py --data-dir "${CLAUDE_PLUGIN_DATA}" --install-optional
```

That installs `python-hcl2` (better Terraform parsing, no init needed),
`tfparse` (best Terraform parsing, needs `terraform init`) and `cfn-lint`
(CloudFormation intrinsic-function resolution). Offer this when a user wants
more accurate Terraform or CloudFormation edges.

**Diagram generation:** set a Gemini API key:

```bash
export GEMINI_API_KEY="your-api-key-here"   # https://aistudio.google.com/apikey
```

Nano Banana Pro renders at roughly $0.134/image at 1K or 2K (a few seconds each)
and embeds a SynthID watermark marking the image as AI-generated.

## Error handling

The parser reports clear messages for: missing/invalid files, unsupported formats,
IaC syntax errors, failed GitHub clones (bad branch, private repo, path outside
the repo, 120 s timeout), and read permission issues. It warns when a parse
succeeds with zero resources. The generator reports a missing `GEMINI_API_KEY`
before installing anything, plus auth/quota/rate errors, unavailable models,
blocked responses, and network failures. On a parse error the script exits
non-zero — surface the message to the user rather than fabricating a diagram.
