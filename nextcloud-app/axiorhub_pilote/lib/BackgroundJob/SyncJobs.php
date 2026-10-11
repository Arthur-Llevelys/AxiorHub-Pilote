<?php

declare(strict_types=1);

namespace OCA\AxiorhubPilote\BackgroundJob;

use OCA\AxiorhubPilote\Db\JobMapper;
use OCA\AxiorhubPilote\Service\EngineClient;
use OCA\AxiorhubPilote\Service\MatterService;
use OCP\AppFramework\Utility\ITimeFactory;
use OCP\BackgroundJob\TimedJob;

/**
 * Suivi borné des travaux encore actifs (au plus 50 par passage, toutes les 5 minutes) : la génération elle-même reste dans les
 * workers AxiorHub. Le périmètre de l'utilisateur est recalculé : un utilisateur sans droits ne reçoit plus d'état.
 */
class SyncJobs extends TimedJob {
	public function __construct(
		ITimeFactory $time,
		private JobMapper $jobs,
		private EngineClient $engine,
		private MatterService $matters,
	) {
		parent::__construct($time);
		$this->setInterval(300);
	}

	protected function run($argument): void {
		// 0.2.0 : intitulés et nouveaux dossiers du moteur importés à chaque passage (rattachements de l'administrateur conservés)
		$this->matters->syncFromEngine();
		foreach ($this->jobs->active(50) as $job) {
			if ($job->getMatterRef() !== '' && !$this->matters->allowed($job->getUserId(), $job->getMatterRef())) {
				$job->setState('acces_retire');
			} else {
				$out = $this->engine->call('GET', '/integration/v1/jobs/' . $job->getEngineJobId(), $job->getUserId(), $this->matters->scope($job->getUserId()));
				if ($out['status'] !== 200 || !isset($out['body']['state'])) {
					continue;
				}
				$job->setState((string)$out['body']['state']);
			}
			$job->setUpdatedAt(time());
			$this->jobs->update($job);
		}
	}
}
