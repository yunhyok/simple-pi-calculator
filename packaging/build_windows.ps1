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
if ($LASTEXITCODE -ne 0) { throw "pip install -e .[dev] failed" }

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
    if ($LASTEXITCODE -ne 0) { throw "Tests failed" }
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
    throw "tools\write_version_info.py not found"
}

# --- 6. PyInstaller build -----------------------------------------------
Write-Host "-- Running PyInstaller" -ForegroundColor Yellow
Push-Location (Join-Path $RepoRoot "packaging")
try {
    # distpath/workpath at the repo root: installer.iss reads ..\dist\SimplePICalculator
    & $VenvPython -m PyInstaller --noconfirm --clean simple_pi_calculator.spec --distpath ..\dist --workpath ..\build
    if ($LASTEXITCODE -ne 0) { throw "PyInstaller failed" }
} finally {
    Pop-Location
}

# --- 6b. Smoke test of the frozen app (windowed exe: read the report file) ---
$Exe = Join-Path $RepoRoot "dist\SimplePICalculator\SimplePICalculator.exe"
$Report = Join-Path $RepoRoot "build\selftest.txt"
$proc = Start-Process -FilePath $Exe -ArgumentList "--self-test", "--self-test-report", "`"$Report`"" -PassThru
if (-not $proc.WaitForExit(120000)) { $proc.Kill(); throw "Self-test timed out" }
if (Test-Path $Report) { Get-Content $Report | Write-Host }
if ($proc.ExitCode -ne 0) { throw "Self-test failed with exit code $($proc.ExitCode)" }

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
