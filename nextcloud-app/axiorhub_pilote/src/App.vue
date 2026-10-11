<template>
	<NcContent app-name="axiorhub_pilote">
		<NcAppContent>
			<div class="axh">
				<h2>{{ t('axiorhub_pilote', 'AxiorHub Pilote') }}</h2>
				<NcNoteCard v-if="!config.engineConfigured" type="warning">
					{{ t('axiorhub_pilote', 'The AxiorHub engine is not configured yet. An administrator sets its address in Administration settings › Additional settings.') }}
				</NcNoteCard>
				<template v-else>
					<NcNoteCard v-if="health && !health.reachable" type="error">
						{{ t('axiorhub_pilote', 'The AxiorHub engine cannot be reached from this Nextcloud server. Work in progress continues on the engine.') }}
					</NcNoteCard>
					<label class="axh-label" for="axh-q">{{ t('axiorhub_pilote', 'Case') }}</label>
					<NcTextField id="axh-q" v-model="query" :label="t('axiorhub_pilote', 'Search by name or reference')" @update:model-value="loadMatters" />
					<ul class="axh-list" role="listbox">
						<li v-for="m in matters" :key="m.matter">
							<NcButton :variant="m.matter === matter ? 'primary' : 'secondary'" @click="matter = m.matter">
								{{ m.label || m.matter }}
							</NcButton>
						</li>
						<li v-if="!matters.length" class="axh-muted">{{ emptyText }}</li>
					</ul>
					<section v-if="matter" class="axh-actions">
						<NcButton variant="primary" :disabled="busy" @click="start('fiche_dossier')">
							{{ t('axiorhub_pilote', 'Prepare the case sheet') }}
						</NcButton>
						<NcTextArea v-model="question" :label="t('axiorhub_pilote', 'Legal question (anonymised before any sending)')" />
						<NcButton :disabled="busy || question.trim().length < 3" @click="start('recherche_juridique')">
							{{ t('axiorhub_pilote', 'Search') }}
						</NcButton>
					</section>
					<p class="axh-status" role="status" aria-live="polite">{{ message }}</p>
					<section v-if="job" class="axh-job">
						<h3>{{ t('axiorhub_pilote', 'Work in progress') }}</h3>
						<p>{{ stateLabel }}<span v-if="job.stage"> — {{ job.stage }}</span></p>
						<NcButton v-if="['pending','running'].includes(job.state)" @click="cancel">{{ t('axiorhub_pilote', 'Cancel') }}</NcButton>
						<NcButton v-if="interrupted" @click="follow(job.id)">{{ t('axiorhub_pilote', 'Resume tracking') }}</NcButton>
						<div v-if="result" class="axh-result">
							<p><strong>{{ t('axiorhub_pilote', 'Result version {version}', { version: result.version }) }}</strong> — {{ t('axiorhub_pilote', 'complete content below ({count} characters): read it entirely before deciding.', { count: fullText.length }) }}</p>
							<template v-if="sections.length">
								<details v-for="s in sections" :key="s.key" open class="axh-section">
									<summary>{{ s.key }}</summary>
									<pre class="axh-pre">{{ s.text }}</pre>
								</details>
							</template>
							<pre v-else class="axh-pre">{{ fullText }}</pre>
							<p v-if="decision" class="axh-decision">{{ decisionLabel }}</p>
							<details v-if="history.length" class="axh-section">
								<summary>{{ t('axiorhub_pilote', 'Decision history ({count})', { count: history.length }) }}</summary>
								<ul>
									<li v-for="(h, i) in history" :key="i">
										{{ t('axiorhub_pilote', '{decision} by {user} on {date} (version {version})', { decision: decisionName(h.decision), user: h.user, date: when(h.at), version: h.version }) }}
										<span v-if="h.comment"> — {{ h.comment }}</span>
									</li>
								</ul>
							</details>
							<NcTextArea v-model="comment" :label="t('axiorhub_pilote', 'Correction requested (required to request a correction)')" />
							<div class="axh-buttons">
								<NcButton variant="primary" :disabled="deciding" @click="decide('approuve')">{{ t('axiorhub_pilote', 'Approve this version') }}</NcButton>
								<NcButton :disabled="deciding" @click="decide('refuse')">{{ t('axiorhub_pilote', 'Refuse this version') }}</NcButton>
								<NcButton :disabled="deciding || comment.trim().length < 3" @click="decide('correction_demandee')">{{ t('axiorhub_pilote', 'Request a correction') }}</NcButton>
							</div>
						</div>
					</section>
					<section v-if="recent.length" class="axh-recent">
						<h3>{{ t('axiorhub_pilote', 'Recent work') }}</h3>
						<ul class="axh-plain">
							<li v-for="j in recent" :key="j.id">
								<NcButton :variant="job?.id === j.id ? 'primary' : 'tertiary'" @click="reopen(j)">
									{{ operationName(j.operation) }} · {{ matterName(j.matter) }} · {{ when(j.created_at * 1000) }}
								</NcButton>
							</li>
						</ul>
					</section>
				</template>
			</div>
		</NcAppContent>
	</NcContent>
