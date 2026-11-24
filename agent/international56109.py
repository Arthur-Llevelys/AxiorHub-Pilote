"""Contrats et plans sans réseau pour les services internationaux proposés.

Les preuves sont résolues par les services internes authentifiés, pas par le LLM.
Ce module n'installe aucun adaptateur ni ne délivre d'autorisation d'exécution.
"""
from dataclasses import dataclass
import hashlib
import json
from pathlib import Path
import re
from urllib.parse import urlsplit, quote
from .common import Stop


def catalogue():
    path=Path(__file__).with_name('data')/'services_internationaux56109.json'
    data=json.loads(path.read_text(encoding='utf-8'))
    out={}
    for s in data['services']:
        if s['id'] in out:raise Stop('service_international_duplique')
        for key in ('docs_url','acces_url'):
            u=urlsplit(s[key])
            if u.scheme!='https' or not u.hostname or u.username or u.password:
                raise Stop('lien_international_invalide')
        out[s['id']]=s
    return out


def service(ident):
    try:return catalogue()[ident]
    except KeyError:raise Stop('service_international_inconnu') from None


def champs_connexion(ident):
    """Décrit les champs ; aucune valeur ni clé n'est chargée ou renvoyée."""
    s=service(ident);a=s['auth']
    if a.startswith('cle_'):fields=[{'name':'api_key','secret':True,'required':True}]
    elif a=='aucune':fields=[]
    else:fields=[{'name':'profil_navigateur_ou_contrat','secret':False,'required':True}]
    return {'service_id':ident,'auth':a,'fields':fields,'acces_url':s['acces_url'],
            'docs_url':s['docs_url'],'statut':'a_raccorder_et_verifier',
            'definition_active_par_defaut':s['definition_active_par_defaut']}


def cle_societe(pays, autorite, identifiant):
    """Évite la collision de registres ; ne valide pas l'existence de l'entité."""
    if not all(isinstance(x,str) for x in (pays,autorite,identifiant)):
        raise Stop('identite_societe_a_preciser')
    country=pays.strip().upper();authority=autorite.strip();raw=identifiant.strip()
    if not re.fullmatch(r'[A-Z]{2}(?:-[A-Z0-9]{2,8})?',country) or not authority or not raw:
        raise Stop('identite_societe_a_preciser')
    if len(authority)>200 or len(raw)>200 or any(ord(c)<32 or ord(c)==127 for c in authority+raw):
        raise Stop('identite_societe_invalide')
    return json.dumps([country,authority,raw],ensure_ascii=False,separators=(',',':'))


@dataclass(frozen=True)
class PreuveAcces:
    service_id: str
    revision_connexion: str
    profil_version: str
    capacites: frozenset
    territoires: frozenset
    valide: bool
    licence_analyse_verifiee: bool=False


def preparer(ident,capacite,*,territoire,preuve=None,analyse=False,pause=False,
             reseau_autorise=False,poste_disponible=False):
    """Plan uniquement ; la barrière de pause/droits est relue à l'exécution.

    Le territoire vient du registre ciblé ou du tribunal, pas de la langue ou du
    droit général du dossier. La preuve doit être reconstruite côté service.
    """
    s=service(ident)
    if capacite not in s['capacites']:raise Stop('capacite_non_declaree')
    if pause:return {'etat':'suspendue'}
    if not reseau_autorise:return {'etat':'reseau_non_autorise'}
    if not territoire:return {'etat':'precision_requise','question':'Quel registre ou tribunal est concerné ?'}
    if territoire not in s['territoires']:raise Stop('territoire_hors_couverture_declaree')
    if (not isinstance(preuve,PreuveAcces) or preuve.service_id!=ident or not preuve.valide
            or not preuve.revision_connexion or not preuve.profil_version or capacite not in preuve.capacites
            or territoire not in preuve.territoires):
        return {'etat':'capacite_a_qualifier'}
    if analyse and s['licence_analyse_a_verifier'] and not preuve.licence_analyse_verifiee:
        return {'etat':'licence_analyse_a_verifier'}
    if s['transport']=='navigateur' and not poste_disponible:
        return {'etat':'poste_ou_compagnon_requis'}
    if s['transport']=='a_qualifier':return {'etat':'adaptateur_a_qualifier'}
    # Un plan de préparation n'autorise jamais paiement, signature ou dépôt.
    return {'etat':'plan_pret','service_id':ident,'capacite':capacite,
            'revision_connexion':preuve.revision_connexion,'profil_version':preuve.profil_version,
            'effet_externe_autorise':False,'transport':s['transport']}


def requete_historique_uk(company_number, *, page=0, taille=25):
    if not isinstance(company_number,str) or not re.fullmatch(r'[A-Z0-9]{8}',company_number):
        raise Stop('company_number_a_verifier')
    if type(page) is not int or page<0 or type(taille) is not int or not 1<=taille<=100:
        raise Stop('pagination_invalide')
    return {'service_id':'uk_companies_house','method':'GET',
            'url':'https://api.company-information.service.gov.uk/company/'+company_number+'/filing-history',
            'params':{'start_index':page*taille,'items_per_page':taille},
            'auth_injection':'cle_basic_par_adaptateur'}


