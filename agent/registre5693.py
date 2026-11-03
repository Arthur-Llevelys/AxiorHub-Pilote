"""5.6.93, lot registre : collecte complète des journaux (N03), contrôles des fichiers sans famine (N05), identité des documents (N06), réparation des données
(cahier de corrections 5.6.90, chapitres N03, N05, N06 et « Réparer les données déjà enregistrées »).

N03. Avant : chaque adaptateur relisait ``ORDER BY updated ASC LIMIT 2000`` puis la source était marquée « fresh » ; la 2 001e demande restait invisible, sans
alerte, et les réponses à décider avaient un plafond fixe de 200. Ici : un parcours commun par pages, avec un curseur stable (clé, identifiant) enregistré après
chaque page. L'avancement du lot (``curseur``, ``lus``, ``complet``) est séparé de la signature complètement consommée (``signature``), qui n'est écrite qu'à la
fin du lot. Tant que tout n'est pas lu, la source dit « Collecte partielle ». Une mise à jour fait avancer la clé de sa ligne : elle est relue au passage
suivant. Un changement sans nouvelle clé (acte signé, ligne supprimée) déclenche une relecture complète, bornée elle aussi. Un retrait (ligne sortie du journal)
est traité explicitement : une carte sans fichier quitte la file ; un fichier déposé reste suivi par son fichier. L'affichage ne lit qu'une page par source ;
le travail d'arrière-plan lit le reste.

N05. Avant : ``ORDER BY production_state='verified', updated`` faisait toujours passer les fichiers non vérifiés d'abord : 40 absents occupaient chaque lot de 40.
Ici : ``checked_at``, ``next_check_at``, ``failure_count`` et une erreur structurée (``check_error``) séparés de ``updated`` ; délai progressif pour un fichier
absent ; panne du stockage : quelques requêtes, puis une attente croissante, sans pénaliser les documents ; une part du lot réservée aux documents valides et
aux versions nouvelles, avec une rotation par date du dernier contrôle. Réessayer, Retrouver le fichier et Retirer du suivi : l'incident reste visible, il
n'occupe plus le travail. Chaque passage compte ses requêtes au stockage et s'arrête à sa borne ; l'affichage n'en fait aucune.

N06. Avant : même empreinte et même format dans le dossier valaient renommage ; deux fichiers distincts au contenu identique devenaient un seul document.
Ici : l'identité composite stockage + compte + identifiant stable passe d'abord, puis le lien de création du producteur, puis une identité provisoire de
chemin. Une empreinte identique n'est qu'une suggestion de doublon, jamais une fusion. Un renommage n'est accepté qu'avec l'identifiant conservé ; pour une
ligne ancienne dont l'identifiant n'a jamais été lu, seulement avec un candidat unique au même contenu, l'ancien emplacement vide et inconnu du registre.

Réparation : répétable, par petits lots avec curseur, sans fichier créé, déplacé ni certifié. Les anciennes collectes repartent du début ; les sources
« fresh » qui ont pu atteindre l'ancien plafond sont signalées ; les anciennes lignes reçoivent des dates de contrôle étalées ; les documents fusionnés
sont scindés, une relecture ambiguë revient à relire.
"""
from datetime import datetime, timedelta, timezone
import hashlib
import json
from pathlib import PurePosixPath
import sqlite3
import uuid

from .common import Stop, digest

VERSION = 5693
ANCIEN_PLAFOND = 2000          # plafond des adaptateurs jusqu'en 5.6.90 : une source qui l'atteignait a pu être tronquée
PAGE_AFFICHAGE = 200           # une page par source et par affichage, au plus
PAGE_FOND = 500
PAGES_FOND = 40                # 20 000 lignes par source et par passage d'arrière-plan
RELECTURE_HEURES = 24          # relecture complète d'une source, au plus une fois par jour en arrière-plan (retraits, changements sans nouvelle clé)
DELAI_ABSENT = timedelta(minutes=15)     # premier délai après un échec ; doublé à chaque échec
DELAI_MAX = timedelta(hours=24)
PANNE_SEUIL = 3                # échecs de stockage consécutifs (hors fichier absent) avant d'arrêter le passage
PANNE_DELAI = timedelta(minutes=10)
PANNE_MAX = timedelta(hours=6)
REQUETES_PAR_PASSAGE = 120     # borne de requêtes au stockage d'un passage de contrôle (40 contrôles par défaut)
LOT_REPARATION = 500
RETRAIT_DELAI = timedelta(hours=1)       # une réservation récente sans ligne de journal n'est pas un retrait : son producteur écrit peut-être encore
PANNE_CLE = 'registre5693:panne'
TOUR_CLE = 'registre5693:tour'
MESURE_CLE = 'registre5693:mesure'
SUIVI = {'actif': 'Suivi', 'recherche': 'Recherche du fichier demandée', 'retire': 'Retiré du suivi'}
ERREURS = {'fichier_introuvable': 'Fichier introuvable à son emplacement.', 'fichier_non_conforme': 'Le fichier ne correspond pas à la sortie attendue.',
           'stockage_injoignable': 'Stockage injoignable : aucun document n’est mis en cause.', 'relecture_impossible': 'Le fichier n’a pas pu être relu.',
           'sortie_non_publiee': 'La sortie n’a jamais été déposée.', 'fichiers_manquants': 'Une partie des fichiers du projet manque.'}
CODES = {'fichier_introuvable_a_cet_emplacement': 'fichier_introuvable', 'fichier_absent_apres_depot': 'fichier_introuvable', 'fichier_non_conforme': 'fichier_non_conforme',
         'fichier_different_de_la_sortie_attendue': 'fichier_non_conforme', 'relecture_impossible': 'relecture_impossible', 'sortie_non_publiee': 'sortie_non_publiee',
         'fichiers_manquants': 'fichiers_manquants'}
ABSENT = ('http_404', 'fichier_nextcloud_introuvable')

SCHEMA = '''
CREATE TABLE IF NOT EXISTS prepared_identity5693(id TEXT PRIMARY KEY, kind TEXT NOT NULL, matter_id TEXT NOT NULL DEFAULT '', document_id TEXT NOT NULL, other_document_id TEXT NOT NULL DEFAULT '',
    item_id TEXT NOT NULL DEFAULT '', other_item_id TEXT NOT NULL DEFAULT '', detail TEXT NOT NULL DEFAULT '{}', state TEXT NOT NULL, created TEXT NOT NULL, updated TEXT NOT NULL,
    UNIQUE(kind, item_id, other_item_id));
CREATE INDEX IF NOT EXISTS prepared_identity5693_document ON prepared_identity5693(document_id, state);
'''
COLONNES = {
    'prepared_sources5689': (('curseur', "TEXT NOT NULL DEFAULT ''"), ('rescan', "TEXT NOT NULL DEFAULT ''"), ('rescan_fin', "TEXT NOT NULL DEFAULT ''"), ('lot_signature', "TEXT NOT NULL DEFAULT ''"),
                             ('complet', 'INTEGER NOT NULL DEFAULT 0'), ('lus', 'INTEGER NOT NULL DEFAULT 0'), ('pages', 'INTEGER NOT NULL DEFAULT 0'), ('dernier_complet', "TEXT NOT NULL DEFAULT ''"),
                             ('parcours', 'INTEGER NOT NULL DEFAULT 0')),
    'prepared_items5689': (('checked_at', "TEXT NOT NULL DEFAULT ''"), ('next_check_at', "TEXT NOT NULL DEFAULT ''"), ('failure_count', 'INTEGER NOT NULL DEFAULT 0'),
                           ('check_error', "TEXT NOT NULL DEFAULT ''"), ('suivi', "TEXT NOT NULL DEFAULT 'actif'")),
}
INDEX = ('CREATE INDEX IF NOT EXISTS prepared_items5693_controle ON prepared_items5689(lifecycle, next_check_at)',
         'CREATE INDEX IF NOT EXISTS prepared_items5693_reponse ON prepared_items5689(response_id)')


