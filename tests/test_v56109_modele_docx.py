"""5.6.109, lot V (F08) : modèles Word du cabinet — import d'un DOCX comme modèle, validation par l'avocat, remplissage fidèle (sans lxml),
production d'un acte contrôlée AVANT l'écriture et de NOUVEAU après réouverture du fichier écrit, avant d'annoncer « prêt ».

Données fictives (DURAND, MARTIN, DOS-001, RG 24/01234) ; stockage simulé (``Stockage`` de ``test_v5689_livrables``) ; aucun réseau, aucun Word
ni LibreOffice : la fidélité visuelle (pagination, polices du poste) n'est pas prouvée ici et reste à contrôler dans Word ou LibreOffice.
La trame fournie (``agent/data/trame_conclusions56109.docx``) est un exemple technique de zones et de styles, PAS un acte conforme.
"""
import hashlib
import io
import json
from pathlib import Path
import unittest
from unittest.mock import patch
import xml.etree.ElementTree as ET
import zipfile

import test_v5689_livrables as t89
from test_v5689_livrables import ASSISTANT, AVOCAT, MID
from agent import livrables5689 as liv, modele_docx56109 as M
from agent.common import Stop

ROOT = Path(__file__).resolve().parent.parent
W = M.W
NS = '{%s}' % W
TRAME = (ROOT / 'agent' / 'data' / 'trame_conclusions56109.docx').read_bytes()
CHAMPS = ['juridiction', 'reference', 'parties', 'faits', 'procedure', 'discussion', 'demandes', 'pieces']
VALEURS = {'juridiction': 'Tribunal judiciaire de Valbrune', 'reference': 'RG 24/01234', 'parties': 'DURAND contre MARTIN',
           'faits': 'Premier fait fictif.\nSecond fait fictif.', 'procedure': 'Assignation fictive.', 'discussion': 'Moyen fictif A & B <client>.',
           'demandes': 'Débouter MARTIN.', 'pieces': 'Pièce n° 1 : contrat fictif.'}
DOSSIER_DEPOT = '/Dossiers/DEMO/20_Actes_et_conclusions/90_AxiorHub_Brouillons'


def archive(document, additions=None):
    parts = {'[Content_Types].xml': b'<Types/>', '_rels/.rels': b'<Relationships/>', 'word/document.xml': document.encode(),
             'word/styles.xml': b'<styles>Cambria</styles>', 'word/footer1.xml': b'<footer>Cabinet page</footer>'}
    parts.update(additions or {})
    out = io.BytesIO()
    with zipfile.ZipFile(out, 'w', zipfile.ZIP_DEFLATED) as z:
        for nom, data in parts.items():
            z.writestr(nom, data)
    return out.getvalue()


def zone(tag='axh:faits', contenu='<w:p><w:pPr><w:pStyle w:val="Corps"/></w:pPr><w:r><w:rPr><w:b/></w:rPr><w:t>Exemple</w:t></w:r></w:p>'):
    return '<w:sdt><w:sdtPr><w:tag w:val="%s"/></w:sdtPr><w:sdtContent>%s</w:sdtContent></w:sdt>' % (tag, contenu)


def document(*corps):
    return ('<w:document xmlns:w="%s" xmlns:w14="http://schemas.microsoft.com/office/word/2010/wordml" xmlns:mc="http://schemas.openxmlformats.org/markup-compatibility/2006" '
            'mc:Ignorable="w14"><w:body><w:p><w:r><w:t>Texte fixe</w:t></w:r></w:p>%s<w:sectPr/></w:body></w:document>') % (W, ''.join(corps))


