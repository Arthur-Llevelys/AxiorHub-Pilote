"""5.6.40 : contre-tests de l'audit 5.6.39 (B01 à B07, P01, P02, E01 à E03).

Données d'essai fictives ; stockage Nextcloud, modèle d'IA et documents Word simulés.
"""
import base64
from datetime import datetime, timedelta, timezone
import io
import json
import re
import unittest
import unittest.mock
import zipfile

from agent.common import Stop
from agent.erreurs5634 import Qualified
import test_v5634 as t34
import test_v5639 as t39

W = 'http://schemas.openxmlformats.org/wordprocessingml/2006/main'


def docx(body_xml, header_xml=''):
    """Document Word minimal (corps donné en XML WordprocessingML, en-tête facultatif)."""
    out = io.BytesIO()
    with zipfile.ZipFile(out, 'w') as z:
        z.writestr('[Content_Types].xml', '<?xml version="1.0" encoding="UTF-8"?><Types xmlns="http://schemas.openxmlformats.org/package/2006/'
                   'content-types"><Default Extension="xml" ContentType="application/xml"/><Default Extension="rels" ContentType="application/'
                   'vnd.openxmlformats-package.relationships+xml"/><Override PartName="/word/document.xml" ContentType="application/'
                   'vnd.openxmlformats-officedocument.wordprocessingml.document.main+xml"/></Types>')
        z.writestr('_rels/.rels', '<?xml version="1.0" encoding="UTF-8"?><Relationships xmlns="http://schemas.openxmlformats.org/package/2006/'
                   'relationships"><Relationship Id="rId1" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/officeDocument" '
                   'Target="word/document.xml"/></Relationships>')
        z.writestr('word/document.xml', '<?xml version="1.0" encoding="UTF-8"?><w:document xmlns:w="%s"><w:body>%s<w:sectPr/></w:body>'
                   '</w:document>' % (W, body_xml))
        if header_xml:
            z.writestr('word/header1.xml', '<?xml version="1.0" encoding="UTF-8"?><w:hdr xmlns:w="%s">%s</w:hdr>' % (W, header_xml))
    return out.getvalue()


def p(*runs):
    """Paragraphe : chaque run est un texte, ou (texte, 'b'|'i') pour le gras ou l'italique."""
    xml = ''
    for r in runs:
        text, style = (r, '') if isinstance(r, str) else r
        rpr = {'b': '<w:rPr><w:b/></w:rPr>', 'i': '<w:rPr><w:i/></w:rPr>'}.get(style, '')
        xml += '<w:r>%s<w:t xml:space="preserve">%s</w:t></w:r>' % (rpr, text.replace('&', '&amp;').replace('<', '&lt;'))
    return '<w:p>%s</w:p>' % xml


def table(*rows):
    return '<w:tbl>%s</w:tbl>' % ''.join('<w:tr>%s</w:tr>' % ''.join('<w:tc>%s</w:tc>' % ''.join(cell) for cell in row) for row in rows)


def document_xml(raw, part='word/document.xml'):
    return zipfile.ZipFile(io.BytesIO(raw)).read(part).decode('utf-8')


