"""5.6.109, lot S (G18, G19, G21) : identités des registres étrangers, documents publics des sociétés étrangères, accès dans Paramètres.

Services réels (``connexions5652`` et son coffre, ``connector_readiness5689``, ``international56109``, ``crm5636``, registre des documents
``registre_docs5688``) ; transports HTTP et stockage du dossier remplacés par des FAUX (aucun appel réel, aucun compte réel). Données
fictives : ZORVAL, KERVILAC, DOS-001, clés « cle-fictive-… », numéros de registre fictifs ou publics neutres.
"""
import base64
import io
import json
from pathlib import Path
import socket
import unittest
from unittest.mock import patch

from agent import connexions5652 as cx, connector_readiness5689 as cr, international56109 as I, membres5650
from agent.common import Stop, private_json
from agent.desk import Desk
import test_agent as fx

PREFIX = '/agent-courriel'
ADMIN = ('cabinet', 'administrateur')
DURAND = ('durand@example.test', 'avocat')
MARTIN = ('martin@example.test', 'avocat')
ASSISTANT = ('bernard@example.test', 'assistant')
CLE_CH = 'cle-fictive-ch-0001'
CLE_CANLII = 'cle-fictive-canlii-0001'
RESERVE = 'DOS-RES'


def _refuse_reseau(*a, **k):
    raise AssertionError('aucun appel réseau réel dans ces essais')


class FauxHTTP:
    """Faux services étrangers : Companies House (Public Data et Document API), SEC, CanLII, OpenCorporates. Appels notés (sans corps)."""

    def __init__(self):
        self.appels, self.total, self.barcodes, self.contenus, self.erreurs = [], 150, {}, {}, {}
        self.cle = CLE_CH

    def __call__(self, origin):
        return self

    def request(self, method, url, data=None, headers=None, limit=0):
        headers = headers or {}
        self.appels.append((method, url, headers.get('Authorization', ''), headers.get('X-API-TOKEN', ''), headers.get('User-Agent', '')))
        for motif, err in list(self.erreurs.items()):
            if motif in url:
                raise err
        if 'company-information.service.gov.uk' in url:
            if headers.get('Authorization') != 'Basic ' + base64.b64encode((self.cle + ':').encode()).decode():
                raise Stop('http_401')
            if '/search/companies' in url:
                return json.dumps({'items': [{'company_number': '00000006', 'title': 'ZORVAL LIMITED', 'company_status': 'active'}]}).encode()
            if url.endswith('/company/00000006'):
                return json.dumps({'company_number': '00000006', 'company_name': 'ZORVAL LIMITED', 'company_status': 'active', 'jurisdiction': 'england-wales'}).encode()
            if '/filing-history' in url:
                debut = int(url.split('start_index=')[1].split('&')[0])
                items = [{'transaction_id': 'TX%04d' % i, 'date': '2026-%02d-01' % (1 + i % 12), 'type': 'AA', 'category': 'accounts',
                          'description': 'Comptes fictifs %d' % i, 'barcode': self.barcodes.get(i, 'B%04d' % i),
                          'links': {'document_metadata': 'https://frontend-doc-api.company-information.service.gov.uk/document/DOC%04d' % i}}
                         for i in range(debut, min(debut + 100, self.total))]
                return json.dumps({'total_count': self.total, 'items': items}).encode()
            if '/document/' in url and not url.endswith('/content'):
                ident = url.rsplit('/', 1)[1]
                return json.dumps({'resources': {'application/pdf': {}}, 'links': {'document': 'https://document-api.company-information.service.gov.uk/document/%s/content' % ident}}).encode()
            if url.endswith('/content'):
                ident = url.split('/document/')[1].split('/')[0]
                return self.contenus.get(ident, b'%PDF-1.4 contenu fictif ' + ident.encode())
        if 'data.sec.gov' in url:
            return json.dumps({'cik': '320193', 'name': 'KERVILAC INC (fictif)', 'filings': {'recent': {
                'accessionNumber': ['0000320193-26-000001'], 'primaryDocument': ['fictif-10k.htm'], 'filingDate': ['2026-10-01'], 'form': ['10-K']}}}).encode()
        if 'www.sec.gov/Archives' in url:
            return b'<html><body>Rapport annuel fictif</body></html>'
        if 'api.canlii.org' in url:
            if 'api_key=' + CLE_CANLII not in url:
                raise Stop('http_403')
            return json.dumps({'caseDatabases': [{'databaseId': 'csc-scc'}], 'legislationDatabases': [{'databaseId': 'cas'}],
                               'databaseId': 'csc-scc', 'caseId': {'fr': '2008csc9'}, 'title': 'Décision fictive'}).encode()
        if 'opencorporates' in url:
            return json.dumps({'results': {'companies': [{'company': {'company_number': '00000006', 'jurisdiction_code': 'gb', 'name': 'ZORVAL LIMITED'}}]}}).encode()
        raise Stop('http_404')


