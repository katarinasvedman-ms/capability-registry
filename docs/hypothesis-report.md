# Hypothesis report: report 6, leased governance and measured selection

## Changes since last report

- H6/H7 were formally reported at **2026-11-17T10:30:05.139547Z**, before
  the first selector deployment at 10:40:09Z. The immutable report-five
  checkpoint and its hash remain under `.artifacts\governance`.
- Added guarded metadata-only BM25/model selection and ran two frozen
  36-case, two-repetition experiments: **72 requests per model, zero errors**.
  Nano improves front-door top-1 to **58/60** versus static **50/60**, but
  regresses factory acceptance to **5/12** versus static **10/12**.
  **H5 is partial**, not a recommendation to replace the factory selector.
- Deployed the smallest available naming-tier default, gpt-4.1-nano, and
  retained gpt-4.1-mini as a supplemental comparison. Neither experiment was
  retuned. Both model deployments are tagged and covered by teardown.
- The final ACA image is
  `sha256:2fd3fc46fba3b215be6c0344910dbc3feb2585439b85ec05de78fe35f71750f2`.
  Live hosted selection does not execute tools; a separate exact approval
  produces one leased ticket call and matching mock/audit record.
  Immutable controls, privileged coder 403 and general-v2 downstream rejection
  were rechecked at **12:36:42.805839Z**.
- No existing grant, Toolbox version/default/member pin, original worker,
  shared networking or .NET package changed. No Toolbox version was promoted.

### Previously accepted report-five changes

- Applied the explicit route decision: general v2 remains reachable; execution
  now requires the gateway managed identity and an exact, signed, single-use lease.
  Shared Foundry networking, existing grants and the original factory were not changed.
- Deployed separate consumer/gateway/mock ACA apps and identities, Key Vault
  remote signing, durable atomic lease-use storage and workspace-backed telemetry.
  H6-H8 measured image 4,
  `sha256:3a04b7c94324670347db5b2d5b53e3a81018315858dea6098ab14296cbe4c6fe`.
  Image 5 adds selection; H8 was not relabelled as a new-image measurement.
- **H6 and H7 are supported for the revised measured scope.** Both mutation
  points fail before the mock; both native MCP carriages and a leased A2A handoff work.
- Measured H8: paired added p50/p95 is **101.646/159.982 ms** for deploy and
  **114.727/185.963 ms** for ticket. This is a secured, pre-authorized control,
  not an unsecured/no-gateway baseline; H8 remains partial on overall acceptability.
- Gateway-only execution became live at **2026-11-17T09:51:18.036208Z**.
  The exact dev tunnel was deleted at **09:52:23.275913Z**; its old route returns 404.
  All immutable compositions/default/pins remain unchanged, and bridge activation
  still succeeds. The .NET proposal remains pending; no version was promoted.
- Report five recorded H6/H7 before H5. Its unchanged snapshot is the
  prerequisite evidence used by the dynamic H5 entry gate.

### Previously accepted report-four changes

- Applied the user's revised policy: Foundry User breadth is an accepted
  **platform limitation**, separate from H4 project-boundary enforcement.
  One isolated, inventory-derived custom role also returned initialize 403
  after full propagation; its complete definition and responses are recorded.
- Published general **version 3**, explicitly excluding deploy_release while
  retaining the other three mock tools and all ten skill pins. Versions 2 and
  3 remain unpromoted; default 1 and existing version bytes are unchanged.
- Created the tagged sibling privileged project, deploy-only Toolbox and
  release-test certificate identity. The existing deploy contract fixture is
  now privileged and rebound there; no existing grant was widened.
- Recorded coder general-v3 200 versus privileged initialize/direct-call 403,
  release 200 on both projects, and the MAF approved-call/no-approval-no-call
  pair. The delayed coder repeat passed after **66,059.604 seconds**.
- Retained unchanged H1-H3 evidence and 26 allowed readiness issues; native
  bridge activation succeeded after the split. Report three is preserved under
  `.artifacts\round-3`; extended teardown is written and **not run**.

**H1-H4 are supported for the measured scope. H4 supports project-boundary
enforcement, not global isolation of the mock deployment operation or
Toolbox-only least privilege.** General v2 still advertises deploy, but its
downstream mock rejects unleased execution. Reachability is not execution authority.

## H6: exact-call leases and bypass prevention — supported, revised scope

**Evidence:** `.artifacts\governance\live.json`, governance-image-4 run
**2026-11-17T10:15:26.566228Z**, contains redacted raw HTTP, native middleware
audit, gateway audit and exact received mock bodies. Previous-image evidence
is retained in `live-before-final-image.json`.

| Test | deploy_release (privileged) | create_ticket (general) |
| --- | --- | --- |
| 1: approved, matching inputs | 200; exactly one accepted call | 200; exactly one accepted call |
| 2a: changed after middleware/before MCP | 403; zero mock requests | 403; zero mock requests |
| 2b: replayed lease, changed gateway body | 403; zero mock requests | 403; zero mock requests |
| 3: consumed lease replay | 403 | 403 |
| 4: expired lease | 403 | 403 |
| 5: no lease, correct caller | 403 | 403 |
| 6: approval incomplete | 403; require_approval, no lease/call | 403; require_approval, no lease/call |

