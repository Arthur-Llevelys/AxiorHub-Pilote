"""5.6.32 (cahier des charges additionnel, R14 / VJ11–VJ14) : contrôle des références juridiques d'un écrit et provenance par paragraphe.

Trois états distincts, jamais confondus :
- ``piste``      : référence repérée dans le texte, non encore confrontée à une source officielle ;
- ``identifiee`` : la source officielle confirme son existence (identifiant, lien) ;
- ``controlee``  : le texte officiel a été lu ; pour un passage cité, la correspondance est vérifiée ; pour un article, la rédaction
                   applicable à la date utile du dossier est comparée à la rédaction actuelle.
Une référence introuvable, une citation altérée ou une source indisponible reste ``a_verifier`` (ou ``anomalie``) avec sa cause :
le contrôle n'invente ni numéro ni texte, et une source en panne n'est jamais présentée comme une vérification réussie.

Le contrôle confirme une identité et un contenu ; il ne prouve ni l'interprétation retenue ni l'applicabilité au dossier (l'avocat
en juge). Toute modification du texte change son empreinte : les contrôles précédents deviennent périmés et sont signalés.
"""
from datetime import datetime, timezone
import difflib
import hashlib
import json
import re

from .common import Stop

CODES = {
    'c. civ.': 'code civil', 'c.civ.': 'code civil', 'code civil': 'code civil',
    'c. trav.': 'code du travail', 'c.trav.': 'code du travail', 'code du travail': 'code du travail',
    'c. com.': 'code de commerce', 'c.com.': 'code de commerce', 'code de commerce': 'code de commerce',
    'c. pén.': 'code pénal', 'c. pen.': 'code pénal', 'code pénal': 'code pénal',
    'cpc': 'code de procédure civile', 'c. pr. civ.': 'code de procédure civile', 'code de procédure civile': 'code de procédure civile',
    'cpp': 'code de procédure pénale', 'c. pr. pén.': 'code de procédure pénale', 'code de procédure pénale': 'code de procédure pénale',
    'c. consom.': 'code de la consommation', 'code de la consommation': 'code de la consommation',
    'cgi': 'code général des impôts', 'code général des impôts': 'code général des impôts',
    'c. assur.': 'code des assurances', 'code des assurances': 'code des assurances',
    'c. séc. soc.': 'code de la sécurité sociale', 'code de la sécurité sociale': 'code de la sécurité sociale',
    'cja': 'code de justice administrative', 'code de justice administrative': 'code de justice administrative',
    'c. urb.': 'code de l’urbanisme', 'code de l’urbanisme': 'code de l’urbanisme', "code de l'urbanisme": 'code de l’urbanisme',
    'cpce': 'code des procédures civiles d’exécution', 'code des procédures civiles d’exécution': 'code des procédures civiles d’exécution',
}
_CODE_ALT = '|'.join(sorted((re.escape(k) for k in CODES), key=len, reverse=True))
ARTICLE = re.compile(r"\b(?:articles?|art\.)\s+(?P<num>[LRDA]\.?\s?\d+(?:[-.]\d+)*|\d+(?:-\d+)*(?:\s(?:bis|ter|quater))?)"
                     r"(?:\s*,?\s*(?:al\.|alinéa)\s*\d+)?\s*(?:,\s*)?(?:du\s+|de\s+la\s+|des\s+|du\s+code\s+)?(?P<code>" + _CODE_ALT + r")",
                     re.IGNORECASE)
LOI = re.compile(r"\b(?:loi|ordonnance|décret)\s+n°\s*(?P<num>\d{2,4}-\d{1,5})", re.IGNORECASE)
POURVOI = re.compile(r"\b(?:pourvoi\s+)?n°\s*(?P<num>\d{2}-\d{2}\.\d{3})")
CE = re.compile(r"\b(?:CE|Conseil d['’]État)\b[^.;\n]{0,80}?n°\s*(?P<num>\d{5,6})")
ECLI = re.compile(r"\bECLI:[A-Z]{2}:[A-Z]+:\d{4}:[A-Z0-9.]+")
QUOTE = re.compile(r"«\s*(?P<q>[^«»]{40,1500}?)\s*»")
STATES = ('piste', 'identifiee', 'controlee', 'a_verifier', 'anomalie')


def _now():
    return datetime.now(timezone.utc).isoformat()


def digest(text):
    return hashlib.sha256(str(text or '').encode('utf-8')).hexdigest()


def paragraphs(text):
    return [p for p in re.split(r'\n\s*\n|\r\n\s*\r\n', str(text or '')) if p.strip()]


