"""Missions persistantes : une intention, les producteurs existants, un résultat prouvé.

Pas de second moteur IA. Les missions référencent les jobs, conversations et
documents du cabinet. Elles n'autorisent jamais un envoi, une signature ou une
suppression. Une commande répétée avec le même identifiant ne crée pas de travail.
"""
import contextvars
import hashlib
import json
import re
import secrets
import os
import time
import uuid
from datetime import datetime, timezone
from pathlib import Path
import sqlite3
from urllib.parse import urlencode

from .common import Stop, clean_path, fold, load_matters, matter_display, under

SCHEMA = '''CREATE TABLE IF NOT EXISTS missions_v567(
 id TEXT PRIMARY KEY, owner TEXT NOT NULL, request_key TEXT NOT NULL,
 fingerprint TEXT NOT NULL, instruction TEXT NOT NULL, matter TEXT NOT NULL,
 kind TEXT NOT NULL, state TEXT NOT NULL, context TEXT NOT NULL, plan TEXT NOT NULL,
 exceptions TEXT NOT NULL, job_id INTEGER, ref TEXT NOT NULL DEFAULT '',
 created TEXT NOT NULL, updated TEXT NOT NULL, UNIQUE(owner,request_key));
 CREATE INDEX IF NOT EXISTS missions567_owner ON missions_v567(owner,updated);
 CREATE TABLE IF NOT EXISTS mission_events_v567(
 id INTEGER PRIMARY KEY, mission TEXT NOT NULL, at TEXT NOT NULL,
 kind TEXT NOT NULL, detail TEXT NOT NULL);
'''
ACTIVE = {'creating', 'queued', 'running', 'cancel_requested'}
LABELS = {'creating': 'Demande enregistrée', 'queued': 'En attente', 'running': 'Préparation en cours',
          'suggested':'Plan proposé, préparation non lancée',
          'cancel_requested': 'Arrêt demandé', 'paused': 'Suspendue', 'decision': 'Une précision est nécessaire',
          'prepared': 'Projet préparé', 'verified': 'Dépôt vérifié', 'answered': 'Réponse disponible',
          'abstained': 'Aucune production nécessaire', 'error': 'Traitement interrompu', 'cancelled': 'Annulée',
          'suspended': 'Suspendue', 'interrupted': 'Interrompue · reprendre'}   # 5.6.97 (B09, B10)
# 5.6.97 (B09) : ordre de lancement écrit dans la même transaction que la mission et son reçu, puis traité sous bail ; une clé par mission et
# par phase (lancement, précision, reprise-n). Un ordre resté sans suite (service coupé après la validation) est repris par le superviseur ou
# à la lecture de la mission ; un travail déjà créé pour la mission est rattaché, jamais recréé.
ORDRES = '''CREATE TABLE IF NOT EXISTS missions_ordres5697(
 id TEXT PRIMARY KEY, mission TEXT NOT NULL, phase TEXT NOT NULL, etat TEXT NOT NULL, bail REAL NOT NULL DEFAULT 0,
 tentatives INTEGER NOT NULL DEFAULT 0, job_id INTEGER, erreur TEXT NOT NULL DEFAULT '', cree REAL NOT NULL, maj TEXT NOT NULL, UNIQUE(mission, phase));
 CREATE INDEX IF NOT EXISTS missions_ordres5697_etat ON missions_ordres5697(etat, cree);
'''
BAIL_SECONDES = 90          # durée pendant laquelle un lancement en cours n'est pas repris par un autre
RELANCE_SECONDES = 15       # un ordre jamais pris depuis ce délai est repris (le lancement suit normalement la validation dans la même requête)
INTERRUPTION_SECONDES = 120  # mission ancienne restée « Demande enregistrée » sans ordre ni travail : « Interrompue · reprendre »
TENTATIVES_MAX = 3
_horloge = time.time
# 5.6.97 : états du panneau, du clic jusqu'au résultat (« Envoi en cours » et « Refusée » avant tout reçu sont posés par le navigateur)
PANNEAU = {'creating': ('recue', 'Reçue'), 'queued': ('attente', 'En attente de calcul'), 'running': ('en_cours', 'En cours'),
           'cancel_requested': ('en_cours', 'Arrêt demandé'), 'answered': ('reponse_prete', 'Réponse prête'), 'prepared': ('projet_a_relire', 'Projet à relire'),
           'verified': ('projet_a_relire', 'Projet à relire · dépôt vérifié'), 'decision': ('choix_requis', 'Choix requis'),
           'suspended': ('suspendue', 'Suspendue'), 'paused': ('suspendue', 'Suspendue'), 'error': ('interrompue', 'Interrompue'),
           'interrupted': ('interrompue', 'Interrompue'), 'cancelled': ('annulee', 'Annulée'), 'abstained': ('sans_production', 'Aucune production nécessaire'),
           'suggested': ('plan_propose', 'Plan proposé')}
REFUS = ('autorisation_mission_revoquee', 'autorisation_mission_absente', 'autorisation_mission_non_verifiable', 'depot_brouillon_role_insuffisant',
         'role_insuffisant', 'dossier_reserve', 'dossier_decline_conflit', 'conflit_a_examiner', 'mission_annulee')
DOSSIER_A_CHOISIR = ('dossier_requis', 'dossier_ambigu', 'changement_dossier')


def ensure_schema(desk):
    desk.db.executescript(SCHEMA)
    desk.db.execute('CREATE TABLE IF NOT EXISTS missions_hidden5636(id TEXT PRIMARY KEY, owner TEXT NOT NULL, at TEXT NOT NULL)')   # 5.6.36
    desk.db.executescript(ORDRES)   # 5.6.97 (B09)


def auth_root():
    return Path(os.environ.get('AXIORHUB_AUTH_STATE') or (os.environ.get('AXIORHUB_DATA_DIR', '/data') + '/auth'))


def owner_for(login):
    """5.6.37 : propriétaire d'une mission demandée hors de l'interface (application Nextcloud).

    Avec comptes propres (mode Docker), la mission appartient au compte relié ; avec l'authentification du serveur,
    elle appartient au cabinet, comme celles de l'interface (sinon check_authority la refuserait)."""
    login = str(login or '')
    if login and (auth_root() / 'users.sqlite3').is_file():
        return login
    return 'cabinet'


def check_authority(desk, args):
    """Revalide la mission immédiatement avant un effet, même après une longue inférence."""
    ident = str(args.get('mission_id') or '')
    if not ident:
        desk.mission_owner567 = 'cabinet'
        return
    if desk.settings('missions567:pause', False):
        raise Stop('preparation_suspendue')
    row = desk.db.execute('SELECT m.owner,m.kind,m.state,j.status AS job_status FROM missions_v567 m LEFT JOIN jobs j ON j.id=m.job_id WHERE m.id=?', (ident,)).fetchone()
    if not row:
        raise Stop('autorisation_mission_absente')
    desk.mission_owner567 = row['owner']
    _limit_ai(desk, row['owner'])
    if row['state'] in ('cancelled', 'cancel_requested'):   # 5.6.34 (B16) : vérifié avant chaque effet
        raise Stop('mission_annulee')
    if row['state'] == 'paused' or row['job_status'] in ('cancelled','cancel_requested'):
        raise Stop('preparation_suspendue')
    from .connector_readiness5689 import garde_mission
    garde_mission(desk, row['kind'])   # 5.6.89 : capacité révoquée ou refusée : seules les missions qui en dépendent s'arrêtent, relu avant chaque effet
    if row['owner'] == 'cabinet':
        return                         # installation avec authentification serveur
    root = auth_root()
    if not (root / 'users.sqlite3').is_file():
        raise Stop('autorisation_mission_non_verifiable')
    db = sqlite3.connect('file:' + str(root / 'users.sqlite3') + '?mode=ro', uri=True, timeout=10)
    try:
        member = db.execute('SELECT active,role FROM users WHERE email=?', (row['owner'],)).fetchone()
    finally:
        db.close()
    if not member or not member[0] or member[1] not in ('administrateur', 'avocat', 'assistant'):
        raise Stop('autorisation_mission_revoquee')
    if member[1] == 'assistant' and row['kind'] == 'mail':
        raise Stop('depot_brouillon_role_insuffisant')


