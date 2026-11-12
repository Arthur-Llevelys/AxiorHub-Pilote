"""5.6.97, lot documents-mission : Nouveau document (§ 13), ouverture de mission (§ 16), portefeuille et prochaine action (§ 17).

Scénarios de recette (identifiants repris dans le nom des tests) :
- § 13 : R13a document vierge ouvert, modifié, comparé, téléchargé, réouvert ; R13b modèle du cabinet (modèle et version gardés) ; R13c « Préparer
  avec l'IA » = demande en file, aucun modèle appelé, IA en pause dite ; R13d deux éditeurs : conflit lisible, rien d'écrasé ; R13e source privée
  révoquée (modèle retiré, dossier réservé) ; R13f bordereau : versions des pièces gelées ; R13g import contrôlé ; R13h un seul effet (double clic,
  rejeu, coupure avant et après l'écriture) ; R13i styles et numérotation conservés par l'éditeur simple.
- § 16 : R16a même courriel et double clic : une demande et un dossier ; R16b partie ajoutée après contrôle : « À décider », contrôle à refaire ;
  R16c refus : aucun dossier ; R16d « signée » exige le fichier signé, renommer le client ne le change pas ; R16e appel, rendez-vous, courriel ;
  R16f rapprochement dans un dossier réservé : compté, jamais décrit ; R16g conversion rejouée ou reprise : le même dossier.
- § 17 : R17a réassigner (historique) ; R17b action terminée : signalée, suivante suggérée ; R17c fichier reçu pendant l'attente : signalé, date
  d'attente inchangée ; R17d date de recontrôle dépassée et seuil ; R17e attente fermée : listes recalculées sans doublon ; R17f dossier masqué :
  aucun compteur changé ; R17g échéance confirmée visible et prioritaire ; R17h version périmée : 409.
- C01 : commandes de l'avocat sans IA (fournisseurs bloqués, pause globale active). M01-M04 : contre-épreuves par mutation en mémoire des gardes.

Données fictives (DURAND, MARTIN, ZORVAL, DOS-001…, example.test). Stockage : dossier local temporaire (aucun Nextcloud). Tout appel au modèle et
toute connexion réseau font échouer les tests.
"""
import base64
from datetime import date, datetime, timedelta, timezone
import io
import json
from pathlib import Path
import socket
import tempfile
import unittest
from unittest.mock import patch
import zipfile

from agent import acces5697, documents5697 as docs, mission5697 as mis, portefeuille5697 as pf
from agent.common import Stop, private_json
from agent.desk import Desk

AVOCATE = ('avocate@example.test', 'avocat')
ASSISTANT = ('assistant@example.test', 'assistant')
AUTRE = ('autre@example.test', 'avocat')
ADMIN = ('cabinet', 'administrateur')


def docx_style(*paragraphes):
    """DOCX avec un style de paragraphe et une numérotation sur chaque paragraphe, plus un tableau et un pied de page."""
    w = 'http://schemas.openxmlformats.org/wordprocessingml/2006/main'
    corps = ''.join('<w:p><w:pPr><w:pStyle w:val="Article"/><w:numPr><w:ilvl w:val="0"/><w:numId w:val="3"/></w:numPr></w:pPr>'
                    '<w:r><w:rPr><w:b/></w:rPr><w:t xml:space="preserve">%s</w:t></w:r></w:p>' % p for p in paragraphes)
    corps += '<w:tbl><w:tr><w:tc><w:p><w:r><w:t>Cellule fixe</w:t></w:r></w:p></w:tc></w:tr></w:tbl>'
    out = io.BytesIO()
    with zipfile.ZipFile(out, 'w') as z:
        z.writestr('[Content_Types].xml', '<Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types"/>')
        z.writestr('word/document.xml', '<w:document xmlns:w="%s"><w:body>%s<w:sectPr/></w:body></w:document>' % (w, corps))
        z.writestr('word/footer1.xml', '<w:ftr xmlns:w="%s"><w:p><w:r><w:t>Pied du cabinet</w:t></w:r></w:p></w:ftr>' % w)
        z.writestr('word/styles.xml', '<w:styles xmlns:w="%s"><w:style w:styleId="Article"/></w:styles>' % w)
    return out.getvalue()


class Base(unittest.TestCase):
    def setUp(self):
        from sqlite_cleanup import close_sqlite_under
        self.t = tempfile.TemporaryDirectory()
        self.addCleanup(self.t.cleanup)
        self.addCleanup(close_sqlite_under, self.t.name)
        base = Path(self.t.name)
        (base / 'state').mkdir()
        self.stockage = base / 'stockage'
        noms = ('DURAND', 'MARTIN', 'ZORVAL')
        self.matters = []
        for i, nom in enumerate(noms):
            ident = 'DOS-%03d' % (i + 1)
            path = '/Dossiers/%s - %s' % (ident, nom)
            (self.stockage / path.lstrip('/') / 'CORRESPONDANCES').mkdir(parents=True)
            self.matters.append({'id': ident, 'client_name': nom, 'path': path, 'references': [ident], 'aliases': [nom], 'correspondents': []})
        private_json(base / 'matters.json', self.matters)
        self.c = {'mode': 'drafts', 'state_dir': str(base / 'state'), 'matters_file': str(base / 'matters.json'), 'mail': {'own_addresses': ['cabinet@example.test']},
                  'nextcloud': {'url': 'https://cloud.example.test', 'local_path': str(self.stockage), 'roots': ['/Dossiers'], 'matter_roots': ['/Dossiers']},
                  'documents': {}, 'ollama': {'model': 'interdit'}, 'calendar': {'urls': [], 'timezone': 'Europe/Paris'}}
        self.d = Desk(self.c)
        self.addCleanup(self.d.db.close)
        for cible in ('agent.model.Model.ask', 'agent.model.Model.complete'):
            p = patch(cible, side_effect=AssertionError('appel au modèle interdit'))
            p.start()
            self.addCleanup(p.stop)
        p = patch.object(socket, 'create_connection', side_effect=AssertionError('connexion réseau interdite'))
        p.start()
        self.addCleanup(p.stop)
        from agent import membres5650
        membres5650.directory(self.d)
        for owner, role in (AVOCATE, ASSISTANT, AUTRE):
            self.d.db.execute('INSERT OR IGNORE INTO membres5650(owner,name,role,first_seen,last_seen) VALUES(?,?,?,?,?)', (owner, owner.split('@')[0], role, 'x', 'x'))
        self.d.db.commit()
        membres5650.set_reserve(self.c['state_dir'], 'DOS-003', [AUTRE[0]])     # DOS-003 réservé à un autre avocat

    def fichiers(self, dossier='DOS-001'):
        m = next(x for x in self.matters if x['id'] == dossier)
        return sorted(str(p.relative_to(self.stockage)).replace('\\', '/') for p in (self.stockage / m['path'].lstrip('/')).rglob('*') if p.is_file())


