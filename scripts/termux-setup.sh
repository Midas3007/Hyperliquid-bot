#!/data/data/com.termux/files/usr/bin/bash
# HEVI one-time setup on Termux (Android). Run from the repo folder:  bash scripts/termux-setup.sh
# Builds the Python dependencies on the phone; the Rust parts (pydantic-core, jiter) take a while the first time.
set -e
cd "$(dirname "$0")/.."

echo "== Installing system packages (Python, compilers, Rust) =="
pkg update -y
pkg install -y python git clang rust make binutils libffi openssl pkg-config

echo "== Installing Python packages (can take 20-40 minutes on a phone) =="
export ANDROID_API_LEVEL="$(getprop ro.build.version.sdk)"   # Rust builds (maturin) need this on Android
export CARGO_BUILD_JOBS=2                                    # gentler on the phone's CPU and battery
pip install -r requirements.txt

echo "== Preparing config =="
if [ ! -f .env ]; then
  cp .env.example .env
fi
sed -i 's|^DATA_DIR=/data.*|DATA_DIR=./data|' .env            # /data isn't writable on Android
mkdir -p data

echo
echo "Done. Next:"
echo "  1. nano .env      (fill in TELEGRAM_BOT_TOKEN and ANTHROPIC_API_KEY; save with Ctrl+O, exit Ctrl+X)"
echo "  2. bash scripts/termux-run.sh"
