<?php

declare(strict_types=1);

namespace OCA\AxiorhubPilote\Service;

use OCP\Files\File;
use OCP\Files\IRootFolder;
use OCP\Files\NotFoundException;

/**
 * Accès aux fichiers par les API publiques (dossier de l'utilisateur, nœuds et permissions) : jamais le répertoire physique « data »,
 * qui contournerait partages, stockages externes et chiffrement. Une sélection fournit un file_id ; le serveur résout le nœud dans le
 * contexte de l'utilisateur et relève ETag, taille, type et version avant transmission.
 */
class FileService {
	public function __construct(
		private IRootFolder $root,
	) {
	}

	/** @return array{file_id:string, name:string, etag:string, size:int, mime:string}|null */
	public function describe(string $userId, int $fileId): ?array {
		try {
			$node = $this->root->getUserFolder($userId)->getFirstNodeById($fileId);
		} catch (NotFoundException) {
			return null;
		}
		if (!$node instanceof File || !$node->isReadable()) {
			return null;
		}
		return ['file_id' => (string)$fileId, 'name' => $node->getName(), 'etag' => $node->getEtag(), 'size' => $node->getSize(), 'mime' => $node->getMimetype()];
	}

	/**
	 * Manifeste des sources autorisées : chaque file_id est revérifié pour l'utilisateur courant.
	 * @param int[] $fileIds
	 * @return array{sources: list<array<string,mixed>>, refused: list<string>}
	 */
	public function manifest(string $userId, array $fileIds): array {
		$sources = [];
		$refused = [];
		foreach (array_slice($fileIds, 0, 500) as $id) {
			$info = $this->describe($userId, (int)$id);
			if ($info === null) {
				$refused[] = (string)$id;
				continue;
			}
			$sources[] = $info;
		}
		return ['sources' => $sources, 'refused' => $refused];
	}
}
