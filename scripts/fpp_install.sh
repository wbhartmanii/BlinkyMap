#!/bin/bash
# BlinkyMap FPP Plugin installer
# Called by FPP Plugin Manager after extracting the plugin zip.

set -e

PLUGIN_DIR="$(cd "$(dirname "$0")/.." && pwd)"   # scripts/ → plugin root
WWW_DIR="$PLUGIN_DIR/www/blinkymap"

# ── Python dependencies ────────────────────────────────────────────────────────
echo "BlinkyMap: installing Python dependencies..."
if command -v apt-get &>/dev/null; then
    # Debian/Ubuntu (FPP on Pi, Proxmox) — apt packages avoid the
    # externally-managed-environment restriction on Python 3.11+
    apt-get install -y -q python3-numpy python3-websockets python3-requests 2>&1 | tail -5
elif command -v pip3 &>/dev/null; then
    pip3 install --quiet --upgrade numpy websockets requests 2>&1 | tail -5
elif command -v python3 &>/dev/null; then
    python3 -m pip install --break-system-packages --quiet --upgrade \
        numpy websockets requests 2>&1 | tail -5
else
    echo "WARNING: no package manager found — skipping Python deps"
fi

# ── Three.js vendor files ──────────────────────────────────────────────────────
echo "BlinkyMap: downloading Three.js..."
mkdir -p "$WWW_DIR/vendor"

THREE_VERSION="0.160.0"
THREE_BASE="https://cdn.jsdelivr.net/npm/three@${THREE_VERSION}"

curl -fsSL "${THREE_BASE}/build/three.module.min.js" \
     -o "$WWW_DIR/vendor/three.module.min.js"

curl -fsSL "${THREE_BASE}/examples/jsm/controls/OrbitControls.js" \
     -o "$WWW_DIR/vendor/OrbitControls.js"

# Patch OrbitControls import to use local three.module
sed -i "s|from 'three'|from './three.module.min.js'|g" \
    "$WWW_DIR/vendor/OrbitControls.js"

# ── Apache alias + WebSocket proxy ─────────────────────────────────────────────
echo "BlinkyMap: configuring Apache..."
if [ -d /etc/apache2/conf-available ]; then
    PLUGIN_NAME="$(basename "$PLUGIN_DIR")"
    CONF="/etc/apache2/conf-available/blinkymap.conf"

    PHP_SOCK=$(ls /run/php/php*-fpm.sock 2>/dev/null | head -1)
    [ -z "$PHP_SOCK" ] && PHP_SOCK="/run/php/php8.2-fpm.sock"

    a2enmod proxy proxy_http proxy_wstunnel >/dev/null 2>&1 || true

    cat > "$CONF" <<APACHECONF
# Proxy WebSocket at same-origin path so FPP's CSP 'self' allows it
ProxyPass /blinkymap-ws ws://127.0.0.1:8765
ProxyPassReverse /blinkymap-ws ws://127.0.0.1:8765

Alias /plugin/${PLUGIN_NAME} /home/fpp/media/plugins/${PLUGIN_NAME}/www
<Directory /home/fpp/media/plugins/${PLUGIN_NAME}/www>
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
else
    echo "WARNING: Apache conf-available not found — skipping Apache config"
fi

# ── HTTPS with self-signed certificate ────────────────────────────────────────
# Required for getUserMedia (camera) to work in any browser without flags.
echo "BlinkyMap: enabling HTTPS..."
if [ -d /etc/apache2/sites-available ] && command -v openssl &>/dev/null; then

    # Only create the SSL site if nothing is already listening on 443
    if ! ls /etc/apache2/sites-enabled/ 2>/dev/null | grep -qi ssl; then
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
            echo "BlinkyMap: self-signed certificate generated (valid 10 years)."
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
        echo "BlinkyMap: HTTPS site enabled."
        echo "  → Accept the browser's self-signed cert warning once, then camera works."
    else
        echo "BlinkyMap: SSL already configured, skipping."
    fi
else
    echo "WARNING: openssl not found — skipping HTTPS (camera needs Chrome flag workaround)"
fi

# ── Reload Apache once after all config changes ────────────────────────────────
if [ -d /etc/apache2 ]; then
    apache2ctl configtest >/dev/null 2>&1 \
        && systemctl reload apache2 && echo "BlinkyMap: Apache reloaded." \
        || echo "WARNING: Apache config test failed — check /etc/apache2/conf-available/blinkymap.conf"
fi

echo "BlinkyMap: install complete."
