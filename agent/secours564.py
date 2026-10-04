"""Secours externe (AxiorHub Pilote 5.6.4) : quand le modèle local est trop lent, la génération est confiée à un fournisseur externe.

Deux déclencheurs, pour les seules fonctions de rédaction et les demandes de l'avocat (le tri des courriels, la lecture des pièces jointes
et le contrôle indépendant restent locaux) :
- une génération locale dépasse le délai (3 minutes par défaut) : elle est abandonnée et refaite chez le premier fournisseur disponible ;
- un travail a attendu son tour plus longtemps que ce délai (modèle local occupé) : il part directement chez le fournisseur externe.

Fournisseurs essayés dans l'ordre Mistral, Anthropic (Claude), OpenAI (ChatGPT), OpenRouter, parmi ceux que l'administrateur a activés ET
autorisés (case « J'autorise l'envoi… »). Tout envoi est pseudonymisé sur le serveur (agent.pseudo540) avant de partir, et chaque bascule
est journalisée (travail, fonction, fournisseur, motif), jamais le texte.
"""
from datetime import datetime, timezone
from html import escape as e
from pathlib import Path
import sqlite3
import threading
import time

from .common import Stop

PURPOSES = ('assistant', 'mail_drafting', 'legal_analysis', 'hearing', 'document_drafting', 'roundcube')
ORDER = ('mistral', 'anthropic', 'openai', 'openrouter')
TYPE_LABELS = {'mistral': 'Mistral AI', 'anthropic': 'Anthropic (Claude)', 'openai': 'OpenAI (ChatGPT)', 'openrouter': 'OpenRouter'}
REASONS = {'attente': 'travail en attente depuis plus que le délai (modèle local occupé)',
           'generation_ia_delai_depasse': 'génération locale plus longue que le délai'}
DEFAULT_DELAY = 180
_context = threading.local()


def normalize(raw):
    raw = raw if isinstance(raw, dict) else {}
    try:
        delay = int(raw.get('delay', DEFAULT_DELAY))
    except (TypeError, ValueError):
        delay = DEFAULT_DELAY
    return {'enabled': bool(raw.get('enabled', False)), 'delay': max(60, min(delay, 900))}


def settings(config):
    return normalize(config.get('secours_routing'))


def candidates(config, purpose):
    """Fournisseurs externes utilisables, dans l'ordre retenu (activés, autorisés, avec un modèle)."""
    from .ai_gateway import provider_registry
    from .model import pseudo_sources
    from .extensions364 import active_skill_instructions
    registry = provider_registry(config)
    out = []
    for kind in ORDER:
        for pid, p in sorted(registry.items()):
            if p.get('type') != kind or not p.get('enabled') or not p.get('external_data_allowed') or not p.get('model'):
                continue
            item = dict(p)
            item.update({'provider_id': pid, 'provider_type': kind, 'purpose': purpose, 'state_dir': config.get('state_dir', ''),
                         'pseudo': pseudo_sources(config), 'skill_instructions': active_skill_instructions(config, purpose)})
            out.append(item)
    return out


def attach(base, config, purpose):
    """Ajoute le secours à la configuration d'un modèle LOCAL (appelé par model.routed_config)."""
    s = settings(config)
    if not s['enabled'] or purpose not in PURPOSES or base.get('provider_type') != 'ollama' or base.get('hybrid_external'):
        return base
    found = candidates(config, purpose)
    if found:
        base['secours'] = {'delay': s['delay'], 'candidates': found}
    return base


# ---------------------------------------------------------------------------------------------- travail en cours
def begin(job_id, created, kind=''):
    """Début d'un travail : retient le temps qu'il a ATTENDU dans la file avant de démarrer (et non sa durée d'exécution)."""
    try:
        stamp = datetime.fromisoformat(str(created)).timestamp()
    except (TypeError, ValueError):
        stamp = time.time()
    _context.job, _context.queue_wait, _context.kind = job_id, max(0.0, time.time() - stamp), kind


def end():
    _context.job, _context.queue_wait, _context.kind = None, 0.0, ''


def waited():
    """Attente du travail en cours dans la file, avant son démarrage (0 hors d'un travail)."""
    return float(getattr(_context, 'queue_wait', 0.0) or 0.0)


def current_job():
    return getattr(_context, 'job', None)


# ---------------------------------------------------------------------------------------------- journal
SCHEMA = '''CREATE TABLE IF NOT EXISTS secours564_log(id INTEGER PRIMARY KEY, at TEXT NOT NULL, job_id INTEGER, purpose TEXT NOT NULL,
  provider TEXT NOT NULL, model TEXT NOT NULL, reason TEXT NOT NULL, status TEXT NOT NULL, error TEXT NOT NULL DEFAULT '');
CREATE INDEX IF NOT EXISTS secours564_job ON secours564_log(job_id);'''