</template>

<script setup>
// 0.2.0 (audit F16, F17) : résultat entièrement consultable avant décision (approuver, refuser, demander une correction) ; suivi rattaché
// à l'identifiant capturé au lancement : une réponse tardive d'un ancien travail ne modifie jamais l'écran d'un autre travail.
import { computed, onBeforeUnmount, onMounted, ref } from 'vue'
import axios from '@nextcloud/axios'
import { loadState } from '@nextcloud/initial-state'
import { t } from '@nextcloud/l10n'
import { generateUrl } from '@nextcloud/router'
import NcAppContent from '@nextcloud/vue/components/NcAppContent'
import NcButton from '@nextcloud/vue/components/NcButton'
import NcContent from '@nextcloud/vue/components/NcContent'
import NcNoteCard from '@nextcloud/vue/components/NcNoteCard'
import NcTextArea from '@nextcloud/vue/components/NcTextArea'
import NcTextField from '@nextcloud/vue/components/NcTextField'

const LIMIT_MS = 12 * 60 * 1000
const config = loadState('axiorhub_pilote', 'config', { engineConfigured: false, isAdmin: false })
const url = (path) => generateUrl('/apps/axiorhub_pilote/api' + path)
const health = ref(null)
const query = ref('')
const matters = ref([])
const syncInfo = ref(null)
const matter = ref('')
const question = ref('')
const busy = ref(false)
const deciding = ref(false)
const message = ref('')
const job = ref(null)
const result = ref(null)
const decision = ref(null)
const comment = ref('')
const interrupted = ref(false)
const recent = ref([])
let timer = null
let generation = 0
let controller = null

const stateLabel = computed(() => ({
	pending: t('axiorhub_pilote', 'Waiting in queue'),
	running: t('axiorhub_pilote', 'In progress'),
	succeeded: t('axiorhub_pilote', 'Finished'),
	failed: t('axiorhub_pilote', 'Failed'),
	cancelled: t('axiorhub_pilote', 'Cancelled'),
	cancel_requested: t('axiorhub_pilote', 'Stop requested'),
	already_finished: t('axiorhub_pilote', 'Already finished'),
}[job.value?.state] || job.value?.state || ''))
const emptyText = computed(() => {
	if (config.isAdmin && syncInfo.value && !syncInfo.value.ok) {
		return t('axiorhub_pilote', 'No case imported from the engine ({code}). Check the connection in Administration settings › AxiorHub.', { code: syncInfo.value.code || 'erreur' })
	}
	return config.isAdmin
		? t('axiorhub_pilote', 'No case found. Import the cases of the engine in Administration settings › AxiorHub.')
		: t('axiorhub_pilote', 'No case is available to you. Ask an administrator to give your group access to your cases.')
})
const content = computed(() => result.value?.content ?? {})
const fullText = computed(() => JSON.stringify(content.value, null, 1))
const sections = computed(() => {
	const data = content.value?.data
	if (!data || typeof data !== 'object' || Array.isArray(data)) {
		return []
	}
	const out = Object.entries(data).map(([key, value]) => ({ key, text: typeof value === 'string' ? value : JSON.stringify(value, null, 1) }))
	for (const key of ['sources', 'corrections']) {
		if (content.value[key] !== undefined) {
			out.push({ key, text: JSON.stringify(content.value[key], null, 1) })
		}
	}
	return out
})
const history = computed(() => (Array.isArray(result.value?.decisions) ? result.value.decisions : []))
const decisionName = (d) => ({
	approuve: t('axiorhub_pilote', 'Approved'),
	refuse: t('axiorhub_pilote', 'Refused'),
	correction_demandee: t('axiorhub_pilote', 'Correction requested'),
}[d] || d)
const operationName = (op) => ({
	fiche_dossier: t('axiorhub_pilote', 'Case sheet'),
	recherche_juridique: t('axiorhub_pilote', 'Legal search'),
}[op] || op)
const matterName = (reference) => matters.value.find((m) => m.matter === reference)?.label || reference
const when = (value) => {
	const d = new Date(value)
	return Number.isNaN(d.getTime()) ? '' : d.toLocaleString()
}
const decisionLabel = computed(() => ({
	approuve: t('axiorhub_pilote', 'Approved: version {version} only.', { version: decision.value?.version }),
	refuse: t('axiorhub_pilote', 'Refused: version {version}.', { version: decision.value?.version }),
	correction_demandee: t('axiorhub_pilote', 'Correction requested on version {version}.', { version: decision.value?.version }),
}[decision.value?.decision] || ''))

function key() {
	const bytes = new Uint8Array(16)
	crypto.getRandomValues(bytes)
	return Array.from(bytes, (b) => b.toString(16).padStart(2, '0')).join('')
}