def requete_sec(cik):
    if not isinstance(cik,str) or not re.fullmatch(r'[0-9]{1,10}',cik):raise Stop('cik_a_verifier')
    return {'service_id':'us_sec','method':'GET',
            'url':'https://data.sec.gov/submissions/CIK'+cik.zfill(10)+'.json',
            'en_tetes_requis':['User-Agent'],'auth_injection':None}


def requete_canlii(database_id,case_id,langue='fr'):
    if langue not in ('fr','en'):raise Stop('langue_canlii_invalide')
    for x in (database_id,case_id):
        if not isinstance(x,str) or not re.fullmatch(r'[a-z0-9-]{1,100}',x):raise Stop('reference_canlii_invalide')
    return {'service_id':'ca_canlii','method':'GET',
            'url':'https://api.canlii.org/v1/caseBrowse/'+langue+'/'+database_id+'/'+case_id+'/',
            'auth_injection':'api_key_en_query_par_adaptateur_journal_expurge',
            'contenu':'metadonnees_seulement'}


def empreinte_document(dossier, societe, service_id, reference, revision):
    service(service_id)
    if not all(isinstance(x,str) and x for x in (dossier,societe,reference,revision)):
        raise Stop('provenance_document_incomplete')
    return hashlib.sha256(json.dumps([dossier,societe,service_id,reference,revision],
        ensure_ascii=False,separators=(',',':')).encode()).hexdigest()


# ==================================================================================================================================
# 5.6.109 (lot S, G18 et G19) : raccordement au produit — identités de registre sur les fiches de contact existantes, preuves d'accès
# relues dans les tables du serveur, documents publics des sociétés étrangères classés dans le dossier par le registre de documents
# existant (``registre_docs5688``, sous-dossier du dossier), sans second registre ni nouvelle page.
#
# - Identité canonique : pays + autorité du registre + numéro TEXTUEL (``cle_societe``) portée par la fiche ``contacts5636`` (colonnes
#   ajoutées, index unique) ; jamais de fusion par nom, siège ou numéro nu ; en Allemagne, tribunal et section (HRB/HRA) dans l'autorité.
#   Le droit applicable du dossier et son tribunal ne changent pas quand une société étrangère y entre (aucune règle française appliquée
#   à son registre, aucune règle étrangère appliquée au dossier).
# - Preuve d'accès (``preuve_courante``) : reconstruite à chaque appel depuis ``connexions5652`` (génération du compte) et les preuves par
#   capacité de ``connector_readiness5689`` ; changer de compte ou de clé change la génération, donc périme la preuve. Les preuves sont
#   versionnées et déjà exportées (``capability_proofs5689``) ; aucun secret n'y figure.
# - Collecte (``collecter``) : contrôle d'accès neutre (404) avant tout, plan fermé (``preparer``), pause et mode local strict relus,
#   historique paginé et borné, téléchargement du format d'origine, empreinte binaire ; une même version n'est jamais archivée deux fois,
#   une nouvelle version s'ajoute sans remplacer l'ancienne ; dépôt exclusif (aucun écrasement) ; écriture par clé d'idempotence et reçu ;
#   résultat incertain (délai, coupure) : « À vérifier », jamais rejoué à l'aveugle ; 429 : arrêt, quota dit tel quel.
# - Nature distincte : copie ordinaire, copie certifiée, synthèse du fournisseur, impression de page ; devise et crédits conservés à part,
#   sans conversion. Un document public de la partie adverse n'est JAMAIS une pièce communiquée par elle (colonne ``piece_communiquee``
#   toujours 0, dossier « Documents publics des sociétés », rien dans les pièces adverses).
DOSSIER_DOCUMENTS = 'Documents publics des sociétés'
REGISTRE_EXISTANT = {'uk_legislation': 'gb_legislation', 'ca_laws': 'ca_federal', 'qc_legis': 'ca_quebec', 'de_laws': 'de_bund', 'es_boe': 'es_boe',
                     'ca_canlii': 'canlii'}
SERVICES_COLLECTE = ('uk_companies_house', 'us_sec')     # adaptateurs documentaires raccordés (faux transports en essai, aucun appel réel)
DEVISES = {'uk_companies_house': 'GBP', 'us_sec': 'USD', 'pappers_international': 'EUR', 'opencorporates': 'GBP'}
NATURES = {'ordinaire': 'Copie ordinaire', 'certifiee': 'Copie certifiée', 'synthese': 'Synthèse du fournisseur', 'impression': 'Impression de page web',
           'signe': 'Document signé'}
INCERTAINS = ('delai_http_depasse', 'connexion_http_indisponible', 'http_500', 'http_502', 'http_503', 'http_504')
MAX_DOCUMENTS = 20
COLONNES_CONTACTS = (('registre_pays', "TEXT NOT NULL DEFAULT ''"), ('registre_autorite', "TEXT NOT NULL DEFAULT ''"),
                     ('registre_numero', "TEXT NOT NULL DEFAULT ''"), ('registre_cle', "TEXT NOT NULL DEFAULT ''"))
COLONNES_DOCUMENTS = (('service', "TEXT NOT NULL DEFAULT ''"), ('societe_cle', "TEXT NOT NULL DEFAULT ''"), ('reference', "TEXT NOT NULL DEFAULT ''"),
                      ('revision', "TEXT NOT NULL DEFAULT ''"), ('nature_document', "TEXT NOT NULL DEFAULT ''"), ('format', "TEXT NOT NULL DEFAULT ''"),
                      ('devise', "TEXT NOT NULL DEFAULT ''"), ('cout', "TEXT NOT NULL DEFAULT ''"), ('credits', "TEXT NOT NULL DEFAULT ''"),
                      ('role_societe', "TEXT NOT NULL DEFAULT ''"), ('etat', "TEXT NOT NULL DEFAULT ''"), ('cle_idem', "TEXT NOT NULL DEFAULT ''"),
                      ('piece_communiquee', 'INTEGER NOT NULL DEFAULT 0'))
