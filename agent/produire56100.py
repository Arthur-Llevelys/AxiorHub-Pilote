"""5.6.100 (lot E, P4, V100-05) : Produire met les documents prêts au premier plan (cahier, §§ 13 et 23).

Derrière ``interface56100.active(desk, owner)`` ; l'ancienne vue Résultats reste entière quand l'interrupteur est éteint.

- Lecture : ``livrables5689.projection(sync=False)`` (aucun parcours des journaux ni du stockage à l'affichage ; le rattrapage reste au travail
  ``synchro_livrables5689`` du worker). Le schéma est préparé au démarrage (``ensure_schema``, appelé par ``Desk._prepare``). Les natures retenues
  (documents et notes préparés, sans les courriels, qui ont leur filtre) et l'état sont filtrés DANS la requête, avant comptes, curseur et limite.
- Carte : type, dossier, titre, version, état réel (« À relire », « Prêt », « En préparation », « À compléter », « Échec ») ; le lien porte la
  version exacte (``version=``) et le retour vers Produire. Un fichier en préparation dit son état de production ; « Télécharger » attend un
  fichier vérifié.
- Actions, dans cet ordre, selon les droits et capacités calculés par le serveur (``livrables_apercu5689.vue``) : Aperçu, Modifier (éditeur
  configuré), Demander une correction à l'IA (``modifier-ia`` : version de base, empreinte, consigne, zone, clé d'idempotence), Comparer (deux
  versions explicitement lues), Télécharger, Exporter (format et destination choisis explicitement, droits revérifiés au clic et à l'exécution).
- IA en pause : ouvrir, éditer à la main, télécharger et suivre un export restent possibles ; une correction demandée attend la reprise.
- Comparer : chaque version est lue pour elle-même (texte conservé ou fichier dont l'empreinte est celle de la version) ; une version qui n'est plus
  lisible est signalée, jamais remplacée par la dernière.
"""
import difflib
from datetime import date
from html import escape as e
from pathlib import PurePosixPath
from urllib.parse import urlencode
import uuid

from .common import Stop

PAR_PAGE = 10
ONGLETS = (('pret', 'Prêts'), ('en_cours', 'En préparation'), ('echec', 'Échecs et à compléter'), ('tout', 'Tous'))
ETATS_PRODUCTION = {'pret': ('verified',), 'en_cours': ('preparing', 'published'), 'echec': ('failed', 'partial')}
EXPORT_ATTENTE = ('demande', 'en_cours', 'ecriture')
CONVERSION = ('conversion_indisponible', 'conversion_non_verifiee')
LIGNES_MAX = 4000


def familles():
    """Documents et notes préparés ; les réponses aux courriels restent dans le filtre Courriels (même lecteur qu'ailleurs)."""
    from .livrables5689 import NATURES
    return tuple(k for k in NATURES if k != 'courriel')


def ensure_schema(desk):
    """Préparation au démarrage (``Desk._prepare``) : registre, propositions et exports ; un affichage de Produire ne crée rien."""
    from . import livrables5689 as liv, livrables_export5689 as export, propositions5689 as patches
    liv.ensure_schema(desk)
    patches.schema(desk)
    export.schema(desk)


def nouvelle(desk, owner):
    try:
        from .interface56100 import active
        return active(desk, owner)
    except Exception:   # noqa: BLE001
        return True


