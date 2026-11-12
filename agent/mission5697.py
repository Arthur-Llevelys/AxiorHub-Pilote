"""5.6.97 (lot documents-mission, § 16) : ouverture de mission, de la première demande au dossier actif.

Trois entrées, un seul formulaire : « Créer une demande depuis ce message » (Courriels), « Nouvelle demande » dans un rendez-vous (Agenda) et
« Nouvelle demande » (Dossiers : appel ou saisie libre). La source, la date, le correspondant et l'objet sont repris et restent corrigeables ;
l'expéditeur n'est jamais le client par défaut : son rôle est demandé.

- ``demande_mission5697`` : brouillon à identifiant stable et version (409 si périmée). États : Nouvelle → À qualifier → À décider → Acceptée /
  Refusée ; Acceptée → Convertie. Une même source (courriel, rendez-vous) ne fait qu'une demande ; un double clic rend la même demande.
- ``decision_conflits5697`` : contrôle par ``conflicts500`` (aucun modèle), attaché à une **version des parties**. Tout changement de partie
  invalide le contrôle et ramène la demande à « À décider » ; l'acceptation exige un contrôle valide de la version courante, et un motif dès
  qu'un rapprochement ou une limite subsiste. Les rapprochements dans un dossier que la personne ne peut pas voir sont comptés, jamais décrits.
- **Conversion** : réservation unique par demande (``demande_conversion5697``, clé primaire = demande, dossier unique), données du dossier
  gelées dans la réservation, puis ``dossiers5697.creer`` (une transaction : identifiant, référence, liens, ordre de rangement) avec une clé
  stable : un rejeu, un double clic ou une reprise après coupure rendent le dossier déjà créé. Une demande refusée ne crée aucun dossier.
- **Espace de préparation privé** (``preparation5697``) : projet de convention préparé sans IA à partir des données saisies, convention signée et
  justificatif déposés tels quels (empreinte gardée). « Signée » exige le fichier signé ; renommer le client ensuite ne touche pas ce fichier.
  À la conversion, les fichiers sont rangés dans le dossier par le registre commun (``documents5697``) ; rétention : ``RETENTION_JOURS``.
"""
import base64
from datetime import datetime, timedelta, timezone
import hashlib
import json
import re
import uuid

from . import acces5697
from .common import Stop

ETATS = {'nouvelle': 'Nouvelle', 'a_qualifier': 'À qualifier', 'a_decider': 'À décider', 'acceptee': 'Acceptée', 'refusee': 'Refusée', 'convertie': 'Convertie'}
ORIGINES = {'courriel': 'Courriel', 'rendez_vous': 'Rendez-vous', 'appel': 'Appel', 'saisie': 'Saisie libre'}
ROLES_CORRESPONDANT = {'client': 'Le client lui-même', 'representant': 'Représentant du client', 'prescripteur': 'Prescripteur (confrère, expert-comptable…)',
                       'adverse': 'Partie adverse ou son conseil', 'autre': 'Autre', '': 'À préciser'}
ROLES_PARTIES = {'client': 'Client', 'adverse': 'Partie adverse', 'tiers': 'Autre partie'}
TYPES_MISSION = {'conseil': 'Conseil', 'contentieux': 'Contentieux', 'a_qualifier': 'À qualifier'}
CLIENT_ETATS = {'existant': 'Client existant', 'a_creer': 'Client à créer', 'a_identifier': 'À identifier'}
NATURES_PREPARATION = {'convention_projet': 'Projet de convention', 'convention_signee': 'Convention signée', 'justificatif': 'Justificatif'}
FINALS = ('refusee', 'convertie')
VALIDEURS = ('administrateur', 'avocat')
RETENTION_JOURS = 90
MAX_FICHIER = 10_000_000
MODIFIABLES = ('source_date', 'correspondant', 'correspondant_role', 'objet', 'client_id', 'client_nom', 'client_etat', 'parties', 'responsable', 'type_mission',
               'honoraires')
SCHEMA = '''
CREATE TABLE IF NOT EXISTS demande_mission5697(id TEXT PRIMARY KEY, cabinet_id TEXT NOT NULL, origine TEXT NOT NULL, source_ref TEXT NOT NULL DEFAULT '',
  source_date TEXT NOT NULL DEFAULT '', correspondant TEXT NOT NULL DEFAULT '', correspondant_role TEXT NOT NULL DEFAULT '', objet TEXT NOT NULL DEFAULT '',
  client_id TEXT NOT NULL DEFAULT '', client_nom TEXT NOT NULL DEFAULT '', client_etat TEXT NOT NULL DEFAULT 'a_identifier', parties TEXT NOT NULL DEFAULT '[]',
  parties_version INTEGER NOT NULL DEFAULT 1, responsable TEXT NOT NULL DEFAULT '', type_mission TEXT NOT NULL DEFAULT 'a_qualifier', honoraires TEXT NOT NULL DEFAULT '{}',
  etat TEXT NOT NULL DEFAULT 'nouvelle', version INTEGER NOT NULL DEFAULT 1, decision_motif TEXT NOT NULL DEFAULT '', decide_par TEXT NOT NULL DEFAULT '',
  decide_le TEXT NOT NULL DEFAULT '', dossier_id TEXT, convention_etat TEXT NOT NULL DEFAULT '', cree_par TEXT NOT NULL, cree_le TEXT NOT NULL,
  modifie_par TEXT NOT NULL DEFAULT '', modifie_le TEXT NOT NULL);
CREATE UNIQUE INDEX IF NOT EXISTS demande_mission5697_source ON demande_mission5697(cabinet_id, origine, source_ref) WHERE source_ref<>'';
CREATE UNIQUE INDEX IF NOT EXISTS demande_mission5697_dossier ON demande_mission5697(dossier_id) WHERE dossier_id IS NOT NULL;
CREATE INDEX IF NOT EXISTS demande_mission5697_etat ON demande_mission5697(etat, modifie_le);
CREATE TABLE IF NOT EXISTS decision_conflits5697(id TEXT PRIMARY KEY, demande_id TEXT NOT NULL, parties_version INTEGER NOT NULL, parties_empreinte TEXT NOT NULL,
  check_id TEXT NOT NULL, resultat TEXT NOT NULL, resume TEXT NOT NULL DEFAULT '', decision TEXT NOT NULL DEFAULT '', motif TEXT NOT NULL DEFAULT '',
  acteur TEXT NOT NULL, cree_le TEXT NOT NULL, decide_par TEXT NOT NULL DEFAULT '', decide_le TEXT NOT NULL DEFAULT '', valide INTEGER NOT NULL DEFAULT 1);
CREATE INDEX IF NOT EXISTS decision_conflits5697_demande ON decision_conflits5697(demande_id, parties_version);
CREATE TABLE IF NOT EXISTS demande_conversion5697(demande_id TEXT PRIMARY KEY, dossier_id TEXT UNIQUE, cle_dossier TEXT NOT NULL, acteur TEXT NOT NULL, role TEXT NOT NULL,
  donnees TEXT NOT NULL, etat TEXT NOT NULL DEFAULT 'reserve', cree_le TEXT NOT NULL, maj_le TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS demande_mission5697_journal(id TEXT PRIMARY KEY, demande_id TEXT NOT NULL, le TEXT NOT NULL, acteur TEXT NOT NULL, evenement TEXT NOT NULL,
  detail TEXT NOT NULL DEFAULT '');
CREATE INDEX IF NOT EXISTS demande_mission5697_journal_d ON demande_mission5697_journal(demande_id, le);
CREATE TABLE IF NOT EXISTS demande_mission5697_ops(acteur TEXT NOT NULL, cle TEXT NOT NULL, empreinte TEXT NOT NULL, demande_id TEXT NOT NULL, resultat TEXT NOT NULL DEFAULT '{}',
  le TEXT NOT NULL, PRIMARY KEY(acteur, cle));
CREATE TABLE IF NOT EXISTS preparation5697(id TEXT PRIMARY KEY, demande_id TEXT NOT NULL, nature TEXT NOT NULL, nom TEXT NOT NULL, mime TEXT NOT NULL, octets BLOB NOT NULL,
  sha256 TEXT NOT NULL, taille INTEGER NOT NULL, depose_par TEXT NOT NULL, depose_le TEXT NOT NULL, document_id TEXT NOT NULL DEFAULT '', range_le TEXT NOT NULL DEFAULT '',
  UNIQUE(demande_id, nature, sha256));
CREATE INDEX IF NOT EXISTS preparation5697_demande ON preparation5697(demande_id, nature);
'''


