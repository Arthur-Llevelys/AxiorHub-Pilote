<template>
	<NcContent app-name="axiorhub_pilote" class="axh-app">
		<NcAppNavigation class="axh-nav" :aria-label="t('axiorhub_pilote', 'AxiorHub sections')">
			<div class="axh-side">
				<div class="axh-brand">
					<img :src="logo" width="38" height="38" alt="">
					<div>
						<span class="axh-brand-name">AxiorHub</span>
						<span class="axh-brand-sub">{{ t('axiorhub_pilote', 'Pilote · AI lawyer agent') }}</span>
					</div>
				</div>
				<nav v-for="(group, g) in groups" :key="g" class="axh-side-group">
					<a v-for="s in group"
						:key="s.path"
						href="#"
						:aria-current="view === 'axiorhub' && selected === s.path ? 'page' : undefined"
						@click.prevent="go(s.path)">
						<span class="axh-side-icon" aria-hidden="true">{{ s.icon }}</span>
						<span>{{ s.label }}</span>
					</a>
				</nav>
				<nav class="axh-side-group">
					<a href="#" :title="t('axiorhub_pilote', 'Collapse the menu')" @click.prevent="collapse">
						<span class="axh-side-icon" aria-hidden="true">«</span>
						<span>{{ t('axiorhub_pilote', 'Collapse the menu') }}</span>
					</a>
					<a href="#" @click.prevent="openTab">
						<span class="axh-side-icon" aria-hidden="true">↗</span>
						<span>{{ t('axiorhub_pilote', 'Open in a new tab') }}</span>
					</a>
					<a href="#" :aria-current="view === 'simple' ? 'page' : undefined" @click.prevent="view = 'simple'">
						<span class="axh-side-icon" aria-hidden="true">☰</span>
						<span>{{ t('axiorhub_pilote', 'Simplified view') }}</span>
					</a>
					<a v-if="config.localUrl" href="#" :aria-current="view === 'local' ? 'page' : undefined" @click.prevent="view = 'local'">
						<span class="axh-side-icon" aria-hidden="true">⌂</span>
						<span>{{ t('axiorhub_pilote', 'My local AxiorHub') }}</span>
					</a>
				</nav>
				<p class="axh-side-about">
					{{ t('axiorhub_pilote', 'AxiorHub Pilote for Nextcloud {version}', { version: APP_VERSION }) }}
				</p>
			</div>
		</NcAppNavigation>
		<NcAppContent>
			<div v-if="view === 'axiorhub'" class="axh-embed">
				<NcNoteCard v-if="!config.linked" type="info" class="axh-note">
					<p>{{ t('axiorhub_pilote', 'Link your AxiorHub account to use AxiorHub Pilote in Nextcloud.') }}</p>
					<p><a class="axh-link" :href="config.personalUrl">{{ t('axiorhub_pilote', 'Open Personal settings › AxiorHub Pilote') }}</a></p>
					<p>{{ t('axiorhub_pilote', 'AxiorHub installed on this computer (discovery mode)? Enter the address of your local AxiorHub in the same settings.') }}</p>
				</NcNoteCard>
				<NcNoteCard v-if="discovery && !problem" type="warning" class="axh-note axh-discovery">
					<p>{{ t('axiorhub_pilote', 'Discovery mode: AxiorHub is installed on a workstation (Windows 11 or Ubuntu desktop). Use it only for tests and to discover the application: it can only be reached from this computer, stops when the computer is switched off, and local exchanges are not encrypted (HTTP). For your firm’s work, install the server version (Linux, HTTPS): more secure, backed up and reachable from every computer.') }}</p>
				</NcNoteCard>
				<NcNoteCard v-else-if="problem" type="warning" class="axh-note">
					<p>{{ problemText }}</p>
					<div class="axh-buttons">
						<NcButton @click="reconnect(current)">{{ t('axiorhub_pilote', 'Try again') }}</NcButton>
						<NcButton @click="openTab">{{ t('axiorhub_pilote', 'Open in a new tab') }}</NcButton>
						<NcButton variant="tertiary" @click="view = 'simple'">{{ t('axiorhub_pilote', 'Simplified view') }}</NcButton>
						<NcButton v-if="config.localUrl" variant="tertiary" @click="view = 'local'">{{ t('axiorhub_pilote', 'My local AxiorHub') }}</NcButton>
					</div>
				</NcNoteCard>
				<iframe v-if="src && !problem"
					ref="frame"
					:key="frameKey"
					:src="src"
					class="axh-frame"
					:title="t('axiorhub_pilote', 'AxiorHub interface')"
					allow="microphone; clipboard-read; clipboard-write"
					referrerpolicy="no-referrer" />
				<NcLoadingIcon v-else-if="loading" :size="44" class="axh-loading" />
			</div>
			<div v-else-if="view === 'local'" class="axh-local">
				<NcNoteCard type="warning" class="axh-note">
					<p><strong>{{ t('axiorhub_pilote', 'My local AxiorHub (discovery mode)') }}</strong></p>
					<p>{{ t('axiorhub_pilote', 'Discovery mode: AxiorHub is installed on a workstation (Windows 11 or Ubuntu desktop). Use it only for tests and to discover the application: it can only be reached from this computer, stops when the computer is switched off, and local exchanges are not encrypted (HTTP). For your firm’s work, install the server version (Linux, HTTPS): more secure, backed up and reachable from every computer.') }}</p>
					<p>{{ t('axiorhub_pilote', 'AxiorHub opens in a new tab of this browser, on this computer only. Nextcloud does not exchange any data with it: no Pilote, no search and no display inside Nextcloud in this mode.') }}</p>
					<div class="axh-buttons">
						<NcButton variant="primary" @click="openLocal">{{ t('axiorhub_pilote', 'Open my local AxiorHub') }}</NcButton>
						<NcButton variant="tertiary" :href="config.personalUrl">{{ t('axiorhub_pilote', 'Change the address') }}</NcButton>
					</div>
					<p class="axh-local-url">{{ config.localUrl }}</p>
				</NcNoteCard>
			</div>
			<SimpleView v-else />
		</NcAppContent>
	</NcContent>
