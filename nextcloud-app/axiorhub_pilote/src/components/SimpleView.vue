<template>
	<div class="axh">
		<header class="axh-head">
			<h2>{{ t('axiorhub_pilote', 'AxiorHub Pilote') }}</h2>
			<p v-if="me" class="axh-muted">{{ t('axiorhub_pilote', 'Linked to {user} · AxiorHub {version}', { user: me.user || '—', version: me.version || '' }) }}</p>
		</header>

		<NcNoteCard v-if="!config.linked && !config.engineConfigured" type="info">
			<p>{{ t('axiorhub_pilote', 'Link your AxiorHub account to use AxiorHub Pilote in Nextcloud.') }}</p>
			<p><a class="axh-link" :href="config.personalUrl">{{ t('axiorhub_pilote', 'Open Personal settings › AxiorHub Pilote') }}</a></p>
		</NcNoteCard>
		<template v-else>
			<NcNoteCard v-if="health && !health.reachable" type="warning">
				<p>{{ healthText }}</p>
				<p v-if="config.linked"><a class="axh-link" :href="config.personalUrl">{{ t('axiorhub_pilote', 'Check the connection in Personal settings › AxiorHub Pilote') }}</a></p>
				<NcButton @click="checkHealth">{{ t('axiorhub_pilote', 'Try again') }}</NcButton>
			</NcNoteCard>

			<section class="axh-case">
				<NcSelect v-model="selected"
					:options="matters"
					label="label"
					:filterable="false"
					:loading="loadingMatters"
					:input-label="t('axiorhub_pilote', 'Case (optional for the Pilot and the searches)')"
					:placeholder="t('axiorhub_pilote', 'Search a case by name or reference')"
					@search="onSearch" />
				<p v-if="!matters.length && !loadingMatters" class="axh-muted">{{ emptyText }}</p>
			</section>

			<nav class="axh-tabs" role="tablist">
				<NcButton v-for="tab in tabs"
					:key="tab.id"
					role="tab"
					:aria-selected="active === tab.id ? 'true' : 'false'"
					:variant="active === tab.id ? 'primary' : 'secondary'"
					@click="active = tab.id">
					{{ tab.label }}
				</NcButton>
			</nav>

			<section v-if="active === 'pilote'" class="axh-panel">
				<NcTextArea v-model="instruction" :label="t('axiorhub_pilote', 'Instruction to the Pilot')" :placeholder="t('axiorhub_pilote', 'Describe the expected result… e.g. “Summarise the last letter of the other side and list the deadlines”')" rows="5" />
				<label class="axh-label" for="axh-intent">{{ t('axiorhub_pilote', 'Expected result') }}</label>
				<select id="axh-intent" v-model="intent" class="axh-select">
					<option value="auto">{{ t('axiorhub_pilote', 'Let the Pilot decide') }}</option>
					<option value="question">{{ t('axiorhub_pilote', 'Answer') }}</option>
					<option value="analysis">{{ t('axiorhub_pilote', 'Analysis') }}</option>
					<option value="document">{{ t('axiorhub_pilote', 'Word draft in the case folder') }}</option>
					<option value="mail">{{ t('axiorhub_pilote', 'Email draft in Drafts (never sent)') }}</option>
				</select>
				<NcButton variant="primary" :disabled="busy || instruction.trim().length < 3" @click="start('instruction', instruction)">
					{{ t('axiorhub_pilote', 'Send the instruction') }}
				</NcButton>
			</section>

			<section v-if="active === 'recherche'" class="axh-panel">
				<NcTextField v-model="searchText" :label="t('axiorhub_pilote', 'Search the cases (emails, documents, notes)')" @keyup.enter="search" />
				<NcButton variant="primary" :disabled="searching || searchText.trim().length < 2" @click="search">{{ t('axiorhub_pilote', 'Search') }}</NcButton>
				<p v-if="searchInfo" class="axh-muted">{{ searchInfo }}</p>
				<ul class="axh-results">
					<li v-for="(r, i) in searchResults" :key="i">
						<strong>{{ r.title }}</strong>
						<span class="axh-muted"> · {{ kindName(r.kind) }}<template v-if="r.date"> · {{ r.date }}</template><template v-if="r.matter"> · {{ matterName(r.matter) }}</template></span>
						<p v-if="r.excerpt">{{ r.excerpt }}</p>
					</li>
				</ul>
			</section>

			<section v-if="active === 'fiche'" class="axh-panel">
				<p v-if="!selected" class="axh-muted">{{ t('axiorhub_pilote', 'Choose a case above.') }}</p>
				<NcButton variant="primary" :disabled="busy || !selected" @click="start('fiche_dossier', '')">
					{{ t('axiorhub_pilote', 'Prepare the case sheet') }}
				</NcButton>
			</section>

			<section v-if="active === 'juridique'" class="axh-panel">
				<NcTextArea v-model="question" :label="t('axiorhub_pilote', 'Legal question (anonymised before any sending)')" />
				<NcButton variant="primary" :disabled="busy || question.trim().length < 3" @click="start('recherche_juridique', question)">
					{{ t('axiorhub_pilote', 'Search') }}
				</NcButton>
			</section>

			<section v-if="active === 'travaux'" class="axh-panel">
				<p v-if="!recent.length" class="axh-muted">{{ t('axiorhub_pilote', 'No work yet.') }}</p>
				<ul class="axh-plain">
					<li v-for="j in recent" :key="j.id">
						<NcButton :variant="job?.id === j.id ? 'primary' : 'tertiary'" @click="reopen(j)">
							{{ operationName(j.operation) }} · {{ matterName(j.matter) || t('axiorhub_pilote', 'Whole firm') }} · {{ when(j.created_at * 1000) }}
						</NcButton>
					</li>
				</ul>
			</section>

			<p class="axh-status" role="status" aria-live="polite">{{ message }}</p>

			<section v-if="job" class="axh-job">
				<h3>{{ operationName(job.operation || current) }} — {{ stateLabel }}</h3>
				<p v-if="job.stage" class="axh-muted">{{ job.stage }}</p>
				<NcButton v-if="['pending','running'].includes(job.state)" @click="cancel">{{ t('axiorhub_pilote', 'Cancel') }}</NcButton>
				<NcButton v-if="interrupted" @click="follow(job.id)">{{ t('axiorhub_pilote', 'Resume tracking') }}</NcButton>
				<div v-if="result" class="axh-result">
					<template v-if="content.reponse !== undefined">
						<pre class="axh-pre">{{ content.reponse || t('axiorhub_pilote', 'No text answer: see the deliverable.') }}</pre>
						<p v-if="content.livrable"><strong>{{ t('axiorhub_pilote', 'Deliverable:') }}</strong> {{ content.livrable }}</p>
						<details v-if="content.sources && content.sources.length" class="axh-section">
							<summary>{{ t('axiorhub_pilote', 'Sources ({count})', { count: content.sources.length }) }}</summary>
							<ul><li v-for="(s, i) in content.sources" :key="i">{{ sourceText(s) }}</li></ul>
						</details>
					</template>
					<template v-else>
						<p><strong>{{ t('axiorhub_pilote', 'Result version {version}', { version: result.version }) }}</strong> — {{ t('axiorhub_pilote', 'complete content below ({count} characters): read it entirely before deciding.', { count: fullText.length }) }}</p>
						<template v-if="sections.length">
							<details v-for="s in sections" :key="s.key" open class="axh-section">
								<summary>{{ s.key }}</summary>
								<pre class="axh-pre">{{ s.text }}</pre>
							</details>
						</template>
						<pre v-else class="axh-pre">{{ fullText }}</pre>
					</template>
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
		</template>
	</div>
