"""Échéances de procédure (AxiorHub 4.5.0).

Le modèle de langage ne fixe jamais une date. Ce module :
* repère, avec des règles déterministes, l'événement de départ (signification, notification,
  déclaration d'appel, avis...) et sa date, citée telle qu'elle figure dans la pièce ;
* demande le calcul au moteur ``deadlines450`` ;
* tient l'agenda à jour sans doublon (identifiant d'événement stable) ;
* contrôle la cohérence agenda / dossier / actes préparés et émet des rappels en cascade ;
* journalise toute correction manuelle.

5.6.89 (chapitre 38) : le candidat délai vit dans ``delais5689`` (événement, règle versionnée, données, étapes, validation) ; ``depuis_candidat`` porte au suivi une date validée.
Le suivi de procédure et les agendas restent distincts : l'inscription dans un agenda est une action à part.

5.6.96 (lot échéances, D01-D05) : l'acte est **qualifié avant tout calcul** (``qualifier_acte``, partagée par la détection par règles, les
agents documentaires et la détection par modèle) ; la date cherchée reste dans la phrase de l'indice, l'action (« notifiées le ») est exigée
et une date de décision citée (« arrêt du », « Cass. », « CA ») n'est jamais un événement. Une détection ne produit qu'une proposition
(``delais5689``) : aucune ligne de suivi, aucun rappel ni aucune écriture d'agenda avant la décision d'une personne (``INSCRITES``).
Écarter ou annuler est une décision interne : l'agenda n'est repris que sur demande expresse (``retirer_agenda``), après vérification de
l'identifiant, du calendrier et de la version (ETag). Chaque proposition écartée laisse une empreinte (``deadline_ecartes5696``) ; chaque acte
sans délai calculé est consigné avec sa source (``deadline_actes5696``).
"""
from .common import matter_display
from datetime import date, datetime, timedelta, timezone
import json
from pathlib import PurePosixPath
import re
from zoneinfo import ZoneInfo

from . import deadlines450 as engine
from .common import Stop, digest, load_matters
from .notices440 import DATE_NUM, DATE_TEXT, MONTHS, NOTICE_EXTENSIONS, fold_chars

# 5.6.96 (D02) : seules les échéances décidées par une personne sont inscrites à l'agenda, rappelées et contrôlées.
INSCRITES = ('confirmee', 'manuel')
ACTIVE = INSCRITES
STATUS_LABELS = {'a_confirmer': 'À confirmer', 'confirmee': 'Confirmée', 'manuel': 'Corrigée à la main',
                 'a_completer': 'À compléter', 'terminee': 'Terminée', 'annulee': 'Annulée'}
# 5.6.96 (D03) : motifs suggérés pour écarter ou annuler ; la personne peut toujours écrire le sien.
MOTIFS_ECART = ('Arrêt ou jugement cité, pas un événement', 'Acte d’incident : aucun délai au fond', 'Notification non prouvée', 'Doublon')
# 5.6.96 (D04) : états d'agenda d'une échéance close qui attendent une décision (jamais traités automatiquement, sauf relance d'un retrait décidé).
AGENDA_A_DECIDER = ('retrait_propose', 'synchronisation_a_reprendre', 'a_reconcilier')
MAX_ESSAIS_AGENDA = 3
MENTION_SANS_FOND = 'Aucun délai au fond déclenché par cet acte'
MENTION_CALENDRIER = 'Calendrier ou ordonnance de la mise en état : dates à vérifier et à reporter à la main, aucun délai calculé automatiquement'
TYPES_ACTE = {'conclusions_incident': 'Conclusions d’incident (conseiller de la mise en état)', 'ordonnance_calendrier': 'Ordonnance ou calendrier de la mise en état',
              'signification': 'Signification ou notification d’une décision', 'declaration_appel': 'Déclaration d’appel', 'avis_fixation': 'Avis de fixation ou du greffe',
              'conclusions_fond': 'Conclusions au fond', 'decision': 'Jugement, arrêt ou ordonnance', 'autre': 'Acte non qualifié'}
# Textes cités par le cahier des charges : ceux que ``deadlines450`` n'applique pas rendent toute détection « à vérifier », avec la référence.
REFERENCES_CAHIER = (('906-2', 'CPC art. 906-2'), ('908', 'CPC art. 908'), ('909', 'CPC art. 909'), ('910', 'CPC art. 910'), ('912', 'CPC art. 912'),
                     ('913-5', 'CPC art. 913-5'), ('524', 'CPC art. 524 (rédaction en vigueur au 1er octobre 2026)'))
DECRET_2023 = 'décret du 29 décembre 2023, art. 16'
OFFSETS = (30, 14, 7, 2, 0)
OFFSET_LABELS = {30: 'J-30', 14: 'J-14', 7: 'J-7', 2: 'J-2', 0: 'jour J'}
DEFAULT_RULE = {
    'signification_jugement': 'appel_jugement_contentieux',
    'signification_ordonnance_refere': 'appel_ordonnance_refere',
    'declaration_appel': 'conclusions_appelant',
    'signification_declaration_appel': 'constitution_intime',
    'avis_greffe_circuit': 'signification_da_appelant',
    'avis_fixation_bref_delai': 'bref_delai_appelant',
    'conclusions_appelant': 'conclusions_intime',
    'signification_decision_cassation': 'pourvoi_cassation',
}
# (événement, motif des indices, indice obligatoire du contexte)
CUES = (
    ('signification_declaration_appel', r"signification\s+(?:de\s+la\s+)?declaration\s+d.appel|declaration\s+d.appel\s+(?:a\s+ete\s+)?signifiee?", ''),
    ('avis_fixation_bref_delai', r"avis\s+de\s+fixation", r'bref\s+delai|905'),
    ('avis_greffe_circuit', r"avis\s+(?:du\s+greffe|de\s+circuit|d.orientation)|article\s+902", ''),
    ('declaration_appel', r"declaration\s+d.appel", ''),
    ('conclusions_appelant', r"conclusions?\s+(?:de\s+l.appelant|d.appelant)?", r'appelant'),
    ('signification_ordonnance_refere', r"ordonnance\s+de\s+refere", r'signifi|notifi'),
    ('signification_jugement', r"(?:jugement|decision|arret)", r'signifi|notifi'),
)
ACTION = re.compile(r"(?:signifie[es]?\s+(?:a\s+\w+\s+)?|notifie[es]?\s+|signification\s+(?:du\s+\w+\s+)?|notification\s+|"
                    r"en\s+date\s+du\s+|date\s+du\s+|\bdu\s+|\ble\s+|\bremise\s+le\s+|\bformee?\s+le\s+)")
CANDIDATE_NAME = re.compile(r"signif|jugement|ordonnance|declaration d.appel|avis|conclusions|arret|huissier|commissaire de justice|notification")
# 5.6.96 (D01) : indices de qualification, sur le texte replié (minuscules sans accents, positions conservées).
# Acte d'incident : nom du fichier ou en-tête. « Appel incident » est un autre objet (CPC art. 910) : il ne compte pas.
INCIDENT_NOM = re.compile(r"(?<!appel\s)(?<!appels\s)(?<!appel_)(?<!appel-)\bincidents?\b(?!\s+(?:ou|et)\s+(?:appel|provoque))")
INCIDENT_TETE = re.compile(r"conclusions?[\s_.-]+(?:recapitulatives?[\s_.-]+)?(?:d[\s'’_.-]*)?incidents?\b"
                           r"|(?:\ba|\bdevant)\s+(?:madame\s+|monsieur\s+)?(?:le\s+|la\s+)?conseill(?:er|ere)\s+de\s+la\s+mise\s+en\s+etat")
INCIDENT_SAISINE = re.compile(r"specialement\s+adressees?\s+au\s+conseiller\s+de\s+la\s+mise\s+en\s+etat|saisie?\s+par\s+des\s+conclusions\s+specialement\s+adressees")
CALENDRIER = re.compile(r"\bcalendrier\s+de\s+(?:la\s+)?(?:procedure|mise\s+en\s+etat)|\bavis\s+de\s+calendrier"
                        r"|\bordonnance\s+(?:du\s+conseiller\s+de\s+la\s+mise\s+en\s+etat|de\s+(?:la\s+)?mise\s+en\s+etat|fixant\s+(?:le\s+)?calendrier)")
# Date d'une décision citée : précédée de « arrêt du », « jugement rendu le », « Cass. … », « CA Valbrune, »… Jamais un événement de départ.
DECISION_AVANT = re.compile(r"\b(?:arret|jugement|decision|ordonnance|sentence)\b[^.;:\n]{0,50}?\b(?:du|en\s+date\s+du|rendue?\s+le|prononcee?\s+le)\s+$"
                            r"|\bcass\b[^;\n]{0,45}$|\bc\.?\s?a\.?\s+[a-z][^;:\n\d]{1,40},\s*$|\b(?:ce|cjue|cedh)\b[^;\n\d]{0,30},\s*$")
CITATION_APRES = re.compile(r"^[\s,;:()]*(?:n[°o]|cass\b|pourvoi|rg\b|bull|jurisdata|inedit|publie|f-[pbdr]|d\.\s*\d|jcp|gaz\.?\s*pal|rtd|legifrance)")
# Action de notification exigée entre l'indice et la date (« notifiées le », « reçues le », « par RPVA ») ; réserves qui l'annulent.
ACTION_NOTIF = re.compile(r"\b(?:notifi|signifi)\w*|\brecue?s?\b|\breception\b|\brpva\b|e-?barreau")
AVANT_INDICE = re.compile(r"\b(?:notifie|signifie|recu)\w*\s+(?:ses|leurs|les|des|ces|nos|vos)\s+$")
RESERVE_LEAD = re.compile(r"\bn(?:e\s|['’])|\bpas\b|\bjamais\b|\bsera\b|\bseront\b|\bdevr\w+|\bdoi(?:t|vent)\b|\ba\s+notifier\b|\bavant\s+le\b|\bau\s+plus\s+tard\b|\bjusqu.au\b|\bdans\s+le\s+delai\b")
PREUVE = re.compile(r"\b(?:notifi|signifi)\w*\s+(?:[^.;\n]{0,40}?\s)?(?:le|du|en\s+date\s+du)\s+\d|\b(?:notification|signification)\s+(?:du|en\s+date\s+du|faite\s+le|le)\s+\d"
                    r"|accuse\s+de\s+reception|avis\s+de\s+reception|\brpva\b|e-?barreau|\brecue?s?\s+le\s+\d")