Both approval flags are contract floors; ticket's contract is deliberately
stricter than its remote metadata. Middleware issues RS256 leases using the
consumer identity's exact Key Vault key grant; private signing material never
leaves Key Vault. RFC 8785 inputs, capability/version, immutable pin, caller,
task and approver are bound. Independent gateway verification consumes a
nonce using an atomic Azure Table insert. The mock independently verifies
gateway Entra identity, exact lease and a separate atomic nonce partition.
An actual probe inside the gateway ACA with its valid managed identity but
**no lease** returned 403 for both tools (`gateway-no-lease-probe.txt`).
The read-only `get_build_status` exemption is separately proven on image 5:
200, one accepted mock call, gateway identity checked and no approval/lease
issued (`read-tool.json`). H6's missing-lease denials concern side effects;
they do not remove the contract's intended read-only exemption.

Test 7, from factory-coder-test, has raw evidence for every revised route:

- Privileged direct deploy: **403**, preserved after migration.
- General v2: list **200** and deploy remains advertised. Direct deploy succeeded
  before closure, then Toolbox returned HTTP **200 with JSON-RPC -32003** reporting
  the downstream mock's **403**. This is not misreported as Toolbox HTTP 403.
- Retired dev tunnel: **404**, after exact-tunnel deletion. The old owned local
  mock and tunnel processes were also stopped.

The owned RemoteTool connections' mutable targets were changed to the secured
ACA mock; immutable server_url annotations still show the historical tunnel.
A live routing probe proved target indirection before migration, then restored
its diagnostic change. All four pinned manifests and general default compare
byte-for-byte equal before/after, including final controls. No grant was widened.

**What would change the answer:** either mutation reaches the mock; any
non-403 privileged coder response; acceptance without gateway identity and a
valid exact lease; changed immutable controls; or loss of the measured
RemoteTool target/metadata-forwarding behavior. General-v2 exposure itself is
the explicitly accepted limitation, not a silently failed network-closure test.

## H7: lease carriage — supported for the actual Python client

**Evidence:** ACA image-4 run **2026-11-17T10:15:33.952143Z** in `live.json`.
Pinned MAF 1.25.0's actual `MCPStreamableHTTPTool` carries the lease after native
function middleware using `header_provider`. Matching lease IDs connect
consumer audit, gateway receipt and exactly one accepted mock call.

The same native client also passes the reserved `_capability_lease` fallback
using runtime-only `additional_tool_argument_names`. The gateway strips it
before schema validation/Toolbox forwarding; received mock arguments contain
only the capability inputs. No immutable Toolbox schema/version was changed.
One native MAF function performs a leased A2A `message/send` handoff; the
independent receiving mock verifier records exactly one accepted sub-agent call.

The installed API is named `Agent`, not `ChatClientAgent`; the deterministic
chat client includes `FunctionInvocationLayer`, so native tool execution and
function middleware actually run. It is not a model-quality experiment.
The low-level MCP `ClientSession.call_tool` has no per-call header parameter;
that does not negate the measured high-level MAF path.

**FoundryToolbox reasoning only:** a future client must expose an after-policy
header/runtime-argument injection point or invoke through a lease-aware
transport. Server-side prompt-agent tools bypass local function middleware.
No FoundryToolbox/header compatibility or Foundry-hosted deployment is inferred.

**What would change the answer:** client/session behavior prevents both
carriages, reserved data leaks downstream, or a handoff loses exact context.

## H8: ACA governance latency — partial, measured

**Evidence:** `live.json` and derived `latency.json`, measured
**2026-11-17T10:16:41.876607Z**. Twenty alternating paired samples per tool,
after two warmup pairs; no samples or failures were silently discarded.
Percentiles use linear interpolation at `(n-1)*p`. All values below are ms,
shown as **p50 / p95**.

| Component | deploy_release | create_ticket |
| --- | ---: | ---: |
| Prepared-control end to end | 427.328 / 490.166 | 416.888 / 454.564 |
| Governed end to end | 525.582 / 582.090 | 534.474 / 590.427 |
| **Paired added end to end** | **101.646 / 159.982** | **114.727 / 185.963** |
| Policy | 0.008 / 0.009 | 0.008 / 0.009 |
| Schema/canonicalisation/hash | 0.112 / 0.149 | 0.114 / 0.242 |
| Remote Key Vault signing | 38.314 / 65.551 | 37.198 / 50.109 |
| Gateway verification incl. atomic use | 7.323 / 11.335 | 7.363 / 9.554 |
| MCP transport/session residual, governed | 74.863 / 87.203 | 75.416 / 95.955 |
| MCP transport/session residual, control | 7.573 / 9.221 | 6.970 / 9.598 |
| Gateway residence incl. downstream, governed | 342.562 / 388.674 | 343.076 / 384.564 |

The control preissues a lease and completes gateway signature/atomic-use
verification outside the timed span. Dispatch atomically consumes a prepared
grant and rechecks expiry, inputs and all bound context before forwarding.
The mock still requires gateway identity, independently verifies the lease
and consumes its nonce in **both** modes. This protects the user-required
security floor; it is **not** an unsecured/no-proxy baseline.

The physical gateway hop is common. The reported transport/session residual is
native MCP dispatch minus gateway residence, not isolated wire RTT; header
changes cause session reconciliation. Component percentiles are not additive.
Mock verification/downstream transport remain inside both end-to-end values.
No target was supplied, so "acceptable" and the full cost versus no gateway
are not established.

