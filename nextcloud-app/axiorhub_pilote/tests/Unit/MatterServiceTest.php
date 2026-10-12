<?php

declare(strict_types=1);

namespace OCA\AxiorhubPilote\Tests\Unit;

use OCA\AxiorhubPilote\Db\MatterMapper;
use OCA\AxiorhubPilote\Service\EngineClient;
use OCA\AxiorhubPilote\Service\EngineSettings;
use OCA\AxiorhubPilote\Service\MatterService;
use OCA\AxiorhubPilote\Service\UserLink;
use OCP\IGroupManager;
use PHPUnit\Framework\TestCase;

/** 0.3.0 : un compte relié voit les dossiers de son compte AxiorHub (lus au moteur, filtrés, archivés exclus) ; périmètre « * ». */
class MatterServiceTest extends TestCase {
	public function testLinkedUserListsTheCasesOfItsAxiorhubAccount(): void {
		$engine = $this->createMock(EngineClient::class);
		$engine->expects($this->once())->method('call')->with('GET', '/integration/v1/matters', 'alice', ['*'])->willReturn(['status' => 200, 'body' => ['matters' => [
			['id' => 'DEMO-A', 'label' => 'DEMO - Bail commercial', 'archived' => false],
			['id' => 'DEMO-B', 'label' => 'BRAVO - Succession', 'archived' => false],
			['id' => 'OLD-1', 'label' => 'DEMO - Ancien', 'archived' => true],
		]]]);
		$links = $this->createMock(UserLink::class);
		$links->method('linked')->willReturn(true);
		$service = new MatterService($this->createMock(MatterMapper::class), $this->createMock(IGroupManager::class), $engine,
			$this->createMock(EngineSettings::class), $links);
		$out = $service->listFor('alice', 'bail');
		$this->assertSame([['matter' => 'DEMO-A', 'label' => 'DEMO - Bail commercial']], $out['matters']);
		$this->assertSame(['*'], $service->scope('alice'));
		$this->assertTrue($service->allowed('alice', 'DEMO-B'));
		$this->assertFalse($service->allowed('alice', '../etc'));
	}

	public function testEngineErrorIsPassedToTheInterface(): void {
		$engine = $this->createMock(EngineClient::class);
		$engine->method('call')->willReturn(['status' => 503, 'body' => ['code' => 'moteur_occupe']]);
		$links = $this->createMock(UserLink::class);
		$links->method('linked')->willReturn(true);
		$service = new MatterService($this->createMock(MatterMapper::class), $this->createMock(IGroupManager::class), $engine,
			$this->createMock(EngineSettings::class), $links);
		$this->assertSame('moteur_occupe', $service->listFor('alice')['code']);
	}
}
