#!/usr/bin/env bash
# Download the conversation library's sources, pinned, and verify them (SHA256SUMS).
#   data/conversation-library/fetch.sh WORKDIR
# Then build: see README.md in this folder.
set -euo pipefail
here="$(cd "$(dirname "$0")" && pwd)"
out="${1:?usage: fetch.sh WORKDIR}"
mkdir -p "$out/dailydialog" "$out/topicalchat" "$out/kokoro"
cd "$out"

# DailyDialog, ConvLab's mirror (CC BY-NC-SA 4.0), pinned revision.
curl -fsSL -o dailydialog/data.zip \
  "https://huggingface.co/datasets/ConvLab/dailydialog/resolve/745c1796cfe209b469394567f496815d2bc495d2/data.zip"
unzip -p dailydialog/data.zip data/dialogues.json > dailydialog/dialogues.json

# Topical-Chat (CDLA-Sharing-1.0), held-out conversations, pinned commit.
for f in test_freq test_rare; do
  curl -fsSL -o "topicalchat/$f.json" \
    "https://raw.githubusercontent.com/alexa/Topical-Chat/7c939229cbcf6f55f6977b341a5a2f2fe982d53f/conversations/$f.json"
done

# Kokoro v1.0 (Apache-2.0), the voices.
for f in kokoro-v1.0.onnx voices-v1.0.bin; do
  curl -fsSL -o "kokoro/$f" "https://github.com/thewh1teagle/kokoro-onnx/releases/download/model-files-v1.0/$f"
done

shasum -a 256 -c "$here/SHA256SUMS"
echo "sources ready in $out"
