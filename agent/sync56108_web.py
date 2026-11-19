"""5.6.108 (lot S4) : synchronisation des postes — parcours « Créer un espace partagé » / « Rejoindre le cabinet », indicateur, conflits,
rapprochement, dossiers à partager, missions, pauses (Paramètres › Confort et maintenance, poste de travail seulement).

Routes ``/api440/m56108/sync/*`` :

- GET ``etat`` (indicateur et détail, lecture seule) ;
- POST administrateur : ``creer``, ``rejoindre``, ``code``, ``appairer``, ``adhesion/accepter``, ``adhesion/refuser``, ``retirer``,
  ``desactiver``, ``categories``, ``pause_cabinet`` ;
- POST administrateur et avocat : ``partager``, ``resoudre``, ``rapprocher``, ``mission/decider``, ``mission/transferer``, ``mission/reprendre``.

Toute écriture : origine et jeton (``check_post``), clé d'idempotence (``cle`` ; même clé et autre corps : 409), réponse
``Cache-Control: private, no-store`` ; objet absent et objet interdit : même 404 neutre ; rôle qui ne peut jamais appeler : 403. Aucune
écriture pendant une lecture. Pare-feu : jamais modifié par AxiorHub ; Windows demande lui-même l'autorisation « réseau privé » quand le
poste se met à l'écoute, la rubrique l'explique.
"""
from html import escape as e
import hashlib
import json
import re
import secrets

from .common import Stop
from . import sync56108 as S

PREFIXE = 'm56108/sync/'
ROLES = ('administrateur', 'avocat', 'assistant')
ROUTES_GET = ('etat',)
ROUTES_ADMIN = ('creer', 'rejoindre', 'code', 'appairer', 'adhesion/accepter', 'adhesion/refuser', 'retirer', 'desactiver', 'categories',
                'pause_cabinet', 'mission/attribuer')
ROUTES_AVOCAT = ('partager', 'resoudre', 'rapprocher', 'mission/decider', 'mission/transferer', 'mission/reprendre')
CLE = re.compile(r'^[A-Za-z0-9_-]{16,80}$')
NEUTRES = ('introuvable', 'poste_absent', 'dossier_local_absent', 'version_inconnue', 'mission_inconnue')
STATUTS = {'introuvable': '404 Not Found', 'route_inconnue': '404 Not Found', 'role_insuffisant': '403 Forbidden', 'origine_ou_csrf_refuse': '403 Forbidden',
           'sync_reservee_au_poste': '404 Not Found', 'cle_idempotence_deja_utilisee': '409 Conflict', 'espace_deja_present': '409 Conflict',
           'aucun_conflit': '409 Conflict', 'conflit_a_resoudre': '409 Conflict', 'routine_deja_surveillee': '409 Conflict'}
MESSAGES = {
    'introuvable': 'Élément introuvable.',
    'synchronisation_desactivee': 'La synchronisation des postes est désactivée sur ce poste (Paramètres › Confort et maintenance).',
    'espace_deja_present': 'Ce poste appartient déjà à un espace partagé.',
    'espace_absent': 'Créez d’abord un espace partagé, ou rejoignez le cabinet.',
    'personne_requise': 'Indiquez le nom de la personne qui utilise ce poste (pas le nom de l’ordinateur).',
    'autorite_requise': 'Seul le poste qui a créé l’espace partagé accepte ou retire un poste.',
    'poste_non_appaire': 'Ce poste n’est pas (ou plus) appairé : affichez un code et appairez-le de nouveau.',
    'personnes_autorisees_requises': 'Dossier réservé : cochez au moins une autre personne autorisée ; il ne sera partagé qu’avec elles.',
    'aucun_conflit': 'Ce conflit est déjà résolu : rechargez la page.',
    'conflit_a_resoudre': 'Résolvez d’abord le conflit de versions de ce dossier.',
    'reprise_forcee_interdite_effet_externe': 'Reprise forcée refusée : cette mission peut produire un effet externe (envoi, dépôt, paiement, '
                                              'signature). Rallumez le poste exécuteur pour un transfert confirmé.',
    'confirmation_requise': 'Cochez la confirmation : l’ancien poste ne peut pas garantir qu’il s’est arrêté.',
    'executeur_non_designe': 'Seul le poste exécuteur de cette mission peut la transférer.',
    'executeur_invalide': 'Choisissez un poste membre de l’espace.',
    'routine_deja_surveillee': 'Cette routine (ou cette boîte) est déjà surveillée par un poste.',
    'cle_idempotence_requise': 'Formulaire périmé : rechargez la page puis recommencez.',
    'cle_idempotence_deja_utilisee': 'Ce formulaire a déjà servi pour une autre demande : rechargez la page.',
    'role_insuffisant': 'Action réservée à l’administrateur (ou à un avocat) de ce poste.',
    'sync_reservee_au_poste': 'La synchronisation des postes n’existe que sur les postes de travail.',
}
ETATS = {'desactivee': 'Synchronisation désactivée', 'seul': 'Ce poste travaille seul', 'refusee': 'Connexion refusée',
         'conflit': 'Conflit à résoudre', 'en_cours': 'Synchronisation en cours', 'absent': 'Poste absent', 'partiel': 'Poste en version ancienne',
         'premier': 'Premier échange en attente', 'a_jour': 'À jour'}
