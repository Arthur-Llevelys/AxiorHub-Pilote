"""5.6.80 : réponses rapides de l'assistant et de la conversation vocale, et solutions de repli.

Une question de l'avocat suivait le même chemin qu'un travail de fond : file d'attente partagée, verrou général, synchronisation Nextcloud,
calcul d'empreintes (jusqu'à 180 s), une génération par section de chaque pièce jointe, recherche juridique, choix d'outils par le modèle,
prompt de 60 000 caractères, puis contrôle par un second modèle. Ce module fournit :

- **une voie rapide** : un fil du worker 1 ne traite que les questions (``INTERACTIVE``), quelle que soit la file ; il interroge la base
  en lecture seule toutes les 0,5 s ;
- **la priorité sur le modèle** : pendant une question, les travaux automatiques attendent avant leur prochain appel au modèle (90 s au plus) ;
- **un contexte réduit** : seuls les passages utiles partent au modèle ; pièces jointes choisies par pertinence (BM25) ;
- **des replis** : délai court sur le modèle, puis réponse extractive (passages trouvés, sans rédaction, annoncée comme telle) ; recherche
  juridique bornée ; recherche sans IA disponible en quelques millisecondes pour l'affichage immédiat.
"""
from collections import Counter
import json
import logging
import math
from pathlib import Path, PurePosixPath
import re
import sqlite3
import threading
import time

from .common import Stop, fold

INTERACTIVE = ('assistant_answer',)
LANE_ID = 'rapide'
FLAG = 'interactif5680.flag'
FLAG_MAX_AGE = 240            # un drapeau plus ancien (processus arrêté en pleine réponse) n'a plus d'effet
YIELD_MAX_SECONDS = 90
DEFAULTS = {'voice_seconds': 60, 'assistant_seconds': 150, 'voice_context': 14000, 'assistant_context': 32000,
            'legal_voice_seconds': 6, 'legal_assistant_seconds': 15}
BUSY_CODES = {'moteur_ollama_injoignable', 'fournisseur_ia_injoignable', 'generation_ia_delai_depasse', 'modele_ollama_absent',
              'connexion_http_indisponible', 'delai_http_depasse', 'quota_fournisseur_depasse'}
FULL_READING = re.compile(r'\b(resum|synthe|relis|relire|relecture|integral|ensemble d|tout le document|toute la piece|tout ce document|entierement|'
                          r'en entier|chronolog|compar|incoherenc|contradiction|liste toutes|liste tous|analyse complete|analyser complete)')
LEGAL_WORDS = re.compile(r'\b(jurisprudence|arret|article|loi|code|decret|cour de cassation|conseil d.etat|texte applicable|fondement juridique|'
                         r'prescription|delai legal)\b')
WORD = re.compile(r'[0-9a-z]{3,}')
STOP_WORDS = frozenset(('les des une pour dans que qui sur par avec est son ses aux pas plus cette ces mais ont ete sont nous vous leur leurs '
                        'comme tout tous fait etre elle elles ils aussi donc ainsi quel quelle quels quelles dont sans entre vers chez '
                        'peut doit faut cela ceci quoi comment pourquoi quand'))
# 5.6.81 : phrases sans objet (essai du micro, politesse) : rien à chercher dans les pièces du cabinet
SMALL_TALK = frozenset(('ok okay alors bon bien tres bonjour bonsoir salut coucou hello allo merci super parfait voila hein test tester teste essai essayer '
                        'micro ca cela fonctionne fonctionner fonctionnes marche marcher marches entends entendre entendez ecoute ecoutes ecoutez recois '
                        'recevez tu te toi je me moi on il y a as ai avez es etes est ce que qu quoi comment va vas allez vous nous la le les de du un une et '
                        'ou si pas ne maintenant encore m t s l d n j c').split())
log = logging.getLogger(__name__)


def settings(config):
    raw = (config or {}).get('rapide5680') or {}
    out = {}
    for key, value in DEFAULTS.items():
        try:
            out[key] = max(1, int(raw.get(key, value)))
        except (TypeError, ValueError):
            out[key] = value
    return out


def is_voice(purpose):
    return purpose == 'voice_conversation'


def deadline(config, purpose, current=240):
    """Délai de silence accepté du modèle pour une question (premier mot compris)."""
    s = settings(config)
    return min(int(current or 240), s['voice_seconds'] if is_voice(purpose) else s['assistant_seconds'])


