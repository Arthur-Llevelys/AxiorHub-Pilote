"""5.6.36 : CRM & contacts — clients, parties, confrères, juridictions et experts dans un répertoire relié aux dossiers.

Un contact est une personne ou une société, rangée dans une catégorie (client, partie adverse, confrère…). Il est relié à un ou
plusieurs dossiers avec un rôle propre à chaque dossier (une même société peut être cliente dans un dossier et adverse dans un autre ;
rien n'est fusionné d'office). Le répertoire se remplit :
- à la main (page « Contacts & CRM ») ;
- par l'import des correspondants déjà associés aux dossiers (adresses et rôles) et des parties adverses connues ;
- par les initiatives et le secrétariat juridique (création proposée, validée par l'avocat).
Aucune donnée n'est envoyée à l'extérieur ; aucune fiche n'est supprimée (« Archiver » la retire des listes).
"""
from datetime import datetime, timezone
from html import escape as e
import json
import re
import uuid

from .common import Stop, load_matters, matter_display

CATEGORIES = {'client': 'Client', 'partie_adverse': 'Partie adverse', 'confrere': 'Confrère', 'juridiction': 'Juridiction',
              'commissaire': 'Commissaire de justice', 'expert': 'Expert', 'prospect': 'Prospect', 'autre': 'Autre',
              'a_classer': 'À classer'}                       # 5.6.57 : contacts collectés depuis la boîte, rôle non deviné
ROLES = {'client': 'Client', 'partie_adverse': 'Partie adverse', 'avocat_adverse': 'Avocat de la partie adverse', 'confrere': 'Confrère',
         'juridiction': 'Juridiction', 'commissaire': 'Commissaire de justice', 'expert': 'Expert', 'dirigeant': 'Dirigeant',
         'associe': 'Associé', 'temoin': 'Témoin', 'autre': 'Autre'}
KINDS = {'personne': 'Personne', 'societe': 'Société ou organisme'}
FIELDS = ('kind', 'category', 'name', 'first_name', 'company', 'function', 'email', 'phone', 'address', 'siren', 'barreau', 'notes')
EMAIL = re.compile(r'^[^@\s]{1,64}@[^@\s]{1,190}\.[^@\s]{2,24}$')


def _now():
    return datetime.now(timezone.utc).isoformat(timespec='seconds')


def schema(desk):
    if desk.db.in_transaction:   # 5.6.37 (audit F08) : executescript validerait l'opération en cours ; schéma créé avant BEGIN
        return
    desk.db.executescript('''
    CREATE TABLE IF NOT EXISTS contacts5636(id TEXT PRIMARY KEY, kind TEXT NOT NULL, category TEXT NOT NULL, name TEXT NOT NULL,
      first_name TEXT NOT NULL DEFAULT '', company TEXT NOT NULL DEFAULT '', function TEXT NOT NULL DEFAULT '', email TEXT NOT NULL DEFAULT '',
      phone TEXT NOT NULL DEFAULT '', address TEXT NOT NULL DEFAULT '', siren TEXT NOT NULL DEFAULT '', barreau TEXT NOT NULL DEFAULT '',
      notes TEXT NOT NULL DEFAULT '', source TEXT NOT NULL DEFAULT 'manuel', archived INTEGER NOT NULL DEFAULT 0, created TEXT NOT NULL,
      updated TEXT NOT NULL);
    CREATE INDEX IF NOT EXISTS contacts5636_email ON contacts5636(email);
    CREATE TABLE IF NOT EXISTS contact_links5636(contact TEXT NOT NULL, matter TEXT NOT NULL, role TEXT NOT NULL, created TEXT NOT NULL,
      PRIMARY KEY(contact, matter, role));
    CREATE INDEX IF NOT EXISTS contact_links5636_matter ON contact_links5636(matter);''')


def _clean(data):
    out = {}
    for k in FIELDS:
        v = str(data.get(k) or '').strip()
        if any(ord(c) < 32 and c not in '\n' for c in v):
            raise Stop('contact_caracteres_invalides')
        out[k] = v[:2000] if k in ('notes', 'address') else v[:200]
    out['kind'] = out['kind'] if out['kind'] in KINDS else ('societe' if out['siren'] or (out['company'] and not out['first_name']) else 'personne')
    out['category'] = out['category'] if out['category'] in CATEGORIES else 'autre'
    if not out['name']:
        raise Stop('contact_nom_requis')
    out['email'] = out['email'].lower()
    if out['email'] and not EMAIL.match(out['email']):
        raise Stop('contact_courriel_invalide')
    out['siren'] = re.sub(r'\s', '', out['siren'])
    if out['siren'] and not re.fullmatch(r'\d{9}(\d{5})?', out['siren']):
        raise Stop('contact_siren_invalide')
    return out