</template>

<script setup>
// 0.4.0 : l'interface AxiorHub elle-même, affichée dans Nextcloud (cadre), avec les rubriques AxiorHub dans la navigation Nextcloud.
// La connexion se fait par une adresse à usage unique remise par le moteur au compte relié (aucun mot de passe ni secret dans le
// navigateur) ; si la session AxiorHub expire ou si le navigateur la bloque dans le cadre, une nouvelle adresse est demandée une
// fois, puis l'écran propose « Ouvrir dans un onglet » et la « Vue simplifiée » (écran de la 0.3.0).
import { computed, onBeforeUnmount, onMounted, ref } from 'vue'
import axios from '@nextcloud/axios'
import { emit, subscribe, unsubscribe } from '@nextcloud/event-bus'
import { loadState } from '@nextcloud/initial-state'
import { t } from '@nextcloud/l10n'
import { generateUrl, imagePath } from '@nextcloud/router'
import NcAppContent from '@nextcloud/vue/components/NcAppContent'
import NcAppNavigation from '@nextcloud/vue/components/NcAppNavigation'
import NcButton from '@nextcloud/vue/components/NcButton'
import NcContent from '@nextcloud/vue/components/NcContent'
import NcLoadingIcon from '@nextcloud/vue/components/NcLoadingIcon'
import NcNoteCard from '@nextcloud/vue/components/NcNoteCard'
import SimpleView from './components/SimpleView.vue'