def _limit_ai(desk, owner):
    """5.6.88 : une mission lit pour son propriétaire : ses droits bornent « Ce que l'IA peut voir » (registre illisible : tout fermé
    de ce que les droits peuvent fermer, jamais ouvert)."""
    from . import membres5650
    membres5650.set_author(owner)
    if owner == 'cabinet':
        membres5650.set_ai_denied(())
        return
    try:
        row = desk.db.execute('SELECT role FROM membres5650 WHERE owner=?', (owner,)).fetchone()
    except sqlite3.Error:   # annuaire pas encore créé
        row = None
    try:
        membres5650.set_ai_denied(membres5650.ai_limits(desk.c['state_dir'], owner, row['role'] if row else 'avocat'))
    except Stop:
        membres5650.set_ai_denied(('factures', 'temps'))


def _event(desk, ident, kind, detail):
    _inscrire(desk, ident, kind, detail)
    desk.db.commit()
    from .live430 import emit
    emit(desk, 'mission', 'Mission : ' + LABELS.get(kind, kind), matter=str(detail.get('matter') or ''))


def _inscrire(desk, ident, kind, detail):
    """5.6.97 (B09) : événement écrit dans la transaction de l'appelant, sans validation ni notification (le reçu de la mission)."""
    desk.db.execute('INSERT INTO mission_events_v567(mission,at,kind,detail) VALUES (?,?,?,?)',
                    (ident, desk.now(), kind, json.dumps(detail, ensure_ascii=False)))


def _annoncer(desk, kind, matter):
    """Notification après validation ; une panne ne défait rien (la mission, son reçu et son ordre existent déjà)."""
    try:
        from .live430 import emit
        emit(desk, 'mission', 'Mission : ' + LABELS.get(kind, kind), matter=str(matter or ''))
    except Exception:   # noqa: BLE001
        if desk.db.in_transaction:
            desk.db.rollback()


def _ordre(desk, mission, phase):
    """Ordre de lancement inséré dans la transaction de l'appelant. ``phase`` : lancement, precision ou reprise (numérotée ici)."""
    if phase != 'lancement':
        phase += '-%d' % (desk.db.execute('SELECT COUNT(*) FROM missions_ordres5697 WHERE mission=?', (mission,)).fetchone()[0] + 1)
    ident = uuid.uuid4().hex
    desk.db.execute("INSERT INTO missions_ordres5697(id,mission,phase,etat,cree,maj) VALUES (?,?,?,'a_lancer',?,?)", (ident, mission, phase, _horloge(), desk.now()))
    return ident


def _clore_ordre(desk, ident, etat, job_id=None, erreur=''):
    desk.db.execute('UPDATE missions_ordres5697 SET etat=?,job_id=?,erreur=?,bail=0,maj=? WHERE id=?', (etat, job_id, str(erreur)[:120], desk.now(), ident))
    desk.db.commit()


def _ref_du_travail(ident, kind, args):
    """Référence de la mission d'après le travail produit pour elle (preuve de rattachement)."""
    if kind == 'docrequest520':
        return 'docreq:' + str(args.get('request') or '')
    if kind == 'assistant_answer':
        return 'ask:' + str(args.get('thread') or '')
    if kind == 'maildraft5613':
        return 'newmail:' + ident
    if kind == 'mission_conseil5689':
        return 'conseil:' + ident
    if kind == 'assistance5689':
        return 'assistance:' + ident
    return 'mail:' + str(args.get('key') or '')


def _rattacher(desk, row):
    """Un travail déjà produit pour cette mission (coupure entre la mise en file et l'écriture de la mission) est rattaché : relu, jamais recréé."""
    orphan = desk.db.execute("SELECT id,kind,args FROM jobs WHERE json_valid(args) AND json_extract(args,'$.mission_id')=? ORDER BY id LIMIT 1", (row['id'],)).fetchone()
    if not orphan:
        return False
    ref = _ref_du_travail(row['id'], orphan['kind'], json.loads(orphan['args']))
    desk.db.execute("UPDATE missions_v567 SET job_id=?,ref=?,state='queued' WHERE id=?", (orphan['id'], ref, row['id']))
    desk.db.commit()
    row.update(job_id=orphan['id'], ref=ref, state='queued')
    return True


def _role(desk, owner):
    if owner == 'cabinet':
        return 'administrateur'
    try:
        row = desk.db.execute('SELECT role FROM membres5650 WHERE owner=?', (owner,)).fetchone()
    except sqlite3.Error:
        row = None
    return row['role'] if row else 'avocat'


def _au_nom_du_proprietaire(desk, owner, fonction, *args):
    """Reprise hors de la requête d'origine : dossiers masqués et limites de l'IA du propriétaire de la mission, dans un contexte copié (la
    requête ou le superviseur qui reprend garde le sien)."""
    def dedans():
        from . import membres5650
        membres5650.set_viewer(membres5650.hidden_for(desk.c.get('state_dir'), owner, _role(desk, owner)))
        membres5650.set_viewer_role(_role(desk, owner))
        _limit_ai(desk, owner)
        return fonction(*args)
    return contextvars.copy_context().run(dedans)


def _executer_ordre(desk, ident, reprise=False):
    """Traite un ordre sous bail : un seul lanceur à la fois ; mission relue avant l'effet ; travail existant rattaché ; un ordre devenu sans
    objet (mission annulée, suspendue, déjà lancée) est clos sans effet. ``reprise`` : hors de la requête d'origine (contexte du propriétaire)."""
    t = _horloge()
    pris = desk.db.execute("UPDATE missions_ordres5697 SET etat='en_cours',bail=?,tentatives=tentatives+1,maj=? WHERE id=? AND "
                           "(etat='a_lancer' OR (etat='en_cours' AND bail<?))", (t + BAIL_SECONDES, desk.now(), ident, t)).rowcount
    desk.db.commit()
    if not pris:
        return
    ordre = desk.db.execute('SELECT * FROM missions_ordres5697 WHERE id=?', (ident,)).fetchone()
    row = desk.db.execute('SELECT * FROM missions_v567 WHERE id=?', (ordre['mission'],)).fetchone()
    if not row or row['state'] != 'creating' or row['job_id']:
        _clore_ordre(desk, ident, 'sans_objet', row['job_id'] if row else None)
        return
    row = dict(row)
    if _rattacher(desk, row):
        _clore_ordre(desk, ident, 'fait', row['job_id'])
        return
    if ordre['tentatives'] > TENTATIVES_MAX:   # lancement qui échoue à chaque reprise : l'avocat décide, rien n'est relancé en boucle
        desk.db.execute("UPDATE missions_v567 SET state='interrupted',updated=? WHERE id=? AND state='creating' AND job_id IS NULL", (desk.now(), row['id']))
        _clore_ordre(desk, ident, 'echec', None, 'tentatives_epuisees')
        return
    if reprise:
        _au_nom_du_proprietaire(desk, row['owner'], _launch, desk, row)
    else:
        _launch(desk, row)
    apres = desk.db.execute('SELECT state,job_id,exceptions FROM missions_v567 WHERE id=?', (row['id'],)).fetchone()
    _clore_ordre(desk, ident, 'fait' if apres['job_id'] else 'echec', apres['job_id'], '' if apres['job_id'] else (json.loads(apres['exceptions'] or '[]') or [{}])[0].get('code', ''))


