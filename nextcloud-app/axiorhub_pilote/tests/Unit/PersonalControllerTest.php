<?php

declare(strict_types=1);

namespace OCA\AxiorhubPilote\Tests\Unit;

use OCA\AxiorhubPilote\Controller\PersonalController;
use OCA\AxiorhubPilote\Service\EngineClient;
use OCA\AxiorhubPilote\Service\EngineSettings;
use OCA\AxiorhubPilote\Service\UserLink;
use OCP\IRequest;
use OCP\IUser;
use OCP\IUserSession;
use PHPUnit\Framework\TestCase;

/** 0.3.0 : relier son compte AxiorHub — santé contrôlée d'abord, motif précis en cas de refus, clé stockée seulement en cas de succès. */
class PersonalControllerTest extends TestCase {
	private function controller(EngineClient $engine, UserLink $links): PersonalController {
		$user = $this->createMock(IUser::class);
		$user->method('getUID')->willReturn('alice');
		$session = $this->createMock(IUserSession::class);
		$session->method('getUser')->willReturn($user);
		return new PersonalController($this->createMock(IRequest::class), $links, $engine, $this->createMock(EngineSettings::class), $session);
	}

	public function testSuccessfulLinkStoresTheKeyNeverThePassword(): void {
		$engine = $this->createMock(EngineClient::class);
		$engine->method('health')->willReturn(['status' => 200, 'body' => ['version' => '5.6.36', 'contract' => '1.2']]);
		$engine->expects($this->once())->method('pair')->with('https://moteur.example.test/agent-courriel', false, 'avocat-fictif', 'mot-de-passe-fictif', 'alice')
			->willReturn(['status' => 200, 'body' => ['key_id' => str_repeat('c', 32), 'secret' => 'cle-fictive', 'user' => 'avocat-fictif', 'role' => 'avocat']]);
		$links = $this->createMock(UserLink::class);
		$links->expects($this->once())->method('store')->with('alice', 'https://moteur.example.test/agent-courriel', false,
			$this->callback(fn ($key) => !isset($key['password']) && $key['secret'] === 'cle-fictive'));
		$out = $this->controller($engine, $links)->link('https://moteur.example.test/agent-courriel/', 'avocat-fictif', 'mot-de-passe-fictif');
		$this->assertSame(200, $out->getStatus());
		$this->assertSame('avocat-fictif', $out->getData()['axiorhub_user']);
		$this->assertStringNotContainsString('mot-de-passe-fictif', json_encode($out->getData()));
	}

	public function testRefusalIsReportedWithItsCodeAndNothingIsStored(): void {
		$engine = $this->createMock(EngineClient::class);
		$engine->method('health')->willReturn(['status' => 200, 'body' => []]);
		$engine->method('pair')->willReturn(['status' => 401, 'body' => ['code' => 'identifiants_refuses']]);
		$links = $this->createMock(UserLink::class);
		$links->expects($this->never())->method('store');
		$out = $this->controller($engine, $links)->link('https://moteur.example.test', 'avocat-fictif', 'faux');
		$this->assertSame([401, 'identifiants_refuses', 'pair'], [$out->getStatus(), $out->getData()['code'], $out->getData()['step']]);
	}

	public function testUnreachableEngineStopsBeforeSendingThePassword(): void {
		$engine = $this->createMock(EngineClient::class);
		$engine->method('health')->willReturn(['status' => 503, 'body' => ['code' => 'moteur_injoignable']]);
		$engine->expects($this->never())->method('pair');
		$out = $this->controller($engine, $this->createMock(UserLink::class))->link('https://moteur.example.test', 'a', 'b');
		$this->assertSame(['moteur_injoignable', 'health'], [$out->getData()['code'], $out->getData()['step']]);
	}

	public function testHttpAddressIsRefused(): void {
		$out = $this->controller($this->createMock(EngineClient::class), $this->createMock(UserLink::class))->link('http://moteur.example.test', 'a', 'b');
		$this->assertSame([400, 'https_required'], [$out->getStatus(), $out->getData()['code']]);
	}
}
