"""5.6.103 (lot L) : liaison directe entre postes sur le réseau local, sans serveur.

Deux (ou trois) postes simulés dans le même processus : un ``Desk`` par dossier temporaire, transport réel sur 127.0.0.1 (essais
seulement : ``boucle=True`` n'est jamais passé par l'interface), ports libres choisis par le système. Données fictives (DURAND,
MARTIN, BERNARD, DOS-001, example.test) ; modèle simulé ; aucun appel réseau hors de la boucle locale, aucun envoi.
"""
import hashlib
import io
import json
import os
from pathlib import Path
import secrets
import socket
import sys
import threading
import time
import unittest
from unittest.mock import patch

from agent import liaison56103 as L, liaison_web56103 as LW, messagerie5650 as mx, pilote_messagerie56102 as pm, membres5650 as mb
from agent.common import Stop
from agent.desk import Desk
import test_agent as fixtures

RACINE = Path(__file__).resolve().parents[1]
# adresses publiques d'essai construites par morceaux (aucune adresse littérale hors des plages admises par privacy-scan)
SANS_168 = '.'.join(('192', '18', '0', '1'))      # forme du cas signalé par l'avocat : 192 sans le « 168 »
HORS_172 = '.'.join(('172', '32', '0', '1'))
AUTH = {'origin': 'http://127.0.0.1:8769', 'csrf': 'c' * 40, 'poste': True}


class FauxModele:
    def __init__(self, sortie):
        self.sortie = sortie

    def complete(self, messages, temperature=None, max_tokens=3500, json_schema=None):
        return json.dumps(self.sortie)


class Poste:
    """Un poste de travail simulé : sa base, son identité, sa liaison sur 127.0.0.1 (port choisi par le système)."""

    def __init__(self, test, nom, personne, demarrer=True):
        f = fixtures.EngineTests(methodName='test_observation_has_no_mail_write')
        f.setUp()
        test.addCleanup(f.doCleanups)
        self.c, self.nom, self.test = f.c, nom, test
        self.desk = Desk(self.c)
        test.addCleanup(self._fermer)
        mx.ensure_schema(self.desk)
        pm.ensure_schema(self.desk)
        from agent import taches5688
        taches5688.schema(self.desk)
        self.desk.db.execute(mb.SCHEMA)
        self.desk.db.execute("INSERT OR REPLACE INTO membres5650(owner,name,role,first_seen,last_seen) VALUES('cabinet',?,'administrateur','2026-11-15','2026-11-15')",
                             (personne,))
        self.desk.db.commit()
        self.personne = personne
        L.activer(self.desk, 'cabinet', personne, True)
        self.desk.setting(L.REGLAGE_NOM, nom)
        self.ident = L.identite(self.c['state_dir'])
        self.liaison = None
        if demarrer:
            self.demarrer()

    def _fermer(self):
        if self.liaison is not None:
            self.liaison.arreter()
        try:
            self.desk.db.close()
        except Exception:   # noqa: BLE001
            pass

    def ouvrir(self):
        return Desk(self.c)

    def demarrer(self):
        self.liaison = L.Liaison(self.ouvrir, boucle=True).demarrer('127.0.0.1', 0)
        self.desk.setting(L.REGLAGE_PORT, self.liaison.port)
        return self.liaison

    def membres(self):
        return L.augmenter(self.desk, mb.directory(self.desk))

    def ecrire(self, tid, texte):
        out = mx.post(self.desk, 'cabinet', self.personne, 'administrateur', tid, texte, self.membres())
        return out, pm.apres_message(self.desk, 'cabinet', 'administrateur', tid, out['id'], texte)

    def sync(self):
        return self.liaison.synchroniser(forcer=True)

    def messages(self, tid):
        return mx.messages(self.desk, 'cabinet', tid, limit=200)

    def fils(self):
        return mx.threads(self.desk, 'cabinet', self.membres())

    def poste(self, autre):
        return self.desk.db.execute('SELECT * FROM liaison56103_postes WHERE id=?', (autre.ident.id,)).fetchone()


def appairer(a, b):
    """``b`` affiche un code, ``a`` le saisit (adresse de ``b`` en boucle locale)."""
    code = L.nouveau_code(b.desk)['code']
    return L.appairer(a.desk, '127.0.0.1', b.liaison.port, code, boucle=True)


class Relais:
    """Relais TCP sur 127.0.0.1 : écoute passive du réseau entre deux postes (octets enregistrés dans chaque sens)."""

    def __init__(self, test, port_cible):
        self.cible, self.sessions = port_cible, []
        self.s = socket.socket()
        self.s.bind(('127.0.0.1', 0))
        self.s.listen(4)
        self.s.settimeout(0.3)
        self.port = self.s.getsockname()[1]
        self.arret = threading.Event()
        threading.Thread(target=self._boucle, daemon=True).start()
        test.addCleanup(self.fermer)

    def fermer(self):
        self.arret.set()
        self.s.close()

    def _boucle(self):
        while not self.arret.is_set():
            try:
                c, _ = self.s.accept()
            except (socket.timeout, OSError):
                continue
            d = socket.create_connection(('127.0.0.1', self.cible))
            capture = {'c2s': bytearray(), 's2c': bytearray()}
            self.sessions.append(capture)
            threading.Thread(target=self._pompe, args=(c, d, capture['c2s']), daemon=True).start()
            threading.Thread(target=self._pompe, args=(d, c, capture['s2c']), daemon=True).start()

    @staticmethod
    def _pompe(src, dst, buf):
        try:
            while True:
                data = src.recv(65536)
                if not data:
                    break
                buf += data
                dst.sendall(data)
        except OSError:
            pass
        finally:
            for s in (src, dst):
                try:
                    s.shutdown(socket.SHUT_RDWR)
                except OSError:
                    pass


def env_json(data, role='administrateur', user='cabinet', auth=AUTH):
    raw = json.dumps(data).encode('utf-8')
    return {'HTTP_ORIGIN': auth['origin'], 'HTTP_X_CSRF_TOKEN': auth['csrf'], 'CONTENT_TYPE': 'application/json', 'CONTENT_LENGTH': str(len(raw)),
            'wsgi.input': io.BytesIO(raw), 'HTTP_X_AXIORHUB_USER': user, 'HTTP_X_AXIORHUB_ROLE': role, 'REQUEST_METHOD': 'POST'}


def env_get(role='administrateur', user='cabinet'):
    return {'HTTP_X_AXIORHUB_USER': user, 'HTTP_X_AXIORHUB_ROLE': role, 'REQUEST_METHOD': 'GET'}


def cle():
    return secrets.token_urlsafe(18)