def _hidden():
    """Dossiers masqués au lecteur de la requête (dossiers réservés) ; vide hors requête (tâches internes)."""
    try:
        from .membres5650 import viewer_hidden
        return viewer_hidden() or frozenset()
    except Exception:   # noqa: BLE001
        return frozenset()


def _restricted(desk, contact, hidden=None):
    """5.6.56 (audit 5.6.55, N15) : un contact relié à un dossier masqué au lecteur est entièrement soustrait à ce lecteur (fiche,
    listes, modifications, archivage) : ses notes et liens peuvent révéler le dossier. Tous les liens sont examinés, pas le premier."""
    hidden = _hidden() if hidden is None else hidden
    if not hidden:
        return False
    return any(r[0] in hidden for r in desk.db.execute('SELECT matter FROM contact_links5636 WHERE contact=?', (str(contact),)))


def _guard(desk, contact):
    if _restricted(desk, contact):
        raise Stop('dossier_reserve')


def label(c):
    who = ' '.join(x for x in (c.get('first_name', ''), c.get('name', '')) if x).strip()
    return who + ((' (' + c['company'] + ')') if c.get('company') and c.get('kind') == 'personne' else '')


def _check_link(desk, matter, role):
    if matter not in {m['id'] for m in load_matters(desk.c)}:
        raise Stop('dossier_absent')
    if role not in ROLES:
        raise Stop('role_contact_invalide')


def save(desk, data, owner='cabinet', source='manuel', commit=True):
    """5.6.37 (F08) : ``commit=False`` = écriture dans la transaction de l'appelant (opération Corporate), sans validation ni journal.

    5.6.56 (audit 5.6.55, N23) : dossier et rôle du lien contrôlés AVANT toute écriture ; fiche et lien validés ensemble (un refus ne
    laisse aucune fiche à moitié créée). N15 : une fiche reliée à un dossier masqué au lecteur ne peut pas être modifiée par lui."""
    schema(desk)
    clean = _clean(data)
    ident = str(data.get('id') or '')
    role = str(data.get('role') or clean['category'])
    if data.get('matter'):
        _check_link(desk, str(data['matter']), role)
    now = _now()
    try:
        if ident:
            if not desk.db.execute('SELECT 1 FROM contacts5636 WHERE id=?', (ident,)).fetchone():
                raise Stop('contact_absent')
            _guard(desk, ident)
            desk.db.execute('UPDATE contacts5636 SET %s, updated=? WHERE id=?' % ','.join('%s=?' % k for k in FIELDS),
                            [clean[k] for k in FIELDS] + [now, ident])
        else:
            ident = uuid.uuid4().hex
            desk.db.execute('INSERT INTO contacts5636(id,%s,source,created,updated) VALUES(?,%s,?,?,?)' % (','.join(FIELDS), ','.join('?' for _ in FIELDS)),
                            [ident] + [clean[k] for k in FIELDS] + [source, now, now])
        if data.get('matter'):
            link(desk, ident, str(data['matter']), role, commit=False)
    except BaseException:
        if commit:
            desk.db.rollback()
        raise
    if commit:
        desk.db.commit()
        desk.audit('crm5636_contact', {'id': ident, 'category': clean['category'], 'owner': owner})
    return get(desk, ident)


def find_or_create(desk, data, source='initiative', commit=True):
    """Contact existant (même courriel, ou même nom et même catégorie) ou nouveau contact ; renvoie la fiche."""
    schema(desk)
    clean = _clean(data)
    row = None
    if clean['email']:
        row = desk.db.execute('SELECT id FROM contacts5636 WHERE email=? AND archived=0', (clean['email'],)).fetchone()
    if row is None:
        # 5.6.56 (audit 5.6.55, N22) : l'homonymie seule ne fusionne jamais deux adresses différentes ; le rapprochement par nom ne vaut
        # que si l'une des deux fiches n'a pas de courriel.
        row = desk.db.execute("SELECT id FROM contacts5636 WHERE lower(name)=lower(?) AND lower(first_name)=lower(?) AND category=? AND archived=0 "
                              "AND (email='' OR ?='') ORDER BY email='' DESC LIMIT 1",
                              (clean['name'], clean['first_name'], clean['category'], clean['email'])).fetchone()
    if row is not None:
        if data.get('matter'):
            link(desk, row['id'], str(data['matter']), str(data.get('role') or clean['category']), commit=commit)
        return get(desk, row['id'])
    return save(desk, data, source=source, commit=commit)


