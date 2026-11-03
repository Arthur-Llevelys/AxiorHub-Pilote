"""5.6.93, lot « aperçu » : audit 5.6.90, anomalies N04 (aperçu vide des courriels HTML) et N09 (export CSV incomplet).

Données fictives, stockage simulé (``test_v5689_livrables.Stockage``), aucun réseau. Les classeurs sont construits ici en Python standard :
colonne 201, plus de 100 000 lignes, plusieurs feuilles, dates, Unicode, formules sans valeur enregistrée, formules dangereuses.
"""
import csv
import hashlib
import io
import json
import socket
import unittest
from html.parser import HTMLParser
from unittest.mock import patch
import zipfile

import test_v5689_livrables as fixtures
from agent import livrables_apercu5689 as apercu, livrables_export5689 as export, livrables_web5689 as web
from agent.common import Stop

MID = fixtures.MID
AVOCAT = fixtures.AVOCAT


# ------------------------------------------------------------------------------------------------ outils N04
def courriel(html='', texte='', charset='utf-8'):
    """Courriel fictif : partie texte et partie HTML (multipart/alternative) ou une seule des deux."""
    head = 'From: Maître BERNARD <confrere@example.test>\r\nTo: cabinet@example.test\r\nSubject: Dossier RG 24/01234\r\nMIME-Version: 1.0\r\n'
    parts = []
    if texte:
        parts.append('Content-Type: text/plain; charset=utf-8\r\nContent-Transfer-Encoding: 8bit\r\n\r\n' + texte)
    if html:
        parts.append('Content-Type: text/html; charset="%s"\r\nContent-Transfer-Encoding: 8bit\r\n\r\n' % charset + html)
    if len(parts) == 1:
        return (head + parts[0]).encode('utf-8')
    if not parts:
        return (head + 'Content-Type: text/plain; charset=utf-8\r\n\r\n').encode('utf-8')
    body = ''.join('--frontiere5693\r\n%s\r\n' % p for p in parts) + '--frontiere5693--\r\n'
    return (head + 'Content-Type: multipart/alternative; boundary="frontiere5693"\r\n\r\n' + body).encode('utf-8')


class _Inventaire(HTMLParser):
    """Relit une sortie assainie : balises, attributs et équilibre des ouvertures et fermetures."""
    VIDES = {'br', 'hr'}

    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.balises, self.attributs, self.pile, self.desequilibres, self.texte = set(), set(), [], 0, []

    def handle_starttag(self, tag, attrs):
        self.balises.add(tag)
        self.attributs.update((tag, k, v if k == 'class' else '') for k, v in attrs)
        if tag not in self.VIDES:
            self.pile.append(tag)

    def handle_endtag(self, tag):
        if not self.pile or self.pile.pop() != tag:
            self.desequilibres += 1

    def handle_data(self, data):
        self.texte.append(data)


def inventaire(html):
    p = _Inventaire()
    p.feed(html)
    p.close()
    return p


def violations(html):
    """Ce qu'une sortie assainie ne doit jamais contenir : balise hors liste blanche, attribut actif ou chargement extérieur."""
    inv = inventaire(html)
    bad = [t for t in inv.balises if t not in apercu.ALLOWED | {'span'}]
    bad += ['%s@%s' % (t, k) for t, k, v in inv.attributs if not ((k in ('colspan', 'rowspan') and t in ('td', 'th')) or (k == 'class' and (t, v) in (('span', 'a89-bloque'), ('div', 'a89-tete'))))]
    low = html.lower()
    bad += [m for m in ('<script', 'src=', 'href=', 'url(', 'style=', 'srcset', 'onerror', 'onclick', 'onmouseover', 'http-equiv', 'formaction', 'poster=') if m in low]
    return bad


# ------------------------------------------------------------------------------------------------ outils N09
NS = 'http://schemas.openxmlformats.org/spreadsheetml/2006/main'
RN = 'http://schemas.openxmlformats.org/officeDocument/2006/relationships'


def cellule(ref, valeur, kind='inlineStr', style=None, formule=None):
    s = ' s="%d"' % style if style is not None else ''
    f = '<f>%s</f>' % formule if formule is not None else ''
    if kind == 'inlineStr':
        return '<c r="%s" t="inlineStr"%s>%s<is><t xml:space="preserve">%s</t></is></c>' % (ref, s, f, valeur)
    if kind == 'n':
        return '<c r="%s"%s>%s%s</c>' % (ref, s, f, '' if valeur is None else '<v>%s</v>' % valeur)
    return '<c r="%s" t="%s"%s>%s%s</c>' % (ref, kind, s, f, '' if valeur is None else '<v>%s</v>' % valeur)


