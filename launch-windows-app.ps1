#Requires -Version 5.1
<#
  Odysseus - native Windows desktop app (WebView2 window, no browser).

  Same setup as launch-windows.ps1, but starts launcher.py in desktop mode:
  FastAPI in the background, UI in a native window.

  Usage:
    powershell -ExecutionPolicy Bypass -File .\launch-windows-app.ps1
    powershell -ExecutionPolicy Bypass -File .\launch-windows-app.ps1 -Port 7000

  Requires WebView2 Runtime (preinstalled on Windows 10/11). If the window fails
  to open, install: https://developer.microsoft.com/microsoft-edge/webview2/
#>
param(
    [int]$Port = 7000,
    [string]$BindHost = "127.0.0.1"
)

$ErrorActionPreference = "Stop"
Set-Location -Path $PSScriptRoot

function Write-Step($msg) { Write-Host ""; Write-Host ("==> " + $msg) -ForegroundColor Cyan }
function Fail($msg) {
    Write-Host ""
    Write-Host ("ERROR: " + $msg) -ForegroundColor Red
    Write-Host ""
    Read-Host "Press Enter to exit"
    exit 1
}

# Reuse the same Python/venv/bootstrap logic as launch-windows.ps1
$launchScript = Join-Path $PSScriptRoot "launch-windows.ps1"
if (-not (Test-Path $launchScript)) {
    Fail "launch-windows.ps1 not found next to this script."
}

Write-Step "Preparing environment (venv, deps, setup)"
& $launchScript -Port $Port -BindHost $BindHost -BootstrapOnly
if ($LASTEXITCODE -ne 0) { Fail "Bootstrap failed." }

$venvPy = Join-Path $PSScriptRoot "venv\Scripts\python.exe"
if (-not (Test-Path $venvPy)) { Fail "venv not found after bootstrap." }

Write-Step "Installing desktop shell dependencies"
& $venvPy -m pip install pywebview pystray Pillow --quiet
if ($LASTEXITCODE -ne 0) { Fail "Desktop dependency install failed." }

Write-Step ("Starting Odysseus desktop app at http://{0}:{1}" -f $BindHost, $Port)
Write-Host "Close the window or use the tray icon to exit."
Write-Host ""

$env:ODYSSEUS_DESKTOP = "1"
$env:APP_BIND = $BindHost
$env:APP_PORT = "$Port"
& $venvPy launcher.py
