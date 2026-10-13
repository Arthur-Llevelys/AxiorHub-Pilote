/* Réglages administrateur : enregistrement par l'API de l'application (jeton CSRF fourni par @nextcloud/axios).
   0.2.0 : l'enregistrement teste la liaison et importe les dossiers du moteur ; tableau des dossiers et de leur groupe autorisé. */
import axios from '@nextcloud/axios'
import { generateUrl } from '@nextcloud/router'
import { t, n } from '@nextcloud/l10n'

const api = (path) => generateUrl('/apps/axiorhub_pilote/api' + path)
const form = document.getElementById('axh-admin-form')
const table = document.querySelector('#axh-admin-matters tbody')

// 0.4.2 : causes expliquées (moteur installé sur un poste local, moteur injoignable)
const REASONS = {
	moteur_poste_non_pris_en_charge: () => t('axiorhub_pilote', 'AxiorHub installed on a workstation: it can only be used in discovery mode, from the computer where it is installed (Nextcloud on the same computer, or the “My local AxiorHub” link). For your firm, install the server version (Linux, HTTPS).'),
	moteur_injoignable: () => t('axiorhub_pilote', 'AxiorHub cannot be reached from this Nextcloud server (address, firewall or certificate). An AxiorHub installed on a workstation cannot be reached from a hosted Nextcloud: use “My local AxiorHub” (discovery mode) or install the server version.'),
}

function syncText(sync) {
	if (!sync) {
		return ''
	}
	if (!sync.ok) {
		return REASONS[sync.code] ? REASONS[sync.code]() : t('axiorhub_pilote', 'Cases not imported: {code}', { code: sync.code || 'erreur' })
	}
	return n('axiorhub_pilote', '%n case imported', '%n cases imported', sync.imported) + ' · '
		+ n('axiorhub_pilote', '%n case on the engine', '%n cases on the engine', sync.total)
}

async function loadMatters() {
	if (!table) {
		return
	}
	const { data } = await axios.get(api('/admin/matters'))
	table.replaceChildren()
	for (const m of data.matters) {
		const tr = document.createElement('tr')
		const label = document.createElement('td')
		label.textContent = m.label
		const ref = document.createElement('td')
		ref.textContent = m.matter
		const cell = document.createElement('td')
		const select = document.createElement('select')
		select.setAttribute('aria-label', t('axiorhub_pilote', 'Group allowed for {case}', { case: m.label }))
		for (const g of data.groups) {
			const option = new Option(g, g, false, g === m.group)
			select.append(option)
		}
		const status = document.createElement('span')
		status.setAttribute('role', 'status')
		select.addEventListener('change', async () => {
			try {
				await axios.post(api('/admin/matters'), { matterRef: m.matter, groupId: select.value, label: m.label })
				status.textContent = ' ' + t('axiorhub_pilote', 'Saved.')
			} catch (error) {
				status.textContent = ' ' + t('axiorhub_pilote', 'Not saved: {code}', { code: error?.response?.data?.code || 'erreur' })
			}
		})
		cell.append(select, status)
		tr.append(label, ref, cell)
		table.append(tr)
	}
	if (!data.matters.length) {
		const tr = document.createElement('tr')
		const td = document.createElement('td')
		td.colSpan = 3
		td.textContent = t('axiorhub_pilote', 'No case imported yet: save the settings or click “Import the cases of the engine”.')
		tr.append(td)
		table.append(tr)
	}
}

if (form) {
	form.addEventListener('submit', async (event) => {
		event.preventDefault()
		const status = document.getElementById('axh-admin-status')
		const data = new FormData(form)
		status.textContent = t('axiorhub_pilote', 'Saving and testing the connection…')
		try {
			const { data: out } = await axios.post(api('/config'), {
				engineUrl: data.get('engineUrl'),
				instanceId: data.get('instanceId'),
				secret: data.get('secret') || null,
				allowLocal: data.get('allowLocal') === 'on',
				defaultGroup: data.get('defaultGroup') || '',
			})
			status.textContent = t('axiorhub_pilote', 'Saved.') + ' '
				+ (out.reachable ? t('axiorhub_pilote', 'Engine reachable.') : t('axiorhub_pilote', 'Engine not reachable: check the address, the identifier and the secret on both sides.'))
				+ ' ' + syncText(out.sync)
			form.secret.value = ''
			await loadMatters()
		} catch (error) {
			const code = error?.response?.data?.code || 'erreur'
			status.textContent = REASONS[code] ? REASONS[code]() : t('axiorhub_pilote', 'Not saved: {code}', { code })
		}
	})
	document.getElementById('axh-admin-sync')?.addEventListener('click', async () => {
		const status = document.getElementById('axh-admin-sync-status')
		status.textContent = t('axiorhub_pilote', 'Importing…')
		try {
			const { data } = await axios.post(api('/admin/sync'))
			status.textContent = syncText(data)
		} catch (error) {
			status.textContent = syncText(error?.response?.data || { ok: false })
		}
		await loadMatters()
	})
	loadMatters().catch(() => {})
}
