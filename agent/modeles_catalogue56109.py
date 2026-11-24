"""5.6.109 (correctif de production, lot E) : un modèle externe n'est retenu que s'il figure au catalogue renvoyé par la clé du compte ;
un refus du fournisseur est expliqué et journalisé.

Incident (5.6.108, décrit sans donnée du cabinet) : clé Mistral reconnue, mode mixte routé vers « mistral-large-4 », toute génération refusée
avec le seul message « http 400 ». Causes établies dans le code :
- ``modeles5681.apply_mixte`` (clé déjà enregistrée) écrivait ``MIXTE_MODEL`` sur la connexion AVANT de lire le catalogue, et ne retenait que
  ce nom exact (ou ses versions datées) ; ``ia540.apply_mixed``, ``ia540.apply_external_all``, ``ai_gateway.save_route`` et l'installation
  (``setup560``) acceptaient un nom saisi ou écrit en dur : le « test » de ``Model`` ne vérifie que la clé (``GET /models``), jamais que le
  modèle est listé ;
- ``Model._translate_error`` laissait ``http_400`` tel quel ; le type, le code et le paramètre de l'erreur, déjà extraits par
  ``erreurs5634.provider_error``, n'étaient jamais montrés ; aucun journal du service n'en gardait trace.

Ce module : ``choisir`` (préréglage : modèle DU catalogue, par famille et niveau, alias « -latest » s'il est listé, sinon la version la plus
récente ; refus clair avec la liste sinon), ``exiger`` (un modèle saisi doit être listé), ``garde_modele`` (dernier rempart dans ``Model`` :
un modèle absent du dernier catalogue connu n'est jamais envoyé), ``a_choisir`` (affichage « Modèle à choisir », lecture seule),
``refus`` (400, 404, 422 → motif lisible d'après les seuls champs courts du fournisseur), ``journaliser_refus`` (une ligne sans contenu).
Aucun identifiant de modèle n'est écrit ici : seul le catalogue de la clé en fournit.
"""
from datetime import datetime, timezone
import re
import sys
from pathlib import Path

from .common import Stop
from .erreurs5634 import Qualified

TYPES_A_CATALOGUE = ('mistral', 'anthropic', 'openai', 'openrouter')    # Gemini a sa propre garde (``gemini56109.garde_connexion``)
MAX_PROPOSES = 40
JETON = re.compile(r'[A-Za-z0-9_.\[\]-]{1,60}')
CODES = ('modele_absent_du_catalogue', 'modele_inconnu_du_fournisseur', 'parametre_refuse_par_fournisseur', 'echantillonnage_refuse_par_fournisseur',
         'requete_refusee_par_fournisseur')
MESSAGES = {
    'modele_absent_du_catalogue': 'Modèle à choisir : ce modèle ne figure pas dans le catalogue renvoyé par la clé de cette connexion. Choisissez un modèle listé dans Paramètres › Modèles d’IA. Rien n’a été envoyé.',
    'modele_inconnu_du_fournisseur': 'Modèle inconnu du fournisseur : il refuse ce nom de modèle. Choisissez un modèle listé par votre clé dans Paramètres › Modèles d’IA.',
    'echantillonnage_refuse_par_fournisseur': 'Paramètres d’échantillonnage refusés par le fournisseur (température, top_p) : un nouvel essai identique échouerait.',
    'parametre_refuse_par_fournisseur': 'Paramètre refusé par le fournisseur : retirez-le du profil de réglages ou choisissez un autre modèle.',
    'requete_refusee_par_fournisseur': 'Requête refusée par le fournisseur : un nouvel essai identique échouerait. Vérifiez le modèle choisi et ses options.',
}


# ------------------------------------------------------------------------------------------------ catalogue de la connexion
def _type(config, provider_id):
    from .ai_gateway import provider_registry
    return str((provider_registry(config).get(str(provider_id)) or {}).get('type') or '')


