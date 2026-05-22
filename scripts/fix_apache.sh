#!/bin/bash
# Configures Apache for BlinkyMap: alias, WebSocket proxy, and HTTPS.
# Supports Raspberry Pi OS (Bullseye/Bookworm) and Debian 11/12 x86.
#
# Run once manually if the plugin was installed before this script existed:
#   curl -fsSL https://raw.githubusercontent.com/wbhartmanii/BlinkyMap/main/scripts/fix_apache.sh | sudo bash

set -euo pipefail

. /etc/os-release 2>/dev/null || true
echo "BlinkyMap fix_apache: running on ${PRETTY_NAME:-unknown OS}"

CONF="/etc/apache2/conf-available/blinkymap.conf"

# Detect the PHP-FPM socket (handles php7.4, php8.1, php8.2, php8.3, etc.)
PHP_SOCK=$(ls /run/php/php*-fpm.sock 2>/dev/null | head -1)
[ -z "$PHP_SOCK" ] && PHP_SOCK="/run/php/php8.2-fpm.sock"

# ── Apache alias + WebSocket proxy ────────────────────────────────────────────
a2enmod proxy proxy_http proxy_wstunnel >/dev/null 2>&1 || true

cat > "$CONF" <<APACHECONF
# Proxy WebSocket at same-origin path so FPP's CSP 'self' allows it
ProxyPass /blinkymap-ws ws://127.0.0.1:8765
ProxyPassReverse /blinkymap-ws ws://127.0.0.1:8765

Alias /plugin/blinkymap /home/fpp/media/plugins/blinkymap/www
<Directory /home/fpp/media/plugins/blinkymap/www>
    Options FollowSymLinks
    AllowOverride None
    Require all granted
    DirectoryIndex index.php index.html
    <FilesMatch "\\.php\$">
        SetHandler "proxy:unix:${PHP_SOCK}|fcgi://localhost"
    </FilesMatch>
</Directory>
APACHECONF

a2enconf blinkymap >/dev/null 2>&1 || true

# ── HTTPS with self-signed certificate ────────────────────────────────────────
if command -v openssl &>/dev/null && ! ls /etc/apache2/sites-enabled/ 2>/dev/null | grep -qi ssl; then
    SSL_DIR="/etc/apache2/ssl/blinkymap"
    mkdir -p "$SSL_DIR"

    if [ ! -f "$SSL_DIR/server.crt" ]; then
        MY_IP=$(hostname -I 2>/dev/null | awk '{print $1}')
        MY_HOST=$(hostname 2>/dev/null || echo "fpp")
        SAN="DNS:${MY_HOST},DNS:localhost"
        [ -n "$MY_IP" ] && SAN="${SAN},IP:${MY_IP}"

        # -addext requires OpenSSL 1.1.1+ (Bullseye/Bookworm); fall back for older builds
        openssl req -x509 -nodes -days 3650 -newkey rsa:2048 \
            -keyout "$SSL_DIR/server.key" \
            -out    "$SSL_DIR/server.crt" \
            -subj   "/CN=${MY_HOST}/O=BlinkyMap-FPP" \
            -addext "subjectAltName=${SAN}" \
            2>/dev/null \
        || openssl req -x509 -nodes -days 3650 -newkey rsa:2048 \
            -keyout "$SSL_DIR/server.key" \
            -out    "$SSL_DIR/server.crt" \
            -subj   "/CN=${MY_HOST}/O=BlinkyMap-FPP" \
            2>/dev/null
        chmod 600 "$SSL_DIR/server.key"
        echo "Self-signed certificate generated (valid 10 years)."
    fi

    a2enmod ssl >/dev/null 2>&1 || true

    cat > /etc/apache2/sites-available/blinkymap-ssl.conf <<SSLCONF
<IfModule mod_ssl.c>
    <VirtualHost _default_:443>
        DocumentRoot /opt/fpp/www
        SSLEngine on
        SSLCertificateFile    ${SSL_DIR}/server.crt
        SSLCertificateKeyFile ${SSL_DIR}/server.key
    </VirtualHost>
</IfModule>
SSLCONF

    a2ensite blinkymap-ssl >/dev/null 2>&1 || true
    echo "HTTPS enabled. Accept the browser's self-signed cert warning once — camera will then work."
else
    echo "SSL already configured or openssl not found — skipping HTTPS."
fi

# ── Reload Apache ──────────────────────────────────────────────────────────────
apache2ctl configtest
systemctl reload apache2

MY_IP=$(hostname -I 2>/dev/null | awk '{print $1}' || echo "your-pi-ip")
echo ""
echo "BlinkyMap ready:"
echo "  HTTP:  http://${MY_IP}/plugin/blinkymap/"
echo "  HTTPS: https://${MY_IP}/plugin/blinkymap/  ← use this for camera access"
