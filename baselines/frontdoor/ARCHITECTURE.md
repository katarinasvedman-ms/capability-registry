# Architecture

```text
React portal / embedded chat / LOB route
         MSAL PKCE or explicit mock user
                    |
          FastAPI conversation API -- SQLite
                    |
        live: MAF Agent + FoundryChatClient
        mock: explicit deterministic decision provider
                    |
       stable knowledge/discovery/invocation tools
          |                         |
 mock IQ / Foundry IQ / Search     APIC / local versioned catalog
                                    |
                       governed capability invoker
                        /            |           \
             MCP SDK client    A2A SDK client    published_agent
                    |                |                |
             AI/API Gateway   Supply specialist   ESS-style specialist
                    |           own data/tools     mock HR backend
            mock legacy HTTP API
```

## Boundaries and ownership

The four presentation journeys reuse these boundaries: Leave of Absence is
the primary multi-tool KNOW/ACT flow; new laptop ordering uses governed MCP
through the existing gateway and mock ERP; Supply Risk retains the Foundry A2A
specialist; Concur is navigation to `/lob/concur`. The authenticated mock
expense workspace does not receive a conversation and does not submit to SAP
Concur. Original scanner/service-request and case routes remain available as
technical examples, rather than headline scenarios.

Laptop create/read tools use the existing Operations write/read permissions.
The exact `item=laptop` proposal requires confirmation before the ERP write;
the approval is the idempotency key and the returned `HW-...` order is
owner-scoped. APIM and ACA validate a distinct hardware execution grant, not
a service-request grant reused for a different operation.

`backend\app.py` owns provider-aware authentication, conversation/task APIs, SSE delivery,
and the approval-decision endpoint. SQLite stores messages, pending actions,
requests, balances, approvals, and redacted audit events. A task continues when
its browser stream disconnects; the client can retrieve its status and refresh
the persisted conversation instead of blindly resubmitting a mutation.
Once the SSE task ID arrives, the portal can explicitly cancel running or queued
work through the owner-checked task API. Local disconnection is distinguished
from cancellation, and neither implies rollback of a committed business effect.

`backend\orchestrator.py` supplies the five stable meta-tools. In live mode MAF
performs the model/tool loop; domain tools do not appear in its top-level context.
Offline mode is deterministic simulation and visibly identified. It is not
evidence that a remote model deployment was invoked.

`backend\auth.py` validates tenant-bound API tokens, maps Entra app roles to
permissions, and resolves `entra:{tid}:{oid}` ownership independently of seeded
users. `AUTH_MODE`, `DEMO_MODE`, `KNOWLEDGE_PROVIDER`, `SUPPLY_PROVIDER`,
`ESS_PROVIDER`, `GATEWAY_PROVIDER`, and `CATALOG_PROVIDER`
are independent switches.
Trusted profile data supplies employee attributes, never grants. Mock sign-in is
disabled in Entra mode. Model access uses a separate workload identity.

The live preview selects a Foundry IQ knowledge base; direct Azure AI Search
remains an explicit alternative. Both use
a server-generated country/permission/
employee filter applied before retrieval, then rechecks returned ACLs. The model
cannot override that filter. Sources preserve IDs, sections and provider labels;
the indexed demo content and Work IQ context remain explicitly synthetic.

Foundry IQ uses a dedicated paid Basic Search service with managed-identity
access to the existing Foundry model. Its knowledge base performs low-effort LLM
query planning and semantic retrieval over the owned policy index. Per-source
ACL filters apply before retrieval. The adapter checks source identity, document
keys and returned ACL metadata, and passes only validated extractive evidence to
MAF for the final answer. Planning/search metadata is visible in knowledge
activity. Errors and partial responses fail closed without switching providers.
The original Free direct-search service remains intact. This is not a Work IQ,
SharePoint or automatic enterprise-ingestion integration.

`backend\capabilities.py` loads and validates the catalog from the selected
local file or authenticated API Center inventory. Search ranks lexical
matches in names, descriptions, examples, domains, and tags, returns a shortlist,
and excludes inactive or unauthorized capabilities. Metadata hides invocation
configuration. Deprecated entries remain visible in the explorer but cannot be
invoked. An authorized administrator can atomically replace the in-memory catalog
through `/api/catalog/reload`; invalid catalog content leaves the previous
catalog intact.
Invalid reloads return a structured 422 rather than replacing the working
registry. Protocol declarations must agree with the supported adapter.

The live profile uses `CATALOG_PROVIDER=apic`. API Center Free holds native MCP
and Foundry agent/card assets plus serialized, strictly validated capability
policy metadata. The workload has Reader scoped only to this registry. ARM
inventory is cached for 60 seconds; expired refresh failures fail closed without
stale/local fallback. This does not use the Standard-only registry MCP endpoint.
Discovery filters APIC-sourced entries with server-derived Entra permissions
before exposing model metadata. Registry Reader is not a business permission.
The local catalog remains an offline provider and publication seed only.

`backend\invoker.py` resolves catalog IDs to server-controlled adapters, rechecks
current user permissions, validates JSON Schema inputs, and enforces approval
before execution. New catalog entries can reuse existing adapters without
changing agent instructions; adding a new transport requires adapter code.

