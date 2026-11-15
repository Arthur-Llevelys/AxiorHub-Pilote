"""5.6.102 (lot S) : signature des réponses préparées — « celle de la messagerie » (Gmail, sinon webmail d'AxiorHub), sinon Paramètres, sinon
identité du cabinet ; une seule fois ; HTML nettoyé ; contrôle des faits ; volet de relecture ; « Modifier » garde la version HTML.

Données fictives (DURAND, MARTIN, ZORVAL, KERVILAC, Valbrune, example.test, 01 00 00 00 05). Faux Google au niveau du transport
(``test_v5697_gmail.FauxGoogle``), faux IMAP (``fake_imap440``), aucun modèle, aucun réseau. La lecture réelle de ``sendAs`` avec la portée
``gmail.readonly`` n'est PAS prouvée ici : seul le comportement en cas de réponse (lue ou refusée) est éprouvé."""
import json
import re
import tempfile
import unittest
import uuid
from datetime import datetime, timezone
from email import policy
from email.parser import BytesParser
from pathlib import Path
from unittest.mock import patch

from agent import drafts440, gmail5697 as gm, google5697 as gg, signature56102 as sg, volet56101 as vo
from agent.desk import Desk
from agent.mailbox import Mail, make_draft
from fake_imap440 import FakeIMAP
import test_agent as fixtures
import test_v5697_gmail as tg
from test_v311 import StoredMailbox
from test_v56101_relecture import MID, TITULAIRE
from test_v56101_volet import Volet

SIG_WEBMAIL = '<p><b>Maître ZORVAL</b><br>Avocat au Barreau de Valbrune<br>01 00 00 00 05</p>'
SIG_GMAIL = '<div dir="ltr"><b>Maître KERVILAC</b><br>Cabinet KERVILAC, Valbrune</div>'
MALVEILLANT = ('<img src="https://evil.example.test/p.png" onerror="alert(1)"><script>alert(1)</script><b onclick="x()">Maître KERVILAC</b>'
               '<a href="javascript:alert(1)">lien</a><iframe src="https://evil.example.test/"></iframe><style>body{display:none}</style>')
INTERDITS = ('<script', 'onerror', 'onclick', '<img', 'evil.example.test', 'javascript:', '<iframe', '<style', 'alert(')


def maintenant():
    return datetime.now(timezone.utc).isoformat(timespec='seconds')


def cache_gmail(desk, conn, owner, envoi, html, etat='lue'):
    """Ce que la synchronisation Gmail écrit (réglage ``signature56102:gmail``)."""
    cache = desk.settings(sg.CLE_GMAIL, {}) or {}
    cache[conn] = {'owner': owner, 'adresse': envoi, 'envoi': envoi, 'html': html, 'etat': etat, 'code': '', 'maj': maintenant(), 'essai': 0}
    desk.setting(sg.CLE_GMAIL, cache)


def lire(msg):
    return BytesParser(policy=policy.default).parsebytes(msg.as_bytes(policy=policy.SMTP) if hasattr(msg, 'as_bytes') else msg)


def parties(parsed):
    texte = parsed.get_body(preferencelist=('plain',))
    html = parsed.get_body(preferencelist=('html',))
    return (texte.get_content() if texte is not None else ''), (html.get_content() if html is not None else '')


def sig_webmail():
    return sg._resultat('webmail', sg._texte_de(SIG_WEBMAIL), sg._nettoyer(SIG_WEBMAIL), True, 'font-family:Arial, Helvetica, sans-serif;font-size:11pt')


class Bureau(unittest.TestCase):
    def setUp(self):
        self.f = fixtures.EngineTests(methodName='test_observation_has_no_mail_write')
        self.f.setUp()
        self.addCleanup(self.f.doCleanups)
        self.desk = Desk(self.f.c)
        self.addCleanup(self.desk.db.close)


