"""5.6.97 (lot agenda, § 14 et § 14.1) : grille horaire Jour et Semaine de /planning, tenue sur un écran sans défilement.

- Barre compacte : Aujourd'hui, précédent, suivant, période, Jour / Semaine / Mois / Liste, source (Agenda personnel, Agenda en ligne),
  « + Nouveau », volets. Aucun espace imposé au-dessus de la grille ; les outils secondaires (mini calendrier, filtres, tâches non
  programmées, sources et synchronisation, préférences) sont dans un volet replié, jamais par-dessus la grille.
- Grille : jours et heures alignés, ligne « Journée entière », repère de l'heure courante, événements cliquables. La hauteur est celle
  de la fenêtre (``v5697-agenda.js`` mesure la zone utile) ; les lignes se partagent la hauteur (``minmax(0, 1fr)``). Placement par
  classes de la feuille (la politique de sécurité interdit les styles en ligne) : colonne, ligne, étendue, couloir.
- Chevauchements : deux couloirs par jour, au-delà « + N » ouvrable ; événements hors plage signalés dans l'en-tête du jour avec
  « Voir toute la journée ». Rien n'est masqué par débordement. Couleurs doublées d'un libellé (type) et d'un texte d'état.
- Création : clic ou Entrée sur un créneau, glisser (ou Maj + flèches) pour une plage ; « + Nouveau » ; l'éditeur est un dialogue de la
  même page (Échap ferme et rend le focus). Préférences par utilisateur : jours affichés, plage horaire, pas de 15 ou 30 min, fuseau.
- Droits : sources, dossiers, tâches et responsables passent par ``acces5697`` avant tout affichage ; aucun appel à un modèle.
"""
import calendar
from datetime import date, datetime, timedelta, timezone
from html import escape
import json
import uuid

from .common import Stop, load_matters, matter_display

JOURS = ('Lundi', 'Mardi', 'Mercredi', 'Jeudi', 'Vendredi', 'Samedi', 'Dimanche')
JOURS_COURTS = ('Lun', 'Mar', 'Mer', 'Jeu', 'Ven', 'Sam', 'Dim')
MOIS = ('janvier', 'février', 'mars', 'avril', 'mai', 'juin', 'juillet', 'août', 'septembre', 'octobre', 'novembre', 'décembre')
ETIQUETTES = {'rendez_vous': 'RDV', 'repas': 'Repas', 'autre': 'Autre', 'audience': 'Audience', 'echeance': 'Échéance', 'tache': 'Tâche'}
ETATS_SOURCE = {'a_jour': 'à jour', 'perime': 'données périmées', 'erreur': 'en erreur', 'jamais_lu': 'jamais lu', 'desactive': 'désactivé',
                'non_connecte': 'non connecté', 'via_en_ligne': 'lu avec « Agenda en ligne »'}
LIGNES_TETE = 2          # ligne 1 : jours ; ligne 2 : journée entière ; puis un créneau par ligne


def e(value):
    return escape(str(value if value is not None else ''), quote=True)


