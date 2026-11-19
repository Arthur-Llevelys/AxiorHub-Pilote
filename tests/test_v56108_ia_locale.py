"""5.6.108, lot L : assistant « Configurer l'IA locale » (détection d'Ollama, machine, modèles, suggestions, téléchargement confirmé, réglages).

L1 détection (absent, arrêté, joignable ; adresse publique refusée ; OLLAMA_HOST montré, jamais utilisé) ; L2 machine (classes, Ollama distant) ;
L3 modèles installés (« :cloud » non local) ; L4 suggestions (catalogue vérifié, installé d'abord, téléchargement seulement à défaut, jamais
« :cloud ») ; L5 téléchargement (confirmation, catalogue, progression, un seul à la fois, annulation, reprise, idempotence, mode local strict) ;
L6 réglages (répartition, embeddings confirmés, conseil, essai court, retour réversible, 409) ; L7 routes (administrateur seulement, 404,
no-store, aucune écriture pendant un GET) ; L8 textes en/de/es. Faux Ollama de bouclage : aucun vrai téléchargement, aucun modèle réel,
aucune adresse du cabinet."""
import io
import json
from pathlib import Path
import time
import unittest
from unittest.mock import patch

import test_agent as fixtures
from faux_ollama56108 import FauxOllama, FausseSonde, modele
from test_v5689_ia import muter
from agent import ia_locale56108 as L
from agent.common import Stop, load_config, private_json
from agent.desk import Desk

ORIGINE_SONDE = L.Sonde      # classe d'origine, gardée avant tout patch

ROOT = Path(__file__).resolve().parents[1]
ADMIN = ('admin@example.test', 'administrateur')
AVOCAT = ('martin@example.test', 'avocat')
ORIGINE = 'https://cabinet.example.test'
INJOIGNABLE = 'http://127.0.0.1:9'          # port de bouclage fermé : « Ollama absent ou arrêté »


def synchrone(cible, *args):
    cible(*args)


def attendre(condition, delai=10.0):
    fin = time.monotonic() + delai
    while time.monotonic() < fin:
        if condition():
            return True
        time.sleep(0.05)
    return False


class Base(unittest.TestCase):
    modeles = ()

    def setUp(self):
        self.f = fixtures.EngineTests(methodName='test_observation_has_no_mail_write')
        self.f.setUp()
        self.addCleanup(self.f.doCleanups)
        self.ollama = FauxOllama(self.modeles)
        self.addCleanup(self.ollama.arreter)
        c = dict(self.f.c)
        c['ollama'] = {'url': self.ollama.url, 'model': 'qwen3:8b'}
        c['rag'] = {'enabled': True, 'embedding_model': 'qwen3-embedding:0.6b'}
        self.config = self.f.base / 'config.json'
        private_json(self.config, c)
        self.desk = Desk(load_config(str(self.config)))
        self.addCleanup(self.desk.db.close)
        self.env = {'axiorhub.config_path': str(self.config)}
        self.sonde = FausseSonde()
        L._SONDES.clear()
        for cible, valeur in (('agent.ia_locale56108.demarrer', synchrone), ('agent.ia_locale56108.Sonde', lambda *a, **k: self.sonde)):
            p = patch(cible, valeur)
            p.start()
            self.addCleanup(p.stop)
        self.addCleanup(L._SONDES.clear)

    def recharger(self):
        self.desk.db.close()
        self.desk = Desk(load_config(str(self.config)))
        return self.desk

    def appel(self, route, corps=None, args=None, who=ADMIN, methode=None):
        raw = json.dumps(corps if corps is not None else {}).encode()
        env = {'HTTP_X_AXIORHUB_USER': who[0], 'HTTP_X_AXIORHUB_ROLE': who[1], 'HTTP_ORIGIN': ORIGINE, 'HTTP_X_CSRF_TOKEN': 'jeton',
               'CONTENT_LENGTH': str(len(raw)), 'CONTENT_TYPE': 'application/json', 'wsgi.input': io.BytesIO(raw), **self.env}
        methode = methode or ('POST' if corps is not None else 'GET')
        out = L.api(env, self.desk, {'origin': ORIGINE, 'csrf': 'jeton'}, '/p', L.PREFIXE + route, args or {}, methode)
        return out, json.loads(out['body'])

    def changements(self):
        return self.desk.db.total_changes


