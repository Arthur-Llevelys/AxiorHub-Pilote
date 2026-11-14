"""5.6.96 (lot accueil : F01-F05, I01, I02) : en-tête d'Aujourd'hui, carte météo, rang « À décider / Ma journée », actions en fenêtre flottante.

- Bandeau (F01, F02, I01) : « {Bonjour|Bon après-midi|Bonsoir} {Prénom}, nous sommes le {jour date}, il est {h h mm}. » La formule suit l'heure dans
  le fuseau du compte (5 h-12 h, 12 h-18 h, sinon le soir). Le prénom est la préférence ``prenom`` du compte (``assistant567``, propre au compte, jamais
  déduit de l'adresse de connexion ni du profil du cabinet), échappée ici ; vide → la formule seule. Le serveur rend le texte initial (page lisible sans
  JavaScript) ; ``v5696-accueil.js`` le tient à jour (``Intl.DateTimeFormat``, mêmes seuils) sans recharger la page ni appeler l'IA. Pas d'``aria-live``.
- Carte météo (F04) : cellule à droite de l'en-tête, remplie **après** le premier affichage par ``accueil/meteo`` (ETag/304, aucun travail IA).
  Météo désactivée ou masquée par la personne : aucune carte, rien n'est chargé. Réglage (clé, lieu, activation) : administrateur, ``reglages_html``.
- Rang (I01) : à gauche « À décider / À relire » (trois premières actions, puis « Voir la suite » qui déplie la file complète, inchangée) ; à droite
  « Ma journée » (bloc existant) précédé des échéances **confirmées** du jour. Une date tirée d'une pièce reste « À vérifier ».
- Fenêtre flottante (I02) : ``accueil/action?nature=&cle=`` rend le détail d'une action d'une table fermée (réponse, échéance, document) avec le même
  service et les mêmes formulaires que la page complète, après contrôle du cabinet, des dossiers réservés et des droits (403/404 sans contenu).
  GET ne crée, ne valide et n'envoie rien.

Le compte et le rôle viennent de la requête authentifiée, jamais du corps.
"""
from datetime import datetime
import hashlib
from html import escape as e
import json
import re
from urllib.parse import urlencode
from zoneinfo import ZoneInfo

from . import meteo5696
from .common import Stop

ROLES = ('administrateur', 'avocat', 'assistant')
LECTURE = ('accueil/meteo', 'accueil/action', 'accueil/rang')
ECRITURE = ('accueil/options',)
REGLAGE = ('accueil/reglage/tester', 'accueil/reglage/lieux', 'accueil/reglage/lieu', 'accueil/reglage/activer', 'accueil/reglage/effacer')   # administrateur
NATURES = ('reponse', 'echeance', 'document')        # I02 : table fermée des détails ouvrables en fenêtre flottante
PREMIERES = 3                                         # I01 : actions visibles dans le rang, le reste dans « Voir la suite »
A_VERIFIER = ('a_confirmer', 'a_completer')           # échéance tirée d'une pièce, pas encore validée
CONFIRMEES = ('confirmee', 'manuel')
STATUTS = {'role_insuffisant': '403 Forbidden', 'origine_ou_csrf_refuse': '403 Forbidden', 'dossier_reserve': '403 Forbidden', 'acces_refuse': '403 Forbidden',
           'route_inconnue': '404 Not Found', 'introuvable': '404 Not Found', 'version_en_conflit': '409 Conflict',
           'controle_indisponible': '503 Service Unavailable'}   # 5.6.100 (R04) : contrôle des droits en panne : rien n'est montré


def allowed(role, route):
    route = route.split('?', 1)[0]
    if role not in ROLES:
        return False
    if route in REGLAGE:
        return role == 'administrateur'
    return route in LECTURE + ECRITURE


def T(cle, **valeurs):
    from .i18n5631 import t
    return t('f96.' + cle, **valeurs)


# ------------------------------------------------------------------------------------------------ bandeau (F01, F02)
def formule(heure):
    """Seuils de la salutation (heure locale du compte, 0-23) ; ``v5696-accueil.js`` applique les mêmes."""
    if 5 <= heure < 12:
        return 'matin'
    if 12 <= heure < 18:
        return 'apres_midi'
    return 'soir'


def prenom_du_compte(desk, owner):
    from .assistant567 import profile
    return ' '.join(str(profile(desk, owner).get('prenom') or '').split())[:40]


