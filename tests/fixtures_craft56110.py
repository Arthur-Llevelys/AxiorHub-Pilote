"""5.6.110 (lot E) : documents fictifs VALIDES (bibliothèque standard seulement) pour GridCraft, PdfCraft et DeckCraft — classeur .xlsx,
PDF texte d'une page, présentation .pptx d'une diapositive. Données fictives (DURAND, MARTIN, DOS-001). Servent aux essais
(``test_v56110_craft``) et au parcours navigateur (``scripts/parcours_p110e.py``), où les vraies applications les ouvrent."""
import io
import zipfile

_CT = 'http://schemas.openxmlformats.org/package/2006/content-types'
_RELS = 'http://schemas.openxmlformats.org/package/2006/relationships'
_R = 'http://schemas.openxmlformats.org/officeDocument/2006/relationships'
_SML = 'http://schemas.openxmlformats.org/spreadsheetml/2006/main'
_PML = 'http://schemas.openxmlformats.org/presentationml/2006/main'
_DML = 'http://schemas.openxmlformats.org/drawingml/2006/main'
_DATE = (1980, 1, 1, 0, 0, 0)          # horodatage fixe : mêmes octets à chaque appel


def _zip(parties):
    out = io.BytesIO()
    with zipfile.ZipFile(out, 'w', zipfile.ZIP_DEFLATED) as z:
        for nom, texte in parties:
            info = zipfile.ZipInfo(nom, _DATE)
            info.compress_type = zipfile.ZIP_DEFLATED
            z.writestr(info, texte.encode('utf-8') if isinstance(texte, str) else texte)
    return out.getvalue()


def _x(texte):
    return str(texte).replace('&', '&amp;').replace('<', '&lt;').replace('>', '&gt;')


def xlsx_fictif(lignes=(('Créancier', 'Montant'), ('DURAND', 1200), ('MARTIN', 800)), formule=True):
    """Classeur d'une feuille « Créances » : lignes données (texte en chaînes intégrées, nombres), puis une somme en formule."""
    rangs = []
    for i, ligne in enumerate(lignes, 1):
        cellules = []
        for j, v in enumerate(ligne):
            ref = '%s%d' % (chr(65 + j), i)
            if isinstance(v, (int, float)):
                cellules.append('<c r="%s"><v>%s</v></c>' % (ref, v))
            else:
                cellules.append('<c r="%s" t="inlineStr"><is><t>%s</t></is></c>' % (ref, _x(v)))
        rangs.append('<row r="%d">%s</row>' % (i, ''.join(cellules)))
    if formule:
        n = len(lignes)
        total = sum(v for l in lignes[1:] for v in l[1:] if isinstance(v, (int, float)))
        rangs.append('<row r="%d"><c r="A%d" t="inlineStr"><is><t>Total</t></is></c><c r="B%d"><f>SUM(B2:B%d)</f><v>%s</v></c></row>' % (
            n + 1, n + 1, n + 1, n, total))
    parties = [
        ('[Content_Types].xml', '<?xml version="1.0" encoding="UTF-8" standalone="yes"?><Types xmlns="%s">'
         '<Default Extension="rels" ContentType="application/vnd.openxmlformats-package.relationships+xml"/>'
         '<Default Extension="xml" ContentType="application/xml"/>'
         '<Override PartName="/xl/workbook.xml" ContentType="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet.main+xml"/>'
         '<Override PartName="/xl/worksheets/sheet1.xml" ContentType="application/vnd.openxmlformats-officedocument.spreadsheetml.worksheet+xml"/>'
         '<Override PartName="/xl/styles.xml" ContentType="application/vnd.openxmlformats-officedocument.spreadsheetml.styles+xml"/>'
         '</Types>' % _CT),
        ('_rels/.rels', '<?xml version="1.0" encoding="UTF-8" standalone="yes"?><Relationships xmlns="%s">'
         '<Relationship Id="rId1" Type="%s/officeDocument" Target="xl/workbook.xml"/></Relationships>' % (_RELS, _R)),
        ('xl/workbook.xml', '<?xml version="1.0" encoding="UTF-8" standalone="yes"?><workbook xmlns="%s" xmlns:r="%s"><sheets>'
         '<sheet name="Créances" sheetId="1" r:id="rId1"/></sheets></workbook>' % (_SML, _R)),
        ('xl/_rels/workbook.xml.rels', '<?xml version="1.0" encoding="UTF-8" standalone="yes"?><Relationships xmlns="%s">'
         '<Relationship Id="rId1" Type="%s/worksheet" Target="worksheets/sheet1.xml"/>'
         '<Relationship Id="rId2" Type="%s/styles" Target="styles.xml"/></Relationships>' % (_RELS, _R, _R)),
        ('xl/styles.xml', '<?xml version="1.0" encoding="UTF-8" standalone="yes"?><styleSheet xmlns="%s">'
         '<fonts count="1"><font><sz val="11"/><name val="Calibri"/></font></fonts><fills count="2"><fill><patternFill patternType="none"/></fill>'
         '<fill><patternFill patternType="gray125"/></fill></fills><borders count="1"><border><left/><right/><top/><bottom/><diagonal/></border></borders>'
         '<cellStyleXfs count="1"><xf numFmtId="0" fontId="0" fillId="0" borderId="0"/></cellStyleXfs>'
         '<cellXfs count="1"><xf numFmtId="0" fontId="0" fillId="0" borderId="0" xfId="0"/></cellXfs>'
         '<cellStyles count="1"><cellStyle name="Normal" xfId="0" builtinId="0"/></cellStyles></styleSheet>' % _SML),
        ('xl/worksheets/sheet1.xml', '<?xml version="1.0" encoding="UTF-8" standalone="yes"?><worksheet xmlns="%s"><sheetData>%s</sheetData></worksheet>'
         % (_SML, ''.join(rangs))),
    ]
    return _zip(parties)


