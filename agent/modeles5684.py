"""5.6.84 : versions et validation des modèles de la bibliothèque unique (cahier 5.6.84, § 8). Pas de seconde bibliothèque : ce module ajoute un cycle de vie à ``bibliotheque5639``.

Cycle : brouillon, à relire, validé, à réviser, retiré. Une version publiée est figée : une nouvelle version ne réécrit jamais un document existant, et le modèle livré reste disponible. Un modèle
non validé reste utilisable à la main, signalé comme tel ; une génération autonome exige une version validée. Aucune expression de clause ni aucun contenu de modèle n'est exécuté."""
from datetime import date
from html import escape as e
import hashlib
import json
import re

from .common import Stop
from .erreurs5634 import Qualified

STATES = {'draft': 'Brouillon', 'review': 'À relire', 'validated': 'Validé', 'needs_update': 'À réviser', 'withdrawn': 'Retiré'}
MOVES = {'draft': ('review', 'withdrawn'), 'review': ('validated', 'draft', 'withdrawn'), 'validated': ('needs_update', 'withdrawn'), 'needs_update': ('review', 'withdrawn'), 'withdrawn': ('draft',)}
LAWYER_MOVES = ('validated', 'withdrawn')
TAG = re.compile(r'\{\{\s*(\w+)\s*\}\}')


def schema(desk):
    desk.db.execute('''CREATE TABLE IF NOT EXISTS template_versions5684(template_id TEXT NOT NULL, version INTEGER NOT NULL, parent_version INTEGER NOT NULL DEFAULT 0, state TEXT NOT NULL, sha256 TEXT NOT NULL, text TEXT NOT NULL,
        legal_sources TEXT NOT NULL DEFAULT '[]', comment TEXT NOT NULL DEFAULT '', author TEXT NOT NULL DEFAULT '', validated_by TEXT NOT NULL DEFAULT '', review_date TEXT NOT NULL DEFAULT '', created TEXT NOT NULL, PRIMARY KEY(template_id, version))''')
    desk.db.commit()
    _colonnes(desk)


def _colonnes(desk):
    """5.6.89 : type de document, langue, domaine, champs obligatoires : colonnes ajoutées sans toucher aux versions existantes."""
    known = {r[1] for r in desk.db.execute('PRAGMA table_info(template_versions5684)')}
    for column in ('document_type', 'language', 'domain', 'required_fields'):
        if column not in known:
            desk.db.execute("ALTER TABLE template_versions5684 ADD COLUMN %s TEXT NOT NULL DEFAULT ''" % column)
    desk.db.commit()


def _sha(text):
    return hashlib.sha256(text.encode('utf-8')).hexdigest()


def _seed(desk, t):
    """Première version : le modèle tel qu'il existe, à l'état brouillon. Les personnalisations du cabinet ne sont pas écrasées."""
    text = '\n'.join(t['texte'])
    desk.db.execute('INSERT OR IGNORE INTO template_versions5684(template_id,version,state,sha256,text,legal_sources,author,created) VALUES(?,1,?,?,?,?,?,?)',
                    (t['id'], 'draft', _sha(text), text, json.dumps(t.get('sources') or [], ensure_ascii=False), 'axiorhub', date.today().isoformat()))
    desk.db.commit()


def current(desk, ident):
    from . import bibliotheque5639 as lib
    schema(desk)
    row = desk.db.execute('SELECT * FROM template_versions5684 WHERE template_id=? ORDER BY version DESC LIMIT 1', (ident,)).fetchone()
    if not row:
        _seed(desk, lib.template(desk, ident))
        row = desk.db.execute('SELECT * FROM template_versions5684 WHERE template_id=? ORDER BY version DESC LIMIT 1', (ident,)).fetchone()
    return dict(row)


def selectionner(desk, ident, autonome=False, document_type=''):
    """5.6.89 (§ 39) : état du modèle à la sélection. Retiré : refusé. Génération autonome : version validée exigée, le reste est une limite explicite. À la main, un modèle non validé reste utilisable et signalé."""
    row = current(desk, ident)
    if row['state'] == 'withdrawn':
        raise Stop('modele_retire')
    if autonome and row['state'] != 'validated':
        raise Stop('modele_non_valide')
    if document_type and row['document_type'] and row['document_type'] != document_type:
        raise Stop('modele_hors_parcours')
    return row


