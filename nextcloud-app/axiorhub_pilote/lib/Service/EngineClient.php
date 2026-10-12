<?php

declare(strict_types=1);

namespace OCA\AxiorhubPilote\Service;

use OCP\Http\Client\IClientService;
use Psr\Log\LoggerInterface;

/**
 * Client du contrat moteur /integration/v1 : contexte signé (HMAC-SHA256), opération signée, expiration courte et nonce à usage unique ;
 * aucune redirection suivie, délais et tailles bornés. Les journaux portent le correlation_id renvoyé par le moteur, jamais le texte
 * d'une pièce ni un secret.
 *
 * 0.3.0 :
 * - compte relié personnellement (préférence) : adresse et clé personnelle de l'utilisateur, en-tête X-AxiorHub-Key ;
 * - sinon mode d'instance (secret partagé, périmètre par groupes) ;
 * - réponse non JSON (page d'attente, erreur du frontal) ou moteur occupé : code précis (moteur_occupe, reponse_moteur_invalide,
 *   acces_refuse_par_le_frontal) au lieu d'un « injoignable » trompeur ; une lecture est réessayée une fois quand le moteur est occupé.
 */
class EngineClient {
	private const TIMEOUT = 25;

	public function __construct(
		private IClientService $clients,
		private EngineSettings $settings,
		private UserLink $links,
		private LoggerInterface $logger,
	) {
	}

	/** Contexte signé ; le JSON exact signé est transmis tel quel (aucune recanonisation côté moteur). */
	public static function sign(array $ctx, string $secret): string {
		$payload = rtrim(strtr(base64_encode((string)json_encode($ctx, JSON_UNESCAPED_UNICODE | JSON_UNESCAPED_SLASHES)), '+/', '-_'), '=');
		return $payload . '.' . hash_hmac('sha256', $payload, $secret);
	}

	/** @param string[] $scope */
	public function context(string $instance, string $secret, string $userId, array $scope, string $operation): string {
		return self::sign([
			'instance' => $instance,
			'user' => $userId,
			'scope' => array_values($scope),
			'op' => $operation,
			'exp' => (new \DateTimeImmutable('now', new \DateTimeZone('UTC')))->modify('+2 minutes')->format('Y-m-d\TH:i:sP'),
			'nonce' => bin2hex(random_bytes(16)),
		], $secret);
	}

	public function isAvailableFor(string $userId): bool {
		return $this->links->linked($userId) || $this->settings->isConfigured();
	}

	/**
	 * @param string[] $scope
	 * @param array<string,string> $query
	 * @return array{status:int, body:array<string,mixed>}
	 */
	public function call(string $method, string $path, string $userId, array $scope, ?array $body = null, array $query = []): array {
		if (!str_starts_with($path, '/integration/v1/') || str_contains($path, '?')) {
			throw new \InvalidArgumentException('path_refused');
		}
		$cred = $this->links->credentials($userId);
		if ($cred === null && !$this->settings->isConfigured()) {
			return ['status' => 409, 'body' => ['code' => 'compte_non_relie',
				'message' => 'Link your AxiorHub account in Personal settings › AxiorHub Pilote.']];
		}
		$url = $cred !== null ? $cred['url'] : $this->settings->engineUrl();
		$allowLocal = $cred !== null ? $cred['allow_local'] : $this->settings->allowLocal();
		$target = $url . $path . ($query !== [] ? '?' . http_build_query($query) : '');
		$out = $this->request($method, $target, $this->signedHeaders($cred, $method, $path, $userId, $scope), $body, $allowLocal);
		if ($method === 'GET' && $out['status'] === 503 && ($out['body']['code'] ?? '') === 'moteur_occupe') {
			usleep(1500000);   // lecture : un seul nouvel essai (signature et nonce renouvelés)
			$out = $this->request($method, $target, $this->signedHeaders($cred, $method, $path, $userId, $scope), $body, $allowLocal);
		}
		return $out;
	}

