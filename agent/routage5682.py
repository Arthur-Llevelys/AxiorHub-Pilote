"""5.6.82 : service de routage (RoutingService). Choisit modèle et effort pour chaque étape, en six étapes :

1. pause, annulation, dossier et autorisation d'envoi, avant tout appel ;
2. retrait des fournisseurs et modèles incompatibles (mode, capacités, contexte, limites connues) ;
3. un calcul ou une date relevée n'appelle pas le modèle : ces travaux passent par leurs moteurs déterministes, en amont de ce service ;
4. candidats du niveau exigé ; sans mesure du banc, règle prudente documentée et choix marqué « provisoire » ;
5. parmi eux, consommation estimée la plus basse (le local d'abord, puis l'abonnement, puis l'API) ;
6. effort, budget et décision enregistrés AVANT la génération, puis nouveau contrôle au moment de l'appel.

On ne fait pas essayer tous les modèles du moins capable au plus capable : l'escalade est unique par étape, à une frontière stable, et ne
suit qu'un contrôle précis (schéma invalide, sortie incomplète, réponse vide). Une pause, une annulation, une erreur d'authentification ou
une limite n'autorisent aucune escalade et aucun changement de fournisseur.
"""
from datetime import datetime, timedelta, timezone
import hashlib
import json
from pathlib import Path
import secrets
import sqlite3

from .common import Stop
from . import catalogue5682, conso5682, politique5682, tache5682

SCHEMA = '''
CREATE TABLE IF NOT EXISTS decisions_5682(
  id INTEGER PRIMARY KEY, uid TEXT NOT NULL UNIQUE, at TEXT NOT NULL, mission TEXT NOT NULL, etape TEXT NOT NULL, proprietaire TEXT NOT NULL,
  dossier_h TEXT NOT NULL, fonction TEXT NOT NULL, type_tache TEXT NOT NULL, niveau TEXT NOT NULL, fournisseur TEXT NOT NULL, modele TEXT NOT NULL,
  effort TEXT NOT NULL, raisons TEXT NOT NULL, raison_courte TEXT NOT NULL, mode TEXT NOT NULL, politique_version INTEGER NOT NULL,
  estimation REAL, unite TEXT NOT NULL, provisoire INTEGER NOT NULL, escalade_de TEXT NOT NULL DEFAULT '', automatique INTEGER NOT NULL DEFAULT 0,
  etat TEXT NOT NULL, code TEXT NOT NULL DEFAULT '',
  fini TEXT NOT NULL DEFAULT '');
CREATE INDEX IF NOT EXISTS decisions_5682_at ON decisions_5682(at);
'''
ESCALATE_ON = frozenset(('json_ia_invalide', 'schema_ia_invalide', 'generation_ia_incomplete', 'reponse_ia_vide'))
# Mode automatique : panne, limite ou plafond d'une connexion : repli sur une autre connexion cochée, jamais au-delà de cette liste.
FALLBACK_ON = frozenset(('fournisseur_ia_injoignable', 'moteur_ollama_injoignable', 'modele_ollama_absent', 'generation_ia_delai_depasse', 'quota_fournisseur_depasse',
                         'budget_mensuel_fournisseur_depasse', 'budget_requete_fournisseur_depasse', 'chatgpt_limite_usage_atteinte', 'chatgpt_indisponible'))
OUTPUT_TOKENS = {'low': 1, 'medium': 1.5, 'high': 2}
KEEP_DAYS = 180
LOCAL_LEVELS = {'fast': 'econome', 'complex': 'standard', 'control': 'standard'}


def _connect(state_dir):
    db = sqlite3.connect(Path(state_dir) / 'desk.sqlite3', timeout=10)
    db.row_factory = sqlite3.Row
    db.executescript(SCHEMA)
    return db


