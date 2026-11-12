"""5.6.97 (lot gestion, § 18) : registre procédural et preuve des dépôts.

Échéances. Le registre reste ``echeances450`` (détection qualifiée, candidat, confirmation, correction motivée, écart, accomplissement,
agenda par identifiant stable). Ce module y ajoute ce que le § 18 demande sans créer un second registre :
- l'état affiché « À vérifier » pour toute date proposée (extraite d'un acte ou d'un courriel, ou départ incertain) : ni rappel ferme, ni
  événement d'agenda tant qu'une personne ne l'a pas validée ;
- la saisie manuelle **sourcée** (une source et un repère ouvrable sont exigés) ;
- le suivi : responsable, destinataire et date du rappel (pour une échéance confirmée seulement), réattribution tracée, rappel critique non
  acquitté signalé ; **l'acquittement du rappel ne vaut pas accomplissement de l'acte** (accomplir reste une décision motivée et prouvée).
Déplacer un événement dans un agenda ne modifie pas l'échéance : seul le registre fait foi, l'agenda en est une projection.

Dépôts. « Préparer le paquet » **gèle les octets** de l'acte et des pièces (copie conservée dans la base, empreinte SHA-256) et la liste
ordonnée des pièces : modifier le Word ensuite ne change pas le paquet d'origine, qui reste ouvrable. Aucun dépôt externe n'est déclenché
par la création du paquet (AxiorHub n'a aucun canal de dépôt intégré). Trois états restent distincts : l'**alerte** (notification e-Barreau
ou courriel : repère un événement, ne prouve rien), le **dépôt déclaré** (canal, référence d'affaire, identifiant d'envoi, date, auteur),
l'**accusé rapproché** (accusé original importé dont la référence, l'envoi et la date correspondent au dépôt déclaré). Un accusé d'un
autre envoi est refusé, et le refus reste visible ; un accusé déjà importé n'est pas importé deux fois.
"""
import base64
from datetime import date
import hashlib
import json
from pathlib import PurePosixPath
import re
import unicodedata

from .common import Stop
from . import gestion5697 as g

ETATS_ECHEANCE = {'a_confirmer': 'À vérifier', 'a_completer': 'À vérifier (calcul à compléter)', 'confirmee': 'Confirmée', 'manuel': 'Confirmée (corrigée à la main)',
                  'terminee': 'Accomplie', 'annulee': 'Écartée ou sans objet'}
CANAUX = {'ebarreau': 'e-Barreau (RPVA)', 'rpva_autre': 'Autre plateforme de communication électronique', 'greffe': 'Dépôt au greffe', 'courrier': 'Envoi postal', 'autre': 'Autre canal'}
ETATS_DEPOT = {'gele': 'Paquet gelé : aucun dépôt déclaré', 'depot_declare': 'Dépôt déclaré : accusé à rapprocher', 'accuse_rapproche': 'Accusé rapproché',
               'rejete': 'Rejet signalé par le canal'}
NATURES_ACCUSE = {'accuse': 'Accusé de dépôt original', 'notification': 'Notification (alerte seulement)', 'rejet': 'Avis de rejet'}
ETATS_ACCUSE = {'rapproche': 'Rapproché', 'refuse': 'Rapprochement refusé', 'alerte': 'Alerte : ne vaut pas accusé', 'rejet': 'Rejet enregistré'}
MAX_PAQUET = 200 * 1024 * 1024
MAX_ACCUSE = 10 * 1024 * 1024
MENTION = 'Un paquet gelé n’est pas une preuve de dépôt ; seul l’accusé original rapproché l’est.'


# ------------------------------------------------------------------------------------------------ registre
class AgendaAbsent:
    """Aucun agenda fourni : l'écriture échoue proprement (« agenda indisponible »), jamais un succès supposé ni une erreur technique."""

    def put_event(self, *args, **kwargs):
        raise Stop('agenda_indisponible')


