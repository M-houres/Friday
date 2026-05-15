# Friday

> Build vertical AI applications on a reusable workflow, tool, artifact, and operations backbone.

Friday is an open-source framework for building **complex but bounded AI products**: legal assistants, report generators, internal copilots, approval-driven workflows, multi-page AI workbenches, and similar business applications.

It is **not** a zero-code platform and it is **not** trying to be an unconstrained general-purpose super agent. The design center is practical product delivery:

- reusable runtime, auth, billing, workflow, artifact, and sandbox infrastructure
- deterministic `Skill` workflows first
- agent orchestration as a fallback, not the default for every task
- configurable project pages and operations console out of the box

## Why Friday

Most AI app teams do not want to rebuild the same foundation for every new product:

- login and account bootstrap
- model routing and streaming output
- workflow execution and status tracking
- artifact generation and controlled downloads
- approval steps and async jobs
- admin and ops configuration surfaces

Friday turns those into a shared base layer, so a new application usually means building:

- a new `Skill`
- a new frontend page
- a new manifest/config entry

## What It Is Good At

Friday is a strong fit for:

- multi-step AI workflows with clear business boundaries
- AI products that generate deliverables such as reports, markdown, JSON, PPT, or files
- internal workbenches with multiple pages, task history, and operations tooling
- human-in-the-loop products with approvals, retries, and async execution
- vertical domain apps such as legal, content, knowledge, analysis, or operations assistants

Friday is a weaker fit for:

- highly autonomous open-ended agents running for hours or days
- large-scale multi-agent team simulation
- ERP-grade transactional core systems
- real-time collaborative systems where AI is only one small subsystem

## Architecture Direction

Friday follows a simple principle:

**Workflow First, Agent Optional**

- If a task matches a known business `Skill`, run the deterministic `skill_pipeline`
- If a task does not match or needs broader decomposition, fall back to agent DAG orchestration

This keeps production behavior more stable than pushing every request into a free-form agent loop.

## Core Capabilities

- Skill-first execution model
- Agent DAG fallback orchestration
- FastAPI API and static page mounting
- per-workflow SSE stream channel
- sandboxed execution with workflow ownership
- managed artifact generation and download
- project and skill manifest auto-discovery
- login, account bootstrap, rate limiting, and auth modes
- async jobs, approvals, and operations endpoints
- model routing, retries, and circuit breakers

## Project Layout

```text
app.py                    # app entrypoint and auto-discovery bootstrap
src/                      # core runtime, APIs, orchestration, tools, models
skills/                   # vertical business skills
static/                   # frontend pages
config/skills/            # skill manifests
config/projects/          # project/page manifests
scripts/new_app.py        # scaffold a new vertical app
```

## Quick Start

### 1. Install dependencies

```bash
pip install -r requirements.txt
```

### 2. Start infrastructure

```bash
docker compose up -d
```

### 3. Configure environment

```bash
copy config/.env.example config/.env
```

Fill in the model and runtime settings you actually want to use.

### 4. Run the app

```bash
python app.py
```

Default entrypoints:

- panel: `http://localhost:8000/panel`
- API docs: `http://localhost:8000/docs`
- business pages: auto-mounted from project and skill manifests

## Create a New AI App

Generate a scaffold:

```bash
python scripts/new_app.py contract_review ^
  --name "合同审查助手" ^
  --trigger "合同|法务|审查|协议" ^
  --description "面向合同审查场景的垂直应用骨架" ^
  --project-id legal_suite ^
  --artifact-kind markdown ^
  --create-project-config
```

This generates:

```text
skills/contract_review_skill.py
static/contract-review.html
config/skills/contract_review.json
config/projects/legal_suite.json
```

In practice, most new apps only require:

1. implementing the business logic in `skills/*.py`
2. building the scenario UI in `static/*.html`
3. wiring project and skill metadata in `config/projects` and `config/skills`

## How Friday Extends

### Skill

A `Skill` is the vertical business entrypoint. It usually defines:

- trigger matching
- workflow steps
- tool execution
- result shaping for the frontend

### Skill Manifest

`config/skills/<app>.json` declares route, page, execution mode, visibility, and artifact type.

### Project Manifest

`config/projects/*.json` describes product-level pages, grouping, navigation, and page scenarios.

### Frontend Page

`static/*.html` pages are mounted automatically. A page may map to one skill, many skills, or a scenario pipeline.

## Development Model

Recommended delivery sequence for a new vertical AI product:

1. scaffold the app
2. make the Skill workflow run end-to-end
3. connect artifacts, external APIs, or knowledge sources
4. polish page UX and streaming feedback
5. add approval, billing, and ops behaviors if needed

## Current Status

The project is already usable as a **first production-capable AI application base**, especially for vertical workflow-driven products.

Recent hardening includes:

- persistent artifact metadata
- sandbox registry and recovery
- Redis-backed SSE cross-process bridging with local fallback
- bootstrap-only DB schema initialization
- full regression suite passing

## Docs

- [API.md](./API.md)
- [INTEGRATION.md](./INTEGRATION.md)
- [ARCHITECTURE.md](./ARCHITECTURE.md)
- [docs/architecture-v2.md](./docs/architecture-v2.md)
- [docs/implementation-plan-v2.md](./docs/implementation-plan-v2.md)

## License

MIT
