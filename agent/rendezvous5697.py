"""5.6.97 (lot agenda, § 14.2 et § 14.3) : visio Nextcloud Talk et ouverture de Speakr depuis un rendez-vous de l'agenda.

Talk (§ 14.2) — commande métier « Agenda », distincte de la préparation E11 de ``talk568`` (dont les gardes ne sont pas assouplies) :
- réglage du cabinet « Salons Talk depuis l'agenda » DÉSACTIVÉ par défaut ; Talk indisponible (non configuré, désactivé, refusé) : le
  rendez-vous est enregistré quand même, sans visio, avec le motif ;
- la demande est inscrite dans la transaction de création (``agenda5697.creer``) ; le salon est créé APRÈS le commit, par le client OCS de
  ``talk568`` : conversation de groupe privée (POST /room, ``roomType=2``), nom neutre sans client ni dossier, puis RELUE (type, visibilité,
  nom, participants attendus) avant d'afficher « Lien prêt » ; jusque-là « Lien en préparation » ;
- résultat inconnu (délai, coupure) : « Confirmation inconnue » ; la reprise RÉCONCILIE (recherche du nom neutre) avant de recréer ;
- participants : seulement après un choix explicite, comptes du cabinet ayant accès au dossier ; l'ajout peut déclencher une notification
  Talk (dit à l'écran). « Lien prêt » et « Accès donné » sont deux états distincts ; aucune invitation calendrier ni courriel n'est envoyée ;
- le lien n'est rendu qu'aux personnes autorisées (droits du dossier) ; il n'entre dans aucun export ni agenda partagé ; déplacer le
  rendez-vous garde le salon.

Speakr (§ 14.3) — seulement une adresse et un lien :
- Paramètres › Connexions › Speakr : adresse du cabinet (administrateur) et adresse personnelle (si les droits le permettent), HTTPS (HTTP
  seulement en boucle locale d'essai), sans identifiant, mot de passe, paramètre ni fragment ; aucun compte ni jeton Speakr n'est saisi ;
- « Ouvrir Speakr » ouvre l'adresse dans un nouvel onglet, sans référent (``rel="noopener noreferrer"``) ; ni titre ni dossier dans l'URL ;
- AxiorHub ne démarre AUCUNE capture ni transfert audio ; la pause de l'IA d'AxiorHub ne pilote pas Speakr.
"""
from html import escape as e
import json
import re
from urllib.parse import quote, urlencode, urlsplit

from .common import Stop

TALK = 'agenda5697:talk'                 # réglage du cabinet : {'actif': bool}
SPEAKR = 'agenda5697:speakr:'            # + 'cabinet' ou le compte : {'url': str}
ETATS = {'demande': 'Lien en préparation', 'creation': 'Lien en préparation', 'incertain': 'Confirmation inconnue : vérification avant toute relance',
         'pret': 'Lien prêt', 'refuse': 'Talk indisponible', 'indisponible': 'Talk indisponible'}
SCHEMA = '''
CREATE TABLE IF NOT EXISTS agenda_salons5697(event_id TEXT PRIMARY KEY, proprietaire TEXT NOT NULL, dossier TEXT NOT NULL DEFAULT '', nom TEXT NOT NULL,
  etat TEXT NOT NULL, jeton TEXT NOT NULL DEFAULT '', participants TEXT NOT NULL DEFAULT '[]', preuve TEXT NOT NULL DEFAULT '{}', motif TEXT NOT NULL DEFAULT '',
  tentatives INTEGER NOT NULL DEFAULT 0, updated TEXT NOT NULL);
'''


def schema(desk):
    desk.db.executescript(SCHEMA)
    desk.db.commit()


# ------------------------------------------------------------------------------------------------ Talk : disponibilité
def talk_reglage(desk):
    value = desk.settings(TALK, {}) or {}
    return {'actif': bool(value.get('actif'))}


def regler_talk(desk, acteur, actif):
    if not isinstance(actif, bool):
        raise Stop('valeur_invalide')
    desk.setting(TALK, {'actif': actif})
    desk.audit('agenda5697_talk_reglage', {'actif': actif, 'par': str(acteur)[:80]})
    return talk_reglage(desk)


