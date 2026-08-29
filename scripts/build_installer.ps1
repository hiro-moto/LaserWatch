param(
    [switch]$SkipTests
)

$ErrorActionPreference = "Stop"
Set-Location (Split-Path $PSScriptRoot -Parent)

function Require-Command($name, $message) {
    if (-not (Get-Command $name -ErrorAction SilentlyContinue)) {
        throw $message
    }
}

Require-Command "uv" "uv was not found. Install it from https://docs.astral.sh/uv/ and reopen PowerShell."

Write-Host "==> Syncing Python 3.12 environment"
uv sync --extra build
if ($LASTEXITCODE -ne 0) { throw "uv sync failed" }

if (-not $SkipTests) {
    Write-Host "==> Running regression tests"
    & "$PSScriptRoot\run_tests.ps1"
}

Write-Host "==> Building portable application with PyInstaller"
uv run pyinstaller --noconfirm --clean LaserWatch.spec
if ($LASTEXITCODE -ne 0) { throw "PyInstaller failed" }

$isccCandidates = @(
    "$env:ProgramFiles(x86)\Inno Setup 6\ISCC.exe",
    "$env:ProgramFiles\Inno Setup 6\ISCC.exe"
)

$iscc = $null
foreach ($candidate in $isccCandidates) {
    if ($candidate -and (Test-Path $candidate)) {
        $iscc = $candidate
        break
    }
}

if (-not $iscc) {
    $cmd = Get-Command ISCC.exe -ErrorAction SilentlyContinue
    if ($cmd) { $iscc = $cmd.Source }
}

if (-not $iscc) {
    throw "Inno Setup 6 was not found. Install it with: winget install --id JRSoftware.InnoSetup -e"
}

Write-Host "==> Building installer with Inno Setup"
& $iscc ".\installer\LaserWatch.iss"
if ($LASTEXITCODE -ne 0) { throw "Inno Setup compilation failed" }

Write-Host ""
Write-Host "Build complete"
Write-Host "Portable app: dist\LaserWatch\LaserWatch.exe"
Write-Host "Installer:    installer\output\LaserWatch_Setup_0.8.4.exe"
