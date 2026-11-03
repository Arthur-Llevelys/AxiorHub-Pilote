"""5.6.83 : formulaires officiels : fiche par formulaire, téléchargement depuis la source officielle, remplissage des champs autorisés (cahier complément § 8).

Aucun PDF n'est livré dans le dépôt. La fiche conserve URL officielle, millésime, empreinte, format et applicabilité. Le PDF téléchargé est comparé à
l'empreinte relevée : s'il diffère, il est signalé « version à vérifier » et l'avocat confirme avant usage. La date imprimée ne suffit pas à déclarer un
formulaire périmé. Le remplissage passe par pypdf (copie embarquée), sur une copie déposée dans le dossier ; le vierge n'est jamais modifié.

Formats relevés le 31 octobre 2026 : 2672-SD, 2676-SD et 2759-SD ont de vrais champs ; les autres sont des PDF plats. Pour un PDF plat, aucun Cerfa
approximatif n'est redessiné : une fiche des données à recopier est produite, avec le lien du service officiel guidé quand il existe.

Les noms techniques des champs ne disent rien (« a1 ») : le libellé est retrouvé d'après la position du texte voisin. Le rapprochement se fait d'abord par
mots-clés. Pour les champs restants, le modèle de langage ne reçoit QUE des libellés et des noms de clés, jamais une donnée du dossier."""
from datetime import date
from html import escape as e
from io import BytesIO
import hashlib
import json
from pathlib import Path
import re
from urllib.error import HTTPError, URLError
from urllib.parse import urljoin, urlsplit
from urllib.request import HTTPRedirectHandler, Request, build_opener
import uuid
import zipfile

from .common import Stop, clean_path, fold, load_matters

CATALOGUE = Path(__file__).parent / 'data' / 'formulaires5683.json'
HOSTS = ('www.impots.gouv.fr', 'www.formulaires.service-public.gouv.fr')
MAX_PDF = 20_000_000
SETTING = 'formulaires5683:etat'
KEYS = {'client_nom': 'Nom du client', 'client_adresse': 'Adresse du client', 'client_email': 'Courriel du client', 'adverse_nom': 'Nom de la partie adverse', 'juridiction': 'Juridiction',
        'numero_rg': 'Numéro RG', 'reference_dossier': 'Référence du dossier', 'cabinet_nom': 'Cabinet', 'societe_nom': 'Dénomination de la société', 'societe_forme': 'Forme de la société',
        'societe_siren': 'SIREN', 'societe_siege': 'Siège', 'societe_capital': 'Capital', 'cedant_nom': 'Cédant', 'cessionnaire_nom': 'Acquéreur', 'nombre_titres': 'Nombre de titres cédés',
        'total_titres': 'Nombre total de titres', 'prix_total': 'Prix total', 'date_cession': 'Date de la cession', 'base_taxable': 'Base nette taxable', 'droits': 'Droits calculés'}
PATTERNS = (('societe_siren', r'\bsiren\b'), ('date_cession', r'date de la cession'), ('total_titres', r'nombre total de droits'), ('nombre_titres', r'nombre et numeros des droits'),
            ('societe_siege', r'siege de la societe'), ('societe_nom', r'forme et (denomination|designation)'), ('juridiction', r'juridiction|tribunal'), ('numero_rg', r'\brg\b'),
            ('client_email', r'courriel'), ('client_adresse', r'adresse'))


class _SameHosts(HTTPRedirectHandler):
    """Une redirection hors des sites officiels est refusée."""
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        if urlsplit(newurl).hostname not in HOSTS or urlsplit(newurl).scheme != 'https':
            raise Stop('formulaire_hote_refuse')
        return super().redirect_request(req, fp, code, msg, headers, newurl)


