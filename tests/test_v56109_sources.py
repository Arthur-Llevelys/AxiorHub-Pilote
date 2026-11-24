"""5.6.109, lot S (F11) : sources publiques sans clé préconfigurées, activées dès la première ouverture puis vérifiées automatiquement.

Services réels (``sources5632``, ``sources_publiques56109``, registre ``extensions364``, ``web5632``) ; le transport MCP est un FAUX serveur
(fonction d'appel injectée) : aucun réseau, aucun compte, aucun service réel. Données fictives (DURAND, ZORVAL, DOS-001, example.test).
"""
import io
import json
import socket
import unittest
from unittest.mock import patch

from agent import common, extensions364 as ext, mcp5614, mcp_internationaux5697 as mi
from agent import sources5632 as S, sources_publiques56109 as SP, web5632
from agent.common import Stop
from agent.desk import Desk
import test_agent as fx

PREFIX = '/agent-courriel'
ADMIN = ('cabinet', 'administrateur')
DURAND = ('durand@example.test', 'avocat')
TEXTE_OFFICIEL = ('Found 2 dataset(s) for query: DVF\n\n1. Demandes de valeurs foncières (jeu fictif)\n   ID: 0000fictif0001\n'
                  '   Organization: Direction fictive\n   URL: https://www.data.gouv.fr/datasets/fictif-1\n\n'
                  '2. Valeurs foncières agrégées (jeu fictif)\n   ID: 0000fictif0002\n   Organization: Producteur fictif\n'
                  '   URL: https://www.data.gouv.fr/datasets/fictif-2\n')
OUTILS = ('search_datasets', 'get_dataset_info', 'list_dataset_resources', 'get_resource_info', 'query_resource_data')


def _refuse_reseau(*a, **k):
    raise AssertionError('aucun appel réseau réel dans ces essais')


class FauxMCP:
    """Faux serveur data.gouv.fr : initialize, tools/list, tools/call ; réponses configurables, appels notés."""

    def __init__(self, texte=TEXTE_OFFICIEL, outils=OUTILS, erreur=None, pendant=None):
        self.texte, self.outils, self.erreur, self.pendant, self.appels = texte, outils, erreur, pendant, []

    def __call__(self, item, methode, params=None):
        self.appels.append((item.get('provider'), methode, (params or {}).get('name'), (params or {}).get('arguments')))
        if self.erreur:
            raise Stop(self.erreur)
        if methode == 'initialize':
            return {'protocolVersion': '2025-06-18', 'serverInfo': {'name': 'faux'}}
        if methode == 'tools/list':
            return {'tools': [{'name': n, 'inputSchema': {'type': 'object', 'properties': {'query': {'type': 'string'}, 'page_size': {'type': 'integer'}},
                                                         'required': ['query']}} for n in self.outils]}
        if methode == 'tools/call':
            if self.pendant:
                self.pendant()
            return {'content': [{'type': 'text', 'text': self.texte}], 'isError': False}
        return {}

    def appels_outils(self):
        return [a for a in self.appels if a[1] == 'tools/call']


class Base(unittest.TestCase):
    def setUp(self):
        self.f = fx.EngineTests('test_observation_has_no_mail_write')
        self.f.setUp()
        self.addCleanup(self.f.doCleanups)
        for cible, nom in ((socket.socket, 'connect'), (socket, 'create_connection'), (socket, 'getaddrinfo')):
            p = patch.object(cible, nom, _refuse_reseau)
            p.start()
            self.addCleanup(p.stop)
        self.d = Desk(self.f.c)
        self.addCleanup(self.d.db.close)
        self.auth = {'origin': 'https://cabinet.example.test', 'csrf': 'test-csrf', 'prefix': PREFIX}
        self.n = 0

    def item(self, pid):
        return S._item(self.d, pid)

    def planifier(self, faux, stamp=1_000_000.0):
        return SP.planifier(self.d, stamp, appel=faux)

    def api(self, route, data=None, acteur=ADMIN, methode='POST'):
        raw = json.dumps(data or {}).encode()
        env = {'REQUEST_METHOD': methode, 'HTTP_ORIGIN': self.auth['origin'], 'HTTP_X_CSRF_TOKEN': 'test-csrf', 'CONTENT_TYPE': 'application/json',
               'CONTENT_LENGTH': str(len(raw)), 'wsgi.input': io.BytesIO(raw), 'HTTP_X_AXIORHUB_ROLE': acteur[1], 'HTTP_X_AXIORHUB_USER': acteur[0]}
        out = SP.api(env, self.d, self.auth, PREFIX, SP.PREFIXE + route, {}, methode)
        out['json'] = json.loads(out['body'])
        return out

    def cle(self):
        self.n += 1
        return 'cle-f11-essai-%04d' % self.n


