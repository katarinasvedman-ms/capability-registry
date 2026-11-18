# H9: Azure AI Gateway provider spike

## Result and boundary

**Partial. Keep the reference gateway as the working provider.** Toolbox
federation with managed-identity backend authentication and Entra caller access
works. This is **not** a Q1 managed-identity federation failure. A successful
leased execution through AI Gateway was not established: the unchanged mock
trusts the reference gateway's execution assertion, not the new gateway.
The same middleware-issued lease and inputs succeeded through the reference
gateway, with exactly one accepted mock call.

Neither native lease verification nor a working verifier-hop integration was
demonstrated. Consequently, the new-path H6 matrix and governed latency comparison
remain **unexecuted**, not passed by counting upstream errors as policy enforcement.
No mutated-input experiment was sent through an unverified configuration.
No mutated-input stop condition was triggered; that is not evidence of resistance.

This is the bounded spike on `spike/ai-gateway`, not an accelerator deliverable.
The experiment was bounded to 45 minutes from 2026-11-18 08:26:02 UTC; no expansion
into a new credential/protocol bridge or production gateway was undertaken.
The main hypothesis report is unchanged.

## Versions, evidence and owned state

| Component | Exact observation/pin |
| --- | --- |
| Azure CLI | 2.93.0 |
| `aigateway` extension | 1.0.0b34; installed in the ignored, task-isolated extension directory |
| Official wheel SHA-256 | `f4f1c509ccf0e8c5f135604f318bc60077eb14b4cef9036456f266874b9c5c86` |
| ARM API | `2026-11-01`, explicitly selected |
| Runtime initialize | `Azure AI Gateway`, version `1.0.0`; protocol `2025-03-26` |
| AI Gateway SDK | None; pinned CLI, existing `httpx==0.28.1` and `azure-identity==1.25.3` |
| Public CLI documentation | [Azure/aigateway revision `18803de4737bfb046d67e61e7bc307f0f710e5fe`][cli] |
| Public portal bundle | `index-2j_b9SrK.js`, SHA-256 `c6f8075eb3d8a5fde1d18aad6a418cf9d7edf3328a4a5881b4e47693149f9d12` |

The user supplied a GA date. The inspected [Learn overview][overview],
[concepts][concepts], [comparison][comparison] and pinned CLI README still label
the product/CLI **preview**. The stable ARM API was actually available and the
resource provisioned successfully. Those observations do not independently
establish GA status. Agent hooks and gateway private endpoints also have their
own explicit preview qualifications.

Created one Standard, system-assigned-identity gateway,
`cr-h9-f3e5ad02-aigw`, in `factory-skills`, Sweden Central. Its runtime hostname
was taken from ARM, not constructed:

```text
cr-h9-f3e5-iilzfhpstysrc.swedencentral-01.ai.gateway.azure.com
```

Its three owned ToolServers are `general`, `privileged`, and the unsuccessful
`reference` integration probe. There are six new role assignments: two
project-scoped Foundry User assignments for the gateway identity, and four
ToolServer-scoped AI Gateway Tools User assignments for the existing callers.
No pre-existing assignment was widened or replaced.

The gateway carries `purpose=capability-registry-testbed`,
`owner=katarina.svedman`, `run=f3e5ad02`, `phase=h9`. Child registrations and
role assignments carry matching ownership descriptions where native tags are
not available. Both expired test certificates were renewed by appending public
credentials to the same applications; private PEM files remain in their
existing protected, ignored locations.

Raw evidence is local and ignored, not published as credentials or repository
fixtures:

| Artifact under `.artifacts\h9` | Evidence |
| --- | --- |
| `state.json` | Command arguments/results, ownership, authentication configuration, `q1_probes`, `q2_probes`, ordered `q3_attempts` |
| `provider.json`, `help-*.json` | Actual resource-provider availability and pinned CLI interface |
| `baseline.json`, `final-controls.json` | Report/binding hashes, immutable live manifests/default, direct coder isolation, ACA readiness/images |
| `public-doc-assets.json`, downloaded public assets | Hashed CEL, hook, catalog and private-networking documentation/UI evidence |

