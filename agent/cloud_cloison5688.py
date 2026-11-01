"""5.6.88 : le cloisonnement interne (dossier réservé, ``membres5650``) répercuté sur les permissions du stockage cloud.

Action sensible, d'où un circuit fermé :

1. **Correspondance** : chaque membre reçoit explicitement son identité cloud. Rien n'est deviné : une identité sans correspondance n'est jamais retirée.
2. **Aperçu** : tout ce que le fournisseur déclare sur le dossier et son contenu est classé, sans rien modifier.
3. **Confirmation par un administrateur** : l'état est relu ; s'il diffère de l'aperçu, rien n'est fait et un nouvel aperçu est demandé.
4. **Trace** : chaque retrait et son résultat au journal d'audit ; une nouvelle lecture du fournisseur vérifie le résultat.

Ce qui n'existe pas ici, volontairement : ajouter un accès, créer un lien, modifier un dossier parent. Un accès hérité n'est jamais retiré : il vient d'un dossier
commun à d'autres dossiers, et l'aperçu l'explique. Le compte relié n'est jamais retiré, sans quoi AxiorHub perdrait lui-même l'accès. Lever une réservation ne rend
aucun accès. Aucun modèle ni automatisme n'atteint ce module : il n'est appelé que par les routes réservées aux administrateurs.

Pas de propagation vers Nextcloud : ses partages, dossiers de groupe et partages hérités ne se lisent pas de façon assez sûre pour un retrait automatique.
"""
from datetime import datetime, timedelta, timezone
import hashlib
import json
import re
import uuid

from .common import Stop, clean_path, load_matters, under
from . import cloud_gestion5688 as gestion

TTL_MINUTES = 30
MAX_ITEMS = 300
ACTIONS = {'retirer': 'À retirer', 'conserver': 'Conservé', 'herite': 'Hérité, non retiré', 'a_verifier': 'À vérifier, non retiré', 'parent_commun': 'Dossier commun, non retiré'}
SCHEMA = '''
CREATE TABLE IF NOT EXISTS cloud_identites5688(provider TEXT NOT NULL, owner TEXT NOT NULL, identity TEXT NOT NULL, updated_by TEXT NOT NULL, updated TEXT NOT NULL, PRIMARY KEY(provider, owner));
CREATE TABLE IF NOT EXISTS cloud_cloisons5688(id TEXT PRIMARY KEY, matter TEXT NOT NULL, provider TEXT NOT NULL, state TEXT NOT NULL, plan_json TEXT NOT NULL, plan_hash TEXT NOT NULL,
  created_by TEXT NOT NULL, confirmed_by TEXT NOT NULL DEFAULT '', result_json TEXT NOT NULL DEFAULT '{}', created TEXT NOT NULL, updated TEXT NOT NULL);
CREATE INDEX IF NOT EXISTS cloud_cloisons5688_matter ON cloud_cloisons5688(matter, created);
'''


def _now():
    return datetime.now(timezone.utc).isoformat(timespec='seconds')


def schema(desk):
    desk.db.executescript(SCHEMA)
    desk.db.commit()


def _admin(role):
    if role != 'administrateur':
        raise Stop('role_insuffisant')


def set_identity(desk, actor, role, provider, owner, identity):
    """Correspondance saisie par un administrateur. Vide : retirée. Une adresse ne sert qu'à un seul membre : sinon un retrait viserait la mauvaise personne."""
    _admin(role)
    schema(desk)
    from . import cloud5688 as core
    if provider not in core.PROVIDERS:
        raise Stop('fournisseur_inconnu')
    identity = str(identity or '').strip().lower()
    if not identity:
        desk.db.execute('DELETE FROM cloud_identites5688 WHERE provider=? AND owner=?', (provider, str(owner)))
    else:
        if not re.fullmatch(r'[^@\s]+@[^@\s]+\.[^@\s]+', identity):
            raise Stop('identite_cloud_invalide')
        used = desk.db.execute('SELECT owner FROM cloud_identites5688 WHERE provider=? AND identity=? AND owner<>?', (provider, identity, str(owner))).fetchone()
        if used:
            raise Stop('identite_cloud_deja_utilisee')
        desk.db.execute('INSERT OR REPLACE INTO cloud_identites5688 VALUES(?,?,?,?,?)', (provider, str(owner), identity, str(actor), _now()))
    desk.db.commit()
    desk.audit('cloud5688_correspondance', {'provider': provider, 'membre': str(owner), 'par': str(actor)})
    return {'message': 'Correspondance enregistrée.'}


def identities(desk, provider):
    schema(desk)
    return {r['owner']: r['identity'] for r in desk.db.execute('SELECT owner, identity FROM cloud_identites5688 WHERE provider=?', (provider,))}


