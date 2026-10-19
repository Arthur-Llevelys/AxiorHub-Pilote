"""5.6.64 : retour du cabinet du 19 octobre 2026 — Aujourd'hui encombré (133 éléments dont 76 « À décider »), salve de travaux à la
reprise de l'IA, lacunes « Lecture de … » pour des fichiers temporaires et des archives. Données fictives (example.test)."""
from datetime import datetime, timedelta, timezone
import unittest
from unittest.mock import patch

import test_v5652 as base
from agent import file5651, memoire5651


def _now(days=0):
    return (datetime.now(timezone.utc) - timedelta(days=days)).isoformat()


class VueDuJour(unittest.TestCase):
    def test_tri_entre_le_jour_et_la_tenue_des_dossiers(self):
        every = [{'class': 'decision_agent', 'object': 'decision:x', 'matter': 'A'},
                 {'class': 'informations', 'object': 'informations:propose', 'matter': 'A', 'matter_name': 'DOSSIER A'},
                 {'class': 'identite', 'object': 'parties:1:2', 'matter': 'B', 'matter_name': 'DOSSIER B'},
                 {'class': 'projet', 'object': 'suivi:u1', 'matter': 'A', 'matter_name': 'DOSSIER A'},
                 {'class': 'projet', 'object': 'relire:mail:1', 'matter': '', 'when': _now(2)},
                 {'class': 'projet', 'object': 'relire:mail:2', 'matter': 'C', 'matter_name': 'DOSSIER C', 'when': _now(40)},
                 {'class': 'livrable', 'object': 'document:d', 'matter': 'A', 'when': _now(1)}]
        keep, curation, client, old = file5651.today_split(every)
        self.assertEqual([x['object'] for x in keep], ['decision:x', 'relire:mail:1', 'document:d'])
        self.assertEqual(len(curation), 2)
        self.assertEqual(len(client), 1)
        self.assertEqual(len(old), 1)
        html = file5651._today_summary('/p', curation, client, old)
        self.assertIn('Hors de la vue du jour (4)', html)
        self.assertIn('2 point(s) de fiches de dossiers', html)
        self.assertIn('/p/dossier?id=A&amp;vue=vue', html)
        self.assertIn('1 point(s) d’avancement client proposé(s)', html)
        self.assertIn('1 projet(s) de plus de 14 jours', html)

    def test_la_page_du_dossier_garde_tout(self):
        src = (base.Path(__file__).resolve().parents[1] / 'agent' / 'file5651.py').read_text(encoding='utf-8')
        self.assertIn("if not matter:   # 5.6.64 : vue « du jour » sur Aujourd'hui", src)


class PiecesEtLacunes(base.Base):
    def _doc(self, name, reading):
        self.desk.db.execute('INSERT INTO d51_documents(matter,path,etag,name,nature,nature_source,reading,error_category,error,doc_date,modified,text_sha,updated) '
                             'VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?)', ('DOS-001', '/Dossiers/DEMO/' + name, 'e', name, 'a_classer', 'regle', reading, 'erreur', '',
                                                                  '', '', '', _now()))

    def test_pas_de_lacune_pour_ce_qui_n_est_pas_une_piece(self):
        for name in ('E065839B.tmp', '~$Conclusions.docx', '.~lock.Note.odt#', 'Thumbs.db', 'Raccourci.lnk', 'Conclusions.pdf'):
            self.assertEqual(memoire5651.not_a_document(name), name != 'Conclusions.pdf', name)
        from agent import modele5651
        modele5651.ensure_schema(self.desk)
        memoire5651.ensure_schema(self.desk)
        self._doc('E065839B.tmp', 'erreur')
        self._doc('DEPOT CONCLUSIONS 3.zip', 'non_prise_en_charge')
        self._doc('PV CONSTAT scanne.pdf', 'erreur')
        self.desk.db.commit()
        memoire5651.open_gap(self.desk, 'DOS-001', 'Lecture de « E065839B.tmp »', 'ancienne lacune', '/Dossiers/DEMO/E065839B.tmp', [], 'facultatif',
                             'fichier_illisible')          # ouverte par une version précédente
        memoire5651.sync_gaps(self.desk, 'DOS-001')
        open_ = [g['expected'] for g in memoire5651.gaps(self.desk, 'DOS-001')]
        self.assertEqual(open_, ['Lecture de « PV CONSTAT scanne.pdf »'])  # seul un vrai document illisible reste à signaler
        self.assertEqual(memoire5651.changes(self.desk, 'DOS-001'), [])     # fermeture silencieuse : rien dans « Changements récents »

    def test_journal_du_dossier_sans_lacunes_de_lecture_abandonnees(self):
        memoire5651.ensure_schema(self.desk)
        memoire5651.record_change(self.desk, 'DOS-001', 'correction', 'lacune', 'g1', 'abandonnee', 'Lacune abandonnée : Lecture de « X.tmp »')
        memoire5651.record_change(self.desk, 'DOS-001', 'correction', 'lacune', 'g2', 'resolue', 'Information fournie : date de signification')
        titles = [c['title'] for c in memoire5651.changes(self.desk, 'DOS-001')]
        self.assertEqual(titles, ['Information fournie : date de signification'])
        gid = memoire5651.open_gap(self.desk, 'DOS-001', 'Lecture de « Scan.pdf »', 'illisible', '/x/Scan.pdf', [], 'facultatif', 'fichier_illisible')
        memoire5651.resolve_gap(self.desk, 'DOS-001', gid, 'avocat@example.test', state='abandonnee')
        self.assertEqual(len(memoire5651.changes(self.desk, 'DOS-001')), 1)    # abandon d'une lecture : journal technique