SCHEMA_56109 = '''CREATE TABLE IF NOT EXISTS international56109_ops(acteur TEXT NOT NULL, cle TEXT NOT NULL, nature TEXT NOT NULL, empreinte TEXT NOT NULL,
  etat TEXT NOT NULL, resultat TEXT NOT NULL DEFAULT '{}', cree_le TEXT NOT NULL, maj_le TEXT NOT NULL, PRIMARY KEY(acteur, cle))'''
PREFIXE = 'm56109/international/'
LECTURES = ('societe', 'documents')
ECRITURES = ('identifier', 'collecter')
ROLES_ACTION = ('administrateur', 'avocat')
MAX_POST = 8000
CLE_IDEM = re.compile(r'^[A-Za-z0-9_.:-]{8,120}$')
STATUTS = {'introuvable': '404 Not Found', 'role_insuffisant': '403 Forbidden', 'origine_ou_csrf_refuse': '403 Forbidden', 'cle_reutilisee': '409 Conflict',
           'operation_en_cours': '409 Conflict', 'identite_deja_attribuee': '409 Conflict', 'requete_trop_longue': '413 Payload Too Large',
           'controle_acces_indisponible': '503 Service Unavailable'}
MESSAGES = {'introuvable': 'Dossier ou société introuvable, ou action non permise.', 'role_insuffisant': 'Votre rôle ne permet pas cette action.',
            'cle_reutilisee': 'Cette demande a déjà servi pour une autre action : rechargez la page.', 'cle_requise': 'Demande incomplète : rechargez la page.',
            'operation_en_cours': 'Cette action est déjà en cours : patientez puis rechargez la page.',
            'identite_deja_attribuee': 'Ce numéro de registre est déjà celui d’une autre fiche : rien n’a été fusionné ; vérifiez les deux fiches.',
            'identite_societe_a_preciser': 'Précisez le pays, l’autorité du registre et le numéro.',
            'tribunal_et_section_requis': 'Allemagne : indiquez le tribunal et la section (par exemple « Berlin HRB »).',
            'company_number_a_verifier': 'Numéro Companies House : 8 caractères, zéros de tête compris.', 'cik_a_verifier': 'CIK : chiffres seulement.',
            'identite_registre_requise': 'Renseignez d’abord le registre de la société (pays, autorité, numéro).',
            'service_non_raccorde': 'Ce registre se consulte sur son site ou avec un compte : aucune collecte automatique n’est raccordée.',
            'requete_trop_longue': 'Demande trop longue.', 'origine_ou_csrf_refuse': 'Session expirée ou origine refusée : rechargez la page.',
            'controle_acces_indisponible': 'Contrôle des accès indisponible : réessayez dans un instant.'}
ETATS_COLLECTE = {'suspendue': 'IA en pause : collecte suspendue', 'reseau_non_autorise': 'Bloquée par le mode local strict ou la politique',
                  'capacite_a_qualifier': 'Accès à qualifier (Paramètres › Connexions API)', 'adaptateur_a_qualifier': 'Adaptateur à qualifier',
                  'precision_requise': 'Précision requise : quel registre ?', 'poste_ou_compagnon_requis': 'Poste ou compagnon requis',
                  'terminee': 'Collecte terminée', 'partielle': 'Collecte partielle', 'quota_atteint': 'Quota atteint : collecte arrêtée'}


def _maintenant():
    from datetime import datetime, timezone
    return datetime.now(timezone.utc).isoformat(timespec='seconds')


def _colonnes(desk, table, colonnes):
    have = {r[1] for r in desk.db.execute('PRAGMA table_info(%s)' % table)}
    for nom, decl in colonnes:
        if nom not in have:
            desk.db.execute('ALTER TABLE %s ADD COLUMN %s %s' % (table, nom, decl))


def ensure_schema(desk):
    """Démarrage (``Desk._prepare``) : colonnes additives des registres existants (contacts, documents du registre), reçus du lot."""
    from . import crm5636, inpi5688
    if desk.db.in_transaction:
        desk.db.commit()
    crm5636.schema(desk)
    inpi5688.ensure_schema(desk)
    _colonnes(desk, 'contacts5636', COLONNES_CONTACTS)
    _colonnes(desk, 'registre_docs5688', COLONNES_DOCUMENTS)
    desk.db.execute("CREATE UNIQUE INDEX IF NOT EXISTS contacts5636_registre56109 ON contacts5636(registre_cle) WHERE registre_cle<>''")
    desk.db.execute(SCHEMA_56109)
    desk.db.commit()


