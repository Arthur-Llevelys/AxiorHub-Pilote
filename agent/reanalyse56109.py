"""5.6.109 (lot A, F03) : « Analyser le dossier » depuis la fiche du dossier — commande, suivi, couverture, comparaison avant remplacement.

Rien de parallèle : la commande est le travail existant ``analyser_dossier5624`` (``dossier5624.request_analysis`` : une campagne et un travail
par dossier, pause, annulation et reprise du moteur des travaux) ; la couverture est celle que ``dossier5624`` conserve (une ligne par pièce
et par courriel éligible) ; les informations du dossier restent dans le registre des faits (``legal_memory``) et ses propres commandes.

- **Bloc de la fiche** (``bloc_fiche_html``, monté par ``fiche_ui5698``) : état réel, bouton « Analyser le dossier » (puis « Réanalyser »),
  dialogue simple avant lancement (ce qui sera lu, ce qui sera envoyé à quelle IA, durée et budget ; « Actualiser ce qui a changé » par défaut,
  « Analyse complète » en option), suivi (travail, étapes, couverture, attentes), « Arrêter l'analyse », listes de la couverture.
  Afficher la fiche ne lance rien et n'écrit rien (R05) ; tout part d'un POST protégé (Origin, CSRF, corps borné, clé d'idempotence).
- **Comparaison avant remplacement** (``apres_campagne``, ``decider``) : à la fin d'une campagne, ce que l'analyse voudrait changer à une
  information VALIDÉE par le cabinet devient une proposition (ancienne information, proposition, justification, sources) à décider :
  Accepter, Conserver l'ancienne, Modifier ou Demander une précision. Aucune information validée n'est remplacée sans cette décision ; la
  décision contrôle la révision attendue (une modification faite entre-temps rend un conflit, rien n'est perdu) ; l'historique du registre
  garde chaque version. La nouvelle synthèse du modèle est montrée avec ses changements et ses sources ; elle reste une proposition du modèle.
- **Droits** : dossier absent ou réservé à d'autres : même 404 neutre, avant tout compteur, extrait ou nom de pièce ; registre des droits
  illisible : rien n'est montré ; lancer, arrêter et décider : administrateur et avocat (comme « fiche/analyse » et « fact/validate »).
- **IA en pause** : la consultation (état, couverture, comparaison, décisions) reste possible ; une demande d'analyse est conservée et dite
  « en attente », avec son motif.
"""
from html import escape as e
import hashlib
import json
from pathlib import PurePosixPath
import re
import sqlite3
import uuid

from .common import Stop
from . import acces5697
from . import dossier5624 as D

PREFIXE = 'm56109/reanalyse/'
LECTURES = ('etat', 'couverture', 'propositions')
ECRITURES = ('lancer', 'arreter', 'decider')
ROLES_ACTION = ('administrateur', 'avocat')
MAX_POST = 20_000
CLE = re.compile(r'^[A-Za-z0-9_.:-]{8,120}$')
ACTIONS = {'accepter': 'Accepter', 'conserver': 'Conserver l’ancienne', 'modifier': 'Modifier', 'precision': 'Demander une précision'}
OUVERTES = ('a_valider', 'precision')
ETATS = {'a_valider': 'à valider', 'precision': 'précision demandée', 'acceptee': 'acceptée', 'conservee': 'ancienne information conservée',
         'modifiee': 'modifiée par l’avocat', 'vue': 'vue', 'caduque': 'remplacée par une analyse plus récente'}
GENRES = {'preuve': 'Information validée dont la pièce a changé', 'successeur': 'Autre valeur lue dans les pièces', 'synthese': 'Nouvelle synthèse du dossier'}
UNIQUES = {'jurisdiction': 'Juridiction', 'case_number': 'Numéro de RG'}     # une seule valeur de référence par dossier : une autre valeur lue est un successeur proposé
CHAMPS_SYNTHESE = (('parties', 'Parties'), ('claims', 'Demandes'), ('jurisdiction', 'Juridiction'), ('case_number', 'Numéro de RG'),
                   ('latest_instructions', 'Dernières instructions'), ('open_questions', 'Questions ouvertes'))

SCHEMA = '''
CREATE TABLE IF NOT EXISTS reanalyse56109_ops(acteur TEXT NOT NULL, cle TEXT NOT NULL, nature TEXT NOT NULL, empreinte TEXT NOT NULL,
  etat TEXT NOT NULL, resultat TEXT NOT NULL DEFAULT '{}', cree_le TEXT NOT NULL, maj_le TEXT NOT NULL, PRIMARY KEY(acteur, cle));
CREATE TABLE IF NOT EXISTS reanalyse56109_propositions(id TEXT PRIMARY KEY, matter TEXT NOT NULL, campagne INTEGER NOT NULL, genre TEXT NOT NULL,
  cible TEXT NOT NULL, cible_revision INTEGER NOT NULL DEFAULT 0, type TEXT NOT NULL DEFAULT '', ancien TEXT NOT NULL DEFAULT '{}',
  propose TEXT NOT NULL DEFAULT '{}', justification TEXT NOT NULL DEFAULT '', sources TEXT NOT NULL DEFAULT '[]', etat TEXT NOT NULL DEFAULT 'a_valider',
  version INTEGER NOT NULL DEFAULT 1, decide_par TEXT NOT NULL DEFAULT '', decide_le TEXT NOT NULL DEFAULT '', motif TEXT NOT NULL DEFAULT '',
  cree_le TEXT NOT NULL, maj_le TEXT NOT NULL);
CREATE INDEX IF NOT EXISTS reanalyse56109_propositions_dossier ON reanalyse56109_propositions(matter, etat);
'''
STATUTS = {'introuvable': '404 Not Found', 'role_insuffisant': '403 Forbidden', 'origine_ou_csrf_refuse': '403 Forbidden',
           'version_perimee': '409 Conflict', 'information_modifiee': '409 Conflict', 'deja_decidee': '409 Conflict', 'cle_reutilisee': '409 Conflict',
           'operation_en_cours': '409 Conflict', 'operation_deja_terminee': '409 Conflict', 'requete_trop_longue': '413 Payload Too Large',
           'controle_acces_indisponible': '503 Service Unavailable'}
