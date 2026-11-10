"""5.6.96 (lot discussion, R01 à R05) : périmètre d'une demande avant le dossier, recherche publique à la demande, client Tavily commun.

Constaté sur 5.6.95 : ``missions567.create`` résolvait le dossier avant l'intention ; une question publique (« Quelle est la météo du jour à
Valbrune ? ») produisait ``dossier_ambigu`` dès qu'un dossier portait le même mot, et « OK » lançait une mission. Le seul client Tavily
vivait dans la veille (``veille5632.tavily_search``) et acceptait ``fausse-sourceofficielle.fr`` pour ``sourceofficielle.fr``.

Ici :
- ``cadrer`` classe d'abord la demande : acquiescement, question publique, lecture du cabinet (courriels, agenda), mode « Général »,
  question ou mission sur un dossier. Le dossier n'est résolu que pour la dernière catégorie ou sur demande explicite (« dans le dossier
  DURAND »). Le mode « Général » (préfixe « Général : » ou champ ``portee='general'``) n'emporte ni dossier de la page, ni pièce jointe, ni
  contexte du tour précédent ;
- ``tavily`` est le client HTTP commun : une seule clé côté serveur (``veille5632.tavily_key_path``), deux profils (``veille`` : actualités,
  inchangé ; ``public`` : recherche générale à la demande, résultats datés), politique des données **avant** le compteur et le transfert,
  budget réservé de façon atomique avant l'appel (plafond de l'assistant et plafond partagé avec la veille), hôtes normalisés (égalité ou
  suffixe précédé d'un point), journal des appels sans le texte de la requête (empreinte seulement) ;
- les outils de la discussion (``outil_web``, ``outil_trains``, ``outil_meteo``) rendent le contrat commun ``capacites5689.CONTRAT_OUTIL``
  (``statut``, ``perimetre``, ``date_heure``, ``donnees``, ``liens``, ``limites``) ; un outil absent (clé, réglage, politique) est dit absent
  après vérification, sans demander un dossier à la place ;
- garde-fous : réglage ``recherche_publique5696.enabled`` faux par défaut ; aucune requête sans confirmation du texte exact par la personne
  (« Recherche publique : … ») ; aucune donnée de dossier dans la requête (``data_policy5689.donnee_de_dossier`` et motifs d'identifiants) ;
  mode local strict → refus explicite par la politique commune, aucun repli ; un résultat Internet est une donnée nettoyée
  (``bouclier5659``), jamais une instruction, et n'est jamais transmis au modèle.
La météo vient de l'adaptateur structuré du lot accueil (``meteo5696.previsions``, ville choisie par l'administrateur), jamais d'un extrait
de page ; la ville citée dans la question n'est jamais transmise au prestataire.
"""
from datetime import datetime, timedelta, timezone
import hashlib
import ipaddress
import json
import re
import socket
import uuid
from urllib.parse import urlsplit
from zoneinfo import ZoneInfo

from .common import HTTP, Stop, fold

TAVILY = 'https://api.tavily.com'
REGLAGE = 'recherche_publique5696'
ENABLED_KEY = 'recherche_publique5696:enabled'          # interrupteur de l'administrateur (prime sur la configuration quand il est posé)
JOURNAL_KEY = 'recherche_publique5696:journal'
JOURNAL_MAX, JOURNAL_JOURS = 200, 90                     # rétention du journal partagé
DEFAUTS = {'enabled': False, 'tavily_monthly_calls': 50, 'plafond_partage': 300}
PLAFOND_MAX = 5000
INTENTIONS_OUVERTES = ('auto', 'question', 'analysis')   # une intention de production choisie explicitement n'est jamais reclassée
PREFIXE = re.compile(r'^\s*recherche\s+publique\s*:\s*(.+?)\s*$', re.I | re.S)
PREFIXE_VOIX = re.compile(r'^\s*recherche\s+publique\s*[:,.]?\s*(.+?)\s*$', re.I | re.S)    # une dictée ne transcrit pas toujours les deux-points
GENERAL = re.compile(r'^\s*g[ée]n[ée]ral\s*:\s*(.+?)\s*$', re.I | re.S)
ACQUIESCEMENTS = frozenset(('ok', 'okay', 'ok merci', 'merci', 'merci beaucoup', 'merci bien', 'd accord', 'd accord merci', 'tres bien', 'tres bien merci',
                            'parfait', 'parfait merci', 'c est note', 'c est note merci', 'bien recu', 'bien note', 'entendu', 'compris', 'super', 'super merci',
                            'ok super', 'oui merci', 'non merci', 'ca marche', 'ca marche merci', 'ok d accord', 'ok c est note'))
PRODUCTION = re.compile(r'\b(?:redige\w*|ecri[st]\w*|ecrivez|envoie\w*|envoyer|envoyez|publie\w*|depose\w*|prepare\w* (?:un|une|le|la|les) '
                        r'(?:courriel|mail|courrier|note|lettre|projet|acte|conclusions))\b')
EXPLICITE = re.compile(r'\b(?:dossiers?|affaires?)\b')
JURIDIQUE_METEO = re.compile(r'\b(?:clients?|clientes?|conclusions?|contrats?|force majeure|intemperies?|sinistres?|jurisprudences?|assignations?|expertises?|'
                             r'pieces?|litiges?|preuves?|constats?)\b')
METEO = re.compile(r'\bmeteo\b|\bquel temps (?:fait|fera|va|fait il|fera t il)\b|\btemperatures? (?:exterieures?|du jour|de demain|prevues?)\b|\bprevisions? meteo')
TRAINS_MOTS = re.compile(r'\b(?:trains?|sncf|ter|tgv|ouigo|intercites|inoui|rer|transilien)\b')
TRAINS_ETAT = re.compile(r'\b(?:greves?|perturbations?|trafic|circule\w*|circulation|retards?|annule\w*|supprime\w*|maintenu\w*)\b')
TRAIN_NUMERO = re.compile(r'\b(?:tgv|ter|train|intercites|ouigo|inoui)\s*(?:n(?:o|umero)?\s*)?(\d{3,6})\b')
WEB = re.compile(r'\b(?:cherche\w*|recherche\w*|regarde\w*|verifie\w*|renseigne\w*|trouve\w*|consulte\w*)\b.{0,60}?\b(?:sur internet|sur le web|en ligne|sur tavily|sur la toile)\b'
                 r'|\b(?:sur internet|sur le web|en ligne)\b.{0,40}?\b(?:cherche\w*|recherche\w*|regarde\w*|verifie\w*|trouve\w*)\b')
