# Capability registry instructions

## Project scope and source of truth

Read `docs\brief.md` before implementing changes. This repository is a small
hypothesis testbed for a governed capability registry, not a platform. At the
time of the initial instructions the brief was the only project file. The
contract/static-selection/shared-adapter experiments now exist; trust-tier
provisioning and model evaluation remain gated. Read `docs\hypothesis-report.md`
and `docs\contract.md` before extending them.

The existing factory and enterprise front-door implementations referenced by
the brief are external inputs. `baselines\provenance.json` identifies the
allowlisted frozen source copies used here, including work-in-progress bytes.
Do not edit those copies or the original sibling checkouts to implement an
integration; use the adapter specializations and disposable runtime fixtures.
Original sources can evolve independently; do not silently refresh the capture.

## Current commands

Use Python 3.13 and PowerShell from the repository root:

```powershell
.\.venv\Scripts\python.exe -m pip install -c requirements.lock -e ".[copilot]" --group dev
.\.venv\Scripts\python.exe -m tools.capture_sources --verify
.\.venv\Scripts\python.exe -m tools.build_catalog
.\.venv\Scripts\python.exe -m tools.validate_catalog --offline
.\.venv\Scripts\python.exe -m pytest -q
.\.venv\Scripts\python.exe -m pytest -q tests\test_static_selection.py -k provider_parity
.\.venv\Scripts\python.exe -m tools.verify_selection
.\.venv\Scripts\python.exe -m ruff check .
.\.venv\Scripts\python.exe -m ruff format --check .
.\.venv\Scripts\python.exe -m tools.verify_baselines
```

MAF application integration uses a separate `.venv-maf` environment with
`requirements-maf.lock` and the `maf` optional dependency group. Run
`.\.venv-maf\Scripts\python.exe -B -m tools.verify_frontdoor`.
Never combine the two runtime dependency sets or install into source checkouts.
See README for environment creation and explicit live inspection commands.

`catalog\testbed-approval.json` records the user's explicit fixture review:
all ten skills, hardware/handoff front-door fixtures, and three synthetic tools.
These are testbed-only approvals, not production approvals. Do not invent
owners; absent owners remain unassigned readiness issues. Other entries stay
draft. A successful structural or historical-pin check is not full readiness.
The revised H4 gate requires supported H1 and H2, recorded live SDK/core and
MAF/tool calls, and only draft/nonfixture or unassigned-owner readiness causes.
Report-three adds common kind/lifecycle and clears those source-fit collisions.
H4 is complete. Read the latest report and explicit safety stops before mutations.
The report-four decision explicitly supersedes the earlier role-breadth stop.
Foundry User breadth is an accepted testbed limitation, separate from project
boundary enforcement. `tools.h4_resume` records this new authorization epoch
under `.artifacts\h4\resumed.json`, preserving the old raw evidence.
Try one isolated inventory-derived custom role; publish unpromoted general v3
without deploy_release, then create the privileged deploy-only composition and
release certificate identity. The coder's existing grants must not be widened.
Stop if coder receives anything other than 403 on privileged, if old pins/default
change, or if bridge breaks. Exact owned H4 resources are in
`.artifacts\h4\ownership.json`; teardown is written and must not be run.
This revised experiment is complete: the isolated custom role failed,
Foundry User was retained, coder general-v3 200 versus privileged initialize/
direct-call 403 passed and was repeated after the interrupted session resumed
overnight. Release/MAF approval and unchanged pins/native bridge controls passed.
H4 supports only project-boundary enforcement. Preserve the recorded evidence;
do not rerun publication/recovery commands that rewrite bindings or H3 summaries.

The latest decision authorizes leased governance on ACA, then H5/model deployment
only after H6/H7 are reported and `h5_entry_gate` opens. Separate consumer/gateway/
mock apps and identities, Key Vault signing and atomic Table replay protection
are live. H6/H7 passed; H8 has a qualified secured-control measurement. Existing
grants, shared networking, immutable Toolbox versions/default/member pins and
the .NET package must stay unchanged. General v2 exposure is accepted, not a
failed test: the mock independently requires gateway identity plus exact lease.
The owned mutable RemoteTool targets now point to ACA; the dev tunnel is deleted.
Never restart the unauthenticated route. Use `tools.governance_live` H6/H7/H8/
runtime phases and `tools.verify_governance_bridge`, not historical H3 writers.
Dependencies are `.[maf,governance]` with `requirements-maf.lock` in `.venv-maf`.
`scripts\teardown-testbed.ps1` covers governance plus H4; do not execute it.
H5 is now measured and partial: nano improves front-door top-1 but regresses
factory exact-set selection. Static remains the consumer mode default; explicit
model mode uses gpt-4.1-nano 2025-04-14. Mini is a supplemental frozen comparison.
Never tune `evaluation\settings*.json`, `dataset*.json` or selector source against
these observations. `tools.h5_metrics [--nano]` recomputes retained results;
`tools.evaluate_h5 --live [--nano]` refuses overwriting evidence.
The dynamic gate records both completed experiments without claiming acceptable
latency or factory improvement. Both owned model deployments are in teardown.

