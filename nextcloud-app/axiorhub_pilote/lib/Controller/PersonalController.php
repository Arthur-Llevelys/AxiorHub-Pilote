<?php

declare(strict_types=1);

namespace OCA\AxiorhubPilote\Controller;

use OCA\AxiorhubPilote\AppInfo\Application;
use OCA\AxiorhubPilote\Service\EngineClient;
use OCA\AxiorhubPilote\Service\EngineSettings;
use OCA\AxiorhubPilote\Service\UserLink;
use OCP\AppFramework\Controller;
use OCP\AppFramework\Http;
use OCP\AppFramework\Http\Attribute\NoAdminRequired;
use OCP\AppFramework\Http\DataResponse;
use OCP\IRequest;
use OCP\IUserSession;

/**
 * 0.3.0 : paramètres personnels « AxiorHub Pilote » — relier, tester, délier SON compte AxiorHub.
 * Le mot de passe AxiorHub n'est utilisé que pour obtenir la clé personnelle ; il n'est ni conservé, ni journalisé, ni renvoyé.
 */
class PersonalController extends Controller {
	public function __construct(
		IRequest $request,
		private UserLink $links,
		private EngineClient $engine,
		private EngineSettings $settings,
		private IUserSession $session,
	) {
		parent::__construct(Application::APP_ID, $request);
	}

	private function uid(): string {
		return $this->session->getUser()?->getUID() ?? '';
	}

	#[NoAdminRequired]
	public function status(): DataResponse {
		$cred = $this->links->credentials($this->uid());
		return new DataResponse([
			'linked' => $cred !== null,
			'engine_url' => $cred['url'] ?? $this->settings->engineUrl(),
			'axiorhub_user' => $cred['user'] ?? '',
			'linked_at' => $cred['linked_at'] ?? '',
			'allow_local' => $cred['allow_local'] ?? false,
		]);
	}

	#[NoAdminRequired]
	public function link(string $engineUrl, string $login, string $password, bool $allowLocal = false): DataResponse {
		$uid = $this->uid();
		try {
			$url = UserLink::checkUrl($engineUrl, $allowLocal);
		} catch (\InvalidArgumentException $e) {
			return new DataResponse(['code' => $e->getMessage()], Http::STATUS_BAD_REQUEST);
		}
		if (trim($login) === '' || $password === '' || strlen($password) > 1024) {
			return new DataResponse(['code' => 'identifiants_requis'], Http::STATUS_BAD_REQUEST);
		}
		$health = $this->engine->health($url, $allowLocal);
		if ($health['status'] !== 200) {
			return new DataResponse(['code' => (string)($health['body']['code'] ?? 'moteur_injoignable'), 'message' => (string)($health['body']['message'] ?? ''),
				'step' => 'health'], Http::STATUS_BAD_GATEWAY);
		}
		$out = $this->engine->pair($url, $allowLocal, trim($login), $password, mb_substr($uid, 0, 100));
		if ($out['status'] !== 200 || !isset($out['body']['key_id'], $out['body']['secret'])) {
			return new DataResponse(['code' => (string)($out['body']['code'] ?? 'appairage_refuse'), 'message' => (string)($out['body']['message'] ?? ''),
				'step' => 'pair'], $out['status'] >= 400 ? $out['status'] : Http::STATUS_BAD_GATEWAY);
		}
		$this->links->store($uid, $url, $allowLocal, $out['body']);
		return new DataResponse(['linked' => true, 'axiorhub_user' => (string)($out['body']['user'] ?? ''), 'role' => (string)($out['body']['role'] ?? ''),
			'version' => (string)($health['body']['version'] ?? '')]);
	}

	#[NoAdminRequired]
	public function test(): DataResponse {
		$uid = $this->uid();
		if (!$this->links->linked($uid)) {
			return new DataResponse(['code' => 'compte_non_relie'], Http::STATUS_CONFLICT);
		}
		$out = $this->engine->call('GET', '/integration/v1/me', $uid, ['*']);
		return new DataResponse($out['body'], $out['status'] === 200 ? Http::STATUS_OK : $out['status']);
	}

	#[NoAdminRequired]
	public function unlink(): DataResponse {
		$uid = $this->uid();
		if ($this->links->linked($uid)) {
			$this->engine->call('POST', '/integration/v1/unpair', $uid, ['*'], []);   // révocation côté moteur (au mieux)
		}
		$this->links->forget($uid);
		return new DataResponse(['linked' => false]);
	}
}
