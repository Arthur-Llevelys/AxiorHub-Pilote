"""5.6.82 : catalogue des modèles par connexion (ModelCatalog).

Deux notions distinctes :

- **Le catalogue** : ce que le fournisseur liste pour CETTE connexion (compte, espace, heure de lecture). Lui seul rend un modèle candidat.
- **La référence datée** (``REFERENCE``) : niveau, tarif ou crédits relevés sur les pages officielles. Elle ne prouve aucun droit d'accès
  et ne fabrique jamais un identifiant : un slug absent du catalogue n'est pas appelé.

Une capacité inconnue reste inconnue. Elle ne s'infère pas d'un appel réussi et n'est pas sondée par des générations répétées.
Niveaux : ``econome``, ``standard``, ``avance``, ``maximal`` (ce dernier réservé à l'option du volet avancé).
"""
from datetime import datetime, timezone
import json
from pathlib import Path
import re
import sqlite3

from .common import Stop

NIVEAUX = ('econome', 'standard', 'avance', 'maximal')
NIVEAU_LABELS = {'econome': 'Économe', 'standard': 'Standard', 'avance': 'Avancé', 'maximal': 'Maximal (option)'}
DOC_DATE = '2026-10-31'
SOURCES = {'chatgpt': 'https://learn.chatgpt.com/docs/pricing', 'mistral': 'https://docs.mistral.ai/models',
           'anthropic': 'https://platform.claude.com/docs/en/about-claude/models/overview'}
UNITS = {'chatgpt': 'credits', 'mistral': 'usd', 'anthropic': 'usd', 'openai': 'usd', 'openrouter': 'usd', 'ollama': 'aucune'}
PLACEHOLDER = {'mistral': 'mistral-small-latest', 'anthropic': 'claude-sonnet-5-5'}   # valeur technique avant la lecture de la liste, jamais appelée
# (motif, niveau, entrée, entrée en cache, sortie) par million de jetons. ChatGPT : crédits, repère de consommation sans valeur de quota.
REFERENCE = {
    'chatgpt': ((r'gpt-6-luna$', 'econome', 2.5, 0.25, 12.5), (r'gpt-6\.1-terra$', 'standard', 25.0, 1.25, 125.0),
                (r'gpt-6\.1-sol$', 'avance', 50.0, 2.5, 250.0), (r'gpt-6-sol$', 'avance', 50.0, 5.0, 250.0),
                (r'gpt-6-astra$', 'maximal', 250.0, 25.0, 1250.0)),
    'mistral': ((r'mistral-small-(latest|2603)', 'econome', 0.15, None, 0.60), (r'mistral-large-4', 'avance', 0.70, None, 2.10)),
    'anthropic': ((r'claude-haiku-5-5', 'econome', 0.10, None, 0.50), (r'claude-sonnet-5-5', 'standard', 2.0, None, 10.0),
                  (r'claude-opus-5-5', 'avance', 4.0, None, 20.0), (r'claude-fable-5-1', 'maximal', 10.0, None, 50.0)),
}
# Familles d'un nom de modèle : une nouvelle version garde son niveau sans attendre une mise à jour d'AxiorHub (tarif alors « non fourni »).
FAMILIES = {'chatgpt': (('luna', 'econome'), ('mini', 'econome'), ('terra', 'standard'), ('sol', 'avance'), ('astra', 'maximal')),
            'mistral': (('small', 'econome'), ('ministral', 'econome'), ('medium', 'standard'), ('large', 'avance')),
            'anthropic': (('haiku', 'econome'), ('sonnet', 'standard'), ('opus', 'avance'), ('fable', 'maximal')),
            'openai': (('mini', 'econome'), ('gpt', 'standard')), 'openrouter': ()}
# Efforts documentés pour la voie et la famille. Ailleurs (Mistral), le réglage n'est pas envoyé : on choisit le modèle.
DOC_EFFORTS = {'chatgpt': ('low', 'medium', 'high'), 'anthropic': ('low', 'medium', 'high')}
KEY = 'catalogue5682:'


def _now():
    return datetime.now(timezone.utc).isoformat()


def reference(provider_type, slug):
    for pattern, niveau, entree, cache, sortie in REFERENCE.get(provider_type, ()):
        if re.match(pattern, str(slug)):
            return {'niveau': niveau, 'entree': entree, 'cache': cache, 'sortie': sortie, 'unite': UNITS[provider_type],
                    'source': SOURCES[provider_type], 'releve': DOC_DATE}
    return None


def level_of(provider_type, slug):
    ref = reference(provider_type, slug)
    if ref:
        return ref['niveau']
    name = str(slug).lower()
    for word, niveau in FAMILIES.get(provider_type, ()):
        if word in name:
            return niveau
    return ''


