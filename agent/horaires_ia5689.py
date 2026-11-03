"""5.6.89 (cahier § 26 à 28) : pauses programmées de l'IA, politique commune des arrêts.

Le cabinet programme des plages pendant lesquelles l'IA ne travaille pas (exemple : chaque jour de 18 h à 7 h). Le réglage agit côté serveur,
même sans navigateur ouvert, et il est désactivé tant que le cabinet ne l'a pas activé.

- **Une seule décision** : ``evaluer`` reçoit l'heure UTC (injectée par les essais) et combine, dans cet ordre, annulation terminale, droits et
  sécurité, pause manuelle, pause du dossier, plage horaire. Elle renvoie autorisé, motifs actifs, prochaine transition UTC, fuseau et révision.
  Les contrôles existants (``pause5629``, ``arret5643``) la consultent avant chaque calcul et chaque effet : aucun cache ne fait autorité.
- **Aucun minuteur sur la pause manuelle** : ``missions567:pause`` reste le seul fait de l'avocat. La plage est recalculée, jamais écrite dans ce
  booléen : « Reprendre » ne touche pas aux horaires, et la fin d'une plage ne lève pas une pause manuelle.
- **Heures locales, bornes UTC** : les règles restent en heure locale avec un fuseau IANA ; chaque borne est construite à la date locale puis
  convertie. Début inclus, fin exclue ; une fin antérieure au début appartient au lendemain ; les jours cochés sont ceux du début. Une borne
  inexistante au printemps prend le premier instant valide suivant ; une heure répétée à l'automne prend le premier instant pour un début, le
  dernier pour une fin. Le lendemain se calcule sur la date, jamais par 86 400 secondes.
- **Illisible = bloqué** : sur une instance configurée, une politique illisible ou un fuseau introuvable bloque les nouveaux appels à l'IA et
  affiche un incident ; aucun calcul par défaut en mode ouvert. Les services sans IA continuent.
- **Dérogation** : 30 minutes au plus, enregistrée à part avec auteur, échéance et périmètre. Elle ne neutralise que le motif horaire.
- **Surveillance légère** : ``surveiller`` s'appuie sur la boucle des automatismes et le superviseur ; sans modèle, sans travail mis en file, sans
  écriture tant que l'état ne change pas. Une transition est prise sous ``BEGIN IMMEDIATE`` : un seul travailleur l'enregistre.

Une réponse du modèle reçue après le début d'une pause n'est ni publiée ni utilisée : elle est gardée comme résultat partiel, et le travail repris
recalcule à partir des sources du moment. L'arrêt du calcul chez le fournisseur n'est jamais affirmé : seule l'interruption est demandée.
"""
from datetime import date, datetime, timedelta, timezone
import hashlib
from html import escape
import json
from pathlib import Path
import re
import sqlite3
import uuid
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError, available_timezones

from .common import Stop

CLE_POLITIQUE = 'horaires_ia5689:policy'
CLE_DEROGATION = 'horaires_ia5689:derogation'      # à part de la politique : auteur, échéance et périmètre
CLE_ETAT = 'horaires_ia5689:etat'                  # dernière transition enregistrée (une seule entre plusieurs travailleurs)
CLE_PURGE = 'horaires_ia5689:purge'
PAUSE_GLOBALE = 'missions567:pause'
PAUSE_DOSSIERS = 'pause5629:matters'
CLES = (PAUSE_GLOBALE, PAUSE_DOSSIERS, CLE_POLITIQUE, CLE_DEROGATION)

MOTIF = 'pause_horaire'
CODE = 'ia_en_pause_horaire'
CODE_ILLISIBLE = 'horaires_ia_illisibles'
FUSEAU_DEFAUT = 'Europe/Paris'
PLAGES_MAX = 50
JOURS_AVANT, JOURS_APRES = 2, 10                   # dates locales examinées autour de l'instant : une semaine complète et les nuits à cheval
DEROGATION_MINUTES = 30
PERIMETRES = {'demandes': 'Demandes de l’avocat, sans les automatismes', 'cabinet': 'Tout le travail de l’IA du cabinet', 'dossier': 'Un seul dossier'}
REPRISE_IMMEDIATE = 5                              # travaux automatiques repris d'un coup ; les suivants sont espacés (pas de rafale)
REPRISE_ESPACEMENT = 15
SUSPENDUS_MAX = 500
TARDIVES_JOURS = 30                                # conservation des réponses tardives
CLES_ECHEANCE = ('echeance_absolue', 'not_after', 'expires_at')
HEURE = re.compile(r'(?:[01][0-9]|2[0-3]):[0-5][0-9]')
HEURE_FIN = re.compile(r'(?:(?:[01][0-9]|2[0-3]):[0-5][0-9]|24:00)')
NOM_FUSEAU = re.compile(r'[A-Za-z0-9_+-]+(?:/[A-Za-z0-9_+-]+)*')
JOURS_NOMS = ('lundi', 'mardi', 'mercredi', 'jeudi', 'vendredi', 'samedi', 'dimanche')
INCIDENTS = {'politique_illisible': 'Le réglage des horaires est illisible.',
             'fuseau_introuvable': 'Le fuseau horaire du cabinet est introuvable sur ce serveur (base des fuseaux absente ou incomplète).'}


