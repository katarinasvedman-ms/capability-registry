# Governed capability registry testbed

This implements the contract/static-selection/shared-adapter experiments from
`docs\brief.md`. Required kind/lifecycle preserve the source distinctions:
H1, H2 and the tested H3 paths are supported. Revised H4 measures project
boundary enforcement separately from the accepted Foundry User role-breadth
limitation: the certificate access matrix, approved MAF call/no-approval-no-call,
unchanged pins/bridge and delayed overnight coder repeat passed.
See `docs\hypothesis-report.md` for the measured outcome.
H6/H7 are now demonstrated on separate ACA apps with signed leases, independent
verification and a secured mock. H8 has a measured, qualified comparator.
H5 was measured after that report: nano improves front-door selection but
regresses factory exact-set coverage. H5 remains partial; the static default
and original factory are unchanged.

The 28-entry catalog includes the original ten skills/fifteen front-door entries
and three new mock tools. Fifteen named fixtures have explicit testbed approval,
not production approval. Remaining ownership gaps and inference provenance are recorded
in `catalog\field-gaps.json` and `docs\contract.md`.

## Setup and checks

Credentials, virtual environments and `.artifacts` are intentionally excluded
from Git. Raw live evidence and deployment/teardown ownership manifests remain
local; this public repository contains the implementation and summarized report.
A fresh clone cannot use those local live-evaluation or cleanup commands without
the corresponding authorized manifests and credentials. Do not copy private keys
into the repository. Git preserves exact file bytes for the frozen hash checks.

Use Python 3.13 and PowerShell from this repository:

```powershell
python -m venv .venv
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

The Copilot SDK is pinned to the original `1.0.31`. The provider tests exercise
real SDK descriptors and the captured provider's safe multi-file loading
callbacks. Separate live checks now also record native activation in bridge and
provider distribution modes. The original factory bridge resolver/selector and
deployed defaults remain unchanged.

Run the front-door integration checks in a **separate environment**:

```powershell
python -m venv .venv-maf
.\.venv-maf\Scripts\python.exe -m pip install -c requirements-maf.lock -e ".[maf,governance]" --group dev
.\.venv-maf\Scripts\python.exe -B -m tools.verify_frontdoor
```

This uses the captured Catalog/Invoker with temporary SQLite state and an
in-memory tool transport. It checks permission-filtered discovery, fresh
authorization after approval, input validation, approval before execution,
idempotent replay and unchanged handoff. It does not invoke a live MAF agent,
APIM, business system or Foundry tool.

## Architecture

`contracts` and `catalog` define the common metadata boundary. `selection`
provides deterministic skill selection over trusted role/technology/task/policy
metadata, including kind, lifecycle, domain, operation, capability requirements and technology
constraints. The same interface supports weighted front-door discovery.
The captured provider map is an oracle/ceiling, not the implementation
of candidate selection. Unreviewed controls fail closed.

`adapters\core` owns listing/filtering, pinned-reference/schema checks, bounded
Foundry MCP transport and audit. `adapters\copilot_sdk` specializes the captured
provider and retains its final safe materialization/cleanup. It substitutes the
shared transport without modifying the captured source. Use one registry and
credential/transport lifecycle per provider session.

`adapters\maf` specializes only MCP dispatch beneath the captured Invoker's
existing policy envelope. Its final tool-call callback must carry the
server-resolved identity, approval and correlation context. A2A/handoff and
other protocols remain on the original paths.

## Read-only source baselines

`baselines\provenance.json` records captured source revisions, content hashes
and captured-byte changes relative to each recorded HEAD. Source files are
allowlisted; credentials, `.env`, databases, logs, caches and virtualenvs are
excluded. Do not edit the baseline source files for integration experiments.

The source checkouts are work in progress and can keep evolving independently.
Their continued evolution is reported without replacing the captured inputs:

```powershell
.\.venv\Scripts\python.exe -m tools.source_drift
```

For a deliberately new baseline, use `tools.capture_sources --output` with a
fresh destination, then review the resulting source/policy/version changes.
Existing captures are not overwritten. Original repository checkouts and
deployment defaults are not integration targets.

## Explicit live inspection

With an existing authorized Azure CLI sign-in:

```powershell
.\.venv\Scripts\python.exe -m tools.validate_catalog --probe `
  --binding .\catalog\historical-binding.json --compare-snapshot
.\.venv\Scripts\python.exe -m tools.assess_gate
```

