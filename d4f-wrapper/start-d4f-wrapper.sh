#!/bin/bash
# deepseek4free wrapper (OpenAI-compatible, port 20233) for the dsh model picker.
# Started by start-all.sh when port 20233 is not listening.
#
# Paths (override with env vars when your layout differs):
#   D4F_VENV    — python venv with the deepseek4free (dsk) package installed
#                 (default: /data/dsh-test/deepseek4free/.venv on blackarch-pc)
#   D4F_WRAPPER — the wrapper script itself
#                 (default: d4f_wrapper.py next to this launcher)
SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
VENV="${D4F_VENV:-/data/dsh-test/deepseek4free/.venv}"
WRAPPER="${D4F_WRAPPER:-$SCRIPT_DIR/d4f_wrapper.py}"
exec "$VENV/bin/python" "$WRAPPER"
