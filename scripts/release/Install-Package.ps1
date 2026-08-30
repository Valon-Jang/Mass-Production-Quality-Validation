[CmdletBinding()]
param(
    [ValidateSet("Install", "Update", "Remove")]
    [string]$Action = "Install",
    [string]$PackagePath = "",
    [string]$InstallRoot = "",
    [string]$DataRoot = "",
    [switch]$DryRun
)

$ErrorActionPreference = "Stop"
Set-StrictMode -Version Latest

if ([string]::IsNullOrWhiteSpace($env:LOCALAPPDATA)) {
    throw "LOCALAPPDATA is required for the default current-user paths."
}
if ([string]::IsNullOrWhiteSpace($InstallRoot)) {
    $InstallRoot = Join-Path $env:LOCALAPPDATA "Programs\Mass Production Quality Validation"
}
if ([string]::IsNullOrWhiteSpace($DataRoot)) {
    $DataRoot = Join-Path $env:LOCALAPPDATA "Mass Production Quality Validation\data"
}

$Tool = Join-Path $PSScriptRoot "package_tool.py"
if (-not (Test-Path -LiteralPath $Tool -PathType Leaf)) {
    throw "The package transaction tool is missing."
}

$Arguments = @($Tool, $Action.ToLowerInvariant())
if ($Action -ne "Remove") {
    if ([string]::IsNullOrWhiteSpace($PackagePath)) {
        $AdjacentManifest = Join-Path $PSScriptRoot "extension-manifest.json"
        if (Test-Path -LiteralPath $AdjacentManifest -PathType Leaf) {
            $PackagePath = $PSScriptRoot
        }
        else {
            throw "PackagePath is required when the installer is not inside an extracted package."
        }
    }
    $Arguments += @("--package", [System.IO.Path]::GetFullPath($PackagePath))
}
$Arguments += @(
    "--install-root", [System.IO.Path]::GetFullPath($InstallRoot),
    "--data-root", [System.IO.Path]::GetFullPath($DataRoot)
)
if ($DryRun) {
    $Arguments += "--dry-run"
}

& py -3.12 @Arguments
if ($LASTEXITCODE -ne 0) { exit $LASTEXITCODE }