def renseigner(desk, data, owner='cabinet', role='avocat'):
    """Fiche de la version courante : type, langue, domaine, champs obligatoires. Une version validée reste figée."""
    row = current(desk, str(data.get('modele') or ''))
    if row['state'] == 'validated':
        raise Stop('modele_valide_fige')
    fields = [x.strip() for x in str(data.get('champs_obligatoires') or '').split(',') if x.strip()]
    desk.db.execute('UPDATE template_versions5684 SET document_type=?, language=?, domain=?, required_fields=? WHERE template_id=? AND version=?',
                    (str(data.get('type') or '')[:60], str(data.get('langue') or '')[:12], str(data.get('domaine') or '')[:80], json.dumps(fields, ensure_ascii=False), row['template_id'], row['version']))
    desk.db.commit()
    desk.audit('modele5689_fiche', {'modele': row['template_id'], 'version': row['version'], 'owner': owner})
    return {'message': 'Fiche du modèle enregistrée.'}


def champs_manquants(desk, ident, valeurs):
    """Champs obligatoires déclarés et non renseignés : une limite à montrer, pas un blocage silencieux."""
    row = current(desk, ident)
    return [f for f in json.loads(row.get('required_fields') or '[]') if not str((valeurs or {}).get(f) or '').strip()]


def versions(desk, ident):
    current(desk, ident)
    return [dict(r) for r in desk.db.execute('SELECT version, parent_version, state, sha256, comment, author, validated_by, review_date, created FROM template_versions5684 WHERE template_id=? ORDER BY version DESC', (ident,))]


def _known(t):
    from . import bibliotheque_cessions5684 as cat, cessions5684
    known = set(cat.LABELS) | {'cabinet', 'operation', 'dossier'} | {k for k, _, _ in t['variables']} | set(t.get('fixes') or {})
    for fields in cessions5684.FIELDS.values():
        known |= {f[0] for f in fields}
    from . import bibliotheque5639 as lib
    base = lib.BUILTIN.get(t['id'])
    if base:
        known |= set(TAG.findall('\n'.join(base['texte'])))
    return known


def check(t, text, declared=()):
    """Contrôle avant publication : clauses équilibrées et champs connus. Un champ inconnu arrête la publication avec un message lisible (BI04)."""
    from . import bibliotheque5639 as lib
    lines = text.split('\n')
    lib.check_structure(lines)
    original = {k for k in TAG.findall('\n'.join(lib.BUILTIN[t['id']]['texte']))} if t['id'] in lib.BUILTIN else set()
    allowed = (_known_base(t) | original | set(declared))
    unknown = sorted(set(TAG.findall(text)) - allowed)
    if unknown:
        raise Qualified('modele_champ_inconnu', precision=', '.join(unknown[:8]))


def _known_base(t):
    from . import bibliotheque_cessions5684 as cat, cessions5684
    known = set(cat.LABELS) | {'cabinet', 'operation', 'dossier'} | set(t.get('fixes') or {})
    for fields in cessions5684.FIELDS.values():
        known |= {f[0] for f in fields}
    return known


def publish(desk, data, owner='cabinet'):
    """« Personnaliser le modèle » : nouvelle version explicite, avec auteur et commentaire. Elle repart à l'état brouillon ; le modèle livré et les anciennes versions restent."""
    from . import bibliotheque5639 as lib
    t = lib.template(desk, data.get('modele'))
    text = str(data.get('texte') or '').replace('\r\n', '\n').strip('\n')
    if not text.strip():
        raise Stop('modele_vide')
    comment = re.sub(r'\s+', ' ', str(data.get('commentaire') or '')).strip()[:300]
    if len(comment) < 3:
        raise Stop('commentaire_version_requis')
    check(t, text, [x.strip() for x in str(data.get('nouveaux_champs') or '').split(',') if x.strip()])
    base = current(desk, t['id'])
    if _sha(text) == base['sha256']:
        return {'message': 'Aucune différence avec la version en cours.', 'version': base['version']}
    number = base['version'] + 1
    desk.db.execute('INSERT INTO template_versions5684(template_id,version,parent_version,state,sha256,text,legal_sources,comment,author,created) VALUES(?,?,?,?,?,?,?,?,?,?)',
                    (t['id'], number, base['version'], 'draft', _sha(text), text, base['legal_sources'], comment, str(owner), date.today().isoformat()))
    desk.db.commit()
    lib.customize(desk, {'modele': t['id'], 'texte': text, 'version_vue': lib.view_token(t)}, owner)
    desk.audit('modele5684_version', {'modele': t['id'], 'version': number, 'owner': owner})
    return {'message': 'Version %d créée : à relire avant validation.' % number, 'version': number}