MESSAGES = {
    'introuvable': 'Dossier introuvable, ou action non permise sur ce dossier.',
    'role_insuffisant': 'Votre rôle ne permet pas de lancer une analyse ni de décider d’un changement.',
    'origine_ou_csrf_refuse': 'Session expirée ou origine refusée : rechargez la page.',
    'version_perimee': 'Cette proposition a changé entre-temps : rechargez la page. Rien n’a été modifié.',
    'information_modifiee': 'L’information a été modifiée entre-temps (par vous ou sur un autre poste) : la comparaison a été mise à jour avec la '
                            'version actuelle. Relisez-la avant de décider ; rien n’a été remplacé.',
    'deja_decidee': 'Cette proposition a déjà reçu une décision.',
    'cle_reutilisee': 'Cette demande a déjà servi pour une autre action : rechargez la page.',
    'cle_requise': 'Demande incomplète : rechargez la page.',
    'operation_en_cours': 'Cette action est déjà en cours : patientez puis rechargez la page.',
    'operation_deja_terminee': 'L’analyse est déjà terminée : rien à arrêter.',
    'aucune_analyse_en_cours': 'Aucune analyse en cours ni en attente pour ce dossier.',
    'action_invalide': 'Décision inconnue.',
    'texte_requis': 'Pour « Modifier », saisissez le texte corrigé.',
    'motif_requis': 'Indiquez la précision demandée.',
    'requete_trop_longue': 'Demande trop longue.',
    'controle_acces_indisponible': 'Contrôle des accès indisponible : réessayez dans un instant. Rien n’a été affiché ni modifié.',
    'automatismes_en_pause': 'IA en pause : la demande n’a pas pu être enregistrée.',
    'file_attente_pleine': 'La file des travaux est pleine : réessayez dans un moment.',
}


# ------------------------------------------------------------------------------------------------ schéma, droits, idempotence
def ensure_schema(desk):
    """Démarrage (``Desk._prepare``) : tables de l'analyse (campagnes, couverture : ``dossier5624``) et du lot. Sans changement : aucune écriture."""
    D.ensure_schema(desk)
    noms = re.findall(r'CREATE\s+(?:TABLE|INDEX)\s+IF\s+NOT\s+EXISTS\s+(\w+)', SCHEMA)
    existants = {r[0] for r in desk.db.execute('SELECT name FROM sqlite_master')}
    if any(n not in existants for n in noms):
        if desk.db.in_transaction:
            desk.db.commit()
        desk.db.executescript(SCHEMA)
        desk.db.commit()


def message(code):
    return MESSAGES.get(code, 'Demande refusée.')


def _acces(desk, acteur, mid):
    """Acteur normalisé et dossier, ou le même 404 neutre (absent, réservé, registre illisible) — avant tout compteur ou nom de pièce."""
    mid = str(mid or '')[:80]
    try:
        a = acces5697.exiger(desk, acteur, dossier_id=mid)
        m = D._matter(desk, mid)
    except Stop as ex:
        if str(ex) == 'controle_acces_indisponible':
            raise
        raise Stop('introuvable') from None
    return a, m


def executer(desk, compte, cle, nature, parametres, fn):
    """Une commande = un effet : même clé et mêmes paramètres → même reçu ; autre usage de la clé → ``cle_reutilisee`` (409)."""
    cle = str(cle or '')
    if not CLE.match(cle):
        raise Stop('cle_requise')
    empreinte = hashlib.sha256(json.dumps({'n': nature, 'p': parametres}, sort_keys=True, ensure_ascii=False, default=str).encode()).hexdigest()
    row = desk.db.execute('SELECT empreinte, etat, resultat FROM reanalyse56109_ops WHERE acteur=? AND cle=?', (compte, cle)).fetchone()
    if row:
        if row[0] != empreinte:
            raise Stop('cle_reutilisee')
        if row[1] == 'fait':
            return {**json.loads(row[2]), 'rejoue': True}
        if row[1] == 'echec':
            raise Stop(json.loads(row[2]).get('error') or 'operation_echouee')
        raise Stop('operation_en_cours')
    quand = desk.now()
    desk.db.execute('INSERT INTO reanalyse56109_ops(acteur,cle,nature,empreinte,etat,resultat,cree_le,maj_le) VALUES(?,?,?,?,?,?,?,?)',
                    (compte, cle, nature, empreinte, 'en_cours', '{}', quand, quand))
    desk.db.commit()
    try:
        out = fn()
    except BaseException as ex:
        if desk.db.in_transaction:
            desk.db.rollback()
        code = str(ex).split(':')[0] if isinstance(ex, Stop) else 'erreur_interne'
        desk.db.execute("UPDATE reanalyse56109_ops SET etat='echec', resultat=?, maj_le=? WHERE acteur=? AND cle=?", (json.dumps({'error': code}), desk.now(), compte, cle))
        desk.db.commit()
        raise
    desk.db.execute("UPDATE reanalyse56109_ops SET etat='fait', resultat=?, maj_le=? WHERE acteur=? AND cle=?",
                    (json.dumps(out, ensure_ascii=False, default=str), desk.now(), compte, cle))
    desk.db.commit()
    return out


# ------------------------------------------------------------------------------------------------ commande et suivi
def lancer(desk, acteur, mid, mode, cle):
    """POST « Analyser le dossier » : la campagne et son travail (une seule commande vivante par dossier ; un double clic la retrouve)."""
    a, m = _acces(desk, acteur, mid)
    if a.role not in ROLES_ACTION:
        raise Stop('role_insuffisant')
    mode = mode if mode in D.MODES else 'changements'

    def faire():
        out = D.request_analysis(desk, m['id'], None, mode, par=a.compte, cle=cle)
        desk.audit('reanalyse56109_lancee', {'matter': m['id'], 'par': a.compte, 'mode': mode, 'travail': out.get('job_id'), 'deja': bool(out.get('already'))})
        if not out.get('already'):
            out['message'] = 'Analyse du dossier lancée (travail n° %d, %s). Le suivi s’affiche dans la fiche.' % (out['job_id'], D.MODES[mode].lower())
        return out
    return executer(desk, a.compte, cle, 'lancer', {'d': m['id'], 'mode': mode}, faire)


