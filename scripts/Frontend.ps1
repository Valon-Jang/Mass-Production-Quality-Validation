[CmdletBinding()]
param(
    [ValidateSet("Install", "Typecheck", "Test", "Build", "Check", "Dev")]
    [string]$Mode = "Check"
)

$ErrorActionPreference = "Stop"
Set-StrictMode -Version Latest

$RepoRoot = (Resolve-Path (Join-Path $PSScriptRoot "..")).Path
$FrontendRoot = Join-Path $RepoRoot "frontend"
$PackageJson = Join-Path $FrontendRoot "package.json"
$PackageLock = Join-Path $FrontendRoot "package-lock.json"
$NodeModules = Join-Path $FrontendRoot "node_modules"

if (-not (Test-Path -LiteralPath $PackageJson -PathType Leaf)) {
    throw "Missing frontend package: $PackageJson"
}
if (-not (Test-Path -LiteralPath $PackageLock -PathType Leaf)) {
    throw "Missing frontend dependency lock: $PackageLock"
}

function Invoke-FrontendScript {
    param([Parameter(Mandatory = $true)][string]$Name)

    & npm.cmd --prefix $FrontendRoot run $Name
    if ($LASTEXITCODE -ne 0) { exit $LASTEXITCODE }
}

if ($Mode -eq "Install") {
    & npm.cmd --prefix $FrontendRoot ci
    if ($LASTEXITCODE -ne 0) { exit $LASTEXITCODE }
    return
}

if (-not (Test-Path -LiteralPath $NodeModules -PathType Container)) {
    throw "Run scripts\Bootstrap.ps1 before frontend checks."
}

switch ($Mode) {
    "Typecheck" { Invoke-FrontendScript -Name "typecheck" }
    "Test" { Invoke-FrontendScript -Name "test" }
    "Build" { Invoke-FrontendScript -Name "build" }
    "Dev" { Invoke-FrontendScript -Name "dev" }
    "Check" {
        Invoke-FrontendScript -Name "typecheck"
        Invoke-FrontendScript -Name "test"
        Invoke-FrontendScript -Name "build"
    }
}
