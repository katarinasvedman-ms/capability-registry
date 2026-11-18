# H9b: AI Gateway view + exact-call verifier

## Result and changes since H9

**Supported for the two measured synthetic writes, using path B.** The clean
caller has no Foundry/Toolbox grant. Its actual pinned MAF client reaches
`create_ticket` through AI Gateway, the reference verifier, general Toolbox v3,
and the secured mock. Release reaches privileged Toolbox v1 through the same
profile. Both pass H6 tests 1-6, including both mutation points and replay.

The real hook supplies arguments, but **not the incoming lease header or the
original caller identity**. Path A cannot verify this header-carried lease with
that observed payload. Reserved-argument carriage works on path B; isolated
header carriage does not. This is not a native-verification result.

Paired ACA measurements are complete. With reserved-argument carriage on both
providers, added consumer elapsed p50/p95 is **+6.652/+185.654 ms** for ticket
and **-15.794/+84.832 ms** for deploy. These are **provider-profile differences,
not isolated AI Gateway processing costs**. Different Toolbox preflight paths
remain; the decomposition and limitations below are essential to interpreting
the numbers.

The authorized box began **2026-11-18 09:46:37.635 UTC**, with a fixed deadline
of **10:46:37.635 UTC**. Conformance completed at **10:00:25.451 UTC**; both
measurement runs completed by **10:05:45.098 UTC**. Final immutable/IAM controls
were captured at **10:07:02.549 UTC**. No stop condition fired.

Reused the H9 gateway, its ToolServers and managed identity, all three ACA apps,
Key Vault key, replay table, Foundry projects and immutable Toolbox versions.
Only one ARM role assignment was added: viewer on the general ToolServer.
The existing certificate identities and their grants were not changed.
The gateway and mock gained additive profile routes/verification support; the
old reference route remains operational and was the measured comparator.

The main hypothesis report, catalog binding bytes, defaults, old pins and .NET
proposal are unchanged. No promotion, shared Foundry network change, new
Foundry grant, new signing-key grant, or teardown was performed.

## Evidence, versions and reproducibility

Raw requests/responses are retained locally under ignored
`.artifacts\h9b\state.json`, including:

- `baseline` / `final`: Toolbox manifest bytes/hashes, default, binding/report
  hashes, original/current ARM role assignments, ToolServers and app configs.
- `viewer`, `created`, `viewer_assignments`: exact ownership, public identity
  metadata and the single scoped assignment.
- `hook_captures`: authenticated, actual hook HTTP headers and JSON body.
- `http`, `conformance`, `viewer_deploy_denial`, `hidden_tool_denial`:
  requests/responses, negative controls and mock/verifier deltas.
- `measurements`, `normalized_measurements`, `session_probes`, `final_events`:
  individual observations, client wire exchanges and matching server audits.
- `measurement_mock_checks`: **176 measurement leases, each accepted exactly
  once** by the mock, including warmups from both experiments.

Credentials, bearer leases and execution assertions are redacted from retained
HTTP/audit evidence. Serialized bodies are parsed before redaction. Private
certificate material is ACL-protected, ignored and outside the image context.
The appendix reproduces the observed hook JSON without inventing fields.

| Component | Pin / observation |
| --- | --- |
| Azure CLI | 2.93.0 |
| Gateway management | ARM REST `2026-11-01`, through that CLI |
| AI Gateway extension | H9's isolated `1.0.0b34`, unchanged; no new gateway SDK or extension installation |
| Microsoft Graph | v1.0 for the new tagged application/service principal |
| Python client stack | Existing `requirements-maf.lock`; MAF core 1.25.0, MCP 2.3.0, azure-identity 1.25.3, PyJWT 2.15.1, rfc8785 0.1.4 |
| Lease and execution signing | Existing immutable Key Vault RSA key version, RS256; REST 7.5 |
| Profile ACA image | `governance:20261118-h9b-profile`, digest `sha256:d6854af35ad4a2e0741c93829515cc6acd44261bda2e6f0376c1728fa45958e3` |
| Live revisions | Consumer/mock `--0000005`; gateway `--0000006` |

The [Learn overview][overview] still labels AI Gateway preview. The accepted
H9 architecture decision does not turn a documentation claim into an observed
feature, or establish GA status. Native CEL capabilities were not re-tested;
this spike's new evidence is the real hook contract and functioning verifier
hop.

