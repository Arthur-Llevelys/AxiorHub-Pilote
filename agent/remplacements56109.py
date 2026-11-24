"""5.6.109 (lot G2, G07) : remplacements et postulation liés à la même audience.

Raccordé aux services existants, sans annuaire de confrères ni événement recopié :
- **même audience** : un remplacement désigne la fiche ``audiences5688`` (identifiant stable, horaire et lieu lus dans l'événement de
  l'agenda) ; il est affiché depuis le dossier (onglet Agenda) et par audience ; jamais un second événement ;
- **instruction versionnée** : confrère (contact existant, adresse vérifiée, jamais une adresse ou un compte du cabinet), rôle (substitution
  ou postulation), instantané de l'audience, périmètre, instructions, versions exactes des documents (registre commun), honoraires convenus
  ou « À convenir », date de réponse souhaitée. Toute modification — ou un changement d'horaire de l'audience — produit une NOUVELLE version
  à confirmer au confrère : l'acceptation d'une version ne vaut pas pour la suivante ;
- **preuves** : envoi déclaré avec sa preuve (le logiciel n'envoie rien), acceptation EXPRESSE prouvée (un accusé automatique ou l'ouverture
  d'un lien ne prouvent rien), refus, compte rendu ; chaque preuve porte l'empreinte de son texte et la version d'instruction visée ;
- **accès du confrère** : partage commun de G03 (``partage_client56109``) limité aux versions retenues, expirant au lendemain de l'audience,
  révoqué à l'annulation (historique et reçus conservés) ; aucun accès aux courriels ni aux autres dossiers ;
- **suivi** : la tâche « compte rendu à recevoir » rejoint la file existante (``taches5688``, origine « audience ») une seule fois ; le compte
  rendu reçu devient une version du registre des livrables rattachée au dossier ; l'honoraire du confrère est un engagement DISTINCT de la
  facture du client : aucune facture, aucun débours n'est créé, sa qualification est une décision du cabinet.
Aucun appel de modèle : tout est utilisable IA en pause.
"""
from datetime import datetime, timedelta, timezone
from decimal import Decimal, InvalidOperation
import hashlib
from html import escape as e
import json
import sqlite3
import uuid

from .common import Stop
from . import partage_client56109 as P

PREFIXE = 'm56109/remplacements/'
ROLES_MISSION = {'substitution': 'Substitution (remplacement à l’audience)', 'postulation': 'Postulation'}
ETATS = {'brouillon': 'Brouillon', 'preparee': 'Demande préparée', 'envoyee': 'Envoyée (preuve déclarée)', 'acceptee': 'Acceptée (preuve)',
         'refusee': 'Refusée', 'pieces_partagees': 'Pièces partagées', 'realisee': 'Audience réalisée', 'compte_rendu_recu': 'Compte rendu reçu',
         'honoraires_a_rapprocher': 'Honoraires à rapprocher', 'cloturee': 'Clôturée', 'annulee': 'Annulée'}
FINAUX = ('refusee', 'cloturee', 'annulee')
# action → (états de départ, état d'arrivée) ; ``None`` : l'état ne change pas
TRANSITIONS = {'preparer': (('brouillon',), 'preparee'), 'envoyer': (('preparee',), 'envoyee'), 'accepter': (('envoyee',), 'acceptee'),
               'refuser': (('envoyee',), 'refusee'), 'partager': (('acceptee',), 'pieces_partagees'),
               'realiser': (('acceptee', 'pieces_partagees'), 'realisee'), 'compte_rendu': (('realisee',), 'compte_rendu_recu'),
               'honoraire': (tuple(k for k in ETATS if k not in FINAUX), None), 'a_rapprocher': (('compte_rendu_recu',), 'honoraires_a_rapprocher'),
               'rapprocher': (('honoraires_a_rapprocher',), 'cloturee'), 'cloturer': (('compte_rendu_recu',), 'cloturee'),
               'annuler': (tuple(k for k in ETATS if k not in FINAUX), 'annulee')}
SOURCES_NON_PROBANTES = ('accuse_automatique', 'ouverture_lien', 'lecture_seule')
QUALIFICATIONS = {'a_qualifier': 'À qualifier par le cabinet', 'honoraire_confrere': 'Honoraire du confrère à régler par le cabinet',
                  'retrocession': 'Rétrocession convenue', 'refacturation_decidee': 'Refacturation au client décidée par le cabinet',
                  'debours_decide': 'Débours (décision expresse du cabinet)'}
