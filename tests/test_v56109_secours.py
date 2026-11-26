"""5.6.109 (lot R) : incident d'un serveur de production en 5.6.108 (base du bureau endommagée après la sauvegarde).

R1 essai des verrous POSIX (Linux seulement) ; R2 aucune ouverture directe d'une base vivante (garde statique, espion) ; R3 sauvegarde plus sûre
(copie dans un processus enfant, échec lisible, planification jamais avalée, alerte de 48 h visible) ; R4 base illisible : arrêt typé, page 503,
aucune écriture ni suppression, services qui sortent sans boucle ; R5 outil de secours en lecture seule par défaut, remise en place sans écrasement.
Bases fictives uniquement ; aucun appel réseau."""
import builtins
import hashlib
import importlib.util
import io
import json
import os
from pathlib import Path
import shutil
import sqlite3
import subprocess
import sys
import tempfile
import time
import unittest
from datetime import datetime, timedelta, timezone
from unittest.mock import patch

import test_v5688_reversibilite as rev
import essai_verrous56109 as essai
import garde_sqlite56109 as garde
from agent import desk as desk_module, health567, operations, reversibilite5688 as rv, sauvegarde5688 as sv, secours56109 as sc
from agent.common import Stop, private_json
from agent.desk import Desk

ROOT = Path(__file__).resolve().parents[1]


def empreinte_fichier(chemin):
    return hashlib.sha256(Path(chemin).read_bytes()).hexdigest()   # lot R : hors service (base fictive fermée, contrôle du test)


def instantane(dossier):
    """{chemin relatif: sha256} de tous les fichiers (bases fermées du test)."""
    out = {}
    for p in sorted(Path(dossier).rglob('*')):
        if p.is_file():
            out[p.relative_to(dossier).as_posix()] = empreinte_fichier(p)
    return out


def abimer(chemin, debut=100, longueur=3900):
    """Base rendue illisible par écrasement d'octets (page du schéma), sur une copie fictive."""
    with open(chemin, 'r+b') as f:   # lot R : hors service (base fictive fermée)
        f.seek(debut)
        f.write(bytes((i * 37 + 11) % 256 for i in range(longueur)))


ANCIEN = ROOT / 'tests' / 'donnees' / 'sauvegarde5688_v56108.txt'


def _ancien_module():
    """sauvegarde5688 tel que livré en 5.6.108, chargé à part : sert aux contre-exemples.

    Gardé comme donnée de test (``tests/donnees/sauvegarde5688_v56108.txt``). Il était d'abord relu dans l'historique Git du dépôt de
    travail : la recette GitHub, dont l'historique est autre, retombait alors sur le module CORRIGÉ et les trois contre-exemples échouaient
    (constat de la recette publiée de la 5.6.109). Sans la donnée, ou si elle n'est pas le module défectueux, les contre-exemples sont sautés."""
    try:
        source = ANCIEN.read_text(encoding='utf-8')
    except OSError:
        return None
    if "with open(chemin, 'rb') as f:" not in source or '_est_sqlite' not in source:
        return None
    spec = importlib.util.spec_from_loader('agent.sauvegarde5688_ancien56109', loader=None)
    module = importlib.util.module_from_spec(spec)
    module.__package__ = 'agent'
    exec(compile(source, 'sauvegarde5688_ancien', 'exec'), module.__dict__)
    return module, source


class Espion:
    """Relève toute ouverture Python d'un fichier de base (open, io.open, os.open) ; sqlite3 (code C) n'y passe pas."""

    def __init__(self, racine):
        self.racine, self.vus = str(Path(racine).resolve()), []

    def _noter(self, chemin):
        try:
            texte = str(Path(os.fsdecode(chemin)).resolve())
        except (TypeError, ValueError, OSError):
            return
        if texte.startswith(self.racine) and texte.lower().endswith(sc.SUFFIXES) and sv.TRAVAIL not in texte:
            self.vus.append(texte)

    def __enter__(self):
        vrai_open, vrai_os_open = builtins.open, os.open

        def faux_open(fichier, *a, **k):
            if isinstance(fichier, (str, bytes, os.PathLike)):
                self._noter(fichier)
            return vrai_open(fichier, *a, **k)

        def faux_os_open(fichier, *a, **k):
            self._noter(fichier)
            return vrai_os_open(fichier, *a, **k)
        self.patches = [patch('builtins.open', faux_open), patch('io.open', faux_open), patch('os.open', faux_os_open)]
        for p in self.patches:
            p.start()
        return self

    def __exit__(self, *exc):
        for p in reversed(self.patches):
            p.stop()


