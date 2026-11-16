"""5.6.103 (lot T) : tableau blanc (Excalidraw embarqué) dans la Messagerie instantanée.

Données fictives (comptes example.test, dossier DOS-001) ; modèle factice ; aucun appel réseau. Le navigateur (Excalidraw, conversion
Mermaid, sondage) n'est pas exécuté ici : le fichier assemblé est contrôlé (aucun CDN, empreintes), le parcours facultatif est
``scripts/parcours_p103t.py``.
"""
import base64
import hashlib
import importlib.util
import json
from pathlib import Path
import re
import unittest
from unittest.mock import patch

from agent import membres5650 as mb, messagerie5650 as mx, pilote_messagerie56102 as pm, tableau56103 as tb
from agent.common import Stop
from agent.desk import Desk
import test_agent as fixtures
import test_v440_web as tw

ROOT = Path(__file__).resolve().parents[1]
AVOCATE, ASSISTANT, AUTRE = 'avocate@example.test', 'assistant@example.test', 'autre@example.test'
GENS = ((AVOCATE, 'Me DURAND', 'avocat'), (ASSISTANT, 'Paul MARTIN', 'assistant'), (AUTRE, 'Lea BERNARD', 'avocat'), ('cabinet', 'admin', 'administrateur'))
PNG = b'\x89PNG\r\n\x1a\n' + b'\x00' * 64
ORGANIGRAMME = 'flowchart TD\n  A["Associé DURAND"] --> B["Collaboratrice MARTIN"]\n  A --> C["Assistant BERNARD"]'


def elt(ident, version=1, nonce=100, kind='rectangle', **kw):
    return {'id': ident, 'type': kind, 'x': 0, 'y': 0, 'width': 10, 'height': 10, 'angle': 0, 'version': version, 'versionNonce': nonce,
            'isDeleted': False, 'index': 'a0', **kw}


def data_url(brut, mime='image/png'):
    return 'data:%s;base64,%s' % (mime, base64.b64encode(brut).decode())


class FauxModele:
    def __init__(self, sortie, appels):
        self.sortie, self.appels = sortie, appels

    def complete(self, messages, temperature=None, max_tokens=3500, json_schema=None):
        self.appels.append((messages, json_schema))
        if isinstance(self.sortie, Exception):
            raise self.sortie
        return json.dumps(self.sortie)


class Base(unittest.TestCase):
    def setUp(self):
        self.f = fixtures.EngineTests(methodName='test_observation_has_no_mail_write')
        self.f.setUp()
        self.addCleanup(self.f.doCleanups)
        self.addCleanup(mb.clear)
        self.d = Desk(self.f.c)
        mx.ensure_schema(self.d)
        pm.ensure_schema(self.d)
        tb.ensure_schema(self.d)
        self.d.db.execute(mb.SCHEMA)
        for owner, name, role in GENS:
            self.d.db.execute("INSERT OR REPLACE INTO membres5650(owner,name,role,first_seen,last_seen) VALUES(?,?,?,'2026-11-15','2026-11-15')", (owner, name, role))
        self.d.db.commit()
        self.members = mb.directory(self.d)
        self.roles = {o: r for o, _, r in GENS}
        self.tid = mx.create_group(self.d, AVOCATE, 'Pôle contentieux', [ASSISTANT], self.members)
        self.appels = []

    def stocke(self, ident):
        r = self.d.db.execute('SELECT data FROM tableau56103_elements WHERE thread=? AND id=?', (self.tid, ident)).fetchone()
        return json.loads(r['data']) if r else None