def maintenant():
    """Instant courant ; un seul point d'entrée, pour que les essais puissent avancer l'horloge."""
    return datetime.now(timezone.utc)


def _iso(moment):
    return moment.isoformat(timespec='microseconds')


def colonnes(desk, table, wanted):
    """Migration idempotente : ``PRAGMA table_info`` puis ``ALTER TABLE`` pour chaque colonne absente. Aucune table n'est recréée."""
    known = {r[1] for r in desk.db.execute('PRAGMA table_info(%s)' % table)}
    for name, decl in wanted:
        if name not in known:
            desk.db.execute('ALTER TABLE %s ADD COLUMN %s %s' % (table, name, decl))


def ensure_schema(desk):
    """Appelé par ``livrables5689.ensure_schema`` : une fois par connexion, puis rien."""
    if getattr(desk, '_registre5693', None) is desk.db:
        return
    from .livrables5689 import SCHEMA as REGISTRE
    desk.db.executescript(REGISTRE)          # tables du registre d'abord : les colonnes s'y ajoutent
    desk.db.executescript(SCHEMA)
    for table, wanted in COLONNES.items():
        colonnes(desk, table, wanted)
    for sql in INDEX:
        desk.db.execute(sql)
    from . import office_gen5684 as gen
    gen.schema(desk)            # colonne « storage » de l'identité composite (N06)
    desk.db.commit()
    try:
        desk._registre5693 = desk.db
    except AttributeError:      # objet sans attributs libres : la vérification est simplement refaite
        pass


# ============================================================================================ N03 : parcours commun des journaux
class Parcours:
    """Un journal lu par pages : clé d'ordre (mise à jour), identifiant de ligne, filtre de retenue et lecture d'une ligne en sorties du registre.

    ``retenu(desk, ligne)`` : True (la ligne décrit une sortie), False (la ligne a quitté le journal des sorties : retrait), None (rien à dire, par exemple
    une demande encore en cours). ``objet_sql`` donne l'objet du lien producteur à partir de la ligne, pour les retraits et la réparation des identités."""

    def __init__(self, nom, table, cle, ident, lire, retenu=None, colonnes='t.*', prefiltre='', objet_sql='', prefixe=False, suppressions=False):
        self.nom, self.table, self.lire, self.retenu = nom, table, lire, retenu or (lambda desk, r: True)
        self.cle = "COALESCE(CAST(%s AS TEXT),'')" % cle
        self.ident = "CAST(%s AS TEXT)" % ident
        self.colonnes, self.prefiltre, self.objet_sql, self.prefixe, self.suppressions = colonnes, prefiltre, objet_sql, prefixe, suppressions

    def page(self, desk, apres=None, jusqua=None, limite=PAGE_FOND):
        where, params = ([self.prefiltre] if self.prefiltre else []), []
        if apres:
            where.append('(%s > ? OR (%s = ? AND %s > ?))' % (self.cle, self.cle, self.ident))
            params += [apres[0], apres[0], apres[1]]
        if jusqua:
            where.append('(%s < ? OR (%s = ? AND %s <= ?))' % (self.cle, self.cle, self.ident))
            params += [jusqua[0], jusqua[0], jusqua[1]]
        sql = 'SELECT %s, %s AS _cle, %s AS _ident%s FROM %s%s ORDER BY %s, %s LIMIT ?' % (self.colonnes, self.cle, self.ident, self._objet(), self.table,
                                                                                        (' WHERE ' + ' AND '.join(where)) if where else '', self.cle, self.ident)
        return desk.db.execute(sql, params + [int(limite)]).fetchall()

    def _objet(self):
        return (', CAST(%s AS TEXT) AS _objet' % self.objet_sql) if self.objet_sql else ''

    def fin(self, desk):
        """Dernière clé du journal au début d'un lot : l'instant de fin de la relecture complète ; ce qui arrive ensuite est lu par le parcours ordinaire."""
        row = desk.db.execute('SELECT %s, %s FROM %s%s ORDER BY %s DESC, %s DESC LIMIT 1' % (self.cle, self.ident, self.table, (' WHERE ' + self.prefiltre) if self.prefiltre else '', self.cle, self.ident)).fetchone()
        return [row[0], row[1]] if row else None

    def lignes_de(self, desk, objet):
        """Lignes du journal qui décrivent cet objet (réparation des identités)."""
        if not self.objet_sql:
            return []
        test = ("? LIKE (%s || ':%%')" if self.prefixe else '%s = ?') % self.objet_sql
        return desk.db.execute('SELECT %s, %s AS _cle, %s AS _ident%s FROM %s WHERE %s%s LIMIT 5' % (self.colonnes, self.cle, self.ident, self._objet(), self.table, test,
                                                                                                    (' AND ' + self.prefiltre) if self.prefiltre else ''), (str(objet),)).fetchall()


def lignes(*parcours):
    """Lecture complète d'une source, page après page (aucun plafond) : la forme historique ``rows(desk)`` des adaptateurs. Le registre, lui, lit page par page
    grâce à l'attribut ``parcours`` ; une fonction ``rows`` remplacée (essais, panne simulée) est lue comme avant, en une fois."""
    def rows(desk):
        for p in parcours:
            after = None
            while True:
                page = p.page(desk, after)
                for r in page:
                    yield from _entree(desk, p, r)
                if len(page) < PAGE_FOND:
                    break
                after = [page[-1]['_cle'], page[-1]['_ident']]
    rows.parcours = parcours
    return rows


def _entree(desk, p, r):
    """(identifiant, lecture différée) d'une ligne retenue ; une ligne en retrait donne une lecture qui traite ce retrait."""
    verdict = p.retenu(desk, r)
    if verdict is True:
        yield r['_ident'], p.lire(desk, r)
    elif verdict is False and p.objet_sql:
        objet = r['_objet'] if '_objet' in r.keys() else r['_ident']
        yield r['_ident'], lambda: _retrait(desk, _service_du(p), objet, p.prefixe) or []


def _service_du(p):
    return p.nom.split('.', 1)[0]


def _json(value, default=''):
    try:
        data = json.loads(value) if value else None
    except ValueError:
        return default
    return data if data is not None else default


def _etat(desk, sid):
    row = desk.db.execute('SELECT * FROM prepared_sources5689 WHERE source=?', (sid,)).fetchone()
    return dict(row) if row else None