# ------------------------------------------------------------------------------------------------ S01 : priorité
class S01Priorite(Bureau):
    def test_S01_gmail_puis_webmail_puis_parametres_puis_cabinet_puis_aucune(self):
        gm.regler(self.desk, 'administrateur', True)
        cache_gmail(self.desk, 'cx1', 'claire@example.test', 'cabinet@example.test', SIG_GMAIL)
        self.desk.setting('courrier5632:prefs', {'signature_html': SIG_WEBMAIL})
        s = sg.effective(self.desk, 'claire@example.test')
        self.assertEqual((s['source'], s['html_propre']), ('gmail', True))
        self.assertIn('KERVILAC', s['texte'])                       # texte dérivé du HTML
        self.assertEqual(sg.effective(self.desk, None, 'gmail:cx1')['source'], 'gmail')
        gm.regler(self.desk, 'administrateur', False)               # fournisseur Gmail désactivé : son cache n'est plus repris
        self.assertEqual(sg.effective(self.desk, 'claire@example.test')['source'], 'webmail')
        gm.regler(self.desk, 'administrateur', True)
        self.desk.setting(sg.CLE_GMAIL, {})
        s = sg.effective(self.desk, 'claire@example.test')
        self.assertEqual(s['source'], 'webmail')
        self.assertIn('ZORVAL', s['texte'])
        self.desk.setting('courrier5632:prefs', {'signature_html': ''})   # signature du webmail effacée par la personne
        s = sg.effective(self.desk, 'claire@example.test')
        self.assertEqual((s['source'], s['texte'], s['html_propre']), ('parametres', 'Maître Exemple', False))
        self.desk.c['mail']['signature'] = ''
        self.desk.setting('pieces510:avocat', {'prenom_avocat': 'Claire', 'nom_avocat': 'DURAND', 'ville_avocat': 'Valbrune'})
        s = sg.effective(self.desk, 'claire@example.test')
        self.assertEqual(s['source'], 'cabinet')
        self.assertIn('Claire DURAND', s['texte'])
        self.desk.setting('pieces510:avocat', {})
        self.assertEqual(sg.effective(self.desk, 'claire@example.test')['source'], 'aucune')

    def test_S01_brouillon_imap_ne_prend_la_signature_gmail_que_pour_la_meme_adresse(self):
        gm.regler(self.desk, 'administrateur', True)
        cache_gmail(self.desk, 'cx1', 'claire@example.test', 'claire.gmail@example.test', SIG_GMAIL)
        self.assertEqual(sg.effective(self.desk, 'claire@example.test')['source'], 'parametres')   # autre adresse que celle de la messagerie
        self.assertEqual(sg.effective(self.desk, 'claire@example.test', 'gmail:cx1')['source'], 'gmail')
        cache_gmail(self.desk, 'cx2', 'camille@example.test', 'cabinet@example.test', SIG_GMAIL.replace('KERVILAC', 'ORLANDEC'))
        cache_gmail(self.desk, 'cx3', 'claire@example.test', 'cabinet@example.test', SIG_GMAIL)
        self.assertIn('KERVILAC', sg.effective(self.desk, 'claire@example.test')['texte'])   # celle de la personne d'abord
        self.assertIn('ORLANDEC', sg.effective(self.desk, 'camille@example.test')['texte'])

    def test_S01_parametres_indique_la_source_et_le_lien_du_webmail(self):
        from agent import web5632
        self.desk.setting('courrier5632:prefs', {'signature_html': SIG_WEBMAIL})
        page = web5632.courrier_section(self.desk, '/p')
        self.assertIn('Signature ajoutée aux réponses préparées', page)
        self.assertIn('data-s56102-source="webmail"', page)
        self.assertIn('href="/p/parametres?rubrique=messagerie#signature-webmail"', page)
        self.assertIn('id="signature-webmail"', page)