def link(desk, contact, matter, role, commit=True):
    schema(desk)
    _check_link(desk, matter, role)
    if not desk.db.execute('SELECT 1 FROM contacts5636 WHERE id=?', (contact,)).fetchone():
        raise Stop('contact_absent')
    _guard(desk, contact)
    desk.db.execute('INSERT OR IGNORE INTO contact_links5636 VALUES(?,?,?,?)', (contact, matter, role, _now()))
    if commit:
        desk.db.commit()
    return get(desk, contact)


def unlink(desk, contact, matter, role):
    schema(desk)
    _guard(desk, contact)
    desk.db.execute('DELETE FROM contact_links5636 WHERE contact=? AND matter=? AND role=?', (contact, matter, role))
    desk.db.commit()
    return get(desk, contact)


def archive(desk, contact, value=True):
    schema(desk)
    if not desk.db.execute('SELECT 1 FROM contacts5636 WHERE id=?', (contact,)).fetchone():
        raise Stop('contact_absent')
    _guard(desk, contact)
    desk.db.execute('UPDATE contacts5636 SET archived=?, updated=? WHERE id=?', (1 if value else 0, _now(), contact))
    desk.db.commit()
    return {'id': contact, 'archived': bool(value), 'message': 'Contact archivé (il reste consultable).' if value else 'Contact rétabli.'}


def get(desk, contact):
    schema(desk)
    row = desk.db.execute('SELECT * FROM contacts5636 WHERE id=?', (contact,)).fetchone()
    if not row:
        raise Stop('contact_absent')
    _guard(desk, contact)
    out = dict(row)
    labels = _labels(desk)
    out['links'] = [{'matter': r['matter'], 'matter_label': labels.get(r['matter'], r['matter']), 'role': r['role'], 'role_label': ROLES.get(r['role'], r['role'])}
                    for r in desk.db.execute('SELECT matter,role FROM contact_links5636 WHERE contact=? ORDER BY created', (contact,))]
    out['label'] = label(out)
    return out


def _labels(desk):
    try:
        return {m['id']: matter_display(m) for m in load_matters(desk.c)}
    except Stop:
        return {}


def search(desk, q='', category='', matter='', archived=False, limit=200):
    schema(desk)
    where, params = ['archived=?'], [1 if archived else 0]
    if category in CATEGORIES:
        where.append('category=?')
        params.append(category)
    if matter:
        where.append('id IN (SELECT contact FROM contact_links5636 WHERE matter=?)')
        params.append(matter)
    for word in [w for w in re.split(r'\s+', str(q or '').strip().lower()) if w][:6]:
        where.append("(lower(name||' '||first_name||' '||company||' '||email||' '||phone||' '||siren||' '||notes) LIKE ?)")
        params.append('%' + word.replace('%', '').replace('_', '') + '%')
    rows = desk.db.execute('SELECT * FROM contacts5636 WHERE ' + ' AND '.join(where) + ' ORDER BY lower(name), lower(first_name) LIMIT ?',
                           params + [max(1, min(int(limit), 1000))]).fetchall()
    out = []
    hidden = _hidden()
    for r in rows:
        c = dict(r)
        c['matters'] = [x['matter'] for x in desk.db.execute('SELECT DISTINCT matter FROM contact_links5636 WHERE contact=?', (r['id'],))]
        if hidden and set(c['matters']) & hidden:                # 5.6.56 (N15) : même prédicat que la fiche
            continue
        c['label'] = label(c)
        out.append(c)
    return out


def count(desk, archived=False):
    """Nombre total de contacts visibles (affiché à côté d'un filtre sans résultat)."""
    return len(search(desk, archived=archived, limit=1000))


def for_matter(desk, mid):
    schema(desk)
    if str(mid) in _hidden():
        return []
    rows = desk.db.execute('SELECT c.*, l.role FROM contact_links5636 l JOIN contacts5636 c ON c.id=l.contact WHERE l.matter=? AND c.archived=0 '
                           'ORDER BY l.role, lower(c.name)', (mid,)).fetchall()
    return [{**dict(r), 'label': label(dict(r)), 'role_label': ROLES.get(r['role'], r['role'])} for r in rows]


