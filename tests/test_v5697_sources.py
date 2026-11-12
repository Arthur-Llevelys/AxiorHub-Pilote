"""5.6.97, lot sources : J01 à J06, J08 et J09 (cahier v3, l. 546-652). J07 (sélection avant appel) appartient au lot proteger.

Données fictives, aucun appel réel : les serveurs MCP, le fournisseur OAuth et la lecture officielle sont des faux qui enregistrent
leurs appels ; tout appel réseau non simulé fait échouer le test (``SansReseau``). Les échantillons officiels sont des fichiers écrits
à la main (tests/fixtures/sources5697), chacun avec sa référence de format et sa date de collecte."""
import base64
from datetime import date, datetime, timedelta, timezone
import hashlib
from html.parser import HTMLParser
import io
import json
from pathlib import Path
import re
import unittest
from unittest.mock import patch
from urllib.parse import parse_qs, urlsplit
import zipfile

import test_desk
from agent.common import Stop, load_config, read_secret
from agent.desk import Desk

FIX = Path(__file__).resolve().parent / 'fixtures' / 'sources5697'
CLE_A = 'cle-fictive-valide-0001'
CLE_B = 'cle-fictive-refusee-0002'
CLE_C = 'cle-fictive-valide-0003'


def octets(nom):
    return (FIX / nom).read_bytes()


class SansReseau(unittest.TestCase):
    """Tout appel réseau non simulé échoue (sockets, résolution DNS, ouverture urllib)."""

    def garde_reseau(self):
        def interdit(*a, **k):
            raise AssertionError('appel réseau non simulé')
        for cible in ('socket.socket.connect', 'socket.create_connection', 'socket.getaddrinfo', 'urllib.request.OpenerDirector.open'):
            p = patch(cible, side_effect=interdit)
            p.start()
            self.addCleanup(p.stop)


class FauxMCP:
    """Serveur MCP fictif : vérifie la clé reçue, enregistre chaque méthode et ses paramètres."""

    def __init__(self, outils=('search', 'get_law_article', 'search_case_law', 'get_decision'), cles=(CLE_A, CLE_C), refus=(), circulaire=False,
                 init_vide=False, schema_variante=False):
        self.outils, self.cles, self.refus, self.circulaire, self.init_vide, self.schema_variante = outils, cles, refus, circulaire, init_vide, schema_variante
        self.appels = []

    def __call__(self, item, method, params=None):
        secret = read_secret(item['secret_file']) if item.get('secret_file') else ''
        self.appels.append((item.get('provider'), method, json.loads(json.dumps(params or {})), secret))
        if item.get('auth_type') in ('bearer', 'oauth2') and secret not in self.cles:
            raise Stop('http_401')
        if method == 'initialize':
            return {} if self.init_vide else {'protocolVersion': '2025-06-18', 'serverInfo': {'name': 'serveur fictif'}}
        if method == 'tools/list':
            props = {'query': {'type': 'string'}}
            if self.schema_variante:
                props['jurisdiction'] = {'type': 'string'}
            tools = [{'name': n, 'description': 'Outil fictif', 'inputSchema': {'type': 'object', 'properties': props, 'required': ['query']}} for n in self.outils]
            return {'tools': tools, 'nextCursor': 'curseur-1'} if self.circulaire else {'tools': tools}
        if method == 'tools/call':
            if params['name'] in self.refus:
                raise Stop('http_403')
            return {'structuredContent': {'results': [{'title': 'Piste fictive', 'identifier': 'X-1'}]}}
        return {}


class Base(SansReseau):
    def setUp(self):
        import test_agent as fixtures
        self.f = fixtures.EngineTests(methodName='test_observation_has_no_mail_write')
        self.f.setUp()
        self.addCleanup(self.f.doCleanups)
        self.desk = Desk(self.f.c)
        self.addCleanup(self.desk.db.close)
        self.garde_reseau()

    @property
    def racine(self):
        from agent.sources5632 import _index_racine
        return _index_racine(self.desk)

    def item(self, pid):
        from agent.sources5632 import item_id
        return self.desk.settings('lawve:item:' + item_id(pid), {}) or {}

    def charger_echantillons(self):
        from agent import mcp_internationaux5697 as mi
        mi.importer(self.racine, 'de_bund', [('bgb', octets('de-bgb.xml'))], 'echantillon')
        mi.importer(self.racine, 'es_boe', [('BOE-A-1889-4763', octets('es-boe-a-1889-4763.xml'))], 'echantillon')
        mi.importer(self.racine, 'gb_legislation', [('ukpga/1979/54', octets('gb-ukpga-1979-54.xml')), ('asp/2099/1', octets('gb-asp-2099-1-fictif.xml'))], 'echantillon')
        mi.importer(self.racine, 'ca_federal', [('I-21', octets('ca-i-21-fr.xml')), ('I-21', octets('ca-i-21-en.xml'))], 'echantillon')
        mi.importer(self.racine, 'ca_quebec', [('CCQ-1991', octets('qc-ccq-1991-fr.xml'))], 'echantillon')


