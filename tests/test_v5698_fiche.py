"""5.6.98 : fiche du dossier — onglets Parties, Juridiction et Fichiers (``fiche5698``, ``fiche_ui5698``, routes ``m57/dossiers/<id>/…``).

Schéma additif rejouable, commandes idempotentes (rejeu, clé réutilisée, version périmée), 404 neutre pour un dossier réservé, aucune IA,
arborescence bornée et stockage hors ligne, une seule vérité pour la juridiction et le RG (instance principale ↔ registre), alias
``vue=documents``, rendu des trois onglets. Faux stockage en mémoire, aucun réseau (tout appel de socket échoue), aucun modèle ; données
fictives seulement (DURAND, MARTIN, BERNARD, ZORVAL, KERVILAC, ORLANDEC, DOS-001, RG 24/01234, Valbrune, Brumeval, example.test)."""
import socket
import unittest
import uuid
from unittest.mock import patch

from agent import dossiers5697 as D, fiche5698 as F
from agent.common import Stop
from test_v5697_dossiers import ADMIN, AVOCATE, CAMILLE, CLAIRE, JETON, Base, FauxStockage


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


class StockageFichiers(FauxStockage):
    """Faux stockage avec fichiers (taille, date RFC 1123 comme le WebDAV) ; ``lectures`` compte les PROPFIND."""

    def __init__(self):
        super().__init__()
        self.fichiers = {}
        self.lectures = 0

    def ajouter(self, chemin, taille=100, modifie='Mon, 02 Nov 2026 10:00:00 GMT'):
        self.fichiers[chemin] = (taille, modifie)
        parent = chemin.rsplit('/', 1)[0]
        while parent and parent not in self.repertoires:
            self.repertoires.add(parent)
            parent = parent.rsplit('/', 1)[0]

    def list_folder(self, chemin):
        self.lectures += 1
        out = super().list_folder(chemin)
        out += [{'path': p, 'directory': False, 'size': t, 'modified': m} for p, (t, m) in sorted(self.fichiers.items()) if p.rsplit('/', 1)[0] == chemin]
        return out


class Fiche(Base):
    def setUp(self):
        super().setUp()
        self.stock = StockageFichiers()
        p = patch.object(D, '_client_stockage', lambda desk: self.stock)
        p.start()
        self.addCleanup(p.stop)

    def dossier(self, acteur=ADMIN, **donnees):
        recu = self.creer(acteur=acteur, **donnees)
        D.traiter_rangements(self.desk)
        return D.lire(self.desk, ADMIN, recu['id'])

    def page(self, ident, **args):
        from agent import dossier5679
        return dossier5679.page(self.desk, {'csrf': JETON, 'owner': 'cabinet'}, '/p', {'id': ident, **args}, lambda titre, corps, *a: corps + (a[3] if len(a) > 3 else ''))


# ------------------------------------------------------------------------------------------------ schéma et migration
class Schema(Fiche):
    def test_schema_additif_et_rejouable(self):
        d = self.dossier(titre='DURAND c/ MARTIN', client='DURAND')
        F.ensure_schema(self.desk)
        F._PRET.clear()
        F.ensure_schema(self.desk)                     # rejoué : aucune erreur, aucune colonne en double
        colonnes = [r[1] for r in self.desk.db.execute('PRAGMA table_info(dossier_parties5697)')]
        for c in ('qualite', 'type_personne', 'telephone', 'adresse', 'siren', 'avocat_id', 'note', 'version', 'conflit_statut'):
            self.assertEqual(colonnes.count(c), 1, c)
        self.assertTrue(self.desk.db.execute("SELECT 1 FROM sqlite_master WHERE name='dossier_instances5698'").fetchone())
        client = F.parties(self.desk, ADMIN, d['id'])['parties'][0]   # partie créée en 5.6.97 : valeurs par défaut, rien de perdu
        self.assertEqual((client['nom'], client['role'], client['version'], client['groupe']), ('DURAND', 'client', 1, 'clients'))

    def test_migration_instance_principale_sans_perte_et_rejouable(self):
        d = self.dossier(titre='ZORVAL', juridiction='Tribunal judiciaire de Valbrune', rg='24/01234')
        inst = F.instances(self.desk, ADMIN, d['id'])['instances']
        self.assertEqual(len(inst), 1)
        self.assertTrue(inst[0]['principale'])
        self.assertEqual((inst[0]['juridiction'], inst[0]['rg'], inst[0]['provenance']), ('Tribunal judiciaire de Valbrune', '24/01234', 'registre'))
        self.assertEqual(F.migrer_instances(self.desk), 0)
        self.assertEqual(F.migrer_instances(self.desk, d['id']), 0)
        self.assertEqual(self.desk.db.execute('SELECT COUNT(*) FROM dossier_instances5698').fetchone()[0], 1)
        sans = self.dossier(titre='KERVILAC')
        self.assertEqual(F.instances(self.desk, ADMIN, sans['id'])['instances'], [])   # ni juridiction ni RG : aucune instance inventée