def _heure(minutes):
    return '%d h' % (minutes // 60) if minutes % 60 == 0 else '%d h %02d' % (minutes // 60, minutes % 60)


def _periode(jours):
    a, b = jours[0], jours[-1]
    if a == b:
        return '%s %d %s %d' % (JOURS[a.weekday()], a.day, MOIS[a.month - 1], a.year)
    if a.month == b.month:
        return 'Semaine du %d au %d %s %d' % (a.day, b.day, MOIS[b.month - 1], b.year)
    return 'Semaine du %d %s au %d %s %d' % (a.day, MOIS[a.month - 1], b.day, MOIS[b.month - 1], b.year)


def _evenements(desk, acteur, source, debut, fin):
    """Événements de la source choisie, droits appliqués ; état de chaque source pour l'affichage honnête."""
    from . import agenda5697, calendriers5697
    from .acces5697 import dossiers_autorises
    if source == 'personal':
        return agenda5697.locaux(desk, acteur, debut, fin), [], ''
    from .workplan import _calendar_urls, calendar_events
    perimetre = dossiers_autorises(desk, acteur)
    out, avertissement = [], ''
    if _calendar_urls(desk):
        try:
            data = calendar_events(desk, debut.isoformat(), fin.isoformat(), refresh=True, source='online')
            calendriers5697._noter(desk, 'en_ligne', acteur.compte, True)
        except Exception as ex:   # noqa: BLE001 — toute panne de lecture : cache affiché et marqué périmé, jamais une page en erreur
            data = calendar_events(desk, debut.isoformat(), fin.isoformat(), refresh=False, source='online')
            calendriers5697._noter(desk, 'en_ligne', acteur.compte, False, 'Lecture CalDAV indisponible (%s) : données en cache, potentiellement incomplètes.' % str(ex)[:60])
            avertissement = 'Lecture de l’agenda en ligne indisponible : données en cache, potentiellement incomplètes.'
        for ev in perimetre.filtrer(data['events']):
            if str(ev.get('calendar_url', '')).startswith('agenda5697:'):
                continue
            out.append({'id': 'cache:' + ev['id'], 'title': ev['title'], 'starts': ev['starts'], 'ends': ev['ends'], 'matter': ev['matter'], 'all_day': _jour_entier(ev),
                        'source': 'en_ligne', 'source_label': 'Agenda en ligne', 'nature': 'rendez_vous', 'nature_label': 'Événement', 'modifiable': False,
                        'serie': bool(ev.get('recurrence_id')), 'etat_synchro': 'lu'})
    for s in calendriers5697.sources(desk, acteur.compte):
        if s['fournisseur'] in ('caldav', 'google') and s['lecture']:
            rows, etat = calendriers5697.lire_cible(desk, acteur, s['id'], debut, fin)
            if etat.get('etat') in ('perime', 'erreur'):
                avertissement = 'Un calendrier en ligne n’a pas pu être lu : données en cache, potentiellement incomplètes.'
            for ev in rows:
                out.append({'id': 'cache:' + ev['id'], 'title': ev['title'], 'starts': ev['starts'], 'ends': ev['ends'], 'matter': ev['matter'],
                            'all_day': _jour_entier(ev), 'source': s['id'], 'source_label': s['libelle'], 'nature': 'rendez_vous', 'nature_label': 'Événement',
                            'modifiable': False, 'serie': bool(ev.get('recurrence_id')), 'etat_synchro': 'lu'})
    out += calendriers5697.en_attente(desk, acteur, debut, fin)
    return out, calendriers5697.sources(desk, acteur.compte), avertissement


def _jour_entier(ev):
    a, b = datetime.fromisoformat(ev['starts']), datetime.fromisoformat(ev['ends'])
    return b - a >= timedelta(days=1) and a.time() == b.time()


def _place(ev, jours, tz, h0, h1, pas):
    """Par jour affiché : ('jour', i) pour la ligne Journée entière ; ('heure', i, ligne, etendue) ; ('avant'|'apres', i) hors plage."""
    a, b = datetime.fromisoformat(ev['starts']).astimezone(tz), datetime.fromisoformat(ev['ends']).astimezone(tz)
    out = []
    for i, d in enumerate(jours):
        jour_a = datetime.combine(d, datetime.min.time(), tz)
        jour_b = datetime.combine(d + timedelta(days=1), datetime.min.time(), tz)
        if not (a < jour_b and b > jour_a):
            continue
        if ev.get('all_day') or (b - a >= timedelta(hours=24)):
            out.append(('jour', i))
            continue
        debut = max(a, jour_a)
        fin = min(b, jour_b)
        m_a = (debut.replace(tzinfo=None) - datetime.combine(d, datetime.min.time())).total_seconds() // 60
        m_b = (fin.replace(tzinfo=None) - datetime.combine(d, datetime.min.time())).total_seconds() // 60 if fin < jour_b else 24 * 60
        if m_b <= h0 * 60:
            out.append(('avant', i))
            continue
        if m_a >= h1 * 60:
            out.append(('apres', i))
            continue
        m_a, m_b = max(m_a, h0 * 60), min(m_b, h1 * 60)
        ligne = int((m_a - h0 * 60) // pas)
        etendue = max(1, int(-(-(m_b - h0 * 60) // pas)) - ligne)
        out.append(('heure', i, ligne, etendue))
    return out


def _couloirs(items):
    """Deux couloirs par jour ; au-delà, regroupés sous « + N ». ``items`` : (ligne, etendue, ev). Rend (placés, débordements)."""
    places, deborde = [], []
    fins = [-1, -1]
    for ligne, etendue, ev in sorted(items, key=lambda x: (x[0], -x[1])):
        libre = [k for k in (0, 1) if fins[k] <= ligne]
        if libre:
            places.append((ligne, etendue, ev, libre[0]))
            fins[libre[0]] = ligne + etendue
        else:
            deborde.append((ligne, etendue, ev))
    # un événement seul sur sa plage prend toute la largeur
    pleins = []
    for ligne, etendue, ev, k in places:
        voisin = any(o is not ev and l2 < ligne + etendue and ligne < l2 + n2 for l2, n2, o, _ in places) or \
            any(l2 < ligne + etendue and ligne < l2 + n2 for l2, n2, _ in deborde)
        pleins.append((ligne, etendue, ev, k, not voisin))
    return pleins, deborde


def _bouton_evenement(ev, tz, classes):
    a, b = datetime.fromisoformat(ev['starts']).astimezone(tz), datetime.fromisoformat(ev['ends']).astimezone(tz)
    nature = ev.get('nature') or 'rendez_vous'
    etat = ''
    if ev.get('etat_synchro') not in (None, '', 'local', 'lu'):
        etat = ev.get('etat_label') or ev['etat_synchro']
    heure = 'journée' if ev.get('all_day') else '%s – %s' % (a.strftime('%H:%M'), b.strftime('%H:%M'))
    label = '%s, %s, %s%s%s' % (ev.get('nature_label') or 'Événement', ev['title'], heure, ', ' + ev['source_label'] if ev.get('source_label') else '',
                                ', ' + etat if etat else '')
    return ('<button type="button" class="ag97-ev ag97-n-%s%s %s" data-ag97-ev="%s" data-source="%s" data-modifiable="%s" aria-label="%s">'
            '<span class="ag97-ev-tag">%s</span><span class="ag97-ev-h">%s</span><span class="ag97-ev-t">%s</span>%s</button>') % (
        e(nature), ' ag97-attente' if etat else '', classes, e(ev['id']), e(ev.get('source', '')), 'oui' if ev.get('modifiable') else 'non', e(label),
        e(ETIQUETTES.get(nature, 'Évén.')), e(heure), e(ev['title']), ('<span class="ag97-ev-etat">%s</span>' % e(etat)) if etat else '')


def page(desk, args, url, prefix, source='personal', subnav=''):
    """Vue Jour ou Semaine. ``source`` : 'personal' (Agenda personnel) ou 'online' (Agenda en ligne)."""
    from . import agenda5697, rendezvous5697
    from .acces5697 import acteur_requete, dossiers_autorises
    agenda5697.schema(desk)
    acteur = acteur_requete()
    owner = acteur.compte
    prefs = agenda5697.preferences(desk, owner)
    tz = agenda5697.zone_de(desk, prefs['zone'])
    vue = 'day' if args.get('view') == 'day' else 'week'
    aujourd_hui = datetime.now(tz).date()
    try:
        jour = date.fromisoformat(args.get('date', '') or aujourd_hui.isoformat())
    except ValueError:
        raise Stop('date_agenda_invalide') from None
    sub = 'personnel' if source == 'personal' else 'en_ligne'
    complet = args.get('plage') == 'journee'
    h0, h1 = (0, 24) if complet else (prefs['heure_debut'], prefs['heure_fin'])
    pas = prefs['pas']
    if vue == 'day':
        jours = [jour]
    else:
        lundi = jour - timedelta(days=jour.weekday())
        n = 7 if prefs['jours'] == 7 or jour.weekday() >= 5 else 5
        jours = [lundi + timedelta(days=i) for i in range(n)]
    debut = datetime.combine(jours[0], datetime.min.time(), tz)
    fin = datetime.combine(jours[-1] + timedelta(days=1), datetime.min.time(), tz)
    events, sources, avertissement = _evenements(desk, acteur, source, debut, fin)
    pas_jour = timedelta(days=1 if vue == 'day' else 7)
    nb = (h1 - h0) * 60 // pas
    lien = lambda **kw: url('/planning', vue='agenda', agenda=sub, **kw)   # noqa: E731

    # ---------------------------------------------------------------- barre compacte
    vues = ''.join('<a class="ag97-vue" %shref="%s">%s</a>' % ('aria-current="page" ' if v == vue else '', e(lien(view=v, date=jour.isoformat())), lab)
                   for v, lab in (('day', 'Jour'), ('week', 'Semaine'), ('month', 'Mois'), ('list', 'Liste')))
    choix_source = ''.join('<option value="%s"%s>%s</option>' % (e(lien(view=vue, date=jour.isoformat()).replace('agenda=' + sub, 'agenda=' + k)), ' selected' if k == sub else '', lab)
                           for k, lab in (('personnel', 'Agenda personnel'), ('en_ligne', 'Agenda en ligne')))
    barre = ('<div class="ag97-barre" role="toolbar" aria-label="Navigation de l’agenda">'
             '<a class="ax-btn ghost ag97-auj" href="%s">Aujourd’hui</a><a class="ax-btn ghost ag97-nav" href="%s" aria-label="Période précédente">‹</a>'
             '<a class="ax-btn ghost ag97-nav" href="%s" aria-label="Période suivante">›</a><h2 class="ag97-periode" id="ag97-periode">%s</h2>'
             '<nav class="ag97-vues" aria-label="Vues de l’agenda">%s</nav>%s'
             '<button type="button" class="ax-btn ag97-nouveau" data-ag97-nouveau>+ Nouveau</button>'
             '<button type="button" class="ax-btn ghost ag97-volets-btn" data-ag97-volets aria-expanded="false" aria-controls="ag97-volets">Volets</button></div>') % (
        e(lien(view=vue, date=aujourd_hui.isoformat())), e(lien(view=vue, date=(jours[0] - pas_jour).isoformat())),
        e(lien(view=vue, date=(jours[0] + pas_jour).isoformat())), e(_periode(jours)), vues,
        subnav or '<label class="ag97-source">Calendrier<select data-ag97-source>%s</select></label>' % choix_source)

    # ---------------------------------------------------------------- placement
    col = lambda i: 2 + 2 * i   # noqa: E731 — deux couloirs par jour ; la colonne 1 porte les heures
    par_jour = {i: [] for i in range(len(jours))}
    journee = {i: [] for i in range(len(jours))}
    hors = {i: [0, 0] for i in range(len(jours))}
    for ev in events:
        for p in _place(ev, jours, tz, h0, h1, pas):
            if p[0] == 'jour':
                journee[p[1]].append(ev)
            elif p[0] == 'heure':
                par_jour[p[1]].append((p[2], p[3], ev))
            else:
                hors[p[1]][0 if p[0] == 'avant' else 1] += 1
    cellules = ['<div class="ag97-coin ag97-c1 ag97-r1" aria-hidden="true">%s</div>' % e(tz.key.split('/')[-1].replace('_', ' '))]
    for i, d in enumerate(jours):
        avant, apres = hors[i]
        info = ''
        if avant or apres:
            info = '<span class="ag97-hors">%s%s</span> <a class="ag97-toute" href="%s">Voir toute la journée</a>' % (
                ('▲ %d avant %s ' % (avant, _heure(h0 * 60))) if avant else '', ('▼ %d après %s' % (apres, _heure(h1 * 60))) if apres else '',
                e(url('/planning', vue='agenda', agenda=sub, view='day', date=d.isoformat(), plage='journee')))
        cellules.append('<div class="ag97-jour ag97-c%d ag97-l2 ag97-r1%s"><a href="%s"><strong>%s</strong> %s</a>%s</div>' % (
            col(i), ' ag97-aujourdhui' if d == aujourd_hui else '', e(lien(view='day', date=d.isoformat())), e(JOURS_COURTS[d.weekday()] if vue == 'week' else JOURS[d.weekday()]),
            e(d.strftime('%d/%m')), info))
    cellules.append('<div class="ag97-h ag97-c1 ag97-r2">Journée entière</div>')
    for i, d in enumerate(jours):
        cellules.append('<button type="button" class="ag97-case ag97-jcase ag97-c%d ag97-l2 ag97-r2" data-jour="%s" aria-label="%s, journée entière : créer">'
                        '</button>' % (col(i), e(d.isoformat()), e('%s %d %s' % (JOURS[d.weekday()], d.day, MOIS[d.month - 1]))))
        chips = journee[i]
        if chips:
            cellules.append('<div class="ag97-jliste ag97-c%d ag97-l2 ag97-r2">%s</div>' % (col(i), ''.join(_bouton_evenement(ev, tz, 'ag97-jev') for ev in chips[:2]) + (
                ('<details class="ag97-plus"><summary>+ %d</summary><div class="ag97-pop">%s</div></details>' % (len(chips) - 2, ''.join(_bouton_evenement(ev, tz, 'ag97-pev') for ev in chips[2:])))
                if len(chips) > 2 else '')))
    for k in range(nb):
        m = h0 * 60 + k * pas
        r = LIGNES_TETE + 1 + k
        cellules.append('<div class="ag97-h ag97-c1 ag97-r%d%s">%s</div>' % (r, ' ag97-pleine' if m % 60 == 0 else '', e(_heure(m)) if m % 60 == 0 or pas == 30 and nb <= 24 else ''))
        for i, d in enumerate(jours):
            cellules.append('<button type="button" class="ag97-case ag97-c%d ag97-l2 ag97-r%d%s" data-jour="%s" data-minute="%d" aria-label="%s, %s : créer"></button>' % (
                col(i), r, ' ag97-pleine' if m % 60 == 0 else '', e(d.isoformat()), m, e('%s %d %s' % (JOURS[d.weekday()], d.day, MOIS[d.month - 1])),
                e('%s – %s' % (_heure(m), _heure(m + pas)))))
    for i in range(len(jours)):
        places, deborde = _couloirs(par_jour[i])
        for ligne, etendue, ev, k, plein in places:
            classes = 'ag97-c%d %s ag97-r%d ag97-s%d' % (col(i) + (0 if plein else k), 'ag97-l2' if plein else 'ag97-l1', LIGNES_TETE + 1 + ligne, min(etendue, nb - ligne))
            cellules.append(_bouton_evenement(ev, tz, classes))
        if deborde:
            ligne = min(x[0] for x in deborde)
            cellules.append('<details class="ag97-plus ag97-c%d ag97-l1 ag97-r%d"><summary aria-label="%d autre(s) événement(s) sur ce créneau">+ %d</summary>'
                            '<div class="ag97-pop">%s</div></details>' % (col(i) + 1, LIGNES_TETE + 1 + ligne, len(deborde), len(deborde),
                                                                            ''.join(_bouton_evenement(ev, tz, 'ag97-pev') for _, _, ev in deborde)))
    maintenant = datetime.now(tz)
    if maintenant.date() in jours and h0 * 60 <= maintenant.hour * 60 + maintenant.minute < h1 * 60:
        i = jours.index(maintenant.date())
        m = maintenant.hour * 60 + maintenant.minute - h0 * 60
        cellules.append('<div class="ag97-maintenant ag97-c%d ag97-l2 ag97-r%d ag97-o%d" role="presentation"><span class="ag97-sr">Heure actuelle : %s</span></div>' % (
            col(i), LIGNES_TETE + 1 + m // pas, int((m % pas) * 10 // pas), e(maintenant.strftime('%H:%M'))))
    grille = ('<div class="ag97-grille ag97-j%d ag97-rows%d" id="ag97-grille" data-ag97-grille role="group" aria-labelledby="ag97-periode" '
              'aria-describedby="ag97-aide">%s</div><p class="ag97-sr" id="ag97-aide">Flèches pour se déplacer entre les créneaux, Entrée pour créer, Maj + flèches '
              'pour étendre la plage, Échap pour fermer l’éditeur.</p>') % (len(jours), nb, ''.join(cellules))

    # ---------------------------------------------------------------- volets (repliés)
    volets = _volets(desk, acteur, prefs, jour, vue, sub, url, sources, source)
    avis = ('<p class="ag97-avis" role="status">%s</p>' % e(avertissement)) if avertissement else ''
    editeur = editeur_html(desk, acteur, source, tz)
    donnees = {'zone': tz.key, 'pas': pas, 'source': source, 'heure_debut': h0, 'heure_fin': h1, 'api': prefix + '/api440/m5689/agenda',
               'aujourdhui': aujourd_hui.isoformat(), 'vue': vue}
    return ('<section class="ag97%s" data-ag97 aria-label="Agenda">%s%s<div class="ag97-corps">%s%s</div>%s'
            '<script type="application/json" id="ag97-donnees">%s</script><script defer src="%s/static/v5697-agenda.js"></script></section>') % (
        ' ag97-volets-ouverts' if prefs['volets'] else '', barre, avis, grille, volets, editeur,
        json.dumps(donnees).replace('<', '\\u003c').replace('>', '\\u003e').replace('&', '\\u0026'), e(prefix))


def _mini(jour, sub, vue, url):
    premier = jour.replace(day=1)
    lignes = ''
    for semaine in calendar.Calendar(firstweekday=0).monthdatescalendar(premier.year, premier.month):
        lignes += '<tr>' + ''.join('<td><a href="%s"%s%s>%d</a></td>' % (
            e(url('/planning', vue='agenda', agenda=sub, view=vue, date=d.isoformat())), ' class="ag97-hors-mois"' if d.month != premier.month else '',
            ' aria-current="date"' if d == jour else '', d.day) for d in semaine) + '</tr>'
    precedent = (premier - timedelta(days=1)).replace(day=1)
    suivant = (premier + timedelta(days=32)).replace(day=1)
    return ('<div class="ag97-mini"><div class="ag97-mini-tete"><a href="%s" aria-label="Mois précédent">‹</a><strong>%s %d</strong><a href="%s" aria-label="Mois suivant">›</a></div>'
            '<table><thead><tr>%s</tr></thead><tbody>%s</tbody></table></div>') % (
        e(url('/planning', vue='agenda', agenda=sub, view=vue, date=precedent.isoformat())), e(MOIS[premier.month - 1].capitalize()), premier.year,
        e(url('/planning', vue='agenda', agenda=sub, view=vue, date=suivant.isoformat())), ''.join('<th scope="col" abbr="%s">%s</th>' % (j, j[0]) for j in JOURS), lignes)


def _volets(desk, acteur, prefs, jour, vue, sub, url, sources, source):
    from . import agenda5697
    taches = agenda5697.taches_non_programmees(desk, acteur)
    liste = ''.join('<li><span>%s%s</span><button type="button" class="ax-btn ghost" data-ag97-programmer="%s" data-titre="%s">Programmer</button></li>' % (
        e(t['title']), (' <small>échéance %s</small>' % e(t['due'])) if t['due'] else '', e(t['id']), e(t['title'])) for t in taches) or \
        '<li class="ag97-muet">Aucune tâche ouverte sans créneau.</li>'
    filtres = ''.join('<label class="ag97-case-filtre"><input type="checkbox" checked data-ag97-filtre="%s"> %s</label>' % (k, e(v))
                      for k, v in (('rendez_vous', 'Rendez-vous'), ('repas', 'Repas professionnels'), ('audience', 'Audiences'), ('echeance', 'Échéances'),
                                   ('tache', 'Tâches'), ('autre', 'Autres')))
    etats = ''.join('<li><strong>%s</strong> · %s%s%s</li>' % (
        e(s['libelle']), e(ETATS_SOURCE.get(s['etat'], s['etat'])), (' · dernière lecture réussie : %s (UTC)' % e(s['derniere_synchro'][:16].replace('T', ' '))) if s.get('derniere_synchro') else '',
        (' · ' + e(s['message'])) if s.get('message') and s['etat'] not in ('a_jour',) else '') for s in sources) if source == 'online' else \
        '<li><strong>Agenda personnel</strong> · local, non synchronisé (aucune copie vers Nextcloud ou Google).</li>'
    options = ''.join('<option value="%d"%s>%s</option>' % (h, ' selected' if h == prefs['heure_debut'] else '', _heure(h * 60)) for h in range(0, 23))
    options_fin = ''.join('<option value="%d"%s>%s</option>' % (h, ' selected' if h == prefs['heure_fin'] else '', _heure(h * 60)) for h in range(2, 25))
    return ('<div class="ag97-volets" id="ag97-volets" role="region" aria-label="Outils de l’agenda" hidden>'
            '<details open><summary>Mini calendrier</summary>%s</details>'
            '<details><summary>Filtres</summary><div class="ag97-filtres">%s</div></details>'
            '<details%s><summary>Tâches non programmées (%d)</summary><ul class="ag97-taches">%s</ul></details>'
            '<details><summary>Sources et synchronisation</summary><ul class="ag97-sources">%s</ul></details>'
            '<details><summary>Préférences d’affichage</summary><form class="ag97-prefs" data-ag97-prefs>'
            '<label>Jours<select name="jours"><option value="5"%s>Lundi à vendredi</option><option value="7"%s>Sept jours</option></select></label>'
            '<label>De<select name="heure_debut">%s</select></label><label>À<select name="heure_fin">%s</select></label>'
            '<label>Pas<select name="pas"><option value="30"%s>30 min</option><option value="15"%s>15 min</option></select></label>'
            '<label>Fuseau<input name="zone" value="%s" placeholder="Fuseau du cabinet" maxlength="60"></label>'
            '<button class="ax-btn ghost" type="submit">Enregistrer</button><p role="status" class="ag97-statut"></p></form></details></div>') % (
        _mini(jour, sub, vue, url), filtres, ' open' if taches else '', len(taches), liste, etats,
        ' selected' if prefs['jours'] == 5 else '', ' selected' if prefs['jours'] == 7 else '', options, options_fin,
        ' selected' if prefs['pas'] == 30 else '', ' selected' if prefs['pas'] == 15 else '', e(prefs['zone']))


def editeur_html(desk, acteur, source, tz):
    """Éditeur compact (dialogue de la même page). Les listes viennent du serveur, droits appliqués ; aucun secret."""
    from . import calendriers5697, rendezvous5697
    from .acces5697 import dossiers_autorises
    from .membres5650 import directory
    perimetre = dossiers_autorises(desk, acteur)
    try:
        dossiers = [m for m in load_matters(desk.c) if m['id'] in perimetre]
    except Stop:
        dossiers = []
    options_dossiers = ''.join('<option value="%s">%s</option>' % (e(m['id']), e(matter_display(m))) for m in sorted(dossiers, key=lambda m: matter_display(m).lower()))
    calendriers = '<option value="local">Agenda personnel (local, non synchronisé)</option>' + ''.join(
        '<option value="%s"%s>%s%s</option>' % (e(c['id']), '' if c['ecriture'] else ' disabled', e(c['libelle']), '' if c['ecriture'] else ' (lecture seule)')
        for c in calendriers5697.cibles(desk, acteur.compte))
    try:
        membres = directory(desk)
    except Exception:   # noqa: BLE001 — annuaire indisponible : seule l'option « Sans responsable » reste
        membres = {}
    responsables = '<option value="">Sans responsable</option>' + ''.join('<option value="%s">%s</option>' % (e(k), e(v.get('name') or k)) for k, v in sorted(membres.items()))
    talk_ok, talk_motif = rendezvous5697.talk_disponible(desk)
    speakr = rendezvous5697.speakr_adresse(desk, acteur.compte)
    return ('<dialog class="ag97-editeur" id="ag97-editeur" aria-labelledby="ag97-editeur-titre"><form method="dialog" class="ag97-form" data-ag97-form novalidate>'
            '<h2 id="ag97-editeur-titre">Nouvel événement</h2><input type="hidden" name="cle" value="%s"><input type="hidden" name="id"><input type="hidden" name="version">'
            '<fieldset class="ag97-objet"><legend class="ag97-sr">Que créer ?</legend>'
            '<label><input type="radio" name="objet" value="evenement" checked> Événement</label><label><input type="radio" name="objet" value="tache"> Tâche</label>'
            '<label><input type="radio" name="objet" value="les_deux"> Événement et tâche liée</label></fieldset>'
            '<label class="ag97-titre">Titre<input name="titre" required maxlength="200" autocomplete="off"></label>'
            '<div class="ag97-ligne" data-ag97-pour="evenement"><label>Type<select name="nature"><option value="rendez_vous">Rendez-vous</option>'
            '<option value="repas">Repas professionnel</option><option value="autre">Autre</option></select></label>'
            '<label>Calendrier<select name="calendrier">%s</select></label></div>'
            '<div class="ag97-ligne" data-ag97-pour="evenement"><label>Début<input type="datetime-local" name="debut" step="300"></label>'
            '<label>Fin<input type="datetime-local" name="fin" step="300"></label><label class="ag97-coche"><input type="checkbox" name="journee_entiere"> Journée entière</label></div>'
            '<div class="ag97-choix" data-ag97-choix hidden></div>'
            '<div class="ag97-ligne"><label>Lieu<input name="lieu" maxlength="200"></label><label>Dossier (facultatif)<select name="dossier"><option value="">Sans dossier</option>%s</select></label></div>'
            '<fieldset class="ag97-visio" data-ag97-visio><legend>Visio</legend>'
            '<label><input type="radio" name="visio" value="sans" checked> Sans visio</label>'
            '<label><input type="radio" name="visio" value="talk"%s> Créer un salon privé Nextcloud Talk</label>'
            '<label><input type="radio" name="visio" value="lien"> Coller un lien HTTPS existant</label>'
            '<input name="lien" type="url" placeholder="https://…" maxlength="500" aria-label="Lien de visio fourni" hidden>%s%s</fieldset>'
            '<fieldset class="ag97-tache" data-ag97-pour="tache"><legend>Tâche</legend><div class="ag97-ligne">'
            '<label>Responsable<select name="responsable">%s</select></label><label>Priorité<select name="priorite"><option value="normale">Normale</option>'
            '<option value="haute">Haute</option><option value="basse">Basse</option></select></label><label>Échéance (facultative)<input type="date" name="echeance"></label></div>'
            '<label class="ag97-coche" data-ag97-reserver><input type="checkbox" name="reserver" checked> Réserver ce créneau dans l’agenda</label></fieldset>'
            '<details class="ag97-options"><summary>Options</summary><label>Visibilité<select name="visibilite"><option value="normale">Normale</option>'
            '<option value="privee">Privée</option></select></label><label>Notes<textarea name="notes" rows="2" maxlength="2000"></textarea></label></details>'
            '<div class="ag97-detail" data-ag97-detail hidden></div>'
            '<p class="ag97-statut" role="status" data-ag97-statut></p>'
            '<div class="ag97-boutons"><button type="submit" class="ax-btn" value="enregistrer" data-ag97-enregistrer>Enregistrer</button>'
            '<button type="button" class="ax-btn ghost" data-ag97-annuler>Annuler</button></div></form></dialog>') % (
        uuid.uuid4().hex, calendriers, options_dossiers, '' if talk_ok else ' disabled data-ag97-toujours-inactif',
        '' if talk_ok else '<small class="ag97-aide">Talk indisponible : %s Le rendez-vous peut être enregistré sans visio ou avec un lien existant.</small>' % e(talk_motif),
        ('<label class="ag97-coche"><input type="checkbox" name="speakr"> Prévoir une transcription avec Speakr</label>') if speakr else '',
        responsables)
