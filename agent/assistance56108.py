"""5.6.108 (lot T) : Assistance métier en cinq parcours (nouvelle interface ; ancienne présentation : ``assistance35_ui``).

- **Entrée** (``/assistance-metier``) : choix du dossier parmi les dossiers autorisés (``load_matters()`` filtré ; libellé humain,
  identifiant en valeur, toujours résolu de nouveau par le serveur), puis cinq entrées : Préparer une audience, Examiner les arguments,
  Préparer un appel, Effectuer un calcul, Revoir la facturation (liste fermée ``outil``). Un paramètre inconnu ouvre l'accueil.
- **Lecture seule** : un GET affiche un parcours sans créer de travail ni écrire (aucun schéma créé ici ; l'index des documents est
  ouvert en lecture seule).
- **Soumission** : actions existantes (``coach_hearing35``, ``prepare_call35``, ``record_call35``, ``calculate35``,
  ``billing_review35`` ; ``prepare_legal_opinion`` pour l'avis contradictoire) avec CSRF et validation serveur ; reçu idempotent
  (``assistance35.soumettre``), numéro de demande et état visibles.
- **Résultat** : après ``assistance35.preview`` (garde du dossier), résultat principal, trois réserves au plus (les autres dans un
  détail), sources ouvertes par ``/dossier/fichier`` (le service revérifie dossier et chemin), puis Modifier les données, Reprendre,
  Exporter (droit « exporter ») ; le JSON n'est qu'un diagnostic avancé, jamais tronqué.
- Aucun numéro n'est composé (aucun lien ``tel:``), aucun envoi, dépôt ni facture. Une addition de jours n'est jamais un délai.
- Export : ``GET /api440/m56108/assistance/export?id=…`` (texte, ``Cache-Control: private, no-store``, 404 neutre).
"""
from datetime import datetime
import json
import re
import secrets
import sqlite3
from pathlib import Path
from urllib.parse import urlsplit
from zoneinfo import ZoneInfo

from .common import Stop, fold, load_matters, matter_display
from . import assistance35 as a35

OUTILS = (
    ('audience', 'Préparer une audience', 'Ouvrir la préparation existante, puis s’entraîner : thèmes repérés et thèmes à vérifier.'),
    ('arguments', 'Examiner les arguments', 'Avis contradictoire sourcé, recherche juridique et décisions comparables qualifiées.'),
    ('appel', 'Préparer un appel', 'Questions et documents à relire ; compte rendu à valider après l’appel. Aucun numéro n’est composé.'),
    ('calcul', 'Effectuer un calcul', 'Intérêts, indexation ou addition de jours : résultat, formule, paramètres et source.'),
    ('facturation', 'Revoir la facturation', 'Soldes par devise, diligences à confirmer et date de synchronisation.'),
)
NOMS = {k: t for k, t, _ in OUTILS}
CALCULS = (('interets', 'simple_interest', 'Intérêts simples'), ('indexation', 'rent_indexation', 'Indexation d’un loyer'),
           ('jours', 'calendar_days', 'Addition de jours calendaires'))
OUTIL_DU_TYPE = {'coaching': 'audience', 'appel': 'appel', 'compte_rendu_appel': 'appel', 'calcul': 'calcul', 'facturation': 'facturation'}
TITRES = {'appel': 'Fiche de préparation de l’appel', 'compte_rendu_appel': 'Compte rendu d’appel à valider', 'calcul': 'Résultat du calcul',
          'coaching': 'Bilan de l’entraînement', 'facturation': 'Revue de facturation'}
STATUTS = {'projet_interne': 'Projet interne à relire', 'à relire': 'À relire', 'paramètres_à_valider': 'Paramètres à valider',
           'à valider': 'À valider', 'à vérifier': 'À vérifier'}
ETATS = {'pending': 'Reçue, en attente de traitement', 'running': 'En cours', 'done': 'Résultat prêt', 'error': 'Échec',
         'cancelled': 'Annulée', 'cancel_requested': 'Annulation demandée', 'ignored': 'Non traitée',
         'suspended': 'Suspendue : reprise automatique à la fin de la pause'}
EN_ATTENTE = ('pending', 'running', 'cancel_requested', 'suspended')
MOTIFS = {
    'auteur_non_verifiable': 'Demande retenue : son auteur n’a pas pu être vérifié. Rien n’a été préparé ; relancez-la depuis cette page.',
    'droit_retire': 'Le droit nécessaire a été retiré à ce compte depuis la demande : rien n’a été préparé.',
    'dossier_absent': 'Dossier introuvable ou plus accessible : rien n’a été préparé.',
    'audience_non_prete_pour_coaching': 'Audience introuvable ou pas encore prête pour l’entraînement.',
    'projet_assistance_absent': 'Projet introuvable.',
    'sources_dossier_appel_absentes': 'Aucun document indexé dans ce dossier : la fiche d’appel ne peut pas citer de pièce à relire.',
    'correspondant_appel_non_lie_au_dossier': 'Ce contact n’est pas lié au dossier.',
    'factures_non_synchronisees': 'Factures pas encore synchronisées : lancez d’abord la synchronisation des impayés.',
    'client_invoice_ninja_non_lie': 'Le dossier n’est pas rattaché à un client de l’outil de facturation.',
    'factures_a_actualiser': 'Dernière synchronisation de plus de 24 heures : actualisez les impayés avant la revue.',
    'source_calcul_non_officielle': 'Source refusée : indiquez une page officielle (Légifrance, Insee, Banque de France, Service-Public).',
    'source_calcul_invalide': 'Adresse de la source invalide (HTTPS obligatoire).',
}
CHAMPS_CALCUL = {'principal': 'Principal (€)', 'annual_rate_percent': 'Taux annuel (%)', 'start_date': 'Début', 'end_date_excluded': 'Fin (exclue)',
                 'days': 'Nombre de jours', 'convention': 'Convention de calcul', 'formula': 'Formule', 'rent': 'Loyer de base (€)',
                 'base_index': 'Indice de base', 'new_index': 'Nouvel indice', 'calendar_days_added': 'Jours calendaires ajoutés',
                 'interest': 'Intérêts', 'total': 'Total', 'currency': 'Devise', 'new_rent': 'Nouveau loyer', 'difference': 'Écart',
                 'arithmetic_date': 'Date arithmétique'}