def _get(url, limit=MAX_PDF):
    parts = urlsplit(url)
    if parts.scheme != 'https' or parts.hostname not in HOSTS:
        raise Stop('formulaire_hote_refuse')
    opener = build_opener(_SameHosts)
    try:
        with opener.open(Request(url, headers={'User-Agent': 'Mozilla/5.0 (AxiorHub Pilote)'}), timeout=40) as r:
            data = r.read(limit + 1)
    except (HTTPError, URLError, TimeoutError, OSError):
        raise Stop('formulaire_indisponible') from None
    if len(data) > limit:
        raise Stop('formulaire_trop_volumineux')
    return data


def catalogue():
    return json.loads(CATALOGUE.read_text(encoding='utf-8'))


def form_of(fid):
    form = next((f for f in catalogue()['formulaires'] if f['id'] == fid), None)
    if not form:
        raise Stop('formulaire_inconnu')
    return form


def _pdf_url(form, getter):
    """Fiche impots.gouv.fr : le lien du PDF courant est lu sur la page officielle. Cerfa : l'adresse pérenne du formulaire."""
    if urlsplit(form['page']).hostname != 'www.impots.gouv.fr':
        return form['page']
    html = getter(form['page'], 2_000_000).decode('utf-8', 'replace')
    links = re.findall(r'href="(/sites/default/files/formulaires/[^"]+\.pdf)"[^>]*>\s*(?:<[^>]+>\s*)*([^<]*)', html)
    if not links:
        raise Stop('formulaire_indisponible')
    wanted = 'A1' if form['id'] == '2072-s-a1-sd' else ''
    return urljoin(form['page'], next((a for a, b in links if wanted and wanted in b), links[0][0]))


def _cache(desk, form):
    folder = Path(desk.c['state_dir']) / 'formulaires5683'
    folder.mkdir(parents=True, exist_ok=True)
    return folder / (form['id'] + '.pdf')


def fetch(desk, fid, getter=None):
    """Télécharge le vierge officiel. Une empreinte différente n'est pas une erreur : c'est une version à vérifier, jamais remplacée en silence."""
    form = form_of(fid)
    getter = getter or _get
    raw = getter(_pdf_url(form, getter), MAX_PDF)
    if raw[:5] != b'%PDF-':
        raise Stop('formulaire_indisponible')
    sha = hashlib.sha256(raw).hexdigest()
    _cache(desk, form).write_bytes(raw)
    state = dict(desk.settings(SETTING, {}) or {})
    state[fid] = {'sha256': sha, 'le': date.today().isoformat(), 'etat': 'version_verifiee' if sha == form['sha256'] else 'version_a_verifier', 'confirme_par': ''}
    desk.setting(SETTING, state)
    desk.audit('formulaire5683_telecharge', {'id': fid, 'etat': state[fid]['etat']})
    return state[fid]


def confirm_version(desk, fid, owner):
    state = dict(desk.settings(SETTING, {}) or {})
    if fid not in state:
        raise Stop('formulaire_a_telecharger')
    state[fid] = {**state[fid], 'etat': 'version_confirmee', 'confirme_par': str(owner)}
    desk.setting(SETTING, state)
    desk.audit('formulaire5683_version', {'id': fid, 'owner': owner, 'sha256': state[fid]['sha256'][:16]})
    return {'message': 'Version confirmée pour ce formulaire.'}


def blank(desk, fid):
    """Vierge utilisable : présent et de version vérifiée ou confirmée par l'avocat."""
    form = form_of(fid)
    state = (desk.settings(SETTING, {}) or {}).get(fid)
    path = _cache(desk, form)
    if not state or not path.is_file():
        raise Stop('formulaire_a_telecharger')
    if state['etat'] == 'version_a_verifier':
        raise Stop('formulaire_version_a_verifier')
    raw = path.read_bytes()
    if hashlib.sha256(raw).hexdigest() != state['sha256']:
        raise Stop('formulaire_version_a_verifier')
    return form, raw


