"""5.6.109 (lot G4, G17) : documents publics Pappers du client et de l'adversaire classés dans le dossier (``pappers_documents56109``).

Capacité « API Pappers Entreprises » dans les connexions existantes (distincte de Pappers MCP et de Pappers Justice), faux transport HTTP (aucun
appel réel, aucune clé réelle), budget de crédits borné, trois modes d'autorisation, originaux et versions conservés, jamais « pièce
communiquée », coût remonté au registre de consommation ; le site public Pappers n'est jamais navigué. SIREN d'essai seulement."""
import json
from pathlib import Path
import socket
import unittest
from unittest.mock import patch

from agent import connexions5652 as cx, crm5636, pappers_documents56109 as PP
from agent.common import Stop
import test_v5652 as base

PDF_C = b'%PDF-1.4\nstatuts fictifs DURAND\n%%EOF\n'
PDF_A = b'%PDF-1.4\nstatuts fictifs ZORVAL\n%%EOF\n'
PDF_ACTE = b'%PDF-1.4\nacte fictif\n%%EOF\n'
PDF_COMPTES = b'%PDF-1.4\ncomptes fictifs\n%%EOF\n'
CLIENT, ADVERSE = '000010017', '000010025'
ADMIN = ('admin@example.test', 'administrateur')
AVOCAT = ('avocat@example.test', 'avocat')


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


class Base(base.Base):
    def setUp(self):
        super().setUp()
        PP.ensure_schema(self.desk)
        self.revision_statuts_client = '2026-03-01'
        self.fake.on('GET', 'api.pappers.fr/v2/suivi-jetons', {'jetons_restants': 1000})
        self.fake.on('GET', 'api.pappers.fr/v2/entreprise?siren=' + CLIENT, lambda *a: {
            'nom_entreprise': 'DURAND SAS', 'derniers_statuts': {'token': 'tok-c-st-' + self.revision_statuts_client, 'date_depot': self.revision_statuts_client},
            'actes': [{'token': 'tok-c-acte', 'date_depot': '2025-06-01', 'actes': [{'type': 'Procès-verbal'}]}],
            'comptes': [{'token': 'tok-c-cpt', 'date_depot': '2025-07-01', 'date_cloture': '2024-12-31'}]})
        self.fake.on('GET', 'api.pappers.fr/v2/entreprise?siren=' + ADVERSE, {'nom_entreprise': 'ZORVAL SA', 'derniers_statuts': {'token': 'tok-a-st', 'date_depot': '2024-01-10'}})
        self.fake.on('GET', 'token=tok-c-st', lambda *a: PDF_C if self.revision_statuts_client == '2026-03-01' else PDF_C.replace(b'DURAND', b'DURAND v2'))
        self.fake.on('GET', 'token=tok-c-acte', PDF_ACTE)
        self.fake.on('GET', 'token=tok-c-cpt', PDF_COMPTES)
        self.fake.on('GET', 'token=tok-a-st', PDF_A)
        crm5636.save(self.desk, {'kind': 'societe', 'category': 'client', 'name': 'DURAND SAS', 'siren': CLIENT, 'matter': base.MID, 'role': 'client'})
        crm5636.save(self.desk, {'kind': 'societe', 'category': 'partie_adverse', 'name': 'ZORVAL SA', 'siren': ADVERSE, 'matter': base.MID, 'role': 'partie_adverse'})
        PP.enregistrer_politique(self.desk, *ADMIN, {'mode': 'demande', 'credits_recherche': '1', 'credits_document': '1', 'credits_mois': '50'})
        self.conn = self.connecter()
        self.n = 0

    def connecter(self, provider=PP.PROVIDER):
        v = cx.save(self.desk, *ADMIN, {'provider': provider, 'environment': 'production', 'api_key': 'cle-fictive-123456',
                                        'cap_entreprises_documents': True, 'cap_entreprises_telecharger': True})
        out = cx.test(self.desk, *ADMIN, v['id'])
        self.assertTrue(out['ok'], out)
        return v['id']

    def collecter(self, **kw):
        self.n += 1
        return PP.collecter(self.desk, kw.pop('acteur', AVOCAT), base.MID, kw.pop('cle', 'cle-pappers-%04d' % self.n), **kw)

    def fichiers(self):
        racine = self.root / 'Dossiers' / 'DEMO'
        return sorted(str(p.relative_to(racine)).replace('\\', '/') for p in racine.rglob('*') if p.is_file() and p.suffix == '.pdf' and p.name != 'Convention.pdf')

    def appels_api(self):
        return [c for c in self.fake.calls if 'api.pappers.fr' in c[1]]


