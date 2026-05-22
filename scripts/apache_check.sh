#!/bin/bash
hr() { echo "======================================== $1"; }

hr "Apache enabled sites/configs"
ls /etc/apache2/sites-enabled/ 2>/dev/null
ls /etc/apache2/conf-enabled/ 2>/dev/null

hr "All Apache config content"
cat /etc/apache2/sites-enabled/*.conf 2>/dev/null || cat /etc/apache2/sites-enabled/* 2>/dev/null

hr "Apache conf-enabled"
cat /etc/apache2/conf-enabled/*.conf 2>/dev/null

hr "FPP etc apache config"
find /opt/fpp/etc -name '*.conf' -o -name '*apache*' 2>/dev/null | head -10
cat /opt/fpp/etc/apache2*.conf 2>/dev/null || find /opt/fpp/etc -name '*.conf' | xargs cat 2>/dev/null | head -100

hr "How existing working plugin URL routes (if any)"
find /etc/apache2 -type f | xargs grep -l 'plugin\|Plugin\|media' 2>/dev/null
curl -s -o /dev/null -w '%{http_code}' http://localhost/plugin/blinkymap/index.php
echo

hr "DocumentRoot"
grep -r 'DocumentRoot\|Alias\|plugin' /etc/apache2/sites-enabled/ 2>/dev/null | head -30