def talk_disponible(desk):
    """(disponible, motif utile). Aucun appel réseau : la capacité de l'instance est vérifiée au moment de créer le salon."""
    if not talk_reglage(desk)['actif']:
        return False, 'Les salons Talk depuis l’agenda ne sont pas activés (Paramètres › Connexions › Agenda : visio et Speakr).'
    nc = desk.c.get('nextcloud') or {}
    if not str(nc.get('url') or '').startswith('https://') or not nc.get('username') or not (nc.get('password_file') or nc.get('app_password_file')):
        return False, 'Nextcloud n’est pas configuré sur ce serveur (adresse HTTPS, compte et secret).'
    return True, ''


class SalonsAgenda:
    """Client OCS de ``talk568`` (même transport, mêmes en-têtes), étendu des seules opérations de la commande Agenda."""

    def __init__(self, cfg, talk=None):
        from .talk568 import Talk
        self.talk = talk or Talk(cfg)
        self.cfg = cfg

    def capacite(self):
        http = self.talk.http
        raw = http.request('GET', http.base + '/ocs/v2.php/cloud/capabilities?format=json', None, {'OCS-APIRequest': 'true', 'Accept': 'application/json'}, 2_000_000)
        try:
            caps = json.loads(raw)['ocs']['data']['capabilities']
        except (KeyError, ValueError, TypeError):
            raise Stop('reponse_talk_invalide') from None
        return isinstance(caps.get('spreed'), dict)

    def trouver(self, nom):
        return self.talk.find(nom)

    def creer(self, nom):
        return self.talk.create(nom)

    def ajouter(self, jeton, compte):
        self.talk.call('POST', '/room/' + jeton + '/participants', {'newParticipant': compte, 'source': 'users'})

    def relire(self, jeton, nom, attendus):
        """Relecture avant d'afficher le lien : groupe privé (type 2, non listé), nom neutre, participants = créateur + choisis explicitement."""
        if not re.fullmatch(r'[A-Za-z0-9]{4,128}', str(jeton or '')):
            raise Stop('jeton_salon_talk_invalide')
        room = self.talk.call('GET', '/room/' + jeton)
        gens = self.talk.call('GET', '/room/' + jeton + '/participants')
        if int(room.get('type', 0)) != 2 or int(room.get('listable', 0)) != 0 or room.get('name') != nom:
            raise Stop('salon_talk_non_prive')
        if not isinstance(gens, list):
            raise Stop('salon_talk_participants_inattendus')
        vus = {str(p.get('actorId') or p.get('userId') or '') for p in gens if p.get('actorType', 'users') == 'users'}
        if any(p.get('actorType', 'users') != 'users' for p in gens) or vus != {self.cfg['username'], *attendus}:
            raise Stop('salon_talk_participants_inattendus')
        return {'type': 2, 'listable': 0, 'participants': sorted(vus), 'relu': True}


def _client(desk, client=None):
    return client or SalonsAgenda(desk.c['nextcloud'])


# ------------------------------------------------------------------------------------------------ Talk : demande, création, relecture
def nom_neutre(event_id):
    """Aucun nom de client, de partie ni de dossier : seulement un repère interne de l'événement."""
    return 'AxiorHub · rendez-vous ' + re.sub(r'[^A-Za-z0-9]', '', str(event_id))[-12:]


def demander_salon(desk, event_id, proprietaire, dossier):
    """Inscrit la demande dans la transaction de l'appelant (aucun commit, aucun réseau ; table créée par ``agenda5697.schema``)."""
    desk.db.execute('INSERT OR IGNORE INTO agenda_salons5697(event_id,proprietaire,dossier,nom,etat,updated) VALUES(?,?,?,?,?,?)',
                    (event_id, proprietaire, dossier, nom_neutre(event_id), 'demande', desk.now()))


def _ligne(desk, event_id):
    row = desk.db.execute('SELECT * FROM agenda_salons5697 WHERE event_id=?', (str(event_id),)).fetchone()
    return dict(row) if row else None


def _etat(desk, event_id, etat, **champs):
    sets = ', '.join('%s=?' % k for k in champs)
    desk.db.execute('UPDATE agenda_salons5697 SET etat=?, updated=?' + (', ' + sets if sets else '') + ' WHERE event_id=?',
                    (etat, desk.now(), *champs.values(), str(event_id)))
    desk.db.commit()