def _suivi(desk, deadline_id):
    row = desk.db.execute('SELECT * FROM echeance_suivi5697 WHERE deadline_id=?', (deadline_id,)).fetchone()
    return dict(row) if row else None


def _echeance(desk, acteur, ident):
    from . import echeances450
    echeances450.ensure_schema(desk)
    row = echeances450._row(desk, str(ident or ''))       # noqa: SLF001 — lecture du registre existant, sans le dupliquer
    if not row:
        raise Stop('introuvable')
    g.dossier(desk, acteur, row['matter'])                 # garde avant tout : réservé ou absent, même refus
    return row


def _publique(desk, row):
    from . import echeances450
    out = echeances450.public(row)
    out['etat_libelle'] = ETATS_ECHEANCE.get(row['status'], row['status'])
    out['a_verifier'] = row['status'] in ('a_confirmer', 'a_completer')
    out['suivi'] = _suivi(desk, row['id'])
    return out


def registre(desk, acteur, mid):
    """Dossier → Procédure : échéances (toutes, closes comprises), actes consignés, audiences et dépôts, après la garde du dossier."""
    from . import echeances450
    g.dossier(desk, acteur, mid)
    g.schema(desk)
    echeances450.ensure_schema(desk)
    echeances = [_publique(desk, dict(r)) for r in desk.db.execute(
        "SELECT * FROM deadlines450 WHERE matter=? ORDER BY CASE WHEN due='' THEN 1 ELSE 0 END, due, created", (str(mid),))]
    actes = echeances450.actes_consignes(desk, str(mid))
    try:
        audiences = [dict(r) for r in desk.db.execute("SELECT id,juridiction,chambre,type_audience,rg,statut FROM audiences5688 WHERE matter=?", (str(mid),))]
    except Exception:   # noqa: BLE001 — table des audiences jamais initialisée
        audiences = []
    return {'echeances': echeances, 'actes': actes, 'audiences': audiences, 'depots': depots(desk, acteur, mid), 'mention': MENTION}


def saisir_echeance(desk, acteur, mid, data, dav=None):
    """Saisie manuelle sourcée. Départ prouvé : échéance confirmée (agenda selon les réglages). Départ incertain : « À vérifier », aucun rappel."""
    from . import echeances450
    g.dossier(desk, acteur, mid)
    g.droit(desk, acteur, 'valider')
    source = g.texte(data.get('source'), 500)
    if len(source) < 3:
        raise Stop('source_requise')
    regle, depart = str(data.get('regle') or ''), str(data.get('depart') or '')
    try:
        date.fromisoformat(depart)
    except ValueError:
        raise Stop('date_invalide') from None
    note = g.texte(data.get('note'), 400)
    if data.get('depart_certain') is True:
        out = echeances450.create_manual(desk, dav or AgendaAbsent(), str(mid), regle, depart, source_path=source, note=note)
    else:
        out = echeances450.create_deadline(desk, str(mid), regle, depart, source_path=source, excerpt=g.texte(data.get('extrait'), 400),
                                           origin='manuel_a_verifier', status='a_confirmer', note=note)
    g.schema(desk)
    with g.transaction(desk):
        g.journal(desk, mid, 'echeance', out['id'], 'saisie_manuelle', acteur, source, {}, {'statut': out['status'], 'due': out.get('due', '')})
    return _publique(desk, echeances450._row(desk, out['id']))