The probe reads only the captured historical Toolbox version. It records
HTTP observations and instruction hashes under `.artifacts`; it does not
publish versions, change defaults or provision resources. Successful probing is
not full-catalog readiness. `--live --ready --bindings` is the separate
strict readiness path. `--ready` intentionally exits nonzero while any draft or
unassigned owner remains, even if those causes are allowed by the H4 gate:

```powershell
.\.venv\Scripts\python.exe -m tools.validate_catalog --offline --ready --bindings .\catalog\bindings.json
```

### Live adapter evidence

The existing general Toolbox has a new **unpromoted version 2** for synthetic
tools. Default **1** and all ten original skill pins are unchanged. The mock
has moved to secured ACA. The old tunnel was deleted; do not restart it.
See `mocks\README.md`. Do not republish or promote to rerun checks.

```powershell
.\.venv\Scripts\python.exe -m tools.assess_gate
```

The original H3 commands are historical: their unleased mock calls are now
expected to fail. Do not overwrite frozen H3 evidence. Use the governance
commands below for current execution. The .NET diff/proposal under `docs\changes`
remains pending; neither publication nor promotion is authorized.
The original H3 summaries remain frozen. Do not rerun those summary-writing
commands merely to inspect the split; `tools.h4_maf` records H4 separately.
Do not run the old `publish_mock --verify-existing` recovery command after
the split: it expects only one mock composition and can overwrite bindings.

H5/model deployment is authorized by the latest go decision, only after the
recorded H6/H7 report and dynamic entry gate. The offline
GitHub workflow is defined; hosted execution requires an actual Git repository
and configured remote.

## Leased ACA reference consumer

The owned deployment is recorded in `.artifacts\governance\deployment.json`.
Consumer, gateway and mock have separate managed identities. Signing stays in
Key Vault; Azure Table atomic inserts prevent replay. Caller tokens are forwarded
to pinned Toolbox, not exchanged OBO. General v2 remains reachable/listable, but
its downstream mock requires gateway identity and a valid exact lease.
The mutable owned connection targets, not immutable Toolbox versions, were updated.

```powershell
.\.venv-maf\Scripts\python.exe -m pytest -q tests\test_governance_lease.py tests\test_governance_gateway.py
.\.venv-maf\Scripts\python.exe -m tools.governance_live h6 --live
.\.venv-maf\Scripts\python.exe -m tools.governance_live h7 --live
.\.venv-maf\Scripts\python.exe -m tools.governance_live h8 --live
.\.venv-maf\Scripts\python.exe -m tools.governance_metrics
.\.venv-maf\Scripts\python.exe -m tools.governance_live runtime --live
.\.venv\Scripts\python.exe -m tools.verify_governance_bridge --live
.\.venv-maf\Scripts\python.exe -m tools.verify_governance_telemetry
```

These are explicit synthetic calls using the existing test certificates; do not
silently rerun historical provisioning/publication. H8 uses a pre-authorized,
transport-equivalent control, not an unsecured/no-gateway baseline.
`docs\governance-design.md` records AGT fallback and carriage details.

All apps listen on 8088. The nonstreaming `/responses` fixture accepts standard
`input` plus string `metadata`: `capability_id`, JSON-encoded `arguments`, and
`synthetic_approval: "completed"`. It demonstrates native MAF invocation, not
general conversational/model selection. Responses validate against the pinned
OpenAI model. `/readiness`, graceful SIGTERM/restart and three unleased skill
loads are measured. Foundry hosting remains a documented future deployment stub;
no Foundry hosted agent is deployed.

### Measured model selection

`selection\retrieval.py` implements metadata-only BM25 and a pluggable guarded
model choice over eligible top-k; `selection\model.py` supplies the bounded
Foundry project client. The authenticated consumer `/selection` endpoint accepts
`{"input":"Test create_ticket","mode":"model"}`. Static remains the default mode;
explicit model mode uses pinned gpt-4.1-nano 2025-04-14. Selection never executes.
Submit a second `/responses` request with the chosen ID, exact inputs and
synthetic approval to exercise the lease path.

The owned nano and supplemental mini deployments use GlobalStandard capacity
50 each, NoAutoUpgrade, and existing release-project access without new grants.
Both frozen experiments have 36 cases/two repetitions and 72 successful model
requests. Front-door scores: static 50/60, BM25 48/60, nano 58/60, mini 56/60.
Factory optional-aware exact-set scores: static/BM25/mini 10/12, nano 5/12.
Do not replace factory selection based on the front-door result. Full splits,
counterexamples and measured latency are in the report; no budget is assumed.

Recompute retained results without cloud calls:

```powershell
.\.venv\Scripts\python.exe -m tools.h5_metrics
.\.venv\Scripts\python.exe -m tools.h5_metrics --nano
.\.venv\Scripts\python.exe -m tools.assess_gate
```

`tools.evaluate_h5 --live [--nano]` and
`tools.verify_hosted_selection --live` run the credentialed experiments in
`.venv-maf`; they intentionally refuse to overwrite existing evidence.
Settings/datasets and recorded source hashes under `evaluation` are frozen,
not tuning inputs. Register a separate experiment before collecting new results.
The image includes settings but not labelled datasets. Hosted proof in
`.artifacts\h5\hosted.json` connects selection to exactly one approved leased
mock call and rechecks caller ceilings and immutable controls.

`scripts\teardown-testbed.ps1` covers new owned resources plus H4 and was **not run**.
It includes both owned model deployments.
It does not restore the retired tunnel. Existing general default/pins remain
outside cleanup; a retained owned general-v2 connection fails closed after its
ACA mock is removed. Key Vault soft-delete retention is preserved.

## H4 safety gate and teardown

`catalog\project-tiers.json` is the trusted routing allowlist, not proof that
the privileged project was created. Privileged entries cannot resolve through
general even if a binding falsely labels that project privileged. CI supplies
bindings to validation and exercises an intentionally failing CLI case:

```powershell
.\.venv\Scripts\python.exe -m tools.validate_catalog --offline --bindings .\catalog\bindings.json
.\.venv\Scripts\python.exe -m tools.verify_h4_negative
```

The historical `tools.h4_preflight` experiment stopped under the previous role
rule. Its raw `.artifacts\h4\evidence.json` is preserved, not rewritten.
The user's revised authorization accepts Foundry User as a platform limitation.
`tools.h4_resume` maintains a separate `resumed.json`, one isolated custom-role
trial, general v3, the privileged project and exact certificate-role bindings.
Do not rerun completed publication/provisioning phases or duplicate identities.

The explicit live phases are:

```powershell
.\.venv\Scripts\python.exe -m tools.h4_resume --live custom_role_setup
.\.venv\Scripts\python.exe -m tools.h4_resume --live custom_role_finish
.\.venv\Scripts\python.exe -m tools.h4_resume --live publish_general
.\.venv\Scripts\python.exe -m tools.h4_resume --live provision
.\.venv\Scripts\python.exe -m tools.h4_resume --live matrix
.\.venv-maf\Scripts\python.exe -m tools.h4_maf --live
.\.venv\Scripts\python.exe -m tools.h4_resume --live controls
.\.venv\Scripts\python.exe -m tools.h4_resume --live repeat
.\.venv\Scripts\python.exe -m tools.verify_h4_negative
.\.venv\Scripts\python.exe -m tools.h4_resume --live finalize
.\.venv\Scripts\python.exe -m tools.assess_gate
```

Custom-role and repeated coder controls wait at least fifteen minutes.
Any privileged coder response other than 403 stops immediately; it is not
retried into an apparent success. Positive release controls allow bounded
propagation. Existing general versions 1/2 are compared byte-for-byte, all ten
instruction hashes are checked, and bridge activation is a native dev-session
proof, not a deployed-worker test.

General v3 excludes deploy_release and is used explicitly, without promotion.
General v2 remains immutable, unpromoted **and addressable**. H4 alone did not
revoke it or protect the then-public mock. H6 subsequently retired the tunnel
and secured the ACA replacement behind gateway identity and exact-call leases.
Project-boundary evidence is distinct from gateway execution control.
The .NET pin is untouched.

`resumed-maf.json` records the release certificate's real adapter/tool-call,
approval/no-call pair, raw mock traffic and core audit. `ownership.json` records
exact created IDs. Private certificates stay ignored and ACL-restricted,
expire after one day and never enter logs. Entra/project resources carry
purpose/owner/run tags. Role definitions/assignments lack native tag support;
they use ownership descriptions. Toolbox compositions use descriptions and
the manifest, not custom capability metadata.

`scripts\teardown-h4.ps1` is deliberately **not run**. Its default previews only
manifest-owned objects; `-Execute` enables ownership-checked, confirmed exact-ID
deletion. It covers the owned custom role, new general v3, privileged project/
connection/Toolbox, two certificate identities and their owned assignments.
It does not remove pre-existing versions 1/2, defaults, source files or the
historical general-v2 connection. The old tunnel is already retired; use the
extended testbed script for current ACA/model resources. Evidence remains after teardown.
See `docs\toolbox-conventions.md` for name/schema transformations and approval floors.