HTTP request/response bodies, statuses and headers are recorded in those probe
arrays. Authorization, leases and gateway assertions are redacted; a redacted
body is not claimed to be byte-identical to the wire. The local tools are
`tools\h9_spike.py`, `tools\h9_access.py`, and `tools\h9_policies.py`. They require
an explicit `--live` and refuse cloud work after this run's deadline.

## Q1: Toolbox federation with managed identity

**Finding: supported for federation; partial for the requested positive write.**
The backend is a directly registered **remote MCP server**, not API Center or
an OpenAPI conversion. General uses the existing explicit Toolbox v3 URL:

```text
https://factory-skills-resource.services.ai.azure.com/api/projects/factory-skills/toolboxes/factory-skills/versions/3/mcp?api-version=v1
```

The CLI input uses `kind: mcp`, namespace `general`,
`transport: streamableHttp`, and:

```json
{"type":"managedIdentity","managedIdentity":{"resource":"https://ai.azure.com"}}
```

ARM readback represents this as
`authentication.managedIdentity.resource: https://ai.azure.com`.
The system identity initially received Foundry User on **general only**;
privileged access was added separately for Q2. No account-wide grant was used.

**Evidence:** `q1_probes` contains these actual HTTP exchanges:

| Operation through `/default/toolservers/general/mcp` | Result |
| --- | --- |
| `initialize` | HTTP 200 |
| `notifications/initialized` | HTTP 202 |
| `tools/list` | HTTP 200; three upstream tools, schemas retained |
| `tools/call create_ticket`, unleased | HTTP 200, MCP `isError:true`; downstream mock HTTP 403 |
| Same call with an actual middleware-issued matching lease and `_meta` context | HTTP 200, MCP `isError:true`; exactly one rejected mock attempt |
| Reference gateway control, same lease/caller/task/inputs | HTTP 200; exactly one accepted mock call |

Advertised names acquire an additional namespace, for example
`general_capability-registry-mock-f3e5ad02___create_ticket`.
The approved comparison's lease ID is
`0a49f6ac-c680-4a3d-9a55-c010969ba248`. Its reference result is synthetic ticket
`MOCK-TICKET-CB7ABECA3282`. The AI Gateway attempt lacked the trusted reference
execution assertion; the positive reference attempt included it. No assertion
was impersonated and no mock check was relaxed.

**What would change the answer:** a working verification/execution adapter that
preserves the original caller and the mock's existing trust boundary, followed
by successful positive and negative controls. The current evidence must not be
reported as inability to authenticate AI Gateway to Toolbox.

## Q2: Entra caller access and tool-level rules

**Finding: supported at ToolServer granularity; not a demonstrated per-caller
tool ACL on a shared federated server.**

Certificate client credentials requesting the specified
`https://ai.gateway.azure.com/Data.All` failed with **AADSTS1002012**:
client-credential flows require `/.default`. The successful probes used
`https://ai.gateway.azure.com/.default`, with Entra access tokens, never gateway
keys. They do not prove a delegated `Data.All` flow. The observed access-token
audience was the gateway resource's application ID
`18550318-e177-4205-a743-6816d6fcdc3f`.

The new gateway-only access profile is:

| ToolServer | Upstream | Gateway-visible tools | Caller role |
| --- | --- | --- | --- |
| `general` | General Toolbox v3 | `get_build_status`, `create_ticket` | Coder and release: AI Gateway Tools User on this child only |
| `privileged` | Privileged Toolbox v1 | `deploy_release` | Release only |
| `reference` | Existing reference gateway | Integration failed; not a usable execution route | Release only |

The `general` **gateway** allow-list hides `order_hardware`; its original Toolbox
v3 exposure remains unchanged. This is not a Toolbox pin/version modification.
The same two tools are exposed to both callers on that endpoint.

**Evidence:** the initial `q2_probes` shows coder general list 200, privileged
list/call **403**, and two deploy-name attempts through general rejected **400**:
the privileged namespace and the general-prefixed deploy name. Release lists
both servers with 200 and can submit the privileged invocation through the
gateway; it still fails at the unchanged downstream execution boundary.
A successful release deployment is **not** claimed.
The final read-only repeat also denied coder access to the `reference`
ToolServer with 403; release retained the independently observed upstream 502.

