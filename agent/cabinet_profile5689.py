"""5.6.89 (objectif 1, chapitres 31 et 32) : profil versionné du cabinet, adresses propres, assistant de démarrage et mission de démonstration.

- **Profil** : identité, coordonnées, adresses propres, signatures, activités, langues, références de modèles et de compétences, politique. Il ne contient que des
  références : tout secret est refusé à l'entrée, identifiants et jetons restent au coffre. Chaque changement crée une révision ; une révision périmée est refusée (409).
- **Reprise des réglages** : une seule fois, sans secret. Une adresse déjà configurée reste reconnue mais demeure « à confirmer » : la reprise ne certifie rien.
- **Données déduites** d'une signature ou d'un document : des propositions, appliquées seulement après confirmation.
- **Adresses propres** : confirmées une par une, par compte ; jamais un domaine entier, jamais de suffixe ni de point retiré. Le rôle de la personne reste distinct de son
  adresse. Le service d'identification de tous les circuits de courriel reste ``sens5643``, qui lit ce registre.
- **Assistant** : quatre étapes, progression enregistrée, reprise sans ressaisie ni doublon. Les réglages de modèle restent dans la rubrique avancée.
- **Modes d'hébergement** : déclaratifs. Le manifeste dit ce qui est réellement fourni ; aucune plateforme n'est créée, un mode non fourni est décrit comme tel.
- **Démonstration** : dossier fictif et modèle localisé, sans compte, sans modèle d'IA, sans lecture ni écriture d'un vrai dossier ; marquée fictive partout.
"""
from datetime import datetime, timezone
import hashlib
import json
import re
import sqlite3
import uuid

from .common import Stop

