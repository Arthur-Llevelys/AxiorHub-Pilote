"""5.6.49 : recherche juridique AVANT chaque réponse — les connecteurs juridiques du cabinet (GoodLegal, OpenLegi, LibreJustice…)
sont interrogés avant que le modèle rédige, et ce qu'ils renvoient lui est donné comme source.

Jusqu'en 5.6.48, ces connecteurs servaient seulement à la page Recherche juridique, à la préparation d'audience et aux missions : une
question à l'agent, une note, des conclusions ou un courrier étaient rédigés sans les consulter, le modèle citant le droit de mémoire.

- **Où** : questions et documents (``docrequest520``), assistant (``workspace.chat``), réponses aux courriels reçus seulement quand le
  courriel pose une question de droit (``is_legal_question``).
- **Ce qui part** : une requête courte, anonymisée par ``legal_research.anonymize_query`` (noms du dossier, adresses, téléphones,
  IBAN, numéros RG) après retrait des noms propres (``neutral_query``). Aucune pièce, aucun courriel n'est transmis.
- **Ce que reçoit le modèle** : « sources_juridiques » (L1, L2…) avec leur statut. Seule une décision revérifiée sur la source
  officielle est « verifiee » ; tout le reste est une « piste_a_verifier », à citer avec la mention « (à vérifier) ».
- **Traçabilité** : chaque réponse garde le bilan de la recherche (connecteurs consultés, en échec, non utilisés et pourquoi) ; une
  référence citée dans le texte qui ne vient d'aucune source consultée est signalée « à vérifier » (``unsourced_references``).
- **Réglage** : Paramètres › Sources juridiques affiche, pour chaque connecteur, s'il est utilisé avant les réponses et sinon pourquoi.
"""
import hashlib
import json
import re
import time

from .common import Stop, fold

TTL = 3600
PURPOSES = ('legal_analysis', 'hearing')
MAX_VERIFIED = 6
MAX_LEADS = 8
RULE = ('« sources_juridiques » : textes et décisions trouvés par les connecteurs juridiques du cabinet AVANT la rédaction. Pour toute règle '
        'de droit, appuie-toi d’abord sur ces sources et cite leur identifiant (L1, L2…). Une source au statut « verifiee » se cite telle '
        'quelle ; une source « piste_a_verifier » ne se cite qu’avec la mention « (à vérifier) ». N’invente aucun article, aucune décision, '
        'aucune date ni aucun numéro : si aucune source ne couvre un point de droit, écris [À VÉRIFIER : fondement juridique de …]. Ce que '
        'tu crois savoir du droit sans source se signale comme à vérifier.')
STATUS_LABELS = {'consultees': 'sources consultées', 'aucune_source_active': 'aucun connecteur juridique utilisable',
                 'echec': 'recherche impossible', 'non_juridique': 'pas de question de droit'}
# Mots qui gardent leur majuscule dans une requête juridique (le reste des noms propres est retiré avant tout envoi).
KEEP = {'code', 'civil', 'commerce', 'travail', 'penal', 'procedure', 'consommation', 'cour', 'cass', 'cassation', 'conseil', 'etat',
        'constitutionnel', 'tribunal', 'judiciaire', 'administratif', 'appel', 'article', 'art', 'loi', 'decret', 'ordonnance', 'arrete',
        'directive', 'reglement', 'cedh', 'cjue', 'ue', 'france', 'republique', 'chambre', 'civ', 'com', 'soc', 'crim', 'ass', 'plen', 'mixte',
        'urssaf', 'tva', 'sci', 'sarl', 'sas', 'sa', 'eurl', 'snc', 'selarl', 'gie', 'cgi', 'cpc', 'cpp', 'csp', 'css', 'je', 'il', 'elle',
        'nous', 'vous', 'ils', 'le', 'la', 'les', 'un', 'une', 'des', 'quel', 'quelle', 'quels', 'quelles', 'peut', 'est', 'comment',
        'pourquoi', 'quand', 'que', 'qui', 'ou', 'dans', 'pour', 'sur', 'si', 'en', 'au', 'aux', 'du', 'de', 'ce', 'cette', 'mon', 'ma',
        'mes', 'notre', 'nos', 'leur', 'leurs', 'prepare', 'redige', 'fais', 'merci', 'bonjour', 'madame', 'monsieur', 'maitre', 'me'}