def _row(provider_type, item, account, workspace, read_at):
    slug = str(item.get('slug') or item.get('id') or '')
    known = [str(x).lower() for x in item.get('efforts') or []]
    documented = DOC_EFFORTS.get(provider_type, ())
    capacity = {'contexte': int(item['contexte']) if str(item.get('contexte') or '').isdigit() else None,
                'entrees': item.get('entrees') or None, 'structure': item.get('structure'),
                'efforts': known or (list(documented) if reference(provider_type, slug) else []),
                'preuve': 'catalogue_serveur' if known or item.get('contexte') else ('documentation' if documented else 'inconnue'),
                'transport': provider_type}
    return {'slug': slug, 'nom': str(item.get('nom') or item.get('label') or slug), 'niveau': level_of(provider_type, slug), 'statut': 'actif',
            'compte': account, 'espace': workspace, 'lu_le': read_at, 'source': 'liste_du_fournisseur', 'capacites': capacity}


def _store(desk, provider_id, account, workspace, rows):
    key = KEY + provider_id
    old = desk.settings(key, {}) or {}
    same_account = old.get('compte', account) == account
    seen = {r['slug'] for r in rows}
    gone = [{**r, 'statut': 'retire', 'retire_le': r.get('retire_le') or _now()} for r in (old.get('modeles') or [])
            if same_account and r.get('slug') not in seen]
    desk.setting(key, {'compte': account, 'espace': workspace, 'lu_le': _now(), 'modeles': rows + gone})
    return rows + gone


def refresh(desk, provider_id, transport=None, fetch=None):
    """Relit la liste du fournisseur. Un modèle disparu sort des nouvelles sélections et garde son historique."""
    from .ai_gateway import provider_registry
    provider = provider_registry(desk.c).get(provider_id) or {}
    kind = provider.get('type') or ('chatgpt' if provider_id == 'chatgpt' else '')
    read_at = _now()
    if kind == 'chatgpt':
        from . import chatgpt5662
        rows = chatgpt5662.refresh_models(desk, transport)
        acc = chatgpt5662.account(desk)
        account, workspace = str(acc.get('subject') or ''), str(acc.get('espace') or '')
    elif kind == 'ollama':
        from . import modeles5681
        rows, account, workspace = modeles5681.list_models(desk, provider_id, True, fetch)['modeles'], 'local', ''
    elif kind:
        from . import modeles5681
        rows, account, workspace = modeles5681.list_models(desk, provider_id, True, fetch)['modeles'], '', ''
    else:
        raise Stop('fournisseur_ia_absent')
    built = [_row(kind, r, account, workspace, read_at) for r in rows]
    stored = _store(desk, provider_id, account, workspace, built)
    desk.audit('catalogue_ia_lu', {'provider': provider_id, 'modeles': len(built), 'donnees_transmises': 'aucune'})
    return stored


def _read(state_dir, key):
    path = Path(str(state_dir or '')) / 'desk.sqlite3'
    if not path.is_file():
        return None
    db = sqlite3.connect(path, timeout=5)
    try:
        row = db.execute('SELECT value FROM settings WHERE key=?', (key,)).fetchone()
    finally:
        db.close()
    try:
        return json.loads(row[0]) if row else None
    except (TypeError, ValueError):
        return None


def entries(state_dir, provider_id):
    return (_read(state_dir, KEY + str(provider_id)) or {}).get('modeles') or []


def active(state_dir, provider_id):
    return [r for r in entries(state_dir, provider_id) if r.get('statut') == 'actif']


def mark_refused(state_dir, provider_id, slug, capability):
    """Une capacité refusée par le fournisseur n'est plus envoyée pour ce modèle (preuve conservée) ; aucune boucle de tentatives."""
    path = Path(str(state_dir or '')) / 'desk.sqlite3'
    if not path.is_file():
        return
    db = sqlite3.connect(path, timeout=5)
    try:
        row = db.execute('SELECT value FROM settings WHERE key=?', (KEY + str(provider_id),)).fetchone()
        if not row:
            return
        data = json.loads(row[0])
        for item in data.get('modeles') or []:
            if item.get('slug') == slug:
                caps = item.setdefault('capacites', {})
                if capability == 'reasoning':
                    caps['efforts'] = []
                caps['preuve'] = 'refus_fournisseur'
        db.execute('UPDATE settings SET value=? WHERE key=?', (json.dumps(data, ensure_ascii=False), KEY + str(provider_id)))
        db.commit()
    finally:
        db.close()


def prices(provider, row):
    """(entrée, cache, sortie, unité) pour ce modèle : référence datée, sinon tarif saisi pour le modèle réglé du fournisseur."""
    kind = provider.get('type', 'ollama')
    ref = reference(kind, row.get('slug'))
    if ref:
        return ref['entree'], ref['cache'], ref['sortie'], ref['unite']
    if row.get('slug') == provider.get('model') and (provider.get('input_usd_per_million') or provider.get('output_usd_per_million')):
        return float(provider.get('input_usd_per_million') or 0), None, float(provider.get('output_usd_per_million') or 0), 'usd'
    return None, None, None, UNITS.get(kind, 'usd')