def decider(desk, acteur, ident, data, dav=None):
    """Valider, corriger (motif), écarter (motif) ou marquer accompli (preuve). Toujours tracé ; version attendue (409 sinon)."""
    from . import echeances450
    row = _echeance(desk, acteur, ident)
    g.droit(desk, acteur, 'valider')
    action, cle, version = str(data.get('action') or ''), str(data.get('cle') or ''), data.get('version')
    qui = g.compte(acteur)
    dav = dav or AgendaAbsent()
    if action == 'valider':
        out = echeances450.confirm(desk, dav, row['id'], actor=qui, version=version, cle=cle)
    elif action == 'corriger':
        out = echeances450.correct(desk, dav, row['id'], data.get('motif'), new_due=str(data.get('date') or ''), start_date=str(data.get('depart') or ''),
                                   actor=qui, version=version, cle=cle)
    elif action == 'ecarter':
        out = echeances450.close(desk, dav, row['id'], 'annulee', data.get('motif'), qui, version, cle, ecart=True)
    elif action == 'accomplir':
        preuve = g.texte(data.get('preuve'), 500)
        if len(preuve) < 3:
            raise Stop('preuve_accomplissement_requise')     # l'acquittement d'un rappel ne suffit pas : il faut une preuve de l'acte
        out = echeances450.close(desk, dav, row['id'], 'terminee', preuve, qui, version, cle)
        g.schema(desk)
        desk.db.execute('INSERT INTO echeance_suivi5697(deadline_id,matter,preuve_accomplissement,updated) VALUES(?,?,?,?) ON CONFLICT(deadline_id) DO UPDATE SET '
                        'preuve_accomplissement=excluded.preuve_accomplissement,updated=excluded.updated', (row['id'], row['matter'], preuve, g.maintenant()))
    else:
        raise Stop('action_inconnue')
    g.schema(desk)
    with g.transaction(desk):
        g.journal(desk, row['matter'], 'echeance', row['id'], action, acteur, str(data.get('motif') or data.get('preuve') or ''), {'statut': row['status'], 'due': row['due']},
                  {'statut': out['status'], 'due': out.get('due', '')})
    return _publique(desk, echeances450._row(desk, row['id']))


def attribuer(desk, acteur, ident, data):
    """Responsable, destinataire et date du rappel. Un rappel ne porte que sur une échéance confirmée, avec un destinataire ET une date."""
    from . import echeances450
    row = _echeance(desk, acteur, ident)
    g.droit(desk, acteur, 'agenda')
    g.schema(desk)
    actuel = _suivi(desk, row['id']) or {'responsable': '', 'rappel_destinataire': '', 'rappel_le': '', 'critique': 1, 'revision': 0}
    if actuel['revision']:
        g.version_attendue(actuel, data.get('version_suivi'))
    responsable = g.texte(data.get('responsable', actuel['responsable']), 160)
    destinataire = g.texte(data.get('rappel_destinataire', actuel['rappel_destinataire']), 160)
    rappel = str(data.get('rappel_le', actuel['rappel_le']) or '')
    if rappel or destinataire:
        if row['status'] not in echeances450.INSCRITES:
            raise Stop('rappel_sur_echeance_confirmee_seulement')
        if not destinataire or not rappel:
            raise Stop('rappel_destinataire_et_date_requis')
        rappel = g.jour(rappel)
    critique = 1 if data.get('critique', actuel['critique']) not in (False, 0, '0', 'non') else 0
    nouveau = {'responsable': responsable, 'rappel_destinataire': destinataire, 'rappel_le': rappel, 'critique': critique}
    with g.transaction(desk):
        desk.db.execute('INSERT INTO echeance_suivi5697(deadline_id,matter,responsable,rappel_destinataire,rappel_le,critique,updated) VALUES(?,?,?,?,?,?,?) '
                        'ON CONFLICT(deadline_id) DO UPDATE SET responsable=excluded.responsable,rappel_destinataire=excluded.rappel_destinataire,'
                        'rappel_le=excluded.rappel_le,critique=excluded.critique,acquitte_par=CASE WHEN echeance_suivi5697.rappel_le<>excluded.rappel_le '
                        "THEN '' ELSE echeance_suivi5697.acquitte_par END,acquitte_le=CASE WHEN echeance_suivi5697.rappel_le<>excluded.rappel_le THEN '' "
                        'ELSE echeance_suivi5697.acquitte_le END,revision=echeance_suivi5697.revision+1,updated=excluded.updated',
                        (row['id'], row['matter'], responsable, destinataire, rappel, critique, g.maintenant()))
        g.journal(desk, row['matter'], 'echeance', row['id'], 'reattribution' if actuel['responsable'] and actuel['responsable'] != responsable else 'suivi',
                  acteur, '', {k: actuel.get(k) for k in nouveau}, nouveau)
    return _publique(desk, echeances450._row(desk, row['id']))


