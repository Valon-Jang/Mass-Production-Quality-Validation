[CmdletBinding()]
param(
    [string]$OutputPath = "",
    [switch]$SkipFrontendBuild
)

$ErrorActionPreference = "Stop"
Set-StrictMode -Version Latest

$RepoRoot = (Resolve-Path (Join-Path $PSScriptRoot "..\..")).Path
$Tool = Join-Path $PSScriptRoot "package_tool.py"
$ManifestPath = Join-Path $RepoRoot "packaging\extension-manifest.json"
$Manifest = Get-Content -LiteralPath $ManifestPath -Raw -Encoding UTF8 | ConvertFrom-Json

if (-not $SkipFrontendBuild) {
    & (Join-Path $RepoRoot "scripts\Frontend.ps1") -Mode Build
    if ($LASTEXITCODE -ne 0) { exit $LASTEXITCODE }
}

if ([string]::IsNullOrWhiteSpace($OutputPath)) {
    $ReleaseRoot = Join-Path $RepoRoot ".staging\releases"
    $OutputPath = Join-Path $ReleaseRoot ("MASS-PRODUCTION-QUALITY-VALIDATION-extension-{0}.zip" -f $Manifest.mass_production_quality_validation_version)
}
$OutputPath = [System.IO.Path]::GetFullPath($OutputPath)

$RepoPython = Join-Path $RepoRoot ".venv\Scripts\python.exe"
if (Test-Path -LiteralPath $RepoPython -PathType Leaf) {
    & $RepoPython $Tool build --repo-root $RepoRoot --output $OutputPath
}
else {
    & py -3.12 $Tool build --repo-root $RepoRoot --output $OutputPath
}
if ($LASTEXITCODE -ne 0) { exit $LASTEXITCODE }