# ------------------------------------------------------------------------------------------------ J01 · catalogue livré
class Catalogue(Base):
    def test_J01_catalogue_visible_sans_appel_sans_secret_et_ids_distincts(self):
        from agent import sources5632 as s
        fiches = {p['id']: p for p in s.catalogue(self.desk)}               # garde réseau active : aucun appel sortant
        for pid in ('justicelibre', 'openlegi', 'goodlegal', 'librejustice', 'legaldatahunter', 'eulex', 'datagouv', 'pappers_mcp', 'contract_factory',
                    'openeu_auto', 'patriboum', 'canlii', 'de_bund', 'es_boe', 'gb_legislation', 'ca_federal', 'ca_quebec'):
            self.assertIn(pid, fiches)
            p = fiches[pid]
            self.assertFalse(p['configured'] or p['enabled'] or p['secret_configured'])
            self.assertIn(p['category'], s.CATEGORIES)
            self.assertIn(p['transport'], s.TRANSPORTS)
            self.assertTrue(set(p['capabilities']) <= set(s.CAPACITES) and p['capabilities'])
            self.assertTrue(p['territories'])
            for lien in (p['access_url'], p['terms_url'], p['help']):
                self.assertTrue(lien == '' or lien.startswith('https://'), (pid, lien))
            for ancien in ('account', 'help', 'terms', 'auth', 'coverage', 'note', 'endpoint'):   # lecture des champs anciens conservée
                self.assertIn(ancien, p)
        self.assertNotEqual(fiches['librejustice']['endpoint'], fiches['justicelibre']['endpoint'])
        self.assertEqual(fiches['pappers_mcp']['capabilities'], ['entreprise', 'justice'])
        from agent import registre5634
        self.assertEqual(registre5634.catalog()['pappers']['protocol'], 'rest')            # REST historique distinct de pappers_mcp
        self.assertEqual(registre5634.catalog()['de_bund']['transport'], 'integre')       # J07 : le lot proteger reconnaît une source locale
        self.assertEqual(registre5634.catalog()['ca_quebec']['territories'], ['CA-QC'])

    def test_J01_fiche_a_qualifier_visible_sans_aucun_usage(self):
        from agent import sources5632 as s
        for pid in ('openeu_auto', 'patriboum', 'canlii'):
            self.assertEqual(s.mode(s.PROVIDERS[pid]), 'a_verifier')
            for appel in (lambda: s.connect(self.desk, pid, allow_external=True), lambda: s.verify(self.desk, pid, appel=FauxMCP()),
                          lambda: s.enable(self.desk, pid, True)):
                with self.assertRaises(Stop) as ctx:
                    appel()
                self.assertEqual(str(ctx.exception), 'source_a_verifier')
            self.assertEqual(self.item(pid), {})

    def test_J01_mise_a_jour_garde_les_connexions_historiques(self):
        """Fiche enregistrée par une 5.6.9x (sans révision, version de secret ni candidate) : toujours active et lisible."""
        from agent import extensions364 as ext, registre5634, sources5632 as s
        secret = ext._save_secret(self.desk, s.item_id('goodlegal'), CLE_A)
        ancien = {'id': s.item_id('goodlegal'), 'kind': 'connector', 'provider': 'goodlegal', 'name': 'GoodLegal', 'endpoint': 'https://api.goodlegal.fr/goodlegal/mcp',
                  'transport': 'streamable_http', 'auth_type': 'bearer', 'secret_file': secret, 'external_data_allowed': True, 'enabled': True,
                  'purposes': ['legal_analysis'], 'last_test': {'status': 'ok', 'tool_specs': [{'name': 'search'}]}}
        self.desk.setting('lawve:item:' + ancien['id'], ancien)
        self.desk.c['lawve_extensions'] = {ancien['id']: ancien}
        fiche = next(p for p in s.catalogue(self.desk) if p['id'] == 'goodlegal')
        self.assertTrue(fiche['enabled'] and fiche['secret_configured'] and fiche['allow_external'])
        self.assertTrue(next(r for r in registre5634.status(self.desk) if r['id'] == 'goodlegal')['active'])
        s.connect(self.desk, 'goodlegal', '', allow_external=True)                        # réenregistrer sans clé : la clé historique reste
        self.assertEqual(self.item('goodlegal')['secret_file'], secret)
        self.assertTrue(self.item('goodlegal')['enabled'])


# ------------------------------------------------------------------------------------------------ J02 / J06 · clé, test, activation
class CleEtTest(Base):
    def test_J02_cle_refusee_ne_remplace_pas_la_cle_active(self):
        from agent import sources5632 as s
        faux = FauxMCP()
        s.connect(self.desk, 'goodlegal', CLE_A, allow_external=True)
        self.assertTrue(s.verify(self.desk, 'goodlegal', appel=faux)['status'] == 'ok')
        actif = self.item('goodlegal')['secret_file']
        self.assertEqual((read_secret(actif), self.item('goodlegal')['secret_version']), (CLE_A, 1))
        s.connect(self.desk, 'goodlegal', CLE_B, allow_external=True)
        candidate = self.item('goodlegal')['candidate']['file']
        self.assertEqual(self.item('goodlegal')['secret_file'], actif)                    # la candidate ne remplace rien avant son test
        out = s.verify(self.desk, 'goodlegal', appel=faux)
        self.assertTrue(out['cle_candidate_refusee'])
        self.assertEqual(read_secret(self.item('goodlegal')['secret_file']), CLE_A)
        self.assertFalse(Path(candidate).exists())
        self.assertEqual(self.item('goodlegal')['secret_version'], 1)
        s.connect(self.desk, 'goodlegal', CLE_C, allow_external=True)
        self.assertEqual(s.verify(self.desk, 'goodlegal', appel=faux)['secret_version'], 2)
        self.assertEqual(read_secret(self.item('goodlegal')['secret_file']), CLE_C)
        self.assertFalse(Path(actif).exists())                                             # ancienne clé effacée après publication
        visible = json.dumps([s.catalogue(self.desk), [dict(r) for r in self.desk.db.execute('SELECT * FROM audit')]], ensure_ascii=False)
        for cle in (CLE_A, CLE_B, CLE_C):
            self.assertNotIn(cle, visible)                                                 # ni catalogue ni journal
            self.assertNotIn(cle, Path(self.item('goodlegal')['secret_file']).read_text(encoding='utf-8'))   # chiffrée au coffre

    def test_J02_test_initialize_puis_tools_list_sans_rien_activer(self):
        from agent import sources5632 as s
        faux = FauxMCP()
        s.connect(self.desk, 'justicelibre', '', allow_external=True)
        out = s.verify(self.desk, 'justicelibre', appel=faux)
        self.assertEqual([a[1] for a in faux.appels], ['initialize', 'tools/list'])         # aucun outil exécuté au premier bouton
        self.assertEqual(out['capacites'], {'textes': 'outil_present', 'decisions': 'outil_present'})
        self.assertFalse(self.item('justicelibre')['enabled'])                             # le test seul n'active rien
        s.verify(self.desk, 'justicelibre', 'complet', appel=faux)
        appels = [a for a in faux.appels if a[1] == 'tools/call']
        self.assertEqual({a[2]['name'] for a in appels}, {'search', 'search_case_law'})
        self.assertTrue(all(a[2]['arguments'] == {'query': 'responsabilité civile délictuelle'} for a in appels))   # question fictive publique
        s.enable(self.desk, 'justicelibre', True)
        self.assertTrue(self.item('justicelibre')['enabled'])

    def test_J02_activation_exige_un_test_de_la_cle_en_vigueur(self):
        from agent import sources5632 as s
        s.connect(self.desk, 'openlegi', CLE_A, allow_external=True)
        with self.assertRaises(Stop) as ctx:
            s.enable(self.desk, 'openlegi', True)
        self.assertEqual(str(ctx.exception), 'cle_candidate_a_tester')
        s.verify(self.desk, 'openlegi', appel=FauxMCP())
        s.enable(self.desk, 'openlegi', True)
        s.connect(self.desk, 'openlegi', CLE_C, allow_external=True)
        with self.assertRaises(Stop):
            s.enable(self.desk, 'openlegi', True)
        s.connect(self.desk, 'openlegi', '', allow_external=False)                          # retrait de l'accord : source désactivée
        self.assertFalse(self.item('openlegi')['enabled'])
        fiche = next(p for p in s.catalogue(self.desk) if p['id'] == 'openlegi')
        self.assertFalse(fiche['allow_external'])                                           # l'accord affiché = valeur enregistrée

    def test_J02_schema_modifie_desactive_la_source(self):
        from agent import sources5632 as s
        s.connect(self.desk, 'justicelibre', '', allow_external=True)
        s.verify(self.desk, 'justicelibre', appel=FauxMCP())
        s.enable(self.desk, 'justicelibre', True)
        out = s.verify(self.desk, 'justicelibre', appel=FauxMCP(schema_variante=True))
        self.assertTrue(out['schema_modifie'])
        self.assertFalse(self.item('justicelibre')['enabled'])

    def test_J02_outils_du_compte_prive_jamais_proposes(self):
        from agent import sources5632 as s
        s.connect(self.desk, 'justicelibre', '', allow_external=True)
        s.verify(self.desk, 'justicelibre', appel=FauxMCP(outils=('search', 'get_law_article', 'list_dossiers', 'upload_document')))
        self.assertEqual({t['name'] for t in self.item('justicelibre')['last_test']['tool_specs']}, {'search', 'get_law_article'})

    def test_J02_revision_perimee_refusee(self):
        from agent import sources5632 as s
        s.connect(self.desk, 'goodlegal', CLE_A, allow_external=True)
        rev = self.item('goodlegal')['revision']
        s.connect(self.desk, 'goodlegal', '', allow_external=True, revision=rev)
        with self.assertRaises(Stop) as ctx:
            s.connect(self.desk, 'goodlegal', CLE_C, allow_external=True, revision=rev)
        self.assertEqual(str(ctx.exception), 'revision_source_perimee')
        self.assertFalse(self.item('goodlegal')['candidate'].get('file', '').endswith('0003'))

    def test_J09_echecs_mcp_explicites(self):
        from agent import sources5632 as s
        s.connect(self.desk, 'justicelibre', '', allow_external=True)
        for faux, code in ((FauxMCP(circulaire=True), 'pagination_outils_mcp_incomplete'), (FauxMCP(init_vide=True), 'reponse_mcp_invalide')):
            out = s.verify(self.desk, 'justicelibre', appel=faux)
            self.assertEqual((out['status'], out['error']), ('error', code))
        s.connect(self.desk, 'goodlegal', CLE_B, allow_external=True)
        out = s.verify(self.desk, 'goodlegal', appel=FauxMCP())
        self.assertEqual(out['status'], 'error')
        self.assertTrue(out['cle_candidate_refusee'])