def connus(state_dir, provider_id):
    """Identifiants actifs du dernier catalogue lu pour cette connexion (lecture seule) ; None si aucun n'a jamais été lu."""
    from . import catalogue5682
    rows = catalogue5682.entries(state_dir, provider_id)
    if not rows:
        return None
    return [str(r.get('slug')) for r in rows if r.get('statut') == 'actif' and r.get('slug')]


def relire(desk, provider_id, fetch=None):
    """Catalogue relu chez le fournisseur avec la clé enregistrée (lecture seule chez lui, aucune donnée de dossier) et conservé."""
    from . import catalogue5682
    return [str(r['slug']) for r in catalogue5682.refresh(desk, provider_id, fetch=fetch) if r.get('statut') == 'actif']


def _proposes(slugs):
    return sorted(slugs or [], key=str.lower)[:MAX_PROPOSES]


def exiger(desk, provider_id, model, fetch=None, relecture=True):
    """Un modèle saisi ou préréglé n'est retenu que s'il est listé par la clé. Catalogue inconnu : relu une fois (si ``relecture``)."""
    kind = _type(desk.c, provider_id)
    if kind not in TYPES_A_CATALOGUE:
        return model
    slugs = connus(desk.c['state_dir'], provider_id)
    if (slugs is None or model not in slugs) and relecture:
        slugs = relire(desk, provider_id, fetch)
    if slugs is not None and model not in slugs:
        raise Qualified('modele_absent_du_catalogue', modele=str(model)[:160], proposes=_proposes(slugs))
    return model


def _version(slug):
    """Clé d'ordre : date de version (quatre chiffres, AAMM) d'abord, puis les autres nombres du nom."""
    nombres = [int(x) for x in re.findall(r'\d+', slug)]
    dates = [n for n, brut in zip(nombres, re.findall(r'\d+', slug)) if len(brut) == 4]
    return (max(dates) if dates else 0, tuple(nombres))


def choisir(kind, slugs, famille, niveau):
    """Préréglage : modèle du catalogue de la famille (préfixe) et du niveau demandés. Alias « <famille>-latest » s'il est listé, sinon la
    version la plus récente. Aucun : refus clair, liste des modèles proposés jointe ; rien n'est enregistré par l'appelant."""
    from .catalogue5682 import level_of
    famille = famille.lower()
    candidats = [s for s in slugs or [] if s.lower().startswith(famille) and level_of(kind, s) == niveau]
    if not candidats:
        raise Qualified('modele_ou_route_fournisseur_absent', famille=famille, proposes=_proposes(slugs))
    alias = famille + '-latest'
    if alias in candidats:
        return alias
    return max(candidats, key=_version)


def garde_modele(cfg):
    """Dans ``Model`` (fournisseurs à clé API) : un modèle absent du dernier catalogue connu de la connexion n'est jamais envoyé.
    Catalogue jamais lu : rien n'est supposé (la lecture se fait à l'enregistrement de la clé). Contrôles de connexion exclus."""
    kind = str(cfg.get('provider_type') or cfg.get('type') or '')
    if kind not in TYPES_A_CATALOGUE or cfg.get('diagnostic5682') or cfg.get('test5696') or not cfg.get('state_dir'):
        return
    slugs = connus(cfg['state_dir'], cfg.get('provider_id') or cfg.get('id') or kind)
    model = str(cfg.get('model') or '')
    if slugs is not None and model not in slugs:
        raise Qualified('modele_absent_du_catalogue', modele=model[:160], proposes=_proposes(slugs))


def a_choisir(state_dir, provider_id, model):
    """« Modèle à choisir » : le modèle en service n'est pas dans le dernier catalogue connu (affichage, lecture seule)."""
    slugs = connus(state_dir, provider_id)
    return slugs is not None and str(model or '') not in slugs


# ------------------------------------------------------------------------------------------------ refus du fournisseur (400, 404, 422)
def _jeton(value):
    texte = str(value if value is not None else '').strip()
    return texte if JETON.fullmatch(texte) and not texte.lower().startswith(('sk-', 'aiza')) else ''


