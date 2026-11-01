"""5.6.86 : une réponse préparée sans dossier reste visible et se retrouve. Avant, elle passait après les 12 premières cartes d'Aujourd'hui, n'apparaissait pas dans
la page Courriels, et le panneau du travail ne menait nulle part. Données fictives, aucun appel réseau."""
from datetime import datetime, timedelta, timezone
import importlib
import inspect
import json
from pathlib import Path
import re
from types import SimpleNamespace
from unittest.mock import patch

import test_v5652 as base
from agent import chatgpt5662 as cg, competences5632, competences5643 as c43, file5651, intelligence, model, pseudo540 as pseudo, jobview561, production420, reponses5671 as rep
from agent.common import Stop
from agent.pseudo540 import Pseudonymizer, _fold
from agent.schema5634 import strict_form

HERE = Path(__file__).resolve().parent

KEY = 'd' * 64


def key(n):
    return '%064x' % n


class Base(base.Base):
    def draft(self, ident=KEY, matter='', requires=True, when=None):
        data = {'result': {'body': 'Bonjour,\nProjet de réponse.', 'requires_decision': requires, 'limits': ['Contrôle de sécurité non satisfait : dépôt bloqué.'], 'source_ids': []},
                'verification': None, 'matter': matter, 'matter_path': '', 'key': ident, 'source_hash': 'h', 'created_at': self.desk.now(), 'style': 'prudent', 'recipients': ['client@example.test'], 'sources': []}
        self.desk.db.execute('INSERT OR REPLACE INTO manual_drafts VALUES (?,?,?)', (ident, json.dumps(data), when or self.desk.now()))
        self.desk.db.commit()

    def stamp(self, minutes):
        return (datetime.now(timezone.utc) - timedelta(minutes=minutes)).isoformat()


class Aujourdhui(Base):
    def test_sans_dossier_passe_avant_les_courriels_plus_anciens(self):
        for n in range(1, 16):
            self.draft(key(n), base.MID, when=self.stamp(100 + n))
        self.draft(KEY, '', when=self.stamp(1))
        rows = [i for i in file5651.items(self.desk, '/p') if i['class'] == 'courriel']
        self.assertEqual(rows[0]['object'], 'reponse5671:' + KEY)
        self.assertEqual(rows[0]['matter_name'], 'Sans dossier')
        html = file5651.html(self.desk, '/p')
        section = html.split('q51-g-courriels', 1)[1].split('q51-g-', 1)[0]
        self.assertIn('mail?key=' + KEY, section)

    def test_tri_du_plus_recent_au_plus_ancien(self):
        for n, minutes in ((1, 30), (2, 10), (3, 20)):
            self.draft(key(n), base.MID if n != 2 else '', when=self.stamp(minutes))
        rows = [i['object'][-1] for i in file5651.items(self.desk, '/p') if i['class'] == 'courriel']
        self.assertEqual(rows, ['2', '3', '1'])

    def test_tri_quelle_que_soit_la_source(self):
        for n, minutes in ((1, 30), (2, 10), (3, 20)):
            self.draft(key(n), '', when=self.stamp(minutes))
        real = rep.pending
        with patch.object(rep, 'pending', lambda desk: sorted(real(desk), key=lambda x: x[2])):      # autre source, autre ordre
            rows = [i['object'][-1] for i in file5651.items(self.desk, '/p') if i['class'] == 'courriel']
        self.assertEqual(rows, ['2', '3', '1'])

    def test_tout_voir_montre_tout(self):
        for n in range(1, 41):
            self.draft(key(n), '', when=self.stamp(n))
        html = file5651.html(self.desk, '/p', view='courriels')
        self.assertEqual(len(re.findall(r'class="q51-item q51-courriel', html)), 40)

    def test_limite_sur_les_reponses_en_attente(self):
        self.draft(KEY, '', when=self.stamp(5000))
        for n in range(1, 151):
            self.draft(key(n), base.MID, requires=False)
        self.assertEqual([x[0] for x in rep.pending(self.desk)], [KEY])