MAX_TEXTE = 300
CHAMPS_TEXTE = {'nom_cabinet': 200, 'barreau': 120, 'toque': 40, 'adresse': 300, 'telephone': 40, 'langue': 5, 'policy_ref': 120, 'profil_deploiement': 20}
CHAMPS_LISTE = {'activites': 60, 'langues': 5, 'template_refs': 120, 'skill_refs': 120}
ADRESSE = re.compile(r'[^\s@*<>,;]+@[^\s@*<>,;]+\.[^\s@*<>,;]+')
STATUTS_ALIAS = {'a_confirmer': 'À confirmer', 'confirmee': 'Confirmée', 'retiree': 'Retirée'}
ROLES_PERSONNE = {'': 'Non précisé', 'avocat': 'Avocat', 'collaborateur': 'Collaborateur', 'assistant': 'Assistant(e)', 'boite_commune': 'Boîte commune'}
CHAMPS_PROPOSABLES = {'adresse_propre': 'Adresse du cabinet', 'telephone': 'Téléphone', 'barreau': 'Barreau', 'toque': 'Toque', 'nom_cabinet': 'Nom du cabinet'}
ETAPES = (('identite', 'Identité du cabinet'), ('outils', 'Outils et authentification'), ('modeles', 'Modèles et compétences'), ('demonstration', 'Mission de démonstration'))
LIBELLES_ETAPES = dict(ETAPES)
MANIFESTE_VERSION = 1
# Ce que chaque mode recouvre réellement dans cette version. « non_fourni » : décrit, jamais vendu ni simulé.
DEPLOIEMENTS = {
    'serveur_cabinet': {'libelle': 'Serveur du cabinet', 'etat': 'fourni', 'description': 'AxiorHub tourne sur le serveur ou le poste du cabinet. Le cabinet garde la main sur la base, '
                        'les documents, les secrets et les sauvegardes, et en assume l’exploitation.', 'responsable': 'cabinet'},
    'hybride': {'libelle': 'Hybride', 'etat': 'partiel', 'description': 'AxiorHub tourne chez le cabinet ; seuls les services que vous reliez vous-même (stockage cloud, modèle externe) sont '
                'à l’extérieur. Ce n’est pas un hébergement fourni par AxiorHub : chaque service relève de votre contrat avec son fournisseur.', 'responsable': 'cabinet et fournisseurs choisis'},
    'cloud_gere': {'libelle': 'Cloud géré', 'etat': 'non_fourni', 'description': 'Cette version ne fournit pas d’hébergement géré : aucun serveur, sauvegarde, surveillance ni support n’est pris '
                   'en charge pour vous. Ce choix enregistre seulement votre souhait ; rien n’est souscrit, créé ni facturé, et l’installation continue de tourner là où elle se trouve.', 'responsable': 'aucun prestataire'},
}
# Mission de démonstration : modèle localisé, données fictives uniquement.
DEMO_VERSION = 1
DEMO = {
    'fr': {'titre': 'Note de synthèse', 'marque': 'DÉMONSTRATION : DONNÉES FICTIVES', 'dossier': 'Dossier fictif DEMO-FICTIF, ZORVAL contre KERVILAC, RG 24/01234, tribunal de Valbrune',
           'faits': ('La société ZORVAL a commandé des travaux à la société KERVILAC.', 'Une facture de 12 000 euros reste impayée depuis trois mois.', 'Aucune mise en demeure ne figure au dossier fictif.'),
           'analyse': 'Avant toute action, le dossier devrait contenir le contrat, la facture et une mise en demeure datée.',
           'suite': 'Proposition : préparer une mise en demeure, à relire et à valider par l’avocat.',
           'fin': 'Document de démonstration : aucun dossier réel, aucun compte, aucun modèle d’IA n’a été utilisé. Il ne constitue pas un avis juridique.',
           'etapes': ('Lire les faits fictifs', 'Appliquer le modèle dans la langue choisie', 'Présenter le résultat et ses limites')},
    'en': {'titre': 'Summary note', 'marque': 'DEMONSTRATION: FICTITIOUS DATA', 'dossier': 'Fictitious matter DEMO-FICTIF, ZORVAL v. KERVILAC, RG 24/01234, Valbrune court',
           'faits': ('ZORVAL ordered works from KERVILAC.', 'An invoice of 12,000 euros has been unpaid for three months.', 'The fictitious file holds no formal notice.'),
           'analyse': 'Before acting, the file should contain the contract, the invoice and a dated formal notice.',
           'suite': 'Suggestion: prepare a formal notice, for the lawyer to review and approve.',
           'fin': 'Demonstration document: no real matter, account or AI model was used. It is not legal advice.',
           'etapes': ('Read the fictitious facts', 'Apply the template in the chosen language', 'Present the result and its limits')},
    'de': {'titre': 'Zusammenfassung', 'marque': 'DEMONSTRATION: FIKTIVE DATEN', 'dossier': 'Fiktive Akte DEMO-FICTIF, ZORVAL gegen KERVILAC, RG 24/01234, Gericht Valbrune',
           'faits': ('ZORVAL hat bei KERVILAC Arbeiten bestellt.', 'Eine Rechnung über 12.000 Euro ist seit drei Monaten unbezahlt.', 'Die fiktive Akte enthält keine Mahnung.'),
           'analyse': 'Vor jedem Schritt sollte die Akte den Vertrag, die Rechnung und eine datierte Mahnung enthalten.',
           'suite': 'Vorschlag: eine Mahnung vorbereiten, die der Anwalt prüft und freigibt.',
           'fin': 'Demonstrationsdokument: Es wurden keine echte Akte, kein Konto und kein KI-Modell verwendet. Es ist keine Rechtsberatung.',
           'etapes': ('Fiktive Fakten lesen', 'Vorlage in der gewählten Sprache anwenden', 'Ergebnis und Grenzen darstellen')},
    'es': {'titre': 'Nota de síntesis', 'marque': 'DEMOSTRACIÓN: DATOS FICTICIOS', 'dossier': 'Expediente ficticio DEMO-FICTIF, ZORVAL contra KERVILAC, RG 24/01234, tribunal de Valbrune',
           'faits': ('ZORVAL encargó obras a KERVILAC.', 'Una factura de 12 000 euros lleva tres meses impagada.', 'El expediente ficticio no contiene ningún requerimiento.'),
           'analyse': 'Antes de actuar, el expediente debería contener el contrato, la factura y un requerimiento fechado.',
           'suite': 'Propuesta: preparar un requerimiento, que el abogado revisará y validará.',
           'fin': 'Documento de demostración: no se ha utilizado ningún expediente real, cuenta ni modelo de IA. No constituye asesoramiento jurídico.',
           'etapes': ('Leer los hechos ficticios', 'Aplicar el modelo en el idioma elegido', 'Presentar el resultado y sus límites')},
}
SCHEMA = '''
CREATE TABLE IF NOT EXISTS cabinet_profile5689(cabinet_id TEXT PRIMARY KEY, revision INTEGER NOT NULL, data_json TEXT NOT NULL, origin TEXT NOT NULL, author TEXT NOT NULL, updated TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS cabinet_profile_versions5689(cabinet_id TEXT NOT NULL, revision INTEGER NOT NULL, data_json TEXT NOT NULL, author TEXT NOT NULL, created TEXT NOT NULL, PRIMARY KEY(cabinet_id, revision));
CREATE TABLE IF NOT EXISTS identity_aliases5689(id TEXT PRIMARY KEY, account TEXT NOT NULL, address TEXT NOT NULL, origin TEXT NOT NULL, status TEXT NOT NULL, author TEXT NOT NULL,
  person_role TEXT NOT NULL DEFAULT '', created TEXT NOT NULL, updated TEXT NOT NULL, UNIQUE(account, address));
CREATE TABLE IF NOT EXISTS cabinet_proposals5689(id TEXT PRIMARY KEY, field TEXT NOT NULL, value TEXT NOT NULL, origin TEXT NOT NULL, status TEXT NOT NULL, author TEXT NOT NULL, created TEXT NOT NULL,
  decided_by TEXT NOT NULL DEFAULT '', decided_at TEXT NOT NULL DEFAULT '', UNIQUE(field, value));
CREATE TABLE IF NOT EXISTS cabinet_wizard5689(cabinet_id TEXT PRIMARY KEY, step TEXT NOT NULL, status TEXT NOT NULL, values_json TEXT NOT NULL, done_json TEXT NOT NULL, revision INTEGER NOT NULL, updated TEXT NOT NULL, updated_by TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS demo_missions5689(id TEXT PRIMARY KEY, owner TEXT NOT NULL, cle TEXT NOT NULL, langue TEXT NOT NULL, state TEXT NOT NULL, result_json TEXT NOT NULL, created TEXT NOT NULL, UNIQUE(owner, cle));
'''
EXEMPLE_RESERVE = 'cabinet@example.com'     # valeur neutre posée par load_config quand la messagerie n'est pas choisie


