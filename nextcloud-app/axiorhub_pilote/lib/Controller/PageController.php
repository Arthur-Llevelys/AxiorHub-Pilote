<?php

declare(strict_types=1);

namespace OCA\AxiorhubPilote\Controller;

use OCA\AxiorhubPilote\AppInfo\Application;
use OCA\AxiorhubPilote\Service\EmbedSession;
use OCA\AxiorhubPilote\Service\EngineSettings;
use OCA\AxiorhubPilote\Service\UserLink;
use OCP\AppFramework\Controller;
use OCP\AppFramework\Http\Attribute\NoAdminRequired;
use OCP\AppFramework\Http\Attribute\NoCSRFRequired;
use OCP\AppFramework\Http\ContentSecurityPolicy;
use OCP\AppFramework\Http\FeaturePolicy;
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
		private EmbedSession $embed,
	) {
		parent::__construct(Application::APP_ID, $request);
	}

	/**
	 * Page d'entrée (lecture seule) : seul point sans jeton CSRF, comme toute page Nextcloud ; aucun secret dans l'état initial.
	 * 0.4.0 : pour un compte relié, adresse de connexion à usage unique de l'interface AxiorHub (cadre) ; la politique de sécurité de
	 * la page n'autorise que l'origine du moteur dans un cadre.
	 */
	#[NoAdminRequired]
	#[NoCSRFRequired]
	public function index(string $page = ''): TemplateResponse {
		$user = $this->session->getUser();
		$uid = $user?->getUID() ?? '';
		$linked = $this->links->linked($uid);
		$embed = $linked ? $this->embed->open($uid, $page !== '' ? $page : EmbedSession::DEFAULT_PAGE) : ['ok' => false, 'code' => 'compte_non_relie'];
		$this->initialState->provideInitialState('config', [
			'engineConfigured' => $this->settings->isConfigured(),
			'linked' => $linked,   // 0.3.0 : compte AxiorHub relié dans les paramètres personnels
			'personalUrl' => $this->urls->linkToRoute('settings.PersonalSettings.index', ['section' => Application::APP_ID]),
			'isAdmin' => $user !== null && $this->groups->isAdmin($uid),
			'embed' => $embed,
			'localUrl' => $this->links->localUrl($uid),   // 0.4.2 : « Mon AxiorHub local » (mode découverte, lien ouvert dans un onglet)
		]);
		$response = new TemplateResponse(Application::APP_ID, 'main');
		$origins = $linked ? $this->embed->frameOrigins($uid, $embed) : [];
		if ($origins !== []) {
			$csp = new ContentSecurityPolicy();
			$policy = new FeaturePolicy();
			foreach ($origins as $origin) {
				$csp->addAllowedFrameDomain($origin);
				$policy->addAllowedMicrophoneDomain($origin);   // dictée d'AxiorHub dans le cadre
			}
			$response->setContentSecurityPolicy($csp);
			$response->setFeaturePolicy($policy);
		}
		return $response;
	}
}