MAX_OPTIONS = 300


# ------------------------------------------------------------------------------------------------ outils
def _h(value):
    from html import escape
    return escape(str(value if value is not None else ''), quote=True)


def _date(value):
    """Date et heure du cabinet (Europe/Paris), jamais l'heure UTC brute."""
    try:
        d = datetime.fromisoformat(str(value).replace('Z', '+00:00'))
        if d.tzinfo is not None:
            d = d.astimezone(ZoneInfo('Europe/Paris'))
        return d.strftime('%d/%m/%Y %H:%M') if 'T' in str(value) else d.strftime('%d/%m/%Y')
    except (TypeError, ValueError):
        return str(value or '')[:16]


def _recu():
    return secrets.token_urlsafe(18)


def _motif(code):
    code = str(code or '').split(':')[0]
    if code in MOTIFS:
        return MOTIFS[code]
    from . import comptes56108   # 5.6.108 (lot C, B05) : compte désactivé, introuvable, annuaire illisible…
    if code in comptes56108.CODES:
        return comptes56108.message(code)
    try:
        from .web import REASONS
        return REASONS.get(code) or code.replace('_', ' ')
    except ImportError:
        return code.replace('_', ' ')


def _form(form, action, label, fields, extra, cle):
    """Formulaire POST existant (CSRF, ``/action``) ; ``data-as108-saisie`` : saisie conservée dans l'onglet (script)."""
    html = form(action, label, {**fields, 'recu': _recu()}, extra)
    return html.replace('<form method="post"', '<form class="as108-form" data-as108-saisie="%s" method="post"' % _h(cle), 1)


def _matieres(desk):
    try:
        return load_matters(desk.c)
    except Stop:
        return None


def _resoudre(matters, valeur):
    """Dossier autorisé désigné par son identifiant (valeur du sélecteur) ; sinon par libellé exact unique."""
    valeur = str(valeur or '').strip()
    if not valeur or matters is None:
        return None
    for m in matters:
        if m['id'] == valeur:
            return m
    cible = fold(valeur)
    trouves = [m for m in matters if cible in [fold(str(x)).strip() for x in [m.get('client_name', ''), matter_display(m)]
                                                   + list(m.get('aliases') or []) + list(m.get('references') or []) if str(x).strip()]]
    return trouves[0] if len(trouves) == 1 else None


def _libelle(m):
    nom = matter_display(m)
    client = str(m.get('client_name') or '')
    return nom if not client or fold(client) in fold(nom) else '%s — %s' % (client, nom)


# ------------------------------------------------------------------------------------------------ page
def page(desk, e, link, form, args, url):
    outil = str(args.get('outil') or '')
    outil = outil if outil in NOMS else ''
    matters = _matieres(desk)
    demande_mid = str(args.get('matter') or '')
    m = _resoudre(matters, demande_mid)
    mid = m['id'] if m else ''
    q = str(args.get('q') or '')
    out = ('<div class="as108" data-as108><header class="as108-tete"><p class="eyebrow">ASSISTANCE MÉTIER</p>'
           '<h2>Que souhaitez-vous préparer ?</h2><p class="as108-aide">Projets internes à relire : aucun appel téléphonique, '
           'dépôt, facture ou envoi n’est effectué ici.</p></header>')
    out += _choix_dossier(matters, m, demande_mid, q, outil, url)
    out += '<nav class="as108-cartes" aria-label="Parcours d’Assistance métier"><ul>'
    for cle, titre, aide in OUTILS:
        cible = url('/assistance-metier', outil=cle, **({'matter': mid} if mid else {}))
        out += ('<li><a class="as108-carte" href="%s"%s><strong>%s</strong><span>%s</span></a></li>'
                % (_h(cible), ' aria-current="page"' if cle == outil else '', _h(titre), _h(aide)))
    out += '</ul></nav>'
    statut = _demande_html(desk, args, url)
    out += statut['html']
    projet = str(args.get('project') or '') or statut['project']
    if projet:
        out += _resultat_section(desk, form, url, projet, matters)
    if outil:
        out += '<section class="as108-parcours" aria-labelledby="as108-titre"><h2 id="as108-titre">%s</h2>' % _h(NOMS[outil])
        out += '<p><a class="ax-btn ghost" href="%s">Retour aux cinq parcours</a></p>' % _h(url('/assistance-metier', **({'matter': mid} if mid else {})))
        if not m:
            out += '<p class="notice">Choisissez d’abord un dossier ci-dessus : ce parcours prépare un projet rattaché à un dossier.</p>'
        else:
            out += {'audience': _audience, 'arguments': _arguments, 'appel': _appel, 'calcul': _calcul,
                    'facturation': _facturation}[outil](desk, form, url, m, args)
        out += '</section>'
    out += _recents(desk, url, mid, matters)
    return out + '<script defer src="%s"></script></div>' % _h(url('/static/v56108-assistance.js'))


