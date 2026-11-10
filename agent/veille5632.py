"""5.6.32 (cahier des charges additionnel, R12 / R13 — VJ01 à VJ10) : veille juridique planifiée, sourcée et affichée sur Aujourd'hui.

- Profil propre à la veille (thèmes, mots-clés, exclusions, territoire, sources, fréquence, heure, fuseau, budget) ; il ne dépend ni
  du rôle A6 ni de l'activation de toutes les initiatives. Les anciens réglages (domaines et flux officiels) sont repris sans être effacés.
- Sources : catalogue initial de flux réellement testés, flux publics ajoutés par l'utilisateur (HTTPS public, sans redirection,
  XML contrôlé), connecteur juridique activé (décisions récentes par thème) et, si une clé est fournie, recherche Internet Tavily.
- Exécution fiable : un travail unique par utilisateur et créneau (contrainte d'unicité), bail de travail, reprises bornées ; le dernier
  succès n'est mis à jour qu'après publication du résultat ; une source en panne donne une veille partielle, jamais « aucune nouveauté ».
- Dates séparées (publication, décision, consultation) ; déduplication par URL canonique et contenu ; un commentaire n'est pas une décision.
- Les textes collectés sont des données : aucune instruction qu'ils contiennent n'est exécutée.
"""
from datetime import date, datetime, timedelta, timezone
from email.utils import parsedate_to_datetime
import hashlib
from html import escape as e, unescape
import ipaddress
import json
import re
import socket
from urllib.parse import urljoin, urlsplit, urlunsplit
from zoneinfo import ZoneInfo

from .common import HTTP, Stop, fold, read_secret, xml_bytes

TESTED_ON = '2026-10-11'
FEEDS = (   # flux vérifiés le 11 octobre 2026 (réponse 200, format RSS, éléments datés)
    ('cnil', 'CNIL — actualités', 'https://www.cnil.fr/fr/rss.xml', 'officielle', 'FR', 'fr'),
    ('justice', 'Ministère de la Justice — actualités', 'https://www.justice.gouv.fr/rss.xml', 'officielle', 'FR', 'fr'),
    ('eurlex', 'EUR-Lex — législation du Parlement et du Conseil', 'https://eur-lex.europa.eu/EN/display-feed.rss?rssId=162', 'officielle', 'UE', 'en'),
)
OFFICIAL_HOSTS = ('cnil.fr', 'justice.gouv.fr', 'eur-lex.europa.eu', 'legifrance.gouv.fr', 'courdecassation.fr', 'conseil-etat.fr',
                  'conseil-constitutionnel.fr', 'curia.europa.eu', 'service-public.gouv.fr', 'economie.gouv.fr', 'travail-emploi.gouv.fr')
MODES = ('daily', 'days', 'weekly', 'manual', 'interval')
# 5.6.96 (I05) : routine « Veille juridique et économique » tous les N jours calendaires (mode « interval »), comptés en dates locales
# depuis une ancre ; les thèmes portent un domaine (les thèmes historiques restent juridiques).
ROUTINE = 'Veille juridique et économique'
DOMAINES = ('juridique', 'economique')
INTERVALLE_DEFAUT, INTERVALLE_MIN, INTERVALLE_MAX = 3, 1, 30
STATES =('pending', 'running', 'done', 'partial', 'failed', 'cancelled')
LEASE = timedelta(minutes=15)
MAX_ATTEMPTS = 3
TAVILY = 'https://api.tavily.com'


def _now():
    return datetime.now(timezone.utc)


def schema(desk):
    desk.db.executescript('''
    CREATE TABLE IF NOT EXISTS veille_runs5632(id INTEGER PRIMARY KEY AUTOINCREMENT, owner TEXT NOT NULL, slot TEXT NOT NULL, state TEXT NOT NULL,
      attempts INTEGER NOT NULL DEFAULT 0, lease_until TEXT NOT NULL DEFAULT '', created TEXT NOT NULL, started TEXT NOT NULL DEFAULT '',
      finished TEXT NOT NULL DEFAULT '', added INTEGER NOT NULL DEFAULT 0, sources TEXT NOT NULL DEFAULT '[]', error TEXT NOT NULL DEFAULT '',
      UNIQUE(owner, slot));
    CREATE TABLE IF NOT EXISTS veille_items5632(id TEXT PRIMARY KEY, owner TEXT NOT NULL, theme TEXT NOT NULL, kind TEXT NOT NULL,
      source_kind TEXT NOT NULL, source TEXT NOT NULL, url TEXT NOT NULL, title TEXT NOT NULL, published TEXT NOT NULL, decided TEXT NOT NULL,
      consulted TEXT NOT NULL, summary TEXT NOT NULL, reason TEXT NOT NULL, content_sha256 TEXT NOT NULL, status TEXT NOT NULL DEFAULT 'new',
      matter TEXT NOT NULL DEFAULT '', run_id INTEGER NOT NULL);
    CREATE INDEX IF NOT EXISTS veille_items5632_owner ON veille_items5632(owner, status, consulted);
    ''')
    cols = {r[1] for r in desk.db.execute('PRAGMA table_info(veille_runs5632)')}
    if 'job_id' not in cols:                                   # 5.6.56 (N07) : travail associé au passage, pour la réconciliation
        desk.db.execute("ALTER TABLE veille_runs5632 ADD COLUMN job_id TEXT NOT NULL DEFAULT ''")
        desk.db.commit()


# --------------------------------------------------------------------------------------------- profil
def default_profile(desk, owner):
    """Migration douce : domaines et flux officiels de l'ancienne veille deviennent des thèmes et des sources, sans effacer l'ancien réglage."""
    themes, custom = [], []
    try:
        from . import settings568
        old = settings568.profile(desk, owner)
        for field in old.get('legal_fields') or []:
            themes.append({'name': str(field)[:80], 'keywords': [str(field)[:80]], 'exclusions': [], 'territory': 'FR', 'lang': 'fr'})
        custom = [u for u in old.get('news_sources') or [] if u not in [f[2] for f in FEEDS]]
        tz = old.get('timezone') or 'Europe/Paris'
        hour = old.get('briefing_time') or '07:30'
    except Exception:   # noqa: BLE001 — aucun ancien réglage
        tz, hour = 'Europe/Paris', '07:30'
    raw = desk.settings('proactive568:profile:' + owner, None)
    explicit_off = isinstance(raw, dict) and raw.get('news_enabled') is False   # 5.6.56 : une veille désactivée n'est jamais réactivée
    for t in themes:
        t['domaine'] = 'juridique'
    # 5.6.96 (I05) : « interval_days » et « anchor_date » pour le mode « interval » ; un profil jamais enregistré garde la fréquence
    # historique (« daily ») : la routine de trois jours est PROPOSÉE à l'écran (``propose``), jamais imposée à une veille existante.
    return {'enabled': bool(themes) and not explicit_off, 'themes': themes, 'feeds': [f[0] for f in FEEDS], 'custom_feeds': custom[:20], 'connector': True,
            'tavily': False, 'mode': 'daily', 'days': [0, 1, 2, 3, 4], 'weekday': 0, 'hour': hour, 'tz': tz,
            'max_results': 5, 'tavily_calls_per_run': 3, 'tavily_monthly_calls': 300, 'lookback_days': 14,
            'interval_days': INTERVALLE_DEFAUT, 'anchor_date': ''}