def move(desk, data, owner='cabinet', role='avocat'):
    """Changer l'état : la validation et le retrait reviennent à un avocat, avec date de revue ; une version retirée reste liée aux documents historiques."""
    ident = str(data.get('modele') or '')
    row = current(desk, ident)
    target = str(data.get('etat') or '')
    if target not in MOVES.get(row['state'], ()):
        raise Stop('etat_modele_invalide')
    if target in LAWYER_MOVES and role == 'assistant':
        raise Stop('validation_avocat_requise')
    today = date.today().isoformat()
    desk.db.execute('UPDATE template_versions5684 SET state=?, validated_by=?, review_date=? WHERE template_id=? AND version=?',
                    (target, str(owner) if target == 'validated' else row['validated_by'], today if target == 'validated' else row['review_date'], ident, row['version']))
    desk.db.commit()
    desk.audit('modele5684_etat', {'modele': ident, 'version': row['version'], 'etat': target, 'owner': owner})
    return {'message': 'Modèle : %s.' % STATES[target]}


def badge(desk, t):
    return '<span class="b5639-badge">%s</span>' % e(STATES[current(desk, t['id'])['state']])


def panel_html(desk, t):
    row = current(desk, t['id'])
    moves = ''.join('<form class="m5-form m5-inline" data-api="m5684/modele/etat" data-reload="1"><input type="hidden" name="modele" value="%s"><input type="hidden" name="etat" value="%s"><button class="ax-btn ghost" type="submit">%s</button></form>' % (
        e(t['id'], quote=True), target, e(STATES[target])) for target in MOVES[row['state']])
    rows = ''.join('<li>v%d : %s%s%s</li>' % (v['version'], e(STATES[v['state']]), (' — ' + e(v['comment'])) if v['comment'] else '', (' — validé par %s le %s' % (e(v['validated_by']), e(v['review_date']))) if v['validated_by'] else '') for v in versions(desk, t['id']))
    fiche = '' if row['state'] == 'validated' else ('<details><summary>Fiche du modèle</summary><form class="m5-form" data-api="m5684/modele/fiche" data-reload="1"><input type="hidden" name="modele" value="%s"><label>Type de document<input name="type" value="%s"></label><label>Langue<input name="langue" value="%s"></label>'
                                                    '<label>Domaine<input name="domaine" value="%s"></label><label>Champs obligatoires, séparés par des virgules<input name="champs_obligatoires" value="%s"></label><button class="ax-btn ghost" type="submit">Enregistrer la fiche</button></form></details>' % (
                                                        e(t['id'], quote=True), e(row['document_type'], quote=True), e(row['language'], quote=True), e(row['domain'], quote=True), e(', '.join(json.loads(row['required_fields'] or '[]')), quote=True)))
    return fiche + ('<section class="ax-card"><h3>Validation du modèle</h3><p>Version %d : <strong>%s</strong>. Portée : %s.</p><div>%s</div><ul>%s</ul><details><summary>Publier une nouvelle version</summary><form class="m5-form" data-api="m5684/modele/version" data-reload="1">'
            '<input type="hidden" name="modele" value="%s"><label>Texte du modèle<textarea name="texte" rows="10" data-no-dictation>%s</textarea></label><label>Commentaire<input name="commentaire" required></label><label>Champs nouveaux, séparés par des virgules<input name="nouveaux_champs"></label>'
            '<button class="ax-btn ghost" type="submit">Publier cette version</button></form></details></section>') % (
        row['version'], e(STATES[row['state']]), e(t.get('portee') or 'à qualifier'), moves, rows, e(t['id'], quote=True), e(row['text']))