class Remplissage(unittest.TestCase):
    def test_F08_sans_lxml_et_parties_protegees_octet_pour_octet(self):
        src = (ROOT / 'agent' / 'modele_docx56109.py').read_text(encoding='utf-8')
        self.assertNotIn('lxml', src.replace('SANS lxml', ''))
        r = M.remplir(TRAME, VALEURS)
        with zipfile.ZipFile(io.BytesIO(TRAME)) as a, zipfile.ZipFile(io.BytesIO(r['bytes'])) as b:
            self.assertEqual(a.namelist(), b.namelist())
            for nom in a.namelist():
                if nom != 'word/document.xml':
                    self.assertEqual(a.read(nom), b.read(nom), nom)
            avant, apres = a.read('word/document.xml').decode(), b.read('word/document.xml').decode()
        self.assertEqual(avant[:avant.index('<w:body>')], apres[:apres.index('<w:body>')])     # racine, préfixes et mc:Ignorable intacts
        self.assertIn('mc:Ignorable="w14 wp14"', apres)
        self.assertEqual(r['fields'], sorted(CHAMPS))

    def test_F08_style_du_prototype_par_paragraphe_et_texte_echappe(self):
        brut = archive(document(zone(contenu='<w:p w14:paraId="1234ABCD"><w:pPr><w:pStyle w:val="Corps"/><w:numPr><w:numId w:val="4"/></w:numPr></w:pPr>'
                                             '<w:r><w:rPr><w:rFonts w:ascii="Cambria"/><w:sz w:val="24"/></w:rPr><w:t>Ancien exemple</w:t></w:r></w:p>')))
        r = M.remplir(brut, {'faits': 'Un\nDeux {{7*7}} & <b>'})
        racine = ET.fromstring(zipfile.ZipFile(io.BytesIO(r['bytes'])).read('word/document.xml'))
        paras = racine.findall('.//%ssdtContent/%sp' % (NS, NS))
        self.assertEqual([''.join(t.text for t in p.iter(NS + 't')) for p in paras], ['Un', 'Deux {{7*7}} & <b>'])
        for p in paras:
            self.assertEqual(p.find('%spPr/%spStyle' % (NS, NS)).get(NS + 'val'), 'Corps')
            self.assertEqual(p.find('%sr/%srPr/%ssz' % (NS, NS, NS)).get(NS + 'val'), '24')
            self.assertIsNone(p.get('{http://schemas.microsoft.com/office/word/2010/wordml}paraId'))
        self.assertTrue(M.controler(brut, r['bytes'], {'faits': 'Un\nDeux {{7*7}} & <b>'})['ok'])

    def test_F08_refus_motives_sans_aplatir(self):
        cas = [(archive(document(zone(contenu='<w:tbl/>'))), 'zone_complexe'),
               (archive(document(zone(contenu='<w:p><w:r><w:t>a</w:t></w:r><w:r><w:t>b</w:t></w:r></w:p>'))), 'zone_complexe'),
               (archive(document(zone()), {'word/vbaProject.bin': b'x'}), 'contenu_actif_ou_signe'),
               (archive(document(zone()), {'word/_rels/document.xml.rels': b'<Relationships><Relationship TargetMode="External" Target="https://example.test"/></Relationships>'}), 'relation_externe'),
               (archive('<!DOCTYPE x [<!ENTITY a "b">]><x/>'), 'xml_actif'),
               (archive(document(zone()), {'../evil': b'x'}), 'archive_invalide')]
        for brut, code in cas:
            with self.assertRaisesRegex(Stop, code):
                M.remplir(brut, {'faits': 'A'})
        with self.assertRaisesRegex(Stop, 'aucune_zone'):
            M.inspecter(archive(document()))
        with self.assertRaisesRegex(Stop, 'champs_absents'):
            M.remplir(archive(document(zone())), {'faits': 'A', 'autre': 'B'})

    def test_F08_controle_detecte_les_ecarts(self):
        bon = M.remplir(TRAME, VALEURS)['bytes']
        self.assertTrue(M.controler(TRAME, bon, VALEURS)['ok'])
        self.assertFalse(M.controler(TRAME, bon, {**VALEURS, 'faits': 'Autre texte'})['ok'])
        abime = io.BytesIO()
        with zipfile.ZipFile(io.BytesIO(bon)) as a, zipfile.ZipFile(abime, 'w') as b:
            for i in a.infolist():
                b.writestr(i, a.read(i) + b'\n' if i.filename == 'word/footer1.xml' else a.read(i))   # pied de page retouché d'un octet
        constats = M.controler(TRAME, abime.getvalue(), VALEURS)
        self.assertFalse(constats['ok'])
        self.assertTrue(any(c['code'] == 'partie_modifiee' for c in constats['constats']))
        self.assertFalse(M.controler(TRAME, b'pas un zip', VALEURS)['ok'])


