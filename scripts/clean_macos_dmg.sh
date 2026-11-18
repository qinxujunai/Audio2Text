#!/bin/bash
# Remove the volume-icon file, which Finder can expose when hidden files are shown.
set -euo pipefail
image="$1"
work="$(mktemp -d "${TMPDIR:-/tmp}/wanxiang-dmg.XXXXXX")"
mounted=0
cleanup() {
  if [[ "$mounted" == 1 ]]; then hdiutil detach "$work/mount" -quiet || true; fi
  rm -rf "$work"
}
trap cleanup EXIT
hdiutil convert "$image" -format UDRW -o "$work/writable.dmg" -quiet
mkdir "$work/mount"
hdiutil attach "$work/writable.dmg" -nobrowse -mountpoint "$work/mount" -quiet
mounted=1
rm -f "$work/mount/.VolumeIcon.icns"
hdiutil detach "$work/mount" -quiet
mounted=0
hdiutil convert "$work/writable.dmg" -format UDZO -o "$work/clean.dmg" -quiet
mv "$work/clean.dmg" "$image"