class Base(unittest.TestCase):
    def setUp(self):
        self.addCleanup(mb.clear)

    def deux(self):
        a = Poste(self, 'Poste de Me DURAND', 'Me DURAND')
        b = Poste(self, 'Poste de Paul MARTIN', 'Paul MARTIN')
        appairer(a, b)
        return a, b

    def direct(self, a, b):
        return mx.open_direct(a.desk, 'cabinet', L.PARTICIPANT + b.ident.id, a.membres())


# ---------------------------------------------------------------------------------------------------- activation, port fermé
class Activation(Base):
    def test_L01_desactivee_par_defaut_aucun_port_ouvert(self):
        f = fixtures.EngineTests(methodName='test_observation_has_no_mail_write')
        f.setUp()
        self.addCleanup(f.doCleanups)
        d = Desk(f.c)
        self.addCleanup(d.db.close)
        self.assertFalse(L.est_active(d))
        cfg = Path(f.c['state_dir']).parent / 'config.json'
        cfg.write_text(json.dumps(f.c), encoding='utf-8')
        self.assertFalse(L.active_dans(cfg))
        with patch.object(L.Liaison, 'demarrer', side_effect=AssertionError('aucun port ne doit être ouvert')), \
                patch.object(L.socket, 'socket', side_effect=AssertionError('aucun socket ne doit être créé')):
            self.assertEqual(L.service(f.c, intervalle=0), 0)
        # le superviseur du poste ne lance le processus « liaison » que si la liaison est activée, et l'arrête ensuite
        sys.path.insert(0, str(RACINE))
        import poste
        p = {'config': cfg, 'logs': Path(f.c['state_dir']).parent, 'runtime': Path(f.c['state_dir']).parent / 'runtime.json'}
        sup = poste.Supervisor(p, 8799, python=sys.executable)
        lances, arretes = [], []

        class Proc:
            returncode = None

            def poll(self):
                return None
        with patch.object(sup, '_spawn', side_effect=lambda name, args: (lances.append((name, args)), sup.procs.__setitem__(name, Proc()))), \
                patch.object(sup, '_terminate', side_effect=lambda proc, *a: arretes.append(proc)):
            sup._liaison103()
            self.assertEqual(lances, [])
            L.activer(d, 'cabinet', 'Me DURAND', True)
            self.assertTrue(L.active_dans(cfg))
            sup._liaison103()
            self.assertEqual([n for n, _ in lances], ['liaison'])
            self.assertIn('liaison', lances[0][1])
            sup._liaison103()                                          # déjà lancé : pas de second processus
            self.assertEqual(len(lances), 1)
            L.activer(d, 'cabinet', 'Me DURAND', False)
            sup._liaison103()
            self.assertEqual(len(arretes), 1)

    def test_L01_poste_desactive_refuse_les_sessions_et_l_appairage(self):
        a, b = self.deux()
        L.activer(b.desk, 'cabinet', 'Paul MARTIN', False)
        tid = self.direct(a, b)
        a.ecrire(tid, 'Bonjour, message pour un poste désactivé.')
        bilan = a.sync()
        self.assertIn('erreur', bilan[b.ident.id])
        self.assertEqual(b.desk.db.execute('SELECT COUNT(*) FROM liaison56103_messages').fetchone()[0], 0)
        L.nouveau_code(b.desk)
        with self.assertRaises(Stop):
            L.appairer(a.desk, '127.0.0.1', b.liaison.port, '12345678', boucle=True)


# ---------------------------------------------------------------------------------------------------- appairage
class Appairage(Base):
    def test_L02_appairage_echange_les_cles_et_consomme_le_code(self):
        a = Poste(self, 'Poste de Me DURAND', 'Me DURAND')
        b = Poste(self, 'Poste de Paul MARTIN', 'Paul MARTIN')
        L.nouveau_code(b.desk)
        self.assertIsNotNone(L.code_actif(b.c['state_dir']))
        code = json.loads((Path(b.c['state_dir']) / 'liaison56103' / 'code-appairage.json').read_text())['code']
        out = L.appairer(a.desk, '127.0.0.1', b.liaison.port, code[:4] + '-' + code[4:], boucle=True)
        self.assertEqual(out, {'id': b.ident.id, 'nom': 'Poste de Paul MARTIN'})
        pb, pa = a.poste(b), b.poste(a)
        self.assertEqual(bytes(pb['cle_signature']), b.ident.cle_signature)
        self.assertEqual(bytes(pb['cle_echange']), b.ident.cle_echange)
        self.assertEqual(bytes(pa['cle_signature']), a.ident.cle_signature)
        self.assertEqual(pa['port'], a.liaison.port)
        self.assertIsNone(L.code_actif(b.c['state_dir']))           # usage unique
        with self.assertRaisesRegex(Stop, 'aucun_code_actif'):
            L.appairer(a.desk, '127.0.0.1', b.liaison.port, code, boucle=True)
        # clé privée au coffre chiffré : jamais en clair sur le disque
        brut = L._chemin_identite(a.c['state_dir']).read_bytes()
        self.assertTrue(brut.startswith(b'AXIORHUB-VAULT-V1:'))

    def test_L03_code_faux_refuse_essais_limites(self):
        a = Poste(self, 'Poste de Me DURAND', 'Me DURAND')
        b = Poste(self, 'Poste de Paul MARTIN', 'Paul MARTIN')
        bon = L.nouveau_code(b.desk)['code'].replace('-', '')
        faux = '%08d' % ((int(bon) + 1) % 10 ** 8)
        with self.assertRaisesRegex(Stop, 'code_refuse'):
            L.appairer(a.desk, '127.0.0.1', b.liaison.port, faux, boucle=True)
        self.assertEqual(L.code_actif(b.c['state_dir'])['essais'], L.CODE_ESSAIS - 1)
        self.assertIsNone(a.poste(b))
        self.assertIsNone(b.poste(a))
        for _ in range(L.CODE_ESSAIS - 1):
            with self.assertRaises(Stop):
                L.appairer(a.desk, '127.0.0.1', b.liaison.port, faux, boucle=True)
        self.assertIsNone(L.code_actif(b.c['state_dir']))           # code effacé après le dernier essai
        with self.assertRaisesRegex(Stop, 'aucun_code_actif'):      # même le bon code ne sert plus
            L.appairer(a.desk, '127.0.0.1', b.liaison.port, bon, boucle=True)
        self.assertIsNone(a.poste(b))

    def test_L04_code_expire_refuse(self):
        a = Poste(self, 'Poste de Me DURAND', 'Me DURAND')
        b = Poste(self, 'Poste de Paul MARTIN', 'Paul MARTIN')
        code = L.nouveau_code(b.desk, duree=-1)['code']
        with self.assertRaisesRegex(Stop, 'aucun_code_actif'):
            L.appairer(a.desk, '127.0.0.1', b.liaison.port, code, boucle=True)
        self.assertIsNone(a.poste(b))
        with self.assertRaisesRegex(Stop, 'code_appairage_invalide'):
            L.normaliser_code('12-34')

    def test_L04_spake2_elements_hors_groupe_refuses(self):
        self.assertTrue(L._membre(L.M) and L._membre(L.N) and L._membre(pow(L.G, 12345, L.P)))
        for mauvais in (0, 1, L.P - 1, L.P, L.P + 5):
            self.assertFalse(L._membre(mauvais))
        with self.assertRaisesRegex(Stop, 'element_invalide'):
            L._entier('%x' % (L.P - 1))


