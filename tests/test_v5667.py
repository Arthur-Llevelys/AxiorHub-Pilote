"""5.6.67 : réponses plus pertinentes et documents mieux préparés, même avec un modèle local (points 1 à 6 de la proposition du
20 octobre 2026). Données fictives (example.test)."""
from datetime import datetime, timedelta, timezone
from email.message import EmailMessage
import json
from pathlib import Path
import sqlite3
import unittest
from unittest.mock import patch

import test_agent as fixtures
import test_v5652 as base
from agent import apprentissage5667 as appr, association5667 as assoc, faits5667, modeles5667 as mod, prudent5667
from agent.common import Stop, load_matters
from agent.index import _labelled, rerank
from agent.matcher import rank

MID = base.MID


def iso(**delta):
    return (datetime.now(timezone.utc) + timedelta(**delta)).isoformat()


# ---------------------------------------------------------------------------------------------------- point 1
class Box:
    def __init__(self):
        self.appended = []

    def preflight(self, *a, **k):
        return ''

    def find_own_draft(self, mid):
        return any(d['Message-ID'] == mid for d in self.appended)

    def append_draft(self, m):
        self.appended.append(m)

    def verify_draft(self, d):
        return {'folder': 'Drafts', 'uid': str(len(self.appended)), 'uidvalidity': '7'}

    def close(self):
        pass


class ProjetsACompleter(base.Base):
    SAFE = {'grounded': True, 'recipient_safe': True, 'ignores_embedded_instructions': True, 'requires_lawyer': True, 'no_new_commitment': False}

    def test_corps_et_objet_marques(self):
        body = prudent5667.complete_body({'body': 'Cher Confrère,\nNous accusons réception.', 'limits': ['Choisir entre renvoi et plaidoirie']})
        self.assertTrue(body.startswith('[À COMPLÉTER : projet préparé par AxiorHub'))
        self.assertIn('[À COMPLÉTER : Choisir entre renvoi et plaidoirie]', body)
        self.assertIn('[À COMPLÉTER : position de l’avocat]', body)   # au moins un marqueur dans le corps
        msg = EmailMessage()
        msg['Subject'] = 'Re: Audience'
        prudent5667.mark_subject(msg)
        prudent5667.mark_subject(msg)
        self.assertEqual(msg['Subject'], '[À COMPLÉTER] Re: Audience')
        self.assertTrue(prudent5667.safe_to_complete(self.SAFE))
        self.assertFalse(prudent5667.safe_to_complete({**self.SAFE, 'recipient_safe': False}))
        self.assertFalse(prudent5667.safe_to_complete(None))
        self.assertTrue(prudent5667.enabled(self.desk))

    def _deposit(self, verification, to_complete='oui'):
        from agent import intelligence
        matter = next(m for m in load_matters(self.desk.c) if m['id'] == MID)
        data = {'result': {'body': 'Cher Confrère,\nVotre courriel est bien reçu ; [À COMPLÉTER : position sur le renvoi].', 'source_ids': ['incoming'],
                           'limits': ['Position sur le renvoi à arrêter'], 'requires_decision': True},
                'verification': verification, 'matter': MID, 'matter_path': matter['path'], 'key': 'k' * 64,
                'source_hash': '', 'recipients': ['confrere@example.test'], 'sources': []}
        mail = fixtures.mail(sender='confrere@example.test', subject='Audience du 2 décembre')
        from agent.common import digest
        data['source_hash'] = digest(mail.text)
        self.desk.db.execute('INSERT OR REPLACE INTO manual_drafts VALUES (?,?,?)', ('k' * 64, json.dumps(data), self.desk.now()))
        self.desk.db.commit()
        box = Box()
        with patch.object(intelligence, 'Mailbox', return_value=box), \
                patch.object(intelligence, 'fetch_source', return_value=({'matter': MID}, mail)), \
                patch('agent.integration.set_work_state'):
            out = intelligence.deposit_draft(self.desk, {'key': 'k' * 64, 'confirm': 'yes', 'a_completer': to_complete})
        return out, box

    def test_projet_juridique_depose_a_completer(self):
        out, box = self._deposit(self.SAFE)
        self.assertEqual(out['brouillon_imap'], 'verifie')
        draft = box.appended[0]
        self.assertTrue(str(draft['Subject']).startswith('[À COMPLÉTER]'))
        self.assertTrue(draft.get_content().startswith('[À COMPLÉTER : projet préparé par AxiorHub'))
        self.assertEqual(draft['X-AxiorHub-A-Completer'], 'oui')

    def test_sans_controle_de_surete_rien_n_est_depose(self):
        with self.assertRaisesRegex(Stop, 'controle_de_surete_non_satisfait'):
            self._deposit({**self.SAFE, 'grounded': False})
        with self.assertRaisesRegex(Stop, 'depot_bloque_decision_requise'):
            self._deposit(self.SAFE, to_complete='')                 # sans « à compléter », la décision requise bloque toujours

    def test_envoi_depuis_axiorhub_refuse(self):
        from agent import send490
        msg = EmailMessage()
        msg['Subject'] = '[À COMPLÉTER] Re: Audience'
        msg['To'] = 'confrere@example.test'
        msg.set_content('Texte')

        class Mail:
            pass
        m = Mail()
        m.msg = msg
        self.desk.setting('send490:enabled', True)
        with patch.object(send490, 'smtp_config', return_value={'host': 'smtp.example.test'}), \
                patch.object(send490, '_matter_of', return_value=MID), \
                patch.object(self.desk, 'settings', side_effect=lambda k, d=None: True if k in ('send490:enabled', 'send5636:all_matters') else d):
            with self.assertRaisesRegex(Stop, 'brouillon_a_completer'):
                send490._check_sendable(self.desk, m)


