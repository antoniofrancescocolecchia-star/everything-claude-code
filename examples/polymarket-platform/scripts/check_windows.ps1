#Requires -Version 5.1
Set-StrictMode -Version Latest
$ErrorActionPreference = 'Continue'

# ---------------------------------------------------------------------------
# Locate project root (parent of scripts/)
# ---------------------------------------------------------------------------
$ScriptDir  = Split-Path -Parent $MyInvocation.MyCommand.Path
$ProjectDir = Split-Path -Parent $ScriptDir
$Failures   = 0

# ---------------------------------------------------------------------------
# Output helpers
# ---------------------------------------------------------------------------
function Write-Pass {
    param([string]$Msg)
    Write-Host "  [PASS] $Msg" -ForegroundColor Green
}

function Write-Fail {
    param([string]$Msg)
    Write-Host "  [FAIL] $Msg" -ForegroundColor Red
    $script:Failures++
}

function Write-Warn {
    param([string]$Msg)
    Write-Host "  [WARN] $Msg" -ForegroundColor Yellow
}

function Write-Section {
    param([string]$Title)
    Write-Host ""
    Write-Host "--- $Title ---" -ForegroundColor Cyan
}

Write-Host ""
Write-Host "=== Polymarket Platform - Environment Check ===" -ForegroundColor Cyan
Write-Host "Root: $ProjectDir" -ForegroundColor DarkGray

# ---------------------------------------------------------------------------
# Python
# ---------------------------------------------------------------------------
Write-Section "Python"
$PyFound = $false

foreach ($cand in @('py', 'python3.12', 'python')) {
    try {
        if ($cand -eq 'py') {
            $verStr = "$(& py -3.12 --version 2>&1)"
        }
        else {
            $verStr = "$(& $cand --version 2>&1)"
        }
        if ($verStr -match '3\.[1-9][2-9]') {
            Write-Pass "Python: $verStr"
            $PyFound = $true
            break
        }
    }
    catch {
        # try next candidate
    }
}

if (-not $PyFound) {
    Write-Fail "Python 3.12+ not found in PATH"
}

# ---------------------------------------------------------------------------
# Virtual environment
# ---------------------------------------------------------------------------
Write-Section "Virtual Environment"
$VenvPy = Join-Path $ProjectDir '.venv\Scripts\python.exe'

if (Test-Path $VenvPy) {
    Write-Pass ".venv exists"
    $pkgCheck = & $VenvPy -c "import polymarket_platform; print('ok')" 2>&1
    if ("$pkgCheck" -eq 'ok') {
        Write-Pass "polymarket_platform package importable"
    }
    else {
        Write-Fail "polymarket_platform not installed in .venv (run Start-Polymarket.cmd)"
    }
}
else {
    Write-Fail ".venv not found - run Start-Polymarket.cmd to create it"
}

# ---------------------------------------------------------------------------
# .env
# ---------------------------------------------------------------------------
Write-Section ".env"
$EnvFile = Join-Path $ProjectDir '.env'

if (Test-Path $EnvFile) {
    Write-Pass ".env file exists"

    function Get-EnvVal {
        param([string]$Key)
        $raw = Get-Content $EnvFile -Raw -ErrorAction SilentlyContinue
        if ($raw -match "(?m)^$Key=(.+)$") {
            return $Matches[1].Trim()
        }
        return $null
    }

    $tokenId = Get-EnvVal 'POLY_TOKEN_ID'
    if ($tokenId -and ($tokenId -ne 'YOUR_CLOB_TOKEN_ID')) {
        Write-Pass "POLY_TOKEN_ID is set"
    }
    else {
        Write-Fail "POLY_TOKEN_ID is missing or still the placeholder value"
    }

    $dryRun = Get-EnvVal 'BOT_DRY_RUN'
    if ($dryRun -eq 'false') {
        Write-Warn "BOT_DRY_RUN=false - live trading mode"
        $privKey = Get-EnvVal 'POLY_PRIVATE_KEY'
        if ($privKey -and ($privKey -ne '0xYOUR_PRIVATE_KEY')) {
            Write-Pass "POLY_PRIVATE_KEY is set (live mode)"
        }
        else {
            Write-Fail "POLY_PRIVATE_KEY missing but BOT_DRY_RUN=false"
        }
        $funder = Get-EnvVal 'POLY_FUNDER_ADDRESS'
        if ($funder -and ($funder -ne '0xYOUR_FUNDER_ADDRESS')) {
            Write-Pass "POLY_FUNDER_ADDRESS is set (live mode)"
        }
        else {
            Write-Fail "POLY_FUNDER_ADDRESS missing but BOT_DRY_RUN=false"
        }
    }
    else {
        Write-Pass "BOT_DRY_RUN=true (dry-run mode)"
    }
}
else {
    Write-Fail ".env not found - run Start-Polymarket.cmd to create it"
}

# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------
Write-Section "Tests"

if (Test-Path $VenvPy) {
    Write-Host "  Running pytest..." -ForegroundColor DarkGray
    $testOut  = & $VenvPy -m pytest -q --tb=short 2>&1
    $testExit = $LASTEXITCODE
    if ($testExit -eq 0) {
        $summary = ($testOut | Where-Object { $_ -match 'passed' } | Select-Object -Last 1)
        Write-Pass "pytest: $summary"
    }
    else {
        Write-Fail "pytest failed"
        $testOut | ForEach-Object { Write-Host "  $_" -ForegroundColor Red }
    }
}
else {
    Write-Warn "Skipping tests - .venv not found"
}

# ---------------------------------------------------------------------------
# Lint
# ---------------------------------------------------------------------------
Write-Section "Ruff Lint"

if (Test-Path $VenvPy) {
    $ruffOut  = & $VenvPy -m ruff check . 2>&1
    $ruffExit = $LASTEXITCODE
    if ($ruffExit -eq 0) {
        Write-Pass "ruff: no issues"
    }
    else {
        Write-Fail "ruff found issues"
        $ruffOut | ForEach-Object { Write-Host "  $_" -ForegroundColor Red }
    }
}
else {
    Write-Warn "Skipping lint - .venv not found"
}

# ---------------------------------------------------------------------------
# CLI smoke test
# ---------------------------------------------------------------------------
Write-Section "CLI"

if (Test-Path $VenvPy) {
    & $VenvPy -m polymarket_platform.cli --help 2>&1 | Out-Null
    if ($LASTEXITCODE -eq 0) {
        Write-Pass "polymarket_platform.cli --help works"
    }
    else {
        Write-Fail "polymarket_platform.cli --help failed"
    }
}
else {
    Write-Warn "Skipping CLI check - .venv not found"
}

# ---------------------------------------------------------------------------
# Summary
# ---------------------------------------------------------------------------
Write-Host ""
Write-Host "---------------------------------------------" -ForegroundColor DarkGray

if ($Failures -eq 0) {
    Write-Host "  All checks passed. Ready to run." -ForegroundColor Green
    exit 0
}
else {
    Write-Host "  $Failures check(s) failed. See above." -ForegroundColor Red
    Write-Host "  Run Start-Polymarket.cmd to bootstrap the environment." -ForegroundColor Yellow
    exit 1
}