def profile(desk, owner='cabinet'):
    saved = desk.settings('veille5632:profile:' + owner, None)
    base = default_profile(desk, owner)
    if isinstance(saved, dict):
        base.update({k: v for k, v in saved.items() if k in base})
    base['themes'] = [{**t, 'domaine': t.get('domaine') if t.get('domaine') in DOMAINES else 'juridique'}   # 5.6.96 : thèmes historiques juridiques
                      for t in base['themes'] if isinstance(t, dict)]
    return base


def propose(desk, owner='cabinet'):
    """5.6.96 (I05) : profil affiché dans Paramètres › Veille et dans le guide. Une veille jamais enregistrée se voit proposer la routine
    « Veille juridique et économique » tous les trois jours ; une veille déjà réglée (même désactivée) est montrée telle quelle."""
    p = profile(desk, owner)
    if desk.settings('veille5632:profile:' + owner, None) is None:
        p = {**p, 'mode': 'interval', 'interval_days': INTERVALLE_DEFAUT}
    return p


def _theme(raw, domaine='juridique'):
    """« Nom : mot-clé, mot-clé ; sauf : exclusion, exclusion »."""
    raw = str(raw or '').strip()
    if not raw:
        return None
    name, _, rest = raw.partition(':')
    keys, _, excl = rest.partition(';')
    excl = re.sub(r'^\s*(?:sauf|exclure|exclusions?)\s*:?', '', excl, flags=re.I)
    keywords = [k.strip()[:80] for k in (keys or name).split(',') if k.strip()]
    return {'name': name.strip()[:80], 'keywords': keywords[:20], 'exclusions': [x.strip()[:80] for x in excl.split(',') if x.strip()][:20],
            'territory': 'FR', 'lang': 'fr', 'domaine': domaine}


def _themes_saisis(value, domaine):
    rows = value if isinstance(value, list) else str(value or '').splitlines()
    out = []
    for row in rows:
        t = dict(row) if isinstance(row, dict) else _theme(row, domaine)
        if t and t.get('name'):
            t['domaine'] = t.get('domaine') if isinstance(row, dict) and t.get('domaine') in DOMAINES else domaine
            out.append(t)
    return out


def _cadence(p):
    return (p['mode'], int(p.get('interval_days') or INTERVALLE_DEFAUT), p['hour'], p['tz'])


def save_profile(desk, owner, data):
    p = profile(desk, owner)
    before = (_cadence(p), bool(p.get('enabled')))
    if 'themes' in data or 'themes_eco' in data:
        # 5.6.96 (I05) : domaines saisis séparément ; « themes » seul (appelants historiques) remplace les thèmes juridiques et garde les
        # thèmes économiques, sauf s'il porte lui-même des thèmes économiques (liste complète).
        legal = _themes_saisis(data['themes'], 'juridique') if 'themes' in data else [t for t in p['themes'] if t['domaine'] == 'juridique']
        if 'themes_eco' in data:
            eco = _themes_saisis(data['themes_eco'], 'economique')
        elif any(t['domaine'] == 'economique' for t in legal):
            eco = []
        else:
            eco = [t for t in p['themes'] if t['domaine'] == 'economique']
        themes = legal + eco
        if len(themes) > 30:
            raise Stop('trop_de_themes_veille')
        p['themes'] = themes
    if 'feeds' in data:
        p['feeds'] = [f for f in data['feeds'] if f in {x[0] for x in FEEDS}]
    if 'custom_feeds' in data:
        urls = data['custom_feeds'] if isinstance(data['custom_feeds'], list) else str(data['custom_feeds']).split()
        p['custom_feeds'] = [validate_feed_url(u, resolve=False) for u in urls if str(u).strip()][:20]
    for key in ('connector', 'tavily', 'enabled'):
        if key in data:
            p[key] = data[key] in (True, 'true', '1', 'on')
    if 'mode' in data:
        if data['mode'] not in MODES:
            raise Stop('frequence_veille_invalide')
        p['mode'] = data['mode']
    if 'days' in data:
        p['days'] = sorted({int(d) for d in data['days'] if str(d).isdigit() and 0 <= int(d) <= 6})
    if 'weekday' in data:
        p['weekday'] = max(0, min(6, int(data['weekday'])))
    if 'hour' in data:
        if not re.fullmatch(r'([01]\d|2[0-3]):[0-5]\d', str(data['hour'])):
            raise Stop('heure_veille_invalide')
        p['hour'] = data['hour']
    if 'tz' in data:
        try:
            ZoneInfo(str(data['tz']))
        except Exception:
            raise Stop('fuseau_inconnu') from None
        p['tz'] = str(data['tz'])
    for key, lo, hi in (('max_results', 1, 10), ('tavily_calls_per_run', 0, 20), ('tavily_monthly_calls', 0, 5000), ('lookback_days', 1, 60)):
        if key in data:
            p[key] = max(lo, min(hi, int(data[key])))
    if 'interval_days' in data:   # 5.6.96 (I05) : intervalle validé côté serveur, jamais borné en silence
        try:
            n = int(str(data['interval_days']).strip())
        except ValueError:
            raise Stop('intervalle_veille_invalide') from None
        if not INTERVALLE_MIN <= n <= INTERVALLE_MAX:
            raise Stop('intervalle_veille_invalide')
        p['interval_days'] = n
    ancre = None
    if str(data.get('anchor_date') or '').strip():
        try:
            ancre = date.fromisoformat(str(data['anchor_date']).strip())
        except ValueError:
            raise Stop('ancre_veille_invalide') from None
        today = _now().astimezone(ZoneInfo(p['tz'])).date()
        if ancre < today or ancre > today + timedelta(days=366):
            raise Stop('ancre_veille_invalide')   # jamais dans le passé : aucun passage « rattrapé » avant l'activation
    if p['mode'] == 'interval':
        # Après une modification de cadence ou une (ré)activation, ancre FUTURE recalculée ; l'historique des passages est conservé.
        if ancre is not None:
            p['anchor_date'] = ancre.isoformat()
        elif not p.get('anchor_date') or before != (_cadence(p), bool(p.get('enabled'))):
            p['anchor_date'] = premiere_date(p).isoformat()
    desk.setting('veille5632:profile:' + owner, p)
    desk.audit('veille5632_profil', {'owner': owner, 'themes': len(p['themes']), 'mode': p['mode'], 'tavily': p['tavily']})
    return p