class PageCourriels(Base):
    def test_section_avec_et_sans_dossier(self):
        self.draft(KEY, '')
        html = rep.section_html(self.desk, '/p')
        self.assertIn('Réponses à décider', html)
        self.assertIn('/p/mail?key=' + KEY, html)
        self.assertIn('Sans dossier', html)
        self.assertNotIn('Réponse préparée, à décider', html)

    def test_rien_quand_rien_nattend(self):
        self.assertEqual(rep.section_html(self.desk, '/p'), '')
        self.draft(KEY, '', requires=False)
        self.assertEqual(rep.section_html(self.desk, '/p'), '')

    def test_echappement(self):
        self.draft(KEY, '')
        with patch('agent.desk.report_for', lambda c, k: {'subject': '<script>alert(1)</script>'}):
            self.assertNotIn('<script>', rep.section_html(self.desk, '/p'))

    def test_page_reelle(self):
        self.draft(KEY, '')
        from agent.web440 import drafts_page
        out = drafts_page(self.desk, {'csrf': 'x'}, '/p', {})
        self.assertIn('Réponses à décider', out if isinstance(out, str) else json.dumps(out, ensure_ascii=False, default=str))


class Travail(Base):
    def job(self, args):
        self.desk.db.execute("INSERT INTO jobs(kind,args,status,created,priority) VALUES('prepare_reply',?,'done',?,0)", (json.dumps(args), self.desk.now()))
        self.desk.db.commit()
        return self.desk.db.execute('SELECT MAX(id) FROM jobs').fetchone()[0]

    def test_panneau_mene_a_la_reponse(self):
        self.draft(KEY, '')
        html = jobview561.job_html(self.desk, '/p', self.job({'key': KEY, 'matter': ''}))
        self.assertIn('Ouvrir la réponse préparée', html)
        self.assertIn('/p/mail?key=' + KEY, html)
        self.assertIn('Sans dossier', html)

    def test_pas_de_lien_sans_reponse(self):
        html = jobview561.job_html(self.desk, '/p', self.job({'key': KEY, 'matter': ''}))
        self.assertNotIn('Ouvrir la réponse préparée', html)

    def test_message_sans_dossier(self):
        text = production420._business_message('prepare_reply', '', 'prepared', {}, [])
        self.assertIn('sans dossier', text)
        self.assertNotIn('pour le cabinet', text)
        self.assertIn('une décision reste nécessaire', production420._business_message('prepare_reply', 'Dossier fictif', 'prepared', {}, []))

    def test_message_avec_le_nom_du_dossier(self):
        self.assertNotEqual(production420._matter_label(self.desk, base.MID), '')
        self.assertEqual(production420._matter_label(self.desk, 'inconnu'), 'inconnu')
        self.assertEqual(production420._matter_label(self.desk, ''), '')


class Depot(Base):
    def run_reply(self, args, possible=True):
        modes, deposits = [], []

        def prepared(desk, a):
            modes.append(a.get('mode_projet'))
            return {'projet_prepare': True, 'depot_autorise': False, 'a_completer_possible': possible}

        def deposit(desk, a):
            deposits.append(a)
            return {'brouillon_imap': 'verifie'}
        with patch.object(intelligence, 'prepare_draft', prepared), patch.object(intelligence, 'deposit_draft', deposit):
            return intelligence.prepare_reply(self.desk, {'key': KEY, 'matter': '', **args}), modes, deposits

    def test_demande_depuis_la_boite_arrive_a_completer(self):
        out, modes, deposits = self.run_reply({'origine': 'boite5625', 'sans_dossier': 'oui'})
        self.assertEqual(modes, ['a_completer'])
        self.assertEqual(deposits[0]['a_completer'], 'oui')
        self.assertTrue(out['a_completer'])

    def test_controle_de_surete_refuse_reste_en_attente(self):
        out, _, deposits = self.run_reply({'origine': 'boite5625'}, possible=False)
        self.assertEqual(deposits, [])
        self.assertIn('attend votre décision', out['message'])

    def test_autre_origine_inchangee(self):
        _, modes, deposits = self.run_reply({'origine': 'autre'})
        self.assertEqual((modes, deposits), ([''], []))

    def test_reglage_coupe(self):
        with patch('agent.prudent5667.enabled', lambda desk: False):
            _, modes, deposits = self.run_reply({'origine': 'boite5625'})
        self.assertEqual((modes, deposits), ([''], []))


