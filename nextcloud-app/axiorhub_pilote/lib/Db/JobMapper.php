<?php

declare(strict_types=1);

namespace OCA\AxiorhubPilote\Db;

use OCP\AppFramework\Db\DoesNotExistException;
use OCP\AppFramework\Db\QBMapper;
use OCP\DB\QueryBuilder\IQueryBuilder;
use OCP\IDBConnection;

/** @template-extends QBMapper<Job> */
class JobMapper extends QBMapper {
	public function __construct(IDBConnection $db) {
		parent::__construct($db, 'axh_jobs', Job::class);
	}

	/** @throws DoesNotExistException */
	public function findForUser(string $userId, string $engineJobId): Job {
		$qb = $this->db->getQueryBuilder();
		$qb->select('*')->from($this->getTableName())
			->where($qb->expr()->eq('user_id', $qb->createNamedParameter($userId)))
			->andWhere($qb->expr()->eq('engine_job_id', $qb->createNamedParameter($engineJobId)));
		return $this->findEntity($qb);
	}

	/** @return Job[] */
	public function recentForUser(string $userId, int $limit = 30): array {
		$qb = $this->db->getQueryBuilder();
		$qb->select('*')->from($this->getTableName())
			->where($qb->expr()->eq('user_id', $qb->createNamedParameter($userId)))
			->orderBy('created_at', 'DESC')->setMaxResults($limit);
		return $this->findEntities($qb);
	}

	/** @return Job[] travaux encore actifs (suivi borné en tâche de fond) */
	public function active(int $limit = 50): array {
		$qb = $this->db->getQueryBuilder();
		$qb->select('*')->from($this->getTableName())
			->where($qb->expr()->in('state', $qb->createNamedParameter(['pending', 'running', 'cancel_requested'], IQueryBuilder::PARAM_STR_ARRAY)))
			->orderBy('updated_at', 'ASC')->setMaxResults($limit);
		return $this->findEntities($qb);
	}
}
