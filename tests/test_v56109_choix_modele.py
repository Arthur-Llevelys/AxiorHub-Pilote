"""5.6.109 (lot T) : choix exact du modèle pour chaque fonction, niveau de contexte transmis par fonction.

Faux transports seulement : ``Transport`` remplace ``HTTP`` dans ``agent.model`` et ``agent.economie569`` ; il répond comme un moteur Ollama
local (adresse 127.0.0.1) ou comme le faux Mistral de ``test_v5696_mistral`` (adresse https). Aucun réseau, aucune clé réelle, aucun modèle
réel ; données fictives (DURAND, DOS-001, example.test). Les garde-fous ont leur contre-exemple par mutation EN MÉMOIRE (``patch``).
"""
import importlib.util
import json
from pathlib import Path
import sqlite3
import tempfile
import unittest
from unittest.mock import patch

import test_v5696_mistral as M
from agent import ai_gateway, catalogue5682, choix_modele56109 as CM, docrequest520, ia540, politique5682, routage5682, standalone_auth
from agent.common import Stop
from agent.erreurs5634 import explain
from agent.model import Model, modele_local_seul, routed_config
from support567 import authorize_external

ROOT = Path(__file__).resolve().parents[1]
ADMIN = 'admin@example.test'
LOCAL = 'modele-local:12b'
LOCAL2 = 'autre-local:4b'
MODELES_LOCAUX = {'models': [{'name': LOCAL, 'size': 7_000_000_000}, {'name': LOCAL2, 'size': 3_000_000_000}]}


class FauxOllama:
    def __init__(self):
        self.chats, self.tags = [], json.loads(json.dumps(MODELES_LOCAUX))

    def __call__(self, methode, chemin, corps=None):
        if chemin == '/api/tags':
            return self.tags
        if chemin == '/api/show':
            if corps and corps.get('model') not in [m['name'] for m in self.tags['models']]:
                raise Stop('http_404')
            return {'capabilities': ['completion']}
        self.chats.append(corps)
        return {'done': True, 'done_reason': 'stop', 'message': {'content': '{"ok": true}'}, 'prompt_eval_count': 10, 'eval_count': 5}


class Base(M.Base):
    def setUp(self):
        super().setUp()
        self.ollama = FauxOllama()
        ollama, mistral = self.ollama, self.mistral

        class Transport:
            def __init__(self, base, **kw):
                self.base, self.headers = str(base), {}

            def json(self, methode, chemin, corps=None):
                if self.base.startswith('http://127.') or self.base.startswith('http://localhost'):
                    return ollama(methode, chemin, corps)
                return mistral(methode, chemin, self.headers.get('Authorization', '')[len('Bearer '):], corps)
        for target in ('agent.model.HTTP', 'agent.economie569.HTTP'):
            p = patch(target, Transport)
            p.start()
            self.addCleanup(p.stop)
        self.desk.c['ollama'] = {**(self.desk.c.get('ollama') or {}), 'url': 'http://127.0.0.1:11434', 'model': LOCAL}

    def mixte(self):
        """Mode mixte (routes écrites par ``ia540.apply_mixed``) : tri, pièces jointes, contrôle en local ; le reste vers Mistral."""
        self.en_service(mode=False)
        authorize_external(self.desk.c)
        ia540.apply_mixed(self.desk, 'mistral', 'mistral-large-4', LOCAL, test=False)
        authorize_external(self.desk.c)

    def posts_mistral(self):
        return [c for c in self.mistral.appels if c[0] == 'POST']

    def appel(self, purpose):
        cfg = routed_config(self.desk.c, purpose)
        return cfg, Model(cfg).complete([{'role': 'system', 'content': 'Consigne fictive.'}, {'role': 'user', 'content': 'Question fictive (DURAND).'}], 0, 200)


def resolution(config):
    out = {}
    for p in ai_gateway.PURPOSES:
        try:
            c = routed_config(config, p)
            out[p] = (c.get('provider_id'), c.get('provider_type'), c.get('model'), bool(c.get('routeur5682')), bool(c.get('secours')))
        except Stop as ex:
            out[p] = ('stop', str(ex))
    return out


