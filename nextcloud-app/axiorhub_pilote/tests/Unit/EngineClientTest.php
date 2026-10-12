<?php

declare(strict_types=1);

namespace OCA\AxiorhubPilote\Tests\Unit;

use OCA\AxiorhubPilote\Service\EngineClient;
use OCA\AxiorhubPilote\Service\EngineSettings;
use OCA\AxiorhubPilote\Service\UserLink;
use OCP\Http\Client\IClient;
use OCP\Http\Client\IClientService;
use OCP\Http\Client\IResponse;
use PHPUnit\Framework\TestCase;
use Psr\Log\LoggerInterface;

/** Contrat moteur (agent/integration5634.py, integration5636.py) : contexte signé, clé personnelle, réponses non JSON qualifiées. */
class EngineClientTest extends TestCase {
	/** @var list<array{method:string, url:string, options:array}> */
	private array $sent = [];

	private function response(int $status, string $body): IResponse {
		$r = $this->createMock(IResponse::class);
		$r->method('getStatusCode')->willReturn($status);
		$r->method('getBody')->willReturn($body);
		return $r;
	}

	/** @param list<IResponse> $responses */
	private function client(array $responses, ?array $cred = null, bool $instance = false): EngineClient {
		$http = $this->createMock(IClient::class);
		$http->method('get')->willReturnCallback(function (string $url, array $options) use (&$responses) {
			$this->sent[] = ['method' => 'GET', 'url' => $url, 'options' => $options];
			return array_shift($responses);
		});
		$http->method('post')->willReturnCallback(function (string $url, array $options) use (&$responses) {
			$this->sent[] = ['method' => 'POST', 'url' => $url, 'options' => $options];
			return array_shift($responses);
		});
		$service = $this->createMock(IClientService::class);
		$service->method('newClient')->willReturn($http);
		$settings = $this->createMock(EngineSettings::class);
		$settings->method('isConfigured')->willReturn($instance);
		$settings->method('instanceId')->willReturn('cabinet-test');
		$settings->method('secret')->willReturn(str_repeat('s', 40));
		$settings->method('engineUrl')->willReturn('https://moteur.example.test/agent-courriel');
		$links = $this->createMock(UserLink::class);
		$links->method('credentials')->willReturn($cred);
		$links->method('linked')->willReturn($cred !== null);
		return new EngineClient($service, $settings, $links, $this->createMock(LoggerInterface::class));
	}

	private function cred(): array {
		return ['url' => 'https://perso.example.test/agent-courriel', 'key_id' => str_repeat('a', 32), 'secret' => 'secret-personnel-fictif',
			'user' => 'avocat-fictif', 'role' => 'avocat', 'allow_local' => false, 'linked_at' => ''];
	}

	private function ctx(string $header): array {
		return json_decode(base64_decode(strtr(explode('.', $header)[0], '-_', '+/')), true);
	}

	public function testSignatureIsHmacOfThePayloadAndContextIsShortLived(): void {
		$header = EngineClient::sign(['instance' => 'x', 'user' => 'alice', 'scope' => ['DEMO-A'], 'op' => 'GET /integration/v1/me',
			'exp' => '2026-10-12T10:00:00+00:00', 'nonce' => str_repeat('0', 32)], 'k');
		[$payload, $mac] = explode('.', $header);
		$this->assertSame(hash_hmac('sha256', $payload, 'k'), $mac);
		$this->assertStringNotContainsString('=', $payload);
		$this->assertSame('GET /integration/v1/me', $this->ctx($header)['op']);
	}

