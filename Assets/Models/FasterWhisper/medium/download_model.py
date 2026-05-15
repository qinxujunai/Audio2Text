from huggingface_hub import snapshot_download

import os
from pathlib import Path

repo_id = "Systran/faster-whisper-medium"
local_dir = Path(__file__).resolve().parent
token = os.environ.get("HF_TOKEN")

print("Downloading model to:", local_dir)

snapshot_download(
    repo_id=repo_id,
    local_dir=str(local_dir),
    token=token,
    ignore_patterns=["*.lock"],
)

print("Model download complete:", local_dir)
