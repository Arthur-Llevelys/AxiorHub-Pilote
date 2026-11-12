"""5.6.97 (lot documents-mission, § 17) : portefeuille et prochaine action de chaque affaire, deux listes d'Aujourd'hui.

Source de vérité : les objets métier existants. La prochaine action est une **référence** (type + identifiant) vers une tâche ouverte (``tasks``,
complétée par ``taches5688``), une échéance confirmée (``deadlines450``) ou une demande de pièces (registre du lot gestion, si présent) ; rien
n'est recopié. Ce module ne garde que ce qui n'existe nulle part ailleurs : responsable, état opératoire, attente (de qui, quoi, depuis quand,
quand recontrôler) et l'historique des attributions.

- ``etat`` : lecture d'une affaire. Une action terminée, annulée, rattachée à un autre dossier ou devenue invisible n'est plus « active » :
  elle est signalée et la suivante est suggérée (échéance confirmée la plus proche, puis tâche ouverte), sans rien écrire.
- ``definir`` : écriture protégée par la version attendue (409 sinon) et par une clé d'idempotence (un double clic rend le même état).
- ``ajouter_action`` / ``preparer_relance`` : créent une tâche par le service des tâches (``taches5688.create``), sans aucun modèle, et la
  désignent comme prochaine action ; une relance ne contacte personne.
- ``listes`` : « Dossiers sans prochaine action » et « Attentes à relancer », calculées sans IA à partir des dates et des objets. Le périmètre
  de l'acteur (``acces5697``) est appliqué AVANT le filtre « Mes dossiers », les totaux et le tri : un dossier masqué ne change aucun compteur.
  Seuil sans date de recontrôle : ``SEUIL_DEFAUT`` jours après le début de l'attente (réglage ``portefeuille5697:seuil_jours``).
- Les traitements d'arrière-plan et les imports n'écrivent jamais ici : seule ``definir`` (geste d'une personne) change ``attente_depuis``.
  Un fichier ou un courriel arrivé dans le dossier depuis le début de l'attente est signalé comme « dépôt à qualifier ».
"""
from datetime import date, datetime, timedelta, timezone
import hashlib
import re
import uuid

from . import acces5697
from .common import Stop

ETATS = {'a_traiter': 'À traiter', 'en_cours': 'En cours', 'attente_client': 'En attente du client', 'attente_tiers': 'En attente d’un tiers',
         'pret_a_cloturer': 'Prêt à clôturer'}
ATTENTES = ('attente_client', 'attente_tiers')
ACTIFS = ('a_traiter', 'en_cours')
TYPES = {'tache': 'Tâche', 'echeance': 'Échéance confirmée', 'demande_pieces': 'Demande de pièces'}
ECHEANCES_CONFIRMEES = ('confirmee', 'manuel')
CLOS = ('clos', 'cloture', 'cloturee', 'archive', 'archivee', 'termine', 'ferme')
SEUIL_DEFAUT = 14
REGLAGE_SEUIL = 'portefeuille5697:seuil_jours'
DEPOTS = ('nouveau_document', 'nouvelle_version', 'nouveau_courriel')     # journal des changements du dossier (memoire5651)
SCHEMA = '''
CREATE TABLE IF NOT EXISTS portefeuille5697(dossier_id TEXT PRIMARY KEY, responsable_id TEXT NOT NULL DEFAULT '', etat_operatoire TEXT NOT NULL DEFAULT 'a_traiter',
  next_action_type TEXT NOT NULL DEFAULT '', next_action_id TEXT NOT NULL DEFAULT '', attente_de TEXT NOT NULL DEFAULT '', motif_attente TEXT NOT NULL DEFAULT '',
  attente_depuis TEXT NOT NULL DEFAULT '', revoir_le TEXT NOT NULL DEFAULT '', version INTEGER NOT NULL DEFAULT 0, derniere_cle TEXT NOT NULL DEFAULT '',
  modifie_par TEXT NOT NULL DEFAULT '', modifie_le TEXT NOT NULL DEFAULT '');
CREATE INDEX IF NOT EXISTS portefeuille5697_resp ON portefeuille5697(responsable_id);
CREATE TABLE IF NOT EXISTS portefeuille5697_historique(id TEXT PRIMARY KEY, dossier_id TEXT NOT NULL, champ TEXT NOT NULL, avant TEXT NOT NULL DEFAULT '',
  apres TEXT NOT NULL DEFAULT '', acteur TEXT NOT NULL, le TEXT NOT NULL);
CREATE INDEX IF NOT EXISTS portefeuille5697_historique_dossier ON portefeuille5697_historique(dossier_id, le);
CREATE TABLE IF NOT EXISTS portefeuille5697_ops(cle TEXT PRIMARY KEY, dossier_id TEXT NOT NULL, nature TEXT NOT NULL, objet TEXT NOT NULL DEFAULT '', acteur TEXT NOT NULL,
  le TEXT NOT NULL);
'''
CHAMPS = ('responsable_id', 'etat_operatoire', 'next_action_type', 'next_action_id', 'attente_de', 'motif_attente', 'attente_depuis', 'revoir_le')