COMMANDE_WEB = re.compile(r'^(?:(?:peux|pourrais|veux|voudrais)[- ]tu |est[- ]ce que tu (?:peux|pourrais) |pouvez[- ]vous |merci de |s il te plait )?(?:me |nous )?'
                          r'(?:cherche[rz]?|recherche[rz]?|regarde[rz]?|verifie[rz]?|renseigne[rz]?(?:[- ](?:moi|nous))?|trouve[rz]?|consulte[rz]?)\s+'
                          r'(?:sur internet|sur le web|en ligne|sur tavily|sur la toile)\s*'
                          r'(?:si |ce que |ce qu |quels? |quelles? |les |la |le |l |des |du |de la |de l |un |une )?', re.S)
# Identifiants qui trahissent une donnée du cabinet même inconnue du registre : adresse électronique, lien, longue suite de chiffres
# (téléphone, SIREN, numéro de pièce), référence de dossier ou de rôle.
IDENTIFIANTS = re.compile(r'@|https?://|www\.|(?:\d[\s()./-]*){8}|\b(?:DOS-\d|RG\s*n?[°o]?\s*\d|\d{2}/\d{4,5}\b|client\s*:|dossier\s*:)', re.I)
OPERATEURS_TRAINS = ('sncf-connect.com', 'sncf-voyageurs.com', 'sncf.com', 'ouigo.com', 'garesetconnexions.sncf', 'transilien.com', 'ter.sncf.com')
HOTES_INTERDITS = ('localhost', 'localhost.localdomain')
SUFFIXES_INTERDITS = ('.local', '.localhost', '.internal', '.lan', '.home', '.arpa', '.intranet', '.corp')
LIENS_METEO = {'openweathermap': ('https://openweathermap.org/', 'OpenWeather'), 'open_meteo': ('https://open-meteo.com/', 'Open-Meteo')}
MOTIFS = {'recherche_publique_desactivee': 'Les recherches publiques dans l’assistant ne sont pas autorisées par le cabinet (Paramètres › Recherche juridique et veille, administrateur).',
          'donnee_de_dossier': 'La demande contient une donnée du cabinet (nom, référence, adresse, numéro) : reformulez avec des termes publics seulement. Rien n’a été envoyé.',
          'pieces_jointes': 'Une recherche publique ne part jamais avec des pièces jointes : retirez-les ou posez la question sans pièce. Rien n’a été envoyé.',
          'mode_local_strict': 'Mode local strict : aucune recherche publique ne part vers un tiers, et aucun repli n’est tenté.',
          'nom_de_partie_dans_la_requete': 'La requête contient une donnée de dossier : elle ne part pas.',
          'prestataire_non_admis': 'Tavily n’est pas admis au registre des prestataires du cabinet.',
          'politique_illisible': 'La politique des données est illisible : aucun transfert.',
          'region_inconnue': 'Le lieu de traitement de Tavily n’est pas renseigné alors que la politique l’exige.',
          'region_non_admise': 'Le lieu de traitement de Tavily n’est pas admis par la politique.',
          'categorie_fermee': 'Cette catégorie de données est fermée à l’IA pour ce compte.',
          'cle_tavily_absente': 'Aucune clé Tavily n’est configurée (Paramètres › Recherche juridique et veille).',
          'cle_tavily_refusee': 'La clé Tavily a été refusée par le service (401 ou 403) : à vérifier dans les paramètres.',
          'plafond_tavily_atteint': 'Le plafond mensuel des appels Tavily (partagé avec la veille) est atteint.',
          'plafond_recherche_publique_atteint': 'Le plafond mensuel des recherches publiques de l’assistant est atteint.',
          'quota_tavily_atteint': 'Le quota du compte Tavily est atteint pour ce mois.',
          'tavily_reponse_incertaine': 'Tavily n’a pas répondu à temps : résultat incertain, compté dans le budget, aucune relance automatique.',
          'tavily_reponse_illisible': 'Réponse de Tavily illisible.',
          'tavily_indisponible': 'Tavily est indisponible pour le moment.',
          'plafond_tavily_invalide': 'Le plafond de la veille est illisible.'}
REFUS = frozenset(('mode_local_strict', 'nom_de_partie_dans_la_requete', 'prestataire_non_admis', 'politique_illisible', 'region_inconnue', 'region_non_admise',
                   'dossier_exclu', 'donnees_dossier_interdites', 'categorie_fermee', 'dossier_reserve', 'outil_inconnu'))


def _now():
    return datetime.now(timezone.utc)


def norm(text):
    return re.sub(r'[^a-z0-9]+', ' ', fold(str(text or ''))).strip()


def _heure(desk, owner='cabinet', instant=None):
    """Heure de vérification affichée dans le fuseau de la personne."""
    from .acces5683 import today_of
    try:
        zone = ZoneInfo(today_of(desk, owner)[1])
    except Exception:   # noqa: BLE001 — fuseau illisible : UTC
        zone = timezone.utc
    return (instant or _now()).astimezone(zone).strftime('%d/%m/%Y à %H h %M')


# ------------------------------------------------------------------------------------------------ périmètre (R01)
def acquiescement(text):
    """« OK », « merci », « d'accord » : une réponse dans le fil, sans lecture ni mission."""
    return norm(text) in ACQUIESCEMENTS


def reponse_acquiescement(text):
    return 'Je vous en prie.' if 'merci' in norm(text).split() else 'D’accord.'


