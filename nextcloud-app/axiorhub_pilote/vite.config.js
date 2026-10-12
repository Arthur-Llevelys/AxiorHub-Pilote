import { defineConfig } from 'vite'
import vue from '@vitejs/plugin-vue'

// Actifs compilés dans js/ (JavaScript) puis css/ (feuille unique, déplacée par scripts/move-css.mjs) : inclus dans le paquet,
// l'utilisateur n'a pas besoin de npm pour installer l'application. Aucun script en ligne (CSP Nextcloud).
export default defineConfig({
	plugins: [vue()],
	// appName / appVersion : attendus par @nextcloud/vue 9 (journal « library was used without setting appName » sinon)
	define: { 'process.env.NODE_ENV': JSON.stringify('production'), appName: JSON.stringify('axiorhub_pilote'), appVersion: JSON.stringify(process.env.npm_package_version || '') },
	build: {
		outDir: 'js',
		emptyOutDir: true,
		cssCodeSplit: false,
		sourcemap: false,
		rollupOptions: {
			input: { 'axiorhub_pilote-main': 'src/main.js', 'axiorhub_pilote-admin': 'src/admin.js', 'axiorhub_pilote-personal': 'src/personal.js' },
			output: {
				entryFileNames: '[name].mjs',
				chunkFileNames: 'axiorhub_pilote-[name]-[hash].chunk.mjs',
				assetFileNames: 'axiorhub_pilote-[name][extname]',
			},
		},
	},
})