def schema(desk):
    desk.db.executescript(SCHEMA)
    desk.db.commit()


def _now():
    return datetime.now(timezone.utc).isoformat(timespec='microseconds')


def aujourd_hui(desk, now=None):
    """Jour du cabinet (fuseau de l'agenda) ; ``now`` : instant simulé du banc."""
    from .cockpit530 import tz
    instant = now or datetime.now(timezone.utc)
    if isinstance(instant, date) and not isinstance(instant, datetime):
        return instant
    return instant.astimezone(tz(desk)).date()


def _jour(value):
    value = str(value or '').strip()[:10]
    if not value:
        return ''
    try:
        return date.fromisoformat(value).isoformat()
    except ValueError:
        raise Stop('date_invalide') from None


def _texte(value, limite):
    return re.sub(r'\s+', ' ', str(value or '')).strip()[:limite]


def seuil(desk):
    try:
        value = int(desk.settings(REGLAGE_SEUIL, SEUIL_DEFAUT))
    except (TypeError, ValueError):
        value = SEUIL_DEFAUT
    return max(1, min(value, 365))


def definir_seuil(desk, acteur, jours):
    a = acces5697.acteur_de(acteur)
    if a.role != 'administrateur':
        raise Stop('role_insuffisant')
    try:
        jours = int(jours)
    except (TypeError, ValueError):
        raise Stop('seuil_invalide') from None
    if not 1 <= jours <= 365:
        raise Stop('seuil_invalide')
    desk.setting(REGLAGE_SEUIL, jours)
    return {'seuil_jours': jours}


# ------------------------------------------------------------------------------------------------ registre des affaires
def dossiers_actifs(desk):
    """Affaires actives : registre historique des dossiers, qui porte aussi la projection du registre unique (``dossiers5697.projeter`` : un
    dossier archivé y est marqué). Rend {id: {'id', 'libelle'}} SANS filtre de droits : l'appelant applique le périmètre avant tout calcul."""
    from .common import load_matters, matter_display
    out = {}
    for m in load_matters(desk.c, unfiltered=True):
        etat = str((m.get('registre5697') or {}).get('statut') or m.get('status') or m.get('etat') or '')
        if etat not in CLOS:
            reference = (m.get('registre5697') or {}).get('reference')
            out[m['id']] = {'id': m['id'], 'libelle': ('%s · %s' % (reference, matter_display(m))) if reference else matter_display(m)}
    return out


def _registre_unique(desk, dossier_id):
    """Ligne du registre unique des affaires (lot dossiers) : le responsable y est la source de vérité ; absente : ``None``."""
    try:
        row = desk.db.execute('SELECT id,responsable,version FROM dossiers5697 WHERE id=?', (dossier_id,)).fetchone()
    except Exception:   # noqa: BLE001 — registre unique pas encore créé : dossier historique
        return None
    return dict(row) if row else None


def _responsable(desk, ligne):
    unique = _registre_unique(desk, ligne['dossier_id'])
    return unique['responsable'] if unique else ligne['responsable_id']


# ------------------------------------------------------------------------------------------------ objets métier référencés
def _taches(desk, dossier_id=None):
    from . import taches5688
    rows = [t for t in taches5688.open_tasks(desk) if t.get('statut') not in ('terminee', 'annulee')]
    return [t for t in rows if dossier_id is None or str(t.get('matter') or '') == dossier_id]


