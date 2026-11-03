"""5.6.93 : charge du serveur — surveillance proportionnée, sans perdre une détection.

Relevé du cabinet (3 novembre 2026, 3 heures, serveur qui « ne fait que tourner ») : 403 lots de documents, 190 surveillances de dossier,
93 parcours des dossiers, 59 suites de missions… pour 12 appels d'IA seulement. La charge venait des passages automatiques : lectures
Nextcloud (PROPFIND), IMAP, extraction de texte. Inventaire des travaux automatiques (déclencheur → cadence → coût par passage → suites) :

- ``live_mail430`` : boucle de surveillance (5 min) et IMAP IDLE → SELECT + SEARCH, puis lecture des seuls nouveaux messages → tri,
  ``orchestrator_mail_sweep``. **Jamais espacé ni plafonné ici** : la détection des courriels garde son rythme dans tous les réglages.
- ``live_documents430`` : boucle (lot toutes les 5 min pendant un tour, ``watch430.LOT_DOCUMENTS``), repos entre deux tours → avant :
  un PROPFIND par répertoire de 3 dossiers par lot, même inchangés ; un dossier modifié lance ``index``, ``monitor_matter``,
  ``extract_facts460`` (``Desk.admit``), et chaque pièce nouvelle ``analyze_deadline450`` / ``analyze_notice440`` (une fois par ETag).
- ``index`` : après un changement → relisait toute l'arborescence du dossier (PROPFIND par répertoire), lit et extrait les seuls fichiers
  à ETag nouveau, calcule les empreintes des seuls passages nouveaux → ``refresh_brief`` si le contenu a changé,
  ``refresh_operational_memory`` (autonomie).
- ``monitor_matter`` : changement de documents, événement d'agenda, tour périodique (``monitor_all``) → relisait toute l'arborescence.
- ``sync`` (30 min) : ``discover`` (25 PROPFIND par lot, enchaîné jusqu'au bout de l'arbre, puis tout recommencé au passage suivant),
  lecture des en-têtes des 80 derniers messages de deux dossiers IMAP (environ 480 commandes), indexation tournante de 3 dossiers.
- ``proactive568_cycle`` (toutes les 30 s tant qu'un événement attend), ``sent568_scan`` / ``received568_scan`` (5 min, IMAP),
  ``live_calendar430`` (3 min, un REPORT CalDAV par agenda), ``production_cycle391`` (5 min), ``monitor_all`` (lots de 10 dossiers).

Corrections (interrupteur de secours ``charge5693:actif``, vrai par défaut ; faux = comportement 5.6.92) :

1. **ETag de dossier** : Nextcloud propage l'ETag d'un dossier à chaque changement de sa sous-arborescence. Un lot lit l'ETag de
   tous les dossiers d'un même répertoire parent en un PROPFIND ; un dossier inchangé n'est pas relu. Un dossier modifié n'est relu
   que dans ses sous-répertoires dont l'ETag a changé (cache ``charge5693_arbre``). Relecture complète de sécurité par dossier à un
   intervalle long (24 h, 72 h en réglage minimal). Hors Nextcloud (dossier local, WebDAV générique, stockage cloud), rien ne change.
2. **Regroupement** : un seul travail en attente par type et par dossier (les arguments sont fusionnés vers le plus complet : un tour
   périodique fusionné avec un changement de documents relit Nextcloud) ; ``monitor_matter``, ``discover`` et ``proactive568_cycle``
   attendent une fenêtre après le précédent du même dossier (ou du même titulaire) au lieu d'être relancés aussitôt.
3. **Plafond horaire par type** : au-delà, le travail n'est ni refusé ni perdu : il est mis en file avec un départ différé.
4. **Aucun modèle ni empreinte pour un fichier inchangé** : l'indexation d'un dossier dont l'ETag n'a pas changé depuis la dernière
   indexation complète ne relit rien (une requête) et ne relance pas les synthèses.
5. **Fréquence de surveillance** (normale, économe, minimale) réglable dans Paramètres › Confort et maintenance ; le régime économe
   (``economie569``) impose au moins le réglage économe. La relève des courriels n'est jamais concernée.

Mesures : chaque requête au stockage, commande IMAP, lecture de fichier, extraction et lot d'empreintes est comptée dans le fil courant
(``compter``) puis enregistrée par tranche de 10 minutes et par type de travail (``charge5693_mesures``, conservées 8 jours), sans aucun
contenu de dossier ni nom de fichier.
"""
from datetime import datetime, timedelta, timezone
import json
import sqlite3
import threading
import time

from .common import Stop, clean_path, under

ACTIF = 'charge5693:actif'
FREQUENCE = 'charge5693:frequence'
NIVEAUX = ('normale', 'econome', 'minimale')
LIBELLES = {'normale': 'Normale', 'econome': 'Économe', 'minimale': 'Minimale'}
EXPLICATIONS = {'normale': 'documents des dossiers contrôlés toutes les 30 minutes environ',
                'econome': 'toutes les heures, analyses automatiques regroupées et plafonnées plus bas',
                'minimale': 'toutes les 4 heures ; relecture complète de sécurité tous les 3 jours'}