def champs(detail):
    """Champs courts du refus (type, code, paramètre, statut) ; jamais le message libre, qui peut recopier le texte envoyé."""
    pe = (detail or {}).get('provider_error') if isinstance((detail or {}).get('provider_error'), dict) else {}
    return {k: _jeton(pe.get(k)) for k in ('type', 'code', 'param', 'status') if _jeton(pe.get(k))}


def refus(cfg, error):
    """Refus 400, 404 ou 422 d'un fournisseur externe → motif lisible, détail conservé (statut, champs courts, modèle demandé).
    ``None`` : aucun indice (le 404 garde alors son code historique)."""
    code = str(error).split(':')[0]
    if code not in ('http_400', 'http_404', 'http_422'):
        return None
    detail = dict(getattr(error, 'detail', None) or {})
    c = champs(detail)
    texte = ' '.join(c.get(k, '') for k in ('type', 'code', 'status')).lower()
    param = c.get('param', '')
    modele = str((cfg or {}).get('model') or '')[:160]
    extra = {k: v for k, v in detail.items() if k not in ('modele', 'param', 'type_refus', 'code_refus')}
    if 'sampling' in texte or param.lower() in ('top_p', 'temperature'):   # 5.6.109 : refus d'échantillonnage (glouton : température 0 sans top_p 1)
        return Qualified('echantillonnage_refuse_par_fournisseur', **extra, modele=modele, glouton='greedy' in texte, param=param,
                         type_refus=c.get('type', ''), code_refus=c.get('code', ''))
    if 'model' in texte or param.lower() == 'model':
        return Qualified('modele_inconnu_du_fournisseur', **extra, modele=modele, type_refus=c.get('type', ''), code_refus=c.get('code', ''))
    if param:
        return Qualified('parametre_refuse_par_fournisseur', **extra, modele=modele, param=param, type_refus=c.get('type', ''),
                         code_refus=c.get('code', ''))
    if code == 'http_404':
        return None
    return Qualified('requete_refusee_par_fournisseur', **extra, modele=modele, type_refus=c.get('type', '') or c.get('status', ''),
                     code_refus=c.get('code', ''))


def expliquer(code, d):
    """Message de ``erreurs5634.explain`` pour ces codes : modèle, paramètre, type et code du fournisseur, liste des modèles proposés."""
    d = d or {}
    statut = (' (%s)' % d['remote_status']) if d.get('remote_status') else ''
    modele = (' « %s »' % d['modele']) if d.get('modele') else ''
    proposes = ', '.join(d.get('proposes') or [])
    if code == 'modele_ou_route_fournisseur_absent':
        return ('Aucun modèle de la famille « %s » n’est proposé par la clé de cette connexion : rien n’a été enregistré. Modèles proposés : %s.' % (
            d.get('famille', ''), proposes or 'aucun'))
    if code == 'modele_absent_du_catalogue':
        return ('Modèle à choisir : le modèle%s ne figure pas dans le catalogue renvoyé par la clé de cette connexion. Rien n’a été envoyé ni '
                'enregistré. Modèles proposés : %s.' % (modele, proposes or 'aucun'))
    motif = ' '.join(x for x in (('type %s' % d['type_refus']) if d.get('type_refus') else '',
                                  ('code %s' % d['code_refus']) if d.get('code_refus') else '') if x)
    motif = (' — motif du fournisseur : %s' % motif) if motif else ''
    if code == 'echantillonnage_refuse_par_fournisseur':
        precision = ' (température 0 : top_p doit valoir 1)' if d.get('glouton') else ((' : « %s »' % d['param']) if d.get('param') else '')
        return ('Paramètres d’échantillonnage refusés par le fournisseur%s%s%s (modèle%s). Un nouvel essai identique échouerait.' % (
            precision, statut, motif, modele))
    if code == 'modele_inconnu_du_fournisseur':
        return 'Modèle inconnu du fournisseur%s : le modèle%s est refusé%s. Choisissez un modèle listé par votre clé dans Paramètres › Modèles d’IA.' % (
            statut, modele, motif)
    if code == 'parametre_refuse_par_fournisseur':
        return ('Paramètre refusé par le fournisseur%s : « %s »%s (modèle%s). Retirez ce réglage du profil ou choisissez un autre modèle.' % (
            statut, d.get('param', ''), motif, modele))
    return 'Requête refusée par le fournisseur%s%s (modèle%s) : un nouvel essai identique échouerait. Vérifiez le modèle choisi et ses options.' % (
        statut, motif, modele)


