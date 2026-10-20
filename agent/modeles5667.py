"""5.6.67 (point 6) : le bon modèle pour chaque tâche, éprouvé sur les cas du cabinet.

1. Répartition recommandée, à partir des modèles réellement installés dans Ollama (taille annoncée, en milliards de paramètres) :
   - « rapide » (tri des courriels, lecture des pièces jointes, conversation vocale) : le plus petit modèle de conversation d'au moins
     3 milliards de paramètres ;
   - « approfondi » (rédaction des courriels et des documents, analyse juridique) : le plus grand modèle jusqu'à 40 milliards (au-delà,
     les temps de réponse d'un poste ou d'un petit serveur deviennent trop longs), de préférence entre 14 et 40 ;
   - « contrôle » : un modèle différent du rédacteur quand il en existe un d'au moins 8 milliards (un regard indépendant), sinon le même.
   « Appliquer » passe par le même enregistrement que Paramètres (chaque modèle est vérifié auprès d'Ollama).
2. Banc d'essai sur vos cas : jusqu'à 20 réponses que vous avez réellement envoyées après un brouillon de l'agent. Le courriel reçu et
   votre réponse sont anonymisés (parties, adresses, téléphones, montants, dates, RG remplacés), puis le modèle choisi rédige sa réponse
   au courriel ; elle est comparée à la vôtre (texte et éléments clés retrouvés). Le banc ne s'exécute qu'avec un modèle LOCAL : les cas
   du cabinet ne sortent jamais. Les cas et les scores restent dans la base locale.
"""
from datetime import datetime, timezone
import difflib
import json
from pathlib import Path
import re
import sqlite3
import time

from .common import Stop, fold

SCHEMA = '''
CREATE TABLE IF NOT EXISTS banc5667_cases(id TEXT PRIMARY KEY, matter TEXT NOT NULL, role TEXT NOT NULL DEFAULT '', incoming TEXT NOT NULL,
  reply TEXT NOT NULL, created TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS banc5667_runs(id INTEGER PRIMARY KEY AUTOINCREMENT, model TEXT NOT NULL, at TEXT NOT NULL, cases INTEGER NOT NULL,
  score REAL NOT NULL, similarity REAL NOT NULL, coverage REAL NOT NULL, seconds REAL NOT NULL, failures INTEGER NOT NULL, details TEXT NOT NULL);
'''
MAX_CASES = 20
FAST_MIN, CONTROL_MIN, COMPLEX_MAX, COMPLEX_PREFERRED = 3.0, 8.0, 40.0, 14.0
ROLE_LABELS = {'fast': 'Rapide (tri, pièces jointes)', 'complex': 'Approfondi (rédaction, analyse)', 'control': 'Contrôle indépendant'}
STOP = {'nous', 'vous', 'votre', 'notre', 'dans', 'pour', 'avec', 'cette', 'mais', 'leur', 'sont', 'avoir', 'faire', 'bien', 'tout', 'plus',
        'cordialement', 'confraternellement', 'madame', 'monsieur', 'maitre', 'confrere', 'bonjour', 'merci', 'ainsi', 'comme', 'entre'}


def _now():
    return datetime.now(timezone.utc).isoformat(timespec='seconds')


def ensure_schema(desk):
    desk.db.executescript(SCHEMA)
    desk.db.commit()


# ------------------------------------------------------------------------------------------------ répartition
def _billions(details, size):
    raw = str((details or {}).get('parameter_size') or '').strip().upper()
    m = re.fullmatch(r'([\d.]+)\s*([BM])', raw)
    if m:
        value = float(m.group(1))
        return value / 1000 if m.group(2) == 'M' else value
    return round(size / 0.6e9, 1) if size else 0.0     # repli : taille du fichier (quantification 4 bits ≈ 0,6 Go par milliard)


_TAGS = {}   # liste des modèles d'Ollama gardée 2 minutes : la page Paramètres ne réinterroge pas Ollama à chaque affichage
TAGS_SECONDS = 120


def installed(desk, tags=None):
    if tags is None:
        from .common import HTTP
        cfg = desk.c.get('ollama') or {}
        url = str(cfg.get('url') or 'http://127.0.0.1:11434')
        cached = _TAGS.get(url)
        if cached and time.monotonic() - cached[0] < TAGS_SECONDS:
            tags = cached[1]
        else:
            tags = HTTP(url, local_only=True, timeout=5).json('GET', '/api/tags')
            _TAGS[url] = (time.monotonic(), tags)
    out = []
    for x in (tags or {}).get('models', []):
        if not isinstance(x, dict):
            continue
        name = str(x.get('name') or x.get('model') or '')
        low = name.lower()
        if not name or 'embed' in low or low.endswith('-cloud') or ':cloud' in low:
            continue
        out.append({'name': name, 'billions': _billions(x.get('details'), int(x.get('size') or 0)), 'size_gb': round(int(x.get('size') or 0) / 1e9, 1)})
    return sorted(out, key=lambda m: (m['billions'], m['name']))


