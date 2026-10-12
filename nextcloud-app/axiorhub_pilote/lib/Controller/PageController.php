<?php

declare(strict_types=1);

namespace OCA\AxiorhubPilote\Controller;

use OCA\AxiorhubPilote\AppInfo\Application;
use OCA\AxiorhubPilote\Service\EngineSettings;
use OCA\AxiorhubPilote\Service\UserLink;
use OCP\AppFramework\Controller;
use OCP\AppFramework\Http\Attribute\NoAdminRequired;
use OCP\AppFramework\Http\Attribute\NoCSRFRequired;
use OCP\AppFramework\Http\TemplateResponse;
use OCP\AppFramework\Services\IInitialState;
use OCP\IGroupManager;
use OCP\IRequest;
use OCP\IURLGenerator;
use OCP\IUserSession;

class PageController extends Controller {
	public function __construct(
		IRequest $request,
		private IInitialState $initialState,
		private EngineSettings $settings,
		private IUserSession $session,
		private IGroupManager $groups,
		private UserLink $links,
		private IURLGenerator $urls,
	) {
		parent::__construct(Application::APP_ID, $request);
	}

	/** Page d'entrée (lecture seule) : seul point sans jeton CSRF, comme toute page Nextcloud ; aucun secret dans l'état initial. */
	#[NoAdminRequired]
	#[NoCSRFRequired]
	public function index(): TemplateResponse {
		$user = $this->session->getUser();
		$uid = $user?->getUID() ?? '';
		$this->initialState->provideInitialState('config', [
			'engineConfigured' => $this->settings->isConfigured(),
			'linked' => $this->links->linked($uid),   // 0.3.0 : compte AxiorHub relié dans les paramètres personnels
			'personalUrl' => $this->urls->linkToRoute('settings.PersonalSettings.index', ['section' => Application::APP_ID]),
			'isAdmin' => $user !== null && $this->groups->isAdmin($uid),
		]);
		return new TemplateResponse(Application::APP_ID, 'main');
	}
}