def intention_publique(text):
    """'meteo', 'trains', 'web' ou '' : une demande de production (rédiger, envoyer, déposer) ou qui vise explicitement un dossier n'est jamais
    publique ; une question météo dans un contexte juridique (force majeure, sinistre, conclusions…) relève du dossier."""
    q = norm(text)
    if not q or PRODUCTION.search(q) or EXPLICITE.search(q):
        return ''
    if METEO.search(q) and not JURIDIQUE_METEO.search(q):
        return 'meteo'
    if TRAINS_MOTS.search(q) and (TRAINS_ETAT.search(q) or TRAIN_NUMERO.search(q)):
        return 'trains'
    if WEB.search(q):
        return 'web'
    return ''


def lecture_cabinet(text):
    """Lecture des courriels ou de l'agenda du cabinet (``acces5683``), sans rédaction ni pièce demandée."""
    from . import acces5683 as a
    if not (a.wants_mail(text) or a.wants_agenda(text)):
        return False
    return not (a.DRAFTING.search(norm(text)) or a.wants_documents(text))


def requete_web(text):
    """Mots publics d'une demande « cherche sur Internet … » : la commande est retirée, le reste est proposé tel quel à la confirmation."""
    q = ' '.join(str(text or '').split())
    plat = norm(q)
    m = COMMANDE_WEB.match(plat)
    if m and m.end() < len(plat):
        # mots d'origine (accents et traits d'union gardés) alignés sur les mots repliés de la commande retirée
        mots = re.findall(r"\w+(?:[-.]\w+)*", q)
        besoin, compte, i = len(plat[:m.end()].split()), 0, 0
        while i < len(mots) and compte < besoin:
            compte += len(norm(mots[i]).split())
            i += 1
        reste = ' '.join(mots[i:]) if compte == besoin else plat[m.end():]
        return reste.strip(' ?.!')[:400]
    return q.strip(' ?.!')[:400]


def cadrer(text, data=None):
    """Périmètre d'une demande, avant toute résolution de dossier.

    Retour : ``perimetre`` (acquiescement, public, cabinet, general, dossier) ; ``outil`` (meteo, trains, web ou '') ; ``texte`` (sans
    préfixe de mode) ; ``general`` ; ``requete`` (texte public proposé ou confirmé) ; ``confirmee`` (préfixe « Recherche publique : » ou champ
    ``confirmer_recherche`` égal au texte exact proposé) ; ``resoudre_dossier`` (le dossier n'est cherché que pour une demande sur un dossier,
    ou sur demande explicite) ; ``pieces`` (pièces jointes téléversées pour cette demande)."""
    data = data if isinstance(data, dict) else {}
    texte = str(text or '').strip()
    general = str(data.get('portee') or '') == 'general'
    m = GENERAL.match(texte)
    if m:
        general, texte = True, m.group(1).strip()
    pieces = bool(data.get('attachments'))
    explicite = bool(EXPLICITE.search(norm(texte)))
    out = {'perimetre': 'dossier', 'outil': '', 'texte': texte, 'general': general, 'requete': '', 'confirmee': False,
           'resoudre_dossier': not general or explicite, 'pieces': pieces}
    if acquiescement(texte):
        return {**out, 'perimetre': 'acquiescement', 'resoudre_dossier': False}
    m = (PREFIXE_VOIX if data.get('channel') == 'voice' else PREFIXE).match(texte)
    if m:
        return {**out, 'perimetre': 'public', 'outil': 'web', 'requete': ' '.join(m.group(1).split())[:400], 'confirmee': True, 'resoudre_dossier': False}
    outil = intention_publique(texte)
    if outil:
        requete = requete_web(texte) if outil == 'web' else ''
        confirmee = bool(requete) and str(data.get('confirmer_recherche') or '').strip() == requete
        return {**out, 'perimetre': 'public', 'outil': outil, 'requete': requete, 'confirmee': confirmee, 'resoudre_dossier': False}
    if not pieces and not explicite and lecture_cabinet(texte):
        return {**out, 'perimetre': 'cabinet', 'resoudre_dossier': False}
    if general:
        return {**out, 'perimetre': 'general'}
    return out


def cadre_dossier(text):
    """Cadre d'une demande dont l'intention de production a été choisie explicitement : protection du dossier inchangée."""
    return {'perimetre': 'dossier', 'outil': '', 'texte': str(text or '').strip(), 'general': False, 'requete': '', 'confirmee': False, 'resoudre_dossier': True, 'pieces': False}


def restreindre(data, cadre):
    """Données de la demande réduites au périmètre : le mode Général n'emporte ni dossier, ni pièce, ni contexte (le préfixe « Général : »
    reste dans l'instruction pour que la réponse ignore aussi l'historique) ; une question publique n'emporte ni le dossier de la page, ni le
    courriel ouvert, ni les documents cochés (les pièces jointes téléversées restent, pour être refusées explicitement)."""
    raw = data.get('context') if isinstance(data.get('context'), dict) else {}
    if cadre['general'] and cadre['perimetre'] != 'public':
        return {**data, 'instruction': 'Général : ' + cadre['texte'], 'matter': '', 'attachments': [], 'context': {'page': '/'}}
    if cadre['perimetre'] == 'public':
        # une recherche confirmée (préfixe dicté sans deux-points, ou confirmation en un clic) est réécrite sous sa forme canonique, pour que
        # le tour de la file la reconnaisse à l'identique
        texte = 'Recherche publique : ' + cadre['requete'] if cadre['outil'] == 'web' and cadre['confirmee'] else cadre['texte']
        return {**data, 'instruction': texte, 'matter': '', 'context': {'page': str(raw.get('page') or '/')}}
    return data


