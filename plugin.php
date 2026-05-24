<?php
/**
 * BlinkyMap FPP plugin main page.
 * Included by FPP's www/plugin.php within FPP's HTML layout so the
 * FPP header and navigation stay visible.
 *
 * Camera (getUserMedia) requires a secure context all the way up the
 * frame chain — an HTTPS iframe inside an HTTP parent is NOT enough.
 * So if the current FPP session is HTTP, we redirect the whole page to
 * HTTPS first. The user sees the self-signed cert warning once; after
 * that BlinkyMap (and FPP) both work over HTTPS permanently.
 */

$name = isset($pluginName) ? $pluginName : basename(__DIR__);
$ip   = parse_url("http://{$_SERVER['HTTP_HOST']}", PHP_URL_HOST);

$is_https = (!empty($_SERVER['HTTPS']) && $_SERVER['HTTPS'] !== 'off')
         || (!empty($_SERVER['HTTP_X_FORWARDED_PROTO'])
             && $_SERVER['HTTP_X_FORWARDED_PROTO'] === 'https');

$spa_url = "https://{$ip}/plugin/{$name}/";
?>
<?php if (!$is_https): ?>
<script>
// Redirect the whole FPP session to HTTPS so the iframe is in a fully
// secure context (required for getUserMedia / camera access).
location.replace('https://' + location.hostname
                 + location.pathname + location.search + location.hash);
</script>
<noscript>
  <p style="padding:1rem">
    BlinkyMap requires HTTPS for camera access.
    <a href="https://<?= htmlspecialchars($ip) ?>/plugin.php?plugin=<?= htmlspecialchars($name) ?>">
      Switch to HTTPS →
    </a>
  </p>
</noscript>
<?php else: ?>
<style>
#bm-wrap  { width:100%; height:calc(100vh - 180px); min-height:420px; }
#bm-frame { width:100%; height:100%; border:none; border-radius:6px; background:#0d0d14; }
</style>
<div id="bm-wrap">
  <iframe id="bm-frame"
          src="<?= htmlspecialchars($spa_url) ?>"
          allow="camera; microphone"
          allowfullscreen>
  </iframe>
</div>
<?php endif; ?>