def _reserver(desk, mission, sql, valeurs, phase):
    """Passage à « Demande enregistrée » et ordre de lancement dans une seule transaction ; rien si un autre clic a déjà pris la mission."""
    desk.db.execute('BEGIN IMMEDIATE')
    try:
        if not desk.db.execute(sql, valeurs).rowcount:
            desk.db.commit()
            return ''
        ordre = _ordre(desk, mission, phase)
        desk.db.commit()
        return ordre
    except BaseException:
        desk.db.rollback()
        raise


def reprendre_ordres(desk, mission=None, limite=10, immediat=False):
    """5.6.97 (B09) : ordres restés sans suite (jamais pris depuis ``RELANCE_SECONDES``, ou bail échu) : repris, au plus ``limite`` à la fois.
    ``immediat`` : la même demande est rejouée par son auteur : un ordre jamais pris est traité sans attendre (la prise est atomique, un seul
    lanceur gagne). Rend le nombre d'ordres traités. Aucune exception ne remonte : l'ordre reste en place pour un prochain passage."""
    ensure_schema(desk)
    t = _horloge()
    sql = "SELECT id FROM missions_ordres5697 WHERE ((etat='a_lancer' AND cree<?) OR (etat='en_cours' AND bail<?))"
    valeurs = [t + 1 if immediat else t - RELANCE_SECONDES, t]
    if mission:
        sql, valeurs = sql + ' AND mission=?', valeurs + [mission]
    traites = 0
    for (ident,) in desk.db.execute(sql + ' ORDER BY cree LIMIT ?', valeurs + [limite]).fetchall():
        try:
            _executer_ordre(desk, ident, reprise=True)
            traites += 1
        except Exception:   # noqa: BLE001 — bail conservé : repris au passage suivant, au plus TENTATIVES_MAX fois
            if desk.db.in_transaction:
                desk.db.rollback()
    return traites


def superviser_ordres(c, desk=None):
    """Appelé par le superviseur du travailleur (toutes les 30 s) : aucune IA appelée, aucune transaction gardée ouverte. Sans ordre en
    souffrance, rien n'est ouvert ni écrit (lecture seule de la base)."""
    if desk is None:
        chemin = Path(c['state_dir']) / 'desk.sqlite3'
        if not chemin.is_file():
            return None
        base = sqlite3.connect(chemin.resolve().as_uri() + '?mode=ro', uri=True, timeout=2)
        try:
            attente = base.execute("SELECT COUNT(*) FROM missions_ordres5697 WHERE etat IN ('a_lancer','en_cours')").fetchone()[0]
        except sqlite3.Error:
            attente = 0
        finally:
            base.close()
        if not attente:
            return None
        from .desk import Desk
        desk = Desk(c)
    reprendre_ordres(desk)
    return desk


def _interrompre_si_orpheline(desk, row):
    """Mission d'avant la 5.6.97 restée « Demande enregistrée » sans travail ni ordre : elle n'a jamais démarré. Elle passe à « Interrompue ·
    reprendre » (aucun effet rejoué, aucune donnée certifiée) ; l'avocat la relance d'un clic."""
    if row['state'] != 'creating' or row['job_id']:
        return
    if desk.db.execute('SELECT 1 FROM missions_ordres5697 WHERE mission=? LIMIT 1', (row['id'],)).fetchone():
        return
    try:
        cree = datetime.fromisoformat(str(row['created']).replace('Z', '+00:00'))
        cree = cree if cree.tzinfo else cree.replace(tzinfo=timezone.utc)
    except ValueError:
        cree = datetime.min.replace(tzinfo=timezone.utc)
    if (datetime.now(timezone.utc) - cree).total_seconds() < INTERRUPTION_SECONDES:
        return
    message = [{'code': 'mission_interrompue', 'message': 'La préparation n’a jamais démarré (service interrompu). Rien n’a été produit ni envoyé : reprenez-la.'}]
    if desk.db.execute("UPDATE missions_v567 SET state='interrupted',exceptions=?,updated=? WHERE id=? AND state='creating' AND job_id IS NULL",
                       (json.dumps(message, ensure_ascii=False), desk.now(), row['id'])).rowcount:
        _inscrire(desk, row['id'], 'interrupted', {'matter': row['matter'], 'motif': 'sans_travail_ni_ordre'})
    desk.db.commit()
    row.update(state='interrupted', exceptions=json.dumps(message, ensure_ascii=False))


def _row(desk, ident, owner, admin=False):
    if not re.fullmatch(r'[a-f0-9]{32}', str(ident)):
        raise Stop('mission_invalide')
    ensure_schema(desk)
    row = desk.db.execute('SELECT * FROM missions_v567 WHERE id=?', (ident,)).fetchone()
    if not row or (row['owner'] != owner and not admin):
        raise Stop('mission_absente')
    return dict(row)


def _context(desk, data, matter, owner='cabinet'):
    raw = data.get('context') or {}
    if not isinstance(raw, dict):
        raise Stop('contexte_mission_invalide')
    parent_id = str(raw.get('mission_id') or '')
    active, recent, thread_id = '', [], ''
    if parent_id:
        previous = get(desk,parent_id,owner)
        if previous['matter'] != (matter['id'] if matter else ''):
            raise Stop('mission_precedente_autre_dossier')
        active = previous['instruction'][:300]
        if previous['result'].get('text'):
            recent = [previous['result']['text'][:4000]]
        if previous['kind']=='question' and previous['ref'].startswith('ask:'):
            thread_id = previous['ref'][4:]
    sources = raw.get('selected_documents') or []
    if not isinstance(sources, list) or len(sources) > 20:
        raise Stop('sources_mission_invalides')
    paths = []
    for source in sources:
        path = clean_path(str(source))
        if not matter or not under(path, matter['path']):
            raise Stop('source_mission_autre_dossier')
        if path not in paths:
            paths.append(path)
    attachments = data.get('attachments') or []
    from .improvements36 import MAX_ATTACHMENTS
    if not isinstance(attachments, list) or len(attachments) > MAX_ATTACHMENTS:
        raise Stop('trop_de_pieces_jointes')
    key = str(raw.get('mail_key') or '')
    if key:
        from .workspace import chat_scope
        _, selected, _ = chat_scope(desk.c, {'matter': matter['id'] if matter else '', 'key': key})
        if selected and matter and selected['id'] != matter['id']:
            raise Stop('courriel_mission_autre_dossier')
    for ident in attachments:
        from .improvements36 import attachment_source
        attachment_source(desk, str(ident), matter['id'] if matter else '', key)
    page = str(raw.get('page') or '/')
    if not re.fullmatch(r'/[A-Za-z0-9/_-]{0,160}', page):
        page = '/'
    extra = {}
    if raw.get('ui') is not None:   # 5.6.89 : contexte du panneau, revalidé sur le serveur : il n'élargit rien, le dossier reste celui de la mission
        from .ui_ai5689 import pour_mission
        vu = pour_mission(desk, raw['ui'], matter['id'] if matter else '', owner)
        for path in vu['documents']:
            path = clean_path(path)
            if not matter or not under(path, matter['path']):
                raise Stop('source_mission_autre_dossier')
            if path not in paths and len(paths) < 20:
                paths.append(path)
        extra = {'fonction': vu['fonction_libelle'], 'objets': vu['objets']}
    return {'page': page, 'selected_documents': paths, 'mail_key': key,
            'attachments': list(dict.fromkeys(str(a) for a in attachments)),
            'parent_mission':parent_id, 'active_mission':active, 'recent_results':recent, 'thread_id':thread_id,
            'due': str(data.get('due') or '')[:32], 'channel': 'voice' if data.get('channel') == 'voice' else 'text', **extra}