ARTICLES = re.compile(r"\bart(?:icles?|s?\.)?\s*((?:\d{3}(?:-\d{1,2})?)(?:\s*(?:,|et|a)\s*\d{3}(?:-\d{1,2})?)*)")
DECRET_2023_RE = re.compile(r"decret\s+(?:n[°o]\s*[\d-]+\s+)?du\s+29\s+decembre\s+2023")
RG_REF = re.compile(r"\b(?:n[°o]\s*)?r\.?\s?g\.?\s*(?:n[°o]\s*)?:?\s*(\d{2}/\d{3,6})")
DESTINATAIRE = re.compile(r"(?:\ba|\bdevant|adressees?\s+a)\s+(?:madame\s+|monsieur\s+)?(?:le\s+|la\s+|l.)?"
                          r"(conseill(?:er|ere)\s+de\s+la\s+mise\s+en\s+etat|premier\s+president|cour\s+d.appel(?:\s+de\s+[a-z'’ -]{2,30})?|tribunal\s+[a-z'’ -]{3,40}|greffe)")


# --------------------------------------------------------------- storage
def ensure_schema(desk):
    desk.db.executescript('''
    CREATE TABLE IF NOT EXISTS deadlines450(
      id TEXT PRIMARY KEY, matter TEXT NOT NULL, rule_id TEXT NOT NULL, start_event TEXT NOT NULL,
      start_date TEXT NOT NULL, regime_date TEXT NOT NULL DEFAULT '', distance TEXT NOT NULL DEFAULT 'none',
      alsace INTEGER NOT NULL DEFAULT 0, due TEXT NOT NULL DEFAULT '', calc TEXT NOT NULL DEFAULT '{}',
      error TEXT NOT NULL DEFAULT '', source_path TEXT NOT NULL DEFAULT '', source_excerpt TEXT NOT NULL DEFAULT '',
      status TEXT NOT NULL, origin TEXT NOT NULL, calendar_uid TEXT NOT NULL DEFAULT '',
      calendar_state TEXT NOT NULL DEFAULT '', act_prepared INTEGER NOT NULL DEFAULT 0, note TEXT NOT NULL DEFAULT '',
      created TEXT NOT NULL, updated TEXT NOT NULL);
    CREATE INDEX IF NOT EXISTS deadlines450_matter ON deadlines450(matter,status);
    CREATE INDEX IF NOT EXISTS deadlines450_due ON deadlines450(due);
    CREATE TABLE IF NOT EXISTS deadline_journal450(
      id INTEGER PRIMARY KEY AUTOINCREMENT, deadline_id TEXT NOT NULL, at TEXT NOT NULL, action TEXT NOT NULL,
      reason TEXT NOT NULL DEFAULT '', before TEXT NOT NULL DEFAULT '{}', after TEXT NOT NULL DEFAULT '{}');
    CREATE TABLE IF NOT EXISTS deadline_reminders450(
      deadline_id TEXT NOT NULL, kind TEXT NOT NULL, at TEXT NOT NULL, PRIMARY KEY(deadline_id,kind));
    CREATE TABLE IF NOT EXISTS deadline_obs450(
      id TEXT PRIMARY KEY, matter TEXT NOT NULL, path TEXT NOT NULL, etag TEXT NOT NULL, status TEXT NOT NULL,
      result TEXT NOT NULL DEFAULT '{}', created TEXT NOT NULL, updated TEXT NOT NULL);
    CREATE TABLE IF NOT EXISTS deadline_actes5696(
      id TEXT PRIMARY KEY, matter TEXT NOT NULL, path TEXT NOT NULL, version TEXT NOT NULL DEFAULT '', page TEXT NOT NULL DEFAULT '',
      type_acte TEXT NOT NULL, mention TEXT NOT NULL DEFAULT '', qualification TEXT NOT NULL DEFAULT '{}', created TEXT NOT NULL);
    CREATE INDEX IF NOT EXISTS deadline_actes5696_m ON deadline_actes5696(matter, created);
    CREATE TABLE IF NOT EXISTS deadline_ecartes5696(
      empreinte TEXT PRIMARY KEY, matter TEXT NOT NULL, evenement TEXT NOT NULL, date_evenement TEXT NOT NULL DEFAULT '',
      source_path TEXT NOT NULL DEFAULT '', extrait TEXT NOT NULL DEFAULT '', motif TEXT NOT NULL, auteur TEXT NOT NULL DEFAULT '',
      origine TEXT NOT NULL DEFAULT '', created TEXT NOT NULL);
    CREATE INDEX IF NOT EXISTS deadline_ecartes5696_m ON deadline_ecartes5696(matter, created);
    ''')
    # 5.6.96 : colonnes ajoutées sans rejouer aucun effet ni certifier une ligne ancienne (version pour le 409, essais de retrait,
    # auteur, clé d'idempotence et résultat agenda au journal).
    for table, column, kind in (('deadlines450', 'revision', 'INTEGER NOT NULL DEFAULT 1'), ('deadlines450', 'calendar_tries', 'INTEGER NOT NULL DEFAULT 0'),
                                ('deadline_journal450', 'actor', "TEXT NOT NULL DEFAULT ''"), ('deadline_journal450', 'cle', "TEXT NOT NULL DEFAULT ''"),
                                ('deadline_journal450', 'calendar', "TEXT NOT NULL DEFAULT ''")):
        if column not in {r[1] for r in desk.db.execute('PRAGMA table_info(%s)' % table)}:
            desk.db.execute('ALTER TABLE %s ADD COLUMN %s %s' % (table, column, kind))
    desk.db.commit()


def enabled(desk):
    return bool(desk.settings('automation:deadlines450_enabled', True))


def _matter(desk, matter_id):
    return next((m for m in load_matters(desk.c) if m['id'] == matter_id), None)


def matter_label(desk, matter_id):
    m = _matter(desk, matter_id)
    return matter_display(m) if m else matter_id


def _row(desk, ident):
    ensure_schema(desk)
    row = desk.db.execute('SELECT * FROM deadlines450 WHERE id=?', (ident,)).fetchone()
    return dict(row) if row else None


def public(row):
    out = dict(row)
    try:
        out['calc'] = json.loads(out.get('calc') or '{}')
    except ValueError:
        out['calc'] = {}
    out['status_label'] = STATUS_LABELS.get(out['status'], out['status'])
    try:
        out['rule_label'] = engine.rule(out['rule_id'])['label']
    except engine.DeadlineError:
        out['rule_label'] = out['rule_id']
    out['start_event_label'] = engine.start_event_label(out['start_event'])
    if out.get('due'):
        out['due_label'] = engine.fr_date(date.fromisoformat(out['due']))
    return out


# ------------------------------------------------------------ extraction
def _dates_after(folded, pos, span=110):
    """Dates avec année explicite situées juste après ``pos`` (jamais d'année devinée)."""
    zone = folded[pos:pos + span]
    found = []
    for match in DATE_TEXT.finditer(zone):
        if not match[3]:
            continue
        try:
            found.append((match.start(), date(int(match[3]), MONTHS[match[2]], int(match[1])), match.end()))
        except ValueError:
            continue
    for match in DATE_NUM.finditer(zone):
        year = int(match[3])
        year += 2000 if year < 100 else 0
        try:
            found.append((match.start(), date(year, int(match[2]), int(match[1])), match.end()))
        except ValueError:
            continue
    return sorted(found)


def decisions_citees(folded):
    """5.6.96 (D01) : dates de décisions citées (« arrêt du … », « Cass. 2e civ., … », « CA Valbrune, … »), avec leur position.
    La recherche en arrière s'arrête à la date précédente : une décision citée ne contamine pas la date suivante."""
    out, fin_precedente = [], 0
    for pos, when, end in _dates_after(folded, 0, span=len(folded) + 1):
        avant = folded[max(fin_precedente, pos - 60):pos]
        fin_precedente = max(fin_precedente, end)
        if DECISION_AVANT.search(avant):
            out.append({'position': pos, 'date': when.isoformat(), 'citation': bool(CITATION_APRES.search(folded[end:end + 40])),
                        'extrait': re.sub(r'\s+', ' ', folded[max(0, pos - 40):end + 30]).strip()})
    return out


def _articles_du_moteur():
    """Numéros d'articles que ``deadlines450`` applique (données versionnées) : « CPC art. 906-2 » donne « 906-2 »."""
    connus = set()
    for r in engine.rules():
        for a in r.get('articles', []):
            connus.update(re.findall(r'\d{3}(?:-\d{1,2})?', a))
    return connus


