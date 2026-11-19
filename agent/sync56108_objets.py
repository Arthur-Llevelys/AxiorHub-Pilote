"""5.6.108 (lot S2) : objets métier synchronisés — dossiers, tâches, événements — et leurs projections vers les modules existants.

- **Instantané** (``snapshot``) : état partageable d'un objet local, sans chemin physique, sans identifiant local (le dossier lié est
  désigné par son identifiant global). **Projection** : écriture dans les tables du module (registre ``dossiers5697`` par ses fonctions
  de validation et de modification, ``tasks`` + ``taches5688`` + ``taches5697``, ``agenda5634`` + ``agenda5697``), DANS la transaction de
  réception, sous un point de sauvegarde : un refus local (référence déjà prise, dossier pas encore reçu) n'efface jamais l'opération
  reçue ; l'objet passe « à rapprocher » ou « en attente », visible et repris.
- **Rapprochement** : jamais de fusion sur la seule ressemblance d'un titre. Une référence déjà utilisée sur ce poste donne un doublon à
  examiner : « Associer à ce dossier » (les deux états deviennent deux versions à comparer) ou « Garder séparé » (référence locale
  distincte, la référence partagée reste celle de l'origine). Un événement de même UID (connecteur) est rattaché à l'existant, jamais recréé.
- **Dossiers réservés** : projetés réservés à la personne de ce poste ; un dossier partagé avec tout le cabinet qui devient réservé n'émet
  plus rien (« bloqué : devenu réservé ») tant que l'administrateur n'a pas choisi les personnes.
- **Événements privés** (visibilité « privée ») et tâches ou événements sans dossier ne sont jamais partagés sauf choix explicite de la
  catégorie pour l'espace.
"""
from datetime import datetime
import json
import re
import uuid

from .common import Stop
from . import sync56108 as S

PREFIXES = {'dossier': 'dos', 'tache': 'tac', 'evenement': 'evt'}
ACTEUR_SYNC = None


def _acteur():
    from .acces5697 import Acteur
    return Acteur('synchronisation', 'administrateur')


def preparer(desk):
    """Hors transaction : schémas des modules projetés et identifiant du cabinet (``cabinet_id`` l'écrit au premier appel)."""
    from . import dossiers5697 as D
    D.ensure_schema(desk)
    D._cabinet(desk)
    if desk.db.in_transaction:
        desk.db.commit()


# ------------------------------------------------------------------------------------------------ validation des charges (liste fermée)
def _champ(valeur, regle):
    genre = regle[0]
    if genre == 'str':
        return isinstance(valeur, str) and len(valeur) <= regle[1] and not re.search(r'[\x00-\x08\x0b\x0c\x0e-\x1f]', valeur)
    if genre == 'enum':
        return valeur in regle[1]
    if genre == 'bool':
        return isinstance(valeur, bool)
    if genre == 'gid':
        return valeur == '' or (isinstance(valeur, str) and re.fullmatch(r'^%s-[0-9a-f]{32}$' % regle[1], valeur) is not None)
    if genre == 'mot':
        return isinstance(valeur, str) and re.fullmatch(r'[a-z_]{0,30}', valeur) is not None
    if genre == 'instant':
        if not isinstance(valeur, str) or len(valeur) > 40:
            return False
        try:
            return datetime.fromisoformat(valeur).tzinfo is not None
        except ValueError:
            return False
    if genre == 'jour':
        if valeur == '':
            return True
        try:
            datetime.strptime(str(valeur), '%Y-%m-%d')
            return True
        except ValueError:
            return False
    if genre == 'rappels':
        return isinstance(valeur, list) and len(valeur) <= 4 and all(type(x) is int and 0 <= x <= 100000 for x in valeur)
    if genre == 'parties':
        return isinstance(valeur, list) and len(valeur) <= 40 and all(
            isinstance(p, dict) and set(p) == {'nom', 'role', 'courriel'} and _champ(p['nom'], ('str', 160)) and _champ(p['role'], ('mot',))
            and _champ(p['courriel'], ('str', 254)) for p in valeur)
    return False


DOSSIER = {'titre': ('str', 160), 'reference': ('str', 40), 'client': ('str', 160), 'client_etat': ('enum', ('identifie', 'a_identifier')),
           'nature': ('enum', ('conseil', 'contentieux', 'a_qualifier')), 'statut': ('enum', ('actif', 'archive')), 'responsable': ('str', 120),
           'juridiction': ('str', 160), 'rg': ('str', 40), 'resume': ('str', 4000), 'confidentialite': ('enum', ('cabinet', 'reserve')),
           'parties': ('parties',)}
