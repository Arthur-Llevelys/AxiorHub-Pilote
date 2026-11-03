"""5.6.89, lot livrables : registre commun, relecture par version, projection, visionneuse, téléchargement, export, propositions et vérification.

Données fictives (DURAND, MARTIN, ZORVAL, KERVILAC, DOS-001, RG 24/01234, example.test), stockage et modèle simulés : aucun réseau, aucun modèle réel, aucun éditeur.
Ce qui exige un vrai navigateur (aperçu réellement affiché, focus, sessionStorage) ou un vrai EuroOffice (réouverture, rendu) n'est pas prouvé ici : ces tests vérifient le serveur.
"""
from datetime import datetime, timedelta, timezone
import hashlib
import io
import json
from pathlib import Path
import sqlite3
import unittest
from unittest.mock import patch
import zipfile

import test_agent
import test_v5684 as t84
from agent import cockpit530, docrequest520, file5651, file5657, livrables5689 as liv, livrables_apercu5689 as apercu, livrables_export5689 as export, livrables_sources5689 as sources, livrables_web5689 as web
from agent import export5688, headless5684, membres5650, modeles5684, propositions5689 as patches, reponses5671, verification5689 as verification, web5689
from agent.common import Stop, digest
from agent.desk import Desk

MID = 'DOS-001'
DOCX = 'application/vnd.openxmlformats-officedocument.wordprocessingml.document'
W = 'http://schemas.openxmlformats.org/wordprocessingml/2006/main'
R = 'http://schemas.openxmlformats.org/officeDocument/2006/relationships'
AVOCAT, ASSISTANT = ('avocate@example.test', 'avocat'), ('assistant@example.test', 'assistant')
PNG = b'\x89PNG\r\n\x1a\n' + b'\x00' * 32


def texte_docx(*paragraphes):
    """Word minimal : un paragraphe, un run, un texte."""
    body = ''.join('<w:p><w:r><w:t xml:space="preserve">%s</w:t></w:r></w:p>' % p for p in paragraphes)
    return _archive('<w:document xmlns:w="%s"><w:body>%s<w:sectPr/></w:body></w:document>' % (W, body))


def _archive(document, extra=(), rels=''):
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, 'w', zipfile.ZIP_DEFLATED) as z:
        z.writestr('[Content_Types].xml', '<Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types"><Default Extension="xml" ContentType="application/xml"/><Default Extension="rels" ContentType="application/vnd.openxmlformats-package.relationships+xml"/><Default Extension="png" ContentType="image/png"/></Types>')
        z.writestr('_rels/.rels', '<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships"><Relationship Id="rId1" Type="%s/officeDocument" Target="word/document.xml"/></Relationships>' % R)
        z.writestr('word/document.xml', document)
        if rels:
            z.writestr('word/_rels/document.xml.rels', '<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">%s</Relationships>' % rels)
        for name, content in extra:
            z.writestr(name, content)
    return buf.getvalue()


def docx_complexe():
    """Modèle fictif : tableau, image, en-tête, pied, numérotation, style et renvoi REF vers un signet."""
    body = ('<w:p><w:pPr><w:pStyle w:val="Titre1"/></w:pPr><w:bookmarkStart w:id="1" w:name="art3"/><w:r><w:t>Article 3 Prix</w:t></w:r><w:bookmarkEnd w:id="1"/></w:p>'
            '<w:p><w:pPr><w:numPr><w:ilvl w:val="0"/><w:numId w:val="1"/></w:numPr></w:pPr><w:r><w:t>Le prix est fixé par les parties ZORVAL et KERVILAC.</w:t></w:r></w:p>'
            '<w:p><w:r><w:t>Voir l’article 3 ci-dessus, </w:t></w:r><w:r><w:fldChar w:fldCharType="begin"/></w:r><w:r><w:instrText> REF art3 </w:instrText></w:r><w:r><w:fldChar w:fldCharType="end"/></w:r></w:p>'
            '<w:tbl><w:tr><w:tc><w:p><w:r><w:t>Cellule fictive</w:t></w:r></w:p></w:tc></w:tr></w:tbl>'
            '<w:p><w:r><w:drawing><a:blip xmlns:a="http://schemas.openxmlformats.org/drawingml/2006/main" xmlns:r="%s" r:embed="rIdImage"/></w:drawing></w:r></w:p>'
            '<w:sectPr><w:headerReference xmlns:r="%s" w:type="default" r:id="rIdEntete"/></w:sectPr>') % (R, R)
    rels = ('<Relationship Id="rIdImage" Type="%s/image" Target="media/logo.png"/><Relationship Id="rIdEntete" Type="%s/header" Target="header1.xml"/>'
            '<Relationship Id="rIdNum" Type="%s/numbering" Target="numbering.xml"/><Relationship Id="rIdStyles" Type="%s/styles" Target="styles.xml"/>') % (R, R, R, R)
    extra = (('word/media/logo.png', PNG), ('word/header1.xml', '<w:hdr xmlns:w="%s"><w:p><w:r><w:t>En-tête du cabinet fictif</w:t></w:r></w:p></w:hdr>' % W),
             ('word/numbering.xml', '<w:numbering xmlns:w="%s"><w:abstractNum w:abstractNumId="0"/><w:num w:numId="1"><w:abstractNumId w:val="0"/></w:num></w:numbering>' % W),
             ('word/styles.xml', '<w:styles xmlns:w="%s"><w:style w:type="paragraph" w:styleId="Titre1"><w:name w:val="Titre 1"/></w:style></w:styles>' % W))
    return _archive('<w:document xmlns:w="%s"><w:body>%s</w:body></w:document>' % (W, body), extra, rels)


class Stockage:
    """Nextcloud simulé : ETag, identifiant de fichier, création exclusive, remplacement sous ETag. Compte chaque écriture."""

    def __init__(self):
        self.files, self.etags, self.ids, self.counter, self.ecritures, self.lectures = {}, {}, {}, 0, [], 0
        self.panne = None

    def add(self, path, data):
        self.counter += 1
        self.files[path] = data
        self.etags[path] = '"e%d"' % self.counter
        self.ids.setdefault(path, str(1000 + self.counter))

    def renommer(self, old, new):
        self.files[new], self.etags[new], self.ids[new] = self.files.pop(old), self.etags.pop(old), self.ids.pop(old)

    def stat(self, path):
        if self.panne:
            raise Stop(self.panne)
        if path not in self.files:
            raise Stop('fichier_nextcloud_introuvable')
        return {'path': path, 'etag': self.etags[path], 'size': len(self.files[path]), 'modified': '', 'fileid': self.ids[path]}

    def download(self, item):
        self.lectures += 1
        if item['path'] not in self.files:
            raise Stop('http_404')
        return self.files[item['path']]

    def list_folder(self, path):
        return [{'path': p, 'directory': False, 'etag': self.etags[p], 'size': len(b)} for p, b in self.files.items() if p.rsplit('/', 1)[0] == path]

    def ensure_folder(self, path, boundary):
        return path

    def put_file(self, path, data, kind=''):
        if path in self.files:
            raise Stop('http_412')
        self.add(path, data)
        self.ecritures.append(('creation', path))
        return path

    def replace_file(self, path, data, etag):
        if self.etags.get(path) != etag:
            raise Stop('version_nextcloud_modifiee')
        self.add(path, data)
        self.ecritures.append(('remplacement', path))
        return path

    def file_web_url(self, path):
        return ''


class Base(unittest.TestCase):
    def setUp(self):
        self.f = test_agent.EngineTests('test_observation_has_no_mail_write')
        self.f.setUp()
        self.addCleanup(self.f.doCleanups)
        self.desk = Desk(self.f.c)
        self.addCleanup(self.desk.db.close)
        self.nc = Stockage()
        for target in ('agent.office440.dav_client',):
            p = patch(target, return_value=self.nc)
            p.start()
            self.addCleanup(p.stop)
        self.desk.setting('cockpit530:drafts', {'at': 9e18, 'ok': True, 'validity': '5', 'items': []})
        cockpit530.ensure_schema(self.desk)
        docrequest520.ensure_schema(self.desk)
        self.caps = {'editeur': True, 'convertisseur': True}
        p = patch.object(apercu, 'capacites', side_effect=lambda desk: dict(self.caps))
        p.start()
        self.addCleanup(p.stop)

    # producteurs simulés au plus près de leur journal
    def demande(self, ident='r1', path='/Dossiers/DEMO/PROJETS/Contrat DURAND.docx', raw=None, kind='contrat', when=None, **result):
        raw = texte_docx('Contrat entre DURAND et MARTIN.') if raw is None else raw
        if path not in self.nc.files or self.nc.files[path] != raw:
            self.nc.add(path, raw)
        sha = hashlib.sha256(raw).hexdigest()
        now = when or datetime.now(timezone.utc).isoformat()
        payload = {'title': 'Contrat DURAND', 'sha256': sha, 'readback_sha256': sha, **result}
        old = self.desk.db.execute('SELECT 1 FROM docreq520 WHERE id=?', (ident,)).fetchone()
        if old:
            self.desk.db.execute("UPDATE docreq520 SET result=?, updated=? WHERE id=?", (json.dumps(payload), now, ident))
        else:
            self.desk.db.execute("INSERT INTO docreq520(id,matter,request,kind,status,path,result,created,updated) VALUES(?,?,?,?,?,?,?,?,?)", (ident, MID, 'Préparer un contrat fictif', kind, 'cree', path, json.dumps(payload), now, now))
        self.desk.db.commit()
        liv.synchroniser(self.desk)
        return liv.charger(self.desk, self.doc_id(path))

    def production(self, ident, updated, verification_json):
        from agent import production420
        production420.ensure_schema(self.desk)
        self.desk.db.execute('INSERT INTO production_deliverables_v420 VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)', (ident, 1, 'create_document_files', 'job', 's', MID, 'document', 'Projet', 'verified', 'deposited', '[]', '', '[]', '/Dossiers/DEMO', verification_json, 'ok', '', 0, 0, updated, updated))
        self.desk.db.commit()

    def doc_id(self, path):
        return self.desk.db.execute("SELECT document_id FROM prepared_items5689 WHERE path=? ORDER BY version_number DESC LIMIT 1", (path,)).fetchone()[0]

    def items(self):
        return cockpit530.review_items(self.desk, True)

    def api(self, name, args=None, body=None, who=AVOCAT):
        """Passage par ``web5689`` comme une vraie requête : identité dans l'en-tête authentifié, CSRF, corps JSON."""
        raw = json.dumps(body or {}).encode()
        env = {'HTTP_X_AXIORHUB_USER': who[0], 'HTTP_X_AXIORHUB_ROLE': who[1], 'HTTP_ORIGIN': 'https://cabinet.example.test', 'HTTP_X_CSRF_TOKEN': 'jeton', 'CONTENT_LENGTH': str(len(raw)),
               'CONTENT_TYPE': 'application/json', 'wsgi.input': io.BytesIO(raw), 'REQUEST_METHOD': 'POST' if body is not None else 'GET'}
        auth = {'origin': 'https://cabinet.example.test', 'csrf': 'jeton'}
        return web5689.api(env, self.desk, auth, '/p', 'm5689/' + name, args or {}, 'POST' if body is not None else 'GET')

    def js(self, out):
        return json.loads(out['body'])


class B01(Base):
    def _lignes(self):
        now = datetime.now(timezone.utc)
        for ident, delta, raw in (('avant', 0, '{"title":"Conclusions fictives"}'), ('abime', 1, '{json invalide'), ('apres', 2, '{"title":"Note fictive"}')):
            self.desk.db.execute('INSERT INTO docreq520(id,matter,request,kind,status,path,result,created,updated) VALUES(?,?,?,?,?,?,?,?,?)',
                                 (ident, MID, 'CONTENU CONFIDENTIEL DE LA DEMANDE', 'conclusions', 'cree', '/Dossiers/DEMO/%s.docx' % ident, raw, now.isoformat(), (now + timedelta(seconds=delta)).isoformat()))
        self.desk.db.commit()

    def test_B01_une_ligne_abimee_n_emporte_pas_les_autres(self):
        self._lignes()
        titres = {x['title'] for x in self.items()}
        self.assertEqual(titres, {'Conclusions fictives', 'Note fictive'})

    def test_B01_etat_partiel_de_la_source_et_journal_sans_contenu(self):
        self._lignes()
        self.items()
        state = next(s for s in liv.etat_sources(self.desk) if s['id'] == 'docrequest520')
        self.assertEqual(state['state'], 'partial')
        self.assertIn('1 ligne', state['message'])
        logs = [r['data'] for r in self.desk.db.execute("SELECT data FROM audit WHERE action='livrables5689_ligne_illisible'")] if self._audit_table() else []
        self.assertTrue(logs)
        self.assertIn('abime', logs[0])
        self.assertNotIn('CONFIDENTIEL', ' '.join(logs))
        self.assertNotIn('json invalide', ' '.join(logs))

    def _audit_table(self):
        return self.desk.db.execute("SELECT 1 FROM sqlite_master WHERE name='audit'").fetchone()

    def test_B01_une_source_en_panne_ne_vide_pas_les_autres_categories(self):
        self.demande()

        def panne(desk):
            raise sqlite3.DatabaseError('disque')
        src = dict(next(s for s in sources.SOURCES if s['id'] == 'corporate5636'), signature=lambda desk: 'neuf', rows=panne)
        with patch.object(sources, 'SOURCES', tuple(src if s['id'] == 'corporate5636' else s for s in sources.SOURCES)):
            self.assertEqual(len(self.items()), 1)
            state = next(s for s in liv.etat_sources(self.desk) if s['id'] == 'corporate5636')
        self.assertEqual(state['state'], 'unavailable')

    def test_B01_retirer_la_ligne_abimee_ramene_un_etat_complet(self):
        self._lignes()
        self.items()
        self.desk.db.execute("DELETE FROM docreq520 WHERE id='abime'")
        self.desk.db.commit()
        self.items()
        self.assertEqual(next(s for s in liv.etat_sources(self.desk) if s['id'] == 'docrequest520')['state'], 'fresh')