class ChatGPT(Base):
    def run_stream(self, *replies):
        calls = []

        def transport(method, url, body, headers, timeout=60, stream=False):
            calls.append(json.loads(body))
            reply = replies[min(len(calls), len(replies)) - 1]
            if isinstance(reply, Exception):
                raise reply
            return iter(reply)
        with patch.object(cg, 'access_token', lambda *a, **k: 'jeton-fictif'), patch.object(cg.time, 'sleep', lambda s: None):
            try:
                return cg.respond(self.desk.c['state_dir'], 'modele-fictif', [{'role': 'user', 'content': 'Texte fictif'}], transport=transport), calls, None
            except Stop as error:
                return None, calls, error

    @staticmethod
    def failed(code, message='Motif fictif'):
        return ['data: ' + json.dumps({'type': 'response.failed', 'response': {'error': {'code': code, 'message': message}}})]

    OK = ['data: ' + json.dumps({'type': 'response.output_text.delta', 'delta': 'Réponse'}), 'data: ' + json.dumps({'type': 'response.completed', 'response': {'usage': {}}})]

    def last(self):
        return self.desk.settings(cg.FAILURE_KEY, {})

    def test_motif_du_flux_conserve(self):
        _, calls, error = self.run_stream(self.failed('code_fictif'))
        self.assertEqual(str(error), 'chatgpt_generation_echouee')
        self.assertEqual(len(calls), 1)
        self.assertEqual((self.last()['code'], self.last()['detail'], self.last()['modele']), ('code_fictif', 'Motif fictif', 'modele-fictif'))
        self.assertNotIn('Texte fictif', json.dumps(self.last()))

    def test_motif_http_conserve(self):
        _, calls, error = self.run_stream(cg.HTTPFailure(400, 'invalid_request_error', 'Refus fictif', 'text.format'))
        self.assertEqual((str(error), len(calls)), ('chatgpt_generation_echouee', 1))
        self.assertEqual((self.last()['statut'], self.last()['param']), (400, 'text.format'))

    def test_incident_openai_un_seul_nouvel_essai(self):
        out, calls, error = self.run_stream(self.failed('server_error'), self.OK)
        self.assertEqual((out[0], len(calls), error), ('Réponse', 2, None))
        self.assertEqual(self.last(), {})
        out, calls, error = self.run_stream(cg.HTTPFailure(500), self.OK)
        self.assertEqual((out[0], len(calls)), ('Réponse', 2))
        _, calls, error = self.run_stream(self.failed('server_error'))
        self.assertEqual(len(calls), 2)
        self.assertEqual(self.last()['code'], 'server_error')
        _, calls, _ = self.run_stream(cg.HTTPFailure(502))
        self.assertEqual(len(calls), 2)

    def test_codes_reconnus(self):
        self.assertEqual(str(self.run_stream(self.failed('context_length_exceeded'))[2]), 'contexte_trop_long')
        self.assertEqual(str(self.run_stream(self.failed('rate_limit_exceeded'))[2]), 'chatgpt_limite_usage_atteinte')
        self.assertEqual(str(self.run_stream(self.failed('model_not_found'))[2]), 'modele_ou_route_fournisseur_absent')

    def test_affichage_dans_les_parametres(self):
        from agent import ia5682
        self.run_stream(self.failed('code_fictif', '<b>Motif</b>'))
        html = ia5682._last_failure(self.desk)
        self.assertIn('code_fictif', html)
        self.assertNotIn('<b>', html)
        old = dict(self.last(), at='2020-01-01T00:00:00+00:00')
        self.desk.setting(cg.FAILURE_KEY, old)
        self.assertEqual(ia5682._last_failure(self.desk), '')


