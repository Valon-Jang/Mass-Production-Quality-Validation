[CmdletBinding()]
param()

$ErrorActionPreference = "Stop"
Set-StrictMode -Version Latest

$RepoRoot = (Resolve-Path (Join-Path $PSScriptRoot "..")).Path
$Python = Join-Path $RepoRoot ".venv\Scripts\python.exe"
if (-not (Test-Path -LiteralPath $Python -PathType Leaf)) {
    & py -3.12 -m venv (Join-Path $RepoRoot ".venv")
    if ($LASTEXITCODE -ne 0) { exit $LASTEXITCODE }
}

& $Python -m pip install --disable-pip-version-check --quiet "pip-tools==7.6.1"
if ($LASTEXITCODE -ne 0) { exit $LASTEXITCODE }

Push-Location $RepoRoot
try {
    $RuntimeTarget = Join-Path $RepoRoot "requirements\runtime.lock"
    $DevTarget = Join-Path $RepoRoot "requirements\dev.lock"
    $RuntimeTemp = Join-Path $RepoRoot "requirements\.runtime.lock.tmp"
    $DevTemp = Join-Path $RepoRoot "requirements\.dev.lock.tmp"

    function Assert-LockFile {
        param([string]$Path)
        if (-not (Test-Path -LiteralPath $Path -PathType Leaf)) {
            throw "Lock generation did not create $Path"
        }
        if ((Get-Item -LiteralPath $Path).Length -lt 1024) {
            throw "Generated lock is unexpectedly empty or incomplete: $Path"
        }
        if (-not (Select-String -LiteralPath $Path -SimpleMatch "--hash=sha256:" -Quiet)) {
            throw "Generated lock has no hashes: $Path"
        }
    }

    function Normalize-LockHeader {
        param([string]$Path, [string]$TemporaryName, [string]$FinalName)
        $Content = [System.IO.File]::ReadAllText($Path)
        $Content = $Content.Replace("$RepoRoot\", "")
        $Content = $Content.Replace($TemporaryName, $FinalName)
        $Utf8WithoutBom = New-Object System.Text.UTF8Encoding($false)
        [System.IO.File]::WriteAllText($Path, $Content, $Utf8WithoutBom)
    }

    function Install-LockAtomically {
        param([string]$TemporaryPath, [string]$TargetPath)
        if (Test-Path -LiteralPath $TargetPath -PathType Leaf) {
            $BackupPath = "$TargetPath.backup"
            try {
                [System.IO.File]::Replace($TemporaryPath, $TargetPath, $BackupPath, $true)
            }
            finally {
                Remove-Item -LiteralPath $BackupPath -Force -ErrorAction SilentlyContinue
            }
        }
        else {
            [System.IO.File]::Move($TemporaryPath, $TargetPath)
        }
    }

    try {
        & $Python -m piptools compile --quiet --generate-hashes --resolver=backtracking --strip-extras `
            --output-file=$RuntimeTemp requirements\runtime.in
        if ($LASTEXITCODE -ne 0) { exit $LASTEXITCODE }
        Normalize-LockHeader -Path $RuntimeTemp `
            -TemporaryName ".runtime.lock.tmp" -FinalName "runtime.lock"
        Assert-LockFile -Path $RuntimeTemp

        & $Python -m piptools compile --quiet --generate-hashes --allow-unsafe `
            --resolver=backtracking --strip-extras `
            --output-file=$DevTemp requirements\dev.in
        if ($LASTEXITCODE -ne 0) { exit $LASTEXITCODE }
        Normalize-LockHeader -Path $DevTemp `
            -TemporaryName ".dev.lock.tmp" -FinalName "dev.lock"
        Assert-LockFile -Path $DevTemp

        Install-LockAtomically -TemporaryPath $RuntimeTemp -TargetPath $RuntimeTarget
        Install-LockAtomically -TemporaryPath $DevTemp -TargetPath $DevTarget
    }
    finally {
        Remove-Item -LiteralPath $RuntimeTemp -Force -ErrorAction SilentlyContinue
        Remove-Item -LiteralPath $DevTemp -Force -ErrorAction SilentlyContinue
    }
}
finally {
    Pop-Location
}