class B02(Base):

    def test_B02_la_version_b_revient_a_relire_apres_relecture_de_a(self):
        """Reproduction de l'audit, à l'identique : marqueur de chemin posé, puis nouvelle empreinte au même chemin."""
        path, now = '/Dossiers/DEMO/PROJETS/Contrat.docx', datetime.now(timezone.utc)
        files = lambda sha: json.dumps({'files': [{'path': path, 'sha256': sha}]})
        self.production('PROD', now.isoformat(), files('a' * 64))
        self.desk.db.commit()
        self.assertEqual(len(self.items()), 1)
        self.desk.db.execute('INSERT INTO cockpit530_reviewed VALUES(?,?,?,?)', ('doc:' + digest(path)[:24], 'relu', 'Contrat fictif', now.isoformat()))
        self.desk.db.commit()
        self.assertEqual(self.items(), [])          # la version A relue quitte la liste
        self.desk.db.execute('UPDATE production_deliverables_v420 SET updated=?, verification=? WHERE id=?', ((now + timedelta(minutes=1)).isoformat(), files('b' * 64), 'PROD'))
        self.desk.db.commit()
        after = self.items()
        self.assertEqual(len(after), 1)
        self.assertEqual(after[0]['version_id'], 'sha256:' + 'b' * 64)

    def test_B02_relecture_portee_par_cockpit_sur_la_version_affichee(self):
        row = self.demande()
        item = self.items()[0]
        cockpit530.review(self.desk, {'item': item['id'], 'decision': 'relu'})
        self.assertEqual(self.items(), [])
        review = self.desk.db.execute('SELECT * FROM prepared_reviews5689').fetchone()
        self.assertEqual((review['document_id'], review['version_id'], review['decision']), (row['document_id'], row['version_id'], 'relu'))
        self.demande(raw=texte_docx('Contrat modifié entre DURAND et MARTIN.'))
        self.assertEqual(len(self.items()), 1)

    def test_B02_un_renommage_ne_duplique_pas_et_garde_la_relecture(self):
        row = self.demande()
        liv.relire(self.desk, row['document_id'], row['version_id'], 'relu', *AVOCAT)
        self.nc.renommer(row['path'], '/Dossiers/DEMO/PROJETS/Contrat renommé.docx')
        out = liv.synchroniser_fichiers(self.desk, self.nc)
        self.assertEqual(out['nouvelles_versions'], 0)
        rows = self.desk.db.execute('SELECT * FROM prepared_items5689 WHERE document_id=?', (row['document_id'],)).fetchall()
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]['path'], '/Dossiers/DEMO/PROJETS/Contrat renommé.docx')
        self.assertEqual(rows[0]['review_state'], 'reviewed')

    def test_B02_changement_de_contenu_constate_par_la_synchronisation(self):
        row = self.demande()
        liv.relire(self.desk, row['document_id'], row['version_id'], 'relu', *AVOCAT)
        self.assertEqual(liv.synchroniser_fichiers(self.desk, self.nc)['nouvelles_versions'], 0)
        self.nc.add(row['path'], texte_docx('Édition humaine du contrat.'))
        self.assertEqual(liv.synchroniser_fichiers(self.desk, self.nc)['nouvelles_versions'], 1)
        current = liv.charger(self.desk, row['document_id'])
        self.assertEqual((current['version_number'], current['review_state']), (2, 'to_review'))
        old = liv.charger(self.desk, row['document_id'], version_id=row['version_id'])
        self.assertEqual((old['lifecycle'], old['review_state']), ('replaced', 'reviewed'))      # la décision reste sur sa version

    def test_B02_ancien_marqueur_sans_version_connue_n_approuve_rien(self):
        self.nc.add('/Dossiers/DEMO/Ancien.docx', texte_docx('x'))
        now = datetime.now(timezone.utc).isoformat()
        self.desk.db.execute("INSERT INTO docreq520(id,matter,request,kind,status,path,result,created,updated) VALUES('r9',?,?,?,?,?,?,?,?)", (MID, 'Ancien', 'note', 'cree', '/Dossiers/DEMO/Ancien.docx', '{}', now, now))
        self.desk.db.execute('INSERT INTO cockpit530_reviewed VALUES(?,?,?,?)', ('doc:' + digest('/Dossiers/DEMO/Ancien.docx')[:24], 'valide', 'Ancien', now))
        self.desk.db.commit()
        self.assertEqual(len(self.items()), 1)        # l'empreinte n'est pas connue : aucune version n'est tenue pour relue
        mark = self.desk.db.execute('SELECT state FROM prepared_legacy_marks5689').fetchone()
        self.assertEqual(mark['state'], 'version_inconnue')
        self.assertFalse(self.desk.db.execute('SELECT 1 FROM prepared_reviews5689').fetchone())

    def test_B02_ancien_marqueur_ne_vaut_pas_pour_une_version_posterieure(self):
        now = datetime.now(timezone.utc)
        mark = (now - timedelta(days=2)).isoformat()
        self.desk.db.execute('INSERT INTO cockpit530_reviewed VALUES(?,?,?,?)', ('doc:' + digest('/Dossiers/DEMO/PROJETS/Contrat DURAND.docx')[:24], 'relu', 'x', mark))
        self.desk.db.commit()
        self.demande(when=now.isoformat())              # seule version connue : postérieure au marqueur
        self.assertEqual(len(self.items()), 1)
        self.assertFalse(self.desk.db.execute('SELECT 1 FROM prepared_reviews5689').fetchone())

    def test_B02_la_migration_est_repetable_et_ne_cree_aucun_fichier(self):
        self.demande()
        before = (dict(self.nc.files), len(self.nc.ecritures), self.nc.lectures)
        counts = []
        for _ in range(3):
            liv.migrer(self.desk)
            counts.append(self.desk.db.execute('SELECT COUNT(*) FROM prepared_items5689').fetchone()[0])
        self.assertEqual(set(counts), {1})
        self.assertEqual((dict(self.nc.files), len(self.nc.ecritures), self.nc.lectures), before)

    def test_B02_relecture_refusee_sur_une_version_perimee(self):
        row = self.demande()
        self.demande(raw=texte_docx('Autre version.'))
        with self.assertRaisesRegex(Stop, 'version_perimee'):
            liv.relire(self.desk, row['document_id'], row['version_id'], 'relu', *AVOCAT)

    def test_B02_lecture_personnelle_distincte_de_la_decision_du_cabinet(self):
        row = self.demande()
        out = liv.relire(self.desk, row['document_id'], row['version_id'], 'relu', *ASSISTANT)
        self.assertEqual(out['review_state'], 'to_review')       # l'assistant(e) n'a pas le droit « Valider »
        self.assertEqual(self.desk.db.execute('SELECT scope FROM prepared_reviews5689').fetchone()[0], 'personnel')
        self.assertEqual(len(self.items()), 1)
        self.assertEqual(liv.relire(self.desk, row['document_id'], row['version_id'], 'relu', *AVOCAT)['review_state'], 'reviewed')

    def test_B02_l_ancien_booleen_verified_ne_reprend_que_le_stockage(self):
        path = '/Dossiers/DEMO/PROJETS/Ancien.docx'
        self.production('P2', datetime.now(timezone.utc).isoformat(), json.dumps({'verified': True, 'files': [{'path': path, 'sha256': 'c' * 64}]}))
        self.desk.db.commit()
        liv.migrer(self.desk)
        row = liv.charger(self.desk, self.doc_id(path))
        check = verification.vue(self.desk, row)
        states = {c['check_id']: c['state'] for c in check['checks']}
        self.assertEqual(states['storage.file'], 'passed')
        self.assertTrue(next(c for c in check['checks'] if c['check_id'] == 'storage.file')['evidence_ref'].startswith('ancien:'))
        self.assertEqual(states['human.review'], 'pending')
        self.assertEqual(states['format.archive'], 'pending')
        self.assertEqual(row['review_state'], 'to_review')
        self.assertFalse(check['ready_for_review'])


class B03(t84.Base):
    def setUp(self):
        super().setUp()
        self.desk.setting('cockpit530:drafts', {'at': 9e18, 'ok': True, 'validity': '5', 'items': []})

    def test_B03_acte_de_cession_visible_avec_lien_et_reserve(self):
        out = t84.ces.prepare_document(self.desk, {'id': self.op(), 'reference': 'acte_cession_fonds'}, 'avocate', self.docs)
        item = next(x for x in cockpit530.review_items(self.desk, True) if x.get('path') == out['path'])
        self.assertTrue(item['verified'])                                 # relu dans le stockage par ``constater``
        self.assertEqual(item['reserve'], out['reserve'])
        self.assertTrue(item['reserve'])                                  # modèle non validé dans ce parcours
        card = next(c for c in file5657.review_items(self.desk, '/p') if c['item'] == item['id'])
        self.assertTrue(card['href'].startswith('/p/livrables/' + item['document_id']))
        self.assertIn('Réserves', card['consequence'])
        view = apercu.vue(self.desk, liv.charger(self.desk, item['document_id']), 'avocate', 'administrateur', {'editeur': False, 'convertisseur': False})
        self.assertTrue(view['actions']['download'])

    def test_B03_reprise_trouve_le_document_sans_le_recreer(self):
        did = self.op()
        calls = []
        real = self.docs.put_file

        def coupe(path, data, kind=''):
            real(path, data, kind)
            calls.append(path)
            raise RuntimeError('arrêt entre le dépôt et le journal')
        with patch.object(self.docs, 'put_file', side_effect=coupe), self.assertRaises(RuntimeError):
            t84.ces.prepare_document(self.desk, {'id': did, 'reference': 'acte_cession_fonds'}, 'avocate', self.docs)
        self.assertEqual(len(calls), 1)
        out = t84.ces.prepare_document(self.desk, {'id': did, 'reference': 'acte_cession_fonds'}, 'avocate', self.docs)
        self.assertEqual(out['path'], calls[0])                           # même sortie, aucun second fichier
        self.assertEqual([p for p in self.docs.files if 'v2' in p], [])
        self.assertEqual(self.desk.db.execute("SELECT production_state FROM prepared_items5689 WHERE path=?", (out['path'],)).fetchone()[0], 'verified')

    def test_B03_journal_seul_reconcilie_sans_recreer(self):
        """Ligne deliverables5683 d'avant la mise à jour : reprise par l'adaptateur, sans lecture ni écriture de stockage, et rien n'est certifié."""
        from agent import operations5683 as ops
        did = self.op()
        path = '/Dossiers/DEMO/JURIDIQUE/Acte ancien.docx'
        self.desk.db.execute('INSERT INTO deliverables5683 VALUES(?,?,?,?,?,?,?,?,?)', ('x1', did, 'acte_ancien', 1, path, '', json.dumps({'reserve': True}), 'projet', ops._now()))
        self.desk.db.commit()
        before = dict(self.docs.files)
        liv.migrer(self.desk)
        liv.migrer(self.desk)
        rows = self.desk.db.execute('SELECT * FROM prepared_items5689 WHERE path=?', (path,)).fetchall()
        self.assertEqual(len(rows), 1)
        self.assertEqual((rows[0]['production_state'], rows[0]['review_state'], rows[0]['reserve']), ('published', 'to_review', 1))
        self.assertEqual(dict(self.docs.files), before)

    def test_LIV05_acte_marque_signe_devient_un_original_protege(self):
        out = t84.ces.prepare_document(self.desk, {'id': self.op(), 'reference': 'acte_cession_fonds'}, 'avocate', self.docs)
        self.desk.db.execute("UPDATE deliverables5683 SET state='signe' WHERE path=?", (out['path'],))
        self.desk.db.commit()
        liv.synchroniser(self.desk)
        row = self.desk.db.execute('SELECT * FROM prepared_items5689 WHERE path=?', (out['path'],)).fetchone()
        self.assertEqual(row['protected'], 'signe')
        view = apercu.vue(self.desk, liv.charger(self.desk, row['document_id']), 'avocate', 'administrateur', {'editeur': True, 'convertisseur': False})
        self.assertFalse(view['actions']['ai_edit'])
        self.assertFalse(view['actions']['editor'])

    def test_B03_circuit_du_travailleur_toujours_visible(self):
        from agent import production420
        self.desk.db.commit()
        production420.ensure_schema(self.desk)
        path = '/Dossiers/DEMO/PROJETS/Conclusions_projet.docx'
        self.docs.files[path] = texte_docx('Conclusions fictives.')
        production420.record_job(self.desk, {'id': 98765, 'kind': 'prepare_document_project'}, {'matter': MID}, 'done', {'projet_prepare': True, 'created_files': [{'path': path, 'sha256': 'd' * 64, 'bytes': 10}]})
        self.assertIn(path, [x.get('path') for x in cockpit530.review_items(self.desk, True)])

    def test_UX01_formulaire_cession_et_bibliotheque_au_registre(self):
        did = self.op()
        form = t84.ces.prepare_document(self.desk, {'id': did, 'reference': 'acte_cession_fonds'}, 'avocate', self.docs)
        t = t84.lib.template(self.desk, next(iter(t84.lib.BUILTIN)))
        out = t84.lib.generate(self.desk, {'modele': t['id'], 'destination': 'dossier', 'matter': t84.MID}, 'avocate', self.docs)
        paths = {r['path'] for r in self.desk.db.execute("SELECT path FROM prepared_items5689 WHERE lifecycle='current'")}
        self.assertTrue({form['path'], out['path']} <= paths)
        row = self.desk.db.execute('SELECT * FROM prepared_items5689 WHERE path=?', (out['path'],)).fetchone()
        self.assertEqual((row['source_service'], row['production_state']), ('bibliotheque5639', 'verified'))
        self.assertTrue(row['reserve'])          # modèle non validé : réserve visible

    def test_LIV05_modele_retire_refuse_a_la_generation(self):
        ident = next(iter(t84.lib.BUILTIN))
        modeles5684.current(self.desk, ident)
        modeles5684.move(self.desk, {'modele': ident, 'etat': 'withdrawn'}, 'avocate', 'avocat')
        with self.assertRaisesRegex(Stop, 'modele_retire'):
            t84.lib.generate(self.desk, {'modele': ident, 'destination': 'dossier', 'matter': t84.MID}, 'avocate', self.docs)
        self.assertEqual(self.docs.files.keys() & {p for p in self.docs.files if 'JURIDIQUE' in p}, set())


