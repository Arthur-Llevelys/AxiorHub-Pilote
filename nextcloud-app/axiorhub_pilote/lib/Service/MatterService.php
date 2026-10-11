<?php

declare(strict_types=1);

namespace OCA\AxiorhubPilote\Service;

use OCA\AxiorhubPilote\Db\Matter;
use OCA\AxiorhubPilote\Db\MatterMapper;
use OCP\AppFramework\Db\DoesNotExistException;
use OCP\IGroupManager;

/**
 * Périmètre d'un utilisateur : dossiers rattachés à un groupe dont il est membre (ou tous, pour un administrateur).
 * Le périmètre est recalculé à chaque requête : un utilisateur qui perd ses droits perd l'accès aux résultats.
 *
 * 0.2.0 : les dossiers du moteur sont importés automatiquement (enregistrement des réglages, bouton « Importer les dossiers », tâche de
 * fond) et rattachés au groupe par défaut (« admin » sauf réglage contraire) ; un rattachement choisi par l'administrateur n'est jamais
 * écrasé, seul l'intitulé est actualisé. Aucun dossier n'est supprimé automatiquement.
 */
class MatterService {
	public const SYSTEM_USER = 'nextcloud-admin-sync';

	public function __construct(
		private MatterMapper $mapper,
		private IGroupManager $groups,
		private EngineClient $engine,
		private EngineSettings $settings,
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

	public function isEmpty(): bool {
		return $this->mapper->countAll() === 0;
	}

	/**
	 * Importe la liste des dossiers du moteur (identifiant et intitulé seulement).
	 * @return array{ok:bool, imported:int, updated:int, total:int, code?:string}
	 */
	public function syncFromEngine(): array {
		if (!$this->settings->isConfigured()) {
			return ['ok' => false, 'imported' => 0, 'updated' => 0, 'total' => 0, 'code' => 'moteur_non_configure'];
		}
		$out = $this->engine->call('GET', '/integration/v1/matters', self::SYSTEM_USER, ['*']);
		if ($out['status'] !== 200 || !isset($out['body']['matters']) || !is_array($out['body']['matters'])) {
			return ['ok' => false, 'imported' => 0, 'updated' => 0, 'total' => 0, 'code' => (string)($out['body']['code'] ?? 'moteur_injoignable')];
		}
		$group = $this->settings->defaultGroup();
		if (!$this->groups->groupExists($group)) {
			$group = 'admin';
		}
		$imported = 0;
		$updated = 0;
		foreach (array_slice($out['body']['matters'], 0, 5000) as $row) {
			$ref = (string)($row['id'] ?? '');
			if (!preg_match('/^[A-Za-z0-9_.-]{1,80}$/', $ref)) {
				continue;
			}
			$label = mb_substr((string)($row['label'] ?? $ref), 0, 255);
			try {
				$m = $this->mapper->findByRef($ref);
				if ($m->getLabel() !== $label) {
					$m->setLabel($label);
					$this->mapper->update($m);
					$updated++;
				}
			} catch (DoesNotExistException) {
				$m = new Matter();
				$m->setMatterRef($ref);
				$m->setLabel($label);
				$m->setGroupId($group);
				$m->setRootFileId(0);
				$m->setCreatedAt(time());
				$this->mapper->insert($m);
				$imported++;
			}
		}
		return ['ok' => true, 'imported' => $imported, 'updated' => $updated, 'total' => count($out['body']['matters'])];
	}

	/** Rattachement choisi par l'administrateur (création ou modification). */
	public function link(string $matterRef, string $label, string $groupId, int $rootFileId = 0): void {
		try {
			$m = $this->mapper->findByRef($matterRef);
			$m->setGroupId($groupId);
			if ($label !== '') {
				$m->setLabel(mb_substr($label, 0, 255));
			}
			if ($rootFileId > 0) {
				$m->setRootFileId($rootFileId);
			}
			$this->mapper->update($m);
		} catch (DoesNotExistException) {
			$m = new Matter();
			$m->setMatterRef($matterRef);
			$m->setLabel(mb_substr($label !== '' ? $label : $matterRef, 0, 255));
			$m->setGroupId($groupId);
			$m->setRootFileId($rootFileId);
			$m->setCreatedAt(time());
			$this->mapper->insert($m);
		}
	}

	/** @return list<array{matter:string,label:string,group:string}> */
	public function adminList(): array {
		return array_map(fn (Matter $m) => ['matter' => $m->getMatterRef(), 'label' => $m->getLabel(), 'group' => $m->getGroupId()], $this->mapper->findAll());
	}
}
