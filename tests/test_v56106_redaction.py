"""5.6.106 (lot R) : rédaction d'un courriel — mise en forme étendue (styles en liste fermée), date du jour du cabinet, reprise d'un
brouillon, pièces jointes désignées par empreinte (sans doublon), corps borné des brouillons, modèles de réponse du cabinet (droits,
version, idempotence, aucune écriture en lecture), dictée et observation bornée de v310, rédaction compacte (14 px, contraste)."""
from datetime import date
from email import policy
from email.message import EmailMessage
from email.parser import BytesParser
import base64
import io
import json
from pathlib import Path
import re
import unittest
from unittest.mock import patch

from agent.common import Stop
import test_v530 as t530
from test_v5628 import WebBox

ROOT = Path(__file__).resolve().parents[1]
STATIC = ROOT / 'agent' / 'static'


def b64(raw):
    return base64.b64encode(raw).decode()


def lire(uid):
    return BytesParser(policy=policy.default).parsebytes(WebBox.store['Drafts'][int(uid)][1])


class Base(t530.Base):
    """Même installation que ``test_v5628.Webmail`` (fausse messagerie), sans en hériter les tests."""
    def setUp(self):
        super().setUp()
        WebBox.reset()
        for c in (self.f.c, self.desk.c):
            c['mail'].update({'inbox': 'INBOX', 'sent': 'Sent', 'drafts': 'Drafts', 'from_address': 'cabinet@example.test', 'from_name': 'Cabinet EXEMPLE',
                              'own_addresses': ['cabinet@example.test']})
        for target in ('agent.mailbox.Mailbox', 'agent.send490.Mailbox', 'agent.drafts440.Mailbox'):
            p = patch(target, WebBox)
            p.start()
            self.addCleanup(p.stop)