REFUS_LIAISON = ('poste_non_appaire', 'signature_refusee', 'poste_refuse', 'poste_inattendu')
REFUS_SYNC = ('signature_refusee', 'espace_non_membre', 'poste_retire', 'origine_non_membre', 'relais_non_membre', 'acteur_refuse',
              'synchronisation_desactivee')
CHAMPS = {'titre': 'Intitulé', 'reference': 'Référence', 'client': 'Client', 'client_etat': 'État du client', 'nature': 'Nature', 'statut': 'Statut',
          'responsable': 'Responsable', 'juridiction': 'Juridiction', 'rg': 'Numéro RG', 'resume': 'Résumé', 'confidentialite': 'Confidentialité',
          'echeance': 'Échéance', 'etat': 'État', 'rappel': 'Rappel', 'priorite': 'Priorité', 'dossier': 'Dossier', 'origine': 'Origine',
          'debut': 'Début', 'fin': 'Fin', 'lieu': 'Lieu', 'description': 'Description', 'rappels': 'Rappels', 'journee': 'Journée entière',
          'nom': 'Nom', 'taille': 'Taille', 'sha256': 'Empreinte', 'supprime': 'Suppression', 'kind': 'Type', 'uid': 'Identifiant', 'zone': 'Fuseau'}
DOMAINES = {'messages': 'messages', 'dossiers': 'dossiers', 'taches': 'tâches', 'agenda': 'agenda', 'documents': 'documents', 'missions': 'missions'}

SCHEMA = '''CREATE TABLE IF NOT EXISTS sync56108_recus(compte TEXT NOT NULL, route TEXT NOT NULL, cle TEXT NOT NULL, empreinte TEXT NOT NULL,
  reponse TEXT NOT NULL, cree_le TEXT NOT NULL, PRIMARY KEY(compte, route, cle))'''


def ensure_schema(desk):
    desk.db.execute(SCHEMA)
    desk.db.commit()


def allowed(role, method, api):
    route = str(api or '').split('?', 1)[0]
    route = route[len(PREFIXE):] if route.startswith(PREFIXE) else ''
    if role not in ROLES:
        return False
    if method == 'GET':
        return route in ROUTES_GET
    if method != 'POST':
        return False
    return (role == 'administrateur' and route in ROUTES_ADMIN + ROUTES_AVOCAT) or (role == 'avocat' and route in ROUTES_AVOCAT)


def _out(value, status='200 OK'):
    return {'status': status, 'kind': 'application/json; charset=utf-8', 'body': json.dumps(value, ensure_ascii=False, default=str),
            'headers': [('Cache-Control', 'private, no-store')]}


def message(code):
    base = str(code or '').split(':')[0]
    if base in MESSAGES:
        return MESSAGES[base]
    from .liaison_web56103 import message as m103
    return m103(code)


def api(env, desk, auth, prefix, name, args, method):
    try:
        return _api(env, desk, auth, prefix, name, args, method)
    except Stop as ex:
        code = str(ex).split(':')[0]
        if code in NEUTRES:
            code = 'introuvable'
        return _out({'error': code, 'message': message(str(ex) if code != 'introuvable' else code)}, STATUTS.get(code, '400 Bad Request'))


def _api(env, desk, auth, prefix, name, args, method):
    from .web567 import actor
    if not (auth or {}).get('poste'):
        raise Stop('sync_reservee_au_poste')
    route = name[len(PREFIXE):].split('?', 1)[0] if name.startswith(PREFIXE) else ''
    me, role = actor(env)
    if not allowed(role, method, PREFIXE + route):
        raise Stop('route_inconnue' if route not in ROUTES_GET + ROUTES_ADMIN + ROUTES_AVOCAT else 'role_insuffisant')
    if method == 'GET':
        return _out(etat_public(desk, role))
    from .web440 import check_post
    data = check_post(env, auth)
    cle = data.get('cle')
    if not CLE.fullmatch(str(cle or '')):
        raise Stop('cle_idempotence_requise')
    empreinte = hashlib.sha256(json.dumps({k: v for k, v in data.items() if k != 'cle'}, sort_keys=True, default=str).encode()).hexdigest()
    r = desk.db.execute('SELECT empreinte, reponse FROM sync56108_recus WHERE compte=? AND route=? AND cle=?', (me, route, cle)).fetchone()
    if r:
        if r['empreinte'] != empreinte:
            raise Stop('cle_idempotence_deja_utilisee')
        return _out(json.loads(r['reponse']))
    out = _effet(desk, auth, env, me, role, route, data)
    desk.db.execute('INSERT OR IGNORE INTO sync56108_recus VALUES(?,?,?,?,?,?)', (me, route, cle, empreinte, json.dumps(out, ensure_ascii=False, default=str),
                                                                                  S._maintenant()))
    desk.db.commit()
    return _out(out)


def _nom_personne(desk, auth, me):
    from .membres5650 import directory
    return (directory(desk, auth).get(me) or {}).get('name') or me


