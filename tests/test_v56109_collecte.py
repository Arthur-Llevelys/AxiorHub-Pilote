"""5.6.109 (lot G4, G11) : collecte supervisée d'un portail procédural (``collecte_portail56109``).

Faux navigateur (événements DevTools et téléchargements simulés), faux portail derrière un adaptateur d'essai, vraie barrière de prise de main
du lot O (``navigateur56109``), vrai stockage local du cabinet (répertoire temporaire), fausse messagerie pour les brouillons. Aucun portail,
compte, certificat ni réseau réel : ces essais ne qualifient ni e-Barreau, ni Portalis, ni un écran réel. Données fictives (DOS-001, example.test).
"""
from datetime import datetime, timedelta, timezone
import hashlib
import io
import json
from pathlib import Path
import socket
import tempfile
import unittest
from unittest.mock import patch
import zipfile

from agent import collecte_portail56109 as C, navigateur56109 as N, navigateur5673 as nv, dossiers5697 as D
from agent.common import Stop
import test_v5652 as base
from test_v56109_offre import Etat, FausseBoite

MAINTENANT = datetime.now(timezone.utc).replace(microsecond=0)


def _reseau_interdit(*a, **k):
    raise AssertionError('appel réseau non simulé')


def setUpModule():
    global _p1, _p2
    _p1 = patch.object(socket.socket, 'connect', _reseau_interdit)
    _p2 = patch.object(socket, 'create_connection', _reseau_interdit)
    _p1.start()
    _p2.start()


def tearDownModule():
    _p1.stop()
    _p2.stop()


def zip_de(entrees):
    out = io.BytesIO()
    with zipfile.ZipFile(out, 'w') as z:
        for nom, data in entrees:
            z.writestr(nom, data)
    return out.getvalue()


class FauxCDP:
    def __init__(self):
        self.evenements = []

    def drain(self):
        out, self.evenements = self.evenements, []
        return out


class FauxNavigateur:
    def __init__(self, dossier):
        self.cdp = FauxCDP()
        self.downloads = Path(dossier)


class FauxPortail:
    """Portail simulé : pages de non-lus (deux par page), ouverture qui marque « lu », export ZIP natif par message."""
    def __init__(self):
        self.connecte, self.compte, self.delegation = True, 'avocat-A', ''
        self.messages, self.affiche, self.taille_page, self.doubles = [], '', 2, set()

    def non_lus_initiaux(self):
        return [m for m in self.messages if m['non_lu']]


class FauxOnglet:
    """Onglet piloté : chaque méthode est une commande au navigateur, comptée (aucune ne doit partir quand la barrière refuse)."""
    def __init__(self, portail, navigateur):
        self.portail, self.navigateur, self.appels = portail, navigateur, []

    def text(self):
        self.appels.append('text')
        return 'connecte' if self.portail.connecte else 'connexion'

    def navigate(self, url):
        self.appels.append('navigate')
        ident = url.rsplit('/', 1)[-1]
        self.portail.affiche = ident
        for m in self.portail.messages:
            if m['identifiant'] == ident:
                m['non_lu'] = False                       # l'ouverture change l'état sur le portail

    def click(self, ref):
        self.appels.append('click')
        m = next(x for x in self.portail.messages if x['identifiant'] == self.portail.affiche)
        guids = ['g-' + m['identifiant']] + (['g2-' + m['identifiant']] if m['identifiant'] in self.portail.doubles else [])
        for g in guids:
            (self.navigateur.downloads / g).write_bytes(m['zip'])
            self.navigateur.cdp.evenements.append({'method': 'Browser.downloadWillBegin', 'params': {'guid': g, 'suggestedFilename': 'message.zip'}})
            self.navigateur.cdp.evenements.append({'method': 'Browser.downloadProgress', 'params': {'guid': g, 'state': 'completed'}})


