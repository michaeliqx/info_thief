#!/bin/bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_DIR="$(cd "$SCRIPT_DIR/../.." && pwd)"
RSSHUB_DIR="${RSSHUB_DIR:-$PROJECT_DIR/.rsshub/RSSHub_stable}"
RSSHUB_BRANCH="${RSSHUB_BRANCH:-stable}"
PATCH_FILE="$SCRIPT_DIR/patches/huxiu-util-compat.patch"

mkdir -p "$(dirname "$RSSHUB_DIR")"

if [ ! -d "$RSSHUB_DIR/.git" ] || [ ! -f "$RSSHUB_DIR/package.json" ]; then
  echo "[rsshub] cloning RSSHub ($RSSHUB_BRANCH) ..."
  rm -rf "$RSSHUB_DIR"
  # 先 clone 到本地磁盘 /tmp，避免 NFS 上 pack 临时文件导致 "could not open tmp_pack" 错误
  TMP_CLONE="$(mktemp -d)"
  trap "rm -rf '$TMP_CLONE'" EXIT
  git clone --depth=1 -b "$RSSHUB_BRANCH" https://github.com/DIYgod/RSSHub.git "$TMP_CLONE/RSSHub"
  mv "$TMP_CLONE/RSSHub" "$RSSHUB_DIR"
else
  echo "[rsshub] using existing repo: $RSSHUB_DIR"
fi

if [ -f "$PATCH_FILE" ]; then
  if grep -q "state?.briefStoreModule" "$RSSHUB_DIR/lib/routes/huxiu/util.ts"; then
    echo "[rsshub] compatibility patch already applied"
  else
    echo "[rsshub] applying compatibility patch"
    git -C "$RSSHUB_DIR" apply "$PATCH_FILE"
  fi
fi

if [ ! -x "$RSSHUB_DIR/node_modules/.bin/tsx" ]; then
  echo "[rsshub] installing dependencies"
  (
    cd "$RSSHUB_DIR"
    PUPPETEER_SKIP_DOWNLOAD=1 npm install --omit=dev --no-audit --no-fund
  )
else
  echo "[rsshub] dependencies already installed"
fi

echo "[rsshub] setup complete"