Temporary server-wide blocking was also tested. The initial wildcard probe
immediately after update still reached the downstream denial. A later exact-name
probe, after configuration propagation, retained the tool in `tools/list` but
returned HTTP 200 with MCP `isError:true`, text **"blocked by the gateway"** and
denial code `ToolNotAvailable`, for **both** callers. The block was cleared.
This proves the exact-name case, not wildcard enforcement or instantaneous
configuration convergence.

**What would change the answer:** documented and exercised caller-specific tool
rules within one ToolServer. Until then, separate ToolServers plus scoped RBAC
provide different exposure profiles, not one shared-endpoint per-caller tool list.
The existing broad direct Foundry grants are deliberately retained.

## Q3: Signed-lease verification

**Finding: no working AI Gateway verification path established.** Attempts were
ordered native expression, hook, then direct reference-hop integration. A later
documentation correction refined the native/hook probes without changing the
baseline. None is presented as an implemented exact-call verifier.

### 1. Native CEL

The initial fail-closed feature-discovery payload `type: cel` was rejected by
ARM with **400 ValidationError**, target `policies[0]`. This guessed discriminator
is **not** evidence that the product lacks CEL.

The current [in-product CEL reference][cel] does document
`properties.policies[].when: "@cel(...)"` and expression-valued fields on
specific policy types. A second, correctly shaped `ipFilter` probe used
`when: "@cel(request.body != null && tool.arguments != null)"` to investigate
the input access needed for canonical hashing. ARM returned **200 but omitted
`when` on readback**, retaining a plain deny policy. It was immediately restored
without treating this as conditional enforcement.

The documented CEL context exposes URL/headers, authenticated identity/claims,
tool name/method/protocol and response metadata. It does not expose the
canonical tool inputs or document a lease JWS verification/JCS primitive.
Expressions cannot make network calls, including fetching a Key Vault key.
An authenticated Entra JWT's claims are not validation of an arbitrary lease
header. No complete signature/expiry/body-hash expression was demonstrated.

### 2. Agent hook

