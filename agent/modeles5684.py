"""5.6.84 : versions et validation des modèles de la bibliothèque unique (cahier 5.6.84, § 8). Pas de seconde bibliothèque : ce module ajoute un cycle de vie à ``bibliotheque5639``.

Cycle : brouillon, à relire, validé, à revoir (5.6.108 ; « à réviser » auparavant), retiré. Une version publiée est figée : une nouvelle version ne réécrit jamais un document existant, et le modèle livré reste disponible. Un modèle
non validé reste utilisable à la main, signalé comme tel ; une génération autonome exige une version validée. Aucune expression de clause ni aucun contenu de modèle n'est exécuté."""
from datetime import date
from html import escape as e
import hashlib
import json
import re

from .common import Stop
from .erreurs5634 import Qualified
from . import droits56108 as D

STATES = dict(D.ETATS)   # 5.6.108 (lot B) : libellés du cahier (brouillon, à relire, validé, à revoir, retiré)
MOVES = {'draft': ('review', 'withdrawn'), 'review': ('validated', 'draft', 'withdrawn'), 'validated': ('needs_update', 'withdrawn'), 'needs_update': ('review', 'withdrawn'), 'withdrawn': ('draft',)}
LAWYER_MOVES = ('validated', 'withdrawn')
TAG = re.compile(r'\{\{\s*(\w+)\s*\}\}')


def schema(desk):
    desk.db.execute('''CREATE TABLE IF NOT EXISTS template_versions5684(template_id TEXT NOT NULL, version INTEGER NOT NULL, parent_version INTEGER NOT NULL DEFAULT 0, state TEXT NOT NULL, sha256 TEXT NOT NULL, text TEXT NOT NULL,
        legal_sources TEXT NOT NULL DEFAULT '[]', comment TEXT NOT NULL DEFAULT '', author TEXT NOT NULL DEFAULT '', validated_by TEXT NOT NULL DEFAULT '', review_date TEXT NOT NULL DEFAULT '', created TEXT NOT NULL, PRIMARY KEY(template_id, version))''')
    desk.db.commit()
    _colonnes(desk)
    _colonnes56108(desk)
    _colonnes_questionnaire56108(desk)


# 5.6.108 (lot B) : qualification par droit applicable. ``review_date`` reste la DATE DE VALIDATION (sens historique) ; la date de réexamen est le
# champ distinct ``reexamen_le``. Type d'acte = ``document_type``, langue = ``language``, sources = ``legal_sources``, champs requis = ``required_fields``.
COLONNES56108 = (('systeme', "TEXT NOT NULL DEFAULT ''"), ('profils_parties', "TEXT NOT NULL DEFAULT '[]'"), ('provenance', "TEXT NOT NULL DEFAULT ''"),
                 ('licence', "TEXT NOT NULL DEFAULT ''"), ('reexamen_le', "TEXT NOT NULL DEFAULT ''"), ('applicable_depuis', "TEXT NOT NULL DEFAULT ''"),
                 ('applicable_jusquau', "TEXT NOT NULL DEFAULT ''"), ('options', "TEXT NOT NULL DEFAULT '[]'"),
                 ('incompatibilites', "TEXT NOT NULL DEFAULT '[]'"), ('qualification', "TEXT NOT NULL DEFAULT ''"))
_READY56108 = set()


def _colonnes56108(desk):
    key = str((getattr(desk, 'c', None) or {}).get('state_dir') or id(desk.db))
    if key in _READY56108:
        return
    known = {r[1] for r in desk.db.execute('PRAGMA table_info(template_versions5684)')}
    for column, decl in COLONNES56108:
        if column not in known:
            desk.db.execute('ALTER TABLE template_versions5684 ADD COLUMN %s %s' % (column, decl))
    desk.db.execute('''CREATE TABLE IF NOT EXISTS modeles56108_actifs(systeme TEXT NOT NULL, langue TEXT NOT NULL, type_acte TEXT NOT NULL,
        template_id TEXT NOT NULL, version INTEGER NOT NULL, designe_par TEXT NOT NULL, designe_le TEXT NOT NULL, PRIMARY KEY(systeme, langue, type_acte))''')
    desk.db.commit()
    _READY56108.add(key)


