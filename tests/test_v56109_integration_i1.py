"""5.6.109 (intégration I1 : sûreté, exploitation, cohérence entre lots).

I1-02 mise à jour : plus aucune ouverture directe d'une base vivante par ``upgrade.py`` (reconnaissance par ``sqlite3`` en lecture seule) ; garde
statique étendue aux scripts d'installation et de la racine. I1-03 unités systemd : redémarrage sans boucle (code 78), limites mémoire.
I1-04 veille de sécurité du serveur. I1-05 refus de confidentialité définitifs. I1-06 formalités (contrôle humain public, fiche de connexion).
I1-07 simulation : l'export ne sort jamais le texte brut non masqué. Données fictives, aucun appel réseau."""
import ast
import os
from pathlib import Path
import sqlite3
import tempfile
import unittest

import garde_sqlite56109 as garde

ROOT = Path(__file__).resolve().parents[1]


class MiseAJour(unittest.TestCase):
    def test_I1_02_upgrade_ne_lit_plus_l_entete_par_open(self):
        trouvees = garde.analyser(ROOT, [ROOT / 'upgrade.py'])
        self.assertEqual(trouvees, [], '\n'.join(trouvees))

    def test_I1_02_garde_etendue_aux_scripts_d_installation_et_de_la_racine(self):
        fichiers = {p.relative_to(ROOT).as_posix() for p in garde.fichiers_du_produit(ROOT)}
        for nom in ('upgrade.py', 'installer.py', 'miseajour.py', 'manage.py', 'updater.py', 'standalone.py', 'recette.py'):
            self.assertTrue(nom in fichiers, nom)
        trouvees = garde.analyser(ROOT, garde.scripts_de_la_racine(ROOT))   # agent/ : test_v56109_secours.Garde (même liste étendue)
        self.assertEqual(trouvees, [], '\n'.join(trouvees))

    def test_I1_02_reconnaissance_des_bases_sans_descripteur_python(self):
        import upgrade
        with tempfile.TemporaryDirectory() as d:
            d = Path(d)
            vraie = d / 'desk.sqlite3'
            db = sqlite3.connect(vraie)
            db.execute('PRAGMA journal_mode=WAL')
            db.execute('CREATE TABLE t(x)')
            db.execute("INSERT INTO t VALUES('DURAND')")
            db.commit()
            fausse = d / 'documents.sqlite3'
            fausse.write_bytes(b'index intact')
            vide = d / 'vide.sqlite3'
            vide.write_bytes(b'')
            try:
                self.assertTrue(upgrade.est_base_sqlite(vraie))
                self.assertFalse(upgrade.est_base_sqlite(fausse))
                self.assertFalse(upgrade.est_base_sqlite(vide))
                self.assertFalse(upgrade.est_base_sqlite(d / 'absente.sqlite3'))
                self.assertEqual(fausse.read_bytes(), b'index intact')   # lot R : hors service (fichier fictif, jamais une base)
                self.assertEqual(vide.stat().st_size, 0)
                self.assertFalse((d / 'absente.sqlite3').exists())
                self.assertEqual(db.execute('SELECT x FROM t').fetchone()[0], 'DURAND')
            finally:
                db.close()

    def test_I1_02_contre_exemple_l_ancienne_lecture_est_vue_par_la_garde(self):
        ancien = ("def f(state):\n    for path in state.glob('*.sqlite3'):\n"
                  "        with path.open('rb') as handle:valid=handle.read(16)==b'SQLite format 3\\x00'\n")
        self.assertGreaterEqual(len(garde.ouvertures_directes(ancien)), 1)


def _unite(nom):
    """Sections d'une unité systemd : {section: {clé: [valeurs]}}."""
    sections, courante = {}, None
    for ligne in (ROOT / 'deploy' / nom).read_text(encoding='utf-8').splitlines():
        ligne = ligne.strip()
        if not ligne or ligne.startswith('#'):
            continue
        if ligne.startswith('['):
            courante = sections.setdefault(ligne.strip('[]'), {})
        elif '=' in ligne:
            cle, valeur = ligne.split('=', 1)
            courante.setdefault(cle, []).append(valeur)
    return sections