def _now():
    return datetime.now(timezone.utc).isoformat(timespec='microseconds')


def ensure_schema(desk):
    if getattr(desk, '_schema5689p', False):
        return
    if not desk.db.execute("SELECT 1 FROM sqlite_master WHERE name='demo_missions5689'").fetchone():
        for statement in SCHEMA.split(';'):
            if statement.strip():
                desk.db.execute(statement)
    desk._schema5689p = True


def _peut_regler(desk, owner, role):
    if role == 'administrateur':
        return
    from .membres5650 import effective_rights
    try:
        if effective_rights(desk.c['state_dir'], owner, role).get('reglages'):
            return
    except Stop:
        pass
    raise Stop('role_insuffisant')


def cabinet_id(desk):
    """Identifiant stable du cabinet ; un identifiant reçu d'un client n'a aucune valeur d'autorisation."""
    value = desk.settings('cabinet5689:id', '')
    if not value:
        value = 'cab-' + uuid.uuid4().hex
        desk.setting('cabinet5689:id', value)
    return value


# ------------------------------------------------------------------------------------------------ refus des secrets
def _refuser_secrets(value):
    from .export5688 import VALEUR_SECRETE, _secrete
    if isinstance(value, dict):
        for key, item in value.items():
            if _secrete(key):
                raise Stop('secret_refuse_dans_le_profil')
            _refuser_secrets(item)
    elif isinstance(value, (list, tuple)):
        for item in value:
            _refuser_secrets(item)
    elif isinstance(value, str) and VALEUR_SECRETE.search(value):
        raise Stop('secret_refuse_dans_le_profil')


def _liste(value):
    if isinstance(value, (list, tuple)):
        return [str(x).strip() for x in value if str(x).strip()]
    return [x.strip() for x in re.split(r'[,;\n]', str(value or '')) if x.strip()]


def _nettoyer(data):
    """Liste fermée de champs : un champ inconnu est refusé, jamais conservé."""
    _refuser_secrets(data)
    out = {}
    for key, limit in CHAMPS_TEXTE.items():
        if key in data:
            value = str(data[key] or '').strip()
            if any(ord(c) < 32 for c in value) or len(value) > limit:
                raise Stop('reglage_texte_invalide')
            out[key] = value
    for key, limit in CHAMPS_LISTE.items():
        if key in data:
            out[key] = list(dict.fromkeys(x[:limit] for x in _liste(data[key])))[:200]
    if 'langue' in out or 'langues' in out:
        from .i18n5631 import clean
        for lang in ([out['langue']] if out.get('langue') else []) + out.get('langues', []):
            if not clean(lang):
                raise Stop('langue_inconnue')
    if out.get('profil_deploiement') and out['profil_deploiement'] not in DEPLOIEMENTS:
        raise Stop('profil_deploiement_inconnu')
    return out


# ------------------------------------------------------------------------------------------------ reprise des réglages (une seule fois, sans secret)
def _reprise(desk):
    from .pieces510 import profile as avocat
    p = avocat(desk)
    domaines = str(desk.settings('cabinet5631:domaines', '') or '')
    langue = str(desk.settings('cabinet5631:langue_redaction', '') or 'fr')
    cfg = desk.c.get('cabinet5689') or {}
    data = {'nom_cabinet': str(desk.settings('cabinet5631:cabinet', '') or '')[:200], 'barreau': p.get('ville_avocat', '')[:120], 'toque': p.get('numero_toque_avocat', '')[:40],
            'adresse': p.get('adresse_cabinet_avocat', '')[:300], 'telephone': p.get('numero_telephone_avocat', '')[:40], 'activites': _liste(domaines)[:50], 'langue': langue, 'langues': [langue],
            'signatures': [], 'template_refs': [], 'skill_refs': [], 'policy_ref': '', 'profil_deploiement': str(cfg.get('profil') or '')}
    if data['profil_deploiement'] not in DEPLOIEMENTS:
        data['profil_deploiement'] = ''
    if str((desk.c.get('mail') or {}).get('signature') or '').strip():
        data['signatures'].append({'ref': 'config:mail.signature', 'libelle': 'Signature de la messagerie'})   # une référence : le texte reste dans la configuration
    for key, sql in (('template_refs', "SELECT id FROM cabinet_templates_v330 WHERE status='approved' ORDER BY label"), ('skill_refs', 'SELECT id FROM skill_state5632 WHERE enabled=1 ORDER BY id')):
        try:
            data[key] = [r['id'] for r in desk.db.execute(sql)]
        except sqlite3.OperationalError:                          # table pas encore créée : rien à reprendre
            pass
    return data