class Preconfiguration(Base):
    def test_F11_installation_vierge_entrees_creees_activation_demandee_sans_reseau(self):
        for pid in SP.PRECONFIGUREES:
            item = self.item(pid)
            self.assertTrue(item, pid)
            self.assertTrue(item['activation_demandee'], pid)
            self.assertEqual(item['activation_origine'], SP.ORIGINE)
            self.assertFalse(item['enabled'], 'activation effective seulement après la preuve : ' + pid)
            self.assertEqual(item['secret_file'], '')
            self.assertEqual(item['auth_type'], 'none')
        # sources à compte, à clé, OAuth ou conditionnelles : jamais préconfigurées
        for pid in ('openlegi', 'goodlegal', 'librejustice', 'eulex', 'pappers_mcp', 'contract_factory', 'openeu_auto', 'patriboum', 'canlii'):
            self.assertEqual(self.item(pid), {}, pid)

    def test_F11_temoin1_vrai_demarrage_de_desk_installation_vierge(self):
        """Contre le témoin 1 (faux bureau sans démarrage) : un vrai ``Desk`` sur un état vierge préconfigure les sources publiques."""
        from pathlib import Path
        self.assertEqual(self.d.db.execute("SELECT COUNT(*) FROM settings WHERE key LIKE 'lawve:item:%' AND key NOT LIKE '%axiorhub%'").fetchone()[0], 0)
        self.assertTrue((Path(self.f.c['state_dir']) / 'desk.sqlite3').is_file())
        rows = {p['id']: p for p in S.catalogue(self.d)}
        for pid in ('datagouv', 'justicelibre', 'de_bund', 'es_boe', 'gb_legislation', 'ca_federal', 'ca_quebec'):
            self.assertTrue(rows[pid]['configured'], pid)
            self.assertTrue(rows[pid]['activation_demandee'], pid)
            self.assertFalse(rows[pid]['enabled'], pid)
            self.assertNotEqual(rows[pid]['etat_source'], 'desactive', pid)

    def test_F11_preconfiguree_jamais_presentee_comme_desactivee_ni_extension_utilisateur(self):
        from agent import registre5634
        raison = {r['id']: r for r in registre5634.status(self.d)}['justicelibre']['reason']
        self.assertIn('Non configurée', raison)
        self.assertNotIn('Désactivée', raison)
        S.enable(self.d, 'justicelibre', False)                     # choix explicite de l'utilisateur : là, « Désactivée »
        self.assertIn('Désactivée', {r['id']: r for r in registre5634.status(self.d)}['justicelibre']['reason'])

    def test_F11_migration_rejouee_sans_effet(self):
        avant = {pid: self.item(pid)['revision'] for pid in SP.PRECONFIGUREES}
        d2 = Desk(self.f.c)
        self.addCleanup(d2.db.close)
        self.assertEqual(SP.preconfigurer_installation(d2), [])
        self.assertEqual({pid: S._item(d2, pid)['revision'] for pid in SP.PRECONFIGUREES}, avant)

    def test_F11_desactivation_utilisateur_et_compte_existant_preserves_a_la_mise_a_jour(self):
        S.enable(self.d, 'datagouv', False)                       # choix de l'utilisateur
        jl = self.item('justicelibre')
        jl.update(auth_type='bearer', secret_file='/coffre/fictif.secret', external_data_allowed=False, activation_demandee=False)
        self.d.setting('lawve:item:' + S.item_id('justicelibre'), jl)   # compte existant d'avant la mise à jour, sans provenance
        self.d.setting(SP.REPERE, [])                              # installation d'avant 5.6.109 : aucun repère
        avant = {pid: self.item(pid) for pid in ('datagouv', 'justicelibre')}
        d2 = Desk(self.f.c)
        self.addCleanup(d2.db.close)
        self.assertEqual({pid: S._item(d2, pid) for pid in ('datagouv', 'justicelibre')}, avant)
        self.assertFalse(S._item(d2, 'datagouv')['activation_demandee'])
        self.assertEqual(S._item(d2, 'datagouv')['activation_origine'], 'utilisateur')

    def test_F11_source_supprimee_jamais_recreee(self):
        self.d.db.execute("DELETE FROM settings WHERE key=?", ('lawve:item:' + S.item_id('es_boe'),))
        self.d.db.commit()
        d2 = Desk(self.f.c)
        self.addCleanup(d2.db.close)
        self.assertEqual(S._item(d2, 'es_boe'), {})

    def test_F11_contrat_preconfigurer_non_mutant_et_oauth_exclu(self):
        cat = {'datagouv': S.PROVIDERS['datagouv'], 'eulex': S.PROVIDERS['eulex'], 'openlegi': S.PROVIDERS['openlegi']}
        existant = {}
        plan = SP.preconfigurer(cat, existant, ['datagouv', 'datagouv', 'eulex', 'openlegi'])
        self.assertEqual(list(plan), ['datagouv'])
        self.assertEqual(existant, {})

    def test_F11_note_justicelibre_distingue_consultation_et_integration_commerciale(self):
        note = S.PROVIDERS['justicelibre']['note']
        self.assertIn('offre commerciale payante', note)
        self.assertIn('accord écrit', note)
        self.assertIn('9 octobre 2026', note)
        self.assertNotIn('usage commercial soumis', note)


