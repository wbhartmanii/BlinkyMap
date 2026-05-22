#!/bin/bash
# Adds the Apache alias that maps /plugin/blinkymap/ to the plugin's www/ dir.
# Run once manually if the plugin was installed before this script existed:
#   curl -fsSL https://raw.githubusercontent.com/wbhartmanii/BlinkyMap/main/scripts/fix_apache.sh | sudo bash

set -euo pipefail

CONF="/etc/apache2/conf-available/blinkymap.conf"

# Detect the PHP-FPM socket (php8.x-fpm.sock or php-fpm.sock)
PHP_SOCK=$(ls /run/php/php*-fpm.sock 2>/dev/null | head -1)
if [ -z "$PHP_SOCK" ]; then
    PHP_SOCK="/run/php/php8.2-fpm.sock"
fi

a2enmod headers >/dev/null 2>&1 || true

cat > "$CONF" <<APACHECONF
Alias /plugin/blinkymap /home/fpp/media/plugins/blinkymap/www
<Directory /home/fpp/media/plugins/blinkymap/www>
    Options FollowSymLinks
    AllowOverride None
    Require all granted
    DirectoryIndex index.php index.html
    <FilesMatch "\\.php\$">
        SetHandler "proxy:unix:${PHP_SOCK}|fcgi://localhost"
    </FilesMatch>
    Header always unset Content-Security-Policy
</Directory>
APACHECONF

a2enconf blinkymap >/dev/null 2>&1 || true
apache2ctl configtest
systemctl reload apache2
echo "BlinkyMap: Apache alias active (PHP-FPM socket: ${PHP_SOCK})"
echo "URL: http://$(hostname -I | awk '{print $1}')/plugin/blinkymap/"
