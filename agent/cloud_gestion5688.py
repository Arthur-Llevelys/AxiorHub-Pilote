"""5.6.88 : gestion des stockages cloud — connexions, préparation, contrôle d'aptitude, choix du stockage.

Le stockage ne change que sur décision d'un administrateur, après un contrôle d'aptitude réussi sur le compte réel ; une copie terminée ne bascule
jamais rien. Le retour à Nextcloud est une décision du même ordre et ne déplace aucun fichier.
"""
from datetime import datetime, timedelta, timezone
import json

from .common import Stop, clean_path
from . import cloud5688 as core

APTITUDE = 'cloud5688:aptitude:'
PREPARE = 'cloud5688:preparation:'
APTITUDE_DAYS = 30
SAFE_PARAMS = ('dossier_id', 'site', 'bibliotheque', 'equipe')


def _admin(role):
    if role != 'administrateur':
        raise Stop('role_insuffisant')


def connection(desk, provider):
    """Connexion du cabinet pour ce fournisseur (jamais une connexion personnelle) ; ``None`` si elle n'existe pas encore."""
    from . import connexions5652 as cx
    if provider not in core.PROVIDERS:
        raise Stop('fournisseur_inconnu')
    cx.ensure_schema(desk)
    return desk.db.execute("SELECT * FROM connexions5652 WHERE provider=? AND owner='cabinet' ORDER BY updated DESC LIMIT 1", (provider,)).fetchone()


def store_settings(desk, provider):
    """Réglages sans secret qui décrivent le stockage ; les identifiants retenus à la préparation s'y ajoutent."""
    row = connection(desk, provider)
    if not row:
        raise Stop('connexion_absente')
    params = json.loads(row['params_json'] or '{}')
    base = str(params.get('base') or '').strip().strip('/')
    out = {'provider': provider, 'connection': row['id'], 'base': clean_path('/' + base) if base else core.DEFAULT_BASE, **{k: params[k] for k in SAFE_PARAMS if params.get(k)}}
    return {**out, **(desk.settings(PREPARE + provider, {}) or {})}


def client_for(desk, key, max_bytes=None):
    """Accès à un stockage désigné : ``nextcloud`` (Nextcloud ou dossier local, tel que configuré) ou un fournisseur cloud relié."""
    from .dav import DAV
    cfg = {k: v for k, v in (desk.c.get('nextcloud') or {}).items() if k != core.KEY}
    if max_bytes:
        cfg['max_file_bytes'] = int(max_bytes)
    if key != 'nextcloud':
        cfg[core.KEY] = {**store_settings(desk, key), 'state_dir': str(desk.c['state_dir'])}
    return DAV(cfg)


def active(desk):
    """Clé du stockage en service : ``nextcloud`` ou un fournisseur ; lecture directe du choix enregistré."""
    selected = core.read_selection(desk.c['state_dir'])
    return selected['provider'] if selected else 'nextcloud'


def prepare(desk, role, provider):
    """Crée le dossier AxiorHub et les racines chez le fournisseur et retient les identifiants utiles ; rien d'autre n'est écrit."""
    _admin(role)
    store = client_for(desk, provider)
    extra = store.prepare()
    desk.setting(PREPARE + provider, {**(desk.settings(PREPARE + provider, {}) or {}), **(extra or {})})
    desk.audit('cloud5688_prepare', {'provider': provider})
    return {'message': 'Dossier AxiorHub et racines prêts chez %s.' % core.PROVIDERS[provider]['label']}


def check(desk, role, provider):
    """Contrôle d'aptitude sur le compte réel : crée, relit, remplace et met à la corbeille un fichier d'essai dans la première racine."""
    _admin(role)
    row = connection(desk, provider)
    if not row:
        raise Stop('connexion_absente')
    out = core.aptitude(client_for(desk, provider), clean_path(desk.c['nextcloud']['roots'][0]))
    desk.setting(APTITUDE + provider, {**out, 'generation': row['generation']})
    desk.audit('cloud5688_aptitude', {'provider': provider, 'ok': out['ok']})
    failed = next((s for s in out['etapes'] if not s['ok']), None)
    return {**out, 'message': 'Contrôle réussi : ce compte respecte le contrat attendu.' if out['ok'] else
            'Contrôle échoué à l’étape « %s »%s. Ce stockage ne peut pas être choisi.' % (failed['etape'], (' (%s)' % failed['detail']) if failed['detail'] else '')}


def aptitude_valid(desk, provider):
    """Contrôle réussi, récent, et sur la même génération de connexion : changer de compte oblige à refaire le contrôle."""
    row = connection(desk, provider)
    done = desk.settings(APTITUDE + provider, None) or {}
    if not row or not done.get('ok') or done.get('generation') != row['generation']:
        return False
    try:
        return datetime.fromisoformat(done['date']) > datetime.now(timezone.utc) - timedelta(days=APTITUDE_DAYS)
    except (KeyError, TypeError, ValueError):
        return False


def activate(desk, actor, role, provider, without_copy=False):
    """Bascule décidée par un administrateur. Sans copie terminée vers ce stockage, l'accord explicite est exigé : les dossiers y seraient vides."""
    from . import cloud_migration5688 as migration
    from . import connexions5652 as cx
    _admin(role)
    row = connection(desk, provider)
    if not row or cx.state_of(desk, row) not in ('verifiee_lecture', 'verifiee_ecriture'):
        raise Stop('connexion_non_verifiee')
    if not aptitude_valid(desk, provider):
        raise Stop('controle_aptitude_requis')
    if not without_copy and not migration.finished_towards(desk, provider):
        raise Stop('copie_terminee_requise')
    selected = {**store_settings(desk, provider), 'choisi_par': str(actor), 'date': datetime.now(timezone.utc).isoformat(timespec='seconds')}
    core.write_selection(desk.c['state_dir'], selected)
    desk.audit('cloud5688_stockage_choisi', {'provider': provider, 'par': str(actor), 'sans_copie': bool(without_copy)})
    return {'message': 'Stockage choisi : %s. Les documents sont désormais lus et écrits à cet endroit.' % core.PROVIDERS[provider]['label']}


def deactivate(desk, actor, role):
    _admin(role)
    previous = active(desk)
    core.clear_selection(desk.c['state_dir'])
    desk.audit('cloud5688_retour_nextcloud', {'precedent': previous, 'par': str(actor)})
    return {'message': 'Retour au stockage d’origine. Aucun fichier n’a été déplacé : ce qui a été écrit dans le cloud y reste.'}


def status(desk, provider):
    """État lisible d'un fournisseur pour la page Paramètres ; sans secret ni appel réseau."""
    from . import connexions5652 as cx
    row = connection(desk, provider)
    if not row:
        return {'provider': provider, 'label': core.PROVIDERS[provider]['label'], 'view': None, 'etat': 'non_configuree', 'etat_libelle': cx.STATES['non_configuree'],
                'aptitude': None, 'aptitude_valide': False, 'pret': False}
    view = cx.public_view(desk, row)
    return {'provider': provider, 'label': core.PROVIDERS[provider]['label'], 'view': view, 'etat': view['etat'], 'etat_libelle': view['etat_libelle'],
            'aptitude': desk.settings(APTITUDE + provider, None), 'aptitude_valide': aptitude_valid(desk, provider),
            'pret': bool(desk.settings(PREPARE + provider, None) is not None)}