# ------------------------------------------------------------------------------------------------ R1 : essai Linux
class Verrous(unittest.TestCase):
    def test_R1_essai_declare_non_applicable_hors_linux(self):
        if sys.platform.startswith('linux'):
            self.skipTest('Linux : voir test_R1_essai_linux')
        rendu = essai.essai()
        self.assertFalse(rendu['applicable'])
        self.assertIn('Linux', rendu['motif'])

    @unittest.skipUnless(sys.platform.startswith('linux'), 'verrous POSIX : essai réel seulement sous Linux')
    def test_R1_essai_linux(self):
        rendu = essai.essai()
        print('\nlot R, essai des verrous POSIX : ' + json.dumps({k: rendu[k] for k in ('conclusion', 'sqlite', 'python')}) + ' ; essai : ' +
              json.dumps({k: rendu['essai'][k] for k in ('verrou_partage_vu_avant', 'verrou_perdu_apres_open_close', 'wal_supprime_par_b', 'base_integre',
                                                       'lignes_perdues')}) + ' ; témoin : ' +
              json.dumps({k: rendu['temoin'][k] for k in ('verrou_partage_vu_avant', 'verrou_perdu_apres_open_close', 'base_integre', 'lignes_perdues')}))
        self.assertTrue(rendu['applicable'])
        self.assertTrue(rendu['temoin']['base_integre'])          # le témoin, sans open()/close(), reste sain
        self.assertEqual(rendu['temoin']['lignes_perdues'], 0)
        self.assertFalse(rendu['temoin']['verrou_perdu_apres_open_close'])
        self.assertIn(rendu['conclusion'], ('demontre', 'plausible', 'ecarte'))


# ------------------------------------------------------------------------------------------------ R2 : aucune ouverture directe
class Garde(unittest.TestCase):
    def test_R2_garde_statique_aucune_ouverture_directe_dans_le_produit(self):
        trouvees = garde.analyser(ROOT, garde.fichiers_du_produit(ROOT))
        self.assertEqual(trouvees, [], '\n'.join(trouvees))

    def test_R2_contre_exemple_la_garde_voit_les_motifs_dangereux(self):
        exemples = {
            "def f(chemin):\n    with open(chemin, 'rb') as f:\n        return f.read(16) == b'SQLite format 3\\x00'\n": 1,
            "from pathlib import Path\ndef g(c):\n    p = Path(c['state_dir']) / 'desk.sqlite3'\n    return p.read_bytes()\n": 1,
            "import shutil\nBASE = 'documents.sqlite3'\ndef h(d):\n    shutil.copyfile(d / BASE, d / 'x')\n": 1,
            "import os\ndef _p(d):\n    return d / 'search490.sqlite3'\ndef k(d):\n    os.close(os.open(_p(d), os.O_CREAT))\n": 1,
            "import sqlite3\ndef ok(d):\n    return sqlite3.connect(str(d / 'desk.sqlite3'))\n": 0,
        }
        for source, attendu in exemples.items():
            self.assertEqual(len(garde.ouvertures_directes(source)), attendu, source)

    def test_R2_contre_exemple_la_garde_echouait_sur_la_5_6_108(self):
        ancien = _ancien_module()
        if ancien is None:
            self.skipTest('module de référence 5.6.108 absent')
        self.assertTrue(any('SQLite format 3' in x for _, x in garde.ouvertures_directes(ancien[1])))

    def test_R2_upgrade_corrige_par_l_integrateur(self):
        """upgrade.py lisait l'en-tête des bases par open() (signalé par le lot R) ; corrigé à l'intégration (I1) : reconnaissance par sqlite3 en
        lecture seule (``upgrade.est_base_sqlite``). Ce test constatait le défaut ; il exige maintenant son absence (upgrade.py est aussi dans
        ``garde.fichiers_du_produit``)."""
        trouvees = garde.analyser(ROOT, [ROOT / 'upgrade.py'])
        self.assertEqual(trouvees, [], trouvees)


