"""5.6.108 (lot W) : fichiers .docx FICTIFS pour la garde, le contrôle et la mesure de fidélité de l'éditeur intégré.

``docx_fictif(*options)`` construit un document Word minimal et valide (données fictives : DURAND, MARTIN, DOS-001, RG 24/01234, Valbrune)
avec, au choix : ``suivi`` (insertion et suppression suivies), ``commentaires``, ``champs`` (PAGE dans le pied, DATE simple, renvoi REF),
``numerotation`` (liste numérotée), ``notes`` (note de bas de page), ``sections`` (deux sections), ``entetes`` (en-tête et pied de page),
``tableau``, ``controle`` (contrôle de contenu), ``signet``, ``lien``, ``mise_en_forme`` (gras, italique, souligné), ``saut``,
``image`` (petite image incorporée), ``zone_texte``, ``objet`` (fragment importé), ``macros`` (partie vbaProject), ``protection``.
"""
import base64
import io
import zipfile

W = 'http://schemas.openxmlformats.org/wordprocessingml/2006/main'
R = 'http://schemas.openxmlformats.org/officeDocument/2006/relationships'
NS = ('xmlns:w="%s" xmlns:r="%s" xmlns:wp="http://schemas.openxmlformats.org/drawingml/2006/wordprocessingDrawing" '
      'xmlns:a="http://schemas.openxmlformats.org/drawingml/2006/main" xmlns:pic="http://schemas.openxmlformats.org/drawingml/2006/picture" '
      'xmlns:wps="http://schemas.microsoft.com/office/word/2010/wordprocessingShape" '
      'xmlns:mc="http://schemas.openxmlformats.org/markup-compatibility/2006" xmlns:v="urn:schemas-microsoft-com:vml"') % (W, R)
PNG = base64.b64decode('iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVR42mP8z8BQDwAEhQGAhKmMIQAAAABJRU5ErkJggg==')
TOUTES = ('suivi', 'commentaires', 'champs', 'numerotation', 'notes', 'sections', 'entetes', 'tableau', 'controle', 'signet', 'lien',
          'mise_en_forme', 'saut', 'image', 'zone_texte', 'objet', 'macros', 'protection')


def _p(texte, ppr='', runs=None):
    contenu = runs if runs is not None else '<w:r><w:t xml:space="preserve">%s</w:t></w:r>' % texte
    return '<w:p>%s%s</w:p>' % (('<w:pPr>%s</w:pPr>' % ppr) if ppr else '', contenu)


