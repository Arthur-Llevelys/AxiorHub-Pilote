"""5.6.109 (lot G1, G01) : préfacturation assistée sans double saisie ni double facturation (``gestion56109``, ``time500``,
``honoraires5697``, ``facturation5653``).

Une diligence vue par deux traces (rendez-vous et compte rendu) ou depuis deux postes ne donne qu'une entrée et qu'une réservation ; une
durée inconnue n'est jamais écrite zéro ; l'estimation est étiquetée et doit être confirmée ; la validation se fait par lot atomique ;
l'argent est calculé en centimes entiers avec l'arrondi commun ; une facture émise n'est jamais recalculée. Chaque garde-fou a son test de
mutation (en mémoire). Données fictives (DURAND, MARTIN, DOS-001, example.test) ; aucun réseau ni modèle.
"""
from datetime import date, datetime, timedelta, timezone
import io
import json
import sqlite3
import unittest
from unittest.mock import patch

from agent import facturation5653 as fact, gestion56109 as G, honoraires5697 as hon, time500, workplan
from agent.common import Stop
from test_v5697_gestion import ADMIN, ASSISTANT, AUTRE, AVOCAT, CLIENT, ENTITE, MID, MID2, Base

JOUR = '2026-11-10'
ORIGINE, JETON = 'https://pilote.example.test', 'jeton-csrf-fictif'


class Socle(Base):
    def setUp(self):
        super().setUp()
        workplan.ensure_schema(self.desk)
        time500.set_terms(self.desk, MID, 'horaire', '200')
        fact.save_entity(self.desk, *ADMIN, ENTITE)
        fact.save_client(self.desk, *AVOCAT, MID, CLIENT)

    def rdv(self, ident='evt-fictif-1', jour=JOUR, debut='10:00', fin='11:00', mid=MID, titre='Rendez-vous DURAND (fictif)'):
        self.desk.db.execute('INSERT INTO calendar_cache(id,uid,recurrence_id,calendar_url,href,etag,title,description,location,starts,ends,busy,matter,'
                             "matter_candidates,fetched) VALUES(?,?,'','cal','h','e',?,'','',?,?,1,?,'[]','')",
                             (ident, ident, titre, '%sT%s:00+00:00' % (jour, debut), '%sT%s:00+00:00' % (jour, fin), mid))
        self.desk.db.commit()

    def compte_rendu(self, ident='evt-fictif-1', mid=MID):
        self.desk.db.execute("INSERT INTO meeting500_reports VALUES(?,?,?,?,?,?)", (ident, mid, 'Compte rendu fictif', 'brouillon', JOUR + 'T12:00:00+00:00',
                                                                                  JOUR + 'T12:00:00+00:00'))
        self.desk.db.commit()

    def collecter(self, acteur=AVOCAT, **plus):
        return G.collecter(self.desk, acteur, {'du': '2026-11-01', 'au': '2026-11-20', **plus})

    def lignes(self, acteur=AVOCAT, **f):
        return G.prefacturation(self.desk, acteur, f)['lignes']

    def trace(self, source, minutes='', acteur=AVOCAT, **plus):
        d = {'dossier': MID, 'nature': 'appel', 'source': source, 'jour': JOUR, 'minutes': minutes, 'libelle': 'Appel du client (fictif)'}
        d.update(plus)
        return G.signaler_trace(self.desk, acteur, d)['diligence']

    def ligne(self, did):
        return dict(self.desk.db.execute('SELECT * FROM prefact_diligences56109 WHERE id=?', (did,)).fetchone())

    def valider(self, lignes, cle='lot-essai-0001', acteur=AVOCAT):
        return G.valider_lot(self.desk, acteur, {'cle': cle, 'lignes': lignes})

    def entrees(self):
        return self.desk.db.execute('SELECT COUNT(*) FROM time500_entries').fetchone()[0]


