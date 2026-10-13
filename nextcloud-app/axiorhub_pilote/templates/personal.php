<?php
declare(strict_types=1);
/** @var array $_ */
/** @var \OCP\IL10N $l */
\OCP\Util::addScript('axiorhub_pilote', 'axiorhub_pilote-personal');
?>
<div id="axiorhub-pilote-personal" class="section">
	<h2><?php p($l->t('AxiorHub Pilote')); ?></h2>
	<p><?php p($l->t('Link your Nextcloud account to your own AxiorHub account to use AxiorHub Pilote here. Nextcloud users who have not linked an account have no access.')); ?></p>
	<fieldset id="axh-config" class="axh-config">
		<legend><strong><?php p($l->t('Where is AxiorHub installed?')); ?></strong></legend>
		<p><label><input type="radio" name="axhConfig" value="serveur" <?php if ($_['configuration'] === 'serveur') { p('checked'); } ?>>
			<strong><?php p($l->t('On a server (recommended)')); ?></strong></label><br>
			<span class="axh-hint"><?php p($l->t('AxiorHub server version (Linux, HTTPS) reachable by this Nextcloud: everything works inside Nextcloud, from every computer of the firm.')); ?></span></p>
		<p><label><input type="radio" name="axhConfig" value="meme-poste" <?php if ($_['configuration'] === 'meme-poste') { p('checked'); } ?>>
			<strong><?php p($l->t('On this computer, with Nextcloud on the same computer (discovery mode)')); ?></strong></label><br>
			<span class="axh-hint"><?php p($l->t('The AxiorHub interface works inside Nextcloud, from this computer only, with a permanent warning. For tests only.')); ?></span></p>
		<p><label><input type="radio" name="axhConfig" value="poste-heberge" <?php if ($_['configuration'] === 'poste-heberge') { p('checked'); } ?>>
			<strong><?php p($l->t('On my computer, with Nextcloud hosted on a server (discovery mode)')); ?></strong></label><br>
			<span class="axh-hint"><?php p($l->t('Only a link that opens AxiorHub in a new tab: in this setup the Pilot, the search and the display inside Nextcloud are not available.')); ?></span></p>
		<p class="axh-hint" id="axh-last-ok"><?php if ($_['last_ok'] !== '') { p($l->t('Last successful test: %s', [$_['last_ok']])); } ?></p>
	</fieldset>
	<div data-axh-config="serveur meme-poste">
	<div id="axh-personal-linked" <?php if (!$_['linked']) { p('hidden'); } ?>>
		<p><strong><?php p($l->t('Linked to the AxiorHub account:')); ?></strong> <span id="axh-personal-user"><?php p($_['axiorhub_user']); ?></span>
			— <span id="axh-personal-url"><?php p($_['engine_url']); ?></span></p>
		<p><button type="button" id="axh-personal-test"><?php p($l->t('Test the connection')); ?></button>
			<button type="button" id="axh-personal-unlink"><?php p($l->t('Unlink')); ?></button>
			<span id="axh-personal-status-linked" role="status"></span></p>
	</div>
	<form id="axh-personal-form" <?php if ($_['linked']) { p('hidden'); } ?>>
		<p class="axh-hint"><?php p($l->t('AxiorHub Pilote for Nextcloud is designed for the server version of AxiorHub (Linux server reachable over HTTPS by this Nextcloud server). An AxiorHub installed on a workstation (Windows 11 or Ubuntu desktop) only works in discovery mode, for tests: with a Nextcloud installed on the same computer, or as a link that opens it in a new tab (“My local AxiorHub” below).')); ?></p>
		<p><label><?php p($l->t('AxiorHub Pilote address')); ?><br>
			<input type="url" name="engineUrl" value="<?php p($_['engine_url']); ?>" placeholder="https://courriel.example.test/agent-courriel" required size="50"></label></p>
		<p><label><?php p($l->t('AxiorHub login')); ?><br><input type="text" name="login" autocomplete="username" required></label></p>
		<p><label><?php p($l->t('AxiorHub password')); ?><br><input type="password" name="password" autocomplete="current-password" required></label></p>
		<p><label><input type="checkbox" name="allowLocal" <?php if ($_['allow_local']) { p('checked'); } ?>> <?php p($l->t('Engine on this server (local address)')); ?></label></p>
		<p class="axh-hint"><?php p($l->t('Your password is used once to obtain a personal key and is never stored. The administrator of AxiorHub must first allow Nextcloud accounts (AxiorHub › Settings › Connections › Nextcloud app).')); ?></p>
		<p><button type="button" id="axh-personal-probe"><?php p($l->t('Test the address')); ?></button>
			<button type="submit" class="primary"><?php p($l->t('Link my account')); ?></button> <span id="axh-personal-status" role="status"></span></p>
	</form>
	</div>
	<div data-axh-config="poste-heberge">
	<h3><?php p($l->t('My local AxiorHub (discovery mode)')); ?></h3>
	<p class="axh-hint axh-warning"><?php p($l->t('Discovery mode: AxiorHub is installed on a workstation (Windows 11 or Ubuntu desktop). Use it only for tests and to discover the application: it can only be reached from this computer, stops when the computer is switched off, and local exchanges are not encrypted (HTTP). For your firm’s work, install the server version (Linux, HTTPS): more secure, backed up and reachable from every computer.')); ?></p>
	<form id="axh-personal-local">
		<p><label><?php p($l->t('Address of my local AxiorHub')); ?><br>
			<input type="url" name="localUrl" value="<?php p($_['local_url']); ?>" placeholder="http://127.0.0.1:8769/agent-courriel/" size="50"></label></p>
		<p class="axh-hint"><?php p($l->t('For a hosted Nextcloud: this address is only used by your browser, to open your local AxiorHub in a new tab from the “My local AxiorHub” menu entry; Nextcloud never connects to it (no Pilote, no search, no display inside Nextcloud). If Nextcloud runs on this same computer, link your account above instead, with the address http://127.0.0.1:8769/agent-courriel and “Engine on this server (local address)”.')); ?></p>
		<p><button type="submit"><?php p($l->t('Save')); ?></button>
			<button type="button" id="axh-personal-local-open"><?php p($l->t('Open my local AxiorHub')); ?></button>
			<span id="axh-personal-local-status" role="status"></span></p>
	</form>
	</div>
</div>
