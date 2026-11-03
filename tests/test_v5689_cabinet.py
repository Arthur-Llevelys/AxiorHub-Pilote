"""5.6.89, lot cabinet : profil versionné, assistant de démarrage, preuves de capacité, identité de courriel, mission de démonstration.

Données fictives (domaines example.test, ZORVAL, KERVILAC), faux services : aucun réseau, aucun modèle, aucun compte réel. Les fournisseurs répondent depuis un serveur simulé :
ces tests prouvent le circuit logiciel, pas le comportement d'un vrai Microsoft 365, Gmail ou Nextcloud. Les essais sur de vrais comptes de test restent à faire (chapitre 32).
"""
import base64
from datetime import datetime, timedelta, timezone
import email
from email.message import EmailMessage
import io
import json
from pathlib import Path
import sqlite3
import tempfile
import unittest
from unittest.mock import patch
import zipfile

from agent import cabinet_profile5689 as cp, cabinet_web5689 as web, competences5632, connector_readiness5689 as cr, connexions5652 as cx, export5688, membres5650, missions567, sens5643, web5689
from agent.common import Stop
from agent.dav import DAV
from agent.desk import Desk
from agent.document_projects import _dav
from agent.mailbox import Mail, Mailbox, exclusion, make_draft, own_addresses
from agent.office440 import dav_client
import test_agent as fixtures
from test_v5652 import Fake, PARAMS, SECRETS

ADMIN, AVOCAT, ASSISTANT = 'admin@example.test', 'avocat@example.test', 'assistant@example.test'


def courriel(de='client@example.test', a='cabinet@example.test', cc='', repondre=''):
    m = EmailMessage()
    m['From'] = de
    m['To'] = a
    if cc:
        m['Cc'] = cc
    if repondre:
        m['Reply-To'] = repondre
    m['Subject'] = 'DOS-001 fictif'
    m['Message-ID'] = '<fictif-1@example.test>'
    m.set_content('Bonjour.')
    return Mail('1', '7', 'INBOX', set(), datetime.now(timezone.utc), m)


class Base(unittest.TestCase):
    def setUp(self):
        self.f = fixtures.EngineTests('test_observation_has_no_mail_write')
        self.f.setUp()
        self.addCleanup(self.f.doCleanups)
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        (self.root / 'Dossiers' / 'DEMO').mkdir(parents=True)
        (self.root / 'Dossiers' / 'DEMO' / 'Livrable.txt').write_text('Projet fictif', encoding='utf-8')
        self.f.c['nextcloud'] = {**self.f.c['nextcloud'], 'local_path': str(self.root)}
        self.f.c['ollama'] = {'url': 'http://127.0.0.1:11434', 'model': 'test-local'}
        self.desk = Desk(self.f.c)
        self.addCleanup(self.desk.db.close)
        self.fake = Fake()
        p = patch.object(cx, 'HTTP_FACTORY', self.fake)
        p.start()
        self.addCleanup(p.stop)
        self.n = 0

    def cle(self):
        self.n += 1
        return 'essai-cle-%06d' % self.n

    def qualif(self, conn, cap, autoriser=False, owner=ADMIN, role='administrateur', cle=None):
        return cr.qualifier(self.desk, owner, role, conn, cap, cle or self.cle(), autoriser)

    def ia_prouvee(self):
        with patch('agent.config567.test', return_value={'ok': True, 'models': ['test-local'], 'writes': 0}):
            return self.qualif('cfg-ia', 'ai.generate')

    def graph(self, caps=('mail.create_draft',)):
        """Compte Microsoft fictif relié par le circuit habituel des connexions."""
        data = {'provider': 'microsoft_graph', 'environment': 'test', 'client_id': PARAMS['client_id'], 'tenant': 'common', 'portee': 'cabinet'}
        for cap in caps:
            data['cap_' + cap.replace('.', '_')] = True
        view = cx.save(self.desk, ADMIN, 'administrateur', data)
        cx._store_tokens(self.desk, view['id'], {'access_token': 'jeton-oauth-fictif', 'refresh_token': 'renouvellement-fictif', 'expires_in': 3600}, cx._row(self.desk, view['id'])['token_version'])
        self.fake.on('GET', '/me?', {'userPrincipalName': 'cabinet@example.test'})
        self.fake.on('GET', '/me/messages?', {'value': [{'subject': 'Fictif', 'from': {'emailAddress': {'address': 'client@example.test'}}}]})
        return view['id']

    def brouillons_graph(self, cle_changement='ck-1'):
        """Boîte simulée : un brouillon créé, relu, retiré. ``deplacements`` compte les mises à la corbeille."""
        self.etat = {'sujet': '', 'cle': cle_changement, 'deplacements': 0, 'creations': 0}

        def creer(method, url, data, headers):
            self.etat['creations'] += 1
            corps = json.loads(data)
            self.etat['sujet'] = corps['subject']
            self.etat['destinataires'] = corps['toRecipients']
            return {'id': 'brouillon-1'}

        def relire(method, url, data, headers):
            return {'subject': self.etat['sujet'], 'isDraft': True, 'changeKey': self.etat['cle'], 'toRecipients': []}

        def deplacer(method, url, data, headers):
            self.etat['deplacements'] += 1
            return {'id': 'brouillon-1'}
        self.fake.on('POST', '/me/messages', creer)
        self.fake.on('GET', '/me/messages/brouillon-1', relire)
        self.fake.on('POST', '/me/messages/brouillon-1/move', deplacer)

    def api(self, route, data=None, method='POST', owner=ADMIN, role='administrateur', args=None):
        corps = json.dumps(data or {}).encode()
        env = {'HTTP_ORIGIN': 'https://axiorhub.example.test', 'HTTP_X_CSRF_TOKEN': 'jeton', 'CONTENT_TYPE': 'application/json', 'CONTENT_LENGTH': str(len(corps)), 'wsgi.input': io.BytesIO(corps),
               'HTTP_X_AXIORHUB_USER': owner, 'HTTP_X_AXIORHUB_ROLE': role}
        out = web5689.api(env, self.desk, {'origin': 'https://axiorhub.example.test', 'csrf': 'jeton'}, '/agent', 'm5689/' + route, args or {}, method)
        body = out['body']
        return out['status'], json.loads(body) if isinstance(body, (str, bytes)) and out.get('kind', '').startswith('application/json') else body