SCHEMA = '''
CREATE TABLE IF NOT EXISTS remplacements56109(id TEXT PRIMARY KEY, cabinet TEXT NOT NULL, audience_id TEXT NOT NULL, matter_id TEXT NOT NULL,
  confrere_contact_id TEXT NOT NULL, confrere_email TEXT NOT NULL, confrere_verifie_par TEXT NOT NULL, role TEXT NOT NULL CHECK(role IN ('substitution','postulation')),
  etat TEXT NOT NULL, instruction_version INTEGER NOT NULL DEFAULT 0, acceptee_version INTEGER NOT NULL DEFAULT 0,
  honoraire_minor INTEGER CHECK(honoraire_minor IS NULL OR honoraire_minor >= 0), devise TEXT NOT NULL DEFAULT 'EUR',
  honoraire_statut TEXT NOT NULL DEFAULT 'a_convenir' CHECK(honoraire_statut IN ('a_convenir','convenu','rapproche')),
  qualification TEXT NOT NULL DEFAULT 'a_qualifier', rapprochement_ref TEXT NOT NULL DEFAULT '', partage_id TEXT NOT NULL DEFAULT '',
  tache_compte_rendu TEXT NOT NULL DEFAULT '', compte_rendu_document_id TEXT NOT NULL DEFAULT '', compte_rendu_version_id TEXT NOT NULL DEFAULT '',
  motif TEXT NOT NULL DEFAULT '', version INTEGER NOT NULL DEFAULT 1, cree_par TEXT NOT NULL, cree_le TEXT NOT NULL, maj_le TEXT NOT NULL);
CREATE INDEX IF NOT EXISTS remplacements56109_audience ON remplacements56109(audience_id);
CREATE INDEX IF NOT EXISTS remplacements56109_dossier ON remplacements56109(matter_id, cree_le);
CREATE TABLE IF NOT EXISTS remplacements_instructions56109(remplacement_id TEXT NOT NULL, version INTEGER NOT NULL CHECK(version >= 1), contenu TEXT NOT NULL,
  sha256 TEXT NOT NULL CHECK(length(sha256)=64), cree_par TEXT NOT NULL, cree_le TEXT NOT NULL, PRIMARY KEY(remplacement_id, version));
CREATE TABLE IF NOT EXISTS remplacements_preuves56109(id TEXT PRIMARY KEY, remplacement_id TEXT NOT NULL, instruction_version INTEGER NOT NULL,
  nature TEXT NOT NULL CHECK(nature IN ('envoi','acceptation','refus','compte_rendu','honoraire','rapprochement','annulation')),
  preuve TEXT NOT NULL, sha256 TEXT NOT NULL, par TEXT NOT NULL, le TEXT NOT NULL);
CREATE INDEX IF NOT EXISTS remplacements_preuves56109_r ON remplacements_preuves56109(remplacement_id, le);
CREATE TABLE IF NOT EXISTS remplacements_ops56109(acteur TEXT NOT NULL, cle TEXT NOT NULL, nature TEXT NOT NULL, empreinte TEXT NOT NULL,
  etat TEXT NOT NULL, resultat TEXT NOT NULL DEFAULT '{}', cree_le TEXT NOT NULL, PRIMARY KEY(acteur, cle));
'''
TABLES = ('remplacements56109', 'remplacements_instructions56109', 'remplacements_preuves56109', 'remplacements_ops56109')
MESSAGES = {
    'introuvable': 'Remplacement introuvable, ou action non permise.',
    'audience_absente': 'Audience introuvable, ou action non permise.',
    'confrere_inconnu': 'Choisissez le confrère parmi les contacts du cabinet.',
    'adresse_confrere_requise': 'Le contact du confrère n’a pas d’adresse : complétez sa fiche.',
    'alias_du_cabinet': 'Cette adresse appartient au cabinet : un confrère destinataire ne peut pas être une adresse du cabinet.',
    'role_mission_invalide': 'Choisissez substitution ou postulation.',
    'transition_impossible': 'Cette étape n’est pas possible dans l’état actuel du remplacement.',
    'instruction_requise': 'Préparez d’abord l’instruction au confrère.',
    'preuve_requise': 'Indiquez la preuve (référence du courriel, de l’écrit ou de l’échange).',
    'acceptation_non_prouvee': 'Un accusé automatique ou l’ouverture d’un lien ne prouvent pas l’acceptation : il faut une acceptation expresse du confrère.',
    'instruction_modifiee_a_renvoyer': 'L’instruction a changé depuis l’envoi : envoyez la nouvelle version au confrère avant de recueillir son acceptation.',
    'montant_invalide': 'Montant invalide.',
    'devise_invalide': 'Devise invalide (code à trois lettres).',
    'qualification_inconnue': 'Qualification inconnue.',
    'documents_requis_pour_partager': 'Aucun document retenu dans l’instruction : rien à partager.',
}


def _maintenant():
    return P.maintenant()


def _adresses_cabinet(desk):
    out = set()
    try:
        from .mailbox import own_addresses
        out |= {str(x).lower() for x in own_addresses(desk.c.get('mail') or desk.c.get('imap') or {})}
    except Exception:   # noqa: BLE001 — registre des identités illisible : les comptes du cabinet restent refusés ci-dessous
        pass
    try:
        from .audiences5688 import members
        out |= {str(k).lower() for k in members(desk)}
    except Exception:   # noqa: BLE001
        pass
    return out


def _confrere(desk, cid, a):
    from . import appels56109 as AP
    c = AP._contact(desk, str(cid or '').strip()) if cid else None
    if not c:
        raise Stop('confrere_inconnu')
    email = str(c.get('email') or '').strip().lower()
    if '@' not in email:
        raise Stop('adresse_confrere_requise')
    if email in _adresses_cabinet(desk) or email == a.compte.lower():
        raise Stop('alias_du_cabinet')
    return c, email


