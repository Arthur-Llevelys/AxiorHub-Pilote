"""5.6.109 (lot G4, G10) : offre et PDF joint au brouillon depuis une instruction (``offre56109``).

Recette : « Christophe Martin, 22 novembre, 800 € HT » — une offre, un vrai PDF lisible, même prix et même périmètre, bon fil, brouillon
déposé et vérifié, sans envoi ni doublon ; montant de l'instruction ou de l'engagement du dossier, jamais inventé. Fausse messagerie (aucun IMAP
ni SMTP), convertisseur PDF simulé (LibreOffice jamais lancé), aucun réseau ; données fictives (example.test, DOS-001)."""
from datetime import datetime, timezone
from email.message import EmailMessage
import hashlib
import io
import json
import re
import socket
import unittest
from unittest.mock import patch
import zipfile

from agent import facturation5653 as fact, facturx5653 as fx, offre56109 as O, livrables5689 as liv
from agent.common import Stop
from agent.mailbox import Mail, body_text, draft_key
import test_v5652 as base52
import test_v530 as skillfixtures
from agent import competences5632 as c32, competences5643 as c43
import test_v5653 as base

INSTRUCTION = ('Réponds au courriel de Christophe Martin du 22 novembre 2026. Prépare une lettre de mission pour l’analyse de ses conditions '
               'générales de vente au forfait de 800 euros HT et joins-la en PDF au brouillon.')


def _reseau_interdit(*a, **k):
    raise AssertionError('appel réseau non simulé')


def setUpModule():
    global _p1, _p2
    _p1 = patch.object(socket.socket, 'connect', _reseau_interdit)
    _p2 = patch.object(socket, 'create_connection', _reseau_interdit)
    _p1.start()
    _p2.start()


def tearDownModule():
    _p1.stop()
    _p2.stop()


def texte_docx(data):
    with zipfile.ZipFile(io.BytesIO(data)) as z:
        xml = z.read('word/document.xml').decode('utf-8')
    paras = re.findall(r'<w:p[ >].*?</w:p>', xml, re.S)
    return [re.sub(r'<[^>]+>', '', p).replace(' ', ' ').replace('\xa0', ' ') for p in paras]


def faux_convertisseur(docx):
    """Convertisseur simulé : le texte du Word rendu en vrai PDF (lisible par le lecteur PDF du produit)."""
    from agent.document_projects import _pdf
    return _pdf(texte_docx(docx))


def courriel(uid, expediteur='Christophe Martin <compta@example.test>', jour=22, heure=10, objet='Analyse de nos CGV', mid=None):
    m = EmailMessage()
    m['From'] = expediteur
    m['To'] = 'cabinet@example.test'
    m['Subject'] = objet
    m['Message-ID'] = mid or '<cgv-%s@example.test>' % uid
    m.set_content('Bonjour Maître, pourriez-vous analyser nos conditions générales de vente ?')
    return Mail(str(uid), '7', 'INBOX', set(), datetime(2026, 11, jour, heure, 0, tzinfo=timezone.utc), m)


class Etat:
    """État partagé de la fausse messagerie (plusieurs connexions successives voient les mêmes brouillons)."""
    def __init__(self):
        self.inbox, self.drafts, self.appends, self.fail_append, self.stored_on_failure, self.closed = {}, [], 0, False, False, 0