# ------------------------------------------------------------------------------------------------ parties
class Parties(Fiche):
    def test_ajout_rejeu_cle_reutilisee_doublon_et_champs(self):
        d = self.dossier(titre='DURAND c/ MARTIN', client='DURAND')
        cle = uuid.uuid4().hex
        corps = {'cle': cle, 'nom': 'BERNARD', 'role': 'avocat_client', 'courriel': 'bernard@example.test', 'qualite': 'Avocat au barreau de Brumeval'}
        code, recu = self.appel('dossiers/%s/parties' % d['id'], corps)
        self.assertEqual(code, '201', recu)
        code, rejoue = self.appel('dossiers/%s/parties' % d['id'], corps)
        self.assertEqual((code, rejoue['id'], rejoue.get('rejoue')), ('200', recu['id'], True))
        self.assertEqual(self.appel('dossiers/%s/parties' % d['id'], {**corps, 'qualite': 'autre'})[0], '409')   # même clé, autre corps
        code, double = self.appel('dossiers/%s/parties' % d['id'], {**corps, 'cle': uuid.uuid4().hex})
        self.assertEqual((code, double['error']), ('409', 'partie_en_double'))
        code, err = self.appel('dossiers/%s/parties' % d['id'], {'cle': uuid.uuid4().hex, 'nom': '', 'role': 'inconnu', 'siren': '123456789',
                                                                 'courriel': 'pas-une-adresse'})
        self.assertEqual(code, '422')
        self.assertTrue({'nom', 'role', 'siren', 'courriel'} <= set(err['champs']), err)
        code, societe = self.appel('dossiers/%s/parties' % d['id'], {'cle': uuid.uuid4().hex, 'nom': 'ZORVAL SAS', 'role': 'client', 'siren': '000 010 017',
                                                                     'avocat_id': recu['id'], 'adresse': '3 rue des Prés, Valbrune'})
        self.assertEqual(code, '201', societe)
        p = next(x for x in F.parties(self.desk, ADMIN, d['id'])['parties'] if x['id'] == societe['id'])
        self.assertEqual((p['siren'], p['type_personne'], p['avocat']), ('000010017', 'morale', 'BERNARD'))
        code, err = self.appel('dossiers/%s/parties' % d['id'], {'cle': uuid.uuid4().hex, 'nom': 'ORLANDEC', 'role': 'client', 'type_personne': 'physique',
                                                                 'siren': '000010025'})
        self.assertEqual((code, list(err['champs'])), ('422', ['siren']))
        self.assertEqual(self.appel('dossiers/%s/parties' % d['id'], {'cle': uuid.uuid4().hex, 'nom': 'ORLANDEC', 'role': 'client',
                                                                      'avocat_id': societe['id']})[1]['champs'], {'avocat_id': 'Choisissez un avocat inscrit dans ce dossier.'})

    def test_modification_version_perimee_puis_retrait(self):
        d = self.dossier(titre='DURAND c/ MARTIN', client='DURAND')
        recu = F.ajouter_partie(self.desk, ADMIN, d['id'], {'nom': 'BERNARD', 'role': 'avocat_client'}, uuid.uuid4().hex)
        client = F.ajouter_partie(self.desk, ADMIN, d['id'], {'nom': 'MARTIN', 'role': 'client', 'avocat_id': recu['id']}, uuid.uuid4().hex)
        code, maj = self.appel('dossiers/%s/parties/%s' % (d['id'], recu['id']), {'cle': uuid.uuid4().hex, 'version': 1, 'nom': 'BERNARD',
                                                                                    'role': 'avocat_client', 'telephone': '01 00 00 00 05'})
        self.assertEqual((code, maj['version']), ('200', 2))
        code, perime = self.appel('dossiers/%s/parties/%s' % (d['id'], recu['id']), {'cle': uuid.uuid4().hex, 'version': 1, 'nom': 'BERNARD',
                                                                                       'role': 'avocat_client', 'telephone': '09 00 00 00 99'})
        self.assertEqual((code, perime['error']), ('409', 'version_perimee'))
        self.assertIn('rien n’a été écrasé', perime['message'])
        self.assertEqual(next(p for p in F.parties(self.desk, ADMIN, d['id'])['parties'] if p['id'] == recu['id'])['telephone'], '01 00 00 00 05')
        self.assertEqual(self.appel('dossiers/%s/parties/%s/retirer' % (d['id'], recu['id']), {'cle': uuid.uuid4().hex, 'version': 1})[0], '409')
        code, retrait = self.appel('dossiers/%s/parties/%s/retirer' % (d['id'], recu['id']), {'cle': uuid.uuid4().hex, 'version': 2})
        self.assertEqual((code, retrait['etat']), ('200', 'retiree'))
        restant = next(p for p in F.parties(self.desk, ADMIN, d['id'])['parties'] if p['id'] == client['id'])
        self.assertEqual((restant['avocat_id'], restant['avocat']), ('', ''))   # plus représenté par un avocat retiré
        journal = [j['action'] for j in D.journal(self.desk, ADMIN, d['id'])]
        self.assertIn('partie_retiree', journal)
        self.assertEqual(self.appel('dossiers/%s/parties/%s' % (d['id'], 'inconnue'), {'cle': uuid.uuid4().hex, 'version': 1, 'nom': 'X', 'role': 'autre'})[0], '404')

    def test_partie_adverse_relance_la_verification_des_conflits(self):
        code, autre = self.appel('dossiers', {'titre': 'KERVILAC', 'client': 'KERVILAC', 'cle': uuid.uuid4().hex})
        self.assertEqual(code, '201', autre)
        d = self.dossier(titre='DURAND c/ KERVILAC', client='DURAND')
        code, recu = self.appel('dossiers/%s/parties' % d['id'], {'cle': uuid.uuid4().hex, 'nom': 'KERVILAC', 'role': 'adverse'})
        self.assertEqual(code, '201', recu)
        self.assertNotEqual(recu['conflits']['statut'], 'aucun_resultat')
        self.assertTrue(recu['conflits']['correspondances'])
        self.assertTrue(all(m.get('matter') != d['id'] for m in recu['conflits']['correspondances']))
        p = next(x for x in F.parties(self.desk, ADMIN, d['id'])['parties'] if x['id'] == recu['id'])
        self.assertEqual(p['conflit_statut'], recu['conflits']['statut'])
        self.assertIn('Conflits d’intérêts', self.page(d['id'], vue='parties'))
        code, libre = self.appel('dossiers/%s/parties' % d['id'], {'cle': uuid.uuid4().hex, 'nom': 'ORLANDEC', 'role': 'adverse'})
        self.assertEqual((code, libre['conflits']['statut']), ('201', 'aucun_resultat'))
        self.assertIn('pas une garantie', libre['conflits']['resume'])

    def test_parties_lues_proposees_jamais_ajoutees(self):
        d = self.dossier(titre='DURAND c/ MARTIN', client='DURAND')
        with patch('agent.resume5659.representation', lambda desk, mid: [
                {'id': 'p1', 'nom': 'DURAND', 'client': True, 'qualite': 'demandeur', 'conseil': '', 'version': 1, 'sources': []},
                {'id': 'p2', 'nom': 'MARTIN', 'client': False, 'qualite': 'défendeur', 'conseil': 'Maître BERNARD', 'version': 1, 'sources': []}]):
            html = self.page(d['id'], vue='parties')
        self.assertIn('Proposées, à valider', html)
        self.assertIn('MARTIN', html)
        self.assertIn('Ajouter au dossier', html)
        self.assertEqual([p['nom'] for p in F.parties(self.desk, ADMIN, d['id'])['parties']], ['DURAND'])   # rien d'ajouté automatiquement