for _nom in [n for n in dir(Base) if n.startswith('test')]:
    setattr(Base, _nom, None)          # socle seulement : les tests 5.6.52 ne sont pas rejoués ici


class G17Capacite(Base):
    def test_G17_capacite_distincte_de_pappers_mcp_et_justice(self):
        spec = cx._spec(PP.PROVIDER)
        self.assertEqual(set(spec['caps']), {PP.CAP_LISTE, PP.CAP_FICHIER})
        self.assertNotIn(PP.PROVIDER, cx.TOOLS.values())
        self.assertNotIn(PP.CAP_LISTE, {c for c, _ in cx.TOOLS.values()})      # aucun outil de l'agent n'appelle l'API directement
        self.assertIsInstance(cx.adapter(self.desk, cx._row(self.desk, self.conn)), PP.PappersEntreprises)
        cx.disconnect(self.desk, *ADMIN, self.conn)
        self.connecter('pappers_justice')                                        # Pappers Justice vérifié : n'atteste pas la collecte
        self.assertEqual(self.collecter()['etat'], 'capacite_a_qualifier')
        self.assertIn('Pappers Entreprises (API)', cx.settings_html(self.desk, '', *ADMIN))

    def test_G17_cle_dans_l_en_tete_jamais_dans_l_adresse(self):
        self.collecter()
        calls = self.appels_api()
        self.assertTrue(calls)
        self.assertTrue(all('cle-fictive' not in url for _, url, _, _ in calls))
        self.assertTrue(all(h.get('api-key') == 'cle-fictive-123456' for _, _, _, h in calls))
        self.assertTrue(all(url.startswith('https://api.pappers.fr/v2/') for _, url, _, _ in calls))


class G17Collecte(Base):
    def test_G17_client_et_adversaire_classes_jamais_piece_communiquee(self):
        out = self.collecter()
        self.assertEqual(out['etat'], 'terminee', out)
        self.assertEqual(len(out['classes']), 4)
        self.assertTrue(all(f.startswith(PP.DOSSIER_DOCUMENTS + '/') for f in self.fichiers()))
        self.assertFalse(any('PIECES ADVERSES' in f or 'NOS PIECES' in f for f in self.fichiers()))
        docs = PP.documents(self.desk, AVOCAT, base.MID)
        self.assertEqual({d['piece_communiquee'] for d in docs}, {0})
        self.assertEqual({(d['siren'], d['role_societe']) for d in docs}, {(CLIENT, 'client'), (ADVERSE, 'adversaire')})
        adverse = next(d for d in docs if d['siren'] == ADVERSE)
        self.assertEqual((self.root / adverse['chemin'].lstrip('/')).read_bytes(), PDF_A)          # original exact
        self.assertTrue(all(c['categorie'] == 'documents_publics_societes' for c in out['classes']))
        self.assertFalse(out['piece_communiquee'])

    def test_G17_versions_conservees_et_changement_montre(self):
        self.collecter()
        avant = self.fichiers()
        n_appels = len(self.appels_api())
        again = self.collecter()                                                 # rien de nouveau : aucun téléchargement payant
        self.assertEqual((again['etat'], len(again['classes'])), ('aucun_nouveau_document_selectionne', 0))
        self.assertEqual(len([c for c in self.appels_api()[n_appels:] if 'telechargement' in c[1]]), 0)
        self.revision_statuts_client = '2026-10-05'
        nouveau = self.collecter()
        self.assertEqual(len(nouveau['classes']), 1)
        self.assertEqual(nouveau['versions_nouvelles'], [{'siren': CLIENT, 'reference': 'statuts', 'ancienne': '2026-03-01', 'nouvelle': '2026-10-05'}])
        apres = self.fichiers()
        self.assertTrue(set(avant) < set(apres))                                 # l'ancienne version reste
        revs = [d['revision'] for d in PP.documents(self.desk, AVOCAT, base.MID) if d['siren'] == CLIENT and d['reference'] == 'statuts']
        self.assertEqual(sorted(revs), ['2026-03-01', '2026-10-05'])

    def test_G17_faux_pdf_refuse(self):
        self.fake.on('GET', 'token=tok-a-st', b'<html>connexion</html>')
        out = self.collecter()
        self.assertTrue(any(x['code'] == 'document_pdf_non_recu' for x in out['a_verifier']))
        self.assertFalse(any('ZORVAL' in f for f in self.fichiers()))
        self.assertEqual(out['etat'], 'partielle')

    def test_G17_cout_au_registre_de_consommation(self):
        self.collecter()
        from agent.budget_ledger5689 import connect
        db = connect(self.desk.c['state_dir'])
        try:
            rows = db.execute("SELECT unit_code, provider, quantite_milli, etat FROM budget_quantites56109").fetchall()
        finally:
            db.close()
        self.assertEqual({(r[0], r[1]) for r in rows}, {('credit_pappers', 'pappers')})
        self.assertEqual(sum(r[2] for r in rows), 6000)                           # 2 recherches + 4 documents, 1 crédit chacun

    def test_G17_reprise_meme_cle_meme_resultat(self):
        a = self.collecter(cle='cle-pappers-unique')
        n = len(self.appels_api())
        b = self.collecter(cle='cle-pappers-unique')
        self.assertTrue(b['rejoue'])
        self.assertEqual(len(self.appels_api()), n)
        self.assertEqual(len(a['classes']), len(b['classes']))