def _choix_dossier(matters, m, demande_mid, q, outil, url):
    if matters is None:
        return '<p class="notice">Liste des dossiers indisponible pour l’instant : réessayez dans un instant.</p>'
    filtre = fold(q.strip())
    choix = sorted(matters, key=lambda x: fold(_libelle(x)))
    if filtre:
        choix = [x for x in choix if filtre in fold(' '.join([_libelle(x), x['id']] + [str(v) for v in (x.get('references') or []) + (x.get('aliases') or [])]))]
    total = len(choix)
    choix = choix[:MAX_OPTIONS]
    if m and m not in choix:
        choix = [m] + choix
    options = '<option value="">Choisir un dossier…</option>' + ''.join(
        '<option value="%s"%s>%s</option>' % (_h(x['id']), ' selected' if m and x['id'] == m['id'] else '', _h(_libelle(x))) for x in choix)
    absent = ''
    if demande_mid and not m:
        absent = '<p class="notice" role="status">Dossier introuvable parmi vos dossiers : choisissez-le dans la liste ou cherchez-le.</p>'
        q = q or demande_mid
    trop = ('<p class="as108-aide">%d dossiers correspondent : les %d premiers sont proposés ; précisez la recherche.</p>' % (total, MAX_OPTIONS)
            if total > MAX_OPTIONS else '')
    return ('<form class="as108-dossier" method="get" action="%s">%s<label for="as108-matter">Dossier</label>'
            '<select id="as108-matter" name="matter">%s</select>'
            '<label for="as108-q">Rechercher par client, partie ou référence</label><input id="as108-q" name="q" maxlength="150" value="%s">'
            '<button class="ax-btn" type="submit">Afficher</button></form>%s%s') % (
        _h(url('/assistance-metier')), '<input type="hidden" name="outil" value="%s">' % _h(outil) if outil else '', options, _h(q), absent, trop)


def _demande_html(desk, args, url):
    if not args.get('job'):
        return {'html': '', 'project': ''}
    try:
        d = a35.demande(desk, args.get('job'))
    except (Stop, sqlite3.Error):
        d = None
    if not d:
        return {'html': '<p class="notice" role="status">Demande introuvable.</p>', 'project': ''}
    etat = ETATS.get(d['status'], d['status'])
    attente = ' data-as108-attente' if d['status'] in EN_ATTENTE else ''
    texte = 'Demande n° %d · %s' % (d['job_id'], etat)
    if d['status'] == 'error':
        texte += ' : ' + _motif(d['error'])
    elif d['status'] in ('suspended', 'cancelled', 'ignored') and d.get('message'):
        texte += ' · ' + d['message']
    html = '<p class="as108-etat as108-etat-%s" role="status"%s>%s</p>' % (_h(d['status']), attente, _h(texte))
    if attente:
        html += '<p><a class="ax-btn ghost" href="%s">Actualiser l’état</a></p>' % _h(url('/assistance-metier', job=d['job_id'], matter=d['matter'],
                                                                                        **({'outil': args['outil']} if args.get('outil') in NOMS else {})))
    return {'html': html, 'project': d['project_id'] if d['status'] == 'done' else ''}


# ------------------------------------------------------------------------------------------------ parcours
def _audiences(desk, mid):
    try:
        rows = desk.db.execute("SELECT id,status,data,created FROM hearing_projects_v250 WHERE matter=? AND status IN ('pending','done') "
                               'ORDER BY created DESC LIMIT 20', (mid,)).fetchall()
    except sqlite3.Error:
        return None
    out = []
    for r in rows:
        try:
            prep = (json.loads(r['data'] or '{}') or {}).get('preparation') or {}
        except ValueError:
            prep = {}
        titre = str(prep.get('title') or 'Audience préparée')[:120]
        cadre = str(prep.get('procedural_context') or '')[:90]
        out.append({'id': r['id'], 'label': ' · '.join(x for x in (titre, cadre, _date(r['created'])[:10],
                                                                  'validée' if r['status'] == 'done' else 'à valider') if x)})
    return out