def owners(desk):
    """Propriétaires d'un profil de veille. 5.6.56 (audit 5.6.55, N06) : un ancien profil (« proactive568 ») repris à l'écran est
    MATÉRIALISÉ une fois dans le nouveau registre (migration idempotente, activation antérieure respectée) : il est réellement
    planifié, sans demander à l'utilisateur d'enregistrer de nouveau un réglage présenté comme actif."""
    rows = desk.db.execute("SELECT key FROM settings WHERE key LIKE 'veille5632:profile:%'").fetchall()
    found = [r['key'].split(':', 2)[-1] for r in rows]
    for r in desk.db.execute("SELECT key FROM settings WHERE key LIKE 'proactive568:profile:%'").fetchall():
        owner = r['key'].split(':', 2)[-1]
        if owner in found:
            continue
        p = default_profile(desk, owner)
        if p['themes']:
            desk.setting('veille5632:profile:' + owner, {**p, 'migre_de': 'proactive568', 'migre_le': _now().isoformat()})
            found.append(owner)
    return found


def _job_active(desk, run):
    """Le passage a-t-il un travail réellement vivant (en file ou en cours) ?"""
    ids = [str(run['job_id'] or '')] if 'job_id' in run.keys() else []
    for jid in [i for i in ids if i]:
        row = desk.db.execute("SELECT 1 FROM jobs WHERE CAST(id AS TEXT)=? AND status IN ('pending','running','cancel_requested')", (jid,)).fetchone()
        if row:
            return True
    marker = '%"slot": "' + str(run['slot']).replace('%', '') + '"%'
    return bool(desk.db.execute("SELECT 1 FROM jobs WHERE kind='veille5632_collect' AND status IN ('pending','running','cancel_requested') "
                                "AND args LIKE ? AND args LIKE ?", (marker, '%"owner": "' + str(run['owner']).replace('%', '') + '"%')).fetchone())


def _enqueue_run(desk, run, priority):
    """Mise en file d'un passage ; un refus (file pleine) est consigné sur le passage, qui sera repris au cycle suivant."""
    try:
        job = desk.enqueue('veille5632_collect', {'owner': run['owner'], 'slot': run['slot']}, priority=priority)
    except Stop as ex:
        desk.db.execute("UPDATE veille_runs5632 SET error=?,job_id='' WHERE id=?", (('mise_en_file_refusee:' + str(ex))[:200], run['id']))
        desk.db.commit()
        raise
    desk.db.execute("UPDATE veille_runs5632 SET job_id=?,error='' WHERE id=?", (str(job or ''), run['id']))
    desk.db.commit()
    return job


# --------------------------------------------------------------------------------------------- planification
def _ancre(p):
    try:
        return date.fromisoformat(str(p.get('anchor_date') or ''))
    except ValueError:
        return None


def jour_intervalle(p, jour_local):
    """5.6.96 (I05) : jour de passage du mode « interval » : compté en DATES LOCALES depuis l'ancre (jamais par ajout de 72 h UTC) ;
    aucun jour avant l'ancre."""
    ancre = _ancre(p)
    if ancre is None:
        return False
    jours = (jour_local - ancre).days
    return jours >= 0 and jours % max(INTERVALLE_MIN, min(INTERVALLE_MAX, int(p.get('interval_days') or INTERVALLE_DEFAUT))) == 0


def _jour_eligible(p, jour):
    """Calcul du jour partagé par ``next_due`` et ``current_slot`` (tous les modes)."""
    mode = p['mode']
    if mode == 'daily':
        return True
    if mode == 'days':
        return jour.weekday() in (p.get('days') or [])
    if mode == 'weekly':
        return jour.weekday() == p.get('weekday', 0)
    if mode == 'interval':
        return jour_intervalle(p, jour)
    return False


def _instant(p, jour):
    """Instant (UTC) du créneau d'un jour local. Une heure locale inexistante (passage à l'heure d'été) est décalée à la première minute
    valide ; une heure répétée (retour à l'heure d'hiver) donne sa première occurrence : un seul créneau, au même identifiant."""
    tz = ZoneInfo(p['tz'])
    h, m = (int(x) for x in p['hour'].split(':'))
    mur = datetime(jour.year, jour.month, jour.day, h, m)
    for minutes in range(0, 24 * 60):
        essai = mur + timedelta(minutes=minutes)
        utc = essai.replace(tzinfo=tz, fold=0).astimezone(timezone.utc)
        if utc.astimezone(tz).replace(tzinfo=None) == essai:
            return utc
    return mur.replace(tzinfo=tz).astimezone(timezone.utc)


def premiere_date(p, now=None):
    """Première date locale éligible APRÈS l'activation (ancre du mode « interval ») : aujourd'hui si l'heure n'est pas passée, sinon demain."""
    now = now or _now()
    jour = now.astimezone(ZoneInfo(p['tz'])).date()
    while _instant(p, jour) <= now:
        jour += timedelta(days=1)
    return jour


def next_due(p, now=None):
    """Prochaine exécution prévue (heure locale du fuseau choisi) ; None en mode « à la demande »."""
    if p['mode'] == 'manual' or not p.get('enabled'):
        return None
    tz = ZoneInfo(p['tz'])
    now = now or _now()
    start = now.astimezone(tz).date()
    ancre = _ancre(p) if p['mode'] == 'interval' else None
    if ancre and ancre > start:
        start = ancre
    for offset in range(0, INTERVALLE_MAX + 8):
        jour = start + timedelta(days=offset)
        if _jour_eligible(p, jour):
            moment = _instant(p, jour)
            if moment >= now:
                return moment.astimezone(tz)
    return None


def _slot_id(p, jour):
    return 'slot-' + jour.isoformat() + 'T' + p['hour'] + '-' + p['tz']


def current_slot(p, now=None):
    """Créneau échu le plus récent (un seul rattrapage après un arrêt du poste, jamais une collecte par heure manquée)."""
    if p['mode'] == 'manual' or not p.get('enabled'):
        return None
    now = now or _now()
    today = now.astimezone(ZoneInfo(p['tz'])).date()
    ancre = _ancre(p) if p['mode'] == 'interval' else None
    for back in range(0, INTERVALLE_MAX + 8):
        jour = today - timedelta(days=back)
        if p['mode'] == 'interval' and (ancre is None or jour < ancre):
            return None   # jamais avant l'ancre
        if _jour_eligible(p, jour) and _instant(p, jour) <= now:
            return _slot_id(p, jour)
    return None