def record(state_dir, candidate, reason, status, error=''):
    if not state_dir:
        return
    try:
        db = sqlite3.connect(Path(state_dir) / 'desk.sqlite3', timeout=10)
        try:
            db.executescript(SCHEMA)
            db.execute('INSERT INTO secours564_log(at,job_id,purpose,provider,model,reason,status,error) VALUES (?,?,?,?,?,?,?,?)',
                       (datetime.now(timezone.utc).isoformat(), current_job(), candidate.get('purpose', ''), candidate.get('provider_id', ''),
                        str(candidate.get('model', ''))[:160], str(reason)[:80], status, str(error)[:200]))
            db.commit()
        finally:
            db.close()
    except sqlite3.Error:
        pass


def for_job(desk, job_id):
    desk.db.executescript(SCHEMA)
    return [dict(r) for r in desk.db.execute('SELECT * FROM secours564_log WHERE job_id=? ORDER BY id', (job_id,))]


def recent(desk, limit=20):
    desk.db.executescript(SCHEMA)
    return [dict(r) for r in desk.db.execute('SELECT * FROM secours564_log ORDER BY id DESC LIMIT ?', (limit,))]


# ---------------------------------------------------------------------------------------------- réglage (administrateur)
def save(desk, data):
    flag = data.get('enabled')
    value = normalize({'enabled': flag is True or str(flag or '').strip().lower() in ('yes', 'on', 'true', '1'), 'delay': data.get('delay', DEFAULT_DELAY)})
    if value['enabled'] and not candidates(desk.c, 'assistant'):
        raise Stop('secours_sans_fournisseur')
    desk.setting('ai:secours', value)
    desk.c['secours_routing'] = value
    desk.audit('secours564_regle', value)
    if not value['enabled']:
        return {'message': 'Secours externe désactivé : tout reste sur le modèle local.'}
    names = ', '.join(TYPE_LABELS.get(c['provider_type'], c['provider_type']) for c in candidates(desk.c, 'assistant'))
    return {'message': 'Secours externe activé après %d s, dans l’ordre : %s. Envois toujours pseudonymisés.' % (value['delay'], names)}


def section_html(desk, prefix):
    from .ai_gateway import provider_registry
    s = settings(desk.c)
    registry = provider_registry(desk.c)
    usable = {c['provider_type'] for c in candidates(desk.c, 'assistant')}
    rows = ''
    for n, kind in enumerate(ORDER, 1):
        configured = [pid for pid, p in registry.items() if p.get('type') == kind]
        state = ('<span class="ok">prêt</span>' if kind in usable else
                 ('activé mais non autorisé ou sans modèle' if configured else 'non configuré'))
        rows += '<tr><td>%d</td><td>%s</td><td>%s</td></tr>' % (n, e(TYPE_LABELS[kind]), state)
    log = recent(desk, 10)
    journal = ''.join('<li>%s · %s · %s · %s%s</li>' % (e(x['at'][:16].replace('T', ' ')), e(x['provider']), e(x['purpose']),
                                                        e(REASONS.get(x['reason'], x['reason'])), (' · échec : ' + e(x['error'])) if x['status'] != 'ok' else '')
                      for x in log)
    return ('<section class="ax-card" id="secours564"><h2>Secours externe (modèle local trop lent)</h2>'
            '<p class="ax-muted">Pour les rédactions et vos demandes seulement (le tri des courriels, les pièces jointes et le contrôle restent '
            'locaux) : si le modèle local dépasse le délai, ou si un travail a attendu son tour plus longtemps, la génération est confiée au '
            'premier fournisseur prêt de la liste. <strong>Toujours pseudonymisé.</strong></p>'
            '<table class="vf-table"><thead><tr><th>Ordre</th><th>Fournisseur</th><th>État</th></tr></thead><tbody>%s</tbody></table>'
            '<p class="vf-note">Un fournisseur est « prêt » quand il est activé, autorisé (« J’autorise l’envoi… ») et qu’un modèle est indiqué, dans '
            '<a href="%s">Paramètres › IA</a>.</p>'
            '<form class="m5-form" data-api="m540/secours" data-reload="1">'
            '<label class="m5-field"><span><input type="checkbox" name="enabled" value="yes"%s style="width:auto"> Activer le secours externe</span></label>'
            '<label class="m5-field">Délai avant bascule (secondes, 60 à 900)<input name="delay" type="number" min="60" max="900" step="30" value="%d"></label>'
            '<button class="ax-btn" type="submit">Enregistrer</button></form>'
            '%s</section>') % (rows, e(prefix + '/parametres?tab=ia'), ' checked' if s['enabled'] else '', s['delay'],
                                ('<h3>Dernières bascules</h3><ul class="vf-note">%s</ul>' % journal) if journal else '')