class Redaction(Base):

    # ------------------------------------------------------------------------------------------- nettoyage (R2)
    def test_R2_styles_de_la_liste_fermee_gardes_et_reecrits(self):
        from agent.webmail5628 import clean_html
        sale = ('<p style="text-align: center; color: rgb(180, 35, 24)">Titre</p><span style="background-color:#FFF3A3;font-size:large">surligné</span>'
                '<div style="margin-left: 80px">retrait</div><h2>Un</h2><h3>Deux</h3><hr><p style="text-align:start">g</p>')
        propre = clean_html(sale)
        self.assertIn('<p style="color: #b42318; text-align: center">Titre</p>', propre)
        self.assertIn('<span style="background-color: #fff3a3; font-size: large">surligné</span>', propre)
        self.assertIn('<div style="margin-left: 80px">retrait</div>', propre)
        self.assertIn('<h2>Un</h2><h3>Deux</h3><hr>', propre)
        self.assertIn('<p style="text-align: left">g</p>', propre)

    def test_R2_surete_javascript_expression_url_evenements_style_balises_inconnues(self):
        from agent.webmail5628 import clean_html
        cas = [
            '<a href="javascript:alert(1)" style="color:#b42318">x</a>',
            '<p style="color: expression(alert(1))">x</p>',
            '<p style="background-color: url(https://ex.test/p.png)">x</p>',
            '<p style="background-color:#fff3a3;background-image:url(https://ex.test/t.png)">x</p>',
            '<p style="color:#b42318 /* */; behavior:url(x.htc)">x</p>',
            '<p onmouseover="alert(1)" style="font-size: 900px; margin-left: 13px; text-align: justify">x</p>',
            '<style>p{color:red}</style><p>x</p>', '<marquee>x</marquee><blink>y</blink>', '<svg onload=alert(1)><p>z</p></svg>',
            '<p style="color:#ff0000">hors palette</p>', '<span style="color:\\62 lue">échappement</span>', '<p style="position:fixed;top:0">x</p>',
        ]
        for sale in cas:
            propre = clean_html(sale)
            for motif in ('javascript', 'expression', 'url(', 'onmouseover', 'onload', 'behavior', '<style', 'marquee', 'blink', '<svg', 'red',
                          '#ff0000', '900px', '13px', 'justify', 'position', 'background-image', '\\'):
                self.assertNotIn(motif, propre.lower(), (sale, propre))
        self.assertEqual(clean_html('<p style="color:#ff0000">hors palette</p>'), '<p>hors palette</p>')

    def test_R2_mutation_garde_retire_laisserait_passer(self):
        from agent import webmail5628 as w
        sale = '<p style="color: expression(alert(1))">x</p>'
        self.assertNotIn('expression', w.clean_html(sale))
        with patch.dict(w.PROPRIETES, {'color': lambda v: v}):        # garde de la palette retiré (en mémoire seulement)
            self.assertIn('expression(alert(1))', w.clean_html(sale))
        with patch.object(w, 'STYLED', w.STYLED | {'a'}), patch.dict(w.PROPRIETES, {'background-color': lambda v: v}):
            self.assertIn('url(', w.clean_html('<a href="https://ex.test" style="background-color:url(https://ex.test/x)">x</a>'))
        self.assertNotIn('url(', w.clean_html('<a href="https://ex.test" style="background-color:url(https://ex.test/x)">x</a>'))

    # ------------------------------------------------------------------------------------------- date du jour (R2)
    def test_R2_date_du_jour_du_cabinet(self):
        from agent import webmail5628 as w
        with patch('agent.acces5683.today_of', return_value=(date(2026, 12, 1), 'Europe/Paris')):
            self.assertEqual(w.jour_du_cabinet(self.desk), '1er décembre 2026')
        with patch('agent.acces5683.today_of', return_value=(date(2026, 11, 16), 'Europe/Paris')):
            self.assertEqual(w.template(self.desk, 'nouveau')['jour'], '16 novembre 2026')
            self.assertEqual(w.template(self.desk, 'repondre', '1')['jour'], '16 novembre 2026')

    # ------------------------------------------------------------------------------------------- pièces jointes (R4)
    def test_R4_pieces_rendues_puis_reprises_par_empreinte_sans_doublon(self):
        from agent import webmail5628 as w
        note = b'Note de DURAND sur DOS-001'
        premier = w.save(self.desk, {'mode': 'transferer', 'source_uid': '2', 'to': 'martin@example.test', 'subject': 'TR : Pièces', 'html': '<p>Voir</p>',
                                     'keep': ['0'], 'files': [{'name': 'note.txt', 'type': 'text/plain', 'data': b64(note), 'cle': 'p1abc'}]})
        pieces = {p['name']: p for p in premier['pieces']}
        self.assertEqual(sorted(pieces), ['bordereau.pdf', 'note.txt'])
        self.assertEqual(pieces['note.txt']['origines'], [{'type': 'local', 'cle': 'p1abc'}])
        self.assertEqual(pieces['bordereau.pdf']['origines'], [{'type': 'source', 'index': 0}])
        self.assertEqual(pieces['note.txt']['empreinte'], w.empreinte(note))
        # deuxième enregistrement : pièces reprises du brouillon par empreinte ; le fichier local renvoyé (chemin de l'envoi) n'est pas doublé
        second = w.save(self.desk, {'mode': 'transferer', 'source_uid': '2', 'to': 'martin@example.test', 'subject': 'TR : Pièces', 'html': '<p>Voir</p>',
                                    'draft_uid': premier['uid'], 'draft_keep': [pieces['note.txt']['empreinte'], pieces['bordereau.pdf']['empreinte']],
                                    'keep': ['0'], 'files': [{'name': 'note.txt', 'type': 'text/plain', 'data': b64(note), 'cle': 'p1abc'}]})
        msg = lire(second['uid'])
        self.assertEqual(sorted(p.get_filename() for p in msg.iter_attachments()), ['bordereau.pdf', 'note.txt'])
        self.assertNotIn(int(premier['uid']), WebBox.store['Drafts'])
        # une pièce retirée (« ✕ ») n'est plus dans draft_keep : elle disparaît du brouillon suivant
        troisieme = w.save(self.desk, {'mode': 'nouveau', 'to': 'martin@example.test', 'subject': 'TR : Pièces', 'html': '<p>Voir</p>',
                                       'draft_uid': second['uid'], 'draft_keep': [pieces['bordereau.pdf']['empreinte']]})
        self.assertEqual([p.get_filename() for p in lire(troisieme['uid']).iter_attachments()], ['bordereau.pdf'])

    def test_R4_empreintes_invalides_refusees_et_limites_inchangees(self):
        from agent import webmail5628 as w
        with self.assertRaisesRegex(Stop, 'pieces_du_brouillon_invalides'):
            w.build(self.desk, {'mode': 'nouveau', 'to': 'a@example.test', 'html': 'x', 'draft_uid': '1', 'draft_keep': ['../../etc']})
        with self.assertRaisesRegex(Stop, 'dix_pieces_jointes_maximum'):
            w.build(self.desk, {'mode': 'nouveau', 'to': 'a@example.test', 'html': 'x',
                                'files': [{'name': 'f%d.txt' % i, 'data': b64(b'x%d' % i)} for i in range(11)]})
        with self.assertRaisesRegex(Stop, 'pieces_jointes_trop_lourdes'):
            w.build(self.desk, {'mode': 'nouveau', 'to': 'a@example.test', 'html': 'x',
                                'files': [{'name': 'a.bin', 'data': b64(b'a' * (8 * 1024 * 1024))}, {'name': 'b.bin', 'data': b64(b'b' * (8 * 1024 * 1024))}]})
        self.assertEqual((w.MAX_FILES, w.MAX_UPLOAD), (10, 15 * 1024 * 1024))

    def test_R4_corps_borne_des_brouillons_au_dela_de_la_borne_commune(self):
        from agent import webmail5628 as w
        from agent.web440 import MAX_JSON
        auth = {'origin': 'https://cabinet.example.test', 'csrf': 'jeton-essai'}
        gros = json.dumps({'mode': 'nouveau', 'files': [{'name': 'a.bin', 'data': 'A' * (MAX_JSON + 1000)}]}).encode()
        env = lambda corps, **k: {'HTTP_ORIGIN': k.get('origin', auth['origin']), 'HTTP_X_CSRF_TOKEN': k.get('csrf', auth['csrf']),   # noqa: E731
                                  'CONTENT_LENGTH': str(k.get('longueur', len(corps))), 'CONTENT_TYPE': 'application/json', 'wsgi.input': io.BytesIO(corps)}
        self.assertEqual(w._corps(env(gros), auth)['mode'], 'nouveau')
        with self.assertRaisesRegex(Stop, 'origine_ou_csrf_refuse'):
            w._corps(env(gros, csrf='autre'), auth)
        with self.assertRaisesRegex(Stop, 'origine_ou_csrf_refuse'):
            w._corps(env(gros, origin='https://autre.example.test'), auth)
        with self.assertRaisesRegex(Stop, 'requete_trop_longue'):
            w._corps(env(b'{}', longueur=w.MAX_CORPS + 1), auth)
        with self.assertRaisesRegex(Stop, 'objet_json_requis'):
            w._corps(env(b'[1]'), auth)

    # ------------------------------------------------------------------------------------------- brouillons (R5)
    def _brouillon_etranger(self):
        m = EmailMessage()
        m['From'], m['To'], m['Cc'], m['Bcc'] = 'cabinet@example.test', 'Client Durand <durand@example.test>', 'zorval@example.test', 'archive@example.test'
        m['Subject'], m['Message-ID'], m['In-Reply-To'], m['References'] = 'Re : Assemblée générale', '<d9@x>', '<a1@x>', '<a0@x> <a1@x>'
        m['X-AxiorHub-Origine'] = 'agent440'
        m.set_content('Bonjour,\nProjet de réponse.')
        m.add_alternative('<html><body><div style="font-family:Arial"><p style="color:#1f4e9c">Bonjour,</p><script>x</script><p>Projet</p></div></body></html>', subtype='html')
        m.add_attachment(b'%PDF-1.4 convention', maintype='application', subtype='pdf', filename='convention.pdf')
        WebBox.store['Drafts'][40] = ({'\\Draft'}, m.as_bytes(policy=policy.SMTP))
        return '40'

    def test_R5_modifier_rouvre_le_brouillon_puis_le_remplace(self):
        from agent import webmail5628 as w
        uid = self._brouillon_etranger()
        t = w.template(self.desk, 'brouillon', uid, 'INBOX')            # dossier demandé ignoré : toujours le dossier des brouillons
        self.assertEqual((t['mode'], t['draft_uid'], t['reprise_uid']), ('reprise', uid, uid))
        self.assertEqual((t['to'], t['cc'], t['bcc'], t['subject']), ('Client Durand <durand@example.test>', 'zorval@example.test', 'archive@example.test',
                                                                        'Re : Assemblée générale'))
        self.assertIn('<p style="color: #1f4e9c">Bonjour,</p>', t['html'])
        self.assertNotIn('script', t['html'])
        self.assertEqual([(p['name'], p['empreinte']) for p in t['pieces_brouillon']], [('convention.pdf', w.empreinte(b'%PDF-1.4 convention'))])
        out = w.save(self.desk, {'mode': 'reprise', 'to': t['to'], 'cc': t['cc'], 'bcc': t['bcc'], 'subject': t['subject'], 'html': t['html'] + '<p>Ajout</p>',
                                 'draft_uid': t['draft_uid'], 'reprise_uid': t['reprise_uid'], 'draft_keep': [t['pieces_brouillon'][0]['empreinte']]})
        self.assertNotIn(40, WebBox.store['Drafts'])                     # le brouillon rouvert est remplacé, même d'une autre origine
        msg = lire(out['uid'])
        self.assertEqual((msg['In-Reply-To'], msg['References']), ('<a1@x>', '<a0@x> <a1@x>'))   # fil de la conversation gardé
        self.assertEqual([p.get_filename() for p in msg.iter_attachments()], ['convention.pdf'])
        self.assertIn('Ajout', msg.get_body(preferencelist=('html',)).get_content())
        # enregistrement suivant (enregistrement automatique) : le nouveau brouillon remplace le précédent, fil toujours gardé
        encore = w.save(self.desk, {'mode': 'reprise', 'to': t['to'], 'subject': t['subject'], 'html': '<p>v3</p>', 'draft_uid': out['uid'],
                                    'reprise_uid': t['reprise_uid'], 'draft_keep': [p['empreinte'] for p in out['pieces']]})
        self.assertNotIn(int(out['uid']), WebBox.store['Drafts'])
        self.assertEqual(lire(encore['uid'])['In-Reply-To'], '<a1@x>')

    def test_R5_mutation_brouillon_etranger_non_designe_jamais_supprime(self):
        from agent import webmail5628 as w
        uid = self._brouillon_etranger()
        w.save(self.desk, {'mode': 'nouveau', 'to': 'a@example.test', 'subject': 'x', 'html': '<p>x</p>', 'draft_uid': uid})
        self.assertIn(40, WebBox.store['Drafts'])                        # sans « Modifier » (reprise_uid), un brouillon de l'agent reste
        w.save(self.desk, {'mode': 'nouveau', 'to': 'a@example.test', 'subject': 'x', 'html': '<p>x</p>', 'draft_uid': uid, 'reprise_uid': '41'})
        self.assertIn(40, WebBox.store['Drafts'])                        # reprise d'un autre brouillon : rien n'est retiré

    def test_R5_brouillon_absent_refuse(self):
        from agent import webmail5628 as w
        with self.assertRaises(Stop):
            w.template(self.desk, 'brouillon', '99')
        with self.assertRaises(Stop):
            w.template(self.desk, 'brouillon', 'abc')

    # ------------------------------------------------------------------------------------------- images collées (R7)
    PNG = b'\x89PNG\r\n\x1a\n' + b'\x00' * 24 + b'image DURAND'
    JPEG = b'\xff\xd8\xff\xe0' + b'\x00' * 24 + b'image MARTIN'

    def _img(self, raw, kind='png', extra=''):
        return '<img src="data:image/%s;base64,%s"%s>' % (kind, b64(raw), extra)

    def test_R7_image_collee_convertie_en_piece_integree_cid(self):
        from agent import webmail5628 as w
        html = '<p>Plan :</p>' + self._img(self.PNG, extra=' alt="plan" width="400" onerror="x()" style="border:1px"') + self._img(self.PNG) + self._img(self.JPEG, 'jpeg')
        out = w.save(self.desk, {'mode': 'nouveau', 'to': 'durand@example.test', 'subject': 'Plan', 'html': html,
                                 'files': [{'name': 'note.txt', 'data': b64(b'note'), 'cle': 'p1'}]})
        msg = lire(out['uid'])
        corps = msg.get_body(preferencelist=('html',)).get_content()
        self.assertNotIn('data:', corps)
        self.assertNotIn('onerror', corps)
        cids = re.findall(r'src="cid:([^"]+)"', corps)
        self.assertEqual(len(cids), 3)
        self.assertEqual(cids[0], cids[1])                                     # même image : une seule pièce intégrée
        self.assertIn('alt="plan" width="400"', corps)
        liees = {str(p['Content-ID']).strip('<>'): p for p in msg.walk() if p.get('Content-ID')}
        self.assertEqual(sorted(liees), sorted(set(cids)))
        self.assertEqual(liees[cids[0]].get_payload(decode=True), self.PNG)
        self.assertEqual(liees[cids[2]].get_content_type(), 'image/jpeg')
        self.assertEqual([p.get_filename() for p in msg.iter_attachments()], ['note.txt'])   # les images ne sont pas des pièces jointes
        self.assertEqual([x['name'] for x in out['pieces']], ['note.txt'])
        types = [p.get_content_type() for p in msg.walk()]
        self.assertIn('multipart/related', types)
        # reprise : les images sont réaffichées (data:), puis de nouveau intégrées à l'enregistrement suivant
        t = w.template(self.desk, 'brouillon', out['uid'])
        self.assertIn('src="data:image/png;base64,%s"' % b64(self.PNG), t['html'])
        self.assertNotIn('cid:', t['html'])
        encore = w.save(self.desk, {'mode': 'reprise', 'to': t['to'], 'subject': t['subject'], 'html': t['html'], 'draft_uid': t['draft_uid'],
                                    'reprise_uid': t['reprise_uid'], 'draft_keep': [x['empreinte'] for x in t['pieces_brouillon']]})
        corps2 = lire(encore['uid']).get_body(preferencelist=('html',)).get_content()
        self.assertEqual(len(re.findall(r'src="cid:', corps2)), 3)
        self.assertNotIn('data:', corps2)

    def test_R7_refus_svg_distante_data_html_et_bornes(self):
        from agent import webmail5628 as w
        sale = ('<img src="data:image/svg+xml;base64,%s"><img src="https://tiers.example.test/suivi.png"><img src="//tiers.example.test/x.png">'
                '<img src="data:text/html;base64,PHNjcmlwdD4="><img src="javascript:alert(1)"><img src="cid:inconnu@x">') % b64(b'<svg onload="x"/>')
        propre = w.clean_html(sale, images=True)
        for motif in ('svg', 'tiers.example.test', 'text/html', 'javascript'):
            self.assertNotIn(motif, propre)
        msg, _ = w.build(self.desk, {'mode': 'nouveau', 'to': 'a@example.test', 'html': '<p>x</p>' + sale})
        corps = msg.get_body(preferencelist=('html',)).get_content()
        self.assertNotIn('<img', corps)                                        # cid inconnu retiré aussi : aucune référence vers une partie absente
        with self.assertRaisesRegex(Stop, 'image_refusee'):                    # type déclaré PNG, octets SVG : refusé (signature des octets)
            w.build(self.desk, {'mode': 'nouveau', 'to': 'a@example.test', 'html': self._img(b'<svg xmlns="http://www.w3.org/2000/svg"/>')})
        with self.assertRaisesRegex(Stop, 'image_trop_lourde'):
            w.build(self.desk, {'mode': 'nouveau', 'to': 'a@example.test', 'html': self._img(self.PNG + b'x' * (2 * 1024 * 1024))})
        with self.assertRaisesRegex(Stop, 'dix_images_maximum'):
            w.build(self.desk, {'mode': 'nouveau', 'to': 'a@example.test', 'html': ''.join(self._img(self.PNG + bytes([i])) for i in range(11))})
        self.assertEqual(w.clean_html(self._img(self.PNG)), '')                # sans images=True (aperçus, signatures…) : aucune image
        self.assertNotIn('width', w.clean_html(self._img(self.PNG, extra=' width="999"'), images=True))

    def test_R7_mutation_controle_du_src(self):
        from agent import webmail5628 as w
        distante = '<img src="https://tiers.example.test/suivi.png">'
        self.assertEqual(w.clean_html(distante, images=True), '')
        with patch.object(w, 'IMG_SRC', re.compile(r'.+')):                  # contrôle du src retiré (en mémoire seulement)
            self.assertIn('tiers.example.test', w.clean_html(distante, images=True))
        with patch.object(w, 'type_image', lambda raw: 'png'):               # contrôle par la signature des octets retiré
            msg, _ = w.build(self.desk, {'mode': 'nouveau', 'to': 'a@example.test', 'html': self._img(b'<svg/>')})
            self.assertTrue(any(p.get_payload(decode=True) == b'<svg/>' for p in msg.walk() if p.get('Content-ID')))

    def test_R7_point_de_montage_envoi_joindre_html(self):
        from agent import webmail5628 as w
        msg, _ = w.build(self.desk, {'mode': 'nouveau', 'to': 'a@example.test', 'html': '<p>Voir</p>' + self._img(self.PNG)})
        out = EmailMessage(policy=policy.SMTP)
        out.set_content('Voir')
        self.assertTrue(w.joindre_html(out, msg))
        corps = out.get_body(preferencelist=('html',)).get_content()
        cid = re.findall(r'src="cid:([^"]+)"', corps)[0]
        self.assertEqual([p.get_payload(decode=True) for p in out.walk() if str(p.get('Content-ID', '')).strip('<>') == cid], [self.PNG])
        self.assertNotIn('data:', corps)

    # ------------------------------------------------------------------------------------------- navigateur (R1 à R6, compacité)
    def test_R1_R6_scripts_routes_et_assets(self):
        page = self.request('/courriels', query='vue=boite')['body']
        self.assertIn('/static/v56106-redaction.js', page)
        self.assertLess(page.index('/static/v5628.js'), page.index('/static/v56106-redaction.js'))
        from agent import web440, shell501
        self.assertIn('/static/v56106-redaction.js', web440.STATIC)
        self.assertIn('/static/v56106-redaction.css', web440.STATIC)
        self.assertIn('v56106-redaction.css', shell501.BUNDLE)
        self.assertIn('.bx56106r-barre', (STATIC / 'app520.css').read_text(encoding='utf-8'))
        js = (STATIC / 'v56106-redaction.js').read_text(encoding='utf-8')
        for crochet in ("window.axiorhubDictee = { attacher", "axiorhubMic.stream('dictee'", "'/dictation'", "execCommand('insertText'", '☐', '☑',
                        "'formatBlock', 'h2'", "'formatBlock', 'h3'", "aligner(editor, 'center')", 'insertHorizontalRule', "'background-color'",
                        "appliquerStyle(editor, 'color'", "'undo'", "'redo'", 'collerHTML', 'Image distante non insérée',
                        'm5636/contacts?q=', 'j.texte.length < 2', 'ArrowDown', 'Escape', "'drop'", 'MAX_PIECES = 10', '15 * 1024 * 1024',
                        'PERIODE = 30000', 'Enregistré à ', 'm56106/redaction/modeles', 'Confidentiel — vocal local', 'window.confirm(\'Mode Rapide'):
            self.assertIn(crochet, js)
        self.assertNotIn('innerHTML = m.html', js)
        self.assertNotIn("'styleWithCSS', false, true", js)                # politique de sécurité : aucun attribut style écrit par les commandes
        self.assertNotIn("setAttribute('style'", js)
        self.assertNotIn("execCommand('insertImage'", js)
        self.assertNotIn("style: '", js)                       # un modèle passe par la commande d'édition, jamais par innerHTML
        v5628 = (STATIC / 'v5628.js').read_text(encoding='utf-8')
        for crochet in ('bx5628-options-envoi', "'data-lot': 'E'", "compose('brouillon', m, c)", 'reprise_uid', 'window.axh56106r', 'Cc / Cci',
                        "window.axh5628 = { toolbar: toolbar, compose: compose }"):
            self.assertIn(crochet, v5628)

    def test_R7_html_colle_assaini_hors_de_la_page(self):
        """Aucun attribut d'événement ne doit atteindre le document vivant : tout HTML collé ou déposé est intercepté (capture), ses attributs
        on… et style retirés du texte avant l'analyse, puis analysé dans un document inerte (DOMParser) avant insertion."""
        js = (STATIC / 'v56106-redaction.js').read_text(encoding='utf-8')
        corps = js[js.index('function assainir(html)'):js.index('function collerHTML(editor, html)')]
        avant, apres = corps.index("replace(ATTR_INTERDIT, '')"), corps.index('new DOMParser().parseFromString(source')
        self.assertLess(avant, apres)                                          # retrait textuel AVANT toute analyse
        self.assertIn("/^on/i.test(a.name)", corps)                            # puis retrait dans le document inerte
        motif = re.search(r"var ATTR_INTERDIT = /(.+)/gi;", js).group(1).replace('\\\\', '\\')
        rx = re.compile(motif, re.I)
        for sale in ('<img src="https://t.example.test/x.png" onerror="x()">', "<p onclick='a()'>x</p>", '<b ONMOUSEOVER=y()>x</b>',
                     '<svg onload="w()"></svg>', '<p style="color:red" onfocus="f()">x</p>'):
            self.assertNotRegex(rx.sub('', sale), r'(?i)\son[a-z]+\s*=|style\s*=')
        paste = js[js.index("editor.addEventListener('paste'"):js.index("editor.addEventListener('drop', function (ev)")]
        self.assertIn("if (html) { ev.preventDefault(); ev.stopImmediatePropagation();", paste)   # tout HTML intercepté, pas seulement les images
        self.assertIn('}, true);', paste)                                      # phase de capture
        self.assertNotIn('innerHTML = html', js)
        self.assertNotIn("execCommand('insertHTML'", js)

    def test_R1_v310_equipe_les_champs_crees_plus_tard_de_facon_bornee(self):
        js = (STATIC / 'v310.js').read_text(encoding='utf-8')
        self.assertIn('new MutationObserver', js)
        self.assertIn('dictationBudget=200', js)
        self.assertIn("[data-no-dictation]", js)
        self.assertIn('dataset.dictationReady', js)
        self.assertIn("observe(document.body,{childList:true,subtree:true})", js)
        self.assertIn('setTimeout(dictationScan,200)', js)

    def test_compacite_texte_14px_et_contraste_AA(self):
        css = re.sub(r'/\*.*?\*/', '', (STATIC / 'v56106-redaction.css').read_text(encoding='utf-8'), flags=re.S)
        tailles = [int(x) for x in re.findall(r'font-size:(\d+)px', css)]
        self.assertTrue(tailles and min(tailles) >= 14, tailles)
        self.assertIn('position:sticky;bottom:0', css)                  # boutons toujours visibles
        self.assertIn('min-height:30px', css)                            # champs d'en-tête sur une ligne (≈ 30 px)
        self.assertIn('flex:1 1 auto', css)                              # l'éditeur prend la hauteur restante

        def lum(h):
            c = [int(h[i:i + 2], 16) / 255 for i in (1, 3, 5)]
            c = [x / 12.92 if x <= 0.03928 else ((x + 0.055) / 1.055) ** 2.4 for x in c]
            return 0.2126 * c[0] + 0.7152 * c[1] + 0.0722 * c[2]

        def ratio(a, b):
            la, lb = sorted((lum(a), lum(b)), reverse=True)
            return (la + 0.05) / (lb + 0.05)
        clair = [('#3d4757', '#ffffff'), ('#4a5566', '#f6f7f9'), ('#b42318', '#ffffff'), ('#1f2a3a', '#f4f7fd'), ('#ffffff', '#1f4e9c'),
                 ('#1f4e9c', '#ffffff'), ('#6b7480', '#ffffff'), ('#1f2a3a', '#fff3a3'), ('#1f2a3a', '#cfe3ff')]
        sombre = [('#c4cdd9', '#1a2029'), ('#b4bfcc', '#1a2029'), ('#ff9b8f', '#1a2029'), ('#e6ebf2', '#202833'), ('#ffffff', '#4a6390'), ('#8a94a3', '#1a2029')]
        for texte, fond in clair + sombre:
            self.assertGreaterEqual(ratio(texte, fond), 4.5, (texte, fond))
        for couleur in ('#000000', '#5b6676', '#b42318', '#1f4e9c', '#1a7f37', '#b54708'):   # palette du texte lisible sur fond blanc
            self.assertGreaterEqual(ratio(couleur, '#ffffff'), 4.5, couleur)


