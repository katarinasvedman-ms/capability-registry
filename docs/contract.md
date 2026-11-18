# Capability contract experiment

The common schema covers all ten captured factory skills, all fifteen captured
front-door capabilities, and three synthetic tools. There are no per-kind
extension objects. Fifteen entries have explicit **testbed-only** approval;
thirteen remain draft. This is not a production approval catalog.

## Fields and provenance

| Group | Fields | Representation |
| --- | --- | --- |
| Platform | `id`, `type`, `kind`, `version`, `status`, `domain`, `protocol`, `invocation` | Contract-only normalized identity, governance, discovery path, business domain and routing. Required `kind` is knowledge/action/handoff, independent of type and operation. Capability semantic versions are distinct from numeric Toolbox/member pins. |
| Domain | `name`, `description`, `inputs` | MCP-native name/description and tool `inputSchema`, where available; source metadata for other protocols. Skills have null inputs. |
| Domain | `examples`, `tags`, `owner` | Contract-only. Source examples/tags/team owners are preserved. Missing ownership is the literal `unassigned`, never an invented team. |
| Applicability | `roles`, `technologies`, `tasks`, `requires` | Contract-only. `requires` replaces `dependencies`, defaults to `[]`, and contains capability IDs only. Every reference must resolve; missing references and cycles are errors. |
| Controls | `operation` | Required `read`, `write`, or `handoff`. Skills are read; source actions are write. Write/handoff are side-effecting; leases belong to a later phase and are not implemented here. |
| Controls | `lifecycle` | Required active/deprecated/retired operational state, independent of draft/approved governance status. Deprecated is excluded from discovery by default; retired always. |
| Controls | `risk_tier`, `permissions`, `approval_required`, `reviewer`, `approved_at`, `approval_scope` | Contract-only. Source permissions/confirmation rules remain intact. Approved fixtures require known controls and `approval_scope: testbed`. |

Toolbox-native skill resources expose name/description/type/url, not these
governance/applicability fields. The common `type` is not that index's package
format. No custom Toolbox-version metadata is required or written.

Unknown review/risk controls are nullable only for draft imports. An unassigned
owner is structurally representable but always a readiness issue, including for
approved fixtures. Approval date/reviewer/scope are recorded in
`catalog\testbed-approval.json`; rebuilding does not invent a fresh review date.

## Technology requirements and capability prerequisites

Strings and objects without a constraint, such as `"dotnet"` and
`{"name":"dotnet"}`, are equivalent unconstrained applicability alternatives.
`"*"` as a string means any stack. Explicitly constrained objects are conjunctive
requirements. `{"name":"dotnet","constraint":">=8,<10"}` requires both the
technology and a matching trusted `context.technology_versions.dotnet`.
Missing version evidence fails closed.

Supported constraints are comma-separated numeric release comparisons
`==`, `!=`, `>=`, `<=`, `>`, `<`; trailing zero components compare equally.
Constraint `"*"` requires presence without asserting a numeric version.
Invalid constraint syntax is a validation error, not an unconstrained fallback.
No new factory runtime dependency was introduced for comparisons.

The factory source's `prerequisites: ["dotnet"]` refers to a **technology**, not
an existing capability ID. Importing it as `requires: ["dotnet"]` would correctly
fail reference validation. Instead:

- Source capability dependencies become `requires`.
- Source technology prerequisites become conjunctive technology objects with
  constraint `"*"`.
- The captured single compatibility label, `copilot-sdk-python-1`, becomes another
  required technology. It is the source runtime-generation identifier, **not**
  an invented SDK semantic-version constraint. Trusted factory selection context
  includes it; the actual SDK remains pinned separately to `1.0.31`.

All ten captured skills have exactly one compatibility label. The importer
refuses multiple alternative runtime labels rather than silently interpreting
alternatives as conjunction.

The single `select(context, catalog, policy)` interface supports factory role
selection and front-door goal/domain/operation/kind/lifecycle discovery. Bridge selection
retains the source's request-level failure on unmet prerequisites/compatibility;
provider candidate selection filters incompatible candidates. Forced or
transitive requirements cannot bypass constraints, permissions, role or tier.

## Explicit fixture classification

All ten factory skills are approved as fixtures. The two approved front-door
IDs are `operations.order_hardware` (domain parity and authorization/approval/
idempotency checks) and `operations.open_expenses` (unchanged non-MCP handoff).
The three `mock.*` tools are approved for live transport checks. Everything else,
including the source-deprecated inventory entry, is draft as instructed.

