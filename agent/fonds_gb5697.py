"""5.6.97 (J05) : fonds « Angleterre et pays de Galles » (legislation.gov.uk, Crown Legislation Markup Language).

Format officiel (documentation publique https://www.legislation.gov.uk/developer/formats/xml, /developer/uris, /openapi) : racine
``Legislation`` (espace ``http://www.legislation.gov.uk/namespaces/legislation``), attributs ``DocumentURI``, ``IdURI``,
``RestrictExtent`` (« E+W+S+N.I. »), ``RestrictStartDate`` ; ``ukm:Metadata`` avec ``dc:title``, ``dct:valid`` (date de l'état),
``ukm:PrimaryMetadata`` (``ukm:Year``, ``ukm:Number``, ``ukm:UnappliedEffects``) ; corps ``Primary/Body/.../P1group/Title`` et
``P1`` (``Pnumber``, ``P1para/P2/P2para/Text``), chaque élément pouvant porter ``RestrictExtent``, ``RestrictStartDate`` et
``RestrictEndDate``. Adresses : ``/{type}/{année}/{numéro}/data.xml``, ``/section/{n}``.

Étendue : une disposition n'entre dans « GB-EAW » que si son étendue comprend l'Angleterre ET le pays de Galles ; une règle de tout
le Royaume-Uni peut y figurer, jamais une règle propre à l'Écosse ou à l'Irlande du Nord. Une modification connue non encore
intégrée (``UnappliedEffects``) interdit la mention « Citable ». Licence : Open Government Licence v3.0 (attribution conservée).
"""
import re

from .common import Stop

PAYS = 'GB'
ZONES = ('GB-EAW', 'GB')
LANGUES = ('en',)
HOTES = ('www.legislation.gov.uk',)
IDENT_RE = r'(ukpga|uksi|asp|nia|anaw|asc|mwa|wsi|ssi|nisr|ukla)/[0-9]{4}/[0-9]{1,5}'
ACCEPT = 'application/xml'
MAX_OCTETS = 30_000_000
SYNC_QUALIFIEE = True
BASE = 'https://www.legislation.gov.uk'
NS = {'l': 'http://www.legislation.gov.uk/namespaces/legislation', 'ukm': 'http://www.legislation.gov.uk/namespaces/metadata',
      'dc': 'http://purl.org/dc/elements/1.1/', 'dct': 'http://purl.org/dc/terms/'}


def urls_document(ident):
    if not re.fullmatch(IDENT_RE, ident):
        raise Stop('identifiant_source_invalide')
    return ['%s/%s/data.xml' % (BASE, ident)]


def _https(uri, defaut):
    uri = str(uri or '')
    if uri.startswith('http://www.legislation.gov.uk/'):
        uri = 'https://' + uri[len('http://'):]
    return uri if uri.startswith(BASE + '/') else defaut


def _parties(etendue):
    return {x.strip().upper() for x in str(etendue or '').replace('N.I.', 'NI').split('+') if x.strip()}


def _texte_p1(p1):
    from .mcp_internationaux5697 import texte
    ns = '{%s}' % NS['l']
    lignes = []
    p2s = list(p1.iter(ns + 'P2'))
    if p2s:
        for p2 in p2s:
            numero = texte(p2.find(ns + 'Pnumber'))
            corps = ' '.join(texte(t) for t in p2.iter(ns + 'Text'))
            lignes.append(('(%s) %s' % (numero, corps)) if numero else corps)
    else:
        lignes.append(' '.join(texte(t) for t in p1.iter(ns + 'Text')))
    return '\n'.join(x for x in lignes if x.strip())


def analyser(contenu, ident):
    from .mcp_internationaux5697 import jour, texte, xml_sur
    if not re.fullmatch(IDENT_RE, str(ident)):
        raise Stop('identifiant_source_invalide')
    root = xml_sur(contenu, MAX_OCTETS)
    ns = '{%s}' % NS['l']
    if root.tag != ns + 'Legislation':
        raise Stop('format_source_inattendu')
    meta = root.find('ukm:Metadata', NS)
    titre = texte(meta.find('dc:title', NS)) if meta is not None else ''
    valide = jour(texte(meta.find('dct:valid', NS))) if meta is not None else ''
    non_appliquees = set()
    if meta is not None:
        for effet in meta.iter('{%s}UnappliedEffect' % NS['ukm']):
            for m in re.finditer(r's\.\s*(\d+[A-Z]*)', effet.get('AffectedProvisions', '')):
                non_appliquees.add(m.group(1).lower())
    etendue_racine = root.get('RestrictExtent', '')
    debut_racine = jour(root.get('RestrictStartDate'))
    norme = {'identifiant': ident, 'abreviation': titre, 'titre': titre or ident, 'langue': 'en', 'zone': 'GB', 'nature': 'consolide',
             'lien': '%s/%s' % (BASE, ident), 'publication': '', 'a_jour_au': valide, 'articles': []}
    parents = {c: p for p in root.iter() for c in p}
    for p1 in root.iter(ns + 'P1'):
        numero = texte(p1.find(ns + 'Pnumber'))
        if not numero:
            continue
        groupe = parents.get(p1)
        groupe = groupe if groupe is not None and groupe.tag == ns + 'P1group' else None
        attribut = lambda nom: p1.get(nom) or (groupe.get(nom) if groupe is not None else '') or ''  # noqa: E731
        corps = _texte_p1(p1)
        debut = jour(attribut('RestrictStartDate')) or debut_racine
        if not corps or not debut:
            continue
        norme['articles'].append({'article': 's. ' + numero, 'titre': texte(groupe.find(ns + 'Title')) if groupe is not None else '',
                                  'texte': corps, 'debut': debut, 'fin': jour(attribut('RestrictEndDate')),
                                  'etendue': attribut('RestrictExtent') or etendue_racine,
                                  'version': 'legislation.gov.uk, version du %s' % debut,
                                  'revision': 'modifications_non_appliquees' if numero.lower() in non_appliquees else '',
                                  'lien': _https(p1.get('DocumentURI'), '%s/%s/section/%s' % (BASE, ident, numero))})
    return [norme]


def analyser_synchronisation(documents, ident):
    normes = []
    for raw in documents:
        normes += analyser(raw, ident)
    return normes


def couvre(zone, zone_norme, etendue):
    parties = _parties(etendue)
    if zone == 'GB-EAW':
        return {'E', 'W'} <= parties
    if zone == 'GB':
        return {'E', 'W', 'S', 'NI'} <= parties
    return False


def identifiant_officiel(norme, article):
    return '%s s. %s' % (norme['identifiant'], article['cle'])


def reference(norme, article):
    return '%s, s. %s (étendue %s, %s)' % (norme['titre'], article['cle'], article['etendue'], article['version'])