# ================================================================================ L1 : détection
class Detection(Base):
    def test_L1_01_ollama_joignable_version(self):
        d = L.detecter(self.desk, sonde=self.sonde)
        self.assertTrue(d['joignable'])
        self.assertEqual(d['etat'], 'joignable')
        self.assertEqual(d['version'], '0.12.99')
        self.assertEqual(d['adresse'], self.ollama.url)

    def test_L1_02_absent_lien_officiel_et_commande_ubuntu_jamais_executee(self):
        self.desk.c['ollama']['url'] = INJOIGNABLE
        with patch('agent.ia_locale56108.CANDIDATS', ()):
            d = L.detecter(self.desk, sonde=FausseSonde(plateforme='linux', trouve=False))
        self.assertEqual(d['etat'], 'absent')
        self.assertIn({'type': 'lien', 'url': 'https://ollama.com/download'}, d['actions'])
        self.assertIn({'type': 'commande', 'texte': 'curl -fsSL https://ollama.com/install.sh | sh'}, d['actions'])
        self.assertTrue(d['commande_jamais_executee'])
        with patch('agent.ia_locale56108.CANDIDATS', ()):
            w = L.detecter(self.desk, sonde=FausseSonde(plateforme='windows', trouve=False))
        self.assertEqual([a['type'] for a in w['actions']], ['lien'])            # Windows : lien seulement

    def test_L1_03_installe_mais_arrete(self):
        self.desk.c['ollama']['url'] = INJOIGNABLE
        with patch('agent.ia_locale56108.CANDIDATS', ()):
            d = L.detecter(self.desk, sonde=FausseSonde(plateforme='linux', trouve=True, service=True))
        self.assertEqual(d['etat'], 'arrete')
        self.assertIn('ne répond pas', d['message'])
        self.assertEqual(d['actions'], [{'type': 'commande', 'texte': 'sudo systemctl start ollama'}])

    def test_L1_04_repli_sur_127_0_0_1_puis_localhost(self):
        self.desk.c['ollama']['url'] = INJOIGNABLE
        with patch('agent.ia_locale56108.CANDIDATS', (self.ollama.url,)):
            d = L.detecter(self.desk, sonde=self.sonde)
        self.assertTrue(d['joignable'])
        self.assertEqual([e['adresse'] for e in d['essais']], [INJOIGNABLE, self.ollama.url])
        self.assertEqual(L.CANDIDATS, ('http://127.0.0.1:11434', 'http://localhost:11434'))

    def test_L1_05_adresse_publique_refusee_privee_et_nom_local_admis(self):
        for url in ('http://8.8.8.8:11434', 'http://1.1.1.1', 'http://ollama.example.com:11434', 'http://[2001:db8::1]:11434',
                    'http://169.254.169.254', 'ftp://10.0.0.77', 'http://u:p' + '@10.0.0.77:11434', 'http://10.0.0.77:11434/api', 'http://10.0.0.77:11434?x=1'):
            with self.subTest(url=url), self.assertRaises(Stop):
                L.verifier_adresse(url)
        self.assertEqual(L.verifier_adresse('10.0.0.77'), 'http://10.0.0.77:11434')
        self.assertEqual(L.verifier_adresse('http://172.20.0.5:8080/'), 'http://172.20.0.5:8080')
        self.assertEqual(L.verifier_adresse('http://[fd00::5]:11434'), 'http://[fd00::5]:11434')
        self.assertEqual(L.verifier_adresse('http://serveur-ia.local'), 'http://serveur-ia.local:11434')
        self.assertEqual(L.verifier_adresse('http://ollama:11434'), 'http://ollama:11434')
        with self.assertRaisesRegex(Stop, 'adresse_ollama_publique_refusee'):
            L.detecter(self.desk, adresse='http://8.8.8.8:11434', sonde=self.sonde)

    def test_L1_05_mutation_le_refus_de_l_adresse_publique_est_le_garde_fou(self):
        with muter(L, "        if not (ip.is_loopback or any(ip in r for r in RESEAUX_PRIVES if r.version == ip.version)):\n            raise Stop('adresse_ollama_publique_refusee')",
                   "        pass") as copie:
            self.assertEqual(copie.verifier_adresse('http://8.8.8.8:11434'), 'http://8.8.8.8:11434')   # sans le garde-fou, l'adresse publique passerait

    def test_L1_06_ollama_host_montre_jamais_utilise(self):
        sonde = FausseSonde(ollama_host='http://10.0.0.77:11434')
        self.desk.c['ollama']['url'] = INJOIGNABLE
        with patch('agent.ia_locale56108.CANDIDATS', ()):
            d = L.detecter(self.desk, sonde=sonde)
        self.assertEqual(d['ollama_host'], 'http://10.0.0.77:11434')
        self.assertNotIn('http://10.0.0.77:11434', [e['adresse'] for e in d['essais']])   # montrée, jamais essayée

    def test_L1_07_ollama_distant_adresse_saisie(self):
        d = L.detecter(self.desk, adresse=self.ollama.url, sonde=self.sonde)
        self.assertEqual(d['source'], 'saisie')
        self.assertTrue(d['joignable'])
        self.assertTrue(d['locale'])                       # bouclage : machine d'AxiorHub ; une IP privée serait « autre ordinateur »
        self.assertFalse(L.est_locale('http://10.0.0.77:11434'))


