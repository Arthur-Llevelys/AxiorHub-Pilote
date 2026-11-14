"""5.6.97 (lot gestion, § 21) : clôture, archivage, réouverture et restauration vérifiée.

« Préparer la clôture » laisse le dossier actif et liste les points bloquants, avec leurs liens : échéances ouvertes, tâches en cours,
travail à facturer, factures non réglées, pièces reçues à vérifier, dernière information au client, partages à révoquer. Les contrôles sont
**recalculés sur le serveur** au moment de clôturer (``cloturer``), avec la version du dossier attendue : l'écran n'en est qu'un reflet.
Certains points se lèvent par une décision motivée (travail non facturé, pièce reçue, information du client) ; une facture non réglée se
lève en gardant un suivi financier (responsable et prochaine date) ; une échéance critique ouverte, une tâche ouverte, un partage actif ou
une révocation non confirmée bloquent toujours.

Clôturer enregistre décision, date, auteur, motif et synthèse des contrôles, archive l'affaire et met l'IA en pause sur ce dossier
(``pause5629`` : ses automatismes de production sont suspendus) ; droits et historique restent. Une nouvelle entrée sur un dossier archivé
est signalée au responsable sans le rouvrir (``signaler_entree``). Rouvrir est réservé à un valideur, motivé et tracé ; cela ne recrée
aucun partage, aucun envoi ni aucun rendez-vous, et ne relance aucun automatisme (la pause de l'IA se lève à part, depuis la fiche).
La suppression et la conservation relèvent d'une politique distincte de l'archivage : rien n'est supprimé ici.

Restauration : ``paquet_restauration`` construit, dans le format de ``export5688`` (manifeste, tailles, empreintes SHA-256), un paquet
du seul dossier : données de toutes les tables du dossier (liste fermée ``export5688.TABLES``, secrets retirés), droits, fichiers et
versions lus dans un état cohérent, paquets gelés et accusés, originaux archivés, et la liste explicite de ce qui n'est que **référencé**
(événements d'agenda distants, partages, messages non archivés). ``verifier_restauration`` le recharge dans une **instance isolée**
(dossier temporaire, aucune connexion, aucun travail lancé), contrôle les empreintes, les droits et chaque lien vers un fichier, puis y
teste la réouverture. Un fichier absent ou un lien cassé fait échouer la vérification.
"""
from contextlib import contextmanager
from datetime import datetime, timedelta, timezone
import base64
import hashlib
import json
from pathlib import Path, PurePosixPath
import shutil
import sqlite3
from urllib.parse import urlencode

from .common import Stop, load_matters, private_json
from . import gestion5697 as g

LEVABLES = ('travail_a_facturer', 'pieces_a_verifier', 'information_client')
LIBELLES = {'echeances': 'Échéances ouvertes', 'taches': 'Tâches en cours', 'actes': 'Actes et dépôts en cours', 'travail_a_facturer': 'Travail à facturer',
            'factures_non_reglees': 'Factures non réglées', 'pieces_a_verifier': 'Pièces reçues à vérifier', 'information_client': 'Dernière information au client',
            'partages': 'Partages à révoquer'}
DOSSIER_RESTAURATION = 'restaurations5697'
RETENTION_HEURES = 24
CONSERVES = 3
MAX_FICHIER = 100 * 1024 * 1024
MAX_TOTAL = 2 * 1024 ** 3


def _etat(desk, mid):
    """État de l'affaire. Migrée au registre unique (lot dossiers) : statut et version viennent de ce registre ; ce module ne garde que les
    métadonnées de clôture (pause posée, suivi financier). Pas encore migrée : état local du lot."""
    g.schema(desk)
    row = desk.db.execute('SELECT * FROM dossiers_etat5697 WHERE matter=?', (str(mid),)).fetchone()
    e = dict(row) if row else {'matter': str(mid), 'etat': 'actif', 'version': 1, 'pause_posee': 0, 'suivi_financier': '{}'}
    unique = g.registre_unique(desk, mid)
    e['registre'] = 'unique' if unique else 'local'
    if unique:
        e['etat'], e['version'] = ('archive' if unique['statut'] == 'archive' else 'actif'), unique['version']
    return e


def _commande_registre(desk, mid, cle):
    """Vrai si le registre unique a déjà exécuté le changement de statut de cette clé (réponse perdue, coupure avant notre trace)."""
    try:
        return bool(desk.db.execute('SELECT 1 FROM dossier_commandes5697 WHERE operation=? AND cle=?', ('modifier:' + str(mid), cle)).fetchone())
    except Exception:   # noqa: BLE001 — registre unique absent : aucune reprise possible, le chemin ordinaire s'applique
        return False


def _statut_registre(desk, acteur, mid, e, statut, cle):
    """Statut porté au registre unique par son propre service (version attendue, clé d'idempotence) : aucun second statut.
    5.6.100 (R10) : appelé DANS la transaction de la clôture (``dossiers5697.transition_cloture``) ; la projection JSON du registre
    est une suite durable (``_terminer_suites``)."""
    from . import dossiers5697
    try:
        dossiers5697.transition_cloture(desk, acteur, str(mid), e['version'], statut, cle)
    except Stop as ex:
        if str(ex).split(':')[0] == 'version_perimee':
            raise Stop('version_en_conflit') from None
        raise
    return g.registre_unique(desk, mid)['version']


# ------------------------------------------------------------------------------------------------ 5.6.100 (R10) : une transaction, des suites durables
@contextmanager
def _transaction_unique(desk):
    """Contrôles relus, transition, état local, décision et journal dans UNE transaction (BEGIN IMMEDIATE : aucune autre écriture ne
    s'intercale entre le contrôle et la transition). Une validation implicite qui couperait la transaction est un défaut signalé
    (``transaction_interrompue``), jamais ignoré."""
    if desk.db.in_transaction:
        desk.db.commit()
    desk.db.execute('BEGIN IMMEDIATE')
    try:
        yield
        if not desk.db.in_transaction:
            raise Stop('transaction_interrompue')
    except BaseException:
        if desk.db.in_transaction:
            desk.db.rollback()
        raise
    desk.db.commit()


class _SansValidation:
    """Connexion vue par les contrôles relus dans la transaction : les schémas ont été préparés juste avant (``_preparer_schemas``),
    donc ``executescript`` (qui validerait la transaction en cours) et ``commit`` sont sans effet ; tout le reste est la vraie connexion."""
    def __init__(self, db):
        self._db = db

    def executescript(self, _sql):
        return None

    def commit(self):
        return None

    def __getattr__(self, nom):
        return getattr(self._db, nom)


