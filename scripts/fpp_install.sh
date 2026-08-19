#!/bin/bash
# BlinkyMap FPP Plugin installer
# Called by FPP Plugin Manager after extracting the plugin zip.
# Supports: Raspberry Pi OS (Bullseye/Bookworm) and Debian 11/12 x86.
# FPP compatibility: 6.x+ (8.0+ recommended for plugin manager).

set -e

PLUGIN_DIR="$(cd "$(dirname "$0")/.." && pwd)"   # scripts/ → plugin root
WWW_DIR="$PLUGIN_DIR/www/blinkymap"

# ── OS detection (informational) ───────────────────────────────────────────────
. /etc/os-release 2>/dev/null || true
echo "BlinkyMap: installing on ${PRETTY_NAME:-unknown OS}"

# ── Detect FPP DocumentRoot from running Apache config ────────────────────────
# -R (not -r): sites-enabled entries are symlinks into sites-available,
# and plain -r will not follow them.
FPP_DOCROOT=$(grep -Rh "^[[:space:]]*DocumentRoot" /etc/apache2/sites-enabled/ 2>/dev/null \
    | grep -v "#" | awk '{print $2}' | head -1)
[ -z "$FPP_DOCROOT" ] && FPP_DOCROOT="/opt/fpp/www"
echo "BlinkyMap: detected FPP DocumentRoot: ${FPP_DOCROOT}"

# ── System dependencies ────────────────────────────────────────────────────────
echo "BlinkyMap: installing system dependencies..."
if command -v apt-get &>/dev/null; then
    # Package lists on an FPP image are often stale; a refresh failure is not fatal.
    if ! apt-get update -q >/dev/null 2>&1; then
        echo "  apt-get update failed — continuing with cached lists"
    fi
    apt-get install -y -q openssl python3-numpy python3-requests 2>&1 | tail -5

    # python3-websockets not in Buster repos — try apt, fall back to pip
    if ! apt-get install -y -q python3-websockets 2>/dev/null; then
        echo "  python3-websockets not in apt, trying pip..."
        if command -v pip3 &>/dev/null; then
            pip3 install --quiet websockets 2>&1 | tail -3
        elif command -v python3 &>/dev/null; then
            python3 -m pip install --break-system-packages --quiet websockets 2>&1 | tail -3
        else
            echo "WARNING: could not install python3-websockets"
        fi
    fi
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
THREE_OK=1

# A CDN failure must not abort the install — everything except the 3D viewer
# still works, and the files can be dropped in later.
curl -fsSL --retry 3 --retry-delay 2 --connect-timeout 15 \
     "${THREE_BASE}/build/three.module.min.js" \
     -o "$WWW_DIR/vendor/three.module.min.js" || THREE_OK=0

curl -fsSL --retry 3 --retry-delay 2 --connect-timeout 15 \
     "${THREE_BASE}/examples/jsm/controls/OrbitControls.js" \
     -o "$WWW_DIR/vendor/OrbitControls.js" || THREE_OK=0

# Guard against a truncated or HTML error-page download
for f in three.module.min.js OrbitControls.js; do
    if [ ! -s "$WWW_DIR/vendor/$f" ] || head -c 200 "$WWW_DIR/vendor/$f" | grep -qi "<!doctype\|<html"; then
        THREE_OK=0
        rm -f "$WWW_DIR/vendor/$f"
    fi
done

if [ "$THREE_OK" = "1" ]; then
    # Patch OrbitControls import to use local three.module
    sed -i "s|from 'three'|from './three.module.min.js'|g" \
        "$WWW_DIR/vendor/OrbitControls.js"
    echo "  Three.js ${THREE_VERSION} installed."
else
    echo "  WARNING: Three.js download failed — the 3D Model tab will not render."
    echo "           Re-run this script once the Pi has internet access."
fi

# ── Apache alias + WebSocket proxy ─────────────────────────────────────────────
# Prefer newest PHP-FPM socket. Set before both Apache blocks — the SSL vhost
# below needs it too.
PHP_SOCK=$(ls /run/php/php*-fpm.sock 2>/dev/null | sort -rV | head -1)
[ -z "$PHP_SOCK" ] && PHP_SOCK="/run/php/php7.4-fpm.sock"

echo "BlinkyMap: configuring Apache..."
if [ -d /etc/apache2/conf-available ]; then
    PLUGIN_NAME="$(basename "$PLUGIN_DIR")"
    CONF="/etc/apache2/conf-available/blinkymap.conf"

    a2enmod proxy proxy_http proxy_wstunnel >/dev/null 2>&1 || true

    a2enmod headers >/dev/null 2>&1 || true

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
    # FPP serves static assets with 'max-age=31536000, immutable'. That is fine
    # for the query-versioned JS and CSS, but fatal for the HTML that names those
    # versions: a cached page keeps requesting last week's ?v= forever, and the
    # user tests old code no matter how often they reload.
    <FilesMatch "\\.html\$">
        Header set Cache-Control "no-store, no-cache, must-revalidate, max-age=0"
        Header set Pragma "no-cache"
    </FilesMatch>
</Directory>
APACHECONF

    a2enconf blinkymap >/dev/null 2>&1 || true
else
    echo "WARNING: Apache conf-available not found — skipping Apache config"
fi

# ── HTTPS with self-signed certificate ────────────────────────────────────────
# getUserMedia (camera) requires a secure context in all modern browsers.
echo "BlinkyMap: enabling HTTPS..."
if [ -d /etc/apache2/sites-available ] && command -v openssl &>/dev/null; then

    # Skip if any SSL site is already enabled
    if ls /etc/apache2/sites-enabled/ 2>/dev/null | grep -qi ssl; then
        echo "  SSL already configured — skipping."
    else
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
            echo "  Self-signed certificate generated (valid 10 years)."
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
        echo "  HTTPS enabled. Accept the browser's self-signed cert warning once."
    fi
else
    echo "  WARNING: openssl not found — camera will need the Chrome insecure-origin flag."
fi

# ── Reload Apache (systemd or SysVinit) ───────────────────────────────────────
if [ -d /etc/apache2 ]; then
    if apache2ctl configtest >/dev/null 2>&1; then
        if command -v systemctl &>/dev/null && systemctl is-system-running &>/dev/null; then
            systemctl reload apache2
        else
            service apache2 reload 2>/dev/null || apache2ctl graceful
        fi
        echo "BlinkyMap: Apache reloaded."
    else
        echo "WARNING: Apache config test failed — check /etc/apache2/conf-available/blinkymap.conf"
    fi
fi

MY_IP=$(hostname -I 2>/dev/null | awk '{print $1}' || echo "your-pi-ip")
echo ""
echo "BlinkyMap install complete."
echo "  HTTP:  http://${MY_IP}/plugin/blinkymap/"
echo "  HTTPS: https://${MY_IP}/plugin/blinkymap/  ← use this for camera access"