# ---------------------------------------------------------------------------------------------------- T01 fusion concurrente, T02 409
class Fusion(Base):
    def test_T01_deux_participants_elements_differents_meme_base(self):
        a = tb.envoyer(self.d, AVOCATE, self.tid, 0, [elt('rectA')])
        b = tb.envoyer(self.d, ASSISTANT, self.tid, 0, [elt('rectB')])      # même base, autre élément : fusion sans conflit
        self.assertEqual((a['version'], b['version']), (1, 2))
        self.assertEqual([e['id'] for e in b['elements']], ['rectA'])           # la personne reçoit ce qu'elle n'avait pas
        lu = tb.lire_scene(self.d, ASSISTANT, self.tid, depuis=0)
        self.assertEqual(sorted(e['id'] for e in lu['elements']), ['rectA', 'rectB'])
        self.assertEqual(tb.lire_scene(self.d, AVOCATE, self.tid, depuis=2)['inchange'], True)

    def test_T01_regle_d_excalidraw_version_puis_nonce(self):
        tb.envoyer(self.d, AVOCATE, self.tid, 0, [elt('r', version=3, nonce=50, x=1)])
        out = tb.envoyer(self.d, ASSISTANT, self.tid, 1, [elt('r', version=2, nonce=1, x=2)])    # version plus basse : ignorée
        self.assertEqual((out['appliques'], out['ignores']), (0, 1))
        self.assertEqual(self.stocke('r')['x'], 1)
        tb.envoyer(self.d, ASSISTANT, self.tid, 1, [elt('r', version=3, nonce=40, x=3)])         # même version, nonce plus petit : gagne
        self.assertEqual(self.stocke('r')['x'], 3)
        tb.envoyer(self.d, AVOCATE, self.tid, 2, [elt('r', version=3, nonce=45, x=4)])           # même version, nonce plus grand : perd
        self.assertEqual(self.stocke('r')['x'], 3)
        self.assertTrue(tb.gagne({'version': 4, 'versionNonce': 999}, {'version': 3, 'nonce': 1}))
        self.assertFalse(tb.gagne({'version': 3, 'versionNonce': 7}, {'version': 3, 'nonce': 7}))

    def test_T02_409_meme_element_change_depuis_la_base(self):
        tb.envoyer(self.d, AVOCATE, self.tid, 0, [elt('r', version=1)])
        tb.envoyer(self.d, AVOCATE, self.tid, 1, [elt('r', version=2, x=5)])
        with self.assertRaises(tb.Conflit) as c:                                 # l'assistant modifie « r » depuis la version 1
            tb.envoyer(self.d, ASSISTANT, self.tid, 1, [elt('r', version=2, nonce=7, x=9)])
        donnees = c.exception.donnees
        self.assertEqual((donnees['error'], donnees['version'], donnees['conflits']), ('version_perimee', 2, ['r']))
        self.assertEqual(donnees['elements'][0]['x'], 5)                          # changements manqués, pour la fusion du navigateur
        self.assertEqual(self.stocke('r')['x'], 5)                                # rien d'appliqué
        out = tb.envoyer(self.d, ASSISTANT, self.tid, 2, [elt('r', version=3, nonce=7, x=9)])    # après fusion : nouvelle base
        self.assertEqual((out['appliques'], self.stocke('r')['x']), (1, 9))

    def test_T02_409_base_trop_ancienne_ou_inconnue(self):
        with patch.object(tb, 'ECART_MAX', 2):
            for i in range(4):
                tb.envoyer(self.d, AVOCATE, self.tid, i, [elt('e%d' % i)])
            with self.assertRaises(tb.Conflit) as c:                             # 4 versions de retard > 2 : rechargement complet
                tb.envoyer(self.d, ASSISTANT, self.tid, 1, [elt('neuf')])
            self.assertTrue(c.exception.donnees['complet'])
            self.assertEqual(len(c.exception.donnees['elements']), 4)
        with self.assertRaisesRegex(Stop, 'version_inconnue'):
            tb.envoyer(self.d, AVOCATE, self.tid, 99, [elt('x')])

    def test_T02_mutation_sans_controle_de_base_l_ecrasement_serait_silencieux(self):
        """Garde-fou : sans le contrôle de la version de base, un envoi périmé passerait sans 409 (le test ci-dessus échouerait)."""
        tb.envoyer(self.d, AVOCATE, self.tid, 0, [elt('r', version=1)])
        tb.envoyer(self.d, AVOCATE, self.tid, 1, [elt('r', version=2, x=5)])
        source = Path(tb.__file__).read_text(encoding='utf-8')
        self.assertIn('if changes or version - base > ECART_MAX:', source)
        mutant = source.replace('if changes or version - base > ECART_MAX:', 'if False:')
        spec = importlib.util.spec_from_loader('agent.tableau56103_mutant', loader=None)
        module = importlib.util.module_from_spec(spec)
        module.__package__ = 'agent'
        exec(compile(mutant, 'tableau56103_mutant', 'exec'), module.__dict__)
        out = module.envoyer(self.d, ASSISTANT, self.tid, 1, [elt('r', version=3, nonce=7, x=9)])
        self.assertEqual(out['appliques'], 1)                                     # le mutant accepte : c'est bien le contrôle qui protège


