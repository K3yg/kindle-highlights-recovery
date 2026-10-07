# Windows installer: Python dependencies + Desktop / Start Menu shortcuts.
# Run via install.bat (double-click), or: powershell -ExecutionPolicy Bypass -File install.ps1
$ErrorActionPreference = 'Stop'
$repo = Split-Path -Parent $MyInvocation.MyCommand.Path
Write-Host "== Kindle Highlights Recovery: install ==" -ForegroundColor Cyan

# 1. Python. Ask the interpreter for its real path: the "python" on PATH may
#    be a Microsoft Store alias, which makes a poor shortcut target.
$python = $null
foreach ($cmd in 'py', 'python') {
  $c = Get-Command $cmd -ErrorAction SilentlyContinue
  if (-not $c) { continue }
  try {
    $exe = (& $c.Source -c "import sys; print(sys.executable)" 2>$null | Select-Object -First 1)
    $ok = (& $c.Source -c "import sys; print(int(sys.version_info >= (3, 10)))" 2>$null | Select-Object -First 1)
    if ($exe -and (Test-Path $exe) -and $ok -eq '1') { $python = $exe.Trim(); break }
  } catch {}
}
if (-not $python) {
  Write-Host "Python 3.10 or newer was not found." -ForegroundColor Red
  Write-Host "Install it from https://www.python.org/downloads/ and tick 'Add python.exe to PATH',"
  Write-Host "then run this installer again."
  exit 1
}
Write-Host "Python: $python"

# 2. Dependencies needed to decode KFX books.
Write-Host "Installing dependencies..."
& $python -m pip install --disable-pip-version-check -q -r (Join-Path $repo 'requirements.txt')
if ($LASTEXITCODE -ne 0) {
  Write-Host "Retrying as a per-user install..."
  & $python -m pip install --disable-pip-version-check -q --user -r (Join-Path $repo 'requirements.txt')
  if ($LASTEXITCODE -ne 0) { Write-Host "Could not install dependencies." -ForegroundColor Red; exit 1 }
}

# 3. Shortcuts that open the app without a console window.
$pythonw = Join-Path (Split-Path -Parent $python) 'pythonw.exe'
if (-not (Test-Path $pythonw)) { $pythonw = $python }
$shell = New-Object -ComObject WScript.Shell
$targets = @(
  (Join-Path ([Environment]::GetFolderPath('Desktop')) 'Kindle Highlights Recovery.lnk'),
  (Join-Path ([Environment]::GetFolderPath('Programs')) 'Kindle Highlights Recovery.lnk')
)
foreach ($lnk in $targets) {
  $s = $shell.CreateShortcut($lnk)
  $s.TargetPath = $pythonw
  $s.Arguments = '-m khr gui'
  $s.WorkingDirectory = $repo
  $s.IconLocation = "$pythonw,0"
  $s.Description = 'Recover Kindle highlights into My Clippings.txt and HTML'
  $s.Save()
  Write-Host "Shortcut: $lnk"
}

Write-Host ""
Write-Host "Done. Plug in and unlock your Kindle, then open 'Kindle Highlights Recovery'." -ForegroundColor Green