class AdaptateurEssai(C.Adaptateur):
    """Adaptateur d'ESSAI (jamais un profil réel) : n'utilise que le pilote, donc la barrière."""
    version = 'essai-1'
    portail = None
    avant_ouverture = None

    def identite(self, pilote):
        return {'compte': self.portail.compte, 'delegation': self.portail.delegation} if pilote.text() == 'connecte' else None

    def page_non_lus(self, pilote, numero, debut, fin):
        pilote.text()
        liste = self.portail._figee if hasattr(self.portail, '_figee') else None
        tous = [m for m in self.portail.messages if m['non_lu'] or (liste and m['identifiant'] in liste)]
        page = tous[(numero - 1) * self.portail.taille_page:numero * self.portail.taille_page]
        return {'messages': [{k: m[k] for k in m if k != 'zip'} for m in page], 'derniere': numero * self.portail.taille_page >= len(tous)}

    def ouvrir(self, pilote, identifiant):
        if AdaptateurEssai.avant_ouverture:
            AdaptateurEssai.avant_ouverture()
        pilote.navigate('https://portail.example.test/messages/' + identifiant)

    def message_affiche(self, pilote):
        pilote.text()
        return self.portail.affiche

    def exporter(self, pilote, identifiant):
        pilote.click('export')


class _Gestionnaire:
    def persist(self, s):
        pass


def message(ident, jours=1, non_lu=True, rg='24/01234', juridiction='Tribunal judiciaire de Valbrune', dossier_portail='', entrees=None, dates=None, natures=None):
    return {'identifiant': ident, 'recu': (MAINTENANT - timedelta(days=jours)).isoformat(), 'non_lu': non_lu, 'objet': 'Notification %s' % ident,
            'juridiction': juridiction, 'rg': rg, 'portail_dossier': dossier_portail, 'dates': dates or [], 'natures': natures or {},
            'zip': zip_de(entrees or [('message.pdf', b'%PDF-1.4 corps ' + ident.encode()), ('conclusions.pdf', b'%PDF-1.4 conclusions ' + ident.encode())])}


class Base(base.Base):
    def setUp(self):
        super().setUp()
        C.ensure_schema(self.desk)
        D.ensure_schema(self.desk)
        quand = datetime.now(timezone.utc).isoformat()
        self.desk.db.execute("INSERT OR REPLACE INTO dossiers5697(id,cabinet_id,ref,ref_norm,titre,juridiction,rg,chemin,cree_le,modifie_le) VALUES(?,?,?,?,?,?,?,?,?,?)",
                             (base.MID, 'cabinet', 'DOS-001', 'dos001', 'DURAND c/ MARTIN', 'Tribunal judiciaire de Valbrune', '24/01234', '/Dossiers/DEMO', quand, quand))
        for pid, role, courriel in (('p1', 'client', 'durand@example.test'), ('p2', 'avocat_client', 'dominus@example.test'),
                                    ('p3', 'adverse', 'martin@example.test'), ('p4', 'conseil_adverse', 'confrere.adverse@example.test')):
            self.desk.db.execute('INSERT OR REPLACE INTO dossier_parties5697(id,dossier_id,nom,nom_norm,role,courriel,cree_le) VALUES(?,?,?,?,?,?,?)',
                                 (pid, base.MID, pid, pid, role, courriel, quand))
        self.desk.db.commit()
        self.portail = FauxPortail()
        self.tmp_dl = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp_dl.cleanup)
        self.navigateur = FauxNavigateur(self.tmp_dl.name)
        self.onglet = FauxOnglet(self.portail, self.navigateur)
        self.seance = nv.Session(_Gestionnaire(), 'cabinet', 'Collecte d’essai', '')
        AdaptateurEssai.portail = self.portail
        AdaptateurEssai.avant_ouverture = None
        self.boite = Etat()
        for p in (patch.dict(C.ADAPTATEURS, {'ebarreau': AdaptateurEssai}),
                  patch.object(C, 'NAVIGATION', lambda desk, acteur: C.Navigation(self.seance, self.onglet, self.navigateur)),
                  patch.object(C, 'BOITE', lambda cfg: FausseBoite(self.boite, cfg))):
            p.start()
            self.addCleanup(p.stop)
        self.profil = C.enregistrer_profil(self.desk, 'admin@example.test', 'administrateur', {
            'portail': 'ebarreau', 'url_entree': 'https://portail.example.test/', 'domaines': 'portail.example.test', 'systeme': 'Windows 11',
            'navigateur': 'Chromium d’essai', 'compte': 'avocat-A', 'type_compte': 'professionnel'})
        self.profil = C.qualifier_profil(self.desk, 'admin@example.test', 'administrateur', self.profil['id'], self.profil['version'], 'essai simulé')
        self.n = 0

    def lancer(self):
        self.n += 1
        return C.lancer(self.desk, 'avocat@example.test', 'avocat', {'cle': 'cle-collecte-%04d' % self.n, 'profil': self.profil['id']})

    def collecter(self):
        row = self.lancer()
        return C.executer(self.desk, row['id'], 'cabinet')

    def fichiers(self, sous=''):
        racine = self.root / 'Dossiers' / 'DEMO' / sous
        return sorted(str(p.relative_to(self.root / 'Dossiers')) for p in racine.rglob('*') if p.is_file() and p.name != 'Convention.pdf') if racine.exists() else []


