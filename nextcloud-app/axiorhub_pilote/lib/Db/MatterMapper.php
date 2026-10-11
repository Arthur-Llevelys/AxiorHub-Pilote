<?php

declare(strict_types=1);

namespace OCA\AxiorhubPilote\Db;

use OCP\AppFramework\Db\QBMapper;
use OCP\IDBConnection;

/** @template-extends QBMapper<Matter> */
class MatterMapper extends QBMapper {
	public function __construct(IDBConnection $db) {
		parent::__construct($db, 'axh_matters', Matter::class);
	}

	/** @return Matter[] */
	public function findAll(): array {
		$qb = $this->db->getQueryBuilder();
		$qb->select('*')->from($this->getTableName())->orderBy('label', 'ASC');
		return $this->findEntities($qb);
	}
}