# ---------------------------------------------------------------------------------------------------- T03 droits
class Droits(Base):
    def test_T03_non_participant_meme_refus_qu_un_fil_absent(self):
        tb.envoyer(self.d, AVOCATE, self.tid, 0, [elt('r')])
        for appel in (lambda: tb.lire_scene(self.d, AUTRE, self.tid), lambda: tb.envoyer(self.d, AUTRE, self.tid, 1, [elt('z')]),
                      lambda: tb.deposer_image(self.d, AUTRE, self.tid, 'img-00000001', 'image/png', data_url(PNG)),
                      lambda: tb.lire_scene(self.d, AVOCATE, 999999)):
            with self.assertRaisesRegex(Stop, 'conversation_absente'):
                appel()
        self.assertIsNone(self.stocke('z'))

    def test_T03_dossier_reserve_et_fil_de_dossier(self):
        fil = mx.matter_thread(self.d, 'DOS-001', AVOCATE)
        tb.envoyer(self.d, AVOCATE, fil, 0, [elt('d')])
        self.assertEqual(len(tb.lire_scene(self.d, AUTRE, fil)['elements']), 1)  # fil de dossier : qui voit le dossier voit le tableau
        with self.assertRaisesRegex(Stop, 'conversation_absente'):
            tb.lire_scene(self.d, AUTRE, fil, frozenset({'DOS-001'}))            # dossier réservé : rien n'est révélé

    def test_T03_mutation_sans_controle_de_droits_l_acces_serait_ouvert(self):
        with patch.object(mx, 'can_read', lambda *a, **k: True):
            out = tb.envoyer(self.d, AUTRE, self.tid, 0, [elt('intrus')])
        self.assertEqual(out['appliques'], 1)                                     # le contrôle retiré, l'écriture passe : le test T03 le verrait

    def test_T03_roles_et_identite_de_pilote(self):
        self.assertTrue(tb.allowed('assistant', 'POST', 'm56103/tableau/envoyer'))
        self.assertTrue(tb.allowed('avocat', 'GET', 'm56103/tableau/scene'))
        self.assertFalse(tb.allowed('ia', 'GET', 'm56103/tableau/scene'))
        self.assertFalse(tb.allowed('avocat', 'GET', 'm56103/tableau/envoyer'))
        env = {'HTTP_X_AXIORHUB_USER': 'pilote', 'HTTP_X_AXIORHUB_ROLE': 'avocat', 'REQUEST_METHOD': 'GET'}
        r = tb.api(env, self.d, {'origin': 'https://cabinet.example.test', 'csrf': 'x'}, '/p', 'm56103/tableau/scene', {'fil': str(self.tid)}, 'GET')
        self.assertEqual(r['status'], '403 Forbidden')


# ---------------------------------------------------------------------------------------------------- T04 tailles et contenus
class Tailles(Base):
    def test_T04_types_liens_et_apercus_refuses(self):
        out = tb.envoyer(self.d, AVOCATE, self.tid, 0, [
            elt('ok'), elt('cadre', kind='embeddable', link='https://video.example.test/x'), elt('fen', kind='iframe'),
            elt('lien', link='https://site.example.test'), elt('interne', link='/dossier?id=DOS-001'),
            elt('apercu', customData={tb.MARQUE_PROPOSITION: 3}), elt('../x'), {'id': 'v', 'type': 'text', 'version': 'deux', 'versionNonce': 1}])
        motifs = {r['id']: r['motif'] for r in out['refuses']}
        self.assertEqual(out['appliques'], 2)                                     # « ok » et le lien interne
        self.assertEqual((motifs['cadre'], motifs['fen'], motifs['lien'], motifs['apercu'], motifs['v']),
                         ('type_refuse', 'type_refuse', 'lien_externe_refuse', 'apercu_non_accepte', 'version_invalide'))
        self.assertEqual(motifs['../x'], 'identifiant_invalide')

    def test_T04_element_lot_et_scene_bornes(self):
        lourd = elt('lourd', kind='freedraw', points=[[i, i] for i in range(20000)])
        out = tb.envoyer(self.d, AVOCATE, self.tid, 0, [lourd])
        self.assertEqual(out['refuses'][0]['motif'], 'element_trop_lourd')
        with self.assertRaisesRegex(Stop, 'lot_trop_grand'):
            tb.envoyer(self.d, AVOCATE, self.tid, 0, [elt('e%d' % i) for i in range(tb.MAX_LOT + 1)])
        with patch.object(tb, 'MAX_VIVANTS', 3):
            tb.envoyer(self.d, AVOCATE, self.tid, 0, [elt('a'), elt('b'), elt('c')])
            with self.assertRaisesRegex(Stop, 'tableau_trop_grand'):
                tb.envoyer(self.d, AVOCATE, self.tid, 1, [elt('d')])
            self.assertIsNone(self.stocke('d'))                                    # tout ou rien
            tb.envoyer(self.d, AVOCATE, self.tid, 1, [elt('a', version=2, isDeleted=True), elt('d')])   # une suppression libère la place
        self.assertEqual(tb.lire_scene(self.d, AVOCATE, self.tid)['version'], 2)

    def test_T04_images_types_taille_nombre(self):
        self.assertEqual(tb.deposer_image(self.d, AVOCATE, self.tid, 'img-00000001', 'image/png', data_url(PNG))['version'], 1)
        self.assertTrue(tb.deposer_image(self.d, ASSISTANT, self.tid, 'img-00000001', 'image/png', data_url(PNG))['deja'])   # immuable
        svg = b'<svg xmlns="http://www.w3.org/2000/svg"><script>alert(1)</script></svg>'
        for mime, brut, code in (('image/svg+xml', svg, 'type_image_refuse'), ('image/png', b'GIF89a' + b'\x00' * 20, 'type_image_refuse'),
                                 ('image/png', PNG + b'\x00' * tb.MAX_IMAGE_OCTETS, 'image_trop_lourde')):
            with self.assertRaisesRegex(Stop, code):
                tb.deposer_image(self.d, AVOCATE, self.tid, 'img-00000002', mime, data_url(brut, mime))
        with self.assertRaisesRegex(Stop, 'type_image_refuse'):
            tb.deposer_image(self.d, AVOCATE, self.tid, 'img-00000003', 'image/png', 'data:image/jpeg;base64,' + base64.b64encode(PNG).decode())
        with patch.object(tb, 'MAX_IMAGES', 1), self.assertRaisesRegex(Stop, 'images_trop_nombreuses'):
            tb.deposer_image(self.d, AVOCATE, self.tid, 'img-00000004', 'image/png', data_url(PNG))
        lu = tb.lire_scene(self.d, ASSISTANT, self.tid)
        self.assertEqual(lu['fichiers'], ['img-00000001'])
        self.assertTrue(tb.lire_image(self.d, ASSISTANT, self.tid, 'img-00000001')['dataURL'].startswith('data:image/png;base64,'))
        with self.assertRaisesRegex(Stop, 'conversation_absente'):
            tb.lire_image(self.d, AUTRE, self.tid, 'img-00000001')