# ------------------------------------------------------------------------------------------------ identités (G18)
def service_registre(pays, autorite):
    """Registre d'après l'immatriculation de la société (jamais d'après le droit du dossier, la langue ou le nom)."""
    pays, autorite = str(pays or '').strip().upper(), str(autorite or '').strip()
    if pays == 'GB' and autorite == 'CompaniesHouse':
        return 'uk_companies_house'
    if pays == 'US' and autorite == 'SEC':
        return 'us_sec'
    if pays == 'CA-QC' and autorite == 'REQ':
        return 'qc_req'
    if pays == 'CA' and autorite == 'CorporationsCanada':
        return 'ca_corporations'
    if pays == 'CA-ON' and autorite == 'OBR':
        return 'on_registry'
    if pays == 'ES' and autorite.startswith('RegistroMercantil'):
        return 'es_registradores'
    if pays == 'DE' and re.fullmatch(r'.{2,80} (HRB|HRA)', autorite):
        return 'de_handelsregister'
    return None


def valider_identite(pays, autorite, numero):
    """Clé canonique après les contrôles propres à l'autorité ; un numéro reste du texte (zéros et préfixes conservés)."""
    cle = cle_societe(pays, autorite, numero)
    pays, autorite, numero = pays.strip().upper(), autorite.strip(), numero.strip()
    if pays == 'DE' and not re.fullmatch(r'.{2,80} (HRB|HRA)', autorite):
        raise Stop('tribunal_et_section_requis')
    if service_registre(pays, autorite) == 'uk_companies_house':
        requete_historique_uk(numero)
    if service_registre(pays, autorite) == 'us_sec':
        requete_sec(numero)
    return cle


def _contact(desk, contact_id):
    from . import crm5636
    try:
        return crm5636.get(desk, str(contact_id or '')[:80])
    except Stop:
        raise Stop('introuvable') from None


def identifier(desk, acteur, contact_id, pays, autorite, numero):
    """Identité de registre d'une fiche de société existante ; un numéro déjà porté par une AUTRE fiche est refusé (aucune fusion)."""
    c = _contact(desk, contact_id)
    cle = valider_identite(str(pays or ''), str(autorite or ''), str(numero or ''))
    autre = desk.db.execute("SELECT id FROM contacts5636 WHERE registre_cle=? AND id<>?", (cle, c['id'])).fetchone()
    if autre:
        raise Stop('identite_deja_attribuee')
    p = json.loads(cle)
    desk.db.execute('UPDATE contacts5636 SET registre_pays=?, registre_autorite=?, registre_numero=?, registre_cle=?, kind=?, updated=? WHERE id=?',
                    (p[0], p[1], p[2], cle, 'societe', _maintenant(), c['id']))
    desk.db.commit()
    desk.audit('international56109_identite', {'contact': c['id'], 'pays': p[0], 'autorite': p[1], 'par': acteur})
    return profil(desk, c['id'])


def ajouter_societe(desk, acteur, matter, nom, pays, autorite, numero, role='partie_adverse'):
    """Société étrangère partie au dossier : fiche retrouvée par sa SEULE identité de registre, sinon créée ; liée au dossier avec son rôle."""
    from . import crm5636, acces5697
    acces5697.exiger(desk, acteur, dossier_id=str(matter or ''))
    cle = valider_identite(str(pays or ''), str(autorite or ''), str(numero or ''))
    row = desk.db.execute('SELECT id FROM contacts5636 WHERE registre_cle=?', (cle,)).fetchone()
    if row:
        crm5636.link(desk, row['id'], matter, role)
        return profil(desk, row['id'])
    c = crm5636.save(desk, {'kind': 'societe', 'category': role if role in crm5636.CATEGORIES else 'autre', 'name': str(nom or '')[:200],
                            'matter': matter, 'role': role})
    return identifier(desk, acteur, c['id'], pays, autorite, numero)


def profil(desk, contact_id):
    """Profil international de la fiche : registre, service et territoire de la SOCIÉTÉ ; le droit et le tribunal du dossier sont ailleurs."""
    c = _contact(desk, contact_id)
    service_id = service_registre(c.get('registre_pays'), c.get('registre_autorite')) if c.get('registre_cle') else None
    return {'contact': c['id'], 'nom': c['label'], 'pays': c.get('registre_pays') or '', 'autorite': c.get('registre_autorite') or '',
            'numero': c.get('registre_numero') or '', 'cle': c.get('registre_cle') or '', 'service': service_id or '',
            'service_libelle': (catalogue().get(service_id) or {}).get('label', '') if service_id else '',
            'collecte_raccordee': service_id in SERVICES_COLLECTE, 'dossiers': [{'dossier': l['matter'], 'role': l['role']} for l in c['links']]}


# ------------------------------------------------------------------------------------------------ preuves d'accès (G18, G21)
def preuve_courante(desk, service_id, owner='cabinet', role='administrateur'):
    """``PreuveAcces`` reconstruite côté serveur : connexion utilisable, génération du compte, capacités PROUVÉES (preuves encore valides
    de cette génération). Aucune connexion, aucune capacité prouvée : ``None`` ou preuve invalide ; jamais un objet venu du modèle."""
    from . import connexions5652 as cx, connector_readiness5689 as cr
    cx.ensure_schema(desk)
    cr.ensure_schema(desk)
    rows = [r for r in desk.db.execute('SELECT * FROM connexions5652 WHERE provider=? AND disconnected=0 ORDER BY updated DESC', (service_id,)).fetchall()
            if cx.can_use(r, owner)]
    if not rows:
        return None
    conn = cr.connexion(desk, rows[0]['id'])
    prouvees = frozenset(c for c in conn['spec_caps'] if cr.etat_capacite(desk, conn, c, owner, role)['etat'] == 'prouvee')
    return PreuveAcces(service_id=service_id, revision_connexion='%s:%s' % (rows[0]['id'], rows[0]['generation']),
                       profil_version='adaptateur-' + str(conn['adapter_version']), capacites=prouvees,
                       territoires=frozenset(service(service_id)['territoires']), valide=bool(prouvees))


