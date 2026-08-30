[CmdletBinding()]
param(
    [ValidateSet("Phase0", "Current")]
    [string]$Phase = "Current"
)

$ErrorActionPreference = "Stop"
Set-StrictMode -Version Latest

$RepoRoot = (Resolve-Path (Join-Path $PSScriptRoot "..")).Path
$Python = Join-Path $RepoRoot ".venv\Scripts\python.exe"
if (-not (Test-Path -LiteralPath $Python -PathType Leaf)) {
    throw "Run scripts\Bootstrap.ps1 first."
}

Push-Location $RepoRoot
try {
    $Manifest = "backend/tests/required_phase0_test_ids.txt"
    $MinimumCount = "backend/tests/required_phase0_test_count.txt"
    if ($Phase -eq "Current") {
        $Manifest = "backend/tests/required_regression_test_ids.txt"
        $MinimumCount = "backend/tests/required_current_test_count.txt"
    }
    & $Python (Join-Path $RepoRoot "scripts\run_test_gate.py") `
        --manifest $Manifest --minimum-count-file $MinimumCount
    if ($LASTEXITCODE -ne 0) { exit $LASTEXITCODE }
}
finally {
    Pop-Location
}