**What would change the answer:** an agreed budget and an explicitly authorized,
isolated no-gateway comparator, plus larger/load/cold-start samples. These
results are single-replica warm synthetic calls, not production capacity proof.

## Accepted limitations carried forward

- **Role breadth:** Foundry User remains the H4 platform limitation. Gateway
  execution control does not revoke direct Foundry reachability.
- **Synthetic approval:** authenticated test fixtures issue exact signed leases;
  no human UI or production approval authority is claimed.
- **Python only:** no .NET runtime/provider parity is newly claimed. The .NET
  content proposal remains pending; its pinned package is untouched.
- **General v2 exposure:** deliberately retained. Production choices are no
  project grant, stale-version retirement or gateway-only private ingress;
  none was applied to general v2 because grants/versions/shared networking
  must remain unchanged.
- **Mutable connection indirection:** execution follows the owned connection's
  target even while immutable advertised server_url remains historical.
  This service behavior and trusted `_meta` forwarding are measured dependencies.
- **Identity:** caller's original AI-audience token is forwarded to Toolbox;
  no OBO delegation is claimed. Only gateway execution assertions use its MI.
  Mock metadata discovery remains public; every executable call requires gateway
  identity, and side-effecting calls additionally require an exact lease.
- **Reference consumer:** deterministic, nonstreaming Responses fixture, three
  selected general skills and one action sub-agent; not a general conversational
  agent or cross-language/Foundry-hosting test. The separate `/selection` endpoint
  supports static/BM25/model decisions; execution still requires a second,
  exact approved request. Live Responses objects validate
  against the pinned OpenAI model. Port 8088/readiness and graceful SIGTERM/
  restart are recorded in ACA/Log Analytics; no Foundry deployment occurred.
- **Operations:** one replica, short-lived test certificates, bounded in-memory
  evidence/prepared caches. Replay nonces are durable but have no automatic TTL
  cleanup. Telemetry proves server-span ingestion and audit correlation, not
  complete distributed trace propagation. H8 has the comparator limits above.
- **H5 quality and scope:** small, manually annotated synthetic/source dataset,
  only eight held-out goals and two repetitions, not independent samples or
  production task-quality proof. Model selection can choose a wrong approved
  alternative; nano loses factory-required semantic coverage. The unchanged
  static factory selector remains the parity baseline. No safe model-skip rule
  or acceptable-latency budget is established. Temperature/seed do not guarantee
  identical repeated outputs. H5 latency is controller-to-Foundry, not ACA
  execution latency; the live ACA proof is separate.

`scripts\teardown-testbed.ps1` covers the new owned resources and H4 set.
It is syntax-checked only, **not run**. Key Vault soft-delete retention is not
bypassed. It deliberately does not restore the retired unauthenticated route;
retained general v2's owned connection will fail closed after ACA teardown.

## H1: one contract fits both kinds — supported

**Evidence:** all original 25 entries plus three mock tools validate structurally.
The schema has no per-kind extension dictionaries. Domain and operation now
participate in selection. Capability `requires` references resolve and cycles
are rejected. Numeric technology constraints reject unsupported/missing version
evidence.

Factory prerequisites are technology names, not capability IDs. They are
preserved as conjunctive technology presence constraints; only actual capability
dependencies become `requires`. The runtime label `copilot-sdk-python-1` is
preserved as a required technology, not misrepresented as SDK version 1.
See `docs\contract.md` for the common semantics and their bounded grammar.

The original domain pair now projects differently and has identical source/
contract filtered results: `operations.order_hardware` for Operations, no result
after changing only its domain to AnotherDomain. The source files were not edited.

Both **concrete, valid counterexamples** now pass in `.artifacts\frontdoor.json`:

| Counterfactual based on a captured entry | Source result | Contract result |
| --- | --- | --- |
| `people.leave_policy`: change only source discovery type knowledge to read | Knowledge-filter discovery excludes the executable-read variant | Kind changes knowledge -> action, independently of unchanged read operation; filtered IDs match |
| `operations.old_inventory`: change only operational status deprecated to active | Inventory discovery changes from empty to that entry | Lifecycle changes deprecated -> active while approval stays draft; metadata filtered IDs match |

No source or fixture approval was changed by these counterfactuals. Source
read maps to the executable action path with read operation, not to knowledge;
source inactive maps to retired. All fifteen source entries round-trip exactly.
`docs\contract.md` lists every inferred kind/lifecycle value, also recorded in
`catalog\field-gaps.json`. Deprecated discovery requires an explicit trusted
opt-in, retired discovery is always excluded, and both still resolve immutable
pins for historical audit. Neither permits new execution.

**What would change the answer:** a new source-valid semantic collision, an
unmapped source field, or a future source revision that fails the same checks.
This result covers the captured source set, not every possible capability.

### Fixture review and readiness

Reviewer is `katarina.svedman`, approval date 2026-11-16, scope `testbed`, as
recorded in `catalog\testbed-approval.json`. Approved fixtures are:

- All ten factory skills: factory source-fit/selection and SDK adapter fixtures.
- `operations.order_hardware`: domain parity and the authorization, input,
  approval, revocation and idempotency envelope.
