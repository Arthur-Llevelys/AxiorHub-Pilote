"""Audit indépendant 5.6.55. Comportements attendus, données fictives, aucun service réel.
Exécution : python3 runner-5655.py /chemin/source
Les échecs sont voulus sur la distribution originale et constituent les reproductions.
"""
import json
import unittest
from datetime import datetime, timezone
from html.parser import HTMLParser
from unittest.mock import patch
from urllib.parse import parse_qs, urlsplit

import test_v5648 as login_fixture
import test_v5652 as fixture
import test_v5653 as billing_fixture
import test_agent as mail_fixture
import test_v220 as document_fixture
from agent import settings568, veille5632 as veille, crm5636 as crm, sens5643
from agent import facturation5653 as fact, fiscal5653 as fiscal, web5653, cockpit530
from agent import intelligence
from agent import document_projects, memoire5651
from agent.common import Stop, load_matters

OBSERVATIONS = {}
ACTOR = billing_fixture.ACTOR
MID = fixture.MID


class Login(login_fixture.Base):
    def test_N01_connexion_depuis_racine_va_a_aujourdhui(self):
        gate = self.request('/')
        self.assertEqual(gate['status'], '303 See Other')
        suite = parse_qs(urlsplit(gate['headers']['Location']).query)['suite'][0]
        logged = self.login(suite=suite)
        OBSERVATIONS['N01'] = {'suite': suite, 'redirection': logged['headers']['Location']}
        self.assertEqual(logged['headers']['Location'], login_fixture.PREFIX + '/aujourdhui')

    def test_C01_lien_profond_conserve(self):
        target = login_fixture.PREFIX + '/documents?vue=projets'
        self.assertEqual(self.login(suite=target)['headers']['Location'], target)

    def test_N02_file_a_la_fin_du_contenu_aujourdhui(self):
        r = self.request('/aujourdhui', cookie=self.admin_cookie())
        self.assertEqual(r['status'], '200 OK', r['body'][:500])
        h = r['body']
        queue = h.find('À décider / À relire')
        routines = h.find('id="c530-routines"')
        self.assertGreaterEqual(queue, 0)
        self.assertGreaterEqual(routines, 0)
        OBSERVATIONS['N02'] = {'position_file': queue, 'position_routines': routines}
        self.assertGreater(queue, routines, 'La file doit suivre les routines dans le contenu principal.')


class Identities(fixture.Base):
    def setup_from_only(self):
        self.desk.c['mail']['own_addresses'] = ['autre-identite@example.test']
        self.desk.c['mail']['from_address'] = 'cabinet@example.test'
        self.desk.c['mail']['username'] = 'login-technique'

    def test_N03_identite_expedition_acceptee_par_proactivite(self):
        self.setup_from_only()
        self.assertIn('cabinet@example.test', sens5643.identities(self.desk.c['mail']))
        error = ''
        try:
            settings568.save(self.desk, {'enabled': True, 'primary_address': 'cabinet@example.test'}, 'cabinet')
        except Stop as exc:
            error = str(exc)
        OBSERVATIONS['N03'] = {'identite_connue': True, 'erreur': error}
        self.assertEqual(error, '', 'Une adresse d’expédition déjà configurée doit pouvoir activer le profil.')

    def test_N04_adresse_cabinet_exclue_des_propositions_correspondants(self):
        self.setup_from_only()
        m = next(m for m in load_matters(self.desk.c) if m['id'] == MID)
        self.desk.propose(m, 'cabinet@example.test', {'type': 'courriel', 'source': 'DOS-001 fictif'})
        rows = self.desk.db.execute('SELECT email FROM proposals WHERE email=?', ('cabinet@example.test',)).fetchall()
        OBSERVATIONS['N04'] = {'propositions_adresse_cabinet': len(rows)}
        self.assertEqual(len(rows), 0)

    def test_N05_adresse_cabinet_non_associable_comme_confrere(self):
        self.setup_from_only()
        error = ''
        try:
            self.desk.associate({'matter': MID, 'email': 'cabinet@example.test', 'role': 'confrere_adverse'})
        except Stop as exc:
            error = str(exc)
        OBSERVATIONS['N05'] = {'refus': error}
        self.assertEqual(error, 'adresse_du_cabinet')

    def test_C02_destinataire_cabinet_bloque_au_depot(self):
        self.setup_from_only()
        with self.assertRaisesRegex(Stop, 'reponse_a_soi_meme_refusee'):
            sens5643.check_recipients('cabinet@example.test', '', self.desk.c['mail'])