def qualifier_acte(text, path='', page='', version=''):
    """5.6.96 (D01) : qualification de l'acte **avant tout calcul**, partagée par la détection par règles (``extract_start_events``), les
    agents documentaires (``procedure568.source_events``) et la détection par modèle (``delais5689``). Ne statue sur aucun délai : dit ce
    qu'est l'acte, à qui il s'adresse, sur quel RG, quelle preuve de notification il porte, quelles dates sont des décisions citées et
    quelles règles spéciales citées le moteur ne sait pas calculer. ``sans_calcul`` : aucun délai n'est calculé automatiquement."""
    text = str(text or '')
    folded = fold_chars(text)
    nom = fold_chars(PurePosixPath(str(path or '')).name)
    tete = fold_chars(text[:400])
    entete = fold_chars(text[:1200])
    incident = bool(INCIDENT_NOM.search(nom) or INCIDENT_NOM.search(tete) or INCIDENT_TETE.search(nom + '\n' + tete) or INCIDENT_SAISINE.search(entete))
    calendrier = not incident and bool(CALENDRIER.search(nom + '\n' + tete))
    zone = nom + '\n' + tete
    if incident:
        type_acte = 'conclusions_incident'
    elif calendrier:
        type_acte = 'ordonnance_calendrier'
    elif re.search(r'\bsignifi|\bsignification\b|commissaire\s+de\s+justice|\bhuissier\b|\bnotification\s+d', zone):
        type_acte = 'signification'
    elif re.search(r"declaration\s+d.appel", zone):
        type_acte = 'declaration_appel'
    elif re.search(r"\bavis\s+(?:de\s+fixation|du\s+greffe|de\s+circuit|d.orientation)", zone):
        type_acte = 'avis_fixation'
    elif re.search(r'\bconclusions?\b', zone):
        type_acte = 'conclusions_fond'
    elif re.search(r'\b(?:jugement|arret|ordonnance)\b', zone):
        type_acte = 'decision'
    else:
        type_acte = 'autre'
    m = DESTINATAIRE.search(entete)
    destinataire = m.group(1).strip() if m else ('conseiller de la mise en etat' if incident else '')
    instance = ('appel' if re.search(r"cour\s+d.appel|declaration\s+d.appel|\bappelante?s?\b|\bintimee?s?\b|mise\s+en\s+etat", folded) else
                'cassation' if re.search(r'cour\s+de\s+cassation|\bpourvoi\b', folded) else 'premiere_instance' if re.search(r'\btribunal\b', folded) else '')
    preuve = PREUVE.search(folded)
    mention_preuve = ''
    if preuve:
        debut = max(folded.rfind('. ', 0, preuve.start()) + 1, folded.rfind('\n', 0, preuve.start()) + 1, 0)
        fins = [i for i in (folded.find('. ', preuve.end()), folded.find('\n', preuve.end())) if i != -1]
        mention_preuve = re.sub(r'\s+', ' ', text[debut:min(fins) if fins else len(text)]).strip(' .')[:300]
    cites = set()
    for a in ARTICLES.finditer(folded):
        cites.update(re.findall(r'\d{3}(?:-\d{1,2})?', a.group(1)))
    connus = _articles_du_moteur()
    non_calculees = [label for num, label in REFERENCES_CAHIER if num in cites and num not in connus]
    if DECRET_2023_RE.search(folded):
        non_calculees.append(DECRET_2023)
    decisions = decisions_citees(folded)
    expresses = []
    if calendrier:
        # cahier D01 : un calendrier propose ses dates expresses « à vérifier » (jamais inscrites), avec leur passage
        citees = {d['position'] for d in decisions}
        expresses = [{'date': when.isoformat(), 'extrait': re.sub(r'\s+', ' ', text[max(0, pos - 80):end + 10]).strip()}
                     for pos, when, end in _dates_after(folded, 0, span=len(folded) + 1) if pos not in citees][:10]
    return {'type_acte': type_acte, 'type_libelle': TYPES_ACTE[type_acte], 'incident': incident, 'calendrier': calendrier,
            'sans_calcul': incident or calendrier, 'mention': MENTION_SANS_FOND if incident else (MENTION_CALENDRIER if calendrier else ''),
            'destinataire': destinataire, 'rg': list(dict.fromkeys(RG_REF.findall(folded)))[:5], 'instance': instance,
            'preuve_notification': bool(preuve), 'mention_notification': mention_preuve,
            'decisions_citees': [{k: d[k] for k in ('date', 'citation', 'extrait')} for d in decisions][:20], 'dates_expresses': expresses,
            'references_non_calculees': non_calculees, 'source': str(path or '')[:500], 'page': str(page or '')[:40], 'version': str(version or '')[:200]}


def rg_du_dossier(desk, matter_id):
    """Numéros RG des procédures actives connues du dossier (``modele5651``), normalisés ; vide si rien n'est connu."""
    try:
        rows = desk.db.execute("SELECT rg FROM d51_procedures WHERE matter=? AND statut='active' AND rg<>''", (matter_id,)).fetchall()
    except Exception:   # noqa: BLE001 : table absente, rien de connu
        return []
    return sorted({re.sub(r'[^0-9/]', '', str(r[0])) for r in rows} - {''})


def motifs_de_verification(qualification, evenement, date_evenement, declaration='', nature='', rg_dossier=()):
    """5.6.96 (D01) : ce qui rend une détection « à vérifier » au lieu d'une date proposée. Liste vide : rien à signaler."""
    motifs = []
    rg_acte = {re.sub(r'[^0-9/]', '', r) for r in qualification.get('rg') or []}
    if rg_acte and rg_dossier and not rg_acte & set(rg_dossier):
        motifs.append('RG de l’acte (%s) différent de celui du dossier (%s) : à vérifier.' % (', '.join(sorted(rg_acte)), ', '.join(rg_dossier)))
    if declaration and evenement != 'declaration_appel' and str(date_evenement) < str(declaration):
        motifs.append('Date antérieure à la déclaration d’appel du %s connue du dossier : à vérifier.' % declaration)
    if nature == 'redaction':
        motifs.append('Date de rédaction de l’acte, réception non prouvée : à vérifier.')
    motifs += ['Règle spéciale citée (%s) que le moteur ne calcule pas : à vérifier.' % ref for ref in qualification.get('references_non_calculees', [])]
    return motifs


def extract_start_events(text, today=None, source_path='', qualification=None):
    """Événements de départ trouvés dans ``text``. Chaque date figure littéralement dans le texte.
    5.6.96 (D01) : un acte d'incident ou un calendrier ne donne aucun événement ; la date est cherchée **dans la phrase** de l'indice ;
    la date d'une décision citée n'en est jamais un ; des conclusions exigent l'action de notification (« notifiées le ») entre l'indice
    et la date, sans négation ni futur. Chaque événement porte la nature de sa date (notification, acte, rédaction)."""
    today = today or date.today()
    q = qualification or qualifier_acte(text, source_path)
    if q['sans_calcul']:
        return []
    folded = fold_chars(text)
    citees = {d['position'] for d in decisions_citees(folded)}
    out, seen = [], set()
    for event, cue_re, need in CUES:
        for cue in re.finditer(cue_re, folded):
            sentence_start = max(folded.rfind('. ', 0, cue.start()), folded.rfind('\n\n', 0, cue.start()), 0)
            ending = [i for i in (folded.find('. ', cue.end()), folded.find('\n\n', cue.end())) if i != -1]
            sentence_end = min(ending) if ending else len(folded)
            window = folded[sentence_start:min(sentence_end, cue.end() + 140)]
            if need and not re.search(need, window):
                continue
            if event == 'signification_jugement' and not re.search(r'signifi|notifi', window[cue.start() - sentence_start:]):
                continue
            if event == 'declaration_appel' and re.search(r'signifi', window):
                continue
            # 5.6.96 (D01) : « conclusions de l'intimé » ou « conclusions d'incident » ne sont pas des conclusions de l'appelant au fond.
            if event == 'conclusions_appelant' and re.match(r"\s*(?:de\s+l.|des\s+|d.)(?:intime|incident)", folded[cue.end():cue.end() + 24]):
                continue
            # 5.6.96 (D01) : la date reste dans la phrase de l'indice et n'est jamais celle d'une décision citée.
            after = [d for d in _dates_after(folded, cue.end(), span=max(0, min(sentence_end - cue.end(), 140)))
                     if d[1] <= today + timedelta(days=2) and d[1].year >= 2000 and cue.end() + d[0] not in citees]
            chosen = None
            for offset, when, end in after:
                lead = folded[cue.end():cue.end() + offset]
                # la date visée suit l'indice (« signifié le ... », « en date du ... ») : une autre date
                # d'abord citée (« jugement du 5 mars, signifié le 12 mars ») ne passe que si l'action la précède.
                if event in ('signification_jugement', 'signification_ordonnance_refere') and not re.search(r'signifi|notifi|signification|notification', folded[cue.start():cue.end() + offset + 1]):
                    continue
                # 5.6.96 (D01) : des conclusions ne font courir un délai qu'une fois notifiées : l'action est exigée entre l'indice et la
                # date (ou juste avant l'indice : « a notifié ses conclusions le … »), sans négation, futur ni date limite.
                if event == 'conclusions_appelant' and (not (ACTION_NOTIF.search(lead) or AVANT_INDICE.search(folded[max(0, cue.start() - 40):cue.start()]))
                                                         or RESERVE_LEAD.search(lead)):
                    continue
                chosen = (when, cue.end() + offset, cue.end() + end)
                break
            if not chosen:
                continue
            key = (event, chosen[0])
            if key in seen:
                continue
            seen.add(key)
            phrase = folded[sentence_start:chosen[1]]
            if event == 'declaration_appel':
                nature = 'acte'
            elif event in ('avis_fixation_bref_delai', 'avis_greffe_circuit'):
                nature = 'notification' if ACTION_NOTIF.search(phrase) else 'redaction'
            else:
                nature = 'notification'
            context = re.sub(r'\s+', ' ', text[max(0, cue.start() - 60):chosen[2] + 40]).strip()
            out.append({'event': event, 'date': chosen[0], 'context': context[:300],
                        'rule_id': DEFAULT_RULE.get(event, ''),
                        'alternatives': [r['id'] for r in engine.rules() if r['start_event'] == event],
                        'nature_date': nature, 'type_acte': q['type_acte']})
    return sorted(out, key=lambda e: (e['date'], e['event']))