def creneau_conforme(p, slot):
    """5.6.96 (I05) : le travailleur revérifie, avant la collecte, que le passage planifié appartient encore à la configuration
    (activation, thèmes, mode, jour, heure, fuseau). Un passage « Actualiser » (manual-…) n'est pas concerné."""
    slot = str(slot or '')
    if not slot.startswith('slot-'):
        return True
    if p['mode'] == 'manual' or not p.get('enabled') or not p['themes']:
        return False
    m = re.fullmatch(r'slot-(\d{4}-\d{2}-\d{2})T(\d{2}:\d{2})-(.+)', slot)
    if not m:
        return True   # identifiant d'une version antérieure : seule l'activation est revérifiée
    try:
        jour = date.fromisoformat(m.group(1))
    except ValueError:
        return False
    return m.group(2) == p['hour'] and m.group(3) == p['tz'] and _jour_eligible(p, jour)


def sources_utilisables(desk, p):
    """Sources réellement interrogeables pour les thèmes choisis ; le connecteur juridique ne sert que les thèmes juridiques."""
    out = []
    if p['feeds']:
        out.append('flux')
    if p['custom_feeds']:
        out.append('flux_personnels')
    if p['connector'] and any(t['domaine'] == 'juridique' for t in p['themes']):
        out.append('connecteur')
    if p['tavily'] and tavily_key_path(desk):
        out.append('tavily')
    return out


def attente(desk, p):
    """Une routine attend une sélection (thème) et une source utilisable avant son premier passage ; '' si elle est prête."""
    if not p['themes']:
        return 'selection'
    if not sources_utilisables(desk, p):
        return 'source'
    return ''


def _reserver(desk, owner, slot, now, priority=29):
    """5.6.96 (I05) : réservation du passage ET liaison de son travail dans UNE transaction (verrou d'écriture SQLite) : deux cycles
    concurrents ne créent qu'un travail ; un passage terminé, annulé ou en cours n'est jamais relancé ; signal au travailleur après la
    validation seulement. Un refus de mise en file est consigné sur le passage, repris au cycle suivant (5.6.56, N07)."""
    args = {'owner': owner, 'slot': slot}
    if desk.db.in_transaction:
        desk.db.commit()
    desk.db.execute('BEGIN IMMEDIATE')
    try:
        desk.db.execute("INSERT OR IGNORE INTO veille_runs5632(owner,slot,state,created) VALUES(?,?,'pending',?)", (owner, slot, now.isoformat()))
        run = desk.db.execute('SELECT * FROM veille_runs5632 WHERE owner=? AND slot=?', (owner, slot)).fetchone()
        if run['state'] != 'pending' or _job_active(desk, run):
            desk.db.commit()
            return None
        job = desk.enqueue('veille5632_collect', args, priority=priority, transactional=True)
        desk.db.execute("UPDATE veille_runs5632 SET job_id=?,error='' WHERE id=?", (str(job or ''), run['id']))
        desk.db.commit()
    except Stop as ex:
        if desk.db.in_transaction:
            desk.db.rollback()
        desk.db.execute("INSERT OR IGNORE INTO veille_runs5632(owner,slot,state,created) VALUES(?,?,'pending',?)", (owner, slot, now.isoformat()))
        desk.db.execute("UPDATE veille_runs5632 SET error=?,job_id='' WHERE owner=? AND slot=? AND state='pending'",
                        (('mise_en_file_refusee:' + str(ex))[:200], owner, slot))
        desk.db.commit()
        raise
    except BaseException:
        if desk.db.in_transaction:
            desk.db.rollback()
        raise
    desk.notify_queued(job, args)
    return job


def schedule(desk, stamp=None):
    """Appelé par le cycle de surveillance : au plus un travail par utilisateur et par créneau (contrainte d'unicité, réservation
    transactionnelle). 5.6.96 (I05) : seul planificateur de la veille, y compris la routine « tous les trois jours »."""
    schema(desk)
    now = datetime.fromtimestamp(stamp, timezone.utc) if stamp else _now()
    jobs = []
    for owner in owners(desk):
        p = profile(desk, owner)
        slot = current_slot(p, now)
        if not slot or attente(desk, p):
            continue
        job = _reserver(desk, owner, slot, now)   # 5.6.60 : priorité 29, programmée par l'avocat, devant les travaux de maintenance
        if job is not None:
            jobs.append(job)
    try:   # 5.6.61 : une fois, résultats Internet sans date déjà collectés masqués
        hide_undated_web(desk)
    except Exception:   # noqa: BLE001
        pass
    # 5.6.61 : collectes mises en file avant la 5.6.60 avec l'ancienne priorité (65) : relevées
    desk.db.execute("UPDATE jobs SET priority=29 WHERE kind='veille5632_collect' AND status='pending' AND priority>29")
    desk.db.commit()
    try:
        from .blocage5660 import close_orphan_veille
        close_orphan_veille(desk)
    except Exception:   # noqa: BLE001
        pass
    return jobs


def refresh(desk, owner='cabinet'):
    """« Actualiser » : une seule collecte à la fois ; un second clic rejoint la collecte en cours au lieu d'en lancer une autre."""
    schema(desk)
    active = desk.db.execute("SELECT * FROM veille_runs5632 WHERE owner=? AND state IN ('pending','running') ORDER BY id DESC LIMIT 1", (owner,)).fetchone()
    if active and _job_active(desk, active):                 # 5.6.56 (N07) : on ne « rejoint » qu'un travail réellement vivant
        # 5.6.61 : rejoindre ne suffit pas : la collecte en file prend la priorité d'une demande de l'avocat
        desk.db.execute("UPDATE jobs SET priority=MIN(priority,20),not_before=NULL WHERE kind='veille5632_collect' AND status='pending' "
                        "AND args LIKE ? AND args LIKE ?", ('%"slot": "' + str(active['slot']).replace('%', '') + '"%', '%"owner": "' + str(owner).replace('%', '') + '"%'))
        desk.db.commit()
        return {'run': active['id'], 'slot': active['slot'], 'joined': True}
    if active and active['state'] == 'pending':              # passage orphelin : réparé par ce clic
        job = _enqueue_run(desk, active, 20)
        return {'run': active['id'], 'slot': active['slot'], 'job': job, 'joined': False, 'repare': True}
    slot = 'manual-' + _now().strftime('%Y-%m-%dT%H:%M')
    desk.db.execute("INSERT OR IGNORE INTO veille_runs5632(owner,slot,state,created) VALUES(?,?,'pending',?)", (owner, slot, _now().isoformat()))
    desk.db.commit()
    job = desk.enqueue('veille5632_collect', {'owner': owner, 'slot': slot}, priority=20)
    row = desk.db.execute('SELECT id FROM veille_runs5632 WHERE owner=? AND slot=?', (owner, slot)).fetchone()
    return {'run': row['id'], 'slot': slot, 'job': job, 'joined': False}


