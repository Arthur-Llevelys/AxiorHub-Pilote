"""5.6.51 (audit 5.6.50, Dossier-D12) : routes /api440/m5651/… de la page Dossier refondue et des fonctions d'autonomie.

Chaque écriture porte le dossier (``matter``) : le contrôle central (5.6.50/5.6.51) refuse un dossier réservé avant ce module ; chaque
service vérifie ensuite que l'objet visé appartient bien à ce dossier et contrôle la version attendue. Les routes qui créent ou
modifient une information validée relèvent du droit « Valider » (registre ``membres5650.VALIDATE_API``), contrôlé aussi ici.
Erreurs cohérentes : accès refusé (403), objet absent, version en conflit, contenu incomplet (400).
"""
from .common import Stop

ALL_ROLES = ('administrateur', 'avocat', 'assistant')
# Routes ouvertes aux assistant(e)s : consultation, classement des documents (D07 : ne déclare aucune communication), relance de lecture.
ASSISTANT_ROUTES = ('analyser', 'lu', 'preference', 'document/classer', 'couverture/reprendre')
VALIDATE_ROUTES = ('information', 'information/ajouter', 'partie', 'partie/ajouter', 'partie/fusion', 'partie/separer', 'partie/annuler-fusion',
                   'procedure', 'decision-cabinet', 'decision-cabinet/retirer', 'file/decider', 'file/lot', 'relecture/decider', 'relecture/appliquer',
                   'preparation', 'regle')
LAWYER_ROUTES = ('mission', 'relecture', 'rappel', 'lacune')
ROUTES = ASSISTANT_ROUTES + VALIDATE_ROUTES + LAWYER_ROUTES


def allowed(role, route):
    route = route.split('?', 1)[0]
    if role not in ALL_ROLES or route not in ROUTES:
        return False
    return role in ('administrateur', 'avocat') or route in ASSISTANT_ROUTES


def _bool(value):
    return value is True or str(value).lower() in ('true', 'on', '1', 'oui')