class Producteurs(Base):
    """UX01 : chaque producteur, chaque nature, avec sa version et un lien ouvrable."""

    def ligne(self, table, **values):
        cols = ','.join(values)
        self.desk.db.execute('INSERT INTO %s(%s) VALUES(%s)' % (table, cols, ','.join('?' for _ in values)), list(values.values()))
        self.desk.db.commit()

    def test_UX01_chaque_nature_apparait_avec_version_et_lien(self):
        now = datetime.now(timezone.utc).isoformat()
        self.demande('c1', '/Dossiers/DEMO/PROCEDURE/Conclusions.docx', kind='conclusions')
        self.demande('c2', '/Dossiers/DEMO/CORRESPONDANCES/Courrier.docx', texte_docx('Courrier fictif.'), kind='courrier')
        self.demande('c3', '/Dossiers/DEMO/LIVRABLES/Note.docx', texte_docx('Note fictive.'), kind='note')
        from agent import document_projects
        document_projects.ensure_schema(self.desk)
        self.ligne('document_projects_v220', id='p' * 32, matter=MID, document_type='cgv', status='done', instruction='CGV', data='{}', preview_hash='', challenge_hash='', created=now, expires=now, decided=now,
                   result=json.dumps({'created_files': [{'path': '/Dossiers/DEMO/PROJETS/Conditions generales.docx', 'sha256': 'e' * 64, 'bytes': 5}, {'path': '/Dossiers/DEMO/PROJETS/Rapport_sources.pdf', 'sha256': 'f' * 64, 'bytes': 5}]}))
        from agent import facturation5653 as fact
        fact.ensure_schema(self.desk) if hasattr(fact, 'ensure_schema') else None
        self.ligne('b53_documents', id='f1', kind='convention', matter=MID, state='brouillon', snapshot_json='{}', created_by='a', created=now, updated=now, files_json=json.dumps({'docx': {'chemin': '/Dossiers/DEMO/FACTURATION/Convention.docx', 'sha256': 'a' * 64, 'verifie_le': now}}))
        data = liv.projection(self.desk, prefix='/p')
        kinds = {c['kind'] for c in data['items']}
        self.assertTrue({'conclusions', 'courrier', 'note', 'conditions_generales', 'convention'} <= kinds, kinds)
        self.assertTrue(all(c['href'].startswith('/p/livrables/') and c['version'] == 1 for c in data['items']))
        cgv = next(c for c in data['items'] if c['kind'] == 'conditions_generales')
        self.assertEqual((cgv['exports'], cgv['formats']), (1, ['docx', 'pdf']))       # DOCX et rapport du même projet : une carte

    def test_UX01_word_corporate_missions_et_courrier_chacun_au_registre(self):
        from agent import corporate5636, courrier5679, taches5614, word_legal
        now = datetime.now(timezone.utc).isoformat()
        word_legal.ensure_schema(self.desk)
        self.ligne('word_projects_v250', id='w' * 32, matter=MID, status='done', instruction='Réviser', data='{}', preview_hash='', challenge_hash='', created=now, expires=now, decided=now,
                   result=json.dumps({'created_files': [{'path': '/Dossiers/DEMO/Conclusions_propre.docx', 'sha256': '1' * 64, 'bytes': 9}, {'path': '/Dossiers/DEMO/Conclusions_comparee.docx', 'sha256': '2' * 64, 'bytes': 9}, {'path': '/Dossiers/DEMO/Rapport.pdf', 'sha256': '3' * 64, 'bytes': 9}]}))
        corporate5636.schema(self.desk) if hasattr(corporate5636, 'schema') else None
        self.ligne('operations5636', id='op1', company='c1', kind='cession', data='{}', docs='[]', state='fait', result=json.dumps({'files': ['/Dossiers/DEMO/JURIDIQUE/PV de décision - ZORVAL.docx']}), created=now, updated=now)
        self.desk.db.executescript(courrier5679.SCHEMA)
        self.ligne('courriers5679', id='co1', matter=MID, owner='a', state='pret', data='{}', path='/Dossiers/DEMO/CORRESPONDANCES/Courrier.docx', created=now, updated=now)
        self.desk.db.executescript(taches5614.SCHEMA)
        values = {'id': 'm1', 'owner': 'a', 'request_key': 'k', 'matter': MID, 'title': 'Mission', 'objective': 'o', 'parcours': 'p', 'state': 'fini'}
        for r in self.desk.db.execute('PRAGMA table_info(missions5614)').fetchall():
            if r[3] and r[4] is None and r[1] not in values:
                values[r[1]] = 0 if 'INT' in r[2].upper() else now
        self.ligne('missions5614', **values)
        self.ligne('ops5614', id='o1', mission='m1', task='t', kind='depot', state='confirme', payload_hash='h', remote_ref='/Dossiers/DEMO/LIVRABLES/Mission.docx', detail=json.dumps({'sha256': '4' * 64, 'verified_at': now}), created=now, updated=now)
        data = liv.projection(self.desk)
        by = {c['source']: c for c in data['items']}
        self.assertEqual(set(by), {'word_legal', 'corporate5636', 'courrier5679', 'missions5614'}, by.keys())
        self.assertEqual(by['word_legal']['exports'], 2)                 # version propre, comparaison et rapport : une carte
        self.assertTrue(by['missions5614']['verified'])
        self.assertEqual(by['courrier5679']['kind'], 'courrier')
        self.assertEqual(by['corporate5636']['matter_id'], MID)
        self.assertFalse(by['corporate5636']['verified'])                # journal sans relecture : rien n'est certifié
        self.assertEqual(data['counts']['total'], 4)

    def test_UX01_formulaire_depose_verifie_et_a_relire(self):
        from agent import formulaires5683 as forms
        path = forms._depot(self.desk, self.nc, {'titre': 'Déclaration fictive'}, 'f1', MID, 'avocate', '/Dossiers/DEMO/FORMULAIRES/Declaration.pdf', b'%PDF-1.4 x', 'application/pdf')
        row = liv.charger(self.desk, self.doc_id(path))
        self.assertEqual((row['kind'], row['production_state'], row['review_state'], row['source_service']), ('formulaire', 'verified', 'to_review', 'formulaires5683'))
        self.assertTrue(row['reserve'])

    def test_UX01_note_rangee_reste_suivie_apres_depart_du_brouillon(self):
        from agent import notes5666
        notes5666.schema(self.desk)
        now = datetime.now(timezone.utc).isoformat()
        path = '/Dossiers/DEMO/2026-11-02 - Note interne - Renvoi.docx'
        self.ligne('rangement5689', compte='c', dossier_imap='Drafts', uidvalidity='5', uid='1', item='mail:5:1', matter=MID, destination=path, emplacement='dossier', empreinte='a' * 64, etape='termine', created=now, updated=now)
        self.ligne('cockpit530_reviewed', item='mail:5:1', decision='range', title='Renvoi — ' + path, at=now)
        data = liv.projection(self.desk)
        note = next(c for c in data['items'] if c['kind'] == 'note')
        self.assertTrue(note['verified'])
        row = liv.charger(self.desk, note['document_id'])
        self.assertEqual(json.loads(row['provenance'])['brouillon_source'], 'mail:5:1')

    def test_UX01_projet_non_cree_n_est_jamais_presente_comme_pret(self):
        from agent import document_projects
        document_projects.ensure_schema(self.desk)
        now = datetime.now(timezone.utc)
        self.ligne('document_projects_v220', id='a' * 32, matter=MID, document_type='contrat', status='pending', instruction='Contrat', data='{"title":"Contrat ZORVAL"}', preview_hash='', challenge_hash='', created=now.isoformat(), expires=(now + timedelta(days=1)).isoformat(), decided='', result='')
        data = liv.projection(self.desk)
        card = data['items'][0]
        self.assertEqual(card['state'], 'preparing')
        self.assertFalse(card['verified'])
        self.assertEqual(self.items(), [])
        src = next(s for s in data['sources'] if s['id'] == 'document_projects')
        self.assertEqual(src['state'], 'running')
        row = liv.charger(self.desk, card['document_id'])
        view = apercu.vue(self.desk, row, *AVOCAT)
        self.assertFalse(view['actions']['download'])
        self.assertEqual(view['allowed_formats'], [])
        self.assertTrue(any('Aucun fichier vérifié' in r for r in view['unavailable_reasons']))

    def test_UX01_la_meme_sortie_vue_par_deux_journaux_fait_une_carte(self):
        raw = texte_docx('Contrat.')
        row = self.demande(raw=raw)
        self.production('D', datetime.now(timezone.utc).isoformat(), json.dumps({'files': [{'path': row['path'], 'sha256': row['sha256']}]}))
        self.desk.db.commit()
        data = liv.projection(self.desk)
        self.assertEqual(data['counts']['total'], 1)
        links = self.desk.db.execute("SELECT COUNT(*) FROM prepared_source_links5689 WHERE link_kind='producteur'").fetchone()[0]
        self.assertEqual(links, 2)

    def test_UX01_les_trois_pages_lisent_la_meme_version(self):
        row = self.demande()
        projection = next(c for c in liv.projection(self.desk)['items'] if c['document_id'] == row['document_id'])
        today = next(x for x in self.items() if x['document_id'] == row['document_id'])
        dossier = next(c for c in liv.projection(self.desk, matter=MID)['items'] if c['document_id'] == row['document_id'])
        self.assertEqual({projection['version_id'], today['version_id'], dossier['version_id']}, {row['version_id']})

    def test_UX01_page_produire_et_dossier_affichent_la_section(self):
        row = self.demande()
        html = web.section_html(self.desk, '/p', MID)
        self.assertIn('/p/livrables/' + row['document_id'], html)
        self.assertIn('Contrat DURAND', html)
        self.assertNotIn('/Dossiers/DEMO', html)      # jamais de chemin complet

    def test_UX11_jeton_de_revision_change_a_chaque_creation_et_modification(self):
        first = self.js(self.api('livrables/revision'))['revision']
        self.assertEqual(self.js(self.api('livrables/revision'))['revision'], first)   # rien de neuf : rien à recharger
        row = self.demande()
        second = self.js(self.api('livrables/revision'))['revision']
        self.assertNotEqual(first, second)
        data = self.js(self.api('livrables'))
        self.assertEqual((data['counts']['total'], data['revision']), (1, second))
        liv.relire(self.desk, row['document_id'], row['version_id'], 'relu', *AVOCAT)
        self.assertNotEqual(self.js(self.api('livrables/revision'))['revision'], second)

    def test_UX12_source_indisponible_pas_de_faux_zero_ni_tout_est_relu(self):
        self.desk.setting('cockpit530:drafts', {'at': 9e18, 'ok': False, 'validity': '5', 'items': []})
        data = liv.projection(self.desk)
        self.assertFalse(data['complet'])
        self.assertEqual(next(s for s in data['sources'] if s['id'] == 'messagerie')['state'], 'unavailable')
        html = cockpit530.review_html(self.desk, '/p')
        self.assertNotIn('Tout est relu', html)
        self.assertIn('Liste incomplète', html)
        section = web.section_html(self.desk, '/p', None)
        self.assertIn('ce n’est pas un zéro certain', section)
        self.assertNotIn('Aucun document préparé', section)

    def test_UX12_vide_certain_et_panne_ne_se_confondent_pas(self):
        data = liv.projection(self.desk)
        self.assertTrue(data['complet'])
        self.assertEqual(next(s for s in data['sources'] if s['id'] == 'docrequest520')['state'], 'empty')
        self.assertIn('Tout est relu', cockpit530.review_html(self.desk, '/p'))
        self.assertIn('Aucun document préparé', web.section_html(self.desk, '/p', None))

    def test_UX13_ancien_projet_non_relu_reste_accessible_avec_compteur(self):
        old = (datetime.now(timezone.utc) - timedelta(days=45)).isoformat()
        self.demande('old', '/Dossiers/DEMO/PROJETS/Ancien.docx', when=old)
        self.assertEqual(self.items(), [])                        # hors de la vue récente d'Aujourd'hui
        data = liv.projection(self.desk, recence='30')
        self.assertEqual(data['items'], [])
        self.assertEqual(data['counts']['a_relire_anciens'], 1)
        self.assertEqual(data['filters']['recence'], '30')
        everything = liv.projection(self.desk, revue='to_review')
        self.assertEqual(len(everything['items']), 1)
        self.assertIn('plus de 30 jours', web.aujourdhui_html(self.desk, '/p'))

    def _deux_cartes(self):
        key = 'a' * 64
        self.desk.db.execute('CREATE TABLE IF NOT EXISTS reply_drafts5687(draft_key TEXT PRIMARY KEY, mail_key TEXT NOT NULL)')
        self.desk.db.execute('INSERT INTO reply_drafts5687 VALUES(?,?)', ('brouillon-1', key))
        self.desk.db.execute('INSERT INTO manual_drafts VALUES(?,?,?)', (key, json.dumps({'matter': MID, 'result': {'body': 'Réponse fictive à Monsieur DURAND.', 'requires_decision': True, 'limits': ['Point à vérifier']}}, ensure_ascii=False).replace('"requires_decision": true', '"requires_decision": true'), datetime.now(timezone.utc).isoformat()))
        self.desk.db.commit()
        return key

    def test_UX14_reponse_et_brouillon_une_seule_carte_une_seule_decision(self):
        key = self._deux_cartes()
        cards = reponses5671.queue_items(self.desk, '/p')
        self.assertEqual([c['object'] for c in cards], ['reponse5671:' + key])      # pas encore déposée : la décision est ici
        self.desk.setting('cockpit530:drafts', {'at': 9e18, 'ok': True, 'validity': '5', 'items': [
            {'uid': '7', 'subject': 'Re: Calendrier', 'to': 'confrere@example.test', 'date': datetime.now(timezone.utc).isoformat(), 'agent': True, 'agent_key': 'brouillon-1', 'message_id': 'axiorhub-1@example.test', 'in_reply_to': 'orig@example.test'}]})
        self.assertEqual(reponses5671.queue_items(self.desk, '/p'), [])              # déposée : plus de seconde décision
        data = liv.projection(self.desk, nature_filtre='courriel')
        self.assertEqual(len(data['items']), 1)
        card = data['items'][0]
        self.assertEqual((card['response_id'], card['deposit_state']), (key, 'depose'))
        row = liv.charger(self.desk, card['document_id'])
        self.assertEqual(row['deposit_ref'], '5:7')                                  # référence IMAP distincte de la référence source
        self.assertEqual(self.desk.db.execute("SELECT COUNT(DISTINCT document_id) FROM prepared_items5689 WHERE kind='courriel'").fetchone()[0], 1)

    def test_UX14_objet_identique_sur_deux_courriels_reste_deux_cartes(self):
        self.desk.setting('cockpit530:drafts', {'at': 9e18, 'ok': True, 'validity': '5', 'items': [
            {'uid': '7', 'subject': 'Re: Votre dossier', 'to': 'a@example.test', 'date': '2026-11-02', 'agent': True, 'agent_key': 'k1', 'message_id': 'm1@example.test', 'in_reply_to': ''},
            {'uid': '8', 'subject': 'Re: Votre dossier', 'to': 'b@example.test', 'date': '2026-11-02', 'agent': True, 'agent_key': 'k2', 'message_id': 'm2@example.test', 'in_reply_to': ''}]})
        self.assertEqual(liv.projection(self.desk, nature_filtre='courriel')['counts']['par_nature']['courriel'], 2)

    def test_API_pagination_filtres_et_compteurs(self):
        for n in range(5):
            self.demande('r%d' % n, '/Dossiers/DEMO/PROJETS/Contrat %d.docx' % n, texte_docx('Contrat %d' % n))
        self.demande('n1', '/Dossiers/DEMO/LIVRABLES/Note.docx', texte_docx('Note.'), kind='note')
        first = self.js(self.api('livrables', {'limite': '4'}))
        self.assertEqual((len(first['items']), first['counts']['total'], first['counts']['par_nature']['contrat'], first['counts']['par_nature']['note']), (4, 6, 5, 1))
        second = self.js(self.api('livrables', {'limite': '4', 'curseur': first['next_cursor']}))
        self.assertEqual(len(second['items']), 2)
        self.assertIsNone(second['next_cursor'])
        self.assertEqual(len({c['document_id'] for c in first['items'] + second['items']}), 6)
        notes = self.js(self.api('livrables', {'nature': 'note'}))
        self.assertEqual([c['kind'] for c in notes['items']], ['note'])
        self.assertEqual(notes['counts']['par_nature']['contrat'], 5)       # les autres natures gardent leur compteur
        self.assertEqual(self.api('livrables', {'nature': 'inconnue'})['status'], '400 Bad Request')
        for field in ('items', 'sources', 'next_cursor', 'scope', 'generated_at', 'synced_at', 'filters', 'counts'):
            self.assertIn(field, first)

    def test_API_aucun_parcours_du_stockage_a_l_affichage(self):
        self.demande()
        lectures, ecritures = self.nc.lectures, len(self.nc.ecritures)
        with patch.object(self.nc, 'stat', side_effect=AssertionError('stockage lu à l’affichage')):
            liv.projection(self.desk)
            self.items()
            file5657.review_items(self.desk, '/p')
            web.section_html(self.desk, '/p', MID)
        self.assertEqual((self.nc.lectures, len(self.nc.ecritures)), (lectures, ecritures))

    def test_API_dossier_reserve_filtre_avant_compteurs(self):
        self.demande()
        data = liv.projection(self.desk, frozenset({MID}))
        self.assertEqual((data['items'], data['counts']['total'], data['counts']['a_relire']), ([], 0, 0))

    def test_API_export_du_cabinet_couvre_les_tables(self):
        tables = {t[2] for t in export5688.TABLES}
        for table in ('prepared_items5689', 'prepared_representations5689', 'prepared_reviews5689', 'prepared_source_links5689', 'document_checks5689', 'document_patches5689', 'prepared_exports5689', 'prepared_legacy_marks5689'):
            self.assertIn(table, tables)