def traiter(desk, event_id, client=None):
    """Exécute une demande de salon (après le commit de l'événement). Rend la ligne à jour, ou None sans demande.
    Réconcilie avant de recréer : un résultat inconnu n'est jamais suivi d'une seconde création aveugle."""
    schema(desk)
    row = _ligne(desk, event_id)
    if not row or row['etat'] in ('pret', 'refuse', 'indisponible'):
        return row
    ok, motif = talk_disponible(desk)
    if not ok:
        _etat(desk, event_id, 'indisponible', motif=motif)
        return _ligne(desk, event_id)
    from .pause5629 import checkpoint
    checkpoint(desk)   # hors travail de fond : sans effet ; dans un travail suspendu ou annulé, aucun effet n'est produit
    try:
        c = _client(desk, client)
        if not c.capacite():
            _etat(desk, event_id, 'indisponible', motif='Talk n’est pas installé sur l’instance Nextcloud configurée.')
            return _ligne(desk, event_id)
        jeton = row['jeton'] or (c.trouver(row['nom']) if row['etat'] in ('creation', 'incertain') else '')
        if not jeton:
            _etat(desk, event_id, 'creation', tentatives=row['tentatives'] + 1)   # inscrit AVANT l'appel : une coupure laisse une trace
            try:
                jeton = c.creer(row['nom'])
            except Stop as ex:
                if str(ex) in ('delai_http_depasse', 'connexion_http_indisponible') or str(ex).startswith('http_5'):
                    _etat(desk, event_id, 'incertain', motif='Réponse de Talk perdue : vérification avant toute relance.')
                    return _ligne(desk, event_id)
                raise
            except Exception:   # noqa: BLE001 — résultat inconnu
                _etat(desk, event_id, 'incertain', motif='Réponse de Talk perdue : vérification avant toute relance.')
                return _ligne(desk, event_id)
            desk.db.execute('UPDATE agenda_salons5697 SET jeton=? WHERE event_id=?', (jeton, event_id))
            desk.db.commit()
        preuve = c.relire(jeton, row['nom'], set(json.loads(row['participants'] or '[]')))
        preuve['verifie_le'] = desk.now()
        _etat(desk, event_id, 'pret', jeton=jeton, preuve=json.dumps(preuve), motif='')
    except Stop as ex:
        code = str(ex)
        if code in ('delai_http_depasse', 'connexion_http_indisponible'):
            _etat(desk, event_id, 'incertain', motif='Talk ne répond pas : vérification avant toute relance.')
        else:
            _etat(desk, event_id, 'refuse', motif=_motif(code))
    desk.audit('agenda5697_salon', {'evenement': event_id, 'etat': (_ligne(desk, event_id) or {}).get('etat', '')})
    return _ligne(desk, event_id)


def _motif(code):
    return {'http_401': 'Nextcloud a refusé la connexion (compte ou secret).', 'http_403': 'Nextcloud a refusé la création du salon (droits du compte).',
            'http_404': 'Talk n’est pas installé ou n’est pas accessible sur l’instance configurée.', 'reponse_talk_invalide': 'Réponse de Talk illisible.',
            'salon_talk_non_prive': 'Le salon relu n’est pas privé : lien non affiché.',
            'salon_talk_participants_inattendus': 'Les participants relus ne sont pas ceux attendus : lien non affiché.',
            'nextcloud_non_configure': 'Nextcloud n’est pas configuré.'}.get(code, 'Talk a refusé la demande (%s).' % code)


def relancer(desk, acteur, event_id, client=None):
    """Action explicite « Vérifier et reprendre » : relecture et réconciliation d'abord."""
    _autorise(desk, acteur, event_id)
    row = _ligne(desk, event_id)
    if not row:
        raise Stop('introuvable')
    if row['etat'] in ('refuse', 'indisponible'):
        _etat(desk, event_id, 'incertain' if row['jeton'] else 'demande')
    return etat_public(desk, acteur, traiter(desk, event_id, client)['event_id'])


def _autorise(desk, acteur, event_id):
    """Le lien n'est rendu qu'aux personnes qui voient le dossier de l'événement (réponse neutre sinon)."""
    from .acces5697 import NEUTRE, peut_lire_dossier
    schema(desk)
    row = _ligne(desk, event_id)
    if row and not peut_lire_dossier(desk, acteur, row['dossier']):
        raise Stop(NEUTRE)
    return row


def etat_public(desk, acteur, event_id):
    """État affichable : libellé honnête, lien seulement si relu ET si la personne est autorisée ; jamais dans un export."""
    try:
        row = _autorise(desk, acteur, event_id)
    except Stop:
        return None
    if not row:
        return None
    out = {'etat': row['etat'], 'libelle': ETATS.get(row['etat'], row['etat']), 'motif': row['motif'], 'participants': json.loads(row['participants'] or '[]'),
           'invitation_envoyee': False, 'lien': ''}
    if row['etat'] == 'pret' and row['jeton']:
        out['lien'] = str(desk.c['nextcloud']['url']).rstrip('/') + '/call/' + quote(row['jeton'], safe='')
    return out


