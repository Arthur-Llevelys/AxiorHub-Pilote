<?php

declare(strict_types=1);

namespace OCA\AxiorhubPilote\Controller;

use OCA\AxiorhubPilote\AppInfo\Application;
use OCA\AxiorhubPilote\Service\MatterService;
use OCA\AxiorhubPilote\Service\UserLink;
use OCP\AppFramework\Controller;
use OCP\AppFramework\Http\Attribute\NoAdminRequired;
use OCP\AppFramework\Http\DataResponse;
use OCP\IGroupManager;
use OCP\IRequest;
use OCP\IUserSession;

class MatterController extends Controller {
	public function __construct(
		IRequest $request,
		private MatterService $matters,
		private UserLink $links,
		private IUserSession $session,
		private IGroupManager $groups,
	) {
		parent::__construct(Application::APP_ID, $request);
	}

	/**
	 * Dossiers accessibles à l'utilisateur courant (déduit côté serveur ; aucun user_id accepté du navigateur).
	 * 0.3.0 : compte relié = dossiers du compte AxiorHub ; sinon groupes Nextcloud (import automatique à la première ouverture par un
	 * administrateur).
	 */
	#[NoAdminRequired]
	public function index(string $q = ''): DataResponse {
		$uid = $this->session->getUser()?->getUID() ?? '';
		$linked = $this->links->linked($uid);
		$sync = null;
		if (!$linked && $this->matters->isEmpty() && $this->groups->isAdmin($uid)) {
			$sync = $this->matters->syncFromEngine();
		}
		$list = $this->matters->listFor($uid, mb_substr($q, 0, 120));
		return new DataResponse($list + ['sync' => $sync, 'linked' => $linked, 'is_admin' => $this->groups->isAdmin($uid)]);
	}
}