def acquitter_rappel(desk, acteur, ident):
    """Le destinataire a vu le rappel. L'échéance reste ouverte : seul « accomplir », avec preuve, la clôt."""
    row = _echeance(desk, acteur, ident)
    g.schema(desk)
    suivi = _suivi(desk, row['id'])
    if not suivi or not suivi['rappel_le']:
        raise Stop('aucun_rappel')
    with g.transaction(desk):
        desk.db.execute('UPDATE echeance_suivi5697 SET acquitte_par=?,acquitte_le=?,revision=revision+1,updated=? WHERE deadline_id=?',
                        (g.compte(acteur), g.maintenant(), g.maintenant(), row['id']))
        g.journal(desk, row['matter'], 'echeance', row['id'], 'rappel_acquitte', acteur)
    return _publique(desk, row)


def rappels_a_signaler(desk, acteur, aujourdhui=None):
    """Rappels critiques échus et non acquittés d'échéances encore ouvertes : signalés (au responsable, sinon à l'administrateur). Droits d'abord."""
    from . import echeances450
    g.schema(desk)
    echeances450.ensure_schema(desk)
    perimetre = g.perimetre(desk, acteur)
    jour = (aujourdhui or date.today()).isoformat()
    filtre, args = perimetre.sql('s.matter')
    rows = desk.db.execute("SELECT s.*, d.due, d.status FROM echeance_suivi5697 s JOIN deadlines450 d ON d.id=s.deadline_id WHERE " + filtre +
                           " AND s.critique=1 AND s.rappel_le<>'' AND s.rappel_le<=? AND s.acquitte_le='' AND d.status IN ('confirmee','manuel') ORDER BY s.rappel_le",
                           (*args, jour)).fetchall()
    out = []
    for r in rows:
        if not r['signale_le']:
            desk.db.execute('UPDATE echeance_suivi5697 SET signale_le=? WHERE deadline_id=?', (g.maintenant(), r['deadline_id']))
            g.journal(desk, r['matter'], 'echeance', r['deadline_id'], 'rappel_critique_non_acquitte', 'serveur')
        out.append({'echeance': r['deadline_id'], 'matter': r['matter'], 'due': r['due'], 'destinataire': r['rappel_destinataire'],
                    'signaler_a': r['responsable'] or 'administrateur', 'rappel_le': r['rappel_le']})
    desk.db.commit()
    return out


# ------------------------------------------------------------------------------------------------ paquet gelé
def _lire(client, path):
    info = client.stat(path)
    return info, client.download(info)


def _depot(desk, acteur, ident):
    g.schema(desk)
    row = desk.db.execute('SELECT * FROM depots5697 WHERE id=?', (str(ident or ''),)).fetchone()
    if not row:
        raise Stop('introuvable')
    g.dossier(desk, acteur, row['matter'])
    return row


def _elements(desk, mid, data):
    """Liste ordonnée (acte, puis pièces dans l'ordre du bordereau). Depuis un paquet du contentieux, ses fichiers et empreintes font foi."""
    if data.get('paquet_id'):
        from . import contentieux_paquets5689 as paquets
        pq = paquets._paquet(desk, data['paquet_id'])        # noqa: SLF001
        if pq['matter'] != str(mid):
            raise Stop('introuvable')
        return [{'role': f['role'], 'titre': f.get('titre', ''), 'numero': str(f.get('numero') or ''), 'piece_id': f.get('piece_id', ''), 'path': f['path'],
                 'sha_attendu': f['sha256']} for f in json.loads(pq['fichiers'] or '[]')], pq['id']
    out = []
    acte = data.get('acte') if isinstance(data.get('acte'), dict) else ({'path': data['acte_path']} if data.get('acte_path') else None)
    if acte:
        out.append({'role': 'acte', 'titre': g.texte(acte.get('titre') or PurePosixPath(str(acte['path'])).name, 200), 'numero': '', 'piece_id': '', 'path': str(acte['path'])})
    for p in data.get('pieces') if isinstance(data.get('pieces'), list) else []:
        if not isinstance(p, dict) or not p.get('path'):
            raise Stop('piece_invalide')
        out.append({'role': 'piece', 'titre': g.texte(p.get('titre') or PurePosixPath(str(p['path'])).name, 200), 'numero': g.texte(p.get('numero'), 20),
                    'piece_id': g.texte(p.get('piece_id'), 80), 'path': str(p['path'])})
    return out, ''


