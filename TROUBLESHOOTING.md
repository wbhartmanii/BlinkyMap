# BlinkyMap Troubleshooting

---

## Camera error: "Cannot read properties of undefined (reading 'getUserMedia')"

**Cause:** Browsers block camera access (`getUserMedia`) on plain HTTP. It only
works on HTTPS or localhost — this is a hard browser security rule, not a bug.

**Fix:** Use the HTTPS URL the installer sets up:
```
https://<pi-ip>/plugin/blinkymap/
```
Your browser will show a self-signed certificate warning. Click
**Advanced → Proceed** (Chrome) or **Accept the Risk and Continue** (Firefox)
once. The camera will work on every subsequent visit without any prompts.

---

## WebSocket indicator dot is yellow or red

The dot in the top-right of the UI shows WebSocket connection status:
- **Green** — connected to the Python backend
- **Yellow** — connection error
- **Red** — disconnected (auto-reconnect every 3 s)

### Yellow dot on first load

The Python backend (`blinkymap_server.py`) failed to start. Check the log:

```bash
cat /tmp/blinkymap_server.log
```

**Common cause — missing Python packages:**
```bash
sudo apt-get install -y python3-numpy python3-websockets python3-requests
```
Then reload the page; `index.php` will restart the server automatically.

### Yellow dot — Content Security Policy blocked

If the browser console (F12) shows:
```
Connecting to 'ws://...:8765/' violates Content Security Policy
```
The Apache WebSocket proxy is not configured. Run the fix script:
```bash
cd /home/fpp/media/plugins/blinkymap   # or BlinkyMap
sudo bash scripts/fix_apache.sh
```

---

## Plugin page returns 404

The Apache alias for the plugin directory is missing.

```bash
cd /home/fpp/media/plugins/blinkymap   # or BlinkyMap
sudo bash scripts/fix_apache.sh
```

This writes `/etc/apache2/conf-available/blinkymap.conf`, enables it, and
reloads Apache.

---

## Python package install fails

### "externally-managed-environment" error (Debian 12 / RPi OS Bookworm)

`pip` is blocked system-wide. Use `apt` instead:
```bash
sudo apt-get install -y python3-numpy python3-websockets python3-requests
```

### `python3-websockets` not found in apt (Debian 10 / RPi OS Buster)

Install via pip:
```bash
pip3 install websockets
# or if pip3 is missing:
sudo apt-get install -y python3-pip && pip3 install websockets
```

---

## HTTPS doesn't load / port 443 refused

The self-signed SSL VirtualHost may not be enabled. Re-run the fix script:
```bash
cd /home/fpp/media/plugins/blinkymap
sudo bash scripts/fix_apache.sh
```

If you already have another SSL site configured (e.g. a pre-existing FPP HTTPS
setup), `fix_apache.sh` will skip creating a new one to avoid conflicts. In
that case your existing HTTPS URL already works — just navigate to
`https://<pi-ip>/plugin/blinkymap/`.

---

## Plugin installed but doesn't appear in FPP menu

FPP loads `menu.inc` from the plugin root to add menu entries. Verify the file
exists and is readable:
```bash
cat /home/fpp/media/plugins/blinkymap/menu.inc
```
It should output an `<a>` tag pointing to `/plugin/blinkymap/index.php`.
If the file is missing, re-install the plugin via **Plugin Manager**.

---

## Plugin was installed manually (not via Plugin Manager)

If you cloned or copied the plugin without running `fpp_install.sh`, run it now
as root to set up all dependencies and Apache config:
```bash
sudo bash /home/fpp/media/plugins/blinkymap/scripts/fpp_install.sh
```

---

## Installed on an older FPP (6.x / 7.x)

Older FPP versions stored plugins in `/home/fpp/plugins/` rather than
`/home/fpp/media/plugins/`. The scripts detect both locations automatically.
If you are running FPP 6–7 and the auto-detection fails, pass the path
explicitly:
```bash
sudo PLUGIN_DIR=/home/fpp/plugins/blinkymap bash scripts/fix_apache.sh
```

---

## Apache config test fails after running fix_apache.sh

```bash
sudo apache2ctl configtest
```

Check the output for the specific error. Common causes:

| Error | Fix |
|-------|-----|
| `proxy_wstunnel` module missing | `sudo a2enmod proxy proxy_http proxy_wstunnel` |
| `mod_ssl` not installed | `sudo apt-get install -y openssl && sudo a2enmod ssl` |
| Port 443 already in use by another VirtualHost | Edit `/etc/apache2/sites-available/blinkymap-ssl.conf` and change `_default_:443` to your Pi's IP |
| PHP-FPM socket path wrong | Update the `SetHandler` line in `/etc/apache2/conf-available/blinkymap.conf` to match the actual socket: `ls /run/php/` |

---

## Still stuck?

Open an issue at https://github.com/wbhartmanii/BlinkyMap/issues and include:
- FPP version (`cat /etc/fpp/release`)
- OS (`cat /etc/os-release`)
- Output of `cat /tmp/blinkymap_server.log`
- Output of `sudo apache2ctl configtest`
