"""5.6.101 (lot M, cahier § 9, M01-M04, V101-11) : mesure facultative du temps de traitement, par personne.

Décision du cabinet D6 : facultative, par personne, **désactivée par défaut**. Chaque compte l'active dans Mon compte, pour lui seul.

- M02 : seuls sont enregistrés le type d'élément (``courriel``, ``gmail``, ``document``, ``note_ia``), la commande finale, la durée entre
  l'ouverture et la décision, et le jour (jour du cabinet). Jamais l'objet, le dossier, le contenu ni l'identifiant de l'élément : le
  lecteur (lot R) ne les met pas dans ses événements et le service refuse tout autre champ. Les durées de plus de 30 minutes (élément
  laissé ouvert) sont écartées, dans le navigateur et ici. La clé d'idempotence est un nombre tiré au hasard par le navigateur pour
  chaque mesure : elle ne désigne pas l'élément, elle empêche seulement de compter deux fois le même envoi.
- M03 : Mon compte affiche le résumé de la semaine (médiane par type, nombre d'éléments, évolution sur la semaine précédente), un
  export CSV et une suppression complète.
- M04 : chaque route ne lit et n'écrit que les mesures du compte connecté ; aucun paramètre ne désigne un autre compte ; aucune page
  d'administration, aucun export du cabinet (``export5688.TABLES``) ni journal d'audit ne les reprend.

Routes (déclarées dans ``web440.api`` et ``standalone_auth.allowed``) :

- ``POST /api440/m56101/chrono/reglage`` ``{"actif": "1"|"0", "revision": "oui"|"non"}`` : activation (409 si l'état a changé depuis
  l'affichage ; même choix renvoyé : rien n'est réécrit).
- ``POST /api440/m56101/chrono/mesure`` ``{"type", "commande", "duree_ms", "cle", "jeton"}`` : envoyée par ``navigator.sendBeacon``
  (``agent/static/v56101-chrono.js``), qui ne peut pas poser d'en-tête : le jeton CSRF voyage dans le corps (comme le champ ``csrf`` des
  formulaires historiques), l'origine est contrôlée, le corps est borné à 1 Ko. Rien n'est écrit si la mesure est désactivée.
- ``POST /api440/m56101/chrono/supprimer`` : suppression de toutes les mesures du compte (idempotente par nature).
- ``GET /api440/m56101/chrono/export`` : export CSV des mesures du compte (lecture seule).

Le schéma est créé par ``Desk`` à la préparation de la base, jamais pendant une lecture (R05) ; les lectures (Mon compte, script de la
page, export) n'écrivent rien.
"""
import csv
from datetime import timedelta
import hmac
from html import escape as e
import io
import json
from pathlib import Path
import re
import sqlite3
from statistics import median

from .common import Stop

SCHEMA = '''
CREATE TABLE IF NOT EXISTS chrono56101_comptes(owner TEXT PRIMARY KEY, actif INTEGER NOT NULL DEFAULT 0, modifie TEXT NOT NULL DEFAULT '');
CREATE TABLE IF NOT EXISTS chrono56101_durees(id INTEGER PRIMARY KEY, owner TEXT NOT NULL, type TEXT NOT NULL, commande TEXT NOT NULL,
  duree_ms INTEGER NOT NULL, jour TEXT NOT NULL, cle TEXT NOT NULL);
CREATE UNIQUE INDEX IF NOT EXISTS chrono56101_durees_cle ON chrono56101_durees(owner, cle);
CREATE INDEX IF NOT EXISTS chrono56101_durees_jour ON chrono56101_durees(owner, jour);
'''
ROUTES = ('reglage', 'mesure', 'supprimer', 'export')
TYPES = {'courriel': 'Courriels', 'gmail': 'Gmail', 'document': 'Documents produits', 'note_ia': 'Notes de l’IA'}
CHAMPS = {'type', 'commande', 'duree_ms', 'cle', 'jeton'}
LIMITE_MS = 30 * 60 * 1000                      # M02 : au-delà, l'élément a été laissé ouvert : durée écartée
MAX_CORPS = 1024
COMMANDE = re.compile(r'[a-z][a-z0-9_]{0,39}')
CLE = re.compile(r'[A-Za-z0-9_-]{16,64}')
PRIVE = [('Cache-Control', 'private, no-store')]
MESSAGES = {'version_en_conflit': 'Le réglage a été modifié entre-temps : rechargez la page pour voir l’état actuel.',
            'choix_invalide': 'Choix inconnu.',
            'mesure_invalide': 'Mesure refusée : seuls le type, la commande et la durée sont acceptés.',
            'origine_ou_csrf_refuse': 'Demande refusée : rechargez la page.',
            'requete_trop_longue': 'Demande trop longue.',
            'json_requis': 'Format de la demande refusé.',
            'json_invalide': 'Format de la demande refusé.',
            'objet_json_requis': 'Format de la demande refusé.',
            'role_insuffisant': 'La mesure du temps se règle depuis un compte nominatif.',
            'methode_refusee': 'Méthode refusée.',
            'route_inconnue': 'Adresse inconnue.'}