TACHE = {'titre': ('str', 2000), 'echeance': ('jour',), 'statut': ('mot',), 'dossier': ('gid', 'dos'), 'responsable': ('str', 120),
         'etat': ('mot',), 'rappel': ('str', 20), 'priorite': ('enum', ('basse', 'normale', 'haute')), 'origine': ('mot',)}
EVENEMENT = {'uid': ('str', 255), 'recurrence_id': ('str', 64), 'kind': ('mot',), 'titre': ('str', 200), 'description': ('str', 2000),
             'lieu': ('str', 200), 'debut': ('instant',), 'fin': ('instant',), 'journee': ('bool',), 'dossier': ('gid', 'dos'), 'rappels': ('rappels',),
             'statut': ('enum', ('actif', 'annule')), 'source': ('str', 80), 'remote_uid': ('str', 255), 'zone': ('str', 64), 'nature': ('mot',),
             'visio': ('mot',), 'lien': ('str', 500)}
SUPPRIME = {'supprime': ('bool',)}


def _verifier(charge, spec):
    if set(charge) != set(spec) or not all(_champ(charge[k], spec[k]) for k in spec):
        raise Stop('charge_invalide')


def valider_charge(type_, charge):
    """Charge d'une opération selon son type (forme stricte, aucune clé en plus) ; avant toute écriture."""
    genre, action = type_.split('.', 1)
    if not isinstance(charge, dict):
        raise Stop('charge_invalide')
    if genre == 'document':
        from .sync56108_fichiers import valider_charge as v
        return v(type_, charge)
    if genre in ('mission', 'espace'):
        from .sync56108_missions import valider_charge as v
        return v(type_, charge)
    spec = {'dossier': DOSSIER, 'tache': TACHE, 'evenement': EVENEMENT}.get(genre)
    if spec is None:
        raise Stop('charge_invalide')
    if charge.get('supprime') is True:
        if genre == 'dossier' or action not in ('suppression', 'resolution'):
            raise Stop('charge_invalide')
        return _verifier(charge, SUPPRIME)
    if action == 'suppression':
        raise Stop('charge_invalide')
    return _verifier(charge, spec)


def droits_type(desk, op, esp):
    """Droits propres au type (missions : seul l'exécuteur désigné rend compte), avant toute écriture."""
    genre = op['type'].split('.', 1)[0]
    if genre == 'mission' or op['type'] == 'espace.pause':
        from .sync56108_missions import droits
        droits(desk, op, esp)
    if genre == 'dossier' and op['charge'].get('confidentialite') == 'reserve' and not esp['parent']:
        raise Stop('dossier_reserve_hors_sous_espace')   # un dossier réservé ne circule jamais dans l'espace de tout le cabinet


# ------------------------------------------------------------------------------------------------ correspondances
def objet_de(desk, genre, local_id):
    return desk.db.execute('SELECT * FROM sync56108_objets WHERE genre=? AND local_id=?', (genre, str(local_id))).fetchone()


def local_de(desk, objet):
    r = desk.db.execute("SELECT local_id FROM sync56108_objets WHERE objet=? AND local_id IS NOT NULL", (objet,)).fetchone()
    return r[0] if r else ''


def _gid_dossier(desk, matter):
    if not matter:
        return ''
    o = objet_de(desk, 'dossier', matter)
    return o['objet'] if o else ''


def _ref_partagee(desk, objet, ref_locale):
    r = desk.db.execute("SELECT etat FROM sync56108_objets WHERE objet=?", (objet,)).fetchone() if objet else None
    try:
        etat = json.loads(r[0]) if r and r[0] else {}
    except ValueError:
        etat = {}
    if etat.get('_ref_locale') == ref_locale and etat.get('reference'):
        return etat['reference']
    return ref_locale