def salutation(desk, owner, maintenant=None):
    """Texte initial du bandeau, dans la langue de l'interface et le fuseau du compte. ``maintenant`` : instant simulé (tests)."""
    from .i18n5631 import current, format_date
    prenom = prenom_du_compte(desk, owner)
    tz = meteo5696.fuseau(desk, owner)
    now = (maintenant or datetime.now(ZoneInfo(tz))).astimezone(ZoneInfo(tz))
    mot = T('formule.' + formule(now.hour))
    salut = T('salut', formule=mot, prenom=prenom) if prenom else mot
    texte = T('phrase', salut=salut, date=format_date(now.date()), heure=T('heure', h=now.hour, mm='%02d' % now.minute))
    return {'texte': texte, 'datetime': now.strftime('%Y-%m-%dT%H:%M'), 'fuseau': tz, 'prenom': prenom, 'lang': current(), 'formule': formule(now.hour)}


def _messages():
    """Gabarits du bandeau et libellés de la carte pour le script (donnée JSON, jamais exécutée)."""
    from .i18n5631 import catalog, current, t
    cles = [k for k in catalog('fr') if k.startswith(('f96.formule.', 'f96.meteo.', 'f96.carte.', 'f96.fenetre.', 'f96.reglage.', 'f96.rang.', 'f96.erreur.', 'f96.options.')) or k in ('f96.salut', 'f96.phrase', 'f96.heure')]
    donnees = {k[4:]: t(k, lang=current()) for k in cles}
    return json.dumps(donnees, ensure_ascii=False).replace('<', '\\u003c').replace('>', '\\u003e').replace('&', '\\u0026')


def options_html(desk, owner):
    """« Options d'accueil » : prénom affiché et carte météo, enregistrés pour la personne (route accueil/options)."""
    from .assistant567 import profile
    p = profile(desk, owner)
    meteo = ''
    if meteo5696.configuration(desk)['enabled']:
        meteo = '<label class="f96-case"><input type="checkbox" name="meteo_visible"%s> %s</label>' % ('' if p.get('meteo_masquee') else ' checked', e(T('options.meteo')))
    return ('<details class="f96-options"><summary>%s</summary><form class="f96-form" data-f96-options autocomplete="off" novalidate>'
            '<label for="f96-prenom">%s</label><input id="f96-prenom" type="text" name="prenom" maxlength="40" value="%s" autocomplete="off" aria-describedby="f96-prenom-aide f96-options-message">'
            '<p class="f96-aide" id="f96-prenom-aide">%s</p>%s'
            '<button type="submit" class="ax-btn ghost">%s</button><p class="f96-message" id="f96-options-message" role="status" data-f96-message></p></form></details>') % (
        e(T('options.titre')), e(T('options.prenom')), e(str(p.get('prenom') or ''), quote=True), e(T('options.prenom_aide')), meteo, e(T('enregistrer')))


def bandeau_html(desk, owner):
    """Salutation rendue par le serveur ; le script lit le fuseau, le prénom et la langue dans les attributs."""
    s = salutation(desk, owner)
    return ('<p class="f96-bandeau" id="f96-bandeau" data-f96-horloge data-fuseau="%s" data-prenom="%s" data-lang="%s"><time datetime="%s">%s</time></p>'
            '<script type="application/json" id="f96-messages" data-f96-messages>%s</script>') % (
        e(s['fuseau'], quote=True), e(s['prenom'], quote=True), e(s['lang'], quote=True), e(s['datetime'], quote=True), e(s['texte']), _messages())


# ------------------------------------------------------------------------------------------------ carte (F04)
def carte_html(desk, owner, role, prefix):
    """Cellule météo de l'en-tête. Rien tant que la météo n'est pas activée (clé testée, lieu choisi), rien si la personne l'a masquée : le script
    ne charge alors rien. Ici seulement la coquille ; le contenu arrive par la route légère, après l'affichage."""
    from .assistant567 import profile
    cfg = meteo5696.configuration(desk)
    if not cfg['enabled']:
        if role == 'administrateur':      # l'administrateur voit où régler la météo, sans aucune requête
            return '<div class="f96-meteo f96-meteo-off"><a href="%s">%s</a></div>' % (e(prefix + '/parametres?rubrique=connexions#f96-reglage', quote=True), e(T('carte.regler')))
        return ''
    if profile(desk, owner).get('meteo_masquee'):
        return ''
    reglage = ('<a class="f96-meteo-lien" href="%s">%s</a>' % (e(prefix + '/parametres?rubrique=connexions#f96-reglage', quote=True), e(T('carte.changer_ville')))) if role == 'administrateur' else ''
    return ('<section class="f96-meteo" id="f96-meteo" data-f96-meteo data-url="%s" aria-labelledby="f96-meteo-titre">'
            '<details class="f96-meteo-pli"><summary class="f96-meteo-resume"><span class="f96-meteo-tete"><span id="f96-meteo-titre" class="f96-meteo-titre">%s</span>'
            '<span class="f96-meteo-ville">%s</span></span><span class="f96-meteo-jour" data-f96-jour><span class="f96-meteo-etat" data-f96-etat>%s</span></span></summary>'
            '<div class="f96-meteo-detail"><ol class="f96-jours" data-f96-jours></ol>'
            '<p class="f96-meteo-pied"><span data-f96-consulte></span> <span data-f96-ancien hidden>%s</span></p>'
            '<p class="f96-meteo-actions"><button type="button" class="ax-btn ghost" data-f96-masquer>%s</button>%s</p></div></details>'
            '<p class="f96-attribution"><a href="%s" target="_blank" rel="noopener noreferrer">%s</a></p></section>') % (
        e(prefix + '/api440/m5689/accueil/meteo', quote=True), e(T('carte.titre')), e(cfg['ville']), e(T('carte.chargement')), e(T('carte.ancien')),
        e(T('carte.masquer')), reglage, e(meteo5696.ATTRIBUTION_LIEN, quote=True), e(meteo5696.ATTRIBUTION))