# ---------------------------------------------------------------------------------------------------- point 2
def _mail(subject, sender='inconnu@example.test', text='Bonjour, voir objet.'):
    return fixtures.mail(sender=sender, subject=subject).__class__(
        '9', '7', 'INBOX', set(), datetime.now(timezone.utc), _msg(subject, sender, text))


def _msg(subject, sender, text):
    m = EmailMessage()
    m['From'] = sender
    m['To'] = 'cabinet@example.test'
    m['Subject'] = subject
    m['Message-ID'] = '<x-9@example.test>'
    m.set_content(text)
    return m


class Rattachement(base.Base):
    def _seed(self):
        from agent import modele5651
        modele5651.ensure_schema(self.desk)
        now = iso()
        self.desk.db.execute("INSERT INTO d51_procedures(id,matter,rg,origin,created,updated) VALUES('p1',?, 'RG 24/01234','document',?,?)", (MID, now, now))
        for pid, mid, name in (('a', MID, 'ZORVAL Holding'), ('b', MID, 'Banque Fictive Lyon'), ('c', 'DOS-AUTRE', 'Banque Fictive Lyon')):
            self.desk.db.execute("INSERT INTO d51_parties(id,matter,kind,display_name,core,origin,created,updated) VALUES(?,?,?,?,?,?,?,?)",
                                 (pid, mid, 'morale', name, name.lower(), 'document', now, now))
        self.desk.db.commit()

    def test_rg_et_parties_propres_au_dossier(self):
        self._seed()
        learned = assoc.learned(self.desk.c['state_dir'])
        self.assertIn('24/01234', learned[MID]['references'])
        self.assertIn('ZORVAL Holding', learned[MID]['names'])
        self.assertNotIn('Banque Fictive Lyon', learned[MID]['names'])          # partagé avec un autre dossier : ne distingue rien
        matters = assoc.augment(self.desk.c, load_matters(self.desk.c))
        matter, role, ranked = rank(_mail('Avis de renvoi – RG 24-01234'), matters)
        self.assertEqual((matter or {}).get('id'), MID)
        self.assertIsNone(role)                                                 # le rôle n'est jamais deviné
        self.assertEqual(load_matters(self.desk.c)[0].get('learned_references'), None)   # rien n'est écrit dans la configuration

    def test_date_jamais_prise_pour_un_rg(self):
        self.assertEqual(assoc.rg_variants('Audience du 20/10/2026.', in_text=True), [])
        self.assertEqual(assoc.rg_variants('n° RG : 23/01234, audience', in_text=True), ['23/01234', '23-01234'])
        self.assertEqual(assoc.rg_variants('RG 24/01234.'), ['24/01234', '24-01234'])

    def test_expediteur_confirme_et_rg_appris_a_la_confirmation(self):
        from agent import portfolio
        portfolio.ensure_schema(self.desk)
        self.desk.db.execute("INSERT INTO portfolio_mail_links(id,mail_key,message_id,thread_root,folder,uid,matter,confidence,level,source,sender,"
                             "recipients,mail_date,status,updated) VALUES('l1','k','m','r','INBOX','1',?,100,'certain','avocat','greffe@example.test',"
                             "'[]',?, 'confirmed',?)", (MID, iso(), iso()))
        self.desk.db.commit()
        self.assertEqual(assoc.learned(self.desk.c['state_dir'])[MID]['senders'], ['greffe@example.test'])
        matter = next(m for m in load_matters(self.desk.c) if m['id'] == MID)
        self.desk.propose(matter, 'nouveau@example.test', {'type': 'score_automatique', 'source': 'Calendrier – RG 25/00042', 'date': iso()})
        key = self.desk.db.execute("SELECT key FROM proposals WHERE email='nouveau@example.test'").fetchone()[0]
        self.desk.associate({'proposal': key, 'matter': MID, 'email': 'nouveau@example.test', 'role': 'tiers'})
        refs = next(m for m in load_matters(self.desk.c) if m['id'] == MID)['references']
        self.assertIn('25/00042', refs)


