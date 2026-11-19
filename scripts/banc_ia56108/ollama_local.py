"""5.6.108 (lot E) : client Ollama du banc d'essai, local ou sur le réseau privé du cabinet (option explicite).

- Hôte par défaut : seulement ``http://127.0.0.1``, ``http://localhost`` ou ``http://[::1]`` (port quelconque).
- Hôte du réseau privé (décision du cabinet du 18/11/2026) : seulement par l'option ``--hote-reseau-prive`` de la ligne de commande, jamais
  lu dans ``OLLAMA_HOST`` ni dans une autre variable ; seulement une IP littérale de 10.0.0.0/8, 172.16.0.0/12, 192.168.0.0/16 ou fc00::/7
  (jamais un nom, jamais une IP publique ni de documentation). L'adresse n'est jamais recopiée dans un message ni dans le rapport.
- Aucun mandataire (proxy) : les variables de proxy du système sont ignorées.
- Modèle : un nom qui finit par ``:cloud`` ou ``-cloud`` (modèles exécutés hors de l'hôte par Ollama) est refusé ; après ``/api/show``, un
  modèle qui déclare un hôte ou un modèle distant est refusé aussi.
- Génération reproductible : ``temperature`` 0, graine fixe, ``num_ctx`` égal à la place simulée, aucun flux ; pause facultative entre
  deux appels (``Cadence``).
"""
import ipaddress
import json
import re
import time
import urllib.error
import urllib.request
from urllib.parse import urlsplit

HOTES_LOCAUX = ('127.0.0.1', 'localhost', '::1')
HOTE_DEFAUT = 'http://127.0.0.1:11434'
RESEAUX_PRIVES = tuple(ipaddress.ip_network(n) for n in ('10.0.0.0/8', '172.16.0.0/12', '192.168.0.0/16', 'fc00::/7'))


class Refus(Exception):
    """Configuration refusée par le banc (hôte non admis, modèle distant…)."""


def _forme(parts):
    if parts.scheme != 'http' or parts.username or parts.password:
        return False
    if parts.path not in ('', '/') or parts.query or parts.fragment:
        return False
    try:
        parts.port   # port invalide : ValueError
    except ValueError:
        return False
    return True


def verifier_hote(url):
    parts = urlsplit(str(url or ''))
    if not _forme(parts) or (parts.hostname or '') not in HOTES_LOCAUX:
        raise Refus('hôte refusé : sans option, le banc n’appelle qu’un Ollama local (http://127.0.0.1:11434) : %s' % url)
    return '%s://%s' % (parts.scheme, parts.netloc)


def verifier_hote_prive(url):
    """Hôte du réseau privé : IP littérale d'une plage privée seulement. Le message de refus ne recopie pas l'adresse."""
    parts = urlsplit(str(url or ''))
    if not _forme(parts):
        raise Refus('hôte du réseau privé refusé : forme attendue http://<adresse IP privée>:<port>')
    try:
        ip = ipaddress.ip_address(parts.hostname or '')
    except ValueError:
        raise Refus('hôte du réseau privé refusé : un nom n’est pas admis, seulement une adresse IP littérale') from None
    if not any(ip in reseau for reseau in RESEAUX_PRIVES if reseau.version == ip.version):
        raise Refus('hôte du réseau privé refusé : l’adresse n’appartient pas à une plage privée (10/8, 172.16/12, 192.168/16, fc00::/7)')
    return '%s://%s' % (parts.scheme, parts.netloc)


def verifier_modele(nom):
    nom = str(nom or '').strip()
    if not nom or not re.fullmatch(r'[A-Za-z0-9._/-]+(?::[A-Za-z0-9._-]+)?', nom):
        raise Refus('nom de modèle invalide : %r' % nom)
    if re.search(r'[:-]cloud$', nom, re.I):
        raise Refus('modèle refusé : %s s’exécute hors de l’hôte (modèle « cloud »)' % nom)
    return nom


def analyser_cible(valeur):
    """``ollama:<nom>`` → nom du modèle vérifié."""
    texte = str(valeur or '')
    if not texte.startswith('ollama:'):
        raise Refus('fournisseur refusé : seul « ollama:<nom> » est accepté : %s' % texte)
    return verifier_modele(texte[len('ollama:'):])