def _audience(desk, form, url, m, args):
    out = ('<p>La préparation (écritures des parties, plans de 5, 10 et 20 minutes, questions probables) se fait dans le parcours d’audience : '
           '<a href="%s">Ouvrir la préparation d’audience du dossier</a>.</p>') % _h(url('/audiences-word', matter=m['id']))
    audiences = _audiences(desk, m['id'])
    if audiences is None:
        return out + '<p class="notice">Audiences préparées indisponibles pour l’instant.</p>'
    if not audiences:
        return out + '<p class="notice">Aucune audience préparée pour ce dossier : préparez-la d’abord, puis revenez vous entraîner.</p>'
    options = ''.join('<option value="%s">%s</option>' % (_h(x['id']), _h(x['label'])) for x in audiences)
    extra = ('<h3>S’entraîner (facultatif)</h3><label for="as108-aud">Audience préparée</label><select id="as108-aud" name="hearing_project_id" required>%s</select>'
             '<label for="as108-speech">Transcription relue de votre entraînement</label>'
             '<textarea id="as108-speech" name="speech" required maxlength="20000" rows="6"></textarea>'
             '<fieldset class="as108-ligne"><legend>Durée réelle</legend><label>Minutes<input type="number" name="duree_minutes" min="0" max="120" inputmode="numeric" required></label>'
             '<label>Secondes<input type="number" name="duree_secondes" min="0" max="59" value="0" inputmode="numeric"></label></fieldset>'
             '<label for="as108-plan">Plan visé</label><select id="as108-plan" name="target_minutes"><option value="5">5 minutes</option>'
             '<option value="10">10 minutes</option><option value="20">20 minutes</option></select>'
             '<p class="as108-aide">Le bilan montre les thèmes du plan repérés et ceux à vérifier (repérage lexical) : ce n’est pas une note.</p>') % options
    return out + _form(form, 'coach_hearing35', 'Établir le bilan de l’entraînement', {'outil': 'audience', 'matter': m['id']}, extra, 'audience:' + m['id'])


def _decisions_candidates(desk, mid):
    """Décisions de première instance indexées dans le dossier (lecture seule de l'index ; aucune table créée)."""
    chemin = Path(desk.c['state_dir']) / 'documents.sqlite3'
    if not chemin.is_file():
        return []
    try:
        db = sqlite3.connect('file:%s?mode=ro' % chemin.as_posix(), uri=True, timeout=2)
        try:
            rows = db.execute('SELECT path FROM docs WHERE matter=? AND (error IS NULL OR error=\'\') ORDER BY modified DESC LIMIT 400', (mid,)).fetchall()
        finally:
            db.close()
    except sqlite3.Error:
        return []
    out = []
    for (path,) in rows:
        nom = fold(str(path).rsplit('/', 1)[-1])
        if any(x in nom for x in ('jugement', 'ordonnance', 'decision')) and nom.rsplit('.', 1)[-1] in ('pdf', 'docx', 'odt', 'txt'):
            out.append(str(path))
    return out[:30]


def _arguments(desk, form, url, m, args):
    out = '<p>%s · %s</p>' % (link_html(url('/orchestrateur-avis'), 'Ouvrir l’orchestrateur d’avis'),
                              link_html(url('/recherche-juridique', matter=m['id']), 'Ouvrir la recherche juridique du dossier'))
    try:
        data = a35.comparables(desk, m['id'])
        frequence = ('%s %%' % data['observed_favorable_percent'] if data['observed_favorable_percent'] is not None
                     else 'série insuffisante (moins de dix décisions)')
        out += ('<p>Décisions comparables qualifiées : %d distincte(s), dont %d favorable(s) ; fréquence observée : %s. Fréquence descriptive, '
                'jamais une probabilité de succès.</p>') % (data['comparable_count'], data['favorable_count'], _h(frequence))
    except (Stop, sqlite3.Error):
        out += '<p class="notice">Décisions comparables indisponibles pour l’instant.</p>'
    decisions = _decisions_candidates(desk, m['id'])
    choix = ''.join('<option value="%s">%s</option>' % (_h(p), _h(p.rsplit('/', 1)[-1])) for p in decisions)
    extra = ('<label for="as108-question">Prétention ou question à examiner</label>'
             '<textarea id="as108-question" name="question" required maxlength="8000" rows="6" '
             'placeholder="Ex. : la clause pénale est-elle réductible ? Position du client : …"></textarea>'
             '<p class="as108-aide">Précisez la position du client et l’écrit concerné : l’avis distingue arguments, preuves, sources et réserves.</p>'
             '<input type="hidden" name="providers" value="openlegi,goodlegal,pappers"><input type="hidden" name="run_research" value="yes">'
             '<label for="as108-critique">Examiner aussi une décision de première instance ?</label>'
             '<select id="as108-critique" name="critique_first_instance"><option value="no">Non</option><option value="yes">Oui</option></select>')
    if choix:
        extra += '<label for="as108-decision">Décision examinée (si oui)</label><select id="as108-decision" name="judgment_source"><option value="">—</option>%s</select>' % choix
    out += '<p class="as108-aide">La préparation utilise l’IA : pendant sa pause, la demande est reçue et attend la reprise ; aucun résultat n’est simulé.</p>'
    out += _form(form, 'prepare_legal_opinion', 'Préparer l’avis contradictoire', {'matter': m['id']}, extra, 'arguments:' + m['id'])
    try:   # 5.6.108 (lot K) : « Demander l'avis du conseil » (annonce, confirmation, conseils du dossier dépliables) ; le service refait ses contrôles
        from . import conseil56108
        from .membres5650 import current_author, viewer_role
        out += conseil56108.arguments_html(desk, url, m['id'], current_author() or 'cabinet', viewer_role())
    except Exception:   # noqa: BLE001 — le parcours reste utilisable ; l'absence est dite
        out += '<p class="notice">Conseil de modèles indisponible pour l’instant.</p>'
    return out