# ------------------------------------------------------------------------------------------------ profil
class Profil(Base):
    def test_reprise_unique_sans_secret_et_sans_certifier(self):
        self.desk.setting('pieces510:avocat', {'nom_avocat': 'DURAND', 'ville_avocat': 'Valbrune', 'numero_telephone_avocat': '0100000000'})
        self.desk.c['mail']['password_file'] = str(self.f.base / 'imap.secret')
        lu = cp.lire(self.desk)
        self.assertEqual((lu['revision'], lu['identite']['barreau']), (1, 'Valbrune'))
        self.assertEqual({a['statut'] for a in lu['alias']}, {'a_confirmer'})          # une reprise ne certifie rien
        self.assertIn('cabinet@example.test', lu['own_addresses'])                     # et ne fait rien perdre
        brut = self.desk.db.execute('SELECT data_json FROM cabinet_profile5689').fetchone()[0]
        self.assertNotIn('imap.secret', brut)
        self.desk.setting('pieces510:avocat', {'ville_avocat': 'Autre'})
        cp.importer_existant(self.desk)
        self.assertEqual(cp.lire(self.desk)['identite']['barreau'], 'Valbrune')        # une seule fois

    def test_reprise_des_modeles_valides_et_des_competences_actives(self):
        from agent import cabinet_docs33
        cabinet_docs33.ensure_schema(self.desk)
        competences5632.ensure_builtin(self.desk)
        for ident, statut in (('modele-valide', 'approved'), ('modele-brouillon', 'draft')):
            self.desk.db.execute('INSERT INTO cabinet_templates_v330 VALUES(?,?,?,?,?,?,?,?,?)', (ident, 'Courrier fictif ' + ident, 1, 'a' * 64, statut, '[]', '1', '2026-11-02', ''))
        self.desk.db.commit()
        lu = cp.lire(self.desk)
        self.assertEqual(lu['template_refs'], ['modele-valide'])
        self.assertIn('builtin.assistant_juridique', lu['skill_refs'])

    def test_adresses_saisies_a_l_installation_sont_confirmees(self):
        self.desk.c['cabinet5689'] = {'profil': 'serveur_cabinet', 'adresses_saisies': ['cabinet@example.test']}
        lu = cp.lire(self.desk)
        self.assertEqual(lu['profil_deploiement'], 'serveur_cabinet')
        self.assertEqual([a['statut'] for a in lu['alias'] if a['adresse'] == 'cabinet@example.test'], ['confirmee'])

    def test_contrat_cabinetprofile(self):
        lu = cp.lire(self.desk)
        attendu = ('cabinet_id', 'revision', 'own_addresses', 'signature_refs', 'practice_areas', 'language', 'template_refs', 'skill_refs', 'connector_refs', 'policy_ref')
        self.assertTrue(all(k in lu for k in attendu))
        self.assertTrue(lu['cabinet_id'])
        self.assertGreaterEqual(len(lu['own_addresses']), 1)

    def test_revision_perimee_409_droits_403_et_versions(self):
        lu = cp.lire(self.desk)
        status, out = self.api('cabinet/profil', {'revision': lu['revision'], 'nom_cabinet': 'Cabinet ZORVAL'})
        self.assertEqual(status[:3], '200')
        self.assertEqual(out['revision'], 2)
        status, out = self.api('cabinet/profil', {'revision': lu['revision'], 'nom_cabinet': 'Écrasement'})
        self.assertEqual(status[:3], '409')
        self.assertEqual(out['error'], 'revision_perimee')
        self.assertEqual(cp.lire(self.desk)['identite']['nom_cabinet'], 'Cabinet ZORVAL')
        status, out = self.api('cabinet/profil', {'revision': 2, 'nom_cabinet': 'Refus'}, owner=AVOCAT, role='avocat')
        self.assertEqual(status[:3], '403')
        status, out = self.api('cabinet/profil', {'revision': 2, 'nom_cabinet': 'Refus'}, owner=ASSISTANT, role='assistant')
        self.assertEqual(status[:3], '403')
        self.assertEqual([v['revision'] for v in cp.versions(self.desk)], [2, 1])
        status, out = self.api('cabinet/profil', method='GET', owner=ASSISTANT, role='assistant')
        self.assertEqual((status[:3], out['revision']), ('200', 2))

    def test_droit_reglages_accorde_a_un_avocat(self):
        lu = cp.lire(self.desk)
        membres5650.set_rights(self.desk.c['state_dir'], ADMIN, AVOCAT, 'avocat', {'reglages': True})
        self.assertEqual(cp.enregistrer(self.desk, AVOCAT, 'avocat', {'revision': lu['revision'], 'barreau': 'Valbrune'})['revision'], 2)

    def test_acteur_vient_de_la_session(self):
        lu = cp.lire(self.desk)
        status, out = self.api('cabinet/profil', {'revision': lu['revision'], 'author': 'usurpateur', 'role': 'administrateur'}, owner=AVOCAT, role='avocat')
        self.assertEqual(status[:3], '403')

    def test_secret_et_champ_inconnu_refuses(self):
        lu = cp.lire(self.desk)
        for champ, code in (({'nom_cabinet': 'Bearer abcdefghijklmnopqrstuvwxyz012345'}, 'secret_refuse_dans_le_profil'), ({'api_key': 'x'}, 'secret_refuse_dans_le_profil'),
                            ({'mot_de_passe': 'x'}, 'secret_refuse_dans_le_profil'), ({'champ_libre': 'x'}, 'champ_profil_inconnu')):
            with self.assertRaises(Stop) as ctx:
                cp.enregistrer(self.desk, ADMIN, 'administrateur', {'revision': lu['revision'], **champ})
            self.assertEqual(str(ctx.exception), code)
        self.assertEqual(cp.lire(self.desk)['revision'], lu['revision'])

    def test_enregistrer_a_l_identique_ne_cree_pas_de_revision(self):
        lu = cp.lire(self.desk)
        out = cp.enregistrer(self.desk, ADMIN, 'administrateur', {'revision': lu['revision'], 'barreau': lu['identite']['barreau']})
        self.assertTrue(out['inchange'])
        self.assertEqual(out['revision'], lu['revision'])

    def test_donnees_deduites_restent_des_propositions(self):
        cp.lire(self.desk)
        texte = 'Maître DURAND\nAvocat au Barreau de Valbrune\nToque 123\n01 00 00 00 42\ncontact@example.test'
        out = cp.deduire(self.desk, ADMIN, 'administrateur', texte)
        champs = {p['champ']: p for p in out['propositions']}
        self.assertEqual(set(champs), {'barreau', 'toque', 'telephone', 'adresse_propre'})
        lu = cp.lire(self.desk)
        self.assertEqual(lu['identite']['barreau'], '')                                # rien d'appliqué
        self.assertNotIn('contact@example.test', lu['own_addresses'])
        self.assertNotIn('contact@example.test', sens5643.identities(self.desk.c['mail']))
        cp.deduire(self.desk, ADMIN, 'administrateur', texte)
        self.assertEqual(len(cp.propositions(self.desk)), 4)                           # pas de doublon
        cp.decider_proposition(self.desk, ADMIN, 'administrateur', champs['barreau']['id'], 'confirmer')
        cp.decider_proposition(self.desk, ADMIN, 'administrateur', champs['adresse_propre']['id'], 'confirmer')
        cp.decider_proposition(self.desk, ADMIN, 'administrateur', champs['toque']['id'], 'rejeter')
        lu = cp.lire(self.desk)
        self.assertEqual((lu['identite']['barreau'], lu['identite']['toque']), ('Valbrune', ''))
        self.assertIn('contact@example.test', lu['own_addresses'])
        with self.assertRaises(Stop) as ctx:
            cp.decider_proposition(self.desk, ADMIN, 'administrateur', champs['toque']['id'], 'confirmer')
        self.assertEqual(str(ctx.exception), 'proposition_deja_decidee')
        self.assertFalse(any('Maître' in json.dumps(dict(r)) for r in self.desk.db.execute('SELECT * FROM cabinet_proposals5689')))   # le texte source n'est pas conservé

    def test_modes_d_hebergement_honnetes(self):
        lu = cp.lire(self.desk)
        cp.enregistrer(self.desk, ADMIN, 'administrateur', {'revision': lu['revision'], 'profil_deploiement': 'cloud_gere'})
        m = cp.manifeste(self.desk)
        self.assertEqual(m['modes']['cloud_gere']['etat'], 'non_fourni')
        self.assertEqual(m['modes']['serveur_cabinet']['etat'], 'fourni')
        self.assertEqual(m['choix'], 'cloud_gere')
        self.assertFalse(next(s for s in m['services'] if s['id'] == 'hebergement_gere')['inclus'])
        with self.assertRaises(Stop):
            cp.enregistrer(self.desk, ADMIN, 'administrateur', {'revision': cp.lire(self.desk)['revision'], 'profil_deploiement': 'mutualise'})

    def test_manifeste_n_annonce_pas_un_service_non_relie(self):
        self.desk.c['mail']['enabled'] = False
        m = cp.manifeste(self.desk)
        self.assertFalse(next(s for s in m['services'] if s['id'] == 'messagerie')['inclus'])

    def test_export_comprend_les_tables_sans_secret(self):
        vues = {t[2] for t in export5688.TABLES}
        cible = {'cabinet_profile5689', 'cabinet_profile_versions5689', 'identity_aliases5689', 'cabinet_proposals5689', 'cabinet_wizard5689', 'capability_proofs5689', 'capability_tests5689',
                 'capability_revocations5689', 'demo_missions5689'}
        self.assertLessEqual(cible, vues)
        conn = self.graph()
        self.brouillons_graph()
        self.qualif(conn, 'mail.create_draft', True)
        cp.lire(self.desk)
        for table in cible:
            noms = [r[1] for r in self.desk.db.execute('PRAGMA table_info(%s)' % table)]
            self.assertTrue(noms, table)
            self.assertFalse([n for n in noms if export5688._secrete(n)], table)
            brut = json.dumps([dict(r) for r in self.desk.db.execute('SELECT * FROM ' + table)], ensure_ascii=False)
            for secret in ('jeton-oauth-fictif', 'renouvellement-fictif'):
                self.assertNotIn(secret, brut, table)