def _ecrire(desk, sid, **fields):
    fields.setdefault('last_try', _iso(maintenant()))
    if not desk.db.execute('SELECT 1 FROM prepared_sources5689 WHERE source=?', (sid,)).fetchone():
        desk.db.execute("INSERT INTO prepared_sources5689(source,state) VALUES(?,?)", (sid, fields.get('state', 'partial')))
    desk.db.execute('UPDATE prepared_sources5689 SET %s WHERE source=?' % ', '.join(k + '=?' for k in fields), tuple(fields.values()) + (sid,))


def collecter(desk, src, force=False, fond=False, signature=None):
    """Lit une source paginée. Retour : 'inchangee', 'partielle' ou 'complete'. Chaque page validée enregistre son curseur : un arrêt au milieu d'un lot
    reprend à la page suivante, sans perte ; une ligne relue est sans effet (``observe`` est idempotent)."""
    from . import livrables5689 as liv
    sid = src['id']
    walks = src['rows'].parcours
    st = _etat(desk, sid) or {}
    now = _iso(maintenant())
    complete_before = bool(st.get('complet')) and st.get('parcours') == VERSION
    rescan = _json(st.get('rescan'), {}) or None
    stale_full = fond and (not st.get('dernier_complet') or st['dernier_complet'] < _iso(maintenant() - timedelta(hours=RELECTURE_HEURES)))
    if not force and complete_before and not rescan and st.get('signature') == signature and st.get('state') != 'unavailable' and not stale_full:
        return 'inchangee'
    if complete_before or not st:        # nouveau lot : la signature mesurée maintenant ne sera « consommée » qu'à la fin du lot
        debut = 'debut' if not _json(st.get('curseur'), {}) else st.get('rescan_fin') or ''
        st.update(lot_signature=signature, lus=0, pages=0, errors=0, rescan_fin=debut)
        _ecrire(desk, sid, lot_signature=signature, lus=0, pages=0, errors=0, complet=0, parcours=VERSION, state=st.get('state') or 'partial',
                message=st.get('message') or '', rescan_fin=debut)
    elif not _json(st.get('curseur'), {}) and not rescan and st.get('rescan_fin') != 'debut':      # collecte reprise depuis le début (réparation)
        st['rescan_fin'] = 'debut'
        _ecrire(desk, sid, rescan_fin='debut')
    # « force » relit ce qui est nouveau avec le budget de l'arrière-plan ; la relecture complète, elle, n'a lieu qu'une fois par jour ou sur un changement sans clé
    if (stale_full or (complete_before and st.get('signature') != signature and _sans_nouvelle_cle(desk, walks, _json(st.get('curseur'), {})))) and not rescan:
        rescan = {'pos': {}, 'fin': {p.nom: p.fin(desk) for p in walks}}
        _ecrire(desk, sid, rescan=json.dumps(rescan), rescan_fin=json.dumps(rescan['fin']))
    desk.db.commit()
    taille = PAGE_FOND if (fond or force) else PAGE_AFFICHAGE
    pages = PAGES_FOND if (fond or force) else 1
    curseur = _json(st.get('curseur'), {}) or {}
    lus, errors = int(st.get('lus') or 0), int(st.get('errors') or 0)
    fini = True
    for p in walks:                       # 1. ce qui est nouveau ou modifié depuis le curseur : les documents récents d'abord visibles (budget propre à chaque parcours)
        done, _, n, e = _lire_pages(desk, sid, p, curseur, None, taille, pages, 'curseur')
        lus, errors, fini = lus + n, errors + e, fini and done
    if fini and rescan:                   # 2. relecture complète, jusqu'à l'instant de fin du lot ; les retraits supprimés sont constatés à la fin
        for p in walks:
            pos = rescan['pos']
            if pos.get(p.nom) == 'fin':
                continue
            end = rescan['fin'].get(p.nom)
            done = True
            if end:
                done, _, n, e = _lire_pages(desk, sid, p, pos, end, taille, pages, 'rescan', rescan)
                lus, errors = lus + n, errors + e
            if done:
                pos[p.nom] = 'fin'
                _ecrire(desk, sid, rescan=json.dumps(rescan))
                desk.db.commit()
            fini = fini and done
        if fini:
            for p in walks:
                if p.suppressions:
                    _retraits_supprimes(desk, p)
            rescan = None
            _ecrire(desk, sid, rescan='', rescan_fin='', dernier_complet=now)
            desk.db.commit()
    if fini and not rescan:
        message = ('%d ligne(s) illisible(s) ignorée(s) ; les autres documents restent affichés.' % errors) if errors else ''
        extra = {'dernier_complet': now, 'rescan_fin': ''} if st.get('rescan_fin') == 'debut' else {}     # lu depuis le début : vaut relecture complète
        _ecrire(desk, sid, state='partial' if errors else 'fresh', message=message, signature=st.get('lot_signature') or signature or '', complet=1, lus=lus, errors=errors, rows=lus,
                last_ok=now, parcours=VERSION, **extra)
        desk.db.commit()
        return 'complete'
    _ecrire(desk, sid, state='partial', message='Collecte partielle : %d ligne(s) du journal lue(s) dans ce lot, la suite est lue en arrière-plan. Les documents déjà connus restent affichés.' % lus,
            complet=0, lus=lus, errors=errors, last_ok=now, parcours=VERSION)
    desk.db.commit()
    liv_audit = getattr(desk, 'audit', None)
    if liv_audit and not complete_before and not st.get('lus'):
        liv_audit('registre5693_collecte_partielle', {'source': sid})
    return 'partielle'


def _sans_nouvelle_cle(desk, walks, curseur):
    """La signature a changé mais aucune ligne n'a de clé nouvelle : changement sans date (acte signé) ou ligne supprimée. Une relecture complète s'impose."""
    for p in walks:
        page = p.page(desk, curseur.get(p.nom), None, 1)
        if page:
            return False
    return True


def _lire_pages(desk, sid, p, positions, end, taille, pages, colonne, rescan=None):
    """Pages d'un parcours. Une ligne illisible n'abandonne que ses écritures ; le curseur n'avance qu'avec la page validée. Retour : (fini, pages restantes, lues, erreurs)."""
    from . import livrables5689 as liv
    lus = errors = 0
    while pages > 0:
        page = p.page(desk, positions.get(p.nom), end, taille)
        pages -= 1
        page_errors = 0
        for r in page:
            for ident, read in _entree(desk, p, r):
                try:
                    with liv._atomique(desk):
                        for entry in read():
                            liv.observe(desk, entry)
                except (Stop, ValueError, TypeError, KeyError, AttributeError, sqlite3.IntegrityError) as ex:
                    page_errors += 1
                    # l'identifiant et la nature de l'erreur, jamais le contenu de la ligne ni le texte de l'exception (il peut citer ce contenu)
                    desk.audit('livrables5689_ligne_illisible', {'source': sid, 'ligne': str(ident)[:80], 'cause': type(ex).__name__})
        lus, errors = lus + len(page), errors + page_errors
        if page:
            positions[p.nom] = [page[-1]['_cle'], page[-1]['_ident']]
            st = _etat(desk, sid) or {}
            progress = {'pages': int(st.get('pages') or 0) + 1, 'lus': int(st.get('lus') or 0) + len(page), 'errors': int(st.get('errors') or 0) + page_errors}
            if colonne == 'curseur':
                _ecrire(desk, sid, curseur=json.dumps(positions), **progress)
            else:
                _ecrire(desk, sid, rescan=json.dumps(rescan), **progress)
            desk.db.commit()
        if len(page) < taille:
            return True, pages, lus, errors
    return False, pages, lus, errors