# ------------------------------------------------------------------------------------------------ juridiction
class Instances(Fiche):
    def test_instance_principale_synchronisee_avec_le_registre(self):
        d = self.dossier(titre='DURAND c/ MARTIN')
        code, prem = self.appel('dossiers/%s/instances' % d['id'], {'cle': uuid.uuid4().hex, 'degre': 'premiere_instance', 'juridiction': 'Tribunal judiciaire de Valbrune',
                                                                    'rg': '24/01234', 'portalis': 'abcd-w-b7h-12345', 'nature': 'fond', 'saisine': '2026-03-02'})
        self.assertEqual(code, '201', prem)
        self.assertTrue(prem['principale'])                                     # première instance : principale
        reg = D.lire(self.desk, ADMIN, d['id'])
        self.assertEqual((reg['juridiction'], reg['rg']), ('Tribunal judiciaire de Valbrune', '24/01234'))
        # le registre modifié ailleurs se lit aussitôt sur l'instance principale
        D.modifier(self.desk, ADMIN, d['id'], reg['version'], {'rg': '24/09999'}, uuid.uuid4().hex)
        inst = F.instances(self.desk, ADMIN, d['id'])
        self.assertEqual(inst['instances'][0]['rg'], '24/09999')
        self.assertEqual(inst['instances'][0]['portalis'], 'ABCD-W-B7H-12345')
        # formulaire affiché avant cette modification : version du dossier périmée, rien n'est écrasé
        code, perime = self.appel('dossiers/%s/instances/%s' % (d['id'], prem['id']), {'cle': uuid.uuid4().hex, 'version': prem['version'],
                                                                                         'version_dossier': prem['version_dossier'], 'juridiction': 'Tribunal judiciaire de Valbrune',
                                                                                         'rg': '24/01234', 'principale': '1'})
        self.assertEqual((code, perime['error']), ('409', 'version_perimee'))
        self.assertEqual(D.lire(self.desk, ADMIN, d['id'])['rg'], '24/09999')
        code, maj = self.appel('dossiers/%s/instances/%s' % (d['id'], prem['id']), {'cle': uuid.uuid4().hex, 'version': prem['version'],
                                                                                      'version_dossier': inst['version_dossier'], 'juridiction': 'Tribunal judiciaire de Brumeval',
                                                                                      'rg': '24/09999', 'principale': '1', 'etat': 'juge'})
        self.assertEqual(code, '200', maj)
        self.assertEqual(D.lire(self.desk, ADMIN, d['id'])['juridiction'], 'Tribunal judiciaire de Brumeval')
        # appel : devient principale ; la première instance garde sa juridiction et son RG
        code, appel = self.appel('dossiers/%s/instances' % d['id'], {'cle': uuid.uuid4().hex, 'degre': 'appel', 'juridiction': 'Cour d’appel de Valbrune',
                                                                     'rg': '25/00042', 'principale': True})
        self.assertEqual(code, '201', appel)
        reg = D.lire(self.desk, ADMIN, d['id'])
        self.assertEqual((reg['juridiction'], reg['rg']), ('Cour d’appel de Valbrune', '25/00042'))
        anciennes = {i['id']: i for i in F.instances(self.desk, ADMIN, d['id'])['instances']}
        self.assertFalse(anciennes[prem['id']]['principale'])
        self.assertEqual((anciennes[prem['id']]['juridiction'], anciennes[prem['id']]['rg']), ('Tribunal judiciaire de Brumeval', '24/09999'))
        self.assertEqual(sum(1 for i in anciennes.values() if i['principale']), 1)
        projet = next(m for m in D._registre_json(self.desk) if m['id'] == d['id'])
        self.assertIn('25/00042', projet['references'])                       # projection JSON lue par les pages existantes
        # l'instance principale ne se retire pas ; l'ancienne, si
        code, refus = self.appel('dossiers/%s/instances/%s/retirer' % (d['id'], appel['id']), {'cle': uuid.uuid4().hex, 'version': 1})
        self.assertEqual((code, refus['error']), ('409', 'instance_principale'))
        code, ok = self.appel('dossiers/%s/instances/%s/retirer' % (d['id'], prem['id']), {'cle': uuid.uuid4().hex, 'version': anciennes[prem['id']]['version']})
        self.assertEqual(code, '200', ok)

    def test_champs_instance_et_rejeu(self):
        d = self.dossier(titre='ZORVAL')
        code, err = self.appel('dossiers/%s/instances' % d['id'], {'cle': uuid.uuid4().hex, 'degre': 'lune', 'rg': '24/01234;DROP', 'saisine': '2026-13-45',
                                                                   'portalis': '§§', 'etat': 'perdu'})
        self.assertEqual(code, '422')
        self.assertTrue({'degre', 'rg', 'saisine', 'portalis', 'etat'} <= set(err['champs']), err)
        self.assertEqual(self.appel('dossiers/%s/instances' % d['id'], {'cle': uuid.uuid4().hex})[1]['champs'],
                         {'juridiction': 'Indiquez au moins la juridiction ou le numéro RG.'})
        corps = {'cle': uuid.uuid4().hex, 'degre': 'refere', 'juridiction': 'Tribunal de commerce de Valbrune'}
        self.assertEqual(self.appel('dossiers/%s/instances' % d['id'], corps)[0], '201')
        code, rejoue = self.appel('dossiers/%s/instances' % d['id'], corps)
        self.assertEqual((code, rejoue.get('rejoue')), ('200', True))
        self.assertEqual(len(F.instances(self.desk, ADMIN, d['id'])['instances']), 1)


