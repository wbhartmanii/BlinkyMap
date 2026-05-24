# Security Policy

## Scope

BlinkyMap is a **local network tool** — it runs on your FPP instance and is
accessed from devices on your home or show network. It is not designed to be
exposed to the public internet.

Key things to understand about the attack surface:

- The WebSocket server (`blinkymap_server.py`) binds to `127.0.0.1:8765` only
  and is proxied through Apache — it is not directly reachable from outside the
  host.
- The web UI is served over HTTPS with a self-signed certificate (camera
  requires a secure context). The certificate is generated locally at install
  time and never transmitted anywhere.
- There is no authentication. BlinkyMap assumes everyone on your local network
  is trusted. **Do not expose port 443 or 8765 to the internet.**
- No user data, credentials, or pixel layout data leave your network.

## Supported Versions

Only the latest release on the `main` branch receives security fixes.

| Version | Supported |
|---------|-----------|
| `main` (latest) | ✅ |
| Older commits | ❌ |

## Reporting a Vulnerability

If you find a security issue, please **do not open a public GitHub issue**.

Use GitHub's private vulnerability reporting instead:

1. Go to the [Security tab](https://github.com/wbhartmanii/BlinkyMap/security)
2. Click **Report a vulnerability**
3. Describe the issue, steps to reproduce, and potential impact

You can also reach the maintainer directly at **wbhartmanii@gmail.com**.

We'll acknowledge your report within a few days and aim to release a fix within
30 days for confirmed vulnerabilities. We'll credit you in the release notes
unless you'd prefer to stay anonymous.
