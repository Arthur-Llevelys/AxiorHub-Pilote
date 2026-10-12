<?php

declare(strict_types=1);

namespace OCA\AxiorhubPilote\Tests\Unit;

use OCA\AxiorhubPilote\Controller\PageController;
use OCA\AxiorhubPilote\Controller\SessionController;
use OCA\AxiorhubPilote\Service\EmbedSession;
use OCA\AxiorhubPilote\Service\EngineClient;
use OCA\AxiorhubPilote\Service\EngineSettings;
use OCA\AxiorhubPilote\Service\UserLink;
use OCP\AppFramework\Services\IInitialState;
use OCP\IGroupManager;
use OCP\IRequest;
use OCP\IURLGenerator;
use OCP\IUser;
use OCP\IUserSession;
use PHPUnit\Framework\TestCase;

/** 0.4.0 : interface AxiorHub affichée dans Nextcloud — adresse de connexion à usage unique, origines contrôlées, cadre limité au moteur. */
class EmbedSessionTest extends TestCase {
	private const BASE = 'https://moteur.example.test/agent-courriel';

	private function links(bool $linked = true): UserLink {
		$links = $this->createMock(UserLink::class);
		$links->method('linked')->willReturn($linked);
		$links->method('credentials')->willReturn($linked ? ['url' => self::BASE, 'key_id' => str_repeat('c', 32), 'secret' => 'cle-fictive',
			'user' => 'avocat-fictif', 'role' => 'administrateur', 'allow_local' => false, 'linked_at' => ''] : null);
		return $links;
	}

	private function urls(): IURLGenerator {
		$urls = $this->createMock(IURLGenerator::class);
		$urls->method('getAbsoluteURL')->willReturn('https://Nuage.example.test/');
		$urls->method('linkToRoute')->willReturn('/settings/user/axiorhub_pilote');
		return $urls;
	}

	private function engine(array $answer, ?array &$sent = null): EngineClient {
		$engine = $this->createMock(EngineClient::class);
		$engine->method('call')->willReturnCallback(function (string $method, string $path, string $uid, array $scope, ?array $body) use ($answer, &$sent) {
			$sent = ['method' => $method, 'path' => $path, 'uid' => $uid, 'body' => $body];
			return $answer;
		});
		return $engine;
	}

	private function ok(): array {
		return ['status' => 200, 'body' => ['url' => self::BASE . '/nextcloud-session?jeton=jeton-fictif&suite=%2Fdossiers', 'base' => self::BASE,
			'frame_origin' => 'https://moteur.example.test', 'expires_in' => 60, 'standalone' => false]];
	}

	public function testOpenAsksTheEngineWithTheNextcloudOriginAndAnInternalPage(): void {
		$sent = null;
		$embed = new EmbedSession($this->engine($this->ok(), $sent), $this->links(), $this->urls());
		$out = $embed->open('alice', '/dossiers');
		$this->assertTrue($out['ok']);
		$this->assertSame(['POST', '/integration/v1/session', 'alice'], [$sent['method'], $sent['path'], $sent['uid']]);
		$this->assertSame(['suite' => '/dossiers', 'origin' => 'https://nuage.example.test'], $sent['body']);
		$this->assertSame('https://moteur.example.test', $out['frame_origin']);
		$this->assertStringStartsWith(self::BASE . '/nextcloud-session?jeton=', $out['url']);
	}

	public function testPageIsAlwaysAnInternalPath(): void {
		foreach (['//autre.example.test', 'https://autre.example.test', '/integration/v1/me', "/a\nb", '\\x', ''] as $page) {
			$this->assertSame('/aujourdhui', EmbedSession::cleanPage($page), $page);
		}
		$this->assertSame('/corporate', EmbedSession::cleanPage('/corporate'));
	}

	public function testAnAddressOutsideTheAnnouncedEngineIsRefused(): void {
		$answer = $this->ok();
		$answer['body']['url'] = 'https://autre.example.test/agent-courriel/nextcloud-session?jeton=x';
		$out = (new EmbedSession($this->engine($answer), $this->links(), $this->urls()))->open('alice');
		$this->assertSame([false, 'reponse_moteur_invalide'], [$out['ok'], $out['code']]);
	}

	public function testOlderEngineIsReportedAsToBeUpdated(): void {
		$out = (new EmbedSession($this->engine(['status' => 404, 'body' => ['code' => 'route_inconnue']]), $this->links(), $this->urls()))->open('alice');
		$this->assertSame([false, 'moteur_a_mettre_a_jour'], [$out['ok'], $out['code']]);
	}

	public function testUnlinkedUserGetsNoAddress(): void {
		$engine = $this->createMock(EngineClient::class);
		$engine->expects($this->never())->method('call');
		$out = (new EmbedSession($engine, $this->links(false), $this->urls()))->open('bob');
		$this->assertSame([false, 'compte_non_relie'], [$out['ok'], $out['code']]);
	}

	public function testPageAllowsOnlyTheEngineInAFrame(): void {
		$embed = new EmbedSession($this->engine($this->ok()), $this->links(), $this->urls());
		$user = $this->createMock(IUser::class);
		$user->method('getUID')->willReturn('alice');
		$session = $this->createMock(IUserSession::class);
		$session->method('getUser')->willReturn($user);
		$state = [];
		$initial = $this->createMock(IInitialState::class);
		$initial->method('provideInitialState')->willReturnCallback(function ($key, $value) use (&$state) {
			$state[$key] = $value;
		});
		$controller = new PageController($this->createMock(IRequest::class), $initial, $this->createMock(EngineSettings::class), $session,
			$this->createMock(IGroupManager::class), $this->links(), $this->urls(), $embed);
		$response = $controller->index('/dossiers');
		$csp = $response->getContentSecurityPolicy()->buildPolicy();
		$this->assertStringContainsString('https://moteur.example.test', $csp);
		$this->assertStringNotContainsString('autre.example.test', $csp);
		$this->assertTrue($state['config']['embed']['ok']);
		$this->assertStringNotContainsString('cle-fictive', json_encode($state));          // aucun secret dans la page
	}

	public function testSessionRouteReportsRefusalsWithTheirCode(): void {
		$user = $this->createMock(IUser::class);
		$user->method('getUID')->willReturn('alice');
		$session = $this->createMock(IUserSession::class);
		$session->method('getUser')->willReturn($user);
		$revoked = new EmbedSession($this->engine(['status' => 401, 'body' => ['code' => 'cle_personnelle_revoquee_ou_inconnue']]), $this->links(), $this->urls());
		$out = (new SessionController($this->createMock(IRequest::class), $revoked, $session))->create('/dossiers');
		$this->assertSame([403, 'cle_personnelle_revoquee_ou_inconnue'], [$out->getStatus(), $out->getData()['code']]);
		$good = new EmbedSession($this->engine($this->ok()), $this->links(), $this->urls());
		$out = (new SessionController($this->createMock(IRequest::class), $good, $session))->create('/dossiers');
		$this->assertSame(200, $out->getStatus());
	}
}