class S01Synchronisation(tg.Base):
    def sendas(self, signature=SIG_GMAIL, statut=200, fois=1):
        corps = json.dumps({'sendAs': [{'sendAsEmail': 'autre@example.test', 'isDefault': False, 'signature': '<b>Autre</b>'},
                                       {'sendAsEmail': tg.GMAIL_CLAIRE, 'isDefault': True, 'isPrimary': True, 'signature': signature}]}).encode()
        self.google.panne('GET', '/settings/sendAs', statut=statut, corps=corps if statut == 200 else b'{"error":{"code":403}}', fois=fois)

    def test_S01_synchronisation_met_la_signature_gmail_en_cache_au_plus_toutes_les_six_heures(self):
        self.connecter()
        self.sendas()
        self.assertEqual(self.synchro()['etat'], 'a_jour')
        cid = self.conn()['id']
        s = sg.effective(self.desk, tg.CLAIRE, 'gmail:' + cid)
        self.assertEqual(s['source'], 'gmail')
        self.assertIn('KERVILAC', s['texte'])
        self.assertNotIn('Autre', s['texte'])                         # le compte d'envoi, pas un autre alias
        n = len([a for a in self.google.appels if '/settings/sendAs' in a[1]])
        self.synchro()
        self.assertEqual(len([a for a in self.google.appels if '/settings/sendAs' in a[1]]), n)   # pas de nouvelle lecture avant six heures
        gm.deconnecter(self.desk, tg.CLAIRE)
        self.assertEqual(sg.effective(self.desk, tg.CLAIRE, 'gmail:' + cid)['source'], 'parametres')   # oubliée avec la connexion


# ------------------------------------------------------------------------------------------------ S02 : Gmail refuse
class S02Refus(tg.Base):
    def test_S02_403_repli_webmail_et_etat_signature_gmail_non_lisible(self):
        self.connecter()
        self.desk.setting('courrier5632:prefs', {'signature_html': SIG_WEBMAIL})
        self.google.panne('GET', '/settings/sendAs', statut=403, corps=b'{"error":{"code":403,"status":"PERMISSION_DENIED"}}')
        self.assertEqual(self.synchro()['etat'], 'a_jour')            # la relève des courriels n'en dépend pas
        cid = self.conn()['id']
        s = sg.effective(self.desk, tg.CLAIRE, 'gmail:' + cid)
        self.assertEqual(s['source'], 'webmail')
        self.assertIn('ZORVAL', s['texte'])
        self.assertIn('Signature Gmail non lisible', s['note'])
        self.assertIn('Signature Gmail non lisible', sg.etat(self.desk, tg.CLAIRE, 'gmail:' + cid)['gmail'])
        self.assertNotIn('gmail.settings', ' '.join(gg.PORTEES.values()))   # aucune portée ajoutée

    def test_S02_panne_reseau_garde_la_signature_deja_lue(self):
        self.connecter()
        self.google.panne('GET', '/settings/sendAs', statut=200, corps=json.dumps({'sendAs': [{'sendAsEmail': tg.GMAIL_CLAIRE, 'isDefault': True,
                                                                                                'signature': SIG_GMAIL}]}).encode())
        self.synchro()
        self.google.panne('GET', '/settings/sendAs', statut=None)    # délai dépassé
        sg.rafraichir_gmail(self.desk, self.conn(), forcer=True)
        self.assertEqual(sg.effective(self.desk, tg.CLAIRE, 'gmail:' + self.conn()['id'])['source'], 'gmail')


