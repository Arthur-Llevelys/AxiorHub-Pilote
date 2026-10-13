<?php
declare(strict_types=1);
/** @var array $_ */
/** @var \OCP\IL10N $l */
\OCP\Util::addScript('axiorhub_pilote', 'axiorhub_pilote-admin');
?>
<div id="axiorhub-pilote-admin" class="section">
	<h2><?php p($l->t('AxiorHub engine')); ?></h2>
	<p><?php p($l->t('The AxiorHub engine is installed separately (server of the firm). Nextcloud must be able to reach it; a workstation that may be switched off is not a reliable engine.')); ?></p>
	<p class="axh-hint"><?php p($l->t('AxiorHub Pilote for Nextcloud is designed for the server version of AxiorHub (Linux server reachable over HTTPS by this Nextcloud server). An AxiorHub installed on a workstation (Windows 11 or Ubuntu desktop) only works in discovery mode, for tests: with a Nextcloud installed on the same computer, or as a link that opens it in a new tab (“My local AxiorHub” below).')); ?></p>
	<form id="axh-admin-form">
		<p><label><?php p($l->t('Engine address (HTTPS)')); ?><br><input type="url" name="engineUrl" value="<?php p($_['engine_url']); ?>" required></label></p>
		<p><label><?php p($l->t('Instance identifier (same as integration5634.instance_id on the engine)')); ?><br><input type="text" name="instanceId" value="<?php p($_['instance_id']); ?>" required></label></p>
		<p><label><?php p($l->t('Shared secret')); ?><br><input type="password" name="secret" autocomplete="new-password" placeholder="<?php p($_['secret_set'] ? $l->t('Already set — leave empty to keep it') : ''); ?>"></label></p>
		<p><label><input type="checkbox" name="allowLocal" <?php if ($_['allow_local']) { p('checked'); } ?>> <?php p($l->t('Engine on this server (local address)')); ?></label></p>
		<p><label><?php p($l->t('Group allowed by default for the cases imported from the engine')); ?><br><input type="text" name="defaultGroup" value="<?php p($_['default_group']); ?>"></label></p>
		<p class="axh-hint"><?php p($l->t('The shared secret must be the same as on the engine (at least 8 characters; 32 random characters recommended). Saving tests the connection and imports the cases of the engine.')); ?></p>
		<p><button type="submit"><?php p($l->t('Save')); ?></button> <span id="axh-admin-status" role="status"></span></p>
	</form>
	<h3><?php p($l->t('Cases and allowed groups')); ?></h3>
	<p><?php p($l->t('Each case of the engine is visible to the members of its group (administrators see all cases). A group chosen here is kept when the list is imported again.')); ?></p>
	<p><button type="button" id="axh-admin-sync"><?php p($l->t('Import the cases of the engine')); ?></button> <span id="axh-admin-sync-status" role="status"></span></p>
	<table id="axh-admin-matters" class="grid"><thead><tr><th><?php p($l->t('Case')); ?></th><th><?php p($l->t('Reference')); ?></th><th><?php p($l->t('Group')); ?></th></tr></thead><tbody></tbody></table>
</div>