class Qualification(Base):
    def test_F11_activation_automatique_sans_clic_puis_jamais_rejouee(self):
        faux = FauxMCP()
        faits = dict(self.planifier(faux))
        self.assertEqual(faits.get('datagouv'), 'operationnel')
        item = self.item('datagouv')
        self.assertTrue(item['enabled'])
        self.assertEqual(item['purposes'], ['donnees_publiques'])
        self.assertEqual(item['last_test']['capacites']['donnees_publiques'], 'ok')
        appel = [a for a in faux.appels_outils() if a[0] == 'datagouv']
        self.assertEqual(appel[0][2], 'search_datasets')
        self.assertEqual(appel[0][3]['query'], 'DVF')               # requête neutre, aucune donnée du cabinet
        nb = len(faux.appels)
        self.planifier(faux, 2_000_000.0)
        self.assertFalse([a for a in faux.appels[nb:] if a[0] == 'datagouv'], 'source déjà active : aucune nouvelle sonde')
        self.assertEqual(SP.etat_source(self.d, 'datagouv', S.PROVIDERS['datagouv'], item), 'pret')

    def test_F11_au_plus_deux_sources_par_passage(self):
        faux = FauxMCP()
        self.assertLessEqual(len(self.planifier(faux)), SP.MAX_PAR_PASSAGE)

    def test_F11_hors_ligne_garde_la_demande_et_delai_progressif(self):
        faux = FauxMCP(erreur='connexion_http_indisponible')
        self.assertEqual(SP.qualifier_et_activer(self.d, 'datagouv', faux, 1_000_000.0), 'hors_ligne')
        item = self.item('datagouv')
        self.assertTrue(item['activation_demandee'])
        self.assertFalse(item['enabled'])
        self.assertEqual(item['qualification56109']['etat'], 'hors_ligne')
        n = len(faux.appels)
        self.assertEqual(SP.qualifier_et_activer(self.d, 'datagouv', faux, 1_000_100.0), 'deja_reservee')   # avant le délai : rien
        self.assertEqual(len(faux.appels), n)
        faux.erreur = None
        self.assertEqual(SP.qualifier_et_activer(self.d, 'datagouv', faux, 1_000_000.0 + SP.DELAIS[0] + 1), 'operationnel')   # reprise du réseau
        self.assertTrue(self.item('datagouv')['enabled'])

    def test_F11_bail_durable_une_seule_sonde_a_la_fois(self):
        self.assertTrue(SP._reserver(self.d, 'datagouv', self.item('datagouv')['activation_revision'], 1_000_000.0, 'fenetre-1'))
        faux = FauxMCP()
        self.assertEqual(SP.qualifier_et_activer(self.d, 'datagouv', faux, 1_000_010.0, 'fenetre-2'), 'deja_reservee')
        self.assertEqual(faux.appels, [])

    def test_F11_desactivation_pendant_la_sonde_empeche_l_activation(self):
        faux = FauxMCP(pendant=lambda: S.enable(self.d, 'datagouv', False))
        etat = SP.qualifier_et_activer(self.d, 'datagouv', faux, 1_000_000.0)
        self.assertEqual(etat, 'demande_retiree')
        item = self.item('datagouv')
        self.assertFalse(item['enabled'])
        self.assertFalse(item['activation_demandee'])
        self.assertEqual(item['activation_origine'], 'utilisateur')

    def test_F11_enable_refuse_une_demande_de_revision_differente(self):
        faux = FauxMCP()
        S.verify(self.d, 'datagouv', 'complet', faux)
        rev = self.item('datagouv')['activation_revision']
        with self.assertRaisesRegex(Stop, 'demande_activation_retiree'):
            S.enable(self.d, 'datagouv', True, revision_demande=rev + 1)
        self.assertFalse(self.item('datagouv')['enabled'])

    def test_F11_pause_bloque_avant_la_sonde(self):
        self.d.setting('missions567:pause', True)
        faux = FauxMCP()
        self.assertEqual(SP.qualifier_et_activer(self.d, 'datagouv', faux, 1_000_000.0), 'suspendu')
        self.assertEqual(faux.appels, [])
        self.assertTrue(self.item('datagouv')['activation_demandee'])

    def test_F11_mutation_sans_controle_de_pause_la_sonde_partirait(self):
        self.d.setting('missions567:pause', True)
        faux = FauxMCP()
        with patch.object(SP, '_pause', lambda desk: False):        # garde retirée en mémoire seulement
            SP.qualifier_et_activer(self.d, 'datagouv', faux, 1_000_000.0)
        self.assertTrue(faux.appels, 'sans la garde, la sonde part : le contrôle de pause est bien ce qui la retient')

    def test_F11_local_strict_bloque_distant_sans_appel(self):
        faux = FauxMCP()
        with patch('agent.selection_sources5697.local_strict', lambda desk: True):
            self.assertEqual(SP.qualifier_et_activer(self.d, 'datagouv', faux, 1_000_000.0), 'bloque_local_strict')
            self.assertEqual(SP.etat_source(self.d, 'datagouv', S.PROVIDERS['datagouv'], self.item('datagouv')), 'bloque_local_strict')
        self.assertEqual(faux.appels, [])
        self.assertTrue(self.item('datagouv')['activation_demandee'])

    def test_F11_hors_processus_de_service_aucune_sonde(self):
        self.assertIsNone(common.EGRESS_GUARD)
        self.assertEqual(SP.planifier(self.d, 1_000_000.0), [])

    def test_F11_poignee_de_main_seule_ne_vaut_pas_recherche(self):
        faux = FauxMCP(outils=('search_datasets',))                   # outils revus incomplets
        self.assertEqual(SP.qualifier_et_activer(self.d, 'datagouv', faux, 1_000_000.0), 'outils_a_valider')
        self.assertFalse(self.item('datagouv')['enabled'])
        self.assertFalse(faux.appels_outils(), 'aucune recherche tant que les outils revus ne sont pas tous présents')

    def test_F11_reponse_non_reconnue_indeterminee_jamais_vide(self):
        faux = FauxMCP(texte='Service en maintenance, merci de réessayer plus tard.')
        self.assertEqual(SP.qualifier_et_activer(self.d, 'datagouv', faux, 1_000_000.0), 'outils_a_valider')
        self.assertEqual(self.item('datagouv')['last_test']['capacites']['donnees_publiques'], 'indetermine')
        self.assertFalse(self.item('datagouv')['enabled'])

    def test_F11_temoin_test_complet_appelle_vraiment_l_outil(self):
        faux = FauxMCP()
        out = S._essai(S.PROVIDERS['datagouv'], {}, 'complet', faux)
        self.assertEqual(out['capacites']['donnees_publiques'], 'ok')
        self.assertIn('tools/call', [a[1] for a in faux.appels])
        self.assertEqual(out['levels']['search'], 'ok')

    def test_F11_index_etranger_absent_corpus_absent_jamais_consulte(self):
        self.assertEqual(SP.qualifier_et_activer(self.d, 'de_bund', None, 1_000_000.0), 'corpus_absent')   # transport intégré : aucun réseau
        self.assertFalse(self.item('de_bund')['enabled'])
        row = next(r for r in S.catalogue(self.d) if r['id'] == 'de_bund')
        self.assertEqual(row['etat_source'], 'corpus_absent')
        self.assertFalse(row['index']['present'])
        with self.assertRaisesRegex(Stop, 'synchronisation_a_qualifier'):
            mi.regler_synchronisation(self.d, 'ca_quebec', True)      # Québec : synchronisation toujours fermée

    def test_F11_correspondance_permissions_historiques(self):
        self.assertEqual(SP.permissions({'transport': 'integre', 'external_data_allowed': True}, True),
                         {'index_local': True, 'requete_externe': False, 'synchronisation_internet': False})
        self.assertEqual(SP.permissions({'transport': 'streamable_http', 'external_data_allowed': True}, True)['requete_externe'], False)
        self.assertEqual(SP.permissions({'transport': 'streamable_http', 'external_data_allowed': True}, False)['requete_externe'], True)
        self.assertEqual(SP.permissions({'transport': 'integre', 'external_data_allowed': False}, False)['index_local'], False)


