"""5.6.95 (lot aujourdhui, A01, A02) : encadré « Actuellement » en haut d'Aujourd'hui.

Une ligne par rubrique (agenda, tâches, courriels à traiter, réponses à décider, documents à relire, avis, stratégie, agent), avec un nombre
et le plus urgent nommé ; chaque ligne mène à sa liste. Le calcul lit les données déjà présentes dans la base, **sans aucun appel à l'IA**
sans écrire aucune donnée : pas de synchronisation du registre, pas de travail mis en file. Il est borné : chaque rubrique est une ou deux requêtes
limitées, et un délai global (``BUDGET_MS``) arrête les rubriques restantes, qui s'annoncent « non calculées » plutôt que comme un zéro.

Droits : les dossiers réservés (``hidden``) sont retirés avant tout compte ; un événement, une tâche ou un travail d'un dossier réservé ne
compte pas et n'est jamais nommé. Le droit « honoraires » filtre les documents comme le registre commun (``livrables5689._where``).
Une source illisible donne l'état ``panne`` (« source indisponible »), jamais un zéro.

Le texte rendu ne contient pas de durée relative (« dans 12 min ») : il ne change que si les données changent, ce qui permet la réponse
conditionnelle ``304`` de la route de rafraîchissement (``actuellement_web5695``). La lecture à voix haute reprend ``texte_parle`` et ne
se fait que sur un clic de l'utilisateur (bouton Écouter, ``v5695-aujourdhui.js``).
"""
from datetime import datetime, time as dtime, timedelta, timezone
from html import escape as e
import hashlib
import json
import re
import time
from urllib.parse import urlencode

BUDGET_MS = 1500                      # au-delà, les rubriques restantes ne sont pas calculées (annoncées comme telles)
RAFRAICHIR_S = 60                     # rafraîchissement automatique de la page (A02)
ECHEANCES_JOURS = 7                   # échéances de procédure proches
PRESCRIPTION_JOURS = 30               # prescriptions proches
RECENCE_JOURS = 30                    # documents « à relire » récents (même borne que le registre commun)
STRATEGIE_TTL = 300                   # points « À décider » gardés 5 min (ou jusqu'à la prochaine décision) : leur service parcourt plusieurs modules
TITRE_MAX = 160                       # titre nommé dans une ligne (l'écran le tronque proprement) ; le titre complet reste dans l'infobulle
AUTO_CLE = 'actuellement5695:arret:'  # + compte : mise à jour automatique arrêtée par cette personne
# 5.6.95 : textes visibles au catalogue (``agent/locales/*.json``, clés ``a95.*``), dans la langue de la personne
RUBRIQUES = (('agenda', 'agenda'), ('taches', 'tache'), ('courriels', 'courriel'), ('reponses', 'discussion'), ('documents', 'document'), ('avis', 'alerte'),
             ('strategie', 'tableau'), ('agent', 'ia'))
ETATS_SIGNALES = ('panne', 'partiel', 'retard')


def T(cle, n=None, **valeurs):
    from .i18n5631 import t
    return t('a95.' + cle, n=n, **valeurs)


def etat_libelle(etat):
    return T('etat.' + etat) if etat in ETATS_SIGNALES else ''


def _court(texte, n=TITRE_MAX):
    texte = ' '.join(str(texte or '').split())
    return texte if len(texte) <= n else texte[:n - 1].rstrip() + '…'