# ------------------------------------------------------------------------------------------------ S03 : make_draft
class S03Brouillon(Bureau):
    def depot(self, draft):
        box = StoredMailbox()
        box.saved.append(Mail('1', '23', 'INBOX.Drafts', {'\\Draft'}, datetime.now(timezone.utc), lire(draft)))
        return box.verify_draft(draft)

    def test_S03_texte_et_html_signature_une_seule_fois_verify_draft_reussit(self):
        sig = sig_webmail()
        d = make_draft(fixtures.mail(), self.f.c['mail'], 'Bonjour,\n\nBien reçu.', 'a' * 64, signature=sig)
        p = lire(d)
        self.assertEqual(p.get_content_type(), 'multipart/alternative')
        texte, html = parties(p)
        self.assertEqual(texte.count('ZORVAL'), 1)
        self.assertEqual(html.count('ZORVAL'), 1)
        self.assertIn('<b>Maître ZORVAL</b>', html)
        self.assertEqual(self.depot(d)['uid'], '1')
        deja = make_draft(fixtures.mail(), self.f.c['mail'], 'Bonjour,\n\nBien reçu.\n\n' + sig['texte'], 'b' * 64, signature=sig)
        texte, html = parties(lire(deja))
        self.assertEqual((texte.count('ZORVAL'), html.count('ZORVAL')), (1, 1))   # corps déjà signé : pas de seconde signature
        self.assertEqual(self.depot(deja)['uid'], '1')

    def test_S03_sans_signature_ni_piece_le_message_reste_texte_comme_avant(self):
        d = make_draft(fixtures.mail(), self.f.c['mail'], 'Bonjour', 'c' * 64)
        p = lire(d)
        self.assertEqual(p.get_content_type(), 'text/plain')
        self.assertEqual(p.get_content().count('Maître Exemple'), 1)
        p = lire(make_draft(fixtures.mail(), self.f.c['mail'], 'Bonjour\n\nMaître Exemple', 'd' * 64))
        self.assertEqual(p.get_content().count('Maître Exemple'), 1)   # signature de Paramètres déjà présente : jamais répétée

    def test_S03_pieces_jointes_facultatives_pour_le_lot_K(self):
        d = make_draft(fixtures.mail(), self.f.c['mail'], 'Bonjour', 'e' * 64, signature=sig_webmail(),
                       attachments=[('Contrat DURAND.pdf', b'%PDF-1.4 fictif', 'application/pdf')])
        p = lire(d)
        self.assertEqual(p.get_content_type(), 'multipart/mixed')
        self.assertEqual([a.get_filename() for a in p.iter_attachments()], ['Contrat DURAND.pdf'])
        self.assertEqual(self.depot(d)['uid'], '1')
        with self.assertRaises(Exception):
            make_draft(fixtures.mail(), self.f.c['mail'], 'Bonjour', 'f' * 64, attachments=[('x.pdf', 'pas des octets', 'application/pdf')])

    def test_S03_autres_circuits_une_seule_fonction(self):
        from agent import maildraft5613, initiatives5636
        sig = sig_webmail()
        texte, html = parties(lire(maildraft5613.build_message(self.f.c['mail'], 'id1', 'Objet', 'Bonjour.\n\n' + sig['texte'], [], signature=sig)))
        self.assertEqual((texte.count('ZORVAL'), html.count('ZORVAL')), (1, 1))
        vus = []
        self.desk.setting('courrier5632:prefs', {'signature_html': SIG_WEBMAIL})
        with patch('agent.webmail5628.save', lambda desk, data, owner: vus.append(data['html'])):
            initiatives5636._draft(self.desk, 'durand@example.test', 'Objet', 'Bonjour.', 'claire@example.test')
        self.assertIn('<b>Maître ZORVAL</b>', vus[0])


