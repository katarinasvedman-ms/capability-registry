[CmdletBinding(SupportsShouldProcess, ConfirmImpact = 'High')]
param([switch]$Execute)

$ErrorActionPreference = 'Stop'
# Do not roll the owned connections back to the retired unauthenticated tunnel.
# Retained H3 general v2/connection fail closed once their owned ACA mock is gone.
$root = Split-Path $PSScriptRoot -Parent
$h9bPath = Join-Path $root '.artifacts\h9b\state.json'
if (Test-Path -LiteralPath $h9bPath) {
    $h9b = Get-Content -LiteralPath $h9bPath -Raw | ConvertFrom-Json
    if ($h9b.run -ne 'h9b-f3e5ad02') { throw 'Unexpected H9b ownership manifest.' }
    $h9bItems = @($h9b.created)
    [array]::Reverse($h9bItems)
    foreach ($item in $h9bItems) {
        if ($item.kind -notin @('application', 'servicePrincipal', 'roleAssignment')) {
            throw 'Unexpected H9b resource kind.'
        }
        if ($item.kind -eq 'roleAssignment') {
            if ($item.principalId -ne $h9b.viewer.principal_id -or
                $item.id -notmatch '^/subscriptions/8aebca5c-b21c-4fca-bbc9-dd77b898e99a/resourceGroups/factory-skills/providers/Microsoft\.ApiManagement/aigateways/cr-h9-f3e5ad02-aigw/workspaces/default/toolServers/general/providers/Microsoft\.Authorization/roleAssignments/[0-9a-f-]+$') {
                throw 'Unexpected H9b assignment scope.'
            }
        } elseif ($item.id -notmatch '^[0-9a-f-]{36}$' -or
            $item.appId -ne $h9b.viewer.client_id) {
            throw 'Unexpected H9b directory object.'
        }
        if (-not $Execute) { Write-Output "Would remove owned H9b $($item.kind): $($item.id)"; continue }
        if ($item.kind -eq 'roleAssignment') {
            $raw = & az resource show --ids $item.id --api-version 2022-04-01 --output json --only-show-errors 2>&1
        } else {
            $collection = if ($item.kind -eq 'application') { 'applications' } else { 'servicePrincipals' }
            $raw = & az rest --method GET --url "https://graph.microsoft.com/v1.0/$collection/$($item.id)" --output json --only-show-errors 2>&1
        }
        if ($LASTEXITCODE -ne 0) {
            if ("$raw" -match 'ResourceNotFound|Request_ResourceNotFound|could not be found|does not exist') { continue }
            throw 'Could not verify owned H9b object.'
        }
        $current = "$raw" | ConvertFrom-Json
        if ($item.kind -eq 'roleAssignment') {
            if ($current.properties.principalId -ne $h9b.viewer.principal_id -or
                $current.properties.description -ne 'purpose=capability-registry-testbed;owner=katarina.svedman;run=f3e5ad02;phase=h9b') {
                throw 'H9b assignment ownership mismatch.'
            }
        } elseif ($current.appId -ne $item.appId -or $current.displayName -ne 'viewer-test' -or
            'phase=h9b' -notin $current.tags -or 'purpose=capability-registry-testbed' -notin $current.tags -or
            'owner=katarina.svedman' -notin $current.tags -or 'run=f3e5ad02' -notin $current.tags) {
            throw 'H9b directory ownership mismatch.'
        }
        if ($PSCmdlet.ShouldProcess($item.id, 'Delete exactly owned H9b object')) {
            if ($item.kind -eq 'roleAssignment') {
                & az resource delete --ids $item.id --api-version 2022-04-01 --output none --only-show-errors
            } else {
                & az rest --method DELETE --url "https://graph.microsoft.com/v1.0/$collection/$($item.id)" --output none --only-show-errors
            }
            if ($LASTEXITCODE -ne 0) { throw 'Exact H9b deletion failed.' }
        }
    }
}
$h9Path = Join-Path $root '.artifacts\h9\state.json'
if (Test-Path -LiteralPath $h9Path) {
    $h9 = Get-Content -LiteralPath $h9Path -Raw | ConvertFrom-Json
    if ($h9.run -ne 'h9-f3e5ad02') { throw 'Unexpected H9 ownership manifest.' }
    $gatewayId = '/subscriptions/8aebca5c-b21c-4fca-bbc9-dd77b898e99a/resourceGroups/factory-skills/providers/Microsoft.ApiManagement/aigateways/cr-h9-f3e5ad02-aigw'
    $h9Items = @($h9.created)
    [array]::Reverse($h9Items)
    foreach ($item in $h9Items) {
        if ($item.kind -notin @('aiGateway', 'roleAssignment')) {
            throw 'Unexpected H9 resource kind.'
        }
        if ($item.kind -eq 'aiGateway' -and $item.id -ne $gatewayId) {
            throw 'Unexpected H9 gateway scope.'
        }
        if ($item.kind -eq 'roleAssignment' -and
            $item.id -notmatch '^/subscriptions/8aebca5c-b21c-4fca-bbc9-dd77b898e99a/resourceGroups/factory-skills/providers/(?:Microsoft\.CognitiveServices/accounts/factory-skills-resource/projects/factory-skills(?:-privileged)?|Microsoft\.ApiManagement/aigateways/cr-h9-f3e5ad02-aigw(?:/workspaces/default/toolServers/[^/]+)?)/providers/Microsoft\.Authorization/roleAssignments/[0-9a-f-]+$') {
            throw 'Unexpected H9 assignment scope.'
        }
        if (-not $Execute) { Write-Output "Would remove owned H9 $($item.kind): $($item.id)"; continue }
        $apiVersion = if ($item.kind -eq 'aiGateway') { '2026-11-01' } else { '2022-04-01' }
        $raw = & az resource show --ids $item.id --api-version $apiVersion --output json --only-show-errors 2>&1
        if ($LASTEXITCODE -ne 0) {
            if ("$raw" -match 'ResourceNotFound|could not be found|does not exist') { continue }
            throw "Could not verify owned H9 resource: $($item.id)"
        }
        $current = "$raw" | ConvertFrom-Json
        if ($item.kind -eq 'roleAssignment') {
            if ($current.properties.principalId -ne $item.principalId -or
                $current.properties.description -ne 'purpose=capability-registry-testbed;owner=katarina.svedman;run=f3e5ad02;phase=h9') {
                throw 'H9 assignment ownership mismatch.'
            }
        } elseif ($current.tags.purpose -ne 'capability-registry-testbed' -or
            $current.tags.owner -ne 'katarina.svedman' -or $current.tags.phase -ne 'h9' -or
            $current.tags.run -ne 'f3e5ad02') {
            throw 'H9 gateway ownership tags mismatch.'
        }
        if ($PSCmdlet.ShouldProcess($item.id, 'Delete exactly owned H9 resource')) {
            & az resource delete --ids $item.id --api-version $apiVersion --only-show-errors --output none
            if ($LASTEXITCODE -ne 0) { throw 'Exact H9 resource deletion failed.' }
        }
    }
}
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
