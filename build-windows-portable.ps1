#Requires -Version 5.1
<#
  Build a portable Windows distribution for Odysseus.

  Output layout:
    dist\Odysseus\Odysseus.exe
    dist\Odysseus\static\...
    dist\Odysseus\scripts\...
    dist\Odysseus\mcp_servers\...
    dist\Odysseus\services\hwfit\data\...

  The app then keeps using its normal filesystem layout when frozen.

  Usage:
    powershell -ExecutionPolicy Bypass -File .\build-windows-portable.ps1
#>

$ErrorActionPreference = "Stop"
Set-Location -Path $PSScriptRoot

function Write-Step($msg) { Write-Host ""; Write-Host ("==> " + $msg) -ForegroundColor Cyan }
function Fail($msg) {
    Write-Host ""
    Write-Host ("ERROR: " + $msg) -ForegroundColor Red
    exit 1
}

Write-Step "Checking for Python"
$pyExe = $null
if (Test-Path ".\.venv\Scripts\python.exe") {
    $pyExe = (Resolve-Path ".\.venv\Scripts\python.exe").Path
} else {
    foreach ($c in @("py", "python")) {
        $cmd = Get-Command $c -ErrorAction SilentlyContinue
        if ($cmd) { $pyExe = $cmd.Source; break }
    }
    if ($pyExe -like "*WindowsApps*python.exe") {
        $pyCmd = Get-Command py -ErrorAction SilentlyContinue
        if ($pyCmd) {
            $pyExe = $pyCmd.Source
        }
    }
}
if (-not $pyExe) {
    Fail "Python not found on PATH. Install Python 3.11+ first."
}
Write-Host ("Using Python: " + $pyExe)

Write-Step "Installing build dependencies"
& $pyExe -m pip install --upgrade pip --quiet
& $pyExe -m pip install -r requirements.txt -r requirements-optional.txt pyinstaller pystray Pillow pywebview
if ($LASTEXITCODE -ne 0) { Fail "Dependency install failed." }

Write-Step "Installing bundled SearXNG (SimpleXNG) for portable"
$simplexWhl = Join-Path $env:TEMP "simplexng-0.1.3-py3-none-any.whl"
if (-not (Test-Path $simplexWhl)) {
    Invoke-WebRequest -Uri "https://files.pythonhosted.org/packages/51/44/0e20ffd1ce42899b953e522bf64e3d791205a36b75f9319ddd599ff235ab/simplexng-0.1.3-py3-none-any.whl" -OutFile $simplexWhl
}
& $pyExe -m pip install $simplexWhl --no-deps --quiet
if ($LASTEXITCODE -ne 0) { Fail "SimpleXNG wheel install failed." }
& $pyExe -m pip install -r requirements-portable-searxng.txt --quiet
if ($LASTEXITCODE -ne 0) { Fail "SimpleXNG dependency install failed." }
& $pyExe -c "from simplexng.simplexng import main; print('simplexng ok')"
if ($LASTEXITCODE -ne 0) { Fail "SimpleXNG import check failed." }

Write-Step "Checking Cursor SDK bridge"
& $pyExe -c "from cursor_sdk._vendor import resolve_bridge_path; print('bridge:', resolve_bridge_path())"
if ($LASTEXITCODE -ne 0) { Fail "cursor-sdk bridge not available. Install cursor-sdk before building portable exe." }

Write-Step "Building portable exe bundle"
Remove-Item -Recurse -Force build, dist -ErrorAction SilentlyContinue

$searxngBundleArgs = @()
Get-Content "requirements-portable-searxng.txt" | ForEach-Object {
    $line = ($_ -split '#')[0].Trim()
    if (-not $line) { return }
    $pkg = ($line -split '[=<>!\[]')[0].Trim()
    if (-not $pkg) { return }
    $importName = $pkg -replace '-', '_'
    $searxngBundleArgs += "--hidden-import=$importName"
    $searxngBundleArgs += "--collect-all", $importName
}
$searxngBundleArgs += "--hidden-import=fasttext"
$searxngBundleArgs += "--collect-all", "fasttext_predict"

$dataArgs = @(
    "--add-data", "static;static",
    "--add-data", "scripts;scripts",
    "--add-data", "mcp_servers;mcp_servers",
    "--add-data", "services/hwfit/data;services/hwfit/data",
    "--add-data", "config;config",
    "--add-data", ".env.example;.env.example"
)

Write-Step "Bundling Tcl/Tk for tkinter splash"
$tclTkLines = & $pyExe -c @"
import sys
from pathlib import Path
base = Path(sys.base_prefix)
tcl = base / 'tcl' / 'tcl8.6'
tk = base / 'tcl' / 'tk8.6'
if not tcl.is_dir() or not tk.is_dir():
    raise SystemExit('Tcl/Tk data not found under sys.base_prefix')
print(f'{tcl};_tcl_data')
print(f'{tk};_tk_data')
"@
if ($LASTEXITCODE -ne 0) { Fail "Tcl/Tk data not found. Install full Python with tkinter support." }
foreach ($line in $tclTkLines) {
    $sep = $line.IndexOf(';')
    $src = $line.Substring(0, $sep)
    $dst = $line.Substring($sep + 1)
    $dataArgs += "--add-data", ($src + ";" + $dst)
    Write-Host ("  " + $dst + " <- " + $src)
}

$pyInstallerArgs = @(
    "--noconfirm", "--clean", "--onedir", "--noconsole",
    "--icon=static/icon.ico", "--name", "Odysseus",
    "--hidden-import=desktop_shell",
    "--hidden-import=src.desktop_shutdown",
    "--hidden-import=src.frozen_runtime",
    "--hidden-import=src.desktop_single_instance",
    "--hidden-import=src.subprocess_entry",
    "--hidden-import=src.tool_content",
    "--hidden-import=src.python_runtime",
    "--hidden-import=webview",
    "--hidden-import=webview.platforms.edgechromium",
    "--hidden-import=src.bundled_searxng",
    "--hidden-import=src.bundled_searxng_worker",
    "--hidden-import=src.uvloop_compat",
    "--hidden-import=simplexng.simplexng",
    "--hidden-import=tkinter"
) + $searxngBundleArgs + @(
    "--collect-all", "cursor_sdk",
    "--collect-all", "simplexng",
    "--collect-all", "ddgs",
    "--collect-all", "webview",
    "--collect-all", "matplotlib"
) + $dataArgs + @("launcher.py")

& $pyExe -m PyInstaller @pyInstallerArgs
if ($LASTEXITCODE -ne 0) { Fail "PyInstaller build failed." }

Write-Host ""
Write-Host "Build complete." -ForegroundColor Green
Write-Host "Portable app folder: $PSScriptRoot\dist\Odysseus" -ForegroundColor Green
Write-Host "Distribute the whole folder (or zip it) so static assets and scripts stay with the exe." -ForegroundColor Green