def classeur(feuilles, styles='', partages=None, date1904=False):
    """Classeur XLSX minimal : ``feuilles`` = [(nom, contenu de sheetData)] ; ``partages`` = liste XML de <si> ; ``styles`` = contenu de styles.xml."""
    data = io.BytesIO()
    with zipfile.ZipFile(data, 'w', zipfile.ZIP_DEFLATED) as z:
        z.writestr('[Content_Types].xml', '<Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types"><Default Extension="rels" ContentType="application/vnd.openxmlformats-package.relationships+xml"/><Default Extension="xml" ContentType="application/xml"/></Types>')
        z.writestr('_rels/.rels', '<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships"><Relationship Id="root" Type="%s/officeDocument" Target="xl/workbook.xml"/></Relationships>' % RN)
        sheets = ''.join('<sheet name="%s" sheetId="%d" r:id="s%d"/>' % (nom, i, i) for i, (nom, _) in enumerate(feuilles, 1))
        pr = '<workbookPr date1904="1"/>' if date1904 else ''
        z.writestr('xl/workbook.xml', '<workbook xmlns="%s" xmlns:r="%s">%s<sheets>%s</sheets></workbook>' % (NS, RN, pr, sheets))
        rels = ''.join('<Relationship Id="s%d" Type="%s/worksheet" Target="worksheets/sheet%d.xml"/>' % (i, RN, i) for i in range(1, len(feuilles) + 1))
        z.writestr('xl/_rels/workbook.xml.rels', '<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">%s</Relationships>' % rels)
        for i, (_, contenu) in enumerate(feuilles, 1):
            z.writestr('xl/worksheets/sheet%d.xml' % i, '<worksheet xmlns="%s"><sheetData>%s</sheetData></worksheet>' % (NS, contenu))
        if partages is not None:
            z.writestr('xl/sharedStrings.xml', '<sst xmlns="%s">%s</sst>' % (NS, ''.join(partages)))
        if styles:
            z.writestr('xl/styles.xml', '<styleSheet xmlns="%s">%s</styleSheet>' % (NS, styles))
    return data.getvalue()


def lire_csv(data):
    text = bytes(data).decode('utf-8')
    return list(csv.reader(io.StringIO(text.lstrip('﻿'), newline=''), delimiter=';'))


def grand_classeur(lignes=100_005):
    """Feuille de plus de 100 000 lignes, avec une valeur importante en colonne 201 (GS1) et une dernière ligne repérable."""
    rows = ['<row r="1">%s%s</row>' % (cellule('A1', 'Première colonne'), cellule('GS1', 'VALEUR IMPORTANTE EN COLONNE 201'))]
    rows += ['<row r="%d"><c r="A%d"><v>%d</v></c></row>' % (n, n, n) for n in range(2, lignes)]
    rows.append('<row r="%d">%s</row>' % (lignes, cellule('B%d' % lignes, 'DERNIÈRE LIGNE %d' % lignes)))
    return classeur([('Données', ''.join(rows))])


class Base(fixtures.Base):
    def fichier(self, path, raw, **fields):
        from agent import livrables5689 as liv
        res = liv.reserve(self.desk, 'essai', path, 1, matter=MID, title=fields.pop('title', 'Tableau fictif'), path=path, **fields)
        self.nc.put_file(path, raw)
        return liv.constater(self.desk, res, self.nc, path, raw)

    def page(self, row):
        shell = lambda title, body, *a: body
        env = {'HTTP_X_AXIORHUB_USER': AVOCAT[0], 'HTTP_X_AXIORHUB_ROLE': AVOCAT[1]}
        return web.page_route(env, self.f.c, {'csrf': 'c'}, '/p', '/livrables/' + row['document_id'], {}, shell)['body']