class PasDeSalve(base.Base):
    def test_dossier_modifie_reporte_quand_la_file_est_chargee(self):
        from agent import watch430

        class Client:
            refused = []

            def inventory_step(self, path, state):
                return {'files': {path + '/Nouveau.pdf': {'etag': 'e1', 'modified': _now()}}}, True
        for i in range(6):
            self.desk.db.execute("INSERT INTO jobs(kind,args,status,created,priority) VALUES('index',?,'pending',?,50)", ('{"matter":"M%d"}' % i, _now()))
        self.desk.db.commit()
        matter = {'id': 'DOS-001', 'path': '/Dossiers/DEMO'}
        with patch('agent.watch430.indexable_matters', return_value=[matter]), patch('agent.watch430.ignored', return_value=[]), \
                patch('agent.watch430.recent_change', return_value=True), patch('agent.automation568.observe', return_value=set()), \
                patch('agent.notices440.observe_inventory'), patch('agent.echeances450.observe_inventory'):
            result = watch430.documents_check(self.desk, dav=Client())
        self.assertEqual((result['deferred'], result['changed']), (1, 0))
        self.assertEqual(self.desk.db.execute("SELECT COUNT(*) FROM jobs WHERE kind IN ('monitor_matter','extract_facts460')").fetchone()[0], 0)
        self.assertTrue(self.desk.db.execute("SELECT 1 FROM live_dirty_v430 WHERE source='documents' AND object_key='DOS-001'").fetchone())   # repris plus tard


def _info(text, path, review='enregistre'):
    return {'text': text, 'author': '', 'author_label': 'Document', 'origin': 'document', 'proof': '', 'review': review, 'relevance': 'pertinent',
            'date_value': '2026-09-01', 'date_precision': 'jour', 'sources': [{'path': path, 'extrait': ''}], 'kind': 'fait'}