def _audience(desk, a, hid):
    """Fiche d'audience existante (``audiences5688``) ; dossier relu par la garde commune ; absente ou interdite : même 404 neutre."""
    from . import audiences5688 as au
    if not P.IDENT.fullmatch(str(hid or '')):
        raise Stop('introuvable')
    if not desk.db.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='audiences5688'").fetchone():
        raise Stop('introuvable')                               # H1 : schéma créé au démarrage ; jamais de DDL pendant une lecture
    row = desk.db.execute(au.SELECT + ' WHERE h.id=?', (str(hid),)).fetchone()
    if not row:
        raise Stop('introuvable')
    if row['matter']:
        P.dossier(desk, a, row['matter'])
    out = dict(row)                                              # H1 : sans ``au.view`` (annuaire des membres : DDL) pendant une lecture
    out['type_label'] = au.TYPES.get(out['type_audience'], out['type_audience'])
    return out


def _remplacement(desk, a, rid):
    if not P.IDENT.fullmatch(str(rid or '')):
        raise Stop('introuvable')
    row = desk.db.execute('SELECT * FROM remplacements56109 WHERE id=?', (str(rid),)).fetchone()
    if not row:
        raise Stop('introuvable')
    P.dossier(desk, a, row['matter_id'])
    return dict(row)


def _instruction(desk, rid, version=None):
    sql = 'SELECT * FROM remplacements_instructions56109 WHERE remplacement_id=? %s ORDER BY version DESC LIMIT 1' % ('AND version=?' if version else '')
    row = desk.db.execute(sql, (rid, version) if version else (rid,)).fetchone()
    if not row:
        return None
    return {**dict(row), 'contenu': json.loads(row['contenu'])}


def _instantane_audience(h):
    return {'audience_id': h['id'], 'starts': h['starts'], 'ends': h['ends'], 'location': h['location'], 'title': h['title'], 'juridiction': h['juridiction'],
            'chambre': h['chambre'], 'rg': h['rg'], 'type': h['type_audience'], 'statut': h['statut']}


def echeance_acces(desk, rid):
    """C2 : fin de l'accès du confrère = fin du lendemain de l'audience COURANTE (fuseau du cabinet), lue sans écriture ; ``''`` si
    l'audience ou son horaire sont illisibles (l'appelant ferme alors l'accès)."""
    try:
        r = desk.db.execute('SELECT h.event_id, h.starts AS h_starts, a.starts AS a_starts FROM remplacements56109 x JOIN audiences5688 h ON h.id=x.audience_id '
                            'LEFT JOIN agenda5634 a ON a.id=h.event_id WHERE x.id=?', (str(rid),)).fetchone()
    except sqlite3.Error:
        return ''
    debut = (r['a_starts'] or r['h_starts']) if r else ''
    try:
        jour = datetime.fromisoformat(str(debut)).astimezone(P.tz(desk)).date()
    except ValueError:
        return ''
    return datetime.combine(jour + timedelta(days=2), datetime.min.time(), P.tz(desk)).astimezone(timezone.utc).isoformat(timespec='seconds')


def ecarts_audience(desk, a, row):
    """Champs de l'audience modifiés depuis l'instruction en vigueur (lecture seule : aucune écriture, R05)."""
    ins = _instruction(desk, row['id'])
    if not ins:
        return []
    try:
        h = _audience(desk, a, row['audience_id'])
    except Stop:
        return ['audience']
    avant = ins['contenu']['audience']
    maintenant = _instantane_audience(h)
    return [k for k in ('starts', 'ends', 'location', 'statut') if str(avant.get(k) or '') != str(maintenant.get(k) or '')]


# ------------------------------------------------------------------------------------------------ créer, instruire
def creer(desk, acteur_, data, cle):
    """« Demander un remplacement » depuis une audience : confrère vérifié, rôle. Brouillon : rien n'est envoyé. Avocat et administrateur."""
    a = P.acteur(acteur_)
    P.personne(desk, a)
    P.valideur(a)
    h = _audience(desk, a, data.get('audience'))
    if not h['matter']:
        raise Stop('introuvable')
    role = str(data.get('role') or '')
    if role not in ROLES_MISSION:
        raise Stop('role_mission_invalide')
    c, email = _confrere(desk, data.get('confrere'), a)
    parametres = {'h': h['id'], 'c': c['id'], 'role': role}

    def faire():
        rid = 'rp-' + uuid.uuid4().hex[:24]
        quand = _maintenant()
        desk.db.execute('INSERT INTO remplacements56109(id,cabinet,audience_id,matter_id,confrere_contact_id,confrere_email,confrere_verifie_par,role,etat,cree_par,cree_le,maj_le) '
                        "VALUES(?,?,?,?,?,?,?,?,'brouillon',?,?,?)", (rid, P.cabinet(desk), h['id'], h['matter'], c['id'], email,
                                                                    'contact du cabinet vérifié par ' + a.compte, role, a.compte, quand, quand))
        desk.db.commit()
        desk.audit('remplacement56109_cree', {'remplacement': rid, 'audience': h['id'], 'matter': h['matter'], 'par': a.compte})
        return {'id': rid}

    out = P.executer(desk, 'remplacements_ops56109', a.compte, cle, 'creer', parametres, faire)
    return dict(vue(desk, a, out['id']), rejoue=out['rejoue'])


def _montant(valeur):
    if valeur in (None, ''):
        return None
    try:
        d = Decimal(str(valeur).replace(',', '.').replace(' ', ''))
    except InvalidOperation:
        raise Stop('montant_invalide') from None
    if d < 0 or d > Decimal('10000000') or d != d.quantize(Decimal('0.01')):
        raise Stop('montant_invalide')
    return int(d * 100)