# ------------------------------------------------------------------------------------------------ IMP01A : assistant interrompu
class Assistant(Base):
    def test_IMP01A_reprise_de_l_etape_sans_ressaisie_ni_doublon(self):
        etat = cp.assistant(self.desk)
        self.assertEqual((etat['etape'], [e['id'] for e in etat['etapes']]), ('identite', ['identite', 'outils', 'modeles', 'demonstration']))
        saisie = {'etape': 'identite', 'action': 'enregistrer', 'revision': etat['revision'], 'nom_cabinet': 'Cabinet KERVILAC', 'adresses_propres': 'accueil@example.test\ncontact+dossiers@example.test',
                  'signature': 'Barreau de Valbrune', 'profil_deploiement': 'hybride'}
        out = cp.avancer(self.desk, ADMIN, 'administrateur', saisie)
        self.assertEqual(out['etape'], 'identite')
        self.assertEqual(cp.lire(self.desk)['identite']['nom_cabinet'], '')            # « reprendre plus tard » n'applique rien
        # coupure : nouvelle ouverture, tout est là
        autre = Desk(self.f.c)
        self.addCleanup(autre.db.close)
        repris = cp.assistant(autre)
        self.assertEqual(repris['valeurs']['identite']['nom_cabinet'], 'Cabinet KERVILAC')
        self.assertEqual(repris['etape'], 'identite')
        continuer = {**saisie, 'action': 'continuer', 'revision': repris['revision']}
        out = cp.avancer(autre, ADMIN, 'administrateur', continuer)
        self.assertEqual(out['etape'], 'outils')
        revision = cp.lire(autre)['revision']
        adresses = len(cp.aliases(autre))
        signatures = len(cp.lire(autre)['signature_refs'])
        # rejouer l'étape après un retour arrière ne crée rien de plus
        out = cp.avancer(autre, ADMIN, 'administrateur', {'etape': 'outils', 'action': 'retour', 'revision': out['revision']})
        self.assertEqual(out['etape'], 'identite')
        out = cp.avancer(autre, ADMIN, 'administrateur', {**saisie, 'action': 'continuer', 'revision': out['revision']})
        self.assertEqual(out['etape'], 'outils')
        self.assertEqual((cp.lire(autre)['revision'], len(cp.aliases(autre)), len(cp.lire(autre)['signature_refs'])), (revision, adresses, signatures))
        self.assertEqual(autre.db.execute('SELECT COUNT(*) FROM cabinet_wizard5689').fetchone()[0], 1)

    def test_IMP01A_reouverture_ne_double_pas_le_compte_ni_le_modele(self):
        premier = self.graph()
        second = self.graph()
        self.assertEqual(premier, second)
        self.assertEqual(self.desk.db.execute('SELECT COUNT(*) FROM connexions5652').fetchone()[0], 1)
        etat = cp.assistant(self.desk)
        donnees = {'etape': 'modeles', 'action': 'continuer', 'revision': etat['revision'], 'modele:M1': True, 'template_refs': ['M1', 'M1'], 'competence:assistant-juridique': True}
        out = cp.avancer(self.desk, ADMIN, 'administrateur', donnees)
        lu = cp.lire(self.desk)
        self.assertEqual(lu['template_refs'], ['M1'])
        out = cp.avancer(self.desk, ADMIN, 'administrateur', {**donnees, 'revision': out['revision']})
        self.assertEqual((cp.lire(self.desk)['template_refs'], cp.lire(self.desk)['revision']), (['M1'], lu['revision']))

    def test_IMP01A_creation_interrompue_ne_recree_pas_la_ressource(self):
        conn = self.graph()
        self.brouillons_graph()
        self.fake.on('GET', 'mailFolders/drafts/messages', lambda *a: {'value': [{'id': 'brouillon-1', 'subject': self.etat['sujet']}]})

        def coupure(method, url, data, headers):
            corps = json.loads(data)
            self.etat.update(sujet=corps['subject'], creations=self.etat['creations'] + 1)
            raise Stop('delai_http_depasse')                                           # créé là-bas, accusé perdu ici
        self.fake.routes = [r for r in self.fake.routes if not (r[0] == 'POST' and r[1] == '/me/messages')]
        self.fake.routes.insert(0, ('POST', '/me/messages', coupure, 200))
        out = self.qualif(conn, 'mail.create_draft', True)
        self.assertEqual(out['preuves'][-1]['resultat'], 'indetermine')
        self.assertNotEqual(out['etat'], 'prouvee')
        out = self.qualif(conn, 'mail.create_draft', True)
        self.assertEqual(self.etat['creations'], 1)                                    # aucune seconde création
        self.assertEqual(out['etat'], 'prouvee')
        self.assertTrue(next(p for p in out['preuves'] if p['nature'] == 'creation_test')['preuve']['ressource_reprise'])
        self.assertEqual(self.etat['deplacements'], 1)

    def test_IMP01A_incertain_sans_recherche_possible_reste_a_verifier(self):
        conn = self.graph()
        self.brouillons_graph()
        self.fake.routes = [r for r in self.fake.routes if not (r[0] == 'POST' and r[1] == '/me/messages')]
        self.fake.fail['/me/messages'] = 'reseau'
        self.qualif(conn, 'mail.create_draft', True)
        del self.fake.fail['/me/messages']
        comptes = len([c for c in self.fake.calls if c[0] == 'POST'])
        out = self.qualif(conn, 'mail.create_draft', True)                             # la recherche échoue (route absente) : rien n'est recréé à l'aveugle
        self.assertEqual(len([c for c in self.fake.calls if c[0] == 'POST']), comptes)
        self.assertEqual(out['nettoyage']['etat'], 'a_verifier')

    def test_revision_de_l_assistant_perimee(self):
        etat = cp.assistant(self.desk)
        cp.avancer(self.desk, ADMIN, 'administrateur', {'etape': 'identite', 'action': 'enregistrer', 'revision': etat['revision']})
        status, out = self.api('cabinet/assistant', {'etape': 'identite', 'action': 'enregistrer', 'revision': etat['revision']})
        self.assertEqual((status[:3], out['error']), ('409', 'revision_perimee'))

    def test_assistant_sans_droit_403(self):
        etat = cp.assistant(self.desk)
        status, out = self.api('cabinet/assistant', {'etape': 'identite', 'action': 'enregistrer', 'revision': etat['revision']}, owner=AVOCAT, role='avocat')
        self.assertEqual(status[:3], '403')

    def test_fin_d_assistant_exige_la_demonstration(self):
        etat = cp.assistant(self.desk)
        for etape in ('identite', 'outils', 'modeles'):
            etat = cp.avancer(self.desk, ADMIN, 'administrateur', {'etape': etape, 'action': 'continuer', 'revision': etat['revision']})
        self.assertEqual(etat['etape'], 'demonstration')
        with self.assertRaises(Stop) as ctx:
            cp.avancer(self.desk, ADMIN, 'administrateur', {'etape': 'demonstration', 'action': 'continuer', 'revision': etat['revision']})
        self.assertEqual(str(ctx.exception), 'demonstration_requise')
        cp.lancer_demonstration(self.desk, ADMIN, 'administrateur', 'demo-cle-0001')
        etat = cp.avancer(self.desk, ADMIN, 'administrateur', {'etape': 'demonstration', 'action': 'continuer', 'revision': etat['revision']})
        self.assertEqual(etat['statut'], 'termine')

    def test_secret_refuse_dans_le_brouillon_de_l_assistant(self):
        etat = cp.assistant(self.desk)
        with self.assertRaises(Stop):
            cp.avancer(self.desk, ADMIN, 'administrateur', {'etape': 'identite', 'action': 'enregistrer', 'revision': etat['revision'], 'signature': 'Bearer abcdefghijklmnopqrstuvwxyz012345'})
        self.assertNotIn('abcdefghij', self.desk.db.execute('SELECT values_json FROM cabinet_wizard5689').fetchone()[0])

    def test_ecran_de_parametres_reprend_l_etape_et_echappe(self):
        etat = cp.assistant(self.desk)
        cp.avancer(self.desk, ADMIN, 'administrateur', {'etape': 'identite', 'action': 'enregistrer', 'revision': etat['revision'], 'nom_cabinet': '<script>ZORVAL</script>'})
        env = {'HTTP_X_AXIORHUB_USER': ADMIN, 'HTTP_X_AXIORHUB_ROLE': 'administrateur'}
        html = web.settings_html(self.desk, {'csrf': 'c', 'origin': 'o'}, '/agent', env)
        self.assertIn('&lt;script&gt;ZORVAL', html)
        self.assertNotIn('<script>ZORVAL', html)
        self.assertIn('aria-current="step"', html)
        for mode in cp.DEPLOIEMENTS.values():
            self.assertIn(mode['libelle'], html)
        self.assertIn('Non fourni par cette version', html)
        etat = cp.assistant(self.desk)
        for etape in ('identite', 'outils', 'modeles'):
            etat = cp.avancer(self.desk, ADMIN, 'administrateur', {'etape': etape, 'action': 'continuer', 'revision': etat['revision']})
            html = web.settings_html(self.desk, {'csrf': 'c', 'origin': 'o'}, '/agent', env)
            self.assertIn(cp.LIBELLES_ETAPES[etat['etape']], html)
        cp.lancer_demonstration(self.desk, ADMIN, 'administrateur', 'demo-cle-0002')
        html = web.settings_html(self.desk, {'csrf': 'c', 'origin': 'o'}, '/agent', env)
        self.assertIn('/api440/m5689/cabinet/demonstration/fichier?id=', html)
        self.assertIn('DONNÉES FICTIVES', html)

    def test_rubrique_de_parametres_et_controle_des_routes(self):
        from agent import parametres5621, standalone_auth
        self.assertEqual(parametres5621.rubrique_for('/parametres', {'rubrique': 'demarrage'}), 'demarrage')
        env = {'HTTP_X_AXIORHUB_USER': ADMIN, 'HTTP_X_AXIORHUB_ROLE': 'administrateur', 'QUERY_STRING': 'rubrique=demarrage'}
        html = parametres5621.page(self.desk, {'csrf': 'c', 'origin': 'o'}, '/agent', env, {'rubrique': 'demarrage'})
        html = html['body'] if isinstance(html, dict) else html
        self.assertIn('id="c5689"', html)
        for role, attendu in (('administrateur', True), ('avocat', True), ('assistant', False)):
            self.assertEqual(standalone_auth.allowed(role, 'POST', '/api440/m5689/connexions/cfg-ia/qualifier'), attendu, role)
        self.assertTrue(standalone_auth.allowed('assistant', 'GET', '/api440/m5689/cabinet/preparation'))
        self.assertTrue(standalone_auth.allowed('assistant', 'POST', '/api440/m5689/cabinet/demonstration'))
        self.assertFalse(standalone_auth.allowed('avocat', 'POST', '/api440/m5689/cabinet/inconnue'))
        self.assertEqual(membres5650.category('POST', '/api440/m5689/cabinet/assistant'), 'reglages')

    def test_ecran_sans_droit_explique(self):
        html = web.settings_html(self.desk, {'csrf': 'c', 'origin': 'o'}, '/agent', {'HTTP_X_AXIORHUB_USER': ASSISTANT, 'HTTP_X_AXIORHUB_ROLE': 'assistant'})
        self.assertIn('Réglages du cabinet', html)
        self.assertNotIn('name="nom_cabinet"', html)