class FausseBoite:
    def __init__(self, etat, cfg):
        self.etat, self.cfg = etat, cfg

    def search(self, folder, *criteria):
        if folder == self.cfg['drafts']:
            mid = criteria[-1].strip('"')
            return [str(i + 1) for i, d in enumerate(self.etat.drafts) if d['Message-ID'] == mid]
        return list(self.etat.inbox)

    def fetch(self, folder, uid, headers_only=False, **kw):
        if folder == self.cfg['drafts']:
            d = self.etat.drafts[int(uid) - 1]
            return Mail(uid, '9', folder, {'\\Draft'}, datetime.now(timezone.utc), d)
        return self.etat.inbox[uid]

    def find_own_draft(self, mid):
        return any(d['Message-ID'] == mid for d in self.etat.drafts)

    def append_draft(self, msg):
        self.etat.appends += 1
        if self.etat.fail_append:
            if self.etat.stored_on_failure:
                self.etat.drafts.append(msg)
            raise Stop('append_imap_incertain')
        self.etat.drafts.append(msg)

    def verify_draft(self, expected):
        ok = [i for i, d in enumerate(self.etat.drafts) if d['Message-ID'] == expected['Message-ID']]
        if len(ok) != 1:
            raise Stop('brouillon_non_retrouve' if not ok else 'plusieurs_brouillons_meme_identifiant')
        saved = self.etat.drafts[ok[0]]
        if draft_key(saved) != draft_key(expected) or body_text(saved) != body_text(expected):
            raise Stop('brouillon_contenu_non_conforme')
        return {'uid': str(ok[0] + 1), 'uidvalidity': '9', 'folder': self.cfg['drafts'], 'verified_at': datetime.now(timezone.utc).isoformat()}

    def close(self):
        self.etat.closed += 1


class Base(base52.Base):
    """Socle de ``test_v5653.Billing`` (fiche du cabinet et client fictifs) sans hériter de ses propres tests."""
    def setUp(self):
        super().setUp()
        base.fact.save_entity(self.desk, 'admin@example.test', 'administrateur', base.ENTITY)
        base.fact.save_client(self.desk, base.ACTOR, 'avocat', base.MID, base.CLIENT)
        self.boite = Etat()
        self.boite.inbox['1'] = courriel(1)
        p1 = patch.object(O, 'BOITE', lambda cfg: FausseBoite(self.boite, cfg))
        p2 = patch.object(fx, 'office_pdf', faux_convertisseur)
        p3 = patch.object(O, 'jour_cabinet', lambda desk: datetime(2026, 11, 23).date())
        for p in (p1, p2, p3):
            p.start()
            self.addCleanup(p.stop)
        O.ensure_schema(self.desk)
        self.n = 0

    def offre(self, instruction=INSTRUCTION, acteur=None, **plus):
        self.n += 1
        return O.preparer(self.desk, acteur or (base.ACTOR, 'avocat'), {'cle': plus.pop('cle', 'cle-offre-%04d' % self.n), 'instruction': instruction, **plus})

    def pieces(self, draft):
        return [p for p in draft.iter_attachments()]


# Socle seulement : les tests propres des classes de base (5.6.52, 5.6.53) ne sont pas rejoués ici.
for _nom in [n for n in dir(Base) if n.startswith('test')]:
    setattr(Base, _nom, None)

class G10Interpretation(unittest.TestCase):
    def test_G10_instruction_de_la_recette(self):
        r = O.interpreter(INSTRUCTION, datetime(2026, 11, 23).date())
        self.assertEqual((r['nom'], r['jour'], r['montant_ht_cents'], r['type'], r['prestation'], r['pdf']),
                         ('Christophe Martin', '2026-11-22', 80000, 'lettre_mission', 'analyse_cgv', True))
        court = O.interpreter('Christophe Martin, 22 novembre, 800 € HT, analyse des CGV', datetime(2026, 11, 23).date())
        self.assertEqual((court['nom'], court['jour'], court['montant_ht_cents']), ('Christophe Martin', '2026-11-22', 80000))

    def test_G10_montant_jamais_complete(self):
        j = datetime(2026, 11, 23).date()
        self.assertIsNone(O.interpreter('Courriel de Christophe Martin du 22 novembre, analyse des CGV', j)['montant_ht_cents'])
        self.assertIn('montant_ht_ou_ttc', O.interpreter('Courriel de Christophe Martin du 22 novembre, 800 €', j)['ambiguites'])
        self.assertIn('montant_ht_ou_ttc', O.interpreter('Courriel de Christophe Martin du 22 novembre, 960 € TTC', j)['ambiguites'])
        self.assertIn('montant_ambigu', O.interpreter('Courriel de Christophe Martin du 22 novembre, 800 € HT ou 900 € HT', j)['ambiguites'])