def _tache(desk, ident):
    """Tâche par identifiant, ouverte ou non : (ligne, ouverte)."""
    from . import taches5688
    taches5688.schema(desk)
    row = desk.db.execute('SELECT t.id,t.matter,t.title,t.due,t.status,x.etat,x.responsable FROM tasks t LEFT JOIN taches5688 x ON x.task_id=t.id WHERE t.id=?', (ident,)).fetchone()
    if row:
        ouverte = row['status'] == 'open' and (row['etat'] or 'a_faire') not in ('terminee', 'annulee')
        return {'id': row['id'], 'matter': row['matter'] or '', 'title': row['title'] or '', 'due': row['due'] or '', 'responsable': row['responsable'] or ''}, ouverte
    try:
        row = desk.db.execute('SELECT id,matter,title,due,status FROM work_tasks_v211 WHERE id=?', (ident,)).fetchone()
    except Exception:   # noqa: BLE001 — table des tâches Nextcloud absente : la tâche est inconnue
        row = None
    if row:
        return {'id': row['id'], 'matter': row['matter'] or '', 'title': row['title'] or '', 'due': row['due'] or '', 'responsable': ''}, row['status'] in ('todo', 'in_progress', 'blocked')
    return None, False


def _echeances(desk, dossier_id=None):
    from . import echeances450
    echeances450.ensure_schema(desk)
    sql = "SELECT id,matter,rule_id,start_event,due,status,note FROM deadlines450 WHERE status IN ('confirmee','manuel') AND due<>''"
    args = ()
    if dossier_id is not None:
        sql, args = sql + ' AND matter=?', (dossier_id,)
    return [dict(r) for r in desk.db.execute(sql + ' ORDER BY due, id', args)]


def _echeance(desk, ident):
    from . import echeances450
    echeances450.ensure_schema(desk)
    row = desk.db.execute('SELECT id,matter,rule_id,start_event,due,status,note FROM deadlines450 WHERE id=?', (ident,)).fetchone()
    if not row:
        return None, False
    return dict(row), row['status'] in ECHEANCES_CONFIRMEES and bool(row['due'])


def _libelle_echeance(e):
    nom = ''
    try:
        from . import deadlines450
        nom = str(deadlines450.rule(e['rule_id']).get('label') or '')
    except Exception:   # noqa: BLE001 — règle inconnue ou retirée : la note ou un libellé neutre
        nom = ''
    return _texte(e.get('note') or nom or 'Échéance confirmée', 200)


def _demande_pieces(desk, ident):
    """Demande de pièces du lot gestion (§ 20), lue par son service si elle existe ; sinon inconnue (jamais devinée)."""
    for nom in ('pieces5697', 'gestion5697'):
        try:
            module = __import__('agent.' + nom, fromlist=['lire_demande'])
        except ImportError:
            continue
        lire = getattr(module, 'lire_demande', None)
        if not lire:
            continue
        try:
            d = dict(lire(desk, acces5697.AGENT, ident))
        except Stop:
            return None, False
        return {'id': ident, 'matter': str(d.get('dossier_id') or d.get('matter') or ''), 'title': str(d.get('titre') or d.get('objet') or 'Demande de pièces'),
                'due': str(d.get('echeance') or d.get('due') or '')}, str(d.get('etat') or '') not in ('remise', 'close', 'annulee', 'terminee')
    return None, False


def resoudre(desk, type_, ident):
    """(objet, ouvert) d'une référence de prochaine action ; type inconnu : (None, False)."""
    if type_ == 'tache':
        return _tache(desk, ident)
    if type_ == 'echeance':
        e, ouverte = _echeance(desk, ident)
        if e:
            e = {'id': e['id'], 'matter': e['matter'], 'title': _libelle_echeance(e), 'due': e['due']}
        return e, ouverte
    if type_ == 'demande_pieces':
        return _demande_pieces(desk, ident)
    return None, False


def candidats(desk, acteur, dossier_id):
    """Actions possibles du dossier : échéances confirmées (prioritaires), puis tâches ouvertes, par date."""
    acces5697.exiger(desk, acteur, dossier_id)
    out = [{'ref': 'echeance:' + e['id'], 'type': 'echeance', 'id': e['id'], 'libelle': _libelle_echeance(e), 'date': e['due'], 'type_libelle': TYPES['echeance']}
           for e in _echeances(desk, dossier_id)]
    out += [{'ref': 'tache:' + t['id'], 'type': 'tache', 'id': t['id'], 'libelle': _texte(t.get('title'), 200), 'date': str(t.get('due') or ''), 'type_libelle': TYPES['tache']}
            for t in sorted(_taches(desk, dossier_id), key=lambda t: (str(t.get('due') or '9999'), str(t['id'])))]
    return out