def carte_fil(text, reponse, data, state='answered', exceptions=(), sources=(), perimetre='general'):
    """5.6.96 (R01) : réponse immédiate dans le fil, sans mission enregistrée ni travail en file (acquiescement, météo, recherche publique à
    confirmer). Même forme qu'une mission pour les cartes du texte et de la voix ; identifiant vide : rien à relire ni à reprendre."""
    plan = {'objective': text, 'deliverable': 'Réponse dans le fil', 'intent': 'question', 'intent_reasons': ['Réponse immédiate, sans lecture de dossier ni mission.'],
            'intent_explicit': False, 'steps': ['Répondre dans le fil'], 'restrictions': ['Aucune lecture de dossier', 'Aucun envoi'], 'document_kind': '', 'new_mail': False,
            'autonomy': 'prepare', 'perimetre': perimetre}
    row = {'id': '', 'kind': 'question', 'state': state, 'label': LABELS.get(state, state), 'conversation': True, 'instruction': text, 'matter': '', 'matter_label': '',
           'exceptions': list(exceptions), 'plan': plan, 'result': {'text': reponse, 'sources': list(sources)} if state == 'answered' else {},
           'context': {'page': '/', 'channel': 'voice' if data.get('channel') == 'voice' else 'text', 'selected_documents': [], 'attachments': []},
           'job': None, 'title': text, 'subtitle': '', 'automatic': False, 'complex': False}
    from .pilote5613 import result_lines
    row['lignes'] = result_lines(row)
    row['panneau'] = etat_panneau(row)   # 5.6.97 : réponse immédiate (sans travail) : affichée selon son type
    return row