class FormatStrict(Base):
    def problems(self, s, where='racine', out=None):
        out = [] if out is None else out
        if not isinstance(s, dict):
            return out
        if 'properties' in s:
            if s.get('additionalProperties') is not False:
                out.append(where + ' ouvert')
            if set(s['properties']) != set(s.get('required') or []):
                out.append(where + ' champ facultatif')
        for word in ('maxLength', 'minLength', 'uniqueItems', 'default', 'oneOf'):
            if word in s:
                out.append(where + ' ' + word)
        for name, item in (s.get('properties') or {}).items():
            self.problems(item, where + '.' + name, out)
        self.problems(s.get('items'), where + '[]', out)
        for item in s.get('anyOf') or []:
            self.problems(item, where + '|', out)
        return out

    def test_les_deux_fonctions_refusees(self):
        from agent import initiatives5636, maildraft5613
        self.assertTrue(self.problems(initiatives5636.SCHEMA))
        for schema in (initiatives5636.SCHEMA, maildraft5613.SCHEMA_OUT):
            self.assertEqual(self.problems(strict_form(schema)), [])

    def test_tous_les_schemas_de_lapplication(self):
        count = 0
        for path in sorted((HERE.parent / 'agent').glob('*.py')):
            try:
                module = importlib.import_module('agent.' + path.stem)
            except Exception:   # noqa: BLE001 — module propre à une plateforme
                continue
            for name, value in vars(module).items():
                if isinstance(value, dict) and value.get('type') == 'object' and 'properties' in value:
                    count += 1
                    self.assertEqual(self.problems(strict_form(value)), [], path.stem + '.' + name)
        self.assertGreater(count, 30)

    def test_un_champ_nomme_default_reste(self):
        out = strict_form({'type': 'object', 'properties': {'default': {'type': 'string', 'maxLength': 3}}})
        self.assertEqual(out, {'type': 'object', 'properties': {'default': {'type': 'string'}}, 'additionalProperties': False, 'required': ['default']})

    def test_schema_conforme_inchange(self):
        schema = {'type': 'object', 'properties': {'a': {'type': 'array', 'maxItems': 3, 'items': {'type': 'string'}}}, 'additionalProperties': False, 'required': ['a']}
        self.assertEqual(strict_form(schema), schema)

    def test_requete_chatgpt(self):
        from agent import initiatives5636
        req = cg.build_request('modele-fictif', [{'role': 'user', 'content': 'x'}], initiatives5636.SCHEMA)
        self.assertEqual(self.problems(req['text']['format']['schema']), [])
        self.assertEqual(self.problems(initiatives5636.SCHEMA)[0][:7], 'racine ')       # l'original sert encore aux contrôles de l'application