class Unites(unittest.TestCase):
    SERVICES = ('axiorhub-mail-desk-worker.service', 'axiorhub-mail-desk-worker@.service', 'axiorhub-mail-watch.service', 'axiorhub-mail-ui.service')

    def test_I1_03_redemarrage_sans_boucle_et_code_78(self):
        from agent import secours56109
        for nom in self.SERVICES:
            u = _unite(nom)
            self.assertEqual(u['Unit'].get('StartLimitIntervalSec'), ['600'], nom)
            self.assertEqual(u['Unit'].get('StartLimitBurst'), ['5'], nom)
            self.assertNotIn('StartLimitIntervalSec', u['Service'], nom)   # ignoré par systemd hors de [Unit]
            self.assertEqual(u['Service'].get('RestartSec'), ['15'], nom)
            self.assertEqual(u['Service'].get('RestartPreventExitStatus'), [str(secours56109.CODE_SORTIE)], nom)
            self.assertEqual(u['Service'].get('Restart'), ['on-failure'], nom)
            self.assertIn('/web.py ', u['Service']['ExecStart'][0], nom)   # web.py : arret_service sort en 78 sur base illisible
        self.assertIn('arret_service', (ROOT / 'web.py').read_text(encoding='utf-8'))

    def test_I1_03_limites_memoire(self):
        for nom in ('axiorhub-mail-desk-worker.service', 'axiorhub-mail-desk-worker@.service'):
            s = _unite(nom)['Service']
            self.assertEqual((s.get('MemoryHigh'), s.get('MemoryMax')), (['1536M'], ['2G']), nom)
        s = _unite('axiorhub-mail-ui.service')['Service']
        self.assertEqual((s.get('MemoryHigh'), s.get('MemoryMax'), s.get('MemorySwapMax')), (['450M'], ['768M'], ['256M']))
        self.assertEqual(_unite('axiorhub-mail-watch.service')['Service'].get('MemoryMax'), ['512M'])

    def test_I1_03_unites_gerees_identiques_a_l_installateur(self):
        import upgrade
        source = (ROOT / 'installer.py').read_text(encoding='utf-8')
        arbre = ast.parse(source)
        core = next(ast.literal_eval(n.value) for n in arbre.body if isinstance(n, ast.Assign) and getattr(n.targets[0], 'id', '') == 'CORE_UNITS')
        self.assertEqual(set(core), set(upgrade.MANAGED_UNITS))
        for nom in upgrade.MANAGED_UNITS:
            self.assertTrue((ROOT / 'deploy' / nom).is_file(), nom)
            self.assertNotIn(b'\r', (ROOT / 'deploy' / nom).read_bytes(), nom)

    def test_I1_03_mise_a_jour_reecrit_les_unites_presentes_avec_copie(self):
        import upgrade
        with tempfile.TemporaryDirectory() as d:
            d = Path(d)
            release, systemd, sauvegardes = d / 'release', d / 'systemd', d / 'backups'
            (release / 'deploy').mkdir(parents=True)
            systemd.mkdir()
            sauvegardes.mkdir()
            for nom in ('axiorhub-mail-ui.service', 'axiorhub-mail-watch.service', 'axiorhub-mail-desk-worker.service'):
                (release / 'deploy' / nom).write_bytes((ROOT / 'deploy' / nom).read_bytes())
            ancienne = b'[Unit]\nDescription=ancienne\n\n[Service]\nRestart=on-failure\nRestartSec=5\nMemoryMax=512M\n'
            (systemd / 'axiorhub-mail-ui.service').write_bytes(ancienne)
            (systemd / 'axiorhub-mail-watch.service').write_bytes((ROOT / 'deploy' / 'axiorhub-mail-watch.service').read_bytes())
            faits = upgrade.refresh_units(release, systemd, sauvegardes)
            self.assertEqual([f['unit'] for f in faits], ['axiorhub-mail-ui.service'])   # identique : intacte ; absente : jamais ajoutée
            self.assertEqual((systemd / 'axiorhub-mail-ui.service').read_bytes(), (ROOT / 'deploy' / 'axiorhub-mail-ui.service').read_bytes())
            self.assertEqual(Path(faits[0]['backup']).read_bytes(), ancienne)
            self.assertFalse((systemd / 'axiorhub-mail-desk-worker.service').exists())
            self.assertEqual(upgrade.refresh_units(release, systemd, sauvegardes), [])   # idempotent

    @unittest.skipUnless(hasattr(os, 'geteuid') and os.geteuid() == 0, 'Installation root simulée (Linux)')
    def test_I1_03_upgrade_complet_reecrit_les_unites_et_le_note_dans_le_recu(self):
        import hashlib
        import json
        import test_upgrade
        import upgrade
        cas = test_upgrade.UpgradeFilesystemTests('test_round_trip_preserves_state_and_permissions')
        cas.setUp()
        try:
            (cas.source / 'deploy').mkdir()
            texte = (ROOT / 'deploy' / 'axiorhub-mail-ui.service').read_text(encoding='utf-8')
            (cas.source / 'deploy' / 'axiorhub-mail-ui.service').write_text(texte, encoding='utf-8')
            manifeste = (cas.source / 'MANIFEST.sha256').read_text()
            (cas.source / 'MANIFEST.sha256').write_text(manifeste + hashlib.sha256(texte.encode()).hexdigest() + '  deploy/axiorhub-mail-ui.service\n')
            systemd = Path(cas.tmp.name) / 'systemd'
            systemd.mkdir()
            (systemd / 'axiorhub-mail-ui.service').write_text('[Service]\nMemoryMax=512M\n')
            upgrade.upgrade(cas.source, cas.base, cas.config, cas.state, systemd=systemd)
            self.assertIn('MemoryMax=768M', (systemd / 'axiorhub-mail-ui.service').read_text())
            recu = json.loads(upgrade.receipt_path(cas.base).read_text())
            self.assertEqual([u['unit'] for u in recu['unit_backups']], ['axiorhub-mail-ui.service'])
        finally:
            cas.doCleanups()


class GardeStatique(unittest.TestCase):
    """F (relecture indépendante) : formes que la garde statique ne voyait pas, et formes sûres qu'elle ne doit pas signaler."""
    VUES = {
        'code au niveau du module': "from pathlib import Path\nBASE = Path('/var/lib/x') / 'desk.sqlite3'\ndonnees = BASE.read_bytes()\n",
        'bloc if __name__': "import sys\nif __name__ == '__main__':\n    with open(sys.argv[1] + '/desk.sqlite3', 'rb') as f:\n        f.read()\n",
        'chemin tenu dans un attribut': ("from pathlib import Path\nclass C:\n    def __init__(self, d):\n        self.base = Path(d) / 'desk.sqlite3'\n"
                                         "    def lire(self):\n        return open(self.base, 'rb').read()\n"),
        'paramètre d’une fonction utilitaire': ("def lire(chemin):\n    with open(chemin, 'rb') as f:\n        return f.read()\n"
                                                "def sauver(etat):\n    return lire(etat / 'documents.sqlite3')\n"),
        'filtre par suffixe': ("from pathlib import Path\ndef f(d):\n    for p in Path(d).iterdir():\n        if p.suffix == '.sqlite3':\n"
                               "            data = p.read_bytes()\n"),
        'zipfile.write': "import zipfile\ndef f(etat, z):\n    z.write(etat / 'desk.sqlite3')\n",
        'tarfile.add': "import tarfile\ndef f(etat, tar):\n    tar.add(str(etat) + '/desk.sqlite3-wal')\n",
        'subprocess cp': "import subprocess\ndef f(etat):\n    subprocess.run(['cp', str(etat / 'desk.sqlite3'), '/tmp/x'])\n",
        'io.FileIO': "import io\ndef f(etat):\n    return io.FileIO(etat / 'search490.sqlite3').read()\n",
        'chemin venu de la configuration': ("from pathlib import Path\ndef f(c):\n    for p in Path(c['state_dir']).iterdir():\n"
                                            "        with open(p, 'rb') as x:\n            x.read()\n"),
    }
    SURES = {
        'sqlite3 en lecture seule': "import sqlite3\ndef f(etat):\n    return sqlite3.connect('file:' + str(etat / 'desk.sqlite3') + '?mode=ro', uri=True)\n",
        'parcours qui écarte les bases': ("from pathlib import Path\nSUFFIXES = ('.sqlite3', '-wal', '-shm')\ndef f(c):\n"
                                          "    for p in Path(c['state_dir']).iterdir():\n        if p.name.endswith(SUFFIXES):\n            continue\n"
                                          "        p.read_bytes()\n"),
        'parcours restreint aux .json': "from pathlib import Path\ndef f(c):\n    for p in Path(c['state_dir']).glob('*.json'):\n        p.read_text()\n",
        'nom réutilisé après la boucle': ("def f(etat, autre):\n    for path in etat.glob('*.sqlite3'):\n        pass\n"
                                          "    for path in autre.glob('*.txt'):\n        path.read_text()\n"),
        'contenu lu puis écrit': "def f(src, dst):\n    data = src.read_bytes()\n    dst.write_bytes(data)\n",
    }

    def test_I1_F_la_garde_voit_les_formes_signalees(self):
        for nom, source in self.VUES.items():
            self.assertTrue(garde.ouvertures_directes(source), nom)

    def test_I1_F_la_garde_laisse_passer_les_formes_sures(self):
        for nom, source in self.SURES.items():
            self.assertEqual(garde.ouvertures_directes(source), [], nom)

    def test_I1_F_exemptions_explicites(self):
        source = "def f(etat):\n    return open(etat / 'desk.sqlite3', 'rb')   # lot R : hors service (copie temporaire)\n"
        self.assertEqual(garde.ouvertures_directes(source), [])
        source = "def f(etat):\n    return open(etat / 'desk.sqlite3', 'rb')   # garde56109 : base exclue\n"
        self.assertEqual(garde.ouvertures_directes(source), [])

    def test_I1_F_garde_parcourt_deploy_et_scripts_et_aucune_ouverture(self):
        fichiers = {p.relative_to(ROOT).as_posix() for p in garde.fichiers_du_produit(ROOT)}
        self.assertTrue(any(f.startswith('deploy/') for f in fichiers))
        self.assertTrue(any(f.startswith('scripts/') for f in fichiers))
        autres = [ROOT / f for f in sorted(fichiers) if f.startswith(('deploy/', 'scripts/'))]
        trouvees = garde.analyser(ROOT, autres)
        self.assertEqual(trouvees, [], '\n'.join(trouvees))