def pdf_fictif(lignes=('Bordereau fictif DOS-001', 'Client : DURAND'), pages=1):
    """PDF texte (Helvetica, WinAnsi), ``pages`` pages, table des renvois exacte."""
    objets = {1: '<< /Type /Catalog /Pages 2 0 R >>', 3: '<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica /Encoding /WinAnsiEncoding >>'}
    kids = []
    num = 4
    for n in range(pages):
        flux = 'BT /F1 18 Tf 72 760 Td 24 TL ' + ' '.join('(%s) Tj T*' % t.replace('\\', '\\\\').replace('(', '\\(').replace(')', '\\)')
                                                       for t in list(lignes) + (['Page %d' % (n + 1)] if pages > 1 else [])) + ' ET'
        brut = flux.encode('cp1252')
        objets[num] = '<< /Type /Page /Parent 2 0 R /MediaBox [0 0 595 842] /Resources << /Font << /F1 3 0 R >> >> /Contents %d 0 R >>' % (num + 1)
        objets[num + 1] = brut
        kids.append('%d 0 R' % num)
        num += 2
    objets[2] = '<< /Type /Pages /Kids [%s] /Count %d >>' % (' '.join(kids), pages)
    out = io.BytesIO()
    out.write(b'%PDF-1.4\n%\xe2\xe3\xcf\xd3\n')
    positions = {}
    for i in sorted(objets):
        positions[i] = out.tell()
        corps = objets[i]
        if isinstance(corps, bytes):
            out.write(b'%d 0 obj\n<< /Length %d >>\nstream\n' % (i, len(corps)) + corps + b'\nendstream\nendobj\n')
        else:
            out.write(('%d 0 obj\n%s\nendobj\n' % (i, corps)).encode('latin-1'))
    xref = out.tell()
    total = max(objets) + 1
    out.write(b'xref\n0 %d\n0000000000 65535 f \n' % total)
    for i in range(1, total):
        out.write(b'%010d 00000 n \n' % positions[i])
    out.write(b'trailer\n<< /Size %d /Root 1 0 R >>\nstartxref\n%d\n%%%%EOF\n' % (total, xref))
    return out.getvalue()


