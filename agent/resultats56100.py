"""5.6.100 (lot D, R01 · § 06 et § 22) : projection des résultats lisibles de l'IA, par dossier.

Les trois cartes « Travaillés récemment par l'IA » de l'écran Dossiers ne lisent plus la synthèse complète, Nextcloud ni la file des
travaux : elles lisent cette projection, écrite quand un résultat lisible est prêt (analyse sourcée, proposition exploitable, livrable
prêt, note), et jamais pendant l'affichage (R05).

Contrat entre lots (feuille de route 5.6.100) :
- ``publier(desk, dossier, genre, objet_id, version, termine_utc, libelle='')`` : appelé par un producteur (le lot G à la publication
  d'une note) quand le résultat est prêt. Idempotent : (genre, objet, version) ne donne qu'une ligne. Le dossier est résolu par les
  alias du registre (``dossiers5697.resoudre``) ; un dossier inconnu ou un genre inconnu est refusé (``Stop``), jamais deviné.
- ``recents(desk, acteur, limite=3)`` : au plus ``limite`` affaires DISTINCTES, de la plus récente à la plus ancienne (horodatage UTC
  de réussite puis identifiant). Les alias sont résolus à la lecture (un alias créé après la publication est suivi), le droit est
  contrôlé sur le dossier (canonique et d'origine) ET sur l'objet exact, puis on déduplique, puis on borne. Répertoires du cabinet et
  de dépôt, résultats en échec, remplacés ou devenus inaccessibles : écartés. Aucune écriture.

Ne sont jamais projetés : indexations, lectures techniques de pièces, missions lancées, échecs, dépôts dans la messagerie (un dépôt
n'est pas un travail de l'IA), copies de travail faites à la main.

Rattrapage (``rattraper``, appelé par la boucle des automatismes) : livrables prêts des producteurs IA (registre ``livrables5689``),
fiches de travail (``fiche5627``) et rappels rédigés par un modèle (``d51_narratives``) — les seuls signaux de ``suivi5679.last_ai_work``
dont le lien vers le résultat exact est vérifiable. Une lecture de pièces ou un travail de la file sans résultat retrouvable ne crée
aucune carte.
"""
from datetime import datetime, timezone
import hashlib
import sqlite3

from .common import Stop

GENRES = {'analyse': 'Analyse', 'livrable': 'Document prêt', 'proposition': 'Proposition', 'note': 'Note'}
SCHEMA = '''
CREATE TABLE IF NOT EXISTS dossier56100_resultats (
  cabinet TEXT NOT NULL, id TEXT NOT NULL, dossier_canonique TEXT NOT NULL, dossier_source TEXT NOT NULL DEFAULT '',
  type TEXT NOT NULL CHECK (type IN ('analyse','livrable','proposition','note')),
  objet_id TEXT NOT NULL, version TEXT NOT NULL, finished INTEGER NOT NULL, libelle TEXT NOT NULL DEFAULT '',
  origine TEXT NOT NULL DEFAULT 'producteur', cree_le TEXT NOT NULL DEFAULT '',
  PRIMARY KEY(cabinet,id)
);
CREATE INDEX IF NOT EXISTS dossier56100_recents ON dossier56100_resultats(cabinet,finished DESC,id DESC);
CREATE INDEX IF NOT EXISTS dossier56100_dossier ON dossier56100_resultats(cabinet,dossier_canonique,finished DESC);
'''
# Producteurs du registre des livrables dont les documents sont un travail de l'IA (les autres : saisies, factures, brouillons déposés,
# copies de travail faites à la main, formulaires remplis par modèle de document)
SERVICES_IA = ('docrequest520', 'document_projects', 'word_legal', 'courrier5679', 'notes5666', 'missions5614', 'production420',
               'consultation5689', 'playbook5689', 'documents5697')
LOT = 64
REGLAGE = 'resultats56100:rattrapage'
# Point de montage : vérificateurs d'objets fournis par d'autres lots, {genre: f(desk, acteur, ligne) -> lien interne ou ''}.
# Utilisé seulement si l'objet n'est pas reconnu par les vérifications de ce module ; une exception vaut refus.
VERIFICATEURS = {}


def ensure_schema(desk):
    """Table additive ; appelée par ``Desk`` à la préparation de la base (jamais pendant une requête de lecture)."""
    if desk.db.in_transaction:
        desk.db.commit()
    desk.db.executescript(SCHEMA)
    desk.db.commit()