def claim(desk, owner, slot):
    """Prise du travail avec bail ; un bail expiré (travail interrompu) est repris, au plus MAX_ATTEMPTS fois."""
    schema(desk)
    now = _now()
    row = desk.db.execute('SELECT * FROM veille_runs5632 WHERE owner=? AND slot=?', (owner, slot)).fetchone()
    if not row:
        desk.db.execute("INSERT OR IGNORE INTO veille_runs5632(owner,slot,state,created) VALUES(?,?,'pending',?)", (owner, slot, now.isoformat()))
        row = desk.db.execute('SELECT * FROM veille_runs5632 WHERE owner=? AND slot=?', (owner, slot)).fetchone()
    if row['state'] in ('done', 'partial', 'cancelled'):
        return None
    if row['state'] == 'running' and row['lease_until'] > now.isoformat():
        return None
    if row['attempts'] >= MAX_ATTEMPTS:
        desk.db.execute("UPDATE veille_runs5632 SET state='failed', error='reprises_epuisees', finished=? WHERE id=?", (now.isoformat(), row['id']))
        desk.db.commit()
        return None
    cur = desk.db.execute("UPDATE veille_runs5632 SET state='running', attempts=attempts+1, lease_until=?, started=? WHERE id=? AND attempts=?",
                          ((now + LEASE).isoformat(), now.isoformat(), row['id'], row['attempts']))
    desk.db.commit()
    return row['id'] if cur.rowcount else None


# --------------------------------------------------------------------------------------------- sources
def validate_feed_url(url, resolve=True):
    """Flux ajouté par l'utilisateur : HTTPS public uniquement (aucune adresse du réseau interne), port standard, sans identifiants."""
    url = str(url or '').strip()
    p = urlsplit(url)
    try:
        port = p.port
    except ValueError:
        raise Stop('source_veille_invalide') from None
    if p.scheme != 'https' or p.username or p.password or p.fragment or port not in (None, 443) or not p.hostname or len(url) > 600:
        raise Stop('source_veille_https_publique_requise')
    host = p.hostname.lower()
    if host == 'localhost' or host.endswith(('.local', '.internal', '.lan')):
        raise Stop('source_veille_reseau_interne_refuse')
    try:
        if not ipaddress.ip_address(host).is_global:
            raise Stop('source_veille_reseau_interne_refuse')
    except ValueError:
        pass
    if resolve:
        try:
            addresses = {r[4][0] for r in socket.getaddrinfo(host, 443, type=socket.SOCK_STREAM)}
        except socket.gaierror:
            raise Stop('source_veille_introuvable') from None
        if not addresses or any(not ipaddress.ip_address(a).is_global for a in addresses):
            raise Stop('source_veille_reseau_interne_refuse')
    return url


def _clean(text, limit=2000):
    return re.sub(r'\s+', ' ', unescape(re.sub(r'<[^>]+>', ' ', str(text or '')))).strip()[:limit]


def canonical(url):
    p = urlsplit(str(url or ''))
    query = '&'.join(sorted(q for q in p.query.split('&') if q and not q.lower().startswith(('utm_', 'xtor=', 'at_'))))
    return urlunsplit((p.scheme.lower(), (p.hostname or '').lower(), p.path.rstrip('/') or '/', query, ''))


def parse_feed(raw, source, now=None, days=14):
    """RSS ou Atom (XML contrôlé) : titre, lien, date de publication, extrait ; un élément sans date reste « non renseignée »."""
    now = now or _now()
    root = xml_bytes(raw)
    a = '{http://www.w3.org/2005/Atom}'
    rows = root.findall('.//item') or root.findall(a + 'entry')
    if not rows and root.tag not in ('rss', a + 'feed', '{http://www.w3.org/1999/02/22-rdf-syntax-ns#}RDF'):
        raise Stop('flux_veille_rss_atom_requis')
    out = []
    for r in rows[:200]:
        title = _clean(r.findtext('title') or r.findtext(a + 'title'), 600)
        link = r.findtext('link') or next((x.get('href', '') for x in r.findall(a + 'link') if x.get('rel', 'alternate') == 'alternate'), '')
        url = urljoin(source, (link or '').strip())
        if not title or not url.startswith('https://'):
            continue
        when = r.findtext('pubDate') or r.findtext(a + 'published') or r.findtext(a + 'updated') or r.findtext('{http://purl.org/dc/elements/1.1/}date')
        published = ''
        if when:
            try:
                d = parsedate_to_datetime(when) if ',' in when else datetime.fromisoformat(str(when).strip().replace('Z', '+00:00'))
                if d.tzinfo is None:
                    d = d.replace(tzinfo=timezone.utc)
                if d < now - timedelta(days=days) or d > now + timedelta(hours=2):
                    continue
                published = d.astimezone(timezone.utc).isoformat()
            except (ValueError, TypeError, OverflowError):
                published = ''
        summary = _clean(r.findtext('description') or r.findtext(a + 'summary') or r.findtext(a + 'content') or '', 1500)
        out.append({'url': url, 'title': title, 'published': published, 'summary': summary})
    return out


def fetch_feed(url, fetch=None):
    if fetch:
        return fetch(url)
    p = urlsplit(url)
    return HTTP('https://' + p.netloc, timeout=20).request('GET', url, headers={'Accept': 'application/rss+xml, application/atom+xml, application/xml, text/xml'},
                                                            limit=3_000_000)


def tavily_key_path(desk):
    return desk.settings('veille5632:tavily_secret', '') or ''


def save_tavily_key(desk, key):
    key = str(key or '').strip()
    if not 8 <= len(key) <= 300 or re.search(r'\s', key):
        raise Stop('cle_tavily_invalide')
    from pathlib import Path
    from .vault567 import write
    path = write(Path(desk.c['state_dir']) / 'vault' / 'veille5632' / 'tavily.secret', key)
    desk.setting('veille5632:tavily_secret', path)
    desk.audit('veille5632_cle_tavily', {'replaced': True})
    return {'configured': True}


def _usage_key():
    return 'veille5632:tavily_usage:' + _now().strftime('%Y-%m')


def usage(desk):
    return desk.settings(_usage_key(), {'calls': 0, 'uncertain': 0}) or {'calls': 0, 'uncertain': 0}


