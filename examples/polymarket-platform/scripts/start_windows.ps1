#Requires -Version 5.1
Set-StrictMode -Version Latest
$ErrorActionPreference = 'Stop'

# ---------------------------------------------------------------------------
# Locate project root (parent of scripts/)
# ---------------------------------------------------------------------------
$ScriptDir  = Split-Path -Parent $MyInvocation.MyCommand.Path
$ProjectDir = Split-Path -Parent $ScriptDir

Set-Location $ProjectDir
Write-Host ""
Write-Host "=== Polymarket Platform ===" -ForegroundColor Cyan
Write-Host "Root: $ProjectDir" -ForegroundColor DarkGray
Write-Host ""

# ---------------------------------------------------------------------------
# Locate Python 3.12+
# ---------------------------------------------------------------------------
$PyExe  = $null
$PyArgs = @()

foreach ($cand in @('py', 'python3.12', 'python')) {
    try {
        if ($cand -eq 'py') {
            $verStr = "$(& py -3.12 --version 2>&1)"
        }
        else {
            $verStr = "$(& $cand --version 2>&1)"
        }
        if ($verStr -match '3\.[1-9][2-9]') {
            if ($cand -eq 'py') {
                $PyExe  = 'py'
                $PyArgs = @('-3.12')
            }
            else {
                $PyExe  = $cand
                $PyArgs = @()
            }
            Write-Host "Python found: $verStr" -ForegroundColor Green
            break
        }
    }
    catch {
        # try next candidate
    }
}

if (-not $PyExe) {
    Write-Host "ERROR: Python 3.12+ not found." -ForegroundColor Red
    Write-Host "Install from https://www.python.org/downloads/ and tick 'Add to PATH'." -ForegroundColor Yellow
    Read-Host "Press Enter to exit"
    exit 1
}

# ---------------------------------------------------------------------------
# Virtual environment
# ---------------------------------------------------------------------------
$VenvDir = Join-Path $ProjectDir '.venv'
$VenvPy  = Join-Path $VenvDir 'Scripts\python.exe'

if (-not (Test-Path $VenvPy)) {
    Write-Host "Creating virtual environment (.venv)..." -ForegroundColor Yellow
    if ($PyExe -eq 'py') {
        & py -3.12 -m venv .venv
    }
    else {
        & $PyExe -m venv .venv
    }
    if ($LASTEXITCODE -ne 0) {
        Write-Host "ERROR: Failed to create virtual environment." -ForegroundColor Red
        Read-Host "Press Enter to exit"
        exit 1
    }
    Write-Host "Virtual environment created." -ForegroundColor Green
}
else {
    Write-Host "Virtual environment found." -ForegroundColor Green
}

# ---------------------------------------------------------------------------
# Activate and upgrade pip
# ---------------------------------------------------------------------------
$ActivateScript = Join-Path $VenvDir 'Scripts\Activate.ps1'
if (Test-Path $ActivateScript) {
    . $ActivateScript
}

Write-Host "Upgrading pip..." -ForegroundColor DarkGray
& $VenvPy -m pip install --quiet --upgrade pip

# ---------------------------------------------------------------------------
# Install / update package (editable install)
# ---------------------------------------------------------------------------
$EggInfo   = Join-Path $ProjectDir 'polymarket_platform.egg-info'
$Pyproject = Join-Path $ProjectDir 'pyproject.toml'

$needsInstall = $false
if (-not (Test-Path $EggInfo)) {
    $needsInstall = $true
}
else {
    $projTime = (Get-Item $Pyproject).LastWriteTime
    $eggTime  = (Get-Item $EggInfo).LastWriteTime
    if ($projTime -gt $eggTime) {
        $needsInstall = $true
    }
}

if ($needsInstall) {
    Write-Host "Installing package and dependencies..." -ForegroundColor Yellow
    & $VenvPy -m pip install --quiet -e ".[dev]"
    if ($LASTEXITCODE -ne 0) {
        Write-Host "ERROR: pip install failed. Check your internet connection." -ForegroundColor Red
        Read-Host "Press Enter to exit"
        exit 1
    }
    Write-Host "Dependencies installed." -ForegroundColor Green
}
else {
    Write-Host "Dependencies up to date." -ForegroundColor Green
}