class Base(t89.Base):
    def setUp(self):
        super().setUp()
        M.ensure_schema(self.desk)
        p = patch.object(M, '_client', return_value=self.nc)
        p.start()
        self.addCleanup(p.stop)
        self.n = 0

    def cle(self):
        self.n += 1
        return 'cle-modele-%04d' % self.n

    def appel(self, route, body=None, args=None, who=AVOCAT, binaire=None):
        methode = 'POST' if body is not None or binaire is not None else 'GET'
        raw = binaire if binaire is not None else json.dumps(body or {}).encode()
        env = {'HTTP_X_AXIORHUB_USER': who[0], 'HTTP_X_AXIORHUB_ROLE': who[1], 'HTTP_ORIGIN': 'https://cabinet.example.test', 'HTTP_X_CSRF_TOKEN': 'jeton',
               'CONTENT_LENGTH': str(len(raw)), 'CONTENT_TYPE': 'application/octet-stream' if binaire is not None else 'application/json', 'wsgi.input': io.BytesIO(raw),
               'REQUEST_METHOD': methode}
        out = M.api(env, self.desk, {'origin': 'https://cabinet.example.test', 'csrf': 'jeton'}, '/p', M.PREFIXE + route, args or {}, methode)
        out['json'] = json.loads(out['body'])
        return out

    def modele_valide(self):
        m = M.importer_exemple(self.desk, AVOCAT[0], AVOCAT[1], self.cle())
        return M.valider(self.desk, AVOCAT[0], AVOCAT[1], {'valider_modele': True}, m['id'], m['sha256'], self.cle())

    def depots(self):
        return sorted(p for p in self.nc.files if p.startswith(DOSSIER_DEPOT))


class Registre(Base):
    def test_F08_import_brouillon_puis_validation_par_l_avocat(self):
        out = self.appel('importer', args={'nom': 'Conclusions du cabinet', 'type': 'conclusions', 'droit': 'FR', 'juridiction': 'Tribunal judiciaire', 'cle': self.cle()}, binaire=TRAME)
        self.assertEqual(out['status'], '200 OK', out['json'])
        m = out['json']
        self.assertEqual((m['etat'], m['champs']), ('brouillon', CHAMPS))
        self.assertIn('TRAME TECHNIQUE DE CONCLUSIONS', ' '.join(m['textes_fixes']))     # parties fixes montrées pour relecture
        copie = (Path(self.desk.c['state_dir']) / 'modeles-actes56109' / (m['id'] + '.docx')).read_bytes()
        self.assertEqual(copie, TRAME)                                                     # original conservé tel quel
        refus = self.appel(m['id'] + '/valider', {'sha256': m['sha256'], 'cle': self.cle()}, who=ASSISTANT)
        self.assertEqual(refus['status'], '403 Forbidden')
        self.assertEqual(self.appel(m['id'] + '/valider', {'sha256': '0' * 64, 'cle': self.cle()})['status'], '409 Conflict')
        ok = self.appel(m['id'] + '/valider', {'sha256': m['sha256'], 'cle': self.cle()})
        self.assertEqual(ok['json']['etat'], 'valide')
        # une nouvelle version validée remplace la précédente sans l'effacer
        m2 = self.appel('importer', args={'nom': 'Conclusions du cabinet', 'type': 'conclusions', 'droit': 'FR', 'juridiction': 'Tribunal judiciaire', 'cle': self.cle()}, binaire=TRAME)['json']
        self.assertEqual(m2['version'], 2)
        self.appel(m2['id'] + '/valider', {'sha256': m2['sha256'], 'cle': self.cle()})
        etats = {r['id']: r['etat'] for r in M.lister(self.desk)}
        self.assertEqual((etats[m['id']], etats[m2['id']]), ('remplace', 'valide'))

    def test_F08_trame_d_exemple_dite_non_conforme(self):
        m = M.importer_exemple(self.desk, AVOCAT[0], AVOCAT[1], self.cle())
        self.assertTrue(m['exemple'])
        self.assertIn('pas un acte conforme', m['nom'])
        self.assertEqual(hashlib.sha256(TRAME).hexdigest(), M.EXEMPLE_SHA256)
        html = M.section_html(self.desk, '/p', AVOCAT[0], AVOCAT[1])
        self.assertIn('ce n’est pas un acte conforme', html)

    def test_F08_refus_d_un_modele_non_admissible(self):
        out = self.appel('importer', args={'type': 'conclusions', 'cle': self.cle()}, binaire=archive(document()))
        self.assertEqual(out['json']['error'], 'aucune_zone')
        self.assertIn('éditeur existant', out['json']['message'])
        self.assertEqual(M.lister(self.desk), [])