def _metadonnees(t):
    """Valeurs de qualification à écrire dans une ligne du registre (5.6.108) pour le modèle ``t`` de la bibliothèque."""
    q = D.qualification(t)
    return {'systeme': q['systeme'], 'language': q['langue'], 'document_type': q['type_acte'],
            'profils_parties': json.dumps(q['profils_parties'], ensure_ascii=False), 'provenance': q['provenance'], 'licence': q['licence'],
            'applicable_depuis': q['applicable_depuis'], 'applicable_jusquau': q['applicable_jusquau'],
            'options': json.dumps(q['options'], ensure_ascii=False), 'incompatibilites': json.dumps(q['incompatibilites'], ensure_ascii=False),
            'legal_sources': json.dumps(q['sources'], ensure_ascii=False), 'required_fields': json.dumps(q['champs_requis'], ensure_ascii=False),
            'qualification': '56108'}


def migrer56108(desk):
    """Migration compatible (démarrage) : métadonnées de qualification des versions déjà enregistrées — seulement les champs VIDES, jamais une saisie
    du cabinet ; ancien code « EN » → « UK-EAW » ; date de réexamen des versions déjà validées = validation + un an (au-delà : à revoir, la sélection
    autonome les écarte). Idempotent (marqueur ``qualification``)."""
    from . import bibliotheque5639 as lib
    lib.schema(desk)
    rows = desk.db.execute("SELECT * FROM template_versions5684 WHERE qualification='' LIMIT 2000").fetchall()
    if not rows:
        return 0
    cab = {r['id']: r for r in desk.db.execute('SELECT * FROM bibliotheque5639')}
    done = 0
    for row in rows:
        row = dict(row)
        try:
            t = lib._build(row['template_id'], cab.get(row['template_id']))
        except Stop:     # modèle du cabinet supprimé : sa version historique garde ses champs, seul l'ancien code est migré
            t = None
        meta = _metadonnees(t) if t else {'qualification': '56108'}
        changes = {}
        for col, value in meta.items():
            have = row.get(col)
            if col == 'qualification' or have in (None, '') or (col in ('legal_sources', 'profils_parties', 'options', 'incompatibilites') and have == '[]'):
                changes[col] = value
        if row.get('systeme'):
            changes['systeme'] = D.migrer(row['systeme'])
        if row['state'] == 'validated' and not row.get('reexamen_le') and D.date_iso(row.get('review_date')):
            changes['reexamen_le'] = D.reexamen_par_defaut(row['review_date'])
        desk.db.execute('UPDATE template_versions5684 SET %s WHERE template_id=? AND version=?' % ', '.join('%s=?' % c for c in changes),
                        list(changes.values()) + [row['template_id'], row['version']])
        done += 1
    desk.db.commit()
    try:
        desk.audit('droits56108_qualification', {'versions': done})
    except Exception:   # noqa: BLE001
        pass
    return done


def etats(desk):
    """Version courante de chaque modèle déjà inscrit au registre, en UNE lecture et sans rien écrire (pages et sélection) ; un modèle absent du
    registre est à l'état de brouillon de sa version 1."""
    try:
        rows = desk.db.execute('SELECT t.* FROM template_versions5684 t JOIN (SELECT template_id, MAX(version) AS v FROM template_versions5684 '
                               'GROUP BY template_id) m ON m.template_id=t.template_id AND m.v=t.version').fetchall()
    except Exception:   # noqa: BLE001 — registre pas encore créé (première lecture) ou bureau sans base : tout est brouillon
        return {}
    return {r['template_id']: dict(r) for r in rows}


def actifs(desk):
    try:
        return {(r['systeme'], r['langue'], r['type_acte']): (r['template_id'], r['version'])
                for r in desk.db.execute('SELECT * FROM modeles56108_actifs')}
    except Exception:   # noqa: BLE001
        return {}