def context_limit(config, purpose, current):
    s = settings(config)
    return min(int(current), s['voice_context'] if is_voice(purpose) else s['assistant_context'])


# ------------------------------------------------------------------------------------------------ priorité sur le modèle
def _flag(state_dir):
    return Path(state_dir) / FLAG


def mark(state_dir):
    try:
        _flag(state_dir).touch()
    except OSError:
        pass


def clear(state_dir):
    try:
        _flag(state_dir).unlink(missing_ok=True)
    except OSError:
        pass


def busy(state_dir):
    try:
        return time.time() - _flag(state_dir).stat().st_mtime < FLAG_MAX_AGE
    except OSError:
        return False


def yield_to_questions(job, sleep=time.sleep, max_seconds=YIELD_MAX_SECONDS):
    """Un travail automatique attend qu'une question en cours soit servie, avant son prochain appel au modèle. Le temps est borné : un
    travail de fond n'est jamais bloqué durablement. Retourne le nombre de secondes attendues."""
    if not job or not job.get('automatic') or not job.get('state_dir'):
        return 0
    waited = 0
    while waited < max_seconds and busy(job['state_dir']):
        from .arret5643 import reason_to_stop
        if reason_to_stop(job['state_dir'], job):
            break
        sleep(1)
        waited += 1
    return waited


# ------------------------------------------------------------------------------------------------ voie rapide
def _pending(c):
    path = Path(c['state_dir']) / 'desk.sqlite3'
    db = sqlite3.connect(path.resolve().as_uri() + '?mode=ro', uri=True, timeout=1)
    try:
        return db.execute("SELECT 1 FROM jobs WHERE status='pending' AND kind IN (%s) LIMIT 1" % ','.join('?' * len(INTERACTIVE)), INTERACTIVE).fetchone() is not None
    finally:
        db.close()


def fast_lane(c, stop, desk_factory=None):
    """Fil du worker 1 : ne prend que les questions de l'avocat. Toute erreur est journalisée et le fil continue."""
    desk = None
    while not stop.is_set():
        worked = False
        try:
            if _pending(c):
                if desk is None:
                    if desk_factory:
                        desk = desk_factory(c)
                    else:
                        from .desk import Desk
                        desk = Desk(c)
                    desk.worker_id = LANE_ID
                if c.get('_path'):
                    from .ai_gateway import load_runtime_settings
                    from .common import load_config
                    from .extensions364 import load_runtime_settings as load_extensions
                    desk.c = load_config(c['_path'])
                    load_runtime_settings(desk)
                    load_extensions(desk)
                worked = bool(desk.work_once(only=INTERACTIVE))
        except (sqlite3.Error, Stop):
            stop.wait(2)
        except Exception:   # noqa: BLE001 — le fil ne s'arrête jamais ; la file ordinaire reste disponible
            log.exception('voie rapide')
            desk = None
            stop.wait(2)
        if not worked:
            stop.wait(0.5)


def start_fast_lane(c, stop):
    thread = threading.Thread(target=fast_lane, args=(c, stop), daemon=True, name='voie-rapide5680')
    thread.start()
    return thread


# ------------------------------------------------------------------------------------------------ passages pertinents des pièces jointes
def tokens(text):
    return [w for w in WORD.findall(fold(text)) if w not in STOP_WORDS]


def wants_full_reading(question):
    return bool(FULL_READING.search(fold(question)))


def bm25(parts_tokens, query, k1=1.5, b=0.75):
    """Scores BM25 de chaque passage pour la question (sans dépendance, sur les seuls textes joints)."""
    n = len(parts_tokens)
    if not n:
        return []
    lengths = [len(t) for t in parts_tokens]
    average = (sum(lengths) / n) or 1.0
    df = Counter()
    for t in parts_tokens:
        df.update(set(t))
    out = []
    terms = list(dict.fromkeys(query))
    for t, length in zip(parts_tokens, lengths):
        tf = Counter(t)
        score = 0.0
        for term in terms:
            if tf[term]:
                idf = math.log(1 + (n - df[term] + 0.5) / (df[term] + 0.5))
                score += idf * tf[term] * (k1 + 1) / (tf[term] + k1 * (1 - b + b * length / average))
        out.append(score)
    return out


