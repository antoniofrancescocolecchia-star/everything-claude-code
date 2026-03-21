#Requires -Version 5.1
<#
.SYNOPSIS
    Interactive first-run configuration wizard for Polymarket Platform.

.DESCRIPTION
    Asks only for the values needed to run. Writes them to .env.
    Never overwrites values that are already set unless they are placeholders.
    Safe to re-run at any time; existing non-placeholder values are preserved.
#>

Set-StrictMode -Version Latest
$ErrorActionPreference = 'Stop'

$ScriptDir  = Split-Path -Parent $MyInvocation.MyCommand.Path
$ProjectDir = Split-Path -Parent $ScriptDir
$EnvFile    = Join-Path $ProjectDir '.env'

# ── Helpers ───────────────────────────────────────────────────────────────────

function Get-EnvLine {
    param([string]$Key)
    $content = Get-Content $EnvFile -Raw -ErrorAction SilentlyContinue
    if ($content -match "(?m)^$Key=(.*)$") {
        return $Matches[1].Trim()
    }
    return $null
}

function Set-EnvLine {
    param([string]$Key, [string]$Value)
    $content = Get-Content $EnvFile -Raw -ErrorAction SilentlyContinue
    if (-not $content) { $content = '' }

    $escaped = [regex]::Escape($Key)
    if ($content -match "(?m)^$escaped=") {
        # Replace existing line
        $content = $content -replace "(?m)^$escaped=.*$", "$Key=$Value"
    } else {
        # Append new line
        if ($content -and -not $content.EndsWith("`n")) {
            $content += "`n"
        }
        $content += "$Key=$Value`n"
    }
    Set-Content -Path $EnvFile -Value $content -NoNewline
}

