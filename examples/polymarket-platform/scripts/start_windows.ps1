#Requires -Version 5.1
<#
.SYNOPSIS
    Bootstrap and launch the Polymarket Platform on Windows.

.DESCRIPTION
    - Detects project root from script location
    - Creates .venv with py -3.12 if missing
    - Installs/updates dependencies
    - Creates .env from .env.example if missing
    - Runs first-time config wizard if required settings are absent
    - Starts the trading engine (dry-run by default)
#>

Set-StrictMode -Version Latest
$ErrorActionPreference = 'Stop'

# ── Project root ──────────────────────────────────────────────────────────────
$ScriptDir  = Split-Path -Parent $MyInvocation.MyCommand.Path
$ProjectDir = Split-Path -Parent $ScriptDir

Set-Location $ProjectDir
Write-Host ""
Write-Host "=== Polymarket Platform ===" -ForegroundColor Cyan
Write-Host "    Root: $ProjectDir" -ForegroundColor DarkGray
Write-Host ""

# ── Python 3.12 ───────────────────────────────────────────────────────────────
$PyExe = $null
foreach ($candidate in @('py -3.12', 'python3.12', 'python')) {
    try {
        $ver = & cmd /c "$candidate --version 2>&1"
        if ($ver -match '3\.1[2-9]') {
            # Resolve actual executable path
            if ($candidate -eq 'py -3.12') {
                $PyExe = 'py'
                $PyArgs = @('-3.12')
            } else {
                $PyExe = $candidate
                $PyArgs = @()
            }
            Write-Host "Python found: $ver" -ForegroundColor Green
            break
        }
    } catch { }
}

if (-not $PyExe) {
    Write-Host ""
    Write-Host "ERROR: Python 3.12 not found." -ForegroundColor Red
    Write-Host "  Install it from https://www.python.org/downloads/" -ForegroundColor Yellow
    Write-Host "  Make sure 'Add to PATH' is checked during install." -ForegroundColor Yellow
    Write-Host ""
    Read-Host "Press Enter to exit"
    exit 1
}

# ── Virtual environment ───────────────────────────────────────────────────────
$VenvDir = Join-Path $ProjectDir '.venv'
$VenvPy  = Join-Path $VenvDir 'Scripts\python.exe'

if (-not (Test-Path $VenvPy)) {
    Write-Host "Creating virtual environment (.venv) ..." -ForegroundColor Yellow
    if ($PyExe -eq 'py') {
        & py -3.12 -m venv .venv
    } else {
        & $PyExe -m venv .venv
    }
    if ($LASTEXITCODE -ne 0) {
        Write-Host "ERROR: Failed to create virtual environment." -ForegroundColor Red
        Read-Host "Press Enter to exit"
        exit 1
    }
    Write-Host "Virtual environment created." -ForegroundColor Green
} else {
    Write-Host "Virtual environment found." -ForegroundColor Green
}

# ── Activate & upgrade pip ────────────────────────────────────────────────────
$ActivateScript = Join-Path $VenvDir 'Scripts\Activate.ps1'
if (Test-Path $ActivateScript) {
    . $ActivateScript
}

Write-Host "Upgrading pip ..." -ForegroundColor DarkGray
& $VenvPy -m pip install --quiet --upgrade pip

# ── Install / update dependencies ─────────────────────────────────────────────
$EggInfo = Join-Path $ProjectDir 'polymarket_platform.egg-info'
$Pyproject = Join-Path $ProjectDir 'pyproject.toml'

