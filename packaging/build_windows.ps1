<#
.SYNOPSIS
    Local Windows build script for Simple PI Calculator.

.DESCRIPTION
    Creates an isolated virtual environment, installs the package, builds the
    PyInstaller onedir bundle, and compiles the Inno Setup installer.
    Mirrors the steps run in .github/workflows/build-windows.yml so a
    developer can reproduce a CI build locally.

.PARAMETER Version
    Version string passed to Inno Setup as MyAppVersion (e.g. "0.1.0").
    Defaults to the value of simple_pi_calculator.__version__.

.PARAMETER SkipTests
    Skip running pytest before building.

.EXAMPLE
    powershell -ExecutionPolicy Bypass -File packaging\build_windows.ps1

.EXAMPLE
    powershell -ExecutionPolicy Bypass -File packaging\build_windows.ps1 -Version 0.1.0 -SkipTests
#>

[CmdletBinding()]
param(
    [string]$Version,
    [switch]$SkipTests
)

$ErrorActionPreference = "Stop"

# Resolve repo root as the parent of this script's directory (packaging\).
$RepoRoot = Split-Path -Parent $PSScriptRoot
Set-Location $RepoRoot

Write-Host "== Simple PI Calculator :: local Windows build ==" -ForegroundColor Cyan
Write-Host "Repo root: $RepoRoot"

# --- 1. Virtual environment -------------------------------------------
$VenvDir = Join-Path $RepoRoot ".venv-build"
if (-not (Test-Path $VenvDir)) {
    Write-Host "-- Creating virtual environment at $VenvDir" -ForegroundColor Yellow
    py -3.11 -m venv $VenvDir
}
$VenvPython = Join-Path $VenvDir "Scripts\python.exe"

& $VenvPython -m pip install --upgrade pip

# --- 2. Install the package (+ dev extras: pytest, pytest-qt, pyinstaller) --
Write-Host "-- Installing package (editable, dev extras)" -ForegroundColor Yellow
& $VenvPython -m pip install -e ".[dev]"
if ($LASTEXITCODE -ne 0) {
    Write-Host "  '.[dev]' extra not found; falling back to explicit deps." -ForegroundColor Yellow
    & $VenvPython -m pip install -e .
    & $VenvPython -m pip install pyinstaller pytest pytest-qt
}

# --- 3. Version -------------------------------------------------------
if (-not $Version) {
    $Version = & $VenvPython -c "import simple_pi_calculator as m; print(m.__version__)"
    $Version = $Version.Trim()
}
Write-Host "-- Building version $Version" -ForegroundColor Yellow

# --- 4. Tests -----------------------------------------------------------
if (-not $SkipTests) {
    Write-Host "-- Running tests (QT_QPA_PLATFORM=offscreen)" -ForegroundColor Yellow
    $env:QT_QPA_PLATFORM = "offscreen"
    & $VenvPython -m pytest -q
    Remove-Item Env:\QT_QPA_PLATFORM -ErrorAction SilentlyContinue
} else {
    Write-Host "-- Skipping tests (-SkipTests)" -ForegroundColor DarkYellow
}

# --- 5. Windows version resource for PyInstaller --------------------------
$VersionInfoScript = Join-Path $RepoRoot "tools\write_version_info.py"
$VersionInfoOut = Join-Path $RepoRoot "packaging\version_info.txt"
if (Test-Path $VersionInfoScript) {
    Write-Host "-- Generating version_info.txt" -ForegroundColor Yellow
    & $VenvPython $VersionInfoScript $VersionInfoOut
} else {
    Write-Host "  tools\write_version_info.py not found yet; skipping (PyInstaller will build without a version resource)." -ForegroundColor DarkYellow
}

# --- 6. PyInstaller build -----------------------------------------------
Write-Host "-- Running PyInstaller" -ForegroundColor Yellow
Push-Location (Join-Path $RepoRoot "packaging")
try {
    & $VenvPython -m PyInstaller --noconfirm --clean simple_pi_calculator.spec
} finally {
    Pop-Location
}

# --- 7. Inno Setup compile ------------------------------------------------
Write-Host "-- Compiling installer with Inno Setup" -ForegroundColor Yellow
$Iscc = Get-Command iscc.exe -ErrorAction SilentlyContinue
if (-not $Iscc) {
    $DefaultIscc = "C:\Program Files (x86)\Inno Setup 6\ISCC.exe"
    if (Test-Path $DefaultIscc) {
        $IsccPath = $DefaultIscc
    } else {
        throw "ISCC.exe (Inno Setup 6) not found on PATH or at '$DefaultIscc'. Install Inno Setup 6 (https://jrsoftware.org/isinfo.php) and re-run, or add it to PATH."
    }
} else {
    $IsccPath = $Iscc.Source
}

Push-Location (Join-Path $RepoRoot "packaging")
try {
    & $IsccPath "/DMyAppVersion=$Version" installer.iss
} finally {
    Pop-Location
}

$InstallerDir = Join-Path $RepoRoot "dist-installer"
Write-Host ""
Write-Host "== Build complete ==" -ForegroundColor Green
Write-Host "  Onedir app:  $RepoRoot\dist\SimplePICalculator\SimplePICalculator.exe"
Write-Host "  Installer:   $InstallerDir\SimplePICalculator-Setup-$Version.exe"