def extract(text):
    """Références repérées, avec le paragraphe qui les porte (provenance). Aucune référence n'est complétée ou devinée."""
    refs, seen = [], set()
    for index, para in enumerate(paragraphs(text)):
        found = []
        for m in ARTICLE.finditer(para):
            code = CODES.get(m.group('code').lower().replace('’', '’'), m.group('code').lower())
            num = re.sub(r'\s+', '', m.group('num')).replace('.', '') if m.group('num')[:1].upper() in 'LRDA' else m.group('num').strip()
            found.append({'kind': 'article', 'code': code, 'num': num, 'label': 'art. %s %s' % (num, code)})
        for m in LOI.finditer(para):
            found.append({'kind': 'texte', 'num': m.group('num'), 'label': m.group(0)})
        for m in POURVOI.finditer(para):
            found.append({'kind': 'cassation', 'num': m.group('num'), 'label': 'Cass., n° ' + m.group('num')})
        for m in CE.finditer(para):
            found.append({'kind': 'conseil_etat', 'num': m.group('num'), 'label': 'CE, n° ' + m.group('num')})
        for m in ECLI.finditer(para):
            found.append({'kind': 'ecli', 'num': m.group(0), 'label': m.group(0)})
        for m in QUOTE.finditer(para):
            found.append({'kind': 'citation', 'num': '', 'quote': m.group('q').strip(), 'label': '« ' + m.group('q').strip()[:60] + '… »'})
        for ref in found:
            key = (ref['kind'], ref.get('code', ''), ref['num'], ref.get('quote', ''), index)
            if key in seen:
                continue
            seen.add(key)
            ref.update({'paragraph': index, 'state': 'piste', 'reason': '', 'evidence': {}})
            refs.append(ref)
    return refs


def _json(text):
    try:
        value = json.loads(text)
        return value if isinstance(value, (dict, list)) else {}
    except (ValueError, TypeError):
        return {}


class Checker:
    """Une session MCP vers la première source activée qui déclare les outils nécessaires (JusticeLibre ou autre fournisseur qualifié)."""

    def __init__(self, desk, call=None):
        from . import extensions364 as ext, mcp5614, sources5632
        self.items = [i for i in sources5632.enabled_items(desk) if i.get('preferred_tools')]
        self.item = self.items[0] if self.items else None
        self.session = None
        self.tools = []
        self._call = call
        if self.item:
            self.session = mcp5614.CallSession(self.item, call or ext._mcp_call)
            self.session.initialize()
            self.tools = self.session.list_tools()

    def available(self, capability):
        from .sources5632 import tool_for
        return bool(self.item and tool_for(self.item, capability, self.tools))

    def run(self, capability, args):
        from . import mcp5614
        from .sources5632 import tool_for
        tool = tool_for(self.item, capability, self.tools)
        if not tool:
            raise Stop('capacite_source_indisponible')
        return _json(mcp5614.text_from(self.session.call_tool(tool['name'], args)))


def _similar(a, b):
    norm = lambda s: re.sub(r"[\s'’]+", ' ', str(s or '').lower()).strip()   # noqa: E731
    return round(difflib.SequenceMatcher(None, norm(a), norm(b)).ratio() * 100)