STATUTS = {'version_en_conflit': '409 Conflict', 'role_insuffisant': '403 Forbidden', 'route_inconnue': '404 Not Found',
           'methode_refusee': '405 Method Not Allowed', 'requete_trop_longue': '413 Payload Too Large'}


def ensure_schema(desk):
    """Tables additives ; appelée par ``Desk`` à la préparation de la base (jamais pendant une requête de lecture)."""
    if desk.db.in_transaction:
        desk.db.commit()
    desk.db.executescript(SCHEMA)
    desk.db.commit()


def allowed(role, route):
    """Droits des écritures (``standalone_auth.allowed``) : chaque rôle, pour son propre compte seulement."""
    return role in ('administrateur', 'avocat', 'assistant') and str(route or '').split('?', 1)[0] in ('reglage', 'mesure', 'supprimer')


def _cle_compte(owner):
    return str(owner or 'cabinet')[:200]


# ------------------------------------------------------------------------------------------------ lectures (aucune écriture)
def actif(desk, owner):
    """M01 : vrai seulement si la personne l'a activée (désactivée par défaut ; table absente ou illisible : désactivée)."""
    try:
        row = desk.db.execute('SELECT actif FROM chrono56101_comptes WHERE owner=?', (_cle_compte(owner),)).fetchone()
    except sqlite3.Error:
        return False
    return bool(row and row[0])


def actif_lu(state_dir, owner):
    """Même lecture, sans ouvrir le bureau : base ouverte en lecture seule (``mode=ro``) pour le gabarit de chaque page."""
    chemin = Path(str(state_dir or '.')) / 'desk.sqlite3'
    if not chemin.is_file():
        return False
    try:
        db = sqlite3.connect(chemin.resolve().as_uri() + '?mode=ro', uri=True, timeout=1)
    except sqlite3.Error:
        return False
    try:
        row = db.execute('SELECT actif FROM chrono56101_comptes WHERE owner=?', (_cle_compte(owner),)).fetchone()
    except sqlite3.Error:
        return False
    finally:
        db.close()
    return bool(row and row[0])


def script_html(cfg, prefix, owner=None):
    """Script d'écoute du lecteur, servi seulement à la personne qui a activé la mesure ; '' sinon (rien n'est écouté, rien n'envoyé)."""
    if owner is None:
        from .i18n5631 import _ctx as _langue
        owner = getattr(_langue, 'owner', '') or 'cabinet'
    if not cfg or not cfg.get('state_dir') or not actif_lu(cfg['state_dir'], owner):
        return ''
    return '<script defer src="%s/static/v56101-chrono.js" data-chrono-actif="1"></script>' % e(prefix or '', quote=True)


def mesures(desk, owner, depuis=None):
    """Mesures du compte (jour, type, commande, durée en ms), les plus récentes d'abord. Liste vide si la table manque."""
    sql, params = 'SELECT jour,type,commande,duree_ms FROM chrono56101_durees WHERE owner=?', [_cle_compte(owner)]
    if depuis:
        sql, params = sql + ' AND jour>=?', params + [depuis]
    try:
        return [dict(jour=r[0], type=r[1], commande=r[2], duree_ms=int(r[3])) for r in desk.db.execute(sql + ' ORDER BY jour DESC, id DESC', params)]
    except sqlite3.Error:
        return []


def _jour(desk, owner):
    from .acces5683 import today_of
    return today_of(desk, owner)[0]


