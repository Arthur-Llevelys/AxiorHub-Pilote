"""5.6.108 (lot R) : mode « chaîne » du banc d'essai de l'IA.

Le mode modèle historique rejoue la seule requête capturée : il ne mesure ni la barrière après rédaction ni la recherche faite par le
programme. Pour un arbre qui expose ``agent.references56108.produire`` (5.6.108 et suivantes), le mode chaîne appelle ce point d'entrée du
produit, qui enchaîne recherche d'office, besoins de recherche, rédaction, barrière et reprise :

- **transport du modèle injecté** : ``Relais`` relie ``model.Model._leaf`` du produit (méthode, politique, « think », nettoyage des sources :
  exactement l'appel réel) au client ``ollama_local.Ollama`` du banc (Ollama local, ou du réseau privé du cabinet par option explicite : mêmes
  gardes, aucun mandataire, adresse jamais recopiée) ; ``temperature`` 0, graine fixe, ``num_ctx`` égal à la place simulée ;
- **connecteurs factices** : ``ConnecteursCorpus`` cherche dans le champ fictif ``corpus`` de la situation (textes de loi et décisions inventés,
  marqués « texte fictif pour l'essai ») ; aucun vrai connecteur n'est appelé.

Mesures sur le **texte final** (après barrière et remplacement des numéros de sources), plus les compteurs de la barrière (références retirées,
reprises, marquées), le nombre d'appels au modèle et la durée totale. L'arbre 5.6.107 garde le rejeu simple.
"""
import json
import re
import time
import unicodedata

from . import mesures


def _plier(texte):
    t = unicodedata.normalize('NFKD', str(texte or '').lower())
    return ''.join(c for c in t if not unicodedata.combining(c))


def _num(valeur):
    return re.sub(r'[\s.]', '', str(valeur or '')).upper()


class ConnecteursCorpus:
    """Connecteurs factices : recherche déterministe dans le corpus fictif d'une situation. Aucun réseau."""

    def __init__(self, corpus, disponible=True):
        self.corpus = [c for c in corpus or [] if isinstance(c, dict)]
        self._dispo = disponible
        self.requetes = []

    def disponible(self):
        return (True, '') if self._dispo else (False, 'connecteurs factices désactivés')

    @staticmethod
    def _sortie(c):
        return {'reference': c['reference'], 'titre': c['titre'], 'texte': c['texte'], 'lien': c.get('lien', ''), 'statut': 'piste_a_verifier'}

    def chercher(self, besoin):
        self.requetes.append(dict(besoin))
        if besoin.get('type') == 'article':
            code = _plier(besoin.get('code'))
            return [self._sortie(c) for c in self.corpus if c['type'] == 'article' and _num(c.get('numero')) == _num(besoin.get('numero'))
                    and (not code or _plier(c.get('code')) == code)]
        if besoin.get('type') == 'decision' and besoin.get('numero'):
            return [self._sortie(c) for c in self.corpus if c['type'] == 'decision' and _num(c.get('numero')) == _num(besoin['numero'])]
        mots = {m for m in re.findall(r'[a-z0-9]{4,}', _plier(besoin.get('termes')))}
        if not mots:
            return []
        seuil = 1 if len(mots) == 1 else 2
        notes = []
        for i, c in enumerate(self.corpus):
            score = len(mots & set(re.findall(r'[a-z0-9]{4,}', _plier(c['titre'] + ' ' + c['texte']))))
            if score >= seuil:
                notes.append((-score, i, c))
        return [self._sortie(c) for _s, _i, c in sorted(notes)[:2]]