class G01Rapprochement(Socle):
    def test_G01_deux_traces_deux_postes_une_entree_une_reservation(self):
        self.rdv()
        self.compte_rendu()
        self.collecter()
        self.collecter()                                                       # même relevé rejoué (ou autre poste) : rien de plus
        ls = self.lignes()
        self.assertEqual(len(ls), 1)
        self.assertEqual(sorted(ls[0]['traces']), ['cr|evt-fictif-1', 'rdv|evt-fictif-1'])
        time500.estimate(self.desk, MID, '2026-11-01', '2026-11-20')           # l'ancienne estimation propose aussi ce rendez-vous
        l = ls[0]
        recu = self.valider([{'id': l['id'], 'version': l['version'], 'confirmer': True}])
        self.assertEqual(recu['nombre'], 1)
        rejeu = self.valider([{'id': l['id'], 'version': l['version'], 'confirmer': True}])
        self.assertTrue(rejeu['rejoue'])
        self.assertEqual(rejeu['validees'], recu['validees'])                  # même reçu
        with self.assertRaises(G.Refus) as ctx:                                # autre poste, autre clé : conflit explicite
            self.valider([{'id': l['id'], 'version': l['version'], 'confirmer': True}], cle='lot-autre-poste-01')
        self.assertEqual(str(ctx.exception), 'diligence_deja_validee')
        for p in self.desk.db.execute('SELECT id FROM time500_proposals').fetchall():   # l'ancien chemin ne la valide pas une seconde fois
            with self.assertRaises(Stop):
                time500.validate(self.desk, p[0], 60, 'Rendez-vous', None, AVOCAT[0])
        self.assertEqual(self.entrees(), 1)
        hon.preparer_brouillon(self.desk, AVOCAT, MID, {'cle': 'brouillon-essai-0001'})
        self.assertEqual(self.desk.db.execute("SELECT COUNT(*) FROM b53_allocations WHERE source_kind='temps'").fetchone()[0], 1)
        with self.assertRaisesRegex(Stop, 'rien_a_facturer'):
            hon.preparer_brouillon(self.desk, AVOCAT, MID, {'cle': 'brouillon-essai-0002'})

    def test_G01_mutation_sans_la_garde_l_ancien_chemin_compterait_deux_fois(self):
        self.rdv()
        self.collecter()
        time500.estimate(self.desk, MID, '2026-11-01', '2026-11-20')
        l = self.lignes()[0]
        with patch.object(G, 'refs_validees', lambda desk, refs: set()):     # garde retirée (en mémoire)
            self.valider([{'id': l['id'], 'version': l['version'], 'confirmer': True}])
            pid = self.desk.db.execute("SELECT id FROM time500_proposals WHERE status='a_valider'").fetchone()[0]
            time500.validate(self.desk, pid, 60, 'Rendez-vous', None, AVOCAT[0])
        self.assertEqual(self.entrees(), 2)                                    # double comptage : la garde est nécessaire

    def test_G01_saisie_par_l_ancien_chemin_n_est_pas_reproposee(self):
        self.rdv()
        time500.estimate(self.desk, MID, '2026-11-01', '2026-11-20')
        pid = self.desk.db.execute("SELECT id FROM time500_proposals WHERE status='a_valider'").fetchone()[0]
        time500.validate(self.desk, pid, 60, 'Rendez-vous', None, AVOCAT[0])
        self.collecter()
        self.assertEqual(self.lignes(), [])                                    # déjà saisie : pas de seconde ligne
        self.assertEqual([x['statut'] for x in self.lignes(tout='1')], ['deja_saisie'])

    def test_G01_chevauchement_exige_un_choix_puis_fusion_sans_addition(self):
        a = self.trace('tel-0001', '30', debut=JOUR + 'T09:00:00+00:00', fin=JOUR + 'T09:30:00+00:00', auteur=AVOCAT[0])
        b = self.trace('tel-0002', '20', debut=JOUR + 'T09:10:00+00:00', fin=JOUR + 'T09:30:00+00:00', auteur=AVOCAT[0])
        self.assertEqual({self.ligne(a)['statut'], self.ligne(b)['statut']}, {'chevauchement_a_verifier'})
        with self.assertRaises(G.Refus) as ctx:
            self.valider([{'id': a, 'version': self.ligne(a)['version']}])
        self.assertIn('Chevauchement', ctx.exception.detail[a])
        G.rapprocher(self.desk, AVOCAT, {'id': b, 'avec': a, 'decision': 'fusionner', 'version': self.ligne(b)['version']})
        self.assertEqual(self.ligne(b)['statut'], 'fusionnee')
        self.assertEqual(self.ligne(a)['statut'], 'a_valider')
        self.valider([{'id': a, 'version': self.ligne(a)['version']}])
        self.assertEqual(self.entrees(), 1)
        self.assertEqual(self.desk.db.execute('SELECT minutes FROM time500_entries').fetchone()[0], 30)   # aucune addition 30 + 20

    def test_G01_travaux_distincts_consignes(self):
        a = self.trace('tel-0011', '30', debut=JOUR + 'T09:00:00+00:00', fin=JOUR + 'T09:30:00+00:00', auteur=AVOCAT[0])
        b = self.trace('tel-0012', '20', debut=JOUR + 'T09:10:00+00:00', fin=JOUR + 'T09:30:00+00:00', auteur=AVOCAT[0])
        G.rapprocher(self.desk, AVOCAT, {'id': a, 'avec': b, 'decision': 'distinct', 'version': self.ligne(a)['version']})
        self.valider([{'id': a, 'version': self.ligne(a)['version']}, {'id': b, 'version': self.ligne(b)['version']}])
        self.assertEqual(self.entrees(), 2)