def _mission():
    """Racine du travail en cours : les enfants et les reprises partagent le même budget."""
    from .arret5643 import current_job
    job = current_job()
    if not job or not job.get('job_id') or not job.get('state_dir'):
        return ''
    db = sqlite3.connect(Path(job['state_dir']) / 'desk.sqlite3', timeout=5)
    try:
        ident = job['job_id']
        for _ in range(12):
            row = db.execute('SELECT parent_job_id FROM jobs WHERE id=?', (ident,)).fetchone()
            if not row or not row[0]:
                break
            ident = row[0]
    except sqlite3.Error:
        pass
    finally:
        db.close()
    return str(ident)


def _automatic():
    from .arret5643 import current_job
    return bool((current_job() or {}).get('automatic'))


def _matters(config, payload, messages):
    from .policy567 import matter_ids
    return matter_ids(config, [payload, messages])


def _models_for(config, rule, provider_id, provider):
    kind = provider.get('type', 'ollama')
    if kind == 'ollama':
        out = []
        local = config.get('ollama') or {}
        routes = config.get('model_routing') or {}
        for role, level in LOCAL_LEVELS.items():
            slug = str(routes.get(role + '_model') or local.get('model') or '') if str(routes.get(role + '_provider') or 'ollama') == 'ollama' else str(local.get('model') or '')
            if slug and not any(r['slug'] == slug for r in out):
                out.append({'slug': slug, 'nom': slug, 'niveau': level, 'statut': 'actif', 'capacites': {'contexte': None, 'efforts': []}})
        return out
    rows = catalogue5682.active(config.get('state_dir'), provider_id)
    if kind in ('openai', 'openrouter') and provider.get('model'):
        # Passerelles sans famille connue : le modèle réglé par l'administrateur compte comme standard.
        slug = str(provider['model'])
        rows = [({**r, 'niveau': r.get('niveau') or 'standard'} if r['slug'] == slug else r) for r in rows]
        if not any(r['slug'] == slug for r in rows):
            rows = rows + [{'slug': slug, 'nom': slug, 'niveau': 'standard', 'statut': 'actif', 'capacites': {'contexte': None, 'efforts': []}}]
    return rows


def _benchmarked(config, purpose, provider_id, slug):
    from .hybrid400 import _benchmark_recommendation
    for row in _benchmark_recommendation(config, purpose).get('all', []):
        if row['provider'] == provider_id and row['model'] == slug and int(row.get('samples') or 0) >= 3:
            return True
    return False


def _rank_level(wanted, row, allow_max):
    level = row.get('niveau') or ''
    if level not in catalogue5682.NIVEAUX or (level == 'maximal' and not allow_max):
        return None
    gap = catalogue5682.NIVEAUX.index(level) - catalogue5682.NIVEAUX.index(wanted)
    return gap if gap >= 0 else None