# ------------------------------------------------------------------------------------------------ droits et IA
class Droits(Fiche):
    def test_dossier_reserve_404_neutre(self):
        d = self.dossier(acteur=AVOCATE, titre='KERVILAC confidentiel', confidentialite='reserve', membres=[CLAIRE])
        absent = self.appel('dossiers/inexistant/parties', owner=CAMILLE, role='avocat')
        for route, corps in (('parties', None), ('instances', None), ('fichiers', None),
                             ('parties', {'cle': uuid.uuid4().hex, 'nom': 'ZORVAL', 'role': 'adverse'}),
                             ('instances', {'cle': uuid.uuid4().hex, 'juridiction': 'Tribunal judiciaire de Valbrune'})):
            code, rep = self.appel('dossiers/%s/%s' % (d['id'], route), corps, owner=CAMILLE, role='avocat')
            self.assertEqual(code, '404', route)
            self.assertEqual(rep, absent[1], route)
            self.assertNotIn('KERVILAC', str(rep))
        with self.assertRaises(Stop) as ctx:
            F.parties(self.desk, (CAMILLE, 'avocat'), d['id'])
        self.assertEqual(str(ctx.exception), 'introuvable')
        self.assertEqual(self.desk.db.execute('SELECT COUNT(*) FROM dossier_parties5697 WHERE dossier_id=?', (d['id'],)).fetchone()[0], 0)
        self.assertEqual(self.appel('dossiers/%s/parties' % d['id'], owner=CLAIRE, role='avocat')[0], '200')

    def test_roles_des_routes(self):
        from agent.dossiers_web5697 import allowed
        for role in ('administrateur', 'avocat', 'assistant'):
            self.assertTrue(allowed(role, 'POST', 'dossiers/x/parties'))
            self.assertTrue(allowed(role, 'POST', 'dossiers/x/parties/p'))
            self.assertTrue(allowed(role, 'POST', 'dossiers/x/instances/i/retirer'))
            self.assertTrue(allowed(role, 'GET', 'dossiers/x/fichiers'))
        self.assertFalse(allowed('invite', 'POST', 'dossiers/x/parties'))
        self.assertFalse(allowed('avocat', 'POST', 'dossiers/x/parties/p/supprimer'))
        self.assertFalse(allowed('avocat', 'POST', 'dossiers/x/fichiers'))
        self.assertFalse(allowed('avocat', 'PATCH', 'dossiers/x/parties/p'))
        code, rep = self.appel('dossiers/x/parties', {'cle': uuid.uuid4().hex, 'nom': 'ZORVAL', 'role': 'adverse'}, owner='invite@example.test', role='invite')
        self.assertEqual(code, '403')

    def test_sans_ia_et_en_pause(self):
        self.desk.setting('missions567:pause', True)
        d = self.dossier(titre='ORLANDEC', client='ORLANDEC', juridiction='Tribunal judiciaire de Valbrune')
        self.desk.setting('pause5629:matters', {d['id']: {'at': 'x'}})
        bloque = AssertionError('appel au modèle ou au réseau')
        with patch('agent.ai_gateway.estimate_and_check_budget', side_effect=bloque), patch('agent.common.HTTP.request', side_effect=bloque):
            self.assertEqual(self.appel('dossiers/%s/parties' % d['id'], {'cle': uuid.uuid4().hex, 'nom': 'ZORVAL', 'role': 'adverse'},
                                        owner=CLAIRE, role='avocat')[0], '201')
            inst = F.instances(self.desk, AVOCATE, d['id'])['instances'][0]
            self.assertEqual(self.appel('dossiers/%s/instances/%s' % (d['id'], inst['id']), {
                'cle': uuid.uuid4().hex, 'version': inst['version'], 'version_dossier': inst['version_dossier'], 'juridiction': inst['juridiction'],
                'rg': '24/01234', 'principale': '1'}, owner=CLAIRE, role='avocat')[0], '200')
            for vue in ('parties', 'juridiction', 'fichiers'):
                self.assertNotIn('d79-warn', self.page(d['id'], vue=vue), vue)
            self.assertNotIn('d79-warn', self.page(d['id'], vue='fichiers', sous='repertoire'))