# ---------------------------------------------------------------------------
# Create .env from .env.example if not present
# ---------------------------------------------------------------------------
$EnvFile    = Join-Path $ProjectDir '.env'
$EnvExample = Join-Path $ProjectDir '.env.example'

if (-not (Test-Path $EnvFile)) {
    if (Test-Path $EnvExample) {
        Copy-Item $EnvExample $EnvFile
        Write-Host ".env created from .env.example" -ForegroundColor Yellow
    }
    else {
        New-Item -ItemType File -Path $EnvFile | Out-Null
        Write-Host ".env created (empty)" -ForegroundColor Yellow
    }
}

# ---------------------------------------------------------------------------
# Helper: read a value from .env
# ---------------------------------------------------------------------------
function Get-EnvValue {
    param([string]$Key)
    $raw = Get-Content $EnvFile -Raw -ErrorAction SilentlyContinue
    if ($raw -match "(?m)^$Key=(.+)$") {
        return $Matches[1].Trim()
    }
    return $null
}

# ---------------------------------------------------------------------------
# Run setup wizard when required values are missing
# ---------------------------------------------------------------------------
$TokenId      = Get-EnvValue 'POLY_TOKEN_ID'
$placeholders = @('YOUR_CLOB_TOKEN_ID', '', $null)
$needsWizard  = ($placeholders -contains $TokenId)

if ($needsWizard) {
    Write-Host ""
    Write-Host "First-run: POLY_TOKEN_ID is not configured. Launching setup wizard..." -ForegroundColor Yellow
    $SetupScript = Join-Path $ScriptDir 'setup_config.ps1'
    & powershell.exe -NoProfile -ExecutionPolicy Bypass -File $SetupScript
    if ($LASTEXITCODE -ne 0) {
        Write-Host "Setup wizard failed or was cancelled." -ForegroundColor Red
        Read-Host "Press Enter to exit"
        exit 1
    }
}

# ---------------------------------------------------------------------------
# Run environment validation
# ---------------------------------------------------------------------------
$CheckScript = Join-Path $ScriptDir 'check_windows.ps1'
if (Test-Path $CheckScript) {
    Write-Host ""
    Write-Host "Running environment validation..." -ForegroundColor DarkGray
    & powershell.exe -NoProfile -ExecutionPolicy Bypass -File $CheckScript
    if ($LASTEXITCODE -ne 0) {
        Write-Host "Environment validation failed. See messages above." -ForegroundColor Red
        Read-Host "Press Enter to exit"
        exit 1
    }
}

# ---------------------------------------------------------------------------
# Dry-run safety gate
# ---------------------------------------------------------------------------
$DryRun  = Get-EnvValue 'BOT_DRY_RUN'
$PrivKey = Get-EnvValue 'POLY_PRIVATE_KEY'
$liveCreds = ($PrivKey -and ($PrivKey -ne '0xYOUR_PRIVATE_KEY') -and ($PrivKey.Length -gt 10))

if (($DryRun -eq 'false') -and (-not $liveCreds)) {
    Write-Host ""
    Write-Host "WARNING: BOT_DRY_RUN=false but POLY_PRIVATE_KEY is not set." -ForegroundColor Red
    Write-Host "Switching to dry-run mode for this session." -ForegroundColor Yellow
    $env:BOT_DRY_RUN = 'true'
    $DryRun = 'true'
}

if ($DryRun -ne 'false') {
    Write-Host ""
    Write-Host "Mode: DRY RUN (paper trading, no real orders)" -ForegroundColor Cyan
}
else {
    Write-Host ""
    Write-Host "Mode: LIVE TRADING" -ForegroundColor Red
    Write-Host "Real orders will be placed on Polymarket." -ForegroundColor Red
    Write-Host ""
    $confirm = Read-Host "Type YES to continue with live trading"
    if ($confirm -ne 'YES') {
        Write-Host "Aborted." -ForegroundColor Yellow
        exit 0
    }
}

# ---------------------------------------------------------------------------
# Launch
# ---------------------------------------------------------------------------
Write-Host ""
Write-Host "Starting Polymarket Platform..." -ForegroundColor Green
Write-Host "(Press Ctrl+C to stop)" -ForegroundColor DarkGray
Write-Host ""

& $VenvPy -m polymarket_platform.cli run