def recommend(models):
    """{'fast','complex','control','why'} ; chaînes vides si aucun modèle n'est installé."""
    if not models:
        return {'fast': '', 'complex': '', 'control': '', 'why': 'Aucun modèle de conversation installé dans Ollama.'}
    usable = [m for m in models if m['billions'] >= FAST_MIN] or models
    fast = usable[0]
    bounded = [m for m in models if m['billions'] <= COMPLEX_MAX] or models[:1]
    preferred = [m for m in bounded if m['billions'] >= COMPLEX_PREFERRED]
    complex_ = (preferred or bounded)[-1]
    others = [m for m in bounded if m['name'] != complex_['name'] and m['billions'] >= CONTROL_MIN]
    control = others[-1] if others else complex_
    why = ('Rédaction : %s (%s Md de paramètres)%s. Tri et pièces jointes : %s (%s Md). Contrôle : %s.' % (
        complex_['name'], _fmt(complex_['billions']),
        '' if complex_['billions'] >= COMPLEX_PREFERRED else ' — aucun modèle de 14 à 40 Md installé : la qualité de rédaction en sera limitée',
        fast['name'], _fmt(fast['billions']),
        control['name'] + (' (modèle distinct du rédacteur)' if control['name'] != complex_['name'] else ' (même modèle : aucun autre modèle '
                                                                                                          'd’au moins 8 Md installé)')))
    return {'fast': fast['name'], 'complex': complex_['name'], 'control': control['name'], 'why': why}


def _fmt(value):
    return ('%.1f' % value).rstrip('0').rstrip('.').replace('.', ',')


def current(desk):
    routing = desk.c.get('model_routing') or {}
    base = (desk.c.get('ollama') or {}).get('model', '')
    return {role: str(routing.get(role + '_model') or base) for role in ROLE_LABELS}


def apply(desk, choice):
    """Enregistre la répartition (chaque modèle est vérifié auprès d'Ollama par l'enregistrement habituel de Paramètres)."""
    from .preferences363 import save_model
    done = {}
    for role in ROLE_LABELS:
        name = str((choice or {}).get(role) or '').strip()
        if name:
            save_model(desk, name, role)
            done[role] = name
    if not done:
        raise Stop('repartition_vide')
    desk.audit('modeles5667_repartition', done)
    return {'ok': True, 'applied': done, 'message': 'Répartition appliquée aux prochains travaux : ' +
            ', '.join('%s → %s' % (ROLE_LABELS[r].split(' (')[0], n) for r, n in done.items()) + '.'}


# ------------------------------------------------------------------------------------------------ banc d'essai
def _anonymize(desk, matter, text):
    from .savoir5659 import anonymize
    out, _ = anonymize(desk, matter, str(text or ''))
    return out


def build_cases(desk, fetch=None, limit=MAX_CASES):
    """Cas du cabinet : courriel reçu (relu dans la messagerie) et réponse réellement envoyée, rapprochés avec certitude d'un brouillon
    de l'agent ; anonymisés avant d'être enregistrés. ``fetch(message_id)`` → texte du courriel reçu (messagerie par défaut)."""
    from .learning480 import _role_of
    ensure_schema(desk)
    path = Path(desk.c['state_dir']) / 'sent-memory.sqlite3'
    if not path.is_file():
        return {'cases': 0, 'reason': 'memoire_des_envois_absente'}
    try:   # envois antérieurs à la 5.6.67 : courriel reçu retrouvé par le texte exact du brouillon de l'agent
        from .memory import SentMemory
        SentMemory(desk.c).backfill_pairs()
    except Exception:   # noqa: BLE001 — banc constitué avec les seuls rapprochements déjà connus
        pass
    db = sqlite3.connect('file:%s?mode=ro' % path.as_posix(), uri=True, timeout=5)
    try:
        pairs = db.execute('SELECT p.sent_key,p.incoming_message_id,e.matter,e.body,e.audience FROM pairs5667 p JOIN examples_v2 e '
                           'ON e.key=p.sent_key ORDER BY e.sent_at DESC LIMIT ?', (limit * 3,)).fetchall()
    except sqlite3.Error:
        pairs = []
    finally:
        db.close()
    if fetch is None:
        fetch = _fetch_incoming(desk)
    made = 0
    for sent_key, message_id, matter_ref, body, audience in pairs:
        if made >= limit:
            break
        if desk.db.execute('SELECT 1 FROM banc5667_cases WHERE id=?', (sent_key,)).fetchone():
            made += 1
            continue
        try:
            incoming = fetch(message_id)
        except Stop:
            continue
        if not incoming or not body:
            continue
        matter = str(matter_ref or '').split('@', 1)[0]
        desk.db.execute('INSERT OR REPLACE INTO banc5667_cases(id,matter,role,incoming,reply,created) VALUES(?,?,?,?,?,?)',
                        (sent_key, matter, _role_of(audience), _anonymize(desk, matter, incoming)[:6000],
                         _anonymize(desk, matter, body)[:4000], _now()))
        made += 1
    desk.db.commit()
    return {'cases': desk.db.execute('SELECT COUNT(*) FROM banc5667_cases').fetchone()[0]}