class RefusDefinitifs(unittest.TestCase):
    CODES = ('pseudo_marqueur_inconnu', 'pseudo_marqueur_altere', 'registre_identites_illisible')

    def test_I1_05_refus_de_confidentialite_definitifs(self):
        from agent import transmission56109, travaux5689
        for code in self.CODES + tuple(sorted(transmission56109.CODES)):
            self.assertTrue(travaux5689.definitif(code), code)
        self.assertFalse(travaux5689.definitif('delai_depasse'))   # une panne passagère reste relançable

    def test_I1_05_un_automatisme_ne_retente_pas_un_refus(self):
        import json
        from datetime import datetime, timezone
        from agent import travaux5689
        from agent.common import Stop

        class Bureau:
            db = sqlite3.connect(':memory:')
        Bureau.db.row_factory = sqlite3.Row
        Bureau.db.execute('CREATE TABLE jobs(id INTEGER PRIMARY KEY, kind TEXT, args TEXT, status TEXT, finished TEXT, result TEXT)')
        fini = datetime.now(timezone.utc).isoformat()
        for i, code in enumerate(self.CODES + ('pseudo_identite_residuelle',)):
            args = {'dossier': 'DOS-001', 'n': i}
            Bureau.db.execute('INSERT INTO jobs(kind,args,status,finished,result) VALUES(?,?,?,?,?)',
                              ('analyse', json.dumps(args, sort_keys=True), 'error', fini, json.dumps({'erreur': code})))
            with self.assertRaises(Stop):
                travaux5689.check_relance(Bureau, 'analyse', args, 'automatic')
            travaux5689.check_relance(Bureau, 'analyse', args, 'manual')   # l'utilisateur garde la main


import test_v56109_formalites as TF   # noqa: E402  (socle du lot G3 : opération Corporate, formalité INPI, faux documents)


class FormalitesADecider(TF.Socle):
    def _demande(self):
        op = self.op_siege()
        fid = TF.F.preparer(self.desk, TF.AVOCAT, {'nature': 'inpi:modification', 'op_kind': 'corporate5636', 'op_id': op})['formalite']
        TF.G14Inpi.pret(self, fid)
        eid = TF.F.demander(self.desk, TF.AVOCAT, {'formalite': fid, 'action': 'signer_inpi'})['effet']
        return fid, eid, TF.F.fiche_effet(self.desk, TF.AVOCAT, eid)

    def test_I1_06_exiger_humain_public_et_alias(self):
        from agent import bland56109 as B
        from agent.common import Stop
        with self.assertRaisesRegex(Stop, 'confirmation_humaine_requise'):
            B.exiger_humain()
        with self.assertRaisesRegex(Stop, 'confirmation_humaine_requise'):
            B._exiger_humain()
        with B.requete_humaine(TF.AVOCAT[0]):
            B.exiger_humain()
            B._exiger_humain()
        self.assertNotIn('_exiger_humain', (ROOT / 'agent' / 'formalites56109.py').read_text(encoding='utf-8'))   # plus d'usage privé hors du lot B

    def test_I1_06_mutation_du_controle_public_atteint_les_formalites(self):
        from unittest.mock import patch
        from agent import bland56109 as B
        fid, eid, fe = self._demande()
        with patch.object(B, 'exiger_humain', lambda: None):     # garde publique retirée : l'accord passe hors de l'interface
            TF.F.autoriser(self.desk, TF.AVOCAT, {'effet': eid, 'version': fe['version'], 'empreinte': fe['empreinte'], 'relu': True})
        self.assertEqual(self.fiche(fid)['effets'][0]['etat'], 'autorise')

    def test_I1_06_a_decider_affiche_la_fiche_des_formalites_et_non_le_bouton_generique(self):
        from agent import connexions5652 as cx
        from agent.common import Stop
        fid, eid, fe = self._demande()
        html = cx.sheet_html(self.desk, '/p', TF.AVOCAT[0], TF.AVOCAT[1], fe['operation'])
        self.assertNotIn('m5652/operation/valider', html)          # le bouton générique refusait ces opérations
        self.assertNotIn('Valider et transmettre', html)
        self.assertIn('m56109/operations/formalites/autoriser', html)
        self.assertIn('value="%s"' % fe['empreinte'], html)
        self.assertIn('name="relu"', html)
        self.assertIn('m56109/operations/formalites/annuler', html)
        # assistant : pas d'autorisation (réservée aux valideurs), annulation possible
        html = cx.sheet_html(self.desk, '/p', TF.ASSISTANT[0], TF.ASSISTANT[1], fe['operation'])
        self.assertNotIn('formalites/autoriser', html)
        self.assertIn('Autorisation réservée', html)
        # dossier hors de portée : le même 404 neutre, avant tout contenu
        with patch_peut_lire(False), self.assertRaisesRegex(Stop, 'introuvable'):
            cx.sheet_html(self.desk, '/p', TF.AVOCAT[0], TF.AVOCAT[1], fe['operation'])
        # une fois autorisé : plus de formulaire d'accord
        self.autoriser(eid)
        html = cx.sheet_html(self.desk, '/p', TF.AVOCAT[0], TF.AVOCAT[1], fe['operation'])
        self.assertNotIn('formalites/autoriser', html)

    def test_I1_06_contre_exemple_sans_delegation_le_generique_revient(self):
        from unittest.mock import patch
        from agent import connexions5652 as cx
        fid, eid, fe = self._demande()
        origine = cx._row_op_provider
        with patch.object(cx, '_row_op_provider', lambda d, o: 'autre_' + origine(d, o)):
            try:
                html = cx.sheet_html(self.desk, '/p', TF.AVOCAT[0], TF.AVOCAT[1], fe['operation'])
            except Exception:   # noqa: BLE001 — la fiche générique peut aussi refuser : dans les deux cas, pas la fiche des formalités
                html = ''
        self.assertNotIn('formalites/autoriser', html)


