"""5.6.97 (lot agenda : B05, B06, § 14.1, § 15) : un horaire valide, un seul rendez-vous, création et édition depuis la grille.

- B05 · changement d'heure : une heure locale saisie (« naïve ») est convertie par ``instants_valides`` : aucun instant (29 mars, 02 h 30 à
  Paris) → refus et horaire valide proposé ; deux instants (25 octobre, 02 h 30) → les deux décalages sont présentés et l'avocat choisit
  (ou le décalage explicite déjà validé est réutilisé). La fin est comparée en UTC (fin > début) ; une durée en minutes s'ajoute à
  l'instant UTC ; une journée entière est une paire de dates locales, fin exclusive. Une échéance procédurale n'est jamais recalculée ici.
- B06 · idempotence : la clé est créée à l'ouverture de l'éditeur et reste stable après un délai réseau. Portée : cabinet, acteur, commande,
  calendrier. Dans UNE transaction (``BEGIN IMMEDIATE``) : la clé, l'événement, la tâche liée, la demande de salon et la commande distante.
  Même clé et même contenu normalisé : le résultat initial ; contenu modifié : ``cle_idempotence_deja_utilisee`` (409). Deux rendez-vous
  voulus ont deux clés. Modifier exige la version lue (``updated``) : une autre session a écrit entre-temps → ``version_perimee`` (409).
- § 14.1 · un événement (Rendez-vous, Repas professionnel, Autre…), une tâche (avec ou sans bloc réservé) ou les deux, reliés. Déplacer
  le bloc change son horaire, jamais l'échéance de la tâche. Aucun appel à un modèle : tout fonctionne avec l'IA en pause.
- Droits : ``acces5697`` (lot proteger) avant tout filtre, total ou LIMIT ; un événement d'un dossier réservé répond « introuvable ».
- Effets externes (salon Talk, calendrier distant) : inscrits dans la transaction, exécutés APRÈS le commit, jamais avant ; aucun n'est
  déclenché sans le geste explicite de l'avocat (bouton Enregistrer avec l'option choisie).
"""
from datetime import date, datetime, timedelta, timezone
import hashlib
import json
import re
import uuid
from zoneinfo import ZoneInfo

from .common import Stop, load_matters

COMMANDES = ('creer', 'modifier')
NATURES = {'rendez_vous': 'Rendez-vous', 'repas': 'Repas professionnel', 'autre': 'Autre', 'audience': 'Audience', 'echeance': 'Échéance',
           'tache': 'Tâche (créneau réservé)'}
NATURES_SAISIE = ('rendez_vous', 'repas', 'autre')      # types proposés par l'éditeur compact (§ 14.1)
VISIOS = ('sans', 'talk', 'lien')
PRIORITES = {'basse': 'Basse', 'normale': 'Normale', 'haute': 'Haute'}
VISIBILITES = ('normale', 'privee')
SCHEMA = '''
CREATE TABLE IF NOT EXISTS agenda_ops5697(cabinet TEXT NOT NULL, acteur TEXT NOT NULL, commande TEXT NOT NULL, calendrier TEXT NOT NULL, cle TEXT NOT NULL,
  empreinte TEXT NOT NULL, dossier TEXT NOT NULL DEFAULT '', resultat TEXT NOT NULL, created TEXT NOT NULL, PRIMARY KEY(cabinet, acteur, commande, calendrier, cle));
CREATE TABLE IF NOT EXISTS agenda5697(event_id TEXT PRIMARY KEY, nature TEXT NOT NULL DEFAULT 'rendez_vous', visibilite TEXT NOT NULL DEFAULT 'normale',
  visio TEXT NOT NULL DEFAULT 'sans', lien TEXT NOT NULL DEFAULT '', speakr INTEGER NOT NULL DEFAULT 0, tache_id TEXT NOT NULL DEFAULT '',
  calendrier TEXT NOT NULL DEFAULT 'local', zone TEXT NOT NULL DEFAULT '', proprietaire TEXT NOT NULL DEFAULT '', updated TEXT NOT NULL);
CREATE INDEX IF NOT EXISTS agenda5697_tache ON agenda5697(tache_id);
CREATE TABLE IF NOT EXISTS taches5697(task_id TEXT PRIMARY KEY, priorite TEXT NOT NULL DEFAULT 'normale', evenement TEXT NOT NULL DEFAULT '', updated TEXT NOT NULL);
'''


class HoraireInvalide(Stop):
    """B05 : horaire inexistant (``horaire_inexistant``) ou ambigu (``horaire_ambigu``) ; ``detail`` porte la proposition ou les deux choix."""

    def __init__(self, code, detail):
        super().__init__(code)
        self.detail = detail