# ---------------------------------------------------------------------------------------------------- point 3
class Passages(unittest.TestCase):
    def _row(self, sid, n, text, path='/D/Piece.pdf'):
        return (MID, sid, path, 'e', '2026-10-01', 'document', n, text, '{}', 'x')

    def test_second_tri_diversite_et_seuil(self):
        rows = {('s1', 0): self._row('s1', 0, 'renvoi audience décembre conclusions adverses délai'),
                ('s1', 1): self._row('s1', 1, 'renvoi audience décembre conclusions adverses délai'),        # doublon presque exact
                ('s2', 0): self._row('s2', 0, 'renvoi audience calendrier', '/D/Avis de renvoi.pdf'),
                ('s3', 0): self._row('s3', 0, 'facture honoraires provision')}
        scores = {('s1', 0): 1.0, ('s1', 1): 0.99, ('s2', 0): 0.6, ('s3', 0): 0.2}
        out = rerank(rows, scores, ['renvoi', 'audience', 'decembre'], limit=3)
        ids = [(r[1], r[6]) for r, _ in out]
        self.assertEqual(ids[0], ('s1', 0))
        self.assertEqual(ids[1], ('s2', 0))                 # la pièce distincte passe avant le doublon
        cut = rerank(rows, scores, ['renvoi'], limit=4, min_relative=0.9)
        self.assertEqual(len(cut), 4)                       # au moins 4 passages gardés
        self.assertEqual(rerank({}, {}, ['x']), [])

    def test_une_grande_piece_ne_prive_pas_les_autres(self):
        rows, scores = {}, {}
        for i in range(80):
            rows[('big', i)] = self._row('big', i, 'texte %d renvoi' % i, '/D/Gros.pdf')
            scores[('big', i)] = 1 - i / 1000
        for j in range(5):
            rows[('p%d' % j, 0)] = self._row('p%d' % j, 0, 'renvoi pièce %d' % j, '/D/P%d.pdf' % j)
            scores[('p%d' % j, 0)] = 0.5
        out = rerank(rows, scores, ['renvoi'], limit=6)
        self.assertEqual(sum(1 for r, _ in out if r[1] == 'big'), 2)
        self.assertEqual(len(out), 6)

    def test_passage_etiquete(self):
        self.assertEqual(_labelled('Texte', '/Dossiers/DEMO/Avis de renvoi.pdf', 'document'), 'Pièce : Avis de renvoi.pdf — document\nTexte')