class FauxStockage:
    def __init__(self):
        self.fichiers, self.dossiers, self.erreur = {}, [], None

    def ensure_folder(self, path, boundary):
        self.dossiers.append((path, boundary))
        return path

    def put_archive(self, path, data, limit, kind='application/octet-stream'):
        if self.erreur:
            err, self.erreur = self.erreur, None
            raise err
        if path in self.fichiers:
            raise Stop('http_412')
        self.fichiers[path] = data
        return path


class Base(unittest.TestCase):
    def setUp(self):
        self.f = fx.EngineTests('test_observation_has_no_mail_write')
        self.f.setUp()
        self.addCleanup(self.f.doCleanups)
        for cible, nom in ((socket.socket, 'connect'), (socket, 'create_connection'), (socket, 'getaddrinfo')):
            p = patch.object(cible, nom, _refuse_reseau)
            p.start()
            self.addCleanup(p.stop)
        matters = list(self.f.matters) + [{'id': RESERVE, 'client_name': 'Client ZORVAL', 'path': '/Dossiers/RES', 'references': [RESERVE],
                                           'correspondents': [{'email': 'zorval@example.test', 'role': 'client'}]}]
        private_json(self.f.matter_file, matters)
        membres5650.set_reserve(self.f.c['state_dir'], RESERVE, ['martin@example.test'])
        self.d = Desk(self.f.c)
        self.addCleanup(self.d.db.close)
        self.http = FauxHTTP()
        self.stock = FauxStockage()
        for p in (patch.object(cx, 'HTTP_FACTORY', self.http), patch('agent.document_projects._dav', lambda desk: self.stock)):
            p.start()
            self.addCleanup(p.stop)
        self.auth = {'origin': 'https://cabinet.example.test', 'csrf': 'test-csrf', 'prefix': PREFIX}
        self.n = 0

    def cle(self):
        self.n += 1
        return 'cle-s-essai-%04d' % self.n

    def connexion(self, provider='uk_companies_house', **champs):
        data = {'provider': provider, 'portee': 'cabinet', **champs}
        if provider == 'uk_companies_house' and 'api_key' not in champs:
            data['api_key'] = CLE_CH
        return cx.save(self.d, 'cabinet', 'administrateur', data)

    def qualifier(self, conn_id, *caps):
        return [cr.qualifier(self.d, 'cabinet', 'administrateur', conn_id, c, self.cle())['etat'] for c in caps]

    def ch_pret(self):
        vue = self.connexion()
        self.assertEqual(self.qualifier(vue['id'], 'societe.rechercher', 'documents.lister', 'documents.telecharger'), ['prouvee'] * 3)
        return vue

    def societe_uk(self, role='partie_adverse', matter='DOS-001'):
        return I.ajouter_societe(self.d, DURAND, matter, 'ZORVAL LIMITED', 'GB', 'CompaniesHouse', '00000006', role)

    def api(self, route, data=None, acteur=DURAND, methode='POST', args=None):
        raw = json.dumps(data or {}).encode()
        env = {'REQUEST_METHOD': methode, 'HTTP_ORIGIN': self.auth['origin'], 'HTTP_X_CSRF_TOKEN': 'test-csrf', 'CONTENT_TYPE': 'application/json',
               'CONTENT_LENGTH': str(len(raw)), 'wsgi.input': io.BytesIO(raw), 'HTTP_X_AXIORHUB_ROLE': acteur[1], 'HTTP_X_AXIORHUB_USER': acteur[0]}
        out = I.api(env, self.d, self.auth, PREFIX, I.PREFIXE + route, args or {}, methode)
        out['json'] = json.loads(out['body'])
        return out