# Socle seulement : les tests propres des classes de base (5.6.52, 5.6.53) ne sont pas rejoués ici.
for _nom in [n for n in dir(Base) if n.startswith('test')]:
    setattr(Base, _nom, None)

class G11Profils(Base):
    def test_G11_sans_adaptateur_qualifie_rien_n_est_collecte(self):
        with patch.dict(C.ADAPTATEURS, {}, clear=True):
            p = C.vue_profil(self.desk, self.profil['id'])
            self.assertIn('adaptateur_ecran', p['manques'])
            row = self.lancer()
            self.assertEqual(row['etat'], 'profil_non_qualifie')
            vue = C.executer(self.desk, row['id'], 'cabinet')
        self.assertEqual(vue['etat'], 'profil_non_qualifie')
        self.assertEqual(self.onglet.appels, [])

    def test_G11_portalis_non_qualifie_sans_adresse_ni_compte_professionnel(self):
        p = C.enregistrer_profil(self.desk, 'admin@example.test', 'administrateur', {'portail': 'portalis', 'compte': 'avocat-A'})
        self.assertEqual(p['qualification'], 'non_qualifie')
        self.assertTrue({'url_entree', 'type_compte'} <= set(p['manques']))
        with self.assertRaisesRegex(Stop, 'profil_non_qualifiable'):
            C.qualifier_profil(self.desk, 'admin@example.test', 'administrateur', p['id'], p['version'], 'essai')
        with self.assertRaisesRegex(Stop, 'type_compte_non_professionnel'):
            C.enregistrer_profil(self.desk, 'admin@example.test', 'administrateur', {'portail': 'portalis', 'type_compte': 'justiciable'})

    def test_G11_modification_du_profil_perime_la_preuve(self):
        p = C.enregistrer_profil(self.desk, 'admin@example.test', 'administrateur', {'id': self.profil['id'], 'version': self.profil['version'], 'portail': 'ebarreau',
                                 'url_entree': 'https://portail.example.test/', 'domaines': 'portail.example.test', 'systeme': 'Ubuntu 24.04',
                                 'navigateur': 'Chromium d’essai', 'compte': 'avocat-A', 'type_compte': 'professionnel'})
        self.assertEqual((p['qualification'], p['version']), ('non_qualifie', self.profil['version'] + 1))

    def test_G11_profil_reserve_a_l_administrateur_et_domaine_large_refuse(self):
        with self.assertRaises(Stop):
            C.enregistrer_profil(self.desk, 'avocat@example.test', 'avocat', {'portail': 'ebarreau'})
        with self.assertRaisesRegex(Stop, 'domaine_portail_invalide'):
            C.enregistrer_profil(self.desk, 'admin@example.test', 'administrateur', {'portail': 'ebarreau', 'domaines': '*.example.test'})

    def test_G11_contrat_sans_action_engageante(self):
        for interdit in ('envoyer', 'deposer', 'signer', 'supprimer', 'payer', 'deleguer', 'repondre'):
            self.assertFalse(any(interdit in n for n in dir(C.Adaptateur)), interdit)