Implementation: `tools\h9b_spike.py`, `tools\h9b_live.py`,
`governance\execution.py` and additive gateway/consumer/mock routes.
The live entry points require `--live` and reject work past the recorded box;
they are an experiment record, not an unattended provisioning loop.

Offline metric reproduction:

```powershell
.\.venv-maf\Scripts\python.exe -m tools.h9b_metrics
.\.venv-maf\Scripts\python.exe -m pytest -q tests\test_h9b_profile.py tests\test_governance_gateway.py tests\test_governance_lease.py tests\test_h9_spike.py
```

### 1. Clean caller

**Finding: supported.** Created `viewer-test`, a single-tenant application and
service principal with a certificate, no password and no Foundry grant.

| Identity field | Value |
| --- | --- |
| Client/application ID | `02737aef-9f3e-4d87-a0ca-579446169ef7` |
| Service-principal object ID | `ba0f0afe-a71c-44cf-ac52-4407640c628e` |
| Certificate expiry | 2026-11-19 09:51:56 UTC |
| Only ARM role | AI Gateway Tools User |
| Scope suffix | `aigateways/cr-h9-f3e5ad02-aigw/workspaces/default/toolServers/general` |

**Evidence:** repeated before and after the integration:

| Request as viewer | Observed |
| --- | --- |
| General gateway `tools/list` | 200, exactly `get_build_status` and `create_ticket`, with their advertised prefixes |
| Privileged gateway `tools/list` | 403 |
| General Toolbox v3, direct `tools/list` with an AI-audience token | 403; response body empty |
| Privileged gateway `tools/call deploy_release` | 403; zero verifier requests and zero mock requests |
| Hidden general `tools/call order_hardware` | 400; zero verifier requests and zero mock requests |

These are Entra application tokens, not gateway keys. Gateway acquisition uses
`https://ai.gateway.azure.com/.default`; direct Toolbox/consumer acquisition
uses `https://ai.azure.com/.default`. The empty direct-403 body is retained as
such, not replaced with an invented authorization explanation.

This proves lack of Toolbox authorization for this new caller, **not network
unreachability** of the public Toolbox endpoint. Coder/release retain their
old direct grants for comparison; the gateway is not literally the only
Foundry User principal in the entire existing testbed.

**What would change the answer:** an inherited/new grant allowing a direct
viewer Toolbox call, a tool outside its gateway view being usable, or a
deployment where clean callers cannot acquire/use gateway Entra tokens.

### 2. Actual Agent Hook contract

**Finding: arguments are present; the lease header and original caller are
absent. Path B selected.**

Installed a temporary `preToolCall`, `failClosed`, managed-identity hook on the
existing general ToolServer. The capture endpoint authenticated the gateway MI
and always returned 403; it never authorized a call. Sent a real approved MAF
lease in `X-Capability-Lease`, with task, approver, marker and MCP metadata.

At **09:52:50.403 UTC** the gateway POSTed a 575-byte
`agent-hooks/0.1` context. It contained `tool_call.name`, `tool_call.args`,
`target`, an agent identifier, session identifier and trace/correlation fields.
There was **no body headers collection, original caller oid, lease, incoming
task/approver header, or incoming MCP metadata marker**.

Hook HTTP authorization authenticated object ID
`ec22ab82-d83b-4977-8e0a-d93101ae402e`: the AI Gateway MI, not release-test.
Hook transport headers included gateway-generated correlation/request IDs and
`x-ms-apim-agent-hooks-recursion: 1`; they did not include the caller's lease.
The session ID equalled the generated request correlation ID; this does not
prove a persistent agent conversation identity.

The gateway returned HTTP 200 with an MCP error on hook rejection. The capture
policy was removed before the provider was enabled. No hook timeout injection
was run: path A was ruled out by the missing lease, before implementing an
allowing interceptor. A hook timeout/availability SLO is not claimed.

**What would change the answer:** a documented, observed hook field carrying
the original signed lease and a reliable request/context contract. Arguments
alone are insufficient. Moving a lease into arguments merely to deliver it
to a hook would be a different experiment from the requested header path A.

### 3. Verifier hop

**Finding: supported, path B with reserved-argument carriage.**

The two existing ToolServers now point to additive routes on the existing
reference gateway:

```text
general    -> <reference-gateway>/h9b/mcp/general
privileged -> <reference-gateway>/h9b/mcp/privileged
```