- `operations.open_expenses`: unchanged Concur handoff regression.
- `mock.get_build_status`, `mock.create_ticket`, `mock.deploy_release`: live
  read/write/approval transport paths.

Everything else stays draft, including the source-deprecated inventory entry.
Source team owners remain intact. Ten skills and three new mocks have
`owner: unassigned`. Fourteen fixtures are `general`; the revised H4 decision
explicitly classifies synthetic `mock.deploy_release` as `privileged`, without
changing its approval status/scope. This is a user-authorized isolation fixture,
not inferred elevated access for every write. The unselected administrative capability
remains draft; ordinary write permission was not treated as privileged access.

| Readiness cause | Before: 25 entries | After: 28 entries |
| --- | ---: | ---: |
| Unapproved draft / draft, not a fixture | 24 | 13 |
| Missing pinned Toolbox binding | 21 | 0 |
| Owner unassigned | Not separately checked | 13 |
| **Total** | **45** | **26** |

For the original 25 entries alone the revised count is 23; the three new
unassigned mock owners add three issues. The revised check reports ownership
explicitly and requires Toolbox pins for approved MCP fixtures, not for draft
nonfixtures or non-Toolbox handoffs/knowledge routes. Thus the counts describe
the old and newly authorized readiness policies, not an unchanged metric.
All remaining causes are precisely the two allowed by the gate.
Before evidence is preserved under `.artifacts\round-1`; after counts and
per-ID groups are in `.artifacts\gate.json`.

## H2: metadata-only selection is sufficient — supported

**Evidence:** no review overlays or skill bodies are used to generate candidates.
For both coder and tester in the captured .NET implementation/testing context,
the contract result is byte-identical under canonical serialization to both
the provider permitted map and the original bridge selector:

```json
["dotnet-development", "test-strategy"]
```

`.artifacts\selection-parity.json` separately records each oracle/result.
Provider permitted/required maps were not used to generate candidates. Permitted
maps remain ceilings. Bridge cases also cover an unknown stack, forced unmet
technology prerequisites, all-runtime incompatibility, and a single incompatible
skill among otherwise compatible skills. Invalid prerequisites/compatibility
reject the request rather than silently dropping a required bridge skill.
Rejection wording differs: source `Unmet prerequisites` / `Incompatible skill`
versus contract `Unmet technology constraint`; those raw diagnostics are retained.

The source/contract domain pair passes. Front-door parity covers 5,600 combinations
of real names/examples, source discovery-type filters, domains and permission
sets, preserving weighted scores, threshold, ordering and limits. Independent
knowledge/action/handoff filters and active/deprecated/inactive source states
also pass. Detailed cases are in `.artifacts\frontdoor-parity.json`.
No source selector is called by the contract implementation.

**Limits:** full metadata parity compares the same fifteen source capabilities;
actual consumer selection applies the same authorized fixture ceiling to both
oracles. It does not expose draft source entries by inventing approvals.
The original policy-envelope checks still pass. No model accuracy, all-role
activation, future source revision, or H5 latency result is inferred.

**What would change the answer:** a source/contract mismatch under these filters,
the fixture policy or factory prerequisites/compatibility. Source filter cases
remain in the acceptance criteria rather than being dropped after passing.

## H3: generic adapter core — supported for the tested paths

**Evidence:** both runtime adapters use the shared listing, selected-ID filtering,
pin/schema resolution, transport and audit core. Runtime-specific dispatch
remains isolated; the original front-door policy envelope and non-MCP handoff
are retained.

### Live MAF tool path

The existing general project is
`https://factory-skills-resource.services.ai.azure.com/api/projects/factory-skills`.
Toolbox `factory-skills` version **2** adds the synthetic RemoteTool connection;
default **1** and all ten original member pins **1** are unchanged.
`.artifacts\mock-publication.json` records before/after state and readback.

The deterministic server under `mocks` exposes three requested tools plus
`order_hardware`, which gives the approved source fixture a real testbed pin.
Each completed:

`list -> contract filter -> resolve version 2/schema -> call_tool -> core audit`.

| Tool | Operation | Confirmed path |
| --- | --- | --- |
| `get_build_status` | read | `BUILD-42` returns synthetic succeeded status |
| `create_ticket` | write | Deterministic synthetic ticket; original front-door action confirmation retained |
| `deploy_release` | write, remote approval required | Exact `v1.2.3` approval before call; synthetic deployment only |
| `order_hardware` | write, approval required | Exact laptop approval; no actual hardware order |

The remote `create_ticket` metadata allows non-confirming calls, but the original
front-door Catalog requires approval for all actions. The consumer deliberately
keeps that stricter rule; it was not silently weakened to create another path.
Approved write replay returned stored results without duplicate calls.

Foundry adds a three-underscore server-name prefix and the default JSON Schema
2020-12 annotation. Bindings preserve exact advertised names, and schema
comparison normalizes only the equivalent default annotation. No input
constraints were removed. Both transformations are recorded, not concealed.

`.artifacts\maf-live.json` contains each raw upstream JSON-RPC request/response,
the actual request/response received by the mock, the original Invoker activity
and core audit. Identity `alex`, approval IDs and trace IDs reached the mock in
MCP `_meta`. Authentication to Foundry used the operator's Azure CLI credential;
`alex` is the source's synthetic policy identity, **not** a worker/RBAC proof.
The MAF dependency environment is separate; no MAF model inference was needed
to exercise its real application adapter.