class G01Durees(Socle):
    def test_G01_duree_inconnue_jamais_zero(self):
        did = self.trace('tel-0100')
        l = self.ligne(did)
        self.assertIsNone(l['minutes'])
        self.assertEqual((l['origine_duree'], l['statut']), ('inconnue', 'a_completer'))
        with self.assertRaises(G.Refus) as ctx:
            self.valider([{'id': did, 'version': l['version']}])
        self.assertIn('Durée inconnue', ctx.exception.detail[did])
        self.assertEqual(self.entrees(), 0)
        self.assertEqual(self.desk.db.execute('SELECT COUNT(*) FROM prefact_diligences56109 WHERE minutes=0').fetchone()[0], 0)
        self.assertEqual(self.lignes()[0]['nature_montant'], 'a_completer')
        G.completer(self.desk, AVOCAT, {'id': did, 'version': l['version'], 'minutes': '25'})
        self.valider([{'id': did, 'version': self.ligne(did)['version']}])
        self.assertEqual(self.desk.db.execute('SELECT minutes FROM time500_entries').fetchone()[0], 25)

    def test_G01_mutation_le_schema_refuse_zero_et_une_inconnue_chiffree(self):
        now = '2026-11-10T00:00:00+00:00'
        for minutes, origine in ((0, 'saisie'), (15, 'inconnue')):
            with self.assertRaises(sqlite3.IntegrityError):
                self.desk.db.execute("INSERT INTO prefact_diligences56109(id,matter,source_kind,source_id,jour,minutes,origine_duree,statut,cree_le,modifie_le) "
                                     "VALUES(?,?,'appel',?,?,?,?,'a_valider',?,?)", ('dil-x%d' % minutes, MID, 'x%d' % minutes, JOUR, minutes, origine, now, now))
        self.desk.db.rollback()

    def test_G01_estimation_etiquetee_et_confirmation_exigee(self):
        self.rdv(debut='14:00', fin='14:50')
        self.collecter()
        l = self.lignes()[0]
        self.assertEqual((l['origine'], l['origine_libelle'], l['nature_montant']), ('estimee', 'estimée', 'estime'))
        self.assertTrue(any('estimée' in a and 'pas mesurée' in a for a in l['alertes']))
        with self.assertRaises(G.Refus) as ctx:
            self.valider([{'id': l['id'], 'version': l['version']}])
        self.assertIn('estimée', ctx.exception.detail[l['id']])
        out = self.valider([{'id': l['id'], 'version': l['version'], 'confirmer': True}])
        self.assertEqual(out['validees'][0]['minutes'], 50)
        self.assertEqual(json.loads(self.ligne(l['id'])['convention'])['origine_duree'], 'estimee')   # l'origine reste lisible après validation