def decide(config, purpose, stage='', payload=None, messages=None, max_tokens=3500, needs_json=False, escalate_from=None, owner='', skip=()):
    """Décision complète et enregistrée. Rend le dictionnaire de décision ; Stop si aucun candidat ne convient (le travail va « À vérifier »)."""
    from .ai_gateway import provider_registry
    from .arret5643 import reason_to_stop
    state_dir = config.get('state_dir')
    rule = politique5682.read_fresh(state_dir) or politique5682.read(config)
    if not rule:
        raise Stop('politique_ia_absente')
    code = reason_to_stop(state_dir)
    if code:
        raise Stop(code)
    if rule['mode'] == 'chatgpt':
        from . import chatgpt5682 as _plan
        _plan.check_deferred(state_dir)
    matters = _matters(config, payload, messages)
    matter = sorted(matters)[0] if matters else ''
    from .hybrid400 import policy as hybrid_policy
    from . import chatgpt5682
    reserved = bool(matters & set(hybrid_policy(config)['excluded_matters']))
    mission = _mission()
    prof = tache5682.profile(purpose, stage, payload, messages, rule['preference'], matter, owner, needs_json=needs_json)
    wanted = prof['niveau']
    excluded = []
    if escalate_from:
        order = catalogue5682.NIVEAUX
        wanted = order[min(order.index(escalate_from['niveau']) + 1, 2 if not rule['niveau_maximal'] else 3)]
    registry = provider_registry(config)
    found = []
    for provider_id in politique5682.candidates(config, rule):
        provider = registry[provider_id]
        kind = provider.get('type', 'ollama')
        if provider_id in skip:
            continue
        if kind != 'ollama' and reserved:            # dossier exclu des envois : le modèle local seul, s'il est permis
            excluded.append('politique_externe_dossier_exclu:' + provider_id)
            continue
        if kind == 'chatgpt' and rule['mode'] == 'auto' and chatgpt5682.deferred(state_dir):
            excluded.append('chatgpt_limite_usage_atteinte:' + provider_id)
            continue
        rows = _models_for(config, rule, provider_id, provider)
        forced = (rule['modeles'].get(provider_id) or {}).get(wanted)
        if forced not in [r['slug'] for r in rows]:    # modèle retiré, ou réglé pour un autre compte : le choix automatique reprend
            forced = None
        for row in rows:
            slug = row['slug']
            caps = row.get('capacites') or {}
            gap = 0 if forced == slug else _rank_level(wanted, row, rule['niveau_maximal'])
            if forced and forced != slug:
                gap = None
            if gap is None:
                continue
            context = caps.get('contexte')
            if context and prof['jetons_estimes'] + max_tokens > context:
                excluded.append('contexte_insuffisant:' + slug)
                continue
            if needs_json and caps.get('structure') is False:
                excluded.append('sortie_structuree_absente:' + slug)
                continue
            if kind == 'ollama' and wanted in ('avance', 'maximal') and not _benchmarked(config, purpose, provider_id, slug):
                excluded.append('niveau_local_non_mesure:' + slug)
                continue
            prices = catalogue5682.prices(provider, row)
            if kind not in ('ollama', 'chatgpt') and prices[0] is None:
                excluded.append('tarif_inconnu:' + slug)
                continue
            if kind == 'ollama':
                prices = (0.0, None, 0.0, 'aucune')
            elif kind == 'chatgpt' and prices[3] == 'usd':
                prices = (None, None, None, 'credits')
            found.append((provider_id, provider, row, gap, prices))
    if not found:
        raise Stop('aucun_modele_admissible' if not excluded else excluded[0].split(':')[0])

    def cost(item):
        unit = item[4][3]
        channel = 0 if unit == 'aucune' else (1 if unit == 'credits' else 2)
        estimate = conso5682.estimate(item[4], prof['jetons_estimes'], max_tokens)
        return (item[3], channel, float('inf') if estimate is None else estimate, catalogue5682.NIVEAUX.index(item[2].get('niveau') or 'standard'))
    provider_id, provider, row, gap, prices = min(found, key=cost)
    if escalate_from and row['slug'] == escalate_from.get('modele') and provider_id == escalate_from.get('fournisseur'):
        raise Stop('escalade_sans_modele_superieur')
    automatic = _automatic()
    if automatic and prices[3] != 'aucune':       # les demandes de l'avocat ne passent jamais par ce plafond
        from .sobriete5682 import check_daily
        check_daily(state_dir, rule, row.get('niveau') or wanted)
    allowed = (row.get('capacites') or {}).get('efforts') or []
    effort = prof['effort'] if prof['effort'] in allowed else ''
    tokens_out = int(max_tokens * OUTPUT_TOKENS.get(prof['effort'], 1))
    estimate = conso5682.estimate(prices, prof['jetons_estimes'], tokens_out)
    limit = rule['plafond_mission_credits'] if prices[3] == 'credits' else rule['plafond_mission_usd']
    uid = secrets.token_hex(12)
    reservation = conso5682.reserve(state_dir, mission, estimate, prices[3], limit, uid)
    provisional = provider.get('type') != 'ollama' and not _benchmarked(config, purpose, provider_id, row['slug'])
    short = tache5682.short_reason({**prof, 'niveau': row.get('niveau') or wanted}, bool(escalate_from))
    if not effort and prof['effort']:
        short += ' Effort non réglable sur ce modèle : le modèle a été choisi en conséquence.'
    decision = {'uid': uid, 'mission': mission, 'etape': stage or 'completion', 'proprietaire': owner, 'fonction': purpose,
                'dossier_h': hashlib.sha256(matter.encode()).hexdigest()[:16] if matter else '', 'type_tache': prof['type_tache'],
                'niveau': row.get('niveau') or wanted, 'fournisseur': provider_id, 'type_fournisseur': provider.get('type', 'ollama'), 'modele': row['slug'],
                'nom_modele': row.get('nom') or row['slug'], 'effort': effort, 'raisons': prof['raisons'] + excluded[:6], 'raison_courte': short,
                'mode': rule['mode'], 'politique_version': rule['version'], 'estimation': estimate, 'unite': prices[3], 'provisoire': provisional,
                'escalade_de': (escalate_from or {}).get('uid', ''), 'automatique': automatic, 'prix': prices, 'reservation': reservation, 'sources': prof['sources'],
                'sources_manquantes': prof['sources_manquantes']}
    _record(state_dir, decision)
    return decision


