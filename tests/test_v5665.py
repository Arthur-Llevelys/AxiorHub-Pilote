"""5.6.65 : corrections de l'audit de la 5.6.64 (19 octobre 2026), cas complémentaires recommandés par l'audit. Les contre-tests de
l'audit eux-mêmes sont dans test_audit5664.py et test_files5664.py. Données fictives (example.test)."""
from datetime import datetime, timedelta, timezone
import json
import unittest
from unittest.mock import patch

import test_v5652 as base
import test_v5657 as f57
from agent import cadence5662, confidentiel5664 as conf, crm5636, crm5657, modele5651, queue521, savoir5659
from agent.common import Stop, load_matters
from agent.index import DocumentIndex

ACTOR = 'avocat@example.test'


def iso(**delta):
    return (datetime.now(timezone.utc) + timedelta(**delta)).isoformat()


class Confidentialite(base.Base):
    def test_index_existant_requalifie_sans_reindexation(self):
        """R01 : un index construit avant la 5.6.65 (sans statut de source) est requalifié à la lecture, ETag inchangé."""
        matter = load_matters(self.desk.c)[0]
        index = DocumentIndex(self.desk.c['state_dir'], self.desk.c.get('rag'), self.desk.c.get('ollama'))
        self.addCleanup(index.db.close)
        text = 'Lettre officielle — correspondance confidentielle entre avocats.\n' + ('Texte.\n' * 400) + 'PASSAGE772'
        sid, _ = index.put_source(matter, '/Dossiers/DEMO/Lettre.txt', text, 'e1')
        index.db.execute("UPDATE knowledge_chunks SET meta='{}' WHERE source_id=?", (sid,))   # état d'une version antérieure
        index.db.commit()
        self.assertEqual(index.put_source(matter, '/Dossiers/DEMO/Lettre.txt', text, 'e1'), (sid, False))   # pas de réindexation
        self.assertTrue(index.source_confidential(matter, sid))
        metas = [json.loads(r[0]) for r in index.db.execute('SELECT meta FROM knowledge_chunks WHERE source_id=?', (sid,))]
        self.assertTrue(all(m[conf.FLAG] and m['politique_confidentialite'] == conf.POLICY for m in metas))
        plain, _ = index.put_source(matter, '/Dossiers/DEMO/Assignation.txt', 'Assignation devant le tribunal.', 'e2')
        self.assertFalse(index.source_confidential(matter, plain))

    def test_base_explicite_gardee_base_automatique_ecartee(self):
        """R02 : seule une base désignée par l'avocat peut être une correspondance confidentielle."""
        explicit = {'kind': 'base_document', 'path': '/D/Courrier confidentiel.docx', 'excerpt': '', 'selection': 'explicite'}
        automatic = dict(explicit, selection='automatique')
        self.assertEqual(conf.filter_packet([explicit]), ([explicit], 0))
        self.assertEqual(conf.filter_packet([automatic]), ([], 1))

    def test_nature_publique_etablie_par_le_serveur_seulement(self):
        """R04 : une piste juridique non vérifiée (type fourni par une entrée) reste contrôlée ; une clause de contrat n'est pas visée."""
        lead = {'kind': 'legal_lead', 'path': 'https://exemple.test', 'excerpt': 'Correspondance confidentielle entre avocats (article 66-5).'}
        self.assertEqual(conf.filter_packet([lead])[1], 1)
        self.assertFalse(conf.is_confidential('Accord de confidentialité.pdf'))
        self.assertFalse(conf.is_confidential('Contrat.pdf', 'Article 9 — Confidentialité : les parties gardent confidentielles les informations.'))

    def test_fait_de_la_memoire_porte_sa_provenance(self):
        """R11 : un fait de la mémoire structurée tiré d'une correspondance confidentielle est retiré des sources d'un document."""
        fact = {'kind': 'structured_memory', 'path': 'Mémoire structurée du dossier', 'excerpt': 'Offre de 20 000 euros',
                'source_paths': ['/Dossiers/DEMO/Lettre confidentielle.eml']}
        self.assertEqual(conf.filter_packet([fact])[1], 1)

    def test_information_mixte_gardee_en_interne(self):
        """R03 : en usage interne, l'information mixte reste, marquée ; pour un tiers, elle est écartée avec un message."""
        from agent import contexte5651, memoire5651
        info = {'text': 'Offre de 20 000 euros', 'author': '', 'author_label': 'Document', 'origin': 'document', 'proof': '',
                'review': 'enregistre', 'relevance': 'pertinent', 'date_value': '2026-09-01', 'date_precision': 'jour', 'kind': 'fait',
                'sources': [{'path': '/D/Lettre confidentielle.eml'}, {'path': '/D/Justificatif.pdf'}]}

        def run(**kw):
            with patch.object(modele5651, 'ensure_schema'), patch.object(memoire5651, 'ensure_schema'), \
                    patch.object(modele5651, 'refresh', return_value={'state': 'ok'}), patch.object(modele5651, 'parties', return_value=[]), \
                    patch.object(modele5651, 'reference_procedure', return_value={}), patch.object(modele5651, 'assertions', return_value=[info]), \
                    patch.object(memoire5651, 'decisions', return_value=[]), patch.object(memoire5651, 'gaps', return_value=[]):
                return contexte5651.for_drafting(self.desk, base.MID, 'note', **kw)
        external = run()
        self.assertNotIn('faits_dates', external)
        self.assertIn('Dont 1 aussi appuyée(s) par d’autres pièces', external['correspondances_confidentielles'])
        internal = run(confidential=True)
        self.assertIn('confidentiel — usage interne', internal['faits_dates'][0]['confidentialite'])


