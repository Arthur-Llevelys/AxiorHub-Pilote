"""5.6.89 (objectif 1, chapitres 31 et 32) : capacités prouvées des connexions et préparation calculée par mission.

- **Preuves séparées** : authentification, lecture, création de test autorisée, réouverture, révocation. Chacune garde compte, capacité, date, résultat,
  version d'adaptateur, expiration et une erreur expurgée (un code, jamais un contenu). Une déclaration n'est pas une preuve : ni ``caps_json``, ni l'état
  « vérifiée » du registre, ni ``competences5632.available_capabilities`` ne rendent une capacité opérationnelle. Une ancienne mention « connecté » reste à qualifier.
- **Aucun second circuit d'authentification** : le registre ``connexions5652`` fait foi (``test``, ``adapter``, ``disconnect``) ; les comptes de la configuration
  (messagerie, documents, agenda, modèle local) passent par leurs clients habituels.
- **Tests sans effet extérieur** : jamais de courriel envoyé ni d'engagement payant. Seuls un brouillon sans destinataire et un fichier d'essai peuvent être créés,
  sur autorisation explicite ; le reste (signature, recommandé, facture, prélèvement) demeure « Écriture à autoriser » et validé opération par opération.
- **Ressource de test** : réservée avant sa création (une reprise ne recrée rien), identifiée exactement, nettoyée seulement si elle est inchangée, à la corbeille si le service en a une.
- **Révocation** : immédiate, relue avant chaque effet (aucun cache), elle suspend les seules étapes qui en dépendent et n'efface aucun livrable.
- **Opérationnel** se calcule par mission : capacités requises, preuves encore valides, droits et politique active.

La politique commune (``data_policy5689``) est consultée en un seul point : ``_politique``.
"""
from datetime import datetime, timedelta, timezone
from email.message import EmailMessage
from email.utils import format_datetime, formataddr
import hashlib
import json
from email import policy as email_policy
from pathlib import Path
import re
import sqlite3
import uuid

from .common import Stop, clean_path

SCHEMA = '''
CREATE TABLE IF NOT EXISTS capability_proofs5689(id TEXT PRIMARY KEY, run TEXT NOT NULL, connection TEXT NOT NULL, provider TEXT NOT NULL, account TEXT NOT NULL DEFAULT '',
  capability TEXT NOT NULL, kind TEXT NOT NULL, result TEXT NOT NULL, test_id TEXT NOT NULL DEFAULT '', generation TEXT NOT NULL, adapter_version TEXT NOT NULL,
  checked_at TEXT NOT NULL, expires_at TEXT NOT NULL, evidence TEXT NOT NULL DEFAULT '{}', error TEXT NOT NULL DEFAULT '', actor TEXT NOT NULL DEFAULT '');
CREATE INDEX IF NOT EXISTS capability_proofs5689_lien ON capability_proofs5689(connection, capability, kind);
CREATE TABLE IF NOT EXISTS capability_tests5689(test_id TEXT PRIMARY KEY, connection TEXT NOT NULL, capability TEXT NOT NULL, generation TEXT NOT NULL, marker TEXT NOT NULL,
  remote_ref TEXT NOT NULL DEFAULT '', fingerprint TEXT NOT NULL DEFAULT '', state TEXT NOT NULL, attempts INTEGER NOT NULL DEFAULT 0, created TEXT NOT NULL, updated TEXT NOT NULL,
  error TEXT NOT NULL DEFAULT '');
CREATE UNIQUE INDEX IF NOT EXISTS capability_tests5689_actif ON capability_tests5689(connection, capability, generation) WHERE state IN ('reservee','creee','a_verifier');
CREATE TABLE IF NOT EXISTS capability_revocations5689(id TEXT PRIMARY KEY, connection TEXT NOT NULL, capability TEXT NOT NULL, generation TEXT NOT NULL DEFAULT '', actor TEXT NOT NULL,
  reason TEXT NOT NULL DEFAULT '', at TEXT NOT NULL, lifted_at TEXT NOT NULL DEFAULT '', lifted_by TEXT NOT NULL DEFAULT '');
CREATE INDEX IF NOT EXISTS capability_revocations5689_lien ON capability_revocations5689(connection, capability, lifted_at);
CREATE TABLE IF NOT EXISTS qualification_runs5689(cle TEXT PRIMARY KEY, connection TEXT NOT NULL, capability TEXT NOT NULL, action TEXT NOT NULL, actor TEXT NOT NULL,
  result_json TEXT NOT NULL DEFAULT '', at TEXT NOT NULL);
'''
KINDS = {'authentification': 'Authentification', 'lecture': 'Lecture', 'creation_test': 'Création de test', 'reouverture': 'Réouverture', 'revocation': 'Révocation'}
RESULTATS = {'reussi': 'Réussi', 'echec': 'Échec', 'indetermine': 'Résultat incertain'}
VALIDITE_JOURS = {'authentification': 30, 'lecture': 30, 'creation_test': 90, 'reouverture': 90, 'revocation': 365}
# Preuves exigées par nature de capacité ; « interdit » : aucun test possible, chaque effet reste validé par une personne.
EXIGENCES = {'lecture': ('authentification', 'lecture'), 'brouillon': ('authentification', 'creation_test', 'reouverture'), 'ecriture': ('authentification', 'creation_test', 'reouverture')}
SONDES_ECRITURE = ('mail.create_draft', 'stockage.ecriture')
# Capacité → paramètres d'une lecture minimale, sans contenu de dossier.
LECTURES = {'calendar.read': {}, 'mail.read': {'limit': 1}, 'contacts.read': {}, 'documents.read_selected': {}, 'law.search': {'query': 'bail'},
            'case_law.search': {'query': 'bail'}, 'legal.search': {'query': 'bail'}, 'billing.clients': {'query': 'ZORVAL'}, 'crm.lookup': {'query': 'ZORVAL'},
            'stockage.lecture': {}}
DROITS = {'mail.create_draft': 'deposer', 'calendar.write': 'agenda'}
# Catégorie du réglage « Ce que l'IA peut voir », lue par la politique commune.
CATEGORIES = {'mail.read': 'courriels', 'mail.create_draft': 'courriels', 'calendar.read': 'agenda', 'calendar.write': 'agenda', 'contacts.read': 'contacts', 'crm.lookup': 'contacts',
              'documents.read_selected': 'documents', 'documents.export': 'documents', 'stockage.lecture': 'documents', 'stockage.ecriture': 'documents', 'billing.clients': 'factures'}
CONFIG = {
    'cfg-messagerie': {'label': 'Messagerie du cabinet (IMAP)', 'caps': {'mail.read': ('Lire les courriels', 'lecture', None), 'mail.create_draft': ('Créer un brouillon (jamais d’envoi)', 'brouillon', None)}},
    'cfg-stockage': {'label': 'Documents du cabinet (Nextcloud ou dossier local)', 'caps': {'stockage.lecture': ('Lire et lister les documents', 'lecture', None), 'stockage.ecriture': ('Écrire un document', 'ecriture', None)}},
    'cfg-agenda': {'label': 'Agendas du cabinet', 'caps': {'calendar.read': ('Lire l’agenda', 'lecture', None)}},
    'cfg-ia': {'label': 'Modèle d’IA local', 'caps': {'ai.generate': ('Serveur joignable et modèle installé', 'lecture', None)}},
}
RUBRIQUE = {'cfg-messagerie': 'connexions', 'cfg-stockage': 'stockage', 'cfg-agenda': 'connexions', 'cfg-ia': 'ia'}
# Capacité logique d'une mission → capacités du registre qui la satisfont.
ALTERNATIVES = {'legal.search': ('law.search', 'case_law.search', 'legal.search')}
ETATS = {'prouvee': 'Opérationnelle', 'a_qualifier': 'À qualifier', 'expiree': 'Preuve expirée : à requalifier', 'revoquee': 'Révoquée', 'refusee': 'Refusée par le service',
         'ecriture_a_autoriser': 'Écriture à autoriser', 'droits_insuffisants': 'Droits insuffisants', 'politique_refuse': 'Refusée par la politique du cabinet', 'non_configuree': 'Non configurée'}