def arreter(desk, acteur, mid, cle):
    """POST « Arrêter l'analyse » : arrêt du travail par le moteur (confirmé par le travail lui-même ; rien de déjà écrit n'est défait)."""
    a, m = _acces(desk, acteur, mid)
    if a.role not in ROLES_ACTION:
        raise Stop('role_insuffisant')

    def faire():
        vivant = D._pending_job(desk, m['id']) or D._waiting_job(desk, m['id'])
        if not vivant:
            raise Stop('aucune_analyse_en_cours')
        jid = vivant['id'] if 'id' in vivant.keys() else vivant['job']
        res = desk.cancel_job(jid)
        desk.audit('reanalyse56109_arret', {'matter': m['id'], 'par': a.compte, 'travail': jid, 'etat': res.get('status')})
        return {'job_id': jid, 'status': res.get('status'),
                'message': 'Analyse annulée avant son démarrage.' if res.get('status') == 'cancelled'
                else 'Arrêt demandé : l’étape en cours s’interrompt, ce qui a déjà été lu est conservé.'}
    return executer(desk, a.compte, cle, 'arreter', {'d': m['id']}, faire)


def etat(desk, acteur, mid):
    """GET : état réel de l'analyse (lecture seule) : travail, étapes, couverture, attentes, propositions ouvertes."""
    a, m = _acces(desk, acteur, mid)
    st = D.state(desk, m['id'])
    return {'dossier': m['id'], 'etat': st['overall'], 'libelle': st['overall_label'], 'travail': st['job'], 'arret_demande': st['arret_demande'],
            'attente': st['attente'], 'derniere_reussite': st['last_success'], 'campagne': st['campagne'],
            'etapes': [{'etape': s['step'], 'libelle': s['label'], 'etat': s['state'], 'etat_libelle': s['state_label'], 'perimee': s['fresh'] is False,
                        'motif': s['error']} for s in st['steps']],
            'couverture': st['couverture'], 'propositions': len(propositions(desk, m['id']))}


def propositions(desk, mid, etats=OUVERTES, limite=100):
    try:
        rows = desk.db.execute('SELECT * FROM reanalyse56109_propositions WHERE matter=? AND etat IN (%s) ORDER BY cree_le DESC, id LIMIT ?' % ','.join('?' * len(etats)),
                               (mid, *etats, int(limite))).fetchall()
    except sqlite3.Error:
        return []
    out = []
    for r in rows:
        d = dict(r)
        for k in ('ancien', 'propose', 'sources'):
            try:
                d[k] = json.loads(d[k] or ('[]' if k == 'sources' else '{}'))
            except ValueError:
                d[k] = [] if k == 'sources' else {}
        d['etat_libelle'], d['genre_libelle'] = ETATS.get(d['etat'], d['etat']), GENRES.get(d['genre'], d['genre'])
        out.append(d)
    return out


# ------------------------------------------------------------------------------------------------ comparaison avant remplacement
def _vue(r):
    """Ce qu'on montre d'une information du registre (jamais plus que son intitulé, son contenu, sa date, son état et ses sources)."""
    try:
        snap = json.loads(r['source_snapshot']) if isinstance(r['source_snapshot'], str) else (r['source_snapshot'] or [])
    except (ValueError, TypeError):
        snap = []
    return {'record': r['id'], 'type': r['record_type'], 'titre': str(r['title'] or '')[:500], 'contenu': str(r['content'] or '')[:4000],
            'date': str(r['event_date'] or ''), 'etat': r['status'], 'revision': int(r['revision'] or 0),
            'sources': [{'path': str(s.get('path') or ''), 'extrait': str(s.get('excerpt') or '')[:300], 'modifie': str(s.get('modified') or '')}
                        for s in snap[:6] if isinstance(s, dict)]}


def _proposer(desk, mid, numero, genre, cible, cible_revision, type_, ancien, propose, justification, sources, quand):
    """Proposition ouverte pour cette cible : une seule à la fois (une analyse plus récente rend l'ancienne caduque, jamais deux décisions
    contradictoires en attente). Rend 1 si une proposition a été créée."""
    empreinte = hashlib.sha256(json.dumps([mid, genre, cible, propose], sort_keys=True, ensure_ascii=False, default=str).encode()).hexdigest()[:32]
    if desk.db.execute('SELECT 1 FROM reanalyse56109_propositions WHERE id=?', (empreinte,)).fetchone():
        return 0      # même changement déjà proposé (ou déjà décidé) : pas reproposé à chaque analyse
    desk.db.execute("UPDATE reanalyse56109_propositions SET etat='caduque', maj_le=?, version=version+1 WHERE matter=? AND genre=? AND cible=? AND etat IN ('a_valider','precision')",
                    (quand, mid, genre, cible))
    desk.db.execute('INSERT INTO reanalyse56109_propositions(id,matter,campagne,genre,cible,cible_revision,type,ancien,propose,justification,sources,cree_le,maj_le) '
                    'VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?)', (empreinte, mid, int(numero or 0), genre, cible, int(cible_revision or 0), type_,
                                                        json.dumps(ancien, ensure_ascii=False), json.dumps(propose, ensure_ascii=False), justification[:1000],
                                                        json.dumps(sources, ensure_ascii=False), quand, quand))
    return 1