# ====================================================================================== § 13
class Documents(Base):
    def test_R13a_vierge_ouvert_modifie_compare_telecharge_reouvert(self):
        out = docs.creer(self.d, AVOCATE, {'dossier': 'DOS-001', 'mode': 'vierge', 'type': 'courrier', 'titre': 'Courrier à MARTIN',
                                           'valeurs': {'destinataire': 'Maître MARTIN'}}, 'cle-vierge-0001')
        self.assertEqual(out['version'], 1)
        self.assertIn('CORRESPONDANCES/Courrier à MARTIN.docx', out['chemin'])
        vue = docs.ouvrir(self.d, AVOCATE, out['document_id'])
        self.assertTrue(vue['editable'])
        self.assertIn('Destinataire : Maître MARTIN', vue['paragraphes'])
        self.assertIn('Objet : [À COMPLÉTER : objet]', vue['paragraphes'])       # champ non établi visible
        lignes = [x.replace('[À COMPLÉTER : objet]', 'Relance amiable') for x in vue['paragraphes']]
        v2 = docs.enregistrer(self.d, AVOCATE, out['document_id'], 1, 'cle-version-0002', lignes=lignes)
        self.assertEqual(v2['version'], 2)
        diff = docs.comparer(self.d, AVOCATE, out['document_id'], 1, 2)
        self.assertEqual([x['texte'] for x in diff['lignes'] if x['op'] == 'ajout'], ['Objet : Relance amiable'])
        nom, mime, octets = docs.fichier(self.d, AVOCATE, out['document_id'])
        self.assertEqual(nom, 'Courrier à MARTIN.docx')
        self.assertIn('Objet : Relance amiable', docs.paragraphes_de(octets))
        self.assertEqual(docs.ouvrir(self.d, AVOCATE, out['document_id'])['version'], 2)
        reg = self.d.db.execute('SELECT version_number,lifecycle FROM prepared_items5689 WHERE document_id=? ORDER BY version_number', (out['document_id'],)).fetchall()
        self.assertEqual([tuple(r) for r in reg], [(1, 'replaced'), (2, 'current')])     # une seule ligne de registre par version, aucun second registre

    def test_R13b_modele_du_cabinet_garde_modele_et_version(self):
        from agent import bibliotheque5639
        modele = next(t for t in bibliotheque5639.catalog(self.d) if t.get('langue') == 'fr')
        out = docs.creer(self.d, AVOCATE, {'dossier': 'DOS-001', 'mode': 'modele', 'type': 'libre', 'titre': 'Acte depuis modèle', 'modele': modele['id']}, 'cle-modele-0001')
        row = self.d.db.execute('SELECT provenance,production_state FROM prepared_items5689 WHERE document_id=?', (out['document_id'],)).fetchone()
        prov = json.loads(row['provenance'])
        self.assertEqual((prov['mode'], prov['modele']), ('modele', modele['id']))
        self.assertGreaterEqual(prov['modele_version'], 1)
        self.assertEqual(row['production_state'], 'verified')

    def test_R13c_preparer_avec_ia_met_en_file_sans_modele_et_dit_la_pause(self):
        from agent.arret5643 import PAUSE_GLOBAL
        self.d.setting(PAUSE_GLOBAL, True)
        out = docs.creer(self.d, AVOCATE, {'dossier': 'DOS-001', 'mode': 'ia', 'type': 'conclusions', 'consigne': 'Conclusions en réponse sur le bail DURAND'}, 'cle-ia-00001')
        self.assertTrue(out['ia_en_pause'])
        self.assertIn('pause', out['message'])
        again = docs.creer(self.d, AVOCATE, {'dossier': 'DOS-001', 'mode': 'ia', 'type': 'conclusions', 'consigne': 'Conclusions en réponse sur le bail DURAND'}, 'cle-ia-00001')
        self.assertEqual(again['demande'], out['demande'])
        self.assertEqual(self.d.db.execute("SELECT COUNT(*) FROM jobs WHERE kind='docrequest520'").fetchone()[0], 1)
        with self.assertRaises(Stop):     # le bordereau se prépare à la main, à partir des pièces : pas de demande à l'IA
            docs.creer(self.d, AVOCATE, {'dossier': 'DOS-001', 'mode': 'ia', 'type': 'bordereau', 'consigne': 'Bordereau des pièces'}, 'cle-ia-00002')

    def test_R13d_deux_editeurs_conflit_lisible_sans_ecrasement(self):
        out = docs.creer(self.d, AVOCATE, {'dossier': 'DOS-001', 'mode': 'vierge', 'type': 'libre', 'titre': 'Note DURAND'}, 'cle-conflit-01')
        lu = docs.ouvrir(self.d, AVOCATE, out['document_id'])           # deux personnes ouvrent la version 1
        lu2 = docs.ouvrir(self.d, ASSISTANT, out['document_id'])
        docs.enregistrer(self.d, AVOCATE, out['document_id'], lu['version'], 'cle-conflit-02', lignes=lu['paragraphes'] + ['Texte de l’avocate'])
        with self.assertRaises(docs.Conflit) as ctx:
            docs.enregistrer(self.d, ASSISTANT, out['document_id'], lu2['version'], 'cle-conflit-03', lignes=lu2['paragraphes'] + ['Texte de l’assistant'])
        self.assertEqual(str(ctx.exception), 'version_en_conflit')
        self.assertEqual(ctx.exception.detail['version_courante'], 2)
        self.assertIn('avocate@example.test', ctx.exception.detail['message'])
        self.assertIn('n’a pas été enregistré', ctx.exception.detail['message'])
        texte = docs.paragraphes_de(docs.fichier(self.d, AVOCATE, out['document_id'])[2])
        self.assertIn('Texte de l’avocate', texte)
        self.assertNotIn('Texte de l’assistant', texte)                  # rien d'écrasé
        copie = docs.copie(self.d, ASSISTANT, out['document_id'], lu2['paragraphes'] + ['Texte de l’assistant'], 'cle-conflit-04')
        self.assertIn('Texte de l’assistant', docs.paragraphes_de(docs.fichier(self.d, ASSISTANT, copie['document_id'])[2]))

    def test_R13d_modification_hors_axiorhub_detectee_par_etag(self):
        out = docs.creer(self.d, AVOCATE, {'dossier': 'DOS-001', 'mode': 'vierge', 'type': 'libre', 'titre': 'Note ZORVAL'}, 'cle-etag-0001')
        lu = docs.ouvrir(self.d, AVOCATE, out['document_id'])
        chemin = self.stockage / out['chemin'].lstrip('/')
        chemin.write_bytes(docx_style('Modifié dans le stockage'))      # autre éditeur, hors registre
        with self.assertRaises(docs.Conflit):
            docs.enregistrer(self.d, AVOCATE, out['document_id'], lu['version'], 'cle-etag-0002', lignes=['Ma version'])
        self.assertEqual(docs.paragraphes_de(chemin.read_bytes())[0], 'Modifié dans le stockage')

    def test_R13e_source_revoquee_bloque_la_reutilisation(self):
        from agent import bibliotheque5639, modeles5684
        modele = next(t for t in bibliotheque5639.catalog(self.d) if t.get('langue') == 'fr')
        modeles5684.current(self.d, modele['id'])
        self.d.db.execute("UPDATE template_versions5684 SET state='withdrawn' WHERE template_id=?", (modele['id'],))
        self.d.db.commit()
        with self.assertRaises(Stop) as ctx:
            docs.creer(self.d, AVOCATE, {'dossier': 'DOS-001', 'mode': 'modele', 'type': 'libre', 'modele': modele['id']}, 'cle-retire-01')
        self.assertEqual(str(ctx.exception), 'modele_retire')
        with self.assertRaises(Stop) as ctx:      # dossier réservé à un autre : réponse neutre, aucun fichier
            docs.creer(self.d, AVOCATE, {'dossier': 'DOS-003', 'mode': 'vierge', 'type': 'libre'}, 'cle-reserve-1')
        self.assertEqual(str(ctx.exception), 'introuvable')
        self.assertEqual(self.fichiers('DOS-003'), [])
        out = docs.creer(self.d, AUTRE, {'dossier': 'DOS-003', 'mode': 'vierge', 'type': 'libre', 'titre': 'Note réservée'}, 'cle-reserve-2')
        for f in (docs.ouvrir, docs.fichier):
            with self.assertRaises(Stop) as ctx:
                f(self.d, AVOCATE, out['document_id'])
            self.assertEqual(str(ctx.exception), 'introuvable')

    def test_R13f_bordereau_gele_les_versions_des_pieces(self):
        from agent import contentieux5689
        contentieux5689.schema(self.d)
        now = datetime.now(timezone.utc).isoformat()
        for i, (num, titre, sha) in enumerate((('1', 'Bail commercial', 'a' * 64), ('2', 'Commandement de payer', 'b' * 64))):
            self.d.db.execute("INSERT INTO contentieux_exhibits5689(id,matter,cle,cote,partie,numero,numero_cle,titre,version,version_id,sha256,date_piece,statut,created,updated) "
                              "VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)", ('p%d' % i, 'DOS-001', 'k%d' % i, 'cabinet', '', num, num.zfill(4), titre, 2, 'v2-%d' % i, sha, '2024-01-0%d' % (i + 1), 'actif', now, now))
        self.d.db.commit()
        out = docs.creer(self.d, AVOCATE, {'dossier': 'DOS-001', 'mode': 'vierge', 'type': 'bordereau', 'titre': 'Bordereau DURAND', 'pieces': ['p1', 'p0']}, 'cle-bcp-00001')
        prov = json.loads(self.d.db.execute('SELECT provenance FROM prepared_items5689 WHERE document_id=?', (out['document_id'],)).fetchone()[0])
        self.assertEqual([(p['numero'], p['version'], p['sha256'][:1]) for p in prov['pieces']], [('1', 2, 'a'), ('2', 2, 'b')])
        self.d.db.execute("UPDATE contentieux_exhibits5689 SET version=3, sha256=? WHERE id='p0'", ('c' * 64,))   # la pièce évolue ensuite
        self.d.db.commit()
        prov = json.loads(self.d.db.execute('SELECT provenance FROM prepared_items5689 WHERE document_id=?', (out['document_id'],)).fetchone()[0])
        self.assertEqual(prov['pieces'][0]['version'], 2)                 # le bordereau garde la version utilisée
        self.assertIn('– Pièce n° 1 — Bail commercial — 01/01/2024 — version 2 (empreinte aaaaaaaaaaaa)', docs.ouvrir(self.d, AVOCATE, out['document_id'])['paragraphes'])

    def test_R13g_import_controle_format(self):
        brut = docx_style('Contrat importé')
        out = docs.creer(self.d, AVOCATE, {'dossier': 'DOS-002', 'mode': 'import', 'type': 'libre', 'nom_fichier': 'Contrat MARTIN.docx',
                                           'contenu': base64.b64encode(brut).decode()}, 'cle-import-001')
        self.assertEqual((self.stockage / out['chemin'].lstrip('/')).read_bytes(), brut)
        with self.assertRaises(Stop) as ctx:
            docs.creer(self.d, AVOCATE, {'dossier': 'DOS-002', 'mode': 'import', 'type': 'libre', 'nom_fichier': 'faux.pdf',
                                         'contenu': base64.b64encode(b'pas un pdf').decode()}, 'cle-import-002')
        self.assertEqual(str(ctx.exception), 'contenu_ne_correspond_pas_au_format')
        with self.assertRaises(Stop):
            docs.creer(self.d, AVOCATE, {'dossier': 'DOS-002', 'mode': 'import', 'type': 'libre', 'nom_fichier': 'script.exe', 'contenu': 'TVo='}, 'cle-import-003')

    def test_R13h_double_clic_rejeu_et_coupures_un_seul_fichier(self):
        data = {'dossier': 'DOS-002', 'mode': 'vierge', 'type': 'libre', 'titre': 'Note MARTIN'}
        a = docs.creer(self.d, AVOCATE, dict(data), 'cle-double-001')
        b = docs.creer(self.d, AVOCATE, dict(data), 'cle-double-001')
        self.assertTrue(b['rejoue'])
        self.assertEqual(a['document_id'], b['document_id'])
        self.assertEqual(self.fichiers('DOS-002'), ['Dossiers/DOS-002 - MARTIN/Note MARTIN.docx'])
        with self.assertRaises(Stop):     # même clé, autre contenu : refusé
            docs.creer(self.d, AVOCATE, dict(data, titre='Autre'), 'cle-double-001')
        # coupure APRÈS l'écriture, avant la réponse : le rejeu relit le stockage et ne redépose rien
        from agent.dav import LocalFolder
        vrai = LocalFolder.put_file

        def ecrit_puis_coupe(client, path, raw, content_type='application/octet-stream'):
            vrai(client, path, raw, content_type)
            raise Stop('connexion_http_indisponible')
        with patch.object(LocalFolder, 'put_file', ecrit_puis_coupe):
            with self.assertRaises(Stop):
                docs.creer(self.d, AVOCATE, dict(data, titre='Note coupée'), 'cle-coupe-0001')
        out = docs.creer(self.d, AVOCATE, dict(data, titre='Note coupée'), 'cle-coupe-0001')
        self.assertEqual(out['etat'], 'verified')
        self.assertEqual(sum(1 for f in self.fichiers('DOS-002') if 'Note coupée' in f), 1)
        # coupure AVANT l'écriture : la commande réservée reprend et dépose une fois
        with patch.object(LocalFolder, 'put_file', side_effect=Stop('connexion_http_indisponible')):
            with self.assertRaises(Stop):
                docs.creer(self.d, AVOCATE, dict(data, titre='Note avant'), 'cle-coupe-0002')
        self.assertFalse(any('Note avant' in f for f in self.fichiers('DOS-002')))
        docs.creer(self.d, AVOCATE, dict(data, titre='Note avant'), 'cle-coupe-0002')
        self.assertEqual(sum(1 for f in self.fichiers('DOS-002') if 'Note avant' in f), 1)
        # version : réponse perdue après l'écriture, le rejeu constate sans réécrire ni créer une troisième version
        lu = docs.ouvrir(self.d, AVOCATE, a['document_id'])
        vrai_replace = LocalFolder.replace_file

        def remplace_puis_coupe(client, path, raw, etag):
            vrai_replace(client, path, raw, etag)
            raise Stop('connexion_http_indisponible')
        with patch.object(LocalFolder, 'replace_file', remplace_puis_coupe):
            with self.assertRaises(Stop):
                docs.enregistrer(self.d, AVOCATE, a['document_id'], lu['version'], 'cle-coupe-0003', lignes=lu['paragraphes'] + ['Ajout'])
        v = docs.enregistrer(self.d, AVOCATE, a['document_id'], lu['version'], 'cle-coupe-0003', lignes=lu['paragraphes'] + ['Ajout'])
        self.assertEqual(v['version'], 2)
        self.assertEqual(self.d.db.execute('SELECT MAX(version_number) FROM prepared_items5689 WHERE document_id=?', (a['document_id'],)).fetchone()[0], 2)

    def test_R13i_editeur_simple_conserve_styles_tableau_et_pied(self):
        brut = docx_style('Article premier', 'Article deux')
        out = docs.creer(self.d, AVOCATE, {'dossier': 'DOS-001', 'mode': 'import', 'type': 'libre', 'nom_fichier': 'Statuts.docx', 'octets': brut}, 'cle-style-0001')
        lu = docs.ouvrir(self.d, AVOCATE, out['document_id'])
        self.assertEqual(lu['paragraphes'], ['Article premier', 'Article deux'])
        docs.enregistrer(self.d, AVOCATE, out['document_id'], 1, 'cle-style-0002', lignes=['Article premier', 'Article deux modifié', 'Article trois'])
        nouveau = docs.fichier(self.d, AVOCATE, out['document_id'])[2]
        with zipfile.ZipFile(io.BytesIO(nouveau)) as z:
            xml = z.read('word/document.xml').decode()
            self.assertEqual(z.read('word/footer1.xml'), zipfile.ZipFile(io.BytesIO(brut)).read('word/footer1.xml'))
        self.assertEqual(xml.count('<w:numId w:val="3"/>'), 3)            # numérotation gardée, y compris pour le paragraphe ajouté
        self.assertEqual(xml.count('<w:pStyle w:val="Article"/>'), 3)
        self.assertIn('Cellule fixe', xml)
        self.assertEqual(docs.paragraphes_de(nouveau), ['Article premier', 'Article deux modifié', 'Article trois'])