# ------------------------------------------------------------------------------------------------ collecte et classement (G19)
def _pause(desk):
    from .sources_publiques56109 import _pause as pause
    return pause(desk)


def _local_strict(desk):
    from .selection_sources5697 import local_strict
    return local_strict(desk)


def executer(desk, compte, cle, nature, parametres, fn):
    """Une commande = un effet : même clé et mêmes paramètres → même reçu ; autre usage de la clé → ``cle_reutilisee`` (409)."""
    cle = str(cle or '')
    if not CLE_IDEM.match(cle):
        raise Stop('cle_requise')
    empreinte = hashlib.sha256(json.dumps({'n': nature, 'p': parametres}, sort_keys=True, ensure_ascii=False, default=str).encode()).hexdigest()
    row = desk.db.execute('SELECT empreinte, etat, resultat FROM international56109_ops WHERE acteur=? AND cle=?', (compte, cle)).fetchone()
    if row:
        if row[0] != empreinte:
            raise Stop('cle_reutilisee')
        if row[1] == 'fait':
            return {**json.loads(row[2]), 'rejoue': True}
        if row[1] == 'echec':
            raise Stop(json.loads(row[2]).get('error') or 'operation_echouee')
        raise Stop('operation_en_cours')
    quand = _maintenant()
    desk.db.execute('INSERT INTO international56109_ops(acteur,cle,nature,empreinte,etat,resultat,cree_le,maj_le) VALUES(?,?,?,?,?,?,?,?)',
                    (compte, cle, nature, empreinte, 'en_cours', '{}', quand, quand))
    desk.db.commit()
    try:
        out = fn()
    except BaseException as ex:
        if desk.db.in_transaction:
            desk.db.rollback()
        code = str(ex).split(':')[0] if isinstance(ex, Stop) else 'erreur_interne'
        desk.db.execute("UPDATE international56109_ops SET etat='echec', resultat=?, maj_le=? WHERE acteur=? AND cle=?", (json.dumps({'error': code}), _maintenant(), compte, cle))
        desk.db.commit()
        raise
    desk.db.execute("UPDATE international56109_ops SET etat='fait', resultat=?, maj_le=? WHERE acteur=? AND cle=?",
                    (json.dumps(out, ensure_ascii=False, default=str), _maintenant(), compte, cle))
    desk.db.commit()
    return out


def _nom_sur(texte):
    return re.sub(r'\s+', ' ', re.sub(r'[^\w .,()\'’-]+', ' ', str(texte or ''))).strip()[:100] or 'document'


def _deposer(client, chemin, data, kind):
    """Dépôt exclusif : un fichier déjà là n'est jamais remplacé ; un nom libre est pris à côté."""
    base, ext = (chemin.rsplit('.', 1) + [''])[:2]
    for n in range(1, 6):
        cible = chemin if n == 1 else '%s (%d).%s' % (base, n, ext)
        try:
            return client.put_archive(cible, data, 30_000_000, kind)
        except Stop as ex:
            if str(ex).split(':')[0] != 'http_412' or n >= 5:
                raise
    raise Stop('depot_impossible')


def collecter(desk, acteur, matter, contact_id, cle, max_documents=MAX_DOCUMENTS):
    """Documents publics d'une société étrangère du dossier, classés dans « Documents publics des sociétés » (voir l'en-tête de section)."""
    from . import acces5697
    a = acces5697.exiger(desk, acteur, dossier_id=str(matter or ''))          # même 404 neutre : dossier absent ou réservé, avant tout
    c = _contact(desk, contact_id)
    liens = [l for l in c['links'] if l['matter'] == matter]
    if not liens:
        raise Stop('introuvable')
    if not c.get('registre_cle'):
        raise Stop('identite_registre_requise')
    service_id = service_registre(c['registre_pays'], c['registre_autorite'])
    if service_id not in SERVICES_COLLECTE:
        raise Stop('service_non_raccorde')
    compte = a.compte
    parametres = {'dossier': matter, 'societe': c['registre_cle'], 'service': service_id, 'max': int(max_documents)}
    return executer(desk, compte, cle, 'collecte', parametres,
                    lambda: _collecter(desk, compte, matter, c, service_id, liens[0]['role'], max(1, min(int(max_documents), MAX_DOCUMENTS)), cle))