def apres_campagne(desk, mid, numero):
    """Fin d'une campagne, DANS le travail (jamais pendant une lecture) : ce que l'analyse changerait à une information validée par le
    cabinet devient une proposition à décider. Rien n'est remplacé ici. Rend le nombre de propositions créées."""
    from .common import fold
    from .legal_memory import ensure_schema as lm_schema
    ensure_schema(desk)
    lm_schema(desk)
    camp = D._campagne(desk, mid, numero)
    if not camp:
        return 0
    depuis, quand, n = camp['cree_le'], desk.now(), 0
    # 1. validation suspendue parce que la pièce source a changé (5.6.24, F33) : l'ancienne version validée est dans l'historique
    for h in desk.db.execute("SELECT h.record_id, h.data FROM legal_memory_history h JOIN legal_memory_records r ON r.id=h.record_id WHERE r.matter=? "
                             "AND h.reason='preuve_modifiee_validation_suspendue' AND h.changed_at>=? AND r.status='suggested' ORDER BY h.id", (mid, depuis)).fetchall():
        r = desk.db.execute('SELECT * FROM legal_memory_records WHERE id=?', (h['record_id'],)).fetchone()
        try:
            ancien = json.loads(h['data'])
        except ValueError:
            continue
        nouvelle = _vue(r)
        n += _proposer(desk, mid, numero, 'preuve', r['id'], r['revision'], r['record_type'], _vue({**ancien, 'id': r['id']}), nouvelle,
                       'La pièce qui prouvait cette information a changé depuis votre validation : la validation est suspendue jusqu’à votre décision.',
                       nouvelle['sources'], quand)
    # 2. autre valeur lue pour une information à valeur unique déjà validée (juridiction, numéro de RG)
    for type_ in UNIQUES:
        valides = desk.db.execute("SELECT * FROM legal_memory_records WHERE matter=? AND record_type=? AND status IN ('validated','pinned') ORDER BY updated DESC",
                                  (mid, type_)).fetchall()
        nouveaux = desk.db.execute("SELECT * FROM legal_memory_records WHERE matter=? AND record_type=? AND status='suggested' AND created>=? ORDER BY created",
                                   (mid, type_, depuis)).fetchall()
        for v in valides[:1]:
            for s in nouveaux[:5]:
                if fold(str(s['content'])) == fold(str(v['content'])):
                    continue
                vue = _vue(s)
                n += _proposer(desk, mid, numero, 'successeur', v['id'], v['revision'], type_, _vue(v), vue,
                               'Une pièce lue par cette analyse donne une autre valeur que celle que vous avez validée.', vue['sources'], quand)
    # 3. nouvelle synthèse du modèle : changements par rubrique, sources de la fiche
    fiches = desk.db.execute('SELECT version, data, sources, created FROM case_briefs WHERE matter=? ORDER BY version DESC LIMIT 2', (mid,)).fetchall()
    if len(fiches) == 2 and str(fiches[0]['created']) >= depuis:
        try:
            neuve, avant = json.loads(fiches[0]['data']), json.loads(fiches[1]['data'])
            sources = [{'path': str(s.get('path') or ''), 'extrait': str(s.get('excerpt') or '')[:200], 'modifie': str(s.get('modified') or '')}
                       for s in json.loads(fiches[0]['sources'] or '[]') if isinstance(s, dict) and s.get('path')][:12]
        except (ValueError, TypeError):
            neuve = avant = None
        if isinstance(neuve, dict) and isinstance(avant, dict):
            liste = lambda v: [str(x) for x in (v if isinstance(v, list) else ([v] if v else []))]
            changements = {}
            for champ, _ in CHAMPS_SYNTHESE:
                a, b = liste(avant.get(champ)), liste(neuve.get(champ))
                retires, ajoutes = [x for x in a if x not in b][:20], [x for x in b if x not in a][:20]
                if retires or ajoutes:
                    changements[champ] = {'retires': retires, 'ajoutes': ajoutes}
            if changements:
                n += _proposer(desk, mid, numero, 'synthese', 'fiche', fiches[0]['version'], 'synthese',
                               {'version': fiches[1]['version'], 'changements': {k: v['retires'] for k, v in changements.items()}},
                               {'version': fiches[0]['version'], 'changements': {k: v['ajoutes'] for k, v in changements.items()}},
                               'Nouvelle synthèse calculée par le modèle sur %d source(s) : proposition du modèle, elle ne remplace aucune information validée.' % len(sources),
                               sources, quand)
    desk.db.commit()
    return n


