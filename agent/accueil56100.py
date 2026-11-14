"""5.6.100 (lot E, P2, V100-04) : Aujourd'hui montre d'abord ce qui appelle une action (cahier, §§ 13 et 21).

Derrière ``interface56100.active(desk, owner)`` ; l'ancienne présentation reste entière quand l'interrupteur est éteint.

- Raison et ordre (``classer``) : calculés à partir de l'état local des registres, sans IA ni écriture, dans un ordre stable :
  1. échéances **confirmées** proches (dépassées depuis 30 jours au plus, ou dans les 7 prochains jours) ;
  2. événements datés vérifiés (audiences enregistrées, à venir dans les 7 jours) ;
  3. décisions bloquantes (file « À décider », travaux interrompus en dernier) ;
  4. travaux prêts (projets, documents, réponses préparées, notes) ;
  5. dates tirées d'une pièce, **à vérifier** : une catégorie distincte, jamais une échéance, jamais cachée.
  La raison est un texte fixe ou calculé (date, catégorie) : jamais le texte libre d'une demande ou d'une pièce.
- « Voir la suite » ouvre ``/aujourdhui?vue=file&file=…`` : la même liste, dans le même ordre, paginée côté serveur. Les liens historiques
  ``/aujourdhui?file=…`` (cartes de la file 5.6.51) restent servis par le poste de pilotage.
- Ma journée : l'agenda est lu par ``agenda_lecture5697`` (droits du dossier joints dans la requête), aujourd'hui et demain dans le fuseau du
  cabinet ; « Rien à l'agenda » est distinct de « Dernière synchronisation à … ». Les dates proposées ne sont ni déposées ni comptées comme
  échéances confirmées.

Les dossiers réservés (``hidden``) sont retirés avant tout compte et toute page ; une source illisible s'annonce, jamais comme un zéro.
"""
from datetime import date, datetime, time as dtime, timedelta, timezone
from html import escape as e
import re
from urllib.parse import urlencode

JOURS_PROCHES = 7                      # échéances confirmées et audiences « proches »
JOURS_DEPASSES = 30                    # une échéance confirmée dépassée reste signalée un mois
PAR_PAGE = 30                          # vue « file » paginée
AGENDA_MAX = 6                         # premières entrées datées de Ma journée
RAISON_MAX = 160
CATEGORIES = (('echeance', 'Échéance confirmée'), ('evenement', 'Audience'), ('decision', 'Décision attendue'), ('travail', 'Prêt à relire'),
              ('a_verifier', 'Date à vérifier'))
LIBELLES = dict(CATEGORIES)
RANG = {k: i for i, (k, _) in enumerate(CATEGORIES)}
FILTRES = (('tout', 'Tout'), ('echeance', 'Échéances'), ('evenement', 'Audiences'), ('decision', 'Décisions'), ('travail', 'Relectures'),
           ('a_verifier', 'Dates à vérifier'))
# filtres de la file historique (5.6.51) acceptés dans la vue paginée : même intention, catégories de la 5.6.100
ANCIENS = {'': 'tout', 'decider': 'decision', 'echecs': 'decision', 'relire': 'travail', 'courriels': 'travail', 'documents': 'travail', 'notes': 'travail'}
MOIS = ('janvier', 'février', 'mars', 'avril', 'mai', 'juin', 'juillet', 'août', 'septembre', 'octobre', 'novembre', 'décembre')


def nouvelle(desk, owner):
    """Interrupteur de la nouvelle présentation (réglage illisible : présentation par défaut, active)."""
    try:
        from .interface56100 import active
        return active(desk, owner)
    except Exception:   # noqa: BLE001
        return True


def fuseau(desk):
    from .cockpit530 import tz
    return tz(desk)