def _record(state_dir, d):
    db = _connect(state_dir)
    try:
        db.execute('INSERT INTO decisions_5682(uid,at,mission,etape,proprietaire,dossier_h,fonction,type_tache,niveau,fournisseur,modele,effort,raisons,'
                   'raison_courte,mode,politique_version,estimation,unite,provisoire,escalade_de,automatique,etat) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)',
                   (d['uid'], datetime.now(timezone.utc).isoformat(), d['mission'], d['etape'], d['proprietaire'], d['dossier_h'], d['fonction'],
                    d['type_tache'], d['niveau'], d['fournisseur'], d['modele'], d['effort'], json.dumps(d['raisons'], ensure_ascii=False), d['raison_courte'],
                    d['mode'], d['politique_version'], d['estimation'], d['unite'], int(d['provisoire']), d['escalade_de'], int(d['automatique']), 'decidee'))
        db.execute('DELETE FROM decisions_5682 WHERE at<?', ((datetime.now(timezone.utc) - timedelta(days=KEEP_DAYS)).isoformat(),))
        db.commit()
    finally:
        db.close()


def close(state_dir, decision, state, code=''):
    db = _connect(state_dir)
    try:
        db.execute('UPDATE decisions_5682 SET etat=?,code=?,fini=? WHERE uid=?', (state, str(code)[:80], datetime.now(timezone.utc).isoformat(), decision['uid']))
        db.commit()
    finally:
        db.close()


def child_config(parent_cfg, decision, config):
    """Configuration du transport retenu : le fournisseur réellement appelé est celui de la décision enregistrée."""
    from .ai_gateway import provider_registry
    provider = provider_registry(config)[decision['fournisseur']]
    keep = {k: v for k, v in parent_cfg.items() if k in ('purpose', 'temperature', 'sampling', 'profile', 'profile_version', 'subprofile', 'state_dir', 'pseudo',
                                                         'skill_instructions', 'external_policy_config', 'external_context', 'timeout_seconds', 'max_context_chars')}
    cfg = {**dict(provider), **keep, 'provider_id': decision['fournisseur'], 'provider_type': decision['type_fournisseur'], 'model': decision['modele'],
           'effort5682': decision['effort'], 'decision5682': decision['uid'], 'mission5682': decision['mission'], 'prix5682': decision['prix'], 'sans_liste5682': True}
    if decision['type_fournisseur'] not in ('ollama', 'chatgpt') and decision['unite'] == 'usd':
        cfg['input_usd_per_million'], cfg['output_usd_per_million'] = decision['prix'][0], decision['prix'][2]
    return cfg