class Production(Base):
    def test_F08_acte_pret_seulement_apres_reouverture_et_controle(self):
        m = self.modele_valide()
        out = self.appel(m['id'] + '/produire', {'dossier': MID, 'titre': 'Conclusions DURAND', 'valeurs': VALEURS, 'cle': 'prod-cle-0001'})
        self.assertEqual(out['status'], '200 OK', out['json'])
        r = out['json']
        self.assertTrue(r['pret'])
        self.assertTrue(r['controle']['ok'])
        [chemin] = self.depots()
        relu = self.nc.files[chemin]
        self.assertTrue(M.controler(TRAME, relu, VALEURS)['ok'])
        item = liv.charger(self.desk, r['document_id'])
        self.assertEqual((item['production_state'], item['matter_id'], item['path']), ('verified', MID, chemin))   # même registre, même lecteur
        self.assertEqual(r['href'], '/p/livrables/' + r['document_id'])
        rejeu = self.appel(m['id'] + '/produire', {'dossier': MID, 'titre': 'Conclusions DURAND', 'valeurs': VALEURS, 'cle': 'prod-cle-0001'})
        self.assertTrue(rejeu['json']['replayed'])
        self.assertEqual(len(self.depots()), 1)

    def test_F08_garde_fou_docx_recontrole_apres_ecriture(self):
        """Écart trouvé seulement à la réouverture : jamais « prêt ». Ce test échoue si le second contrôle est retiré de ``_constater``."""
        m = self.modele_valide()
        vrai = M.controler
        appels = []

        def controle(raw_m, raw_s, valeurs):
            appels.append(1)
            out = vrai(raw_m, raw_s, valeurs)
            return out if len(appels) == 1 else {'ok': False, 'constats': [{'code': 'style', 'libelle': 'écart simulé'}], 'resume': 'écart'}
        with patch.object(M, 'controler', side_effect=controle):
            r = self.appel(m['id'] + '/produire', {'dossier': MID, 'titre': 'Conclusions', 'valeurs': VALEURS, 'cle': 'prod-cle-0002'})['json']
        self.assertEqual(len(appels), 2)
        self.assertFalse(r['pret'])
        self.assertEqual(r['etat'], 'non_conforme')
        self.assertEqual(liv.charger(self.desk, r['document_id'])['production_state'], 'failed')

    def test_F08_controle_avant_ecriture_rien_d_ecrit(self):
        m = self.modele_valide()
        with patch.object(M, 'controler', return_value={'ok': False, 'constats': [{'code': 'x', 'libelle': 'x'}], 'resume': 'x'}):
            out = self.appel(m['id'] + '/produire', {'dossier': MID, 'titre': 'Conclusions', 'valeurs': VALEURS, 'cle': 'prod-cle-0003'})
        self.assertEqual(out['json']['error'], 'controle_docx_echoue')
        self.assertEqual(self.depots(), [])

    def test_F08_reponse_perdue_meme_cible_jamais_un_second_fichier(self):
        m = self.modele_valide()
        vrai_put = self.nc.put_file

        def put_puis_coupure(path, data, kind=''):
            vrai_put(path, data, kind)
            raise ConnectionResetError('réponse perdue')
        with patch.object(self.nc, 'put_file', side_effect=put_puis_coupure):
            with patch.object(self.nc, 'stat', side_effect=Stop('stockage_injoignable')):
                r = self.appel(m['id'] + '/produire', {'dossier': MID, 'titre': 'Conclusions', 'valeurs': VALEURS, 'cle': 'prod-cle-0004'})
        self.assertEqual(r['status'], '202 Accepted')
        self.assertEqual(r['json']['etat'], 'a_verifier')
        self.assertFalse(r['json']['pret'])
        r2 = self.appel(m['id'] + '/produire', {'dossier': MID, 'titre': 'Conclusions', 'valeurs': VALEURS, 'cle': 'prod-cle-0004'})
        self.assertTrue(r2['json']['pret'], r2['json'])
        self.assertEqual(len(self.depots()), 1)

    def test_F08_modele_non_valide_dossier_reserve_et_valeurs_manquantes(self):
        m = M.importer_exemple(self.desk, AVOCAT[0], AVOCAT[1], self.cle())
        self.assertEqual(self.appel(m['id'] + '/produire', {'dossier': MID, 'valeurs': VALEURS, 'cle': self.cle()})['json']['error'], 'modele_non_valide')
        m = self.modele_valide()
        with patch('agent.membres5650.hidden_for', return_value=frozenset({MID})):
            reserve = self.appel(m['id'] + '/produire', {'dossier': MID, 'valeurs': VALEURS, 'cle': self.cle()})
        self.assertEqual(reserve['status'], '404 Not Found')
        self.assertEqual(self.appel('0' * 32 + '/produire', {'dossier': MID, 'valeurs': VALEURS, 'cle': self.cle()})['json'], reserve['json'])
        manque = {k: v for k, v in VALEURS.items() if k != 'pieces'}
        self.assertEqual(self.appel(m['id'] + '/produire', {'dossier': MID, 'valeurs': manque, 'cle': self.cle()})['json']['error'], 'champs_absents_ou_inconnus')
        self.assertEqual(self.depots(), [])


if __name__ == '__main__':
    unittest.main()