def resume(desk, owner, aujourdhui=None):
    """M03 : semaine en cours (du lundi à aujourd'hui) et semaine précédente, par type : médiane (ms), nombre, évolution de la médiane."""
    aujourdhui = aujourdhui or _jour(desk, owner)
    lundi = aujourdhui - timedelta(days=aujourdhui.weekday())
    precedent = lundi - timedelta(days=7)
    semaines = {'courante': {}, 'precedente': {}}
    for m in mesures(desk, owner, precedent.isoformat()):
        cle = 'courante' if m['jour'] >= lundi.isoformat() else 'precedente'
        semaines[cle].setdefault(m['type'], []).append(m['duree_ms'])
    lignes = []
    for t in TYPES:
        a, b = semaines['courante'].get(t, []), semaines['precedente'].get(t, [])
        if not a and not b:
            continue
        ma, mb = (int(median(a)) if a else None), (int(median(b)) if b else None)
        lignes.append({'type': t, 'mediane': ma, 'nombre': len(a), 'mediane_prec': mb, 'nombre_prec': len(b),
                       'evolution': (ma - mb) if ma is not None and mb is not None else None})
    return {'lundi': lundi.isoformat(), 'lignes': lignes, 'nombre_semaine': sum(len(v) for v in semaines['courante'].values())}


def total(desk, owner):
    try:
        return int(desk.db.execute('SELECT COUNT(*) FROM chrono56101_durees WHERE owner=?', (_cle_compte(owner),)).fetchone()[0])
    except sqlite3.Error:
        return 0