TOUR_MINUTES = {'normale': 30, 'econome': 60, 'minimale': 240}          # repos entre deux tours complets des documents
RELECTURE_HEURES = {'normale': 24, 'econome': 24, 'minimale': 72}       # relecture complète de sécurité, par dossier
# Minutes minimales entre deux passages d'un contrôle périodique (normale, économe, minimale). Jamais ``live_mail430``.
INTERVALLES = {'live_calendar430': (3, 5, 15), 'sent568_scan': (5, 10, 30), 'received568_scan': (5, 10, 30), 'proactive568_cycle': (2, 5, 15),
               'sync': (30, 60, 180), 'health': (30, 60, 120), 'monitor_all': (30, 120, 360), 'production_cycle391': (5, 15, 30),
               'reconcile_inbox': (15, 60, 120), 'refresh_cabinet_pilotage': (30, 120, 240)}
# Fenêtre de regroupement (minutes) après le précédent travail du même dossier (ou des mêmes arguments sans dossier).
FENETRES = {'monitor_matter': (10, 20, 60), 'discover': (30, 60, 180), 'proactive568_cycle': (2, 5, 15)}
# Plafond par heure en réglage normal ; divisé par 2 (économe) ou par 4 (minimale), jamais moins de 1.
PLAFONDS = {'live_documents430': 30, 'live_calendar430': 30, 'monitor_matter': 60, 'discover': 12, 'proactive568_cycle': 30,
            'orchestrator_mail_sweep': 30, 'monitor_all': 6, 'production_cycle391': 12, 'index': 40, 'extract_facts460': 20,
            'refresh_operational_memory': 20, 'refresh_brief': 20, 'sync_legal_memory': 20, 'analyze_deadline450': 30,
            'analyze_notice440': 30, 'sent568_scan': 12, 'received568_scan': 12, 'sync': 4}
PAR_DOSSIER = frozenset(('monitor_matter', 'index', 'extract_facts460', 'refresh_operational_memory', 'refresh_brief', 'sync_legal_memory'))
RESTRICTIFS = ('tour', 'path')       # arguments qui restreignent un travail : la fusion garde la version la plus complète
JAMAIS = frozenset(('live_mail430', 'run', 'automation_setting'))   # détection des courriels et réglages : jamais regroupés ni plafonnés
BUDGET_DOSSIERS = 20                 # répertoires relus au plus par dossier et par passage (comme ``DAV.inventory_step``)
LOT_MAXIMUM = 3                      # dossiers modifiés relus par lot (inchangé depuis 5.6.62)
REQUETES_LOT = 30                    # lectures d'ETag au plus par lot
MESURES_JOURS = 8
CATEGORIES = (('stockage', 'Requêtes au stockage (Nextcloud)'), ('imap', 'Commandes IMAP'), ('lecture_fichier', 'Fichiers lus'),
              ('extraction', 'Extractions de texte'), ('embeddings', 'Passages envoyés au calcul d’empreintes'))

_fil = threading.local()


def horloge():
    """Heure du contrôleur (secondes) ; les essais la remplacent par une horloge simulée."""
    return time.time()


# ---------------------------------------------------------------- réglage
def actif(desk):
    try:
        return bool(desk.settings(ACTIF, True))
    except (sqlite3.Error, ValueError, TypeError):
        return True


def reglage(desk):
    brut = desk.settings(FREQUENCE, None)
    if isinstance(brut, dict) and brut.get('valeur') in NIVEAUX and isinstance(brut.get('revision'), int):
        return {'valeur': brut['valeur'], 'revision': brut['revision']}
    return {'valeur': 'normale', 'revision': 0}


def econome_impose(desk):
    try:
        from .economie569 import enabled
        return enabled(desk)
    except (sqlite3.Error, ImportError):
        return False


def niveau(desk):
    """Réglage en vigueur : celui de l'avocat, et au moins « économe » si le régime économe est activé (il a ainsi un effet réel)."""
    valeur = reglage(desk)['valeur']
    if econome_impose(desk) and NIVEAUX.index(valeur) < 1:
        return 'econome'
    return valeur


def _rang(desk):
    return NIVEAUX.index(niveau(desk))


def enregistrer_frequence(desk, valeur, revision, auteur):
    """Nouvelle fréquence ; la révision lue par le formulaire doit être la révision actuelle (409 sinon). Même valeur : rien n'est réécrit."""
    if valeur not in NIVEAUX:
        raise Stop('frequence_invalide')
    if isinstance(revision, bool) or not isinstance(revision, int):
        try:
            revision = int(str(revision))
        except (TypeError, ValueError):
            raise Stop('frequence_invalide') from None
    if desk.db.in_transaction:
        desk.db.commit()
    desk.db.execute('BEGIN IMMEDIATE')
    try:
        actuel = reglage(desk)
        if revision != actuel['revision']:
            raise Stop('revision_perimee')
        if valeur == actuel['valeur']:
            desk.db.rollback()
            return etat_reglage(desk)
        desk.db.execute('INSERT OR REPLACE INTO settings VALUES (?,?)', (FREQUENCE, json.dumps({'valeur': valeur, 'revision': revision + 1})))
        desk.db.commit()
    except BaseException:
        if desk.db.in_transaction:
            desk.db.rollback()
        raise
    desk.audit('charge5693_frequence', {'valeur': valeur, 'revision': revision + 1, 'par': str(auteur)[:120]})
    return etat_reglage(desk)