def schema(desk):
    desk.db.executescript(SCHEMA)
    desk.db.commit()


def _now():
    return datetime.now(timezone.utc).isoformat(timespec='microseconds')


def _texte(value, limite):
    return re.sub(r'\s+', ' ', str(value or '')).strip()[:limite]


def _empreinte(corps):
    return hashlib.sha256(json.dumps(corps, sort_keys=True, ensure_ascii=False, default=str).encode('utf-8')).hexdigest()


def _cle(value):
    value = str(value or '').strip()
    if not re.fullmatch(r'[A-Za-z0-9_.:-]{8,120}', value):
        raise Stop('cle_idempotence_requise')
    return value


def _cabinet(desk):
    try:
        from .cabinet_profile5689 import cabinet_id
        return cabinet_id(desk)
    except Exception:   # noqa: BLE001 — profil illisible : identifiant par défaut du registre commun
        return 'cabinet'


def _journal(desk, demande_id, acteur, evenement, detail=''):
    desk.db.execute('INSERT INTO demande_mission5697_journal(id,demande_id,le,acteur,evenement,detail) VALUES(?,?,?,?,?,?)',
                    (uuid.uuid4().hex, demande_id, _now(), acteur, evenement, str(detail)[:500]))


def _begin(desk):
    if desk.db.in_transaction:
        desk.db.commit()
    desk.db.execute('BEGIN IMMEDIATE')


# ------------------------------------------------------------------------------------------------ sources (courriel, rendez-vous)
def preremplir(desk, acteur, origine, ref):
    """Valeurs reprises de la source, à corriger : date, correspondant, objet. Une source invisible pour la personne : 404 neutre."""
    a = acces5697.acteur_de(acteur)
    ref = _texte(ref, 120)
    if origine == 'courriel':
        if not re.fullmatch(r'[0-9a-f]{64}', ref) or not acces5697.peut_lire_message(desk, a, ref):
            raise Stop('introuvable')
        row = desk.db.execute('SELECT subject,sender,received FROM work_items WHERE mail_key=?', (ref,)).fetchone()
        if not row:
            raise Stop('introuvable')
        return {'origine': origine, 'source_ref': ref, 'source_date': str(row['received'] or '')[:10], 'correspondant': _texte(row['sender'], 200),
                'objet': _texte(row['subject'], 300)}
    if origine == 'rendez_vous':
        row = desk.db.execute('SELECT id,title,starts,matter FROM calendar_cache WHERE id=?', (ref,)).fetchone() if ref else None
        if not row or not acces5697.peut_lire_dossier(desk, a, row['matter'] or None):
            raise Stop('introuvable')
        return {'origine': origine, 'source_ref': ref, 'source_date': str(row['starts'] or '')[:10], 'correspondant': '', 'objet': _texte(row['title'], 300)}
    if origine in ('appel', 'saisie'):
        return {'origine': origine, 'source_ref': '', 'source_date': datetime.now(timezone.utc).date().isoformat(), 'correspondant': '', 'objet': ''}
    raise Stop('origine_inconnue')


def _source_visible(desk, a, d):
    if d['origine'] == 'courriel' and d['source_ref']:
        return acces5697.peut_lire_message(desk, a, d['source_ref'])
    if d['origine'] == 'rendez_vous' and d['source_ref']:
        row = desk.db.execute('SELECT matter FROM calendar_cache WHERE id=?', (d['source_ref'],)).fetchone()
        return acces5697.peut_lire_dossier(desk, a, (row['matter'] if row else '') or None)
    return True


