<?php

declare(strict_types=1);

namespace OCA\AxiorhubPilote\Controller;

use OCA\AxiorhubPilote\AppInfo\Application;
use OCA\AxiorhubPilote\Db\Matter;
use OCA\AxiorhubPilote\Db\MatterMapper;
use OCA\AxiorhubPilote\Service\EngineClient;
use OCA\AxiorhubPilote\Service\EngineSettings;
use OCP\AppFramework\Controller;
use OCP\AppFramework\Http;
use OCP\AppFramework\Http\Attribute\NoAdminRequired;
use OCP\AppFramework\Http\DataResponse;
use OCP\IGroupManager;
use OCP\IRequest;
use OCP\IUserSession;

/** Réglages d'instance (administrateurs seulement : pas de NoAdminRequired) et test de liaison au moteur. */
class ConfigController extends Controller {
	public function __construct(
		IRequest $request,
		private EngineSettings $settings,
		private EngineClient $engine,
		private MatterMapper $matters,
		private IGroupManager $groups,
		private IUserSession $session,
	) {
		parent::__construct(Application::APP_ID, $request);
	}

	/** État de la liaison, lisible par tout utilisateur connecté ; aucune information secrète. */
	#[NoAdminRequired]
	public function health(): DataResponse {
		if (!$this->settings->isConfigured()) {
			return new DataResponse(['configured' => false]);
		}
		$out = $this->engine->call('GET', '/integration/v1/health', $this->session->getUser()?->getUID() ?? '', []);
		return new DataResponse(['configured' => true, 'engine' => $out['body'], 'reachable' => $out['status'] === 200]);
	}

	public function save(string $engineUrl, string $instanceId, ?string $secret = null, bool $allowLocal = false): DataResponse {
		try {
			$this->settings->save($engineUrl, $instanceId, $secret, $allowLocal);
		} catch (\InvalidArgumentException $e) {
			return new DataResponse(['code' => $e->getMessage()], Http::STATUS_BAD_REQUEST);
		}
		return new DataResponse(['saved' => true]);
	}

	public function saveMatter(string $matterRef, string $label, string $groupId, int $rootFileId = 0): DataResponse {
		if (!preg_match('/^[A-Za-z0-9_.-]{1,80}$/', $matterRef) || !$this->groups->groupExists($groupId)) {
			return new DataResponse(['code' => 'rattachement_invalide'], Http::STATUS_BAD_REQUEST);
		}
		$m = new Matter();
		$m->setMatterRef($matterRef);
		$m->setLabel(mb_substr($label, 0, 255));
		$m->setGroupId($groupId);
		$m->setRootFileId($rootFileId);
		$m->setCreatedAt(time());
		$this->matters->insert($m);
		return new DataResponse(['saved' => true]);
	}
}