# ------------------------------------------------------------------------------------------------ réglage, budget, journal (R02)
def reglage(desk):
    """Réglage ``recherche_publique5696`` de la configuration (``enabled`` faux par défaut) ; l'interrupteur de l'administrateur
    (``desk.settings``) l'emporte quand il a été posé. Les plafonds sont bornés à [0, 5000]."""
    cfg = desk.c.get(REGLAGE) if isinstance(desk.c.get(REGLAGE), dict) else {}
    out = dict(DEFAUTS)
    out['enabled'] = cfg.get('enabled') is True
    interrupteur = desk.settings(ENABLED_KEY, None)
    if isinstance(interrupteur, bool):
        out['enabled'] = interrupteur
    for cle in ('tavily_monthly_calls', 'plafond_partage'):
        try:
            out[cle] = max(0, min(PLAFOND_MAX, int(cfg.get(cle, DEFAUTS[cle]))))
        except (TypeError, ValueError):
            out[cle] = DEFAUTS[cle]
    return out


def autoriser(desk, actif, acteur=''):
    """Interrupteur de l'administrateur ; réversible, journalisé sans secret ni requête."""
    desk.setting(ENABLED_KEY, bool(actif))
    desk.audit('recherche_publique5696_autorisation', {'enabled': bool(actif), 'owner': str(acteur or '')})
    return reglage(desk)


def _plafonds(desk, profil, p=None):
    reg = reglage(desk)
    try:
        veille = max(0, min(PLAFOND_MAX, int((p or {}).get('tavily_monthly_calls', 300))))
    except (TypeError, ValueError, AttributeError):
        raise Stop('plafond_tavily_invalide') from None
    return {'veille': veille, 'public': reg['tavily_monthly_calls'], 'partage': reg['plafond_partage'], 'profil': profil}


def usage(desk):
    """Compteur mensuel commun (clé de ``veille5632``) : ``calls`` (tous les départs), ``public`` (assistant), ``uncertain`` (coupures après envoi)."""
    from .veille5632 import _usage_key
    return {'calls': 0, 'uncertain': 0, 'public': 0, **(desk.settings(_usage_key(), {}) or {})}


def reserver(desk, plafonds, incertain=False):
    """Réservation atomique AVANT l'appel : une transaction immédiate relit le compteur, refuse au plafond, puis incrémente. Une coupure
    après envoi est comptée comme incertaine (consommée chez le prestataire sans réponse exploitable), jamais relancée en boucle."""
    from .veille5632 import _usage_key
    key = _usage_key()
    if desk.db.in_transaction:
        desk.db.commit()
    desk.db.execute('BEGIN IMMEDIATE')
    try:
        row = desk.db.execute('SELECT value FROM settings WHERE key=?', (key,)).fetchone()
        used = {'calls': 0, 'uncertain': 0, 'public': 0, **(json.loads(row[0]) if row else {})}
        if incertain:
            used['uncertain'] += 1
        else:
            if used['calls'] >= plafonds['partage']:
                raise Stop('plafond_tavily_atteint')
            if plafonds['profil'] == 'public' and used['public'] >= plafonds['public']:
                raise Stop('plafond_recherche_publique_atteint')
            if plafonds['profil'] == 'veille' and used['calls'] >= plafonds['veille']:
                raise Stop('plafond_tavily_atteint')
            used['calls'] += 1
            if plafonds['profil'] == 'public':
                used['public'] += 1
        desk.db.execute('INSERT OR REPLACE INTO settings VALUES (?,?)', (key, json.dumps(used)))
        desk.db.commit()
    except BaseException:
        desk.db.rollback()
        raise
    return used


def quota(desk):
    reg, used = reglage(desk), usage(desk)
    return {'public': used['public'], 'plafond_public': reg['tavily_monthly_calls'], 'total': used['calls'], 'plafond_partage': reg['plafond_partage'],
            'incertains': used['uncertain'], 'enabled': reg['enabled']}


def empreinte(texte):
    return hashlib.sha256(' '.join(str(texte or '').split()).encode()).hexdigest()[:16]


def journaliser(desk, profil, requete, statut, resultats=0, acteur=''):
    """Journal partagé des appels : date, profil, empreinte de la requête, nombre de résultats, statut. Jamais le texte de la requête."""
    cutoff = (_now() - timedelta(days=JOURNAL_JOURS)).isoformat()
    entrees = [x for x in (desk.settings(JOURNAL_KEY, []) or []) if isinstance(x, dict) and str(x.get('at') or '') >= cutoff][-(JOURNAL_MAX - 1):]
    entrees.append({'id': uuid.uuid4().hex, 'at': _now().isoformat(timespec='seconds'), 'profil': profil, 'empreinte': empreinte(requete), 'resultats': int(resultats or 0),
                    'statut': statut, 'acteur_h': hashlib.sha256(str(acteur).encode()).hexdigest()[:12] if acteur else ''})
    desk.setting(JOURNAL_KEY, entrees)


def journal(desk, limit=50):
    entrees = [x for x in (desk.settings(JOURNAL_KEY, []) or []) if isinstance(x, dict)]
    return list(reversed(entrees))[:max(1, int(limit))]


# ------------------------------------------------------------------------------------------------ garde-fous (R04)
def politique(desk, requete, finalite, acteur=''):
    """Décision commune (``data_policy5689``) avant le compteur et le transfert. La recherche publique de l'assistant est refusée en mode
    local strict par la politique elle-même (finalité ``recherche_publique``) : refus explicite, aucun repli."""
    from . import data_policy5689 as dp
    return dp.exiger(desk.c, 'recherche_web', 'tavily', finalite=finalite, requete=str(requete)[:400], acteur=acteur)


def donnee_de_dossier(desk, requete):
    """Vrai si la requête porte une donnée du cabinet : nom ou référence connus (``data_policy5689``), adresse, lien, longue suite de chiffres,
    référence de dossier ou de rôle."""
    from .data_policy5689 import donnee_de_dossier as connue
    texte = str(requete or '')
    return bool(IDENTIFIANTS.search(texte)) or connue(desk.c, texte)


# ------------------------------------------------------------------------------------------------ hôtes et adresses (R05)
def normaliser_domaines(domaines):
    out = []
    for d in domaines or ():
        d = fold(str(d or '')).strip()
        d = re.sub(r'^https?://', '', d).split('/')[0].rstrip('.')
        if d.startswith('*.'):
            d = d[2:]
        if d and d not in out:
            out.append(d)
    return tuple(out)