# ------------------------------------------------------------------------------------------------ preuves de capacité
class Preuves(Base):
    def test_AV02_preuves_separees_avec_compte_capacite_date_version_expiration(self):
        conn = self.graph()
        self.brouillons_graph()
        out = self.qualif(conn, 'mail.create_draft', True)
        self.assertEqual(out['etat'], 'prouvee')
        self.assertEqual([p['nature'] for p in out['preuves']], ['authentification', 'creation_test', 'reouverture'])
        self.assertEqual(self.etat['destinataires'], [])                               # jamais de destinataire
        rows = self.desk.db.execute("SELECT * FROM capability_proofs5689 WHERE capability='mail.create_draft'").fetchall()
        self.assertEqual({r['kind'] for r in rows}, {'authentification', 'creation_test', 'reouverture'})
        for r in rows:
            self.assertEqual(r['account'], 'cabinet@example.test')
            self.assertEqual(r['adapter_version'], '1')
            self.assertGreater(r['expires_at'], r['checked_at'])
            self.assertEqual(r['result'], 'reussi')
        self.assertFalse([c for c in self.fake.calls if '/send' in c[1]])
        self.assertEqual(out['nettoyage']['etat'], 'nettoyee')
        self.assertTrue(out['nettoyage']['corbeille'])

    def test_AV02_lecture_reussie_ne_prouve_pas_l_ecriture(self):
        conn = self.graph(('mail.read', 'mail.create_draft'))
        self.assertEqual(self.qualif(conn, 'mail.read')['etat'], 'prouvee')
        ligne = cr.etat_capacite(self.desk, cr.connexion(self.desk, conn), 'mail.create_draft')
        self.assertEqual(ligne['etat'], 'ecriture_a_autoriser')                        # authentifié, écriture jamais essayée

    def test_AV02_reouverture_echouee_n_est_pas_une_reussite(self):
        conn = self.graph()
        self.brouillons_graph()
        self.fake.routes = [r for r in self.fake.routes if r[1] != '/me/messages/brouillon-1']
        self.fake.on('GET', '/me/messages/brouillon-1', {}, 404)
        out = self.qualif(conn, 'mail.create_draft', True)
        natures = {p['nature']: p['resultat'] for p in out['preuves']}
        self.assertEqual((natures['creation_test'], natures['reouverture']), ('reussi', 'echec'))
        self.assertEqual(out['etat'], 'refusee')
        self.assertEqual(self.etat['deplacements'], 0)

    def test_sans_autorisation_explicite_rien_n_est_cree(self):
        conn = self.graph()
        self.brouillons_graph()
        out = self.qualif(conn, 'mail.create_draft', False)
        self.assertEqual(self.etat['creations'], 0)
        self.assertEqual(out['etat'], 'ecriture_a_autoriser')

    def test_ecriture_desactivee_reste_a_autoriser(self):
        conn = self.graph(())
        self.brouillons_graph()
        out = self.qualif(conn, 'mail.create_draft', True)
        self.assertEqual((out['etat'], self.etat['creations']), ('ecriture_a_autoriser', 0))

    def test_jamais_d_envoi_ni_d_engagement_pour_tester(self):
        data = {'provider': 'youtrust', 'environment': 'test', 'api_key': SECRETS['api_key'], 'cap_signature_send_approved': True}
        conn = cx.save(self.desk, ADMIN, 'administrateur', data)['id']
        data = {'provider': 'gocardless', 'environment': 'test', 'access_token': SECRETS['access_token'], 'cap_payment_prepare_mandate': True}
        paiement = cx.save(self.desk, ADMIN, 'administrateur', data)['id']
        data = {'provider': 'pappers_justice', 'environment': 'test', 'api_key': SECRETS['api_key'], 'cap_external_legal_ai_ask_authorized': True}
        ia = cx.save(self.desk, ADMIN, 'administrateur', data)['id']
        for ident, cap in ((conn, 'signature.send_approved'), (paiement, 'payment.prepare_mandate'), (paiement, 'payment.submit_authorized'), (ia, 'external_legal_ai.ask_authorized')):
            status, out = self.api('connexions/%s/qualifier' % ident, {'capacite': cap, 'cle': self.cle(), 'autoriser_creation': True})
            self.assertEqual((status[:3], out['error']), ('403', 'test_interdit_envoi_ou_engagement'))
        self.assertEqual(self.fake.calls, [])
        ligne = cr.etat_capacite(self.desk, cr.connexion(self.desk, conn), 'signature.send_approved')
        self.assertEqual(ligne['etat'], 'ecriture_a_autoriser')

    def test_nettoyage_ne_touche_que_la_ressource_inchangee(self):
        conn = self.graph()
        self.brouillons_graph()

        def relecture_puis_modifie(method, url, data, headers):
            self.etat['lectures'] = self.etat.get('lectures', 0) + 1
            if self.etat['lectures'] > 1:
                self.etat['cle'] = 'ck-modifie-par-une-personne'
            return {'subject': self.etat['sujet'], 'isDraft': True, 'changeKey': self.etat['cle'], 'toRecipients': []}
        self.fake.on('GET', '/me/messages/brouillon-1', relecture_puis_modifie)
        out = self.qualif(conn, 'mail.create_draft', True)
        self.assertEqual(out['nettoyage']['etat'], 'modifiee_conservee')
        self.assertEqual(self.etat['deplacements'], 0)
        self.assertEqual(cr.essais(self.desk, conn)[0]['state'], 'modifiee_conservee')

    def test_fichier_d_essai_cycle_complet_a_la_corbeille(self):
        out = self.qualif('cfg-stockage', 'stockage.ecriture', True)
        self.assertEqual(out['etat'], 'prouvee')
        self.assertEqual(out['nettoyage']['etat'], 'nettoyee')
        self.assertFalse(list((self.root / 'Dossiers').glob('AxiorHub test de connexion*')))
        self.assertTrue((self.root / 'Dossiers' / 'DEMO' / 'Livrable.txt').is_file())

    def test_fichier_d_essai_modifie_par_une_personne_est_conserve(self):
        from agent.dav import LocalFolder
        with patch.object(LocalFolder, 'trash_file', side_effect=Stop('http_503')):
            out = self.qualif('cfg-stockage', 'stockage.ecriture', True)
        self.assertEqual(out['nettoyage']['etat'], 'a_nettoyer')
        fichier = next((self.root / 'Dossiers').glob('AxiorHub test de connexion*'))
        fichier.write_text('Note d’une personne', encoding='utf-8')
        out = cr.nettoyer(self.desk, ADMIN, 'administrateur', 'cfg-stockage', out['nettoyage']['test_id'])
        self.assertEqual(out['etat'], 'modifiee_conservee')
        self.assertEqual(fichier.read_text(encoding='utf-8'), 'Note d’une personne')

    def test_brouillon_gmail_sans_destinataire_retire_apres_relecture(self):
        view = cx.save(self.desk, ADMIN, 'administrateur', {'provider': 'google_workspace', 'environment': 'test', 'client_id': PARAMS['client_id'], 'client_secret': SECRETS['client_secret'], 'cap_mail_create_draft': True})
        cx._store_tokens(self.desk, view['id'], {'access_token': 'jeton-oauth-fictif', 'refresh_token': 'renouvellement-fictif', 'expires_in': 3600}, cx._row(self.desk, view['id'])['token_version'])
        etat = {'sujet': '', 'suppressions': []}
        self.fake.on('GET', 'openidconnect.googleapis.com', {'email': 'cabinet@example.test'})

        def creer(method, url, data, headers):
            brut = base64.urlsafe_b64decode(json.loads(data)['message']['raw'])
            message = email.message_from_bytes(brut)
            etat['sujet'], etat['a'] = message['Subject'], message['To']
            return {'id': 'dr-1'}
        self.fake.on('POST', '/users/me/drafts', creer)
        self.fake.on('GET', '/users/me/drafts/dr-1', lambda *a: {'message': {'snippet': 'Élément de test', 'payload': {'headers': [{'name': 'Subject', 'value': etat['sujet']}]}}})
        self.fake.on('DELETE', '/users/me/drafts/dr-1', lambda m, url, *a: etat['suppressions'].append(url) or {})
        out = self.qualif(view['id'], 'mail.create_draft', True)
        self.assertEqual(out['etat'], 'prouvee')
        self.assertFalse(etat['a'])
        self.assertEqual(len(etat['suppressions']), 1)
        self.assertFalse(out['nettoyage']['corbeille'])                                 # dit honnêtement : la portée « compose » n'ouvre pas la corbeille
        self.assertFalse([c for c in self.fake.calls if '/send' in c[1]])

    def test_boite_imap_brouillon_retrouve_relu_retire(self):
        etat = {'messages': {}, 'retraits': [], 'logout': 0}
        marque = {}

        class Boite:
            validity = '7'

            def __init__(self, cfg):
                self.cfg = cfg

            def close(self):
                etat['logout'] += 1

            def search(self, folder, *crit):
                if 'HEADER' in crit:
                    return ['41'] if etat['messages'] else []
                return ['1', '2', '3']

            def append_draft(self, message):
                etat['messages']['41'] = message
                marque['valeur'] = message['X-AxiorHub-Test']

            def fetch(self, folder, uid):
                return Mail(uid, '7', folder, {'\\Draft'}, datetime.now(timezone.utc), etat['messages'][uid])

        with patch('agent.mailbox.Mailbox', Boite), patch('agent.drafts440.discard_draft', lambda cfg, uid, validity, confirm: etat['retraits'].append((uid, validity, confirm)) or {'moved_to_trash': True}):
            self.assertEqual(self.qualif('cfg-messagerie', 'mail.read')['etat'], 'prouvee')
            out = self.qualif('cfg-messagerie', 'mail.create_draft', True)
        self.assertEqual(out['etat'], 'prouvee')
        self.assertEqual(etat['retraits'], [('41', '7', 'yes')])
        self.assertTrue(out['nettoyage']['corbeille'])
        self.assertIsNone(etat['messages']['41']['To'])

    def test_stockage_cloud_qualifie_par_son_client(self):
        from agent.dav import LocalFolder
        view = cx.save(self.desk, ADMIN, 'administrateur', {'provider': 'cloud_dropbox', 'environment': 'production', 'client_id': PARAMS['client_id'], 'cap_stockage_ecriture': True})
        cx._store_tokens(self.desk, view['id'], {'access_token': 'jeton-oauth-fictif', 'refresh_token': 'renouvellement-fictif', 'expires_in': 3600}, cx._row(self.desk, view['id'])['token_version'])
        client = LocalFolder(self.desk.c['nextcloud'])
        with patch('agent.cloud_gestion5688.client_for', return_value=client):
            self.assertEqual(self.qualif(view['id'], 'stockage.lecture')['etat'], 'prouvee')
            out = self.qualif(view['id'], 'stockage.ecriture', True)
        self.assertEqual((out['etat'], out['nettoyage']['etat']), ('prouvee', 'nettoyee'))
        self.desk.c['nextcloud']['stockage_cloud'] = {'provider': 'cloud_dropbox'}
        cr.revoquer(self.desk, ADMIN, 'administrateur', view['id'], 'stockage.ecriture', self.cle())
        with self.assertRaises(Stop) as ctx:
            cr.avant_effet(self.desk, 'stockage.ecriture')
        self.assertEqual(str(ctx.exception), 'capacite_revoquee')

    def test_source_juridique_declaree_reste_a_qualifier(self):
        with patch('agent.sources5632.enabled_items', return_value=[{'provider': 'fictif'}]):
            ligne = cr.etat_logique(self.desk, 'legal.search')
        self.assertEqual((ligne['etat'], ligne['code']), ('a_qualifier', 'source_declaree_non_qualifiee'))
        self.assertEqual(cr.etat_logique(self.desk, 'legal.search')['etat'], 'non_configuree')

    def test_idempotence_meme_cle_meme_resultat(self):
        conn = self.graph()
        self.brouillons_graph()
        cle = self.cle()
        premier = self.qualif(conn, 'mail.create_draft', True, cle=cle)
        second = self.qualif(conn, 'mail.create_draft', True, cle=cle)
        self.assertTrue(second['reprise'])
        self.assertEqual(self.etat['creations'], 1)
        self.assertEqual(second['etat'], premier['etat'])
        with self.assertRaises(Stop) as ctx:
            self.qualif('cfg-stockage', 'stockage.lecture', cle=cle)
        self.assertEqual(str(ctx.exception), 'cle_idempotence_deja_utilisee')
        with self.assertRaises(Stop):
            self.qualif(conn, 'mail.create_draft', True, cle='court')

    def test_ancienne_mention_connecte_reste_a_qualifier(self):
        conn = self.graph(('mail.read', 'mail.create_draft'))
        caps = json.loads(cx._row(self.desk, conn)['caps_json'])
        caps['mail.read']['verified_at'] = '2026-01-01T00:00:00+00:00'
        caps['mail.create_draft']['verified_write'] = '2026-01-01T00:00:00+00:00'
        self.desk.db.execute("UPDATE connexions5652 SET caps_json=?,last_test='2026-01-01T00:00:00+00:00',identity_json=? WHERE id=?",
                             (json.dumps(caps), json.dumps({'compte': 'cabinet@example.test'}), conn))
        self.desk.db.commit()
        row = cx._row(self.desk, conn)
        self.assertEqual(cx.state_of(self.desk, row), 'verifiee_ecriture')
        vue = cx.public_view(self.desk, row)
        self.assertEqual(vue['etat'], 'verifiee_ecriture')                             # la mention reste affichée telle quelle…
        self.assertEqual(vue['qualification']['mail.read']['etat'], 'a_qualifier')     # …mais ne vaut pas preuve
        self.assertEqual(vue['qualification']['mail.create_draft']['etat'], 'a_qualifier')
        self.assertNotEqual(cr.preparation(self.desk, ADMIN, 'administrateur', 'courriel')['etat'], 'operationnel')
        self.assertEqual(self.desk.db.execute('SELECT COUNT(*) FROM capability_proofs5689').fetchone()[0], 0)

    def test_capacite_declaree_n_est_pas_une_preuve(self):
        declarees = competences5632.available_capabilities(self.desk)
        self.assertIn('storage.read', declarees)
        prouvees = competences5632.available_capabilities(self.desk, prouvees=True)
        self.assertNotIn('storage.read', prouvees)
        self.assertNotIn('ai.generate', prouvees)
        self.assertIn('docx.generate', prouvees)                                       # local, sans connexion
        self.qualif('cfg-stockage', 'stockage.lecture')
        self.assertIn('storage.read', competences5632.available_capabilities(self.desk, prouvees=True))

    def test_test_du_registre_ne_prouve_que_l_authentification(self):
        conn = self.graph(('mail.read',))
        out = cx.test(self.desk, ADMIN, 'administrateur', conn)
        self.assertTrue(out['ok'])
        self.assertEqual(out['qualification']['mail.read']['etat'], 'a_qualifier')
        self.assertEqual(out['qualification']['mail.read']['preuves']['authentification']['resultat'], 'reussi')

    def test_preuve_expiree_ou_d_un_autre_compte_ou_adaptateur(self):
        self.qualif('cfg-stockage', 'stockage.lecture')
        conn = cr.connexion(self.desk, 'cfg-stockage')
        self.assertEqual(cr.etat_capacite(self.desk, conn, 'stockage.lecture')['etat'], 'prouvee')
        passe = (datetime.now(timezone.utc) - timedelta(days=1)).isoformat()
        self.desk.db.execute("UPDATE capability_proofs5689 SET expires_at=? WHERE kind='lecture'", (passe,))
        self.assertEqual(cr.etat_capacite(self.desk, conn, 'stockage.lecture')['etat'], 'expiree')
        self.desk.db.execute("UPDATE capability_proofs5689 SET expires_at='2999-01-01T00:00:00+00:00'")
        self.assertEqual(cr.etat_capacite(self.desk, {**conn, 'adapter_version': '2'}, 'stockage.lecture')['etat'], 'expiree')
        self.assertEqual(cr.etat_capacite(self.desk, {**conn, 'account': 'autre@example.test'}, 'stockage.lecture')['etat'], 'expiree')
        self.assertEqual(cr.etat_capacite(self.desk, {**conn, 'generation': 'autre'}, 'stockage.lecture')['etat'], 'a_qualifier')

    def test_changement_de_compte_perime_les_preuves(self):
        conn = self.graph(('mail.read',))
        self.assertEqual(self.qualif(conn, 'mail.read')['etat'], 'prouvee')
        cx.save(self.desk, ADMIN, 'administrateur', {'id': conn, 'provider': 'microsoft_graph', 'client_id': 'autre-application-fictive'})
        self.assertEqual(cr.etat_capacite(self.desk, cr.connexion(self.desk, conn), 'mail.read')['etat'], 'a_qualifier')

    def test_droits_de_qualification(self):
        status, out = self.api('connexions/cfg-stockage/qualifier', {'capacite': 'stockage.lecture', 'cle': self.cle()}, owner=AVOCAT, role='avocat')
        self.assertEqual(status[:3], '403')
        status, out = self.api('connexions/cfg-stockage/qualifier', {'capacite': 'stockage.lecture', 'cle': self.cle()}, owner=ASSISTANT, role='assistant')
        self.assertEqual(status[:3], '403')
        status, out = self.api('connexions/cfg-stockage/qualifier', {'capacite': 'stockage.lecture', 'cle': self.cle()})
        self.assertEqual((status[:3], out['etat']), ('200', 'prouvee'))
        status, out = self.api('connexions/absente/qualifier', {'capacite': 'x', 'cle': self.cle()})
        self.assertEqual(status[:3], '404')
        self.assertFalse(web.allowed('avocat', 'connexions/cfg-stockage/inconnue'))
        self.assertFalse(web.allowed('invite', 'cabinet/profil'))

    def test_csrf_exige(self):
        corps = b'{}'
        env = {'HTTP_ORIGIN': 'https://intrus.example.test', 'HTTP_X_CSRF_TOKEN': 'faux', 'CONTENT_TYPE': 'application/json', 'CONTENT_LENGTH': '2', 'wsgi.input': io.BytesIO(corps), 'HTTP_X_AXIORHUB_ROLE': 'administrateur',
               'HTTP_X_AXIORHUB_USER': ADMIN}
        out = web5689.api(env, self.desk, {'origin': 'https://axiorhub.example.test', 'csrf': 'jeton'}, '/agent', 'm5689/cabinet/profil', {}, 'POST')
        self.assertFalse(out['status'].startswith('200'))

    def test_preuves_sans_secret_et_erreur_expurgee(self):
        conn = self.graph(('mail.read',))
        self.fake.on('GET', '/me/messages?', {}, 403)
        out = self.qualif(conn, 'mail.read')
        self.assertEqual(out['etat'], 'refusee')
        self.assertEqual(out['preuves'][-1]['erreur'], 'http_403')
        brut = json.dumps([dict(r) for r in self.desk.db.execute('SELECT * FROM capability_proofs5689')], ensure_ascii=False)
        self.assertNotIn('jeton-oauth-fictif', brut)
        vue = json.dumps(cx.public_view(self.desk, cx._row(self.desk, conn)), ensure_ascii=False)
        self.assertNotIn('jeton-oauth-fictif', vue)

    def test_retention_des_preuves(self):
        for _ in range(cr.RETENUES + 3):
            self.qualif('cfg-stockage', 'stockage.lecture')
        n = self.desk.db.execute("SELECT COUNT(*) FROM capability_proofs5689 WHERE kind='lecture'").fetchone()[0]
        self.assertEqual(n, cr.RETENUES)


