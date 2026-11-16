"""5.6.106 (lot I) : images et pages de PDF pour les modèles capables de voir, masquage avant tout envoi externe.

Faux fournisseurs (forme exacte du message de chaque fournisseur), fausse page rendue et faux OCR (boîtes de mots). Les essais avec les
vrais outils (Poppler, Tesseract) sont sautés proprement quand ils ne sont pas installés. Aucune connexion réseau, aucun modèle réel."""
import base64
import json
from pathlib import Path
import sqlite3
import unittest
from unittest.mock import patch
import zlib

from agent import pseudo540, vision56106 as V
from agent.common import Stop
from agent.desk import Desk
from agent.model import Model, routed_config
import test_agent as fixtures

# ------------------------------------------------------------------------------------------------ fausse page et faux OCR
W_OCR, H_OCR = 566, 800           # rendu de l'OCR (COTE_OCR ramené à 800 dans les essais)
ENCRE = 40                        # gris des mots sur la fausse page (blanc 255, noir 0)
# (texte, confiance, x, y, largeur, hauteur, ligne) en coordonnées du rendu de l'OCR
MOTS = [('Madame', 95, 40, 40, 70, 20, 1), ('Hélène', 95, 120, 40, 60, 20, 1), ('KERVILAC', 95, 190, 40, 100, 20, 1),
        ('Courriel', 94, 40, 80, 70, 20, 2), ('durand@example.test', 93, 120, 80, 180, 20, 2),
        ('Tél.', 92, 40, 120, 40, 20, 3), ('01', 92, 90, 120, 20, 20, 3), ('00', 92, 115, 120, 20, 20, 3), ('00', 92, 140, 120, 20, 20, 3),
        ('00', 92, 165, 120, 20, 20, 3), ('05', 92, 190, 120, 20, 20, 3),
        ('Le', 96, 40, 300, 25, 20, 4), ('contrat', 96, 70, 300, 70, 20, 4), ('est', 96, 145, 300, 30, 20, 4), ('signé', 96, 180, 300, 55, 20, 4),
        ('griffonné', 12, 40, 400, 90, 20, 5)]
SENSIBLES = {'Hélène', 'KERVILAC', 'durand@example.test', '01', '05'}
SURS = {'contrat', 'signé', 'Le', 'est'}


def faux_rendu(pdf, page, cote, gris):
    h = int(cote)
    w = int(round(cote * W_OCR / H_OCR))
    c = 1 if gris else 3
    px = bytearray([255]) * (w * h * c)
    f = h / float(H_OCR)
    for _, _, x, y, ww, hh, _ in MOTS:
        for yy in range(int(y * f), min(h, int((y + hh) * f))):
            debut = (yy * w + int(x * f)) * c
            fin = (yy * w + min(w, int((x + ww) * f))) * c
            px[debut:fin] = bytes([ENCRE]) * (fin - debut)
    return (b'P5' if gris else b'P6') + b'\n%d %d\n255\n' % (w, h) + bytes(px)


def faux_tsv(_png):
    lignes = ['level\tpage_num\tblock_num\tpar_num\tline_num\tword_num\tleft\ttop\twidth\theight\tconf\ttext']
    for n, (t, conf, x, y, w, h, ligne) in enumerate(MOTS, 1):
        lignes.append('5\t1\t1\t1\t%d\t%d\t%d\t%d\t%d\t%d\t%s\t%s' % (ligne, n, x, y, w, h, conf, t))
    return '\n'.join(lignes) + '\n'


def faux_pdf():
    return V.pdf_image(10, 14, 'FlateDecode', 'DeviceGray', zlib.compress(b'\xff' * 140))


def lire_png(data):
    w, h, c, px = V.png_lire(data)
    return w, h, c, px


def pixel(img, x, y):
    w, h, c, px = img
    return px[(y * w + x) * c]


class FakeHTTP:
    payloads = []
    caps = ['completion']

    def __init__(self, base, *args, **kwargs):
        self.base, self.headers = base, {}

    def json(self, method, path, payload=None):
        FakeHTTP.payloads.append((path, json.loads(json.dumps(payload)) if payload is not None else None))
        if path == '/api/show':
            return {'capabilities': list(FakeHTTP.caps)}
        if path == '/models':
            return {'data': [{'id': 'x'}]}
        if path == '/chat/completions':
            return {'choices': [{'message': {'content': 'Réponse.'}, 'finish_reason': 'stop'}], 'usage': {'prompt_tokens': 10, 'completion_tokens': 5}}
        if path == '/messages':
            return {'content': [{'type': 'text', 'text': 'Réponse.'}], 'stop_reason': 'end_turn', 'usage': {'input_tokens': 12, 'output_tokens': 7}}
        if path == '/api/chat':
            return {'done': True, 'message': {'content': 'Réponse locale.'}}
        raise AssertionError(path)


def dernier(path):
    return [p for chemin, p in FakeHTTP.payloads if chemin == path][-1]


def images_envoyees(req, kind):
    """Images (octets) trouvées dans une requête, au format du fournisseur."""
    out = []
    for m in req.get('messages') or []:
        if kind == 'ollama':
            out += [base64.b64decode(x) for x in m.get('images') or []]
        elif isinstance(m.get('content'), list):
            for b in m['content']:
                if b.get('type') == 'image':
                    out.append(base64.b64decode(b['source']['data']))
                elif b.get('type') == 'image_url':
                    url = b['image_url'] if isinstance(b['image_url'], str) else b['image_url']['url']
                    out.append(base64.b64decode(url.split(',', 1)[1]))
    return out


