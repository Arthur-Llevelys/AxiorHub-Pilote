<?php

declare(strict_types=1);

namespace OCA\AxiorhubPilote\Controller;

use OCA\AxiorhubPilote\AppInfo\Application;
use OCA\AxiorhubPilote\Service\EmbedSession;
use OCP\AppFramework\Controller;
use OCP\AppFramework\Http;
use OCP\AppFramework\Http\Attribute\NoAdminRequired;
use OCP\AppFramework\Http\DataResponse;
use OCP\IRequest;
use OCP\IUserSession;

/**
 * 0.4.0 : nouvelle adresse de connexion à usage unique (session AxiorHub expirée dans le cadre, « Ouvrir dans un onglet »).
 * Protégée par le jeton CSRF de Nextcloud ; réservée au compte relié de l'utilisateur connecté.
 */
class SessionController extends Controller {
	public function __construct(
		IRequest $request,
		private EmbedSession $embed,
		private IUserSession $session,
	) {
		parent::__construct(Application::APP_ID, $request);
	}

	#[NoAdminRequired]
	public function create(string $page = ''): DataResponse {
		$uid = $this->session->getUser()?->getUID() ?? '';
		$out = $this->embed->open($uid, $page !== '' ? $page : EmbedSession::DEFAULT_PAGE);
		if ($out['ok']) {
			return new DataResponse($out);
		}
		$status = match ($out['code'] ?? '') {
			'compte_non_relie' => Http::STATUS_CONFLICT,
			'cle_personnelle_revoquee_ou_inconnue', 'role_insuffisant' => Http::STATUS_FORBIDDEN,
			default => Http::STATUS_BAD_GATEWAY,
		};
		return new DataResponse($out, $status);
	}
}