def check_ref(checker, ref, date_utile=''):
    """Contrôle d'une référence ; la cause d'un échec est conservée, jamais masquée."""
    provider = checker.item.get('provider', '') if checker.item else ''
    try:
        if ref['kind'] == 'article':
            if not checker.available('law'):
                return _set(ref, 'piste', 'lecture_article_indisponible')
            now = checker.run('law', {'code': ref['code'], 'num': ref['num']})
            if now.get('error') or not now.get('texte'):
                return _set(ref, 'anomalie', 'article_introuvable', {'provider': provider, 'detail': str(now.get('error', ''))[:200]})
            ev = {'provider': provider, 'official_id': now.get('legiarti', ''), 'url': now.get('source_url', ''), 'etat': now.get('etat', ''),
                  'version_from': now.get('date_debut', ''), 'excerpt': str(now.get('texte', ''))[:600], 'text_sha256': digest(now.get('texte', ''))}
            if date_utile:
                then = checker.run('law', {'code': ref['code'], 'num': ref['num'], 'date': date_utile})
                if then.get('error') or not then.get('texte'):
                    return _set(ref, 'a_verifier', 'article_absent_a_la_date_utile', ev)
                ev['date_utile'] = date_utile
                ev['version_at_date_from'] = then.get('date_debut', '')
                ev['excerpt_at_date'] = str(then.get('texte', ''))[:600]
                if digest(then.get('texte', '')) != ev['text_sha256']:
                    ev['versions_differ'] = True
                    return _set(ref, 'controlee', 'redaction_differente_a_la_date_utile', ev)
            return _set(ref, 'controlee', '', ev)
        if ref['kind'] == 'texte':
            if not checker.available('resolve'):
                return _set(ref, 'piste', 'resolution_indisponible')
            got = checker.run('resolve', {'reference': ref['num']})
            if got.get('error') or not got.get('legitext'):
                return _set(ref, 'anomalie', 'texte_introuvable', {'provider': provider})
            return _set(ref, 'identifiee', '', {'provider': provider, 'official_id': got.get('legitext', ''), 'url': got.get('source_url', ''),
                                                 'title': got.get('titre_texte', '')})
        if ref['kind'] in ('cassation', 'conseil_etat', 'ecli'):
            if not checker.available('case_search') and not checker.available('text'):
                return _set(ref, 'piste', 'recherche_decision_indisponible')
            if ref['kind'] == 'conseil_etat' and checker.available('text'):
                got = checker.run('text', {'numero': ref['num'], 'court': 'CE'})
                if got.get('error') or not (got.get('texte') or got.get('text') or got.get('id')):
                    return _set(ref, 'a_verifier', 'decision_non_trouvee_par_numero', {'provider': provider})
                return _set(ref, 'controlee', '', {'provider': provider, 'official_id': got.get('id', ''), 'url': got.get('source_url', got.get('url', '')),
                                                    'excerpt': str(got.get('texte') or got.get('text') or '')[:400]})
            if not checker.available('case_search'):
                return _set(ref, 'piste', 'recherche_decision_indisponible')
            got = checker.run('case_search', {'query': ref['num'], 'court': 'CASS' if ref['kind'] == 'cassation' else 'JUDICIAIRE'})
            rows = got.get('results', got.get('decisions', [])) if isinstance(got, dict) else got
            wanted = ref['num'].replace('.', '').replace('-', '')
            match = next((r for r in rows or [] if isinstance(r, dict) and wanted in str(r.get('numero', '')).replace('.', '').replace('-', '')), None)
            if not match and ref['kind'] == 'ecli':
                match = next((r for r in rows or [] if isinstance(r, dict) and r.get('ecli') == ref['num']), None)
            if not match:
                return _set(ref, 'a_verifier', 'decision_non_trouvee', {'provider': provider})
            return _set(ref, 'identifiee', '', {'provider': provider, 'official_id': match.get('id', ''), 'ecli': match.get('ecli', ''),
                                                 'title': match.get('titre', ''), 'date': match.get('date', '')})
        if ref['kind'] == 'citation':
            if not checker.available('citation'):
                return _set(ref, 'piste', 'controle_citation_indisponible')
            got = checker.run('citation', {'text': ref['quote'][:3000]})
            origins = got.get('textes_d_origine') or got.get('decisions_d_origine') or []
            best = max((o for o in origins if isinstance(o, dict)), key=lambda o: o.get('correspondance', 0), default=None)
            if not best:
                return _set(ref, 'a_verifier', 'citation_sans_source', {'provider': provider})
            ev = {'provider': provider, 'official_id': best.get('id', ''), 'title': best.get('titre', ''), 'url': best.get('lien', ''),
                  'match_percent': best.get('correspondance', 0), 'differences': best.get('differences', [])[:20]}
            if best.get('correspondance', 0) >= 98:
                return _set(ref, 'controlee', '', ev)
            return _set(ref, 'anomalie', 'citation_alteree', ev)
    except Stop as ex:
        return _set(ref, 'a_verifier', 'source_indisponible:' + str(ex)[:60], {'provider': provider})
    return _set(ref, 'piste', 'type_non_pris_en_charge')


def _set(ref, state, reason='', evidence=None):
    ref['state'], ref['reason'] = state, reason
    ref['evidence'] = evidence or {}
    ref['checked_at'] = _now()
    return ref


def _schema(desk):
    desk.db.executescript('''CREATE TABLE IF NOT EXISTS controles5632(id INTEGER PRIMARY KEY AUTOINCREMENT, owner TEXT NOT NULL, matter TEXT NOT NULL,
        label TEXT NOT NULL, text_sha256 TEXT NOT NULL, date_utile TEXT NOT NULL, provider TEXT NOT NULL, result TEXT NOT NULL, created TEXT NOT NULL);
        CREATE INDEX IF NOT EXISTS controles5632_doc ON controles5632(owner, text_sha256);''')