class PlafondRappels(unittest.TestCase):
    def test_I1_06_plafond_quotidien_compte_au_jour_du_cabinet_et_non_au_jour_utc(self):
        """Constaté pendant l'intégration (test_v56109_bland.B10 en échec entre minuit et 1 h) : un envoi automatique fait à 0 h 20 à Paris
        (23 h 20 UTC la veille) n'était pas compté dans le plafond du jour."""
        from datetime import date
        from unittest.mock import patch
        from zoneinfo import ZoneInfo
        from agent import bland56109 as B

        class Bureau:
            db = sqlite3.connect(':memory:')
        Bureau.db.execute('CREATE TABLE bland56109_rappels(cle TEXT, mode TEXT, etat TEXT, envoye_le TEXT)')
        Bureau.db.execute("INSERT INTO bland56109_rappels VALUES('r1','auto','envoye','2026-11-23T23:20:00+00:00')")   # 24/11 0 h 20 à Paris
        Bureau.db.execute("INSERT INTO bland56109_rappels VALUES('r0','auto','envoye','2026-11-23T22:50:00+00:00')")   # 23/11 23 h 50 à Paris
        with patch.object(B, '_jour_cabinet', lambda d: date(2026, 11, 24)), patch.object(B, '_tz', lambda d: ZoneInfo('Europe/Paris')):
            self.assertEqual(B._envois_auto_du_jour(Bureau), 1)


def patch_peut_lire(valeur):
    from unittest.mock import patch
    return patch('agent.acces5697.peut_lire_dossier', lambda *a, **k: valeur)


import test_v56109_simulation as TS   # noqa: E402  (socle du lot N : dossier contentieux fictif, faux modèle, faux connecteur)


class SimulationExport(TS.Simulation):
    def _bloquee(self):
        self.modele.chiffre = True                               # le faux modèle écrit « Succès probable à 72 %. »
        self.cadrer()
        _, recu = self.simuler()
        self.travailler()
        return recu['run_id']

    def test_I1_07_l_export_ne_sort_jamais_le_pourcentage_brut(self):
        import json
        import zipfile
        from agent import export5688 as X
        rid = self._bloquee()
        row = self.desk.db.execute('SELECT state, result_json FROM simulation_runs56109 WHERE id=?', (rid,)).fetchone()
        self.assertEqual(row['state'], 'blocked')
        self.assertNotIn('72 %', row['result_json'])              # masqué à l'écriture de la colonne exportée
        self.assertIn(TS.S.MASQUE, row['result_json'])
        brut = self.desk.db.execute('SELECT matter_id, result_brut_json FROM simulation_bruts56109 WHERE run_id=?', (rid,)).fetchone()
        self.assertEqual(brut['matter_id'], self.did)
        self.assertIn('72 %', brut['result_brut_json'])           # le brut est gardé, hors export
        from agent import export56108
        self.assertTrue(X.motif_exclusion('simulation_bruts56109'))
        self.assertNotIn('simulation_bruts56109', {x[2] for x in export56108.EXPORT})
        with tempfile.TemporaryDirectory() as d:
            dest = Path(d) / 'export.zip'
            meta, _ = X.construire(self.desk, dest, {'documents': False})
            with zipfile.ZipFile(dest) as z:
                # fichiers de la simulation (simulations, cadrages, corrections) ; le projet d'avis bloqué du registre des consultations
                # (``recherche_juridique/consultations``, module opinions, antérieur) garde son propre texte : hors du périmètre de ce correctif
                noms = [n for n in z.namelist() if 'simulation' in n]
                tout = b'\n'.join(z.read(n) for n in noms)
            self.assertTrue(noms)
            self.assertTrue(X.verifier_manifeste(dest)['ok'])
        self.assertIn(rid.encode(), tout)                          # la simulation est bien exportée…
        self.assertNotIn('72 %'.encode(), tout)                    # … sans le chiffre inventé
        self.assertNotIn('72 %'.encode(), tout)
        self.assertNotIn('simulation_bruts56109', json.dumps(meta, ensure_ascii=False, default=str))

    def test_I1_07_projet_d_avis_bloque_des_consultations_exporte_masque(self):
        """Décision de l'intégrateur : le projet d'avis bloqué (registre des consultations, module opinions) ne sort pas non plus le
        pourcentage inventé ; le brut reste en base (``legal_opinion_bruts56109``, exclue avec motif) et ``opinions.preview`` est inchangé."""
        import json
        import zipfile
        from agent import export5688 as X, export56108, opinions
        rid = self._bloquee()
        pid = self.desk.db.execute('SELECT opinion_project_id FROM simulation_runs56109 WHERE id=?', (rid,)).fetchone()[0]
        row = self.desk.db.execute('SELECT status, data FROM legal_opinion_projects_v260 WHERE id=?', (pid,)).fetchone()
        self.assertEqual(row['status'], 'blocked')                   # blocage inchangé
        self.assertNotIn('72 %', row['data'])                         # colonne exportée : masquée à l'écriture
        brut = self.desk.db.execute('SELECT matter, data_brut_json FROM legal_opinion_bruts56109 WHERE project_id=?', (pid,)).fetchone()
        self.assertEqual(brut['matter'], self.did)
        self.assertIn('72 %', brut['data_brut_json'])
        self.assertIn('72 %', json.dumps(opinions.preview(self.desk, pid), ensure_ascii=False))   # lecture du module : comme avant
        self.assertTrue(X.motif_exclusion('legal_opinion_bruts56109'))
        self.assertNotIn('legal_opinion_bruts56109', {x[2] for x in export56108.EXPORT})
        with tempfile.TemporaryDirectory() as d:
            dest = Path(d) / 'export.zip'
            X.construire(self.desk, dest, {'documents': False})
            with zipfile.ZipFile(dest) as z:
                noms = [n for n in z.namelist() if 'consultations' in n]
                tout = b'\n'.join(z.read(n) for n in noms)
            self.assertTrue(X.verifier_manifeste(dest)['ok'])
        self.assertIn(pid.encode(), tout)
        self.assertNotIn('72 %'.encode(), tout)
        self.assertIn(TS.S.MASQUE.encode(), tout)
        self.assertEqual(opinions.MASQUE_PROBABILITE, TS.S.MASQUE)    # un seul libellé de masquage
        # mutation : sans le masquage à l'écriture, le chiffre inventé partirait dans l'export
        from unittest.mock import patch
        with patch.object(opinions, 'masquer_probabilites', lambda valeur: valeur):
            rid2 = self._bloquee()
        pid2 = self.desk.db.execute('SELECT opinion_project_id FROM simulation_runs56109 WHERE id=?', (rid2,)).fetchone()[0]
        self.assertIn('72 %', self.desk.db.execute('SELECT data FROM legal_opinion_projects_v260 WHERE id=?', (pid2,)).fetchone()[0])

    def test_I1_07_affichage_et_lecture_inchanges(self):
        rid = self._bloquee()
        r = self.lire(rid)
        self.assertEqual(r['state'], 'blocked')
        self.assertNotIn('72 %', TS.json.dumps(r, ensure_ascii=False))
        self.assertNotIn('72 %', self.html())

    def test_I1_07_sans_chiffre_rien_n_est_garde_en_double(self):
        self.cadrer()
        _, recu = self.simuler()
        self.travailler()
        self.assertIsNone(self.desk.db.execute('SELECT 1 FROM simulation_bruts56109 WHERE run_id=?', (recu['run_id'],)).fetchone())

    def test_I1_07_mutation_sans_masquage_a_l_ecriture_le_brut_partirait(self):
        from unittest.mock import patch
        with patch.object(TS.S, '_sans_pourcentage', lambda valeur: valeur):
            rid = self._bloquee()
        self.assertIn('72 %', self.desk.db.execute('SELECT result_json FROM simulation_runs56109 WHERE id=?', (rid,)).fetchone()[0])


