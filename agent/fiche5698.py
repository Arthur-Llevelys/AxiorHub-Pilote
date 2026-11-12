"""5.6.98 : fiche du dossier organisée comme dans un logiciel de gestion de cabinet — Parties, Juridiction, Fichiers.

**Parties** : les parties du registre unique (``dossier_parties5697``) reçoivent, par migration additive (``ALTER TABLE … ADD COLUMN``,
rejouable), qualité, personne physique ou morale, téléphone, adresse, SIREN (personne morale, clé de Luhn), avocat qui la représente,
note, version et dernier contrôle de conflits. Elles sont regroupées : Clients, Parties adverses, Avocats, Autres intervenants. Ajout,
modification et retrait passent par une commande idempotente (``dossiers5697._commande``) et, pour une modification ou un retrait, la
version attendue (``version_perimee`` sinon : rien n'est écrasé). L'ajout d'une partie adverse relance la vérification des conflits de la
création (``dossiers5697.verifier_conflits``) et garde le résultat sur la fiche de la partie. Les parties lues dans les pièces ne sont
jamais ajoutées automatiquement : l'écran les propose, l'avocat valide.

**Juridiction** : une ou plusieurs instances (``dossier_instances5698``). L'instance principale n'a pas sa propre juridiction ni son
propre RG : ce sont ceux du registre ``dossiers5697`` (une seule vérité) ; la modifier écrit le registre dans la même transaction, et une
modification du registre se lit aussitôt sur l'instance. Migration : un dossier qui a une juridiction ou un RG et aucune instance reçoit son
instance principale (rejouable, sans perte).

**Fichiers** : arborescence réelle du répertoire du dossier, en lecture seule, bornée (profondeur, entrées lues, sous-répertoires comptés,
pagination), uniquement sous le chemin du dossier et dans les racines autorisées ; un stockage injoignable est un état, jamais « vide ».

Aucun appel à un modèle ; tout fonctionne IA en pause. Dossier absent ou réservé : ``Stop('introuvable')`` (même réponse).
"""
from datetime import date, datetime, timezone
from email.utils import parsedate_to_datetime
import re
import sqlite3
import uuid

from .common import Stop, clean_path, under
from . import dossiers5697 as D

GROUPES = (('clients', 'Clients', ('client',)),
           ('adverses', 'Parties adverses', ('adverse',)),
           ('avocats', 'Avocats', ('avocat_client', 'conseil_adverse', 'avocat_postulant')),
           ('autres', 'Autres intervenants', ('expert', 'commissaire_justice', 'mandataire_judiciaire', 'notaire', 'assureur', 'correspondant',
                                              'autre', 'juridiction')))
GROUPE_DU_ROLE = {r: g for g, _, roles in GROUPES for r in roles}
ROLES_AVOCATS = GROUPES[2][2]
ROLES_HORS_FORMULAIRE = ('juridiction',)   # ancienne saisie : affichée et conservée ; la juridiction a désormais son onglet
TYPES_PERSONNE = {'': 'Non précisé', 'physique': 'Personne physique', 'morale': 'Personne morale'}
DEGRES = {'premiere_instance': 'Première instance', 'refere': 'Référé', 'appel': 'Appel', 'cassation': 'Cassation', 'renvoi': 'Renvoi après cassation',
          'execution': 'Exécution', 'autre': 'Autre', 'a_preciser': 'Degré à préciser'}
NATURES_INSTANCE = {'': 'Non précisée', 'fond': 'Fond', 'refere': 'Référé', 'requete': 'Requête', 'appel': 'Appel', 'pourvoi': 'Pourvoi', 'autre': 'Autre'}
ETATS_INSTANCE = {'en_cours': 'En cours', 'juge': 'Jugé', 'radie': 'Radié', 'clos': 'Clos'}
COLONNES_PARTIES = (('qualite', "TEXT NOT NULL DEFAULT ''"), ('type_personne', "TEXT NOT NULL DEFAULT ''"), ('telephone', "TEXT NOT NULL DEFAULT ''"),
                    ('adresse', "TEXT NOT NULL DEFAULT ''"), ('siren', "TEXT NOT NULL DEFAULT ''"), ('avocat_id', "TEXT NOT NULL DEFAULT ''"),
                    ('note', "TEXT NOT NULL DEFAULT ''"), ('version', 'INTEGER NOT NULL DEFAULT 1'), ('modifie_le', "TEXT NOT NULL DEFAULT ''"),
                    ('modifie_par', "TEXT NOT NULL DEFAULT ''"), ('conflit_id', "TEXT NOT NULL DEFAULT ''"), ('conflit_statut', "TEXT NOT NULL DEFAULT ''"),
                    ('conflit_resume', "TEXT NOT NULL DEFAULT ''"), ('conflit_le', "TEXT NOT NULL DEFAULT ''"))
CHAMPS_PARTIE = ('nom', 'role', 'qualite', 'type_personne', 'courriel', 'telephone', 'adresse', 'siren', 'avocat_id', 'note')
CHAMPS_INSTANCE = ('degre', 'juridiction', 'chambre', 'rg', 'portalis', 'nature', 'saisine', 'magistrat', 'greffe', 'etat', 'principale')
CONFLITS = {'aucun_resultat': 'Aucun rapprochement', 'information': 'Information (même côté)', 'a_examiner': 'À examiner',
            'non_verifie': 'Vérification impossible pour le moment'}
