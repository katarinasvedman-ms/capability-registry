# Synthetic capability testbed

No build is queried, ticket created, hardware ordered or release deployed.
Outputs are deterministic; accepted/rejected calls are logged with credentials
and leases redacted. Bodies are bounded to 64 KiB.

## Current execution boundary

`mocks.secured` runs in its own ACA app/managed identity. Metadata-only MCP
initialize/list is public so the existing RemoteTool connection can discover.
Executable calls require the gateway's verified Entra identity; side effects
also require an exact signed lease and independent atomic replay check.
Approval is synthetic, not a human approval system.

`get_build_status` is read-only. `create_ticket` is a general write; contract
approval is intentionally stricter than its remote metadata. `deploy_release`
is privileged and approval-required. The inherited hardware fixture remains.
One action sub-agent demonstrates leased A2A `message/send`; it has no actual
business effects.

## Published state and retired route

- General Toolbox `factory-skills` default **1**, ten member pins **1**, unchanged.
- General **v2**, unpromoted/immutable, still advertises deploy.
- General **v3**, unpromoted, excludes deploy and retains the other three mocks.
- Privileged `capability-registry-privileged` **v1**, deploy only.
- Both owned RemoteTool connection targets now point to the ACA mock `/mcp`.
  Immutable advertised server_url annotations still show the historical tunnel.
- Gateway-only identity/lease execution became live at
  **2026-11-17T09:51:18.036208Z**.
- Tunnel `capability-registry-f3e5ad02.eun1` was **deleted** at
  **09:52:23.275913Z**; its old URL returns 404. The owned loopback mock and
  tunnel processes are stopped. **Do not restart the unauthenticated route.**

General v2 discovery stays 200 by explicit scope decision. Its unleased direct
deploy is rejected downstream with 403, wrapped by Toolbox in HTTP 200/
JSON-RPC -32003. Project grants/network reachability and execution authorization
are distinct; see `docs\toolbox-conventions.md`.

## Evidence and reruns

`.artifacts\governance\live.json` records raw redacted HTTP, audit and exact
mock receipts for H6/H7, runtime checks and H8 samples. `latency.json` derives
percentiles. `gateway-no-lease-probe.txt` records the actual gateway MI without
a lease getting 403 from both side-effecting tools. ACA logs persist in the
owned workspace; `/evidence` is a bounded authenticated diagnostic cache.

```powershell
.\.venv-maf\Scripts\python.exe -m tools.governance_live h6 --live
.\.venv-maf\Scripts\python.exe -m tools.governance_live h7 --live
```

Do not rerun historical publication/recovery or H3 summary writers: those
unleased calls now deliberately fail and recovery can overwrite split bindings.
Original publication used the documented ARM RemoteTool connection schema and
`azd ai toolbox connection add --from-version 1`; evidence remains frozen in
`.artifacts\mock-publication.json` and `.artifacts\maf-live.json`.

## Retention and cleanup

Keep the requested testbed until explicitly asked to clean it up.
`.artifacts\governance\deployment.json` and `.artifacts\h4\ownership.json`
inventory exact owned resources. `scripts\teardown-testbed.ps1` covers governance
plus H4, validates ownership and was **not run**, including no preview execution.
It does not restore the retired tunnel; retained general-v2 metadata/connection
will fail closed if the owned ACA mock is removed. Never delete the shared
general project/Toolbox, change its default/pins, or kill unrelated processes.