# ================================================================================ L2 : machine
class Machine(Base):
    def test_L2_01_classes_expliquees(self):
        self.assertEqual(L.classer(64, 24)[0], 'forte')
        self.assertEqual(L.classer(32, 12)[0], 'moyenne')
        self.assertEqual(L.classer(64, None)[0], 'moyenne')         # sans carte graphique : moyenne, plus lent (dit)
        self.assertIn('plus lentes', L.classer(64, None)[2])
        self.assertEqual(L.classer(16, None)[:2], ('legere', 8.0))
        self.assertEqual(L.classer(16, 8)[0], 'legere')
        self.assertEqual(L.classer()[0], 'inconnue')

    def test_L2_02_poste_local_sonde(self):
        sonde = FausseSonde(ram=32, coeurs=12, cartes=[{'nom': 'Carte fictive', 'vram_go': 24.0, 'source': 'nvidia-smi'}], disque=80.0)
        m = L.evaluer_machine(self.ollama.url, True, sonde=sonde)
        self.assertEqual((m['classe'], m['ram_go'], m['coeurs'], m['vram_go'], m['disque_libre_go']), ('forte', 32, 12, 24.0, 80.0))

    def test_L2_03_ollama_distant_api_ps_et_saisie(self):
        self.ollama.ps = [{'name': 'qwen3:14b', 'size': 11 * 10 ** 9, 'size_vram': 11 * 10 ** 9}]
        m = L.evaluer_machine(self.ollama.url, False)
        self.assertEqual((m['source'], m['classe'], m['vram_go']), ('ollama_distant', 'moyenne', 11.0))
        self.assertIn('d’au moins 11 Go', m['explication'])
        m = L.evaluer_machine(self.ollama.url, False, vram_saisie='24')
        self.assertEqual(m['classe'], 'forte')
        self.assertEqual(L.evaluer_machine(self.ollama.url, False, vram_saisie='abc')['vram_saisie'], None)

    def test_L2_04_sonde_reelle_rien_n_est_installe_ni_lance(self):
        with patch('agent.ia_locale56108.shutil.which', return_value=None),                 patch('agent.ia_locale56108.subprocess.run', side_effect=AssertionError('aucune commande ne doit être lancée')):
            s = ORIGINE_SONDE(plateforme='linux')
            self.assertEqual(s.cartes(), [])                            # sans nvidia-smi ni rocm-smi : aucune carte, rien lancé
            self.assertIn('trouve', s.executable())


# ================================================================================ L3, L4 : modèles installés et suggestions
INSTALLES_FORTE = (modele('qwen3:32b', 32.8, 20.0, ('completion', 'tools', 'thinking'), famille='qwen3'),
                   modele('mistral-small3.2:24b', 24.0, 15.0, ('completion', 'tools', 'vision'), famille='mistral3'),
                   modele('qwen3:8b', 8.2, 5.2, ('completion', 'tools', 'thinking'), famille='qwen3'),
                   modele('qwen3-embedding:0.6b', 0.6, 0.64, ('embedding',), famille='qwen3'),
                   modele('gpt-oss:120b-cloud', 116.8, 0.0, ('completion',), distant=True))