class AuditB01(unittest.TestCase):
    """B01 (P0) : un montant d'un autre poste n'est jamais validé ; preuve par le libellé ET le montant de l'extrait."""

    B = {'devise': 'EUR', 'unite': 'unites', 'exercice': '2025'}
    TEXT = 'Chiffre d’affaires 2025 : 812 450 EUR\nCharges externes : 20 000 EUR'

    def check(self, x, text, exercice='2025'):
        from agent.comptes5639 import check
        return check({**self.B, **x}, text, exercice)[0]

    def test_b01_amount_of_the_next_unknown_line_is_refused(self):
        self.assertIsNotNone(self.check({'libelle': 'Chiffre d’affaires', 'montant': '20 000 EUR', 'extrait': 'Chiffre d’affaires 2025 : 812 450 EUR'},
                                        self.TEXT))
        self.assertIsNone(self.check({'libelle': 'Chiffre d’affaires', 'montant': '812 450 EUR',
                                      'extrait': 'Chiffre d’affaires 2025 : 812 450 EUR'}, self.TEXT))      # témoin C01
        self.assertIsNotNone(self.check({'libelle': 'Chiffre d’affaires', 'montant': '20 000 EUR', 'extrait': self.TEXT}, self.TEXT))
        same_line = 'Chiffre d’affaires 2025 : 812 450 EUR Charges externes : 20 000 EUR'
        self.assertIsNotNone(self.check({'libelle': 'Chiffre d’affaires', 'montant': '20 000 EUR', 'extrait': same_line}, same_line))

    def test_b01_acceptance_cases(self):
        table_ = 'Compte de résultat (en euros)\nRubrique 2025 2024\nChiffre d’affaires\n812 450  790 120\nRésultat net  20 000  18 000'
        self.assertIsNone(self.check({'libelle': 'Chiffre d’affaires', 'montant': '812 450', 'extrait': 'Chiffre d’affaires\n812 450  790 120'}, table_))
        ocr = 'Comptes annuels de l’exercice 2025 (en euros)\nTotal du bilan : 1 204\n350 €'
        self.assertIsNone(self.check({'libelle': 'Total du bilan', 'montant': '1 204 350 €', 'extrait': 'Total du bilan : 1 204\n350 €'}, ocr))
        twice = 'Comptes 2025 (en euros)\nChiffre d’affaires : 20 000 €\nRésultat net : 20 000 €'
        self.assertIsNone(self.check({'libelle': 'Résultat net', 'montant': '20 000 €', 'extrait': 'Résultat net : 20 000 €'}, twice))
        ambiguous = 'Comptes 2025 (en euros)\nChiffre d’affaires : 812 450 € contre 790 120 €'
        self.assertEqual(self.check({'libelle': 'Chiffre d’affaires', 'montant': '812 450 €',
                                     'extrait': 'Chiffre d’affaires : 812 450 € contre 790 120 €'}, ambiguous), 'rattachement_ambigu')
        signed = 'Comptes 2025 (en euros)\nCapitaux propres : (12 500) €'
        self.assertEqual(self.check({'libelle': 'Capitaux propres', 'montant': '12 500 €', 'extrait': 'Capitaux propres : (12 500) €'}, signed),
                         'montant_absent_de_l_extrait')
        usd = 'Frais divers : 100 EUR.\nRésultat net 2025 : 20 000 USD.'
        self.assertEqual(self.check({'libelle': 'Résultat net', 'montant': '20 000 EUR', 'extrait': 'Résultat net 2025 : 20 000 USD.'}, usd),
                         'devise_differente_des_comptes')
        kilo = 'Comptes 2025 — en milliers d’euros\nChiffre d’affaires net : 812'
        self.assertEqual(self.check({'libelle': 'Chiffre d’affaires', 'montant': '812 450', 'extrait': 'Chiffre d’affaires net : 812'}, kilo),
                         'unite_differente_des_comptes')
        old = 'Comptes annuels\nChiffre d’affaires 2024 : 790 000 EUR.'
        self.assertEqual(self.check({'libelle': 'Chiffre d’affaires', 'montant': '790 000 EUR', 'extrait': 'Chiffre d’affaires 2024 : 790 000 EUR'},
                                    old), 'exercice_different')

    def test_b01_report_and_method(self):
        from agent import corporate5636 as co, comptes5639
        self.assertEqual((comptes5639.METHODE, co.METHODE_COMPTES), (4, 4))
        answer = {'activite': 'a', 'evenements': 'b', 'perspectives': 'c', 'chiffres': [
            {**self.B, 'libelle': 'Chiffre d’affaires', 'montant': '20 000 EUR', 'extrait': 'Chiffre d’affaires 2025 : 812 450 EUR'},
            {**self.B, 'libelle': 'Chiffre d’affaires', 'montant': '812 450 EUR', 'extrait': 'Chiffre d’affaires 2025 : 812 450 EUR'}]}

        class Model:
            def complete(self, *a, **k):
                return json.dumps(answer)

        class Desk:
            c = {'documents': {}}
        with unittest.mock.patch('agent.documents.extract', return_value=self.TEXT):
            out = co.accounts_analysis(Desk(), {'matter': ''}, b'', 'comptes.pdf', model=Model(), exercice='2025')
        self.assertEqual([x['statut'] for x in out['controle_chiffres']], ['a_verifier', 'justifie'])
        self.assertEqual(out['controle_chiffres'][1]['position_montant'][0], self.TEXT.index('812 450'))

    def test_p01_one_analysis_per_document(self):
        from agent import corporate5636 as co, comptes5639
        figures = [{**self.B, 'libelle': 'Chiffre d’affaires', 'montant': '812 450 EUR', 'extrait': 'Chiffre d’affaires 2025 : 812 450 EUR'}] * 6

        class Model:
            def complete(self, *a, **k):
                return json.dumps({'activite': 'a', 'evenements': 'b', 'perspectives': 'c', 'chiffres': figures})

        class Desk:
            c = {'documents': {}}
        real = comptes5639._facts
        calls = []
        with unittest.mock.patch('agent.documents.extract', return_value=self.TEXT), \
                unittest.mock.patch.object(comptes5639, '_facts', side_effect=lambda a: calls.append(1) or real(a)):
            out = co.accounts_analysis(Desk(), {'matter': ''}, b'', 'comptes.pdf', model=Model(), exercice='2025')
        self.assertEqual(len(calls), 1)
        self.assertEqual({x['statut'] for x in out['controle_chiffres']}, {'justifie'})

    def test_b01_recheck_shows_the_changes(self):
        from agent.corporate5636 import _control_changes
        html = _control_changes([{'libelle': 'CA', 'montant': '20 000', 'statut': 'justifie'}],
                                [{'libelle': 'CA', 'montant': '20 000', 'statut': 'a_verifier', 'cause': 'montant_absent_de_l_extrait'}],
                                ['/Dossiers/X/JURIDIQUE/Rapport de gestion - X.docx'])
        self.assertIn('1 écart(s)', html)
        self.assertIn('justifié → à vérifier (montant absent de l extrait)', html.replace('&#x27;', "'").replace('’', ' '))
        self.assertIn('Rapport de gestion - X.docx', html)