def masquage_respecte(data):
    """Vrai si les mots sensibles sont noirs (0) et les mots sûrs restent lisibles (encre) dans l'image envoyée."""
    img = lire_png(data)
    w = img[0]
    f = w / float(W_OCR)
    for t, _, x, y, ww, hh, _ in MOTS:
        cx, cy = int((x + ww / 2) * f), int((y + hh / 2) * f)
        if t in SENSIBLES and pixel(img, cx, cy) != 0:
            return False
        if t in SURS and pixel(img, cx, cy) != ENCRE:
            return False
    return True


class Base(unittest.TestCase):
    def setUp(self):
        self.f = fixtures.EngineTests('test_observation_has_no_mail_write')
        self.f.setUp()
        self.addCleanup(self.f.doCleanups)
        self.d = Desk(self.f.c)
        self.c = self.f.c
        secret = Path(self.c['state_dir']) / 'k.secret'
        secret.write_text('cle-test\n')
        secret.chmod(0o600)
        for pid, kind, url, model in (('openai', 'openai', 'https://api.openai.com/v1', 'gpt-5'),
                                      ('anthropic', 'anthropic', 'https://api.anthropic.com/v1', 'claude-sonnet-5-5'),
                                      ('mistral', 'mistral', 'https://api.mistral.ai/v1', 'mistral-large-2512')):
            self.c.setdefault('ai_providers', {})[pid] = {'id': pid, 'type': kind, 'url': url, 'model': model, 'enabled': True,
                                                          'external_data_allowed': True, 'secret_file': str(secret), 'state_dir': self.c['state_dir'],
                                                          'monthly_budget_usd': 10, 'per_request_budget_usd': 1, 'input_usd_per_million': 1,
                                                          'output_usd_per_million': 1}
        FakeHTTP.payloads, FakeHTTP.caps = [], ['completion']
        pseudo540._CACHE['key'] = None
        V._CACHE.clear()
        for cible, valeur in (('agent.model.HTTP', FakeHTTP), ('agent.vision56106._rendu_outil', faux_rendu),
                              ('agent.vision56106._tesseract_tsv', faux_tsv), ('agent.vision56106.COTE', 400),
                              ('agent.vision56106.COTE_OCR', 800)):
            p = patch(cible, valeur)
            p.start()
            self.addCleanup(p.stop)

    def piece(self, page=1):
        return V.Piece(faux_pdf(), '/Dossiers/DEMO/pieces/scan.pdf', page, 'DOS-001', '/Dossiers/DEMO/pieces/scan.pdf')

    def externe(self, provider, model):
        from agent import ia540
        from support567 import authorize_external
        with patch('agent.ia540.Model', create=True):
            ia540.apply_mixed(self.d, provider, model, 'test-local', test=False)
        authorize_external(self.c)
        cfg = routed_config(self.c, 'assistant')
        self.assertEqual(cfg['provider_id'], provider)
        return cfg

    def appel(self, cfg, lot, texte='Que dit la page jointe ?'):
        with lot:
            return Model(cfg).complete([{'role': 'system', 'content': 'Consignes.'}, {'role': 'user', 'content': texte}], 0, 300)

    def lot(self, n=1, reserve=False):
        return V.Lot([self.piece() for _ in range(n)], self.c, 'DOS-001', reserve)

    def journal(self):
        return V.journal(self.c['state_dir'], 50)


# ================================================================================================ capacité « vision »
class Capacite(Base):
    def test_I1_liste_datee_et_exceptions(self):
        for modele, attendu in (('claude-sonnet-5-5', True), ('gpt-4o-mini', True), ('openai/gpt-5', True), ('pixtral-large-latest', True),
                                ('mistral-medium-2508', True), ('mistral-large-2512', True), ('gemma3:12b', True), ('gemma3:1b', False),
                                ('qwen2.5vl:7b', True), ('llama3.2-vision:11b', True), ('mistral-large-2411', False), ('o3-mini', False),
                                ('test-local', False), ('llama3.1:8b', False)):
            with self.subTest(modele=modele):
                self.assertEqual(V.connue(modele)[0], attendu)
        self.assertIn(V.LISTE_DU, V.capacite({'provider_type': 'openai', 'model': 'gpt-5'}, None, '')['libelle'])

    def test_I1_ollama_capabilities_puis_reglage_prioritaire(self):
        cfg = {'provider_type': 'ollama', 'provider_id': 'ollama', 'model': 'test-local'}
        self.assertTrue(V.capacite(cfg, ['completion', 'vision'], self.c['state_dir'])['vision'])
        self.assertFalse(V.capacite(cfg, ['completion'], self.c['state_dir'])['vision'])
        self.assertFalse(V.capacite(cfg, None, self.c['state_dir'])['vision'])          # absent de la liste
        self.d.setting(V.KEY, {'modeles': {'ollama|test-local': 'oui'}, 'lecture_auto_locale': False, 'version': 1})
        out = V.capacite(cfg, ['completion'], self.c['state_dir'])
        self.assertEqual((out['vision'], out['source']), (True, 'reglage'))
        self.d.setting(V.KEY, {'modeles': {'openai|gpt-5': 'non'}, 'version': 2})
        self.assertFalse(V.capacite({'provider_type': 'openai', 'provider_id': 'openai', 'model': 'gpt-5'}, None, self.c['state_dir'])['vision'])

    def test_I1_modele_sans_vision_ne_recoit_jamais_d_image(self):
        FakeHTTP.caps = ['completion']
        lot = self.lot()
        self.appel(routed_config(self.c, 'assistant'), lot)
        req = dernier('/api/chat')
        self.assertEqual(images_envoyees(req, 'ollama'), [])
        self.assertEqual(lot.dernier()['envoi'], 'texte_seul')
        self.assertEqual(lot.dernier()['motif'], 'modele_sans_vision')
        self.assertIn('ne lit pas les images', lot.message())
        self.assertEqual(self.journal()[0]['nature'], 'texte_seul')

    def test_I1_abonnement_chatgpt_texte_seul(self):
        class M:
            provider_type, cfg = 'chatgpt', {'provider_id': 'chatgpt', 'model': 'gpt-5', 'state_dir': ''}
        with self.lot() as lot:
            self.assertEqual(V.choisir(M), [])
        self.assertEqual(lot.dernier()['motif'], 'canal_sans_image')


