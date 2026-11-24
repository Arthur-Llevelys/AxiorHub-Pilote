"""Contrats locaux de qualité, sans moteur tiers installé ni réseau.

Ces utilitaires ne garantissent ni anonymat ni vérité juridique. Ils servent au
pipeline existant et ne créent aucun second registre de pseudonymes ou d'index.
"""
import hashlib
import json
import re
from urllib.parse import urlsplit
from .common import Stop


def cle_cache(*,perimetre_id,source_sha,version_extracteur,version_options,revision_droits,
              revision_entites,revision_politique,modele_version='',date_droit=''):
    required=(perimetre_id,source_sha,version_extracteur,version_options,revision_droits,revision_entites,revision_politique)
    if not all(isinstance(x,str) and x for x in required) or not re.fullmatch('[0-9a-f]{64}',source_sha):
        raise Stop('cle_cache_qualite_incomplete')
    return hashlib.sha256(json.dumps([*required,modele_version,date_droit],
        ensure_ascii=False,separators=(',',':')).encode()).hexdigest()


def reunir_zones(texte,zones,*,maximum=10000):
    """Offsets Python Unicode [début,fin[ ; le même texte doit être utilisé.

    Un détecteur local fournit ses zones, les règles existantes les complètent.
    Les chevauchements sont réunis ; le registre pseudo540 attribue les jetons.
    Aucun verdict de non-sensibilité n'est produit lorsque la liste est vide.
    """
    if not isinstance(texte,str) or type(maximum) is not int or not 1<=maximum<=10000:
        raise Stop('zones_pseudo_invalides')
    if not isinstance(zones,(list,tuple)) or len(zones)>maximum:raise Stop('zones_pseudo_invalides')
    valid=[]
    for z in zones:
        if (not isinstance(z,dict) or type(z.get('start')) is not int or type(z.get('end')) is not int
                or not 0<=z['start']<z['end']<=len(texte)):
            raise Stop('offset_pseudo_invalide')
        valid.append((z['start'],z['end']))
    out=[]
    for a,b in sorted(valid):
        if out and a<out[-1]['end']:out[-1]['end']=max(out[-1]['end'],b)
        else:out.append({'start':a,'end':b})
    return out


def citation_verifiee(record, *, texte_original, domaines_autorises):
    """Correspondance du passage seulement ; dates et droit restent à contrôler.

    record vient de l'adaptateur, pas d'une citation inventée par le modèle.
    Ne fabrique ni ELI ni ECLI quand le fournisseur ne les renseigne pas.
    """
    if not isinstance(record,dict):raise Stop('citation_source_absente')
    for key in ('source_id','source_url','passage','version'):
        if not isinstance(record.get(key),str) or not record[key].strip():raise Stop('citation_source_incomplete')
    u=urlsplit(record['source_url'])
    if (u.scheme!='https' or u.hostname not in domaines_autorises or u.username or u.password or u.query):
        raise Stop('citation_url_non_qualifiee')
    norm=lambda s:re.sub(r'\s+',' ',s).strip()
    if not isinstance(texte_original,str) or norm(record['passage']) not in norm(texte_original):
        raise Stop('citation_passage_non_retrouve')
    out={k:record[k] for k in ('source_id','source_url','passage','version')}
    for key in ('eli_uri','ecli'):
        if record.get(key):out[key]=record[key]
    out['etat']='passage_retrouve_dans_la_source'
    out['sha256_source']=hashlib.sha256(texte_original.encode()).hexdigest()
    return out


# ------------------------------------------------------------------------------------------------ 5.6.109 (lot Q, G22) : raccordement
def _norm(s):
    return re.sub(r'\s+',' ',str(s or '')).strip().casefold()


def passage_relu(passage,texte_source,*,minimum=12):
    """Vrai seulement si le passage cité figure dans le texte source relu (espaces et casse normalisés ; guillemets typographiques
    rapprochés). Un passage trop court (moins de ``minimum`` caractères utiles) n'est jamais réputé relu : il prouverait trop peu."""
    rapproche=lambda s:_norm(s).replace('’',"'").replace('«','"').replace('»','"').replace(' ',' ').strip(' "')
    p,t=rapproche(passage),rapproche(texte_source)
    return len(p)>=minimum and p in t


def etat_citation(passage,texte_source,*,source_lisible,version_attendue,version_courante):
    """État d'une citation avant son affichage : ``verifiee`` seulement si la source est lisible par l'acteur, que sa version n'a pas
    changé depuis l'extrait ET que le passage cité a été relu dans ce texte ; sinon ``a_verifier`` avec le motif. Ne dit rien de la portée
    juridique ni de l'applicabilité (contrôles distincts)."""
    if not source_lisible:
        return {'etat':'a_verifier','motif':'acces_non_autorise'}
    if not version_attendue or version_attendue!=version_courante:
        return {'etat':'a_verifier','motif':'source_modifiee'}
    if not passage_relu(passage,texte_source):
        return {'etat':'a_verifier','motif':'passage_non_retrouve'}
    return {'etat':'verifiee','motif':''}


def pseudonymiser_zones(ps,texte,zones,categorie='PERSONNE'):
    """Zones d'un détecteur local facultatif → marqueurs du MÊME registre ``pseudo540`` (instance ``Pseudonymizer`` de la requête) :
    aucun second système de marqueurs. Les zones sont réunies (``reunir_zones``) puis remplacées de la fin vers le début. Une liste vide
    ne prouve aucune absence de donnée personnelle (texte rendu tel quel)."""
    from .pseudo540 import CATEGORIES
    if categorie not in CATEGORIES or not hasattr(ps,'token'):raise Stop('categorie_pseudo_invalide')
    out=texte
    for z in reversed(reunir_zones(texte,zones)):
        original=texte[z['start']:z['end']]
        if original.strip():out=out[:z['start']]+ps.token(categorie,original)+out[z['end']:]
    return out