# ------------------------------------------------------------------------------------------------ J03 · entreprises, données publiques
class Specialisees(Base):
    def test_J03_pappers_entreprise_sans_droit_justice(self):
        from agent import sources5632 as s
        revus = {'entreprise': ('recherche_entreprise',), 'justice': ('recherche_justice',)}
        with patch.dict(s.PROVIDERS['pappers_mcp'], {'outils_revus': revus}):
            s.connect(self.desk, 'pappers_mcp', CLE_A, allow_external=True)
            faux = FauxMCP(outils=('recherche_entreprise', 'recherche_justice'), refus=('recherche_justice',))
            out = s.verify(self.desk, 'pappers_mcp', 'complet', appel=faux)
        self.assertEqual(out['capacites'], {'entreprise': 'ok', 'justice': 'refuse'})
        fiche = next(p for p in s.catalogue(self.desk) if p['id'] == 'pappers_mcp')
        self.assertEqual(fiche['last_test']['capacites']['justice'], 'refuse')
        recherches = [a[2]['arguments'] for a in faux.appels if a[1] == 'tools/call']
        self.assertTrue(all(a == {'query': '000010017'} for a in recherches))               # SIREN d'essai seulement
        self.assertEqual(len([a for a in faux.appels if a[1] == 'tools/call' and a[2]['name'] == 'recherche_entreprise']), 1)

    def test_J03_contract_factory_aucune_formalite(self):
        from agent import sources5632 as s
        s.connect(self.desk, 'contract_factory', '', allow_external=True)
        s.verify(self.desk, 'contract_factory', appel=FauxMCP(outils=('recherche_siren', 'generer_statuts', 'creer_societe', 'deposer_formalite')))
        self.assertEqual([t['name'] for t in self.item('contract_factory')['last_test']['tool_specs']], ['recherche_siren'])
        self.assertIsNone(s.tool_for({'provider': 'contract_factory', 'preferred_tools': {'search': 'generer_statuts'}}, 'search', [{'name': 'generer_statuts'}]))

    def test_J03_categories_et_sources_conditionnelles_jamais_appelees(self):
        from agent import sources5632 as s
        self.assertEqual((s.PROVIDERS['datagouv']['category'], s.PROVIDERS['pappers_mcp']['category'], s.PROVIDERS['patriboum']['category']),
                         ('donnees_publiques', 'entreprises', 'patrimoine'))
        faux = FauxMCP()
        for pid in ('openeu_auto', 'patriboum'):
            with self.assertRaises(Stop):
                s.verify(self.desk, pid, appel=faux)
        self.assertEqual(faux.appels, [])