def preparer_paquet(desk, acteur, mid, data, client=None):
    """« Préparer le paquet » : octets et liste des pièces gelés, avec leurs empreintes. Aucun dépôt externe n'est déclenché."""
    from . import pause5629
    from .office440 import dav_client
    m = g.dossier(desk, acteur, mid)
    g.schema(desk)
    cle = g.cle_de(data.get('cle'))
    canal = str(data.get('canal') or '')
    if canal not in CANAUX:
        raise Stop('canal_invalide')
    elements, paquet_id = _elements(desk, mid, data)
    if not elements:
        raise Stop('paquet_vide')
    reference = g.texte(data.get('reference_affaire'), 120)
    params = g.empreinte([str(mid), canal, reference, paquet_id, [e['path'] for e in elements]])
    old = g.deja_fait(desk, 'depots5697', cle, params)
    if old:
        return vue_depot(desk, acteur, old['id'])
    client = client or dav_client(desk)
    racine = str(m.get('path') or '')
    fichiers, total = [], 0
    for rang, e in enumerate(elements, 1):
        if racine and not (e['path'] == racine or e['path'].startswith(racine.rstrip('/') + '/')):
            raise Stop('fichier_hors_dossier')
        pause5629.checkpoint(desk)
        info, raw = _lire(client, e['path'])
        sha = hashlib.sha256(raw).hexdigest()
        if e.get('sha_attendu') and e['sha_attendu'] != sha:
            raise Stop('manifeste_modifie')                  # le fichier a changé depuis le manifeste du paquet : rien n'est gelé
        total += len(raw)
        if total > MAX_PAQUET:
            raise Stop('paquet_trop_volumineux')
        fichiers.append({**{k: e[k] for k in ('role', 'titre', 'numero', 'piece_id', 'path')}, 'rang': rang, 'version_id': str(info.get('etag') or ''),
                         'sha256': sha, 'taille': len(raw), 'contenu': raw})
    manifeste = {'dossier': str(mid), 'canal': canal, 'reference_affaire': reference, 'paquet_contentieux': paquet_id,
                 'fichiers': [{k: f[k] for k in ('rang', 'role', 'titre', 'numero', 'piece_id', 'path', 'version_id', 'sha256', 'taille')} for f in fichiers],
                 'depot': {'declare': False, 'accuse_rapproche': False}}
    ident, now = g.nouvel_id('dep'), g.maintenant()
    with g.transaction(desk):
        desk.db.execute('INSERT INTO depots5697(id,matter,cle,parametres,paquet_id,canal,reference_affaire,manifeste,manifeste_sha,etat,cree_par,created,updated) '
                        'VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?)', (ident, str(mid), cle, params, paquet_id, canal, reference, json.dumps(manifeste, ensure_ascii=False),
                                                              g.empreinte(manifeste), 'gele', g.compte(acteur), now, now))
        for f in fichiers:
            desk.db.execute('INSERT INTO depot_fichiers5697(depot_id,rang,role,titre,numero,piece_id,path,version_id,sha256,taille,contenu) VALUES(?,?,?,?,?,?,?,?,?,?,?)',
                            (ident, f['rang'], f['role'], f['titre'], f['numero'], f['piece_id'], f['path'], f['version_id'], f['sha256'], f['taille'], f['contenu']))
        g.journal(desk, mid, 'depot', ident, 'paquet_gele', acteur, '', {}, {'manifeste_sha': g.empreinte(manifeste), 'fichiers': len(fichiers)})
    return vue_depot(desk, acteur, ident)