LEGAL_WORDS = re.compile(
    r"\b(articles?|art\.|code (civil|de commerce|du travail|de procedure|penal|de la consommation)|loi|decret|jurisprudence|cour de cassation|"
    r"cass\.?|arret|prescri\w*|forclos\w*|delai (d.appel|pour agir|de recours|de prescription|de preavis)|responsabilite|nullite|clause|"
    r"resiliation|resolution du contrat|indemni\w*|dommages.interets|prejudice|licenciement|preavis|garantie|vice cache|bail|loyer|"
    r"caution|recours|contester|contestation|obligation|droit de|mes droits|legal\w*|illegal\w*|recevab\w*|competen\w*)\b")
ASKING = re.compile(r"\?|\b(puis.je|peut.on|pouvons.nous|est.ce que|ai.je le droit|avons.nous le droit|quels sont|est.il possible|"
                    r"suis.je|sommes.nous|risque|que faire|comment faire|faut.il|doit.on|dois.je)\b")


# ------------------------------------------------------------------------------------------------ connecteurs
def connectors(desk):
    """Connecteurs juridiques déclarés : utilisé avant les réponses, ou raison lisible (même règle que la recherche elle-même)."""
    from .extensions364 import _legal_provider
    out = []
    for item in (desk.c.get('lawve_extensions') or {}).values():
        if not isinstance(item, dict) or item.get('kind') != 'connector':
            continue
        provider = _legal_provider(item)
        if not provider:
            continue
        reason = ''
        if not item.get('enabled'):
            reason = 'connecteur désactivé'
        elif not item.get('external_data_allowed'):
            reason = 'envoi de requêtes anonymisées non autorisé pour ce connecteur'
        elif (item.get('last_test') or {}).get('status') != 'ok':
            reason = 'test de connexion non réussi : refaites le test du connecteur'
        elif not set(PURPOSES).intersection(item.get('purposes') or []):
            reason = 'usage « analyse juridique » non coché pour ce connecteur'
        out.append({'id': str(item.get('id') or ''), 'name': str(item.get('name') or item.get('id') or provider), 'provider': provider,
                    'utilise': not reason, 'raison': reason})
    return sorted(out, key=lambda x: x['name'].lower())


# ------------------------------------------------------------------------------------------------ requête
def is_legal_question(text):
    """Courriel reçu : recherche seulement s'il pose une question de droit (terme juridique ET tournure interrogative)."""
    folded = fold(str(text or ''))
    return bool(LEGAL_WORDS.search(folded) and ASKING.search(folded))


def mail_question(subject, body, limit=700):
    """Courriel reçu : seules les phrases qui posent la question de droit partent (pas le courriel entier)."""
    sentences = re.split(r'(?<=[.?!])\s+|\n+', str(body or '')[:6000])
    keep = [s.strip() for s in sentences if s.strip() and ('?' in s or LEGAL_WORDS.search(fold(s)))]
    return (str(subject or '').strip() + ' — ' + ' '.join(keep))[:limit]


def neutral_query(text, limit=900):
    """Requête courte sans noms propres : les mots à majuscule hors vocabulaire juridique deviennent [NOM], les longs nombres [NUMERO].
    Une seconde anonymisation (noms du dossier, adresses, téléphones, IBAN, RG) suit dans ``legal_research.anonymize_query``."""
    text = re.sub(r'\s+', ' ', str(text or '')).strip()
    text = re.sub(r'\b[\w.+-]+@[\w-]+(\.[\w-]+)+\b', '[ADRESSE]', text)
    text = re.sub(r'\b\d[\d .]{5,}\d\b', '[NUMERO]', text)
    out = []
    for token in re.split(r'(\s+)', text):
        word = re.sub(r'^[^\w]+|[^\w]+$', '', token)
        if word and word[0].isupper() and fold(word) not in KEEP and not re.fullmatch(r'[LRD]\.?\d.*|\d.*', word):
            token = token.replace(word, '[NOM]')
        out.append(token)
    text = re.sub(r'(\[NOM\][\s,]*){2,}', '[NOM] ', ''.join(out)).strip()
    return text[:limit]


POLICY = '5651.1'   # 5.6.51 (audit, amélioration 6) : toute évolution de la politique de recherche invalide le cache