class Espionnage(rev.Base):
    def _bases_fictives(self):
        etat = Path(self.desk.c['state_dir'])
        db = sqlite3.connect(etat / 'annexe.sqlite3')
        db.execute('CREATE TABLE t(x)')
        db.execute("INSERT INTO t VALUES ('ZORVAL')")
        db.commit()
        db.close()
        return etat

    def test_R2_aucune_ouverture_python_d_une_base_pendant_la_sauvegarde(self):
        etat = self._bases_fictives()
        self.phrase()
        with Espion(etat) as espion:
            out = self.sauver()
        self.assertEqual(out['statut'], 'reussie')
        self.assertEqual(espion.vus, [])
        self.assertIn('etat/annexe.sqlite3', {f for f in self._manifeste()})

    def _manifeste(self):
        cible = self.extraire()
        return [f['chemin'] for f in json.loads((cible / 'manifeste.json').read_text(encoding='utf-8'))['fichiers']]

    def test_R2_contre_exemple_la_5_6_108_ouvrait_la_base_vivante(self):
        ancien = _ancien_module()
        if ancien is None:
            self.skipTest('module de référence 5.6.108 absent')
        etat = self._bases_fictives()
        self.phrase()
        with Espion(etat) as espion:
            ancien[0].executer(self.desk, 'manuel')
        self.assertTrue(any(v.endswith('desk.sqlite3') for v in espion.vus), espion.vus)

    def test_R3_copie_dans_un_processus_enfant(self):
        self._bases_fictives()
        self.phrase()
        vrai = subprocess.run
        appels = []

        def espion(cmd, *a, **k):
            appels.append(cmd)
            return vrai(cmd, *a, **k)
        with patch('subprocess.run', espion):
            self.assertEqual(self.sauver()['statut'], 'reussie')
        enfants = [c for c in appels if isinstance(c, list) and c[:3] == [sys.executable, '-I', '-c'] and c[3] == sv.COPIE_ENFANT]
        self.assertGreaterEqual(len(enfants), 2)                    # desk.sqlite3 et annexe.sqlite3 au moins
        self.assertTrue(any(c[4].endswith('desk.sqlite3') for c in enfants))

    def test_R3_copie_en_echec_motif_lisible_original_intact(self):
        etat = self._bases_fictives()
        abimer(etat / 'annexe.sqlite3')
        avant = empreinte_fichier(etat / 'annexe.sqlite3')
        self.phrase()
        with self.assertRaisesRegex(Stop, 'copie_base_impossible'):
            self.sauver()
        self.assertEqual(empreinte_fichier(etat / 'annexe.sqlite3'), avant)
        row = self.desk.db.execute('SELECT * FROM sauvegardes5688 ORDER BY debut DESC LIMIT 1').fetchone()
        self.assertEqual((row['statut'], row['erreur']), ('echec', 'copie_base_impossible'))
        avert = json.loads(row['avertissements'])
        self.assertTrue(any('annexe.sqlite3' in a and 'Base non sauvegardée' in a for a in avert), avert)
        self.assertIn('Une base n’a pas pu être copiée', rv.motif('copie_base_impossible'))
        self.assertEqual(self.desk.settings(sv.REUSSITE, ''), '')
        self.assertTrue((self.dest() / row['nom']).is_file())       # archive relue, sans la base abîmée, conservée pour le reste

    def test_R3_echec_ne_purge_aucune_ancienne_archive(self):
        etat = self._bases_fictives()
        self.phrase()
        self.sauver()
        anciennes = sorted(p.name for p in self.dest().iterdir())
        abimer(etat / 'annexe.sqlite3')
        with patch.object(sv, 'purger_local', side_effect=AssertionError('rétention après un échec')):
            with self.assertRaises(Stop):
                self.sauver()
        self.assertTrue(set(anciennes) <= {p.name for p in self.dest().iterdir()})

    def test_R3_base_principale_illisible_aucune_archive(self):
        self.phrase()
        etat = Path(self.desk.c['state_dir'])
        with patch.object(sv, 'copier_base', return_value={'ok': False, 'quick_check': False, 'motif': 'base_illisible'}):
            with self.assertRaisesRegex(Stop, 'base_principale_illisible'):
                self.sauver()
        self.assertFalse([p for p in self.dest().iterdir() if sv.NOM.match(p.name)])
        self.assertTrue((etat / 'desk.sqlite3').is_file())


