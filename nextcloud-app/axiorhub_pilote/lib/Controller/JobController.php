<?php

declare(strict_types=1);

namespace OCA\AxiorhubPilote\Controller;

use OCA\AxiorhubPilote\AppInfo\Application;
use OCA\AxiorhubPilote\Db\Job;
use OCA\AxiorhubPilote\Db\JobMapper;
use OCA\AxiorhubPilote\Service\EngineClient;
use OCA\AxiorhubPilote\Service\FileService;
use OCA\AxiorhubPilote\Service\MatterService;
use OCP\AppFramework\Controller;
use OCP\AppFramework\Db\DoesNotExistException;
use OCP\AppFramework\Http;
use OCP\AppFramework\Http\Attribute\NoAdminRequired;
use OCP\AppFramework\Http\DataResponse;
use OCP\DB\Exception as DbException;
use OCP\IRequest;
use OCP\IUserSession;

/**
 * Travaux : PHP envoie une commande courte au moteur et rend aussitôt un état ; les générations longues s'exécutent dans les workers
 * AxiorHub. Les droits sont revérifiés à chaque appel (dossier, travail, résultat) ; protection CSRF conservée sur les mutations.
 */
class JobController extends Controller {
	public function __construct(
		IRequest $request,
		private EngineClient $engine,
		private MatterService $matters,
		private FileService $files,
		private JobMapper $jobs,
		private IUserSession $session,
	) {
		parent::__construct(Application::APP_ID, $request);
	}

	private function uid(): string {
		return $this->session->getUser()?->getUID() ?? '';
	}

	private function forward(array $out, int $okStatus = Http::STATUS_OK): DataResponse {
		return new DataResponse($out['body'], $out['status'] < 400 ? $okStatus : $out['status']);
	}

	#[NoAdminRequired]
	public function create(string $operation, string $matter = '', string $question = '', array $fileIds = [], string $idempotencyKey = ''): DataResponse {
		$uid = $this->uid();
		if ($matter !== '' && !$this->matters->allowed($uid, $matter)) {
			return new DataResponse(['code' => 'dossier_hors_perimetre'], Http::STATUS_FORBIDDEN);
		}
		if (!preg_match('/^[A-Za-z0-9_-]{8,80}$/', $idempotencyKey)) {
			return new DataResponse(['code' => 'cle_idempotence_requise'], Http::STATUS_BAD_REQUEST);
		}
		$scope = $this->matters->scope($uid);
		$manifestId = '';
		if ($fileIds !== [] && $matter !== '') {
			$manifest = $this->files->manifest($uid, array_map('intval', $fileIds));
			$m = $this->engine->call('POST', '/integration/v1/source-manifests', $uid, $scope, ['matter' => $matter, 'sources' => $manifest['sources']]);
			if ($m['status'] >= 400) {
				return $this->forward($m);
			}
			$manifestId = (string)($m['body']['manifest_id'] ?? '');
		}
		$out = $this->engine->call('POST', '/integration/v1/jobs', $uid, $scope, [
			'operation' => $operation, 'matter' => $matter, 'question' => $question, 'manifest_id' => $manifestId, 'idempotency_key' => $idempotencyKey,
		]);
		if ($out['status'] < 400 && isset($out['body']['job_id'])) {
			$job = new Job();
			$job->setUserId($uid);
			$job->setEngineJobId((string)$out['body']['job_id']);
			$job->setOperation($operation);
			$job->setMatterRef($matter);
			$job->setState('pending');
			$job->setIdempotencyKey($idempotencyKey);
			$job->setCreatedAt(time());
			$job->setUpdatedAt(time());
			try {
				$this->jobs->insert($job);
			} catch (DbException $e) {
				// même clé d'idempotence : le travail existe déjà pour cet utilisateur (double clic, deux onglets)
				if ($e->getReason() !== DbException::REASON_UNIQUE_CONSTRAINT_VIOLATION) {
					throw $e;
				}
			}
		}
		return $this->forward($out, Http::STATUS_ACCEPTED);
	}

	private function own(string $id): ?Job {
		try {
			return $this->jobs->findForUser($this->uid(), $id);
		} catch (DoesNotExistException) {
			return null;
		}
	}

	/** 0.2.0 : travaux récents de l'utilisateur (réouverture d'un résultat ancien) ; aucune donnée d'un autre utilisateur. */
	#[NoAdminRequired]
	public function index(): DataResponse {
		return new DataResponse(['jobs' => $this->jobs->recentForUser($this->uid(), 30)]);
	}

	#[NoAdminRequired]
	public function show(string $id): DataResponse {
		$job = $this->own($id);
		if ($job === null) {
			return new DataResponse(['code' => 'travail_absent'], Http::STATUS_NOT_FOUND);
		}
		$out = $this->engine->call('GET', '/integration/v1/jobs/' . $id, $this->uid(), $this->matters->scope($this->uid()));
		if ($out['status'] === 200 && isset($out['body']['state'])) {
			$job->setState((string)$out['body']['state']);
			$job->setUpdatedAt(time());
			$this->jobs->update($job);
		}
		return $this->forward($out);
	}

	#[NoAdminRequired]
	public function cancel(string $id): DataResponse {
		if ($this->own($id) === null) {
			return new DataResponse(['code' => 'travail_absent'], Http::STATUS_NOT_FOUND);
		}
		return $this->forward($this->engine->call('POST', '/integration/v1/jobs/' . $id . '/cancel', $this->uid(), $this->matters->scope($this->uid()), []));
	}

	#[NoAdminRequired]
	public function result(string $id): DataResponse {
		if ($this->own($id) === null) {
			return new DataResponse(['code' => 'travail_absent'], Http::STATUS_NOT_FOUND);
		}
		return $this->forward($this->engine->call('GET', '/integration/v1/results/' . $id, $this->uid(), $this->matters->scope($this->uid())));
	}

	#[NoAdminRequired]
	public function approve(string $id, string $version, string $decision, string $comment = ''): DataResponse {
		if ($this->own($id) === null) {
			return new DataResponse(['code' => 'travail_absent'], Http::STATUS_NOT_FOUND);
		}
		if (!in_array($decision, ['approuve', 'refuse', 'correction_demandee'], true) || mb_strlen($comment) > 5000) {
			return new DataResponse(['code' => 'decision_invalide'], Http::STATUS_BAD_REQUEST);
		}
		return $this->forward($this->engine->call('POST', '/integration/v1/results/' . $id . '/approvals', $this->uid(), $this->matters->scope($this->uid()),
			['version' => $version, 'decision' => $decision, 'comment' => $comment]));
	}
}
