<?php

declare(strict_types=1);

namespace OCA\AxiorhubPilote\Controller;

use OCA\AxiorhubPilote\AppInfo\Application;
use OCA\AxiorhubPilote\Service\EngineClient;
use OCA\AxiorhubPilote\Service\MatterService;
use OCP\AppFramework\Controller;
use OCP\AppFramework\Http;
use OCP\AppFramework\Http\Attribute\NoAdminRequired;
use OCP\AppFramework\Http\DataResponse;
use OCP\IRequest;
use OCP\IUserSession;

/** 0.3.0 : « Rechercher dans les dossiers » — index du cabinet interrogé par le moteur, périmètre revérifié par le moteur. */
class SearchController extends Controller {
	public function __construct(
		IRequest $request,
		private EngineClient $engine,
		private MatterService $matters,
		private IUserSession $session,
	) {
		parent::__construct(Application::APP_ID, $request);
	}

	#[NoAdminRequired]
	public function index(string $q = '', string $matter = ''): DataResponse {
		$uid = $this->session->getUser()?->getUID() ?? '';
		$q = trim($q);
		if (mb_strlen($q) < 2 || mb_strlen($q) > 300) {
			return new DataResponse(['code' => 'recherche_invalide'], Http::STATUS_BAD_REQUEST);
		}
		if ($matter !== '' && !$this->matters->allowed($uid, $matter)) {
			return new DataResponse(['code' => 'dossier_hors_perimetre'], Http::STATUS_FORBIDDEN);
		}
		$query = ['q' => $q];
		if ($matter !== '') {
			$query['matter'] = $matter;
		}
		$out = $this->engine->call('GET', '/integration/v1/search', $uid, $this->matters->scope($uid), null, $query);
		return new DataResponse($out['body'], $out['status']);
	}
}