# ================================================================================================ mise à jour : aucun changement
class MiseAJour(Base):
    def test_T01_sans_choix_resolution_identique_dans_chaque_mode(self):
        """Mise à jour sans choix : pour chaque mode, la résolution est celle du chemin historique (bloc lot T neutralisé), fonction par fonction."""
        def comparer(nom):
            with self.subTest(mode=nom):
                avec = resolution(self.desk.c)
                with patch('agent.choix_modele56109.resoudre', lambda *a: None):
                    sans = resolution(self.desk.c)
                self.assertEqual(avec, sans)
                self.assertEqual(CM.lire(self.desk.c)['fonctions'], {})
        comparer('routes_tout_local')
        self.mixte()
        comparer('mixte')
        politique5682.save(self.desk, {'mode': 'local'}, ADMIN)
        comparer('local')
        politique5682.save(self.desk, {'mode': 'auto', 'fournisseurs': ['ollama', 'mistral'], 'consentement': True}, ADMIN)
        comparer('auto')
        politique5682.save(self.desk, {'mode': 'mistral', 'consentement': True}, ADMIN)
        comparer('mistral')

    def test_T01_routes_heritees_et_mixte_ne_deviennent_pas_des_choix(self):
        self.mixte()
        self.desk.setting('ai:route:assistant', {'provider': 'mistral', 'model': 'mistral-small-latest'})
        # valeur malformée ou sans marque explicite : jamais lue comme un choix épinglé
        self.desk.setting(CM.KEY, {'version': 3, 'fonctions': {'assistant': {'connexion': 'mistral', 'modele': 'mistral-small-latest'}}})
        self.assertIsNone(CM.choix_de(self.desk.c, 'assistant'))
        for p in ai_gateway.PURPOSES:
            self.assertIsNone(CM.resoudre(self.desk.c, p, ai_gateway.PURPOSES[p][1]))
        self.assertEqual(CM.nombre_epingles(self.desk.c), 0)
        self.assertNotIn('réglée(s) à la main', ia540.current(self.desk)['label'])


