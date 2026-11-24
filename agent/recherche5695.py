"""5.6.95 (R01) : service commun de recherche dans un dossier.

Une seule fonction, ``passages(desk, matter, question, limite, acteur, role)``, rend les passages utiles d'un dossier : document, section
(extrait N de l'index), extrait borné, score, provenance. Elle s'appuie sur l'index existant (``index.DocumentIndex.ranked_chunks`` :
plein texte et, si activée, recherche sémantique, puis second tri lexical) : aucun second index.

Dans l'ordre :

1. **droits** : un dossier réservé à d'autres membres (``membres5650``) ne fournit aucun passage, ni compte, ni indice ;
2. **décisions de l'avocat** : une pièce dont l'avocat a retiré la source (``assertions5689.retirer_source``, état ``retiree``) ne remonte
   pas ; un fait refusé ou archivé (``legal_memory`` : ``disputed``, ``archived``) ou rejeté (``case_assertions5689`` : ``rejected``,
   ``superseded``) ne remonte pas ; un élément de mémoire structurée validé ou épinglé par l'avocat qui répond à la question passe en tête,
   marqué ``valide_avocat`` ;
3. **point d'accroche de reclassement**, neutre par défaut : aucun reclasseur n'est livré (le cabinet a retiré l'option mesurée en 5.6.95,
   faute de gain et de licence sûre). Un reclasseur futur, local, se passe en argument (``reclasseur``) : au plus 40 candidats ; en cas
   d'échec ou de délai dépassé, l'ordre de la recherche actuelle est gardé tel quel, sans erreur. ``scripts/mesure-recherche.py`` le mesure
   avant toute proposition.

Aucun appel réseau, aucun appel à un modèle de langue : la recherche se fait sur la base locale. Les appelants existants (préparation de
brouillon, réponse de l'assistant, outil ``chercher`` du connecteur Claude) passent par ce service sans changer leur format de sortie :
``classeur`` fournit à ``DocumentIndex.sources`` / ``fast_sources`` un classement compatible avec ``ranked_chunks``.

5.6.109 (lot Q, G22) : chaque passage porte sa version (ETag indexé) et son localisateur ; ``relire_citations`` relit une citation contre
le passage source actuel avant qu'elle soit dite vérifiée ; le classement est mis en cache par périmètre, droits, révisions (identités,
politique, extracteur) et état de l'index (``qualite_sources56109.cle_cache``) — droits toujours relus avant ; ``chercher_corpus`` impose un
corpus explicite (affaire, emplacement local autorisé, fonds public).
"""
import json
import re
import sqlite3
import time

from .common import Stop, fold

CANDIDATS = 40          # passages candidats lus dans l'index (et reclassés au plus)
EXTRAIT_MAX = 1200      # caractères d'un extrait rendu (et lu par le reclasseur)
MODE = 'fusion'         # 'fusion' (rangs de la recherche actuelle et du reclasseur fusionnés) ou 'remplacement' (ordre du reclasseur seul)
FUSION_K = 60
MEMOIRE_MAX = 3         # éléments validés de la mémoire structurée placés en tête
MEMOIRE_MOTS = 2        # mots utiles communs avec la question, au moins…
MEMOIRE_PART = 0.4      # … et au moins cette part des mots utiles de la question
LIBELLES = {'validated': 'validé par l’avocat', 'pinned': 'épinglé et validé par l’avocat'}
_MOT = re.compile(r'[a-z0-9]{3,}')
_VIDES = frozenset(('les', 'des', 'une', 'pour', 'dans', 'que', 'qui', 'sur', 'par', 'avec', 'est', 'son', 'ses', 'aux', 'pas', 'plus',
                    'cette', 'ces', 'mais', 'ont', 'ete', 'sont', 'nous', 'vous', 'leur', 'leurs', 'quel', 'quelle', 'quels', 'quelles',
                    'dossier', 'piece', 'pieces', 'quand', 'comment', 'combien', 'elle', 'il', 'ils', 'elles', 'entre', 'avant', 'apres',
                    'date', 'fait', 'faits', 'dit', 'dire', 'selon', 'lors', 'tout', 'tous', 'toute', 'toutes', 'notre', 'votre', 'avez'))


