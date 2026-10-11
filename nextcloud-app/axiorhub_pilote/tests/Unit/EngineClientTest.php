<?php

declare(strict_types=1);

namespace OCA\AxiorhubPilote\Tests\Unit;

use OCA\AxiorhubPilote\Service\EngineClient;
use OCA\AxiorhubPilote\Service\EngineSettings;
use OCP\Http\Client\IClientService;
use PHPUnit\Framework\TestCase;
use Psr\Log\LoggerInterface;

/** Le contexte signé suit le contrat du moteur (agent/integration5634.py) : charge base64url, HMAC-SHA256, nonce, expiration courte. */
class EngineClientTest extends TestCase {
	public function testContextIsSignedAndShortLived(): void {
		$settings = $this->createMock(EngineSettings::class);
		$settings->method('instanceId')->willReturn('cabinet-test');
		$settings->method('secret')->willReturn(str_repeat('s', 40));
		$client = new EngineClient($this->createMock(IClientService::class), $settings, $this->createMock(LoggerInterface::class));
		[$payload, $mac] = explode('.', $client->context('alice', ['DEMO-A'], 'GET /integration/v1/health'));
		$this->assertSame(hash_hmac('sha256', $payload, str_repeat('s', 40)), $mac);
		$ctx = json_decode(base64_decode(strtr($payload, '-_', '+/')), true);
		$this->assertSame('cabinet-test', $ctx['instance']);
		$this->assertSame(['DEMO-A'], $ctx['scope']);
		$this->assertMatchesRegularExpression('/^[a-f0-9]{32}$/', $ctx['nonce']);
		$this->assertLessThanOrEqual(time() + 300, (new \DateTimeImmutable($ctx['exp']))->getTimestamp());
	}
}