# ------------------------------------------------------------------------------------------------ outils
def _cabinet_lu(desk):
    """Identifiant du cabinet en lecture seule (``cabinet_profile5689.cabinet_id`` l'écrirait s'il manquait)."""
    try:
        return str(desk.settings('cabinet5689:id', '') or 'cabinet')
    except Exception:   # noqa: BLE001
        return 'cabinet'


def _cabinet(desk):
    try:
        from .cabinet_profile5689 import cabinet_id
        return str(cabinet_id(desk))
    except Exception:   # noqa: BLE001
        return _cabinet_lu(desk)


def _instant(valeur):
    """Horodatage UTC en millisecondes depuis un nombre (secondes) ou une date ISO ; illisible : ``Stop('date_invalide')``."""
    if isinstance(valeur, bool):
        raise Stop('date_invalide')
    if isinstance(valeur, (int, float)):
        secondes = float(valeur)
        if secondes > 1e11:          # déjà en millisecondes
            secondes /= 1000.0
        return int(secondes * 1000)
    texte = str(valeur or '').strip()
    if not texte:
        raise Stop('date_invalide')
    try:
        quand = datetime.fromisoformat(texte.replace('Z', '+00:00'))
    except ValueError:
        raise Stop('date_invalide') from None
    if quand.tzinfo is None:          # horodatage du serveur sans fuseau : UTC (format de ``desk.now``)
        quand = quand.replace(tzinfo=timezone.utc)
    return int(quand.timestamp() * 1000)


def _ident(genre, objet_id, version):
    return hashlib.sha256(('%s\x1f%s\x1f%s' % (genre, objet_id, version)).encode('utf-8')).hexdigest()[:40]


def _canonique(desk, dossier):
    """Dossier canonique : identifiant du registre unique (alias résolus) ou, pour un dossier historique non importé, son identifiant
    du registre JSON. Inconnu : ''."""
    dossier = str(dossier or '').strip()
    if not dossier or len(dossier) > 200:
        return ''
    try:
        from . import dossiers5697
        courant = dossiers5697.resoudre(desk, dossier)
    except Exception:   # noqa: BLE001 — registre unique illisible : le registre historique décide
        courant = ''
    if courant:
        return courant
    try:
        from .common import load_matters
        if any(str(m.get('id') or '') == dossier for m in load_matters(desk.c, unfiltered=True)):
            return dossier
    except Exception:   # noqa: BLE001
        pass
    return ''


# ------------------------------------------------------------------------------------------------ écriture (producteurs, worker)
def publier(desk, dossier, genre, objet_id, version, termine_utc, libelle='', origine='producteur'):
    """Inscrit un résultat lisible. Rend l'identifiant de la ligne (le même pour le même objet et la même version)."""
    genre = str(genre or '')
    if genre not in GENRES:
        raise Stop('genre_inconnu')
    objet_id, version = str(objet_id or '').strip(), str(version if version is not None else '').strip()
    if not objet_id or len(objet_id) > 200 or len(version) > 200:
        raise Stop('objet_invalide')
    finished = _instant(termine_utc)
    canon = _canonique(desk, dossier)
    if not canon:
        raise Stop('dossier_absent')
    ident = _ident(genre, objet_id, version)
    libelle = str(libelle or GENRES[genre]).strip()[:120]
    cabinet = _cabinet(desk)
    if desk.db.in_transaction:
        desk.db.commit()
    desk.db.execute('INSERT INTO dossier56100_resultats(cabinet,id,dossier_canonique,dossier_source,type,objet_id,version,finished,libelle,origine,cree_le) '
                    'VALUES(?,?,?,?,?,?,?,?,?,?,?) ON CONFLICT(cabinet,id) DO UPDATE SET dossier_canonique=excluded.dossier_canonique, '
                    'finished=MAX(finished,excluded.finished), libelle=excluded.libelle',
                    (cabinet, ident, canon, str(dossier), genre, objet_id, version, finished, libelle, str(origine)[:40],
                     datetime.now(timezone.utc).isoformat(timespec='seconds')))
    desk.db.commit()
    return ident


def _depuis(desk, source):
    return str((desk.settings(REGLAGE, {}) or {}).get(source) or '')


def _noter(desk, source, valeur):
    etat = dict(desk.settings(REGLAGE, {}) or {})
    etat[source] = valeur
    desk.setting(REGLAGE, etat)