def _fetch_incoming(desk):
    from .mailbox import Mailbox, imap_quote
    cfg = desk.c['mail']

    def fetch(message_id):
        box = Mailbox(cfg)
        try:
            uids = box.search(cfg['inbox'], 'UNDELETED', 'HEADER', 'Message-ID', imap_quote(message_id))
            if not uids:
                raise Stop('courriel_introuvable')
            return box.fetch(cfg['inbox'], uids[-1]).text
        finally:
            box.close()
    return fetch


def _key_words(text):
    return {w for w in re.findall(r'[a-z]{6,}', fold(text)) if w not in STOP}


def score(generated, reference):
    """(similarité du texte, part des mots clés de votre réponse retrouvés) — chacun entre 0 et 1."""
    from .style480 import core
    a, b = re.findall(r"[\w']+", fold(core(generated))), re.findall(r"[\w']+", fold(core(reference)))
    similarity = difflib.SequenceMatcher(None, a, b, autojunk=False).ratio() if a and b else 0.0
    keys = _key_words(core(reference))
    coverage = len(keys & _key_words(generated)) / len(keys) if keys else 0.0
    return round(similarity, 4), round(coverage, 4)


PROMPT = ('Tu es l’avocat du cabinet. Rédige la réponse au courriel ci-dessous, en français, prête à relire : ton professionnel, '
          'aucune invention de fait, de date ou de montant ; « [À COMPLÉTER : …] » là où une information manque. Les éléments entre '
          'crochets ([PARTIE], [DATE]…) remplacent des données anonymisées : garde-les tels quels. Réponds par le seul texte du courriel.')


def run(desk, model_name, model=None, clock=time.monotonic):
    """Fait rédiger les réponses aux cas par ``model_name`` (modèle local) et enregistre le score moyen."""
    ensure_schema(desk)
    name = str(model_name or '').strip()
    if not re.fullmatch(r'[A-Za-z0-9_.:/-]{1,100}', name):
        raise Stop('modele_invalide')
    cases = [dict(r) for r in desk.db.execute('SELECT * FROM banc5667_cases ORDER BY created DESC LIMIT ?', (MAX_CASES,))]
    if not cases:
        raise Stop('aucun_cas_du_cabinet')
    if model is None:
        from .model import Model
        cfg = dict(desk.c.get('ollama') or {})
        if not cfg.get('url'):
            raise Stop('banc_modele_local_requis')
        model = Model({**cfg, 'model': name, 'purpose': 'mail_drafting'})
    details, sims, covs, seconds, failures = [], [], [], [], 0
    for case in cases:
        started = clock()
        try:
            out = model.complete([{'role': 'system', 'content': PROMPT}, {'role': 'user', 'content': case['incoming']}],
                                 temperature=0, max_tokens=900)
        except Stop as ex:
            failures += 1
            details.append({'cas': case['id'][:8], 'erreur': str(ex)[:80]})
            continue
        took = clock() - started
        sim, cov = score(str(out or ''), case['reply'])
        sims.append(sim)
        covs.append(cov)
        seconds.append(took)
        details.append({'cas': case['id'][:8], 'role': case['role'], 'similarite': sim, 'elements_cles': cov, 'secondes': round(took, 1)})
    n = len(sims)
    similarity = sum(sims) / n if n else 0.0
    coverage = sum(covs) / n if n else 0.0
    total = round(100 * (0.4 * similarity + 0.6 * coverage), 1) if n else 0.0
    desk.db.execute('INSERT INTO banc5667_runs(model,at,cases,score,similarity,coverage,seconds,failures,details) VALUES(?,?,?,?,?,?,?,?,?)',
                    (name, _now(), len(cases), total, round(similarity, 4), round(coverage, 4), round(sum(seconds) / n, 1) if n else 0.0,
                     failures, json.dumps(details, ensure_ascii=False)))
    desk.db.commit()
    return {'model': name, 'cases': len(cases), 'score': total, 'failures': failures,
            'message': 'Banc d’essai de %s sur %d cas du cabinet : score %s / 100.' % (name, len(cases), _fmt(total))}