# ================================================================================================ N04
class N04(Base):
    BONJOUR = '<html><head><meta charset="utf-8"><title>Courriel</title></head><body><p>Bonjour, veuillez lire le contrat joint.</p></body></html>'

    def test_N04_T1_head_meta_link_input_autofermantes_et_signature_restent_visibles(self):
        cas = {
            self.BONJOUR: ['Bonjour, veuillez lire le contrat joint.'],
            '<head><link rel="stylesheet" href="https://style.example.test/s.css"><meta name="x" content="y"></head><body><p>Texte après le lien de style</p></body>': ['Texte après le lien de style'],
            '<p>Votre nom :<input type="text" value="DURAND"> merci</p><p>Suite visible</p>': ['merci', 'Suite visible'],
            '<p>Ligne 1<br/>Ligne 2<meta/><link/><div/>Ligne 3</p><p>Ligne 4<img src="https://x.example.test/a.png"/>fin</p>': ['Ligne 1', 'Ligne 2', 'Ligne 3', 'Ligne 4', 'fin'],
            '<html><head><meta http-equiv="Content-Type" content="text/html; charset=utf-8" /></head><body><p>Version XHTML</p></body></html>': ['Version XHTML'],
            ('<div>Cordialement,</div><div class="sig"><table><tr><td><img src="https://logo.example.test/l.png" width="80"></td><td><b>Maître MARTIN</b><br>Avocat au barreau de Valbrune<br>'
             '<a href="mailto:contact@example.test">contact@example.test</a></td></tr></table></div>'): ['Cordialement,', 'Maître MARTIN', 'Avocat au barreau de Valbrune', 'contact@example.test'],
        }
        for html, attendus in cas.items():
            safe, _, _ = apercu.assainir(html)
            for texte in attendus:
                self.assertIn(texte, ''.join(inventaire(safe).texte), html)
            self.assertNotIn('Courriel', ''.join(inventaire(safe).texte))       # le titre de <head> n'est pas du corps
            self.assertEqual(inventaire(safe).desequilibres, 0, safe)
            self.assertEqual(violations(safe), [], safe)
        # contre-épreuve : avec l'ancien comportement (meta compté comme élément à contenu masqué), le corps disparaît
        with patch.object(apercu, 'VIDES', apercu.VIDES - {'meta'}), patch.object(apercu, 'MASQUES', apercu.MASQUES | {'meta'}):
            self.assertNotIn('Bonjour', apercu.assainir('<meta charset="utf-8"><p>Bonjour</p>')[0])
        self.assertIn('Bonjour', apercu.assainir('<meta charset="utf-8"><p>Bonjour</p>')[0])

    def test_N04_T1_courriel_complet_visible_dans_la_visionneuse_sans_reecriture(self):
        raw = courriel(html=self.BONJOUR, texte='Bonjour (version texte).')
        row = self.fichier('/Dossiers/DEMO/Courriel meta.eml', raw)
        out = self.js(self.api('livrables/%s/apercu' % row['document_id']))
        self.assertEqual(out['preview_kind'], 'email')
        self.assertIn('Bonjour, veuillez lire le contrat joint.', out['html'])
        self.assertNotIn('aperçu texte', out['note'])                         # le HTML suffit : pas de repli
        frame = self.api('livrables/%s/cadre' % row['document_id'])
        self.assertIn('Bonjour, veuillez lire', frame['body'])
        self.assertIn("default-src 'none'", frame['csp'])
        self.assertEqual(self.nc.files['/Dossiers/DEMO/Courriel meta.eml'], raw)   # le message d'origine n'est jamais réécrit
        self.assertEqual([e for e in self.nc.ecritures if e[1].endswith('.eml')], [('creation', '/Dossiers/DEMO/Courriel meta.eml')])

    def test_N04_T1_apercu_vide_se_replie_sur_le_texte_avec_la_mention(self):
        html, note = apercu._eml(courriel(html='<html><head><style>p{color:red}</style></head><body><script>x()</script></body></html>', texte='Bonjour, texte du message.'))
        self.assertIn('Bonjour, texte du message.', html)
        self.assertIn('aperçu texte', note.lower())
        # trois situations distinctes : message vide, format illisible, échec de l'assainissement
        vide = apercu.corps_courriel('', '')
        self.assertEqual(vide['etat'], 'vide')
        self.assertIn('vide', vide['note'].lower())
        illisible_html, illisible_note = apercu._eml(courriel(html='<p>Bonjour</p>', charset='x-inconnu-5693'))
        self.assertIn('lisible', illisible_note)
        self.assertNotIn('vide', illisible_note.lower())
        avec_texte = apercu._eml(courriel(html='<p>Bonjour</p>', texte='Texte de secours', charset='x-inconnu-5693'))
        self.assertIn('Texte de secours', avec_texte[0])
        self.assertIn('aperçu texte', avec_texte[1].lower())
        with patch.object(apercu._Assaini, 'feed', side_effect=RecursionError('profondeur')):
            echec = apercu.corps_courriel('<p>Bonjour</p>', 'Texte de secours')
            sans_texte = apercu.corps_courriel('<p>Bonjour</p>', '')
        self.assertEqual((echec['etat'], sans_texte['etat']), ('echec', 'echec'))
        self.assertIn('Texte de secours', echec['html'])
        self.assertIn('aperçu texte', echec['note'].lower())
        self.assertNotIn('Bonjour', sans_texte['html'])                       # jamais le HTML brut à la place
        self.assertEqual(len({vide['note'], illisible_note, sans_texte['note']}), 3)

    def test_N04_T2_variantes_mal_formees_imbrications_et_accents(self):
        cas = [
            ('<html><head><meta charset=utf-8><title>Sans fermeture de head</title><body><p>Corps après head ouvert</p>', 'Corps après head ouvert'),
            ('<head><meta charset=utf-8><head><title>x</title></head></head><p>Deux head</p>', 'Deux head'),
            ('<p>a<div>b</p>c</div><p>Suite après imbrication</p>', 'Suite après imbrication'),
            ('<p>Début</p><head><meta charset=utf-8><p>Suite après un head tardif</p>', 'Suite après un head tardif'),
            ('</div></span></p><p>Après fermetures orphelines</p>', 'Après fermetures orphelines'),
            ('<b><i>gras italique</b> suite non masquée</i>', 'suite non masquée'),
            ('<div><svg><p>dessin</p></div><p>Après un svg non fermé</p>', 'Après un svg non fermé'),
            ('<table><tr><td>cellule<td>cellule 2</tr></table><p>Après tableau</p>', 'Après tableau'),
            ('<META CHARSET="UTF-8"><LINK REL=icon HREF="https://x.example.test/i.ico"><P>MAJUSCULES</P>', 'MAJUSCULES'),
            ('<p>R&eacute;union &agrave; 14&nbsp;h : l’échéance du 3 d&#233;cembre, coût 1 200 €</p>', 'Réunion à 14\xa0h : l’échéance du 3 décembre, coût 1 200 €'),
            ('<p>Accents directs : çà et là, Ökonomie, ZORVAL</p></br><p>Ligne après br fermant</p>', 'Ligne après br fermant'),
            ('<title>Titre ouvert</title><p>Corps après titre</p><style>p{}</style><p>Après style</p>', 'Après style'),
            ('<script>var s="</p><p>faux";</script><p>Après script piégé</p>', 'Après script piégé'),
        ]
        for html, attendu in cas:
            safe, _, _ = apercu.assainir(html)
            texte = ''.join(inventaire(safe).texte)
            self.assertIn(attendu, texte, html)
            self.assertEqual(inventaire(safe).desequilibres, 0, safe)
            self.assertEqual(violations(safe), [], safe)
        self.assertNotIn('faux', ''.join(inventaire(apercu.assainir(cas[-1][0])[0]).texte))

    def test_N04_T3_script_image_distante_et_lien_actif_jamais_reintroduits(self):
        html = ('<html><head><base href="https://base.example.test/"><meta http-equiv="refresh" content="0;url=https://redir.example.test/">'
                '<link rel="stylesheet" href="https://css.example.test/s.css"><style>@import url(https://css.example.test/i.css);</style><SCRIPT>alert(1)</SCRIPT></head><body>'
                '<p onmouseover="vol()">Texte utile</p><img src="https://suivi.example.test/pixel.png" srcset="https://suivi.example.test/2x.png 2x"><img/src=x onerror=alert(2)>'
                '<a href="https://piege.example.test/x" onclick="vol()">Cliquez ici</a> <a href="javascript:alert(3)">Lien script</a><a href="https://y.example.test"><img src="https://y.example.test/b.png"></a>'
                '<div style="background:url(https://fond.example.test/f.png)">Fond</div><svg><image href="https://svg.example.test/i.png"/></svg><input type="image" src="https://btn.example.test/b.png">'
                '<iframe src="https://cadre.example.test/"></iframe><object data="https://obj.example.test/o.swf"></object><embed src="https://emb.example.test/e">'
                '<video poster="https://vid.example.test/p.png"><source src="https://vid.example.test/v.mp4"></video><noscript><img src="https://ns.example.test/n.png"></noscript>'
                '<form action="https://form.example.test/"><button formaction="https://form.example.test/b">Envoyer</button></form><!--[if mso]><img src="https://mso.example.test/m.png"><![endif]-->'
                '<scr<script>ipt>alert(4)</script><p>Fin du message</p></body></html>')
        reseau = []
        with patch.object(socket.socket, 'connect', side_effect=lambda *a: reseau.append(a)), patch('socket.create_connection', side_effect=lambda *a, **k: reseau.append(a)):
            safe, images, liens = apercu.assainir(html)
            vue, note = apercu._eml(courriel(html=html, texte='Texte de secours'))
        self.assertEqual(reseau, [])
        self.assertEqual(violations(safe), [], safe)
        self.assertEqual(violations(vue), [], vue)
        texte = ''.join(inventaire(safe).texte)
        for attendu in ('Texte utile', 'Cliquez ici', 'piege.example.test/x', 'Fin du message', 'Fond'):
            self.assertIn(attendu, texte)
        self.assertNotIn('alert(1)', texte)
        self.assertGreaterEqual(images, 4)
        self.assertEqual(liens, 3)
        self.assertIn('image(s) distante(s) non chargée(s)', note)
        self.assertIn("default-src 'none'", apercu.cadre(safe)['csp'])
        # contre-épreuve : le contrôle détecte bien une sortie non assainie
        with patch.object(apercu, 'assainir', side_effect=lambda h: (h, 0, 0)):
            brut, _ = apercu._eml(courriel(html=html))
        self.assertNotEqual(violations(brut), [])