MAX_PROFONDEUR = 6       # sous-répertoires sous le répertoire du dossier
PAR_PAGE = 50            # entrées affichées par page
MAX_LUES = 2000          # entrées lues dans un répertoire (au-delà : signalé « liste tronquée »)
MAX_COMPTES = 25         # sous-répertoires dont le contenu est compté (une lecture chacun)
ABSENT = ('fichier_nextcloud_introuvable', 'repertoire_absent', 'http_404', 'chemin_absent', 'chemin_est_un_fichier')

SCHEMA = '''
CREATE TABLE IF NOT EXISTS dossier_instances5698(
  id TEXT PRIMARY KEY, dossier_id TEXT NOT NULL, degre TEXT NOT NULL DEFAULT 'a_preciser', juridiction TEXT NOT NULL DEFAULT '',
  chambre TEXT NOT NULL DEFAULT '', rg TEXT NOT NULL DEFAULT '', portalis TEXT NOT NULL DEFAULT '', nature TEXT NOT NULL DEFAULT '',
  saisine TEXT NOT NULL DEFAULT '', magistrat TEXT NOT NULL DEFAULT '', greffe TEXT NOT NULL DEFAULT '', etat TEXT NOT NULL DEFAULT 'en_cours',
  principale INTEGER NOT NULL DEFAULT 0, provenance TEXT NOT NULL DEFAULT 'manuel', version INTEGER NOT NULL DEFAULT 1,
  auteur TEXT NOT NULL DEFAULT '', cree_le TEXT NOT NULL, modifie_le TEXT NOT NULL, modifie_par TEXT NOT NULL DEFAULT '');
CREATE INDEX IF NOT EXISTS dossier_instances5698_d ON dossier_instances5698(dossier_id);
CREATE UNIQUE INDEX IF NOT EXISTS dossier_instances5698_principale ON dossier_instances5698(dossier_id) WHERE principale=1;
'''
_PRET = set()


# ------------------------------------------------------------------------------------------------ schéma et migration
def ensure_schema(desk):
    """Migration additive et rejouable : colonnes ajoutées aux parties du registre, table des instances, instance principale tirée du
    registre pour les dossiers qui ont une juridiction ou un RG. Jamais dans une transaction ouverte."""
    D.ensure_schema(desk)
    cle = (str(desk.c.get('state_dir')), id(desk.db))
    if cle in _PRET:
        return
    if desk.db.in_transaction:
        desk.db.commit()
    presentes = {r[1] for r in desk.db.execute('PRAGMA table_info(dossier_parties5697)')}
    for nom, declaration in COLONNES_PARTIES:
        if nom in presentes:
            continue
        try:
            desk.db.execute('ALTER TABLE dossier_parties5697 ADD COLUMN %s %s' % (nom, declaration))
        except sqlite3.OperationalError as ex:   # autre processus plus rapide : la colonne existe, rien à faire
            if 'duplicate column' not in str(ex).lower():
                raise
    desk.db.executescript(SCHEMA)
    desk.db.commit()
    migrer_instances(desk)
    _PRET.add(cle)


def migrer_instances(desk, dossier_id=None):
    """Dossier avec juridiction ou RG et aucune instance : son instance principale est créée (jamais deux fois). Rend le nombre créé."""
    if desk.db.in_transaction:
        desk.db.commit()
    quand = _maintenant()
    sql = ("INSERT INTO dossier_instances5698(id,dossier_id,degre,juridiction,rg,etat,principale,provenance,version,auteur,cree_le,modifie_le,modifie_par) "
           "SELECT lower(hex(randomblob(16))), d.id, 'a_preciser', d.juridiction, d.rg, 'en_cours', 1, 'registre', 1, 'migration', ?, ?, 'migration' "
           "FROM dossiers5697 d WHERE (d.juridiction<>'' OR d.rg<>'') AND NOT EXISTS (SELECT 1 FROM dossier_instances5698 i WHERE i.dossier_id=d.id)"
           + (' AND d.id=?' if dossier_id else ''))
    n = desk.db.execute(sql, (quand, quand) + ((dossier_id,) if dossier_id else ())).rowcount
    desk.db.commit()
    return max(0, n)


# ------------------------------------------------------------------------------------------------ outils
def _maintenant():
    return datetime.now(timezone.utc).isoformat(timespec='seconds')


def _acteur(acteur):
    return D._acteur(acteur)


def _dossier(desk, a, ident):
    """Identifiant courant d'un dossier du registre visible par l'acteur ; sinon ``introuvable`` (absent et réservé : même réponse)."""
    ensure_schema(desk)
    courant = D.resoudre(desk, ident)
    if not courant or not D._visible(desk, a, courant):
        raise Stop('introuvable')
    if not desk.db.execute('SELECT 1 FROM dossiers5697 WHERE id=?', (courant,)).fetchone():
        raise Stop('introuvable')
    migrer_instances(desk, courant)
    return courant


def _texte(err, v, d, champ, maxi, message='Valeur trop longue ou caractères invalides.'):
    try:
        v[champ] = D._texte(d.get(champ), maxi)
    except ValueError:
        err[champ] = message
        v[champ] = ''


def _version(valeur, champ='version'):
    try:
        return int(valeur)
    except (TypeError, ValueError):
        raise D.Refus('champs_invalides', {champ: 'Version attendue manquante : rechargez la page.'}) from None


def _vrai(valeur):
    return valeur is True or str(valeur).strip().lower() in ('1', 'on', 'true', 'oui')


