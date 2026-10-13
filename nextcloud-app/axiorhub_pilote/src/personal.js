/* 0.3.0 : paramètres personnels « AxiorHub Pilote » — relier, tester et délier son compte AxiorHub.
   Le mot de passe saisi part une seule fois vers le serveur Nextcloud, qui obtient la clé personnelle ; il n'est jamais conservé. */
import axios from '@nextcloud/axios'
import { generateUrl } from '@nextcloud/router'
import { t } from '@nextcloud/l10n'

const api = (path) => generateUrl('/apps/axiorhub_pilote/api' + path)
const form = document.getElementById('axh-personal-form')
const linked = document.getElementById('axh-personal-linked')
const status = document.getElementById('axh-personal-status')
const statusLinked = document.getElementById('axh-personal-status-linked')

const REASONS = {
	adresse_locale_requise: () => t('axiorhub_pilote', 'Enter an address of this computer: http://127.0.0.1:<port>, http://localhost:<port> or http://[::1]:<port>.'),
	adresse_locale_invalide: () => t('axiorhub_pilote', 'This address is not valid.'),
	identifiants_refuses: () => t('axiorhub_pilote', 'Login or password refused by AxiorHub.'),
	trop_de_tentatives: () => t('axiorhub_pilote', 'Too many attempts: try again in 15 minutes.'),
	application_nextcloud_non_autorisee: () => t('axiorhub_pilote', 'AxiorHub does not allow Nextcloud accounts yet: in AxiorHub, Settings › Connections › Nextcloud app, tick “Allow linking accounts from Nextcloud”.'),
	integration_desactivee: () => t('axiorhub_pilote', 'AxiorHub does not allow Nextcloud accounts yet: in AxiorHub, Settings › Connections › Nextcloud app, tick “Allow linking accounts from Nextcloud”.'),
	https_required: () => t('axiorhub_pilote', 'The address must start with https://.'),
	engine_url_invalid: () => t('axiorhub_pilote', 'This address is not valid.'),
	moteur_injoignable: () => t('axiorhub_pilote', 'AxiorHub cannot be reached from this Nextcloud server: check the address (for example https://courriel.example.test/agent-courriel). An AxiorHub installed on a workstation cannot be reached from a hosted Nextcloud: use “Address of my local AxiorHub” below (discovery mode).'),
	moteur_poste_non_pris_en_charge: () => t('axiorhub_pilote', 'AxiorHub installed on a workstation: it can only be used in discovery mode, from the computer where it is installed (Nextcloud on the same computer, or the “My local AxiorHub” link). For your firm, install the server version (Linux, HTTPS).'),
	adresse_locale_refusee: () => t('axiorhub_pilote', 'Nextcloud refuses this local address: tick “Engine on this server (local address)”.'),
	moteur_occupe: () => t('axiorhub_pilote', 'AxiorHub is busy: try again in a few seconds.'),
	reponse_moteur_invalide: () => t('axiorhub_pilote', 'This address answers but is not AxiorHub Pilote: check the address (it usually ends with /agent-courriel).'),
	acces_refuse_par_le_frontal: () => t('axiorhub_pilote', 'The web server in front of AxiorHub asks for a login: allow /integration/v1/ without login on that server.'),
}

// 0.4.2 : adresse de l'AxiorHub local (mode découverte) : lien seulement, jamais joint par le serveur Nextcloud
const localForm = document.getElementById('axh-personal-local')
if (localForm) {
	localForm.addEventListener('submit', async (event) => {
		event.preventDefault()
		const out = document.getElementById('axh-personal-local-status')
		try {
			const { data } = await axios.post(api('/personal/local'), { localUrl: new FormData(localForm).get('localUrl') || '' })
			out.textContent = data.local_url ? t('axiorhub_pilote', 'Saved: the “My local AxiorHub” entry appears in AxiorHub Pilote.') : t('axiorhub_pilote', 'Address removed.')
		} catch (error) {
			out.textContent = reason(error)
		}
	})
}

function reason(error) {
	const data = error?.response?.data || {}
	const known = REASONS[data.code]
	return known ? known() : (data.message || t('axiorhub_pilote', 'The request was refused.') + (data.code ? ' (' + data.code + ')' : ''))
}

function say(target, text, bad = false) {
	if (target) {
		target.textContent = text
		target.style.color = bad ? 'var(--color-error)' : ''
	}
}

form?.addEventListener('submit', async (ev) => {
	ev.preventDefault()
	const f = new FormData(form)
	const button = form.querySelector('button[type=submit]')
	button.disabled = true
	say(status, t('axiorhub_pilote', 'Linking…'))
	try {
		const { data } = await axios.post(api('/personal/link'), {
			engineUrl: String(f.get('engineUrl') || ''), login: String(f.get('login') || ''), password: String(f.get('password') || ''),
			allowLocal: f.get('allowLocal') === 'on',
		})
		form.querySelector('input[name=password]').value = ''
		say(status, t('axiorhub_pilote', 'Account linked: {user}.', { user: data.axiorhub_user }))
		setTimeout(() => window.location.reload(), 900)
	} catch (error) {
		say(status, reason(error), true)
	} finally {
		button.disabled = false
	}
})

document.getElementById('axh-personal-test')?.addEventListener('click', async () => {
	say(statusLinked, t('axiorhub_pilote', 'Testing…'))
	try {
		const { data } = await axios.get(api('/personal/test'))
		say(statusLinked, t('axiorhub_pilote', 'Connection works: AxiorHub {version}, account {user}.', { version: data.version || '', user: data.user || '' }))
	} catch (error) {
		say(statusLinked, reason(error), true)
	}
})

document.getElementById('axh-personal-unlink')?.addEventListener('click', async () => {
	if (!window.confirm(t('axiorhub_pilote', 'Unlink your AxiorHub account? The personal key is revoked; you can link again at any time.'))) {
		return
	}
	try {
		await axios.post(api('/personal/unlink'))
		window.location.reload()
	} catch (error) {
		say(statusLinked, reason(error), true)
	}
})

if (linked && !linked.hidden) {
	say(statusLinked, '')
}