def schema(desk):
    """Tables du lot (et celles qu'il écrit) créées AVANT toute transaction : ``executescript`` validerait une transaction ouverte."""
    from . import agenda5634, calendriers5697, rendezvous5697, taches5688
    agenda5634.schema(desk)
    taches5688.schema(desk)
    desk.db.executescript(SCHEMA + rendezvous5697.SCHEMA + calendriers5697.SCHEMA)
    desk.db.commit()


# ------------------------------------------------------------------------------------------------ B05 : heures locales et changement d'heure
def zone_de(desk, valeur=''):
    """Fuseau demandé (validé) ou fuseau du cabinet."""
    if valeur:
        try:
            return ZoneInfo(str(valeur))
        except Exception:   # noqa: BLE001 — nom inconnu : refusé, jamais remplacé en silence
            raise Stop('fuseau_invalide') from None
    from .agenda5634 import _tz
    return _tz(desk)


def instants_valides(local_naif, zone):
    """Instants UTC qui affichent exactement ``local_naif`` dans ``zone`` : 0 (heure sautée), 1, ou 2 (heure répétée), triés."""
    return sorted({u for fold in (0, 1)
                   if (u := local_naif.replace(tzinfo=zone, fold=fold).astimezone(timezone.utc)).astimezone(zone).replace(tzinfo=None) == local_naif})