function IsPlaceholder {
    param([string]$Value)
    return ([string]::IsNullOrWhiteSpace($Value) `
        -or $Value -eq 'YOUR_CLOB_TOKEN_ID' `
        -or $Value -eq '0xYOUR_PRIVATE_KEY' `
        -or $Value -eq '0xYOUR_FUNDER_ADDRESS')
}

function Prompt-Value {
    param(
        [string]$Key,
        [string]$Label,
        [string]$Default = '',
        [string]$Hint = '',
        [switch]$Required
    )
    $current = Get-EnvLine $Key
    if (-not (IsPlaceholder $current)) {
        Write-Host "  $Label : [already set, keeping]" -ForegroundColor DarkGray
        return $current
    }

    Write-Host ""
    if ($Hint) { Write-Host "  $Hint" -ForegroundColor DarkGray }

    $prompt = "  $Label"
    if ($Default) { $prompt += " [$Default]" }
    $prompt += " : "

    $value = Read-Host $prompt
    $value = $value.Trim()

    if ([string]::IsNullOrWhiteSpace($value)) {
        if ($Default) {
            $value = $Default
        } elseif ($Required) {
            Write-Host "  (required — cannot be empty)" -ForegroundColor Red
            # Re-prompt once
            $value = (Read-Host $prompt).Trim()
            if ([string]::IsNullOrWhiteSpace($value)) {
                Write-Host "  Skipping — you can set $Key manually in .env later." -ForegroundColor Yellow
                return $null
            }
        }
    }
    return $value
}

# ── Wizard ────────────────────────────────────────────────────────────────────

Write-Host ""
Write-Host "=== Polymarket Platform — First-Run Setup ===" -ForegroundColor Cyan
Write-Host ""
Write-Host "This wizard will create/update your .env file." -ForegroundColor White
Write-Host "Press Enter to accept the default shown in [brackets]." -ForegroundColor DarkGray
Write-Host "Existing values already in .env will not be changed." -ForegroundColor DarkGray
Write-Host ""

# ── Mode ─────────────────────────────────────────────────────────────────────
Write-Host "--- Trading Mode ---" -ForegroundColor Yellow

$currentDryRun = Get-EnvLine 'BOT_DRY_RUN'
if ([string]::IsNullOrWhiteSpace($currentDryRun)) {
    $dryRunChoice = Read-Host "  Run in dry-run (paper trading) mode? [Y/n]"
    $dryRunChoice = $dryRunChoice.Trim().ToLower()
    $dryRunVal = if ($dryRunChoice -eq 'n') { 'false' } else { 'true' }
    Set-EnvLine 'BOT_DRY_RUN' $dryRunVal
    Write-Host "  BOT_DRY_RUN=$dryRunVal" -ForegroundColor Green
} else {
    Write-Host "  BOT_DRY_RUN=$currentDryRun [already set]" -ForegroundColor DarkGray
}

# ── Core market ───────────────────────────────────────────────────────────────
Write-Host ""
Write-Host "--- Core Market Settings ---" -ForegroundColor Yellow

$tokenId = Prompt-Value `
    -Key 'POLY_TOKEN_ID' `
    -Label 'Token ID (POLY_TOKEN_ID)' `
    -Hint 'The CLOB token ID for the market you want to trade. Find it on Polymarket.' `
    -Required

if ($tokenId) {
    Set-EnvLine 'POLY_TOKEN_ID' $tokenId
    Write-Host "  POLY_TOKEN_ID set." -ForegroundColor Green
}

# ── Live credentials (only if live mode) ─────────────────────────────────────
$dryRunFinal = Get-EnvLine 'BOT_DRY_RUN'
if ($dryRunFinal -eq 'false') {
    Write-Host ""
    Write-Host "--- Live Trading Credentials ---" -ForegroundColor Yellow
    Write-Host "  (Required for BOT_DRY_RUN=false)" -ForegroundColor DarkGray

    $privKey = Prompt-Value `
        -Key 'POLY_PRIVATE_KEY' `
        -Label 'Private key (0x...)' `
        -Hint 'Your Ethereum wallet private key — starts with 0x. NEVER share this.'

    if ($privKey) { Set-EnvLine 'POLY_PRIVATE_KEY' $privKey }

    $funder = Prompt-Value `
        -Key 'POLY_FUNDER_ADDRESS' `
        -Label 'Funder address (0x...)' `
        -Hint 'Your wallet address (the one that holds the funds).'

    if ($funder) { Set-EnvLine 'POLY_FUNDER_ADDRESS' $funder }
} else {
    Write-Host ""
    Write-Host "  (Skipping live credentials — dry-run mode)" -ForegroundColor DarkGray
}

# ── Strategy defaults ─────────────────────────────────────────────────────────
Write-Host ""
Write-Host "--- Strategy Thresholds (press Enter for defaults) ---" -ForegroundColor Yellow

$buyThr = Prompt-Value -Key 'BUY_THRESHOLD'  -Label 'Buy threshold'  -Default '0.45' `
    -Hint 'Buy when ask price <= this (0.0 to 1.0). Default: 0.45'
if ($buyThr) { Set-EnvLine 'BUY_THRESHOLD' $buyThr }

$sellThr = Prompt-Value -Key 'SELL_THRESHOLD' -Label 'Sell threshold' -Default '0.55' `
    -Hint 'Sell when bid price >= this (0.0 to 1.0). Default: 0.55'
if ($sellThr) { Set-EnvLine 'SELL_THRESHOLD' $sellThr }

$buyAmt = Prompt-Value -Key 'STRAT_BUY_AMOUNT_USD' -Label 'Buy amount USD' -Default '10.00' `
    -Hint 'USD amount per BUY order. Default: 10.00'
if ($buyAmt) { Set-EnvLine 'STRAT_BUY_AMOUNT_USD' $buyAmt }

# ── Done ─────────────────────────────────────────────────────────────────────
Write-Host ""
Write-Host "=== Setup complete ===" -ForegroundColor Green
Write-Host "  Config saved to: $EnvFile" -ForegroundColor White
Write-Host ""
Write-Host "  To change settings later, edit .env directly or re-run:" -ForegroundColor DarkGray
Write-Host "    powershell -ExecutionPolicy Bypass -File scripts\setup_config.ps1" -ForegroundColor DarkGray
Write-Host ""