# ------------------------------------------------------------------------------------------------ IMP01B : lecture seule
class LectureSeule(Base):
    def test_IMP01B_lecture_disponible_redaction_bloquee_correction_precise(self):
        self.desk.c['mail']['enabled'] = False                                          # seule la boîte Microsoft de lecture entre en jeu
        conn = self.graph(('mail.read', 'mail.create_draft'))
        self.fake.on('POST', '/me/messages', {}, 403)
        self.ia_prouvee()
        self.assertEqual(self.qualif(conn, 'mail.read')['etat'], 'prouvee')
        out = self.qualif(conn, 'mail.create_draft', True)
        self.assertEqual(out['etat'], 'refusee')
        p = cr.preparation(self.desk, ADMIN, 'administrateur', 'courriel')
        self.assertEqual((p['etat'], p['etat_libelle']), ('lecture_disponible', 'Lecture disponible'))
        self.assertEqual(p['capacites_disponibles'], ['mail.read', 'ai.generate'])
        self.assertEqual([r['capacite'] for r in p['raisons']], ['mail.create_draft'])
        self.assertIn('cabinet@example.test', p['prochaine_action'])                    # correction précise : quel compte, quel droit
        self.assertIn('droit manquant', p['prochaine_action'])
        self.assertEqual(p['raisons'][0]['code'], 'http_403')
        etapes = {e['id']: e for e in p['etapes']}
        self.assertTrue(etapes['lire']['prete'] and etapes['rediger']['prete'])
        self.assertTrue(etapes['deposer']['suspendue'])
        lecture = cr.preparation(self.desk, ADMIN, 'administrateur', 'agenda')
        self.assertNotEqual(lecture['etat'], 'operationnel')                            # une autre capacité non prouvée n'est pas devinée
        self.assertEqual(cx._connections_for(self.desk, 'mail.create_draft', ADMIN), [])
        self.assertEqual([r['id'] for r in cx._connections_for(self.desk, 'mail.read', ADMIN)], [conn])

    def test_IMP01B_mission_de_lecture_operationnelle_redaction_non(self):
        from agent.dav import LocalFolder
        self.ia_prouvee()
        self.qualif('cfg-stockage', 'stockage.lecture')
        with patch.object(LocalFolder, 'put_file', side_effect=Stop('http_403')):
            out = self.qualif('cfg-stockage', 'stockage.ecriture', True)
        self.assertEqual(out['etat'], 'refusee')
        self.assertEqual(cr.preparation(self.desk, ADMIN, 'administrateur', 'lecture')['etat'], 'operationnel')
        redaction = cr.preparation(self.desk, ADMIN, 'administrateur', 'redaction')
        self.assertEqual(redaction['etat'], 'lecture_disponible')
        with self.assertRaises(Stop) as ctx:
            dav_client(self.desk).put_file('/Dossiers/DEMO/Projet.txt', b'x', 'text/plain')    # bloqué avant effet
        self.assertEqual(str(ctx.exception), 'ecriture_refusee_par_le_compte')
        self.assertFalse((self.root / 'Dossiers' / 'DEMO' / 'Projet.txt').exists())

    def test_operationnel_exige_toutes_les_preuves(self):
        self.ia_prouvee()
        self.qualif('cfg-stockage', 'stockage.lecture')
        p = cr.preparation(self.desk, ADMIN, 'administrateur', 'redaction')
        self.assertEqual((p['etat'], p['etat_libelle']), ('ecriture_a_autoriser', 'Écriture à autoriser'))
        self.qualif('cfg-stockage', 'stockage.ecriture', True)
        p = cr.preparation(self.desk, ADMIN, 'administrateur', 'redaction')
        self.assertEqual((p['etat'], p['raisons']), ('operationnel', []))

    def test_droits_du_compte_bloquent_operationnel(self):
        self.desk.c['mail']['enabled'] = False
        conn = self.graph(('mail.read', 'mail.create_draft'))
        self.brouillons_graph()
        self.ia_prouvee()
        self.qualif(conn, 'mail.read')
        self.qualif(conn, 'mail.create_draft', True)
        self.assertEqual(cr.preparation(self.desk, ADMIN, 'administrateur', 'courriel')['etat'], 'operationnel')
        p = cr.preparation(self.desk, ASSISTANT, 'assistant', 'courriel')              # l'assistant n'a pas le droit de déposer
        self.assertEqual(p['raisons'][0]['etat'], 'droits_insuffisants')
        self.assertNotEqual(p['etat'], 'operationnel')

    def test_connexion_personnelle_d_un_autre_ne_sert_pas(self):
        self.desk.c['mail']['enabled'] = False
        conn = self.graph(('mail.read',))
        self.desk.db.execute('UPDATE connexions5652 SET owner=? WHERE id=?', ('personnel@example.test', conn))
        self.desk.db.commit()
        self.assertEqual(cr.etat_logique(self.desk, 'mail.read', AVOCAT, 'avocat')['etat'], 'non_configuree')
        self.assertEqual(cr.preparation(self.desk, AVOCAT, 'avocat', 'agenda')['mission'], 'agenda')

    def test_politique_refuse_ou_illisible_ne_laisse_pas_operationnel(self):
        self.qualif('cfg-stockage', 'stockage.lecture')
        conn = cr.connexion(self.desk, 'cfg-stockage')
        with patch('agent.data_policy5689.evaluer', return_value={'allowed': False, 'denial_reasons': ['mode_local_strict']}):
            ligne = cr.etat_capacite(self.desk, conn, 'stockage.lecture')
            self.assertEqual((ligne['etat'], ligne['code']), ('politique_refuse', 'mode_local_strict'))
            with self.assertRaises(Stop) as ctx:
                self.qualif('cfg-stockage', 'stockage.lecture')
            self.assertEqual(str(ctx.exception), 'politique_refuse')
        self.assertEqual(cr.etat_capacite(self.desk, conn, 'stockage.lecture')['etat'], 'prouvee')

    def test_politique_illisible_bloque_sans_ouvrir(self):
        self.qualif('cfg-stockage', 'stockage.lecture')
        self.desk.setting('politique5689', {'revision': 'illisible'})
        ligne = cr.etat_capacite(self.desk, cr.connexion(self.desk, 'cfg-stockage'), 'stockage.lecture')
        self.assertEqual((ligne['etat'], ligne['code']), ('politique_refuse', 'politique_illisible'))
        self.assertEqual(cr.preparation(self.desk, ADMIN, 'administrateur', 'lecture')['etat'], 'bloquee')
        self.assertEqual(cp.lire(self.desk)['policy_ref'], 'politique:illisible')

    def test_categorie_fermee_par_le_cabinet_refuse_la_capacite(self):
        self.qualif('cfg-stockage', 'stockage.lecture')
        self.desk.setting('ia_vue5688', {'documents': False})
        ligne = cr.etat_capacite(self.desk, cr.connexion(self.desk, 'cfg-stockage'), 'stockage.lecture')
        self.assertEqual((ligne['etat'], ligne['code']), ('politique_refuse', 'categorie_fermee'))

    def test_preparation_route_et_mission_inconnue(self):
        status, out = self.api('cabinet/preparation', method='GET', args={'mission': 'lecture'}, owner=ASSISTANT, role='assistant')
        self.assertEqual((status[:3], out['mission']), ('200', 'lecture'))
        self.assertTrue(all(k in out for k in ('capacites_requises', 'capacites_disponibles', 'raisons', 'prochaine_action')))
        status, out = self.api('cabinet/preparation', method='GET', args={'mission': 'inventee'})
        self.assertEqual((status[:3], out['error']), ('400', 'mission_inconnue'))