def declarer_depot(desk, acteur, ident, data):
    """Dépôt fait par l'avocat sur le canal retenu : canal, référence d'affaire, identifiant d'envoi, date et auteur. Ce n'est pas encore une preuve."""
    row = _depot(desk, acteur, ident)
    g.droit(desk, acteur, 'valider')
    g.version_attendue(row, data.get('version'))
    if row['etat'] != 'gele':
        raise Stop('depot_deja_declare')
    envoi = g.texte(data.get('envoi_ref'), 120)
    if len(envoi) < 3:
        raise Stop('identifiant_envoi_requis')
    jour = g.jour(data.get('date_depot'))
    canal = str(data.get('canal') or row['canal'])
    if canal not in CANAUX:
        raise Stop('canal_invalide')
    reference = g.texte(data.get('reference_affaire') or row['reference_affaire'], 120)
    if not reference:
        raise Stop('reference_affaire_requise')
    with g.transaction(desk):
        n = desk.db.execute("UPDATE depots5697 SET etat='depot_declare',canal=?,reference_affaire=?,envoi_ref=?,date_depot=?,declare_par=?,declare_le=?,revision=revision+1,updated=? "
                            "WHERE id=? AND revision=? AND etat='gele'", (canal, reference, envoi, jour, g.compte(acteur), g.maintenant(), g.maintenant(), row['id'], row['revision'])).rowcount
        if not n:
            raise Stop('version_en_conflit')
        g.journal(desk, row['matter'], 'depot', row['id'], 'depot_declare', acteur, '', {'etat': row['etat']}, {'etat': 'depot_declare', 'envoi': envoi, 'date': jour})
    return vue_depot(desk, acteur, row['id'])


def _norme(valeur):
    """Forme comparable : sans accents, espaces ni ponctuation ; « RG 24/01234 » et « N° RG : 24/01234 » se valent."""
    valeur = unicodedata.normalize('NFKD', str(valeur or '')).encode('ascii', 'ignore').decode().upper()
    return re.sub(r'^(?:NO?)?RG', '', re.sub(r'[^A-Z0-9]', '', valeur))


# Une ligne « Libellé : valeur » ; la valeur est la fin de la ligne (une référence d'affaire contient des espaces : « RG 24/01234 »).
RE_REFERENCE = re.compile(r'^[ \t]*(?:r[ée]f[ée]rence(?:\s+de\s+l.affaire)?|n[°o]\s*rg)[ \t]*:[ \t]*([^\r\n]{3,60})', re.I | re.M)
RE_ENVOI = re.compile(r'^[ \t]*(?:identifiant\s+(?:de\s+l.|d.)?envoi|n[°o]\s*(?:d.)?envoi|envoi\s+n[°o])[ \t]*:[ \t]*([^\r\n]{3,60})', re.I | re.M)
RE_DATE = re.compile(r'(?:date(?:\s+(?:de\s+)?(?:d[ée]p[ôo]t|r[ée]ception|l.envoi))?|d[ée]pos[ée]e?\s+le|re[çc]ue?\s+le)\s*:?\s*(\d{2})/(\d{2})/(\d{4})', re.I)


def lire_accuse(raw):
    """Repères lus dans un accusé textuel (texte, courriel, XML). Rien n'est deviné : un champ absent reste vide. PDF : à saisir par la personne."""
    try:
        texte = raw.decode('utf-8')
    except UnicodeDecodeError:
        return {}
    if '\x00' in texte[:2048]:
        return {}
    out = {}
    m = RE_REFERENCE.search(texte)
    if m:
        out['reference'] = m.group(1).strip()
    m = RE_ENVOI.search(texte)
    if m:
        out['envoi_ref'] = m.group(1).strip()
    m = RE_DATE.search(texte)
    if m:
        try:
            out['date'] = date(int(m.group(3)), int(m.group(2)), int(m.group(1))).isoformat()
        except ValueError:
            pass
    return out