# ------------------------------------------------------------------------------------------------ fichiers
class Fichiers(Fiche):
    def test_arborescence_bornee_et_paginee(self):
        d = self.dossier(titre='DURAND c/ MARTIN')
        base = d['stockage']['chemin']
        for i in range(40):
            self.stock.ajouter('%s/CORRESPONDANCES/Courrier %02d.pdf' % (base, i), 2048)
        self.stock.ajouter(base + '/NOS PIECES/Piece 01.pdf', 3 * 1024 * 1024, 'Tue, 03 Nov 2026 09:00:00 GMT')
        for i in range(30):
            self.stock.repertoires.add('%s/ZLOT %02d' % (base, i))
        for i in range(25):
            self.stock.ajouter('%s/Note %02d.docx' % (base, i))
        t = F.arborescence(self.desk, base)
        self.assertEqual(t['etat'], 'ok')
        self.assertEqual(t['total'], len(self.stock.list_folder(base)))
        self.assertEqual(t['pages'], 2)
        self.assertEqual(len(t['dossiers']) + len(t['fichiers']), F.PAR_PAGE)
        corr = next(x for x in t['dossiers'] if x['nom'] == 'CORRESPONDANCES')
        self.assertEqual((corr['fichiers'], corr['taille']), (40, 40 * 2048))
        self.assertEqual(next(x for x in t['dossiers'] if x['nom'] == 'NOS PIECES')['modifie'], '2026-11-03')
        self.assertEqual(sum(1 for x in t['dossiers'] if x['fichiers'] is not None), F.MAX_COMPTES)   # comptage borné
        self.assertEqual(t['non_comptes'], len(t['dossiers']) - F.MAX_COMPTES)
        suite = F.arborescence(self.desk, base, '', 2)
        self.assertEqual(len(suite['dossiers']) + len(suite['fichiers']), t['total'] - F.PAR_PAGE)
        sous = F.arborescence(self.desk, base, 'CORRESPONDANCES')
        self.assertEqual((len(sous['fichiers']), sous['fil'][0]['nom']), (40, 'CORRESPONDANCES'))
        html = self.page(d['id'], vue='fichiers', sous='repertoire', rep='CORRESPONDANCES')
        self.assertIn('Courrier 07.pdf', html)
        self.assertIn('data-d79-doc=', html)
        self.assertIn('aria-selected="true"', html)

    def test_chemins_refuses_et_profondeur(self):
        d = self.dossier(titre='ZORVAL')
        base = d['stockage']['chemin']
        avant = self.stock.lectures
        for rel in ('..', '../DEMO', 'a/../..', '.cache', 'a\\b', 'a/%2e%2e', '/'.join(['n'] * (F.MAX_PROFONDEUR + 1))):
            t = F.arborescence(self.desk, base, rel)
            self.assertEqual((t['etat'], t['dossiers'], t['fichiers']), ('refuse', [], []), rel)
        self.assertEqual(self.stock.lectures, avant)                          # aucune lecture du stockage hors du dossier
        self.assertEqual(F.arborescence(self.desk, '/Ailleurs/Secret')['etat'], 'refuse')   # hors des racines autorisées
        html = self.page(d['id'], vue='fichiers', sous='repertoire', rep='../DEMO')
        self.assertIn('Emplacement refusé', html)

    def test_hors_ligne_jamais_vide_et_sans_repertoire(self):
        d = self.dossier(titre='DURAND')
        self.stock.hors_ligne = True
        t = F.arborescence(self.desk, d['stockage']['chemin'])
        self.assertEqual(t['etat'], 'hors_ligne')
        self.assertIn('ce n’est pas un répertoire vide', t['motif'])
        html = self.page(d['id'], vue='fichiers', sous='repertoire')
        self.assertIn('Stockage injoignable', html)
        self.assertNotIn('Ce répertoire est vide', html)
        self.assertEqual(F.arborescence(self.desk, '')['etat'], 'sans_repertoire')
        # rangement pas encore fait : l'onglet le dit et propose « Reprendre le rangement »
        recu = self.creer(titre='KERVILAC')
        html = self.page(recu['id'], vue='fichiers', sous='repertoire')
        self.assertIn('Reprendre le rangement', html)
        self.assertIn('m57/dossiers/%s/rangement' % recu['id'], html)
        code, rep = self.appel('dossiers/%s/fichiers' % recu['id'])
        self.assertEqual((code, rep['etat']), ('200', 'en_preparation'))