# ------------------------------------------------------------------------------------------------ IMP01C : révocation
class Revocation(Base):
    def operation(self):
        """Opération de signature préparée sur une capacité prouvée à la main par une vérification ancienne, comme en production."""
        (self.root / 'Dossiers' / 'DEMO' / 'Convention.pdf').write_bytes(b'%PDF-1.4 convention fictive')
        view = cx.save(self.desk, ADMIN, 'administrateur', {'provider': 'youtrust', 'environment': 'test', 'api_key': SECRETS['api_key'], 'cap_signature_send_approved': True})
        self.fake.on('GET', '/signature_requests?limit=1', {'data': []})
        cx.test(self.desk, ADMIN, 'administrateur', view['id'])
        self.fake.on('POST', '/v3/signature_requests', {'id': 'sr-1', 'status': 'draft'})
        self.fake.on('POST', '/signature_requests/sr-1/documents', {'id': 'doc-1'})
        self.fake.on('POST', '/signature_requests/sr-1/signers', {'id': 'sg-1'})
        self.fake.on('POST', '/signature_requests/sr-1/activate', {'status': 'ongoing'})
        op = cx.prepare_from_form(self.desk, ADMIN, 'administrateur', {'outil': 'signature.preparer', 'matter': 'DOS-001', 'document': '/Dossiers/DEMO/Convention.pdf', 'title': 'Convention fictive',
                                                                     'first_name': 'Alice', 'last_name': 'Fictive', 'email': 'alice@example.test'})
        caps = json.loads(cx._row(self.desk, view['id'])['caps_json'])
        caps['signature.send_approved']['verified_write'] = '2026-01-01T00:00:00+00:00'  # ancienne preuve en cache
        self.desk.db.execute('UPDATE connexions5652 SET caps_json=? WHERE id=?', (json.dumps(caps), view['id']))
        self.desk.db.commit()
        return view['id'], op

    def test_IMP01C_capacite_revoquee_bloque_avant_effet_malgre_le_cache(self):
        conn, op = self.operation()
        avant = len([c for c in self.fake.calls if c[0] == 'POST'])
        out = cr.revoquer(self.desk, ADMIN, 'administrateur', conn, 'signature.send_approved', self.cle())
        self.assertEqual(out['etat'], 'revoquee')
        self.assertEqual(out['preuve']['resultat'], 'reussi')
        self.assertEqual(cr.etat_capacite(self.desk, cr.connexion(self.desk, conn), 'signature.send_approved')['etat'], 'revoquee')
        ligne = self.desk.db.execute('SELECT * FROM connexion_ops5652 WHERE id=?', (op['id'],)).fetchone()
        with self.assertRaises(Stop) as ctx:
            cx.validate(self.desk, ADMIN, 'administrateur', op['id'], ligne['version'])
        self.assertEqual(str(ctx.exception), 'capacite_revoquee')
        self.assertEqual(len([c for c in self.fake.calls if c[0] == 'POST']), avant)       # aucun effet
        self.assertEqual(self.desk.db.execute('SELECT COUNT(*) FROM connexion_ops5652 WHERE id=?', (op['id'],)).fetchone()[0], 1)   # l'opération reste, suspendue

    def test_IMP01C_outil_de_l_agent_ne_voit_plus_la_capacite(self):
        self.desk.c['mail']['enabled'] = False
        conn = self.graph(('mail.read', 'contacts.read'))
        self.fake.on('GET', '/me/contacts?', {'value': []})
        cx.test(self.desk, ADMIN, 'administrateur', conn)
        self.assertEqual(cx.call_tool(self.desk, ADMIN, 'administrateur', 'courriel.lire', {})['outil'], 'courriel.lire')
        cr.revoquer(self.desk, ADMIN, 'administrateur', conn, 'mail.read', self.cle())
        caps = json.loads(cx._row(self.desk, conn)['caps_json'])
        caps['mail.read'].update(enabled=True, verified_at='2026-01-01T00:00:00+00:00')  # cache en retard : la révocation fait foi, pas cette mention
        self.desk.db.execute('UPDATE connexions5652 SET caps_json=? WHERE id=?', (json.dumps(caps), conn))
        self.desk.db.commit()
        with self.assertRaises(Stop) as ctx:
            cx.call_tool(self.desk, ADMIN, 'administrateur', 'courriel.lire', {})
        self.assertEqual(str(ctx.exception), 'aucune_connexion_verifiee_pour_cet_outil')
        self.assertEqual(cx.call_tool(self.desk, ADMIN, 'administrateur', 'contacts.lire', {})['outil'], 'contacts.lire')   # les autres capacités continuent

    def test_IMP01C_seules_les_etapes_dependantes_sont_suspendues_sans_rien_effacer(self):
        self.ia_prouvee()
        self.qualif('cfg-stockage', 'stockage.lecture')
        self.qualif('cfg-stockage', 'stockage.ecriture', True)
        self.assertEqual(cr.preparation(self.desk, ADMIN, 'administrateur', 'redaction')['etat'], 'operationnel')
        cr.revoquer(self.desk, ADMIN, 'administrateur', 'cfg-stockage', 'stockage.ecriture', self.cle())
        p = cr.preparation(self.desk, ADMIN, 'administrateur', 'redaction')
        etapes = {e['id']: e for e in p['etapes']}
        self.assertEqual((etapes['lire']['suspendue'], etapes['rediger']['suspendue'], etapes['deposer']['suspendue']), (False, False, True))
        self.assertEqual(p['etat'], 'lecture_disponible')
        client = _dav(self.desk)
        with self.assertRaises(Stop) as ctx:
            client.put_file('/Dossiers/DEMO/Projet.txt', b'x', 'text/plain')
        self.assertEqual(str(ctx.exception), 'capacite_revoquee')
        with self.assertRaises(Stop):
            dav_client(self.desk).trash_file('/Dossiers/DEMO/Livrable.txt', 'x')
        self.assertEqual((self.root / 'Dossiers' / 'DEMO' / 'Livrable.txt').read_text(encoding='utf-8'), 'Projet fictif')   # livrable intact, encore lisible
        self.assertTrue(any('DEMO' in str(i.get('path')) for i in client.list_folder('/Dossiers')))

    def test_IMP01C_mission_dependante_arretee_mission_independante_continue(self):
        missions567.ensure_schema(self.desk)
        for ident, genre in (('m-doc', 'document'), ('m-question', 'question')):
            self.desk.db.execute("INSERT INTO missions_v567(id,owner,request_key,fingerprint,instruction,matter,kind,state,context,plan,exceptions,created,updated) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?)",
                                 (ident, 'cabinet', ident, 'f', 'Fictif', 'DOS-001', genre, 'running', '{}', '{}', '[]', '2026-11-02', '2026-11-02'))
        self.desk.db.commit()
        missions567.check_authority(self.desk, {'mission_id': 'm-doc'})
        cr.revoquer(self.desk, ADMIN, 'administrateur', 'cfg-stockage', 'stockage.ecriture', self.cle())
        with self.assertRaises(Stop) as ctx:
            missions567.check_authority(self.desk, {'mission_id': 'm-doc'})
        self.assertEqual(str(ctx.exception), 'capacite_revoquee')
        missions567.check_authority(self.desk, {'mission_id': 'm-question'})

    def test_IMP01C_depot_imap_bloque_quel_que_soit_le_circuit(self):
        cr.ensure_schema(self.desk)
        self.desk.db.commit()
        from agent.sens5643 import appliquer_identites
        cp.lire(self.desk)
        appliquer_identites(self.desk)

        class Conn:
            appends = 0

            def append(self, *a):
                Conn.appends += 1
                return 'OK', []
        boite = Mailbox.__new__(Mailbox)
        boite.cfg, boite.conn = self.desk.c['mail'], Conn()
        boite.append_draft(EmailMessage(policy=__import__('email').policy.SMTP))
        self.assertEqual(Conn.appends, 1)
        cr.revoquer(self.desk, ADMIN, 'administrateur', 'cfg-messagerie', 'mail.create_draft', self.cle())
        ancienne = dict(self.desk.c['mail'])                                            # configuration en mémoire d'un travail déjà lancé
        boite.cfg = ancienne
        with self.assertRaises(Stop) as ctx:
            boite.append_draft(EmailMessage(policy=__import__('email').policy.SMTP))
        self.assertEqual(str(ctx.exception), 'capacite_revoquee')
        self.assertEqual(Conn.appends, 1)

    def test_IMP01C_revocation_totale_par_le_circuit_existant(self):
        conn = self.graph(('mail.read',))
        self.qualif(conn, 'mail.read')
        out = cr.revoquer(self.desk, ADMIN, 'administrateur', conn, '', self.cle())
        self.assertEqual(out['preuve']['resultat'], 'reussi')
        row = cx._row(self.desk, conn)
        self.assertEqual(row['disconnected'], 1)
        self.assertFalse(cx._vault(self.desk, conn, 'access').is_file())                # jetons supprimés du coffre
        self.assertEqual(cx._connections_for(self.desk, 'mail.read', ADMIN), [])
        self.assertNotEqual(cr.etat_capacite(self.desk, cr.connexion(self.desk, conn), 'mail.read')['etat'], 'prouvee')

    def test_IMP01C_refus_du_service_en_usage_reel_fait_tomber_la_preuve(self):
        self.desk.c['mail']['enabled'] = False
        conn = self.graph(('mail.read',))
        self.qualif(conn, 'mail.read')
        self.assertEqual(cr.etat_capacite(self.desk, cr.connexion(self.desk, conn), 'mail.read')['etat'], 'prouvee')
        self.fake.on('GET', '/me/messages?', {}, 401)
        with self.assertRaises(Stop):
            cx.call_tool(self.desk, ADMIN, 'administrateur', 'courriel.lire', {})
        self.assertEqual(cr.etat_capacite(self.desk, cr.connexion(self.desk, conn), 'mail.read')['etat'], 'refusee')

    def test_retablir_n_efface_pas_la_revocation_de_la_preuve(self):
        self.qualif('cfg-stockage', 'stockage.lecture')
        cr.revoquer(self.desk, ADMIN, 'administrateur', 'cfg-stockage', 'stockage.lecture', self.cle())
        with self.assertRaises(Stop) as ctx:
            self.qualif('cfg-stockage', 'stockage.lecture')
        self.assertEqual(str(ctx.exception), 'capacite_revoquee')
        cr.reautoriser(self.desk, ADMIN, 'administrateur', 'cfg-stockage', 'stockage.lecture', self.cle())
        self.assertEqual(cr.etat_capacite(self.desk, cr.connexion(self.desk, 'cfg-stockage'), 'stockage.lecture')['etat'], 'a_qualifier')   # l'ancienne preuve ne revient pas
        self.assertEqual(self.qualif('cfg-stockage', 'stockage.lecture')['etat'], 'prouvee')

    def test_enregistrer_le_formulaire_ne_leve_pas_la_revocation(self):
        conn = self.graph(('mail.read',))
        cr.revoquer(self.desk, ADMIN, 'administrateur', conn, 'mail.read', self.cle())
        vue = cx.save(self.desk, ADMIN, 'administrateur', {'id': conn, 'provider': 'microsoft_graph', 'cap_mail_read': True})
        self.assertFalse(vue['capacites']['mail.read']['enabled'])

    def test_competence_perd_la_capacite_revoquee(self):
        self.assertIn('storage.read', competences5632.available_capabilities(self.desk))
        cr.revoquer(self.desk, ADMIN, 'administrateur', 'cfg-stockage', 'stockage.lecture', self.cle())
        self.assertNotIn('storage.read', competences5632.available_capabilities(self.desk))

    def test_droits_de_revocation(self):
        status, out = self.api('connexions/cfg-stockage/revoquer', {'capacite': 'stockage.lecture', 'cle': self.cle()}, owner=AVOCAT, role='avocat')
        self.assertEqual(status[:3], '403')
        self.assertFalse(cr.revoquees(self.desk, 'cfg-stockage'))