## Intended architecture

- `contracts\capability.schema.json` defines one type-discriminated contract for
  skills, tools, agents, and workflows; `docs\contract.md` documents which fields
  are Toolbox-native versus contract-only. `catalog\catalog.json` holds contract
  instances and references existing factory pinned manifests without changing
  those manifests.
- `tools\validate_catalog.py` is the validation/CI entry point. It must
  validate every entry against the schema, resolve skill/tool entries to existing
  pinned Toolbox versions, and reject privileged entries in the general project.
- `selection\` exposes `select(context, catalog, policy)` returning candidate IDs
  and rationale. Selection reads contract metadata, never skill/tool bodies.
  The static implementation uses domain, operation, requires, technology
  constraints, kind, lifecycle, roles, risk tier and status to reproduce the
  factory's existing permitted-skills outcomes exactly. The measured model-based
  implementation uses BM25 over name, description, examples, and tags, followed
  by a pluggable small model.
- `adapters\core\` owns registry listing, selection-based filtering, pinned-version
  resolution, and audit records. It must not import runtime-specific packages.
  `adapters\copilot_sdk\` handles final skill loading and temporary-directory
  materialization; `adapters\maf\` handles final MCP tool invocation. Keep the
  front door's A2A/handoff paths unchanged.
- `infra\` and the ownership manifests describe the deployed testbed.
  The narrow built-ins failed; one isolated custom-role trial supplements them.
  Foundry User is accepted only as a documented platform limitation. Prove
  coder denial on the privileged project and repeat after fifteen minutes.
  General v3 omits deployment, but immutable general v2 still exposes it.
  H6 independently proves mock execution enforcement, not network reachability
  closure of general v2. Never claim these are the same boundary.

## Implementation constraints and evidence

- Use Python throughout and the Copilot SDK version pinned by the existing PoC.
  Add no factory runtime dependencies beyond the skill provider's existing
  requirements. Record anything that would not port to .NET.
- Preserve bridge mode as the factory default and retain the existing opt-in
  flags for preview Toolbox skills and the SDK skill provider. Preserve existing
  factory behavior and shared-skill pins.
- Toolbox `factory-skills` version 2 is the unpromoted synthetic-tool testbed;
  default version 1 and all ten existing skill pins remain unchanged.
  `catalog\bindings.json` records exact service-advertised tool names.
  Do not promote, republish .NET content, or alter original bindings without
  a separate decision. See `mocks\README.md` and `docs\changes`.
- Source factory prerequisites are technologies, not capability IDs. Preserve
  them as conjunctive technology presence constraints; actual source capability
  dependencies become `requires`. Do not invent alias capabilities.
- Skills are MCP resources (`skill://index.json`, `resources/read`); Tool Search
  ranks `tools/list`, not skill resources. The skill index supplies
  name/description/type/url, not role, technology, task, or risk metadata.
  Keep that metadata in the contract; do not depend on custom Toolbox-version
  metadata.
- Multi-file SDK skills require a directory. Retain ephemeral per-session files
  rather than treating "file-free" as in-memory-only loading.
- Audit records include entry ID, version, identity, selection rationale, and
  outcome. Model-selection logs additionally include model, top-k, latency,
  and chosen versus baseline results.
- Validate the contract against all ten actual factory skills and all actual
  front-door capabilities. Report fields that cannot be populated honestly.
  Evaluate selection on contract examples plus the IKEA spike's recorded
  requests; report accuracy, p50/p95 added latency, and the share of requests
  with unambiguous BM25 top-1 results.
- Trust-tier tests must record raw HTTP evidence: coder identity against the
  privileged endpoint returns 403, release identity returns 200, and general
  shared-skill pins remain unchanged. Configuration alone is not evidence.
- `docs\hypothesis-report.md` must assess H1-H5 as supported, not supported, or
  partial, with evidence and what would change the answer. Separate verified
  facts from inferences; do not invent Toolbox, Tool Search, or SDK behavior.
  Test undocumented behavior or mark it unverified.

## Delivery order

Implement contract -> catalog/validation -> static selection -> adapter core
and runtime adapters -> trust-tier split -> governance/ACA -> H6/H7/H8 report
-> model-based selection/H5 -> final report.
After the adapters, recompute `tools.assess_gate` and stop unless every revised
H4 condition passes. Do not hardcode a success or discard counterexamples.
For H3, report core-versus-adapter line counts and logic that could not be shared.

Do not expand into orchestration, dispatching, role sequencing, front-door UI,
content signing/attestation, or emulated toolbox-scope RBAC. Changes to the
external factory/front door must be limited to what the hypotheses require.
