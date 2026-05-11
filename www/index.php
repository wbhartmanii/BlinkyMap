<?php
/**
 * BlinkyMap FPP Plugin entry point.
 * FPP loads this page when the user clicks BlinkyMap in the menu.
 *
 * We ensure the Python WebSocket backend is running, then redirect
 * the browser to the single-page app.
 */

$plugin_dir = dirname(__FILE__, 2);          // www/ → plugin root
$server_script = $plugin_dir . '/blinkymap_server.py';
$pid_file = '/tmp/blinkymap_server.pid';
$ws_port = 8765;

// ── start server if not already running ───────────────────────────────────────
function server_running(int $port): bool {
    $sock = @fsockopen('127.0.0.1', $port, $errno, $errstr, 0.3);
    if ($sock) { fclose($sock); return true; }
    return false;
}

if (!server_running($ws_port)) {
    // Kill any stale pid
    if (file_exists($pid_file)) {
        $old_pid = (int) file_get_contents($pid_file);
        if ($old_pid > 0) posix_kill($old_pid, SIGTERM);
        @unlink($pid_file);
    }

    // Launch the server in the background (nohup so it survives the PHP process)
    $cmd = sprintf(
        'nohup python3 %s --port %d > /tmp/blinkymap_server.log 2>&1 & echo $!',
        escapeshellarg($server_script),
        $ws_port
    );
    $pid = (int) shell_exec($cmd);
    if ($pid > 0) file_put_contents($pid_file, $pid);

    // Give it a moment to bind
    usleep(800000);
}

// ── redirect to the SPA ───────────────────────────────────────────────────────
header('Location: /plugin/blinkymap/blinkymap/');
exit;