def create(desk, data, owner='cabinet'):
    text = str(data.get('instruction') or '').strip()
    # 5.6.96 (R01) : le périmètre est classé AVANT le dossier (acquiescement, question publique, lecture du cabinet, mode Général, demande
    # sur un dossier). Le dossier n'est résolu que pour une demande sur un dossier ou sur demande explicite ; une intention de production
    # choisie explicitement garde la protection actuelle (dossier_ambigu, changement_dossier).
    from . import recherche_publique5696 as public
    requested = 'analysis' if data.get('analysis_only') is True else str(data.get('intent') or 'auto')
    cadre = public.cadrer(text, data) if requested in public.INTENTIONS_OUVERTES else public.cadre_dossier(text)
    if cadre['perimetre'] == 'acquiescement':
        return carte_fil(text, public.reponse_acquiescement(text), data, perimetre='acquiescement')
    data = public.restreindre(data, cadre)
    text = str(data.get('instruction') or '').strip()
    ensure_schema(desk)
    if not 3 <= len(text) <= 12000:
        raise Stop('question_requise_12000_caracteres_maximum')
    token = str(data.get('request_key') or '')
    if not re.fullmatch(r'[A-Za-z0-9-]{16,80}', token):
        raise Stop('identifiant_requete_invalide')
    if cadre['perimetre'] == 'public':   # 5.6.96 (R03, R04) : outil du bon tour ; rien ne part sans confirmation du texte exact
        immediat = public.avant_mission(desk, cadre, owner)
        if immediat:
            state, texte, exceptions, sources = immediat
            return carte_fil(text, texte, data, state, exceptions, sources, perimetre='public')
    matters = {str(m['id']): m for m in load_matters(desk.c)}
    chosen = str(data.get('matter') or '')
    if chosen and chosen not in matters:
        raise Stop('dossier_absent')
    from .docrequest520 import find_matter, guess_kind
    named, candidates = find_matter(desk, text) if cadre['resoudre_dossier'] else (None, [])
    exceptions = []
    if chosen and named and named['id'] != chosen:
        exceptions.append({'code': 'changement_dossier', 'message': 'La demande nomme un autre dossier. Choisissez explicitement le dossier de cette mission.',
                           'candidates': [{'id': m['id'], 'label': matter_display(m)} for m in (matters[chosen], named)]})
    elif not chosen and named:
        chosen = named['id']
    elif not chosen and candidates:
        exceptions.append({'code': 'dossier_ambigu', 'message': 'Quel dossier concerne cette demande ?',
                           'candidates': [{'id': m['id'], 'label': matter_display(m)} for m in candidates]})
    ctx = _context(desk, data, matters.get(chosen), owner)
    f = fold(text)
    # 5.6.13 : l'intention (Question / Analyse / Document / Brouillon de courriel) est explicite ou classée par pilote5613,
    # jamais déduite d'un seul mot comme « contrat » ou « audience » ; une question ne crée plus de document.
    from .pilote5613 import resolve_intent, DELIVERABLES
    intent = resolve_intent(text, 'question' if cadre['perimetre'] == 'public' and requested == 'auto' else requested, ctx['mail_key'])
    if intent['intent'] == 'mission':      # 5.6.14 : mission complexe (parcours par tâches)
        from . import taches5614
        from .parcours5614 import detect
        if not chosen:
            raise Stop('dossier_requis_pour_mission_complexe')
        m = taches5614.create(desk, {'instruction': text, 'matter': chosen, 'parcours': str(data.get('parcours') or detect(text) or 'assignation'), 'request_key': token,
                                     'answers': data.get('answers') or {}, 'autonomy': data.get('autonomy') or 'prepare'}, owner)
        return complex_shim(m)
    if intent['intent'] == 'facturation':  # 5.6.14 : parcours de facturation — aperçu et données manquantes, aucune écriture sans validation
        from .facturation5614 import missing_for
        if not chosen:
            raise Stop('dossier_requis_pour_facturation')
        info = missing_for(desk, chosen, 'quote')
        return {'id': 'facturation-' + chosen, 'kind': 'facturation', 'state': 'decision' if info['missing'] else 'prepared', 'label': 'Données manquantes' if info['missing'] else 'Prêt : créer le projet ou le devis depuis Honoraires',
                'instruction': text, 'matter': chosen, 'matter_label': matter_display(matters[chosen]), 'exceptions': [{'code': 'facturation', 'message': 'À renseigner : ' + ', '.join(info['missing'])}] if info['missing'] else [],
                'plan': {'deliverable': 'Facturation Invoice Ninja', 'steps': ['Vérifier société, client facturé et tarif', 'Créer ou rattacher le projet', 'Préparer le devis en brouillon (aperçu)', 'Relire dans Invoice Ninja'], 'restrictions': ['Aucun envoi', 'Aucune conversion', 'Aucun paiement']},
                'result': {'message': 'Parcours de facturation : ' + ('données manquantes à compléter.' if info['missing'] else 'tout est prêt.'), 'open_url': '/honoraires?' + urlencode({'matter': chosen}), 'missing': info['missing']}, 'context': ctx, 'job': None}
    kind = {'question': 'question', 'analysis': 'question', 'document': 'document', 'mail': 'mail'}[intent['intent']]
    conseil = data.get('conseil5689') if isinstance(data.get('conseil5689'), dict) else None   # 5.6.89 : consultation ou revue de contrat depuis le dossier (contrat de mission dans consultation5689)
    if conseil:
        kind = 'conseil'
    assistance = data.get('assistance5689') if isinstance(data.get('assistance5689'), dict) else None   # 5.6.89 : action du panneau IA (posé par ui_ai5689.creer, retiré des demandes du navigateur)
    if assistance:
        kind = 'assistance'
    if kind == 'document' and not chosen and not exceptions:
        exceptions.append({'code': 'dossier_requis', 'message': 'Choisissez le dossier où préparer ce document.',
                           'candidates': [{'id': m['id'], 'label': matter_display(m)} for m in list(matters.values())[:100]]})
    if desk.settings('missions567:pause', False):
        exceptions.append({'code': 'preparation_suspendue', 'message': 'La préparation de nouvelles missions est suspendue par l’administrateur.'})
    plan = {'objective': text, 'deliverable': DELIVERABLES[intent['intent']],
            'intent': intent['intent'], 'intent_reasons': intent['reasons'], 'intent_explicit': intent['explicit'], 'perimetre': cadre['perimetre'],   # 5.6.96 (R01)
            'steps': ['Identifier le dossier et les sources' if cadre['perimetre'] == 'dossier' else 'Identifier le périmètre et les sources',
                      'Consulter les sources publiques autorisées' if cadre['perimetre'] == 'public' else 'Consulter les sources autorisées et l’agenda',
                      'Préparer le résultat', 'Contrôler et relire le dépôt' if kind != 'question' else 'Présenter la réponse et ses sources'],
            'restrictions': ['Aucun envoi de courriel', 'Aucun dépôt auprès d’un tiers', 'Aucune signature ni facture définitive'],
            'document_kind': 'note' if 'plaidoirie' in f or 'audience' in f else guess_kind(text),
            'new_mail': kind == 'mail' and not ctx['mail_key']}
    from .assistant567 import profile
    autonomy = str(data.get('autonomy') or ('suggest' if profile(desk,owner)['initiative']=='proposer' else 'prepare'))
    if autonomy not in ('prepare','suggest'):
        raise Stop('autonomie_mission_invalide')
    plan['autonomy']=autonomy
    if conseil:
        plan.update(deliverable=conseil['livrable'], steps=conseil['etapes'], document_kind='')
    if assistance:
        plan.update(deliverable=assistance['livrable'], steps=assistance['etapes'], document_kind='')
    canonical = json.dumps({'instruction': text, 'matter': chosen, 'kind': kind, 'context': ctx,'autonomy':autonomy}, ensure_ascii=False, sort_keys=True)
    fp = hashlib.sha256(canonical.encode()).hexdigest()
    ident, stamp = secrets.token_hex(16), desk.now()
    etat = 'decision' if exceptions else ('suggested' if autonomy=='suggest' else 'creating')
    ordre = ''
    desk.db.execute('BEGIN IMMEDIATE')
    try:
        old = desk.db.execute('SELECT id,fingerprint FROM missions_v567 WHERE owner=? AND request_key=?', (owner, token)).fetchone()
        if old:
            if old['fingerprint'] != fp:
                raise Stop('requete_reutilisee_avec_autres_donnees')
            desk.db.commit()
            reprendre_ordres(desk, old['id'], immediat=True)   # 5.6.97 (B09) : même clé rejouée après une coupure : l'ordre resté sans suite est repris, une fois
            return get(desk, old['id'], owner)
        desk.db.execute('INSERT INTO missions_v567 VALUES (?,?,?,?,?,?,?,?,?,?,?,NULL,?,?,?)',
                        (ident, owner, token, fp, text, chosen, kind, etat,
                         json.dumps(ctx, ensure_ascii=False), json.dumps(plan, ensure_ascii=False), json.dumps(exceptions, ensure_ascii=False), '', stamp, stamp))
        # 5.6.97 (B09) : mission, reçu et ordre de lancement dans la même transaction ; le lancement suit la validation
        _inscrire(desk, ident, 'decision' if exceptions else 'creating', {'matter': chosen, 'kind': kind})
        if etat == 'creating':
            ordre = _ordre(desk, ident, 'lancement')
        desk.db.commit()
    except BaseException:
        desk.db.rollback()
        raise
    _annoncer(desk, 'decision' if exceptions else 'creating', chosen)
    if ordre:
        _executer_ordre(desk, ordre)
    return get(desk, ident, owner)


def _launch(desk, row):
    ctx, plan = json.loads(row['context']), json.loads(row['plan'])
    try:
        if row['kind'] == 'conseil':
            job = desk.enqueue('mission_conseil5689', {'mission_id': row['id'], 'matter': row['matter'], 'owner': row['owner']}, priority=0)
            ref = 'conseil:' + row['id']
        elif row['kind'] == 'assistance':
            job = desk.enqueue('assistance5689', {'mission_id': row['id'], 'matter': row['matter'], 'owner': row['owner']}, priority=0)
            ref = 'assistance:' + row['id']
        elif row['kind'] == 'mail' and not ctx.get('mail_key'):
            # 5.6.13 : courriel demandé sans courriel sélectionné → brouillon neuf dans Drafts (jamais un Word par défaut)
            job = desk.enqueue('maildraft5613', {'instruction': row['instruction'], 'matter': row['matter'], 'mission_id': row['id'],
                               'attachments': ctx.get('attachments', []), 'selected_documents': ctx.get('selected_documents', [])}, priority=0)
            ref = 'newmail:' + row['id']
        elif row['kind'] == 'mail':
            job = desk.enqueue('prepare_reply', {'key': ctx['mail_key'], 'matter': row['matter'],
                               'instruction': row['instruction'], 'mission_id': row['id']}, priority=0)
            ref = 'mail:' + ctx['mail_key']
        elif row['kind'] == 'document':
            from .docrequest520 import submit
            out = submit(desk, row['instruction'], row['matter'], plan['document_kind'],
                         attachments=ctx['attachments'], mission_id=row['id'], selected_documents=ctx['selected_documents'])
            job, ref = out['job_id'], 'docreq:' + out['request']
        else:
            from .integration import submit_question
            out = submit_question(desk, row['instruction'], row['matter'], ctx['mail_key'], attachment_ids=ctx['attachments'],
                                  thread_id=ctx.get('thread_id',''), page_context={k:ctx.get(k) for k in ('page','selected_documents','active_mission','recent_results','fonction','objets')}, mission_id=row['id'],
                                  purpose='voice_conversation' if ctx.get('channel')=='voice' else 'assistant', auteur=row['owner'])
            job, ref = out['job_id'], 'ask:' + out['thread_id']
        desk.db.execute("UPDATE missions_v567 SET state='queued',job_id=?,ref=?,updated=? WHERE id=?", (job, ref, desk.now(), row['id']))
        desk.db.commit()
        _event(desk, row['id'], 'queued', {'matter': row['matter'], 'job_id': job})
    except (Stop, OSError) as error:
        desk.db.execute("UPDATE missions_v567 SET state='error',exceptions=?,updated=? WHERE id=?", 
                        (json.dumps([{'code': str(error), 'message': 'Préparation non démarrée. Consultez le motif et reprenez la mission.'}]), desk.now(), row['id']))
        desk.db.commit()