# ------------------------------------------------------------------------------------------------ lecture
def _cache(desk):
    """Tâches ouvertes et échéances confirmées lues une fois pour toute une liste (et non une fois par dossier)."""
    out = {'taches': {}, 'echeances': {}}
    for t in _taches(desk):
        out['taches'].setdefault(str(t.get('matter') or ''), []).append(t)
    for e in _echeances(desk):
        out['echeances'].setdefault(e['matter'], []).append(e)
    return out


def _ligne(desk, dossier_id):
    row = desk.db.execute('SELECT * FROM portefeuille5697 WHERE dossier_id=?', (dossier_id,)).fetchone()
    if row:
        return dict(row)
    return {'dossier_id': dossier_id, 'responsable_id': '', 'etat_operatoire': 'a_traiter', 'next_action_type': '', 'next_action_id': '', 'attente_de': '',
            'motif_attente': '', 'attente_depuis': '', 'revoir_le': '', 'version': 0, 'derniere_cle': '', 'modifie_par': '', 'modifie_le': ''}


def _depots_depuis(desk, dossier_id, depuis):
    """Fichiers et courriels arrivés dans le dossier depuis le début de l'attente (journal du dossier, non technique)."""
    if not depuis:
        return []
    try:
        from . import memoire5651
        memoire5651.ensure_schema(desk)
        rows = desk.db.execute('SELECT kind,title,created FROM d51_changes WHERE matter=? AND technical=0 AND kind IN (%s) AND substr(created,1,10)>=? ORDER BY id DESC LIMIT 5'
                               % ','.join('?' * len(DEPOTS)), (dossier_id,) + DEPOTS + (depuis,)).fetchall()
    except Exception:   # noqa: BLE001 — journal illisible : rien n'est signalé, rien n'est inventé
        return []
    return [{'nature': r['kind'], 'titre': _texte(r['title'], 160), 'le': r['created']} for r in rows]


def _calculer(desk, ligne, jour, seuil_jours, perimetre=None, cache=None):
    """Projection déterministe d'une ligne : action active ou non, suggestion, relance due, dépôts à qualifier, échéance prioritaire."""
    d = dict(ligne)
    d['responsable_id'] = _responsable(desk, d)
    d['etat_libelle'] = ETATS.get(d['etat_operatoire'], d['etat_operatoire'])
    action, active, motif = None, False, ''
    if d['next_action_type'] and d['next_action_id']:
        objet, ouvert = resoudre(desk, d['next_action_type'], d['next_action_id'])
        if objet is None:
            motif = 'introuvable'
        elif str(objet.get('matter') or '') != d['dossier_id']:
            motif = 'autre_dossier'
        elif perimetre is not None and objet.get('matter') not in perimetre:
            motif = 'introuvable'
        elif not ouvert:
            motif = 'terminee'
        else:
            active = True
        if objet is not None and motif != 'autre_dossier':
            action = {'ref': d['next_action_type'] + ':' + d['next_action_id'], 'type': d['next_action_type'], 'type_libelle': TYPES.get(d['next_action_type'], ''),
                      'libelle': _texte(objet.get('title'), 200), 'date': str(objet.get('due') or '')}
    d['action'] = action if active else None
    d['action_retiree'] = motif          # « terminee » : la référence n'est plus active, une suivante est suggérée
    echeances = cache['echeances'].get(d['dossier_id'], []) if cache else _echeances(desk, d['dossier_id'])
    prochaine = next((e for e in echeances if e['due'] >= jour.isoformat()), None) or (echeances[0] if echeances else None)
    d['echeance_prioritaire'] = {'ref': 'echeance:' + prochaine['id'], 'libelle': _libelle_echeance(prochaine), 'date': prochaine['due']} if prochaine else None
    suggestion = None
    if not active:
        if prochaine:
            suggestion = dict(d['echeance_prioritaire'], type='echeance', type_libelle=TYPES['echeance'])
        else:
            taches = sorted(cache['taches'].get(d['dossier_id'], []) if cache else _taches(desk, d['dossier_id']), key=lambda t: (str(t.get('due') or '9999'), str(t['id'])))
            if taches:
                suggestion = {'ref': 'tache:' + taches[0]['id'], 'type': 'tache', 'type_libelle': TYPES['tache'], 'libelle': _texte(taches[0].get('title'), 200),
                              'date': str(taches[0].get('due') or '')}
    d['suggestion'] = suggestion
    d['en_attente'] = d['etat_operatoire'] in ATTENTES
    d['a_relancer'] = False
    d['revoir_effectif'] = ''
    d['depots'] = []
    if d['en_attente']:
        revoir = d['revoir_le']
        if not revoir and d['attente_depuis']:
            revoir = (date.fromisoformat(d['attente_depuis'][:10]) + timedelta(days=seuil_jours)).isoformat()
        d['revoir_effectif'] = revoir
        d['a_relancer'] = bool(revoir) and revoir <= jour.isoformat()
        d['depots'] = _depots_depuis(desk, d['dossier_id'], d['attente_depuis'][:10])
    # date utile : échéance confirmée prioritaire, puis date de l'action, puis recontrôle de l'attente
    dates = [x for x in ((d['echeance_prioritaire'] or {}).get('date'), (d['action'] or {}).get('date'), d['revoir_effectif']) if x]
    d['date_utile'] = min(dates) if dates else ''
    if d['etat_operatoire'] in ATTENTES and d['a_relancer']:
        d['etat_libelle'] = 'À relancer'
    return d


