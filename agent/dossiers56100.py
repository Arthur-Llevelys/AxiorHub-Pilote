"""5.6.100 (lot D, priorité 3 · cahier §§ 06, 13 et 22) : écran Dossiers paginé et reprise d'une affaire.

- ``page`` : UNE requête de liste pour la page affichée (30 lignes par défaut, 50 au plus), après les droits (``acces5697``), le type de
  répertoire (``rangement56100``), la recherche (intitulé, référence, client, RG, nom d'une partie, sans accents) et un tri stable ;
  le total vient d'un agrégat, pas d'une liste. Les dossiers historiques pas encore importés restent listés en lecture (après ceux du
  registre), avec la migration proposée. Aucune écriture.
- ``cartes_recents_html`` : bloc « Travaillés récemment par l'IA » (``resultats56100.recents``) : trois cartes au plus, nombre réel.
- ``reprise_html`` : quatre champs de l'en-tête de la fiche (Responsable, Prochaine action, Prochaine échéance confirmée, Dernier
  changement) avec leurs liens, et « Attribuer » / « Choisir la prochaine action » selon les droits ; IA en pause : tout reste modifiable
  (les formulaires passent par ``portefeuille5697``, sans modèle).
- ``volet_nature_html`` : Conseil (points à négocier, version comparée, obligations proposées / confirmées) ou Contentieux (actes,
  pièces probantes, correspondances, échéances vérifiées, affirmations attribuées à leur partie), lus dans les registres existants.
"""
from datetime import date, datetime, timezone
from html import escape as e
import sqlite3
from urllib.parse import urlencode

from .common import Stop

TAILLE_DEFAUT = 30
TAILLE_MAX = 50
TRIS = {'activite': ('d.modifie_le DESC, d.id ASC', 'Dernière modification'), 'titre': ('d.titre COLLATE NOCASE ASC, d.id ASC', 'Intitulé'),
        'reference': ('d.ref COLLATE NOCASE ASC, d.id ASC', 'Référence')}


# ------------------------------------------------------------------------------------------------ outils
def _fonctions(db):
    """Fonctions SQL de la page (pliage des accents, règle de rangement) : déterministes, sans accès à la base."""
    from .dossiers5697 import _plier
    from . import rangement56100
    db.create_function('ax100_plier', 1, lambda t: _plier(t or ''), deterministic=True)
    db.create_function('ax100_exclu', 6, lambda type_, chemin, titre, client, rg, provenance: 1 if rangement56100.exclu_registre(
        {'chemin': chemin, 'titre': titre, 'client': client, 'rg': rg, 'provenance': provenance}, type_) else 0, deterministic=True)