# ------------------------------------------------------------------------------------------------ parties
def _valider_partie(desk, did, donnees, pid=''):
    d = donnees if isinstance(donnees, dict) else {}
    err, v = {}, {}
    _texte(err, v, d, 'nom', 160, 'Nom trop long ou caractères invalides.')
    if not v.get('nom') and 'nom' not in err:
        err['nom'] = 'Indiquez le nom.'
    v['role'] = str(d.get('role') or '')
    if v['role'] not in D.ROLES_PARTIES:
        err['role'] = 'Choisissez un rôle.'
    _texte(err, v, d, 'qualite', 160)
    v['type_personne'] = str(d.get('type_personne') or '')
    if v['type_personne'] not in TYPES_PERSONNE:
        err['type_personne'] = 'Personne physique ou morale.'
    v['courriel'] = str(d.get('courriel') or '').strip().lower()[:254]
    if v['courriel'] and not re.fullmatch(r'[^\s@,;<>"]+@[^\s@,;<>"]+\.[^\s@,;<>"]+', v['courriel']):
        err['courriel'] = 'Adresse de courriel invalide.'
    v['telephone'] = re.sub(r'\s+', ' ', str(d.get('telephone') or '')).strip()
    if v['telephone'] and not re.fullmatch(r'\+?[0-9 ().-]{4,30}', v['telephone']):
        err['telephone'] = 'Numéro de téléphone invalide.'
    _texte(err, v, d, 'adresse', 300)
    v['siren'] = re.sub(r'[\s.]+', '', str(d.get('siren') or ''))
    if v['siren']:
        from .facturation5653 import luhn_ok
        if not (re.fullmatch(r'[0-9]{9}', v['siren']) and luhn_ok(v['siren'])):
            err['siren'] = 'SIREN invalide : neuf chiffres, clé de contrôle comprise.'
        elif v['type_personne'] == 'physique':
            err['siren'] = 'Le SIREN concerne une personne morale.'
        elif not v['type_personne']:
            v['type_personne'] = 'morale'
    v['avocat_id'] = str(d.get('avocat_id') or '')[:80]
    if v['avocat_id']:
        row = desk.db.execute('SELECT role FROM dossier_parties5697 WHERE id=? AND dossier_id=?', (v['avocat_id'], did)).fetchone()
        if not row or row['role'] not in ROLES_AVOCATS or v['avocat_id'] == pid:
            err['avocat_id'] = 'Choisissez un avocat inscrit dans ce dossier.'
    _texte(err, v, d, 'note', 1000)
    if err:
        raise D.Refus('champs_invalides', err)
    return v


def _partie(desk, did, pid):
    row = desk.db.execute('SELECT * FROM dossier_parties5697 WHERE id=? AND dossier_id=?', (str(pid or ''), did)).fetchone()
    if not row:
        raise Stop('introuvable')
    return row


def _vue_partie(row, noms=None):
    p = {k: row[k] for k in row.keys() if k not in ('nom_norm',)}
    p['role_libelle'] = D.ROLES_PARTIES.get(row['role'], row['role'])
    p['groupe'] = GROUPE_DU_ROLE.get(row['role'], 'autres')
    p['type_libelle'] = TYPES_PERSONNE.get(row['type_personne'], '')
    p['avocat'] = (noms or {}).get(row['avocat_id'], '') if row['avocat_id'] else ''
    p['conflit_libelle'] = CONFLITS.get(row['conflit_statut'], '')
    return p


def parties(desk, acteur, ident):
    """Parties du dossier (après la garde) : ``{'dossier': id, 'groupes': [(clé, libellé, [parties])], 'parties': [...]}``."""
    a = _acteur(acteur)
    did = _dossier(desk, a, ident)
    rows = desk.db.execute('SELECT * FROM dossier_parties5697 WHERE dossier_id=? ORDER BY nom_norm, role', (did,)).fetchall()
    noms = {r['id']: r['nom'] for r in rows}
    toutes = [_vue_partie(r, noms) for r in rows]
    ordre = {r: i for _, _, roles in GROUPES for i, r in enumerate(roles)}
    groupes = [(cle, libelle, sorted((p for p in toutes if p['groupe'] == cle), key=lambda p: (ordre.get(p['role'], 99), D._plier(p['nom']))))
               for cle, libelle, _ in GROUPES]
    return {'dossier': did, 'groupes': groupes, 'parties': toutes}


def _recu_partie(desk, did, pid, operation, cle, quand, message):
    return {'id': pid, 'dossier': did, 'version': (desk.db.execute('SELECT version FROM dossier_parties5697 WHERE id=?', (pid,)).fetchone() or [0])[0],
            'message': message, 'recu': {'operation': operation, 'cle': cle, 'quand': quand}}