### Live native Copilot SDK activation

After the live MAF checks, the dev session ran bridge distribution first,
then provider distribution through the core adapter, with SDK **1.0.31**,
SDK-managed CLI **1.0.109**, and the existing authorized
**claude-haiku-4.5** Copilot model. No Azure model was deployed.

Both activated `test-strategy` from unchanged historical member pin 1:

| Mode | Native event | UTC timestamp | Native source |
| --- | --- | --- | --- |
| Bridge | `skill.invoked` | 2026-11-16 12:16:43.313 | `custom`, core-pinned materialized directory |
| Provider | `skill.invoked` | 2026-11-16 12:17:04.080 | `sdk`, lazy core-backed provider |

The events are native SDK notifications, not a model's statement or fabricated
callback. Full events, native skill catalogs, HTTP observations, core audits
and successful temporary-directory cleanup are preserved under
`.artifacts\sdk-live\20261116T121627973238Z`; the summary is
`.artifacts\sdk-live.json`. An earlier model-availability failure for `gpt-4.1`
is preserved in the previous attempt directory; no activation was claimed for
that attempt.

These are isolated dev-session distribution checks, not deployed factory
worker runs or a replacement for its approved-mirror verification. The original
factory bridge remains default and provider remains opt-in.

### Shared code and remaining specialization

| Code | Nonblank physical Python lines |
| --- | ---: |
| New shared core, including Foundry transport | 413 |
| New Copilot SDK adapter | 78 |
| New MAF application adapter | 32 |
| Inherited SDK `read_skill` materialization | 70 |

`tools.assess_gate` recomputes these counts, excluding comment-only lines and
including docstrings, in `.artifacts\gate.json`.
SDK descriptors, safe archive materialization, directories, activation callbacks
and cleanup remain SDK-specific. Fresh business authorization, owner-scoped
state, input validation, exact-input approval, durable idempotency and final
tool-call context remain in the inherited front-door envelope.

The inherited SDK `read_skill` method is 70 such lines; inherited initialization/
failure/cleanup and the original Invoker are not misrepresented as new shared
code. The common transport retries bounded reads but never automatically retries
tool calls. The original provider's activated-file cache/integrity remains.

**Limits:** this supports shared adapter plumbing for the approved testbed
paths, not production downstream identity enforcement, worker isolation,
Toolbox-level RBAC, an attested remote server binary, or .NET SDK portability.
The Python callback/descriptor integration is not a demonstrated .NET port.
The historical H3 mock used a development tunnel. H6 replaces it with the secured
ACA mock; neither evidence set establishes production availability.

**What would change the answer:** a failing native activation, changed input
schema/pin, missing downstream call context, or a required runtime-specific
behavior that cannot remain behind the shared core would weaken this result.
Production identity/RBAC claims require separate evidence and are not inferred.

## Pinned .NET content change

`docs\changes\dotnet-pinned-content.md` and the adjacent full diff/JSON open the
documented change. Both `SKILL.md` and `references/validation.md` differ between
the historical pinned package and current source. Current source requires
explicit valid/invalid input and outcome evidence for input-validation proposals.
Current package bytes match the frozen source capture; its source semantic
version is 1.0.3. The pinned frontmatter does not identify a semantic version,
so no older semantic version was invented.

The proposal follows the normal skill-upload, downloaded-hash verification and
immutable Toolbox-composition publication path. The next numeric pin is
service-assigned, not guessed. **No .NET package/new .NET pin was published;
promotion remains the user's separate decision.**

## H4: project-boundary enforcement — supported

**Scope:** the revised user decision separates within-project role breadth from
access to a project with no grant. The prior stop was correct under the previous
rule and remains recorded in `.artifacts\h4\evidence.json`; it was not erased.
The new authorization epoch and complete raw HTTP are in `resumed.json`.

### Role breadth: known platform limitation, not the boundary result

| Candidate | Observed general MCP result |
| --- | --- |
| Reader | initialize 403 |
| Foundry Agent Consumer | initialize 403 |
| Foundry Project Runtime User | initialize 403 |
| Their accumulated union after 900.0165 seconds | initialize 403 |
| Foundry User fallback | initialize 200, initialized 204, tools/list 200 after propagation |
| New isolated custom-role trial | initialize 403 initially and after 900.644 seconds from its grant |

The earlier built-in trials accumulated grants on coder; they are not claimed
as separately isolated role trials. The new custom role was tested on the new
release-test principal **before** its Foundry User grants, against existing
general v1. Its grant was at **2026-11-16 14:00:20.856 UTC**; probes began at
**14:00:21.917** and **14:15:21.499 UTC**. No broad release grant contaminated
this trial. The actual custom definition ID is
`9206fe1d-1019-453c-a8db-ef472ec02c6f`.

The custom definition grants account/project management reads and only:

```text
Microsoft.CognitiveServices/accounts/AIServices/agents/read
Microsoft.CognitiveServices/accounts/AIServices/assets/read
Microsoft.CognitiveServices/accounts/AIServices/responses/*
Microsoft.CognitiveServices/accounts/AIServices/endpoints/interact/action
```

These are real inventory-derived discovery/runtime candidates. No registered
Toolbox-specific data action was found. The failed candidate therefore did not
replace Foundry User. This does not prove that every possible custom role fails.
`custom-role-definition.json`, `all-role-definitions.json` and
`provider-data-operations.json` retain the actual definitions/inventory.

