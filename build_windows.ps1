$ErrorActionPreference = 'Stop'

$root = Split-Path -Parent $MyInvocation.MyCommand.Path
Set-Location $root

python -m pip install --upgrade -r requirements.txt pyinstaller
python .\make_icon.py
python -m PyInstaller --noconfirm --clean .\sendmail_app.spec

Write-Host "Windows build complete: dist\SendMailOutreach.exe"