# ---------------------------------------------------------------------------------------------------- point 4
class Faits(unittest.TestCase):
    REF = "Courriel du 14/10/2026 : renvoi au 12 décembre 2026. Montant réclamé : 20 000 €. RG 24/01234. Partie : ZORVAL."

    def test_elements_absents_marques(self):
        out, issues = faits5667.check("L'affaire ZORVAL (RG 24-01234) est renvoyée au mardi 12 décembre 2026 ; 20 000 € ; 25 000 € ; "
                                      "audience le 15 mars 2027 ; dossier DUPONTEL.", self.REF, names=True)
        self.assertEqual([i['texte'] for i in issues], ['25 000 €', '15 mars 2027', 'DUPONTEL'])
        self.assertIn('[À VÉRIFIER : 25 000 €]', out)
        self.assertNotIn('[À VÉRIFIER : 24-01234]', out)
        self.assertEqual(faits5667.check(out, self.REF, names=True), (out, []))     # idempotent

    def test_documents_sans_controle_des_noms_et_date_du_jour_admise(self):
        today = datetime.now().strftime('%d/%m/%Y')
        out, issues = faits5667.check('PAR CES MOTIFS, plaise au TRIBUNAL — fait le %s.' % today, self.REF)
        self.assertEqual(issues, [])

    def test_exemples_d_anciens_envois_ne_valent_pas_preuve(self):
        kept = faits5667.without_examples([{'kind': 'sent_example', 'excerpt': '3 000 €'}, {'kind': 'document', 'excerpt': 'x'}])
        self.assertEqual([s['kind'] for s in kept], ['document'])


class Etapes(base.Base):
    class Model:
        def __init__(self, replies):
            self.replies, self.calls = list(replies), []

        def complete(self, messages, temperature=None, max_tokens=0, json_schema=None):
            self.calls.append(messages[-1]['content'][:60])
            return json.dumps(self.replies.pop(0))

    def test_plan_puis_sections_avec_leurs_sources(self):
        from agent import docrequest520 as d
        payload = {'dossier': {'extraits': [{'source': 'P1', 'texte': 'a'}, {'source': 'P2', 'texte': 'b'}], 'courriels': []}}
        m = self.Model([{'titre': 'Note', 'nom_fichier': 'note', 'sections': [{'titre': 'Faits', 'objet': 'x', 'sources': ['P1']},
                                                                              {'titre': 'Analyse', 'objet': 'y', 'sources': ['P2', 'Z9']}]},
                        {'paragraphes': [{'style': 'texte', 'texte': 'Les faits.'}], 'sources': ['P1']},
                        {'paragraphes': [{'style': 'texte', 'texte': 'L’analyse.'}], 'sources': ['P2']}])
        doc = d._stepwise(m, payload, 'système')
        self.assertEqual([p['texte'] for p in doc['paragraphes']], ['Faits', 'Les faits.', 'Analyse', 'L’analyse.'])
        self.assertEqual(doc['sources'], ['P1', 'P2'])
        self.assertEqual(len(m.calls), 3)
        self.assertEqual(d._subset(payload['dossier'], {'P2'})['extraits'], [{'source': 'P2', 'texte': 'b'}])

    def test_plan_inexploitable_redaction_d_un_seul_tenant(self):
        from agent import docrequest520 as d
        self.assertIsNone(d._stepwise(self.Model([{'titre': 'x', 'sections': [{'titre': 'Seule', 'objet': 'x'}]}]), {'dossier': {}}, 's'))
        self.assertIsNone(d._stepwise(self.Model([{'pas': 'un plan'}]), {'dossier': {}}, 's'))

    def test_document_controle(self):
        from agent import docrequest520 as d
        doc = d.check_document({'paragraphes': [{'style': 'texte', 'texte': 'Somme de 9 999 € due.'}], 'a_completer': []}, {'x': 'rien'})
        self.assertIn('[À VÉRIFIER : 9 999 €]', doc['paragraphes'][0]['texte'])
        self.assertTrue(doc['a_completer'])


