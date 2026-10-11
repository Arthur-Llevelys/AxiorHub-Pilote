<?php

declare(strict_types=1);

namespace OCA\AxiorhubPilote\Service;

use OCA\AxiorhubPilote\Db\Matter;
use OCA\AxiorhubPilote\Db\MatterMapper;
use OCP\IGroupManager;

/**
 * Périmètre d'un utilisateur : dossiers rattachés à un groupe dont il est membre (ou tous, pour un administrateur).
 * Le périmètre est recalculé à chaque requête : un utilisateur qui perd ses droits perd l'accès aux résultats.
 */
class MatterService {
	public function __construct(
		private MatterMapper $mapper,
		private IGroupManager $groups,
	) {
	}

	/** @return Matter[] */
	public function visible(string $userId): array {
		$all = $this->mapper->findAll();
		if ($this->groups->isAdmin($userId)) {
			return $all;
		}
		return array_values(array_filter($all, fn (Matter $m) => $this->groups->isInGroup($userId, $m->getGroupId())));
	}

	/** @return string[] */
	public function scope(string $userId): array {
		return array_map(fn (Matter $m) => $m->getMatterRef(), $this->visible($userId));
	}

	public function allowed(string $userId, string $matterRef): bool {
		return in_array($matterRef, $this->scope($userId), true);
	}
}
