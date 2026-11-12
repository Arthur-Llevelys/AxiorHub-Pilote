"""5.6.97 (J07 « Citation », lot sources) : forme normalisée d'une référence juridique, quelle que soit la source qui l'a proposée.

Champs : pays, sous-zone, identifiant officiel et son type (CELEX, ECLI, ELI, BOE, Légifrance, citation neutre), lien, langue, version,
date utile, extrait, statut et raison. Règles :
- le fournisseur qui répond ne décide pas seul qu'une référence est citable : « citable » exige une vérification sur la source officielle
  (autorité vérifiée par ``legal_research.verify_official_decision``, ou relecture d'un adaptateur intégré qui prouve la version à la date
  utile, ``mcp_internationaux5697.citation``) ;
- le lien n'est retenu que s'il pointe vers un hôte officiel de la zone (contrôle d'origine par adaptateur et par zone) ;
- source indisponible, zone ambiguë, identifiant officiel absent, traduction non officielle seule : « Piste à vérifier », avec la raison.
Aucune lecture réseau ici.
"""
import re
from urllib.parse import urlsplit

STATUT_CITABLE, STATUT_PISTE = 'citable', 'piste_a_verifier'
HOTES = {
    'FR': ('legifrance.gouv.fr', 'www.legifrance.gouv.fr', 'courdecassation.fr', 'www.courdecassation.fr', 'judilibre.courdecassation.fr',
           'conseil-etat.fr', 'www.conseil-etat.fr', 'conseil-constitutionnel.fr', 'www.conseil-constitutionnel.fr', 'justice.fr', 'www.justice.fr'),
    'UE': ('eur-lex.europa.eu', 'curia.europa.eu', 'publications.europa.eu'),
    'CEDH': ('hudoc.echr.coe.int',),
    'DE-BUND': ('www.gesetze-im-internet.de',),
    'ES-ETAT': ('www.boe.es',),
    'GB-EAW': ('www.legislation.gov.uk', 'caselaw.nationalarchives.gov.uk'),
    'GB': ('www.legislation.gov.uk', 'caselaw.nationalarchives.gov.uk'),
    'CA-FED': ('laws-lois.justice.gc.ca',),
    'CA-QC': ('www.legisquebec.gouv.qc.ca',),
}
PAYS = {'FR': 'FR', 'UE': 'UE', 'CEDH': 'CEDH', 'DE-BUND': 'DE', 'ES-ETAT': 'ES', 'GB-EAW': 'GB', 'GB': 'GB', 'CA-FED': 'CA', 'CA-QC': 'CA'}
LANGUES = {'FR': ('fr',), 'DE-BUND': ('de',), 'ES-ETAT': ('es',), 'GB-EAW': ('en', 'cy'), 'GB': ('en', 'cy'), 'CA-FED': ('fr', 'en'),
           'CA-QC': ('fr', 'en'), 'CEDH': ('fr', 'en'),
           'UE': ('bg', 'es', 'cs', 'da', 'de', 'et', 'el', 'en', 'fr', 'ga', 'hr', 'it', 'lv', 'lt', 'hu', 'mt', 'nl', 'pl', 'pt', 'ro', 'sk', 'sl', 'fi', 'sv')}
MOTIFS = (('ecli', r'\bECLI:[A-Z]{2}:[A-Z0-9.]{1,10}:[0-9]{4}:[A-Z0-9.:]{1,40}'),
          ('celex', r'\b[0-9CE][0-9]{4}[A-Z]{1,2}[0-9]{4}(?:\([0-9]{1,2}\))?\b'),
          ('boe', r'\bBOE-[A-Z]-[0-9]{4}-[0-9]{1,10}\b'),
          ('legifrance', r'\b(?:LEGIARTI|LEGITEXT|JORFTEXT|JORFARTI|JURITEXT|CETATEXT|CONSTEXT|KALITEXT)[0-9]{12}\b'),
          ('citation_neutre', r'\[[0-9]{4}\] (?:UKSC|UKPC|EWCA (?:Civ|Crim)|EWHC|UKUT|EWFC) [0-9]{1,5}(?: \([A-Za-z ]{2,20}\))?'),
          ('citation_neutre', r'\b[0-9]{4} (?:CSC|SCC|CAF|FCA|CF|FC|QCCA|QCCS|QCCQ) [0-9]{1,6}\b'))