def _dossier_visible(desk, env, dossier_local):
    from .acces5697 import acteur_de, peut_lire_dossier
    if not dossier_local or not peut_lire_dossier(desk, acteur_de(env), dossier_local):
        raise Stop('introuvable')


def _objet_visible(desk, env, objet):
    """Un objet synchronisé n'est manipulable que si son dossier local est visible de la personne (même 404 sinon)."""
    o = desk.db.execute('SELECT * FROM sync56108_objets WHERE objet=?', (str(objet or ''),)).fetchone()
    if not o:
        raise Stop('introuvable')
    if o['genre'] == 'dossier':
        if o['local_id']:
            _dossier_visible(desk, env, o['local_id'])
        return o
    etat = json.loads(o['etat']) if o['etat'] else {}
    if etat.get('dossier'):
        from .sync56108_objets import local_de
        _dossier_visible(desk, env, local_de(desk, etat['dossier']))
    return o


def _personnes(data):
    out = [k[2:] for k, v in data.items() if k.startswith('p_') and v is True]
    if isinstance(data.get('personnes'), list):
        out += [str(x) for x in data['personnes']]
    return [p for p in out if S.PERS_RE.fullmatch(p)]


def _effet(desk, auth, env, me, role, route, data):
    from . import liaison56103 as L
    if route == 'desactiver':
        out = S.desactiver(desk, data.get('desactive') in (True, '1', 'true', 'on'), me)
        return {**out, 'message': ('Synchronisation désactivée : l’écoute est fermée, plus rien ne part ni n’arrive.' if out['desactive']
                                   else 'Synchronisation réactivée.')}
    if S.desactivee(desk):
        raise Stop('synchronisation_desactivee')
    if route == 'creer':
        out = S.creer_espace(desk, data.get('nom'), data.get('personne') or _nom_personne(desk, auth, me), me, me)
        return {**out, 'message': 'Espace partagé « %s » créé. Affichez un code pour relier un autre poste.' % out['nom']}
    if route == 'rejoindre':
        S.ouvrir_rejoindre(desk, data.get('personne') or _nom_personne(desk, auth, me), me, str(data.get('poste') or ''), me)
        return {'message': 'Ce poste peut rejoindre le cabinet pendant 10 minutes : choisissez le poste du cabinet (ou saisissez son adresse) '
                           'et tapez le code affiché sur lui.'}
    if route == 'code':
        out = L.nouveau_code(desk)
        desk.audit('sync56108_code_affiche', {'par': me})
        return {'code': out['code'], 'message': 'Code d’appairage : %s (10 minutes, 5 essais). Saisissez-le sur l’autre poste ; vérifiez que '
                                                'l’empreinte affichée est la même des deux côtés.' % out['code']}
    if route == 'appairer':
        out = L.appairer(desk, data.get('adresse'), data.get('port') or L.PORT_TCP, data.get('code'))
        return {**out, 'message': 'Poste « %s » appairé (empreinte %s). La demande d’adhésion part au prochain échange.' % (
            out['nom'], L.empreinte_lisible(out['id'])[:19])}
    if route in ('adhesion/accepter', 'adhesion/refuser'):
        out = S.accepter_adhesion(desk, str(data.get('poste') or ''), me, refuser=route == 'adhesion/refuser')
        return {**out, 'message': ('Poste « %s » accepté dans l’espace.' if out['etat'] == 'acceptee' else 'Demande de « %s » refusée.') % out['nom']}
    if route == 'retirer':
        S.retirer_poste(desk, str(data.get('poste') or ''), me)
        return {'message': 'Poste retiré : il ne reçoit plus rien. Ce qu’il a déjà reçu reste sur lui (rien ne peut l’effacer à distance).'}
    if route == 'categories':
        esp = S.espace_cabinet(desk)
        if esp is None or esp['autorite'] != S._ident(desk).id:
            raise Stop('autorite_requise')
        cats = {k: data.get(k) is True for k in ('taches_sans_dossier', 'agenda_sans_dossier')}
        membres = S._membres_actuels(desk, esp['id'])
        with S._tx(desk):
            esp2 = dict(esp)
            esp2['categories'] = json.dumps(cats)
            op = S.emettre(desk, esp['id'], 'espace.membres', esp['id'], S._charge_membres(desk, esp2, membres, esp['version'] + 1))
            S._appliquer_membres(desk, op)
        return {'message': 'Catégories partagées enregistrées.'}
    if route == 'pause_cabinet':
        from .sync56108_missions import pause_cabinet
        pause_cabinet(desk, data.get('pause') is True, str(data.get('motif') or ''))
        return {'message': 'Pause du cabinet %s sur tous les postes (missions partagées seulement ; la synchronisation continue).'
                           % ('posée' if data.get('pause') is True else 'levée')}
    if route == 'partager':
        dossier = str(data.get('dossier') or '')
        _dossier_visible(desk, env, dossier)
        out = S.partager_dossier(desk, dossier, _personnes(data), me)
        return {**out, 'message': 'Dossier partagé avec ses tâches et son agenda.' if not out.get('deja') else 'Dossier déjà partagé.'}
    if route == 'resoudre':
        o = _objet_visible(desk, env, data.get('objet'))
        S.resoudre(desk, o['objet'], str(data.get('choix') or ''), me)
        return {'message': 'Version retenue : elle référence les deux versions et part vers les autres postes.'}
    if route == 'rapprocher':
        from .sync56108_objets import rapprocher
        o = _objet_visible(desk, env, data.get('objet'))
        if data.get('cible'):
            _dossier_visible(desk, env, str(data['cible']))
        out = rapprocher(desk, o['objet'], str(data.get('decision') or ''), str(data.get('cible') or ''))
        return {**out, 'message': ('Dossiers associés : comparez maintenant les deux versions.' if out['decision'] == 'associer'
                                   else 'Dossier gardé séparé (référence locale %s).' % out.get('reference_locale', ''))}
    from . import sync56108_missions as M
    if route == 'mission/attribuer':
        cles = dict(M.cles_du_poste(desk))
        cle_ = str(data.get('routine') or '')
        if cle_ not in cles:
            raise Stop('introuvable')
        M.attribuer(desk, cles[cle_], str(data.get('executeur') or ''), routine=cle_, effet_externe=cle_.startswith('boite:'))
        return {'message': 'Exécuteur unique désigné : les autres postes n’exécutent plus « %s » (ils en voient l’état).' % cles[cle_]}
    if route == 'mission/decider':
        M.decider(desk, data.get('objet'), str(data.get('etat') or ''), str(data.get('detail') or ''))
        return {'message': 'Décision enregistrée et transmise aux postes.'}
    if route == 'mission/transferer':
        M.transferer(desk, data.get('objet'), str(data.get('vers') or ''), str(data.get('motif') or ''))
        return {'message': 'Abandon confirmé par ce poste ; la mission passe au poste choisi.'}
    if route == 'mission/reprendre':
        M.reprendre_force(desk, data.get('objet'), data.get('confirme') is True, str(data.get('motif') or ''))
        return {'message': 'Reprise explicite enregistrée sur ce poste (journalisée).'}
    raise Stop('route_inconnue')