# ====================================================================================== § 16
class Missions(Base):
    def courriel(self, cle='a' * 64, sujet='Demande de conseil bail', expediteur='durand@example.test', dossier=''):
        self.d.db.execute('INSERT INTO work_items(mail_key,state,source_status,source_reason,matter,subject,sender,received,updated,resolved) VALUES(?,?,?,?,?,?,?,?,?,?)',
                          (cle, 'needs_action', 'review', '', dossier, sujet, expediteur, '2026-11-02T09:00:00+00:00', '2026-11-02T09:00:00+00:00', ''))
        self.d.db.commit()
        return cle

    def acceptee(self, cle='cle-dem-00001', parties=None):
        d = mis.creer(self.d, AVOCATE, {'origine': 'appel', 'objet': 'Bail commercial KERVILAC', 'client_nom': 'KERVILAC', 'client_etat': 'a_creer',
                                        'correspondant_role': 'client', 'responsable': AVOCATE[0], 'type_mission': 'conseil',
                                        'parties': parties if parties is not None else [{'nom': 'ORLANDEC', 'role': 'adverse'}]}, cle)
        d = mis.verifier_conflits(self.d, AVOCATE, d['id'], d['version'])
        return mis.decider(self.d, AVOCATE, d['id'], d['version'], 'accepter', 'Aucun conflit après examen du rapport.')

    def test_R16a_meme_courriel_et_double_clic_une_demande_et_un_dossier(self):
        cle = self.courriel()
        a = mis.creer(self.d, AVOCATE, {'origine': 'courriel', 'source_ref': cle}, 'cle-mail-00001')
        b = mis.creer(self.d, AVOCATE, {'origine': 'courriel', 'source_ref': cle}, 'cle-mail-00001')
        c = mis.creer(self.d, AVOCATE, {'origine': 'courriel', 'source_ref': cle}, 'cle-mail-00002')      # autre clic, même courriel
        self.assertEqual(len({a['id'], b['id'], c['id']}), 1)
        self.assertTrue(c.get('existante'))
        self.assertEqual(a['correspondant'], 'durand@example.test')
        self.assertEqual(a['client_nom'], '')                          # l'expéditeur ne devient pas le client
        d = mis.modifier(self.d, AVOCATE, a['id'], a['version'], {'client_nom': 'DURAND', 'correspondant_role': 'client', 'responsable': AVOCATE[0],
                                                                   'type_mission': 'conseil'})
        d = mis.verifier_conflits(self.d, AVOCATE, d['id'], d['version'])
        d = mis.decider(self.d, AVOCATE, d['id'], d['version'], 'accepter', 'Rapport examiné : client déjà connu, même côté.')
        x = mis.convertir(self.d, AVOCATE, d['id'], d['version'])
        y = mis.convertir(self.d, AVOCATE, d['id'], d['version'])
        self.assertEqual(x['dossier_id'], y['dossier_id'])
        self.assertTrue(y['rejoue'])
        self.assertEqual(self.d.db.execute('SELECT COUNT(*) FROM dossiers5697').fetchone()[0], 1)
        self.assertEqual(mis.lire(self.d, AVOCATE, d['id'])['etat'], 'convertie')

    def test_R16b_partie_ajoutee_apres_controle_retour_a_decider(self):
        d = self.acceptee()
        self.assertEqual(d['etat'], 'acceptee')
        d = mis.modifier(self.d, AVOCATE, d['id'], d['version'], {'parties': d['parties'] + [{'nom': 'BERNARD', 'role': 'tiers'}]})
        self.assertEqual(d['etat'], 'a_decider')
        self.assertFalse(d['controle']['valide'])
        with self.assertRaises(Stop) as ctx:
            mis.decider(self.d, AVOCATE, d['id'], d['version'], 'accepter', 'Toujours aucun conflit.')
        self.assertEqual(str(ctx.exception), 'controle_conflits_a_refaire')
        with self.assertRaises(Stop):
            mis.convertir(self.d, AVOCATE, d['id'], d['version'])
        d = mis.verifier_conflits(self.d, AVOCATE, d['id'], d['version'])
        self.assertTrue(d['controle']['valide'])
        self.assertEqual(d['controle']['parties_version'], 2)

    def test_R16c_refus_aucun_dossier(self):
        d = mis.creer(self.d, AVOCATE, {'origine': 'saisie', 'objet': 'Litige ZORVAL', 'client_nom': 'ZORVAL'}, 'cle-refus-0001')
        with self.assertRaises(Stop):
            mis.decider(self.d, AVOCATE, d['id'], d['version'], 'refuser', 'court')
        d = mis.decider(self.d, AVOCATE, d['id'], d['version'], 'refuser', 'Conflit d’intérêts avec un client existant.')
        self.assertEqual(d['etat'], 'refusee')
        with self.assertRaises(Stop):
            mis.convertir(self.d, AVOCATE, d['id'], d['version'])
        self.assertEqual(self.d.db.execute('SELECT COUNT(*) FROM dossiers5697').fetchone()[0] if self.d.db.execute(
            "SELECT 1 FROM sqlite_master WHERE name='dossiers5697'").fetchone() else 0, 0)

    def test_R16d_signee_exige_le_fichier_et_renommer_ne_le_change_pas(self):
        d = self.acceptee()
        self.assertNotEqual(d['convention_etat'], 'signee')
        d = mis.preparer_convention(self.d, AVOCATE, d['id'])
        self.assertEqual(d['convention_etat'], 'projet')
        signe = b'%PDF-1.4 convention signee fictive'
        d = mis.deposer(self.d, AVOCATE, d['id'], 'convention_signee', 'Convention signée KERVILAC.pdf', signe, 'application/pdf')
        self.assertEqual(d['convention_etat'], 'signee')
        avant = [(p['nom'], p['sha256']) for p in d['preparations'] if p['nature'] == 'convention_signee']
        d = mis.modifier(self.d, AVOCATE, d['id'], d['version'], {'client_nom': 'KERVILAC SAS'})
        apres = [(p['nom'], p['sha256']) for p in d['preparations'] if p['nature'] == 'convention_signee']
        self.assertEqual(avant, apres)
        self.assertEqual(mis.preparation(self.d, AVOCATE, d['id'], d['preparations'][-1]['id'])[2], signe)

    def test_R16e_appel_rendez_vous_et_courriel_meme_formulaire(self):
        cle = self.courriel(cle='b' * 64)
        self.d.db.execute('INSERT INTO calendar_cache(id,uid,recurrence_id,calendar_url,href,etag,title,description,location,starts,ends,busy,matter,matter_candidates,fetched) '
                          'VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)', ('ev1', 'ev1', '', '/cal/', '/cal/ev1', 'e', 'Premier rendez-vous MARTIN', '', '', '2026-11-03T10:00:00+00:00',
                                                                   '2026-11-03T11:00:00+00:00', 1, '', '[]', 'x'))
        self.d.db.commit()
        for origine, ref, objet in (('courriel', cle, 'Demande de conseil bail'), ('rendez_vous', 'ev1', 'Premier rendez-vous MARTIN'), ('appel', '', '')):
            d = mis.creer(self.d, AVOCATE, {'origine': origine, 'source_ref': ref}, 'cle-entree-' + origine)
            self.assertEqual((d['origine'], d['objet'], d['etat']), (origine, objet, 'nouvelle'))
        with self.assertRaises(Stop):
            mis.creer(self.d, AVOCATE, {'origine': 'courriel', 'source_ref': 'c' * 64}, 'cle-entree-inconnu')

    def test_R16f_rapprochement_dans_un_dossier_reserve_compte_jamais_decrit(self):
        d = mis.creer(self.d, AVOCATE, {'origine': 'saisie', 'objet': 'Contentieux ZORVAL', 'client_nom': 'ACME', 'parties': [{'nom': 'ZORVAL', 'role': 'adverse'}]}, 'cle-masque-01')
        d = mis.verifier_conflits(self.d, AVOCATE, d['id'], d['version'])
        c = d['controle']
        self.assertEqual(c['masques'], 1)                                # ZORVAL est client du dossier réservé DOS-003
        self.assertFalse(any(r['matter'] == 'DOS-003' for r in c['rapprochements']))
        self.assertNotIn('DOS-003', json.dumps(c, ensure_ascii=False))
        self.assertTrue(c['a_examiner'])
        with self.assertRaises(Stop):     # accepter sans motif alors qu'un rapprochement subsiste : refusé
            mis.decider(self.d, AVOCATE, d['id'], d['version'], 'accepter', '')
        vu = mis.lire(self.d, AUTRE, d['id'])['controle']                 # l'avocat autorisé voit le dossier concerné
        self.assertTrue(any(r['matter'] == 'DOS-003' for r in vu['rapprochements']))

    def test_R16g_conversion_reprise_apres_coupure_rend_le_meme_dossier(self):
        d = self.acceptee(cle='cle-coupure-01')
        from agent import dossiers5697
        vrai = dossiers5697.creer

        def cree_puis_coupe(*args, **kwargs):
            vrai(*args, **kwargs)
            raise Stop('connexion_http_indisponible')
        with patch.object(dossiers5697, 'creer', cree_puis_coupe):
            with self.assertRaises(Stop):
                mis.convertir(self.d, AVOCATE, d['id'], d['version'])
        self.assertEqual(mis.lire(self.d, AVOCATE, d['id'])['etat'], 'acceptee')       # état provisoire distinct d'une conversion
        out = mis.convertir(self.d, AVOCATE, d['id'], d['version'])
        self.assertEqual(self.d.db.execute('SELECT COUNT(*) FROM dossiers5697').fetchone()[0], 1)
        self.assertEqual(self.d.db.execute('SELECT COUNT(*) FROM demande_conversion5697').fetchone()[0], 1)
        with self.assertRaises(Exception):   # contrainte unique : un second dossier pour la même demande est impossible
            self.d.db.execute("INSERT INTO demande_conversion5697(demande_id,dossier_id,cle_dossier,acteur,role,donnees,cree_le,maj_le) VALUES(?,?,?,?,?,?,?,?)",
                              (d['id'], 'autre', 'x', 'x', 'avocat', '{}', 'x', 'x'))
        self.d.db.rollback()
        parties = {r[0] for r in self.d.db.execute('SELECT nom FROM dossier_parties5697 WHERE dossier_id=?', (out['dossier_id'],))}
        self.assertEqual(parties, {'KERVILAC', 'ORLANDEC'})

    def test_R16_assistant_ne_decide_pas(self):
        d = mis.creer(self.d, ASSISTANT, {'origine': 'appel', 'objet': 'Appel MARTIN', 'client_nom': 'MARTIN'}, 'cle-assist-001')
        d = mis.verifier_conflits(self.d, ASSISTANT, d['id'], d['version'])
        with self.assertRaises(Stop) as ctx:
            mis.decider(self.d, ASSISTANT, d['id'], d['version'], 'accepter', 'Motif suffisant pour décider.')
        self.assertEqual(str(ctx.exception), 'role_insuffisant')


