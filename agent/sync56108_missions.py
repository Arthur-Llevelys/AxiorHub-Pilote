"""5.6.108 (lot S4) : une seule exécution par attribution — missions et routines partagées entre postes, pauses à portée explicite.

- Chaque mission (ou routine, ou surveillance d'une boîte partagée) désigne un **poste exécuteur** et une **génération d'attribution**.
  Seul l'exécuteur de la génération courante rend compte (``mission.etat``, ``mission.resultat``) : la réception le vérifie AVANT toute
  écriture. Les autres postes voient l'état et le résultat ; **recevoir un résultat ne met jamais de travail en file** (aucun ``enqueue``).
- ``peut_executer`` est le point de montage du moteur de missions : vrai seulement sur l'exécuteur désigné, pour la génération courante,
  hors pause de la mission et hors pause du cabinet. Un conflit d'attribution (deux attributions concurrentes) : personne n'exécute.
- Poste exécuteur absent : « Attend le poste … ». **Transfert** = abandon confirmé par l'exécuteur, puis nouvelle génération pour l'autre
  poste. Reprise forcée (ancien poste injoignable) : explicite, journalisée, **refusée** pour une mission à effet externe (envoi, dépôt,
  paiement, signature) : aucune bascule silencieuse.
- Pauses : du poste (pause de l'IA existante, ``pause5629``), de la mission (``mission.etat`` « pause »), du cabinet (``espace.pause``).
  La synchronisation continue pendant la pause de l'IA et n'appelle jamais de modèle. Une annulation reste : un « en cours » arrivé en
  retard ne la défait pas.
"""
import json
import re

from .common import Stop
from . import sync56108 as S

ETATS = ('en_attente', 'en_cours', 'pause', 'reprise', 'terminee', 'echec', 'annulee', 'transfert_demande')
ETATS_DE_TOUS = ('pause', 'reprise', 'annulee', 'transfert_demande')   # décisions d'une personne, depuis n'importe quel poste membre
FINAUX = ('terminee', 'annulee')

SCHEMA = '''
CREATE TABLE IF NOT EXISTS sync56108_missions(objet TEXT PRIMARY KEY, espace TEXT NOT NULL, titre TEXT NOT NULL DEFAULT '', dossier TEXT NOT NULL DEFAULT '',
  routine TEXT NOT NULL DEFAULT '', executeur TEXT NOT NULL, generation INTEGER NOT NULL, etat TEXT NOT NULL DEFAULT 'en_attente', detail TEXT NOT NULL DEFAULT '',
  effet_externe INTEGER NOT NULL DEFAULT 0, abandon_gen INTEGER NOT NULL DEFAULT 0, forcee INTEGER NOT NULL DEFAULT 0, resultat TEXT NOT NULL DEFAULT '{}',
  local_ref TEXT NOT NULL DEFAULT '', maj_le TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS sync56108_pauses(espace TEXT PRIMARY KEY, pause INTEGER NOT NULL DEFAULT 0, motif TEXT NOT NULL DEFAULT '', par TEXT NOT NULL DEFAULT '',
  maj_le TEXT NOT NULL);
'''


def ensure_schema(desk):
    desk.db.executescript(SCHEMA)
    desk.db.commit()


# ------------------------------------------------------------------------------------------------ charges
def _str(v, n):
    return isinstance(v, str) and len(v) <= n and not re.search(r'[\x00-\x08\x0b\x0c\x0e-\x1f]', v)


def _gen(v):
    return type(v) is int and 1 <= v < 2 ** 31