def ajouter_partie(desk, acteur, ident, donnees, cle):
    """Ajout d'une partie (formulaire sans IA). Partie adverse : vérification des conflits relancée après l'écriture, résultat rendu et
    gardé sur la partie. Rejeu (même clé, même corps) : reçu initial ; même personne avec le même rôle : ``partie_en_double`` (409)."""
    a = _acteur(acteur)
    D._peut_ecrire(a)
    did = _dossier(desk, a, ident)
    v = _valider_partie(desk, did, donnees)
    cabinet = D._cabinet(desk)
    with D._transaction(desk):
        empreinte, deja = D._commande(desk, cabinet, a, 'partie_ajouter:' + did, cle, v)
        if deja:
            return _avec_conflit(desk, deja)
        pid, quand = uuid.uuid4().hex, _maintenant()
        try:
            desk.db.execute('INSERT INTO dossier_parties5697(id,dossier_id,nom,nom_norm,role,courriel,contact_id,cree_le,qualite,type_personne,telephone,'
                            'adresse,siren,avocat_id,note,version,modifie_le,modifie_par) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,1,?,?)',
                            (pid, did, v['nom'], D._plier(v['nom']), v['role'], v['courriel'], '', quand, v['qualite'], v['type_personne'], v['telephone'],
                             v['adresse'], v['siren'], v['avocat_id'], v['note'], quand, a.compte))
        except sqlite3.IntegrityError:
            raise D.Refus('partie_en_double', {'nom': 'Cette personne figure déjà dans le dossier avec ce rôle.'}) from None
        D._journal(desk, did, a.compte, 'partie_ajoutee', {'partie': pid, 'role': v['role']})
        recu = _recu_partie(desk, did, pid, 'partie_ajouter', cle, quand, '%s ajouté(e) : %s' % (D.ROLES_PARTIES[v['role']], v['nom']))
        D._enregistrer_commande(desk, cabinet, a, 'partie_ajouter:' + did, cle, empreinte, recu)
    if v['role'] in ('adverse', 'client'):
        _inscrire_conflits(desk, did, v)
    if v['role'] == 'adverse':
        recu['conflits'] = _verifier_conflits(desk, a, did, pid, v)
        recu['message'] += ' · conflits : ' + recu['conflits']['resume']
    return recu


def _avec_conflit(desk, recu):
    row = desk.db.execute('SELECT conflit_statut,conflit_resume,conflit_le FROM dossier_parties5697 WHERE id=?', (recu.get('id', ''),)).fetchone()
    if row and row['conflit_statut']:
        recu['conflits'] = {'statut': row['conflit_statut'], 'resume': row['conflit_resume'], 'le': row['conflit_le']}
    return recu


def _verifier_conflits(desk, a, did, pid, v):
    """Même recherche qu'à la création (``dossiers5697.verifier_conflits`` : conflicts500, local, sans modèle). Le dossier lui-même est
    écarté ; un rapprochement vers un dossier réservé reste anonyme. Une panne de la recherche n'annule jamais l'ajout."""
    quand = _maintenant()
    try:
        out = D.verifier_conflits(desk, a, {'parties': [{'nom': v['nom'], 'role': 'adverse', 'courriel': v.get('courriel', '')}]})
        corresp = [m for m in out['correspondances'] if m.get('matter') != did]
        statut = out['statut'] if corresp or out['statut'] == 'aucun_resultat' else 'aucun_resultat'
        resume = out['resume'] if corresp or out['statut'] == 'aucun_resultat' else \
            'Aucun rapprochement trouvé dans les sources interrogées. Ce n’est pas une garantie d’absence de conflit.'
        res = {'id': out['id'], 'statut': statut, 'resume': resume, 'correspondances': corresp, 'avis': out['avis'], 'limites': out['limites'][:3]}
    except Stop as ex:
        res = {'id': '', 'statut': 'non_verifie', 'resume': 'Vérification des conflits impossible pour le moment (%s) : relancez-la.' % str(ex).split(':')[0],
               'correspondances': [], 'avis': '', 'limites': []}
    except Exception:   # noqa: BLE001 — la partie est ajoutée ; l'état « non vérifié » est affiché, jamais « aucun conflit »
        res = {'id': '', 'statut': 'non_verifie', 'resume': 'Vérification des conflits impossible pour le moment : relancez-la.',
               'correspondances': [], 'avis': '', 'limites': []}
    if desk.db.in_transaction:
        desk.db.commit()
    desk.db.execute('UPDATE dossier_parties5697 SET conflit_id=?, conflit_statut=?, conflit_resume=?, conflit_le=? WHERE id=?',
                    (res['id'], res['statut'], res['resume'][:500], quand, pid))
    D._journal(desk, did, a.compte, 'partie_conflits', {'partie': pid, 'statut': res['statut']})
    desk.db.commit()
    res['le'] = quand
    return res


def _inscrire_conflits(desk, did, v):
    """La partie entre dans le référentiel des conflits (comme à la création, ``lier_conflits``), sans changer l'état du dossier."""
    try:
        from . import conflicts500
        from .common import digest
        conflicts500.m5.ensure_schema(desk)
        role = D.ROLES_CONFLITS.get(v['role'], 'tiers')
        desk.db.execute('INSERT OR IGNORE INTO conflicts500_parties VALUES(?,?,?,?,?,?,?,?)',
                        (digest('c500party|%s|%s|%s' % (did, conflicts500.norm(v['nom']), role)), did, v['nom'], conflicts500.norm(v['nom']), role,
                         v.get('courriel', ''), 'fiche5698', conflicts500.m5.now()))
        desk.db.commit()
    except Exception:   # noqa: BLE001 — nom non normalisable ou référentiel absent : la partie reste au dossier
        if desk.db.in_transaction:
            desk.db.rollback()