const config = loadState('axiorhub_pilote', 'config', { engineConfigured: false, linked: false, personalUrl: '', isAdmin: false, embed: { ok: false } })
// mêmes rubriques, icônes et ordre que le menu d'AxiorHub (six accès principaux, puis les fonctions du cabinet)
const sections = [
	{ path: '/aujourdhui', icon: '◎', label: t('axiorhub_pilote', 'Today') },
	{ path: '/dossiers', icon: '▦', label: t('axiorhub_pilote', 'Cases') },
	{ path: '/courriels', icon: '✉', label: t('axiorhub_pilote', 'Emails') },
	{ path: '/planning', icon: '◷', label: t('axiorhub_pilote', 'Calendar and tasks') },
	{ path: '/production', icon: '＋', label: t('axiorhub_pilote', 'Produce') },
	{ path: '/corporate', icon: '⚖', label: t('axiorhub_pilote', 'Corporate') },
	{ path: '/contacts-crm', icon: '☏', label: t('axiorhub_pilote', 'Contacts & CRM') },
	{ path: '/initiatives', icon: '✦', label: t('axiorhub_pilote', 'Initiatives') },
	{ path: '/outils-ia', icon: '◇', label: t('axiorhub_pilote', 'AI tools') },
	{ path: '/recherche-juridique', icon: '⌕', label: t('axiorhub_pilote', 'Legal research') },
	{ path: '/missions', icon: '➤', label: t('axiorhub_pilote', 'Missions') },
	{ path: '/toutes-les-fonctions', icon: '▸', label: t('axiorhub_pilote', 'All functions') },
	{ path: '/parametres', icon: '⚙', label: t('axiorhub_pilote', 'Settings') },
]
const PRIMARY = ['/aujourdhui', '/dossiers', '/courriels', '/planning', '/production', '/parametres']
const groups = [
	PRIMARY.map((p) => sections.find((s) => s.path === p)),
	sections.filter((s) => !PRIMARY.includes(s.path)),
]
const logo = imagePath('axiorhub_pilote', 'axiorhub-logo.png')
// eslint-disable-next-line no-undef
const APP_VERSION = typeof appVersion === 'string' ? appVersion : ''
const REASONS = {
	moteur_a_mettre_a_jour: () => t('axiorhub_pilote', 'This AxiorHub engine is too old to be displayed in Nextcloud: update it to version 5.6.37 or later.'),
	moteur_injoignable: () => t('axiorhub_pilote', 'AxiorHub cannot be reached from this Nextcloud server (address, firewall or certificate). An AxiorHub installed on a workstation cannot be reached from a hosted Nextcloud: use “My local AxiorHub” (discovery mode) or install the server version.'),
	moteur_poste_non_pris_en_charge: () => t('axiorhub_pilote', 'AxiorHub installed on a workstation: it can only be used in discovery mode, from the computer where it is installed (Nextcloud on the same computer, or the “My local AxiorHub” link). For your firm, install the server version (Linux, HTTPS).'),
	moteur_occupe: () => t('axiorhub_pilote', 'AxiorHub is busy finishing another operation: try again in a few seconds.'),
	cle_personnelle_revoquee_ou_inconnue: () => t('axiorhub_pilote', 'Your personal key was revoked: link your account again in Personal settings.'),
	integration_desactivee: () => t('axiorhub_pilote', 'AxiorHub does not accept Nextcloud requests: ask the administrator to allow the Nextcloud app in AxiorHub.'),
	application_nextcloud_non_autorisee: () => t('axiorhub_pilote', 'AxiorHub does not accept Nextcloud requests: ask the administrator to allow the Nextcloud app in AxiorHub.'),
	role_insuffisant: () => t('axiorhub_pilote', 'Your AxiorHub account cannot open the full interface: use the simplified view.'),
	acces_refuse_par_le_frontal: () => t('axiorhub_pilote', 'The web server in front of AxiorHub asks for a login: allow /integration/v1/ without login on that server.'),
	distribution_autonome: () => t('axiorhub_pilote', 'This AxiorHub installation (Docker distribution) asks for its own login and cannot be displayed inside Nextcloud: open it in a new tab.'),
	cadre_bloque: () => t('axiorhub_pilote', 'Your browser does not keep the AxiorHub session inside Nextcloud (third-party cookies blocked). Open AxiorHub in a new tab, or serve AxiorHub and Nextcloud under the same domain.'),
}

const view = ref('axiorhub')
const src = ref('')
const base = ref('')
const frameOrigin = ref('')
const current = ref('/aujourdhui')   // rubrique réellement chargée dans le cadre
const selected = ref('/aujourdhui')  // rubrique choisie par l'utilisateur (surlignée dans le menu, même pendant le chargement)
const loading = ref(false)
// 0.4.1 (audit 5.6.37, F08) : chaque demande de session porte un numéro ; seule la plus récente peut modifier l'affichage
let sessionSeq = 0
let sessionAbort = null
const problem = ref(null)
const frameKey = ref(0)
const discovery = ref(false)   // 0.4.2 : moteur installé sur un poste (mode découverte) : bandeau d'avertissement permanent
let renewals = 0

const problemText = computed(() => (REASONS[problem.value?.code] ? REASONS[problem.value.code]()
	: (problem.value?.message || t('axiorhub_pilote', 'The request was refused.') + (problem.value?.code ? ' (' + problem.value.code + ')' : ''))))

function apply(session, path) {
	if (session?.ok && session.standalone) {
		// distribution Docker : la couche des comptes du cabinet ne s'affiche pas dans un cadre
		base.value = session.base
		frameOrigin.value = session.frame_origin
		current.value = path
		problem.value = { code: 'distribution_autonome' }
	} else if (session?.ok) {
		discovery.value = !!session.poste
		src.value = session.url
		base.value = session.base
		frameOrigin.value = session.frame_origin
		current.value = path
		problem.value = null
		frameKey.value += 1
	} else {
		problem.value = session || { code: 'session_refusee' }
	}
}