def valider_charge(type_, ch):
    cles = {'mission.attribution': {'titre', 'dossier', 'routine', 'executeur', 'generation', 'effet_externe', 'local_ref'},
            'mission.etat': {'generation', 'etat', 'detail'}, 'mission.resultat': {'generation', 'resume', 'livrable', 'effet'},
            'mission.abandon': {'generation', 'motif'}, 'mission.reprise': {'generation', 'executeur', 'forcee', 'motif'},
            'espace.pause': {'pause', 'motif'}}.get(type_)
    if cles is None or set(ch) != cles:
        raise Stop('charge_invalide')
    ok = True
    if 'generation' in ch:
        ok &= _gen(ch['generation'])
    if 'executeur' in ch:
        ok &= isinstance(ch['executeur'], str) and S.POSTE_RE.fullmatch(ch['executeur']) is not None
    if type_ == 'mission.attribution':
        ok &= _str(ch['titre'], 200) and (ch['dossier'] == '' or (isinstance(ch['dossier'], str) and re.fullmatch(r'dos-[0-9a-f]{32}', ch['dossier']) is not None))
        ok &= _str(ch['routine'], 120) and isinstance(ch['effet_externe'], bool) and _str(ch['local_ref'], 80)
    elif type_ == 'mission.etat':
        ok &= ch['etat'] in ETATS and _str(ch['detail'], 300)
    elif type_ == 'mission.resultat':
        ok &= _str(ch['resume'], 2000) and (ch['livrable'] == '' or (isinstance(ch['livrable'], str) and re.fullmatch(r'doc-[0-9a-f]{32}', ch['livrable']) is not None))
        ok &= _str(ch['effet'], 120)
    elif type_ == 'mission.abandon':
        ok &= _str(ch['motif'], 300)
    elif type_ == 'mission.reprise':
        ok &= isinstance(ch['forcee'], bool) and _str(ch['motif'], 300)
    elif type_ == 'espace.pause':
        ok &= isinstance(ch['pause'], bool) and _str(ch['motif'], 300)
    if not ok:
        raise Stop('charge_invalide')
    return ch


def _mission(desk, objet):
    if not S._table(desk.db, 'sync56108_missions'):
        return None
    return desk.db.execute('SELECT * FROM sync56108_missions WHERE objet=?', (objet,)).fetchone()


def droits(desk, op, esp):
    """Avant toute écriture : seul l'exécuteur de la génération courante rend compte ; transfert seulement après abandon confirmé ; reprise
    forcée refusée pour un effet externe ; une nouvelle attribution n'écrase jamais une mission existante."""
    t, ch = op['type'], op['charge']
    if t == 'espace.pause':
        if op['objet'] != 'pau-' + op['espace'][4:]:
            raise Stop('charge_invalide')
        return
    m = _mission(desk, op['objet'])
    if t == 'mission.attribution':
        if m is not None:
            raise Stop('mission_deja_attribuee')
        if ch['generation'] != 1 or not S.membre_actif(desk, op['espace'], ch['executeur']):
            raise Stop('executeur_invalide')
        return
    if m is None:
        raise Stop('mission_inconnue')
    if t == 'mission.etat' and ch['etat'] in ETATS_DE_TOUS:
        return
    if t in ('mission.etat', 'mission.resultat', 'mission.abandon'):
        if op['origine'] != m['executeur'] or ch['generation'] != m['generation']:
            raise Stop('executeur_non_designe')
        return
    if t == 'mission.reprise':
        if ch['generation'] != m['generation'] + 1 or not S.membre_actif(desk, op['espace'], ch['executeur']):
            raise Stop('generation_invalide')
        if ch['forcee']:
            if m['effet_externe']:
                raise Stop('reprise_forcee_interdite_effet_externe')
        elif m['abandon_gen'] != m['generation']:
            raise Stop('abandon_non_confirme')
        return
    raise Stop('charge_invalide')


