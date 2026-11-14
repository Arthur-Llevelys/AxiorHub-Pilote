"""5.6.97 (lot gestion, § 18 à § 21) : socle commun du registre procédural, des honoraires, des pièces échangées et de la clôture.

Ce module ne porte aucune règle métier : schéma des tables du lot, garde d'accès (``acces5697``, appliquée avant toute lecture, tout total et
tout tri), droits fins (``membres5650``), journal unique du lot, idempotence et version attendue. Les quatre domaines s'y appuient :
- ``procedure5697`` (§ 18) : registre procédural sur ``echeances450`` ; paquet de dépôt gelé (octets et pièces), accusé rapproché ;
- ``honoraires5697`` (§ 19) : quatre indicateurs, frais et jalons, rapprochement (source, entité, identifiant externe) ;
- ``remises5697`` (§ 20) : pièces demandées (``parcours5657``) et remises par partage Nextcloud en lecture seule, révocation vérifiée ;
- ``cloture5697`` (§ 21) : contrôles recalculés sur le serveur, clôture et réouverture tracées, paquet de restauration vérifié.

Aucune commande de ce lot n'appelle un modèle : elles restent disponibles quand l'IA est en pause (règle commune de la 5.6.97).
Aucun effet externe n'est déclenché sans une action explicite ; tout effet suit l'ordre réserver (transaction) → agir → constater.
"""
from datetime import datetime, timezone
import json
import re
import uuid

from .common import Stop, load_matters
from .contentieux5689 import empreinte, transaction   # mêmes primitives que le contentieux : pas de seconde implémentation

__all__ = ('empreinte', 'transaction')