# ====================================================================================== § 17
class Portefeuille(Base):
    def setUp(self):
        super().setUp()
        from agent import echeances450, taches5688
        echeances450.ensure_schema(self.d)
        taches5688.schema(self.d)
        pf.schema(self.d)
        self.jour = pf.aujourd_hui(self.d)

    def tache(self, dossier, titre, cle):
        return pf._creer_tache(self.d, acces5697.acteur_de(AVOCATE), dossier, titre, '', cle, 'banc')[0]

    def ids(self, liste):
        return [x['dossier_id'] for x in liste['elements']]

    def test_R17a_reassigner_garde_l_historique(self):
        e = pf.etat(self.d, AVOCATE, 'DOS-001')
        e = pf.definir(self.d, AVOCATE, 'DOS-001', {'responsable_id': AVOCATE[0]}, e['version'], 'cle-resp-0001')
        e = pf.definir(self.d, AVOCATE, 'DOS-001', {'responsable_id': ASSISTANT[0]}, e['version'], 'cle-resp-0002')
        h = [(x['avant'], x['apres']) for x in pf.historique(self.d, AVOCATE, 'DOS-001') if x['champ'] == 'responsable_id']
        self.assertEqual(sorted(h), sorted([('', AVOCATE[0]), (AVOCATE[0], ASSISTANT[0])]))
        with self.assertRaises(Stop):     # une personne sans accès au dossier réservé ne peut pas en être responsable
            pf.definir(self.d, AUTRE, 'DOS-003', {'responsable_id': AVOCATE[0]}, 0, 'cle-resp-0003')
        with self.assertRaises(Stop):     # l'assistant ne réassigne pas
            pf.definir(self.d, ASSISTANT, 'DOS-001', {'responsable_id': AVOCATE[0]}, e['version'], 'cle-resp-0004')

    def test_R17b_action_terminee_signalee_et_suivante_suggeree(self):
        e = pf.ajouter_action(self.d, AVOCATE, 'DOS-001', 'Appeler le client', '', 0, 'cle-act-00001')
        self.assertEqual(e['action']['libelle'], 'Appeler le client')
        self.assertNotIn('DOS-001', self.ids(pf.listes(self.d, AVOCATE)['sans_action']))
        suivante = self.tache('DOS-001', 'Rédiger la note', 'cle-act-00002')
        fermee = e['tache']
        self.d.db.execute("UPDATE tasks SET status='closed' WHERE id=?", (fermee,))      # l'action est terminée
        self.d.db.commit()
        e = pf.etat(self.d, AVOCATE, 'DOS-001')
        self.assertIsNone(e['action'])
        self.assertEqual(e['action_retiree'], 'terminee')
        self.assertEqual(e['suggestion']['ref'], 'tache:' + suivante)
        sans = pf.listes(self.d, AVOCATE)['sans_action']
        ligne = next(x for x in sans['elements'] if x['dossier_id'] == 'DOS-001')
        self.assertIn('terminée', ligne['motif'])
        with self.assertRaises(Stop):     # une action terminée ne peut pas être désignée
            pf.definir(self.d, AVOCATE, 'DOS-001', {'next_action': 'tache:' + fermee}, e['version'], 'cle-act-00003')

    def test_R17c_depot_pendant_l_attente_signale_sans_changer_la_date(self):
        from agent import memoire5651
        e = pf.etat(self.d, AVOCATE, 'DOS-002')
        depuis = (self.jour - timedelta(days=3)).isoformat()
        e = pf.definir(self.d, AVOCATE, 'DOS-002', {'etat_operatoire': 'attente_client', 'attente_de': 'MARTIN', 'motif_attente': 'Bail signé',
                                                     'attente_depuis': depuis, 'revoir_le': (self.jour + timedelta(days=10)).isoformat()}, e['version'], 'cle-att-00001')
        self.assertNotIn('DOS-002', self.ids(pf.listes(self.d, AVOCATE)['attentes']))
        memoire5651.record_change(self.d, 'DOS-002', 'nouveau_document', 'document', '/Dossiers/x/Bail.pdf', 'v1', 'Nouveau document : Bail.pdf')
        att = pf.listes(self.d, AVOCATE)['attentes']
        ligne = next(x for x in att['elements'] if x['dossier_id'] == 'DOS-002')
        self.assertIn('à qualifier', ligne['motif'])
        self.assertEqual(pf.etat(self.d, AVOCATE, 'DOS-002')['attente_depuis'], depuis)        # l'import ne réinitialise pas l'attente

    def test_R17d_recontrole_depasse_et_seuil_sans_date(self):
        e = pf.etat(self.d, AVOCATE, 'DOS-001')
        pf.definir(self.d, AVOCATE, 'DOS-001', {'etat_operatoire': 'attente_tiers', 'attente_de': 'Greffe', 'motif_attente': 'Copie du jugement',
                                                 'revoir_le': (self.jour + timedelta(days=5)).isoformat()}, e['version'], 'cle-relance-01')
        self.assertNotIn('DOS-001', self.ids(pf.listes(self.d, AVOCATE)['attentes']))
        plus_tard = self.jour + timedelta(days=6)          # le banc avance la date
        att = pf.listes(self.d, AVOCATE, now=plus_tard)['attentes']
        self.assertIn('DOS-001', self.ids(att))
        self.assertEqual(next(x for x in att['elements'] if x['dossier_id'] == 'DOS-001')['etat_libelle'], 'À relancer')
        e = pf.etat(self.d, AVOCATE, 'DOS-002')
        pf.definir(self.d, AVOCATE, 'DOS-002', {'etat_operatoire': 'attente_client', 'motif_attente': 'Pièces comptables'}, e['version'], 'cle-relance-02')
        self.assertNotIn('DOS-002', self.ids(pf.listes(self.d, AVOCATE, now=self.jour + timedelta(days=pf.SEUIL_DEFAUT - 1))['attentes']))
        self.assertIn('DOS-002', self.ids(pf.listes(self.d, AVOCATE, now=self.jour + timedelta(days=pf.SEUIL_DEFAUT))['attentes']))
        r = pf.preparer_relance(self.d, AVOCATE, 'DOS-001', 'cle-relance-03', now=plus_tard)
        r2 = pf.preparer_relance(self.d, AVOCATE, 'DOS-001', 'cle-relance-03', now=plus_tard)
        self.assertEqual(r['tache_relance'], r2['tache_relance'])
        self.assertEqual(r['etat_operatoire'], 'attente_tiers')

    def test_R17e_fermer_l_attente_recalcule_sans_doublon(self):
        e = pf.etat(self.d, AVOCATE, 'DOS-001')
        e = pf.definir(self.d, AVOCATE, 'DOS-001', {'etat_operatoire': 'attente_client', 'motif_attente': 'Documents', 'revoir_le': self.jour.isoformat()},
                       e['version'], 'cle-ferme-0001')
        l1 = pf.listes(self.d, AVOCATE)
        self.assertEqual(self.ids(l1['attentes']).count('DOS-001'), 1)
        self.assertNotIn('DOS-001', self.ids(l1['sans_action']))
        pf.definir(self.d, AVOCATE, 'DOS-001', {'etat_operatoire': 'a_traiter'}, e['version'], 'cle-ferme-0002')
        l2 = pf.listes(self.d, AVOCATE)
        self.assertNotIn('DOS-001', self.ids(l2['attentes']))
        self.assertEqual(self.ids(l2['sans_action']).count('DOS-001'), 1)
        self.assertEqual(l2['sans_action']['nombre'], len(set(self.ids(l2['sans_action']))))

    def test_R17f_dossier_masque_ne_change_aucun_compteur(self):
        avant = pf.listes(self.d, AVOCATE)
        self.assertNotIn('DOS-003', self.ids(avant['sans_action']))
        e = pf.etat(self.d, AUTRE, 'DOS-003')
        pf.definir(self.d, AUTRE, 'DOS-003', {'etat_operatoire': 'attente_client', 'motif_attente': 'Réservé', 'revoir_le': self.jour.isoformat()}, e['version'], 'cle-masque-001')
        apres = pf.listes(self.d, AVOCATE)
        self.assertEqual((avant['sans_action']['nombre'], avant['attentes']['nombre']), (apres['sans_action']['nombre'], apres['attentes']['nombre']))
        self.assertIn('DOS-003', self.ids(pf.listes(self.d, AUTRE)['attentes']))
        with self.assertRaises(Stop) as ctx:
            pf.etat(self.d, AVOCATE, 'DOS-003')
        self.assertEqual(str(ctx.exception), 'introuvable')
        mes = pf.listes(self.d, AVOCATE, mes_dossiers=True)               # filtre après les droits
        self.assertEqual(mes['sans_action']['nombre'], 0)

    def test_R17g_echeance_confirmee_visible_et_prioritaire(self):
        due = (self.jour + timedelta(days=4)).isoformat()
        self.d.db.execute("INSERT INTO deadlines450(id,matter,rule_id,start_event,start_date,due,status,origin,created,updated) VALUES(?,?,?,?,?,?,?,?,?,?)",
                          ('ech1', 'DOS-001', 'regle-fictive', 'signification', due, due, 'confirmee', 'banc', due, due))
        self.d.db.execute("INSERT INTO deadlines450(id,matter,rule_id,start_event,start_date,due,status,origin,created,updated) VALUES(?,?,?,?,?,?,?,?,?,?)",
                          ('ech2', 'DOS-001', 'regle-fictive', 'signification', due, (self.jour + timedelta(days=1)).isoformat(), 'a_confirmer', 'banc', due, due))
        self.d.db.commit()
        self.tache('DOS-001', 'Tâche quelconque', 'cle-ech-00001')
        e = pf.etat(self.d, AVOCATE, 'DOS-001')
        self.assertEqual(e['suggestion']['ref'], 'echeance:ech1')        # confirmée d'abord ; une date à vérifier n'est jamais proposée
        e = pf.definir(self.d, AVOCATE, 'DOS-001', {'etat_operatoire': 'attente_client', 'motif_attente': 'Pièces', 'revoir_le': (self.jour + timedelta(days=30)).isoformat()},
                       e['version'], 'cle-ech-00002')
        self.assertEqual(e['echeance_prioritaire']['ref'], 'echeance:ech1')
        self.assertEqual(e['date_utile'], due)
        with self.assertRaises(Stop):
            pf.definir(self.d, AVOCATE, 'DOS-001', {'etat_operatoire': 'a_traiter', 'next_action': 'echeance:ech2'}, e['version'], 'cle-ech-00003')

    def test_R17h_version_perimee_409(self):
        e = pf.etat(self.d, AVOCATE, 'DOS-001')
        pf.definir(self.d, AVOCATE, 'DOS-001', {'etat_operatoire': 'en_cours'}, e['version'], 'cle-vers-00001')
        with self.assertRaises(Stop) as ctx:
            pf.definir(self.d, ASSISTANT, 'DOS-001', {'etat_operatoire': 'pret_a_cloturer'}, e['version'], 'cle-vers-00002')
        self.assertEqual(str(ctx.exception), 'version_en_conflit')
        again = pf.definir(self.d, AVOCATE, 'DOS-001', {'etat_operatoire': 'en_cours'}, e['version'], 'cle-vers-00001')     # double clic : même état
        self.assertEqual(again['version'], 1)
        with self.assertRaises(Stop):     # une action d'un autre dossier n'est pas acceptée
            t = self.tache('DOS-002', 'Tâche MARTIN', 'cle-vers-00003')
            pf.definir(self.d, AVOCATE, 'DOS-001', {'next_action': 'tache:' + t}, again['version'], 'cle-vers-00004')