class Suggestions(Base):
    modeles = INSTALLES_FORTE

    def test_L3_01_installes_cloud_non_local(self):
        ms = {m['nom']: m for m in L.modeles_installes(self.ollama.url)}
        self.assertFalse(ms['gpt-oss:120b-cloud']['local'])
        self.assertTrue(ms['gpt-oss:120b-cloud']['cloud'])
        self.assertTrue(ms['qwen3-embedding:0.6b']['embedding'])
        self.assertEqual(ms['qwen3:32b']['contexte'], 32768)
        self.assertIn('thinking', ms['qwen3:32b']['capacites'])
        self.assertTrue(ms['qwen3:8b']['petit'])
        self.assertFalse(ms['qwen3:32b']['petit'])

    def test_L3_02_hote_distant_declare_par_show_non_local(self):
        self.ollama.modeles['autre:7b'] = modele('autre:7b', 7, 4, distant=True)
        ms = {m['nom']: m for m in L.modeles_installes(self.ollama.url)}
        self.assertFalse(ms['autre:7b']['local'])

    def test_L4_01_forte_installe_d_abord_aucun_telechargement(self):
        installes = L.modeles_installes(self.ollama.url)
        s = L.suggerer(installes, 'forte', 24.0, {'rapide': '', 'analyse': '', 'controle': '', 'embeddings': 'qwen3-embedding:0.6b'})
        r = {x['role']: x for x in s['roles']}
        self.assertEqual(r['analyse']['choisi'], 'qwen3:32b')
        self.assertEqual(r['controle']['choisi'], 'mistral-small3.2:24b')           # famille différente du rédacteur
        self.assertEqual(r['rapide']['choisi'], 'qwen3:8b')
        self.assertEqual(r['embeddings']['choisi'], 'qwen3-embedding:0.6b')
        self.assertEqual(s['telechargements'], [])
        self.assertNotIn('gpt-oss:120b-cloud', json.dumps(s))                         # jamais proposé, même en alternative

    def test_L4_02_legere_rien_installe_telechargements_du_catalogue(self):
        s = L.suggerer([], 'legere', 8.0, {})
        r = {x['role']: x for x in s['roles']}
        self.assertEqual(r['rapide']['telecharger']['nom'], 'qwen3:4b')
        self.assertEqual(r['analyse']['telecharger']['nom'], 'qwen3:8b')
        self.assertEqual(r['controle']['telecharger']['nom'], 'ministral-3:8b')
        self.assertEqual(r['embeddings']['telecharger']['nom'], 'qwen3-embedding:0.6b')
        for t in s['telechargements']:
            self.assertFalse(L.est_cloud(t['nom']))
            self.assertLessEqual(t['memoire_go'], 8.5)                                # tient dans la machine
            self.assertTrue(t['licence_libelle'] and t['fiche'].startswith('https://ollama.com/library/') and t['releve_le'])

    def test_L4_03_moyenne_role_mal_rempli_suggere_un_telechargement(self):
        installes = [m for m in L.modeles_installes(self.ollama.url) if m['nom'] in ('qwen3:8b', 'qwen3-embedding:0.6b')]
        s = L.suggerer(installes, 'moyenne', 12.0, {'analyse': 'qwen3:8b'})
        r = {x['role']: x for x in s['roles']}
        self.assertEqual(r['rapide']['choisi'], 'qwen3:8b')
        self.assertEqual(r['rapide']['telecharger'], None)
        self.assertEqual(r['analyse']['origine'], 'repli')                             # 8 Md ne remplit pas « analyse » sur une machine moyenne
        self.assertEqual(r['analyse']['telecharger']['nom'], 'qwen3:14b')

    def test_L4_04_catalogue_verifie_complet(self):
        cat = L.catalogue()
        self.assertEqual(cat['releve_le'], '2026-11-19')
        for m in cat['modeles']:
            with self.subTest(m=m['nom']):
                self.assertFalse(L.est_cloud(m['nom']))
                self.assertIn(m['licence'], cat['licences'])
                self.assertTrue(m['fiche'].startswith('https://ollama.com/library/'))
                self.assertTrue(m['source_licence'].startswith(('https://ollama.com/library/', 'https://huggingface.co/')))
                self.assertEqual(m['releve_le'], '2026-11-19')
                self.assertGreater(m['telechargement_go'], 0)
        for role, classes in cat['roles'].items():
            for classe, noms in classes.items():
                for n in noms:
                    self.assertIn(n, cat['par_nom'], '%s/%s : %s absent du catalogue' % (role, classe, n))
        from agent import conseil56108 as K
        self.assertTrue(set(cat['conseil'].values()) <= set(K.PAR_ID))

    def test_L4_05_correspondance_des_noms(self):
        self.assertTrue(L.correspond('qwen3:8b-q4_K_M', 'qwen3:8b'))
        self.assertTrue(L.correspond('bge-m3:latest', 'bge-m3'))
        self.assertFalse(L.correspond('qwen3:8b', 'qwen3:4b'))
        self.assertFalse(L.correspond('qwen3:8b-cloud', 'qwen3:8b') and not L.est_cloud('qwen3:8b-cloud'))

    def test_L4_06_conseil_suggere_selon_la_classe(self):
        c = L.conseil_suggere(self.desk, L.modeles_installes(self.ollama.url), 'forte')
        self.assertEqual(c['suggere'], 'local_equilibre')
        self.assertTrue(all(p['local'] for p in c['prereglages']))


