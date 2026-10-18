$ErrorActionPreference = 'Stop'
Set-Location -LiteralPath $PSScriptRoot
if (Test-Path -LiteralPath 'dist\MusicShelf.exe') {
    Start-Process -FilePath (Join-Path $PSScriptRoot 'dist\MusicShelf.exe') -WindowStyle Normal
} elseif (Test-Path -LiteralPath '.venv\Scripts\pythonw.exe') {
    Start-Process -FilePath (Join-Path $PSScriptRoot '.venv\Scripts\pythonw.exe') -ArgumentList 'main.py' -WorkingDirectory $PSScriptRoot -WindowStyle Normal
} else {
    throw 'Run build.ps1 first, or install requirements.txt in a Python environment.'
}