def _mots(texte):
    return {m for m in _MOT.findall(fold(texte or '')) if m not in _VIDES}


def _mid(matter):
    return str(matter['id'] if isinstance(matter, dict) else matter or '')


def dossiers_masques(desk, acteur=None, role=None):
    """Dossiers interdits à l'utilisateur : réservés à d'autres membres (droits relus ici, jamais supposés par l'appelant)."""
    from .membres5650 import hidden_for, viewer_hidden
    masques = set(viewer_hidden() or ())
    if acteur is not None and role is not None:
        masques |= set(hidden_for(desk.c['state_dir'], acteur, role))
    return masques


def pieces_ecartees(desk, mid):
    """Chemins des pièces dont l'avocat a retiré la source (et aucune version courante ne la remplace)."""
    try:
        rows = desk.db.execute("SELECT path,etat FROM source_versions5689 WHERE matter=? AND path<>''", (mid,)).fetchall()
    except sqlite3.OperationalError:   # mémoire typée jamais ouverte sur ce poste : aucune pièce écartée
        return set()
    retirees = {r[0] for r in rows if r[1] == 'retiree'}
    courantes = {r[0] for r in rows if r[1] == 'courante'}
    return retirees - courantes


def _extrait(texte, mots, maximum=EXTRAIT_MAX):
    """Fenêtre de ``maximum`` caractères la plus riche en mots de la question, coupée à une limite de phrase ou de mot."""
    texte = str(texte or '').strip()
    if len(texte) <= maximum:
        return texte
    plie = fold(texte)
    meilleur, debut = -1, 0
    pas = max(100, maximum // 4)
    for d in range(0, max(1, len(texte) - maximum + pas), pas):
        fenetre = plie[d:d + maximum]
        n = sum(fenetre.count(m) for m in mots)
        if n > meilleur:
            meilleur, debut = n, d
    if debut:
        coupe = max(texte.rfind('. ', 0, debut + 120), texte.rfind('\n', 0, debut + 120))
        debut = coupe + 1 if coupe >= max(0, debut - 200) else debut
    morceau = texte[debut:debut + maximum]
    if debut + maximum < len(texte):
        fin = max(morceau.rfind('. '), morceau.rfind('\n'))
        morceau = morceau[:fin + 1] if fin > maximum // 2 else morceau.rsplit(' ', 1)[0]
    return ('… ' if debut else '') + morceau.strip() + (' …' if debut + len(morceau) < len(texte) else '')


def _pertinent(mots, texte):
    """Nombre de mots utiles communs si l'élément répond à la question (seuils ``MEMOIRE_MOTS`` et ``MEMOIRE_PART``), sinon 0. Une référence
    chiffrée de la question (facture, RG, article, date) absente de l'élément l'écarte : la décision sur la F2024-044 ne répond pas à la F2024-031."""
    siens = _mots(texte)
    if any(any(c.isdigit() for c in m) and m not in siens for m in mots):
        return 0
    communs = len(mots & siens)
    return communs if communs >= MEMOIRE_MOTS and communs >= MEMOIRE_PART * len(mots) else 0


def _memoire_validee(desk, mid, mots):
    """Éléments validés ou épinglés par l'avocat qui répondent à la question (ancienne mémoire et mémoire typée), les plus proches d'abord."""
    out = []
    try:
        rows = desk.db.execute("SELECT id,record_type,title,content,status,updated,sources FROM legal_memory_records "
                               "WHERE matter=? AND status IN ('validated','pinned') ORDER BY updated DESC LIMIT 300", (mid,)).fetchall()
    except sqlite3.OperationalError:
        rows = []
    for r in rows:
        communs = _pertinent(mots, r[2] + ' ' + r[3])
        if communs:
            try:
                sources = [str(x) for x in json.loads(r[6] or '[]')]
            except ValueError:
                sources = []
            out.append({'cle': 'memoire-' + str(r[0])[:20], 'communs': communs, 'epingle': r[4] == 'pinned', 'date': r[5],
                        'texte': '[%s] %s — %s\n%s' % (LIBELLES[r[4]], r[1], r[2], r[3]), 'sources': sources, 'origine': 'legal_memory'})
    try:
        rows = desk.db.execute("SELECT id,kind,statement,asserted_by,updated,passage FROM case_assertions5689 "
                               "WHERE matter=? AND status='validated' ORDER BY updated DESC LIMIT 300", (mid,)).fetchall()
    except sqlite3.OperationalError:
        rows = []
    from .assertions5689 import KINDS
    for r in rows:
        communs = _pertinent(mots, r[2])
        if communs:
            out.append({'cle': 'assertion-' + str(r[0])[:20], 'communs': communs, 'epingle': r[1] == 'lawyer_decision', 'date': r[4],
                        'texte': '[validé par l’avocat] %s — %s%s' % (KINDS.get(r[1], r[1]), r[2], ' (selon %s)' % r[3] if r[1] == 'allegation' else ''),
                        'sources': [], 'origine': 'case_assertions5689'})
    out.sort(key=lambda x: (-x['communs'], not x['epingle']))   # tri stable : le plus récent d'abord à égalité
    return out[:MEMOIRE_MAX]


def _index(desk, index):
    if index is not None:
        return index, False
    from .intelligence import source_index
    return source_index(desk.c), True


# ------------------------------------------------------------------ 5.6.109 (lot Q, G22) : cache par périmètre, droits et révisions
CACHE_MAX = 64
_CACHE = {}          # clé de qualité -> (lignes classées, erreur sémantique) ; en mémoire du processus : aucune écriture pendant une lecture


def _empreinte(valeur):
    import hashlib
    return hashlib.sha256(json.dumps(valeur, ensure_ascii=False, sort_keys=True, default=str).encode('utf-8')).hexdigest()


def revisions(desk, acteur=None, role=None):
    """Révisions qui invalident un résultat dérivé : droits (dossiers réservés vus par l'acteur), registre des identités (``pseudo540``,
    lot P), politique des données (``politique5682``) et extracteur (``documents``). ``None`` pour une révision illisible : jamais de cache."""
    from . import pseudo540
    from .documents import signature_extraction
    state_dir = desk.c.get('state_dir', '')
    try:
        droits = _empreinte(sorted(dossiers_masques(desk, acteur, role)))
    except Exception:   # noqa: BLE001 — contrôle des droits illisible : pas de cache (et la recherche elle-même refusera)
        droits = None
    rev = pseudo540.revision(state_dir)
    try:
        from .politique5682 import read_fresh
        politique = _empreinte(read_fresh(state_dir))
    except Exception:   # noqa: BLE001
        politique = None
    return {'droits': droits, 'entites': None if rev is None else str(rev), 'politique': politique,
            'extracteur': signature_extraction(desk.c.get('documents') or {})}


def _etat_index(index, mid):
    """Empreinte de l'état de l'index du dossier (nombre de passages, dernière indexation, versions) : une pièce ajoutée, retirée ou
    réindexée change la clé."""
    try:
        row = index.db.execute('SELECT COUNT(*),MAX(indexed_at),group_concat(DISTINCT etag) FROM knowledge_chunks WHERE matter=?', (mid,)).fetchone()
        vecteurs = index.db.execute('SELECT COUNT(*) FROM knowledge_embeddings WHERE matter=?', (mid,)).fetchone()
    except sqlite3.Error:
        return None
    classeur = getattr(index.ranked_chunks, '__func__', index.ranked_chunks)   # classement remplacé (essai, option) : autre clé
    return _empreinte(list(row) + list(vecteurs) + [id(classeur)])


def cle_recherche(desk, index, mid, question, kinds, recent, nombre, acteur=None, role=None):
    """Clé ``qualite_sources56109.cle_cache`` d'une recherche, ou ``None`` (révision absente : la recherche est refaite, jamais servie)."""
    from .qualite_sources56109 import cle_cache
    from .acces5683 import today_of
    r = revisions(desk, acteur, role)
    etat = _etat_index(index, mid)
    if None in (r['droits'], r['entites'], r['politique'], etat):
        return None
    rag = getattr(index, 'rag', {}) or {}
    try:
        return cle_cache(perimetre_id=_empreinte([desk.c.get('cabinet_id') or 'cabinet', acteur, role, mid, question, kinds, recent, nombre]),
                         source_sha=etat, version_extracteur=r['extracteur'], version_options=_empreinte([MODE, CANDIDATS]),
                         revision_droits=r['droits'], revision_entites=r['entites'], revision_politique=r['politique'],
                         modele_version=str(rag.get('embedding_model', '')) if rag.get('enabled') else 'fts', date_droit=str(today_of(desk)[0]))
    except Stop:
        return None


def _classement(desk, index, mid, question, nombre, kinds, recent, acteur, role, etat):
    """``ranked_chunks`` servi par le cache quand la clé (périmètre, droits, révisions, état de l'index) est identique ; une recherche
    sémantique en erreur n'est jamais mise en cache. Les droits du dossier sont relus AVANT par l'appelant, cache ou non."""
    cle = cle_recherche(desk, index, mid, question, kinds, recent, nombre, acteur, role) if hasattr(index, 'db') else None
    if cle is not None and cle in _CACHE:
        etat['cache'] = 'servi'
        return _CACHE[cle]
    ranked, erreur = index.ranked_chunks(mid, [question], limit=nombre, kinds=kinds, recent=recent)
    etat['cache'] = 'calcule' if cle is not None else 'sans_cache'
    if cle is not None and not erreur:
        if len(_CACHE) >= CACHE_MAX:
            _CACHE.pop(next(iter(_CACHE)))
        _CACHE[cle] = (ranked, erreur)
    return ranked, erreur


def _candidats(desk, index, mid, question, kinds=None, recent=False, reclasseur=None, nombre=CANDIDATS, mode=None, acteur=None, role=None):
    """Lignes de l'index (format ``knowledge_chunks``) et scores, pièces écartées retirées, reclassées si un reclasseur est fourni.
    Retourne (liste de (ligne, score, provenance), état)."""
    mode = mode or MODE
    etat = {'candidats': 0, 'pieces_ecartees': 0, 'reclassement': 'desactive', 'erreur_semantique': ''}
    ranked, erreur = _classement(desk, index, mid, question, nombre, kinds, recent, acteur, role, etat)
    etat['erreur_semantique'] = erreur or ''
    ecartees = pieces_ecartees(desk, mid)
    gardes = []
    for row, score in ranked:
        if row[2] in ecartees:
            etat['pieces_ecartees'] += 1
            continue
        gardes.append((row, score, 'index'))
    etat['candidats'] = len(gardes)
    if reclasseur is not None and gardes:
        mots = _mots(question)
        debut = time.monotonic()
        try:
            scores = reclasseur(question, [_extrait(row[7], mots) for row, _, _ in gardes[:CANDIDATS]])
        except Stop as ex:
            etat['reclassement'] = 'echec : ' + str(ex)
        except Exception as ex:   # noqa: BLE001 — reclasseur défaillant : ordre de la recherche actuelle, sans erreur
            etat['reclassement'] = 'echec : ' + type(ex).__name__
        else:
            tete = list(zip(gardes[:CANDIDATS], scores))
            if mode == 'fusion':   # rangs de la recherche actuelle et du modèle fusionnés (RRF, comme index.fuse) : aucun des deux n'écrase l'autre
                rang = {i: n for n, i in enumerate(sorted(range(len(tete)), key=lambda i: -tete[i][1]), 1)}
                fusion = {i: 1 / (FUSION_K + i + 1) + 1 / (FUSION_K + rang[i]) for i in range(len(tete))}
                ordre = sorted(range(len(tete)), key=lambda i: (-fusion[i], i))
                gardes = [(tete[i][0][0], round(float(tete[i][1]), 4), 'reclasse') for i in ordre] + gardes[CANDIDATS:]
            else:
                tete.sort(key=lambda x: -x[1])
                gardes = [(row, round(float(s), 4), 'reclasse') for (row, _, _), s in tete] + gardes[CANDIDATS:]
            etat['reclassement'] = 'applique'
        etat['reclassement_ms'] = round((time.monotonic() - debut) * 1000, 1)
    return gardes, etat


def chercher(desk, matter, question, limite=10, acteur=None, role=None, index=None, reclasseur=None, memoire=True, kinds=None, recent=False, mode=None):
    """(passages, état) ; ``reclasseur`` : None (par défaut, recherche actuelle) ou fonction locale (question, textes) -> scores (mesure, essais).
    Un dossier masqué rend ([], {'refus': 'dossier_reserve'}) : ni passage ni compte."""
    mid = _mid(matter)
    question = str(question or '').strip()[:4000]
    limite = max(1, min(int(limite or 10), 40))
    if not mid:
        raise Stop('dossier_requis')
    if mid in dossiers_masques(desk, acteur, role):
        return [], {'refus': 'dossier_reserve'}
    index, ferme = _index(desk, index)
    try:
        gardes, etat = _candidats(desk, index, mid, question, kinds, recent, reclasseur, mode=mode, acteur=acteur, role=role)
        mots = _mots(question)
        sortie = []
        if memoire:
            for item in _memoire_validee(desk, mid, mots):
                sortie.append({'dossier': mid, 'document': 'Mémoire structurée du dossier', 'nom': 'Mémoire structurée du dossier',
                               'section': item['origine'], 'extrait': item['texte'][:EXTRAIT_MAX], 'score': None, 'provenance': 'memoire_validee',
                               'valide_avocat': True, 'source_id': item['cle'], 'nature': 'structured_memory', 'date': item['date'],
                               'sources_originales': item['sources'], 'confidentiel': False})
        for row, score, provenance in gardes:
            if len(sortie) >= limite:
                break
            # 5.6.109 (lot Q, G22) : version de la source (ETag indexé) et localisateur (section de l'index, jamais présentée comme une page)
            sortie.append({'dossier': mid, 'document': row[2], 'nom': str(row[2]).rsplit('/', 1)[-1], 'section': 'extrait %d' % (int(row[6]) + 1),
                           'extrait': _extrait(row[7], mots), 'score': score, 'provenance': provenance, 'valide_avocat': False,
                           'source_id': row[1], 'nature': row[5], 'date': row[4], 'confidentiel': bool(index.source_confidential(mid, row[1])),
                           'version': row[3] or '', 'localisateur': {'type': 'section_index', 'numero': int(row[6]) + 1}, 'corpus': 'affaire'})
        etat['rendus'] = len(sortie)
        return sortie[:limite], etat
    finally:
        if ferme:
            index.db.close()


def passages(desk, matter, question, limite=10, acteur=None, role=None, **options):
    """Service commun (R01) : liste de passages, voir ``chercher``."""
    return chercher(desk, matter, question, limite, acteur, role, **options)[0]


def classeur(desk, acteur=None, role=None, reclasseur=None):
    """Classement compatible avec ``DocumentIndex.ranked_chunks`` (mêmes arguments, même retour ``([(ligne, score)], erreur)``) pour les
    appelants qui gardent leur format de sortie : droits et pièces écartées appliqués, mémoire non mêlée aux documents ; sans reclasseur,
    exactement le classement actuel."""

    def classer(index, matter, terms, limit=10, kinds=None, min_relative=0.0, recent=False):
        mid = _mid(matter)
        if mid in dossiers_masques(desk, acteur, role):
            return [], ''
        question = ' '.join(str(t or '') for t in terms if t)
        nombre = max(CANDIDATS, int(limit)) if reclasseur is not None else int(limit)   # sans reclasseur : exactement le classement actuel
        gardes, etat = _candidats(desk, index, mid, question, kinds, recent, reclasseur, nombre, acteur=acteur, role=role)
        return [(row, score) for row, score, _ in gardes][:max(1, int(limit))], etat.get('erreur_semantique', '')
    return classer


# ------------------------------------------------------------------ 5.6.109 (lot Q, G22) : citations relues avant d'être dites vérifiées
def relire_citations(desk, matter, citations, acteur=None, role=None, index=None):
    """Relit chaque citation d'une réponse contre le passage source ACTUEL de l'index du dossier avant tout affichage « vérifiée ».

    ``citations`` : liste de ``{'source_id', 'version', 'section' (numéro d'extrait, 1 = premier), 'passage'}`` reprise d'un résultat de
    ``chercher``. Chaque citation reçoit ``etat`` : ``verifiee`` seulement si le dossier est lisible par l'acteur, que la pièce n'a pas été
    retirée par l'avocat, que la version indexée est celle de l'extrait et que le passage cité figure dans le texte relu ; sinon
    ``a_verifier`` et son motif. Dossier interdit : toutes « à vérifier » (acces_non_autorise), sans rien relire ni dire de la pièce."""
    from .qualite_sources56109 import etat_citation
    mid = _mid(matter)
    out = []
    items = [c for c in (citations or []) if isinstance(c, dict)][:100]
    if not mid or mid in dossiers_masques(desk, acteur, role):
        return [{**{k: c.get(k) for k in ('source_id', 'version', 'section', 'passage')}, 'etat': 'a_verifier', 'motif': 'acces_non_autorise'}
                for c in items]
    index, ferme = _index(desk, index)
    try:
        ecartees = pieces_ecartees(desk, mid)
        for c in items:
            sid, version = str(c.get('source_id') or ''), str(c.get('version') or '')
            try:
                numero = int(c.get('section') or 0)
            except (TypeError, ValueError):
                numero = 0
            rows = index.db.execute('SELECT path,etag,text FROM knowledge_chunks WHERE matter=? AND source_id=? AND chunk_no BETWEEN ? AND ? '
                                    'ORDER BY chunk_no', (mid, sid, max(0, numero - 2), numero)).fetchall() if sid and numero >= 1 else []
            lisible = bool(rows) and rows[0][0] not in ecartees
            courante = rows[0][1] if rows else ''
            texte = '\n'.join(r[2] or '' for r in rows)       # l'extrait N et son voisin : un passage à cheval sur deux extraits est relu
            out.append({'source_id': sid, 'version': version, 'section': numero, 'passage': str(c.get('passage') or '')[:2000],
                        **etat_citation(c.get('passage'), texte, source_lisible=lisible, version_attendue=version, version_courante=courante)})
        return out
    finally:
        if ferme:
            index.db.close()


def chercher_corpus(desk, corpus, question, limite=10, acteur=None, role=None, **options):
    """5.6.109 (lot Q, F13 et G23) : point d'entrée commun avec un corpus EXPLICITE, jamais deviné ni élargi :

    - ``{'type': 'affaire', 'id': dossier}`` → ``chercher`` (droits, pièces écartées, mémoire validée) ;
    - ``{'type': 'emplacement_local', 'ids': [...]}`` → ``contexte56109.chercher`` (fichiers d'un répertoire autorisé du poste, hors affaire :
      un répertoire n'est pas un dossier client) ;
    - ``{'type': 'fonds_public', 'pays': ..., 'langue': ..., 'date': ...}`` → ``fonds_juridique56109.rechercher`` (fonds juridique local,
      distinct des pièces privées).
    Un corpus absent ou inconnu est refusé (``corpus_requis``) : pas de recherche globale sans filtre."""
    corpus = corpus if isinstance(corpus, dict) else {}
    nature = corpus.get('type')
    if nature == 'affaire':
        return chercher(desk, corpus.get('id'), question, limite, acteur, role, **options)
    if nature == 'emplacement_local':
        from . import contexte56109
        return contexte56109.chercher(desk, (acteur, role), corpus.get('ids') or [], question, limite)
    if nature == 'fonds_public':
        from . import fonds_juridique56109
        return fonds_juridique56109.rechercher(desk, question, pays=corpus.get('pays') or '', langue=corpus.get('langue') or '',
                                               date_utile=corpus.get('date') or '', limite=limite)
    raise Stop('corpus_requis')