# ================================================================================ L5 : téléchargement
class Telechargement(Base):
    modeles = (modele('qwen3:8b', 8.2, 5.2, ('completion', 'tools', 'thinking'), famille='qwen3'),)

    def demande(self, **plus):
        return {'cle': plus.pop('cle', 'cle-telechargement-0001'), 'modele': plus.pop('modele', 'qwen3:4b'), 'confirme': True, **plus}

    def test_L5_01_jamais_sans_confirmation_explicite(self):
        for confirme in (None, False, 'true', 1):
            data = self.demande()
            data['confirme'] = confirme
            with self.subTest(confirme=confirme), self.assertRaisesRegex(Stop, 'confirmation_requise'):
                L.demander_telechargement(self.desk, data, ADMIN[0])
        self.assertEqual(self.ollama.pulls(), [])

    def test_L5_01_mutation_sans_le_controle_de_confirmation_le_pull_partirait(self):
        with muter(L, "    if data.get('confirme') is not True:\n        raise Stop('confirmation_requise')", "    pass") as copie:
            copie.demarrer = synchrone
            copie.Sonde = lambda *a, **k: self.sonde
            data = self.demande()
            data.pop('confirme')
            copie.demander_telechargement(self.desk, data, ADMIN[0])
        self.assertEqual(len(self.ollama.pulls()), 1)

    def test_L5_02_hors_catalogue_et_cloud_refuses(self):
        with self.assertRaisesRegex(Stop, 'modele_hors_catalogue'):
            L.demander_telechargement(self.desk, self.demande(modele='inconnu:7b'), ADMIN[0])
        with self.assertRaisesRegex(Stop, 'modele_cloud_refuse'):
            L.demander_telechargement(self.desk, self.demande(modele='qwen3:8b-cloud'), ADMIN[0])
        self.assertEqual(self.ollama.pulls(), [])

    def test_L5_03_progression_et_fin(self):
        out = L.demander_telechargement(self.desk, self.demande(), ADMIN[0])
        op = L.operation(self.desk, out['operation']['id'])
        self.assertEqual(op['etat'], 'termine')
        self.assertEqual((op['fait'], op['total'], op['pourcent']), (4 * 10 ** 9, 4 * 10 ** 9, 100.0))
        self.assertTrue(attendre(lambda: 'qwen3:4b' in self.ollama.modeles))       # le faux Ollama a « installé » le modèle
        self.assertEqual(out['fiche']['licence'], 'Apache-2.0')
        actions = [r[0] for r in self.desk.db.execute("SELECT action FROM audit WHERE action LIKE 'ia_locale56108%'")]
        self.assertIn('ia_locale56108_telechargement_demande', actions)
        self.assertIn('ia_locale56108_telechargement_termine', actions)

    def test_L5_04_idempotence_meme_cle(self):
        a = L.demander_telechargement(self.desk, self.demande(), ADMIN[0])
        b = L.demander_telechargement(self.desk, self.demande(), ADMIN[0])
        self.assertEqual(a['operation']['id'], b['operation']['id'])
        self.assertTrue(b['idempotent'])
        self.assertEqual(len(self.ollama.pulls()), 1)
        with self.assertRaisesRegex(Stop, 'cle_reutilisee'):
            L.demander_telechargement(self.desk, self.demande(modele='qwen3:14b'), ADMIN[0])

    def test_L5_05_un_seul_a_la_fois_puis_annulation(self):
        self.ollama.etapes_pull, self.ollama.pause_pull = 200, 0.05
        import threading
        with patch('agent.ia_locale56108.demarrer', lambda cible, *a: threading.Thread(target=cible, args=a, daemon=True).start()):
            out = L.demander_telechargement(self.desk, self.demande(), ADMIN[0])
            ident = out['operation']['id']
            self.assertTrue(attendre(lambda: (L.operation(self.desk, ident)['fait'] or 0) > 0))
            with self.assertRaisesRegex(Stop, 'telechargement_en_cours'):
                L.demander_telechargement(self.desk, self.demande(cle='cle-telechargement-0002', modele='qwen3:14b'), ADMIN[0])
            r = L.annuler(self.desk, {'cle': 'cle-annulation-0001', 'id': ident}, ADMIN[0])
            self.assertIn('Annulation demandée', r['message'])
            self.assertTrue(attendre(lambda: L.operation(self.desk, ident)['etat'] == 'annule'))
        self.assertNotIn('qwen3:4b', self.ollama.modeles)
        self.assertTrue(attendre(lambda: self.ollama.pulls_interrompus >= 1))          # la connexion a été fermée : Ollama arrête

    def test_L5_06_erreur_puis_reprise(self):
        self.ollama.erreur_pull_a = 3
        out = L.demander_telechargement(self.desk, self.demande(), ADMIN[0])
        op = L.operation(self.desk, out['operation']['id'])
        self.assertEqual(op['etat'], 'erreur')
        self.assertIn('fictive network error', op['erreur'])
        self.ollama.erreur_pull_a = None
        out = L.demander_telechargement(self.desk, self.demande(cle='cle-reprise-00001'), ADMIN[0])   # « Reprendre » : nouvelle demande confirmée
        self.assertEqual(L.operation(self.desk, out['operation']['id'])['etat'], 'termine')

    def test_L5_07_connexion_coupee_sans_succes_n_est_jamais_un_succes(self):
        self.ollama.coupure_pull_a = 4
        out = L.demander_telechargement(self.desk, self.demande(), ADMIN[0])
        op = L.operation(self.desk, out['operation']['id'])
        self.assertEqual((op['etat'], op['erreur']), ('erreur', 'flux_interrompu'))

    def test_L5_08_mode_local_strict(self):
        with patch('agent.selection_sources5697.local_strict', return_value=True):
            self.assertFalse(L.telechargement_permis(self.desk)[0])
            with self.assertRaisesRegex(Stop, 'telechargement_local_strict'):
                L.demander_telechargement(self.desk, self.demande(), ADMIN[0])
            with patch('agent.passerelle5689._admis', return_value=({'registry.ollama.ai'}, set())):
                self.assertTrue(L.telechargement_permis(self.desk)[0])
        self.assertEqual(self.ollama.pulls(), [])

    def test_L5_09_operation_sans_signe_de_vie_dite_interrompue(self):
        out = L.demander_telechargement(self.desk, self.demande(), ADMIN[0])
        self.desk.db.execute("UPDATE ia_locale56108_operations SET etat='en_cours', battement=0 WHERE id=?", (out['operation']['id'],))
        self.desk.db.commit()
        self.assertEqual(L.operation(self.desk, out['operation']['id'])['etat'], 'interrompu')
        L.demander_telechargement(self.desk, self.demande(cle='cle-telechargement-0003', modele='qwen3:14b'), ADMIN[0])   # plus de blocage