def import_existing(desk, owner='cabinet'):
    """Correspondants déjà associés aux dossiers (adresse et rôle) et parties adverses connues → contacts reliés (sans doublon)."""
    schema(desk)
    added = linked = 0
    role_map = {'client': ('client', 'client'), 'adverse': ('partie_adverse', 'partie_adverse'), 'confrere': ('confrere', 'avocat_adverse'),
                'avocat_adverse': ('confrere', 'avocat_adverse'), 'juridiction': ('juridiction', 'juridiction'), 'greffe': ('juridiction', 'juridiction'),
                'expert': ('expert', 'expert'), 'huissier': ('commissaire', 'commissaire'), 'commissaire': ('commissaire', 'commissaire'),
                # 5.6.56 (audit 5.6.55, N08) : rôles canoniques des correspondants (Desk.ROLES) ; un partenaire n'est jamais classé adversaire
                'confrere_adverse': ('confrere', 'avocat_adverse'), 'commissaire_justice': ('commissaire', 'commissaire'),
                'prospect': ('prospect', 'autre'), 'tiers': ('autre', 'autre'), 'administration': ('autre', 'autre'),
                'autre_partie': ('autre', 'autre'), 'personnel': ('autre', 'autre')}
    for m in load_matters(desk.c):
        for c in m.get('correspondents') or []:
            email = str(c.get('email') or '').strip().lower()
            if not EMAIL.match(email):
                continue
            category, role = role_map.get(str(c.get('role') or ''), ('autre', 'autre'))
            before = desk.db.execute('SELECT id FROM contacts5636 WHERE email=?', (email,)).fetchone()
            contact = find_or_create(desk, {'name': str(c.get('name') or email.split('@')[0]), 'email': email, 'category': category,
                                            'matter': m['id'], 'role': role}, source='import')
            # 5.6.56 (audit 5.6.55, § 8.2) : un contact IMPORTÉ resté « autre » faute de conversion est reclassé, s'il n'a jamais été
            # corrigé à la main (créé = modifié) ; une catégorie choisie par l'utilisateur n'est jamais remplacée.
            if (before is not None and contact.get('source') == 'import' and contact.get('category') == 'autre' and category != 'autre'
                    and contact.get('created') == contact.get('updated')):
                desk.db.execute("UPDATE contacts5636 SET category=? WHERE id=? AND category='autre' AND created=updated", (category, contact['id']))
                desk.db.commit()
            added += before is None
            linked += 1
        try:
            from .dossiers5632 import adverse_parties
            names = adverse_parties(desk, m['id'])[:9]
        except Exception:   # noqa: BLE001 — dossier sans partie connue
            names = []
        for name in names:
            name = str(name).strip()
            if len(name) < 2:
                continue
            before = desk.db.execute("SELECT 1 FROM contacts5636 WHERE lower(name)=lower(?) AND category='partie_adverse'", (name,)).fetchone()
            find_or_create(desk, {'name': name, 'kind': 'societe' if re.search(r'\b(SAS|SARL|SA|SCI|EURL|SASU|SNC|SELARL|SCP)\b', name) else 'personne',
                                  'category': 'partie_adverse', 'matter': m['id'], 'role': 'partie_adverse'}, source='import')
            added += before is None
            linked += 1
    desk.setting('crm5636:import', _now())
    desk.audit('crm5636_import', {'added': added, 'linked': linked, 'owner': owner})
    return {'added': added, 'linked': linked, 'message': '%d contact(s) ajouté(s), %d lien(s) avec les dossiers.' % (added, linked)}


# ------------------------------------------------------------------------------------------------ pages
def _options(choices, current=''):
    return ''.join('<option value="%s"%s>%s</option>' % (e(k, quote=True), ' selected' if k == current else '', e(v)) for k, v in choices.items())