	public function testInstanceModeKeepsTheSharedSecretAndScope(): void {
		$client = $this->client([$this->response(200, '{"matters":[]}')], null, true);
		$client->call('GET', '/integration/v1/matters', 'alice', ['DEMO-A']);
		$headers = $this->sent[0]['options']['headers'];
		$this->assertArrayNotHasKey('X-AxiorHub-Key', $headers);
		$ctx = $this->ctx($headers['X-AxiorHub-Context']);
		$this->assertSame(['cabinet-test', ['DEMO-A']], [$ctx['instance'], $ctx['scope']]);
		$this->assertMatchesRegularExpression('/^[a-f0-9]{32}$/', $ctx['nonce']);
		$this->assertLessThanOrEqual(time() + 300, (new \DateTimeImmutable($ctx['exp']))->getTimestamp());
	}

	public function testLinkedUserSignsWithThePersonalKeyAtItsOwnAddress(): void {
		$client = $this->client([$this->response(200, '{"user":"avocat-fictif"}')], $this->cred());
		$out = $client->call('GET', '/integration/v1/me', 'alice', ['DEMO-A']);
		$this->assertSame(200, $out['status']);
		$this->assertSame('https://perso.example.test/agent-courriel/integration/v1/me', $this->sent[0]['url']);
		$headers = $this->sent[0]['options']['headers'];
		$this->assertSame(str_repeat('a', 32), $headers['X-AxiorHub-Key']);
		[$payload, $mac] = explode('.', $headers['X-AxiorHub-Context']);
		$this->assertSame(hash_hmac('sha256', $payload, 'secret-personnel-fictif'), $mac);
		$ctx = $this->ctx($headers['X-AxiorHub-Context']);
		$this->assertSame(['personnel:avocat-fictif', 'GET /integration/v1/me', []], [$ctx['instance'], $ctx['op'], $ctx['scope']]);
		$this->assertFalse($this->sent[0]['options']['allow_redirects']);
	}

	public function testUnlinkedUserWithoutInstanceIsAskedToLinkAnAccount(): void {
		$out = $this->client([])->call('GET', '/integration/v1/matters', 'bob', []);
		$this->assertSame([409, 'compte_non_relie'], [$out['status'], $out['body']['code']]);
		$this->assertSame([], $this->sent);
	}

	public function testHtmlBusyPageIsQualifiedAndAReadIsRetriedOnce(): void {
		$client = $this->client([$this->response(503, '<!doctype html><title>Un instant</title>'), $this->response(200, '{"matters":[]}')], $this->cred());
		$out = $client->call('GET', '/integration/v1/matters', 'alice', ['*']);
		$this->assertSame(200, $out['status']);
		$this->assertCount(2, $this->sent);
		$this->assertNotSame($this->sent[0]['options']['headers']['X-AxiorHub-Context'], $this->sent[1]['options']['headers']['X-AxiorHub-Context']);   // nonce renouvelé
	}

	public function testWritesAreNeverReplayedAndNonJsonGetsAPreciseCode(): void {
		$client = $this->client([$this->response(503, '<html>'), $this->response(302, '')], $this->cred());
		$out = $client->call('POST', '/integration/v1/jobs', 'alice', ['*'], ['operation' => 'instruction']);
		$this->assertSame([503, 'moteur_occupe'], [$out['status'], $out['body']['code']]);
		$this->assertCount(1, $this->sent);
		$out = $client->call('GET', '/integration/v1/me', 'alice', ['*']);
		$this->assertSame('acces_refuse_par_le_frontal', $out['body']['code']);
	}

	public function testQueryIsSentSeparatelyFromTheSignedOperation(): void {
		$client = $this->client([$this->response(200, '{"results":[]}')], $this->cred());
		$client->call('GET', '/integration/v1/search', 'alice', ['*'], null, ['q' => 'bail commercial']);
		$this->assertSame('https://perso.example.test/agent-courriel/integration/v1/search?q=bail+commercial', $this->sent[0]['url']);
		$this->assertSame('GET /integration/v1/search', $this->ctx($this->sent[0]['options']['headers']['X-AxiorHub-Context'])['op']);
		$this->expectException(\InvalidArgumentException::class);
		$client->call('GET', '/integration/v1/search?q=x', 'alice', ['*']);
	}
}
