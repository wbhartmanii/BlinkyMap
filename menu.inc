<?php
// $menu is set by FPP's list_plugin_entries() to the current dropdown name.
// Output an <a> tag only for the menu section where BlinkyMap belongs.
if (isset($menu) && $menu == 'content') {
    echo '<a href="/plugin/blinkymap/index.php">BlinkyMap</a>';
}
