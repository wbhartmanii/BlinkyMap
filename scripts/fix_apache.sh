#!/bin/bash
# Configures Apache for BlinkyMap: alias, WebSocket proxy, and HTTPS.
# Supports Raspberry Pi OS (Bullseye/Bookworm) and Debian 11/12 x86.
# FPP compatibility: 6.x+ (8.0+ recommended for plugin manager).
#
# Run once manually if the plugin was installed before this script existed:
#   curl -fsSL https://raw.githubusercontent.com/wbhartmanii/BlinkyMap/main/scripts/fix_apache.sh | sudo bash

set -euo pipefail

. /etc/os-release 2>/dev/null || true
echo "BlinkyMap fix_apache: running on ${PRETTY_NAME:-unknown OS}"

CONF="/etc/apache2/conf-available/blinkymap.conf"

# ── Locate the BlinkyMap plugin directory ─────────────────────────────────────
# FPP 8.0+ uses /home/fpp/media/plugins/; FPP 6-7 used /home/fpp/plugins/
PLUGIN_DIR=""
for candidate in \
    "/home/fpp/media/plugins/blinkymap" \
    "/home/fpp/media/plugins/BlinkyMap" \
    "/home/fpp/plugins/blinkymap" \
    "/home/fpp/plugins/BlinkyMap"; do
    [ -d "$candidate" ] && { PLUGIN_DIR="$candidate"; break; }
done

if [ -z "$PLUGIN_DIR" ]; then
    echo "ERROR: BlinkyMap plugin not found in /home/fpp/media/plugins/ or /home/fpp/plugins/"
    echo "Install the plugin first, then re-run this script."
    exit 1
fi
PLUGIN_NAME="$(basename "$PLUGIN_DIR")"
echo "  Found plugin at: ${PLUGIN_DIR}"

# ── Detect FPP DocumentRoot from running Apache config ────────────────────────
FPP_DOCROOT=$(grep -rh "^[[:space:]]*DocumentRoot" /etc/apache2/sites-enabled/ 2>/dev/null \
    | grep -v "#" | awk '{print $2}' | head -1)
[ -z "$FPP_DOCROOT" ] && FPP_DOCROOT="/opt/fpp/www"
echo "  Detected FPP DocumentRoot: ${FPP_DOCROOT}"

# ── Detect PHP-FPM socket (newest version wins) ───────────────────────────────
PHP_SOCK=$(ls /run/php/php*-fpm.sock 2>/dev/null | sort -rV | head -1)
[ -z "$PHP_SOCK" ] && PHP_SOCK="/run/php/php7.4-fpm.sock"
echo "  PHP-FPM socket: ${PHP_SOCK}"

# ── Apache alias + WebSocket proxy ────────────────────────────────────────────
a2enmod proxy proxy_http proxy_wstunnel >/dev/null 2>&1 || true

cat > "$CONF" <<APACHECONF
# Proxy WebSocket at same-origin path so FPP's CSP 'self' allows it
ProxyPass /blinkymap-ws ws://127.0.0.1:8765
ProxyPassReverse /blinkymap-ws ws://127.0.0.1:8765

Alias /plugin/${PLUGIN_NAME} ${PLUGIN_DIR}/www
<Directory ${PLUGIN_DIR}/www>
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
# Always (re)write blinkymap-ssl.conf — ensures the Directory block is present
# even if the site was installed by an older version of this script.
if command -v openssl &>/dev/null && [ -d /etc/apache2/sites-available ]; then
    SSL_DIR="/etc/apache2/ssl/blinkymap"
    mkdir -p "$SSL_DIR"

    if [ ! -f "$SSL_DIR/server.crt" ]; then
        MY_IP=$(hostname -I 2>/dev/null | awk '{print $1}')
        MY_HOST=$(hostname 2>/dev/null || echo "fpp")
        SAN="DNS:${MY_HOST},DNS:localhost"
        [ -n "$MY_IP" ] && SAN="${SAN},IP:${MY_IP}"

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
        DocumentRoot ${FPP_DOCROOT}
        SSLEngine on
        SSLCertificateFile    ${SSL_DIR}/server.crt
        SSLCertificateKeyFile ${SSL_DIR}/server.key
        <Directory ${FPP_DOCROOT}>
            Options -Indexes +FollowSymLinks
            AllowOverride All
            Require all granted
            <FilesMatch "\.php\$">
                SetHandler "proxy:unix:${PHP_SOCK}|fcgi://localhost"
            </FilesMatch>
        </Directory>
    </VirtualHost>
</IfModule>
SSLCONF

    a2ensite blinkymap-ssl >/dev/null 2>&1 || true
    echo "HTTPS configured. Accept the browser's self-signed cert warning once — camera will then work."
else
    echo "openssl not found or no Apache sites-available — skipping HTTPS."
fi

# ── Reload Apache (systemd or SysVinit) ───────────────────────────────────────
apache2ctl configtest
if command -v systemctl &>/dev/null && systemctl is-system-running &>/dev/null; then
    systemctl reload apache2
else
    service apache2 reload 2>/dev/null || apache2ctl graceful
fi

MY_IP=$(hostname -I 2>/dev/null | awk '{print $1}' || echo "your-pi-ip")
echo ""
echo "BlinkyMap ready:"
echo "  HTTP:  http://${MY_IP}/plugin/${PLUGIN_NAME}/"
echo "  HTTPS: https://${MY_IP}/plugin/${PLUGIN_NAME}/  ← use this for camera access"
