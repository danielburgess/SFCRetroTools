<#
.SYNOPSIS
  One-shot dev-environment setup (Windows).

.DESCRIPTION
  Installs everything needed to build the ROM and run the script editor:
    1. Installs `uv` (Astral's Python toolchain manager) if it is missing.
    2. Creates the project virtual environment (.venv) with a matching
       CPython (uv downloads it if needed).
    3. Installs retrotool FROM PyPI:
         retrotool[all]     -> build engine + bundled libsfx/asar/bass/xdelta
         retrotool[editor]  -> `retrotool edit` GUI (pywebview uses the
                               EdgeChromium backend on Windows)

  Run this ONCE per machine (or after dependencies change). Adapted from the
  Rushing Beat Shura translation project's contributor scripts — generic: it
  reads the ROM path from project.toml, so it works in any retrotool project.
#>
[CmdletBinding()]
param()

$ErrorActionPreference = 'Stop'
Set-StrictMode -Version Latest

$RetrotoolSpec = 'retrotool[all,editor]>=0.9.3'
$PyVersion = '3.13'

# Repo root = two levels up from this script (scripts\win\..\..).
$RepoRoot = Resolve-Path (Join-Path $PSScriptRoot '..\..')
Set-Location $RepoRoot
Write-Host "==> Project: $RepoRoot" -ForegroundColor Cyan

if (-not (Test-Path 'project.toml')) {
    throw "no project.toml in $RepoRoot — run this from the project checkout."
}

function Test-Command($name) {
    return [bool](Get-Command $name -ErrorAction SilentlyContinue)
}

# --- 1. Ensure uv is installed ---------------------------------------------
if (-not (Test-Command 'uv')) {
    Write-Host "==> Installing uv (Python toolchain manager)..." -ForegroundColor Cyan
    try {
        Invoke-RestMethod https://astral.sh/uv/install.ps1 | Invoke-Expression
    } catch {
        throw "Failed to install uv automatically. Install it from https://docs.astral.sh/uv/ and re-run this script. ($_)"
    }
    $uvBin = Join-Path $env:USERPROFILE '.local\bin'
    if (Test-Path (Join-Path $uvBin 'uv.exe')) { $env:Path = "$uvBin;$env:Path" }
    if (-not (Test-Command 'uv')) {
        throw "uv was installed but is not on PATH in this session. Close and reopen your terminal, then re-run setup."
    }
}
Write-Host ("==> uv: " + (uv --version)) -ForegroundColor Green

# --- 2. Create the virtual environment (.venv) -----------------------------
Write-Host "==> Creating .venv (CPython $PyVersion)..." -ForegroundColor Cyan
uv venv --python $PyVersion

# --- 3. Install dependencies FROM PyPI -------------------------------------
Write-Host "==> Installing $RetrotoolSpec from PyPI..." -ForegroundColor Cyan
uv pip install $RetrotoolSpec

# --- 4. Verify the environment ---------------------------------------------
Write-Host ""
Write-Host "==> Verifying the environment:" -ForegroundColor Cyan
$fail = $false
function Check($label, [scriptblock]$test) {
    if (& $test) { Write-Host "  ✓ $label" -ForegroundColor Green }
    else { Write-Host "  ✗ $label" -ForegroundColor Red; $script:fail = $true }
}
$py = '.venv\Scripts\python.exe'
Check '.venv python'         { Test-Path $py }
Check 'retrotool CLI'        { Test-Path '.venv\Scripts\retrotool.exe' }
Check 'retrotool importable' { & $py -c 'import retrotool' 2>$null; $LASTEXITCODE -eq 0 }
Check 'Pillow (PIL)'         { & $py -c 'import PIL' 2>$null; $LASTEXITCODE -eq 0 }
Check 'pywebview (editor)'   { & $py -c 'import webview' 2>$null; $LASTEXITCODE -eq 0 }

# Source ROM path from project.toml (generic). In THIS example the "game"
# is synthesized by tools/make_demo_rom.py.
$romPath = & $py -c "import tomllib; print(tomllib.load(open('project.toml','rb'))['rom']['file'])" 2>$null
if ($romPath) {
    if (Test-Path $romPath) {
        Write-Host "  ✓ source ROM ($romPath)" -ForegroundColor Green
    } else {
        Write-Host "  ! source ROM MISSING — generate the demo ROM with:" -ForegroundColor Yellow
        Write-Host "      $py tools\make_demo_rom.py" -ForegroundColor Yellow
        Write-Host "    (a real project would say: place your legally-obtained copy at $romPath)" -ForegroundColor Yellow
    }
}

if ($fail) { throw "Setup FAILED one or more checks - review the output above." }
Write-Host ""
Write-Host "==> OK. Environment ready. Next steps (see README.md):" -ForegroundColor Green
Write-Host "    $py tools\make_demo_rom.py          (demo source ROM)"
Write-Host "    .venv\Scripts\retrotool build .      (build the translated ROM)"
Write-Host "    .venv\Scripts\retrotool edit .       (GUI script editor)"