def _table(desk, nom):
    return bool(desk.db.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name=?", (nom,)).fetchone())


# ------------------------------------------------------------------------------------------------ états
def etat_reel(row):
    """(code, libellé) de l'état réel d'une version : production d'abord, relecture ensuite. Jamais « Prêt » sans fichier vérifié."""
    production, revue = row['production_state'], row['review_state']
    if production in ('preparing', 'published'):
        return 'en_preparation', 'En préparation'
    if production == 'failed':
        return 'echec', 'Échec'
    if production == 'partial' or row.get('reserve') or revue == 'changes_requested':
        return 'a_completer', 'À compléter'
    if revue == 'to_review':
        return 'a_relire', 'À relire'
    return 'pret', 'Prêt'


def etat_export(row):
    """Libellé court de l'export le plus récent d'une version : « En attente », « Export vérifié », « Conversion indisponible » ou l'échec."""
    if row is None:
        return '', ''
    state, erreur = row['state'], str(row['error'] or '')
    if state in EXPORT_ATTENTE:
        return 'attente', 'Export en attente'
    if state == 'exporte':
        return 'verifie', 'Export vérifié'
    if state == 'echec' and erreur.split(':')[0] in CONVERSION:
        return 'conversion', 'Conversion indisponible'
    if state == 'echec':
        return 'echec', 'Échec de l’export'
    return 'arrete', 'Export arrêté'


def ia_en_pause(desk):
    try:
        from .arret5643 import manual_paused
        return bool(manual_paused(desk))
    except Exception:   # noqa: BLE001
        return False


# ------------------------------------------------------------------------------------------------ lecture
def _comptes(desk, hidden, droits):
    """Nombre de documents par onglet, droits compris (même clause que la projection), en une requête."""
    from . import livrables5689 as liv
    base, params = liv._where(hidden, droits, desk=desk)
    kinds = familles()
    rows = desk.db.execute('SELECT production_state, COUNT(*) FROM prepared_items5689' + base + ' AND kind IN (%s) GROUP BY production_state' % ','.join('?' for _ in kinds),
                           list(params) + list(kinds)).fetchall()
    par = {r[0]: r[1] for r in rows}
    out = {k: sum(par.get(s, 0) for s in v) for k, v in ETATS_PRODUCTION.items()}
    out['tout'] = sum(par.values())
    return out


def _lignes(desk, ids):
    if not ids:
        return {}
    return {r['id']: dict(r) for r in desk.db.execute('SELECT * FROM prepared_items5689 WHERE id IN (%s)' % ','.join('?' for _ in ids), list(ids))}


def _versions(desk, docs):
    """Pour chaque document : nombre de versions et version précédente de la version affichée (comparaison explicite)."""
    if not docs:
        return {}
    out = {}
    for r in desk.db.execute('SELECT document_id, version_id, version_number FROM prepared_items5689 WHERE document_id IN (%s) ORDER BY document_id, version_number DESC'
                             % ','.join('?' for _ in docs), list(docs)):
        out.setdefault(r['document_id'], []).append((r['version_number'], r['version_id']))
    return out


def _exports(desk, versions):
    if not versions or not _table(desk, 'prepared_exports5689'):
        return {}
    out = {}
    for r in desk.db.execute('SELECT * FROM prepared_exports5689 WHERE version_id IN (%s) ORDER BY created' % ','.join('?' for _ in versions), list(versions)):
        out[r['version_id']] = r                      # le plus récent l'emporte
    return out


def _date(valeur):
    try:
        jour = date.fromisoformat(str(valeur or '')[:10])
    except ValueError:
        return ''
    return jour.strftime('%d/%m/%Y')


# ------------------------------------------------------------------------------------------------ rendu
def _href(prefix, chemin, **params):
    return prefix + chemin + ('?' + urlencode(params) if params else '')


def carte_html(desk, prefix, row, card, vue, versions, export_row, pause, retour):
    """Une carte compacte : type, dossier, titre, version, état réel, puis les actions permises, dans l'ordre du cahier (§ 23.2)."""
    doc, vid = row['document_id'], row['version_id']
    code, libelle = etat_reel(row)
    detail = _href(prefix, '/livrables/' + doc, version=vid, return_to=retour)
    actions = vue['actions']
    boutons = ['<a class="ax-btn" href="%s">Aperçu<span class="sr-only"> : %s</span></a>' % (e(detail, quote=True), e(row['title']))]
    if actions['editor']:
        from .fiche460 import doc_href
        boutons.append('<a class="ax-btn ghost" href="%s">Modifier<span class="sr-only"> : %s</span></a>' % (e(prefix + doc_href(row['path'], row['matter_id']), quote=True), e(row['title'])))
    if actions['ai_edit']:
        attente = '<p class="p100-note">L’IA est en pause : la demande sera enregistrée et traitée à la reprise. Rien n’est modifié sans votre décision.</p>' if pause else \
            '<p class="p100-note">Le texte avant/après et les sources seront montrés pour décision ; rien n’est modifié sans vous.</p>'
        boutons.append(('<details class="p100-ia"><summary class="ax-btn ghost">Demander une correction à l’IA</summary>'
                        '<form class="m5-form p100-form" data-api="%s">%s<input type="hidden" name="version_id" value="%s"><input type="hidden" name="sha256" value="%s">'
                        '<input type="hidden" name="cle" value="%s"><label>Correction souhaitée<textarea name="instruction" rows="3" minlength="3" maxlength="2000" required></textarea></label>'
                        '<label>Passage visé (facultatif)<input name="zone" maxlength="4000"></label><button class="ax-btn" type="submit">Demander la proposition</button>'
                        '<p role="status" class="m5-status"></p></form></details>') % (
            e('m5689/livrables/%s/modifier-ia' % doc, quote=True), attente, e(vid, quote=True), e(row['sha256'], quote=True), uuid.uuid4().hex))
    liste = versions.get(doc) or []
    precedente = next((v for n, v in liste if n < row['version_number']), '')
    if precedente:
        boutons.append('<a class="ax-btn ghost" href="%s#l100-comparer">Comparer<span class="sr-only"> la version %d à la précédente : %s</span></a>' % (
            e(_href(prefix, '/livrables/' + doc, version=vid, comparer=precedente, return_to=retour), quote=True), row['version_number'], e(row['title'])))
    if actions['download']:
        boutons.append('<a class="ax-btn ghost" href="%s">Télécharger<span class="sr-only"> : %s, version %d</span></a>' % (
            e(_href(prefix, '/api440/m5689/livrables/%s/telecharger' % doc, version=vid), quote=True), e(row['title']), row['version_number']))
    if actions['export']:
        from . import export_tableur5693 as tableur
        formats = tableur.options_html(vue['allowed_formats'])
        dest = ''.join('<option value="%s">%s</option>' % (e(k, quote=True), e(v)) for k, v in vue['destinations'].items())
        conversion = ''
        if PurePosixPath(row['path'] or '').suffix.lower() == '.docx' and 'pdf' not in vue['allowed_formats']:
            conversion = '<p class="p100-note">PDF : conversion indisponible sur cette installation ; le format Word reste exportable.</p>'
        boutons.append(('<details class="p100-export"><summary class="ax-btn ghost">Exporter</summary><form class="p100-form" data-api="%s" data-export="1">'
                        '<input type="hidden" name="version_id" value="%s"><input type="hidden" name="cle" value="%s">'
                        '<label>Format<select name="format" required><option value="">Choisir un format…</option>%s</select></label>'
                        '<label>Destination<select name="destination" required><option value="">Choisir une destination…</option>%s</select></label>%s'
                        '<p class="p100-note">Version %d exactement. L’export n’envoie aucun message au client.</p>'
                        '<button class="ax-btn" type="submit">Lancer l’export</button><p role="status" class="m5-status"></p></form></details>') % (
            e('m5689/livrables/%s/exporter' % doc, quote=True), e(vid, quote=True), uuid.uuid4().hex, formats, dest, conversion, row['version_number']))
    marques = ['<span class="p100-etat" data-etat="%s">%s</span>' % (code, e(libelle))]
    code_export, libelle_export = etat_export(export_row)
    if libelle_export:
        marques.append('<span class="p100-export-etat" data-export="%s">%s</span>' % (code_export, e(libelle_export)))
    progression = ''
    if code == 'en_preparation':
        from .livrables5689 import PRODUCTION
        progression = '<p class="p100-progression" role="status">%s%s. Le téléchargement sera possible une fois le fichier vérifié.</p>' % (
            e(PRODUCTION.get(row['production_state'], 'En préparation')), (' — ' + e(row['state_reason'])) if row.get('state_reason') else '')
    elif code == 'echec' and row.get('state_reason'):
        progression = '<p class="p100-progression">%s</p>' % e(str(row['state_reason']).replace('_', ' '))
    limites = [r for r in vue['unavailable_reasons'] if 'téléchar' in r or 'export' in r.lower() or 'Éditeur' in r]
    return ('<li class="p100-carte" data-livrable="%s" data-version="%s" data-etat="%s"><div class="p100-tete"><span class="p100-type">%s</span><span class="p100-dossier">%s</span></div>'
            '<h3 class="p100-titre"><a href="%s">%s</a></h3><p class="p100-meta"><span>Version %d</span><span>%s</span>%s</p>%s'
            '<div class="p100-actions" role="group" aria-label="%s">%s</div>%s</li>') % (
        e(doc, quote=True), e(vid, quote=True), code, e(card['nature']), e(card['matter_label']), e(detail, quote=True), e(row['title']), row['version_number'],
        e(_date(card['updated_at'])), ''.join(marques), progression, e('Actions sur ' + row['title'], quote=True), ''.join(boutons),
        ('<p class="p100-limites">%s</p>' % e(' '.join(limites[:2]))) if limites else '')


def section_html(desk, prefix, owner, role, args, retour=None):
    """Bloc « Documents prêts » de Produire › Résultats : onglets par état, cartes paginées (curseur), actions permises. Une erreur de lecture
    s'annonce (jamais un zéro) et n'empêche pas le reste de la page."""
    from . import livrables5689 as liv, livrables_apercu5689 as apercu
    from .livrables_web5689 import sources_html
    onglet = str(args.get('docs') or 'pret')
    if onglet not in dict(ONGLETS):
        onglet = 'pret'
    if not retour:         # retour au filtre initial : onglet et page (curseur) de Produire, validés par la visionneuse (``ui_navigation5689``)
        garde = {k: v for k, v in (('docs', onglet if onglet != 'pret' else ''), ('curseur', str(args.get('curseur') or '')[:400])) if v}
        retour = _href(prefix, '/production', **garde)
    hidden, droits = liv.droits_requete()
    try:
        data = liv.projection(desk, hidden, droits, etat='' if onglet == 'tout' else onglet, recence=None, curseur=str(args.get('curseur') or ''), limite=PAR_PAGE,
                              sync=False, prefix=prefix, familles=familles())
        comptes = _comptes(desk, hidden, droits)
    except Stop as ex:
        if str(ex).split(':')[0] == 'curseur_invalide':
            return section_html(desk, prefix, owner, role, {'docs': onglet})
        raise
    except Exception:   # noqa: BLE001 — Produire s'affiche toujours ; la panne est dite
        import logging
        logging.getLogger(__name__).exception('documents prêts')
        return ('<section class="ax-card p100" id="p100-documents" aria-labelledby="p100-titre"><h2 id="p100-titre">Documents prêts</h2>'
                '<p class="l89-alerte" role="status">Lecture du registre impossible pour l’instant : ce n’est pas une liste vide. Réessayez dans un instant.</p></section>')
    rows = _lignes(desk, [c['id'] for c in data['items']])
    versions = _versions(desk, sorted({c['document_id'] for c in data['items']}))
    exports = _exports(desk, [c['version_id'] for c in data['items']])
    caps = apercu.capacites(desk)
    pause = ia_en_pause(desk)
    cartes = []
    for c in data['items']:
        row = rows.get(c['id'])
        if not row:
            continue
        cartes.append(carte_html(desk, prefix, row, c, apercu.vue(desk, row, owner, role, caps), versions, exports.get(row['version_id']), pause, retour))
    onglets = ''.join('<a href="%s"%s>%s <span class="q51-n">%d</span></a>' % (
        e(_href(prefix, '/production', docs=k), quote=True), ' aria-current="true"' if k == onglet else '', e(lib), comptes.get(k, 0)) for k, lib in ONGLETS)
    if cartes:
        corps = '<ul class="p100-cartes">%s</ul>' % ''.join(cartes)
    elif data['complet']:
        corps = '<p class="ax-muted">%s</p>' % {'pret': 'Aucun document prêt pour l’instant. Les documents en préparation sont dans l’onglet suivant.',
                                                 'en_cours': 'Aucun document en préparation.', 'echec': 'Aucun échec ni document à compléter.',
                                                 'tout': 'Aucun document préparé pour l’instant.'}[onglet]
    else:
        corps = '<p class="ax-muted">Rien à afficher, mais une source n’a pas pu être lue : ce n’est pas un zéro certain.</p>'
    suite = ''
    if data['next_cursor']:
        suite = '<p><a class="ax-btn ghost" href="%s">Documents suivants</a></p>' % e(_href(prefix, '/production', docs=onglet, curseur=data['next_cursor']), quote=True)
    elif args.get('curseur'):
        suite = '<p><a class="ax-btn ghost" href="%s">Revenir aux premiers documents</a></p>' % e(_href(prefix, '/production', docs=onglet), quote=True)
    pause_note = '<p class="p100-pause" role="status">IA en pause : ouvrir, modifier à la main, télécharger et suivre un export restent possibles.</p>' if pause else ''
    return ('<section class="ax-card p100" id="p100-documents" aria-labelledby="p100-titre" data-revision="%s"><div class="p100-entete"><h2 id="p100-titre">Documents prêts</h2>'
            '<a class="ax-btn ghost" href="%s">Tous les documents préparés</a></div>%s%s<nav class="q51-filters p100-onglets" aria-label="État des documents">%s</nav>%s%s</section>'
            '<script defer src="%s"></script>') % (
        e(data['revision'], quote=True), e(prefix + '/livrables', quote=True), pause_note, sources_html(data['sources'], True), onglets, corps, suite,
        e(prefix + '/static/v5689-livrables.js', quote=True))


# ------------------------------------------------------------------------------------------------ comparer
def _texte_version(desk, row, client=None):
    """Paragraphes d'une version précise : texte conservé à son enregistrement, sinon le fichier s'il porte exactement l'empreinte de cette version.
    Une version qui n'est plus lisible lève ``version_plus_disponible`` : jamais le contenu d'une autre version à sa place."""
    from . import livrables_apercu5689 as apercu
    if _table(desk, 'documents5697_textes'):
        r = desk.db.execute('SELECT texte FROM documents5697_textes WHERE document_id=? AND version_number=?', (row['document_id'], int(row['version_number']))).fetchone()
        if r:
            return str(r['texte'] or '').split('\n')
    if not row.get('path') or not row.get('sha256'):
        raise Stop('version_plus_disponible')
    _courante, raw = apercu.octets(desk, row, client, version_demandee=row['version_id'])
    ext = PurePosixPath(row['path']).suffix.lower()
    if ext == '.docx':
        from .verification5689 import paragraphes
        return paragraphes(raw)
    if ext in ('.txt', '.md', '.csv'):
        return raw.decode('utf-8', 'replace').split('\n')
    raise Stop('comparaison_format_non_pris_en_charge')


def comparer(desk, row, de_version, hidden=frozenset(), droits=None, client=None):
    """Différences par paragraphe entre la version ``de_version`` et la version ``row`` du même document. Les deux versions sont résolues et
    contrôlées (dossier réservé, droit) avant toute lecture ; une version absente ou illisible est dite comme telle."""
    from . import livrables5689 as liv
    if not de_version or de_version == row['version_id']:
        raise Stop('version_a_comparer_absente')
    ancienne = liv.charger(desk, row['document_id'], hidden, version_id=str(de_version), droits=droits)
    if ancienne['document_id'] != row['document_id']:
        raise Stop('version_inconnue')
    out = {'document_id': row['document_id'], 'de': {'version': ancienne['version_number'], 'version_id': ancienne['version_id']},
           'a': {'version': row['version_number'], 'version_id': row['version_id']}, 'lignes': [], 'changements': 0, 'indisponible': ''}
    try:
        gauche = _texte_version(desk, ancienne, client)
    except Stop as ex:
        out['indisponible'] = 'de'
        out['motif'] = str(ex).split(':')[0]
        return out
    try:
        droite = _texte_version(desk, row, client)
    except Stop as ex:
        out['indisponible'] = 'a'
        out['motif'] = str(ex).split(':')[0]
        return out
    lignes = []
    for op, i1, i2, j1, j2 in difflib.SequenceMatcher(a=gauche, b=droite, autojunk=False).get_opcodes():
        if op == 'equal':
            lignes += [{'op': 'egal', 'texte': x} for x in gauche[i1:i2]]
        else:
            lignes += [{'op': 'retrait', 'texte': x} for x in gauche[i1:i2]] + [{'op': 'ajout', 'texte': x} for x in droite[j1:j2]]
    out['changements'] = sum(1 for x in lignes if x['op'] != 'egal')
    out['lignes'] = lignes[:LIGNES_MAX]
    out['tronque'] = len(lignes) > LIGNES_MAX
    return out


MOTIFS = {'version_plus_disponible': 'Cette version n’est plus lisible dans le stockage (le fichier a changé depuis) : aucune autre version n’est montrée à sa place.',
          'fichier_pas_encore_cree': 'Le fichier de cette version n’existe pas encore.', 'comparaison_format_non_pris_en_charge': 'Comparaison non disponible pour ce format.',
          'document_office_non_lisible': 'Le fichier Word de cette version est illisible.'}


def comparaison_html(desk, row, de_version, hidden=frozenset(), droits=None, client=None):
    """Section « Comparer » de la visionneuse : retraits et ajouts écrits (jamais la couleur seule). Une erreur n'empêche pas la page."""
    try:
        c = comparer(desk, row, de_version, hidden, droits, client)
    except Stop as ex:
        code = str(ex).split(':')[0]
        texte = {'version_inconnue': 'Cette version n’est pas connue du registre.', 'version_a_comparer_absente': 'Choisissez une autre version à comparer.',
                 'dossier_reserve': 'Version non accessible.', 'livrable_absent': 'Version non accessible.'}.get(code, MOTIFS.get(code, 'Comparaison impossible pour l’instant.'))
        return '<section class="ax-card l100-comparer" id="l100-comparer" aria-labelledby="l100-t-comparer"><h2 id="l100-t-comparer">Comparer</h2><p class="l89-alerte">%s</p></section>' % e(texte)
    tete = '<p>Version %d comparée à la version %d.</p>' % (c['a']['version'], c['de']['version'])
    if c['indisponible']:
        cote = 'La version %d' % (c['de']['version'] if c['indisponible'] == 'de' else c['a']['version'])
        corps = '<p class="l89-alerte">%s : %s</p>' % (e(cote), e(MOTIFS.get(c.get('motif'), 'lecture impossible.')))
    elif not c['changements']:
        corps = '<p>Aucune différence de texte entre ces deux versions.</p>'
    else:
        marque = {'retrait': ('del', 'Retiré'), 'ajout': ('ins', 'Ajouté')}
        lignes = []
        for x in c['lignes']:
            if x['op'] == 'egal':
                lignes.append('<li class="l100-egal">%s</li>' % e(x['texte']))
            else:
                balise, mot = marque[x['op']]
                lignes.append('<li class="l100-%s"><span class="l100-marque">%s :</span> <%s>%s</%s></li>' % (x['op'], mot, balise, e(x['texte']), balise))
        corps = '<p>%d paragraphe(s) retiré(s) ou ajouté(s)%s.</p><ol class="l100-diff">%s</ol>' % (
            c['changements'], ' (début seulement)' if c.get('tronque') else '', ''.join(lignes))
    return ('<section class="ax-card l100-comparer" id="l100-comparer" aria-labelledby="l100-t-comparer"><h2 id="l100-t-comparer">Comparer les versions</h2>%s%s'
            '<p class="ax-muted">Comparaison de texte établie par AxiorHub, version par version ; elle ne crée pas de révisions Word.</p></section>') % (tete, corps)
