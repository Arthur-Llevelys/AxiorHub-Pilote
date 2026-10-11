<?php

declare(strict_types=1);

namespace OCA\AxiorhubPilote\Service;

use OCA\AxiorhubPilote\AppInfo\Application;
use OCP\IAppConfig;
use OCP\Security\ICrypto;

/**
 * Réglages d'instance : adresse du moteur, identifiant d'instance, secret partagé (chiffré et marqué sensible).
 * Le secret n'est jamais renvoyé au navigateur, ni placé dans l'état initial, une URL ou un journal.
 */
class EngineSettings {
	public function __construct(
		private IAppConfig $appConfig,
		private ICrypto $crypto,
	) {
	}

	public function engineUrl(): string {
		return rtrim($this->appConfig->getValueString(Application::APP_ID, 'engine_url', ''), '/');
	}

	public function instanceId(): string {
		return $this->appConfig->getValueString(Application::APP_ID, 'instance_id', '');
	}

	public function allowLocal(): bool {
		return $this->appConfig->getValueBool(Application::APP_ID, 'allow_local_engine', false);
	}

	public function secret(): string {
		$stored = $this->appConfig->getValueString(Application::APP_ID, 'engine_secret', '', lazy: true);
		return $stored === '' ? '' : $this->crypto->decrypt($stored);
	}

	public function isConfigured(): bool {
		return $this->engineUrl() !== '' && $this->instanceId() !== '' && $this->secret() !== '';
	}

	/** @throws \InvalidArgumentException */
	public function save(string $url, string $instance, ?string $secret, bool $allowLocal): void {
		$parts = parse_url($url);
		if ($parts === false || !isset($parts['scheme'], $parts['host']) || isset($parts['user']) || isset($parts['pass']) || isset($parts['query'])) {
			throw new \InvalidArgumentException('engine_url_invalid');
		}
		if ($parts['scheme'] !== 'https' && !($allowLocal && $parts['scheme'] === 'http' && in_array($parts['host'], ['127.0.0.1', 'localhost', '::1'], true))) {
			throw new \InvalidArgumentException('https_required');
		}
		if (!preg_match('/^[A-Za-z0-9_.-]{4,80}$/', $instance)) {
			throw new \InvalidArgumentException('instance_invalid');
		}
		$this->appConfig->setValueString(Application::APP_ID, 'engine_url', rtrim($url, '/'));
		$this->appConfig->setValueString(Application::APP_ID, 'instance_id', $instance);
		$this->appConfig->setValueBool(Application::APP_ID, 'allow_local_engine', $allowLocal);
		if ($secret !== null && $secret !== '') {
			if (strlen($secret) < 32) {
				throw new \InvalidArgumentException('secret_too_short');
			}
			$this->appConfig->setValueString(Application::APP_ID, 'engine_secret', $this->crypto->encrypt($secret), lazy: true, sensitive: true);
		}
	}
}