SCHEMA = '''
CREATE TABLE IF NOT EXISTS gestion_journal5697(id INTEGER PRIMARY KEY AUTOINCREMENT, matter TEXT NOT NULL, objet TEXT NOT NULL, objet_id TEXT NOT NULL, action TEXT NOT NULL,
  acteur TEXT NOT NULL, motif TEXT NOT NULL DEFAULT '', avant TEXT NOT NULL DEFAULT '{}', apres TEXT NOT NULL DEFAULT '{}', at TEXT NOT NULL);
CREATE INDEX IF NOT EXISTS gestion_journal5697_m ON gestion_journal5697(matter, id);
CREATE TABLE IF NOT EXISTS echeance_suivi5697(deadline_id TEXT PRIMARY KEY, matter TEXT NOT NULL, responsable TEXT NOT NULL DEFAULT '', rappel_destinataire TEXT NOT NULL DEFAULT '',
  rappel_le TEXT NOT NULL DEFAULT '', critique INTEGER NOT NULL DEFAULT 1, acquitte_par TEXT NOT NULL DEFAULT '', acquitte_le TEXT NOT NULL DEFAULT '', signale_le TEXT NOT NULL DEFAULT '',
  preuve_accomplissement TEXT NOT NULL DEFAULT '', revision INTEGER NOT NULL DEFAULT 1, updated TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS depots5697(id TEXT PRIMARY KEY, matter TEXT NOT NULL, cle TEXT NOT NULL UNIQUE, parametres TEXT NOT NULL DEFAULT '', paquet_id TEXT NOT NULL DEFAULT '',
  canal TEXT NOT NULL, reference_affaire TEXT NOT NULL DEFAULT '', manifeste TEXT NOT NULL, manifeste_sha TEXT NOT NULL, etat TEXT NOT NULL, envoi_ref TEXT NOT NULL DEFAULT '',
  date_depot TEXT NOT NULL DEFAULT '', declare_par TEXT NOT NULL DEFAULT '', declare_le TEXT NOT NULL DEFAULT '', cree_par TEXT NOT NULL, revision INTEGER NOT NULL DEFAULT 1,
  created TEXT NOT NULL, updated TEXT NOT NULL);
CREATE INDEX IF NOT EXISTS depots5697_m ON depots5697(matter, created);
CREATE TABLE IF NOT EXISTS depot_fichiers5697(depot_id TEXT NOT NULL, rang INTEGER NOT NULL, role TEXT NOT NULL, titre TEXT NOT NULL DEFAULT '', numero TEXT NOT NULL DEFAULT '',
  piece_id TEXT NOT NULL DEFAULT '', path TEXT NOT NULL, version_id TEXT NOT NULL DEFAULT '', sha256 TEXT NOT NULL, taille INTEGER NOT NULL, contenu BLOB NOT NULL,
  PRIMARY KEY(depot_id, rang));
CREATE TABLE IF NOT EXISTS depot_accuses5697(id TEXT PRIMARY KEY, depot_id TEXT NOT NULL, matter TEXT NOT NULL, nature TEXT NOT NULL, sha256 TEXT NOT NULL, nom_fichier TEXT NOT NULL DEFAULT '',
  contenu BLOB NOT NULL, reference TEXT NOT NULL DEFAULT '', envoi_ref TEXT NOT NULL DEFAULT '', date_accuse TEXT NOT NULL DEFAULT '', etat TEXT NOT NULL, motif TEXT NOT NULL DEFAULT '',
  importe_par TEXT NOT NULL, importe_le TEXT NOT NULL);
CREATE INDEX IF NOT EXISTS depot_accuses5697_d ON depot_accuses5697(depot_id, importe_le);
CREATE INDEX IF NOT EXISTS depot_accuses5697_h ON depot_accuses5697(sha256);
CREATE TABLE IF NOT EXISTS frais5697(id TEXT PRIMARY KEY, parametres TEXT NOT NULL DEFAULT '', matter TEXT NOT NULL, cle TEXT NOT NULL UNIQUE, jour TEXT NOT NULL, libelle TEXT NOT NULL, nature TEXT NOT NULL,
  montant_ht_cents INTEGER NOT NULL, etat TEXT NOT NULL, saisi_par TEXT NOT NULL, valide_par TEXT NOT NULL DEFAULT '', valide_le TEXT NOT NULL DEFAULT '', created TEXT NOT NULL);
CREATE INDEX IF NOT EXISTS frais5697_m ON frais5697(matter, etat);
CREATE TABLE IF NOT EXISTS jalons5697(id TEXT PRIMARY KEY, parametres TEXT NOT NULL DEFAULT '', matter TEXT NOT NULL, cle TEXT NOT NULL UNIQUE, libelle TEXT NOT NULL, montant_ht_cents INTEGER NOT NULL,
  date_prevue TEXT NOT NULL DEFAULT '', etat TEXT NOT NULL, revision INTEGER NOT NULL DEFAULT 1, created TEXT NOT NULL, updated TEXT NOT NULL);
CREATE INDEX IF NOT EXISTS jalons5697_m ON jalons5697(matter, etat);
CREATE TABLE IF NOT EXISTS fin_externes5697(source TEXT NOT NULL, entite TEXT NOT NULL, objet TEXT NOT NULL, id_externe TEXT NOT NULL, matter TEXT NOT NULL DEFAULT '',
  document TEXT NOT NULL DEFAULT '', numero TEXT NOT NULL DEFAULT '', montant_cents INTEGER NOT NULL DEFAULT 0, solde_cents INTEGER NOT NULL DEFAULT 0, devise TEXT NOT NULL DEFAULT 'EUR',
  jour TEXT NOT NULL DEFAULT '', echeance TEXT NOT NULL DEFAULT '', etat TEXT NOT NULL DEFAULT '', liens TEXT NOT NULL DEFAULT '[]', empreinte TEXT NOT NULL, vu_le TEXT NOT NULL,
  PRIMARY KEY(source, entite, objet, id_externe));
CREATE INDEX IF NOT EXISTS fin_externes5697_m ON fin_externes5697(matter, objet);
CREATE TABLE IF NOT EXISTS fin_synchros5697(id TEXT PRIMARY KEY, source TEXT NOT NULL, entite TEXT NOT NULL, debut TEXT NOT NULL, fin TEXT NOT NULL DEFAULT '', etat TEXT NOT NULL,
  erreur TEXT NOT NULL DEFAULT '', compte INTEGER NOT NULL DEFAULT 0, anomalies TEXT NOT NULL DEFAULT '[]');
CREATE INDEX IF NOT EXISTS fin_synchros5697_s ON fin_synchros5697(source, entite, debut);
CREATE TABLE IF NOT EXISTS fin_demandes5697(cle TEXT PRIMARY KEY, nature TEXT NOT NULL, document TEXT NOT NULL, matter TEXT NOT NULL, source TEXT NOT NULL, entite TEXT NOT NULL,
  etat TEXT NOT NULL, id_externe TEXT NOT NULL DEFAULT '', tentatives INTEGER NOT NULL DEFAULT 0, reponse TEXT NOT NULL DEFAULT '', acteur TEXT NOT NULL, created TEXT NOT NULL, updated TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS pieces_suivi5697(request TEXT PRIMARY KEY, matter TEXT NOT NULL, destinataire TEXT NOT NULL DEFAULT '', verifie_par TEXT NOT NULL DEFAULT '',
  revoir_le TEXT NOT NULL DEFAULT '', updated TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS pieces_receptions5697(id TEXT PRIMARY KEY, parametres TEXT NOT NULL DEFAULT '', request TEXT NOT NULL, item TEXT NOT NULL, matter TEXT NOT NULL, cle TEXT NOT NULL UNIQUE, origine TEXT NOT NULL,
  ref TEXT NOT NULL DEFAULT '', path TEXT NOT NULL DEFAULT '', sha256 TEXT NOT NULL DEFAULT '', taille INTEGER NOT NULL DEFAULT 0, type TEXT NOT NULL DEFAULT '', etat TEXT NOT NULL,
  recu_par TEXT NOT NULL, recu_le TEXT NOT NULL, decide_par TEXT NOT NULL DEFAULT '', decide_le TEXT NOT NULL DEFAULT '');
CREATE INDEX IF NOT EXISTS pieces_receptions5697_r ON pieces_receptions5697(request, item);
CREATE TABLE IF NOT EXISTS remises5697(id TEXT PRIMARY KEY, matter TEXT NOT NULL, cle TEXT NOT NULL UNIQUE, parametres TEXT NOT NULL DEFAULT '', destinataire TEXT NOT NULL,
  destinataire_type TEXT NOT NULL, canal TEXT NOT NULL, fichiers TEXT NOT NULL DEFAULT '[]', copie_dossier TEXT NOT NULL DEFAULT '', share_id TEXT NOT NULL DEFAULT '',
  expiration TEXT NOT NULL DEFAULT '', etat TEXT NOT NULL, etat_distant TEXT NOT NULL DEFAULT '', preuve TEXT NOT NULL DEFAULT '{}', erreur TEXT NOT NULL DEFAULT '', url_jeton TEXT NOT NULL DEFAULT '',
  verifie_le TEXT NOT NULL DEFAULT '', cree_par TEXT NOT NULL, revision INTEGER NOT NULL DEFAULT 1, created TEXT NOT NULL, updated TEXT NOT NULL);
CREATE INDEX IF NOT EXISTS remises5697_m ON remises5697(matter, created);
CREATE TABLE IF NOT EXISTS dossiers_etat5697(matter TEXT PRIMARY KEY, etat TEXT NOT NULL, version INTEGER NOT NULL DEFAULT 1, pause_posee INTEGER NOT NULL DEFAULT 0,
  suivi_financier TEXT NOT NULL DEFAULT '{}', updated TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS clotures5697(id TEXT PRIMARY KEY, parametres TEXT NOT NULL DEFAULT '', matter TEXT NOT NULL, cle TEXT NOT NULL UNIQUE, decision TEXT NOT NULL, version_dossier INTEGER NOT NULL, controles TEXT NOT NULL,
  synthese TEXT NOT NULL DEFAULT '', motif TEXT NOT NULL DEFAULT '', auteur TEXT NOT NULL, at TEXT NOT NULL);
CREATE INDEX IF NOT EXISTS clotures5697_m ON clotures5697(matter, at);
CREATE TABLE IF NOT EXISTS entrees_archivees5697(id TEXT PRIMARY KEY, matter TEXT NOT NULL, nature TEXT NOT NULL, ref TEXT NOT NULL, responsable TEXT NOT NULL DEFAULT '', at TEXT NOT NULL,
  UNIQUE(matter, nature, ref));
CREATE TABLE IF NOT EXISTS cloture_suites56100(cloture_id TEXT NOT NULL, etape TEXT NOT NULL, matter TEXT NOT NULL, etat TEXT NOT NULL, essais INTEGER NOT NULL DEFAULT 0,
  erreur TEXT NOT NULL DEFAULT '', created TEXT NOT NULL, updated TEXT NOT NULL, PRIMARY KEY(cloture_id, etape));
CREATE INDEX IF NOT EXISTS cloture_suites56100_m ON cloture_suites56100(matter, etat);
'''
VALIDEURS = ('administrateur', 'avocat')
CLE = re.compile(r'^[A-Za-z0-9_.:-]{8,80}$')     # même forme que le registre unique des dossiers