def fields(raw):
    """Champs réels, avec le libellé retrouvé d'après la position : texte le plus proche à gauche ou au-dessus du champ."""
    from ._vendor.pypdf import PdfReader
    reader = PdfReader(BytesIO(raw))
    if reader.is_encrypted:
        raise Stop('formulaire_chiffre')
    out, seen = [], set()
    for number, page in enumerate(reader.pages, 1):
        spans = []

        def visit(text, cm, tm, fd, size):
            if text.strip():
                spans.append((float(tm[4]), float(tm[5]), re.sub(r'\s+', ' ', text).strip()))
        page.extract_text(visitor_text=visit)
        for annot in page.get('/Annots') or []:
            widget = annot.get_object()
            if widget.get('/Subtype') != '/Widget':
                continue
            parent = widget.get('/Parent').get_object() if widget.get('/Parent') else {}
            name = _full_name(widget)
            kind = str(widget.get('/FT') or parent.get('/FT') or '')
            if not name or name in seen or kind not in ('/Tx', '/Btn', '/Ch'):
                continue
            seen.add(name)
            rect = [float(x) for x in widget.get('/Rect')]
            tip = str(widget.get('/TU') or parent.get('/TU') or '')
            out.append({'name': name, 'type': {'/Tx': 'texte', '/Btn': 'case', '/Ch': 'choix'}[kind], 'page': number, 'label': (tip or _nearest(spans, rect))[:160]})
    return out


def _full_name(widget):
    parts, node = [], widget
    while node is not None:
        if node.get('/T'):
            parts.append(str(node.get('/T')))
        parent = node.get('/Parent')
        node = parent.get_object() if parent else None
    return '.'.join(reversed(parts))


def _nearest(spans, rect):
    x, y = rect[0], (rect[1] + rect[3]) / 2
    best, score = '', 1e9
    for sx, sy, text in spans:
        if sx > rect[2] or len(text) < 3:
            continue
        dy, dx = abs(sy - y), max(0.0, x - sx)
        if sy < y - 8 or dy > 60:      # le libellé est sur la ligne du champ ou au-dessus, jamais nettement en dessous
            continue
        value = dy * 3 + dx * 0.2
        if value < score:
            best, score = text, value
    return best


def matter_values(desk, mid, extra=None):
    """Données du dossier, par clé connue : une valeur absente reste absente ; rien n'est supposé."""
    from . import modele5651
    matter = next((m for m in load_matters(desk.c) if m['id'] == mid), None)
    if not matter:
        raise Stop('dossier_inconnu')
    out = {'client_nom': matter.get('client_name') or '', 'reference_dossier': matter.get('reference') or matter['id'], 'cabinet_nom': (desk.c.get('cabinet') or {}).get('name') or ''}
    try:
        rows = modele5651.parties(desk, mid)
        adverse = next((p for p in rows if not p.get('client')), None)
        out['adverse_nom'] = adverse['display_name'] if adverse else ''
        ref = modele5651.reference_procedure(desk, mid)
        if ref:
            out.update(juridiction=ref.get('juridiction') or '', numero_rg=ref.get('rg') or '')
    except Exception:   # noqa: BLE001 — fiche du dossier pas encore construite : champs laissés vides
        pass
    out.update({k: str(v) for k, v in (extra or {}).items() if v not in (None, '')})
    return {k: v for k, v in out.items() if str(v).strip()}


def cession_values(desk, did):
    """Données d'une fiche de cession, reprises telles quelles, avec le décompte déterministe des droits."""
    from . import corporate5636 as co, operations5683 as ops
    dos = ops._row(desk, did)
    c = co.company(desk, dos['company'])
    d = dos['data']
    t = ops.taxes(desk, dos)
    price, qty = co._dec(d.get('prix_unitaire')), co._dec(d.get('nombre'))
    return dos['matter'], {'societe_nom': c['denomination'], 'societe_forme': c['forme'], 'societe_siren': c['siren'], 'societe_siege': c['siege'], 'societe_capital': c['capital'], 'cedant_nom': d.get('cedant'),
                           'cessionnaire_nom': d.get('cessionnaire_nom'), 'nombre_titres': d.get('nombre'), 'total_titres': ops.co.holdings(desk, dos['company'])[1] or '',
                           'prix_total': str(price * qty) if price is not None and qty is not None else '', 'date_cession': (d.get('dates') or {}).get('date_signature') or d.get('date_cession'),
                           'droits': t.get('droits') if t['etat'] == 'calcule' else ''}