# ------------------------------------------------------------------------------------------------ lecture
def _parties(valeur):
    out, vues = [], set()
    for p in (valeur if isinstance(valeur, list) else [])[:12]:
        nom = _texte((p or {}).get('nom') if isinstance(p, dict) else '', 160)
        role = str((p or {}).get('role') or '') if isinstance(p, dict) else ''
        if not nom:
            continue
        if role not in ROLES_PARTIES:
            raise Stop('role_partie_invalide')
        courriel = _texte((p or {}).get('courriel'), 200).lower()
        if courriel and not re.fullmatch(r'[^\s@,;<>"]+@[^\s@,;<>"]+\.[^\s@,;<>"]+', courriel):
            raise Stop('adresse_invalide')
        cle = (nom.lower(), role)
        if cle not in vues:
            vues.add(cle)
            out.append({'nom': nom, 'role': role, 'courriel': courriel})
    return out


def _parties_controlees(d):
    """Parties envoyées au contrôle : le client (s'il est nommé) et chaque partie saisie."""
    parties = list(d['parties'])
    if d['client_nom'] and not any(p['role'] == 'client' and p['nom'].lower() == d['client_nom'].lower() for p in parties):
        parties.insert(0, {'nom': d['client_nom'], 'role': 'client', 'courriel': ''})
    return parties


def _ligne(desk, demande_id):
    row = desk.db.execute('SELECT * FROM demande_mission5697 WHERE id=?', (str(demande_id or ''),)).fetchone()
    if not row:
        raise Stop('introuvable')
    d = dict(row)
    d['parties'] = json.loads(d['parties'] or '[]')
    d['honoraires'] = json.loads(d['honoraires'] or '{}')
    return d


def _visible(desk, a, d):
    if not _source_visible(desk, a, d):
        return False
    if d.get('dossier_id') and not acces5697.peut_lire_dossier(desk, a, d['dossier_id']):
        return False
    return True


def lire(desk, acteur, demande_id):
    """Demande lisible par l'acteur (404 neutre sinon), avec son contrôle courant filtré, les éléments manquants et les doublons possibles."""
    schema(desk)
    a = acces5697.acteur_de(acteur)
    d = _ligne(desk, demande_id)
    if not _visible(desk, a, d):
        raise Stop('introuvable')
    d['etat_libelle'] = ETATS.get(d['etat'], d['etat'])
    d['origine_libelle'] = ORIGINES.get(d['origine'], d['origine'])
    d['manquants'] = manquants(d)
    d['controle'] = controle_courant(desk, a, d)
    d['doublons'] = doublons(desk, a, d)
    d['preparations'] = [dict(r) for r in desk.db.execute('SELECT id,nature,nom,mime,sha256,taille,depose_par,depose_le,document_id,range_le FROM preparation5697 '
                                                          'WHERE demande_id=? ORDER BY depose_le', (d['id'],))]
    d['journal'] = [dict(r) for r in desk.db.execute('SELECT le,acteur,evenement,detail FROM demande_mission5697_journal WHERE demande_id=? ORDER BY le DESC LIMIT 40', (d['id'],))]
    conv = desk.db.execute('SELECT dossier_id,etat FROM demande_conversion5697 WHERE demande_id=?', (d['id'],)).fetchone()
    d['conversion'] = dict(conv) if conv else None
    return d


def lister(desk, acteur, etats=None, limite=100):
    """Demandes visibles (droits appliqués avant la limite), les plus récentes d'abord."""
    schema(desk)
    a = acces5697.acteur_de(acteur)
    purger(desk)
    sql, args = 'SELECT * FROM demande_mission5697', []
    if etats:
        sql += ' WHERE etat IN (%s)' % ','.join('?' * len(etats))
        args += list(etats)
    out = []
    for row in desk.db.execute(sql + ' ORDER BY modifie_le DESC', args).fetchall():
        d = dict(row)
        if not _visible(desk, a, d):
            continue
        out.append({'id': d['id'], 'objet': d['objet'], 'client_nom': d['client_nom'], 'origine': ORIGINES.get(d['origine'], d['origine']), 'etat': d['etat'],
                    'etat_libelle': ETATS.get(d['etat'], d['etat']), 'responsable': d['responsable'], 'modifie_le': d['modifie_le'], 'dossier_id': d['dossier_id'] or ''})
        if len(out) >= limite:
            break
    return out


def manquants(d):
    out = []
    if not d['client_nom']:
        out.append('Client (existant ou à créer)')
    if not d['objet']:
        out.append('Objet de la demande')
    if not d['responsable']:
        out.append('Personne responsable')
    if d['type_mission'] == 'a_qualifier':
        out.append('Type de mission')
    if not d['correspondant_role']:
        out.append('Rôle du correspondant')
    if not any(p['role'] == 'adverse' for p in d['parties']) and d['type_mission'] == 'contentieux':
        out.append('Partie adverse')
    return out


def doublons(desk, a, d):
    """Demandes, dossiers visibles et contacts dont le nom du client est proche : à vérifier avant de créer (jamais fusionnés)."""
    from .conflicts500 import similarity
    nom = d.get('client_nom') or ''
    if not nom:
        return []
    out = []
    for row in desk.db.execute("SELECT id,client_nom,objet,etat FROM demande_mission5697 WHERE id<>? AND etat NOT IN ('refusee') AND client_nom<>''", (d['id'],)):
        if similarity(nom, row['client_nom']) and _visible(desk, a, _ligne(desk, row['id'])):
            out.append({'nature': 'demande', 'id': row['id'], 'libelle': '%s — %s (%s)' % (row['client_nom'], row['objet'], ETATS.get(row['etat'], row['etat']))})
    from .common import load_matters, matter_display
    perimetre = acces5697.dossiers_autorises(desk, a)
    for m in load_matters(desk.c, unfiltered=True):
        if m['id'] in perimetre and any(similarity(nom, x) for x in [m.get('client_name', '')] + list(m.get('aliases') or []) if x):
            out.append({'nature': 'dossier', 'id': m['id'], 'libelle': matter_display(m)})
    try:
        for row in desk.db.execute('SELECT id,name,first_name,company FROM contacts5636 WHERE archived=0'):
            libelle = row['company'] or ' '.join(x for x in (row['name'], row['first_name']) if x)
            if libelle and similarity(nom, libelle):
                out.append({'nature': 'contact', 'id': row['id'], 'libelle': libelle})
    except Exception:   # noqa: BLE001 — répertoire absent : pas de suggestion
        pass
    return out[:12]