class Publication(Base):
    def test_IMP05C_accuse_perdu_retrouve_le_document_et_sa_preuve(self):
        raw = texte_docx('Acte fictif.')
        path = '/Dossiers/DEMO/Acte.docx'
        first = liv.reserve(self.desk, 'essai', 'o1', 1, matter=MID, title='Acte', path=path, sha256=hashlib.sha256(raw).hexdigest())
        self.nc.put_file(path, raw)
        # accusé perdu : le producteur n'a jamais appelé constater et recommence
        again = liv.reserve(self.desk, 'essai', 'o1', 1, matter=MID, title='Acte', path=path, sha256=hashlib.sha256(raw).hexdigest())
        self.assertEqual(again['id'], first['id'])
        self.assertTrue(liv.deja_publiee(self.desk, again, self.nc))
        done = liv.constater(self.desk, again, self.nc, path, raw)
        self.assertEqual(done['production_state'], 'verified')
        self.assertEqual(len(self.nc.ecritures), 1)
        check = {c['check_id']: c for c in verification.vue(self.desk, done)['checks']}
        self.assertEqual(check['storage.file']['state'], 'passed')
        self.assertTrue(check['storage.file']['evidence_ref'].startswith('sha256:'))

    def test_IMP05C_reconciliation_apres_arret_sans_recreer(self):
        raw = texte_docx('Acte fictif.')
        path = '/Dossiers/DEMO/Acte.docx'
        res = liv.reserve(self.desk, 'essai', 'o2', 1, matter=MID, title='Acte', path=path, sha256=hashlib.sha256(raw).hexdigest())
        self.nc.put_file(path, raw)
        out = liv.reconcilier(self.desk, self.nc, age_minutes=-1)
        self.assertEqual(out['verifies'], 1)
        self.assertEqual(liv._item(self.desk, res['id'])['production_state'], 'verified')
        self.assertEqual(len(self.nc.ecritures), 1)

    def test_IMP05C_sortie_absente_devient_echec_visible_et_contenu_different_partiel(self):
        absent = liv.reserve(self.desk, 'essai', 'o3', 1, matter=MID, title='A', path='/Dossiers/DEMO/Absent.docx', sha256='a' * 64)
        different = liv.reserve(self.desk, 'essai', 'o4', 1, matter=MID, title='B', path='/Dossiers/DEMO/Autre.docx', sha256='b' * 64)
        self.nc.add('/Dossiers/DEMO/Autre.docx', b'contenu inattendu')
        out = liv.reconcilier(self.desk, self.nc, age_minutes=-1)
        self.assertEqual((out['absents'], out['differents']), (1, 1))
        self.assertEqual(liv._item(self.desk, absent['id'])['production_state'], 'failed')
        self.assertEqual(liv._item(self.desk, different['id'])['production_state'], 'partial')
        self.assertEqual(liv.etat_contrat(liv._item(self.desk, different['id'])), 'partial')
        self.assertNotIn('b' * 64, liv._item(self.desk, different['id'])['version_id'])

    def test_IMP05C_fichier_non_conforme_jamais_marque_verifie(self):
        res = liv.reserve(self.desk, 'essai', 'o5', 1, matter=MID, title='A', path='/Dossiers/DEMO/A.docx')
        self.nc.add('/Dossiers/DEMO/A.docx', b'autre contenu')
        out = liv.constater(self.desk, res, self.nc, raw=b'contenu attendu')
        self.assertEqual((out['production_state'], out['state_reason']), ('failed', 'fichier_non_conforme'))
        self.assertEqual(liv.projection(self.desk)['items'][0]['state'], 'failed')

    def test_IMP05C_sortie_ne_fait_pas_echouer_le_producteur(self):
        with patch.object(liv, 'reserve', side_effect=sqlite3.OperationalError('base occupée')):
            sortie = liv.Sortie(self.desk, 'essai', 'o', 1)
        self.assertIsNone(sortie.item)
        self.assertFalse(sortie.publiee(self.nc))
        self.assertIsNone(sortie.constater(self.nc, '/x'))