</template>

<script setup>
// 0.4.0 : « Vue simplifiée » (écran de la 0.3.0), à côté de l'interface AxiorHub affichée dans Nextcloud.
// 0.3.0 : interface complète — instruction au Pilote, recherche dans les dossiers, fiche du dossier, recherche juridique, travaux.
// Compte relié dans les paramètres personnels (clé personnelle) ; suivi rattaché au travail lancé (une réponse tardive d'un ancien
// travail ne modifie jamais l'écran d'un autre travail) ; résultat entièrement lisible avant décision.
import { computed, onBeforeUnmount, onMounted, ref, watch } from 'vue'
import axios from '@nextcloud/axios'
import { loadState } from '@nextcloud/initial-state'
import { t } from '@nextcloud/l10n'
import { generateUrl } from '@nextcloud/router'
import NcButton from '@nextcloud/vue/components/NcButton'
import NcNoteCard from '@nextcloud/vue/components/NcNoteCard'
import NcSelect from '@nextcloud/vue/components/NcSelect'
import NcTextArea from '@nextcloud/vue/components/NcTextArea'
import NcTextField from '@nextcloud/vue/components/NcTextField'

const LIMIT_MS = 15 * 60 * 1000
const config = loadState('axiorhub_pilote', 'config', { engineConfigured: false, linked: false, personalUrl: '', isAdmin: false })
const url = (path) => generateUrl('/apps/axiorhub_pilote/api' + path)
const tabs = [
	{ id: 'pilote', label: t('axiorhub_pilote', 'Pilot') },
	{ id: 'recherche', label: t('axiorhub_pilote', 'Search the cases') },
	{ id: 'fiche', label: t('axiorhub_pilote', 'Case sheet') },
	{ id: 'juridique', label: t('axiorhub_pilote', 'Legal search') },
	{ id: 'travaux', label: t('axiorhub_pilote', 'Recent work') },
]
const active = ref('pilote')
const health = ref(null)
const me = ref(null)
const matters = ref([])
const loadingMatters = ref(false)
const syncInfo = ref(null)
const selected = ref(null)
const instruction = ref('')
const intent = ref('auto')
const question = ref('')
const searchText = ref('')
const searching = ref(false)
const searchResults = ref([])
const searchInfo = ref('')
const busy = ref(false)
const deciding = ref(false)
const message = ref('')
const job = ref(null)
const current = ref('')
const result = ref(null)
const decision = ref(null)
const comment = ref('')
const interrupted = ref(false)
const recent = ref([])
let timer = null
let generation = 0
let controller = null
let searchTimer = null