# ------------------------------------------------------------------------------------------------ R3 : planification et alerte
class Planification(rev.Base):
    def test_R3_file_automatique_pleine_la_sauvegarde_passe_quand_meme(self):
        self.actif(heure=0)
        for i in range(desk_module.AUTOMATIC_LIMIT):
            self.desk.db.execute("INSERT INTO jobs(kind,args,status,created,priority,origin) VALUES ('index',?,'pending',?,80,'automatic')",
                                 (json.dumps({'n': i}), self.desk.now()))
        self.desk.db.commit()
        with patch('agent.cadence5662.busy', return_value=''):
            self.assertTrue(sv.schedule(self.desk))
        self.assertTrue(self.desk.db.execute("SELECT 1 FROM jobs WHERE kind='sauvegarde5688' AND status='pending'").fetchone())

    def test_R3_refus_de_mise_en_file_visible_jamais_avale(self):
        self.actif(heure=0)
        with patch.object(self.desk, 'enqueue', side_effect=Stop('file_attente_pleine')):
            self.assertFalse(sv.schedule(self.desk))
        refus = self.desk.settings(sv.PLANIFICATION)
        self.assertEqual(refus['motif'], 'file_attente_pleine')
        self.assertEqual(sv.etat(self.desk)['planification']['motif'], 'file_attente_pleine')
        self.assertFalse(sv.due(self.desk))                         # pas de nouvel essai à chaque passage de la boucle…
        now = datetime.now(timezone.utc) + timedelta(minutes=11)
        self.assertTrue(sv.due(self.desk, now))                     # … mais dix minutes plus tard

    def test_R3_contre_exemple_5_6_108_refus_avale_par_la_boucle(self):
        ancien = _ancien_module()
        if ancien is None:
            self.skipTest('module de référence 5.6.108 absent')
        self.actif(heure=0)
        with patch.object(self.desk, 'enqueue', side_effect=Stop('file_attente_pleine')):
            with self.assertRaises(Stop):
                ancien[0].schedule(self.desk)                       # l'erreur montait ; automation_tick l'avalait sans trace
        self.assertIsNone(self.desk.settings(sv.PLANIFICATION, None))


class WebBase(unittest.TestCase):
    """Mêmes réglages que ``test_v5688_reversibilite.Web`` (interface, compte, requête), sans hériter de ses tests."""
    ORIGIN = rev.Web.ORIGIN
    setUp = rev.Web.setUp
    request = rev.Web.request


class AlerteVisible(WebBase):
    def test_R3_alerte_48_heures_affichee_dans_aujourd_hui(self):
        sv.definir_phrase(self.desk, rev.PHRASE, rev.PHRASE, 'admin')
        sv.enregistrer(self.desk, {'actif': True}, 'admin')
        self.desk.setting(sv.REUSSITE, (datetime.now(timezone.utc) - timedelta(hours=72)).isoformat())
        self.assertEqual(sv.alerte(self.desk), 'ancienne')
        r = self.request('/aujourdhui')
        html = r['body'].decode('utf-8')
        self.assertTrue(r['status'].startswith('200'), r['status'])
        self.assertIn('il y a plus de 48 heures', html)
        self.desk.setting(sv.REUSSITE, datetime.now(timezone.utc).isoformat())
        self.assertNotIn('il y a plus de 48 heures', self.request('/aujourdhui')['body'].decode('utf-8'))