def _key(matter, query, connectors=()):
    """Cache d'une heure : dossier, requête, connecteurs utilisables et version de la politique."""
    used = ','.join(sorted(c['id'] for c in connectors if c.get('utilise')))
    return 'juridique5649:cache:' + hashlib.sha256(('%s|%s|%s|%s' % (POLICY, matter, query, used)).encode('utf-8')).hexdigest()[:40]


# ------------------------------------------------------------------------------------------------ recherche
def research(desk, matter_id, question, purpose='redaction', researcher=None):
    """Recherche avant rédaction. Ne lève jamais : renvoie un bilan dont « sources » (L1…) est donné au modèle."""
    state = connectors(desk)
    usable = [c for c in state if c['utilise']]
    base = {'statut': 'aucune_source_active', 'connecteurs': state, 'sources': [], 'verifiees': 0, 'pistes': 0, 'rapports': [],
            'requete': '', 'objet': purpose, 'quand': desk.now()}
    try:   # 5.6.52 (I03 à I05) : connexions juridiques directes vérifiées (Judilibre, Pappers Justice, Légifrance)
        from .connexions5652 import available_tools
        direct = any(t['outil'] in ('jurisprudence.rechercher', 'droit.rechercher_documentation', 'droit.rechercher_texte') for t in available_tools(desk, 'cabinet'))
    except Exception:   # noqa: BLE001
        direct = False
    if not usable and not direct:
        return base
    query = neutral_query(question)
    if len(query) < 8:
        return {**base, 'statut': 'echec', 'erreur': 'question_trop_courte'}
    key = _key(matter_id, query, state)
    cached = desk.settings(key)
    if isinstance(cached, dict) and time.time() - float(cached.get('at') or 0) < TTL and isinstance(cached.get('data'), dict):
        return {**cached['data'], 'connecteurs': state, 'cache': True}
    found = {'leads': [], 'verified_authorities': [], 'connectors': []}
    if usable:
        try:
            if researcher is None:
                from .legal_research import research_enabled_mcp as researcher
            found = researcher(desk, matter_id or '', query, MAX_LEADS)
        except Stop as ex:
            if not direct:
                return {**base, 'statut': 'echec', 'erreur': str(ex), 'requete': query}
        except Exception as ex:   # noqa: BLE001 — la rédaction continue, sans source, et le dit
            if not direct:
                return {**base, 'statut': 'echec', 'erreur': type(ex).__name__, 'requete': query}
    if direct:
        try:
            from .connexions5652 import legal_leads
            more, reports = legal_leads(desk, query)
            found = {**found, 'leads': list(found.get('leads') or []) + more, 'connectors': list(found.get('connectors') or []) + reports}
        except Exception:   # noqa: BLE001
            pass
    sources, seen = [], set()
    for v in (found.get('verified_authorities') or [])[:MAX_VERIFIED]:
        ref = str(v.get('identifier') or v.get('ecli') or '')
        seen.add(ref)
        sources.append({'id': 'L%d' % (len(sources) + 1), 'statut': 'verifiee', 'type': str(v.get('kind') or 'decision'),
                        'titre': str(v.get('title') or '')[:300], 'reference': ref[:200], 'juridiction': str(v.get('court') or '')[:200],
                        'date': str(v.get('date') or '')[:40], 'extrait': str(v.get('exact_excerpt') or '')[:1500],
                        'url': str(v.get('official_url') or '')[:500]})
    for lead in found.get('leads') or []:
        ref = str(lead.get('identifier') or lead.get('ecli') or '')
        if ref and ref in seen:
            continue
        text = str(lead.get('exact_excerpt') or lead.get('summary') or '').strip()
        if not (text or lead.get('title')):
            continue
        seen.add(ref or lead.get('title'))
        sources.append({'id': 'L%d' % (len(sources) + 1), 'statut': 'piste_a_verifier', 'type': 'piste', 'titre': str(lead.get('title') or '')[:300],
                        'reference': ref[:200], 'juridiction': str(lead.get('court') or '')[:200], 'date': str(lead.get('date') or '')[:40],
                        'extrait': text[:900], 'fournisseur': str(lead.get('provider') or ''),
                        'url': str(lead.get('official_url') or lead.get('provider_url') or '')[:500]})
        if len(sources) >= MAX_VERIFIED + MAX_LEADS:
            break
    reports = [{**{k: str(r.get(k) or '') for k in ('connector', 'provider', 'status', 'error')}, 'resultats': int(r.get('leads') or 0)}
               for r in (found.get('connectors') or [])]
    answered = any(r['status'] in ('ok', 'completed', 'partial') for r in reports)
    data = {**base, 'statut': 'consultees' if answered else 'echec', 'sources': sources, 'rapports': reports, 'requete': found.get('anonymized_query') or query,
            'verifiees': sum(1 for s in sources if s['statut'] == 'verifiee'), 'pistes': sum(1 for s in sources if s['statut'] != 'verifiee')}
    if not answered:
        data['erreur'] = 'aucun_connecteur_n_a_repondu'
    try:
        desk.setting(key, {'at': time.time(), 'data': {k: v for k, v in data.items() if k != 'connecteurs'}})
    except Exception:   # noqa: BLE001 — cache facultatif
        pass
    desk.audit('juridique5649_recherche', {'matter': matter_id or '', 'objet': purpose, 'statut': data['statut'], 'verifiees': data['verifiees'],
                                           'pistes': data['pistes'], 'connecteurs': [r['provider'] for r in reports]})
    return data