def _table(desk, nom):
    return bool(desk.db.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name=?", (nom,)).fetchone())


def _jour(jour, aujourdhui):
    if jour == aujourdhui:
        return 'aujourd’hui'
    if jour == aujourdhui + timedelta(days=1):
        return 'demain'
    if jour == aujourdhui - timedelta(days=1):
        return 'hier'
    return 'le %d %s' % (jour.day, MOIS[jour.month - 1])


def _heure(moment):
    return '%d h %02d' % (moment.hour, moment.minute)


def _court(texte, n=RAISON_MAX):
    texte = re.sub(r'\s+', ' ', str(texte or '')).strip()
    return texte if len(texte) <= n else texte[:n].rsplit(' ', 1)[0] + ' …'


def _sans(hidden, colonne):
    if not hidden:
        return '', []
    return ' AND %s NOT IN (%s)' % (colonne, ','.join('?' for _ in hidden)), sorted(str(h) for h in hidden)


# ------------------------------------------------------------------------------------------------ catégories
def _echeances(desk, labels, hidden, statuts, debut, fin, limite):
    """Échéances lues dans la table du moteur, dossiers réservés retirés dans la requête. Table absente : rien à dire (pas de création ici)."""
    from . import echeances450 as ech
    if not _table(desk, 'deadlines450'):
        return []
    clause, extra = _sans(hidden, 'matter')
    sql = 'SELECT * FROM deadlines450 WHERE status IN (%s)' % ','.join('?' for _ in statuts)
    args = list(statuts)
    if debut is not None:
        sql, args = sql + " AND due<>'' AND due>=? AND due<=?", args + [debut.isoformat(), fin.isoformat()]
    rows = desk.db.execute(sql + clause + " ORDER BY CASE WHEN due='' THEN 1 ELSE 0 END, due, id LIMIT ?", args + extra + [int(limite)]).fetchall()
    out = []
    for row in rows:
        r = ech.public(dict(row))
        out.append((r, labels.get(r['matter']) or r['matter']))     # libellés lus une fois pour toute la liste
    return out


def _echeance_action(prefix, r, label, aujourdhui, confirmee):
    from .accueil_web5696 import T
    try:
        jour = date.fromisoformat(r['due'])
    except (TypeError, ValueError):
        jour = None
    if confirmee:
        quand = ('dépassée depuis %s' % _jour(jour, aujourdhui)) if jour and jour < aujourdhui else _jour(jour, aujourdhui)
        raison = 'Échéance confirmée %s' % quand
        etat, bouton, nature = 'Confirmée', 'Voir l’échéance', 'echeance'
    else:
        raison = 'Date tirée d’une pièce : à vérifier avant de la confirmer (ce n’est pas une échéance)'
        etat, bouton, nature = T('ligne.a_verifier'), T('bouton.echeance'), 'echeance'
    return {'nature': nature, 'cle': r['id'], 'dossier': label, 'objet': '%s — %s' % (r.get('due_label') or T('ligne.date_a_completer'), r['rule_label']),
            'etat': etat, 'bouton': bouton, 'href': prefix + '/echeances#e-' + r['id'], 'categorie': 'echeance' if confirmee else 'a_verifier',
            'raison': raison, 'tri': (r.get('due') or '9999-12-31', r['id'])}


def _audiences(desk, prefix, hidden, labels, debut_utc, fin_utc, z, aujourdhui):
    """Audiences enregistrées (registre ``audiences5688``), à venir, dans la période : événements datés vérifiés."""
    if not _table(desk, 'audiences5688'):
        return []
    from .audiences5688 import TYPES
    clause, extra = _sans(hidden, 'matter')
    rows = desk.db.execute("SELECT id,matter,title,type_audience,juridiction,starts,all_day FROM audiences5688 WHERE statut='a_venir' AND starts>=? AND starts<?"
                           + clause + ' ORDER BY starts, id LIMIT 50', [debut_utc, fin_utc] + extra).fetchall()
    out = []
    for r in rows:
        try:
            debut = datetime.fromisoformat(str(r['starts']).replace('Z', '+00:00'))
            debut = (debut if debut.tzinfo else debut.replace(tzinfo=timezone.utc)).astimezone(z)
        except ValueError:
            continue
        quand = _jour(debut.date(), aujourdhui) + ('' if r['all_day'] else ' à ' + _heure(debut))
        nom = TYPES.get(r['type_audience'], 'Audience')
        objet = ' — '.join(x for x in (r['title'] or nom, r['juridiction']) if x)
        out.append({'nature': '', 'cle': r['id'], 'dossier': labels.get(r['matter'], r['matter'] or ''), 'objet': objet, 'etat': 'Vérifiée',
                    'bouton': 'Voir l’audience', 'href': prefix + '/audiences', 'categorie': 'evenement', 'raison': '%s %s' % (nom, quand),
                    'tri': (debut.astimezone(timezone.utc).isoformat(), r['id'])})
    return out


def _raison_file(item):
    """Raison d'un élément de la file : texte fixe ou calculé par le serveur. Le titre, la question ou la demande d'origine n'y figurent jamais
    (une demande peut contenir une consigne, une pièce un texte adverse)."""
    from .file5651 import CLASSES
    cls = item.get('_classe') or ''
    if cls == 'echec_agent':
        return 'Travail interrompu : relancez-le ou écartez-le'
    if cls == 'courriel':
        return 'Réponse préparée : à relire avant tout envoi'
    if cls == 'note_interne':
        return 'Note interne à ranger dans le dossier'
    if cls in ('projet', 'livrable', 'relecture'):
        quand = str(item.get('_quand') or '')[:10]
        try:
            jour = date.fromisoformat(quand)
            return 'Prêt à relire depuis le %d %s' % (jour.day, MOIS[jour.month - 1])
        except ValueError:
            return 'Prêt à relire'
    if item.get('_priorite') == 'haute' and item.get('_raison_prioritaire'):
        return _court(item['_raison_prioritaire'])
    return 'Décision attendue : %s' % CLASSES.get(cls, 'à examiner').lower()


def classer(desk, prefix, owner, role, hidden, maintenant=None, limite_echeances=200):
    """Toutes les actions, dans l'ordre de lecture d'Aujourd'hui, avec ``categorie`` et ``raison``. Rend ``(actions, complet)`` : une source
    illisible met ``complet`` à faux, jamais un zéro. Aucune écriture, aucun appel au modèle."""
    from .accueil_web5696 import CONFIRMEES, A_VERIFIER, actions_file
    from .livrables5689 import _labels
    z = fuseau(desk)
    now = (maintenant or datetime.now(timezone.utc)).astimezone(z)
    aujourdhui = now.date()
    out, complet = [], True
    try:
        labels, _matters = _labels(desk)
    except Exception:   # noqa: BLE001 — sans libellés, l'identifiant du dossier s'affiche
        labels = {}
    try:
        for r, label in _echeances(desk, labels, hidden, CONFIRMEES, aujourdhui - timedelta(days=JOURS_DEPASSES),
                                   aujourdhui + timedelta(days=JOURS_PROCHES), limite_echeances):
            out.append(_echeance_action(prefix, r, label, aujourdhui, True))
        for r, label in _echeances(desk, labels, hidden, A_VERIFIER, None, None, limite_echeances):
            out.append(_echeance_action(prefix, r, label, aujourdhui, False))
    except Exception:   # noqa: BLE001 — la liste reste utilisable ; la panne est dite
        complet = False
    try:
        debut = datetime.combine(aujourdhui, dtime.min, z)
        out += _audiences(desk, prefix, hidden, labels, now.astimezone(timezone.utc).isoformat(),
                          (debut + timedelta(days=JOURS_PROCHES + 1)).astimezone(timezone.utc).isoformat(), z, aujourdhui)
    except Exception:   # noqa: BLE001
        complet = False
    lignes, ok = actions_file(desk, prefix, owner, role, hidden)
    complet = complet and ok
    from .file5651 import FILTERS
    decider = set(FILTERS['decider']) | {'echec_agent'}
    for n, a in enumerate(lignes):
        cat = 'decision' if a.get('_classe') in decider else 'travail'
        a.update(categorie=cat, raison=_raison_file(a), tri=('', '%06d' % n))
        if cat == 'decision' and a.get('_classe') == 'echec_agent':
            a['tri'] = ('~', '%06d' % n)                   # interrompus en fin de rubrique
        out.append(a)
    out.sort(key=lambda a: (RANG[a['categorie']], a['tri']))
    return out, complet


def comptes(actions):
    n = {k: 0 for k, _ in CATEGORIES}
    for a in actions:
        n[a['categorie']] += 1
    return n


# ------------------------------------------------------------------------------------------------ « Voir la suite »
def filtre_de(valeur):
    valeur = str(valeur or '')
    if valeur in dict(FILTRES):
        return valeur
    return ANCIENS.get(valeur, 'tout')


def suite_href(prefix, filtre='tout', page=1):
    params = {'vue': 'file', 'file': filtre}
    if page > 1:
        params['page'] = page
    return prefix + '/aujourdhui?' + urlencode(params)


def suite_html(prefix, total, premieres):
    """Lien « Voir la suite » : la vue paginée, mêmes objets, même ordre. Aucune carte longue répétée sous un autre défilement."""
    reste = max(0, total - premieres)
    libelle = ('Voir la suite (%d autre%s)' % (reste, 's' if reste > 1 else '')) if reste else 'Voir la file paginée'
    return '<p class="f100-suite"><a class="ax-btn ghost" href="%s" data-f100-suite>%s</a></p>' % (e(suite_href(prefix), quote=True), e(libelle))


def a_verifier_html(prefix, actions, affichees):
    """Dates à vérifier non affichées dans les premières lignes : dites à part, avec leur lien ; jamais cachées."""
    reste = sum(1 for a in actions if a['categorie'] == 'a_verifier') - sum(1 for a in affichees if a['categorie'] == 'a_verifier')
    if reste <= 0:
        return ''
    return '<p class="f100-a-verifier"><a href="%s">%d date%s tirée%s de pièces, à vérifier</a> : ce ne sont pas des échéances confirmées.</p>' % (
        e(suite_href(prefix, 'a_verifier'), quote=True), reste, 's' if reste > 1 else '', 's' if reste > 1 else '')


def vue_file_html(desk, prefix, owner, role, filtre='tout', page=1):
    """Page ``/aujourdhui?vue=file`` : la liste d'Aujourd'hui, filtrée par catégorie et paginée (30 lignes). Mêmes lignes, mêmes boutons,
    même fenêtre de travail que le rang ; les dossiers réservés sont retirés avant comptes et pages."""
    from . import accueil_web5696 as acc, tableau5688
    filtre = filtre_de(filtre)
    try:
        page = max(1, min(int(page or 1), 10000))
    except (TypeError, ValueError):
        page = 1
    hidden = acc._masques(desk, owner, role)
    actions, complet = classer(desk, prefix, owner, role, hidden)
    n = comptes(actions)
    total = len(actions)
    choisies = [a for a in actions if filtre == 'tout' or a['categorie'] == filtre]
    pages = max(1, (len(choisies) + PAR_PAGE - 1) // PAR_PAGE)
    page = min(page, pages)
    tranche = choisies[(page - 1) * PAR_PAGE:page * PAR_PAGE]
    for a in tranche:
        a['lecteur'] = identifiant_lecteur(desk, a['nature'], a['cle'])
    onglets = ''.join('<a href="%s"%s>%s <span class="q51-n">%d</span></a>' % (
        e(suite_href(prefix, k), quote=True), ' aria-current="true"' if k == filtre else '', e(lib), total if k == 'tout' else n[k]) for k, lib in FILTRES)
    if tranche:
        corps = '<ol class="f96-lignes f100-lignes" start="%d">%s</ol>' % ((page - 1) * PAR_PAGE + 1, ''.join(acc.ligne_html(a) for a in tranche))
    elif complet:
        corps = '<p class="f96-vide">%s</p>' % ('Rien dans cette catégorie pour l’instant.' if filtre != 'tout' else 'Rien à décider ni à relire pour l’instant.')
    else:
        corps = ''
    panne = '' if complet else '<p class="f96-panne" role="status">Une source n’a pas pu être lue : la liste peut être incomplète (ce n’est pas un zéro).</p>'
    pager = ''
    if pages > 1:
        liens = []
        if page > 1:
            liens.append('<a class="ax-btn ghost" rel="prev" href="%s">Page précédente</a>' % e(suite_href(prefix, filtre, page - 1), quote=True))
        liens.append('<span class="f100-page">Page %d sur %d</span>' % (page, pages))
        if page < pages:
            liens.append('<a class="ax-btn ghost" rel="next" href="%s">Page suivante</a>' % e(suite_href(prefix, filtre, page + 1), quote=True))
        pager = '<nav class="f100-pages" aria-label="Pages de la file">%s</nav>' % ''.join(liens)
    try:
        prefs = tableau5688.charger(desk, owner)          # personnalisation conservée : accent et densité de la personne
    except Exception:   # noqa: BLE001
        prefs = {'accent': '', 'densite': 'confort'}
    attrs = (' data-accent="%s"' % e(prefs['accent'], quote=True)) if prefs.get('accent') else ''
    attrs += (' data-densite="%s"' % e(prefs['densite'], quote=True)) if prefs.get('densite', 'confort') != 'confort' else ''
    return ('<div class="c530 a89 f100-vue-file"%s><section class="c530-card f96-decider f100-file" id="f100-file" aria-labelledby="f100-file-titre">'
            '<div class="c530-head"><h2 id="f100-file-titre">À décider / À relire</h2><a class="ax-btn ghost" href="%s">Retour à Aujourd’hui</a></div>'
            '<p class="f100-explication">Même liste, même ordre qu’Aujourd’hui : échéances confirmées, audiences, décisions, travaux prêts, puis dates à vérifier.</p>'
            '<nav class="q51-filters" aria-label="Catégories de la file">%s</nav>%s%s%s'
            '<p class="f100-historique"><a href="%s">Cartes détaillées de la file (filtres historiques)</a></p></section>'
            '<script type="application/json" data-f96-messages>%s</script><script defer src="%s"></script>%s</div>') % (
        attrs, e(prefix + '/aujourdhui', quote=True), onglets, panne, corps, pager, e(prefix + '/aujourdhui?file=decider', quote=True), acc._messages(),
        e(prefix + '/static/v5696-accueil.js', quote=True), lecteur_script(prefix))


# ------------------------------------------------------------------------------------------------ Ma journée
def agenda_html(desk, prefix, owner, role, maintenant=None):
    """Premières entrées datées d'aujourd'hui et de demain (fuseau du cabinet), lues par ``agenda_lecture5697`` avec les droits de la personne.
    « Rien à l'agenda » et l'état de synchronisation sont deux informations distinctes ; une lecture impossible est dite comme telle."""
    from .agenda_lecture5697 import evenements_en_ligne
    from .livrables5689 import _labels
    z = fuseau(desk)
    now = (maintenant or datetime.now(timezone.utc)).astimezone(z)
    aujourdhui = now.date()
    debut = datetime.combine(aujourdhui, dtime.min, z)
    try:
        rows = evenements_en_ligne(desk, (owner or 'cabinet', role or 'administrateur'), debut, debut + timedelta(days=2))
        labels, _m = _labels(desk)
    except Exception:   # noqa: BLE001 — l'accueil s'affiche toujours ; la panne est dite
        return '<p class="f96-panne" role="status">Agenda : lecture impossible pour l’instant (ce n’est pas un agenda vide).</p>'
    instant = now.astimezone(timezone.utc).isoformat()
    a_venir = [r for r in rows if str(r.get('ends') or '') > instant][:AGENDA_MAX]
    lignes = []
    for r in a_venir:
        try:
            s = datetime.fromisoformat(str(r['starts']).replace('Z', '+00:00'))
            s = (s if s.tzinfo else s.replace(tzinfo=timezone.utc)).astimezone(z)
        except ValueError:
            continue
        journee = s.time() == dtime.min
        jour = 'Aujourd’hui' if s.date() <= aujourdhui else ('Demain' if s.date() == aujourdhui + timedelta(days=1) else _jour(s.date(), aujourdhui))
        quand = jour + (' · journée' if journee else ' · ' + _heure(s))
        sous = ('<span class="c530-sub">%s</span>' % e(labels.get(r.get('matter'), ''))) if r.get('matter') and labels.get(r.get('matter')) else ''
        lignes.append('<li class="c530-ev f100-ev"><time datetime="%s">%s</time><div><a href="%s">%s</a>%s</div></li>' % (
            e(s.isoformat(), quote=True), e(quand), e(prefix + '/planning?' + urlencode({'vue': 'agenda'}), quote=True), e(r.get('title') or 'Événement'), sous))
    liste = ('<ul class="c530-list f100-agenda">%s</ul>' % ''.join(lignes)) if lignes else '<p class="c530-empty f100-rien">Rien à l’agenda aujourd’hui ni demain.</p>'
    return liste + synchro_html(desk, z, aujourdhui)


def synchro_html(desk, z, aujourdhui):
    """État de la synchronisation de l'agenda en ligne : dernière lecture constatée dans le cache, jamais déduite de l'absence d'événement."""
    try:
        row = desk.db.execute("SELECT MAX(fetched) FROM calendar_cache WHERE calendar_url<>'local:agenda'").fetchone() if _table(desk, 'calendar_cache') else None
    except Exception:   # noqa: BLE001
        row = None
    valeur = str(row[0] or '') if row else ''
    if not valeur:
        return '<p class="c530-note f100-synchro">Agenda en ligne : aucune synchronisation constatée pour l’instant.</p>'
    try:
        moment = datetime.fromisoformat(valeur.replace('Z', '+00:00'))
        moment = (moment if moment.tzinfo else moment.replace(tzinfo=timezone.utc)).astimezone(z)
    except ValueError:
        return '<p class="c530-note f100-synchro">Agenda en ligne : date de synchronisation illisible.</p>'
    quand = _heure(moment) if moment.date() == aujourdhui else '%s à %s' % (_jour(moment.date(), aujourdhui), _heure(moment))
    return '<p class="c530-note f100-synchro">Dernière synchronisation de l’agenda à %s.</p>' % e(quand)


# ------------------------------------------------------------------------------------------------ lecteur commun (lot B)
def lecteur_script(prefix):
    """Script du lecteur commun (lot B), chargé seulement s'il est livré : sans lui, la fenêtre garde son contenu historique."""
    from pathlib import Path
    if not (Path(__file__).parent / 'static' / 'v56100-lecteur.js').is_file():
        return ''
    return '<script defer src="%s"></script>' % e(prefix + '/static/v56100-lecteur.js', quote=True)


def identifiant_lecteur(desk, nature, cle):
    """Identifiant stable de l'objet de relecture (lot B, ``relecture56100``) pour une réponse préparée ; '' si le service ne le fournit pas
    (la fenêtre ouvre alors le détail historique, mêmes droits)."""
    if nature != 'reponse' or not re.fullmatch(r'[a-f0-9]{64}', str(cle or '')):
        return ''
    try:
        from .relecture56100 import CABINET
        if not _table(desk, 'file56100_items'):
            return ''
        row = desk.db.execute("SELECT id FROM file56100_items WHERE cabinet=? AND mail_key=? AND kind='proposition' AND etat='a_relire' ORDER BY updated DESC, id DESC LIMIT 1",
                              (CABINET, str(cle))).fetchone()
    except Exception:   # noqa: BLE001 — projection absente ou illisible : la fenêtre ouvre le détail historique (mêmes droits)
        return ''
    valeur = str(row[0]) if row else ''
    return valeur if re.fullmatch(r'[A-Za-z0-9_-]{1,128}', valeur) else ''