def looks_like_candidate(path):
    p = PurePosixPath(path)
    return p.suffix.lower() in NOTICE_EXTENSIONS and bool(CANDIDATE_NAME.search(fold_chars(p.stem)))


# ------------------------------------------------------------- creation
def _journal(desk, ident, action, reason='', before=None, after=None, actor='', cle='', calendar=''):
    """5.6.96 (D05) : motif, auteur, horodatage, ancien et nouvel état, résultat agenda et clé d'idempotence."""
    desk.db.execute('INSERT INTO deadline_journal450(deadline_id,at,action,reason,before,after,actor,cle,calendar) VALUES(?,?,?,?,?,?,?,?,?)',
                    (ident, desk.now(), action, reason, json.dumps(before or {}, ensure_ascii=False, default=str),
                     json.dumps(after or {}, ensure_ascii=False, default=str), str(actor or '')[:160], str(cle or '')[:200], str(calendar or '')[:80]))


def _deja_decide(desk, ident, cle):
    """Même clé d'idempotence déjà journalisée pour cette échéance : la décision est prise, on ne la rejoue pas (double clic, renvoi)."""
    cle = str(cle or '').strip()
    return bool(cle) and desk.db.execute('SELECT 1 FROM deadline_journal450 WHERE deadline_id=? AND cle=?', (ident, cle)).fetchone() is not None


def _version_attendue(row, version):
    """409 si la page qui décide n'a pas vu la dernière version (second onglet). Les appels anciens, sans version, restent acceptés."""
    if version in (None, ''):
        return
    try:
        if int(version) != int(row['revision']):
            raise Stop('version_en_conflit')
    except (TypeError, ValueError):
        raise Stop('version_en_conflit') from None


# ------------------------------------------------- 5.6.96 : actes consignés, propositions écartées
def _normaliser(extrait):
    return re.sub(r'\s+', ' ', fold_chars(str(extrait or ''))).strip()[:300]


def empreinte_ecart(matter, evenement, date_evenement, source_path, extrait):
    """Empreinte stable d'une proposition : événement (type d'acte), date, source et extrait normalisé. Une autre source ou une autre date
    est une preuve nouvelle, présentée séparément."""
    return digest('ecart5696|%s|%s|%s|%s|%s' % (matter, evenement, str(date_evenement or '')[:10], source_path or '', _normaliser(extrait)))


def deja_ecarte(desk, matter, evenement, date_evenement, source_path, extrait):
    """Même empreinte, ou même événement à la même date dans la même source (extrait relu autrement) : pas une preuve nouvelle."""
    ensure_schema(desk)
    return desk.db.execute('SELECT 1 FROM deadline_ecartes5696 WHERE empreinte=? OR (matter=? AND evenement=? AND date_evenement=? AND source_path=?)',
                           (empreinte_ecart(matter, evenement, date_evenement, source_path, extrait), matter, evenement, str(date_evenement or '')[:10],
                            str(source_path or '')[:500])).fetchone() is not None


def memoriser_ecart(desk, matter, evenement, date_evenement, source_path, extrait, motif, auteur='', origine='echeance'):
    """5.6.96 (D05) : la décision d'écarter survit à la relecture du fichier ; le même faux positif n'est pas reproposé."""
    ensure_schema(desk)
    desk.db.execute('INSERT OR IGNORE INTO deadline_ecartes5696(empreinte,matter,evenement,date_evenement,source_path,extrait,motif,auteur,origine,created) '
                    'VALUES(?,?,?,?,?,?,?,?,?,?)', (empreinte_ecart(matter, evenement, date_evenement, source_path, extrait), matter, evenement,
                                                    str(date_evenement or '')[:10], str(source_path or '')[:500], _normaliser(extrait), str(motif or '')[:300],
                                                    str(auteur or '')[:160], origine, desk.now()))
    desk.db.commit()


def ecarts(desk, matter='', limit=100):
    ensure_schema(desk)
    sql, args = 'SELECT * FROM deadline_ecartes5696', []
    if matter:
        sql, args = sql + ' WHERE matter=?', [matter]
    return [dict(r) for r in desk.db.execute(sql + ' ORDER BY created DESC LIMIT ?', args + [int(limit)])]


def consigner_acte(desk, matter, path, version, qualification, mention='', page=''):
    """5.6.96 (D01) : l'acte qualifié est consigné avec sa source, sa version et sa page ; un acte d'incident porte la mention
    « Aucun délai au fond déclenché par cet acte ». Une même version d'un même fichier n'est consignée qu'une fois."""
    ensure_schema(desk)
    ident = digest('acte5696|%s|%s|%s|%s' % (matter, path, version, page))
    desk.db.execute('INSERT OR IGNORE INTO deadline_actes5696(id,matter,path,version,page,type_acte,mention,qualification,created) VALUES(?,?,?,?,?,?,?,?,?)',
                    (ident, matter, str(path)[:500], str(version or '')[:200], str(page or '')[:40], qualification['type_acte'],
                     str(mention or qualification.get('mention') or '')[:300], json.dumps(qualification, ensure_ascii=False), desk.now()))
    desk.db.commit()
    return ident


def actes_consignes(desk, matter='', limit=50):
    ensure_schema(desk)
    sql, args = 'SELECT * FROM deadline_actes5696', []
    if matter:
        sql, args = sql + ' WHERE matter=?', [matter]
    out = []
    for r in desk.db.execute(sql + ' ORDER BY created DESC LIMIT ?', args + [int(limit)]):
        row = dict(r)
        try:
            row['qualification'] = json.loads(row['qualification'] or '{}')
        except ValueError:
            row['qualification'] = {}
        row['type_libelle'] = TYPES_ACTE.get(row['type_acte'], row['type_acte'])
        out.append(row)
    return out


def declaration_connue(desk, matter_id):
    """Date de la déclaration d'appel connue : profil de procédure du dossier (``procedure568``), sinon une déclaration au suivi."""
    try:
        from .procedure568 import case_profile
        declared = str(case_profile(desk, matter_id).get('declaration_date') or '')
    except Exception:   # noqa: BLE001 : profil illisible, on se rabat sur le suivi
        declared = ''
    return declared or regime_for(desk, matter_id)


def regime_for(desk, matter_id):
    ensure_schema(desk)
    row = desk.db.execute("SELECT start_date FROM deadlines450 WHERE matter=? AND start_event='declaration_appel' "
                          "AND status NOT IN ('annulee') ORDER BY start_date DESC LIMIT 1", (matter_id,)).fetchone()
    return row[0] if row else ''


def _compute_fields(rule_id, start, distance, alsace, regime_date):
    try:
        result = engine.compute(rule_id, start, distance=distance, alsace=alsace, regime_date=regime_date or None)
        return result['due'], json.dumps(result, ensure_ascii=False), ''
    except engine.DeadlineError as ex:
        return '', '{}', ex.message


def create_deadline(desk, matter_id, rule_id, start, distance='none', alsace=False, regime_date='', source_path='',
                    excerpt='', origin='auto', status='a_confirmer', note=''):
    """Crée (ou retrouve) l'échéance. Idempotent : même dossier, règle, départ et régime = même ligne."""
    ensure_schema(desk)
    if not _matter(desk, matter_id):
        raise Stop('dossier_absent')
    spec = engine.rule(rule_id)
    start = str(start)
    date.fromisoformat(start)
    if spec.get('regime') and not regime_date:
        regime_date = regime_for(desk, matter_id)
        if not regime_date and spec['start_event'] == 'declaration_appel':
            regime_date = start
    ident = digest('deadline450|%s|%s|%s' % (matter_id, rule_id, start))
    existing = _row(desk, ident)
    if existing:
        return public(existing) | {'created_now': False}
    due, calc, error = _compute_fields(rule_id, start, distance, alsace, regime_date)
    if error:
        status = 'a_completer'
    now = desk.now()
    desk.db.execute('''INSERT INTO deadlines450(id,matter,rule_id,start_event,start_date,regime_date,distance,alsace,due,calc,
        error,source_path,source_excerpt,status,origin,calendar_uid,calendar_state,act_prepared,note,created,updated)
        VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)''',
                    (ident, matter_id, rule_id, spec['start_event'], start, regime_date or '', distance, int(bool(alsace)), due, calc,
                     error, source_path[:500], excerpt[:400], status, origin, '', '', 0, note[:500], now, now))
    _journal(desk, ident, 'creation', origin, {}, {'rule': rule_id, 'start': start, 'due': due, 'status': status})
    desk.db.commit()
    desk.audit('echeance_450_creee', {'matter': matter_id, 'rule': rule_id, 'origin': origin, 'due': due})
    return public(_row(desk, ident)) | {'created_now': True}