class G17Budget(Base):
    def test_G17_budget_de_credits_borne_aucun_appel_au_dela(self):
        PP.enregistrer_politique(self.desk, *ADMIN, {'credits_mois': '3'})
        out = self.collecter()
        self.assertEqual(out['etat'], 'budget_pappers_depasse')
        self.assertLessEqual(PP.credits_engages(self.desk), 3)
        self.assertEqual(len([c for c in self.appels_api() if 'telechargement' in c[1]]), 0)   # le plan refuse avant tout téléchargement

    def test_G17_mutation_reservation_retiree_le_budget_serait_depasse(self):
        """Mutation en mémoire : sans réservation bornée, les crédits engagés dépasseraient le plafond — la réservation est la garde."""
        PP.enregistrer_politique(self.desk, *ADMIN, {'credits_mois': '4'})
        def reserver_sans_garde(desk, dossier, siren, nature, credits, reference='', autorisation=''):
            desk.db.execute("INSERT INTO pappers_appels56109(call_id,dossier,siren,nature,credits_reserves,etat,mois,at) VALUES(?,?,?,?,?,'reserve',?,?)",
                            ('x-%s-%s' % (siren, reference), dossier, siren, nature, str(credits), PP._mois(desk), PP._now()))
            desk.db.commit()
            return 'x-%s-%s' % (siren, reference)
        with patch.object(PP, 'reserver', reserver_sans_garde), patch.object(PP, '_disponible', lambda *a: '1000'):
            self.collecter()
        self.assertGreater(PP.credits_engages(self.desk), 4)

    def test_G17_reservation_atomique_refuse_au_plafond(self):
        PP.enregistrer_politique(self.desk, *ADMIN, {'credits_mois': '2'})
        PP.reserver(self.desk, base.MID, CLIENT, 'recherche', '1')
        PP.reserver(self.desk, base.MID, CLIENT, 'recherche', '1')
        with self.assertRaisesRegex(Stop, 'budget_pappers_depasse'):
            PP.reserver(self.desk, base.MID, CLIENT, 'recherche', '1')

    def test_G17_plafond_ou_tarif_inconnus_aucune_collecte(self):
        PP.enregistrer_politique(self.desk, *ADMIN, {'credits_mois': ''})
        self.assertEqual(self.collecter()['etat'], 'plafond_credits_a_definir')
        PP.enregistrer_politique(self.desk, *ADMIN, {'credits_mois': '50', 'credits_document': ''})
        self.assertEqual(self.collecter()['etat'], 'cout_a_preciser')
        self.assertEqual(len([c for c in self.appels_api() if 'telechargement' in c[1]]), 0)

    def test_G17_plafond_du_dossier_restreint(self):
        PP.restreindre_dossier(self.desk, AVOCAT, base.MID, 'demande', '3')
        out = self.collecter()
        self.assertEqual(out['etat'], 'budget_pappers_depasse')