def modifier_partie(desk, acteur, ident, pid, version, donnees, cle):
    a = _acteur(acteur)
    D._peut_ecrire(a)
    did = _dossier(desk, a, ident)
    _partie(desk, did, pid)
    version = _version(version)
    v = _valider_partie(desk, did, donnees, pid)
    cabinet = D._cabinet(desk)
    with D._transaction(desk):
        empreinte, deja = D._commande(desk, cabinet, a, 'partie_modifier:' + str(pid), cle, {'version': version, **v})
        if deja:
            return _avec_conflit(desk, deja)
        row = _partie(desk, did, pid)
        if row['version'] != version:
            raise Stop('version_perimee')
        quand = _maintenant()
        try:
            desk.db.execute('UPDATE dossier_parties5697 SET nom=?, nom_norm=?, role=?, courriel=?, qualite=?, type_personne=?, telephone=?, adresse=?, '
                            'siren=?, avocat_id=?, note=?, version=version+1, modifie_le=?, modifie_par=? WHERE id=? AND version=?',
                            (v['nom'], D._plier(v['nom']), v['role'], v['courriel'], v['qualite'], v['type_personne'], v['telephone'], v['adresse'],
                             v['siren'], v['avocat_id'], v['note'], quand, a.compte, pid, version))
        except sqlite3.IntegrityError:
            raise D.Refus('partie_en_double', {'nom': 'Cette personne figure déjà dans le dossier avec ce rôle.'}) from None
        if row['role'] in ROLES_AVOCATS and v['role'] not in ROLES_AVOCATS:   # n'est plus avocat : plus personne n'est « représenté par » lui
            desk.db.execute("UPDATE dossier_parties5697 SET avocat_id='', version=version+1, modifie_le=? WHERE dossier_id=? AND avocat_id=?", (quand, did, pid))
        champs = sorted(k for k in CHAMPS_PARTIE if str(row[k] if k in row.keys() else '') != str(v[k]))
        D._journal(desk, did, a.compte, 'partie_modifiee', {'partie': pid, 'champs': champs, 'version': version + 1})
        recu = _recu_partie(desk, did, pid, 'partie_modifier', cle, quand, 'Partie enregistrée : %s' % v['nom'])
        D._enregistrer_commande(desk, cabinet, a, 'partie_modifier:' + str(pid), cle, empreinte, recu)
    adverse_nouvelle = v['role'] == 'adverse' and (row['role'] != 'adverse' or D._plier(row['nom']) != D._plier(v['nom']))
    if v['role'] in ('adverse', 'client') and (adverse_nouvelle or row['role'] != v['role']):
        _inscrire_conflits(desk, did, v)
    if adverse_nouvelle:
        recu['conflits'] = _verifier_conflits(desk, a, did, pid, v)
        recu['message'] += ' · conflits : ' + recu['conflits']['resume']
    return recu


def retirer_partie(desk, acteur, ident, pid, version, cle):
    """Retrait (version attendue) ; le nom et le rôle restent au journal du dossier. Les parties qu'il représentait n'ont plus d'avocat désigné."""
    a = _acteur(acteur)
    D._peut_ecrire(a)
    did = _dossier(desk, a, ident)
    _partie(desk, did, pid)
    version = _version(version)
    cabinet = D._cabinet(desk)
    with D._transaction(desk):
        empreinte, deja = D._commande(desk, cabinet, a, 'partie_retirer:' + str(pid), cle, {'version': version})
        if deja:
            return deja
        row = _partie(desk, did, pid)
        if row['version'] != version:
            raise Stop('version_perimee')
        quand = _maintenant()
        desk.db.execute('DELETE FROM dossier_parties5697 WHERE id=? AND version=?', (pid, version))
        desk.db.execute("UPDATE dossier_parties5697 SET avocat_id='', version=version+1, modifie_le=? WHERE dossier_id=? AND avocat_id=?", (quand, did, pid))
        D._journal(desk, did, a.compte, 'partie_retiree', {'partie': pid, 'nom': row['nom'], 'role': row['role']})
        recu = {'id': pid, 'dossier': did, 'version': version, 'etat': 'retiree', 'message': 'Partie retirée du dossier : %s' % row['nom'],
                'recu': {'operation': 'partie_retirer', 'cle': cle, 'quand': quand}}
        D._enregistrer_commande(desk, cabinet, a, 'partie_retirer:' + str(pid), cle, empreinte, recu)
    return recu


def parties_proposees(desk, did, inscrites):
    """Parties lues dans les pièces (``modele5651``, sans IA) absentes du registre du dossier : proposées, jamais ajoutées."""
    from .resume5659 import representation
    connus = {D._plier(p['nom']) for p in inscrites}
    out = []
    for p in representation(desk, did):
        if not p['nom'] or D._plier(p['nom']) in connus:
            continue
        out.append({'nom': p['nom'][:160], 'qualite': (p['qualite'] or '')[:160], 'conseil': p['conseil'] or '', 'role': 'client' if p['client'] else 'adverse',
                    'sources': p.get('sources') or []})
    return out[:40]


# ------------------------------------------------------------------------------------------------ instances (juridiction)
def _valider_instance(donnees):
    d = donnees if isinstance(donnees, dict) else {}
    err, v = {}, {}
    v['degre'] = str(d.get('degre') or 'a_preciser')
    if v['degre'] not in DEGRES:
        err['degre'] = 'Degré inconnu.'
    for champ, maxi in (('juridiction', 160), ('chambre', 160), ('magistrat', 160), ('greffe', 300)):
        _texte(err, v, d, champ, maxi)
    _texte(err, v, d, 'rg', 40)
    if v.get('rg') and not re.fullmatch(r'[0-9A-Za-z /._-]{1,40}', v['rg']):
        err['rg'] = 'Numéro RG invalide.'
    v['portalis'] = re.sub(r'\s+', '', str(d.get('portalis') or '')).upper()
    if v['portalis'] and not re.fullmatch(r'[A-Z0-9][A-Z0-9-]{3,39}', v['portalis']):
        err['portalis'] = 'Numéro Portalis invalide : lettres, chiffres et tirets.'
    v['nature'] = str(d.get('nature') or '')
    if v['nature'] not in NATURES_INSTANCE:
        err['nature'] = 'Nature inconnue.'
    v['saisine'] = str(d.get('saisine') or '').strip()[:10]
    if v['saisine']:
        try:
            date.fromisoformat(v['saisine'])
        except ValueError:
            err['saisine'] = 'Date de saisine invalide.'
    v['etat'] = str(d.get('etat') or 'en_cours')
    if v['etat'] not in ETATS_INSTANCE:
        err['etat'] = 'État inconnu.'
    v['principale'] = _vrai(d.get('principale'))
    if not v.get('juridiction') and not v.get('rg') and 'juridiction' not in err and 'rg' not in err:
        err['juridiction'] = 'Indiquez au moins la juridiction ou le numéro RG.'
    if err:
        raise D.Refus('champs_invalides', err)
    return v


