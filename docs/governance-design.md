# Governance implementation decisions

Report-four resources, versions, names and grants remain the baseline.
H6-H8 require new live evidence; local tests alone do not support them.
H5 must not begin before H6/H7 have been reported and safety stops cleared.

## Policy interface and AGT assessment

The public AGT repository was inspected at
`5bcf1daa98f1ff1d44c57e9e969b7ded0546f0b2`:

- [MAF integration](https://github.com/microsoft/agent-governance-toolkit/blob/5bcf1daa98f1ff1d44c57e9e969b7ded0546f0b2/agent-governance-python/agent-os/src/agent_os/integrations/maf_adapter.py)
- [Policy-engine split](https://github.com/microsoft/agent-governance-toolkit/blob/5bcf1daa98f1ff1d44c57e9e969b7ded0546f0b2/docs/packages/agent-control-specification.md)

Its capability guard is genuine MAF function middleware, but requires MAFKernel,
the native AdapterRuntime and AdapterExecutionState, plus native intervention
snapshots/transformation handling. It is not a drop-in tri-state
`evaluate(call)` engine. Its documented compatibility SDK is distinct from
upstream `agent-control-spec`; the documented published compatibility wheel
targets the older engine. Substituting either by name would not establish fit.

This phase uses the permitted deterministic fallback behind `Evaluator`.
The version-one YAML policy references contract status/lifecycle, permissions,
operation, approval requirement and trust tier, never individual tool names.
This keeps the existing dispatch adapter intact rather than introducing AGT's
kernel/runtime wrapper and argument transformations into the lease boundary.
AGT is **not installed or claimed as tested**. The used evaluator is pinned
as in-repository policy version 1; runtime package versions remain locked.
An independently tested native-engine adapter can replace it behind the interface.

## Lease semantics

`rfc8785==0.1.4` canonicalizes JSON after input-schema validation. Object order,
JSON formatting and equivalent number encodings normalize; meaningful string
whitespace is preserved. Non-finite/unsupported numbers fail closed.
JWT signatures bind capability, contract version, immutable Toolbox/member/
tool pin, canonical inputs hash, task, caller identity, approval and short
validity. An unchanged semantic version cannot redirect an existing lease to
a different composition. The signing adapter requests Key Vault RS256 signing
of a SHA-256 digest; no private key is downloaded. Test keys are generated
ephemerally inside tests, not stored in code/config.

The pure verifier checks signature, exact key version/algorithm, issuer,
audience, time and all bound fields. Atomic durable single-use consumption
uses an Azure Table insert in the independent gateway; a pure verifier alone
does **not** prevent replay. The mock verifies independently and consumes a
separate nonce partition. Only nonce/expiry, not inputs or credentials, are stored.

The native function middleware stops before dispatch on pending approval/deny.
Approval is trusted synthetic host context, not a model argument or human UI.
The host must bind approval to the exact call before constructing that context.
MAF/core remains pinned to 1.25.0. That installed release exposes `Agent`,
not `ChatClientAgent`; a reference consumer must identify this API naming
difference rather than claiming the unavailable class was instantiated.

## Actual carriage and hosting

The actual pinned `MCPStreamableHTTPTool` exposes `header_provider`.
Its documented implementation resolves host runtime kwargs again for generated
tool calls, supports invocation middleware-provided values, and binds headers to
the MCP session. ContextVar lease carriage is isolated per asynchronous call.
H7 now records the actual ACA client-to-gateway exchange in both header and
runtime-only reserved-argument modes. The gateway strips reserved data before
schema validation and forwarding. A leased A2A handoff is separately recorded.
The low-level MCP ClientSession.call_tool has no per-call HTTP-header parameter;
these are different client paths and must not be conflated.

The consumer uses the pinned native `Agent`/`FunctionInvocationLayer`, not
an unavailable API name. `/responses` is a deterministic nonstreaming fixture;
standard Responses input/metadata and response-model validation are exercised.
`/skills/{name}` uses the shared provider/core for three selected skill pins,
without leases. ACA runs all three apps on 8088 with distinct managed identities.
Graceful SIGTERM, restart and persisted HTTP spans are measured. Foundry hosting
is a future deployment stub only; no Foundry hosting path is provisioned.

## Accepted reachability boundary

A proxy cannot revoke direct Azure authorization. Coder retains general-project
Foundry User; general v2 remains addressable, and the shared Foundry account has
public endpoints. A new gateway by itself cannot make that endpoint accept only
the gateway identity. This is separate from rejecting a lease at the gateway.

Account network restrictions could enforce a gateway-only path, but would also
affect the original factory that shares this account. Its existing ACA environment
has no VNet. Such a restriction must not be silently applied to shared resources.
Removing the existing coder grant would contradict the prescribed baseline.
The user's revised test deliberately leaves v2 reachable and instead requires
the mock to authenticate the gateway **and** verify an exact lease.

Only the two owned RemoteTool connections' mutable targets changed. A live
probe established that invocation follows that target, not the historical
immutable server_url annotation. No Toolbox version/default/pin, shared network
or existing grant changed. The secured mock identity/lease gate became live at
2026-11-17T09:51:18.036208Z; the tunnel was deleted at 09:52:23.275913Z.
General v2 discovery stays 200; its direct unleased deploy now gets downstream
403, wrapped by Toolbox in HTTP 200/JSON-RPC error -32003.

Original caller AI-audience tokens are forwarded unchanged to Toolbox; no OBO
exchange is claimed. Gateway MI assertions travel in trusted MCP metadata and
are verified against tenant/audience/signature/exact object ID by the mock.
Metadata-only discovery is public so the existing unauthenticated RemoteTool
connections can initialize/list; executable calls are protected.
Gateway identity is required for every execution; signed leases are additionally
required when policy marks the call side-effecting. The live read-only
`get_build_status` path returns 200 with no approval/lease and one mock call,
recorded in `.artifacts\governance\read-tool.json`. Skills likewise remain
selection-controlled and unleased.

## H8 security-preserving comparator

The bounded release-only control preissues a lease and completes gateway
signature/atomic-use verification in an untimed preparation request. Timed
dispatch pops a one-use prepared context and rechecks every binding/hash/expiry
before contacting the mock. Restarts/cache loss fail closed. Independent mock
identity/lease/nonce enforcement remains in both modes. This is not a no-proxy
or unsecured baseline, and no acceptable-overhead target is invented.

Physical gateway transit is common; native MCP dispatch minus gateway residence
is reported as a transport/session residual, not pure network RTT. Header
changes reconcile MCP sessions. Policy, JCS/hash, remote signing and gateway
verification timings are measured independently; percentile components do not sum.
See the report and `.artifacts\governance\latency.json`.

## Selection is not execution approval

H5 adds `/selection` with server-supplied policy ceilings and metadata-only
static/BM25/model modes. Static is the default; explicit model mode uses the
pinned nano deployment. The model never sees bindings/bodies and cannot return
IDs outside the eligible top-k or drop explicit required capabilities.
A selection response performs no tool call. The reference client then supplies
exact inputs and a separate synthetic approval to `/responses`; the existing
MAF lease path remains unchanged. This two-step fixture is not a conversational
or production approval workflow. Nano's factory regressions and both frozen
experiments remain findings, not silently corrected labels or tuning inputs.

## Future hosting stub and APIM mapping (not implemented)

The existing image is parameterized by `SERVICE`, `GOV_CONFIG` and identity
configuration; `SERVICE=consumer` exposes 8088, `/readiness`, nonstreaming
`/responses` and SIGTERM handling. A future Foundry hosted deployment would
reference this image digest, supply its independently authorized identity and
configuration, and separately verify protocol/identity/network compatibility.
No Foundry deployment descriptor, hosted resource or compatibility proof is
claimed. The measured target remains ACA.

For a future APIM ingress, map Entra authentication/allowed routing to JWT
validation and a fixed pinned-backend allowlist. Preserve the lease header or
strip the reserved argument before downstream schema validation. Keep exact
JCS/signature/context verification and atomic nonce use in a dedicated verifier
backend unless equivalent APIM behavior is independently proven; an APIM cache
is not assumed to be a durable single-use ledger. Preserve the original caller
token and use a separately authorized gateway execution identity for the mock.
Correlate lease/audit IDs and reject before forwarding on every failure.
Project grants/private ingress still govern reachability; APIM policies alone
do not revoke direct Toolbox access. No APIM resources or policies were created.