def _reponse_de(desk, thread_id, job_id):
    """Réponse liée à la question de cette mission : la réponse porte le travail de sa question (5.6.97) ; pour une réponse plus ancienne, la
    première réponse du fil après le message demandeur. Jamais la dernière réponse d'un fil où d'autres questions ont été posées depuis."""
    answer = desk.db.execute("SELECT content,sources FROM assistant_messages WHERE thread_id=? AND role='assistant' AND job_id=? ORDER BY id LIMIT 1",
                             (thread_id, job_id)).fetchone()
    if answer:
        return answer
    asked = desk.db.execute("SELECT id FROM assistant_messages WHERE thread_id=? AND role='user' AND job_id=? ORDER BY id LIMIT 1", (thread_id, job_id)).fetchone()
    if not asked:
        return None
    return desk.db.execute("SELECT content,sources FROM assistant_messages WHERE thread_id=? AND role='assistant' AND id>? AND (job_id IS NULL OR job_id=?) ORDER BY id LIMIT 1",
                           (thread_id, asked['id'], job_id)).fetchone()


def _suspension(desk, job):
    """5.6.97 (B10) : motif d'un travail suspendu et reprise attendue. Pause programmée : reprise automatique à la fin de la plage (heure locale
    du planning, « demain 7 h » quand la plage passe minuit) ; pause manuelle ou du dossier : reprise manuelle, quand la pause est levée."""
    motif = str(job['stop_reason'] or '')
    if motif == 'pause_horaire':
        from . import horaires_ia5689 as h
        now = h._utc(None)
        try:
            horaire = h.lire_horaire(h.photo(desk), now)
        except Stop:
            horaire = {'incident': 'illisible', 'pause': False, 'fin': None}
        if horaire.get('pause') and horaire.get('fin') and not horaire.get('incident'):
            zone = h._zone(horaire['politique']['timezone'])
            fin = horaire['fin'].astimezone(zone)
            quand = h._jour(fin, now.astimezone(zone)) + ' ' + h._heure(fin)
            return {'motif': 'pause_horaire', 'reprise': 'automatique', 'jusqua': horaire['fin'].isoformat(), 'libelle': 'Suspendue jusqu’à ' + quand,
                    'geste': 'Pause programmée de l’IA : la demande reprendra seule ' + quand + ', une seule fois. Rien à refaire.'}
        if horaire.get('pause'):
            return {'motif': 'pause_horaire', 'reprise': 'manuelle', 'jusqua': '', 'libelle': 'Suspendue : pause programmée sans fin prévue',
                    'geste': 'La demande reprendra quand la programmation de l’IA le permettra (Paramètres › Horaires de l’IA).'}
        return {'motif': 'pause_horaire', 'reprise': 'automatique', 'jusqua': '', 'libelle': 'Suspendue · reprise imminente',
                'geste': 'La plage de pause est terminée : la demande est remise en file sous peu, une seule fois.'}
    if motif == 'pause_dossier':
        return {'motif': 'pause_dossier', 'reprise': 'manuelle', 'jusqua': '', 'libelle': 'Suspendue : IA en pause sur ce dossier',
                'geste': 'La demande reprendra quand la pause de ce dossier sera levée. Rien à refaire.'}
    return {'motif': motif or 'pause', 'reprise': 'manuelle', 'jusqua': '', 'libelle': 'Suspendue : IA en pause',
            'geste': 'La demande reprendra quand l’IA sera relancée (« Reprendre l’IA »). Rien à refaire.'}


def etat_panneau(row):
    """5.6.97 : état explicite d'une demande pour le panneau, avec le geste utile. Un HTTP 200 seul n'annonce jamais un document prêt : « Projet à
    relire » suit l'état réel de la mission, « Réponse prête » une réponse enregistrée."""
    state = str(row.get('state') or '')
    code, libelle = PANNEAU.get(state, ('recue', row.get('label') or 'Reçue'))
    exceptions = [x for x in row.get('exceptions') or [] if isinstance(x, dict)]
    codes = {str(x.get('code') or '') for x in exceptions}
    geste = ''
    if state == 'creating':
        geste = 'Demande reçue par le serveur : la préparation démarre.'
    elif state == 'queued':
        geste = 'Calcul en file d’attente : le résultat s’affichera ici.'
    elif state == 'running':
        geste = 'Préparation en cours. Vous pouvez fermer le panneau : le travail continue.'
    elif state == 'decision':
        if codes & set(DOSSIER_A_CHOISIR):
            libelle, geste = 'Choix requis : le dossier', 'Choisissez le dossier ci-dessous puis « Lancer dans ce dossier » : la même demande repart, sans nouvelle saisie.'
        elif 'recherche_publique_a_confirmer' in codes:
            libelle, geste = 'Choix requis : confirmer la recherche', 'Rien n’est parti : confirmez la recherche publique proposée, ou reformulez.'
        elif 'preparation_suspendue' in codes:
            code, libelle, geste = 'suspendue', 'Suspendue', 'La préparation des nouvelles demandes est suspendue par l’administrateur.'
        else:
            geste = ' '.join(str(x.get('message') or '') for x in exceptions).strip() or 'Une précision est nécessaire pour démarrer.'
    elif state == 'suspended':
        s = row.get('suspension') or {}
        libelle, geste = s.get('libelle') or libelle, s.get('geste') or ''
    elif state == 'paused':
        geste = 'Suspendue à votre demande : « Reprendre » relance le même travail.'
    elif state in ('error', 'interrupted'):
        erreur = str((row.get('job') or {}).get('code') or '')
        if codes & set(REFUS) or any(c in erreur for c in REFUS):
            code, libelle, geste = 'refusee', 'Refusée', 'Les droits ou le contrôle du dossier ne permettent pas cette demande. Rien n’a été produit.'
        elif state == 'interrupted':
            libelle, geste = 'Interrompue · reprendre', 'La préparation n’a pas abouti. Rien n’a été envoyé : « Reprendre » relance la même demande.'
        else:
            geste = 'Rien n’a été envoyé : consultez le motif puis « Relancer ».'
    elif state == 'answered':
        geste = 'Lisez la réponse et ses sources ci-dessous.'
    elif state in ('prepared', 'verified'):
        geste = 'Ouvrez le projet pour le relire. Rien n’est envoyé sans vous.'
    elif state == 'suggested':
        geste = 'Plan proposé : « Démarrer la préparation » pour lancer.'
    return {'code': code, 'libelle': libelle, 'geste': geste}