def _collecter(desk, compte, matter, c, service_id, role_societe, maximum, cle):
    from . import connexions5652 as cx, document_projects
    from .common import clean_path, load_matters, under
    preuve = preuve_courante(desk, service_id, 'cabinet')
    plan = preparer(service_id, 'documents.lister', territoire=c['registre_pays'], preuve=preuve, pause=_pause(desk),
                    reseau_autorise=not _local_strict(desk))
    bilan = {'etat': plan['etat'], 'etat_libelle': ETATS_COLLECTE.get(plan['etat'], plan['etat']), 'classes': [], 'deja_la': 0, 'a_verifier': [],
             'erreurs': [], 'pagination_complete': None, 'devise': DEVISES.get(service_id, ''), 'cout_connu': '0', 'credits': '',
             'service': service_id}
    if plan['etat'] != 'plan_pret':
        return bilan
    if 'documents.telecharger' not in preuve.capacites:
        bilan.update(etat='capacite_a_qualifier', etat_libelle=ETATS_COLLECTE['capacite_a_qualifier'])
        return bilan
    m = next((x for x in load_matters(desk.c) if x['id'] == matter), None)
    if m is None or not m.get('path'):
        raise Stop('introuvable')
    row = cx._connections_for(desk, 'documents.lister', 'cabinet')
    row = next((r for r in row if r['provider'] == service_id), None)
    if row is None:
        bilan.update(etat='capacite_a_qualifier', etat_libelle=ETATS_COLLECTE['capacite_a_qualifier'])
        return bilan
    ad = cx.adapter(desk, row)
    p = json.loads(c['registre_cle'])
    liste = ad.call('documents.lister', {'numero': p[2]} if service_id == 'uk_companies_house' else {'cik': p[2]})
    bilan['pagination_complete'] = bool(liste.get('complet'))
    dossier = clean_path('%s/%s/%s' % (m['path'], DOSSIER_DOCUMENTS, _nom_sur('%s (%s %s)' % (c['label'], p[0], p[2]))))
    if not under(dossier, m['path']):
        raise Stop('destination_hors_dossier')
    client = document_projects._dav(desk)
    client.ensure_folder(dossier, m['path'])
    connus = {r['inpi_id']: r for r in desk.db.execute('SELECT * FROM registre_docs5688 WHERE matter=?', (matter,))}
    empreintes = {r['sha256']: r['chemin'] for r in connus.values() if r['etat'] == 'classe' and r['sha256']}
    for doc in liste.get('documents', [])[:maximum]:
        if _pause(desk):
            bilan['etat'] = 'suspendue'
            break
        ident = empreinte_document(matter, c['registre_cle'], service_id, doc['reference'] + '|ordinaire', doc.get('revision') or doc.get('date') or 'v1')
        ancien = connus.get(ident)
        if ancien is not None:
            if ancien['etat'] == 'a_verifier':
                bilan['a_verifier'].append({'reference': doc['reference'], 'libelle': ancien['libelle']})   # jamais rejoué à l'aveugle
            else:
                bilan['deja_la'] += 1
            continue
        try:
            got = ad.call('documents.telecharger', {'metadonnees': doc.get('metadonnees', ''), 'url': doc.get('url', '')})
        except Stop as ex:
            code = str(ex).split(':')[0]
            bilan['erreurs'].append({'reference': doc['reference'], 'code': code})
            if code == 'http_429':
                bilan['etat'] = 'quota_atteint'
                break
            continue
        data = got['fichier']
        # le téléchargement a eu lieu : son coût est dû (services raccordés gratuits : 0 déclaré, comme la colonne « cout » du registre)
        _noter_cout(desk, ident, service_id, DEVISES.get(service_id, ''), '0', '', 'documents.telecharger')
        sha = hashlib.sha256(data).hexdigest()
        libelle = _nom_sur('%s %s %s' % (doc.get('date') or '', doc.get('type') or '', doc.get('description') or ''))
        base = {'inpi_id': ident, 'matter': matter, 'siren': '', 'nature': 'document_public_etranger', 'libelle': libelle[:200], 'date_depot': str(doc.get('date') or '')[:10],
                'date_cloture': '', 'sha256': sha, 'taille': len(data), 'texte_etat': 'non_lu', 'extrait': '', 'recupere_le': _maintenant(), 'service': service_id,
                'societe_cle': c['registre_cle'], 'reference': doc['reference'], 'revision': doc.get('revision') or doc.get('date') or 'v1', 'nature_document': 'ordinaire',
                'format': got.get('format', ''), 'devise': DEVISES.get(service_id, ''), 'cout': '0', 'credits': '', 'role_societe': role_societe, 'cle_idem': cle,
                'piece_communiquee': 0}
        if sha in empreintes:     # même binaire déjà classé (autre référence) : aucune seconde copie
            _inserer_document(desk, {**base, 'chemin': empreintes[sha], 'etat': 'deja_classe'})
            bilan['deja_la'] += 1
            continue
        ext = 'pdf' if got.get('format') == 'application/pdf' else 'html'
        _inserer_document(desk, {**base, 'chemin': '', 'etat': 'en_cours'})          # intention écrite AVANT le dépôt
        try:
            chemin = _deposer(client, clean_path('%s/%s (%s).%s' % (dossier, libelle[:90], re.sub(r'\W', '', doc['reference'])[:16], ext)), data,
                              got.get('format') or 'application/octet-stream')
        except Stop as ex:
            code = str(ex).split(':')[0]
            etat = 'a_verifier' if code in INCERTAINS else 'echec'
            desk.db.execute('UPDATE registre_docs5688 SET etat=? WHERE inpi_id=? AND matter=?', (etat, ident, matter))
            desk.db.commit()
            (bilan['a_verifier'] if etat == 'a_verifier' else bilan['erreurs']).append({'reference': doc['reference'], 'code': code, 'libelle': libelle})
            if etat == 'echec':
                desk.db.execute("DELETE FROM registre_docs5688 WHERE inpi_id=? AND matter=? AND etat='echec'", (ident, matter))
                desk.db.commit()
            continue
        desk.db.execute("UPDATE registre_docs5688 SET etat='classe', chemin=? WHERE inpi_id=? AND matter=?", (chemin, ident, matter))
        desk.db.commit()
        empreintes[sha] = chemin
        bilan['classes'].append({'reference': doc['reference'], 'chemin': chemin})
    if bilan['etat'] == 'plan_pret':
        bilan['etat'] = 'terminee' if bilan['pagination_complete'] and not bilan['a_verifier'] and not bilan['erreurs'] else 'partielle'
    bilan['etat_libelle'] = ETATS_COLLECTE.get(bilan['etat'], bilan['etat'])
    desk.audit('international56109_collecte', {'dossier': matter, 'service': service_id, 'classes': len(bilan['classes']), 'deja_la': bilan['deja_la'],
                                               'a_verifier': len(bilan['a_verifier']), 'erreurs': [x['code'] for x in bilan['erreurs']], 'etat': bilan['etat']})
    return bilan