class G10Recette(Base):
    def test_G10_recette_une_offre_un_vrai_pdf_un_brouillon_sans_envoi(self):
        out = self.offre()
        self.assertEqual(out['etat'], 'depose', out)
        self.assertEqual((out['montant_ht'], out['origine_montant'], out['type'], out['dossier']), ('800,00 €', 'instruction', 'lettre_mission', base.MID))
        self.assertFalse(out['envoi'])
        self.assertEqual(len(self.boite.drafts), 1)
        draft = self.boite.drafts[0]
        self.assertEqual(draft['To'], 'compta@example.test')                      # adresse exacte du message source, confirmée au registre
        self.assertEqual(draft['In-Reply-To'], '<cgv-1@example.test>')            # bon fil
        corps = draft.get_body(preferencelist=('plain',)).get_content()
        self.assertIn('800,00 € HT', corps)
        self.assertIn('conditions générales de vente', corps)
        pj = self.pieces(draft)
        self.assertEqual(len(pj), 1)
        self.assertEqual(pj[0].get_content_type(), 'application/pdf')
        data = pj[0].get_payload(decode=True)
        self.assertTrue(data.startswith(b'%PDF-'))
        self.assertEqual(hashlib.sha256(data).hexdigest(), out['pdf']['sha256'])   # octets de la pièce = PDF vérifié
        texte = O.texte_pdf(data)                                                   # rouvert par le lecteur PDF : même prix, même périmètre
        self.assertIn('800,00', texte)
        self.assertIn('Analyse des conditions générales de vente', texte)
        docs = fact.documents(self.desk, base.MID, ('lettre_mission', 'devis'))
        self.assertEqual(len(docs), 1)                                              # une seule offre
        self.assertEqual(docs[0]['state'], 'brouillon')                             # ni approuvée, ni publiée, ni émise
        self.assertEqual(out['modele'], 'defaut:03-lettre-mission')
        self.assertTrue(out['modele_version'])
        self.assertTrue(out['brouillon']['uid'])

    def test_G10_document_canonique_du_registre_des_livrables(self):
        out = self.offre()
        row = self.desk.db.execute('SELECT * FROM prepared_items5689 WHERE id=?', (out['livrable'],)).fetchone()
        self.assertIsNotNone(row)
        self.assertEqual((row['sha256'], row['matter_id'], row['source_service'], row['source_object_id']),
                         (out['pdf']['sha256'], base.MID, 'facturation5653', out['document']))
        # même identité que la future publication du registre de facturation (même service, même objet)
        doc = liv._document_of(self.desk, {'service': 'facturation5653', 'object': out['document']})
        self.assertEqual(doc, row['document_id'])

    def test_G10_double_clic_et_reprise_sans_doublon(self):
        a = self.offre(cle='cle-unique-0001')
        b = O.preparer(self.desk, (base.ACTOR, 'avocat'), {'cle': 'cle-unique-0001', 'instruction': INSTRUCTION})
        self.assertEqual((a['id'], b['id'], b['etat']), (a['id'], a['id'], 'depose'))
        self.assertEqual((len(self.boite.drafts), self.boite.appends), (1, 1))
        self.assertEqual(len(fact.documents(self.desk, base.MID, ('lettre_mission',))), 1)
        with self.assertRaisesRegex(Stop, 'cle_reutilisee'):
            O.preparer(self.desk, (base.ACTOR, 'avocat'), {'cle': 'cle-unique-0001', 'instruction': INSTRUCTION + ' Merci.'})

    def test_G10_reponse_perdue_a_rapprocher_puis_rapprochee_sans_second_depot(self):
        self.boite.fail_append, self.boite.stored_on_failure = True, True
        out = self.offre(cle='cle-perdue-0001')
        self.assertEqual(out['etat'], 'a_rapprocher')
        self.boite.fail_append = False
        again = O.preparer(self.desk, (base.ACTOR, 'avocat'), {'cle': 'cle-perdue-0001', 'instruction': INSTRUCTION})
        self.assertEqual(again['etat'], 'depose')
        self.assertTrue(again['brouillon']['rapproche'])
        self.assertEqual((len(self.boite.drafts), self.boite.appends), (1, 1))     # jamais un second APPEND

    def test_G10_reponse_perdue_sans_brouillon_reste_a_rapprocher(self):
        self.boite.fail_append = True
        out = self.offre(cle='cle-perdue-0002')
        self.boite.fail_append = False
        again = O.preparer(self.desk, (base.ACTOR, 'avocat'), {'cle': 'cle-perdue-0002', 'instruction': INSTRUCTION})
        self.assertEqual((out['etat'], again['etat'], self.boite.appends, len(self.boite.drafts)), ('a_rapprocher', 'a_rapprocher', 1, 0))

    def test_G10_aucun_envoi_smtp(self):
        import smtplib
        with patch.object(smtplib, 'SMTP', side_effect=AssertionError('SMTP interdit')), patch.object(smtplib, 'SMTP_SSL', side_effect=AssertionError('SMTP interdit')):
            out = self.offre()
        self.assertEqual(out['etat'], 'depose')