const matter = computed(() => selected.value?.matter || '')
const REASONS = {
	compte_non_relie: () => t('axiorhub_pilote', 'Link your AxiorHub account in Personal settings › AxiorHub Pilote.'),
	moteur_injoignable: () => t('axiorhub_pilote', 'AxiorHub cannot be reached from this Nextcloud server (address, firewall or certificate).'),
	moteur_occupe: () => t('axiorhub_pilote', 'AxiorHub is busy finishing another operation: try again in a few seconds.'),
	reponse_moteur_invalide: () => t('axiorhub_pilote', 'The configured address answers but is not AxiorHub Pilote: check it in Personal settings.'),
	acces_refuse_par_le_frontal: () => t('axiorhub_pilote', 'The web server in front of AxiorHub asks for a login: allow /integration/v1/ without login on that server.'),
	cle_personnelle_revoquee_ou_inconnue: () => t('axiorhub_pilote', 'Your personal key was revoked: link your account again in Personal settings.'),
	integration_desactivee: () => t('axiorhub_pilote', 'AxiorHub does not accept Nextcloud requests: ask the administrator to allow the Nextcloud app in AxiorHub.'),
	dossier_hors_perimetre: () => t('axiorhub_pilote', 'This case is not available to your account.'),
}
const reason = (data) => (REASONS[data?.code] ? REASONS[data.code]() : (data?.message || t('axiorhub_pilote', 'The request was refused.') + (data?.code ? ' (' + data.code + ')' : '')))
const healthText = computed(() => reason(health.value))

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
	return config.linked
		? t('axiorhub_pilote', 'No case found for this search.')
		: t('axiorhub_pilote', 'No case is available to you. Link your AxiorHub account in Personal settings › AxiorHub Pilote.')
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
	instruction: t('axiorhub_pilote', 'Instruction to the Pilot'),
}[op] || op || '')
const kindName = (k) => ({ courriel: t('axiorhub_pilote', 'email'), fichier: t('axiorhub_pilote', 'document'), note: t('axiorhub_pilote', 'note'), agenda: t('axiorhub_pilote', 'calendar') }[k] || k || '')
const matterName = (reference) => matters.value.find((m) => m.matter === reference)?.label || reference || ''
const sourceText = (s) => (typeof s === 'string' ? s : (s.path || s.title || s.id || JSON.stringify(s)))
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

async function checkHealth() {
	try {
		health.value = (await axios.get(url('/health'))).data
		me.value = health.value?.linked && health.value?.reachable ? health.value.engine : null
	} catch (error) {
		health.value = { reachable: false, ...(error?.response?.data || {}) }
	}
}

async function loadMatters(q = '') {
	loadingMatters.value = true
	try {
		const { data } = await axios.get(url('/matters'), { params: { q } })
		matters.value = data.matters || []
		if (data.sync) {
			syncInfo.value = data.sync
		}
		if (data.code) {
			message.value = reason(data)
		}
	} catch (error) {
		matters.value = []
		message.value = reason(error?.response?.data)
	} finally {
		loadingMatters.value = false
	}
}

function onSearch(text) {
	clearTimeout(searchTimer)
	searchTimer = setTimeout(() => loadMatters(text || ''), 300)
}