class G01Lot(Socle):
    def test_G01_lot_atomique_rien_avant_une_erreur(self):
        a, b, c = self.trace('tel-0201', '30'), self.trace('tel-0202', '45'), self.trace('tel-0203')
        with self.assertRaises(G.Refus):
            self.valider([{'id': x, 'version': self.ligne(x)['version']} for x in (a, b, c)])
        self.assertEqual(self.entrees(), 0)
        self.assertEqual(self.ligne(a)['statut'], 'a_valider')

    def test_G01_mutation_panne_au_milieu_du_lot_tout_est_annule(self):
        a, b = self.trace('tel-0301', '30'), self.trace('tel-0302', '45')
        vrai, n = time500._insert_entry, []

        def panne(*args, **kw):
            n.append(1)
            if len(n) == 2:
                raise sqlite3.OperationalError('disque plein (simulé)')
            return vrai(*args, **kw)
        with patch.object(time500, '_insert_entry', panne):
            with self.assertRaises(sqlite3.OperationalError):
                self.valider([{'id': x, 'version': self.ligne(x)['version']} for x in (a, b)])
        self.assertEqual(self.entrees(), 0)                                    # la première entrée n'a pas survécu à la panne
        self.assertEqual(self.ligne(a)['statut'], 'a_valider')
        self.assertIsNone(self.desk.db.execute("SELECT 1 FROM gestion_recus56109").fetchone())   # aucun reçu sans effet

    def test_G01_version_perimee_409(self):
        did = self.trace('tel-0401', '30')
        v = self.ligne(did)['version']
        G.completer(self.desk, AVOCAT, {'id': did, 'version': v, 'minutes': '35'})
        with self.assertRaisesRegex(Stop, 'version_perimee'):
            self.valider([{'id': did, 'version': v}])