# ================================================================================ G21 : accès étrangers dans Paramètres et l'installation
class Acces(Base):
    def test_G21_liens_officiels_et_bons_champs_par_fournisseur(self):
        cat = I.catalogue()
        for sid in cx.API_INTERNATIONALES:
            spec = cx.PROVIDERS_INTERNATIONAUX[sid]
            self.assertEqual(spec['access'], cat[sid]['acces_url'])
            self.assertTrue(spec['access'].startswith('https://'))
            secrets = [f['name'] for f in spec['fields'] if f['secret']]
            self.assertEqual(secrets, [] if sid == 'us_sec' else ['api_key'], sid)
        self.assertIn('HTTP Basic', cx.PROVIDERS_INTERNATIONAUX['uk_companies_house']['auth'])
        self.assertIn('X-API-TOKEN', cx.PROVIDERS_INTERNATIONAUX['opencorporates']['auth'])
        self.assertEqual([f['name'] for f in cx.PROVIDERS_INTERNATIONAUX['us_sec']['fields']], ['contact'])
        self.assertNotIn('decisions.lire', cx.PROVIDERS_INTERNATIONAUX['ca_canlii']['caps'])

    def test_G21_une_seule_connexion_par_fournisseur_et_compte(self):
        a = self.connexion(environment='test')
        b = self.connexion(environment='production', id=a['id'], version=a['version'])
        c = cx.save(self.d, 'cabinet', 'administrateur', {'provider': 'uk_companies_house', 'portee': 'cabinet', 'environment': 'test'})
        self.assertEqual(len({a['id'], b['id'], c['id']}), 1)
        self.assertEqual(self.d.db.execute("SELECT COUNT(*) FROM connexions5652 WHERE provider='uk_companies_house'").fetchone()[0], 1)

    def test_G21_secret_au_coffre_jamais_rendu_ni_exporte(self):
        vue = self.connexion()
        self.assertNotIn(CLE_CH, json.dumps(vue))
        html = cx.settings_html(self.d, PREFIX, 'cabinet', 'administrateur', {'famille': 'international'})
        self.assertNotIn(CLE_CH, html)
        fichier = cx._vault(self.d, vue['id'], 'api_key')
        self.assertTrue(fichier.read_text(encoding='utf-8').startswith('AXIORHUB-VAULT-V1:'))
        self.assertNotIn(CLE_CH, fichier.read_text(encoding='utf-8'))
        from agent import export5688 as X
        self.assertTrue(X.motif_exclusion('connexions5652'), 'les connexions (secrets, comptes) ne sont jamais exportées')

    def test_G21_cle_vide_conserve_et_choix_desactives_conserves(self):
        vue = self.connexion(cap_documents_telecharger='')
        self.assertFalse(vue['capacites']['documents.telecharger']['enabled'])
        vue = cx.save(self.d, 'cabinet', 'administrateur', {'provider': 'uk_companies_house', 'id': vue['id'], 'version': vue['version'], 'api_key': ''})
        self.assertFalse(vue['capacites']['documents.telecharger']['enabled'], 'choix désactivé conservé')
        self.assertIn('api_key', vue['secrets_enregistres'], 'clé vide : clé enregistrée conservée')

    def test_G21_revision_de_la_cle_invalide_la_preuve(self):
        vue = self.ch_pret()
        avant = I.preuve_courante(self.d, 'uk_companies_house')
        self.assertTrue(avant.valide)
        self.assertIn('documents.telecharger', avant.capacites)
        self.assertEqual(I.preparer('uk_companies_house', 'documents.lister', territoire='GB', preuve=avant, reseau_autorise=True)['etat'], 'plan_pret')
        self.http.cle = 'cle-fictive-ch-0002'
        cx.save(self.d, 'cabinet', 'administrateur', {'provider': 'uk_companies_house', 'id': vue['id'], 'version': cx._row(self.d, vue['id'])['version'],
                                                       'api_key': 'cle-fictive-ch-0002'})
        apres = I.preuve_courante(self.d, 'uk_companies_house')
        self.assertFalse(apres.valide)
        self.assertNotEqual(apres.revision_connexion, avant.revision_connexion)
        self.assertEqual(I.preparer('uk_companies_house', 'documents.lister', territoire='GB', preuve=apres, reseau_autorise=True)['etat'], 'capacite_a_qualifier')

    def test_G21_canlii_metadonnees_ne_prouvent_pas_le_texte_integral(self):
        vue = self.connexion('ca_canlii', api_key=CLE_CANLII)
        self.assertEqual(self.qualifier(vue['id'], 'decisions.metadonnees'), ['prouvee'])
        preuve = I.preuve_courante(self.d, 'ca_canlii')
        self.assertIn('decisions.metadonnees', preuve.capacites)
        self.assertNotIn('decisions.lire', preuve.capacites)
        with self.assertRaisesRegex(Stop, 'capacite_non_declaree'):
            I.preparer('ca_canlii', 'decisions.lire', territoire='CA', preuve=preuve, reseau_autorise=True)
        with self.assertRaisesRegex(Stop, 'capacite_non_codee'):
            cx.adapter(self.d, cx._row(self.d, vue['id'])).call('decisions.lire', {'id': '1'})

    def test_G21_canlii_cle_jamais_dans_une_erreur(self):
        vue = self.connexion('ca_canlii', api_key=CLE_CANLII)
        self.http.erreurs['caseBrowse'] = Stop('http_403:GET https://api.canlii.org/v1/caseBrowse/fr/?api_key=' + CLE_CANLII)
        with self.assertRaises(Stop) as ctx:
            cx.adapter(self.d, cx._row(self.d, vue['id'])).identity()
        self.assertEqual(str(ctx.exception), 'http_403')
        out = cx.test(self.d, 'cabinet', 'administrateur', vue['id'])
        self.assertNotIn(CLE_CANLII, json.dumps(out))
        self.assertNotIn(CLE_CANLII, json.dumps([dict(r) for r in self.d.db.execute('SELECT * FROM audit')], default=str))

    def test_G21_companies_house_cle_seulement_vers_les_hotes_declares(self):
        vue = self.connexion()
        ad = cx.adapter(self.d, cx._row(self.d, vue['id']))
        ad.identity()
        self.assertTrue(self.http.appels[-1][2].startswith('Basic '))
        n = len(self.http.appels)
        with self.assertRaisesRegex(Stop, 'adresse_document_refusee'):
            ad.call('documents.telecharger', {'metadonnees': 'https://hote-piege.example.test/document/X'})
        self.assertEqual(len(self.http.appels), n, 'aucune requête (donc aucune clé) vers un hôte non déclaré')

    def test_G21_aucune_cle_factice_pour_un_service_sans_cle(self):
        html = cx.settings_html(self.d, PREFIX, 'cabinet', 'administrateur', {'famille': 'international'})
        carte = html.split('id="cx52-us_sec"')[1].split('</article>')[0]
        self.assertNotIn('type="password"', carte)
        self.assertIn('name="contact"', carte)
        etrangers = html.split('cx52-etrangers')[1]
        self.assertIn('CENDOJ jurisprudence', etrangers)
        self.assertIn('SOQUIJ', etrangers)
        self.assertNotIn('type="password"', etrangers)
        self.assertNotIn('Connecté', etrangers)

    def test_G21_filtre_pays(self):
        html = cx.settings_html(self.d, PREFIX, 'cabinet', 'administrateur', {'famille': 'international', 'pays': 'GB'})
        self.assertIn('id="cx52-uk_companies_house"', html)
        self.assertNotIn('id="cx52-ca_canlii"', html)
        self.assertNotIn('id="cx52-us_sec"', html)

    def test_G21_installation_meme_service_d_enregistrement(self):
        from agent import sources_publiques56109 as SP
        html = SP.installation_html(self.d, 'jeton-csrf')
        self.assertIn('acces56109_uk_companies_house', html)
        self.assertIn('Obtenir mes accès', html)
        message = SP.installation_action(self.d, 'cabinet', {'op': 'acces56109_uk_companies_house', 'api_key': CLE_CH})
        self.assertIn('non vérifié', message)
        self.assertEqual(self.d.db.execute("SELECT COUNT(*) FROM connexions5652 WHERE provider='uk_companies_house'").fetchone()[0], 1)
        SP.installation_action(self.d, 'cabinet', {'op': 'acces56109_uk_companies_house', 'api_key': ''})
        self.assertEqual(self.d.db.execute("SELECT COUNT(*) FROM connexions5652 WHERE provider='uk_companies_house'").fetchone()[0], 1)
        self.assertNotIn(CLE_CH, SP.installation_html(self.d, 'jeton-csrf'))
        with self.assertRaisesRegex(Stop, 'fournisseur_inconnu'):
            SP.installation_action(self.d, 'cabinet', {'op': 'acces56109_ar24'})

    def test_G21_sec_user_agent_et_aucune_cle(self):
        vue = self.connexion('us_sec', contact='Cabinet DURAND contact@example.test')
        self.assertTrue(cx.test(self.d, 'cabinet', 'administrateur', vue['id'])['ok'])
        self.assertEqual(self.qualifier(vue['id'], 'documents.lister'), ['prouvee'])
        self.assertIn('contact@example.test', self.http.appels[-1][4])
        self.assertEqual(self.http.appels[-1][2], '')