class G11Connexion(Base):
    def test_G11_attente_connexion_humaine_sans_boucle(self):
        self.portail.connecte = False
        self.portail.messages = [message('m1')]
        vue = self.collecter()
        self.assertEqual(vue['etat'], 'attente_connexion')
        self.assertEqual(self.onglet.appels, ['text'])                              # une seule observation, aucune saisie, aucun PIN
        self.assertEqual(vue['messages'], [])

    def test_G11_identite_differente_suspend(self):
        self.portail.compte = 'autre-avocat'
        self.portail.messages = [message('m1')]
        vue = self.collecter()
        self.assertEqual(vue['etat'], 'identite_differente')
        self.assertNotIn('navigate', self.onglet.appels)


class G11Inventaire(Base):
    def test_G11_cinq_jours_toutes_pages_non_lus_initiaux(self):
        self.portail.messages = [message('m1', 1), message('m2', 2), message('m3', 3), message('m4', 4),
                                 message('lu', 1, non_lu=False), message('ancien', 6)]
        vue = self.collecter()
        self.assertEqual(vue['couverture'], 'complete')
        self.assertEqual(sorted(m['identifiant'] for m in vue['messages']), ['m1', 'm2', 'm3', 'm4'])
        self.assertEqual(vue['pages_lues'], 3)                                      # 5 non-lus sur 3 pages (dont l'ancien, hors fenêtre)
        self.assertEqual(vue['etat'], 'terminee')
        self.assertTrue(all(m['effet_portail'] == 'lu_par_consultation' for m in vue['messages']))
        debut = datetime.fromisoformat(vue['debut_utc'])
        self.assertEqual(datetime.fromisoformat(vue['fin_utc']) - debut, timedelta(days=5))

    def test_G11_plafond_de_pages_couverture_partielle_jamais_complete(self):
        self.portail.messages = [message('m%d' % i, 1) for i in range(5)]
        with patch.object(C, 'MAX_PAGES', 1):
            vue = self.collecter()
        self.assertEqual((vue['couverture'], vue['etat'], vue['motif']), ('partielle', 'partielle', 'plafond_de_pages'))

    def test_G11_boite_vide_partielle_n_est_pas_aucun_message(self):
        def panne(*a, **k):
            raise Stop('session_expiree')
        with patch.object(AdaptateurEssai, 'page_non_lus', panne):
            vue = self.collecter()
        self.assertEqual((vue['couverture'], vue['etat']), ('partielle', 'partielle'))
        self.assertTrue(vue['motif'].startswith('inventaire_interrompu'))