def pptx_fictif(diapos=(('Restitution fictive', 'Dossier DOS-001 : MARTIN'),)):
    """Présentation 16:9 : masque, disposition, thème, une diapositive (titre, texte) par élément de ``diapos``."""
    def sp(ident, nom, ph, texte, y):
        return ('<p:sp><p:nvSpPr><p:cNvPr id="%d" name="%s"/><p:cNvSpPr><a:spLocks noGrp="1"/></p:cNvSpPr><p:nvPr><p:ph %s/></p:nvPr></p:nvSpPr>'
                '<p:spPr><a:xfrm><a:off x="838200" y="%d"/><a:ext cx="10515600" cy="1325563"/></a:xfrm></p:spPr>'
                '<p:txBody><a:bodyPr/><a:lstStyle/><a:p><a:r><a:rPr lang="fr-FR"/><a:t>%s</a:t></a:r></a:p></p:txBody></p:sp>') % (ident, nom, ph, y, _x(texte))
    arbre = ('<p:cSld><p:spTree><p:nvGrpSpPr><p:cNvPr id="1" name=""/><p:cNvGrpSpPr/><p:nvPr/></p:nvGrpSpPr><p:grpSpPr/>%s</p:spTree></p:cSld>')
    ns = 'xmlns:a="%s" xmlns:r="%s" xmlns:p="%s"' % (_DML, _R, _PML)
    entete = '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
    theme = (entete + '<a:theme xmlns:a="%s" name="Fictif"><a:themeElements><a:clrScheme name="Fictif">'
             '<a:dk1><a:srgbClr val="000000"/></a:dk1><a:lt1><a:srgbClr val="FFFFFF"/></a:lt1><a:dk2><a:srgbClr val="1F2937"/></a:dk2>'
             '<a:lt2><a:srgbClr val="F3F4F6"/></a:lt2><a:accent1><a:srgbClr val="2563EB"/></a:accent1><a:accent2><a:srgbClr val="16A34A"/></a:accent2>'
             '<a:accent3><a:srgbClr val="D97706"/></a:accent3><a:accent4><a:srgbClr val="DC2626"/></a:accent4><a:accent5><a:srgbClr val="7C3AED"/></a:accent5>'
             '<a:accent6><a:srgbClr val="0891B2"/></a:accent6><a:hlink><a:srgbClr val="2563EB"/></a:hlink><a:folHlink><a:srgbClr val="7C3AED"/></a:folHlink>'
             '</a:clrScheme><a:fontScheme name="Fictif"><a:majorFont><a:latin typeface="Calibri"/><a:ea typeface=""/><a:cs typeface=""/></a:majorFont>'
             '<a:minorFont><a:latin typeface="Calibri"/><a:ea typeface=""/><a:cs typeface=""/></a:minorFont></a:fontScheme>'
             '<a:fmtScheme name="Fictif"><a:fillStyleLst><a:solidFill><a:schemeClr val="phClr"/></a:solidFill><a:solidFill><a:schemeClr val="phClr"/></a:solidFill>'
             '<a:solidFill><a:schemeClr val="phClr"/></a:solidFill></a:fillStyleLst><a:lnStyleLst><a:ln w="6350"><a:solidFill><a:schemeClr val="phClr"/></a:solidFill></a:ln>'
             '<a:ln w="12700"><a:solidFill><a:schemeClr val="phClr"/></a:solidFill></a:ln><a:ln w="19050"><a:solidFill><a:schemeClr val="phClr"/></a:solidFill></a:ln>'
             '</a:lnStyleLst><a:effectStyleLst><a:effectStyle><a:effectLst/></a:effectStyle><a:effectStyle><a:effectLst/></a:effectStyle>'
             '<a:effectStyle><a:effectLst/></a:effectStyle></a:effectStyleLst><a:bgFillStyleLst><a:solidFill><a:schemeClr val="phClr"/></a:solidFill>'
             '<a:solidFill><a:schemeClr val="phClr"/></a:solidFill><a:solidFill><a:schemeClr val="phClr"/></a:solidFill></a:bgFillStyleLst></a:fmtScheme>'
             '</a:themeElements></a:theme>') % _DML
    master = (entete + '<p:sldMaster %s>%s<p:clrMap bg1="lt1" tx1="dk1" bg2="lt2" tx2="dk2" accent1="accent1" accent2="accent2" accent3="accent3" '
              'accent4="accent4" accent5="accent5" accent6="accent6" hlink="hlink" folHlink="folHlink"/>'
              '<p:sldLayoutIdLst><p:sldLayoutId id="2147483649" r:id="rId1"/></p:sldLayoutIdLst></p:sldMaster>') % (
        ns, arbre % (sp(2, 'Titre', 'type="title"', 'Titre', 365125) + sp(3, 'Texte', 'idx="1"', 'Texte', 1825625)))
    layout = (entete + '<p:sldLayout %s type="obj" preserve="1">%s<p:clrMapOvr><a:masterClrMapping/></p:clrMapOvr></p:sldLayout>') % (
        ns, arbre % (sp(2, 'Titre', 'type="title"', 'Titre', 365125) + sp(3, 'Texte', 'idx="1"', 'Texte', 1825625)))
    parties = []
    ct = ['<Default Extension="rels" ContentType="application/vnd.openxmlformats-package.relationships+xml"/>',
          '<Default Extension="xml" ContentType="application/xml"/>',
          '<Override PartName="/ppt/presentation.xml" ContentType="application/vnd.openxmlformats-officedocument.presentationml.presentation.main+xml"/>',
          '<Override PartName="/ppt/slideMasters/slideMaster1.xml" ContentType="application/vnd.openxmlformats-officedocument.presentationml.slideMaster+xml"/>',
          '<Override PartName="/ppt/slideLayouts/slideLayout1.xml" ContentType="application/vnd.openxmlformats-officedocument.presentationml.slideLayout+xml"/>',
          '<Override PartName="/ppt/theme/theme1.xml" ContentType="application/vnd.openxmlformats-officedocument.theme+xml"/>']
    ids, rels = [], ['<Relationship Id="rId1" Type="%s/slideMaster" Target="slideMasters/slideMaster1.xml"/>' % _R,
                     '<Relationship Id="rId2" Type="%s/theme" Target="theme/theme1.xml"/>' % _R]
    for i, (titre, texte) in enumerate(diapos, 1):
        ct.append('<Override PartName="/ppt/slides/slide%d.xml" ContentType="application/vnd.openxmlformats-officedocument.presentationml.slide+xml"/>' % i)
        parties.append(('ppt/slides/slide%d.xml' % i, entete + '<p:sld %s>%s<p:clrMapOvr><a:masterClrMapping/></p:clrMapOvr></p:sld>' % (
            ns, arbre % (sp(2, 'Titre 1', 'type="title"', titre, 365125) + sp(3, 'Texte 2', 'idx="1"', texte, 1825625)))))
        parties.append(('ppt/slides/_rels/slide%d.xml.rels' % i, entete + '<Relationships xmlns="%s"><Relationship Id="rId1" Type="%s/slideLayout" '
                        'Target="../slideLayouts/slideLayout1.xml"/></Relationships>' % (_RELS, _R)))
        ids.append('<p:sldId id="%d" r:id="rId%d"/>' % (255 + i, 2 + i))
        rels.append('<Relationship Id="rId%d" Type="%s/slide" Target="slides/slide%d.xml"/>' % (2 + i, _R, i))
    tete = [
        ('[Content_Types].xml', entete + '<Types xmlns="%s">%s</Types>' % (_CT, ''.join(ct))),
        ('_rels/.rels', entete + '<Relationships xmlns="%s"><Relationship Id="rId1" Type="%s/officeDocument" Target="ppt/presentation.xml"/></Relationships>'
         % (_RELS, _R)),
        ('ppt/presentation.xml', entete + '<p:presentation %s><p:sldMasterIdLst><p:sldMasterId id="2147483648" r:id="rId1"/></p:sldMasterIdLst>'
         '<p:sldIdLst>%s</p:sldIdLst><p:sldSz cx="12192000" cy="6858000"/><p:notesSz cx="6858000" cy="9144000"/></p:presentation>' % (ns, ''.join(ids))),
        ('ppt/_rels/presentation.xml.rels', entete + '<Relationships xmlns="%s">%s</Relationships>' % (_RELS, ''.join(rels))),
        ('ppt/slideMasters/slideMaster1.xml', master),
        ('ppt/slideMasters/_rels/slideMaster1.xml.rels', entete + '<Relationships xmlns="%s"><Relationship Id="rId1" Type="%s/slideLayout" '
         'Target="../slideLayouts/slideLayout1.xml"/><Relationship Id="rId2" Type="%s/theme" Target="../theme/theme1.xml"/></Relationships>' % (_RELS, _R, _R)),
        ('ppt/slideLayouts/slideLayout1.xml', layout),
        ('ppt/slideLayouts/_rels/slideLayout1.xml.rels', entete + '<Relationships xmlns="%s"><Relationship Id="rId1" Type="%s/slideMaster" '
         'Target="../slideMasters/slideMaster1.xml"/></Relationships>' % (_RELS, _R)),
        ('ppt/theme/theme1.xml', theme),
    ]
    return _zip(tete + parties)
