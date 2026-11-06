#!/data/data/com.termux/files/usr/bin/bash
# Run HEVI on Termux and keep it running: holds a wake lock so Android doesn't suspend it, and restarts it
# 30 seconds after any crash. Stop it with Ctrl+C twice.
cd "$(dirname "$0")/.."
termux-wake-lock 2>/dev/null || echo "(termux-wake-lock unavailable - keep the Termux notification active)"
while true; do
  python main.py
  echo "HEVI stopped (exit $?) - restarting in 30s. Ctrl+C to quit."
  sleep 30
done