async function fresh(path, signal) {
	const { data } = await axios.post(generateUrl('/apps/axiorhub_pilote/api/session'), { page: path }, signal ? { signal } : undefined)
	return data
}

function invalidateSession() {
	sessionSeq += 1
	if (sessionAbort) {
		sessionAbort.abort()
		sessionAbort = null
	}
	return sessionSeq
}

async function reconnect(path) {
	const seq = invalidateSession()   // la demande précédente est abandonnée ; sa réponse tardive sera ignorée
	sessionAbort = typeof AbortController === 'function' ? new AbortController() : null
	selected.value = path
	loading.value = true
	problem.value = null
	try {
		const session = await fresh(path, sessionAbort?.signal)
		if (seq === sessionSeq) {
			apply(session, path)
		}
	} catch (error) {
		if (seq === sessionSeq && !axios.isCancel?.(error) && error?.name !== 'CanceledError') {
			apply(error?.response?.data || { code: 'moteur_injoignable' }, path)
		}
	} finally {
		if (seq === sessionSeq) {
			loading.value = false
			sessionAbort = null
		}
	}
}

function go(path) {
	view.value = 'axiorhub'
	selected.value = path
	if (!config.linked) {
		return
	}
	if (!base.value || problem.value) {
		reconnect(path)
		return
	}
	invalidateSession()   // un choix direct rend obsolète toute demande de session encore en vol
	loading.value = false
	current.value = path
	src.value = base.value + path   // session AxiorHub déjà ouverte dans le cadre
	frameKey.value += 1
}

async function openTab() {
	const opened = window.open('', '_blank')   // ouvert pendant le clic (sinon bloqué), adresse donnée ensuite
	try {
		const session = await fresh(current.value)
		if (session?.ok && opened) {
			opened.opener = null
			opened.location.href = session.url
			return
		}
		opened?.close()
		apply(session, current.value)
	} catch (error) {
		opened?.close()
		problem.value = error?.response?.data || { code: 'moteur_injoignable' }
	}
}

// 0.4.2 : AxiorHub installé sur ce poste, Nextcloud hébergé : simple lien ouvert par le navigateur (jamais par le serveur Nextcloud)
function openLocal() {
	const url = String(config.localUrl || '')
	if (/^https?:\/\/(127\.0\.0\.1|localhost|\[::1\])(:\d{1,5})?(\/|$)/i.test(url)) {
		window.open(url, '_blank', 'noopener,noreferrer')
	}
}

// menu de gauche repliable : bouton « Replier le menu », bouton ☰ de Nextcloud pour le rouvrir ; choix mémorisé dans ce navigateur
const NAV_KEY = 'axiorhub_pilote:menu'
function collapse() {
	emit('toggle-navigation', { open: false })
}
function onNavigation({ open }) {
	try {
		window.localStorage.setItem(NAV_KEY, open ? 'ouvert' : 'replie')
	} catch (e) {
		// stockage du navigateur indisponible : le choix vaut pour cette page
	}
}

function onMessage(event) {
	// seuls les messages du cadre AxiorHub (origine annoncée par le moteur) sont pris en compte
	if (!frameOrigin.value || event.origin !== frameOrigin.value || typeof event.data !== 'object' || event.data === null) {
		return
	}
	if (event.data.axiorhub === 'page' && typeof event.data.path === 'string') {
		const match = sections.find((s) => event.data.path === s.path || event.data.path.startsWith(s.path + '/'))
		if (match) {
			current.value = match.path
			selected.value = match.path
		}
		renewals = 0
	} else if (event.data.axiorhub === 'session') {
		if (renewals < 1) {   // une seule nouvelle adresse : au-delà, le navigateur bloque la session dans le cadre
			renewals += 1
			reconnect(current.value)
		} else {
			problem.value = { code: 'cadre_bloque' }
		}
	}
}