def maintenant():
    """Heure UTC du contrôleur ; les essais la remplacent par une horloge simulée."""
    return datetime.now(timezone.utc)


def _utc(now=None):
    now = now if now is not None else maintenant()
    return (now if now.tzinfo else now.replace(tzinfo=timezone.utc)).astimezone(timezone.utc)


def politique_defaut():
    """Politique d'une instance qui n'a rien réglé : planning désactivé, le fonctionnement existant est conservé."""
    return {'schema_version': 1, 'revision': 0, 'enabled': False, 'timezone': FUSEAU_DEFAUT, 'scope': 'cabinet', 'windows': []}


# ---------------------------------------------------------------- validation
def _zone(nom):
    """Fuseau IANA ; une base absente (serveur sans ``tzdata``) donne le même refus qu'un nom inconnu : jamais de repli silencieux sur UTC."""
    if not isinstance(nom, str) or not NOM_FUSEAU.fullmatch(nom):
        raise Stop('horaires_fuseau_inconnu')
    try:
        return ZoneInfo(nom)
    except (ZoneInfoNotFoundError, ValueError, OSError, ImportError):
        raise Stop('horaires_fuseau_inconnu') from None


def fuseau_disponible(nom):
    """Le nom doit figurer dans la base de fuseaux du poste : ``localtime`` et les fichiers annexes ne sont pas des fuseaux IANA."""
    _zone(nom)
    try:
        return nom in available_timezones()
    except (OSError, ValueError, ImportError):
        return False


def _minutes(texte):
    return int(texte[:2]) * 60 + int(texte[3:5])


def valider(data, fuseau_requis=True):
    """Contrôle métier au-delà du schéma : clés fermées, identifiants uniques, heures, début différent de la fin équivalente, 24:00 ramené au
    minuit suivant. Renvoie la politique normalisée ; ``Stop`` avec un code précis sinon."""
    if not isinstance(data, dict) or set(data) != {'schema_version', 'revision', 'enabled', 'timezone', 'scope', 'windows'}:
        raise Stop('horaires_format_invalide')
    revision, plages = data['revision'], data['windows']
    if (data['schema_version'] != 1 or data['scope'] != 'cabinet' or isinstance(revision, bool) or not isinstance(revision, int) or revision < 0
            or not isinstance(data['enabled'], bool) or not isinstance(plages, list)):
        raise Stop('horaires_format_invalide')
    if not isinstance(data['timezone'], str) or not 1 <= len(data['timezone']) <= 100:
        raise Stop('horaires_fuseau_inconnu')
    if fuseau_requis and not fuseau_disponible(data['timezone']):
        raise Stop('horaires_fuseau_inconnu')
    if len(plages) > PLAGES_MAX:
        raise Stop('horaires_trop_de_plages')
    vues, sortie = set(), []
    for plage in plages:
        if not isinstance(plage, dict) or not isinstance(plage.get('id'), str) or not 1 <= len(plage['id']) <= 80:
            raise Stop('horaires_format_invalide')
        if plage['id'] in vues:
            raise Stop('horaires_plage_en_double')
        vues.add(plage['id'])
        jours = plage.get('days')
        if (not isinstance(jours, list) or not 1 <= len(jours) <= 7 or len(set(map(str, jours))) != len(jours)
                or any(isinstance(j, bool) or not isinstance(j, int) or not 1 <= j <= 7 for j in jours)):
            raise Stop('horaires_jours_invalides')
        if plage.get('kind') == 'full_day':
            if set(plage) != {'id', 'days', 'kind'}:
                raise Stop('horaires_format_invalide')
            sortie.append({'id': plage['id'], 'days': sorted(jours), 'kind': 'full_day'})
            continue
        if plage.get('kind') != 'period' or set(plage) != {'id', 'days', 'kind', 'start', 'end'}:
            raise Stop('horaires_format_invalide')
        debut, fin = plage['start'], plage['end']
        if not isinstance(debut, str) or not isinstance(fin, str) or not HEURE.fullmatch(debut) or not HEURE_FIN.fullmatch(fin):
            raise Stop('horaires_heure_invalide')
        fin = '00:00' if fin == '24:00' else fin                   # 24:00 : minuit suivant ; 00:00 → 24:00 équivaut à début égal à fin
        if debut == fin:
            raise Stop('horaires_debut_egal_fin')
        sortie.append({'id': plage['id'], 'days': sorted(jours), 'kind': 'period', 'start': debut, 'end': fin})
    return {'schema_version': 1, 'revision': revision, 'enabled': data['enabled'], 'timezone': data['timezone'], 'scope': 'cabinet', 'windows': sortie}