def hote(url):
    try:
        return (urlsplit(str(url or '')).hostname or '').lower().rstrip('.')
    except ValueError:
        return ''


def hote_admis(url, domaines=()):
    """HTTPS seulement, hôte en minuscules sans point final, ni adresse IP ni hôte local ; avec une liste de domaines : égalité ou suffixe
    précédé d'un point (``www.sourceofficielle.fr`` admis pour ``sourceofficielle.fr``, ``fausse-sourceofficielle.fr`` rejeté)."""
    try:
        p = urlsplit(str(url or ''))
        port = p.port
    except ValueError:
        return False
    host = (p.hostname or '').lower().rstrip('.')
    if p.scheme != 'https' or not host or p.username or p.password or port not in (None, 443):
        return False
    if not re.fullmatch(r'[a-z0-9][a-z0-9.-]{0,252}', host) or '.' not in host or not host.rsplit('.', 1)[-1].isalpha():
        return False     # adresses IP, hôtes sans domaine
    if host in HOTES_INTERDITS or host.endswith(SUFFIXES_INTERDITS):
        return False
    doms = normaliser_domaines(domaines)
    return not doms or any(host == d or host.endswith('.' + d) for d in doms)


def _resoudre(host):
    try:
        return sorted({x[4][0] for x in socket.getaddrinfo(host, 443, proto=socket.IPPROTO_TCP)})
    except (OSError, UnicodeError):
        return []


def adresse_publique(ip):
    try:
        a = ipaddress.ip_address(str(ip).split('%')[0])
    except ValueError:
        return False
    return a.is_global and not a.is_multicast


def lire_page(url, domaines=(), http=None, resoudre=None, limite=1_000_000, delai=10):
    """Accès borné à une adresse retournée : périmètre, résolution contrôlée (aucune adresse privée ni locale), aucune redirection suivie
    (``common.HTTP`` ne suit pas les 3xx), temps et taille bornés. Le contenu lu reste une donnée, jamais une instruction."""
    if not hote_admis(url, domaines):
        raise Stop('adresse_hors_perimetre')
    p = urlsplit(url)
    adresses = (resoudre or _resoudre)(p.hostname)
    if not adresses:
        raise Stop('adresse_non_resolue')
    if not all(adresse_publique(a) for a in adresses):
        raise Stop('adresse_privee_refusee')
    client = http or HTTP('https://' + p.netloc, timeout=delai)
    try:
        return client.request('GET', url, limit=limite)
    except Stop as ex:
        if str(ex).startswith('http_3'):
            raise Stop('redirection_refusee') from None
        raise


# ------------------------------------------------------------------------------------------------ client Tavily commun (R02, R05)
def tavily(desk, requete, *, profil='veille', max_results=5, days=7, domaines=(), http=None, p=None, acteur=''):
    """Recherche Tavily. Ordre : clé, politique des données, réservation atomique du budget, appel borné, filtrage des hôtes, journal.
    ``profil='veille'`` : actualités datées (``topic=news``, fenêtre glissante) ; ``profil='public'`` : recherche générale à la demande.
    Réponse générée du moteur désactivée : seules les pages servent de preuve. 401/403/429/432 traduits ; une coupure après envoi est
    comptée comme consommation incertaine, sans relance en boucle."""
    from .veille5632 import _clean, tavily_key_path
    from .common import read_secret
    requete = ' '.join(str(requete or '').split())[:400]
    if profil not in ('veille', 'public'):
        raise Stop('profil_recherche_invalide')
    path = tavily_key_path(desk)
    if not path:
        raise Stop('cle_tavily_absente')
    politique(desk, requete, 'veille' if profil == 'veille' else 'recherche_publique', acteur)   # 5.6.89 : avant le compteur et le transfert
    plafonds = _plafonds(desk, profil, p)
    reserver(desk, plafonds)                                                                       # réservation avant l'effet
    body = {'query': requete, 'topic': 'news' if profil == 'veille' else 'general', 'search_depth': 'basic', 'max_results': max(1, min(10, int(max_results))),
            'include_answer': False, 'include_raw_content': False, 'include_images': False}
    if profil == 'veille':   # 5.6.61 : articles datés plutôt que formations, livres et guides permanents
        body['time_range'] = 'week' if days <= 7 else 'month'
    doms = normaliser_domaines(domaines)
    if doms:
        body['include_domains'] = list(doms)[:20]
    client = http or HTTP(TAVILY, timeout=30 if profil == 'veille' else 12)
    try:
        raw = client.request('POST', TAVILY + '/search', json.dumps(body).encode(), {'Content-Type': 'application/json', 'Authorization': 'Bearer ' + read_secret(path)}, 2_000_000)
    except Stop as ex:
        code = str(ex)
        if code in ('delai_http_depasse', 'connexion_http_indisponible'):
            reserver(desk, plafonds, incertain=True)
            journaliser(desk, profil, requete, 'incertain', 0, acteur)
            raise Stop('tavily_reponse_incertaine') from None
        journaliser(desk, profil, requete, 'erreur', 0, acteur)
        raise Stop({'http_401': 'cle_tavily_refusee', 'http_403': 'cle_tavily_refusee', 'http_429': 'quota_tavily_atteint', 'http_432': 'quota_tavily_atteint',
                    'http_433': 'quota_tavily_atteint'}.get(code, 'tavily_indisponible')) from None
    try:
        data = json.loads(raw)
    except ValueError:
        data = None
    rows = data.get('results') if isinstance(data, dict) else None
    if not isinstance(rows, list):
        journaliser(desk, profil, requete, 'erreur', 0, acteur)
        raise Stop('tavily_reponse_illisible')
    out = []
    for r in rows[:body['max_results'] * 3]:
        if not isinstance(r, dict):
            continue
        url = str(r.get('url') or '')
        if len(url) > 2000 or not hote_admis(url, doms):
            continue   # restriction de domaines et d'hôtes appliquée aussi côté AxiorHub
        out.append({'url': url, 'title': _clean(r.get('title'), 600), 'published': str(r.get('published_date') or ''), 'summary': _clean(r.get('content'), 1500), 'score': r.get('score')})
    out = out[:body['max_results']]
    journaliser(desk, profil, requete, 'ok' if out else 'vide', len(out), acteur)
    return out


