# Brief: Governed Capability Registry — hypothesis testbed

## Context

Two engagements converged on the same pattern: a governed registry of reusable capabilities, consumed by different runtimes through thin adapters.

- **ai\-factory / factory\-skills** (existing): Copilot SDK agents in ACA Jobs consume skills from a Foundry Toolbox via a skill provider (experimental SDK hook, verified) or via a bridge (default). Selection today is a static permitted\_skills map per role.
- **Enterprise agentic front door** (existing demo): a MAF orchestrator in FastAPI selects and invokes capabilities (tools) via a local registry/invoker over MCP/A2A; mock systems of record.

Verified facts to respect:

- Toolbox is a generic registry: versions, pinning, promotion, one MCP endpoint for both tools and skills. Skills in Toolbox are preview; tools and Tool Search are GA.
- Skills are MCP resources (skill://index.json, resources/read); Tool Search ranks tools/list only. The index carries name/description/type/url — no role, technology, task or risk metadata.
- Per\-toolbox RBAC is not enforced; project\-level grant is the boundary. Role separation today is configured, not enforced.
- Copilot SDK skill provider needs a directory for multi\-file skills; file\-free means ephemeral per\-session files.

## Goal

Build a small testbed that lets us answer five hypotheses with evidence. Do not build a platform. Do not refactor the existing factory or front door beyond what each hypothesis requires.

## Hypotheses to test

- **H1 — One contract fits both kinds.** A single capability contract schema (type\-discriminated) can describe every existing factory skill and every existing front\-door capability without per\-kind escape hatches.
- **H2 — Contract metadata is sufficient for selection.** A selection stage that reads only the contract (not the skill/tool body) can reproduce the current permitted\_skills map for the factory and the current capability choice for the front door.
- **H3 — Generic adapter core.** Listing, filtering, pinning and audit are shared code; only the final step (load skill vs call tool) is runtime\-specific.
- **H4 — Project per trust tier is workable.** Splitting registry entries across two Foundry projects by risk\_tier enforces separation that toolbox\-per\-role does not, without breaking the factory's shared\-skill pins.
- **H5 — A decision model improves selection at acceptable latency.** Model\-based selection over contract metadata beats the static map on a labelled set, and a small model with a BM25 pre\-filter keeps added latency within budget.

## Deliverables

### 1\. Capability contract (contracts/capability.schema.json \+ docs/contract.md)

Fields, grouped and marked **Toolbox\-native** or **contract\-only**:

- Platform: id, type (skill \| tool \| agent \| workflow), version, status (draft \| approved \| deprecated), protocol, invocation (uri or call spec)
- Domain: name, description, examples[] (request → this capability), tags[], owner, inputs (JSON Schema for tools; null for skills)
- Applicability (contract\-only): roles[], technologies[], tasks[], dependencies[]
- Controls: risk\_tier (general \| privileged), permissions[], approval\_required, reviewer, approved\_at

Write a contract instance for all ten factory skills and all front\-door capabilities. Report every field that any entry could not fill honestly — that is the H1 result.

### 2\. Catalog \+ validation (catalog/, tools/validate\_catalog.py)

- A single catalog.json of contract instances, replacing the factory's current catalog.json where fields overlap. Keep the factory's pinned manifests as\-is; the catalog references them.
- CI check: every entry validates against the schema; every skill/tool entry resolves to an existing pinned Toolbox version; no privileged entry lives in the general project (see H4).
- Do not add custom metadata to Toolbox versions. If you find a documented way to do so, report it; do not depend on it.

### 3\. Selection stage (selection/)

A library with one interface: select(context, catalog, policy) \-> [candidate ids] \+ rationale. Two implementations:

- **Static**: reproduce today's permitted\_skills behaviour from roles[] \+ risk\_tier \+ status. Must be byte\-identical in outcome to the current map for the factory. This is the H2 baseline.
- **Model\-based**: BM25 pre\-filter to top\-k on name/description/examples/tags, then a small model picks and explains. Pluggable model; default to the smallest available. Log model, k, latency, and chosen vs baseline.

Build a labelled set from the contract examples[] plus the IKEA spike's recorded requests. Produce a table: static vs model\-based accuracy, p50/p95 added latency, share of requests where top\-1 BM25 was already unambiguous (and so the model could be skipped).

### 4\. Adapter core (adapters/core/, adapters/copilot\_sdk/, adapters/maf/)

- core: list from registry, filter via selection output, resolve pinned version, emit audit record (entry id, version, identity, selection rationale, outcome). No runtime imports.
- copilot\_sdk: existing skill provider rewired to use core. Behaviour unchanged; retain temp\-dir materialisation.
- maf: tool consumer for the front door using core for list/filter/pin/audit and call\_tool for invocation. Replace the demo's local registry/invoker for the capabilities that are in Toolbox; keep A2A/handoff paths untouched.

Report lines of code in core vs each adapter, and any logic you could not move into core. That is the H3 result.

### 5\. Trust\-tier split (infra/)

- Create a second Foundry project privileged. Move one existing skill into it (choose the one closest to needing elevated access, or add a deliberately privileged test skill that references a secret name).
- Grant: factory role identities get Foundry User on general only; one release identity gets both.
- Tests: coder identity against privileged endpoint → 403; release identity → 200; shared skill pins in general unchanged. Record raw HTTP evidence, as in the earlier RBAC report.

### 6\. Report (docs/hypothesis\-report.md)

One section per hypothesis: **supported / not supported / partial**, evidence, and what would change the answer. Separate verified facts from inferences. No recommendations beyond what the evidence shows.

## Constraints

- Python throughout; pinned Copilot SDK version as in the current PoC. Note anything that would not port to .NET.
- No new runtime dependencies for the factory beyond what the skill provider already requires.
- Bridge mode remains the factory default. Nothing here may break it.
- Preview features (Toolbox skills, SDK skill provider) stay behind the same opt\-in flags as today.
- Do not invent facts about Toolbox, Tool Search or the SDK; where the docs are silent, test and record, or mark as unverified.

## Out of scope

- Any orchestration logic, dispatcher, or role sequencing.
- Front\-door UI changes.
- Signing or attestation of skill content.
- Waiting for or emulating toolbox\-scope RBAC.

## Order

1 → 2 → 3 (static only) → 4 → 5 → 3 (model\-based) → 6. Stop and report after step 4 if H1 or H3 is not supported; that changes the rest.