if (-not (Test-Path $EggInfo)) {
    Write-Host "Installing package and dependencies (first run, may take a minute) ..." -ForegroundColor Yellow
    & $VenvPy -m pip install --quiet -e ".[dev]"
    if ($LASTEXITCODE -ne 0) {
        Write-Host "ERROR: pip install failed. Check your internet connection." -ForegroundColor Red
        Read-Host "Press Enter to exit"
        exit 1
    }
    Write-Host "Dependencies installed." -ForegroundColor Green
} else {
    # Sync only if pyproject.toml is newer than egg-info
    $ProjMtime  = (Get-Item $PyProject).LastWriteTime
    $EggMtime   = (Get-Item $EggInfo).LastWriteTime
    if ($ProjMtime -gt $EggMtime) {
        Write-Host "pyproject.toml changed — syncing dependencies ..." -ForegroundColor Yellow
        & $VenvPy -m pip install --quiet -e ".[dev]"
        Write-Host "Dependencies updated." -ForegroundColor Green
    } else {
        Write-Host "Dependencies up to date." -ForegroundColor Green
    }
}

# ── .env setup ───────────────────────────────────────────────────────────────
$EnvFile     = Join-Path $ProjectDir '.env'
$EnvExample  = Join-Path $ProjectDir '.env.example'

if (-not (Test-Path $EnvFile)) {
    if (Test-Path $EnvExample) {
        Copy-Item $EnvExample $EnvFile
        Write-Host ".env created from .env.example" -ForegroundColor Yellow
    } else {
        New-Item -ItemType File -Path $EnvFile | Out-Null
        Write-Host ".env created (empty)" -ForegroundColor Yellow
    }
}

# ── First-run config wizard ───────────────────────────────────────────────────
$SetupScript = Join-Path $ScriptDir 'setup_config.ps1'

function Get-EnvValue {
    param([string]$Key)
    $content = Get-Content $EnvFile -Raw -ErrorAction SilentlyContinue
    if ($content -match "(?m)^$Key=(.+)$") {
        return $Matches[1].Trim()
    }
    return $null
}

$TokenId = Get-EnvValue 'POLY_TOKEN_ID'
$HasPlaceholderToken = $TokenId -eq 'YOUR_CLOB_TOKEN_ID' -or [string]::IsNullOrWhiteSpace($TokenId)

if ($HasPlaceholderToken) {
    Write-Host ""
    Write-Host "First-run setup: POLY_TOKEN_ID is not configured." -ForegroundColor Yellow
    Write-Host "Running setup wizard ..." -ForegroundColor Yellow
    & powershell.exe -NoProfile -ExecutionPolicy Bypass -File $SetupScript
    if ($LASTEXITCODE -ne 0) {
        Write-Host "Setup wizard failed or was cancelled." -ForegroundColor Red
        Read-Host "Press Enter to exit"
        exit 1
    }
}

# ── Dry-run safety check ──────────────────────────────────────────────────────
$DryRun    = Get-EnvValue 'BOT_DRY_RUN'
$PrivKey   = Get-EnvValue 'POLY_PRIVATE_KEY'
$HasLiveCreds = ($PrivKey -and $PrivKey -ne '0xYOUR_PRIVATE_KEY' -and $PrivKey.Length -gt 10)

if ($DryRun -eq 'false' -and -not $HasLiveCreds) {
    Write-Host ""
    Write-Host "WARNING: BOT_DRY_RUN=false but POLY_PRIVATE_KEY is not set." -ForegroundColor Red
    Write-Host "         Switching to dry-run mode for this session." -ForegroundColor Yellow
    $env:BOT_DRY_RUN = 'true'
}

if ($DryRun -ne 'false') {
    Write-Host ""
    Write-Host "Mode: DRY RUN (paper trading, no real orders)" -ForegroundColor Cyan
} else {
    Write-Host ""
    Write-Host "Mode: LIVE TRADING" -ForegroundColor Red
    Write-Host "      Real orders will be placed on Polymarket." -ForegroundColor Red
    Write-Host ""
    $confirm = Read-Host "Type YES to continue with live trading"
    if ($confirm -ne 'YES') {
        Write-Host "Aborted." -ForegroundColor Yellow
        exit 0
    }
}

# ── Launch ────────────────────────────────────────────────────────────────────
Write-Host ""
Write-Host "Starting Polymarket Platform ..." -ForegroundColor Green
Write-Host "(Press Ctrl+C to stop)" -ForegroundColor DarkGray
Write-Host ""

& $VenvPy -m polymarket_platform.cli run