def instruire(desk, acteur_, rid, data):
    """Nouvelle version de l'instruction au confrère (instantané de l'audience, périmètre, instructions, versions exactes, honoraires,
    réponse souhaitée). Après un envoi ou une acceptation, l'état revient à « Demande préparée » : la nouvelle version est à envoyer et à
    faire accepter ; un partage déjà ouvert sur d'autres documents est révoqué (accès futur fermé, historique gardé)."""
    a = P.acteur(acteur_)
    P.personne(desk, a)
    P.valideur(a)
    row = _remplacement(desk, a, rid)
    if row['etat'] in FINAUX or row['etat'] in ('realisee', 'compte_rendu_recu', 'honoraires_a_rapprocher'):
        raise Stop('transition_impossible')
    P._version(row, data.get('version'))
    h = _audience(desk, a, row['audience_id'])
    docs = [P.version_registre(desk, a, row['matter_id'], d, v) for d, v in P._paires(data.get('documents'))]
    contenu = {'audience': _instantane_audience(h), 'role': row['role'], 'perimetre': P.texte(data.get('perimetre'), 2000),
               'instructions': P.texte(data.get('instructions'), 8000), 'reponse_souhaitee': str(data.get('reponse_souhaitee') or '')[:10],
               'honoraires': {'montant_minor': _montant(data.get('honoraire')), 'devise': str(data.get('devise') or 'EUR').upper()[:3]},
               'documents': [{'document_id': d['document_id'], 'version_id': d['version_id'], 'sha256': d['sha256'], 'titre': d['titre']} for d in docs]}
    if len(contenu['honoraires']['devise']) != 3 or not contenu['honoraires']['devise'].isalpha():
        raise Stop('devise_invalide')
    parametres = {'rid': row['id'], 'v': row['version'], 'contenu': contenu}

    def faire():
        r = desk.db.execute('SELECT * FROM remplacements56109 WHERE id=?', (row['id'],)).fetchone()
        if r['version'] != row['version']:
            raise Stop('version_perimee')
        n = r['instruction_version'] + 1
        empreinte = hashlib.sha256(P.canon(contenu).encode()).hexdigest()
        quand = _maintenant()
        etat = 'preparee' if r['etat'] in ('envoyee', 'acceptee', 'pieces_partagees') else r['etat']
        desk.db.execute('INSERT INTO remplacements_instructions56109(remplacement_id,version,contenu,sha256,cree_par,cree_le) VALUES(?,?,?,?,?,?)',
                        (r['id'], n, P.canon(contenu), empreinte, a.compte, quand))
        hm = contenu['honoraires']['montant_minor']
        desk.db.execute('UPDATE remplacements56109 SET instruction_version=?, etat=?, honoraire_minor=?, devise=?, honoraire_statut=?, version=version+1, maj_le=? WHERE id=?',
                        (n, etat, hm, contenu['honoraires']['devise'], 'convenu' if hm is not None else 'a_convenir', quand, r['id']))
        desk.db.commit()
        if r['partage_id'] and r['etat'] in ('acceptee', 'pieces_partagees'):
            P.revoquer_interne(desk, a, r['partage_id'], 'Nouvelle instruction au confrère (version %d)' % n)
            desk.db.execute("UPDATE remplacements56109 SET partage_id='' WHERE id=?", (r['id'],))
            desk.db.commit()
        desk.audit('remplacement56109_instruction', {'remplacement': r['id'], 'version': n, 'sha256': empreinte, 'par': a.compte})
        return {'id': r['id'], 'instruction_version': n}

    out = P.executer(desk, 'remplacements_ops56109', a.compte, data.get('cle'), 'instruire', parametres, faire)
    v = vue(desk, a, out['id'])
    return dict(v, rejoue=out['rejoue'], message=('Instruction version %d enregistrée.' % out['instruction_version'])
                + (' Elle est à envoyer et à faire accepter par le confrère.' if v['etat'] == 'preparee' and out['instruction_version'] > 1 else ''))


# ------------------------------------------------------------------------------------------------ transitions
def _preuve(desk, rid, version, nature, texte, a):
    t = P.texte(texte, 2000)
    if len(t) < 5:
        raise Stop('preuve_requise')
    desk.db.execute('INSERT INTO remplacements_preuves56109(id,remplacement_id,instruction_version,nature,preuve,sha256,par,le) VALUES(?,?,?,?,?,?,?,?)',
                    ('rpp-' + uuid.uuid4().hex[:20], rid, version, nature, t, hashlib.sha256(t.encode()).hexdigest(), a.compte, _maintenant()))


def _tache_compte_rendu(desk, a, row):
    """Tâche « compte rendu à recevoir » dans la file existante (``taches5688``, origine « audience ») : une seule, retrouvée si elle existe."""
    from . import taches5688
    titre = 'Compte rendu du confrère à recevoir (remplacement %s)' % row['id'][-8:]
    for t in taches5688.of_origin(desk, 'audience', row['audience_id']):
        if t['title'] == titre:
            return t['id']
    try:
        out = taches5688.create(desk, {'origine': 'audience', 'origine_ref': row['audience_id'], 'title': titre, 'responsable': a.compte}, a.compte)
    except Stop as ex:
        if str(ex) != 'responsable_inconnu':
            raise
        out = taches5688.create(desk, {'origine': 'audience', 'origine_ref': row['audience_id'], 'title': titre}, a.compte)   # « Sans responsable »
    return out['id']


