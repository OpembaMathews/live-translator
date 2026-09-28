<#
    Build the Windows installer.

    Prepares a payload folder holding a private Python and the launchers,
    then hands it to Inno Setup. The libraries are NOT put in here: they are
    installed from PyPI while the installer runs, which is what keeps the
    .exe around 20 MB instead of 250 MB.

    Needs Inno Setup 6:  winget install JRSoftware.InnoSetup
#>
[CmdletBinding()]
param(
    [string]$PythonVersion = "3.12.8",
    [switch]$SkipCompile          # prepare the payload but do not run ISCC
)

$ErrorActionPreference = "Stop"
$here    = Split-Path -Parent $MyInvocation.MyCommand.Path
$root    = Split-Path -Parent $here
$payload = Join-Path $here "payload"
$runtime = Join-Path $payload "runtime"

Write-Host "Live Translator installer build" -ForegroundColor Cyan
Write-Host "  project : $root"

# --- a clean payload ------------------------------------------------------
if (Test-Path $payload) { Remove-Item $payload -Recurse -Force }
New-Item -ItemType Directory -Path $runtime -Force | Out-Null

# --- the private Python ---------------------------------------------------
# The embeddable build is a zip, not an installer: it unpacks into the app
# folder and never touches the machine's own Python.
$zipUrl = "https://www.python.org/ftp/python/$PythonVersion/python-$PythonVersion-embed-amd64.zip"
$zip    = Join-Path $env:TEMP "python-embed-$PythonVersion.zip"
Write-Host "  python  : $zipUrl"
if (-not (Test-Path $zip)) {
    Invoke-WebRequest -Uri $zipUrl -OutFile $zip -UseBasicParsing
}
Expand-Archive -Path $zip -DestinationPath $runtime -Force

# The embeddable build ships with site imports switched off, so pip cannot
# see what it installs. Turning it on is the documented way to make this
# build usable as a runtime.
$pth = Get-ChildItem -Path $runtime -Filter "python*._pth" | Select-Object -First 1
if (-not $pth) { throw "no ._pth file in the embeddable Python" }
$lines = Get-Content $pth.FullName | ForEach-Object {
    if ($_ -match '^\s*#\s*import\s+site\s*$') { "import site" } else { $_ }
}
if ($lines -notcontains "import site") { $lines += "import site" }
if ($lines -notcontains "Lib\site-packages") { $lines += "Lib\site-packages" }
# The embeddable build's ._pth replaces sys.path outright, so the folder
# holding the app has to be named or "import livetranslator" fails. The
# runtime sits at <app>\runtime, so ".." is the app folder.
if ($lines -notcontains "..") { $lines += ".." }
$lines | Set-Content $pth.FullName -Encoding ASCII

# pip itself, since the embeddable build has no ensurepip
$getpip = Join-Path $runtime "get-pip.py"
Invoke-WebRequest -Uri "https://bootstrap.pypa.io/get-pip.py" -OutFile $getpip -UseBasicParsing
& (Join-Path $runtime "python.exe") $getpip --no-warn-script-location | Out-Null
Remove-Item $getpip
Write-Host "  pip     : installed into the payload runtime"

# --- launchers ------------------------------------------------------------
# pythonw.exe so there is no console window behind the app.
@"
@echo off
rem Live Translator. Uses the private Python installed beside it.
cd /d "%~dp0"
start "" "%~dp0runtime\pythonw.exe" -m livetranslator %*
"@ | Set-Content (Join-Path $payload "LiveTranslator.bat") -Encoding ASCII

@"
@echo off
rem The paper reader on its own. A PDF path may be passed in.
cd /d "%~dp0"
start "" "%~dp0runtime\pythonw.exe" -m livetranslator.ui.reader_window %*
"@ | Set-Content (Join-Path $payload "Reader.bat") -Encoding ASCII

# python.exe here, not pythonw: this one must show what it is doing.
@"
@echo off
rem Installs the libraries from PyPI. Safe to run again: pip skips what is
rem already there, so a download cut off halfway is fixed by running it.
cd /d "%~dp0"
echo Downloading the libraries Live Translator needs (about 200 MB).
echo This runs once. Leave the window open until it finishes.
echo.
"%~dp0runtime\python.exe" -m pip install --no-warn-script-location -r "%~dp0requirements.txt"
if errorlevel 1 (
  echo.
  echo The download did not finish. Check the connection and run this again:
  echo   "%~dp0install-libraries.bat"
  pause
  exit /b 1
)
echo.
echo Done. Live Translator is ready to start.
timeout /t 3 >nul
"@ | Set-Content (Join-Path $payload "install-libraries.bat") -Encoding ASCII

Write-Host "  payload : $payload"

# --- compile --------------------------------------------------------------
if ($SkipCompile) {
    Write-Host "Payload ready; skipping the compile step." -ForegroundColor Yellow
    exit 0
}

# winget installs Inno Setup per-user by default, which is not in either
# Program Files, so look there too rather than claiming it is missing.
$iscc = @(
    "$env:LOCALAPPDATA\Programs\Inno Setup 6\ISCC.exe",
    "$env:ProgramFiles\Inno Setup 6\ISCC.exe",
    "${env:ProgramFiles(x86)}\Inno Setup 6\ISCC.exe",
    "$env:LOCALAPPDATA\Programs\Inno Setup 7\ISCC.exe",
    "$env:ProgramFiles\Inno Setup 7\ISCC.exe"
) | Where-Object { $_ -and (Test-Path -LiteralPath $_) } | Select-Object -First 1

if (-not $iscc) {
    $onPath = Get-Command "ISCC.exe" -ErrorAction SilentlyContinue
    if ($onPath) { $iscc = $onPath.Source }
}

if (-not $iscc) {
    Write-Host ""
    Write-Host "Inno Setup is not installed. Install it with:" -ForegroundColor Yellow
    Write-Host "  winget install JRSoftware.InnoSetup"
    Write-Host "then run this script again."
    exit 1
}

& $iscc (Join-Path $here "live-translator.iss")
$exe = Get-ChildItem (Join-Path $root "dist") -Filter "*setup.exe" |
       Sort-Object LastWriteTime -Descending | Select-Object -First 1
if ($exe) {
    Write-Host ""
    Write-Host ("Built {0}  ({1:N1} MB)" -f $exe.Name, ($exe.Length / 1MB)) `
        -ForegroundColor Green
}