def _adresses_configurees(desk):
    from .sens5643 import identities
    mail = dict(desk.c.get('mail') or {})
    for key in ('identites_confirmees', 'identites_retirees'):
        mail.pop(key, None)
    return {a: o for a, o in identities(mail).items() if a != EXEMPLE_RESERVE}


def importer_existant(desk):
    """Une seule fois : les valeurs existantes deviennent la révision 1. Aucun secret n'est lu ; une adresse saisie à l'installation est confirmée, les autres restent à confirmer."""
    ensure_schema(desk)
    cid = cabinet_id(desk)
    if desk.db.execute('SELECT 1 FROM cabinet_profile5689 WHERE cabinet_id=?', (cid,)).fetchone():
        return False
    data = _reprise(desk)
    now = _now()
    payload = json.dumps(data, ensure_ascii=False, sort_keys=True)
    cur = desk.db.execute('INSERT OR IGNORE INTO cabinet_profile5689(cabinet_id,revision,data_json,origin,author,updated) VALUES(?,1,?,?,?,?)', (cid, payload, 'reprise', 'migration', now))
    if not cur.rowcount:
        desk.db.rollback()
        return False
    desk.db.execute('INSERT INTO cabinet_profile_versions5689(cabinet_id,revision,data_json,author,created) VALUES(?,1,?,?,?)', (cid, payload, 'migration', now))
    saisies = {str(a).strip().lower() for a in (desk.c.get('cabinet5689') or {}).get('adresses_saisies', [])}
    for address, origin in sorted(_adresses_configurees(desk).items()):
        status = 'confirmee' if address in saisies else 'a_confirmer'
        desk.db.execute('INSERT OR IGNORE INTO identity_aliases5689(id,account,address,origin,status,author,person_role,created,updated) VALUES(?,?,?,?,?,?,?,?,?)',
                        (uuid.uuid4().hex, 'messagerie', address, 'installation' if address in saisies else origin, status, 'migration', '', now, now))
    desk.db.commit()
    desk.audit('cabinet5689_reprise', {'revision': 1})
    return True


# ------------------------------------------------------------------------------------------------ lecture et enregistrement du profil
def _ligne(desk):
    importer_existant(desk)
    return desk.db.execute('SELECT * FROM cabinet_profile5689 WHERE cabinet_id=?', (cabinet_id(desk),)).fetchone()


def _connector_refs(desk):
    from . import connexions5652 as cx
    from .connector_readiness5689 import CONFIG, _config_declaree
    cx.ensure_schema(desk)
    refs = [c for c in CONFIG if _config_declaree(desk, c)]
    return refs + [r['id'] for r in desk.db.execute('SELECT id FROM connexions5652 ORDER BY provider')]


def _policy_ref(desk, data):
    """Référence de la politique en vigueur (mode et révision de ``data_policy5689``) ; le profil ne la recopie pas. Illisible : dit tel quel."""
    from .data_policy5689 import etat_public
    etat = etat_public(desk)
    return 'politique:%s:r%s' % (etat['mode'], etat['revision']) if etat['lisible'] else 'politique:illisible'


def aliases(desk, compte=None):
    ensure_schema(desk)
    rows = desk.db.execute('SELECT * FROM identity_aliases5689 ORDER BY account, address').fetchall()
    return [{'id': r['id'], 'compte': r['account'], 'adresse': r['address'], 'origine': r['origin'], 'statut': r['status'], 'statut_libelle': STATUTS_ALIAS[r['status']], 'auteur': r['author'],
             'role_personne': r['person_role']} for r in rows if compte is None or r['account'] == compte]


def propositions(desk, statut='a_confirmer'):
    ensure_schema(desk)
    return [{'id': r['id'], 'champ': r['field'], 'champ_libelle': CHAMPS_PROPOSABLES.get(r['field'], r['field']), 'valeur': r['value'], 'origine': r['origin'], 'statut': r['status']}
            for r in desk.db.execute('SELECT * FROM cabinet_proposals5689 WHERE status=? ORDER BY created', (statut,))]


def lire(desk):
    """Vue du profil : contrat ``CabinetProfile`` et détails d'affichage ; aucun secret (le profil n'en contient pas)."""
    from .sens5643 import identities
    row = _ligne(desk)
    data = json.loads(row['data_json'])
    return {'cabinet_id': row['cabinet_id'], 'revision': row['revision'], 'own_addresses': sorted(a for a in identities(desk.c.get('mail') or {}) if a != EXEMPLE_RESERVE),
            'signature_refs': [s['ref'] for s in data.get('signatures', [])], 'practice_areas': data.get('activites', []), 'language': data.get('langue', ''), 'template_refs': data.get('template_refs', []),
            'skill_refs': data.get('skill_refs', []), 'connector_refs': _connector_refs(desk), 'policy_ref': _policy_ref(desk, data),
            'identite': {k: data.get(k, '') for k in ('nom_cabinet', 'barreau', 'toque')}, 'coordonnees': {k: data.get(k, '') for k in ('adresse', 'telephone')}, 'langues': data.get('langues', []),
            'signatures': [{'ref': s['ref'], 'libelle': s.get('libelle', '')} for s in data.get('signatures', [])], 'profil_deploiement': data.get('profil_deploiement', ''), 'alias': aliases(desk),
            'propositions': propositions(desk), 'mis_a_jour': row['updated'], 'origine': row['origin']}


