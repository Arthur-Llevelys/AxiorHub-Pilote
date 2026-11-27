"""5.6.111 (dossier technique, correctifs C01, C02, C06) : comportements des correctifs ciblés, sans serveur ni fournisseur réel."""
import hashlib,importlib.util,sys,types
from pathlib import Path
from types import SimpleNamespace
import tempfile,unittest
from unittest.mock import patch
import agent
from agent import sync56108_fichiers as sync,poste_session
from agent.common import Stop

ROOT=Path(agent.__file__).resolve().parents[1]
try:
    import pydantic  # noqa: F401
except ImportError:  # recette Linux minimale : pydantic simulé, comme test_v5693_operations
    if 'pydantic' not in sys.modules:
        _f=types.ModuleType('pydantic');_f.BaseModel=type('BaseModel',(),{});_f.Field=lambda default=None,**kw:default;sys.modules['pydantic']=_f
sp=importlib.util.spec_from_file_location('outil_contre_audit',ROOT/'integrations/openwebui/axiorhub_tool.py')
outil=importlib.util.module_from_spec(sp);sp.loader.exec_module(outil)

class FichiersSQLite(unittest.TestCase):
    def test_journaux_refuses_avant_lecture(self):
        for nom in ('desk.db-wal','desk.db-shm','desk.db-journal','base.sqlite-wal','base.sqlite3-shm','DESK.DB-JOURNAL'):
            with self.subTest(nom=nom),patch('builtins.open',side_effect=AssertionError('ne doit pas ouvrir')):
                with self.assertRaisesRegex(Stop,'fichier_exclu_de_la_synchronisation'):
                    sync.manifeste(nom)
    def test_publication_source_refusee_avant_acces_bureau(self):
        with self.assertRaisesRegex(Stop,'fichier_exclu_de_la_synchronisation'):
            sync.publier(SimpleNamespace(),'desk.db-wal','message.pdf')
    def test_nom_destination_sqlite_refuse(self):
        with self.assertRaisesRegex(Stop,'fichier_exclu_de_la_synchronisation'):
            sync.publier(SimpleNamespace(),'message.pdf','desk.db-shm')
    def test_document_ordinaire_reste_lisible(self):
        with tempfile.TemporaryDirectory() as tmp:
            p=Path(tmp)/'note.txt';data=b'Note fictive';p.write_bytes(data)
            m=sync.manifeste(p)
            self.assertEqual(m['taille'],len(data));self.assertEqual(m['sha256'],hashlib.sha256(data).hexdigest())

class Outil(unittest.TestCase):
    def tool(self,responses):
        t=outil.Tools();t._call=__import__('unittest.mock',fromlist=['Mock']).Mock(side_effect=responses);return t
    def test_erreur_diligences_reste_visible(self):
        t=self.tool([{'error':'Refus fictif','status':403}]);r=t.lister_les_propositions_de_diligences_et_facturation()
        self.assertEqual(r['status'],403);self.assertEqual(r['source'],'diligences');self.assertIn('error',r)
        self.assertEqual(t._call.call_count,1)
    def test_erreur_facturation_et_lecture_partielle(self):
        t=self.tool([{'proposals':[{'id':'fictif'}]},{'error':'Indisponible fictif','status':503}])
        r=t.lister_les_propositions_de_diligences_et_facturation()
        self.assertEqual(r['source'],'facturation');self.assertEqual(r['diligences'],[{'id':'fictif'}]);self.assertIn('error',r)
    def test_absence_du_champ_pas_confondue_avec_liste_vide(self):
        r=self.tool([{},{}]).lister_les_propositions_de_diligences_et_facturation()
        self.assertIn('error',r)
    def test_vraies_listes_vides_sans_erreur(self):
        r=self.tool([{'proposals':[]},{'proposals':[]}]).lister_les_propositions_de_diligences_et_facturation()
        self.assertNotIn('error',r);self.assertEqual(r['diligences'],[]);self.assertEqual(r['facturation'],[])

class Cookie(unittest.TestCase):
    def exchange(self,scheme,**kw):
        with tempfile.TemporaryDirectory() as tmp:
            auth=Path(tmp)/'ui-auth.json';token=poste_session.new_launch_token(auth)
            r=poste_session.exchange(auth,{'QUERY_STRING':'jeton='+token,'wsgi.url_scheme':scheme,**kw},'/agent-courriel')
            self.assertEqual(r['status'],'303 See Other');return dict(r['headers'])['Set-Cookie']
    def test_cookie_https_secure(self):
        c=self.exchange('https');self.assertIn('; Secure',c);self.assertIn('HttpOnly; SameSite=Strict',c)
    def test_bouclage_http_conserve(self):
        self.assertNotIn('; Secure',self.exchange('http'))
    def test_entete_client_seul_ne_change_pas_scheme(self):
        self.assertNotIn('; Secure',self.exchange('http',HTTP_X_FORWARDED_PROTO='https'))

if __name__=='__main__':unittest.main(verbosity=2)