async function loadRecent() {
	try {
		recent.value = (await axios.get(url('/jobs'))).data.jobs || []
	} catch (error) {
		recent.value = []
	}
}

// 0.2.0 : réouverture d'un travail ancien — son résultat figé et l'historique des décisions sont relus auprès du moteur.
function reopen(j) {
	stopTracking()
	job.value = { id: j.id, state: j.state }
	result.value = null
	decision.value = null
	comment.value = ''
	message.value = ''
	follow(j.id)
}
async function loadMatters() {
	const { data } = await axios.get(url('/matters'), { params: { q: query.value } })
	matters.value = data.matters
	if (data.sync) {
		syncInfo.value = data.sync
	}
}

function stopTracking() {
	generation += 1
	clearTimeout(timer)
	if (controller) {
		controller.abort()
		controller = null
	}
}

async function start(operation) {
	busy.value = true
	message.value = ''
	try {
		const { data } = await axios.post(url('/jobs'), { operation, matter: matter.value, question: question.value, idempotencyKey: key() })
		stopTracking()
		job.value = { id: data.job_id, state: data.state === 'existing' ? 'running' : 'pending' }
		result.value = null
		decision.value = null
		comment.value = ''
		follow(data.job_id)
		loadRecent()
	} catch (error) {
		message.value = error?.response?.data?.message || t('axiorhub_pilote', 'The request was refused.')
	} finally {
		busy.value = false
	}
}

function follow(jobId) {
	stopTracking()
	const mine = generation
	controller = new AbortController()
	const signal = controller.signal
	const current = () => mine === generation && job.value?.id === jobId
	interrupted.value = false
	const started = Date.now()
	let delay = 5000
	let failures = 0
	const tick = async () => {
		try {
			const { data } = await axios.get(url('/jobs/' + jobId), { signal })
			if (!current()) {
				return
			}
			failures = 0
			delay = 5000
			job.value = { ...data, id: jobId }
			if (data.state === 'succeeded') {
				const res = (await axios.get(url('/results/' + jobId), { signal })).data
				if (current()) {
					result.value = res
				}
				return
			}
			if (['failed', 'cancelled', 'already_finished'].includes(data.state)) {
				message.value = data.errors?.[0]?.message || ''
				return
			}
		} catch (error) {
			if (!current() || axios.isCancel?.(error) || error?.name === 'CanceledError') {
				return
			}
			failures += 1
			delay = Math.min(60000, delay * 2)
			if (error?.response?.status === 401 || failures > 5) {
				interrupted.value = true
				message.value = t('axiorhub_pilote', 'Tracking interrupted; the work may continue on the engine.')
				return
			}
		}
		if (Date.now() - started > LIMIT_MS) {
			interrupted.value = true
			message.value = t('axiorhub_pilote', 'Tracking interrupted; the work may continue on the engine.')
			return
		}
		if (current()) {
			timer = setTimeout(tick, delay)
		}
	}
	tick()
}

async function cancel() {
	const jobId = job.value.id
	const { data } = await axios.post(url('/jobs/' + jobId + '/cancel'))
	message.value = data.message || ''
	if (job.value?.id === jobId) {
		follow(jobId)
	}
}

async function decide(choice) {
	const jobId = job.value.id
	const version = result.value.version
	deciding.value = true
	try {
		const { data } = await axios.post(url('/results/' + jobId + '/approvals'), { version, decision: choice, comment: comment.value })
		if (job.value?.id === jobId) {
			decision.value = data
			message.value = t('axiorhub_pilote', 'Decision recorded for this version only.')
			axios.get(url('/results/' + jobId)).then(({ data: res }) => {   // historique à jour ; un échec ici n'annule pas la décision
				if (job.value?.id === jobId) {
					result.value = res
				}
			}).catch(() => {})
		}
	} catch (error) {
		message.value = error?.response?.data?.message || t('axiorhub_pilote', 'The result changed since you read it: reload it before approving.')
	} finally {
		deciding.value = false
	}
}

onMounted(async () => {
	if (!config.engineConfigured) {
		return
	}
	health.value = (await axios.get(url('/health'))).data
	await loadMatters()
	await loadRecent()
})

onBeforeUnmount(stopTracking)
</script>

<style scoped>
.axh { padding: 16px; max-width: 980px; }
.axh-list { list-style: none; padding: 0; display: flex; flex-wrap: wrap; gap: 6px; margin: 8px 0; }
.axh-actions { display: grid; gap: 8px; margin: 12px 0; }
.axh-muted { color: var(--color-text-maxcontrast); }
.axh-pre { white-space: pre-wrap; overflow-wrap: anywhere; }
.axh-section { margin: 6px 0; }
.axh-buttons { display: flex; flex-wrap: wrap; gap: 8px; margin-top: 8px; }
.axh-decision { font-weight: bold; }
.axh-label { font-weight: bold; }
.axh-recent { margin-top: 16px; }
.axh-plain { list-style: none; padding: 0; display: grid; gap: 4px; }
</style>
