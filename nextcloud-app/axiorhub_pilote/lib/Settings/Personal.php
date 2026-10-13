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
		$uid = $this->session->getUser()?->getUID() ?? '';
		$cred = $this->links->credentials($uid);
		$local = $this->links->localUrl($uid);
		return new TemplateResponse(Application::APP_ID, 'personal', [
			'configuration' => self::configuration($cred, $local),
			'last_ok' => $this->links->lastOk($uid),
			'linked' => $cred !== null,
			'engine_url' => $cred['url'] ?? $this->settings->engineUrl(),
			'axiorhub_user' => $cred['user'] ?? '',
			'linked_at' => $cred['linked_at'] ?? '',
			'allow_local' => $cred['allow_local'] ?? false,
			'local_url' => $this->links->localUrl($this->session->getUser()?->getUID() ?? ''),
		]);
	}

	/** 0.4.3 : configuration proposée à l'ouverture — déduite de l'état actuel, modifiable par l'utilisateur. */
	public static function configuration(?array $cred, string $local): string {
		if ($cred !== null) {
			$host = strtolower((string)(parse_url((string)($cred['url'] ?? ''), PHP_URL_HOST) ?? ''));
			return in_array($host, ['127.0.0.1', 'localhost', '::1', '[::1]', 'host.docker.internal'], true) && !empty($cred['allow_local'])
				? 'meme-poste' : 'serveur';
		}
		return $local !== '' ? 'poste-heberge' : 'serveur';
	}

	public function getSection(): string {
		return Application::APP_ID;
	}

	public function getPriority(): int {
		return 10;
	}
}