RANG = ('prouvee', 'ecriture_a_autoriser', 'expiree', 'a_qualifier', 'refusee', 'droits_insuffisants', 'politique_refuse', 'revoquee', 'non_configuree')
SYNTHESES = {'operationnel': 'Opérationnel', 'lecture_disponible': 'Lecture disponible', 'ecriture_a_autoriser': 'Écriture à autoriser', 'a_qualifier': 'À qualifier', 'bloquee': 'Bloquée'}
# Mission : capacités requises et étapes ; une étape n'est suspendue que si l'une de SES capacités manque.
MISSIONS = {
    'demonstration': {'label': 'Mission de démonstration', 'etapes': (('faits', 'Lire les faits fictifs', ()), ('modele', 'Composer le modèle localisé', ()), ('resultat', 'Présenter le résultat', ()))},
    'lecture': {'label': 'Lecture et analyse d’un dossier', 'etapes': (('lire', 'Lire les documents du dossier', ('stockage.lecture',)), ('analyser', 'Analyser', ('ai.generate',)))},
    'redaction': {'label': 'Rédaction d’un projet dans le dossier', 'etapes': (('lire', 'Lire les documents du dossier', ('stockage.lecture',)), ('rediger', 'Rédiger', ('ai.generate',)),
                                                                               ('deposer', 'Déposer le projet dans le dossier', ('stockage.ecriture',)))},
    'courriel': {'label': 'Préparation d’une réponse par courriel', 'etapes': (('lire', 'Lire le courriel', ('mail.read',)), ('rediger', 'Rédiger', ('ai.generate',)),
                                                                               ('deposer', 'Déposer le brouillon', ('mail.create_draft',)))},
    'agenda': {'label': 'Lecture de l’agenda', 'etapes': (('lire', 'Lire l’agenda', ('calendar.read',)),)},
    'recherche': {'label': 'Recherche juridique', 'etapes': (('chercher', 'Chercher les sources', ('legal.search',)), ('rediger', 'Rédiger', ('ai.generate',)))},
    'consultation': {'label': 'Consultation', 'etapes': (('lire', 'Lire le dossier', ('stockage.lecture',)), ('chercher', 'Chercher les sources', ('legal.search',)),
                                                         ('rediger', 'Rédiger', ('ai.generate',)), ('deposer', 'Déposer la consultation', ('stockage.ecriture',)))},
    'contentieux': {'label': 'Écritures et pièces', 'etapes': (('lire', 'Lire le dossier', ('stockage.lecture',)), ('chercher', 'Chercher les sources', ('legal.search',)),
                                                               ('rediger', 'Rédiger', ('ai.generate',)), ('deposer', 'Déposer le projet', ('stockage.ecriture',)))},
}
# Genre d'une mission existante (missions567) → capacités d'effet relues avant chaque effet.
EFFETS_MISSION = {'mail': ('mail.create_draft',), 'document': ('stockage.ecriture',)}
ECRITURES_DAV = ('put_file', 'put_archive', 'replace_file', 'trash_file', 'create_folder', 'ensure_folder')
SUJET = 'Test de connexion AxiorHub %s, à ignorer'
CORPS = 'Élément de test créé par AxiorHub pour vérifier cette connexion. Il ne contient aucune donnée de dossier. Aucun destinataire, aucun envoi.\n'
PREFIXE_FICHIER = 'AxiorHub test de connexion %s.txt'
INCERTAINS = ('delai_http_depasse', 'connexion_http_indisponible', 'append_imap_incertain')
REFUS = ('http_401', 'http_403', 'jeton', 'autorisation_refusee', 'imap_identifiants_refuses', 'dossier_non_inscriptible')
RETENUES = 50


def _now():
    return datetime.now(timezone.utc).isoformat(timespec='microseconds')


def ensure_schema(desk):
    """Instructions séparées : ``executescript`` validerait la transaction de l'appelant."""
    if getattr(desk, '_schema5689r', False):
        return
    if not desk.db.execute("SELECT 1 FROM sqlite_master WHERE name='capability_tests5689_actif'").fetchone():
        for statement in SCHEMA.split(';'):
            if statement.strip():
                desk.db.execute(statement)
    desk._schema5689r = True


def _lire(desk, sql, args=()):
    """Lecture de garde : table absente (aucune révocation possible) sans rien créer ; autre panne, l'effet s'arrête."""
    try:
        return desk.db.execute(sql, args).fetchall()
    except sqlite3.OperationalError as ex:
        if 'no such table' in str(ex):
            return []
        raise Stop('controle_capacite_indisponible') from None


def _code(ex):
    """Erreur expurgée : un code court, sans adresse, chemin, URL ni contenu."""
    return re.sub(r'[^a-z0-9_]', '', str(ex).split(':')[0].lower())[:80] or 'erreur'


def _adapter_version(ad):
    return str(getattr(ad, 'version', '1') or '1')


# ------------------------------------------------------------------------------------------------ connexions vues par ce service
def _compte(identite):
    identite = identite if isinstance(identite, dict) else {}
    for key in ('compte', 'email', 'utilisateur', 'nom', 'societe', 'name'):
        if identite.get(key):
            return str(identite[key])[:200]
    return ''


def _config_declaree(desk, conn_id):
    from .fonctions5631 import capabilities
    caps = capabilities(desk.c)
    return {'cfg-messagerie': 'mail' in caps, 'cfg-stockage': 'storage' in caps, 'cfg-agenda': bool((desk.c.get('calendar') or {}).get('urls')),
            'cfg-ia': bool((desk.c.get('ollama') or {}).get('url') and (desk.c.get('ollama') or {}).get('model'))}[conn_id]


def _config_generation(desk, conn_id):
    """Empreinte des réglages de ce compte : changer d'hôte, d'identifiant ou de secret périme ses preuves."""
    from .guide5631 import fingerprint
    key = {'cfg-messagerie': 'messagerie', 'cfg-stockage': 'stockage', 'cfg-agenda': 'agenda', 'cfg-ia': 'ia'}[conn_id]
    try:
        values, version = fingerprint(desk.c, key)
    except (KeyError, TypeError, ValueError, Stop):
        return ''
    return hashlib.sha256((values + version).encode()).hexdigest()[:24]


def _config_compte(desk, conn_id):
    if conn_id == 'cfg-messagerie':
        return str((desk.c.get('mail') or {}).get('username') or '')[:200]
    if conn_id in ('cfg-stockage', 'cfg-agenda'):
        nc = desk.c.get('nextcloud') or {}
        return str(nc.get('username') or nc.get('local_path') or '')[:200]
    return str((desk.c.get('ollama') or {}).get('model') or '')[:200]


def _config_activee(desk, conn_id, cap):
    if cap == 'mail.create_draft':
        return str(desk.c.get('mode') or '') == 'drafts'      # mode « observation » : aucun brouillon tant que le cabinet ne l'a pas décidé
    return True


def connexion(desk, conn_id):
    """Vue commune d'une connexion du registre ou d'un compte de la configuration ; aucun secret."""
    from . import connexions5652 as cx
    conn_id = str(conn_id or '')
    if conn_id in CONFIG:
        spec = CONFIG[conn_id]
        return {'id': conn_id, 'provider': conn_id, 'label': spec['label'], 'owner': 'cabinet', 'generation': _config_generation(desk, conn_id), 'account': _config_compte(desk, conn_id),
                'caps': {c: {'enabled': _config_activee(desk, conn_id, c)} for c in spec['caps']}, 'spec_caps': spec['caps'], 'declaree': _config_declaree(desk, conn_id), 'row': None,
                'adapter_version': '1'}
    row = cx._row(desk, conn_id)
    spec = cx._spec(row['provider'])
    try:
        version = _adapter_version(_classe_adaptateur(row['provider']))
    except Stop:
        version = '1'
    return {'id': row['id'], 'provider': row['provider'], 'label': row['label'] or spec.get('label', row['provider']), 'owner': row['owner'], 'generation': str(row['generation']),
            'account': _compte(json.loads(row['identity_json'] or '{}')), 'caps': json.loads(row['caps_json'] or '{}'), 'spec_caps': spec.get('caps', {}),
            'declaree': cx.state_of(desk, row) not in ('non_configuree',), 'row': row, 'adapter_version': version}