# ------------------------------------------------------------------------------------------------ indicateur
def _postes_detail(desk, esp, moi):
    from .liaison56103 import presence_de
    out = []
    for m in desk.db.execute('SELECT * FROM sync56108_membres WHERE espace=? AND retire=0 AND poste<>? ORDER BY nom', (esp['id'], moi)).fetchall():
        lp = desk.db.execute('SELECT * FROM liaison56103_postes WHERE id=?', (m['poste'],)).fetchone()
        cap = desk.db.execute('SELECT * FROM sync56108_capacites WHERE poste=?', (m['poste'],)).fetchone()
        ech = desk.db.execute('SELECT * FROM sync56108_echanges WHERE poste=?', (m['poste'],)).fetchone()
        domaines = json.loads(cap['domaines']) if cap else []
        out.append({'id': m['poste'], 'nom': m['nom'] or (lp['nom'] if lp else 'Poste'), 'personnes': [p['nom'] for p in json.loads(m['personnes'] or '[]')],
                    'presence': presence_de(dict(lp)) if lp else 'hors_ligne', 'appaire': bool(lp and not lp['retire'] and lp['cle_signature'] is not None),
                    'erreur_liaison': (lp['erreur'] if lp else '') or '', 'domaines': domaines, 'version': cap['version'] if cap else '',
                    'dernier_echange': ech['fin'] if ech else '', 'refus': ech['refus'] if ech else '', 'en_cours': bool(ech and ech['en_cours']),
                    'attente': S.en_attente(desk, m['poste'], moi), 'recues': ech['recues'] if ech else 0, 'envoyees': ech['envoyees'] if ech else 0})
    return out


def indicateur(desk):
    """Un seul état lisible : jamais « À jour » si seuls des messages ont été échangés, si un pair est ancien, ou avant un premier échange
    réussi des données métier avec chaque poste."""
    if S.desactivee(desk):
        return {'etat': 'desactivee', 'libelle': ETATS['desactivee'], 'postes': []}
    esp = S.espace_cabinet(desk)
    if esp is None:
        r = S.rejoindre_en_cours(desk)
        return {'etat': 'seul', 'libelle': 'Rejoindre le cabinet : en attente d’acceptation' if r else ETATS['seul'], 'postes': []}
    moi = S._ident(desk).id
    postes = _postes_detail(desk, esp, moi)
    conflits = desk.db.execute("SELECT COUNT(*) FROM sync56108_objets WHERE conflit=1 OR projection='a_rapprocher'").fetchone()[0]
    telechargements = desk.db.execute("SELECT COUNT(*), COALESCE(SUM(taille),0) FROM sync56108_documents WHERE etat<>'disponible' AND supprime=0").fetchone() \
        if S._table(desk.db, 'sync56108_documents') else (0, 0)
    attente = sum(p['attente'] for p in postes)
    base = {'postes': postes, 'conflits': conflits, 'attente': attente, 'telechargements': telechargements[0], 'octets_restants': telechargements[1]}
    if not postes:
        return {**base, 'etat': 'seul', 'libelle': 'Espace créé : aucun autre poste n’a encore rejoint'}
    if any(p['erreur_liaison'] in REFUS_LIAISON or p['refus'] in REFUS_SYNC or not p['appaire'] for p in postes):
        return {**base, 'etat': 'refusee', 'libelle': 'Connexion refusée : vérifier l’appairage ou les droits'}
    if conflits:
        return {**base, 'etat': 'conflit', 'libelle': 'Conflit à résoudre (%d)' % conflits}
    absents = [p for p in postes if p['presence'] == 'hors_ligne' and p['attente']]
    if any(p['en_cours'] for p in postes) or ((attente or telechargements[0]) and len(absents) < len(postes)):
        return {**base, 'etat': 'en_cours', 'libelle': 'Synchronisation en cours : %d changement(s), %d fichier(s) à recevoir' % (attente, telechargements[0])}
    if absents:
        return {**base, 'etat': 'absent', 'libelle': 'Poste absent : %d changement(s) en attente' % sum(p['attente'] for p in absents)}
    if any('dossiers' not in p['domaines'] for p in postes):
        return {**base, 'etat': 'partiel', 'libelle': 'Poste en version ancienne : messages seulement, données non synchronisées'}
    if any(not p['dernier_echange'] or p['refus'] for p in postes):
        return {**base, 'etat': 'premier', 'libelle': ETATS['premier']}
    return {**base, 'etat': 'a_jour', 'libelle': ETATS['a_jour']}