def _instance(desk, did, iid):
    row = desk.db.execute('SELECT * FROM dossier_instances5698 WHERE id=? AND dossier_id=?', (str(iid or ''), did)).fetchone()
    if not row:
        raise Stop('introuvable')
    return row


def _vue_instance(row, dossier):
    v = dict(row)
    v['principale'] = bool(row['principale'])
    if v['principale']:   # une seule vérité : juridiction et RG de l'instance principale sont ceux du registre
        v['juridiction'], v['rg'], v['version_dossier'] = dossier['juridiction'], dossier['rg'], dossier['version']
    v['degre_libelle'] = DEGRES.get(row['degre'], row['degre'])
    v['nature_libelle'] = NATURES_INSTANCE.get(row['nature'], row['nature']) if row['nature'] else ''
    v['etat_libelle'] = ETATS_INSTANCE.get(row['etat'], row['etat'])
    return v


def instances(desk, acteur, ident):
    a = _acteur(acteur)
    did = _dossier(desk, a, ident)
    dossier = desk.db.execute('SELECT juridiction,rg,version FROM dossiers5697 WHERE id=?', (did,)).fetchone()
    rows = desk.db.execute('SELECT * FROM dossier_instances5698 WHERE dossier_id=? ORDER BY principale DESC, saisine DESC, cree_le', (did,)).fetchall()
    return {'dossier': did, 'version_dossier': dossier['version'], 'instances': [_vue_instance(r, dossier) for r in rows]}


def _destituer(desk, did, quand):
    """L'ancienne instance principale garde la juridiction et le RG du registre au moment où elle cesse de l'être (aucune perte)."""
    d = desk.db.execute('SELECT juridiction,rg FROM dossiers5697 WHERE id=?', (did,)).fetchone()
    desk.db.execute('UPDATE dossier_instances5698 SET principale=0, juridiction=?, rg=?, version=version+1, modifie_le=? WHERE dossier_id=? AND principale=1',
                    (d['juridiction'], d['rg'], quand, did))


def _ecrire_registre(desk, did, juridiction, rg, compte, quand):
    """Juridiction et RG de l'instance principale écrits dans le registre (même transaction) : version du dossier + 1, journal."""
    row = desk.db.execute('SELECT juridiction,rg,version FROM dossiers5697 WHERE id=?', (did,)).fetchone()
    maj = {k: val for k, val in (('juridiction', juridiction), ('rg', rg)) if row[k] != val}
    if not maj:
        return False
    desk.db.execute('UPDATE dossiers5697 SET %s, version=version+1, modifie_le=?, modifie_par=? WHERE id=?' % ', '.join('%s=?' % k for k in maj),
                    list(maj.values()) + [quand, compte, did])
    D._journal(desk, did, compte, 'modification', {'champs': sorted(maj), 'version': row['version'] + 1, 'origine': 'instance_principale'})
    return True


def _recu_instance(desk, did, iid, operation, cle, quand, message):
    row = desk.db.execute('SELECT version,principale FROM dossier_instances5698 WHERE id=?', (iid,)).fetchone()
    return {'id': iid, 'dossier': did, 'version': row['version'] if row else 0, 'principale': bool(row['principale']) if row else False,
            'version_dossier': desk.db.execute('SELECT version FROM dossiers5697 WHERE id=?', (did,)).fetchone()[0],
            'message': message, 'recu': {'operation': operation, 'cle': cle, 'quand': quand}}


def ajouter_instance(desk, acteur, ident, donnees, cle):
    """Nouvelle instance ; la première du dossier, ou celle cochée « principale », devient l'instance principale (registre mis à jour)."""
    a = _acteur(acteur)
    D._peut_ecrire(a)
    did = _dossier(desk, a, ident)
    v = _valider_instance(donnees)
    cabinet = D._cabinet(desk)
    ecrit = False
    with D._transaction(desk):
        empreinte, deja = D._commande(desk, cabinet, a, 'instance_ajouter:' + did, cle, v)
        if deja:
            return deja
        quand, iid = _maintenant(), uuid.uuid4().hex
        principale = v['principale'] or not desk.db.execute('SELECT 1 FROM dossier_instances5698 WHERE dossier_id=?', (did,)).fetchone()
        if principale:
            _destituer(desk, did, quand)
        desk.db.execute('INSERT INTO dossier_instances5698(id,dossier_id,degre,juridiction,chambre,rg,portalis,nature,saisine,magistrat,greffe,etat,principale,'
                        'provenance,version,auteur,cree_le,modifie_le,modifie_par) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,1,?,?,?,?)',
                        (iid, did, v['degre'], v['juridiction'], v['chambre'], v['rg'], v['portalis'], v['nature'], v['saisine'], v['magistrat'], v['greffe'],
                         v['etat'], 1 if principale else 0, 'manuel', a.compte, quand, quand, a.compte))
        if principale:
            ecrit = _ecrire_registre(desk, did, v['juridiction'], v['rg'], a.compte, quand)
        D._journal(desk, did, a.compte, 'instance_ajoutee', {'instance': iid, 'principale': bool(principale)})
        recu = _recu_instance(desk, did, iid, 'instance_ajouter', cle, quand,
                              'Instance ajoutée' + (' (principale : juridiction et RG du dossier mis à jour)' if principale else ''))
        D._enregistrer_commande(desk, cabinet, a, 'instance_ajouter:' + did, cle, empreinte, recu)
    if ecrit:
        D.projeter(desk, did)
    return recu