def _ecrire(desk, row, data, owner):
    """Nouvelle révision, conditionnée à la révision lue : deux écritures concurrentes ne s'écrasent jamais."""
    now = _now()
    payload = json.dumps(data, ensure_ascii=False, sort_keys=True)
    revision = row['revision'] + 1
    cur = desk.db.execute("UPDATE cabinet_profile5689 SET revision=?,data_json=?,origin='saisie',author=?,updated=? WHERE cabinet_id=? AND revision=?",
                          (revision, payload, str(owner)[:200], now, row['cabinet_id'], row['revision']))
    if not cur.rowcount:
        desk.db.rollback()
        raise Stop('revision_perimee')
    desk.db.execute('INSERT INTO cabinet_profile_versions5689(cabinet_id,revision,data_json,author,created) VALUES(?,?,?,?,?)', (row['cabinet_id'], revision, payload, str(owner)[:200], now))
    desk.db.commit()
    desk.audit('cabinet5689_profil', {'revision': revision, 'champs': sorted(k for k in data if data[k])})
    return revision


def enregistrer(desk, owner, role, data):
    """Écriture du profil : droit « réglages », révision attendue obligatoire, champs connus seulement. L'acteur vient de la session."""
    _peut_regler(desk, owner, role)
    if not isinstance(data, dict):
        raise Stop('objet_json_requis')
    data = dict(data)
    attendue = data.pop('revision', None)
    data.pop('cle', None)
    if attendue in (None, '') or not re.fullmatch(r'\d{1,9}', str(attendue)):
        raise Stop('revision_requise')
    inconnus = [k for k in data if k not in CHAMPS_TEXTE and k not in CHAMPS_LISTE]
    _refuser_secrets(data)
    if inconnus:
        raise Stop('champ_profil_inconnu')
    row = _ligne(desk)
    if int(attendue) != row['revision']:
        raise Stop('revision_perimee')
    actuel = json.loads(row['data_json'])
    nouveau = {**actuel, **_nettoyer(data)}
    if nouveau == actuel:
        return {**lire(desk), 'inchange': True}
    _ecrire(desk, row, nouveau, owner)
    return lire(desk)


def versions(desk):
    ensure_schema(desk)
    return [{'revision': r['revision'], 'auteur': r['author'], 'date': r['created'][:19]} for r in
            desk.db.execute('SELECT revision,author,created FROM cabinet_profile_versions5689 WHERE cabinet_id=? ORDER BY revision DESC LIMIT 50', (cabinet_id(desk),))]


# ------------------------------------------------------------------------------------------------ adresses propres
def _compte_valide(desk, compte):
    if compte == 'messagerie':
        return compte
    from . import connexions5652 as cx
    cx._row(desk, compte)
    return compte


def _adresse(value):
    """Une adresse complète, telle qu'elle est écrite (casse et espaces mis à part) : ni domaine entier, ni suffixe ou point retiré."""
    from .sens5643 import _norm
    address = _norm(value)
    if not ADRESSE.fullmatch(address):
        raise Stop('adresse_complete_requise')
    return address


def enregistrer_alias(desk, owner, role, adresse, statut='confirmee', compte='messagerie', role_personne='', origine='saisie'):
    """Confirme, ajoute ou retire une adresse d'un compte ; l'effet est immédiat sur tous les circuits (``sens5643``)."""
    from .sens5643 import appliquer_identites
    _peut_regler(desk, owner, role)
    importer_existant(desk)
    address = _adresse(adresse)
    compte = _compte_valide(desk, str(compte or 'messagerie'))
    if statut not in STATUTS_ALIAS:
        raise Stop('statut_alias_invalide')
    if str(role_personne or '') not in ROLES_PERSONNE:
        raise Stop('role_personne_invalide')
    if statut == 'retiree' and address == str((desk.c.get('mail') or {}).get('from_address') or '').strip().lower():
        raise Stop('adresse_expedition_non_retirable')
    now = _now()
    cur = desk.db.execute('UPDATE identity_aliases5689 SET status=?,person_role=?,author=?,updated=? WHERE account=? AND address=?', (statut, str(role_personne or ''), str(owner)[:200], now, compte, address))
    if not cur.rowcount:
        desk.db.execute('INSERT INTO identity_aliases5689(id,account,address,origin,status,author,person_role,created,updated) VALUES(?,?,?,?,?,?,?,?,?)',
                        (uuid.uuid4().hex, compte, address, str(origine)[:60], statut, str(owner)[:200], str(role_personne or ''), now, now))
    desk.db.commit()
    appliquer_identites(desk)
    desk.audit('cabinet5689_alias', {'compte': compte, 'statut': statut})
    return next(a for a in aliases(desk, compte) if a['adresse'] == address)


