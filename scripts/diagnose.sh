#!/bin/bash
# BlinkyMap FPP layout diagnostics
hr() { echo "========================================"; }

hr; echo "FPP INSTALL PATHS"
which fppd 2>/dev/null || find /opt /usr /usr/local -name fppd 2>/dev/null | head -3
find / -maxdepth 6 -name "fpp_version" 2>/dev/null | head -5
ls /opt/fpp/ 2>/dev/null || echo "no /opt/fpp"

hr; echo "WEB SERVER RUNNING"
ps aux | grep -E 'apache|nginx|lighttpd|php-fpm' | grep -v grep
systemctl list-units --type=service --state=running 2>/dev/null | grep -iE 'apache|nginx|light|php|web'

hr; echo "FPP WWW ROOT"
ls /opt/fpp/www/ 2>/dev/null || ls /usr/share/fpp/www/ 2>/dev/null || find / -maxdepth 6 -path '*/fpp/www' -type d 2>/dev/null | head -3

hr; echo "FPP PLUGIN LOADER (which PHP files reference menu.inc or plugin)"
find / -maxdepth 8 -path '*/fpp*' -name '*.php' 2>/dev/null | xargs grep -l 'menu\.inc\|menuEntries\|loadPlugin' 2>/dev/null | head -10

hr; echo "FPP API STATUS"
curl -s http://localhost/api/fppd/status 2>/dev/null | python3 -m json.tool 2>/dev/null | grep -E 'version|Version' | head -5 || echo "API not reachable"

hr; echo "PLUGIN LIST FROM API"
curl -s http://localhost/api/plugin/list 2>/dev/null || echo "no plugin API response"

hr; echo "PLUGINS DIR LISTING"
ls -la /home/fpp/media/plugins/

hr; echo "FPP LOGS (last 30 lines)"
sudo journalctl -u fppd --no-pager -n 30 2>/dev/null