def apres_reception(desk, op):
    """Projection (dans la transaction) : table d'état, dans l'ordre d'arrivée, règles monotones. Jamais de travail mis en file."""
    ensure_tables(desk)
    t, ch, now = op['type'], op['charge'], S._maintenant()
    if t == 'espace.pause':
        hs = S.tetes(desk, op['objet'])
        pause = any(json.loads(desk.db.execute('SELECT brut FROM sync56108_operations WHERE id=?', (h,)).fetchone()[0])['charge']['pause'] for h in hs)
        desk.db.execute('INSERT INTO sync56108_pauses(espace,pause,motif,par,maj_le) VALUES(?,?,?,?,?) ON CONFLICT(espace) DO UPDATE SET pause=excluded.pause,'
                        'motif=excluded.motif,par=excluded.par,maj_le=excluded.maj_le', (op['espace'], int(pause), ch['motif'][:300], op['acteur'], now))
        return []
    m = _mission(desk, op['objet'])
    if t == 'mission.attribution':
        desk.db.execute('INSERT OR IGNORE INTO sync56108_missions(objet,espace,titre,dossier,routine,executeur,generation,etat,effet_externe,local_ref,maj_le) '
                        "VALUES(?,?,?,?,?,?,1,'en_attente',?,?,?)", (op['objet'], op['espace'], ch['titre'], ch['dossier'], ch['routine'], ch['executeur'],
                                                                   int(ch['effet_externe']), ch['local_ref'] if op['origine'] == S._ident(desk).id else '', now))
    elif m is None:
        return []
    elif t == 'mission.etat':
        if m['etat'] == 'annulee' or (m['etat'] in FINAUX and ch['etat'] not in ('annulee',)):
            return []   # une annulation (ou une fin) n'est jamais défaite par un état arrivé en retard
        etat = 'en_attente' if ch['etat'] == 'reprise' else ch['etat']
        desk.db.execute('UPDATE sync56108_missions SET etat=?, detail=?, maj_le=? WHERE objet=?', (etat, ch['detail'], now, op['objet']))
    elif t == 'mission.resultat':
        desk.db.execute("UPDATE sync56108_missions SET resultat=?, etat=CASE WHEN etat='annulee' THEN etat ELSE 'terminee' END, maj_le=? WHERE objet=?",
                        (S.canon({'resume': ch['resume'], 'livrable': ch['livrable'], 'effet': ch['effet'], 'generation': ch['generation']}), now, op['objet']))
    elif t == 'mission.abandon':
        desk.db.execute('UPDATE sync56108_missions SET abandon_gen=?, detail=?, maj_le=? WHERE objet=?', (ch['generation'], ch['motif'], now, op['objet']))
    elif t == 'mission.reprise':
        if ch['generation'] == m['generation'] + 1:
            desk.db.execute("UPDATE sync56108_missions SET executeur=?, generation=?, forcee=?, etat=CASE WHEN etat IN ('terminee','annulee') THEN etat ELSE 'en_attente' END, "
                            'detail=?, maj_le=? WHERE objet=?', (ch['executeur'], ch['generation'], int(ch['forcee']), ch['motif'], now, op['objet']))
    return []


def ensure_tables(desk):
    if not S._table(desk.db, 'sync56108_missions'):
        desk.db.execute(SCHEMA.split(';')[0])
        desk.db.execute(SCHEMA.split(';')[1])


# ------------------------------------------------------------------------------------------------ actions locales
def _emettre(desk, espace, type_, objet, charge):
    ensure_schema(desk)
    with S._tx(desk):
        op = S.emettre(desk, espace, type_, objet, charge)
        apres_reception(desk, op)
    return op


def attribuer(desk, titre, executeur, dossier_gid='', routine='', effet_externe=False, local_ref=''):
    """Nouvelle mission partagée, exécutée par ``executeur`` (génération 1). Une routine ou une boîte partagée n'a qu'un surveillant."""
    esp = S.espace_cabinet(desk)
    if esp is None:
        raise Stop('espace_absent')
    if dossier_gid:
        o = desk.db.execute("SELECT espace FROM sync56108_objets WHERE objet=?", (dossier_gid,)).fetchone()
        if not o:
            raise Stop('introuvable')
        espace = o[0]
    else:
        espace = esp['id']
    if not S.membre_actif(desk, espace, executeur):
        raise Stop('executeur_invalide')
    ensure_schema(desk)
    if routine and desk.db.execute("SELECT 1 FROM sync56108_missions WHERE routine=? AND etat NOT IN ('terminee','annulee')", (routine,)).fetchone():
        raise Stop('routine_deja_surveillee')
    objet = S.nouvel_id('mis')
    _emettre(desk, espace, 'mission.attribution', objet, {'titre': S._nom(titre, 200), 'dossier': dossier_gid, 'routine': str(routine)[:120],
                                                          'executeur': executeur, 'generation': 1, 'effet_externe': bool(effet_externe),
                                                          'local_ref': str(local_ref)[:80]})
    return {'objet': objet}


def _courante(desk, objet):
    m = _mission(desk, str(objet or ''))
    if m is None:
        raise Stop('introuvable')
    return m


def rendre_compte(desk, objet, etat=None, resultat=None, detail=''):
    """Exécuteur seulement : état ou résultat de la génération courante."""
    m = _courante(desk, objet)
    moi = S._ident(desk).id
    if m['executeur'] != moi:
        raise Stop('executeur_non_designe')
    if resultat is not None:
        return _emettre(desk, m['espace'], 'mission.resultat', m['objet'], {'generation': m['generation'], 'resume': str(resultat.get('resume') or '')[:2000],
                                                                          'livrable': str(resultat.get('livrable') or ''), 'effet': str(resultat.get('effet') or '')[:120]})
    return _emettre(desk, m['espace'], 'mission.etat', m['objet'], {'generation': m['generation'], 'etat': etat, 'detail': str(detail)[:300]})


