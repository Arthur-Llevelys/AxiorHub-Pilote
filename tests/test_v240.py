import json
from pathlib import Path
import unittest

import test_agent as fixtures

from agent.api import dispatch, openapi
from agent.common import Stop
from agent.desk import Desk
from agent.legal_research import (deterministic_control, exhibits,
    identify_latest_writings, import_mcp_results, prepare_research_query,
    provenance, register_exhibits, verify_official_decision)
import upgrade


EXACT_QUOTE = "La réparation intégrale replace la victime dans sa situation antérieure exacte."


class RegistryDAV:
    def __init__(self, raw):
        self.raw=raw
        self.items=[
          {'path':'/Dossiers/DEMO/Conclusions DEMO 2026-09-10 v2.docx','directory':False,
           'etag':'"old"','modified':'Thu, 10 Sep 2026 08:00:00 GMT'},
          {'path':'/Dossiers/DEMO/Conclusions DEMO 2026-09-12 v3.docx','directory':False,
           'etag':'"new"','modified':'Sat, 12 Sep 2026 08:00:00 GMT'},
        ]
    def inventory(self,path):return list(self.items)
    def download(self,item):return self.raw


class ExhibitDAV:
    def inventory(self,path):
        return [
          {'path':path+'/Pièce 1 facture.pdf','directory':False,'etag':'a'},
          {'path':path+'/Annexe copie facture.pdf','directory':False,'etag':'b'},
          {'path':path+'/Pièce 2 photo.jpg','directory':False,'etag':'c'},
        ]
    def download(self,item):
        return b'meme-contenu' if 'facture' in item['path'] else b'photo-distincte'