# ------------------------------------------------------------------------------------------------ IMP01D : adresses propres
class Identite(Base):
    def setUp(self):
        super().setUp()
        cp.enregistrer_alias(self.desk, ADMIN, 'administrateur', 'accueil+dossiers@example.test', 'confirmee', role_personne='boite_commune')
        self.cfg = self.desk.c['mail']

    def test_IMP01D_envoye_et_copie_a_soi_exclus_de_tous_les_circuits(self):
        envoye = courriel(de='Maître <accueil+dossiers@example.test>', a='client@example.test')
        soi = courriel(de='accueil+dossiers@example.test', a='cabinet@example.test')
        self.assertEqual((sens5643.classer(envoye, self.cfg), sens5643.classer(soi, self.cfg)), ('envoye_cabinet', 'copie_soi'))
        for message in (envoye, soi):
            self.assertEqual(exclusion(message, self.cfg, allow_seen=True), 'message_du_cabinet')       # surveillance
            self.assertNotEqual(sens5643.direction(message, self.cfg), 'received')                     # réponses
        with self.assertRaises(Stop) as ctx:                                            # brouillons, tous circuits confondus
            make_draft(courriel(), self.cfg, 'Bonjour', 'a' * 64, to='accueil+dossiers@example.test')
        self.assertEqual(str(ctx.exception), 'reponse_a_soi_meme_refusee')
        self.assertIn('accueil+dossiers@example.test', own_addresses(self.cfg))         # rattachement, contacts, tableaux : même registre

    def test_IMP01D_tiers_du_meme_domaine_n_est_pas_exclu(self):
        for adresse in ('confrere@example.test', 'accueil@example.test', 'accueil+autre@example.test', 'accueil.dossiers@example.test', 'accueil+dossiers@sous.example.test'):
            tiers = courriel(de=adresse, a='cabinet@example.test')
            self.assertEqual(sens5643.classer(tiers, self.cfg), 'tiers', adresse)
            self.assertEqual(exclusion(tiers, self.cfg, allow_seen=True), '', adresse)
            self.assertNotIn(adresse, own_addresses(self.cfg))
            self.assertEqual(sens5643.check_recipients(adresse, '', self.cfg), [adresse])

    def test_IMP01D_identite_ambigue(self):
        self.assertEqual(sens5643.classer(courriel(de='accueil+dossiers@example.test, intrus@example.test'), self.cfg), 'ambigu')
        self.assertEqual(sens5643.classer(courriel(de='cabinet@example.test', a='client@example.test', repondre='intrus@example.test'), self.cfg), 'ambigu')
        self.assertEqual(sens5643.classer(courriel(de='cabinet@example.test', a='client@example.test', repondre='accueil+dossiers@example.test'), self.cfg), 'envoye_cabinet')
        self.assertEqual(sens5643.classer(courriel(de='client@example.test', repondre='autre@example.test'), self.cfg), 'tiers')

    def test_IMP01D_journal_de_temps_et_memoire_lisent_le_meme_registre(self):
        from agent.engine import known_correspondent
        from agent.memory import scope_detail
        from agent.time500 import _is_sent
        self.assertTrue(_is_sent(self.desk, {'sender': 'accueil+dossiers@example.test', 'folder': 'INBOX'}))
        self.assertFalse(_is_sent(self.desk, {'sender': 'accueil@example.test', 'folder': 'INBOX'}))
        own = own_addresses(self.cfg)
        self.assertEqual(scope_detail(courriel(de='accueil+dossiers@example.test', a='cabinet@example.test'), [], own)[2], 'destinataires_absents_ou_trop_nombreux')   # copie à soi : pas d'exemple de style
        self.assertEqual(scope_detail(courriel(de='accueil@example.test', a='cabinet@example.test'), [], own)[2], 'expediteur_non_reconnu')
        self.assertTrue(known_correspondent(self.desk.c, 'accueil+dossiers@example.test', []))     # rattachement
        self.assertFalse(known_correspondent(self.desk.c, 'confrere@example.test', []))

    def test_AV01_rapprochement_errone_corrigeable(self):
        cp.lire(self.desk)
        cp.enregistrer_alias(self.desk, ADMIN, 'administrateur', 'erreur@example.test', 'confirmee')
        self.assertEqual(exclusion(courriel(de='erreur@example.test'), self.cfg, allow_seen=True), 'message_du_cabinet')
        cp.enregistrer_alias(self.desk, ADMIN, 'administrateur', 'erreur@example.test', 'retiree')
        self.assertEqual(exclusion(courriel(de='erreur@example.test'), self.cfg, allow_seen=True), '')

    def test_AV01_retrait_d_une_adresse_reprise_de_la_configuration(self):
        self.desk.c['mail']['own_addresses'] = ['cabinet@example.test', 'ancienne@example.test']
        self.desk.db.execute('DELETE FROM identity_aliases5689')
        self.desk.db.execute('DELETE FROM cabinet_profile5689')
        self.desk.db.execute('DELETE FROM cabinet_profile_versions5689')
        self.desk.db.commit()
        self.assertTrue(cp.importer_existant(self.desk))
        self.assertIn('ancienne@example.test', own_addresses(self.cfg))
        cp.enregistrer_alias(self.desk, ADMIN, 'administrateur', 'ancienne@example.test', 'retiree')
        self.assertNotIn('ancienne@example.test', own_addresses(self.cfg))
        self.assertEqual(sens5643.classer(courriel(de='ancienne@example.test'), self.cfg), 'tiers')
        self.assertEqual(self.desk.c['mail']['own_addresses'], ['cabinet@example.test', 'ancienne@example.test'])    # fichier de configuration intact

    def test_adresse_d_expedition_non_retirable(self):
        with self.assertRaises(Stop) as ctx:
            cp.enregistrer_alias(self.desk, ADMIN, 'administrateur', 'cabinet@example.test', 'retiree')
        self.assertEqual(str(ctx.exception), 'adresse_expedition_non_retirable')

    def test_ni_domaine_ni_joker(self):
        for valeur in ('@example.test', '*@example.test', 'example.test', '', 'a@b'):
            with self.assertRaises(Stop) as ctx:
                cp.enregistrer_alias(self.desk, ADMIN, 'administrateur', valeur, 'confirmee')
            self.assertEqual(str(ctx.exception), 'adresse_complete_requise', valeur)

    def test_aucune_normalisation_de_suffixe_ni_de_point(self):
        out = cp.enregistrer_alias(self.desk, ADMIN, 'administrateur', ' Prenom.Nom+Essai@Example.TEST ', 'confirmee')
        self.assertEqual(out['adresse'], 'prenom.nom+essai@example.test')
        self.assertNotIn('prenomnom+essai@example.test', own_addresses(self.cfg))
        self.assertNotIn('prenom.nom@example.test', own_addresses(self.cfg))

    def test_par_compte_et_role_distinct_de_l_adresse(self):
        conn = self.graph(())
        cp.enregistrer_alias(self.desk, ADMIN, 'administrateur', 'partage@example.test', 'confirmee', compte=conn, role_personne='collaborateur')
        cp.enregistrer_alias(self.desk, ADMIN, 'administrateur', 'partage@example.test', 'confirmee', compte='messagerie', role_personne='avocat')
        lignes = [a for a in cp.aliases(self.desk) if a['adresse'] == 'partage@example.test']
        self.assertEqual({(a['compte'], a['role_personne']) for a in lignes}, {(conn, 'collaborateur'), ('messagerie', 'avocat')})
        cp.enregistrer_alias(self.desk, ADMIN, 'administrateur', 'partage@example.test', 'retiree', compte=conn)
        self.assertIn('partage@example.test', own_addresses(self.cfg))                  # encore confirmée sur l'autre compte
        with self.assertRaises(Stop):
            cp.enregistrer_alias(self.desk, ADMIN, 'administrateur', 'autre@example.test', 'confirmee', compte='connexion-inconnue')

    def test_registre_charge_avec_la_configuration(self):
        c = {'state_dir': self.f.c['state_dir'], 'mail': {'own_addresses': ['cabinet@example.test']}}
        sens5643.load_config_identites(c)
        self.assertIn('accueil+dossiers@example.test', own_addresses(c['mail']))
        vide = {'state_dir': str(self.f.base / 'absent'), 'mail': {}}
        sens5643.load_config_identites(vide)
        self.assertEqual(vide['mail'], {})

    def test_alias_sans_droit_403(self):
        status, out = self.api('cabinet/alias', {'adresse': 'x@example.test'}, owner=ASSISTANT, role='assistant')
        self.assertEqual(status[:3], '403')
        self.assertNotIn('x@example.test', own_addresses(self.cfg))