# ------------------------------------------------------------------------------------------------ J04 / J05 · adaptateurs intégrés
class Adaptateurs(Base):
    def setUp(self):
        super().setUp()
        self.charger_echantillons()

    def test_J04_allemagne_version_a_la_date_sans_etat_historique(self):
        from agent import mcp_internationaux5697 as mi
        c = mi.citation(self.racine, 'de_bund', 'BGB', '§ 433', '2024-10-15')
        self.assertEqual((c['statut'], c['identifiant_officiel'], c['sous_zone'], c['langue']), ('citable', 'BGB § 433', 'DE-BUND', 'de'))
        self.assertEqual(c['lien'], 'https://www.gesetze-im-internet.de/bgb/__433.html')
        self.assertIn('Kaufpreis', c['extrait'])
        self.assertEqual(c['publication'], 'RGBl 1896, 195')                                # promulgation distincte de la consolidation
        self.assertEqual(mi.citation(self.racine, 'de_bund', 'BGB', '433', '2000-01-01')['raison'], 'version_anterieure_non_prouvee')
        apres = (date.today() + timedelta(days=1)).isoformat()
        self.assertEqual(mi.citation(self.racine, 'de_bund', 'BGB', '433', apres)['statut'], 'piste_a_verifier')   # instantané antérieur
        self.assertEqual(mi.citation(self.racine, 'de_bund', 'BGB', '999')['raison'], 'article_introuvable')

    def test_J04_espagne_texte_original_et_consolidation_distincts(self):
        from agent import mcp_internationaux5697 as mi
        avant = mi.citation(self.racine, 'es_boe', 'BOE-A-1889-4763', 'Artículo 1', '1950-01-01')
        apres = mi.citation(self.racine, 'es_boe', 'BOE-A-1889-4763', '1', '1980-01-01')
        original = mi.citation(self.racine, 'es_boe', 'BOE-A-1889-4763', '1', '1980-01-01', original=True)
        self.assertEqual((avant['statut'], apres['statut']), ('citable', 'citable'))
        self.assertIn('Península', avant['texte'])
        self.assertIn('fuentes del ordenamiento', apres['texte'])
        self.assertEqual((original['nature'], original['texte']), ('original', avant['texte']))
        self.assertEqual(avant['fin'], '1974-07-28')
        self.assertEqual(apres['identifiant_officiel'], 'BOE-A-1889-4763, art. 1')
        self.assertTrue(apres['lien'].startswith('https://www.boe.es/buscar/act.php?id=BOE-A-1889-4763'))
        self.assertEqual(mi.citation(self.racine, 'es_boe', 'BOE-A-1889-4763', '1', '1800-01-01')['statut'], 'piste_a_verifier')

    def test_J04_espagne_norme_autonomique_refusee_index_conserve(self):
        from agent import mcp_internationaux5697 as mi
        avant = mi.etat_index(self.racine, 'es_boe')
        with self.assertRaises(Stop) as ctx:
            mi.importer(self.racine, 'es_boe', [('BOE-A-2099-77777', octets('es-autonomica-fictive.xml'))])
        self.assertEqual(str(ctx.exception), 'norme_hors_fonds_etat')
        self.assertEqual(mi.etat_index(self.racine, 'es_boe'), avant)

    def test_J05_angleterre_galles_etendue_et_modifications_non_appliquees(self):
        from agent import mcp_internationaux5697 as mi
        r = mi.chercher(self.racine, 'gb_legislation', 'contract of sale', 'GB-EAW', '2020-01-01')
        self.assertEqual({x['identifier'] for x in r['results']}, {'ukpga/1979/54'})     # l'acte écossais fictif est écarté
        self.assertEqual(r['results'][0]['statut'], 'piste_a_verifier')                     # une recherche ne rend que des pistes
        self.assertEqual(mi.citation(self.racine, 'gb_legislation', 'ukpga/1979/54', '2', '2020-01-01')['statut'], 'citable')
        self.assertEqual(mi.citation(self.racine, 'gb_legislation', 'ukpga/1979/54', '3', '2020-01-01')['raison'], 'modifications_non_appliquees')
        self.assertEqual(mi.citation(self.racine, 'gb_legislation', 'asp/2099/1', '1', '2020-01-01')['raison'], 'hors_perimetre_territorial')

    def test_J05_canada_federal_et_quebec_separes_fr_en_relies(self):
        from agent import mcp_internationaux5697 as mi
        fed = mi.chercher(self.racine, 'ca_federal', 'interprète', 'CA-FED', '2020-01-01')
        qc = mi.chercher(self.racine, 'ca_quebec', 'préjudice', 'CA-QC', '2020-01-01')
        self.assertEqual({x['identifier'] for x in fed['results']}, {'I-21'})
        self.assertEqual({x['identifier'] for x in qc['results']}, {'CCQ-1991'})
        self.assertEqual(mi.chercher(self.racine, 'ca_federal', 'préjudice', 'CA-QC')['statut'], 'hors_perimetre')
        fr = mi.citation(self.racine, 'ca_federal', 'I-21', '12', '2020-01-01', 'fr')
        en = mi.citation(self.racine, 'ca_federal', 'I-21', '12', '2020-01-01', 'en')
        self.assertEqual((fr['statut'], en['statut']), ('citable', 'citable'))
        self.assertEqual((fr['lien'], en['lien']), ('https://laws-lois.justice.gc.ca/fra/lois/I-21/article-12.html',
                                                    'https://laws-lois.justice.gc.ca/eng/acts/I-21/section-12.html'))
        ccq = mi.citation(self.racine, 'ca_quebec', 'CCQ-1991', '1457', '2020-01-01')
        self.assertEqual((ccq['statut'], ccq['identifiant_officiel'], ccq['sous_zone']), ('citable', 'RLRQ, c. CCQ-1991, art. 1457', 'CA-QC'))
        self.assertEqual(mi.citation(self.racine, 'ca_quebec', 'I-21', '12')['raison'], 'reference_introuvable')

    def test_J05_jurisprudence_indisponible_signalee_sans_reference_inventee(self):
        from agent import mcp_internationaux5697 as mi
        for pid, etat in (('de_bund', 'couverture_partielle'), ('gb_legislation', 'licence_a_verifier'), ('es_boe', 'lien_seulement'),
                          ('ca_federal', 'cle_conditionnelle')):
            self.assertNotIn('search_decisions', [t['name'] for t in mi.outils(pid)])
            r = mi.chercher(self.racine, pid, 'decision jurisprudence', mi.module(pid).ZONES[0])
            self.assertEqual(r['decisions']['etat'], etat)
            self.assertTrue(r['decisions']['lien'].startswith('https://'))
            self.assertEqual(r['results'], [])

    def test_J08_index_absent_rend_un_etat_pas_un_faux_zero(self):
        from agent import mcp_internationaux5697 as mi
        import shutil
        shutil.rmtree(Path(self.racine) / 'de_bund')
        r = mi.chercher(self.racine, 'de_bund', 'Kaufvertrag')
        self.assertEqual(r['statut'], 'index_absent')
        self.assertIsNone(mi.etat_index(self.racine, 'de_bund')['articles'])
        self.assertEqual(mi.citation(self.racine, 'de_bund', 'BGB', '433')['raison'], 'index_absent')