# ---------------------------------------------------------------------------------------------------- T05 Mermaid validé
class Mermaid(unittest.TestCase):
    def test_T05_types_permis(self):
        for texte in (ORGANIGRAMME, 'graph LR\n  A["Assignation"] --> B["Audience"]', 'sequenceDiagram\n  Client->>Avocat: Pièces',
                      '%% chronologie\nflowchart LR\n  A["4 déc. : audience"]', '```mermaid\nstateDiagram-v2\n  [*] --> Ouvert\n```',
                      'flowchart TD\n  A["Étape 1<br>remise"] --> B'):
            premiere = next(ligne for ligne in tb.valider_mermaid(texte).splitlines() if ligne.strip() and not ligne.strip().startswith('%%'))
            self.assertIn(premiere.split()[0], tb.TYPES_MERMAID, texte)

    def test_T05_refus_hors_liste_et_contenus_dangereux(self):
        cas = {'gantt\n  title Procédure': 'type', 'pie\n  "A" : 1': 'type', 'timeline\n  2024 : audience': 'type', 'journey\n  title x': 'type',
               'flowchart XY\n  A --> B': 'type', '%%{init: {"securityLevel": "loose"}}%%\nflowchart TD\n  A --> B': 'directive',
               '---\nconfig:\n  theme: dark\n---\nflowchart TD\n  A --> B': 'entete_yaml', 'flowchart TD\n  A --> B\n  click A callback': 'interaction',
               'flowchart TD\n  A["https://cabinet.example.test"]': 'lien', 'flowchart TD\n  A["<script>alert(1)</script>"]': 'html',
               'flowchart TD\n  A["<img src=x onerror=alert(1)>"]': 'html', 'flowchart TD\n  A["javascript:alert(1)"]': 'lien',
               'sequenceDiagram\n  link Alice: Site @ www.example.test': 'interaction', '': 'vide', 'flowchart TD\n' + 'A-->B\n' * 200: 'trop_long'}
        for texte, raison in cas.items():
            with self.assertRaises(Stop, msg=texte) as c:
                tb.valider_mermaid(texte)
            self.assertEqual(str(c.exception), 'mermaid_refuse:' + raison, texte)
        with self.assertRaisesRegex(Stop, 'trop_long'):
            tb.valider_mermaid('flowchart TD\n  A["' + 'x' * tb.MAX_MERMAID + '"]')