def decider(desk, acteur, mid, pid, action, version, texte='', motif='', cle=''):
    """POST : décision de l'avocat sur une proposition. Accepter, Conserver l'ancienne, Modifier (texte corrigé) ou Demander une précision.
    La révision de l'information visée est contrôlée : modifiée entre-temps, la comparaison est rafraîchie et un conflit est rendu (409)."""
    a, m = _acces(desk, acteur, mid)
    if a.role not in ROLES_ACTION:
        raise Stop('role_insuffisant')
    if action not in ACTIONS:
        raise Stop('action_invalide')
    texte, motif = str(texte or '').strip()[:8000], str(motif or '').strip()[:1000]
    if action == 'modifier' and not texte:
        raise Stop('texte_requis')
    if action == 'precision' and not motif:
        raise Stop('motif_requis')

    def faire():
        from .legal_memory import change_record
        p = desk.db.execute('SELECT * FROM reanalyse56109_propositions WHERE id=? AND matter=?', (str(pid or ''), m['id'])).fetchone()
        if not p:
            raise Stop('introuvable')
        if p['etat'] not in OUVERTES:
            raise Stop('deja_decidee')
        if str(version) != str(p['version']):
            raise Stop('version_perimee')
        quand = desk.now()
        if action == 'precision':
            nouvel = 'precision'
        elif p['genre'] == 'synthese':
            nouvel = 'vue'
        else:
            rec = desk.db.execute('SELECT * FROM legal_memory_records WHERE id=? AND matter=?', (p['cible'], m['id'])).fetchone()
            if not rec or int(rec['revision']) != int(p['cible_revision']):      # modifiée entre-temps : conflit montré, rien n'est remplacé
                if rec:
                    champ = 'propose' if p['genre'] == 'preuve' else 'ancien'
                    desk.db.execute('UPDATE reanalyse56109_propositions SET %s=?, cible_revision=?, version=version+1, maj_le=? WHERE id=?' % champ,
                                    (json.dumps(_vue(rec), ensure_ascii=False), int(rec['revision']), quand, p['id']))
                else:
                    desk.db.execute("UPDATE reanalyse56109_propositions SET etat='caduque', version=version+1, maj_le=? WHERE id=?", (quand, p['id']))
                desk.db.commit()
                raise Stop('information_modifiee')
            note = {'accepter': 'Proposition de l’analyse n° %d acceptée par l’avocat', 'conserver': 'Ancienne information conservée par l’avocat (analyse n° %d)',
                    'modifier': 'Corrigé par l’avocat (analyse n° %d)'}[action] % p['campagne'] + ((' : ' + motif) if motif else '')
            try:
                if p['genre'] == 'preuve':        # même information, preuve relue : la validation revient par décision, jamais d'office
                    change_record(desk, 'validate_memory', {'record': rec['id'], 'matter': m['id'], 'revision': rec['revision'], 'note': note,
                                                           **({'text': texte} if action == 'modifier' else {})})
                else:                             # successeur : l'information validée n'est archivée que si son successeur est accepté
                    successeur = str((json.loads(p['propose']) or {}).get('record') or '')
                    s = desk.db.execute('SELECT * FROM legal_memory_records WHERE id=? AND matter=?', (successeur, m['id'])).fetchone()
                    if not s:
                        raise Stop('information_modifiee')
                    if action == 'conserver':
                        change_record(desk, 'dispute_memory', {'record': s['id'], 'matter': m['id'], 'revision': s['revision'], 'note': note})
                    else:
                        change_record(desk, 'validate_memory', {'record': s['id'], 'matter': m['id'], 'revision': s['revision'], 'note': note,
                                                               **({'text': texte} if action == 'modifier' else {})})
                        change_record(desk, 'archive_memory', {'record': rec['id'], 'matter': m['id'], 'revision': rec['revision']})
                        desk.db.execute('UPDATE legal_memory_records SET supersedes=? WHERE id=?', (rec['id'], s['id']))   # lien de remplacement
            except Stop as ex:
                if desk.db.in_transaction:
                    desk.db.rollback()
                if str(ex) == 'fait_modifie_entre_temps_recharger':
                    raise Stop('information_modifiee') from None
                raise
            nouvel = {'accepter': 'acceptee', 'conserver': 'conservee', 'modifier': 'modifiee'}[action]
        cur = desk.db.execute('UPDATE reanalyse56109_propositions SET etat=?, decide_par=?, decide_le=?, motif=?, version=version+1, maj_le=? WHERE id=? AND version=?',
                              (nouvel, a.compte, quand, motif, quand, p['id'], p['version']))
        if cur.rowcount != 1:
            desk.db.rollback()
            raise Stop('version_perimee')
        desk.db.commit()
        desk.audit('reanalyse56109_decision', {'matter': m['id'], 'proposition': p['id'], 'genre': p['genre'], 'action': action, 'par': a.compte})
        return {'proposition': p['id'], 'etat': nouvel,
                'message': {'acceptee': 'Proposition acceptée : l’information du dossier est à jour, l’ancienne version reste dans l’historique.',
                            'conservee': 'Ancienne information conservée : la proposition est écartée et ne sera pas reproposée.',
                            'modifiee': 'Information corrigée et validée ; l’ancienne version reste dans l’historique.',
                            'precision': 'Précision demandée : la proposition reste ouverte, rien n’a été modifié.',
                            'vue': 'Changements de la synthèse marqués comme vus.'}[nouvel]}
    return executer(desk, a.compte, cle, 'decider', {'d': m['id'], 'p': str(pid), 'a': action, 'v': str(version), 't': texte, 'm': motif}, faire)


# ------------------------------------------------------------------------------------------------ rendu dans la fiche
def _cle():
    return uuid.uuid4().hex


def _date(valeur):
    v = str(valeur or '')[:16].replace('T', ' ')
    return ('%s/%s/%s %s' % (v[8:10], v[5:7], v[0:4], v[11:16])).strip() if len(v) >= 10 else ''


def destination_ia(desk):
    """Fragment HTML : à quel modèle la synthèse est envoyée (réglage du cabinet relu, aucun appel). Le nom du modèle et celui du service
    sont des données, mises à part du texte."""
    try:
        from .model import routed_config
        cfg = routed_config(desk.c, 'attachment_review')
        nom, genre = str(cfg.get('model') or ''), str(cfg.get('provider_type') or cfg.get('type') or '')
        if not nom:
            raise Stop('modele_non_configure')
        if genre == 'ollama':
            return '<span>au modèle local</span> <code>%s</code> <span>(sur ce poste ou le serveur du cabinet ; rien ne sort).</span>' % e(nom)
        return ('<span>au modèle</span> <code>%s</code> <span>du service externe</span> <code>%s</code><span>, selon la politique des données du cabinet '
                '(pseudonymisation comprise si elle est activée).</span>') % (e(nom), e(str(cfg.get('provider_id') or genre or '?')))
    except Exception:   # noqa: BLE001 — réglage illisible ou IA non configurée : on le dit, on n'invente pas
        return '<span>au modèle choisi dans Paramètres › IA (réglage illisible pour le moment : la synthèse sera refusée plutôt qu’envoyée ailleurs).</span>'


def _source_lien(prefix, mid, s):
    from .fiche460 import doc_href
    chemin = str(s.get('path') or '')
    if not chemin:
        return ''
    nom = PurePosixPath(chemin).name or chemin
    if chemin.startswith('/'):
        return '<a class="d79-doc" href="%s" data-d79-doc="%s">%s</a>' % (e(prefix + doc_href(chemin, mid), quote=True), e(chemin, quote=True), e(nom))
    return e(nom)