import test_v56109_lecture as TL   # noqa: E402  (cabinet d'essai du lot L : comptes, faux IMAP, stockage local temporaire)


class PiecesGarde(TL.Base):
    """A1 (relecture indépendante) : la relecture des droits avant chaque dépôt ne doit jamais être sautée."""

    def _lot(self, pieces):
        return self.json(self.b_post('classer/preparer', self.demande(self.uid_p, pieces)))

    def _retirer(self):
        TL.membres5650.set_reserve(self.b.f.c['state_dir'], TL.VISIBLE, ['titulaire@example.test'])

    def test_I1_A1_travail_pris_entre_la_mise_en_file_et_l_etat_du_lot(self):
        from unittest.mock import patch
        from agent import correspondances5643 as cx
        pieces = self.pieces(self.uid_p, self.b_get)
        lot = self._lot([pieces[0], pieces[1]])
        vrai = cx.confirm
        arrets = []

        def confirm_puis_worker(desk, owner, data):
            out = vrai(desk, owner, data)
            self._retirer()                      # droits retirés, puis le worker prend le travail aussitôt
            arrets.extend(self.travaux())
            return out
        with patch.object(cx, 'confirm', confirm_puis_worker):
            self.b_post('classer/confirmer', {'id': lot['id']})
        self.assertEqual(arrets, ['autorisation_retiree'])
        self.assertEqual(self.fichiers(), [])

    def test_I1_A1_arret_du_processus_entre_les_deux_validations(self):
        from unittest.mock import patch
        from agent import correspondances5643 as cx
        pieces = self.pieces(self.uid_p, self.b_get)
        lot = self._lot([pieces[0]])
        vrai = cx.confirm

        def confirm_puis_arret(desk, owner, data):
            vrai(desk, owner, data)
            raise RuntimeError('processus arrêté')
        with patch.object(cx, 'confirm', confirm_puis_arret):
            try:
                self.b_post('classer/confirmer', {'id': lot['id']})
            except RuntimeError:
                pass
        etat = self.desk.db.execute('SELECT etat FROM pieces56109_lots WHERE id=?', (lot['id'],)).fetchone()[0]
        en_file = self.nb_travaux()
        self.assertTrue(etat == 'confirme' or en_file == 0, (etat, en_file))   # jamais un travail en file pour un lot non confirmé
        self._retirer()
        self.travaux()
        self.assertEqual(self.fichiers(), [])

    def test_I1_A1_lot_attendu_absent_ou_non_confirme_refuse(self):
        from agent import pieces56109 as P
        from agent.common import Stop
        pieces = self.pieces(self.uid_p, self.b_get)
        lot = self._lot([pieces[0]])
        row = self.desk.db.execute('SELECT * FROM pieces56109_lots WHERE id=?', (lot['id'],)).fetchone()
        cle = TL.json.loads(row['pieces'])[0]['cle']
        with self.assertRaisesRegex(Stop, 'autorisation_retiree'):   # lot encore « propose » : rien à contrôler n'est jamais « autorisé »
            P.garde_effet(self.desk, {'id': row['link_id']}, {'key': cle})
        P.garde_effet(self.desk, {'id': row['link_id']}, {'key': 'autre-piece-sans-lot'})   # pièce classée sans le lecteur : ses propres gardes

    def test_I1_A1_droits_relus_avec_le_role_actuel_du_compte(self):
        from agent import connexion5648
        pieces = self.pieces(self.uid_p, self.b_get)
        lot = self._lot([pieces[0]])
        self.json(self.b_post('classer/confirmer', {'id': lot['id']}))
        db = connexion5648.connect(self.b.f.c['state_dir'])
        try:
            db.execute("UPDATE users SET role='assistant' WHERE ident='avocate@example.test'")   # rôle retiré entre la demande et le travail
            db.commit()
        finally:
            db.close()
        self.assertEqual(self.travaux(), ['autorisation_retiree'])
        self.assertEqual(self.fichiers(), [])


