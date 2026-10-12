<?php

declare(strict_types=1);

namespace OCA\AxiorhubPilote\Tests\Unit;

use OCA\AxiorhubPilote\Service\UserLink;
use OCP\IConfig;
use OCP\Security\ICrypto;
use PHPUnit\Framework\TestCase;

/** 0.3.0 : liaison personnelle — clé chiffrée dans les préférences de l'utilisateur, jamais de mot de passe, adresse HTTPS contrôlée. */
class UserLinkTest extends TestCase {
	/** @var array<string,string> */
	private array $values = [];

	private function link(): UserLink {
		$config = $this->createMock(IConfig::class);
		$config->method('getUserValue')->willReturnCallback(fn ($u, $app, $k, $d = '') => $this->values[$u . '/' . $k] ?? $d);
		$config->method('setUserValue')->willReturnCallback(function ($u, $app, $k, $v) {
			$this->values[$u . '/' . $k] = (string)$v;
		});
		$config->method('deleteUserValue')->willReturnCallback(function ($u, $app, $k) {
			unset($this->values[$u . '/' . $k]);
		});
		$crypto = $this->createMock(ICrypto::class);
		$crypto->method('encrypt')->willReturnCallback(fn ($v) => 'chiffre:' . strrev($v));
		$crypto->method('decrypt')->willReturnCallback(fn ($v) => strrev(substr($v, 8)));
		return new UserLink($config, $crypto);
	}

	public function testStoreKeepsOnlyAnEncryptedKeyAndForgetRemovesEverything(): void {
		$l = $this->link();
		$this->assertFalse($l->linked('alice'));
		$l->store('alice', 'https://moteur.example.test/agent-courriel/', false, ['key_id' => str_repeat('b', 32), 'secret' => 'secret-fictif', 'user' => 'avocat-fictif', 'role' => 'avocat']);
		$this->assertTrue($l->linked('alice'));
		$this->assertSame('chiffre:fitcif-terces', $this->values['alice/key_secret']);
		$this->assertStringNotContainsString('secret-fictif', implode('|', $this->values));
		$c = $l->credentials('alice');
		$this->assertSame(['https://moteur.example.test/agent-courriel', 'secret-fictif', 'avocat-fictif'], [$c['url'], $c['secret'], $c['user']]);
		$this->assertFalse($l->linked('bob'));
		$l->forget('alice');
		$this->assertSame([], $this->values);
	}

	/** @dataProvider urls */
	public function testUrlRules(string $url, bool $local, ?string $error): void {
		if ($error !== null) {
			$this->expectExceptionMessage($error);
		}
		$this->assertSame(rtrim($url, '/'), UserLink::checkUrl($url, $local));
	}

	public static function urls(): array {
		return [
			['https://courriel.example.test/agent-courriel/', false, null],
			['http://courriel.example.test/agent-courriel', false, 'https_required'],
			['http://127.0.0.1:8765', true, null],
			['http://10.0.0.5:8765', true, 'https_required'],
			['https://user:pw@courriel.example.test', false, 'engine_url_invalid'],
			['https://courriel.example.test/?x=1', false, 'engine_url_invalid'],
			['pas une adresse', false, 'engine_url_invalid'],
		];
	}
}
