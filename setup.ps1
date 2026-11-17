# One-time setup for Windows. Double-click SETUP-WINDOWS.bat instead of running this directly.
# Installs Python and ffmpeg if missing (via winget), installs the clipper, then asks for your keys.
$ErrorActionPreference = 'Continue'
Set-Location -LiteralPath $PSScriptRoot

function Say($msg) { Write-Host "`n== $msg" -ForegroundColor Cyan }
function Fail($msg) {
    Write-Host "`nSETUP STOPPED: $msg" -ForegroundColor Red
    Write-Host "Copy everything in this window and send it to Claude for the fix."
    exit 1
}
function Refresh-Path {
    $env:Path = [Environment]::GetEnvironmentVariable('Path', 'Machine') + ';' + [Environment]::GetEnvironmentVariable('Path', 'User')
}
function Find-Python {
    $candidates = @(
        @('py', '-3.12'), @('py', '-3.11'), @('py', '-3.13'), @('py', '-3.10'), @('py', '-3'), @('python'), @('python3')
    )
    foreach ($c in $candidates) {
        if (-not (Get-Command $c[0] -ErrorAction SilentlyContinue)) { continue }
        $extra = @($c | Select-Object -Skip 1)
        $out = & $c[0] @extra -c "import sys; print(sys.version_info >= (3, 10))" 2>$null
        if ($LASTEXITCODE -eq 0 -and "$out".Trim() -eq 'True') { return , $c }
    }
    # Installed but not on PATH yet: look in the standard install folders.
    foreach ($v in @('312', '311', '313', '310')) {
        foreach ($root in @("$env:LOCALAPPDATA\Programs\Python", "$env:ProgramFiles")) {
            $exe = Join-Path $root "Python$v\python.exe"
            if (Test-Path $exe) { return , @($exe) }
        }
    }
    return $null
}
function Have-Winget { [bool](Get-Command winget -ErrorAction SilentlyContinue) }

Say "Checking Python (need 3.10 or newer)"
$py = Find-Python
if (-not $py) {
    if (-not (Have-Winget)) { Fail "Python 3.10+ is not installed and winget is missing. Install Python 3.12 from https://www.python.org/downloads/ (tick 'Add python.exe to PATH'), then double-click SETUP-WINDOWS.bat again." }
    Write-Host "Installing Python 3.12 (click Yes if Windows asks)..."
    winget install -e --id Python.Python.3.12 --accept-source-agreements --accept-package-agreements
    Refresh-Path
    $py = Find-Python
    if (-not $py) { Fail "Python installed but this window can't see it yet. Close this window and double-click SETUP-WINDOWS.bat again." }
}
$pyExtra = @($py | Select-Object -Skip 1)
Write-Host ("Using: " + (& $py[0] @pyExtra --version))

Say "Checking ffmpeg"
if (-not (Get-Command ffmpeg -ErrorAction SilentlyContinue)) {
    if (-not (Have-Winget)) { Fail "ffmpeg is not installed and winget is missing. Install ffmpeg from https://www.gyan.dev/ffmpeg/builds/ and add its bin folder to PATH." }
    Write-Host "Installing ffmpeg (click Yes if Windows asks)..."
    winget install -e --id Gyan.FFmpeg --accept-source-agreements --accept-package-agreements
    Refresh-Path
    if (-not (Get-Command ffmpeg -ErrorAction SilentlyContinue)) { Fail "ffmpeg installed but this window can't see it yet. Close this window and double-click SETUP-WINDOWS.bat again." }
}
Write-Host "ffmpeg found."

Say "Creating a private Python environment in .venv"
$venvPy = Join-Path $PSScriptRoot '.venv\Scripts\python.exe'
if (-not (Test-Path $venvPy)) {
    & $py[0] @pyExtra -m venv .venv
    if ($LASTEXITCODE -ne 0 -or -not (Test-Path $venvPy)) { Fail "could not create the .venv folder." }
}

Say "Installing the clipper and its libraries (takes a few minutes the first time)"
& $venvPy -m pip install --upgrade pip --quiet --disable-pip-version-check
& $venvPy -m pip install -e . --disable-pip-version-check
if ($LASTEXITCODE -ne 0) { Fail "installing the libraries failed (see the red text above)." }

$env:Path = (Join-Path $PSScriptRoot '.venv\Scripts') + ';' + $env:Path

Say "Your keys"
& $venvPy -m clipper setup
$keysOk = ($LASTEXITCODE -eq 0)

Say "Final check"
& $venvPy -m clipper check

if ($keysOk) {
    Write-Host "`nALL SET. Double-click START-WINDOWS.bat to start clipping." -ForegroundColor Green
    Write-Host "Clips will appear in the data\clips folder. Nothing gets posted anywhere."
} else {
    Write-Host "`nAlmost done: the Twitch keys are still missing. Double-click SETUP-WINDOWS.bat again once you have them." -ForegroundColor Yellow
}