class Visionneuse(Base):
    def fichier(self, path, raw, **fields):
        res = liv.reserve(self.desk, 'essai', path, 1, matter=MID, title=fields.pop('title', 'Document fictif'), path=path, **fields)
        self.nc.put_file(path, raw)
        return liv.constater(self.desk, res, self.nc, path, raw)

    def test_LIV01_detail_et_lien_direct_sur_le_meme_contenu(self):
        row = self.demande()
        view = self.js(self.api('livrables/' + row['document_id']))
        self.assertEqual(view['view']['deliverable_id'], row['document_id'])
        for key in ('deliverable_id', 'matter_id', 'version_id', 'storage_ref', 'title', 'kind', 'mime_type', 'preview_kind', 'state', 'actions', 'allowed_formats', 'authorized_destination_refs', 'capability_revision', 'unavailable_reasons'):
            self.assertIn(key, view['view'])
        self.assertNotIn(row['path'], json.dumps(view['view'], ensure_ascii=False))     # référence opaque seulement

    def test_LIV01_page_autonome_avec_retour_et_actions(self):
        row = self.demande()
        shell = lambda title, body, *a: body
        env = {'HTTP_X_AXIORHUB_USER': AVOCAT[0], 'HTTP_X_AXIORHUB_ROLE': AVOCAT[1]}
        out = web.page_route(env, self.f.c, {'csrf': 'c'}, '/p', '/livrables/' + row['document_id'], {'retour': '/p/livrables?nature=contrat&q=durand'}, shell)
        html = out['body']
        self.assertIn('href="/p/livrables?nature=contrat&amp;q=durand"', html)
        for label in ('Modifier avec l’IA', 'Ouvrir dans l’éditeur', 'Télécharger', 'Exporter', 'Contrat DURAND', 'Version'[:7].lower()):
            self.assertIn(label, html)
        self.assertIn('sandbox=""', html)

    def test_LIV01_retour_ne_redirige_jamais_hors_de_l_application(self):
        for bad in ('https://exemple.test/', '//exemple.test/x', '/\\exemple.test', 'javascript:alert(1)', '/a\nb'):
            self.assertEqual(web._retour_sur(bad, '/p'), '/p/livrables', bad)
        self.assertEqual(web._retour_sur('/p/dossier?id=DOS-001&vue=travaux', '/p'), '/p/dossier?id=DOS-001&vue=travaux')

    def test_LIV01_lien_vers_une_version_remplacee_montre_la_derniere_par_document(self):
        row = self.demande()
        self.demande(raw=texte_docx('Version deux.'))
        self.assertEqual(self.js(self.api('livrables/' + row['document_id']))['view']['version_id'], liv.charger(self.desk, row['document_id'])['version_id'])

    def test_LIV02_docx_rendu_cadre_isole(self):
        row = self.demande()
        out = self.js(self.api('livrables/%s/apercu' % row['document_id']))
        self.assertEqual(out['preview_kind'], 'document')
        self.assertIn('Contrat entre DURAND', out['html'])
        frame = self.api('livrables/%s/cadre' % row['document_id'])
        self.assertIn("default-src 'none'", frame['csp'])
        self.assertIn(('Cache-Control', 'private, no-store'), frame['headers'])

    def test_LIV02_pdf_et_image_par_reference_avec_type_maitrise(self):
        from agent._vendor.pypdf import PdfWriter
        w = PdfWriter()
        w.add_blank_page(100, 100)
        buf = io.BytesIO()
        w.write(buf)
        pdf = self.fichier('/Dossiers/DEMO/Piece.pdf', buf.getvalue())
        png = self.fichier('/Dossiers/DEMO/Plan.png', PNG)
        out = self.js(self.api('livrables/%s/apercu' % pdf['document_id']))
        self.assertEqual(out['preview_kind'], 'pdf')
        self.assertIn('/api440/m5689/livrables/', out['url'])
        served = self.api('livrables/%s/telecharger' % pdf['document_id'], {'version': pdf['version_id'], 'apercu': '1'})
        self.assertEqual(served['kind'], 'application/pdf')
        self.assertTrue(served['frame_self'])
        self.assertEqual(self.js(self.api('livrables/%s/apercu' % png['document_id']))['preview_kind'], 'image')
        self.assertEqual(self.api('livrables/%s/telecharger' % png['document_id'], {'apercu': '1'})['kind'], 'image/png')

    def test_LIV02_faux_pdf_jamais_affiche(self):
        row = self.fichier('/Dossiers/DEMO/Faux.pdf', b'<html><script>alert(1)</script></html>')
        out = self.api('livrables/%s/telecharger' % row['document_id'], {'apercu': '1'})
        self.assertEqual(out['status'], '400 Bad Request')

    def test_LIV02_courriel_html_assaini_et_images_bloquees(self):
        raw = ('From: Maître BERNARD <confrere@example.test>\r\nTo: cabinet@example.test\r\nSubject: Audience RG 24/01234\r\nMIME-Version: 1.0\r\nContent-Type: text/html; charset=utf-8\r\n\r\n'
               '<p onclick="vol()">Bonjour</p><script>alert(1)</script><img src="https://suivi.example.test/pixel.png"><a href="https://piege.example.test/x">Cliquez</a><iframe src="https://x.example.test"></iframe>').encode()
        row = self.fichier('/Dossiers/DEMO/Message.eml', raw)
        out = self.js(self.api('livrables/%s/apercu' % row['document_id']))
        self.assertEqual(out['preview_kind'], 'email')
        html = out['html']
        for forbidden in ('<script', 'onclick', '<img', 'src=', '<iframe', 'href='):
            self.assertNotIn(forbidden, html)
        self.assertIn('Bonjour', html)
        self.assertIn('piege.example.test', html)                 # adresse visible, inerte
        self.assertIn('1 image(s) distante(s) non chargée(s)', out['note'])

    def test_LIV02_tableau_csv_et_classeur(self):
        row = self.fichier('/Dossiers/DEMO/Temps.csv', 'Nom;Heures\nDURAND;<b>3</b>\n'.encode())
        out = self.js(self.api('livrables/%s/apercu' % row['document_id']))
        self.assertEqual(out['preview_kind'], 'table')
        self.assertIn('<td>DURAND</td>', out['html'])
        self.assertIn('&lt;b&gt;3&lt;/b&gt;', out['html'])

    def test_LIV02_texte_markdown_sans_script(self):
        row = self.fichier('/Dossiers/DEMO/Note.md', '# Titre\n<script>alert(1)</script>'.encode())
        out = self.js(self.api('livrables/%s/apercu' % row['document_id']))
        self.assertEqual(out['preview_kind'], 'text')
        self.assertNotIn('<script', out['html'])

    def test_LIV02_format_inconnu_dit_sa_limite(self):
        row = self.fichier('/Dossiers/DEMO/Plan.dwg', b'\x00\x01binaire')
        out = self.js(self.api('livrables/%s/apercu' % row['document_id']))
        self.assertEqual((out['preview_kind'], out['html'], out['url']), ('unsupported', '', ''))
        self.assertIn('Aperçu impossible', out['note'])
        view = self.js(self.api('livrables/' + row['document_id']))['view']
        self.assertFalse(view['actions']['preview'])
        self.assertTrue(view['actions']['download'])

    def test_LIV04_telechargement_exact_nom_et_type(self):
        row = self.demande()
        out = self.api('livrables/%s/telecharger' % row['document_id'], {'version': row['version_id']})
        self.assertEqual(out['body'], self.nc.files[row['path']])
        self.assertEqual(out['kind'], DOCX)
        headers = dict(out['headers'])
        self.assertIn('Contrat%20DURAND.docx', headers['Content-Disposition'])
        self.assertTrue(headers['Content-Disposition'].startswith('attachment;'))
        self.assertEqual(headers['Cache-Control'], 'private, no-store')
        self.assertEqual(headers['X-Content-Type-Options'], 'nosniff')

    def test_LIV04_aucune_autre_version_substituee(self):
        row = self.demande()
        self.demande(raw=texte_docx('Version deux.'))
        out = self.api('livrables/%s/telecharger' % row['document_id'], {'version': row['version_id']})
        self.assertEqual(out['status'], '409 Conflict')
        self.assertEqual(self.js(out)['error'], 'version_plus_disponible')

    def test_LIV04_version_courante_changee_dans_le_stockage_est_constatee(self):
        row = self.demande()
        self.nc.add(row['path'], texte_docx('Édition humaine.'))
        out = self.api('livrables/%s/telecharger' % row['document_id'])
        self.assertEqual(out['body'], self.nc.files[row['path']])
        self.assertEqual(liv.charger(self.desk, row['document_id'])['version_number'], 2)

    def test_LIV04_nom_de_fichier_maitrise(self):
        row = self.fichier('/Dossiers/DEMO/x.pdf', b'%PDF-1.4 x', title='../../etc/passwd"\r\nX-Injection: 1')
        out = self.api('livrables/%s/telecharger' % row['document_id'])
        header = dict(out['headers'])['Content-Disposition']
        self.assertNotIn('\n', header)
        self.assertNotIn('/', header.split("filename*=UTF-8''")[0].replace('attachment;', ''))
        self.assertNotIn('../', export._nom(row, 'pdf'))

    def test_LIV04_chemin_recu_du_navigateur_ignore(self):
        row = self.demande()
        out = self.api('livrables/%s/telecharger' % row['document_id'], {'path': '/Dossiers/AUTRE/secret.docx', 'chemin': '/etc/passwd'})
        self.assertEqual(out['body'], self.nc.files[row['path']])
        self.assertEqual(self.api('livrables/..%2F..%2Fsecret/telecharger')['status'], '404 Not Found')

    def test_LIV06_revocation_appliquee_au_detail_a_l_export_et_au_telechargement(self):
        row = self.demande()
        doc = row['document_id']
        for route in ('livrables/%s/telecharger', 'livrables/%s', 'livrables/%s/apercu', 'livrables/%s/cadre'):
            self.assertEqual(self.api(route % doc, who=ASSISTANT)['status'], '200 OK')
        with patch('agent.membres5650.hidden_for', return_value=frozenset({MID})):
            for route in ('livrables/%s/telecharger', 'livrables/%s', 'livrables/%s/apercu', 'livrables/%s/cadre'):
                self.assertEqual(self.api(route % doc, who=ASSISTANT)['status'], '403 Forbidden', route)
            self.assertEqual(self.api('livrables/%s/exporter' % doc, body={'format': 'docx', 'destination': 'dest-dossier', 'cle': 'cle-000001'}, who=ASSISTANT)['status'], '403 Forbidden')

    def test_LIV06_droit_exporter_retire_refuse_a_chaque_action(self):
        row = self.demande()
        membres = {**membres5650.ROLE_DEFAULTS['assistant'], 'exporter': False}
        with patch('agent.membres5650.effective_rights', return_value=membres):
            self.assertEqual(self.api('livrables/%s/telecharger' % row['document_id'], who=ASSISTANT)['status'], '403 Forbidden')
            self.assertEqual(self.api('livrables/%s/exporter' % row['document_id'], body={'format': 'docx', 'destination': 'dest-dossier', 'cle': 'cle-000002'}, who=ASSISTANT)['status'], '403 Forbidden')
            view = self.js(self.api('livrables/' + row['document_id'], who=ASSISTANT))['view']
        self.assertFalse(view['actions']['download'])
        self.assertTrue(any('droit d’exporter' in r for r in view['unavailable_reasons']))
        self.assertIn('exporter', membres5650.fine_rights('POST', '/api440/m5689/livrables/x/exporter'))
        self.assertIn('exporter', membres5650.fine_rights('GET', '/api440/m5689/livrables/x/telecharger'))

    def test_LIV06_honoraires_masques_sans_le_droit(self):
        now = datetime.now(timezone.utc).isoformat()
        from agent import facturation5653 as fact
        fact.ensure_schema(self.desk) if hasattr(fact, 'ensure_schema') else None
        self.desk.db.execute("INSERT INTO b53_documents(id,kind,matter,state,snapshot_json,created_by,created,updated,files_json) VALUES('f1','facture',?,'emise','{}','a',?,?,?)", (MID, now, now, json.dumps({'pdf': {'chemin': '/Dossiers/DEMO/FACTURATION/F1.pdf', 'sha256': 'a' * 64, 'verifie_le': now}})))
        self.desk.db.commit()
        liv.synchroniser(self.desk)
        row = self.desk.db.execute("SELECT * FROM prepared_items5689 WHERE source_service='facturation5653'").fetchone()
        self.assertEqual((row['protected'], row['required_right'], row['review_state']), ('emis', 'voir_honoraires', 'not_required'))
        rights = {**membres5650.ROLE_DEFAULTS['assistant'], 'voir_honoraires': False}
        with patch('agent.membres5650.effective_rights', return_value=rights):
            self.assertEqual(self.js(self.api('livrables', who=ASSISTANT))['counts']['total'], 0)
            self.assertEqual(self.api('livrables/' + row['document_id'], who=ASSISTANT)['status'], '403 Forbidden')

    def test_LIV06_vues_partagees_filtrent_comme_l_api(self):
        now = datetime.now(timezone.utc).isoformat()
        self.demande()
        from agent import facturation5653 as fact
        fact.ensure_schema(self.desk) if hasattr(fact, 'ensure_schema') else None
        self.desk.db.execute("INSERT INTO b53_documents(id,kind,matter,state,snapshot_json,created_by,created,updated,files_json) VALUES('f2','devis',?,'brouillon','{}','a',?,?,?)", (MID, now, now, json.dumps({'pdf': {'chemin': '/Dossiers/DEMO/FACTURATION/Devis ZORVAL.pdf', 'sha256': 'b' * 64, 'verifie_le': now}})))
        self.desk.db.commit()
        self.assertEqual(len(self.items()), 2)
        self.assertIn('Devis', web.section_html(self.desk, '/p', MID))
        membres5650.set_ai_denied(('factures', 'temps'))
        self.addCleanup(membres5650.clear)
        self.assertEqual(len(self.items()), 1)                       # sans le droit, ni carte ni compteur
        html = web.section_html(self.desk, '/p', MID)
        self.assertNotIn('Devis', html)
        membres5650.clear()
        membres5650.set_viewer({MID})
        self.assertEqual(self.items(), [])                           # dossier réservé : même règle
        self.assertNotIn('Contrat DURAND', web.section_html(self.desk, '/p', None))

    def test_LIV06_ecriture_refusee_sans_jeton_de_page(self):
        row = self.demande()
        raw = b'{}'
        env = {'HTTP_X_AXIORHUB_USER': AVOCAT[0], 'HTTP_X_AXIORHUB_ROLE': AVOCAT[1], 'HTTP_ORIGIN': 'https://ailleurs.example.test', 'HTTP_X_CSRF_TOKEN': 'faux', 'CONTENT_LENGTH': '2', 'CONTENT_TYPE': 'application/json', 'wsgi.input': io.BytesIO(raw)}
        out = web5689.api(env, self.desk, {'origin': 'https://cabinet.example.test', 'csrf': 'jeton'}, '/p', 'm5689/livrables/%s/relecture' % row['document_id'], {}, 'POST')
        self.assertEqual(self.js(out)['error'], 'origine_ou_csrf_refuse')
        self.assertFalse(self.desk.db.execute('SELECT 1 FROM prepared_reviews5689').fetchone())

    def test_LIV06_routes_declarees_inconnue_refusee(self):
        self.assertTrue(web5689.allowed('assistant', 'livrables/abc/exporter'))
        self.assertFalse(web5689.allowed('assistant', 'documents/propositions/abc/decider'))
        self.assertTrue(web5689.allowed('avocat', 'documents/propositions/abc/decider'))
        self.assertFalse(web5689.allowed('avocat', 'livrables/abc/supprimer'))
        self.assertFalse(web5689.allowed('invite', 'livrables/abc/exporter'))

    def test_LIV08_editeur_absent_motif_et_repli(self):
        self.caps['editeur'] = False
        row = self.demande()
        view = self.js(self.api('livrables/' + row['document_id']))['view']
        self.assertFalse(view['actions']['editor'])
        self.assertTrue(view['actions']['download'])
        self.assertTrue(any('Éditeur EuroOffice non relié' in r for r in view['unavailable_reasons']))

    def test_LIV08_convertisseur_absent_motif_repli_et_aucune_reussite(self):
        self.caps['convertisseur'] = False
        row = self.demande()
        view = self.js(self.api('livrables/' + row['document_id']))['view']
        self.assertEqual(view['allowed_formats'], ['docx'])
        self.assertTrue(any('LibreOffice absent' in r for r in view['unavailable_reasons']))
        out = self.api('livrables/%s/exporter' % row['document_id'], body={'format': 'pdf', 'destination': 'dest-dossier', 'cle': 'cle-000003'})
        self.assertEqual(self.js(out)['error'], 'conversion_indisponible')
        self.assertIn('format d’origine', self.js(out)['message'])
        self.assertEqual(self.api('livrables/%s/telecharger' % row['document_id'], {'format': 'pdf'})['status'], '400 Bad Request')
        self.assertEqual(self.nc.ecritures, [])

    def test_LIV08_page_affiche_l_erreur_de_stockage_sans_masquer_le_document(self):
        row = self.demande()
        self.nc.panne = 'http_503'
        shell = lambda title, body, *a: body
        env = {'HTTP_X_AXIORHUB_USER': AVOCAT[0], 'HTTP_X_AXIORHUB_ROLE': AVOCAT[1]}
        html = web.page_route(env, self.f.c, {'csrf': 'c'}, '/p', '/livrables/' + row['document_id'], {}, shell)['body']
        self.assertIn('Contrat DURAND', html)
        self.assertIn('Aperçu indisponible', html)


class Routeur(Base):
    """Routes réelles de ``web440`` : page complète avec son gabarit, API sous le préfixe, statique déclaré."""

    def route(self, path, args=None, method='GET'):
        from agent import web440
        env = {'HTTP_X_AXIORHUB_USER': AVOCAT[0], 'HTTP_X_AXIORHUB_ROLE': AVOCAT[1], 'REQUEST_METHOD': method}
        return web440.route(env, self.f.c, {'csrf': 'jeton', 'origin': 'https://cabinet.example.test', 'owner': AVOCAT[0]}, '/p', path, args or {}, method)

    def test_LIV01_liste_et_detail_servis_par_le_routeur(self):
        row = self.demande()
        listing = self.route('/livrables')
        self.assertEqual(listing['status'], '200 OK')
        self.assertIn('/p/livrables/' + row['document_id'], listing['body'])
        self.assertIn('Documents préparés', listing['body'])
        self.assertIn('aria-current="page"', listing['body'])
        detail = self.route('/livrables/' + row['document_id'])
        self.assertEqual(detail['status'], '200 OK')
        self.assertIn('Contrat DURAND', detail['body'])
        self.assertIn('v5689-livrables.js', detail['body'])
        self.assertEqual(self.route('/livrables/inconnu')['status'], '404 Not Found')
        self.assertEqual(self.route('/api440/m5689/livrables')['status'], '200 OK')
        self.assertEqual(self.route('/static/v5689-livrables.js')['status'], '200 OK')

    def test_LIV01_page_du_dossier_et_file_ouvrent_le_meme_detail(self):
        from agent import dossier5627
        row = self.demande()
        html = dossier5627.travaux_html(self.desk, '/p', {'id': MID})
        self.assertIn('/p/livrables/' + row['document_id'], html)
        self.assertEqual(liv.lien_demande(self.desk, '/p', 'r1'), '/p/livrables/' + row['document_id'])

    def test_UX12_filtre_documents_visible_a_zero_et_etats_distincts(self):
        with patch('agent.aujourdhui5614.items', return_value=[]):
            html = file5651.html(self.desk, '/p')
            self.assertIn('Documents <span class="q51-n">0</span>', html)
            vide = file5651.html(self.desk, '/p', None, 'documents')
            self.assertIn('toutes les sources ont répondu', vide)
            self.desk.setting('cockpit530:drafts', {'at': 9e18, 'ok': False, 'validity': '5', 'items': []})
            liv.synchroniser(self.desk)
            panne = file5651.html(self.desk, '/p', None, 'documents')
        self.assertIn('Liste incomplète', panne)
        self.assertNotIn('toutes les sources ont répondu', panne)

    def test_API_forme_du_contrat_de_projection(self):
        self.demande()
        item = liv.projection(self.desk)['items'][0]
        for key in ('id', 'document_id', 'version_id', 'matter_id', 'kind', 'title', 'state', 'review', 'verified', 'location_ref', 'updated_at'):
            self.assertIn(key, item)
        self.assertIn(item['kind'], liv.NATURES)
        self.assertIn(item['state'], ('preparing', 'ready', 'partial', 'failed', 'replaced', 'archived'))
        self.assertIn(item['review'], ('to_review', 'reviewed', 'changes_requested', 'not_required'))
        self.assertTrue(item['location_ref'].startswith('loc-'))