def choisir(desk, systeme, langue, type_acte, au=None):
    """5.6.108 : sélection AUTONOME d'un modèle pour un droit, une langue et un type d'acte (voir ``droits56108.choisir``)."""
    from . import bibliotheque5639 as lib
    au = D.date_iso(au) or D.jour_du_cabinet(desk)
    presents = {t['id'] for t in lib.catalog(desk)}
    return D.choisir([m for m in etats(desk).values() if m['template_id'] in presents], systeme, langue, type_acte, au, actifs(desk))


def _colonnes(desk):
    """5.6.89 : type de document, langue, domaine, champs obligatoires : colonnes ajoutées sans toucher aux versions existantes."""
    known = {r[1] for r in desk.db.execute('PRAGMA table_info(template_versions5684)')}
    for column in ('document_type', 'language', 'domain', 'required_fields'):
        if column not in known:
            desk.db.execute("ALTER TABLE template_versions5684 ADD COLUMN %s TEXT NOT NULL DEFAULT ''" % column)
    desk.db.commit()


_READY_Q56108 = set()


def _colonnes_questionnaire56108(desk):
    """5.6.108 (lot Q) : questionnaire de la version (JSON ``axiorhub-questionnaire-1``, vide = dérivé de la trame) et son empreinte ; voir
    ``questionnaires56108``. Écrit à la validation (figé) ou à la publication d'un questionnaire du cabinet, jamais pendant une lecture."""
    key = str((getattr(desk, 'c', None) or {}).get('state_dir') or id(desk.db))
    if key in _READY_Q56108:
        return
    known = {r[1] for r in desk.db.execute('PRAGMA table_info(template_versions5684)')}
    for column in ('questionnaire', 'questionnaire_sha'):
        if column not in known:
            desk.db.execute("ALTER TABLE template_versions5684 ADD COLUMN %s TEXT NOT NULL DEFAULT ''" % column)
    desk.db.commit()
    _READY_Q56108.add(key)


def _heriter_questionnaire56108(desk, base, ident, number):
    """5.6.108 (lot Q) : une nouvelle version du texte reprend le questionnaire ÉCRIT par le cabinet (complété à la lecture des variables nouvelles) ;
    un questionnaire seulement figé depuis la trame ne l'est pas : il sera dérivé du nouveau texte, puis figé à la validation."""
    try:
        origine = json.loads(base.get('questionnaire') or '{}').get('origine')
    except ValueError:
        origine = ''
    if origine == 'cabinet':
        desk.db.execute('UPDATE template_versions5684 SET questionnaire=?, questionnaire_sha=? WHERE template_id=? AND version=?',
                        (base['questionnaire'], base.get('questionnaire_sha') or '', ident, number))


def _sha(text):
    return hashlib.sha256(text.encode('utf-8')).hexdigest()


def _seed(desk, t):
    """Première version : le modèle tel qu'il existe, à l'état brouillon. Les personnalisations du cabinet ne sont pas écrasées."""
    text = '\n'.join(t['texte'])
    desk.db.execute('INSERT OR IGNORE INTO template_versions5684(template_id,version,state,sha256,text,legal_sources,author,created) VALUES(?,1,?,?,?,?,?,?)',
                    (t['id'], 'draft', _sha(text), text, json.dumps(t.get('sources') or [], ensure_ascii=False), 'axiorhub', date.today().isoformat()))
    meta = _metadonnees(t)   # 5.6.108 (lot B) : qualification (droit, langue, type, sources de contrôle…) dès la première version
    desk.db.execute('UPDATE template_versions5684 SET %s WHERE template_id=? AND version=1 AND qualification=?' % ', '.join('%s=?' % c for c in meta),
                    list(meta.values()) + [t['id'], ''])
    desk.db.commit()