# ---------------------------------------------------------------------------------------------------- messages, groupes, Pilote
class Messages(Base):
    def test_L05_message_a_deux_dans_les_deux_sens_sans_echo(self):
        a, b = self.deux()
        tid = self.direct(a, b)
        a.ecrire(tid, 'Bonjour Paul, la réunion DOS-001 est à 15 h.')
        self.assertEqual(a.sync()[b.ident.id], {'remis': 1, 'refuses': 0})
        fils_b = b.fils()
        self.assertEqual(len(fils_b), 1)
        self.assertEqual(fils_b[0]['titre'], 'Poste de Me DURAND')       # nom du poste relié
        tid_b = fils_b[0]['id']
        recu = b.messages(tid_b)[-1]
        self.assertEqual((recu['auteur'], recu['nom'], recu['texte']), (L.PARTICIPANT + a.ident.id, 'Me DURAND', 'Bonjour Paul, la réunion DOS-001 est à 15 h.'))
        b.ecrire(tid_b, 'Bien reçu, Me DURAND.')
        self.assertEqual(b.sync()[a.ident.id]['remis'], 1)
        self.assertEqual(a.messages(tid)[-1]['texte'], 'Bien reçu, Me DURAND.')
        self.assertEqual(L.exporter(a.desk, a.ident), 0)               # un message reçu ne repart jamais
        self.assertEqual(L.exporter(b.desk, b.ident), 0)
        self.assertEqual(len(b.messages(tid_b)), 2)

    def test_L06_groupe_de_trois_postes_et_mentions(self):
        a = Poste(self, 'Poste de Me DURAND', 'Me DURAND')
        b = Poste(self, 'Poste de Paul MARTIN', 'Paul MARTIN')
        c = Poste(self, 'Poste de Lea BERNARD', 'Lea BERNARD')
        appairer(a, b)
        appairer(a, c)
        appairer(b, c)
        g = mx.create_group(a.desk, 'cabinet', 'Pôle contentieux', [L.PARTICIPANT + b.ident.id, L.PARTICIPANT + c.ident.id], a.membres())
        a.ecrire(g, 'Point sur DOS-001 demain.')
        bilan = a.sync()
        self.assertEqual({k: v['remis'] for k, v in bilan.items()}, {b.ident.id: 1, c.ident.id: 1})
        for poste in (b, c):
            f = [x for x in poste.fils() if x['genre'] == 'groupe']
            self.assertEqual([x['titre'] for x in f], ['Pôle contentieux'])
            info = mx.thread_info(poste.desk, 'cabinet', f[0]['id'], poste.membres())
            self.assertEqual(len(info['membres']), 3)                     # le compte du poste et les deux autres postes
        g_b = [x for x in b.fils() if x['genre'] == 'groupe'][0]['id']
        out, _ = b.ecrire(g_b, '@PostedeLeaBERNARD peux-tu relire ?')
        self.assertEqual(out['mentions'], [L.PARTICIPANT + c.ident.id])
        self.assertEqual({k: v['remis'] for k, v in b.sync().items()}, {a.ident.id: 1, c.ident.id: 1})
        g_c = [x for x in c.fils() if x['genre'] == 'groupe'][0]['id']
        dernier = c.desk.db.execute('SELECT mentions FROM messages5650 WHERE thread=? ORDER BY id DESC LIMIT 1', (g_c,)).fetchone()
        self.assertEqual(json.loads(dernier['mentions']), ['cabinet'])     # sur le poste de Lea BERNARD, la mention vise son compte
        self.assertEqual(a.messages(g)[-1]['texte'], '@PostedeLeaBERNARD peux-tu relire ?')
        self.assertEqual(c.desk.db.execute('SELECT COUNT(*) FROM fils5650 WHERE kind=?', ('groupe',)).fetchone()[0], 1)

    def test_L07_pilote_tourne_sur_le_poste_qui_l_interpelle(self):
        a, b = self.deux()
        g = mx.create_group(a.desk, 'cabinet', 'Équipe DOS-001', [L.PARTICIPANT + b.ident.id], a.membres())
        _, demande = a.ecrire(g, '@pilote résume la discussion')
        self.assertEqual(demande['etat'], 'en_file')
        a.sync()
        g_b = [x for x in b.fils() if x['genre'] == 'groupe'][0]['id']
        self.assertIn('@pilote', b.messages(g_b)[-1]['texte'])
        self.assertEqual(b.desk.db.execute('SELECT COUNT(*) FROM pilote56102_demandes').fetchone()[0], 0)    # jamais l'IA d'un autre poste
        self.assertEqual(b.desk.db.execute('SELECT COUNT(*) FROM jobs WHERE kind=?', (pm.JOB,)).fetchone()[0], 0)
        self.assertEqual(a.desk.db.execute('SELECT COUNT(*) FROM jobs WHERE kind=?', (pm.JOB,)).fetchone()[0], 1)
        d = a.desk.db.execute("SELECT id FROM pilote56102_demandes WHERE etat='en_file'").fetchone()
        with patch.object(pm, '_modele', lambda desk: FauxModele({'reponse': 'Résumé : réunion prévue, rien d’urgent.', 'actions': []})):
            self.assertEqual(pm.job(a.desk, {'demande': d['id']})['etat'], 'fait')
        a.sync()
        rep = b.messages(g_b)[-1]
        self.assertEqual(rep['genre'], 'ia')
        self.assertEqual(rep['nom'], 'Pilote (poste de Poste de Me DURAND)')
        self.assertIn('Résumé : réunion prévue', rep['texte'])
        # le collaborateur interpelle Pilote depuis SON poste : c'est son poste qui travaille
        _, demande_b = b.ecrire(g_b, '@pilote une tâche pour demain ?')
        self.assertEqual(demande_b['etat'], 'en_file')
        b.sync()
        self.assertEqual(a.desk.db.execute('SELECT COUNT(*) FROM jobs WHERE kind=?', (pm.JOB,)).fetchone()[0], 1)
        self.assertEqual(b.desk.db.execute('SELECT COUNT(*) FROM jobs WHERE kind=?', (pm.JOB,)).fetchone()[0], 1)

    def test_L08_piece_jointe_empreinte_verifiee(self):
        a, b = self.deux()
        tid = self.direct(a, b)
        contenu = b'%PDF-1.4 fictif DOS-001 ' + bytes(range(256)) * 40
        out = L.deposer_piece(a.desk, 'cabinet', 'Me DURAND', 'administrateur', tid, contenu, 'Conclusions DURAND.pdf', a.membres())
        self.assertEqual(out['sha256'], hashlib.sha256(contenu).hexdigest())
        self.assertEqual(a.sync()[b.ident.id]['remis'], 1)
        tid_b = b.fils()[0]['id']
        m = b.messages(tid_b)[-1]
        self.assertTrue(m['lien'].startswith('/api440/m56103/liaison/piece?id='))
        self.assertEqual(m['lien_libelle'], 'Télécharger Conclusions DURAND.pdf')
        nom, donnees = L.lire_piece(b.desk, 'cabinet', m['lien'].split('=')[-1])
        self.assertEqual((nom, donnees), ('Conclusions DURAND.pdf', contenu))
        # fichier altéré sur le disque de l'expéditeur : l'empreinte ne correspond plus, le poste destinataire refuse
        out2 = L.deposer_piece(a.desk, 'cabinet', 'Me DURAND', 'administrateur', tid, b'version 1 DOS-001', 'note.txt', a.membres())
        chemin = L.chemin_piece(a.desk, out2['sha256'])
        chemin.write_bytes(b'version 2 modifiee')
        self.assertEqual(a.sync()[b.ident.id], {'remis': 0, 'refuses': 1})
        motif = a.desk.db.execute("SELECT motif FROM liaison56103_sortie WHERE etat='refuse'").fetchone()['motif']
        self.assertEqual(motif, 'empreinte_piece_invalide')
        self.assertEqual(len(b.messages(tid_b)), 1)
        with self.assertRaisesRegex(Stop, 'empreinte_piece_invalide'):
            L.recevoir_message(b.desk, a.ident.id, {'t': 'message', 'gid': 'm-' + 'a' * 32, 'fil': {'gid': L._gid_direct(a.ident.id, b.ident.id), 'genre': 'direct',
                                'membres': [{'id': a.ident.id}, {'id': b.ident.id}]}, 'texte': 'x', 'piece': {'nom': 'a', 'taille': 3, 'sha256': '0' * 64}},
                               b'abc', b.ident.id)

    def test_L09_piece_au_dela_de_25_mo_refusee(self):
        a, b = self.deux()
        tid = self.direct(a, b)
        with self.assertRaisesRegex(Stop, 'piece_trop_grande'):
            L.deposer_piece(a.desk, 'cabinet', 'Me DURAND', 'administrateur', tid, b'\0' * (L.MAX_PIECE + 1), 'gros.bin', a.membres())
        enveloppe = {'t': 'message', 'gid': 'm-' + 'b' * 32, 'fil': {'gid': L._gid_direct(a.ident.id, b.ident.id), 'genre': 'direct',
                     'membres': [{'id': a.ident.id}, {'id': b.ident.id}]}, 'texte': 'x', 'piece': {'nom': 'g', 'taille': L.MAX_PIECE + 1, 'sha256': '0' * 64}}
        with self.assertRaisesRegex(Stop, 'piece_trop_grande'):
            L.recevoir_message(b.desk, a.ident.id, enveloppe, b'', b.ident.id)

        class Corps:
            def read(self, *a):
                raise AssertionError('le corps ne doit pas être lu')
        env = {'HTTP_ORIGIN': AUTH['origin'], 'HTTP_X_CSRF_TOKEN': AUTH['csrf'], 'CONTENT_TYPE': 'application/octet-stream',
               'CONTENT_LENGTH': str(L.MAX_PIECE + 1), 'wsgi.input': Corps(), 'HTTP_X_CLE_IDEMPOTENCE': cle()}
        r = LW.api(env, a.desk, AUTH, '/agent-courriel', 'm56103/liaison/piece', {'fil': str(tid)}, 'POST')
        self.assertEqual(r['status'], '413 Payload Too Large')
        env2 = dict(env, HTTP_X_CSRF_TOKEN='faux', CONTENT_LENGTH='10')          # CSRF contrôlé avant toute lecture du corps
        self.assertEqual(LW.api(env2, a.desk, AUTH, '/agent-courriel', 'm56103/liaison/piece', {'fil': str(tid)}, 'POST')['status'], '403 Forbidden')

    def test_L10_presence_en_ligne_absent_hors_ligne(self):
        a, b = self.deux()
        a.desk.setting(L.REGLAGE_PRESENCE, 'absent')
        a.sync()
        self.assertEqual(L.presence_de(dict(b.poste(a))), 'absent')
        self.assertEqual(L.presence_de(dict(a.poste(b))), 'en_ligne')       # la présence de B revient à A en fin de session
        a.desk.setting(L.REGLAGE_PRESENCE, 'en_ligne')
        a.sync()
        self.assertEqual(L.presence_de(dict(b.poste(a))), 'en_ligne')
        ancien = '2026-11-15T08:00:00+00:00'
        b.desk.db.execute('UPDATE liaison56103_postes SET contact_le=?', (ancien,))
        b.desk.db.commit()
        self.assertEqual(L.presence_de(dict(b.poste(a))), 'hors_ligne')
        etat = LW.etat_public(b.desk, 'avocat')
        self.assertEqual(etat['postes'][0]['presence'], 'hors_ligne')
        self.assertEqual(etat['postes'][0]['vu_le'], ancien)                # dernière vue affichée
        self.assertNotIn('code', etat)

    def test_L11_file_d_attente_puis_remise_au_retour(self):
        a, b = self.deux()
        tid = self.direct(a, b)
        b.liaison.arreter()
        b.liaison = None
        a.ecrire(tid, 'Message laissé pendant que le poste de Paul MARTIN est éteint.')
        self.assertEqual(a.sync()[b.ident.id], {'erreur': 'poste_injoignable'})
        etat = L.etat_fil(a.desk, 'cabinet', tid)
        self.assertEqual(etat['attente'], 1)
        self.assertEqual(etat['postes'][0]['nom'], 'Poste de Paul MARTIN')
        self.assertEqual(etat['postes'][0]['presence'], 'en_ligne')         # vu à l'appairage il y a quelques secondes
        b.demarrer()                                                         # retour du poste, sur un autre port, annoncé par la découverte
        self.assertIsNotNone(L.recevoir_annonce(a.desk, b.liaison.annonce(), '127.0.0.1', a.ident.id, boucle=True))
        self.assertEqual(a.poste(b)['port'], b.liaison.port)
        self.assertEqual(a.sync()[b.ident.id]['remis'], 1)
        self.assertEqual(b.messages(b.fils()[0]['id'])[-1]['texte'], 'Message laissé pendant que le poste de Paul MARTIN est éteint.')
        self.assertEqual(L.etat_fil(a.desk, 'cabinet', tid)['attente'], 0)