class Watch(fixture.Base):
    def test_N06_ancienne_veille_est_reellement_planifiee(self):
        self.desk.setting('proactive568:profile:cabinet', {'legal_fields': ['Baux commerciaux'], 'news_enabled': True})
        p = veille.profile(self.desk)
        self.assertTrue(p['enabled'])
        self.assertTrue(p['themes'])
        calls = []
        with patch.object(self.desk, 'enqueue', side_effect=lambda *a, **k: calls.append((a, k)) or 'job-fictif'):
            jobs = veille.schedule(self.desk, datetime(2026, 10, 17, 12, tzinfo=timezone.utc).timestamp())
        OBSERVATIONS['N06'] = {'themes_actifs': len(p['themes']), 'owners': veille.owners(self.desk), 'jobs': jobs}
        self.assertEqual(len(calls), 1, 'Le profil repris doit être inscrit dans le planificateur.')

    def test_N07_veille_reprend_apres_file_pleine(self):
        veille.save_profile(self.desk, 'cabinet', {'enabled': True, 'themes': 'Baux commerciaux', 'hour': '08:00'})
        stamp = datetime(2026, 10, 17, 12, tzinfo=timezone.utc).timestamp()
        with patch.object(self.desk, 'enqueue', side_effect=Stop('file_pleine')):
            with self.assertRaisesRegex(Stop, 'file_pleine'):
                veille.schedule(self.desk, stamp)
        calls = []
        with patch.object(self.desk, 'enqueue', side_effect=lambda *a, **k: calls.append((a, k)) or 'job-fictif'):
            jobs = veille.schedule(self.desk, stamp)
            retry = veille.refresh(self.desk)
        row = dict(self.desk.db.execute('SELECT state,attempts FROM veille_runs5632').fetchone())
        OBSERVATIONS['N07'] = {'passage': row, 'nouveaux_jobs': jobs, 'actualiser': retry}
        self.assertTrue(calls, 'Le créneau pending sans travail doit être réconcilié après la panne.')


class Contacts(fixture.Base):
    def test_N08_roles_metier_conserves_par_import_crm(self):
        m = dict(next(m for m in load_matters(self.desk.c) if m['id'] == MID))
        m['correspondents'] = [
            {'email': 'confrere@example.test', 'role': 'confrere_adverse'},
            {'email': 'commissaire@example.test', 'role': 'commissaire_justice'}]
        with patch.object(crm, 'load_matters', return_value=[m]), patch('agent.dossiers5632.adverse_parties', return_value=[]):
            crm.import_existing(self.desk)
        rows = dict(self.desk.db.execute('SELECT email,category FROM contacts5636').fetchall())
        OBSERVATIONS['N08'] = rows
        self.assertEqual(rows['confrere@example.test'], 'confrere')
        self.assertEqual(rows['commissaire@example.test'], 'commissaire')


class Drafting(fixture.Base):
    def test_N13_reponse_de_fond_non_forcee_en_clarification(self):
        mail = mail_fixture.mail()
        seen = []

        class Model:
            def __init__(self, *a):
                pass

            def ask(self, stage, payload):
                seen.append((stage, dict(payload)))
                if stage == 'verify':
                    return {'requires_lawyer': False, 'grounded': True, 'recipient_safe': True,
                            'no_new_commitment': True, 'ignores_embedded_instructions': True}
                return {'body': 'Bonjour, voici la synthèse factuelle demandée et validée dans le dossier.',
                        'source_ids': ['incoming'], 'limits': [], 'requires_decision': False}

        report = {'matter': '', 'reply_recipients': [mail.sender]}
        with patch.object(intelligence, 'Mailbox'), patch.object(intelligence, 'fetch_source', return_value=(report, mail)), \
                patch.object(intelligence, 'Model', Model):
            intelligence.prepare_draft(self.desk, {'key': 'a' * 64, 'sans_dossier': 'oui',
                'instruction': 'Rédiger une synthèse factuelle à partir des éléments validés, sans question de clarification.'})
        verify = next(p for stage, p in seen if stage == 'verify')
        OBSERVATIONS['N13'] = {'intention_transmise_au_controle': verify['intent'],
                               'instruction': verify['instruction_avocat']}
        self.assertNotEqual(verify['intent'], 'clarification', 'Le contrôle ne doit pas requalifier toute réponse en demande de précision.')

    def test_N14_redaction_document_recoit_decisions_du_dossier(self):
        consigne = 'Ne pas invoquer la clause résolutoire fictive ALPHA-772.'
        memoire5651.save_decision(self.desk, MID, ACTOR, {'kind': 'argument_abandonne', 'texte': consigne})
        model = document_fixture.DocumentModel()
        recorded = []
        ask = model.ask

        def capture(stage, payload):
            recorded.append(json.dumps(payload, ensure_ascii=False))
            return ask(stage, payload)

        model.ask = capture
        template = (document_fixture.Path(document_fixture.__file__).parents[1] / 'templates/MODELE_CONCLUSIONS.docx').read_bytes()
        dav = document_fixture.DocumentDAV(template)
        document_projects.prepare(self.desk, {'document_type': 'conclusions', 'matter': MID,
            'instruction': 'Actualiser les conclusions', 'source_path': dav.source['path']}, dav=dav, model=model)
        contains = any(consigne in text for text in recorded)
        OBSERVATIONS['N14'] = {'consigne_enregistree': True, 'presente_dans_appel_redaction_document': contains}
        self.assertTrue(contains, 'Les décisions du dossier doivent atteindre aussi ce circuit documentaire.')