# ================================================================================================ N09
class N09(Base):
    def test_N09_T1_colonne_201_et_plus_de_100000_lignes_conservees(self):
        from agent import export_tableur5693 as tableur
        raw = grand_classeur()
        data = export.convertir(raw, 'xlsx', 'feuille1.csv')
        rows = lire_csv(data)
        self.assertEqual(len(rows), 100_005)
        self.assertEqual(rows[0][200], 'VALEUR IMPORTANTE EN COLONNE 201')
        self.assertEqual(rows[-1][1], 'DERNIÈRE LIGNE 100005')
        preuve = data.preuve['feuilles'][0]
        self.assertEqual((preuve['plage'], preuve['lignes'], preuve['colonnes'], preuve['statut']), ('A1:GS100005', 100_005, 201, 'complet'))
        self.assertTrue(export.verifier_sortie('feuille1.csv', data))
        self.assertTrue(export.verifier_sortie('feuille1.csv', bytes(data)))        # relecture du fichier écrit : même empreinte, même preuve
        # la vérification rapproche les données de la portée : une ligne retirée est détectée
        tronque = b'\r\n'.join(bytes(data).split(b'\r\n')[:-2]) + b'\r\n'
        self.assertFalse(export.verifier_sortie('feuille1.csv', tronque))
        self.assertFalse(tableur.verifier('feuille1.csv', tronque, data.preuve))
        # l'ancien format d'appel reste honnête : la feuille unique est exportée entière
        self.assertIn(b'VALEUR IMPORTANTE', export.convertir(raw, 'xlsx', 'csv'))
        # au-delà de la limite d'export : refus explicite, jamais un fichier tronqué
        with patch.object(tableur, 'LIMITE_LIGNES', 100_000):
            with self.assertRaises(Stop) as ctx:
                export.convertir(raw, 'xlsx', 'feuille1.csv')
        self.assertEqual(str(ctx.exception), 'export_trop_volumineux')
        with patch.object(tableur, 'LIMITE_COLONNES', 200):
            with self.assertRaises(Stop):
                export.convertir(raw, 'xlsx', 'feuille1.csv')

    def test_N09_T1_export_suivi_jamais_exporte_si_tronque(self):
        from agent import export_tableur5693 as tableur
        row = self.fichier('/Dossiers/DEMO/Grand tableau.xlsx', grand_classeur())
        body = {'format': 'feuille1.csv', 'destination': 'dest-exports', 'cle': 'cle-n09-limite', 'version_id': row['version_id']}
        out = self.js(self.api('livrables/%s/exporter' % row['document_id'], body=body))
        with patch.object(tableur, 'LIMITE_LIGNES', 100_000):
            with self.assertRaises(Stop):
                export.executer(self.desk, {'export': out['id']}, self.nc)
        state = self.js(self.api('livrables/exports/' + out['id']))
        self.assertEqual(state['state'], 'echec')
        self.assertFalse(state['done'])
        self.assertEqual(state['erreur'], 'export_trop_volumineux')
        self.assertEqual([p for _, p in self.nc.ecritures if '/EXPORTS/' in p], [])
        # même demande dans les limites : exporté, relu, complet
        body['cle'] = 'cle-n09-complet'
        out = self.js(self.api('livrables/%s/exporter' % row['document_id'], body=body))
        export.executer(self.desk, {'export': out['id']}, self.nc)
        state = self.js(self.api('livrables/exports/' + out['id']))
        self.assertEqual(state['state'], 'exporte')
        path = next(p for _, p in self.nc.ecritures if '/EXPORTS/' in p)
        rows = lire_csv(self.nc.files[path])
        self.assertEqual((len(rows), rows[0][200], rows[-1][1]), (100_005, 'VALEUR IMPORTANTE EN COLONNE 201', 'DERNIÈRE LIGNE 100005'))

    def _trois_feuilles(self):
        return classeur([('Données', '<row r="1">%s%s</row><row r="2">%s</row>' % (cellule('A1', 'Nom'), cellule('B1', 'Heures'), cellule('A2', 'DURAND'))),
                         ('Honoraires 2026', '<row r="1">%s</row><row r="3">%s</row>' % (cellule('A1', 'Facture'), cellule('C3', 'KERVILAC'))),
                         ('Vide', '')])

    def test_N09_T2_plusieurs_feuilles_choix_visible_et_zip_avec_manifeste(self):
        from agent import export_tableur5693 as tableur
        raw = self._trois_feuilles()
        with self.assertRaises(Stop) as ctx:                       # jamais la première feuille par défaut
            export.convertir(raw, 'xlsx', 'csv')
        self.assertEqual(str(ctx.exception), 'feuille_a_choisir')
        with self.assertRaises(Stop):
            export.convertir(raw, 'xlsx', 'feuille9.csv')
        second = export.convertir(raw, 'xlsx', 'feuille2.csv')
        self.assertEqual(lire_csv(second), [['Facture'], [], ['', '', 'KERVILAC']])
        self.assertEqual(second.preuve['feuilles'][0]['nom'], 'Honoraires 2026')
        self.assertFalse(tableur.verifier('feuille1.csv', second, second.preuve))   # la feuille vérifiée est celle demandée
        data = export.convertir(raw, 'xlsx', 'feuilles.zip')
        self.assertTrue(export.verifier_sortie('feuilles.zip', bytes(data)))
        with zipfile.ZipFile(io.BytesIO(data)) as z:
            manifeste = json.loads(z.read('manifeste.json'))
            self.assertEqual(len(manifeste['feuilles']), 3)
            self.assertEqual(manifeste['feuilles_du_classeur'], 3)
            self.assertEqual(sorted(z.namelist()), sorted(['manifeste.json'] + [f['fichier'] for f in manifeste['feuilles']]))
            for entry in manifeste['feuilles']:
                contenu = z.read(entry['fichier'])
                self.assertEqual(hashlib.sha256(contenu).hexdigest(), entry['sha256'])
                self.assertEqual(len(lire_csv(contenu)), entry['lignes'])
                self.assertEqual(entry['statut'], 'complet')
            self.assertEqual([f['nom'] for f in manifeste['feuilles']], ['Données', 'Honoraires 2026', 'Vide'])
            self.assertEqual(manifeste['source_sha256'], hashlib.sha256(raw).hexdigest())
            self.assertIn('styles', manifeste['avertissement'])
        # ZIP abîmé ou incomplet : refusé
        abime = io.BytesIO()
        with zipfile.ZipFile(io.BytesIO(data)) as src, zipfile.ZipFile(abime, 'w') as dst:
            for name in src.namelist():
                if name != manifeste['feuilles'][1]['fichier']:
                    dst.writestr(name, src.read(name))
        self.assertFalse(export.verifier_sortie('feuilles.zip', abime.getvalue()))
        modifie = io.BytesIO()
        with zipfile.ZipFile(io.BytesIO(data)) as src, zipfile.ZipFile(modifie, 'w') as dst:
            for name in src.namelist():
                dst.writestr(name, src.read(name) + (b'ajout;\r\n' if name == manifeste['feuilles'][0]['fichier'] else b''))
        self.assertFalse(export.verifier_sortie('feuilles.zip', modifie.getvalue()))

    def test_N09_T2_page_propose_la_portee_et_export_suivi_de_la_feuille_choisie(self):
        row = self.fichier('/Dossiers/DEMO/Temps passés.xlsx', self._trois_feuilles())
        html = self.page(row)
        for value, label in (('feuille1.csv', 'Données'), ('feuille2.csv', 'Honoraires 2026'), ('feuille3.csv', 'Vide'), ('feuilles.zip', 'toutes les feuilles')):
            self.assertIn('value="%s"' % value, html)
            self.assertIn(label, html)
        self.assertIn('ni les styles ni les formules', html)
        view = self.js(self.api('livrables/' + row['document_id']))['view']
        self.assertIn('feuilles.zip', view['allowed_formats'])
        refus = self.api('livrables/%s/exporter' % row['document_id'], body={'format': 'csv', 'destination': 'dest-exports', 'cle': 'cle-n09-ancien', 'version_id': row['version_id']})
        self.assertEqual(refus['status'], '400 Bad Request')                  # l'ancien format « première feuille » n'est plus proposé
        out = self.js(self.api('livrables/%s/exporter' % row['document_id'], body={'format': 'feuille2.csv', 'destination': 'dest-exports', 'cle': 'cle-n09-feuille2', 'version_id': row['version_id']}))
        export.executer(self.desk, {'export': out['id']}, self.nc)
        self.assertEqual(self.js(self.api('livrables/exports/' + out['id']))['state'], 'exporte')
        path = next(p for _, p in self.nc.ecritures if '/EXPORTS/' in p)
        self.assertTrue(path.endswith('.feuille2.csv'))
        self.assertEqual(lire_csv(self.nc.files[path])[-1], ['', '', 'KERVILAC'])
        out = self.js(self.api('livrables/%s/exporter' % row['document_id'], body={'format': 'feuilles.zip', 'destination': 'dest-exports', 'cle': 'cle-n09-zip', 'version_id': row['version_id']}))
        export.executer(self.desk, {'export': out['id']}, self.nc)
        zpath = [p for _, p in self.nc.ecritures if p.endswith('.zip')][0]
        self.assertTrue(export.verifier_sortie('feuilles.zip', self.nc.files[zpath]))

    def test_N09_T3_dates_unicode_vides_guillemets_separateurs_formules(self):
        from agent import export_tableur5693 as tableur
        styles = ('<numFmts count="1"><numFmt numFmtId="164" formatCode="dd/mm/yyyy\\ hh:mm"/></numFmts>'
                  '<cellXfs count="4"><xf numFmtId="0"/><xf numFmtId="14"/><xf numFmtId="164"/><xf numFmtId="4"/></cellXfs>')
        partages = ['<si><t>Partagée é</t></si>', '<si><r><t>Riche </t></r><r><t>texte</t></r><rPh><t>phonétique</t></rPh></si>']
        ligne1 = ''.join([cellule('A1', '45962', 'n', style=1), cellule('B1', '45962.75', 'n', style=2), cellule('C1', '1234.5', 'n', style=3), cellule('E1', '0', 's'), cellule('F1', '1', 's')])
        ligne2 = ''.join([cellule('A2', 'Unicode : é à ç – 日本 – 🙂 – ŒUVRE'), cellule('C2', 'Il a dit "oui"'), cellule('D2', 'a;b,c\nligne'), cellule('E2', '1', 'b')])
        ligne3 = ''.join([cellule('A3', '6', 'n', formule='B3*2'), cellule('B3', None, 'n', formule='C3*2'), cellule('C3', '-3', 'n'), cellule('D3', '#DIV/0!', 'e', formule='1/0')])
        ligne4 = ''.join([cellule('A4', '=HYPERLINK("https://piege.example.test","x")'), cellule('B4', '+cmd|calc'), cellule('C4', '-2+3'), cellule('D4', '@SUM(A1)'), cellule('E4', '\t=1')])
        raw = classeur([('Essai', '<row r="1">%s</row><row r="2">%s</row><row r="3">%s</row><row r="4">%s</row>' % (ligne1, ligne2, ligne3, ligne4))], styles=styles, partages=partages)
        data = export.convertir(raw, 'xlsx', 'feuille1.csv')
        rows = lire_csv(data)
        self.assertEqual(rows[0], ['2025-11-01', '2025-11-01 18:00:00', '1234.5', '', 'Partagée é', 'Riche texte'])
        self.assertEqual(rows[1], ['Unicode : é à ç – 日本 – 🙂 – ŒUVRE', '', 'Il a dit "oui"', 'a;b,c\nligne', 'VRAI'])
        self.assertEqual(rows[2], ['6', "'=C3*2", '-3', '#DIV/0!'])           # valeur calculée sans cache : la formule reste visible, inerte
        self.assertEqual(rows[3], ['\'=HYPERLINK("https://piege.example.test","x")', "'+cmd|calc", "'-2+3", "'@SUM(A1)", "'\t=1"])
        feuille = data.preuve['feuilles'][0]
        self.assertEqual(feuille['formules_sans_valeur'], 1)
        self.assertEqual(feuille['cellules_sans_valeur'], ['B3'])
        self.assertEqual(feuille['dates'], 2)
        self.assertGreaterEqual(feuille['formules_neutralisees'], 6)
        self.assertTrue(data.startswith('﻿'.encode('utf-8')))           # accents lisibles à l'ouverture dans un tableur
        self.assertTrue(export.verifier_sortie('feuille1.csv', data))
        # date 1904
        raw1904 = classeur([('A', '<row r="1">%s</row>' % cellule('A1', '0', 'n', style=1))], styles=styles, date1904=True)
        self.assertEqual(lire_csv(export.convertir(raw1904, 'xlsx', 'feuille1.csv'))[0], ['1904-01-01'])
        # contre-épreuve : sans la garde, une formule dangereuse passerait, et la vérification la refuse
        with patch.object(export, '_garde_cellule', side_effect=lambda v: str(v)):
            brut = export.convertir(raw, 'xlsx', 'feuille1.csv')
        self.assertEqual(lire_csv(brut)[3][0], '=HYPERLINK("https://piege.example.test","x")')
        self.assertFalse(export.verifier_sortie('feuille1.csv', brut))
        self.assertFalse(tableur.verifier('feuille1.csv', brut, brut.preuve))

    def test_N09_T3_classeur_hostile_refuse(self):
        doctype = classeur([('A', '')])
        buf = io.BytesIO()
        with zipfile.ZipFile(io.BytesIO(doctype)) as src, zipfile.ZipFile(buf, 'w') as dst:
            for name in src.namelist():
                content = src.read(name)
                if name == 'xl/worksheets/sheet1.xml':
                    content = b'<?xml version="1.0"?><!DOCTYPE x [<!ENTITY a "aaaa"><!ENTITY b "&a;&a;&a;&a;">]><worksheet xmlns="%s"><sheetData><row r="1"><c r="A1" t="inlineStr"><is><t>&b;</t></is></c></row></sheetData></worksheet>' % NS.encode()
                dst.writestr(name, content)
        with self.assertRaises(Stop) as ctx:
            export.convertir(buf.getvalue(), 'xlsx', 'feuille1.csv')
        self.assertEqual(str(ctx.exception), 'classeur_refuse')               # aucune entité développée
        with self.assertRaises(Stop) as ctx:
            export.convertir(b'pas un classeur', 'xlsx', 'feuille1.csv')
        self.assertEqual(str(ctx.exception), 'classeur_illisible')
        manquante = io.BytesIO()                                               # feuille annoncée mais absente : refus, pas un export partiel
        with zipfile.ZipFile(io.BytesIO(self._trois_feuilles()), 'r') as src, zipfile.ZipFile(manquante, 'w') as dst:
            for name in src.namelist():
                if name != 'xl/worksheets/sheet2.xml':
                    dst.writestr(name, src.read(name))
        with self.assertRaises(Stop) as ctx:
            export.convertir(manquante.getvalue(), 'xlsx', 'feuilles.zip')
        self.assertEqual(str(ctx.exception), 'classeur_incomplet')

    def test_N09_reprise_anciens_csv_reperes_conserves_et_nouveau_nom(self):
        from agent import export_tableur5693 as tableur
        row = self.fichier('/Dossiers/DEMO/Tableau.xlsx', classeur([('Données', '<row r="1">%s%s</row>' % (cellule('A1', 'A'), cellule('GS1', 'VALEUR IMPORTANTE')))]))
        export.schema(self.desk)
        ancien = '/Dossiers/DEMO/EXPORTS/Tableau fictif.csv'
        self.nc.add(ancien, 'A\r\n'.encode())
        self.desk.db.execute("INSERT INTO prepared_exports5689(id,idempotency_key,parameters,item_id,document_id,version_id,format,destination,state,output_path,expected_sha256,sha256,size,requested_by,created,updated) "
                             "VALUES('ancien5690','cle-ancienne-5690','[]',?,?,?,'csv','dest-exports','exporte',?,'x','x',3,'avocate@example.test','2026-10-01T00:00:00+00:00','2026-10-01T00:00:00+00:00')",
                             (row['id'], row['document_id'], row['version_id'], ancien))
        self.desk.db.commit()
        liste = tableur.a_recontroler(self.desk, row['document_id'])
        self.assertEqual([x['id'] for x in liste], ['ancien5690'])
        self.assertEqual(liste[0]['fichier'], 'Tableau fictif.csv')
        self.assertIn('à recontrôler', self.page(row))
        out = self.js(self.api('livrables/%s/exporter' % row['document_id'], body={'format': 'feuille1.csv', 'destination': 'dest-exports', 'cle': 'cle-n09-reprise', 'version_id': row['version_id']}))
        export.executer(self.desk, {'export': out['id']}, self.nc)
        nouveau = next(p for _, p in self.nc.ecritures if '/EXPORTS/' in p)
        self.assertNotEqual(nouveau, ancien)
        self.assertEqual(self.nc.files[ancien], 'A\r\n'.encode())             # l'ancien fichier est conservé tel quel
        self.assertIn(b'VALEUR IMPORTANTE', self.nc.files[nouveau])
        liste = tableur.a_recontroler(self.desk, row['document_id'])
        self.assertEqual(liste[0]['remplace_par'], nouveau.rsplit('/', 1)[-1])  # toujours signalé, avec le nouvel export contrôlé
        # aucune donnée ancienne certifiée : l'ancien export reste « à recontrôler » même après le nouvel export
        self.assertEqual(self.desk.db.execute("SELECT state FROM prepared_exports5689 WHERE id='ancien5690'").fetchone()[0], 'exporte')


if __name__ == '__main__':
    unittest.main()