class Export(Base):
    def lancer(self, row, fmt='pdf', dest='dest-exports', cle='cle-export-1'):
        return self.js(self.api('livrables/%s/exporter' % row['document_id'], body={'format': fmt, 'destination': dest, 'cle': cle, 'version_id': row['version_id']}))

    def pdf(self):
        from agent._vendor.pypdf import PdfWriter
        w = PdfWriter()
        w.add_blank_page(100, 100)
        buf = io.BytesIO()
        w.write(buf)
        return buf.getvalue()

    def job(self, out):
        return export.executer(self.desk, {'export': out['id']}, self.nc)

    def test_LIV05_formats_qualifies_et_fichier_converti_verifie(self):
        row = self.demande()
        self.assertEqual(self.js(self.api('livrables/' + row['document_id']))['view']['allowed_formats'], ['docx', 'pdf'])
        with patch('agent.pieces510.docx_to_pdf', return_value=self.pdf()):
            out = self.lancer(row)
            self.assertFalse(out['done'])                 # jamais de succès anticipé
            self.assertEqual(self.js(self.api('livrables/exports/' + out['id']))['state'], 'demande')
            self.job(out)
        state = self.js(self.api('livrables/exports/' + out['id']))
        self.assertEqual((state['state'], state['done']), ('exporte', True))
        path = next(p for p in self.nc.files if p.endswith('.pdf'))
        self.assertTrue(path.startswith('/Dossiers/DEMO/EXPORTS/'))
        card = liv.projection(self.desk)['items'][0]
        self.assertEqual(card['formats'], ['docx', 'pdf'])                   # même carte
        self.assertEqual(liv.projection(self.desk)['counts']['total'], 1)

    def test_LIV05_facture_garde_son_format_pdf_ordinaire_pas_structure(self):
        row = dict(self.demande(), source_service='facturation5653')
        self.assertEqual(apercu.formats(row, {'convertisseur': True}), ['docx'])

    def test_LIV05_destination_non_autorisee_refusee(self):
        row = self.demande()
        out = self.api('livrables/%s/exporter' % row['document_id'], body={'format': 'docx', 'destination': '/etc', 'cle': 'cle-000004'})
        self.assertEqual(out['status'], '403 Forbidden')
        self.assertFalse(self.desk.db.execute("SELECT 1 FROM jobs WHERE kind='export_livrable5689'").fetchone())

    def test_LIV05_original_signe_jamais_ecrase(self):
        row = self.demande()
        self.desk.db.execute("UPDATE prepared_items5689 SET protected='signe' WHERE id=?", (row['id'],))
        self.desk.db.commit()
        row = liv.charger(self.desk, row['document_id'])
        original = self.nc.files[row['path']]
        self.job(self.lancer(row, 'docx', 'dest-dossier'))
        self.assertEqual(self.nc.files[row['path']], original)
        self.assertEqual([k for k, _ in self.nc.ecritures].count('remplacement'), 0)
        self.assertEqual(len([p for p in self.nc.files if p.endswith('.docx')]), 2)

    def test_LIV05_conversion_invalide_jamais_exportee(self):
        row = self.demande()
        with patch('agent.pieces510.docx_to_pdf', return_value=b'%PDF-pas un pdf'):
            out = self.lancer(row)
            with self.assertRaisesRegex(Stop, 'conversion_non_verifiee'):
                self.job(out)
        state = self.js(self.api('livrables/exports/' + out['id']))
        self.assertEqual(state['state'], 'echec')
        self.assertFalse(state['done'])
        self.assertEqual(self.nc.ecritures, [])

    def test_LIV07_reponse_perdue_meme_cle_pas_de_second_fichier(self):
        row = self.demande()
        with patch('agent.pieces510.docx_to_pdf', return_value=self.pdf()):
            out = self.lancer(row)
            self.job(out)
            again = self.lancer(row)
            self.job(again)
        self.assertEqual(again['id'], out['id'])
        self.assertEqual(self.desk.db.execute("SELECT COUNT(*) FROM jobs WHERE kind='export_livrable5689'").fetchone()[0], 1)
        self.assertEqual(len([p for p in self.nc.files if p.endswith('.pdf')]), 1)

    def test_LIV07_coupure_apres_ecriture_reprise_sans_doublon(self):
        row = self.demande()
        real = self.nc.stat
        seen = {'n': 0}

        def coupe(path):
            info = real(path)
            if path.endswith('.pdf'):
                seen['n'] += 1
                if seen['n'] == 1:      # l'écriture a abouti, la relecture n'aura jamais lieu
                    raise RuntimeError('coupure')
            return info
        with patch('agent.pieces510.docx_to_pdf', return_value=self.pdf()):
            out = self.lancer(row)
            with patch.object(self.nc, 'stat', side_effect=coupe), self.assertRaises(RuntimeError):
                self.job(out)
            self.assertFalse(self.js(self.api('livrables/exports/' + out['id']))['done'])       # rien d'affirmé sans preuve
            self.job(out)
        self.assertTrue(self.js(self.api('livrables/exports/' + out['id']))['done'])
        self.assertEqual(len(self.nc.ecritures), 1)

    def test_LIV07_fichier_different_a_la_destination_jamais_declare_exporte(self):
        row = self.demande()
        with patch('agent.pieces510.docx_to_pdf', return_value=self.pdf()):
            out = self.lancer(row)
            self.desk.db.execute("UPDATE prepared_exports5689 SET state='ecriture', output_path='/Dossiers/DEMO/EXPORTS/X.pdf', expected_sha256=? WHERE id=?", ('a' * 64, out['id']))
            self.desk.db.commit()
            self.nc.add('/Dossiers/DEMO/EXPORTS/X.pdf', b'%PDF-autre')
            with self.assertRaisesRegex(Stop, 'fichier_different'):
                self.job(out)
        self.assertEqual(self.js(self.api('livrables/exports/' + out['id']))['state'], 'echec')

    def test_LIV07_meme_cle_autres_parametres_refusee(self):
        row = self.demande()
        with patch('agent.pieces510.docx_to_pdf', return_value=self.pdf()):
            self.lancer(row)
            out = self.api('livrables/%s/exporter' % row['document_id'], body={'format': 'docx', 'destination': 'dest-exports', 'cle': 'cle-export-1'})
        self.assertEqual(out['status'], '409 Conflict')

    def test_LIV07_conversion_par_travail_passe_par_le_point_de_controle(self):
        row = self.demande()
        with patch('agent.pause5629.checkpoint', side_effect=Stop('operation_annulee')):
            out = self.lancer(row, 'docx')
            with self.assertRaisesRegex(Stop, 'operation_annulee'):
                self.job(out)
        self.assertEqual(self.nc.ecritures, [])