def etat_public(desk, role):
    """Lecture seule (aucune écriture, aucun secret) : indicateur, personnes, adhésions en attente pour l'administrateur."""
    out = indicateur(desk)
    esp = S.espace_cabinet(desk)
    out['espace'] = {'nom': esp['nom'], 'autorite': esp['autorite']} if esp else None
    if role == 'administrateur' and esp is not None:
        out['adhesions'] = [{'poste': r['poste'], 'nom_poste': r['nom_poste'], 'personne': r['personne_nom']}
                            for r in desk.db.execute("SELECT * FROM sync56108_adhesions WHERE etat='en_attente'")]
    return out


def indicateur_html(desk, prefix=''):
    """Pastille de l'en-tête général (rafraîchie par ``v56108-sync.js``) ; rien si le poste travaille seul ou si la synchronisation est
    désactivée."""
    try:
        i = indicateur(desk)
    except Exception:   # noqa: BLE001 — l'en-tête s'affiche toujours
        return ''
    if i['etat'] in ('seul', 'desactivee'):
        return ''
    return ('<a class="s108-pastille s108-entete s108-%s" data-s108-indicateur href="%s/parametres#sync56108" role="status" title="Synchronisation des postes">'
            '%s</a><script defer src="%s"></script>') % (e(i['etat']), e(prefix), e(i['libelle']), e(prefix + '/static/v56108-sync.js', quote=True))


class _Lecture:
    """Base de l'état ouverte en lecture seule (``mode=ro``) : l'en-tête n'écrit jamais et n'ouvre pas de bureau complet."""

    def __init__(self, cfg, db):
        self.c, self.db = cfg, db

    def settings(self, key, default=None):
        row = self.db.execute('SELECT value FROM settings WHERE key=?', (key,)).fetchone()
        return json.loads(row[0]) if row else default


def indicateur_entete(cfg, prefix=''):
    """Point de montage de ``shell501.topbar_html`` : rien pour un poste seul (aucun espace), lecture seule, jamais bloquant."""
    import sqlite3
    from pathlib import Path
    try:
        base = Path((cfg or {}).get('state_dir', '')) / 'desk.sqlite3'
        if not base.is_file():
            return ''
        db = sqlite3.connect('file:%s?mode=ro' % base.as_posix(), uri=True, timeout=1)
        db.row_factory = sqlite3.Row
        try:
            if not S._table(db, 'sync56108_espaces') or not db.execute("SELECT 1 FROM sync56108_espaces WHERE etat='actif'").fetchone():
                return ''
            return indicateur_html(_Lecture(cfg, db), prefix)
        finally:
            db.close()
    except Exception:   # noqa: BLE001 — l'en-tête s'affiche toujours
        return ''


# ------------------------------------------------------------------------------------------------ Paramètres
def _cle():
    return '<input type="hidden" name="cle" value="%s">' % secrets.token_urlsafe(18)


def _form(api, champs, bouton, classe='', confirm='', reload=True):
    return ('<form class="m5-form s108-ligne" data-api="m56108/sync/%s"%s%s>%s%s<button class="ax-btn%s" type="submit">%s</button>'
            '<p role="status" class="m5-status"></p></form>') % (e(api, quote=True), ' data-reload' if reload else '',
                                                                 (' data-confirm="%s"' % e(confirm, quote=True)) if confirm else '', _cle(), champs,
                                                                 (' ' + classe) if classe else '', e(bouton))


def _cache(nom, valeur):
    return '<input type="hidden" name="%s" value="%s">' % (e(nom, quote=True), e(str(valeur), quote=True))


def _heure(iso):
    from .liaison_web56103 import _heure as h
    return h(iso) if iso else ''