def _page_of(text):
    m = re.search(r'\[Page\s+(\d+)\]', text or '')
    return m.group(1) if m else ''


def select_parts(documents, question, budget):
    """Choisit les passages à donner au modèle parmi les pièces jointes. ``documents`` : [(source, parts)]. Chaque pièce garde au moins son
    meilleur passage (si le budget le permet) ; le reste va aux passages les mieux notés. Retourne (sources, bilan)."""
    query = tokens(question)
    flat = [(d, i, p) for d, (_, parts) in enumerate(documents) for i, p in enumerate(parts)]
    scores = bm25([tokens(p['excerpt']) for _, _, p in flat], query)
    ranked = sorted(range(len(flat)), key=lambda x: (-scores[x], flat[x][0], flat[x][1]))
    chosen, used, seen_doc = [], 0, set()
    for pass_no in (0, 1):
        for x in ranked:
            d = flat[x][0]
            if x in chosen or (pass_no == 0 and d in seen_doc):
                continue
            size = len(flat[x][2]['excerpt'])
            if used + size > budget:
                continue
            chosen.append(x)
            used += size
            seen_doc.add(d)
    chosen.sort(key=lambda x: (flat[x][0], flat[x][1]))
    out = []
    for x in chosen:
        d, i, part = flat[x]
        source = documents[d][0]
        page = _page_of(part['excerpt'])
        out.append({'id': source['id'] + '-passage-' + str(part.get('ordinal', i)), 'kind': 'piece_jointe_locale',
                    'path': source['path'] + (' · page ' + page if page else ' · caractères %s–%s' % (part['char_start'] + 1, part['char_end'])),
                    'excerpt': part['excerpt'], 'sha256': part.get('sha256', ''), 'partial': True})
    return out, {'documents': len(documents), 'passages_total': len(flat), 'passages_retenus': len(out), 'caracteres_retenus': used,
                 'extracted_chars': sum(len(p['excerpt']) for _, _, p in flat), 'parts_analyzed': len(out), 'legacy_partial': False}


def attachment_sources(desk, ids, question, matter, key, budget, voice=False):
    """Pièces jointes pour une question. Retourne (sources, bilan, identifiants à analyser en entier, motifs des pièces non lues).
    Tout le texte tient dans le budget : il est donné tel quel. Lecture complète demandée sur une pièce trop longue : analyse progressive
    (jamais en conversation vocale). Sinon : passages les plus pertinents."""
    from .improvements36 import attachment_parts
    from .workspace import unreadable_reason
    documents, unreadable = [], []
    for ident in ids:
        try:
            source, parts, meta = attachment_parts(desk, ident, matter, key)
        except Stop as ex:
            if not unreadable_reason(str(ex)):
                raise
            unreadable.append(unreadable_reason(str(ex)))
            continue
        if not parts:   # import ancien : seul l'extrait conservé existe
            parts = [{'ordinal': 0, 'char_start': 0, 'char_end': len(source['excerpt']), 'excerpt': source['excerpt'], 'sha256': ''}]
        documents.append((source, parts))
    total = sum(len(p['excerpt']) for _, parts in documents for p in parts)
    if total > budget and wants_full_reading(question) and not voice:
        return [], None, [s['id'].replace('attachment-', '', 1) for s, _ in documents], unreadable
    if not documents:
        return [], None, [], unreadable
    if total <= budget:
        sources, report = select_parts(documents, '', budget)
        return sources, {**report, 'integral': True}, [], unreadable
    sources, report = select_parts(documents, question, budget)
    return sources, report, [], unreadable


# ------------------------------------------------------------------------------------------------ replis
def bounded(call, seconds, default):
    """Résultat de ``call`` s'il arrive dans le délai, sinon ``default`` : une source lente ne retient jamais la réponse."""
    box = {}

    def run():
        try:
            box['value'] = call()
        except Exception:   # noqa: BLE001 — une source en panne n'empêche pas de répondre
            log.exception('source bornée')
    worker = threading.Thread(target=run, daemon=True, name='source-bornee5680')
    worker.start()
    worker.join(seconds)
    return box.get('value', default)