def runs(desk, limit=12):
    ensure_schema(desk)
    return [dict(r) for r in desk.db.execute('SELECT * FROM banc5667_runs ORDER BY id DESC LIMIT ?', (int(limit),))]


def perform(desk, kind, args):
    """Travail « banc5667 » (demandé par l'administrateur) : constitue les cas puis fait passer le modèle choisi."""
    built = build_cases(desk)
    if not built.get('cases'):
        raise Stop('aucun_cas_du_cabinet')
    return run(desk, str(args.get('model') or ''))


# ------------------------------------------------------------------------------------------------ affichage (Paramètres › IA)
def html(desk, prefix, admin=True):
    from html import escape as e
    try:
        models = installed(desk)
        error = ''
    except Stop as ex:
        from .erreurs5634 import explain
        models, error = [], explain(str(ex).split(':')[0], getattr(ex, 'detail', None))
    rec = recommend(models)
    now = current(desk)
    rows = ''.join('<tr><td>%s</td><td>%s</td><td>%s</td></tr>' % (e(ROLE_LABELS[r]), e(now.get(r, '') or '—'), e(rec.get(r, '') or '—'))
                   for r in ROLE_LABELS)
    same = all(now.get(r) == rec.get(r) for r in ROLE_LABELS if rec.get(r))
    action = ''
    if admin and rec.get('complex') and not same:
        action = ('<form class="m5-form" data-api="m5667/modeles/appliquer" data-reload>%s<button class="ax-btn" type="submit">Appliquer cette '
                  'répartition</button><p role="status" class="m5-status"></p></form>') % ''.join(
            '<input type="hidden" name="%s" value="%s">' % (r, e(rec[r], quote=True)) for r in ROLE_LABELS if rec.get(r))
    bench = runs(desk)
    bench_rows = ''.join('<tr><td>%s</td><td>%s</td><td>%d</td><td>%s / 100</td><td>%s %%</td><td>%s s</td></tr>' % (
        e(r['model']), e(r['at'][:16].replace('T', ' ')), r['cases'], _fmt(r['score']), _fmt(round(100 * r['coverage'])), _fmt(r['seconds']))
        for r in bench)
    launch = ''
    if admin and models:
        options = ''.join('<option value="%s"%s>%s</option>' % (e(m['name'], quote=True), ' selected' if m['name'] == rec.get('complex') else '',
                                                                e('%s (%s Md)' % (m['name'], _fmt(m['billions'])))) for m in models)
        launch = ('<form class="m5-form" data-api="m5667/banc/lancer" data-reload><label>Modèle à éprouver <select name="model">%s</select>'
                  '</label><button class="ax-btn ghost" type="submit">Lancer le banc sur vos cas</button><p role="status" class="m5-status"></p>'
                  '</form>') % options
    return ('<section class="ax-card" id="modeles5667"><h2>Le bon modèle pour chaque tâche</h2>%s'
            '<table class="ax-table"><thead><tr><th>Tâche</th><th>Modèle actuel</th><th>Recommandé</th></tr></thead><tbody>%s</tbody></table>'
            '<p class="ax-muted">%s</p>%s'
            '<h3>Banc d’essai sur vos cas</h3><p class="ax-muted">Jusqu’à %d réponses que vous avez réellement envoyées après un brouillon de '
            'l’agent, anonymisées ; le modèle choisi rédige sa réponse, comparée à la vôtre (texte et éléments clés). Uniquement avec un modèle '
            'local : vos cas ne sortent jamais du cabinet. Le banc tourne en arrière-plan (plusieurs minutes).</p>%s%s</section>') % (
        ('<p class="d51-warn">Ollama injoignable : %s</p>' % e(error)) if error else '', rows, e(rec['why']), action, MAX_CASES, launch,
        ('<table class="ax-table"><thead><tr><th>Modèle</th><th>Date</th><th>Cas</th><th>Score</th><th>Éléments clés retrouvés</th>'
         '<th>Temps moyen</th></tr></thead><tbody>%s</tbody></table>' % bench_rows) if bench_rows else
        '<p class="ax-muted">Aucun banc lancé pour l’instant.</p>')