# ================================================================================ L6 : réglages, essai, retour
class Reglages(Base):
    modeles = INSTALLES_FORTE + (modele('bge-m3:latest', 0.57, 1.2, ('embedding',), famille='bert'),)

    def appliquer(self, **plus):
        data = {'cle': plus.pop('cle', 'cle-appliquer-00001'), 'version': L.version(self.desk), 'rapide': 'qwen3:8b', 'analyse': 'qwen3:32b',
                'controle': 'mistral-small3.2:24b', **plus}
        return L.appliquer(self.desk, data, ADMIN[0], self.env)

    def test_L6_01_repartition_appliquee_et_reversible(self):
        avant = {r: self.desk.settings('model:role:' + r, '') for r in ('fast', 'complex', 'control')}
        out = self.appliquer(conseil='local_equilibre')
        self.assertIn('répartition des modèles', out['appliques'])
        self.assertIn('préréglage du conseil', out['appliques'])
        self.assertEqual(self.desk.settings('model:role:complex'), 'qwen3:32b')
        self.assertEqual(self.desk.settings('model:role:control'), 'mistral-small3.2:24b')
        from agent import conseil56108 as K
        self.assertEqual((K.reglages(self.desk)['mode'], K.reglages(self.desk)['prereglage']), ('prereglage', 'local_equilibre'))
        self.assertEqual(L.version(self.desk), 1)
        r = L.revenir(self.desk, {'cle': 'cle-revenir-000001', 'version': 1}, ADMIN[0], self.env)
        self.assertEqual(r['ecarts'], [])
        self.assertEqual({k: self.desk.settings('model:role:' + k, '') for k in avant}, avant)
        self.assertEqual(K.reglages(self.desk)['mode'], 'auto')
        self.assertIsNone(L.derniere_configuration(self.desk))

    def test_L6_02_version_perimee_409_et_idempotence(self):
        out = self.appliquer()
        self.assertEqual(self.appliquer()['version'], out['version'])                   # même clé rejouée : même réponse
        with self.assertRaisesRegex(Stop, 'version_perimee'):
            self.appliquer(cle='cle-appliquer-00002', version=0)
        res, corps = self.appel('appliquer', {'cle': 'cle-appliquer-00003', 'version': 0, 'analyse': 'qwen3:32b'})
        self.assertEqual(res['status'], '409 Conflict')

    def test_L6_03_modele_cloud_ou_non_installe_refuse(self):
        with self.assertRaisesRegex(Stop, 'modele_cloud_refuse'):
            self.appliquer(analyse='gpt-oss:120b-cloud')
        with self.assertRaisesRegex(Stop, 'modele_non_installe'):
            self.appliquer(analyse='qwen3:14b')
        with self.assertRaisesRegex(Stop, 'modele_embedding_requis'):
            self.appliquer(embeddings='qwen3:8b', confirmer_embeddings=True)
        self.assertEqual(self.desk.settings('model:role:complex', ''), '')

    def test_L6_04_embeddings_changement_annonce_et_confirme(self):
        with self.assertRaisesRegex(Stop, 'confirmation_embeddings_requise'):
            self.appliquer(embeddings='bge-m3:latest')
        self.assertEqual(json.loads(self.config.read_text(encoding='utf-8'))['rag']['embedding_model'], 'qwen3-embedding:0.6b')
        out = self.appliquer(cle='cle-appliquer-00004', embeddings='bge-m3:latest', confirmer_embeddings=True)
        self.assertIn('recalculés au fil de l’indexation', out['reindexation'])
        self.assertEqual(json.loads(self.config.read_text(encoding='utf-8'))['rag']['embedding_model'], 'bge-m3:latest')
        self.assertFalse(self.desk.db.execute("SELECT COUNT(*) FROM jobs WHERE kind LIKE '%index%'").fetchone()[0])   # rien de lancé
        L.revenir(self.desk, {'cle': 'cle-revenir-000002', 'version': L.version(self.desk)}, ADMIN[0], self.env)
        self.assertEqual(json.loads(self.config.read_text(encoding='utf-8'))['rag']['embedding_model'], 'qwen3-embedding:0.6b')

    def test_L6_05_adresse_du_reseau_enregistree_puis_retablie(self):
        autre = FauxOllama(INSTALLES_FORTE)
        self.addCleanup(autre.arreter)
        out = self.appliquer(adresse=autre.url)
        self.assertIn('adresse d’Ollama', out['appliques'])
        self.assertEqual(json.loads(self.config.read_text(encoding='utf-8'))['ollama']['url'], autre.url)
        L.revenir(self.desk, {'cle': 'cle-revenir-000003', 'version': L.version(self.desk)}, ADMIN[0], self.env)
        self.assertEqual(json.loads(self.config.read_text(encoding='utf-8'))['ollama']['url'], self.ollama.url)

    def test_L6_06_essai_court_duree_mesuree(self):
        out = self.appliquer(essai=True)
        op = L.operation(self.desk, out['essai']['id'])
        lignes = op['resultat']['lignes']
        self.assertEqual(op['etat'], 'termine')
        self.assertTrue(all(x['ok'] and x['secondes'] is not None for x in lignes))
        self.assertIn('embeddings', [x['role'] for x in lignes])
        generes = [x for x in self.ollama.journal if x[:2] == ('POST', '/api/generate')]
        self.assertEqual({x[2] for x in generes}, {'qwen3:8b', 'qwen3:32b', 'mistral-small3.2:24b'})

    def test_L6_07_essai_respecte_la_pause_de_l_ia(self):
        with patch('agent.arret5643.reason_to_stop', return_value='pause_ia'):
            out = L.lancer_essai(self.desk, {'cle': 'cle-essai-pause-01'}, ADMIN[0])
        op = L.operation(self.desk, out['operation']['id'])
        self.assertEqual(op['etat'], 'erreur')
        self.assertTrue(all(x['erreur'] == 'pause_ia' for x in op['resultat']['lignes']))
        self.assertFalse([x for x in self.ollama.journal if x[:2] == ('POST', '/api/generate')])

    def test_L6_08_retour_sans_configuration(self):
        with self.assertRaisesRegex(Stop, 'aucune_configuration'):
            L.revenir(self.desk, {'cle': 'cle-revenir-000004', 'version': 0}, ADMIN[0], self.env)


