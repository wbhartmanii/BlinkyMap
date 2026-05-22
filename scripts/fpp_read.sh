#!/bin/bash
# Read FPP's plugin loading source to understand registration requirements
hr() { echo "======================================== $1"; }

hr "FPP menu.inc (format reference)"
cat /opt/fpp/www/menu.inc 2>/dev/null | head -60

hr "common.php plugin section"
grep -n -A 5 -B 2 'plugin\|menu\.inc\|menuEntries\|pluginMenu' /opt/fpp/www/common.php 2>/dev/null | head -80

hr "plugin.php (API handler)"
cat /opt/fpp/www/plugin.php 2>/dev/null | head -100

hr "FPP API - list plugins properly"
curl -s http://localhost/api/plugins 2>/dev/null
echo
curl -s 'http://localhost/api/plugin/blinkymap' 2>/dev/null
echo

hr "media/config plugin files"
ls /home/fpp/media/config/ 2>/dev/null
echo "---"
ls /home/fpp/media/config/ | grep -i plugin
cat /home/fpp/media/config/plugin*.* 2>/dev/null || echo "(no plugin config files)"

hr "Any existing installed plugins"
find /home/fpp/media/plugins -name 'pluginInfo.json' | while read f; do
  echo "--- $f"
  cat "$f"
done

hr "FPP plugin install API (try POST)"
curl -s -X POST http://localhost/api/plugin/blinkymap/install 2>/dev/null || echo "(no response)"