def decider(desk, objet, etat, detail=''):
    """Une personne, depuis tout poste : pause, reprise, annulation, demande de transfert (vers ``detail`` : identifiant du poste)."""
    if etat not in ETATS_DE_TOUS:
        raise Stop('etat_invalide')
    m = _courante(desk, objet)
    return _emettre(desk, m['espace'], 'mission.etat', m['objet'], {'generation': m['generation'], 'etat': etat, 'detail': str(detail)[:300]})


def transferer(desk, objet, vers, motif=''):
    """Exécuteur courant : abandon confirmé puis nouvelle génération pour ``vers`` (transfert propre)."""
    m = _courante(desk, objet)
    moi = S._ident(desk).id
    if m['executeur'] != moi:
        raise Stop('executeur_non_designe')
    if not S.membre_actif(desk, m['espace'], vers) or vers == moi:
        raise Stop('executeur_invalide')
    _emettre(desk, m['espace'], 'mission.abandon', m['objet'], {'generation': m['generation'], 'motif': str(motif or 'transfert')[:300]})
    return _emettre(desk, m['espace'], 'mission.reprise', m['objet'], {'generation': m['generation'] + 1, 'executeur': vers, 'forcee': False,
                                                                      'motif': str(motif or 'transfert')[:300]})


def reprendre_force(desk, objet, confirme=False, motif=''):
    """Reprise explicite par CE poste quand l'exécuteur est injoignable : confirmation exigée ; refusée pour un effet externe."""
    m = _courante(desk, objet)
    if not confirme:
        raise Stop('confirmation_requise')
    if m['effet_externe']:
        raise Stop('reprise_forcee_interdite_effet_externe')
    moi = S._ident(desk).id
    return _emettre(desk, m['espace'], 'mission.reprise', m['objet'], {'generation': m['generation'] + 1, 'executeur': moi, 'forcee': True,
                                                                      'motif': str(motif or 'reprise forcée : poste exécuteur injoignable')[:300]})


def pause_cabinet(desk, pause, motif=''):
    esp = S.espace_cabinet(desk)
    if esp is None:
        raise Stop('espace_absent')
    ensure_schema(desk)
    return _emettre(desk, esp['id'], 'espace.pause', 'pau-' + esp['id'][4:], {'pause': bool(pause), 'motif': str(motif)[:300]})


def cabinet_en_pause(desk):
    esp = S.espace_cabinet(desk)
    if esp is None or not S._table(desk.db, 'sync56108_pauses'):
        return False
    r = desk.db.execute('SELECT pause FROM sync56108_pauses WHERE espace=?', (esp['id'],)).fetchone()
    return bool(r and r[0])


def peut_executer(desk, objet):
    """Point de montage du moteur de missions : ce poste exécute-t-il cette mission maintenant ? (exécuteur désigné, génération courante,
    pas de conflit d'attribution, ni pause de mission, ni pause du cabinet, ni fin). La pause de l'IA du poste reste contrôlée par le moteur."""
    m = _mission(desk, str(objet or ''))
    if m is None:
        return False
    if m['routine'] and desk.db.execute("SELECT 1 FROM sync56108_missions WHERE routine=? AND objet<? AND etat NOT IN ('terminee','annulee')",
                                        (m['routine'], m['objet'])).fetchone():
        return False   # deux attributions concurrentes de la même routine : une seule (règle déterministe, jamais l'horloge) surveille
    return (m['executeur'] == S._ident(desk).id and m['etat'] in ('en_attente', 'en_cours') and m['abandon_gen'] != m['generation']
            and not cabinet_en_pause(desk))


def etat_lisible(desk, m):
    """Texte de l'état vu depuis ce poste (« Attend le poste … » si l'exécuteur est absent)."""
    from .liaison56103 import presence_de
    moi = S._ident(desk).id
    nom = desk.db.execute('SELECT nom FROM sync56108_membres WHERE poste=? LIMIT 1', (m['executeur'],)).fetchone()
    nom = (nom[0] if nom else '') or 'poste relié'
    if m['etat'] in FINAUX:
        return {'terminee': 'Terminée', 'annulee': 'Annulée'}[m['etat']]
    if m['etat'] == 'pause':
        return 'En pause (mission)'
    if cabinet_en_pause(desk):
        return 'En pause (cabinet)'
    if m['executeur'] == moi:
        return 'Exécutée sur ce poste' + (' (reprise forcée)' if m['forcee'] else '')
    p = desk.db.execute('SELECT * FROM liaison56103_postes WHERE id=?', (m['executeur'],)).fetchone()
    if p is None or presence_de(dict(p)) == 'hors_ligne':
        return 'Attend le poste « %s »' % nom
    return 'Exécutée sur le poste « %s »' % nom