# ================================================================================ L7 : routes
class Routes(Base):
    modeles = INSTALLES_FORTE

    def test_L7_01_administrateur_seulement(self):
        for route, corps in (('etat', None), ('operation', None), ('telecharger', {'cle': 'cle-route-000001'}), ('appliquer', {}), ('revenir', {}),
                             ('essai', {}), ('annuler', {})):
            with self.subTest(route=route):
                res, data = self.appel(route, corps, who=AVOCAT)
                self.assertEqual(res['status'], '403 Forbidden')
        from agent import standalone_auth
        self.assertFalse(standalone_auth.allowed('avocat', 'POST', '/api440/' + L.PREFIXE + 'telecharger'))
        self.assertTrue(standalone_auth.allowed('administrateur', 'POST', '/api440/' + L.PREFIXE + 'telecharger'))
        self.assertFalse(L.allowed('avocat', 'GET', L.PREFIXE + 'etat'))
        self.assertTrue(L.allowed('administrateur', 'GET', L.PREFIXE + 'etat'))
        self.assertFalse(L.allowed('administrateur', 'GET', L.PREFIXE + 'telecharger'))
        self.assertEqual(self.ollama.pulls(), [])

    def test_L7_01_mutation_sans_le_controle_du_role(self):
        with muter(L, "    if role != 'administrateur':\n        return _erreur('role_insuffisant')", "    pass") as copie:
            raw = b'{}'
            env = {'HTTP_X_AXIORHUB_USER': AVOCAT[0], 'HTTP_X_AXIORHUB_ROLE': AVOCAT[1], 'CONTENT_LENGTH': '2', 'wsgi.input': io.BytesIO(raw)}
            res = copie.api(env, self.desk, {'origin': ORIGINE, 'csrf': 'jeton'}, '/p', L.PREFIXE + 'etat', {}, 'GET')
        self.assertEqual(res['status'], '404 Not Found')     # la seconde barrière (allowed) tient ; sans les deux, l'état serait servi
        with muter(L, "    if role != 'administrateur':\n        return False", "    pass") as copie2:
            self.assertTrue(copie2.allowed('avocat', 'POST', L.PREFIXE + 'telecharger'))

    def test_L7_02_get_sans_ecriture_no_store_404(self):
        avant = self.changements()
        res, data = self.appel('etat')
        self.assertEqual(res['status'], '200 OK')
        self.assertIn(('Cache-Control', 'private, no-store'), res['headers'])
        self.assertTrue(data['detection']['joignable'])
        self.assertEqual(self.changements(), avant)                                  # R05 : aucune écriture pendant la lecture
        res, data = self.appel('operation', args={'id': 'inconnue'})
        self.assertEqual(res['status'], '404 Not Found')
        res, data = self.appel('inconnue')
        self.assertEqual(res['status'], '404 Not Found')
        res, data = self.appel('etat', args={'adresse': 'http://8.8.8.8:11434'})
        self.assertEqual((res['status'], data['error']), ('400 Bad Request', 'adresse_ollama_publique_refusee'))

    def test_L7_03_post_csrf_et_corps_borne(self):
        raw = json.dumps({'cle': 'cle-route-000002'}).encode()
        env = {'HTTP_X_AXIORHUB_USER': ADMIN[0], 'HTTP_X_AXIORHUB_ROLE': ADMIN[1], 'HTTP_ORIGIN': 'https://autre.example.test', 'HTTP_X_CSRF_TOKEN': 'jeton',
               'CONTENT_LENGTH': str(len(raw)), 'CONTENT_TYPE': 'application/json', 'wsgi.input': io.BytesIO(raw)}
        res = L.api(env, self.desk, {'origin': ORIGINE, 'csrf': 'jeton'}, '/p', L.PREFIXE + 'telecharger', {}, 'POST')
        self.assertEqual(json.loads(res['body'])['error'], 'origine_ou_csrf_refuse')
        res, data = self.appel('telecharger', {'cle': 'x' * 10, 'modele': 'q' * 9000})
        self.assertEqual(res['status'], '413 Payload Too Large')
        self.assertEqual(self.ollama.pulls(), [])

    def test_L7_04_parcours_complet_par_les_routes(self):
        res, d = self.appel('etat')
        self.assertEqual(d['machine']['classe'], 'legere')                              # sonde fictive : 16 Go, sans carte
        res, r = self.appel('telecharger', {'cle': 'cle-route-000003', 'modele': 'qwen3:4b', 'confirme': True})
        self.assertEqual(res['status'], '200 OK', r)
        res, op = self.appel('operation', args={'id': r['operation']['id']})
        self.assertEqual(op['etat'], 'termine')

    def test_L7_05_section_et_guide(self):
        html = L.section_html(self.desk, '/p', 'administrateur')
        self.assertIn('id="ia-locale56108"', html)
        self.assertIn('/p/static/v56108-ia-locale.js', html)
        self.assertNotIn('onclick', html)
        self.assertIn('Seul l’administrateur', L.section_html(self.desk, '/p', 'avocat'))
        self.assertNotIn('<script', L.section_html(self.desk, '/p', 'avocat'))
        from agent import guide5631, web440, shell501, export56108
        self.assertTrue(guide5631._carte_disponible('ia_locale'))
        self.assertIn('/static/v56108-ia-locale.js', web440.STATIC)
        self.assertIn('v56108-ia-locale.css', shell501.BUNDLE)
        exclues = {t for t, _m in export56108.EXPORT_EXCLUES}
        self.assertTrue({'ia_locale56108_operations', 'ia_locale56108_configurations'} <= exclues)
        css = (ROOT / 'agent' / 'static' / 'app520.css').read_text(encoding='utf-8')
        self.assertIn('.l108-etape', css)