# ---------------------------------------------------------------------------------------------------- point 5
def _sent_memory(state_dir, rows):
    db = sqlite3.connect(Path(state_dir) / 'sent-memory.sqlite3')
    db.execute('CREATE TABLE IF NOT EXISTS examples_v2 (key TEXT PRIMARY KEY, matter TEXT, recipients TEXT, sent_at TEXT, body TEXT, draft TEXT, '
               'stats TEXT, provenance TEXT, account TEXT, audience TEXT)')
    db.execute('CREATE TABLE IF NOT EXISTS pairs5667 (sent_key TEXT PRIMARY KEY, incoming_message_id TEXT NOT NULL, created TEXT NOT NULL)')
    for r in rows:
        db.execute('INSERT INTO examples_v2 VALUES (?,?,?,?,?,?,?,?,?,?)', r)
    db.commit()
    db.close()


class Corrections(base.Base):
    DRAFT = 'Bonjour,\nVotre dossier avance normalement.\nNous restons attentifs.\nBien cordialement'
    FINAL = 'Bonjour,\nVotre dossier avance normalement.\nJe reste à votre disposition pour tout complément.\nBien cordialement'
    AUD = json.dumps([['client@example.test', 'client']])

    def test_phrase_ajoutee_trois_fois_devient_proposition_puis_regle(self):
        rows = [('k%d' % i, MID + '@x', '[]', iso(days=-i), self.FINAL, self.DRAFT, '{}', 'matched_draft_and_sent', 'a', self.AUD) for i in range(3)]
        _sent_memory(self.desk.c['state_dir'], rows)
        out = appr.refresh_proposals(self.desk)
        self.assertEqual(out['examples'], 3)
        props = appr.proposals(self.desk)
        kinds = {(p['kind'], p['sentence']) for p in props}
        self.assertIn(('ajout', 'Je reste à votre disposition pour tout complément.'), kinds)
        self.assertIn(('retrait', 'Nous restons attentifs.'), kinds)
        add = next(p for p in props if p['kind'] == 'ajout')
        res = appr.decide(self.desk, add['id'], 'adopter')
        self.assertIn('ajouter, quand c’est pertinent, la phrase', res['rule']['sentence'])
        with self.assertRaisesRegex(Stop, 'proposition_deja_traitee'):
            appr.decide(self.desk, add['id'], 'adopter')
        rem = next(p for p in appr.proposals(self.desk) if p['kind'] == 'retrait')
        appr.decide(self.desk, rem['id'], 'ecarter')
        appr.refresh_proposals(self.desk)
        self.assertEqual(appr.proposals(self.desk), [])               # écartée ou adoptée : ne revient pas

    def test_jamais_un_fait_ni_un_nom(self):
        self.assertFalse(appr.generalizable('Le rendez-vous est fixé au 12 décembre.'))
        self.assertFalse(appr.generalizable('Je transmets le dossier à Maître Dupontel ce jour.'))
        self.assertFalse(appr.generalizable('Écrire à contact@example.test pour tout complément.'))
        self.assertTrue(appr.generalizable('Je reste à votre disposition pour tout complément.'))

    def test_part_des_documents_gardee(self):
        from agent import docrequest520
        docrequest520.ensure_schema(self.desk)
        text = 'Note sur le dossier. ' * 40
        self.desk.db.execute("INSERT INTO docreq520(id,matter,request,kind,status,path,result,created,updated) VALUES('r1',?,?,?, 'cree', "
                             "'/Dossiers/DEMO/Note.docx',?,?,?)", (MID, 'Note', 'note', json.dumps({'texte_produit': text}), iso(days=-5), iso(days=-5)))
        self.desk.db.commit()

        class Client:
            def stat(self, path):
                return {'path': path, 'etag': 'v2'}

            def download(self, meta):
                return b'x'
        with patch('agent.documents.extract', return_value=text + ' Ajout de l’avocat.'):
            self.assertEqual(appr.measure_documents(self.desk, Client())['measured'], 1)
            self.assertEqual(appr.measure_documents(self.desk, Client())['measured'], 0)    # même version : pas de nouvelle mesure
        dash = appr.documents_dashboard(self.desk)
        self.assertEqual((dash[0]['kind'], dash[0]['count'], dash[0]['leger']), ('note', 1, 100))
        self.assertIn('Documents produits', appr.html(self.desk, '/p'))