# ------------------------------------------------------------------------------------------------ création et qualification
def _valeurs(data, actuel=None):
    v = dict(actuel or {})
    if 'source_date' in data:
        v['source_date'] = _texte(data.get('source_date'), 10)
    for champ, limite in (('correspondant', 200), ('objet', 300), ('client_id', 80), ('client_nom', 160), ('responsable', 120)):
        if champ in data:
            v[champ] = _texte(data.get(champ), limite)
    if 'correspondant_role' in data:
        if str(data.get('correspondant_role') or '') not in ROLES_CORRESPONDANT:
            raise Stop('role_correspondant_invalide')
        v['correspondant_role'] = str(data.get('correspondant_role') or '')
    if 'client_etat' in data:
        if str(data.get('client_etat') or '') not in CLIENT_ETATS:
            raise Stop('etat_client_invalide')
        v['client_etat'] = str(data['client_etat'])
    if 'type_mission' in data:
        if str(data.get('type_mission') or '') not in TYPES_MISSION:
            raise Stop('type_mission_invalide')
        v['type_mission'] = str(data['type_mission'])
    if 'parties' in data:
        v['parties'] = _parties(data.get('parties'))
    if 'honoraires' in data:
        h = data.get('honoraires') if isinstance(data.get('honoraires'), dict) else {}
        honoraires = {'mode': str(h.get('mode') or '') if str(h.get('mode') or '') in ('', 'forfait', 'temps', 'mixte') else '',
                      'perimetre': _texte(h.get('perimetre'), 1000)}
        for champ in ('forfait', 'taux_horaire', 'provision'):
            honoraires[champ + '_cents'] = _centimes(h.get(champ + '_cents') if h.get(champ + '_cents') not in (None, '') else h.get(champ))
        v['honoraires'] = honoraires
    return v


def _centimes(value):
    if value in (None, ''):
        return None
    if isinstance(value, int):
        return value if value >= 0 else None
    from .crm5676 import cents
    try:
        out = cents(value)
    except (Stop, ValueError):
        raise Stop('montant_invalide') from None
    if out is not None and out < 0:
        raise Stop('montant_invalide')
    return out


def creer(desk, acteur, data, cle):
    """Nouvelle demande depuis une source. Même source déjà reprise : la demande existante (``existante``) ; même clé : même réponse."""
    schema(desk)
    a = acces5697.acteur_de(acteur)
    cle = _cle(cle)
    origine = str(data.get('origine') or '')
    base = preremplir(desk, a, origine, data.get('source_ref'))      # contrôle d'accès à la source, valeurs serveur
    v = _valeurs(data, {'source_date': base['source_date'], 'correspondant': base['correspondant'], 'objet': base['objet'], 'correspondant_role': '',
                        'client_id': '', 'client_nom': '', 'client_etat': 'a_identifier', 'parties': [], 'responsable': '', 'type_mission': 'a_qualifier',
                        'honoraires': {}})
    corps = {'origine': origine, 'source_ref': base['source_ref'], **{k: v[k] for k in sorted(v)}}
    empreinte = _empreinte(corps)
    cabinet = _cabinet(desk)
    _begin(desk)
    try:
        fait = desk.db.execute('SELECT * FROM demande_mission5697_ops WHERE acteur=? AND cle=?', (a.compte, cle)).fetchone()
        if fait:
            desk.db.rollback()
            if fait['empreinte'] != empreinte:
                raise Stop('cle_idempotence_deja_utilisee')
            return dict(lire(desk, a, fait['demande_id']), rejoue=True)
        if base['source_ref']:
            meme = desk.db.execute('SELECT id FROM demande_mission5697 WHERE cabinet_id=? AND origine=? AND source_ref=?', (cabinet, origine, base['source_ref'])).fetchone()
            if meme:
                desk.db.execute('INSERT INTO demande_mission5697_ops(acteur,cle,empreinte,demande_id,le) VALUES(?,?,?,?,?)', (a.compte, cle, empreinte, meme['id'], _now()))
                desk.db.commit()
                return dict(lire(desk, a, meme['id']), existante=True)
        ident, now = uuid.uuid4().hex, _now()
        desk.db.execute('INSERT INTO demande_mission5697(id,cabinet_id,origine,source_ref,source_date,correspondant,correspondant_role,objet,client_id,client_nom,client_etat,'
                        'parties,parties_version,responsable,type_mission,honoraires,etat,version,cree_par,cree_le,modifie_par,modifie_le) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,1,?,?,?,?,1,?,?,?,?)',
                        (ident, cabinet, origine, base['source_ref'], v['source_date'], v['correspondant'], v['correspondant_role'], v['objet'], v['client_id'], v['client_nom'],
                         v['client_etat'], json.dumps(v['parties'], ensure_ascii=False), v['responsable'], v['type_mission'], json.dumps(v['honoraires'], ensure_ascii=False),
                         'nouvelle', a.compte, now, a.compte, now))
        _journal(desk, ident, a.compte, 'creation', ORIGINES[origine])
        desk.db.execute('INSERT INTO demande_mission5697_ops(acteur,cle,empreinte,demande_id,le) VALUES(?,?,?,?,?)', (a.compte, cle, empreinte, ident, now))
        desk.db.commit()
    except BaseException:
        if desk.db.in_transaction:
            desk.db.rollback()
        raise
    desk.audit('mission5697_demande', {'id': ident, 'origine': origine, 'par': a.compte[:80]})
    return lire(desk, a, ident)


