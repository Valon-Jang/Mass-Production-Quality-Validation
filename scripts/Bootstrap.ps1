[CmdletBinding()]
param()

$ErrorActionPreference = "Stop"
Set-StrictMode -Version Latest

$RepoRoot = (Resolve-Path (Join-Path $PSScriptRoot "..")).Path
$VenvPath = Join-Path $RepoRoot ".venv"
$Python = Join-Path $VenvPath "Scripts\python.exe"
$LockFile = Join-Path $RepoRoot "requirements\dev.lock"

if (-not (Test-Path -LiteralPath $Python -PathType Leaf)) {
    Write-Host "Creating the Python 3.12 virtual environment..."
    & py -3.12 -m venv $VenvPath
    if ($LASTEXITCODE -ne 0) { exit $LASTEXITCODE }
}

& $Python -c "import sys; assert sys.version_info[:2] == (3, 12), sys.version"
if ($LASTEXITCODE -ne 0) { exit $LASTEXITCODE }

if (-not (Test-Path -LiteralPath $LockFile -PathType Leaf)) {
    throw "Missing dependency lock: $LockFile"
}
$LockInfo = Get-Item -LiteralPath $LockFile
if ($LockInfo.Length -lt 1024) {
    throw "Dependency lock is unexpectedly empty or incomplete: $LockFile"
}
if (-not (Select-String -LiteralPath $LockFile -SimpleMatch "--hash=sha256:" -Quiet)) {
    throw "Dependency lock does not contain package hashes: $LockFile"
}

& $Python -m pip install --disable-pip-version-check --quiet `
    --require-hashes --requirement $LockFile
if ($LASTEXITCODE -ne 0) { exit $LASTEXITCODE }

& $Python -m pip check
if ($LASTEXITCODE -ne 0) { exit $LASTEXITCODE }

& $Python (Join-Path $RepoRoot "scripts\check_requirements.py")
if ($LASTEXITCODE -ne 0) { exit $LASTEXITCODE }

& (Join-Path $RepoRoot "scripts\Frontend.ps1") -Mode Install
if ($LASTEXITCODE -ne 0) { exit $LASTEXITCODE }
& (Join-Path $RepoRoot "scripts\Frontend.ps1") -Mode Build
if ($LASTEXITCODE -ne 0) { exit $LASTEXITCODE }

$DataDirectory = Join-Path $RepoRoot ".localdata"
if (-not (Test-Path -LiteralPath $DataDirectory -PathType Container)) {
    New-Item -ItemType Directory -Path $DataDirectory | Out-Null
}

Push-Location $RepoRoot
try {
    $HadDatabaseUrl = Test-Path -LiteralPath "Env:MASS_PRODUCTION_QUALITY_VALIDATION_DATABASE_URL"
    if (-not $HadDatabaseUrl) {
        $env:MASS_PRODUCTION_QUALITY_VALIDATION_DATABASE_URL = "sqlite+pysqlite:///./.localdata/mass_production_quality_validation.sqlite3"
    }
    try {
        & $Python -m alembic upgrade head
        if ($LASTEXITCODE -ne 0) { exit $LASTEXITCODE }
    }
    finally {
        if (-not $HadDatabaseUrl) {
            Remove-Item -LiteralPath "Env:MASS_PRODUCTION_QUALITY_VALIDATION_DATABASE_URL" -ErrorAction SilentlyContinue
        }
    }
}
finally {
    Pop-Location
}

Write-Host "Mass Production Quality Validation bootstrap completed with locked Python/frontend dependencies, the local database, and the web build."
