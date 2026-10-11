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

	/** @throws \OCP\AppFramework\Db\DoesNotExistException */
	public function findByRef(string $matterRef): Matter {
		$qb = $this->db->getQueryBuilder();
		$qb->select('*')->from($this->getTableName())->where($qb->expr()->eq('matter_ref', $qb->createNamedParameter($matterRef)));
		return $this->findEntity($qb);
	}

	public function countAll(): int {
		$qb = $this->db->getQueryBuilder();
		$qb->select($qb->func()->count('*', 'n'))->from($this->getTableName());
		$res = $qb->executeQuery();
		$n = (int)$res->fetchOne();
		$res->closeCursor();
		return $n;
	}

	/** @return Matter[] */
	public function findAll(): array {
		$qb = $this->db->getQueryBuilder();
		$qb->select('*')->from($this->getTableName())->orderBy('label', 'ASC');
		return $this->findEntities($qb);
	}
}