def reglages_html(desk, prefix, role):
    """Carte de réglage de la météo (administrateur) : Paramètres › Connexions, et étape « sources » du guide d'installation. Champ de clé masqué,
    jamais prérempli ; « Tester » essaie la clé saisie **avant** de l'enregistrer ; lieu choisi parmi les homonymes ; activation explicite."""
    if role != 'administrateur':
        return ''
    cfg = meteo5696.configuration(desk)
    etat = T('reglage.etat_actif') if cfg['enabled'] else T('reglage.etat_coupe') if cfg['coupe'] else T('reglage.etat_inactif')
    cle = T('reglage.cle_testee', date=cfg['teste_le'][:16].replace('T', ' ')) if cfg['cle_testee'] else T('reglage.cle_enregistree_non_testee') if cfg['cle_enregistree'] else T('reglage.cle_absente')
    lieu = ('%s%s%s (%s, %s)' % (cfg['ville'], (', ' + cfg['region']) if cfg['region'] else '', (', ' + cfg['pays']) if cfg['pays'] else '', cfg['latitude'], cfg['longitude'])) if cfg['valide'] else T('reglage.lieu_absent')
    rev = '<input type="hidden" name="revision" value="%d">' % cfg['revision']
    return ('<section class="f96-reglage" id="f96-reglage" aria-labelledby="f96-reglage-titre" data-f96-reglage data-api="%s"><h3 id="f96-reglage-titre">%s</h3>'
            '<p>%s <strong>%s</strong></p><p class="f96-aide">%s</p>'
            '<p class="f96-aide"><a href="%s" target="_blank" rel="noopener noreferrer">%s</a></p>'
            '<form class="f96-form" data-f96-etape="tester" autocomplete="off" novalidate>%s<p>%s %s</p>'
            '<label for="f96-cle">%s</label><input id="f96-cle" type="password" name="cle" autocomplete="new-password" spellcheck="false" maxlength="128" aria-describedby="f96-cle-aide f96-cle-message">'
            '<p class="f96-aide" id="f96-cle-aide">%s</p><button type="submit" class="ax-btn">%s</button><p class="f96-message" id="f96-cle-message" role="status" data-f96-message></p></form>'
            '<form class="f96-form" data-f96-etape="lieux" autocomplete="off" novalidate><p>%s %s</p>'
            '<label for="f96-ville">%s</label><input id="f96-ville" type="text" name="ville" maxlength="80" required aria-describedby="f96-lieux-message">'
            '<label for="f96-pays">%s</label><input id="f96-pays" type="text" name="pays" maxlength="2" pattern="[A-Za-z]{2}" autocomplete="off">'
            '<button type="submit" class="ax-btn ghost">%s</button><p class="f96-message" id="f96-lieux-message" role="status" data-f96-message></p>'
            '<fieldset class="f96-homonymes" data-f96-homonymes hidden><legend>%s</legend><div data-f96-liste></div>'
            '<button type="button" class="ax-btn" data-f96-choisir>%s</button></fieldset></form>'
            '<form class="f96-form" data-f96-etape="activer" novalidate>%s<label class="f96-case"><input type="checkbox" name="actif"%s%s> %s</label>'
            '<button type="submit" class="ax-btn ghost">%s</button><p class="f96-message" role="status" data-f96-message></p></form>'
            '<form class="f96-form" data-f96-etape="effacer" data-confirmer="%s" novalidate>%s<button type="submit" class="ax-btn ghost"%s>%s</button><p class="f96-message" role="status" data-f96-message></p></form>'
            '<p class="f96-aide">%s</p><script type="application/json" data-f96-messages>%s</script><script defer src="%s"></script></section>') % (
        e(prefix + '/api440/m5689/accueil/reglage/', quote=True), e(T('reglage.titre')), e(T('reglage.etat')), e(etat), e(T('reglage.explication')),
        e(meteo5696.LIEN_CLE, quote=True), e(T('reglage.obtenir')),
        rev, e(T('reglage.cle')), e(cle), e(T('reglage.champ_cle')), e(T('reglage.cle_aide')), e(T('reglage.tester')),
        e(T('reglage.lieu')), e(lieu), e(T('reglage.ville')), e(T('reglage.pays')), e(T('reglage.chercher')), e(T('reglage.homonymes')), e(T('reglage.choisir')),
        rev, ' checked' if cfg['actif'] else '', '' if (cfg['cle_testee'] and cfg['valide']) or cfg['actif'] else ' disabled', e(T('reglage.activer')), e(T('enregistrer')),
        e(T('reglage.effacer_confirmer'), quote=True), rev, '' if cfg['cle_enregistree'] else ' disabled', e(T('reglage.effacer')),
        e(T('reglage.attribution')), _messages(), e(prefix + '/static/v5696-accueil.js', quote=True))