class Propositions(Base):
    OP = [{'ancre': 'Le prix est de mille euros.', 'texte': 'Le prix est de deux mille euros.', 'motif': 'Accord du client'}]

    def base(self):
        return self.demande(raw=texte_docx('Contrat entre DURAND et MARTIN.', 'Le prix est de mille euros.', 'Fin du contrat.'))

    def proposer(self, row, cle='cle-prop-1', ops=None):
        return self.js(self.api('documents/%s/proposition' % row['document_id'], body={'cle': cle, 'operations': ops or self.OP, 'version_id': row['version_id'], 'sha256': row['sha256'], 'motif': 'Accord du client', 'sources': ['Courriel fictif']}))

    def decider(self, p, decision='accepter', **extra):
        return self.api('documents/propositions/%s/decider' % p['id'], body={'decision': decision, 'version_id': p['base_version_id'], 'sha256': p['base_sha256'], **extra})

    def test_LIV03_proposition_comparaison_rien_d_applique_sans_decision(self):
        row = self.base()
        before = self.nc.files[row['path']]
        p = self.proposer(row)
        self.assertEqual(p['state'], 'proposee')
        self.assertEqual(p['comparaison'][0]['avant'], 'Le prix est de mille euros.')
        self.assertEqual(p['comparaison'][0]['apres'], 'Le prix est de deux mille euros.')
        self.assertIn('ne crée pas de révisions Word natives', p['note'])
        self.assertEqual(self.nc.files[row['path']], before)
        self.assertEqual(self.nc.ecritures, [])

    def test_LIV03_accepter_publie_une_version_et_invalide_a_bon_escient(self):
        row = self.base()
        liv.relire(self.desk, row['document_id'], row['version_id'], 'relu', *AVOCAT)
        verification.controler(self.desk, liv.charger(self.desk, row['document_id']), client=self.nc)
        p = self.proposer(row)
        out = self.js(self.decider(p))
        self.assertEqual(out['state'], 'appliquee')
        current = liv.charger(self.desk, row['document_id'])
        self.assertEqual((current['version_number'], current['review_state'], current['lifecycle']), (2, 'to_review', 'current'))
        self.assertIn('deux mille euros', ' '.join(verification.paragraphes(self.nc.files[row['path']])))
        old = liv.charger(self.desk, row['document_id'], version_id=row['version_id'])
        self.assertEqual((old['lifecycle'], old['review_state']), ('replaced', 'reviewed'))
        checks = {c['check_id']: c['state'] for c in verification.vue(self.desk, current)['checks']}
        self.assertEqual(checks['storage.file'], 'passed')            # preuve refaite sur la nouvelle version
        self.assertEqual(checks['format.archive'], 'pending')         # à refaire
        self.assertEqual(checks['human.review'], 'pending')
        self.assertTrue(self.desk.db.execute("SELECT 1 FROM jobs WHERE kind='controle_livrable5689'").fetchone())
        self.assertEqual(self.js(self.api('livrables/' + row['document_id']))['view']['version_id'], current['version_id'])     # visible immédiatement

    def test_LIV03_adapter_applique_le_texte_de_l_avocat(self):
        row = self.base()
        p = self.proposer(row)
        self.assertEqual(self.js(self.decider(p, 'adapter', texte=['Le prix est de trois mille euros.']))['state'], 'appliquee')
        self.assertIn('trois mille euros', ' '.join(verification.paragraphes(self.nc.files[row['path']])))

    def test_LIV03_refuser_ne_touche_a_rien(self):
        row = self.base()
        before = self.nc.files[row['path']]
        p = self.proposer(row)
        self.assertEqual(self.js(self.decider(p, 'refuser'))['state'], 'refusee')
        self.assertEqual(self.nc.files[row['path']], before)
        self.assertEqual(self.decider(p)['status'], '409 Conflict')

    def test_LIV03_inverser_repasse_par_une_proposition(self):
        row = self.base()
        p = self.proposer(row)
        self.decider(p)
        current = liv.charger(self.desk, row['document_id'])
        back = self.js(self.api('documents/propositions/%s/inverser' % p['id'], body={'cle': 'cle-inverse'}))
        self.assertEqual(back['state'], 'proposee')
        self.assertEqual(back['base_version_id'], current['version_id'])
        self.decider(back)
        self.assertIn('Le prix est de mille euros.', verification.paragraphes(self.nc.files[row['path']]))

    def test_LIV03_proposition_ia_bonne_version_et_sources_du_dossier(self):
        row = self.base()
        calls = []

        class Modele:
            def complete(self, messages, **kw):
                calls.append(messages)
                payload = json.loads(messages[1]['content'])
                index = next(p['index'] for p in payload['paragraphes'] if 'mille' in p['texte'])
                return json.dumps({'propositions': [{'index': index, 'texte': 'Le prix est de deux mille euros.', 'motif': 'Consigne', 'sources': ['S1']}]})
        p = self.js(self.api('livrables/%s/modifier-ia' % row['document_id'], body={'cle': 'cle-ia-01', 'instruction': 'Porter le prix à deux mille euros', 'version_id': row['version_id'], 'sha256': row['sha256']}))
        self.assertEqual(p['state'], 'demandee')
        self.assertTrue(p['job_id'])
        with patch('agent.contexte5651.for_drafting', return_value={'faits': ['DURAND']}):
            patches.travail_ia(self.desk, {'proposition': p['id']}, Modele(), self.nc)
        stored = self.js(self.api('documents/%s/propositions' % row['document_id']))['propositions'][0]
        self.assertEqual((stored['state'], stored['origin']), ('proposee', 'ia'))
        self.assertIn('DONNÉES', calls[0][0]['content'])
        self.assertIn('Le prix est de mille euros.', calls[0][1]['content'])
        self.assertEqual(self.nc.ecritures, [])               # le modèle propose, il n'écrit pas

    def test_LIV03_modifier_ia_refuse_une_base_ancienne(self):
        row = self.base()
        self.nc.add(row['path'], texte_docx('Autre.'))
        liv.synchroniser_fichiers(self.desk, self.nc)
        out = self.api('livrables/%s/modifier-ia' % row['document_id'], body={'cle': 'cle-ia-02', 'instruction': 'Modifier', 'version_id': row['version_id'], 'sha256': row['sha256']})
        self.assertEqual(out['status'], '409 Conflict')

    def test_IMP05B_edition_humaine_concurrente_conflit_sans_ecrasement(self):
        row = self.base()
        p = self.proposer(row)
        human = texte_docx('Contrat entre DURAND et MARTIN.', 'Le prix est de mille cinq cents euros.', 'Fin du contrat.')
        self.nc.add(row['path'], human)
        out = self.decider(p)
        self.assertEqual(out['status'], '409 Conflict')
        body = self.js(out)
        self.assertEqual(body['error'], 'conflit_edition_concurrente')
        self.assertIn('Ouvrir la comparaison', body['conflit']['choix'])
        self.assertEqual(self.nc.files[row['path']], human)
        stored = self.js(self.api('documents/%s/propositions' % row['document_id']))['propositions'][0]
        self.assertEqual(stored['state'], 'conflit')
        self.assertEqual(stored['conflict']['passages'][0]['toujours_presente'], False)
        self.assertEqual(liv.charger(self.desk, row['document_id'])['version_number'], 2)     # l'édition humaine devient une version

    def test_IMP05B_proposition_perimee_refusee(self):
        row = self.base()
        p = self.proposer(row)
        self.demande(raw=texte_docx('Nouvelle version.'))
        self.assertEqual(self.js(self.decider(p))['error'], 'version_perimee')
        self.assertEqual(self.js(self.api('documents/%s/propositions' % row['document_id']))['propositions'][0]['state'], 'perimee')

    def test_IMP05B_modification_pendant_la_publication_copie_de_conflit(self):
        """L'ETag change entre la lecture et la publication : ``headless5684`` garde la proposition à côté, le fichier humain reste."""
        row = self.base()
        p = self.proposer(row)
        real = headless5684.apply_operations
        human = texte_docx('Édition concurrente.')
        original_stat = self.nc.stat
        calls = {'n': 0}

        def stat(path):
            calls['n'] += 1
            if calls['n'] == 3:     # après la relecture de l'étape de décision et le début de revise
                self.nc.add(row['path'], human)
            return original_stat(path)
        with patch.object(self.nc, 'stat', side_effect=stat):
            out = self.decider(p)
        self.assertEqual(out['status'], '409 Conflict')
        self.assertEqual(self.nc.files[row['path']], human)
        self.assertTrue(real)

    def test_IMP05B_editeur_ouvert_proposition_transmise_jamais_remplacee(self):
        row = self.base()
        p = self.proposer(row)
        with patch('agent.headless5684.revise', return_value={'etat': 'proposition', 'command_id': 'c1'}) as revise:
            out = self.js(self.decider(p))
        self.assertEqual(out['state'], 'editeur')
        self.assertTrue(revise.called)
        self.assertEqual(liv.charger(self.desk, row['document_id'])['version_number'], 1)

    def test_IMP05C_accuse_perdu_sur_proposition_sans_doublon(self):
        row = self.base()
        p = self.proposer(row)
        with patch.object(liv, 'nouvelle_version_constatee', side_effect=RuntimeError('arrêt après publication')), self.assertRaises(RuntimeError):
            self.decider(p)
        self.assertEqual(len(self.nc.ecritures), 1)
        out = self.js(self.decider(p))
        self.assertEqual(out['state'], 'appliquee')
        self.assertEqual(len(self.nc.ecritures), 1)           # aucune seconde écriture
        self.assertEqual(liv.charger(self.desk, row['document_id'])['version_number'], 2)
        again = self.js(self.decider(p))                      # accusé perdu, encore : même résultat
        self.assertEqual(again['state'], 'appliquee')
        self.assertEqual(len(self.nc.ecritures), 1)

    def test_IMP05C_proposition_meme_cle_meme_resultat(self):
        row = self.base()
        first = self.proposer(row)
        again = self.proposer(row)
        self.assertEqual(first['id'], again['id'])
        other = self.api('documents/%s/proposition' % row['document_id'], body={'cle': 'cle-prop-1', 'operations': [{'ancre': 'Fin du contrat.', 'texte': 'Fin.'}], 'version_id': row['version_id'], 'sha256': row['sha256']})
        self.assertEqual(other['status'], '409 Conflict')

    def test_LIV05_original_recu_signe_ou_emis_jamais_modifie(self):
        for state in ('recu', 'signe', 'emis'):
            row = self.demande(ident='r' + state, path='/Dossiers/DEMO/%s.docx' % state, raw=texte_docx('Original ' + state))
            self.desk.db.execute('UPDATE prepared_items5689 SET protected=? WHERE id=?', (state, row['id']))
            self.desk.db.commit()
            out = self.api('documents/%s/proposition' % row['document_id'], body={'cle': 'cle-' + state + '-001', 'operations': [{'ancre': 'Original ' + state, 'texte': 'Modifié'}], 'version_id': row['version_id'], 'sha256': row['sha256']})
            self.assertEqual(out['status'], '403 Forbidden')
            self.assertEqual(self.js(self.api('livrables/' + row['document_id']))['view']['actions']['ai_edit'], False)

    def test_LIV05_copie_de_travail_distincte_de_l_original(self):
        row = self.demande()
        self.desk.db.execute("UPDATE prepared_items5689 SET protected='signe' WHERE id=?", (row['id'],))
        self.desk.db.commit()
        original = self.nc.files[row['path']]
        out = self.js(self.api('livrables/%s/copie-travail' % row['document_id'], body={'cle': 'cle-copie-1'}))
        self.assertNotEqual(out['document_id'], row['document_id'])
        copy = liv.charger(self.desk, out['document_id'])
        self.assertEqual((copy['protected'], copy['production_state']), ('', 'verified'))
        self.assertEqual(self.nc.files[row['path']], original)
        self.assertEqual(self.js(self.api('livrables/%s/copie-travail' % row['document_id'], body={'cle': 'cle-copie-1'}))['document_id'], out['document_id'])
        self.assertEqual(len(self.nc.ecritures), 1)

    def test_LIV05_pdf_non_modifiable_oriente_vers_une_copie(self):
        res = liv.reserve(self.desk, 'essai', 'pdf', 1, matter=MID, title='Pièce', path='/Dossiers/DEMO/P.pdf')
        self.nc.put_file('/Dossiers/DEMO/P.pdf', b'%PDF-1.4 x')
        row = liv.constater(self.desk, res, self.nc, '/Dossiers/DEMO/P.pdf')
        out = self.api('documents/%s/proposition' % row['document_id'], body={'cle': 'cle-pdf-0001', 'operations': [{'ancre': 'x', 'texte': 'y'}], 'version_id': row['version_id'], 'sha256': row['sha256']})
        self.assertEqual(self.js(out)['error'], 'format_non_modifiable')

    def test_LIV03_assistant_propose_l_avocat_decide(self):
        row = self.base()
        p = self.proposer(row)
        out = self.api('documents/propositions/%s/decider' % p['id'], body={'decision': 'accepter'}, who=ASSISTANT)
        self.assertEqual(out['status'], '404 Not Found')          # route refusée au rôle
        self.assertEqual(self.nc.ecritures, [])

    def test_LIV03_ancre_ambigue_refusee(self):
        row = self.demande(raw=texte_docx('Même phrase.', 'Même phrase.'))
        out = self.api('documents/%s/proposition' % row['document_id'], body={'cle': 'cle-ambigue', 'operations': [{'ancre': 'Même phrase.', 'texte': 'Autre'}], 'version_id': row['version_id'], 'sha256': row['sha256']})
        self.assertEqual(self.js(out)['error'], 'ancre_word_ambigue')

    def test_LIV03_texte_a_cheval_sur_plusieurs_elements_style_conserve(self):
        body = '<w:p><w:pPr><w:pStyle w:val="Normal"/></w:pPr><w:r><w:t>Le prix </w:t></w:r><w:r><w:t>est de mille euros.</w:t></w:r></w:p>'
        raw = _archive('<w:document xmlns:w="%s"><w:body>%s</w:body></w:document>' % (W, body))
        new = patches.appliquer(raw, patches._normaliser([{'ancre': 'Le prix est de mille euros.', 'attendu': 'mille', 'texte': 'deux mille'}]))
        text = zipfile.ZipFile(io.BytesIO(new)).read('word/document.xml').decode()
        self.assertIn('deux mille euros', text)
        self.assertIn('pStyle', text)