class PanneDroitsLecteur(TL.Base):
    """A2 : une panne du contrôle des droits (registre illisible) donne 503 sur ``destinations`` et ``classer/*``, jamais 404 ni accès."""

    def test_I1_A2_panne_du_registre_503_sur_destinations_et_classer(self):
        from unittest.mock import patch
        from agent import membres5650
        pieces = self.pieces(self.uid_p, self.b_get)
        lot = self.json(self.b_post('classer/preparer', self.demande(self.uid_p, [pieces[0]])))

        from agent import acces5697

        def panne(*a, **k):                     # contrôle du dossier en panne (registre illisible), après l'authentification de la requête
            raise Stop('controle_indisponible')
        from agent.common import Stop
        with patch.object(acces5697, 'masques', panne):
            dest = self.b_get('destinations', affaire=TL.VISIBLE)
            prep = self.b_post('classer/preparer', self.demande(self.uid_p, [pieces[1]]))
            conf = self.b_post('classer/confirmer', {'id': lot['id']})
        for nom, r in (('destinations', dest), ('classer/preparer', prep), ('classer/confirmer', conf)):
            self.assertEqual(r['status'], '503 Service Unavailable', (nom, r['body']))
        self.assertEqual((self.nb_travaux(), self.fichiers()), (0, []))
        # sans panne : un dossier interdit reste un 404 neutre (aucun changement pour les autres appelants)
        self.assertTrue(self.b_get('destinations', affaire=TL.RESERVE)['status'].startswith('404'))
        with patch.object(acces5697, 'masques', panne):
            self.assertFalse(acces5697.peut_lire_dossier(self.desk, ('avocate@example.test', 'avocat'), TL.VISIBLE))   # inchangé : refus


import test_v56103_fichiers as T3   # noqa: E402  (source locale du poste : dossier choisi fictif)


class _EspionBases:
    """Relève toute ouverture par descripteur d'un fichier de base SQLite ou de ses journaux (builtins.open, io.open, os.open)."""
    SUFFIXES = ('.sqlite3', '.sqlite', '.db', '-wal', '-shm', '-journal')

    def __init__(self):
        self.vus = []

    def __enter__(self):
        import builtins
        import io
        from unittest.mock import patch
        vrai_open, vrai_os_open = builtins.open, os.open

        def garde(f):
            def ouvrir(chemin, *a, **k):
                if isinstance(chemin, (str, bytes, os.PathLike)) and os.fsdecode(chemin).lower().endswith(self.SUFFIXES):
                    self.vus.append(os.fsdecode(chemin))
                return f(chemin, *a, **k)
            return ouvrir
        self._patches = [patch.object(builtins, 'open', garde(vrai_open)), patch.object(io, 'open', garde(vrai_open)),
                         patch.object(os, 'open', garde(vrai_os_open))]
        for p in self._patches:
            p.start()
        return self

    def __exit__(self, *exc):
        for p in reversed(self._patches):
            p.stop()


class SourceLocaleBases(T3.Base):
    """Relecture : ``fichiers56103`` (scrutation, empreinte, lecture, copie d'une source locale) ouvrait par open() les bases SQLite.
    Socle de ``test_v56103_fichiers.Local`` repris sans hériter de ses tests."""
    POSTE, passe, toutes, logique = T3.Local.POSTE, staticmethod(T3.Local.passe), T3.Local.toutes, T3.Local.logique

    def setUp(self):
        T3.Base.setUp(self)                       # même socle que test_v56103_fichiers.Local.setUp
        from agent import fichiers56103 as F
        self.t = tempfile.TemporaryDirectory()
        self.addCleanup(self.t.cleanup)
        self.racine = Path(self.t.name) / 'Travail'
        (self.racine / 'Contrats').mkdir(parents=True)
        self.fichier = self.racine / 'Contrats' / 'Bail ZORVAL.docx'
        self.fichier.write_bytes(b'PK version 1')
        self.passe(self.fichier)
        F.regler_local(self.desk, self.POSTE, T3.DURAND, [{'chemin': str(self.racine), 'nom': 'Travail', 'surveille': True}],
                       {'plafond_mo': 100, 'jours': 30, 'intervalle': 60}, self.cle())
        self.l = F.SourceLocale(self.desk)
        self.ident = F.dossiers_locaux(self.desk)[0]['id']
        db = sqlite3.connect(self.racine / 'Contrats' / 'outil.sqlite3')
        db.execute('PRAGMA journal_mode=WAL')
        db.execute('CREATE TABLE t(x)')
        db.execute("INSERT INTO t VALUES('DURAND')")
        db.commit()
        self.addCleanup(db.close)
        self.base_vivante = db
        for nom in ('outil.sqlite3', 'outil.sqlite3-wal', 'outil.sqlite3-shm'):
            if (self.racine / 'Contrats' / nom).exists():
                self.passe(self.racine / 'Contrats' / nom)

    def test_I1_F_scrutation_empreinte_lecture_et_copie_sans_descripteur(self):
        from agent import fichiers56103 as F
        from agent.common import Stop
        with _EspionBases() as espion:
            bilan = F.scruter(self.desk, T3.time.time())
            props = F.proprietes(self.desk, self.POSTE, T3.DURAND, 'local', self.logique('Contrats/outil.sqlite3'), True, self.toutes())
            with self.assertRaisesRegex(Stop, 'fichier_base_sqlite_non_lu'):
                self.l.lire(self.logique('Contrats/outil.sqlite3'), 10_000_000)
            with self.assertRaisesRegex(Stop, 'fichier_base_sqlite_non_lu'):
                self.l.copier(self.logique('Contrats/outil.sqlite3'), self.logique('copie.sqlite3'))
            with self.assertRaisesRegex(Stop, 'fichier_base_sqlite_non_lu'):
                self.l.copier(self.logique('Contrats'), self.logique('Contrats-copie'))   # dossier qui contient une base
        self.assertEqual(espion.vus, [])
        self.assertTrue(props['sha256'] == '' and props['empreinte_etat'].startswith('stat-'), props)
        self.assertEqual(bilan['erreurs'], 0)
        self.assertGreaterEqual(bilan.get('bases_non_lues', 0), 1)
        # le fichier ordinaire, lui, est toujours versionné et lu
        self.assertEqual(self.l.lire(self.logique('Contrats/Bail ZORVAL.docx'), 1000), b'PK version 1')
        self.assertEqual(self.base_vivante.execute('SELECT x FROM t').fetchone()[0], 'DURAND')


import test_v5688_reversibilite as TR   # noqa: E402  (bureau fictif avec sauvegarde, export et phrase de chiffrement)


