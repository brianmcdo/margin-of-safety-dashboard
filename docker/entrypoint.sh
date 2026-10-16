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
        exec streamlit run src/dashboard/app.py \
            --server.address=0.0.0.0 \
            --server.port=8501 \
            --server.headless=true
        ;;
    scan)
        exec python -m src.main "$@"
        ;;
    *)
        exec "$mode" "$@"
        ;;
esac