# ================================================================================================ formats des fournisseurs
class Formats(Base):
    def test_I2_ollama_local_images_base64_sans_masquage(self):
        FakeHTTP.caps = ['completion', 'vision']
        lot = self.lot()
        self.appel(routed_config(self.c, 'assistant'), lot)
        req = dernier('/api/chat')
        user = [m for m in req['messages'] if m['role'] == 'user'][-1]
        self.assertIsInstance(user['content'], str)                      # le texte reste une chaîne
        self.assertEqual(len(user['images']), 1)
        data = base64.b64decode(user['images'][0], validate=True)        # base64 brut, sans préfixe data:
        self.assertTrue(data.startswith(b'\x89PNG'))
        img = lire_png(data)
        self.assertLessEqual(max(img[0], img[1]), V.COTE)
        f = img[0] / float(W_OCR)
        self.assertEqual(pixel(img, int(240 * f), int(50 * f)), ENCRE)   # KERVILAC lisible : modèle local, image telle quelle
        self.assertEqual(lot.dernier()['envoi'], 'image_locale')
        self.assertEqual(self.journal()[0]['nature'], 'image_locale')

    def test_I2_openai_image_url_objet_data_masquee(self):
        cfg = self.externe('openai', 'gpt-5')
        lot = self.lot()
        self.appel(cfg, lot, 'Madame Hélène KERVILAC demande une analyse.')
        req = dernier('/chat/completions')
        user = [m for m in req['messages'] if m['role'] == 'user'][-1]
        self.assertEqual(user['content'][0]['type'], 'text')
        bloc = user['content'][1]
        self.assertEqual(bloc['type'], 'image_url')
        self.assertTrue(bloc['image_url']['url'].startswith('data:image/png;base64,'))
        self.assertNotIn('KERVILAC', json.dumps(req, ensure_ascii=False))          # texte pseudonymisé comme avant
        (data,) = images_envoyees(req, 'openai')
        self.assertTrue(masquage_respecte(data))
        b = lot.dernier()
        self.assertEqual(b['envoi'], 'image_masquee')
        self.assertGreaterEqual(b['zones'], 4)
        self.assertIn('Image masquée avant envoi : %d zones noircies' % b['zones'], lot.message())
        self.assertIn('pas parfait', lot.message())

    def test_I2_mistral_image_url_chaine(self):
        cfg = self.externe('mistral', 'mistral-large-2512')
        self.appel(cfg, self.lot())
        user = [m for m in dernier('/chat/completions')['messages'] if m['role'] == 'user'][-1]
        bloc = user['content'][1]
        self.assertEqual(bloc['type'], 'image_url')
        self.assertIsInstance(bloc['image_url'], str)                    # Mistral : chaîne data:, pas un objet
        self.assertTrue(bloc['image_url'].startswith('data:image/png;base64,'))
        self.assertTrue(masquage_respecte(base64.b64decode(bloc['image_url'].split(',', 1)[1])))

    def test_I2_claude_bloc_image_base64_avant_le_texte(self):
        cfg = self.externe('anthropic', 'claude-sonnet-5-5')
        self.appel(cfg, self.lot())
        req = dernier('/messages')
        user = req['messages'][-1]
        self.assertEqual(user['content'][0]['type'], 'image')
        self.assertEqual(user['content'][0]['source']['type'], 'base64')
        self.assertEqual(user['content'][0]['source']['media_type'], 'image/png')
        self.assertEqual(user['content'][-1]['type'], 'text')
        self.assertTrue(masquage_respecte(base64.b64decode(user['content'][0]['source']['data'])))

    def test_I2_budget_compte_les_jetons_des_images(self):
        cfg = self.externe('anthropic', 'claude-sonnet-5-5')
        vus = []
        from agent import ai_gateway
        vrai = ai_gateway.estimate_and_check_budget

        def espion(c, messages, max_tokens):
            vus.append(sum(len(str(m.get('content') or '')) for m in messages))
            return vrai(c, messages, max_tokens)
        with patch('agent.ai_gateway.estimate_and_check_budget', espion):
            self.appel(cfg, self.lot())
            self.appel(cfg, V.Lot((), self.c))
        self.assertGreater(vus[0] - vus[1], 2 * 85 - 1)

    def test_I2_formats_png_jpeg_pdf(self):
        for w, h, c in ((5, 3, 1), (4, 4, 3)):
            px = bytearray((i * 7) % 256 for i in range(w * h * c))
            self.assertEqual(V.png_lire(V.png(w, h, c, px)), (w, h, c, px))
        # PNG avec alpha et filtre Sub/Paeth (décodeur Python) : composé sur blanc
        brut = b''.join(bytes([1]) + bytes([0, 0, 0, 0] * 2) for _ in range(2))
        ihdr = __import__('struct').pack('>IIBBBBB', 2, 2, 8, 6, 0, 0, 0)

        def bloc(k, d):
            return __import__('struct').pack('>I', len(d)) + k + d + __import__('struct').pack('>I', zlib.crc32(k + d) & 0xffffffff)
        rgba = b'\x89PNG\r\n\x1a\n' + bloc(b'IHDR', ihdr) + bloc(b'IDAT', zlib.compress(brut)) + bloc(b'IEND', b'')
        self.assertEqual(V.png_lire(rgba), (2, 2, 3, bytearray([255] * 12)))
        jpeg = b'\xff\xd8\xff\xe0\x00\x04ab\xff\xc0\x00\x0b\x08\x00\x20\x00\x30\x03\x01\x11\x00\xff\xd9'
        self.assertEqual(V.jpeg_info(jpeg)[:3], (0x30, 0x20, 3))
        self.assertEqual(V.nombre_pages(faux_pdf()), 1)
        with self.assertRaises(Stop):
            V.Piece(b'x', 'note.docx')
        with self.assertRaises(Stop):
            V.Piece(faux_pdf(), 'a.pdf', 2)


