# Toolbox conventions

These are observed service transformations and consumer rules, not custom
Toolbox-version metadata. Raw observations are in `.artifacts\maf-live.json`
and `.artifacts\mock-publication.json`.

- Foundry advertises remote tools as `<server-label>___<tool-name>`. For the
  current server this is `capability-registry-mock-f3e5ad02___deploy_release`.
  Bindings preserve the exact advertised name; the contract retains its logical
  call spec. Never guess or strip the prefix when calling the pinned endpoint.
- Foundry adds the default JSON Schema annotation
  `"$schema": "https://json-schema.org/draft/2020-12/schema"`. Comparison
  normalizes only the equivalent missing/default annotation. Changed constraints
  or a different explicit dialect still fail.
- Resolve explicit numeric Toolbox and member versions only. A successful
  publication does not authorize promotion; promotion is a separate decision.
  General default 1, skill pins 1 and unpromoted composition 2 remain unchanged.
  Revised H4 uses an additional unpromoted composition 3 explicitly, with
  `allowed_tools` restricted to `get_build_status`, `create_ticket` and
  `order_hardware`. The privileged composition advertises only `deploy_release`.
- Toolbox `require_approval` is a floor. Contract `approval_required` can be
  stricter, never looser. A non-`never` explicit server requirement cannot be
  resolved under a false contract approval flag. Remote mock approval metadata
  is also checked; absence of a Toolbox setting is not evidence that the remote
  server enforces human approval.

  The service's [Toolbox documentation](https://learn.microsoft.com/azure/foundry/agents/how-to/tools/toolbox)
  states that `require_approval` is runtime-enforced: the MCP endpoint itself
  does not block `tools/call` while awaiting confirmation. This testbed therefore
  retains the original Invoker approval state machine; metadata alone is not
  treated as an approval enforcement mechanism.

The current version-2 MCP connection omits `require_approval`; its mock advertises
per-tool approval in `_meta.approval_required`. `create_ticket` permits calls
without remote approval, while the inherited front-door action envelope and
contract deliberately require it. `deploy_release` requires confirmation in
both the contract and mock. The historical H3/H4 mock checked approval-context
presence only. The H6 ACA replacement independently verifies gateway Entra
identity and a signed exact-call lease; approval itself remains synthetic.

Project RBAC and reachable tools must be tested independently of these contract
controls. A hidden/nondefault version is still addressable by its numeric pin.
An executable operation present in general is not isolated merely because
another copy or contract binding is marked privileged.

## Identity

A gateway governs **what a reaching call may execute**; reachability is governed
by project grants and, where required, network isolation. H4 proves grant-based
closure for the privileged endpoint, not revocation of general v2.

The authorized governance testbed deliberately retains the general v2 route.
Three production mitigations are **no grant (project split)**, **retire the stale
version**, and **a private endpoint with the gateway as sole ingress**. None was
applied here: coder's prescribed general grant remains; v2 is immutable and must
stay available/unpromoted; shared-account network changes could disrupt the
original factory and are explicitly excluded. Existing privileged grant closure
is retained, not newly applied as a mitigation to general v2.

The mock's new execution boundary is gateway Entra identity **and** an exact,
valid, single-use lease. Merely reaching general v2 or listing its exposed tool
is expected; it is not permission to execute the secured mock. Metadata-only
MCP initialization/discovery is distinct from an executable tools/call.

The revised H4 authorization accepts **Foundry User as a testbed platform
limitation**, not a least-privilege role design. The role definition grants
`Microsoft.CognitiveServices/*` data actions, excluding only:

- `Microsoft.CognitiveServices/accounts/AIServices/agents/endpoints/UserIdentityImpersonation/action`
- `Microsoft.CognitiveServices/accounts/OpenAI/fine-tunes-deployments/write`
- `Microsoft.CognitiveServices/accounts/AIServices/fine_tuning_deployments/write`

Its management actions also include Cognitive Services reads/list keys,
project connection secret listing, deployment operations and support actions.
The complete definition, including all management actions, is retained in
`.artifacts\h4\evidence.json` and `all-role-definitions.json`.

No built-in role in the captured inventory carries a Toolbox-specific data
action. Reader, Foundry Agent Consumer and Foundry Project Runtime User were
tried cumulatively; their union still returned 403 on MCP `initialize` after
900.0165 seconds. Adding Foundry User eventually enabled `initialize` and
`tools/list` 200. This is a result for the roles tried, not proof that every
conceivable custom permission combination fails.

The revised experiment additionally defines one custom role using the
inventory's AIServices agent/asset reads, response actions and endpoint
interaction action, without the broad Cognitive Services wildcard. It is
tested under the new release certificate identity **before** its fallback
assignment so existing coder grants cannot contaminate the observation.
Definition, assignment, initial and full-propagation responses are recorded
separately in `.artifacts\h4\resumed.json`. Both probes returned `initialize`
403, at 14:00:21.917 and 14:15:21.499 UTC respectively. The custom role did not
replace Foundry User. This isolated failed trial is not represented as proof
that all custom roles are impossible.

The tested custom role has management account/project reads and only these
data actions:

```text
Microsoft.CognitiveServices/accounts/AIServices/agents/read
Microsoft.CognitiveServices/accounts/AIServices/assets/read
Microsoft.CognitiveServices/accounts/AIServices/responses/*
Microsoft.CognitiveServices/accounts/AIServices/endpoints/interact/action
```

These are inventory-derived candidate discovery/runtime permissions, not
undocumented invented Toolbox actions. The registered provider-operation
inventory is also retained in `.artifacts\h4\provider-data-operations.json`.

Least privilege for Toolbox clients therefore relies on **project boundaries
+ a gateway**, rather than built-in role selection alone. H4 measures only
the project boundary; H6/H7 separately measure gateway execution enforcement. Approval
and narrower tool allowlists remain consumer controls, not IAM boundaries.
What would change the role finding: a built-in or custom role exposing a
Toolbox-scoped data action, verified by `initialize`/`tools/list` 200 without
`Microsoft.CognitiveServices/*`.

Publishing general v3 does **not** revoke v2. Both versions remain unpromoted;
the original general v2 deployment route is still addressable by a caller
with general-project access. The revised H4 result is scoped to general v3
versus the privileged project, not global isolation of the mock operation.
The former public synthetic development tunnel was retired on
2026-11-17T09:52:23.275913Z. General v2 still lists deploy with 200 but unleased
execution gets downstream mock 403 (Toolbox HTTP 200/JSON-RPC -32003).
Publishing/rebinding alone is not represented as a security boundary.

If enforced toolbox-scope RBAC becomes available, the two projects could
collapse into one without changing the common capability contract.

The privileged RemoteTool connection uses a distinct name ending in
`-privileged`: reusing the general connection name returned a real ownership
collision even beneath a different project ARM path. H4 did not update the
general connection. H6 subsequently changed only both owned mutable connection
targets to the secured ACA mock. A live routing probe showed invocation follows
the target while immutable server_url annotations stay historical; all Toolbox
version bytes/default/member pins remain unchanged. The connection service returns `tags: null` despite supplied
ARM tags; purpose/owner/run ownership is therefore carried in its supported
`properties.metadata`. Native project/Entra tags and role/composition
description labels remain separately identified in the ownership manifest.