class EspionExecution:
    """Garde À L'EXÉCUTION : relève toute ouverture ou copie Python d'un fichier VIVANT du dossier d'état (``*.sqlite3``, ``-wal``, ``-shm``,
    ``-journal``) par builtins.open, io.open, io.FileIO, os.open, pathlib.Path.open/read_bytes/read_text/write_bytes/write_text,
    shutil.copy/copy2/copyfile/copytree/move, zipfile.ZipFile.write et tarfile.TarFile.add. sqlite3 (code C) n'y passe pas : c'est la voie
    permise. Ne voit pas : un sous-processus (la sauvegarde copie dans un processus enfant, par sqlite3), le code C d'une bibliothèque."""
    SUFFIXES = ('.sqlite3', '.sqlite', '.db', '-wal', '-shm', '-journal')

    def __init__(self, etat, exclus=()):
        self.etat, self.exclus, self.vus = str(Path(etat).resolve()), tuple(exclus), []

    def noter(self, origine, chemin):
        try:
            texte = str(Path(os.fsdecode(chemin)).resolve())
        except (TypeError, ValueError, OSError):
            return
        if texte.startswith(self.etat) and texte.lower().endswith(self.SUFFIXES) and not any(x in texte for x in self.exclus):
            self.vus.append((origine, texte))

    def __enter__(self):
        import builtins
        import io
        import pathlib
        import shutil
        import tarfile
        import zipfile
        from unittest.mock import patch
        espion = self

        def envelopper(origine, f, position=0, methode=False):
            def appel(*a, **k):
                cible = a[position] if len(a) > position else None
                if isinstance(cible, (str, bytes, os.PathLike)):
                    espion.noter(origine, cible)
                return f(*a, **k)
            return appel
        cibles = [(builtins, 'open', 0), (io, 'open', 0), (os, 'open', 0), (io, 'FileIO', 0), (shutil, 'copy', 0), (shutil, 'copy2', 0),
                  (shutil, 'copyfile', 0), (shutil, 'copytree', 0), (shutil, 'move', 0)]
        self.patches = [patch.object(m, nom, envelopper('%s.%s' % (m.__name__, nom), getattr(m, nom), pos)) for m, nom, pos in cibles]
        for nom in ('open', 'read_bytes', 'read_text', 'write_bytes', 'write_text'):
            self.patches.append(patch.object(pathlib.Path, nom, envelopper('Path.' + nom, getattr(pathlib.Path, nom), 0)))
        self.patches.append(patch.object(zipfile.ZipFile, 'write', envelopper('ZipFile.write', zipfile.ZipFile.write, 1)))
        self.patches.append(patch.object(tarfile.TarFile, 'add', envelopper('TarFile.add', tarfile.TarFile.add, 1)))
        for p in self.patches:
            p.start()
        return self

    def __exit__(self, *exc):
        for p in reversed(self.patches):
            p.stop()


class GardeExecution(TR.Base):
    """F : les chemins qui manipulent le dossier d'état, exécutés sur un bureau fictif (base en WAL, une base annexe), n'ouvrent aucune
    base vivante autrement que par sqlite3 : sauvegarde complète, export, contrôle de santé, diagnostic de secours, reconnaissance des
    bases par la mise à jour (et mise à jour complète sous Linux en root), synchronisation (schéma et écoute)."""

    def _etat(self):
        etat = Path(self.desk.c['state_dir'])
        self.desk.db.execute('PRAGMA journal_mode=WAL')
        self.desk.db.execute('CREATE TABLE IF NOT EXISTS i1_essai(x)')
        self.desk.db.execute("INSERT INTO i1_essai VALUES('DURAND')")
        self.desk.db.commit()
        db = sqlite3.connect(etat / 'annexe.sqlite3')
        db.execute('CREATE TABLE IF NOT EXISTS t(x)')
        db.execute("INSERT INTO t VALUES ('ZORVAL')")
        db.commit()
        db.close()
        return etat

    def test_I1_F_garde_a_l_execution_aucune_base_vivante_ouverte(self):
        import json
        import upgrade
        from agent import export5688, health567, secours56109 as sc, sync56108, sync56108_fichiers
        etat = self._etat()
        self.phrase()
        config = Path(self.tmp.name) / 'config-i1.json'
        config.write_text(json.dumps({'state_dir': str(etat)}), encoding='utf-8')
        with tempfile.TemporaryDirectory() as d, EspionExecution(etat, exclus=(TR.sv.TRAVAIL,)) as espion:
            self.assertEqual(self.sauver()['statut'], 'reussie')                    # sauvegarde complète
            export5688.construire(self.desk, Path(d) / 'export.zip', {'documents': False})
            self.assertTrue(health567.check(str(config))['ok'])
            sc.diagnostic(str(etat), complet=True)
            sc.sauvegardes(str(etat))
            sc.archives(str(etat))
            bases = sorted(etat.glob('*.sqlite3'))
            self.assertTrue(bases and all(upgrade.est_base_sqlite(p) for p in bases))
            upgrade.prune_database_backups(Path(d))
            sync56108.ensure_schema(self.desk)
            sync56108.ecoute_requise(self.desk)
            sync56108_fichiers.ensure_schema(self.desk)
        self.assertEqual(espion.vus, [])
        self.assertEqual(self.desk.db.execute('SELECT x FROM i1_essai').fetchone()[0], 'DURAND')

    def test_I1_F_contre_exemple_l_espion_voit_une_ouverture_directe(self):
        import shutil
        import zipfile
        etat = self._etat()
        with tempfile.TemporaryDirectory() as d, EspionExecution(etat) as espion:
            with open(etat / 'desk.sqlite3', 'rb') as f:   # lot R : hors service (contre-exemple volontaire du test)
                f.read(16)
            (etat / 'annexe.sqlite3').read_bytes()          # lot R : hors service (contre-exemple volontaire du test)
            shutil.copyfile(etat / 'annexe.sqlite3', Path(d) / 'x')   # lot R : hors service (contre-exemple volontaire du test)
            with zipfile.ZipFile(Path(d) / 'z.zip', 'w') as z:
                z.write(etat / 'annexe.sqlite3', 'annexe')   # lot R : hors service (contre-exemple volontaire du test)
        origines = {o for o, _ in espion.vus}
        self.assertTrue({'builtins.open', 'Path.read_bytes', 'shutil.copyfile', 'ZipFile.write'} <= origines, espion.vus)

    @unittest.skipUnless(hasattr(os, 'geteuid') and os.geteuid() == 0, 'mise à jour complète : installation root simulée (Linux)')
    def test_I1_F_mise_a_jour_complete_sans_ouverture_directe(self):
        import hashlib
        import json
        import upgrade
        etat = self._etat()
        racine = Path(self.tmp.name) / 'maj'
        base, ancienne, source = racine / 'opt', racine / 'opt' / 'releases' / '5.6.108', racine / 'paquet'
        ancienne.mkdir(parents=True)
        source.mkdir()
        for dossier, texte in ((ancienne, 'ancien'), (source, 'nouveau')):
            (dossier / 'code.py').write_text(texte)
            (dossier / 'MANIFEST.sha256').write_text(hashlib.sha256(texte.encode()).hexdigest() + '  code.py\n')
        (base / 'current').symlink_to(ancienne)
        config = racine / 'config.json'
        config.write_text(json.dumps({'mode': 'observe'}))
        with EspionExecution(etat) as espion:
            upgrade.upgrade(source, base, config, etat, systemd=racine / 'systemd')
        self.assertEqual(espion.vus, [])
        self.assertTrue(json.loads(upgrade.receipt_path(base).read_text())['database_backups'])