def run(parent, messages, temperature, max_tokens, json_schema, stage, data):
    """Exécute une étape : décision, appel, au plus une escalade justifiée. Rien n'est concaténé : l'étape recommence entièrement sur le nouveau modèle."""
    from .arret5643 import STOP_CODES
    config = parent.cfg['external_policy_config']
    state_dir = config.get('state_dir')
    purpose = parent.cfg.get('purpose', 'assistant')
    escalate = getattr(parent, 'escalade5682', None)
    owner = str(parent.cfg.get('proprietaire5682') or '')
    try:
        decision = decide(config, purpose, stage, data, messages, max_tokens, json_schema is not None, escalate, owner)
    except Stop as error:
        if str(error) != 'escalade_sans_modele_superieur':
            raise
        decision = decide(config, purpose, stage, data, messages, max_tokens, json_schema is not None, None, owner)   # déjà au plus haut : même modèle
    used_escalation = bool(escalate)
    tried = []
    while True:
        cfg = child_config(parent.cfg, decision, config)
        cfg['trace5682'] = (stage, data)
        parent.last_decision5682 = decision
        parent.last_provider, parent.last_model = decision['fournisseur'], decision['modele']
        try:
            value = parent._call_child(cfg, 'complete', messages, temperature, max_tokens, json_schema)
        except Stop as error:
            code = str(error)
            close(state_dir, decision, 'erreur', code)
            conso5682.settle(state_dir, decision['reservation'], 'liberee' if code in STOP_CODES else 'incertain')
            if code not in STOP_CODES and code in FALLBACK_ON and decision['mode'] == 'auto':
                tried.append(decision['fournisseur'])
                try:
                    decision = decide(config, purpose, stage, data, messages, max_tokens, json_schema is not None, None, owner, tried)
                except Stop:
                    raise error from None
                continue
            if code in STOP_CODES or code not in ESCALATE_ON or used_escalation:
                raise
            used_escalation = True
            try:
                decision = decide(config, purpose, stage, data, messages, max_tokens, json_schema is not None, decision, owner)
            except Stop:
                raise error from None
            continue
        close(state_dir, decision, 'terminee')
        conso5682.settle(state_dir, decision['reservation'], 'consomme')
        return value


def summary(decision):
    """Détail « Choix de l'IA » : modèle réel, effort, raison courte, origine des sources, consommation connue. Pas de raisonnement interne."""
    if not decision:
        return None
    unit = {'credits': 'crédits (repère)', 'usd': '$ estimés', 'aucune': ''}.get(decision.get('unite'), '')
    amount = decision.get('estimation')
    return {'modele': decision.get('nom_modele') or decision.get('modele'), 'fournisseur': decision.get('fournisseur'),
            'effort': tache5682.EFFORT_LABELS.get(decision.get('effort') or '', 'non réglable'), 'raison': decision.get('raison_courte'),
            'sources': decision.get('sources', 0), 'consommation': ('%s %s' % (('%.3f' % amount).replace('.', ','), unit)).strip() if amount is not None else 'Non fournie',
            'provisoire': bool(decision.get('provisoire')), 'canal': {'credits': 'Utilise votre abonnement ChatGPT', 'usd': 'API facturée à l’usage', 'aucune': 'Modèle local'}.get(decision.get('unite'), '')}


def for_job(state_dir, job_id):
    """Dernier choix d'un travail, pour le détail « Choix de l'IA » (rien si le mode historique est en place)."""
    if not state_dir or not job_id:
        return None
    db = _connect(state_dir)
    try:
        row = db.execute('SELECT * FROM decisions_5682 WHERE mission=? ORDER BY id DESC LIMIT 1', (str(job_id),)).fetchone()
    finally:
        db.close()
    if not row:
        return None
    return summary({**dict(row), 'nom_modele': row['modele'], 'sources': 0})


def recent(state_dir, limit=30):
    db = _connect(state_dir)
    try:
        rows = db.execute('SELECT at,etape,fonction,type_tache,niveau,fournisseur,modele,effort,raison_courte,estimation,unite,provisoire,escalade_de,etat,code '
                          'FROM decisions_5682 ORDER BY id DESC LIMIT ?', (max(1, min(int(limit), 200)),)).fetchall()
        return [dict(r) for r in rows]
    finally:
        db.close()
