<?php

declare(strict_types=1);

namespace OCA\AxiorhubPilote\Db;

use OCP\AppFramework\Db\Entity;

/**
 * Rattachement d'un dossier du moteur (référence) à un groupe Nextcloud et à un dossier racine (file_id).
 *
 * @method string getMatterRef()
 * @method void setMatterRef(string $value)
 * @method string getLabel()
 * @method void setLabel(string $value)
 * @method string getGroupId()
 * @method void setGroupId(string $value)
 * @method int getRootFileId()
 * @method void setRootFileId(int $value)
 * @method int getCreatedAt()
 * @method void setCreatedAt(int $value)
 */
class Matter extends Entity implements \JsonSerializable {
	protected $matterRef = '';
	protected $label = '';
	protected $groupId = '';
	protected $rootFileId = 0;
	protected $createdAt = 0;

	public function __construct() {
		$this->addType('rootFileId', 'integer');
		$this->addType('createdAt', 'integer');
	}

	public function jsonSerialize(): array {
		return ['matter' => $this->matterRef, 'label' => $this->label, 'root_file_id' => $this->rootFileId];
	}
}