def _retrait(desk, service, objet, prefixe=False):
    """La ligne a quitté le journal des sorties. Une carte sans fichier (réponse décidée, projet abandonné) quitte la file ; un fichier déposé reste suivi
    par son fichier, jamais effacé sur la foi d'un journal. Un autre journal qui décrit encore le même objet garde la carte."""
    test = "l.source_object_id LIKE ? || ':%'" if prefixe else 'l.source_object_id=?'
    rows = desk.db.execute("SELECT i.* FROM prepared_source_links5689 l JOIN prepared_items5689 i ON i.id=l.item_id WHERE l.link_kind='producteur' AND l.source_service=? AND %s AND i.lifecycle='current'" % test,
                           (service, str(objet))).fetchall()
    now = _iso(maintenant())
    for row in rows:
        others = desk.db.execute("SELECT COUNT(*) FROM prepared_source_links5689 WHERE item_id=? AND link_kind='producteur' AND source_service<>?", (row['id'], service)).fetchone()[0]
        if others or row['path'] or row['production_state'] != 'preparing':
            continue
        desk.db.execute("UPDATE prepared_items5689 SET lifecycle='archived', state_reason='retire_du_journal', updated=? WHERE id=?", (now, row['id']))
    return []


def _retraits_supprimes(desk, p):
    """Lignes supprimées du journal : leurs liens n'ont plus de ligne. Une réservation récente n'est pas concernée (son producteur écrit peut-être encore)."""
    service = _service_du(p)
    limit = _iso(maintenant() - RETRAIT_DELAI)
    table = p.table.split(' LEFT JOIN ')[0]
    test = ("l.source_object_id LIKE (%s || ':%%')" if p.prefixe else 'l.source_object_id = %s') % p.objet_sql
    try:
        rows = desk.db.execute("SELECT DISTINCT l.source_object_id FROM prepared_source_links5689 l JOIN prepared_items5689 i ON i.id=l.item_id WHERE l.link_kind='producteur' AND l.source_service=? "
                               "AND i.lifecycle='current' AND i.updated<=? AND NOT EXISTS (SELECT 1 FROM %s WHERE %s) LIMIT 500" % (table, test), (service, limit)).fetchall()
    except sqlite3.OperationalError:
        return
    for r in rows:
        _retrait(desk, service, r[0])
    desk.db.commit()


def diagnostic_sources(desk):
    """Sources dont l'ancien parcours a pu atteindre son plafond : journal d'au moins 2 000 lignes, lu avant cette version."""
    from . import livrables_sources5689 as sources
    out = []
    for src in sources.SOURCES:
        walks = getattr(src.get('rows'), 'parcours', ())
        st = _etat(desk, src['id']) or {}
        for p in walks:
            try:
                n = desk.db.execute('SELECT COUNT(*) FROM %s%s' % (p.table, (' WHERE ' + p.prefiltre) if p.prefiltre else '')).fetchone()[0]
            except sqlite3.OperationalError:
                continue
            plafond = 200 if src['id'] == 'reponses5671' else ANCIEN_PLAFOND
            if n >= plafond and st.get('parcours') != VERSION:
                out.append({'source': src['id'], 'lignes': n, 'etat_ancien': st.get('state', ''), 'plafond': plafond})
    return out


def migrer_sources(desk):
    """Relance la collecte de chaque source lue par l'ancien parcours : depuis le début, de façon idempotente (rien n'est supprimé, ni version ni relecture).
    Une source « fresh » qui a pu être tronquée devient « Collecte partielle » jusqu'à la fin de la nouvelle lecture."""
    ensure_schema(desk)
    suspects = {d['source'] for d in diagnostic_sources(desk)}
    count = 0
    for row in desk.db.execute('SELECT source, state FROM prepared_sources5689 WHERE parcours<>?', (VERSION,)).fetchall():
        message = 'Collecte reprise depuis le début : l’ancienne lecture s’arrêtait à un plafond.' if row['source'] in suspects else 'Collecte reprise depuis le début.'
        desk.db.execute("UPDATE prepared_sources5689 SET curseur='', rescan='', rescan_fin='', complet=0, lus=0, pages=0, errors=0, lot_signature='', parcours=?, state=?, message=? WHERE source=?",
                        (VERSION, 'partial' if row['state'] in ('fresh', 'partial') or row['source'] in suspects else row['state'], message, row['source']))
        count += 1
    desk.db.commit()
    if suspects:
        desk.audit('registre5693_sources_reprises', {'sources': sorted(suspects)})
    return {'sources_reprises': count, 'plafond_atteint': sorted(suspects)}


# ============================================================================================ N05 : contrôles des fichiers
def delai(failures, salt=''):
    """Délai progressif : 15 min, 30 min, 1 h… jusqu'à 24 h ; un léger décalage par document étale les reprises."""
    base = min(DELAI_MAX, DELAI_ABSENT * (2 ** max(0, int(failures) - 1)))
    jitter = int(hashlib.sha256(str(salt).encode()).hexdigest()[:4], 16) % 60
    return base + timedelta(seconds=jitter)


def erreur(code, message='', **detail):
    """Erreur structurée d'un contrôle : un code stable, un texte clair, l'instant. Jamais le contenu du fichier ni un chemin complet."""
    return json.dumps({'code': code, 'message': message or ERREURS.get(code, code.replace('_', ' ')), 'at': _iso(maintenant()), **detail}, ensure_ascii=False)


def noter_echec(desk, item_id, reason, controle=True):
    """Échec propre au document (fichier absent, contenu différent) : erreur structurée, compteur, prochaine tentative différée."""
    row = desk.db.execute('SELECT failure_count FROM prepared_items5689 WHERE id=?', (item_id,)).fetchone()
    if not row:
        return
    code = CODES.get(str(reason), str(reason)[:60])
    failures = int(row['failure_count'] or 0) + (1 if controle else 0)
    now = maintenant()
    desk.db.execute('UPDATE prepared_items5689 SET checked_at=?, next_check_at=?, failure_count=?, check_error=? WHERE id=?',
                    (_iso(now), _iso(now + delai(failures, item_id)) if controle else _iso(now), failures, erreur(code, essais=failures), item_id))


def noter_succes(desk, item_id, fileid=''):
    now = _iso(maintenant())
    desk.db.execute("UPDATE prepared_items5689 SET checked_at=?, next_check_at='', failure_count=0, check_error='', suivi=CASE WHEN suivi='recherche' THEN 'actif' ELSE suivi END, "
                    "file_id=CASE WHEN file_id='' AND ?<>'' THEN ? ELSE file_id END WHERE id=?", (now, str(fileid or ''), str(fileid or ''), item_id))