class G10GardeFous(Base):
    def test_G10_convertisseur_absent_pdf_indisponible_rien_joint(self):
        with patch.object(fx, 'office_pdf', lambda docx: None):
            out = self.offre()
        self.assertEqual(out['etat'], 'pdf_indisponible')
        self.assertEqual((self.boite.drafts, self.boite.appends), ([], 0))
        self.assertTrue(out['questions'])

    def test_G10_word_annonce_comme_pdf_refuse(self):
        """Contre-exemple : un .docx renommé et typé PDF ; sans la garde ``verifier_pdf`` il serait joint au brouillon."""
        def faux_apercu(desk, actor, role, doc_id, _orig=fact.preview_file):
            with patch.object(fx, 'office_pdf', lambda d: None):
                vrai = _orig(desk, actor, role, doc_id)
            return {**vrai, 'name': 'Lettre de mission.pdf', 'mime': 'application/pdf'}
        with patch.object(fact, 'preview_file', faux_apercu):
            out = self.offre()
        self.assertEqual(out['etat'], 'pdf_indisponible')
        self.assertEqual(self.boite.drafts, [])

    def test_G10_mutation_sans_garde_un_word_partirait_comme_pdf(self):
        """Mutation en mémoire : la garde retirée, le Word part en pièce jointe « PDF » — preuve que la garde est ce qui l'empêche."""
        def faux_apercu(desk, actor, role, doc_id, _orig=fact.preview_file):
            with patch.object(fx, 'office_pdf', lambda d: None):
                vrai = _orig(desk, actor, role, doc_id)
            return {**vrai, 'name': 'Lettre de mission.pdf', 'mime': 'application/pdf'}
        with patch.object(fact, 'preview_file', faux_apercu), \
                patch.object(O, 'verifier_pdf', lambda apercu, attendus: (hashlib.sha256(apercu['data']).hexdigest(), len(apercu['data']))):
            out = self.offre()
        self.assertEqual(out['etat'], 'depose')
        data = self.pieces(self.boite.drafts[0])[0].get_payload(decode=True)
        self.assertTrue(data.startswith(b'PK'))                                     # un Word sous l'étiquette PDF

    def test_G10_pdf_non_concordant_refuse(self):
        from agent.document_projects import _pdf
        with patch.object(fx, 'office_pdf', lambda docx: _pdf(['Lettre de mission', 'Forfait de 900,00 € HT'])):
            out = self.offre()
        self.assertEqual((out['etat'], out['erreur']), ('pdf_indisponible', 'pdf_non_concordant'))

    def test_G10_montant_absent_question_jamais_invente(self):
        out = self.offre('Réponds au courriel de Christophe Martin du 22 novembre : lettre de mission pour l’analyse de ses CGV, PDF joint.')
        self.assertEqual(out['etat'], 'question')
        self.assertEqual(out['questions'][0]['code'], 'montant_a_preciser')
        self.assertEqual(fact.documents(self.desk, base.MID, ('lettre_mission', 'devis')), [])
        self.assertEqual(self.boite.drafts, [])

    def test_G10_montant_du_catalogue_engagement_du_dossier(self):
        from agent import catalogue56109 as C
        with patch.object(C, 'tarif_applicable', lambda desk, a, d, *x, **k: {'origine': 'proposition_acceptee', 'montant_ht_cents': 65000, 'libelle': 'Devis accepté'}):
            out = self.offre('Réponds au courriel de Christophe Martin du 22 novembre : lettre de mission pour l’analyse de ses CGV, PDF joint.')
        self.assertEqual((out['etat'], out['montant_ht'], out['origine_montant']), ('depose', '650,00 €', 'catalogue:proposition_acceptee'))

    def test_G10_estimation_non_engageante_du_catalogue_refusee(self):
        from agent import catalogue56109 as C
        with patch.object(C, 'tarif_applicable', lambda desk, a, d, *x, **k: {'origine': 'a_chiffrer', 'libelle': 'à chiffrer', 'taux_horaire_confirme': 25000}):
            out = self.offre('Réponds au courriel de Christophe Martin du 22 novembre : lettre de mission pour l’analyse de ses CGV, PDF joint.')
        self.assertEqual(out['questions'][0]['code'], 'montant_a_preciser')

    def test_G10_montant_hors_tarif_dans_la_reponse_refuse(self):
        """Mutation : une réponse qui cite un montant absent du document calculé est refusée avant tout dépôt."""
        with patch.object(O, 'corps_reponse', lambda *a: 'Bonjour, forfait de 1 200,00 € HT.'):
            with self.assertRaisesRegex(Stop, 'montant_hors_tarif'):
                self.offre()
        self.assertEqual(self.boite.drafts, [])

    def test_G10_homonymes_question_precise(self):
        self.boite.inbox['2'] = courriel(2, heure=15, objet='Autre question')
        out = self.offre()
        self.assertEqual(out['questions'][0]['code'], 'plusieurs_messages')
        self.assertEqual(len(out['questions'][0]['candidats']), 2)
        choisi = self.offre(message='<cgv-2@example.test>')
        self.assertEqual(choisi['etat'], 'depose')
        self.assertEqual(self.boite.drafts[0]['In-Reply-To'], '<cgv-2@example.test>')

    def test_G10_expediteur_hors_registre_aucune_identite_inventee(self):
        self.boite.inbox = {'3': courriel(3, expediteur='Christophe Martin <inconnu@example.test>')}
        out = self.offre()
        self.assertEqual(out['questions'][0]['code'], 'client_a_rattacher')
        self.assertEqual(self.boite.drafts, [])

    def test_G10_autre_jour_message_introuvable(self):
        self.boite.inbox = {'4': courriel(4, jour=21)}
        self.assertEqual(self.offre()['questions'][0]['code'], 'message_introuvable')

    def test_G10_droit_facturation_relu(self):
        with self.assertRaises(Stop):
            self.offre(acteur=('assistant@example.test', 'assistant'))
        self.assertEqual(self.boite.drafts, [])

    def test_G10_version_du_document_changee_entre_etapes(self):
        out = self.offre(cle='cle-version-0001')
        row = self.desk.db.execute('SELECT * FROM offres56109 WHERE id=?', (out['id'],)).fetchone()
        self.desk.db.execute("UPDATE offres56109 SET etat='document_prepare', pdf_sha256='' WHERE id=?", (out['id'],))
        self.desk.db.commit()
        d = fact.get(self.desk, row['document_id'])
        fact.update(self.desk, base.ACTOR, 'avocat', d['id'], {'textes': {'hypotheses': 'Modifié à la main'}}, d['version'])
        with self.assertRaisesRegex(Stop, 'version_perimee'):
            O.preparer(self.desk, (base.ACTOR, 'avocat'), {'cle': 'cle-version-0001', 'instruction': INSTRUCTION})

    def test_G10_publication_soumise_a_approbation_conservee(self):
        out = self.offre()
        with self.assertRaisesRegex(Stop, 'approbation_requise_ou_perimee'):
            fact.export_draft(self.desk, base.ACTOR, 'avocat', out['document'])