# ------------------------------------------------------------------------------------------------ données déduites : des propositions
def deduire(desk, owner, role, texte, origine='signature'):
    """Propositions tirées d'une signature ou d'un document : le texte est une donnée, il n'est pas conservé, et rien n'est appliqué sans confirmation."""
    _peut_regler(desk, owner, role)
    importer_existant(desk)
    texte = str(texte or '')[:4000]
    _refuser_secrets(texte)
    found = []
    for address in ADRESSE.findall(texte):
        found.append(('adresse_propre', address.strip('.').lower()))
    for phone in re.findall(r'(?:\+\d{2,3}[ .]?|\b0)\d(?:[ .-]?\d{2}){4}\b', texte):
        found.append(('telephone', phone.strip()))
    match = re.search(r'(?i)\bbarreau\s+(?:de|du|des|d’|d\')\s*([^\n,;]{2,60})', texte)
    if match:
        found.append(('barreau', match[1].strip()))
    match = re.search(r'(?i)\btoque\s*(?:n°|numéro|:)?\s*([A-Za-z0-9-]{1,20})', texte)
    if match:
        found.append(('toque', match[1].strip()))
    connues = set(lire(desk)['own_addresses'])
    now = _now()
    crees = 0
    for field, value in found:
        if field == 'adresse_propre' and value in connues:
            continue
        crees += desk.db.execute('INSERT OR IGNORE INTO cabinet_proposals5689(id,field,value,origin,status,author,created) VALUES(?,?,?,?,?,?,?)',
                                 (uuid.uuid4().hex, field, value[:200], str(origine)[:60], 'a_confirmer', str(owner)[:200], now)).rowcount
    desk.db.commit()
    return {'propositions': propositions(desk), 'ajoutees': crees}


def decider_proposition(desk, owner, role, ident, decision):
    """Confirmer applique la valeur (nouvelle révision ou adresse confirmée) ; rejeter la garde écartée pour ne pas la reproposer."""
    _peut_regler(desk, owner, role)
    ensure_schema(desk)
    if decision not in ('confirmer', 'rejeter'):
        raise Stop('decision_invalide')
    row = desk.db.execute('SELECT * FROM cabinet_proposals5689 WHERE id=?', (str(ident or ''),)).fetchone()
    if not row:
        raise Stop('proposition_absente')
    now = _now()
    cur = desk.db.execute("UPDATE cabinet_proposals5689 SET status=?,decided_by=?,decided_at=? WHERE id=? AND status='a_confirmer'",
                          ('confirmee' if decision == 'confirmer' else 'rejetee', str(owner)[:200], now, row['id']))
    if not cur.rowcount:
        desk.db.rollback()
        raise Stop('proposition_deja_decidee')
    desk.db.commit()
    if decision == 'confirmer':
        if row['field'] == 'adresse_propre':
            enregistrer_alias(desk, owner, role, row['value'], 'confirmee', origine=row['origin'])
        else:
            ligne = _ligne(desk)
            data = json.loads(ligne['data_json'])
            data[row['field']] = row['value']
            _ecrire(desk, ligne, data, owner)
    return lire(desk)


# ------------------------------------------------------------------------------------------------ modes d'hébergement
def manifeste(desk):
    """Manifeste versionné : ce qui est réellement inclus ici, d'après la configuration, sans appel réseau. Un service non relié n'est jamais annoncé."""
    from .fonctions5631 import capabilities
    caps = capabilities(desk.c)
    row = _ligne(desk)
    choix = json.loads(row['data_json']).get('profil_deploiement') or ''
    externes = bool((desk.c.get('ai_gateway') or {}).get('providers') or desk.c.get('ai_providers'))
    cloud = bool((desk.c.get('nextcloud') or {}).get('stockage_cloud'))
    services = [{'id': 'application', 'libelle': 'Application AxiorHub', 'inclus': True, 'detail': 'Installée et exploitée par le cabinet.'},
                {'id': 'base_et_sauvegardes', 'libelle': 'Base de données et sauvegardes', 'inclus': True, 'detail': 'Locales ; leur planification et leur restauration relèvent du cabinet.'},
                {'id': 'documents', 'libelle': 'Documents', 'inclus': 'storage' in caps, 'detail': 'Stockage cloud relié par le cabinet.' if cloud else ('Nextcloud ou dossier local.' if 'storage' in caps else 'Non relié.')},
                {'id': 'messagerie', 'libelle': 'Messagerie', 'inclus': 'mail' in caps, 'detail': 'Boîte du cabinet.' if 'mail' in caps else 'Non reliée.'},
                {'id': 'modele_local', 'libelle': 'Modèle d’IA local', 'inclus': bool((desk.c.get('ollama') or {}).get('url')), 'detail': 'Serveur Ollama du cabinet.'},
                {'id': 'modele_externe', 'libelle': 'Modèle d’IA externe', 'inclus': externes, 'detail': 'Fournisseur choisi par le cabinet.' if externes else 'Aucun.'},
                {'id': 'hebergement_gere', 'libelle': 'Hébergement, surveillance et support gérés', 'inclus': False, 'detail': 'Non fournis par cette version.'}]
    return {'version': MANIFESTE_VERSION, 'choix': choix, 'choix_libelle': DEPLOIEMENTS.get(choix, {}).get('libelle', 'Non choisi'), 'modes': {k: dict(v) for k, v in DEPLOIEMENTS.items()}, 'services': services}