def selection(desk, limite=40, since=''):
    """Contrôles arrivés à échéance. Une part du lot (la moitié, l'unité impaire en alternance) va aux documents valides et aux versions nouvelles, du plus
    anciennement contrôlé au plus récent (jamais contrôlé d'abord) ; l'autre aux reprises (absents, incomplets, recherches demandées) par échéance.
    Une part inutilisée revient à l'autre groupe. Jamais tous les échecs en priorité absolue, jamais l'inverse."""
    now = _iso(maintenant())
    tour = int(desk.settings(TOUR_CLE, 0) or 0)
    desk.setting(TOUR_CLE, tour + 1)
    base = ("FROM prepared_items5689 WHERE lifecycle='current' AND path<>'' AND production_state IN ('published','verified','partial') AND source_at>=? AND suivi<>'retire' "
            "AND (next_check_at='' OR next_check_at<=?)")
    valides = desk.db.execute('SELECT * ' + base + " AND production_state IN ('published','verified') AND failure_count=0 ORDER BY checked_at, updated, id LIMIT ?", (since, now, limite)).fetchall()
    reprises = desk.db.execute('SELECT * ' + base + " AND NOT (production_state IN ('published','verified') AND failure_count=0) ORDER BY suivi='recherche' DESC, next_check_at, checked_at, id LIMIT ?",
                               (since, now, limite)).fetchall()
    part_a = limite // 2 + (limite % 2 if tour % 2 == 0 else 0)
    part_b = limite - part_a
    a, b = list(valides[:part_a]), list(reprises[:part_b])
    reste = limite - len(a) - len(b)
    if reste > 0:
        a += list(valides[len(a):len(a) + reste])
        reste = limite - len(a) - len(b)
        b += list(reprises[len(b):len(b) + reste])
    return a + b


class Borne(Exception):
    """Fin anticipée d'un passage de contrôle : borne de requêtes atteinte, ou stockage en panne."""


class Compteur:
    """Client de stockage compté : chaque requête (état, lecture, liste) est comptée ; la borne arrête le passage ; trois échecs du stockage d'affilée
    (hors fichier absent) aussi. Le passage sait ainsi distinguer « ce document est absent » de « le stockage ne répond pas »."""

    def __init__(self, client, borne=REQUETES_PAR_PASSAGE):
        self.client, self.borne, self.requetes, self.consecutifs, self.panne = client, borne, 0, 0, False
        self._dossiers, self._empreintes = {}, {}      # un dossier n'est listé, un candidat n'est lu, qu'une fois par passage

    def _appel(self, name, *args):
        if isinstance(self.client, Compteur):
            return getattr(self.client, name)(*args)
        if self.requetes >= self.borne:
            raise Borne('borne_requetes')
        self.requetes += 1
        try:
            out = getattr(self.client, name)(*args)
        except Stop as ex:
            if str(ex) in ABSENT:
                self.consecutifs = 0
            else:
                self.consecutifs += 1
                if self.consecutifs >= PANNE_SEUIL:
                    self.panne = True
                    raise Borne('stockage_en_panne') from None
            raise
        self.consecutifs = 0
        return out

    def stat(self, path):
        return self._appel('stat', path)

    def download(self, item):
        return self._appel('download', item)

    def list_folder(self, path):
        if path not in self._dossiers:
            self._dossiers[path] = self._appel('list_folder', path)
        return self._dossiers[path]

    def empreinte(self, item):
        """Empreinte et taille d'un fichier candidat, lues une fois par passage (même chemin, même ETag)."""
        key = (item.get('path'), item.get('etag'))
        if key not in self._empreintes:
            raw = self.download(item)
            self._empreintes[key] = (hashlib.sha256(raw).hexdigest(), len(raw))
        return self._empreintes[key]

    def __getattr__(self, name):            # écriture ou autre appel (rare ici) : délégué tel quel
        return getattr(self.client, name)


def compter(client, borne=REQUETES_PAR_PASSAGE):
    return client if isinstance(client, Compteur) else Compteur(client, borne)


def en_panne(desk):
    """Attente en cours après une panne générale du stockage : aucun contrôle, aucune requête, jusqu'à l'échéance."""
    value = desk.settings(PANNE_CLE, None) or {}
    return bool(value.get('jusqua')) and value['jusqua'] > _iso(maintenant())


def noter_panne(desk, compteur):
    value = desk.settings(PANNE_CLE, None) or {}
    if compteur.panne:
        essais = int(value.get('essais') or 0) + 1
        attente = min(PANNE_MAX, PANNE_DELAI * (2 ** (essais - 1)))
        desk.setting(PANNE_CLE, {'jusqua': _iso(maintenant() + attente), 'essais': essais, 'depuis': value.get('depuis') or _iso(maintenant())})
        desk.audit('registre5693_stockage_en_panne', {'essais': essais, 'requetes': compteur.requetes})
    elif value and compteur.requetes and not compteur.consecutifs:
        desk.setting(PANNE_CLE, {})


def mesurer(desk, out):
    """Dernière mesure d'un passage : nombre de requêtes au stockage, borne, contrôles ; affichée au diagnostic."""
    desk.setting(MESURE_CLE, {'at': _iso(maintenant()), **{k: out.get(k) for k in ('controles', 'requetes', 'borne', 'absents', 'nouvelles_versions', 'verifies', 'panne', 'arret')}})


def controle(row):
    """État du suivi d'un fichier, pour la carte et la fiche : réel, sans succès supposé. Les actions possibles sont données, jamais déclenchées ici."""
    keys = row.keys() if hasattr(row, 'keys') else row
    if 'suivi' not in keys:
        return None
    err = _json(row['check_error'], {}) if row['check_error'] else {}
    suivi = row['suivi'] or 'actif'
    if suivi == 'retire':
        etat, label = 'retire', 'Retiré du suivi : l’incident reste noté, le fichier n’est plus contrôlé.'
    elif err:
        etat, label = 'incident', err.get('message') or 'Incident de contrôle.'
    elif row['checked_at']:
        etat, label = 'a_jour', 'Fichier contrôlé.'
    else:
        etat, label = 'en_attente', 'Contrôle du fichier pas encore fait.'
    actions = []
    if row['path'] and row['lifecycle'] == 'current' and row['production_state'] in ('published', 'verified', 'partial'):     # un dépôt en échec se relance chez son producteur
        if etat == 'incident':
            actions += ['reessayer', 'retrouver']
        actions.append('reprendre_suivi' if suivi == 'retire' else 'retirer_suivi')
    return {'etat': etat, 'label': label, 'code': err.get('code', ''), 'echecs': int(row['failure_count'] or 0), 'dernier': row['checked_at'], 'prochain': row['next_check_at'],
            'suivi': suivi, 'suivi_label': SUIVI.get(suivi, suivi), 'actions': actions}


def _ligne_suivie(desk, ident, version_id, hidden=frozenset(), droits=None):
    from . import livrables5689 as liv
    row = liv.charger(desk, ident, hidden, droits=droits)
    if version_id and str(version_id) != row['version_id']:
        raise Stop('version_perimee')
    if row['lifecycle'] != 'current' or not row['path']:
        raise Stop('fichier_pas_encore_cree')
    return row


def reessayer(desk, ident, version_id='', owner='', hidden=frozenset(), droits=None):
    """Réessayer : le contrôle redevient dû au prochain passage d'arrière-plan. Aucune requête ici, aucun travail créé (une pause reste respectée)."""
    row = _ligne_suivie(desk, ident, version_id, hidden, droits)
    desk.db.execute("UPDATE prepared_items5689 SET next_check_at=?, suivi=CASE WHEN suivi='retire' THEN 'actif' ELSE suivi END WHERE id=?", (_iso(maintenant()), row['id']))
    desk.db.commit()
    desk.audit('registre5693_reessayer', {'document': row['document_id'][:40], 'par': str(owner)[:120]})
    return _vue(desk, row['id'])