def modifier_instance(desk, acteur, ident, iid, version, donnees, cle, version_dossier=None):
    """Modification avec version attendue. Instance principale : la version du dossier affichée est aussi vérifiée (une juridiction ou un
    RG changé ailleurs n'est jamais écrasé) ; une instance cesse d'être principale seulement quand une autre le devient."""
    a = _acteur(acteur)
    D._peut_ecrire(a)
    did = _dossier(desk, a, ident)
    _instance(desk, did, iid)
    version = _version(version)
    vd = None if version_dossier in (None, '') else _version(version_dossier, 'version_dossier')
    v = _valider_instance(donnees)
    cabinet = D._cabinet(desk)
    ecrit = False
    with D._transaction(desk):
        empreinte, deja = D._commande(desk, cabinet, a, 'instance_modifier:' + str(iid), cle, {'version': version, 'version_dossier': vd, **v})
        if deja:
            return deja
        row = _instance(desk, did, iid)
        dossier = desk.db.execute('SELECT version FROM dossiers5697 WHERE id=?', (did,)).fetchone()
        if row['version'] != version or (row['principale'] and vd is not None and vd != dossier['version']):
            raise Stop('version_perimee')
        if row['principale'] and not v['principale']:
            raise D.Refus('champs_invalides', {'principale': 'Désignez d’abord une autre instance comme principale.'})
        quand = _maintenant()
        devient = v['principale'] and not row['principale']
        if devient:
            _destituer(desk, did, quand)
        desk.db.execute('UPDATE dossier_instances5698 SET degre=?, juridiction=?, chambre=?, rg=?, portalis=?, nature=?, saisine=?, magistrat=?, greffe=?, etat=?, '
                        'principale=?, version=version+1, modifie_le=?, modifie_par=? WHERE id=?',
                        (v['degre'], v['juridiction'], v['chambre'], v['rg'], v['portalis'], v['nature'], v['saisine'], v['magistrat'], v['greffe'], v['etat'],
                         1 if v['principale'] else 0, quand, a.compte, iid))
        if v['principale']:
            ecrit = _ecrire_registre(desk, did, v['juridiction'], v['rg'], a.compte, quand)
        D._journal(desk, did, a.compte, 'instance_modifiee', {'instance': iid, 'version': row['version'] + 1, 'principale': bool(v['principale'])})
        recu = _recu_instance(desk, did, iid, 'instance_modifier', cle, quand, 'Instance enregistrée')
        D._enregistrer_commande(desk, cabinet, a, 'instance_modifier:' + str(iid), cle, empreinte, recu)
    if ecrit:
        D.projeter(desk, did)
    return recu


def retirer_instance(desk, acteur, ident, iid, version, cle):
    a = _acteur(acteur)
    D._peut_ecrire(a)
    did = _dossier(desk, a, ident)
    _instance(desk, did, iid)
    version = _version(version)
    cabinet = D._cabinet(desk)
    with D._transaction(desk):
        empreinte, deja = D._commande(desk, cabinet, a, 'instance_retirer:' + str(iid), cle, {'version': version})
        if deja:
            return deja
        row = _instance(desk, did, iid)
        if row['version'] != version:
            raise Stop('version_perimee')
        if row['principale']:
            raise D.Refus('instance_principale', {'principale': 'L’instance principale porte la juridiction et le RG du dossier : désignez-en d’abord une autre.'})
        quand = _maintenant()
        desk.db.execute('DELETE FROM dossier_instances5698 WHERE id=? AND version=?', (iid, version))
        D._journal(desk, did, a.compte, 'instance_retiree', {'instance': iid, 'juridiction': row['juridiction'], 'rg': row['rg'], 'degre': row['degre']})
        recu = {'id': iid, 'dossier': did, 'version': version, 'etat': 'retiree', 'message': 'Instance retirée du dossier.',
                'recu': {'operation': 'instance_retirer', 'cle': cle, 'quand': quand}}
        D._enregistrer_commande(desk, cabinet, a, 'instance_retirer:' + str(iid), cle, empreinte, recu)
    return recu


# ------------------------------------------------------------------------------------------------ fichiers : arborescence réelle
def chemin_relatif(rel):
    """Sous-répertoire demandé, relatif au répertoire du dossier : jamais ``..``, ``.``, segment caché, antislash ni encodage ambigu."""
    rel = str(rel or '').strip().strip('/')
    if not rel:
        return ''
    if len(rel) > 600:
        raise Stop('chemin_refuse')
    morceaux = rel.split('/')
    if any(p in ('', '.', '..') or p.startswith('.') for p in morceaux):
        raise Stop('chemin_refuse')
    if len(morceaux) > MAX_PROFONDEUR:
        raise Stop('profondeur_depassee')
    clean_path('/' + rel)   # antislash, caractère de contrôle, « %41 » : refusés
    return rel


def jour(valeur):
    """Date d'une entrée (RFC 1123 du WebDAV ou ISO du stockage local) → ``AAAA-MM-JJ`` ; illisible : ``''``."""
    valeur = str(valeur or '').strip()
    if not valeur:
        return ''
    if re.match(r'\d{4}-\d{2}-\d{2}', valeur):
        return valeur[:10]
    try:
        return parsedate_to_datetime(valeur).date().isoformat()
    except (TypeError, ValueError, IndexError):
        return ''