def current(desk, ident):
    from . import bibliotheque5639 as lib
    schema(desk)
    row = desk.db.execute('SELECT * FROM template_versions5684 WHERE template_id=? ORDER BY version DESC LIMIT 1', (ident,)).fetchone()
    if not row:
        _seed(desk, lib.template(desk, ident))
        row = desk.db.execute('SELECT * FROM template_versions5684 WHERE template_id=? ORDER BY version DESC LIMIT 1', (ident,)).fetchone()
    return dict(row)


ECARTS = {'autre_droit': 'modele_autre_droit', 'autre_langue': 'modele_autre_langue', 'autre_type': 'modele_hors_parcours',
          'non_valide': 'modele_non_valide', 'validation_non_tracee': 'modele_non_valide', 'sans_source': 'modele_sans_source_de_controle',
          'validation_posterieure': 'modele_non_valide', 'reexamen_depasse': 'modele_a_reexaminer', 'hors_periode': 'modele_hors_periode'}


def selectionner(desk, ident, autonome=False, document_type='', systeme='', langue='', au=None):
    """5.6.89 (§ 39) : état du modèle à la sélection. Retiré : refusé. Génération autonome : version validée exigée, le reste est une limite explicite. À la main, un modèle non validé reste utilisable et signalé.

    5.6.108 (lot B) : une génération autonome exige en plus une validation tracée par une personne (jamais une identité d'IA), au moins une source de
    contrôle, une date de réexamen non dépassée, la période d'applicabilité, et — si le droit est donné — le même système juridique (liste fermée ;
    « CA », « UK » ou « EN » seuls refusés). À la main, un modèle d'un autre droit que celui demandé est refusé ; le reste est signalé."""
    row = current(desk, ident)
    if row['state'] == 'withdrawn':
        raise Stop('modele_retire')
    if systeme:
        systeme = D.systeme_requis(systeme)
        if (row.get('systeme') or '') and row['systeme'] != systeme:
            raise Stop('modele_autre_droit')
    if autonome and row['state'] != 'validated':
        raise Stop('modele_non_valide')
    if document_type and row['document_type'] and row['document_type'] != document_type:
        raise Stop('modele_hors_parcours')
    if autonome:
        motif = D.ecart_autonome(row, systeme or row.get('systeme') or '', langue, '', D.date_iso(au) or D.jour_du_cabinet(desk))
        if motif == 'autre_droit':   # modèle sans droit qualifié : rien n'est présumé
            raise Stop('systeme_juridique_a_preciser')
        if motif:
            raise Stop(ECARTS.get(motif, 'modele_non_valide'))
    return row


def renseigner(desk, data, owner='cabinet', role='avocat'):
    """Fiche de la version courante : type, langue, domaine, champs obligatoires. Une version validée reste figée."""
    row = current(desk, str(data.get('modele') or ''))
    if row['state'] == 'validated':
        raise Stop('modele_valide_fige')
    fields = [x.strip() for x in str(data.get('champs_obligatoires') or '').split(',') if x.strip()]
    desk.db.execute('UPDATE template_versions5684 SET document_type=?, language=?, domain=?, required_fields=? WHERE template_id=? AND version=?',
                    (str(data.get('type') or '')[:60], str(data.get('langue') or '')[:12], str(data.get('domaine') or '')[:80], json.dumps(fields, ensure_ascii=False), row['template_id'], row['version']))
    _renseigner56108(desk, row, data)
    desk.db.commit()
    desk.audit('modele5689_fiche', {'modele': row['template_id'], 'version': row['version'], 'owner': owner})
    return {'message': 'Fiche du modèle enregistrée.'}


def _liste(value, sep=','):
    return [x.strip()[:300] for x in str(value or '').replace('\r', '').split(sep) if x.strip()][:40]