def _contact_form(c=None, matter_opts=''):
    c = c or {}
    hidden = ('<input type="hidden" name="id" value="%s">' % e(c['id'], quote=True)) if c.get('id') else ''
    def field(name, lab, kind='text', extra=''):
        return '<label>%s<input type="%s" name="%s" value="%s"%s></label>' % (e(lab), kind, name, e(c.get(name, ''), quote=True), extra)
    return ('<form class="m5-form crm-form" data-api="m5636/contacts/enregistrer" data-reload="1">%s'
            '<div class="crm-grid"><label>Type<select name="kind">%s</select></label><label>Catégorie<select name="category">%s</select></label>'
            '%s%s%s%s%s%s%s%s</div><label>Adresse<textarea name="address" rows="2" data-no-dictation>%s</textarea></label>'
            '<label>Notes<textarea name="notes" rows="2">%s</textarea></label>%s'
            '<button class="ax-btn" type="submit">%s</button></form>') % (
        hidden, _options(KINDS, c.get('kind', 'personne')), _options(CATEGORIES, c.get('category', 'client')),
        field('name', 'Nom ou raison sociale', extra=' required maxlength="200"'), field('first_name', 'Prénom'), field('company', 'Société'),
        field('function', 'Fonction'), field('email', 'Courriel', 'email'), field('phone', 'Téléphone', 'tel'), field('siren', 'SIREN / SIRET'),
        field('barreau', 'Barreau (confrère)'), e(c.get('address', '')), e(c.get('notes', '')),
        ('<div class="crm-grid"><label>Relier au dossier<select name="matter"><option value="">— aucun —</option>%s</select></label>'
         '<label>Rôle dans ce dossier<select name="role">%s</select></label></div>' % (matter_opts, _options(ROLES, 'client'))) if matter_opts and not c.get('id') else '',
        'Enregistrer les modifications' if c.get('id') else 'Ajouter le contact')


def page_html(desk, prefix, args):
    schema(desk)
    labels = _labels(desk)
    matter_opts = ''.join('<option value="%s">%s</option>' % (e(k, quote=True), e(v)) for k, v in sorted(labels.items(), key=lambda kv: kv[1].casefold()))
    if args.get('id'):
        try:
            c = get(desk, str(args['id']))
        except Stop:
            return '<p class="notice">Ce contact n’existe pas.</p>'
        links = ''.join('<li><a href="%s">%s</a> — %s <form class="m5-form m5-inline m5-mini" data-api="m5636/contacts/delier" data-reload="1">'
                        '<input type="hidden" name="contact" value="%s"><input type="hidden" name="matter" value="%s"><input type="hidden" name="role" value="%s">'
                        '<button class="ax-btn ghost" type="submit">Retirer</button></form></li>' % (
                            e(prefix + '/dossier?id=' + l['matter'], quote=True), e(l['matter_label']), e(l['role_label']), e(c['id'], quote=True),
                            e(l['matter'], quote=True), e(l['role'], quote=True)) for l in c['links'])
        return ('<p><a href="%s">← Tous les contacts</a></p><h1>%s</h1><p class="ax-muted">%s · %s%s</p>'
                '<section class="ax-card"><h2>Dossiers</h2><ul>%s</ul><form class="m5-form crm-grid" data-api="m5636/contacts/relier" data-reload="1">'
                '<input type="hidden" name="contact" value="%s"><label>Dossier<select name="matter" required>%s</select></label>'
                '<label>Rôle<select name="role">%s</select></label><button class="ax-btn ghost" type="submit">Relier à ce dossier</button></form></section>'
                '<section class="ax-card"><h2>Fiche</h2>%s</section><form class="m5-form m5-inline" data-api="m5636/contacts/archiver" data-reload="1">'
                '<input type="hidden" name="contact" value="%s"><input type="hidden" name="archived" value="%s"><button class="ax-btn ghost" type="submit">%s</button></form>') % (
            e(prefix + '/contacts-crm', quote=True), e(c['label']), e(CATEGORIES.get(c['category'], c['category'])), e(KINDS.get(c['kind'], '')),
            (' · ' + e(c['email'])) if c['email'] else '', links or '<li class="ax-muted">Relié à aucun dossier.</li>', e(c['id'], quote=True),
            matter_opts, _options(ROLES, c['category'] if c['category'] in ROLES else 'autre'), _contact_form(c), e(c['id'], quote=True),
            '0' if c['archived'] else '1', 'Rétablir' if c['archived'] else 'Archiver')
    q, cat, mid = str(args.get('q') or ''), str(args.get('categorie') or ''), str(args.get('dossier') or '')
    rows = search(desk, q, cat, mid)
    total = count(desk) if (q or cat or mid) and not rows else len(rows)
    empty = ('<li class="ax-muted">0 résultat pour ce filtre (%d contact(s) au total). <a href="%s">Réinitialiser les filtres</a></li>' % (
        total, e(prefix + '/contacts-crm', quote=True))) if (q or cat or mid) else '<li class="ax-muted">Aucun contact.</li>'
    chips = ''.join('<a class="crm-chip%s" href="%s">%s</a>' % (' on' if k == cat else '', e(prefix + '/contacts-crm?categorie=' + k, quote=True), e(v))
                    for k, v in (('', 'Tous'),) + tuple(CATEGORIES.items()))
    cards = ''.join('<li><a href="%s"><strong>%s</strong></a> <span class="ax-muted">%s%s%s</span>%s</li>' % (
        e(prefix + '/contacts-crm?id=' + r['id'], quote=True), e(r['label']), e(CATEGORIES.get(r['category'], r['category'])),
        (' · ' + e(r['email'])) if r['email'] else '', (' · ' + e(r['phone'])) if r['phone'] else '',
        ('<br><small>' + e(', '.join(labels.get(x, x) for x in r['matters'][:4])) + ('…' if len(r['matters']) > 4 else '') + '</small>') if r['matters'] else '')
        for r in rows)
    first = '' if desk.settings('crm5636:import', '') else (
        '<div class="notice">Le répertoire est vide ou récent : <form class="m5-form m5-inline" data-api="m5636/contacts/importer" data-reload="1">'
        '<button class="ax-btn" type="submit">Importer les correspondants et parties des dossiers</button></form></div>')
    try:   # 5.6.57 (§ 8.3) : alimentation progressive depuis la boîte mail
        from .crm5657 import panel_html as _crm57
        first += _crm57(desk, prefix)
    except Exception:   # noqa: BLE001
        pass
    return ('<h1>Contacts & CRM</h1><p class="ax-muted">Clients, parties, confrères, juridictions et experts, reliés à chaque dossier avec leur rôle. '
            'Rien n’est envoyé à l’extérieur.</p>%s<form method="get" class="crm-search"><input type="search" name="q" value="%s" placeholder="Nom, société, '
            'courriel, téléphone, SIREN…" aria-label="Rechercher un contact"><select name="dossier" aria-label="Dossier"><option value="">Tous les dossiers</option>%s'
            '</select><input type="hidden" name="categorie" value="%s"><button class="ax-btn" type="submit">Rechercher</button></form><nav class="crm-chips">%s</nav>'
            '<div class="crm-layout"><section class="ax-card"><h2>%d contact(s)</h2><ul class="crm-list">%s</ul></section>'
            '<section class="ax-card"><h2>Nouveau contact</h2>%s<div class="ax-muted"><form class="m5-form m5-inline" data-api="m5636/contacts/importer" '
            'data-reload="1"><button class="ax-btn ghost" type="submit">Importer de nouveau les correspondants des dossiers</button></form></div></section></div>') % (
        first, e(q, quote=True), matter_opts.replace('value="%s"' % e(mid, quote=True), 'value="%s" selected' % e(mid, quote=True)) if mid else matter_opts,
        e(cat, quote=True), chips, len(rows), cards or empty, _contact_form(None, matter_opts))