Backend authentication remains managed identity, resource
`https://ai.azure.com`. Namespaces remain `general` / `privileged`; the general
allow-list and caller role assignments are unchanged. The unsuccessful H9
`reference` ToolServer remains an unused probe, not a third working provider.

The verifier validates the Entra token and allows exactly the gateway MI as
trusted upstream. It sees that MI object ID, **not the original caller**.
The original clean caller is taken from the **signature-verified lease subject**.
Unverified claims only select the expected verification context; they do not
authorize dispatch. The verifier checks RS256/key version, expiry, contract
version, immutable profile pin, RFC 8785 input hash, caller subject, task,
approver and a durable atomic single-use nonce.

Authorization binds to the **lease**, not to the transport MI. No OBO or caller
impersonation is performed. The already-authenticated gateway MI bearer token
is used unchanged on the Toolbox hop, where that MI has the existing grant.
This is trusted bearer-token forwarding, not delegation to the lease subject.

The new profile pins general tool execution to v3 and privileged execution to
v1. These are separate runtime profile bindings: the existing
`catalog\bindings.json`, legacy v2 binding and its lease path are unchanged.
Profile middleware receives those profile bindings; the `LeaseMiddleware`
implementation and the lease claim format are unchanged.

An isolated header-only request sent the lease but the verifier did not
receive it: MCP error, zero mock requests. The working client therefore carries
`_capability_lease` as a reserved argument. The verifier strips it **before**
schema/canonical-input comparison or Toolbox forwarding. The gateway's new
backend schema declares that carrier; original Toolbox schemas were not changed.

**What would change the answer:** loss/rewriting of the reserved argument,
inability to forward an AI-audience gateway MI token, non-atomic replay state,
or a conformance failure. Native header forwarding would remove this carrier
limitation, but would still require an observed client/session test.

### 4. Execution trust chain

**Finding: supported without granting a new identity signing privileges.**

```text
caller Entra identity
  -> AI Gateway ToolServer RBAC + filtered view
  -> reference verifier authenticates gateway MI and verifies exact lease
  -> existing consumer Key Vault signer issues execution assertion
  -> verifier calls pinned Toolbox as the gateway MI
  -> mock verifies lease AND verifier-path execution assertion
```

The reference gateway previously had only public-key access, not signing
permission. Rather than widening its grant, it uses an authenticated internal
endpoint on the **existing consumer signing authority**:
`POST /h9b/execution-assertions`. That endpoint accepts only the reference
gateway's management-audience MI proof, independently verifies the same lease
and policy, and signs a separate, at-most-30-second execution assertion through
the consumer's existing Key Vault Crypto User permission.

This extra signing RPC is measured, not hidden. Lease issuance remains in MAF
middleware; this endpoint issues an **execution assertion**, never an execution
lease. No private signing material is downloaded or embedded in configuration.

The execution assertion has type `execution+jwt`, issuer
`urn:capability-registry:verifier`, audience
`urn:capability-registry:mock-execution`, and binds the complete lease digest,
lease nonce, identity, task, approver, capability/version/pin and inputs hash.
Its expiry cannot exceed the lease's expiry. Lease and execution assertions
share the existing Key Vault key but have strict, distinct type/issuer/audience.

The mock independently verifies both JWTs and consumes the original lease
nonce atomically in its own partition. It does **not** trust the AI Gateway MI
by itself. A live direct mock request with a valid viewer lease but no execution
assertion returned 403, accepted false. Focused tests also reject a lease or
Entra token substituted for an execution assertion.

The legacy reference route still uses its existing Entra execution assertion
and works unchanged. The new proof is accepted independently of which frontend
delivered a call, provided its lease, exact inputs and verifier signature match.

**What would change the answer:** an assertion accepted without its exact
lease/input binding, acceptance of gateway MI alone, or unauthorized access to
the signing endpoint. Production should evaluate a dedicated assertion key and
signer boundary; no such grant/key change was authorized for this spike.

### 5. H6 conformance

**Finding: supported for both writes.** All rows have raw HTTP and before/after
mock evidence. Ticket uses viewer-test; deploy uses release-test.