class LegalResearch240Tests(unittest.TestCase):
    def setUp(self):
        self.f=fixtures.EngineTests(methodName='test_observation_has_no_mail_write')
        self.f.setUp();self.addCleanup(self.f.doCleanups)
        self.f.c['legal_research']={'enabled':True,'official_hosts':[],
          'providers':{name:{'enabled':False} for name in ('openlegal','openlegi','goodlegal','pappers')}}
        self.d=Desk(self.f.c)
        self.template=(Path(__file__).parents[1]/'templates/MODELE_CONCLUSIONS.docx').read_bytes()

    def official(self,url):
        return ("<html><body>Décision JURITEXT000012345678. "+EXACT_QUOTE+
          " Motifs complémentaires suffisamment longs pour constituer un texte officiel contrôlable.</body></html>").encode()

    def test_latest_writings_is_content_hashed_and_fails_closed_on_exact_tie(self):
        dav=RegistryDAV(self.template)
        result=identify_latest_writings(self.d,{'matter':'DOS-001','document_type':'conclusions'},dav=dav)
        self.assertEqual(result['certainty'],'certain')
        self.assertIn('2026-09-12 v3',result['selected']['path'])
        self.assertEqual(len(result['selected']['sha256']),64)
        dav.items[0].update(path='/Dossiers/DEMO/Conclusions A 2026-09-12 v3.docx',
          modified=dav.items[1]['modified'])
        tied=identify_latest_writings(self.d,{'matter':'DOS-001','document_type':'conclusions'},dav=dav)
        self.assertEqual(tied['certainty'],'ambiguous')
        self.assertTrue(tied['requires_explicit_source_confirmation'])

    def test_mcp_query_is_locally_anonymized_before_export(self):
        result=prepare_research_query(self.d,{'matter':'DOS-001',
          'question':'Client DEMO, DOS-001, client@example.test demande la réparation intégrale.',
          'providers':['openlegal','openlegi','goodlegal','pappers']})
        self.assertNotIn('Client DEMO',result['anonymized_query'])
        self.assertNotIn('DOS-001',result['anonymized_query'])
        self.assertNotIn('client@example.test',result['anonymized_query'])
        self.assertEqual(len(result['query_id']),64)
        self.assertGreaterEqual(len(result['redaction_report']),3)

    def test_mcp_lead_becomes_citable_only_after_effective_official_match(self):
        prepared=prepare_research_query(self.d,{'matter':'DOS-001','question':'Réparation intégrale du préjudice.',
          'providers':['openlegal']})
        lead={'title':'Décision test','url':'https://openlegal.example/lead/1',
          'official_url':'https://www.legifrance.gouv.fr/juri/id/JURITEXT000012345678',
          'identifier':'JURITEXT000012345678','exact_excerpt':EXACT_QUOTE,
          'court':'Cour de cassation','date':'2026-01-01'}
        result=import_mcp_results(self.d,{'query_id':prepared['query_id'],'provider':'openlegal',
          'anonymized_query':prepared['anonymized_query'],'results':[lead]},official_fetcher=self.official)
        self.assertEqual(len(result['verified_authorities']),1)
        self.assertTrue(result['verified_authorities'][0]['citable'])
        stored=self.d.db.execute('SELECT verification_status,authority_id FROM legal_research_leads_v240').fetchone()
        self.assertEqual(stored['verification_status'],'verified')
        self.assertTrue(stored['authority_id'])

    def test_official_verification_rejects_a_quote_not_present(self):
        result=verify_official_decision(self.d,{'matter':'DOS-001',
          'official_url':'https://www.legifrance.gouv.fr/juri/id/JURITEXT000012345678',
          'identifier':'JURITEXT000012345678',
          'exact_excerpt':'Cet extrait de plus de trente caractères ne figure pas dans la décision.'},fetcher=self.official)
        self.assertFalse(result['citable'])
        self.assertEqual(result['verification_reason'],'exact_excerpt_not_found')

    def test_mcp_import_rejects_a_different_query_or_provider(self):
        prepared=prepare_research_query(self.d,{'matter':'DOS-001','question':'Question abstraite.',
          'providers':['goodlegal']})
        with self.assertRaisesRegex(Stop,'requete_mcp_non_conforme'):
            import_mcp_results(self.d,{'query_id':prepared['query_id'],'provider':'goodlegal',
              'anonymized_query':'Question modifiée','results':[]})
        with self.assertRaisesRegex(Stop,'fournisseur_juridique_non_autorise'):
            import_mcp_results(self.d,{'query_id':prepared['query_id'],'provider':'pappers','results':[]})

    def test_exhibit_registry_detects_only_byte_identical_duplicates(self):
        result=register_exhibits(self.d,{'matter':'DOS-001'},dav=ExhibitDAV())
        self.assertEqual(result['hashed'],3)
        self.assertEqual(len(result['exact_duplicates']),1)
        self.assertEqual(len(result['exact_duplicates'][0]['paths']),2)
        self.assertEqual(len(exhibits(self.d,'DOS-001')['exhibits']),3)

    def _controlled_data(self):
        packet=[{'id':'source-one','kind':'document','path':'/Dossiers/DEMO/source.docx',
          'etag':'v1','excerpt':'Audience le 14/09/2026. Demande de 4 000 euros. Argument vérifié.'}]
        data={'proposal_id':'a'*32,'matter':{'id':'DOS-001','path':'/Dossiers/DEMO'},
          'source':{'sha256':'1'*64},'latest_writings':{'certainty':'certain'},
          'destination_folder':'/Dossiers/DEMO/Brouillons',
          'future_files':['/Dossiers/DEMO/Brouillons/nouveau.docx'],
          'legal_research':[],'safety':{'external_queries_anonymized':True},
          'draft':{'introduction':'Audience le 14/09/2026.','introduction_source_ids':['source-one'],
            'source_ids':['source-one'],'sections':[{'body':'Demande de 4 000 euros.','source_ids':['source-one']}],
            'requests':[{'text':'Argument vérifié.','source_ids':['source-one']}],
            'exhibits_referenced':[]}}
        return data,packet

    def test_paragraph_provenance_and_deterministic_control_are_independent(self):
        data,packet=self._controlled_data();result=deterministic_control(self.d,data,packet,data['proposal_id'])
        self.assertEqual(result['status'],'passed')
        recorded=provenance(self.d,data['proposal_id'])
        self.assertTrue(recorded['complete'])
        self.assertTrue(all(row['exact_quotes'] for row in recorded['paragraphs']))
        data['draft']['requests'][0]['text']='Condamnation à 99 999 euros.'
        blocked=deterministic_control(self.d,data,packet,data['proposal_id'])
        self.assertEqual(blocked['status'],'blocked')
        self.assertIn('numeric_and_date_tokens_supported',blocked['blocking_reasons'])

    def test_api_openwebui_and_migration_expose_v240_controls(self):
        spec=openapi('https://cabinet.test')
        for path in ('/legal-research/prepare','/legal-research/{query_id}/mcp-results',
          '/document-projects/{project_id}/provenance','/matters/{matter_id}/exhibits'):
            self.assertIn(path,spec['paths'])
        caps=dispatch(self.d,'/capabilities','GET')
        self.assertEqual(caps['version'],'5.6.23')
        self.assertIn('deterministic_document_control',caps['can'])
        tool=(Path(__file__).parents[1]/'integrations/openwebui/axiorhub_tool.py').read_text()
        self.assertIn('def preparer_une_requete_mcp_juridique_anonymisee(',tool)
        self.assertIn('def enregistrer_et_verifier_les_resultats_mcp_juridiques(',tool)
        migration=upgrade.migrate_legal_research(Path(self.f.c['state_dir']))
        self.assertTrue(migration['registries_created'])
        tables={x[0] for x in self.d.db.execute("SELECT name FROM sqlite_master WHERE type='table'")}
        self.assertIn('legal_authorities_v240',tables)
        self.assertIn('paragraph_provenance_v240',tables)


if __name__=='__main__':unittest.main()