# ------------------------------------------------------------------------------------------------ pages
class Pages(Fiche):
    def test_onglets_ordre_alias_et_rendu(self):
        from agent import dossier5679
        self.assertEqual([k for k, _ in dossier5679.TABS][:4], ['synthese', 'parties', 'juridiction', 'fichiers'])
        d = self.dossier(titre='DURAND c/ MARTIN', client='DURAND', juridiction='Tribunal judiciaire de Valbrune', rg='24/01234')
        alias = self.page(d['id'], vue='documents')
        self.assertIn('aria-current="page">Fichiers</a>', alias)
        self.assertIn('Documents du dossier', alias)                          # « Par type » : classement existant, inchangé
        self.assertIn('vue=fichiers&amp;sous=repertoire', alias)
        parties = self.page(d['id'], vue='parties')
        for attendu in ('aria-current="page">Parties</a>', 'Clients', 'Parties adverses', 'Avocats', 'Autres intervenants', 'Ajouter une partie adverse',
                        'data-d97-api="m57/dossiers/%s/parties"' % d['id'], 'name="cle"', 'name="version"', 'data-d98-confirmer'):
            self.assertIn(attendu, parties)
        jur = self.page(d['id'], vue='juridiction')
        for attendu in ('aria-current="page">Juridiction</a>', 'Tribunal judiciaire de Valbrune', '24/01234', 'principale', 'Prochaine audience',
                        'Derniers actes', 'name="version_dossier"', 'Ajouter une instance'):
            self.assertIn(attendu, jur)
        for html in (alias, parties, jur):
            self.assertNotIn('d79-warn', html)
            self.assertIn('/static/v5698-fiche.js', html)
            self.assertNotIn('<script>', html)

    def test_dossier_hors_registre_en_lecture(self):
        html = self.page('DOS-001', vue='parties')                             # dossier JSON pas encore importé
        self.assertIn('pas encore inscrit au registre unique', html)
        self.assertNotIn('d79-warn', html)
        self.assertNotIn('d79-warn', self.page('DOS-001', vue='juridiction'))
        self.assertNotIn('d79-warn', self.page('DOS-001', vue='fichiers'))

    def test_nouveau_dossier_roles_groupes(self):
        from agent import dossiers_ui5697
        html = dossiers_ui5697.page_nouveau(self.desk, {'HTTP_X_AXIORHUB_USER': 'cabinet', 'HTTP_X_AXIORHUB_ROLE': 'administrateur'}, {}, '/p', {})
        for groupe in ('Clients', 'Parties adverses', 'Avocats', 'Autres intervenants'):
            self.assertIn('<optgroup label="%s">' % groupe, html)
        self.assertIn('<option value="adverse" selected>', html)
        self.assertIn('value="commissaire_justice"', html)
        self.assertNotIn('value="juridiction"', html)                        # la juridiction a son onglet

    def test_static_servi(self):
        from agent import web440
        self.assertIn('/static/v5698-fiche.js', web440.STATIC)


if __name__ == '__main__':
    unittest.main()