def retrouver(desk, ident, version_id='', owner='', hidden=frozenset(), droits=None):
    """Retrouver le fichier : au prochain passage, son dossier est relu ; un renommage n'est accepté qu'avec une preuve (identifiant conservé)."""
    row = _ligne_suivie(desk, ident, version_id, hidden, droits)
    desk.db.execute("UPDATE prepared_items5689 SET next_check_at=?, suivi='recherche' WHERE id=?", (_iso(maintenant()), row['id']))
    desk.db.commit()
    desk.audit('registre5693_retrouver', {'document': row['document_id'][:40], 'par': str(owner)[:120]})
    return _vue(desk, row['id'])


def retirer_suivi(desk, ident, version_id='', owner='', hidden=frozenset(), droits=None, reprendre=False):
    """Retirer du suivi : le document reste au registre avec son incident ; il n'occupe plus les contrôles. Reprendre le suivi l'y remet."""
    row = _ligne_suivie(desk, ident, version_id, hidden, droits)
    desk.db.execute("UPDATE prepared_items5689 SET suivi=?, next_check_at=? WHERE id=?", ('actif' if reprendre else 'retire', _iso(maintenant()) if reprendre else row['next_check_at'], row['id']))
    desk.db.commit()
    desk.audit('registre5693_reprendre_suivi' if reprendre else 'registre5693_retirer_suivi', {'document': row['document_id'][:40], 'par': str(owner)[:120]})
    return _vue(desk, row['id'])


def _vue(desk, item_id):
    row = desk.db.execute('SELECT * FROM prepared_items5689 WHERE id=?', (item_id,)).fetchone()
    return {'document_id': row['document_id'], 'version_id': row['version_id'], 'controle': controle(row)}


def initialiser_controles(desk, lot=LOT_REPARATION):
    """Anciennes lignes sans date de contrôle : par lots, avec un curseur. Les documents valides restent dus (rotation ordinaire) ; les absences gardent leur
    incident et reçoivent une échéance étalée sur 24 h, pour ne pas toutes repartir au même passage. Rien n'est certifié ni recréé."""
    ensure_schema(desk)
    after = str(desk.settings('registre5693:init', '') or '')
    if after == 'fin':
        return 0
    rows = desk.db.execute("SELECT id, production_state, state_reason FROM prepared_items5689 WHERE id>? ORDER BY id LIMIT ?", (after, lot)).fetchall()
    now = maintenant()
    for r in rows:
        if r['production_state'] in ('partial', 'failed') and r['state_reason']:
            code = CODES.get(r['state_reason'], r['state_reason'][:60])
            spread = timedelta(minutes=int(hashlib.sha256(r['id'].encode()).hexdigest()[:6], 16) % (24 * 60))
            desk.db.execute("UPDATE prepared_items5689 SET failure_count=MAX(failure_count,1), next_check_at=?, check_error=? WHERE id=? AND check_error=''",
                            (_iso(now + spread), erreur(code, reprise='5.6.93'), r['id']))
    desk.setting('registre5693:init', rows[-1]['id'] if len(rows) == lot else 'fin')
    return len(rows)


# ============================================================================================ N06 : identité des documents
IDENTITES = '''
CREATE TABLE IF NOT EXISTS document_identities5693(storage TEXT NOT NULL, fileid TEXT NOT NULL, document_id TEXT NOT NULL, created TEXT NOT NULL, PRIMARY KEY(storage, fileid));
CREATE INDEX IF NOT EXISTS document_identities5693_document ON document_identities5693(document_id);
'''


def par_identifiant(desk, fileid):
    """Identité composite : (stockage + compte, identifiant de fichier) → document. Une ligne ancienne sans stockage connu n'est reprise que si son document
    n'appartient pas déjà, sous ce même identifiant, à un autre stockage."""
    fileid = str(fileid or '')
    if not fileid:
        return None
    key = stockage(desk)
    hit = desk.db.execute('SELECT o.* FROM document_identities5693 i JOIN office_documents5684 o ON o.document_id=i.document_id WHERE i.storage=? AND i.fileid=?', (key, fileid)).fetchone()
    if hit:
        return hit
    for row in desk.db.execute("SELECT * FROM office_documents5684 WHERE fileid=? AND fileid<>''", (fileid,)).fetchall():
        if not desk.db.execute('SELECT 1 FROM document_identities5693 WHERE document_id=? AND storage<>?', (row['document_id'], key)).fetchone():
            return row
    return None


def noter_identifiant(desk, fileid, document_id):
    if fileid:
        desk.db.execute('INSERT OR IGNORE INTO document_identities5693(storage,fileid,document_id,created) VALUES(?,?,?,?)', (stockage(desk), str(fileid), str(document_id), _iso(maintenant())))


def stockage(desk):
    """Clé du stockage et du compte : adresse du serveur (ou dossier local, ou stockage cloud) et compte. Un identifiant de fichier n'a de sens que dans ce couple."""
    cfg = (getattr(desk, 'c', None) or {}).get('nextcloud_documents') or (getattr(desk, 'c', None) or {}).get('nextcloud') or {}
    if not isinstance(cfg, dict):
        return 'defaut'
    where = cfg.get('webdav_url') or cfg.get('url') or cfg.get('local_path') or cfg.get('stockage_cloud') or ''
    return digest('%s|%s' % (str(where).rstrip('/').lower(), str(cfg.get('username') or cfg.get('user') or '')))[:24]


def suggerer_doublon(desk, item):
    """Même contenu qu'un autre document courant du même dossier : une suggestion à examiner, jamais une fusion."""
    if not item.get('sha256') or not item.get('path'):
        return None
    ensure_schema(desk)
    for other in desk.db.execute("SELECT * FROM prepared_items5689 WHERE sha256=? AND matter_id=? AND document_id<>? AND lifecycle='current' AND path<>'' LIMIT 5",
                                 (item['sha256'], item['matter_id'], item['document_id'])).fetchall():
        if PurePosixPath(other['path']).suffix.lower() != PurePosixPath(item['path']).suffix.lower():
            continue
        a, b = sorted((item['id'], other['id']))
        now = _iso(maintenant())
        desk.db.execute("INSERT OR IGNORE INTO prepared_identity5693(id,kind,matter_id,document_id,other_document_id,item_id,other_item_id,detail,state,created,updated) VALUES(?,?,?,?,?,?,?,?,?,?,?)",
                        (uuid.uuid4().hex, 'doublon', item['matter_id'], item['document_id'], other['document_id'], a, b, json.dumps({'motif': 'meme_contenu'}), 'a_examiner', now, now))
        return other['document_id']
    return None


def doublons(desk, document_id=None, hidden=frozenset()):
    ensure_schema(desk)
    sql, params = "SELECT * FROM prepared_identity5693 WHERE kind='doublon' AND state='a_examiner'", []
    if document_id:
        sql += ' AND (document_id=? OR other_document_id=?)'
        params += [document_id, document_id]
    if hidden:
        sql += ' AND matter_id NOT IN (%s)' % ','.join('?' for _ in hidden)
        params += sorted(hidden)
    return [dict(r) for r in desk.db.execute(sql + ' ORDER BY created DESC LIMIT 50', params)]


