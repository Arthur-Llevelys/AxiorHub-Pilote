"""5.6.97 (J04) : fonds « Droit fédéral allemand » (Gesetze im Internet), lu par mcp_internationaux5697.

Format officiel (documentation publique, DTD https://www.gesetze-im-internet.de/dtd/1.01/gii-norm.dtd et
https://www.gesetze-im-internet.de/hinweise.html) : racine ``dokumente`` (attributs ``builddate``, ``doknr``) contenant des ``norm`` ;
``metadaten`` porte ``jurabk`` (abréviation), ``langue`` (titre long), ``ausfertigung-datum``, ``fundstelle`` (``periodikum``,
``zitstelle``), ``enbez`` (« § 433 »), ``titel`` et ``standangabe`` ; ``textdaten/text/Content/P`` porte les alinéas. Une archive
``/<abréviation>/xml.zip`` par loi.

Version : la consolidation du site n'est datée que par ``builddate`` ; elle n'est donc prouvée qu'à partir de cette date. Aucun état
historique n'est attribué au texte courant (date utile antérieure = « Piste à vérifier »). La publication fédérale (``fundstelle``)
est conservée à part : la consolidation n'est pas le texte de promulgation.
"""
import re

from .common import Stop

PAYS = 'DE'
ZONES = ('DE-BUND',)
LANGUES = ('de',)
HOTES = ('www.gesetze-im-internet.de',)
IDENT_RE = r'[A-Za-z0-9][A-Za-z0-9_\-]{0,39}'
ACCEPT = 'application/zip, application/xml'
MAX_OCTETS = 30_000_000
SYNC_QUALIFIEE = True
BASE = 'https://www.gesetze-im-internet.de'


def urls_document(ident):
    if not re.fullmatch(IDENT_RE, ident):
        raise Stop('identifiant_source_invalide')
    return ['%s/%s/xml.zip' % (BASE, ident.lower())]


def _numero(enbez):
    m = re.search(r'(\d+[a-z]?)', str(enbez or ''))
    return m.group(1) if m else ''


def analyser(contenu, ident):
    from .mcp_internationaux5697 import jour, texte, xml_sur, zip_sur
    ident = str(ident or '').lower()
    if not re.fullmatch(IDENT_RE, ident):
        raise Stop('identifiant_source_invalide')
    fichiers = zip_sur(contenu) if bytes(contenu[:2]) == b'PK' else [('document.xml', contenu)]
    normes = []
    for _, raw in fichiers:
        root = xml_sur(raw)
        if root.tag != 'dokumente':
            raise Stop('format_source_inattendu')
        stand = jour(root.get('builddate'))
        if not stand:
            raise Stop('date_consolidation_absente')            # sans date de consolidation, aucune version n'est prouvée
        tete = {'identifiant': ident, 'abreviation': '', 'titre': '', 'langue': 'de', 'zone': 'DE-BUND', 'nature': 'consolide',
                'lien': '%s/%s/index.html' % (BASE, ident), 'publication': '', 'a_jour_au': stand, 'articles': []}
        for norm in root.findall('norm'):
            meta = norm.find('metadaten')
            if meta is None:
                continue
            enbez = (meta.findtext('enbez') or '').strip()
            if not tete['abreviation'] and meta.findtext('jurabk'):
                tete['abreviation'] = (meta.findtext('amtabk') or meta.findtext('jurabk')).strip()
            if not tete['titre'] and meta.findtext('langue'):
                tete['titre'] = meta.findtext('langue').strip()
                fund = meta.find('fundstelle')
                if fund is not None:
                    tete['publication'] = ' '.join(x for x in ((fund.findtext('periodikum') or '').strip(), (fund.findtext('zitstelle') or '').strip()) if x)
                stand_txt = ' '.join(texte(s.find('standkommentar')) for s in meta.findall('standangabe') if s.find('standkommentar') is not None)
                tete['a_jour_au'] = stand + ((' — ' + stand_txt[:300]) if stand_txt else '')
            numero = _numero(enbez)
            if not enbez or not numero:
                continue
            contenu_art = norm.find('textdaten/text/Content')
            alineas = [texte(p) for p in contenu_art.findall('P')] if contenu_art is not None else []
            corps = '\n'.join(a for a in alineas if a)
            if not corps:
                continue
            tete['articles'].append({'article': enbez, 'titre': (meta.findtext('titel') or '').strip(), 'texte': corps, 'debut': stand, 'fin': '',
                                     'version': 'Consolidation Gesetze im Internet du %s' % stand, 'lien': '%s/%s/__%s.html' % (BASE, ident, numero)})
        if not tete['titre']:
            tete['titre'] = tete['abreviation'] or ident.upper()
        normes.append(tete)
    return normes


def analyser_synchronisation(documents, ident):
    normes = []
    for raw in documents:
        normes += analyser(raw, ident)
    return normes


def couvre(zone, zone_norme, etendue):
    return zone == zone_norme


def identifiant_officiel(norme, article):
    return '%s § %s' % (norme['abreviation'] or norme['identifiant'].upper(), article['cle'])


def reference(norme, article):
    return '§ %s %s (consolidation Gesetze im Internet du %s)' % (article['cle'], norme['abreviation'] or norme['identifiant'].upper(), article['debut'])