def link_html(href, label):
    return '<a href="%s">%s</a>' % (_h(href), _h(label))


def _appel(desk, form, url, m, args):
    contacts = [c for c in (m.get('correspondents') or []) if isinstance(c, dict) and c.get('email')]
    options = '<option value="">Sans contact désigné</option>' + ''.join(
        '<option value="%s">%s</option>' % (_h(c['email']), _h(' · '.join(x for x in (str(c.get('name') or c['email']), str(c.get('role') or '')) if x)))
        for c in contacts)
    extra = ('<label for="as108-contact">Contact lié au dossier</label><select id="as108-contact" name="contact_email">%s</select>'
             '<label for="as108-objet">Objet de l’appel</label><input id="as108-objet" name="purpose" required maxlength="1000">'
             '<p class="as108-aide">La fiche prépare les questions et les documents à relire. Aucun appel n’est lancé, rien n’est enregistré.</p>') % options
    out = _form(form, 'prepare_call35', 'Préparer la fiche d’appel', {'outil': 'appel', 'matter': m['id']}, extra, 'appel:' + m['id'])
    try:
        fiches = a35.recent(desk, 'appel', m['id'], 10)
    except (Stop, sqlite3.Error):
        return out + '<p class="notice">Fiches d’appel indisponibles pour l’instant.</p>'
    if fiches:
        out += '<h3>Après l’appel : saisir le compte rendu</h3><ul class="as108-liste">'
        for f in fiches:
            out += '<li><a href="%s">Fiche du %s</a> · %s</li>' % (
                _h(url('/assistance-metier', project=f['project_id'], matter=m['id'], outil='appel')), _h(_date(f['created'])),
                _h(STATUTS.get(f['status'], f['status'])))
        out += '</ul>'
    return out


def _calcul(desk, form, url, m, args):
    choix = str(args.get('type') or '')
    out = '<ul class="as108-choix" aria-label="Type de calcul">'
    for cle, _kind, titre in CALCULS:
        out += '<li><a class="ax-btn%s" href="%s"%s>%s</a></li>' % ('' if cle == choix else ' ghost', _h(url('/assistance-metier', outil='calcul', matter=m['id'], type=cle)),
                                                                   ' aria-current="true"' if cle == choix else '', _h(titre))
    out += '</ul>'
    kind = {c: k for c, k, _ in CALCULS}.get(choix)
    if not kind:
        return out + '<p class="as108-aide">Choisissez le calcul : seuls les paramètres correspondants sont demandés. Les calculs n’utilisent pas l’IA.</p>'
    commun = ('<label for="as108-src">Adresse HTTPS officielle de la source</label><input id="as108-src" type="url" name="source_url" required maxlength="1500" '
              'placeholder="https://www.legifrance.gouv.fr/…">'
              '<label for="as108-ref">Référence précise du taux, de l’indice ou de la règle</label><input id="as108-ref" name="source_reference" required maxlength="300">')
    if kind == 'simple_interest':
        champs = ('<label for="as108-p">Principal en euros</label><input id="as108-p" name="principal" required inputmode="decimal">'
                  '<label for="as108-t">Taux annuel en %</label><input id="as108-t" name="annual_rate" required inputmode="decimal">'
                  '<label for="as108-d1">Date de début</label><input id="as108-d1" type="date" name="start_date" required>'
                  '<label for="as108-d2">Date de fin (exclue)</label><input id="as108-d2" type="date" name="end_date" required>')
        note = 'Intérêts simples, jours réels / 365 : le taux légal applicable, les versements et la capitalisation restent à vérifier.'
    elif kind == 'rent_indexation':
        champs = ('<label for="as108-l">Loyer de base en euros</label><input id="as108-l" name="rent" required inputmode="decimal">'
                  '<label for="as108-i1">Indice de base</label><input id="as108-i1" name="base_index" required inputmode="decimal">'
                  '<label for="as108-i2">Nouvel indice</label><input id="as108-i2" name="new_index" required inputmode="decimal">')
        note = 'Loyer initial × nouvel indice ÷ indice de base : vérifier l’indice, les trimestres et la clause du bail.'
    else:
        champs = ('<label for="as108-d0">Date de départ</label><input id="as108-d0" type="date" name="start_date" required>'
                  '<label for="as108-n">Nombre de jours</label><input id="as108-n" type="number" name="days" min="1" max="3650" required inputmode="numeric">')
        note = 'Addition arithmétique uniquement : ce n’est pas un délai de procédure (aucun report, aucun jour férié, aucune échéance créée).'
    extra = champs + commun + '<p class="as108-aide">%s</p>' % _h(note)
    return out + _form(form, 'calculate35', 'Calculer', {'outil': 'calcul', 'type': choix, 'matter': m['id'], 'calculation_type': kind}, extra,
                       'calcul:%s:%s' % (choix, m['id']))