# ---------------------------------------------------------------------------------------------------- T06 Pilote dessine (politique commune)
class PiloteDessine(Base):
    def ecrire(self, me, texte):
        out = mx.post(self.d, me, self.members[me]['name'], self.roles[me], self.tid, texte, self.members)
        return out, tb.apres_message(self.d, me, self.roles[me], self.tid, out['id'], texte)

    def executer(self, sortie):
        p = self.d.db.execute("SELECT id FROM tableau56103_propositions WHERE etat='en_file' ORDER BY id DESC LIMIT 1").fetchone()
        self.assertIsNotNone(p)
        with patch.object(tb, '_modele', lambda desk: FauxModele(sortie, self.appels)):
            return tb.job(self.d, {'proposition': p['id']})

    def test_T06_mention_met_en_file_sans_modele_puis_proposition(self):
        with patch('agent.model.Model', side_effect=AssertionError('modèle appelé dans la requête')):
            _, etat = self.ecrire(AVOCATE, '@pilote dessine l’organigramme du cabinet')
        self.assertEqual(etat['etat'], 'en_file')
        job = self.d.db.execute('SELECT * FROM jobs WHERE kind=?', (tb.JOB,)).fetchone()
        self.assertEqual(job['origin'] if 'origin' in job.keys() else 'user', 'user')
        out = self.executer({'mermaid': ORGANIGRAMME, 'titre': 'Organigramme du cabinet', 'reponse': 'Voici une proposition.'})
        self.assertEqual(out['etat'], 'proposee')
        messages, schema = self.appels[0]
        self.assertEqual(schema, tb.SORTIE)
        ctx = json.loads(messages[1]['content'])
        self.assertEqual((ctx['genre_demande'], ctx['demande'][:15]), ('organigramme', '@pilote dessine'))
        dernier = mx.messages(self.d, ASSISTANT, self.tid)[-1]
        self.assertEqual((dernier['auteur'], dernier['lien']), (pm.PILOTE, '/messagerie?fil=%d&tableau=1' % self.tid))
        self.assertIn('Organigramme du cabinet', dernier['texte'])
        lu = tb.lire_scene(self.d, ASSISTANT, self.tid)
        self.assertEqual(lu['propositions'][0]['mermaid'], ORGANIGRAMME)
        self.assertEqual(lu['elements'], [])                                    # rien dans la scène commune sans « Ajouter au tableau »

    def test_T06_mention_ordinaire_suit_le_chemin_de_pilote(self):
        self.assertIsNone(self.ecrire(AVOCATE, '@pilote résume la discussion')[1])
        self.assertIsNone(self.ecrire(AVOCATE, 'Il dessine bien, @pilote ?')[1])

    def test_T06_modele_de_la_politique_commune(self):
        vus = {}

        class Capture:
            def __init__(self, cfg):
                vus['cfg'] = cfg

        with patch('agent.model.routed_config', side_effect=lambda c, role: vus.setdefault('role', role) and {'routed': role}), patch('agent.model.Model', Capture):
            tb._modele(self.d)
        self.assertEqual((vus['role'], vus['cfg']), ('assistant', {'routed': 'assistant'}))

    def test_T06_pause_horaires_budget_expliques(self):
        self.d.setting('missions567:pause', True)
        _, etat = self.ecrire(AVOCATE, '@pilote dessine la procédure de référé')
        self.assertEqual((etat['etat'], etat['motif']), ('refuse', 'ia_en_pause'))
        self.assertEqual(self.d.db.execute('SELECT COUNT(*) FROM jobs WHERE kind=?', (tb.JOB,)).fetchone()[0], 0)
        self.assertIn('en pause', mx.messages(self.d, AVOCATE, self.tid)[-1]['texte'])
        self.d.setting('missions567:pause', False)
        self.ecrire(AVOCATE, '@pilote dessine la chronologie')
        out = self.executer(Stop('budget_cabinet_atteint'))
        self.assertEqual((out['etat'], out['motif']), ('echec', 'budget_cabinet_atteint'))
        self.assertIn('budget', mx.messages(self.d, AVOCATE, self.tid)[-1]['texte'])

    def test_T06_mermaid_hors_liste_refuse_avant_tout_rendu(self):
        self.ecrire(AVOCATE, '@pilote dessine la chronologie')
        out = self.executer({'mermaid': 'gantt\n  title Procédure\n  Audience :a1, 2026-12-04, 1d', 'titre': 'x', 'reponse': ''})
        self.assertEqual((out['etat'], out['motif']), ('echec', 'mermaid_refuse:type'))
        self.assertEqual(self.d.db.execute("SELECT mermaid FROM tableau56103_propositions").fetchone()[0], '')
        self.assertEqual(tb.lire_scene(self.d, AVOCATE, self.tid)['propositions'], [])
        self.assertIn('type de diagramme non permis', mx.messages(self.d, AVOCATE, self.tid)[-1]['texte'])

    def test_T06_arret_demande_pendant_l_appel_aucun_effet(self):
        self.ecrire(AVOCATE, '@pilote dessine l’organigramme')
        job = self.d.db.execute('SELECT * FROM jobs WHERE kind=?', (tb.JOB,)).fetchone()
        self.d.db.execute("UPDATE jobs SET status='cancel_requested', stop_reason='pause' WHERE id=?", (job['id'],))
        self.d.db.commit()
        self.d.active_job_id = job['id']
        out = self.executer({'mermaid': ORGANIGRAMME, 'titre': 'Organigramme', 'reponse': ''})
        self.assertEqual(out['etat'], 'echec')
        self.assertEqual(tb.lire_scene(self.d, AVOCATE, self.tid)['propositions'], [])

    def test_T06_pilote_absent_ou_desactive(self):
        pm.changer_presence(self.d, AVOCATE, 'Me DURAND', self.tid, False)
        self.assertEqual(self.ecrire(AVOCATE, '@pilote dessine un schéma')[1]['etat'], 'absent')
        self.d.setting(pm.REGLAGE, False)
        self.assertEqual(self.ecrire(AVOCATE, '@pilote dessine un schéma')[1]['etat'], 'desactive')


