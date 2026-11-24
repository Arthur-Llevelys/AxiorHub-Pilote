#!/usr/bin/env python3
"""5.6.109 (lot K, F07) : banc de la décision locale — comparaison de campagnes JSONL, exécution volontaire sur un Ollama local.

  # comparer deux campagnes déjà mesurées sur les MÊMES cas (aucun modèle, aucun réseau)
  python scripts/banc-decision56109.py comparer --cas tests/decision56109-cas-amorcage.jsonl --reference avant.jsonl --candidat apres.jsonl --sortie comparaison.json

  # exécuter les cas sur le daemon Ollama du poste (génère réellement ; jamais lancé par les tests ni par la recette)
  python scripts/banc-decision56109.py executer --cas tests/decision56109-cas-amorcage.jsonl --modele NOM_DU_MODELE_LOCAL --sortie campagne.jsonl --je-confirme-l-appel-ollama

« executer » n'admet qu'une adresse de boucle locale (127.0.0.1 ou ::1), ne télécharge aucun modèle, refuse un modèle délégué à un service
distant (/api/show avant toute donnée) et refuse d'écraser une campagne existante. Chaque ligne produite : id, decision (statut, intention,
action…), duree_ms. Les adaptateurs Skejja ou AnyJev, s'ils sont développés, produisent le même JSONL.

Limites : les 24 cas fournis sont synthétiques (amorces à valider par l'avocat), pas un jeu d'évaluation réservé ; le comparateur donne le taux
de décisions correctes, la couverture, les erreurs techniques, la médiane et le p95 des durées vues du client. Il ne mesure ni la mémoire ou la
VRAM du daemon, ni la fidélité juridique, ni les questions répétées, ni le temps de correction de l'avocat. Ne jamais fabriquer une campagne de
référence pour revendiquer un gain : mesurer réellement le parcours actuel sur les mêmes cas.
"""
import argparse
from dataclasses import asdict
import json
import math
from pathlib import Path
import statistics
import sys
import time

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from agent.decision56109 import FENETRE, SCHEMA, SORTIE_MAX, SYSTEME, Contexte, CoucheDecision, ErreurMoteur   # noqa: E402
import ipaddress   # noqa: E402
from urllib.parse import urlsplit   # noqa: E402
from urllib.request import HTTPRedirectHandler, ProxyHandler, Request, build_opener   # noqa: E402

# Transport autonome de la référence, réservé à ce banc (hors du produit : le produit n'appelle le modèle que par ``agent.model.Model``).


