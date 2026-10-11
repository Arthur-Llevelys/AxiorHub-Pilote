"""5.6.32 (R08) : aide aux balises des modèles Word du cabinet et modèle d'exemple fictif à télécharger.

- Aide : chaque balise reconnue (« {{destinataire}} »…), son rôle, si elle est obligatoire, un exemple fictif, et les règles du
  contrôle d'import (balise {{corps}} seule dans son paragraphe, aucune date Word automatique, aucun lien externe).
- Modèle d'exemple : un DOCX minimal construit ici (aucune donnée réelle) contenant toutes les balises, qui passe le même contrôle
  que vos modèles (cabinet_docs33.inspect_template). Il sert de point de départ : ouvrez-le dans Word, ajoutez votre en-tête, votre
  logo et votre pied de page, puis importez-le.
"""
import io
import zipfile
from html import escape

from .cabinet_docs33 import FIELDS, REQUIRED, SAMPLE, inspect_template

ROLES = {'destinataire': 'Nom du destinataire', 'qualite': 'Qualité du destinataire (facultatif)', 'adresse': 'Adresse du destinataire (facultatif)',
         'reference': 'Référence du dossier (facultatif)', 'objet': 'Objet du courrier', 'date': 'Date du courrier (figée à la préparation)',
         'envoi': 'Mode d’envoi (courriel, courrier simple, recommandé)', 'appel': 'Formule d’appel', 'corps': 'Corps du courrier (seul dans son paragraphe)',
         'fin': 'Formule de fin', 'signature': 'Signature texte (facultatif)'}
ORDER = ('destinataire', 'qualite', 'adresse', 'reference', 'date', 'envoi', 'objet', 'appel', 'corps', 'fin', 'signature')
RULES = ('Écrivez chaque balise telle quelle, accolades comprises, sans espace : {{objet}}.',
         'La balise {{corps}} est seule dans son paragraphe ; le texte du courrier y est inséré paragraphe par paragraphe.',
         'Aucun champ de date automatique de Word (DATE, TIME…) : la date du projet est figée à la préparation.',
         'Les liens externes et modèles attachés sont neutralisés à l’import ; l’en-tête, le pied de page, le logo et les tableaux sont conservés.',
         'Le modèle reste « à examiner » tant que vous n’avez pas contrôlé toutes les pages de l’original et de l’exemple rempli.')
W = 'http://schemas.openxmlformats.org/wordprocessingml/2006/main'


def help_html(download_url):
    rows = ''.join('<tr><td><code>{{%s}}</code></td><td>%s</td><td>%s</td><td>%s</td></tr>' % (
        k, escape(ROLES[k]), 'oui' if k in REQUIRED else 'non', escape(SAMPLE.get(k, '')).replace('\n', '<br>')) for k in ORDER if k in FIELDS)
    rules = ''.join('<li>%s</li>' % escape(r) for r in RULES)
    return ('<section class="m5632-tags" id="balises"><h3>Balises reconnues</h3><p>Votre modèle Word contient ces balises ; AxiorHub les remplace '
            'lors de la préparation d’un courrier. Les exemples sont fictifs.</p><div class="m5632-table"><table><thead><tr><th>Balise</th><th>Rôle</th>'
            '<th>Obligatoire</th><th>Exemple fictif</th></tr></thead><tbody>%s</tbody></table></div><ul>%s</ul>'
            '<p><a class="ax-btn ghost" href="%s" download="modele-courrier-exemple.docx">Télécharger un modèle d’exemple (fictif)</a></p></section>') % (
        rows, rules, escape(download_url, quote=True))


def _p(text, bold=False):
    rpr = '<w:rPr><w:b/></w:rPr>' if bold else ''
    return '<w:p><w:r>%s<w:t xml:space="preserve">%s</w:t></w:r></w:p>' % (rpr, escape(text, quote=False))


def example_docx():
    """DOCX minimal (texte seul) contenant toutes les balises ; validé par le contrôle d'import avant d'être servi."""
    body = ''.join([
        _p('CABINET EXEMPLE — Avocat (exemple fictif)', True), _p('10 rue du Test, 69000 Lyon'), _p(''),
        _p('{{destinataire}}', True), _p('{{qualite}}'), _p('{{adresse}}'), _p(''),
        _p('Lyon, le {{date}}'), _p('{{envoi}}'), _p('Réf. : {{reference}}'), _p('Objet : {{objet}}', True), _p(''),
        _p('{{appel}}'), _p('{{corps}}'), _p('{{fin}}'), _p(''), _p('{{signature}}')])
    document = ('<?xml version="1.0" encoding="UTF-8" standalone="yes"?><w:document xmlns:w="%s"><w:body>%s'
                '<w:sectPr><w:pgSz w:w="11906" w:h="16838"/><w:pgMar w:top="1417" w:right="1417" w:bottom="1417" w:left="1417" w:header="708" '
                'w:footer="708" w:gutter="0"/></w:sectPr></w:body></w:document>') % (W, body)
    files = {
        '[Content_Types].xml': ('<?xml version="1.0" encoding="UTF-8" standalone="yes"?><Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types">'
                                '<Default Extension="rels" ContentType="application/vnd.openxmlformats-package.relationships+xml"/>'
                                '<Default Extension="xml" ContentType="application/xml"/>'
                                '<Override PartName="/word/document.xml" ContentType="application/vnd.openxmlformats-officedocument.wordprocessingml.document.main+xml"/>'
                                '</Types>'),
        '_rels/.rels': ('<?xml version="1.0" encoding="UTF-8" standalone="yes"?><Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">'
                        '<Relationship Id="rId1" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/officeDocument" Target="word/document.xml"/>'
                        '</Relationships>'),
        'word/document.xml': document,
    }
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, 'w', zipfile.ZIP_DEFLATED) as z:
        for name, text in files.items():
            info = zipfile.ZipInfo(name, date_time=(2026, 1, 1, 0, 0, 0))   # contenu stable (même empreinte à chaque téléchargement)
            info.compress_type = zipfile.ZIP_DEFLATED
            z.writestr(info, text.encode('utf-8'))
    raw = buf.getvalue()
    inspect_template(raw)   # le même contrôle que l'import : une régression ici se verrait avant tout téléchargement
    return raw
