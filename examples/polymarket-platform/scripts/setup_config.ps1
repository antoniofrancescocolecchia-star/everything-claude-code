#Requires -Version 5.1
Set-StrictMode -Version Latest
$ErrorActionPreference = 'Stop'

# ---------------------------------------------------------------------------
# Locate project root (parent of scripts/)
# ---------------------------------------------------------------------------
$ScriptDir  = Split-Path -Parent $MyInvocation.MyCommand.Path
$ProjectDir = Split-Path -Parent $ScriptDir
$EnvFile    = Join-Path $ProjectDir '.env'

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

function Get-EnvLine {
    param([string]$Key)
    if (-not (Test-Path $EnvFile)) {
        return $null
    }
    $raw = Get-Content $EnvFile -Raw -ErrorAction SilentlyContinue
    if ($raw -match "(?m)^$Key=(.*)$") {
        return $Matches[1].Trim()
    }
    return $null
}

function Set-EnvLine {
    param([string]$Key, [string]$Value)
    $raw = ''
    if (Test-Path $EnvFile) {
        $raw = Get-Content $EnvFile -Raw -ErrorAction SilentlyContinue
        if (-not $raw) {
            $raw = ''
        }
    }
    $escaped = [regex]::Escape($Key)
    if ($raw -match "(?m)^$escaped=") {
        $raw = $raw -replace "(?m)^$escaped=.*$", "$Key=$Value"
    }
    else {
        if (($raw.Length -gt 0) -and (-not $raw.EndsWith("`n"))) {
            $raw = $raw + "`n"
        }
        $raw = $raw + "$Key=$Value`n"
    }
    [System.IO.File]::WriteAllText($EnvFile, $raw, [System.Text.Encoding]::UTF8)
}

function Test-Placeholder {
    param([string]$Value)
    if ([string]::IsNullOrWhiteSpace($Value)) {
        return $true
    }
    $placeholders = @('YOUR_CLOB_TOKEN_ID', '0xYOUR_PRIVATE_KEY', '0xYOUR_FUNDER_ADDRESS')
    return ($placeholders -contains $Value)
}

function Read-RequiredValue {
    param(
        [string]$Key,
        [string]$Label,
        [string]$Hint
    )
    $current = Get-EnvLine $Key
    if (-not (Test-Placeholder $current)) {
        Write-Host "  $Label : [already set, keeping]" -ForegroundColor DarkGray
        return $current
    }
    Write-Host ""
    if ($Hint) {
        Write-Host "  $Hint" -ForegroundColor DarkGray
    }
    $val = (Read-Host "  $Label").Trim()
    if ([string]::IsNullOrWhiteSpace($val)) {
        Write-Host "  Skipping $Key - you can set it manually in .env later." -ForegroundColor Yellow
        return $null
    }
    return $val
}

function Read-OptionalValue {
    param(
        [string]$Key,
        [string]$Label,
        [string]$Default,
        [string]$Hint
    )
    $current = Get-EnvLine $Key
    if (-not (Test-Placeholder $current)) {
        Write-Host "  $Label : [already set, keeping]" -ForegroundColor DarkGray
        return $current
    }
    Write-Host ""
    if ($Hint) {
        Write-Host "  $Hint" -ForegroundColor DarkGray
    }
    $val = (Read-Host "  $Label [$Default]").Trim()
    if ([string]::IsNullOrWhiteSpace($val)) {
        return $Default
    }
    return $val
}

# ---------------------------------------------------------------------------
# Wizard
# ---------------------------------------------------------------------------
Write-Host ""
Write-Host "=== Polymarket Platform - First-Run Setup ===" -ForegroundColor Cyan
Write-Host ""
Write-Host "This wizard creates/updates your .env file." -ForegroundColor White
Write-Host "Press Enter to accept the default shown in [brackets]." -ForegroundColor DarkGray
Write-Host "Existing non-placeholder values in .env will not be changed." -ForegroundColor DarkGray
Write-Host ""

# --- Trading mode ---
Write-Host "--- Trading Mode ---" -ForegroundColor Yellow