def for_model(result):
    """Ce qui est donné au modèle : sources (L1…) et consigne ; rien si aucune recherche n'a abouti."""
    if not result or not result.get('sources'):
        return {}
    return {'sources_juridiques': [{k: s[k] for k in ('id', 'statut', 'type', 'titre', 'reference', 'juridiction', 'date', 'extrait')}
                                   for s in result['sources']],
            'regle_sources_juridiques': RULE}


def as_sources(result):
    """Même contenu, au format des sources de l'assistant et des réponses aux courriels (identifiant, nature, extrait)."""
    return [{'id': s['id'], 'kind': 'source_juridique_verifiee' if s['statut'] == 'verifiee' else 'piste_juridique_a_verifier',
             'title': s['titre'], 'reference': s['reference'], 'court': s['juridiction'], 'date': s['date'], 'excerpt': s['extrait'],
             'partial': s['statut'] != 'verifiee'} for s in (result or {}).get('sources', [])]


# ------------------------------------------------------------------------------------------------ contrôle et bilan
def _article_keys(text):
    """(code canonique, numéro normalisé) des articles d'un texte, y compris la forme « Code X, article N » (code avant l'article)."""
    from .refs470 import ALIAS_RE, canonical_code, extract_articles
    text = str(text or '')
    keys = set()
    for ref in extract_articles(text):
        code = ref.get('code') or ''
        if not code:   # « Code de procédure civile, article 700 » : code cité juste avant l'article
            window = text[max(0, ref['start'] - 90):ref['start']]
            found = list(re.finditer(ALIAS_RE, window, re.I))
            if found:
                code = canonical_code(found[-1].group(0)) or ''
        keys.add((code, ref['number']))
    return keys


def _decision_keys(text):
    from .refs470 import extract_decisions
    keys = set()
    for ref in extract_decisions(str(text or '')):
        for k in ('pourvoi', 'rg', 'ce_number', 'cc_number'):
            if ref.get(k):
                keys.add(fold(str(ref[k])).replace(' ', ''))
    for ecli in re.findall(r'ECLI:[A-Z]{2}:[A-Z0-9]+:\d{4}:[A-Z0-9.]+', str(text or ''), re.I):
        keys.add(ecli.upper())
    return keys


def unsourced_references(text, result):
    """Références citées dans le texte qui ne correspondent à AUCUNE source consultée (5.6.51, audit B03) :
    - un article correspond seulement au même (code, numéro) — le même numéro dans un autre code ne suffit pas ;
    - un article cité sans code est « à vérifier » (identité incomplète) ;
    - une décision correspond par son identifiant officiel (pourvoi, RG, numéro, ECLI), jamais par sa seule date ;
    - contrôle impossible (analyseur en échec) : un signalement explicite, jamais une liste vide.
    Une correspondance de référence n'atteste pas que la source soutient l'affirmation."""
    try:
        from .refs470 import extract
        refs = extract(str(text or ''))
        articles, decisions = set(), set()
        for s in (result or {}).get('sources', []):
            blob = ' \n '.join(str(s.get(k) or '') for k in ('titre', 'reference', 'extrait'))
            articles |= _article_keys(blob)
            decisions |= _decision_keys(blob)
            if s.get('reference'):
                decisions.add(fold(str(s['reference'])).replace(' ', ''))
                decisions.add(str(s['reference']).upper())
        cited = _article_keys(str(text or ''))
    except Exception:   # noqa: BLE001
        return ['contrôle des références incomplet : vérifiez chaque référence citée']
    out = []
    for ref in refs:
        raw = str(ref.get('raw') or '').strip()
        if not raw:
            continue
        if ref.get('kind') == 'article':
            match = [k for k in cited if k[1] == ref['number'] and (not ref.get('code') or k[0] == ref['code'])]
            code = (match[0][0] if match else ref.get('code')) or ''
            if not code:
                out.append(raw[:160] + ' (code non précisé)')
                continue
            if (code, ref['number']) not in articles:
                out.append(raw[:160])
        else:
            idents = {fold(str(ref.get(k))).replace(' ', '') for k in ('pourvoi', 'rg', 'ce_number', 'cc_number') if ref.get(k)}
            if not idents or not (idents & decisions):
                out.append(raw[:160] + ('' if idents else ' (identifiant officiel absent)'))
    return list(dict.fromkeys(out))[:12]