class G01Argent(Socle):
    def test_G01_arrondi_commun_moitie_superieure_sans_flottant(self):
        self.assertEqual(G.montant_temps(1, 30), 1)                            # 0,5 centime → 1 (arrondi commun)
        self.assertEqual(time500._amount(1, 30), 1)
        self.assertEqual(int(round(1 * 30 / 60.0)), 0)                         # contre-exemple : l'ancien calcul flottant donnait 0
        self.assertEqual(G.montant_temps(50, 20000), 16667)
        self.assertIsNone(G.montant_temps(None, 20000))                        # durée inconnue : pas de montant, jamais zéro
        with self.assertRaises(ValueError):
            G.montant_temps(50.0, 20000)

    def test_G01_ligne_de_facture_egale_au_temps_valide(self):
        did = self.trace('tel-0501', '50')
        self.valider([{'id': did, 'version': self.ligne(did)['version']}])
        montant = self.desk.db.execute('SELECT amount_cents FROM time500_entries').fetchone()[0]
        self.assertEqual(montant, 16667)
        b = hon.preparer_brouillon(self.desk, AVOCAT, MID, {'cle': 'brouillon-essai-0501'})
        self.assertEqual(b['snapshot']['lignes'][0]['montant_ht_cents'], montant)   # et non 166,00 € (quantité arrondie à 0,83 h)

    def test_G01_tarif_modifie_conflit_puis_montant_fige(self):
        did = self.trace('tel-0601', '50')
        vu = self.lignes()[0]['taux_cents']
        self.assertEqual(vu, 20000)
        time500.set_terms(self.desk, MID, 'horaire', '250')
        with self.assertRaises(G.Refus) as ctx:
            self.valider([{'id': did, 'version': self.ligne(did)['version'], 'taux_vu': vu}])
        self.assertEqual(str(ctx.exception), 'tarif_modifie')
        self.valider([{'id': did, 'version': self.ligne(did)['version'], 'taux_vu': 25000}])
        time500.set_terms(self.desk, MID, 'horaire', '300')
        self.assertEqual(self.desk.db.execute('SELECT amount_cents, rate_cents FROM time500_entries').fetchone()[:], (20833, 25000))   # figé

    def test_G01_forfait_non_majore(self):
        time500.set_terms(self.desk, MID, 'forfait', budget='1000')
        did = self.trace('tel-0701', '600')
        self.assertEqual(self.lignes()[0]['nature_montant'], 'forfait')
        out = self.valider([{'id': did, 'version': self.ligne(did)['version']}])
        self.assertEqual((out['validees'][0]['montant_cents'], out['validees'][0]['forfait']), (0, True))
        i = hon.indicateurs(self.desk, AVOCAT, MID)
        self.assertEqual(i['travail_a_facturer']['detail']['temps_cents'], '0.00')   # aucun supplément créé par le dépassement
        self.assertIn('pas un supplément', hon.forfait(self.desk, AVOCAT, MID)['mention'])

    def test_G01_facture_emise_jamais_recalculee(self):
        did = self.trace('tel-0801', '60')
        self.valider([{'id': did, 'version': self.ligne(did)['version']}])
        b = hon.preparer_brouillon(self.desk, AVOCAT, MID, {'cle': 'brouillon-essai-0801'})
        d = fact.issue(self.desk, *AVOCAT, fact.approve(self.desk, *AVOCAT, b['id'], b['version'])['id'], 'emission-essai-0801')
        avant = fact.get(self.desk, d['id'])['snapshot']
        time500.set_terms(self.desk, MID, 'horaire', '400')
        fact.recalculate_drafts(self.desk, *AVOCAT, MID)
        self.assertEqual(fact.get(self.desk, d['id'])['snapshot'], avant)
        with self.assertRaisesRegex(Stop, 'document_emis_non_modifiable'):
            fact.update(self.desk, *AVOCAT, d['id'], {'lignes': [{'nature': 'honoraires', 'libelle': 'X', 'quantite': '1', 'prix_unitaire_ht': '1'}]}, d['version'])

    def test_G01_mutation_sans_la_garde_une_facture_emise_serait_recalculee(self):
        did = self.trace('tel-0901', '60')
        self.valider([{'id': did, 'version': self.ligne(did)['version']}])
        b = hon.preparer_brouillon(self.desk, AVOCAT, MID, {'cle': 'brouillon-essai-0901'})
        d = fact.issue(self.desk, *AVOCAT, fact.approve(self.desk, *AVOCAT, b['id'], b['version'])['id'], 'emission-essai-0901')
        avant = fact.get(self.desk, d['id'])['snapshot']['totaux']['ht_cents']
        with patch.object(fact, 'EDITABLE', fact.EDITABLE + ('emise',)):       # garde retirée (en mémoire)
            fact.update(self.desk, *AVOCAT, d['id'], {'lignes': [{'nature': 'honoraires', 'libelle': 'X', 'quantite': '1', 'prix_unitaire_ht': '1'}]}, d['version'])
        self.assertNotEqual(fact.get(self.desk, d['id'])['snapshot']['totaux']['ht_cents'], avant)


