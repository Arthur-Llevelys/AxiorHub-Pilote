<?php

declare(strict_types=1);

namespace OCA\AxiorhubPilote\Service;

use OCA\AxiorhubPilote\AppInfo\Application;
use OCP\IConfig;
use OCP\Security\ICrypto;

/**
 * 0.3.0 : liaison personnelle d'un compte Nextcloud à un compte AxiorHub (paramètres personnels « AxiorHub Pilote »).
 *
 * Chaque utilisateur Nextcloud n'est pas avocat : l'accès n'est ouvert qu'à celui qui relie SON compte AxiorHub. Le mot de passe
 * AxiorHub est transmis une seule fois au moteur (HTTPS) et n'est jamais conservé ; le moteur remet une clé personnelle (identifiant +
 * secret) conservée chiffrée dans les préférences de l'utilisateur, révocable des deux côtés.
 */
class UserLink {
	public function __construct(
		private IConfig $config,
		private ICrypto $crypto,
	) {
	}

	public function linked(string $userId): bool {
		return $userId !== '' && $this->config->getUserValue($userId, Application::APP_ID, 'key_id', '') !== '';
	}

	/** @return array{url:string, key_id:string, secret:string, user:string, role:string, allow_local:bool, linked_at:string}|null */
	public function credentials(string $userId): ?array {
		if (!$this->linked($userId)) {
			return null;
		}
		$secret = $this->config->getUserValue($userId, Application::APP_ID, 'key_secret', '');
		try {
			$secret = $secret === '' ? '' : $this->crypto->decrypt($secret);
		} catch (\Exception) {
			return null;   // clé de chiffrement de l'instance changée : l'utilisateur relie de nouveau son compte
		}
		return [
			'url' => $this->config->getUserValue($userId, Application::APP_ID, 'engine_url', ''),
			'key_id' => $this->config->getUserValue($userId, Application::APP_ID, 'key_id', ''),
			'secret' => $secret,
			'user' => $this->config->getUserValue($userId, Application::APP_ID, 'axiorhub_user', ''),
			'role' => $this->config->getUserValue($userId, Application::APP_ID, 'axiorhub_role', ''),
			'allow_local' => $this->config->getUserValue($userId, Application::APP_ID, 'allow_local', '0') === '1',
			'linked_at' => $this->config->getUserValue($userId, Application::APP_ID, 'linked_at', ''),
		];
	}

	public function store(string $userId, string $url, bool $allowLocal, array $key): void {
		$this->config->setUserValue($userId, Application::APP_ID, 'engine_url', rtrim($url, '/'));
		$this->config->setUserValue($userId, Application::APP_ID, 'allow_local', $allowLocal ? '1' : '0');
		$this->config->setUserValue($userId, Application::APP_ID, 'key_id', (string)$key['key_id']);
		$this->config->setUserValue($userId, Application::APP_ID, 'key_secret', $this->crypto->encrypt((string)$key['secret']));
		$this->config->setUserValue($userId, Application::APP_ID, 'axiorhub_user', mb_substr((string)($key['user'] ?? ''), 0, 200));
		$this->config->setUserValue($userId, Application::APP_ID, 'axiorhub_role', mb_substr((string)($key['role'] ?? ''), 0, 40));
		$this->config->setUserValue($userId, Application::APP_ID, 'linked_at', gmdate('Y-m-d\TH:i:s\Z'));
	}

	public function forget(string $userId): void {
		foreach (['engine_url', 'allow_local', 'key_id', 'key_secret', 'axiorhub_user', 'axiorhub_role', 'linked_at'] as $key) {
			$this->config->deleteUserValue($userId, Application::APP_ID, $key);
		}
	}

	/** Adresse HTTPS d'un moteur, contrôlée (pas d'identifiants dans l'URL, pas de requête ; HTTP seulement pour une adresse locale autorisée). */
	public static function checkUrl(string $url, bool $allowLocal): string {
		$parts = parse_url(trim($url));
		if ($parts === false || !isset($parts['scheme'], $parts['host']) || isset($parts['user']) || isset($parts['pass']) || isset($parts['query']) || isset($parts['fragment'])) {
			throw new \InvalidArgumentException('engine_url_invalid');
		}
		if ($parts['scheme'] !== 'https' && !($allowLocal && $parts['scheme'] === 'http' && in_array($parts['host'], ['127.0.0.1', 'localhost', '::1'], true))) {
			throw new \InvalidArgumentException('https_required');
		}
		return rtrim(trim($url), '/');
	}
}
