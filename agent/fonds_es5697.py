"""5.6.97 (J04) : fonds « Législation de l'État espagnol » (API Open Data du BOE, legislación consolidada).

Format officiel (documentation publique https://www.boe.es/datosabiertos/api/api.php et APIconsolidada.pdf) : enveloppe
``response/status`` + ``data`` ; ``data/metadatos`` (``identificador``, ``titulo``, ``ambito``, ``fecha_publicacion``,
``fecha_actualizacion``, ``url_eli``, ``url_html_consolidada``…) ; ``data/texto/bloque`` (``id``, ``tipo``, ``titulo``) contenant des
``version`` (``id_norma`` = norme modificatrice, ``fecha_publicacion``, ``fecha_vigencia``) faites de ``p class=...``.
Adresses : ``/datosabiertos/api/legislacion-consolidada/id/{id}/metadatos`` et ``/texto``. Toutes les dates en AAAAMMJJ.

Le texte original (version publiée par la norme elle-même) et la consolidation restent distincts ; chaque version a sa date de
vigueur. Seul le champ « Estatal » entre dans ce fonds : une norme autonomique est refusée, jamais déclarée sans essai.
Réutilisation : citer la source (Agencia Estatal Boletín Oficial del Estado) et la date de mise à jour.
"""
import re

from .common import Stop

PAYS = 'ES'
ZONES = ('ES-ETAT',)
LANGUES = ('es',)
HOTES = ('www.boe.es',)
IDENT_RE = r'BOE-A-[0-9]{4}-[0-9]{1,10}'
ACCEPT = 'application/xml'
MAX_OCTETS = 8_000_000
SYNC_QUALIFIEE = True
API = 'https://www.boe.es/datosabiertos/api/legislacion-consolidada'


def url_texte_boe(ident):
    if not re.fullmatch(IDENT_RE, str(ident)):
        raise Stop('identifiant_boe_invalide')
    return '%s/id/%s/texto' % (API, ident)


def urls_document(ident):
    texte_url = url_texte_boe(ident)
    return ['%s/id/%s/metadatos' % (API, ident), texte_url]


def _ok(root):
    from .mcp_internationaux5697 import texte
    if root.tag != 'response':
        raise Stop('format_source_inattendu')
    code = texte(root.find('status/code'))
    if code != '200':
        raise Stop(('source_officielle_erreur_%s' % re.sub(r'[^0-9a-z]', '', code or 'inconnue'))[:60])
    data = root.find('data')
    if data is None:
        raise Stop('format_source_inattendu')
    return data


def _corps(version):
    from .mcp_internationaux5697 import texte
    lignes = []
    for p in version.iter('p'):
        classe = p.get('class', '')
        if classe in ('articulo', 'nota_pie'):
            continue
        t = texte(p)
        if t:
            lignes.append(t)
    return '\n'.join(lignes)


def _normes(metadatos, texto, ident):
    from .mcp_internationaux5697 import hote_officiel, jour, texte
    if metadatos is None or texto is None:
        raise Stop('format_source_inattendu')
    if texte(metadatos.find('identificador')) != ident:
        raise Stop('identifiant_boe_incoherent')
    ambito = texte(metadatos.find('ambito'))
    if ambito and 'estatal' not in ambito.lower():
        raise Stop('norme_hors_fonds_etat')                      # communautés autonomes : jamais rangées dans ES-ETAT
    eli = texte(metadatos.find('url_eli'))
    lien = texte(metadatos.find('url_html_consolidada'))
    if not hote_officiel(lien, HOTES):
        lien = 'https://www.boe.es/buscar/act.php?id=' + ident
    norme = {'identifiant': ident, 'abreviation': '', 'titre': texte(metadatos.find('titulo')) or ident, 'langue': 'es', 'zone': 'ES-ETAT',
             'nature': 'consolidado', 'lien': lien, 'eli': eli if hote_officiel(eli, HOTES) else '',
             'publication': 'BOE du %s' % jour(texte(metadatos.find('fecha_publicacion'))),
             'a_jour_au': jour(texte(metadatos.find('fecha_actualizacion'))), 'articles': []}
    for bloque in texto.iter('bloque'):
        if bloque.get('tipo') != 'precepto':
            continue
        for version in bloque.findall('version'):
            debut = jour(version.get('fecha_vigencia')) or jour(version.get('fecha_publicacion'))
            corps = _corps(version)
            if not corps or not debut:
                continue
            modificatrice = version.get('id_norma', '')
            norme['articles'].append({'article': bloque.get('titulo', ''), 'titre': bloque.get('titulo', ''), 'texte': corps, 'debut': debut, 'fin': '',
                                      'version': '%s, en vigueur depuis le %s' % (modificatrice, debut), 'original': modificatrice == ident,
                                      'lien': 'https://www.boe.es/buscar/act.php?id=%s#%s' % (ident, re.sub(r'[^A-Za-z0-9_-]', '', bloque.get('id', '')))})
    return [norme]


def analyser(contenu, ident):
    """Réponse complète ``/id/{id}`` (metadatos et texto dans ``data``)."""
    from .mcp_internationaux5697 import xml_sur
    if not re.fullmatch(IDENT_RE, str(ident)):
        raise Stop('identifiant_boe_invalide')
    data = _ok(xml_sur(contenu, MAX_OCTETS))
    return _normes(data.find('metadatos'), data.find('texto'), ident)


def analyser_synchronisation(documents, ident):
    """Deux réponses : ``/metadatos`` puis ``/texto``."""
    from .mcp_internationaux5697 import xml_sur
    if len(documents) != 2:
        raise Stop('format_source_inattendu')
    meta = _ok(xml_sur(documents[0], MAX_OCTETS))
    texto = _ok(xml_sur(documents[1], MAX_OCTETS))
    return _normes(meta.find('metadatos') if meta.find('metadatos') is not None else meta,
                   texto.find('texto') if texto.find('texto') is not None else texto, ident)


def couvre(zone, zone_norme, etendue):
    return zone == zone_norme


def identifiant_officiel(norme, article):
    return '%s, art. %s' % (norme['identifiant'], article['cle'])


def reference(norme, article):
    return '%s, artículo %s (%s)' % (norme['titre'], article['cle'], article['version'])
