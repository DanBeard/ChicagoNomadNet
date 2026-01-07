#!/bin/bash
# Install Security Now! services
#
# This script sets up systemd services and cron jobs for the Security Now! system.
# Run with sudo.

set -e

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_DIR="$(dirname "$SCRIPT_DIR")"

echo "=== Security Now! Service Installer ==="
echo ""

# Check if running as root
if [ "$EUID" -ne 0 ]; then
    echo "Please run with sudo"
    exit 1
fi

# Install systemd services
echo "Installing systemd services..."

cp "$SCRIPT_DIR/security-host.service" /etc/systemd/system/
cp "$SCRIPT_DIR/llama-server.service" /etc/systemd/system/

# Reload systemd
systemctl daemon-reload

echo ""
echo "Services installed. To enable and start:"
echo "  sudo systemctl enable security-host llama-server"
echo "  sudo systemctl start security-host llama-server"
echo ""

# Install cron jobs
echo "Installing cron jobs..."
cp "$SCRIPT_DIR/security-now.cron" /etc/cron.d/security-now
chmod 644 /etc/cron.d/security-now

echo ""
echo "Cron jobs installed."
echo ""

# Create log files
touch /var/log/security_now_fetch.log
touch /var/log/security_now_digest.log
touch /var/log/security_now_cleanup.log
chown v:v /var/log/security_now_*.log

echo "Log files created in /var/log/"
echo ""

# Initialize database
echo "Initializing database..."
sudo -u v /home/v/ChicagoNomadNet/venv/bin/python -c "
import sys
sys.path.insert(0, '/home/v/ChicagoNomadNet')
from projects.security_now.news import news_db
news_db.init_db()
print('Database initialized.')
"

echo ""
echo "=== Installation Complete ==="
echo ""
echo "Next steps:"
echo "1. Edit /etc/systemd/system/security-host.service to set SN_AUTHKEY"
echo "2. Copy your fine-tuned model to /home/v/models/security_now_mistral7b.gguf"
echo "3. Start services: sudo systemctl start security-host llama-server"
echo "4. Fetch initial news: python -m projects.security_now.news.news_fetcher"
echo "5. Generate first digest: python -m projects.security_now.generator.digest_generator"
