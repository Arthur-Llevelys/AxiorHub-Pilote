// Déplace la feuille de style produite par Vite (js/*.css) vers css/axiorhub_pilote-main.css, chargée par \OCP\Util::addStyle.
import { mkdirSync, readdirSync, renameSync } from 'node:fs'

mkdirSync('css', { recursive: true })
for (const name of readdirSync('js')) {
	if (name.endsWith('.css')) {
		renameSync('js/' + name, 'css/axiorhub_pilote-main.css')
	}
}