def propose(rows, values):
    """Rapprochement déterministe par mots-clés : chaque proposition reste à relire ; un champ sans correspondance reste vide."""
    out = {}
    for row in rows:
        label = fold(row['label'])
        key = next((k for k, pattern in PATTERNS if re.search(pattern, label)), '')
        if key and row['type'] == 'texte' and values.get(key):
            out[row['name']] = {'cle': key, 'valeur': values[key], 'origine': 'mots-clés'}
    return out


def ai_mapping(model, rows, known, max_fields=120):
    """Champs non rapprochés : le modèle reçoit seulement les libellés et les noms de clés, jamais une valeur. Réponse hors contrat : une seule réparation,
    puis rien (SK10). Une correspondance vers une clé ou un champ inconnu est ignorée."""
    todo = [{'champ': r['name'], 'libelle': r['label']} for r in rows if r['type'] == 'texte' and r['label'] and r['name'] not in known][:max_fields]
    if not todo:
        return {}
    keys = {k: v for k, v in KEYS.items()}
    system = ('Associe chaque champ d’un formulaire administratif français à une clé de la liste. Réponds uniquement par un objet JSON {"champ": "clé"}. '
              'Laisse de côté tout champ sans clé évidente. N’invente aucune clé.')
    ask = json.dumps({'cles': keys, 'champs': todo}, ensure_ascii=False)
    messages = [{'role': 'system', 'content': system}, {'role': 'user', 'content': ask}]
    names = {t['champ'] for t in todo}
    for attempt in (0, 1):
        raw = str(model.complete(messages, temperature=0, max_tokens=2000) or '')
        try:
            data = json.loads(raw[raw.index('{'):raw.rindex('}') + 1])
            if isinstance(data, dict):
                return {str(k): str(v) for k, v in data.items() if str(k) in names and str(v) in keys}
        except ValueError:
            pass
        messages = messages + [{'role': 'assistant', 'content': raw[:2000]}, {'role': 'user', 'content': 'Réponse illisible : renvoie uniquement l’objet JSON demandé.'}]
    return {}


def fill(raw, values):
    """Copie remplie : seuls les champs existants et autorisés sont écrits ; l'affichage est recalculé par le lecteur. Retourne (octets, écrits, vides)."""
    from ._vendor.pypdf import PdfReader, PdfWriter
    from ._vendor.pypdf.generic import BooleanObject, DictionaryObject, NameObject
    rows = fields(raw)
    known = {r['name']: r for r in rows}
    wanted = {k: str(v) for k, v in values.items() if k in known and known[k]['type'] == 'texte' and str(v).strip()}
    writer = PdfWriter(clone_from=PdfReader(BytesIO(raw)))
    acro = writer._root_object.get('/AcroForm')
    if acro is not None and '/DR' not in acro.get_object():      # formulaire sans table de polices : pypdf en a besoin pour écrire la valeur
        acro.get_object()[NameObject('/DR')] = DictionaryObject()
    for page in writer.pages:
        writer.update_page_form_field_values(page, wanted, auto_regenerate=False)
    writer.set_need_appearances_writer(True)
    if acro is not None:
        acro.get_object()[NameObject('/NeedAppearances')] = BooleanObject(True)
    buffer = BytesIO()
    writer.write(buffer)
    return buffer.getvalue(), sorted(wanted), sorted(r['name'] for r in rows if r['name'] not in wanted)