def depuis_candidat(desk, dav, candidat, owner='cabinet', agenda=False):
    """5.6.89 : date validée par un avocat sur une règle validée : elle rejoint le suivi, sans doublon. Une correction garde la même échéance (même événement d'agenda) et note que l'agenda est à reprendre.
    L'agenda n'est écrit que sur demande expresse."""
    ensure_schema(desk)
    calcul, matter = candidat['calcul'], candidat['matter']
    start, due, distance, alsace, regime = candidat['date_evenement'], calcul['date'], calcul.get('distance', 'none'), bool(calcul.get('alsace')), calcul.get('regime_date', '')
    detail = json.dumps({'steps': calcul['etapes'], 'warnings': calcul['reserves'], 'rule_id': candidat['base'], 'regle': candidat['rule_id'], 'regle_version': candidat['rule_version'], 'candidat': candidat['id'], 'rules_version': calcul.get('moteur', ''), 'due': due}, ensure_ascii=False)
    ident = candidat.get('deadline_id') or digest('deadline450|%s|%s|%s' % (matter, candidat['base'], start))
    row = _row(desk, ident)
    if not row:
        ident = create_deadline(desk, matter, candidat['base'], start, distance, alsace, regime, candidat.get('source_path', ''), candidat.get('citation', ''), 'candidat5689', 'confirmee')['id']
        row = _row(desk, ident)
    a_reprendre = bool(row['calendar_uid']) and row['due'] != due
    desk.db.execute("UPDATE deadlines450 SET rule_id=?,start_date=?,regime_date=?,distance=?,alsace=?,due=?,calc=?,error='',status='confirmee',calendar_state=?,updated=? WHERE id=?",
                    (candidat['base'], start, regime, distance, int(alsace), due, detail, 'a_resynchroniser' if a_reprendre else row['calendar_state'], desk.now(), ident))
    _journal(desk, ident, 'validation_candidat', 'Candidat %s validé par %s' % (candidat['id'][:12], str(owner)[:80]), {'due': row['due'], 'status': row['status']}, {'due': due, 'status': 'confirmee'})
    desk.db.commit()
    state = sync_calendar(desk, dav, ident) if agenda and dav is not None else ''
    return public(_row(desk, ident)) | {'calendar': state}


# --------------------------------------------------------------- agenda
def _calendar_urls(desk):
    cfg = desk.c.get('calendar', {})
    return cfg.get('urls', []), cfg.get('timezone', 'Europe/Paris')


def _title(desk, row):
    prefix = {'a_confirmer': 'Échéance À CONFIRMER – ', 'terminee': 'TERMINÉE – ', 'annulee': 'ANNULÉE – '}.get(row['status'], 'Échéance – ')
    try:
        label = engine.rule(row['rule_id'])['label']
    except engine.DeadlineError:
        label = row['rule_id']
    return (prefix + label + ' – ' + matter_label(desk, row['matter']))[:200]


def _description(row):
    calc = json.loads(row.get('calc') or '{}')
    lines = ['Créée par AxiorHub (moteur de délais, aucune date déduite par un modèle).']
    lines += calc.get('steps', [])
    if row.get('origin') == 'manuel' or row.get('status') == 'manuel':
        lines.append('Date corrigée à la main (voir le journal).')
    if row.get('source_path'):
        lines.append('Source : ' + row['source_path'])
    return '\n'.join(lines)[:1900]


def sync_calendar(desk, dav, ident):
    """Crée ou met à jour l'unique événement de l'échéance (UID stable : pas de doublon)."""
    row = _row(desk, ident)
    if not row or not row['due']:
        return ''
    if row['status'] not in INSCRITES:
        # 5.6.96 (D02) : une proposition (à confirmer, à compléter) ou une échéance close ne produit ni PUT ni proposition d'écriture
        # (``autonomy480.pending_add``), quel que soit le niveau d'autonomie de l'agenda.
        return 'non_validee'
    urls, tz_name = _calendar_urls(desk)
    if not urls or not desk.settings('automation:deadline_calendar450', True):
        state = 'non_configure'
    else:
        tz = ZoneInfo(tz_name)
        when = date.fromisoformat(row['due'])
        start = datetime(when.year, when.month, when.day, 9, 0, tzinfo=tz)
        title, description = _title(desk, row), _description(row)
        state = ''
        from . import autonomy480
        if not autonomy480.allows(desk, 'agenda_echeance', 'agir'):
            autonomy480.pending_add(desk, 'agenda_echeance', row['matter'] if 'matter' in row.keys() else '', title,
                {'uid': ident, 'title': title, 'start': start.isoformat(), 'end': (start + timedelta(minutes=30)).isoformat(),
                 'description': description, 'deadline_id': ident})
            desk.db.execute('UPDATE deadlines450 SET calendar_state=?,updated=? WHERE id=?', ('a_valider', desk.now(), ident))
            desk.db.commit()
            return 'a_valider'
        for replace in ((True, False) if row['calendar_uid'] else (False, True)):
            try:
                uid = dav.put_event(urls[0], ident, title, start, start + timedelta(minutes=30), description, replace=replace)
                state = 'mis_a_jour' if replace else 'cree'
                desk.db.execute('UPDATE deadlines450 SET calendar_uid=? WHERE id=?', (uid, ident))
                break
            except Stop as ex:
                if str(ex) != 'http_412' and str(ex) != 'http_404':
                    state = 'erreur:' + str(ex)[:60]
                    break
                state = 'erreur:' + str(ex)
    desk.db.execute('UPDATE deadlines450 SET calendar_state=?,updated=? WHERE id=?', (state, desk.now(), ident))
    desk.db.commit()
    return state


# -------------------------------------------------------- user actions
def _need_reason(reason):
    reason = re.sub(r'\s+', ' ', str(reason or '')).strip()
    if len(reason) < 3:        # 5.6.96 (D03) : même seuil que les candidats du contentieux
        raise Stop('motif_obligatoire')
    return reason[:300]


def _preuve_detection(desk, row, regime_date=''):
    """5.6.96 (D02) : une détection automatique ne se confirme que sur preuve : l'extrait requalifié n'est pas un acte d'incident ni un
    calendrier, porte le même événement à la même date avec une notification (ou l'acte lui-même), et la date ne précède pas la
    déclaration d'appel connue. Sinon : correction motivée (« Corriger la date ») ou écart. Une saisie humaine n'est pas concernée."""
    if row['origin'] != 'auto' or row['status'] not in ('a_confirmer', 'a_completer'):
        return
    q = qualifier_acte(row['source_excerpt'], row['source_path'])
    if q['incident']:
        raise Stop('acte_incident_sans_delai_au_fond')
    if q['sans_calcul']:
        raise Stop('preuve_notification_a_verifier')
    declared = regime_date or declaration_connue(desk, row['matter'])
    if declared and row['start_event'] != 'declaration_appel' and row['start_date'] < declared:
        raise Stop('depart_anterieur_declaration_a_verifier')
    trouves = extract_start_events(row['source_excerpt'], date.fromisoformat(row['start_date']), row['source_path'], q)
    if not any(e['event'] == row['start_event'] and e['date'].isoformat() == row['start_date'] and e['nature_date'] != 'redaction' for e in trouves):
        raise Stop('preuve_notification_a_verifier')


def confirm(desk, dav, ident, rule_id='', distance=None, alsace=None, regime_date=None, actor='', version=None, cle=''):
    row = _row(desk, ident)
    if not row:
        raise Stop('echeance_absente')
    if _deja_decide(desk, ident, cle):
        return public(row) | {'calendar': row['calendar_state'], 'deja_decide': True}
    if row['status'] in ('terminee', 'annulee'):
        raise Stop('echeance_close')
    _version_attendue(row, version)
    _preuve_detection(desk, row, regime_date or '')
    before = {'rule': row['rule_id'], 'due': row['due'], 'status': row['status']}
    rule_id = rule_id or row['rule_id']
    spec = engine.rule(rule_id)
    if spec['start_event'] != row['start_event']:
        raise Stop('regle_incompatible_avec_evenement')
    distance = row['distance'] if distance is None else distance
    alsace = bool(row['alsace']) if alsace is None else bool(alsace)
    regime_date = row['regime_date'] if regime_date is None else regime_date
    keep_manual = (row['status'] == 'manuel' and rule_id == row['rule_id'] and distance == row['distance']
                   and alsace == bool(row['alsace']) and (regime_date or '') == row['regime_date'])
    if keep_manual:   # une date corrigée à la main reste telle quelle jusqu'à nouvelle correction
        due, calc, error, status = row['due'], row['calc'], '', 'manuel'
    else:
        due, calc, error = _compute_fields(rule_id, row['start_date'], distance, alsace, regime_date)
        status = 'a_completer' if error else 'confirmee'
    desk.db.execute('UPDATE deadlines450 SET rule_id=?,distance=?,alsace=?,regime_date=?,due=?,calc=?,error=?,status=?,revision=revision+1,updated=? WHERE id=?',
                    (rule_id, distance, int(alsace), regime_date or '', due, calc, error, status, desk.now(), ident))
    desk.db.commit()
    state = sync_calendar(desk, dav, ident) if due else ''
    _journal(desk, ident, 'confirmation', '', before, {'rule': rule_id, 'due': due, 'status': status}, actor, cle, state)
    desk.db.commit()
    desk.audit('echeance_450_confirmee', {'id': ident[:12], 'due': due})
    return public(_row(desk, ident)) | {'calendar': state}


def correct(desk, dav, ident, reason, new_due='', start_date='', rule_id='', distance=None, regime_date=None, actor='', version=None, cle=''):
    """Correction manuelle, toujours motivée et journalisée ; l'événement d'agenda est remplacé, jamais dupliqué."""
    reason = _need_reason(reason)
    row = _row(desk, ident)
    if not row:
        raise Stop('echeance_absente')
    if _deja_decide(desk, ident, cle):
        return public(row) | {'calendar': row['calendar_state'], 'deja_decide': True}
    if row['status'] in ('terminee', 'annulee'):
        raise Stop('echeance_close')       # 5.6.96 : une échéance écartée ou annulée ne revient pas par une correction
    _version_attendue(row, version)
    before = {'rule': row['rule_id'], 'start': row['start_date'], 'due': row['due'], 'status': row['status']}
    if new_due:
        try:
            forced = date.fromisoformat(str(new_due))
        except ValueError:
            raise Stop('date_invalide') from None
        calc = json.loads(row['calc'] or '{}')
        calc.setdefault('steps', []).append('Date corrigée à la main le %s : %s (motif : %s).' % (
            desk.now()[:10], engine.fr_date(forced), reason))
        desk.db.execute("UPDATE deadlines450 SET due=?,calc=?,error='',status='manuel',revision=revision+1,updated=? WHERE id=?",
                        (forced.isoformat(), json.dumps(calc, ensure_ascii=False), desk.now(), ident))
        after = {'due': forced.isoformat(), 'status': 'manuel'}
    else:
        start = start_date or row['start_date']
        try:
            date.fromisoformat(start)
        except ValueError:
            raise Stop('date_invalide') from None
        rid = rule_id or row['rule_id']
        if engine.rule(rid)['start_event'] != row['start_event']:
            raise Stop('regle_incompatible_avec_evenement')
        dist = row['distance'] if distance is None else distance
        regime = row['regime_date'] if regime_date is None else regime_date
        due, calc, error = _compute_fields(rid, start, dist, bool(row['alsace']), regime)
        status = 'a_completer' if error else 'confirmee'
        desk.db.execute('UPDATE deadlines450 SET rule_id=?,start_date=?,distance=?,regime_date=?,due=?,calc=?,error=?,status=?,revision=revision+1,updated=? WHERE id=?',
                        (rid, start, dist, regime or '', due, calc, error, status, desk.now(), ident))
        after = {'rule': rid, 'start': start, 'due': due, 'status': status}
    desk.db.commit()
    state = sync_calendar(desk, dav, ident)
    _journal(desk, ident, 'correction', reason, before, after, actor, cle, state)
    desk.db.commit()
    desk.audit('echeance_450_corrigee', {'id': ident[:12], 'reason': reason[:80]})
    return public(_row(desk, ident)) | {'calendar': state}


