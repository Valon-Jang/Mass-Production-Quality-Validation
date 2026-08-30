[CmdletBinding()]
param(
    [ValidateRange(1024, 65535)]
    [int]$Port = 8765,
    [string]$DataRoot = "",
    [ValidateRange(5, 120)]
    [int]$StartupTimeoutSeconds = 30,
    [switch]$NoBrowser,
    [switch]$DryRun
)

$ErrorActionPreference = "Stop"
Set-StrictMode -Version Latest

$CodeRoot = (Resolve-Path $PSScriptRoot).Path
$Python = Join-Path $CodeRoot ".venv\Scripts\python.exe"
$BackendRoot = Join-Path $CodeRoot "backend"
$FrontendRoot = Join-Path $CodeRoot "frontend\dist"
if ([string]::IsNullOrWhiteSpace($DataRoot)) {
    if ([string]::IsNullOrWhiteSpace($env:LOCALAPPDATA)) {
        throw "LOCALAPPDATA is required for the default current-user data path."
    }
    $DataRoot = Join-Path $env:LOCALAPPDATA "Mass Production Quality Validation\data"
}
$DataRoot = [System.IO.Path]::GetFullPath($DataRoot)
$CodePrefix = $CodeRoot.TrimEnd('\') + '\'
$DataPrefix = $DataRoot.TrimEnd('\') + '\'
if ($DataRoot.Equals($CodeRoot, [System.StringComparison]::OrdinalIgnoreCase) -or
    $DataPrefix.StartsWith($CodePrefix, [System.StringComparison]::OrdinalIgnoreCase) -or
    $CodePrefix.StartsWith($DataPrefix, [System.StringComparison]::OrdinalIgnoreCase)) {
    throw "Mass Production Quality Validation code and data roots must be separate."
}

$BrowserUrl = "http://127.0.0.1:$Port/"
$HealthUrl = "http://127.0.0.1:$Port/api/v1/health/live"
if ($DryRun) {
    [ordered]@{
        action = "launch"
        dry_run = $true
        host = "127.0.0.1"
        port = $Port
        code_root = $CodeRoot
        data_root = $DataRoot
        browser = (-not $NoBrowser)
        persistent_os_integration = $false
    } | ConvertTo-Json -Compress
    return
}

if (-not (Test-Path -LiteralPath $Python -PathType Leaf)) {
    throw "The private Python runtime is missing. Reinstall or update Mass Production Quality Validation."
}
if (-not (Test-Path -LiteralPath (Join-Path $FrontendRoot "index.html") -PathType Leaf)) {
    throw "The prebuilt Mass Production Quality Validation frontend is missing."
}

function Get-MassProductionQualityValidationHealth {
    try {
        $Health = Invoke-RestMethod -Uri $HealthUrl -Method Get -TimeoutSec 1
        return ($Health.status -eq "ok" -and $Health.service -eq "Mass Production Quality Validation")
    }
    catch {
        return $false
    }
}

function Open-MassProductionQualityValidationBrowser {
    if (-not $NoBrowser) {
        Start-Process $BrowserUrl
    }
}

if (Get-MassProductionQualityValidationHealth) {
    Open-MassProductionQualityValidationBrowser
    return
}

$MutexName = "Local\MassProductionQualityValidationLocalhostPort$Port"
$Mutex = New-Object System.Threading.Mutex($false, $MutexName)
$Acquired = $false
$Server = $null
try {
    $Acquired = $Mutex.WaitOne(0, $false)
    if (-not $Acquired) {
        $Deadline = [DateTime]::UtcNow.AddSeconds($StartupTimeoutSeconds)
        while ([DateTime]::UtcNow -lt $Deadline) {
            if (Get-MassProductionQualityValidationHealth) {
                Open-MassProductionQualityValidationBrowser
                return
            }
            Start-Sleep -Milliseconds 200
        }
        throw "Another Mass Production Quality Validation launcher owns this localhost port but did not become ready."
    }

    $DatabaseRoot = Join-Path $DataRoot "database"
    $OriginalRoot = Join-Path $DataRoot "original-files"
    $StagingRoot = Join-Path $DataRoot "intake-staging"
    New-Item -ItemType Directory -Path $DatabaseRoot -Force | Out-Null
    New-Item -ItemType Directory -Path $OriginalRoot -Force | Out-Null
    New-Item -ItemType Directory -Path $StagingRoot -Force | Out-Null
    $DatabasePath = (Join-Path $DatabaseRoot "mass_production_quality_validation.sqlite3").Replace('\', '/')

    $env:MASS_PRODUCTION_QUALITY_VALIDATION_ENVIRONMENT = "production"
    $env:MASS_PRODUCTION_QUALITY_VALIDATION_DATABASE_URL = "sqlite+pysqlite:///$DatabasePath"
    $env:MASS_PRODUCTION_QUALITY_VALIDATION_ORIGINAL_FILE_STORE_ROOT = $OriginalRoot
    $env:MASS_PRODUCTION_QUALITY_VALIDATION_INTAKE_STAGING_ROOT = $StagingRoot
    $env:MASS_PRODUCTION_QUALITY_VALIDATION_FRONTEND_DIST_PATH = $FrontendRoot

    Push-Location $CodeRoot
    try {
        & $Python -m alembic upgrade head
        if ($LASTEXITCODE -ne 0) {
            throw "Mass Production Quality Validation database migration failed."
        }
        $ServerArguments = @(
            "-m", "uvicorn", "app.main:app",
            "--app-dir", "backend",
            "--host", "127.0.0.1",
            "--port", $Port.ToString()
        )
        $Server = Start-Process -FilePath $Python -ArgumentList $ServerArguments `
            -PassThru -WindowStyle Hidden -WorkingDirectory $CodeRoot
    }
    finally {
        Pop-Location
    }

    $Deadline = [DateTime]::UtcNow.AddSeconds($StartupTimeoutSeconds)
    while ([DateTime]::UtcNow -lt $Deadline) {
        if ($Server.HasExited) {
            throw "Mass Production Quality Validation stopped before the localhost endpoint became ready."
        }
        if (Get-MassProductionQualityValidationHealth) {
            Open-MassProductionQualityValidationBrowser
            $Server.WaitForExit()
            if ($Server.ExitCode -ne 0) { exit $Server.ExitCode }
            return
        }
        Start-Sleep -Milliseconds 200
    }
    throw "Mass Production Quality Validation did not become ready within the configured startup bound."
}
finally {
    if ($null -ne $Server -and -not $Server.HasExited) {
        Stop-Process -Id $Server.Id -Force -ErrorAction SilentlyContinue
        $Server.WaitForExit()
    }
    if ($Acquired) {
        $Mutex.ReleaseMutex()
    }
    $Mutex.Dispose()
}