# ================================================================================================ confidentialité
class Confidentialite(Base):
    def test_I3_mots_sensibles_par_les_regles_existantes(self):
        mots = V.mots_ocr(faux_tsv(b''))
        retenus = {mots[i]['texte'] for i in V.sensibles(self.c, mots)}
        for t in SENSIBLES | {'griffonné'}:          # confiance 12 : noirci par prudence
            self.assertIn(t, retenus)
        for t in SURS:
            self.assertNotIn(t, retenus)

    def test_I3_ocr_en_panne_rien_vers_l_externe(self):
        cfg = self.externe('openai', 'gpt-5')

        def panne(_png):
            raise Stop('ocr_indisponible')
        with patch('agent.vision56106._tesseract_tsv', panne):
            lot = self.lot()
            self.appel(cfg, lot)
        req = dernier('/chat/completions')
        self.assertEqual(images_envoyees(req, 'openai'), [])
        self.assertTrue(all(isinstance(m['content'], str) for m in req['messages']))   # texte seul, pseudonymisé par la voie commune
        self.assertEqual(lot.dernier()['motif'], 'ocr_indisponible')
        self.assertIn('rien n’est envoyé à l’externe', lot.message())

    def test_I3_ocr_vide_ou_incertain_rien_vers_l_externe(self):
        cfg = self.externe('openai', 'gpt-5')
        for tsv, motif in (('level\n', 'ocr_sans_texte'),
                           ('h\n5\t1\t1\t1\t1\t1\t1\t1\t5\t5\t20\tflou\n5\t1\t1\t1\t1\t2\t9\t1\t5\t5\t30\tencore\n', 'ocr_trop_incertain')):
            with self.subTest(motif=motif), patch('agent.vision56106._tesseract_tsv', lambda _p, t=tsv: t):
                V._CACHE.clear()
                lot = self.lot()
                self.appel(cfg, lot)
                self.assertEqual(images_envoyees(dernier('/chat/completions'), 'openai'), [])
                self.assertEqual(lot.dernier()['motif'], motif)

    def test_I3_masquage_en_panne_rien_vers_l_externe(self):
        cfg = self.externe('openai', 'gpt-5')
        with patch('agent.pseudo540.Pseudonymizer.text', side_effect=RuntimeError('panne')):
            lot = self.lot()
            with lot:
                V.choisir(type('M', (), {'provider_type': 'openai', 'cfg': cfg})())
        self.assertEqual(lot.dernier()['motif'], 'masquage_en_echec')

    def test_I3_dossier_reserve_jamais_d_image_externe_meme_masquee(self):
        cfg = self.externe('openai', 'gpt-5')
        lot = self.lot(reserve=True)
        self.appel(cfg, lot)
        self.assertEqual(images_envoyees(dernier('/chat/completions'), 'openai'), [])
        self.assertEqual(lot.dernier()['motif'], 'dossier_reserve')
        # le même dossier réservé, modèle local capable de voir : l'image part telle quelle
        FakeHTTP.caps = ['completion', 'vision']
        cfg_local = dict(routed_config(self.c, 'mail_triage'))
        self.assertEqual(cfg_local['provider_type'], 'ollama')
        lot2 = self.lot(reserve=True)
        self.appel(cfg_local, lot2)
        self.assertEqual(len(images_envoyees(dernier('/api/chat'), 'ollama')), 1)

    def test_I3_reserve_lu_dans_le_registre_des_membres(self):
        from agent import membres5650
        self.assertFalse(V.reserve(self.c, 'DOS-001'))
        membres5650.set_reserve(self.c['state_dir'], 'DOS-001', ['avocat1'])
        self.assertTrue(V.reserve(self.c, 'DOS-001'))
        with patch('agent.membres5650.load', side_effect=OSError('panne')):
            self.assertTrue(V.reserve(self.c, 'DOS-002'))          # contrôle en panne : traité comme réservé

    def test_I3_politique_des_donnees_appliquee_a_l_image(self):
        cfg = self.externe('openai', 'gpt-5')
        refus = {'allowed': False, 'denial_reasons': ['categorie_fermee:documents']}
        with patch('agent.data_policy5689.evaluer', return_value=refus):
            lot = self.lot()
            with lot:
                V.choisir(type('M', (), {'provider_type': 'openai', 'cfg': cfg})())
        self.assertEqual(lot.dernier()['motif'], 'politique_donnees')
        self.assertEqual(lot.dernier()['images'], 0)

    def test_I3_mutation_masquage_contourne_dans_choisir_est_bloque_au_transport(self):
        """Mutation en mémoire : ``choisir`` donne l'image NON masquée au fournisseur externe ; le second verrou de ``monter`` refuse."""
        cfg = self.externe('openai', 'gpt-5')
        with patch.object(V.Piece, 'image_masquee', lambda self, config: self.image_locale()):
            with self.assertRaises(Stop) as ctx:
                self.appel(cfg, self.lot())
        self.assertEqual(str(ctx.exception), 'image_non_masquee_refusee')
        self.assertFalse([p for p, _ in FakeHTTP.payloads if p == '/chat/completions'])   # rien n'est parti

    def test_I3_mutation_destination_externe_prise_pour_locale_serait_detectee(self):
        """Mutation en mémoire des deux verrous (destination externe prise pour locale) : l'image partirait non masquée, et le contrôle
        ``masquage_respecte`` de ces essais le détecte. Sans mutation, le même contrôle passe (essais précédents)."""
        cfg = self.externe('openai', 'gpt-5')
        with patch.object(V, '_locale', lambda model: True), patch.object(V, 'capacite', lambda *a, **k: {'vision': True, 'source': 'liste', 'libelle': ''}):
            self.appel(cfg, self.lot())
        (data,) = images_envoyees(dernier('/chat/completions'), 'openai')
        self.assertFalse(masquage_respecte(data))

    def test_I3_journal_sans_contenu_d_image(self):
        cfg = self.externe('openai', 'gpt-5')
        self.appel(cfg, self.lot())
        j = self.journal()[0]
        self.assertEqual((j['nature'], j['images']), ('image_masquee', 1))
        self.assertGreater(j['octets'], 0)
        self.assertGreaterEqual(j['zones'], 4)
        db = sqlite3.connect(Path(self.c['state_dir']) / 'desk.sqlite3')
        try:
            colonnes = [r[1] for r in db.execute('PRAGMA table_info(vision56106_transmissions)')]
            self.assertNotIn('image', ' '.join(c for c in colonnes if c != 'images'))
            dump = '\n'.join(db.iterdump())
        finally:
            db.close()
        self.assertNotIn('iVBORw0KGgo', dump)              # aucune image PNG en base64 dans la base
        self.assertNotIn('data:image', dump)
        self.assertNotIn('\\x89PNG', dump)