def modifier(desk, acteur, demande_id, version, data, cle=''):
    """Qualification : version attendue (409 sinon). Une partie (ou le client) changée crée une nouvelle version des parties : le contrôle de
    conflits précédent ne vaut plus pour elle et la demande revient à « À décider » si elle y était ou avait été acceptée."""
    schema(desk)
    a = acces5697.acteur_de(acteur)
    actuel = lire(desk, a, demande_id)
    if actuel['etat'] in FINALS:
        raise Stop('demande_close')
    changes = {k: v for k, v in _valeurs({k: data[k] for k in MODIFIABLES if k in data}).items()}
    if 'responsable' in changes and changes['responsable']:
        from .audiences5688 import members
        if changes['responsable'] != 'cabinet' and changes['responsable'] not in members(desk):
            raise Stop('responsable_inconnu')
    nouveau = {k: changes.get(k, actuel[k]) for k in MODIFIABLES}
    differents = [k for k in MODIFIABLES if nouveau[k] != actuel[k]]
    parties_changees = _parties_controlees({**actuel, **nouveau}) != _parties_controlees(actuel)
    _begin(desk)
    try:
        row = desk.db.execute('SELECT version,etat,parties_version FROM demande_mission5697 WHERE id=?', (actuel['id'],)).fetchone()
        try:
            attendue = int(version)
        except (TypeError, ValueError):
            raise Stop('version_attendue_requise') from None
        if row['version'] != attendue:
            raise Stop('version_en_conflit')
        if not differents:
            desk.db.rollback()
            return lire(desk, a, actuel['id'])
        etat = row['etat']
        pv = row['parties_version']
        if parties_changees:
            pv += 1
            desk.db.execute('UPDATE decision_conflits5697 SET valide=0 WHERE demande_id=? AND valide=1', (actuel['id'],))
            etat = 'a_decider' if etat in ('a_decider', 'acceptee') else 'a_qualifier'
        elif etat == 'nouvelle':
            etat = 'a_qualifier'
        extra = {'decision_motif': '', 'decide_par': '', 'decide_le': ''} if row['etat'] == 'acceptee' and etat != 'acceptee' else {}
        sets = {k: (json.dumps(nouveau[k], ensure_ascii=False) if k in ('parties', 'honoraires') else nouveau[k]) for k in differents}
        sets.update(extra, etat=etat, parties_version=pv, modifie_par=a.compte, modifie_le=_now())
        desk.db.execute('UPDATE demande_mission5697 SET %s, version=version+1 WHERE id=? AND version=?' % ', '.join(k + '=?' for k in sets),
                        tuple(sets.values()) + (actuel['id'], attendue))
        _journal(desk, actuel['id'], a.compte, 'qualification', ', '.join(differents) + (' — parties modifiées : contrôle des conflits à refaire' if parties_changees else ''))
        desk.db.commit()
    except BaseException:
        if desk.db.in_transaction:
            desk.db.rollback()
        raise
    return lire(desk, a, actuel['id'])


# ------------------------------------------------------------------------------------------------ conflits
def _filtrer(desk, a, resultat):
    """Rapport du contrôle tel que la personne peut le voir : un rapprochement dans un dossier invisible est compté, pas décrit."""
    perimetre = acces5697.dossiers_autorises(desk, a)
    visibles, masques = [], 0
    for m in resultat.get('matches') or []:
        if m.get('matter') and m['matter'] not in perimetre:
            masques += 1
            continue
        visibles.append({k: m.get(k) for k in ('party', 'role_new', 'matter', 'matter_label', 'other_name', 'other_role', 'level', 'risk', 'sources')})
    return {'resume': resultat.get('summary', ''), 'statut': resultat.get('status', ''), 'rapprochements': visibles, 'masques': masques,
            'limites': list(resultat.get('limits') or []), 'couverture': resultat.get('coverage') or {}, 'methode': resultat.get('method', ''),
            'le': resultat.get('searched_at', '')}


def controle_courant(desk, a, d):
    """Dernier contrôle de la demande : valide seulement pour la version courante des parties ; « à examiner » s'il reste un doute."""
    row = desk.db.execute('SELECT * FROM decision_conflits5697 WHERE demande_id=? ORDER BY cree_le DESC LIMIT 1', (d['id'],)).fetchone()
    if not row:
        return None
    from . import conflicts500
    try:
        rapport = conflicts500.get(desk, row['check_id'], log=False)['result']
    except Stop:
        rapport = {}
    vue = _filtrer(desk, a, rapport)
    valide = bool(row['valide']) and row['parties_version'] == d['parties_version']
    a_examiner = row['resultat'] != 'aucun_resultat' or vue['masques'] > 0 or any('incomplète' in x for x in vue['limites'])
    return {'id': row['id'], 'parties_version': row['parties_version'], 'valide': valide, 'resultat': row['resultat'], 'a_examiner': a_examiner,
            'decision': row['decision'], 'motif': row['motif'], 'acteur': row['acteur'], 'le': row['cree_le'], 'decide_par': row['decide_par'],
            'decide_le': row['decide_le'], **vue}


def verifier_conflits(desk, acteur, demande_id, version):
    """Contrôle des conflits sur la version courante des parties (``conflicts500`` : comparaison locale, sans modèle)."""
    from . import conflicts500
    schema(desk)
    a = acces5697.acteur_de(acteur)
    d = lire(desk, a, demande_id)
    if d['etat'] in FINALS:
        raise Stop('demande_close')
    if d['version'] != int(version):
        raise Stop('version_en_conflit')
    parties = _parties_controlees(d)
    if not parties:
        raise Stop('aucune_partie')
    check = conflicts500.check(desk, [{'name': p['nom'], 'role': p['role'], 'email': p['courriel']} for p in parties], matter='', trigger='demande5697')
    _begin(desk)
    try:
        row = desk.db.execute('SELECT version,parties_version,etat FROM demande_mission5697 WHERE id=?', (d['id'],)).fetchone()
        if row['version'] != d['version'] or row['parties_version'] != d['parties_version']:
            raise Stop('version_en_conflit')         # les parties ont changé pendant le contrôle : il ne vaut pas pour elles
        desk.db.execute('UPDATE decision_conflits5697 SET valide=0 WHERE demande_id=? AND valide=1', (d['id'],))
        desk.db.execute('INSERT INTO decision_conflits5697(id,demande_id,parties_version,parties_empreinte,check_id,resultat,resume,acteur,cree_le,valide) VALUES(?,?,?,?,?,?,?,?,?,1)',
                        (uuid.uuid4().hex, d['id'], d['parties_version'], _empreinte(parties), check['id'], check['status'], check['result'].get('summary', ''), a.compte, _now()))
        etat = 'a_decider' if row['etat'] in ('nouvelle', 'a_qualifier', 'a_decider', 'acceptee') else row['etat']
        extra = ", decision_motif='', decide_par='', decide_le=''" if row['etat'] == 'acceptee' else ''
        desk.db.execute('UPDATE demande_mission5697 SET etat=?, version=version+1, modifie_par=?, modifie_le=?%s WHERE id=?' % extra, (etat, a.compte, _now(), d['id']))
        _journal(desk, d['id'], a.compte, 'controle_conflits', check['result'].get('summary', ''))
        desk.db.commit()
    except BaseException:
        if desk.db.in_transaction:
            desk.db.rollback()
        raise
    return lire(desk, a, d['id'])


