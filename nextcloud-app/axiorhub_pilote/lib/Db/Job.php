<?php

declare(strict_types=1);

namespace OCA\AxiorhubPilote\Db;

use OCP\AppFramework\Db\Entity;

/**
 * Travail confié au moteur : référence du travail distant, opération, dossier, état connu, clé d'idempotence (unique par utilisateur).
 *
 * @method string getUserId()
 * @method void setUserId(string $value)
 * @method string getEngineJobId()
 * @method void setEngineJobId(string $value)
 * @method string getOperation()
 * @method void setOperation(string $value)
 * @method string getMatterRef()
 * @method void setMatterRef(string $value)
 * @method string getState()
 * @method void setState(string $value)
 * @method string getIdempotencyKey()
 * @method void setIdempotencyKey(string $value)
 * @method int getCreatedAt()
 * @method void setCreatedAt(int $value)
 * @method int getUpdatedAt()
 * @method void setUpdatedAt(int $value)
 */
class Job extends Entity implements \JsonSerializable {
	protected $userId = '';
	protected $engineJobId = '';
	protected $operation = '';
	protected $matterRef = '';
	protected $state = 'pending';
	protected $idempotencyKey = '';
	protected $createdAt = 0;
	protected $updatedAt = 0;

	public function __construct() {
		$this->addType('createdAt', 'integer');
		$this->addType('updatedAt', 'integer');
	}

	public function jsonSerialize(): array {
		return ['id' => $this->engineJobId, 'operation' => $this->operation, 'matter' => $this->matterRef, 'state' => $this->state,
			'created_at' => $this->createdAt, 'updated_at' => $this->updatedAt];
	}
}
