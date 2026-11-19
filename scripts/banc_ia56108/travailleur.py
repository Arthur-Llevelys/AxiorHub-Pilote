"""5.6.108 (lot E) : travailleur du banc d'essai de l'IA, lancé dans un sous-processus dont ``PYTHONPATH`` désigne l'arbre mesuré.

Il importe le paquet ``agent`` de CET arbre (5.6.107 en lecture seule, 5.6.108…), ouvre une base fictive temporaire, puis, pour chaque
situation, construit les instructions par le point d'entrée réel du produit :

1. ``competences5632.for_job(desk, row, args)`` (contexte d'un travail : vraie consigne, phase, compétences retenues, chargement) ;
2. ``model.Model._leaf`` avec un faux transport Ollama, qui capture la requête exacte qui partirait vers le fournisseur (assemblage dans la
   place réelle ``budget_chars``, nettoyage des sources, politique, refus « contexte insuffisant »). Aucun appel réseau.
3. si l'arbre l'expose (contrat du lot I, 5.6.108), ``competences5632.apercu_instructions(desk, kind, args, cfg)``.

Rien n'est écrit dans l'arbre mesuré : ``PYTHONDONTWRITEBYTECODE`` est posé par le lanceur, la base vit dans un dossier temporaire.
Usage (interne) : ``python travailleur.py <entrée.json> <sortie.json>``.
"""
import json
import os
import sys
import tempfile
import traceback
from pathlib import Path

MAX_TOKENS = {4000: 1000, 8000: 1500, 16000: 2000, 32000: 3000}
LIBELLES = {
    'prepare_reply': 'préparer une réponse à un courriel', 'assistant_answer': 'répondre à une question de l’avocat',
    'prepare_legal_opinion': 'préparer une consultation juridique', 'legal_research': 'faire une recherche juridique',
    'prepare_document_project': 'préparer un projet d’acte', 'attachment_review': 'analyser des pièces',
    'digest_pieces5632': 'analyser des pièces', 'prepare_hearing': 'préparer une audience', 'verify_deliverable420': 'contrôler un livrable',
    'analyser_dossier5624': 'analyser le dossier', 'fiche_dossier5627': 'établir la fiche du dossier', 'refresh_brief': 'mettre à jour la synthèse',
}


