<?php

declare(strict_types=1);

namespace OCA\AxiorhubPilote\Controller;

use OCA\AxiorhubPilote\AppInfo\Application;
use OCA\AxiorhubPilote\Service\MatterService;
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
		private IUserSession $session,
		private IGroupManager $groups,
	) {
		parent::__construct(Application::APP_ID, $request);
	}

	/** Dossiers accessibles à l'utilisateur courant (déduit côté serveur ; aucun user_id accepté du navigateur).
	 * 0.2.0 : si aucun dossier n'est encore importé, la première ouverture par un administrateur importe ceux du moteur. */
	#[NoAdminRequired]
	public function index(string $q = ''): DataResponse {
		$uid = $this->session->getUser()?->getUID() ?? '';
		$sync = null;
		if ($this->matters->isEmpty() && $this->groups->isAdmin($uid)) {
			$sync = $this->matters->syncFromEngine();
		}
		$needle = mb_strtolower(trim($q));
		$rows = array_values(array_filter($this->matters->visible($uid),
			fn ($m) => $needle === '' || str_contains(mb_strtolower($m->getLabel() . ' ' . $m->getMatterRef()), $needle)));
		return new DataResponse(['matters' => array_slice($rows, 0, 50), 'total' => count($rows), 'sync' => $sync,
			'is_admin' => $this->groups->isAdmin($uid)]);
	}
}