class _SansRedirection(HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        raise ErreurMoteur('redirection_interdite')


class OllamaLocal:
    """Transport autonome de la référence, pour le banc ``scripts/banc-decision56109.py`` sur demande explicite : boucle locale IPv4/IPv6,
    sans proxy ni redirection, /api/show avant toute donnée, budget global de temps, lecture bornée. Le produit, lui, passe par ``MoteurOllama``."""
    nom, local = 'ollama', True

    def __init__(self, modele, url='http://127.0.0.1:11434', delai=8.0, *, horloge=time.monotonic, ouvrir=None):
        parsed = urlsplit(url)
        try:
            adresse = ipaddress.ip_address(parsed.hostname or '')
            port = parsed.port
        except ValueError:
            raise ValueError('adresse_locale_requise') from None
        if (not adresse.is_loopback or parsed.scheme != 'http' or parsed.username or parsed.password or parsed.query or parsed.fragment
                or parsed.path not in ('', '/') or not port):
            raise ValueError('adresse_locale_requise')
        if not modele or len(modele) > 160 or any(ord(x) < 32 for x in modele):
            raise ValueError('modele_invalide')
        if not 0.1 <= delai <= 15:
            raise ValueError('delai_invalide')
        self.modele, self.url = modele, url.rstrip('/')
        self.delai, self.horloge = float(delai), horloge
        self.ouvrir = ouvrir or build_opener(ProxyHandler({}), _SansRedirection()).open

    def _ouvrir(self, path, payload, fin, checkpoint):
        checkpoint()
        restant = fin - self.horloge()
        if restant <= 0:
            raise ErreurMoteur('budget_temps_depasse')
        req = Request(self.url + path, data=json.dumps(payload).encode('utf-8'), headers={'Content-Type': 'application/json'}, method='POST')
        return self.ouvrir(req, timeout=min(2.0, restant))

    def _lire(self, response, fin, checkpoint, limite=65536):
        total = 0
        while True:
            checkpoint()
            if self.horloge() >= fin:
                raise ErreurMoteur('budget_temps_depasse')
            chunk = response.read1(min(4096, limite - total + 1))
            if not chunk:
                break
            total += len(chunk)
            if total > limite:
                raise ErreurMoteur('reponse_http_trop_grande')
            yield chunk

    def decider(self, contexte, checkpoint):
        fin = self.horloge() + self.delai
        try:
            with self._ouvrir('/api/show', {'model': self.modele}, fin, checkpoint) as r:
                meta = json.loads(b''.join(self._lire(r, fin, checkpoint)))
            if (not isinstance(meta, dict) or not isinstance(meta.get('details'), dict) or not meta['details'] or meta.get('error')
                    or meta.get('remote_host') or meta.get('remote_model')):
                raise ErreurMoteur('modele_local_non_verifie')
            paquet = {'instruction': contexte.instruction, 'contexte': contexte.resume, 'champs_manquants': contexte.champs_manquants,
                      'source_ids': contexte.source_ids}
            payload = {'model': self.modele, 'stream': True, 'keep_alive': '2m', 'format': SCHEMA,
                       'messages': [{'role': 'system', 'content': SYSTEME}, {'role': 'user', 'content': json.dumps(paquet, ensure_ascii=False)}],
                       'options': {'num_ctx': FENETRE, 'num_predict': SORTIE_MAX}}
            if not isinstance(meta.get('capabilities'), list) or 'thinking' in meta['capabilities']:
                payload['think'] = False
            if sum(len(m['content'].encode('utf-8')) for m in payload['messages']) + SORTIE_MAX + 256 > FENETRE:
                raise ErreurMoteur('budget_contexte_insuffisant')
            morceaux, tampon, termine = [], b'', False
            with self._ouvrir('/api/chat', payload, fin, checkpoint) as r:
                for chunk in self._lire(r, fin, checkpoint):
                    tampon += chunk
                    while b'\n' in tampon:
                        ligne, tampon = tampon.split(b'\n', 1)
                        if not ligne.strip():
                            continue
                        if termine:
                            raise ErreurMoteur('donnees_apres_fin')
                        rep = json.loads(ligne)
                        if not isinstance(rep, dict) or rep.get('error') or (rep.get('message') or {}).get('tool_calls'):
                            raise ErreurMoteur('reponse_ollama_invalide')
                        contenu = (rep.get('message') or {}).get('content', '')
                        if not isinstance(contenu, str):
                            raise ErreurMoteur('reponse_ollama_invalide')
                        morceaux.append(contenu)
                        if sum(len(x.encode('utf-8')) for x in morceaux) > 8192:
                            raise ErreurMoteur('sortie_trop_grande')
                        if rep.get('done') is True:
                            if rep.get('done_reason') == 'length':
                                raise ErreurMoteur('sortie_tronquee')
                            termine = True
                if tampon.strip():
                    raise ErreurMoteur('flux_sans_fin_de_ligne')
            if not termine:
                raise ErreurMoteur('flux_incomplet')
            checkpoint()
            return ''.join(morceaux)
        except (OSError, ValueError, TypeError, AttributeError) as ex:
            raise ErreurMoteur('transport_local_indisponible') from ex


CLES_ENTREE = frozenset(Contexte.__dataclass_fields__)


def lire(path):
    rows = [json.loads(x) for x in Path(path).read_text(encoding='utf-8').splitlines() if x.strip()]
    if len({r['id'] for r in rows}) != len(rows):
        raise ValueError('identifiant_duplique')
    return rows


def mesurer(cas, resultats):
    gold = {x['id']: x for x in cas}
    by_id = {x['id']: x for x in resultats}
    if set(by_id) != set(gold):
        raise ValueError('couverture_de_cas_differente')
    n = len(gold)
    if not n:
        raise ValueError('aucun_cas')
    corrects = proposes = erreurs = 0
    durees = []
    for ident, item in gold.items():
        r = by_id[ident]
        d = r['decision']
        attendu = item['attendu']
        bon = d['intention'] == attendu['intention'] and d['action'] in attendu['actions']
        if attendu['intention'] == 'indetermine':
            bon = d['statut'] == 'indetermine'
        corrects += int(bon)
        proposes += int(d['statut'] == 'propose')
        erreurs += int(d['statut'] == 'bloque' or bool(r.get('erreur')))
        ms = r['duree_ms']
        if type(ms) not in (int, float) or not math.isfinite(ms) or ms < 0:
            raise ValueError('duree_invalide')
        durees.append(ms)
    ordonnees = sorted(durees)
    return {'cas': n, 'corrects': corrects, 'taux_correct': corrects / n, 'propositions': proposes, 'couverture_decisions': proposes / n,
            'erreurs_bloquantes_ou_techniques': erreurs, 'mediane_ms': statistics.median(durees),
            'p95_ms': ordonnees[max(0, math.ceil(0.95 * n) - 1)],
            'limite': 'Corpus synthétique et durées client ; pas une qualification juridique ni une mesure de mémoire ou de VRAM.'}


def comparer(cas, reference, candidat):
    return {'reference': mesurer(cas, reference), 'candidat': mesurer(cas, candidat)}


def contexte_du_cas(case):
    entree = {k: v for k, v in case['entree'].items() if k in CLES_ENTREE}
    for k in ('champs_manquants', 'source_ids'):
        if k in entree:
            entree[k] = tuple(entree[k])
    return Contexte(**entree)


def executer(cas, modele, url, dest):
    # Banc isolé sur cas synthétiques : ces callbacks vides ne sont jamais ceux du produit (``decision56109.au_travail`` raccorde la pause,
    # les droits et la politique des données).
    couche = CoucheDecision(OllamaLocal(modele, url), checkpoint=lambda: None, autoriser=lambda c, a: None)
    with dest.open('x', encoding='utf-8', newline='\n') as f:
        for case in cas:
            debut = time.monotonic()
            out = couche.proposer(contexte_du_cas(case))
            f.write(json.dumps({'id': case['id'], 'decision': asdict(out), 'duree_ms': round((time.monotonic() - debut) * 1000, 3)}, ensure_ascii=False) + '\n')
            f.flush()


def main(argv=None):
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = p.add_subparsers(dest='commande', required=True)
    r = sub.add_parser('executer', help='Appelle volontairement le daemon Ollama du poste')
    r.add_argument('--cas', required=True)
    r.add_argument('--modele', required=True)
    r.add_argument('--url', default='http://127.0.0.1:11434')
    r.add_argument('--sortie', required=True)
    r.add_argument('--je-confirme-l-appel-ollama', dest='confirme', action='store_true')
    c = sub.add_parser('comparer', help='Compare deux campagnes ayant les mêmes cas')
    for flag in ('cas', 'reference', 'candidat', 'sortie'):
        c.add_argument('--' + flag, required=True)
    args = p.parse_args(argv)
    cas = lire(args.cas)
    dest = Path(args.sortie)
    if dest.exists():
        raise SystemExit('La sortie existe : choisir un nouveau nom pour conserver la campagne.')
    if args.commande == 'comparer':
        rapport = comparer(cas, lire(args.reference), lire(args.candidat))
        dest.write_text(json.dumps(rapport, ensure_ascii=False, indent=2) + '\n', encoding='utf-8', newline='\n')
        return 0
    if not args.confirme:
        raise SystemExit('« executer » génère réellement sur le daemon Ollama du poste : ajouter --je-confirme-l-appel-ollama.')
    executer(cas, args.modele, args.url, dest)
    return 0


if __name__ == '__main__':
    sys.exit(main())