class Controles(Base):
    def row(self, raw, **result):
        return self.demande(raw=raw, **result)

    def states(self, check):
        return {c['check_id']: c['state'] for c in check['checks']}

    def test_IMP05A_modele_complexe_exploitable_apres_generation_et_reouverture(self):
        raw = docx_complexe()
        new = patches.appliquer(raw, patches._normaliser([{'ancre': 'Le prix est fixé par les parties ZORVAL et KERVILAC.', 'attendu': 'fixé', 'texte': 'arrêté'}]))
        for name in ('word/media/logo.png', 'word/header1.xml', 'word/numbering.xml', 'word/styles.xml'):
            self.assertEqual(zipfile.ZipFile(io.BytesIO(raw)).read(name), zipfile.ZipFile(io.BytesIO(new)).read(name), name)
        self.assertEqual(verification.archive(new), ([], 0))
        problems, facts = verification.structure(new)
        self.assertEqual(problems, [])
        self.assertEqual((facts['tableaux'], facts['images'], facts['en_tetes'], facts['numerotations'], facts['renvois'], facts['signets']), (1, 1, 1, 1, 1, 1))
        row = self.row(new)
        check = verification.controler(self.desk, row, client=self.nc)
        states = self.states(check)
        self.assertEqual((states['format.archive'], states['format.structure'], states['sources.renvois']), ('passed', 'passed', 'passed'))
        self.assertEqual(states['format.rendu'], 'pending')           # pas de moteur qualifié ici : rien n'est prétendu
        self.assertTrue(check['ready_for_review'])

    def test_IMP05A_paragraphe_reecrit_par_render_docx_garde_le_reste_de_l_archive(self):
        raw = docx_complexe()
        new = patches.appliquer(raw, patches._normaliser([{'operation': 'insert_after', 'ancre': 'Le prix est fixé par les parties ZORVAL et KERVILAC.', 'texte': 'Paragraphe ajouté.'}]))
        self.assertEqual(verification.structure(new)[0], [])
        self.assertIn('Paragraphe ajouté.', verification.paragraphes(new))

    def test_IMP05A_structure_cassee_detectee(self):
        raw = docx_complexe().replace(b'', b'')
        broken = _archive('<w:document xmlns:w="%s"><w:body><w:p><w:pPr><w:numPr><w:numId w:val="9"/></w:numPr><w:pStyle w:val="Absent"/></w:pPr><w:r><w:fldChar w:fldCharType="begin"/></w:r><w:r><w:instrText> REF nulle </w:instrText></w:r></w:p></w:body></w:document>' % W)
        problems, _ = verification.structure(broken)
        self.assertEqual(len(problems), 4)                 # style, numérotation, signet, champ non fermé
        self.assertTrue(raw)

    def test_IMP05A_lien_interne_casse_et_archive_abimee(self):
        raw = _archive('<w:document xmlns:w="%s"><w:body/></w:document>' % W, rels='<Relationship Id="r1" Type="x" Target="media/absent.png"/>')
        problems, _ = verification.archive(raw)
        self.assertTrue(any('lien interne cassé' in p for p in problems))
        self.assertEqual(verification.archive(b'pas une archive')[0], ['archive illisible'])
        row = self.row(b'pas une archive')
        check = verification.controler(self.desk, row, client=self.nc)
        self.assertEqual(self.states(check)['format.archive'], 'failed')

    def test_IMP05D_citation_introuvable_non_verifiee_sans_validation_induite(self):
        raw = texte_docx('Vu l’article L. 145-41 du code de commerce.', 'Cass. civ. 3e, 7 mars 2024, n° 22-12.345.')
        row = self.row(raw)
        check = verification.controler(self.desk, row, client=self.nc)
        states = self.states(check)
        self.assertEqual(states['format.archive'], 'passed')              # le DOCX est parfait…
        self.assertEqual(states['sources.citations'], 'partial')          # … et les citations restent non vérifiées
        citations = next(c for c in check['checks'] if c['check_id'] == 'sources.citations')
        self.assertEqual({c['statut'] for c in citations['details']['citations']}, {verification.NON_VERIFIEE})
        self.assertEqual(len(citations['details']['citations']), 2)
        self.assertTrue(all('Non vérifiée' in r for r in citations['reservations'][:2]))
        self.assertEqual(check['human_review'], 'not_reviewed')
        self.assertNotIn('valide', json.dumps({k: v for k, v in check.items() if k != 'checks'}))

    def test_IMP05D_source_consultee_reste_a_apprecier_en_contexte(self):
        raw = texte_docx('Vu l’article L. 145-41 du code de commerce.')
        row = self.row(raw, recherche_juridique={'sources': [{'id': 'L1', 'statut': 'verifiee', 'titre': 'Code de commerce', 'reference': 'article L. 145-41 du code de commerce'}]})
        check = verification.controler(self.desk, row, client=self.nc)
        citations = next(c for c in check['checks'] if c['check_id'] == 'sources.citations')
        self.assertEqual(citations['details']['citations'][0]['statut'], 'Source consultée')
        self.assertEqual(citations['state'], 'partial')                   # jamais « réussi » sur une simple correspondance
        self.assertTrue(any('soutien de chaque affirmation' in r for r in citations['reservations']))

    def test_IMP05D_reference_signalee_par_le_producteur_reprise(self):
        row = self.row(texte_docx('Texte sans citation.'), recherche_juridique={'references_a_verifier': ['CEDH, arrêt fictif'], 'sources': []})
        check = verification.controler(self.desk, row, client=self.nc)
        self.assertEqual(next(c for c in check['checks'] if c['check_id'] == 'sources.citations')['details']['citations'][0]['reference'], 'CEDH, arrêt fictif')

    def test_controle_champs_non_renseignes(self):
        check = verification.controler(self.desk, self.row(texte_docx('Le preneur {{nom_preneur}} verse [à compléter] et signe ______.')), client=self.nc)
        item = next(c for c in check['checks'] if c['check_id'] == 'sources.champs')
        self.assertEqual((item['state'], item['details']['nombre']), ('failed', 3))

    def test_controle_pieces_absentes_du_registre(self):
        from agent.legal_research import ensure_schema
        ensure_schema(self.desk)
        check = verification.controler(self.desk, self.row(texte_docx('Comme le montre la pièce n° 1.', 'Voir pièce n° 7.')), client=self.nc)
        self.assertEqual(self.states(check)['sources.pieces'], 'partial')       # registre vide : non vérifiable, dit tel quel
        for n in range(3):
            values = {'matter': MID, 'path': '/Dossiers/DEMO/P%d.pdf' % n, 'sha256': 'a' * 64, 'bytes': 1, 'status': 'present'}
            for r in self.desk.db.execute('PRAGMA table_info(exhibit_registry_v240)').fetchall():
                if r[3] and r[4] is None and r[1] not in values:
                    values[r[1]] = 0 if 'INT' in r[2].upper() else 'x%d' % n
            self.desk.db.execute('INSERT INTO exhibit_registry_v240(%s) VALUES(%s)' % (','.join(values), ','.join('?' for _ in values)), list(values.values()))
        self.desk.db.commit()
        check = verification.controler(self.desk, liv.charger(self.desk, check['document_id']), client=self.nc)
        self.assertEqual(self.states(check)['sources.pieces'], 'failed')

    def test_controle_parties_et_montants(self):
        attendus = {'parties': ['DURAND', 'MARTIN'], 'montants': ['1500']}
        check = verification.controler(self.desk, self.row(texte_docx('Entre DURANT et MARTIN, pour 1 800 €.')), client=self.nc, attendus=attendus)
        item = next(c for c in check['checks'] if c['check_id'] == 'sources.parties_montants')
        self.assertEqual(item['state'], 'failed')
        self.assertTrue(any('DURANT' in r for r in item['reservations']))
        self.assertTrue(any('1800' in r for r in item['reservations']))
        ok = verification.controler(self.desk, self.row(texte_docx('Entre DURAND et MARTIN, pour 1 500,00 euros.')), client=self.nc, attendus=attendus)
        self.assertEqual(self.states(ok)['sources.parties_montants'], 'passed')

    def test_controle_renvoi_interne(self):
        check = verification.controler(self.desk, self.row(texte_docx('Article 1 Objet', 'Article 2 Prix', 'Selon l’article 9 ci-dessus.')), client=self.nc)
        self.assertEqual(self.states(check)['sources.renvois'], 'failed')

    def test_quatre_controles_separes_et_idempotents(self):
        row = self.row(texte_docx('Texte.'))
        first = verification.controler(self.desk, row, client=self.nc)
        count = self.desk.db.execute('SELECT COUNT(*) FROM document_checks5689').fetchone()[0]
        events = self.desk.db.execute("SELECT COUNT(*) FROM audit WHERE action='document_controle5689'").fetchone()[0]
        second = verification.controler(self.desk, row, client=self.nc)
        self.assertEqual(first, second)
        self.assertEqual(self.desk.db.execute('SELECT COUNT(*) FROM document_checks5689').fetchone()[0], count)
        self.assertEqual(self.desk.db.execute("SELECT COUNT(*) FROM audit WHERE action='document_controle5689'").fetchone()[0], events)
        self.assertEqual({c['domain'] for c in first['checks']}, {'storage', 'format', 'sources', 'human_review'})
        for key in ('document_id', 'version_id', 'sha256', 'checks', 'required_check_ids', 'ready_for_review', 'human_review'):
            self.assertIn(key, first)

    def test_reouverture_reussie_n_est_pas_une_relecture(self):
        row = self.row(texte_docx('Texte.'))
        check = verification.controler(self.desk, row, client=self.nc)
        self.assertTrue(check['ready_for_review'])
        self.assertEqual(check['human_review'], 'not_reviewed')
        self.assertEqual(self.states(check)['human.review'], 'pending')
        self.assertEqual(liv.charger(self.desk, row['document_id'])['review_state'], 'to_review')
        out = liv.relire(self.desk, row['document_id'], row['version_id'], 'modifications', *AVOCAT, note='Revoir le prix')
        check = verification.vue(self.desk, out)
        self.assertEqual(check['human_review'], 'changes_requested')
        self.assertEqual(self.states(check)['human.review'], 'partial')

    def test_invalidation_limitee_aux_preuves_concernees(self):
        row = self.row(texte_docx('Vu l’article L. 145-41 du code de commerce.'))
        verification.controler(self.desk, row, client=self.nc)
        verification.invalider(self.desk, row['document_id'], row['version_id'], 'sources')
        states = self.states(verification.vue(self.desk, row))
        self.assertEqual((states['sources.citations'], states['sources.pieces']), ('pending', 'pending'))
        self.assertEqual((states['storage.file'], states['format.archive'], states['sources.champs']), ('passed', 'passed', 'passed'))
        verification.controler(self.desk, row, client=self.nc)
        verification.invalider(self.desk, row['document_id'], row['version_id'], 'stockage')
        states = self.states(verification.vue(self.desk, row))
        self.assertEqual((states['storage.file'], states['format.archive']), ('pending', 'passed'))

    def test_format_non_word_controles_sans_objet(self):
        res = liv.reserve(self.desk, 'essai', 't', 1, matter=MID, title='Note', path='/Dossiers/DEMO/N.txt')
        self.nc.put_file('/Dossiers/DEMO/N.txt', 'Texte {{a}}'.encode())
        row = liv.constater(self.desk, res, self.nc, '/Dossiers/DEMO/N.txt')
        check = verification.controler(self.desk, row, client=self.nc)
        states = self.states(check)
        self.assertEqual((states['format.archive'], states['sources.champs']), ('not_applicable', 'failed'))
        self.assertTrue(check['ready_for_review'])

    def test_route_verification_et_controle_par_travail(self):
        row = self.row(texte_docx('Texte.'))
        out = self.js(self.api('documents/%s/controler' % row['document_id'], body={}))
        self.assertTrue(out['job_id'])
        verification.travail(self.desk, {'document_id': row['document_id'], 'version_id': row['version_id']}, self.nc)
        self.assertTrue(self.js(self.api('documents/%s/verification' % row['document_id']))['ready_for_review'])


class Modeles(t84.Base):
    def test_modele_etats_effet_reel_sur_la_selection(self):
        ident = next(iter(t84.lib.BUILTIN))
        self.assertEqual(modeles5684.selectionner(self.desk, ident)['state'], 'draft')          # à la main : utilisable, signalé
        with self.assertRaisesRegex(Stop, 'modele_non_valide'):
            modeles5684.selectionner(self.desk, ident, autonome=True)
        modeles5684.move(self.desk, {'modele': ident, 'etat': 'review'}, 'avocate', 'avocat')
        with self.assertRaisesRegex(Stop, 'modele_non_valide'):
            modeles5684.selectionner(self.desk, ident, autonome=True)
        modeles5684.move(self.desk, {'modele': ident, 'etat': 'validated'}, 'avocate', 'avocat')
        self.assertEqual(modeles5684.selectionner(self.desk, ident, autonome=True)['state'], 'validated')
        modeles5684.move(self.desk, {'modele': ident, 'etat': 'withdrawn'}, 'avocate', 'avocat')
        for autonome in (False, True):
            with self.assertRaisesRegex(Stop, 'modele_retire'):
                modeles5684.selectionner(self.desk, ident, autonome=autonome)

    def test_modele_fiche_champs_obligatoires_et_migration_additive(self):
        ident = next(iter(t84.lib.BUILTIN))
        modeles5684.renseigner(self.desk, {'modele': ident, 'type': 'contrat', 'langue': 'fr', 'domaine': 'baux', 'champs_obligatoires': 'cedant, prix'}, 'avocate')
        row = modeles5684.current(self.desk, ident)
        self.assertEqual((row['document_type'], row['language'], row['domain'], json.loads(row['required_fields'])), ('contrat', 'fr', 'baux', ['cedant', 'prix']))
        self.assertEqual(modeles5684.champs_manquants(self.desk, ident, {'cedant': 'ZORVAL'}), ['prix'])
        with self.assertRaisesRegex(Stop, 'modele_hors_parcours'):
            modeles5684.selectionner(self.desk, ident, document_type='assignation')
        modeles5684.schema(self.desk)                 # migration rejouée : idempotente


class Gardes(Base):
    """Chaque garde-fou est retiré en mémoire : le résultat interdit doit alors apparaître. Rien n'est modifié dans l'arbre."""

    def base(self):
        return self.demande(raw=texte_docx('Le prix est de mille euros.', 'Fin.'))

    def test_garde_version_de_base(self):
        row = self.base()
        stale = {'cle': 'cle-garde-1', 'operations': [{'ancre': 'Fin.', 'texte': 'Terme.'}], 'version_id': 'sha256:' + 'f' * 64, 'sha256': 'f' * 64}
        self.assertEqual(self.api('documents/%s/proposition' % row['document_id'], body=stale)['status'], '409 Conflict')
        with patch.object(patches, '_base_conforme'):
            self.assertEqual(self.api('documents/%s/proposition' % row['document_id'], body=stale)['status'], '200 OK')

    def test_garde_original_protege(self):
        row = self.base()
        self.desk.db.execute("UPDATE prepared_items5689 SET protected='signe' WHERE id=?", (row['id'],))
        self.desk.db.commit()
        body = {'cle': 'cle-garde-2', 'operations': [{'ancre': 'Fin.', 'texte': 'Terme.'}], 'version_id': row['version_id'], 'sha256': row['sha256']}
        self.assertEqual(self.api('documents/%s/proposition' % row['document_id'], body=body)['status'], '403 Forbidden')
        with patch.object(patches, '_droit_modifier'):
            self.assertEqual(self.api('documents/%s/proposition' % row['document_id'], body=body)['status'], '200 OK')

    def test_garde_html_assaini(self):
        raw = b'Subject: x\r\nContent-Type: text/html; charset=utf-8\r\n\r\n<script>alert(1)</script><img src="https://suivi.example.test/p.png">'
        res = liv.reserve(self.desk, 'essai', 'g', 1, matter=MID, title='M', path='/Dossiers/DEMO/M.eml')
        self.nc.put_file('/Dossiers/DEMO/M.eml', raw)
        row = liv.constater(self.desk, res, self.nc, '/Dossiers/DEMO/M.eml')
        self.assertNotIn('<script', apercu.apercu(self.desk, row)['html'])
        with patch.object(apercu, 'assainir', side_effect=lambda html: (html, 0, 0)):
            self.assertIn('<script', apercu.apercu(self.desk, row)['html'])

    def test_garde_droit_d_export(self):
        row = self.base()
        rights = {**membres5650.ROLE_DEFAULTS['assistant'], 'exporter': False}
        with patch('agent.membres5650.effective_rights', return_value=rights):
            self.assertEqual(self.api('livrables/%s/telecharger' % row['document_id'], who=ASSISTANT)['status'], '403 Forbidden')
            with patch.object(web, '_exiger_export'):
                self.assertEqual(self.api('livrables/%s/telecharger' % row['document_id'], who=ASSISTANT)['status'], '200 OK')

    def test_garde_dossier_reserve(self):
        row = self.base()
        real = liv.charger
        with patch('agent.membres5650.hidden_for', return_value=frozenset({MID})):
            self.assertEqual(self.api('livrables/' + row['document_id'], who=ASSISTANT)['status'], '403 Forbidden')
            with patch.object(liv, 'charger', side_effect=lambda desk, ident, hidden=frozenset(), version_id=None, droits=None: real(desk, ident, frozenset(), version_id, droits)):
                self.assertEqual(self.api('livrables/' + row['document_id'], who=ASSISTANT)['status'], '200 OK')

    def test_garde_sortie_verifiee_avant_exporte(self):
        row = self.base()
        body = {'format': 'pdf', 'destination': 'dest-exports', 'cle': 'cle-garde-3'}
        with patch('agent.pieces510.docx_to_pdf', return_value=b'%PDF-faux'):
            out = self.js(self.api('livrables/%s/exporter' % row['document_id'], body=body))
            with self.assertRaises(Stop):
                export.executer(self.desk, {'export': out['id']}, self.nc)
            self.assertEqual(self.nc.ecritures, [])
            with patch.object(export, 'verifier_sortie', return_value=True):
                export.executer(self.desk, {'export': out['id']}, self.nc)
        self.assertEqual(len(self.nc.ecritures), 1)

    def test_garde_migration_sans_approbation_implicite(self):
        """Sans le contrôle de date et d'empreinte, l'ancien marqueur approuverait une version qu'il n'a jamais vue."""
        now = datetime.now(timezone.utc)
        path = '/Dossiers/DEMO/PROJETS/Contrat DURAND.docx'
        self.desk.db.execute('INSERT INTO cockpit530_reviewed VALUES(?,?,?,?)', ('doc:' + digest(path)[:24], 'relu', 'x', (now - timedelta(days=2)).isoformat()))
        self.desk.db.commit()
        with patch.object(liv, '_instant', side_effect=lambda value: datetime(1970, 1, 1, tzinfo=timezone.utc)):
            self.demande(when=now.isoformat())
        self.assertEqual(self.items(), [])       # mutation : tout paraît antérieur, le document disparaît comme avant la correction
        self.desk.db.execute('DELETE FROM prepared_reviews5689')
        self.desk.db.execute('DELETE FROM prepared_legacy_marks5689')
        self.desk.db.execute("UPDATE prepared_items5689 SET review_state='to_review'")
        self.desk.db.commit()
        self.assertEqual(len(self.items()), 1)   # contrôle en place : la version postérieure reste à relire

    def test_garde_redirection_ouverte(self):
        self.assertEqual(web._retour_sur('//exemple.test', '/p'), '/p/livrables')
        with patch.object(web, 'urlsplit', side_effect=lambda v: type('U', (), {'scheme': '', 'netloc': ''})):
            self.assertEqual(web._retour_sur('//exemple.test', '/p'), '/p/livrables')     # le préfixe « // » est refusé même si l'analyse échouait


if __name__ == '__main__':
    unittest.main()