With `GATEWAY_PROVIDER=apim`, MCP calls go to the existing APIM instance's
native REST-backed tools, then to the VNet-integrated ACA API and private Blob
storage. The backend supplies an audience-specific workload token and a
separately signed user/execution grant. APIM validates the caller and uses its
managed identity downstream; ACA independently verifies owner, roles and
exact approved inputs. There is no local mutation fallback. The persistent
preview selects this path; the local gateway remains the offline provider.
Discovery uses APIC independently of the execution transport.

The MCP gateway exposes real schema discovery and tool calls and governs access
to the mock legacy HTTP API. The legacy API shares the gateway process but is a
distinct HTTP contract with an enforced asynchronous deadline, not just an
HTTPX network-timeout setting. The Supply specialist runs independently and delegates
through A2A 1.0 JSON-RPC/SSE, with an Agent Card, task states, and evidence
artifacts. A2A traffic does not pass through the gateway. ESS uses an intentionally
separate published-agent HTTP contract, not a purported Copilot Studio tenant.

With `ESS_PROVIDER=foundry`, the People `ask` operation instead invokes the
version-pinned `frontdoor-ess` managed Foundry prompt agent over Responses.
`backend\foundry_ess.py` runs its bounded client-side function loop with isolated,
stateless consultations. Only five strictly validated HR functions are accepted;
identity, endpoints and approval grants cannot be model arguments. Context and
policy use the existing authorization/ACL helpers; balance and request reads use
the normal Invoker and simulated HR service. A proposal requires all three facts
and uses the Invoker's backend-only `proposal_only` flag, so the specialist cannot
submit even an already approved action. Corrections and Confirm remain ordinary
backend invocations; downstream HR approval validation and durable idempotency
are unchanged. There is no cloud-to-mock fallback. This is ESS-style reasoning,
not the Microsoft ESS product; HR operations and data remain synthetic.

The Azure Supply provider instead delegates to a real Foundry prompt agent via
native incoming A2A 1.0 JSONRPC. It fetches an authenticated Agent Card using
workload identity, not the user's portal token. Business eligibility is still
checked by the invoker before delegation. Foundry execution is non-streaming
and read-only; the portal streams its own lifecycle activity. The agent's
embedded synthetic evidence is generated from `services\supply_data.py`,
not retrieved from a live ERP. Its structured output is checked against that
snapshot before returning to the orchestrator. Local A2A remains the explicit
offline provider; errors do not silently substitute it for Foundry.

## Mutation lifecycle

A proposal creates a pending approval bound to user, conversation, capability,
and a canonical input hash plus the server-resolved adapter/tool target. The decision endpoint changes only its approval
status. Execution atomically claims an approved action; downstream HR and legacy
handlers independently verify the matching execution and current permissions.
Offline mutations are persisted under the approval ID in the same SQLite
transaction as their business effects. The Azure mock instead uses conditional
immutable Blob creation bound to approval, owner and input hash; portal approval
state is updated separately, not through a cross-service transaction.
Duplicate execution returns the recorded result.

Changing dates supersedes the earlier proposal. Approvals expire after 30
minutes. A failed transport can reconcile an existing mutation by approval ID;
otherwise a retry requires renewed confirmation with that same ID. Cancellation
does not promise rollback of a business effect that already committed.

## Handoff and governance

The LOB application is a separate portal route backed by a user-authorized case
endpoint. It receives only the case identifier, never the chat transcript.
Suggested handoffs are resolved from that user's permitted case records;
the signed-in operator is not sent to Alex's example case.

Correlation IDs accompany protocol calls and redacted audit events. Gateway and
domain services require a server-only service credential, enforce user
permissions, and apply quotas. Only the portal and backend are published by
Compose. The internal credential is generated once and persisted with demo state,
or supplied through `SERVICE_SECRET`.
Entra invocation also requires short-lived, audience-bound RS256 delegation.
Downstream services verify the principal and role mapping independently; tool
arguments cannot change the user. Only the backend mounts the signing-private-key
volume. Public keys and approval state remain in shared demo storage.

The shared SQLite volume is a deliberate local-demo simplification, enabling
downstream approval validation and transactional idempotency without a separate
identity/approval service. Production would replace synthetic sessions, shared
service credentials and shared storage with per-service workload identities,
real downstream delegated tokens where supported, and domain-owned stores.

## Scope

Implemented domains are People, Supply, and Operations. Knowledge can use real
Foundry IQ/Search infrastructure; its content and Work IQ-style data remain simulated.
The native architecture view dims Product, Legal,
IT, Fabric IQ, and Web IQ. API Center implements the user-requested live Azure
registry extension; it is not a dependency of the credential-free baseline.
Downstream Entra OBO, proactive Teams notifications, and customer-specific
enterprise applications remain outside the implemented runtime.

## Repeatable acceptance boundary

`scripts\test.ps1 -Browser` combines backend/SDK checks and frontend lint/build
with production-container browser/smoke journeys. The latter run in a uniquely
named Compose project using mock providers, no workload credential env-file, and
fresh state/signing volumes. Browser API calls stay on the test origin. Cleanup
removes only that project's resources; it does not switch the live preview's
providers or reset its database.