Accepted Foundry User still grants `Microsoft.CognitiveServices/*` except:

```text
Microsoft.CognitiveServices/accounts/AIServices/agents/endpoints/UserIdentityImpersonation/action
Microsoft.CognitiveServices/accounts/OpenAI/fine-tunes-deployments/write
Microsoft.CognitiveServices/accounts/AIServices/fine_tuning_deployments/write
```

It also carries management reads, account key/project-connection secret listing,
deployment and support actions. These were recorded from the role definition,
not exercised to retrieve secrets. Least privilege for Toolbox clients therefore
depends on **project boundaries plus a gateway**, not built-in role selection.
The gateway is not implemented or proved by H4.

**What would change the role finding:** a built-in or custom Toolbox-scoped data
action that demonstrably enables initialize/tools-list 200 without the broad
wildcard. See `docs\toolbox-conventions.md`, Identity.

### Published split and exact identities

Both projects are under existing account `factory-skills-resource`, resource
group `factory-skills`, Sweden Central. The sibling
`factory-skills-privileged` project was created at
**2026-11-16 14:15:59.691 UTC**; nothing moved out of general.

| Project / Toolbox | Pin | Content | Default/promotion |
| --- | --- | --- | --- |
| General / factory-skills | 1 | Original ten shared skill pins 1 | Existing default, unchanged |
| General / factory-skills | 2 | Original four-tool mock connection and ten skill pins | Existing immutable version, unpromoted |
| General / factory-skills | 3 | get_build_status, create_ticket, order_hardware; same ten skill pins | New, unpromoted; cross-project tests use this exact pin |
| Privileged / capability-registry-privileged | 1 | Only deploy_release, no skills; require_approval always | First version automatically becomes this new Toolbox's default |

`allowed_tools` filters were verified from actual tools/list responses.
Bindings preserve advertised tool names. The existing deploy contract entry
is now tool/action/write, privileged, approved/testbed, active and
approval-required, pinned to privileged v1. The other three tool fixtures use
general v3; shared skill bindings remain on v1.

Coder reuses the exact manifest-owned certificate identity:
client `c81e9d6b-4e68-46b2-a8df-23dfdc7d67b2`,
principal `59a7e01a-f12e-4821-b31e-daab354ff9c1`.
Its four existing grants remain **general only**. Release-test is newly created:
client `43962035-c240-4dd0-b380-ac0c714a25f1`,
principal `efb75feb-397a-45e1-9249-d08a83b4ee0e`. It has the failed custom
trial grant on general, plus new Foundry User grants on general and privileged.
No existing grant was widened and no deployed worker identity was changed.

Reusing the general connection name under the new project returned a real
ownership-collision 400; the general connection was not updated. A distinct
`capability-registry-mock-f3e5ad02-privileged` connection succeeded.
ARM provisioning succeeded before the data-plane project became available;
the recorded project-not-found 404s were handled through bounded read-only
availability checks before publication, not presented as RBAC denials.

### Raw access matrix and propagation repeat

| Certificate identity / request | First observation, 2026-11-16 UTC | Repeat, 2026-11-17 UTC |
| --- | --- | --- |
| Coder -> general v3 initialize / tools-list | 200 / 200, 14:24:15.596 | 200 / 200, 08:45:23.729 |
| Coder -> privileged initialize | 403, 14:24:12.992 | 403, 08:45:28.425 |
| Coder -> privileged direct tools/call deploy_release | 403, 14:24:14.722 | 403, 08:45:29.149 |
| Release -> general v3 initialize / tools-list | 200 / 200, 14:24:17.987 | Not required |
| Release -> privileged initialize / tools-list | 200 / 200, 14:24:20.748 | Not required |

Timestamps identify probe start; full per-request bodies/statuses/responses are
retained. Direct calls intentionally bypass MCP initialization and the contract
adapter to test HTTP authorization independently; they include the real
advertised tool name and valid synthetic release input. Both returned HTTP 403.
General v3 advertises exactly the other three tools, including in the repeat.

The initial matrix completed at **2026-11-16 14:24:22.441 UTC**. The scheduled
repeat was interrupted with the session, then explicitly resumed on the user's
continuation. It began **2026-11-17 08:45:22.045 UTC**:
**66,059.604 seconds later**, exceeding the required fifteen minutes. This is
an overnight delayed repeat, not a claim that the original timer completed.
No revised boundary/default/pin safety stop occurred.

### MAF approval and factory controls

At **2026-11-16 14:24:35.959 UTC**, the MAF harness used the release certificate,
verified its JWT principal against the binding, and retained the original
Invoker authorization/input/exact-approval/idempotency envelope.
Listing, contract filtering, privileged pin/schema resolution, tools/call and
core audit all ran on the shared adapter path.

Without completed approval, invocation returned the pending approval and
terminated before dispatch: **zero HTTP requests, zero new mock requests and
no execution core audit**. After the testbed completed the real approval-state
transition for the exact `{"release":"v0.0.4"}` input, exactly one deployment
call reached the mock, with approval/trace context, HTTP 200 and a verified-pin
completed audit under the release principal. Raw request/response, source
events and audit are in `resumed-maf.json`. This synthetic approval transition
does not claim a production human approval or signed downstream authorization.