# ------------------------------------------------------------------------------------------------ instantanés
def snapshot(desk, genre, local_id):
    db = desk.db
    if genre == 'dossier':
        r = db.execute('SELECT * FROM dossiers5697 WHERE id=?', (local_id,)).fetchone()
        if not r:
            return None
        o = objet_de(desk, 'dossier', local_id)
        parties = [{'nom': p['nom'], 'role': p['role'], 'courriel': p['courriel'] or ''} for p in db.execute(
            "SELECT nom, role, courriel FROM dossier_parties5697 WHERE dossier_id=? ORDER BY role, nom_norm", (local_id,))]
        return {'titre': r['titre'], 'reference': _ref_partagee(desk, o['objet'] if o else '', r['ref']), 'client': r['client'],
                'client_etat': r['client_etat'], 'nature': r['nature'], 'statut': r['statut'], 'responsable': r['responsable'], 'juridiction': r['juridiction'],
                'rg': r['rg'], 'resume': r['resume'], 'confidentialite': r['confidentialite'], 'parties': parties}
    if genre == 'tache':
        r = db.execute('SELECT * FROM tasks WHERE id=?', (local_id,)).fetchone()
        if not r:
            return None
        x = db.execute('SELECT * FROM taches5688 WHERE task_id=?', (local_id,)).fetchone()
        y = db.execute('SELECT * FROM taches5697 WHERE task_id=?', (local_id,)).fetchone() if S._table(db, 'taches5697') else None
        return {'titre': r['title'] or '', 'echeance': (r['due'] or '')[:10], 'statut': re.sub(r'[^a-z_]', '', str(r['status'] or 'open'))[:30],
                'dossier': _gid_dossier(desk, r['matter']), 'responsable': (x['responsable'] if x else '') or '', 'etat': (x['etat'] if x else 'a_faire') or 'a_faire',
                'rappel': str((x['rappel'] if x else '') or '')[:20], 'priorite': (y['priorite'] if y else 'normale') or 'normale',
                'origine': re.sub(r'[^a-z_]', '', str((x['origine'] if x else '') or ''))[:30]}
    if genre == 'evenement':
        r = db.execute('SELECT * FROM agenda5634 WHERE id=?', (local_id,)).fetchone()
        if not r:
            return None
        y = db.execute('SELECT * FROM agenda5697 WHERE event_id=?', (local_id,)).fetchone() if S._table(db, 'agenda5697') else None
        try:
            rappels = [int(v) for v in json.loads(r['reminders'] or '[]')][:4]
        except (ValueError, TypeError):
            rappels = []
        return {'uid': r['uid'], 'recurrence_id': r['recurrence_id'] or '', 'kind': re.sub(r'[^a-z_]', '', r['kind'])[:30], 'titre': r['title'],
                'description': r['description'] or '', 'lieu': r['location'] or '', 'debut': r['starts'], 'fin': r['ends'], 'journee': bool(r['all_day']),
                'dossier': _gid_dossier(desk, r['matter']), 'rappels': rappels, 'statut': 'annule' if r['status'] == 'annule' else 'actif',
                'source': (r['source'] or 'local')[:80], 'remote_uid': r['remote_uid'] or '', 'zone': ((y['zone'] if y else '') or '')[:64],
                'nature': re.sub(r'[^a-z_]', '', (y['nature'] if y else '') or '')[:30], 'visio': re.sub(r'[^a-z_]', '', (y['visio'] if y else '') or '')[:30],
                'lien': ((y['lien'] if y else '') or '')[:500], '_visibilite': (y['visibilite'] if y else 'normale') or 'normale'}
    return None


def _partage_de(desk, dossier_local):
    r = desk.db.execute('SELECT espace FROM sync56108_partages WHERE dossier=?', (dossier_local,)).fetchone()
    return r[0] if r else None


def espace_pour(desk, genre, local_id, snap):
    """Espace où l'objet est partagé ; None : non partagé (choix explicite des dossiers ; privé exclu)."""
    if genre == 'dossier':
        return _partage_de(desk, local_id)
    if genre == 'evenement' and snap.get('_visibilite') == 'privee':
        return None
    matter = desk.db.execute('SELECT matter FROM %s WHERE id=?' % ('tasks' if genre == 'tache' else 'agenda5634'), (local_id,)).fetchone()
    matter = matter[0] if matter else ''
    if matter:
        return _partage_de(desk, matter)
    esp = S.espace_cabinet(desk)
    cat = json.loads(esp['categories'] or '{}') if esp else {}
    return esp['id'] if esp and cat.get('taches_sans_dossier' if genre == 'tache' else 'agenda_sans_dossier') else None


def _propre(snap):
    return {k: v for k, v in snap.items() if not k.startswith('_')}


