#!/usr/bin/env bash
# Builds dist/ChinaHKMarkets.exe (Windows, one file). Run from the repository root in Git Bash:
#   bash desktop/build.sh
# Needs Python 3.10-3.12 (the desktop libraries don't support newer ones yet); set PYTHON to choose one.
set -euo pipefail
PY="${PYTHON:-python}"
[ -d .venv-desktop ] || "$PY" -m venv .venv-desktop
.venv-desktop/Scripts/python -m pip install -q -r desktop/requirements.txt
.venv-desktop/Scripts/python desktop/make_icon.py
.venv-desktop/Scripts/pyinstaller --noconfirm --clean --onefile --windowed --name ChinaHKMarkets \
  --icon desktop/icon.ico desktop/app.py
echo "Built: dist/ChinaHKMarkets.exe"