def get(desk, ident, owner='cabinet', admin=False, prefix=''):
    row = _row(desk, ident, owner, admin)
    if not row['job_id'] and row['state'] in ('creating','error'):
        _rattacher(desk, row)
    if not row['job_id'] and row['state'] == 'creating':   # 5.6.97 (B09) : ordre resté sans suite repris ; mission ancienne sans ordre : interrompue
        if reprendre_ordres(desk, ident):
            row = _row(desk, ident, owner, admin)
        _interrompre_si_orpheline(desk, row)
    for key in ('context', 'plan', 'exceptions'):
        row[key] = json.loads(row[key])
    row['result'] = {}
    if row['job_id']:
        job = desk.db.execute('SELECT id,status,progress,result,stop_reason,not_before,created,started,finished FROM jobs WHERE id=?', (row['job_id'],)).fetchone()
        if job:
            result = json.loads(job['result'] or '{}')
            # 5.6.97 (B10) : un travail suspendu (pause manuelle, du dossier, programmée) est projeté « Suspendue », avec motif et reprise
            state = {'pending': 'queued', 'running': 'running', 'error': 'error', 'cancelled': 'paused', 'cancel_requested': 'cancel_requested',
                     'suspended': 'suspended'}.get(job['status'], row['state'])
            if row['state'] == 'cancelled' or (row['state'] == 'cancel_requested' and job['status'] in ('cancelled', 'error', 'done')):
                state = 'cancelled' if job['status'] != 'done' or row['state'] == 'cancelled' else state   # 5.6.34 : annulation durable
            if job['status'] == 'done' and row['state'] not in ('cancelled','cancel_requested'):
                state = 'prepared'
                if row['kind'] == 'mail' and row['ref'].startswith('newmail:'):
                    state = 'verified' if result.get('brouillon_imap') == 'verifie' else 'prepared'
                    row['result'] = {'message': result.get('message', ''), 'proof': 'Brouillon relu dans ' + str(result.get('folder', '')) if result.get('brouillon_imap') == 'verifie' else '',
                                     'subject': result.get('subject', ''), 'to_complete': result.get('to_complete', []), 'manifest': result.get('manifest', {}),
                                     'location': 'Brouillons IMAP (%s) · destinataire à renseigner' % result.get('folder', ''), 'open_url': prefix + '/courriels'}
                elif row['kind'] == 'mail':
                    state = 'verified' if result.get('brouillon_imap') == 'verifie' else ('prepared' if result.get('projet_prepare') else 'abstained')
                    row['result'] = {'message': result.get('message', ''), 'proof': result.get('brouillon_imap', ''),
                                     'open_url': prefix + '/mail?' + urlencode({'key': row['context']['mail_key']})}
                elif row['kind'] == 'document' and row['ref'].startswith('docreq:'):
                    doc = desk.db.execute('SELECT status,path,result FROM docreq520 WHERE id=?', (row['ref'][7:],)).fetchone()
                    if doc and doc['status'] == 'cree':
                        proof = json.loads(doc['result'] or '{}')
                        state = 'verified' if proof.get('readback_sha256') == proof.get('sha256') and proof.get('sha256') else 'prepared'
                        from .pilote5613 import document_checks   # 5.6.13 : trois états distincts
                        checks = document_checks(desk, doc, proof)
                        root = next((m['path'] for m in load_matters(desk.c) if m['id'] == row['matter']), '')
                        rel = doc['path'][len(root):].lstrip('/') if root and doc['path'].startswith(root) else doc['path']
                        row['result'] = {'message': 'Projet Word disponible dans le dossier.', 'path': doc['path'], 'sources': proof.get('sources', []),
                                         'to_complete': proof.get('to_complete', []), 'proof': proof.get('readback_sha256', ''),
                                         'open_url': prefix + '/documents/edit?' + urlencode({'path': doc['path']}),
                                         'location': rel, 'checks': checks, 'control': (proof.get('control') or {}).get('summary', ''),
                                         'template': (proof.get('template') or {}).get('label', ''), 'manifest': proof.get('manifest', {}),
                                         'revision_diff': proof.get('revision_diff'), 'rule_proposal': proof.get('rule_proposal'), 'notes': proof.get('notes', [])}
                        if row['result']['rule_proposal']:
                            from .pilote5613 import rule_proposal
                            current = rule_proposal(desk, row['result']['rule_proposal']['id'])
                            row['result']['rule_proposal']['state'] = current['state'] if current else 'proposed'
                    elif doc and doc['status'] == 'dossier_a_choisir':
                        state = 'decision'
                        proof = json.loads(doc['result'] or '{}')
                        if not row['exceptions']:
                            row['exceptions'] = [{'code': 'dossier_ambigu', 'message': 'Quel dossier concerne cette demande ?', 'candidates': proof.get('candidates', [])}]
                elif row['kind'] == 'conseil':
                    from .consultation5689 import resultat_mission
                    state, row['result'] = resultat_mission(desk, ident, prefix)
                elif row['kind'] == 'assistance':
                    from .ui_ai5689 import resultat_mission as resultat_assistance
                    state, row['result'] = resultat_assistance(desk, ident, prefix)
                elif row['kind'] == 'question' and row['ref'].startswith('ask:'):
                    answer = _reponse_de(desk, row['ref'][4:], job['id'])   # 5.6.97 (B09) : la réponse à SA question, pas la dernière du fil
                    if answer:
                        state = 'answered'
                        row['result'] = {'text': answer['content'], 'sources': json.loads(answer['sources'] or '[]')}
            if row['state'] == 'cancel_requested' and job['status'] in ('done','error','cancelled'):
                state = 'cancelled'
            row['job'] = {'id': job['id'], 'status': job['status'], 'progress': job['progress'] or '',
                          'error': result.get('erreur', '') if job['status'] == 'error' else '',
                          'code': str(result.get('erreur', '')) if job['status'] == 'error' else ''}   # 5.6.97 : code brut (le motif affiché est traduit)
            from .routage5682 import for_job
            row['choix_ia'] = for_job(desk.c['state_dir'], job['id'])   # 5.6.82 : modèle réel, effort, raison courte, consommation
            from .data_policy5689 import synthese_mission
            row['synthese_donnees'] = synthese_mission(desk, job['id'])   # 5.6.89 : Données utilisées, Services destinataires, Limites
            if state == 'suspended':   # 5.6.97 (B10) : projection seulement ; la mission reste « en attente » en base et reprend avec son travail
                row['suspension'] = _suspension(desk, job)
            elif state != row['state']:
                desk.db.execute('UPDATE missions_v567 SET state=?,updated=? WHERE id=?', (state, desk.now(), ident))
                desk.db.commit()
            row['state'] = state
            row['job'].update(cree=job['created'] or '', demarre=job['started'] or '', termine=job['finished'] or '')
    row['label'] = LABELS.get(row['state'], row['state'])
    row['matter_label'] = next((matter_display(m) for m in load_matters(desk.c) if m['id'] == row['matter']), '')
    # 5.6.61 : présentation — l'instruction d'une mission automatique contient la consigne de sécurité donnée au modèle ; l'avocat voit
    # l'objet (« Document nouveau ou modifié : X ») et l'action de l'agent, le motif d'échec en clair, jamais une progression périmée.
    from .file5657 import mission_text, _reason
    action, subject = mission_text(row.get('instruction', ''))
    row['title'], row['subtitle'], row['automatic'] = (subject, action, True) if subject else (row.get('instruction', ''), '', False)
    job = row.get('job')
    if isinstance(job, dict):
        if row['state'] not in ('creating', 'queued', 'running'):
            job['progress'] = ''
        if job.get('error'):
            job['error'] = _reason(job['error'])
    for x in row.get('exceptions') or []:
        if isinstance(x, dict) and x.get('message') and ' ' not in str(x['message']).strip():
            x['message'] = _reason(x['message'])
    from .pilote5613 import result_lines
    row['lignes'] = result_lines(row)   # 5.6.89 (B11) : résultat demandé, résultat préparé, dépôt vérifié
    from .ui_ai5689 import pour_carte
    row['resultat5689'] = pour_carte(desk, row)   # 5.6.89 : même contrat de résultat pour toute mission
    row['panneau'] = etat_panneau(row)   # 5.6.97 : état explicite et geste utile
    row['verifie_a'] = desk.now()
    row.pop('fingerprint', None)
    return row