# ================================================================================ G18 : identités et profils internationaux
class Identites(Base):
    def test_G18_societe_uk_dans_un_dossier_francais(self):
        from agent.common import load_matters
        avant = [m for m in load_matters(self.d.c) if m['id'] == 'DOS-001']
        p = self.societe_uk()
        self.assertEqual((p['pays'], p['autorite'], p['numero'], p['service']), ('GB', 'CompaniesHouse', '00000006', 'uk_companies_house'))
        self.assertEqual(p['dossiers'], [{'dossier': 'DOS-001', 'role': 'partie_adverse'}])
        self.assertEqual([m for m in load_matters(self.d.c) if m['id'] == 'DOS-001'], avant, 'le droit et le tribunal du dossier ne changent pas')
        with self.assertRaises(Stop):
            I.preparer('uk_companies_house', 'documents.lister', territoire='FR', preuve=None, reseau_autorise=True)

    def test_G18_numeros_textuels_zeros_et_autorites_distinctes(self):
        with self.assertRaisesRegex(Stop, 'company_number_a_verifier'):
            I.valider_identite('GB', 'CompaniesHouse', '6')
        a = I.ajouter_societe(self.d, DURAND, 'DOS-001', 'ZORVAL QC', 'CA-QC', 'REQ', '1100000001', 'client')
        b = I.ajouter_societe(self.d, DURAND, 'DOS-001', 'ZORVAL CANADA', 'CA', 'CorporationsCanada', '1100000001', 'client')
        self.assertNotEqual(a['contact'], b['contact'])
        self.assertEqual((a['service'], b['service']), ('qc_req', 'ca_corporations'))
        c = I.ajouter_societe(self.d, DURAND, 'DOS-001', 'KERVILAC GmbH', 'DE', 'Berlin HRB', '123', 'partie_adverse')
        e = I.ajouter_societe(self.d, DURAND, 'DOS-001', 'KERVILAC GmbH', 'DE', 'Hamburg HRB', '123', 'partie_adverse')
        self.assertNotEqual(c['contact'], e['contact'])
        with self.assertRaisesRegex(Stop, 'tribunal_et_section_requis'):
            I.valider_identite('DE', 'Handelsregister', '123')
        with self.assertRaisesRegex(Stop, 'identite_societe_a_preciser'):
            I.valider_identite('GB', 'CompaniesHouse', 6)

    def test_G18_pas_de_fusion_par_nom(self):
        a = self.societe_uk()
        b = I.ajouter_societe(self.d, DURAND, 'DOS-001', 'ZORVAL LIMITED', 'GB', 'CompaniesHouse', '00000007', 'partie_adverse')
        self.assertNotEqual(a['contact'], b['contact'], 'même nom, autre numéro : deux sociétés')
        with self.assertRaisesRegex(Stop, 'identite_deja_attribuee'):
            I.identifier(self.d, 'durand@example.test', b['contact'], 'GB', 'CompaniesHouse', '00000006')
        c = I.ajouter_societe(self.d, DURAND, 'DOS-001', 'Nom différent', 'GB', 'CompaniesHouse', '00000006', 'partie_adverse')
        self.assertEqual(c['contact'], a['contact'], 'même identité de registre : même fiche, retrouvée par le registre seul')

    def test_G18_mutation_sans_index_unique_deux_fiches_porteraient_le_meme_numero(self):
        a = self.societe_uk()
        b = I.ajouter_societe(self.d, DURAND, 'DOS-001', 'AUTRE', 'GB', 'CompaniesHouse', '00000007', 'partie_adverse')
        self.d.db.execute('DROP INDEX contacts5636_registre56109')
        try:
            self.d.db.execute('UPDATE contacts5636 SET registre_cle=? WHERE id=?', (I.cle_societe('GB', 'CompaniesHouse', '00000006'), b['contact']))
            self.assertEqual(self.d.db.execute('SELECT COUNT(*) FROM contacts5636 WHERE registre_cle=?', (I.cle_societe('GB', 'CompaniesHouse', '00000006'),)).fetchone()[0], 2)
        finally:
            self.d.db.rollback()
            I.ensure_schema(self.d)
        import sqlite3
        with self.assertRaises(sqlite3.IntegrityError):
            self.d.db.execute('UPDATE contacts5636 SET registre_cle=? WHERE id=?', (I.cle_societe('GB', 'CompaniesHouse', '00000006'), b['contact']))
        self.d.db.rollback()

    def test_G18_preuves_versionnees_et_exportables_sans_secret(self):
        vue = self.ch_pret()
        from agent import export5688 as X
        self.assertIn('capability_proofs5689', {t[2] for t in X.tables_exportees()})
        lignes = [dict(r) for r in self.d.db.execute('SELECT * FROM capability_proofs5689 WHERE connection=?', (vue['id'],))]
        self.assertTrue(lignes)
        self.assertTrue(all(r['generation'] for r in lignes))
        self.assertNotIn(CLE_CH, json.dumps(lignes))

    def test_G18_route_404_neutre_dossier_reserve_ou_absent(self):
        absent = self.api('collecter', {'d': 'DOS-INEXISTANT', 'contact': 'x', 'cle': self.cle()})
        reserve = self.api('collecter', {'d': RESERVE, 'contact': 'x', 'cle': self.cle()})
        self.assertEqual((absent['status'], reserve['status']), ('404 Not Found', '404 Not Found'))
        self.assertEqual(absent['json'], reserve['json'])
        self.assertEqual(self.api('collecter', {'d': 'DOS-001', 'contact': 'x', 'cle': self.cle()}, acteur=ASSISTANT)['status'], '403 Forbidden')

    def test_G18_identifier_par_la_route_avec_cle_d_idempotence(self):
        p = self.societe_uk()
        cle = self.cle()
        out = self.api('identifier', {'contact': p['contact'], 'pays': 'GB', 'autorite': 'CompaniesHouse', 'numero': '00000006', 'cle': cle})
        self.assertEqual(out['status'], '200 OK')
        self.assertIn(('Cache-Control', 'private, no-store'), out['headers'])
        self.assertTrue(self.api('identifier', {'contact': p['contact'], 'pays': 'GB', 'autorite': 'CompaniesHouse', 'numero': '00000006', 'cle': cle})['json']['rejoue'])
        self.assertEqual(self.api('identifier', {'contact': p['contact'], 'pays': 'GB', 'autorite': 'CompaniesHouse', 'numero': '00000008', 'cle': cle})['status'], '409 Conflict')

    def test_G18_fiche_de_contact_montre_le_registre(self):
        from agent import crm5636
        p = self.societe_uk()
        html = crm5636.page_html(self.d, PREFIX, {'id': p['contact']})
        self.assertIn('Registre étranger et documents publics', html)
        self.assertIn('00000006', html)
        self.assertIn('Collecter les documents publics', html)
        self.assertIn('n’est pas une pièce qu’elle a communiquée', html)