# ------------------------------------------------------------------------------------------------ J08 · transport intégré, sûreté
class TransportIntegre(Base):
    def setUp(self):
        super().setUp()
        self.charger_echantillons()

    def test_J08_adaptateur_passe_par_le_registre_sans_reseau(self):
        from agent import extensions364 as ext, registre5634, sources5632 as s
        s.connect(self.desk, 'de_bund', allow_external=True)
        out = s.verify(self.desk, 'de_bund', 'complet')
        self.assertEqual((out['status'], out['capacites']), ('ok', {'textes': 'ok'}))
        s.enable(self.desk, 'de_bund', True)
        self.assertTrue(next(r for r in registre5634.status(self.desk) if r['id'] == 'de_bund')['active'])
        resultats = ext.legal_connector_search(self.desk, 'Kaufvertrag Verkäufer', 5)
        de = next(r for r in resultats if r['provider'] == 'de_bund')
        self.assertEqual(de['status'], 'ok')
        self.assertEqual(de['rows'][0]['identifier'], 'bgb')
        self.assertEqual(de['rows'][0]['statut'], 'piste_a_verifier')

    def test_J08_identifiant_compile_et_parametres_bornes(self):
        from agent import mcp_internationaux5697 as mi
        item = {'provider': 'de_bund', 'index_racine': self.racine}
        for mauvais, code in (({'provider': 'module_externe', 'index_racine': self.racine}, 'adaptateur_integre_inconnu'),):
            with self.assertRaises(Stop) as ctx:
                mi.appel(mauvais, 'tools/list')
            self.assertEqual(str(ctx.exception), code)
        for params, code in (({'name': 'executer_script', 'arguments': {}}, 'outil_integre_inconnu'),
                             ({'name': 'search_norms', 'arguments': {'query': 'x', 'url': 'https://www.example.test/autre'}}, 'arguments_outil_inconnus'),
                             ({'name': 'search_norms', 'arguments': {'query': 'x', 'zone': 'FR'}}, 'arguments_outil_invalides'),
                             ({'name': 'get_norm', 'arguments': {'identifiant': 'bgb'}}, 'arguments_outil_requis')):
            with self.assertRaises(Stop) as ctx:
                mi.appel(item, 'tools/call', params)
            self.assertEqual(str(ctx.exception), code)
        with self.assertRaises(Stop):
            mi.appel({'provider': 'de_bund', 'index_racine': str(Path(self.racine).parent)}, 'tools/call',
                     {'name': 'search_norms', 'arguments': {'query': 'Kaufvertrag'}})
        with self.assertRaises(Stop):
            mi.appel(item, 'resources/read', {'uri': 'file:///etc/passwd'})

    def test_J08_xml_sans_dtd_archive_bornee_sans_chemin_sortant(self):
        from agent import mcp_internationaux5697 as mi
        bombe = b'<?xml version="1.0"?><!DOCTYPE d [<!ENTITY a "aaaa">]><dokumente builddate="20240101">&a;</dokumente>'
        with self.assertRaises(Stop) as ctx:
            mi.importer(self.racine, 'de_bund', [('bgb', bombe)])
        self.assertEqual(str(ctx.exception), 'xml_refuse')
        tampon = io.BytesIO()
        with zipfile.ZipFile(tampon, 'w') as z:
            z.writestr('../../evasion.xml', octets('de-bgb.xml'))
        with self.assertRaises(Stop) as ctx:
            mi.importer(self.racine, 'de_bund', [('bgb', tampon.getvalue())])
        self.assertEqual(str(ctx.exception), 'archive_source_chemin_refuse')
        tampon = io.BytesIO()
        with zipfile.ZipFile(tampon, 'w', zipfile.ZIP_DEFLATED) as z:
            z.writestr('bgb.xml', b'<a>' + b' ' * 200_000 + b'</a>')
        with self.assertRaises(Stop) as ctx:
            mi.zip_sur(tampon.getvalue(), max_total=100_000)
        self.assertEqual(str(ctx.exception), 'archive_source_trop_volumineuse')
        self.assertEqual(mi.citation(self.racine, 'de_bund', 'BGB', '433', '2024-10-15')['statut'], 'citable')   # index précédent intact

    def test_J08_synchronisation_desactivee_par_defaut(self):
        from agent import mcp_internationaux5697 as mi
        lus = []
        for pid in mi.FONDS:
            self.assertFalse(mi.etat_synchronisation(self.desk, pid)['active'])
        with self.assertRaises(Stop) as ctx:
            mi.synchroniser(self.desk, 'es_boe', ['BOE-A-1889-4763'], lecture=lambda url, n: lus.append(url))
        self.assertEqual(str(ctx.exception), 'synchronisation_desactivee')
        self.assertEqual(lus, [])
        with self.assertRaises(Stop) as ctx:
            mi.regler_synchronisation(self.desk, 'ca_quebec', True)
        self.assertEqual(str(ctx.exception), 'synchronisation_a_qualifier')

    def reponses_boe(self):
        complet = octets('es-boe-a-1889-4763.xml').decode('utf-8')
        meta = re.sub(r'<texto>.*</texto>', '', complet, flags=re.S).encode('utf-8')
        texte = re.sub(r'<metadatos>.*</metadatos>', '', complet, flags=re.S).encode('utf-8')
        return {'https://www.boe.es/datosabiertos/api/legislacion-consolidada/id/BOE-A-1889-4763/metadatos': meta,
                'https://www.boe.es/datosabiertos/api/legislacion-consolidada/id/BOE-A-1889-4763/texto': texte}

    def test_J08_synchronisation_bascule_apres_validation_et_echec_conserve(self):
        from agent import mcp_internationaux5697 as mi
        mi.regler_synchronisation(self.desk, 'es_boe', True, 'cabinet')
        reponses, lus = self.reponses_boe(), []

        def lecture(url, max_octets):
            lus.append(url)
            return reponses[url]
        out = mi.synchroniser(self.desk, 'es_boe', ['BOE-A-1889-4763'], lecture=lecture, owner='cabinet')
        self.assertEqual(out['synchronisation']['resultat'], 'ok')
        self.assertEqual(out['index']['origine'], 'synchronisation')
        self.assertEqual(set(lus), set(reponses))                                           # seules les adresses officielles fixées
        actif = mi.etat_index(self.racine, 'es_boe')
        out = mi.synchroniser(self.desk, 'es_boe', ['BOE-A-1889-4763'], lecture=lambda url, n: b'<response><status><code>500</code></status></response>')
        self.assertEqual(out['synchronisation']['resultat'], 'echec')
        self.assertEqual(mi.etat_index(self.racine, 'es_boe'), actif)                       # index précédent et sa date conservés
        self.assertEqual(mi.citation(self.racine, 'es_boe', 'BOE-A-1889-4763', '1', '1980-01-01')['statut'], 'citable')
        with self.assertRaises(Stop):
            mi.synchroniser(self.desk, 'es_boe', ['https://www.example.test/autre'], lecture=lecture)
        self.assertEqual(len(list((Path(self.racine) / 'es_boe').glob('index-*.sqlite3'))), 2)   # rétention : actif + précédent

    def test_J08_lecture_officielle_refuse_un_hote_non_officiel(self):
        from agent import mcp_internationaux5697 as mi
        lire = mi.lecture_officielle(mi.module('es_boe'))
        with self.assertRaises(Stop) as ctx:
            lire('https://www.example.test/datosabiertos/api/legislacion-consolidada/id/BOE-A-1889-4763/texto', 1000)
        self.assertEqual(str(ctx.exception), 'source_non_officielle')