Controls after the split confirmed byte-identical existing general v1/v2
manifests, all ten unchanged instruction hashes, and default 1. Publishing v3
legitimately changes latest/updated_at, not default_version. Native dev-session
bridge activation of test-strategy succeeded at
**2026-11-16 14:25:11.668 UTC**, with temporary-directory cleanup. No factory
worker was reconfigured or triggered.

The CI negative harness now explicitly forges the **general** endpoint even
after deploy's real binding moved to privileged. Actual CLI validation rejects
it with exit **1**, including when the forged tier claims privileged.
The dynamic gate recomputes H4 from raw matrix/approval/control evidence and
elapsed time rather than treating a passing entry gate as H4 proof.

### Limits, ownership and teardown

**At the H4 checkpoint**, immutable general v2 exposed deploy_release and the
development tunnel exposed the unauthenticated synthetic mock. General v3 publication
does not revoke v2 or secure the server. H4 supports the revised project-boundary
question only; it does not establish that coder cannot invoke the same mock
operation through a historical or direct route. A production gateway and route
retirement were separate work. H6 above records the subsequently authorized
execution boundary and tunnel retirement without rewriting this H4 result.

Retained ownership totals: two applications, two service principals, two local
certificates, seven role assignments, one custom role definition, one new
project (including its system-assigned identity), one privileged connection,
one privileged Toolbox and one new general composition v3.
Certificates are ignored, ACL-restricted, one-day credentials, never printed.

Project/Entra objects carry purpose/owner/run tags. Role definitions and
assignments lack native tag support and use descriptions. The connection
service returned tags null despite supplied tags, so its supported metadata
carries purpose/owner/run labels. Compositions use descriptions/manifest,
not invented capability metadata. These exceptions are explicit.

`ownership.json` contains exact IDs. `scripts\teardown-h4.ps1` is extended and
**not run**, including no preview execution. It validates ownership and scopes,
requires explicit execution/confirmation, and includes the new v3, privileged
resources, custom role, certificate identities and assignments. Existing
general v1/v2/default/skill pins, evidence and mock/tunnel are not removed.
Execution is untested deliberately; PowerShell syntax was checked.

**What would change the boundary answer:** a non-403 privileged coder response,
failed release positive control, changed old pins/default, broken bridge, or a
future platform revision that changes these measured results. Role breadth
alone is a separate accepted limitation under the revised user policy.

**If toolbox-scope RBAC becomes supported and enforced, the two projects could
collapse into one without changing the contract.** That remains conditional
on live enforcement proof, not an available feature assumed by this experiment.

## H5: model selection and acceptable latency — partial, measured

**Evidence:** `evaluation\settings*.json`, `dataset*.json`,
`.artifacts\h5\evaluation.json`, `metrics.json`, their `nano` counterparts,
and `hosted.json`. Full raw sanitized model requests/responses, elapsed times,
errors, code hashes, provenance and entry-gate evidence are retained.
Mini ran **10:44:32-10:46:57Z**; nano **12:30:19-12:32:44Z**, 2026-11-17.

### Frozen method and deployments

Two owned deployments in the existing account/Sweden Central:
`cr-gov-f3e5ad02-selector` (gpt-4.1-mini) and
`cr-gov-f3e5ad02-selector-nano` (gpt-4.1-nano), both model version
**2025-04-14**, GlobalStandard capacity **50**, NoAutoUpgrade,
Microsoft.DefaultV2. Nano is the smallest available naming-tier default;
mini is supplemental. Both use the project OpenAI endpoint with the existing
release identity grant; **no additional model role or existing-grant widening**.

The 36 preregistered cases comprise nine contract examples (in-sample),
13 recorded front-door goals, eight manually annotated held-out goals and
six frozen factory assertions. Recorded request **text only** was captured
from the evolving read-only source, with its current hash/HEAD recorded;
historical model choices are not labels. Original frozen baselines were not
replaced. Draft source intents remain denied rather than inventing approvals.
Nano registration followed the mini run to satisfy the smallest-tier default;
its cases, labels, prompt and all non-model parameters are byte-equivalent,
with derivation hashes. No outcome-based tuning or selective rerun occurred.

Both experiments use top-k **5**, BM25 k1 **1.5** / b **0.75**, two repetitions,
temperature **0**, seed **19**, max output **256**, timeout **30 seconds**,
cap **120 requests per experiment**, minimum request interval **2 seconds**.
Each made **72 measured requests**; separate smoke calls are not benchmark
samples. No retries, timeouts, transport errors or invalid-output failures
occurred. Token totals: mini **35,476**, nano **35,523**.

Eligibility is enforced before retrieval: approval/lifecycle, trusted
permissions/tier/common filters, technology constraints and dependency closure.
Factory hard eligibility preserves static role/task/permitted/required controls.
Only ID/name/description/examples/tags reach the model, not bodies, bindings,
inputs or endpoints. Structured output is checked against the eligible top-k,
cardinality and required capabilities. Selection never grants execution or
approval. Zero-overlap candidates are available to the model for semantic
matching/abstention; BM25 alone abstains at zero.

### Accuracy, with distinct denominators

Counts are correct observations / total; two observations per frozen case.
Factory acceptance permits the source fixture's explicitly optional skills.
Strict exact-set excludes them; these are not front-door top-1 metrics.