def etat(desk, acteur, dossier_id, now=None):
    """État opératoire d'une affaire visible par l'acteur (404 neutre sinon)."""
    schema(desk)
    acces5697.exiger(desk, acteur, dossier_id)
    return _calculer(desk, _ligne(desk, dossier_id), aujourd_hui(desk, now), seuil(desk))


def historique(desk, acteur, dossier_id, limite=50):
    schema(desk)
    acces5697.exiger(desk, acteur, dossier_id)
    return [dict(r) for r in desk.db.execute('SELECT champ,avant,apres,acteur,le FROM portefeuille5697_historique WHERE dossier_id=? ORDER BY le DESC LIMIT ?',
                                             (dossier_id, int(limite)))]


# ------------------------------------------------------------------------------------------------ écriture
def _verifier_reference(desk, a, dossier_id, ref):
    if not ref:
        return '', ''
    type_, _, ident = str(ref).partition(':')
    if type_ not in TYPES or not ident:
        raise Stop('prochaine_action_invalide')
    objet, ouvert = resoudre(desk, type_, ident)
    if objet is None or not acces5697.peut_lire_dossier(desk, a, objet.get('matter')):
        raise Stop('prochaine_action_introuvable')
    if str(objet.get('matter') or '') != dossier_id:
        raise Stop('prochaine_action_autre_dossier')
    if not ouvert:
        raise Stop('prochaine_action_terminee')
    return type_, ident