def tavily_search(desk, query, max_results=5, days=7, domains=(), http=None, p=None):
    """Recherche Tavily de la veille (actualités datées). 5.6.96 (R02, R05) : le client HTTP est commun avec l'assistant
    (``recherche_publique5696.tavily``, profil ``veille``) : politique des données avant le compteur, budget réservé de façon atomique
    avant l'appel (plafond de la veille et plafond partagé avec l'assistant), hôtes normalisés (égalité ou suffixe précédé d'un point :
    ``fausse-x.fr`` n'est plus admis pour ``x.fr``), 401/403/429/432 traduits, coupure après envoi comptée comme incertaine sans relance."""
    from .recherche_publique5696 import tavily
    return tavily(desk, query, profil='veille', max_results=max_results, days=days, domaines=domains, http=http, p=p or {})


def connector_recent(desk, theme, days=14, call=None):
    """Décisions récentes par thème via un connecteur juridique activé qui déclare l'outil de recherche (tri par date)."""
    from .controle5632 import Checker
    checker = Checker(desk, call)
    if not checker.item or not checker.available('search'):
        return None
    since = (_now() - timedelta(days=days)).strftime('%Y-%m-%d')
    query = ' '.join(theme['keywords'][:4])[:300]
    got = checker.run('search', {'query': query, 'sort': 'date_desc', 'date_min': since, 'limit': 8})
    rows = got.get('results', got.get('decisions', [])) if isinstance(got, dict) else got
    out = []
    for r in rows or []:
        if not isinstance(r, dict):
            continue
        url = str(r.get('url') or r.get('lien') or r.get('source_url') or '')
        rid = str(r.get('id') or '')
        if not url and rid:
            url = 'https://justicelibre.org/decision/' + rid
        out.append({'url': url, 'title': _clean(r.get('titre') or r.get('title'), 600), 'published': '', 'decided': str(r.get('date') or ''),
                    'summary': _clean(r.get('snippet') or r.get('resume') or '', 1200), 'official_id': rid, 'provider': checker.item.get('provider', '')})
    return out


def requete_theme(theme):
    """Requête Internet d'un thème : ses seuls mots-clés publics, suivis du domaine (juridique ou économique)."""
    suffixe = ' actualité économique' if theme.get('domaine') == 'economique' else ' actualité juridique'
    return ' '.join(theme['keywords'][:5]) + suffixe


def requete_publique(desk, query):
    """5.6.96 (I05) : aucun contenu de dossier dans une requête publique. Un mot-clé qui désigne un dossier, une partie ou un
    correspondant connu du cabinet (``data_policy5689.donnee_de_dossier``) refuse la requête AVANT tout appel ou décompte."""
    from .data_policy5689 import donnee_de_dossier
    if donnee_de_dossier(desk.c, query):
        raise Stop('donnee_de_dossier_dans_la_requete')
    return query


# --------------------------------------------------------------------------------------------- traitement
def classify(themes, item):
    """Thème et raison visible ; filtre déterministe (mots-clés, exclusions) — il ne se limite pas au titre."""
    text = fold(item.get('title', '') + ' ' + item.get('summary', ''))
    for t in themes:
        if any(fold(x) in text for x in t.get('exclusions') or []):
            continue
        hits = [k for k in t.get('keywords') or [] if fold(k) and fold(k) in text]
        if hits:
            return t['name'], 'Mots-clés trouvés : ' + ', '.join(hits[:5])
    return None, ''


def kind_of(url, source_kind):
    host = (urlsplit(url).hostname or '').lower()
    if source_kind == 'connector':
        return 'decision'
    if any(host == h or host.endswith('.' + h) for h in OFFICIAL_HOSTS):
        return 'texte_officiel'
    return 'commentaire'


def _publish(desk, owner, run_id, theme, reason, item, source_kind, source):
    url = item.get('url') or ''
    body = (item.get('title', '') + '\n' + item.get('summary', '')).strip()
    content = hashlib.sha256(body.encode()).hexdigest()
    iid = hashlib.sha256(json.dumps([owner, canonical(url) if url else item.get('official_id', ''), content], ensure_ascii=False).encode()).hexdigest()[:40]
    if desk.db.execute('SELECT 1 FROM veille_items5632 WHERE owner=? AND (id=? OR (url=? AND url<>\'\' AND content_sha256=?))', (owner, iid, canonical(url), content)).fetchone():
        return 0
    cur = desk.db.execute('INSERT OR IGNORE INTO veille_items5632(id,owner,theme,kind,source_kind,source,url,title,published,decided,consulted,summary,reason,content_sha256,run_id) '
                          'VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)',
                          (iid, owner, theme, kind_of(url, source_kind), source_kind, source, canonical(url) if url else '', item.get('title', '')[:600],
                           item.get('published', ''), item.get('decided', ''), _now().isoformat(), item.get('summary', '')[:1500], reason[:300], content, run_id))
    return cur.rowcount


# adresse : rubriques de catalogue (formations, diplômes, librairie) ; titre : seulement des formules sans ambiguïté
# (« du » ou « cours » sont des mots ordinaires : « arrêt du Conseil d'État », « au cours de »)
EVERGREEN_PATH = re.compile(r"(?:^|[\s/_-])(?:formations?|masters?|diplomes?|cursus|offre-de-formation|catalogue|isbn|librairie|boutique|"
                            r"livres?|ouvrages?|fiche-de-revision|guide-complet)(?:$|[\s/_.,:-])")
EVERGREEN_TITLE = re.compile(r"\b(?:choisir sa formation|offre de formation|guide complet|master \d|master droit|diplome universitaire|"
                             r"fiche de revision|cours de droit)\b|^du |^diu |^master ")
BOOKSHOP_HOSTS = ('lgdj.fr', 'amazon.fr', 'amazon.com', 'fnac.com', 'decitre.fr', 'cultura.com', 'leslibraires.fr', 'onisep.fr', 'studyrama.com',
                  'letudiant.fr', 'univ-droit.fr')


def _web_date(value):
    """Date de publication d'un résultat Internet (ISO ou RFC 2822) → AAAA-MM-JJ, ou '' si absente ou illisible."""
    value = str(value or '').strip()
    if not value:
        return ''
    try:
        stamp = datetime.fromisoformat(value.replace('Z', '+00:00'))
    except ValueError:
        try:
            from email.utils import parsedate_to_datetime
            stamp = parsedate_to_datetime(value)
        except (TypeError, ValueError, IndexError):
            return ''
    return stamp.date().isoformat()


