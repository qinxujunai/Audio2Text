"""Fetch the pinned Apple Silicon FFmpeg binary used by the macOS bundle."""
import hashlib
import io
import json
import os
from pathlib import Path
import urllib.request
import zipfile

VERSION = '0.8.1'
WHEEL = 'imageio_ffmpeg-0.8.1-py3-none-macosx_12_0_arm64.whl'
SHA256 = '5636d691e36e5274b13fe38182b1f627b2e35dcecc63944012c2470f9484fc45'
root = Path(__file__).resolve().parent.parent
with urllib.request.urlopen(f'https://pypi.org/pypi/imageio-ffmpeg/{VERSION}/json', timeout=30) as response:
    metadata = json.load(response)
release = next(item for item in metadata['urls'] if item['filename'] == WHEEL)
if release['digests']['sha256'] != SHA256:
    raise SystemExit('FFmpeg wheel checksum does not match the pinned version')
with urllib.request.urlopen(release['url'], timeout=120) as response:
    data = response.read()
if hashlib.sha256(data).hexdigest() != SHA256:
    raise SystemExit('FFmpeg wheel checksum failed')
output = root / 'web/src-tauri/sidecar/ffmpeg'
output.mkdir(parents=True, exist_ok=True)
with zipfile.ZipFile(io.BytesIO(data)) as archive:
    (output / 'ffmpeg').write_bytes(archive.read('imageio_ffmpeg/binaries/ffmpeg-macos-aarch64-v9.0'))
    (output / 'IMAGEIO-LICENSE.txt').write_bytes(archive.read('imageio_ffmpeg-0.8.1.dist-info/licenses/LICENSE'))
os.chmod(output / 'ffmpeg', 0o755)
print('Prepared pinned Apple Silicon FFmpeg')
