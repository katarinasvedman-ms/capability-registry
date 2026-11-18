# Brief addendum: runtime action control, reference consumer, hosting

**Applies to:** the `capability-registry` testbed brief (H1–H5). This document adds to it; it does not replace anything in it.
**Scope change:** the testbed is now a **stand-alone solution accelerator** for the governed capability registry pattern. Electrolux and IKEA are no longer the targets; they become two *configurations* used to prove the hypotheses. Nothing customer-specific goes into the repo.

---

## 1. What is added and why

The original brief covers two questions:

1. **Registry** — what exists, in which version, approved by whom. (Toolbox + catalog + publish pipeline.)
2. **Selection** — what this job or request may *see*. (Policy filter, then matching.)

Both stop before execution. They narrow the candidate set but trust the model to call the chosen capability with the right inputs. This addendum adds the third question:

3. **Runtime action control** — is *this exact call* allowed: this capability, this version, these inputs, this task, this identity, once, now.

This is agent governance in the strict sense. It is implemented as a **lease issued against a contract entry and verified at an independent gateway**. The concept comes from the `agentic-harness-validation` repo (ADR 0002, intent-based capability leases). Only the concept moves over; the code does not (it is .NET + Dafny; the accelerator is Python). Formal verification is explicitly **out of scope** — schema validation against the contract's `inputs` plus deterministic policy replaces it.

---

## 2. Verified platform facts to anchor on

Facts, not assumptions. Each has been checked against documentation during the design sessions; re-verify version-specific details before the report.

| Fact | Consequence for the design |
|---|---|
| MAF **function middleware** intercepts every function call the agent executes, can inspect and modify arguments and results, short-circuit the call, or terminate. It only works on agents that wrap a `ChatClientAgent`. | Lease issuance lives in function middleware. The reference consumer must be a `ChatClientAgent`-based MAF agent. |
| Function middleware only sees tool calls that **execute in the agent's own process**. Tools that Foundry executes server-side (prompt agent with MCP/Toolbox tools) never pass through it. | The reference consumer calls Toolbox as an MCP *client*; it does not attach Toolbox to a Foundry prompt agent. Foundry-executed tools are out of scope for this phase. |
| **Agent Governance Toolkit (AGT)** is public preview. It intercepts tool calls in deterministic code before execution; YAML policy with `deny` / `allow` / `require_approval`; has a documented MAF integration and an `McpGateway` component. Its policy engine has moved to a separate project (`agent-control-spec`). | Use AGT for policy evaluation inside middleware. Treat it as a preview dependency behind an interface; the lease contract must not depend on AGT internals. |
| **Foundry Toolbox** `require_approval` exists per tool. | Maps to the contract's `approval_required`. The lease flow must respect it, not duplicate it. |
| **Foundry Hosted Agents** are GA; hosted-agent Toolboxes are GA; the *Python* MAF hosting package is prerelease. Hosted agents get a dedicated Entra agent identity. The container runtime contract: port 8088, `GET /readiness`, `POST /responses` or `POST /invocations`, SIGTERM. The hosting model is independent of the protocol. | Build the container to the hosted-agent runtime contract from day one, even though phase 1 runs in ACA. This makes the later move a deployment change, not a code change. |

---

## 3. Lease contract

The lease is the central artefact. It is issued by policy in middleware and verified at the gateway. It must be:

- **Bound to a registry entry**: `capability_id` + `version` from the contract. A promotion cannot change what was approved.
- **Bound to inputs**: a hash of the **canonicalised** arguments (sorted keys, normalised whitespace/number formats, schema-validated against the entry's `inputs`).
- **Bound to context**: `task_id` (the job or conversation), `agent_identity` (Entra object id of the caller), optional `approver` when `require_approval` applied.
- **Single-use and short-lived**: `lease_id`, `issued_at`, `expires_at`, consumed on first successful verification.
- **Signed**: by the issuing policy component, verifiable by the gateway without a round-trip to the agent. Key handling via Key Vault / managed identity. No shared secrets in code.
- **Fail-closed**: missing, expired, used, mismatched, or unverifiable lease → the gateway rejects. Never fall back to "allow".

Suggested wire format: a compact JWT (or equivalent signed structure) carried in a header on the MCP call. If a header cannot be injected for a given client path, carry it as a reserved argument. H7 tests both.

The lease does **not** contain the result, business logic, or anything the downstream domain owns. The domain system remains the final authority on whether the operation is allowed on its side.

---

## 4. Where leases apply — and where they do not

| Capability kind | Lease required? | Why |
|---|---|---|
| Tool (`call_tool` via Toolbox/MCP) | **Yes**, on every side-effecting call | This is the whole point. |
| Tool, read-only (knowledge lookups, Foundry IQ) | Policy decides via `risk_tier`; default **no** | Leases are for side effects. Over-applying them adds latency without control value. |
| Sub-agent (A2A) | **Yes**, for the handoff itself when the sub-agent can act | The handoff is the side effect from the caller's perspective. The sub-agent governs its own tool calls. |
| Skill (load into context) | **No** | Loading has no side effect. Selection (`permitted_skills`, trust tier) is the right control. The *tools the skilled agent then calls* are leased. |

This split is a contract rule: `risk_tier` and `type` together determine whether the gateway requires a lease for an entry.

---

## 5. Deliverables added to the brief

### 7. Governance middleware (`governance/`)

- `policy/` — AGT-backed policy evaluation behind a small interface: `evaluate(call) -> allow | deny | require_approval(approvers)`. Policy YAML lives in the repo and references contract fields (`risk_tier`, `approval_required`, `permissions`), never tool names hard-coded.
- `lease/` — issuance: canonicalise inputs, validate against the entry's `inputs` schema, build and sign the lease, write the audit record. Verification: a pure function usable by the gateway with no dependency on the agent code.
- `middleware/maf.py` — MAF function middleware that runs `policy → lease → inject` before every tool call and records the outcome. Terminates the call on `deny`; pauses for approval on `require_approval` (approval is a workflow step, not middleware).
- Audit record per call: `lease_id, capability_id, version, inputs_hash, task_id, identity, decision, approver, gateway_result`.

### 8. Gateway (`gateway/`)

Phase 1: a thin MCP-forwarding gateway in front of the Toolbox endpoint (AGT `McpGateway` if it fits; otherwise a minimal FastAPI/MCP proxy). Verifies the lease, then forwards to Toolbox with the agent's identity. Runs as its **own** container with its **own** identity — never in the agent's process.

Note for later phases: in production this role belongs in APIM (or equivalent). Do not build APIM policies now; document the mapping.

### 9. Reference consumer (`consumer/`)

A MAF agent, Python, `ChatClientAgent`-based, that demonstrates every path through the registry:

- Routes to **tools** via Toolbox `call_tool` through the gateway (leased).
- Hands off to **sub-agents** via A2A (leased).
- Loads **skills** on demand via the provider pattern from the existing factory work (selection-controlled, not leased).
- Uses the selection stage from the original brief (static first; model-based when H5 is run).

Exposes `POST /responses` (OpenAI Responses protocol) and `GET /readiness` on port 8088, handles SIGTERM. Identity via `DefaultAzureCredential` only.

### 10. Mock capability set (`mocks/`)

Enough to exercise every row of section 4 and both trust tiers:

- 3–4 tools behind a mock MCP server: at least one read-only (`general`), one side-effecting `general` (e.g. `create_ticket`), one `privileged` with `approval_required` (e.g. `deploy_release`).
- 2 sub-agents over A2A: one knowledge-only, one that can act.
- 3 skills from the existing factory set, across both tiers.

All mocks are deterministic and log what they received, so tests can assert exactly what reached them.

### 11. Infrastructure (`infra/`, `azd`)

- Phase 1 target: **Azure Container Apps**. Separate apps for consumer, gateway, mock MCP server, mock sub-agents. Managed identity per app. App Insights with OpenTelemetry. Key Vault for lease signing keys.
- Keep the existing two-project (trust tier) Toolbox layout from H4.
- Parameterise hosting so that the consumer image can later be deployed as a Foundry hosted agent without code changes. Do **not** implement the Foundry deployment path now; leave a documented stub.

---

## 6. Hypotheses added

### H6 — Lease blocks mutated inputs and replay

> A lease issued in MAF function middleware and verified at an independent gateway prevents execution of a permitted tool when the inputs differ from what was approved, when the lease has already been used, or when no lease is present — with no code path from the model to the tool that bypasses it.

Tests (all against the mock side-effecting tools):

1. Approved call with matching inputs → gateway 200, mock receives exactly the canonicalised inputs.
2. Same lease, inputs mutated after issuance (simulate the model or a bug changing an argument) → gateway 403, mock receives nothing.
3. Replay of a consumed lease → 403.
4. Expired lease → 403.
5. Call with no lease, correct identity → 403.
6. `privileged` tool without approval step completed → no lease issued; middleware terminates; audit shows `require_approval` with no approver.
7. Agent code modified to call the Toolbox endpoint directly, bypassing the gateway → connection fails (network/identity): Toolbox endpoint accepts only the gateway's identity. If this cannot be enforced in phase 1, record it as a known gap and the production mitigation.

Evidence: raw HTTP for each case, audit records, mock logs.

### H7 — Lease carriage through the gateway

> The lease can be carried from middleware to the gateway on the MCP call path the reference consumer actually uses, without modifying the Toolbox or the downstream tool.

Tests:

1. Header carriage via the MCP client the consumer uses. Record whether per-call headers can be injected after middleware runs.
2. Fallback: reserved-argument carriage, with the gateway stripping it before forwarding. Record whether Toolbox/tool schemas tolerate it.
3. A2A handoff: lease carried on the handoff request; the receiving sub-agent's gateway verifies it.

Report which path works, which doesn't, and what that implies for a future Foundry-hosted or Foundry-prompt-agent consumer (where the client is `FoundryToolbox` rather than your own MCP client — out of scope to test now, in scope to reason about).

### H8 — Latency budget of governance

> Policy evaluation + lease issuance + gateway verification adds acceptable overhead per tool call.

Measure p50/p95 added latency per call across the mock set, on ACA, with and without the governance path. No target is set in advance; report the number and where the time goes (policy, signing, network hop, verification). This feeds the same latency discussion as H5.

---

## 7. Updated order and stop conditions

Original: 1 → 2 → 3 (static) → 4 → 5 → 3 (model) → 6.

New: 1 → 2 → 3 (static) → **10 (mocks)** → 4 → **7 → 8 → 9** → 5 → **H6 → H7 → H8** → 3 (model) / H5 → 6 (report).

Stop and report:

- After step 4 if H1 or H3 is not supported (unchanged).
- After H6 test 2 if a mutated-input call reaches the mock. That means the canonicalisation or verification is wrong and nothing downstream is meaningful.
- After H7 if neither carriage path works. The pattern then needs a different enforcement point and the design should be revisited before further building.

---

## 8. Constraints added

- The gateway, the lease verifier and the consumer are **separate processes with separate identities**. No shortcut where the consumer verifies its own lease.
- No formal verification tooling. No Dafny, no model checkers.
- AGT behind an interface; the accelerator must still run with a trivial in-repo policy evaluator if AGT is unavailable.
- Phase 1 hosting is ACA only. The container must nevertheless satisfy the Foundry hosted-agent runtime contract (port, readiness, protocol endpoint, SIGTERM). Do not add Foundry hosting; do not block it either.
- Nothing customer-specific in names, mocks or docs. Electrolux and IKEA appear only in `docs/configurations/` as two example configurations of the same accelerator.

---

## 9. Out of scope (added)

- Foundry hosted-agent deployment (later phase).
- Governing tools executed server-side by Foundry prompt agents.
- APIM policies (document the mapping only).
- Formal verification of plans.
- Human approval UI beyond a minimal console or CLI prompt sufficient to test `require_approval`.

---

## 10. Items still to verify (carry into the report)

1. Can per-call headers be injected on the MCP client path after MAF middleware runs? (H7)
2. Does AGT's MAF integration run in the same place as hand-written function middleware, or does it require its own agent wrapper?
3. Can the Toolbox endpoint be restricted to accept only the gateway's identity (network or RBAC) in a single Foundry project? (H6 test 7)
4. Skill provider parity across SDK languages (unchanged from the original brief; matters if a .NET consumer is added).
5. Status of AGT's policy engine split (`agent-control-spec`) at the time of the build; pin whatever is used.