async function loadRecent() {
	try {
		recent.value = (await axios.get(url('/jobs'))).data.jobs || []
	} catch (error) {
		recent.value = []
	}
}

async function search() {
	searching.value = true
	searchInfo.value = ''
	try {
		const { data } = await axios.get(url('/search'), { params: { q: searchText.value, matter: matter.value } })
		searchResults.value = data.results || []
		searchInfo.value = t('axiorhub_pilote', '{count} result(s)', { count: searchResults.value.length }) + (data.partial ? ' · ' + t('axiorhub_pilote', 'partial (time limit)') : '')
	} catch (error) {
		searchResults.value = []
		searchInfo.value = reason(error?.response?.data)
	} finally {
		searching.value = false
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

function reopen(j) {
	stopTracking()
	job.value = { id: j.id, state: j.state, operation: j.operation }
	current.value = j.operation
	result.value = null
	decision.value = null
	comment.value = ''
	message.value = ''
	follow(j.id)
}

async function start(operation, text) {
	busy.value = true
	message.value = ''
	try {
		const { data } = await axios.post(url('/jobs'), { operation, matter: matter.value, question: text, intent: intent.value, idempotencyKey: key() })
		stopTracking()
		current.value = operation
		job.value = { id: data.job_id, state: data.state === 'existing' ? 'running' : 'pending', operation }
		result.value = null
		decision.value = null
		comment.value = ''
		follow(data.job_id)
		loadRecent()
	} catch (error) {
		message.value = reason(error?.response?.data)
	} finally {
		busy.value = false
	}
}

function follow(jobId) {
	stopTracking()
	const mine = generation
	controller = new AbortController()
	const signal = controller.signal
	const isCurrent = () => mine === generation && job.value?.id === jobId
	interrupted.value = false
	const started = Date.now()
	let delay = 4000
	let failures = 0
	const tick = async () => {
		try {
			const { data } = await axios.get(url('/jobs/' + jobId), { signal })
			if (!isCurrent()) {
				return
			}
			failures = 0
			delay = 4000
			job.value = { ...job.value, ...data, id: jobId }
			if (data.state === 'succeeded') {
				const res = (await axios.get(url('/results/' + jobId), { signal })).data
				if (isCurrent()) {
					result.value = res
				}
				return
			}
			if (['failed', 'cancelled', 'already_finished'].includes(data.state)) {
				message.value = data.errors?.[0]?.message || ''
				return
			}
		} catch (error) {
			if (!isCurrent() || axios.isCancel?.(error) || error?.name === 'CanceledError') {
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
		if (isCurrent()) {
			timer = setTimeout(tick, delay)
		}
	}
	tick()
}

async function cancel() {
	const jobId = job.value.id
	try {
		const { data } = await axios.post(url('/jobs/' + jobId + '/cancel'))
		message.value = data.message || ''
	} catch (error) {
		message.value = reason(error?.response?.data)
	}
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

watch(active, (tab) => {
	if (tab === 'travaux') {
		loadRecent()
	}
})

onMounted(async () => {
	if (!config.linked && !config.engineConfigured) {
		return
	}
	await checkHealth()
	await loadMatters()
	await loadRecent()
})

onBeforeUnmount(stopTracking)
</script>

<style scoped>
.axh { padding: 16px; max-width: 1080px; }
.axh-head { display: flex; flex-wrap: wrap; align-items: baseline; gap: 12px; }
.axh-case { margin: 12px 0; max-width: 640px; }
.axh-tabs { display: flex; flex-wrap: wrap; gap: 6px; margin: 12px 0; }
.axh-panel { display: grid; gap: 10px; margin: 12px 0; max-width: 820px; }
.axh-select { max-width: 420px; }
.axh-muted { color: var(--color-text-maxcontrast); }
.axh-link { color: var(--color-primary-element); text-decoration: underline; }
.axh-pre { white-space: pre-wrap; overflow-wrap: anywhere; }
.axh-section { margin: 6px 0; }
.axh-buttons { display: flex; flex-wrap: wrap; gap: 8px; margin-top: 8px; }
.axh-decision { font-weight: bold; }
.axh-label { font-weight: bold; }
.axh-results { list-style: none; padding: 0; display: grid; gap: 10px; }
.axh-results li { border-bottom: 1px solid var(--color-border); padding-bottom: 8px; }
.axh-plain { list-style: none; padding: 0; display: grid; gap: 4px; }
.axh-job { margin-top: 16px; border-top: 1px solid var(--color-border); padding-top: 12px; }
</style>