# ====================================================================================== C01, mutations
class SansIA(Base):
    def test_C01_commandes_sans_ia_en_pause_avec_fournisseurs_bloques(self):
        from agent.arret5643 import PAUSE_GLOBAL
        self.d.setting(PAUSE_GLOBAL, True)
        bloque = AssertionError('fournisseur IA appelé')
        with patch('agent.ai_gateway.estimate_and_check_budget', side_effect=bloque), patch('agent.ai_gateway.common_completion', side_effect=bloque), \
             patch('agent.common.HTTP.request', side_effect=AssertionError('appel réseau')), \
             patch('agent.desk.Desk.enqueue', side_effect=AssertionError('travail IA mis en file')):
            doc = docs.creer(self.d, AVOCATE, {'dossier': 'DOS-001', 'mode': 'vierge', 'type': 'courrier', 'titre': 'Courrier en pause'}, 'cle-pause-0001')
            lu = docs.ouvrir(self.d, AVOCATE, doc['document_id'])
            docs.enregistrer(self.d, AVOCATE, doc['document_id'], lu['version'], 'cle-pause-0002', lignes=lu['paragraphes'] + ['Ajout'])
            d = mis.creer(self.d, AVOCATE, {'origine': 'appel', 'objet': 'Appel DURAND', 'client_nom': 'DURAND SARL', 'responsable': AVOCATE[0], 'type_mission': 'conseil',
                                            'correspondant_role': 'client'}, 'cle-pause-0003')
            d = mis.verifier_conflits(self.d, AVOCATE, d['id'], d['version'])
            d = mis.decider(self.d, AVOCATE, d['id'], d['version'], 'accepter', 'Rapport examiné, aucun conflit retenu.')
            out = mis.convertir(self.d, AVOCATE, d['id'], d['version'])
            self.assertTrue(out['dossier_id'])
            e = pf.ajouter_action(self.d, AVOCATE, 'DOS-001', 'Appeler le greffe', '', 0, 'cle-pause-0004')
            self.assertTrue(e['action'])
            pf.listes(self.d, AVOCATE)
        self.assertEqual(self.d.db.execute('SELECT COUNT(*) FROM jobs').fetchone()[0], 0)