def _noter_cout(desk, call_id, service_id, devise, cout, credits, operation):
    """5.6.109 (intégration I2, G19 → F18) : coût déclaré (dans sa devise, sans conversion) et crédits du fournisseur inscrits au registre de
    consommation (``budget_ledger5689.noter_quantite``), comme les crédits Pappers du lot G4. Un coût non renseigné n'est pas inscrit (jamais
    un faux zéro) ; un service gratuit déclare 0. Même appel relevé deux fois : rien de plus. Le registre refuse : journalisé, l'effet reste."""
    from .budget_ledger5689 import noter_quantite
    try:
        fournisseur = str(service(service_id).get('label') or service_id)
    except Stop:
        fournisseur = service_id
    for suffixe, unite, valeur in (('montant', 'montant_%s_%s' % (str(devise or '').lower(), service_id), cout), ('credits', 'credit_%s' % service_id, credits)):
        if str(valeur or '').strip() == '' or (suffixe == 'montant' and not re.fullmatch(r'[a-z]{3}', str(devise or '').lower())):
            continue
        try:
            noter_quantite(desk.c['state_dir'], '%s:%s' % (call_id, suffixe), fournisseur, unite, str(valeur).strip().replace(',', '.'), etat='declaree',
                           operation=operation, source='international56109', config=desk.c)
        except Stop as ex:
            desk.audit('international56109_cout_non_inscrit', {'appel': call_id, 'motif': str(ex).split(':')[0]})


def _inserer_document(desk, ligne):
    cols = list(ligne)
    desk.db.execute('INSERT OR IGNORE INTO registre_docs5688(%s) VALUES(%s)' % (','.join(cols), ','.join('?' * len(cols))), [ligne[k] for k in cols])
    desk.db.commit()


def enregistrer_document(desk, matter, societe_cle, service_id, reference, revision, nature, sha256, chemin, devise='', cout='', credits='', role_societe=''):
    """Document public obtenu autrement (copie certifiée commandée, impression de page) : même registre, nature distincte (une copie
    certifiée n'est jamais confondue avec la copie ordinaire de la même référence et de la même version)."""
    if nature not in NATURES:
        raise Stop('nature_document_inconnue')
    ident = empreinte_document(matter, societe_cle, service_id, reference + '|' + nature, revision)
    _inserer_document(desk, {'inpi_id': ident, 'matter': matter, 'siren': '', 'nature': 'document_public_etranger', 'libelle': NATURES[nature], 'date_depot': '',
                             'date_cloture': '', 'chemin': chemin, 'sha256': sha256, 'taille': 0, 'texte_etat': 'non_lu', 'extrait': '', 'recupere_le': _maintenant(),
                             'service': service_id, 'societe_cle': societe_cle, 'reference': reference, 'revision': revision, 'nature_document': nature, 'format': '',
                             'devise': devise, 'cout': cout, 'credits': credits, 'role_societe': role_societe, 'etat': 'classe', 'cle_idem': '', 'piece_communiquee': 0})
    _noter_cout(desk, ident, service_id, devise, cout, credits, 'document_' + nature)   # 5.6.109 (I2) : coût et crédits au registre de consommation
    return ident


def documents(desk, matter, societe_cle=''):
    """Documents publics étrangers classés pour ce dossier (versions et natures distinctes, coûts par devise sans conversion)."""
    sql = "SELECT * FROM registre_docs5688 WHERE matter=? AND nature='document_public_etranger'" + (' AND societe_cle=?' if societe_cle else '') + ' ORDER BY date_depot DESC, recupere_le DESC'
    return [dict(r) for r in desk.db.execute(sql, (matter, societe_cle) if societe_cle else (matter,))]