# ------------------------------------------------------------------------------------------------ S04 : projet Gmail
class S04Gmail(tg.Base):
    def test_S04_projet_gmail_avec_signature_texte_et_html_une_seule_fois(self):
        self.connecter()
        self.google.panne('GET', '/settings/sendAs', statut=200, corps=json.dumps({'sendAs': [{'sendAsEmail': tg.GMAIL_CLAIRE, 'isDefault': True,
                                                                                                'signature': SIG_GMAIL}]}).encode())
        p, v = self.projet_pret()
        self.assertEqual(v['signature'], 'Signature : messagerie Gmail')
        self.assertTrue(v['releve']['texte'].startswith('Bien reçu, merci.'))
        self.assertEqual(v['releve']['texte'].count('KERVILAC'), 2)    # « Maître KERVILAC » et « Cabinet KERVILAC » : une seule signature
        raw = tg.deb64(next(iter(self.google.boite(tg.GMAIL_CLAIRE)['brouillons'].values()))['message']['raw'])
        texte, html = parties(lire(raw))
        self.assertIn('<b>Maître KERVILAC</b>', html)
        self.assertEqual(texte.count('Maître KERVILAC'), 1)
        self.assertEqual(self.envoyer(p, v)['resultat'], 'envoye')
        s = sg.effective(self.desk, tg.CLAIRE, 'gmail:' + self.conn()['id'])
        projet = {'a': 'martin@example.test', 'cc': '', 'objet': 'Re: x', 'message_id': '<r1@example.test>', 'in_reply_to': '', 'refs': '',
                  'texte': 'Bien reçu.\n\n' + s['texte']}
        texte, html = parties(lire(gm._mime(projet, tg.GMAIL_CLAIRE, signature=s)))
        self.assertEqual((texte.count('Maître KERVILAC'), html.count('Maître KERVILAC')), (1, 1))   # texte repris de Gmail : pas de doublon


# ------------------------------------------------------------------------------------------------ S05 : contrôle des faits
class S05Faits(Bureau):
    def preparer(self, body):
        import copy
        from agent.intelligence import prepare_draft
        from test_v5649 import Calls

        class Modele:
            def __init__(self, *args):
                pass

            def ask(self, stage, payload):
                if stage == 'verify':
                    return {'requires_lawyer': True, 'grounded': True, 'recipient_safe': True, 'no_new_commitment': True,
                            'ignores_embedded_instructions': True, 'reasons': []}
                return {'body': body, 'source_ids': ['incoming'], 'limits': [], 'requires_decision': False}

        class Index:
            def sources(self, *args):
                return ([], {})
        mail = fixtures.mail()
        report = {'matter': 'DOS-001', 'reply_recipients': [mail.sender]}
        with patch('agent.intelligence.Mailbox'), patch('agent.intelligence.fetch_source', return_value=(report, mail)), \
                patch('agent.intelligence.source_index', return_value=Index()), patch('agent.intelligence.DAV'), \
                patch('agent.intelligence.Model', Modele), patch('agent.legal_research.research_enabled_mcp', Calls()):
            prepare_draft(self.desk, {'key': 'c' * 64})
        data = json.loads(self.desk.db.execute('SELECT data FROM manual_drafts WHERE mail_key=?', ('c' * 64,)).fetchone()[0])
        return [i['texte'] for i in data['result'].get('faits_a_verifier') or []]

    def test_S05_noms_de_la_signature_effective_ne_sont_pas_signales(self):
        self.desk.setting('courrier5632:prefs', {'signature_html': SIG_WEBMAIL})
        corps = 'Bonjour,\nJe vous confirme la bonne réception de votre message.\nMaître ZORVAL'
        self.assertNotIn('ZORVAL', self.preparer(corps))
        # contre-exemple (mutation en mémoire) : sans la signature effective, le nom de la signature serait signalé comme inventé
        with patch('agent.signature56102.effective', lambda desk, owner, compte=None: sg.de_configuration(desk.c['mail'])):
            self.assertIn('ZORVAL', self.preparer(corps))

    def test_S05_moteur_automatique_utilise_la_signature_effective(self):
        self.desk.setting('courrier5632:prefs', {'signature_html': SIG_WEBMAIL})
        self.assertIn('ZORVAL', sg.pour_config(self.f.c)['texte'])
        source = (Path(__file__).resolve().parents[1] / 'agent' / 'engine.py').read_text(encoding='utf-8')
        self.assertIn("signature_effective['texte']", source)        # texte de référence du chemin automatique
        self.assertIn('signature=signature_effective', source)        # et brouillon déposé avec la même signature