# ================================================================================================ le choix est respecté exactement
class Respecte(Base):
    def test_T02_choix_local_en_mode_mistral_exclusif(self):
        self.en_service(mode=True)
        CM.enregistrer(self.desk, ADMIN, 'mail_triage', 'ollama', LOCAL2)
        avant = len(self.posts_mistral())
        cfg, _ = self.appel('mail_triage')
        self.assertEqual((cfg['provider_id'], cfg['model']), ('ollama', LOCAL2))
        self.assertEqual([c['model'] for c in self.ollama.chats], [LOCAL2])
        self.assertEqual(len(self.posts_mistral()), avant)          # aucun autre modèle
        for cle in CM.REPLIS:
            self.assertNotIn(cle, cfg)
        # les autres fonctions restent au mode général (routeur Mistral)
        self.assertTrue(routed_config(self.desk.c, 'assistant').get('routeur5682'))

    def test_T02_mutation_sans_exception_de_la_garde_le_local_est_refuse(self):
        self.en_service(mode=True)
        CM.enregistrer(self.desk, ADMIN, 'mail_triage', 'ollama', LOCAL2)
        with patch('agent.choix_modele56109.local_epingle_admis', lambda cfg: False):
            with self.assertRaises(Stop) as ctx:
                self.appel('mail_triage')
        self.assertEqual(str(ctx.exception), 'fournisseur_interdit_par_politique')

    def test_T02_choix_mistral_pour_une_fonction_locale_du_mode_mixte(self):
        self.mixte()
        self.assertEqual(routed_config(self.desk.c, 'mail_triage')['provider_id'], 'ollama')
        CM.enregistrer(self.desk, ADMIN, 'mail_triage', 'mistral', 'mistral-small-latest')
        authorize_external(self.desk.c)
        cfg, out = self.appel('mail_triage')
        self.assertEqual(out, 'OK')
        envoyes = [c[3].get('model') for c in self.posts_mistral()]
        self.assertEqual(envoyes, ['mistral-small-latest'])
        self.assertEqual(self.ollama.chats, [])
        row = self.desk.db.execute("SELECT fonction,fournisseur,modele,mode,raison_courte,etat FROM decisions_5682 WHERE mode='choix_fixe' ORDER BY id DESC").fetchone()
        self.assertEqual(tuple(row)[:4], ('mail_triage', 'mistral', 'mistral-small-latest', 'choix_fixe'))
        self.assertIn('Choix fixé dans Paramètres', row[4])
        self.assertEqual(row[5], 'terminee')
        self.assertIn('1 fonction(s) réglée(s) à la main', ia540.current(self.desk)['label'])

    def test_T02_choix_mistral_precis_en_mode_mistral_sans_routeur(self):
        self.en_service(mode=True)
        CM.enregistrer(self.desk, ADMIN, 'assistant', 'mistral', 'mistral-small-latest')
        cfg, out = self.appel('assistant')
        self.assertNotIn('routeur5682', cfg)
        self.assertEqual(out, 'OK')
        self.assertEqual([c[3].get('model') for c in self.posts_mistral()], ['mistral-small-latest'])
        self.assertEqual(self.ollama.chats, [])

    def test_T06_detail_de_la_mission_par_appel(self):
        from agent import trace480
        self.mixte()
        CM.enregistrer(self.desk, ADMIN, 'mail_triage', 'mistral', 'mistral-small-latest')
        authorize_external(self.desk.c)
        trace480.begin('job-fictif-t06', 'mail', 'DOS-001')
        self.addCleanup(trace480.end)
        trace480.note(contexte56109={'fonction': 'mail_triage', 'niveau': 'reduit', 'categories': {'extraits': {'nombre': 4, 'caracteres': 7300}},
                                     'ecarte': '8 passage(s) non transmis : contexte réduit.'})
        self.appel('mail_triage')
        lignes = CM.appels_mission(self.desk.c['state_dir'], 'job-fictif-t06')
        self.assertEqual(len(lignes), 1)
        x = lignes[0]
        self.assertEqual((x['fonction'], x['connexion'], x['modele'], x['choix']), ('mail_triage', 'mistral', 'mistral-small-latest', CM.ORIGINE))
        self.assertGreater(x['caracteres'], 0)
        self.assertEqual(x['jetons_estimes'], x['caracteres'] // 3)
        self.assertIn('8 passage(s) non transmis : contexte réduit.', x['texte'])
        self.assertIn('choix fixé dans Paramètres', x['texte'])

    def test_T02_echec_du_modele_epingle_aucune_substitution(self):
        self.mixte()
        CM.enregistrer(self.desk, ADMIN, 'assistant', 'mistral', 'mistral-small-latest')
        authorize_external(self.desk.c)
        self.mistral.refus[M.CLE_EN_SERVICE] = 'reseau'
        cfg = routed_config(self.desk.c, 'assistant')
        model = Model.__new__(Model)
        Model.__init__(model, {**cfg, 'sans_liste5682': True})
        with self.assertRaises(Stop) as ctx:
            model.complete([{'role': 'user', 'content': 'Question fictive.'}], 0, 100)
        self.assertEqual(self.ollama.chats, [])                      # aucun secours local, aucun autre modèle
        d = ctx.exception.detail
        self.assertEqual((d['fonction'], d['modele']), ('Assistant du cabinet', 'mistral-small-latest'))
        texte = explain(str(ctx.exception), d)
        self.assertIn('Assistant du cabinet', texte)
        self.assertIn('aucun autre modèle', texte)

    def test_T02_mutation_verrou_un_repli_attache_est_refuse(self):
        self.mixte()
        CM.enregistrer(self.desk, ADMIN, 'assistant', 'mistral', 'mistral-small-latest')
        authorize_external(self.desk.c)
        cfg = routed_config(self.desk.c, 'assistant')
        cfg['secours'] = {'delay': 1, 'candidates': []}
        with self.assertRaises(Stop) as ctx:
            Model({**cfg, 'sans_liste5682': True}).complete([{'role': 'user', 'content': 'x'}], 0, 50)
        self.assertEqual(str(ctx.exception), 'choix_modele_modifie')

    def test_T03_relance_avec_connexion_choisie_par_l_avocat_prioritaire(self):
        self.mixte()
        CM.enregistrer(self.desk, ADMIN, 'assistant', 'ollama', LOCAL2)
        with politique5682.one_provider('mistral'):
            self.assertIsNone(CM.resoudre(self.desk.c, 'assistant', 'complex'))


# ================================================================================================ intersection avec la politique
class Politique(Base):
    def test_T03_choix_externe_refuse_en_mode_local_a_l_enregistrement(self):
        self.en_service(mode=False)
        politique5682.save(self.desk, {'mode': 'local'}, ADMIN)
        avant_politique = self.desk.settings(politique5682.KEY)
        with self.assertRaises(Stop) as ctx:
            CM.enregistrer(self.desk, ADMIN, 'assistant', 'mistral', 'mistral-small-latest')
        self.assertEqual(str(ctx.exception), 'choix_modele_interdit_local')
        self.assertIsNone(self.desk.settings(CM.KEY))
        self.assertEqual(self.desk.settings(politique5682.KEY), avant_politique)
        self.assertIn('IA locale uniquement', explain('choix_modele_interdit_local'))

    def test_T03_choix_externe_refuse_a_l_execution_rien_n_est_envoye(self):
        self.mixte()
        CM.enregistrer(self.desk, ADMIN, 'assistant', 'mistral', 'mistral-small-latest')
        politique5682.save(self.desk, {'mode': 'local'}, ADMIN)       # passage en « IA locale uniquement » après le choix
        avant = len(self.posts_mistral())
        with self.assertRaises(Stop) as ctx:
            routed_config(self.desk.c, 'assistant')
        self.assertEqual(str(ctx.exception), 'choix_modele_interdit_local')
        self.assertEqual(len(self.posts_mistral()), avant)
        self.assertEqual(self.desk.settings(politique5682.KEY)['mode'], 'local')
        self.assertIn('IA locale uniquement', ia540.local_models_html(self.desk, ''))

    def test_T03_mutation_sans_intersection_l_envoi_partirait(self):
        """Contre-exemple : intersection retirée en mémoire, le même choix part vers Mistral malgré « Tout en local »."""
        self.mixte()
        CM.enregistrer(self.desk, ADMIN, 'assistant', 'mistral', 'mistral-small-latest')
        ia540.set_local(self.desk)
        with self.assertRaises(Stop):
            routed_config(self.desk.c, 'assistant')
        with patch('agent.choix_modele56109.verifier_autorise', lambda *a: 'mistral'):
            cfg = routed_config(self.desk.c, 'assistant')
        self.assertEqual(cfg['provider_id'], 'mistral')

    def test_T03_mode_exclusif_autre_fournisseur_refuse_et_connexion_desactivee(self):
        self.en_service(mode=True)
        reg = ai_gateway.provider_registry(self.desk.c)
        faux = {**reg['mistral'], 'type': 'anthropic'}
        with self.assertRaises(Stop) as ctx:
            CM.verifier_autorise(self.desk.c, 'claude', faux, 'assistant')
        self.assertEqual(str(ctx.exception), 'choix_modele_interdit_par_politique')
        with self.assertRaises(Stop) as ctx:
            CM.verifier_autorise(self.desk.c, 'mistral', {**reg['mistral'], 'enabled': False}, 'assistant')
        self.assertEqual(str(ctx.exception), 'choix_modele_connexion_inactive')
        with self.assertRaises(Stop) as ctx:
            CM.verifier_autorise(self.desk.c, 'mistral', {**reg['mistral'], 'external_data_allowed': False}, 'assistant')
        self.assertEqual(str(ctx.exception), 'choix_modele_externe_non_autorise')

    def test_T03_local_exige_le_choix_externe_ne_s_applique_pas(self):
        self.mixte()
        CM.enregistrer(self.desk, ADMIN, 'assistant', 'mistral', 'mistral-small-latest')
        with modele_local_seul():
            cfg = routed_config(self.desk.c, 'assistant')
        self.assertEqual(cfg['provider_type'], 'ollama')
        self.assertEqual(cfg['choix56109'], {'applique': False, 'motif': 'local_exige', 'fonction': 'assistant', 'connexion': 'mistral',
                                             'modele': 'mistral-small-latest'})

    def test_T03_dossier_exclu_reste_selon_la_regle_generale(self):
        self.mixte()
        CM.enregistrer(self.desk, ADMIN, 'mail_triage', 'mistral', 'mistral-small-latest')
        authorize_external(self.desk.c)
        self.desk.c['hybrid_routing']['excluded_matters'] = ['DOS-001']
        avant = len(self.posts_mistral())
        cfg = routed_config(self.desk.c, 'mail_triage')
        cfg['external_context'] = {'matter': 'DOS-001'}
        Model(cfg).complete([{'role': 'user', 'content': json.dumps({'matter': 'DOS-001', 'texte': 'fictif'})}], 0, 50)
        self.assertEqual(len(self.posts_mistral()), avant)           # rien n'est parti vers le modèle externe choisi
        self.assertEqual([c['model'] for c in self.ollama.chats], [LOCAL])   # règle générale du tri : local


# ================================================================================================ modèle vérifié
class ModeleVerifie(Base):
    def test_T04_modele_absent_du_catalogue_refuse(self):
        self.mixte()
        with self.assertRaises(Stop) as ctx:
            CM.enregistrer(self.desk, ADMIN, 'assistant', 'mistral', 'modele-inexistant-9')
        self.assertEqual(str(ctx.exception), 'modele_absent_du_catalogue')
        self.assertIsNone(self.desk.settings(CM.KEY))

    def test_T04_modele_retire_du_catalogue_modele_a_choisir_sans_substitution(self):
        self.mixte()
        CM.enregistrer(self.desk, ADMIN, 'assistant', 'mistral', 'mistral-small-latest')
        self.catalogue([m for m in M.MODELES if m['id'] != 'mistral-small-latest'])
        with self.assertRaises(Stop) as ctx:
            routed_config(self.desk.c, 'assistant')
        self.assertEqual(str(ctx.exception), 'choix_modele_a_choisir')
        self.assertEqual(ctx.exception.detail['modele'], 'mistral-small-latest')
        self.assertIn('Modèle à choisir', ia540.local_models_html(self.desk, ''))

    def catalogue(self, modeles):
        self.mistral.modeles = list(modeles)
        catalogue5682.refresh(self.desk, 'mistral')

    def test_T04_modele_local_non_installe_puis_desinstalle(self):
        with self.assertRaises(Stop) as ctx:
            CM.enregistrer(self.desk, ADMIN, 'mail_triage', 'ollama', 'absent:1b')
        self.assertEqual(str(ctx.exception), 'choix_modele_non_installe')
        CM.enregistrer(self.desk, ADMIN, 'mail_triage', 'ollama', LOCAL2)
        self.ollama.tags['models'] = [m for m in self.ollama.tags['models'] if m['name'] != LOCAL2]
        self.assertIn('Modèle à choisir', ia540.local_models_html(self.desk, ''))
        cfg = routed_config(self.desk.c, 'mail_triage')
        with self.assertRaises(Stop) as ctx:
            Model(cfg)
        self.assertEqual(str(ctx.exception), 'modele_ollama_absent')
        self.assertEqual(self.ollama.chats, [])

    def test_T04_modele_cloud_refuse_comme_local(self):
        self.ollama.tags['models'].append({'name': 'gros:cloud', 'size': 1})
        with self.assertRaises(Stop) as ctx:
            CM.enregistrer(self.desk, ADMIN, 'mail_triage', 'ollama', 'gros:cloud')
        self.assertEqual(str(ctx.exception), 'choix_modele_distant')


# ================================================================================================ écran, droits, version
class Ecran(Base):
    def env(self, role='administrateur', csrf='jeton', origin='https://axiorhub.example.test', corps=None):
        import io
        data = json.dumps(corps or {}).encode()
        return {'HTTP_X_AXIORHUB_USER': ADMIN, 'HTTP_X_AXIORHUB_ROLE': role, 'HTTP_ORIGIN': origin, 'HTTP_X_CSRF_TOKEN': csrf,
                'CONTENT_TYPE': 'application/json', 'CONTENT_LENGTH': str(len(data)), 'wsgi.input': io.BytesIO(data)}

    AUTH = {'origin': 'https://axiorhub.example.test', 'csrf': 'jeton'}

    def test_T05_non_administrateur_refuse(self):
        self.assertFalse(standalone_auth.allowed('avocat', 'POST', '/api440/m56109/choix-modele/enregistrer'))
        self.assertFalse(standalone_auth.allowed('assistant', 'POST', '/api440/m56109/choix-modele/remettre'))
        self.assertTrue(standalone_auth.allowed('administrateur', 'POST', '/api440/m56109/choix-modele/enregistrer'))
        out = CM.api(self.env('avocat', corps={'fonction': 'assistant', 'connexion': 'ollama', 'modele': LOCAL2}), self.desk, self.AUTH, '',
                     'm56109/choix-modele/enregistrer', {}, 'POST')
        self.assertEqual(out['status'], '403 Forbidden')
        self.assertIsNone(self.desk.settings(CM.KEY))
        out = CM.api(self.env('avocat'), self.desk, self.AUTH, '', 'm56109/choix-modele/etat', {}, 'GET')
        self.assertEqual(out['status'], '404 Not Found')

    def test_T05_csrf_et_version_perimee(self):
        out = CM.api(self.env(csrf='faux', corps={'fonction': 'mail_triage', 'connexion': 'ollama', 'modele': LOCAL2, 'version': 0}), self.desk, self.AUTH, '',
                     'm56109/choix-modele/enregistrer', {}, 'POST')
        self.assertEqual(out['status'], '403 Forbidden')
        self.assertIsNone(self.desk.settings(CM.KEY))
        ok = CM.api(self.env(corps={'fonction': 'mail_triage', 'connexion': 'ollama', 'modele': LOCAL2, 'contexte': 'reduit', 'version': 0}), self.desk, self.AUTH, '',
                    'm56109/choix-modele/enregistrer', {}, 'POST')
        self.assertEqual(ok['status'], '200 OK')
        self.assertIn(('Cache-Control', 'private, no-store'), ok['headers'])
        rejoue = CM.api(self.env(corps={'fonction': 'mail_triage', 'connexion': 'ollama', 'modele': LOCAL, 'version': 0}), self.desk, self.AUTH, '',
                        'm56109/choix-modele/enregistrer', {}, 'POST')
        self.assertEqual(rejoue['status'], '409 Conflict')
        self.assertEqual(CM.choix_de(self.desk.c, 'mail_triage')['modele'], LOCAL2)
        audit = [json.loads(r[0]) for r in self.desk.db.execute("SELECT data FROM audit WHERE action='routage_ia_modifie'")]
        self.assertTrue(any(a.get('choix56109') and a.get('model') == LOCAL2 for a in audit))

    def test_T05_affichage_du_tableau_et_remise(self):
        self.mixte()
        CM.enregistrer(self.desk, ADMIN, 'mail_drafting', 'ollama', LOCAL2, 'reduit')
        html = ia540.local_models_html(self.desk, '')
        self.assertIn('data-api="m56109/choix-modele/enregistrer"', html)
        self.assertIn('Choisi par vous', html)
        self.assertIn('Selon le mode général', html)
        self.assertIn('local · ' + LOCAL2, html)
        self.assertIn('Tout remettre selon le mode général', html)
        self.assertIn('data-api="m56109/navigateur/modele"', html)        # navigateur : son mécanisme, même tableau
        self.assertIn('<label class="cm109-l">Connexion<select', html)
        self.assertIn('Contexte transmis', html)
        self.assertNotIn('<script', html)
        CM.remettre(self.desk, ADMIN)
        self.assertEqual(CM.lire(self.desk.c)['fonctions'], {})
        self.assertEqual(routed_config(self.desk.c, 'mail_drafting')['provider_id'], 'mistral')

    def test_T05_ancienne_presentation_derriere_l_interrupteur(self):
        with patch('agent.interface56100.active', lambda desk, owner='cabinet': False):
            html = ia540.local_models_html(self.desk, '')
        self.assertNotIn('m56109/choix-modele', html)
        self.assertIn('<th>Modèle utilisé</th>', html)


# ================================================================================================ contexte transmis par fonction
class FauxIndex:
    def __init__(self, *a, **k):
        pass

    def ranked_chunks(self, matter, terms, limit=10, **k):
        return [((i, 'src%d' % i, '/Dossiers/DOS-001/piece%02d.pdf' % i, '', '2026-0%d-01' % (1 + i % 9), 'pièce', '', 'Passage fictif %d ' % i + 'x' * 1700), 1.0 - i / 100)
                for i in range(limit)], {}

    def source_confidential(self, matter, sid):
        return sid == 'src2'


class FauxMessage:
    def __init__(self, n):
        self.sender, self.subject, self.text = 'martin%d@example.test' % n, 'Objet fictif %d' % n, 'Corps fictif ' * 150


class FauxBoite:
    def __init__(self, *a, **k):
        pass

    def fetch(self, folder, uid, headers_only=False):
        return FauxMessage(int(uid))

    def close(self):
        pass


class FauxDav:
    def stat(self, path):
        return path

    def download(self, item):
        return b'contenu'


class FauxDesk:
    def __init__(self, tmp, reglage=None):
        self.c = {'state_dir': tmp, 'mail': {}, 'rag': {}, 'ollama': {}, 'documents': {}}
        if reglage is not None:
            self.c[CM.CONFIG_KEY] = reglage
        self.db = sqlite3.connect(':memory:')
        self.db.row_factory = sqlite3.Row
        self.db.executescript('''CREATE TABLE portfolio_mail_links(folder TEXT, uid TEXT, sender TEXT, mail_date TEXT, matter TEXT);
            CREATE TABLE calendar_cache(title TEXT, starts TEXT, matter TEXT);
            CREATE TABLE legal_memory_records(title TEXT, content TEXT, sources TEXT, status TEXT, matter TEXT, updated TEXT);''')
        from datetime import datetime, timedelta, timezone
        now = datetime.now(timezone.utc)
        for i in range(12):
            self.db.execute('INSERT INTO portfolio_mail_links VALUES(?,?,?,?,?)', ('INBOX', str(i + 1), 'x', '2026-11-%02d' % (20 - i), 'DOS-001'))
        for i in range(-10, 14):
            self.db.execute('INSERT INTO calendar_cache VALUES(?,?,?)', ('Échéance fictive %d' % i, (now + timedelta(days=i * 3)).isoformat(), 'DOS-001'))
        for i in range(25):
            self.db.execute('INSERT INTO legal_memory_records VALUES(?,?,?,?,?,?)', ('Fait fictif %d' % i, 'Détail ' * 40, json.dumps(['/Dossiers/DOS-001/p%d.pdf' % i] if i % 3 else []),
                                                                                  'pinned' if i in (20, 21) else 'validated', 'DOS-001', '2026-10-%02d' % (28 - i)))
        self.db.commit()


MATTER = {'id': 'DOS-001', 'path': '/Dossiers/DOS-001', 'client_name': 'DURAND', 'name': 'DURAND'}
INVENTAIRE = [{'path': '/Dossiers/DOS-001/fichier%03d.pdf' % i, 'modified': '2026-%02d-01' % (1 + i % 12)} for i in range(95)]


def avant_lot_t():
    """``docrequest520`` tel qu'il était avant le lot T (dépôt Git local, lecture seule) : référence octet pour octet."""
    import subprocess
    # commit d'intégration précédant le lot T (fixe : après l'intégration, HEAD contiendra le lot T lui-même) ; absent (clone partiel) : test sauté
    try:
        source = subprocess.run(['git', 'show', 'de7c571:agent/docrequest520.py'], cwd=ROOT, capture_output=True, check=True).stdout
    except (OSError, subprocess.CalledProcessError):
        raise unittest.SkipTest('version de référence de docrequest520 indisponible (dépôt sans historique)')
    spec = importlib.util.spec_from_loader('agent.docrequest520_avant_lot_t', loader=None)
    module = importlib.util.module_from_spec(spec)
    module.__package__ = 'agent'
    exec(compile(source, 'docrequest520_avant', 'exec'), module.__dict__)
    return module


class Contexte(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        for target, value in (('agent.index.DocumentIndex', FauxIndex), ('agent.mailbox.Mailbox', FauxBoite),
                              ('agent.document_projects._inventory', lambda client, path: list(INVENTAIRE)),
                              ('agent.documents.extract', lambda raw, name, cfg: 'Texte du document choisi par l’avocat (fictif).'),
                              ('agent.live430.progress', lambda *a, **k: None)):
            p = patch(target, value)
            p.start()
            self.addCleanup(p.stop)
        from agent import trace480
        trace480.begin('job-fictif', 'mail', 'DOS-001')
        self.addCleanup(trace480.end)

    def ctx(self, module, reglage=None, fonction='mail_drafting'):
        desk = FauxDesk(self.tmp.name, reglage)
        kwargs = {'fonction': fonction} if module is docrequest520 else {}
        return module.context(desk, MATTER, 'Demande fictive DURAND', FauxDav(), (), ['/Dossiers/DOS-001/choisi.pdf'], **kwargs)

    @staticmethod
    def reduit(fonction='mail_drafting'):
        return {'version': 1, 'fonctions': {fonction: {'contexte': 'reduit'}}}

    def test_T07_mise_a_jour_sans_reglage_contexte_identique_octet_pour_octet(self):
        avant = avant_lot_t()
        ref = self.ctx(avant)
        for reglage in (None, {'version': 2, 'fonctions': {'mail_drafting': {'contexte': 'normal'}}}, self.reduit('document_drafting')):
            with self.subTest(reglage=reglage):
                nouveau = self.ctx(docrequest520, reglage)
                self.assertEqual(json.dumps(nouveau, ensure_ascii=False, sort_keys=True), json.dumps(ref, ensure_ascii=False, sort_keys=True))

    def test_T07_reduit_bornes_documents_choisis_conserves_ecart_affiche(self):
        normal, notes_n = self.ctx(docrequest520)
        reduit, notes_r = self.ctx(docrequest520, self.reduit())
        taille = lambda c: len(json.dumps(c, ensure_ascii=False))
        self.assertEqual([len(normal[k]) for k in ('fichiers', 'extraits', 'courriels', 'agenda', 'faits')], [80, 12, 12, 20, 25])
        self.assertEqual([len(reduit[k]) for k in ('fichiers', 'extraits', 'courriels', 'agenda', 'faits')], [20, 4, 4, 8, 10])
        self.assertEqual(reduit['pieces_jointes'], normal['pieces_jointes'])     # choisi par l'avocat : jamais retiré
        self.assertEqual(reduit['pieces_jointes'][0]['fichier'], '/Dossiers/DOS-001/choisi.pdf')
        self.assertEqual([x['source'] for x in reduit['extraits']], ['P1', 'P2', 'P3', 'P4'])   # les plus pertinents
        self.assertTrue(reduit['extraits'][2].get('confidentiel'))                              # contrôle de confidentialité gardé
        self.assertTrue(all('sources' in f or 'provenance' in f for f in reduit['faits']))     # provenance gardée
        self.assertEqual([f['fait'] for f in reduit['faits'][:2]], ['Fait fictif 20', 'Fait fictif 21'])   # épinglés d'abord
        self.assertEqual(sum(1 for x in reduit['courriels'] if x['extrait']), 3)
        self.assertEqual(notes_n, [])
        self.assertEqual(notes_r, ['60 nom(s) de fichier, 8 passage(s), 8 courriel(s), 3 extrait(s) de courriel, 12 événement(s) et 15 fait(s) validé(s) non transmis : contexte réduit.'])
        self.assertLess(taille(reduit), taille(normal) * 0.45)
        self.mesures = (taille(normal), taille(reduit))
        print('\n[lot T] contexte assemblé (fictif) : normal %d caractères, réduit %d caractères' % self.mesures)

    def test_T07_reduit_garde_les_prochaines_dates(self):
        from datetime import datetime, timezone
        reduit, _ = self.ctx(docrequest520, self.reduit())
        maintenant = datetime.now(timezone.utc).strftime('%Y-%m-%d %H:%M')
        self.assertTrue(all(x['date'] >= maintenant[:10] for x in reduit['agenda']))

    def test_T07_trace_par_categorie(self):
        from agent import trace480
        self.ctx(docrequest520, self.reduit())
        note = trace480.CTX.get()['notes']['contexte56109']
        self.assertEqual(note['niveau'], 'reduit')
        self.assertEqual(note['categories']['extraits']['nombre'], 4)
        self.assertIn('contexte réduit', note['ecarte'])
        self.assertIn('passages', CM.resume_contexte(note))


class Mesure(Base):
    def test_T07_tailles_premier_et_second_appel_redaction_courriel(self):
        """(e) Rédaction d'un courriel : caractères du premier appel (rédaction) et du second (correction ciblée du contrôle de rédaction)."""
        from agent import maildraft5613
        tmp = tempfile.mkdtemp(dir=self.desk.c['state_dir'])   # sans base : le réglage vient de la configuration du faux bureau
        with patch('agent.index.DocumentIndex', FauxIndex), patch('agent.mailbox.Mailbox', FauxBoite), \
                patch('agent.document_projects._inventory', lambda client, path: list(INVENTAIRE)), patch('agent.live430.progress', lambda *a, **k: None):
            resultats = {}
            for niveau, reglage in (('normal', None), ('reduit', {'version': 1, 'fonctions': {'mail_drafting': {'contexte': 'reduit'}}})):
                ctx, _ = docrequest520.context(FauxDesk(tmp, reglage), MATTER, 'Demande fictive', FauxDav(), (), (), fonction='mail_drafting')
                appels = []

                class Faux:
                    def complete(self, messages, temperature=None, max_tokens=0, json_schema=None):
                        appels.append(len(json.dumps(messages, ensure_ascii=False)))
                        return json.dumps({'objet': 'Objet fictif', 'corps': 'Maître, le montant de 98 765 € est dû au 31 mars 2031. [À COMPLÉTER : date] [À COMPLÉTER : nom] [À COMPLÉTER : réf]',
                                           'variantes': [], 'a_completer': [], 'sources': ['ZZ9']})

                    def ask(self, stage, data):
                        appels.append(len(json.dumps(data, ensure_ascii=False)))
                        return {'body': 'Maître, réponse fictive.', 'variantes': [], 'a_completer': [], 'source_ids': [], 'limits': [], 'requires_decision': False}
                with patch('agent.maildraft5613._mail_model', lambda desk, mid='': Faux()):
                    maildraft5613.compose(self.desk, 'Demande fictive', MATTER, ctx)
                resultats[niveau] = appels
        print('\n[lot T] rédaction de courriel (fictif), caractères par appel : %s' % resultats)
        self.assertGreaterEqual(len(resultats['normal']), 1)
        self.assertLess(resultats['reduit'][0], resultats['normal'][0])
        if len(resultats['normal']) > 1:     # le second appel (correction) renvoie le dossier entier
            self.assertGreater(resultats['normal'][1], resultats['normal'][0] * 0.8)


if __name__ == '__main__':
    unittest.main()