class LibraryEngine(unittest.TestCase):
    """B04 (validation commune), B07 (mise en forme des balises), B03 (champs du Word)."""

    def test_b04_validator_reports_each_structure_error(self):
        from agent import bibliotheque_word5640 as w
        codes = lambda lines: [c for c, _, _ in w.validate(lines)]   # noqa: E731
        self.assertEqual(codes(['[[si a]]', 'x']), ['bloc_non_ferme'])
        self.assertEqual(codes(['x', '[[fin]]']), ['fin_en_trop'])
        self.assertEqual(codes(['[[sinon]]']), ['sinon_isole'])
        self.assertEqual(codes(['[[si a]]', '[[sinon]]', '[[sinon]]', '[[fin]]']), ['sinon_repete'])
        self.assertEqual(codes(['A [[si a]]x']), ['condition_en_ligne_non_fermee'])
        self.assertEqual(codes(['[[si a]]', '[[si b]]', 'x', '[[fin]]', '[[fin]]', 'A [[si a]]x[[sinon]]y[[fin]] B']), [])
        self.assertEqual(codes(['[[ fin ]]']), ['balise_condition_invalide'])
        self.assertIn('Ligne 1', w.describe(w.validate(['[[si clause_penale]]', 'x']), 'Ligne'))

    def test_b07_value_keeps_the_formatting_of_its_tag(self):
        from agent import bibliotheque5639 as b
        raw = docx(p('Partie A : ', ('{{partie_a}}', 'b'), ' signe le présent acte.') + '<w:p><w:bookmarkStart w:id="0" w:name="signet"/>'
                   '<w:r><w:t>{{ville}}</w:t></w:r><w:bookmarkEnd w:id="0"/></w:p>' + p('{{', ('partie_a', 'i'), '}} répartie'))
        t = b._build('accord_confidentialite', None)
        out = document_xml(b.fill_word(raw, t, {'partie_a': 'ALPHA_FICTIVE', 'ville': 'Lyon'}, {}))
        self.assertIn('<w:r><w:rPr><w:b/></w:rPr><w:t xml:space="preserve">ALPHA_FICTIVE</w:t></w:r>', out)
        self.assertIn('<w:t xml:space="preserve">Partie A : </w:t>', out)
        self.assertIn('<w:t xml:space="preserve"> signe le présent acte.</w:t>', out)
        self.assertIn('w:bookmarkStart w:id="0" w:name="signet"', out)                                # signet conservé
        self.assertIn('<w:rPr><w:i/></w:rPr><w:t xml:space="preserve">ALPHA_FICTIVE</w:t>', out)     # balise répartie : style du nom
        self.assertNotIn('{{', out)

    def test_b03_b04_word_keys_tables_headers_and_cells(self):
        from agent import bibliotheque_word5640 as w
        raw = docx(p('Client {{mon_', 'client}}') + table([[p('[[si audit_special]]'), p('Audit {{auditeur}}'), p('[[fin]]')],
                                                             [p('Cellule fixe')]]),
                   header_xml=p('Réf. {{reference_dossier}}'))
        tags, clauses = w.word_keys(raw)
        self.assertEqual((sorted(tags), clauses), (['auditeur', 'mon_client', 'reference_dossier'], ['audit_special']))
        out, _ = w.fill(raw, {'audit_special': False}, lambda n: 'V_' + n, lambda n, t: 'Article %d — %s' % (n, t))
        xml = document_xml(out)
        self.assertNotIn('Audit', xml)
        self.assertIn('Cellule fixe', xml)
        self.assertIn('<w:tc><w:p/></w:tc>', xml)                                                    # cellule vidée : reste un document valide
        self.assertIn('V_mon_client', xml)
        self.assertIn('V_reference_dossier', document_xml(out, 'word/header1.xml'))
        crossing = docx(table([[p('[[si a]]'), p('dans la cellule')]]) + p('[[fin]]'))
        self.assertIn('zones différentes', w.word_structure(crossing)[1])
        with self.assertRaises(ValueError):
            w.fill(crossing, {}, str, lambda n, t: t)


