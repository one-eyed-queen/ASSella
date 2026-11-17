# paws-fingerprinter installer for windows. no admin, nothing outside ur user folder.
#
#   git clone https://github.com/one-eyed-queen/paws-fingerprinter; cd paws-fingerprinter; powershell -ExecutionPolicy Bypass -File install.ps1
#
# makes a private python env in %LOCALAPPDATA%\paws-fingerprinter\venv, installs the dependencies,
# puts a `fingerprint` shim (and `fp`) on your PATH and adds EXE Fingerprinter to the start menu.
# works in cmd, powershell, windows terminal — Search "fingerprint" to find the app too.
#
# options: -DryRun  -NoDesktop  -NoLauncher  -Desktop  -Prefix DIR  -Python PATH  -Uninstall  -Help
param(
    [switch]$DryRun,
    [switch]$NoDesktop,
    [switch]$NoLauncher,
    [switch]$Desktop,
    [switch]$Uninstall,
    [string]$Prefix = (Join-Path $env:LOCALAPPDATA "paws-fingerprinter"),
    [string]$Python = "",
    [switch]$Help
)
$ErrorActionPreference = "Stop"

$Src = $PSScriptRoot
$Bin = Join-Path $Prefix "bin"
$Venv = Join-Path $Prefix "venv"
$VenvPy = Join-Path $Venv "Scripts\python.exe"
$LinkName = "EXE Fingerprinter"

function Say($text) { Write-Host $text }
function Die($text) { Write-Host "paws-fingerprinter installer: $text" -ForegroundColor Red; exit 1 }
function Run([string]$exe, [string[]]$arguments) {
    if ($DryRun) { Say "  would run: $exe $($arguments -join ' ')"; return }
    & $exe @arguments
    if ($LASTEXITCODE -ne 0) { Die "$exe failed (exit code $LASTEXITCODE)" }
}

if ($Help) { Get-Content $PSCommandPath | Select-Object -Skip 1 -First 11 | ForEach-Object { $_ -replace '^# ?', '' }; exit 0 }

if ($Uninstall) {
    foreach ($name in @("fingerprint.cmd", "fp.cmd")) { $p = Join-Path $Bin $name; if (Test-Path $p) { Remove-Item $p -Force } }
    # remove our bin dir from the user PATH (only the exact entry we added)
    $userPath = [Environment]::GetEnvironmentVariable("Path", "User")
    if ($userPath) {
        $kept = ($userPath -split ';' | Where-Object { $_ -and ($_ -ne $Bin) }) -join ';'
        if (-not $DryRun) { [Environment]::SetEnvironmentVariable("Path", $kept, "User") }
    }
    foreach ($dir in @([Environment]::GetFolderPath("Programs"), [Environment]::GetFolderPath("Desktop"))) {
        $lnk = Join-Path $dir "$LinkName.lnk"
        if (Test-Path $lnk) { Remove-Item $lnk -Force }
    }
    $ico = Join-Path $Prefix "paws-fingerprinter.ico"; if (Test-Path $ico) { Remove-Item $ico -Force }
    Say "removed: fingerprint, fp, start menu entry, PATH entry and icon"
    if (Test-Path $Prefix) {
        $answer = Read-Host "delete $Prefix (venv, ~150 MB)? [y/N]"
        if ($answer -match '^[yY]') { Remove-Item $Prefix -Recurse -Force; Say "removed $Prefix" } else { Say "kept $Prefix" }
    }
    Say "settings were kept in %APPDATA%\EXE-fingerprinter (delete that folder yourself too, if you want)"
    exit 0
}

if (-not (Test-Path (Join-Path $Src "src\main.py"))) { Die "run this from a paws-fingerprinter checkout ($Src has no src\main.py)" }