def _facturation(desk, form, url, m, args):
    out = '<p><a class="ax-btn ghost" href="%s">Ouvrir la Facturation pour ce dossier</a></p>' % _h(url('/facturation', vue='dossiers', q=m['id']))
    try:
        from .workstation import unpaid_summary
        s = unpaid_summary(desk, m['id'])
    except (Stop, sqlite3.Error, KeyError, ValueError):
        s = None
    if s is None:
        out += '<p class="notice">État de la facturation indisponible pour l’instant (aucun solde affiché plutôt qu’un faux zéro).</p>'
    else:
        soldes = s.get('balance_by_currency') or {}
        out += '<dl class="as108-dl"><dt>Outil de référence</dt><dd>Invoice Ninja (cache local)</dd><dt>Synchronisation</dt><dd>%s</dd>' % _h(
            'Dernière le ' + _date(s['last_sync']) if s.get('synchronized') and s.get('last_sync') else 'Jamais synchronisé')
        out += '<dt>Client rattaché</dt><dd>%s</dd>' % ('Oui' if s.get('linked') else 'Non : rattachez le client dans la Facturation')
        if s.get('synchronized') and s.get('linked'):
            out += '<dt>Soldes par devise</dt><dd>%s</dd>' % _h(', '.join('%s %s' % (v, k) for k, v in sorted(soldes.items())) or 'Aucun solde ouvert')
        out += '</dl>'
    out += form('refresh_unpaid_invoices', 'Synchroniser les impayés')
    extra = '<p class="as108-aide">Aucune facture, relance ni paiement n’est créé : la revue liste ce qui est à vérifier.</p>'
    return out + _form(form, 'billing_review35', 'Préparer la revue de facturation', {'outil': 'facturation', 'matter': m['id']}, extra, 'facturation:' + m['id'])


# ------------------------------------------------------------------------------------------------ projets
def _recents(desk, url, mid, matters):
    try:
        rows = a35.recent(desk, matter=mid, limit=8)
    except (Stop, sqlite3.Error):
        return '<section class="as108-recents"><h2>Projets récents</h2><p class="notice">Projets indisponibles pour l’instant.</p></section>'
    noms = {x['id']: _libelle(x) for x in (matters or [])}
    out = '<section class="as108-recents"><h2>Projets récents%s</h2>' % (' du dossier' if mid else '')
    if not rows:
        return out + '<p class="as108-aide">Aucun projet pour l’instant.</p></section>'
    out += '<ul class="as108-liste">'
    for r in rows:
        out += '<li><a href="%s">%s</a> · %s · %s · %s</li>' % (
            _h(url('/assistance-metier', project=r['project_id'], matter=r['matter'], outil=OUTIL_DU_TYPE.get(r['kind'], ''))),
            _h(TITRES.get(r['kind'], 'Projet interne')), _h(noms.get(r['matter'], 'Dossier')), _h(_date(r['created'])),
            _h(STATUTS.get(r['status'], r['status'])))
    return out + '</ul></section>'


def _peut_exporter(desk):
    """Le bouton Exporter n'apparaît qu'avec le droit « exporter » (la route le recontrôle par le contrôle central)."""
    from . import membres5650 as mb
    try:
        return bool(mb.effective_rights(desk.c['state_dir'], mb.current_author() or 'cabinet', mb.viewer_role()).get('exporter'))
    except Stop:
        return False


def _resultat_section(desk, form, url, pid, matters):
    try:
        shown = a35.preview(desk, pid)
        m = a35._matter(desk, shown['matter'])
    except Stop:
        return '<section class="as108-resultat"><p class="notice" role="status">Projet introuvable.</p></section>'
    out = '<section class="as108-resultat" aria-labelledby="as108-res">' + resultat_html(shown, _libelle(m), url, m['id'], hid='as108-res')
    outil = OUTIL_DU_TYPE.get(shown.get('kind'), '')
    actions = []
    if outil:
        params = {'outil': outil, 'matter': m['id']}
        if shown.get('kind') == 'calcul':
            params['type'] = {k: c for c, k, _ in CALCULS}.get(shown.get('calculation_type'), '')
        actions.append('<a class="ax-btn ghost" href="%s">Modifier les données</a>' % _h(url('/assistance-metier', **params)))
        actions.append('<a class="ax-btn ghost" href="%s">Reprendre le parcours</a>' % _h(url('/assistance-metier', **params)))
    if _peut_exporter(desk):
        actions.append('<a class="ax-btn ghost" href="%s" download>Exporter (texte)</a>' % _h(url('/api440/m56108/assistance/export', id=shown['project_id'])))
    out += '<p class="as108-actions">%s</p>' % ''.join(actions)
    if shown.get('kind') == 'appel':
        extra = ('<label for="as108-notes">Notes de l’appel réel, saisies après l’échange</label>'
                 '<textarea id="as108-notes" name="notes" required maxlength="12000" rows="6"></textarea>'
                 '<p class="as108-aide">Les notes restent à valider : aucun fait n’est confirmé automatiquement.</p>')
        out += '<h3>Compte rendu de l’appel</h3>' + _form(form, 'record_call35', 'Enregistrer les notes à valider',
                                                          {'outil': 'appel', 'matter': m['id'], 'call_project_id': shown['project_id']}, extra,
                                                          'compte-rendu:' + shown['project_id'])
    out += ('<details class="as108-diagnostic"><summary>Diagnostic avancé (données brutes)</summary><pre>%s</pre></details>'
            % _h(json.dumps(shown, ensure_ascii=False, indent=2)))
    return out + '</section>'