class LibraryAudit(t39._Corp):
    """B02 à B06 sur la bibliothèque réelle (base, imports, demandes de rédaction)."""

    def imported(self, raw, name='Mon modèle.docx'):
        from agent import corporate5636 as co
        return co.import_template(self.desk, {'filename': name, 'data': base64.b64encode(raw).decode()})['source']

    def test_b02_ai_uses_the_selected_word_and_keeps_a_snapshot(self):
        from agent import bibliotheque5639 as b
        first = self.imported(docx(p('TRAME_PERSONNELLE_EXCLUSIVEMENT') + p('CLAUSE_SPECIFIQUE_DU_CABINET {{partie_a}}')))
        b.set_word(self.desk, {'modele': 'accord_confidentialite', 'path': first})
        out = b.generate(self.desk, {'modele': 'accord_confidentialite', 'societe': self.cid, 'destination': 'ia', 'partie_a': 'ALPHA SAS'})
        request = self.desk.db.execute('SELECT request FROM docreq520 WHERE id=?', (out['request'],)).fetchone()['request']
        trame = b.trame_for_request(self.desk, request)
        self.assertIn('TRAME_PERSONNELLE_EXCLUSIVEMENT', trame['texte'])
        self.assertIn('CLAUSE_SPECIFIQUE_DU_CABINET ALPHA SAS', trame['texte'])
        self.assertEqual(trame['source'], 'word')
        self.assertNotIn('ACCORD DE CONFIDENTIALITÉ', trame['texte'])
        down = b.generate(self.desk, {'modele': 'accord_confidentialite', 'partie_a': 'ALPHA SAS'})                     # même source au téléchargement
        raw = base64.b64decode(b.download(self.desk, down['goto'].rsplit('fichier=', 1)[1])['download']['base64'])
        self.assertIn('CLAUSE_SPECIFIQUE_DU_CABINET', document_xml(raw))
        second = self.imported(docx(p('AUTRE_MODELE')), 'Autre.docx')                                   # Word remplacé après mise en file
        b.set_word(self.desk, {'modele': 'accord_confidentialite', 'path': second})
        self.assertIn('TRAME_PERSONNELLE_EXCLUSIVEMENT', b.trame_for_request(self.desk, request)['texte'])   # instantané immuable
        (self.co._templates_dir(self.desk) / second.split(':', 1)[1]).unlink()                          # source devenue inaccessible
        with self.assertRaises(Qualified) as ex:
            b.generate(self.desk, {'modele': 'accord_confidentialite'})
        self.assertEqual(str(ex.exception), 'modele_word_illisible')

    def test_b03_word_only_template_offers_its_fields_and_clauses(self):
        from agent import bibliotheque5639 as b
        source = self.imported(docx(p('Client {{mon_client}}') + p('[[si audit_special]]') + p('AUDIT_SPECIAL_FICTIF') + p('[[fin]]')
                                    + p('SUITE_FIXE')))
        out = b.add(self.desk, {'titre': 'Contrat du cabinet', 'categorie': 'contrat', 'droit': 'FR', 'path': source})
        t = b.template(self.desk, out['id'])
        self.assertIn('mon_client', [k for k, _, _ in t['variables']])
        self.assertIn('audit_special', [k for k, _, _, _ in t['clauses']])
        page = self.request('/corporate', query='vue=bibliotheque&modele=' + out['id'])['body']
        self.assertIn('name="mon_client"', page)
        self.assertIn('name="clause_audit_special"', page)
        b.set_labels(self.desk, {'modele': out['id'], 'libelle_mon_client': 'Nom du client', 'defaut_mon_client': 'CLIENT PAR DÉFAUT'})
        self.assertIn(('mon_client', 'Nom du client', 'CLIENT PAR DÉFAUT'), b.template(self.desk, out['id'])['variables'])
        gen = b.generate(self.desk, {'modele': out['id'], 'mon_client': 'ALPHA', 'clause_audit_special': False})
        xml = document_xml(base64.b64decode(b.download(self.desk, gen['goto'].rsplit('fichier=', 1)[1])['download']['base64']))
        self.assertIn('Client ALPHA', xml)
        self.assertNotIn('AUDIT_SPECIAL_FICTIF', xml)
        self.assertIn('SUITE_FIXE', xml)                                                                 # seule la clause disparaît
        typo = self.imported(docx(p('{{denominaton}} et {{mon_client}}')), 'Typo.docx')
        message = b.set_word(self.desk, {'modele': out['id'], 'path': typo})['message']
        self.assertIn('{{denominaton}} → {{denomination}}', message)

    def test_b04_unclosed_word_condition_is_refused_and_never_truncates(self):
        from agent import bibliotheque5639 as b
        good = self.imported(docx(p('PREMIER_MODELE_VALIDE')))
        b.set_word(self.desk, {'modele': 'accord_confidentialite', 'path': good})
        bad = self.imported(docx(p('[[si clause_penale]]') + p('OPTION_FICTIVE') + p('SUITE_OBLIGATOIRE_FICTIVE')), 'Mauvais.docx')
        with self.assertRaises(Qualified) as ex:
            b.set_word(self.desk, {'modele': 'accord_confidentialite', 'path': bad})
        self.assertIn('[[si clause_penale]]', ex.exception.detail['precision'])
        self.assertEqual(b.template(self.desk, 'accord_confidentialite')['word'], good)                # modèle valide conservé
        # Word modifié après l'import : contrôle refait au lancement, aucun fichier écrit
        target = self.co._templates_dir(self.desk) / good.split(':', 1)[1]
        target.write_bytes(docx(p('[[si clause_penale]]') + p('SUITE_OBLIGATOIRE_FICTIVE')))
        before = self.desk.db.execute('SELECT COUNT(*) FROM bibliotheque5639_fichiers').fetchone()[0]
        with self.assertRaises(Qualified):
            b.generate(self.desk, {'modele': 'accord_confidentialite', 'clause_clause_penale': False})
        self.assertEqual(self.desk.db.execute('SELECT COUNT(*) FROM bibliotheque5639_fichiers').fetchone()[0], before)
        for text in ('A\n[[fin]]', 'A\n[[sinon]]', '[[si x]]\nA\n[[sinon]]\nB\n[[sinon]]\n[[fin]]', 'A [[si x]]B'):
            with self.assertRaises(Stop):
                b.customize(self.desk, {'modele': 'cgv_b2b', 'texte': text})

    def test_b05_long_template_is_kept_whole_or_refused_before_drafting(self):
        from agent import bibliotheque5639 as b
        body = '\n'.join('## Clause %d\n%s' % (i, 'Texte contractuel fictif. ' * 40) for i in range(36)) + '\n## Disposition finale\nDISPOSITION_FINALE_FICTIVE'
        self.assertGreater(len(body), 36000)
        out = b.add(self.desk, {'titre': 'Contrat long', 'categorie': 'contrat', 'droit': 'FR', 'texte': body})
        res = b.generate(self.desk, {'modele': out['id'], 'societe': self.cid, 'destination': 'ia'})
        request = self.desk.db.execute('SELECT request FROM docreq520 WHERE id=?', (res['request'],)).fetchone()['request']
        trame = b.trame_for_request(self.desk, request)
        self.assertIn('DISPOSITION_FINALE_FICTIVE', trame['texte'])
        self.assertEqual(len(trame['sections_attendues']), 37)
        self.assertIn('37 section(s) attendue(s)', request)
        missing = b.coverage(self.desk, request, {'paragraphes': [{'texte': 'Article 1 — Clause 0'}]})
        self.assertIn('Disposition finale', missing)
        huge = b.add(self.desk, {'titre': 'Contrat trop long', 'categorie': 'contrat', 'droit': 'FR', 'texte': body * 2})
        count = self.desk.db.execute('SELECT COUNT(*) FROM docreq520').fetchone()[0]
        with self.assertRaises(Qualified) as ex:
            b.generate(self.desk, {'modele': huge['id'], 'societe': self.cid, 'destination': 'ia'})
        self.assertEqual(str(ex.exception), 'trame_trop_longue')
        self.assertEqual(self.desk.db.execute('SELECT COUNT(*) FROM docreq520').fetchone()[0], count)   # rien mis en file

    def test_b05_missing_sections_are_reported_after_drafting(self):
        from agent import bibliotheque5639 as b, docrequest520
        res = b.generate(self.desk, {'modele': 'accord_confidentialite', 'societe': self.cid, 'destination': 'ia'})
        request = self.desk.db.execute('SELECT request FROM docreq520 WHERE id=?', (res['request'],)).fetchone()['request']
        doc = {'paragraphes': [{'style': 'texte', 'texte': 'Objet du projet'}], 'a_completer': []}
        with unittest.mock.patch.object(docrequest520, 'draft', return_value=dict(doc)):
            missing = b.coverage(self.desk, request, doc)
        self.assertIn('Durée', missing)

    def test_b06_downloads_expire_after_24_hours(self):
        from agent import bibliotheque5639 as b
        out = b.generate(self.desk, {'modele': 'cgv_b2b'}, 'avocat-a')
        jeton = out['goto'].rsplit('fichier=', 1)[1]
        created = b._parse_time(self.desk.db.execute('SELECT created FROM bibliotheque5639_fichiers WHERE jeton=?', (jeton,)).fetchone()['created'])
        self.assertTrue(b.download(self.desk, jeton, 'avocat-a', now=created + timedelta(hours=23, minutes=59)))
        with self.assertRaises(Stop):
            b.download(self.desk, jeton, 'avocat-b', now=created + timedelta(hours=1))                   # autre propriétaire
        with self.assertRaises(Stop):
            b.download(self.desk, jeton, 'avocat-a', now=created + timedelta(hours=24))                  # exactement 24 h : refusé
        out = b.generate(self.desk, {'modele': 'cgv_b2b'}, 'avocat-a')
        jeton = out['goto'].rsplit('fichier=', 1)[1]
        old = (datetime.now(timezone.utc) - timedelta(hours=48)).isoformat()
        self.desk.db.execute('UPDATE bibliotheque5639_fichiers SET created=?, expires_at=? WHERE jeton=?', (old, b._expiry(old), jeton))
        self.desk.db.commit()
        with self.assertRaises(Stop) as ex:                                                              # aucune génération entre-temps
            b.download(self.desk, jeton, 'avocat-a')
        self.assertEqual(str(ex.exception), 'document_bibliotheque_expire')
        page = self.request('/corporate', query='vue=bibliotheque&modele=cgv_b2b&fichier=' + jeton)['body']
        self.assertIn('lien expiré', page)
        res = b.generate(self.desk, {'modele': 'accord_confidentialite', 'societe': self.cid, 'destination': 'ia'})
        self.desk.db.execute('UPDATE bibliotheque5639_trames SET created=?', ((datetime.now(timezone.utc) - timedelta(days=90)).isoformat(),))
        self.desk.db.commit()
        b.purge(self.desk)
        self.assertEqual(self.desk.db.execute('SELECT COUNT(*) FROM bibliotheque5639_trames').fetchone()[0], 1)   # référencé par la demande
        self.assertTrue(res['request'])

    def test_p02_catalog_reads_once_without_ddl(self):
        from agent import bibliotheque5639 as b
        b.catalog(self.desk)                                                                             # initialisation
        seen = []
        self.desk.db.set_trace_callback(seen.append)
        try:
            items = b.catalog(self.desk)
        finally:
            self.desk.db.set_trace_callback(None)
        self.assertGreaterEqual(len(items), 47)
        self.assertFalse([s for s in seen if 'CREATE' in s.upper() or 'ALTER' in s.upper()])
        self.assertEqual(len([s for s in seen if s.lstrip().upper().startswith('SELECT')]), 1)
        b.customize(self.desk, {'modele': 'cgv_b2b', 'texte': '## Objet\nTEXTE_MODIFIE'})
        self.assertEqual(next(t for t in b.catalog(self.desk) if t['id'] == 'cgv_b2b')['texte'], ['## Objet', 'TEXTE_MODIFIE'])

    def test_e01_default_law_is_a_firm_setting_not_the_interface_language(self):
        from agent import bibliotheque5639 as b
        self.assertEqual(b.default_law(self.desk), 'FR')
        with unittest.mock.patch('agent.i18n5631.current', return_value='en'):
            page = self.request('/corporate', query='vue=bibliotheque')['body']
            self.assertIn('Statuts de SAS', page)                                                       # interface en anglais : droit français gardé
            self.assertEqual(b.match(self.desk, 'Rédige un NDA pour ce dossier')['droit'], 'FR')
        b.set_default_law(self.desk, {'droit': 'DE'})
        self.assertEqual(b.match(self.desk, 'Rédige un NDA pour ce dossier')['droit'], 'DE')
        page = self.request('/corporate', query='vue=bibliotheque')['body']
        self.assertIn('Gesellschaftsvertrag (Satzung) einer GmbH', page)
        detail = self.request('/corporate', query='vue=bibliotheque&modele=contrat_prestation')['body']
        self.assertIn('Droit applicable : <strong>Droit français</strong>', detail)
        self.assertIn('Langue du document : <strong>français</strong>', detail)

    def test_e02_preview_uses_the_final_engine_and_flags_missing_mentions(self):
        from agent import bibliotheque5639 as b
        out = b.generate(self.desk, {'modele': 'statuts_sas', 'societe': self.cid, 'destination': 'apercu', 'clause_sortie_conjointe': True})
        self.assertTrue(out['noreload'])
        self.assertIn('Droit de sortie conjointe', out['text'])
        self.assertIn('EXEMPLE SAS', out['text'])
        self.assertTrue(any('à compléter' in w for w in out['warnings']))
        off = b.generate(self.desk, {'modele': 'statuts_sas', 'societe': self.cid, 'destination': 'apercu', 'clause_sortie_conjointe': False})
        self.assertNotIn('Droit de sortie conjointe', off['text'])
        self.assertEqual(self.desk.db.execute('SELECT COUNT(*) FROM bibliotheque5639_fichiers').fetchone()[0], 0)   # rien enregistré
        page = self.request('/corporate', query='societe=%s&vue=bibliotheque&modele=statuts_sas' % self.cid)['body']
        for step in ('1. Destination', '2. Informations', '3. Clauses', '4. Vérifier puis préparer'):
            self.assertIn(step, page)
        self.assertIn('data-extra="destination=apercu"', page)
        self.assertIn('<details class="ax-card b5640-advanced">', page)                                  # personnalisation repliée
        js = (t39.ROOT / 'agent' / 'static' / 'v500.js').read_text(encoding='utf-8')
        self.assertIn("!(res && res.noreload)", js)


