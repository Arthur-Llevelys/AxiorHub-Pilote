<?php

declare(strict_types=1);

namespace OCA\AxiorhubPilote\Service;

use OCP\IURLGenerator;

/**
 * 0.4.0 : l'interface AxiorHub affichée dans Nextcloud.
 *
 * Pour l'utilisateur relié, le moteur (5.6.37, contrat 1.3) remet une adresse de connexion à usage unique valable 60 secondes
 * (POST /integration/v1/session, requête signée avec SA clé personnelle). Le navigateur l'ouvre dans le cadre et reçoit une session
 * AxiorHub de 12 heures ; le moteur n'autorise l'affichage dans un cadre qu'au site Nextcloud déclaré ici. Aucun mot de passe ni
 * secret n'est transmis au navigateur ; l'adresse remise ne sert qu'une fois.
 */
class EmbedSession {
	public const DEFAULT_PAGE = '/aujourdhui';

	public function __construct(
		private EngineClient $engine,
		private UserLink $links,
		private IURLGenerator $urls,
	) {
	}

	/** Page d'arrivée : chemin interne d'AxiorHub uniquement. */
	public static function cleanPage(string $page): string {
		if ($page === '' || $page[0] !== '/' || str_starts_with($page, '//') || str_contains($page, '\\') || strlen($page) > 500
			|| preg_match('/[\x00-\x1f]/', $page) || str_starts_with($page, '/integration/') || str_starts_with($page, '/nextcloud-session')) {
			return self::DEFAULT_PAGE;
		}
		return $page;
	}

	/** Origine (schéma, hôte, port) d'une adresse ; '' si elle n'est pas HTTP(S). */
	public static function origin(string $url): string {
		$parts = parse_url($url);
		if ($parts === false || !isset($parts['scheme'], $parts['host']) || !in_array(strtolower($parts['scheme']), ['http', 'https'], true)) {
			return '';
		}
		$host = strtolower($parts['host']);
		return strtolower($parts['scheme']) . '://' . $host . (isset($parts['port']) ? ':' . $parts['port'] : '');
	}

	public function nextcloudOrigin(): string {
		return self::origin($this->urls->getAbsoluteURL('/'));
	}

	/**
	 * @return array{ok:bool, url?:string, base?:string, frame_origin?:string, standalone?:bool, code?:string, message?:string}
	 */
	public function open(string $userId, string $page = self::DEFAULT_PAGE): array {
		if (!$this->links->linked($userId)) {
			return ['ok' => false, 'code' => 'compte_non_relie'];
		}
		$out = $this->engine->call('POST', '/integration/v1/session', $userId, ['*'],
			['suite' => self::cleanPage($page), 'origin' => $this->nextcloudOrigin()]);
		$body = $out['body'];
		if ($out['status'] !== 200 || !isset($body['url'], $body['base'])) {
			$code = (string)($body['code'] ?? 'session_refusee');
			// moteur 5.6.36 ou plus ancien : la route n'existe pas encore
			if ($out['status'] === 404 && $code === 'route_inconnue') {
				$code = 'moteur_a_mettre_a_jour';
			}
			return ['ok' => false, 'code' => $code, 'message' => (string)($body['message'] ?? '')];
		}
		$url = (string)$body['url'];
		$base = rtrim((string)$body['base'], '/');
		$frame = self::origin($base);
		// l'adresse remise doit appartenir au moteur annoncé (jamais une autre destination dans le cadre)
		if ($frame === '' || self::origin($url) !== $frame || !str_starts_with($url, $base . '/')) {
			return ['ok' => false, 'code' => 'reponse_moteur_invalide'];
		}
		return ['ok' => true, 'url' => $url, 'base' => $base, 'frame_origin' => $frame, 'standalone' => (bool)($body['standalone'] ?? false),
			'poste' => ($body['mode'] ?? '') === 'poste'];   // 0.4.2 : moteur installé sur un poste (mode découverte)
	}

	/**
	 * Origines que la page Nextcloud peut afficher dans un cadre : celle du moteur annoncé et celle de l'adresse reliée.
	 * @return string[]
	 */
	public function frameOrigins(string $userId, array $session): array {
		$out = [];
		if (($session['frame_origin'] ?? '') !== '') {
			$out[] = (string)$session['frame_origin'];
		}
		$cred = $this->links->credentials($userId);
		if ($cred !== null && self::origin($cred['url']) !== '') {
			$out[] = self::origin($cred['url']);
		}
		return array_values(array_unique($out));
	}
}
