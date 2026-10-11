<?php
declare(strict_types=1);
// Gabarit d'entrée : l'interface Vue compilée est servie par l'application (aucun script en ligne, CSP Nextcloud respectée).
\OCP\Util::addScript('axiorhub_pilote', 'axiorhub_pilote-main');
\OCP\Util::addStyle('axiorhub_pilote', 'axiorhub_pilote-main');
?>
<div id="axiorhub-pilote"></div>