def _classe_adaptateur(provider):
    from .adaptateurs5652 import ADAPTERS
    if provider in ADAPTERS:
        return ADAPTERS[provider]
    from .cloud5688 import adapter_class
    return adapter_class(provider)


def _nature(conn, cap):
    entry = conn['spec_caps'].get(cap)
    return entry[1] if entry else ''


def _sonde_ecriture(cap):
    return cap in SONDES_ECRITURE


def _peut_utiliser(desk, conn, owner, role):
    from . import connexions5652 as cx
    return conn['row'] is None or cx.can_use(conn['row'], owner) or (role == 'administrateur' and conn['owner'] == 'cabinet')


def _droit(desk, owner, role, droit):
    if role == 'administrateur':
        return True
    from .membres5650 import effective_rights
    try:
        return bool(effective_rights(desk.c['state_dir'], owner, role).get(droit))
    except Stop:
        return False


def _peut_regler(desk, conn, owner, role):
    """Qualifier ou révoquer : le titulaire d'une connexion personnelle, ou le droit « réglages » pour un compte du cabinet (mêmes règles que l'enregistrement)."""
    from . import connexions5652 as cx
    if conn['row'] is not None:
        if not cx._visible(conn['row'], owner, role):
            raise Stop('connexion_absente')
        cx._may_configure(desk, conn['owner'], owner, role)
        return
    if not _droit(desk, owner, role, 'reglages'):
        raise Stop('role_insuffisant')


# ------------------------------------------------------------------------------------------------ politique : un seul point de consultation
def _politique(desk, conn, capacite, mission='', owner='cabinet', role='', journal=False):
    """Décision de ``data_policy5689`` pour cette capacité, sur cette connexion : son refus ou une politique illisible bloque, aucun comportement ouvert par défaut.
    Un compte de la configuration (messagerie, documents, modèle local) reste chez le cabinet ; une connexion du registre est une destination du prestataire.
    Le calcul d'affichage ne journalise pas ; un essai réel, si."""
    from . import data_policy5689 as politique
    outil = 'llm' if capacite == 'ai.generate' else 'outil_mcp'
    decision = politique.evaluer(desk.c, outil, 'ollama' if conn['row'] is None else conn['provider'], finalite=str(mission or 'preparation'), labels=[CATEGORIES[capacite]] if capacite in CATEGORIES else [],
                                 acteur=owner, role=role, local=conn['row'] is None, mission=str(mission or ''), journal=journal)
    motifs = decision['denial_reasons']
    return {'permis': bool(decision['allowed']), 'evaluee': True, 'motif': str(motifs[0]).split(':')[0] if motifs else ''}


# ------------------------------------------------------------------------------------------------ preuves
def _preuve(desk, conn, capability, kind, result, run='', test_id='', evidence=None, error='', actor=''):
    now = datetime.now(timezone.utc)
    pid = uuid.uuid4().hex
    desk.db.execute('INSERT INTO capability_proofs5689(id,run,connection,provider,account,capability,kind,result,test_id,generation,adapter_version,checked_at,expires_at,evidence,error,actor) '
                    'VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)',
                    (pid, run or uuid.uuid4().hex, conn['id'], conn['provider'], conn['account'], capability, kind, result, test_id, conn['generation'], conn['adapter_version'],
                     now.isoformat(timespec='microseconds'), (now + timedelta(days=VALIDITE_JOURS[kind])).isoformat(timespec='microseconds'),
                     json.dumps(evidence or {}, ensure_ascii=False, sort_keys=True)[:1000], error[:80], str(actor)[:200]))
    desk.db.commit()
    return {'id': pid, 'nature': kind, 'capacite': capability, 'resultat': result, 'resultat_libelle': RESULTATS.get(result, result), 'erreur': error[:80], 'compte': conn['account'],
            'date': now.isoformat(timespec='seconds'), 'version_adaptateur': conn['adapter_version'], 'preuve': evidence or {}}


def _dernieres(desk, conn, capability):
    """Dernière preuve de chaque nature pour ce compte et cette génération ; l'authentification du registre vaut pour toutes ses capacités (« * »)."""
    ensure_schema(desk)
    out = {}
    for row in desk.db.execute('SELECT * FROM capability_proofs5689 WHERE connection=? AND capability IN (?,?) AND generation=? ORDER BY checked_at DESC, rowid DESC',
                               (conn['id'], capability, '*', conn['generation'])):
        out.setdefault(row['kind'], row)
    return out


def _derniere_revocation(desk, conn_id, capability):
    row = desk.db.execute('SELECT MAX(at) AS m FROM capability_revocations5689 WHERE connection=? AND capability IN (?,?)', (conn_id, capability, '*')).fetchone()
    return (row['m'] if row else '') or ''


def _valide(preuve, conn, seuil, now):
    return (preuve['result'] == 'reussi' and preuve['adapter_version'] == conn['adapter_version'] and preuve['expires_at'] > now and preuve['checked_at'] > seuil and
            (not conn['account'] or not preuve['account'] or conn['account'] == preuve['account']))


def revoquees(desk, conn_id):
    """Capacités révoquées et non rétablies : lecture directe, jamais mise en cache."""
    return {r['capability'] for r in _lire(desk, "SELECT capability FROM capability_revocations5689 WHERE connection=? AND lifted_at=''", (conn_id,))}


def _revoquee(desk, conn_id, capability):
    found = revoquees(desk, conn_id)
    return capability in found or '*' in found


def _refus_prouve(desk, conn_id, capability):
    """Dernier essai d'écriture sur ce compte : refus net du service (écriture bloquée avant effet, sans attendre une erreur en production)."""
    if not _lire(desk, "SELECT 1 FROM capability_proofs5689 WHERE connection=? AND capability=? AND kind='creation_test' AND result='echec' LIMIT 1", (conn_id, capability)):
        return False
    last = _dernieres(desk, connexion(desk, conn_id), capability).get('creation_test')
    return bool(last is not None and last['result'] == 'echec' and last['error'].startswith(REFUS))


def etat_capacite(desk, conn, capability, owner='cabinet', role='administrateur', mission=''):
    """Intersection déclaration, révocation, droits, politique et preuves encore valides. Calcul serveur, sans cache."""
    nature = _nature(conn, capability)
    label = conn['spec_caps'].get(capability, (capability,))[0]
    out = {'capacite': capability, 'libelle': label, 'nature': nature, 'connexion': conn['id'], 'connexion_libelle': conn['label'], 'compte': conn['account'], 'preuves': {}, 'expire': ''}
    derniers = _dernieres(desk, conn, capability)
    seuil = _derniere_revocation(desk, conn['id'], capability)
    now = _now()
    for kind, row in derniers.items():
        out['preuves'][kind] = {'resultat': row['result'], 'date': row['checked_at'][:19], 'expire': row['expires_at'][:19], 'erreur': row['error'], 'valide': _valide(row, conn, seuil, now),
                                'version_adaptateur': row['adapter_version'], 'compte': row['account']}

    def fini(etat, code=''):
        out.update(etat=etat, etat_libelle=ETATS[etat], code=code, correction=_correction(conn, capability, etat, code), url=_lien(conn))
        return out

    if not nature:
        return fini('non_configuree', 'capacite_non_proposee')
    if _revoquee(desk, conn['id'], capability):
        return fini('revoquee', 'capacite_revoquee')
    if not conn['declaree']:
        return fini('non_configuree', 'connexion_non_configuree')
    droit = DROITS.get(capability)
    if not _peut_utiliser(desk, conn, owner, role) or (droit and not _droit(desk, owner, role, droit)):
        return fini('droits_insuffisants', droit or 'connexion_personnelle')
    if not conn['caps'].get(capability, {}).get('enabled'):
        return fini('ecriture_a_autoriser' if nature != 'lecture' else 'non_configuree', 'capacite_desactivee')
    decision = _politique(desk, conn, capability, mission, owner, role)
    out['politique'] = {'evaluee': decision['evaluee'], 'permis': decision['permis']}
    if not decision['permis']:
        return fini('politique_refuse', decision['motif'] or 'politique_refuse')
    exigees = EXIGENCES.get(nature, ())
    if nature != 'lecture' and not _sonde_ecriture(capability):
        return fini('ecriture_a_autoriser', 'effet_valide_par_operation')
    for kind in exigees:
        row = derniers.get(kind)
        if row is not None and row['result'] == 'echec':
            return fini('refusee', row['error'] or 'echec')
    absentes = [k for k in exigees if k not in derniers]
    if absentes:
        return fini('ecriture_a_autoriser' if nature != 'lecture' and 'authentification' not in absentes else 'a_qualifier', 'preuve_absente')
    if not all(_valide(derniers[k], conn, seuil, now) for k in exigees):
        return fini('expiree' if not seuil or all(derniers[k]['checked_at'] > seuil for k in exigees) else 'a_qualifier', 'preuve_perimee')
    out['expire'] = min(derniers[k]['expires_at'] for k in exigees)[:19]
    return fini('prouvee')