def _info_html(v):
    if 'changements' in v:
        lignes = ''.join('<li><strong>%s</strong> : %s</li>' % (e(dict(CHAMPS_SYNTHESE).get(k, k)), e(' ; '.join(x[:200] for x in vals)))
                         for k, vals in v['changements'].items() if vals)
        return '<p class="ax-muted">Synthèse v%s</p><ul class="a109-liste">%s</ul>' % (e(str(v.get('version', ''))), lignes or '<li class="ax-muted">rien de propre à cette version</li>')
    return '<p><strong>%s</strong>%s</p><p>%s</p>' % (e(v.get('titre') or ''), (' <span class="ax-muted">%s</span>' % e(v['date'])) if v.get('date') else '',
                                                     e(v.get('contenu') or ''))


def comparaison_html(desk, prefix, mid, peut_decider):
    """Changements à décider : ancienne information, proposition, justification, sources ; décision par formulaire protégé."""
    props = propositions(desk, mid)
    if not props:
        return ''
    cartes = []
    for p in props:
        sources = ' · '.join(x for x in (_source_lien(prefix, mid, s) for s in p['sources'][:6]) if x) or '<span class="ax-muted">source non précisée</span>'
        formulaire = ''
        if peut_decider:
            synthese = p['genre'] == 'synthese'
            boutons = ('<button class="ax-btn" type="submit" data-extra="action=accepter">Marquer comme vue</button>' if synthese else
                       '<button class="ax-btn" type="submit" data-extra="action=accepter">Accepter</button>'
                       '<button class="ax-btn ghost" type="submit" data-extra="action=conserver">Conserver l’ancienne</button>'
                       '<button class="ax-btn ghost" type="submit" data-extra="action=modifier">Modifier</button>')
            boutons += '<button class="ax-btn ghost" type="submit" data-extra="action=precision">Demander une précision</button>'
            formulaire = ('<form class="m5-form a109-decision" data-api="%sdecider" data-reload><input type="hidden" name="d" value="%s">'
                          '<input type="hidden" name="proposition" value="%s"><input type="hidden" name="version" value="%d"><input type="hidden" name="cle" value="%s">%s'
                          '<label class="a109-champ">Motif, ou précision demandée<input name="motif" maxlength="1000" autocomplete="off"></label>'
                          '<p class="a109-actions">%s</p></form>') % (
                PREFIXE, e(mid, quote=True), e(p['id'], quote=True), int(p['version']), _cle(),
                '' if synthese else '<label class="a109-champ">Texte corrigé (pour « Modifier »)<textarea name="texte" rows="2" maxlength="8000"></textarea></label>', boutons)
        cartes.append(
            '<article class="a109-prop" id="a109-p-%s"><h4><span>%s</span>%s <span class="a109-badge a109-b-%s">%s</span></h4><dl class="a109-compare">'
            '<div><dt>Ancienne information</dt><dd>%s</dd></div><div><dt>Proposition</dt><dd>%s</dd></div>'
            '<div><dt>Ce qui justifie le changement</dt><dd>%s%s</dd></div><div><dt>Sources</dt><dd>%s</dd></div></dl>%s</article>' % (
                e(p['id'][:12], quote=True), e(GENRES.get(p['genre'], p['genre'])), (' — <span>%s</span>' % e(UNIQUES[p['type']])) if p['type'] in UNIQUES else '',
                e(p['etat'], quote=True), e(p['etat_libelle']), _info_html(p['ancien']), _info_html(p['propose']), e(p['justification']),
                (' <span class="ax-muted">Précision demandée :</span> <q>%s</q>' % e(p['motif'])) if p['etat'] == 'precision' and p['motif'] else '', sources, formulaire))
    return ('<section class="a109-comparaison" aria-labelledby="a109-t-comp"><h3 id="a109-t-comp">Changements à décider <span class="a109-compte">%d</span></h3>'
            '<p class="ax-muted">L’analyse ne remplace jamais une information que le cabinet a validée : elle propose, vous décidez. L’ancienne version '
            'reste dans l’historique du dossier.</p>%s</section>') % (len(props), ''.join(cartes))


def _liste_couverture(desk, mid, titre, genre, etat, ouvert=False, filtre=None):
    lignes = D.couverture_liste(desk, mid, genre, etat, 120)['elements']
    if filtre:
        lignes = [x for x in lignes if filtre(x)]
    if not lignes:
        return ''
    return '<details class="a109-liste-etat"%s><summary><span>%s</span> (%d%s)</summary><ul class="a109-liste">%s</ul></details>' % (
        ' open' if ouvert else '', e(titre), len(lignes), '+' if len(lignes) >= 120 else '',
        ''.join('<li><span class="a109-nom">%s</span>%s</li>' % (e(x['nom'] or x['ref']), (' — <span class="ax-muted">%s</span>' % e(x['motif_libelle'] or x['motif'])) if x['motif'] else '')
                for x in lignes))


def couverture_html(desk, mid, st):
    """Fichiers présents, indexés, analysés et à vérifier, avec leurs motifs — d'après les états conservés du parcours, jamais un texte de l'IA."""
    c = st['couverture']
    if not c['definie']:
        return '<p class="ax-muted">Couverture pas encore relevée : elle le sera à la prochaine analyse (état de chaque pièce et de chaque courriel).</p>'
    p, m = c['pieces'], c['courriels']
    compteurs = ''.join('<li><strong>%d</strong> %s</li>' % (n, e(t)) for n, t in (
        (p['total'], 'pièce(s) présente(s)'), (p['traite'], 'indexée(s)'), (p['faits'], 'lue(s) par le repérage des faits'),
        (p['modele'], 'transmise(s) au modèle pour la synthèse'), (p['en_erreur'] + p['partiels'] + p['en_attente'], 'à vérifier'), (p['exclu'], 'exclue(s)'),
        (m['total'], 'courriel(s) rattaché(s)'), (m['traite'], 'courriel(s) indexé(s)'), (m['en_erreur'] + m['en_attente'], 'courriel(s) à vérifier')))
    out = '<ul class="a109-compteurs">%s</ul>' % compteurs
    if not c['inventaire_complet']:
        out += '<p class="a109-avis" role="note">Inventaire du répertoire incomplet : des fichiers n’ont pas encore été atteints. Relancez l’analyse pour poursuivre.</p>'
    out += '<p class="ax-muted">Une pièce indexée n’est pas pour autant lue par le modèle : la synthèse repose sur des extraits choisis, listés ci-dessous.</p>'
    out += _liste_couverture(desk, mid, 'Pièces à vérifier', 'piece', 'en_erreur', True) + _liste_couverture(desk, mid, 'Pièces en attente', 'piece', 'en_attente', True)
    out += _liste_couverture(desk, mid, 'Pièces lues en partie', 'piece', 'traite', False, lambda x: x['partiel'])
    out += _liste_couverture(desk, mid, 'Courriels à vérifier', 'courriel', 'en_erreur') + _liste_couverture(desk, mid, 'Pièces exclues', 'piece', 'exclu')
    out += _liste_couverture(desk, mid, 'Pièces transmises au modèle pour la synthèse', 'piece', 'traite', False, lambda x: x['modele'])
    return out