class G17Modes(Base):
    def test_G17_trois_modes_respectes(self):
        for mode in ('demande', 'proposer'):
            PP.enregistrer_politique(self.desk, *ADMIN, {'mode': mode})
            out = self.collecter(declenchement='agent')
            self.assertEqual(out['etat'], 'a_autoriser', mode)
        self.assertEqual(len([c for c in self.appels_api() if 'telechargement' in c[1]]), 0)
        PP.enregistrer_politique(self.desk, *ADMIN, {'mode': 'automatique'})
        self.assertEqual(self.collecter(declenchement='agent')['etat'], 'a_autoriser')          # automatique sans autorisation bornée : rien
        aut = PP.autoriser(self.desk, AVOCAT, base.MID, [ADVERSE], ['statuts'], 2, '5')
        out = self.collecter(declenchement='agent', autorisation=aut)
        self.assertEqual([(c['siren'], c['type']) for c in out['classes']], [(ADVERSE, 'statuts')])   # périmètre de l'autorisation seulement

    def test_G17_mode_proposer_initiative_sans_telechargement(self):
        PP.enregistrer_politique(self.desk, *ADMIN, {'mode': 'proposer'})
        aut = PP.autoriser(self.desk, AVOCAT, base.MID, [CLIENT], ['statuts'], 2, '5')
        out = self.collecter(declenchement='agent', autorisation=aut)
        self.assertEqual(out['etat'], 'a_autoriser')
        self.assertTrue(out['proposes'])
        self.assertEqual(len([c for c in self.appels_api() if 'telechargement' in c[1]]), 0)

    def test_G17_dossier_restreint_la_regle_du_cabinet(self):
        PP.enregistrer_politique(self.desk, *ADMIN, {'mode': 'automatique'})
        PP.restreindre_dossier(self.desk, AVOCAT, base.MID, 'demande')
        aut = PP.autoriser(self.desk, AVOCAT, base.MID, [CLIENT], ['statuts'], 2, '5')
        self.assertEqual(self.collecter(declenchement='agent', autorisation=aut)['etat'], 'a_autoriser')

    def test_G17_ordre_de_l_avocat_sur_demande(self):
        self.assertEqual(self.collecter(declenchement='utilisateur')['etat'], 'terminee')

    def test_G17_pause_suspend_l_initiative_pas_l_ordre_manuel(self):
        PP.enregistrer_politique(self.desk, *ADMIN, {'mode': 'automatique'})
        aut = PP.autoriser(self.desk, AVOCAT, base.MID, [CLIENT], ['statuts'], 2, '5')
        self.desk.setting('missions567:pause', True)
        self.assertEqual(self.collecter(declenchement='agent', autorisation=aut)['etat'], 'suspendue')
        self.assertEqual(self.collecter()['etat'], 'terminee')


class G17Gardes(Base):
    def test_G17_role_contradictoire_question_sans_bloquer_les_autres(self):
        crm5636.save(self.desk, {'kind': 'societe', 'category': 'client', 'name': 'ZORVAL SA (bis)', 'siren': ADVERSE, 'matter': base.MID, 'role': 'client'})
        out = self.collecter()
        self.assertTrue(any(q.get('siren') == ADVERSE for q in out['questions']))
        self.assertEqual({c['siren'] for c in out['classes']}, {CLIENT})

    def test_G17_site_public_jamais_navigue_meme_apres_un_refus(self):
        from agent import navigateur5673
        self.fake.on('GET', 'api.pappers.fr/v2/entreprise', {}, status=403)
        with patch.object(navigateur5673, 'manager', side_effect=AssertionError('navigation interdite')), \
                patch.object(navigateur5673.Manager, 'start', side_effect=AssertionError('navigation interdite')):
            out = self.collecter()
        self.assertTrue(all(e['code'] == 'http_403' for e in out['erreurs']))
        self.assertEqual(self.fichiers(), [])
        source = Path(PP.__file__).read_text(encoding='utf-8')
        self.assertNotIn('navigateur5673', source.replace('n\'importe pas le navigateur', ''))
        self.assertNotIn('www.pappers.fr', [c[1].split('/')[2] for c in self.fake.calls])

    def test_G17_local_strict_bloque(self):
        avant = len(self.appels_api())
        with patch.object(PP, '_local_strict', lambda desk: True):
            self.assertEqual(self.collecter()['etat'], 'reseau_non_autorise')
        self.assertEqual(len(self.appels_api()), avant)

    def test_G17_dossier_reserve_meme_404(self):
        from agent import acces5697
        with patch.object(acces5697, 'peut_lire_dossier', lambda *a: False):
            with self.assertRaisesRegex(Stop, 'introuvable'):
                self.collecter()

    def test_G17_assistant_ne_collecte_pas(self):
        with self.assertRaises(Stop):
            self.collecter(acteur=('assistant@example.test', 'assistant'))


if __name__ == '__main__':
    unittest.main()