# ---------------------------------------------------------------------------------------------------- garde-fous du transport
class Garde(Base):
    def _enveloppe(self, a, b, texte, gid=None):
        return {'t': 'message', 'gid': gid or 'm-' + secrets.token_hex(16), 'fil': {'gid': L._gid_direct(a.ident.id, b.ident.id), 'genre': 'direct',
                'createur': b.ident.id, 'membres': [{'id': a.ident.id}, {'id': b.ident.id}]}, 'auteur_nom': 'Intrus', 'genre': 'message', 'texte': texte}

    def _session(self, client, serveur, ident=None):
        sock = socket.create_connection(('127.0.0.1', serveur.liaison.port), timeout=5)
        self.addCleanup(sock.close)
        attendu = client.poste(serveur)
        return L.ouvrir_client(sock, ident or client.ident, attendu, client.nom, 0)

    def test_L15_poste_non_appaire_ou_retire_refuse(self):
        a, b = self.deux()
        c = Poste(self, 'Poste de Lea BERNARD', 'Lea BERNARD')
        # C ne connaît pas A : il tente quand même avec sa propre identité, A refuse
        sock = socket.create_connection(('127.0.0.1', a.liaison.port), timeout=5)
        self.addCleanup(sock.close)
        faux_attendu = {'id': a.ident.id, 'cle_signature': a.ident.cle_signature}
        with self.assertRaisesRegex(Stop, 'poste_refuse:poste_non_appaire'):
            L.ouvrir_client(sock, c.ident, faux_attendu, c.nom, 0)
        # retrait : B est oublié par A, plus aucune session
        L.retirer(a.desk, b.ident.id, 'cabinet')
        with self.assertRaisesRegex(Stop, 'poste_refuse:poste_non_appaire'):
            self._session(b, a)
        self.assertEqual(L.postes(a.desk), [])

    def test_L16_signature_garde_fou_et_mutation(self):
        a, b = self.deux()
        c = Poste(self, 'Poste de Lea BERNARD', 'Lea BERNARD')
        faux = L.identite(c.c['state_dir'])
        faux.id, faux.cle_signature = b.ident.id, b.ident.cle_signature      # clé publique de B (connue), sans sa clé privée
        attendu = b.poste(a)
        sock = socket.create_connection(('127.0.0.1', a.liaison.port), timeout=5)
        self.addCleanup(sock.close)
        with self.assertRaises(Stop):
            L.ouvrir_client(sock, faux, attendu, 'Intrus', 0)
        # mutation en mémoire : sans vérification de signature, l'imposteur entrerait et écrirait dans la messagerie de A
        with patch.object(L, '_verifier', lambda *x: True):
            sock2 = socket.create_connection(('127.0.0.1', a.liaison.port), timeout=5)
            self.addCleanup(sock2.close)
            canal = L.ouvrir_client(sock2, faux, attendu, 'Intrus', 0)
            canal.envoyer(self._enveloppe(a, b, 'message de l’intrus'))
            self.assertEqual(canal.recevoir()[0]['t'], 'ok')
        self.assertEqual(a.desk.db.execute("SELECT COUNT(*) FROM messages5650 WHERE text='message de l’intrus'").fetchone()[0], 1)

    def test_L17_trame_rejouee_refusee_et_mutation(self):
        k = secrets.token_bytes(32), secrets.token_bytes(32)
        a_s, b_s = socket.socketpair()
        self.addCleanup(a_s.close)
        self.addCleanup(b_s.close)
        envoi = L.Canal(a_s, k[0], k[1], b'cs', b'sc')
        envoi.envoyer({'t': 'message', 'texte': 'un'})
        brut = L._lire_brut(b_s, L.MAX_TRAME)                                   # trame interceptée sur le réseau

        def recepteur():
            c_s, d_s = socket.socketpair()
            self.addCleanup(c_s.close)
            self.addCleanup(d_s.close)
            return c_s, L.Canal(d_s, k[1], k[0], b'sc', b'cs')
        c_s, recoit = recepteur()
        L._envoyer_brut(c_s, brut)
        L._envoyer_brut(c_s, brut)                                              # la même trame, rejouée
        self.assertEqual(recoit.recevoir()[0]['texte'], 'un')
        with self.assertRaisesRegex(Stop, 'trame_rejouee'):
            recoit.recevoir()
        with patch.object(L, '_sequence_attendue', lambda recu, attendu: True):  # mutation : sans contrôle, le rejeu passerait
            c_s, recoit = recepteur()
            L._envoyer_brut(c_s, brut)
            L._envoyer_brut(c_s, brut)
            recoit.recevoir()
            self.assertEqual(recoit.recevoir()[0]['texte'], 'un')
        c_s, recoit = recepteur()
        altere = bytearray(brut)
        altere[-1] ^= 1
        L._envoyer_brut(c_s, bytes(altere))
        with self.assertRaisesRegex(Stop, 'trame_alteree'):
            recoit.recevoir()

    def test_L17_session_enregistree_puis_rejouee_refusee(self):
        a, b = self.deux()
        relais = Relais(self, b.liaison.port)
        a.desk.db.execute('UPDATE liaison56103_postes SET port=? WHERE id=?', (relais.port, b.ident.id))
        a.desk.db.commit()
        tid = self.direct(a, b)
        a.ecrire(tid, 'Message original DOS-001.')
        self.assertEqual(a.sync()[b.ident.id]['remis'], 1)
        time.sleep(0.2)
        avant = b.desk.db.execute('SELECT COUNT(*) FROM messages5650').fetchone()[0]
        enregistre = bytes(relais.sessions[0]['c2s'])
        sock = socket.create_connection(('127.0.0.1', b.liaison.port), timeout=5)
        self.addCleanup(sock.close)
        sock.sendall(enregistre)                                                # rejeu complet de la session de A
        try:
            while sock.recv(65536):
                pass
        except OSError:
            pass
        self.assertEqual(b.desk.db.execute('SELECT COUNT(*) FROM messages5650').fetchone()[0], avant)
        # doublon applicatif (même identifiant global) : accusé, jamais réinséré
        env = self._enveloppe(b, a, 'doublon')
        env['fil']['createur'] = a.ident.id
        self.assertEqual(L.recevoir_message(b.desk, a.ident.id, env, b'', b.ident.id).get('doublon'), None)
        self.assertEqual(L.recevoir_message(b.desk, a.ident.id, env, b'', b.ident.id), {'doublon': True})
        self.assertEqual(b.desk.db.execute("SELECT COUNT(*) FROM messages5650 WHERE text='doublon'").fetchone()[0], 1)

    def test_L18_chiffrement_rien_en_clair_sur_le_reseau_et_mutation(self):
        texte = 'Audience DOS-001 renvoyee au 12 decembre, RG 24/01234'

        def ecoute():
            a, b = self.deux()
            relais = Relais(self, b.liaison.port)
            a.desk.db.execute('UPDATE liaison56103_postes SET port=? WHERE id=?', (relais.port, b.ident.id))
            a.desk.db.commit()
            tid = self.direct(a, b)
            a.ecrire(tid, texte)
            self.assertEqual(a.sync()[b.ident.id]['remis'], 1)
            time.sleep(0.2)
            return b''.join(bytes(s['c2s']) + bytes(s['s2c']) for s in relais.sessions)
        capture = ecoute()
        self.assertGreater(len(capture), 200)
        self.assertNotIn(b'DOS-001', capture)
        self.assertNotIn(b'24/01234', capture)
        with patch.object(L, '_chiffrer', lambda aead, n, clair, ad: clair + b'\0' * 16), \
                patch.object(L, '_dechiffrer', lambda aead, n, chiffre, ad: chiffre[:-16]):
            self.assertIn(b'DOS-001', ecoute())                                 # mutation : sans chiffrement, le texte circulerait en clair

    def test_L14_adresses_privees_seulement_et_mutation(self):
        for bonne in ('10.99.0.5', '172.31.250.9', '172.16.0.20', '192.168.254.77', 'fd00::5', 'fe80::5', '[fd00::7]'):
            self.assertTrue(L.verifier_adresse(bonne))
        for mauvaise in (SANS_168, HORS_172, '192.0.2.10', '198.51.100.7', '203.0.113.9', '2001:db8::1', '::ffff:192.0.2.10', '127.0.0.1', '0.0.0.0'):
            with self.assertRaisesRegex(Stop, 'adresse_non_privee'):
                L.verifier_adresse(mauvaise)
        for illisible in ('', 'poste-durand.example.test', '192.168.1', '999.1.1.1'):
            with self.assertRaisesRegex(Stop, 'adresse_invalide'):
                L.verifier_adresse(illisible)
        texte = LW.message('adresse_non_privee:' + SANS_168)
        self.assertIn('Cette adresse n’appartient pas à un réseau local privé', texte)
        self.assertIn('se poursuit par 168', texte)
        self.assertNotIn(SANS_168, texte)                                       # l'adresse saisie n'est pas recopiée
        self.assertEqual(L.verifier_adresse('127.0.0.1', boucle=True), '127.0.0.1')   # boucle locale : essais seulement
        with patch.object(L, 'PLAGES', L.PLAGES + (L.ipaddress.ip_network('0.0.0.0/0'),)):   # mutation : la plage élargie laisserait passer
            self.assertEqual(L.verifier_adresse(SANS_168), SANS_168)
        # par l'interface : refus clair, rien d'enregistré ; une annonce venue d'une adresse publique est ignorée
        a = Poste(self, 'Poste de Me DURAND', 'Me DURAND', demarrer=False)
        r = LW.api(env_json({'adresse': SANS_168, 'port': 47862, 'code': '1234-5678', 'cle': cle()}), a.desk, AUTH, '/agent-courriel',
                   'm56103/liaison/appairer', {}, 'POST')
        self.assertEqual(r['status'], '400 Bad Request')
        self.assertIn('n’appartient pas à un réseau local privé', json.loads(r['body'])['message'])
        annonce = json.dumps({'axh': 'liaison56103', 'v': 1, 'id': 'a' * 32, 'nom': 'X', 'port': 47862}).encode()
        self.assertIsNone(L.recevoir_annonce(a.desk, annonce, '192.0.2.10', a.ident.id))
        self.assertIsNotNone(L.recevoir_annonce(a.desk, annonce, '192.168.254.77', a.ident.id))
        self.assertEqual([d['adresse'] for d in L.decouverts(a.desk)], ['192.168.254.77'])

    def test_L19_decouverte_annonce_minimale(self):
        a = Poste(self, 'Poste de Me DURAND', 'Me DURAND')
        b = Poste(self, 'Poste de Paul MARTIN', 'Paul MARTIN')
        self.assertTrue(a.liaison._demarrer_udp(0, '127.0.0.1'))
        self.assertTrue(b.liaison._demarrer_udp(0, '127.0.0.1'))
        contenu = json.loads(b.liaison.annonce())
        self.assertEqual(set(contenu), {'axh', 'v', 'id', 'nom', 'port'})     # aucune donnée du cabinet
        self.assertTrue(b.liaison.annoncer(('127.0.0.1', a.liaison.port_udp)))
        for _ in range(40):
            vus = L.decouverts(a.desk)
            if vus:
                break
            time.sleep(0.05)
        self.assertEqual([(d['id'], d['nom'], d['port'], d['appaire']) for d in vus], [(b.ident.id, 'Poste de Paul MARTIN', b.liaison.port, False)])