def section_html(desk, auth, prefix, env):
    """Rubrique « Synchronisation des postes » (poste de travail seulement). Aucune écriture pendant l'affichage."""
    if not (auth or {}).get('poste'):
        return ''
    from .web567 import actor
    me, role = actor(env)
    admin, avocat = role == 'administrateur', role in ('administrateur', 'avocat')
    intro = ('<p>Relie les postes AxiorHub du cabinet (Windows 11, Ubuntu 24.04) sur le réseau local ou le Wi-Fi, <strong>sans serveur</strong> ni '
             'Internet : dossiers choisis, tâches, agenda, documents et état des missions. Chaque poste garde sa propre base ; rien n’est partagé sans '
             'votre choix, les dossiers réservés seulement avec les personnes autorisées ; mots de passe, clés et sessions ne passent jamais.</p>')
    titre = '<h2>Synchronisation des postes <span class="s108-nouveau">nouveau</span></h2>'
    i = indicateur(desk)
    if i['etat'] == 'desactivee':
        corps = '<p class="s108-etat">Désactivée sur ce poste : aucun port n’écoute pour elle.</p>'
        if admin:
            corps += _form('desactiver', '<input type="hidden" name="desactive" value="">', 'Réactiver')
        return '<section class="ax-card s108" id="sync56108">%s%s%s</section>' % (titre, intro, corps)
    corps = [_pare_feu()]
    esp = S.espace_cabinet(desk)
    if esp is None:
        rej = S.rejoindre_en_cours(desk)
        if admin and not rej:
            corps.append('<h3>Relier ce poste</h3><p>Sur le premier poste : « Créer un espace partagé ». Sur chacun des autres : « Rejoindre le cabinet ». '
                         'Tant que vous ne le faites pas, ce poste travaille seul, exactement comme avant, et rien n’écoute sur le réseau.</p>')
            corps.append(_form('creer', '<label>Nom du cabinet<input name="nom" maxlength="120" required placeholder="Cabinet"></label>'
                               '<label>Votre nom (la personne, pas l’ordinateur)<input name="personne" maxlength="120" required></label>',
                               'Créer un espace partagé'))
            corps.append(_form('rejoindre', '<label>Votre nom (la personne, pas l’ordinateur)<input name="personne" maxlength="120" required></label>',
                               'Rejoindre le cabinet', 'ghost'))
        elif rej:
            corps.append('<p class="s108-etat" role="status">Rejoindre le cabinet : choisissez le poste qui a créé l’espace et tapez le code '
                         'qu’il affiche. Ensuite, son administrateur accepte votre poste.</p>')
            corps.append(_appairage(desk, admin))
    else:
        corps.append(_indicateur_detail(desk, i))
        if admin:
            corps.append(_adhesions(desk, esp))
            corps.append('<h3>Relier un autre poste</h3>')
            corps.append(_appairage(desk, admin))
        if avocat:
            corps.append(_a_partager(desk, env, esp))
        corps.append(_conflits(desk))
        corps.append(_missions(desk))
        corps.append(_executeurs(desk, admin))
        if admin:
            corps.append(_form('desactiver', '<input type="hidden" name="desactive" value="1">', 'Désactiver la synchronisation sur ce poste',
                               'ghost', 'Désactiver ? L’écoute se ferme ; les autres postes verront ce poste absent.'))
    corps.append('<script defer src="%s"></script>' % e(prefix + '/static/v56108-sync.js', quote=True))
    return '<section class="ax-card s108" id="sync56108">%s%s%s</section>' % (titre, intro, ''.join(corps))


def _pare_feu():
    from .liaison56103 import PORT_TCP, PORT_UDP
    return ('<details class="s108-aide"><summary>Pare-feu et arrière-plan</summary><p>Quand ce poste se met à l’écoute (après la création ou '
            'l’adhésion à un espace et l’appairage d’un poste), Windows peut demander d’autoriser AxiorHub : choisissez <strong>Réseaux privés</strong> '
            'seulement. AxiorHub ne modifie jamais le pare-feu lui-même. Sur Ubuntu, si le pare-feu est actif, l’administrateur ouvre le port TCP %d '
            '(liaison) et UDP %d (découverte) au seul réseau local. Aucun port n’est ouvert vers Internet. Si le Wi-Fi isole les appareils, la '
            'découverte échoue : saisissez l’adresse locale de l’autre poste. Fenêtre fermée, la synchronisation continue tant que le poste '
            'AxiorHub tourne (zone de notification) ; elle n’appelle jamais l’IA.</p></details>') % (PORT_TCP, PORT_UDP)