# ------------------------------------------------------------------------------------------------ rang (I01)
def _masques(desk, owner, role):
    from .membres5650 import hidden_for, viewer_hidden
    return frozenset(viewer_hidden() or ()) | frozenset(hidden_for(desk.c.get('state_dir'), owner, role) or ())


def _aujourdhui(desk, owner):
    return datetime.now(ZoneInfo(meteo5696.fuseau(desk, owner))).date()


def _echeances(desk, owner, hidden, statuts, jour=None, limite=20):
    """Échéances lues dans la table du moteur (aucun calcul ici) ; dossiers réservés retirés avant tout."""
    from . import echeances450 as ech
    ech.ensure_schema(desk)
    marques = ','.join('?' for _ in statuts)
    sql, args = 'SELECT * FROM deadlines450 WHERE status IN (%s)' % marques, list(statuts)
    if jour is not None:
        sql, args = sql + ' AND due=?', args + [jour.isoformat()]
    if hidden:
        sql, args = sql + ' AND matter NOT IN (%s)' % ','.join('?' for _ in hidden), args + sorted(hidden)
    rows = [ech.public(dict(r)) for r in desk.db.execute(sql + " ORDER BY CASE WHEN due='' THEN 1 ELSE 0 END, due, created LIMIT ?", args + [limite])]
    for r in rows:
        r['matter_label'] = ech.matter_label(desk, r['matter'])
    return rows


def actions(desk, prefix, owner, role, hidden=None):
    """Actions du rang, dans l'ordre de lecture : échéances à vérifier, puis la file « À décider / À relire » (même ordre que la file).
    Chaque action : nature (table fermée, ou '' : simple lien), clé opaque, dossier, objet, état, bouton et lien du détail (valide sans script).
    Rend aussi ``(complet, total)`` : une source illisible n'est jamais un zéro."""
    hidden = _masques(desk, owner, role) if hidden is None else hidden
    from . import accueil56100      # 5.6.100 (lot E, P2) : raison et ordre (échéances confirmées, audiences, décisions, travaux, dates à vérifier)
    if accueil56100.nouvelle(desk, owner):
        return accueil56100.classer(desk, prefix, owner, role, hidden)
    out, complet = [], True
    try:
        for r in _echeances(desk, owner, hidden, A_VERIFIER, limite=PREMIERES):
            out.append({'nature': 'echeance', 'cle': r['id'], 'dossier': r['matter_label'], 'objet': '%s — %s' % (r.get('due_label') or T('ligne.date_a_completer'), r['rule_label']),
                        'etat': T('ligne.a_verifier'), 'bouton': T('bouton.echeance'), 'href': prefix + '/echeances#e-' + r['id']})
    except Exception:   # noqa: BLE001 — la liste reste utilisable ; la panne est dite
        complet = False
    lignes, ok = actions_file(desk, prefix, owner, role, hidden)
    for a in lignes:
        for cle in [k for k in a if k.startswith('_')]:
            del a[cle]
    return out + lignes, complet and ok


