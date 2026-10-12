<?php
declare(strict_types=1);
/** @var array $_ */
/** @var \OCP\IL10N $l */
\OCP\Util::addScript('axiorhub_pilote', 'axiorhub_pilote-personal');
?>
<div id="axiorhub-pilote-personal" class="section">
	<h2><?php p($l->t('AxiorHub Pilote')); ?></h2>
	<p><?php p($l->t('Link your Nextcloud account to your own AxiorHub account to use AxiorHub Pilote here. Nextcloud users who have not linked an account have no access.')); ?></p>
	<div id="axh-personal-linked" <?php if (!$_['linked']) { p('hidden'); } ?>>
		<p><strong><?php p($l->t('Linked to the AxiorHub account:')); ?></strong> <span id="axh-personal-user"><?php p($_['axiorhub_user']); ?></span>
			— <span id="axh-personal-url"><?php p($_['engine_url']); ?></span></p>
		<p><button type="button" id="axh-personal-test"><?php p($l->t('Test the connection')); ?></button>
			<button type="button" id="axh-personal-unlink"><?php p($l->t('Unlink')); ?></button>
			<span id="axh-personal-status-linked" role="status"></span></p>
	</div>
	<form id="axh-personal-form" <?php if ($_['linked']) { p('hidden'); } ?>>
		<p><label><?php p($l->t('AxiorHub Pilote address')); ?><br>
			<input type="url" name="engineUrl" value="<?php p($_['engine_url']); ?>" placeholder="https://courriel.example.test/agent-courriel" required size="50"></label></p>
		<p><label><?php p($l->t('AxiorHub login')); ?><br><input type="text" name="login" autocomplete="username" required></label></p>
		<p><label><?php p($l->t('AxiorHub password')); ?><br><input type="password" name="password" autocomplete="current-password" required></label></p>
		<p><label><input type="checkbox" name="allowLocal" <?php if ($_['allow_local']) { p('checked'); } ?>> <?php p($l->t('Engine on this server (local address)')); ?></label></p>
		<p class="axh-hint"><?php p($l->t('Your password is used once to obtain a personal key and is never stored. The administrator of AxiorHub must first allow Nextcloud accounts (AxiorHub › Settings › Connections › Nextcloud app).')); ?></p>
		<p><button type="submit" class="primary"><?php p($l->t('Link my account')); ?></button> <span id="axh-personal-status" role="status"></span></p>
	</form>
</div>