class Relais:
    """Transport HTTP du ``Model`` du produit vers le client Ollama du banc : la requête construite par le produit part telle quelle (sans flux),
    avec la graine fixe du banc ; « think » n'est envoyé qu'à un modèle qui déclare la réflexion (règle du produit et du banc)."""

    def __init__(self, client, graine=42):
        self.client, self.graine, self.appels = client, int(graine), []

    def json(self, method, path, body=None):
        if path != '/api/chat':
            raise ValueError('chemin non relayé : %s' % path)
        corps = json.loads(json.dumps(body or {}))
        corps['model'] = self.client.modele
        corps['stream'] = False
        options = dict(corps.get('options') or {})
        options.update(temperature=0, seed=self.graine)
        corps['options'] = options
        capacites = getattr(self.client, 'capacites', None)
        if 'think' in corps and capacites is not None and 'thinking' not in capacites:
            del corps['think']
        self.client.cadence.attendre()
        debut = time.monotonic()
        try:
            rep = self.client._post('/api/chat', corps)
        finally:
            self.client.cadence.noter()
        self.appels.append({'duree_s': round(time.monotonic() - debut, 2), 'jetons_entree': int((rep or {}).get('prompt_eval_count') or 0),
                            'jetons_sortie': int((rep or {}).get('eval_count') or 0), 'schema': 'format' in corps})
        return rep


class ModeleScripte:
    """Faux client pour les essais (aucun réseau) : réponses écrites à la main selon l'étape (besoins, reprise, rédaction)."""

    def __init__(self, besoins='{"recherches": []}', redaction='Réponse.', reprise=None, modele='faux-local:3b', parametres='3.2B'):
        self.modele, self.capacites, self.taille_parametres = modele, ['completion'], parametres
        self.besoins, self.redaction, self.reprise = besoins, redaction, reprise
        self.requetes = []

        class _Cadence:
            def attendre(self):
                pass

            def noter(self):
                pass
        self.cadence = _Cadence()

    def verifier(self):
        return None

    def _post(self, chemin, corps):
        self.requetes.append(corps)
        systeme = str((corps.get('messages') or [{}])[0].get('content') or '')
        if corps.get('format'):
            texte = self.besoins
        elif systeme.startswith('Tu relis un texte juridique'):
            texte = self.reprise if self.reprise is not None else self.redaction
        else:
            texte = self.redaction
        return {'done': True, 'done_reason': 'stop', 'message': {'role': 'assistant', 'content': texte}, 'prompt_eval_count': 100, 'eval_count': 20}


def modele_produit(client, place, graine):
    """``model.Model`` du produit, sans appel ``/api/show`` (déjà fait par le client du banc), relié au client par ``Relais``."""
    from agent.model import Model
    client.verifier()
    relais = Relais(client, graine)
    m = Model.__new__(Model)
    m.cfg = {'provider_type': 'ollama', 'provider_id': 'ollama', 'model': client.modele, 'url': 'http://127.0.0.1:11434', 'num_ctx': int(place),
             'context_mode': 'fixe', 'skill_instructions': '', 'keep_alive': '5m', 'disable_thinking': True}
    m.provider_type, m.provider_id = 'ollama', 'ollama'
    m.last_provider, m.last_model = 'ollama', client.modele
    m.http, m.context_limit = relais, None
    m.capabilities = getattr(client, 'capacites', None)
    m.parameter_size = str(getattr(client, 'taille_parametres', '') or '')
    m.usage_cost_usd, m.usage_cost_known = 0.0, True
    return m, relais


