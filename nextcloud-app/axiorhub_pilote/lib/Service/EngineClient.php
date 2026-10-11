<?php

declare(strict_types=1);

namespace OCA\AxiorhubPilote\Service;

use OCP\Http\Client\IClientService;
use Psr\Log\LoggerInterface;

/**
 * Client du contrat moteur /integration/v1 : contexte signé (HMAC-SHA256), périmètre de l'utilisateur, expiration courte et nonce à
 * usage unique ; destination administrée uniquement, aucune redirection suivie, délais et tailles bornés.
 * Les journaux portent le correlation_id renvoyé par le moteur, jamais le texte d'une pièce ni un secret.
 */
class EngineClient {
	private const TIMEOUT = 20;

	public function __construct(
		private IClientService $clients,
		private EngineSettings $settings,
		private LoggerInterface $logger,
	) {
	}

	/** @param string[] $scope */
	public function context(string $userId, array $scope, string $operation): string {
		$ctx = [
			'instance' => $this->settings->instanceId(),
			'user' => $userId,
			'scope' => array_values($scope),
			'op' => $operation,
			'exp' => (new \DateTimeImmutable('now', new \DateTimeZone('UTC')))->modify('+2 minutes')->format('Y-m-d\TH:i:sP'),
			'nonce' => bin2hex(random_bytes(16)),
		];
		$payload = rtrim(strtr(base64_encode((string)json_encode($ctx, JSON_UNESCAPED_UNICODE | JSON_UNESCAPED_SLASHES)), '+/', '-_'), '=');
		return $payload . '.' . hash_hmac('sha256', $payload, $this->settings->secret());
	}

	/**
	 * @param string[] $scope
	 * @return array{status:int, body:array<string,mixed>}
	 */
	public function call(string $method, string $path, string $userId, array $scope, ?array $body = null): array {
		if (!$this->settings->isConfigured()) {
			return ['status' => 503, 'body' => ['code' => 'moteur_non_configure', 'message' => 'AxiorHub engine not configured']];
		}
		if (!str_starts_with($path, '/integration/v1/')) {
			throw new \InvalidArgumentException('path_refused');
		}
		$options = [
			'timeout' => self::TIMEOUT,
			'allow_redirects' => false,
			'http_errors' => false,
			'nextcloud' => ['allow_local_address' => $this->settings->allowLocal()],
			'headers' => ['X-AxiorHub-Context' => $this->context($userId, $scope, $method . ' ' . $path), 'Accept' => 'application/json'],
		];
		if ($body !== null) {
			$options['headers']['Content-Type'] = 'application/json';
			$options['body'] = json_encode($body, JSON_UNESCAPED_UNICODE | JSON_UNESCAPED_SLASHES);
		}
		$client = $this->clients->newClient();
		try {
			$response = $method === 'GET' ? $client->get($this->settings->engineUrl() . $path, $options) : $client->post($this->settings->engineUrl() . $path, $options);
		} catch (\Throwable $e) {
			$this->logger->warning('AxiorHub engine unreachable', ['app' => 'axiorhub_pilote', 'path' => $path, 'exception' => get_class($e)]);
			return ['status' => 503, 'body' => ['code' => 'moteur_injoignable', 'retryable' => true, 'message' => 'AxiorHub engine unreachable']];
		}
		$raw = (string)$response->getBody();
		if (strlen($raw) > 5_000_000) {
			return ['status' => 502, 'body' => ['code' => 'reponse_trop_volumineuse']];
		}
		$decoded = json_decode($raw, true);
		if (!is_array($decoded)) {
			return ['status' => 502, 'body' => ['code' => 'reponse_moteur_invalide']];
		}
		if ($response->getStatusCode() >= 400) {
			$this->logger->info('AxiorHub engine error', ['app' => 'axiorhub_pilote', 'code' => $decoded['code'] ?? '', 'correlation_id' => $decoded['correlation_id'] ?? '']);
		}
		return ['status' => $response->getStatusCode(), 'body' => $decoded];
	}
}