def data_sheet(form, values):
    """PDF plat : fiche des données à recopier, sans imiter le formulaire. Word, modifiable."""
    from .document_projects import _blank_docx, _docx
    lines = ['# %s — données à recopier' % form['numero'], form['titre'], '',
             'Ce document n’est pas le formulaire officiel : il rassemble les données du dossier pour les recopier dans le formulaire.']
    if form.get('service_guide'):
        lines.append('Service officiel guidé : ' + form['service_guide'])
    lines += ['Formulaire officiel : ' + form['page'], '', '# Données']
    lines += ['%s : %s' % (KEYS.get(k, k), v) for k, v in values.items()] or ['Aucune donnée disponible dans le dossier.']
    lines += ['', 'Toute donnée manquante reste à compléter à la main.']
    return _docx(_blank_docx(), [(x.lstrip('# '), x.startswith('# ')) for x in lines])


def _folder(desk, mid, client):
    matter = next((m for m in load_matters(desk.c) if m['id'] == mid), None)
    if not matter:
        raise Stop('dossier_inconnu')
    folder = matter['path'].rstrip('/') + '/FORMULAIRES'
    client.ensure_folder(folder, matter['path'])
    return folder


def _put(client, path, raw, kind):
    stem, ext = path.rsplit('.', 1)
    for n in range(1, 40):
        target = path if n == 1 else '%s (%d).%s' % (stem, n, ext)
        try:
            client.put_file(target, raw, kind)
            return target
        except Stop as exc:
            if str(exc) != 'http_412':
                raise
    raise Stop('fichier_nom_indisponible')


def _depot(desk, client, form, fid, mid, owner, path, raw, kind):
    """5.6.89 : réservation au registre commun, dépôt exclusif, relecture. Un formulaire préparé reste à relire et n'est déposé nulle part."""
    from . import livrables5689
    sortie = livrables5689.Sortie(desk, 'formulaires5683', '%s:%s' % (fid, mid), uuid.uuid4().hex, matter=mid, title=str(form.get('titre') or form.get('nom') or fid), kind='formulaire', path=path, mime=kind, owner=owner,
                                  provenance={'producteur': 'formulaires5683', 'formulaire': fid, 'millesime': form.get('millesime', '')}, reserve_note='Formulaire à relire avant usage.')
    target = _put(client, path, raw, kind)
    sortie.constater(client, target, raw)
    return target


def generate(desk, data, owner, client=None):
    """Dépose la copie remplie ou la fiche de données dans le dossier. Le vierge n'est pas modifié ; rien n'est déposé à une administration."""
    from .dav import DAV
    fid = str(data.get('formulaire') or '')
    mid = str(data.get('matter') or '')
    form = form_of(fid)
    dav = client or DAV(desk.c.get('nextcloud_documents') or desk.c['nextcloud'])
    extra = {}
    if str(data.get('dossier') or ''):
        mid, extra = cession_values(desk, str(data['dossier']))
    values = matter_values(desk, mid, extra)
    stem = re.sub(r'[\\/:*?"<>|]', '_', '%s - %s' % (date.today().isoformat(), form['numero']))
    if form['format'] == 'plat':
        raw = data_sheet(form, values)
        path = _depot(desk, dav, form, fid, mid, owner, '%s/%s - données à recopier.docx' % (_folder(desk, mid, dav), stem), raw, 'application/vnd.openxmlformats-officedocument.wordprocessingml.document')
        desk.audit('formulaire5683_fiche', {'id': fid, 'matter': mid, 'owner': owner})
        return {'path': path, 'format': 'plat', 'message': 'PDF sans champs : fiche des données à recopier déposée dans le dossier.', 'service_guide': form.get('service_guide', '')}
    form, blank_raw = blank(desk, fid)
    rows = fields(blank_raw)
    chosen = {str(k)[6:]: str(v) for k, v in data.items() if str(k).startswith('champ:')}
    if not chosen:
        chosen = {k: v['valeur'] for k, v in propose(rows, values).items()}
        if str(data.get('ia') or '') in ('1', 'on', 'true', 'True'):      # demandé par l'utilisateur : libellés seulement, puis valeurs du dossier ici
            from .docrequest520 import _model
            for name, key in ai_mapping(_model(desk, mid), rows, chosen).items():
                if values.get(key):
                    chosen[name] = values[key]
    raw, written, empty = fill(blank_raw, chosen)
    path = _depot(desk, dav, form, fid, mid, owner, '%s/%s.pdf' % (_folder(desk, mid, dav), stem), raw, 'application/pdf')
    desk.audit('formulaire5683_rempli', {'id': fid, 'matter': mid, 'owner': owner, 'ecrits': len(written), 'vides': len(empty)})
    return {'path': path, 'format': 'champs', 'ecrits': len(written), 'vides': len(empty), 'message': '%d champ(s) remplis, %d laissés vides : relisez le formulaire avant usage.' % (len(written), len(empty))}


