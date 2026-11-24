"""5.6.109 (lot G2, G06) : appels, comptes rendus et rappels dans les fiches existantes (contact et dossier).

Raccordé aux services existants, sans page « Secrétariat » ni seconde file de rappels :
- **points d'entrée** : bloc « Appels » de la fiche contact (``crm5676.bloc_contact``, monté dans ``crm5636``) et de l'onglet Correspondances
  du dossier ; aucun menu nouveau ;
- **source « appel »** : un appel est un objet propre (``appels56109``), jamais un événement d'agenda ni un faux ``meeting500_reports.event_id`` ;
  il peut exister SANS dossier (prospect, demande orale) et être rattaché ensuite à un dossier ou à une demande du CRM (``opp5676``), avec
  l'historique des rattachements ; un interlocuteur incertain (homonymes, nom seul) reste « à confirmer » : jamais de fusion de contacts ;
- **durée** : facultative ; inconnue, elle reste inconnue (``NULL``, « À compléter » pour la préfacturation), jamais déduite du texte dicté ;
- **compte rendu** : ``meeting500.structure_notes`` / ``draft_appel`` (projet sans ajout ni réécriture), conservé comme version du registre
  commun des livrables (``livrables5689.observe`` ; un rattachement crée une copie à relire, jamais une réécriture) ; le texte original et chaque correction de l'avocat restent consultables ;
- **une action par décision** : « Créer le rappel », « Ajouter la tâche », « Proposer un rendez-vous » écrivent dans les tâches et l'agenda
  existants (``agenda5697.creer``) ; l'identité de la décision (appel + nature + rang) est réservée AVANT l'effet ; double clic, dictée
  relancée ou reprise sur un autre poste rejoignent le même résultat (clé d'agenda déterministe) ;
- **IA en pause** : tout est manuel et déterministe (aucun appel de modèle) ; l'enregistrement d'un tiers n'est jamais activé ici.
"""
from datetime import datetime, timezone
from html import escape as e
import json
import re
import sqlite3
import uuid

from .common import Stop
from . import partage_client56109 as P

PREFIXE = 'm56109/appels/'
ROLES = P.ROLES
SENS = {'entrant': 'Appel reçu', 'sortant': 'Appel passé'}
NATURES = {'rappel': 'Créer le rappel', 'tache': 'Ajouter la tâche', 'rendez_vous': 'Proposer un rendez-vous'}
DUREE = {'inconnue': 'À compléter', 'saisie': 'Saisie', 'mesuree': 'Mesurée'}
MAX_TEXTE = 20000

SCHEMA = '''
CREATE TABLE IF NOT EXISTS appels56109(id TEXT PRIMARY KEY, cabinet TEXT NOT NULL, sens TEXT NOT NULL CHECK(sens IN ('entrant','sortant')),
  instant_utc TEXT NOT NULL, zone TEXT NOT NULL, contact_id TEXT NOT NULL DEFAULT '', interlocuteur TEXT NOT NULL DEFAULT '',
  contact_a_confirmer INTEGER NOT NULL DEFAULT 0, candidats TEXT NOT NULL DEFAULT '[]', demande_crm_id TEXT NOT NULL DEFAULT '',
  matter_id TEXT NOT NULL DEFAULT '', duree_minutes INTEGER CHECK(duree_minutes IS NULL OR (duree_minutes >= 0 AND duree_minutes <= 1440)),
  duree_origine TEXT NOT NULL DEFAULT 'inconnue' CHECK(duree_origine IN ('inconnue','saisie','mesuree')), texte_original TEXT NOT NULL DEFAULT '',
  saisie TEXT NOT NULL DEFAULT 'texte' CHECK(saisie IN ('texte','dictee')), prochaine_action TEXT NOT NULL DEFAULT '',
  document_id TEXT NOT NULL DEFAULT '', historique TEXT NOT NULL DEFAULT '[]', auteur TEXT NOT NULL, version INTEGER NOT NULL DEFAULT 1,
  cree_le TEXT NOT NULL, maj_le TEXT NOT NULL);
CREATE INDEX IF NOT EXISTS appels56109_contact ON appels56109(contact_id, instant_utc);
CREATE INDEX IF NOT EXISTS appels56109_dossier ON appels56109(matter_id, instant_utc);
CREATE TABLE IF NOT EXISTS appels_comptes_rendus56109(appel_id TEXT NOT NULL, revision INTEGER NOT NULL CHECK(revision >= 1), item_id TEXT NOT NULL,
  document_id TEXT NOT NULL, version_id TEXT NOT NULL, texte TEXT NOT NULL, sha256 TEXT NOT NULL, origine TEXT NOT NULL, auteur TEXT NOT NULL,
  cree_le TEXT NOT NULL, PRIMARY KEY(appel_id, revision));
CREATE TABLE IF NOT EXISTS appels_actions56109(appel_id TEXT NOT NULL, decision TEXT NOT NULL, nature TEXT NOT NULL CHECK(nature IN ('rappel','tache','rendez_vous')),
  valeurs TEXT NOT NULL, etat TEXT NOT NULL CHECK(etat IN ('reservee','faite','echec')), tache_id TEXT NOT NULL DEFAULT '',
  evenement_id TEXT NOT NULL DEFAULT '', cle_agenda TEXT NOT NULL, acteur TEXT NOT NULL, role TEXT NOT NULL, erreur TEXT NOT NULL DEFAULT '',
  cree_le TEXT NOT NULL, maj_le TEXT NOT NULL, PRIMARY KEY(appel_id, decision));
CREATE TABLE IF NOT EXISTS appels_ops56109(acteur TEXT NOT NULL, cle TEXT NOT NULL, nature TEXT NOT NULL, empreinte TEXT NOT NULL,
  etat TEXT NOT NULL, resultat TEXT NOT NULL DEFAULT '{}', cree_le TEXT NOT NULL, PRIMARY KEY(acteur, cle));
'''
TABLES = ('appels56109', 'appels_comptes_rendus56109', 'appels_actions56109', 'appels_ops56109')
MESSAGES = {
    'introuvable': 'Appel introuvable, ou action non permise.',
    'sens_invalide': 'Indiquez si l’appel a été reçu ou passé.',
    'horaire_invalide': 'Date ou heure de l’appel invalide.',
    'duree_invalide': 'Durée invalide (en minutes, 24 heures au plus) ; laissez vide si elle est inconnue.',
    'contact_inconnu': 'Contact inconnu.',
    'demande_inconnue': 'Demande du CRM inconnue.',
    'contact_non_candidat': 'Choisissez l’un des contacts proposés, ou un contact existant.',
    'texte_requis': 'Saisissez ou dictez le compte rendu de l’appel.',
    'nature_invalide': 'Action inconnue.',
    'titre_requis': 'Indiquez l’intitulé de l’action.',
    'decision_deja_prise': 'Cette action a déjà été créée pour cet appel : rien n’a été dupliqué.',
    'rattachement_vide': 'Choisissez un dossier ou une demande.',
}