def actions_file(desk, prefix, owner, role, hidden):
    """Lignes tirées de la file « À décider / À relire » du jour (même ordre que la file), avec quelques champs internes (préfixe ``_``) pour
    le classement de la 5.6.100 ; jamais le texte libre d'une demande dans l'objet (``titre_lisible``)."""
    out, complet = [], True
    try:
        from . import file5651, file5657
        lignes, _cur, _cli, _old = file5651.today_split(file5651.items(desk, prefix, None, '', hidden, actor=owner, role=role))
    except Exception:   # noqa: BLE001
        return [], False
    motif = re.compile(r'^%s/livrables/([A-Za-z0-9_-]{1,80})(?:[?#].*)?$' % re.escape(prefix))
    for it in lignes:
        nature, cle, bouton = '', '', T('bouton.ouvrir')
        objet = str(it.get('object') or '')
        trouve = motif.match(str(it.get('href') or ''))
        if it.get('class') == 'courriel' and objet.startswith('reponse5671:'):
            nature, cle, bouton = 'reponse', objet.split(':', 1)[1], T('bouton.reponse')
        elif trouve:
            nature, cle, bouton = 'document', trouve.group(1), T('bouton.document')
        decider = it.get('class') in file5651.FILTERS['decider']
        out.append({'nature': nature, 'cle': cle, 'dossier': it.get('matter_name') or '', 'objet': file5657.titre_lisible(it.get('title') or ''), 'href': it.get('href') or prefix + '/aujourdhui',
                    'etat': T('ligne.a_decider') if decider else T('ligne.a_relire'), 'bouton': bouton,
                    '_classe': it.get('class') or '', '_priorite': it.get('priority') or '', '_raison_prioritaire': it.get('priority_reason') or '', '_quand': it.get('when') or ''})
    return out, complet


def ligne_html(a):
    """Une ligne d'action : dossier, objet, état écrit, bouton. Le lien garde son adresse de détail (sans script : la page complète)."""
    data = (' data-f96-action="%s" data-f96-cle="%s" data-f96-titre="%s" data-f96-dossier="%s"' % (
        e(a['nature'], quote=True), e(a['cle'], quote=True), e(a['objet'], quote=True), e(a['dossier'], quote=True))) if a['nature'] in NATURES else ''
    # 5.6.100 (lot E) : catégorie et raison calculées (``accueil56100.classer``) ; identifiant du lecteur commun quand le service le fournit
    categorie = (' data-categorie="%s"' % e(a['categorie'], quote=True)) if a.get('categorie') else ''
    raison = ('<p class="f96-ligne-raison">%s</p>' % e(a['raison'])) if a.get('raison') else ''
    if a.get('lecteur') and data:
        data += ' data-ax100-id="%s"' % e(a['lecteur'], quote=True)
    return ('<li class="f96-ligne" data-f96-ligne="%s"%s><p class="f96-ligne-dossier">%s</p><p class="f96-ligne-objet">%s</p>%s'
            '<p class="f96-ligne-etat"><span class="f96-etat">%s</span></p><a class="ax-btn ghost f96-ligne-bouton" href="%s"%s aria-label="%s">%s</a></li>') % (
        e('%s:%s' % (a['nature'] or 'lien', a['cle'] or hashlib.sha256(a['href'].encode()).hexdigest()[:16]), quote=True), categorie, e(a['dossier'] or T('ligne.sans_dossier')), e(a['objet']),
        raison, e(a['etat']), e(a['href'], quote=True), data, e('%s : %s' % (a['bouton'], a['objet']), quote=True), e(a['bouton']))


def lignes_html(desk, prefix, owner, role):
    liste, complet = actions(desk, prefix, owner, role)
    if liste and liste[0].get('categorie'):        # 5.6.100 (lot E) : trois premières lignes, puis les dates à vérifier restantes, dites à part
        from .accueil56100 import a_verifier_html, identifiant_lecteur
        premieres = liste[:PREMIERES]
        for a in premieres:
            a['lecteur'] = identifiant_lecteur(desk, a['nature'], a['cle'])
        corps = '<ol class="f96-lignes">%s</ol>%s' % (''.join(ligne_html(a) for a in premieres), a_verifier_html(prefix, liste, premieres))
    elif liste:
        corps = '<ol class="f96-lignes">%s</ol>' % ''.join(ligne_html(a) for a in liste[:PREMIERES])
    elif complet:
        corps = '<p class="f96-vide">%s</p>' % e(T('rang.rien'))
    else:
        corps = ''
    panne = '' if complet else '<p class="f96-panne" role="status">%s</p>' % e(T('rang.panne'))
    return corps + panne, len(liste), complet