def api(env, desk, auth, prefix, name, args, method):
    from .web440 import json_out
    from .web567 import actor
    from .web5634 import _post
    from . import modele5651, memoire5651
    owner, role = actor(env)
    route = name[len('m5651/'):].split('?', 1)[0]
    if method != 'POST':
        raise Stop('methode_refusee')
    if not allowed(role, route):
        raise Stop('role_insuffisant')
    data = _post(env, auth, 200_000)
    if route in VALIDATE_ROUTES:   # 5.6.51 (B02) : le service vérifie aussi le droit effectif
        from .membres5650 import effective_rights
        try:
            may = effective_rights(desk.c['state_dir'], owner, role)['valider']
        except Stop:
            may = role == 'administrateur'
        if not may:
            raise Stop('role_insuffisant')
    mid = str(data.get('matter') or '')
    m = modele5651._matter(desk, mid)          # dossier absent ou réservé (registre filtré) : refus
    mid = m['id']
    from .membres5650 import viewer_hidden
    hidden = viewer_hidden() or frozenset()
    if mid in hidden:
        raise Stop('dossier_reserve')
    modele5651.ensure_schema(desk)
    memoire5651.ensure_schema(desk)
    from .membres5650 import route_objects
    real = route_objects(desk.c['state_dir'], '/api440/' + name, data)
    if real - {mid}:                                   # 5.6.52 (F01) : objet d'un autre dossier désigné sous ce dossier
        raise Stop('element_absent')
    version = data.get('version')
    if route == 'analyser':
        return json_out({**modele5651.rebuild(desk, mid, force=True), 'message': 'Données du dossier actualisées.'})
    if route == 'lu':
        memoire5651.mark_read(desk, mid, owner)
        return json_out({'ok': True, 'message': 'Marqué comme lu.'})
    if route == 'preference':
        order = str(data.get('ordre') or '')
        if order not in ('ancien', 'recent'):
            raise Stop('preference_invalide')
        desk.setting('chronologie5651:' + owner, {'ordre': order})
        return json_out({'ok': True})
    if route == 'document/classer':
        piece = data.get('piece')
        out = modele5651.classify(desk, mid, str(data.get('path') or ''), owner, nature=data.get('nature') or None,
                                  piece=None if piece in (None, '') else piece in ('oui', True),
                                  piece_party=data.get('piece_party') if data.get('piece_party') is not None else None,
                                  piece_number=data.get('piece_number') if data.get('piece_number') is not None else None, version=version)
        return json_out({'document': out, 'message': 'Classement enregistré (aucune communication déclarée, aucun numéro modifié).'})
    if route == 'couverture/reprendre':
        return json_out(retry_reading(desk, mid, str(data.get('categorie') or ''), owner))
    if route == 'information':
        out = modele5651.review(desk, mid, str(data.get('id') or ''), owner, str(data.get('action') or ''),
                                {'version': version, 'motif': data.get('motif'), 'texte': data.get('texte'), 'proof': data.get('proof') or None,
                                 'date': data.get('date') if str(data.get('date') or '') != '' or data.get('action') == 'corriger' else None, 'avec': data.get('avec')})
        return json_out({'information': out, 'message': 'Décision enregistrée.'})
    if route == 'information/ajouter':
        return json_out({'id': modele5651.add_assertion(desk, mid, owner, data), 'message': 'Information ajoutée (saisie de l’avocat).'})
    if route == 'partie':
        payload = {k: data[k] for k in ('display_name', 'counsel', 'version') if k in data}
        if 'client' in data:
            payload['client'] = _bool(data['client'])
        if _bool(data.get('retirer')):
            payload['retirer'] = True
        return json_out({'partie': modele5651.set_party(desk, mid, str(data.get('id') or ''), owner, payload), 'message': 'Partie corrigée.'})
    if route == 'partie/ajouter':
        return json_out({'id': modele5651.add_party(desk, mid, owner, str(data.get('nom') or ''), _bool(data.get('client'))), 'message': 'Partie ajoutée.'})
    if route == 'partie/fusion':
        return json_out({**modele5651.merge(desk, mid, str(data.get('a') or ''), str(data.get('b') or ''), owner, str(data.get('motif') or '')),
                         'message': 'Mentions réunies (réversible).'})
    if route == 'partie/separer':
        modele5651.separate(desk, mid, str(data.get('a') or ''), str(data.get('b') or ''), owner, str(data.get('motif') or ''))
        return json_out({'ok': True, 'message': 'Personnes distinctes : ce rapprochement ne sera plus proposé.'})
    if route == 'partie/annuler-fusion':
        modele5651.undo_merge(desk, mid, str(data.get('decision') or '0'), owner)
        return json_out({'ok': True, 'message': 'Fusion annulée.'})
    if route == 'procedure':
        payload = dict(data)
        payload['reference'] = _bool(data.get('reference'))
        return json_out({'id': modele5651.save_procedure(desk, mid, owner, payload), 'message': 'Procédure enregistrée.'})
    if route == 'decision-cabinet':
        return json_out({'id': memoire5651.save_decision(desk, mid, owner, data), 'message': 'Décision du cabinet enregistrée.'})
    if route == 'decision-cabinet/retirer':
        memoire5651.withdraw_decision(desk, mid, str(data.get('id') or ''), owner)
        return json_out({'ok': True, 'message': 'Décision retirée (historique conservé).'})
    if route == 'lacune':
        out = memoire5651.resolve_gap(desk, mid, str(data.get('id') or ''), owner, str(data.get('reponse') or ''),
                                      'abandonnee' if _bool(data.get('abandonner')) else 'resolue')
        if out.get('mission') and out['state'] == 'resolue':   # reprise des seules étapes concernées par le moteur de missions
            _answer_mission(desk, out, owner, role)
        return json_out({'lacune': out, 'message': 'Information enregistrée.'})
    if route in ('file/decider', 'file/lot'):
        from . import file5651
        if route == 'file/lot':
            return json_out(file5651.decide_batch(desk, owner, data.get('elements') or [], str(data.get('state') or ''), prefix, hidden, mid))
        out = file5651.decide(desk, owner, str(data.get('key') or ''), str(data.get('state') or ''), str(data.get('revision') or ''),
                              str(data.get('motif') or ''), int(data.get('jours') or 1) if str(data.get('jours') or '1').isdigit() else 1, prefix, hidden, mid)
        return json_out({**out, 'message': 'Décision enregistrée.'})
    if route == 'mission':
        from . import missions5651
        payload = dict(data)
        if isinstance(payload.get('fichiers'), str):
            payload['fichiers'] = [x for x in payload['fichiers'].split('\n') if x.strip()]
        out = missions5651.start(desk, owner, payload)
        return json_out({'mission': out['id'], 'rejointe': bool(out.get('rejointe')),
                         'message': 'Mission déjà en cours : vous la rejoignez.' if out.get('rejointe') else 'Mission lancée.'})
    if route == 'preparation':
        from . import missions5651
        return json_out({**missions5651.set_mode(desk, mid, owner, data), 'message': 'Réglage enregistré.'})
    if route == 'relecture':
        from . import relecture5651
        paras = [x for x in str(data.get('paragraphes') or '').replace(';', ',').split(',') if x.strip()]
        scope = []
        for x in paras:
            if '-' in x:
                a, _, b = x.partition('-')
                if a.strip().isdigit() and b.strip().isdigit():
                    scope += list(range(int(a) - 1, min(int(b), int(a) + 400)))
            elif x.strip().isdigit():
                scope.append(int(x) - 1)
        out = relecture5651.start(desk, mid, owner, str(data.get('path') or ''), str(data.get('consigne') or ''), scope)
        return json_out({'session': out['id'], 'goto': '?id=%s&vue=travaux&relecture=%s' % (mid, out['id']), 'message': 'Relecture demandée.'})
    if route == 'relecture/decider':
        from . import relecture5651
        before = None
        if data.get('action') == 'modifier':
            row = desk.db.execute('SELECT expected,replacement,session FROM d51_proposals WHERE id=? AND matter=?', (str(data.get('id') or ''), mid)).fetchone()
            before = dict(row) if row else None
        out = relecture5651.decide(desk, mid, str(data.get('id') or ''), owner, str(data.get('action') or ''), version or 0, data.get('texte'),
                                   sources_ok=_bool(data.get('sources_verifiees')))
        learned = None
        if before and data.get('portee'):   # A08 : la correction est retenue selon la portée choisie
            from . import apprentissage5651
            path = desk.db.execute('SELECT path FROM d51_review_sessions WHERE id=?', (before['session'],)).fetchone()
            learned = apprentissage5651.learn(desk, owner, {'portee': data.get('portee'), 'categorie': data.get('categorie') or 'style', 'matter': mid,
                                                            'document': path['path'] if path else '', 'avant': before['replacement'], 'apres': out['replacement']})['id']
        return json_out({'proposition': out, 'regle': learned, 'message': 'Décision enregistrée.'})
    if route == 'relecture/appliquer':
        from . import relecture5651
        out = relecture5651.apply(desk, mid, str(data.get('session') or ''), owner)
        return json_out({**out, 'message': ('Version %d déjà créée avec ces décisions.' % out['version']) if out.get('deja_cree') else
                         'Version %d créée : %d modification(s) en suivi, %d à reporter à la main, %d périmée(s), %d à recontrôler (source modifiée).'
                         % (out['version'], out['applied'], out['manual'], out['stale'], out['invalidated'])})
    if route == 'regle':
        from . import apprentissage5651
        out = apprentissage5651.set_status(desk, str(data.get('id') or ''), owner, str(data.get('status') or ''), role == 'administrateur',
                                           expected_matter=mid, expected_version=data.get('version'))
        return json_out({'regle': {'id': out['id'], 'status': out['status'], 'version': out['version']}, 'message': 'Règle mise à jour.'})
    if route == 'rappel':
        kind = str(data.get('kind') or 'faits')
        if kind not in ('faits', 'procedure'):
            raise Stop('type_rappel_invalide')
        job = desk.enqueue('recit5651', {'matter': mid, 'kind': kind, 'owner': owner})
        return json_out({'job': job, 'message': 'Rédaction demandée ; la page se mettra à jour.'})
    raise Stop('route_inconnue')