def _active_store(desk):
    """Le cloisonnement porte sur le stockage en service, jamais sur un stockage seulement relié."""
    provider = gestion.active(desk)
    if provider == 'nextcloud':
        raise Stop('cloisonnement_stockage_cloud_seulement')
    return provider, gestion.client_for(desk, provider)


def _matter(desk, matter):
    rows = [m for m in load_matters(desk.c, unfiltered=True) if m['id'] == matter]
    if not rows or not rows[0].get('path'):
        raise Stop('dossier_absent')
    return rows[0]


def _targets(store, path):
    """Le dossier et ce qu'il contient (les accès posés sur un sous-dossier ou un fichier survivraient sinon) ; borné, et un parcours incomplet est dit."""
    out, pending, partial = [clean_path(path)], [clean_path(path)], False
    while pending:
        for item in store.list_folder(pending.pop(0)):
            if len(out) >= MAX_ITEMS:
                return out, True
            out.append(item['path'])
            if item.get('directory'):
                pending.append(item['path'])
    return out, partial


def _classify(perm, allowed, denied, shared_parent):
    identity = perm.get('identite', '')
    if perm.get('proprietaire'):
        return 'conserver', 'Propriétaire : un propriétaire ne se retire pas.'
    if identity and identity in allowed:
        return 'conserver', 'Compte autorisé sur ce dossier.'
    if perm.get('herite'):
        return 'herite', ('Cet accès vient d’un dossier parent, commun à d’autres dossiers. AxiorHub ne le retire pas : ce compte perdrait aussi ces autres dossiers, et le '
                          'fournisseur ne permet pas de le retirer ici. Pour fermer ce dossier, il faut agir chez le fournisseur sur le dossier parent, ou y restreindre l’héritage.')
    if shared_parent:
        return 'parent_commun', 'Ce dossier contient d’autres dossiers du cabinet : un retrait ici les toucherait aussi. Réservez plutôt chacun de ces dossiers.'
    if perm.get('type') in ('lien', 'domaine'):
        return 'retirer', 'Accès ouvert sans compte désigné (lien ou domaine entier) sur un dossier réservé.'
    if identity in denied:
        return 'retirer', 'Membre du cabinet non autorisé sur ce dossier.'
    return 'a_verifier', 'Identité inconnue d’AxiorHub (personne extérieure, groupe ou membre sans correspondance) : aucun retrait sans vérification. À traiter chez le fournisseur.'


def _plan(desk, store, provider, matter):
    from . import membres5650
    record = _matter(desk, matter)
    owners = membres5650.load(desk.c['state_dir'])['reserves'].get(matter) or []
    if not owners:
        raise Stop('dossier_non_reserve')
    support = store.permission_support()
    if not support.get('possible'):
        raise Stop('cloisonnement_non_disponible')
    known = identities(desk, provider)
    members = membres5650.directory(desk)
    admins = {o for o, m in members.items() if m.get('role') == 'administrateur'}
    account = store.account_identity()
    allowed = {known[o] for o in set(owners) | admins if o in known} | ({account} if account else set())
    denied = {i for o, i in known.items() if o not in owners and o not in admins and i not in allowed}
    path = clean_path(record['path'])
    shared_parent = any(m['id'] != matter and m.get('path') and under(m['path'], path) for m in load_matters(desk.c, unfiltered=True))
    targets, partial = _targets(store, path)
    entries = []
    for target in targets:
        for perm in store.list_permissions(target):
            action, why = _classify(perm, allowed, denied, shared_parent and target == path)
            entries.append({'chemin': target, 'perm': perm, 'identite': perm.get('identite', ''), 'type': perm.get('type', ''), 'role': perm.get('role', ''), 'action': action, 'raison': why})
    entries.sort(key=lambda x: (x['chemin'], x['identite'], str(x['perm'].get('id'))))
    missing = sorted(o for o in owners if o not in known)
    return {'dossier': matter, 'chemin': path, 'autorises': sorted(owners), 'entrees': entries, 'parcours_partiel': partial, 'sans_correspondance': missing, 'limites': support.get('limites', '')}


def _hash(plan):
    keep = [(e['chemin'], e['perm'].get('id'), e['perm'].get('item'), e['action']) for e in plan['entrees']]
    return hashlib.sha256(json.dumps([keep, plan['autorises']], sort_keys=True, ensure_ascii=False).encode()).hexdigest()


