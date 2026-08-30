[CmdletBinding()]
param()

$ErrorActionPreference = "Stop"
Set-StrictMode -Version Latest

$RepoRoot = (Resolve-Path (Join-Path $PSScriptRoot "..")).Path
$ValidationRoot = Join-Path $RepoRoot ".validation"
$ValidationName = "clean-lock-" + [System.Guid]::NewGuid().ToString("N")
$VenvPath = Join-Path $ValidationRoot $ValidationName
$FreshPython = Join-Path $VenvPath "Scripts\python.exe"
$LockFile = Join-Path $RepoRoot "requirements\dev.lock"

New-Item -ItemType Directory -Path $ValidationRoot -Force | Out-Null

try {
    & py -3.12 -m venv $VenvPath
    if ($LASTEXITCODE -ne 0) { exit $LASTEXITCODE }

    & $FreshPython -m pip install --disable-pip-version-check --quiet `
        --require-hashes --requirement $LockFile
    if ($LASTEXITCODE -ne 0) { exit $LASTEXITCODE }

    & $FreshPython -m pip check
    if ($LASTEXITCODE -ne 0) { exit $LASTEXITCODE }

    & $FreshPython -c `
        "from importlib.metadata import version; assert version('pip') == '25.0.1'; assert version('setuptools') == '82.0.1'"
    if ($LASTEXITCODE -ne 0) { exit $LASTEXITCODE }

    Write-Host "Clean Python 3.12 lock installation passed."
}
finally {
    $ResolvedValidationRoot = [System.IO.Path]::GetFullPath($ValidationRoot).TrimEnd('\') + '\'
    $ResolvedVenv = [System.IO.Path]::GetFullPath($VenvPath)
    if (-not $ResolvedVenv.StartsWith($ResolvedValidationRoot, [System.StringComparison]::OrdinalIgnoreCase)) {
        throw "Refusing to clean a validation path outside the workspace validation directory."
    }
    if (Test-Path -LiteralPath $ResolvedVenv -PathType Container) {
        Remove-Item -LiteralPath $ResolvedVenv -Recurse -Force
    }
}