# ------------------------------------------------------------------------------------------------ assistant
def _assistant(desk):
    importer_existant(desk)
    cid = cabinet_id(desk)
    row = desk.db.execute('SELECT * FROM cabinet_wizard5689 WHERE cabinet_id=?', (cid,)).fetchone()
    if row:
        return row
    desk.db.execute("INSERT OR IGNORE INTO cabinet_wizard5689(cabinet_id,step,status,values_json,done_json,revision,updated,updated_by) VALUES(?,?,?,?,?,?,?,?)",
                    (cid, ETAPES[0][0], 'en_cours', '{}', '[]', 1, _now(), ''))
    desk.db.commit()
    return desk.db.execute('SELECT * FROM cabinet_wizard5689 WHERE cabinet_id=?', (cid,)).fetchone()


def assistant(desk):
    """État complet pour reprendre : étape enregistrée et saisies conservées."""
    row = _assistant(desk)
    faites = json.loads(row['done_json'])
    return {'etape': row['step'], 'statut': row['status'], 'revision': row['revision'], 'valeurs': json.loads(row['values_json']), 'faites': faites,
            'etapes': [{'id': i, 'libelle': l, 'faite': i in faites, 'courante': i == row['step']} for i, l in ETAPES], 'mis_a_jour': row['updated']}


def _valeurs_identite(data):
    champs = ('nom_cabinet', 'barreau', 'toque', 'adresse', 'telephone', 'activites', 'langue', 'profil_deploiement', 'adresses_propres', 'signature')
    out = {k: str(data[k])[:4000] if not isinstance(data[k], (list, tuple)) else '\n'.join(str(x) for x in data[k])[:4000] for k in champs if k in data}
    _refuser_secrets(out)
    return out


def _valeurs_modeles(data):
    out = {'template_refs': _liste(data.get('template_refs')), 'skill_refs': _liste(data.get('skill_refs'))}
    for key, value in data.items():                               # cases à cocher du formulaire : « modele:<id> » et « competence:<id> »
        if key.startswith('modele:') and value is True:
            out['template_refs'].append(key[7:])
        elif key.startswith('competence:') and value is True:
            out['skill_refs'].append(key[11:])
    return {k: list(dict.fromkeys(v)) for k, v in out.items()}


def _appliquer_identite(desk, owner, role, valeurs):
    """Étape validée : adresses confirmées une à une, signature en référence, profil. Rejouer l'étape ne crée rien de plus."""
    for address in re.split(r'[\s,;]+', valeurs.get('adresses_propres', '')):
        if address:
            enregistrer_alias(desk, owner, role, address, 'confirmee', origine='assistant')
    ligne = _ligne(desk)
    data = json.loads(ligne['data_json'])
    nouveau = {**data, **_nettoyer({k: v for k, v in valeurs.items() if k not in ('adresses_propres', 'signature')})}
    signature = valeurs.get('signature', '').strip()
    if signature:
        ref = 'sig-' + hashlib.sha256(signature.encode('utf-8')).hexdigest()[:16]
        if not any(s['ref'] == ref for s in nouveau.get('signatures', [])):
            nouveau['signatures'] = list(nouveau.get('signatures', [])) + [{'ref': ref, 'libelle': 'Signature saisie', 'texte': signature[:2000]}]
    if nouveau != data:
        _ecrire(desk, ligne, nouveau, owner)
    if signature:
        deduire(desk, owner, role, signature, 'signature')


def _appliquer_modeles(desk, owner, valeurs):
    ligne = _ligne(desk)
    data = json.loads(ligne['data_json'])
    nouveau = {**data, **_nettoyer(valeurs)}
    if nouveau != data:
        _ecrire(desk, ligne, nouveau, owner)