def _renseigner56108(desk, row, data):
    """5.6.108 (lot B) : qualification par droit applicable, champ par champ et seulement pour les champs envoyés. Le droit d'un modèle livré est
    celui pour lequel son texte a été rédigé (une traduction ne vaut pas adaptation) : seul un modèle ajouté par le cabinet change de droit."""
    changes = {}
    if 'systeme' in data:
        systeme = D.systeme_requis(data.get('systeme'))
        if systeme != (row.get('systeme') or ''):
            from . import bibliotheque5639 as lib
            if row['template_id'] in lib.BUILTIN:
                raise Stop('droit_du_modele_livre_fixe')
            desk.db.execute('UPDATE bibliotheque5639 SET droit=? WHERE id=?', (systeme, row['template_id']))
        changes['systeme'] = systeme
    for key, col in (('provenance', 'provenance'), ('licence', 'licence')):
        if key in data:
            changes[col] = re.sub(r'\s+', ' ', str(data.get(key) or '')).strip()[:300]
    for key, col, sep in (('profils', 'profils_parties', ','), ('sources', 'legal_sources', '\n'), ('options', 'options', ',')):
        if key in data:
            changes[col] = json.dumps(_liste(data.get(key), sep), ensure_ascii=False)
    if 'incompatibilites' in data:
        pairs = [[a.strip(), b.strip()] for a, _, b in (x.partition('/') for x in _liste(data.get('incompatibilites'))) if a.strip() and b.strip()]
        changes['incompatibilites'] = json.dumps(pairs, ensure_ascii=False)
    for key in ('applicable_depuis', 'applicable_jusquau'):
        if key in data:
            value = D.date_iso(data.get(key))
            if str(data.get(key) or '').strip() and not value:
                raise Stop('date_applicabilite_invalide')
            changes[key] = value
    if changes.get('applicable_depuis') and changes.get('applicable_jusquau') and changes['applicable_jusquau'] < changes['applicable_depuis']:
        raise Stop('date_applicabilite_invalide')
    if changes:
        desk.db.execute('UPDATE template_versions5684 SET %s WHERE template_id=? AND version=?' % ', '.join('%s=?' % c for c in changes),
                        list(changes.values()) + [row['template_id'], row['version']])


def champs_manquants(desk, ident, valeurs):
    """Champs obligatoires déclarés et non renseignés : une limite à montrer, pas un blocage silencieux."""
    row = current(desk, ident)
    return [f for f in json.loads(row.get('required_fields') or '[]') if not str((valeurs or {}).get(f) or '').strip()]


def versions(desk, ident):
    current(desk, ident)
    return [dict(r) for r in desk.db.execute('SELECT version, parent_version, state, sha256, comment, author, validated_by, review_date, created, reexamen_le, systeme FROM template_versions5684 WHERE template_id=? ORDER BY version DESC', (ident,))]


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
    herites = ('document_type', 'language', 'domain', 'required_fields', 'systeme', 'profils_parties', 'provenance', 'licence', 'applicable_depuis',
               'applicable_jusquau', 'options', 'incompatibilites', 'qualification')   # 5.6.108 : qualification reprise ; validation et réexamen jamais hérités
    desk.db.execute('UPDATE template_versions5684 SET %s WHERE template_id=? AND version=?' % ', '.join('%s=?' % c for c in herites if c in base),
                    [base[c] for c in herites if c in base] + [t['id'], number])
    _heriter_questionnaire56108(desk, base, t['id'], number)   # 5.6.108 (lot Q)
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
    if D.est_identite_ia(owner):   # 5.6.108 (lot B) : une IA ou un automate ne change jamais l'état d'un modèle, encore moins sa validation
        raise Stop('etat_modele_reserve_aux_personnes')
    if target in LAWYER_MOVES and role == 'assistant':
        raise Stop('validation_avocat_requise')
    today = D.jour_du_cabinet(desk, owner)    # jour du cabinet, pas celui du serveur
    reexamen = row.get('reexamen_le') or ''
    if target == 'validated':   # 5.6.108 : droit qualifié, sources de contrôle, date de réexamen distincte de la date de validation
        if (row.get('systeme') or '') not in D.SYSTEMES:
            raise Stop('systeme_juridique_a_preciser')
        if not json.loads(row.get('legal_sources') or '[]'):
            raise Stop('sources_de_controle_requises')
        asked = str(data.get('reexamen_le') or '').strip()
        reexamen = D.date_iso(asked) if asked else D.reexamen_par_defaut(today)
        if not reexamen or reexamen <= today:
            raise Stop('date_reexamen_invalide')
    desk.db.execute('UPDATE template_versions5684 SET state=?, validated_by=?, review_date=?, reexamen_le=? WHERE template_id=? AND version=?',
                    (target, str(owner) if target == 'validated' else row['validated_by'], today if target == 'validated' else row['review_date'],
                     reexamen, ident, row['version']))
    actif = target == 'validated' and str(data.get('actif') or '') in ('1', 'on', 'true', 'True')
    if actif:   # plusieurs modèles validés pour le même droit, la même langue et le même type : désignation explicite de la version active
        desk.db.execute('INSERT OR REPLACE INTO modeles56108_actifs(systeme, langue, type_acte, template_id, version, designe_par, designe_le) '
                        'VALUES(?,?,?,?,?,?,?)', (row['systeme'], row.get('language') or '', row.get('document_type') or '', ident, row['version'],
                                                  str(owner)[:200], today))
    elif target == 'withdrawn':
        desk.db.execute('DELETE FROM modeles56108_actifs WHERE template_id=?', (ident,))
    if target == 'validated':   # 5.6.108 (lot Q) : le questionnaire de la version validée est figé avec son empreinte
        from .questionnaires56108 import figer
        figer(desk, row)
    desk.db.commit()
    desk.audit('modele5684_etat', {'modele': ident, 'version': row['version'], 'etat': target, 'owner': owner, 'systeme': row.get('systeme') or '',
                                   'reexamen_le': reexamen if target == 'validated' else '', 'version_active': actif})
    return {'message': 'Modèle : %s.' % STATES[target] + ((' Réexamen prévu le %s.' % reexamen) if target == 'validated' else '')}