def maintenant():
    return datetime.now(timezone.utc).isoformat()


def nouvel_id(prefixe):
    return '%s-%s' % (prefixe, uuid.uuid4().hex)        # jamais un horodatage à la seconde


def schema(desk):
    if getattr(desk, '_gestion5697', False):
        return
    desk.db.executescript(SCHEMA)
    desk.db.commit()
    desk._gestion5697 = True


def journal(desk, matter, objet, ident, action, acteur, motif='', avant=None, apres=None):
    desk.db.execute('INSERT INTO gestion_journal5697(matter,objet,objet_id,action,acteur,motif,avant,apres,at) VALUES(?,?,?,?,?,?,?,?,?)',
                    (str(matter), objet, str(ident), action, compte(acteur), str(motif or '')[:500], json.dumps(avant or {}, ensure_ascii=False, default=str),
                     json.dumps(apres or {}, ensure_ascii=False, default=str), maintenant()))


def historique(desk, acteur, mid, objet='', limite=100):
    dossier(desk, acteur, mid)
    schema(desk)
    sql, args = 'SELECT * FROM gestion_journal5697 WHERE matter=?', [str(mid)]
    if objet:
        sql, args = sql + ' AND objet=?', args + [objet]
    return [dict(r) for r in desk.db.execute(sql + ' ORDER BY id DESC LIMIT ?', args + [int(limite)])]