def web_relevant(item, days):
    """5.6.61 : un résultat Internet n'entre dans la veille que s'il est daté, récent et n'a pas l'allure d'une page permanente
    (formation, diplôme, librairie, guide). Renvoie (garder, motif)."""
    host = (urlsplit(item.get('url') or '').hostname or '').lower()
    if any(host == h or host.endswith('.' + h) for h in BOOKSHOP_HOSTS):
        return False, 'librairie_ou_formation'
    path = fold(urlsplit(item.get('url') or '').path.replace('/', ' / '))
    title = fold(item.get('title') or '')
    if EVERGREEN_PATH.search(' ' + path + ' ') or EVERGREEN_TITLE.search(title.strip()):
        return False, 'page_permanente'
    published = _web_date(item.get('published'))
    if not published:
        return False, 'sans_date'
    if published < (_now() - timedelta(days=max(1, int(days)))).date().isoformat():
        return False, 'trop_ancien'
    return True, ''


def hide_undated_web(desk, owner=None):
    """5.6.61, une fois : résultats Internet déjà collectés sans date ou hors sujet masqués (toujours consultables, filtre « masqués »)."""
    schema(desk)
    if desk.settings('veille5661:nettoyage', False):
        return 0
    n = 0
    for r in desk.db.execute("SELECT id,url,title,published FROM veille_items5632 WHERE source_kind='tavily' AND status='new'").fetchall():
        keep, _ = web_relevant({'url': r['url'], 'title': r['title'], 'published': r['published'] or ''}, 3650)
        if not keep:
            n += desk.db.execute("UPDATE veille_items5632 SET status='hidden' WHERE id=?", (r['id'],)).rowcount
    desk.setting('veille5661:nettoyage', True)
    desk.db.commit()
    return n


def collect(desk, args, fetch=None, call=None, http=None):
    """Travail de veille : collecte, déduplication, classement, publication ; état terminé / partiel / échoué selon les sources."""
    schema(desk)
    owner, slot = str(args.get('owner') or 'cabinet'), str(args.get('slot') or '')
    p = profile(desk, owner)
    if not creneau_conforme(p, slot) or (slot.startswith('slot-') and attente(desk, p)):
        # 5.6.96 (I05) : veille désactivée ou cadence modifiée depuis la mise en file : le passage est annulé, jamais collecté
        desk.db.execute("UPDATE veille_runs5632 SET state='cancelled', error='configuration_modifiee', finished=?, lease_until='' "
                        "WHERE owner=? AND slot=? AND state='pending'", (_now().isoformat(), owner, slot))
        desk.db.commit()
        return {'skipped': True, 'reason': 'veille_desactivee_ou_modifiee'}
    run_id = claim(desk, owner, slot)
    if run_id is None:
        return {'skipped': True, 'reason': 'creneau_deja_traite_ou_en_cours'}
    p = profile(desk, owner)
    results, added = [], 0
    try:
        for fid in p['feeds']:
            name, url = next(((f[1], f[2]) for f in FEEDS if f[0] == fid), ('', ''))
            if url:
                results.append(_one_feed(desk, owner, run_id, p, name, url, fetch))
        for url in p['custom_feeds']:
            results.append(_one_feed(desk, owner, run_id, p, url, url, fetch, custom=True))
        if p['connector']:
            for t in [x for x in p['themes'] if x['domaine'] == 'juridique']:   # 5.6.96 : le connecteur juridique ne sert pas l'économie
                try:
                    requete_publique(desk, ' '.join(t['keywords'][:4]))
                    rows = connector_recent(desk, t, p['lookback_days'], call)
                    if rows is None:   # 5.6.61 : connecteur non configuré : source absente, pas une panne de la collecte
                        results.append({'source': 'connecteur juridique', 'status': 'skipped', 'error': 'aucun_connecteur_actif'})
                        break
                    n = sum(_publish(desk, owner, run_id, t['name'], 'Recherche par thème : ' + ', '.join(t['keywords'][:3]), r, 'connector', r.get('provider', ''))
                            for r in rows)
                    results.append({'source': 'connecteur · ' + t['name'], 'status': 'ok', 'items': len(rows), 'added': n})
                except Stop as ex:
                    results.append({'source': 'connecteur · ' + t['name'], 'status': 'error', 'error': str(ex)})
        if p['tavily'] and tavily_key_path(desk):
            for t in p['themes'][:max(0, p['tavily_calls_per_run'])]:
                try:
                    rows = tavily_search(desk, requete_publique(desk, requete_theme(t)), p['max_results'], p['lookback_days'], http=http, p=p)
                    kept = []
                    for r in rows:                     # 5.6.61 : datés, récents, pas de page permanente
                        keep, _why = web_relevant(r, p['lookback_days'])
                        if keep:
                            kept.append({**r, 'published': _web_date(r.get('published'))})
                    n = sum(_publish(desk, owner, run_id, t['name'], 'Recherche Internet : ' + ', '.join(t['keywords'][:3]), r, 'tavily', 'Tavily') for r in kept)
                    results.append({'source': 'Tavily · ' + t['name'], 'status': 'ok', 'items': len(rows), 'kept': len(kept), 'added': n})
                except Stop as ex:
                    results.append({'source': 'Tavily · ' + t['name'], 'status': 'error', 'error': str(ex)})
                    if str(ex) in ('plafond_tavily_atteint', 'quota_tavily_atteint', 'cle_tavily_refusee'):
                        break
        added = sum(r.get('added', 0) for r in results)
        ok = [r for r in results if r['status'] == 'ok']
        counted = [r for r in results if r['status'] != 'skipped']
        state = 'done' if ok and len(ok) == len(counted) else ('partial' if ok else 'failed')
        if not results:
            state = 'failed'
        desk.db.execute('UPDATE veille_runs5632 SET state=?, finished=?, added=?, sources=?, lease_until=\'\', error=? WHERE id=?',
                        (state, _now().isoformat(), added, json.dumps(results, ensure_ascii=False)[:20000],
                         '' if ok else 'toutes_sources_indisponibles', run_id))
        desk.db.commit()
        if state in ('done', 'partial'):
            desk.setting('veille5632:last_success:' + owner, _now().isoformat())   # seulement après publication
        desk.setting('veille5632:last_attempt:' + owner, _now().isoformat())
    except Exception:
        desk.db.execute("UPDATE veille_runs5632 SET state='pending', lease_until='' WHERE id=?", (run_id,))   # reprise bornée par MAX_ATTEMPTS
        desk.db.commit()
        raise
    return {'run': run_id, 'state': state, 'added': added, 'sources': results}


def _one_feed(desk, owner, run_id, p, name, url, fetch, custom=False):
    try:
        if custom:
            validate_feed_url(url, resolve=fetch is None)
        rows = parse_feed(fetch_feed(url, fetch), url, days=p['lookback_days'])
        n = 0
        for r in rows:
            theme, reason = classify(p['themes'], r)
            if theme:
                n += _publish(desk, owner, run_id, theme, reason, r, 'rss', name)
        return {'source': name, 'status': 'ok', 'items': len(rows), 'added': n}
    except Stop as ex:
        return {'source': name, 'status': 'error', 'error': str(ex)}


