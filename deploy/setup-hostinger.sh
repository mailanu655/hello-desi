#!/bin/bash
# =============================================================
# Hello Desi — Hostinger Cloud VPS Setup Script
# Run as root on a fresh Ubuntu 22.04/24.04 Hostinger VPS
# Usage: sudo bash setup-hostinger.sh
# =============================================================

set -euo pipefail

APP_DIR="/opt/hello-desi"
APP_USER="hellodesi"
DOMAIN="${1:-}"  # Pass domain as first argument, or set later

echo "=========================================="
echo " Hello Desi — Hostinger VPS Setup"
echo "=========================================="

# ---- Step 1: System packages ----
echo "[1/7] Updating system and installing packages..."
apt update && apt upgrade -y
apt install -y python3.11 python3.11-venv python3-pip nginx certbot python3-certbot-nginx git ufw curl

# ---- Step 2: Firewall ----
echo "[2/7] Configuring firewall..."
ufw allow OpenSSH
ufw allow 'Nginx Full'
ufw --force enable

# ---- Step 3: Create app user ----
echo "[3/7] Creating application user..."
if ! id "$APP_USER" &>/dev/null; then
    useradd --system --create-home --shell /bin/bash "$APP_USER"
fi

# ---- Step 4: Deploy application code ----
echo "[4/7] Setting up application directory..."
mkdir -p "$APP_DIR"

# Copy code (assumes you've uploaded the Hello Desi folder to /tmp/hello-desi)
if [ -d "/tmp/hello-desi" ]; then
    cp -r /tmp/hello-desi/* "$APP_DIR/"
    echo "  Copied code from /tmp/hello-desi"
else
    echo "  WARNING: /tmp/hello-desi not found."
    echo "  Upload your code first: scp -r 'Hello Desi/' root@YOUR_VPS_IP:/tmp/hello-desi/"
    echo "  Then re-run this script."
fi

# ---- Step 5: Python virtual environment ----
echo "[5/7] Setting up Python virtual environment..."
cd "$APP_DIR"
python3.11 -m venv venv
source venv/bin/activate
pip install --upgrade pip
pip install -r requirements.txt
deactivate

# Set ownership
chown -R "$APP_USER":www-data "$APP_DIR"

# ---- Step 6: Systemd service ----
echo "[6/7] Installing systemd service..."
cp "$APP_DIR/deploy/hellodesi.service" /etc/systemd/system/hellodesi.service
systemctl daemon-reload
systemctl enable hellodesi
systemctl start hellodesi
echo "  Service status:"
systemctl status hellodesi --no-pager || true

# ---- Step 7: Nginx reverse proxy ----
echo "[7/7] Configuring Nginx..."
cp "$APP_DIR/deploy/nginx-hellodesi.conf" /etc/nginx/sites-available/hellodesi

# Replace domain placeholder if provided
if [ -n "$DOMAIN" ]; then
    sed -i "s/YOUR_DOMAIN_OR_IP/$DOMAIN/g" /etc/nginx/sites-available/hellodesi
    echo "  Domain set to: $DOMAIN"
else
    echo "  NOTE: Edit /etc/nginx/sites-available/hellodesi and replace YOUR_DOMAIN_OR_IP"
fi

ln -sf /etc/nginx/sites-available/hellodesi /etc/nginx/sites-enabled/
rm -f /etc/nginx/sites-enabled/default
nginx -t && systemctl reload nginx

echo ""
echo "=========================================="
echo " Setup Complete!"
echo "=========================================="
echo ""
echo "Next steps:"
echo "  1. Make sure your .env file is at $APP_DIR/.env"
echo "  2. Test: curl http://YOUR_VPS_IP/health"
echo "  3. SSL:  sudo certbot --nginx -d YOUR_DOMAIN"
echo "  4. Logs: sudo journalctl -u hellodesi -f"
echo ""
echo "Useful commands:"
echo "  sudo systemctl restart hellodesi   # Restart the bot"
echo "  sudo systemctl status hellodesi    # Check status"
echo "  sudo journalctl -u hellodesi -f    # View live logs"
echo ""