# ------------------------------------------------------------------------------------------------ journalisation d'une commande locale
def journaliser_un(desk, ident, genre, local_id):
    """Dans la transaction ouverte : capture → opération signée (création, modification, clôture, annulation, suppression). Rend 0 ou 1."""
    desk.db.execute('DELETE FROM sync56108_capture WHERE genre=? AND local_id=?', (genre, local_id))
    if genre not in PREFIXES:
        return 0
    o = objet_de(desk, genre, local_id)
    snap = snapshot(desk, genre, local_id)
    if o is not None and o['bloque']:
        return 0
    if snap is not None and genre == 'dossier' and o is not None and snap['confidentialite'] == 'reserve':
        esp_row = desk.db.execute('SELECT parent FROM sync56108_espaces WHERE id=?', (o['espace'],)).fetchone()
        if esp_row and not esp_row['parent']:   # devenu réservé alors qu'il est partagé avec tout le cabinet : plus rien ne part
            desk.db.execute("UPDATE sync56108_objets SET bloque=1, motif='devenu_reserve', maj_le=? WHERE objet=?", (S._maintenant(), o['objet']))
            return 0
    esp = espace_pour(desk, genre, local_id, snap) if snap is not None else None
    if o is None:
        if snap is None or esp is None:
            return 0
        if genre == 'dossier' and snap['confidentialite'] == 'reserve':
            r = desk.db.execute('SELECT parent FROM sync56108_espaces WHERE id=?', (esp,)).fetchone()
            if not r or not r['parent']:
                return 0
        return _emettre(desk, ident, esp, genre, S.nouvel_id(PREFIXES[genre]), genre + '.creation', _propre(snap), [], local_id, None)
    if snap is None:
        if genre == 'dossier':
            return 0
        return _emettre(desk, ident, o['espace'], genre, o['objet'], genre + '.suppression', {'supprime': True}, _parents(o), None, o)
    if esp != o['espace'] and genre != 'dossier':   # tâche ou événement déplacé : retiré de l'ancien espace, recréé dans le nouveau s'il y en a un
        _emettre(desk, ident, o['espace'], genre, o['objet'], genre + '.suppression', {'supprime': True}, _parents(o), None, o)
        desk.db.execute('UPDATE sync56108_objets SET local_id=NULL WHERE objet=?', (o['objet'],))
        if esp is None:
            return 1
        return 1 + _emettre(desk, ident, esp, genre, S.nouvel_id(PREFIXES[genre]), genre + '.creation', _propre(snap), [], local_id, None)
    charge = _propre(snap)
    if S._sha(S.canon(charge)) == o['digest']:
        return 0
    avant = json.loads(o['etat']) if o['etat'] else {}
    type_ = genre + '.modification'
    if genre == 'dossier' and charge['statut'] == 'archive' and avant.get('statut') != 'archive':
        type_ = 'dossier.cloture'
    if genre == 'evenement' and charge['statut'] == 'annule' and avant.get('statut') != 'annule':
        type_ = 'evenement.annulation'
    return _emettre(desk, ident, o['espace'], genre, o['objet'], type_, charge, _parents(o), local_id, o)


def _parents(o):
    return [o['projete_op']] if o and o['projete_op'] else []


def _emettre(desk, ident, espace, genre, objet, type_, charge, parents, local_id, o):
    op = S.emettre(desk, espace, type_, objet, charge, parents, ident)
    n = len(S.tetes(desk, objet))
    etat = dict(charge)
    if o is not None and o['etat']:
        ancien = json.loads(o['etat'])
        if ancien.get('_ref_locale'):
            etat['_ref_locale'] = ancien['_ref_locale']
    if o is None:
        desk.db.execute('INSERT INTO sync56108_objets(objet,genre,espace,local_id,etat,digest,projete_op,conflit,projection,maj_le) '
                        "VALUES(?,?,?,?,?,?,?,?,'faite',?)", (objet, genre, espace, local_id, S.canon(etat), S._sha(S.canon(charge)), op['id'],
                                                               int(n > 1), S._maintenant()))
    else:
        desk.db.execute('UPDATE sync56108_objets SET etat=?,digest=?,projete_op=?,conflit=?,maj_le=? WHERE objet=?',
                        (S.canon(etat), S._sha(S.canon(charge)), op['id'], int(n > 1), S._maintenant(), objet))
    return 1


# ------------------------------------------------------------------------------------------------ réception : projection
def apres_reception(desk, op, moi):
    """Dans la transaction de réception, après l'enregistrement de l'opération : projection (ou conflit, ou attente). Rend les suites à
    exécuter après la validation (registre JSON des dossiers)."""
    genre = S.GENRES[op['objet'][:3]]
    if genre in ('mission', 'pause'):
        from .sync56108_missions import apres_reception as suite
        return suite(desk, op)
    suites = []
    if genre == 'document':   # chaque version est gardée (métadonnées, téléchargement), même concurrente : deux versions à comparer
        from .sync56108_fichiers import apres_reception as noter_version
        suites = noter_version(desk, op)
    o = desk.db.execute('SELECT * FROM sync56108_objets WHERE objet=?', (op['objet'],)).fetchone()
    if o is None:
        desk.db.execute("INSERT INTO sync56108_objets(objet,genre,espace,local_id,projection,maj_le) VALUES(?,?,?,NULL,'a_faire',?)",
                        (op['objet'], genre, op['espace'], S._maintenant()))
    elif o['local_id'] and desk.db.execute('SELECT 1 FROM sync56108_capture WHERE genre=? AND local_id=?', (genre, o['local_id'])).fetchone():
        journaliser_un(desk, S._ident(desk), genre, o['local_id'])   # la modification locale en attente devient une version concurrente
    if len(S.tetes(desk, op['objet'])) > 1:
        desk.db.execute("UPDATE sync56108_objets SET conflit=1, maj_le=? WHERE objet=?", (S._maintenant(), op['objet']))
        return suites
    if genre == 'document':
        desk.db.execute("UPDATE sync56108_objets SET conflit=0, projete_op=?, projection='faite', maj_le=? WHERE objet=?",
                        (op['id'], S._maintenant(), op['objet']))
        return suites
    return projeter(desk, op)