def _lien(conn):
    if conn['row'] is None:
        return '/parametres?rubrique=' + RUBRIQUE.get(conn['id'], 'connexions')
    return '/parametres?rubrique=stockage' if str(conn['provider']).startswith('cloud_') else '/parametres?rubrique=connexions#cx52-' + conn['provider']


def _correction(conn, capability, etat, code):
    """Ce qui manque et la correction à faire, en clair."""
    nom = conn['label']
    if etat == 'prouvee':
        return ''
    if etat == 'revoquee':
        return 'Cette capacité a été révoquée sur %s. Pour la rétablir, un compte habilité la réautorise puis relance la qualification.' % nom
    if etat == 'non_configuree':
        return 'Renseignez et reliez %s dans les réglages, puis lancez la qualification.' % nom
    if etat == 'droits_insuffisants':
        from .membres5650 import RIGHTS
        droit = dict(RIGHTS).get(code)
        return ('Votre compte n’a pas le droit « %s » : demandez-le à l’administrateur du cabinet.' % droit) if droit else 'Cette connexion personnelle n’est pas partagée avec votre compte.'
    if etat == 'politique_refuse':
        from .data_policy5689 import MOTIFS
        return (MOTIFS.get(code) or 'La politique du cabinet refuse cet usage.') + ' Modifiez la politique ou choisissez un autre service.'
    if etat == 'ecriture_a_autoriser':
        if code == 'effet_valide_par_operation':
            return 'Cet effet n’est jamais testé (ni envoi, ni engagement payant) : chaque opération reste préparée puis validée par un compte habilité.'
        return 'Autorisez l’écriture sur %s, puis acceptez la création d’un élément de test, qui sera relu puis retiré.' % nom
    if etat == 'refusee':
        if code.startswith(REFUS):
            return 'Le service refuse cette opération au compte « %s » sur %s. Accordez à ce compte le droit manquant chez le service, puis relancez la qualification.' % (conn['account'] or 'relié', nom)
        from .web440 import human
        return 'La dernière qualification de %s a échoué : %s. Vérifiez le service, puis relancez-la.' % (nom, human(code))
    if etat == 'expiree':
        return 'La preuve de %s a expiré : relancez la qualification.' % nom
    return 'Cette connexion n’a jamais été qualifiée capacité par capacité : lancez la qualification de %s.' % nom


# ------------------------------------------------------------------------------------------------ préparation par mission
def _connexions_pour(desk, capability, owner, role):
    from . import connexions5652 as cx
    cx.ensure_schema(desk)
    wanted = ALTERNATIVES.get(capability, (capability,))
    out = []
    for conn_id, spec in CONFIG.items():
        if any(c in spec['caps'] for c in wanted):
            out.append((conn_id, next(c for c in wanted if c in spec['caps'])))
    for row in desk.db.execute('SELECT * FROM connexions5652 ORDER BY updated DESC'):
        spec = cx._spec(row['provider'])
        for c in wanted:
            if c in spec.get('caps', {}) and (cx.can_use(row, owner) or (role == 'administrateur' and row['owner'] == 'cabinet')):
                out.append((row['id'], c))
                break
    return out


def etat_logique(desk, capability, owner='cabinet', role='administrateur', mission=''):
    """Meilleur état d'une capacité requise parmi les connexions qui la proposent."""
    best = None
    for conn_id, cap in _connexions_pour(desk, capability, owner, role):
        ligne = etat_capacite(desk, connexion(desk, conn_id), cap, owner, role, mission)
        if best is None or RANG.index(ligne['etat']) < RANG.index(best['etat']):
            best = ligne
    if best is None:
        best = {'capacite': capability, 'libelle': capability, 'nature': 'lecture', 'connexion': '', 'connexion_libelle': '', 'compte': '', 'preuves': {}, 'expire': '', 'etat': 'non_configuree',
                'etat_libelle': ETATS['non_configuree'], 'code': 'aucune_connexion', 'correction': 'Aucune connexion ne propose cette capacité : reliez un service dans Paramètres, rubrique Connexions.',
                'url': '/parametres?rubrique=connexions'}
        if capability == 'legal.search' and _sources_declarees(desk):
            best.update(etat='a_qualifier', etat_libelle=ETATS['a_qualifier'], code='source_declaree_non_qualifiee',
                        correction='Des sources juridiques sont activées, mais elles ne se qualifient pas encore ici. Reliez Légifrance ou Judilibre pour obtenir une preuve de lecture.')
    return {**best, 'capacite_requise': capability}


def _sources_declarees(desk):
    from .sources5632 import enabled_items
    return bool(enabled_items(desk))


def _synthese(lignes):
    if all(l['etat'] == 'prouvee' for l in lignes):
        return 'operationnel'
    ko = [l for l in lignes if l['etat'] != 'prouvee']
    lecture_ok = any(l['etat'] == 'prouvee' and l['nature'] == 'lecture' for l in lignes)
    if lecture_ok and all(l['nature'] not in ('lecture', '') for l in ko):
        return 'ecriture_a_autoriser' if all(l['etat'] in ('ecriture_a_autoriser', 'a_qualifier', 'expiree') for l in ko) else 'lecture_disponible'
    if any(l['etat'] in ('revoquee', 'politique_refuse', 'droits_insuffisants', 'non_configuree') for l in ko):
        return 'bloquee'
    return 'a_qualifier'


def preparation(desk, owner, role, mission):
    """Readiness d'un type de mission : capacités requises, disponibles, raisons bloquantes, prochaine action, étapes suspendues. Calcul serveur."""
    ensure_schema(desk)
    spec = MISSIONS.get(str(mission or ''))
    if not spec:
        raise Stop('mission_inconnue')
    requises = list(dict.fromkeys(c for _, _, caps in spec['etapes'] for c in caps))
    lignes = [etat_logique(desk, c, owner, role, str(mission)) for c in requises]
    par_cap = {l['capacite_requise']: l for l in lignes}
    etapes = []
    for ident, label, caps in spec['etapes']:
        manque = [par_cap[c] for c in caps if par_cap[c]['etat'] != 'prouvee']
        etapes.append({'id': ident, 'libelle': label, 'capacites': list(caps), 'suspendue': any(l['etat'] in ('revoquee', 'refusee', 'droits_insuffisants', 'politique_refuse') for l in manque),
                       'prete': not manque, 'manque': [l['capacite_requise'] for l in manque]})
    raisons = [{'capacite': l['capacite_requise'], 'etat': l['etat'], 'etat_libelle': l['etat_libelle'], 'code': l.get('code', ''), 'connexion': l['connexion'], 'connexion_libelle': l['connexion_libelle'],
                'correction': l.get('correction', ''), 'url': l.get('url', '')} for l in lignes if l['etat'] != 'prouvee']
    etat = _synthese(lignes)
    return {'mission': str(mission), 'mission_libelle': spec['label'], 'etat': etat, 'etat_libelle': SYNTHESES[etat], 'capacites_requises': requises,
            'capacites_disponibles': [l['capacite_requise'] for l in lignes if l['etat'] == 'prouvee'], 'capacites': lignes, 'raisons': raisons, 'etapes': etapes,
            'prochaine_action': raisons[0]['correction'] if raisons else '', 'fictive': str(mission) == 'demonstration'}


