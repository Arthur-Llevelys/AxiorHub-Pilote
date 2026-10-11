<?php

declare(strict_types=1);

namespace OCA\AxiorhubPilote\Migration;

use Closure;
use OCP\DB\ISchemaWrapper;
use OCP\DB\Types;
use OCP\Migration\IOutput;
use OCP\Migration\SimpleMigrationStep;

/**
 * Métadonnées propres à l'application (aucune donnée de pièce) : rattachements de dossiers, travaux distants, résultats et versions de
 * sources utilisées. Les pièces restent dans les fichiers Nextcloud ; les identifiants du moteur sont de simples références.
 */
class Version000100Date20261011 extends SimpleMigrationStep {
	public function changeSchema(IOutput $output, Closure $schemaClosure, array $options): ?ISchemaWrapper {
		/** @var ISchemaWrapper $schema */
		$schema = $schemaClosure();
		if (!$schema->hasTable('axh_matters')) {
			$t = $schema->createTable('axh_matters');
			$t->addColumn('id', Types::BIGINT, ['autoincrement' => true, 'notnull' => true]);
			$t->addColumn('matter_ref', Types::STRING, ['notnull' => true, 'length' => 80]);
			$t->addColumn('label', Types::STRING, ['notnull' => true, 'length' => 255, 'default' => '']);
			$t->addColumn('group_id', Types::STRING, ['notnull' => true, 'length' => 64]);
			$t->addColumn('root_file_id', Types::BIGINT, ['notnull' => true, 'default' => 0]);
			$t->addColumn('created_at', Types::BIGINT, ['notnull' => true, 'default' => 0]);
			$t->setPrimaryKey(['id']);
			$t->addUniqueIndex(['matter_ref'], 'axh_matters_ref');
		}
		if (!$schema->hasTable('axh_jobs')) {
			$t = $schema->createTable('axh_jobs');
			$t->addColumn('id', Types::BIGINT, ['autoincrement' => true, 'notnull' => true]);
			$t->addColumn('user_id', Types::STRING, ['notnull' => true, 'length' => 64]);
			$t->addColumn('engine_job_id', Types::STRING, ['notnull' => true, 'length' => 32]);
			$t->addColumn('operation', Types::STRING, ['notnull' => true, 'length' => 40]);
			$t->addColumn('matter_ref', Types::STRING, ['notnull' => true, 'length' => 80, 'default' => '']);
			$t->addColumn('state', Types::STRING, ['notnull' => true, 'length' => 20, 'default' => 'pending']);
			$t->addColumn('idempotency_key', Types::STRING, ['notnull' => true, 'length' => 80]);
			$t->addColumn('created_at', Types::BIGINT, ['notnull' => true, 'default' => 0]);
			$t->addColumn('updated_at', Types::BIGINT, ['notnull' => true, 'default' => 0]);
			$t->setPrimaryKey(['id']);
			$t->addUniqueIndex(['user_id', 'idempotency_key'], 'axh_jobs_idem');
			$t->addIndex(['user_id', 'engine_job_id'], 'axh_jobs_engine');
		}
		if (!$schema->hasTable('axh_source_links')) {
			$t = $schema->createTable('axh_source_links');
			$t->addColumn('id', Types::BIGINT, ['autoincrement' => true, 'notnull' => true]);
			$t->addColumn('engine_job_id', Types::STRING, ['notnull' => true, 'length' => 32]);
			$t->addColumn('file_id', Types::BIGINT, ['notnull' => true]);
			$t->addColumn('etag', Types::STRING, ['notnull' => true, 'length' => 80, 'default' => '']);
			$t->addColumn('created_at', Types::BIGINT, ['notnull' => true, 'default' => 0]);
			$t->setPrimaryKey(['id']);
			$t->addIndex(['engine_job_id'], 'axh_links_job');
		}
		return $schema;
	}
}
