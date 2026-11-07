"""5.6.67 (point 1) : projet de réponse « à compléter » — courriel juridique ou stratégique d'un dossier connu.

Le projet prudent préparé par l'agent (faits et pièces du dossier, réponse rédigée) est déposé dans les Brouillons seulement si le
contrôle indépendant confirme qu'il est fondé sur les sources, sans risque pour le destinataire et qu'il n'a suivi aucune instruction
contenue dans les documents. Réglage : automation:mail_projects_to_complete_enabled.

5.6.95 (D04) : le corps déposé n'est plus une suite de lignes « [À COMPLÉTER : …] » (une par limite relevée, plus un marqueur forcé).
Une seule ligne d'en-tête courte « [À RELIRE : …] » ; les limites vont dans la fiche de contrôle (page du courriel) ; les positions de
l'avocat sont des variantes rédigées (``redaction5695``) ; les marqueurs ne désignent plus que des faits absents (au plus 2). L'objet dit
ce qui reste à faire (« [À RELIRE : 1 choix] », « [À COMPLÉTER : 1 fait] ») ; l'envoi depuis AxiorHub reste refusé tant qu'un bloc de
variantes, un marqueur ou la mention « À RELIRE » subsiste dans l'objet ou le corps (``sens5643.blocage_envoi``).
"""
from .common import Stop
from .redaction5695 import HEADER_FAITS, HEADER_VARIANTES, MENTION, compter, mention_objet

SETTING = 'automation:mail_projects_to_complete_enabled'
SAFETY = ('grounded', 'recipient_safe', 'ignores_embedded_instructions')
PREFIX = '[À COMPLÉTER] '     # ancienne mention d'objet (avant 5.6.95) : toujours reconnue par MENTION et par le blocage d'envoi
HEAD = '[À RELIRE : projet préparé par AxiorHub — gardez une variante par choix, complétez les faits signalés, puis supprimez cette ligne]'


def enabled(desk):
    orchestrator = (getattr(desk, 'c', {}) or {}).get('orchestrator', {}) or {}
    return bool(desk.settings(SETTING, orchestrator.get('mail_projects_to_complete_enabled', True)))


def safe_to_complete(verdict):
    """Sûreté seulement : fondé sur les sources, sans risque pour le destinataire, sans instruction cachée suivie."""
    return isinstance(verdict, dict) and all(verdict.get(k) is True for k in SAFETY)


def complete_body(result):
    """Corps déposé : une ligne d'en-tête, puis le courriel entier (variantes en blocs lisibles). Jamais de corps vide."""
    body = str(result.get('body') or '').strip()
    if not body:
        raise Stop('depot_bloque_decision_requise')
    return HEAD + '\n\n' + body


def _body(message):
    try:
        part = message.get_body(preferencelist=('plain',))
        return part.get_content() if part is not None else ''
    except Exception:   # noqa: BLE001 — message sans corps lisible (en cours de construction)
        return ''


def mark_subject(message, body=None):
    """Objet : mention de ce qui reste à faire ; en-têtes techniques (faits, variantes). Appel répété : aucun doublon."""
    left = compter(_body(message) if body is None else body)
    subject = str(message.get('Subject', ''))
    if not MENTION.match(subject):
        message.replace_header('Subject', mention_objet(left['choix'], left['marqueurs']) + subject)
    if message.get(HEADER_FAITS) is None:   # 5.6.70 : appel répété sans en-tête en double
        message[HEADER_FAITS] = 'oui'
    if left['choix'] and message.get(HEADER_VARIANTES) is None:
        message[HEADER_VARIANTES] = str(left['choix'])
    return message