# ------------------------------------------------------------------------------------------------ J06 · OAuth MCP
class FauxOAuth:
    RESSOURCE = 'https://mcp.eulex.ai/mcp'
    AS = 'https://auth.eulex.ai'

    def __init__(self, serveur=None, token_endpoint=None, s256=True, refus_refresh=False):
        self.serveur, self.token_endpoint = serveur or self.AS, token_endpoint or self.AS + '/token'
        self.s256, self.refus_refresh, self.appels, self.n = s256, refus_refresh, [], 0

    def __call__(self, methode, url, donnees=None):
        self.appels.append((methode, url, dict(donnees or {})))
        if url == 'https://mcp.eulex.ai/.well-known/oauth-protected-resource/mcp':
            return {'resource': self.RESSOURCE, 'authorization_servers': [self.serveur], 'scopes_supported': ['mcp:read']}
        if url == self.serveur + '/.well-known/oauth-authorization-server':
            return {'issuer': self.serveur, 'authorization_endpoint': self.serveur + '/authorize', 'token_endpoint': self.token_endpoint,
                    'registration_endpoint': self.serveur + '/register', 'revocation_endpoint': self.serveur + '/revoke',
                    'code_challenge_methods_supported': ['S256'] if self.s256 else ['plain']}
        if url.endswith('/register'):
            return {'client_id': 'client-fictif-axiorhub'}
        if url.endswith('/token'):
            if donnees.get('grant_type') == 'refresh_token' and self.refus_refresh:
                raise Stop('http_400')
            self.n += 1
            return {'access_token': 'jeton-fictif-acces-%d' % self.n, 'refresh_token': 'jeton-fictif-renouv-%d' % self.n, 'token_type': 'Bearer',
                    'expires_in': 3600, 'scope': 'mcp:read'}
        if url.endswith('/revoke'):
            return {}
        raise AssertionError('adresse OAuth inattendue : ' + url)


class OAuthMCP(Base):
    RETOUR = 'https://cabinet.example.test/agent-courriel/api440/m5632/admin/sources/oauth/retour'

    def demarrer(self, faux, session='session-a'):
        from agent import oauth_mcp5697 as o
        url = o.demarrer(self.desk, 'cabinet', session, 'eulex', self.RETOUR, transport=faux)['url']
        return {k: v[0] for k, v in parse_qs(urlsplit(url).query).items()}, url

    def test_J06_parcours_pkce_s256_etat_unique_et_ressource(self):
        from agent import oauth_mcp5697 as o, sources5632 as s
        faux = FauxOAuth()
        q, url = self.demarrer(faux)
        self.assertTrue(url.startswith('https://auth.eulex.ai/authorize?'))
        self.assertEqual((q['code_challenge_method'], q['resource'], q['redirect_uri'], q['client_id']),
                         ('S256', 'https://mcp.eulex.ai/mcp', self.RETOUR, 'client-fictif-axiorhub'))
        etat = self.desk.settings('oauth_mcp5697:etat:' + hashlib.sha256(q['state'].encode()).hexdigest())
        attendu = base64.urlsafe_b64encode(hashlib.sha256(etat['verifier'].encode()).digest()).decode().rstrip('=')
        self.assertEqual(q['code_challenge'], attendu)
        with self.assertRaises(Stop) as ctx:                                                # autre session : refusé, et l'état est consommé
            o.terminer(self.desk, 'cabinet', 'session-b', {'state': q['state'], 'code': 'code-fictif'}, transport=faux)
        self.assertEqual(str(ctx.exception), 'etat_oauth_invalide')
        with self.assertRaises(Stop):
            o.terminer(self.desk, 'cabinet', 'session-a', {'state': q['state'], 'code': 'code-fictif'}, transport=faux)
        q, _ = self.demarrer(faux)
        out = o.terminer(self.desk, 'cabinet', 'session-a', {'state': q['state'], 'code': 'code-fictif'}, transport=faux)
        self.assertEqual(out['droits'], ['mcp:read'])
        echange = [a for a in faux.appels if a[1].endswith('/token')][-1][2]
        self.assertEqual((echange['code_verifier'] != '', echange['resource'], echange['redirect_uri']), (True, 'https://mcp.eulex.ai/mcp', self.RETOUR))
        self.assertEqual(len([a for a in faux.appels if a[1].endswith('/register')]), 1)     # client enregistré une seule fois
        fiche = next(p for p in s.catalogue(self.desk) if p['id'] == 'eulex')
        self.assertEqual((fiche['mode'], fiche['oauth']['etat'], fiche['enabled']), ('oauth', 'connecte', False))
        visible = json.dumps([s.catalogue(self.desk), [dict(r) for r in self.desk.db.execute('SELECT * FROM audit')]], ensure_ascii=False)
        self.assertNotIn('jeton-fictif', visible)
        self.assertEqual(read_secret(self.item('eulex')['secret_file']), 'jeton-fictif-acces-1')
        self.assertNotIn('jeton-fictif', Path(self.item('eulex')['secret_file']).read_text(encoding='utf-8'))
        mcp = FauxMCP(cles=('jeton-fictif-acces-1',))
        self.assertEqual(s.verify(self.desk, 'eulex', appel=mcp)['status'], 'ok')         # test avec le jeton, puis activation explicite
        s.connect(self.desk, 'eulex', allow_external=True)
        s.enable(self.desk, 'eulex', True)
        self.assertTrue(self.item('eulex')['enabled'])
        self.assertFalse([k for (k,) in self.desk.db.execute("SELECT key FROM settings WHERE key LIKE 'chatgpt%'")])   # parcours distinct

    def test_J06_refus_origine_et_pkce(self):
        from agent import oauth_mcp5697 as o
        q, _ = self.demarrer(FauxOAuth())
        with self.assertRaises(Stop) as ctx:
            o.terminer(self.desk, 'cabinet', 'session-a', {'state': q['state'], 'error': 'access_denied'}, transport=FauxOAuth())
        self.assertEqual(str(ctx.exception), 'autorisation_refusee')
        for faux, code in ((FauxOAuth(serveur='https://auth.example.test'), 'serveur_autorisation_non_autorise'),
                           (FauxOAuth(token_endpoint='https://jetons.example.test/token'), 'origine_oauth_non_autorisee'),
                           (FauxOAuth(s256=False), 'pkce_s256_non_pris_en_charge')):
            with self.assertRaises(Stop) as ctx:
                self.demarrer(faux)
            self.assertEqual(str(ctx.exception), code)
        with self.assertRaises(Stop) as ctx:                                               # source sans OAuth qualifié
            o.demarrer(self.desk, 'cabinet', 's', 'librejustice', self.RETOUR, transport=FauxOAuth())
        self.assertEqual(str(ctx.exception), 'oauth_source_non_qualifie')

    def test_J06_renouvellement_rotation_ratee_et_revocation(self):
        from agent import oauth_mcp5697 as o
        faux = FauxOAuth()
        q, _ = self.demarrer(faux)
        o.terminer(self.desk, 'cabinet', 'session-a', {'state': q['state'], 'code': 'code-fictif'}, transport=faux)
        jetons = self.desk.settings('oauth_mcp5697:jetons:eulex')
        jetons['expire'] = (datetime.now(timezone.utc) - timedelta(minutes=1)).isoformat()
        self.desk.setting('oauth_mcp5697:jetons:eulex', jetons)
        chemin = o.jeton_valide(self.desk, 'eulex', transport=faux)
        self.assertEqual(read_secret(chemin), 'jeton-fictif-acces-2')
        self.desk.setting('oauth_mcp5697:jetons:eulex', {**self.desk.settings('oauth_mcp5697:jetons:eulex'), 'expire': '2000-01-01T00:00:00+00:00'})
        with self.assertRaises(Stop) as ctx:
            o.jeton_valide(self.desk, 'eulex', transport=FauxOAuth(refus_refresh=True))
        self.assertEqual(str(ctx.exception), 'reconnexion_oauth_requise')
        self.assertEqual(self.item('eulex')['oauth']['etat'], 'reconnexion_requise')
        out = o.revoquer(self.desk, 'eulex', 'cabinet', transport=faux)
        self.assertEqual(out['revocation_distante'], 'ok')
        self.assertFalse(Path(chemin).exists())
        self.assertEqual((self.item('eulex')['enabled'], self.item('eulex')['oauth']['etat']), (False, 'revoque'))