# ------------------------------------------------------------------------------------------------ acteur, droits, dossier
def acteur_de(acteur):
    from .acces5697 import acteur_de as normaliser
    return normaliser(acteur)


def compte(acteur):
    try:
        return acteur_de(acteur).compte[:160]
    except Stop:
        return str(acteur or '')[:160]


def dossier(desk, acteur, mid):
    """Dossier lisible par l'acteur. Réservé ou absent : même refus neutre (404), sans dire lequel. Garde appliquée avant toute lecture."""
    from . import acces5697
    acces5697.exiger(desk, acteur, dossier_id=str(mid or ''))
    ligne = _registre(desk, str(mid or ''))
    if not ligne:
        raise Stop(acces5697.NEUTRE)
    return ligne


def _registre(desk, mid):
    """Fiche du dossier : la projection du registre unique (``dossiers5697.projeter`` écrit le registre lu par ``load_matters`` : chemin,
    correspondants), sinon le registre historique d'avant la migration. Jamais un second registre."""
    return next((m for m in load_matters(desk.c, unfiltered=True) if str(m['id']) == mid), None)


def registre_unique(desk, mid):
    """Ligne du registre unique (lot dossiers : statut actif / archivé et version), ou None si le dossier n'y est pas encore migré."""
    try:
        if not desk.db.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='dossiers5697'").fetchone():
            return None
        row = desk.db.execute('SELECT id,statut,version,responsable FROM dossiers5697 WHERE id=?', (str(mid),)).fetchone()
    except Exception:   # noqa: BLE001 — registre unique illisible : l'état local du lot fait foi, signalé par l'appelant
        return None
    return dict(row) if row else None


def perimetre(desk, acteur):
    from . import acces5697
    return acces5697.dossiers_autorises(desk, acteur)


def droit(desk, acteur, nom):
    """Droit effectif du compte (rôle, puis réglage propre au compte). Registre illisible : refus."""
    from . import membres5650
    a = acteur_de(acteur)
    if a.role == 'agent':
        return
    if a.role not in membres5650.ROLES:
        raise Stop('role_insuffisant')
    try:
        droits = membres5650.effective_rights(desk.c['state_dir'], a.compte, a.role)
    except Stop:
        raise Stop('controle_acces_indisponible') from None
    if not droits.get(nom):
        raise Stop('role_insuffisant')


def valideur(acteur):
    if acteur_de(acteur).role not in VALIDEURS:
        raise Stop('role_insuffisant')


def cle_de(valeur):
    valeur = str(valeur or '').strip()
    if not CLE.match(valeur):
        raise Stop('cle_idempotence_requise')
    return valeur


def deja_fait(desk, table, cle, parametres):
    """Même clé, mêmes paramètres : le résultat antérieur. Même clé, autre demande : conflit explicite (jamais un faux succès)."""
    row = desk.db.execute('SELECT * FROM %s WHERE cle=?' % table, (cle,)).fetchone()
    if row and row['parametres'] != parametres:
        raise Stop('cle_idempotence_deja_utilisee')
    return row


def version_attendue(row, valeur, colonne='revision'):
    if valeur in (None, ''):
        raise Stop('version_requise')
    try:
        if int(valeur) != int(row[colonne]):
            raise Stop('version_en_conflit')
    except (TypeError, ValueError):
        raise Stop('version_requise') from None


def texte(valeur, n=300):
    return re.sub(r'\s+', ' ', str(valeur or '')).strip()[:n]


def jour(valeur, requis=True):
    from .facturation5653 import day_value
    return day_value(valeur, requis)


def lire_demande(desk, acteur, ident):
    """Demande de pièces lue par le portefeuille (lot documents-mission, § 17) comme prochaine action : dossier, objet, date de recontrôle, état."""
    from . import remises5697
    v = remises5697.vue_demande(desk, acteur, ident)
    attendus = [e for e in v['elements'] if e['etat'] in ('a_recevoir', 'a_remplacer', 'recu_a_verifier')]
    return {'id': v['id'], 'matter': v['matter'], 'titre': 'Demande de pièces (%d attendue(s))' % len(attendus), 'echeance': v['revoir_le'],
            'etat': 'close' if v['etat'] == 'close' or not attendus else 'ouverte'}