| Test | Ticket | Deploy | Mock requests for rejected attempt |
| --- | --- | --- | --- |
| 1. Exact approved inputs, actual MAF client | 200, one accepted call | 200, one accepted call | Not a rejection |
| 2a. Mutation after middleware, before MCP client | Consumer 403 | Consumer 403 | 0 |
| 2b. Mutated gateway body with matching original lease replayed | MCP error | MCP error | 0 |
| 3. Reuse an already successfully consumed lease | MCP error | MCP error | 0 |
| 4. Expired, correctly signed lease | MCP error | MCP error | 0 |
| 5. No lease | MCP error | MCP error | 0 |
| 6. Incomplete synthetic approval | Consumer 403 | Consumer 403 | 0 |

For rows 2b-5 the outer gateway response is HTTP **200** with
`result.isError: true`; backend denial is not a successful tool call. The
verifier's captured audit records 403. The positive replay-setup invocation
used the same original lease/body successfully once before the second call
was rejected. An invalid body did not consume a valid lease.

Viewer's privileged attempt returned gateway 403 before either verifier or
mock, and the hidden general tool attempt also stopped at the view boundary.
The mock's separate missing-assertion negative control deliberately contacts
the mock; it is not counted as an H6 "rejected before mock" result.

**What would change the answer:** either mutation or a replay reaching the
mock, any negative control being dispatched, viewer escaping its view, or
failure with a different supported client/transport. This is a bounded
synthetic conformance result, not a production adversarial certification.

### 6. H8 measurements and sessions

**Finding: measured; component attribution is qualified.** Same ACA consumer,
operations, arguments and release identity on both providers; two warmup pairs
then 20 alternating-order measured pairs per tool. Each provider invocation
gets a fresh independently signed lease. All measured lease IDs were reconciled
to exactly one accepted mock call.

The initial run used each profile's default carriage (reference header,
AI Gateway reserved argument). Because that confounds MCP reconciliation cost,
a second, explicitly **carriage-normalized** run used reserved arguments on
both. Both raw experiments are retained, not overwritten.

Primary normalized results, milliseconds, p50 / interpolated p95:

| Metric | create_ticket | deploy_release |
| --- | --- | --- |
| Reference consumer elapsed | 478.141 / 542.037 | 481.867 / 554.644 |
| AI Gateway + verifier consumer elapsed | 482.120 / 629.967 | 462.619 / 545.534 |
| Paired added consumer elapsed | **+6.652 / +185.654** | **-15.794 / +84.832** |
| Reference MCP tools/call dispatch | 347.472 / 395.815 | 354.274 / 430.075 |
| AI Gateway + verifier MCP tools/call dispatch | 328.993 / 396.779 | 324.983 / 388.780 |
| Paired added dispatch | -23.865 / +48.038 | -30.315 / +50.222 |

AI Gateway profile decomposition, milliseconds, p50 / p95:

| Component | create_ticket | deploy_release |
| --- | --- | --- |
| MAF policy | 0.008 / 0.009 | 0.008 / 0.012 |
| Lease canonicalisation | 0.136 / 0.180 | 0.125 / 0.199 |
| Lease Key Vault signing | 45.385 / 68.500 | 41.208 / 61.739 |
| Gateway/network/MCP residual, dispatch minus verifier residence | 29.385 / 32.038 | 27.518 / 49.565 |
| Verifier residence, inclusive | 299.829 / 366.797 | 297.946 / 343.223 |
| Verifier signature/hash/replay check | 7.345 / 10.026 | 7.456 / 8.767 |
| Execution-assertion signer RPC, inclusive | 48.762 / 79.699 | 46.948 / 72.502 |
| Execution-assertion Key Vault signing, inside that RPC | 40.196 / 72.118 | 39.534 / 64.653 |
| Toolbox leg, inclusive of mock | 229.353 / 292.074 | 224.553 / 279.454 |
| Toolbox leg minus mock residence | 221.125 / 283.846 | 215.910 / 271.913 |
| Mock verification | 8.147 / 10.644 | 8.319 / 9.401 |
| Mock synthetic business dispatch | 0.076 / 0.096 | 0.077 / 0.110 |
| Mock total residence | 8.338 / 10.828 | 8.568 / 9.620 |

Reference normalized verification was 8.181 / 10.121 ms for ticket and
7.948 / 14.304 ms for deploy. Its gateway residence was
338.988 / 387.034 and 346.397 / 422.110 ms respectively; its external MCP
residual was 8.222 / 10.625 and 7.743 / 8.875 ms.
Complete per-path policy/canonicalisation/signing/backend numbers are in
`.artifacts\h9b\latency.json`.