# ---- a python that's new enough (3.10+) -------------------------------------------------------------
function PyOk($cmd, $extra) {
    try { & $cmd @extra -c "import sys; sys.exit(0 if sys.version_info >= (3, 10) else 1)" 2>$null; return $LASTEXITCODE -eq 0 } catch { return $false }
}
$PyArgs = @()
if (-not $Python) {
    # the py launcher knows every python on the machine; plain `python` may be the microsoft store stub
    if ((Get-Command py -ErrorAction SilentlyContinue) -and (PyOk "py" @("-3"))) { $Python = "py"; $PyArgs = @("-3") }
    elseif ((Get-Command python -ErrorAction SilentlyContinue) -and (PyOk "python" @())) { $Python = "python" }
}
if (-not $Python) { Die "need python 3.10 or newer: winget install Python.Python.3.13 (then open a new terminal)" }
if (-not (PyOk $Python $PyArgs)) { Die "$Python is older than 3.10" }

Say "paws-fingerprinter: installing from $Src"
Say "  python   $Python $($PyArgs -join ' ')"
Say "  into     $Venv"
Say "  command  fingerprint (+ fp)"
if (-not (Get-Command git -ErrorAction SilentlyContinue)) { Say "  note: git isn't installed, updates will need it (winget install Git.Git)" }

# ---- the private environment ----------------------------------------------------------------------
if (-not $DryRun) { New-Item -ItemType Directory -Force -Path $Prefix, $Bin | Out-Null }
Run $Python ($PyArgs + @("-m", "venv", $Venv))
Run $VenvPy @("-m", "pip", "install", "--quiet", "--disable-pip-version-check", "-r", (Join-Path $Src "requirements.txt"))

# ---- the fingerprint command (a shim on your PATH, works in cmd/powershell/terminal) --------------
if (-not $NoLauncher) {
    if (-not $DryRun) {
        foreach ($name in @("fingerprint", "fp")) {
            Set-Content -Path (Join-Path $Bin "$name.cmd") -Encoding ASCII -Value @(
                "@echo off",
                "`"$VenvPy`" `"$Src\src\main.py`" %*"
            )
        }
    } else { Say "  would write $Bin\fingerprint.cmd and $Bin\fp.cmd" }
    $userPath = [Environment]::GetEnvironmentVariable("Path", "User")
    if (-not $userPath -or ($userPath -notlike "*$Bin*")) {
        if (-not $DryRun) { [Environment]::SetEnvironmentVariable("Path", (($userPath.TrimEnd(';')) + ';' + $Bin).TrimStart(';'), "User") }
        Say "  added $Bin to your user PATH (open a new terminal to pick it up)"
    }
}

# ---- start menu (+ optionally desktop) --------------------------------------------------------------
if (-not $NoDesktop) {
    $ico = Join-Path $Prefix "paws-fingerprinter.ico"
    if (-not $DryRun) {
        & $VenvPy -c "from PIL import Image; Image.open(r'$Src\exe_fingerprinter_logo.png').convert('RGBA').save(r'$ico', sizes=[(16,16),(24,24),(32,32),(48,48),(64,64),(128,128),(256,256)])"
    } else { Say "  would build $ico from exe_fingerprinter_logo.png" }
    $wsh = New-Object -ComObject WScript.Shell
    $makeLink = {
        param($folder)
        $lnk = $wsh.CreateShortcut((Join-Path $folder "$LinkName.lnk"))
        $lnk.TargetPath = $VenvPy
        $lnk.Arguments = "`"$Src\src\main.py`""
        $lnk.WorkingDirectory = $Src
        $lnk.IconLocation = "$ico,0"
        $lnk.Description = "Search a Steam appid, see the base game's Windows .exes, and fingerprint or download just them"
        $lnk.Save()
    }
    if (-not $DryRun) {
        & $makeLink ([Environment]::GetFolderPath("Programs"))
        Say "  added '$LinkName' to the start menu (search `"fingerprint`")"
        if ($Desktop) { & $makeLink ([Environment]::GetFolderPath("Desktop")); Say "  added a desktop shortcut too" }
    }
}

Say ""
if ($DryRun) { Say "dry run: nothing was changed" } else { Say "done. type: fingerprint   (in cmd, powershell, windows terminal, or Search it)" }