def decider_html(desk, prefix, owner, role, file_html):
    """Colonne gauche du rang : trois premières actions, puis la file complète (inchangée : mêmes cartes, mêmes boutons) dans « Voir la suite »."""
    lignes, total, _complet = lignes_html(desk, prefix, owner, role)
    suite = ''
    from . import accueil56100
    if accueil56100.nouvelle(desk, owner):
        # 5.6.100 (lot E, § 21) : « Voir la suite » ouvre la vue paginée (mêmes objets, même ordre) ; la file historique reste repliée ici,
        # une seule fois, pour ses cartes et son panneau des décisions (aucun défilement de plus tant qu'elle est fermée)
        suite = accueil56100.suite_html(prefix, total, PREMIERES)
        if file_html:
            suite += '<details class="f96-suite" data-f96-suite><summary>%s</summary>%s</details>' % (e('Déplier les cartes de la file ici'), file_html)
    elif file_html:
        libelle = T('rang.suite', n=total - PREMIERES) if total > PREMIERES else T('rang.file')
        suite = '<details class="f96-suite" data-f96-suite><summary>%s</summary>%s</details>' % (e(libelle), file_html)
    return ('<section class="c530-card f96-decider" id="f96-decider" aria-labelledby="f96-decider-titre" data-url="%s"><div class="c530-head"><h2 id="f96-decider-titre">%s</h2></div>'
            '<div id="f96-lignes-zone">%s</div><p class="f96-annonce" role="status" data-f96-annonce></p>%s</section>') % (
        e(prefix + '/api440/m5689/accueil/rang', quote=True), e(T('rang.titre')), lignes, suite)


def echeances_du_jour_html(desk, prefix, owner, role):
    """« Ma journée » : échéances confirmées dont le terme est aujourd'hui (fuseau du compte). Source illisible : dit comme telle, jamais un zéro."""
    try:
        rows = _echeances(desk, owner, _masques(desk, owner, role), CONFIRMEES, jour=_aujourdhui(desk, owner))
    except Exception:   # noqa: BLE001
        return '<p class="f96-panne" role="status">%s</p>' % e(T('journee.panne'))
    if not rows:
        return '<p class="f96-journee-vide">%s</p>' % e(T('journee.aucune'))
    return '<ul class="f96-echeances" aria-label="%s">%s</ul>' % (e(T('journee.titre'), quote=True), ''.join(
        '<li><a href="%s">%s</a> <span class="f96-ligne-dossier">%s</span> <span class="f96-etat">%s</span></li>' % (
            e(prefix + '/echeances#e-' + r['id'], quote=True), e(r['rule_label']), e(r['matter_label']), e(r['status_label'])) for r in rows))


def journee_html(desk, prefix, owner, role, journee):
    from . import accueil56100
    if accueil56100.nouvelle(desk, owner):
        # 5.6.100 (lot E, § 21.3) : sous « Ma journée », l'agenda lu avec les droits de la personne (aujourd'hui et demain, fuseau du cabinet,
        # état de synchronisation à part), puis les échéances confirmées du jour, puis les tâches et le Deck (bloc existant)
        try:
            from .cockpit530 import day_html
            debut = accueil56100.agenda_html(desk, prefix, owner, role) + ('<section class="f96-echeances-jour" aria-labelledby="f96-ech-titre"><h3 id="f96-ech-titre">%s</h3>%s</section>' % (
                e(T('journee.titre')), echeances_du_jour_html(desk, prefix, owner, role)))
            return '<div class="f96-journee" data-f100-journee><section class="c530-card" id="c530-day" aria-labelledby="c530-t-day">%s</section></div>' % day_html(desk, prefix, evenements_html=debut)
        except Exception:   # noqa: BLE001 — l'accueil s'affiche toujours : disposition précédente
            import logging
            logging.getLogger(__name__).exception('Ma journée 5.6.100')
    return ('<div class="f96-journee"><section class="f96-echeances-jour" aria-labelledby="f96-ech-titre"><h3 id="f96-ech-titre">%s</h3>%s</section>%s</div>') % (
        e(T('journee.titre')), echeances_du_jour_html(desk, prefix, owner, role), journee)


# ------------------------------------------------------------------------------------------------ fenêtre flottante (I02)
def _capture(titre, corps, *args, **kwargs):
    return titre, corps


def _fragment_document(env, desk, auth, prefix, cle, owner, role):
    """Même service de détail que la page ``/livrables/<id>`` (``livrables_web5689._detail_page``), mêmes droits, mêmes formulaires ; seul le cadre de page change."""
    from . import livrables_web5689 as lw
    if not re.fullmatch(r'[A-Za-z0-9_-]{1,80}', cle):
        raise Stop('introuvable')
    try:
        titre, corps = lw._detail_page(desk, env, auth, prefix, cle, {'return_to': prefix + '/aujourdhui'}, _capture)
    except Stop as ex:
        code = str(ex).split(':')[0]
        raise Stop('dossier_reserve' if code in ('dossier_reserve', 'acces_refuse', 'droit_insuffisant') or lw.STATUS.get(code, '').startswith('403') else 'introuvable') from None
    return {'titre': titre, 'corps': corps, 'page': prefix + '/livrables/' + cle}