def complex_shim(m):
    """Présentation d'une mission complexe dans le panneau Pilote (même carte que les missions simples)."""
    return {'id': m['id'], 'kind': 'mission5614', 'state': {'active': 'running', 'decision': 'decision', 'a_valider': 'prepared', 'validee': 'verified', 'terminee': 'prepared', 'annulee': 'paused',
                                                       'suspendue': 'paused', 'suspendue_budget': 'decision', 'bloquee': 'error', 'suggested': 'suggested'}.get(m['state'], 'running'),
            'label': m['label'], 'instruction': m['objective'], 'matter': m['matter'], 'matter_label': m['matter_label'], 'exceptions': [{'code': d['kind'], 'message': d['question']} for d in m['decisions']],
            'plan': {'deliverable': 'Mission complexe : ' + m['title'], 'steps': [t['code'] + ' ' + t['title'] + ' — ' + (t['outcome_label'] if t['run_state'] == 'terminee' else t['label']) for t in m['tasks']],
                     'restrictions': ['Aucun envoi', 'Aucune signification', 'Validation du contenu par l’avocat'], 'intent_reasons': [m['next_action']]},
            'result': {'message': m['next_action'], 'open_url': m['url'], 'location': m['deliverable'] or ''}, 'context': {}, 'job': {'id': 0, 'status': m['state'], 'progress': '%d/%d livrables acceptés' % (m['progress']['accepted'], m['progress']['required']), 'error': ''}, 'complex': True}


def listing(desk, owner='cabinet', admin=False, prefix=''):
    ensure_schema(desk)
    rows = desk.db.execute('SELECT id FROM missions_v567 WHERE id NOT IN (SELECT id FROM missions_hidden5636)' + ('' if admin else ' AND owner=?') +
                          ' ORDER BY created DESC LIMIT 40', () if admin else (owner,)).fetchall()
    return [get(desk, r['id'], owner, admin, prefix) for r in rows]


def control(desk, data, owner='cabinet', admin=False):
    row = _row(desk, str(data.get('id') or ''), owner, admin)
    current = get(desk, row['id'], owner, admin)
    row = _row(desk, row['id'], owner, admin)  # get peut avoir rattaché un job orphelin
    action = str(data.get('action') or '')
    if action == 'pause':
        if current['state'] not in ACTIVE:
            raise Stop('mission_non_active')
        if row['job_id']:
            desk.cancel_job(row['job_id'])
        else:
            desk.db.execute("UPDATE missions_v567 SET state='paused',updated=? WHERE id=?", (desk.now(), row['id']))
            desk.db.commit()
    elif action == 'resolve':
        if current['state'] != 'decision':
            raise Stop('mission_sans_question')
        matter = str(data.get('matter') or '')
        choices = {m['id'] for m in load_matters(desk.c)}
        if matter not in choices:
            raise Stop('dossier_absent')
        ctx = json.loads(row['context'])
        _context(desk, {'context': ctx, 'attachments': ctx['attachments']}, next(m for m in load_matters(desk.c) if m['id'] == matter))
        if desk.settings('missions567:pause', False):
            raise Stop('preparation_suspendue')
        # 5.6.97 (B09) : choix du dossier et ordre de lancement dans la même transaction ; la même demande repart, sans nouvelle saisie
        ordre = _reserver(desk, row['id'], "UPDATE missions_v567 SET matter=?,state='creating',exceptions='[]',updated=? WHERE id=? AND state='decision'",
                          (matter, desk.now(), row['id']), 'precision')
        if not ordre:
            return get(desk, row['id'], owner, admin)
        _executer_ordre(desk, ordre)
    elif action == 'validate':
        # 5.6.13 : validation de l'avocat, distincte du dépôt vérifié et du contrôle juridique.
        if row['kind'] != 'document' or current['state'] not in ('verified', 'prepared') or not current['result'].get('path'):
            raise Stop('mission_sans_projet_a_valider')
        from .cockpit530 import review
        from .common import digest
        review(desk, {'item': 'doc:' + digest(current['result']['path'])[:24], 'decision': 'valide'})
    elif action == 'resume':
        if current['state'] not in ('error', 'paused','suggested','interrupted'):   # 5.6.97 (B09) : « Interrompue · reprendre »
            raise Stop('mission_non_reprenante')
        if desk.settings('missions567:pause', False):
            raise Stop('preparation_suspendue')
        if row['job_id']:
            # Reprend le même job et son producteur, donc le même identifiant de
            # document. Les producteurs vérifient leurs dépôts avant de rejouer.
            desk.db.execute("UPDATE jobs SET status='pending',finished=NULL,result=NULL WHERE id=? AND status IN ('error','cancelled')", (row['job_id'],))
            desk.db.execute("UPDATE missions_v567 SET state='queued',exceptions='[]',updated=? WHERE id=?", (desk.now(), row['id']))
            desk.db.commit()
        else:
            ordre=_reserver(desk,row['id'],"UPDATE missions_v567 SET state='creating',exceptions='[]',updated=? WHERE id=? AND state IN ('error','paused','suggested','interrupted')",
                            (desk.now(),row['id']),'reprise')
            if not ordre:
                return get(desk,row['id'],owner,admin)
            _executer_ordre(desk,ordre)
    elif action == 'cancel':   # 5.6.36 : annulation définitive (un projet déjà déposé reste dans le dossier)
        if current['state'] in ('cancelled',):
            raise Stop('mission_deja_annulee')
        if row['job_id']:
            try:
                desk.cancel_job(row['job_id'])
            except Stop:
                pass   # travail déjà terminé : seule la mission est close
        desk.db.execute("UPDATE missions_v567 SET state='cancelled',updated=? WHERE id=?", (desk.now(), row['id']))
        desk.db.commit()
    elif action == 'hide':   # 5.6.36 : retirée de la liste (rien n'est effacé : historique et projets conservés)
        if current['state'] in ACTIVE or current['state'] == 'suspended':   # 5.6.97 (B10) : un travail suspendu reprendra
            raise Stop('mission_active_a_annuler_dabord')
        desk.db.execute('INSERT OR REPLACE INTO missions_hidden5636 VALUES(?,?,?)', (row['id'], owner, desk.now()))
        desk.db.commit()
    else:
        raise Stop('action_mission_invalide')
    _event(desk, row['id'], action, {'matter': row['matter']})
    return get(desk, row['id'], owner, admin)