class Contacts(base.Base):
    def setUp(self):
        super().setUp()
        crm5657.save_settings(self.desk, ACTOR, {'actif': True, 'dossiers': 'INBOX', 'periode': '90'})

    def test_message_definitivement_illisible_abandonne_apres_trois_essais(self):
        """R05 : relu aux passages suivants, puis abandonné et signalé au troisième échec ; les messages suivants sont importés."""
        own = self.desk.c['mail']['from_address']

        class Box(f57.FakeBox):
            def fetch(self, folder, uid, headers_only=False):
                if uid == '1':
                    raise Stop('message_introuvable')
                return super().fetch(folder, uid, headers_only)
        box = Box({1: f57.msg('Premier <premier@example.test>', own), 2: f57.msg('Second <second@example.test>', own)})
        runs = [crm5657.collect(self.desk, box) for _ in range(3)]
        self.assertEqual([r['errors'] for r in runs], [1, 1, 1])
        self.assertIn('illisible après 3 essais', ' '.join(runs[2]['detail']))
        self.assertIn('second@example.test', {c['email'] for c in crm5636.search(self.desk)})

    def test_identite_du_cabinet_reste_exclue(self):
        """R06 : le réexamen ne vaut pas pour une adresse exclue par sa règle propre (adresse de service)."""
        own = self.desk.c['mail']['from_address']
        box = f57.FakeBox({1: f57.msg('Alertes <noreply@example.test>', own)})
        crm5657.collect(self.desk, box)
        box.messages[2] = f57.msg('Alertes <noreply@example.test>', own)
        crm5657.collect(self.desk, box)
        self.assertNotIn('noreply@example.test', {c['email'] for c in crm5636.search(self.desk)})