def _lire(client, chemin):
    """Entrées directes de ``chemin`` (contrôlées : sous ce chemin, jamais lui-même) ; ``(entrées, tronquée)``."""
    entrees = [x for x in client.list_folder(chemin) or [] if x.get('path') and under(x['path'], chemin) and clean_path(x['path']) != chemin]
    return entrees[:MAX_LUES], len(entrees) > MAX_LUES


def _etat_erreur(ex):
    code = str(ex).split(':')[0]
    if code in ABSENT or 'introuvable' in code or '404' in code:
        return 'absent', 'Répertoire introuvable dans le stockage : il a peut-être été déplacé ou renommé.'
    if code in ('fichier_hors_racines', 'repertoire_exclu', 'chemin_refuse', 'reponse_dav_hors_dossier', 'chemin_nextcloud_absolu_requis'):
        return 'refuse', 'Emplacement refusé : hors du répertoire du dossier ou des racines autorisées.'
    return 'hors_ligne', 'Stockage injoignable pour le moment : la liste des fichiers n’a pas pu être lue (ce n’est pas un répertoire vide).'


def arborescence(desk, base, rel='', page=1, client=None):
    """Contenu d'un répertoire du dossier, en lecture seule et borné. ``etat`` : ``ok``, ``sans_repertoire``, ``refuse``, ``absent`` ou
    ``hors_ligne`` (avec ``motif``) ; jamais une liste vide pour un stockage qui ne répond pas."""
    from .workspace import allowed
    out = {'etat': 'ok', 'motif': '', 'base': '', 'rel': '', 'chemin': '', 'fil': [], 'dossiers': [], 'fichiers': [], 'total': 0, 'page': 1, 'pages': 1,
           'tronquee': False, 'non_comptes': 0}
    if not base:
        out.update(etat='sans_repertoire', motif='Aucun répertoire n’est rattaché à ce dossier.')
        return out
    try:
        base = allowed(desk.c, str(base))
        rel = chemin_relatif(rel)
        chemin = clean_path(base + ('/' + rel if rel else ''))
        if not under(chemin, base):
            raise Stop('chemin_refuse')
    except Stop as ex:
        code = str(ex).split(':')[0]
        out.update(etat='refuse', motif='Profondeur maximale atteinte : ouvrez ce répertoire dans Nextcloud.' if code == 'profondeur_depassee' else _etat_erreur(ex)[1])
        return out
    out.update(base=base, rel=rel, chemin=chemin)
    acc = []
    for morceau in (rel.split('/') if rel else []):
        acc.append(morceau)
        out['fil'].append({'nom': morceau, 'rel': '/'.join(acc)})
    try:
        client = client or D._client_stockage(desk)
        entrees, out['tronquee'] = _lire(client, chemin)
    except Stop as ex:
        out['etat'], out['motif'] = _etat_erreur(ex)
        return out
    except (OSError, ValueError):
        out['etat'], out['motif'] = 'hors_ligne', _etat_erreur(Stop('stockage_injoignable'))[1]
        return out
    nom = lambda x: clean_path(x['path']).rsplit('/', 1)[-1]
    reps = sorted((x for x in entrees if x.get('directory')), key=lambda x: nom(x).casefold())
    fics = sorted((x for x in entrees if not x.get('directory')), key=lambda x: nom(x).casefold())
    tout = reps + fics
    out['total'] = len(tout)
    out['pages'] = max(1, (len(tout) + PAR_PAGE - 1) // PAR_PAGE)
    page = min(max(1, int(page or 1)), out['pages'])
    out['page'] = page
    comptes = 0
    profondeur = len(rel.split('/')) if rel else 0
    for x in tout[(page - 1) * PAR_PAGE: page * PAR_PAGE]:
        n = nom(x)
        sous_rel = (rel + '/' + n) if rel else n
        if x.get('directory'):
            item = {'nom': n, 'rel': sous_rel, 'chemin': clean_path(x['path']), 'modifie': jour(x.get('modified')), 'fichiers': None, 'sous_dossiers': None,
                    'taille': None, 'ouvrable': profondeur + 1 <= MAX_PROFONDEUR}
            if comptes < MAX_COMPTES:
                comptes += 1
                try:
                    contenu, coupe = _lire(client, item['chemin'])
                    fichiers = [c for c in contenu if not c.get('directory')]
                    item.update(fichiers=len(fichiers), sous_dossiers=len(contenu) - len(fichiers), taille=sum(int(c.get('size') or 0) for c in fichiers),
                                incomplet=coupe)
                    dates = [jour(c.get('modified')) for c in contenu] + [item['modifie']]
                    item['modifie'] = max((d for d in dates if d), default='')
                except (Stop, OSError, ValueError):
                    item['illisible'] = True
            else:
                out['non_comptes'] += 1
            out['dossiers'].append(item)
        else:
            out['fichiers'].append({'nom': n, 'chemin': clean_path(x['path']), 'modifie': jour(x.get('modified')), 'taille': int(x.get('size') or 0)})
    return out


def taille_lisible(octets):
    if octets is None:
        return ''
    octets = int(octets)
    for unite, seuil in (('Go', 1024 ** 3), ('Mo', 1024 ** 2), ('ko', 1024)):
        if octets >= seuil:
            return ('%.1f %s' % (octets / seuil, unite)).replace('.', ',')
    return '%d o' % octets
