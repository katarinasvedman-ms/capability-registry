[CmdletBinding(SupportsShouldProcess, ConfirmImpact = 'High')]
param([switch]$Execute)

$ErrorActionPreference = 'Stop'
$root = Split-Path $PSScriptRoot -Parent
$manifestPath = Join-Path $root '.artifacts\h4\ownership.json'
$manifest = Get-Content -LiteralPath $manifestPath -Raw | ConvertFrom-Json
$labels = @('purpose=capability-registry-testbed', 'owner=katarina.svedman', 'h4=f3e5ad02')
if ($manifest.run -ne 'f3e5ad02' -or @($labels | Where-Object { $_ -notin $manifest.tags }).Count) {
    throw 'Ownership manifest does not match this H4 experiment.'
}
$scope = '/subscriptions/8aebca5c-b21c-4fca-bbc9-dd77b898e99a/resourceGroups/factory-skills/providers/Microsoft.CognitiveServices/accounts/factory-skills-resource/projects/factory-skills'
$privilegedScope = "$scope-privileged"
$ownedPrincipals = @($manifest.created | Where-Object kind -eq 'servicePrincipal' | ForEach-Object id)
$credentials = [IO.Path]::GetFullPath((Join-Path $root '.artifacts\h4\credentials')) + '\'
$items = @($manifest.created)
[array]::Reverse($items)
foreach ($item in $items) {
    $target = if ($item.kind -eq 'certificate') { $item.path } else { $item.id }
    if (-not $Execute) { Write-Output "Would remove owned $($item.kind): $target"; continue }
    if ($item.kind -eq 'certificate') {
        $path = [IO.Path]::GetFullPath($item.path)
        if (-not $path.StartsWith($credentials, [StringComparison]::OrdinalIgnoreCase) -or
            [IO.Path]::GetFileName($path) -notin @('factory-coder-test.pem', 'release-test.pem')) {
            throw 'Certificate path is outside the exact owned credential location.'
        }
        if ((Test-Path -LiteralPath $path -PathType Leaf) -and $PSCmdlet.ShouldProcess($path, 'Remove owned private certificate')) {
            Remove-Item -LiteralPath $path
        }
        continue
    }
    if ($item.kind -eq 'roleAssignment') {
        $assignmentScope = $item.id -replace '/providers/Microsoft.Authorization/roleAssignments/[0-9a-f-]{36}$', ''
        if ($assignmentScope -notin @($scope, $privilegedScope) -or
            $item.id -notmatch '/providers/Microsoft.Authorization/roleAssignments/[0-9a-f-]{36}$' -or
            $item.principalId -notin $ownedPrincipals) { throw 'Assignment is outside owned scope/principal.' }
        $url = "https://management.azure.com$($item.id)?api-version=2022-04-01"
    } elseif ($item.kind -eq 'roleDefinition') {
        if ($item.id -notmatch '^/subscriptions/8aebca5c-b21c-4fca-bbc9-dd77b898e99a/providers/Microsoft.Authorization/roleDefinitions/[0-9a-f-]{36}$') {
            throw 'Custom role definition is outside owned subscription.'
        }
        $url = "https://management.azure.com$($item.id)?api-version=2022-04-01"
    } elseif ($item.kind -eq 'project') {
        if ($item.id -ine $privilegedScope) { throw 'Refusing to delete any other project.' }
        $url = "https://management.azure.com$($item.id)?api-version=2025-06-01"
    } elseif ($item.kind -eq 'connection') {
        if ($item.id -ine "$privilegedScope/connections/capability-registry-mock-f3e5ad02-privileged") {
            throw 'Refusing to delete any other connection.'
        }
        $url = "https://management.azure.com$($item.id)?api-version=2025-06-01"
    } elseif ($item.kind -eq 'toolboxVersion') {
        if ($item.project_endpoint -notin @(
            'https://factory-skills-resource.services.ai.azure.com/api/projects/factory-skills',
            'https://factory-skills-resource.services.ai.azure.com/api/projects/factory-skills-privileged'
        ) -or $item.id -notin @(
            'factory-skills/3', 'capability-registry-privileged/1'
        )) { throw 'Unexpected Toolbox composition; no cleanup allowed.' }
        $parts = $item.id.Split('/')
        if (($parts[0] -eq 'factory-skills') -ne ($item.project_endpoint -notlike '*-privileged')) {
            throw 'Composition/project mismatch.'
        }
        $url = "$($item.project_endpoint)/toolboxes/$($parts[0])/versions/$($parts[1])?api-version=v1"
    } elseif ($item.kind -eq 'toolbox') {
        if ($item.project_endpoint -ne 'https://factory-skills-resource.services.ai.azure.com/api/projects/factory-skills-privileged' -or
            $item.id -ne 'capability-registry-privileged') {
            throw 'Refusing to delete any other Toolbox.'
        }
        $url = "$($item.project_endpoint)/toolboxes/$($item.id)?api-version=v1"
    } elseif ($item.kind -in @('application', 'servicePrincipal')) {
        if ($item.id -notmatch '^[0-9a-f-]{36}$') { throw 'Invalid owned directory object ID.' }
        $collection = if ($item.kind -eq 'application') { 'applications' } else { 'servicePrincipals' }
        $url = "https://graph.microsoft.com/v1.0/$collection/$($item.id)"
    } else {
        throw "Unknown resource kind; refusing broad cleanup: $($item.kind)"
    }
    $audience = if ($item.kind -in @('toolboxVersion', 'toolbox')) { 'https://ai.azure.com' }
        elseif ($item.kind -in @('application', 'servicePrincipal')) { 'https://graph.microsoft.com' }
        else { 'https://management.azure.com' }
    $raw = & az rest --method get --url $url --resource $audience --output json 2>&1
    if ($LASTEXITCODE -ne 0) {
        if ("$raw" -match 'ResourceNotFound|Request_ResourceNotFound|could not be found|does not exist') { continue }
        throw "Could not verify exact owned object: $target"
    }
    $current = "$raw" | ConvertFrom-Json
    if ($item.kind -eq 'roleAssignment') {
        if ($current.properties.principalId -ne $item.principalId -or
            $current.properties.roleDefinitionId -ne $item.roleDefinitionId -or
            $current.properties.description -ne ($labels -join ';')) {
            throw 'Assignment ownership changed; refusing deletion.'
        }
    } elseif ($item.kind -eq 'roleDefinition') {
        if ($current.properties.roleName -ne $item.roleName -or
            $current.properties.description -ne ($labels -join ';')) {
            throw 'Custom role ownership changed.'
        }
    } elseif ($item.kind -eq 'project') {
        if ($current.tags.purpose -ne 'capability-registry-testbed' -or
            $current.tags.owner -ne 'katarina.svedman' -or $current.tags.h4 -ne 'f3e5ad02') {
            throw 'Project ownership changed.'
        }
    } elseif ($item.kind -eq 'connection') {
        if ($current.properties.metadata.testbed -ne 'capability-registry-f3e5ad02' -or
            $current.properties.metadata.purpose -ne 'capability-registry-testbed' -or
            $current.properties.metadata.owner -ne 'katarina.svedman') {
            throw 'Connection ownership changed.'
        }
    } elseif ($item.kind -eq 'toolbox') {
        $versionsUrl = "$($item.project_endpoint)/toolboxes/$($item.id)/versions?api-version=v1"
        $versionsRaw = & az rest --method get --url $versionsUrl --resource $audience --output json
        if ($LASTEXITCODE -ne 0) { throw 'Could not verify owned Toolbox versions.' }
        $versions = "$versionsRaw" | ConvertFrom-Json
        if ($versions.has_more -or $versions.nextLink -or @($versions.data).Count -ne 1 -or
            $versions.data[0].version -ne '1' -or $versions.data[0].description -ne $item.description) {
            throw 'Privileged Toolbox has independently changed; refusing deletion.'
        }
    } elseif ($item.kind -eq 'toolboxVersion') {
        if ($current.description -ne $item.description) { throw 'Composition ownership changed.' }
        $parts = $item.id.Split('/')
        $boxUrl = "$($item.project_endpoint)/toolboxes/$($parts[0])?api-version=v1"
        $boxRaw = & az rest --method get --url $boxUrl --resource $audience --output json
        if ($LASTEXITCODE -ne 0) { throw 'Could not verify Toolbox default.' }
        $box = "$boxRaw" | ConvertFrom-Json
        if ($parts[0] -eq 'factory-skills' -and $box.default_version -eq $parts[1]) {
            throw 'General v3 was promoted separately; refusing to remove the current default.'
        }
    } elseif (@($labels | Where-Object { $_ -notin $current.tags }).Count -or
        $current.appId -ne $item.appId) {
        throw 'Directory object ownership changed; refusing deletion.'
    }
    if ($PSCmdlet.ShouldProcess($target, 'Delete exact H4-owned object')) {
        & az rest --method delete --url $url --resource $audience --output none
        if ($LASTEXITCODE -ne 0) { throw "Exact-object deletion failed: $target" }
    }
}
$folder = $credentials.TrimEnd('\')
if ($Execute -and (Test-Path -LiteralPath $folder -PathType Container) -and
    @(Get-ChildItem -LiteralPath $folder -Force).Count -eq 0 -and
    $PSCmdlet.ShouldProcess($folder, 'Remove empty owned credential directory')) {
    Remove-Item -LiteralPath $folder
}
