$ErrorActionPreference = 'Stop'
Set-Location -LiteralPath $PSScriptRoot
python -m venv .venv
if ($LASTEXITCODE -ne 0) { throw 'Install Python 3.11 or newer with Tcl/Tk and add python to PATH.' }
& .\.venv\Scripts\python.exe -m pip install -r requirements-build.txt
if ($LASTEXITCODE -ne 0) { throw 'Build dependency installation failed.' }
& .\.venv\Scripts\python.exe build.py
if ($LASTEXITCODE -ne 0) { throw 'Executable build failed.' }
Write-Host 'Executable: dist\ESARegionDownloader.exe'