def _table(desk, nom):
    return bool(desk.db.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name=?", (nom,)).fetchone())


def _dans(hidden, colonne):
    """Clause SQL qui retire les dossiers réservés (paramètres triés : requête stable)."""
    if not hidden:
        return '', []
    return ' AND %s NOT IN (%s)' % (colonne, ','.join('?' for _ in hidden)), sorted(hidden)


class Contexte:
    """Ce dont chaque rubrique a besoin : base, personne, dossiers masqués, fuseau, libellés, préfixe des liens."""

    def __init__(self, desk, owner, role, hidden, prefix, now=None):
        from .cockpit530 import _labels, tz
        self.desk, self.owner, self.role, self.prefix = desk, str(owner or 'cabinet'), str(role or 'administrateur'), prefix or ''
        # 5.6.97 (lot proteger) : le garde commun relit le registre pour CETTE personne ; les dossiers masqués transmis ne font qu'ajouter
        from .acces5697 import masques
        self.hidden = frozenset(str(h) for h in (hidden or ())) | masques(desk, (self.owner, self.role))
        self.tz = tz(desk)
        self.now = (now or datetime.now(timezone.utc)).astimezone(self.tz)
        self.jour = self.now.date()
        self._labels = None
        self._lab = _labels

    @property
    def labels(self):
        if self._labels is None:
            self._labels = self._lab(self.desk)
        return self._labels

    def dossier(self, mid):
        return self.labels.get(str(mid), '') if mid and str(mid) not in self.hidden else ''

    def utc(self, jour, decalage=0):
        return datetime.combine(jour + timedelta(days=decalage), dtime.min, self.tz).astimezone(timezone.utc).isoformat()

    def heure(self, stamp):
        moment = datetime.fromisoformat(str(stamp).replace('Z', '+00:00'))
        moment = moment if moment.tzinfo else moment.replace(tzinfo=timezone.utc)
        local = moment.astimezone(self.tz)
        heure = T('heure.journee') if local.time() == dtime.min else T('heure', h=local.strftime('%H'), m=local.strftime('%M'))
        if local.date() == self.jour:
            return heure
        return T('heure.demain', heure=heure) if local.date() == self.jour + timedelta(days=1) else local.strftime('%d/%m ') + heure


def _r(nombre, phrase, urgent='', href='', etat='ok', titre=''):
    return {'nombre': nombre, 'phrase': phrase, 'urgent': urgent, 'titre': titre or urgent, 'href': href, 'etat': etat if (nombre or etat != 'ok') else 'vide'}


# ------------------------------------------------------------------------------------------------ rubriques
def agenda(c):
    """Événements d'aujourd'hui et de demain encore à venir ou en cours (agenda synchronisé et événements d'AxiorHub)."""
    if not _table(c.desk, 'calendar_cache'):
        return _r(0, T('agenda.inconnu'), href=c.prefix + '/planning?vue=agenda')
    clause, params = _dans(c.hidden, 'matter')
    if c.hidden:   # 5.6.97 (lot proteger) : un rapprochement ambigu qui touche un dossier masqué ne compte pas (titre souvent nominatif)
        from .acces5697 import Perimetre
        candidats, extra = Perimetre(None, c.hidden).sql_json('matter_candidates')
        clause, params = clause + ' AND ' + candidats, params + list(extra)
    rows = c.desk.db.execute('SELECT title,starts,ends,matter FROM calendar_cache WHERE starts<? AND ends>?%s ORDER BY starts LIMIT 60' % clause,
                             [c.utc(c.jour, 2), c.now.astimezone(timezone.utc).isoformat()] + params).fetchall()
    demain = c.utc(c.jour, 1)
    auj = sum(1 for r in rows if r['starts'] < demain)
    n = len(rows)
    href = c.prefix + '/planning?vue=agenda'
    if not n:
        return _r(0, T('agenda.rien'), href=href)
    r = rows[0]
    nom = ' — '.join(x for x in (_court(r['title'] or T('evenement')), c.dossier(r['matter'])) if x)
    complet = ' — '.join(x for x in (r['title'] or T('evenement'), c.dossier(r['matter'])) if x)
    return _r(n, T('agenda.compte', auj=auj, dem=n - auj), '%s : %s' % (c.heure(r['starts']), nom), href, titre='%s : %s' % (c.heure(r['starts']), complet))


def taches(c):
    """Tâches en retard ou dues aujourd'hui : les miennes et celles sans responsable (registre local et tâches Nextcloud)."""
    from . import taches5688
    mine = taches5688.mine(c.desk, c.owner, 'tout', c.hidden)
    jour = c.jour.isoformat()
    dues = sorted((t for t in mine if t.get('responsable') in (c.owner, '') and str(t.get('due') or '')[:10] and str(t['due'])[:10] <= jour), key=lambda t: str(t['due']))
    href = c.prefix + '/mes-taches'
    if not dues:
        return _r(0, T('taches.rien'), href=href)
    retard = sum(1 for t in dues if str(t['due'])[:10] < jour)
    t = dues[0]
    quand = T('taches.en_retard') if str(t['due'])[:10] < jour else T('taches.aujourdhui')
    nom = ' — '.join(x for x in (_court(t.get('title') or T('tache')), c.dossier(t.get('matter'))) if x)
    return _r(len(dues), T('taches.compte', retard=retard, auj=len(dues) - retard), '%s (%s)' % (nom, quand), href,
              titre='%s (%s)' % (' — '.join(x for x in (t.get('title') or T('tache'), c.dossier(t.get('matter'))) if x), quand))


def courriels(c):
    """Courriels qui attendent une action (à traiter, correspondant ou dossier à confirmer) ; « nouveaux » : arrivés depuis 24 h."""
    clause, params = _dans(c.hidden, 'matter')
    base = "FROM work_items WHERE state IN ('needs_action','needs_confirmation')" + clause
    href = c.prefix + '/courriels?vue=boite'
    depuis = (c.now - timedelta(hours=24)).astimezone(timezone.utc).isoformat()
    if c.hidden:   # 5.6.97 (lot proteger, B01) : chaque message contrôlé sur ses propres rattachements AVANT le total et le plus récent
        from .acces5697 import visible
        from .messages5697 import dossiers_par_cle
        lignes = c.desk.db.execute('SELECT mail_key,subject,matter,received ' + base + ' ORDER BY received DESC', params).fetchall()
        associations = dossiers_par_cle(c.desk, [x['mail_key'] for x in lignes])
        lignes = [x for x in lignes if visible(associations.get(x['mail_key'], frozenset()), c.hidden)]
        n, nouveaux, r = len(lignes), sum(1 for x in lignes if str(x['received'] or '') >= depuis), (lignes[0] if lignes else None)
        if not n:
            return _r(0, T('courriels.rien'), href=href)
    else:
        n = c.desk.db.execute('SELECT COUNT(*) ' + base, params).fetchone()[0]
        if not n:
            return _r(0, T('courriels.rien'), href=href)
        nouveaux = c.desk.db.execute('SELECT COUNT(*) ' + base + ' AND received>=?', params + [depuis]).fetchone()[0]
        r = c.desk.db.execute('SELECT subject,matter,received ' + base + ' ORDER BY received DESC LIMIT 1', params).fetchone()
    nom = ' — '.join(x for x in (_court(r['subject'] or T('sans_objet')), c.dossier(r['matter'])) if x)
    return _r(n, T('courriels.nouveaux', n=nouveaux), T('plus_recent', nom=nom), href)


def _registre(c, courriel):
    from . import livrables5689 as liv
    from .livrables5689 import droits_requete
    if not _table(c.desk, 'prepared_items5689'):
        return None
    _, droits = droits_requete()
    where, params = liv._where(c.hidden, droits)
    where += " AND review_state='to_review' AND production_state IN ('published','verified','preparing')" if courriel else \
        " AND review_state='to_review' AND production_state IN ('published','verified') AND path<>''"
    where += " AND kind='courriel'" if courriel else " AND kind<>'courriel'"
    return where, params


def _sources_en_panne(c):
    try:
        return [r['source'] for r in c.desk.db.execute("SELECT source FROM prepared_sources5689 WHERE state='unavailable'")]
    except Exception:   # noqa: BLE001 — l'état des sources est une précision, pas une condition
        return []


def reponses(c):
    """Réponses préparées (courriels) en attente de votre décision, au registre commun des documents préparés."""
    found = _registre(c, True)
    href = c.prefix + '/livrables?' + urlencode({'nature': 'courriel', 'revue': 'to_review'})
    if found is None:
        return _r(0, T('reponses.aucune'), href=href)
    where, params = found
    n = c.desk.db.execute('SELECT COUNT(*) FROM prepared_items5689' + where, params).fetchone()[0]
    panne = 'messagerie' in _sources_en_panne(c) or 'reponses5671' in _sources_en_panne(c)
    if not n:
        return _r(0, T('reponses.rien'), href=href, etat='partiel' if panne else 'ok')
    choisir = c.desk.db.execute('SELECT COUNT(*) FROM prepared_items5689' + where + " AND deposit_state='a_decider'", params).fetchone()[0]
    r = c.desk.db.execute('SELECT title,matter_id FROM prepared_items5689' + where + ' ORDER BY source_at DESC LIMIT 1', params).fetchone()
    nom = ' — '.join(x for x in (_court(r['title']), c.dossier(r['matter_id'])) if x)
    return _r(n, T('reponses.choisir', n=choisir) if choisir else T('reponses.relire'), T('plus_recente', nom=nom), href, 'partiel' if panne else 'ok')


def documents(c):
    """Documents préparés prêts à relire (30 derniers jours ; les plus anciens sont comptés à part)."""
    found = _registre(c, False)
    href = c.prefix + '/livrables?' + urlencode({'revue': 'to_review'})
    if found is None:
        return _r(0, T('documents.aucun'), href=href)
    where, params = found
    limite = (c.now - timedelta(days=RECENCE_JOURS)).astimezone(timezone.utc).isoformat()
    n = c.desk.db.execute('SELECT COUNT(*) FROM prepared_items5689' + where + ' AND source_at>=?', params + [limite]).fetchone()[0]
    anciens = c.desk.db.execute('SELECT COUNT(*) FROM prepared_items5689' + where + ' AND source_at<?', params + [limite]).fetchone()[0]
    pannes = [s for s in _sources_en_panne(c) if s not in ('messagerie', 'reponses5671')]
    etat = 'partiel' if pannes else 'ok'
    if not n:
        return _r(0, T('documents.anciens_seuls', n=anciens) if anciens else T('documents.rien'), href=href, etat=etat)
    r = c.desk.db.execute('SELECT title,matter_id FROM prepared_items5689' + where + ' AND source_at>=? ORDER BY source_at DESC LIMIT 1', params + [limite]).fetchone()
    nom = ' — '.join(x for x in (_court(r['title']), c.dossier(r['matter_id'])) if x)
    return _r(n, T('documents.prets') + (T('documents.anciens', n=anciens) if anciens else ''), T('plus_recent', nom=nom), href, etat)


def avis(c):
    """Échéances et prescriptions proches, veille juridique non lue, annonces de la veille des sociétés, rappels non lus.
    Chaque source est lue à part : une source illisible rend la rubrique « partielle », jamais un zéro."""
    parts, pannes, proches = [], [], []
    jour = c.jour
    clause, params = _dans(c.hidden, 'matter')
    n_ech = 0
    for table, kind, jours, page in (('deadlines450', 'echeance', ECHEANCES_JOURS, '/echeances'), ('limitation500', 'prescription', PRESCRIPTION_JOURS, '/prescriptions')):
        if not _table(c.desk, table):
            continue
        try:
            # 5.6.96 (D02) : une détection non confirmée n'est pas une échéance (les prescriptions gardent leur suivi propre)
            statuts = "('confirmee','manuel')" if kind == 'echeance' else "('a_confirmer','confirmee','manuel')"
            rows = c.desk.db.execute("SELECT matter,due FROM %s WHERE status IN %s AND due<>'' AND substr(due,1,10)<=?%s ORDER BY due LIMIT 50" % (table, statuts, clause),
                                     [(jour + timedelta(days=jours)).isoformat()] + params).fetchall()
        except Exception:   # noqa: BLE001
            pannes.append(T('src.%ss' % kind))
            continue
        n_ech += len(rows)
        proches += [(str(r['due'])[:10], kind, r['matter'], page) for r in rows]
    if n_ech:
        parts.append(T('avis.echeances', n=n_ech))
    n_veille, titre_veille = 0, ''
    if _table(c.desk, 'veille_items5632'):
        try:
            n_veille = c.desk.db.execute("SELECT COUNT(*) FROM veille_items5632 WHERE owner=? AND status='new'", (c.owner,)).fetchone()[0]
            if n_veille:
                row = c.desk.db.execute("SELECT title FROM veille_items5632 WHERE owner=? AND status='new' ORDER BY COALESCE(NULLIF(published,''),NULLIF(decided,''),consulted) DESC LIMIT 1",
                                        (c.owner,)).fetchone()
                titre_veille = row['title'] if row else ''
                parts.append(T('avis.veille', n=n_veille))
        except Exception:   # noqa: BLE001
            pannes.append(T('src.veille'))
    n_soc = 0
    if _table(c.desk, 'annonces5688'):
        try:
            from .crm5688 import alertes
            n_soc = len(alertes(c.desk, limite=100))
            if n_soc:
                parts.append(T('avis.societes', n=n_soc))
        except Exception:   # noqa: BLE001
            pannes.append(T('src.societes'))
    n_rap = 0
    if _table(c.desk, 'rappels5688'):
        try:
            from .rappels5688 import unread
            n_rap = len(unread(c.desk, c.owner, c.hidden, 100))
            if n_rap:
                parts.append(T('avis.rappels', n=n_rap))
        except Exception:   # noqa: BLE001
            pannes.append(T('src.rappels'))
    total = n_ech + n_veille + n_soc + n_rap
    etat = 'partiel' if pannes else 'ok'
    suite = T('avis.illisible', sources=', '.join(pannes)) if pannes else ''
    if not total:
        return _r(0, T('avis.rien') + suite, href=c.prefix + '/echeances', etat='panne' if pannes and len(pannes) >= 4 else etat)
    if proches:
        due, kind, mid, page = sorted(proches)[0]
        ecart = (datetime.fromisoformat(due).date() - jour).days
        quand = T('quand.depassee') if ecart < 0 else (T('quand.aujourdhui') if ecart == 0 else (T('quand.demain') if ecart == 1 else T('quand.dans', n=ecart)))
        urgent = '%s %s%s' % (T('avis.' + kind), quand, (' — ' + c.dossier(mid)) if c.dossier(mid) else '')
        href = c.prefix + page
    else:
        urgent = T('avis.veille_titre', titre=_court(titre_veille)) if titre_veille else ''
        href = c.prefix + ('/veille' if n_veille else ('/contacts-crm?vue=veille' if n_soc else '/mes-taches'))
    return _r(total, ', '.join(parts) + suite, urgent, href, etat)


_CACHE_STRATEGIE = {}                 # (base, compte, rôle, dossiers masqués) -> (instant, révision, points) ; mémoire du processus, bornée


def _revision_strategie(desk):
    """Révision bon marché des décisions : une décision prise (file « À décider ») ou un nouveau changement de montant invalide la mémoire."""
    rev = [tuple(desk.db.execute("SELECT COUNT(*), COALESCE(MAX(at),'') FROM d51_queue").fetchone())] if _table(desk, 'd51_queue') else []
    if _table(desk, 'd51_changes'):
        rev.append(tuple(desk.db.execute("SELECT COALESCE(MAX(id),0), COALESCE(SUM(acknowledged),0) FROM d51_changes").fetchone()))
    return json.dumps(rev, default=str)


def _points_decider(c):
    """Points « À décider » d'Aujourd'hui, par le service de la file lui-même (mêmes clés, mêmes révisions, mêmes décisions déjà prises),
    sans la tenue des fiches (CURATION, traitée depuis chaque dossier). Seuls les dossiers qui portent un point de décision propre au dossier
    (changement de montant à examiner) sont parcourus : les autres ne pourraient fournir que des points de tenue des fiches, écartés ici.
    Le résultat est gardé ``STRATEGIE_TTL`` secondes, ou jusqu'à la prochaine décision prise dans la file."""
    from . import file5651
    cle = (str(c.desk.c.get('state_dir')), c.owner, c.role, c.hidden)
    revision = _revision_strategie(c.desk)
    deja = _CACHE_STRATEGIE.get(cle)
    if deja and deja[1] == revision and time.monotonic() - deja[0] < STRATEGIE_TTL:
        return deja[2]
    utiles = set()
    if _table(c.desk, 'd51_changes'):
        utiles = {str(r[0]) for r in c.desk.db.execute("SELECT DISTINCT matter FROM d51_changes WHERE kind='montant_modifie' AND acknowledged=0 AND technical=0")}
    ecartes = frozenset(c.hidden | (set(c.labels) - utiles))
    points = [it for it in file5651.items(c.desk, c.prefix, None, 'decider', ecartes, actor=c.owner, role=c.role)
              if it['class'] not in file5651.CURATION and str(it.get('matter') or '') not in c.hidden]
    if len(_CACHE_STRATEGIE) > 64:
        _CACHE_STRATEGIE.clear()
    _CACHE_STRATEGIE[cle] = (time.monotonic(), revision, points)
    return points


def strategie(c):
    """Points de stratégie ou de décision en attente, tels qu'ils existent déjà : la file « À décider » (sans la tenue des fiches, traitée
    depuis chaque dossier) et les analyses stratégiques proposées non validées. Aucun point n'est inventé ni résumé par un modèle."""
    from . import file5651
    items = _points_decider(c)
    analyses = []
    if _table(c.desk, 'strategy_analyses'):
        clause, params = _dans(c.hidden, 's.matter')
        analyses = c.desk.db.execute("SELECT s.matter FROM strategy_analyses s WHERE s.status IN ('proposed','incomplete') AND s.version=(SELECT MAX(version) FROM strategy_analyses x WHERE x.matter=s.matter)%s LIMIT 50"
                                     % clause, params).fetchall()
    n = len(items) + len(analyses)
    href = c.prefix + '/aujourdhui?' + urlencode({'file': 'decider'}) + '#c5656-file'
    if not n:
        return _r(0, T('strategie.rien'), href=href)
    dossiers = {str(it.get('matter') or '') for it in items if it.get('matter')} | {str(a['matter']) for a in analyses}
    if items:
        it = items[0]
        cls = file5651.CLASSES.get(it['class'], '')
        urgent = '%s : %s%s' % (cls, _court(it.get('title') or ''), (' — ' + it['matter_name']) if it.get('matter') and it.get('matter_name') else '')
        titre = '%s : %s%s' % (cls, it.get('title') or '', (' — ' + it['matter_name']) if it.get('matter') and it.get('matter_name') else '')
    else:
        urgent = titre = T('strategie.analyse', dossier=c.dossier(analyses[0]['matter']))
        href = c.prefix + '/dossier?' + urlencode({'id': analyses[0]['matter'], 'vue': 'travail'}) + '#d79-strategie'
    return _r(n, T('strategie.dossiers', n=len(dossiers)) if dossiers else T('strategie.cabinet'), urgent, href, titre=titre)


def agent(c):
    """Ce que fait l'agent : pause générale, travail en cours (nommé, sauf dossier réservé), travaux en attente et suspendus."""
    from .travaux5689 import compteurs
    pause = bool(c.desk.settings('missions567:pause', False))
    n = compteurs(c.desk)
    if c.hidden:   # 5.6.97 (lot proteger) : les travaux d'un dossier masqué ne changent aucun compteur de cette personne
        clause, params = _dans(c.hidden, "COALESCE(json_extract(CASE WHEN json_valid(args) THEN args ELSE '{}' END,'$.matter'),'')")
        par = dict(c.desk.db.execute("SELECT status,COUNT(*) FROM jobs WHERE status IN ('running','cancel_requested','pending','suspended')" + clause +
                                     ' GROUP BY status', params).fetchall())
        n = {**n, 'en_cours': par.get('running', 0) + par.get('cancel_requested', 0), 'attente_calcul': par.get('pending', 0), 'suspendu': par.get('suspended', 0)}
    href = '#c530-feed'
    morceaux = [T('agent.en_cours', n=n['en_cours']), T('agent.attente', n=n['attente_calcul'])]
    if n['suspendu']:
        morceaux.append(T('agent.suspendus', n=n['suspendu']))
    phrase = ((T('agent.pause') + ' · ') if pause else '') + ', '.join(morceaux)
    urgent = ''
    if n['en_cours']:
        from .cockpit530 import MAIL_KINDS, NOISE
        for r in c.desk.db.execute("SELECT kind,args FROM jobs WHERE status IN ('running','cancel_requested') ORDER BY id LIMIT 200"):
            if r['kind'] in NOISE:
                continue
            try:
                args = json.loads(r['args'] or '{}')
            except ValueError:
                args = {}
            mid = str(args.get('matter') or '') if isinstance(args, dict) else ''
            if mid in c.hidden:   # 5.6.97 (lot proteger) : ni nommé ni signalé ; le travail visible suivant est nommé
                continue
            from .web import JOB_LABELS
            libelle = T('agent.courriels') if r['kind'] in MAIL_KINDS else JOB_LABELS.get(r['kind'], T('agent.travail'))
            urgent = T('agent.en_cours_de', quoi=' — '.join(x for x in (libelle, c.dossier(mid)) if x))
            break
    elif pause:
        urgent = T('agent.pause_rien')
    return _r(n['en_cours'] + n['attente_calcul'] + n['suspendu'], phrase, urgent, href, 'ok') if (pause or n['en_cours'] or n['attente_calcul'] or n['suspendu']) else \
        _r(0, T('agent.rien'), href=href)


COUTEUSES = ('strategie',)            # parcourt les modules de la file « À décider » (gardée en mémoire 5 min)
CALCULS = {'agenda': agenda, 'taches': taches, 'courriels': courriels, 'reponses': reponses, 'documents': documents, 'avis': avis, 'strategie': strategie, 'agent': agent}


# ------------------------------------------------------------------------------------------------ calcul, empreinte, rendu
def calculer(desk, owner='cabinet', role='administrateur', hidden=frozenset(), prefix='', now=None, budget_ms=BUDGET_MS):
    """Résumé de l'encadré : rubriques dans l'ordre, empreinte (ETag) et durée du calcul. Aucun appel au modèle, aucune écriture."""
    debut = time.perf_counter()
    c = Contexte(desk, owner, role, hidden, prefix, now)
    trouve = {}
    for ident in sorted(CALCULS, key=lambda k: k in COUTEUSES):     # les rubriques coûteuses en dernier : le délai ne coupe qu'elles
        if (time.perf_counter() - debut) * 1000 > budget_ms:
            trouve[ident] = _r(None, T('msg.retard'), etat='retard')
            continue
        try:
            trouve[ident] = CALCULS[ident](c)
        except Exception as ex:   # noqa: BLE001 — une source en panne s'annonce comme telle, jamais comme un zéro
            import logging
            logging.getLogger(__name__).warning('actuellement : rubrique %s illisible (%s)', ident, type(ex).__name__)
            trouve[ident] = _r(None, T('msg.panne'), etat='panne')
    out = [{'id': ident, 'libelle': T('r.' + ident), 'icone': icone, **trouve[ident]} for ident, icone in RUBRIQUES]
    heure = T('heure', h=c.now.strftime('%H'), m=c.now.strftime('%M'))
    corps = json.dumps(out, ensure_ascii=False, sort_keys=True)
    etag = hashlib.sha256(('5695|%s|%s' % (c.owner, corps)).encode('utf-8')).hexdigest()[:24]
    return {'rubriques': out, 'etag': etag, 'heure': heure, 'calcul_ms': round((time.perf_counter() - debut) * 1000, 1)}


def texte_parle(resume):
    """Texte lu par le bouton Écouter : phrases simples, une par rubrique ; les abréviations sont développées par ``voix/preparer``."""
    phrases = [T('parle.debut', heure=resume['heure'])]
    for r in resume['rubriques']:
        if r['etat'] in ('panne', 'retard'):
            phrases.append('%s : %s' % (r['libelle'], etat_libelle(r['etat']).lower() + '.'))
            continue
        nombre = ('%d, ' % r['nombre']) if r['nombre'] and not r['phrase'][:1].isdigit() else ''
        phrases.append('%s : %s%s.' % (r['libelle'], nombre, r['phrase'].rstrip('.')) + ((' ' + r['urgent'].rstrip('.') + '.') if r['urgent'] else ''))
    # « DURAND c. MARTIN » (libellé des dossiers) se dit « contre » ; le tiret long sépare, il ne se lit pas
    return re.sub(r'(?<=\s)c\.(?=\s)', 'contre', ' '.join(phrases)).replace(' — ', ', ')


def lignes_html(resume):
    """Liste des rubriques (zone remplacée à chaque rafraîchissement). Le nombre, l'icône et un libellé d'état écrit : jamais la couleur seule."""
    from .ui_components5689 import icone
    lignes = []
    for r in resume['rubriques']:
        nombre = '–' if r['nombre'] is None else str(r['nombre'])
        etat = etat_libelle(r['etat'])
        badge = ('<span class="ax95-etat" data-etat="%s">%s</span>' % (e(r['etat'], quote=True), e(etat))) if etat else ''
        urgent = ('<span class="ax95-urgent" title="%s">%s</span>' % (e(r['titre'], quote=True), e(r['urgent']))) if r['urgent'] else ''
        corps = ('<span class="ax95-icone">%s</span><span class="ax95-rubrique">%s</span><span class="ax95-nombre" aria-label="%s">%s</span>'
                 '<span class="ax95-texte"><span class="ax95-phrase">%s</span>%s%s</span>') % (
            icone(r['icone'], taille=16), e(r['libelle']), e(T('nombre_inconnu') if r['nombre'] is None else str(r['nombre']), quote=True), e(nombre), e(r['phrase']), urgent, badge)
        lien = ('<a class="ax95-ligne" href="%s">%s</a>' % (e(r['href'], quote=True), corps)) if r['href'] else '<div class="ax95-ligne">%s</div>' % corps
        lignes.append('<li data-rubrique="%s" data-etat="%s"%s>%s</li>' % (e(r['id'], quote=True), e(r['etat'], quote=True), ' data-zero="1"' if r['nombre'] == 0 else '', lien))
    return '<ul class="ax95-liste">%s</ul>' % ''.join(lignes)


PLIABLES = ('veille', 'societes', 'memoire', 'reprise')   # A03 : blocs du bas d'Aujourd'hui, repliables sans rien retirer
PLIS_CLE = 'actuellement5695:plis:'                       # + compte : blocs repliés par cette personne (ouverts par défaut)


def plis_fermes(desk, owner):
    valeur = desk.settings(PLIS_CLE + str(owner or 'cabinet'), [])
    return frozenset(b for b in (valeur if isinstance(valeur, list) else []) if b in PLIABLES)


def regler_pli(desk, owner, bloc, ouvert):
    """Choix mémorisé pour la personne : bloc replié ou ouvert (aucun autre effet ; renvoyer la même valeur ne change rien)."""
    from .common import Stop
    if bloc not in PLIABLES:
        raise Stop('bloc_inconnu')
    fermes = set(plis_fermes(desk, owner))
    (fermes.discard if ouvert else fermes.add)(bloc)
    desk.setting(PLIS_CLE + str(owner or 'cabinet'), sorted(fermes))
    return {'bloc': bloc, 'ouvert': bool(ouvert)}


def pli_html(bloc, contenu, ferme=False):
    """Bloc dans un ``<details>`` natif : le contenu est inchangé (mêmes identifiants, mêmes scripts) ; replier n'en retire rien.
    L'action écrite (Replier / Déplier) suit l'état par CSS ; le lecteur d'écran annonce l'état ouvert ou fermé du résumé."""
    from .ui_components5689 import icone
    if not contenu:
        return contenu
    return ('<details class="t95-pli" data-pli="%s" data-ouvert="%s"%s><summary class="t95-pli-titre">%s<span class="t95-pli-nom">%s</span>'
            '<span class="t95-pli-action"><span class="t95-si-ouvert">%s</span><span class="t95-si-ferme">%s</span></span></summary>'
            '<p class="t95-pli-statut" role="status"></p>%s</details>') % (
        e(bloc, quote=True), '0' if ferme else '1', '' if ferme else ' open', icone('suite', taille=16, classe='t95-chevron'), e(T('pli.' + bloc)),
        e(T('pli.replier')), e(T('pli.deplier')), contenu)


def auto_actif(desk, owner):
    return not bool(desk.settings(AUTO_CLE + str(owner or 'cabinet'), False))


def regler_auto(desk, owner, actif):
    """Choix mémorisé pour la personne : la mise à jour automatique de l'encadré est arrêtée ou reprise (aucun autre effet)."""
    desk.setting(AUTO_CLE + str(owner or 'cabinet'), not bool(actif))
    return {'auto': bool(actif)}


def zone_json(resume):
    """Contenu rafraîchi : liste, texte de lecture, heure et empreinte."""
    return {'html': lignes_html(resume), 'texte': texte_parle(resume), 'heure': resume['heure'], 'etag': resume['etag']}


def bloc_html(desk, prefix, owner='cabinet', role='administrateur', hidden=None):
    """Encadré complet, rendu côté serveur (utilisable sans script) ; ``v5695-aujourdhui.js`` ajoute le rafraîchissement et la lecture."""
    from .membres5650 import viewer_hidden
    from .ui_components5689 import icone
    hidden = (viewer_hidden() or frozenset()) if hidden is None else hidden
    try:
        resume = calculer(desk, owner, role, hidden, prefix)
    except Exception as ex:   # noqa: BLE001 — Aujourd'hui s'affiche toujours
        import logging
        logging.getLogger(__name__).exception('actuellement : %s', ex)
        return ('<section class="c530-card ax95" id="ax95-actuellement" aria-labelledby="ax95-titre"><h2 id="ax95-titre">%s</h2>'
                '<p class="ax95-erreur" role="alert">%s</p></section>') % (e(T('titre')), e(T('erreur', cause=type(ex).__name__)))
    auto = auto_actif(desk, owner)
    return ('<section class="c530-card ax95" id="ax95-actuellement" aria-labelledby="ax95-titre" data-etag="%s" data-auto="%s" data-delai="%d">'
            '<div class="ax95-tete"><h2 id="ax95-titre">%s</h2><p class="ax95-heure">%s <time id="ax95-heure">%s</time>'
            '<span class="ax95-mode" id="ax95-mode">%s</span></p>'
            '<div class="ax95-actions" role="group" aria-label="%s">'
            '<button type="button" class="ax-btn ghost ax95-btn" data-ax95="actualiser">%s<span>%s</span></button>'
            '<button type="button" class="ax-btn ghost ax95-btn" data-ax95="auto" aria-pressed="%s">%s<span>%s</span></button>'
            '<button type="button" class="ax-btn ghost ax95-btn" data-ax95="ecouter">%s<span>%s</span></button>'
            '<button type="button" class="ax-btn ghost ax95-btn" data-ax95="taire" hidden>%s<span>%s</span></button></div></div>'
            '<div id="ax95-zone">%s</div><p class="ax95-statut" id="ax95-statut" role="status" aria-live="polite"></p>'
            '<script type="application/json" id="ax95-texte">%s</script>'
            '<p class="ax95-pied">%s</p>'
            '<script defer src="%s"></script></section>') % (
        e(resume['etag'], quote=True), '1' if auto else '0', RAFRAICHIR_S, e(T('titre')), e(T('maj')), e(resume['heure']),
        e(T('mode.auto') if auto else T('mode.arret')), e(T('commandes'), quote=True), icone('actualiser', taille=16), e(T('btn.actualiser')),
        'false' if auto else 'true', icone('pause' if auto else 'lancer', taille=16), e(T('btn.arreter') if auto else T('btn.reprendre')),
        icone('discussion', taille=16), e(T('btn.ecouter')), icone('stop', taille=16), e(T('btn.taire')), lignes_html(resume),
        json.dumps(texte_parle(resume), ensure_ascii=False).replace('<', '\\u003c').replace('>', '\\u003e').replace('&', '\\u0026'), e(T('pied')),
        e(prefix + '/static/v5695-aujourdhui.js', quote=True))