def definir(desk, acteur, dossier_id, data, version_attendue, cle=''):
    """Responsable, état, prochaine action, attente. ``version_attendue`` : version lue par la personne (409 si elle a changé)."""
    schema(desk)
    a = acces5697.exiger(desk, acteur, dossier_id)
    cle = _texte(cle, 120)
    if cle:
        fait = desk.db.execute('SELECT dossier_id FROM portefeuille5697_ops WHERE cle=?', (cle,)).fetchone()
        if fait:
            if fait['dossier_id'] != dossier_id:
                raise Stop('cle_idempotence_deja_utilisee')
            return etat(desk, a, dossier_id)
    actuel = _ligne(desk, dossier_id)
    actuel['responsable_id'] = _responsable(desk, actuel)
    try:
        attendue = int(version_attendue)
    except (TypeError, ValueError):
        raise Stop('version_attendue_requise') from None
    nouveau = dict(actuel)
    if 'responsable_id' in data:
        responsable = _texte(data.get('responsable_id'), 120)
        if responsable != actuel['responsable_id']:
            if a.role == 'assistant':
                raise Stop('role_insuffisant')
            if responsable:
                from .audiences5688 import check_person
                check_person(desk, responsable, dossier_id, None, 'responsable_inconnu')
        nouveau['responsable_id'] = responsable
    if 'etat_operatoire' in data:
        valeur = str(data.get('etat_operatoire') or '')
        if valeur not in ETATS:
            raise Stop('etat_operatoire_invalide')
        nouveau['etat_operatoire'] = valeur
    if 'next_action' in data:
        nouveau['next_action_type'], nouveau['next_action_id'] = _verifier_reference(desk, a, dossier_id, _texte(data.get('next_action'), 200))
    jour = aujourd_hui(desk)
    if nouveau['etat_operatoire'] in ATTENTES:
        for champ, limite in (('attente_de', 200), ('motif_attente', 500)):
            if champ in data:
                nouveau[champ] = _texte(data.get(champ), limite)
        if 'revoir_le' in data:
            nouveau['revoir_le'] = _jour(data.get('revoir_le'))
        if not nouveau['motif_attente']:
            raise Stop('motif_attente_requis')
        # le début de l'attente est posé par la personne qui la déclare, jamais par un import ni un traitement de fond
        if actuel['etat_operatoire'] not in ATTENTES or 'attente_depuis' in data:
            nouveau['attente_depuis'] = _jour(data.get('attente_depuis')) or jour.isoformat()
    else:
        nouveau.update(attente_de='', motif_attente='', attente_depuis='', revoir_le='')
    changes = [c for c in CHAMPS if str(nouveau[c]) != str(actuel[c])]
    now = _now()
    unique = _registre_unique(desk, dossier_id) if 'responsable_id' in changes else None
    if unique:      # le responsable vit dans le registre unique des affaires : il y est modifié (version et clé propres), jamais recopié ailleurs
        if _ligne(desk, dossier_id)['version'] != attendue:
            raise Stop('version_en_conflit')
        from . import dossiers5697
        dossiers5697.modifier(desk, a, dossier_id, unique['version'], {'responsable': nouveau['responsable_id']},
                              'pf.' + hashlib.sha256((cle or uuid.uuid4().hex).encode('utf-8')).hexdigest()[:40])
    desk.db.execute('BEGIN IMMEDIATE')
    try:
        courante = desk.db.execute('SELECT version FROM portefeuille5697 WHERE dossier_id=?', (dossier_id,)).fetchone()
        if (courante['version'] if courante else 0) != attendue:
            raise Stop('version_en_conflit')
        if changes:
            version = attendue + 1
            desk.db.execute('INSERT INTO portefeuille5697(dossier_id,%s,version,derniere_cle,modifie_par,modifie_le) VALUES(?,%s,?,?,?,?) '
                            'ON CONFLICT(dossier_id) DO UPDATE SET %s, version=excluded.version, derniere_cle=excluded.derniere_cle, modifie_par=excluded.modifie_par, '
                            'modifie_le=excluded.modifie_le' % (','.join(CHAMPS), ','.join('?' * len(CHAMPS)), ','.join('%s=excluded.%s' % (c, c) for c in CHAMPS)),
                            (dossier_id,) + tuple(nouveau[c] for c in CHAMPS) + (version, cle, a.compte, now))
            for champ in changes:
                desk.db.execute('INSERT INTO portefeuille5697_historique(id,dossier_id,champ,avant,apres,acteur,le) VALUES(?,?,?,?,?,?,?)',
                                (uuid.uuid4().hex, dossier_id, champ, str(actuel[champ])[:500], str(nouveau[champ])[:500], a.compte, now))
        if cle:
            desk.db.execute('INSERT INTO portefeuille5697_ops(cle,dossier_id,nature,objet,acteur,le) VALUES(?,?,?,?,?,?)', (cle, dossier_id, 'definir', '', a.compte, now))
        desk.db.commit()
    except BaseException:
        desk.db.rollback()
        raise
    if changes:
        desk.audit('portefeuille5697_modifie', {'dossier': dossier_id, 'champs': changes, 'par': a.compte[:80]})
    return etat(desk, a, dossier_id)