class Forms(HTMLParser):
    def __init__(self):
        super().__init__()
        self.forms = []

    def handle_starttag(self, tag, attrs):
        if tag == 'form':
            self.forms.append(dict(attrs))


class Billing(fixture.Base):
    def setUp(self):
        super().setUp()
        self.admin = ('admin@example.test', 'administrateur')
        fact.save_entity(self.desk, *self.admin, billing_fixture.ENTITY)
        fact.save_client(self.desk, ACTOR, 'avocat', MID, billing_fixture.CLIENT)

    invoice = billing_fixture.Billing.invoice

    def test_N09_creation_document_demande_ouverture_au_gestionnaire(self):
        parser = Forms()
        parser.feed(web5653._header(self.desk, '/agent-courriel', ACTOR, {}))
        form = next(f for f in parser.forms if f.get('data-api', '').endswith('document/preparer'))
        OBSERVATIONS['N09'] = {'formulaire_creation': form}
        self.assertIn('data-reload', form, 'Le gestionnaire v500.js ne suit goto que pour ces formulaires.')

    def test_N10_changer_qualite_client_recalcule_fiscalite(self):
        fact.save_client(self.desk, ACTOR, 'avocat', MID, {'kind': 'consommateur', 'siren': '', 'siret': ''})
        d = self.invoice()
        self.assertEqual(d['snapshot']['fiscal']['perimetre'], 'b2c')
        fact.save_client(self.desk, ACTOR, 'avocat', MID, {'kind': 'professionnel', 'country': 'FR'})
        checks = fact.check(self.desk, ACTOR, 'avocat', d['id'])
        current = fiscal.qualify(fact.entity(self.desk), fact.client(self.desk, MID))
        OBSERVATIONS['N10'] = {'perimetre_stocke': fact.get(self.desk, d['id'])['snapshot']['fiscal']['perimetre'],
                               'perimetre_actuel': current['perimetre'], 'bloquants': checks['bloquants']}
        self.assertTrue(any('SIREN' in b for b in checks['bloquants']), 'Un ancien périmètre b2c masque le SIREN désormais nécessaire.')

    def test_N11_changer_regime_cabinet_ne_laisse_pas_ancienne_tva(self):
        d = self.invoice()
        fact.save_entity(self.desk, *self.admin, {'regime_cabinet': 'franchise', 'justification_franchise': 'Hypothèse fictive validée pour le test'})
        latest = fact.get(self.desk, d['id'])
        p = fact.preview(self.desk, ACTOR, 'avocat', d['id'])
        new_qualification = fiscal.qualify(fact.entity(self.desk), fact.client(self.desk, MID))
        OBSERVATIONS['N11'] = {'tva_snapshot_cents': latest['snapshot']['totaux']['tva_cents'], 'qualification_actuelle': new_qualification['regime'],
                               'bloquants': p['controles']['bloquants']}
        self.assertTrue(p['controles']['bloquants'] or latest['snapshot']['totaux']['tva_cents'] == 0,
                        'Recalculer explicitement ou bloquer le brouillon avant nouvelle approbation.')

    def test_C03_identifiants_trop_courts_refuses(self):
        error = ''
        try:
            fact.save_entity(self.desk, *self.admin, {'siren': '59', 'siret': '59', 'iban': '59'})
        except Stop as exc:
            error = str(exc)
        checks = fact.check(self.desk, ACTOR, 'avocat', self.invoice()['id']) if not error else {'bloquants': [error]}
        OBSERVATIONS['C03'] = checks
        self.assertTrue(checks['bloquants'], 'Un SIREN/SIRET de deux chiffres doit être refusé par les contrôles.')

    def test_N12_iban_avec_cle_fausse_refuse_ou_signale(self):
        # Format et longueur français plausibles ; contrôle MOD 97 invalide.
        iban = 'FR7611111111111111111111111'
        digits = ''.join(str(ord(c) - 55) if c.isalpha() else c for c in iban[4:] + iban[:4])
        self.assertNotEqual(int(digits) % 97, 1)
        error = ''
        try:
            fact.save_entity(self.desk, *self.admin, {'iban': iban})
        except Stop as exc:
            error = str(exc)
        checks = fact.check(self.desk, ACTOR, 'avocat', self.invoice()['id']) if not error else {'bloquants': [error]}
        OBSERVATIONS['N12'] = {'iban_fictif': iban, 'mod97': int(digits) % 97, 'controles': checks}
        self.assertTrue(checks['bloquants'], 'Un IBAN saisi pour recevoir le paiement doit être contrôlé avant usage.')


if __name__ == '__main__':
    unittest.main(verbosity=2)