# ------------------------------------------------------------------------------------------------ J06 / J09 · écrans communs
class Cartes(HTMLParser):
    def __init__(self):
        super().__init__()
        self.cartes, self.courante, self.zones = {}, None, []

    def handle_starttag(self, tag, attrs):
        a = dict(attrs)
        if tag == 'section' and a.get('data-zone'):
            self.zones.append(a['data-zone'])
        if tag == 'article' and a.get('data-provider'):
            self.courante = a['data-provider']
            self.cartes[self.courante] = {'mode': a.get('data-mode'), 'liens': [], 'champs': [], 'formulaires': []}
        if self.courante:
            if tag == 'a':
                self.cartes[self.courante]['liens'].append((a.get('href'), a.get('target'), a.get('rel')))
            if tag == 'input':
                self.cartes[self.courante]['champs'].append((a.get('type'), a.get('name')))
            if tag == 'form':
                self.cartes[self.courante]['formulaires'].append(a.get('data-m5632'))

    def handle_endtag(self, tag):
        if tag == 'article':
            self.courante = None


def cartes(html):
    p = Cartes()
    p.feed(html)
    return p


class Ecrans(SansReseau):
    setUp0 = test_desk.WebTests.setUp
    request = test_desk.WebTests.request

    def setUp(self):
        self.setUp0()
        self.garde_reseau()

    def desk(self):
        desk = Desk(load_config(str(self.config)))
        self.addCleanup(desk.db.close)
        return desk

    def json_post(self, route, body, cookie=''):
        import test_v5631
        return test_v5631.Web.json_request(self, '/api440/m5632/' + route, body, cookie=cookie)

    def test_J06_memes_cartes_et_memes_liens_dans_parametres_et_guide(self):
        from agent import guide5631 as g
        desk = self.desk()
        session = g.start(desk, 'cabinet', ['recherches'])
        params = cartes(self.request('/parametres', query='rubrique=recherche')['body'])
        guide = cartes(self.request('/guide-installation', query='session=%s&etape=sources' % session['id'])['body'])
        self.assertEqual({k: v['liens'] for k, v in params.cartes.items()}, {k: v['liens'] for k, v in guide.cartes.items()})
        self.assertEqual(guide.zones, ['FR', 'UE', 'DE-BUND', 'ES-ETAT', 'GB-EAW', 'CA-FED', 'CA-QC', 'ENTREPRISES'])
        modes = {k: v['mode'] for k, v in guide.cartes.items()}
        self.assertEqual((modes['goodlegal'], modes['justicelibre'], modes['eulex'], modes['openeu_auto'], modes['de_bund']),
                         ('cle', 'sans_cle', 'oauth', 'a_verifier', 'integre'))
        self.assertIn(('password', 'token'), guide.cartes['goodlegal']['champs'])
        self.assertNotIn(('password', 'token'), guide.cartes['justicelibre']['champs'])
        self.assertNotIn(('password', 'token'), guide.cartes['eulex']['champs'])
        self.assertEqual((guide.cartes['openeu_auto']['champs'], guide.cartes['openeu_auto']['formulaires']), ([], []))   # aucune connexion feinte
        self.assertIn('admin/sources/oauth/start', guide.cartes['eulex']['formulaires'])
        for carte in guide.cartes.values():
            for href, target, rel in carte['liens']:
                self.assertTrue(href.startswith('https://'))
                self.assertEqual((target, rel), ('_blank', 'noopener noreferrer'))
        from agent.sources5632 import liens_guide
        self.assertEqual(set(re.findall(r'href="(https://[^"]+)"', g._where_html('sources', ''))), {u for _, u in liens_guide()})

    def test_J06_configurer_plus_tard_et_retour_sans_ressaisie(self):
        from agent import guide5631 as g
        desk = self.desk()
        s = g.start(desk, 'cabinet', ['recherches'])
        body = self.request('/guide-installation', query='session=%s&etape=sources' % s['id'])['body']
        self.assertIn('data-option="sources"', body)
        self.assertIn('sources', g.PLUS_TARD)
        self.assertEqual(g.REGLAGES['sources'], '/parametres?rubrique=recherche')

    def test_J06_routes_admin_oauth_et_conflit_de_revision(self):
        from agent import sources5632 as s
        desk = self.desk()
        s.connect(desk, 'goodlegal', CLE_A, allow_external=True)
        r = self.json_post('admin/sources/connect', {'provider': 'goodlegal', 'allow_external': '1', 'revision': '0'})
        self.assertTrue(r['status'].startswith('409'))
        faux = FauxOAuth()
        with patch('agent.oauth_mcp5697.transport_reseau', side_effect=faux):
            r = self.json_post('admin/sources/oauth/start', {'provider': 'eulex', 'retour': 'parametres'}, cookie='axiorhub_session=session-fictive')
        self.assertTrue(r['status'].startswith('200'), r)
        url = json.loads(r['body'])['url']
        self.assertEqual(parse_qs(urlsplit(url).query)['redirect_uri'][0],
                         'https://cabinet.example.test/agent-courriel/api440/m5632/admin/sources/oauth/retour')
        r = self.request('/api440/m5632/admin/sources/oauth/retour', query='state=inconnu&code=x')
        self.assertTrue(r['status'].startswith('303'))
        self.assertIn('oauth=erreur&code=etat_oauth_invalide', r['headers']['Location'])

    def test_J09_installation_neuve_aucun_appel_sortant_ni_secret_prerempli(self):
        body = self.request('/parametres', query='rubrique=recherche')['body']   # garde réseau active
        c = cartes(body)
        self.assertTrue(c.cartes)
        self.assertNotIn('value="cle-', body)
        self.assertNotRegex(body, r'name="token"[^>]*value="[^"]')


