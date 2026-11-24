#!/usr/bin/env bash
# 安装 solidcraft-cli（主内核）。默认按平台下预编译包，不解源码。
#   ./tools/install-solidcraft.sh            # 装到 ~/bin/solidcraft-cli
#   VERSION=v0.47.0 ./tools/install-solidcraft.sh
#   PREFIX=/usr/local/bin ./tools/install-solidcraft.sh
set -euo pipefail

VERSION="${VERSION:-v0.47.0}"
VERSION_NUM="${VERSION#v}"
PREFIX="${PREFIX:-$HOME/bin}"
PROXY="${GH_PROXY:-https://gh-proxy.com/}"
REPO="tangency-labs/solidcraft"

os="$(uname -s)"
arch="$(uname -m)"
case "$os/$arch" in
  Darwin/*)                 asset="solidcraft-cli-${VERSION_NUM}-macos-universal.zip" ;;
  Linux/x86_64)             asset="solidcraft-${VERSION_NUM}-linux-x86_64.tar.gz" ;;
  Linux/aarch64|Linux/arm64) asset="solidcraft-${VERSION_NUM}-linux-aarch64.tar.gz" ;;
  *) echo "不支持的平台 $os/$arch；请从 releases 页面手动下载" >&2; exit 1 ;;
esac

url="${PROXY}https://github.com/${REPO}/releases/download/${VERSION}/${asset}"
tmp="$(mktemp -d)"
trap 'rm -rf "$tmp"' EXIT

echo "下载 $asset"
curl -fL --progress-bar -o "$tmp/$asset" "$url"

case "$asset" in
  *.zip) unzip -q "$tmp/$asset" -d "$tmp/x" ;;
  *.tar.gz) mkdir -p "$tmp/x" && tar -xzf "$tmp/$asset" -C "$tmp/x" ;;
esac

bin="$(find "$tmp/x" -type f -name solidcraft-cli -perm -u+x | head -1)"
[ -n "$bin" ] || bin="$(find "$tmp/x" -type f -name solidcraft-cli | head -1)"
[ -n "$bin" ] || { echo "压缩包里没有 solidcraft-cli" >&2; exit 1; }

mkdir -p "$PREFIX"
install -m 0755 "$bin" "$PREFIX/solidcraft-cli"
xattr -d com.apple.quarantine "$PREFIX/solidcraft-cli" 2>/dev/null || true

echo "已安装：$PREFIX/solidcraft-cli"
"$PREFIX/solidcraft-cli" --version