def executer(desk, situation, client, graine, numero, max_tokens):
    """Une situation par la chaîne du produit. Rend la sortie de ``produire`` (texte final, compteurs, sources numérotées) et les appels relayés."""
    from agent import competences5632 as c32
    from agent import references56108 as R
    from agent.common import Stop
    place = int(situation['place'])
    modele, relais = modele_produit(client, place, graine)
    droit = situation.get('droit') or {}
    args = {'instruction': situation['consigne'], 'matter': situation.get('dossier', ''), 'owner': 'cabinet'}
    if situation.get('livrable_produit'):
        args['document_type'] = situation['livrable_produit']
    row = {'id': numero, 'kind': situation['kind'], 'args': json.dumps(args), 'parent_job_id': None}
    from .travailleur import LIBELLES   # même cadrage que le rejeu : seules la chaîne et la méthode viennent du produit

    def appeler(messages, mt, schema=None):
        return modele._leaf(messages, 0, mt, schema)
    entree = {'consigne': situation['consigne'], 'sources': situation.get('sources') or [], 'max_tokens': max_tokens, 'droit': droit,
              'cadre': ('Tu travailles pour un cabinet d’avocats. Travail demandé : %s. Livrable attendu : %s. Réponds en français.'
                        % (LIBELLES.get(situation['kind'], situation['kind']), situation.get('livrable', ''))),
              'entete': 'Dossier : %s — juridiction : %s — droit applicable déclaré : %s' % (situation.get('dossier', ''), droit.get('juridiction', ''), droit.get('libelle', '')),
              'contexte': [situation.get('dossier', ''), 'RG %s' % droit['rg'] if droit.get('rg') else ''], 'cfg': modele.cfg | {'parameter_size': modele.parameter_size},
              'job_id': str(numero)}
    connecteurs = ConnecteursCorpus(situation.get('corpus') or [])
    debut = time.monotonic()
    with c32.for_job(desk, row, args):
        try:
            sortie = R.produire(desk, situation['kind'], entree, appeler, connecteurs)
        except Stop as erreur:
            return {'id': situation['id'], 'erreur': str(erreur), 'appels': relais.appels, 'duree_totale_s': round(time.monotonic() - debut, 2)}
    sortie.update(id=situation['id'], appels=relais.appels, recherches_corpus=connecteurs.requetes, duree_totale_s=round(time.monotonic() - debut, 2))
    return sortie


def mesurer(sortie, situation):
    """Mesures du banc sur le TEXTE FINAL ; les références permises sont celles des sources réellement fournies au modèle (pièces et textes
    trouvés par le programme). S'ajoutent les compteurs de la barrière, les appels au modèle et la durée totale."""
    vue = dict(situation, sources=[{'id': s['id'], 'titre': s.get('titre', ''), 'texte': '%s\n%s' % (s.get('reference', ''), s.get('texte', ''))}
                                   for s in sortie.get('sources') or []])
    texte = str(sortie.get('texte') or '')
    m = mesures.mesures_reponse(texte, vue)
    marquees = mesures.references(' \n '.join(sortie.get('refs_marquees') or []))
    c = sortie.get('compteurs') or {}
    appels = sortie.get('appels') or []
    m.update({
        'nb_inventees_non_marquees': len(set(m['references_inventees']) - marquees),
        'barriere_inconnues': int(c.get('inconnues', 0)), 'barriere_reprises': int(c.get('reprises', 0)), 'barriere_retirees': int(c.get('retirees', 0)),
        'barriere_marquees': int(c.get('marquees', 0)), 'sn_inconnus': int(c.get('sn_inconnus', 0)), 'sn_valides': int(c.get('sn_valides', 0)),
        'besoins': int(c.get('besoins', 0)), 'besoins_ignores': int(c.get('besoins_ignores', 0)), 'd_office': int(c.get('d_office', 0)),
        'recherches_programme': int(c.get('recherches', 0)), 'sources_ajoutees': int(c.get('sources_ajoutees', 0)),
        'appels_modele': int(sortie.get('appels_modele') or len(appels)), 'duree_totale_s': float(sortie.get('duree_totale_s') or 0),
        'duree_s': round(sum(a.get('duree_s', 0) for a in appels), 2), 'jetons_entree': sum(a.get('jetons_entree', 0) for a in appels),
        'jetons_sortie': sum(a.get('jetons_sortie', 0) for a in appels), 'fin': 'chaine', 'petit_modele': bool(sortie.get('petit_modele')),
        'texte_modele': str(sortie.get('texte_modele') or ''),
    })
    return m