def summary(result):
    """Une ligne pour la fiche de contrôle et les limites de l'assistant."""
    if not result:
        return ''
    used = [c['name'] for c in result.get('connecteurs', []) if c['utilise']]
    unused = ['%s (%s)' % (c['name'], c['raison']) for c in result.get('connecteurs', []) if not c['utilise']]
    statut = result.get('statut')
    if statut == 'aucune_source_active':
        line = 'Recherche juridique avant rédaction : aucun connecteur utilisable' + ((' — ' + ' ; '.join(unused)) if unused else ' (aucun configuré)') + '.'
    elif statut == 'echec':
        line = 'Recherche juridique avant rédaction impossible (%s) : le droit cité est à vérifier.' % (result.get('erreur') or 'erreur')
    elif statut == 'non_juridique':
        return ''
    else:
        failed = [r['provider'] for r in result.get('rapports', []) if r['status'] not in ('ok', 'completed', 'partial')]
        line = 'Recherche juridique avant rédaction : %s consulté(s) ; %d décision(s) vérifiée(s) sur la source officielle, %d piste(s) à vérifier.' % (
            ', '.join(used) or 'connecteurs', result.get('verifiees', 0), result.get('pistes', 0))
        if failed:
            line += ' Sans réponse : %s.' % ', '.join(failed)
        if unused:
            line += ' Non utilisé(s) : %s.' % ' ; '.join(unused)
    return line


def panel_html(desk, prefix):
    """Paramètres › Sources juridiques : connecteurs utilisés avant chaque réponse, sinon pourquoi."""
    from html import escape as e
    rows = ''.join('<tr><td>%s</td><td>%s</td><td>%s</td></tr>' % (e(c['name']), e(c['provider']),
                                                                   'Oui' if c['utilise'] else e('Non — ' + c['raison'])) for c in connectors(desk))
    last = desk.db.execute("SELECT at,data FROM audit WHERE action='juridique5649_recherche' ORDER BY id DESC LIMIT 1").fetchone()
    info = ''
    if last:
        try:
            d = json.loads(last['data'])
            info = '<p>Dernière recherche avant rédaction : %s — %s, %d vérifiée(s), %d piste(s).</p>' % (
                e(str(last['at'])[:16].replace('T', ' ')), e(STATUS_LABELS.get(d.get('statut'), d.get('statut', ''))), int(d.get('verifiees') or 0),
                int(d.get('pistes') or 0))
        except (ValueError, TypeError):
            info = ''
    return ('<section class="vf-panel" id="avant-reponse"><h2>Recherche avant chaque réponse</h2>'
            '<p>Avant une question à l’agent, un document ou une réponse de l’assistant (et avant la réponse à un courriel qui pose une question '
            'de droit), AxiorHub interroge les connecteurs juridiques ci-dessous avec une requête anonymisée, sans nom ni pièce, et donne au '
            'modèle les textes et décisions trouvés.</p>%s'
            '<table class="vf-table"><thead><tr><th>Connecteur</th><th>Fournisseur</th><th>Utilisé avant les réponses</th></tr></thead><tbody>%s</tbody></table>'
            '<p class="vf-note">Un connecteur s’active, se teste et reçoit l’usage « analyse juridique » dans <a href="%s">Connecteurs MCP</a>.</p></section>') % (
        info, rows or '<tr><td colspan="3">Aucun connecteur juridique déclaré.</td></tr>', e(prefix + '/mcp', quote=True))