# ------------------------------------------------------------------------------------------------ J07 (part du lot sources) · cache, citation
class CacheEtCitation(Base):
    def test_J07_cache_mcp_isole_par_cabinet_connexion_et_version_du_secret(self):
        from agent import extensions364 as ext
        crees = []

        class Session:
            def __init__(self, item):
                crees.append(ext.session_key(item))
                self.protocol = '2025-06-18'

            def initialize(self):
                return {'protocolVersion': self.protocol}

            def call(self, method, params):
                return {'tools': []}
        base = {'id': 'connector-axiorhub-goodlegal', 'endpoint': 'https://mcp.example.test/mcp', 'secret_file': '/coffre/a.secret',
                'secret_version': 1, 'cabinet': 'cabinet-a'}
        variantes = [base, {**base, 'secret_version': 2}, {**base, 'id': 'connector-axiorhub-openlegi'}, {**base, 'cabinet': 'cabinet-b'}]
        ext._SESSIONS.clear()
        self.addCleanup(ext._SESSIONS.clear)
        with patch('agent.mcp5614.Session', Session):
            for item in variantes + [dict(base)]:
                ext._mcp_call(item, 'tools/list', {})
        self.assertEqual(len(crees), 4)                                                     # la même connexion réutilise sa session, jamais une autre
        self.assertEqual(len(set(crees)), 4)

    def test_J07_local_strict_selectionne_l_adaptateur_integre_et_citation_normalisee(self):
        from agent import registre5634, selection_sources5697 as sel, sources5632 as s
        self.charger_echantillons()
        s.connect(self.desk, 'de_bund', allow_external=True)
        s.verify(self.desk, 'de_bund')
        s.enable(self.desk, 'de_bund', True)
        s.connect(self.desk, 'justicelibre', '', allow_external=True)
        s.verify(self.desk, 'justicelibre', appel=FauxMCP())
        s.enable(self.desk, 'justicelibre', True)
        with patch('agent.selection_sources5697.local_strict', return_value=True):
            choix = sel.selectionner(self.desk, ['de_bund', 'justicelibre'])
        self.assertEqual((choix.ids, choix.ecartes), (('de_bund',), {'justicelibre': 'mode_local_strict'}))   # transport « integre » = local
        out = registre5634.search(self.desk, 'Kaufvertrag Pflichten', '', ['de_bund'], zones=['DE-BUND'])
        self.assertTrue(out['leads'])
        self.assertEqual({l['provider'] for l in out['leads']}, {'de_bund'})
        citation = out['leads'][0]['citation']
        self.assertEqual((citation['sous_zone'], citation['pays'], citation['statut']), ('DE-BUND', 'DE', 'piste_a_verifier'))
        self.assertEqual(citation['lien'], 'https://www.gesetze-im-internet.de/bgb/__433.html')

    def test_J07_citation_normalisee_citable_seulement_apres_verification_officielle(self):
        from agent.citations5697 import normaliser
        piste = {'provider': 'justicelibre', 'title': 'Cass. civ. 1re, décision fictive', 'ecli': 'ECLI:FR:CCASS:2024:C100001',
                 'provider_url': 'https://www.courdecassation.fr/decision/fictive', 'summary': 'Extrait fictif'}
        self.assertEqual(normaliser(piste, zones=['FR'])['raison'], 'non_verifiee_sur_la_source_officielle')
        verifiee = {**piste, 'official_url': 'https://www.courdecassation.fr/decision/fictive', 'status': 'verified', 'citable': True,
                    'exact_excerpt': 'Extrait exact fictif de la décision.', 'official_text_sha256': 'a' * 64}
        c = normaliser(verifiee, zones=['FR'])
        self.assertEqual((c['statut'], c['type_identifiant'], c['identifiant_officiel']), ('citable', 'ecli', 'ECLI:FR:CCASS:2024:C100001'))
        self.assertEqual(normaliser({**verifiee, 'official_url': 'https://www.example.test/copie', 'provider_url': ''}, zones=['FR'])['raison'], 'lien_non_officiel')
        self.assertEqual(normaliser({**verifiee, 'langue': 'en'}, zones=['FR'])['raison'], 'traduction_non_officielle')
        self.assertEqual(normaliser({**verifiee, 'provider': 'legaldatahunter'})['raison'], 'zone_ambigue')     # UE ou international ?
        self.assertEqual(normaliser({'provider': 'eulex', 'provider_url': 'https://eur-lex.europa.eu/eli/reg/2016/679/oj'})['type_identifiant'], 'eli')
        self.assertEqual(normaliser({'provider': 'eulex', 'title': 'Règlement 32016R0679'}, zones=['UE'])['identifiant_officiel'], '32016R0679')
        self.assertEqual(normaliser({'provider': 'goodlegal', 'title': 'Sans référence'}, zones=['FR'])['raison'], 'identifiant_officiel_absent')


# ------------------------------------------------------------------------------------------------ J09 · recette, reprise, IA bloquée
class Recette(Base):
    def test_J09_garde_reseau_fait_echouer_un_appel_non_simule(self):
        from agent.common import HTTP
        with self.assertRaises(AssertionError):
            HTTP('https://justicelibre.org').request('GET', 'https://justicelibre.org/mcp')

    def test_J09_panne_d_une_source_preserve_les_autres(self):
        from agent import extensions364 as ext, mcp_internationaux5697 as mi, sources5632 as s
        self.charger_echantillons()
        s.connect(self.desk, 'de_bund', allow_external=True)
        s.verify(self.desk, 'de_bund')
        s.enable(self.desk, 'de_bund', True)
        s.connect(self.desk, 'justicelibre', '', allow_external=True)
        s.verify(self.desk, 'justicelibre', appel=FauxMCP())
        s.enable(self.desk, 'justicelibre', True)
        reel = ext._mcp_call

        def panne(item, method, params=None):
            if item.get('transport') == 'integre':
                return reel(item, method, params)
            raise Stop('connexion_http_indisponible')
        with patch('agent.extensions364._mcp_call', side_effect=panne):
            resultats = {r['provider']: r for r in ext.legal_connector_search(self.desk, 'Kaufvertrag', 5)}
        self.assertEqual(resultats['de_bund']['status'], 'ok')
        self.assertTrue(resultats['de_bund']['rows'])
        self.assertNotEqual(resultats['justicelibre']['status'], 'ok')
        self.assertTrue(mi.etat_index(self.racine, 'de_bund')['present'])

    def test_J09_commandes_sans_ia_fournisseurs_bloques(self):
        """Connexion, test, activation et lecture d'un adaptateur intégré : aucun appel à un modèle, IA en pause."""
        from agent import arret5643, mcp_internationaux5697 as mi, sources5632 as s
        arret5643.pause_all(self.desk, True)
        self.charger_echantillons()
        with patch('agent.model.Model.complete', side_effect=AssertionError('modèle appelé')):
            s.connect(self.desk, 'ca_quebec', allow_external=True)
            self.assertEqual(s.verify(self.desk, 'ca_quebec', 'complet')['status'], 'ok')
            s.enable(self.desk, 'ca_quebec', True)
            self.assertEqual(mi.citation(self.racine, 'ca_quebec', 'CCQ-1991', '1458', '2020-01-01')['statut'], 'citable')


if __name__ == '__main__':
    unittest.main()