def decider_doublon(desk, ident, decision, owner='', hidden=frozenset()):
    """« Ce sont deux documents » ou « c'est une copie » : la décision est notée ; aucune des deux ne fusionne les documents ni ne touche aux fichiers."""
    if decision not in ('ecarter', 'confirmer'):
        raise Stop('decision_invalide')
    ensure_schema(desk)
    row = desk.db.execute("SELECT * FROM prepared_identity5693 WHERE id=? AND kind='doublon'", (str(ident),)).fetchone()
    if not row or row['matter_id'] in hidden:
        raise Stop('livrable_absent')
    state = 'ecarte' if decision == 'ecarter' else 'copie_confirmee'
    desk.db.execute('UPDATE prepared_identity5693 SET state=?, detail=?, updated=? WHERE id=?', (state, json.dumps({**_json(row['detail'], {}), 'par': str(owner)[:120]}), _iso(maintenant()), row['id']))
    desk.db.commit()
    return {'id': row['id'], 'state': state}


def renommage_prouve(desk, client, row, candidates):
    """Un fichier absent de son emplacement a-t-il été renommé ? Seule preuve forte : le même identifiant de fichier à un autre chemin. Une ligne ancienne
    dont l'identifiant n'a jamais été lu n'est rapprochée que d'un candidat unique au même contenu, inconnu du registre ; sinon c'est une suggestion."""
    matches = []
    for item in candidates:
        if item.get('directory') or PurePosixPath(str(item.get('path'))).suffix.lower() != PurePosixPath(row['path']).suffix.lower():
            continue
        if row['file_id']:
            try:
                info = client.stat(item['path'])
            except Stop:
                continue
            if str(info.get('fileid') or '') == row['file_id']:
                return item, info, 'identifiant'
            continue
        if row['size'] and item.get('size') != row['size']:
            continue
        try:
            if hasattr(client, 'empreinte'):
                sha, size = client.empreinte(item)
            else:
                raw = client.download(item)
                sha, size = hashlib.sha256(raw).hexdigest(), len(raw)
        except Stop:
            continue
        if sha == row['sha256']:
            matches.append((item, size))
    if len(matches) != 1:
        return None, None, 'ambigu' if matches else 'absent'
    item, size = matches[0]
    known = desk.db.execute("SELECT 1 FROM prepared_items5689 WHERE path=? AND document_id<>? AND lifecycle IN ('current','replaced') LIMIT 1", (item['path'], row['document_id'])).fetchone()
    if known:
        return None, None, 'autre_document'
    try:
        info = client.stat(item['path'])
    except Stop:
        info = {'etag': item.get('etag'), 'fileid': ''}
    info['_raw_size'] = size
    return item, info, 'empreinte_unique'


def _chemin_journal(desk, service, objet):
    """Chemin principal décrit aujourd'hui par le journal d'un lien producteur ; None si le journal ne le dit pas."""
    from . import livrables_sources5689 as sources
    for src in sources.SOURCES:
        for p in getattr(src.get('rows'), 'parcours', ()):
            if _service_du(p) != service or not p.objet_sql:
                continue
            try:
                found = p.lignes_de(desk, objet)
            except sqlite3.OperationalError:
                return None
            for r in found:
                try:
                    entries = p.lire(desk, r)()
                except (Stop, ValueError, TypeError, KeyError, AttributeError):
                    continue
                for x in entries or ():
                    if str(x.get('object')) == str(objet) and x.get('path'):
                        return str(x['path'])
    return None


def scinder(desk, lot=LOT_REPARATION):
    """Documents fusionnés à tort (N06) : une carte portant les liens de plusieurs producteurs indépendants dont les journaux décrivent des chemins différents.
    Chaque chemin retrouve sa propre identité, sans déplacer de fichier. La relecture reste sur le fichier vu par la personne quand la chronologie le prouve ;
    sinon le cas est ambigu et revient à relire. Répétable : un document déjà scindé n'a plus qu'un chemin."""
    from . import livrables5689 as liv, office_gen5684 as gen
    ensure_schema(desk)
    gen.schema(desk)            # avant toute transaction : un script de schéma valide la transaction en cours
    after = str(desk.settings('registre5693:scission', '') or '')
    if after == 'fin':
        return {'examines': 0, 'scindes': 0}
    items = desk.db.execute("SELECT i.* FROM prepared_items5689 i WHERE i.id>? AND i.path<>'' AND (SELECT COUNT(*) FROM prepared_source_links5689 l WHERE l.item_id=i.id AND l.link_kind='producteur')>1 "
                            "ORDER BY i.id LIMIT ?", (after, lot)).fetchall()
    split = 0
    for item in items:
        links = desk.db.execute("SELECT * FROM prepared_source_links5689 WHERE item_id=? AND link_kind='producteur' ORDER BY created", (item['id'],)).fetchall()
        paths = {(l['source_service'], l['source_object_id'], l['source_version']): _chemin_journal(desk, l['source_service'], l['source_object_id']) for l in links}
        distinct = {p for p in paths.values() if p}
        if len(distinct) < 2:
            continue
        with liv._atomique(desk):
            made, ambiguous = _scinder_item(desk, dict(item), links, paths)
        split += made
        if made or ambiguous:
            desk.audit('registre5693_scission', {'document': item['document_id'][:40], 'nouveaux': made, 'relecture_ambigue': ambiguous})
    desk.setting('registre5693:scission', items[-1]['id'] if len(items) == lot else 'fin')
    return {'examines': len(items), 'scindes': split}


