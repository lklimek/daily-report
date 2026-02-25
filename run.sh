#!/usr/bin/env bash
# Run the daily report tool.
# All arguments are forwarded to the Python module.
#
# Examples:
#   ./run.sh
#   ./run.sh --date 2026-02-10
#   ./run.sh --from 2026-02-01 --to 2026-02-07
#   ./run.sh --org myorg --user someone

set -euo pipefail
cd "$(dirname "$0")"
clear
exec python3 -m daily_report "$@"