def decalage(instant, zone):
    """Décalage affiché de l'instant dans le fuseau (« +02:00 »)."""
    off = instant.astimezone(zone).utcoffset() or timedelta(0)
    minutes = int(off.total_seconds() // 60)
    return '%s%02d:%02d' % ('+' if minutes >= 0 else '-', abs(minutes) // 60, abs(minutes) % 60)


def suivant_valide(local_naif, zone):
    """Premier horaire local valide après une heure sautée (par pas de 15 minutes, 4 heures au plus)."""
    cursor = local_naif
    for _ in range(16):
        cursor += timedelta(minutes=15)
        if instants_valides(cursor, zone):
            return cursor
    raise Stop('horaire_inexistant')


def instant(local_naif, zone, choix=''):
    """Instant UTC d'une heure locale saisie. ``choix`` : décalage retenu (« +01:00 ») quand l'heure existe deux fois."""
    found = instants_valides(local_naif, zone)
    if not found:
        proposition = suivant_valide(local_naif, zone)
        raise HoraireInvalide('horaire_inexistant', {'saisi': local_naif.strftime('%Y-%m-%dT%H:%M'), 'proposition': proposition.strftime('%Y-%m-%dT%H:%M'),
                                                     'message': 'Cette heure n’existe pas (changement d’heure) : %s est proposé.' % proposition.strftime('%d/%m/%Y %H:%M')})
    if len(found) == 1:
        return found[0]
    offsets = [decalage(u, zone) for u in found]
    if choix and str(choix) in offsets:
        return found[offsets.index(str(choix))]
    raise HoraireInvalide('horaire_ambigu', {'saisi': local_naif.strftime('%Y-%m-%dT%H:%M'), 'choix': [{'decalage': o, 'utc': u.isoformat()} for o, u in zip(offsets, found)],
                                             'message': 'Cette heure existe deux fois (retour à l’heure d’hiver) : choisissez UTC%s ou UTC%s.' % tuple(offsets)})


def _local(texte, code='horaire_invalide'):
    raw = str(texte or '').strip()
    try:
        value = datetime.fromisoformat(raw)
    except ValueError:
        raise Stop(code) from None
    return value


def plage(desk, data):
    """Début et fin UTC d'une saisie. ``debut`` (heure locale « AAAA-MM-JJTHH:MM » ou instant avec décalage) ; ``fin`` locale ou
    ``duree_minutes`` ; ``journee_entiere`` avec ``jour_fin`` (exclusif) ou ``jours`` ; ``decalage_debut``/``decalage_fin`` pour l'heure répétée."""
    zone = zone_de(desk, data.get('zone') or '')
    if data.get('journee_entiere') in (True, 'true', '1', 'on', 1):
        try:
            first = date.fromisoformat(str(data.get('debut') or '')[:10])
            last = date.fromisoformat(str(data['jour_fin'])[:10]) if data.get('jour_fin') else first + timedelta(days=int(data.get('jours') or 1))
        except (ValueError, TypeError):
            raise Stop('date_evenement_invalide') from None
        if not first < last <= first + timedelta(days=31):
            raise Stop('fin_avant_debut' if last <= first else 'duree_evenement_invalide')
        # paire de dates locales, fin exclusive : minuit local de chaque date (le début de journée existe toujours à Paris)
        a = instant(datetime.combine(first, datetime.min.time()), zone)
        b = instant(datetime.combine(last, datetime.min.time()), zone)
        return {'debut': a, 'fin': b, 'journee': True, 'zone': zone.key, 'jour_debut': first.isoformat(), 'jour_fin': last.isoformat()}
    start = _local(data.get('debut'))
    a = start.astimezone(timezone.utc) if start.tzinfo else instant(start, zone, data.get('decalage_debut') or '')
    if data.get('fin'):
        end = _local(data.get('fin'))
        b = end.astimezone(timezone.utc) if end.tzinfo else instant(end, zone, data.get('decalage_fin') or '')
    else:
        try:
            minutes = int(data.get('duree_minutes') or 30)
        except (TypeError, ValueError):
            raise Stop('duree_evenement_invalide') from None
        if not 5 <= minutes <= 1440:
            raise Stop('duree_evenement_invalide')
        b = a + timedelta(minutes=minutes)     # B05 : la durée s'ajoute à l'instant UTC, jamais à l'heure murale
    if b <= a:
        raise Stop('fin_avant_debut')
    if b - a > timedelta(days=1):
        raise Stop('duree_evenement_invalide')
    return {'debut': a, 'fin': b, 'journee': False, 'zone': zone.key}


# ------------------------------------------------------------------------------------------------ B06 : clé d'idempotence
def cabinet_id(desk):
    return str((desk.c.get('cabinet') or {}).get('id') or 'cabinet') if isinstance(desk.c.get('cabinet'), dict) else 'cabinet'


def _cle(value):
    cle = str(value or '').strip()
    if not re.fullmatch(r'[A-Za-z0-9_-]{16,80}', cle):
        raise Stop('cle_idempotence_requise')
    return cle


def empreinte(contenu):
    """Contenu normalisé (clés triées, espaces réduits) : deux envois identiques ont la même empreinte."""
    def norm(v):
        if isinstance(v, str):
            return re.sub(r'\s+', ' ', v).strip()
        if isinstance(v, dict):
            return {k: norm(x) for k, x in sorted(v.items()) if k != 'cle'}
        if isinstance(v, (list, tuple)):
            return [norm(x) for x in v]
        return v
    return hashlib.sha256(json.dumps(norm(contenu), ensure_ascii=False, sort_keys=True, default=str).encode()).hexdigest()


def _op(desk, portee, cle):
    row = desk.db.execute('SELECT * FROM agenda_ops5697 WHERE cabinet=? AND acteur=? AND commande=? AND calendrier=? AND cle=?', (*portee, cle)).fetchone()
    return dict(row) if row else None


def _rejeu(op, sceau):
    """Même clé : même contenu → résultat initial ; contenu différent → conflit explicite (409)."""
    if op['empreinte'] != sceau:
        raise Stop('cle_idempotence_deja_utilisee')
    out = json.loads(op['resultat'])
    out['rejeu'] = True
    out['http'] = 200 if out.get('http') == 201 else out.get('http', 200)
    return out


RETENTION_OPERATIONS = 90        # jours : au-delà, un rejeu de la même clé n'a plus de sens (l'éditeur est fermé depuis longtemps)
RETENTION_SORTIES = 180          # jours : écritures distantes confirmées ou abandonnées


def purger(desk, maintenant=None):
    """Rétention des lignes techniques du lot (opérations idempotentes, écritures distantes closes). Rien d'autre n'est effacé."""
    schema(desk)
    now = maintenant or datetime.now(timezone.utc)
    a = desk.db.execute('DELETE FROM agenda_ops5697 WHERE created<?', ((now - timedelta(days=RETENTION_OPERATIONS)).isoformat(),)).rowcount
    b = desk.db.execute("DELETE FROM agenda_sorties5697 WHERE etat IN ('confirme','abandon','echec') AND updated<?",
                        ((now - timedelta(days=RETENTION_SORTIES)).isoformat(),)).rowcount
    desk.db.commit()
    return {'operations': a, 'sorties': b}


# ------------------------------------------------------------------------------------------------ saisie
def _acteur(acteur):
    from .acces5697 import acteur_de
    return acteur_de(acteur)


def _dossier(desk, acteur, matter):
    """Dossier facultatif : il doit exister ET être visible de l'acteur (réponse neutre sinon)."""
    matter = str(matter or '').strip()
    if not matter:
        return ''
    from .acces5697 import NEUTRE, peut_lire_dossier
    if not peut_lire_dossier(desk, acteur, matter) or not any(m['id'] == matter for m in load_matters(desk.c)):
        raise Stop(NEUTRE)
    return matter


def _texte(value, longueur, code):
    text = re.sub(r'\s+', ' ', str(value or '')).strip()
    if len(text) > longueur:
        raise Stop(code)
    return text


def lien_https(value):
    """Lien de visio fourni : HTTPS, sans identifiant ni mot de passe ; affiché comme « lien fourni »."""
    from urllib.parse import urlsplit
    url = str(value or '').strip()
    parts = urlsplit(url)
    if parts.scheme != 'https' or not parts.hostname or parts.username or parts.password or len(url) > 500 or any(c in url for c in ' <>"\''):
        raise Stop('lien_visio_invalide')
    return url


def normaliser(desk, acteur, data):
    """Saisie de l'éditeur compact → contenu normalisé (aucun effet). ``objet`` : evenement, tache ou les_deux."""
    objet = str(data.get('objet') or 'evenement')
    if objet not in ('evenement', 'tache', 'les_deux'):
        raise Stop('objet_agenda_invalide')
    titre = _texte(data.get('titre') or data.get('title'), 200, 'titre_evenement_invalide')
    if len(titre) < 2:
        raise Stop('titre_evenement_invalide')
    out = {'objet': objet, 'titre': titre, 'dossier': _dossier(desk, acteur, data.get('dossier') or data.get('matter')),
           'calendrier': str(data.get('calendrier') or 'local'), 'lieu': _texte(data.get('lieu'), 200, 'lieu_invalide'),
           'notes': str(data.get('notes') or '').strip()[:2000]}
    tache = data.get('tache') if isinstance(data.get('tache'), dict) else {}
    reserver = objet == 'tache' and tache.get('reserver') in (True, 'true', 'on', '1', 1)
    if objet in ('evenement', 'les_deux') or reserver:
        nature = 'tache' if objet == 'tache' else str(data.get('nature') or 'rendez_vous')
        if nature not in NATURES_SAISIE + ('tache',):
            raise Stop('type_evenement_invalide')
        p = plage(desk, data)
        visio = str(data.get('visio') or 'sans')
        if visio not in VISIOS or (visio != 'sans' and nature != 'rendez_vous'):
            raise Stop('visio_invalide')
        visibilite = str(data.get('visibilite') or 'normale')
        if visibilite not in VISIBILITES:
            raise Stop('visibilite_invalide')
        out['evenement'] = {'nature': nature, 'debut': p['debut'].isoformat(), 'fin': p['fin'].isoformat(), 'journee': p['journee'], 'zone': p['zone'],
                            'visio': visio, 'lien': lien_https(data.get('lien')) if visio == 'lien' else '',
                            'speakr': bool(data.get('speakr') in (True, 'true', 'on', '1', 1)) and nature == 'rendez_vous', 'visibilite': visibilite,
                            'rappels': data.get('rappels') if isinstance(data.get('rappels'), list) else None}
    if data.get('tache_existante') and objet == 'evenement':   # « Programmer » une tâche existante : bloc relié, échéance inchangée
        from .acces5697 import NEUTRE, peut_lire_dossier
        t = desk.db.execute("SELECT id,matter FROM tasks WHERE id=? AND status='open'", (str(data['tache_existante']),)).fetchone()
        if not t or not peut_lire_dossier(desk, acteur, t['matter']):
            raise Stop(NEUTRE)
        out['tache_existante'] = t['id']
        out['dossier'] = out['dossier'] or (t['matter'] or '')
    if objet in ('tache', 'les_deux'):
        priorite = str(tache.get('priorite') or 'normale')
        if priorite not in PRIORITES:
            raise Stop('priorite_invalide')
        echeance = str(tache.get('echeance') or '')[:10]
        if echeance:
            try:
                date.fromisoformat(echeance)
            except ValueError:
                raise Stop('date_tache_invalide') from None
        out['tache'] = {'titre': _texte(tache.get('titre') or titre, 200, 'tache_texte_invalide'), 'responsable': str(tache.get('responsable') or ''),
                        'priorite': priorite, 'echeance': echeance, 'reserver': reserver or objet == 'les_deux'}
    return out


# ------------------------------------------------------------------------------------------------ création (§ 14.1, B06)
def _inserer_tache(desk, contenu, acteur, auth):
    """Tâche du registre commun (``tasks`` + ``taches5688``), SANS commit : la transaction de l'appelant la porte."""
    from . import audiences5688 as au
    t = contenu['tache']
    if len(t['titre']) < 3:
        raise Stop('tache_texte_invalide')
    responsable = au.check_person(desk, t['responsable'], contenu['dossier'], auth, 'responsable_inconnu')
    tid = uuid.uuid4().hex
    stamp = desk.now()
    desk.db.execute('INSERT INTO tasks(id,matter,title,due,status,created) VALUES(?,?,?,?,?,?)', (tid, contenu['dossier'], t['titre'], t['echeance'], 'open', stamp))
    desk.db.execute('INSERT OR REPLACE INTO taches5688(task_id,responsable,rappel,etat,origine,origine_ref,createur,updated) VALUES(?,?,?,?,?,?,?,?)',
                    (tid, responsable, '', 'a_faire', '', '', acteur.compte, stamp))
    desk.db.execute('INSERT OR REPLACE INTO taches5697(task_id,priorite,evenement,updated) VALUES(?,?,?,?)', (tid, t['priorite'], '', stamp))
    return tid


def _ligne_evenement(desk, contenu, acteur):
    """Événement de l'Agenda personnel (``agenda5634``), SANS commit."""
    from . import agenda5634
    ev = contenu['evenement']
    kind = {'repas': 'autre'}.get(ev['nature'], ev['nature'])   # « Repas professionnel » : type propre ici, « Autre » pour l'ancien registre
    when = (datetime.fromisoformat(ev['debut']), datetime.fromisoformat(ev['fin']), ev['journee'])
    data = {'title': contenu['titre'] if ev['nature'] != 'tache' else contenu['tache']['titre'], 'kind': kind, 'matter': contenu['dossier'],
            'location': contenu['lieu'], 'description': contenu['notes'], 'reminders': ev['rappels']}
    return agenda5634.create(desk, data, acteur.compte, when=when, commit=False)


def creer(desk, acteur, data, auth=None, executer=True):
    """Création depuis l'éditeur compact. Rend ``http`` 201 (créé et vérifié), 202 (créé, synchronisation ou salon en attente : reçu
    consultable) ou 200 (rejeu de la même clé)."""
    from . import agenda5634
    a = _acteur(acteur)
    cle = _cle(data.get('cle'))
    contenu = normaliser(desk, a, data)
    calendrier = contenu['calendrier']
    if calendrier != 'local':
        from . import calendriers5697
        calendriers5697.cible_ecriture(desk, a.compte, calendrier)     # destination inscriptible, choisie par l'avocat
    portee = (cabinet_id(desk), a.compte, 'creer', calendrier)
    sceau = empreinte(contenu)
    schema(desk)
    agenda5634.schema(desk)
    if desk.db.in_transaction:
        desk.db.commit()
    desk.db.execute('BEGIN IMMEDIATE')
    try:
        op = _op(desk, portee, cle)
        if op:
            desk.db.rollback()
            return _rejeu(op, sceau)
        stamp, row, tid, sortie = desk.now(), None, '', ''
        if contenu.get('tache'):
            tid = _inserer_tache(desk, contenu, a, auth)
        elif contenu.get('tache_existante'):
            tid = contenu['tache_existante']
            desk.db.execute("INSERT INTO taches5697(task_id,priorite,evenement,updated) VALUES(?,'normale','',?) ON CONFLICT(task_id) DO NOTHING", (tid, stamp))
        if contenu.get('evenement') and calendrier == 'local':
            row = _ligne_evenement(desk, contenu, a)
            ev = contenu['evenement']
            desk.db.execute('INSERT OR REPLACE INTO agenda5697(event_id,nature,visibilite,visio,lien,speakr,tache_id,calendrier,zone,proprietaire,updated) '
                            'VALUES(?,?,?,?,?,?,?,?,?,?,?)', (row['id'], ev['nature'], ev['visibilite'], ev['visio'], ev['lien'], int(ev['speakr']), tid,
                                                              'local', ev['zone'], a.compte, stamp))
            if tid:
                desk.db.execute('UPDATE taches5697 SET evenement=? WHERE task_id=?', (row['id'], tid))
        elif contenu.get('evenement'):
            from . import calendriers5697
            sortie = calendriers5697.inscrire(desk, a.compte, calendrier, cle, contenu)   # une seule commande distante, dans la transaction
            if tid:
                desk.db.execute('UPDATE taches5697 SET evenement=? WHERE task_id=?', ('distant:' + sortie, tid))
        event_id = row['id'] if row else ('distant:' + sortie if sortie else '')
        if contenu.get('evenement') and contenu['evenement']['visio'] == 'talk':
            from . import rendezvous5697
            rendezvous5697.demander_salon(desk, event_id, a.compte, contenu['dossier'])       # demande inscrite, salon créé après le commit
        resultat = {'http': 201, 'evenement_id': event_id, 'tache_id': tid, 'sortie': sortie, 'version': row['updated'] if row else '',
                    'message': _message(contenu, calendrier)}
        desk.db.execute('INSERT INTO agenda_ops5697 VALUES(?,?,?,?,?,?,?,?,?)', (*portee, cle, sceau, contenu['dossier'], json.dumps(resultat), stamp))
        desk.db.commit()
    except BaseException:
        if desk.db.in_transaction:
            desk.db.rollback()
        raise
    desk.audit('agenda5697_cree', {'evenement': event_id, 'tache': tid, 'calendrier': calendrier, 'matter': contenu['dossier'], 'par': a.compte})
    if row and row['kind'] == 'audience':
        from .audiences5688 import on_event
        on_event(desk, row, 'cree')
    out = dict(resultat, rejeu=False)
    if executer:   # effets externes APRÈS le commit ; leur état est relu, jamais supposé
        out.update(_effets(desk, a, event_id, sortie))
    return out


def _message(contenu, calendrier):
    quoi = {'evenement': 'Événement enregistré', 'tache': 'Tâche enregistrée', 'les_deux': 'Événement et tâche liée enregistrés'}[contenu['objet']]
    if contenu.get('evenement') and calendrier != 'local':
        return quoi + ' : envoi au calendrier en ligne en cours de vérification.'
    if contenu.get('evenement'):
        return quoi + ' dans l’Agenda personnel (local, non synchronisé).'
    return quoi + (' (sans créneau dans l’agenda).' if not contenu['tache']['reserver'] else '.')


def _effets(desk, acteur, event_id, sortie):
    """Salon Talk puis écriture distante : chacun relu ; un résultat inconnu devient « confirmation inconnue », jamais un succès."""
    out = {}
    if event_id:
        from . import rendezvous5697
        salon = rendezvous5697.traiter(desk, event_id)
        if salon:
            out['salon'] = rendezvous5697.etat_public(desk, acteur, event_id)
            if salon['etat'] in ('demande', 'creation', 'incertain'):   # en attente : reçu ; indisponible ou refusé : état final, dit tel quel
                out['http'] = 202
    if sortie:
        from . import calendriers5697
        etat = calendriers5697.executer(desk, sortie)
        out['synchronisation'] = etat
        if etat['etat'] != 'confirme':
            out['http'] = 202
    return out


def creer_formulaire(desk, owner, data):
    """B06 pour l'ancienne route ``agenda/creer`` (panneau de l'Agenda personnel) : même clé, même contenu → même événement ; le lien
    Jitsi éventuel est tiré UNE fois, dans la transaction, et rejoué tel quel. L'empreinte porte sur la saisie, pas sur le lien tiré."""
    from . import agenda5634
    from .visio5670 import with_link
    cle = _cle(data.get('cle'))
    saisie = {k: v for k, v in data.items() if k != 'cle'}
    portee = (cabinet_id(desk), str(owner or 'cabinet'), 'creer', 'local')
    sceau = empreinte(saisie)
    schema(desk)
    desk.db.execute('BEGIN IMMEDIATE')
    try:
        op = _op(desk, portee, cle)
        if op:
            desk.db.rollback()
            return _rejeu(op, sceau)
        row = agenda5634.create(desk, with_link(desk, saisie), owner, commit=False)
        resultat = {'http': 201, 'id': row['id'], 'event': agenda5634.public(row), 'message': agenda5634._message(row['sync_state'], 'Événement enregistré')}
        desk.db.execute('INSERT INTO agenda_ops5697 VALUES(?,?,?,?,?,?,?,?,?)', (*portee, cle, sceau, row['matter'], json.dumps(resultat), desk.now()))
        desk.db.commit()
    except BaseException:
        if desk.db.in_transaction:
            desk.db.rollback()
        raise
    desk.audit('agenda5634_cree', {'id': row['id'], 'kind': row['kind'], 'matter': row['matter'], 'source': 'local'})
    if row['kind'] == 'audience':
        from .audiences5688 import on_event
        on_event(desk, row, 'cree')
    return dict(resultat, rejeu=False)


# ------------------------------------------------------------------------------------------------ modification (B06 : version)
def evenement(desk, acteur, event_id):
    """Détail autorisé d'un événement local (§ 15) : réponse neutre si le dossier est réservé ou l'événement absent."""
    from . import agenda5634
    from .acces5697 import NEUTRE, peut_lire_dossier
    schema(desk)
    row = desk.db.execute('SELECT * FROM agenda5634 WHERE id=?', (str(event_id or ''),)).fetchone()
    if not row or not peut_lire_dossier(desk, acteur, row['matter']):
        raise Stop(NEUTRE)
    out = agenda5634.public(dict(row))
    meta = desk.db.execute('SELECT * FROM agenda5697 WHERE event_id=?', (row['id'],)).fetchone()
    meta = dict(meta) if meta else {}
    tz = agenda5634._tz(desk)
    a, b = datetime.fromisoformat(row['starts']).astimezone(tz), datetime.fromisoformat(row['ends']).astimezone(tz)
    out.update({'nature': meta.get('nature') or row['kind'], 'nature_label': NATURES.get(meta.get('nature') or row['kind'], row['kind']),
                'visio': meta.get('visio', 'sans'), 'speakr': bool(meta.get('speakr')), 'tache_id': meta.get('tache_id', ''), 'version': row['updated'],
                'debut_local': a.strftime('%Y-%m-%dT%H:%M'), 'fin_local': b.strftime('%Y-%m-%dT%H:%M'), 'decalage_debut': decalage(a, tz),
                'decalage_fin': decalage(b, tz), 'calendrier': 'local', 'modifiable': not row['recurrence_id'] and row['status'] == 'actif',
                'source_label': 'Agenda personnel', 'lien_fourni': meta.get('lien', '') if meta.get('visio') == 'lien' else ''})
    if row['all_day']:
        out.update(debut_local=a.date().isoformat(), fin_local=b.date().isoformat())
    if out['tache_id']:
        t = desk.db.execute('SELECT t.id,t.title,t.due,t.status,x.priorite FROM tasks t LEFT JOIN taches5697 x ON x.task_id=t.id WHERE t.id=?', (out['tache_id'],)).fetchone()
        out['tache'] = dict(t) if t else None
    from . import rendezvous5697
    out['salon'] = rendezvous5697.etat_public(desk, acteur, row['id'])
    out['speakr_url'] = rendezvous5697.speakr_adresse(desk, _acteur(acteur).compte) if out['speakr'] else ''
    return out


def modifier(desk, acteur, data, auth=None):
    """Modification d'un événement local avec la version lue ; clé d'idempotence par édition. Déplacer un bloc de tâche ne change
    jamais l'échéance de la tâche (seul l'événement est réécrit)."""
    from . import agenda5634
    a = _acteur(acteur)
    cle = _cle(data.get('cle'))
    ident = str(data.get('id') or '')
    courant = evenement(desk, a, ident)                     # droits d'abord : 404 neutre
    if not courant['modifiable']:
        raise Stop('edition_serie_non_prise_en_charge' if courant['series'] else 'evenement_annule')
    changes, contenu = {}, {'id': ident}
    if 'titre' in data:
        changes['title'] = contenu['titre'] = _texte(data['titre'], 200, 'titre_evenement_invalide')
    if 'lieu' in data:
        changes['location'] = contenu['lieu'] = _texte(data['lieu'], 200, 'lieu_invalide')
    if 'notes' in data:
        changes['description'] = contenu['notes'] = str(data['notes'] or '').strip()[:2000]
    if 'dossier' in data:
        changes['matter'] = contenu['dossier'] = _dossier(desk, a, data['dossier'])
    when = None
    if 'debut' in data:
        p = plage(desk, data)
        when = (p['debut'], p['fin'], p['journee'])
        contenu.update(debut=p['debut'].isoformat(), fin=p['fin'].isoformat(), journee=p['journee'])
    if not changes and when is None:
        raise Stop('aucune_modification')
    portee = (cabinet_id(desk), a.compte, 'modifier', 'local')
    sceau = empreinte(contenu)
    schema(desk)
    desk.db.execute('BEGIN IMMEDIATE')
    try:
        op = _op(desk, portee, cle)
        if op:
            desk.db.rollback()
            return _rejeu(op, sceau)
        row = agenda5634.update(desk, {'id': ident, 'base': str(data.get('version') or ''), **changes}, a.compte, when=when, commit=False)
        desk.db.execute('UPDATE agenda5697 SET updated=? WHERE event_id=?', (row['updated'], ident))
        resultat = {'http': 200, 'evenement_id': ident, 'version': row['updated'], 'message': 'Événement modifié dans l’Agenda personnel (local, non synchronisé).'}
        desk.db.execute('INSERT INTO agenda_ops5697 VALUES(?,?,?,?,?,?,?,?,?)', (*portee, cle, sceau, row['matter'], json.dumps(resultat), desk.now()))
        desk.db.commit()
    except BaseException:
        if desk.db.in_transaction:
            desk.db.rollback()
        raise
    desk.audit('agenda5697_modifie', {'evenement': ident, 'par': a.compte})
    from .audiences5688 import on_event
    on_event(desk, row, 'modifie')
    if row.get('_copie_distante'):
        agenda5634._copy(desk, row)
    return dict(resultat, rejeu=False)


# ------------------------------------------------------------------------------------------------ lecture unifiée (§ 15)
def preferences(desk, owner):
    """Préférences d'affichage PAR UTILISATEUR (jours, plage horaire, pas, fuseau) ; jamais un réglage commun."""
    p = desk.settings('agenda5697:prefs:' + str(owner or 'cabinet'), {}) or {}
    out = {'jours': 5, 'heure_debut': 8, 'heure_fin': 19, 'pas': 30, 'zone': '', 'volets': False}
    for key, allowed in (('jours', (5, 7)), ('pas', (15, 30))):
        if p.get(key) in allowed:
            out[key] = p[key]
    if isinstance(p.get('heure_debut'), int) and isinstance(p.get('heure_fin'), int) and 0 <= p['heure_debut'] < p['heure_fin'] <= 24:
        out['heure_debut'], out['heure_fin'] = p['heure_debut'], p['heure_fin']
    if p.get('zone'):
        try:
            ZoneInfo(str(p['zone']))
            out['zone'] = str(p['zone'])
        except Exception:   # noqa: BLE001
            pass
    out['volets'] = bool(p.get('volets'))
    return out


def enregistrer_preferences(desk, owner, data):
    out = preferences(desk, owner)
    for key, allowed in (('jours', (5, 7)), ('pas', (15, 30))):
        if key in data:
            value = int(data[key]) if str(data[key]).isdigit() else None
            if value not in allowed:
                raise Stop('preference_agenda_invalide')
            out[key] = value
    if 'heure_debut' in data or 'heure_fin' in data:
        try:
            a, b = int(data.get('heure_debut', out['heure_debut'])), int(data.get('heure_fin', out['heure_fin']))
        except (TypeError, ValueError):
            raise Stop('preference_agenda_invalide') from None
        if not 0 <= a < b <= 24 or b - a < 2:
            raise Stop('preference_agenda_invalide')
        out['heure_debut'], out['heure_fin'] = a, b
    if 'zone' in data:
        out['zone'] = zone_de(desk, data['zone']).key if data['zone'] else ''
    if 'volets' in data:
        out['volets'] = bool(data['volets'] in (True, 'true', '1', 1))
    desk.setting('agenda5697:prefs:' + str(owner or 'cabinet'), out)
    return out


def taches_non_programmees(desk, acteur, limite=30):
    """Tâches ouvertes sans bloc réservé, filtrées par les droits AVANT la limite."""
    from .acces5697 import dossiers_autorises
    schema(desk)
    perimetre = dossiers_autorises(desk, acteur)
    clause, args = perimetre.sql('t.matter')
    rows = desk.db.execute("SELECT t.id,t.title,t.due,t.matter,COALESCE(x.priorite,'normale') AS priorite FROM tasks t LEFT JOIN taches5697 x ON x.task_id=t.id "
                           "WHERE t.status='open' AND COALESCE(x.evenement,'')='' AND " + clause + " ORDER BY CASE WHEN COALESCE(t.due,'')='' THEN 1 ELSE 0 END, t.due LIMIT ?",
                           (*args, int(limite))).fetchall()
    return [dict(r) for r in rows]


def locaux(desk, acteur, debut, fin, limite=500):
    """Événements de l'Agenda personnel dans la période, droits appliqués AVANT LIMIT."""
    from . import agenda5634
    from .acces5697 import dossiers_autorises
    schema(desk)
    clause, args = dossiers_autorises(desk, acteur).sql('a.matter')
    rows = desk.db.execute("SELECT a.*, m.nature AS nature97, m.visio AS visio97, m.speakr AS speakr97, m.tache_id AS tache97 FROM agenda5634 a "
                           "LEFT JOIN agenda5697 m ON m.event_id=a.id WHERE a.status='actif' AND a.starts<? AND a.ends>? AND " + clause +
                           " ORDER BY a.starts LIMIT ?", (fin.astimezone(timezone.utc).isoformat(), debut.astimezone(timezone.utc).isoformat(), *args, int(limite))).fetchall()
    out = []
    for r in rows:
        item = agenda5634.public(dict(r))
        nature = r['nature97'] or r['kind']
        item.update(source='local', source_label='Agenda personnel', nature=nature, nature_label=NATURES.get(nature, item['kind_label']), modifiable=not r['recurrence_id'],
                    visio=r['visio97'] or 'sans', speakr=bool(r['speakr97']), tache_id=r['tache97'] or '', version=r['updated'], etat_synchro='local')
        out.append(item)
    return out