def close(desk, dav, ident, outcome, reason='', actor='', version=None, cle='', ecart=False):
    """Décision interne : accomplie (``terminee``), devenue sans objet (``annulee``) ou fausse détection écartée (``annulee`` et ``ecart``).
    5.6.96 (D04) : aucun appel à l'agenda, donc jamais un événement créé ni renommé. Si un événement AxiorHub est lié à une échéance
    annulée ou écartée, son retrait est **proposé** (``retrait_propose``) et n'a lieu que sur demande expresse (``retirer_agenda``).
    (D05) : journal complet, propositions d'agenda en attente closes, empreinte mémorisée pour un écart."""
    if outcome not in ('terminee', 'annulee'):
        raise Stop('statut_invalide')
    ecart = bool(ecart) and outcome == 'annulee'
    reason = _need_reason(reason) if outcome == 'annulee' else str(reason or '')[:300]
    row = _row(desk, ident)
    if not row:
        raise Stop('echeance_absente')
    if _deja_decide(desk, ident, cle):
        return public(row) | {'calendar': row['calendar_state'], 'deja_decide': True}
    if row['status'] in ('terminee', 'annulee'):
        raise Stop('echeance_close')
    _version_attendue(row, version)
    if outcome == 'annulee' and row['calendar_uid']:
        state = 'retrait_propose'
    elif outcome == 'annulee':
        state = 'non_inscrite'
    else:
        state = row['calendar_state']        # accomplie : l'événement passé reste tel quel dans l'agenda
    action = 'ecartee' if ecart else outcome
    note = ('Écartée : ' + reason)[:500] if ecart else row['note']
    desk.db.execute('UPDATE deadlines450 SET status=?,calendar_state=?,note=?,revision=revision+1,updated=? WHERE id=?',
                    (outcome, state, note, desk.now(), ident))
    _journal(desk, ident, action, reason, {'status': row['status'], 'calendar_state': row['calendar_state']},
             {'status': outcome, 'calendar_state': state}, actor, cle, state)
    desk.db.commit()
    from . import autonomy480
    fermees = autonomy480.pending_close_for_deadline(desk, ident, 'echeance_' + action)
    if ecart:
        memoriser_ecart(desk, row['matter'], row['start_event'], row['start_date'], row['source_path'], row['source_excerpt'], reason, actor, 'echeance')
    desk.audit('echeance_450_close', {'id': ident[:12], 'outcome': outcome, 'ecart': ecart, 'calendar': state, 'propositions_closes': fermees})
    return public(_row(desk, ident)) | {'calendar': state}


def _uid_propre(row):
    """L'identifiant d'agenda mémorisé est celui que cette échéance a créé elle-même : jamais celui d'une autre entrée."""
    return bool(row['calendar_uid']) and row['calendar_uid'] == 'axiorhub-%s@mail-agent.local' % row['id']


def _retrait(desk, dav, row):
    """Retrait de l'événement lié : UID propre, calendrier configuré, date, origine AxiorHub et version (ETag) relus juste avant.
    Rend l'état d'agenda. Un écart à l'une de ces vérifications : rien n'est effacé (« à vérifier à la main »)."""
    if not _uid_propre(row) or not row['due']:
        return 'a_reconcilier'
    urls, tz_name = _calendar_urls(desk)
    if not urls:
        return 'a_reconcilier'
    tz = ZoneInfo(tz_name)
    jour = date.fromisoformat(row['due'])
    debut = datetime.combine(jour, datetime.min.time(), tz)
    uid = row['calendar_uid']
    trouves = [x for x in dav.events(urls, debut - timedelta(days=400), debut + timedelta(days=400), tz_name) if x.get('uid') == uid]
    if not trouves:
        return 'absent_agenda'
    if len(trouves) > 1:
        return 'a_reconcilier'
    event = trouves[0]
    calendrier = None
    for url in urls:
        lien = dav.calendar_url(url) if hasattr(dav, 'calendar_url') else url
        if lien == event.get('source_url'):
            calendrier = url
            break
    etag = str(event.get('etag') or '')
    if (calendrier is None or not etag or str(event.get('start') or '')[:10] != row['due']
            or not str(event.get('description') or '').startswith('Créée par AxiorHub')):
        return 'a_reconcilier'
    try:
        dav.delete_event(calendrier, uid, etag=etag)
    except Stop as ex:
        if str(ex) in ('http_412', 'evenement_externe_lecture_seule'):
            return 'a_reconcilier'      # l'événement a changé depuis la lecture, ou n'est pas le nôtre : la main reprend
        raise
    return 'retiree'


def retirer_agenda(desk, dav, ident, actor='', version=None, cle='', verifie_main=False):
    """5.6.96 (D04) : demande expresse de retirer l'événement d'agenda d'une échéance annulée ou écartée. Aucun UID : zéro PUT, zéro DELETE.
    UID d'une autre entrée : rien. Coupure : état local juste, « synchronisation à reprendre », relance bornée (``MAX_ESSAIS_AGENDA``).
    ``verifie_main`` : la personne a repris l'agenda elle-même (cas « à vérifier à la main ») ; rien n'est lu ni écrit dans l'agenda."""
    row = _row(desk, ident)
    if not row:
        raise Stop('echeance_absente')
    if _deja_decide(desk, ident, cle):
        return public(row) | {'calendar': row['calendar_state'], 'deja_decide': True}
    if row['status'] != 'annulee':
        raise Stop('echeance_non_annulee')
    _version_attendue(row, version)
    tries = row['calendar_tries']
    if verifie_main:
        state = 'verifie_a_la_main'
    elif not row['calendar_uid']:
        state = 'non_inscrite'
    else:
        from . import pause5629
        pause5629.checkpoint(desk)      # motif d'arrêt relu avant l'effet : une pause n'est pas une coupure et ne compte pas d'essai
        try:
            state = _retrait(desk, dav, row)
            tries = 0
        except (Stop, OSError, ValueError, KeyError):
            state, tries = 'synchronisation_a_reprendre', tries + 1
    uid = '' if state in ('retiree', 'absent_agenda') else row['calendar_uid']
    desk.db.execute('UPDATE deadlines450 SET calendar_state=?,calendar_uid=?,calendar_tries=?,revision=revision+1,updated=? WHERE id=?',
                    (state, uid, tries, desk.now(), ident))
    _journal(desk, ident, 'retrait_agenda', '', {'calendar_state': row['calendar_state']}, {'calendar_state': state}, actor, cle, state)
    desk.db.commit()
    desk.audit('echeance_450_retrait_agenda', {'id': ident[:12], 'state': state})
    return public(_row(desk, ident)) | {'calendar': state}


def reprendre_retraits(desk, dav, limit=20):
    """Relance bornée des retraits décidés par une personne et restés en attente faute d'agenda joignable : au plus ``MAX_ESSAIS_AGENDA``
    essais, puis la main reprend (bouton « Réessayer »). Aucun autre effet n'est rejoué."""
    ensure_schema(desk)
    if dav is None:
        return 0
    from . import pause5629
    repris = 0
    for r in desk.db.execute("SELECT id FROM deadlines450 WHERE status='annulee' AND calendar_state='synchronisation_a_reprendre' AND calendar_tries<? LIMIT ?",
                             (MAX_ESSAIS_AGENDA, int(limit))).fetchall():
        try:
            pause5629.checkpoint(desk)
        except Stop:
            break
        retirer_agenda(desk, dav, r['id'], actor='relance automatique')
        repris += 1
    return repris


def a_decider_agenda(desk):
    """Échéances closes dont l'événement d'agenda attend une décision (retrait proposé, reprise, vérification à la main)."""
    ensure_schema(desk)
    rows = [public(dict(r)) for r in desk.db.execute('SELECT * FROM deadlines450 WHERE status=? AND calendar_state IN (%s) ORDER BY updated DESC LIMIT 100'
                                                     % ','.join('?' * len(AGENDA_A_DECIDER)), ['annulee'] + list(AGENDA_A_DECIDER))]
    for r in rows:
        r['matter_label'] = matter_label(desk, r['matter'])
    return rows


def mark_act_prepared(desk, ident, prepared=True):
    row = _row(desk, ident)
    if not row:
        raise Stop('echeance_absente')
    desk.db.execute('UPDATE deadlines450 SET act_prepared=?,updated=? WHERE id=?', (int(bool(prepared)), desk.now(), ident))
    _journal(desk, ident, 'acte_prepare' if prepared else 'acte_non_prepare')
    desk.db.commit()
    return public(_row(desk, ident))


