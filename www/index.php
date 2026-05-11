<?php
/**
 * BlinkyMap FPP Plugin entry point.
 * FPP loads this page when the user clicks BlinkyMap in the menu.
 *
 * Starts the Python WebSocket backend if it isn't already running,
 * then redirects the browser to the single-page app.
 */

// ── paths ──────────────────────────────────────────────────────────────────────
$plugin_dir    = dirname(__FILE__, 2);          // www/ → plugin root
$plugin_name   = basename($plugin_dir);         // 'blinkymap' or 'BlinkyMap' — whatever FPP cloned
$server_script = $plugin_dir . '/blinkymap_server.py';
$pid_file      = '/tmp/blinkymap_server.pid';
$log_file      = '/tmp/blinkymap_server.log';
$ws_port       = 8765;

// ── start server if not already reachable ─────────────────────────────────────
$sock = @fsockopen('127.0.0.1', $ws_port, $errno, $errstr, 0.3);
if ($sock) {
    fclose($sock);
} else {
    // Kill any stale process (avoid posix_kill — not always compiled in)
    if (file_exists($pid_file)) {
        $old_pid = (int) trim(file_get_contents($pid_file));
        if ($old_pid > 0) exec("kill -15 {$old_pid} 2>/dev/null");
        @unlink($pid_file);
    }

    // Launch server in background if shell_exec is available
    if (function_exists('shell_exec') && file_exists($server_script)) {
        $cmd = 'nohup python3 ' . escapeshellarg($server_script)
             . ' --port ' . intval($ws_port)
             . ' > ' . escapeshellarg($log_file) . ' 2>&1 & echo $!';
        $pid = (int) shell_exec($cmd);
        if ($pid > 0) file_put_contents($pid_file, $pid);
        usleep(800000);
    }
}

// ── redirect to the SPA (use actual plugin folder name for the URL) ───────────
header("Location: /plugin/{$plugin_name}/blinkymap/index.html");
exit;