def docx_fictif(*options, titre='Conclusions pour M. DURAND'):
    o = set(options)
    corps = [_p(titre, '<w:pStyle w:val="Titre1"/><w:jc w:val="center"/>'),
             _p('Dossier DOS-001, RG 24/01234, tribunal judiciaire de Valbrune.'),
             _p('Pour : M. DURAND, demandeur. Contre : Mme MARTIN, défenderesse.')]
    rels = ['<Relationship Id="rIdStyles" Type="%s/styles" Target="styles.xml"/>' % R]
    types = ['<Override PartName="/word/document.xml" ContentType="application/vnd.openxmlformats-officedocument.wordprocessingml.document.main+xml"/>',
             '<Override PartName="/word/styles.xml" ContentType="application/vnd.openxmlformats-officedocument.wordprocessingml.styles+xml"/>']
    parties = {}
    sect_refs = ''
    if 'mise_en_forme' in o:
        corps.append(_p('', runs='<w:r><w:rPr><w:b/></w:rPr><w:t xml:space="preserve">Sur la recevabilité. </w:t></w:r>'
                            '<w:r><w:rPr><w:i/></w:rPr><w:t xml:space="preserve">In limine litis, </w:t></w:r>'
                            '<w:r><w:rPr><w:u w:val="single"/></w:rPr><w:t>la demande est recevable.</w:t></w:r>'))
    if 'suivi' in o:
        corps.append(_p('', runs='<w:r><w:t xml:space="preserve">Le contrat a été signé le </w:t></w:r>'
                            '<w:del w:id="1" w:author="MARTIN" w:date="2026-11-02T10:00:00Z"><w:r><w:delText>2 mars</w:delText></w:r></w:del>'
                            '<w:ins w:id="2" w:author="MARTIN" w:date="2026-11-02T10:00:00Z"><w:r><w:t>3 mars</w:t></w:r></w:ins>'
                            '<w:r><w:t xml:space="preserve"> à Valbrune.</w:t></w:r>'))
    if 'commentaires' in o:
        corps.append(_p('', runs='<w:commentRangeStart w:id="0"/><w:r><w:t>Le préjudice est établi par les pièces.</w:t></w:r>'
                            '<w:commentRangeEnd w:id="0"/><w:r><w:commentReference w:id="0"/></w:r>'))
        parties['word/comments.xml'] = ('<?xml version="1.0" encoding="UTF-8" standalone="yes"?><w:comments %s><w:comment w:id="0" w:author="BERNARD" '
                                        'w:date="2026-11-02T10:00:00Z" w:initials="B"><w:p><w:r><w:t>Ajouter la pièce n° 4.</w:t></w:r></w:p>'
                                        '</w:comment></w:comments>') % NS
        rels.append('<Relationship Id="rIdCom" Type="%s/comments" Target="comments.xml"/>' % R)
        types.append('<Override PartName="/word/comments.xml" ContentType="application/vnd.openxmlformats-officedocument.wordprocessingml.comments+xml"/>')
    if 'signet' in o or 'champs' in o:
        corps.append(_p('', runs='<w:bookmarkStart w:id="5" w:name="Demandes"/><w:r><w:t>Les demandes sont fondées.</w:t></w:r>'
                            '<w:bookmarkEnd w:id="5"/>'))
    if 'champs' in o:
        corps.append(_p('', runs='<w:r><w:t xml:space="preserve">Fait le </w:t></w:r><w:fldSimple w:instr=" DATE \\@ &quot;d MMMM yyyy&quot; ">'
                            '<w:r><w:t>18 novembre 2026</w:t></w:r></w:fldSimple><w:r><w:t xml:space="preserve"> ; voir </w:t></w:r>'
                            '<w:r><w:fldChar w:fldCharType="begin"/></w:r><w:r><w:instrText xml:space="preserve"> REF Demandes \\h </w:instrText></w:r>'
                            '<w:r><w:fldChar w:fldCharType="separate"/></w:r><w:r><w:t>Les demandes sont fondées.</w:t></w:r>'
                            '<w:r><w:fldChar w:fldCharType="end"/></w:r>'))
    if 'numerotation' in o:
        for i, t in enumerate(('Sur la compétence', 'Sur le fond', 'Sur les frais')):
            corps.append(_p(t, '<w:numPr><w:ilvl w:val="0"/><w:numId w:val="1"/></w:numPr>'))
        parties['word/numbering.xml'] = ('<?xml version="1.0" encoding="UTF-8" standalone="yes"?><w:numbering %s><w:abstractNum w:abstractNumId="0">'
                                         '<w:lvl w:ilvl="0"><w:start w:val="1"/><w:numFmt w:val="decimal"/><w:lvlText w:val="%%1."/>'
                                         '<w:lvlJc w:val="left"/><w:pPr><w:ind w:left="720" w:hanging="360"/></w:pPr></w:lvl></w:abstractNum>'
                                         '<w:num w:numId="1"><w:abstractNumId w:val="0"/></w:num></w:numbering>') % NS
        rels.append('<Relationship Id="rIdNum" Type="%s/numbering" Target="numbering.xml"/>' % R)
        types.append('<Override PartName="/word/numbering.xml" ContentType="application/vnd.openxmlformats-officedocument.wordprocessingml.numbering+xml"/>')
    if 'notes' in o:
        corps.append(_p('', runs='<w:r><w:t>La jurisprudence est constante.</w:t></w:r><w:r><w:rPr><w:vertAlign w:val="superscript"/></w:rPr>'
                            '<w:footnoteReference w:id="1"/></w:r>'))
        parties['word/footnotes.xml'] = ('<?xml version="1.0" encoding="UTF-8" standalone="yes"?><w:footnotes %s>'
                                         '<w:footnote w:type="separator" w:id="-1"><w:p><w:r><w:separator/></w:r></w:p></w:footnote>'
                                         '<w:footnote w:type="continuationSeparator" w:id="0"><w:p><w:r><w:continuationSeparator/></w:r></w:p></w:footnote>'
                                         '<w:footnote w:id="1"><w:p><w:r><w:t>Décision fictive, chambre de Valbrune, 2025.</w:t></w:r></w:p></w:footnote>'
                                         '</w:footnotes>') % NS
        rels.append('<Relationship Id="rIdFn" Type="%s/footnotes" Target="footnotes.xml"/>' % R)
        types.append('<Override PartName="/word/footnotes.xml" ContentType="application/vnd.openxmlformats-officedocument.wordprocessingml.footnotes+xml"/>')
    if 'tableau' in o:
        lignes = ''.join('<w:tr>%s</w:tr>' % ''.join('<w:tc><w:tcPr><w:tcW w:w="3000" w:type="dxa"/></w:tcPr>%s</w:tc>' % _p(c) for c in ligne)
                         for ligne in (('Pièce', 'Date', 'Objet'), ('1', '2 mars 2026', 'Contrat'), ('2', '9 mai 2026', 'Mise en demeure')))
        corps.append('<w:tbl><w:tblPr><w:tblW w:w="9000" w:type="dxa"/><w:tblBorders><w:top w:val="single" w:sz="4"/>'
                     '<w:bottom w:val="single" w:sz="4"/><w:insideH w:val="single" w:sz="4"/><w:insideV w:val="single" w:sz="4"/></w:tblBorders></w:tblPr>'
                     '<w:tblGrid><w:gridCol w:w="3000"/><w:gridCol w:w="3000"/><w:gridCol w:w="3000"/></w:tblGrid>%s</w:tbl>' % lignes)
    if 'controle' in o:
        corps.append('<w:sdt><w:sdtPr><w:alias w:val="Client"/><w:tag w:val="client"/></w:sdtPr><w:sdtContent>%s</w:sdtContent></w:sdt>'
                     % _p('Société ZORVAL'))
    if 'lien' in o:
        corps.append(_p('', runs='<w:hyperlink r:id="rIdLien"><w:r><w:t>Site fictif du tribunal</w:t></w:r></w:hyperlink>'))
        rels.append('<Relationship Id="rIdLien" Type="%s/hyperlink" Target="https://tribunal.example.test/" TargetMode="External"/>' % R)
    if 'saut' in o:
        corps.append(_p('', runs='<w:r><w:br w:type="page"/></w:r>'))
        corps.append(_p('Bordereau de communication de pièces'))
    if 'image' in o:
        corps.append(_p('', runs=('<w:r><w:drawing><wp:inline><wp:extent cx="95250" cy="95250"/><wp:docPr id="1" name="Image 1"/>'
                                  '<a:graphic><a:graphicData uri="http://schemas.openxmlformats.org/drawingml/2006/picture"><pic:pic><pic:nvPicPr>'
                                  '<pic:cNvPr id="1" name="logo.png"/><pic:cNvPicPr/></pic:nvPicPr><pic:blipFill><a:blip r:embed="rIdImg"/>'
                                  '<a:stretch><a:fillRect/></a:stretch></pic:blipFill><pic:spPr><a:xfrm><a:off x="0" y="0"/><a:ext cx="95250" cy="95250"/>'
                                  '</a:xfrm><a:prstGeom prst="rect"><a:avLst/></a:prstGeom></pic:spPr></pic:pic></a:graphicData></a:graphic>'
                                  '</wp:inline></w:drawing></w:r>')))
        parties['word/media/image1.png'] = PNG
        rels.append('<Relationship Id="rIdImg" Type="%s/image" Target="media/image1.png"/>' % R)
    if 'zone_texte' in o:
        corps.append(_p('', runs=('<w:r><w:pict><v:shape id="zt1" style="width:200pt;height:40pt"><v:textbox><w:txbxContent>%s</w:txbxContent>'
                                  '</v:textbox></v:shape></w:pict></w:r>') % _p('Encadré : délai de deux mois.')))
    if 'objet' in o:
        corps.append('<w:altChunk r:id="rIdChunk"/>')
        parties['word/annexe.htm'] = b'<html><body><p>Annexe fictive</p></body></html>'
        rels.append('<Relationship Id="rIdChunk" Type="%s/aFChunk" Target="annexe.htm"/>' % R)
        types.append('<Default Extension="htm" ContentType="application/xhtml+xml"/>')
    if 'entetes' in o or 'champs' in o:
        parties['word/header1.xml'] = ('<?xml version="1.0" encoding="UTF-8" standalone="yes"?><w:hdr %s>%s</w:hdr>') % (
            NS, _p('Cabinet fictif — Dossier DOS-001'))
        pied = ('<w:r><w:t xml:space="preserve">Page </w:t></w:r><w:r><w:fldChar w:fldCharType="begin"/></w:r><w:r><w:instrText xml:space="preserve"> PAGE </w:instrText></w:r>'
                '<w:r><w:fldChar w:fldCharType="separate"/></w:r><w:r><w:t>1</w:t></w:r><w:r><w:fldChar w:fldCharType="end"/></w:r>') if 'champs' in o else \
            '<w:r><w:t>Projet à relire — DURAND c/ MARTIN</w:t></w:r>'
        parties['word/footer1.xml'] = ('<?xml version="1.0" encoding="UTF-8" standalone="yes"?><w:ftr %s>%s</w:ftr>') % (NS, _p('', runs=pied))
        rels.append('<Relationship Id="rIdH1" Type="%s/header" Target="header1.xml"/>' % R)
        rels.append('<Relationship Id="rIdF1" Type="%s/footer" Target="footer1.xml"/>' % R)
        types.append('<Override PartName="/word/header1.xml" ContentType="application/vnd.openxmlformats-officedocument.wordprocessingml.header+xml"/>')
        types.append('<Override PartName="/word/footer1.xml" ContentType="application/vnd.openxmlformats-officedocument.wordprocessingml.footer+xml"/>')
        sect_refs = '<w:headerReference w:type="default" r:id="rIdH1"/><w:footerReference w:type="default" r:id="rIdF1"/>'
    page = '<w:pgSz w:w="11906" w:h="16838"/><w:pgMar w:top="1418" w:right="1418" w:bottom="1418" w:left="1418" w:header="709" w:footer="709" w:gutter="0"/>'
    if 'sections' in o:
        corps.append(_p('Fin de la première section.', '<w:sectPr>%s%s</w:sectPr>' % (sect_refs, page)))
        corps.append(_p('Annexe en format paysage.'))
        page = '<w:pgSz w:w="16838" w:h="11906" w:orient="landscape"/><w:pgMar w:top="1418" w:right="1418" w:bottom="1418" w:left="1418" w:header="709" w:footer="709" w:gutter="0"/>'
    corps.append(_p('Sous toutes réserves.'))
    document = ('<?xml version="1.0" encoding="UTF-8" standalone="yes"?><w:document %s><w:body>%s<w:sectPr>%s%s</w:sectPr></w:body></w:document>') % (
        NS, ''.join(corps), sect_refs, page)
    styles = ('<?xml version="1.0" encoding="UTF-8" standalone="yes"?><w:styles %s><w:docDefaults><w:rPrDefault><w:rPr><w:rFonts w:ascii="Garamond" '
              'w:hAnsi="Garamond"/><w:sz w:val="24"/><w:lang w:val="fr-FR"/></w:rPr></w:rPrDefault></w:docDefaults>'
              '<w:style w:type="paragraph" w:default="1" w:styleId="Normal"><w:name w:val="Normal"/></w:style>'
              '<w:style w:type="paragraph" w:styleId="Titre1"><w:name w:val="heading 1"/><w:basedOn w:val="Normal"/><w:pPr><w:outlineLvl w:val="0"/></w:pPr>'
              '<w:rPr><w:b/><w:sz w:val="32"/></w:rPr></w:style></w:styles>') % NS
    reglages = None
    if 'protection' in o:
        reglages = ('<?xml version="1.0" encoding="UTF-8" standalone="yes"?><w:settings %s><w:documentProtection w:edit="readOnly" w:enforcement="1"/>'
                    '</w:settings>') % NS
        rels.append('<Relationship Id="rIdSet" Type="%s/settings" Target="settings.xml"/>' % R)
        types.append('<Override PartName="/word/settings.xml" ContentType="application/vnd.openxmlformats-officedocument.wordprocessingml.settings+xml"/>')
    if 'macros' in o:
        parties['word/vbaProject.bin'] = b'\xd0\xcf\x11\xe0fictif'
        types.append('<Default Extension="bin" ContentType="application/vnd.ms-office.vbaProject"/>')
    out = io.BytesIO()
    with zipfile.ZipFile(out, 'w', zipfile.ZIP_DEFLATED) as z:
        z.writestr('[Content_Types].xml', '<?xml version="1.0" encoding="UTF-8" standalone="yes"?><Types xmlns="http://schemas.openxmlformats.org/package/'
                   '2006/content-types"><Default Extension="rels" ContentType="application/vnd.openxmlformats-package.relationships+xml"/>'
                   '<Default Extension="xml" ContentType="application/xml"/><Default Extension="png" ContentType="image/png"/>%s</Types>' % ''.join(types))
        z.writestr('_rels/.rels', '<?xml version="1.0" encoding="UTF-8" standalone="yes"?><Relationships xmlns="http://schemas.openxmlformats.org/package/'
                   '2006/relationships"><Relationship Id="rId1" Type="%s/officeDocument" Target="word/document.xml"/></Relationships>' % R)
        z.writestr('word/_rels/document.xml.rels', '<?xml version="1.0" encoding="UTF-8" standalone="yes"?><Relationships xmlns="http://schemas.'
                   'openxmlformats.org/package/2006/relationships">%s</Relationships>' % ''.join(rels))
        z.writestr('word/document.xml', document)
        z.writestr('word/styles.xml', styles)
        if reglages:
            z.writestr('word/settings.xml', reglages)
        for nom, contenu in parties.items():
            z.writestr(nom, contenu)
    return out.getvalue()


# Fichiers de la mesure : un par caractéristique, puis un acte « complet » qui les réunit (sauf macros et objet).
JEU = {('fictif-' + o): (o,) for o in TOUTES}
JEU['fictif-complet'] = tuple(x for x in TOUTES if x not in ('macros', 'objet', 'protection'))
JEU['fictif-simple'] = ()