def create_manual(desk, dav, matter_id, rule_id, start, distance='none', alsace=False, regime_date='', source_path='', note=''):
    row = create_deadline(desk, matter_id, rule_id, start, distance, alsace, regime_date, source_path, '', 'manuel', 'confirmee', note)
    if row['status'] in ('a_confirmer',):
        # 5.6.96 : la même échéance existait comme détection ; la saisie à la main est l'attestation d'une personne, consignée au journal
        desk.db.execute("UPDATE deadlines450 SET origin='manuel',updated=? WHERE id=?", (desk.now(), row['id']))
        _journal(desk, row['id'], 'saisie_manuelle', 'Même échéance saisie à la main', {'origin': row['origin']}, {'origin': 'manuel'})
        desk.db.commit()
        row = confirm(desk, dav, row['id'])
    state = sync_calendar(desk, dav, row['id']) if row.get('due') else ''
    return public(_row(desk, row['id'])) | {'calendar': state, 'created_now': row.get('created_now', False)}


def journal(desk, ident, limit=50):
    ensure_schema(desk)
    return [dict(r) for r in desk.db.execute('SELECT at,action,reason,before,after,actor,calendar FROM deadline_journal450 WHERE deadline_id=? '
                                             'ORDER BY id DESC LIMIT ?', (ident, limit))]


def listing(desk, matter='', include_closed=False):
    ensure_schema(desk)
    sql = 'SELECT * FROM deadlines450'
    clauses, args = [], []
    if matter:
        clauses.append('matter=?')
        args.append(matter)
    if not include_closed:
        clauses.append("status NOT IN ('terminee','annulee')")
    if clauses:
        sql += ' WHERE ' + ' AND '.join(clauses)
    sql += " ORDER BY CASE WHEN due='' THEN 1 ELSE 0 END, due, created"
    rows = [public(dict(r)) for r in desk.db.execute(sql, args)]
    for r in rows:
        r['matter_label'] = matter_label(desk, r['matter'])
    return rows


# ------------------------------------------------------ inventory + job
def observe_inventory(desk, matter_id, items, today=None, limit=10):
    if not enabled(desk):
        return 0
    ensure_schema(desk)
    today = today or datetime.now(timezone.utc)
    queued = 0
    for path, info in sorted(items.items()):
        if not looks_like_candidate(path):
            continue
        etag = str(info.get('etag', ''))
        ident = digest('obs450|' + path + '|' + etag)
        if desk.db.execute('SELECT 1 FROM deadline_obs450 WHERE id=?', (ident,)).fetchone():
            continue
        stale = False
        try:
            from email.utils import parsedate_to_datetime
            stale = parsedate_to_datetime(str(info.get('modified', ''))) < today - timedelta(days=45)
        except (TypeError, ValueError):
            pass
        now = desk.now()
        desk.db.execute('INSERT INTO deadline_obs450 VALUES(?,?,?,?,?,?,?,?)',
                        (ident, matter_id, path, etag, 'ignored_old' if stale else 'queued', '{}', now, now))
        desk.db.commit()
        if stale:
            continue
        desk.enqueue('analyze_deadline450', {'matter': matter_id, 'path': path, 'etag': etag,
                                             'size': int(info.get('size', 0) or 0)}, priority=22)
        queued += 1
        if queued >= limit:
            break
    return queued


UNREADABLE = {'ocr_qualite_insuffisante': 'scan de qualité insuffisante', 'ocr_sans_texte_reconnu': 'aucun texte reconnu',
              'document_sans_texte_exploitable': 'aucun texte exploitable', 'pdf_scan_ocr_necessaire': 'scan sans reconnaissance de texte',
              'document_trop_long_pour_analyse': 'document trop long'}


def analyze(desk, args, dav=None, extractor=None, today=None):
    ensure_schema(desk)
    path, etag, mid = str(args.get('path', '')), str(args.get('etag', '')), str(args.get('matter', ''))
    if not _matter(desk, mid) or not looks_like_candidate(path):
        raise Stop('piece_ou_dossier_invalide')
    ident = digest('obs450|' + path + '|' + etag)
    done = desk.db.execute('SELECT status,result FROM deadline_obs450 WHERE id=?', (ident,)).fetchone()
    if done and done['status'] == 'done':
        return json.loads(done['result'])
    from .dav import DAV
    client = dav or DAV(desk.c['nextcloud'])
    name = PurePosixPath(path).name
    stat = client.stat(path) if hasattr(client, 'stat') else {'path': path, 'etag': etag, 'size': int(args.get('size', 0))}
    raw = client.download(stat)
    if extractor is None:
        from .documents import extract
        # 5.6.62 : recherche par règles, sans modèle d'IA : la limite de taille (faite pour le contexte d'un modèle) ne s'applique pas
        cfg = dict(desk.c.get('documents', {}))
        cfg['max_document_chars'] = max(int(cfg.get('max_document_chars', 100000) or 100000), 2_000_000)
        extractor = lambda data, filename: extract(data, filename, cfg)
    try:
        text = extractor(raw, name)
    except Stop as ex:
        code = str(ex)
        if code not in UNREADABLE:
            raise
        # 5.6.62 : pièce illisible — signalée une fois à l'avocat, enregistrée (pas de nouvel essai à chaque passage, pas d'« échec »)
        result = {'status': 'blocked', 'matter': mid, 'path': path, 'illisible': code, 'evenements': 0, 'echeances_creees': 0, 'deja_connues': 0,
                  'message': 'Pièce « %s » illisible (%s) : vérifiez vous-même si elle fait courir un délai.' % (name, UNREADABLE[code])}
        now = desk.now()
        desk.db.execute('INSERT OR REPLACE INTO deadline_obs450 VALUES(?,?,?,?,?,?,?,?)',
                        (ident, mid, path, etag, 'done', json.dumps(result, ensure_ascii=False), now, now))
        desk.db.commit()
        from .live430 import emit
        emit(desk, 'echeance', result['message'] + ' (dossier %s)' % matter_label(desk, mid), matter=mid, dedupe='deadline450-illisible|' + ident)
        return result
    # 5.6.96 (D01, D02) : qualification avant calcul ; une détection ne produit qu'une proposition (``delais5689``) : ni ligne de suivi,
    # ni rappel, ni écriture d'agenda avant la décision d'une personne, qui refait le calcul avec la règle versionnée.
    q = qualifier_acte(text, path, version=etag)
    events = extract_start_events(text, today, path, q)
    acte = {k: q[k] for k in ('type_acte', 'type_libelle', 'incident', 'calendrier', 'mention', 'destinataire', 'rg', 'instance',
                              'preuve_notification', 'references_non_calculees')}
    if q['sans_calcul']:
        consigner_acte(desk, mid, path, etag, q)
    elif events:
        consigner_acte(desk, mid, path, etag, q, 'Événement(s) proposé(s), à vérifier : %s' % ', '.join(sorted({e['event'] for e in events})))
    declared = declaration_connue(desk, mid) if events else ''
    rg_dossier = rg_du_dossier(desk, mid) if events else []
    proposes, connus, ecartes, echecs = 0, 0, 0, 0
    from .delais5689 import depuis_detection
    for event in events:
        when = event['date'].isoformat()
        if deja_ecarte(desk, mid, event['event'], when, path, event['context']):
            ecartes += 1          # D05 : même passage, même date, même source déjà écartés par une personne
            continue
        motifs = motifs_de_verification(q, event['event'], when, declared, event['nature_date'], rg_dossier)
        try:
            fait = depuis_detection(desk, mid, event['event'], when, path, etag, event['context'], motifs=motifs)
        except Exception as error:   # noqa: BLE001 : signalé à l'avocat, jamais remplacé par une échéance inscrite
            desk.audit('delais5689_detection_impossible', {'matter': mid, 'cause': type(error).__name__})
            echecs += 1
            continue
        if fait is None:
            connus += 1
        else:
            proposes += 1
    if q['sans_calcul']:
        message = '« %s » qualifié %s : %s.' % (name, q['type_libelle'].lower(), q['mention'])
    elif echecs:
        message = '%d événement(s) repéré(s) dans « %s » n’ont pas pu être proposés : vérifiez vous-même si la pièce fait courir un délai.' % (echecs, name)
    elif proposes:
        message = '%d événement(s) à vérifier proposé(s) depuis « %s » (Contentieux › Délais) ; rien n’est inscrit ni rappelé avant votre décision.' % (proposes, name)
    elif events:
        message = 'Événement(s) de « %s » déjà proposé(s) ou déjà écarté(s) : rien de nouveau.' % name
    else:
        message = 'Aucun événement de départ exploitable dans « %s » : aucune échéance créée.' % name
    result = {'status': 'prepared' if proposes else 'blocked', 'matter': mid, 'path': path, 'evenements': len(events), 'propositions': proposes,
              'echeances_creees': 0, 'deja_connues': connus, 'deja_ecartees': ecartes, 'echecs': echecs, 'acte': acte, 'message': message}
    now = desk.now()
    desk.db.execute('INSERT OR REPLACE INTO deadline_obs450 VALUES(?,?,?,?,?,?,?,?)',
                    (ident, mid, path, etag, 'done', json.dumps(result, ensure_ascii=False), now, now))
    desk.db.commit()
    if proposes or echecs:
        from .live430 import emit
        emit(desk, 'echeance', result['message'] + ' (dossier %s)' % matter_label(desk, mid), matter=mid,
             dedupe='deadline450-new|' + ident)
    desk.audit('echeances_450_analysees', {'matter': mid, 'path': path, 'propositions': proposes, 'sans_calcul': q['sans_calcul']})
    return result