def vue_connexion(desk, row):
    """Qualification d'une connexion du registre, capacité par capacité (``public_view``). Une ancienne mention « vérifiée » y reste à qualifier."""
    ensure_schema(desk)
    conn = connexion(desk, row['id'])
    out = {}
    for cap in conn['spec_caps']:
        ligne = etat_capacite(desk, conn, cap)
        out[cap] = {k: ligne[k] for k in ('etat', 'etat_libelle', 'correction', 'preuves', 'expire')}
    return out


def tableau(desk, owner, role):
    """Connexions proposées par l'assistant : comptes de la configuration et registre, avec l'état de chaque capacité."""
    from . import connexions5652 as cx
    cx.ensure_schema(desk)
    ensure_schema(desk)
    out = []
    ids = list(CONFIG) + [r['id'] for r in desk.db.execute('SELECT id FROM connexions5652 ORDER BY updated DESC') ]
    for conn_id in ids:
        conn = connexion(desk, conn_id)
        if conn['row'] is not None and not cx._visible(conn['row'], owner, role):
            continue
        out.append({'id': conn['id'], 'libelle': conn['label'], 'compte': conn['account'], 'proprietaire': conn['owner'], 'url': _lien(conn), 'capacites': [
            {**{k: ligne[k] for k in ('capacite', 'libelle', 'nature', 'etat', 'etat_libelle', 'correction', 'preuves')}, 'testable': _sonde_ecriture(c) or (_nature(conn, c) == 'lecture' and (conn['row'] is None or c in LECTURES))}
            for c in conn['spec_caps'] for ligne in (etat_capacite(desk, conn, c, owner, role),)]})
    return out


def essais(desk, conn_id):
    ensure_schema(desk)
    return [dict(r) for r in desk.db.execute('SELECT test_id,capability,state,created,updated,error FROM capability_tests5689 WHERE connection=? ORDER BY created DESC LIMIT 20', (conn_id,))]


# ------------------------------------------------------------------------------------------------ sondes
class SondeRegistre:
    """Connexion du registre : authentification par ``connexions5652.test``, lecture et brouillon par les opérations de l'adaptateur."""

    def __init__(self, desk, conn, owner, role):
        from . import connexions5652 as cx
        self.desk, self.conn, self.owner, self.role, self.cx = desk, conn, owner, role, cx
        self.ad = cx.adapter(desk, conn['row'])

    def authentifier(self):
        out = self.cx.test(self.desk, self.owner, self.role, self.conn['id'])
        if not out.get('ok'):
            raise Stop(out.get('erreur') or 'authentification_refusee')
        return {'compte_lu': bool(_compte(out.get('identite')))}

    def lire(self, cap):
        if cap not in LECTURES:
            raise Stop('sonde_lecture_indisponible')
        result = self.ad.call(cap, dict(LECTURES[cap]))
        return {'elements': sum(len(v) for v in result.values() if isinstance(v, list))}

    def creer(self, cap, marque):
        if cap != 'mail.create_draft':
            raise Stop('sonde_ecriture_indisponible')
        out = self.ad.call(cap, {'subject': SUJET % marque, 'body': CORPS, 'to': []})    # aucun destinataire : ce brouillon ne peut partir vers personne
        if not out.get('remote_ref'):
            raise Stop('reference_test_absente')
        return str(out['remote_ref'])

    def rouvrir(self, cap, ref, marque):
        return self.ad.test_relire(ref, marque)

    def retirer(self, cap, ref, info):
        return self.ad.test_retirer(ref)

    def chercher(self, cap, marque):
        return self.ad.test_chercher(marque)


class SondeStockage:
    """Fichier d'essai dans la première racine, par le client de documents en service ; retrait conditionnel à la version relue."""

    def __init__(self, desk, conn, client, racine):
        self.desk, self.conn, self.client, self.racine = desk, conn, client, racine

    def authentifier(self):
        self.client.list_folder(self.racine)
        return {'racine_ouverte': True}

    def lire(self, cap):
        return {'elements': len(self.client.list_folder(self.racine))}

    def _chemin(self, marque):
        return clean_path(self.racine + '/' + PREFIXE_FICHIER % marque)

    def creer(self, cap, marque):
        path = self._chemin(marque)
        self.client.put_file(path, CORPS.encode('utf-8') + marque.encode(), 'text/plain')
        return path

    def rouvrir(self, cap, ref, marque):
        info = self.client.stat(ref)
        raw = self.client.download({'path': ref, 'etag': info['etag'], 'size': info.get('size', 0)})
        return {'empreinte': hashlib.sha256(raw).hexdigest(), 'conforme': raw.endswith(marque.encode()), 'version': info['etag']}

    def retirer(self, cap, ref, info):
        self.client.trash_file(ref, info['version'])     # conditionnel : une version modifiée entre-temps est refusée par le service
        return {'corbeille': True}

    def chercher(self, cap, marque):
        path = self._chemin(marque)
        try:
            self.client.stat(path)
            return path
        except Stop as ex:
            if _code(ex) in ('http_404', 'fichier_introuvable', 'fichier_absent'):
                return ''
            raise


class SondeMessagerie:
    """Boîte IMAP de la configuration : brouillon sans destinataire, retrouvé par son en-tête ; jamais de SMTP."""

    def __init__(self, desk, conn):
        self.desk, self.conn, self.cfg = desk, conn, desk.c['mail']

    def _boite(self):
        from .mailbox import Mailbox
        return Mailbox(self.cfg)

    def authentifier(self):
        box = self._boite()
        box.close()
        return {'connexion_ouverte': True}

    def lire(self, cap):
        box = self._boite()
        try:
            return {'elements': len(box.search(self.cfg['inbox'], 'ALL'))}    # dossier ouvert en lecture seule : aucun message marqué lu
        finally:
            box.close()

    def creer(self, cap, marque):
        msg = EmailMessage(policy=email_policy.SMTP)
        msg['From'] = formataddr((str(self.cfg.get('from_name') or 'Cabinet'), str(self.cfg.get('from_address') or '')))
        msg['Subject'] = SUJET % marque
        msg['Date'] = format_datetime(datetime.now(timezone.utc))
        msg['Message-ID'] = '<axiorhub-test-%s@mail-agent.local>' % marque
        msg['X-AxiorHub-Test'] = marque
        msg.set_content(CORPS)
        box = self._boite()
        try:
            box.append_draft(msg)
        finally:
            box.close()
        found = self.chercher(cap, marque)
        if not found:
            raise Stop('brouillon_test_non_retrouve')
        return found

    def _message(self, box, ref):
        uid, validity = ref.split(':', 1)
        mail = box.fetch(self.cfg['drafts'], uid)
        if str(mail.uidvalidity) != validity:
            raise Stop('uidvalidity_modifiee')
        return mail

    def rouvrir(self, cap, ref, marque):
        box = self._boite()
        try:
            mail = self._message(box, ref)
            return {'empreinte': hashlib.sha256((mail.subject + '\n' + mail.text).encode('utf-8')).hexdigest(), 'conforme': str(mail.msg.get('X-AxiorHub-Test', '')) == marque and
                    '\\Draft' in mail.flags and not mail.msg.get('To'), 'version': ref}
        finally:
            box.close()

    def retirer(self, cap, ref, info):
        from .drafts440 import discard_draft
        uid, validity = ref.split(':', 1)
        out = discard_draft(self.cfg, uid, validity, 'yes')
        return {'corbeille': bool(out.get('moved_to_trash'))}

    def chercher(self, cap, marque):
        from .mailbox import imap_quote
        box = self._boite()
        try:
            uids = box.search(self.cfg['drafts'], 'UNDELETED', 'HEADER', 'X-AxiorHub-Test', imap_quote(marque))
            if len(uids) > 1:
                raise Stop('plusieurs_ressources_de_test')
            return ('%s:%s' % (uids[0], box.validity)) if uids else ''
        finally:
            box.close()