def projeter_locale(desk, op):
    """Résolution choisie sur ce poste : projection de la version retenue (déclencheurs muets)."""
    genre = S.GENRES[op['objet'][:3]]
    if genre == 'document':
        from .sync56108_fichiers import apres_reception as suite
        out = suite(desk, op)
        desk.db.execute("UPDATE sync56108_objets SET conflit=0, projete_op=?, maj_le=? WHERE objet=?", (op['id'], S._maintenant(), op['objet']))
        return out
    return projeter(desk, op)


def projeter(desk, op):
    """Projection sous point de sauvegarde ; un refus local laisse l'opération enregistrée et l'objet « à rapprocher » ou « en attente »."""
    genre = S.GENRES[op['objet'][:3]]
    o = desk.db.execute('SELECT * FROM sync56108_objets WHERE objet=?', (op['objet'],)).fetchone()
    suites = []
    desk.db.execute('SAVEPOINT sync56108_projection')
    try:
        local_id = {'dossier': _projeter_dossier, 'tache': _projeter_tache, 'evenement': _projeter_evenement}[genre](
            desk, op, o['local_id'] if o else None, o, suites)
        desk.db.execute('RELEASE sync56108_projection')
    except Stop as ex:
        desk.db.execute('ROLLBACK TO sync56108_projection')
        desk.db.execute('RELEASE sync56108_projection')
        code = str(ex).split(':')[0]
        etat = 'en_attente' if code in ('dossier_en_attente',) else 'a_rapprocher'
        desk.db.execute('UPDATE sync56108_objets SET projection=?, motif=?, maj_le=? WHERE objet=?', (etat, code[:60], S._maintenant(), op['objet']))
        return []
    snap = snapshot(desk, genre, local_id) if local_id else None
    etat = dict(op['charge'])
    if o is not None and o['etat']:
        ancien = json.loads(o['etat'])
        if ancien.get('_ref_locale'):
            etat['_ref_locale'] = ancien['_ref_locale']
    desk.db.execute("UPDATE sync56108_objets SET local_id=?, etat=?, digest=?, projete_op=?, conflit=0, projection='faite', motif='', maj_le=? WHERE objet=?",
                    (local_id, S.canon(etat), S._sha(S.canon(_propre(snap))) if snap is not None else '', op['id'], S._maintenant(), op['objet']))
    return suites


def reprendre(desk, limite=100):
    """Objets « en attente » (tâche reçue avant son dossier) : nouvelle projection de leur tête unique."""
    n = 0
    for o in desk.db.execute("SELECT * FROM sync56108_objets WHERE projection='en_attente' ORDER BY maj_le LIMIT ?", (limite,)).fetchall():
        hs = S.tetes(desk, o['objet'])
        if len(hs) != 1:
            continue
        op = json.loads(desk.db.execute('SELECT brut FROM sync56108_operations WHERE id=?', (hs[0],)).fetchone()[0])
        suites = []
        with S._tx(desk):
            suites = projeter(desk, op)
        for s in suites:
            try:
                s()
            except Exception:   # noqa: BLE001
                pass
        n += 1
    return n


# ------------------------------------------------------------------------------------------------ dossiers
def _local_dossier(desk, gid):
    if not gid:
        return ''
    r = desk.db.execute("SELECT local_id FROM sync56108_objets WHERE objet=? AND projection='faite' AND local_id IS NOT NULL", (gid,)).fetchone()
    if not r:
        raise Stop('dossier_en_attente')
    return r[0]


def _reserve_pour_ce_poste(desk, ident):
    p = S.personne_locale(desk)
    from .membres5650 import set_reserve
    set_reserve(desk.c['state_dir'], ident, [p['compte']] if p and p.get('compte') else [])