# ------------------------------------------------------ cross-check
def has_prepared_act(desk, row):
    if row['act_prepared']:
        return True
    try:
        from . import production420
        production420.ensure_schema(desk)
        found = desk.db.execute("SELECT 1 FROM production_deliverables_v420 WHERE matter=? AND status IN ('prepared','verifying','verified') "
                                "AND job_kind<>'analyze_notice440' AND created>=? LIMIT 1", (row['matter'], row['created'])).fetchone()
        return bool(found)
    except Exception:
        return False


def cross_check(desk, dav=None, today=None):
    """Anomalies : date manquante, agenda absent ou divergent, deux dates contradictoires, échéance sans acte préparé."""
    ensure_schema(desk)
    today = today or date.today()
    rows = [public(dict(r)) for r in desk.db.execute("SELECT * FROM deadlines450 WHERE status NOT IN ('terminee','annulee')")]
    anomalies = []

    def add(kind, row, message, **extra):
        anomalies.append({'kind': kind, 'id': row['id'], 'matter': row['matter'], 'matter_label': matter_label(desk, row['matter']),
                          'message': message, 'rule_label': row['rule_label'], **extra})

    for row in rows:
        if row['status'] == 'a_completer' or not row['due']:
            add('date_manquante', row, 'Échéance sans date calculable : %s' % (row['error'] or 'information manquante') )
        if row['status'] not in INSCRITES and row['calendar_uid']:
            # 5.6.96 (D06) : proposition inscrite par une version antérieure : à décider (confirmer, corriger ou écarter), puis reprendre l'agenda
            add('agenda_a_reconcilier', row, 'Proposition non confirmée déjà inscrite à l’agenda par une version antérieure : décidez-en, puis reprenez l’agenda.')
    # 5.6.96 (D04) : une échéance annulée ou écartée dont l'événement d'agenda attend votre décision reste visible
    for row in a_decider_agenda(desk):
        add('agenda_a_reconcilier', row, {'retrait_propose': 'Échéance annulée ou écartée : le retrait de son événement d’agenda vous est proposé.',
                                          'synchronisation_a_reprendre': 'Synchronisation à reprendre : l’agenda n’a pas répondu lors du retrait.',
                                          'a_reconcilier': 'L’événement d’agenda lié ne correspond plus à cette échéance : à vérifier à la main, rien n’a été effacé.'}[row['calendar_state']])
    groups = {}
    for row in rows:
        if row['due'] and row['status'] in ACTIVE:
            groups.setdefault((row['matter'], row['rule_id']), []).append(row)
    for (_, _), group in groups.items():
        if len({g['due'] for g in group}) > 1:
            for g in group:
                add('deux_dates', g, 'Deux dates différentes pour « %s » dans ce dossier : %s.' % (
                    g['rule_label'], ' / '.join(sorted({x['due'] for x in group}))))
    urls, tz_name = _calendar_urls(desk)
    events_by_uid = {}
    calendar_ok = False
    if urls and dav is not None and rows:
        dues = [date.fromisoformat(r['due']) for r in rows if r['due'] and r['status'] in INSCRITES]
        if dues:
            tz = ZoneInfo(tz_name)
            lo = datetime.combine(min(dues) - timedelta(days=400), datetime.min.time(), tz)
            hi = datetime.combine(max(dues) + timedelta(days=2), datetime.min.time(), tz)
            try:
                for event in dav.events(urls, lo, hi, tz_name):
                    events_by_uid.setdefault(event.get('uid', ''), []).append(event)
                calendar_ok = True
            except Stop:
                calendar_ok = False
    for row in rows:
        if not row['due'] or row['status'] not in ACTIVE:
            continue
        if calendar_ok:
            uid = 'axiorhub-%s@mail-agent.local' % row['id']
            found = events_by_uid.get(uid, [])
            dates = {str(e.get('start', ''))[:10] for e in found}
            if not found and date.fromisoformat(row['due']) >= today - timedelta(days=1):
                add('agenda_absent', row, 'L’échéance du %s n’est pas dans l’agenda.' % row['due_label'])
            elif found and row['due'] not in dates:
                add('agenda_divergent', row, 'L’agenda indique %s alors que l’échéance calculée est %s.' % (', '.join(sorted(dates)), row['due']))
        days = (date.fromisoformat(row['due']) - today).days
        if 0 <= days <= 14 and not has_prepared_act(desk, row):
            add('sans_acte', row, 'Échéance dans %d jour(s) sans acte ni brouillon préparé.' % days, days=days)
        if days < 0:
            add('depassee', row, 'Échéance dépassée depuis %d jour(s) et non clôturée.' % -days, days=days)
    return {'anomalies': anomalies, 'agenda_verifie': calendar_ok, 'count': len(anomalies), 'checked': len(rows)}


# ---------------------------------------------------------- reminders
def run_reminders(desk, today=None):
    """Rappels J-30, J-14, J-7, J-2, jour J ; un seul rappel par franchissement, jamais répété."""
    ensure_schema(desk)
    from .live430 import emit
    today = today or date.today()
    sent = 0
    # 5.6.96 (D02) : aucun rappel pour une détection que personne n'a décidée
    for r in desk.db.execute("SELECT * FROM deadlines450 WHERE status IN ('confirmee','manuel') AND due<>''").fetchall():
        row = dict(r)
        days = (date.fromisoformat(row['due']) - today).days
        label = matter_label(desk, row['matter'])
        reached = [o for o in OFFSETS if days <= o] if days >= 0 else []
        for offset in reached:
            kind = 'J%s' % offset
            if desk.db.execute('SELECT 1 FROM deadline_reminders450 WHERE deadline_id=? AND kind=?', (row['id'], kind)).fetchone():
                continue
            desk.db.execute('INSERT INTO deadline_reminders450 VALUES(?,?,?)', (row['id'], kind, desk.now()))
            if offset == min(reached):   # seul le rappel le plus proche est émis ; les paliers franchis sont soldés
                rule = engine.rule(row['rule_id'])['label']
                emit(desk, 'echeance', '%s : %s – %s, échéance le %s%s.' % (
                    OFFSET_LABELS[offset], rule, label, engine.fr_date(date.fromisoformat(row['due'])),
                    ' (à confirmer)' if row['status'] == 'a_confirmer' else ''), matter=row['matter'],
                    dedupe='deadline450|%s|%s' % (row['id'], kind))
                sent += 1
        if 0 <= days <= 7 and not has_prepared_act(desk, row):
            kind = 'sans_acte'
            if not desk.db.execute('SELECT 1 FROM deadline_reminders450 WHERE deadline_id=? AND kind=?', (row['id'], kind)).fetchone():
                desk.db.execute('INSERT INTO deadline_reminders450 VALUES(?,?,?)', (row['id'], kind, desk.now()))
                emit(desk, 'echeance_alerte', 'ALERTE : %s – %s, échéance le %s dans %d jour(s), sans acte ni brouillon préparé.' % (
                    engine.rule(row['rule_id'])['label'], label, engine.fr_date(date.fromisoformat(row['due'])), days),
                    matter=row['matter'], dedupe='deadline450|%s|%s' % (row['id'], kind))
                sent += 1
        if days < 0:
            kind = 'depassee'
            if not desk.db.execute('SELECT 1 FROM deadline_reminders450 WHERE deadline_id=? AND kind=?', (row['id'], kind)).fetchone():
                desk.db.execute('INSERT INTO deadline_reminders450 VALUES(?,?,?)', (row['id'], kind, desk.now()))
                emit(desk, 'echeance_alerte', 'ÉCHÉANCE DÉPASSÉE : %s – %s (%s). Clôturer ou corriger.' % (
                    engine.rule(row['rule_id'])['label'], label, row['due']), matter=row['matter'],
                    dedupe='deadline450|%s|%s' % (row['id'], kind))
                sent += 1
    desk.db.commit()
    return sent


def daily_check(desk, dav=None, today=None):
    """Appelé périodiquement par la surveillance : rappels puis contrôle croisé."""
    if not enabled(desk):
        return {'abstention': 'Échéances désactivées.'}
    sent = run_reminders(desk, today)
    repris = reprendre_retraits(desk, dav)        # 5.6.96 (D04) : seulement des retraits décidés par une personne, au plus trois essais
    report = cross_check(desk, dav, today)
    desk.setting('deadlines450:last_check', {'at': desk.now(), 'anomalies': report['count'], 'reminders': sent, 'retraits_repris': repris})
    return {'reminders': sent, 'anomalies': report['count'], 'remote_write': bool(repris), 'retraits_repris': repris}


def today_block(desk, link, today=None, limit=5):
    """Encart HTML de la page « Aujourd'hui » : prochaines échéances et anomalies."""
    from html import escape as esc
    today = today or date.today()
    ensure_schema(desk)
    rows = [r for r in listing(desk) if r['due'] and r['status'] in INSCRITES][:limit]     # 5.6.96 (D02) : une proposition n'est pas une échéance
    report = cross_check(desk, None, today)
    if not rows and not report['anomalies']:
        return ''
    lines = ''
    for r in rows:
        days = (date.fromisoformat(r['due']) - today).days
        when = 'dépassée' if days < 0 else ('aujourd’hui' if days == 0 else 'dans %d j' % days)
        lines += '<li><strong>%s</strong> (%s) – %s – %s%s</li>' % (
            esc(r['due_label']), esc(when), esc(r['rule_label']), esc(r['matter_label']),
            ' <em>à confirmer</em>' if r['status'] == 'a_confirmer' else '')
    alert = ''
    if report['anomalies']:
        alert = '<p><strong>%d point(s) à vérifier</strong> sur vos échéances.</p>' % len(report['anomalies'])
    return ('<section class="panel" aria-label="Échéances"><h2>Échéances de procédure</h2>%s<ul>%s</ul>'
            '<p><a href="%s">Ouvrir les échéances</a></p></section>') % (alert, lines, esc(link, quote=True))