class SondeConfiguration:
    """Agenda et modèle local : essais existants de ``config567``, sans aucune écriture ni génération."""

    def __init__(self, desk, conn):
        self.desk, self.conn = desk, conn

    def _essai(self):
        from . import config567
        kind = 'ollama' if self.conn['id'] == 'cfg-ia' else 'nextcloud'
        out = config567.test(self.desk, {'connector': kind})
        if not out.get('ok'):
            raise Stop(out.get('code') or 'connexion_indisponible')
        return out

    def authentifier(self):
        self._essai()
        return {'service_joignable': True}

    def lire(self, cap):
        out = self._essai()
        return {'elements': len(out.get('calendars') or out.get('models') or []), 'generation_essayee': False}


def _racine(desk):
    roots = (desk.c.get('nextcloud_documents') or desk.c.get('nextcloud') or {}).get('roots') or []
    if not roots:
        raise Stop('racines_documentaires_obligatoires')
    return clean_path(roots[0])


def sonde(desk, conn, owner, role):
    if conn['id'] == 'cfg-messagerie':
        return SondeMessagerie(desk, conn)
    if conn['id'] == 'cfg-stockage':
        from .office440 import dav_client
        return SondeStockage(desk, conn, dav_client(desk), _racine(desk))
    if conn['id'] in CONFIG:
        return SondeConfiguration(desk, conn)
    if str(conn['provider']).startswith('cloud_'):
        from .cloud_gestion5688 import client_for
        return SondeStockage(desk, conn, client_for(desk, conn['provider']), _racine(desk))
    return SondeRegistre(desk, conn, owner, role)


# ------------------------------------------------------------------------------------------------ qualification
def _cle(cle):
    cle = str(cle or '').strip()
    if not re.fullmatch(r'[A-Za-z0-9_.:-]{8,120}', cle):
        raise Stop('cle_idempotence_requise')
    return cle


def _reprendre(desk, cle, conn_id, capability, action, actor):
    """Même clé : même résultat, sans nouvel essai. La clé d'un autre objet est refusée."""
    row = desk.db.execute('SELECT * FROM qualification_runs5689 WHERE cle=?', (cle,)).fetchone()
    if row:
        if (row['connection'], row['capability'], row['action']) != (conn_id, capability, action):
            raise Stop('cle_idempotence_deja_utilisee')
        return json.loads(row['result_json']) if row['result_json'] else None
    desk.db.execute('INSERT INTO qualification_runs5689(cle,connection,capability,action,actor,at) VALUES(?,?,?,?,?,?)', (cle, conn_id, capability, action, str(actor)[:200], _now()))
    desk.db.commit()
    return None


def _clore(desk, cle, out):
    desk.db.execute('UPDATE qualification_runs5689 SET result_json=? WHERE cle=?', (json.dumps(out, ensure_ascii=False, default=str), cle))
    desk.db.commit()
    return out


def _checkpoint(desk):
    from .pause5629 import checkpoint
    checkpoint(desk)


def _purger(desk):
    """Rétention : les RETENUES dernières preuves de chaque nature ; essais clos et reprises au-delà de 90 jours."""
    limite = (datetime.now(timezone.utc) - timedelta(days=90)).isoformat()
    desk.db.execute('''DELETE FROM capability_proofs5689 WHERE id IN (SELECT id FROM (SELECT id, ROW_NUMBER() OVER (PARTITION BY connection, capability, kind ORDER BY checked_at DESC) AS n
                       FROM capability_proofs5689) WHERE n>?)''', (RETENUES,))
    desk.db.execute("DELETE FROM capability_tests5689 WHERE state IN ('nettoyee','refusee') AND updated<?", (limite,))
    desk.db.execute('DELETE FROM qualification_runs5689 WHERE at<?', (limite,))
    desk.db.commit()


def qualifier(desk, owner, role, conn_id, capability, cle, autoriser_creation=False):
    """Qualification d'UNE capacité. Chaque preuve est écrite séparément ; une reprise ne recrée rien ; aucun envoi, aucun engagement."""
    ensure_schema(desk)
    cle = _cle(cle)
    conn = connexion(desk, conn_id)
    _peut_regler(desk, conn, owner, role)
    capability = str(capability or '')
    if capability not in conn['spec_caps']:
        raise Stop('capacite_inconnue')
    nature = _nature(conn, capability)
    if nature == 'ia_externe' or (nature != 'lecture' and not _sonde_ecriture(capability)):
        raise Stop('test_interdit_envoi_ou_engagement')
    if nature == 'lecture' and conn['row'] is not None and capability not in LECTURES:
        raise Stop('sonde_lecture_indisponible')
    if _revoquee(desk, conn['id'], capability):
        raise Stop('capacite_revoquee')
    decision = _politique(desk, conn, capability, 'qualification', owner, role, journal=True)
    if not decision['permis']:
        raise Stop('politique_refuse')
    prior = _reprendre(desk, cle, conn['id'], capability, 'qualifier', owner)
    if prior is not None:
        return {**prior, 'reprise': True}
    run = uuid.uuid4().hex
    preuves = []

    def fin(nettoyage=None):
        frais = connexion(desk, conn['id'])
        ligne = etat_capacite(desk, frais, capability, owner, role)
        out = {'connexion': conn['id'], 'capacite': capability, 'etat': ligne['etat'], 'etat_libelle': ligne['etat_libelle'], 'correction': ligne['correction'], 'preuves': preuves, 'nettoyage': nettoyage or {},
               'message': 'Qualification terminée : ' + ligne['etat_libelle'] + '.', 'code': ligne.get('code', '')}
        _purger(desk)
        desk.audit('capacite5689_qualifiee', {'connexion': conn['id'], 'capacite': capability, 'etat': ligne['etat']})
        return _clore(desk, cle, out)

    try:
        outil = sonde(desk, conn, owner, role)
    except Stop as ex:
        preuves.append(_preuve(desk, conn, capability, 'authentification', 'echec', run, error=_code(ex), actor=owner))
        return fin()
    _checkpoint(desk)
    try:
        evidence = outil.authentifier()
        conn = connexion(desk, conn['id'])                       # compte lu à l'instant par le registre
    except Stop as ex:
        conn = connexion(desk, conn['id'])
        preuves.append(_preuve(desk, conn, capability, 'authentification', 'echec', run, error=_code(ex), actor=owner))
        return fin()
    preuves.append(_preuve(desk, conn, capability, 'authentification', 'reussi', run, evidence=evidence, actor=owner))
    if nature == 'lecture':
        _checkpoint(desk)
        try:
            preuves.append(_preuve(desk, conn, capability, 'lecture', 'reussi', run, evidence=outil.lire(capability), actor=owner))
        except Stop as ex:
            preuves.append(_preuve(desk, conn, capability, 'lecture', 'echec', run, error=_code(ex), actor=owner))
        return fin()
    if not conn['caps'].get(capability, {}).get('enabled'):
        return fin()
    if not autoriser_creation:
        return fin()                                             # aucune écriture sans accord explicite : l'état reste « Écriture à autoriser »
    return fin(_essai_ecriture(desk, conn, capability, outil, run, owner, preuves))


