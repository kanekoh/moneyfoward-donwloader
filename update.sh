#!/bin/bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"

set -a
source "$SCRIPT_DIR/.env"
set +a

cd "$SCRIPT_DIR"
exec python3 "$SCRIPT_DIR/download_csv.py" "$@"
