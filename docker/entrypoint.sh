#!/bin/sh
# One image, two commands: the dashboard, or a scan that reads config.yaml.
set -eu

cd /app
mkdir -p /app/data /app/output /app/logs

mode="${1:-dashboard}"
if [ "$#" -gt 0 ]; then
    shift
fi

case "$mode" in
    dashboard)
        exec python -m src.api --host 0.0.0.0 --port 8501
        ;;
    scan)
        exec python -m src.main "$@"
        ;;
    *)
        exec "$mode" "$@"
        ;;
esac