class Pseudonymisation(Base):
    def apply(self, *contents):
        ps = Pseudonymizer({'state_dir': self.desk.c['state_dir']}, known=[])
        return ps, [m['content'] for m in ps.apply_messages([{'role': 'user', 'content': c} for c in contents])]

    def test_prenoms_accentues(self):
        for name in ('Hélène Durand', 'François Martin', 'Éric Bernard', 'Jérôme Zorval', 'Thérèse Orlandec'):
            self.assertNotIn(name.split()[1], self.apply('Signé : ' + name + '.')[1][0], name)

    def test_tous_les_prenoms_sont_comparables(self):
        self.assertTrue(all(x == _fold(x) for x in pseudo.FIRST_NAMES))

    def test_signature_apres_saut_de_ligne(self):
        text = 'Bonjour,\nCordialement,\nHélène Durand\nPaul Martin\n\nMarie Dupont\n'
        for content in (text, json.dumps({'mail': text}, ensure_ascii=False), json.dumps({'mail': text}), 'Consigne fixe.\n\n' + json.dumps({'mail': text}, ensure_ascii=False)):
            out = self.apply(content)[1][0]
            for word in ('Durand', 'Martin', 'Dupont'):
                self.assertNotIn(word, out, content[:30])

    def test_json_reste_valide_et_identique_sans_donnee(self):
        text = json.dumps({'mail': 'Ligne un.\nLigne deux.\tSuite\r\nÉté', 'n': 3}, ensure_ascii=False)
        self.assertEqual(self.apply(text)[1][0], text)
        out = self.apply(json.dumps({'mail': 'Bonjour\nHélène Durand\nFin'}))[1][0]
        self.assertEqual(json.loads(out)['mail'].splitlines()[0], 'Bonjour')
        self.assertEqual(len(json.loads(out)['mail'].splitlines()), 3)

    def test_chemin_windows_inchange(self):
        self.assertEqual(self.apply('Fichier C:\\new\\table.txt')[1][0], 'Fichier C:\\new\\table.txt')

    def test_reprises_du_meme_nom(self):
        ps, out = self.apply('Je suis Madame Kenza BELKACEM. Ma signature : Kenza Belkacem, ou Belkacem Kenza.')
        self.assertNotIn('Belkacem', out[0])
        self.assertEqual(len(set(re.findall(r'\[PERSONNE_\d+\]', out[0]))), 1)
        ps, out = self.apply('Madame Hélène KERVILAC écrit.', 'Kervilac confirme, comme Mme Kervilac.')
        self.assertNotIn('ervilac', out[1])
        self.assertNotIn('ERVILAC', out[0])

    def test_civilite_en_minuscules(self):
        self.assertNotIn('Durand', self.apply('Je représente madame Durand et monsieur Martin.')[1][0].replace('monsieur', ''))

    def test_lieu_de_naissance(self):
        ps, out = self.apply('Née le 12/03/1980 à Valbrune-sur-Mer, elle réside ici.')
        self.assertNotIn('Valbrune', out[0])
        self.assertIn('Valbrune-sur-Mer', ps.back.values())
        self.assertNotIn('Valbrune', self.apply('Il est né à Valbrune.')[1][0])

    def test_retablissement(self):
        ps, out = self.apply(json.dumps({'corps': 'Bonjour\nHélène Durand'}, ensure_ascii=False))
        back = ps.restore(out[0], json_mode=False)
        self.assertIn('Hélène Durand', back)

    def test_les_termes_juridiques_restent(self):
        text = 'Le Tribunal Judiciaire de Lyon, la Cour de cassation et la Sécurité Sociale.'
        self.assertEqual(self.apply(text)[1][0], text)

    def test_chemin_complet_vers_chatgpt(self):
        cfg = {'provider_type': 'chatgpt', 'provider_id': 'chatgpt', 'type': 'chatgpt', 'model': 'modele-fictif', 'state_dir': self.desk.c['state_dir'], 'skill_instructions': 'Extension Assistant juridique [fictif]\nMéthode fictive.'}
        sent = []

        class Client(model.Model):
            def __init__(self):
                self.cfg, self.provider_type, self.provider_id, self._trace = cfg, 'chatgpt', 'chatgpt', ('', None)

            def _chatgpt(inner, messages, json_schema):
                sent.extend(messages)
                return 'Réponse à [PERSONNE_1]'
        with patch('agent.policy567.check_external', lambda *a, **k: None), patch('agent.pseudo540.log_transmission', lambda *a, **k: None):
            out = Client()._leaf([{'role': 'system', 'content': 'Consignes.'}, {'role': 'user', 'content': json.dumps({'mail': 'Bonjour\nHélène Durand'}, ensure_ascii=False)}], 0, 100, None)
        text = json.dumps(sent, ensure_ascii=False)
        self.assertNotIn('Durand', text)
        self.assertIn('Assistant juridique', text)                     # la compétence active part avec la demande
        self.assertIn('COMPÉTENCE EXTERNE APPROUVÉE', text)
        self.assertEqual(out, 'Réponse à Hélène Durand')               # la réponse est rétablie sur le serveur

    def test_competence_titres_conserves_noms_masques(self):
        ps = Pseudonymizer({'state_dir': self.desk.c['state_dir']}, known=[('Paul ZORVAL', 'PERSONNE')])
        out = ps.apply_messages([{'role': 'system', 'content': 'MÉTHODE GÉNÉRALE. Exemple : Madame Hélène DURAND et Paul ZORVAL.'}, {'role': 'user', 'content': 'DURAND Hélène, KERVILAC.'}], method_at=0)
        self.assertIn('MÉTHODE GÉNÉRALE', out[0]['content'])
        self.assertNotIn('DURAND', out[0]['content'])
        self.assertNotIn('ZORVAL', out[0]['content'])
        self.assertNotIn('KERVILAC', out[1]['content'])


