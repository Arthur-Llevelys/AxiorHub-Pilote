"""5.6.43 (cahier v9, § 21) : garde commune du sens des courriels et des destinataires, détection des marqueurs « À COMPLÉTER ».

- **Identités du cabinet** : ``own_addresses`` + adresse d'expédition + identifiant de connexion s'il est une adresse + alias déclarés
  (``mail.aliases``, ``mail.identities``). Normalisation par le parseur d'en-têtes (casse, espaces) ; aucun alias n'est fabriqué en retirant
  des points ou des suffixes.
- **Sens** d'un message : ``received`` (auteur extérieur), ``sent`` (auteur du cabinet, au moins un destinataire extérieur), ``internal``
  (auteur et destinataires du cabinet), ``unknown`` (auteur illisible). Seuls les en-têtes comptent : un client qui cite un ancien courriel du
  cabinet reste un message reçu.
- **Aucune réponse automatique à soi-même** : un message sortant ou interne ne déclenche aucun brouillon automatique ; un brouillon dont
  tous les destinataires principaux sont des adresses du cabinet (ou inexistants) est refusé au dépôt, quel que soit le circuit.
"""
import re
from email.utils import getaddresses

from .common import Stop

MARKER = re.compile(r'\[\s*(?:à|a)\s+compl[ée]ter\b[^\]]*\]', re.I)
SALUTATIONS = re.compile(r'^\s*(?:madame|monsieur|ma(?:î|i)tre|cher|ch(?:è|e)re|bonjour)[^\n]*$|'
                         r'je vous prie[^\n]*$|bien (?:à vous|cordialement)[^\n]*$|cordialement[^\n]*$', re.I | re.M)


def _norm(value):
    return str(value or '').strip().lower()


def identities(mail_cfg):
    """Adresses du cabinet et leur origine (affichées par le diagnostic)."""
    found = {}
    for value in mail_cfg.get('own_addresses', []) or []:
        if '@' in str(value):
            found.setdefault(_norm(value), 'own_addresses')
    for key in ('from_address', 'username'):
        value = _norm(mail_cfg.get(key))
        if '@' in value:
            found.setdefault(value, key)
    for key in ('aliases', 'identities'):
        for value in mail_cfg.get(key, []) or []:
            value = value.get('address') if isinstance(value, dict) else value
            if '@' in str(value or ''):
                found.setdefault(_norm(value), key)
    return found


def _addresses(header):
    return [_norm(a) for _, a in getaddresses([str(header or '')]) if '@' in str(a or '')]


def direction(mail, mail_cfg):
    own = identities(mail_cfg)
    author = _addresses(mail.msg.get('From', ''))
    author = author[0] if author else _norm(getattr(mail, 'sender', ''))
    if '@' not in author:
        return 'unknown'
    if author not in own:
        return 'received'
    recipients = _addresses(mail.msg.get('To', '')) + _addresses(mail.msg.get('Cc', ''))
    if recipients and all(r in own for r in recipients):
        return 'internal'
    return 'sent'


def check_recipients(to, cc, mail_cfg):
    """Destinataires principaux d'un brouillon : au moins une adresse extérieure, sinon refus explicite."""
    own = identities(mail_cfg)
    primary = _addresses(to)
    if not primary:
        raise Stop('destinataire_absent')
    if all(a in own for a in primary):
        raise Stop('reponse_a_soi_meme_refusee')
    return primary


def redaction_status(body):
    """'complete', 'partial' (marqueurs ponctuels) ou 'needs_validation' (le fond lui-même est un marqueur)."""
    text = str(body or '')
    markers = MARKER.findall(text)
    if not markers:
        return 'complete'
    rest = MARKER.sub('', text)
    rest = SALUTATIONS.sub('', rest)
    substantive = re.sub(r'\s+', ' ', rest).strip()
    if len(substantive) < 160 or any(re.search(r'fond|réponse|reponse', m, re.I) for m in markers):
        return 'needs_validation'
    return 'partial'


CABINET_NOTE = 'Message envoyé par le cabinet — pas de réponse automatique.'