# ------------------------------------------------------------------------------------------------ contrat d'outil (R03)
def resultat(statut, perimetre, donnees=None, liens=(), limites=(), date_heure='', **extra):
    """Retour commun des outils de l'assistant (``capacites5689.CONTRAT_OUTIL``)."""
    return {'statut': statut, 'perimetre': perimetre, 'date_heure': date_heure or _now().isoformat(timespec='seconds'), 'donnees': donnees if donnees is not None else {},
            'liens': list(liens), 'limites': [str(x) for x in limites if x], **extra}


def _previsions(desk, owner):
    """Adaptateur structuré du lot accueil (``meteo5696.previsions``), importé à l'appel ; absent → ``ImportError``. La ville est celle
    choisie par l'administrateur (coordonnées) : aucun mot de la question n'est transmis au prestataire."""
    from . import meteo5696
    return meteo5696.previsions(desk, owner)


def ville_demandee(text):
    q = norm(text)
    m = re.search(r'\b(?:meteo|temps|temperatures?|previsions?)\b.*?\b(?:a|pour|sur|dans)\s+([a-z][a-z -]{1,40})', q)
    if not m:
        return ''
    return re.split(r'\b(?:aujourd hui|demain|apres demain|ce matin|ce soir|cette semaine|merci|s il|stp|svp|pour|et|le|la|les|du|de)\b', m.group(1), maxsplit=1)[0].strip()


def outil_meteo(desk, owner='cabinet', question=''):
    """Météo par l'adaptateur structuré, jamais par un extrait de page. Statuts : ok, perime, indisponible, desactive, refuse."""
    demandee = ville_demandee(question)
    try:
        p = _previsions(desk, owner)
    except ImportError:
        return resultat('indisponible', 'meteo', limites=['Le service météo n’est pas installé dans cette version.'], motif='meteo_absente')
    except Stop as ex:
        code = str(ex)
        return resultat('refuse' if code in REFUS else 'indisponible', 'meteo', limites=[MOTIFS.get(code, 'Météo indisponible.')], motif=code)
    if not isinstance(p, dict):
        return resultat('indisponible', 'meteo', limites=['Réponse du service météo illisible.'], motif='meteo_illisible')
    statut = str(p.get('statut') or 'indisponible')
    if statut not in ('ok', 'perime', 'indisponible', 'desactive', 'refuse'):
        statut = 'indisponible'
    jours = [j for j in (p.get('jours') or []) if isinstance(j, dict)][:4] if statut in ('ok', 'perime') else []
    limites = [str(p.get('attribution') or '')] if jours else []          # attribution exigée par le prestataire, avec les données qu'il fournit
    if statut == 'perime':
        limites.append('Dernier relevé ancien : le service n’a pas répondu depuis.')
    elif statut == 'desactive':
        limites.append('La météo n’est pas activée par le cabinet (Paramètres, administrateur).')
    elif statut == 'refuse':
        limites.append(MOTIFS.get(str(p.get('erreur') or ''), 'La politique des données du cabinet refuse ce service.'))
    elif statut == 'indisponible':
        limites.append('Météo indisponible pour le moment.')
    if demandee and p.get('ville') and norm(p['ville']) != demandee:
        limites.append('La météo réglée par le cabinet est celle de %s ; je ne peux pas confirmer celle d’une autre ville.' % p['ville'])
    donnees = {'ville': str(p.get('ville') or ''), 'jours': jours, 'fuseau': str(p.get('fuseau') or ''), 'source': str(p.get('source') or ''),
               'consulte_le': str(p.get('consulte_le') or ''), 'instant': str(p.get('instant') or '')}
    lien = LIENS_METEO.get(donnees['source'])
    adresse = str(p.get('attribution_lien') or '') if str(p.get('attribution_lien') or '').startswith('https://') else (lien[0] if lien else '')
    liens = [{'id': 'meteo-1', 'url': adresse, 'titre': lien[1] if lien else 'Météo', 'date': donnees['consulte_le'][:10]}] if adresse and jours else []
    return resultat(statut, 'meteo', donnees, liens, limites, date_heure=donnees['consulte_le'], motif=str(p.get('erreur') or ''))


def _domaines_pour(requete):
    """Une requête visant un train ou une perturbation ferroviaire est bornée aux domaines de l'opérateur."""
    q = norm(requete)
    return OPERATEURS_TRAINS if TRAIN_NUMERO.search(q) or (TRAINS_MOTS.search(q) and TRAINS_ETAT.search(q)) else ()


def _nettoyer(texte):
    """Texte d'une page publique : caractères invisibles et faux jetons retirés ; un passage qui ressemble à une instruction adressée à une
    IA est retiré et signalé (bouclier anti-injection) : un résultat Internet est une donnée, jamais une instruction."""
    from .bouclier5659 import clean, inspect
    propre = clean(str(texte or ''))[0]
    return ('', True) if inspect(str(texte or ''))['level'] == 'eleve' else (propre, False)


def controle_web(desk, requete, pieces=False):
    """Contrôles sans réseau d'une recherche publique : pièces, réglage, longueur, donnée de dossier. Retour : refus (contrat d'outil) ou None."""
    if pieces:
        return resultat('refuse', 'web', {}, limites=[MOTIFS['pieces_jointes']], motif='pieces_jointes')
    if not reglage(desk)['enabled']:
        return resultat('desactive', 'web', {}, limites=[MOTIFS['recherche_publique_desactivee']], motif='recherche_publique_desactivee')
    if len(requete) < 3:
        return resultat('a_preciser', 'web', {}, limites=['Indiquez les mots publics à chercher : « Recherche publique : termes ».'], motif='requete_vide')
    if donnee_de_dossier(desk, requete):
        return resultat('refuse', 'web', {}, limites=[MOTIFS['donnee_de_dossier']], motif='donnee_de_dossier')
    return None