# ------------------------------------------------------------------------------------------------ Mon compte
def duree_texte(ms):
    if ms is None:
        return '—'
    s = int(round(ms / 1000.0))
    return '%d s' % s if s < 60 else '%d min %02d s' % (s // 60, s % 60)


def evolution_texte(ligne):
    d, ref = ligne['evolution'], ligne['mediane_prec']
    if d is None:
        return '—'
    if abs(d) < 1000:
        return 'stable'
    signe = '+' if d > 0 else '−'
    pct = (' (%s%d %%)' % (signe, round(abs(d) * 100.0 / ref))) if ref else ''
    return '%s%s%s' % (signe, duree_texte(abs(d)), pct)


def _tableau(r):
    if not r['lignes']:
        return '<p>Aucune mesure cette semaine ni la semaine précédente.</p>'
    rows = ''.join('<tr><th scope="row">%s</th><td>%s</td><td>%d</td><td>%s</td><td>%d</td><td>%s</td></tr>' % (
        e(TYPES[x['type']]), e(duree_texte(x['mediane'])), x['nombre'], e(duree_texte(x['mediane_prec'])), x['nombre_prec'], e(evolution_texte(x)))
        for x in r['lignes'])
    return ('<div class="ax-table-wrap"><table><caption>Semaine du %s (médiane du temps entre l’ouverture et la décision)</caption><thead><tr>'
            '<th scope="col">Type d’élément</th><th scope="col">Médiane</th><th scope="col">Éléments</th><th scope="col">Médiane, semaine précédente</th>'
            '<th scope="col">Éléments, semaine précédente</th><th scope="col">Évolution</th></tr></thead><tbody>%s</tbody></table></div>') % (
        e('/'.join(reversed(r['lundi'].split('-')))), rows)


def section_mon_compte(desk, prefix, owner):
    """Encadré de Mon compte (contrat lot M) : réglage personnel, résumé hebdomadaire, export CSV, suppression complète."""
    p = e(prefix or '', quote=True)
    est_actif, n = actif(desk, owner), total(desk, owner)
    explication = ('<p class="ax-muted">Mesure facultative, pour vous seul : le temps entre l’ouverture d’un élément à relire et votre décision. '
                   'Sont enregistrés le type d’élément, la commande choisie, la durée et le jour ; jamais l’objet, le dossier, le contenu ni '
                   'l’identifiant de l’élément. Une durée de plus de 30 minutes (élément laissé ouvert) est écartée. Ni les autres membres du '
                   'cabinet ni les administrateurs ne voient vos mesures.</p>')
    if est_actif:
        etat = ('<p role="status"><strong>Mesure active</strong> pour votre compte.</p>'
                '<form class="m5-form" data-api="m56101/chrono/reglage" data-reload><input type="hidden" name="actif" value="0">'
                '<input type="hidden" name="revision" value="oui"><button class="ax-btn ghost" type="submit">Désactiver la mesure</button>'
                '<p role="status" class="m5-status"></p></form>')
    else:
        etat = ('<p role="status"><strong>Mesure désactivée</strong> (réglage par défaut).</p>'
                '<form class="m5-form" data-api="m56101/chrono/reglage" data-reload><input type="hidden" name="actif" value="1">'
                '<input type="hidden" name="revision" value="non"><button class="ax-btn" type="submit">Activer la mesure pour mon compte</button>'
                '<p role="status" class="m5-status"></p></form>')
    donnees = ''
    if n or est_actif:
        donnees = ('<h3>Résumé de la semaine</h3>%s<p class="ax-muted">%d mesure(s) enregistrée(s) au total. Les mesures déjà enregistrées restent '
                   'jusqu’à leur suppression, même après désactivation.</p><p class="c101-chrono-actions">'
                   '<a class="ax-btn ghost" href="%s/api440/m56101/chrono/export" download>Exporter mes mesures (CSV)</a></p>'
                   '<form class="m5-form" data-api="m56101/chrono/supprimer" data-reload data-confirm="Supprimer toutes vos mesures ? Cette suppression '
                   'est définitive."><button class="ax-btn danger" type="submit"%s>Supprimer toutes mes mesures</button>'
                   '<p role="status" class="m5-status"></p></form>') % (_tableau(resume(desk, owner)), n, p, '' if n else ' disabled')
    return '<section class="ax-card" id="c101-chrono"><h2>Mesure du temps de traitement</h2>%s%s%s</section>' % (explication, etat, donnees)


# ------------------------------------------------------------------------------------------------ écritures
def _moi(env):
    """La personne connectée. Un jeton de service sans identité ne mesure rien pour personne."""
    from .web567 import actor
    if str(env.get('HTTP_AUTHORIZATION') or '').startswith('Bearer ') and not env.get('HTTP_X_AXIORHUB_USER'):
        raise Stop('role_insuffisant')
    owner, role = actor(env)
    if role not in ('administrateur', 'avocat', 'assistant'):
        raise Stop('role_insuffisant')
    return _cle_compte(owner), role


def _corps_balise(env, auth):
    """Corps d'une mesure envoyée par ``sendBeacon`` : même origine, jeton CSRF (en-tête ou champ ``jeton``), JSON borné à 1 Ko."""
    if env.get('HTTP_ORIGIN') != auth.get('origin'):
        raise Stop('origine_ou_csrf_refuse')
    try:
        length = int(env.get('CONTENT_LENGTH', '0') or 0)
    except ValueError:
        raise Stop('json_invalide') from None
    if length > MAX_CORPS:
        raise Stop('requete_trop_longue')
    if env.get('CONTENT_TYPE', '').split(';')[0].strip().lower() not in ('application/json', 'text/plain'):
        raise Stop('json_requis')
    try:
        data = json.loads(env['wsgi.input'].read(length).decode('utf-8') or '{}') if length > 0 else {}
    except (ValueError, UnicodeError):
        raise Stop('json_invalide') from None
    if not isinstance(data, dict):
        raise Stop('objet_json_requis')
    jeton = str(env.get('HTTP_X_CSRF_TOKEN') or data.get('jeton') or '')
    if not hmac.compare_digest(jeton.encode('utf-8'), str(auth.get('csrf') or '').encode('utf-8')) or not auth.get('csrf'):
        raise Stop('origine_ou_csrf_refuse')
    return data


def regler(desk, owner, voulu, revision):
    """M01 : activation pour ce compte seul. Même état renvoyé : aucune écriture ; état changé depuis l'affichage : 409."""
    actuel = actif(desk, owner)
    if actuel == voulu:
        return {'enregistre': True, 'inchange': True, 'actif': actuel, 'message': 'Déjà enregistré.'}
    if revision != ('oui' if actuel else 'non'):
        raise Stop('version_en_conflit')
    desk.db.execute('INSERT INTO chrono56101_comptes(owner,actif,modifie) VALUES(?,?,?) ON CONFLICT(owner) DO UPDATE SET actif=excluded.actif,'
                    'modifie=excluded.modifie', (owner, 1 if voulu else 0, desk.now()))
    desk.db.commit()
    return {'enregistre': True, 'actif': voulu, 'revision': 'oui' if voulu else 'non',
            'message': 'Mesure du temps activée pour votre compte.' if voulu else 'Mesure du temps désactivée. Vos mesures restent jusqu’à leur suppression.'}


def enregistrer(desk, owner, data):
    """M02 : une mesure ; tout autre champ que type, commande, durée et clé est refusé. Désactivée ou durée hors bornes : rien d'écrit."""
    if set(data) - CHAMPS:
        raise Stop('mesure_invalide')
    type_, commande, cle = str(data.get('type') or ''), str(data.get('commande') or ''), str(data.get('cle') or '')
    duree = data.get('duree_ms')
    if type_ not in TYPES or not COMMANDE.fullmatch(commande) or not CLE.fullmatch(cle) or isinstance(duree, bool) or not isinstance(duree, (int, float)):
        raise Stop('mesure_invalide')
    if not actif(desk, owner):
        return {'enregistre': False, 'motif': 'mesure_desactivee'}
    duree = int(duree)
    if duree <= 0 or duree > LIMITE_MS:
        return {'enregistre': False, 'motif': 'duree_ecartee'}
    cur = desk.db.execute('INSERT OR IGNORE INTO chrono56101_durees(owner,type,commande,duree_ms,jour,cle) VALUES(?,?,?,?,?,?)',
                          (owner, type_, commande, duree, _jour(desk, owner).isoformat(), cle))
    desk.db.commit()
    return {'enregistre': True, 'deja': cur.rowcount == 0}


def supprimer(desk, owner):
    """M03 : suppression complète des mesures du compte (et d'elles seules)."""
    n = desk.db.execute('DELETE FROM chrono56101_durees WHERE owner=?', (owner,)).rowcount
    desk.db.commit()
    return {'supprime': True, 'nombre': n, 'message': '%d mesure(s) supprimée(s).' % n if n else 'Aucune mesure à supprimer.'}


def _cellule(valeur):
    texte = str(valeur if valeur is not None else '')
    return "'" + texte if texte[:1] in ('=', '+', '-', '@', '\t', '\r') else texte


def export_csv(desk, owner):
    """Export CSV des seules mesures du compte : jour, type, commande, durée en secondes (aucune écriture)."""
    out = io.StringIO()
    out.write('﻿')
    w = csv.writer(out, delimiter=';', lineterminator='\r\n')
    w.writerow(('Jour', 'Type', 'Commande', 'Durée (secondes)'))
    for m in mesures(desk, owner):
        w.writerow((_cellule(m['jour']), _cellule(m['type']), _cellule(m['commande']), '%.1f' % (m['duree_ms'] / 1000.0)))
    return {'status': '200 OK', 'kind': 'text/csv; charset=utf-8', 'body': out.getvalue(),
            'headers': [('Content-Disposition', 'attachment; filename="mesure-temps.csv"')] + PRIVE}


def api(env, desk, auth, prefix, name, args, method):
    from .web440 import check_post, json_out
    route = name[len('m56101/chrono/'):].split('?', 1)[0]
    if route not in ROUTES:
        raise Stop('route_inconnue')
    owner, _role = _moi(env)
    if route == 'export':
        if method not in ('GET', 'HEAD'):
            raise Stop('methode_refusee')
        return export_csv(desk, owner)
    if method != 'POST':
        raise Stop('methode_refusee')
    if route == 'mesure':
        return {**json_out(enregistrer(desk, owner, _corps_balise(env, auth))), 'headers': list(PRIVE)}
    data = check_post(env, auth)                       # origine, jeton CSRF en en-tête, corps JSON borné
    if route == 'reglage':
        choix = str(data.get('actif') if data.get('actif') is not None else '')
        if choix not in ('1', '0', 'True', 'False', 'true', 'false'):
            raise Stop('choix_invalide')
        out = regler(desk, owner, choix in ('1', 'True', 'true'), str(data.get('revision') or ''))
    else:
        out = supprimer(desk, owner)
    return {**json_out(out), 'headers': list(PRIVE)}


def repondre(env, desk, auth, prefix, name, args, method):
    """Point d'entrée de ``web440.api`` : codes d'erreur traduits en statut et message, réponse privée."""
    from .web440 import json_out
    try:
        return api(env, desk, auth, prefix, name, args, method)
    except Stop as error:
        code = str(error).split(':')[0]
        return {**json_out({'error': code, 'message': MESSAGES.get(code, 'Demande refusée.')}, STATUTS.get(code, '400 Bad Request')),
                'headers': list(PRIVE)}