The [pinned GitHub example's][oldhook] string endpoint/`authorization` shape failed service
validation at `hooks.preToolCall.endpoint`. The newer [in-product hook
contract][hooks] requires `endpoint: {"url": ...}` and `authentication`.
Using that wire shape, managed-identity authentication and `failClosed`, the
service accepted a pre-call hook pointing at the existing reference verifier.

An immediate call still followed the prior configuration. After leaving the
hook installed for propagation, the actual MCP response was:

```json
{"isError":true,"content":[{"type":"text","text":"Tool call blocked by a policy before execution"}]}
```

This demonstrates a fail-closed integration response, **not successful lease
verification**. The reference `/mcp` endpoint is not an Agent Hooks
`AgentContext`/`Verdict` interceptor and does not authorize the new MI as an
original caller. The documented hook payload omits agent/session identity and
does not document forwarding the incoming lease header/original caller.
Only allow/deny verdicts are supported, not argument-stripping transforms.
A proper interceptor and trusted context-carriage proof are still required.
The temporary hook was removed.

### 3. Reference verifier as a thin backend hop

Registered a separate `reference` remote MCP backend using managed identity
with the AI audience and the unchanged reference `/mcp` URL. Its `tools/list`
returned **502**, "An upstream tool server returned an invalid or unsuccessful
response." This is consistent with the existing verifier's explicit original-
caller allow-list and credential contract; the 502 alone is not a backend trace
proving its exact rejection reason.

A gateway-audience caller token cannot simply become the original AI-audience
token expected by the reference adapter. Granting the gateway Foundry User
does not solve that protocol/credential handoff. No token was stored as a
static backend credential, no original caller was impersonated, and no existing
verifier configuration/grant was widened.

**H6 status:** tests 1-6 for both writes, including the two mutation points,
replay, expiry, no lease and incomplete approval, were **not run through a
working AI Gateway candidate**. The matching-lease reference positive control
is not a substitute. Neither the hook's fail-closed error nor mock identity
rejections establish native lease enforcement.

There is already a concrete negative result for the unverified direct-federation
configuration: its unleased Q1 call reached the mock and was rejected **there**,
not before it. That configuration therefore does not meet H6's pre-mock
no-lease rejection criterion. It was not used for mutation experiments.

**What would change the answer:** verified native cryptographic/input context,
or an independently hosted hook/protocol adapter preserving trusted caller,
header/argument carriage, canonical inputs, approval and single-use semantics.
Then run the full new-path H6 matrix with an accepted positive control before
benchmarking. Lease issuance remains in the existing MAF middleware and Key Vault.

## Q4: Headers, sessions and latency

**Finding: partial observations; governed performance unmeasured.**

Initialize returned no MCP session ID. Successful discovery and subsequent
requests do not establish behavior across changing per-call headers, nor prove
the absence of upstream reconnection/reconciliation. The matching-lease probe
sent both the lease header and explicit MCP `_meta`; the mock saw the `_meta`
task/approval context. It does **not** isolate byte-for-byte header survival.
No new actual-consumer MAF header-provider/session-carriage proof is claimed.

| New AI Gateway governed measurement | p50 | p95 |
| --- | --- | --- |
| Added end-to-end latency over reference | Not measured | Not measured |
| Policy; canonicalisation/signing; gateway hop; verification | Not measured | Not measured |
| Additional reference-verifier hop | Not measured | Not measured |

There was no functioning governed candidate to benchmark fairly. Error-response
timings would not answer the question. Historical H8 added p50/p95
deploy **101.646/159.982 ms**, ticket **114.727/185.963 ms**, remain image-4
reference measurements against a secured pre-authorized comparator, not an
AI Gateway result or an unsecured/no-gateway baseline.

**What would change the answer:** a positive Q3 path, then actual pinned consumer
MCP-client header/session probes and paired ACA measurements with the H8
decomposition and a matching security baseline.

## Q5: Reachability closure

**Finding: feasible design conditionally, not a live isolation result.**
The [gateway private-networking documentation][private] distinguishes inbound
Private Link from outbound VNet integration. Gateway private endpoints require
preview approval; creating one does not disable public access. Outbound
integration is needed for private Foundry/MCP backends.

Production closure requires restricting the **backend** as well as gateway
ingress: isolate Foundry so only the gateway's permitted network path can reach
it, restrict backend grants to the gateway identity, and route clients through
the gateway. A private endpoint on AI Gateway alone leaves a public Toolbox
route open. The [Foundry integration guidance][foundry] explicitly says
applications must use gateway endpoints for enforcement; selecting a
gateway-routed tool endpoint is not automatic revocation of direct access.

No shared Foundry network/identity setting was changed. No private-endpoint
deployment or access-enrollment change was attempted: it would not cheaply prove
backend isolation without crossing that boundary. Existing project isolation
was rechecked: coder direct privileged call remained **403**, while general v3
still advertised its original three tools.

**What would change the answer:** a dedicated isolated Foundry account and
approved gateway networking, followed by direct-backend denial and positive
gateway-route tests. No claim is made that this spike eliminated the built-in
Foundry User role-breadth limitation.

## Q6: "Discover approved assets" catalog

**Finding: a gateway asset view, not a demonstrated replacement contract
discovery provider. Reasoning only.**

The [Learn overview][overview] describes discovery/testing of models and tools.
Inspection of the public portal's `BrowsePage-Bpt-Niwf.js`, without executing
its JavaScript or running an interactive catalog session, exposes:

| Surface | Fields/filters visible in that implementation |
| --- | --- |
| Asset categories | All, favorites, models, MCP servers |
| Asset presentation | Display name, invokable/API name, description, provider/source, endpoint; MCP client auth and tool count where populated |
| Search | Name, description, provider, MCP auth label, API name |
| Facets | Provider, model capabilities, MCP authentication, tool source |
| Other controls | Name sorting, card/list view, grouping by provider/source/type |

These are UI/source observations, not a promise that every field is populated
or an authoritative authentication/approval policy. "Approved" catalog membership
does not implement our separate governance `status`, `kind`, `lifecycle`,
`risk_tier` or exact-call approval semantics.

The registered ToolServer supports a description, so a textual link to the
external contract could be carried there. Rendering as a clickable link and
arbitrary structured metadata retention/filtering were not tested. No native
independent kind/lifecycle/risk/examples filters were established. Model
capability facets are not the contract's discovery-kind field.

**What would change the answer:** a documented metadata extension plus readback,
search/filter and lifecycle audit-resolution parity against the common contract.
Until then, keep the registry authoritative; treat this catalog as a view or
build an explicit discovery adapter rather than silently dropping metadata.

## Preservation, teardown and accepted limitations

Final controls re-read general v1/v2/v3 and privileged v1: their complete
manifest snapshots and general default **1** match the previous final controls.
All ten skill pins, bindings and the main report's bytes are unchanged.
All three comparator ACA apps remain ready (200), on the original image-5
digest and separate identities; no app was redeployed. The one positive
same-lease control exercised the existing signer, verifier, audit and mock.
No Toolbox version was promoted; the .NET proposal remains pending.

`scripts\teardown-testbed.ps1` now covers the exact owned H9 gateway, its child
registrations and six new assignments before the prior governance/H4 set.
It checks ownership, pins the H9 management API versions, and was parsed only,
**never executed**. The gateway remains provisioned; private credential and
ownership artifacts must be retained for later cleanup.

All prior accepted limitations continue:

- Foundry User's broad within-project data actions remain on test identities
  and now the backend gateway MI; gateway caller RBAC does not revoke them.
- Approval is synthetic; no production human-approval UI/authority is claimed.
- Python only; no new .NET parity or pin promotion.
- General v2 remains directly reachable by design; no-grant/project split,
  stale-version retirement and gateway-only private backend ingress were not
  applied to that stale route. The dev tunnel remains retired.
- Immutable Toolbox compositions still use the previously measured mutable
  connection indirection and trusted `_meta` behavior.
- Original caller AI tokens and gateway MI assertions are distinct; no OBO
  delegation is claimed. Mock metadata discovery is public, execution is not.
- The consumer is a deterministic, nonstreaming reference Responses fixture,
  not a general conversational or Foundry-hosted agent.
- Existing one-replica, short-lived-certificate, bounded evidence/prepared-cache,
  replay-ledger cleanup and incomplete distributed-telemetry limits remain.
- H5's small frozen dataset, nano factory regression, selector/latency limitations
  and unchanged static baseline remain; H8 retains its secured-comparator limits.
- H9 additionally has app-only `/.default` rather than delegated `Data.All`,
  separate ToolServer exposure profiles, observed configuration propagation,
  documentation/wire-shape discrepancies, no verified new lease path, and no
  new-path H6/header/session/latency result. Preview/GA labeling is unresolved.

## Recommendation: accelerator gateway providers

| Option | What it gives | Latency cost | What it cannot currently claim |
| --- | --- | --- | --- |
| **AI Gateway native verification** | Observed MI Toolbox federation, Entra ToolServer access and server-wide allow/block; documented shared gateway controls | Not measured for a valid leased path | No demonstrated RS256/expiry/JCS-input/caller/replay verifier; no new-path H6 pass or production replacement |
| **AI Gateway + reference verifier hop** | Preferred next integration candidate: gateway access/reachability layer plus existing exact-call enforcement, if a trusted credential/protocol adapter is proved | Extra hop and decomposition unmeasured | Direct MI wiring to the unchanged reference endpoint failed; not a working deployed provider, not a measured header/session solution |
| **Reference gateway only** | Current working H6/H7 execution/lease provider, unchanged ACA comparator | Historical H8 deploy +101.646/+159.982 ms and ticket +114.727/+185.963 ms p50/p95 versus secured control, not a new-image result | No AI Gateway catalog/shared platform controls; cannot close public direct Toolbox reachability merely by being a proxy |

[overview]: https://learn.microsoft.com/azure/ai-gateway/overview
[concepts]: https://learn.microsoft.com/azure/ai-gateway/concepts
[comparison]: https://learn.microsoft.com/azure/ai-gateway/compare-api-management-service
[cel]: https://ai.gateway.azure.com/docs/cel-expressions
[hooks]: https://ai.gateway.azure.com/docs/policies/agent-hooks
[private]: https://ai.gateway.azure.com/docs/networking/private-endpoints
[foundry]: https://learn.microsoft.com/azure/foundry/configuration/enable-ai-api-management-gateway-portal
[cli]: https://github.com/Azure/aigateway/blob/18803de4737bfb046d67e61e7bc307f0f710e5fe/cli/README.md
[oldhook]: https://github.com/Azure/aigateway/blob/18803de4737bfb046d67e61e7bc307f0f710e5fe/skills/manage-ai-gateway/references/policies.md