class BusyPages(unittest.TestCase):
    """E03 : essais automatiques bornés, puis page stable avec « Réessayer » ; jamais de nouvel envoi d'un formulaire."""

    def test_e03_bounded_retries(self):
        from agent import erreurs_pages5639 as ep
        env = {'REQUEST_METHOD': 'GET', 'PATH_INFO': '/agent-courriel/dossiers', 'QUERY_STRING': 'q=1'}
        first = ep.busy(env)
        self.assertIn('content="3;url=/agent-courriel/dossiers?q=1&amp;_occupe=1"', first)
        third = ep.busy({**env, 'QUERY_STRING': 'q=1&_occupe=2'})
        self.assertIn('content="8;url=', third)
        last = ep.busy({**env, 'QUERY_STRING': 'q=1&_occupe=4'})
        self.assertNotIn('http-equiv="refresh"', last)
        self.assertIn('Réessayer', last)
        self.assertIn('_occupe=0', last)
        self.assertIn('référence', last)
        self.assertIn('/agent-courriel/diagnostic', last)
        post = ep.busy({'REQUEST_METHOD': 'POST', 'PATH_INFO': '/agent-courriel/action', 'HTTP_HOST': 'cabinet.example.test',
                        'HTTP_REFERER': 'https://cabinet.example.test/agent-courriel/dossiers'})
        self.assertNotIn('Rien n’a été perdu', post)
        self.assertIn('peuvent devoir être saisies de nouveau', post)
        self.assertIn('content="4;url=/agent-courriel/dossiers"', post)                                 # page du formulaire, en GET


if __name__ == '__main__':
    unittest.main()