**Interpretation boundaries:**

- Inclusive nested spans and independently computed percentiles do not add up.
  The gateway/network/MCP residual is **not pure gateway CPU or an isolated
  gateway-hop RTT**. Provider-internal telemetry would be needed for that.
- Reference `Registry.execute` also performs live pinned-manifest/tool/schema
  checks; the new fixed-view verifier calls its verified immutable binding
  directly. Ticket uses the retained legacy v2 reference pin versus the new
  profile's required v3 pin. Both target the same synthetic tool implementation.
  These differences prevent treating negative paired values as evidence that
  adding AI Gateway makes an otherwise identical path faster.
- Consumer elapsed includes its client construction/discovery and middleware;
  dispatch isolates the actual tool-call span. External client timing is also
  retained, but is not the ACA-only primary metric.
- The default-carriage run's paired elapsed differences were ticket
  -58.308 / +58.164 ms and deploy -87.157 / +52.248 ms. Normalizing carriage
  matters; that first run is not silently selected as the better headline.
- One replica, warm steady state, 20 measured pairs, no load/availability target
  or general performance claim. Cold initialization is excluded by warmups.

**Session observations:** two calls through one pinned
`MCPStreamableHTTPTool` instance, with different valid leases:

| Client carriage | Observed initialize requests | Successful calls | Returned MCP session IDs |
| --- | --- | --- | --- |
| Stable headers, changing reserved lease argument | 1 | 2 | None |
| Changing lease header, also carrying the working reserved argument | 2 | 2 | None |

The second case deliberately also carries the reserved lease: it is **not**
proof of successful header forwarding. It shows the pinned MAF client
reinitializes when its effective headers change. That is a client observation,
not proof that AI Gateway itself reconciles sessions. No persistent gateway
session is inferred from absence of a session ID.

**Names:** client-visible
`general_capability-registry-mock-f3e5ad02___create_ticket` and
`privileged_capability-registry-mock-f3e5ad02___deploy_release`.
AI Gateway adds `general_` / `privileged_`; the verifier maps the remaining
Foundry advertised name exactly; Toolbox strips its server namespace before
the mock receives `create_ticket` / `deploy_release`. No fuzzy/suffix-based
authorization mapping is used by the verifier.

**What would change the answer:** identical transport/preflight paths,
provider-internal spans, larger concurrent samples and supported persistent
session/header behavior. None is needed to interpret the measured bounded
profile costs, but each is needed for stronger production latency claims.

## Accepted limitations and retained state

- Foundry User breadth remains on the backend MI and old comparison identities.
  The new caller has no Toolbox grant; old callers' direct routes were not revoked.
- Approval is synthetic; Python only; no human approval UI, .NET parity or
  .NET pin promotion.
- General v2 remains publicly reachable to identities that already have a
  grant. No shared Foundry networking/identity change, stale-version retirement
  or private sole-ingress closure. The dev tunnel remains retired. Stale
  `server_url` text in immutable Toolbox response metadata is not the live
  mutable connection target; the call/audit evidence reaches ACA.
- App-only gateway `/.default`, not a delegated `Data.All` experiment. Separate
  ToolServers provide caller exposure profiles, not per-caller filters within
  one shared server.
- No native gateway lease verifier; no usable lease-bearing header in the
  observed hook; no header-only lease carriage through the new backend.
- The signing authority is the existing consumer MI. Execution assertions
  share the existing lease key with distinct cryptographic contexts and add
  a measured signing RPC; signer/key separation was not authorized.
- Leases are short-lived bearer capabilities. The verifier sees gateway MI,
  not the original transport caller; it authorizes the signed lease subject.
  It does not claim cryptographic end-to-end binding to a forwarded caller token.
- The new hop is conformance-tested for the two leased writes only. Although
  `get_build_status` is listed in the general view, unleased read execution
  through the new hop is not implemented; its existing reference route remains.
  No A2A or FoundryToolbox-client expansion was attempted in H9b.
- Existing one-replica, bounded in-memory evidence, short-lived test
  certificates, replay-ledger cleanup and incomplete distributed telemetry
  limitations remain. The consumer is a deterministic nonstreaming fixture,
  not a deployed Foundry hosted agent. Existing H5 limitations remain.