def legal_research(desk, matter_id, question, voice):
    """Recherche juridique (5.6.49) bornée ; en conversation vocale, seulement si la question le demande."""
    from . import juridique5649
    empty = {'statut': 'delai_depasse', 'sources': [], 'verifiees': 0, 'pistes': 0, 'rapports': [], 'connecteurs': [], 'requete': '', 'objet': 'assistant'}
    if voice and not LEGAL_WORDS.search(fold(question)):
        return {**empty, 'statut': 'non_demandee'}
    s = settings(desk.c)
    config = desk.c

    def run():
        from .desk import Desk
        other = Desk(config)
        try:
            return juridique5649.research(other, matter_id, question, 'assistant')
        finally:
            other.db.close()
    return bounded(run, s['legal_voice_seconds'] if voice else s['legal_assistant_seconds'], empty)


def small_talk(question):
    """Vrai si la phrase n'a aucun objet : chaque mot relève de la conversation (« est-ce que ça fonctionne ? », « bonjour », « merci »)."""
    words = re.findall(r'[0-9a-z]+', fold(str(question or '')))
    return bool(words) and len(words) <= 14 and not set(words) - SMALL_TALK


def small_talk_answer(question):
    """Réponse immédiate, sans modèle ni pièce : aucune donnée ne sort et aucun document sans rapport n'est cité."""
    polite = 'merci' in re.findall(r'[0-9a-z]+', fold(str(question or '')))
    return {'answer': ('Je vous en prie. ' if polite else 'Oui, je vous reçois bien. ') + 'Sur quel dossier ou quelle question voulez-vous travailler ?',
            'source_ids': [], 'limits': [], 'proposed_actions': [], 'sources': [], 'conversation': True}


def conversation_only(args, matter, key, question):
    """Phrase de conversation posée à l'échelle du cabinet, sans dossier, pièce ni courriel : dans un dossier, elle peut prolonger l'échange."""
    context = args.get('page_context') if isinstance(args.get('page_context'), dict) else {}
    return not (matter or key or args.get('attachment_id') or args.get('attachment_ids') or context.get('selected_documents')) and small_talk(question)


def passages(config, matter, question, limit=4):
    """Passages trouvés sans modèle (index plein texte) : quelques millisecondes, disponible même si l'IA est occupée."""
    if not matter and small_talk(question):
        return []
    from .index import DocumentIndex
    index = DocumentIndex(config['state_dir'])
    try:
        rows = index.lexical_passages(matter, [question], limit)
    finally:
        try:
            index.db.close()
        except Exception:   # noqa: BLE001
            pass
    return [{'path': r['path'], 'nom': PurePosixPath(r['path']).name, 'matter': r.get('matter', matter), 'extrait': r['excerpt'][:600]} for r in rows]


def fallback_answer(config, matter, question):
    """Réponse sans rédaction quand le modèle ne répond pas à temps : les passages trouvés, annoncés comme tels."""
    found = passages(config, matter, question, 4)
    if found:
        lines = '\n\n'.join('%d. %s\n%s' % (i, f['nom'], ' '.join(f['extrait'].split())) for i, f in enumerate(found, 1))
        text = 'Le modèle d’IA est occupé ou trop lent : je n’ai pas pu rédiger de réponse à temps. Voici les passages les plus proches de votre demande, sans analyse :\n\n' + lines
    else:
        text = 'Le modèle d’IA est occupé ou trop lent : je n’ai pas pu rédiger de réponse à temps, et aucun passage indexé ne correspond à votre demande. Réessayez dans un instant.'
    return {'answer': text, 'source_ids': [], 'limits': ['Réponse de repli sans IA : passages trouvés par la recherche plein texte, à vérifier dans les pièces.'],
            'proposed_actions': [], 'repli': True,
            'sources': [{'id': 'repli-%d' % i, 'matter': f['matter'], 'path': f['path'], 'modified': '', 'excerpt': f['extrait']} for i, f in enumerate(found, 1)]}


def trim_payload(payload, limit):
    """Réduit ce qui n'est pas une preuve quand le contexte dépasse le budget d'une question rapide (les sources sont gardées)."""
    from .context import payload_size
    for key in ('apprentissage_metier', 'dossier_structure', 'corrections_approuvees', 'contexte_interface'):
        if payload_size(payload) <= limit:
            return payload
        value = payload.get(key)
        if value:
            text = json.dumps(value, ensure_ascii=False, default=str)
            payload[key] = {'extrait': text[:1500]} if len(text) > 1500 else value
    return payload