def _liste(titre, elements, limite=None):
    valeurs = [str(v) for v in elements if str(v or '').strip()]
    if not valeurs:
        return ''
    out = '<h3>%s</h3><ul>' % _h(titre)
    visibles = valeurs if limite is None else valeurs[:limite]
    out += ''.join('<li>%s</li>' % _h(v) for v in visibles) + '</ul>'
    if limite is not None and len(valeurs) > limite:
        out += '<details><summary>%d autre(s)</summary><ul>%s</ul></details>' % (len(valeurs) - limite, ''.join('<li>%s</li>' % _h(v) for v in valeurs[limite:]))
    return out


def _source_lien(url, mid, path):
    path = str(path or '')
    if not path.startswith('/'):
        return _h(path)
    return '<a href="%s" target="_blank" rel="noopener">%s</a>' % (_h(url('/dossier/fichier', matter=mid, path=path)), _h(path.rsplit('/', 1)[-1] or path))


def _https(value):
    try:
        u = urlsplit(str(value or ''))
        return u.scheme == 'https' and bool(u.hostname) and not u.username and not u.password
    except ValueError:
        return False


def resultat_html(p, dossier, url, mid, hid='as108-res'):
    """Vue humaine d'un projet déjà autorisé (sortie fraîche de ``preview``, jamais un JSON du navigateur) ; tout est échappé."""
    kind = p.get('kind', '')
    out = '<h2 id="%s">%s</h2><p><strong>%s</strong> · %s · %s</p>' % (
        _h(hid), _h(TITRES.get(kind, 'Projet interne')), _h(dossier), _h(_date(p.get('created'))), _h(STATUTS.get(p.get('status'), p.get('status') or 'À relire')))
    if kind == 'calcul':
        r = p.get('result') or {}
        devise = r.get('currency', '')
        if 'interest' in r:
            principal = 'Intérêts : %s %s · total : %s %s' % (r.get('interest'), devise, r.get('total'), devise)
        elif 'new_rent' in r:
            principal = 'Nouveau loyer : %s %s (écart : %s %s)' % (r.get('new_rent'), devise, r.get('difference'), devise)
        else:
            principal = 'Date arithmétique : %s — ce n’est pas une échéance de procédure.' % _date(r.get('arithmetic_date'))
        out += '<p class="as108-principal">%s</p>' % _h(principal)
        out += '<h3>Paramètres et formule</h3><dl class="as108-dl">'
        for k, v in (p.get('parameters') or {}).items():
            if k in CHAMPS_CALCUL:
                out += '<dt>%s</dt><dd>%s</dd>' % (_h(CHAMPS_CALCUL[k]), _h(_date(v) if k in ('start_date', 'end_date_excluded') else v))
        out += '</dl>'
        s = p.get('source') or {}
        lien = ('<a href="%s" target="_blank" rel="noopener noreferrer">Ouvrir la source</a>' % _h(s.get('url')) if _https(s.get('url')) else 'adresse à vérifier')
        out += '<p>Source saisie : %s · %s · non vérifiée automatiquement.</p>' % (_h(s.get('reference')), lien)
    elif kind == 'appel':
        out += '<p class="as108-principal">Objet : %s</p>' % _h(p.get('purpose'))
        if p.get('contact_email'):
            out += '<p>Contact : %s</p>' % _h(p.get('contact_email'))
        out += _liste('Questions à aborder', p.get('questions_to_ask') or [])
        docs = [d for d in (p.get('documents_to_review') or []) if isinstance(d, dict)]
        if docs:
            out += '<h3>Documents à relire</h3><ul>%s</ul>' % ''.join('<li>%s</li>' % _source_lien(url, mid, d.get('path')) for d in docs)
        out += _liste('Points à vérifier', p.get('points_for_lawyer') or [])
        out += '<p class="as108-aide">Aucun appel lancé, aucun enregistrement, aucune invitation envoyée.</p>'
    elif kind == 'compte_rendu_appel':
        out += '<h3>Notes à valider</h3><p class="as108-notes">%s</p>' % _h(p.get('notes')).replace('\n', '<br>')
        out += _liste('Suites proposées', p.get('next_steps') or [])
    elif kind == 'coaching':
        out += '<p class="as108-principal">Thèmes du plan de %s minutes repérés : %s sur %s (repérage lexical, pas une note).</p>' % (
            _h(p.get('target_minutes')), _h(len(p.get('items_detected') or [])), _h(p.get('plan_items_count')))
        ecart = int(p.get('duration_gap_seconds') or 0)
        out += '<p>Durée : %s s, soit %s s %s le plan.</p>' % (_h(p.get('duration_seconds')), abs(ecart), 'au-delà de' if ecart > 0 else 'en deçà de' if ecart < 0 else 'exactement')
        out += _liste('Thèmes repérés', [x.get('heading', '') for x in p.get('items_detected') or [] if isinstance(x, dict)])
        out += _liste('Thèmes à vérifier', [x.get('heading', '') for x in p.get('items_to_check') or [] if isinstance(x, dict)])
        out += _liste('Questions d’entraînement', [x.get('question', '') for x in p.get('practice_questions') or [] if isinstance(x, dict)])
        srcs = [x for x in p.get('sources') or [] if isinstance(x, dict)]
        if srcs:
            out += '<h3>Sources du plan</h3><ul>%s</ul>' % ''.join('<li>%s</li>' % _source_lien(url, mid, x.get('path')) for x in srcs)
        out += _liste('Limites du bilan', p.get('limitations') or [], 3)
    elif kind == 'facturation':
        soldes = p.get('balance_by_currency') or {}
        out += '<p class="as108-principal">Soldes ouverts : %s</p>' % _h(', '.join('%s %s' % (v, k) for k, v in sorted(soldes.items())) or 'aucun')
        out += '<p>Données synchronisées le %s.</p>' % _h(_date(p.get('last_sync')))
        out += _liste('Factures échues à examiner', ['%s · solde %s %s · échéance %s' % (r.get('number', ''), r.get('balance', ''), r.get('currency', ''),
                                                                                         _date(r.get('due_date')))
                                                    for r in p.get('invoices') or [] if isinstance(r, dict)])
        out += _liste('Diligences à confirmer (temps estimé, distinct du temps réalisé)',
                      [r.get('label', '') for r in p.get('candidate_diligences') or [] if isinstance(r, dict)])
    else:
        out += '<p>Ce type de projet n’a pas de présentation dédiée : voir le diagnostic avancé.</p>'
    out += _liste('Réserves', p.get('warnings') or [], 3)
    return out