class CorrespondancesConfidentielles(base.Base):
    """Choix du cabinet : correspondances entre avocats (art. 66-5) lisibles en interne, marquées, jamais sources d'un document pour un tiers."""

    def test_reconnaissance_prudente(self):
        from agent.confidentiel5664 import is_confidential
        self.assertTrue(is_confidential('/Dossiers/DEMO/COURRIEL CONFIDENTIEL DE ME DUPONT.eml'))
        self.assertTrue(is_confidential('', '', 'Lettre officielle — confidentielle'))
        self.assertTrue(is_confidential('Courrier.pdf', 'Mon cher confrère, sous le sceau de la confidentialité entre avocats, ...'))
        self.assertTrue(is_confidential('Courrier.pdf', 'Correspondance soumise à l’article 66-5 de la loi du 31 décembre 1971.'))
        self.assertFalse(is_confidential('Courrier NON CONFIDENTIEL.pdf'))
        self.assertFalse(is_confidential('Contrat.pdf', 'Article 12 — Confidentialité. Les parties s’engagent à garder confidentielles ...'))
        self.assertFalse(is_confidential('Conclusions.pdf', 'Plaise au tribunal'))

    def test_sources_d_un_projet_documentaire(self):
        from agent.confidentiel5664 import filter_packet
        packet = [{'kind': 'base_document', 'path': '/D/Lettre confidentielle.docx', 'excerpt': ''},
                  {'kind': 'document', 'path': '/D/COURRIEL CONFIDENTIEL DE ME X.eml', 'excerpt': ''},
                  {'kind': 'mail', 'path': '', 'excerpt': 'Correspondance confidentielle entre avocats', 'metadata': {'subject': 'Proposition'}},
                  {'kind': 'document', 'path': '/D/Assignation.pdf', 'excerpt': 'Le demandeur'}]
        kept, removed = filter_packet(packet)
        self.assertEqual(removed, 2)
        self.assertEqual([s['path'] for s in kept], ['/D/Lettre confidentielle.docx', '/D/Assignation.pdf'])   # le document travaillé reste

    def _structure(self, **kw):
        from agent import contexte5651, memoire5651 as m, modele5651
        infos = [_info('Proposition transactionnelle de 20 000 €', '/D/COURRIEL CONFIDENTIEL DE ME X.eml'),
                 _info('Assignation délivrée le 1er septembre', '/D/Assignation.pdf')]
        with patch.object(modele5651, 'ensure_schema'), patch.object(m, 'ensure_schema'), \
                patch.object(modele5651, 'refresh', return_value={'state': 'ok'}), patch.object(modele5651, 'parties', return_value=[]), \
                patch.object(modele5651, 'reference_procedure', return_value={}), patch.object(modele5651, 'assertions', return_value=infos), \
                patch.object(m, 'decisions', return_value=[]), patch.object(m, 'gaps', return_value=[]):
            return contexte5651.for_drafting(self.desk, 'DOS-001', 'conclusions', **kw)

    def test_contexte_structure_pour_un_tiers_et_en_interne(self):
        out = self._structure()
        texts = [x['texte'] for x in out.get('faits_dates', [])]
        self.assertEqual(texts, ['Assignation délivrée le 1er septembre'])
        self.assertIn('1 information(s)', out['correspondances_confidentielles'])
        internal = self._structure(confidential=True)
        conf = [x for x in internal['faits_dates'] if x['texte'].startswith('Proposition')]
        self.assertEqual(len(conf), 1)
        self.assertIn('confidentiel — usage interne', conf[0]['confidentialite'])
        self.assertNotIn('correspondances_confidentielles', internal)

    def test_redaction_libre_sans_correspondance_confidentielle(self):
        from agent.docrequest520 import _without_confidential
        ctx = {'extraits': [{'fichier': '/D/COURRIEL CONFIDENTIEL DE ME X.eml', 'texte': 'offre'}, {'fichier': '/D/PV.pdf', 'texte': 'constat'}],
               'courriels': [{'objet': 'Confidentiel — proposition', 'extrait': '...'}, {'objet': 'Calendrier', 'extrait': 'audience'}],
               'pieces_jointes': [{'fichier': '/D/Lettre confidentielle Me X.pdf', 'texte': ''}]}
        for kind in ('note', 'conclusions', 'courrier'):   # même une « note / consultation » peut être remise au client
            out = _without_confidential(ctx, kind)
            self.assertEqual([x['fichier'] for x in out['extraits']], ['/D/PV.pdf'])
            self.assertEqual([x['objet'] for x in out['courriels']], ['Calendrier'])
            self.assertIn('2 correspondance(s)', out['correspondances_confidentielles_ecartees'])
            self.assertEqual(len(out['pieces_jointes']), 1)                                   # choix de l'avocat respecté
            self.assertIn('Lettre confidentielle Me X.pdf', out['attention_confidentialite'])
        self.assertEqual(len(ctx['extraits']), 2)                                             # contexte d'origine intact

    def test_marque_dans_la_fiche_du_dossier(self):
        from agent import vue5651
        html = vue5651._source_links('/p', 'DOS-001', [{'path': '/D/COURRIEL CONFIDENTIEL DE ME X.eml'}, {'path': '/D/Assignation.pdf'}])
        self.assertEqual(html.count('c5664-conf'), 1)
        self.assertIn('confidentiel — usage interne', html)


if __name__ == '__main__':
    unittest.main()