def _peut_decider(desk, a):
    if a.role not in VALIDEURS:
        raise Stop('role_insuffisant')
    from .membres5650 import effective_rights
    if not effective_rights(desk.c['state_dir'], a.compte, a.role).get('valider', a.role == 'administrateur'):
        raise Stop('role_insuffisant')


def decider(desk, acteur, demande_id, version, decision, motif):
    """Décision de l'avocat (auteur, date, motif). Accepter exige un contrôle valide de la version courante des parties, et un motif dès qu'un
    rapprochement, un dossier non visible ou une limite subsiste. Refuser exige un motif ; aucun dossier n'est créé."""
    from . import conflicts500
    schema(desk)
    a = acces5697.acteur_de(acteur)
    _peut_decider(desk, a)
    d = lire(desk, a, demande_id)
    if d['etat'] in FINALS:
        raise Stop('demande_close')
    if decision not in ('accepter', 'refuser'):
        raise Stop('decision_invalide')
    motif = _texte(motif, 500)
    controle = d['controle']
    if decision == 'accepter':
        if not controle or not controle['valide']:
            raise Stop('controle_conflits_a_refaire')
        if controle['a_examiner'] and len(motif) < 10:
            raise Stop('motif_decision_conflit_requis')
    elif len(motif) < 10:
        raise Stop('motif_refus_requis')
    _begin(desk)
    try:
        row = desk.db.execute('SELECT version,parties_version FROM demande_mission5697 WHERE id=?', (d['id'],)).fetchone()
        if row['version'] != int(version) or row['parties_version'] != d['parties_version']:
            raise Stop('version_en_conflit')
        now = _now()
        if controle and controle['valide']:
            desk.db.execute('UPDATE decision_conflits5697 SET decision=?, motif=?, decide_par=?, decide_le=? WHERE id=?',
                            ('poursuivre' if decision == 'accepter' else 'decliner', motif, a.compte, now, controle['id']))
        etat = 'acceptee' if decision == 'accepter' else 'refusee'
        desk.db.execute('UPDATE demande_mission5697 SET etat=?, decision_motif=?, decide_par=?, decide_le=?, version=version+1, modifie_par=?, modifie_le=? WHERE id=?',
                        (etat, motif, a.compte, now, a.compte, now, d['id']))
        _journal(desk, d['id'], a.compte, 'decision', '%s — %s' % (ETATS[etat], motif))
        desk.db.commit()
    except BaseException:
        if desk.db.in_transaction:
            desk.db.rollback()
        raise
    if controle and controle['valide']:
        try:      # même décision dans le registre des conflits du cabinet (parties retrouvées par les recherches futures si poursuivi)
            row = desk.db.execute('SELECT check_id FROM decision_conflits5697 WHERE id=?', (controle['id'],)).fetchone()
            conflicts500.decide(desk, row['check_id'], 'accepter' if decision == 'accepter' else 'decliner', motif or 'Aucun rapprochement : poursuite décidée.')
        except Stop:
            pass
    desk.audit('mission5697_decision', {'id': d['id'], 'decision': decision, 'par': a.compte[:80]})
    return lire(desk, a, d['id'])


# ------------------------------------------------------------------------------------------------ espace de préparation
def deposer(desk, acteur, demande_id, nature, nom, octets, mime='application/octet-stream'):
    """Fichier déposé tel quel dans l'espace privé de la demande (empreinte gardée). Une convention signée passe la convention à « signée »."""
    schema(desk)
    a = acces5697.acteur_de(acteur)
    d = lire(desk, a, demande_id)
    if d['etat'] == 'refusee':
        raise Stop('demande_close')
    if nature not in NATURES_PREPARATION:
        raise Stop('nature_invalide')
    if not isinstance(octets, (bytes, bytearray)) or not octets or len(octets) > MAX_FICHIER:
        raise Stop('fichier_vide_ou_trop_volumineux')
    nom = re.sub(r'[\\/:*?"<>|\x00-\x1f]+', ' ', _texte(nom, 120)).strip() or NATURES_PREPARATION[nature]
    sha = hashlib.sha256(octets).hexdigest()
    now = _now()
    _begin(desk)
    try:
        existe = desk.db.execute('SELECT id FROM preparation5697 WHERE demande_id=? AND nature=? AND sha256=?', (d['id'], nature, sha)).fetchone()
        ident = existe['id'] if existe else uuid.uuid4().hex
        if not existe:
            desk.db.execute('INSERT INTO preparation5697(id,demande_id,nature,nom,mime,octets,sha256,taille,depose_par,depose_le) VALUES(?,?,?,?,?,?,?,?,?,?)',
                            (ident, d['id'], nature, nom, _texte(mime, 120) or 'application/octet-stream', bytes(octets), sha, len(octets), a.compte, now))
            if nature == 'convention_signee':
                desk.db.execute("UPDATE demande_mission5697 SET convention_etat='signee', version=version+1, modifie_par=?, modifie_le=? WHERE id=?", (a.compte, now, d['id']))
            elif nature == 'convention_projet' and d['convention_etat'] != 'signee':
                desk.db.execute("UPDATE demande_mission5697 SET convention_etat='projet', version=version+1, modifie_par=?, modifie_le=? WHERE id=?", (a.compte, now, d['id']))
            _journal(desk, d['id'], a.compte, 'depot', '%s : %s (%s)' % (NATURES_PREPARATION[nature], nom, sha[:12]))
        desk.db.commit()
    except BaseException:
        if desk.db.in_transaction:
            desk.db.rollback()
        raise
    if d.get('dossier_id'):
        ranger_preparations(desk, a, d['id'])
    return lire(desk, a, d['id'])