def _etapes_html(st):
    return '<ol class="a109-etapes">%s</ol>' % ''.join(
        '<li class="a109-etape a109-s-%s" data-a109-etape="%s"><span class="a109-nom">%s</span> <span class="a109-badge" data-a109-etat-etape>%s</span>%s%s</li>' % (
            e(s['state'], quote=True), e(s['step'], quote=True), e(s['label']), e(s['state_label']),
            ' <span class="a109-badge a109-b-perimee">périmée</span>' if s['fresh'] is False else '',
            (' <span class="ax-muted">%s</span>' % e(D.motif_lisible(s['error'])[:160])) if s.get('error') else '') for s in st['steps'])


def bloc_fiche_html(desk, acteur, prefix, m, complet=True):
    """Bloc « Analyse du dossier » de la fiche (lecture seule ; les commandes sont des formulaires POST). ``complet`` faux : bandeau court
    (état et lien vers le bloc complet de l'onglet Fichiers)."""
    from urllib.parse import urlencode
    a, m = _acces(desk, acteur, m['id'])
    mid = m['id']
    st = D.state(desk, mid)
    props = len(propositions(desk, mid))
    lien = prefix + '/dossier?' + urlencode({'id': mid, 'vue': 'fichiers'}) + '#a109-analyse'
    etat_html = '<span class="a109-etat a109-e-%s" data-a109-etat>%s</span>' % (e(st['overall'], quote=True), e(st['overall_label']))
    if not complet:
        return ('<p class="a109-bandeau" role="note"><strong>Analyse du dossier</strong> %s%s <a class="ax-btn ghost" href="%s">%s</a></p>' % (
            etat_html, (' <a href="%s">%d changement(s) à décider</a>' % (e(lien, quote=True), props)) if props else '', e(lien, quote=True),
            'Voir l’analyse' if st['overall'] != 'jamais' else 'Analyser le dossier'))
    peut = a.role in ROLES_ACTION
    actif = st['job'] or (st['attente'] or {}).get('job')
    resume = []
    if st['job']:
        resume.append('Travail n° %d %s' % (st['job'], 'en cours d’arrêt (confirmation attendue)' if st['arret_demande'] else 'en file ou en cours'))
    if st['attente']:
        resume.append('Demande conservée (travail n° %d) : %s' % (st['attente']['job'], st['attente']['libelle']))
    if st['last_success']:
        resume.append('dernière étape réussie le %s' % _date(st['last_success']))
    if st['campagne']:
        resume.append('analyse n° %d (%s)' % (st['campagne']['numero'], D.MODES.get(st['campagne']['mode'], st['campagne']['mode']).lower()))
    actions = ''
    if peut and not actif:
        from .pause5629 import is_paused
        pause = ''
        try:
            if desk.settings('missions567:pause', False) or is_paused(desk, mid):
                pause = ('<p class="a109-avis" role="note"><strong>IA en pause.</strong> La demande sera enregistrée et conservée en attente : elle démarrera à la '
                         'reprise de l’IA, sans nouvelle demande. La consultation de l’analyse précédente reste possible.</p>')
        except Exception:   # noqa: BLE001 — réglage illisible : le moteur des travaux refera le contrôle
            pause = ''
        c = st['couverture']
        connu = ('%d pièce(s) et %d courriel(s) connus à la dernière analyse.' % (c['pieces']['total'], c['courriels']['total'])) if c['definie'] else 'L’inventaire sera fait au lancement.'
        actions = (
            '<details class="a109-lancer" data-a109-lancer><summary class="ax-btn">%s</summary><form class="m5-form a109-form" data-api="%slancer" data-reload>'
            '<input type="hidden" name="d" value="%s"><input type="hidden" name="cle" value="%s">'
            '<h3>Avant de lancer</h3><dl class="a109-avant">'
            '<div><dt>Ce qui sera lu</dt><dd><span>Le répertoire du dossier</span>%s <span>et ses sous-répertoires, ainsi que les courriels rattachés au dossier.</span> '
            '<span>%s</span> <span>Sources exclues : fichiers cachés, formats non pris en charge, fichiers trop volumineux, copies de lecture des courriels '
            'déjà conservés.</span></dd></div>'
            '<div><dt>Ce qui sera envoyé à l’IA</dt><dd><span>L’indexation, le repérage des faits et la chronologie se font sur ce poste, sans IA. '
            'Seule la synthèse envoie des extraits choisis des pièces :</span> %s</dd></div>'
            '<div><dt>Durée et budget</dt><dd>Travail en arrière-plan, par lots bornés (%d lots par passage) : un gros dossier demande plusieurs passages, '
            'sans bloquer le reste. Un appel au modèle pour la synthèse, compté dans les plafonds de budget du cabinet. Vous pouvez arrêter à tout moment.</dd></div></dl>%s'
            '<fieldset class="a109-mode"><legend>Étendue</legend>'
            '<label><input type="radio" name="mode" value="changements" checked> Actualiser ce qui a changé <span class="ax-muted">(les étapes dont rien n’a changé ne sont pas refaites)</span></label>'
            '<label><input type="radio" name="mode" value="complete"> Analyse complète <span class="ax-muted">(tout est relu, la synthèse est recalculée)</span></label></fieldset>'
            '<p class="a109-actions"><button class="ax-btn" type="submit">Lancer l’analyse</button></p></form></details>') % (
            'Analyser le dossier' if st['overall'] == 'jamais' else 'Réanalyser le dossier', PREFIXE, e(mid, quote=True), _cle(),
            (' <code>%s</code>' % e(str(m.get('path') or ''))) if m.get('path') else '', e(connu), destination_ia(desk), D.LOTS_INDEXATION, pause)
    elif peut and actif:
        actions = ('<form class="m5-form a109-form" data-api="%sarreter" data-reload data-confirm="Arrêter l’analyse de ce dossier ? Ce qui a déjà été lu est conservé.">'
                   '<input type="hidden" name="d" value="%s"><input type="hidden" name="cle" value="%s">'
                   '<button class="ax-btn ghost" type="submit"%s>Arrêter l’analyse</button></form>') % (
            PREFIXE, e(mid, quote=True), _cle(), ' disabled' if st['arret_demande'] else '')
    return ('<section class="a109 d79-card" id="a109-analyse" data-a109 data-a109-api="%s" data-a109-dossier="%s" data-a109-actif="%s" aria-labelledby="a109-t">'
            '<div class="d79-card-head"><h2 id="a109-t">Analyse du dossier</h2>%s</div>'
            '<p class="a109-resume" role="status" aria-live="polite" data-a109-resume>%s</p>%s'
            '<details class="a109-suivi"%s><summary>Étapes de l’analyse</summary>%s</details>'
            '<details class="a109-couverture" id="a109-couverture"%s><summary>Ce qui a été lu (couverture)</summary>%s</details>%s'
            '<script defer src="%s/static/v56109-reanalyse.js"></script></section>') % (
        e(prefix + '/api440/' + PREFIXE, quote=True), e(mid, quote=True), '1' if actif else '', etat_html,
        e(' · '.join(resume)) or 'Aucune analyse lancée pour ce dossier.', actions, ' open' if actif or st['overall'] in ('partielle', 'en_cours') else '',
        _etapes_html(st), ' open' if st['overall'] == 'partielle' and not actif else '', couverture_html(desk, mid, st),
        comparaison_html(desk, prefix, mid, peut), e(prefix, quote=True))