def transition(desk, acteur_, rid, data, client=None):
    """Étape du remplacement (``action``) : version attendue, clé d'idempotence ; preuves exigées là où le travail réel l'exige."""
    a = P.acteur(acteur_)
    P.personne(desk, a)
    P.valideur(a)
    row = _remplacement(desk, a, rid)
    action = str(data.get('action') or '')
    if action not in TRANSITIONS:
        raise Stop('transition_impossible')
    depart, arrivee = TRANSITIONS[action]
    parametres = {'rid': row['id'], 'action': action, 'v': str(data.get('version')), 'd': {k: str(data.get(k) or '')[:2000] for k in
                                                                                           ('preuve', 'source', 'expresse', 'honoraire', 'devise', 'qualification', 'reference', 'texte', 'motif')}}
    unique = {}

    def faire():
        r = dict(desk.db.execute('SELECT * FROM remplacements56109 WHERE id=?', (row['id'],)).fetchone())
        P._version(r, data.get('version'))
        if r['etat'] not in depart:
            raise Stop('transition_impossible')
        if action != 'annuler' and action != 'honoraire' and not r['instruction_version']:
            raise Stop('instruction_requise')
        champs = {}
        if action == 'envoyer':
            _preuve(desk, r['id'], r['instruction_version'], 'envoi', data.get('preuve'), a)
        elif action in ('accepter', 'refuser'):
            source = str(data.get('source') or '')
            if action == 'accepter' and (source in SOURCES_NON_PROBANTES or data.get('expresse') is not True):
                raise Stop('acceptation_non_prouvee')
            envoi = desk.db.execute("SELECT instruction_version FROM remplacements_preuves56109 WHERE remplacement_id=? AND nature='envoi' ORDER BY le DESC LIMIT 1",
                                    (r['id'],)).fetchone()
            if not envoi or envoi['instruction_version'] != r['instruction_version']:
                raise Stop('instruction_modifiee_a_renvoyer')
            _preuve(desk, r['id'], r['instruction_version'], 'acceptation' if action == 'accepter' else 'refus', data.get('preuve'), a)
            if action == 'accepter':
                champs['acceptee_version'] = r['instruction_version']
        elif action == 'partager':
            ins = _instruction(desk, r['id'])
            if not ins['contenu']['documents']:
                raise Stop('documents_requis_pour_partager')
            fin = datetime.fromisoformat(ins['contenu']['audience']['starts']).astimezone(P.tz(desk)).date() + timedelta(days=1)
            fin = max(fin, P.jour_cabinet(desk))
            base = 'rp-%s-v%d' % (r['id'], r['instruction_version'])
            prep = P.preparer(desk, a, r['matter_id'], {'destinataire': r['confrere_email'], 'identite_verifiee': True, 'canal': 'paquet',
                                                       'permissions': ['lire', 'telecharger'], 'expiration': fin.isoformat(),
                                                       'ressources': [{'document_id': d['document_id'], 'version_id': d['version_id']} for d in ins['contenu']['documents']]},
                              base + '-p', origine='remplacement', objet_lie=r['id'])
            conf = P.confirmer(desk, a, prep['id'], {'cle': base + '-c', 'version': prep['version']}, client)
            champs['partage_id'] = prep['id']
            if conf.get('download'):
                unique['download'] = conf['download']     # paquet du confrère (jeton en clair) : rendu, jamais rangé dans le reçu
        elif action == 'realiser':
            champs['tache_compte_rendu'] = _tache_compte_rendu(desk, a, r)
        elif action == 'compte_rendu':
            texte = P.texte(data.get('texte'), 30000)
            if len(texte) < 5:
                raise Stop('preuve_requise')
            reg = _compte_rendu_registre(desk, r, texte, a)
            champs.update(compte_rendu_document_id=reg['document_id'], compte_rendu_version_id=reg['version_id'])
            _preuve(desk, r['id'], r['instruction_version'], 'compte_rendu', 'Compte rendu reçu (empreinte %s)' % reg['sha256'][:16], a)
        elif action == 'honoraire':
            m = _montant(data.get('honoraire'))
            devise = str(data.get('devise') or r['devise'] or 'EUR').upper()[:3]
            if len(devise) != 3 or not devise.isalpha():
                raise Stop('devise_invalide')
            q = str(data.get('qualification') or r['qualification'])
            if q not in QUALIFICATIONS:
                raise Stop('qualification_inconnue')
            champs.update(honoraire_minor=m, devise=devise, honoraire_statut='convenu' if m is not None else 'a_convenir', qualification=q)
        elif action == 'rapprocher':
            ref = P.texte(data.get('reference'), 300)
            if len(ref) < 3:
                raise Stop('preuve_requise')
            _preuve(desk, r['id'], r['instruction_version'], 'rapprochement', ref, a)
            champs.update(rapprochement_ref=ref, honoraire_statut='rapproche')
        elif action == 'annuler':
            motif = P.texte(data.get('motif'), 300)
            if len(motif) < 3:
                raise Stop('preuve_requise')
            _preuve(desk, r['id'], r['instruction_version'], 'annulation', motif, a)
            champs['motif'] = motif
        if arrivee:
            champs['etat'] = arrivee
        champs['maj_le'] = _maintenant()
        n = desk.db.execute('UPDATE remplacements56109 SET %s, version=version+1 WHERE id=? AND version=?' % ', '.join(k + '=?' for k in champs),
                            tuple(champs.values()) + (r['id'], r['version'])).rowcount
        if n != 1:
            raise Stop('version_perimee')
        desk.db.commit()
        if r['partage_id'] or champs.get('partage_id'):          # C2 : toute étape recalcule l'échéance enregistrée de l'accès du confrère
            borne = echeance_acces(desk, r['id'])
            if borne:
                P.ajuster_echeance(desk, champs.get('partage_id') or r['partage_id'], borne)
                desk.db.commit()
        if action == 'annuler' and r['partage_id']:
            P.revoquer_interne(desk, a, r['partage_id'], 'Remplacement annulé : ' + champs['motif'])   # accès futurs fermés ; reçus et preuves conservés
        desk.audit('remplacement56109_' + action, {'remplacement': r['id'], 'etat': champs.get('etat', r['etat']), 'par': a.compte})
        return {'id': r['id']}

    out = P.executer(desk, 'remplacements_ops56109', a.compte, data.get('cle'), 'transition', parametres, faire)
    res = dict(vue(desk, a, out['id']), rejoue=out['rejoue'])
    if unique.get('download') and not out['rejoue']:
        res.update(download=unique['download'], noreload=True, message='Pièces partagées : le paquet du confrère est téléchargé (versions retenues seulement).')
    else:
        res['message'] = '%s : %s.' % (ETATS[res['etat']], 'demande rejouée' if out['rejoue'] else 'enregistré')
    return res


