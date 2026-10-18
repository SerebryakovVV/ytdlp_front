$ErrorActionPreference = 'Stop'
Set-Location -LiteralPath $PSScriptRoot
if (-not (Test-Path -LiteralPath '.venv\Scripts\python.exe')) {
    python -m venv .venv
    if ($LASTEXITCODE -ne 0) { throw 'Could not create the Python environment.' }
}
& '.\.venv\Scripts\python.exe' -m pip install --disable-pip-version-check -r requirements-build.txt
if ($LASTEXITCODE -ne 0) { throw 'Could not install build dependencies.' }
& '.\.venv\Scripts\python.exe' tools\make_icon.py
& '.\.venv\Scripts\python.exe' -m unittest discover -s tests -v
if ($LASTEXITCODE -ne 0) { throw 'Tests failed.' }
& '.\.venv\Scripts\python.exe' -m PyInstaller --noconfirm --clean --onefile --windowed --name MusicShelf --icon assets\music-shelf.ico --add-data 'assets;assets' main.py
if ($LASTEXITCODE -ne 0) { throw 'EXE build failed.' }
Write-Output "Built $PSScriptRoot\dist\MusicShelf.exe"