def _scinder_item(desk, item, links, paths):
    from . import livrables5689 as liv
    # l'identité est née avec le premier lien producteur : son fichier la garde ; l'ancien registre avait seulement déplacé la carte vers le chemin vu ensuite
    kept = next(p for p in (paths.get((l['source_service'], l['source_object_id'], l['source_version'])) for l in links) if p)
    now = _iso(maintenant())
    seen_fileid = ''     # identifiant de fichier lu au chemin où la carte avait été déplacée : il appartient à ce fichier-là
    if item['path'] != kept:     # localisation du registre seulement : aucun fichier n'est déplacé
        seen_fileid = item['file_id']
        desk.db.execute("UPDATE prepared_items5689 SET path=?, file_id='', updated=? WHERE id=?", (kept, now, item['id']))
        desk.db.execute("UPDATE prepared_representations5689 SET path=?, updated=? WHERE item_id=? AND role='principal'", (kept, now, item['id']))
        desk.db.execute("UPDATE office_documents5684 SET path=?, fileid=CASE WHEN ?<>'' THEN '' ELSE fileid END, updated=? WHERE document_id=?", (kept, seen_fileid, now, item['document_id']))
    reviews = desk.db.execute("SELECT * FROM prepared_reviews5689 WHERE item_id=? AND scope='cabinet'", (item['id'],)).fetchall()
    # chemin affiché au moment de chaque relecture : celui du dernier lien créé avant elle (l'ancien registre déplaçait la carte à chaque nouveau journal)
    shown = {}
    for rv in reviews:
        before = [l for l in links if l['created'] <= rv['at']]
        tie = any(l['created'] == rv['at'] for l in links) or (len(before) > 1 and before[-1]['created'] == before[-2]['created'])
        if not before or tie:       # chronologie muette : on ne sait pas quel fichier la personne avait sous les yeux
            shown[rv['id']] = None
        else:
            shown[rv['id']] = paths.get((before[-1]['source_service'], before[-1]['source_object_id'], before[-1]['source_version']))
    groups = {}
    for l in links:
        p = paths.get((l['source_service'], l['source_object_id'], l['source_version']))
        if p and p != kept:
            groups.setdefault(p, []).append(l)
    made = 0
    for path, group in groups.items():
        found = desk.db.execute('SELECT document_id FROM office_documents5684 WHERE path=? ORDER BY updated DESC LIMIT 1', (path,)).fetchone()
        document_id = found[0] if found and found[0] != item['document_id'] else uuid.uuid4().hex
        if not found or found[0] == item['document_id']:
            desk.db.execute('INSERT INTO office_documents5684(document_id,path,fileid,matter,created,updated) VALUES(?,?,?,?,?,?)', (document_id, path, '', item['matter_id'], now, now))
        ident = uuid.uuid4().hex
        fileid = seen_fileid if path == item['path'] else ''
        if fileid:
            desk.db.execute('UPDATE office_documents5684 SET fileid=? WHERE document_id=?', (fileid, document_id))
            desk.db.execute('UPDATE document_identities5693 SET document_id=? WHERE fileid=? AND document_id=?', (document_id, fileid, item['document_id']))
        row = {k: item[k] for k in item}
        row.update(source_service=group[0]['source_service'], source_object_id=group[0]['source_object_id'], source_version=group[0]['source_version'])     # son propre producteur
        row.update(id=ident, document_id=document_id, version_number=1, path=path, file_id=fileid, lifecycle='current', review_state='to_review' if item['review_state'] != 'not_required' else 'not_required',
                   production_state='published' if item['production_state'] == 'verified' else item['production_state'], state_reason='identite_scindee', legacy_item='doc:' + digest(path)[:24],
                   checked_at='', next_check_at='', failure_count=0, check_error='', suivi='actif', created=now, updated=now)
        cols = [k for k in row if k in item]
        desk.db.execute('INSERT INTO prepared_items5689(%s) VALUES(%s)' % (','.join(cols), ','.join('?' for _ in cols)), [row[k] for k in cols])
        for l in group:
            desk.db.execute('UPDATE prepared_source_links5689 SET item_id=? WHERE item_id=? AND link_kind=? AND source_service=? AND source_object_id=? AND source_version=?',
                            (ident, item['id'], l['link_kind'], l['source_service'], l['source_object_id'], l['source_version']))
        liv._representation(desk, row, path, 'principal', item['sha256'], '', item['size'], False, item['mime_type'])
        for rv in reviews:
            if shown.get(rv['id']) == path:      # la personne avait ce fichier sous les yeux : sa relecture le suit
                desk.db.execute('UPDATE prepared_reviews5689 SET item_id=?, document_id=? WHERE id=?', (ident, document_id, rv['id']))
                desk.db.execute('UPDATE prepared_items5689 SET review_state=? WHERE id=?', (liv.DECISIONS.get(rv['decision'], 'to_review'), ident))
        desk.db.execute("INSERT OR IGNORE INTO prepared_identity5693(id,kind,matter_id,document_id,other_document_id,item_id,other_item_id,detail,state,created,updated) VALUES(?,?,?,?,?,?,?,?,?,?,?)",
                        (uuid.uuid4().hex, 'scission', item['matter_id'], item['document_id'], document_id, item['id'], ident, json.dumps({'liens': len(group)}), 'fait', now, now))
        made += 1
    remaining = [rv for rv in reviews if shown.get(rv['id']) not in groups]
    ambiguous = any(shown.get(rv['id']) is None for rv in reviews)
    if reviews and (ambiguous or not any(shown.get(rv['id']) == kept for rv in remaining)):
        # relecture ambiguë, ou partie avec l'autre fichier : elle reste dans l'historique, elle ne vaut plus pour ce document
        desk.db.execute("UPDATE prepared_items5689 SET review_state='to_review', updated=? WHERE id=?", (now, item['id']))
        if ambiguous:
            desk.db.execute("INSERT OR IGNORE INTO prepared_identity5693(id,kind,matter_id,document_id,other_document_id,item_id,other_item_id,detail,state,created,updated) VALUES(?,?,?,?,?,?,?,?,?,?,?)",
                            (uuid.uuid4().hex, 'relecture_ambigue', item['matter_id'], item['document_id'], '', item['id'], '', '{}', 'a_relire', now, now))
    return made, bool(reviews) and ambiguous


# ============================================================================================ réparation et diagnostic
def migrer(desk, lot=LOT_REPARATION):
    """Réparation répétable, par petits lots : collecte relancée, dates de contrôle initialisées, identités scindées. Aucun effet externe, aucun fichier."""
    ensure_schema(desk)
    out = {}
    if not desk.settings('registre5693:sources', False):
        out['sources'] = migrer_sources(desk)
        desk.setting('registre5693:sources', True)
    out['controles'] = initialiser_controles(desk, lot)
    out['identites'] = scinder(desk, lot)
    return out


def diagnostic(desk, hidden=frozenset()):
    """Diagnostic local, sans requête au stockage : sources incomplètes ou plafonnées, incidents de contrôle, doublons suggérés, dernière mesure de charge."""
    from . import livrables5689 as liv
    liv.ensure_schema(desk)
    clause, params = '', []
    if hidden:
        clause = ' AND matter_id NOT IN (%s)' % ','.join('?' for _ in hidden)
        params = sorted(hidden)
    incidents = desk.db.execute("SELECT COUNT(*) FROM prepared_items5689 WHERE lifecycle='current' AND check_error<>''" + clause, params).fetchone()[0]
    retires = desk.db.execute("SELECT COUNT(*) FROM prepared_items5689 WHERE lifecycle='current' AND suivi='retire'" + clause, params).fetchone()[0]
    sources = [s for s in liv.etat_sources(desk) if s['state'] in ('partial', 'unavailable', 'stale')]
    # fusions suspectes : une carte portant plusieurs objets d'un même producteur (deux demandes, deux projets…) ; la réparation les examine une à une
    suspects = desk.db.execute("SELECT COUNT(*) FROM (SELECT l.item_id FROM prepared_source_links5689 l JOIN prepared_items5689 i ON i.id=l.item_id WHERE l.link_kind='producteur' AND i.lifecycle='current'" + clause.replace('matter_id', 'i.matter_id') +
                               " GROUP BY l.item_id, l.source_service HAVING COUNT(DISTINCT l.source_object_id)>1)", params).fetchone()[0]
    return {'sources_incompletes': sources, 'fusions_suspectes': suspects, 'reparation': {k: desk.settings('registre5693:' + k, '') for k in ('sources', 'init', 'scission')}, 'plafond_atteint': diagnostic_sources(desk), 'incidents': incidents, 'retires': retires, 'doublons': len(doublons(desk, hidden=hidden)),
            'panne': desk.settings(PANNE_CLE, None) or {}, 'mesure': desk.settings(MESURE_CLE, None) or {}}