# ------------------------------------------------------------------------------------------------ démonstration
class Demonstration(Base):
    def setUp(self):
        super().setUp()
        self.desk.c['mail'].update(enabled=False)
        self.desk.c['nextcloud'] = {'roots': ['/Dossiers']}
        self.desk.c['ollama'] = {'model': 'test-local'}

    def test_AV01_mission_fictive_sans_compte_ni_prompt_ni_vrai_dossier(self):
        p = cr.preparation(self.desk, ASSISTANT, 'assistant', 'demonstration')
        self.assertEqual((p['etat'], p['fictive'], p['capacites_requises']), ('operationnel', True, []))
        avant = Path(self.f.c['matters_file']).read_bytes()
        emplois = self.desk.db.execute('SELECT COUNT(*) FROM jobs').fetchone()[0]
        with patch.object(DAV, '__new__', side_effect=AssertionError('aucun accès aux documents')), patch('agent.model.Model', side_effect=AssertionError('aucun modèle')), \
                patch('agent.common.load_matters', side_effect=AssertionError('aucun dossier réel')):
            out = cp.lancer_demonstration(self.desk, ASSISTANT, 'assistant', 'demo-cle-0001', 'fr')
        self.assertTrue(out['fictive'])
        self.assertIn('DÉMONSTRATION', out['marque'])
        self.assertIn('fictif', out['dossier'].lower())
        self.assertFalse(out['modele_ia_appele'] or out['compte_utilise'] or out['dossier_reel_touche'])
        self.assertEqual(Path(self.f.c['matters_file']).read_bytes(), avant)
        self.assertEqual(self.desk.db.execute('SELECT COUNT(*) FROM jobs').fetchone()[0], emplois)
        self.assertEqual({e['etat'] for e in out['etapes']}, {'terminee'})

    def test_modele_localise_et_document_marque_fictif(self):
        for lang, marque in (('fr', 'FICTIVES'), ('en', 'FICTITIOUS'), ('de', 'FIKTIVE'), ('es', 'FICTICIOS')):
            out = cp.lancer_demonstration(self.desk, ADMIN, 'administrateur', 'demo-langue-' + lang, lang)
            self.assertEqual(out['modele']['langue'], lang)
            raw, nom = cp.demonstration_docx(self.desk, ADMIN, out['id'])
            self.assertTrue(nom.startswith('demonstration-fictive-'))
            xml = zipfile.ZipFile(io.BytesIO(raw)).read('word/document.xml').decode('utf-8')
            self.assertEqual(xml.count(marque), 2, lang)                                # en tête et à la fin

    def test_idempotence_et_cloisonnement_par_compte(self):
        premier = cp.lancer_demonstration(self.desk, ADMIN, 'administrateur', 'demo-cle-0003')
        second = cp.lancer_demonstration(self.desk, ADMIN, 'administrateur', 'demo-cle-0003')
        self.assertEqual(premier['id'], second['id'])
        self.assertEqual(self.desk.db.execute('SELECT COUNT(*) FROM demo_missions5689').fetchone()[0], 1)
        with self.assertRaises(Stop) as ctx:
            cp.demonstration(self.desk, AVOCAT, premier['id'])
        self.assertEqual(str(ctx.exception), 'demonstration_absente')

    def test_route_et_retention(self):
        for i in range(12):
            status, out = self.api('cabinet/demonstration', {'cle': 'demo-cle-%04d' % i, 'langue': 'es'}, owner=ASSISTANT, role='assistant')
            self.assertEqual(status[:3], '200')
        self.assertEqual(self.desk.db.execute('SELECT COUNT(*) FROM demo_missions5689').fetchone()[0], 10)
        status, raw = self.api('cabinet/demonstration/fichier', method='GET', args={'id': out['id']}, owner=ASSISTANT, role='assistant')
        self.assertTrue(raw.startswith(b'PK'))
        self.assertIsNone(membres5650.category('POST', '/api440/m5689/cabinet/demonstration'))
        self.assertEqual(membres5650.category('POST', '/api440/m5689/cabinet/profil'), 'reglages')


# ------------------------------------------------------------------------------------------------ mutations en mémoire : chaque garde-fou est utile
class Mutations(Base):
    def test_sans_la_garde_du_registre_l_operation_partirait(self):
        r = Revocation('test_IMP01C_capacite_revoquee_bloque_avant_effet_malgre_le_cache')
        r.desk, r.root, r.fake, r.n, r.f = self.desk, self.root, self.fake, 0, self.f
        conn, op = r.operation()
        cr.revoquer(self.desk, ADMIN, 'administrateur', conn, 'signature.send_approved', self.cle())
        caps = json.loads(cx._row(self.desk, conn)['caps_json'])
        caps['signature.send_approved']['enabled'] = True                               # un cache en retard dit encore « activée »
        self.desk.db.execute('UPDATE connexions5652 SET caps_json=? WHERE id=?', (json.dumps(caps), conn))
        self.desk.db.commit()
        ligne = self.desk.db.execute('SELECT * FROM connexion_ops5652 WHERE id=?', (op['id'],)).fetchone()
        self.desk.db.execute("UPDATE connexion_ops5652 SET state='a_valider' WHERE id=?", (op['id'],))
        self.desk.db.commit()
        with self.assertRaises(Stop) as ctx:
            cx.validate(self.desk, ADMIN, 'administrateur', op['id'], ligne['version'])
        self.assertEqual(str(ctx.exception), 'capacite_revoquee')
        with patch.object(cr, 'avant_effet_connexion', lambda *a: None):
            out = cx.validate(self.desk, ADMIN, 'administrateur', op['id'], ligne['version'])
        self.assertEqual(out['state'], 'envoyee')                                       # garde retirée : l'effet a lieu, donc la garde compte

    def test_sans_la_garde_de_domaine_le_tiers_serait_ecarte(self):
        tiers = courriel(de='confrere@example.test')
        self.assertEqual(exclusion(tiers, self.desk.c['mail'], allow_seen=True), '')
        with patch('agent.mailbox.own_addresses', lambda cfg: {'confrere@example.test'}):
            self.assertEqual(exclusion(tiers, self.desk.c['mail'], allow_seen=True), 'message_du_cabinet')

    def test_sans_comparaison_d_empreinte_la_ressource_modifiee_serait_retiree(self):
        conn = self.graph()
        self.brouillons_graph()
        original = cr.SondeRegistre.rouvrir
        appels = []

        def rouvrir(sonde, cap, ref, marque):
            appels.append(ref)
            out = original(sonde, cap, ref, marque)
            if len(appels) > 1:
                out['empreinte'] = 'autre'
            return out
        with patch.object(cr.SondeRegistre, 'rouvrir', rouvrir):
            out = self.qualif(conn, 'mail.create_draft', True)
        self.assertEqual((out['nettoyage']['etat'], self.etat['deplacements']), ('modifiee_conservee', 0))

    def test_sans_le_refus_d_interdiction_la_sonde_de_lecture_seule_ne_passe_pas(self):
        self.assertNotIn('signature.send_approved', cr.SONDES_ECRITURE)
        self.assertNotIn('registered_mail.send_approved', cr.LECTURES)
        for spec in cx.PROVIDERS.values():
            for cap, (_label, nature, _droit) in spec['caps'].items():
                if nature == 'ecriture':
                    self.assertNotEqual(cap, 'mail.create_draft')
                    self.assertTrue(cap not in cr.SONDES_ECRITURE or cap == 'stockage.ecriture', cap)


if __name__ == '__main__':
    unittest.main()