class DonneesPubliques(Base):
    def test_F11_normaliseur_texte_officiel_garde_texte_et_references(self):
        n = SP.normaliser_datagouv({'content': [{'type': 'text', 'text': TEXTE_OFFICIEL}]})
        self.assertEqual(n['etat'], 'ok')
        self.assertEqual([j['id'] for j in n['jeux']], ['0000fictif0001', '0000fictif0002'])
        self.assertEqual(n['jeux'][0]['url'], 'https://www.data.gouv.fr/datasets/fictif-1')
        self.assertEqual(n['texte'], TEXTE_OFFICIEL)
        self.assertEqual(mcp5614.rows_from({'content': [{'type': 'text', 'text': TEXTE_OFFICIEL}]}), [], 'le parseur des décisions n’est pas utilisé')

    def test_F11_normaliseur_vide_explicite_partiel_et_indetermine(self):
        self.assertEqual(SP.normaliser_datagouv({'content': [{'type': 'text', 'text': 'Found 0 dataset(s) for query: DVF'}]})['etat'], 'vide')
        self.assertEqual(SP.normaliser_datagouv({'content': [{'type': 'text', 'text': 'Erreur interne'}]})['etat'], 'indetermine')
        self.assertEqual(SP.normaliser_datagouv({'content': [{'type': 'text', 'text': 'Found 1 dataset(s)\n1. Jeu fictif\n   ID: x1\n'}]})['etat'], 'partiel')
        self.assertEqual(SP.normaliser_datagouv({'structuredContent': {'datasets': []}})['etat'], 'vide')
        self.assertEqual(SP.normaliser_datagouv({'structuredContent': {'autre': 1}})['etat'], 'indetermine')
        self.assertEqual(SP.normaliser_datagouv({'content': [], 'isError': True})['etat'], 'erreur')

    def test_F11_consulter_operations_revues_et_provenance(self):
        faux = FauxMCP()
        self.planifier(faux)
        with self.assertRaisesRegex(Stop, 'operation_donnees_non_revue'):
            SP.consulter(self.d, 'resources.query', {}, faux)        # à qualifier : jamais proposée
        out = SP.consulter(self.d, 'datasets.search', {'query': 'DVF'}, faux)
        self.assertEqual(out['etat'], 'ok')
        self.assertTrue(all(j['source'] == 'data.gouv.fr' and j['consulte_le'] and j['nature'] == 'donnee_publique' for j in out['jeux']))
        self.assertIn('ni texte de loi', out['limite'])

    def test_F11_consulter_refuse_si_source_desactivee_ou_pause(self):
        faux = FauxMCP()
        with self.assertRaisesRegex(Stop, 'source_desactivee'):
            SP.consulter(self.d, 'datasets.search', {'query': 'DVF'}, faux)
        self.planifier(faux)
        self.d.setting('missions567:pause', True)
        n = len(faux.appels)
        with self.assertRaisesRegex(Stop, 'automatismes_en_pause'):
            SP.consulter(self.d, 'datasets.search', {'query': 'DVF'}, faux)
        self.assertEqual(len(faux.appels), n)

    def test_F11_datagouv_jamais_autorite_juridique(self):
        faux = FauxMCP()
        self.planifier(faux)
        self.assertTrue(self.item('datagouv')['enabled'])
        self.assertEqual(S.PROVIDERS['datagouv']['tools'], {})
        appels = []
        with patch.object(mcp5614, 'search', lambda item, *a, **k: appels.append(item.get('provider')) or {'status': 'ok', 'rows': []}):
            ext.legal_connector_search(self.d, 'responsabilité civile', providers=['datagouv'])
        self.assertNotIn('datagouv', appels)
        from agent import controle5632
        self.assertNotIn('datagouv', [i.get('provider') for i in S.enabled_items(self.d) if i.get('preferred_tools')])


