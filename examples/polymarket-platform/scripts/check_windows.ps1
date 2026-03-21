#Requires -Version 5.1
<#
.SYNOPSIS
    Validate the Polymarket Platform environment on Windows.

.DESCRIPTION
    Checks:
      - Python 3.12 available
      - .venv exists and has the package installed
      - .env exists and required variables are set
      - Tests pass
      - Ruff lint passes
    Exits with code 0 on full pass, 1 on any failure.
#>

Set-StrictMode -Version Latest
$ErrorActionPreference = 'Continue'  # Keep going to show all issues

$ScriptDir  = Split-Path -Parent $MyInvocation.MyCommand.Path
$ProjectDir = Split-Path -Parent $ScriptDir
$Failures   = 0

function Pass  { param([string]$msg) Write-Host "  [PASS] $msg" -ForegroundColor Green }
function Fail  { param([string]$msg) Write-Host "  [FAIL] $msg" -ForegroundColor Red; $script:Failures++ }
function Warn  { param([string]$msg) Write-Host "  [WARN] $msg" -ForegroundColor Yellow }
function Section { param([string]$title) Write-Host ""; Write-Host "--- $title ---" -ForegroundColor Cyan }

Write-Host ""
Write-Host "=== Polymarket Platform — Environment Check ===" -ForegroundColor Cyan
Write-Host "    Root: $ProjectDir" -ForegroundColor DarkGray

# ── Python ────────────────────────────────────────────────────────────────────
Section "Python"
$PyFound = $false
foreach ($candidate in @('py -3.12', 'python3.12', 'python')) {
    try {
        $ver = & cmd /c "$candidate --version 2>&1"
        if ($ver -match '3\.1[2-9]') {
            Pass "Python: $ver"
            $PyFound = $true
            break
        }
    } catch { }
}
if (-not $PyFound) { Fail "Python 3.12 not found in PATH" }

# ── Virtual environment ───────────────────────────────────────────────────────
Section "Virtual Environment"
$VenvPy = Join-Path $ProjectDir '.venv\Scripts\python.exe'

if (Test-Path $VenvPy) {
    Pass ".venv exists at .venv\"
    $pkgCheck = & $VenvPy -c "import polymarket_platform; print('ok')" 2>&1
    if ($pkgCheck -eq 'ok') {
        Pass "polymarket_platform package importable"
    } else {
        Fail "polymarket_platform not installed in .venv (run Start-Polymarket.cmd to install)"
    }
} else {
    Fail ".venv not found — run Start-Polymarket.cmd to create it"
}

# ── .env ─────────────────────────────────────────────────────────────────────
Section ".env"
$EnvFile = Join-Path $ProjectDir '.env'

if (Test-Path $EnvFile) {
    Pass ".env file exists"

    function Get-EnvVal {
        param([string]$Key)
        $content = Get-Content $EnvFile -Raw -ErrorAction SilentlyContinue
        if ($content -match "(?m)^$Key=(.+)$") { return $Matches[1].Trim() }
        return $null
    }

    # POLY_TOKEN_ID is the only truly required variable
    $tokenId = Get-EnvVal 'POLY_TOKEN_ID'
    if ($tokenId -and $tokenId -ne 'YOUR_CLOB_TOKEN_ID') {
        Pass "POLY_TOKEN_ID is set"
    } else {
        Fail "POLY_TOKEN_ID is missing or is still the placeholder value"
    }

    $dryRun = Get-EnvVal 'BOT_DRY_RUN'
    if ($dryRun -eq 'false') {
        Warn "BOT_DRY_RUN=false — live trading mode"
        $privKey = Get-EnvVal 'POLY_PRIVATE_KEY'
        if ($privKey -and $privKey -ne '0xYOUR_PRIVATE_KEY') {
            Pass "POLY_PRIVATE_KEY is set (live mode)"
        } else {
            Fail "POLY_PRIVATE_KEY missing but BOT_DRY_RUN=false"
        }
        $funder = Get-EnvVal 'POLY_FUNDER_ADDRESS'
        if ($funder -and $funder -ne '0xYOUR_FUNDER_ADDRESS') {
            Pass "POLY_FUNDER_ADDRESS is set (live mode)"
        } else {
            Fail "POLY_FUNDER_ADDRESS missing but BOT_DRY_RUN=false"
        }
    } else {
        Pass "BOT_DRY_RUN=true (dry-run / paper trading)"
    }
} else {
    Fail ".env not found — run Start-Polymarket.cmd to create it"
}

# ── Tests ─────────────────────────────────────────────────────────────────────
Section "Tests"
if (Test-Path $VenvPy) {
    Write-Host "  Running pytest ..." -ForegroundColor DarkGray
    $testOut = & $VenvPy -m pytest -q --tb=short 2>&1 | Out-String
    if ($LASTEXITCODE -eq 0) {
        $summary = ($testOut -split "`n" | Where-Object { $_ -match 'passed' } | Select-Object -Last 1).Trim()
        Pass "pytest: $summary"
    } else {
        Fail "pytest failed"
        Write-Host $testOut -ForegroundColor Red
    }
} else {
    Warn "Skipping tests — .venv not found"
}

# ── Lint ──────────────────────────────────────────────────────────────────────
Section "Ruff Lint"
if (Test-Path $VenvPy) {
    $ruffOut = & $VenvPy -m ruff check . 2>&1 | Out-String
    if ($LASTEXITCODE -eq 0) {
        Pass "ruff: no issues"
    } else {
        Fail "ruff found issues:"
        Write-Host $ruffOut -ForegroundColor Red
    }
} else {
    Warn "Skipping lint — .venv not found"
}

# ── CLI ───────────────────────────────────────────────────────────────────────
Section "CLI"
if (Test-Path $VenvPy) {
    $cliOut = & $VenvPy -m polymarket_platform.cli --help 2>&1 | Out-String
    if ($LASTEXITCODE -eq 0) {
        Pass "polymarket_platform.cli --help works"
    } else {
        Fail "polymarket_platform.cli --help failed"
    }
} else {
    Warn "Skipping CLI check — .venv not found"
}

# ── Summary ───────────────────────────────────────────────────────────────────
Write-Host ""
Write-Host "─────────────────────────────────────────────" -ForegroundColor DarkGray
if ($Failures -eq 0) {
    Write-Host "  All checks passed. Ready to run." -ForegroundColor Green
    exit 0
} else {
    Write-Host "  $Failures check(s) failed. See above." -ForegroundColor Red
    Write-Host "  Run Start-Polymarket.cmd to bootstrap the environment." -ForegroundColor Yellow
    exit 1
}
