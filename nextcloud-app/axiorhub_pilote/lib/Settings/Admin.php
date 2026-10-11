<?php

declare(strict_types=1);

namespace OCA\AxiorhubPilote\Settings;

use OCA\AxiorhubPilote\AppInfo\Application;
use OCA\AxiorhubPilote\Service\EngineSettings;
use OCP\AppFramework\Http\TemplateResponse;
use OCP\Settings\ISettings;

/** Réglages administrateur : adresse du moteur, identifiant d'instance ; le secret n'est jamais réaffiché. */
class Admin implements ISettings {
	public function __construct(
		private EngineSettings $settings,
	) {
	}

	public function getForm(): TemplateResponse {
		return new TemplateResponse(Application::APP_ID, 'admin', [
			'engine_url' => $this->settings->engineUrl(),
			'instance_id' => $this->settings->instanceId(),
			'secret_set' => $this->settings->secret() !== '',
			'allow_local' => $this->settings->allowLocal(),
			'default_group' => $this->settings->defaultGroup(),
		]);
	}

	public function getSection(): string {
		return 'additional';
	}

	public function getPriority(): int {
		return 50;
	}
}
