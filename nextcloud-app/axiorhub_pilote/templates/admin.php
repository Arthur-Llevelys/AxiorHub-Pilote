<?php
declare(strict_types=1);
/** @var array $_ */
/** @var \OCP\IL10N $l */
\OCP\Util::addScript('axiorhub_pilote', 'axiorhub_pilote-admin');
?>
<div id="axiorhub-pilote-admin" class="section">
	<h2><?php p($l->t('AxiorHub engine')); ?></h2>
	<p><?php p($l->t('The AxiorHub engine is installed separately (server of the firm). Nextcloud must be able to reach it; a workstation that may be switched off is not a reliable engine.')); ?></p>
	<form id="axh-admin-form">
		<p><label><?php p($l->t('Engine address (HTTPS)')); ?><br><input type="url" name="engineUrl" value="<?php p($_['engine_url']); ?>" required></label></p>
		<p><label><?php p($l->t('Instance identifier (same as integration5634.instance_id on the engine)')); ?><br><input type="text" name="instanceId" value="<?php p($_['instance_id']); ?>" required></label></p>
		<p><label><?php p($l->t('Shared secret')); ?><br><input type="password" name="secret" autocomplete="new-password" placeholder="<?php p($_['secret_set'] ? $l->t('Already set — leave empty to keep it') : ''); ?>"></label></p>
		<p><label><input type="checkbox" name="allowLocal" <?php if ($_['allow_local']) { p('checked'); } ?>> <?php p($l->t('Engine on this server (local address)')); ?></label></p>
		<p><button type="submit"><?php p($l->t('Save')); ?></button> <span id="axh-admin-status" role="status"></span></p>
	</form>
</div>