@contextmanager
def _lecture_dans_transaction(desk):
    vraie = desk.db
    desk.db = _SansValidation(vraie)
    try:
        yield
    finally:
        desk.db = vraie


def _preparer_schemas(desk):
    """Tout ce que les contrôles et la transition peuvent créer est créé AVANT la transaction (``executescript`` valide implicitement)."""
    from . import dossiers5697, echeances450, honoraires5697, parcours5657
    from .cabinet_profile5689 import cabinet_id
    g.schema(desk)
    parcours5657.ensure_schema(desk)
    echeances450.ensure_schema(desk)
    honoraires5697._schemas(desk)   # noqa: SLF001 — mêmes tables que le contrôle des honoraires
    dossiers5697.ensure_schema(desk)
    cabinet_id(desk)
    desk.db.commit()


def _decision_de_cloture(desk, mid):
    """Vrai si la dernière décision tracée sur le dossier est une clôture (ou sa régularisation)."""
    row = desk.db.execute('SELECT decision FROM clotures5697 WHERE matter=? ORDER BY at DESC, rowid DESC LIMIT 1', (str(mid),)).fetchone()
    return bool(row) and row['decision'] in ('cloture', 'regularisation')


def _suite(desk, cloture_id, etape, mid):
    desk.db.execute('INSERT OR IGNORE INTO cloture_suites56100(cloture_id,etape,matter,etat,created,updated) VALUES(?,?,?,?,?,?)',
                    (cloture_id, etape, str(mid), 'a_faire', g.maintenant(), g.maintenant()))


def _terminer_suites(desk, acteur, mid):
    """Suites d'une décision déjà validée (intention durable écrite dans la même transaction) : projection du registre puis pause de l'IA.
    Rejouées après une coupure ou une réponse perdue, chacune n'a lieu qu'une fois ; une pause n'est jamais posée sur un dossier rouvert."""
    from . import pause5629
    for s in desk.db.execute("SELECT * FROM cloture_suites56100 WHERE matter=? AND etat='a_faire' ORDER BY created, etape", (str(mid),)).fetchall():
        etat_final = 'fait'
        try:
            if s['etape'] == 'projection':
                from . import dossiers5697
                if g.registre_unique(desk, mid):
                    dossiers5697.projeter(desk, str(mid))
            elif s['etape'] == 'pause':
                if _etat(desk, mid)['etat'] != 'archive':
                    etat_final = 'annulee'
                elif not pause5629.is_paused(desk, mid):
                    pause5629.set_pause(desk, str(mid), True, g.compte(acteur))   # automatismes de production suspendus ; rien n'est supprimé
        except Exception as ex:   # noqa: BLE001 — la suite reste « à faire » ; l'erreur est notée puis remontée (aucun faux succès)
            desk.db.execute('UPDATE cloture_suites56100 SET essais=essais+1,erreur=?,updated=? WHERE cloture_id=? AND etape=?',
                            (str(ex)[:300], g.maintenant(), s['cloture_id'], s['etape']))
            desk.db.commit()
            raise
        desk.db.execute("UPDATE cloture_suites56100 SET etat=?,essais=essais+1,erreur='',updated=? WHERE cloture_id=? AND etape=? AND etat='a_faire'",
                        (etat_final, g.maintenant(), s['cloture_id'], s['etape']))
        desk.db.commit()


def reprendre_suites(desk):
    """Maintenance : termine les suites restées « à faire » (projection, pause) de tous les dossiers, sans rien décider de nouveau."""
    g.schema(desk)
    faits = 0
    for (mid,) in desk.db.execute("SELECT DISTINCT matter FROM cloture_suites56100 WHERE etat='a_faire'").fetchall():
        from .acces5697 import AGENT
        _terminer_suites(desk, AGENT, mid)
        faits += 1
    return faits


def est_archive(desk, mid):
    return _etat(desk, mid)['etat'] == 'archive'


def etat(desk, acteur, mid):
    g.dossier(desk, acteur, mid)
    e = _etat(desk, mid)
    e['suivi_financier'] = json.loads(e.get('suivi_financier') or '{}')
    e['historique'] = [dict(r) for r in desk.db.execute('SELECT id,decision,version_dossier,motif,auteur,at,synthese FROM clotures5697 WHERE matter=? ORDER BY at DESC', (str(mid),))]
    e['entrees_signalees'] = [dict(r) for r in desk.db.execute('SELECT nature,ref,responsable,at FROM entrees_archivees5697 WHERE matter=? ORDER BY at DESC LIMIT 50', (str(mid),))]
    return e


# ------------------------------------------------------------------------------------------------ contrôles (serveur)
def _ligne(type_, ident, libelle, lien=''):
    return {'type': type_, 'id': str(ident), 'libelle': str(libelle)[:200], 'lien': lien}