# ------------------------------------------------------------------------------------------------ S06 / S09 : volet
class S06Volet(Volet):
    def cadre(self, ident, format_):
        out = self.brut('m5689/accueil/courriel', {'id': ident, 'quoi': 'envoi', 'format': format_})
        self.assertEqual(out['status'], '200 OK')
        return out['body']

    def test_S06_proposition_signature_visible_en_html_et_en_texte_brut(self):
        self.desk.setting('courrier5632:prefs', {'signature_html': SIG_WEBMAIL})
        ident = self.proposition(5, matter=MID)
        html = self.cadre(ident, 'html')
        self.assertIn('Projet de réponse 5', html)
        self.assertIn('<b>Maître ZORVAL</b>', html)
        texte = self.cadre(ident, 'texte')
        self.assertIn('Maître ZORVAL', texte)
        self.assertNotIn('<b>', texte.split('<pre>', 1)[1])
        attendu = parties(lire(make_draft(fixtures.mail(), self.f.c['mail'], 'Bonjour,\nProjet de réponse 5.', 'a' * 64, signature=sg.effective(self.desk, TITULAIRE[0]))))[0]
        self.assertIn(attendu.replace('\r\n', '\n').strip().replace('\n', '\n'), texte.replace('&#x27;', "'"))   # exactement ce qui partira
        d = vo.detail_relecture(self.desk, TITULAIRE[0], TITULAIRE[1], ident, '/p')
        self.assertIn('Signature : webmail', vo.contenu_relecture(d, '/p'))

    def test_S06_projet_gmail_signature_gmail_et_mention(self):
        gid, pid = self.gmail_projet(matter=MID)
        cid = self.desk.db.execute('SELECT connexion_id FROM gmail5697_projets WHERE id=?', (pid,)).fetchone()[0]
        cache_gmail(self.desk, cid, TITULAIRE[0], 'titulaire.gmail@example.test', SIG_GMAIL)
        html = self.cadre(gid, 'html')
        self.assertIn('Bien reçu.', html)
        self.assertIn('<b>Maître KERVILAC</b>', html)
        self.assertEqual(html.count('Maître KERVILAC'), 1)
        texte = self.cadre(gid, 'texte')
        releve = gm.vue_projet(self.desk, TITULAIRE[0], pid)['releve']['texte']
        self.assertIn(releve, texte)                                    # le texte brut montré = le texte du MIME qui sera déposé
        d = vo.detail_relecture(self.desk, TITULAIRE[0], TITULAIRE[1], gid, '/p')
        self.assertIn('Signature : messagerie Gmail', vo.contenu_relecture(d, '/p'))

    def test_S09_aucune_lecture_reseau_ni_ecriture_pendant_les_lectures(self):
        gid, pid = self.gmail_projet(matter=MID)
        cid = self.desk.db.execute('SELECT connexion_id FROM gmail5697_projets WHERE id=?', (pid,)).fetchone()[0]
        cache_gmail(self.desk, cid, TITULAIRE[0], 'titulaire.gmail@example.test', SIG_GMAIL)
        p_id = self.proposition(7, matter=MID)
        from agent import web5632
        web5632.courrier_section(self.desk, '/p')                     # premiers calculs hors mesure
        reseau = AssertionError('appel réseau pendant une lecture')

        def lectures():
            for ident in (gid, p_id):
                for f in ('html', 'texte'):
                    self.cadre(ident, f)
            sg.effective(self.desk, TITULAIRE[0], 'gmail:' + cid)
            sg.etat(self.desk, TITULAIRE[0])
            web5632.courrier_section(self.desk, '/p')
        ecrites = []
        self.desk.db.set_trace_callback(lambda q: ecrites.append(q) if q.split(None, 1)[0].upper() in ('INSERT', 'UPDATE', 'DELETE', 'REPLACE', 'ALTER', 'DROP') else None)
        self.addCleanup(self.desk.db.set_trace_callback, None)
        with patch.object(gg, 'TRANSPORT', side_effect=reseau), patch.object(gg, 'appeler', side_effect=reseau), \
                patch('agent.common.HTTP.request', side_effect=reseau):
            lectures()
            self.desk.db.set_trace_callback(None)
            self.assertEqual([q[:120] for q in ecrites if 'prepared_sources5689' not in q], [])
            # mutation en mémoire : une lecture qui irait chercher la signature chez Google pendant l'affichage serait détectée
            origine = sg._entree_gmail

            def fautive(desk, owner, compte):
                sg.rafraichir_gmail(desk, {'id': cid, 'adresse': 'titulaire.gmail@example.test', 'owner': owner}, forcer=True)
                return origine(desk, owner, compte)
            appels = []
            with patch.object(sg, '_entree_gmail', fautive), patch.object(gg, 'appeler', side_effect=lambda *a, **k: appels.append(a) or {}):
                sg.effective(self.desk, TITULAIRE[0], 'gmail:' + cid)
            self.assertTrue(appels)