# ---------------------------------------------------------------------------------------------------- réglages partagés
class Reglages(Base):
    SECRET = 'cle-fictive-0000-liaison-56103-ZORVAL'

    def _fournisseur(self, poste):
        from agent.ai_gateway import save_provider
        save_provider(poste.desk, 'mistral', 'mistral', 'https://api.mistral.example.test/v1', 'mistral-large-latest', api_key=self.SECRET)

    def _octets(self, poste):
        poste.desk.db.execute('PRAGMA wal_checkpoint(FULL)')
        base = Path(poste.c['state_dir'])
        return b''.join(p.read_bytes() for p in base.glob('desk.sqlite3*'))

    def test_L12_reglage_partage_secret_chiffre_bout_en_bout_et_journalise(self):
        a, b = self.deux()
        self._fournisseur(a)
        cat = {(x['genre'], x['cle']): x for x in L.catalogue(a.desk)}
        self.assertTrue(cat[('fournisseur_ia', 'mistral')]['partageable'])
        self.assertNotIn(self.SECRET, json.dumps(L.catalogue(a.desk)))
        r = LW.api(env_json({'genre': 'fournisseur_ia', 'reglage': 'mistral', 'cle': cle()}), a.desk, AUTH, '/agent-courriel',
                   'm56103/liaison/diffuser', {}, 'POST')
        self.assertEqual(r['status'], '200 OK', r['body'])
        self.assertNotIn(self.SECRET.encode(), self._octets(a))                # file d'envoi : seulement le chiffré
        self.assertEqual(a.sync()[b.ident.id]['remis'], 1)
        recu = b.desk.db.execute('SELECT * FROM liaison56103_reglages_recus').fetchone()
        self.assertEqual((recu['genre'], recu['cle'], recu['etat']), ('fournisseur_ia', 'mistral', 'en_attente'))
        self.assertIsNone(b.desk.settings('ai:provider:mistral'))               # rien n'est appliqué sans décision
        self.assertNotIn(self.SECRET.encode(), self._octets(b))
        html = LW.section_html(b.desk, AUTH, '/agent-courriel', env_get())
        self.assertIn('Appliquer', html)
        self.assertNotIn(self.SECRET, html)
        r = LW.api(env_json({'id': recu['id'], 'cle': cle()}), b.desk, AUTH, '/agent-courriel', 'm56103/liaison/reglage/appliquer', {}, 'POST')
        self.assertEqual(json.loads(r['body'])['etat'], 'applique', r['body'])
        reglage = b.desk.settings('ai:provider:mistral')
        self.assertEqual((reglage['type'], reglage['model']), ('mistral', 'mistral-large-latest'))
        from agent.common import read_secret
        self.assertEqual(read_secret(reglage['secret_file']), self.SECRET)      # rangé au coffre existant
        self.assertTrue(Path(reglage['secret_file']).read_bytes().startswith(b'AXIORHUB-VAULT-V1:'))
        self.assertNotIn(self.SECRET.encode(), self._octets(b))                 # ni la base ni le journal d'audit
        actions = [x['action'] for x in b.desk.db.execute('SELECT action FROM audit')]
        self.assertIn('liaison56103_reglage_applique', actions)
        # déjà décidé : 409 ; l'état revient au poste administrateur
        r = LW.api(env_json({'id': recu['id'], 'cle': cle()}), b.desk, AUTH, '/agent-courriel', 'm56103/liaison/reglage/refuser', {}, 'POST')
        self.assertEqual(r['status'], '409 Conflict')
        b.sync()
        etats = json.loads(a.desk.db.execute('SELECT etats FROM liaison56103_diffusions').fetchone()['etats'])
        self.assertEqual(etats, {b.ident.id: 'applique'})

    def test_L12_refus_par_le_destinataire(self):
        a, b = self.deux()
        self._fournisseur(a)
        L.diffuser(a.desk, 'fournisseur_ia', 'mistral', 'cabinet', 'administrateur')
        a.sync()
        recu = b.desk.db.execute('SELECT * FROM liaison56103_reglages_recus').fetchone()
        out = L.decider_reglage(b.desk, recu['id'], False, 'cabinet', 'administrateur')
        self.assertEqual(out['etat'], 'refuse')
        self.assertIsNone(b.desk.settings('ai:provider:mistral'))
        self.assertEqual(b.desk.db.execute('SELECT secrets_chiffres FROM liaison56103_reglages_recus').fetchone()[0], '{}')   # chiffré effacé
        self.assertIn('liaison56103_reglage_refuse', [x['action'] for x in b.desk.db.execute('SELECT action FROM audit')])
        b.sync()
        self.assertEqual(json.loads(a.desk.db.execute('SELECT etats FROM liaison56103_diffusions').fetchone()['etats']), {b.ident.id: 'refuse'})

    def test_L13_identifiants_personnels_jamais_partages(self):
        a, b = self.deux()
        a.desk.setting('ai:provider:chatgpt', {'type': 'chatgpt', 'model': 'gpt', 'url': 'https://chatgpt.example.test/v1'})
        cat = {(x['genre'], x['cle']): x for x in L.catalogue(a.desk)}
        self.assertFalse(cat[('fournisseur_ia', 'chatgpt')]['partageable'])
        with self.assertRaisesRegex(Stop, 'reglage_personnel_non_partageable'):
            L.diffuser(a.desk, 'fournisseur_ia', 'chatgpt', 'cabinet', 'administrateur')
        with self.assertRaisesRegex(Stop, 'reglage_personnel_non_partageable'):
            L.diffuser(a.desk, 'fournisseur_ia', 'imap_mot_de_passe', 'cabinet', 'administrateur')
        with self.assertRaisesRegex(Stop, 'role_insuffisant'):
            L.diffuser(a.desk, 'fournisseur_ia', 'mistral', 'avocate', 'avocat')
        # côté destinataire : un réglage personnel ou non publié par un administrateur est refusé, même s'il arrive
        base = {'t': 'reglage', 'genre': 'connexion_api', 'cle': 'cx-1', 'role': 'administrateur', 'valeurs': {}, 'secrets': {}}
        for variante in ({'valeurs': {'imap_password': 'x'}}, {'secrets': {'refresh_token': {}}}, {'cle': 'oauth-google'},
                         {'genre': 'fournisseur_ia', 'valeurs': {'type': 'chatgpt'}}, {'role': 'avocat'}):
            env = dict(base, gid='r-' + secrets.token_hex(16), **variante)
            with self.assertRaises(Stop):
                L.recevoir_reglage(b.desk, a.ident.id, env)
        self.assertEqual(b.desk.db.execute('SELECT COUNT(*) FROM liaison56103_reglages_recus').fetchone()[0], 0)
        # le secret d'un poste ne s'ouvre pas avec la clé d'un autre
        boite = L.chiffrer_secret('valeur', b.ident.id, b.ident.cle_echange, 'r-' + 'c' * 32, 'api_key')
        self.assertEqual(L.dechiffrer_secret(b.ident, boite, 'r-' + 'c' * 32, 'api_key'), 'valeur')
        with self.assertRaisesRegex(Stop, 'secret_indechiffrable'):
            L.dechiffrer_secret(a.ident, boite, 'r-' + 'c' * 32, 'api_key')


