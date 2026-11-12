"""5.6.97 (J05) : fonds « Canada — droit fédéral » (fichiers XML de Justice Canada, lois et règlements consolidés).

Format officiel (documentation publique https://laws-lois.justice.gc.ca/eng/XML/ et fichiers XML d'une loi) : racine ``Statute``
(espace ``lims`` = ``http://justice.gc.ca/lims``, ``xml:lang``, ``lims:pit-date``, ``lims:current-date``, ``lims:lastAmendedDate``,
``lims:inforce-start-date``) ; ``Identification`` (``ShortTitle``, ``LongTitle``, ``Chapter/ConsolidatedNumber``) ; ``Body/Section``
(``MarginalNote``, ``Label``, ``Text``, ``Subsection/Label/Text``), chaque section portant ``lims:inforce-start-date`` et
``lims:lastAmendedDate``. Adresses : ``/eng/XML/{X}.xml`` et ``/fra/XML/{X}.xml``.

Les versions française et anglaise sont importées séparément et reliées par le numéro de consolidation. La version d'une section
n'est prouvée qu'à partir de sa dernière modification connue. Ce fonds ne couvre pas le droit des provinces (Québec : fonds_caqc5697).
Reproduction : décret sur la reproduction de la législation fédérale (SI/97-5) — reproduction non officielle, exactitude à respecter.
Les adresses de section (``/eng/acts/{X}/section-{n}.html``, ``/fra/lois/{X}/article-{n}.html``) suivent la présentation du site ;
elles restent à confirmer en recette.
"""
import re

from .common import Stop

PAYS = 'CA'
ZONES = ('CA-FED',)
LANGUES = ('fr', 'en')
HOTES = ('laws-lois.justice.gc.ca',)
IDENT_RE = r'[A-Z]{1,4}-[0-9]{1,3}(\.[0-9]{1,3})?|SOR-[0-9]{2,4}-[0-9]{1,4}'
ACCEPT = 'application/xml'
MAX_OCTETS = 20_000_000
SYNC_QUALIFIEE = True
BASE = 'https://laws-lois.justice.gc.ca'
LIMS = '{http://justice.gc.ca/lims}'
XML_LANG = '{http://www.w3.org/XML/1998/namespace}lang'


def urls_document(ident):
    if not re.fullmatch(IDENT_RE, ident):
        raise Stop('identifiant_source_invalide')
    return ['%s/fra/XML/%s.xml' % (BASE, ident), '%s/eng/XML/%s.xml' % (BASE, ident)]


def _corps(section):
    from .mcp_internationaux5697 import texte
    lignes = []
    for enfant in section:
        nom = enfant.tag
        if nom == 'Text':
            lignes.append(texte(enfant))
        elif nom == 'Subsection':
            label = texte(enfant.find('Label'))
            lignes.append(' '.join(x for x in (label, ' '.join(texte(t) for t in enfant.iter('Text'))) if x))
    return '\n'.join(x for x in lignes if x)


def analyser(contenu, ident):
    from .mcp_internationaux5697 import jour, texte, xml_sur
    if not re.fullmatch(IDENT_RE, str(ident)):
        raise Stop('identifiant_source_invalide')
    root = xml_sur(contenu, MAX_OCTETS)
    if root.tag not in ('Statute', 'Regulation'):
        raise Stop('format_source_inattendu')
    langue = (root.get(XML_LANG) or '').lower()
    if langue not in LANGUES:
        raise Stop('langue_source_inattendue')
    numero = texte(root.find('Identification/Chapter/ConsolidatedNumber')) or texte(root.find('Identification/InstrumentNumber'))
    if numero != ident:
        raise Stop('identifiant_source_incoherent')
    a_jour = jour(root.get(LIMS + 'current-date')) or jour(root.get(LIMS + 'pit-date'))
    fr = langue == 'fr'
    norme = {'identifiant': ident, 'abreviation': texte(root.find('Identification/ShortTitle')), 'titre': texte(root.find('Identification/ShortTitle'))
             or texte(root.find('Identification/LongTitle')) or ident, 'langue': langue, 'zone': 'CA-FED', 'nature': 'consolide',
             'lien': '%s/%s/%s/index.html' % (BASE, 'fra/lois' if fr else 'eng/acts', ident), 'publication': '', 'a_jour_au': a_jour, 'articles': []}
    for section in root.iter('Section'):
        label = texte(section.find('Label'))
        corps = _corps(section)
        dates = [d for d in (jour(section.get(LIMS + 'inforce-start-date')), jour(section.get(LIMS + 'lastAmendedDate'))) if d]
        if not label or not corps or not dates:
            continue
        debut = max(dates)
        norme['articles'].append({'article': label, 'titre': texte(section.find('MarginalNote')), 'texte': corps, 'debut': debut, 'fin': '',
                                  'version': ('consolidation à jour au %s' if fr else 'consolidation current to %s') % (a_jour or '?'),
                                  'lien': '%s/%s/%s/%s-%s.html' % (BASE, 'fra/lois' if fr else 'eng/acts', ident, 'article' if fr else 'section', label)})
    return [norme]


def analyser_synchronisation(documents, ident):
    normes = []
    for raw in documents:
        normes += analyser(raw, ident)
    return normes


def couvre(zone, zone_norme, etendue):
    return zone == zone_norme


def identifiant_officiel(norme, article):
    return '%s, %s %s' % (norme['identifiant'], 'art.' if norme['langue'] == 'fr' else 's.', article['cle'])


def reference(norme, article):
    return '%s, %s %s (%s)' % (norme['titre'], 'art.' if norme['langue'] == 'fr' else 's.', article['cle'], article['version'])