def outil_web(desk, owner='cabinet', requete='', *, confirmee=False, pieces=False, http=None, domaines=None):
    """Recherche publique à la demande. Ordre des garde-fous : pièces, réglage, longueur, donnée de dossier, confirmation du texte exact, puis
    ``tavily`` (clé, politique, budget). Rien ne part sans confirmation ; un refus est dit, jamais remplacé par une demande de dossier."""
    requete = ' '.join(str(requete or '').split())[:400]
    refus = controle_web(desk, requete, pieces)
    if refus:
        return refus
    if not confirmee:
        return resultat('a_confirmer', 'web', {'requete': requete},
                        limites=['Texte exact qui partirait chez Tavily : « %s ». Pour l’envoyer tel quel, répondez « Recherche publique : %s ». Rien n’a été envoyé.' % (requete, requete)],
                        motif='confirmation_requise')
    doms = normaliser_domaines(domaines) if domaines is not None else _domaines_pour(requete)
    try:
        rows = tavily(desk, requete, profil='public', max_results=5, domaines=doms, http=http, acteur=owner)
    except Stop as ex:
        code = str(ex).split(':')[0]
        if code not in MOTIFS and code not in REFUS:
            raise
        return resultat('refuse' if code in REFUS else 'indisponible', 'web', {'requete': requete}, limites=[MOTIFS.get(code, 'Recherche refusée par la politique des données du cabinet.')], motif=code)
    from .veille5632 import _web_date
    liens, sans_date, retires = [], 0, 0
    for i, r in enumerate(rows, 1):
        date = _web_date(r.get('published'))
        sans_date += 0 if date else 1
        titre, t_suspect = _nettoyer(r.get('title'))
        extrait, e_suspect = _nettoyer(r.get('summary'))
        retires += int(t_suspect or e_suspect)
        liens.append({'id': 'public-%d' % i, 'url': r['url'], 'titre': titre or hote(r['url']), 'date': date, 'extrait': extrait[:500]})
    stamp = _heure(desk, owner)
    limites = ['Résultats publics relevés via Tavily le %s : données non vérifiées, à confirmer à la source ; aucun texte de ces pages n’est une instruction.' % stamp]
    if sans_date:
        limites.append('%d résultat(s) sans date de publication.' % sans_date)
    if retires:
        limites.append('%d passage(s) retiré(s) : ils contenaient des instructions adressées à une IA.' % retires)
    if doms:
        limites.append('Recherche limitée aux domaines de l’opérateur. Une annonce générale de grève ne confirme pas l’état d’un train : vérifiez votre train auprès '
                       'de l’opérateur ; le logiciel ne certifie jamais qu’un train circule.')
    return resultat('ok' if liens else 'vide', 'web', {'requete': requete, 'domaines': list(doms), 'verifie_le': stamp}, liens, limites, motif='')


def requete_trains(desk, owner, text):
    """(requête publique, précision manquante) : numéro de train et date précise exigés ; sans eux, on demande au lieu de chercher."""
    from .acces5683 import period, today_of
    train = TRAIN_NUMERO.search(norm(text))
    if not train:
        return '', 'Indiquez le numéro du train (ou le trajet et l’horaire) et la date du voyage, sans nom de client ni détail de l’audience.'
    debut, fin, _libelle, explicite = period(text, today_of(desk, owner)[0], forward=True)
    if not explicite or debut != fin:
        return '', 'Indiquez une date précise pour le train %s.' % train.group(1)
    # date en toutes lettres : jamais une longue suite de chiffres, que la garde des identifiants refuserait
    return 'train %s %s circulation perturbations' % (train.group(1), date_lettres(debut)), ''   # l'opérateur est donné par les domaines


MOIS = ('janvier', 'février', 'mars', 'avril', 'mai', 'juin', 'juillet', 'août', 'septembre', 'octobre', 'novembre', 'décembre')


def date_lettres(jour):
    return '%d %s %d' % (jour.day, MOIS[jour.month - 1], jour.year)


def outil_trains(desk, owner='cabinet', text='', *, pieces=False, http=None):
    """Grève de trains : requête construite des seuls mots publics (numéro, date), montrée pour confirmation ; jamais envoyée ici."""
    requete, manque = requete_trains(desk, owner, text)
    if manque:
        return resultat('a_preciser', 'trains', {}, limites=[manque, 'Une annonce générale de grève ne confirme pas l’état d’un train : seul l’opérateur le confirme.'],
                        motif='train_a_preciser')
    r = outil_web(desk, owner, requete, confirmee=False, pieces=pieces, http=http, domaines=OPERATEURS_TRAINS)
    return {**r, 'perimetre': 'trains'}


# ------------------------------------------------------------------------------------------------ réponses dans le fil
def _texte_meteo(r):
    d = r['donnees']
    if not d.get('jours'):
        return 'Météo' + (' à ' + d['ville'] if d.get('ville') else '') + ' : ' + ' '.join(r['limites'])
    lignes = []
    for i, j in enumerate(d['jours'][:4]):
        quand = ('aujourd’hui', 'demain', 'après-demain')[i] if i < 3 else 'dans trois jours'
        try:
            date = datetime.fromisoformat(str(j.get('date'))).strftime('%d/%m')
        except ValueError:
            date = str(j.get('date') or '')
        lignes.append('%s (%s) : %s, de %s à %s °C' % (quand, date, j.get('libelle') or '—', j.get('min', '?'), j.get('max', '?')))
    tete = 'Météo à %s%s.' % (d.get('ville') or 'la ville du cabinet', ' (dernier relevé ancien)' if r['statut'] == 'perime' else '')
    return tete + '\n' + '\n'.join(lignes) + ('\n' + ' '.join(x for x in r['limites'] if x) if r['limites'] else '')