def _compte_rendu_registre(desk, r, texte, a):
    from . import livrables5689 as liv
    liv.ensure_schema(desk)
    empreinte = hashlib.sha256(texte.encode('utf-8')).hexdigest()
    n = desk.db.execute("SELECT COUNT(*) FROM prepared_source_links5689 WHERE source_service='remplacements56109' AND source_object_id=?", (r['id'],)).fetchone()[0] + 1
    item = liv.observe(desk, {'service': 'remplacements56109', 'object': r['id'], 'version': 'rev:%d' % n, 'matter': r['matter_id'], 'kind': 'note',
                              'label': 'Compte rendu du confrère', 'title': 'Compte rendu d’audience du confrère', 'state': 'verified', 'sha256': empreinte,
                              'owner': a.compte, 'provenance': {'producteur': 'remplacements56109', 'audience': r['audience_id'], 'sha256': empreinte}})
    row = desk.db.execute('SELECT document_id, version_id FROM prepared_items5689 WHERE id=?', (item,)).fetchone()
    return {'document_id': row['document_id'], 'version_id': row['version_id'], 'sha256': empreinte}


# ------------------------------------------------------------------------------------------------ lectures
def vue(desk, acteur_, rid):
    a = P.acteur(acteur_)
    row = _remplacement(desk, a, rid)
    out = {k: row[k] for k in row.keys() if k != 'cabinet'}
    out['etat_libelle'] = ETATS.get(row['etat'], row['etat'])
    out['role_libelle'] = ROLES_MISSION[row['role']]
    out['qualification_libelle'] = QUALIFICATIONS.get(row['qualification'], row['qualification'])
    out['instruction'] = _instruction(desk, row['id'])
    out['instructions'] = [{'version': r['version'], 'sha256': r['sha256'], 'cree_le': r['cree_le'], 'cree_par': r['cree_par']}
                           for r in desk.db.execute('SELECT version, sha256, cree_le, cree_par FROM remplacements_instructions56109 WHERE remplacement_id=? ORDER BY version',
                                                    (row['id'],))]
    out['preuves'] = [dict(r) for r in desk.db.execute('SELECT instruction_version, nature, preuve, sha256, par, le FROM remplacements_preuves56109 '
                                                        'WHERE remplacement_id=? ORDER BY le', (row['id'],))]
    out['ecarts_audience'] = ecarts_audience(desk, a, row)
    out['acceptation_valide'] = bool(row['acceptee_version']) and row['acceptee_version'] == row['instruction_version']
    out['partage'] = None
    if row['partage_id']:
        try:
            p = P.vue(desk, a, row['partage_id'])
            out['partage'] = {'id': p['id'], 'etat': p['etat'], 'etat_libelle': p['etat_libelle'], 'expire_le': p['expire_le'],
                              'ressources': [(x['document_id'], x['version_id']) for x in p['ressources']]}
        except Stop:
            out['partage'] = None
    out['honoraire'] = {'montant_minor': row['honoraire_minor'], 'devise': row['devise'], 'statut': row['honoraire_statut'],
                        'mention': 'Engagement distinct de la facture du client : aucune facture ni débours n’est créé par ce suivi.'}
    return out


def remplacements_audience(desk, acteur_, hid):
    a = P.acteur(acteur_)
    h = _audience(desk, a, hid)
    return [vue(desk, a, r['id']) for r in desk.db.execute('SELECT id FROM remplacements56109 WHERE audience_id=? ORDER BY cree_le DESC', (h['id'],))]