# ------------------------------------------------------------------------------------------------ fiche de contact (point de montage de crm5636)
def section_contact(desk, prefix, c):
    """Bloc « Registre étranger » de la fiche de contact : identité, service, documents classés par dossier, formulaires protégés."""
    from html import escape as e
    import uuid
    if c.get('kind') != 'societe':
        return ''
    p = profil(desk, c['id'])
    ident = ('<p>%s · %s · <strong>%s</strong>%s</p>' % (e(p['pays']), e(p['autorite']), e(p['numero']),
                                                         (' · ' + e(p['service_libelle'])) if p['service_libelle'] else '')
             if p['cle'] else '<p class="ax-muted">Aucun registre étranger renseigné pour cette société.</p>')
    form = ('<details class="i56109-identite"><summary>Registre de la société</summary><form class="m5-form crm-grid" data-api="%sidentifier" data-reload>'
            '<input type="hidden" name="contact" value="%s"><input type="hidden" name="cle" value="%s">'
            '<label>Pays (GB, US, DE, ES, CA, CA-QC…)<input name="pays" value="%s" maxlength="8" required></label>'
            '<label>Autorité du registre<input name="autorite" value="%s" maxlength="200" required placeholder="CompaniesHouse"></label>'
            '<label>Numéro (tel qu’au registre)<input name="numero" value="%s" maxlength="200" required></label>'
            '<button class="ax-btn" type="submit">Enregistrer le registre</button><p role="status" class="m5-status"></p></form></details>') % (
        PREFIXE, e(c['id'], quote=True), uuid.uuid4().hex, e(p['pays'], quote=True), e(p['autorite'], quote=True), e(p['numero'], quote=True))
    blocs = ''
    for d in p['dossiers']:
        docs = documents(desk, d['dossier'], p['cle']) if p['cle'] else []
        lignes = ''.join('<li>%s · %s · %s%s</li>' % (e(x['date_depot'] or '—'), e(x['libelle']), e(NATURES.get(x['nature_document'], x['nature_document'])),
                                                     ' · <strong>À vérifier</strong>' if x['etat'] == 'a_verifier' else '') for x in docs[:30])
        action = ('<form class="m5-form" data-api="%scollecter" data-reload><input type="hidden" name="d" value="%s"><input type="hidden" name="contact" value="%s">'
                  '<input type="hidden" name="cle" value="%s"><button class="ax-btn ghost" type="submit">Collecter les documents publics</button>'
                  '<p role="status" class="m5-status"></p></form>') % (PREFIXE, e(d['dossier'], quote=True), e(c['id'], quote=True), uuid.uuid4().hex) if p['collecte_raccordee'] else ''
        blocs += '<div class="i56109-dossier"><h3>Dossier %s</h3><ul>%s</ul>%s</div>' % (e(d['dossier']), lignes or '<li class="ax-muted">Aucun document public classé.</li>', action)
    return ('<section class="ax-card i56109"><h2>Registre étranger et documents publics</h2>%s%s%s<p class="ax-muted">Un document public de la partie '
            'adverse n’est pas une pièce qu’elle a communiquée. Les documents sont rangés dans « %s » ; chaque version est conservée.</p></section>') % (
        ident, form, blocs, e(DOSSIER_DOCUMENTS))


# ------------------------------------------------------------------------------------------------ routes m56109/international/
def allowed(role, method, api):
    reste = str(api or '').split('?', 1)[0]
    reste = reste[len(PREFIXE):].strip('/') if reste.startswith(PREFIXE) else ''
    if method in ('GET', 'HEAD'):
        return reste in LECTURES
    return method == 'POST' and reste in ECRITURES and role in ROLES_ACTION


def _json(data, status='200 OK'):
    return {'status': status, 'kind': 'application/json; charset=utf-8', 'body': json.dumps(data, ensure_ascii=False, default=str),
            'headers': [('Cache-Control', 'private, no-store')]}


def api(env, desk, auth, prefix, name, args, method):
    from .web567 import actor
    from . import acces5697
    acteur = actor(env)
    reste = str(name).split('?', 1)[0][len(PREFIXE):].strip('/')
    try:
        if reste not in LECTURES + ECRITURES:
            raise Stop('introuvable')
        if not allowed(acteur[1], method, name):
            raise Stop('role_insuffisant' if method == 'POST' and reste in ECRITURES else 'introuvable')
        if method in ('GET', 'HEAD'):
            if reste == 'documents':
                acces5697.exiger(desk, acteur, dossier_id=str(args.get('d') or ''))
                return _json({'documents': documents(desk, str(args.get('d') or ''), str(args.get('societe') or ''))})
            p = profil(desk, str(args.get('contact') or ''))
            for d in p['dossiers']:
                acces5697.exiger(desk, acteur, dossier_id=d['dossier'])
            return _json(p)
        try:
            longueur = int(env.get('CONTENT_LENGTH') or 0)
        except ValueError:
            longueur = MAX_POST + 1
        if longueur > MAX_POST:
            raise Stop('requete_trop_longue')
        from .web440 import check_post
        d = check_post(env, auth)
        if reste == 'identifier':
            c = _contact(desk, d.get('contact'))
            for l in c['links']:
                acces5697.exiger(desk, acteur, dossier_id=l['matter'])
            compte = str(acteur[0])
            return _json(executer(desk, compte, d.get('cle'), 'identifier', {'contact': c['id'], 'pays': d.get('pays'), 'autorite': d.get('autorite'),
                                                                                 'numero': d.get('numero')},
                                  lambda: {**identifier(desk, compte, c['id'], d.get('pays'), d.get('autorite'), d.get('numero')),
                                           'message': 'Registre enregistré. Aucune autre fiche n’a été modifiée.'}))
        out = collecter(desk, acteur, str(d.get('d') or ''), d.get('contact'), d.get('cle'))
        return _json({**out, 'message': out.get('etat_libelle', '')})
    except Stop as ex:
        code = str(ex).split(':')[0]
        if code in ('dossier_reserve', 'dossier_absent', 'acteur_requis', 'contact_absent'):
            code = 'introuvable'
        return _json({'error': code, 'message': MESSAGES.get(code, 'Demande refusée.')}, STATUTS.get(code, '422 Unprocessable Entity'))