def _fragment_reponse(env, desk, auth, prefix, cle, owner, role):
    """Réponse préparée en attente de décision : même contrôle (``reponses5671._load`` : dossiers réservés), même formulaire que la page du courriel."""
    if not re.fullmatch(r'[a-f0-9]{64}', cle):
        return _fragment_document(env, desk, auth, prefix, cle, owner, role)   # réponse déposée : document du registre (nature courriel)
    from . import reponses5671
    from .acces_courriel56100 import exiger as _exiger100
    _exiger100(desk, (owner, role), cle)   # 5.6.100 (R04) : garde commune (projet, message, rattachements) avant tout titre ; absent = interdit = 404
    try:
        data = reponses5671._load(desk, cle)
    except Stop as ex:
        raise Stop('controle_indisponible' if str(ex) == 'controle_indisponible' else 'introuvable') from None
    if str(data.get('matter') or '') in _masques(desk, owner, role):
        raise Stop('introuvable')
    value = data.get('result') or {}
    try:
        from .desk import report_for
        rapport = report_for(desk.c, cle)
    except Stop:
        rapport = {}
    tete = '<p class="f96-fragment-meta">%s · %s</p>' % (e(str(rapport.get('sender') or '')), e(str(rapport.get('received_at') or '')[:16].replace('T', ' ')))
    if value.get('body') and value.get('requires_decision'):
        from .relecture56100 import revision_proposition   # 5.6.100 (lot B, R09) : version affichée portée par chaque formulaire
        corps = reponses5671.editor_html(cle, value, data.get('verification'), revision=revision_proposition(data))
    else:
        corps = '<section class="r5671"><h2>%s</h2><pre class="f96-texte">%s</pre></section>' % (e(T('fenetre.reponse_preparee')), e(str(value.get('body') or '')))
    return {'titre': str(rapport.get('subject') or T('fenetre.courriel'))[:160], 'corps': tete + corps, 'page': prefix + '/mail?' + urlencode({'key': cle})}


def _fragment_echeance(env, desk, auth, prefix, cle, owner, role):
    """Échéance : lecture du moteur (aucun calcul, aucune écriture). Confirmer, corriger ou écarter reste dans Échéances, seul circuit de validation."""
    from . import deadlines450 as engine
    from . import echeances450 as ech
    if not re.fullmatch(r'[A-Za-z0-9_-]{1,80}', cle):
        raise Stop('introuvable')
    row = ech._row(desk, cle)
    if not row:
        raise Stop('introuvable')
    if str(row.get('matter') or '') in _masques(desk, owner, role):
        raise Stop('dossier_reserve')
    r = ech.public(row)
    calc = r['calc'] if isinstance(r['calc'], dict) else {}
    try:
        depart = engine.fr_date(datetime.strptime(r['start_date'], '%Y-%m-%d').date())
    except (TypeError, ValueError):
        depart = r.get('start_date') or ''
    verifiee = r['status'] in CONFIRMEES
    source = r['source_path'].rsplit('/', 1)[-1] if r.get('source_path') else T('fenetre.saisie_main')
    corps = ('<div class="f96-echeance"><p class="f96-avis" data-verifiee="%s">%s</p><dl>'
             '<dt>%s</dt><dd>%s</dd><dt>%s</dt><dd>%s</dd><dt>%s</dt><dd>%s, %s</dd><dt>%s</dt><dd>%s</dd><dt>%s</dt><dd>%s%s</dd></dl>'
             '<h3>%s</h3><ol>%s</ol><p><a class="ax-btn" href="%s">%s</a></p></div>') % (
        '1' if verifiee else '0', e(T('fenetre.echeance_confirmee') if verifiee else T('fenetre.echeance_a_verifier')),
        e(T('fenetre.terme')), e(r.get('due_label') or T('ligne.date_a_completer')), e(T('fenetre.statut')), e(r['status_label']),
        e(T('fenetre.depart')), e(r['start_event_label']), e(depart), e(T('fenetre.regle')), e(r['rule_label']),
        e(T('fenetre.source')), e(source), ('<blockquote>%s</blockquote>' % e(r['source_excerpt'])) if r.get('source_excerpt') else '',
        e(T('fenetre.calcul')), ''.join('<li>%s</li>' % e(s) for s in calc.get('steps', [])) or '<li>%s</li>' % e(T('fenetre.pas_de_calcul')),
        e(prefix + '/echeances#e-' + r['id'], quote=True), e(T('fenetre.ouvrir_echeances')))
    return {'titre': '%s — %s' % (r['rule_label'], ech.matter_label(desk, r['matter'])), 'corps': corps, 'page': prefix + '/echeances#e-' + r['id']}


FRAGMENTS = {'reponse': _fragment_reponse, 'echeance': _fragment_echeance, 'document': _fragment_document}


def fragment(env, desk, auth, prefix, nature, cle, owner, role):
    if nature not in NATURES:
        raise Stop('introuvable')
    out = FRAGMENTS[nature](env, desk, auth, prefix, str(cle or '')[:120], owner, role)
    return {'nature': nature, 'titre': out['titre'], 'html': out['corps'], 'page': out['page']}