def _projeter_dossier(desk, op, local_id, o, suites):
    from . import dossiers5697 as D
    ch = op['charge']
    a = _acteur()
    cabinet = D._cabinet(desk)
    ancien = json.loads(o['etat']) if o is not None and o['etat'] else {}
    ref = D.normaliser_reference(ch['reference'])
    if not ref:
        raise Stop('reference_invalide')
    ref_locale = ancien.get('_ref_locale') or ref
    if not local_id:
        if ref_locale in D._refs_existantes(desk, cabinet):
            raise Stop('reference_deja_utilisee')
        v = D.valider(desk, {**{k: ch[k] for k in ('titre', 'client', 'client_etat', 'nature', 'responsable', 'juridiction', 'rg', 'confidentialite')},
                             'reference': ref_locale, 'parties': ch['parties'], 'provenance': 'connecteur', 'emplacement': {'mode': 'creer'}}, a)
        ident = uuid.uuid4().hex
        quand = S._maintenant()
        try:
            chemin = D.emplacement_propose(desk, v['titre'], ref_locale)
            D._chemin_libre(desk, chemin)
            etat_st, motif = 'a_preparer', ''
        except Stop as ex:   # racine absente ou répertoire pris : le dossier existe, son rangement attend (jamais un autre dossier)
            chemin, etat_st, motif = '', 'en_attente', str(ex).split(':')[0][:60]
        desk.db.execute('INSERT INTO dossiers5697(id,cabinet_id,ref,ref_norm,titre,client,client_etat,nature,statut,responsable,provenance,'
                        'confidentialite,juridiction,rg,resume,chemin,stockage_mode,stockage_etat,stockage_motif,version,auteur,cree_le,modifie_le,modifie_par) '
                        'VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)',
                        (ident, cabinet, ref_locale, ref_locale, v['titre'], v['client'], v['client_etat'], v['nature'], 'actif', v['responsable'],
                         'connecteur', v['confidentialite'], v['juridiction'], v['rg'], str(ch['resume'])[:4000], chemin, 'creer', etat_st, motif, 1,
                         a.compte, quand, quand, a.compte))
        for p in v['parties']:
            D._ajouter_partie(desk, ident, p)
        D._alias(desk, 'ref', ref_locale, ident)
        if chemin:
            desk.db.execute('INSERT INTO dossier_outbox5697(id,dossier_id,kind,operation_key,args,etat,cree_le,modifie_le) VALUES(?,?,?,?,?,?,?,?)',
                            (uuid.uuid4().hex, ident, 'storage', 'rangement:%s:1' % ident, json.dumps({'mode': 'creer', 'chemin': chemin}, ensure_ascii=False),
                             'a_faire', quand, quand))
        if v['confidentialite'] == 'reserve':
            _reserve_pour_ce_poste(desk, ident)   # avant la validation : jamais visible d'une autre personne de ce poste
        D._journal(desk, ident, a.compte, 'creation', {'reference': ref_locale, 'provenance': 'synchronisation', 'origine': op['origine']})
        if ch['statut'] == 'archive':
            D.transition_cloture(desk, a, ident, 1, 'archive', 'sync:%s:c' % op['id'])
        suites.append(lambda: D.projeter(desk, ident))
        return ident
    row = desk.db.execute('SELECT * FROM dossiers5697 WHERE id=?', (local_id,)).fetchone()
    if not row:
        raise Stop('dossier_local_absent')
    modif = {}
    for k in ('titre', 'client', 'client_etat', 'nature', 'responsable', 'juridiction', 'rg', 'resume'):
        if ch[k] != row[k]:
            modif[k] = ch[k]
    if ref != ancien.get('reference', ref) and ref_locale == ancien.get('_ref_locale', ''):
        pass   # référence locale distincte choisie au rapprochement : gardée
    elif ref != row['ref'] and not ancien.get('_ref_locale'):
        modif['reference'] = ref
    if modif:
        D._appliquer_modification(desk, a, cabinet, local_id, row['version'], modif, 'sync:%s:m' % op['id'])
    if ch['statut'] != row['statut']:
        version = desk.db.execute('SELECT version FROM dossiers5697 WHERE id=?', (local_id,)).fetchone()[0]
        D.transition_cloture(desk, a, local_id, version, ch['statut'], 'sync:%s:c' % op['id'])
    voulues = {(p['nom'], p['role']): p for p in ch['parties']}
    actuelles = {(p['nom'], p['role']) for p in desk.db.execute('SELECT nom, role FROM dossier_parties5697 WHERE dossier_id=?', (local_id,))}
    if set(voulues) != actuelles:
        desk.db.execute('DELETE FROM dossier_parties5697 WHERE dossier_id=?', (local_id,))
        for p in voulues.values():
            D._ajouter_partie(desk, local_id, {'nom': p['nom'], 'role': p['role'], 'courriel': p['courriel'], 'contact_id': ''})
    suites.append(lambda: D.projeter(desk, local_id))
    return local_id