def _appairage(desk, admin):
    from . import liaison56103 as L
    if not admin:
        return ''
    out = [_form('code', '', 'Afficher un code')]
    try:
        ident = L.identite(desk.c['state_dir'], creer=False).id
        out.append('<p><span>Empreinte de ce poste, à comparer avec celle affichée sur l’autre poste :</span> <code>%s</code></p>' % e(L.empreinte_lisible(ident)[:19]))
    except Stop:
        pass
    code = L.code_actif(desk.c['state_dir'])
    if code:
        out.append('<p class="s108-code" role="status"><span>Code à saisir sur l’autre poste :</span> <strong>%s-%s</strong> <span>(%d essai(s))</span></p>' % (
            e(code['code'][:4]), e(code['code'][4:]), int(code['essais'])))
    for d in [d for d in L.decouverts(desk) if not d['appaire']]:
        out.append(_form('appairer', _cache('adresse', d['adresse']) + _cache('port', d['port']) +
                         '<span><strong>%s</strong> <code>%s</code></span><label>Code<input name="code" inputmode="numeric" autocomplete="off" required '
                         'maxlength="9" placeholder="1234-5678"></label>' % (e(d['nom']), e(L.empreinte_lisible(d['id'])[:19])), 'Appairer'))
    out.append('<h4>Adresse saisie à la main</h4><p class="ax-muted">Si le Wi-Fi bloque la découverte : relevez l’adresse locale affichée sur l’autre '
               'poste (10…, 172.16 à 172.31…, 192.168…).</p>' + _form(
                   'appairer', '<label>Adresse locale de l’autre poste<input name="adresse" required maxlength="64"></label><label>Port<input name="port" '
                               'inputmode="numeric" value="%d" maxlength="5"></label><label>Code<input name="code" inputmode="numeric" autocomplete="off" required '
                               'maxlength="9" placeholder="1234-5678"></label>' % L.PORT_TCP, 'Appairer'))
    return ''.join(out)


def _indicateur_detail(desk, i):
    lignes = []
    for p in i['postes']:
        cats = ' · '.join(DOMAINES.get(d, d) for d in p['domaines']) or 'inconnues (pas encore d’échange)'
        lignes.append('<tr><td><strong>%s</strong><br><span class="ax-muted">%s</span></td><td>%s</td><td>%s</td><td>%s</td><td>%s</td></tr>' % (
            e(p['nom']), e(', '.join(p['personnes'])), e({'en_ligne': 'en ligne', 'absent': 'absent', 'hors_ligne': 'hors ligne'}.get(p['presence'], '')),
            e(_heure(p['dernier_echange']) or 'jamais'), e(('%d en attente' % p['attente']) if p['attente'] else 'rien en attente'), e(cats)))
    table = ('<table class="ax-table s108-table"><thead><tr><th>Poste</th><th>Présence</th><th>Dernier échange</th><th>Changements</th>'
             '<th>Catégories prises en charge</th></tr></thead><tbody>%s</tbody></table>' % ''.join(lignes)) if lignes else ''
    return '<p class="s108-pastille s108-%s" data-s108-indicateur role="status">%s</p>%s' % (e(i['etat']), e(i['libelle']), table)


def _adhesions(desk, esp):
    if esp['autorite'] != S._ident(desk).id:
        return ''
    from .liaison56103 import empreinte_lisible
    items = []
    for r in desk.db.execute("SELECT * FROM sync56108_adhesions WHERE etat='en_attente'").fetchall():
        items.append('<li><strong>%s</strong> · <span>%s</span> · <code>%s</code><p>Demande à rejoindre l’espace.</p>%s%s</li>' % (
            e(r['personne_nom']), e(r['nom_poste']), e(empreinte_lisible(r['poste'])[:19]),
            _form('adhesion/accepter', _cache('poste', r['poste']), 'Accepter'), _form('adhesion/refuser', _cache('poste', r['poste']), 'Refuser', 'ghost')))
    return ('<h3>Demandes d’adhésion</h3><ul class="s108-liste">%s</ul>' % ''.join(items)) if items else ''


def _a_partager(desk, env, esp):
    from .acces5697 import acteur_de, dossiers_autorises
    if not S._table(desk.db, 'dossiers5697'):
        return ''
    try:
        clause, params = dossiers_autorises(desk, acteur_de(env)).sql('d.id')
    except Stop:
        return ''
    rows = desk.db.execute("SELECT d.id, d.ref, d.titre, d.confidentialite FROM dossiers5697 d WHERE %s AND d.statut='actif' AND d.id NOT IN "
                           "(SELECT dossier FROM sync56108_partages) AND d.id NOT IN (SELECT local_id FROM sync56108_objets WHERE genre='dossier' AND local_id IS NOT NULL) "
                           "ORDER BY d.modifie_le DESC LIMIT 50" % clause, params).fetchall()   # un dossier reçu est déjà partagé
    personnes = desk.db.execute('SELECT gid, nom FROM sync56108_personnes WHERE gid<>? ORDER BY nom', ((S.personne_locale(desk) or {}).get('gid', ''),)).fetchall()
    items = []
    for r in rows:
        champs = _cache('dossier', r['id'])
        if r['confidentialite'] == 'reserve':
            champs += '<fieldset class="s108-personnes"><legend>Dossier réservé : personnes autorisées</legend>%s</fieldset>' % ''.join(
                '<label class="m50-check"><input type="checkbox" name="p_%s"> %s</label>' % (e(p['gid'], quote=True), e(p['nom'])) for p in personnes)
        items.append('<li><strong>%s</strong> · <span>%s</span>%s</li>' % (e(r['ref']), e(r['titre']), _form('partager', champs, 'Partager', 'ghost')))
    return ('<h3>Dossiers à partager</h3><p>Rien n’est partagé sans votre choix. Un dossier partagé emporte ses tâches et son agenda.</p>'
            '<ul class="s108-liste">%s</ul>' % ''.join(items)) if items else ''