# --------------------------------------------------------------------------------------------- consultation
def state(desk, owner='cabinet'):
    schema(desk)
    p = profile(desk, owner)
    last = desk.db.execute('SELECT * FROM veille_runs5632 WHERE owner=? ORDER BY id DESC LIMIT 1', (owner,)).fetchone()
    waiting = attente(desk, p)
    due = None if waiting else next_due(p)   # 5.6.96 : pas de « prochain passage » annoncé pour une routine qui attend
    # 5.6.96 (I05) : dernier passage TERMINÉ, qualifié « complet », « partiel » ou « source indisponible » (un partiel n'est pas un vide)
    fini = desk.db.execute("SELECT state,finished FROM veille_runs5632 WHERE owner=? AND state IN ('done','partial','failed') ORDER BY id DESC LIMIT 1",
                           (owner,)).fetchone()
    return {'configured': bool(p['themes']) and p['enabled'], 'last_success': desk.settings('veille5632:last_success:' + owner, ''),
            'last_attempt': desk.settings('veille5632:last_attempt:' + owner, ''), 'next_run': due.isoformat() if due else '',
            'last_state': last['state'] if last else '', 'last_error': (last['error'] or '') if last else '', 'failed_sources': [s for s in json.loads(last['sources'] or '[]') if s.get('status') not in ('ok', 'skipped')] if last else [],
            'unread': desk.db.execute("SELECT COUNT(*) FROM veille_items5632 WHERE owner=? AND status='new'", (owner,)).fetchone()[0],
            'routine': ROUTINE, 'mode': p['mode'], 'interval_days': p.get('interval_days'), 'attente': waiting if p['enabled'] else '',
            'last_status': {'done': 'complet', 'partial': 'partiel', 'failed': 'source_indisponible'}.get(fini['state'], '') if fini else '',
            'last_finished': (fini['finished'] or '') if fini else '',
            'domaines': {d: sum(1 for t in p['themes'] if t['domaine'] == d) for d in DOMAINES}}


def items(desk, owner='cabinet', status=('new', 'saved', 'read'), limit=50):
    schema(desk)
    marks = ','.join('?' * len(status))
    return [dict(r) for r in desk.db.execute('SELECT * FROM veille_items5632 WHERE owner=? AND status IN (%s) ORDER BY COALESCE(NULLIF(published,\'\'),NULLIF(decided,\'\'),consulted) DESC LIMIT ?' % marks,
                                             (owner, *status, limit)).fetchall()]


def mark(desk, owner, iid, action, matter=''):
    schema(desk)
    if action not in ('read', 'hidden', 'saved', 'new', 'link'):
        raise Stop('action_veille_inconnue')
    if action == 'link':
        if not matter:
            raise Stop('dossier_requis')
        cur = desk.db.execute('UPDATE veille_items5632 SET matter=? WHERE owner=? AND id=?', (str(matter)[:80], owner, iid))
    else:
        cur = desk.db.execute('UPDATE veille_items5632 SET status=? WHERE owner=? AND id=?', (action, owner, iid))
    desk.db.commit()
    if not cur.rowcount:
        raise Stop('element_veille_absent')
    return {'ok': True}


def _date(value):
    return value[:10] if value else ''


def card_html(desk, prefix, owner='cabinet'):
    """Cadre « Veille juridique » des deux vues d'Aujourd'hui (VJ01) : 3 à 5 nouveautés, état, dernier contrôle, prochaine exécution."""
    from .i18n5631 import th, t as tr
    st = state(desk, owner)
    head = '<h2 id="c5632-t-veille">' + th('vj.title') + '</h2>'
    links = ('<div class="v5632-actions"><a class="ax-btn ghost" href="%s">%s</a><button type="button" class="ax-btn ghost" data-v5632="refresh">%s</button>'
             '<a class="ax-btn ghost" href="%s">%s</a></div>') % (e(prefix + '/veille', quote=True), th('vj.read'), th('vj.refresh'),
                                                                   e(prefix + '/parametres?rubrique=veille', quote=True), th('vj.settings'))
    if not st['configured']:
        return head + '<p>' + th('vj.empty_config') + '</p><p><a class="ax-btn" href="%s">%s</a></p>' % (e(prefix + '/parametres?rubrique=veille', quote=True), th('vj.configure'))
    rows = items(desk, owner, ('new',), 5)
    body = ''
    if st['attente']:   # 5.6.96 (I05) : routine en attente d'un thème ou d'une source utilisable
        body += '<p class="v5632-partial">' + th('vj.waiting.' + st['attente']) + '</p>'
    if st['failed_sources'] and st['last_state'] in ('partial', 'failed'):
        body += '<p class="v5632-partial">' + th('vj.partial', sources=', '.join(s['source'] for s in st['failed_sources'][:4])) + '</p>'
    if rows:
        body += '<ul class="v5632-list">' + ''.join(
            '<li><a href="%s" target="_blank" rel="noopener noreferrer">%s</a><small>%s · %s · %s</small></li>' % (
                e(r['url'] or (prefix + '/veille'), quote=True), e(r['title']), e(r['theme']), e(r['source']),
                e(_date(r['published']) or _date(r['decided']) or th('vj.date_unknown'))) for r in rows) + '</ul>'
        more = st['unread'] - len(rows)
        if more > 0:
            body += '<p class="v5632-more">' + th('vj.remaining', n=more) + '</p>'
    elif st['last_state'] in ('done', 'partial'):
        body += '<p>' + th('vj.nothing_new') + '</p>'
    elif st['last_state'] == 'failed':
        body += '<p class="v5632-partial">' + th('vj.failed') + '</p>'
    elif st['last_state'] == 'pending' and st['last_error'].startswith('mise_en_file_refusee'):   # 5.6.56 (§ 6.3) : états distincts
        body += '<p class="v5632-partial">' + th('vj.queue_refused') + '</p>'
    elif st['last_state'] == 'pending':
        body += '<p>' + th('vj.queued') + '</p>'
    elif st['last_state'] == 'running':
        body += '<p>' + th('vj.running') + '</p>'
    else:
        body += '<p>' + th('vj.not_run') + '</p>'
    body += '<p class="v5632-meta">' + th('vj.meta', last=_date(st['last_success']) or '—', attempt=_date(st['last_attempt']) or '—',
                                            next=(st['next_run'][:16].replace('T', ' ') if st['next_run'] else th('vj.on_demand'))) + '</p>'
    if st['last_status']:   # 5.6.96 (I05) : « complet », « partiel » ou « source indisponible »
        body += '<p class="v5632-meta">' + th('vj.last_run', when=st['last_finished'][:16].replace('T', ' '), status=tr('vj.status.' + st['last_status'])) + '</p>'
    return head + body + links