Final controls preserve all captured Toolbox manifest bytes/defaults and
binding/report hashes. The subscription snapshot went from **174 to 175**
assignments; all 174 original assignment records are identical. The only new
assignment is viewer's general ToolServer grant. Caller certificates were still
valid and did not need renewal.

`scripts\teardown-testbed.ps1` includes viewer's exact application/SP/assignment
with tag/scope/principal checks, followed by the H9 and existing governance/H4
cleanup. The existing owned registry covers the two H9b image tags; existing
owned apps cover their new routes/configs. It was parsed, **not executed**.
The live provider and comparator remain running.

## Appendix: exact observed hook fields for the PM

Captured JSON body:

```json
{
  "spec": "agent-hooks/0.1",
  "interception_point": "pre_tool_call",
  "timestamp": "2026-11-18T09:52:50.403Z",
  "sequence": 0,
  "agent": {
    "id": "toolserver:general",
    "framework": "microsoft_azure_aigateway"
  },
  "session": {
    "id": "f76b54ab-6d7f-4227-ac05-96d13ff2e360"
  },
  "tool_call": {
    "id": "91",
    "name": "general_capability-registry-mock-f3e5ad02___create_ticket",
    "args": {
      "title": "H9b real hook payload fixture"
    }
  },
  "target": {
    "title": "H9b real hook payload fixture"
  },
  "request_id": "f76b54ab-6d7f-4227-ac05-96d13ff2e360",
  "trace": {
    "trace_id": "c9da765f103508ff08a02560c95dc1b9",
    "span_id": "0c6d80ed5dabb55d"
  }
}
```

| Needed context | Observed field / absence |
| --- | --- |
| Tool name | `tool_call.name` |
| Arguments | `tool_call.args`; duplicated in `target` |
| Original request headers | Absent from JSON |
| Lease header | Absent from JSON and hook HTTP headers |
| Original caller identity | Absent; hook transport authenticates gateway MI |
| Agent identity | `agent.id = toolserver:general`, framework string |
| Session identity | `session.id`, equal to this request's correlation ID |
| Correlation | `request_id`, `trace.trace_id`, `trace.span_id` |
| Caller MCP metadata | Submitted marker absent |

Gateway-generated hook headers included:

```text
content-type: application/json
accept: application/json
content-length: 575
x-ms-apim-agent-hooks-recursion: 1
x-ms-correlation-id: f76b54ab-6d7f-4227-ac05-96d13ff2e360
x-ms-request-id: 8a56f80f-76de-40e2-a43c-0531e26f7b79
authorization: [REDACTED gateway-managed-identity bearer]
```

ACA ingress forwarding/infrastructure headers are retained in the raw capture,
not mistaken for caller headers. **PM ask:** a documented allow-listed incoming
lease-header/context facility and authenticated original caller context, with
timeout/fail-closed guarantees. That would enable a real path-A evaluation;
H9b does not infer those fields from documentation or invent an allowing verdict.

## Provider-profile recommendation

| Provider | What it gives | Measured latency / cost | What it cannot currently do |
| --- | --- | --- | --- |
| **AI Gateway + verifier (path B)** | Clean-caller ToolServer RBAC/views, MI backend access, exact-call lease verification, durable replay rejection and independently checked execution assertion; measured H6 pass | Normalized added consumer elapsed: ticket +6.652/+185.654 ms; deploy -15.794/+84.832 ms p50/p95 versus the retained reference profile. Extra assertion RPC about 47-49 ms median, inclusive of signing. Residual gateway/network/MCP about 28-29 ms median, not pure gateway processing. | No native lease verification, usable header-only carriage, original-caller transport proof, production approval UI, private sole-ingress closure, unleased-read implementation on this new hop, or isolated apples-to-apples gateway overhead claim |
| **Reference gateway only** | Existing tested H6/H7 lease enforcement, read exemption, A2A and Python MAF integration; no new gateway dependency | Normalized consumer elapsed: ticket 478.141/542.037 ms; deploy 481.867/554.644 ms p50/p95 in this run. No AI Gateway/signing-helper hop for its legacy execution assertion. | Current original-caller forwarding still requires that caller's Toolbox grant. It does not provide platform ToolServer RBAC/catalog/private ingress or close direct public Toolbox routes by itself. |

[overview]: https://learn.microsoft.com/azure/ai-gateway/overview
[hooks]: https://ai.gateway.azure.com/docs/policies/agent-hooks