$currentDryRun = Get-EnvLine 'BOT_DRY_RUN'
if ([string]::IsNullOrWhiteSpace($currentDryRun)) {
    $choice = (Read-Host "  Run in dry-run (paper trading) mode? [Y/n]").Trim().ToLower()
    $dryVal = 'true'
    if ($choice -eq 'n') {
        $dryVal = 'false'
    }
    Set-EnvLine 'BOT_DRY_RUN' $dryVal
    Write-Host "  BOT_DRY_RUN=$dryVal" -ForegroundColor Green
}
else {
    Write-Host "  BOT_DRY_RUN=$currentDryRun [already set]" -ForegroundColor DarkGray
}

# --- Core market settings ---
Write-Host ""
Write-Host "--- Core Market Settings ---" -ForegroundColor Yellow

$tokenId = Read-RequiredValue `
    -Key   'POLY_TOKEN_ID' `
    -Label 'Token ID (POLY_TOKEN_ID)' `
    -Hint  'The CLOB token ID for the market you want to trade. Find it on Polymarket.'

if ($tokenId) {
    Set-EnvLine 'POLY_TOKEN_ID' $tokenId
    Write-Host "  POLY_TOKEN_ID saved." -ForegroundColor Green
}

# --- Live credentials (only when live mode selected) ---
$dryFinal = Get-EnvLine 'BOT_DRY_RUN'
if ($dryFinal -eq 'false') {
    Write-Host ""
    Write-Host "--- Live Trading Credentials ---" -ForegroundColor Yellow
    Write-Host "  (Required because BOT_DRY_RUN=false)" -ForegroundColor DarkGray

    $privKey = Read-RequiredValue `
        -Key   'POLY_PRIVATE_KEY' `
        -Label 'Private key (0x...)' `
        -Hint  'Your Ethereum wallet private key starting with 0x. NEVER share this.'

    if ($privKey) {
        Set-EnvLine 'POLY_PRIVATE_KEY' $privKey
    }

    $funder = Read-RequiredValue `
        -Key   'POLY_FUNDER_ADDRESS' `
        -Label 'Funder address (0x...)' `
        -Hint  'Your wallet address that holds the funds.'

    if ($funder) {
        Set-EnvLine 'POLY_FUNDER_ADDRESS' $funder
    }
}
else {
    Write-Host ""
    Write-Host "  (Skipping live credentials - dry-run mode)" -ForegroundColor DarkGray
}

# --- Strategy thresholds ---
Write-Host ""
Write-Host "--- Strategy Thresholds (press Enter for defaults) ---" -ForegroundColor Yellow

$buyThr = Read-OptionalValue `
    -Key     'BUY_THRESHOLD' `
    -Label   'Buy threshold' `
    -Default '0.45' `
    -Hint    'Buy when ask price <= this (0.0 to 1.0). Default: 0.45'

if ($buyThr) {
    Set-EnvLine 'BUY_THRESHOLD' $buyThr
}

$sellThr = Read-OptionalValue `
    -Key     'SELL_THRESHOLD' `
    -Label   'Sell threshold' `
    -Default '0.55' `
    -Hint    'Sell when bid price >= this (0.0 to 1.0). Default: 0.55'

if ($sellThr) {
    Set-EnvLine 'SELL_THRESHOLD' $sellThr
}

$buyAmt = Read-OptionalValue `
    -Key     'STRAT_BUY_AMOUNT_USD' `
    -Label   'Buy amount USD' `
    -Default '10.00' `
    -Hint    'USD amount per BUY order. Default: 10.00'

if ($buyAmt) {
    Set-EnvLine 'STRAT_BUY_AMOUNT_USD' $buyAmt
}

# --- Done ---
Write-Host ""
Write-Host "=== Setup complete ===" -ForegroundColor Green
Write-Host "  Config saved to: $EnvFile" -ForegroundColor White
Write-Host ""
Write-Host "  To change settings later, edit .env directly or re-run:" -ForegroundColor DarkGray
Write-Host "    powershell -ExecutionPolicy Bypass -File scripts\setup_config.ps1" -ForegroundColor DarkGray
Write-Host ""
