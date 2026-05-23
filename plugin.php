<?php
/**
 * BlinkyMap FPP plugin main page.
 * Included by FPP's www/plugin.php within FPP's HTML layout so the
 * FPP header and navigation stay visible.
 *
 * The SPA must run over HTTPS (camera requires a secure context), so the
 * iframe always uses https:// even when the FPP UI is on HTTP.
 */

// $pluginName is set by FPP's plugin.php before including this file
$name = isset($pluginName) ? $pluginName : basename(__DIR__);
$host = $_SERVER['HTTP_HOST'];

// Strip any port — HTTPS always goes to 443 on this box
$ip = parse_url("http://{$host}", PHP_URL_HOST);

$spa_url = "https://{$ip}/plugin/{$name}/";
?>
<style>
#bm-wrap {
    width: 100%;
    height: calc(100vh - 180px);
    min-height: 420px;
}
#bm-frame {
    width: 100%;
    height: 100%;
    border: none;
    border-radius: 6px;
    background: #0d0d14;
}
</style>

<div id="bm-wrap">
  <iframe id="bm-frame"
          src="<?= htmlspecialchars($spa_url) ?>"
          allow="camera; microphone"
          allowfullscreen>
  </iframe>
</div>