def badge(desk, t):
    return '<span class="b5639-badge">%s</span>' % e(STATES[current(desk, t['id'])['state']])


MOVE_LABELS = {'review': 'Marquer à relire', 'validated': 'Marquer validé', 'draft': 'Repasser en brouillon', 'needs_update': 'Marquer à revoir',
               'withdrawn': 'Retirer'}


def habilite(desk, target=''):
    """5.6.108 (lot B) : le compte qui affiche la page peut-il changer l'état d'un modèle (droit fin « valider un modèle ») ? Valider et retirer
    restent réservés aux avocats et administrateurs. Simple affichage : ``move`` refait ses contrôles."""
    try:
        from .membres5650 import current_author, effective_rights, viewer_role
        role = viewer_role()
        if target in LAWYER_MOVES and role == 'assistant':
            return False
        return bool(effective_rights(desk.c['state_dir'], current_author() or 'cabinet', role).get('valider_modele'))
    except Exception:   # noqa: BLE001 — contrôle indisponible : aucun bouton (jamais d'accès élargi)
        return False


def _move_form(desk, t, row, target):
    extra = ''
    if target == 'validated':
        proposed = D.reexamen_par_defaut(D.jour_du_cabinet(desk))
        extra = ('<label>Réexamen le<input type="date" name="reexamen_le" value="%s" required></label><label class="e5636-switch"><input type="checkbox" '
                 'name="actif" value="1"> Version active pour ce droit, cette langue et ce type d’acte</label>') % e(proposed, quote=True)
    return ('<form class="m5-form m5-inline" data-api="m5684/modele/etat" data-reload="1"><input type="hidden" name="modele" value="%s"><input type="hidden" '
            'name="etat" value="%s">%s<button class="ax-btn ghost" type="submit">%s</button></form>') % (
        e(t['id'], quote=True), target, extra, e(MOVE_LABELS.get(target, STATES[target])))


def _json_list(value):
    try:
        out = json.loads(value or '[]')
    except ValueError:
        return []
    return out if isinstance(out, list) else []