# ---------------------------------------------------------------------------------------------------- T07 accepter / annuler une proposition
class Decision(Base):
    def proposer(self):
        cur = self.d.db.execute("INSERT INTO tableau56103_propositions(cle,thread,demandeur,role,etat,mermaid,titre,created,updated) "
                                "VALUES('d-1',?,?,'avocat','proposee',?,'Organigramme','x','x')", (self.tid, AVOCATE, ORGANIGRAMME))
        self.d.db.commit()
        return cur.lastrowid

    def test_T07_ajout_une_seule_fois_pour_tous(self):
        pid = self.proposer()
        out = tb.decider(self.d, ASSISTANT, self.tid, pid, True, 0, [elt('n1', customData={tb.MARQUE_PROPOSITION: pid}), elt('n2')])
        self.assertEqual((out['etat'], out['appliques']), ('acceptee', 2))
        self.assertIsNone(self.stocke('n1').get('customData'))                    # la marque d'aperçu ne reste jamais
        deux = tb.decider(self.d, AVOCATE, self.tid, pid, True, 0, [elt('autre1'), elt('autre2')])   # second geste, autre poste
        self.assertTrue(deux['deja'])
        self.assertIsNone(self.stocke('autre1'))
        self.assertEqual(tb.lire_scene(self.d, AVOCATE, self.tid)['propositions'], [])

    def test_T07_annuler_et_droits(self):
        pid = self.proposer()
        with self.assertRaisesRegex(Stop, 'conversation_absente'):
            tb.decider(self.d, AUTRE, self.tid, pid, False)
        with self.assertRaisesRegex(Stop, 'elements_requis'):
            tb.decider(self.d, AVOCATE, self.tid, pid, True, 0, [])
        self.assertEqual(tb.decider(self.d, AVOCATE, self.tid, pid, False)['etat'], 'retiree')
        self.assertTrue(tb.decider(self.d, ASSISTANT, self.tid, pid, True, 0, [elt('n')])['deja'])
        self.assertIsNone(self.stocke('n'))
        autre = mx.create_group(self.d, AVOCATE, 'Autre', [AUTRE], self.members)
        with self.assertRaisesRegex(Stop, 'proposition_absente'):              # proposition d'une autre discussion
            tb.decider(self.d, AVOCATE, autre, pid, False)


# ---------------------------------------------------------------------------------------------------- T08 fichier assemblé
class Assemblage(unittest.TestCase):
    CDN = re.compile(r'https?://(?:[a-z0-9-]+\.)*(?:esm\.sh|unpkg\.com|jsdelivr\.net|cdnjs\.cloudflare\.com|googleapis\.com|gstatic\.com|skypack\.dev|jspm\.io|'
                     r'platform\.twitter\.com|embed\.reddit\.com|json\.excalidraw\.com|oss-ai\.excalidraw\.com|oss-collab\.excalidraw\.com|firebaseio\.com|'
                     r'cloudfunctions\.net)', re.I)

    def fichiers(self):
        from agent.excalidraw_fichiers56103 import FICHIERS
        return FICHIERS

    def test_T08_aucun_cdn_dans_le_fichier_assemble(self):
        textes = [p for p in self.fichiers() if p.endswith(('.js', '.css'))]
        self.assertGreater(len(textes), 10)
        for rel in textes:
            texte = (ROOT / 'agent' / 'static' / rel).read_text(encoding='utf-8')
            self.assertIsNone(self.CDN.search(texte), rel)
            self.assertNotIn('esm.sh', texte, rel)
            self.assertIsNone(re.search(r'AIza[0-9A-Za-z_-]{30,}', texte), rel)
        entree = (ROOT / 'agent/static/excalidraw56103.js').read_text(encoding='utf-8')
        self.assertIn('./excalidraw56103/', entree)                               # morceaux relatifs : servis par AxiorHub
        coeur = ''.join((ROOT / 'agent/static' / p).read_text(encoding='utf-8') for p in textes if 'EXCALIDRAW_ASSET_PATH' in (ROOT / 'agent/static' / p).read_text(encoding='utf-8'))
        self.assertIn('new URL("/static/excalidraw56103/",globalThis.location.href)', coeur)   # secours des polices : local

    def test_T08_manifeste_d_empreintes_a_jour(self):
        spec = importlib.util.spec_from_file_location('build_excalidraw', ROOT / 'scripts' / 'build-excalidraw.py')
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        self.assertTrue(module.verifier())
        manifeste = json.loads((ROOT / 'scripts/excalidraw56103/manifeste.json').read_text(encoding='utf-8'))
        for rel, (kind, sha) in self.fichiers().items():
            self.assertEqual(manifeste['sorties']['agent/static/' + rel], sha, rel)
            self.assertEqual(hashlib.sha256((ROOT / 'agent/static' / rel).read_bytes()).hexdigest(), sha, rel)
        self.assertEqual(manifeste['remplacements'], {'esm': 1, 'widgets': 2, 'services': 9})
        verrou = json.loads((ROOT / 'scripts/excalidraw56103/package.json').read_text(encoding='utf-8'))['dependencies']
        self.assertEqual(verrou['@excalidraw/excalidraw'], '0.18.1')
        self.assertTrue(all(re.fullmatch(r'\d+\.\d+\.\d+', v) for v in verrou.values()))   # versions exactes

    def test_T08_licences_permissives_et_polices(self):
        manifeste = json.loads((ROOT / 'scripts/excalidraw56103/manifeste.json').read_text(encoding='utf-8'))
        permises = {'MIT', 'ISC', 'Apache-2.0', 'BSD-2-Clause', 'BSD-3-Clause', '0BSD', 'CC0-1.0', 'Zlib', 'Unlicense', 'OFL-1.1'}
        for p in manifeste['paquets_embarques'] + manifeste['dependances_precompilees_ou_inutilisees']:
            options = re.sub(r'[()]', '', p['license']).split(' OR ')
            self.assertTrue(any(all(x.strip() in permises for x in o.split(' AND ')) for o in options), p)
            self.assertNotRegex(p['license'], r'GPL')
        self.assertEqual({p['licence'] for p in manifeste['polices']} - permises, set())
        self.assertFalse(any('Xiaolai' in p or 'Liberation' in p for p in self.fichiers()))

    def test_T08_fichiers_declares_et_polices_en_octets(self):
        from agent import web440
        for rel in self.fichiers():
            self.assertIn('/static/' + rel, web440.STATIC)
        self.assertIn('/static/v56103-tableau.js', web440.STATIC)
        police = next(p for p in self.fichiers() if p.endswith('.woff2'))
        r = web440.route({}, {}, {}, '/p', '/static/' + police, {}, 'GET')
        self.assertEqual(r['kind'], 'font/woff2')
        self.assertIsInstance(r['body'], bytes)