def remplacements_dossier(desk, acteur_, mid):
    a = P.acteur(acteur_)
    P.dossier(desk, a, mid)
    return [vue(desk, a, r['id']) for r in desk.db.execute('SELECT id FROM remplacements56109 WHERE matter_id=? ORDER BY cree_le DESC LIMIT 30', (str(mid),))]


# ------------------------------------------------------------------------------------------------ bloc du dossier (onglet Agenda)
def _form(route, champs, bouton, extra='', confirmer_='', reload_=True, cls='ax-btn ghost'):
    caches = ''.join('<input type="hidden" name="%s" value="%s">' % (e(k, quote=True), e(str(v), quote=True)) for k, v in champs.items())
    return ('<form class="m5-form rl109-form" data-api="%s"%s%s>%s<input type="hidden" name="cle" value="%s">%s<button class="%s" type="submit">%s</button></form>') % (
        e(PREFIXE + route, quote=True), ' data-reload' if reload_ else '', (' data-confirm="%s"' % e(confirmer_, quote=True)) if confirmer_ else '', caches,
        uuid.uuid4().hex, extra, cls, e(bouton))


def _carte(x, choix):
    t = lambda action, bouton, extra='', **k: _form('%s/transition' % x['id'], {'action': action, 'version': x['version']}, bouton, extra, **k)
    actions = ''
    etat = x['etat']
    if etat not in FINAUX and etat not in ('realisee', 'compte_rendu_recu', 'honoraires_a_rapprocher'):
        opts = ''.join('<option value="%s">%s — version %s</option>' % (e('%s|%s' % (v['document_id'], v['version_id']), quote=True), e(v['titre']), e(v['version_id'][:16]))
                       for v in choix)
        actions += _form('%s/instruire' % x['id'], {'version': x['version']}, 'Enregistrer une nouvelle version de l’instruction',
                         '<label>Périmètre <input name="perimetre" maxlength="2000"></label><label>Instructions <textarea name="instructions" maxlength="8000"></textarea></label>'
                         '<label>Documents retenus <select name="documents" multiple size="3">%s</select></label>'
                         '<label>Honoraires convenus (vide : à convenir) <input name="honoraire" inputmode="decimal"></label>'
                         '<label>Réponse souhaitée le <input type="date" name="reponse_souhaitee"></label>' % opts)
    if etat == 'brouillon':
        actions += t('preparer', 'Marquer la demande préparée')
    if etat == 'preparee':
        actions += t('envoyer', 'Déclarer l’envoi (preuve)', '<label>Preuve de l’envoi <input name="preuve" required maxlength="2000"></label>')
    if etat == 'envoyee':
        actions += t('accepter', 'Enregistrer l’acceptation expresse', '<label>Preuve de l’acceptation <input name="preuve" required maxlength="2000"></label>'
                     '<label class="rl109-case"><input type="checkbox" name="expresse"> Acceptation expresse du confrère (pas un accusé automatique)</label>')
        actions += t('refuser', 'Enregistrer le refus', '<label>Preuve du refus <input name="preuve" required maxlength="2000"></label>')
    if etat == 'acceptee':
        actions += t('partager', 'Partager les pièces retenues', reload_=False, cls='ax-btn')
    if etat in ('acceptee', 'pieces_partagees'):
        actions += t('realiser', 'Audience réalisée')
    if etat == 'realisee':
        actions += t('compte_rendu', 'Enregistrer le compte rendu reçu', '<label>Compte rendu <textarea name="texte" required maxlength="30000"></textarea></label>')
    if etat == 'compte_rendu_recu':
        actions += t('a_rapprocher', 'Honoraires à rapprocher') + t('cloturer', 'Clôturer')
    if etat == 'honoraires_a_rapprocher':
        actions += t('rapprocher', 'Rapprocher', '<label>Référence (facture du confrère, règlement) <input name="reference" required maxlength="300"></label>')
    if etat not in FINAUX:
        actions += t('annuler', 'Annuler le remplacement', '<label>Motif <input name="motif" required maxlength="300"></label>',
                     confirmer_='Annuler ce remplacement ? Les accès futurs du confrère seront révoqués ; l’historique est conservé.')
    ecart = ('<p class="rl109-a-confirmer" role="status">L’audience a changé depuis l’instruction (%s) : préparez une nouvelle version et faites-la accepter.</p>'
             % e(', '.join(x['ecarts_audience']))) if x['ecarts_audience'] and etat not in FINAUX else ''
    preuves = ''.join('<li>%s (instruction v%d) — %s <span class="ax-muted">%s</span></li>' % (e(p['nature']), p['instruction_version'], e(p['preuve'][:160]), e(p['le'][:16]))
                      for p in x['preuves'])
    montant = ('%s %s' % (str(Decimal(x['honoraire_minor']) / 100).replace('.', ','), x['devise'])) if x['honoraire_minor'] is not None else 'À convenir'
    return ('<li class="rl109-carte"><p><strong>%s</strong> — %s · %s · instruction v%d%s</p>%s<p class="ax-muted">Honoraires du confrère : %s (%s). %s</p>'
            '%s<ul>%s</ul><div class="rl109-actions">%s</div></li>') % (
        e(x['confrere_email']), e(x['role_libelle']), e(x['etat_libelle']), x['instruction_version'],
        ' · acceptée' if x['acceptation_valide'] else '', ecart, e(montant), e(x['qualification_libelle']), e(x['honoraire']['mention']),
        ('<p>Accès du confrère : %s, jusqu’au %s</p>' % (e(x['partage']['etat_libelle']), e(x['partage']['expire_le'][:10]))) if x['partage'] else '', preuves, actions)


