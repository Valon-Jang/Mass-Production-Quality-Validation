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
    Write-Host "[1/7] Compile"
    & $Python -m compileall -q backend scripts
    if ($LASTEXITCODE -ne 0) { exit $LASTEXITCODE }

    Write-Host "[2/7] Backend lint and format"
    & (Join-Path $RepoRoot "scripts\Lint.ps1")
    if ($LASTEXITCODE -ne 0) { exit $LASTEXITCODE }

    Write-Host "[3/7] Backend typecheck"
    & (Join-Path $RepoRoot "scripts\Typecheck.ps1")
    if ($LASTEXITCODE -ne 0) { exit $LASTEXITCODE }

    Write-Host "[4/7] Requirement integrity"
    & $Python (Join-Path $RepoRoot "scripts\check_requirements.py")
    if ($LASTEXITCODE -ne 0) { exit $LASTEXITCODE }

    Write-Host "[5/7] Migration graph"
    & $Python (Join-Path $RepoRoot "scripts\check_migrations.py")
    if ($LASTEXITCODE -ne 0) { exit $LASTEXITCODE }

    Write-Host "[6/7] Frontend typecheck, tests, and production build"
    if ($Phase -eq "Current") {
        & (Join-Path $RepoRoot "scripts\Frontend.ps1") -Mode Check
        if ($LASTEXITCODE -ne 0) { exit $LASTEXITCODE }
    }
    else {
        Write-Host "Phase0 Gate does not require the later frontend slice."
    }

    Write-Host "[7/7] Full release tests, required IDs, and no-skip policy"
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

Write-Host "Mass Production Quality Validation release gate passed."
