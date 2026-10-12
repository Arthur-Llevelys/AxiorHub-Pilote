<?php

declare(strict_types=1);

namespace OCA\AxiorhubPilote\Settings;

use OCA\AxiorhubPilote\AppInfo\Application;
use OCP\IL10N;
use OCP\IURLGenerator;
use OCP\Settings\IIconSection;

/** 0.3.0 : rubrique « AxiorHub Pilote » des paramètres personnels. */
class PersonalSection implements IIconSection {
	public function __construct(
		private IURLGenerator $urls,
		private IL10N $l,
	) {
	}

	public function getID(): string {
		return Application::APP_ID;
	}

	public function getName(): string {
		return $this->l->t('AxiorHub Pilote');
	}

	public function getPriority(): int {
		return 80;
	}

	public function getIcon(): string {
		return $this->urls->imagePath(Application::APP_ID, 'app-dark.svg');
	}
}
