#!/usr/bin/env node

import fs from 'node:fs';
import path from 'node:path';

const root = path.resolve(process.argv[2] || '.');
const read = (name) => fs.readFileSync(path.join(root, name), 'utf8');
const js = read('ai_roundcube_assistant.js');
const php = read('ai_roundcube_assistant.php');
const css = read('ai_roundcube_assistant.css');
const sql = read('migrations/001_v8_postgresql.sql');
const failures = [];
const requireMarker = (source, marker, label) => {
    if (!source.includes(marker)) failures.push('absent : ' + label);
};

const supportedBlock = js.match(/const SUPPORTED_ACTIONS = \[([\s\S]*?)\];/);
if (!supportedBlock) failures.push('SUPPORTED_ACTIONS absent');
const supported = supportedBlock
    ? [...supportedBlock[1].matchAll(/'([^']+)'/g)].map((match) => match[1])
    : [];
const uiBlock = js.match(/function createUi\(\) \{([\s\S]*?)document\.body\.appendChild\(menu\);/);
if (!uiBlock) failures.push('construction du menu absente');
const buttons = [...(uiBlock ? uiBlock[1] : '').matchAll(/data-ai-action=\\?"([^"\\]+)\\?"/g)]
    .map((match) => match[1]);
const uniqueButtons = [...new Set(buttons)];
for (const action of uniqueButtons) {
    if (!supported.includes(action)) failures.push('bouton non déclaré : ' + action);
}
for (const action of supported) {
    if (!uniqueButtons.includes(action)) failures.push('action sans bouton : ' + action);
}
if (uniqueButtons.length !== buttons.length) failures.push('bouton data-ai-action dupliqué');

const registrationPattern = /register_action\(\s*'([^']+)'\s*,\s*\[\$this,\s*'([^']+)'\]\s*\)/g;
const registrations = [...php.matchAll(registrationPattern)];
for (const match of registrations) {
    if (!new RegExp('public\\s+function\\s+' + match[2] + '\\s*\\(').test(php)) {
        failures.push('action PHP sans méthode : ' + match[1] + ' -> ' + match[2]);
    }
}

[
    [js, 'id="ai-rc-persistent-action"', 'progression persistante'],
    [js, 'await beginPersistentAction(action)', 'gestionnaire universel'],
    [js, "'awaiting_confirmation'", 'validation humaine journalisée'],
    [js, "_op: 'event_resume'", 'reprise des succès partiels'],
    [js, 'findOpenWebUICalendarDuplicates', 'doublons Open WebUI'],
    [js, 'findNextcloudCalendarDuplicates', 'doublons Nextcloud'],
    [js, 'showConnectorHealth', 'écran de santé'],
    [js, 'showNextcloudBrowser', 'navigateur Nextcloud restreint'],
    [js, 'showAutomationSuggestions', 'proposition d’automations'],
    [js, 'buildOrUpdateProjectOverview', 'État du dossier différentiel'],
    [php, "'plugin.ai_v8_action'", 'route des actions'],
    [php, "'plugin.ai_v8_health'", 'route de santé'],
    [php, "'plugin.ai_v8_nextcloud_browse'", 'route navigateur'],
    [php, 'hash_equals($allowed_calendar, $calendar)', 'liste blanche calendrier'],
    [php, "'aes-256-gcm'", 'jetons Nextcloud chiffrés et authentifiés'],
    [php, "if ($node_id === '')", 'téléchargement exclusivement par nœud opaque'],
    [js, "_op: 'file_import_reserve'", 'réservation d’import de fichier'],
    [js, 'sha256FileHex', 'empreinte SHA-256 des fichiers'],
    [php, "if ($op === 'file_import_complete')", 'finalisation atomique des imports'],
    [php, "':write_preview' => $next === 'awaiting_confirmation' ? 'true' : 'false'", 'booléens PostgreSQL explicites'],
    [php, 'CURLOPT_FOLLOWLOCATION => false', 'refus des redirections'],
    [sql, 'UNIQUE (user_id, client_request_id)', 'idempotence action/double-clic'],
    [sql, 'UNIQUE (user_id, fingerprint)', 'idempotence événements'],
    [sql, 'ai_audit_immutable_guard', 'audit append-only'],
    [css, '.ai-rc-status-partially_succeeded', 'état visuel succès partiel']
].forEach(([source, marker, label]) => requireMarker(source, marker, label));

for (const table of [
    'ai_integration_actions', 'ai_audit_events', 'ai_project_activities',
    'ai_calendar_event_links', 'ai_file_import_fingerprints', 'ai_project_snapshots'
]) requireMarker(sql, 'CREATE TABLE IF NOT EXISTS ' + table, 'table ' + table);

const transition = php.match(/if \(\$op === 'transition'\)[\s\S]*?\$allowed = \[([\s\S]*?)\n\s*\];/);
if (!transition) failures.push('automate d’état absent');
else {
    for (const terminal of ['succeeded', 'partially_succeeded', 'failed', 'cancelled']) {
        if (!new RegExp("'" + terminal + "'\\s*=>\\s*\\[\\]").test(transition[0])) {
            failures.push('état terminal réouvrable : ' + terminal);
        }
    }
}

if (!/if \(state\.busy !== null\)[\s\S]{0,250}Une action est déjà en cours/.test(js)) {
    failures.push('garde anti-double-clic globale absente');
}
if (!/event_resume[\s\S]{0,2500}status IN \('partially_succeeded','failed'\)/.test(php)) {
    failures.push('reprise limitée aux succès partiels/échecs absente');
}
if (!/SELECT confirmed_at[\s\S]{0,1200}n’a pas été confirmée/.test(php)) {
    failures.push('garde serveur de confirmation absente');
}
if (/get_input_string\('_path'/.test(php) || /form\.set\('_path'/.test(js)) {
    failures.push('ancien chemin Nextcloud libre encore accepté');
}

const secretPatterns = [
    /X-API-TOKEN:\s*[A-Za-z0-9_-]{20,}/,
    /ai_nextcloud_password[^\n=]*=\s*['"][^'"]{8,}/,
    /ai_v8_pg_password[^\n=]*=\s*['"][^'"]{8,}/
];
for (const pattern of secretPatterns) {
    if (pattern.test(js) || pattern.test(php)) failures.push('secret intégré dans les sources');
}

if (failures.length) {
    console.error('ÉCHEC CONTRÔLE V8');
    failures.forEach((failure) => console.error('- ' + failure));
    process.exit(1);
}

console.log(
    'CONTRÔLE V8 RÉUSSI : ' + uniqueButtons.length + ' boutons, ' + registrations.length +
    ' actions PHP, contrats de confirmation/idempotence/reprise/audit présents.'
);