def qualification_html(desk, t, row):
    """5.6.108 (lot B) : fiche de qualification lisible — droit, langue, type, parties, provenance, sources de contrôle, validation, réexamen,
    applicabilité, champs requis, options, incompatibilités, usage permis par l'état."""
    from . import bibliotheque5639 as lib
    def ligne(label, value):
        return '<dt>%s</dt><dd>%s</dd>' % (e(label), e(value) if value else '<span class="ax-muted">non renseigné</span>')
    pairs = ', '.join('%s / %s' % tuple(p[:2]) for p in _json_list(row.get('incompatibilites')) if len(p) >= 2)
    periode = ''
    if row.get('applicable_depuis') or row.get('applicable_jusquau'):
        periode = 'du %s au %s' % (row.get('applicable_depuis') or '…', row.get('applicable_jusquau') or '…')
    validation = ('par %s le %s' % (row['validated_by'], row['review_date'])) if row['state'] == 'validated' and row.get('validated_by') else ''
    sources = ''.join('<li>%s</li>' % e(s) for s in _json_list(row.get('legal_sources')))
    return ('<section class="ax-card b56108-qualif"><h3>Qualification du modèle</h3><p><span class="b5639-badge b56108-etat-%s">%s</span> %s</p>'
            '<dl class="d51-dl b56108-fiche">%s%s%s%s%s%s%s%s%s%s%s%s</dl><h4>Sources de contrôle</h4>%s<p class="ax-muted">Les textes officiels servent à contrôler '
            'le modèle ; ils ne fournissent pas à eux seuls un contrat prêt à l’emploi. Une traduction ne vaut pas adaptation à un autre droit.</p>'
            '</section>') % (
        e(row['state'], quote=True), e(STATES[row['state']]), e(D.USAGES.get(row['state'], '')),
        ligne('Droit applicable', D.libelle(row.get('systeme') or t.get('droit'))), ligne('Langue', lib.LANGUES.get(row.get('language'), row.get('language'))),
        ligne('Type d’acte', D.TYPES.get(row.get('document_type'), row.get('document_type'))),
        ligne('Profils des parties', ', '.join(_json_list(row.get('profils_parties')))),
        ligne('Auteur, provenance, licence', ' — '.join(x for x in (row.get('author'), row.get('provenance'), row.get('licence')) if x)),
        ligne('Version', str(row['version'])), ligne('Validé', validation), ligne('Réexamen le', row.get('reexamen_le') if row['state'] == 'validated' else ''),
        ligne('Période d’applicabilité', periode), ligne('Champs requis', ', '.join(_json_list(row.get('required_fields')))),
        ligne('Options', ', '.join(_json_list(row.get('options')))), ligne('Incompatibilités', pairs),
        ('<ul>%s</ul>' % sources) if sources else '<p class="c5636-warn">Aucune source de contrôle : la validation est impossible.</p>')