def _hote_officiel(url, zone):
    p = urlsplit(str(url or ''))
    hote = (p.hostname or '').lower()
    return p.scheme == 'https' and not p.username and not p.password and hote in HOTES.get(zone, ())


def identifiant(*textes):
    """Premier identifiant officiel reconnu dans les champs fournis : (type, valeur) ; ELI reconnu dans un lien officiel."""
    for texte in textes:
        texte = str(texte or '')
        if '/eli/' in texte and texte.startswith('https://'):
            return 'eli', texte
        for type_, motif in MOTIFS:
            m = re.search(motif, texte)
            if m:
                return type_, m.group(0)
    return '', ''


def zone_de(fournisseur, zones=None):
    """Zone d'une référence : celle de la demande si elle est unique, sinon celle de la fiche si elle est unique ; '' = ambiguë."""
    zones = [str(z).upper() for z in (zones or ()) if str(z).strip()]
    if len(zones) == 1:
        return zones[0]
    from .sources5632 import PROVIDERS
    territoires = list((PROVIDERS.get(str(fournisseur or '')) or {}).get('territories', ()))
    if zones:
        territoires = [t for t in territoires if t in zones]
    return territoires[0] if len(territoires) == 1 else ''


def normaliser(reference, fournisseur='', zones=None, date_utile=''):
    """Forme normalisée d'une piste, d'une autorité vérifiée ou d'une citation d'adaptateur intégré."""
    ref = reference if isinstance(reference, dict) else {}
    fournisseur = str(fournisseur or ref.get('provider') or ref.get('fonds') or '')
    if ref.get('identifiant_officiel') is not None and ref.get('fonds'):      # citation déjà normalisée par un adaptateur intégré
        return {k: ref.get(k, '') for k in ('pays', 'sous_zone', 'identifiant_officiel', 'lien', 'langue', 'version', 'date_utile', 'extrait',
                                            'statut', 'raison')} | {'type_identifiant': 'adaptateur', 'fournisseur': fournisseur}
    zone = zone_de(fournisseur, zones)
    lien = str(ref.get('official_url') or ref.get('provider_url') or ref.get('url') or '')
    type_, valeur = identifiant(ref.get('ecli'), ref.get('identifier'), ref.get('reference'), ref.get('title'), lien)
    out = {'pays': PAYS.get(zone, ''), 'sous_zone': zone, 'identifiant_officiel': valeur, 'type_identifiant': type_, 'lien': '',
           'langue': str(ref.get('langue') or ref.get('language') or ''), 'version': str(ref.get('version') or ''),
           'date_utile': str(date_utile or ''), 'extrait': str(ref.get('exact_excerpt') or ref.get('summary') or '')[:1200],
           'statut': STATUT_PISTE, 'raison': '', 'fournisseur': fournisseur}
    if zone and _hote_officiel(lien, zone):
        out['lien'] = lien
    verifiee = bool(ref.get('citable') or ref.get('status') == 'verified') and bool(ref.get('official_text_sha256') and ref.get('exact_excerpt'))
    if not zone:
        out['raison'] = 'zone_ambigue'
    elif not valeur:
        out['raison'] = 'identifiant_officiel_absent'
    elif not out['lien']:
        out['raison'] = 'lien_non_officiel'
    elif out['langue'] and out['langue'] not in LANGUES.get(zone, ()):
        out['raison'] = 'traduction_non_officielle'
    elif not verifiee:
        out['raison'] = 'non_verifiee_sur_la_source_officielle'
    else:
        out['statut'], out['raison'] = STATUT_CITABLE, 'verifiee_sur_la_source_officielle'
    return out