import test_v56101_securite as S   # noqa: E402  (faux serveur, faux système observé, sonde HTTP locale)


class VeilleServeur(S.Base):
    """I1-04 : trois défauts constatés sur un vrai serveur (décrits ici avec des données fictives)."""

    def _s07(self, rapport):
        return {c['cle']: c for c in rapport['constats'] if c['id'] == 'S07'}

    def test_I1_04a_account_conf_d_acme_surveille_et_env_sans_secret_un_cran_plus_bas(self):
        S.serveur_sain(self.r)
        self.r.f('/etc/acme.sh/acme.sh.env', 'export LE_WORKING_DIR="/etc/acme.sh"\nalias acme.sh="/etc/acme.sh/acme.sh --config-home /etc/acme.sh"\n',
                 '0644', 0, 0)
        self.r.f('/etc/acme.sh/account.conf', "ACCOUNT_EMAIL='cabinet@example.test'\nSAVED_CF_Token='%s'\nSAVED_CF_Account_ID='x'\n" % S.SECRET_ENV,
                 '0644', 0, 0)
        rapport = self.passer('complet', sonde=False)
        s07 = self._s07(rapport)
        compte = s07['S07:/etc/acme.sh/account.conf']
        self.assertEqual(compte['niveau'], 'important')
        self.assertTrue(compte['titre'].startswith('Fichier de secrets lisible par le serveur web'))
        self.assertIn('SAVED_CF_Token', ' '.join(compte['vu']))
        env = s07['S07:/etc/acme.sh/acme.sh.env']
        self.assertEqual(env['niveau'], 'surveiller')
        self.assertIn('sans réglage sensible identifié', env['titre'])
        self.assertNotIn(S.SECRET_ENV, S.json.dumps(rapport, ensure_ascii=False))
        # un .env qui contient vraiment des secrets reste « important »
        self.r.f('/srv/factures/.env', 'DB_PASSWORD=%s\n' % S.SECRET_ENV, '0644', 0, 0)
        self.assertEqual(self._s07(self.passer('complet', sonde=False))['S07:/srv/factures/.env']['niveau'], 'important')

    def test_I1_04b_application_servie_par_un_alias_global_de_conf_enabled(self):
        S.serveur_sain(self.r)
        self.r.f('/var/www/roundcube-ancien/program/include/iniset.php', "<?php\ndefine('RCMAIL_VERSION', '1.6.7');\n")
        self.r.f('/etc/apache2/conf-enabled/webmail.conf', '# alias global\nAlias /webmail "/var/www/roundcube-ancien/"\n'
                 '<Directory /var/www/roundcube-ancien>\n  Require all granted\n</Directory>\n')
        rapport = self.passer('complet', sonde=False)
        s09 = [c for c in rapport['constats'] if c['id'] == 'S09']
        self.assertEqual(len(s09), 1, s09)
        self.assertEqual(s09[0]['niveau'], 'important')
        self.assertTrue(s09[0]['titre'].startswith('Roundcube 1.6.7 obsolète (minimum sûr : 1.7.6)'))
        self.assertTrue(any('servie sur tous les sites par un alias global' in v and 'conf-enabled/webmail.conf' in v for v in s09[0]['vu']), s09[0]['vu'])
        app = next(a for a in rapport['applications'] if a['version'] == '1.6.7')
        self.assertEqual(app['exposition'], 'servie')

    def test_I1_04c_copie_dormante_signalee_un_cran_plus_bas_avec_libelle_clair(self):
        S.serveur_sain(self.r)
        self.r.f('/var/www/archives/roundcube-1.6/program/include/iniset.php', "<?php\ndefine('RCMAIL_VERSION', '1.6.7');\n")
        self.r.f('/var/www/archives/old/rainloop/v/1.17.0/index.php', '<?php\n')
        rapport = self.passer('complet', sonde=False)
        s09 = {c['titre']: c for c in rapport['constats'] if c['id'] == 'S09'}
        self.assertEqual(len(s09), 2, s09)
        self.assertTrue(all(c['niveau'] == 'surveiller' for c in s09.values()), s09)
        self.assertTrue(all('copie non servie' in t for t in s09), list(s09))
        self.assertTrue(all(any(v.startswith('Exposition : copie non servie') for v in c['vu']) for c in s09.values()))
        graves = [(c['id'], c['titre']) for c in rapport['constats'] if c['niveau'] in ('critique', 'important')]
        self.assertEqual(graves, [])   # le serveur sain reste sans constat grave
        # la même copie servie par un site redevient « importante »
        self.r.f('/etc/apache2/sites-enabled/archives.conf', '<VirtualHost *:443>\n  ServerName archives.example.test\n  DocumentRoot /var/www/archives\n</VirtualHost>\n')
        rapport = self.passer('complet', sonde=False)
        s09 = [c for c in rapport['constats'] if c['id'] == 'S09']
        self.assertEqual({c['niveau'] for c in s09}, {'important'})
        self.assertTrue(all(any('servie par le site archives.example.test' in v for v in c['vu']) for c in s09))

    def test_I1_04c_sans_configuration_apache_l_exposition_reste_inconnue_et_le_niveau_inchange(self):
        S.serveur_sain(self.r)
        (self.r.base / 'etc/apache2/sites-enabled/cloud.conf').unlink()
        (self.r.base / 'etc/apache2/sites-enabled').rmdir()
        self.r.f('/var/www/archives/roundcube-1.6/program/include/iniset.php', "<?php\ndefine('RCMAIL_VERSION', '1.6.7');\n")
        rapport = self.passer('complet', sonde=False)
        s09 = [c for c in rapport['constats'] if c['id'] == 'S09']
        self.assertEqual([c['niveau'] for c in s09], ['important'])
        self.assertTrue(any('exposition non déterminée' in v for v in s09[0]['vu']))

    def test_I1_04_la_veille_n_execute_rien(self):
        source = (ROOT / 'deploy' / 'securite' / 'veille-securite.py').read_text(encoding='utf-8')
        debut = source.index('def expositions_apache')
        fin = source.index('def sonder')
        for interdit in ('subprocess', 'os.system', 'popen', 'exec(', 'apache2ctl'):
            self.assertNotIn(interdit, source[debut:fin])


if __name__ == '__main__':
    unittest.main()