def doublons(desk, objet):
    """Aperçu des doublons d'un dossier reçu « à rapprocher » : dossiers locaux de même référence (jamais par le seul titre)."""
    from . import dossiers5697 as D
    o = desk.db.execute("SELECT * FROM sync56108_objets WHERE objet=? AND genre='dossier'", (objet,)).fetchone()
    if not o:
        raise Stop('introuvable')
    hs = S.tetes(desk, objet)
    ch = json.loads(desk.db.execute('SELECT brut FROM sync56108_operations WHERE id=?', (hs[-1],)).fetchone()[0])['charge'] if hs else {}
    ref = D.normaliser_reference(ch.get('reference')) or ''
    lid = D.resoudre(desk, ref) if ref else ''
    locaux = [dict(r) for r in desk.db.execute('SELECT id, ref, titre, client, statut FROM dossiers5697 WHERE id=?', (lid,))] if lid else []
    return {'recu': {k: ch.get(k) for k in ('titre', 'reference', 'client', 'statut')}, 'locaux': locaux, 'motif': o['motif']}


def rapprocher(desk, objet, decision, cible=''):
    """« associer » : le dossier local ``cible`` devient cet objet ; son état local part comme version concurrente (comparaison) ;
    « separer » : projection avec une référence locale distincte (la référence partagée reste celle de l'origine)."""
    from . import dossiers5697 as D
    o = desk.db.execute("SELECT * FROM sync56108_objets WHERE objet=? AND genre='dossier' AND projection='a_rapprocher'", (str(objet or ''),)).fetchone()
    if not o:
        raise Stop('introuvable')
    hs = S.tetes(desk, o['objet'])
    if len(hs) != 1:
        raise Stop('conflit_a_resoudre')
    op = json.loads(desk.db.execute('SELECT brut FROM sync56108_operations WHERE id=?', (hs[0],)).fetchone()[0])
    suites = []
    if decision == 'associer':
        if not desk.db.execute('SELECT 1 FROM dossiers5697 WHERE id=?', (str(cible or ''),)).fetchone() or objet_de(desk, 'dossier', cible):
            raise Stop('introuvable')
        with S._tx(desk):
            desk.db.execute("UPDATE sync56108_objets SET local_id=?, projection='faite', motif='', projete_op='', digest='' WHERE objet=?", (cible, o['objet']))
            desk.db.execute('INSERT OR REPLACE INTO sync56108_partages(dossier,espace,par,quand) VALUES(?,?,?,?)', (cible, o['espace'], 'rapprochement',
                                                                                                                  S._maintenant()))
            journaliser_un(desk, S._ident(desk), 'dossier', cible)   # état local = version sans parent : deux têtes, comparaison
        return {'objet': o['objet'], 'decision': 'associer'}
    if decision == 'separer':
        cabinet = D._cabinet(desk)
        ref = D.normaliser_reference(op['charge']['reference']) or 'DOSSIER'
        prises = D._refs_existantes(desk, cabinet)
        n = 2
        while ('%s-%d' % (ref, n))[:40] in prises:
            n += 1
        with S._tx(desk):
            etat = json.loads(o['etat']) if o['etat'] else {}
            etat['_ref_locale'] = ('%s-%d' % (ref, n))[:40]
            etat['reference'] = op['charge']['reference']
            desk.db.execute('UPDATE sync56108_objets SET etat=? WHERE objet=?', (S.canon(etat), o['objet']))
            suites = projeter(desk, op)
        for s in suites:
            try:
                s()
            except Exception:   # noqa: BLE001
                pass
        return {'objet': o['objet'], 'decision': 'separer', 'reference_locale': etat['_ref_locale']}
    raise Stop('decision_inconnue')


