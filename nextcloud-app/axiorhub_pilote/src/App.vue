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
						<li v-if="!matters.length" class="axh-muted">{{ t('axiorhub_pilote', 'No case is available to you.') }}</li>
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
						<NcButton v-if="interrupted" @click="follow">{{ t('axiorhub_pilote', 'Resume tracking') }}</NcButton>
						<div v-if="result">
							<p>{{ t('axiorhub_pilote', 'Result version {version}', { version: result.version }) }}</p>
							<pre class="axh-pre">{{ preview }}</pre>
							<NcButton variant="primary" @click="approve('approuve')">{{ t('axiorhub_pilote', 'Approve this version') }}</NcButton>
						</div>
					</section>
				</template>
			</div>
		</NcAppContent>
	</NcContent>
</template>

<script setup>
import { computed, onMounted, ref } from 'vue'
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
const matter = ref('')
const question = ref('')
const busy = ref(false)
const message = ref('')
const job = ref(null)
const result = ref(null)
const interrupted = ref(false)
let timer = null

const stateLabel = computed(() => ({
	pending: t('axiorhub_pilote', 'Waiting in queue'),
	running: t('axiorhub_pilote', 'In progress'),
	succeeded: t('axiorhub_pilote', 'Finished'),
	failed: t('axiorhub_pilote', 'Failed'),
	cancelled: t('axiorhub_pilote', 'Cancelled'),
	cancel_requested: t('axiorhub_pilote', 'Stop requested'),
}[job.value?.state] || job.value?.state || ''))
const preview = computed(() => JSON.stringify(result.value?.content ?? {}, null, 1).slice(0, 4000))

function key() {
	const bytes = new Uint8Array(16)
	crypto.getRandomValues(bytes)
	return Array.from(bytes, (b) => b.toString(16).padStart(2, '0')).join('')
}

async function loadMatters() {
	const { data } = await axios.get(url('/matters'), { params: { q: query.value } })
	matters.value = data.matters
}

async function start(operation) {
	busy.value = true
	message.value = ''
	try {
		const { data } = await axios.post(url('/jobs'), { operation, matter: matter.value, question: question.value, idempotencyKey: key() })
		job.value = { id: data.job_id, state: data.state === 'existing' ? 'running' : 'pending' }
		result.value = null
		follow()
	} catch (error) {
		message.value = error?.response?.data?.message || t('axiorhub_pilote', 'The request was refused.')
	} finally {
		busy.value = false
	}
}

function follow() {
	interrupted.value = false
	const started = Date.now()
	let delay = 5000
	let failures = 0
	clearTimeout(timer)
	const tick = async () => {
		try {
			const { data } = await axios.get(url('/jobs/' + job.value.id))
			failures = 0
			delay = 5000
			job.value = { id: job.value.id, ...data }
			if (data.state === 'succeeded') {
				result.value = (await axios.get(url('/results/' + job.value.id))).data
				return
			}
			if (['failed', 'cancelled'].includes(data.state)) {
				message.value = data.errors?.[0]?.message || ''
				return
			}
		} catch (error) {
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
		timer = setTimeout(tick, delay)
	}
	tick()
}

async function cancel() {
	const { data } = await axios.post(url('/jobs/' + job.value.id + '/cancel'))
	message.value = data.message || ''
	follow()
}

async function approve(decision) {
	try {
		await axios.post(url('/results/' + job.value.id + '/approvals'), { version: result.value.version, decision })
		message.value = t('axiorhub_pilote', 'Approval recorded for this version only.')
	} catch (error) {
		message.value = error?.response?.data?.message || t('axiorhub_pilote', 'The result changed since you read it: reload it before approving.')
	}
}

onMounted(async () => {
	if (!config.engineConfigured) {
		return
	}
	health.value = (await axios.get(url('/health'))).data
	await loadMatters()
})
</script>

<style scoped>
.axh { padding: 16px; max-width: 900px; }
.axh-list { list-style: none; padding: 0; display: flex; flex-wrap: wrap; gap: 6px; margin: 8px 0; }
.axh-actions { display: grid; gap: 8px; margin: 12px 0; }
.axh-muted { color: var(--color-text-maxcontrast); }
.axh-pre { white-space: pre-wrap; overflow-wrap: anywhere; max-height: 360px; overflow: auto; }
.axh-label { font-weight: bold; }
</style>