def _table(desk, nom):
    return bool(desk.db.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name=?", (nom,)).fetchone())


# 5.6.100 (R10) : chaque point de la fiche de clôture mène à l'endroit où il se règle (onglet et section de la fiche du dossier).
OU_REGLER = {'echeances': ('faits', 'g97-proc-t'), 'actes': ('faits', 'g97-proc-t'), 'taches': ('agenda', 'd97-agenda-t'),
             'travail_a_facturer': ('gestion', 'g97-hon-t'), 'pieces_a_verifier': ('echanges', 'g97-pd-t'), 'information_client': ('echanges', 'g97-rc-t'),
             'partages': ('echanges', 'g97-rc-t')}


def lien_point(mid, cle):
    """Adresse (sans préfixe) où se règle le point ``cle`` du dossier ``mid`` ; les factures se suivent dans Facturation."""
    if cle == 'factures_non_reglees':
        return '/facturation?' + urlencode({'vue': 'documents', 'q': str(mid)})
    vue, ancre = OU_REGLER.get(cle, ('gestion', ''))
    return '/dossier?' + urlencode({'id': str(mid), 'vue': vue}) + ('#' + ancre if ancre else '')


def controles(desk, acteur, mid, decisions=None, suivi_financier=None):
    """Points de clôture recalculés. ``decisions`` : motifs saisis pour les points levables ; ``suivi_financier`` : responsable et date.
    5.6.100 : chaque point et chaque élément portent le lien vers l'écran où ils se règlent."""
    from . import echeances450, honoraires5697, remises5697
    g.dossier(desk, acteur, mid)
    g.schema(desk)
    echeances450.ensure_schema(desk)
    decisions = {k: g.texte(v, 400) for k, v in (decisions or {}).items() if k in LEVABLES and len(g.texte(v, 400)) >= 3}
    suivi = suivi_financier if isinstance(suivi_financier, dict) else json.loads(_etat(desk, mid).get('suivi_financier') or '{}')
    out = []

    def ajouter(cle, etat_, elements, action, levee=''):
        lien = lien_point(mid, cle)
        for x in elements:
            x['lien'] = x.get('lien') or lien
        out.append({'id': cle, 'libelle': LIBELLES[cle], 'etat': etat_, 'elements': elements, 'action': action, 'levee_par': levee, 'lien': lien})

    critiques, autres = [], []
    for r in desk.db.execute("SELECT d.id,d.due,d.status,d.rule_id,COALESCE(s.critique,1) AS critique FROM deadlines450 d LEFT JOIN echeance_suivi5697 s ON s.deadline_id=d.id "
                             "WHERE d.matter=? AND d.status NOT IN ('terminee','annulee')", (str(mid),)):
        (critiques if r['critique'] else autres).append(_ligne('echeance', r['id'], '%s — %s (%s)' % (r['rule_id'], r['due'] or 'date à vérifier', r['status'])))
    ajouter('echeances', 'bloquant' if critiques else ('avertissement' if autres else 'ok'), critiques + autres,
            'Renseigner l’accomplissement prouvé ou un autre état justifié dans le registre.')
    taches = [_ligne('tache', r['id'], r['title'] or 'Tâche') for r in desk.db.execute("SELECT id,title FROM tasks WHERE matter=? AND status IN ('open','todo')", (str(mid),))]
    ajouter('taches', 'bloquant' if taches else 'ok', taches, 'Terminer ou réaffecter les tâches.')
    actes = [_ligne('depot', r['id'], 'Paquet gelé sans dépôt déclaré (%s)' % r['created'][:10]) for r in desk.db.execute(
        "SELECT id,created FROM depots5697 WHERE matter=? AND etat='gele'", (str(mid),))]
    actes += [_ligne('depot', r['id'], 'Dépôt déclaré sans accusé rapproché (%s)' % r['date_depot']) for r in desk.db.execute(
        "SELECT id,date_depot FROM depots5697 WHERE matter=? AND etat='depot_declare'", (str(mid),))]
    ajouter('actes', 'avertissement' if actes else 'ok', actes, 'Déclarer le dépôt et rapprocher l’accusé, ou constater qu’il n’aura pas lieu.')
    # Honoraires : lus sous la garde du dossier ; le contrôle voit tout le dossier, quel que soit le droit d'affichage de l'acteur.
    elements = honoraires5697.a_facturer(desk, str(mid))
    travail = [_ligne(k, x['id'], x.get('label') or x.get('libelle') or k) for k in ('temps', 'frais', 'jalons') for x in elements[k]]
    lev = decisions.get('travail_a_facturer', '')
    ajouter('travail_a_facturer', 'ok' if not travail else ('leve' if lev else 'bloquant'), travail, 'Préparer le solde final ou décider de ne pas facturer (motif).', lev)
    factures = [_ligne('facture', f['id'], '%s : reste %s' % (f['numero'], honoraires5697.euros(f['solde_cents'])))
                for f in honoraires5697._factures(desk, [str(mid)]) + honoraires5697._factures_externes(desk, [str(mid)]) if f['solde_cents'] > 0]   # noqa: SLF001
    suivi_ok = bool(g.texte(suivi.get('responsable'), 160)) and bool(str(suivi.get('prochaine_date') or ''))
    ajouter('factures_non_reglees', 'ok' if not factures else ('leve' if suivi_ok else 'bloquant'), factures,
            'Garder un responsable et une prochaine date de suivi : le solde est conservé après la clôture.', 'suivi financier' if factures and suivi_ok else '')
    pieces = [_ligne('reception', r['id'], r['path'] or r['ref']) for r in desk.db.execute(
        "SELECT r.id,r.path,r.ref FROM pieces_receptions5697 r JOIN p57_request_items i ON i.request=r.request AND i.item=r.item WHERE r.matter=? AND r.etat='a_verifier' "
        "AND i.state='recue'", (str(mid),))] if _table(desk, 'p57_request_items') else []
    lev = decisions.get('pieces_a_verifier', '')
    ajouter('pieces_a_verifier', 'ok' if not pieces else ('leve' if lev else 'bloquant'), pieces, 'Accepter, faire remplacer ou dispenser chaque pièce reçue.', lev)
    informe = desk.db.execute("SELECT COUNT(*) FROM remises5697 WHERE matter=? AND etat IN ('transmise','acces_constate','acces_revoque','expire')", (str(mid),)).fetchone()[0]
    lev = decisions.get('information_client', '')
    ajouter('information_client', 'ok' if informe else ('leve' if lev else 'bloquant'), [],
            'Remettre le dernier compte rendu au client (transmission déclarée), ou motiver l’absence d’information.', lev)
    partages = [_ligne('remise', r['id'], ('Révocation non confirmée : ' if r['etat'] == 'revocation_demandee' else 'Partage actif : ') + r['destinataire'])
                for r in desk.db.execute("SELECT id,etat,destinataire FROM remises5697 WHERE matter=? AND share_id<>'' AND etat IN (%s)" % ','.join('?' * len(remises5697.ACTIFS)),
                                         (str(mid), *remises5697.ACTIFS))]
    ajouter('partages', 'bloquant' if partages else 'ok', partages, 'Révoquer les accès qui doivent cesser et vérifier la révocation.')
    return out


def preparer(desk, acteur, mid):
    """« Préparer la clôture » : rien ne change ; le dossier reste actif pendant le contrôle."""
    from . import parcours5657
    parcours5657.ensure_schema(desk)
    liste = controles(desk, acteur, mid)
    e = _etat(desk, mid)
    return {'dossier': str(mid), 'etat': e['etat'], 'version': e['version'], 'controles': liste, 'bloquant': any(c['etat'] == 'bloquant' for c in liste)}


def _refus_si_bloque(liste):
    bloquants = [c for c in liste if c['etat'] == 'bloquant']
    if bloquants:
        ex = Stop('cloture_bloquee')
        ex.detail = [{'id': c['id'], 'libelle': c['libelle'], 'elements': c['elements'], 'lien': c.get('lien', '')} for c in bloquants]
        raise ex


def _synthese(liste):
    return json.dumps([{k: c[k] for k in ('id', 'etat', 'levee_par')} | {'nombre': len(c['elements'])} for c in liste], ensure_ascii=False)


def _donnees_cloture(data):
    motif = g.texte(data.get('motif'), 500)
    if len(motif) < 3:
        raise Stop('motif_obligatoire')
    suivi = data.get('suivi_financier') if isinstance(data.get('suivi_financier'), dict) else {}
    if suivi:
        suivi = {'responsable': g.texte(suivi.get('responsable'), 160), 'prochaine_date': g.jour(suivi.get('prochaine_date'), False)}
    decisions = data.get('decisions') if isinstance(data.get('decisions'), dict) else {}
    return motif, suivi, decisions


def cloturer(desk, acteur, mid, data):
    """POST …/cloture : version attendue, contrôles recalculés ici, décision tracée, archivage, automatismes suspendus. Idempotent par clé.

    5.6.100 (R10) : seul chemin vers l'archivage (la modification générique du registre refuse ``statut``). Les contrôles sont RELUS dans
    la transaction qui porte la transition (BEGIN IMMEDIATE : rien ne s'intercale) ; statut du registre unique, état local, décision et
    journal sont validés ensemble ou pas du tout. Projection du registre et pause de l'IA sont des suites durables écrites dans la même
    transaction et terminées ensuite ; un rejeu de la même clé (réponse perdue, coupure) les termine une seule fois."""
    from . import pause5629
    g.dossier(desk, acteur, mid)
    g.valideur(acteur)
    g.droit(desk, acteur, 'valider')
    _preparer_schemas(desk)
    cle = g.cle_de(data.get('cle'))
    old = desk.db.execute('SELECT * FROM clotures5697 WHERE cle=?', (cle,)).fetchone()
    if old:
        if old['matter'] != str(mid) or old['decision'] != 'cloture':
            raise Stop('cle_idempotence_deja_utilisee')
        _terminer_suites(desk, acteur, mid)
        return etat(desk, acteur, mid)
    motif, suivi, decisions = _donnees_cloture(data)
    e = _etat(desk, mid)
    reprise = e['etat'] == 'archive' and _commande_registre(desk, mid, cle)   # coupure d'une version antérieure après le registre unique
    if not reprise:
        g.version_attendue(e, data.get('version'), 'version')
        if e['etat'] == 'archive':
            raise Stop('dossier_deja_archive')
    _refus_si_bloque(controles(desk, acteur, mid, decisions, suivi))   # refus rapide, sans verrou ; refait ci-dessous sous verrou
    deja_pause = pause5629.is_paused(desk, mid)
    ident = g.nouvel_id('clo')
    with _transaction_unique(desk):
        with _lecture_dans_transaction(desk):
            e = _etat(desk, mid)
            reprise = e['etat'] == 'archive' and _commande_registre(desk, mid, cle)
            if not reprise:
                g.version_attendue(e, data.get('version'), 'version')
                if e['etat'] == 'archive':
                    raise Stop('dossier_deja_archive')
            liste = controles(desk, acteur, mid, decisions, suivi)
        _refus_si_bloque(liste)
        if reprise:
            nouvelle = e['version']
        else:
            nouvelle = _statut_registre(desk, acteur, mid, e, 'archive', cle) if e['registre'] == 'unique' else e['version'] + 1
        n = desk.db.execute('INSERT INTO dossiers_etat5697(matter,etat,version,pause_posee,suivi_financier,updated) VALUES(?,?,?,?,?,?) ON CONFLICT(matter) DO UPDATE SET '
                            "etat='archive',version=excluded.version,pause_posee=excluded.pause_posee,suivi_financier=excluded.suivi_financier,updated=excluded.updated "
                            "WHERE dossiers_etat5697.version=? OR ?='unique'", (str(mid), 'archive', nouvelle, 0 if deja_pause else 1, json.dumps(suivi, ensure_ascii=False),
                                                                             g.maintenant(), e['version'], e['registre'])).rowcount
        if not n:
            raise Stop('version_en_conflit')
        desk.db.execute('INSERT INTO clotures5697(id,parametres,matter,cle,decision,version_dossier,controles,synthese,motif,auteur,at) VALUES(?,?,?,?,?,?,?,?,?,?,?)',
                        (ident, '', str(mid), cle, 'cloture', e['version'], json.dumps(liste, ensure_ascii=False), _synthese(liste), motif, g.compte(acteur), g.maintenant()))
        g.journal(desk, mid, 'dossier', mid, 'cloture', acteur, motif, {'etat': e['etat'], 'version': e['version']}, {'etat': 'archive'})
        if e['registre'] == 'unique':
            _suite(desk, ident, 'projection', mid)
        if not deja_pause:
            _suite(desk, ident, 'pause', mid)
    _terminer_suites(desk, acteur, mid)
    return etat(desk, acteur, mid)


def regulariser(desk, acteur, mid, data):
    """5.6.100 (R10) : dossier archivé SANS décision de clôture (avant la 5.6.100 : menu « Archiver », import d'un état « archivé ») :
    le responsable enregistre la décision motivée de clôture, avec les mêmes contrôles relus sous verrou (une échéance critique encore
    ouverte bloque). Rien n'est rouvert ni annulé : le statut ne change pas. L'autre correction possible est la réouverture motivée."""
    from . import pause5629
    g.dossier(desk, acteur, mid)
    g.valideur(acteur)
    g.droit(desk, acteur, 'valider')
    _preparer_schemas(desk)
    cle = g.cle_de(data.get('cle'))
    old = desk.db.execute('SELECT * FROM clotures5697 WHERE cle=?', (cle,)).fetchone()
    if old:
        if old['matter'] != str(mid) or old['decision'] != 'regularisation':
            raise Stop('cle_idempotence_deja_utilisee')
        _terminer_suites(desk, acteur, mid)
        return etat(desk, acteur, mid)
    motif, suivi, decisions = _donnees_cloture(data)
    deja_pause = pause5629.is_paused(desk, mid)
    ident = g.nouvel_id('clo')
    with _transaction_unique(desk):
        with _lecture_dans_transaction(desk):
            e = _etat(desk, mid)
            if e['etat'] != 'archive':
                raise Stop('dossier_non_archive')
            if _decision_de_cloture(desk, mid):
                raise Stop('cloture_deja_enregistree')
            g.version_attendue(e, data.get('version'), 'version')
            liste = controles(desk, acteur, mid, decisions, suivi)
        _refus_si_bloque(liste)
        desk.db.execute('INSERT INTO dossiers_etat5697(matter,etat,version,pause_posee,suivi_financier,updated) VALUES(?,?,?,?,?,?) ON CONFLICT(matter) DO UPDATE SET '
                        'pause_posee=excluded.pause_posee,suivi_financier=excluded.suivi_financier,updated=excluded.updated',
                        (str(mid), 'archive', e['version'], 0 if deja_pause else 1, json.dumps(suivi, ensure_ascii=False), g.maintenant()))
        desk.db.execute('INSERT INTO clotures5697(id,parametres,matter,cle,decision,version_dossier,controles,synthese,motif,auteur,at) VALUES(?,?,?,?,?,?,?,?,?,?,?)',
                        (ident, '', str(mid), cle, 'regularisation', e['version'], json.dumps(liste, ensure_ascii=False), _synthese(liste), motif, g.compte(acteur),
                         g.maintenant()))
        g.journal(desk, mid, 'dossier', mid, 'cloture_regularisee', acteur, motif, {'etat': 'archive', 'decision': 'aucune'}, {'etat': 'archive', 'decision': 'cloture'})
        if not deja_pause:
            _suite(desk, ident, 'pause', mid)
    _terminer_suites(desk, acteur, mid)
    out = etat(desk, acteur, mid)
    out['message'] = 'Décision de clôture enregistrée. Le dossier reste archivé ; rien n’a été rouvert ni annulé.'
    return out


def rouvrir(desk, acteur, mid, data, isole=False):
    """Réouverture autorisée, motivée, tracée. Aucun partage, envoi ni rendez-vous recréé ; la pause de l'IA reste (levée à part).
    5.6.100 (R10) : transition, état local, décision et journal dans une transaction ; une pause de clôture encore « à faire » est annulée."""
    g.dossier(desk, acteur, mid)
    g.valideur(acteur)
    if not isole:
        g.droit(desk, acteur, 'valider')
    _preparer_schemas(desk)
    cle = g.cle_de(data.get('cle'))
    old = desk.db.execute('SELECT * FROM clotures5697 WHERE cle=?', (cle,)).fetchone()
    if old:
        if old['matter'] != str(mid) or old['decision'] != 'reouverture':
            raise Stop('cle_idempotence_deja_utilisee')
        _terminer_suites(desk, acteur, mid)
        return etat(desk, acteur, mid)
    motif = g.texte(data.get('motif'), 500)
    if len(motif) < 3:
        raise Stop('motif_obligatoire')
    ident = g.nouvel_id('clo')
    with _transaction_unique(desk):
        e = _etat(desk, mid)
        reprise = e['etat'] == 'actif' and _commande_registre(desk, mid, cle)
        if not reprise:
            if e['etat'] != 'archive':
                raise Stop('dossier_non_archive')
            g.version_attendue(e, data.get('version'), 'version')
        if reprise:
            nouvelle = e['version']
        else:
            nouvelle = _statut_registre(desk, acteur, mid, e, 'actif', cle) if e['registre'] == 'unique' else e['version'] + 1
        n = desk.db.execute("INSERT INTO dossiers_etat5697(matter,etat,version,updated) VALUES(?,'actif',?,?) ON CONFLICT(matter) DO UPDATE SET etat='actif',"
                            "version=excluded.version,updated=excluded.updated WHERE dossiers_etat5697.version=? OR ?='unique'",
                            (str(mid), nouvelle, g.maintenant(), e['version'], e['registre'])).rowcount
        if not n:
            raise Stop('version_en_conflit')
        desk.db.execute('INSERT INTO clotures5697(id,parametres,matter,cle,decision,version_dossier,controles,synthese,motif,auteur,at) VALUES(?,?,?,?,?,?,?,?,?,?,?)',
                        (ident, '', str(mid), cle, 'reouverture', e['version'], '[]', '', motif, g.compte(acteur), g.maintenant()))
        g.journal(desk, mid, 'dossier', mid, 'reouverture', acteur, motif, {'etat': 'archive'}, {'etat': 'actif'})
        desk.db.execute("UPDATE cloture_suites56100 SET etat='annulee',updated=? WHERE matter=? AND etape='pause' AND etat='a_faire'", (g.maintenant(), str(mid)))
        if e['registre'] == 'unique':
            _suite(desk, ident, 'projection', mid)
    _terminer_suites(desk, acteur, mid)
    out = etat(desk, acteur, mid)
    out['message'] = 'Dossier rouvert. Aucun partage, envoi ni rendez-vous n’a été recréé.' + (
        ' L’IA reste en pause sur ce dossier : levez-la depuis la fiche si vous le souhaitez.' if e['pause_posee'] else '')
    return out


# ------------------------------------------------------------------------------------------------ 5.6.100 (R10) : rapprochement, liste Archivés
def _archives(desk):
    """Identifiants archivés : registre unique (statut) et état local des dossiers historiques. Lecture seule, sans création de table."""
    ids = set()
    if _table(desk, 'dossiers5697'):
        ids |= {r[0] for r in desk.db.execute("SELECT id FROM dossiers5697 WHERE statut='archive'")}
    if _table(desk, 'dossiers_etat5697'):
        unique = {r[0] for r in desk.db.execute('SELECT id FROM dossiers5697')} if _table(desk, 'dossiers5697') else set()
        ids |= {r[0] for r in desk.db.execute("SELECT matter FROM dossiers_etat5697 WHERE etat='archive'") if r[0] not in unique}
    return ids


def alertes_archives(desk, ids):
    """Pour la liste « Archivés » : ``{dossier: {'echeances_critiques': n, 'sans_decision': bool}}`` (seulement les dossiers à signaler).
    Lecture seule : aucune table créée, aucune écriture (R05) ; une table absente ne signale rien."""
    ids = [str(i) for i in ids if i]
    out = {}
    if not ids:
        return out
    marques = ','.join('?' * len(ids))
    if _table(desk, 'deadlines450'):
        jointure = ' LEFT JOIN echeance_suivi5697 s ON s.deadline_id=d.id' if _table(desk, 'echeance_suivi5697') else ''
        critique = 'COALESCE(s.critique,1)=1' if jointure else '1=1'
        for mid, n in desk.db.execute("SELECT d.matter,COUNT(*) FROM deadlines450 d%s WHERE d.matter IN (%s) AND d.status NOT IN ('terminee','annulee') AND %s "
                                      'GROUP BY d.matter' % (jointure, marques, critique), ids):
            out.setdefault(mid, {'echeances_critiques': 0, 'sans_decision': False})['echeances_critiques'] = n
    if _table(desk, 'clotures5697'):
        dernieres = {}
        for mid, decision in desk.db.execute('SELECT matter,decision FROM clotures5697 WHERE matter IN (%s) ORDER BY at, rowid' % marques, ids):
            dernieres[mid] = decision
        for mid in ids:
            if dernieres.get(mid) not in ('cloture', 'regularisation'):
                out.setdefault(mid, {'echeances_critiques': 0, 'sans_decision': False})['sans_decision'] = True
    else:
        for mid in ids:
            out.setdefault(mid, {'echeances_critiques': 0, 'sans_decision': False})['sans_decision'] = True
    return out


def rapprochement_clotures(desk, acteur):
    """État de rapprochement : dossiers archivés sans décision de clôture, ou archivés avec une échéance critique encore ouverte, avec les
    points encore ouverts et le lien vers la fiche de clôture. Rien n'est rouvert, annulé ni corrigé automatiquement : le responsable
    choisit entre la régularisation motivée (``regulariser``) et la réouverture motivée (``rouvrir``)."""
    from . import acces5697
    masques = acces5697.masques(desk, acteur)
    archives = sorted(i for i in _archives(desk) if i not in masques)
    alertes = alertes_archives(desk, archives)
    registre = {str(m['id']): m for m in load_matters(desk.c, unfiltered=True)}
    lignes = []
    for mid in archives:
        a = alertes.get(mid)
        if not a:
            continue
        try:
            liste = controles(desk, acteur, mid)
        except Stop:
            continue
        unique = g.registre_unique(desk, mid) or {}
        m = registre.get(mid, {})
        lignes.append({'dossier': mid, 'intitule': str(m.get('client_name') or mid)[:160], 'responsable': unique.get('responsable') or m.get('responsable') or '',
                       'sans_decision': a['sans_decision'], 'echeances_critiques': a['echeances_critiques'],
                       'points_ouverts': [{'id': c['id'], 'libelle': c['libelle'], 'etat': c['etat'], 'nombre': len(c['elements']), 'lien': c['lien']}
                                          for c in liste if c['etat'] in ('bloquant', 'avertissement')],
                       'lien': '/dossier?' + urlencode({'id': mid, 'vue': 'cloture'}),
                       'proposition': 'Régulariser la clôture (décision motivée) ou rouvrir le dossier (motif) : aucune correction automatique.'})
    return lignes


def signaler_entree(desk, mid, nature, ref):
    """Nouvelle entrée (courriel, document, échéance) sur un dossier archivé : signalée au responsable, sans rouvrir. Vrai si signalée."""
    g.schema(desk)
    if not est_archive(desk, mid):
        return False
    m = next((x for x in load_matters(desk.c, unfiltered=True) if str(x['id']) == str(mid)), {}) or {}
    responsable = str(m.get('responsable') or m.get('owner') or '')
    desk.db.execute('INSERT OR IGNORE INTO entrees_archivees5697(id,matter,nature,ref,responsable,at) VALUES(?,?,?,?,?,?)',
                    (g.nouvel_id('ent'), str(mid), str(nature)[:40], str(ref)[:300], responsable, g.maintenant()))
    desk.db.commit()
    return True


# ------------------------------------------------------------------------------------------------ restauration
def _racine_paquets(desk):
    p = Path(desk.c['state_dir']) / DOSSIER_RESTAURATION
    p.mkdir(mode=0o700, exist_ok=True)
    return p


def purger(desk, maintenant=None):
    """Rétention : un paquet de restauration vit 24 heures au plus ; trois au plus sont gardés."""
    racine = _racine_paquets(desk)
    limite = (maintenant or datetime.now(timezone.utc)) - timedelta(hours=RETENTION_HEURES)
    fichiers = sorted(racine.glob('*.zip'), key=lambda p: p.stat().st_mtime, reverse=True)
    retires = 0
    for rang, f in enumerate(fichiers):
        if rang >= CONSERVES or datetime.fromtimestamp(f.stat().st_mtime, timezone.utc) < limite:
            f.unlink(missing_ok=True)
            retires += 1
    return retires


def _tables_du_dossier():
    from . import export5688
    return [(d, n, t, f) for d, n, t, base, f in export5688.TABLES if base == export5688.DESK and f is not None]


def _valeur(net, v):
    if isinstance(v, (bytes, memoryview)):
        return {'base64': base64.b64encode(bytes(v)).decode()}
    if isinstance(v, str):
        return net.texte(v)
    return v


def _lignes(db, net, table, filtre, mid):
    from . import export5688
    colonnes = [r[1] for r in db.execute('PRAGMA table_info("%s")' % table)]
    if not colonnes:
        return None, [], ''
    gardees = [c for c in colonnes if not export5688._secrete(c)]     # noqa: SLF001 — même règle que l'export intégral
    liste = ','.join('"%s"' % c for c in gardees)
    if isinstance(filtre, str):
        rows = db.execute('SELECT %s FROM "%s" WHERE "%s"=?' % (liste, table, filtre), (mid,))
    else:
        col, parent, cle, pcol = filtre
        if not db.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name=?", (parent,)).fetchone():
            return gardees, [], db.execute("SELECT sql FROM sqlite_master WHERE type='table' AND name=?", (table,)).fetchone()[0]
        rows = db.execute('SELECT %s FROM "%s" WHERE "%s" IN (SELECT "%s" FROM "%s" WHERE "%s"=?)' % (liste, table, col, cle, parent, pcol), (mid,))
    lignes = [{c: _valeur(net, v) for c, v in zip(gardees, r)} for r in rows]
    ddl = db.execute("SELECT sql FROM sqlite_master WHERE type='table' AND name=?", (table,)).fetchone()[0]
    return gardees, lignes, ddl


def _sain(path):
    from .export5688 import _sain as sain
    return '/'.join(sain(p) for p in PurePosixPath(path).parts if p not in ('/', '..', '.'))


def paquet_restauration(desk, acteur, mid, client=None):
    """Paquet du seul dossier, au format de l'export intégral. Réservé à l'administrateur ; fichier temporaire à rétention de 24 heures."""
    from . import export5688, membres5650, pause5629, __version__
    from .office440 import dav_client
    m = g.dossier(desk, acteur, mid)
    if g.acteur_de(acteur).role != 'administrateur':
        raise Stop('role_insuffisant')
    g.schema(desk)
    purger(desk)
    chemin = _racine_paquets(desk) / ('%s.zip' % g.nouvel_id('rst'))
    net = export5688.Nettoyage()
    paquet = export5688.Paquet(chemin)
    references, fichiers_dossier, manquants = [], [], []
    try:
        db = sqlite3.connect(Path(desk.c['state_dir'], export5688.DESK).resolve().as_uri() + '?mode=ro', uri=True, timeout=30)
        try:
            db.execute('BEGIN')                              # un seul instant pour tout le registre
            for domaine, _nom, table, filtre in _tables_du_dossier():
                colonnes, lignes, ddl = _lignes(db, net, table, filtre, str(mid))
                if colonnes is None:
                    continue
                with paquet.fichier('donnees/%s.json' % table, domaine=domaine) as sortie:
                    sortie.write(json.dumps({'table': table, 'colonnes': colonnes, 'ddl': ddl, 'lignes': lignes}, ensure_ascii=False, default=str).encode('utf-8'))
            for table, sql, nature, mention in REFERENCES:
                if db.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name=?", (table,)).fetchone():
                    references += [{'nature': nature, 'ref': ident, 'mention': mention} for (ident,) in db.execute(sql, (str(mid),))]
            db.rollback()
        finally:
            db.close()
        with paquet.fichier('dossiers/registre.json', domaine='dossiers') as sortie:
            sortie.write(json.dumps([m], ensure_ascii=False, default=str).encode('utf-8'))
        reserve = membres5650.load(desk.c['state_dir'])['reserves'].get(str(mid))
        with paquet.fichier('droits/reserves.json', domaine='droits') as sortie:
            sortie.write(json.dumps({'dossier': str(mid), 'reserve_a': reserve or []}, ensure_ascii=False).encode('utf-8'))
        client = client or dav_client(desk)
        racine = str(m.get('path') or '').rstrip('/')
        liste = client.inventory(racine) if racine else []
        avant = {f['path']: f.get('etag', '') for f in liste}
        total = 0
        for f in liste:
            pause5629.checkpoint(desk)
            if int(f.get('size') or 0) > MAX_FICHIER or total > MAX_TOTAL:
                manquants.append({'path': f['path'], 'motif': 'trop volumineux pour le paquet'})
                continue
            try:
                raw = client.download(f)
            except Stop as ex:
                manquants.append({'path': f['path'], 'motif': 'illisible : ' + str(ex).split(':')[0]})
                continue
            total += len(raw)
            nom = 'fichiers/' + _sain(f['path'][len(racine):])
            with paquet.fichier(nom, domaine='fichiers') as sortie:
                sortie.write(raw)
            fichiers_dossier.append({'path': f['path'], 'etag': f.get('etag', ''), 'sha256': hashlib.sha256(raw).hexdigest(), 'dans_paquet': nom})
        apres = {f['path']: f.get('etag', '') for f in (client.inventory(racine) if racine else [])}
        if apres != avant:
            raise Stop('fichiers_modifies_pendant_la_capture')   # état incohérent : le paquet est à refaire
        with paquet.fichier('fichiers/inventaire.json', domaine='fichiers') as sortie:
            sortie.write(json.dumps({'racine': racine, 'fichiers': fichiers_dossier, 'manquants': manquants}, ensure_ascii=False).encode('utf-8'))
        meta = {'format': export5688.FORMAT, 'cree': g.maintenant(), 'version': __version__, 'portee': 'dossier', 'dossier': str(mid), 'documents_inclus': True,
                'dossiers_reserves': {'traitement': 'dossier_seul', 'nombre': 1 if reserve else 0}, 'secrets_retires': net.retires,
                'references_seulement': references, 'non_inclus': manquants}
        with paquet.zip.open(export5688._info('MANIFEST.json'), 'w', force_zip64=True) as sortie:   # noqa: SLF001
            sortie.write(json.dumps({**meta, 'fichiers': paquet.fichiers}, ensure_ascii=False, indent=1).encode('utf-8'))
    except BaseException:
        paquet.close()
        chemin.unlink(missing_ok=True)
        raise
    paquet.close()
    with g.transaction(desk):
        g.journal(desk, mid, 'dossier', mid, 'paquet_restauration', acteur, '', {}, {'fichiers': len(fichiers_dossier), 'references': len(references), 'non_inclus': len(manquants)})
    return {'chemin': str(chemin), 'sha256': hashlib.sha256(chemin.read_bytes()).hexdigest(), 'references_seulement': references, 'non_inclus': manquants}


class InstanceIsolee:
    """Instance minimale pour la vérification : une base et un dossier temporaires, aucune connexion, aucun travail, aucun réglage partagé."""

    def __init__(self, cible, matters, roots):
        self.cible = Path(cible)
        (self.cible / 'state').mkdir(parents=True, exist_ok=True)
        private_json(self.cible / 'matters.json', matters)
        self.c = {'state_dir': str(self.cible / 'state'), 'matters_file': str(self.cible / 'matters.json'), 'nextcloud': {'roots': roots}}
        self.db = sqlite3.connect(str(self.cible / 'state' / 'desk.sqlite3'))
        self.db.row_factory = sqlite3.Row
        self.db.execute('CREATE TABLE IF NOT EXISTS settings(key TEXT PRIMARY KEY, value TEXT)')

    def now(self):
        return g.maintenant()

    def settings(self, key, default=None):
        row = self.db.execute('SELECT value FROM settings WHERE key=?', (key,)).fetchone()
        return json.loads(row[0]) if row else default

    def setting(self, key, value):
        self.db.execute('INSERT OR REPLACE INTO settings VALUES(?,?)', (key, json.dumps(value)))
        self.db.commit()

    def audit(self, action, data):
        return None

    def close(self):
        self.db.close()


REFERENCES = (('deadlines450', "SELECT id FROM deadlines450 WHERE matter=? AND calendar_uid<>''", 'evenement_agenda', 'événement d’agenda distant : référence seulement'),
              ('remises5697', "SELECT id FROM remises5697 WHERE matter=? AND share_id<>''", 'partage_nextcloud', 'partage distant : référence seulement, jamais recréé'),
              ('pieces_receptions5697', "SELECT ref FROM pieces_receptions5697 WHERE matter=? AND origine='courriel'", 'courriel',
               'message de la messagerie : référence seulement (original non archivé dans le dossier)'),
              ('message_dossier5697', "SELECT message_ref FROM message_dossier5697 WHERE dossier_id=?", 'courriel',
               'message classé dans le dossier : référence seulement, sauf original conservé dans le dossier'))
CHEMINS = (('deadlines450', 'source_path'), ('contentieux_exhibits5689', 'path'), ('contentieux_actes5689', 'path'), ('pieces_receptions5697', 'path'),
           ('p57_request_items', 'document'), ('registre_docs5688', 'path'))


def verifier_restauration(chemin_zip, cible, mid, droits_attendus=None):
    """Recharge le paquet dans une instance isolée et contrôle : manifeste, empreintes, droits, liens vers les fichiers, réouverture."""
    import zipfile
    from . import export5688
    controle = export5688.verifier_manifeste(chemin_zip)
    anomalies = [{'objet': a['fichier'], 'motif': a['motif']} for a in controle['anomalies']]
    cible = Path(cible)
    if cible.exists() and any(cible.iterdir()):
        raise Stop('instance_isolee_non_vide')
    restaures, liens = {}, 0
    with zipfile.ZipFile(chemin_zip) as z:
        manifeste = json.loads(z.read('MANIFEST.json'))
        if manifeste.get('portee') != 'dossier' or manifeste.get('dossier') != str(mid):
            raise Stop('paquet_d_un_autre_dossier')
        registre = json.loads(z.read('dossiers/registre.json'))
        racine = str(registre[0].get('path') or '').rstrip('/')
        inst = InstanceIsolee(cible, registre, [racine or '/'])
        try:
            noms = set(z.namelist())
            for nom in sorted(n for n in noms if n.startswith('donnees/') and n.endswith('.json')):
                t = json.loads(z.read(nom))
                inst.db.executescript(t['ddl'] + ';')
                for ligne in t['lignes']:
                    valeurs = [base64.b64decode(v['base64']) if isinstance(v, dict) and set(v) == {'base64'} else v for v in (ligne[c] for c in t['colonnes'])]
                    inst.db.execute('INSERT INTO "%s"(%s) VALUES(%s)' % (t['table'], ','.join('"%s"' % c for c in t['colonnes']), ','.join('?' * len(valeurs))), valeurs)
            inst.db.commit()
            inventaire = json.loads(z.read('fichiers/inventaire.json'))
            for f in inventaire['fichiers']:
                if f['dans_paquet'] not in noms:
                    anomalies.append({'objet': f['path'], 'motif': 'fichier absent du paquet'})
                    continue
                raw = z.read(f['dans_paquet'])
                if hashlib.sha256(raw).hexdigest() != f['sha256']:
                    anomalies.append({'objet': f['path'], 'motif': 'empreinte différente'})
                    continue
                dest = inst.cible / 'fichiers' / f['dans_paquet'][len('fichiers/'):]
                dest.parent.mkdir(parents=True, exist_ok=True)
                dest.write_bytes(raw)
                restaures[f['path']] = f['sha256']
            for m in inventaire['manquants']:
                anomalies.append({'objet': m['path'], 'motif': 'non inclus : ' + m['motif']})
            droits = json.loads(z.read('droits/reserves.json'))
            if droits_attendus is not None and sorted(droits['reserve_a']) != sorted(droits_attendus):
                anomalies.append({'objet': 'droits', 'motif': 'réserve du dossier différente de l’original'})
            for table, colonne in CHEMINS:
                if not _table(inst, table):
                    continue
                for (valeur,) in inst.db.execute('SELECT "%s" FROM "%s"' % (colonne, table)):
                    valeur = str(valeur or '')
                    if not racine or not valeur.startswith(racine + '/'):
                        continue                              # une source qui n'est pas un fichier du dossier (texte, référence) n'est pas un lien
                    liens += 1
                    if valeur not in restaures:
                        anomalies.append({'objet': '%s.%s' % (table, colonne), 'motif': 'lien cassé : %s' % valeur})
            if _table(inst, 'depot_fichiers5697'):
                for r in inst.db.execute('SELECT depot_id,rang,sha256,contenu FROM depot_fichiers5697'):
                    liens += 1
                    if hashlib.sha256(bytes(r['contenu'])).hexdigest() != r['sha256']:
                        anomalies.append({'objet': 'paquet %s/%s' % (r['depot_id'], r['rang']), 'motif': 'octets gelés altérés'})
            if _table(inst, 'depot_accuses5697'):
                for r in inst.db.execute('SELECT id,sha256,contenu FROM depot_accuses5697'):
                    liens += 1
                    if hashlib.sha256(bytes(r['contenu'])).hexdigest() != r['sha256']:
                        anomalies.append({'objet': 'accusé %s' % r['id'], 'motif': 'accusé altéré'})
            if _table(inst, 'remises5697'):
                for r in inst.db.execute('SELECT id,fichiers FROM remises5697'):
                    for f in json.loads(r['fichiers'] or '[]'):
                        if f.get('copie'):
                            liens += 1
                            if restaures.get(f['copie']) != f['sha256']:
                                anomalies.append({'objet': 'remise %s' % r['id'], 'motif': 'version remise absente ou différente'})
            reouverture = _essai_reouverture(inst, str(mid))
            if reouverture != 'ok':
                anomalies.append({'objet': 'dossier', 'motif': reouverture})
        finally:
            inst.close()
    return {'ok': not anomalies, 'anomalies': anomalies, 'fichiers_restaures': len(restaures), 'liens_verifies': liens,
            'references_seulement': manifeste.get('references_seulement', []), 'manifeste': {'fichiers': controle['fichiers'], 'verifies': controle['verifies']}}


def _essai_reouverture(inst, mid):
    """Dans l'instance isolée : un dossier archivé se rouvre (sans aucun effet externe), un dossier actif se lit."""
    acteur = ('verification-restauration', 'administrateur')
    try:
        e = _etat(inst, mid)
        if e['etat'] == 'archive':
            rouvrir(inst, acteur, mid, {'cle': 'restauration-essai-%s' % mid[:40], 'version': e['version'], 'motif': 'Essai de réouverture après restauration'}, isole=True)
            if _etat(inst, mid)['etat'] != 'actif':
                return 'réouverture impossible dans l’instance isolée'
        else:
            g.dossier(inst, acteur, mid)
        return 'ok'
    except Stop as ex:
        return 'réouverture impossible : %s' % str(ex).split(':')[0]


def nettoyer_instance(cible):
    shutil.rmtree(cible, ignore_errors=True)
