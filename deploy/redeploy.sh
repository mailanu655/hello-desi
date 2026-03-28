#!/bin/bash
# =============================================================
# Hello Desi — Quick Redeploy Script
# Run on VPS after uploading new code to /tmp/hello-desi/
# Usage: sudo bash /opt/hello-desi/deploy/redeploy.sh
# =============================================================

set -euo pipefail

APP_DIR="/opt/hello-desi"

echo "Redeploying Hello Desi..."

# Stop the service
systemctl stop hellodesi

# Copy new code (preserve .env and venv)
cp "$APP_DIR/.env" /tmp/.env.backup
rsync -av --exclude='venv' --exclude='.env' --exclude='__pycache__' /tmp/hello-desi/ "$APP_DIR/"
cp /tmp/.env.backup "$APP_DIR/.env"

# Update dependencies
cd "$APP_DIR"
source venv/bin/activate
pip install -r requirements.txt --quiet
deactivate

# Fix ownership and restart
chown -R hellodesi:www-data "$APP_DIR"
systemctl start hellodesi

echo "Done! Checking status..."
sleep 2
systemctl status hellodesi --no-pager
curl -s http://127.0.0.1:8000/health
echo ""