def _answer_mission(desk, gap, owner, role):
    """Quand toutes les informations demandées par une mission sont fournies, elles sont transmises à sa décision « données
    manquantes » ; le moteur de missions reprend alors seulement les étapes concernées."""
    try:
        from . import taches5614
        from .memoire5651 import gaps
        if any(g['mission'] == gap['mission'] for g in gaps(desk, gap['matter'])):
            return                                                      # d'autres informations sont encore attendues
        answer = {g['expected']: g['answer'] for g in gaps(desk, gap['matter'], 'resolue') if g['mission'] == gap['mission'] and g['answer']}
        row = desk.db.execute("SELECT id FROM decisions5614 WHERE mission=? AND kind='champs_manquants' AND state='a_decider'", (gap['mission'],)).fetchone()
        if row and answer:
            taches5614.decide(desk, {'id': row['id'], 'action': 'answer', 'answer': answer}, owner, role == 'administrateur')
    except Stop:
        pass


def retry_reading(desk, mid, category, owner):
    """D11 : relance ciblée et bornée (deux fois par version de fichier) d'une catégorie d'erreur ; jamais pour un fichier protégé ou
    d'un format non pris en charge, qu'une relance ne peut pas lire."""
    from .vue5651 import RETRYABLE
    from . import modele5651
    if category not in RETRYABLE:
        raise Stop('relance_inutile')
    desk.db.executescript('CREATE TABLE IF NOT EXISTS d51_retries(matter TEXT NOT NULL, path TEXT NOT NULL, etag TEXT NOT NULL, attempts INTEGER NOT NULL, '
                          'at TEXT NOT NULL, PRIMARY KEY(matter, path, etag));')
    from .index import DocumentIndex
    targets, skipped = [], 0
    for d in modele5651.documents(desk, mid):
        if d['reading'] != 'erreur' or (d['error_category'] or 'autre') != category:
            continue
        row = desk.db.execute('SELECT attempts FROM d51_retries WHERE matter=? AND path=? AND etag=?', (mid, d['path'], d['etag'])).fetchone()
        if row and row['attempts'] >= 2:
            skipped += 1
            continue
        targets.append(d)
    if not targets:
        return {'relances': 0, 'ignores': skipped, 'message': 'Aucune relance utile : %d document(s) déjà relancé(s) deux fois sans succès.' % skipped}
    index = DocumentIndex(desk.c['state_dir'])
    try:
        for d in targets[:100]:
            index.db.execute("UPDATE docs SET etag='' WHERE matter=? AND path=?", (mid, d['path']))
        index.db.commit()
    finally:
        index.db.close()
    from datetime import datetime, timezone
    now = datetime.now(timezone.utc).isoformat(timespec='seconds')
    for d in targets[:100]:
        desk.db.execute('INSERT INTO d51_retries(matter,path,etag,attempts,at) VALUES(?,?,?,1,?) ON CONFLICT(matter,path,etag) DO UPDATE SET attempts=attempts+1,at=?',
                        (mid, d['path'], d['etag'], now, now))
    desk.db.commit()
    job = desk.enqueue('index', {'matter': mid})
    desk.audit('relance_lecture5651', {'matter': mid, 'category': category, 'count': len(targets[:100]), 'by': owner})
    return {'relances': len(targets[:100]), 'ignores': skipped, 'job': job,
            'message': '%d document(s) relancé(s) ; la couverture sera mise à jour après la lecture.' % len(targets[:100])}