def ajouter_participants(desk, acteur, event_id, comptes, confirme=False, client=None, auth=None):
    """Ajout EXPLICITE de comptes Nextcloud du cabinet ayant accès au dossier ; l'ajout peut déclencher une notification Talk."""
    from . import audiences5688 as au
    if confirme is not True:
        raise Stop('confirmation_requise')
    row = _autorise(desk, acteur, event_id)
    if not row:
        raise Stop('introuvable')
    if row['etat'] != 'pret':
        raise Stop('salon_talk_pas_pret')
    voulus = []
    for compte in comptes or []:
        compte = str(compte or '').strip()
        if not re.fullmatch(r'[A-Za-z0-9_.@-]{1,120}', compte):
            raise Stop('participant_invalide')
        au.check_person(desk, compte, row['dossier'], auth, 'participant_inconnu')   # membre du cabinet ET accès au dossier
        voulus.append(compte)
    deja = set(json.loads(row['participants'] or '[]'))
    c = _client(desk, client)
    for compte in voulus:
        if compte in deja:
            continue
        c.ajouter(row['jeton'], compte)
        deja.add(compte)
        desk.db.execute('UPDATE agenda_salons5697 SET participants=?, updated=? WHERE event_id=?', (json.dumps(sorted(deja)), desk.now(), event_id))
        desk.db.commit()
    preuve = c.relire(row['jeton'], row['nom'], deja)
    desk.db.execute('UPDATE agenda_salons5697 SET preuve=?, updated=? WHERE event_id=?', (json.dumps(preuve), desk.now(), event_id))
    desk.db.commit()
    desk.audit('agenda5697_salon_participants', {'evenement': event_id, 'nombre': len(deja)})
    return dict(etat_public(desk, acteur, event_id), message='Accès donné dans Talk à : %s. Talk peut les avoir notifiés ; aucune invitation '
                                                              'calendrier ni courriel n’a été envoyée.' % ', '.join(sorted(deja)))


# ------------------------------------------------------------------------------------------------ Speakr (§ 14.3)
def speakr_valider(url):
    """Adresse de l'instance Speakr : HTTPS (HTTP seulement en boucle locale d'essai), sans identifiant, mot de passe, paramètre ni fragment."""
    url = str(url or '').strip()
    parts = urlsplit(url)
    local = parts.hostname in ('127.0.0.1', 'localhost', '::1')
    if parts.scheme not in ('https', 'http') or (parts.scheme == 'http' and not local) or not parts.hostname:
        raise Stop('adresse_speakr_https_requise')
    if parts.username or parts.password or parts.query or parts.fragment or len(url) > 300 or any(c in url for c in ' <>"\'\\'):
        raise Stop('adresse_speakr_invalide')
    return parts.scheme + '://' + parts.netloc + (parts.path.rstrip('/') or '') + '/'


def speakr_regler(desk, acteur, portee, url):
    """``portee`` : 'cabinet' (administrateur) ou 'personnel' (compte connecté, droit « Gérer l'agenda »). ``url`` vide : retrait."""
    from .acces5697 import acteur_de
    a = acteur_de(acteur)
    if portee == 'cabinet':
        if a.role != 'administrateur':
            raise Stop('role_insuffisant')
        cle = 'cabinet'
    elif portee == 'personnel':
        cle = a.compte
    else:
        raise Stop('portee_invalide')
    value = {'url': speakr_valider(url)} if str(url or '').strip() else {}
    desk.setting(SPEAKR + cle, value)
    desk.audit('agenda5697_speakr', {'portee': portee, 'adresse': bool(value), 'par': a.compte})
    return {'portee': portee, 'url': value.get('url', ''), 'message': 'Adresse Speakr enregistrée.' if value else 'Adresse Speakr retirée.'}


def speakr_adresse(desk, owner):
    """Adresse personnelle si elle existe, sinon celle du cabinet ; vide si aucune n'est réglée (aucun lien d'exemple)."""
    for cle in (str(owner or ''), 'cabinet'):
        if not cle:
            continue
        value = desk.settings(SPEAKR + cle, {}) or {}
        if value.get('url'):
            try:
                return speakr_valider(value['url'])
            except Stop:
                continue
    return ''