def bloc_dossier(desk, prefix, m, acteur_=None):
    """Remplacements des audiences du dossier (onglet Agenda) : lecture seule, aucune écriture ni modèle ; nouvelle interface."""
    from .membres5650 import current_author, viewer_role
    from . import audiences5688 as au
    try:
        a = P.acteur(acteur_ or (current_author() or 'cabinet', viewer_role()))
        from .interface56100 import active
        if not active(desk, a.compte):
            return ''
        liste = remplacements_dossier(desk, a, m['id'])
        audiences = desk.db.execute(au.SELECT + " WHERE h.matter=? AND h.statut IN ('a_venir','renvoyee') ORDER BY starts LIMIT 20", (m['id'],)).fetchall() \
            if desk.db.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='audiences5688'").fetchone() else []   # H1 : lecture seule
        choix = P.versions_partageables(desk, a, m['id']) if a.role in P.VALIDEURS else []
        confreres = []
        if desk.db.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='contacts5636'").fetchone():
            confreres = desk.db.execute("SELECT id, name, first_name, email FROM contacts5636 WHERE category='confrere' AND archived=0 AND email<>'' ORDER BY name LIMIT 200").fetchall()
    except Stop as ex:
        return '<p class="d79-warn" role="alert">%s</p>' % e(MESSAGES.get(str(ex).split(':')[0], 'Remplacements indisponibles.'))
    demande = ''
    if a.role in P.VALIDEURS and audiences:
        demande = _form('creer', {}, 'Demander un remplacement',
                        '<label>Audience <select name="audience">%s</select></label><label>Rôle <select name="role">%s</select></label>'
                        '<label>Confrère <select name="confrere">%s</select></label>' % (
                            ''.join('<option value="%s">%s — %s</option>' % (e(h['id'], quote=True), e(au.when_label(h['starts'], h['all_day'], P.tz(desk))), e(h['title'] or ''))
                                    for h in audiences),
                            ''.join('<option value="%s">%s</option>' % (k, e(v)) for k, v in ROLES_MISSION.items()),
                            ''.join('<option value="%s">%s</option>' % (e(c['id'], quote=True), e(('%s %s — %s' % (c['first_name'], c['name'], c['email'])).strip()))
                                    for c in confreres)), cls='ax-btn')
    return ('<section class="rl109-section" aria-labelledby="rl109-rp-t"><h2 id="rl109-rp-t">Remplacements et postulation</h2>%s<ul class="rl109-liste">%s</ul></section>') % (
        demande, ''.join(_carte(x, choix) for x in liste) or '<li class="ax-muted">Aucun remplacement demandé.</li>')


# ------------------------------------------------------------------------------------------------ routes ``m56109/remplacements/``
def _ecriture(parts):
    return parts == ['creer'] or (len(parts) == 2 and parts[1] in ('instruire', 'transition'))


def allowed(role, method, api_):
    """Lectures : trois rôles (droits du dossier relus) ; écritures : avocat et administrateur."""
    parts = P.route(api_, PREFIXE)
    if role not in P.ROLES or parts is None:
        return False
    if method in ('GET', 'HEAD'):
        return (len(parts) == 2 and parts[0] in ('audience', 'dossier')) or (len(parts) == 1 and parts[0] != 'creer')
    return method == 'POST' and _ecriture(parts) and role in P.VALIDEURS


def api(env, desk, auth, prefix, name, args, method):
    try:
        return _api(env, desk, auth, prefix, name, args, method)
    except Stop as ex:
        return P.erreur(ex, messages=MESSAGES)
    except sqlite3.OperationalError:
        return P.sortie({'error': 'registre_occupe', 'message': P.MESSAGES['registre_occupe']}, '503 Service Unavailable')


def _api(env, desk, auth, prefix, name, args, method):
    from .web567 import actor
    a = actor(env)
    parts = P.route(name, PREFIXE)
    if not parts:
        raise Stop('route_inconnue')
    if not allowed(a[1], method, name):
        raise Stop('role_insuffisant' if a[1] not in P.ROLES or (method == 'POST' and _ecriture(parts)) else 'route_inconnue')
    if method in ('GET', 'HEAD'):
        if parts[0] == 'audience':
            return P.sortie({'remplacements': remplacements_audience(desk, a, parts[1])})
        if parts[0] == 'dossier':
            return P.sortie({'remplacements': remplacements_dossier(desk, a, parts[1])})
        return P.sortie(vue(desk, a, parts[0]))
    from .web440 import check_post
    data = check_post(env, auth)
    if parts == ['creer']:
        out = creer(desk, a, data, data.get('cle'))
        return P.sortie({**out, 'message': 'Remplacement en brouillon : préparez l’instruction au confrère. Rien n’est envoyé.'})
    if parts[1] == 'instruire':
        return P.sortie(instruire(desk, a, parts[0], data))
    return P.sortie(transition(desk, a, parts[0], data))