def _table(desk, nom):
    try:
        return bool(desk.db.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name=?", (nom,)).fetchone())
    except sqlite3.Error:
        return False


def jour_cabinet(desk):
    """Jour du cabinet (fuseau de l'agenda), jamais ``date.today()`` du serveur."""
    try:
        from .portefeuille5697 import aujourd_hui
        return aujourd_hui(desk)
    except Exception:   # noqa: BLE001
        return datetime.now(timezone.utc).date()


def _like(texte):
    return '%' + texte.replace('\\', '\\\\').replace('%', '\\%').replace('_', '\\_') + '%'


def _lien(prefix, chemin, **params):
    return e(prefix + chemin + ('?' + urlencode({k: v for k, v in params.items() if v not in (None, '', False)}) if params else ''), quote=True)


def _date_fr(valeur):
    valeur = str(valeur or '')[:10]
    try:
        return date.fromisoformat(valeur).strftime('%d/%m/%Y')
    except ValueError:
        return ''


# ------------------------------------------------------------------------------------------------ liste paginée
def page(desk, acteur, filtres=None, numero=1, taille=TAILLE_DEFAUT, tri='activite'):
    """Page de dossiers visibles de l'acteur. Rend ``{'lignes', 'total', 'page', 'pages', 'taille', 'tri', 'nb_verifier', 'responsables',
    'historiques'}``. Droits joints AVANT recherche, total et LIMIT ; registre des droits illisible : ``Stop`` (jamais une liste élargie)."""
    from . import acces5697, dossiers5697 as D
    D.ensure_schema(desk)
    a = acces5697.acteur_de(acteur)
    perimetre = acces5697.dossiers_autorises(desk, a)
    f = filtres or {}
    taille = max(1, min(int(taille or TAILLE_DEFAUT), TAILLE_MAX))
    numero = max(1, int(numero or 1))
    tri = tri if tri in TRIS else 'activite'
    _fonctions(desk.db)
    clause, params = perimetre.sql('d.id')
    jointure = ''
    cabinet = str(desk.settings('cabinet5689:id', '') or 'cabinet')
    if _table(desk, 'dossier_types56100'):
        jointure = ' LEFT JOIN dossier_types56100 t ON t.cabinet=? AND t.cle=d.id'
        exclu = 'ax100_exclu(t.type,d.chemin,d.titre,d.client,d.rg,d.provenance)=0'
        base_args = [cabinet]
    else:
        exclu = 'ax100_exclu(NULL,d.chemin,d.titre,d.client,d.rg,d.provenance)=0'
        base_args = []
    where, args = [clause, exclu], list(params)
    statut = str(f.get('statut') or 'actif')
    if statut in D.STATUTS:
        where.append('d.statut=?')
        args.append(statut)
    base_where, base_params = list(where), list(args)        # périmètre et état : base des responsables proposés
    if f.get('nature') in D.NATURES:
        where.append('d.nature=?')
        args.append(f['nature'])
    if f.get('mes'):
        where.append('d.responsable=?')
        args.append(a.compte)
    elif f.get('responsable'):
        where.append('d.responsable=?')
        args.append(str(f['responsable'])[:120])
    q = D._plier(f.get('q') or '')[:120]
    if q:
        motif = _like(q)
        where.append("(ax100_plier(d.titre||' '||d.ref||' '||d.client||' '||d.rg) LIKE ? ESCAPE '\\' OR EXISTS (SELECT 1 FROM dossier_parties5697 p "
                     "WHERE p.dossier_id=d.id AND p.nom_norm LIKE ? ESCAPE '\\'))")
        args += [motif, motif]
    jour = jour_cabinet(desk).isoformat()
    echeances = _table(desk, 'deadlines450')
    filtre_sans_verif = list(where), list(args)
    if f.get('verifier'):
        if echeances:
            where.append("EXISTS (SELECT 1 FROM deadlines450 x WHERE x.matter=d.id AND x.status='a_confirmer' AND x.due>=? AND x.due<>'')")
            args.append(jour)
        else:
            where.append('0')
    sql_where = ' AND '.join(where)
    total_registre = desk.db.execute('SELECT COUNT(*) FROM dossiers5697 d%s WHERE %s' % (jointure, sql_where), base_args + args).fetchone()[0]
    nb_verifier = 0
    if echeances:
        w, p = filtre_sans_verif
        nb_verifier = desk.db.execute("SELECT COUNT(*) FROM deadlines450 x WHERE x.status='a_confirmer' AND x.due>=? AND x.due<>'' AND x.matter IN "
                                      "(SELECT d.id FROM dossiers5697 d%s WHERE %s)" % (jointure, ' AND '.join(w)), [jour] + base_args + p).fetchone()[0]
    responsables = [r[0] for r in desk.db.execute('SELECT DISTINCT d.responsable FROM dossiers5697 d%s WHERE %s AND d.responsable<>\'\' ORDER BY d.responsable'
                                                   % (jointure, ' AND '.join(base_where)), base_args + base_params)]
    # dossiers historiques (registre JSON) pas encore importés : lecture seule, après ceux du registre, mêmes droits et même règle de rangement
    historiques = []
    if not D.migration_faite(desk) and statut != 'archive' and not f.get('nature') and not f.get('mes') and not f.get('responsable'):
        for v in D._virtuels(desk, perimetre):
            if q and q not in D._plier(' '.join((v['titre'], v['ref'], v['client']))):
                continue
            historiques.append(v)
        if f.get('verifier') and historiques:
            ids = [v['id'] for v in historiques]
            avec = {r[0] for r in desk.db.execute("SELECT DISTINCT matter FROM deadlines450 WHERE status='a_confirmer' AND due>=? AND due<>'' AND matter IN (%s)"
                                                  % ','.join('?' * len(ids)), [jour] + ids)} if echeances else set()
            historiques = [v for v in historiques if v['id'] in avec]
        if echeances and historiques and not f.get('verifier'):
            ids = [v['id'] for v in historiques]
            nb_verifier += desk.db.execute("SELECT COUNT(*) FROM deadlines450 WHERE status='a_confirmer' AND due>=? AND due<>'' AND matter IN (%s)"
                                           % ','.join('?' * len(ids)), [jour] + ids).fetchone()[0]
    total = total_registre + len(historiques)
    pages = max(1, -(-total // taille))
    numero = min(numero, pages)
    debut = (numero - 1) * taille
    lignes = []
    if debut < total_registre:
        rows = desk.db.execute('SELECT d.* FROM dossiers5697 d%s WHERE %s ORDER BY %s LIMIT ? OFFSET ?' % (jointure, sql_where, TRIS[tri][0]),
                               base_args + args + [taille, debut]).fetchall()
        lignes = [D._vue(desk, r, parties=False) for r in rows]
    reste = taille - len(lignes)
    if reste > 0 and historiques:
        depart = max(0, debut - total_registre)
        lignes += historiques[depart:depart + reste]
    return {'lignes': lignes, 'total': total, 'page': numero, 'pages': pages, 'taille': taille, 'tri': tri, 'nb_verifier': nb_verifier,
            'responsables': responsables, 'historiques': len(historiques)}


def pagination_html(prefix, base, resultat):
    """Navigation entre pages (liens simples, sans script) ; la position, les filtres et le tri sont dans l'adresse."""
    if resultat['pages'] <= 1:
        return ''
    n, pages = resultat['page'], resultat['pages']

    def lien(num, libelle, courant=False, rel=''):
        return '<a class="d100-page" href="%s"%s%s>%s</a>' % (_lien(prefix, '/dossiers', **{**base, 'p': str(num) if num > 1 else ''}),
                                                            ' aria-current="page"' if courant else '', (' rel="%s"' % rel) if rel else '', e(libelle))
    numeros = sorted({1, pages, n - 1, n, n + 1} & set(range(1, pages + 1)))
    out, avant = [], 0
    for num in numeros:
        if avant and num > avant + 1:
            out.append('<span class="d100-ellipse" aria-hidden="true">…</span>')
        out.append(lien(num, str(num), num == n))
        avant = num
    prec = lien(n - 1, 'Page précédente', rel='prev') if n > 1 else ''
    suiv = lien(n + 1, 'Page suivante', rel='next') if n < pages else ''
    return '<nav class="d100-pagination" aria-label="Pages des dossiers">%s%s%s</nav>' % (prec, ''.join(out), suiv)


# ------------------------------------------------------------------------------------------------ « Travaillés récemment par l'IA »
def cartes_recents_html(desk, acteur, prefix):
    """Bloc sous l'en-tête de Dossiers, avant le tableau : au plus trois cartes, une par affaire ; le nom ouvre la fiche, « Voir le
    résultat » ouvre le résultat exact. Aucun résultat : « Aucun travail IA récent. » ; source illisible : l'état est dit."""
    from . import resultats56100
    try:
        cartes = resultats56100.recents(desk, acteur, 3)
    except Stop:
        corps = '<p class="ax-muted">Travaux récents indisponibles pour le moment.</p>'
    else:
        items = []
        for c in cartes:
            items.append('<li class="d100-carte"><h3><a href="%s">%s</a></h3><p class="d100-carte-meta"><span>%s</span>%s</p>'
                         '<p class="d100-carte-travail">%s · <time>%s</time></p><p><a class="d100-resultat" href="%s">Voir le résultat'
                         '<span class="sr-only"> : %s</span></a></p></li>' % (
                             _lien(prefix, '/dossier', id=c['dossier_canonique']), e(c['titre']), e(c['reference']),
                             ' <span class="d100-archive">Archivé</span>' if c['archive'] else '', e(c['libelle']), e(c['date_locale']),
                             e(prefix + c['lien'], quote=True), e(c['titre'])))
        corps = ('<ul class="d100-cartes" data-d100-nombre="%d">%s</ul>' % (len(items), ''.join(items))) if items else \
            '<p class="ax-muted">Aucun travail IA récent.</p>'
    return '<section class="d100-recents" aria-labelledby="d100-recents-t"><h2 id="d100-recents-t">Travaillés récemment par l’IA</h2>%s</section>' % corps


# ------------------------------------------------------------------------------------------------ en-tête de la fiche
def _lignes(desk, sql, params=()):
    try:
        return desk.db.execute(sql, params).fetchall()
    except sqlite3.Error:
        return []


def _candidats(desk, mid, jour):
    """Échéances confirmées à venir puis tâches ouvertes du dossier (lecture seule, sans création de table)."""
    out = [{'ref': 'echeance:' + r['id'], 'libelle': (r['note'] or str(r['rule_id']).replace('_', ' '))[:120], 'date': r['due'][:10]}
           for r in _lignes(desk, "SELECT id,rule_id,note,due FROM deadlines450 WHERE matter=? AND status IN ('confirmee','manuel') AND due>=? AND due<>'' "
                                  "ORDER BY due, id LIMIT 20", (mid, jour))]
    out += [{'ref': 'tache:' + str(r['id']), 'libelle': str(r['title'] or 'Tâche')[:120], 'date': str(r['due'] or '')[:10]}
            for r in _lignes(desk, "SELECT t.id,t.title,t.due FROM tasks t LEFT JOIN taches5688 x ON x.task_id=t.id WHERE t.matter=? AND t.status='open' "
                                   "AND COALESCE(x.etat,'a_faire') NOT IN ('terminee','annulee') ORDER BY COALESCE(NULLIF(t.due,''),'9999'), t.id LIMIT 30", (mid,))]
    return out


def _dernier_changement(desk, acteur, mid, membres):
    """Le plus récent entre : résultat de l'IA publié, journal du registre, historique du portefeuille, journal du dossier (non technique)."""
    from . import resultats56100
    choix = []
    try:
        r = resultats56100.dernier_du_dossier(desk, acteur, mid)
    except Stop:
        r = None
    if r:
        quand = datetime.fromtimestamp(r['finished'] / 1000.0, timezone.utc).isoformat()
        choix.append((quand, '%s (IA)' % r['libelle'], r['lien']))
    for row in _lignes(desk, 'SELECT quand,acteur,action FROM dossier_journal5697 WHERE dossier_id=? ORDER BY id DESC LIMIT 1', (mid,)):
        libelle = {'creation': 'Dossier créé', 'modification': 'Fiche modifiée', 'migration': 'Dossier importé', 'cloture': 'Dossier clôturé',
                   'reouverture': 'Dossier rouvert', 'courriel': 'Courriel classé', 'type_repertoire': 'Type de répertoire modifié'}.get(row['action'], 'Fiche modifiée')
        choix.append((row['quand'], '%s par %s' % (libelle, membres.get(row['acteur'], row['acteur'] or 'le cabinet')), '/dossier?' + urlencode({'id': mid, 'vue': 'gestion'})))
    for row in _lignes(desk, 'SELECT champ,acteur,le FROM portefeuille5697_historique WHERE dossier_id=? ORDER BY le DESC LIMIT 1', (mid,)):
        libelle = {'responsable_id': 'Responsable modifié', 'next_action_type': 'Prochaine action choisie', 'next_action_id': 'Prochaine action choisie',
                   'etat_operatoire': 'État modifié'}.get(row['champ'], 'Suivi modifié')
        choix.append((row['le'], '%s par %s' % (libelle, membres.get(row['acteur'], row['acteur'])), ''))
    for row in _lignes(desk, 'SELECT title,created FROM d51_changes WHERE matter=? AND technical=0 ORDER BY id DESC LIMIT 1', (mid,)):
        choix.append((row['created'], str(row['title'] or 'Nouveau document')[:120], '/dossier?' + urlencode({'id': mid, 'vue': 'fichiers'})))
    choix = [c for c in choix if c[0]]
    return max(choix, key=lambda c: str(c[0])) if choix else None


def reprise_html(desk, acteur, prefix, mid, membres=None):
    """Quatre champs de reprise (lecture seule ; les formulaires passent par les routes existantes du portefeuille)."""
    from . import acces5697, portefeuille5697 as P
    a = acces5697.exiger(desk, acteur, mid)          # 404 neutre avant toute lecture
    if membres is None:
        try:
            from .membres5650 import directory
            membres = {k: v.get('name') or k for k, v in directory(desk).items()}
        except Exception:   # noqa: BLE001
            membres = {}
    ligne = P.lignes_portefeuille(desk, a, [mid]).get(mid)
    jour = jour_cabinet(desk).isoformat()
    peut_attribuer = a.role in ('administrateur', 'avocat')
    peut_agir = a.role in ('administrateur', 'avocat', 'assistant')
    statut = '<p class="m97-statut" role="status" aria-live="polite" data-m97-statut></p>'
    champs = []
    # Responsable
    resp = (ligne or {}).get('responsable_id') or ''
    if ligne is None:
        valeur = '<span class="ax-muted">indisponible</span>'
    elif resp:
        valeur = '<a href="%s">%s</a>' % (_lien(prefix, '/dossiers', responsable=resp), e(membres.get(resp, resp)))
    else:
        valeur = '<span class="ax-muted">Non attribué</span>'
    if ligne is not None and peut_attribuer:
        options = ''.join('<option value="%s"%s>%s</option>' % (e(k, quote=True), ' selected' if k == resp else '', e(v))
                          for k, v in sorted(membres.items(), key=lambda kv: str(kv[1]).casefold()))
        valeur += ('<details class="d100-agir"><summary>%s</summary><form class="m97-form" data-m97-api="portefeuille/definir">'
                   '<input type="hidden" name="dossier" value="%s"><input type="hidden" name="version" value="%d">'
                   '<label for="d100-resp">Responsable</label><select id="d100-resp" name="responsable_id"><option value="">Non attribué</option>%s</select>'
                   '<button class="ax-btn ghost" type="submit">Enregistrer</button>%s</form></details>') % (
            'Changer' if resp else 'Attribuer', e(mid, quote=True), int(ligne.get('version') or 0), options, statut)
    champs.append(('Responsable', valeur))
    # Prochaine action
    action = (ligne or {}).get('action')
    if ligne is None:
        valeur = '<span class="ax-muted">indisponible</span>'
    elif action:
        cible = _lien(prefix, '/echeances', matter=mid) if action['type'] == 'echeance' else _lien(prefix, '/mes-taches', matter=mid)
        valeur = '<a href="%s">%s</a>%s' % (cible, e(action['libelle']), (' <span class="ax-muted">— %s</span>' % e(_date_fr(action['date']))) if action.get('date') else '')
    else:
        sugg = (ligne or {}).get('suggestion')
        valeur = '<span class="ax-muted">Aucune prochaine action</span>' + (
            (' <span class="d97-suggestion">Suggestion : %s</span>' % e(sugg['libelle'])) if sugg else '')
    if ligne is not None and peut_agir:
        options = ''.join('<option value="%s"%s>%s%s</option>' % (e(c['ref'], quote=True), ' selected' if action and c['ref'] == action.get('ref') else '',
                                                                  e(c['libelle']), (' — ' + e(_date_fr(c['date']))) if c['date'] else '')
                          for c in _candidats(desk, mid, jour))
        choisir = ('<form class="m97-form" data-m97-api="portefeuille/definir"><input type="hidden" name="dossier" value="%s"><input type="hidden" name="version" value="%d">'
                   '<label for="d100-action">Échéance ou tâche du dossier</label><select id="d100-action" name="next_action">%s</select>'
                   '<button class="ax-btn ghost" type="submit">Choisir</button>%s</form>') % (e(mid, quote=True), int(ligne.get('version') or 0), options, statut) if options else ''
        ajouter = ('<form class="m97-form" data-m97-api="portefeuille/action"><input type="hidden" name="dossier" value="%s"><input type="hidden" name="version" value="%d">'
                   '<label for="d100-titre">Nouvelle action</label><input id="d100-titre" name="titre" required maxlength="200">'
                   '<label for="d100-echeance">Pour le</label><input id="d100-echeance" name="echeance" type="date">'
                   '<button class="ax-btn ghost" type="submit">Ajouter</button>%s</form>') % (e(mid, quote=True), int(ligne.get('version') or 0), statut)
        valeur += '<details class="d100-agir"><summary>Choisir la prochaine action</summary>%s%s</details>' % (choisir, ajouter)
    champs.append(('Prochaine action', valeur))
    # Prochaine échéance confirmée (une date proposée reste « À vérifier », comptée à part)
    conf = _lignes(desk, "SELECT id,rule_id,note,due FROM deadlines450 WHERE matter=? AND status IN ('confirmee','manuel') AND due>=? AND due<>'' ORDER BY due, id LIMIT 1", (mid, jour))
    a_verifier = _lignes(desk, "SELECT COUNT(*) FROM deadlines450 WHERE matter=? AND status IN ('a_confirmer','a_completer') AND (due='' OR due>=?)", (mid, jour))
    n_verif = a_verifier[0][0] if a_verifier else 0
    if conf:
        r = conf[0]
        valeur = '<a href="%s">%s — %s</a>' % (_lien(prefix, '/echeances', matter=mid), e(_date_fr(r['due'])), e((r['note'] or str(r['rule_id']).replace('_', ' '))[:120]))
    else:
        valeur = '<span class="ax-muted">Aucune échéance confirmée à venir</span>'
    if n_verif:
        valeur += ' <a class="d97-a-verifier" href="%s">%d date%s à vérifier</a>' % (_lien(prefix, '/echeances', matter=mid), n_verif, '' if n_verif == 1 else 's')
    champs.append(('Prochaine échéance confirmée', valeur))
    # Dernier changement
    dernier = _dernier_changement(desk, a, mid, membres)
    if dernier:
        texte = '%s — %s' % (_date_fr(dernier[0]) or str(dernier[0])[:10], dernier[1])
        valeur = ('<a href="%s">%s</a>' % (e(prefix + dernier[2], quote=True), e(texte))) if dernier[2] else e(texte)
    else:
        valeur = '<span class="ax-muted">Aucun changement enregistré</span>'
    champs.append(('Dernier changement', valeur))
    return ('<dl class="d100-reprise" aria-label="Reprise du dossier">%s</dl><script defer src="%s/static/v5697-mission.js"></script>') % (
        ''.join('<div><dt>%s</dt><dd>%s</dd></div>' % (e(t), v) for t, v in champs), e(prefix, quote=True))


# ------------------------------------------------------------------------------------------------ Conseil et Contentieux
def _compte(desk, sql, params):
    rows = _lignes(desk, sql, params)
    return rows[0][0] if rows and rows[0][0] is not None else 0


def conseil_html(desk, prefix, mid):
    """Points à négocier (revue de contrat), version comparée, obligations après signature : proposées et confirmées."""
    points = _lignes(desk, "SELECT p.clause_titre, p.risque, r.version_id FROM revue_propositions5689 p JOIN revues5689 r ON r.id=p.revue WHERE r.matter=? "
                           "AND p.state IN ('proposee','conflit') ORDER BY CASE p.risque WHEN 'bloquant' THEN 0 WHEN 'eleve' THEN 1 WHEN 'moyen' THEN 2 ELSE 3 END, p.ordre LIMIT 5", (mid,))
    n_points = _compte(desk, "SELECT COUNT(*) FROM revue_propositions5689 p JOIN revues5689 r ON r.id=p.revue WHERE r.matter=? AND p.state IN ('proposee','conflit')", (mid,))
    revue = _lignes(desk, 'SELECT version_id, updated FROM revues5689 WHERE matter=? ORDER BY updated DESC LIMIT 1', (mid,))
    accords = _lignes(desk, "SELECT id,titre,date_signature,statut FROM accords5689 WHERE matter=? AND statut IN ('actif','a_recontroler') ORDER BY date_signature DESC LIMIT 3", (mid,))
    proposees = _compte(desk, "SELECT COUNT(*) FROM occurrences5689 o JOIN accords5689 a ON a.id=o.accord WHERE o.matter=? AND o.etat IN ('proposee','mise_a_jour') "
                              "AND a.statut='actif'", (mid,))
    confirmees = _compte(desk, "SELECT COUNT(*) FROM occurrences5689 o JOIN accords5689 a ON a.id=o.accord WHERE o.matter=? AND o.etat='acceptee' AND a.statut='actif'", (mid,))
    lien = _lien(prefix, '/fiche', matter=mid, onglet='conseil')
    blocs = []
    if n_points:
        blocs.append('<div><h3>Points à négocier <span class="d100-nombre">%d</span></h3><ul>%s</ul></div>' % (n_points, ''.join(
            '<li>%s%s</li>' % (e(p['clause_titre'] or 'Clause'), (' <span class="d100-risque">risque %s</span>' % e(p['risque'])) if p['risque'] else '') for p in points)))
    if revue:
        blocs.append('<div><h3>Version comparée</h3><p>Revue du %s, version <code>%s</code></p></div>' % (e(_date_fr(revue[0]['updated'])), e(str(revue[0]['version_id'])[:12])))
    if accords:
        blocs.append('<div><h3>Obligations après signature</h3><ul>%s</ul><p>%d confirmée%s (inscrite%s) · %d proposée%s, à décider</p></div>' % (
            ''.join('<li>%s — signé le %s%s</li>' % (e(x['titre']), e(_date_fr(x['date_signature'])),
                                                     ' <span class="d97-a-verifier">à recontrôler</span>' if x['statut'] == 'a_recontroler' else '') for x in accords),
            confirmees, '' if confirmees == 1 else 's', '' if confirmees == 1 else 's', proposees, '' if proposees == 1 else 's'))
    corps = ''.join(blocs) or '<p class="ax-muted">Aucune revue de contrat ni accord signé enregistré pour ce dossier.</p>'
    return ('<section class="d100-volet" aria-labelledby="d100-conseil-t"><h2 id="d100-conseil-t">Conseil</h2><div class="d100-volet-grille">%s</div>'
            '<p><a href="%s">Ouvrir le conseil du dossier</a></p></section>') % (corps, lien)


def contentieux_html(desk, prefix, mid, jour):
    """Actes de procédure, pièces probantes, correspondances (séparés), échéances vérifiées, affirmations attribuées à leur partie."""
    cats = {r[0]: (r[1], r[2]) for r in _lignes(desk, "SELECT categorie, SUM(statut='valide'), COUNT(*) FROM contentieux_categories5689 WHERE matter=? GROUP BY categorie", (mid,))}
    pieces = _compte(desk, "SELECT COUNT(*) FROM contentieux_exhibits5689 WHERE matter=? AND statut='actif'", (mid,))
    actes = _compte(desk, 'SELECT COUNT(*) FROM contentieux_actes5689 WHERE matter=?', (mid,))
    verifiees = _lignes(desk, "SELECT due, note, rule_id FROM deadlines450 WHERE matter=? AND status IN ('confirmee','manuel') AND due>=? AND due<>'' ORDER BY due LIMIT 3", (mid, jour))
    affirmations = _lignes(desk, "SELECT texte, affirme_par, statut FROM contentieux_links5689 WHERE matter=? AND affirme_par<>'' AND statut IN ('propose','valide') "
                                 "ORDER BY updated DESC LIMIT 3", (mid,))

    def compte(cle):
        valides, tous = cats.get(cle, (0, 0))
        return '%d (%d validé%s)' % (tous or 0, valides or 0, '' if (valides or 0) == 1 else 's')
    lien = _lien(prefix, '/contentieux', dossier=mid)
    blocs = ['<div><h3>Actes de procédure</h3><p>%s classé%s · %d acte%s préparé%s</p></div>' % (compte('acte_procedure'), '' if cats.get('acte_procedure', (0, 0))[1] == 1 else 's',
                                                                                              actes, '' if actes == 1 else 's', '' if actes == 1 else 's'),
             '<div><h3>Pièces probantes</h3><p>%d pièce%s numérotée%s · %s classée%s comme pièces</p></div>' % (pieces, '' if pieces == 1 else 's', '' if pieces == 1 else 's',
                                                                                                         compte('piece'), '' if cats.get('piece', (0, 0))[1] == 1 else 's'),
             '<div><h3>Correspondances</h3><p>%s</p></div>' % compte('correspondance')]
    blocs.append('<div><h3>Échéances vérifiées</h3>%s</div>' % (('<ul>%s</ul>' % ''.join(
        '<li>%s — %s</li>' % (e(_date_fr(r['due'])), e((r['note'] or str(r['rule_id']).replace('_', ' '))[:100])) for r in verifiees))
        if verifiees else '<p class="ax-muted">Aucune échéance confirmée à venir.</p>'))
    if affirmations:
        blocs.append('<div><h3>Affirmations des parties</h3><ul>%s</ul></div>' % ''.join(
            '<li><span class="d100-attribution">Selon %s</span> : « %s »%s</li>' % (e(r['affirme_par']), e(str(r['texte'])[:180]),
                                                                               '' if r['statut'] == 'valide' else ' <span class="ax-muted">(proposée)</span>') for r in affirmations))
    return ('<section class="d100-volet" aria-labelledby="d100-contentieux-t"><h2 id="d100-contentieux-t">Contentieux</h2><div class="d100-volet-grille">%s</div>'
            '<p><a href="%s">Ouvrir le contentieux du dossier</a></p></section>') % (''.join(blocs), lien)


def volet_nature_html(desk, acteur, prefix, mid, nature):
    """Volet de la synthèse selon la nature du dossier (Conseil ou Contentieux) ; à qualifier : rien."""
    from . import acces5697
    acces5697.exiger(desk, acteur, mid)
    if nature == 'conseil':
        return conseil_html(desk, prefix, mid)
    if nature == 'contentieux':
        return contentieux_html(desk, prefix, mid, jour_cabinet(desk).isoformat())
    return ''


def volet_fiche_html(desk, acteur, prefix, mid, chemin=''):
    """Volet Conseil ou Contentieux de la synthèse : nature du registre unique, sinon devinée du chemin (dossier historique)."""
    from . import dossiers5697 as D
    from .acces5697 import acteur_de
    from .interface56100 import active
    if not active(desk, acteur_de(acteur).compte):
        return ''
    courant = D.resoudre(desk, mid) or mid
    rows = _lignes(desk, 'SELECT nature FROM dossiers5697 WHERE id=?', (courant,))
    nature = rows[0]['nature'] if rows else D._nature_devinee(chemin)
    return volet_nature_html(desk, acteur, prefix, courant, nature)
