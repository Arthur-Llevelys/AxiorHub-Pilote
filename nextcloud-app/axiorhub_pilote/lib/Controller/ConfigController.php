<?php

declare(strict_types=1);

namespace OCA\AxiorhubPilote\Controller;

use OCA\AxiorhubPilote\AppInfo\Application;
use OCA\AxiorhubPilote\Service\EngineClient;
use OCA\AxiorhubPilote\Service\EngineSettings;
use OCA\AxiorhubPilote\Service\MatterService;
use OCP\AppFramework\Controller;
use OCP\AppFramework\Http;
use OCP\AppFramework\Http\Attribute\NoAdminRequired;
use OCP\AppFramework\Http\DataResponse;
use OCP\IGroupManager;
use OCP\IRequest;
use OCP\IUserSession;

/**
 * Réglages d'instance (administrateurs seulement : pas de NoAdminRequired) et test de liaison au moteur.
 * 0.2.0 : l'enregistrement teste aussitôt la liaison et importe les dossiers du moteur ; rattachement des dossiers aux groupes depuis la
 * page d'administration (plus d'appel d'API manuel).
 */
class ConfigController extends Controller {
	public function __construct(
		IRequest $request,
		private EngineSettings $settings,
		private EngineClient $engine,
		private MatterService $matters,
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

	public function save(string $engineUrl, string $instanceId, ?string $secret = null, bool $allowLocal = false, string $defaultGroup = ''): DataResponse {
		try {
			$this->settings->save($engineUrl, $instanceId, $secret, $allowLocal);
		} catch (\InvalidArgumentException $e) {
			return new DataResponse(['code' => $e->getMessage()], Http::STATUS_BAD_REQUEST);
		}
		if ($defaultGroup !== '') {
			if (!$this->groups->groupExists($defaultGroup)) {
				return new DataResponse(['code' => 'groupe_inconnu'], Http::STATUS_BAD_REQUEST);
			}
			$this->settings->setDefaultGroup($defaultGroup);
		}
		$health = $this->engine->call('GET', '/integration/v1/health', $this->session->getUser()?->getUID() ?? '', []);
		$sync = $health['status'] === 200 ? $this->matters->syncFromEngine() : ['ok' => false, 'code' => (string)($health['body']['code'] ?? 'moteur_injoignable')];
		return new DataResponse(['saved' => true, 'reachable' => $health['status'] === 200, 'sync' => $sync]);
	}

	/** Import (ou actualisation des intitulés) des dossiers du moteur. */
	public function sync(): DataResponse {
		$out = $this->matters->syncFromEngine();
		return new DataResponse($out, $out['ok'] ? Http::STATUS_OK : Http::STATUS_BAD_GATEWAY);
	}

	/** Liste des dossiers rattachés et des groupes disponibles (page d'administration). */
	public function matters(): DataResponse {
		$groups = array_map(fn ($g) => $g->getGID(), $this->groups->search(''));
		return new DataResponse(['matters' => $this->matters->adminList(), 'groups' => $groups, 'default_group' => $this->settings->defaultGroup()]);
	}

	public function saveMatter(string $matterRef, string $groupId, string $label = '', int $rootFileId = 0): DataResponse {
		if (!preg_match('/^[A-Za-z0-9_.-]{1,80}$/', $matterRef) || !$this->groups->groupExists($groupId)) {
			return new DataResponse(['code' => 'rattachement_invalide'], Http::STATUS_BAD_REQUEST);
		}
		$this->matters->link($matterRef, $label, $groupId, $rootFileId);
		return new DataResponse(['saved' => true]);
	}
}