# ---------------------------------------------------------------------------------------------------- T09 application réelle
class Application(tw.WebWorkshopTests):
    def setUp(self):
        super().setUp()
        self.addCleanup(mb.clear)
        d = Desk(self.f.c)
        d.db.execute(mb.SCHEMA)
        for owner, name, role in GENS:
            d.db.execute("INSERT OR REPLACE INTO membres5650(owner,name,role,first_seen,last_seen) VALUES(?,?,?,'2026-11-15','2026-11-15')", (owner, name, role))
        d.db.commit()

    def json(self, path, method='GET', body=None, query='', **kw):
        r = self.call('/api440/' + path, method, body, query=query, **kw)
        return r['status'], json.loads(r['body'] or b'{}'), r

    def groupe(self, avec=AVOCATE):
        st, d, _ = self.json('m5650/groupe', 'POST', {'titre': 'Pôle contentieux', 'compte:' + avec: True})
        self.assertTrue(st.startswith('200'), d)
        return d['id']

    def test_T09_page_bouton_config_csp(self):
        tid = self.groupe()
        r = self.call('/messagerie', query='fil=%d' % tid)
        page = r['body'].decode()
        for attendu in ('data-t103-ouvrir', 'Tableau blanc', 'data-t103-config', '/static/v56103-tableau.js'):
            self.assertIn(attendu, page)
        for balise in re.findall(r'<script\b[^>]*>', page):
            self.assertTrue(' src=' in balise or 'application/json' in balise, balise)
        config = json.loads(re.search(r'<script type="application/json" data-t103-config>(.*?)</script>', page).group(1))
        self.assertEqual((config['fil'], config['ressources']), (tid, '/agent-courriel/static/excalidraw56103/'))
        csp = r['headers']['Content-Security-Policy']
        self.assertIn("img-src 'self' data: blob:", csp)
        self.assertIn("script-src 'self' 'wasm-unsafe-eval';", csp)              # rien d'autre n'est assoupli
        self.assertIn("style-src 'self';", csp)
        self.assertIn("connect-src 'self';", csp)
        self.assertIn("img-src 'self';", self.call('/aujourdhui')['headers']['Content-Security-Policy'])
        self.assertNotIn('data-t103-config', self.call('/messagerie')['body'].decode())
        js = self.call('/static/v56103-tableau.js')
        self.assertTrue(js['body'].startswith(b'/* AxiorHub 5.6.103 (lot T)'))
        self.assertNotRegex(js['body'].decode(), r'https?://')
        self.assertIn(b'.t103-scene', self.call('/static/app520.css')['body'])
        self.assertTrue(self.call('/static/excalidraw56103.css')['status'].startswith('200'))
        from agent import interface56100
        interface56100.regler_cabinet(Desk(self.f.c), False)
        self.assertNotIn('data-t103-ouvrir', self.call('/messagerie', query='fil=%d' % tid)['body'].decode())

    def test_T09_api_envoi_idempotent_404_neutre_r05(self):
        tid = self.groupe()
        st, d, r = self.json('m56103/tableau/scene', query='fil=%d&depuis=0' % tid)
        self.assertEqual((st, d['version']), ('200 OK', 0))
        self.assertIn(('Cache-Control', 'private, no-store'), list(r['headers'].items()))
        corps = {'fil': tid, 'base': 0, 'elements': [elt('r1')], 'cle': 't103-' + 'a' * 32}
        st, un, _ = self.json('m56103/tableau/envoyer', 'POST', corps)
        self.assertTrue(st.startswith('200'), un)
        st, deux, _ = self.json('m56103/tableau/envoyer', 'POST', corps)
        self.assertEqual(deux, {k: v for k, v in un.items() if k not in ('elements', 'fichiers')})   # même clé, même reçu, aucun second effet
        self.assertEqual(Desk(self.f.c).db.execute('SELECT version FROM tableau56103_scenes WHERE thread=?', (tid,)).fetchone()[0], 1)
        st, d, _ = self.json('m56103/tableau/envoyer', 'POST', {**corps, 'elements': [elt('r2')]})
        self.assertEqual((st, d['error']), ('409 Conflict', 'cle_idempotence_deja_utilisee'))
        st, d, _ = self.json('m56103/tableau/envoyer', 'POST', {'fil': tid, 'base': 0, 'elements': [elt('r1', version=2, nonce=1)], 'cle': 't103-' + 'b' * 32})
        self.assertEqual((st, d['error'], d['conflits']), ('409 Conflict', 'version_perimee', ['r1']))
        st, d, _ = self.json('m56103/tableau/envoyer', 'POST', {'fil': tid, 'base': 0, 'elements': [elt('x')]})
        self.assertEqual(d['error'], 'cle_idempotence_requise')
        absent = self.json('m56103/tableau/scene', query='fil=999999')
        prive = self.groupe()
        desk = Desk(self.f.c)
        desk.db.execute("UPDATE participants5650 SET left_at='x' WHERE thread=? AND owner='cabinet'", (prive,))
        desk.db.commit()
        interdit = self.json('m56103/tableau/scene', query='fil=%d' % prive)
        self.assertEqual((absent[0], absent[1]), (interdit[0], interdit[1]))      # absent et interdit : même 404 neutre
        self.assertEqual(absent[0], '404 Not Found')
        self.assertEqual(self.json('m56103/tableau/envoyer', 'POST', corps, csrf=False)[0], '403 Forbidden')
        avant = desk.db.total_changes
        lecteur = Desk(self.f.c)
        ecritures = lecteur.db.total_changes
        self.call('/api440/m56103/tableau/scene', query='fil=%d&depuis=0' % tid)
        self.call('/messagerie', query='fil=%d' % tid)
        n = lecteur.db.execute('SELECT (SELECT COUNT(*) FROM tableau56103_elements)+(SELECT COUNT(*) FROM tableau56103_recus)+(SELECT COUNT(*) FROM tableau56103_scenes)').fetchone()[0]
        self.assertEqual(n, 1 + 1 + 1)                                            # R05 : lectures sans écriture
        self.assertEqual(lecteur.db.total_changes, ecritures)
        self.assertEqual(desk.db.total_changes, avant)

    def test_T09_image_route_bornee_et_csrf(self):
        tid = self.groupe()
        corps = {'fil': tid, 'id': 'img-00000001', 'mimeType': 'image/png', 'dataURL': data_url(PNG + b'\x01' * 300_000), 'cle': 't103-' + 'c' * 32}
        self.assertGreater(len(json.dumps(corps)), 260_000)                       # au-delà de la borne commune : route propre aux images
        st, d, _ = self.json('m56103/tableau/fichier', 'POST', corps)
        self.assertTrue(st.startswith('200'), d)
        self.assertEqual(self.json('m56103/tableau/fichier', 'POST', {**corps, 'cle': 't103-' + 'd' * 32}, origin=False)[0], '403 Forbidden')
        self.assertEqual(self.json('m56103/tableau/fichier', 'POST', {**corps, 'cle': 't103-' + 'e' * 32}, csrf=False)[0], '403 Forbidden')
        trop = {**corps, 'id': 'img-00000002', 'dataURL': data_url(PNG + b'\x01' * 1_200_000), 'cle': 't103-' + 'f' * 32}
        st, d, _ = self.json('m56103/tableau/fichier', 'POST', trop)
        self.assertEqual((st, d['error']), ('413 Payload Too Large', 'requete_trop_longue'))
        st, f, _ = self.json('m56103/tableau/fichier', query='fil=%d&id=img-00000001' % tid)
        self.assertEqual(f['mimeType'], 'image/png')
        for route in ('scene', 'envoyer'):                                        # route ordinaire : borne commune de web440
            self.assertEqual(self.json('m56103/tableau/' + route, 'POST', {**corps, 'cle': 't103-' + 'g' * 32})[0], '403 Forbidden' if route == 'scene' else '413 Payload Too Large')

    def test_T09_mention_dessine_par_l_api_sans_modele(self):
        tid = self.groupe()
        with patch('agent.model.Model', side_effect=AssertionError('modèle appelé dans la requête web')):
            st, d, _ = self.json('m5650/ecrire', 'POST', {'id': tid, 'texte': '@pilote dessine le schéma de la procédure'})
        self.assertTrue(st.startswith('200'), d)
        self.assertEqual(d['pilote']['etat'], 'en_file')
        desk = Desk(self.f.c)
        self.assertEqual(desk.db.execute('SELECT COUNT(*) FROM jobs WHERE kind=?', (tb.JOB,)).fetchone()[0], 1)
        self.assertEqual(desk.db.execute('SELECT COUNT(*) FROM jobs WHERE kind=?', (pm.JOB,)).fetchone()[0], 0)   # pas de double réponse
        st, d, _ = self.json('m56103/tableau/scene', query='fil=%d' % tid)
        self.assertEqual(d['propositions'][0]['etat'], 'en_file')


if __name__ == '__main__':
    unittest.main()