class G10Route(Base):
    def appel(self, route, data=None, owner=base.ACTOR, role='avocat'):
        from agent import web440
        corps = json.dumps(data or {}).encode()
        env = {'HTTP_X_AXIORHUB_USER': owner, 'HTTP_X_AXIORHUB_ROLE': role, 'HTTP_ORIGIN': 'https://pilote.example.test', 'HTTP_X_CSRF_TOKEN': 'jeton',
               'CONTENT_TYPE': 'application/json', 'CONTENT_LENGTH': str(len(corps)), 'wsgi.input': io.BytesIO(corps),
               'REQUEST_METHOD': 'GET' if data is None else 'POST'}
        out = web440.api(env, self.desk, {'origin': 'https://pilote.example.test', 'csrf': 'jeton'}, '/p', 'm56109/parcours/' + route, {}, env['REQUEST_METHOD'])
        return out['status'][:3], json.loads(out['body']), dict(out.get('headers') or [])

    def test_G10_route_offre_no_store_et_reponse(self):
        st, body, h = self.appel('offre', {'cle': 'cle-route-0001', 'instruction': INSTRUCTION})
        self.assertEqual((st, body['etat']), ('200', 'depose'))
        self.assertEqual(h.get('Cache-Control'), 'private, no-store')

    def test_G10_route_role_et_autorisation_standalone(self):
        from agent import standalone_auth
        self.assertTrue(standalone_auth.allowed('avocat', 'POST', '/api440/m56109/parcours/offre'))
        self.assertFalse(standalone_auth.allowed('assistant', 'POST', '/api440/m56109/parcours/collecte-lancer'))
        self.assertFalse(standalone_auth.allowed('avocat', 'POST', '/api440/m56109/parcours/collecte-profil'))
        st, body, _ = self.appel('collecte-profil', {'portail': 'ebarreau'})
        self.assertEqual(st, '403')