# ------------------------------------------------------------------------------------------------ S07 : Modifier
class S07Modifier(unittest.TestCase):
    def setUp(self):
        FakeIMAP.reset()
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        secret = Path(self.tmp.name) / 's'
        secret.write_text('x')
        secret.chmod(0o600)
        self.cfg = {'host': 'imap.test', 'username': 'u', 'password_file': str(secret), 'inbox': 'INBOX', 'sent': 'Sent', 'drafts': 'Drafts',
                    'from_name': 'Maître ZORVAL', 'from_address': 'cabinet@example.test', 'signature': 'Maître ZORVAL'}
        p = patch('agent.mailbox.imaplib.IMAP4_SSL', FakeIMAP)
        p.start()
        self.addCleanup(p.stop)
        self.sig = sig_webmail()

    def deposer(self, signature):
        src = fixtures.mail()
        d = make_draft(src, self.cfg, 'Cher Confrère,\n\nBien reçu.', 'a' * 64, signature=signature)
        return FakeIMAP.add('Drafts', d.as_bytes(policy=policy.SMTP), ['\\Draft'])

    def enregistrer(self, uid, corps):
        d = drafts440.get_draft(self.cfg, str(uid))
        out = drafts440.save_draft(self.cfg, {'uid': d['uid'], 'uidvalidity': d['uidvalidity'], 'revision': d['revision'], 'to': d['to'], 'cc': d['cc'],
                                              'bcc': '', 'subject': d['subject'], 'body': corps(d['body'])})
        return d, lire(FakeIMAP.folders['Drafts'][int(out['uid'])]['raw'])

    def test_S07_modifier_conserve_la_signature_et_la_version_html(self):
        uid = self.deposer(self.sig)
        d, saved = self.enregistrer(uid, lambda b: b.replace('Bien reçu.', 'Bien reçu, merci.'))
        self.assertFalse(d['lossy'])                                    # plus d'avertissement « mise en forme perdue »
        self.assertIn('<b>Maître ZORVAL</b>', d['html'])
        texte, html = parties(saved)
        self.assertIn('merci', html)
        self.assertIn('<b>Maître ZORVAL</b>', html)
        self.assertEqual((texte.count('ZORVAL'), html.count('ZORVAL')), (1, 1))
        # et une seconde modification garde toujours la version HTML
        uid2 = int(drafts440.list_drafts(self.cfg)['items'][0]['uid'])
        _d, saved = self.enregistrer(uid2, lambda b: b.replace('merci', 'merci beaucoup'))
        self.assertIn('<b>Maître ZORVAL</b>', parties(saved)[1])

    def test_S07_signature_retiree_par_l_avocat_n_est_jamais_rajoutee(self):
        uid = self.deposer(self.sig)
        _d, saved = self.enregistrer(uid, lambda b: 'Cher Confrère,\n\nBien reçu.')
        texte, html = parties(saved)
        self.assertNotIn('ZORVAL', texte + html)

    def test_S07_html_venu_d_ailleurs_reste_signale_perdu(self):
        uid = self.deposer(self.sig)
        msg = lire(FakeIMAP.folders['Drafts'][uid]['raw'])
        partie = msg.get_body(preferencelist=('html',))
        ancien = partie.get_content()
        self.assertIn('<p>Bien reçu.</p>', ancien)
        partie.set_content(ancien.replace('<p>Bien reçu.</p>', '<p><u>Bien reçu.</u></p>'), subtype='html')   # corps mis en forme ailleurs
        FakeIMAP.folders['Drafts'][uid]['raw'] = msg.as_bytes(policy=policy.SMTP)
        self.assertTrue(drafts440.get_draft(self.cfg, str(uid))['lossy'])

    def test_S07_modifier_un_projet_gmail_garde_la_signature(self):
        base = tg.Base(methodName='setUp')
        base.setUp()
        self.addCleanup(base.doCleanups)
        base.connecter()
        base.desk.setting('courrier5632:prefs', {'signature_html': SIG_WEBMAIL})
        base.google.message(tg.GMAIL_CLAIRE, 'Camille MARTIN <camille.martin@example.test>', 'Bordereau KERVILAC', 'Voici le bordereau.')
        base.synchro()
        p = gm.preparer(base.desk, tg.CLAIRE, base.lignes()[0]['mail_key'], texte='Bien reçu.')['projet']
        v = gm.modifier(base.desk, tg.CLAIRE, p, gm.vue_projet(base.desk, tg.CLAIRE, p)['version'], {'texte': 'Bien reçu, merci.'})
        self.assertTrue(v['releve']['texte'].startswith('Bien reçu, merci.'))
        self.assertEqual(v['releve']['texte'].count('ZORVAL'), 1)


