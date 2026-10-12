<?php

declare(strict_types=1);

namespace OCA\AxiorhubPilote\Settings;

use OCA\AxiorhubPilote\AppInfo\Application;
use OCA\AxiorhubPilote\Service\EngineSettings;
use OCA\AxiorhubPilote\Service\UserLink;
use OCP\AppFramework\Http\TemplateResponse;
use OCP\IUserSession;
use OCP\Settings\ISettings;

/** 0.3.0 : paramètres personnels « AxiorHub Pilote » — chaque avocat relie son compte AxiorHub ; aucun secret n'est réaffiché. */
class Personal implements ISettings {
	public function __construct(
		private UserLink $links,
		private EngineSettings $settings,
		private IUserSession $session,
	) {
	}

	public function getForm(): TemplateResponse {
		$cred = $this->links->credentials($this->session->getUser()?->getUID() ?? '');
		return new TemplateResponse(Application::APP_ID, 'personal', [
			'linked' => $cred !== null,
			'engine_url' => $cred['url'] ?? $this->settings->engineUrl(),
			'axiorhub_user' => $cred['user'] ?? '',
			'linked_at' => $cred['linked_at'] ?? '',
			'allow_local' => $cred['allow_local'] ?? false,
		]);
	}

	public function getSection(): string {
		return Application::APP_ID;
	}

	public function getPriority(): int {
		return 10;
	}
}