# ================================================================================================ plafonds
class Plafonds(Base):
    def test_I2_au_plus_quatre_images_par_demande(self):
        lot = V.Lot([self.piece() for _ in range(6)], self.c, 'DOS-001')
        self.assertEqual((len(lot.pieces), lot.trop), (4, 2))
        FakeHTTP.caps = ['completion', 'vision']
        self.appel(routed_config(self.c, 'assistant'), lot)
        self.assertEqual(len(images_envoyees(dernier('/api/chat'), 'ollama')), 4)
        self.assertIn('au-delà de 4', lot.message())

    def test_I2_poids_total_et_poids_par_image(self):
        FakeHTTP.caps = ['completion', 'vision']
        with patch.object(V, 'MAX_OCTETS_TOTAL', 1):
            lot = self.lot(2)
            self.appel(routed_config(self.c, 'assistant'), lot)
        self.assertEqual(images_envoyees(dernier('/api/chat'), 'ollama'), [])
        self.assertEqual(lot.dernier()['motif'], 'images_trop_lourdes')
        with patch.object(V, 'MAX_OCTETS_IMAGE', 10):
            with self.assertRaises(Stop) as ctx:
                self.piece().image_locale()
        self.assertEqual(str(ctx.exception), 'image_trop_lourde')

    def test_I2_choix_valide_dans_le_dossier_seulement(self):
        m = {'id': 'DOS-001', 'path': '/Dossiers/DEMO'}
        self.assertEqual(V.valider_choix([{'chemin': '/Dossiers/DEMO/a.pdf', 'page': '2'}], m), [{'chemin': '/Dossiers/DEMO/a.pdf', 'page': 2}])
        for liste, code in (([{'chemin': '/Dossiers/AUTRE/a.pdf'}], 'source_mission_autre_dossier'), ([{'chemin': '/Dossiers/DEMO/a.docx'}], 'format_image_non_pris_en_charge'),
                            ([{'chemin': '/Dossiers/DEMO/a.pdf'}] * 5, 'trop_d_images'), ([{'chemin': '/Dossiers/DEMO/a.pdf', 'page': 0}], 'page_invalide')):
            with self.subTest(code=code):
                with self.assertRaises(Stop) as ctx:
                    V.valider_choix(liste, m)
                self.assertEqual(str(ctx.exception), code)
        with self.assertRaises(Stop):
            V.valider_choix([{'chemin': '/Dossiers/DEMO/a.pdf'}], None)


# ================================================================================================ routes, droits, réglages, lecture
class FausseDAV:
    def __init__(self, fichiers):
        self.fichiers = fichiers

    def stat(self, chemin):
        if chemin not in self.fichiers:
            raise Stop('http_404')
        return {'path': chemin}

    def download(self, item):
        return self.fichiers[item['path']]


def env_de(methode='GET', role='avocat', owner='avocat1', corps=b''):
    import io
    return {'REQUEST_METHOD': methode, 'axiorhub.user': owner, 'axiorhub.role': role, 'HTTP_ORIGIN': 'https://axiorhub.example.test',
            'HTTP_X_CSRF_TOKEN': 'jeton', 'CONTENT_TYPE': 'application/json', 'CONTENT_LENGTH': str(len(corps)), 'wsgi.input': io.BytesIO(corps)}


AUTH = {'origin': 'https://axiorhub.example.test', 'csrf': 'jeton'}