def rattraper(desk, limite=300, pause=None):
    """Projection incrémentale depuis les sources dont le lien est vérifiable. Répétable, bornée, sans effet externe ni modèle.
    ``pause`` : point de contrôle appelé avant chaque lot (``pause5629.checkpoint``)."""
    out = {'livrables': 0, 'fiches': 0, 'rappels': 0}
    marques = ','.join('?' * len(SERVICES_IA))
    sources = (
        ('livrables', "SELECT document_id AS objet, version_id AS version, matter_id AS dossier, kind, updated AS quand FROM prepared_items5689 "
                      "WHERE updated>? AND matter_id<>'' AND production_state='verified' AND lifecycle='current' AND source_service IN (%s) "
                      "ORDER BY updated LIMIT ?" % marques, SERVICES_IA),
        ('fiches', "SELECT matter AS dossier, version, created AS quand FROM fiche5627 WHERE created>? ORDER BY created LIMIT ?", ()),
        ('rappels', "SELECT matter AS dossier, kind, version, created AS quand FROM d51_narratives WHERE created>? AND model<>'' ORDER BY created LIMIT ?", ()),
    )
    for nom, sql, extra in sources:
        if pause:
            pause()
        depuis = _depuis(desk, nom)
        try:
            rows = desk.db.execute(sql, (depuis,) + tuple(extra) + (int(limite),)).fetchall() if extra else \
                desk.db.execute(sql, (depuis, int(limite))).fetchall()
        except sqlite3.Error:      # source absente (fonction jamais utilisée) : rien à rattraper
            continue
        dernier = depuis
        for r in rows:
            dernier = max(dernier, str(r['quand'] or ''))
            try:
                if nom == 'livrables':
                    from .livrables5689 import NATURES
                    publier(desk, r['dossier'], 'livrable', r['objet'], r['version'], r['quand'], NATURES.get(r['kind'], 'Document') + ' prêt', 'rattrapage')
                elif nom == 'fiches':
                    publier(desk, r['dossier'], 'analyse', 'fiche5627:%s:%s' % (r['dossier'], r['version']), str(r['version']), r['quand'],
                            'Fiche du dossier', 'rattrapage')
                else:
                    publier(desk, r['dossier'], 'analyse', 'recit5651:%s:%s:%s' % (r['dossier'], r['kind'], r['version']), str(r['version']), r['quand'],
                            'Rappel des faits' if r['kind'] == 'faits' else 'Rappel de la procédure', 'rattrapage')
            except Stop:
                continue        # dossier inconnu, date illisible : aucune carte, la source suivante continue
            out[nom] += 1
        if dernier != depuis:
            _noter(desk, nom, dernier)
    return out


def entretien(desk, now_ts=None, intervalle_s=600):
    """Boucle des automatismes : un rattrapage toutes les 10 minutes au plus ; une erreur n'arrête jamais la boucle."""
    import time
    from . import pause5629
    now_ts = time.time() if now_ts is None else now_ts
    if now_ts - float(desk.settings('auto:resultats56100', 0) or 0) < intervalle_s:
        return None
    desk.setting('auto:resultats56100', now_ts)
    return rattraper(desk, pause=lambda: pause5629.checkpoint(desk))


# ------------------------------------------------------------------------------------------------ lecture
def _resolutions(desk, ids):
    """{id: id canonique courant} par lot (alias créés après la publication)."""
    ids = sorted({i for i in ids if i})
    out = {i: i for i in ids}
    if not ids:
        return out
    place = ','.join('?' * len(ids))
    try:
        connus = {r[0] for r in desk.db.execute('SELECT id FROM dossiers5697 WHERE id IN (%s)' % place, ids)}
        for r in desk.db.execute("SELECT alias,dossier_id FROM dossier_alias5697 WHERE genre='id' AND alias IN (%s)" % place, ids):
            if r[0] not in connus:
                out[r[0]] = r[1]
    except sqlite3.Error:
        pass
    return out