class G01DroitsRoutes(Socle):
    def appel(self, route, data=None, acteur=AVOCAT, args=None):
        from agent import web440
        corps = json.dumps(data or {}).encode()
        env = {'HTTP_X_AXIORHUB_USER': acteur[0], 'HTTP_X_AXIORHUB_ROLE': acteur[1], 'HTTP_ORIGIN': ORIGINE, 'HTTP_X_CSRF_TOKEN': JETON,
               'CONTENT_TYPE': 'application/json', 'CONTENT_LENGTH': str(len(corps)), 'wsgi.input': io.BytesIO(corps),
               'REQUEST_METHOD': 'GET' if data is None else 'POST'}
        out = web440.api(env, self.desk, {'origin': ORIGINE, 'csrf': JETON}, '/p', 'm56109/gestion/' + route, args or {}, env['REQUEST_METHOD'])
        self.assertIn(('Cache-Control', 'private, no-store'), out.get('headers', []))
        return out['status'][:3], json.loads(out['body'])

    def test_G01_routes_formulaire_du_lot_et_409(self):
        st, out = self.appel('prefacturation/trace', {'cle': 'trace-essai-0001', 'dossier': MID, 'nature': 'appel', 'source': 'tel-1001', 'jour': JOUR,
                                                       'minutes': '20', 'libelle': 'Appel (fictif)'})
        self.assertEqual(st, '200')
        did = out['diligence']
        v = self.ligne(did)['version']
        st, out = self.appel('prefacturation/valider-lot', {'cle': 'lot-route-0001', 'sel': {did: True}, 'ver': {did: str(v)}, 'min': {did: ''}})
        self.assertEqual((st, out['nombre']), ('200', 1))
        st, out = self.appel('prefacturation/valider-lot', {'cle': 'lot-route-0002', 'sel': {did: True}, 'ver': {did: str(v)}})
        self.assertEqual(st, '409')
        st, out = self.appel('prefacturation/valider-lot', {'cle': 'lot-route-0001', 'sel': {did: True}, 'ver': {did: str(v + 5)}})
        self.assertEqual(st, '409')                                            # même clé, autre contenu : conflit
        self.assertEqual(out['error'], 'cle_idempotence_deja_utilisee')

    def test_G01_dossier_reserve_et_absent_meme_404_et_droits(self):
        for mid in (MID2, 'DOS-999'):
            st, out = self.appel('prefacturation', None, AUTRE, {'dossier': mid})
            self.assertEqual((st, out['error']), ('404', 'introuvable'))
        st, _ = self.appel('prefacturation/trace', {'cle': 'trace-essai-0002', 'dossier': MID, 'nature': 'appel', 'source': 'tel-1002', 'jour': JOUR,
                                                     'libelle': 'Appel'}, ASSISTANT)
        self.assertEqual(st, '403')                                            # sans le droit « facturation »
        from agent import standalone_auth
        self.assertTrue(standalone_auth.allowed('assistant', 'POST', '/api440/m56109/gestion/prefacturation/valider-lot'))
        self.assertFalse(standalone_auth.allowed('assistant', 'POST', '/api440/m56109/gestion/finances/coordonnees/verifier'))
        self.assertTrue(self.appel('prefacturation', None, ASSISTANT)[1]['voir_montants'])   # l'assistant lit (droit « voir les honoraires »)

    def test_G01_section_inseree_dans_temps_et_honoraires_sans_ecriture(self):
        self.trace('tel-1101', '', libelle='Appel <script>alert(1)</script> (fictif)')
        from agent import web440
        env = {'HTTP_X_AXIORHUB_USER': AVOCAT[0], 'HTTP_X_AXIORHUB_ROLE': AVOCAT[1]}
        vu = []
        habille = G.habiller(self.desk, env, '/p', '/honoraires', {}, lambda titre, html, *a, **k: vu.append(html) or html)
        avant = self.desk.db.total_changes
        habille('Temps et honoraires', '<h1>Temps</h1><section id="dossiers">Dossiers</section>')
        self.assertEqual(self.desk.db.total_changes, avant)                     # lecture seule
        html = vu[0]
        self.assertLess(html.index('id="g109-prefacturation"'), html.index('<section id="dossiers">'))
        self.assertIn('Durée inconnue : à compléter.', html)
        self.assertNotIn('<script>alert', html)
        self.assertIn('data-d97-api="m56109/gestion/prefacturation/valider-lot"', html)
        self.assertTrue(hasattr(web440, 'route'))

    def test_G01_ia_en_pause_aucun_modele(self):
        self.desk.setting('missions567:pause', True)
        with patch('agent.model.Model', side_effect=AssertionError('modèle instancié')), \
                patch('agent.ai_gateway.common_completion', side_effect=AssertionError('fournisseur IA appelé')):
            self.rdv()
            self.collecter()
            did = self.trace('tel-1201', '15')
            self.valider([{'id': did, 'version': self.ligne(did)['version']}])
        with open(G.__file__, encoding='utf-8') as f:
            source = f.read()
        for interdit in ('ai_gateway', 'from .model', 'import model'):
            self.assertNotIn(interdit, source)


if __name__ == '__main__':
    unittest.main()