def importer_accuse(desk, acteur, ident, data):
    """Accusé original (ou notification, ou avis de rejet) relié au paquet. Rapproché seulement si référence, envoi ET date concordent."""
    row = _depot(desk, acteur, ident)
    nature = str(data.get('nature') or 'accuse')
    if nature not in NATURES_ACCUSE:
        raise Stop('nature_accuse_invalide')
    try:
        raw = base64.b64decode(str(data.get('contenu_b64') or ''), validate=True)
    except ValueError:
        raise Stop('fichier_invalide') from None
    if not raw:
        raise Stop('fichier_requis')
    if len(raw) > MAX_ACCUSE:
        raise Stop('fichier_trop_volumineux')
    sha = hashlib.sha256(raw).hexdigest()
    deja = desk.db.execute('SELECT * FROM depot_accuses5697 WHERE sha256=? AND nature=?', (sha, nature)).fetchone()
    if deja:
        if deja['depot_id'] != row['id']:
            raise Stop('accuse_deja_rattache_a_un_autre_depot')
        return vue_depot(desk, acteur, row['id']) | {'deja_importe': True}
    lu = lire_accuse(raw)
    saisi = {'reference': g.texte(data.get('reference'), 120), 'envoi_ref': g.texte(data.get('envoi_ref'), 120), 'date': str(data.get('date') or '')[:10]}
    ecarts = ['%s lue dans le fichier (%s) différente de la saisie (%s)' % (k, lu[k], saisi[k]) for k in ('reference', 'envoi_ref', 'date')
              if lu.get(k) and saisi[k] and _norme(lu[k]) != _norme(saisi[k])]
    valeurs = {k: saisi[k] or lu.get(k, '') for k in saisi}
    if nature == 'notification':
        etat, motif = 'alerte', 'Une notification signale un événement ; elle ne vaut pas accusé de dépôt.'
    elif nature == 'rejet':
        etat, motif = 'rejet', 'Avis de rejet importé.'
    else:
        if row['etat'] not in ('depot_declare', 'accuse_rapproche'):
            raise Stop('depot_non_declare')
        if not all(valeurs.values()):
            raise Stop('reference_envoi_date_requis')
        if valeurs['date']:
            g.jour(valeurs['date'])
        attendu = {'reference': row['reference_affaire'], 'envoi_ref': row['envoi_ref'], 'date': row['date_depot']}
        noms = {'reference': 'référence d’affaire', 'envoi_ref': 'identifiant d’envoi', 'date': 'date'}
        ecarts += ['%s de l’accusé (%s) différente du dépôt déclaré (%s)' % (noms[k], valeurs[k], attendu[k]) for k in attendu if _norme(valeurs[k]) != _norme(attendu[k])]
        if row['etat'] == 'accuse_rapproche':
            ecarts.append('un accusé est déjà rapproché pour ce dépôt')
        etat, motif = ('refuse', 'Rapprochement refusé : ' + ' ; '.join(ecarts) + '.') if ecarts else ('rapproche', '')
    aid = g.nouvel_id('acc')
    with g.transaction(desk):
        desk.db.execute('INSERT INTO depot_accuses5697(id,depot_id,matter,nature,sha256,nom_fichier,contenu,reference,envoi_ref,date_accuse,etat,motif,importe_par,importe_le) '
                        'VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?)', (aid, row['id'], row['matter'], nature, sha, g.texte(data.get('nom_fichier'), 200), raw, valeurs['reference'],
                                                                valeurs['envoi_ref'], valeurs['date'], etat, motif[:900], g.compte(acteur), g.maintenant()))
        nouvel = {'rapproche': 'accuse_rapproche', 'rejet': 'rejete'}.get(etat)
        if nouvel and row['etat'] != 'accuse_rapproche':
            desk.db.execute('UPDATE depots5697 SET etat=?,revision=revision+1,updated=? WHERE id=?', (nouvel, g.maintenant(), row['id']))
        g.journal(desk, row['matter'], 'depot', row['id'], 'accuse_' + etat, acteur, motif, {'etat': row['etat']}, {'accuse': aid, 'sha256': sha})
    return vue_depot(desk, acteur, row['id'])