def speakr_lien_html(desk, owner, libelle='Ouvrir Speakr'):
    """Lien vers l'instance, nouvel onglet sans référent ; aucun titre ni dossier dans l'adresse. Vide si aucune adresse n'est réglée."""
    url = speakr_adresse(desk, owner)
    if not url:
        return ''
    return '<a class="ax-btn ghost ag97-speakr" href="%s" target="_blank" rel="noopener noreferrer" referrerpolicy="no-referrer">%s</a>' % (e(url, quote=True), e(libelle))


def reglages_html(desk, owner, role, prefix):
    """Paramètres › Connexions › « Agenda : visio Talk et Speakr ». Aucun compte ni jeton Speakr n'est demandé."""
    cabinet = (desk.settings(SPEAKR + 'cabinet', {}) or {}).get('url', '')
    perso = (desk.settings(SPEAKR + str(owner or ''), {}) or {}).get('url', '')
    ok, motif = talk_disponible(desk)
    admin = role == 'administrateur'
    talk = ('<form class="ag97-reglage" data-ag97-reglage="talk"><label class="ag97-case"><input type="checkbox" name="actif"%s%s> Proposer « Créer un salon privé '
            'Nextcloud Talk » dans l’éditeur de rendez-vous</label><button class="ax-btn ghost" type="submit"%s>Enregistrer</button>'
            '<p class="ag97-aide">%s</p><p role="status" class="ag97-statut"></p></form>') % (
        ' checked' if talk_reglage(desk)['actif'] else '', '' if admin else ' disabled', '' if admin else ' disabled',
        e('Disponible.' if ok else motif) + ('' if admin else ' Réglage réservé à l’administrateur.'))

    def champ(portee, valeur, actif):
        return ('<form class="ag97-reglage" data-ag97-reglage="speakr" data-portee="%s"><label>%s<input type="url" name="url" value="%s" placeholder="https://…" '
                'maxlength="300"%s></label><div class="ag97-actions"><button class="ax-btn ghost" type="submit"%s>Enregistrer</button>%s'
                '<button class="ax-btn ghost" type="button" data-ag97-retirer%s>Retirer</button></div><p role="status" class="ag97-statut"></p></form>') % (
            portee, 'Adresse Speakr du cabinet' if portee == 'cabinet' else 'Mon adresse Speakr (facultative)', e(valeur, quote=True),
            '' if actif else ' disabled', '' if actif else ' disabled',
            ('<a class="ax-btn ghost" href="%s" target="_blank" rel="noopener noreferrer" referrerpolicy="no-referrer">Ouvrir pour vérifier</a>' % e(valeur, quote=True)) if valeur else '',
            '' if actif and valeur else ' disabled')
    from .calendriers5697 import google_reglage
    google = ('<form class="ag97-reglage" data-ag97-reglage="google"><label class="ag97-case"><input type="checkbox" name="actif"%s%s> Lire et écrire les agendas Google '
              'choisis (portée agenda distincte de Gmail, connexion du compte Google par le lot Gmail)</label><button class="ax-btn ghost" type="submit"%s>Enregistrer</button>'
              '<p class="ag97-aide">Désactivé par défaut. Aucun invité n’est ajouté, aucune notification Google n’est envoyée (sendUpdates=none).</p>'
              '<p role="status" class="ag97-statut"></p></form>') % (' checked' if google_reglage(desk)['actif'] else '', '' if admin else ' disabled', '' if admin else ' disabled')
    return ('<section class="ax-card ag97-reglages" id="ag97-reglages"><h2>Agenda : visio Talk, Google Agenda et Speakr</h2>'
            '<h3>Nextcloud Talk</h3>%s<h3>Google Agenda</h3>%s<h3>Speakr (transcription et résumé)</h3><p class="ag97-aide">Seule l’adresse de votre instance Speakr est enregistrée : '
            'aucun compte ni jeton. « Ouvrir Speakr » l’ouvre dans un nouvel onglet ; vous vous y connectez et démarrez vous-même l’enregistrement, après '
            'avoir informé les participants. AxiorHub ne capte ni ne transfère aucun son.</p>%s%s'
            '<script defer src="%s/static/v5697-agenda.js"></script></section>') % (
        talk, google, champ('cabinet', cabinet, admin), champ('personnel', perso, True), e(prefix, quote=True))