# ------------------------------------------------------------------------------------------------ journal du service
def journaliser_refus(cfg, etape, code, statut, champs_courts):
    """Une ligne par refus du fournisseur (statut 400 et plus) : sortie d'erreur du service (journal systemd) et « erreurs-interface.log ».
    Fournisseur, modèle, fonction, code et champs courts seulement : jamais de texte envoyé, de réponse, de clé ni de message libre."""
    try:
        if int(statut or 0) < 400:
            return
        cfg = cfg or {}
        valeurs = {'connecteur': cfg.get('provider_id') or cfg.get('id') or '', 'type_connecteur': cfg.get('provider_type') or cfg.get('type') or '',
                   'modele': cfg.get('model') or '', 'fonction': cfg.get('purpose') or '', 'etape': etape or '', 'code': code, 'statut': int(statut)}
        valeurs.update({'refus_' + k: v for k, v in (champs_courts or {}).items()})
        morceaux = []
        for k, v in valeurs.items():
            v = str(v)[:80]
            if v and re.fullmatch(r'[A-Za-zÀ-ÿ0-9_.:/\[\] -]{1,80}', v):
                morceaux.append('%s=%s' % (k, v.replace(' ', '_')))
        ligne = '=== %s refus_fournisseur_ia %s' % (datetime.now(timezone.utc).replace(microsecond=0).isoformat(), ' '.join(morceaux))
        try:
            flux = sys.stderr or sys.__stderr__
            if flux is not None:
                print(ligne, file=flux, flush=True)
        except Exception:   # noqa: BLE001 — le journal ne change jamais l'issue de l'appel
            pass
        etat = str(cfg.get('state_dir') or '')
        if etat and Path(etat).is_dir():
            cible = Path(etat) / 'erreurs-interface.log'
            if cible.exists() and cible.stat().st_size > 2_000_000:
                cible.replace(cible.with_suffix('.log.1'))
            with open(cible, 'a', encoding='utf-8') as flux:
                flux.write(ligne + '\n')
    except Exception:   # noqa: BLE001
        pass


# ------------------------------------------------------------------------------------------------ essai d'une requête réelle (point 6)
TEXTE_FICTIF = {'incoming': 'Bonjour Maître, pouvez-vous me confirmer la date du prochain rendez-vous ? (courriel fictif de démonstration)',
                'intent': 'demande_precision', 'sources': [], 'instruction_avocat': ''}


def forme_requete(provider, kind, params, json_schema=True):
    """Éléments de forme d'une requête compatible OpenAI (aucun contenu) : format de sortie, paramètres transmis, nombre de messages système."""
    from .profil5634 import provider_options
    sent, ignores = provider_options(kind, params.get('temperature'), params.get('sampling') or {})
    if kind == 'gemini':
        sortie = 'consigne JSON dans le message système (aucun response_format)'
    elif not json_schema:
        sortie = 'texte'
    elif kind == 'anthropic':
        sortie = 'consigne JSON dans le message système'
    else:
        sortie = 'response_format=' + ('json_schema strict' if provider.get('structured_mode') == 'strict' else 'json_object')
    return {'sortie': sortie, 'parametres': sorted(sent), 'ignores': sorted(ignores)}