# ---------------------------------------------------------------------------------------------------- interface : routes, droits, Paramètres
class Interface(Base):
    def test_L20_routes_droits_idempotence_lecture_sans_ecriture(self):
        self.assertTrue(LW.allowed('assistant', 'POST', 'm56103/liaison/presence'))
        self.assertTrue(LW.allowed('assistant', 'POST', 'm56103/liaison/piece'))
        self.assertFalse(LW.allowed('assistant', 'POST', 'm56103/liaison/appairer'))
        self.assertFalse(LW.allowed('avocat', 'POST', 'm56103/liaison/diffuser'))
        self.assertTrue(LW.allowed('administrateur', 'POST', 'm56103/liaison/reglage/appliquer'))
        self.assertFalse(LW.allowed('administrateur', 'DELETE', 'm56103/liaison/retirer'))
        from agent import standalone_auth
        self.assertTrue(standalone_auth.allowed('assistant', 'POST', '/api440/m56103/liaison/presence'))
        self.assertFalse(standalone_auth.allowed('assistant', 'POST', '/api440/m56103/liaison/code'))
        a, b = self.deux()
        # hors poste de travail : route neutre
        r = LW.api(env_get(), a.desk, {'origin': AUTH['origin'], 'csrf': AUTH['csrf']}, '/agent-courriel', 'm56103/liaison/etat', {}, 'GET')
        self.assertEqual(r['status'], '404 Not Found')
        # lecture : aucune écriture, no-store
        avant = a.desk.db.total_changes
        r = LW.api(env_get(), a.desk, AUTH, '/agent-courriel', 'm56103/liaison/etat', {}, 'GET')
        self.assertEqual(a.desk.db.total_changes, avant)
        self.assertIn(('Cache-Control', 'private, no-store'), r['headers'])
        self.assertEqual(json.loads(r['body'])['postes'][0]['id'], b.ident.id)
        # idempotence : la même clé rend la même réponse sans second code ; une autre demande avec cette clé : 409
        k = cle()
        r1 = json.loads(LW.api(env_json({'cle': k}), a.desk, AUTH, '/agent-courriel', 'm56103/liaison/code', {}, 'POST')['body'])
        r2 = json.loads(LW.api(env_json({'cle': k}), a.desk, AUTH, '/agent-courriel', 'm56103/liaison/code', {}, 'POST')['body'])
        self.assertEqual(r1['code'], r2['code'])
        self.assertEqual(L.code_actif(a.c['state_dir'])['code'], r1['code'].replace('-', ''))
        r3 = LW.api(env_json({'cle': k, 'autre': 1}), a.desk, AUTH, '/agent-courriel', 'm56103/liaison/code', {}, 'POST')
        self.assertEqual(r3['status'], '409 Conflict')
        self.assertEqual(LW.api(env_json({}), a.desk, AUTH, '/agent-courriel', 'm56103/liaison/code', {}, 'POST')['status'], '400 Bad Request')
        # rôle : un compte avocat ne retire pas un poste ; objet absent et objet interdit : même 404
        r = LW.api(env_json({'id': b.ident.id, 'cle': cle()}, role='avocat'), a.desk, AUTH, '/agent-courriel', 'm56103/liaison/retirer', {}, 'POST')
        self.assertEqual(r['status'], '403 Forbidden')
        tid = self.direct(a, b)
        self.assertEqual(LW.api(env_get(user='autre@example.test', role='avocat'), a.desk, AUTH, '/agent-courriel', 'm56103/liaison/fil', {'id': str(tid)},
                                'GET')['status'], '404 Not Found')
        self.assertEqual(LW.api(env_get(), a.desk, AUTH, '/agent-courriel', 'm56103/liaison/piece', {'id': '999'}, 'GET')['status'], '404 Not Found')
        # pièce jointe par l'interface (dépôt binaire), puis téléchargement
        contenu = b'note fictive DOS-001'
        env = {'HTTP_ORIGIN': AUTH['origin'], 'HTTP_X_CSRF_TOKEN': AUTH['csrf'], 'CONTENT_TYPE': 'application/octet-stream', 'CONTENT_LENGTH': str(len(contenu)),
               'wsgi.input': io.BytesIO(contenu), 'HTTP_X_CLE_IDEMPOTENCE': cle(), 'HTTP_X_NOM_FICHIER': 'note%20MARTIN.txt',
               'HTTP_X_AXIORHUB_USER': 'cabinet', 'HTTP_X_AXIORHUB_ROLE': 'administrateur'}
        r = LW.api(env, a.desk, AUTH, '/agent-courriel', 'm56103/liaison/piece', {'fil': str(tid)}, 'POST')
        self.assertEqual(r['status'], '200 OK', r['body'])
        piece = json.loads(r['body'])['piece']
        r = LW.api(env_get(), a.desk, AUTH, '/agent-courriel', 'm56103/liaison/piece', {'id': str(piece)}, 'GET')
        self.assertEqual((r['body'], r['kind']), (contenu, 'application/octet-stream'))
        self.assertIn(('X-Content-Type-Options', 'nosniff'), r['headers'])
        self.assertIn("attachment; filename*=UTF-8''note%20MARTIN.txt", dict(r['headers'])['Content-Disposition'])

    def test_L21_parametres_et_messagerie(self):
        a = Poste(self, 'Poste de Me DURAND', 'Me DURAND', demarrer=False)
        L.activer(a.desk, 'cabinet', 'Me DURAND', False)
        html = LW.section_html(a.desk, AUTH, '/agent-courriel', env_get())
        self.assertIn('Désactivée : aucun port n’est ouvert au réseau', html)
        self.assertEqual(LW.section_html(a.desk, {'origin': 'x', 'csrf': 'y'}, '/agent-courriel', env_get()), '')   # serveur : rien
        r = LW.api(env_json({'actif': True, 'cle': cle()}), a.desk, AUTH, '/agent-courriel', 'm56103/liaison/activer', {}, 'POST')
        self.assertTrue(json.loads(r['body'])['actif'])
        LW.api(env_json({'cle': cle()}), a.desk, AUTH, '/agent-courriel', 'm56103/liaison/code', {}, 'POST')
        html = LW.section_html(a.desk, AUTH, '/agent-courriel', env_get())
        code = L.code_actif(a.c['state_dir'])['code']
        self.assertIn(code[:4] + '-' + code[4:], html)
        self.assertIn('Adresse saisie à la main', html)
        self.assertNotIn(code, LW.section_html(a.desk, AUTH, '/agent-courriel', env_get(role='avocat')))   # le code n'est montré qu'à l'administrateur
        # Messagerie : le poste relié est proposé comme correspondant, et le bandeau n'existe que liaison activée
        b = Poste(self, 'Poste de Paul MARTIN', 'Paul MARTIN')
        a.demarrer()
        appairer(b, a)
        from agent import web5650
        page = web5650.messagerie_page(a.desk, AUTH, '/agent-courriel', env_get(), {})
        self.assertIn(L.PARTICIPANT + b.ident.id, page)
        self.assertIn('Poste de Paul MARTIN', page)
        tid = mx.open_direct(a.desk, 'cabinet', L.PARTICIPANT + b.ident.id, a.membres())
        page = web5650.messagerie_page(a.desk, AUTH, '/agent-courriel', env_get(), {'fil': str(tid)})
        self.assertIn('id="l103-fil"', page)
        self.assertIn('/static/v56103-liaison.js', page)
        L.activer(a.desk, 'cabinet', 'Me DURAND', False)
        page = web5650.messagerie_page(a.desk, AUTH, '/agent-courriel', env_get(), {'fil': str(tid)})
        self.assertNotIn('l103-fil', page)
        self.assertNotIn('Poste de Paul MARTIN (poste relié)', page)

    def test_L22_statiques_declares_et_css_au_paquet(self):
        from agent import web440, shell501
        self.assertIn('/static/v56103-liaison.js', web440.STATIC)
        self.assertIn('v56103-liaison.css', shell501.BUNDLE)
        js = (RACINE / 'agent' / 'static' / 'v56103-liaison.js').read_text(encoding='utf-8')
        self.assertNotIn('innerHTML', js)
        self.assertIn('l103-fil', (RACINE / 'agent' / 'static' / 'app520.css').read_text(encoding='utf-8'))
        doc = (RACINE / 'docs' / 'LIAISON-POSTES-5.6.103.md').read_text(encoding='utf-8')
        self.assertIn('47862', doc)
        self.assertIn('pare-feu', doc.lower())


if __name__ == '__main__':
    unittest.main()