# ================================================================================ G19 : documents publics classés sans doublon
class Documents(Base):
    def setUp(self):
        super().setUp()
        self.ch_pret()
        self.p = self.societe_uk()

    def collecter(self, cle=None, acteur=DURAND, maximum=5):
        return I.collecter(self.d, acteur, 'DOS-001', self.p['contact'], cle or self.cle(), maximum)

    def lignes(self):
        return I.documents(self.d, 'DOS-001')

    def test_G19_classement_societe_fournisseur_reference_revision_empreinte(self):
        n = len(self.http.appels)                                  # les sondes de qualification ont déjà lu une page chacune
        out = self.collecter()
        self.assertEqual(out['etat'], 'terminee')
        self.assertEqual(len(out['classes']), 5)
        self.assertTrue(out['pagination_complete'])
        pages = [a for a in self.http.appels[n:] if '/filing-history' in a[1]]
        self.assertEqual(len(pages), 2, 'historique de 150 dépôts : deux pages de 100 lues')
        dossier = '/Dossiers/DEMO/Documents publics des sociétés/ZORVAL LIMITED (GB 00000006)'
        self.assertTrue(all(c['chemin'].startswith(dossier + '/') for c in out['classes']))
        for r in self.lignes():
            self.assertEqual((r['service'], r['societe_cle'], r['nature_document'], r['etat'], r['piece_communiquee']),
                             ('uk_companies_house', self.p['cle'], 'ordinaire', 'classe', 0))
            self.assertTrue(r['reference'] and r['revision'] and len(r['sha256']) == 64)
            self.assertEqual((r['devise'], r['cout'], r['credits']), ('GBP', '0', ''))
            self.assertEqual(r['format'], 'application/pdf')

    def test_G19_aucun_doublon_rejeu_et_nouvelle_collecte(self):
        cle = self.cle()
        self.collecter(cle)
        fichiers = dict(self.stock.fichiers)
        self.assertTrue(self.collecter(cle)['rejoue'])
        out = self.collecter()
        self.assertEqual((len(out['classes']), out['deja_la']), (0, 5))
        self.assertEqual(self.stock.fichiers, fichiers)
        self.assertEqual(len(self.lignes()), 5)

    def test_G19_nouvelle_version_ajoutee_ancienne_conservee(self):
        self.collecter()
        self.http.barcodes[0] = 'B0000-V2'
        self.http.contenus['DOC0000'] = b'%PDF-1.4 version corrigee fictive'
        out = self.collecter()
        self.assertEqual(len(out['classes']), 1)
        revisions = sorted(r['revision'] for r in self.lignes() if r['reference'] == 'TX0000')
        self.assertEqual(revisions, ['B0000', 'B0000-V2'])
        self.assertEqual(len(self.stock.fichiers), 6, 'l’original reste')

    def test_G19_meme_binaire_autre_reference_jamais_archive_deux_fois(self):
        self.http.contenus['DOC0001'] = self.http.contenus['DOC0000'] = b'%PDF-1.4 meme contenu fictif'
        out = self.collecter(maximum=2)
        self.assertEqual((len(out['classes']), out['deja_la']), (1, 1))
        self.assertEqual(len(self.stock.fichiers), 1)
        self.assertEqual(sorted(r['etat'] for r in self.lignes()), ['classe', 'deja_classe'])

    def test_G19_page_de_connexion_html_jamais_rangee(self):
        self.http.contenus['DOC0000'] = b'<!DOCTYPE html><html><body>Sign in</body></html>'
        out = self.collecter(maximum=1)
        self.assertEqual(out['erreurs'][0]['code'], 'document_html_recu')
        self.assertEqual(self.stock.fichiers, {})
        self.assertEqual(out['etat'], 'partielle')

    def test_G19_quota_429_arret_sans_insister(self):
        self.http.erreurs['/document/DOC0001'] = Stop('http_429')
        out = self.collecter(maximum=5)
        self.assertEqual(out['etat'], 'quota_atteint')
        self.assertEqual(len(out['classes']), 1)
        self.assertFalse([a for a in self.http.appels if 'DOC0002' in a[1]], 'aucun appel après le quota')

    def test_G19_resultat_incertain_a_verifier_jamais_rejoue(self):
        self.stock.erreur = Stop('delai_http_depasse')
        out = self.collecter(maximum=1)
        self.assertEqual(out['a_verifier'][0]['code'], 'delai_http_depasse')
        self.assertEqual(self.lignes()[0]['etat'], 'a_verifier')
        out = self.collecter(maximum=1)
        self.assertEqual(len(out['a_verifier']), 1)
        self.assertEqual(self.stock.fichiers, {}, 'aucun second dépôt à l’aveugle')

    def test_G19_copie_certifiee_distincte_de_la_copie_ordinaire(self):
        self.collecter(maximum=1)
        ordinaire = self.lignes()[0]
        ident = I.enregistrer_document(self.d, 'DOS-001', self.p['cle'], 'uk_companies_house', ordinaire['reference'], ordinaire['revision'], 'certifiee',
                                       'b' * 64, '/Dossiers/DEMO/Documents publics des sociétés/certifiee.pdf', 'GBP', '15.00', '')
        self.assertNotEqual(ident, ordinaire['inpi_id'])
        natures = sorted(r['nature_document'] for r in self.lignes())
        self.assertEqual(natures, ['certifiee', 'ordinaire'])
        self.assertEqual([r['cout'] for r in self.lignes() if r['nature_document'] == 'certifiee'], ['15.00'])

    def test_G19_document_public_adverse_jamais_piece_communiquee(self):
        self.collecter(maximum=2)
        self.assertTrue(all(r['role_societe'] == 'partie_adverse' and r['piece_communiquee'] == 0 for r in self.lignes()))
        self.assertFalse([p for p in self.stock.fichiers if 'Pièces' in p or 'adverse' in p.lower()])

    def test_G19_devises_separees_sec_en_usd_format_d_origine(self):
        vue = self.connexion('us_sec', contact='Cabinet DURAND contact@example.test')
        cx.test(self.d, 'cabinet', 'administrateur', vue['id'])
        self.assertEqual(self.qualifier(vue['id'], 'documents.lister', 'documents.telecharger'), ['prouvee', 'prouvee'])
        p = I.ajouter_societe(self.d, DURAND, 'DOS-001', 'KERVILAC INC', 'US', 'SEC', '320193', 'client')
        out = I.collecter(self.d, DURAND, 'DOS-001', p['contact'], self.cle(), 3)
        self.assertEqual(out['devise'], 'USD')
        ligne = [r for r in self.lignes() if r['service'] == 'us_sec'][0]
        self.assertEqual((ligne['devise'], ligne['format']), ('USD', 'text/html'))
        self.assertTrue(ligne['chemin'].endswith('.html'))

    def test_G19_pause_et_local_strict_avant_tout_appel(self):
        n = len(self.http.appels)
        self.d.setting('missions567:pause', True)
        self.assertEqual(self.collecter()['etat'], 'suspendue')
        self.d.setting('missions567:pause', False)
        with patch('agent.selection_sources5697.local_strict', lambda desk: True):
            self.assertEqual(self.collecter()['etat'], 'reseau_non_autorise')
        self.assertEqual(len(self.http.appels), n)
        self.assertEqual(self.stock.fichiers, {})

    def test_G19_registre_non_raccorde_reste_explique(self):
        p = I.ajouter_societe(self.d, DURAND, 'DOS-001', 'KERVILAC GmbH', 'DE', 'Berlin HRB', '123', 'partie_adverse')
        with self.assertRaisesRegex(Stop, 'service_non_raccorde'):
            I.collecter(self.d, DURAND, 'DOS-001', p['contact'], self.cle())

    def test_G19_dossier_reserve_meme_404_avant_tout_appel(self):
        n = len(self.http.appels)
        out = self.api('collecter', {'d': RESERVE, 'contact': self.p['contact'], 'cle': self.cle()})
        self.assertEqual(out['status'], '404 Not Found')
        self.assertEqual(len(self.http.appels), n)
        ok = self.api('collecter', {'d': RESERVE, 'contact': self.p['contact'], 'cle': self.cle()}, acteur=MARTIN)
        self.assertEqual(ok['status'], '404 Not Found', 'société non liée à ce dossier : même réponse neutre')


if __name__ == '__main__':
    unittest.main()