onMounted(() => {
	window.addEventListener('message', onMessage)
	let saved = null
	try {
		saved = window.localStorage.getItem(NAV_KEY)
	} catch (e) {
		saved = null
	}
	if (saved === 'replie' && window.innerWidth >= 1024) {
		setTimeout(() => emit('toggle-navigation', { open: false }), 0)
	}
	subscribe('navigation-toggled', onNavigation)
	if (config.linked) {
		const initial = config.embed || { ok: false }
		if (!initial.ok && initial.code === 'compte_non_relie') {
			return
		}
		apply(initial, '/aujourdhui')
	} else if (config.engineConfigured) {
		view.value = 'simple'   // mode d'instance (sans compte relié) : écran simplifié de la 0.3.0
	}
})

onBeforeUnmount(() => {
	invalidateSession()
	window.removeEventListener('message', onMessage)
	unsubscribe('navigation-toggled', onNavigation)
})
</script>

<style scoped>
.axh-embed { height: 100%; display: flex; flex-direction: column; }
.axh-frame { flex: 1; width: 100%; min-height: calc(100vh - 50px); border: 0; display: block; background: var(--color-main-background); }
.axh-note { margin: 16px; max-width: 820px; }
.axh-loading { margin: 48px auto; }
.axh-buttons { display: flex; flex-wrap: wrap; gap: 8px; margin-top: 8px; }
.axh-link { color: var(--color-primary-element); text-decoration: underline; }
.axh-discovery { margin-bottom: 0; }
.axh-local { padding: 16px; }
.axh-local-url { font-family: monospace; color: var(--color-text-maxcontrast); word-break: break-all; }
</style>

<style>
/* 0.4.0 : menu aux couleurs d'AxiorHub (valeurs relevées sur le menu d'AxiorHub 5.6.37) : panneau bleu nuit, boutons espacés. */
.axh-app #app-navigation-vue,
.axh-app .app-navigation {
	--color-main-background: #17191e;
	--color-main-background-blur: #17191e;
	--color-main-text: #f5f6f8;
	background: #17191e !important;
	color: #f5f6f8;
	border-right: 1px solid #303a46;
	backdrop-filter: none;
}
/* bande sombre entre le menu et AxiorHub : elle porte le bouton ☰ de Nextcloud (ouvrir / replier le menu) sans recouvrir AxiorHub */
.axh-app .axh-embed { padding-inline-start: calc(var(--default-clickable-area, 44px) + 2 * var(--app-navigation-padding, 6px)); background: #17191e; box-sizing: border-box; }
.axh-app .axh-embed .axh-note { background: var(--color-main-background); }
.axh-app .app-navigation-toggle { background: #17191e !important; color: #f5f6f8 !important; }
.axh-app .app-navigation-toggle:hover { background: #303b4b !important; }
/* menu replié : même règle que Nextcloud, avec une largeur de repli si la variable du thème manque */
.axh-app .app-navigation.app-navigation--closed { margin-inline-start: calc(-1 * min(300px, var(--app-navigation-max-width, 300px))); }
.axh-side { display: flex; flex-direction: column; min-height: 100%; padding: 22px 18px 18px; box-sizing: border-box;
	font-family: system-ui, -apple-system, "Segoe UI", sans-serif; }
.axh-brand { display: flex; align-items: center; gap: 10px; margin: 0 4px 22px; }
.axh-brand img { flex: none; border-radius: 8px; }
.axh-brand-name { display: block; font-size: 24px; font-weight: 800; letter-spacing: -0.035em; line-height: 1.1; color: #fff; }
.axh-brand-sub { display: block; font-size: 12px; line-height: 1.4; color: #ddd2fb; }
.axh-side-group { display: flex; flex-direction: column; gap: 4px; padding: 8px 0; }
.axh-side-group + .axh-side-group { border-top: 1px solid rgba(255, 255, 255, 0.18); margin-top: 10px; padding-top: 14px; }
.axh-side-group a { display: flex; align-items: center; gap: 12px; min-height: 42px; box-sizing: border-box; padding: 10px 13px;
	border-radius: 10px; color: #f5f6f8 !important; font-size: 14.4px; line-height: 1.5; text-decoration: none; }
.axh-side-group a:hover, .axh-side-group a:focus-visible { background: rgba(255, 255, 255, 0.08); outline: none; }
.axh-side-group a[aria-current='page'] { background: #303b4b; color: #fff !important; font-weight: 800; }
.axh-side-icon { display: inline-flex; align-items: center; justify-content: center; flex: none; width: 24px; font-size: 1.24rem; line-height: 1; }
.axh-side-about { margin: auto 4px 0; padding-top: 20px; border-top: 1px solid #303a46; font-size: 12px; line-height: 1.5; color: #e1d9f6; }
</style>