def control(desk, text, owner='cabinet', matter='', label='', date_utile='', call=None, limit=60):
    """Contrôle complet d'un écrit ; résultat conservé avec l'empreinte du texte (provenance par paragraphe)."""
    text = str(text or '')
    if not text.strip():
        raise Stop('texte_a_controler_vide')
    if len(text) > 400_000:
        raise Stop('texte_a_controler_trop_long')
    if date_utile and not re.fullmatch(r'\d{4}-\d{2}-\d{2}', date_utile):
        raise Stop('date_utile_invalide')
    refs = extract(text)[:limit]
    try:
        checker = Checker(desk, call)
    except Stop as ex:
        checker = None
        for ref in refs:
            _set(ref, 'a_verifier', 'source_indisponible:' + str(ex)[:60])
    if checker is not None:
        if not checker.item:
            for ref in refs:
                _set(ref, 'piste', 'aucune_source_activee')
        else:
            for ref in refs:
                check_ref(checker, ref, date_utile)
    counts = {s: sum(1 for r in refs if r['state'] == s) for s in STATES}
    result = {'text_sha256': digest(text), 'date_utile': date_utile, 'references': refs, 'counts': counts,
              'provider': checker.item.get('provider', '') if checker is not None and checker.item else '', 'checked_at': _now(),
              'scope': 'Identité et contenu des références ; l’interprétation et l’applicabilité au dossier relèvent de l’avocat.'}
    _schema(desk)
    desk.db.execute('INSERT INTO controles5632(owner,matter,label,text_sha256,date_utile,provider,result,created) VALUES(?,?,?,?,?,?,?,?)',
                    (owner, str(matter or ''), str(label or '')[:200], result['text_sha256'], date_utile, result['provider'],
                     json.dumps(result, ensure_ascii=False), result['checked_at']))
    desk.db.commit()
    desk.audit('controle5632_references', {'matter': matter, 'refs': len(refs), 'counts': counts, 'provider': result['provider']})
    return result


def latest(desk, text, owner='cabinet'):
    """Dernier contrôle de CE texte ; un texte modifié n'hérite pas des contrôles de sa version précédente (VJ14)."""
    _schema(desk)
    row = desk.db.execute('SELECT result FROM controles5632 WHERE owner=? AND text_sha256=? ORDER BY id DESC LIMIT 1', (owner, digest(text))).fetchone()
    return json.loads(row['result']) if row else None


def report_html(result):
    """Fiche interne de contrôle (distincte du document destiné au client ou à la juridiction)."""
    from html import escape as e
    from .i18n5631 import th
    if not result['references']:
        return '<p class="c5632-empty">' + th('ctl.none_found') + '</p>'
    rows = ''.join('<tr data-state="%s"><td>%s</td><td>%d</td><td><strong>%s</strong>%s</td><td>%s</td></tr>' % (
        e(r['state']), e(r['label']), r['paragraph'] + 1, th('ctl.state.' + r['state']),
        (' — ' + e(th('ctl.reason.' + r['reason'].split(':')[0]) if r['reason'] else '')) if r['reason'] else '',
        ('<a href="%s" target="_blank" rel="noopener noreferrer">%s</a>' % (e(r['evidence'].get('url', ''), quote=True), e(r['evidence'].get('official_id') or r['evidence'].get('title') or 'source'))
         if r['evidence'].get('url') else e(r['evidence'].get('official_id', ''))) +
        (('<br><small>' + th('ctl.match', n=r['evidence'].get('match_percent')) + '</small>') if 'match_percent' in r['evidence'] else '') +
        (('<br><small>' + th('ctl.versions', then=r['evidence'].get('version_at_date_from', ''), now=r['evidence'].get('version_from', '')) + '</small>')
         if r['evidence'].get('versions_differ') else ''))
        for r in result['references'])
    counts = result['counts']
    return ('<p class="c5632-counts">' + ' · '.join('%s : %d' % (th('ctl.state.' + s), counts.get(s, 0)) for s in STATES) + '</p>'
            '<table class="c5632-table"><thead><tr><th>' + th('ctl.col.ref') + '</th><th>' + th('ctl.col.para') + '</th><th>' + th('ctl.col.state') +
            '</th><th>' + th('ctl.col.source') + '</th></tr></thead><tbody>' + rows + '</tbody></table><p class="c5632-scope">' + th('ctl.scope') + '</p>')
