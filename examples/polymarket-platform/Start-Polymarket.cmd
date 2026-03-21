@echo off
:: Start-Polymarket.cmd — double-click entrypoint for Polymarket Platform
:: Launches the PowerShell bootstrap with execution policy bypass.
:: No manual setup required on subsequent runs.

powershell.exe -NoProfile -ExecutionPolicy Bypass -File "%~dp0scripts\start_windows.ps1"

if %ERRORLEVEL% NEQ 0 (
    echo.
    echo ERROR: Startup failed. Review the messages above.
    pause
)