def avancer(desk, owner, role, donnees):
    """Enregistre l'étape, puis passe à la suivante ou reste : ``enregistrer`` garde les saisies sans rien appliquer, ``continuer`` applique. Révision périmée : 409."""
    _peut_regler(desk, owner, role)
    row = _assistant(desk)
    donnees = dict(donnees or {})
    action = str(donnees.get('action') or 'continuer')
    etape = str(donnees.get('etape') or row['step'])
    ordre = [i for i, _ in ETAPES]
    if etape not in ordre or action not in ('continuer', 'enregistrer', 'retour', 'aller'):
        raise Stop('etape_inconnue')
    attendue = donnees.get('revision')
    if attendue in (None, '') or int(attendue) != row['revision']:
        raise Stop('revision_perimee')
    valeurs = json.loads(row['values_json'])
    faites = json.loads(row['done_json'])
    if etape == 'identite':
        valeurs['identite'] = {**valeurs.get('identite', {}), **_valeurs_identite(donnees)}
    elif etape == 'modeles':
        valeurs['modeles'] = _valeurs_modeles(donnees)
    suivante, statut = etape, row['status']
    if action == 'continuer':
        if etape == 'identite':
            _appliquer_identite(desk, owner, role, valeurs['identite'])
        elif etape == 'modeles':
            _appliquer_modeles(desk, owner, valeurs['modeles'])
        elif etape == 'demonstration' and not derniere_demonstration(desk, owner):
            raise Stop('demonstration_requise')
        faites = list(dict.fromkeys(faites + [etape]))
        suivante = ordre[min(ordre.index(etape) + 1, len(ordre) - 1)]
        if len(faites) == len(ordre):
            statut = 'termine'
    elif action == 'retour':
        suivante = ordre[max(ordre.index(etape) - 1, 0)]
    elif action == 'aller':
        suivante = etape
    cur = desk.db.execute('UPDATE cabinet_wizard5689 SET step=?,status=?,values_json=?,done_json=?,revision=revision+1,updated=?,updated_by=? WHERE cabinet_id=? AND revision=?',
                          (suivante, statut, json.dumps(valeurs, ensure_ascii=False), json.dumps(faites), _now(), str(owner)[:200], row['cabinet_id'], row['revision']))
    if not cur.rowcount:
        desk.db.rollback()
        raise Stop('revision_perimee')
    desk.db.commit()
    return {**assistant(desk), 'message': 'Étape enregistrée. Vous pourrez reprendre ici.' if action == 'enregistrer' else 'Assistant mis à jour.'}


# ------------------------------------------------------------------------------------------------ démonstration
def _langue(desk, langue):
    from .i18n5631 import clean, current
    value = clean(langue) or ''
    if not value:
        row = _ligne(desk)
        value = clean(json.loads(row['data_json']).get('langue', '')) or clean(current()) or 'fr'
    return value if value in DEMO else 'fr'


def lancer_demonstration(desk, owner, role, cle, langue=''):
    """Mission fictive déterministe : aucun compte, aucun modèle, aucun accès à un dossier, aucun travail en file. Même clé : même mission."""
    ensure_schema(desk)
    cle = str(cle or '').strip()
    if not re.fullmatch(r'[A-Za-z0-9_.:-]{8,120}', cle):
        raise Stop('cle_idempotence_requise')
    old = desk.db.execute('SELECT * FROM demo_missions5689 WHERE owner=? AND cle=?', (str(owner), cle)).fetchone()
    if old:
        return {**json.loads(old['result_json']), 'reprise': True}
    lang = _langue(desk, langue)
    texte = DEMO[lang]
    ident = uuid.uuid4().hex
    out = {'id': ident, 'fictive': True, 'marque': texte['marque'], 'langue': lang, 'titre': texte['titre'], 'dossier': texte['dossier'], 'faits': list(texte['faits']), 'analyse': texte['analyse'],
           'suite': texte['suite'], 'limites': texte['fin'], 'etapes': [{'libelle': l, 'etat': 'terminee'} for l in texte['etapes']], 'modele': {'id': 'demonstration-note', 'version': DEMO_VERSION, 'langue': lang},
           'modele_ia_appele': False, 'compte_utilise': False, 'dossier_reel_touche': False, 'message': 'Démonstration terminée, sur des données fictives.'}
    desk.db.execute('INSERT INTO demo_missions5689(id,owner,cle,langue,state,result_json,created) VALUES(?,?,?,?,?,?,?)',
                    (ident, str(owner), cle, lang, 'terminee', json.dumps(out, ensure_ascii=False), _now()))
    desk.db.execute('DELETE FROM demo_missions5689 WHERE owner=? AND id NOT IN (SELECT id FROM demo_missions5689 WHERE owner=? ORDER BY created DESC LIMIT 10)', (str(owner), str(owner)))   # rétention
    desk.db.commit()
    desk.audit('cabinet5689_demonstration', {'langue': lang, 'fictive': True})
    return out


def derniere_demonstration(desk, owner):
    ensure_schema(desk)
    row = desk.db.execute('SELECT result_json FROM demo_missions5689 WHERE owner=? ORDER BY created DESC LIMIT 1', (str(owner),)).fetchone()
    return json.loads(row['result_json']) if row else None


def demonstration(desk, owner, ident):
    ensure_schema(desk)
    row = desk.db.execute('SELECT result_json FROM demo_missions5689 WHERE owner=? AND id=?', (str(owner), str(ident or ''))).fetchone()
    if not row:
        raise Stop('demonstration_absente')
    return json.loads(row['result_json'])


def demonstration_docx(desk, owner, ident):
    """Le document est recomposé à la demande : aucun fichier n'est conservé, ni déposé dans un dossier."""
    from .bibliotheque5639 import build_docx
    out = demonstration(desk, owner, ident)
    paras = [(out['marque'], 'titre'), (out['titre'], 'titre'), (out['dossier'], 'article')]
    paras += [(fait, 'liste') for fait in out['faits']] + [(out['analyse'], 'texte'), (out['suite'], 'texte'), (out['limites'], 'note'), (out['marque'], 'note')]
    return build_docx(paras, out['langue'], notice=False), 'demonstration-fictive-%s.docx' % out['langue']