class CinqIA(Base):
    SKILL = 'Extension Assistant juridique [fictif]\nMéthode fictive de raisonnement.'

    def send(self, kind):
        client = model.Model.__new__(model.Model)
        client.cfg = {'provider_type': kind, 'provider_id': kind, 'type': kind, 'model': 'modele-fictif', 'state_dir': self.desk.c['state_dir'], 'skill_instructions': self.SKILL, 'num_ctx': 32768, 'timeout_seconds': 10}
        client.provider_type, client.provider_id, client._trace = kind, kind, ('', None)
        sent = []

        def transport(messages, *a):
            sent.extend(messages)
            return 'Réponse fictive'

        def ollama(method, path, req):
            sent.extend(req['messages'])
            return {'done': True, 'message': {'content': 'Réponse fictive'}}
        client.http = SimpleNamespace(json=ollama)
        client.caps = None
        with patch.object(client, '_anthropic', transport, create=True), patch.object(client, '_chatgpt', transport, create=True), patch.object(client, '_openai_compatible', transport, create=True), \
                patch('agent.policy567.check_external', lambda *a, **k: None), patch('agent.pseudo540.log_transmission', lambda *a, **k: None), patch('agent.profil5634.record', lambda *a, **k: None):
            out = client._leaf([{'role': 'system', 'content': 'Consignes.'}, {'role': 'user', 'content': 'Bonjour\\nHélène Durand'}], 0, 100, None)
        return out, json.dumps(sent, ensure_ascii=False)

    def test_la_competence_part_vers_chaque_ia(self):
        for kind in ('ollama', 'mistral', 'openrouter', 'anthropic', 'openai', 'chatgpt'):
            with self.subTest(kind=kind):
                out, text = self.send(kind)
                self.assertEqual(out, 'Réponse fictive')
                self.assertIn('Méthode fictive de raisonnement', text)
                self.assertIn('COMPÉTENCE EXTERNE APPROUVÉE', text)
                if kind != 'ollama':                                  # le local reste sur le serveur
                    self.assertNotIn('Durand', text)

    def test_relais_et_secours_gardent_la_competence(self):
        from agent import budget5668, hybrid400, secours564
        kept = budget5668.local_fallback({'skill_instructions': self.SKILL, 'purpose': 'assistant'}, {'model_routing': {}, 'ollama': {'url': 'http://127.0.0.1:11434', 'model': 'local-fictif'}},
                                         {'ollama': {'type': 'ollama', 'url': 'http://127.0.0.1:11434'}}, 'assistant', 'complex')
        self.assertEqual(kept['budget_fallback']['skill_instructions'], self.SKILL)
        self.assertIn('skill_instructions', routage_keep())
        self.assertIn('skill_instructions', inspect.getsource(secours564.candidates))
        self.assertIn("result['pseudo'] = pseudo_sources(config)", inspect.getsource(hybrid400.external_config))

    def test_hybride_avec_les_noms_du_cabinet(self):
        from agent import hybrid400
        config = {'state_dir': self.desk.c['state_dir'], 'matters_file': 'dossiers.json', 'nextcloud': {'roots': ['/Dossiers']}}
        with patch('agent.ai_gateway.provider_registry', lambda c: {'relais': {'type': 'openrouter'}}):
            out = hybrid400.external_config(config, {'provider': 'relais', 'model': 'm', 'purpose': 'assistant'})
        self.assertEqual(out['pseudo']['matters_file'], 'dossiers.json')