class Ecrans(Base):
    def test_F11_carte_etat_explicite_et_bouton_reverifier(self):
        row = next(r for r in S.catalogue(self.d) if r['id'] == 'datagouv')
        html = web5632.carte_source(row)
        self.assertIn('Activé : vérification en cours', html)
        self.assertIn('Revérifier', html)
        self.assertIn('data-api="m56109/sources/reverifier"', html)
        row = next(r for r in S.catalogue(self.d) if r['id'] == 'es_boe')
        self.assertIn('Corpus absent', web5632.carte_source(row))

    def test_F11_catalogue_en_get_n_ecrit_rien(self):
        avant = self.d.db.total_changes
        S.catalogue(self.d)
        self.assertEqual(self.d.db.total_changes, avant)

    def test_F11_route_reverifier_administrateur_cle_et_aucun_reseau(self):
        SP.qualifier_et_activer(self.d, 'datagouv', FauxMCP(erreur='delai_http_depasse'), 1_000_000.0)
        cle = self.cle()
        out = self.api('reverifier', {'provider': 'datagouv', 'cle': cle})
        self.assertEqual(out['status'], '200 OK')
        self.assertIn(('Cache-Control', 'private, no-store'), out['headers'])
        row = self.d.db.execute('SELECT prochain_essai, essais FROM sources_publiques56109_sondes WHERE pid=?', ('datagouv',)).fetchone()
        self.assertEqual((row['prochain_essai'], row['essais']), (0, 0))
        self.assertTrue(self.api('reverifier', {'provider': 'datagouv', 'cle': cle})['json']['rejoue'])
        self.assertEqual(self.api('reverifier', {'provider': 'es_boe', 'cle': cle})['status'], '409 Conflict')
        self.assertEqual(self.api('reverifier', {'provider': 'datagouv', 'cle': self.cle()}, acteur=DURAND)['status'], '403 Forbidden')
        self.assertEqual(self.api('etat', methode='GET', acteur=DURAND)['status'], '200 OK')

    def test_F11_installation_montre_les_sources_preparees(self):
        html = SP.installation_html(self.d, 'jeton-csrf')
        self.assertIn('data.gouv.fr', html)
        self.assertIn('Activé : vérification en cours', html)
        self.assertIn('Ignorez cette étape', html)


if __name__ == '__main__':
    unittest.main()