def _creer_tache(desk, a, dossier_id, titre, echeance, cle, nature):
    """Tâche créée par le service des tâches (sans modèle), une seule fois par clé ; rend son identifiant."""
    from . import taches5688
    taches5688.schema(desk)
    cle = _texte(cle, 120)
    if not cle:
        raise Stop('cle_idempotence_requise')
    desk.db.execute('BEGIN IMMEDIATE')
    try:
        fait = desk.db.execute('SELECT dossier_id,objet FROM portefeuille5697_ops WHERE cle=?', (cle,)).fetchone()
        if fait and fait['objet']:
            desk.db.rollback()
            if fait['dossier_id'] != dossier_id:
                raise Stop('cle_idempotence_deja_utilisee')
            return fait['objet'], False
        reprise = None
        if fait:      # coupure entre la création de la tâche et son inscription ici : la tâche déjà créée est reprise, jamais doublée
            reprise = desk.db.execute("SELECT t.id FROM tasks t JOIN taches5688 x ON x.task_id=t.id WHERE x.origine='dossier' AND x.origine_ref=? AND t.title=? "
                                      "AND x.createur=? ORDER BY t.created DESC LIMIT 1", (dossier_id, titre, a.compte)).fetchone()
        else:
            desk.db.execute('INSERT INTO portefeuille5697_ops(cle,dossier_id,nature,objet,acteur,le) VALUES(?,?,?,?,?,?)', (cle, dossier_id, nature, '', a.compte, _now()))
        if reprise:
            desk.db.execute('UPDATE portefeuille5697_ops SET objet=? WHERE cle=?', (reprise['id'], cle))
        desk.db.commit()
    except BaseException:
        if desk.db.in_transaction:
            desk.db.rollback()
        raise
    if reprise:
        return reprise['id'], False
    out = taches5688.create(desk, {'title': titre, 'matter': dossier_id, 'origine': 'dossier', 'origine_ref': dossier_id, 'due': echeance}, a.compte)
    desk.db.execute('UPDATE portefeuille5697_ops SET objet=? WHERE cle=?', (out['id'], cle))
    desk.db.commit()
    return out['id'], True


def ajouter_action(desk, acteur, dossier_id, titre, echeance, version_attendue, cle):
    """« Ajouter une prochaine action » : une tâche ouverte du dossier, désignée comme prochaine action (deux gestes en un, rejouable)."""
    schema(desk)
    a = acces5697.exiger(desk, acteur, dossier_id)
    titre = _texte(titre, 200)
    if len(titre) < 3:
        raise Stop('titre_action_requis')
    fait = desk.db.execute('SELECT objet FROM portefeuille5697_ops WHERE cle=? AND dossier_id=?', (_texte(cle, 120), dossier_id)).fetchone()
    if not (fait and fait['objet']):       # premier passage : la version lue doit être la version courante (409 sinon), avant toute création
        try:
            attendue = int(version_attendue)
        except (TypeError, ValueError):
            raise Stop('version_attendue_requise') from None
        if _ligne(desk, dossier_id)['version'] != attendue:
            raise Stop('version_en_conflit')
    tache, _ = _creer_tache(desk, a, dossier_id, titre, _jour(echeance), cle, 'ajouter_action')
    ligne = _ligne(desk, dossier_id)
    if not (ligne['next_action_type'] == 'tache' and ligne['next_action_id'] == tache):     # rejoué après une coupure : la référence est posée une fois
        data = {'next_action': 'tache:' + tache}
        if ligne['etat_operatoire'] not in ACTIFS + ATTENTES:
            data['etat_operatoire'] = 'en_cours'
        definir(desk, a, dossier_id, data, ligne['version'], cle + ':ref')
    return dict(etat(desk, a, dossier_id), tache=tache)


def preparer_relance(desk, acteur, dossier_id, cle, now=None):
    """« Préparer une relance » : une tâche « Relancer … » pour aujourd'hui, prochaine action ; l'attente continue. Rien n'est envoyé."""
    schema(desk)
    a = acces5697.exiger(desk, acteur, dossier_id)
    ligne = _ligne(desk, dossier_id)
    if ligne['etat_operatoire'] not in ATTENTES:
        raise Stop('aucune_attente')
    qui = ligne['attente_de'] or ('le client' if ligne['etat_operatoire'] == 'attente_client' else 'le tiers')
    titre = _texte('Relancer %s : %s' % (qui, ligne['motif_attente']), 200)
    tache, _ = _creer_tache(desk, a, dossier_id, titre, aujourd_hui(desk, now).isoformat(), cle, 'relance')
    ligne = _ligne(desk, dossier_id)
    if ligne['next_action_id'] != tache:
        definir(desk, a, dossier_id, {'next_action': 'tache:' + tache}, ligne['version'], cle + ':ref')
    return dict(etat(desk, a, dossier_id, now), tache_relance=tache)