def _essai_ecriture(desk, conn, capability, outil, run, actor, preuves):
    """Réservation, création unique, réouverture, nettoyage de CETTE ressource. Une création au résultat incertain n'est jamais rejouée à l'aveugle."""
    row = desk.db.execute("SELECT * FROM capability_tests5689 WHERE connection=? AND capability=? AND generation=? AND state IN ('reservee','creee','a_verifier') ORDER BY created DESC LIMIT 1",
                          (conn['id'], capability, conn['generation'])).fetchone()
    if row is None:
        test_id = uuid.uuid4().hex
        try:
            desk.db.execute('INSERT INTO capability_tests5689(test_id,connection,capability,generation,marker,state,created,updated) VALUES(?,?,?,?,?,?,?,?)',
                            (test_id, conn['id'], capability, conn['generation'], 'axh' + test_id[:20], 'reservee', _now(), _now()))
            desk.db.commit()
        except sqlite3.IntegrityError:
            desk.db.rollback()
            raise Stop('qualification_en_cours') from None
        row = desk.db.execute('SELECT * FROM capability_tests5689 WHERE test_id=?', (test_id,)).fetchone()
    test_id, marque, ref = row['test_id'], row['marker'], row['remote_ref']
    reprise = bool(ref)
    if not ref and row['attempts']:                              # tentative précédente interrompue : la ressource existe peut-être déjà
        try:
            ref = outil.chercher(capability, marque)
            reprise = bool(ref)
        except (Stop, AttributeError) as ex:
            code = _code(ex) if isinstance(ex, Stop) else 'recherche_test_indisponible'
            desk.db.execute("UPDATE capability_tests5689 SET state='a_verifier',error=?,updated=? WHERE test_id=?", (code, _now(), test_id))
            desk.db.commit()
            preuves.append(_preuve(desk, conn, capability, 'creation_test', 'indetermine', run, test_id, error=code, actor=actor))
            return {'etat': 'a_verifier', 'message': 'Une création de test précédente n’a pas pu être vérifiée : rien n’a été recréé.'}
    if not ref:
        desk.db.execute('UPDATE capability_tests5689 SET attempts=attempts+1,updated=? WHERE test_id=?', (_now(), test_id))
        desk.db.commit()
        _checkpoint(desk)
        try:
            ref = outil.creer(capability, marque)
        except Stop as ex:
            code = _code(ex)
            incertain = code in INCERTAINS or code.startswith('http_5')
            if not incertain:
                desk.db.execute("UPDATE capability_tests5689 SET state='refusee',error=?,updated=? WHERE test_id=?", (code, _now(), test_id))
                desk.db.commit()
            preuves.append(_preuve(desk, conn, capability, 'creation_test', 'indetermine' if incertain else 'echec', run, test_id, error=code, actor=actor))
            return {'etat': 'reprise_possible' if incertain else 'refusee'}
    desk.db.execute("UPDATE capability_tests5689 SET state='creee',remote_ref=?,error='',updated=? WHERE test_id=?", (ref, _now(), test_id))
    desk.db.commit()
    preuves.append(_preuve(desk, conn, capability, 'creation_test', 'reussi', run, test_id, {'ressource_reprise': reprise, 'envois': 0}, actor=actor))
    try:
        info = outil.rouvrir(capability, ref, marque)
    except Stop as ex:
        preuves.append(_preuve(desk, conn, capability, 'reouverture', 'echec', run, test_id, error=_code(ex), actor=actor))
        return {'etat': 'a_nettoyer', 'test_id': test_id}
    if not info.get('conforme'):
        preuves.append(_preuve(desk, conn, capability, 'reouverture', 'echec', run, test_id, error='reouverture_non_conforme', actor=actor))
        return {'etat': 'a_nettoyer', 'test_id': test_id}
    if not row['fingerprint']:
        desk.db.execute('UPDATE capability_tests5689 SET fingerprint=?,updated=? WHERE test_id=?', (info['empreinte'], _now(), test_id))
        desk.db.commit()
    preuves.append(_preuve(desk, conn, capability, 'reouverture', 'reussi', run, test_id, {'relu_conforme': True}, actor=actor))
    return _retirer_test(desk, conn, capability, test_id, outil, actor)


def _retirer_test(desk, conn, capability, test_id, outil, actor):
    """Nettoyage de la seule ressource de test, relue d'abord : si elle a changé (ou si elle n'est plus la nôtre), elle est conservée."""
    row = desk.db.execute('SELECT * FROM capability_tests5689 WHERE test_id=?', (test_id,)).fetchone()
    try:
        info = outil.rouvrir(capability, row['remote_ref'], row['marker'])
    except Stop as ex:
        if _code(ex) in ('http_404', 'message_imap_disparu', 'fichier_absent', 'fichier_introuvable'):
            desk.db.execute("UPDATE capability_tests5689 SET state='nettoyee',error='deja_absente',updated=? WHERE test_id=?", (_now(), test_id))
            desk.db.commit()
            return {'etat': 'nettoyee', 'deja_absente': True, 'test_id': test_id}
        return {'etat': 'a_nettoyer', 'test_id': test_id, 'erreur': _code(ex)}
    if info.get('empreinte') != row['fingerprint'] or not info.get('conforme'):
        desk.db.execute("UPDATE capability_tests5689 SET state='modifiee_conservee',updated=? WHERE test_id=?", (_now(), test_id))
        desk.db.commit()
        desk.audit('capacite5689_test_conserve', {'connexion': conn['id'], 'test': test_id})
        return {'etat': 'modifiee_conservee', 'test_id': test_id, 'message': 'La ressource de test a changé depuis sa création : elle n’a pas été touchée.'}
    _checkpoint(desk)
    try:
        out = outil.retirer(capability, row['remote_ref'], info) or {}
    except Stop as ex:
        return {'etat': 'a_nettoyer', 'test_id': test_id, 'erreur': _code(ex)}
    desk.db.execute("UPDATE capability_tests5689 SET state='nettoyee',error='',updated=? WHERE test_id=?", (_now(), test_id))
    desk.db.commit()
    return {'etat': 'nettoyee', 'corbeille': bool(out.get('corbeille')), 'test_id': test_id}


def nettoyer(desk, owner, role, conn_id, test_id):
    """Reprise du nettoyage d'une ressource de test restée en place."""
    ensure_schema(desk)
    conn = connexion(desk, conn_id)
    _peut_regler(desk, conn, owner, role)
    row = desk.db.execute("SELECT * FROM capability_tests5689 WHERE test_id=? AND connection=?", (str(test_id or ''), conn['id'])).fetchone()
    if not row:
        raise Stop('ressource_test_absente')
    if row['state'] != 'creee' or not row['remote_ref']:
        return {'etat': row['state'], 'test_id': row['test_id']}
    if row['generation'] != conn['generation']:
        raise Stop('compte_a_revalider')
    return _retirer_test(desk, conn, row['capability'], row['test_id'], sonde(desk, conn, owner, role), owner)


# ------------------------------------------------------------------------------------------------ raccordements du registre
def noter_authentification(desk, row, ok, code=''):
    """``connexions5652.test`` : un test réussi prouve l'authentification, et rien d'autre."""
    ensure_schema(desk)
    conn = connexion(desk, row['id'])
    _preuve(desk, conn, '*', 'authentification', 'reussi' if ok else 'echec', error='' if ok else _code(code))


def noter_refus(desk, row, capability, code):
    """Refus d'un service en usage réel : la preuve de cette capacité tombe aussitôt (jeton retiré chez le fournisseur, droit ôté)."""
    code = _code(code)
    if not code.startswith(('http_401', 'http_403', 'jeton', 'autorisation_refusee')):
        return
    ensure_schema(desk)
    conn = connexion(desk, row['id'])
    kind = 'lecture' if _nature(conn, capability) == 'lecture' else 'creation_test'
    _preuve(desk, conn, capability, kind, 'echec', error=code)


def avant_effet_connexion(desk, row, capability):
    """Dernier contrôle avant un effet par le registre : ni révocation, ni refus prouvé, quelle que soit la mention en cache dans ``caps_json``."""
    if _revoquee(desk, row['id'], capability):
        raise Stop('capacite_revoquee')
    if _refus_prouve(desk, row['id'], capability):
        raise Stop('ecriture_refusee_par_le_compte')


def _stockage_en_service(desk):
    """Connexion qui porte les documents : le stockage cloud choisi, sinon le compte de la configuration."""
    cloud = (desk.c.get('nextcloud') or {}).get('stockage_cloud')
    provider = cloud.get('provider') if isinstance(cloud, dict) else ''
    if provider:
        rows = _lire(desk, "SELECT id FROM connexions5652 WHERE provider=? AND owner='cabinet' ORDER BY updated DESC LIMIT 1", (provider,))
        if rows:
            return rows[0]['id']
    return 'cfg-stockage'