class SavoirFaire(base.Base):
    def test_version_approuvee_publiee_au_cabinet(self):
        """R12 : après approbation, la version (anonymisée) est visible de tous ; le brouillon suivant reste dans le dossier."""
        from agent import membres5650 as mb
        out = savoir5659.propose(self.desk, ACTOR, {'kind': 'clause', 'title': 'Clause type', 'body': 'Texte PUBLIE772.'}, matter=base.MID)
        savoir5659.decide(self.desk, ACTOR, out['resource'], 1, 'approuver')
        savoir5659.propose(self.desk, ACTOR, {'resource': out['resource'], 'kind': 'clause', 'title': 'Clause type', 'body': 'Texte BROUILLON772.'})
        mb.set_viewer({base.MID})
        self.addCleanup(mb.clear)
        page = json.dumps(savoir5659.listing(self.desk), ensure_ascii=False)
        self.assertIn('PUBLIE772', page)
        self.assertNotIn('BROUILLON772', page)
        self.assertEqual(savoir5659.queue_items(self.desk), [])
        mb.clear()
        self.assertIn('BROUILLON772', json.dumps(savoir5659.listing(self.desk), ensure_ascii=False))

    def test_revision_rattachee_a_un_autre_dossier_refusee(self):
        out = savoir5659.propose(self.desk, ACTOR, {'kind': 'clause', 'title': 'Clause', 'body': 'Texte.'}, matter=base.MID)
        with self.assertRaisesRegex(Stop, 'savoir_faire_autre_dossier'):
            savoir5659.propose(self.desk, ACTOR, {'resource': out['resource'], 'kind': 'clause', 'title': 'Clause', 'body': 'Texte 2.'},
                               matter='DOS-AUTRE')

    def test_route_par_identifiant_controlee(self):
        from agent import membres5650 as mb
        out = savoir5659.propose(self.desk, ACTOR, {'kind': 'clause', 'title': 'Clause', 'body': 'Texte.'}, matter=base.MID)
        found = mb.route_objects(self.desk.c['state_dir'], '/agent-courriel/api440/m5659/savoir/decider', {'resource': out['resource']})
        self.assertEqual(found, {base.MID})


class File(base.Base):
    def _pending(self):
        return self.desk.db.execute("SELECT COUNT(*) FROM jobs WHERE status='pending' AND kind IN ('index','monitor_matter','extract_facts460')").fetchone()[0]

    def test_admission_du_lot_entier(self):
        """R08 : le lot de 3 entre seulement s'il tient en entier ; un travail déjà en file ne consomme pas de place."""
        batch = lambda m: [('index', {'matter': m}, 50), ('monitor_matter', {'matter': m}, 55), ('extract_facts460', {'matter': m}, 58)]
        self.assertIsNotNone(self.desk.admit(batch('A')))            # 0 + 3
        self.assertIsNotNone(self.desk.admit(batch('B')))            # 3 + 3 = 6
        self.assertIsNone(self.desk.admit(batch('C')))               # 6 + 3 : refusé, rien n'est inséré
        self.assertEqual(self._pending(), 6)
        self.assertIsNotNone(self.desk.admit(batch('A')))            # déjà en file : dédoublonné, aucune place nécessaire
        self.assertEqual(self._pending(), 6)

    def test_travail_futur_compte_quand_il_devient_echu(self):
        """R14 : un travail reporté ne pèse pas tant qu'il n'est pas échu ; échu et en attente, il compte."""
        jid = self.desk.enqueue('assistant_answer', {'question': 'Q'}, origin='user')
        self.desk.db.execute('UPDATE jobs SET created=?,not_before=? WHERE id=?', (iso(hours=-2), iso(seconds=30), jid))
        self.desk.db.commit()
        self.assertEqual(cadence5662.busy(self.desk), '')
        self.desk.db.execute('UPDATE jobs SET not_before=? WHERE id=?', (iso(minutes=-5), jid))
        self.desk.db.commit()
        self.assertEqual(cadence5662.busy(self.desk), 'demande_avocat_en_attente')

    def test_purge_lignes_anciennes_sans_origine(self):
        """R17 : la déduction par le type ne sert qu'aux travaux sans origine enregistrée."""
        legacy = self.desk.enqueue('index', {'matter': 'X'}, priority=50)
        self.desk.db.execute("UPDATE jobs SET origin='legacy' WHERE id=?", (legacy,))
        control = self.desk.enqueue('health', {'n': 1}, origin='control')
        self.desk.db.commit()
        out = queue521.purge_automatic(self.desk)
        status = {r['id']: r['status'] for r in self.desk.db.execute('SELECT id,status FROM jobs')}
        self.assertEqual((status[legacy], status[control]), ('cancelled', 'pending'))
        self.assertEqual(out['kept'], 1)


if __name__ == '__main__':
    unittest.main()
