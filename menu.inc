<?php
// FPP includes this file with $plugin (the plugin folder name) in scope.
// It captures the HTML output and injects it into the navigation dropdown.
$_p = isset($plugin) ? $plugin : basename(__DIR__);
// Link through FPP's own plugin router (plugin.php?plugin=name) so FPP
// wraps the page in its header/nav. FPP rewrites this to add ?_menu=... at render time.
echo '<li><a href="plugin.php?plugin=' . htmlspecialchars($_p) . '">BlinkyMap</a></li>' . "\n";