# ------------------------------------------------------------------------------------------------ R4 : base illisible
class Illisible(WebBase):
    def setUp(self):
        super().setUp()
        self.etat = Path(self.f.c['state_dir'])
        self.base = self.etat / 'desk.sqlite3'
        sain = sqlite3.connect(self.etat.parent / 'desk-saine-lotR.sqlite3')
        self.desk.db.commit()
        self.desk.db.backup(sain)
        sain.close()
        self.saine = self.etat.parent / 'desk-saine-lotR.sqlite3'
        self.desk.db.close()
        abimer(self.base)
        for suffixe in ('-wal', '-shm'):
            p = Path(str(self.base) + suffixe)
            if p.exists():
                try:
                    p.unlink()
                except OSError:
                    pass
        desk_module._SCHEMA_READY.clear()                            # nouveau processus (redémarrage d'un service)
        self.addCleanup(desk_module._SCHEMA_READY.clear)
        self.desk.db = sqlite3.connect(':memory:')                  # la fermeture du test ne touche plus la base

    def test_R4_desk_leve_un_arret_type_sans_rien_ecrire_ni_supprimer(self):
        avant = instantane(self.etat)
        with self.assertRaises(sc.BaseIllisible) as ctx:
            Desk(self.f.c)
        self.assertEqual(ctx.exception.base, 'desk.sqlite3')
        self.assertNotIsInstance(ctx.exception, Stop)
        apres = instantane(self.etat)
        self.assertEqual(apres['desk.sqlite3'], avant['desk.sqlite3'])            # aucune écriture, aucune réparation
        self.assertTrue(set(avant) <= set(apres), set(avant) - set(apres))        # aucun fichier supprimé
        alerte = json.loads((self.etat / sc.ALERTE).read_text(encoding='utf-8'))
        self.assertEqual((alerte['etat'], alerte['base']), ('active', 'desk.sqlite3'))
        with patch.object(desk_module.sqlite3, 'connect', side_effect=AssertionError('SQLite rouvert')):
            with self.assertRaises(sc.BaseIllisible):                              # même fichier : plus aucune ouverture
                Desk(self.f.c)

    def test_R4_fichier_qui_n_est_pas_une_base(self):
        abimer(self.base, 0, 64)
        with self.assertRaises(sc.BaseIllisible):
            Desk(self.f.c)

    def test_R4_page_d_arret_503_lisible(self):
        r = self.request('/')
        corps = r['body'].decode('utf-8')
        self.assertTrue(r['status'].startswith('503'), r['status'])
        for texte in ('AxiorHub est arrêté par sécurité', 'Vos courriels, dans votre messagerie.', 'Vos documents et vos dossiers',
                      'docs/REVERSIBILITE-5.6.88.md', 'python3 -m agent.secours56109'):
            self.assertIn(texte, corps)
        for interdit in ('Traceback', 'malformed', str(self.etat), 'sqlite3.DatabaseError'):
            self.assertNotIn(interdit, corps)
        j = self.request('/api440/rev5688/etat')
        self.assertTrue(j['status'].startswith('503'), j['status'])
        self.assertEqual(json.loads(j['body'])['error'], 'base_illisible')
        self.assertEqual(empreinte_fichier(self.base), empreinte_fichier(self.base))

    def test_R4_page_d_arret_aussi_quand_la_corruption_survient_en_cours_de_vie(self):
        with patch.object(desk_module.Desk, '_runtime', side_effect=sqlite3.DatabaseError('database disk image is malformed')):
            shutil.copyfile(self.saine, self.base)                  # lot R : hors service (base fictive du test)
            desk_module._SCHEMA_READY.clear()
            r = self.request('/')
        self.assertTrue(r['status'].startswith('503'), r['status'])

    def test_R4_contre_exemple_une_autre_erreur_reste_une_erreur_500(self):
        shutil.copyfile(self.saine, self.base)                      # lot R : hors service (base fictive du test)
        with patch.object(desk_module.Desk, '_runtime', side_effect=RuntimeError('fictif')):
            r = self.request('/')
        self.assertTrue(r['status'].startswith('500'), r['status'])
        self.assertIsNone(sc.reponse_web({}, 'text/html', '', None, sqlite3.OperationalError('database is locked')))

    def test_R4_remplacement_par_une_copie_saine_leve_l_alerte_sans_la_supprimer(self):
        with self.assertRaises(sc.BaseIllisible):
            Desk(self.f.c)
        os.rename(self.base, self.etat.parent / 'desk-abimee-lotR.sqlite3')
        shutil.copyfile(self.saine, self.base)                      # lot R : hors service (base fictive du test)
        d = Desk(self.f.c)
        d.db.close()
        alerte = json.loads((self.etat / sc.ALERTE).read_text(encoding='utf-8'))
        self.assertEqual(alerte['etat'], 'levee')

    def test_R4_sonde_de_sante(self):
        self.assertEqual(health567.check(self.config)['reason'], 'base_illisible')

    def _lancer(self, *args, attente_max='0'):
        env = {**os.environ, 'AXIORHUB_SECOURS_ATTENTE_MAX': attente_max, 'PYTHONIOENCODING': 'utf-8', 'AXIORHUB_STARTUP_WAIT': '2'}
        debut = time.monotonic()
        script, *reste = args
        options = ['--config', str(self.config)]
        commande = [script, *reste, *options] if script == 'web.py' else [script, *options, *reste]
        fini = subprocess.run([sys.executable, *commande], cwd=ROOT, env=env, capture_output=True, timeout=120)
        return fini.returncode, fini.stderr.decode('utf-8', 'replace'), time.monotonic() - debut

    def test_R4_worker_sort_avec_un_message_unique_et_sans_boucle(self):
        code, err, _ = self._lancer('web.py', 'worker')
        self.assertEqual(code, sc.CODE_SORTIE, err)
        self.assertEqual(err.count('ARRÊT (base illisible)'), 1, err)
        self.assertNotIn('Traceback', err)
        code, err, duree = self._lancer('web.py', 'worker', attente_max='1')
        self.assertEqual(code, sc.CODE_SORTIE, err)
        self.assertIn('arrêt n° 2', err)
        self.assertGreaterEqual(duree, 1.0)                          # délai croissant avant la sortie : plus de redémarrage toutes les 5 s
        alerte = json.loads((self.etat / sc.ALERTE).read_text(encoding='utf-8'))
        self.assertEqual(alerte['services']['worker-1'], 2)

    def test_R4_veille_et_manage_run_sortent_proprement(self):
        code, err, _ = self._lancer('web.py', 'watch')
        self.assertEqual(code, sc.CODE_SORTIE, err)
        self.assertNotIn('Traceback', err)
        code, err, _ = self._lancer('manage.py', 'run')
        self.assertEqual(code, sc.CODE_SORTIE, err)
        self.assertIn('ARRÊT (base illisible)', err)
        self.assertNotIn('controle_pause_indisponible', err)

    def test_R4_delai_croissant_borne(self):
        self.assertEqual([sc.delai(n, 900) for n in (1, 2, 3, 4)], [0.0, 15.0, 30.0, 60.0])
        self.assertEqual(sc.delai(40, 900), 900.0)

    def test_R4_jamais_de_reparation_ni_de_suppression_dans_le_code(self):
        source = (ROOT / 'agent' / 'secours56109.py').read_text(encoding='utf-8')
        for motif in ('unlink(', 'rmtree', 'os.remove', 'REINDEX', 'VACUUM', '.recover', 'writable_schema'):
            self.assertNotIn(motif, source)