| Scope | Static | BM25 | Nano | Mini |
| --- | ---: | ---: | ---: | ---: |
| Front-door top-1 | 50/60 | 48/60 | **58/60** | 56/60 |
| Eligible positive intents | 30/36 | 30/36 | 36/36 | 36/36 |
| Empty/denied/irrelevant front-door intents | 20/24 | 18/24 | 22/24 | 20/24 |
| Recorded goals | 20/26 | 18/26 | 24/26 | 22/26 |
| Held-out synthetic goals | 12/16 | 12/16 | 16/16 | 16/16 |
| In-sample examples | 18/18 | 18/18 | 18/18 | 18/18 |
| Factory source acceptance, optional allowed | 10/12 | 10/12 | **5/12** | 10/12 |
| Factory strict exact-set | 8/12 | 8/12 | 5/12 | 10/12 |

These slices overlap and must not be added. Eight recorded draft-intent cases
contribute 16 observations: static 12/16, BM25 10/16, nano 14/16, mini 12/16.
No draft or privileged-out-of-policy ID was returned; a wrong approved
alternative still fails the conservative source-intent label.
Factory S6 expects abstention in the source matrix, while both the frozen bridge
and contract static selector return `test-strategy` for an unknown stack. That
accounts for static's two errors without contradicting H2's exact source parity.

Concrete counterexamples: both models map the recorded broken-scanner
service-request intent to the approved synthetic `mock.create_ticket` instead
of abstaining for its draft source target. Mini also maps the existing-case
intent to that ticket. Nano omits testing from Python/Java factory cases and
.NET from the API case, and fails the unknown-stack abstention once.
Mini also misses the API case, returning only `api-development`; its aggregate
factory score hides a different error pattern, not factory dominance.
The synthetic ticket/source-service-request distinction is a label ambiguity
worth review in a future separately frozen dataset, not silently relabelled here.

### Added latency and skip heuristic

Controller-to-Foundry elapsed measurements, ms **p50 / p95**, 72 observations
per model; not ACA tool-execution measurements. Admission pacing is outside
the timed selector span; model HTTP excludes token acquisition. All observations
remain included, including first-call authentication. Percentiles use `(n-1)*p`.

| Component | Nano | Mini |
| --- | ---: | ---: |
| Static selector | 0.252 / 0.458 | 0.296 / 0.590 |
| BM25 retrieval/filtering | 0.340 / 0.768 | 0.381 / 0.840 |
| Model HTTP | 941.553 / 1467.334 | 979.599 / 1634.324 |
| **Paired model-selector added over static** | **941.868 / 1583.905** | **979.701 / 1634.536** |

The frozen positive-score margin ratio 1.5 flags **36/60 (60%)** front-door
observations. BM25 is correct on only 30/36; model agrees on 30/36, with nano
correct on 34/36 and mini on 32/36. This is **not a safe skip rule**.
The benchmark actually skips no model calls; the consumer default remains
static unless model mode is explicitly requested. No latency acceptance budget
was supplied, so acceptability is unproven.

### Minimal hosted reference consumer

ACA image 5 exposes authenticated `/selection` modes static/BM25/model with
server-supplied policy ceilings and nano as the explicit model-mode default.
The live release client selects `mock.create_ticket` in all three modes without
any mock call. A separate exact synthetic approval then reaches the mock once
through native MAF middleware, Key Vault signing and gateway verification;
lease IDs match the audit. Coder model selection cannot return privileged
deploy. `hosted.json` records raw HTTP, strict Responses validation, privileged
direct-call 403, general-v2 downstream rejection and unchanged pinned manifests/
default. This is a two-step fixture, not autonomous conversational orchestration.

**What would change the answer:** independently reviewed larger held-out
annotations, restored factory exact-set coverage, a separately evaluated safe
abstention/skip rule and an agreed latency budget. Any new experiment must
register new settings/data rather than overwrite these observations.

## Evidence and retained testbed state

`baselines\provenance.json` remains the frozen source truth. Original source
evolution is reported by `.artifacts\source-drift.json`, not merged into captures.
README documents commands for source-fit, parity, live adapters and gate checks.
The initial report/evidence remains under `.artifacts\round-1`.

Report-four verification passed **60 testbed cases**, snapshot hash checks and
Ruff. Governance/model work has **67 base cases** plus separately exercised
native MAF/lease/gateway cases, a live strict Responses validation,
all three selected skill loads and a fresh native bridge control. Governance
dependencies are pinned in the separate MAF environment. The previously recorded
**53 frozen factory baseline cases** are unchanged; current provider/bridge
selection parity was rerun. Outputs include
`.artifacts\testbed.xml` and `.artifacts\factory-baseline.json`. The historical
manifest and all ten pinned instruction hashes match the pre-change evidence.

Live registry state includes general unpromoted versions 2/3 and the privileged
deploy-only composition, certificate identities and owned IAM objects above.
General default 1, all original skill pins and the .NET proposal/pin remain
unchanged. The active mock is the secured ACA service; the tunnel and its owned
local processes are retired. `.artifacts\governance\deployment.json` inventories
the new resources, including the two pinned model deployments. `mocks\README.md`
documents scope and cleanup. No original checkout,
deployed worker, front-door preview, Toolbox default or existing skill pin was
changed by this work.