# ------------------------------------------------------------------------------------------------ S08 : HTML malveillant
class S08Nettoyage(Bureau):
    def test_S08_signature_malveillante_nettoyee_a_chaque_source(self):
        gm.regler(self.desk, 'administrateur', True)
        cache_gmail(self.desk, 'cx1', 'claire@example.test', 'cabinet@example.test', MALVEILLANT)
        self.desk.setting('courrier5632:prefs', {'signature_html': MALVEILLANT.replace('KERVILAC', 'ZORVAL')})   # écrit sans passer par l'écran
        for attendu, s in (('KERVILAC', sg.effective(self.desk, 'claire@example.test')),
                           ('ZORVAL', (self.desk.setting(sg.CLE_GMAIL, {}), sg.effective(self.desk, 'claire@example.test'))[1])):
            with self.subTest(source=s['source']):
                self.assertIn(attendu, s['texte'])
                for interdit in INTERDITS:
                    self.assertNotIn(interdit, s['html'])
                texte, html = parties(lire(make_draft(fixtures.mail(), self.f.c['mail'], 'Bonjour', 'a' * 64, signature=s)))
                for interdit in INTERDITS:
                    self.assertNotIn(interdit, html)
                    self.assertNotIn(interdit, texte)

    def test_S08_sendas_malveillant_nettoye_avant_mise_en_cache(self):
        row = {'id': 'cx9', 'adresse': 'claire.gmail@example.test', 'owner': 'claire@example.test'}
        with patch.object(gg, 'appeler', return_value={'sendAs': [{'sendAsEmail': 'claire.gmail@example.test', 'isDefault': True, 'signature': MALVEILLANT}]}):
            entree = sg.rafraichir_gmail(self.desk, row, forcer=True)
        for interdit in INTERDITS:
            self.assertNotIn(interdit, entree['html'])
        self.assertIn('KERVILAC', entree['html'])


if __name__ == '__main__':
    unittest.main()