	/** @param string[] $scope */
	private function signedHeaders(?array $cred, string $method, string $path, string $userId, array $scope): array {
		if ($cred !== null) {
			return ['Accept' => 'application/json', 'X-AxiorHub-Key' => $cred['key_id'],
				'X-AxiorHub-Context' => $this->context('personnel:' . $cred['user'], $cred['secret'], $userId, [], $method . ' ' . $path)];
		}
		return ['Accept' => 'application/json',
			'X-AxiorHub-Context' => $this->context($this->settings->instanceId(), $this->settings->secret(), $userId, $scope, $method . ' ' . $path)];
	}

	/**
	 * Appairage : identifiants AxiorHub transmis une fois (HTTPS) ; renvoie la clé personnelle.
	 * @return array{status:int, body:array<string,mixed>}
	 */
	public function pair(string $url, bool $allowLocal, string $login, string $password, string $label): array {
		return $this->request('POST', $url . '/integration/v1/pair', ['Accept' => 'application/json'],
			['login' => $login, 'password' => $password, 'label' => $label], $allowLocal);
	}

	/** @return array{status:int, body:array<string,mixed>} */
	public function health(string $url, bool $allowLocal): array {
		return $this->request('GET', $url . '/integration/v1/health', ['Accept' => 'application/json'], null, $allowLocal);
	}

	/** @return array{status:int, body:array<string,mixed>} */
	private function request(string $method, string $target, array $headers, ?array $body, bool $allowLocal): array {
		$options = [
			'timeout' => self::TIMEOUT,
			'allow_redirects' => false,
			'http_errors' => false,
			'nextcloud' => ['allow_local_address' => $allowLocal],
			'headers' => $headers,
		];
		if ($body !== null) {
			$options['headers']['Content-Type'] = 'application/json';
			// un corps vide est un objet JSON ({}), jamais une liste ([]) : le moteur exige un objet
			$options['body'] = $body === [] ? '{}' : json_encode($body, JSON_UNESCAPED_UNICODE | JSON_UNESCAPED_SLASHES);
		}
		$client = $this->clients->newClient();
		try {
			$response = $method === 'GET' ? $client->get($target, $options) : $client->post($target, $options);
		} catch (\Throwable $e) {
			$this->logger->warning('AxiorHub engine unreachable', ['app' => 'axiorhub_pilote', 'exception' => get_class($e)]);
			$local = str_contains(strtolower($e->getMessage()), 'local') ? 'adresse_locale_refusee' : 'moteur_injoignable';
			return ['status' => 503, 'body' => ['code' => $local, 'retryable' => true,
				'message' => $local === 'adresse_locale_refusee'
					? 'Nextcloud refuses this local address: tick “Engine on this server (local address)”.'
					: 'AxiorHub engine unreachable from this Nextcloud server (address, firewall or certificate).']];
		}
		$status = $response->getStatusCode();
		$raw = (string)$response->getBody();
		if (strlen($raw) > 5_000_000) {
			return ['status' => 502, 'body' => ['code' => 'reponse_trop_volumineuse']];
		}
		$decoded = json_decode($raw, true);
		if (!is_array($decoded)) {
			// page HTML : page d'attente du moteur (base occupée), page de connexion d'un frontal, erreur du serveur web…
			$code = $status === 503 ? 'moteur_occupe' : (in_array($status, [301, 302, 303, 307, 401, 403], true) ? 'acces_refuse_par_le_frontal' : 'reponse_moteur_invalide');
			return ['status' => $status >= 400 ? $status : 502, 'body' => ['code' => $code, 'http_status' => $status, 'retryable' => $status === 503,
				'message' => 'The engine answered with a non-JSON page (HTTP ' . $status . ').']];
		}
		if ($status >= 400) {
			$this->logger->info('AxiorHub engine error', ['app' => 'axiorhub_pilote', 'code' => $decoded['code'] ?? '', 'correlation_id' => $decoded['correlation_id'] ?? '']);
		}
		return ['status' => $status, 'body' => $decoded];
	}
}
