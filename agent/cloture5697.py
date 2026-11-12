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
from datetime import datetime, timedelta, timezone
import base64
import hashlib
import json
from pathlib import Path, PurePosixPath
import shutil
import sqlite3

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
    """Statut porté au registre unique par son propre service (version attendue, clé d'idempotence) : aucun second statut."""
    from . import dossiers5697
    try:
        dossiers5697.modifier(desk, acteur, str(mid), e['version'], {'statut': statut}, cle)
    except Stop as ex:
        if str(ex).split(':')[0] == 'version_perimee':
            raise Stop('version_en_conflit') from None
        raise
    return g.registre_unique(desk, mid)['version']


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


def controles(desk, acteur, mid, decisions=None, suivi_financier=None):
    """Points de clôture recalculés. ``decisions`` : motifs saisis pour les points levables ; ``suivi_financier`` : responsable et date."""
    from . import echeances450, honoraires5697, remises5697
    g.dossier(desk, acteur, mid)
    g.schema(desk)
    echeances450.ensure_schema(desk)
    decisions = {k: g.texte(v, 400) for k, v in (decisions or {}).items() if k in LEVABLES and len(g.texte(v, 400)) >= 3}
    suivi = suivi_financier if isinstance(suivi_financier, dict) else json.loads(_etat(desk, mid).get('suivi_financier') or '{}')
    out = []

    def ajouter(cle, etat_, elements, action, levee=''):
        out.append({'id': cle, 'libelle': LIBELLES[cle], 'etat': etat_, 'elements': elements, 'action': action, 'levee_par': levee})

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


def cloturer(desk, acteur, mid, data):
    """POST …/cloture : version attendue, contrôles recalculés ici, décision tracée, archivage, automatismes suspendus. Idempotent par clé."""
    from . import parcours5657, pause5629
    g.dossier(desk, acteur, mid)
    g.valideur(acteur)
    g.droit(desk, acteur, 'valider')
    g.schema(desk)
    parcours5657.ensure_schema(desk)
    cle = g.cle_de(data.get('cle'))
    old = desk.db.execute('SELECT * FROM clotures5697 WHERE cle=?', (cle,)).fetchone()
    if old:
        if old['matter'] != str(mid) or old['decision'] != 'cloture':
            raise Stop('cle_idempotence_deja_utilisee')
        return etat(desk, acteur, mid)
    e = _etat(desk, mid)
    reprise = e['etat'] == 'archive' and _commande_registre(desk, mid, cle)   # coupure après le registre unique : on termine, sans rien refaire
    if not reprise:
        g.version_attendue(e, data.get('version'), 'version')
        if e['etat'] == 'archive':
            raise Stop('dossier_deja_archive')
    motif = g.texte(data.get('motif'), 500)
    if len(motif) < 3:
        raise Stop('motif_obligatoire')
    suivi = data.get('suivi_financier') if isinstance(data.get('suivi_financier'), dict) else {}
    if suivi:
        suivi = {'responsable': g.texte(suivi.get('responsable'), 160), 'prochaine_date': g.jour(suivi.get('prochaine_date'), False)}
    liste = controles(desk, acteur, mid, data.get('decisions') if isinstance(data.get('decisions'), dict) else {}, suivi)
    bloquants = [c for c in liste if c['etat'] == 'bloquant']
    if bloquants:
        ex = Stop('cloture_bloquee')
        ex.detail = [{'id': c['id'], 'libelle': c['libelle'], 'elements': c['elements']} for c in bloquants]
        raise ex
    synthese = json.dumps([{k: c[k] for k in ('id', 'etat', 'levee_par')} | {'nombre': len(c['elements'])} for c in liste], ensure_ascii=False)
    deja_pause = pause5629.is_paused(desk, mid)
    if reprise:
        nouvelle = e['version']
    else:
        nouvelle = _statut_registre(desk, acteur, mid, e, 'archive', cle) if e['registre'] == 'unique' else e['version'] + 1
    with g.transaction(desk):
        n = desk.db.execute('INSERT INTO dossiers_etat5697(matter,etat,version,pause_posee,suivi_financier,updated) VALUES(?,?,?,?,?,?) ON CONFLICT(matter) DO UPDATE SET '
                            "etat='archive',version=excluded.version,pause_posee=excluded.pause_posee,suivi_financier=excluded.suivi_financier,updated=excluded.updated "
                            "WHERE dossiers_etat5697.version=? OR ?='unique'", (str(mid), 'archive', nouvelle, 0 if deja_pause else 1, json.dumps(suivi, ensure_ascii=False),
                                                                             g.maintenant(), e['version'], e['registre'])).rowcount
        if not n:
            raise Stop('version_en_conflit')
        desk.db.execute('INSERT INTO clotures5697(id,parametres,matter,cle,decision,version_dossier,controles,synthese,motif,auteur,at) VALUES(?,?,?,?,?,?,?,?,?,?,?)',
                        (g.nouvel_id('clo'), '', str(mid), cle, 'cloture', e['version'], json.dumps(liste, ensure_ascii=False), synthese, motif, g.compte(acteur), g.maintenant()))
        g.journal(desk, mid, 'dossier', mid, 'cloture', acteur, motif, {'etat': e['etat'], 'version': e['version']}, {'etat': 'archive'})
    if not deja_pause:
        pause5629.set_pause(desk, str(mid), True, g.compte(acteur))   # automatismes de production suspendus ; rien n'est supprimé
    return etat(desk, acteur, mid)