def essai_requete(desk, provider_id, owner=''):
    """Une requête de la même forme que la rédaction d'un courriel (consignes système, compétences actives, schéma JSON, réglages du profil
    de la fonction, borne de sortie), sur un courriel fictif, par ``Model`` (politique, pseudonymisation, budget, pause, journal). Rend le motif
    précis d'un refus (type, code, paramètre) ou l'acceptation ; aucune donnée de dossier, aucun résultat conservé."""
    import json
    from .ai_gateway import provider_registry
    from .extensions364 import active_skill_instructions
    from .model import BASE, MANUAL_DRAFT_FOURNISSEUR, MANUAL_DRAFT_PROMPT, Model, pseudo_sources
    from .profil5634 import resolve
    provider_id = str(provider_id or '')
    provider = provider_registry(desk.c).get(provider_id)
    if not provider or provider.get('type') in (None, 'ollama', 'chatgpt'):
        raise Stop('fournisseur_ia_absent')
    kind = str(provider.get('type'))
    route = (desk.c.get('model_routing') or {}).get('mail_drafting') or {}
    model = str(route.get('model') if route.get('provider') == provider_id and route.get('model') else provider.get('model') or '')
    params = resolve(desk.c, 'mail_drafting', 'complex')
    cfg = {**provider, 'id': provider_id, 'provider_id': provider_id, 'provider_type': kind, 'model': model, 'purpose': 'mail_drafting',
           'state_dir': desk.c['state_dir'], 'external_policy_config': desk.c, 'pseudo': pseudo_sources(desk.c),
           'temperature': params['temperature'], 'sampling': params['sampling'], 'skill_instructions': active_skill_instructions(desk.c, 'mail_drafting')}
    forme = forme_requete(provider, kind, params)
    decrit = 'modèle %s, %s, paramètres : %s, borne de sortie 3500 jetons' % (model, forme['sortie'], ', '.join(forme['parametres']) or 'aucun')
    messages = [{'role': 'system', 'content': BASE + MANUAL_DRAFT_PROMPT}, {'role': 'user', 'content': json.dumps(TEXTE_FICTIF, ensure_ascii=False)}]
    try:
        Model(cfg).complete(messages, None, 3500, MANUAL_DRAFT_FOURNISSEUR)
    except Stop as error:
        from .erreurs5634 import explain
        code = str(error).split(':')[0]
        desk.audit('essai_requete_fournisseur', {'provider': provider_id, 'modele': model, 'resultat': code, 'donnees_transmises': 'texte fictif'})
        return {'ok': False, 'code': code, 'forme': forme, 'noreload': True,
                'message': 'Requête refusée (%s) : %s' % (decrit, explain(code, getattr(error, 'detail', None)))}
    desk.audit('essai_requete_fournisseur', {'provider': provider_id, 'modele': model, 'resultat': 'acceptee', 'donnees_transmises': 'texte fictif'})
    return {'ok': True, 'forme': forme, 'noreload': True, 'message': 'Requête acceptée par le fournisseur (%s).' % decrit}


# ------------------------------------------------------------------------------------------------ modèles qui raisonnent : marge de sortie
# Constat de production (5.6.108, essai direct de l'administrateur, texte fictif) : 31 jetons de raisonnement sur 32 de sortie ; avec la borne de
# la rédaction, la réponse finissait en « length ». La sortie demandée doit couvrir raisonnement + réponse.
MARGE_RAISONNEMENT = 1024        # marge minimale ajoutée à la borne d'un modèle noté « raisonne »
PLAFOND_SORTIE = 32768           # borne absolue ; la réservation du budget (prix × borne) et les plafonds restent seuls juges du coût
PART_RAISONNEMENT = 0.5          # réponse coupée dont au moins cette part de la sortie est du raisonnement : « consommée en raisonnement »
# Paramètre officiel pour limiter le raisonnement, par fournisseur : aucun n'est établi ici (rien n'est inventé) ; une ligne l'ajouterait.
LIMITE_RAISONNEMENT = {}
MESSAGES['reponse_consommee_en_raisonnement'] = ('Le modèle a consommé sa réponse en raisonnement : même avec une marge de sortie plus grande, il '
                                                 'n’a pas rendu de texte complet. Réessayez plus tard ou routez cette fonction vers un autre modèle.')