class G11Classement(Base):
    def test_G11_originaux_et_annexes_conserves_noms_identiques(self):
        self.portail.messages = [message('m1', entrees=[('message.pdf', b'%PDF corps'), ('annexe.pdf', b'%PDF annexe v1'), ('annexe.pdf', b'%PDF annexe v2')],
                                         natures={'message.pdf': 'message'})]
        vue = self.collecter()
        self.assertEqual(vue['etat'], 'terminee')
        pieces = self.desk.db.execute('SELECT * FROM collecte_pieces56109 ORDER BY ordinal').fetchall()
        self.assertEqual([p['origine'] for p in pieces], ['archive_originale', 'archive', 'archive', 'archive'])
        self.assertTrue(all(p['etat'] == 'verifiee' for p in pieces))
        self.assertEqual(len({p['sha256'] for p in pieces}), 4)
        for p in pieces:                                                             # octets déposés = octets reçus
            self.assertEqual(hashlib.sha256((self.root / p['chemin'].lstrip('/')).read_bytes()).hexdigest(), p['sha256'])
        self.assertTrue(any('CORRESPONDANCES' in f for f in self.fichiers()))

    def test_G11_classement_message_par_message_dans_le_bon_dossier(self):
        quand = datetime.now(timezone.utc).isoformat()
        (self.root / 'Dossiers' / 'AUTRE').mkdir()
        matters = json.loads(Path(self.f.c['matters_file']).read_text(encoding='utf-8'))
        matters.append({'id': 'DOS-002', 'client_name': 'Client AUTRE', 'path': '/Dossiers/AUTRE', 'references': ['DOS-002'], 'aliases': [], 'correspondents': []})
        Path(self.f.c['matters_file']).write_text(json.dumps(matters), encoding='utf-8')
        self.desk.db.execute("INSERT INTO dossiers5697(id,cabinet_id,ref,ref_norm,titre,juridiction,rg,chemin,cree_le,modifie_le) VALUES(?,?,?,?,?,?,?,?,?,?)",
                             ('DOS-002', 'cabinet', 'DOS-002', 'dos002', 'KERVILAC c/ ORLANDEC', 'Tribunal judiciaire de Valbrune', '25/00042', '/Dossiers/AUTRE', quand, quand))
        self.desk.db.commit()
        self.portail.messages = [message('m1', rg='24/01234'), message('m2', rg='25/00042')]
        vue = self.collecter()
        self.assertEqual(sorted((m['identifiant'], m['dossier']) for m in vue['messages']), [('m1', base.MID), ('m2', 'DOS-002')])
        autre = sorted(str(p.relative_to(self.root)) for p in (self.root / 'Dossiers' / 'AUTRE').rglob('*') if p.is_file())
        self.assertTrue(autre and all('m2' in f for f in autre))
        self.assertTrue(all('m2' not in f for f in self.fichiers()))

    def test_G11_meme_rg_deux_juridictions_question_et_zone_recuperee(self):
        quand = datetime.now(timezone.utc).isoformat()
        self.desk.db.execute("INSERT INTO dossiers5697(id,cabinet_id,ref,ref_norm,titre,juridiction,rg,chemin,cree_le,modifie_le) VALUES(?,?,?,?,?,?,?,?,?,?)",
                             ('DOS-003', 'cabinet', 'DOS-003', 'dos003', 'ZORVAL c/ BERNARD', 'Cour d’appel de Valbrune', '24/01234', '/Dossiers/DEMO', quand, quand))
        self.desk.db.commit()
        self.portail.messages = [message('ambigu', juridiction=''), message('m2', rg='24/01234')]
        vue = self.collecter()
        par = {m['identifiant']: m for m in vue['messages']}
        self.assertEqual((par['ambigu']['etat'], par['ambigu']['question']), ('a_preciser', 'rattachement_a_preciser'))
        self.assertEqual(par['m2']['etat'], 'classe')                               # les affaires indépendantes continuent
        self.assertTrue(all('ambigu' not in f for f in self.fichiers()))            # rien rangé au seul RG
        self.assertEqual(vue['etat'], 'partielle')
        local = self.desk.db.execute("SELECT archive_local FROM collecte_messages56109 WHERE identifiant='ambigu'").fetchone()[0]
        self.assertTrue(Path(local).is_file())                                      # original gardé dans la zone récupérée

    def test_G11_rg_seul_sans_juridiction_question(self):
        self.desk.db.execute("UPDATE dossiers5697 SET juridiction='' WHERE id=?", (base.MID,))
        self.desk.db.commit()
        self.portail.messages = [message('m1')]
        vue = self.collecter()
        self.assertEqual(vue['messages'][0]['question'], 'rattachement_a_preciser')

    def test_G11_correspondance_confirmee_puis_classement(self):
        self.portail.messages = [message('m1', rg='', dossier_portail='EB-77')]
        vue = self.collecter()
        self.assertEqual(vue['messages'][0]['etat'], 'a_preciser')
        C.confirmer_correspondance(self.desk, 'avocat@example.test', 'avocat', 'ebarreau', 'avocat-A', 'EB-77', base.MID)
        again = C.executer(self.desk, vue['id'], 'cabinet')
        self.assertEqual(again['messages'][0]['etat'], 'classe')

    def test_G11_actes_dans_procedure(self):
        self.portail.messages = [message('m1', entrees=[('conclusions incident.pdf', b'%PDF c'), ('ordonnance.pdf', b'%PDF o'), ('piece 3.pdf', b'%PDF p')])]
        self.collecter()
        familles = dict(self.desk.db.execute("SELECT nom_source, famille FROM collecte_pieces56109 WHERE origine='archive'").fetchall())
        self.assertEqual(familles, {'conclusions incident.pdf': 'procedure', 'ordonnance.pdf': 'procedure', 'piece 3.pdf': 'a_classer'})
        self.assertTrue(any('PROCEDURE' in f for f in self.fichiers()))
        self.assertFalse(any('NOS PIECES' in f for f in self.fichiers()))           # jamais toutes les annexes dans nos pièces

    def test_G11_reprise_sans_doublon(self):
        self.portail.messages = [message('m1'), message('m2')]
        self.portail._figee = {'m1', 'm2'}                                         # même liste revue au passage suivant (chevauchement)
        self.collecter()
        avant = self.fichiers()
        vue = self.collecter()
        self.assertEqual(self.fichiers(), avant)
        self.assertEqual(self.desk.db.execute('SELECT COUNT(*) FROM collecte_messages56109').fetchone()[0], 2)
        self.assertEqual(self.desk.db.execute('SELECT COUNT(*) FROM collecte_brouillons56109').fetchone()[0], 4)

    def test_G11_telechargement_non_associe_jamais_classe(self):
        self.portail.messages = [message('m1')]
        self.portail.doubles = {'m1'}
        vue = self.collecter()
        self.assertEqual(vue['messages'][0]['etat'], 'telechargement_a_verifier')
        self.assertEqual(self.fichiers(), [])