def _conflits(desk):
    from .sync56108_objets import doublons
    items = []
    for o in desk.db.execute("SELECT * FROM sync56108_objets WHERE conflit=1 OR projection='a_rapprocher' ORDER BY maj_le DESC LIMIT 30").fetchall():
        if o['projection'] == 'a_rapprocher' and o['genre'] == 'dossier':
            try:
                d = doublons(desk, o['objet'])
            except Stop:
                continue
            locaux = ''.join(_form('rapprocher', _cache('objet', o['objet']) + _cache('decision', 'associer') + _cache('cible', l['id']),
                                   'Associer à « %s » (%s)' % (l['titre'], l['ref']), 'ghost',
                                   'Associer ? Les deux états deviendront deux versions à comparer ; rien n’est écrasé.') for l in d['locaux'])
            items.append('<li><p>Dossier reçu « %s » (%s) : la référence existe déjà sur ce poste.</p>%s%s</li>' % (
                e(d['recu'].get('titre') or ''), e(d['recu'].get('reference') or ''), locaux,
                _form('rapprocher', _cache('objet', o['objet']) + _cache('decision', 'separer'), 'Garder séparé', 'ghost')))
            continue
        versions = S.versions(desk, o['objet'])
        if len(versions) < 2:
            continue
        cles = sorted({k for v in versions for k in v['charge']} - {'parties', 'blocs'})
        diff = [k for k in cles if len({json.dumps(v['charge'].get(k), sort_keys=True) for v in versions}) > 1]
        colonnes = ''.join('<th><span>%s</span><br><span>%s</span> <span>%s</span></th>' % (
            e('Suppression' if v['charge'].get('supprime') else 'Version'), e(v['acteur'] or 'Poste'), e(_heure(v['quand']))) for v in versions)
        lignes = ''.join('<tr><th>%s</th>%s</tr>' % (e(CHAMPS.get(k, k)), ''.join('<td>%s</td>' % e(str(v['charge'].get(k, ''))[:200]) for v in versions))
                         for k in diff)
        boutons = ''.join('<td>%s</td>' % _form('resoudre', _cache('objet', o['objet']) + _cache('choix', v['id']), 'Retenir cette version')
                          for v in versions)
        items.append('<li><strong>%s</strong><p>Versions concurrentes : aucune n’est choisie à votre place.</p><table class="ax-table s108-table"><thead><tr>'
                     '<th></th>%s</tr></thead><tbody>%s<tr><th></th>%s</tr></tbody></table></li>' % (
                         e({'dossier': 'Dossier', 'tache': 'Tâche', 'evenement': 'Événement', 'document': 'Document'}.get(o['genre'], o['genre'])),
                         colonnes, lignes, boutons))
    return ('<h3>Conflits à résoudre</h3><ul class="s108-liste">%s</ul>' % ''.join(items)) if items else ''


def _executeurs(desk, admin):
    """Routines et surveillances de ce poste : désigner l'unique poste exécuteur (les autres n'exécutent plus, voient l'état)."""
    from .sync56108_missions import cles_du_poste
    esp = S.espace_cabinet(desk)
    if not admin or esp is None:
        return ''
    deja = {r[0] for r in desk.db.execute("SELECT routine FROM sync56108_missions WHERE etat NOT IN ('terminee','annulee')")}
    postes = desk.db.execute('SELECT poste, nom FROM sync56108_membres WHERE espace=? AND retire=0 ORDER BY nom', (esp['id'],)).fetchall()
    options = ''.join('<option value="%s">%s</option>' % (e(p['poste'], quote=True), e(p['nom'] or 'Poste')) for p in postes)
    items = ''.join('<li>%s %s</li>' % (e(lib), _form('mission/attribuer', _cache('routine', cle_) + '<label>Poste exécuteur<select name="executeur">%s</select></label>'
                                                          % options, 'Désigner', 'ghost')) for cle_, lib in cles_du_poste(desk) if cle_ not in deja)
    return ('<h3>Une seule exécution</h3><p>Pour une boîte ou une routine présente sur plusieurs postes, désignez le seul poste qui l’exécute ; '
            'les autres ne lancent ni IA ni effet externe et affichent « Attend le poste … » s’il est absent.</p><ul class="s108-liste">%s</ul>' % items) if items else ''


def _missions(desk):
    from .sync56108_missions import lister
    ms = lister(desk)
    if not ms:
        return ''
    items = ''.join('<li><strong>%s</strong> · <span>%s</span>%s</li>' % (
        e(m['titre']), e(m['lisible']), _form('mission/decider', _cache('objet', m['objet']) + _cache('etat', 'pause'), 'Mettre en pause', 'ghost')
        if m['etat'] in ('en_attente', 'en_cours') else '') for m in ms)
    return ('<h3>Missions partagées</h3><p>Chaque mission n’est exécutée que par un poste ; les autres voient son état et son résultat.</p>'
            '<ul class="s108-liste">%s</ul>' % items)