def _source_effet(desk, capability):
    if capability == 'stockage.ecriture':
        return _stockage_en_service(desk)
    return next(conn_id for conn_id, spec in CONFIG.items() if capability in spec['caps'])


def avant_effet(desk, capability):
    """Contrôle avant un effet par un compte de la configuration (missions, écriture de documents)."""
    from .fonctions_etat5689 import garde_version   # version incompatible : même arrêt qu'une révocation
    garde_version(desk, capability)
    conn_id = _source_effet(desk, capability)
    if _revoquee(desk, conn_id, capability):
        raise Stop('capacite_revoquee')
    if _refus_prouve(desk, conn_id, capability):
        raise Stop('ecriture_refusee_par_le_compte')


def garde_mission(desk, genre):
    """``missions567.check_authority`` : la mission relit ses seules capacités d'effet ; une mission d'un autre genre continue."""
    for capability in EFFETS_MISSION.get(str(genre or ''), ()):
        avant_effet(desk, capability)


def brider_ecriture(desk, client):
    """Écritures du client de documents refusées si la capacité est révoquée ; lectures et livrables existants intacts."""
    db = getattr(desk, 'db', None)
    if db is None:
        return client
    try:
        avant_effet(desk, 'stockage.ecriture')
    except Stop as ex:
        code = _code(ex)

        def refus(*_args, **_kwargs):
            raise Stop(code)
        for name in ECRITURES_DAV:
            setattr(client, name, refus)
    return client


def garde_configuration(mail_cfg, capability='mail.create_draft', conn_id='cfg-messagerie'):
    """Dépôt IMAP : lecture fraîche de la révocation, sans Desk. Un état illisible arrête l'effet."""
    state_dir = (mail_cfg or {}).get('etat5689')
    if not state_dir:
        return
    path = Path(state_dir) / 'desk.sqlite3'
    if not path.is_file():
        return
    db = sqlite3.connect(path.resolve().as_uri() + '?mode=ro', uri=True, timeout=2)
    try:
        found = db.execute("SELECT 1 FROM capability_revocations5689 WHERE connection=? AND capability IN (?,?) AND lifted_at='' LIMIT 1", (conn_id, capability, '*')).fetchone()
    except sqlite3.OperationalError as ex:
        if 'no such table' in str(ex):
            return
        raise Stop('controle_capacite_indisponible') from None
    finally:
        db.close()
    if found:
        raise Stop('capacite_revoquee')


# ------------------------------------------------------------------------------------------------ révocation
def revoquer(desk, owner, role, conn_id, capability='', cle='', motif=''):
    """Révocation immédiate d'une capacité, ou de toute la connexion. Rien n'est effacé des livrables ; les opérations non envoyées qui en dépendent sont suspendues."""
    from . import connexions5652 as cx
    ensure_schema(desk)
    cle = _cle(cle)
    conn = connexion(desk, conn_id)
    _peut_regler(desk, conn, owner, role)
    capability = str(capability or '')
    if capability and capability not in conn['spec_caps']:
        raise Stop('capacite_inconnue')
    prior = _reprendre(desk, cle, conn['id'], capability or '*', 'revoquer', owner)
    if prior is not None:
        return {**prior, 'reprise': True}
    now = _now()
    cible = capability or '*'
    fournisseur = 'non_concerne'
    if conn['row'] is not None and not capability:
        out = cx.disconnect(desk, owner, role, conn['id'])       # jetons supprimés, génération suivante, opérations non envoyées annulées : le circuit existant
        fournisseur = str(out.get('revocation') or 'non_disponible')
    else:
        desk.db.execute('INSERT INTO capability_revocations5689(id,connection,capability,generation,actor,reason,at) VALUES(?,?,?,?,?,?,?)',
                        (uuid.uuid4().hex, conn['id'], cible, conn['generation'] if conn['row'] is not None else '', str(owner)[:200], str(motif or '')[:200], now))
        if conn['row'] is not None:
            caps = json.loads(conn['row']['caps_json'] or '{}')
            caps[capability] = {**caps.get(capability, {}), 'enabled': False}
            desk.db.execute('UPDATE connexions5652 SET caps_json=?,version=version+1,updated=? WHERE id=?', (json.dumps(caps), now[:19] + '+00:00', conn['id']))
        desk.db.commit()                                         # une opération préparée reste en place : ``validate`` la bloque avant tout effet tant que la révocation dure
    conn = connexion(desk, conn['id'])
    encore = _encore_accessible(desk, conn, capability, owner)
    preuve = _preuve(desk, conn, cible, 'revocation', 'echec' if encore else 'reussi', evidence={'fournisseur': fournisseur, 'capacites_encore_utilisables': encore}, actor=owner)
    desk.audit('capacite5689_revoquee', {'connexion': conn['id'], 'capacite': cible, 'fournisseur': fournisseur})
    return _clore(desk, cle, {'connexion': conn['id'], 'capacite': cible, 'etat': 'revoquee', 'etat_libelle': ETATS['revoquee'], 'preuve': preuve, 'message': 'Accès révoqué : les étapes qui en dépendent sont suspendues. Les documents déjà créés restent en place.'})


def _encore_accessible(desk, conn, capability, owner):
    """Contre-vérification serveur : la capacité ne doit plus sortir du résolveur du registre."""
    from . import connexions5652 as cx
    if conn['row'] is None:
        return []
    caps = [capability] if capability else [c for c, v in conn['caps'].items() if v.get('enabled')]
    return [c for c in caps if any(r['id'] == conn['id'] for r in cx._connections_for(desk, c, owner))]


def reautoriser(desk, owner, role, conn_id, capability='', cle=''):
    """Lève une révocation par une décision humaine. Les anciennes preuves ne reviennent pas : la capacité repart à qualifier."""
    ensure_schema(desk)
    cle = _cle(cle)
    conn = connexion(desk, conn_id)
    _peut_regler(desk, conn, owner, role)
    capability = str(capability or '')
    cible = capability or '*'
    prior = _reprendre(desk, cle, conn['id'], cible, 'reautoriser', owner)
    if prior is not None:
        return {**prior, 'reprise': True}
    now = _now()
    desk.db.execute("UPDATE capability_revocations5689 SET lifted_at=?,lifted_by=? WHERE connection=? AND capability=? AND lifted_at=''", (now, str(owner)[:200], conn['id'], cible))
    if conn['row'] is not None and capability:
        caps = json.loads(conn['row']['caps_json'] or '{}')
        caps[capability] = {**caps.get(capability, {}), 'enabled': True}
        desk.db.execute('UPDATE connexions5652 SET caps_json=?,version=version+1,updated=? WHERE id=?', (json.dumps(caps), now[:19] + '+00:00', conn['id']))
    desk.db.commit()
    desk.audit('capacite5689_reautorisee', {'connexion': conn['id'], 'capacite': cible})
    return _clore(desk, cle, {'connexion': conn['id'], 'capacite': cible, 'etat': 'a_qualifier', 'etat_libelle': ETATS['a_qualifier'], 'message': 'Révocation levée : relancez la qualification.'})


# ------------------------------------------------------------------------------------------------ compétences
CORRESPONDANCE = {'storage.read': ('stockage.lecture', 'cfg-stockage'), 'ai.generate': ('ai.generate', 'cfg-ia'), 'legal.text': ('legal.search', '')}


def filtrer_capacites(desk, declarees, prouvees=False, owner='cabinet', role='administrateur'):
    """``competences5632.available_capabilities`` : une capacité révoquée disparaît toujours ; ``prouvees`` ne garde que ce qu'une preuve valide soutient."""
    out = set(declarees)
    for nom, (cap, conn_id) in CORRESPONDANCE.items():
        if nom not in out:
            continue
        if conn_id and _revoquee(desk, conn_id, cap):
            out.discard(nom)
        elif prouvees:
            ensure_schema(desk)
            if etat_logique(desk, cap, owner, role)['etat'] != 'prouvee':
                out.discard(nom)
    return out
