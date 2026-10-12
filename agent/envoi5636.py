"""5.6.36 : Paramètres › Messagerie › « Envoi depuis AxiorHub » — un réglage simple, au lieu de la page Confort introuvable.

- « Autoriser l'envoi quand je clique sur Envoyer » (case à cocher, confirmation demandée une fois) ;
- « Tous les dossiers » ou « Seulement les dossiers choisis » (liste à cocher) ;
- « Demander une confirmation avant chaque envoi » (recommandé ; décochée, le message part après le contrôle du serveur).
Les garde-fous du serveur restent (brouillon relu, empreinte, « À VÉRIFIER », plafond horaire, double envoi impossible) ;
l'agent n'envoie jamais rien de lui-même.
"""
from html import escape as e

from .common import Stop


def settings_html(desk, prefix):
    from .web490 import send_settings_status   # le module d'envoi n'est atteint que par la couche web (test_v490)
    try:
        s = send_settings_status(desk)
    except Stop:
        return ''
    smtp = s['smtp_configured']
    chosen = ''.join('<label class="e5636-matter"><input type="checkbox" data-envoi5636-matter="%s"%s> %s</label>' % (
        e(m['id'], quote=True), ' checked' if m['enabled'] else '', e(m['label'])) for m in s['matters'][:400])
    return ('<section class="ax-card e5636" id="envoi5636" aria-labelledby="e5636-t"><h2 id="e5636-t">Envoi depuis AxiorHub</h2>'
            '<p class="ax-muted">Sans ce réglage, AxiorHub dépose vos messages dans Brouillons ; vous les envoyez depuis votre messagerie. '
            'L’agent n’envoie jamais rien de lui-même : seul votre clic sur « Envoyer » fait partir un message.</p>%s'
            '<label class="e5636-switch"><input type="checkbox" data-envoi5636="enabled"%s%s> <strong>Autoriser l’envoi quand je clique sur « Envoyer »</strong></label>'
            '<fieldset class="e5636-scope"%s><legend>Pour quels dossiers ?</legend>'
            '<label><input type="radio" name="e5636-scope" value="all" data-envoi5636="scope"%s> Tous les dossiers (et les messages rédigés dans Courriels)</label>'
            '<label><input type="radio" name="e5636-scope" value="some" data-envoi5636="scope"%s> Seulement les dossiers que je coche</label>'
            '<details class="e5636-list"%s><summary>Dossiers autorisés (%d)</summary><div>%s</div></details></fieldset>'
            '<label class="e5636-switch"><input type="checkbox" data-envoi5636="confirm_each"%s> Me demander une confirmation avant chaque envoi '
            '(destinataires et objet affichés) — recommandé</label>'
            '<p role="status" class="e5636-status" data-envoi5636-status></p></section>') % (
        '' if smtp else '<p class="ax-alert">Le serveur d’envoi (SMTP) n’est pas encore réglé : complétez Paramètres › Connexions › Messagerie, '
                        'puis revenez ici.</p>',
        ' checked' if s['requested'] else '', '' if smtp else ' disabled',
        '' if s['requested'] else ' disabled', ' checked' if s['all_matters'] else '', '' if s['all_matters'] else ' checked',
        '' if s['all_matters'] else ' open', sum(1 for m in s['matters'] if m['enabled']), chosen or '<p class="ax-muted">Aucun dossier.</p>',
        ' checked' if s['confirm_each'] else '')