def preparer_convention(desk, acteur, demande_id):
    """Projet de convention d'honoraires construit à partir des données saisies (sans IA) : document Word à relire, jamais signé ici."""
    from . import bibliotheque5639
    a = acces5697.acteur_de(acteur)
    d = lire(desk, a, demande_id)
    h = d['honoraires'] or {}

    def euros(c):
        return ('%s €' % ('{:,.2f}'.format(c / 100).replace(',', ' ').replace('.', ','))) if isinstance(c, int) else '[À COMPLÉTER]'
    mode = {'forfait': 'Honoraires forfaitaires', 'temps': 'Honoraires au temps passé', 'mixte': 'Forfait et temps passé'}.get(h.get('mode'), '[À COMPLÉTER : mode d’honoraires]')
    paras = [('Convention d’honoraires — projet', 'titre'),
             ('Client : %s' % (d['client_nom'] or '[À COMPLÉTER : client]'), 'texte'),
             ('Objet de la mission : %s' % (d['objet'] or '[À COMPLÉTER : objet]'), 'texte'),
             ('Périmètre : %s' % (h.get('perimetre') or '[À COMPLÉTER : périmètre]'), 'texte'),
             ('Mode : %s' % mode, 'texte')]
    if h.get('mode') in ('forfait', 'mixte'):
        paras.append(('Forfait : %s HT' % euros(h.get('forfait_cents')), 'liste'))
    if h.get('mode') in ('temps', 'mixte'):
        paras.append(('Taux horaire : %s HT' % euros(h.get('taux_horaire_cents')), 'liste'))
    paras.append(('Provision : %s' % (euros(h.get('provision_cents')) if h.get('provision_cents') is not None else 'aucune'), 'liste'))
    paras.append(('Projet préparé par AxiorHub à partir de la demande ; à relire. Il ne vaut convention qu’une fois signé.', 'note'))
    raw = bibliotheque5639.build_docx(paras, 'fr', notice=False)
    return deposer(desk, a, demande_id, 'convention_projet', 'Convention d’honoraires (projet) %s.docx' % (d['client_nom'] or '')[:60],
                   raw, 'application/vnd.openxmlformats-officedocument.wordprocessingml.document')


def preparation(desk, acteur, demande_id, ident):
    """Octets d'un fichier de l'espace de préparation (même contrôle que la demande)."""
    a = acces5697.acteur_de(acteur)
    lire(desk, a, demande_id)
    row = desk.db.execute('SELECT nom,mime,octets FROM preparation5697 WHERE id=? AND demande_id=?', (ident, demande_id)).fetchone()
    if not row:
        raise Stop('introuvable')
    return row['nom'], row['mime'], bytes(row['octets'])


def ranger_preparations(desk, acteur, demande_id):
    """Après la conversion : chaque fichier préparé est déposé dans le dossier par le registre commun (une commande par fichier, rejouable)."""
    from . import documents5697
    a = acces5697.acteur_de(acteur)
    d = _ligne(desk, demande_id)
    if not d.get('dossier_id'):
        return {'ranges': 0, 'en_attente': 0}
    ranges = attente = 0
    for row in desk.db.execute("SELECT * FROM preparation5697 WHERE demande_id=? AND document_id='' ORDER BY depose_le", (d['id'],)).fetchall():
        nom = row['nom'] if '.' in row['nom'][-6:] else row['nom'] + '.docx'
        try:
            out = documents5697.creer(desk, a, {'dossier': d['dossier_id'], 'mode': 'import', 'type': 'libre', 'titre': nom.rsplit('.', 1)[0],
                                                'nom_fichier': nom, 'octets': bytes(row['octets'])}, 'prep-' + row['id'])
        except Stop:
            attente += 1          # rangement du dossier en cours ou stockage indisponible : reste dans l'espace privé, rien n'est perdu
            continue
        if out.get('document_id'):
            desk.db.execute('UPDATE preparation5697 SET document_id=?, range_le=? WHERE id=?', (out['document_id'], _now(), row['id']))
            desk.db.commit()
            ranges += 1
        else:
            attente += 1
    return {'ranges': ranges, 'en_attente': attente}


def purger(desk, jours=RETENTION_JOURS):
    """Rétention de l'espace privé : contenu des fichiers rangés dans le dossier, ou d'une demande refusée, effacé après ``jours`` jours."""
    limite = (datetime.now(timezone.utc) - timedelta(days=jours)).isoformat()
    desk.db.execute("DELETE FROM preparation5697 WHERE (document_id<>'' AND range_le<?) OR demande_id IN (SELECT id FROM demande_mission5697 WHERE etat='refusee' AND modifie_le<?)",
                    (limite, limite))
    desk.db.commit()


# ------------------------------------------------------------------------------------------------ ouverture du dossier
def apercu(desk, acteur, demande_id):
    """« Ouvrir le dossier » : ce qui sera repris (client, parties, responsable, correspondance, accord, documents), le futur dossier et son
    rangement, et ce qui manque encore (visible, jamais bloquant sauf la décision)."""
    a = acces5697.acteur_de(acteur)
    d = lire(desk, a, demande_id)
    titre = d['objet'] or 'Nouvelle affaire'
    try:
        from . import dossiers5697
        reference = dossiers5697.proposer_reference(desk)
        emplacement = dossiers5697.emplacement_propose(desk, titre, reference)
    except (ImportError, Stop):
        reference, emplacement = '', ''
    a_regulariser = list(d['manquants'])
    if d['convention_etat'] != 'signee':
        a_regulariser.append('Convention signée (le statut « signée » exige le fichier signé)')
    return {'demande': d['id'], 'titre': titre, 'client': d['client_nom'], 'parties': _parties_controlees(d), 'responsable': d['responsable'],
            'correspondance': d['origine'] == 'courriel', 'accord': d['decision_motif'], 'documents': [p['nom'] for p in d['preparations']],
            'reference': reference, 'emplacement': emplacement, 'a_regulariser': a_regulariser, 'pret': d['etat'] == 'acceptee' and bool(d['controle'] and d['controle']['valide'])}


def _message(desk, d):
    """Courriel d'origine à rattacher au dossier (identité IMAP lue sur le serveur) ; inconnu : rien, il se classera à la main."""
    if d['origine'] != 'courriel' or not d['source_ref']:
        return None
    try:
        from .messages5697 import resoudre
        ref = resoudre(desk, d['source_ref'])
    except Stop:
        return None
    if not (ref.boite and ref.uidvalidity and str(ref.uid or '').isdigit()):
        return None
    return {'compte': ref.compte, 'boite': ref.boite, 'uidvalidity': ref.uidvalidity, 'uid': str(ref.uid), 'sujet': d['objet'], 'expediteur': d['correspondant'],
            'date': d['source_date']}


