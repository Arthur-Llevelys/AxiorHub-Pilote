"""5.6.97 (J05) : fonds « Canada — Québec » (lois et règlements du Québec, Éditeur officiel ; fiche LégisQuébec liée).

Constat (documentation publique lue le 11 novembre 2026) : le jeu « Lois et règlements codifiés du Québec » de Données Québec est
publié en archives XML sans description publique du schéma, sous licence CC BY-NC-ND 4.0 (usage non commercial). Le format officiel
ne peut donc pas être reproduit sans téléchargement, interdit dans cette version. Conséquences :
- la synchronisation réseau de ce fonds est FERMÉE (``SYNC_QUALIFIEE = False``) jusqu'à qualification du format et de la licence ;
- l'index se charge seulement depuis un instantané local au format ``axiorhub-legisquebec-1`` décrit ci-dessous, qui reprend la
  présentation publique de LégisQuébec (identifiant ``lc/CCQ-1991``, « À jour au », numéro d'article ``se:1457``, notes de source
  « 1991, c. 64, a. 1457 ») ; ce n'est PAS le XML de l'Éditeur officiel.

    <instantane format="axiorhub-legisquebec-1" langue="fr" a-jour-au="AAAA-MM-JJ">
      <loi identifiant="CCQ-1991" titre="Code civil du Québec">
        <article numero="1457" en-vigueur-depuis="AAAA-MM-JJ" source="1991, c. 64, a. 1457">texte</article>
      </loi>
    </instantane>

Ce fonds ne couvre pas le droit fédéral (fonds_cafed5697) : les résultats CA-FED et CA-QC restent séparés.
"""
import re

from .common import Stop

PAYS = 'CA'
ZONES = ('CA-QC',)
LANGUES = ('fr', 'en')
HOTES = ('www.legisquebec.gouv.qc.ca',)
IDENT_RE = r'[A-Z][A-Z0-9]{0,9}(-[0-9]{1,4}(\.[0-9]{1,3}){0,3})?(, r\. [0-9.]{1,12})?'
ACCEPT = 'application/xml'
MAX_OCTETS = 20_000_000
SYNC_QUALIFIEE = False
SYNC_RAISON = 'Format XML du jeu Données Québec non documenté publiquement et licence CC BY-NC-ND 4.0 : à qualifier avant toute synchronisation.'
BASE = 'https://www.legisquebec.gouv.qc.ca'


def urls_document(ident):
    raise Stop('synchronisation_a_qualifier')


def analyser(contenu, ident=''):
    from .mcp_internationaux5697 import jour, texte, xml_sur
    root = xml_sur(contenu, MAX_OCTETS)
    if root.tag != 'instantane' or root.get('format') != 'axiorhub-legisquebec-1':
        raise Stop('format_source_inattendu')
    langue = root.get('langue', '')
    if langue not in LANGUES:
        raise Stop('langue_source_inattendue')
    a_jour = jour(root.get('a-jour-au'))
    normes = []
    for loi in root.findall('loi'):
        identifiant = loi.get('identifiant', '')
        if not re.fullmatch(IDENT_RE, identifiant) or (ident and identifiant != ident):
            raise Stop('identifiant_source_invalide')
        segment = '%s/%s/document/%s/%s' % (BASE, langue, 'lc' if langue == 'fr' else 'cs', identifiant)
        norme = {'identifiant': identifiant, 'abreviation': '', 'titre': loi.get('titre', '') or identifiant, 'langue': langue, 'zone': 'CA-QC',
                 'nature': 'consolide', 'lien': segment, 'publication': '', 'a_jour_au': a_jour, 'articles': []}
        for art in loi.findall('article'):
            numero = art.get('numero', '').strip()
            debut = jour(art.get('en-vigueur-depuis'))
            corps = texte(art)
            if not numero or not debut or not corps:
                continue
            norme['articles'].append({'article': numero, 'titre': '', 'texte': corps, 'debut': debut, 'fin': '',
                                      'version': 'LégisQuébec, à jour au %s (source : %s)' % (a_jour or '?', art.get('source', '')[:120]),
                                      'lien': '%s#se:%s' % (segment, numero)})
        normes.append(norme)
    return normes


def analyser_synchronisation(documents, ident):
    raise Stop('synchronisation_a_qualifier')


def couvre(zone, zone_norme, etendue):
    return zone == zone_norme


def identifiant_officiel(norme, article):
    return 'RLRQ, c. %s, art. %s' % (norme['identifiant'], article['cle']) if norme['langue'] == 'fr' else \
        'CQLR, c. %s, s. %s' % (norme['identifiant'], article['cle'])


def reference(norme, article):
    return '%s, art. %s (%s)' % (norme['titre'], article['cle'], article['version'])
