"""Mention d'auteur et licence (AxiorHub 5.6.0).

Conformément à l'article 7 b) de la licence AGPL-3.0, toute version modifiée ou redistribuée doit conserver la mention d'auteur ci-dessous
dans l'interface (page « À propos » et pied du menu) et dans le fichier NOTICE.
"""
from html import escape

PROJECT = 'AxiorHub Pilote'
AUTHOR = 'Timo RAINIO'
AUTHOR_TITLE = 'Avocat au Barreau de Lyon'
ATTRIBUTION = 'AxiorHub Pilote — créé par Timo RAINIO'
LICENSE = 'GNU Affero General Public License v3.0 ou ultérieure (AGPL-3.0-or-later)'
REPOSITORY = 'https://github.com/Arthur-Llevelys/AxiorHub-Pilote'   # adresse du dépôt public de référence, à renseigner à la publication ; AXIORHUB_SOURCE_URL la remplace par installation


def page(prefix, shell, csrf):
    from . import __version__
    import os
    source = os.environ.get('AXIORHUB_SOURCE_URL', '').strip() or REPOSITORY
    repo = ('<p>Code source : <a href="%s" rel="noopener noreferrer">%s</a></p>' % (escape(source, quote=True), escape(source))) if source.startswith('https://') else (
        '<p>Code source : fourni avec cette installation (licence AGPL : tout utilisateur du service peut en obtenir une copie).</p>')
    body = ('<div class="s550"><h1>À propos</h1><section class="ax-card"><h2>%s %s</h2><p><strong>%s</strong>, %s.</p>'
            '<p>Assistant d’exécution pour cabinets d’avocats : courriels, dossiers, documents, agenda et style du cabinet, avec une IA locale ou externe '
            'pseudonymisée.</p>%s</section>'
            '<section class="ax-card"><h2>Licence</h2><p>%s. Vous pouvez utiliser, étudier, modifier et redistribuer ce logiciel aux conditions de la licence. '
            'Si vous proposez une version modifiée à des utilisateurs à travers un réseau, vous devez leur donner accès à son code source.</p>'
            '<p><strong>Mention d’auteur obligatoire</strong> (article 7 b de l’AGPL) : toute version modifiée ou redistribuée conserve la mention '
            '« %s » dans cette page, dans le pied du menu et dans le fichier NOTICE.</p>'
            '<p>Le nom AxiorHub et son logo relèvent de règles propres (TRADEMARKS.md, LOGO-LICENSE.md) : une version modifiée doit porter son propre nom.</p></section>'
            '<section class="ax-card"><h2>Confidentialité</h2><p>Une installation = un cabinet. Les données restent sur votre serveur ; seule une IA externe, si vous '
            'l’activez, reçoit des textes pseudonymisés. Aucune donnée n’est transmise à l’auteur du logiciel.</p></section></div>') % (
        escape(PROJECT), escape(__version__), escape(AUTHOR), escape(AUTHOR_TITLE), repo, escape(LICENSE), escape(ATTRIBUTION))
    return shell('À propos', body, prefix, csrf, '/a-propos')
