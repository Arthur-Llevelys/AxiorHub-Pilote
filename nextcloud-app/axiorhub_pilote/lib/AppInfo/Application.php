<?php

declare(strict_types=1);

namespace OCA\AxiorhubPilote\AppInfo;

use OCP\AppFramework\App;
use OCP\AppFramework\Bootstrap\IBootContext;
use OCP\AppFramework\Bootstrap\IBootstrap;
use OCP\AppFramework\Bootstrap\IRegistrationContext;

class Application extends App implements IBootstrap {
	public const APP_ID = 'axiorhub_pilote';

	public function __construct(array $urlParams = []) {
		parent::__construct(self::APP_ID, $urlParams);
	}

	public function register(IRegistrationContext $context): void {
		// Les services sont injectés automatiquement (autowiring) ; aucun écouteur global n'est nécessaire.
	}

	public function boot(IBootContext $context): void {
	}
}
