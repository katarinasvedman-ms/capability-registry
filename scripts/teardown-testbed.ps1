[CmdletBinding(SupportsShouldProcess, ConfirmImpact = 'High')]
param([switch]$Execute)

$ErrorActionPreference = 'Stop'
# Do not roll the owned connections back to the retired unauthenticated tunnel.
# Retained H3 general v2/connection fail closed once their owned ACA mock is gone.
$root = Split-Path $PSScriptRoot -Parent
$path = Join-Path $root '.artifacts\governance\deployment.json'
if (Test-Path -LiteralPath $path) {
    $manifest = Get-Content -LiteralPath $path -Raw | ConvertFrom-Json
    if ($manifest.run -ne 'f3e5ad02') { throw 'Unexpected governance ownership manifest.' }
    $prefix = '/subscriptions/8aebca5c-b21c-4fca-bbc9-dd77b898e99a/resourceGroups/factory-skills/providers/'
    $items = @($manifest.created)
    [array]::Reverse($items)
    foreach ($item in $items) {
        if (-not $item.id.StartsWith($prefix, [StringComparison]::OrdinalIgnoreCase)) {
            throw 'Refusing cleanup outside the exact testbed resource group.'
        }
        if (-not $Execute) { Write-Output "Would remove owned $($item.kind): $($item.id)"; continue }
        $raw = & az resource show --ids $item.id --output json --only-show-errors 2>&1
        if ($LASTEXITCODE -ne 0) {
            if ("$raw" -match 'ResourceNotFound|could not be found|does not exist') { continue }
            throw "Could not verify owned resource: $($item.id)"
        }
        $current = "$raw" | ConvertFrom-Json
        if ($item.kind -eq 'roleAssignment') {
            if ($current.properties.principalId -ne $item.principalId -or
                $current.properties.description -ne 'purpose=capability-registry-testbed;owner=katarina.svedman;run=f3e5ad02') {
                throw 'Assignment ownership mismatch.'
            }
        } elseif ($item.kind -in @(
            'identity', 'workspace', 'appinsights', 'registry', 'storage',
            'vault', 'environment', 'containerapp', 'modelDeployment'
        )) {
            if ($current.tags.purpose -ne 'capability-registry-testbed' -or
                $current.tags.owner -ne 'katarina.svedman' -or
                $current.tags.run -ne 'f3e5ad02' -or
                $current.name -notmatch '^cr-?gov-?f3e5ad02(?:-|$)') {
                throw 'Resource ownership tags/name mismatch.'
            }
        } else { throw "Unsupported cleanup kind: $($item.kind)" }
        if ($PSCmdlet.ShouldProcess($item.id, 'Delete exactly owned governance resource')) {
            & az resource delete --ids $item.id --only-show-errors --output none
            if ($LASTEXITCODE -ne 0) { throw 'Exact resource deletion failed.' }
        }
    }
}
# The historical script independently verifies H4 object ownership and exact scope.
& (Join-Path $PSScriptRoot 'teardown-h4.ps1') -Execute:$Execute -WhatIf:$WhatIfPreference