def routage_keep():
    from agent import routage5682
    return inspect.getsource(routage5682.child_config)


class CompetenceGenerale(Base):
    def skill(self, name, situations='', phases=None):
        out = competences5632.create(self.desk, 'cabinet', {'nom': name, 'objectif': 'Objectif fictif de méthode.', 'situations': situations, 'etapes': 'Étape fictive.', 'resultat': 'Résultat fictif.', 'controles': 'Contrôle fictif.', 'exemples': '', 'limites': ''}, 'cabinet')
        return out['id']

    def setUp(self):
        super().setUp()
        self.desk.setting('competences5632:auto', True)
        for row in competences5632.listing(self.desk, 'cabinet'):
            if row['enabled']:
                competences5632.set_enabled(self.desk, 'cabinet', row['id'], False)
        self.general = self.skill('Assistant juridique', 'droit français')
        self.other = self.skill('Approbation des comptes', 'décision, cadrage, quoi préparer')
        for ident in (self.general, self.other):
            competences5632.set_enabled(self.desk, 'cabinet', ident, True)

    def chosen(self, kind, phase=None, text=''):
        phase = phase if phase is not None else c43.KIND_PHASE.get(kind, '')
        return [s['id'] for s in c43.select(self.desk, text, phase, 'cabinet', kind=kind)]

    def test_reponses_discussion_et_nouveaux_courriels(self):
        for kind, phase in (('prepare_reply', None), ('maildraft5613', None), ('assistant_answer', None), ('docrequest520', None), ('prepare_document_project', None),
                            ('routine520', None), ('recit5651', None)):
            self.assertIn(self.general, self.chosen(kind, phase), kind)

    def test_surveillance_automatique_non_alourdie(self):
        for kind in ('orchestrate', 'proactive34_cycle', 'live_mail430', 'run'):
            self.assertNotIn(self.general, self.chosen(kind), kind)

    def test_specialisee_plus_methode_de_decision(self):
        self.assertEqual(self.chosen('proactive34_cycle'), [])

    def test_methode_declaree_prime(self):
        with patch.object(c43, 'skill_phases', lambda m: ['redaction_courriel'] if m.get('name') == 'Approbation des comptes' else []):
            real = json.loads
            row = competences5632._row(self.desk, self.other)
            manifest = real(row['manifest'])
            manifest['phases'] = ['redaction_courriel']
            self.desk.db.execute('UPDATE skills5632 SET manifest=? WHERE id=?', (json.dumps(manifest), self.other))
            self.desk.db.commit()
            self.assertEqual(self.chosen('prepare_reply')[0], self.other)

    def test_choix_du_cabinet_et_desactivation(self):
        self.desk.setting(c43.GENERAL_SETTING, self.other)
        self.assertIn(self.other, self.chosen('prepare_reply'))
        self.desk.setting(c43.GENERAL_SETTING, '')
        self.assertEqual(self.chosen('assistant_answer', text=''), [])

    def test_inactive_jamais_utilisee(self):
        competences5632.set_enabled(self.desk, 'cabinet', self.general, False)
        self.assertNotIn(self.general, self.chosen('prepare_reply'))

    def test_budget_de_la_discussion(self):
        seen = []
        with patch.object(competences5632, 'instructions_for', lambda desk, sel, budget=0, **k: seen.append(budget) or 'Texte'):
            with competences5632.for_job(self.desk, {'kind': 'assistant_answer', 'id': None}, {'question': 'Une question fictive'}):
                pass
            with competences5632.for_job(self.desk, {'kind': 'prepare_reply', 'id': None}, {'key': 'x'}):
                pass
        self.assertEqual(seen, [c43.CHAT_BUDGET, competences5632.BUDGET])

    def test_page_competences(self):
        from agent import web5632
        html = web5632._general_form(self.desk, competences5632.listing(self.desk, 'cabinet'))
        self.assertIn('admin/skills/generale', html)
        self.assertIn('selected>Assistant juridique', html)

