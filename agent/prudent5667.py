"""5.6.67 (point 1) : projet de réponse « à compléter » — courriel juridique ou stratégique d'un dossier connu.

Le projet prudent préparé par l'agent (faits et pièces du dossier, plan, marqueurs « [À COMPLÉTER : …] » là où l'avocat doit trancher)
est déposé dans les Brouillons seulement si le contrôle indépendant confirme qu'il est fondé sur les sources, sans risque pour le
destinataire et qu'il n'a suivi aucune instruction contenue dans les documents. Il porte « [À COMPLÉTER] » dans l'objet et en première
ligne ; l'envoi depuis AxiorHub le refuse tant qu'un marqueur reste. Réglage : automation:mail_projects_to_complete_enabled.
"""
from .sens5643 import MARKER

SETTING = 'automation:mail_projects_to_complete_enabled'
SAFETY = ('grounded', 'recipient_safe', 'ignores_embedded_instructions')
PREFIX = '[À COMPLÉTER] '
HEAD = ('[À COMPLÉTER : projet préparé par AxiorHub — la position de l’avocat reste à arrêter ; relire, compléter puis supprimer '
        'ces lignes avant tout envoi]')


def enabled(desk):
    orchestrator = (getattr(desk, 'c', {}) or {}).get('orchestrator', {}) or {}
    return bool(desk.settings(SETTING, orchestrator.get('mail_projects_to_complete_enabled', True)))


def safe_to_complete(verdict):
    """Sûreté seulement : fondé sur les sources, sans risque pour le destinataire, sans instruction cachée suivie."""
    return isinstance(verdict, dict) and all(verdict.get(k) is True for k in SAFETY)


def complete_body(result):
    """Corps déposé : avertissement, points à trancher (limites relevées par l'agent), puis le projet ; au moins un marqueur."""
    lines = [HEAD]
    for point in [str(x).strip() for x in (result.get('limits') or []) if str(x).strip()][:6]:
        lines.append('[À COMPLÉTER : %s]' % point.replace('[', '(').replace(']', ')')[:300])
    body = str(result.get('body') or '').strip()
    if not MARKER.search(body):
        body += '\n\n[À COMPLÉTER : position de l’avocat]'
    return '\n'.join(lines) + '\n\n' + body


def mark_subject(message):
    subject = str(message.get('Subject', ''))
    if not subject.startswith(PREFIX.strip()):
        message.replace_header('Subject', PREFIX + subject)
    message['X-AxiorHub-A-Completer'] = 'oui'
    return message