Fourteen fixtures are general risk. The revised H4 authorization explicitly
classifies `mock.deploy_release` as privileged, action/write, active and
approval-required, retaining its testbed approval. Its binding targets only
the allowlisted privileged project. This classification is the user's synthetic
isolation test, not an inference that all writes need privileged access.
No other selected source indicates elevated access. The administrative
Operations capability was not selected as a fixture. Source owner team names
remain unchanged; the ten skills and three new mocks have unassigned owners.

The remote `create_ticket` mock does not itself require confirmation. The
captured front-door Catalog requires it for **every** action, so this consumer's
contract keeps the stricter confirmation requirement. `deploy_release` requires
confirmation at both layers. This difference is explicit, not a weakening of
the source envelope.

## Discovery taxonomy and lifecycle

Source front-door `type` is normalized losslessly, not copied into an invalid
enum. Its **executable read** path maps to `kind: action, operation: read`;
knowledge retrieval maps to `kind: knowledge, operation: read`. Thus action
does not imply write. Source actions are action/write, handoffs are
handoff/handoff, and agents retain `type: agent`. A2A agents infer kind handoff;
the published read agent infers knowledge. Protocol and route stay unchanged.

Source filters translate as follows: knowledge -> tool/knowledge; read ->
tool/action/read; action -> tool/action/write; agent -> agent; handoff ->
workflow/handoff. This translation preserves the valid knowledge/read
counterexample even when protocol and every other source field are identical.
All fifteen source rows round-trip exactly from the common representation.

Source active/deprecated states map directly to lifecycle; inactive maps to
retired. The inventory counterfactual now changes lifecycle without changing
approval. All nonfixtures remain draft, including deprecated inventory.

Trusted policy `include_deprecated: true` allows deprecated discovery, still
subject to governance, permissions and explicit filters. Retired entries never
enter discovery, even with that option. New execution requires active lifecycle.
`Registry.lookup` resolves snapshot metadata for historical audit, including
non-Toolbox routes, without reauthorizing execution. `Registry.resolve` remains
a non-executing immutable-pin lookup for pinned historical entries.
Deprecated/retired snapshots still resolve without permitting a new call.

Every inferred value is listed here and in `catalog\field-gaps.json`; all other
front-door kind/lifecycle values are source-normalized as above:

| Entry | Inferred kind | Inferred lifecycle |
| --- | --- | --- |
| architecture-analysis | knowledge | active |
| implementation-planning | knowledge | active |
| code-review | knowledge | active |
| test-strategy | knowledge | active |
| legacy-code-analysis | knowledge | active |
| dotnet-development | knowledge | active |
| java-development | knowledge | active |
| python-development | knowledge | active |
| api-development | knowledge | active |
| database-changes | knowledge | active |
| people.employee_self_service | knowledge | source active |
| supply.assess_risk | handoff | source active |
| mock.get_build_status | knowledge | active |
| mock.create_ticket | action | active |
| mock.deploy_release | action | active |

`tools.verify_frontdoor` compares full metadata ranking over the same fifteen
source capabilities, including source-type and independent common-kind filters.
Actual consumer selection additionally applies the same explicit fixture
approval ceiling to both source and contract. This is not an approval overlay
or a claim that draft entries are selectable.

## Pins, transport and audit

`catalog\historical-binding.json` remains unchanged. `catalog\bindings.json`
keeps all ten skill member pins at version 1 of Toolbox version 1. Mock tools
and the hardware fixture use the new, **unpromoted** Toolbox version 2.
Source semantic versions and pinned content may differ; the documented .NET
change proposal does not claim that member version 1 is current source content.

Foundry prefixes advertised mock tool names with
`capability-registry-mock-f3e5ad02___`. Trusted bindings retain those exact names;
logical contract call specs and source schemas are unchanged. Foundry also adds
the default JSON Schema 2020-12 `$schema` annotation. Pin comparison normalizes
only that default annotation; changed constraints or a changed explicit dialect
still fail. Both observed service transformations are recorded in live evidence.
See `docs\toolbox-conventions.md` for the approval floor and publication rules.

The MAF adapter remains below the original Invoker's fresh authorization,
validation, exact-input approval, owner-scoped state and idempotency envelope.
Its MCP call carries identity, approval and trace context in `_meta`; the mock
records what actually arrives. The anonymous synthetic mock checks approval
context presence, not a production approval signature or delegated identity.
No real business operations are exposed.

Audit records include capability ID, semantic version, scope, safe immutable
registry pin, identity, selection rationale, outcome and pin-verification state.
Raw evidence never includes authorization headers. Tool calls are not
automatically retried.