# ================================================================================ L8 : textes en anglais, allemand, espagnol
class Langues(Base):
    def test_L8_01_section_traduite(self):
        from agent import ecrans5632 as E
        import test_v56107_langues as T
        html = L.section_html(self.desk, '/p', 'administrateur') + L.section_html(self.desk, '/p', 'avocat')
        for lang in T.LANGUES:
            self.assertEqual(T.reste_francais(T.visibles(E.translate_html(html, lang))), [], lang)

    def test_L8_02_script_toutes_les_phrases_au_dictionnaire(self):
        from agent import ecrans5632 as E
        import test_v56107_langues as T
        manquants, vus = [], 0
        for no, s in T.litteraux_js(ROOT / 'agent' / 'static' / 'v56108-ia-locale.js'):
            if not T.texte_visible_js(s):
                continue
            vus += 1
            for lang in T.LANGUES:
                if E.phrase(s, E.dictionary(lang)) is None:
                    manquants.append('%d (%s) %s' % (no, lang, s))
        self.assertGreater(vus, 20)
        self.assertEqual(manquants, [])

    def test_L8_03_textes_du_serveur_traduits(self):
        from agent import ecrans5632 as E
        textes = list(L.MESSAGES.values()) + list(L.LIBELLES_ROLES.values())
        sonde = FausseSonde()
        self.desk.c['ollama']['url'] = INJOIGNABLE
        with patch('agent.ia_locale56108.CANDIDATS', ()):
            for p, t in (('linux', False), ('linux', True), ('windows', False), ('windows', True)):
                textes.append(L.detecter(self.desk, sonde=FausseSonde(plateforme=p, trouve=t))['message'])
        def injoignable(url, timeout=4):     # adresse privée fictive : aucune connexion tentée
            raise Stop('connexion_http_indisponible')
        with patch('agent.ia_locale56108._client', injoignable):
            textes.append(L.detecter(self.desk, adresse='http://10.0.0.77:11434', sonde=sonde)['message'])
        for ram, vram in ((64, 24), (32, 12), (64, None), (16, None), (None, None)):
            textes.append(L.classer(ram, vram)[2])
        e = L.etat(self.desk, sonde=sonde)
        textes += [e['reseau'], L.catalogue()['memoire'], L._annonce_reindexation(self.desk, 'bge-m3')]
        textes += [x['libelle'] for x in L.catalogue()['licences'].values()]
        for lang in ('en', 'de', 'es'):
            table = E.dictionary(lang)
            for t in textes:
                with self.subTest(lang=lang, t=t[:60]):
                    self.assertIsNotNone(E.phrase(t, table))


if __name__ == '__main__':
    unittest.main()