def _texte_web(r):
    d = r['donnees']
    if r['statut'] in ('a_confirmer', 'a_preciser'):
        return ' '.join(r['limites'])
    if r['statut'] in ('refuse', 'desactive', 'indisponible'):
        tete = {'refuse': 'Recherche publique refusée.', 'desactive': 'Recherche publique non disponible.', 'indisponible': 'Recherche publique impossible pour le moment.'}[r['statut']]
        return tete + ' ' + ' '.join(r['limites'])
    if r['statut'] == 'vide':
        return ('Recherche publique « %s » : aucun résultat dans les sources interrogées le %s. Cela ne prouve aucune absence.' % (d.get('requete', ''), d.get('verifie_le', '')) +
                (' ' + ' '.join(r['limites'][1:]) if len(r['limites']) > 1 else ''))
    lignes = ['%d. %s (%s) — %s' % (i, x['titre'], x['date'] or 'sans date', x['url']) + ('\n   ' + x['extrait'] if x['extrait'] else '') for i, x in enumerate(r['liens'], 1)]
    return 'Recherche publique « %s », vérifiée le %s :\n%s\n\n%s' % (d.get('requete', ''), d.get('verifie_le', ''), '\n'.join(lignes), ' '.join(r['limites']))


def executer(desk, cadre, owner='cabinet', *, pieces=False, http=None):
    """Outil du bon tour pour une question publique : météo (adaptateur), trains (précision ou proposition), web (proposition ou recherche confirmée)."""
    outil = cadre.get('outil') or 'web'
    if outil == 'meteo':
        r = outil_meteo(desk, owner, question=cadre.get('texte', ''))
        return r, _texte_meteo(r)
    if outil == 'trains':
        r = outil_trains(desk, owner, cadre.get('texte', ''), pieces=pieces, http=http)
    else:
        r = outil_web(desk, owner, cadre.get('requete') or '', confirmee=bool(cadre.get('confirmee')), pieces=pieces, http=http)
    return r, _texte_web(r)


def reponse(desk, cadre, owner='cabinet', *, pieces=False, http=None):
    """Réponse de la discussion pour une question publique (contrat ``CHAT`` + ``sources`` + ``outil``), sans modèle : le texte est écrit par
    le logiciel à partir du retour de l'outil ; aucune page publique n'est transmise au modèle."""
    r, texte = executer(desk, cadre, owner, pieces=pieces, http=http)
    sources = [{'id': x.get('id') or 'public-%d' % i, 'kind': 'source_publique', 'path': x.get('titre') or x['url'], 'url': x['url'], 'modified': x.get('date') or '',
                'excerpt': x.get('extrait') or '', 'matter': ''} for i, x in enumerate(r['liens'], 1) if x.get('url')]
    return {'answer': texte, 'source_ids': [s['id'] for s in sources], 'limits': list(r['limites']), 'proposed_actions': [], 'sources': sources, 'conversation': True, 'outil': r}


def avant_mission(desk, cadre, owner='cabinet'):
    """Question publique posée au fil (texte ou voix), avant toute mission : la météo est servie tout de suite (adaptateur, sans modèle ni file) ;
    une recherche web non confirmée ou un train à préciser rendent la proposition (rien n'est envoyé) ; une recherche confirmée devient une
    question en file, servie par ``workspace.chat``. Retour : None (mission à créer) ou (état, texte, exceptions, sources)."""
    if cadre['outil'] == 'web' and cadre['confirmee'] and not controle_web(desk, ' '.join(str(cadre.get('requete') or '').split())[:400], cadre['pieces']):
        return None   # confirmée et recevable : la recherche part au tour de la file, jamais pendant la requête de la page
    out = reponse(desk, cadre, owner, pieces=cadre['pieces'])
    r = out['outil']
    if r['statut'] == 'a_confirmer':
        return 'decision', out['answer'], [{'code': 'recherche_publique_a_confirmer', 'message': out['answer'], 'requete': r['donnees'].get('requete', '')}], []
    if r['statut'] == 'a_preciser':
        return 'decision', out['answer'], [{'code': 'recherche_publique_a_preciser', 'message': out['answer']}], []
    return 'answered', out['answer'], [], out['sources']


# ------------------------------------------------------------------------------------------------ page de réglage (administrateur)
def disponibilite(desk, acteur=''):
    """Test de disponibilité sans appel réseau ni consommation : réglage, clé, politique des données, budget restant."""
    from .veille5632 import tavily_key_path
    from . import data_policy5689 as dp
    reg, q = reglage(desk), quota(desk)
    etapes = [{'id': 'reglage', 'ok': reg['enabled'], 'detail': 'Recherches publiques autorisées' if reg['enabled'] else MOTIFS['recherche_publique_desactivee']},
              {'id': 'cle', 'ok': bool(tavily_key_path(desk)), 'detail': 'Clé Tavily configurée' if tavily_key_path(desk) else MOTIFS['cle_tavily_absente']}]
    decision = dp.evaluer(desk.c, 'recherche_web', 'tavily', finalite='recherche_publique', acteur=acteur, journal=False)
    motif = (decision['denial_reasons'] or [''])[0].split(':')[0]
    etapes.append({'id': 'politique', 'ok': decision['allowed'],
                   'detail': 'Politique des données : transfert admis pour une requête publique' if decision['allowed'] else MOTIFS.get(motif, 'Refusé par la politique des données.')})
    reste = min(q['plafond_public'] - q['public'], q['plafond_partage'] - q['total'])
    etapes.append({'id': 'budget', 'ok': reste > 0, 'detail': '%d appel(s) restant(s) ce mois-ci' % max(0, reste)})
    ok = all(x['ok'] for x in etapes)
    return {'disponible': ok, 'etapes': etapes, 'quota': q, 'reseau': 'aucun appel effectué par ce test',
            'message': 'Recherche publique disponible (aucun appel effectué).' if ok else 'Indisponible : ' + ' ; '.join(x['detail'] for x in etapes if not x['ok'])}