def resume_texte(p, dossier):
    """Export en texte du projet autorisé : lisible, sans HTML, sans identifiant de compte."""
    lignes = [TITRES.get(p.get('kind'), 'Projet interne'), 'Dossier : ' + dossier, 'Date : ' + _date(p.get('created')),
              'État : ' + STATUTS.get(p.get('status'), str(p.get('status') or '')), '']
    if p.get('kind') == 'calcul':
        for titre, valeurs in (('Résultat', p.get('result') or {}), ('Paramètres', p.get('parameters') or {})):
            lignes.append(titre + ' :')
            lignes += ['  %s : %s' % (CHAMPS_CALCUL.get(k, k), v) for k, v in valeurs.items()]
        s = p.get('source') or {}
        lignes += ['Source saisie : %s (%s), non vérifiée automatiquement' % (s.get('reference', ''), s.get('url', ''))]
    for cle, titre in (('purpose', 'Objet'), ('notes', 'Notes à valider')):
        if p.get(cle):
            lignes += [titre + ' : ' + str(p[cle])]
    for cle, titre in (('questions_to_ask', 'Questions à aborder'), ('points_for_lawyer', 'Points à vérifier'), ('next_steps', 'Suites proposées'),
                       ('limitations', 'Limites'), ('warnings', 'Réserves')):
        if p.get(cle):
            lignes += [titre + ' :'] + ['  - ' + str(x) for x in p[cle]]
    for cle, titre, champ in (('documents_to_review', 'Documents à relire', 'path'), ('items_detected', 'Thèmes repérés', 'heading'),
                              ('items_to_check', 'Thèmes à vérifier', 'heading'), ('sources', 'Sources', 'path'),
                              ('invoices', 'Factures à examiner', 'number'), ('candidate_diligences', 'Diligences à confirmer', 'label')):
        valeurs = [x.get(champ, '') for x in p.get(cle) or [] if isinstance(x, dict)]
        if valeurs:
            lignes += [titre + ' :'] + ['  - ' + str(x) for x in valeurs]
    lignes += ['', 'Projet interne à relire : aucun appel, envoi, dépôt ni facture n’a été effectué.']
    return '\n'.join(lignes) + '\n'


# ------------------------------------------------------------------------------------------------ route
def api(env, desk, auth, prefix, name, args, method):
    """``GET m56108/assistance/export?id=…`` : projet autorisé exporté en texte ; absent ou interdit : même 404 neutre."""
    from .web440 import json_out
    route = name[len('m56108/assistance/'):].split('?', 1)[0]
    prive = [('Cache-Control', 'private, no-store')]
    if route != 'export':
        return json_out({'error': 'introuvable', 'message': 'Élément introuvable.'}, '404 Not Found')
    if method != 'GET':
        return json_out({'error': 'methode_refusee', 'message': 'Méthode refusée.'}, '405 Method Not Allowed')
    pid = str(args.get('id') or '')
    try:
        shown = a35.preview(desk, pid)
        m = a35._matter(desk, shown['matter'])
    except Stop as ex:
        if str(ex) == 'controle_acces_indisponible':
            return {'status': '503 Service Unavailable', 'kind': 'application/json; charset=utf-8', 'headers': prive,
                    'body': json.dumps({'error': 'controle_acces_indisponible', 'message': 'Contrôle des accès indisponible : rien n’a été exporté.'},
                                       ensure_ascii=False)}
        return {'status': '404 Not Found', 'kind': 'application/json; charset=utf-8', 'headers': prive,
                'body': json.dumps({'error': 'introuvable', 'message': 'Élément introuvable.'}, ensure_ascii=False)}
    nom = re.sub(r'[^A-Za-z0-9_-]+', '-', 'assistance-%s-%s' % (shown.get('kind', 'projet'), shown['project_id'][:8]))
    desk.audit('assistance56108_export', {'project': shown['project_id'], 'matter': m['id']})
    return {'status': '200 OK', 'kind': 'text/plain; charset=utf-8', 'body': resume_texte(shown, _libelle(m)),
            'headers': prive + [('Content-Disposition', 'attachment; filename="%s.txt"' % nom), ('X-Content-Type-Options', 'nosniff')]}