def max_tokens(place):
    return MAX_TOKENS.get(int(place), max(800, int(place) // 8))


def messages_de(situation):
    """Messages du banc : même cadrage pour tous les arbres. Seules les instructions de méthode viennent du produit."""
    droit = situation.get('droit') or {}
    systeme = ('Tu travailles pour un cabinet d’avocats. Travail demandé : %s. Livrable attendu : %s. Réponds en français.'
               % (LIBELLES.get(situation['kind'], situation['kind']), situation.get('livrable', '')))
    sources = '\n\n'.join('[%s] %s\n%s' % (s['id'], s['titre'], s['texte']) for s in situation.get('sources') or [])
    utilisateur = ('Consigne de l’avocat :\n%s\n\nDossier : %s — juridiction : %s — droit applicable déclaré : %s\n\nSources fournies :\n%s'
                   % (situation['consigne'], situation.get('dossier', ''), droit.get('juridiction', ''), droit.get('libelle', ''), sources or '(aucune)'))
    return [{'role': 'system', 'content': systeme}, {'role': 'user', 'content': utilisateur}]


class FauxTransport:
    """Faux transport Ollama : capture le corps de la requête ``/api/chat`` et répond « terminé », sans réseau."""

    def __init__(self):
        self.requetes = []

    def json(self, method, path, body=None):
        self.requetes.append({'method': method, 'path': path, 'body': body})
        return {'done': True, 'done_reason': 'stop', 'message': {'role': 'assistant', 'content': '(réponse du faux transport)'},
                'prompt_eval_count': 0, 'eval_count': 0}


def registre(situations):
    """Registre fictif des dossiers (``matters.json``) : un dossier par identifiant, avec son droit applicable et sa juridiction, là où le
    produit les lit (fiche du dossier) ; le registre ``dossiers5697`` est aussi semé (voir ``semer_dossier``)."""
    out = {}
    for s in situations:
        ident = s.get('dossier') or ''
        if not ident or ident in out:
            continue
        droit = s.get('droit') or {}
        out[ident] = {'id': ident, 'client_name': 'Client ' + str(s.get('client') or 'DURAND'), 'path': '/Dossiers/' + ident, 'references': [ident],
                      'aliases': [str(s.get('client') or 'DURAND')], 'correspondents': [{'email': 'client@example.test', 'role': 'client'}],
                      'droit_applicable': droit.get('libelle', ''), 'juridiction': droit.get('juridiction', '')}
    return list(out.values()) or [{'id': 'DOS-001', 'client_name': 'Client DURAND', 'path': '/Dossiers/DOS-001', 'references': ['DOS-001'],
                                   'aliases': ['DURAND'], 'correspondents': [{'email': 'client@example.test', 'role': 'client'}]}]


def config(base, situations=()):
    state = base / 'state'
    state.mkdir()
    matters = base / 'matters.json'
    matters.write_text(json.dumps(registre(situations), ensure_ascii=False), encoding='utf-8')
    return {'mode': 'drafts', 'state_dir': str(state), 'matters_file': str(matters),
            'mail': {'host': 'mail.example.test', 'username': 'cabinet@example.test', 'own_addresses': ['cabinet@example.test'], 'inbox': 'INBOX',
                     'sent': 'Sent', 'drafts': 'Drafts', 'from_name': 'Maître Exemple', 'from_address': 'cabinet@example.test', 'signature': 'Maître Exemple'},
            'nextcloud': {'roots': ['/Dossiers']}, 'documents': {}, 'ollama': {'model': 'banc-local'},
            'calendar': {'urls': ['/calendar/'], 'timezone': 'Europe/Paris', 'notice_hours': 0}, 'max_messages_per_run': 2}


def semer_dossier(desk, situation, deja):
    """Dossier fictif au registre (juridiction, nature) : un lot qui lit le droit du dossier dans le registre le trouve. Erreur : notée, pas bloquante."""
    droit = situation.get('droit') or {}
    ident = situation.get('dossier') or ''
    if not ident or ident in deja:
        return ''
    deja.add(ident)
    try:
        from agent import dossiers5697 as D
        from datetime import datetime, timezone
        D.ensure_schema(desk)
        maintenant = datetime.now(timezone.utc).isoformat()
        desk.db.execute('INSERT OR IGNORE INTO dossiers5697(id,cabinet_id,ref,ref_norm,titre,client,nature,juridiction,rg,resume,cree_le,modifie_le) '
                        'VALUES(?,?,?,?,?,?,?,?,?,?,?,?)',
                        (ident, 'cabinet', ident, ident.lower(), 'Dossier fictif ' + ident, situation.get('client', 'DURAND'),
                         droit.get('nature', 'a_qualifier'), droit.get('juridiction', ''), droit.get('rg', ''),
                         'Droit applicable déclaré : %s (%s).' % (droit.get('libelle', ''), droit.get('code', '')), maintenant, maintenant))
        desk.db.commit()
        return ''
    except Exception as erreur:   # noqa: BLE001 — table d'une autre version : le banc continue, l'écart est noté
        return 'dossier non semé : %s' % erreur


def _jsonable(value, depth=0):
    if depth > 6:
        return str(value)[:200]
    if isinstance(value, dict):
        return {str(k): _jsonable(v, depth + 1) for k, v in value.items()}
    if isinstance(value, (list, tuple, set)):
        return [_jsonable(v, depth + 1) for v in value]
    if isinstance(value, (str, int, float, bool)) or value is None:
        return value
    return str(value)


def capturer(desk, situation, numero):
    from agent import competences5632 as c32
    from agent.common import Stop
    from agent.model import Model
    place = int(situation['place'])
    args = {'instruction': situation['consigne'], 'matter': situation.get('dossier', ''), 'owner': 'cabinet'}
    if situation.get('livrable_produit'):
        args['document_type'] = situation['livrable_produit']
    row = {'id': numero, 'kind': situation['kind'], 'args': json.dumps(args), 'parent_job_id': None}
    out = {'id': situation['id'], 'kind': situation['kind'], 'place': place}
    diagnostic = ''
    try:   # diagnostic seulement : for_job avale ses propres erreurs (une compétence indisponible n'empêche pas le travail)
        from agent.competences5643 import real_instruction, select
        texte, livrable, phase = real_instruction(desk, situation['kind'], args)[:3]
        out['phase'] = phase
        select(desk, texte, phase, 'cabinet', kind=situation['kind'])
    except Exception as erreur:   # noqa: BLE001
        diagnostic = 'sélection en erreur : %s' % erreur
    messages = messages_de(situation)
    cfg = {'provider_type': 'ollama', 'provider_id': 'ollama', 'model': 'banc-local', 'url': 'http://127.0.0.1:11434', 'num_ctx': place,
           'context_mode': 'fixe', 'skill_instructions': '', 'keep_alive': '1m'}
    transport = FauxTransport()
    modele = Model.__new__(Model)
    modele.cfg = dict(cfg)
    modele.provider_type, modele.provider_id = 'ollama', 'ollama'
    modele.last_provider, modele.last_model = 'ollama', 'banc-local'
    modele.http, modele.context_limit, modele.capabilities = transport, None, ['completion']
    modele.usage_cost_usd, modele.usage_cost_known = 0.0, True
    with c32.for_job(desk, row, args) as selection:
        out['selection'] = [{'id': s.get('id'), 'version': s.get('version'), 'mode': s.get('mode'), 'reason': s.get('reason')} for s in selection or []]
        out['instructions_job'] = c32.current_instructions()
        try:
            modele._leaf(messages, 0, max_tokens(place), None)
            out['refus'] = ''
        except Stop as erreur:
            out['refus'] = str(erreur)
    corps = transport.requetes[-1]['body'] if transport.requetes else None
    envoyes = (corps or {}).get('messages') or []
    out['messages_envoyes'] = envoyes
    out['options'] = (corps or {}).get('options') or {}
    # Réglages de la requête du produit à rejouer tels quels (sans eux, un modèle « à réflexion » épuise sa limite et rend une réponse vide).
    out['requete'] = {k: corps[k] for k in ('think', 'format') if isinstance(corps, dict) and k in corps}
    # Méthode transmise : messages « système » ajoutés par le produit, hors cadrage du banc (premier message) et hors alerte du bouclier.
    systeme = [str(m.get('content') or '') for m in envoyes[1:] if m.get('role') == 'system']
    out['alerte_bouclier'] = any(t.startswith('ALERTE DE SÉCURITÉ') for t in systeme)
    out['methode'] = '\n\n'.join(t for t in systeme if not t.startswith('ALERTE DE SÉCURITÉ'))
    out['caracteres_envoyes'] = sum(len(str(m.get('content') or '')) for m in envoyes)
    out['diagnostic'] = diagnostic
    apercu = getattr(c32, 'apercu_instructions', None)
    if apercu:
        try:
            vue = _jsonable(apercu(desk, situation['kind'], args, dict(cfg, messages=messages, max_tokens=max_tokens(place))))
            if isinstance(vue, dict):   # le texte exact est déjà mesuré dans la requête capturée ; l'aperçu garde son détail
                texte = str(vue.pop('texte', '') or '')
                vue['caracteres_texte'] = len(texte)
                # Contrôle croisé : l'aperçu annonce-t-il exactement ce qui part vers le fournisseur (préfixe de politique mis à part) ?
                vue['concorde'] = bool(texte.strip()) == bool(out['methode'].strip()) and out['methode'].rstrip().endswith(texte.strip()[-2000:])
            out['apercu'] = vue
        except Exception as erreur:   # noqa: BLE001
            out['apercu'] = {'erreur': str(erreur)}
    return out


def principal(entree, sortie):
    demande = json.loads(Path(entree).read_text(encoding='utf-8'))
    resultats, erreurs = [], []
    with tempfile.TemporaryDirectory(prefix='banc-ia-') as tmp:
        base = Path(tmp)
        from agent.desk import Desk
        desk = Desk(config(base, demande['situations']))
        try:
            from agent import competences5632 as c32
            c32.ensure_builtin(desk)
            deja = set()
            for numero, situation in enumerate(demande['situations'], start=1001):
                note = semer_dossier(desk, situation, deja)
                try:
                    res = capturer(desk, situation, numero)
                    if note:
                        res['diagnostic'] = (res.get('diagnostic', '') + ' ; ' + note).strip(' ;')
                    resultats.append(res)
                except Exception as erreur:   # noqa: BLE001 — une situation en erreur n'arrête pas le banc
                    erreurs.append({'id': situation.get('id'), 'erreur': '%s: %s' % (type(erreur).__name__, erreur),
                                    'trace': traceback.format_exc()[-1500:]})
            paquet = paquet_assistant()
        finally:
            desk.db.close()
            try:   # bases SQLite fermées avant suppression (Windows)
                import gc
                gc.collect()
            except Exception:   # noqa: BLE001
                pass
    import agent
    Path(sortie).write_text(json.dumps({'arbre': str(Path(agent.__file__).resolve().parents[1]), 'version': getattr(agent, '__version__', ''),
                                        'resultats': resultats, 'erreurs': erreurs, 'paquet': paquet}, ensure_ascii=False), encoding='utf-8')


def paquet_assistant():
    """Textes du paquet « Assistant juridique » de l'arbre mesuré (pour reconnaître sections et fiches dans ce qui est transmis)."""
    from agent import competences5632 as c32
    base = c32.ROOT / 'assistant-juridique'
    out = {'fichiers': {}, 'manifest': {}}
    try:
        out['manifest'] = json.loads((base / 'manifest.json').read_text(encoding='utf-8'))
    except (OSError, ValueError):
        pass
    for chemin in sorted(base.rglob('*.md')):
        rel = chemin.relative_to(base).as_posix()
        if rel.startswith('origine/evals'):
            continue
        out['fichiers'][rel] = chemin.read_text(encoding='utf-8')
    return out


if __name__ == '__main__':
    os.environ.setdefault('PYTHONDONTWRITEBYTECODE', '1')
    sys.dont_write_bytecode = True
    try:
        principal(sys.argv[1], sys.argv[2])
    except Exception:   # noqa: BLE001
        traceback.print_exc()
        sys.exit(2)