def matter_block(desk, prefix, mid):
    """Bloc « Intervenants » de la page d'un dossier."""
    try:
        rows = for_matter(desk, mid)
    except Stop:
        return ''
    items = ''.join('<li><a href="%s">%s</a> — %s%s</li>' % (e(prefix + '/contacts-crm?id=' + r['id'], quote=True), e(r['label']), e(r['role_label']),
                                                             (' · ' + e(r['email'])) if r['email'] else '') for r in rows)
    return ('<section class="ax-card crm-matter"><h2>Intervenants</h2><ul>%s</ul><p><a href="%s">Ajouter ou relier un contact</a></p></section>') % (
        items or '<li class="ax-muted">Aucun intervenant enregistré.</li>', e(prefix + '/contacts-crm?dossier=' + mid, quote=True))


def api_post(desk, route, data, owner):
    if route == 'contacts/enregistrer':
        return save(desk, data, owner)
    if route == 'contacts/relier':
        return link(desk, str(data.get('contact') or ''), str(data.get('matter') or ''), str(data.get('role') or 'autre'))
    if route == 'contacts/delier':
        return unlink(desk, str(data.get('contact') or ''), str(data.get('matter') or ''), str(data.get('role') or ''))
    if route == 'contacts/archiver':
        value = data.get('archived', True)
        return archive(desk, str(data.get('contact') or ''), value if isinstance(value, bool) else str(value) in ('1', 'true', 'True', 'on'))
    if route == 'contacts/importer':
        return import_existing(desk, owner)
    return None


def api_get(desk, route, args):
    if route == 'contacts':
        return {'contacts': search(desk, args.get('q', ''), args.get('categorie', ''), args.get('dossier', ''))}
    if route == 'contacts/fiche':
        return get(desk, str(args.get('id') or ''))
    return None
