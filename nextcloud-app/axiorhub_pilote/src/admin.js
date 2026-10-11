/* Réglages administrateur : enregistrement par l'API de l'application (jeton CSRF fourni par @nextcloud/axios). */
import axios from '@nextcloud/axios'
import { generateUrl } from '@nextcloud/router'
import { t } from '@nextcloud/l10n'

const form = document.getElementById('axh-admin-form')
if (form) {
	form.addEventListener('submit', async (event) => {
		event.preventDefault()
		const status = document.getElementById('axh-admin-status')
		const data = new FormData(form)
		try {
			await axios.post(generateUrl('/apps/axiorhub_pilote/api/config'), {
				engineUrl: data.get('engineUrl'),
				instanceId: data.get('instanceId'),
				secret: data.get('secret') || null,
				allowLocal: data.get('allowLocal') === 'on',
			})
			status.textContent = t('axiorhub_pilote', 'Saved.')
			form.secret.value = ''
		} catch (error) {
			status.textContent = t('axiorhub_pilote', 'Not saved: {code}', { code: error?.response?.data?.code || 'erreur' })
		}
	})
}