# ------------------------------------------------------------------------------------------------ R5 : outil de secours
class Secours(Illisible):
    def test_R5_diagnostic_en_lecture_seule_par_defaut(self):
        avant = instantane(self.etat)
        sortie = io.StringIO()
        with patch('sys.stdout', sortie):
            code = sc.main(['--etat', str(self.etat)])
        self.assertEqual(code, 1)
        rendu = json.loads(sortie.getvalue())
        etats = {b['base']: b['etat'] for b in rendu['bases']}
        self.assertEqual(etats['desk.sqlite3'], 'illisible')
        apres = instantane(self.etat)
        principaux = lambda d: {k: v for k, v in d.items() if not k.endswith(('-shm', '-wal'))}
        self.assertEqual(principaux(apres), principaux(avant))       # bases intactes ; SQLite gère seul ses fichiers -shm et -wal
        self.assertTrue(set(avant) <= set(apres))                     # rien n'est supprimé

    def test_R5_preparer_remise_sans_executer_ne_touche_rien(self):
        extrait = Path(tempfile.mkdtemp(dir=self.f.base))
        (extrait / 'etat').mkdir()
        shutil.copyfile(self.saine, extrait / 'etat' / 'desk.sqlite3')   # lot R : hors service (archive extraite fictive)
        avant = instantane(self.etat)
        plan = sc.preparer_remise(self.etat, 'desk.sqlite3', extrait)
        self.assertFalse(plan['execute'])
        self.assertIn('--executer', plan['a_faire'])
        self.assertEqual(instantane(self.etat), avant)

    def test_R5_remise_en_place_sans_ecrasement_ni_suppression(self):
        extrait = Path(tempfile.mkdtemp(dir=self.f.base))
        (extrait / 'etat').mkdir()
        shutil.copyfile(self.saine, extrait / 'etat' / 'desk.sqlite3')   # lot R : hors service (archive extraite fictive)
        abimee = empreinte_fichier(self.base)
        refus = sc.preparer_remise(self.etat, 'desk.sqlite3', extrait, services_arretes=False, executer=True)
        self.assertIn('refus', refus)
        self.assertEqual(empreinte_fichier(self.base), abimee)
        plan = sc.preparer_remise(self.etat, 'desk.sqlite3', extrait, services_arretes=True, executer=True)
        self.assertTrue(plan['execute'], plan)
        self.assertTrue(plan['controle_final']['integre'])
        self.assertEqual(empreinte_fichier(Path(plan['mis_de_cote']) / 'desk.sqlite3'), abimee)   # l'ancienne base est mise de côté, intacte
        d = Desk(self.f.c)
        d.db.close()

    def test_R5_remise_refusee_si_un_service_tient_son_verrou(self):
        from agent.portable import fcntl
        extrait = Path(tempfile.mkdtemp(dir=self.f.base))
        (extrait / 'etat').mkdir()
        shutil.copyfile(self.saine, extrait / 'etat' / 'desk.sqlite3')   # lot R : hors service (archive extraite fictive)
        with open(self.etat / 'desk-worker.lock', 'a') as verrou:
            fcntl.flock(verrou, fcntl.LOCK_EX | fcntl.LOCK_NB)
            if not sc.services_actifs(self.etat):
                self.skipTest('verrou de fichier non exclusif dans un même processus sur ce système')
            plan = sc.preparer_remise(self.etat, 'desk.sqlite3', extrait, services_arretes=True, executer=True)
        self.assertIn('refus', plan)
        self.assertFalse(plan['execute'])

    def test_R5_archive_extraite_abimee_refusee(self):
        extrait = Path(tempfile.mkdtemp(dir=self.f.base))
        (extrait / 'etat').mkdir()
        shutil.copyfile(self.base, extrait / 'etat' / 'desk.sqlite3')    # lot R : hors service (copie de la base abîmée du test)
        plan = sc.preparer_remise(self.etat, 'desk.sqlite3', extrait, services_arretes=True, executer=True)
        self.assertIn('intégrité', plan['refus'])