def preview(desk, actor, role, matter):
    """Aperçu : aucune écriture chez le fournisseur."""
    _admin(role)
    schema(desk)
    provider, store = _active_store(desk)
    plan = _plan(desk, store, provider, str(matter))
    ident, now = uuid.uuid4().hex, _now()
    desk.db.execute("UPDATE cloud_cloisons5688 SET state='remplace',updated=? WHERE matter=? AND state='apercu'", (now, str(matter)))
    desk.db.execute('INSERT INTO cloud_cloisons5688(id,matter,provider,state,plan_json,plan_hash,created_by,created,updated) VALUES(?,?,?,?,?,?,?,?,?)',
                    (ident, str(matter), provider, 'apercu', json.dumps(plan, ensure_ascii=False), _hash(plan), str(actor), now, now))
    desk.db.commit()
    desk.audit('cloud5688_cloison_apercu', {'dossier': str(matter), 'plan': ident, 'par': str(actor), 'a_retirer': sum(1 for e in plan['entrees'] if e['action'] == 'retirer')})
    return {'id': ident, 'hash': _hash(plan), 'plan': plan, 'message': 'Aperçu prêt : rien n’a été modifié.'}


def confirm(desk, actor, role, plan_id, plan_hash):
    """Applique l'aperçu confirmé par un administrateur, s'il décrit encore la réalité. Les retraits se font un par un : un refus n'arrête pas les suivants et reste visible."""
    _admin(role)
    schema(desk)
    row = desk.db.execute('SELECT * FROM cloud_cloisons5688 WHERE id=?', (str(plan_id or ''),)).fetchone()
    if not row:
        raise Stop('apercu_absent')
    if row['state'] != 'apercu':
        raise Stop('apercu_deja_traite')
    if str(plan_hash or '') != row['plan_hash']:
        raise Stop('apercu_perime')
    if datetime.fromisoformat(row['created']) < datetime.now(timezone.utc) - timedelta(minutes=TTL_MINUTES):
        raise Stop('apercu_perime')
    provider, store = _active_store(desk)
    if provider != row['provider']:
        raise Stop('apercu_perime')
    plan = _plan(desk, store, provider, row['matter'])         # relecture : un accès ajouté ou retiré depuis l'aperçu oblige à le refaire
    if _hash(plan) != row['plan_hash']:
        desk.db.execute("UPDATE cloud_cloisons5688 SET state='perime',updated=? WHERE id=?", (_now(), row['id']))
        desk.db.commit()
        raise Stop('apercu_perime')
    done, failed = [], []
    for entry in plan['entrees']:
        if entry['action'] != 'retirer':
            continue
        label = {'chemin': entry['chemin'], 'identite': entry['identite'], 'type': entry['type']}
        try:
            store.remove_permission(entry['chemin'], entry['perm'])
            done.append(label)
        except Stop as ex:
            failed.append({**label, 'erreur': str(ex).split(':')[0]})
    after = _plan(desk, store, provider, row['matter'])
    remaining = [{'chemin': e['chemin'], 'identite': e['identite']} for e in after['entrees'] if e['action'] == 'retirer']
    state = 'applique' if not failed and not remaining else 'partiel'
    result = {'retires': done, 'echecs': failed, 'encore_presents': remaining}
    desk.db.execute('UPDATE cloud_cloisons5688 SET state=?,confirmed_by=?,result_json=?,updated=? WHERE id=?', (state, str(actor), json.dumps(result, ensure_ascii=False), _now(), row['id']))
    desk.db.commit()
    desk.audit('cloud5688_cloison_appliquee', {'dossier': row['matter'], 'plan': row['id'], 'par': str(actor), 'fournisseur': provider, **result})
    return {'state': state, **result, 'message': ('%d accès retiré(s), vérifié(s) chez le fournisseur.' % len(done)) if state == 'applique' else
            '%d accès retiré(s) ; %d échec(s), %d encore présent(s) : à reprendre.' % (len(done), len(failed), len(remaining))}


def latest(desk, matter):
    schema(desk)
    row = desk.db.execute('SELECT * FROM cloud_cloisons5688 WHERE matter=? ORDER BY created DESC, rowid DESC LIMIT 1', (str(matter),)).fetchone()
    if not row:
        return None
    return {'id': row['id'], 'state': row['state'], 'hash': row['plan_hash'], 'plan': json.loads(row['plan_json']), 'resultat': json.loads(row['result_json'] or '{}'), 'date': row['updated']}


def support(desk):
    """Ce que l'interface doit dire : disponible, ou pourquoi non. Une panne de lecture n'est pas présentée comme une absence de droit."""
    try:
        provider, store = _active_store(desk)
    except Stop:
        return {'possible': False, 'raison': 'Le stockage en service est Nextcloud ou un dossier local : aucune répercussion n’y est faite. Le cloisonnement reste appliqué dans AxiorHub.'}
    try:
        return {**store.permission_support(), 'provider': provider}
    except Stop as ex:
        return {'possible': False, 'raison': 'Lecture impossible chez le fournisseur (%s) : réessayez plus tard.' % str(ex).split(':')[0]}