def etat_reglage(desk):
    r = reglage(desk)
    effectif = niveau(desk)
    return {'valeur': r['valeur'], 'revision': r['revision'], 'effectif': effectif, 'econome_impose': effectif != r['valeur'],
            'tour_minutes': TOUR_MINUTES[effectif], 'relecture_heures': RELECTURE_HEURES[effectif],
            'message': 'Surveillance des documents : %s (%s). La relève des courriels garde toujours son rythme.' % (
                LIBELLES[effectif].lower(), EXPLICATIONS[effectif])}


def intervalle_minutes(desk, kind, minutes):
    """Minutes entre deux passages d'un contrôle périodique : jamais moins que la valeur du réglage en vigueur."""
    if kind in JAMAIS or kind not in INTERVALLES or not actif(desk):
        return minutes
    return max(minutes, INTERVALLES[kind][_rang(desk)])


def repos_tour_secondes(desk, secondes):
    """Repos entre deux tours complets des documents (``watch430``)."""
    if not actif(desk):
        return secondes
    return max(int(secondes), TOUR_MINUTES[niveau(desk)] * 60)


def plafond(desk, kind):
    if kind not in PLAFONDS:
        return 0
    return max(1, PLAFONDS[kind] // (1, 2, 4)[_rang(desk)])


# ---------------------------------------------------------------- admission des travaux automatiques
def _instant(texte):
    try:
        valeur = datetime.fromisoformat(str(texte).replace('Z', '+00:00'))
    except (TypeError, ValueError):
        return None
    return valeur if valeur.tzinfo else valeur.replace(tzinfo=timezone.utc)


def _fusion(ancien, nouveau):
    """Arguments fusionnés : une restriction (tour périodique, pièce précise) n'est gardée que si les deux demandes la portent."""
    sortie = dict(ancien)
    for cle in RESTRICTIFS:
        if cle in sortie and sortie.get(cle) != nouveau.get(cle):
            sortie.pop(cle)
    return sortie


def admission(desk, kind, args, origin, chained=False):
    """Appelé par ``Desk.enqueue`` dans sa transaction, après la recherche d'un travail identique. Renvoie (travail existant, départ différé).

    Seuls les travaux automatiques sont concernés ; la détection des courriels et les demandes de l'avocat jamais. Aucun travail n'est refusé :
    un doublon rejoint le travail en attente du même dossier, un travail trop proche du précédent ou au-delà du plafond horaire part plus tard."""
    if origin != 'automatic' or kind in JAMAIS or not actif(desk):
        return None, None
    from .arret5643 import matter_of
    matter = matter_of(args)
    if kind in PAR_DOSSIER and matter:
        # doublon = mêmes arguments à une restriction près (tour périodique, pièce précise) ; d'autres arguments désignent un autre travail
        base = {k: v for k, v in (args or {}).items() if k not in RESTRICTIFS}
        for ligne in desk.db.execute("SELECT id,args FROM jobs WHERE kind=? AND matter_ref=? AND status='pending' ORDER BY id",
                                     (kind, matter)).fetchall():
            try:
                ancien = json.loads(ligne['args'] or '{}')
            except (TypeError, ValueError):
                continue
            if not isinstance(ancien, dict) or {k: v for k, v in ancien.items() if k not in RESTRICTIFS} != base:
                continue
            fusion = _fusion(ancien, args or {})
            if fusion != ancien:
                desk.db.execute("UPDATE jobs SET args=? WHERE id=? AND status='pending'", (json.dumps(fusion, sort_keys=True), ligne['id']))
            return ligne['id'], None
    maintenant = _instant(desk.now()) or datetime.now(timezone.utc)
    depart = None
    if kind in FENETRES and not chained:
        if matter:
            dernier = desk.db.execute("SELECT MAX(finished) FROM jobs WHERE kind=? AND matter_ref=? AND status IN ('done','error')", (kind, matter)).fetchone()[0]
        else:
            dernier = desk.db.execute("SELECT MAX(finished) FROM jobs WHERE kind=? AND args=? AND status IN ('done','error')",
                                      (kind, json.dumps(args or {}, sort_keys=True))).fetchone()[0]
        fin = _instant(dernier) if dernier else None
        fenetre = timedelta(minutes=FENETRES[kind][_rang(desk)])
        if fin and maintenant < fin + fenetre:
            depart = fin + fenetre
    limite = plafond(desk, kind)
    if limite:
        depuis = (maintenant - timedelta(hours=1)).isoformat()
        lignes = desk.db.execute("SELECT created FROM jobs WHERE kind=? AND origin='automatic' AND created>=? ORDER BY created DESC LIMIT ?",
                                 (kind, depuis, limite)).fetchall()
        if len(lignes) >= limite:
            plus_ancien = _instant(lignes[-1][0]) or maintenant
            depart = max(depart or maintenant, plus_ancien + timedelta(hours=1))
    return None, (depart.isoformat() if depart and depart > maintenant else None)


# ---------------------------------------------------------------- mesures
def compter(categorie, n=1):
    """Compteur du fil courant ; aucune écriture ici (la requête mesurée ne doit jamais attendre la base)."""
    compteurs = getattr(_fil, 'compteurs', None)
    if compteurs is None:
        compteurs = _fil.compteurs = {}
    compteurs[categorie] = compteurs.get(categorie, 0) + int(n)


def relever():
    compteurs = getattr(_fil, 'compteurs', None) or {}
    _fil.compteurs = {}
    return compteurs


MESURES = ('CREATE TABLE IF NOT EXISTS charge5693_mesures(tranche TEXT NOT NULL, travail TEXT NOT NULL, categorie TEXT NOT NULL, '
           'n INTEGER NOT NULL DEFAULT 0, PRIMARY KEY(tranche,travail,categorie))')


def _tranche(secondes):
    return datetime.fromtimestamp(secondes, timezone.utc).strftime('%Y-%m-%dT%H:%M')[:15] + '0'


def noter(desk, travail, compteurs=None):
    """Enregistre les compteurs du fil pour ce type de travail. Base occupée : la mesure est perdue, jamais le travail."""
    compteurs = relever() if compteurs is None else compteurs
    compteurs = {k: v for k, v in compteurs.items() if v}
    if not compteurs:
        return
    tranche = _tranche(horloge())
    try:
        if desk.db.in_transaction:
            desk.db.commit()
        desk.db.execute(MESURES)
        for categorie, n in compteurs.items():
            desk.db.execute('INSERT INTO charge5693_mesures VALUES (?,?,?,?) ON CONFLICT(tranche,travail,categorie) DO UPDATE SET n=n+excluded.n',
                            (tranche, str(travail)[:80], str(categorie)[:40], int(n)))
        if desk.settings('charge5693:purge', '') != tranche[:13]:
            desk.db.execute('DELETE FROM charge5693_mesures WHERE tranche<?', (_tranche(horloge() - MESURES_JOURS * 86400),))
            desk.db.execute('INSERT OR REPLACE INTO settings VALUES (?,?)', ('charge5693:purge', json.dumps(tranche[:13])))
        desk.db.commit()
    except sqlite3.Error:
        try:
            desk.db.rollback()
        except sqlite3.Error:
            pass


def debut_travail(desk):
    """Début d'un travail du fil : ce qui a été compté avant (page, planificateur) lui est attribué à part."""
    noter(desk, 'hors_travail')


def fin_travail(desk, kind):
    noter(desk, kind)


# ---------------------------------------------------------------- arborescences des dossiers (ETag)
ARBRE = ('CREATE TABLE IF NOT EXISTS charge5693_arbre(matter TEXT NOT NULL, dossier TEXT NOT NULL, etag TEXT NOT NULL, fichiers TEXT NOT NULL, '
         'dossiers TEXT NOT NULL, lu TEXT NOT NULL DEFAULT \'\', PRIMARY KEY(matter,dossier))')
ETATS = ('CREATE TABLE IF NOT EXISTS charge5693_dossiers(matter TEXT PRIMARY KEY, etag TEXT NOT NULL DEFAULT \'\', regle TEXT NOT NULL DEFAULT \'\', '
         'relu_complet TEXT NOT NULL DEFAULT \'\', index_etag TEXT NOT NULL DEFAULT \'\', securite_debut TEXT NOT NULL DEFAULT \'\')')


def ensure_schema(desk):
    """Caches techniques reconstruits depuis le stockage : ils ne sont ni exportés ni sauvegardés (voir ``oublier``)."""
    if getattr(desk, '_charge5693', False):
        return
    desk.db.execute(ARBRE)
    desk.db.execute(ETATS)
    desk.db.commit()
    desk._charge5693 = True


def mode_etag(desk, client):
    """Surveillance par ETag de dossier : Nextcloud seulement, interrupteur de secours respecté."""
    return bool(getattr(client, 'etag_propage', False)) and hasattr(client, 'folder_etag') and hasattr(client, 'list_folder') and actif(desk)


def _cache(desk, mid, depuis=''):
    """Répertoires connus du dossier ; ``depuis`` : seulement ceux relus depuis cet instant (relecture de sécurité en plusieurs passages)."""
    sortie = {}
    for ligne in desk.db.execute('SELECT dossier,etag,fichiers,dossiers,lu FROM charge5693_arbre WHERE matter=? AND lu>=?', (mid, depuis)):
        try:
            sortie[ligne[0]] = {'etag': ligne[1], 'fichiers': json.loads(ligne[2]), 'dossiers': json.loads(ligne[3]), 'lu': ligne[4]}
        except (TypeError, ValueError):
            continue
    return sortie


def _complet(cache, dossier, vus=None):
    """Sous-arborescence entièrement connue : chaque sous-répertoire est en cache avec l'ETag vu par son parent."""
    vus = set() if vus is None else vus
    entree = cache.get(dossier)
    if entree is None or dossier in vus or len(vus) > 10000:
        return False
    vus.add(dossier)
    return all(dossier in cache and cache.get(sous, {}).get('etag') == etag and _complet(cache, sous, vus) for sous, etag in entree['dossiers'].items())


def _deplier(cache, dossier, sortie=None, fichiers=None):
    sortie = {} if sortie is None else sortie
    fichiers = {} if fichiers is None else fichiers
    entree = cache[dossier]
    sortie[dossier] = entree
    for chemin, meta in entree['fichiers'].items():
        fichiers[chemin] = dict(meta)
    for sous in entree['dossiers']:
        _deplier(cache, sous, sortie, fichiers)
    return sortie, fichiers


def etat_dossier(desk, mid):
    ensure_schema(desk)
    ligne = desk.db.execute('SELECT etag,regle,relu_complet,index_etag,securite_debut FROM charge5693_dossiers WHERE matter=?', (str(mid),)).fetchone()
    return dict(zip(('etag', 'regle', 'relu_complet', 'index_etag', 'securite_debut'), ligne)) if ligne else None


def _maintenant_iso():
    return datetime.fromtimestamp(horloge(), timezone.utc).isoformat()


def relecture_due(desk, mid, etat=None):
    etat = etat_dossier(desk, mid) if etat is None else etat
    fait = _instant(etat['relu_complet']) if etat and etat['relu_complet'] else None
    return fait is None or datetime.fromtimestamp(horloge(), timezone.utc) - fait >= timedelta(hours=RELECTURE_HEURES[niveau(desk)])


def inventaire(desk, dav, matter, etag_racine=None, complet=False, budget=BUDGET_DOSSIERS):
    """Inventaire d'un dossier : (fichiers, complet, statistiques). Une sous-arborescence dont l'ETag n'a pas changé est reprise du cache ;
    ``complet`` relit tout (relecture de sécurité). Les sous-arborescences relues en entier sont gardées au fur et à mesure : un passage
    interrompu par le budget reprend là où il s'est arrêté. Mêmes garde-fous que ``DAV.inventory_step`` (périmètre, profondeur, volume)."""
    ensure_schema(desk)
    mid, racine = str(matter['id']), clean_path(matter['path'])
    stamp = _maintenant_iso()
    if complet:
        # relecture de sécurité : seuls comptent les répertoires relus depuis son début (elle peut demander plusieurs passages)
        etat = etat_dossier(desk, mid)
        debut = (etat or {}).get('securite_debut') or stamp
        if not etat or not etat.get('securite_debut'):
            desk.db.execute("INSERT INTO charge5693_dossiers(matter,securite_debut) VALUES (?,?) ON CONFLICT(matter) DO UPDATE SET securite_debut=excluded.securite_debut",
                            (mid, debut))
        cache = _cache(desk, mid, debut)
    else:
        cache = _cache(desk, mid)
    stats = {'requetes': 0, 'repris': 0, 'complet': bool(complet)}
    if etag_racine is None and not complet:
        etag_racine = dav.folder_etag(racine)
        stats['requetes'] += 1
    if not complet and etag_racine and cache.get(racine, {}).get('etag') == etag_racine and _complet(cache, racine):
        _, fichiers = _deplier(cache, racine)
        stats['repris'] = 1
        return fichiers, True, stats
    profondeur_max = dav.cfg.get('max_depth', 8) if isinstance(getattr(dav, 'cfg', None), dict) else 8
    nouveaux, tous, reste = {}, {}, [budget]

    def visiter(dossier, etag_vu, profondeur):
        entree = cache.get(dossier)
        if entree and etag_vu and entree['etag'] == etag_vu and _complet(cache, dossier):
            sous, fichiers = _deplier(cache, dossier)
            nouveaux.update(sous)             # gardés avec leur date de lecture d'origine
            tous.update(fichiers)
            stats['repris'] += 1
            return True
        if reste[0] <= 0:
            return False
        enfants = dav.list_folder(dossier)
        reste[0] -= 1
        stats['requetes'] += 1
        propre = getattr(dav, 'etags_dossiers', {}).get(dossier) or ''
        fichiers, dossiers = {}, {}
        for item in sorted(enfants, key=lambda x: x['path']):
            if not under(item['path'], racine):
                raise Stop('surveillance_hors_dossier')
            if item['directory']:
                if profondeur >= profondeur_max:
                    raise Stop('dossier_trop_profond')
                dossiers[item['path']] = item.get('etag', '')
            else:
                fichiers[item['path']] = {'etag': item.get('etag', ''), 'modified': item.get('modified', ''), 'created': item.get('created', ''),
                                          'size': item.get('size', 0), 'error': ''}
        tous.update(fichiers)
        if len(tous) > 100000:
            raise Stop('surveillance_limite_securite')
        entier = True
        for sous, etag in dossiers.items():
            entier = visiter(sous, etag, profondeur + 1) and entier
        if entier and propre:                 # un répertoire sans ETag est relu, jamais gardé en cache
            nouveaux[dossier] = {'etag': propre, 'fichiers': fichiers, 'dossiers': dossiers, 'lu': stamp}
        return entier

    entier = visiter(racine, etag_racine, 0)
    if entier:
        desk.db.execute('DELETE FROM charge5693_arbre WHERE matter=?', (mid,))     # répertoires disparus retirés ; le reste est réécrit ci-dessous
    for dossier, entree in nouveaux.items():
        desk.db.execute('INSERT OR REPLACE INTO charge5693_arbre VALUES (?,?,?,?,?,?)', (mid, dossier, entree['etag'], json.dumps(entree['fichiers'], sort_keys=True),
                                                                                         json.dumps(entree['dossiers'], sort_keys=True), entree.get('lu') or stamp))
    desk.db.commit()
    if not entier:
        return {}, False, stats
    return tous, True, stats


def inventaire_step(desk, dav, matter, etag_racine=None, complet=False):
    """Même forme que ``DAV.inventory_step`` : ({'files': …}, complet)."""
    fichiers, entier, _ = inventaire(desk, dav, matter, etag_racine, complet)
    return {'files': fichiers}, entier


def _sale(desk, mid):
    try:
        return desk.db.execute("SELECT 1 FROM live_dirty_v430 WHERE source='documents' AND object_key=?", (mid,)).fetchone() is not None
    except sqlite3.Error:
        return True


def lot_documents(desk, dav, matters, curseur, maximum=LOT_MAXIMUM):
    """Choix d'un lot : les dossiers inchangés depuis leur dernier passage réglé (même ETag, rien « à traiter », relecture de sécurité non
    échue) sont passés sans autre lecture. Les ETag d'un même parent sont lus en un seul PROPFIND. Renvoie (sélection, dossiers parcourus,
    dossiers inchangés) ; chaque élément de la sélection est (dossier, ETag lu ou '', relecture complète)."""
    ensure_schema(desk)
    racines = (getattr(dav, 'cfg', None) or {}).get('roots') or []
    parents = {}
    for m in matters:
        try:
            parent = clean_path(m['path']).rsplit('/', 1)[0] or '/'
        except Stop:
            continue
        parents[parent] = parents.get(parent, 0) + 1
    lus, selection, avance, inchanges, requetes = {}, [], 0, 0, 0
    i = curseur
    while i < len(matters) and len(selection) < maximum:
        matter = matters[i]
        mid = str(matter['id'])
        try:
            racine = clean_path(matter['path'])
        except Stop:
            racine = ''
        parent = racine.rsplit('/', 1)[0] or '/'
        etag = None
        if racine and parent != racine and parents.get(parent, 0) >= 2 and any(under(parent, r) for r in racines):
            if parent not in lus:
                if requetes >= REQUETES_LOT:
                    break
                try:
                    lus[parent] = {x['path']: x.get('etag', '') for x in dav.list_folder(parent) if x.get('directory')}
                except Stop:
                    lus[parent] = {}
                requetes += 1
            etag = lus[parent].get(racine)
        if etag is None and racine:
            if requetes >= REQUETES_LOT:
                break
            try:
                etag = dav.folder_etag(racine)
            except Stop:
                etag = ''                       # le lot relira le dossier et signalera l'erreur comme avant
            requetes += 1
        i += 1
        avance += 1
        etat = etat_dossier(desk, mid)
        securite = relecture_due(desk, mid, etat)
        if etag and etat and etat['etag'] == etag and not securite and not _sale(desk, mid):
            inchanges += 1
            continue
        selection.append((matter, etag or None, securite))
    return selection, avance, inchanges


def regler(desk, mid, etag, complet=False):
    """Dossier traité avec cet ETag : le prochain tour le passera s'il n'a pas changé. Jamais si une analyse reste « à traiter »."""
    if not etag or _sale(desk, mid):
        return False
    ensure_schema(desk)
    stamp = _maintenant_iso()
    desk.db.execute('INSERT INTO charge5693_dossiers(matter,etag,regle,relu_complet) VALUES (?,?,?,?) ON CONFLICT(matter) DO UPDATE SET '
                    'etag=excluded.etag,regle=excluded.regle,relu_complet=CASE WHEN ? THEN excluded.relu_complet ELSE charge5693_dossiers.relu_complet END,'
                    "securite_debut=CASE WHEN ? THEN '' ELSE charge5693_dossiers.securite_debut END",
                    (str(mid), etag, stamp, stamp if complet else '', 1 if complet else 0, 1 if complet else 0))
    desk.db.commit()
    return True


def oublier(desk, mid=None):
    """Efface le cache (un dossier ou tous) : le prochain passage relit tout. Utilisé après une erreur de lecture."""
    ensure_schema(desk)
    if mid is None:
        desk.db.execute('DELETE FROM charge5693_arbre')
        desk.db.execute('DELETE FROM charge5693_dossiers')
    else:
        desk.db.execute('DELETE FROM charge5693_arbre WHERE matter=?', (str(mid),))
        desk.db.execute('DELETE FROM charge5693_dossiers WHERE matter=?', (str(mid),))
    desk.db.commit()


# ---------------------------------------------------------------- indexation
def index_inchange(desk, dav, matter, index_db):
    """(dossier inchangé depuis sa dernière indexation complète, ETag lu avant la lecture). Une indexation en cours n'est jamais sautée."""
    if not mode_etag(desk, dav):
        return False, ''
    mid = str(matter['id'])
    try:
        etag = dav.folder_etag(clean_path(matter['path']))
    except Stop:
        return False, ''
    if index_db.execute('SELECT 1 FROM inventory_scans WHERE matter=?', (mid,)).fetchone():
        return False, etag
    etat = etat_dossier(desk, mid)
    return bool(etag and etat and etat['index_etag'] == etag), etag


def noter_index(desk, mid, etag):
    if not etag:
        return
    ensure_schema(desk)
    desk.db.execute("INSERT INTO charge5693_dossiers(matter,index_etag) VALUES (?,?) ON CONFLICT(matter) DO UPDATE SET index_etag=excluded.index_etag",
                    (str(mid), etag))
    desk.db.commit()


def resultat_index_inchange(index, mid):
    """Résultat d'une indexation sautée : mêmes clés qu'une indexation ordinaire, rien de modifié."""
    fichiers = index.db.execute('SELECT COUNT(*) FROM docs WHERE matter=?', (mid,)).fetchone()[0]
    return {'fichiers': fichiers, 'en_attente': 0, 'contenu_modifie': False, 'inchange': True,
            'inventaire': {'complete': True, 'next_cursor': None, 'seen': fichiers, 'page_files': 0, 'changed': 0}}


# ---------------------------------------------------------------- parcours des dossiers (discover)
DECOUVERTE = 'charge5693:decouverte'


def decouverte_inchangee(desk, dav, racines):
    """Nouveau parcours : si aucune racine n'a changé depuis le dernier parcours complet (et la relecture de sécurité n'est pas échue),
    aucun répertoire n'est relu. Renvoie (inchangé, ETag des racines)."""
    if not mode_etag(desk, dav):
        return False, {}
    etags = {}
    for racine in racines:
        try:
            etags[racine] = dav.folder_etag(racine)
        except Stop:
            return False, {}
    connu = desk.settings(DECOUVERTE, {}) or {}
    fait = _instant(connu.get('complet')) if isinstance(connu, dict) and connu.get('complet') else None
    frais = fait is not None and datetime.fromtimestamp(horloge(), timezone.utc) - fait < timedelta(hours=RELECTURE_HEURES[niveau(desk)])
    return bool(frais and all(etags.values()) and connu.get('racines') == etags), etags


def decouverte_precedente(desk, dav):
    """ETag des sous-répertoires (hors dossiers) vus au dernier parcours complet ; vide hors Nextcloud ou après la relecture de sécurité."""
    if not mode_etag(desk, dav):
        return {}
    connu = desk.settings(DECOUVERTE, {}) or {}
    fait = _instant(connu.get('complet')) if isinstance(connu, dict) and connu.get('complet') else None
    if fait is None or datetime.fromtimestamp(horloge(), timezone.utc) - fait >= timedelta(hours=RELECTURE_HEURES[niveau(desk)]):
        return {}
    return connu.get('repertoires') or {}


def decouverte_terminee(desk, racines, repertoires):
    desk.setting(DECOUVERTE, {'racines': racines, 'repertoires': dict(list(repertoires.items())[:50000]),
                              'complet': datetime.fromtimestamp(horloge(), timezone.utc).isoformat()})


# ---------------------------------------------------------------- relevé des correspondants (sync)
def courriels_a_lire(desk, dossier, validite, uids, empreinte):
    """Relevé des correspondants : seuls les messages plus récents que le dernier relevé sont relus. Tout est relu si la boîte a changé
    (UIDVALIDITY), si la liste des dossiers a changé, ou une fois par jour."""
    if not actif(desk):
        return uids
    connu = (desk.settings('charge5693:releve_courriels', {}) or {}).get(dossier) or {}
    fait = _instant(connu.get('complet')) if connu.get('complet') else None
    if (connu.get('validite') != str(validite) or connu.get('empreinte') != empreinte or fait is None
            or datetime.fromtimestamp(horloge(), timezone.utc) - fait >= timedelta(hours=24)):
        return uids
    dernier = int(connu.get('dernier') or 0)
    return [u for u in uids if str(u).isdigit() and int(u) > dernier]


def courriels_lus(desk, dossier, validite, uids, empreinte, tout):
    if not actif(desk):
        return
    valeur = desk.settings('charge5693:releve_courriels', {}) or {}
    connu = valeur.get(dossier) or {}
    numeros = [int(u) for u in uids if str(u).isdigit()]
    valeur[dossier] = {'validite': str(validite), 'empreinte': empreinte, 'dernier': max(numeros + [int(connu.get('dernier') or 0) if not tout else 0]),
                       'complet': datetime.fromtimestamp(horloge(), timezone.utc).isoformat() if tout else connu.get('complet', '')}
    desk.setting('charge5693:releve_courriels', valeur)


# ---------------------------------------------------------------- état affiché
def etat(desk, now=None):
    """Données de la page « Charge du serveur » : types de travaux, compteurs, dernier tour, travaux en cours. Aucun contenu de dossier."""
    now = now or datetime.fromtimestamp(horloge(), timezone.utc)
    heure, jour = (now - timedelta(hours=1)).isoformat(), (now - timedelta(hours=24)).isoformat()
    travaux = {}
    for kind, origine, h1, h24 in desk.db.execute(
            "SELECT kind,origin,SUM(CASE WHEN created>=? THEN 1 ELSE 0 END),COUNT(*) FROM jobs WHERE created>=? GROUP BY kind,origin", (heure, jour)):
        ligne = travaux.setdefault(kind, {'kind': kind, 'heure': 0, 'jour': 0, 'automatiques_jour': 0})
        ligne['heure'] += int(h1 or 0)
        ligne['jour'] += int(h24 or 0)
        if origine == 'automatic':
            ligne['automatiques_jour'] += int(h24 or 0)
    mesures = {k: {'heure': 0, 'jour': 0} for k, _ in CATEGORIES}
    par_travail = {}
    try:
        desk.db.execute(MESURES)
        t_heure, t_jour = _tranche(now.timestamp() - 3600), _tranche(now.timestamp() - 86400)
        for tranche, travail, categorie, n in desk.db.execute('SELECT tranche,travail,categorie,n FROM charge5693_mesures WHERE tranche>=?', (t_jour,)):
            if categorie not in mesures:
                continue
            mesures[categorie]['jour'] += n
            if tranche >= t_heure:
                mesures[categorie]['heure'] += n
            if categorie == 'stockage':
                par_travail[travail] = par_travail.get(travail, 0) + n
    except sqlite3.Error:
        mesures = None                                   # mesure indisponible : jamais un faux zéro
    modeles = _appels_modele(desk, heure, jour)
    en_cours = [{'kind': r[0], 'depuis': r[1] or ''} for r in desk.db.execute("SELECT kind,started FROM jobs WHERE status='running' ORDER BY started LIMIT 20")]
    attente = [{'kind': r[0], 'n': r[1], 'differes': r[2] or 0, 'prochain': r[3] or ''} for r in desk.db.execute(
        "SELECT kind,COUNT(*),SUM(CASE WHEN not_before>? THEN 1 ELSE 0 END),MIN(CASE WHEN not_before>? THEN not_before END) FROM jobs "
        "WHERE status='pending' GROUP BY kind ORDER BY COUNT(*) DESC LIMIT 30", (now.isoformat(), now.isoformat()))]
    ensure_schema(desk)
    fin_tour = desk.settings('live430:documents_sweep_done', 0) or 0
    curseur = int(desk.settings('live430:document_cursor', 0) or 0)
    relu = desk.db.execute("SELECT COUNT(*),MIN(NULLIF(relu_complet,'')) FROM charge5693_dossiers").fetchone()
    return {'travaux': sorted(travaux.values(), key=lambda x: (-x['heure'], -x['jour'], x['kind'])), 'mesures': mesures,
            'stockage_par_travail': sorted(par_travail.items(), key=lambda x: -x[1])[:12], 'modeles': modeles,
            'en_cours': en_cours, 'attente': attente, 'tour_en_cours': curseur != 0,
            'dernier_tour': datetime.fromtimestamp(float(fin_tour), timezone.utc).isoformat() if float(fin_tour or 0) > 0 else '',
            'dossiers_suivis': int(relu[0] or 0), 'plus_ancienne_relecture': relu[1] or '', 'reglage': etat_reglage(desk), 'actif': actif(desk)}


def _appels_modele(desk, heure, jour):
    try:
        from .ai_gateway import _usage_db
        db = _usage_db(desk.c['state_dir'])
    except Exception:   # noqa: BLE001 — registre des appels absent : non mesuré, jamais zéro
        return None
    try:
        ligne = db.execute('SELECT SUM(CASE WHEN at>=? THEN 1 ELSE 0 END),COUNT(*) FROM ai_usage_v391 WHERE at>=?', (heure, jour)).fetchone()
        return {'heure': int(ligne[0] or 0), 'jour': int(ligne[1] or 0)}
    except sqlite3.Error:
        return None
    finally:
        db.close()