# ------------------------------------------------------------------------------------------------ routes
def _correspond(entete, etag):
    valeurs = [v.strip() for v in str(entete or '').split(',') if v.strip()]
    return '*' in valeurs or any(v.replace('W/', '', 1).strip('"') == etag for v in valeurs)


def message_erreur(code):
    """Texte clair d'un refus, dans la langue de la personne ; un code sans texte au catalogue reçoit le message générique (jamais un détail du service)."""
    from .i18n5631 import catalog
    return T('erreur.' + code) if 'f96.erreur.' + code in catalog('fr') else T('erreur.refus')


def api(env, desk, auth, prefix, name, args, method):
    from .web440 import json_out
    try:
        return _route(env, desk, auth, prefix, name, args, method)
    except Stop as error:
        code = str(error).split(':')[0]
        return json_out({'error': code, 'message': message_erreur(code)}, STATUTS.get(code, '400 Bad Request'))


def _route(env, desk, auth, prefix, name, args, method):
    from .web440 import check_post, json_out
    from .web567 import actor
    from .assistant567 import profile, save_profile
    owner, role = actor(env)
    route = name[len('m5689/'):].split('?', 1)[0]
    if not allowed(role, route):
        raise Stop('role_insuffisant' if route in LECTURE + ECRITURE + REGLAGE else 'route_inconnue')
    if method == 'GET':
        if route == 'accueil/action':
            return json_out(fragment(env, desk, auth, prefix, str(args.get('nature') or ''), str(args.get('cle') or ''), owner, role))
        if route == 'accueil/rang':
            lignes, total, complet = lignes_html(desk, prefix, owner, role)
            return json_out({'html': lignes, 'total': total, 'complet': complet})
        if route != 'accueil/meteo':
            raise Stop('route_inconnue')
        if profile(desk, owner).get('meteo_masquee'):      # masquée par la personne : rien n'est consulté, ni cache ni service
            return json_out({'statut': 'masque', 'source': meteo5696.SOURCE, 'jours': []})
        corps = json_out(meteo5696.previsions(desk, owner))
        etag = hashlib.sha256(corps['body'].encode('utf-8')).hexdigest()[:32]
        entetes = [('ETag', '"%s"' % etag), ('Cache-Control', 'private, no-cache')]
        if _correspond(env.get('HTTP_IF_NONE_MATCH'), etag):
            return {'status': '304 Not Modified', 'kind': corps['kind'], 'body': '', 'headers': entetes}
        corps['headers'] = entetes
        return corps
    if method != 'POST':
        raise Stop('route_inconnue')
    data = check_post(env, auth)
    revision = data.get('revision')
    if route == 'accueil/options':
        champs = {}
        if 'prenom' in data:
            champs['prenom'] = data['prenom']
        if 'meteo_visible' in data:
            if not isinstance(data['meteo_visible'], bool):
                raise Stop('valeur_invalide')
            champs['meteo_masquee'] = not data['meteo_visible']
        if not champs:
            raise Stop('valeur_invalide')
        p = save_profile(desk, champs, owner)
        return json_out({'ok': True, 'prenom': p['prenom'], 'meteo_masquee': p['meteo_masquee'], 'salutation': salutation(desk, owner)['texte'], 'message': T('options.enregistre')})
    if route == 'accueil/reglage/tester':
        cfg = meteo5696.tester_cle(desk, owner, data.get('cle') or '', revision)
        return json_out({'ok': True, 'revision': cfg['revision'], 'message': T('reglage.test_ok')})
    if route == 'accueil/reglage/lieux':
        return json_out({'ok': True, 'lieux': meteo5696.chercher_lieux(desk, owner, data.get('ville'), data.get('pays') or '')})
    if route == 'accueil/reglage/lieu':
        cfg = meteo5696.choisir_lieu(desk, owner, data.get('lieu') if isinstance(data.get('lieu'), dict) else data, revision)
        return json_out({'ok': True, 'revision': cfg['revision'], 'message': T('reglage.lieu_ok', ville=cfg['ville'])})
    if route == 'accueil/reglage/activer':
        cfg = meteo5696.activer(desk, owner, data.get('actif'), revision)
        return json_out({'ok': True, 'revision': cfg['revision'], 'actif': cfg['actif'], 'message': T('reglage.active') if cfg['actif'] else T('reglage.desactive')})
    if route == 'accueil/reglage/effacer':
        cfg = meteo5696.effacer_cle(desk, owner, revision)
        return json_out({'ok': True, 'revision': cfg['revision'], 'message': T('reglage.efface')})
    raise Stop('route_inconnue')