class HistoriqueSauvegardes(rev.Base):
    def test_R5_historique_des_sauvegardes_en_lecture_seule(self):
        self.phrase()
        self.sauver()
        self.desk.db.commit()
        out = sc.sauvegardes(self.desk.c['state_dir'])
        self.assertEqual(out['historique'][0]['statut'], 'reussie')
        self.assertTrue(out['reussite'])
        self.assertEqual(sc.archives(self.desk.c['state_dir'])['archives'][0]['nom'], out['historique'][0]['nom'])


# ------------------------------------------------------------------------------------------------ R6 : après une restauration (retour de production)
class Restauration(rev.Base):
    """Fait constaté sur un serveur de production en 5.6.108 : après la remise en place de desk.sqlite3 depuis l'archive de la nuit, une
    sauvegarde est partie seule une minute après le redémarrage, et la rétention a retiré l'archive saine du jour (celle de la restauration).
    La base d'une archive est copiée PENDANT le travail de sauvegarde : elle contient ce travail « en cours » et l'ancienne dernière réussite."""

    def _sauvegarde_par_la_file(self):
        self.actif(heure=2)
        sv.lancer(self.desk, 'admin')
        self.assertTrue(self.desk.work_once())
        row = self.desk.db.execute("SELECT nom,id FROM sauvegardes5688 WHERE statut='reussie' ORDER BY debut DESC LIMIT 1").fetchone()
        return row['nom'], row['id']

    def _extraire(self, nom):
        cible = Path(tempfile.mkdtemp(dir=self.tmp.name)) / 'extrait'
        sv.extraire(self.dest() / nom, cible, phrase=rev.PHRASE)
        return cible

    def _remettre_en_place(self, extrait):
        """Geste manuel du cabinet (sans l'outil) : services arrêtés, base de l'archive copiée à la place de la base en service."""
        etat = Path(self.desk.c['state_dir'])
        self.desk.db.close()
        cote = Path(tempfile.mkdtemp(dir=self.tmp.name))
        for suffixe in ('', '-wal', '-shm'):
            p = etat / ('desk.sqlite3' + suffixe)
            if p.exists():
                os.replace(p, cote / p.name)
        shutil.copyfile(extrait / 'etat' / 'desk.sqlite3', etat / 'desk.sqlite3')   # lot R : hors service (base extraite d'une archive fictive)
        desk_module._SCHEMA_READY.clear()
        self.desk = Desk(self.desk.c)
        self.addCleanup(self.desk.db.close)

    def test_R6_la_base_d_une_archive_contient_son_travail_en_cours(self):
        nom, ident = self._sauvegarde_par_la_file()
        db = sqlite3.connect(self._extraire(nom) / 'etat' / 'desk.sqlite3')
        try:
            self.assertEqual(db.execute('SELECT statut FROM sauvegardes5688 WHERE id=?', (ident,)).fetchone()[0], 'en_cours')
            self.assertEqual(db.execute("SELECT status FROM jobs WHERE kind='sauvegarde5688'").fetchone()[0], 'running')
        finally:
            db.close()

    def test_R6_aucune_sauvegarde_ne_part_seule_apres_une_restauration(self):
        nom, ident = self._sauvegarde_par_la_file()
        self._remettre_en_place(self._extraire(nom))
        from agent.queue567 import recover
        recover(self.desk, 'manual')                                    # reprise des travaux interrompus au démarrage du worker
        self.assertFalse(self.desk.db.execute("SELECT 1 FROM jobs WHERE kind='sauvegarde5688' AND status IN ('pending','running')").fetchone())
        self.assertFalse(sv.schedule(self.desk))                         # 5.6.108 : True, sauvegarde mise en file une minute après le démarrage
        self.assertFalse(self.desk.db.execute("SELECT 1 FROM jobs WHERE kind='sauvegarde5688' AND status='pending'").fetchone())
        row = self.desk.db.execute('SELECT statut,erreur FROM sauvegardes5688 WHERE id=?', (ident,)).fetchone()
        self.assertEqual((row['statut'], row['erreur']), ('interrompue', 'interrompue_au_demarrage'))
        garde = sv.etat(self.desk)['garde']
        jusqu = sv._parse(garde['jusqu_a'])
        local = jusqu.astimezone(sv._tz(self.desk))
        self.assertEqual((local.hour, local.minute), (2, 0))             # heure planifiée suivante
        self.assertGreater(jusqu, datetime.now(timezone.utc) + timedelta(hours=1))
        self.assertFalse(sv.due(self.desk, jusqu - timedelta(minutes=1)))
        self.assertTrue(sv.due(self.desk, jusqu + timedelta(minutes=1)))
        html = rv.sauvegarde_html(self.desk)
        self.assertIn('Prochaine sauvegarde automatique', html)

    def test_R6_travail_automatique_reste_en_file_differe_pas_execute(self):
        nom, _ = self._sauvegarde_par_la_file()
        self._remettre_en_place(self._extraire(nom))
        out = sv.executer(self.desk, 'automatique')
        self.assertEqual(out['statut'], 'differee')
        self.assertEqual(len([p for p in self.dest().iterdir() if sv.NOM.match(p.name)]), 1)

    def test_R6_retention_ne_retire_jamais_l_archive_de_la_restauration(self):
        nom, _ = self._sauvegarde_par_la_file()
        self._remettre_en_place(self._extraire(nom))
        sv.schedule(self.desk)                                           # reconnaissance de la restauration (archive d'origine protégée)
        self.assertEqual(sv.executer(self.desk, 'manuel')['statut'], 'reussie')   # même jour : a_conserver ne garde que la plus récente
        self.assertTrue((self.dest() / nom).is_file())

    def test_R6_retention_seulement_apres_relecture_reussie(self):
        nom, _ = self._sauvegarde_par_la_file()
        with patch.object(sv, 'controler', side_effect=Stop('archive_alteree')):
            with self.assertRaises(Stop):
                sv.executer(self.desk, 'manuel')
        self.assertTrue((self.dest() / nom).is_file())

    def test_R6_outil_de_secours_neutralise_le_travail_en_cours_avant_mise_en_place(self):
        nom, ident = self._sauvegarde_par_la_file()
        extrait = self._extraire(nom)
        avant = empreinte_fichier(extrait / 'etat' / 'desk.sqlite3')
        self.desk.db.close()
        plan = sc.preparer_remise(self.desk.c['state_dir'], 'desk.sqlite3', extrait, services_arretes=True, executer=True)
        self.assertTrue(plan['execute'], plan)
        self.assertEqual(empreinte_fichier(extrait / 'etat' / 'desk.sqlite3'), avant)   # l'archive extraite n'est pas modifiée
        db = sqlite3.connect(Path(self.desk.c['state_dir']) / 'desk.sqlite3')
        try:
            self.assertFalse(db.execute("SELECT 1 FROM jobs WHERE kind='sauvegarde5688' AND status IN ('pending','running','cancel_requested','suspended')").fetchone())
            self.assertFalse(db.execute("SELECT 1 FROM sauvegardes5688 WHERE statut='en_cours'").fetchone())
            garde = json.loads(db.execute('SELECT value FROM settings WHERE key=?', (sv.GARDE,)).fetchone()[0])
            self.assertEqual(garde['motif'], 'base_restauree')
            self.assertIn(ident, json.loads(db.execute('SELECT value FROM settings WHERE key=?', (sv.PROTEGEES,)).fetchone()[0]))
        finally:
            db.close()
        desk_module._SCHEMA_READY.clear()
        self.desk = Desk(self.desk.c)
        self.addCleanup(self.desk.db.close)
        self.assertFalse(sv.schedule(self.desk))


if __name__ == '__main__':
    unittest.main()