STATES = {'version_verifiee': 'Version vérifiée', 'version_a_verifier': 'Version à vérifier', 'version_confirmee': 'Version confirmée par l’avocat'}


def page_html(desk, prefix, args, role='avocat'):
    cat = catalogue()
    state = desk.settings(SETTING, {}) or {}
    matters = sorted(((m['id'], m.get('client_name') or m['id']) for m in load_matters(desk.c)), key=lambda x: x[1].casefold())
    options = ''.join('<option value="%s">%s</option>' % (e(k, quote=True), e(v)) for k, v in matters)
    cards = ''
    for f in cat['formulaires']:
        st = state.get(f['id']) or {}
        label = STATES.get(st.get('etat'), 'À télécharger')
        ident = '<input type="hidden" name="formulaire" value="%s">' % e(f['id'], quote=True)
        actions = '<form class="m5-form m5-inline" data-api="m5683/formulaire/telecharger" data-reload="1">%s<button class="ax-btn ghost" type="submit">Télécharger depuis le site officiel</button></form>' % ident
        if st.get('etat') == 'version_a_verifier':
            actions += ('<form class="m5-form m5-inline" data-api="m5683/formulaire/confirmer" data-reload="1">%s<button class="ax-btn ghost" type="submit">Confirmer cette version</button></form>' % ident)
        actions += ('<form class="m5-form m5-inline" data-api="m5683/formulaire/generer">%s<label>Dossier<select name="matter" required>%s</select></label>%s<button class="ax-btn" type="submit">%s</button></form>' % (
            ident, options, '' if f['format'] == 'plat' else '<label class="e5636-switch"><input type="checkbox" name="ia" value="1"> Compléter avec l’IA (elle ne reçoit que les libellés)</label>',
            'Préparer la fiche de données' if f['format'] == 'plat' else 'Remplir depuis le dossier'))
        guide = ' · <a href="%s" target="_blank" rel="noopener noreferrer">Service officiel guidé</a>' % e(f['service_guide'], quote=True) if f.get('service_guide') else ''
        cards += ('<li class="ax-card o83-form"><h3>%s <small>%s</small></h3><p>%s</p><p class="ax-muted">%s · %s · relevé le %s · <a href="%s" target="_blank" rel="noopener noreferrer">Fiche officielle</a>%s</p>'
                  '<p class="ax-muted">%s</p>%s</li>') % (
            e(f['numero']), e(' '.join(x for x in (f['cerfa'], f['millesime']) if x)), e(f['titre']), e(label), 'champs remplissables' if f['format'] == 'champs' else 'PDF sans champs : fiche de données à recopier',
            e(f['releve_le']), e(f['page'], quote=True), guide, e(f['applicabilite']), actions)
    missing = ''.join('<li><strong>%s</strong> : %s</li>' % (e(x['demande']), e(x['note'])) for x in cat['non_identifies'])
    return ('<div class="o83"><h1>Formulaires officiels</h1><p class="ax-muted">Remplissage depuis les données du dossier, sur une copie déposée dans le dossier. Relecture obligatoire : rien n’est transmis à '
            'une administration.</p><ul class="o83-forms">%s</ul><section class="ax-card"><h2>Formulaires non identifiés</h2><ul>%s</ul></section></div>') % (cards, missing)