# ====================================================================================== routes, droits, Aujourd'hui
class Routes(Base):
    def env(self, corps=b'', owner=AVOCATE, csrf='jeton-fictif', ctype='application/json', methode='POST'):
        return {'REQUEST_METHOD': methode, 'HTTP_ORIGIN': 'https://axiorhub.example.test', 'HTTP_X_CSRF_TOKEN': csrf, 'CONTENT_TYPE': ctype,
                'CONTENT_LENGTH': str(len(corps)), 'wsgi.input': io.BytesIO(corps), 'HTTP_X_AXIORHUB_USER': owner[0], 'HTTP_X_AXIORHUB_ROLE': owner[1]}

    def appel(self, route, data=None, owner=AVOCATE, args=None, brut=None, csrf='jeton-fictif'):
        from agent import mission_web5697 as w
        auth = {'origin': 'https://axiorhub.example.test', 'csrf': 'jeton-fictif'}
        if brut is not None:
            env = self.env(brut, owner, csrf, 'application/octet-stream')
        elif data is None:
            env = self.env(b'', owner, csrf, methode='GET')
        else:
            env = self.env(json.dumps(data).encode(), owner, csrf)
        out = w.api(env, self.d, auth, '/p', 'm5689/' + route, args or {}, env['REQUEST_METHOD'])
        corps = out['body']
        return out['status'], (json.loads(corps) if out['kind'].startswith('application/json') else corps)

    def test_W01_droits_par_route(self):
        from agent import membres5650, mission_web5697 as w, standalone_auth
        self.assertFalse(w.allowed('assistant', 'demandes/decider'))
        self.assertFalse(w.allowed('assistant', 'demandes/convertir'))
        self.assertTrue(w.allowed('assistant', 'demandes/creer'))
        self.assertFalse(w.allowed('avocat', 'portefeuille/seuil'))
        self.assertFalse(w.allowed('avocat', 'creation/inconnue'))
        self.assertEqual(membres5650.category('POST', '/api440/m5689/demandes/decider'), 'valider')
        self.assertEqual(membres5650.category('POST', '/api440/m5689/portefeuille/seuil'), 'reglages')
        self.assertFalse(standalone_auth.allowed('assistant', 'POST', '/api440/m5689/demandes/convertir'))
        self.assertTrue(standalone_auth.allowed('assistant', 'POST', '/api440/m5689/creation/creer'))
        statut, out = self.appel('demandes/decider', {'id': 'x', 'version': 1, 'decision': 'accepter'}, owner=ASSISTANT)
        self.assertEqual((statut, out['error']), ('403 Forbidden', 'role_insuffisant'))

    def test_W02_creation_import_csrf_et_conflit_en_409(self):
        statut, out = self.appel('creation/creer', {'dossier': 'DOS-001', 'mode': 'vierge', 'type': 'libre', 'titre': 'Note par la route', 'cle': 'cle-route-0001'}, csrf='autre')
        self.assertEqual(statut, '403 Forbidden')
        self.assertEqual(self.fichiers('DOS-001'), [])
        statut, out = self.appel('creation/creer', {'dossier': 'DOS-001', 'mode': 'vierge', 'type': 'libre', 'titre': 'Note par la route', 'cle': 'cle-route-0001'})
        self.assertEqual(statut, '200 OK')
        statut, imp = self.appel('creation/importer', args={'dossier': 'DOS-001', 'nom': 'Pièce.docx', 'cle': 'cle-route-0002'}, brut=docx_style('Importé par octets'))
        self.assertEqual(statut, '200 OK')
        self.assertIn('Importé par octets', docs.paragraphes_de(docs.fichier(self.d, AVOCATE, imp['document_id'])[2]))
        self.appel('creation/enregistrer', {'id': out['document_id'], 'version': 1, 'lignes': 'Note par la route\nAjout', 'cle': 'cle-route-0003'})
        statut, conflit = self.appel('creation/enregistrer', {'id': out['document_id'], 'version': 1, 'lignes': 'Autre', 'cle': 'cle-route-0004'}, owner=ASSISTANT)
        self.assertEqual(statut, '409 Conflict')
        self.assertIn('version 2', conflit['message'])
        statut, _ = self.appel('creation/document', args={'id': out['document_id']}, owner=AUTRE)
        self.assertEqual(statut, '200 OK')
        reserve = docs.creer(self.d, AUTRE, {'dossier': 'DOS-003', 'mode': 'vierge', 'type': 'libre', 'titre': 'Réservé'}, 'cle-route-0005')
        statut, out = self.appel('creation/fichier', args={'id': reserve['document_id']})
        self.assertEqual(statut, '404 Not Found')

    def test_W03_aujourdhui_listes_et_boutons(self):
        from agent import mission_web5697 as w
        html = w.aujourdhui_html(self.d, '/p', AVOCATE[0], AVOCATE[1])
        self.assertIn('Dossiers sans prochaine action <span class="m97-nombre">2</span>', html)     # DOS-003 réservé : pas compté
        self.assertNotIn('DOS-003', html)
        self.assertIn('/p/nouveau-document', html)
        self.assertIn('Attentes à relancer <span class="m97-nombre">0</span>', html)
        page = w.page_nouveau_document(self.d, acces5697.acteur_de(AVOCATE), '/p', {'dossier': 'DOS-001'})
        for libelle in ('Document vierge', 'Modèle du cabinet', 'Importer un fichier', 'Préparer avec l’IA'):
            self.assertIn(libelle, page)
        self.assertNotIn('<script>', page)

    def test_R17a_responsable_tenu_par_le_registre_unique(self):
        from agent import dossiers5697
        recu = dossiers5697.creer(self.d, AVOCATE, {'titre': 'Affaire KERVILAC', 'client': 'KERVILAC', 'responsable': AVOCATE[0]}, 'cle-unique-0001')
        e = pf.etat(self.d, AVOCATE, recu['id'])
        self.assertEqual(e['responsable_id'], AVOCATE[0])
        e = pf.definir(self.d, AVOCATE, recu['id'], {'responsable_id': ASSISTANT[0]}, e['version'], 'cle-unique-0002')
        self.assertEqual(dossiers5697.lire(self.d, AVOCATE, recu['id'])['responsable'], ASSISTANT[0])     # une seule source de vérité
        self.assertEqual(pf.etat(self.d, AVOCATE, recu['id'])['responsable_id'], ASSISTANT[0])
        self.assertIn(recu['id'], [x['dossier_id'] for x in pf.listes(self.d, ASSISTANT, mes_dossiers=True)['sans_action']['elements']])