class Modeles(Base):
    AUTH = {'origin': 'https://cabinet.example.test', 'csrf': 'jeton-essai'}

    def appel(self, route, corps=None, qui=('maitre-durand', 'avocat')):
        from agent import redaction56106 as r
        env = {}
        if corps is not None:
            raw = json.dumps(corps).encode()
            env = {'HTTP_ORIGIN': self.AUTH['origin'], 'HTTP_X_CSRF_TOKEN': self.AUTH['csrf'], 'CONTENT_LENGTH': str(len(raw)),
                   'CONTENT_TYPE': 'application/json', 'wsgi.input': io.BytesIO(raw)}
        with patch('agent.web567.actor', return_value=qui):
            out = r.api(env, self.desk, self.AUTH, '', 'm56106/redaction/' + route, {}, 'GET' if corps is None else 'POST')
        self.assertIn(('Cache-Control', 'private, no-store'), out['headers'])
        return out['status'], json.loads(out['body'])

    def test_R6_schema_cree_au_demarrage(self):
        noms = {r[0] for r in self.desk.db.execute("SELECT name FROM sqlite_master WHERE type='table'")}
        self.assertTrue({'modeles_reponse56106', 'redaction56106_recus'} <= noms)

    def test_R6_creer_lister_inserer_modifier_supprimer(self):
        st, out = self.appel('modeles/creer', {'titre': 'Bien reçu', 'html': '<p>Bien reçu, je reviens vers vous.</p><script>x</script>', 'cle': 'a' * 32})
        self.assertEqual(st, '200 OK')
        st, liste = self.appel('modeles')
        m = liste['modeles'][0]
        self.assertEqual((m['titre'], m['html'], m['modifiable'], m['version']), ('Bien reçu', '<p>Bien reçu, je reviens vers vous.</p>', True, 1))
        st, autre = self.appel('modeles', qui=('maitre-martin', 'avocat'))   # partagé au cabinet, mais non modifiable par un autre avocat
        self.assertFalse(autre['modeles'][0]['modifiable'])
        st, x = self.appel('modeles/modifier', {'id': m['id'], 'titre': 'Accusé de réception', 'html': '<p>Reçu.</p>', 'version': 1, 'cle': 'b' * 32})
        self.assertEqual((st, x['version']), ('200 OK', 2))
        st, x = self.appel('modeles/modifier', {'id': m['id'], 'titre': 'Périmé', 'html': '<p>x</p>', 'version': 1, 'cle': 'c' * 32})
        self.assertEqual((st, x['error']), ('409 Conflict', 'version_perimee'))
        st, x = self.appel('modeles/supprimer', {'id': m['id'], 'version': 2, 'cle': 'd' * 32})
        self.assertEqual(st, '200 OK')
        self.assertEqual(self.appel('modeles')[1]['modeles'], [])

    def test_R6_droits_auteur_administrateur_404_neutre_et_403(self):
        _, cree = self.appel('modeles/creer', {'titre': 'Relance', 'html': '<p>Je me permets de revenir vers vous.</p>', 'cle': 'e' * 32})
        corps = {'id': cree['id'], 'titre': 'Pris', 'html': '<p>x</p>', 'version': 1, 'cle': 'f' * 32}
        st, x = self.appel('modeles/modifier', corps, qui=('maitre-martin', 'avocat'))
        absent = self.appel('modeles/modifier', {**corps, 'id': '0' * 32, 'cle': 'g' * 32}, qui=('maitre-martin', 'avocat'))
        self.assertEqual((st, x), absent)                                   # objet interdit et objet absent : même 404 neutre
        self.assertEqual(st, '404 Not Found')
        st, _ = self.appel('modeles/supprimer', {'id': cree['id'], 'version': 1, 'cle': 'h' * 32}, qui=('maitre-martin', 'avocat'))
        self.assertEqual(st, '404 Not Found')
        st, x = self.appel('modeles/creer', {'titre': 'x', 'html': '<p>x</p>', 'cle': 'i' * 32}, qui=('assistant-kervilac', 'assistant'))
        self.assertEqual(st, '403 Forbidden')
        self.assertEqual(self.appel('modeles', qui=('assistant-kervilac', 'assistant'))[0], '403 Forbidden')
        st, x = self.appel('modeles/modifier', {**corps, 'cle': 'j' * 32}, qui=('admin-valbrune', 'administrateur'))
        self.assertEqual(st, '200 OK')                                     # un administrateur peut modifier le modèle d'un autre
        from agent.standalone_auth import allowed
        self.assertTrue(allowed('avocat', 'POST', '/api440/m56106/redaction/modeles/creer'))
        self.assertFalse(allowed('assistant', 'POST', '/api440/m56106/redaction/modeles/creer'))
        self.assertFalse(allowed('avocat', 'POST', '/api440/m56106/redaction/inconnue'))

    def test_R6_mutation_garde_auteur_retire_laisserait_modifier(self):
        from agent import redaction56106 as r
        _, cree = self.appel('modeles/creer', {'titre': 'Relance', 'html': '<p>Relance.</p>', 'cle': 'k' * 32})
        corps = {'id': cree['id'], 'titre': 'Détourné', 'html': '<p>x</p>', 'version': 1, 'cle': 'l' * 32}
        sans_garde = lambda desk, owner, role, ident: desk.db.execute('SELECT * FROM modeles_reponse56106 WHERE id=?', (ident,)).fetchone()   # noqa: E731
        with patch.object(r, '_autorise', sans_garde):
            self.assertEqual(self.appel('modeles/modifier', corps, qui=('maitre-martin', 'avocat'))[0], '200 OK')
        corps['cle'] = 'm' * 32
        self.assertEqual(self.appel('modeles/modifier', {**corps, 'version': 2}, qui=('maitre-martin', 'avocat'))[0], '404 Not Found')

    def test_R6_idempotence_et_lecture_sans_ecriture(self):
        corps = {'titre': 'Rendez-vous', 'html': '<p>Je vous propose un rendez-vous.</p>', 'cle': 'n' * 32}
        a = self.appel('modeles/creer', corps)
        b = self.appel('modeles/creer', corps)
        self.assertEqual(a, b)                                             # même clé, même demande : même réponse, un seul modèle
        self.assertEqual(self.desk.db.execute('SELECT COUNT(*) FROM modeles_reponse56106').fetchone()[0], 1)
        st, x = self.appel('modeles/creer', {**corps, 'titre': 'Autre'})
        self.assertEqual((st, x['error']), ('409 Conflict', 'cle_idempotence_deja_utilisee'))
        st, x = self.appel('modeles/creer', {**corps, 'cle': ''})
        self.assertEqual(x['error'], 'cle_idempotence_requise')
        avant = self.desk.db.total_changes
        self.appel('modeles')
        self.assertEqual(self.desk.db.total_changes, avant)                # R05 : une lecture n'écrit rien
        st, x = self.appel('modeles/creer', {'titre': ' ', 'html': '<p>x</p>', 'cle': 'o' * 32})
        self.assertEqual(x['error'], 'titre_requis')
        st, x = self.appel('modeles/creer', {'titre': 'Vide', 'html': '<script>x</script>', 'cle': 'p' * 32})
        self.assertEqual(x['error'], 'modele_vide')


if __name__ == '__main__':
    unittest.main()