class G4Competences(skillfixtures.Base):
    """Les trois méthodes natives du lot sont proposées ACTIVES sur une installation neuve (guide des parcours : « proposées actives comme
    méthodes sur une installation neuve ; une désactivation existante reste conservée ») ; Assistant juridique reste premier."""
    def setUp(self):
        super().setUp()
        c32.ensure_builtin(self.desk)
        self.desk.setting('competences5632:auto', True)

    def selection(self, texte):
        return [r['id'] for r in c43.select(self.desk, texte, 'analyse', kind='assistant_answer')]

    def test_G10_G11_G17_methodes_actives_sur_installation_neuve_et_desactivation_conservee(self):
        cas = (('Réponds au mail de Christophe Martin avec un devis pour analyse CGV à 800 HT', 'builtin.parcours109.offre_brouillon'),
               ('Récupère mes messages e-Barreau RPVA non lus depuis cinq jours', 'builtin.parcours109.portail_procedural'),
               ('Télécharge les statuts Pappers de la partie adverse', 'builtin.parcours109.pappers_documents'))
        for texte, sid in cas:
            self.assertEqual(self.selection(texte), ['builtin.assistant_juridique', sid])   # active d'office, Assistant juridique en tête
        self.assertEqual(self.selection('Explique-moi ce qu’est une clause de non-concurrence'), ['builtin.assistant_juridique'])   # aucune méthode de parcours sans demande
        c32.set_enabled(self.desk, 'cabinet', cas[0][1], False)
        c32.ensure_builtin(self.desk)
        self.assertNotIn(cas[0][1], self.selection(cas[0][0]))                 # désactivation conservée


for _nom in [n for n in dir(skillfixtures.Base) if n.startswith('test')]:
    setattr(G4Competences, _nom, None)


if __name__ == '__main__':
    unittest.main()