# ---------------------------------------------------------------- calcul des bornes
def _instant(jour, minutes, zone, fin=False):
    """Date et heure locales → instant UTC. Heure répétée : premier instant pour un début, dernier pour une fin. Heure inexistante : premier
    instant valide suivant."""
    locale = datetime(jour.year, jour.month, jour.day, minutes // 60, minutes % 60)
    avant = locale.replace(tzinfo=zone, fold=0).astimezone(timezone.utc)
    apres = locale.replace(tzinfo=zone, fold=1).astimezone(timezone.utc)
    if avant == apres:
        return avant
    if avant < apres:
        return apres if fin else avant
    bas, haut = int(apres.timestamp()), int(avant.timestamp())     # le changement d'heure se trouve entre les deux lectures de l'heure
    decalage = datetime.fromtimestamp(haut, zone).utcoffset()
    while haut - bas > 1:
        milieu = (bas + haut) // 2
        if datetime.fromtimestamp(milieu, zone).utcoffset() == decalage:
            haut = milieu
        else:
            bas = milieu
    return datetime.fromtimestamp(haut, timezone.utc)


def plages_utc(politique, premier, dernier):
    """Union des plages dont le jour de début est dans [premier, dernier] : (intervalles UTC fusionnés, intervalles vides expliqués)."""
    zone = _zone(politique['timezone'])
    brutes, ignorees = [], []
    jour = premier
    while jour <= dernier:
        demain = jour + timedelta(days=1)
        for plage in politique['windows']:
            if jour.isoweekday() not in plage['days']:
                continue
            if plage['kind'] == 'full_day':
                bornes = _instant(jour, 0, zone), _instant(demain, 0, zone, fin=True)
            else:
                debut, fin = _minutes(plage['start']), _minutes(plage['end'])
                bornes = _instant(jour, debut, zone), _instant(jour if fin > debut else demain, fin, zone, fin=True)
            if bornes[1] <= bornes[0]:                             # plage entièrement dans l'heure sautée au printemps
                ignorees.append({'plage': plage['id'], 'jour': jour.isoformat(), 'motif': 'intervalle_vide'})
                continue
            brutes.append(bornes)
        jour = demain
    fusion = []
    for debut, fin in sorted(brutes):
        if fusion and debut <= fusion[-1][1]:                      # chevauchement ou plages jointives : aucune reprise entre les deux
            fusion[-1] = (fusion[-1][0], max(fusion[-1][1], fin))
        else:
            fusion.append((debut, fin))
    return fusion, ignorees


def calcul(politique, now):
    """Situation à l'instant ``now`` : en pause ou non, fin de la pause, prochaine pause, plages ignorées à venir."""
    zone = _zone(politique['timezone'])
    local = now.astimezone(zone).date()
    plages, ignorees = plages_utc(politique, local - timedelta(days=JOURS_AVANT), local + timedelta(days=JOURS_APRES))
    pause, fin, prochaine = False, None, None
    for debut_plage, fin_plage in plages:
        if debut_plage <= now < fin_plage:
            pause, fin = True, fin_plage
        elif debut_plage > now:
            prochaine = debut_plage
            break
    continu = pause and fin >= now + timedelta(days=8)             # une semaine entière couverte : aucune fin dans l'horizon n'est réelle
    return {'pause': pause, 'fin': None if continu else fin, 'continu': continu, 'prochaine': None if continu else prochaine,
            'ignorees': [i for i in ignorees if i['jour'] >= (local - timedelta(days=1)).isoformat()]}


# ---------------------------------------------------------------- lecture fraîche
def photo(source):
    """Réglages utiles lus sans cache : ``source`` est un Desk, une connexion SQLite ou le dossier d'état. ``{}`` : instance non configurée."""
    base, ouverte = getattr(source, 'db', source), None
    if isinstance(source, (str, Path)):
        chemin = Path(source or '') / 'desk.sqlite3'
        if not source or not chemin.is_file():
            return {}
        try:
            base = ouverte = sqlite3.connect(chemin.resolve().as_uri() + '?mode=ro', uri=True, timeout=2)
        except sqlite3.Error:
            raise Stop('controle_pause_indisponible') from None
    try:
        return dict(base.execute('SELECT key,value FROM settings WHERE key IN (%s)' % ','.join('?' * len(CLES)), CLES).fetchall())
    except (sqlite3.Error, TypeError, ValueError):
        raise Stop('controle_pause_indisponible') from None
    finally:
        if ouverte is not None:
            ouverte.close()


def _derogation(brut, now):
    """Dérogation encore valable ; illisible ou échue : aucune (le planning s'applique)."""
    try:
        d = json.loads(brut) if brut else None
        if not isinstance(d, dict) or not d.get('id') or d.get('perimetre') not in PERIMETRES:
            return None
        return d if _date(d.get('fin')) > now else None
    except (TypeError, ValueError):
        return None


def _date(texte):
    valeur = datetime.fromisoformat(str(texte).replace('Z', '+00:00'))
    return (valeur if valeur.tzinfo else valeur.replace(tzinfo=timezone.utc)).astimezone(timezone.utc)


def couvre(derogation, automatic, matter):
    """Périmètre de la dérogation : un travail hors périmètre reste en pause."""
    if not derogation:
        return False
    if derogation['perimetre'] == 'cabinet':
        return True
    if derogation['perimetre'] == 'demandes':
        return not automatic
    return bool(matter) and str(matter) == str(derogation.get('dossier') or '')


def lire_horaire(valeurs, now):
    """État du seul planning. Politique absente : désactivé (migration). Politique illisible, fuseau introuvable : incident, jamais « libre »."""
    brut = valeurs.get(CLE_POLITIQUE)
    sortie = {'etat': 'libre', 'pause': False, 'fin': None, 'prochaine': None, 'continu': False, 'ignorees': [], 'incident': '', 'derogation': None,
              'politique': politique_defaut()}
    if brut is None:
        return sortie
    try:
        politique = valider(json.loads(brut), fuseau_requis=False)
    except (TypeError, ValueError, KeyError, Stop):
        return {**sortie, 'etat': 'incident', 'incident': 'politique_illisible', 'politique': _revision_lisible(brut)}
    sortie['politique'] = politique
    if not politique['enabled']:
        return sortie
    try:
        sortie.update(calcul(politique, now))
    except Stop:
        return {**sortie, 'etat': 'incident', 'incident': 'fuseau_introuvable'}
    if sortie['pause']:
        sortie['derogation'] = _derogation(valeurs.get(CLE_DEROGATION), now)
        sortie['etat'] = 'derogation:' + str(sortie['derogation']['id']) if sortie['derogation'] else 'pause'
    return sortie


def _revision_lisible(brut):
    """Politique illisible : la révision reste récupérable pour que l'administrateur puisse réparer le réglage."""
    politique = politique_defaut()
    try:
        ancienne = json.loads(brut)
        if isinstance(ancienne, dict) and isinstance(ancienne.get('revision'), int) and not isinstance(ancienne.get('revision'), bool):
            politique['revision'] = max(0, ancienne['revision'])
    except (TypeError, ValueError):
        pass
    return politique


def code_de(horaire, matter='', automatic=False):
    if horaire['incident']:
        return CODE_ILLISIBLE
    if horaire['pause'] and not couvre(horaire['derogation'], automatic, matter):
        return CODE
    return ''


def code_horaire(source, now=None, matter='', automatic=False):
    """Code d'arrêt du seul planning ('' : pas de blocage horaire). Appelé juste avant chaque appel au modèle et chaque effet autonome."""
    valeurs = photo(source)
    if CLE_POLITIQUE not in valeurs:
        return ''
    return code_de(lire_horaire(valeurs, _utc(now)), matter, automatic)


def evaluer(source, now=None, matter='', automatic=False, job_id=None, args=None, state_dir=None):
    """Décision unique : annulation terminale, droits et sécurité, pause manuelle, pause du dossier, plage horaire. La dérogation ne neutralise que
    le dernier de ces motifs. ``code`` est le motif le plus prioritaire ; les autres restent listés dans ``motifs``."""
    now = _utc(now)
    valeurs = photo(source)
    horaire = lire_horaire(valeurs, now)
    matter = str(matter or '')
    motifs = []
    if job_id:
        from .arret5643 import job_stop_code
        dossier = state_dir or (source if isinstance(source, (str, Path)) else getattr(source, 'c', {}).get('state_dir'))
        arret = job_stop_code(dossier, job_id)
        if arret:
            motifs.append(('annulation' if arret == 'operation_annulee' else 'arret_du_travail', arret))
    if args and hasattr(source, 'db'):
        from .missions567 import check_authority
        try:
            check_authority(source, args)
        except Stop as error:
            motifs.append(('droits', str(error)))
    if horaire['incident']:
        motifs.append(('securite', CODE_ILLISIBLE))
    try:
        if json.loads(valeurs.get(PAUSE_GLOBALE) or 'false'):
            motifs.append(('pause_manuelle', 'ia_en_pause'))
        dossiers = json.loads(valeurs.get(PAUSE_DOSSIERS) or '{}')
    except (TypeError, ValueError):
        raise Stop('controle_pause_indisponible') from None
    if automatic and matter and isinstance(dossiers, dict) and matter in dossiers:
        motifs.append(('pause_dossier', 'agent_en_pause_sur_ce_dossier'))
    if not horaire['incident'] and horaire['pause'] and not couvre(horaire['derogation'], automatic, matter):
        motifs.append((MOTIF, CODE))
    return {'autorise': not motifs, 'motifs': [m for m, _ in motifs], 'code': motifs[0][1] if motifs else '',
            'prochaine_transition_utc': _prochaine(horaire), 'fuseau': horaire['politique']['timezone'], 'revision': horaire['politique']['revision'],
            'incident': horaire['incident'], 'derogation': _publique(horaire['derogation']), 'ignorees': horaire['ignorees']}


def _prochaine(horaire):
    if horaire['incident'] or not horaire['politique']['enabled']:
        return None
    instants = [i for i in (horaire['fin'] if horaire['pause'] else horaire['prochaine'],) if i]
    if horaire['derogation']:
        instants.append(_date(horaire['derogation']['fin']))
    return min(instants).isoformat() if instants else None


def _publique(derogation):
    if not derogation:
        return None
    return {k: derogation.get(k, '') for k in ('id', 'par', 'debut', 'fin', 'perimetre', 'dossier')}


# ---------------------------------------------------------------- phrases affichées
def _heure(local):
    return '%d h' % local.hour + (' %02d' % local.minute if local.minute else '')


def _jour(local, ici):
    ecart = (local.date() - ici.date()).days
    if ecart == 0:
        return 'aujourd’hui'
    if ecart == 1:
        return 'demain'
    return JOURS_NOMS[local.weekday()] if 1 < ecart < 7 else 'le ' + local.strftime('%d/%m/%Y')


def synthese(horaire, now):
    """La ligne de synthèse : un état exprimé en mots, jamais par la seule couleur."""
    now = _utc(now)
    if horaire['incident']:
        return 'Incident : ' + INCIDENTS[horaire['incident']] + ' Les nouveaux appels à l’IA sont bloqués tant que ce n’est pas corrigé.'
    politique = horaire['politique']
    if not politique['enabled']:
        return 'Programmation désactivée : aucune pause programmée.'
    zone = _zone(politique['timezone'])
    ici = now.astimezone(zone)
    if horaire['pause']:
        if horaire['continu']:
            texte = 'IA en pause programmée, sans reprise prévue'
        else:
            fin = horaire['fin'].astimezone(zone)
            texte = 'IA en pause programmée jusqu’à %s %s' % (_jour(fin, ici), _heure(fin))
        if horaire['derogation']:
            texte += ' ; dérogation jusqu’à ' + _heure(_date(horaire['derogation']['fin']).astimezone(zone))
        return texte
    if horaire['prochaine']:
        debut = horaire['prochaine'].astimezone(zone)
        return 'Prochaine pause %s à %s' % (_jour(debut, ici), _heure(debut))
    return 'Aucune pause prévue dans les prochains jours.'


def explications(horaire):
    """Plages sans effet ce jour-là (heure sautée au printemps), en clair."""
    noms = {}
    for item in horaire['ignorees']:
        noms.setdefault(item['jour'], item)
    return ['Le %s, une plage tombe entièrement dans l’heure supprimée au passage à l’heure d’été : elle est ignorée ce jour-là.'
            % date.fromisoformat(jour).strftime('%d/%m/%Y') for jour in sorted(noms)]


def etat_public(desk, now=None):
    """Lecture du planning pour l'écran et l'API : réglage, synthèse, fuseau, prochaine transition, travaux suspendus."""
    now = _utc(now)
    valeurs = photo(desk)
    horaire = lire_horaire(valeurs, now)
    try:
        suspendus = desk.db.execute("SELECT COUNT(*) FROM jobs WHERE status='suspended' AND stop_reason=?", (MOTIF,)).fetchone()[0]
    except sqlite3.Error:
        suspendus = 0
    return {'politique': horaire['politique'], 'synthese': synthese(horaire, now), 'en_pause': horaire['pause'], 'incident': horaire['incident'],
            'incident_message': INCIDENTS.get(horaire['incident'], ''), 'fuseau': horaire['politique']['timezone'], 'revision': horaire['politique']['revision'],
            'prochaine_transition_utc': _prochaine(horaire), 'derogation': _publique(horaire['derogation']), 'explications': explications(horaire),
            'travaux_suspendus': suspendus, 'reponses_tardives': _nombre_tardives(desk)}


def bandeau_html(desk, prefix):
    """Bandeau de la page d'accueil : incident du planning (alerte) ou pause programmée en cours. Rien sans planning actif."""
    now = _utc()
    valeurs = photo(desk)
    if CLE_POLITIQUE not in valeurs:
        return ''
    horaire = lire_horaire(valeurs, now)
    if not horaire['incident'] and not horaire['pause']:
        return ''
    lien = '<a class="ax-btn ghost" href="%s">Horaires de fonctionnement</a>' % escape(prefix + '/parametres?rubrique=horaires', quote=True)
    return '<div class="b5668-banner b5668-full" role="%s"><p><strong>%s</strong></p><p class="b5668-actions">%s</p></div>' % (
        'alert' if horaire['incident'] else 'status', escape(synthese(horaire, now)), lien)


# ---------------------------------------------------------------- écritures
def _depuis_formulaire(data):
    """Corps de requête → politique à valider ; la révision reçue est celle que le formulaire a lue."""
    if not isinstance(data, dict):
        raise Stop('horaires_format_invalide')
    plages = data.get('windows')
    if not isinstance(plages, list):
        raise Stop('horaires_format_invalide')
    plages = [{**p, 'id': str(p.get('id') or uuid.uuid4().hex[:12])} if isinstance(p, dict) else p for p in plages]
    return {'schema_version': 1, 'revision': data.get('revision'), 'enabled': data.get('enabled'), 'timezone': data.get('timezone'), 'scope': 'cabinet', 'windows': plages}


def apercu(desk, data, now=None):
    """Avant validation : ce que le réglage proposé produirait maintenant, sans rien écrire (dont l'effet immédiat d'une pause)."""
    now = _utc(now)
    politique = valider(_depuis_formulaire(data))
    actuel = lire_horaire(photo(desk), now)
    futur = lire_horaire({CLE_POLITIQUE: json.dumps(politique)}, now)
    immediate = futur['pause'] and not actuel['pause']
    return {'synthese': synthese(futur, now), 'pause_immediate': immediate, 'explications': explications(futur), 'fuseau': politique['timezone'],
            'prochaine_transition_utc': _prochaine(futur),
            'message': ('Enregistrer ce réglage met l’IA en pause immédiatement : les travaux en cours seront suspendus, puis repris à la fin de la plage.'
                        if immediate else '')}


def enregistrer(desk, data, auteur, confirmer=False, now=None):
    """Écriture du planning : la révision lue doit être la révision actuelle (409 sinon), et une pause à effet immédiat doit avoir été annoncée."""
    now = _utc(now)
    politique = valider(_depuis_formulaire(data))
    if desk.db.in_transaction:
        desk.db.commit()
    desk.db.execute('BEGIN IMMEDIATE')
    try:
        actuel = lire_horaire(photo(desk), now)
        if politique['revision'] != actuel['politique']['revision']:
            raise Stop('revision_perimee')
        politique['revision'] += 1
        futur = lire_horaire({CLE_POLITIQUE: json.dumps(politique)}, now)
        if futur['pause'] and not actuel['pause'] and not confirmer:
            raise Stop('confirmation_pause_immediate_requise')
        desk.db.execute('INSERT OR REPLACE INTO settings VALUES (?,?)', (CLE_POLITIQUE, json.dumps(politique)))
        desk.db.commit()
    except BaseException:
        if desk.db.in_transaction:
            desk.db.rollback()
        raise
    desk.audit('horaires_ia5689_modifie', {'revision': politique['revision'], 'active': politique['enabled'], 'fuseau': politique['timezone'],
                                           'plages': len(politique['windows']), 'par': str(auteur)[:120]})
    surveiller(desk, now)
    return etat_public(desk, now)


def accorder_derogation(desk, auteur, perimetre='demandes', dossier='', cle='', now=None):
    """« Autoriser exceptionnellement pendant 30 minutes » : le seul planning, dans un périmètre visible. Même clé : même dérogation, sans second journal."""
    now = _utc(now)
    if perimetre not in PERIMETRES:
        raise Stop('derogation_perimetre_invalide')
    dossier = str(dossier or '')
    if perimetre == 'dossier':
        from .common import load_matters
        if not any(str(m['id']) == dossier for m in load_matters(desk.c)):
            raise Stop('dossier_absent')
    else:
        dossier = ''
    horaire = lire_horaire(photo(desk), now)
    if horaire['incident']:
        raise Stop(CODE_ILLISIBLE)                                 # une dérogation ne lève jamais un blocage de sécurité
    if not horaire['pause']:
        raise Stop('aucune_pause_programmee_en_cours')
    cle = str(cle or '')[:80]
    if cle and horaire['derogation'] and horaire['derogation'].get('cle') == cle:
        return etat_public(desk, now)
    derogation = {'id': uuid.uuid4().hex, 'par': str(auteur)[:120], 'debut': now.isoformat(), 'fin': (now + timedelta(minutes=DEROGATION_MINUTES)).isoformat(),
                  'perimetre': perimetre, 'dossier': dossier, 'cle': cle}
    desk.setting(CLE_DEROGATION, derogation)
    desk.audit('horaires_ia5689_derogation', {'id': derogation['id'], 'par': derogation['par'], 'fin': derogation['fin'], 'perimetre': perimetre, 'dossier': dossier})
    surveiller(desk, now)
    return etat_public(desk, now)


def retirer_derogation(desk, auteur, now=None):
    now = _utc(now)
    if desk.db.execute('DELETE FROM settings WHERE key=?', (CLE_DEROGATION,)).rowcount:
        desk.db.commit()
        desk.audit('horaires_ia5689_derogation_retiree', {'par': str(auteur)[:120]})
        surveiller(desk, now)
    else:
        desk.db.commit()
    return etat_public(desk, now)


# ---------------------------------------------------------------- transitions
def _suspendre(desk, horaire, now):
    """Entrée en pause : les travaux qui appellent le modèle, hors périmètre d'une dérogation, sont suspendus (reprenables) ; un travail automatique
    actif reçoit une demande d'arrêt. Un travail de l'avocat en cours s'arrête à son prochain appel au modèle, à un point cohérent."""
    from .arret5643 import SANS_MODELE, is_automatic_row
    message = json.dumps({'message': 'Suspendu : pause programmée de l’IA. Repris automatiquement à la fin de la plage.'}, ensure_ascii=False)
    arret = json.dumps({'message': 'Arrêt demandé par la pause programmée ; confirmation du worker attendue.'}, ensure_ascii=False)
    compte = {'suspendus': 0, 'arrets_demandes': 0, 'en_cours': 0}
    for ligne in desk.db.execute("SELECT id,kind,status,origin,priority,args,matter_ref FROM jobs WHERE status IN ('pending','running')").fetchall():
        if ligne['kind'] in SANS_MODELE:
            continue
        automatic = is_automatic_row(ligne, _args(ligne))
        if couvre(horaire['derogation'], automatic, ligne['matter_ref']):
            continue
        if ligne['status'] == 'pending':
            compte['suspendus'] += desk.db.execute("UPDATE jobs SET status='suspended',stop_reason=?,result=?,pause_debut=COALESCE(pause_debut,?) WHERE id=? AND status='pending'",
                                                   (MOTIF, message, now.isoformat(), ligne['id'])).rowcount
        elif automatic:
            compte['arrets_demandes'] += desk.db.execute("UPDATE jobs SET status='cancel_requested',stop_reason=?,result=? WHERE id=? AND status='running'",
                                                         (MOTIF, arret, ligne['id'])).rowcount
        else:
            compte['en_cours'] += 1
    return compte


def _args(ligne):
    try:
        args = json.loads(ligne['args'] or '{}')
    except (TypeError, ValueError):
        return {}
    return args if isinstance(args, dict) else {}


def _etat_note(desk):
    ligne = desk.db.execute('SELECT value FROM settings WHERE key=?', (CLE_ETAT,)).fetchone()
    try:
        note = json.loads(ligne[0]) if ligne else {}
    except (TypeError, ValueError):
        note = {}
    return str(note.get('etat') or 'libre') if isinstance(note, dict) else 'libre'


def surveiller(desk, now=None):
    """Contrôleur léger : recalcule l'état et n'agit que s'il a changé depuis la dernière transition enregistrée. Aucun rattrapage : un serveur arrêté
    pendant plusieurs transitions n'en voit qu'une à son retour. Renvoie la transition prise, ou None (autre travailleur, ou rien à faire)."""
    now = _utc(now)
    horaire = lire_horaire(photo(desk), now)
    etat = horaire['etat']
    if _etat_note(desk) == etat:
        return _reliquat(desk, horaire, now)
    if desk.db.in_transaction:
        desk.db.commit()
    desk.db.execute('BEGIN IMMEDIATE')                             # un seul travailleur prend la transition
    try:
        ancien = _etat_note(desk)
        if ancien == etat:
            desk.db.rollback()
            return None
        desk.db.execute('INSERT OR REPLACE INTO settings VALUES (?,?)', (CLE_ETAT, json.dumps({'etat': etat, 'depuis': now.isoformat()})))
        compte = _suspendre(desk, horaire, now) if etat != 'libre' else {}
        if not horaire['derogation']:
            desk.db.execute('DELETE FROM settings WHERE key=?', (CLE_DEROGATION,))   # dérogation échue : elle ne sert plus
        desk.audit('horaires_ia5689_transition', {'de': ancien.split(':')[0], 'vers': etat.split(':')[0], 'revision': horaire['politique']['revision'],
                                                  'incident': horaire['incident'], **compte})
    except BaseException:
        if desk.db.in_transaction:
            desk.db.rollback()
        raise
    repris = 0
    if etat == 'libre' or horaire['derogation']:
        from .arret5643 import resume
        repris = resume(desk, now=now)                             # chaque travail est réévalué : pause manuelle, dossier, annulation
        if repris:
            desk.audit('horaires_ia5689_reprise', {'repris': repris})
    _annoncer(desk, horaire, etat, now)
    return {'de': ancien, 'vers': etat, 'repris': repris, **compte}


def _reliquat(desk, horaire, now):
    """Sans transition : un travail arrêté par le planning alors qu'aucune transition n'a été vue (plage entière passée pendant un long travail,
    déclencheur différé) reprend dès que rien ne le retient. Lecture seule tant qu'il n'y a rien à reprendre."""
    if horaire['etat'] in ('pause', 'incident') or desk.db.execute(
            "SELECT 1 FROM jobs WHERE status='suspended' AND stop_reason=? LIMIT 1", (MOTIF,)).fetchone() is None:
        return None
    from .arret5643 import resume
    if resume(desk, now=now):
        desk.audit('horaires_ia5689_reprise', {'reliquat': True})
    return None


def _annoncer(desk, horaire, etat, now):
    """Une notification par transition : la clé de déduplication rend la répétition impossible."""
    from .live430 import emit
    jour = now.isoformat()[:16]
    genre = 'blocked' if horaire['incident'] else 'detected'
    try:
        emit(desk, genre, synthese(horaire, now) if etat != 'libre' else 'Fin de la pause programmée : l’IA reprend si aucune autre pause n’est en vigueur.',
             dedupe='horaires5689-%s-%s' % (etat, jour))
    except (sqlite3.Error, Stop):
        pass


def superviser(c, desk=None):
    """Appelé par le superviseur (toutes les 30 s, même quand tous les travailleurs sont occupés). Sans planning, rien n'est ouvert ni écrit."""
    if desk is None:
        if CLE_POLITIQUE not in photo(c['state_dir']):
            return None
        from .desk import Desk
        desk = Desk(c)
    surveiller(desk)
    purger(desk)
    return desk


def ensure_schema(desk):
    """Migration idempotente : durée suspendue par le planning. Elle ne change l'état d'aucun travail ancien."""
    colonnes = {r[1] for r in desk.db.execute('PRAGMA table_info(jobs)')}
    for nom, definition in (('pause_debut', 'TEXT'), ('pause_secondes', 'INTEGER NOT NULL DEFAULT 0')):
        if nom not in colonnes:
            try:
                desk.db.execute('ALTER TABLE jobs ADD COLUMN %s %s' % (nom, definition))
            except sqlite3.OperationalError as error:
                if 'duplicate column' not in str(error):           # deux travailleurs démarrent ensemble
                    raise
    desk.db.commit()


def differer(desk, kind, raw, priority, origin, parent, matter, transactional=False):
    """Déclencheur automatique reçu pendant une plage : enregistré une fois (``Desk.enqueue`` a déjà retrouvé un travail identique), suspendu, repris
    à la fin de la plage. Nombre borné : au-delà, le producteur repassera, comme pour une file pleine."""
    if desk.db.execute("SELECT COUNT(*) FROM jobs WHERE status='suspended' AND stop_reason=?", (MOTIF,)).fetchone()[0] >= SUSPENDUS_MAX:
        if not transactional:
            desk.db.rollback()
        raise Stop('file_attente_pleine')
    stamp = _utc().isoformat()
    identifiant = desk.db.execute('INSERT INTO jobs (kind,args,status,created,finished,result,priority,origin,parent_job_id,matter_ref,stop_reason,pause_debut) '
                                  'VALUES (?,?,?,?,NULL,?,?,?,?,?,?,?)',
                                  (kind, raw, 'suspended', desk.now(), json.dumps({'message': 'Différé : pause programmée de l’IA. Traité à la fin de la plage.'}, ensure_ascii=False),
                                   priority, origin, parent, matter, MOTIF, stamp)).lastrowid
    if not transactional:
        desk.db.commit()
    return identifiant


def apres_suspension(desk, job_id, ligne):
    """Fin d'un travail arrêté par le planning : une suspension prévue ne consomme pas de tentative ; si la plage a pris fin entre-temps, il repart."""
    from .arret5643 import pause_in_effect
    now = _utc()
    desk.db.execute("UPDATE jobs SET attempts=MAX(attempts-1,0),pause_debut=COALESCE(pause_debut,?) WHERE id=? AND status='suspended'", (now.isoformat(), job_id))
    if not pause_in_effect(desk, ligne, now):
        desk.db.execute("UPDATE jobs SET status='pending',stop_reason=NULL,result=NULL,pause_debut=NULL,not_before=? WHERE id=? AND status='suspended'",
                        ((now + timedelta(seconds=60)).isoformat(), job_id))
    return desk.db.execute('SELECT status,result FROM jobs WHERE id=?', (job_id,)).fetchone()


def motif_revalidation(args, now=None):
    """Échéance absolue dépassée pendant la pause : le travail ne repart pas seul, l'avocat revalide avant toute publication."""
    now = _utc(now)
    for cle in CLES_ECHEANCE:
        if (args or {}).get(cle):
            try:
                if _date(args[cle]) <= now:
                    return 'echeance_depassee'
            except (TypeError, ValueError):
                return 'echeance_illisible'
    return ''


# ---------------------------------------------------------------- réponses tardives
TARDIVES = ('CREATE TABLE IF NOT EXISTS reponses_tardives5689(id INTEGER PRIMARY KEY, job_id INTEGER NOT NULL, kind TEXT NOT NULL DEFAULT \'\', matter TEXT NOT NULL DEFAULT \'\', '
            'motif TEXT NOT NULL, created TEXT NOT NULL, empreinte TEXT NOT NULL, contenu TEXT NOT NULL, etat TEXT NOT NULL DEFAULT \'partielle\')')


def garder_reponse_tardive(job, motif, valeur, now=None):
    """Réponse reçue après le début d'une pause : conservée comme résultat partiel, jamais utilisée. Une écriture impossible n'empêche pas l'arrêt."""
    now = _utc(now)
    try:
        contenu = json.dumps(valeur, ensure_ascii=False, default=str)[:200000]
    except (TypeError, ValueError):
        contenu = str(valeur)[:200000]
    try:
        db = sqlite3.connect(str(Path(job['state_dir']) / 'desk.sqlite3'), timeout=5)
        try:
            db.execute(TARDIVES)
            ligne = db.execute('SELECT kind FROM jobs WHERE id=?', (int(job['job_id']),)).fetchone()
            db.execute('INSERT INTO reponses_tardives5689(job_id,kind,matter,motif,created,empreinte,contenu) VALUES(?,?,?,?,?,?,?)',
                       (int(job['job_id']), ligne[0] if ligne else '', str(job.get('matter') or ''), str(motif), now.isoformat(),
                        hashlib.sha256(contenu.encode('utf-8')).hexdigest(), contenu))
            db.commit()
        finally:
            db.close()
    except (sqlite3.Error, ValueError, TypeError):
        pass


def apres_travail(desk, job_id, status):
    """Le travail repris a produit son résultat à partir des sources du moment : la réponse tardive n'a plus d'objet."""
    if status != 'done':
        return
    try:
        desk.db.execute("UPDATE reponses_tardives5689 SET etat='remplacee' WHERE job_id=? AND etat='partielle'", (job_id,))
    except sqlite3.OperationalError:
        return                                                     # table absente : aucune réponse tardive n'a jamais été gardée


def _nombre_tardives(desk):
    try:
        return desk.db.execute("SELECT COUNT(*) FROM reponses_tardives5689 WHERE etat='partielle'").fetchone()[0]
    except sqlite3.Error:
        return 0


def purger(desk, now=None):
    """Rétention : une fois par jour, les réponses tardives de plus de 30 jours sont retirées."""
    now = _utc(now)
    jour = now.date().isoformat()
    if desk.settings(CLE_PURGE, '') == jour:
        return 0
    desk.setting(CLE_PURGE, jour)
    try:
        retirees = desk.db.execute('DELETE FROM reponses_tardives5689 WHERE created<?', ((now - timedelta(days=TARDIVES_JOURS)).isoformat(),)).rowcount
        desk.db.commit()
    except sqlite3.OperationalError:
        return 0
    return retirees