def convertir(desk, acteur, demande_id, version, data=None):
    """Ouvre le dossier d'une demande acceptée. Réservation unique (clé primaire = demande), données gelées, création par ``dossiers5697.creer``
    avec une clé stable ; le rejeu rend le dossier existant. Rien d'externe n'est envoyé."""
    from . import dossiers5697
    schema(desk)
    a = acces5697.acteur_de(acteur)
    _peut_decider(desk, a)
    data = data or {}
    d = lire(desk, a, demande_id)
    conv = desk.db.execute('SELECT * FROM demande_conversion5697 WHERE demande_id=?', (d['id'],)).fetchone()
    if conv and conv['etat'] == 'fait':
        return {'dossier_id': conv['dossier_id'], 'rejoue': True, 'message': 'Dossier déjà ouvert pour cette demande.', 'url': '/dossier?id=' + conv['dossier_id']}
    if not conv:
        if d['etat'] != 'acceptee':
            raise Stop('demande_non_acceptee')
        if not d['controle'] or not d['controle']['valide'] or d['controle']['decision'] != 'poursuivre':
            raise Stop('controle_conflits_a_refaire')
        if d['version'] != int(version):
            raise Stop('version_en_conflit')
        parties = [{'nom': p['nom'], 'role': {'tiers': 'autre'}.get(p['role'], p['role']), 'courriel': p['courriel']} for p in _parties_controlees(d)]
        donnees = {'titre': d['objet'] or 'Nouvelle affaire', 'client': d['client_nom'], 'client_etat': 'identifie' if d['client_nom'] else 'a_identifier',
                   'nature': d['type_mission'], 'responsable': d['responsable'] or a.compte, 'parties': parties, 'provenance': 'manuel',
                   'reference': _texte(data.get('reference'), 40), 'confidentialite': 'reserve' if data.get('reserve') else 'cabinet',
                   'membres': [x for x in (data.get('membres') or []) if isinstance(x, str)][:50]}
        _begin(desk)
        try:
            row = desk.db.execute('SELECT version,etat FROM demande_mission5697 WHERE id=?', (d['id'],)).fetchone()
            if row['etat'] != 'acceptee' or row['version'] != d['version']:
                raise Stop('version_en_conflit')
            if not desk.db.execute('SELECT 1 FROM demande_conversion5697 WHERE demande_id=?', (d['id'],)).fetchone():
                desk.db.execute('INSERT INTO demande_conversion5697(demande_id,cle_dossier,acteur,role,donnees,etat,cree_le,maj_le) VALUES(?,?,?,?,?,?,?,?)',
                                (d['id'], 'mission5697.' + d['id'], a.compte, a.role, json.dumps(donnees, ensure_ascii=False), 'reserve', _now(), _now()))
                _journal(desk, d['id'], a.compte, 'conversion_reservee', '')
            desk.db.commit()
        except BaseException:
            if desk.db.in_transaction:
                desk.db.rollback()
            raise
        conv = desk.db.execute('SELECT * FROM demande_conversion5697 WHERE demande_id=?', (d['id'],)).fetchone()
    # même acteur et même clé que la réservation : ``dossiers5697`` rend le reçu initial si le dossier a déjà été créé (réponse perdue, reprise)
    proprietaire = acces5697.Acteur(conv['acteur'], conv['role'])
    recu = dossiers5697.creer(desk, proprietaire, json.loads(conv['donnees']), conv['cle_dossier'], message=_message(desk, d))
    dossier_id = recu['id']
    _begin(desk)
    try:
        desk.db.execute("UPDATE demande_conversion5697 SET dossier_id=?, etat='fait', maj_le=? WHERE demande_id=?", (dossier_id, _now(), d['id']))
        desk.db.execute("UPDATE demande_mission5697 SET etat='convertie', dossier_id=?, version=version+1, modifie_par=?, modifie_le=? WHERE id=?",
                        (dossier_id, a.compte, _now(), d['id']))
        _journal(desk, d['id'], a.compte, 'conversion', '%s · %s' % (recu.get('reference', ''), recu.get('titre', '')))
        desk.db.commit()
    except BaseException:
        if desk.db.in_transaction:
            desk.db.rollback()
        raise
    _apres_conversion(desk, a, d, dossier_id)
    desk.audit('mission5697_conversion', {'id': d['id'], 'dossier': dossier_id, 'par': a.compte[:80]})
    return {**recu, 'dossier_id': dossier_id, 'message': 'Dossier %s ouvert depuis la demande. %s' % (recu.get('reference', ''), (recu.get('stockage') or {}).get('libelle', ''))}


def _apres_conversion(desk, a, d, dossier_id):
    """Suites locales de la conversion, chacune rejouable : parties retrouvées par les recherches de conflits, contrôle reconnu pour le
    nouveau dossier (la production n'y est pas bloquée une seconde fois), rangement du répertoire, fichiers préparés déposés."""
    from . import conflicts500, metier500
    try:
        metier500.ensure_schema(desk)
        check = desk.db.execute("SELECT check_id FROM decision_conflits5697 WHERE demande_id=? AND valide=1 AND decision='poursuivre' ORDER BY cree_le DESC LIMIT 1",
                                (d['id'],)).fetchone()
        desk.db.execute('INSERT OR IGNORE INTO conflicts500_matters VALUES(?,?,?,?,?)', (dossier_id, _now(), 0, check['check_id'] if check else '', 'examine'))
        desk.db.commit()
        for p in _parties_controlees(d):
            conflicts500.add_party(desk, dossier_id, p['nom'], p['role'], p['courriel'])
    except Exception:   # noqa: BLE001 — le dossier est ouvert ; ces suites se refont depuis la page Conflits
        if desk.db.in_transaction:
            desk.db.rollback()
    try:
        from . import dossiers5697
        dossiers5697.traiter_rangements(desk, dossier=dossier_id, limite=1)
    except Exception:   # noqa: BLE001 — stockage injoignable : « Rangement en attente », repris par le travailleur des rangements
        pass
    ranger_preparations(desk, a, d['id'])


def base64_octets(valeur):
    try:
        return base64.b64decode(str(valeur or ''), validate=True)
    except ValueError:
        raise Stop('contenu_fichier_invalide') from None