class _AvecFichiers(Base):
    def setUp(self):
        super().setUp()
        self.dav = FausseDAV({'/Dossiers/DEMO/pieces/scan.pdf': faux_pdf()})
        p = patch('agent.vision56106._dav', lambda desk: self.dav)
        p.start()
        self.addCleanup(p.stop)
        from agent.modele5651 import ensure_schema
        ensure_schema(self.d)
        self.d.db.execute("INSERT OR REPLACE INTO d51_documents(matter,path,etag,name,reading,modified,updated) "
                          "VALUES('DOS-001','/Dossiers/DEMO/pieces/scan.pdf','e','scan.pdf','erreur','2026-11-01','2026-11-01')")
        self.d.db.commit()

    def api(self, nom, methode='GET', args=None, data=None, role='avocat', owner='avocat1'):
        corps = json.dumps(data).encode() if data is not None else b''
        with patch('agent.web567.actor', return_value=(owner, role)):
            out = V.api(env_de(methode, role, owner, corps), self.d, AUTH, '', V.PREFIXE + nom, args or {}, methode)
        return out


class Routes(_AvecFichiers):
    def test_I4_droits_declares(self):
        self.assertTrue(V.allowed('assistant', 'POST', 'm56106/vision/apercu'))
        self.assertFalse(V.allowed('avocat', 'POST', 'm56106/vision/reglages'))
        self.assertTrue(V.allowed('administrateur', 'POST', 'm56106/vision/reglages'))
        self.assertFalse(V.allowed('client', 'GET', 'm56106/vision/fichiers'))
        self.assertFalse(V.allowed('avocat', 'POST', 'm56106/vision/autre'))
        from agent import standalone_auth
        self.assertTrue(standalone_auth.allowed('avocat', 'POST', '/api440/m56106/vision/lire'))
        self.assertFalse(standalone_auth.allowed('avocat', 'POST', '/api440/m56106/vision/reglages'))
        from agent import web440
        self.assertIn('/static/v56106-vision.js', web440.STATIC)

    def test_I4_fichiers_vignette_et_404_neutre(self):
        out = self.api('fichiers', args={'d': 'DOS-001'})
        self.assertEqual(out['status'], '200 OK')
        self.assertIn(('Cache-Control', 'private, no-store'), out['headers'])
        self.assertEqual(json.loads(out['body'])['fichiers'][0]['nom'], 'scan.pdf')
        out = self.api('vignette', args={'d': 'DOS-001', 'p': '/Dossiers/DEMO/pieces/scan.pdf', 'page': '1'})
        self.assertEqual(out['kind'], 'image/png')
        absent = self.api('fichiers', args={'d': 'DOS-999'})
        from agent import membres5650
        membres5650.set_reserve(self.c['state_dir'], 'DOS-001', ['autre'])
        interdit = self.api('fichiers', args={'d': 'DOS-001'})
        hors = self.api('vignette', args={'d': 'DOS-001', 'p': '/Dossiers/AUTRE/x.pdf'})
        for out in (absent, interdit, hors):
            self.assertEqual((out['status'], json.loads(out['body'])['error']), ('404 Not Found', 'introuvable'))
        self.assertEqual(absent['body'], interdit['body'])

    def test_I4_controle_en_panne_503(self):
        with patch('agent.membres5650.hidden_for', side_effect=OSError('panne')):
            out = self.api('fichiers', args={'d': 'DOS-001'})
        self.assertEqual(out['status'], '503 Service Unavailable')

    def test_I4_apercu_externe_masque_sans_effet(self):
        self.externe('openai', 'gpt-5')
        avant = len(self.journal())
        out = self.api('apercu', 'POST', data={'d': 'DOS-001', 'p': '/Dossiers/DEMO/pieces/scan.pdf', 'page': 1})
        v = json.loads(out['body'])
        self.assertEqual(v['envoi'], 'image_masquee')
        self.assertIn('Image masquée avant envoi : %d zones noircies' % v['zones'], v['message'])
        self.assertTrue(masquage_respecte(base64.b64decode(v['image'].split(',', 1)[1])))
        self.assertEqual(len(self.journal()), avant)          # un aperçu n'envoie rien
        self.assertFalse([p for p, _ in FakeHTTP.payloads if p in ('/chat/completions', '/messages')])

    def test_I4_apercu_csrf_refuse(self):
        corps = json.dumps({'d': 'DOS-001', 'p': '/Dossiers/DEMO/pieces/scan.pdf'}).encode()
        env = env_de('POST', corps=corps)
        env['HTTP_X_CSRF_TOKEN'] = 'faux'
        with patch('agent.web567.actor', return_value=('avocat1', 'avocat')):
            out = V.api(env, self.d, AUTH, '', V.PREFIXE + 'apercu', {}, 'POST')
        self.assertEqual(out['status'], '403 Forbidden')

    def test_I4_reglages_administrateur_409_et_idempotence(self):
        refuse = self.api('reglages', 'POST', data={'version': 0, 'cle': 'cle-reglage-1'}, role='avocat')
        self.assertEqual(refuse['status'], '403 Forbidden')
        data = {'version': 0, 'cle': 'cle-reglage-1', 'vision:ollama|test-local': 'oui', 'lecture_auto_locale': 'oui'}
        ok = self.api('reglages', 'POST', data=data, role='administrateur', owner='admin')
        self.assertEqual(ok['status'], '200 OK')
        again = self.api('reglages', 'POST', data=data, role='administrateur', owner='admin')
        self.assertEqual(json.loads(again['body']), json.loads(ok['body']))           # même clé, même réponse, rien de rejoué
        r = V.reglages_de(self.c['state_dir'])
        self.assertEqual((r['modeles'], r['lecture_auto_locale'], r['version']), ({'ollama|test-local': 'oui'}, True, 1))
        perime = self.api('reglages', 'POST', data={**data, 'cle': 'cle-reglage-2'}, role='administrateur', owner='admin')
        self.assertEqual(perime['status'], '409 Conflict')
        autre = self.api('reglages', 'POST', data={**data, 'version': 1}, role='administrateur', owner='admin')
        self.assertEqual(autre['status'], '409 Conflict')                              # clé réutilisée pour une autre demande

    def test_I4_lecture_demandee_mise_en_file_une_fois(self):
        files = []
        with patch.object(self.d, 'enqueue', lambda kind, args=None, **k: files.append((kind, args, k)) or 'job1'):
            out = self.api('lire', 'POST', data={'d': 'DOS-001', 'p': '/Dossiers/DEMO/pieces/scan.pdf', 'page': 1, 'cle': 'cle-lecture-01'})
            self.assertEqual(out['status'], '200 OK')
            again = self.api('lire', 'POST', data={'d': 'DOS-001', 'p': '/Dossiers/DEMO/pieces/scan.pdf', 'page': 1, 'cle': 'cle-lecture-01'})
            deux = self.api('lire', 'POST', data={'d': 'DOS-001', 'p': '/Dossiers/DEMO/pieces/scan.pdf', 'page': 1, 'cle': 'cle-lecture-02'})
        self.assertEqual(json.loads(again['body']), json.loads(out['body']))
        self.assertEqual(deux['status'], '409 Conflict')
        self.assertEqual(len(files), 1)
        self.assertEqual(files[0][2].get('origin'), 'user')

    def test_I4_job_lecture_locale_et_transcription(self):
        FakeHTTP.caps = ['completion', 'vision']
        with patch.object(self.d, 'enqueue', lambda *a, **k: 'job1'):
            ident = V.demander_lecture(self.d, 'avocat1', 'avocat', 'DOS-001', '/Dossiers/DEMO/pieces/scan.pdf', 1)
        out = V.job(self.d, {'lecture': ident})
        self.assertEqual(out['etat'], 'fait')
        row = self.d.db.execute('SELECT * FROM vision56106_lectures WHERE id=?', (ident,)).fetchone()
        self.assertEqual(row['texte'], 'Réponse locale.')
        self.assertEqual(len(images_envoyees(dernier('/api/chat'), 'ollama')), 1)
        self.assertEqual(V.job(self.d, {'lecture': ident})['etat'], 'fait')   # rejouée : aucune nouvelle inférence
        html = V.illisibles_html(self.d, '', 'DOS-001')
        self.assertIn('Transcription', html)
        self.assertIn('Réponse locale.', html)

    def test_I4_job_sans_modele_capable_de_voir_echec_dit(self):
        FakeHTTP.caps = ['completion']
        with patch.object(self.d, 'enqueue', lambda *a, **k: 'job1'):
            ident = V.demander_lecture(self.d, 'avocat1', 'avocat', 'DOS-001', '/Dossiers/DEMO/pieces/scan.pdf', 1)
        out = V.job(self.d, {'lecture': ident})
        self.assertEqual(out['etat'], 'echec')
        self.assertIn('ne lit pas les images', out['message'])

    def test_I4_lecture_automatique_desactivee_par_defaut_puis_locale_seulement(self):
        files = []
        with patch.object(self.d, 'enqueue', lambda kind, args=None, **k: files.append(k) or 'job1'):
            self.assertIsNone(V.illisible_constate(self.d, 'DOS-001', '/Dossiers/DEMO/pieces/scan.pdf', 'scan.pdf'))   # réglage désactivé
            self.d.setting(V.KEY, {'modeles': {'ollama|test-local': 'oui'}, 'lecture_auto_locale': True, 'version': 1})
            self.externe('openai', 'gpt-5')
            self.assertEqual(routed_config(self.c, 'attachment_review')['provider_type'], 'ollama')
            self.assertIsNotNone(V.illisible_constate(self.d, 'DOS-001', '/Dossiers/DEMO/pieces/scan.pdf', 'scan.pdf'))
            self.assertIsNone(V.illisible_constate(self.d, 'DOS-001', '/Dossiers/DEMO/pieces/scan.pdf', 'scan.pdf'))   # une seule fois
        self.assertEqual(files[0].get('origin'), 'automatic')
        # fonction de lecture routée vers l'externe : aucune lecture automatique
        self.d.db.execute('DELETE FROM vision56106_lectures')
        with patch('agent.vision56106.destination', return_value={'local': False, 'vision': True}), \
                patch.object(self.d, 'enqueue', lambda *a, **k: files.append(k) or 'job2'):
            self.assertIsNone(V.illisible_constate(self.d, 'DOS-001', '/Dossiers/DEMO/pieces/scan.pdf', 'scan.pdf'))
        self.assertEqual(len(files), 1)

    def test_I4_job_automatique_impose_le_modele_local(self):
        from agent.model import local_seul_actif
        vus = []
        with patch.object(self.d, 'enqueue', lambda *a, **k: 'job1'):
            ident = V.demander_lecture(self.d, 'cabinet', 'administrateur', 'DOS-001', '/Dossiers/DEMO/pieces/scan.pdf', 1, automatique=True)

        def route(config, purpose):
            vus.append(local_seul_actif())
            raise Stop('modele_local_requis')
        with patch('agent.model.routed_config', route):
            out = V.job(self.d, {'lecture': ident})
        self.assertEqual(vus, [True])
        self.assertEqual(out['etat'], 'bloque')

    def test_I4_conversation_lot_et_annotation(self):
        matter = {'id': 'DOS-001', 'path': '/Dossiers/DEMO'}
        args = {'page_context': {'images56106': [{'chemin': '/Dossiers/DEMO/pieces/scan.pdf', 'page': 1}, {'chemin': '/Dossiers/DEMO/absent.pdf', 'page': 1}]}}
        lot = V.conversation(self.d, args, matter, self.dav)
        self.assertEqual(len(lot.pieces), 1)
        self.assertIn('absent.pdf', lot.erreurs[0])
        charge = V.charge_utile(lot)
        self.assertIn('KERVILAC', charge[0]['texte_de_la_page'])      # texte de repli (OCR), pseudonymisé ensuite par la voie commune
        FakeHTTP.caps = ['completion', 'vision']
        self.appel(routed_config(self.c, 'assistant'), lot)
        res = V.annoter(lot, {'answer': 'Réponse.', 'limits': []}, True)
        self.assertIn('Image envoyée telle quelle au modèle local', res['answer'])
        self.assertEqual(res['vision56106']['envoi'], 'image_locale')
        self.assertEqual(V.annoter(V.Lot((), self.c), {'answer': 'x', 'limits': []}, True), {'answer': 'x', 'limits': []})

    def test_I4_reglages_html_et_illisibles_html(self):
        html = V.reglages_html(self.d, '', 'administrateur')
        for attendu in ('Images pour les modèles capables de voir', 'pas parfait', 'data-api="m56106/vision/reglages"', V.LISTE_DU):
            self.assertIn(attendu, html)
        self.assertNotIn('data-api="m56106/vision/reglages"', V.reglages_html(self.d, '', 'avocat'))
        html = V.illisibles_html(self.d, '', 'DOS-001')
        self.assertIn('Lire avec un modèle capable de voir', html)
        self.assertIn('name="p"', html)