# ------------------------------------------------------------------------------------------------ Aujourd'hui
def listes(desk, acteur, mes_dossiers=False, now=None, limite=8):
    """Deux listes d'Aujourd'hui. Ordre des opérations : périmètre de l'acteur, puis filtre « Mes dossiers », puis calcul, totaux et tri."""
    schema(desk)
    a = acces5697.acteur_de(acteur)
    perimetre = acces5697.dossiers_autorises(desk, a)
    actifs = {k: v for k, v in dossiers_actifs(desk).items() if k in perimetre}         # droits d'abord
    lignes = {r['dossier_id']: dict(r) for r in desk.db.execute('SELECT * FROM portefeuille5697')}
    if mes_dossiers:
        actifs = {k: v for k, v in actifs.items() if _responsable(desk, lignes.get(k) or _ligne(desk, k)) == a.compte}
    jour, s = aujourd_hui(desk, now), seuil(desk)
    cache = _cache(desk)
    sans, attentes = [], []
    for ident, dossier in actifs.items():
        d = _calculer(desk, lignes.get(ident) or _ligne(desk, ident), jour, s, perimetre, cache)
        d['libelle'] = dossier['libelle']
        if d['etat_operatoire'] in ACTIFS and not d['action']:
            d['motif'] = ('L’action désignée est terminée : choisir la suivante.' if d['action_retiree'] == 'terminee' else
                          'L’action désignée n’est plus disponible.' if d['action_retiree'] else 'Aucune prochaine action désignée.')
            sans.append(d)
        elif d['en_attente'] and (d['a_relancer'] or d['depots']):
            qui = d['attente_de'] or ('client' if d['etat_operatoire'] == 'attente_client' else 'tiers')
            morceaux = []
            if d['depots']:
                morceaux.append('Nouveau dépôt depuis le %s : à qualifier' % _fr(d['depots'][-1]['le'][:10]))
            if d['a_relancer']:
                morceaux.append('Attente de %s depuis le %s (%s) ; recontrôle prévu le %s' % (qui, _fr(d['attente_depuis']), d['motif_attente'], _fr(d['revoir_effectif'])))
            d['motif'] = ' · '.join(morceaux)
            attentes.append(d)
    cle = lambda d: (d['date_utile'] or '9999-12-31', d['dossier_id'])
    sans.sort(key=cle)
    attentes.sort(key=lambda d: (d['revoir_effectif'] or '9999-12-31', d['dossier_id']))
    return {'jour': jour.isoformat(), 'seuil_jours': s, 'mes_dossiers': bool(mes_dossiers),
            'sans_action': {'nombre': len(sans), 'elements': sans[:limite]}, 'attentes': {'nombre': len(attentes), 'elements': attentes[:limite]}}


def lignes_portefeuille(desk, acteur, ids, now=None):
    """Colonnes de la liste Dossiers (§ 17) pour les dossiers ``ids`` visibles : responsable, prochaine action, date utile, état, dernière activité."""
    schema(desk)
    a = acces5697.acteur_de(acteur)
    perimetre = acces5697.dossiers_autorises(desk, a)
    jour, s = aujourd_hui(desk, now), seuil(desk)
    cache = _cache(desk)
    out = {}
    for ident in ids:
        if ident not in perimetre:
            continue
        d = _calculer(desk, _ligne(desk, ident), jour, s, perimetre, cache)
        d['derniere_activite'] = derniere_activite(desk, ident)
        out[ident] = d
    return out


def derniere_activite(desk, dossier_id):
    """Dernier événement significatif du dossier (journal non technique, documents, portefeuille) ; vide si rien n'est connu."""
    dates = []
    for sql in ('SELECT MAX(created) FROM d51_changes WHERE matter=? AND technical=0', "SELECT MAX(updated) FROM prepared_items5689 WHERE matter_id=?",
                'SELECT MAX(modifie_le) FROM portefeuille5697 WHERE dossier_id=?'):
        try:
            value = desk.db.execute(sql, (dossier_id,)).fetchone()[0]
        except Exception:   # noqa: BLE001 — table absente : source ignorée
            value = None
        if value:
            dates.append(str(value))
    return max(dates) if dates else ''


def _fr(value):
    value = str(value or '')[:10]
    try:
        return date.fromisoformat(value).strftime('%d/%m/%Y')
    except ValueError:
        return value or '—'


def export_lignes(desk):
    """Pour ``export5688`` : lignes brutes (aucun calcul, aucun objet d'un autre registre recopié)."""
    schema(desk)
    return {'portefeuille5697': [dict(r) for r in desk.db.execute('SELECT * FROM portefeuille5697')],
            'portefeuille5697_historique': [dict(r) for r in desk.db.execute('SELECT * FROM portefeuille5697_historique')]}
