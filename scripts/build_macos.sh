#!/bin/bash
# Build an Apple Silicon self-use application from the current checkout.
set -euo pipefail
cd "$(dirname "$0")/.."
if [[ "$(uname -s)" != Darwin || "$(uname -m)" != arm64 ]]; then
  echo 'This build currently supports Apple Silicon macOS only.' >&2; exit 1
fi
if [[ ! -x .venv/bin/python ]]; then
  echo 'Create .venv and install requirements.txt and pyinstaller first.' >&2; exit 1
fi
.venv/bin/python scripts/prepare_macos_ffmpeg.py
.venv/bin/python -m PyInstaller --noconfirm --onedir --name wanxiang-api \
  --distpath web/src-tauri/sidecar --workpath /tmp/wanxiang-pyinstaller-work \
  --specpath /tmp --collect-submodules app --collect-submodules scripts \
  --collect-all faster_whisper --collect-all av --collect-all sherpa_onnx \
  --collect-all playwright --collect-all yt_dlp --collect-all keyring scripts/desktop_sidecar.py
cd web
npm run tauri -- build --config src-tauri/tauri.macos.conf.json --bundles app,dmg
../scripts/clean_macos_dmg.sh src-tauri/target/release/bundle/dmg/万象成文_2.3.2_aarch64.dmg