class Mutations(Base):
    """Contre-épreuves : chaque garde retirée EN MÉMOIRE (patch), le scénario qu'elle protège échoue ; rien n'est modifié dans l'arbre."""

    def test_M01_garde_d_acces_documents(self):
        out = docs.creer(self.d, AUTRE, {'dossier': 'DOS-003', 'mode': 'vierge', 'type': 'libre', 'titre': 'Réservé'}, 'cle-mut-00001')
        with self.assertRaises(Stop):
            docs.ouvrir(self.d, AVOCATE, out['document_id'])
        with patch.object(acces5697, 'exiger', lambda desk, acteur, dossier_id=None, ref=None: acces5697.acteur_de(acteur)):
            self.assertEqual(docs.ouvrir(self.d, AVOCATE, out['document_id'])['titre'], 'Réservé')     # sans la garde : fuite

    def test_M02_controle_de_version_document(self):
        out = docs.creer(self.d, AVOCATE, {'dossier': 'DOS-001', 'mode': 'vierge', 'type': 'libre', 'titre': 'Mutation'}, 'cle-mut-00002')
        lu = docs.ouvrir(self.d, AVOCATE, out['document_id'])
        docs.enregistrer(self.d, AVOCATE, out['document_id'], 1, 'cle-mut-00003', lignes=lu['paragraphes'] + ['A'])
        with patch.object(docs, '_conflit', lambda desk, row, attendue: None), patch.object(docs.Conflit, '__init__', side_effect=AssertionError('conflit')):
            # sans le contrôle du numéro, seul l'ETag protège encore : la version 1 périmée ne passe pas pour autant
            with self.assertRaises(BaseException):
                docs.enregistrer(self.d, AVOCATE, out['document_id'], 1, 'cle-mut-00004', lignes=['B'])

    def test_M03_masquage_des_rapprochements(self):
        d = mis.creer(self.d, AVOCATE, {'origine': 'saisie', 'objet': 'ZORVAL', 'client_nom': 'ACME', 'parties': [{'nom': 'ZORVAL', 'role': 'adverse'}]}, 'cle-mut-00005')
        d = mis.verifier_conflits(self.d, AVOCATE, d['id'], d['version'])
        self.assertEqual(d['controle']['masques'], 1)
        with patch.object(acces5697, 'dossiers_autorises', lambda desk, a: acces5697.Perimetre(acces5697.acteur_de(a), ())):
            fuite = mis.lire(self.d, AVOCATE, d['id'])['controle']
        self.assertEqual(fuite['masques'], 0)                               # sans le périmètre : le dossier réservé serait décrit

    def test_M04_perimetre_avant_compteurs(self):
        e = pf.etat(self.d, AUTRE, 'DOS-003')
        pf.definir(self.d, AUTRE, 'DOS-003', {'etat_operatoire': 'attente_client', 'motif_attente': 'Réservé', 'revoir_le': pf.aujourd_hui(self.d).isoformat()}, e['version'], 'cle-mut-00006')
        self.assertEqual(pf.listes(self.d, AVOCATE)['attentes']['nombre'], 0)
        with patch.object(acces5697, 'dossiers_autorises', lambda desk, a: acces5697.Perimetre(acces5697.acteur_de(a), ())):
            self.assertEqual(pf.listes(self.d, AVOCATE)['attentes']['nombre'], 1)


if __name__ == '__main__':
    unittest.main()