# ------------------------------------------------------------------------------------------------ outils
def _maintenant():
    return P.maintenant()


def _instant(desk, valeur):
    """Heure locale du cabinet (« AAAA-MM-JJTHH:MM ») ou instant avec décalage → UTC ; vide : maintenant."""
    if not valeur:
        return datetime.now(timezone.utc).isoformat(timespec='seconds')
    try:
        d = datetime.fromisoformat(str(valeur).strip())
    except ValueError:
        raise Stop('horaire_invalide') from None
    if d.tzinfo is None:
        from .agenda5697 import instant
        d = instant(d, P.tz(desk))
    return d.astimezone(timezone.utc).isoformat(timespec='seconds')


def _duree(valeur):
    if valeur in (None, ''):
        return None, 'inconnue'
    if isinstance(valeur, bool):
        raise Stop('duree_invalide')
    try:
        n = int(str(valeur).strip())
    except ValueError:
        raise Stop('duree_invalide') from None
    if not 0 < n <= 1440:
        raise Stop('duree_invalide')
    return n, 'saisie'


def _table(desk, nom):
    return bool(desk.db.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name=?", (nom,)).fetchone())


def _contact(desk, cid):
    if not cid:
        return None
    if not _table(desk, 'contacts5636'):
        raise Stop('contact_inconnu')
    row = desk.db.execute('SELECT * FROM contacts5636 WHERE id=?', (str(cid),)).fetchone()
    if not row:
        raise Stop('contact_inconnu')
    return dict(row)


def _nom_contact(c):
    from .crm5636 import label
    return label(c) or str(c.get('email') or c.get('id'))


def candidats(desk, nom, limite=5):
    """Contacts dont le nom correspond : une proposition à confirmer, jamais un rattachement ni une fusion automatiques."""
    nom = re.sub(r'\s+', ' ', str(nom or '')).strip()
    if len(nom) < 3 or not _table(desk, 'contacts5636'):
        return []
    try:
        from .crm5636 import search
        rows = search(desk, nom, limit=limite)
    except Exception:   # noqa: BLE001 — répertoire illisible : aucune proposition (l'appel reste « à confirmer »)
        return []
    return [{'id': r['id'], 'label': r.get('label') or r['id']} for r in rows][:limite]


def _demande(desk, oid):
    if not oid:
        return ''
    if not _table(desk, 'opp5676') or not desk.db.execute('SELECT 1 FROM opp5676 WHERE id=?', (str(oid),)).fetchone():
        raise Stop('demande_inconnue')
    return str(oid)


def _dossier_facultatif(desk, a, mid):
    mid = str(mid or '').strip()
    if not mid:
        return ''
    return P.dossier(desk, a, mid)['id']


def _appel(desk, a, aid):
    """Appel lisible : un appel rattaché à un dossier réservé répond comme un appel absent (même 404 neutre)."""
    if not P.IDENT.fullmatch(str(aid or '')):
        raise Stop('introuvable')
    row = desk.db.execute('SELECT * FROM appels56109 WHERE id=?', (str(aid),)).fetchone()
    if not row:
        raise Stop('introuvable')
    if row['matter_id']:
        P.dossier(desk, a, row['matter_id'])
    return dict(row)


def _version(row, attendue):
    P._version(row, attendue)


# ------------------------------------------------------------------------------------------------ consigner, confirmer, rattacher
def consigner(desk, acteur_, data, cle):
    """« Consigner un appel » : date/heure, sens, interlocuteur, dossier et demande facultatifs, texte ou dictée, durée facultative, prochaine
    action. Aucun événement d'agenda n'est créé ; aucune donnée n'est déduite par un modèle. Trois rôles du cabinet."""
    a = P.acteur(acteur_)
    if a.role not in ROLES:
        raise Stop('role_insuffisant')
    sens = str(data.get('sens') or '')
    if sens not in SENS:
        raise Stop('sens_invalide')
    instant = _instant(desk, data.get('quand'))
    contact = _contact(desk, str(data.get('contact') or '').strip())
    interlocuteur = P.texte(data.get('interlocuteur'), 200)
    proposes = []
    if contact:
        interlocuteur = interlocuteur or _nom_contact(contact)
    elif interlocuteur:
        proposes = candidats(desk, interlocuteur)
    matter = _dossier_facultatif(desk, a, data.get('dossier'))
    demande = _demande(desk, str(data.get('demande') or '').strip())
    duree, origine = _duree(data.get('duree'))
    texte = P.texte(data.get('texte'), MAX_TEXTE)
    saisie = 'dictee' if str(data.get('saisie') or '') == 'dictee' else 'texte'
    action = P.texte(data.get('prochaine_action'), 300)
    parametres = {'sens': sens, 'instant': instant, 'contact': contact['id'] if contact else '', 'interlocuteur': interlocuteur, 'matter': matter,
                  'demande': demande, 'duree': duree, 'texte': texte, 'saisie': saisie, 'action': action}

    def faire():
        aid = 'ap-' + uuid.uuid4().hex[:24]
        quand = _maintenant()
        desk.db.execute('INSERT INTO appels56109(id,cabinet,sens,instant_utc,zone,contact_id,interlocuteur,contact_a_confirmer,candidats,demande_crm_id,matter_id,'
                        'duree_minutes,duree_origine,texte_original,saisie,prochaine_action,historique,auteur,cree_le,maj_le) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)',
                        (aid, P.cabinet(desk), sens, instant, P.tz(desk).key, contact['id'] if contact else '', interlocuteur, 0 if contact or not interlocuteur else 1,
                         json.dumps(proposes, ensure_ascii=False), demande, matter, duree, origine, texte, saisie, action,
                         json.dumps([{'quand': quand, 'par': a.compte, 'quoi': 'consigne', 'dossier': matter, 'demande': demande}], ensure_ascii=False),
                         a.compte, quand, quand))
        desk.db.commit()
        desk.audit('appel56109_consigne', {'appel': aid, 'matter': matter, 'contact': bool(contact), 'demande': bool(demande), 'duree_connue': duree is not None,
                                           'par': a.compte})
        return {'id': aid}

    out = P.executer(desk, 'appels_ops56109', a.compte, cle, 'consigner', parametres, faire)
    return dict(vue(desk, a, out['id']), rejoue=out['rejoue'])


def confirmer_contact(desk, acteur_, aid, data):
    """Interlocuteur confirmé par une personne : un contact existant (proposé ou choisi) ; aucune fusion de fiches."""
    a = P.acteur(acteur_)
    row = _appel(desk, a, aid)
    _version(row, data.get('version'))
    contact = _contact(desk, str(data.get('contact') or '').strip())
    if not contact:
        raise Stop('contact_inconnu')
    quand = _maintenant()
    hist = json.loads(row['historique'] or '[]') + [{'quand': quand, 'par': a.compte, 'quoi': 'contact_confirme', 'contact': contact['id']}]
    n = desk.db.execute('UPDATE appels56109 SET contact_id=?, interlocuteur=?, contact_a_confirmer=0, historique=?, version=version+1, maj_le=? WHERE id=? AND version=?',
                        (contact['id'], row['interlocuteur'] or _nom_contact(contact), json.dumps(hist, ensure_ascii=False), quand, row['id'], row['version'])).rowcount
    if n != 1:
        raise Stop('version_perimee')
    desk.db.commit()
    return vue(desk, a, row['id'])


def rattacher(desk, acteur_, aid, data):
    """Rattachement ultérieur à un dossier et/ou à une demande du CRM : droits du dossier relus, provenance conservée (historique)."""
    a = P.acteur(acteur_)
    row = _appel(desk, a, aid)
    _version(row, data.get('version'))
    matter = _dossier_facultatif(desk, a, data.get('dossier')) if data.get('dossier') else row['matter_id']
    demande = _demande(desk, str(data.get('demande') or '').strip()) if data.get('demande') else row['demande_crm_id']
    if (matter, demande) == (row['matter_id'], row['demande_crm_id']):
        raise Stop('rattachement_vide')
    quand = _maintenant()
    hist = json.loads(row['historique'] or '[]') + [{'quand': quand, 'par': a.compte, 'quoi': 'rattache', 'dossier': matter, 'demande': demande,
                                                      'avant': {'dossier': row['matter_id'], 'demande': row['demande_crm_id']}}]
    copie = None
    from . import livrables5689
    livrables5689.ensure_schema(desk)                       # avant la transaction (préparée au démarrage : sans effet)
    desk.db.execute('BEGIN IMMEDIATE')
    try:
        n = desk.db.execute('UPDATE appels56109 SET matter_id=?, demande_crm_id=?, historique=?, version=version+1, maj_le=? WHERE id=? AND version=?',
                            (matter, demande, json.dumps(hist, ensure_ascii=False), quand, row['id'], row['version'])).rowcount
        if n != 1:
            raise Stop('version_perimee')
        if row['document_id'] and matter != row['matter_id']:
            copie = _copie_rattachee(desk, row, matter, a)
        desk.db.commit()
    except BaseException:
        desk.db.rollback()
        raise
    desk.audit('appel56109_rattache', {'appel': row['id'], 'matter': matter, 'demande': bool(demande), 'par': a.compte,
                                       'document': copie['document_id'] if copie else row['document_id'], 'ancien_dossier': row['matter_id'],
                                       'nouveau_dossier': matter, 'document_origine': row['document_id'] if copie else ''})
    return vue(desk, a, row['id'])


def _inscrire(desk, row, texte, revision, owner, matter, document_id='', objet='', sources=(), extra=None):
    """Version du compte rendu dans le registre commun (``livrables5689.observe``, fonction publique), SANS commit : jamais une réécriture
    d'une version existante. ``document_id`` : document courant de l'appel (copie rattachée) ; vide : document d'origine."""
    import hashlib
    from . import livrables5689 as liv
    texte = str(texte or '').strip()[:30000]
    if not texte:
        raise Stop('notes_vides')
    liv.ensure_schema(desk)
    sha = hashlib.sha256(texte.encode('utf-8')).hexdigest()
    x = {'service': 'appels56109', 'object': objet or row['id'], 'version': 'rev:%d' % int(revision), 'matter': matter, 'kind': 'note',
         'label': 'Compte rendu d’appel', 'title': ('Compte rendu d’appel — %s' % (row.get('interlocuteur') or 'interlocuteur à préciser'))[:300],
         'state': 'verified', 'sha256': sha, 'owner': str(owner)[:160], 'sources': list(sources),
         'provenance': {'producteur': 'appels56109', 'source': 'appel', 'appel': row['id'], 'revision': int(revision), 'sha256': sha, **(extra or {})}}
    if document_id:
        x['document_id'] = document_id
    item = liv.observe(desk, x)
    r = desk.db.execute('SELECT id, document_id, version_id FROM prepared_items5689 WHERE id=?', (item,)).fetchone()
    return {'item_id': r['id'], 'document_id': r['document_id'], 'version_id': r['version_id'], 'sha256': sha}


def _copie_rattachee(desk, row, matter, a):
    """B1 : rattacher ne réécrit pas l'historique. Le dernier compte rendu est COPIÉ dans un document nouveau du nouveau dossier, à relire
    (aucune validation reprise) ; les versions antérieures gardent leur dossier, leur validation et leurs partages. Provenance inscrite au
    registre (lien « source » vers la version d'origine). Dans la transaction de l'appelant."""
    dernier = desk.db.execute('SELECT * FROM appels_comptes_rendus56109 WHERE appel_id=? ORDER BY revision DESC LIMIT 1', (row['id'],)).fetchone()
    if not dernier:
        return None
    rev = dernier['revision'] + 1
    reg = _inscrire(desk, row, dernier['texte'], rev, a.compte, matter, objet='%s@%s' % (row['id'], matter), sources=[dernier['item_id']],
                    extra={'copie_de': {'document_id': dernier['document_id'], 'version_id': dernier['version_id']}, 'ancien_dossier': row['matter_id']})
    desk.db.execute('INSERT INTO appels_comptes_rendus56109(appel_id,revision,item_id,document_id,version_id,texte,sha256,origine,auteur,cree_le) VALUES(?,?,?,?,?,?,?,?,?,?)',
                    (row['id'], rev, reg['item_id'], reg['document_id'], reg['version_id'], dernier['texte'], reg['sha256'], 'rattachement', a.compte, _maintenant()))
    desk.db.execute('UPDATE appels56109 SET document_id=? WHERE id=?', (reg['document_id'], row['id']))
    return reg


# ------------------------------------------------------------------------------------------------ compte rendu
def compte_rendu(desk, acteur_, aid, data):
    """Sans texte : projet structuré (``meeting500.draft_appel``) depuis le texte original, inscrit comme révision 1 du compte rendu canonique.
    Avec texte : correction de l'avocat = révision suivante (même document du registre, nouvelle version) ; l'original reste."""
    from . import meeting500
    a = P.acteur(acteur_)
    row = _appel(desk, a, aid)
    _version(row, data.get('version'))
    corrige = P.texte(data.get('texte'), 30000)
    if not corrige and not row['texte_original'].strip():
        raise Stop('texte_requis')
    projet = None if corrige else meeting500.draft_appel(desk, row, row['texte_original'])
    texte = corrige or projet['text']
    from . import livrables5689
    livrables5689.ensure_schema(desk)
    desk.db.execute('BEGIN IMMEDIATE')
    try:
        courant = desk.db.execute('SELECT version FROM appels56109 WHERE id=?', (row['id'],)).fetchone()['version']
        if courant != row['version']:
            raise Stop('version_perimee')
        rev = desk.db.execute('SELECT COALESCE(MAX(revision),0)+1 FROM appels_comptes_rendus56109 WHERE appel_id=?', (row['id'],)).fetchone()[0]
        if not corrige and rev > 1:
            raise Stop('version_perimee')                   # le projet n'est préparé qu'une fois : les suivantes sont des corrections
        reg = _inscrire(desk, row, texte, rev, a.compte, row['matter_id'], document_id=row['document_id'])
        desk.db.execute('INSERT INTO appels_comptes_rendus56109(appel_id,revision,item_id,document_id,version_id,texte,sha256,origine,auteur,cree_le) VALUES(?,?,?,?,?,?,?,?,?,?)',
                        (row['id'], rev, reg['item_id'], reg['document_id'], reg['version_id'], texte, reg['sha256'], 'correction' if corrige else 'projet',
                         a.compte, _maintenant()))
        desk.db.execute('UPDATE appels56109 SET document_id=?, version=version+1, maj_le=? WHERE id=?', (reg['document_id'], _maintenant(), row['id']))
        desk.db.commit()
    except BaseException:
        desk.db.rollback()
        raise
    desk.audit('appel56109_compte_rendu', {'appel': row['id'], 'revision': rev, 'document': reg['document_id'], 'par': a.compte})
    out = vue(desk, a, row['id'])
    out.update(revision=rev, texte=texte, warnings=(projet or {}).get('warnings', []), sections=(projet or {}).get('sections'))
    return out


# ------------------------------------------------------------------------------------------------ une action par décision
def _valeurs(desk, row, nature, data):
    titre = P.texte(data.get('titre'), 200) or (row['prochaine_action'] if nature != 'rendez_vous' else '')
    if nature == 'rappel' and not titre:
        titre = 'Rappeler %s' % (row['interlocuteur'] or 'l’interlocuteur')
    if len(titre) < 3:
        raise Stop('titre_requis')
    v = {'titre': titre, 'dossier': row['matter_id'], 'responsable': P.texte(data.get('responsable'), 160)}
    if nature in ('rappel', 'tache'):
        echeance = str(data.get('echeance') or '')[:10]
        if echeance:
            try:
                datetime.strptime(echeance, '%Y-%m-%d')
            except ValueError:
                raise Stop('date_tache_invalide') from None
        v['echeance'] = echeance
    else:
        v.update(debut=str(data.get('debut') or ''), duree_minutes=str(data.get('duree_minutes') or '30'))
    return v


def decider(desk, acteur_, aid, data):
    """Créer le rappel, ajouter la tâche ou proposer un rendez-vous à partir de l'appel. La décision (appel + nature + rang) est réservée
    AVANT l'effet ; une seconde demande pour la même décision — double clic, dictée relancée, autre poste — rend le même résultat sans
    rien créer. L'effet passe par ``agenda5697.creer`` (tâches et agenda existants) avec une clé déterministe et l'acteur de la réservation."""
    from . import agenda5697
    a = P.acteur(acteur_)
    if a.role not in ROLES:
        raise Stop('role_insuffisant')
    row = _appel(desk, a, aid)
    nature = str(data.get('nature') or '')
    if nature not in NATURES:
        raise Stop('nature_invalide')
    try:
        rang = int(str(data.get('rang') or 0))
    except ValueError:
        raise Stop('nature_invalide') from None
    if not 0 <= rang <= 100:
        raise Stop('nature_invalide')
    decision = '%s:%d' % (nature, rang)
    existante = desk.db.execute('SELECT * FROM appels_actions56109 WHERE appel_id=? AND decision=?', (row['id'], decision)).fetchone()
    if existante is None:
        valeurs = _valeurs(desk, row, nature, data)
        cle_agenda = 'appel109-' + P.sha(row['id'], decision)[:40]
        quand = _maintenant()
        try:
            desk.db.execute("INSERT INTO appels_actions56109(appel_id,decision,nature,valeurs,etat,cle_agenda,acteur,role,cree_le,maj_le) VALUES(?,?,?,?,'reservee',?,?,?,?,?)",
                            (row['id'], decision, nature, json.dumps(valeurs, ensure_ascii=False), cle_agenda, a.compte, a.role, quand, quand))
            desk.db.commit()
        except sqlite3.IntegrityError:                      # même décision au même instant (autre onglet, autre poste) : la première réservation fait foi
            desk.db.rollback()
        existante = desk.db.execute('SELECT * FROM appels_actions56109 WHERE appel_id=? AND decision=?', (row['id'], decision)).fetchone()
    if existante['etat'] == 'faite':
        return dict(vue(desk, a, row['id']), rejoue=True, tache_id=existante['tache_id'], evenement_id=existante['evenement_id'],
                    message=MESSAGES['decision_deja_prise'])
    v = json.loads(existante['valeurs'])
    saisie = {'cle': existante['cle_agenda'], 'titre': v['titre'], 'dossier': v['dossier']}
    if existante['nature'] == 'rendez_vous':
        saisie.update(objet='evenement', nature='rendez_vous', debut=v['debut'], duree_minutes=v['duree_minutes'],
                      notes='Proposé depuis l’appel du %s.' % row['instant_utc'][:16])
    else:
        saisie.update(objet='tache', tache={'titre': v['titre'], 'echeance': v.get('echeance', ''), 'responsable': v['responsable'],
                                            'priorite': 'haute' if existante['nature'] == 'rappel' else 'normale'})
    try:
        out = agenda5697.creer(desk, (existante['acteur'], existante['role']), saisie, executer=False)
    except Stop as ex:
        desk.db.execute("UPDATE appels_actions56109 SET erreur=?, maj_le=? WHERE appel_id=? AND decision=? AND etat='reservee'",
                        (str(ex).split(':')[0][:120], _maintenant(), row['id'], decision))
        desk.db.commit()
        if str(ex).split(':')[0] in ('titre_evenement_invalide', 'tache_texte_invalide', 'horaire_invalide', 'date_tache_invalide', 'duree_evenement_invalide',
                                     'fin_avant_debut', 'horaire_inexistant', 'horaire_ambigu', 'responsable_inconnu'):
            desk.db.execute("DELETE FROM appels_actions56109 WHERE appel_id=? AND decision=? AND etat='reservee'", (row['id'], decision))
            desk.db.commit()                                # saisie invalide : rien n'a été créé, la décision reste libre
        raise
    desk.db.execute("UPDATE appels_actions56109 SET etat='faite', tache_id=?, evenement_id=?, erreur='', maj_le=? WHERE appel_id=? AND decision=?",
                    (out.get('tache_id') or '', out.get('evenement_id') or '', _maintenant(), row['id'], decision))
    desk.db.commit()
    desk.audit('appel56109_action', {'appel': row['id'], 'decision': decision, 'tache': out.get('tache_id') or '', 'evenement': out.get('evenement_id') or '',
                                     'par': a.compte})
    return dict(vue(desk, a, row['id']), rejoue=bool(out.get('rejeu')), tache_id=out.get('tache_id') or '', evenement_id=out.get('evenement_id') or '',
                message='%s : enregistré dans %s.' % (NATURES[existante['nature']], 'l’agenda' if existante['nature'] == 'rendez_vous' else 'les tâches'))


def duree_prefacturation(desk, aid):
    """Pour la préfacturation (lot G1) : durée renseignée, ou « À compléter » ; jamais une estimation tirée de la longueur du texte."""
    row = desk.db.execute('SELECT duree_minutes, duree_origine FROM appels56109 WHERE id=?', (str(aid),)).fetchone()
    if not row:
        raise Stop('introuvable')
    return {'minutes': row['duree_minutes'], 'origine': row['duree_origine'], 'libelle': DUREE[row['duree_origine']] if row['duree_minutes'] is None else
            '%d min (%s)' % (row['duree_minutes'], DUREE[row['duree_origine']].lower())}


# ------------------------------------------------------------------------------------------------ lectures
def vue(desk, acteur_, aid):
    a = P.acteur(acteur_)
    row = _appel(desk, a, aid)
    out = {k: row[k] for k in row.keys() if k not in ('cabinet',)}
    out['candidats'] = json.loads(row['candidats'] or '[]')
    out['historique'] = json.loads(row['historique'] or '[]')
    out['sens_libelle'] = SENS[row['sens']]
    out['duree'] = duree_prefacturation(desk, row['id'])
    out['comptes_rendus'] = [dict(r) for r in desk.db.execute('SELECT revision, document_id, version_id, sha256, origine, auteur, cree_le, texte '
                                                               'FROM appels_comptes_rendus56109 WHERE appel_id=? ORDER BY revision', (row['id'],))]
    out['actions'] = [dict(r) for r in desk.db.execute('SELECT decision, nature, etat, tache_id, evenement_id, valeurs, erreur, cree_le FROM appels_actions56109 '
                                                        'WHERE appel_id=? ORDER BY cree_le', (row['id'],))]
    return out


def _liste(desk, a, sql, params, limite):
    out = []
    for r in desk.db.execute(sql + ' ORDER BY instant_utc DESC LIMIT ?', params + (limite * 3,)):
        try:
            out.append(vue(desk, a, r['id']))
        except Stop:
            continue                                         # appel d'un dossier réservé : absent de la liste, sans compteur révélateur
        if len(out) >= limite:
            break
    return out


def appels_contact(desk, acteur_, cid, limite=20):
    a = P.acteur(acteur_)
    return _liste(desk, a, 'SELECT id FROM appels56109 WHERE contact_id=?', (str(cid),), limite)


def appels_dossier(desk, acteur_, mid, limite=20):
    a = P.acteur(acteur_)
    P.dossier(desk, a, mid)
    return _liste(desk, a, 'SELECT id FROM appels56109 WHERE matter_id=?', (str(mid),), limite)


# ------------------------------------------------------------------------------------------------ blocs (fiche contact, dossier)
def _form(route, champs, bouton, extra='', cls='ax-btn ghost'):
    caches = ''.join('<input type="hidden" name="%s" value="%s">' % (e(k, quote=True), e(str(v), quote=True)) for k, v in champs.items())
    return ('<form class="m5-form rl109-form" data-api="%s" data-reload>%s<input type="hidden" name="cle" value="%s">%s<button class="%s" type="submit">%s</button></form>') % (
        e(PREFIXE + route, quote=True), caches, uuid.uuid4().hex, extra, cls, e(bouton))


def _appel_html(x):
    cr = x['comptes_rendus'][-1] if x['comptes_rendus'] else None
    actions = ''.join('<li>%s — %s%s</li>' % (e(NATURES.get(t['nature'], t['nature'])), 'créé' if t['etat'] == 'faite' else 'en attente',
                                              (' (%s)' % e(t['erreur'])) if t['erreur'] else '') for t in x['actions'])
    confirmer = ''
    if x['contact_a_confirmer']:
        opts = ''.join('<option value="%s">%s</option>' % (e(c['id'], quote=True), e(c['label'])) for c in x['candidats'])
        confirmer = ('<p class="rl109-a-confirmer">Interlocuteur à confirmer (aucune fusion automatique).</p>' + (_form(
            '%s/contact' % x['id'], {'version': x['version']}, 'Confirmer le contact', '<label>Contact <select name="contact">%s</select></label>' % opts) if opts else ''))
    projet = '' if x['comptes_rendus'] else _form('%s/compte-rendu' % x['id'], {'version': x['version']}, 'Préparer le compte rendu')
    corriger = _form('%s/compte-rendu' % x['id'], {'version': x['version']}, 'Enregistrer la correction',
                     '<label>Compte rendu <textarea name="texte" maxlength="30000">%s</textarea></label>' % e(cr['texte'])) if cr else ''
    decider = ''.join(_form('%s/action' % x['id'], {'nature': n, 'rang': 0}, lib, extra) for n, lib, extra in (
        ('rappel', 'Créer le rappel', '<label>Échéance <input type="date" name="echeance"></label>'),
        ('tache', 'Ajouter la tâche', '<label>Intitulé <input name="titre" maxlength="200" value="%s"></label>' % e(x['prochaine_action'], quote=True)),
        ('rendez_vous', 'Proposer un rendez-vous', '<label>Intitulé <input name="titre" maxlength="200" required></label>'
                                                   '<label>Début <input type="datetime-local" name="debut" required></label>')))
    return ('<li class="rl109-carte"><p><strong>%s</strong> — %s · %s · durée : %s</p>%s<details><summary>Texte original</summary><p class="rl109-texte">%s</p></details>'
            '%s%s%s<ul>%s</ul><div class="rl109-actions">%s</div></li>') % (
        e(x['sens_libelle']), e(x['instant_utc'][:16].replace('T', ' ')), e(x['interlocuteur'] or 'interlocuteur à préciser'), e(x['duree']['libelle']),
        confirmer, e(x['texte_original'] or '—'), ('<p class="ax-muted">Compte rendu : révision %d (registre des livrables)</p>' % cr['revision']) if cr else '',
        projet, corriger, actions, decider)


def _formulaire(champs):
    return _form('consigner', champs, 'Consigner l’appel',
                 '<label>Sens <select name="sens"><option value="entrant">Appel reçu</option><option value="sortant">Appel passé</option></select></label>'
                 '<label>Date et heure <input type="datetime-local" name="quand"></label>'
                 '<label>Interlocuteur <input name="interlocuteur" maxlength="200"></label>'
                 '<label>Durée (minutes, facultative) <input type="number" name="duree" min="1" max="1440"></label>'
                 '<label>Compte rendu (texte ou dictée) <textarea name="texte" maxlength="20000"></textarea></label>'
                 '<label>Prochaine action <input name="prochaine_action" maxlength="300"></label>', 'ax-btn')


def _bloc(liste, formulaire, titre_id):
    return ('<section class="rl109-section" aria-labelledby="%s"><h2 id="%s">Appels</h2><details class="rl109-panneau"><summary class="ax-btn">Consigner un appel</summary>'
            '%s<p class="ax-muted">Aucun rendez-vous n’est créé pour un appel ; la durée inconnue reste « à compléter ».</p></details><ul class="rl109-liste">%s</ul></section>') % (
        titre_id, titre_id, formulaire, ''.join(_appel_html(x) for x in liste) or '<li class="ax-muted">Aucun appel consigné.</li>')


def bloc_dossier(desk, prefix, m, acteur_=None):
    from .membres5650 import current_author, viewer_role
    try:
        a = P.acteur(acteur_ or (current_author() or 'cabinet', viewer_role()))
        from .interface56100 import active
        if not active(desk, a.compte):
            return ''
        liste = appels_dossier(desk, a, m['id'])
    except Stop as ex:
        return '<p class="d79-warn" role="alert">%s</p>' % e(MESSAGES.get(str(ex).split(':')[0], 'Appels indisponibles.'))
    return _bloc(liste, _formulaire({'dossier': m['id']}), 'rl109-ap-d')


def bloc_contact(desk, prefix, contact, acteur_=None):
    from .membres5650 import current_author, viewer_role
    try:
        a = P.acteur(acteur_ or (current_author() or 'cabinet', viewer_role()))
        from .interface56100 import active
        if not active(desk, a.compte):
            return ''
        liste = appels_contact(desk, a, contact['id'])
    except Stop as ex:
        return '<p class="d79-warn" role="alert">%s</p>' % e(MESSAGES.get(str(ex).split(':')[0], 'Appels indisponibles.'))
    return _bloc(liste, _formulaire({'contact': contact['id']}), 'rl109-ap-c')


# ------------------------------------------------------------------------------------------------ routes ``m56109/appels/``
def _ecriture(parts):
    return (parts == ['consigner']) or (len(parts) == 2 and parts[1] in ('contact', 'rattacher', 'compte-rendu', 'action'))


def allowed(role, method, api_):
    """Trois rôles du cabinet pour lire et écrire (un appel se consigne par l'assistant comme par l'avocat) ; droits relus par le service."""
    parts = P.route(api_, PREFIXE)
    if role not in ROLES or parts is None:
        return False
    if method in ('GET', 'HEAD'):
        return (len(parts) == 2 and parts[0] in ('contact', 'dossier')) or (len(parts) == 1 and parts[0] != 'consigner')
    return method == 'POST' and _ecriture(parts)


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
        raise Stop('role_insuffisant' if a[1] not in ROLES or (method == 'POST' and _ecriture(parts)) else 'route_inconnue')
    if method in ('GET', 'HEAD'):
        if parts[0] == 'contact':
            return P.sortie({'appels': appels_contact(desk, a, parts[1])})
        if parts[0] == 'dossier':
            return P.sortie({'appels': appels_dossier(desk, a, parts[1])})
        return P.sortie(vue(desk, a, parts[0]))
    from .web440 import check_post
    data = check_post(env, auth)
    if parts == ['consigner']:
        out = consigner(desk, a, data, data.get('cle'))
        return P.sortie({**out, 'message': 'Appel consigné (déjà enregistré : demande rejouée).' if out['rejoue'] else 'Appel consigné.'})
    aid, quoi = parts
    if quoi == 'contact':
        return P.sortie({**confirmer_contact(desk, a, aid, data), 'message': 'Interlocuteur confirmé.'})
    if quoi == 'rattacher':
        return P.sortie({**rattacher(desk, a, aid, data), 'message': 'Appel rattaché.'})
    if quoi == 'compte-rendu':
        out = compte_rendu(desk, a, aid, data)
        return P.sortie({**out, 'message': 'Compte rendu enregistré (révision %d).' % out['revision']})
    return P.sortie(decider(desk, a, aid, data))