def fiche56108_html(desk, t, row):
    """5.6.108 (lot B) : formulaire de qualification d'une version non validée (une version validée reste figée)."""
    if row['state'] == 'validated':
        return ''
    from . import bibliotheque5639 as lib
    if t['id'] in lib.BUILTIN:
        droit = '<p>Droit applicable : <strong>%s</strong> (fixé par le texte du modèle livré).</p>' % e(D.libelle(row.get('systeme')))
    else:
        droit = '<label>Droit applicable<select name="systeme">%s</select></label>' % ''.join(
            '<option value="%s"%s>%s</option>' % (k, ' selected' if k == row.get('systeme') else '', e(v)) for k, v in D.LIBELLES.items())
    pairs = ', '.join('%s/%s' % tuple(p[:2]) for p in _json_list(row.get('incompatibilites')) if len(p) >= 2)
    return ('<details class="ax-card"><summary>Qualification par droit applicable</summary><form class="m5-form" data-api="m5684/modele/fiche" '
            'data-reload="1"><input type="hidden" name="modele" value="%s"><input type="hidden" name="type" value="%s"><input type="hidden" name="langue" '
            'value="%s"><input type="hidden" name="domaine" value="%s"><input type="hidden" name="champs_obligatoires" value="%s">%s'
            '<label>Profils des parties, séparés par des virgules<input name="profils" value="%s"></label><label>Provenance<input name="provenance" '
            'value="%s"></label><label>Licence ou droits d’utilisation<input name="licence" value="%s"></label><label>Sources de contrôle, une par '
            'ligne<textarea name="sources" rows="3" data-no-dictation>%s</textarea></label><label>Applicable depuis<input type="date" '
            'name="applicable_depuis" value="%s"></label><label>Applicable jusqu’au<input type="date" name="applicable_jusquau" value="%s"></label>'
            '<label>Options (clauses), séparées par des virgules<input name="options" value="%s"></label><label>Incompatibilités (clause/clause, '
            'séparées par des virgules)<input name="incompatibilites" value="%s"></label><button class="ax-btn ghost" type="submit">Enregistrer la '
            'qualification</button></form></details>') % (
        e(t['id'], quote=True), e(row.get('document_type') or '', quote=True), e(row.get('language') or '', quote=True), e(row.get('domain') or '', quote=True),
        e(', '.join(_json_list(row.get('required_fields'))), quote=True), droit, e(', '.join(_json_list(row.get('profils_parties'))), quote=True),
        e(row.get('provenance') or '', quote=True), e(row.get('licence') or '', quote=True), e('\n'.join(_json_list(row.get('legal_sources')))),
        e(row.get('applicable_depuis') or '', quote=True), e(row.get('applicable_jusquau') or '', quote=True),
        e(', '.join(_json_list(row.get('options'))), quote=True), e(pairs, quote=True))


def panel_html(desk, t):
    row = current(desk, t['id'])
    allowed = [target for target in MOVES[row['state']] if habilite(desk, target)]
    moves = ''.join(_move_form(desk, t, row, target) for target in allowed) or \
        '<p class="ax-muted">Changement d’état réservé aux comptes habilités à valider les modèles.</p>'
    rows = ''.join('<li>v%d : %s%s%s</li>' % (v['version'], e(STATES[v['state']]), (' — ' + e(v['comment'])) if v['comment'] else '', (' — validé par %s le %s' % (e(v['validated_by']), e(v['review_date']))) if v['validated_by'] else '') for v in versions(desk, t['id']))
    fiche = '' if row['state'] == 'validated' else ('<details><summary>Fiche du modèle</summary><form class="m5-form" data-api="m5684/modele/fiche" data-reload="1"><input type="hidden" name="modele" value="%s"><label>Type de document<input name="type" value="%s"></label><label>Langue<input name="langue" value="%s"></label>'
                                                    '<label>Domaine<input name="domaine" value="%s"></label><label>Champs obligatoires, séparés par des virgules<input name="champs_obligatoires" value="%s"></label><button class="ax-btn ghost" type="submit">Enregistrer la fiche</button></form></details>' % (
                                                        e(t['id'], quote=True), e(row['document_type'], quote=True), e(row['language'], quote=True), e(row['domain'], quote=True), e(', '.join(json.loads(row['required_fields'] or '[]')), quote=True)))
    return qualification_html(desk, t, row) + fiche + fiche56108_html(desk, t, row) + ('<section class="ax-card"><h3>Validation du modèle</h3><p>Version %d : <strong>%s</strong>. Portée : %s.</p><div>%s</div><ul>%s</ul><details><summary>Publier une nouvelle version</summary><form class="m5-form" data-api="m5684/modele/version" data-reload="1">'
            '<input type="hidden" name="modele" value="%s"><label>Texte du modèle<textarea name="texte" rows="10" data-no-dictation>%s</textarea></label><label>Commentaire<input name="commentaire" required></label><label>Champs nouveaux, séparés par des virgules<input name="nouveaux_champs"></label>'
            '<button class="ax-btn ghost" type="submit">Publier cette version</button></form></details></section>') % (
        row['version'], e(STATES[row['state']]), e(t.get('portee') or 'à qualifier'), moves, rows, e(t['id'], quote=True), e(row['text']))