# ================================================================================================ points de montage (assistant)
class Montage(_AvecFichiers):
    def test_I4_contexte_de_page_et_mission_gardent_le_choix(self):
        from agent import integration, missions567
        ctx = integration._page_context({'images56106': [{'chemin': '/Dossiers/DEMO/pieces/scan.pdf', 'page': '2'}, 'x']}, 'DOS-001')
        self.assertEqual(ctx['images56106'], [{'chemin': '/Dossiers/DEMO/pieces/scan.pdf', 'page': 2}])
        matter = {'id': 'DOS-001', 'path': '/Dossiers/DEMO', 'client_name': 'Client DEMO'}
        out = missions567._context(self.d, {'context': {'images56106': [{'chemin': '/Dossiers/DEMO/pieces/scan.pdf', 'page': 1}]}}, matter)
        self.assertEqual(out['images56106'], [{'chemin': '/Dossiers/DEMO/pieces/scan.pdf', 'page': 1}])
        with self.assertRaises(Stop):
            missions567._context(self.d, {'context': {'images56106': [{'chemin': '/Dossiers/AUTRE/x.pdf'}]}}, matter)

    def test_I4_conversation_de_l_assistant_joint_les_images(self):
        from agent import workspace
        vus = {}

        class M:
            def ask(self, stage, data):
                lot = V.lot_courant()
                vus['lot'] = lot is not None and len(lot.pieces)
                vus['charge'] = data.get('images_jointes')
                lot.bilan.append({'envoi': 'image_masquee', 'zones': 3, 'images': 1, 'motif': '', 'fournisseur': 'openai', 'modele': 'gpt-5'})
                return {'answer': 'Réponse fictive.', 'source_ids': [], 'limits': [], 'proposed_actions': []}
        args = {'matter': 'DOS-001', 'key': '', 'question': 'Que dit la page jointe ?', 'rapide': True, 'purpose': 'assistant', 'societe5688': {},
                'page_context': {'images56106': [{'chemin': '/Dossiers/DEMO/pieces/scan.pdf', 'page': 1}]}}
        with patch('agent.vueia5688.blocked', lambda desk, cat: cat == 'documents'),                 patch('agent.acces5683.gather', lambda *a, **k: ([], {}, False)),                 patch('agent.juridique5649.research', lambda *a, **k: {}),                 patch('agent.rapide5680.legal_research', lambda *a, **k: {}):
            out = workspace.chat(self.d, args, self.dav, M(), None, return_result=True)
        self.assertEqual(vus['lot'], 1)
        self.assertEqual(vus['charge'][0]['piece'], 'scan.pdf — page 1')
        self.assertIn('Image masquée avant envoi : 3 zones noircies', out['answer'])
        self.assertIsNone(V.lot_courant())                  # le lot ne survit pas à la demande


# ================================================================================================ vrais outils (sautés s'ils sont absents)
class VraisOutils(unittest.TestCase):
    def setUp(self):
        if not V._outil('pdftoppm'):
            raise unittest.SkipTest('Poppler (pdftoppm) non installé sur ce poste')

    def test_I2_rendu_reel_d_une_page(self):
        w, h, c, px = V.pnm(V._rendu_outil(faux_pdf(), 1, 200, False))
        self.assertEqual(max(w, h), 200)

    def test_I3_ocr_reel(self):
        if not V._outil('tesseract'):
            raise unittest.SkipTest('Tesseract non installé sur ce poste')
        piece = V.Piece(faux_pdf(), 'blanc.pdf')
        with self.assertRaises(Stop):           # page blanche : aucun mot, donc aucun envoi externe possible
            piece.image_masquee({})


if __name__ == '__main__':
    unittest.main()