class Cadence:
    """Pause minimale entre deux appels au modèle, partagée par tous les clients d'une passe (défaut : aucune)."""

    def __init__(self, pause_ms=0, dormir=None, horloge=None):
        self.pause = max(0, int(pause_ms or 0)) / 1000.0
        self._dormir, self._horloge, self._dernier = dormir, horloge, None

    def _maintenant(self):
        return (self._horloge or time.monotonic)()

    def attendre(self):
        if self.pause and self._dernier is not None:
            reste = self.pause - (self._maintenant() - self._dernier)
            if reste > 0:
                (self._dormir or time.sleep)(reste)

    def noter(self):
        self._dernier = self._maintenant()


class Ollama:
    def __init__(self, modele, hote=HOTE_DEFAUT, delai=600, ouvrir=None, reseau_prive=False, cadence=None):
        self.base = verifier_hote_prive(hote) if reseau_prive else verifier_hote(hote)
        self.portee = 'réseau privé' if reseau_prive else 'local'
        self.modele = verifier_modele(modele)
        self.delai = delai
        self.cadence = cadence or Cadence()
        # Aucun mandataire : ProxyHandler({}) ignore HTTP_PROXY, HTTPS_PROXY et la configuration du système.
        self._ouvrir = ouvrir or urllib.request.build_opener(urllib.request.ProxyHandler({})).open
        self._verifie = False

    def masquer(self, texte):
        """Message d'erreur sans l'adresse de l'hôte (rapport, journal)."""
        return str(texte).replace(self.base, '<hôte>').replace(urlsplit(self.base).hostname or '\0', '<hôte>')

    def _post(self, chemin, corps):
        req = urllib.request.Request(self.base + chemin, data=json.dumps(corps).encode('utf-8'), headers={'Content-Type': 'application/json'}, method='POST')
        with self._ouvrir(req, timeout=self.delai) as rep:
            return json.loads(rep.read().decode('utf-8'))

    def verifier(self):
        if self._verifie:
            return
        info = self._post('/api/show', {'model': self.modele})
        if not isinstance(info, dict) or info.get('remote_host') or info.get('remote_model'):
            raise Refus('modèle refusé : %s est servi par un hôte distant' % self.modele)
        caps = info.get('capabilities')
        self.capacites = [str(c) for c in caps] if isinstance(caps, list) else None
        details = info.get('details') if isinstance(info.get('details'), dict) else {}
        self.taille_parametres = str(details.get('parameter_size') or '')   # 5.6.108 (lot R) : « petit modèle » (14 B ou moins)
        self._verifie = True

    def chat(self, messages, place, max_tokens, graine=42, format_json=False, requete=None):
        self.verifier()
        corps = {'model': self.modele, 'messages': messages, 'stream': False, 'keep_alive': '5m',
                 'options': {'temperature': 0, 'seed': int(graine), 'num_ctx': int(place), 'num_predict': int(max_tokens)}}
        for cle in ('think', 'format'):   # réglages de la requête réelle du produit, rejoués tels quels
            if isinstance(requete, dict) and cle in requete:
                corps[cle] = requete[cle]
        # Comme le produit (model.py, réglage « disable_thinking » vrai par défaut) : un modèle qui déclare la réflexion reçoit « think: false » ;
        # le faux transport de la capture ne connaît pas les capacités du vrai modèle, la règle est donc appliquée ici, sur celles du serveur.
        capacites = getattr(self, 'capacites', None)
        if 'think' not in corps and capacites is not None and 'thinking' in capacites:
            corps['think'] = False
        # « think » n'est jamais envoyé à un modèle qui ne déclare pas la réflexion.
        if 'think' in corps and capacites is not None and 'thinking' not in capacites:
            del corps['think']
        if format_json:
            corps['format'] = 'json'
        self.cadence.attendre()
        debut = time.monotonic()
        try:
            rep = self._post('/api/chat', corps)
        finally:
            self.cadence.noter()
        duree = time.monotonic() - debut
        return {'texte': str(((rep or {}).get('message') or {}).get('content') or ''), 'duree_s': round(duree, 2),
                'jetons_entree': int(rep.get('prompt_eval_count') or 0), 'jetons_sortie': int(rep.get('eval_count') or 0),
                'fin': str(rep.get('done_reason') or '')}
