@echo off
:: Start-Polymarket.cmd
:: Double-click to bootstrap and launch the Polymarket Platform on Windows.
:: Calls start_windows.ps1 via PowerShell with execution-policy bypass.
:: No manual setup required.

powershell.exe -NoProfile -ExecutionPolicy Bypass -File "%~dp0scripts\start_windows.ps1"

if %ERRORLEVEL% NEQ 0 (
    echo.
    echo ERROR: Startup failed. Review the messages above.
    pause
)