def _dossiers(desk, ids):
    """{id: {'titre', 'reference', 'statut', 'exclu'}} pour les dossiers candidats : registre unique d'abord, registre historique ensuite."""
    from . import rangement56100
    ids = sorted(set(ids))
    out = {}
    if ids:
        place = ','.join('?' * len(ids))
        try:
            rows = desk.db.execute('SELECT id,ref,titre,statut,chemin,client,rg,provenance FROM dossiers5697 WHERE id IN (%s)' % place, ids).fetchall()
        except sqlite3.Error:
            rows = []
        types = rangement56100.types(desk, ids)
        for r in rows:
            out[r['id']] = {'titre': r['titre'], 'reference': r['ref'], 'statut': r['statut'],
                            'exclu': rangement56100.exclu_registre(dict(r), types.get(r['id']))}
    reste = [i for i in ids if i not in out]
    if reste:
        from .common import load_matters, matter_display
        try:
            matters = {str(m.get('id') or ''): m for m in load_matters(desk.c, unfiltered=True)}
        except Exception:   # noqa: BLE001 — registre illisible : aucun dossier historique n'est affiché
            matters = {}
        types = rangement56100.types(desk, reste)
        for i in reste:
            m = matters.get(i)
            if m:
                etat = str((m.get('registre5697') or {}).get('statut') or m.get('status') or '')
                out[i] = {'titre': matter_display(m), 'reference': i, 'statut': 'archive' if etat in ('archive', 'archived') else 'actif',
                          'exclu': rangement56100.exclu_historique(m, types.get(i))}
    return out


def _lien_objet(desk, a, caches, droits, ligne, canon):
    """Lien interne vers le résultat EXACT si l'acteur peut l'ouvrir ; sinon ''. Contrôle propre à l'objet, en plus du dossier."""
    genre, objet, version = ligne['type'], ligne['objet_id'], ligne['version']
    if genre in ('livrable', 'note'):
        try:
            from . import livrables5689 as liv
            row = liv.charger(desk, objet, hidden=caches, version_id=version or None, droits=droits)
        except Stop:
            row = None
        except sqlite3.Error:
            row = None
        if row is not None:
            if liv.etat_contrat(row) not in ('ready',) or row['source_service'] in ('messagerie', 'copie_travail5689'):
                return ''
            if _resolutions(desk, [row['matter_id']]).get(row['matter_id']) != canon or row['matter_id'] in caches:
                return ''        # l'objet appartient à un autre dossier (ou à un dossier masqué) : jamais de carte
            from urllib.parse import urlencode
            return '/livrables/%s?%s' % (row['document_id'], urlencode({'version': row['version_id']}))
    if genre == 'analyse':
        parties = objet.split(':')
        try:
            if parties[0] == 'fiche5627' and len(parties) == 3:
                ok = desk.db.execute('SELECT 1 FROM fiche5627 WHERE matter=? AND version=?', (parties[1], parties[2])).fetchone()
                if ok and parties[1] not in caches and _resolutions(desk, [parties[1]]).get(parties[1]) == canon:
                    from urllib.parse import urlencode
                    return '/fiche?' + urlencode({'matter': parties[1]})
                return ''
            if parties[0] == 'recit5651' and len(parties) == 4:
                ok = desk.db.execute("SELECT 1 FROM d51_narratives WHERE matter=? AND kind=? AND version=? AND model<>''",
                                     (parties[1], parties[2], parties[3])).fetchone()
                if ok and parties[1] not in caches and _resolutions(desk, [parties[1]]).get(parties[1]) == canon:
                    from urllib.parse import urlencode
                    return '/dossier?' + urlencode({'id': parties[1], 'vue': 'faits'}) + '#d51-recit-' + parties[2]
                return ''
        except sqlite3.Error:
            return ''
    if genre == 'proposition':
        try:
            row = desk.db.execute("SELECT id,matter,mail_key,etat FROM file56100_items WHERE id=?", (objet,)).fetchone()
        except sqlite3.Error:
            row = None
        if row is not None:
            if row['etat'] in ('ecarte', 'remplace'):
                return ''
            from .acces5697 import filtrer_elements
            if not filtrer_elements(desk, a, [{'matter': row['matter'], 'mail_key': row['mail_key']}], caches):
                return ''
            if row['matter'] and _resolutions(desk, [row['matter']]).get(row['matter']) != canon:
                return ''
            from urllib.parse import urlencode
            return '/courriels?' + urlencode({'item': row['id']})
    verif = VERIFICATEURS.get(genre)
    if verif:
        try:
            lien = str(verif(desk, a, dict(ligne)) or '')
        except Exception:   # noqa: BLE001 — vérification impossible : refus (jamais un accès élargi)
            return ''
        return lien if lien.startswith('/') and not lien.startswith('//') else ''
    return ''


