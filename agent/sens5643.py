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
    """Adresse normalisée ; une valeur « Nom <adresse> » est analysée (5.6.56), jamais réécrite (pas d'alias inventé)."""
    raw = str(value or '').strip()
    if '<' in raw:
        found = [a for _, a in getaddresses([raw]) if '@' in str(a or '')]
        raw = found[0] if found else raw
    return raw.strip().lower()


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
    author = _addresses(mail.msg.get('From', ''))
    author = author[0] if author else _norm(getattr(mail, 'sender', ''))
    return classify(author, mail.msg.get('To', ''), mail.msg.get('Cc', ''), mail_cfg)


def classify(author, to, cc, mail_cfg):
    """Sens d'un message d'après ses SEULS en-têtes (jamais d'après une citation dans le corps)."""
    own = identities(mail_cfg)
    author = _norm(author)
    if '@' not in author:
        return 'unknown'
    if author not in own:
        return 'received'
    recipients = _addresses(to) + _addresses(cc)
    if recipients and all(r in own for r in recipients):
        return 'internal'
    return 'sent'


ORIGINS = {'own_addresses': 'Autres adresses du cabinet (réglage)', 'from_address': 'Adresse d’expédition', 'username': 'Identifiant de connexion',
           'aliases': 'Alias', 'identities': 'Identité déclarée'}
DIRECTIONS = {'received': 'reçu (un projet de réponse peut être préparé)', 'sent': 'envoyé par le cabinet (aucune réponse automatique)',
              'internal': 'interne ou copie personnelle (aucune réponse)', 'unknown': 'expéditeur illisible'}


def identities_html(mail_cfg, args=None):
    """5.6.56 (audit 5.6.55, § 3.1) : « Vos adresses reconnues par l'agent », leur provenance, et un test de classification."""
    from html import escape as e
    rows = ''.join('<li><strong>%s</strong> — %s</li>' % (e(a), e(ORIGINS.get(o, o))) for a, o in sorted(identities(mail_cfg).items()))
    args = args or {}
    test = ''
    if args.get('test_de'):
        verdict = classify(str(args.get('test_de'))[:200], str(args.get('test_a') or '')[:400], '', mail_cfg)
        test = '<p role="status">Message de %s à %s : <strong>%s</strong>.</p>' % (e(str(args['test_de'])[:200]), e(str(args.get('test_a') or '—')[:400]),
                                                                              e(DIRECTIONS.get(verdict, verdict)))
    return ('<section class="ax-card" id="identites5656"><h2>Vos adresses reconnues par l’agent</h2><p class="ax-muted">Un message écrit depuis l’une de '
            'ces adresses est un envoi du cabinet : l’agent n’y répond jamais automatiquement et ne la propose jamais comme correspondant d’un dossier. '
            'Un confrère du même domaine n’est pas assimilé au cabinet. Les adresses se règlent dans Connexions › Courriels.</p><ul>%s</ul>'
            '<form method="get" class="crm-search"><input type="hidden" name="rubrique" value="messagerie"><label>Expéditeur<input name="test_de" '
            'type="email" maxlength="200" required></label><label>Destinataire<input name="test_a" type="email" maxlength="200"></label>'
            '<button class="ax-btn ghost" type="submit">Tester la classification</button></form>%s</section>') % (
        rows or '<li class="ax-muted">Aucune adresse du cabinet configurée.</li>', test)


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