class G11Barriere(Base):
    def test_G11_main_a_l_avocat_aucune_commande(self):
        self.portail.messages = [message('m1')]
        N.changer_main(self.seance, True)
        vue = self.collecter()
        self.assertEqual(vue['etat'], 'suspendue')
        self.assertEqual(self.onglet.appels, [])

    def test_G11_mutation_sans_barriere_les_commandes_partiraient(self):
        """Mutation en mémoire : barrière neutralisée, la commande part malgré la main à l'avocat — preuve que la barrière l'empêche."""
        from contextlib import contextmanager

        @contextmanager
        def sans_barriere(s, epoch, desk=None):
            yield
        self.portail.messages = [message('m1')]
        N.changer_main(self.seance, True)
        with patch.object(N, 'barriere', sans_barriere), patch.object(N, 'puis_je', lambda s, e, d=None: {'ok': True, 'code': '', 'epoch': e}):
            self.collecter()
        self.assertIn('text', self.onglet.appels)

    def test_G11_pause_globale_suspend_avant_toute_observation(self):
        self.portail.messages = [message('m1')]
        self.desk.setting('missions567:pause', True)
        vue = self.collecter()
        self.assertEqual(vue['etat'], 'suspendue')
        self.assertEqual(self.onglet.appels, [])

    def test_G11_prise_de_main_pendant_la_collecte_puis_reprise_sans_doublon(self):
        self.portail.messages = [message('m1'), message('m2')]
        self.portail._figee = {'m1', 'm2'}
        AdaptateurEssai.avant_ouverture = lambda: N.changer_main(self.seance, True)
        row = self.lancer()
        vue = C.executer(self.desk, row['id'], 'cabinet')
        self.assertEqual(vue['etat'], 'suspendue')
        self.assertNotIn('navigate', self.onglet.appels)                          # ouverture calculée avant la prise de main : jetée
        AdaptateurEssai.avant_ouverture = None
        N.changer_main(self.seance, False)
        vue = C.executer(self.desk, row['id'], 'cabinet')
        self.assertEqual(vue['etat'], 'terminee')
        self.assertEqual(self.desk.db.execute("SELECT COUNT(*) FROM collecte_pieces56109 WHERE origine='archive_originale'").fetchone()[0], 2)