# ------------------------------------------------------------------------------------------------ tâches
def _projeter_tache(desk, op, local_id, o, suites):
    ch = op['charge']
    if ch.get('supprime'):
        if local_id:
            for t, c in (('tasks', 'id'), ('taches5688', 'task_id'), ('taches5697', 'task_id')):
                if S._table(desk.db, t):
                    desk.db.execute('DELETE FROM %s WHERE %s=?' % (t, c), (local_id,))
        return None
    matter = _local_dossier(desk, ch['dossier'])
    stamp = S._maintenant()
    if not local_id:
        local_id = uuid.uuid4().hex
        desk.db.execute('INSERT INTO tasks(id,matter,title,due,status,created) VALUES(?,?,?,?,?,?)', (local_id, matter, ch['titre'], ch['echeance'], ch['statut'], stamp))
    else:
        desk.db.execute('UPDATE tasks SET matter=?, title=?, due=?, status=? WHERE id=?', (matter, ch['titre'], ch['echeance'], ch['statut'], local_id))
    desk.db.execute('INSERT INTO taches5688(task_id,responsable,rappel,etat,origine,origine_ref,createur,updated) VALUES(?,?,?,?,?,?,?,?) '
                    'ON CONFLICT(task_id) DO UPDATE SET responsable=excluded.responsable,rappel=excluded.rappel,etat=excluded.etat,origine=excluded.origine,'
                    'updated=excluded.updated', (local_id, ch['responsable'], ch['rappel'], ch['etat'], ch['origine'], matter if ch['origine'] == 'dossier' else '',
                                                 'synchronisation', stamp))
    if S._table(desk.db, 'taches5697'):
        desk.db.execute('INSERT INTO taches5697(task_id,priorite,evenement,updated) VALUES(?,?,?,?) ON CONFLICT(task_id) DO UPDATE SET '
                        'priorite=excluded.priorite, updated=excluded.updated', (local_id, ch['priorite'], '', stamp))
    return local_id


# ------------------------------------------------------------------------------------------------ événements
def _projeter_evenement(desk, op, local_id, o, suites):
    from . import agenda5634
    ch = op['charge']
    if ch.get('supprime'):
        if local_id:
            row = desk.db.execute('SELECT * FROM agenda5634 WHERE id=?', (local_id,)).fetchone()
            if row:
                desk.db.execute("UPDATE agenda5634 SET status='annule', updated=? WHERE id=?", (S._maintenant(), local_id))
                agenda5634._mirror(desk, dict(row, status='annule'))
        return local_id
    matter = _local_dossier(desk, ch['dossier'])
    stamp = S._maintenant()
    if not local_id:
        meme = desk.db.execute('SELECT id FROM agenda5634 WHERE uid=? AND starts=?', (ch['uid'], ch['debut'])).fetchone()
        if meme and not objet_de(desk, 'evenement', meme[0]):
            return meme[0]   # même UID (connecteur des deux côtés) : rattaché à l'existant, jamais recréé
        local_id = uuid.uuid4().hex
    row = {'id': local_id, 'uid': ch['uid'], 'recurrence_id': ch['recurrence_id'], 'kind': ch['kind'] if ch['kind'] in agenda5634.KINDS else 'autre',
           'title': ch['titre'], 'description': ch['description'], 'location': ch['lieu'], 'starts': ch['debut'], 'ends': ch['fin'],
           'all_day': int(ch['journee']), 'matter': matter, 'reminders': json.dumps([r for r in ch['rappels'] if r in agenda5634.REMINDERS]),
           'status': ch['statut'], 'source': ch['source'] or 'local', 'sync_state': 'local', 'remote_uid': ch['remote_uid'], 'owner': 'cabinet',
           'created': stamp, 'updated': stamp, 'calendar_scope': 'personal_local', 'copy_policy': 'none'}   # jamais recopié vers un agenda distant ici
    existe = desk.db.execute('SELECT created FROM agenda5634 WHERE id=?', (local_id,)).fetchone()
    if existe:
        row['created'] = existe[0]
        desk.db.execute('UPDATE agenda5634 SET uid=?,recurrence_id=?,kind=?,title=?,description=?,location=?,starts=?,ends=?,all_day=?,matter=?,'
                        'reminders=?,status=?,source=?,remote_uid=?,updated=? WHERE id=?',
                        (row['uid'], row['recurrence_id'], row['kind'], row['title'], row['description'], row['location'], row['starts'], row['ends'],
                         row['all_day'], row['matter'], row['reminders'], row['status'], row['source'], row['remote_uid'], stamp, local_id))
    else:
        try:
            desk.db.execute('INSERT INTO agenda5634(%s) VALUES(%s)' % (','.join(row), ','.join('?' for _ in row)), tuple(row.values()))
        except Exception as ex:   # noqa: BLE001
            if 'UNIQUE' in str(ex):
                raise Stop('evenement_deja_present') from None
            raise
    if S._table(desk.db, 'agenda5697'):
        desk.db.execute("INSERT INTO agenda5697(event_id,nature,visibilite,visio,lien,calendrier,zone,proprietaire,updated) VALUES(?,?,'normale',?,?,'local',?,?,?) "
                        'ON CONFLICT(event_id) DO UPDATE SET nature=excluded.nature,visio=excluded.visio,lien=excluded.lien,zone=excluded.zone,updated=excluded.updated',
                        (local_id, ch['nature'] or 'rendez_vous', ch['visio'] or 'sans', ch['lien'], ch['zone'], 'synchronisation', stamp))
    agenda5634._mirror(desk, row)
    return local_id