def _zone(desk):
    """Fuseau du cabinet (celui de l'agenda, comme le jour du portefeuille)."""
    from zoneinfo import ZoneInfo
    try:
        from .cockpit530 import tz
        return tz(desk)
    except Exception:   # noqa: BLE001 — fuseau illisible : celui du cabinet par défaut
        return ZoneInfo('Europe/Paris')


def date_locale(desk, finished_ms, zone=None):
    quand = datetime.fromtimestamp(int(finished_ms) / 1000.0, timezone.utc).astimezone(zone or _zone(desk))
    return quand.strftime('%d/%m/%Y à %H h %M')


def recents(desk, acteur, limite=3):
    """Au plus ``limite`` résultats, un par affaire distincte, autorisés pour l'acteur ; chacun :
    ``{'dossier_canonique', 'titre', 'reference', 'archive', 'type', 'libelle', 'finished', 'date_locale', 'lien', 'objet_id', 'version'}``.
    Registre des droits illisible : ``Stop`` (l'appelant affiche l'état, jamais une liste élargie)."""
    from . import acces5697
    a = acces5697.acteur_de(acteur)
    caches = acces5697.masques(desk, a)          # registre des droits illisible : Stop, jamais « aucune restriction »
    droits = None
    try:
        from .livrables5689 import droits_requete
        droits = droits_requete()[1]
    except Exception:   # noqa: BLE001
        droits = None
    limite = max(1, min(int(limite or 3), 12))
    cabinet = _cabinet_lu(desk)
    vus, cartes, apres = set(), [], None
    zone = _zone(desk)
    for _tour in range(40):                      # borne absolue : 40 lots de 64 lignes
        pred = ' AND (finished<? OR (finished=? AND id<?))' if apres else ''
        try:
            rows = desk.db.execute('SELECT * FROM dossier56100_resultats WHERE cabinet=?' + pred + ' ORDER BY finished DESC, id DESC LIMIT ?',
                                   (cabinet,) + ((apres[0], apres[0], apres[1]) if apres else ()) + (LOT,)).fetchall()
        except sqlite3.Error:
            return []
        if not rows:
            break
        resol = _resolutions(desk, [r['dossier_canonique'] for r in rows] + [r['dossier_source'] for r in rows])
        infos = _dossiers(desk, set(resol.values()))
        for r in rows:
            canon = resol.get(r['dossier_canonique'], r['dossier_canonique'])
            if not canon or canon in vus:
                continue
            # droit sur le dossier : identifiant canonique, identifiant publié et identifiant d'origine
            if {canon, r['dossier_canonique'], r['dossier_source']} & set(caches):
                continue
            info = infos.get(canon)
            if not info or info['exclu']:
                continue
            lien = _lien_objet(desk, a, caches, droits, dict(r), canon)
            if not lien:
                continue
            vus.add(canon)
            cartes.append({'dossier_canonique': canon, 'titre': info['titre'], 'reference': info['reference'], 'archive': info['statut'] == 'archive',
                           'type': r['type'], 'libelle': r['libelle'] or GENRES[r['type']], 'finished': r['finished'],
                           'date_locale': date_locale(desk, r['finished'], zone), 'lien': lien, 'objet_id': r['objet_id'], 'version': r['version']})
            if len(cartes) >= limite:
                return cartes
        apres = (rows[-1]['finished'], rows[-1]['id'])
    return cartes


def dernier_du_dossier(desk, acteur, dossier_id):
    """Dernier résultat autorisé d'un dossier (en-tête de la fiche, « Dernier changement ») ; aucun : None."""
    from . import acces5697
    a = acces5697.acteur_de(acteur)
    caches = acces5697.masques(desk, a)
    canon = _resolutions(desk, [str(dossier_id)]).get(str(dossier_id)) or str(dossier_id)
    if canon in caches or str(dossier_id) in caches:
        return None
    try:
        rows = desk.db.execute('SELECT * FROM dossier56100_resultats WHERE cabinet=? AND dossier_canonique IN (?,?) ORDER BY finished DESC, id DESC LIMIT 20',
                               (_cabinet_lu(desk), canon, str(dossier_id))).fetchall()
    except sqlite3.Error:
        return None
    for r in rows:
        lien = _lien_objet(desk, a, caches, None, dict(r), canon)
        if lien:
            return {'type': r['type'], 'libelle': r['libelle'] or GENRES[r['type']], 'finished': r['finished'],
                    'date_locale': date_locale(desk, r['finished']), 'lien': lien}
    return None
