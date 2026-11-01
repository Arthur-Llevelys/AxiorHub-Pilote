"""5.6.88 : ce que l'IA peut voir, catégorie par catégorie (Paramètres › Modèles d'IA › Ce que l'IA peut voir).

Réglage du cabinet, tout autorisé par défaut (comportement inchangé). Il est appliqué au serveur, là où les données sont lues pour le
modèle : un modèle ne peut donc pas le contourner, quoi que dise une consigne ou une pièce. Les droits de la personne restent une limite
supérieure : l'assistant n'ouvre jamais ce qu'elle ne peut pas ouvrir (``membres5650.ai_limits``).

Points de lecture couverts :

- discussion et voix : ``acces5683`` (courriels, agenda), ``workspace.chat`` (courriel de la conversation, courriels indexés, documents
  sélectionnés, recherche dans les pièces ; mémoire, analyses et données structurées du dossier, tirées des pièces, suivent « Documents ») ;
- outils de connexions choisis par l'agent : ``connexions5652.agent_research`` ;
- missions : ``sources5614.snapshot`` (fichiers, courriels, agenda inventoriés pour le modèle) ;
- connecteur Claude : ``claude5675`` (table ``TOOL_CATEGORIES``, y compris les rubriques de la fiche du dossier).

Hors de ce réglage, et dit sur la page : les travaux automatiques sur le courriel qui vient d'arriver (tri, relevé des échéances) et sur les
pièces (indexation, analyse) s'arrêtent par l'autonomie de l'agent, pas ici ; une pièce que la personne joint elle-même à sa question est
lue ; la messagerie interne n'est lue par aucun modèle, le réglage en reste la garantie écrite.
"""
from html import escape as e

from .common import Stop

CATEGORIES = (('courriels', 'Courriels', 'Courriels de la boîte du cabinet, et ceux rattachés à un dossier.'),
              ('agenda', 'Agenda et tâches', 'Rendez-vous, audiences, tâches et échéances inscrites.'),
              ('documents', 'Documents', 'Pièces des dossiers et extraits indexés. Une pièce que vous joignez vous-même à une question reste lue.'),
              ('factures', 'Factures et honoraires', 'Factures, règlements, opportunités chiffrées, connexions de facturation.'),
              ('contacts', 'Contacts', 'Fiches de contacts et de prospects, connexions de contacts.'),
              ('temps', 'Temps passés', 'Temps enregistrés et diligences facturables.'),
              ('messagerie', 'Messagerie interne', 'Aucun modèle ne lit aujourd’hui les conversations entre membres : ce réglage en reste la garantie.'))
IDS = tuple(c[0] for c in CATEGORIES)
LABELS = {c[0]: c[1] for c in CATEGORIES}
SETTING = 'ia_vue5688'
REFUSED = 'non autorisé par le réglage « Ce que l’IA peut voir » ou les droits de ce compte'


def cabinet(desk):
    """{catégorie: bool} ; toute catégorie absente, ou valeur illisible, vaut « autorisé » comme avant la mise à jour."""
    raw = desk.settings(SETTING, {}) or {}
    return {c: raw.get(c) is not False for c in IDS} if isinstance(raw, dict) else {c: True for c in IDS}


def save(desk, values):
    """N'enregistre que des booléens vrais : une valeur douteuse n'ouvre ni ne ferme rien en silence."""
    if any(not isinstance(values.get(c), bool) for c in IDS):
        raise Stop('vue_ia_invalide')
    out = {c: values[c] for c in IDS}
    desk.setting(SETTING, out)
    return out


def blocked(desk, category):
    """Vrai si cette catégorie est fermée au modèle pour la requête en cours (réglage du cabinet, ou droits de la personne)."""
    from .membres5650 import ai_denied
    return category in ai_denied() or not cabinet(desk).get(category, True)


def effective(desk):
    return {c: not blocked(desk, c) for c in IDS}


def settings_html(desk, prefix):
    values = cabinet(desk)
    checks = ''.join('<label class="m88-vue"><input type="checkbox" name="%s"%s> <span><strong>%s</strong><small>%s</small></span></label>' % (
        cid, ' checked' if values[cid] else '', e(label), e(detail)) for cid, label, detail in CATEGORIES)
    closed = [LABELS[c] for c in IDS if not values[c]]
    state = ('Fermé au modèle pour tout le cabinet : %s.' % ', '.join(closed)) if closed else 'Tout est ouvert : comportement par défaut.'
    return ('<h1>Ce que l’IA peut voir</h1>'
            '<p class="ax-muted">Cochez ce que l’assistant, les missions et le connecteur Claude peuvent lire. Le contrôle se fait sur le serveur, au '
            'moment de la lecture. Les droits de chaque personne restent une limite : l’assistant n’ouvre jamais ce qu’elle ne peut pas ouvrir.</p>'
            '<section class="ax-card"><h2>Catégories</h2><p role="status">%s</p>'
            '<form class="m5-form" data-api="m5688s/admin/vue-ia" data-reload>%s'
            '<button class="ax-btn" type="submit">Enregistrer</button><p role="status" class="m5-status"></p></form></section>'
            '<section class="ax-card"><h2>Ce que ce réglage ne couvre pas</h2><ul>'
            '<li>Le tri automatique des nouveaux courriels et l’analyse des pièces en arrière-plan : ils se règlent dans '
            '<a href="%s/autonomie">Autonomie de l’agent</a>.</li>'
            '<li>Une pièce que vous joignez vous-même à une question.</li>'
            '<li>Le contrôle des sociétés citées dans une question : il lit des registres publics. Il ne se sert d’une fiche de contact que si « Contacts » est ouvert.</li>'
            '<li>Ce qui a déjà été transmis à un modèle avant ce réglage.</li></ul></section>') % (e(state), checks, e(prefix, quote=True))