def lister(desk):
    if not S._table(desk.db, 'sync56108_missions'):
        return []
    return [dict(dict(r), lisible=etat_lisible(desk, r)) for r in desk.db.execute('SELECT * FROM sync56108_missions ORDER BY maj_le DESC LIMIT 100')]


# ------------------------------------------------------------------------------------------------ 5.6.108 (S5) : garde du moteur
CODE_AUTRE_POSTE = 'execution_sur_un_autre_poste'
TRAVAUX_BOITE = ('run', 'live_mail430', 'autonomy_mail_sweep', 'orchestrator_mail_sweep')


def cle_execution(desk, kind, args):
    """Clé partageable d'un travail : la même sur tous les postes pour la même routine ou la même boîte. Vide : travail local, jamais partagé."""
    args = args if isinstance(args, dict) else {}
    if args.get('mission_id'):
        return 'mission567:%s' % str(args['mission_id'])[:80]
    if args.get('occurrence') and S._table(desk.db, 'routines5652_occ'):
        r = desk.db.execute('SELECT routine_id FROM routines5652_occ WHERE id=?', (str(args['occurrence']),)).fetchone()
        if r:
            return 'routine5652:%s' % r[0]
    if kind == 'routine520':
        return 'routine520:%s' % re.sub(r'[^a-z_]', '', str(args.get('kind') or ''))[:30]
    if kind == 'routine_message56106':
        return 'routine_message56106'
    if kind in TRAVAUX_BOITE:
        compte = str(((desk.c.get('mail') or {}).get('username')) or '').strip().lower()
        return ('boite:' + compte)[:200] if compte else ''
    if kind == 'gmail5697':
        return 'boite:gmail'
    if kind == 'monitor_matter' and args.get('matter'):
        from .sync56108_objets import objet_de
        o = objet_de(desk, 'dossier', str(args['matter'])) if S._table(desk.db, 'sync56108_objets') else None
        return ('surveillance:' + o['objet']) if o else ''
    return ''


def garde_cle(desk, cle):
    """Avant toute exécution d'une routine, d'une surveillance ou d'une mission partagée : rien à faire si le poste travaille seul ou si la clé
    n'est attribuée à aucun poste ; sinon seul l'exécuteur désigné passe (``Stop(CODE_AUTRE_POSTE)`` ailleurs, avec l'état lisible)."""
    if not cle or not S._table(desk.db, 'sync56108_missions') or S.espace_cabinet(desk) is None or S.desactivee(desk):
        return None
    rows = desk.db.execute("SELECT * FROM sync56108_missions WHERE routine=? AND etat NOT IN ('terminee','annulee') ORDER BY objet", (cle,)).fetchall()
    if not rows:
        return None
    if any(peut_executer(desk, r['objet']) for r in rows):
        return rows[0]['objet']
    desk.attente_poste56108 = etat_lisible(desk, rows[0])
    raise Stop(CODE_AUTRE_POSTE)


def garde_travail(desk, kind, args):
    """Point de montage de ``Desk.work_once`` (juste après l'autorité de la mission) : aucun travail IA ni effet externe sur un poste non exécuteur."""
    desk.attente_poste56108 = ''
    return garde_cle(desk, cle_execution(desk, kind, args))


def message_attente(desk):
    etat = getattr(desk, 'attente_poste56108', '') or 'Exécutée sur un autre poste'
    return 'Exécution partagée : %s. Rien n’est fait sur ce poste (aucun appel à l’IA, aucun effet externe).' % etat


def cles_du_poste(desk):
    """Routines et surveillances de ce poste qui peuvent recevoir un exécuteur unique (Paramètres)."""
    out = []
    compte = str(((desk.c.get('mail') or {}).get('username')) or '').strip().lower()
    if compte:
        out.append(('boite:' + compte, 'Surveillance de la boîte « %s »' % compte))
    from .routines520 import KINDS
    out += [('routine520:' + k, v) for k, v in KINDS.items()]
    if S._table(desk.db, 'routines5652'):
        out += [('routine5652:' + r[0], 'Routine « %s »' % r[1]) for r in desk.db.execute('SELECT id, title FROM routines5652 ORDER BY title LIMIT 50')]
    out.append(('routine_message56106', 'Routines sur message'))
    return out