# ---------------------------------------------------------------------------------------------------- point 6
class Modeles(base.Base):
    TAGS = {'models': [{'name': 'qwen3:8b', 'size': 5_200_000_000, 'details': {'parameter_size': '8.2B'}},
                       {'name': 'gemma3:27b', 'size': 17_000_000_000, 'details': {'parameter_size': '27.4B'}},
                       {'name': 'mistral-small:24b', 'size': 14_000_000_000, 'details': {'parameter_size': '24B'}},
                       {'name': 'llama3:70b', 'size': 40_000_000_000, 'details': {'parameter_size': '70.6B'}},
                       {'name': 'nomic-embed-text', 'size': 270_000_000, 'details': {'parameter_size': '137M'}},
                       {'name': 'qwen3:0.6b', 'size': 520_000_000, 'details': {'parameter_size': '751M'}}]}

    def test_repartition_recommandee(self):
        models = mod.installed(self.desk, self.TAGS)
        self.assertNotIn('nomic-embed-text', [m['name'] for m in models])
        rec = mod.recommend(models)
        self.assertEqual((rec['fast'], rec['complex'], rec['control']), ('qwen3:8b', 'gemma3:27b', 'mistral-small:24b'))
        self.assertEqual(mod.recommend([{'name': 'qwen3:8b', 'billions': 8.2}])['control'], 'qwen3:8b')
        self.assertIn('Aucun modèle', mod.recommend([])['why'])

    def test_appliquer_passe_par_l_enregistrement_verifie(self):
        saved = []
        with patch('agent.preferences363.save_model', side_effect=lambda desk, model, role: saved.append((role, model))):
            out = mod.apply(self.desk, {'fast': 'qwen3:8b', 'complex': 'gemma3:27b', 'control': ''})
        self.assertEqual(saved, [('fast', 'qwen3:8b'), ('complex', 'gemma3:27b')])
        self.assertTrue(out['ok'])

    def test_banc_sur_les_cas_du_cabinet(self):
        _sent_memory(self.desk.c['state_dir'], [('s1', MID + '@x', '[]', iso(), 'Bonjour,\nL’audience est renvoyée ; je vous tiens informé.\nCordialement',
                                                 'brouillon', '{}', 'matched_draft_and_sent', 'a', json.dumps([['client@example.test', 'client']]))])
        db = sqlite3.connect(Path(self.desk.c['state_dir']) / 'sent-memory.sqlite3')
        db.execute("INSERT INTO pairs5667 VALUES('s1','<m1@example.test>',?)", (iso(),))
        db.commit()
        db.close()
        built = mod.build_cases(self.desk, fetch=lambda mid: 'Où en est mon audience, écrit client@example.test ?')
        self.assertEqual(built['cases'], 1)
        case = self.desk.db.execute('SELECT * FROM banc5667_cases').fetchone()
        self.assertNotIn('client@example.test', case['incoming'])                       # anonymisé avant d'être enregistré

        class Model:
            def complete(self, messages, temperature=None, max_tokens=0, json_schema=None):
                return 'Bonjour,\nL’audience est renvoyée ; je vous tiens informé.\nCordialement'
        out = mod.run(self.desk, 'qwen3:8b', model=Model(), clock=iter([0.0, 2.5]).__next__)
        self.assertEqual(out['cases'], 1)
        self.assertGreater(out['score'], 90)
        self.assertIn('qwen3:8b', mod.html.__module__ and json.dumps(mod.runs(self.desk)))
        with self.assertRaisesRegex(Stop, 'modele_invalide'):
            mod.run(self.desk, 'mauvais nom ; rm')

    def test_droits_des_routes(self):
        from agent import web5667
        self.assertTrue(web5667.allowed('avocat', 'corrections/decider'))
        self.assertFalse(web5667.allowed('avocat', 'modeles/appliquer'))
        self.assertTrue(web5667.allowed('administrateur', 'banc/lancer'))
        self.assertFalse(web5667.allowed('assistant', 'corrections/decider'))


if __name__ == '__main__':
    unittest.main()