def rouvrir(desk, acteur, mid, data, isole=False):
    """Réouverture autorisée, motivée, tracée. Aucun partage, envoi ni rendez-vous recréé ; la pause de l'IA reste (levée à part)."""
    g.dossier(desk, acteur, mid)
    g.valideur(acteur)
    if not isole:
        g.droit(desk, acteur, 'valider')
    g.schema(desk)
    cle = g.cle_de(data.get('cle'))
    old = desk.db.execute('SELECT * FROM clotures5697 WHERE cle=?', (cle,)).fetchone()
    if old:
        if old['matter'] != str(mid) or old['decision'] != 'reouverture':
            raise Stop('cle_idempotence_deja_utilisee')
        return etat(desk, acteur, mid)
    e = _etat(desk, mid)
    reprise = e['etat'] == 'actif' and _commande_registre(desk, mid, cle)
    if not reprise:
        if e['etat'] != 'archive':
            raise Stop('dossier_non_archive')
        g.version_attendue(e, data.get('version'), 'version')
    motif = g.texte(data.get('motif'), 500)
    if len(motif) < 3:
        raise Stop('motif_obligatoire')
    if reprise:
        nouvelle = e['version']
    else:
        nouvelle = _statut_registre(desk, acteur, mid, e, 'actif', cle) if e['registre'] == 'unique' else e['version'] + 1
    with g.transaction(desk):
        n = desk.db.execute("INSERT INTO dossiers_etat5697(matter,etat,version,updated) VALUES(?,'actif',?,?) ON CONFLICT(matter) DO UPDATE SET etat='actif',"
                            "version=excluded.version,updated=excluded.updated WHERE dossiers_etat5697.version=? OR ?='unique'",
                            (str(mid), nouvelle, g.maintenant(), e['version'], e['registre'])).rowcount
        if not n:
            raise Stop('version_en_conflit')
        desk.db.execute('INSERT INTO clotures5697(id,parametres,matter,cle,decision,version_dossier,controles,synthese,motif,auteur,at) VALUES(?,?,?,?,?,?,?,?,?,?,?)',
                        (g.nouvel_id('clo'), '', str(mid), cle, 'reouverture', e['version'], '[]', '', motif, g.compte(acteur), g.maintenant()))
        g.journal(desk, mid, 'dossier', mid, 'reouverture', acteur, motif, {'etat': 'archive'}, {'etat': 'actif'})
    out = etat(desk, acteur, mid)
    out['message'] = 'Dossier rouvert. Aucun partage, envoi ni rendez-vous n’a été recréé.' + (
        ' L’IA reste en pause sur ce dossier : levez-la depuis la fiche si vous le souhaitez.' if e['pause_posee'] else '')
    return out


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
