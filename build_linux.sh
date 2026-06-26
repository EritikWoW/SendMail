#!/usr/bin/env bash
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$ROOT"

python3 -m pip install --upgrade -r requirements.txt pyinstaller
python3 ./make_icon.py
python3 -m PyInstaller --noconfirm --clean sendmail_app.spec

echo "Linux build complete: dist/SendMailOutreach"