def vue_depot(desk, acteur, ident):
    """Manifeste, fichiers gelés (sans leurs octets), accusés et anomalies visibles. Rien n'est relu dans Nextcloud : le paquet est gelé."""
    row = _depot(desk, acteur, ident)
    out = {k: row[k] for k in row.keys() if k not in ('parametres', 'cle')}
    out['manifeste'] = json.loads(row['manifeste'])
    out['etat_libelle'], out['canal_libelle'], out['mention'] = ETATS_DEPOT.get(row['etat'], row['etat']), CANAUX.get(row['canal'], row['canal']), MENTION
    out['fichiers'] = [dict(r) for r in desk.db.execute('SELECT rang,role,titre,numero,piece_id,path,version_id,sha256,taille FROM depot_fichiers5697 WHERE depot_id=? ORDER BY rang',
                                                        (row['id'],))]
    out['accuses'] = [{**dict(r), 'nature_libelle': NATURES_ACCUSE[r['nature']], 'etat_libelle': ETATS_ACCUSE.get(r['etat'], r['etat'])} for r in desk.db.execute(
        'SELECT id,nature,sha256,nom_fichier,reference,envoi_ref,date_accuse,etat,motif,importe_par,importe_le FROM depot_accuses5697 WHERE depot_id=? ORDER BY importe_le', (row['id'],))]
    out['anomalies'] = [a['motif'] for a in out['accuses'] if a['etat'] in ('refuse', 'rejet')]
    out['alertes'] = [a['motif'] for a in out['accuses'] if a['etat'] == 'alerte']
    out['rapproche'] = row['etat'] == 'accuse_rapproche'
    return out


def depots(desk, acteur, mid):
    g.dossier(desk, acteur, mid)
    g.schema(desk)
    return [{'id': r['id'], 'canal': CANAUX.get(r['canal'], r['canal']), 'etat': r['etat'], 'etat_libelle': ETATS_DEPOT.get(r['etat'], r['etat']), 'reference_affaire': r['reference_affaire'],
             'date_depot': r['date_depot'], 'created': r['created'], 'revision': r['revision']}
            for r in desk.db.execute('SELECT * FROM depots5697 WHERE matter=? ORDER BY created DESC LIMIT 50', (str(mid),))]


def ouvrir_fichier(desk, acteur, ident, rang):
    """Octets gelés d'un fichier du paquet (ou d'un accusé : ``rang`` = identifiant d'accusé), revérifiés contre leur empreinte."""
    row = _depot(desk, acteur, ident)
    f = desk.db.execute('SELECT path,sha256,contenu FROM depot_fichiers5697 WHERE depot_id=? AND rang=?', (row['id'], int(rang))).fetchone()
    if not f:
        raise Stop('introuvable')
    raw = bytes(f['contenu'])
    if hashlib.sha256(raw).hexdigest() != f['sha256']:
        raise Stop('empreinte_paquet_alteree')
    return raw, PurePosixPath(f['path']).name


def ouvrir_accuse(desk, acteur, ident, accuse_id):
    row = _depot(desk, acteur, ident)
    a = desk.db.execute('SELECT nom_fichier,sha256,contenu FROM depot_accuses5697 WHERE depot_id=? AND id=?', (row['id'], str(accuse_id))).fetchone()
    if not a:
        raise Stop('introuvable')
    raw = bytes(a['contenu'])
    if hashlib.sha256(raw).hexdigest() != a['sha256']:
        raise Stop('empreinte_paquet_alteree')
    return raw, a['nom_fichier'] or 'accuse'