def montage_fiche(desk, acteur, prefix, m, complet=True):
    """Point de montage de ``fiche_ui5698`` : le bloc, ou rien (présentation précédente, dossier non visible, lecture momentanément impossible).
    Ne lève jamais : un défaut du bloc n'empêche pas l'affichage de la fiche."""
    try:
        from .interface56100 import active
        compte = acces5697.acteur_de(acteur).compte
        if not active(desk, compte):
            return ''
        return bloc_fiche_html(desk, acteur, prefix, m, complet)
    except Stop:
        return ''
    except Exception:   # noqa: BLE001
        import logging
        logging.getLogger(__name__).exception('reanalyse56109 : bloc de la fiche')
        return '<p class="ax-muted">Analyse du dossier : affichage momentanément indisponible.</p>' if complet else ''


def statiques():
    return {'/static/v56109-reanalyse.js': 'text/javascript', '/static/v56109-reanalyse.css': 'text/css'}


# ------------------------------------------------------------------------------------------------ routes
def allowed(role, method, api):
    """``standalone_auth.allowed`` : liste fermée. Lectures : tout rôle authentifié (le dossier est relu par le service) ; écritures :
    administrateur et avocat."""
    reste = str(api or '').split('?', 1)[0]
    reste = reste[len(PREFIXE):].strip('/') if reste.startswith(PREFIXE) else ''
    if method in ('GET', 'HEAD'):
        return reste in LECTURES
    return method == 'POST' and reste in ECRITURES and role in ROLES_ACTION


def _json(data, status='200 OK'):
    return {'status': status, 'kind': 'application/json; charset=utf-8', 'body': json.dumps(data, ensure_ascii=False, default=str),
            'headers': [('Cache-Control', 'private, no-store')]}


def api(env, desk, auth, prefix, name, args, method):
    from .web567 import actor
    acteur = actor(env)
    reste = str(name).split('?', 1)[0][len(PREFIXE):].strip('/')
    try:
        if reste not in LECTURES + ECRITURES:
            raise Stop('introuvable')
        if not allowed(acteur[1], method, name):
            raise Stop('role_insuffisant' if method == 'POST' and reste in ECRITURES else 'introuvable')
        if method in ('GET', 'HEAD'):
            mid = str(args.get('d') or '')
            if reste == 'etat':
                return _json(etat(desk, acteur, mid))
            a, m = _acces(desk, acteur, mid)
            if reste == 'couverture':
                return _json(D.couverture_liste(desk, m['id'], str(args.get('genre') or ''), str(args.get('etat') or ''), 200, str(args.get('apres') or '')[:1000]))
            return _json({'propositions': propositions(desk, m['id'])})
        try:
            longueur = int(env.get('CONTENT_LENGTH') or 0)
        except ValueError:
            longueur = MAX_POST + 1
        if longueur > MAX_POST:
            raise Stop('requete_trop_longue')
        from .web440 import check_post
        d = check_post(env, auth)
        mid, cle = str(d.get('d') or ''), d.get('cle')
        if reste == 'lancer':
            return _json(lancer(desk, acteur, mid, str(d.get('mode') or ''), cle))
        if reste == 'arreter':
            return _json(arreter(desk, acteur, mid, cle))
        return _json(decider(desk, acteur, mid, d.get('proposition'), str(d.get('action') or ''), d.get('version'), d.get('texte'), d.get('motif'), cle))
    except Stop as ex:
        code = str(ex).split(':')[0]
        if code in ('dossier_reserve', 'dossier_absent', 'acteur_requis'):
            code = 'introuvable'
        return _json({'error': code, 'message': message(code)}, STATUTS.get(code, '422 Unprocessable Entity'))
