<?php
// FPP includes this file with $plugin (the plugin folder name) in scope.
// It captures the HTML output and injects it into the navigation dropdown.
$_p = isset($plugin) ? $plugin : basename(__DIR__);
echo '<li><a href="/plugin/' . htmlspecialchars($_p) . '/">BlinkyMap</a></li>' . "\n";