class G11DatesEtBrouillons(Base):
    def test_G11_dates_sourcees_a_verifier_sans_calcul(self):
        self.portail.messages = [message('m1', dates=[{'date': '2026-12-10', 'nature': 'audience', 'extrait': 'Audience du 10 décembre 2026'},
                                                      {'date': '2027-02-22', 'nature': 'delai_calcule', 'extrait': 'Conclusions d’incident'}])]
        before = self.desk.db.execute("SELECT COUNT(*) FROM sqlite_master WHERE name='agenda5697'").fetchone()[0]
        n_agenda = self.desk.db.execute('SELECT COUNT(*) FROM agenda5697').fetchone()[0] if before else 0
        self.collecter()
        dates = C.propositions_dates(self.desk, ('avocat@example.test', 'avocat'), base.MID)
        self.assertEqual(sorted((d['date'], d['etat']) for d in dates), [('2026-12-10', 'a_verifier'), ('2027-02-22', 'a_preciser')])
        self.assertTrue(all(d['source'] == 'm1' and d['extrait'] for d in dates))
        if before:
            self.assertEqual(self.desk.db.execute('SELECT COUNT(*) FROM agenda5697').fetchone()[0], n_agenda)   # rien inscrit d'office

    def test_G11_brouillons_client_et_dominus_jamais_adverse_ni_greffe(self):
        self.portail.messages = [message('m1')]
        vue = self.collecter()
        self.assertEqual(sorted((b['destinataire'], b['role'], b['etat']) for b in vue['brouillons']),
                         [('dominus@example.test', 'dominus_litis', 'prepare'), ('durand@example.test', 'client', 'prepare')])
        self.assertEqual(self.boite.drafts, [])                                     # préparés, pas déposés : action distincte
        out = C.deposer_brouillons(self.desk, 'avocat@example.test', 'avocat', vue['id'])
        self.assertEqual((out['deposes'], out['envoi']), (2, False))
        self.assertEqual(sorted(d['To'] for d in self.boite.drafts), ['dominus@example.test', 'durand@example.test'])
        self.assertTrue(all('In-Reply-To' not in d and 'Cc' not in d for d in self.boite.drafts))
        again = C.deposer_brouillons(self.desk, 'avocat@example.test', 'avocat', vue['id'])
        self.assertEqual((again['deposes'], len(self.boite.drafts), self.boite.appends), (0, 2, 2))

    def test_G11_depot_incertain_a_rapprocher_jamais_recree(self):
        self.portail.messages = [message('m1')]
        vue = self.collecter()
        self.boite.fail_append = True
        out = C.deposer_brouillons(self.desk, 'avocat@example.test', 'avocat', vue['id'])
        self.assertEqual(out['a_rapprocher'], 2)
        self.boite.fail_append = False
        again = C.deposer_brouillons(self.desk, 'avocat@example.test', 'avocat', vue['id'])
        self.assertEqual((again['deposes'], again['a_rapprocher'], self.boite.appends), (0, 2, 2))

    def test_G11_destinataire_absent_question_sans_invention(self):
        self.desk.db.execute("DELETE FROM dossier_parties5697 WHERE role IN ('client','avocat_client')")
        self.desk.db.commit()
        self.portail.messages = [message('m1')]
        vue = self.collecter()
        self.assertEqual(vue['brouillons'], [])
        self.assertEqual(vue['messages'][0]['question'], 'destinataire_a_preciser')

    def test_G11_depot_reserve_aux_avocats(self):
        with self.assertRaises(Stop):
            C.deposer_brouillons(self.desk, 'assistant@example.test', 'assistant')


if __name__ == '__main__':
    unittest.main()