MESSAGES['generation_ia_raisonnement_epuise'] = MESSAGES['reponse_consommee_en_raisonnement']


def _usage_sortie(result):
    usage = result.get('usage') if isinstance(result, dict) else None
    usage = usage if isinstance(usage, dict) else {}
    details = usage.get('completion_tokens_details') if isinstance(usage.get('completion_tokens_details'), dict) else {}
    r, s = details.get('reasoning_tokens'), usage.get('completion_tokens')
    return (r if type(r) is int and r > 0 else 0), (s if type(s) is int and s >= 0 else None)


def _jetons_raisonnement(result):
    return _usage_sortie(result)[0]


def noter_raisonnement(cfg, result):
    """Réponse où le fournisseur déclare des jetons de raisonnement : le modèle est noté « raisonne » au catalogue de la connexion, avec le plus
    grand nombre de jetons de raisonnement observé (marge apprise ; preuve : réponse du fournisseur). Ne lève jamais d'exception."""
    try:
        n = _jetons_raisonnement(result)
        if not n or not cfg.get('state_dir'):
            return
        import json
        import sqlite3
        from . import catalogue5682
        pid, model = str(cfg.get('provider_id') or cfg.get('id') or ''), str(cfg.get('model') or '')
        path = Path(str(cfg['state_dir'])) / 'desk.sqlite3'
        if not pid or not model or not path.is_file():
            return
        db = sqlite3.connect(path, timeout=5)
        try:
            row = db.execute('SELECT value FROM settings WHERE key=?', (catalogue5682.KEY + pid,)).fetchone()
            data = json.loads(row[0]) if row else None
            if not isinstance(data, dict):
                return
            for item in data.get('modeles') or []:
                caps = item.setdefault('capacites', {}) if item.get('slug') == model else None
                if caps is not None and (not caps.get('raisonnement') or int(caps.get('raisonnement_jetons') or 0) < n):
                    caps['raisonnement'] = 'reponse_fournisseur'
                    caps['raisonnement_jetons'] = max(n, int(caps.get('raisonnement_jetons') or 0))
                    db.execute('UPDATE settings SET value=? WHERE key=?', (json.dumps(data, ensure_ascii=False), catalogue5682.KEY + pid))
                    db.commit()
        finally:
            db.close()
    except Exception:   # noqa: BLE001
        pass


def plancher_sortie(cfg, max_tokens):
    """Borne de sortie d'un modèle noté « raisonne » : borne demandée + marge (le plus grand raisonnement observé, au moins
    ``MARGE_RAISONNEMENT``), au plus ``PLAFOND_SORTIE``. La réservation du budget suit cette borne : aucun plafond n'est contourné."""
    try:
        from . import catalogue5682
        for item in catalogue5682.entries(cfg.get('state_dir'), cfg.get('provider_id') or cfg.get('id') or ''):
            caps = item.get('capacites') or {}
            if item.get('slug') == cfg.get('model') and caps.get('raisonnement'):
                marge = max(int(caps.get('raisonnement_jetons') or 0), MARGE_RAISONNEMENT)
                return min(int(max_tokens) + marge, max(PLAFOND_SORTIE, int(max_tokens)))
    except Exception:   # noqa: BLE001 — sans catalogue lisible, la borne demandée reste
        pass
    return max_tokens


def raisonnement_epuise(cfg, result, max_tokens):
    """Réponse coupée (« length ») dont la sortie est surtout du raisonnement : erreur qui demande UNE reprise avec une borne plus grande."""
    r, s = _usage_sortie(result)
    if not r or not s or r < PART_RAISONNEMENT * s:
        return None
    suivant = min(max(2 * int(max_tokens), int(max_tokens) + 2 * r), PLAFOND_SORTIE)
    if suivant <= int(max_tokens):
        return Qualified('reponse_consommee_en_raisonnement', jetons_raisonnement=r, jetons_sortie=s)
    return Qualified('generation_ia_raisonnement_epuise', max_tokens_suivant=suivant, jetons_raisonnement=r, jetons_sortie=s)
