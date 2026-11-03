"""5.6.93, lot registre : recette N03 T1 à T3, N05 T1 à T3, N06 T1 à T3 du cahier 5.6.90, et réparation des données déjà enregistrées.

Données fictives (DURAND, MARTIN, ZORVAL, DOS-001, example.test), stockage simulé (``test_v5689_livrables.Stockage``) : aucun réseau, aucun modèle, aucun
service réel. Ce qui exige un vrai Nextcloud (identifiant de fichier conservé par un vrai renommage, ETag réels, panne réelle) n'est pas prouvé ici.
"""
from datetime import datetime, timedelta, timezone
from itertools import islice
import hashlib
import json
import math
import time
from unittest.mock import patch

from test_v5689_livrables import ASSISTANT, AVOCAT, MID, Base, texte_docx
from agent import livrables5689 as liv, livrables_sources5689 as sources, livrables_travaux5689 as travaux, livrables_web5689 as web, registre5693 as reg, registre_web5693 as rweb
from agent.common import Stop


def _now():
    return datetime.now(timezone.utc)


class Registre(Base):
    def setUp(self):
        super().setUp()
        liv.ensure_schema(self.desk)
        self.stats = []
        real = self.nc.stat

        def stat(path):
            self.stats.append(path)
            return real(path)
        self.nc.stat = stat

    # ------------------------------------------------------------------ journaux simulés
    def docreqs(self, start, stop, prefix='Limite'):
        base = _now() - timedelta(days=1)
        rows = []
        for i in range(start, stop):
            when = (base + timedelta(microseconds=i)).isoformat()
            rows.append(('limite-%05d' % i, MID, 'Contrat fictif', 'contrat', 'cree', '/Dossiers/DEMO/PROJETS/%s-%05d.docx' % (prefix, i), '{}', when, when))
        self.desk.db.executemany('INSERT INTO docreq520(id,matter,request,kind,status,path,result,created,updated) VALUES(?,?,?,?,?,?,?,?,?)', rows)
        self.desk.db.commit()
        return rows

    def compte(self, service='docrequest520'):
        return self.desk.db.execute("SELECT COUNT(*) FROM prepared_items5689 WHERE source_service=? AND lifecycle='current'", (service,)).fetchone()[0]

    def visible(self, path):
        return bool(self.desk.db.execute("SELECT 1 FROM prepared_items5689 WHERE path=? AND lifecycle='current'", (path,)).fetchone())

    def etat(self, sid='docrequest520'):
        return next(s for s in liv.etat_sources(self.desk) if s['id'] == sid)

    def absent(self, i, due=False):
        liv.observe(self.desk, dict(service='essai_absent', object='absent-%d' % i, version='1', matter=MID, path='/Dossiers/DEMO/PROJETS/Absent-%02d.docx' % i, title='Absent %d' % i,
                                    sha256=hashlib.sha256(str(i).encode()).hexdigest(), state='partial', when=self.desk.now()))
        self.desk.db.commit()
        if due:        # absent déjà constaté, dont l'échéance est arrivée
            self.desk.db.execute("UPDATE prepared_items5689 SET failure_count=1, next_check_at=?, check_error=? WHERE source_service='essai_absent' AND source_object_id=?",
                                 ((_now() - timedelta(minutes=1)).isoformat(), reg.erreur('fichier_introuvable'), 'absent-%d' % i))
            self.desk.db.commit()

    def item(self, document_id):
        return dict(self.desk.db.execute("SELECT * FROM prepared_items5689 WHERE document_id=? AND lifecycle='current'", (document_id,)).fetchone())

    def post(self, route, body, who=AVOCAT):
        return self.api(route, body=body, who=who)


# ====================================================================================== N03
class N03(Registre):
    def test_N03_T1_2001_puis_10000_demandes_le_dernier_document_visible_apres_un_nombre_borne_de_lots(self):
        rows = self.docreqs(0, 2001)
        last = rows[-1][5]
        passes, states = 0, []
        while not self.visible(last):
            liv.synchroniser(self.desk)              # affichage : une page par source au plus
            passes += 1
            states.append(self.etat())
            self.assertLessEqual(passes, math.ceil(2001 / reg.PAGE_AFFICHAGE) + 1)
            if passes == 1:
                self.assertFalse(liv.projection(self.desk, sync=False)['complet'])     # jamais « à jour » tant que tout n'est pas lu
                self.assertIn('Collecte partielle', states[0]['message'])
        self.assertTrue(all(s['state'] == 'partial' for s in states[:-1]))
        self.assertEqual((self.compte(), self.etat()['state']), (2001, 'fresh'))
        rows = self.docreqs(2001, 10000)
        liv.synchroniser(self.desk, fond=True)       # un passage d'arrière-plan suffit pour 7 999 lignes de plus
        self.assertTrue(self.visible(rows[-1][5]))
        self.assertEqual((self.compte(), self.etat()['state']), (10000, 'fresh'))
        self.assertTrue(liv.projection(self.desk, sync=False)['complet'])
        before = self.desk.db.execute('SELECT COUNT(*) FROM prepared_items5689').fetchone()[0]
        liv.synchroniser(self.desk, force=True)      # relecture complète : idempotente
        self.assertEqual(self.desk.db.execute('SELECT COUNT(*) FROM prepared_items5689').fetchone()[0], before)

    def test_N03_T2_plus_de_200_reponses_a_decider_aucune_oubliee_aucun_faux_complet(self):
        now = _now()
        drafts = [('%064x' % i, json.dumps({'matter': MID, 'result': {'body': 'Réponse fictive %d à Monsieur DURAND.' % i, 'requires_decision': True, 'limits': []}}), (now + timedelta(microseconds=i)).isoformat())
                  for i in range(250)]
        self.desk.db.executemany('INSERT INTO manual_drafts VALUES(?,?,?)', drafts)
        self.desk.db.commit()
        liv.synchroniser(self.desk)
        self.assertEqual(self.compte('reponses5671'), 200)
        self.assertEqual(self.etat('reponses5671')['state'], 'partial')         # 200 lues : la collecte n'est pas déclarée complète
        self.assertFalse(liv.projection(self.desk, sync=False)['complet'])
        liv.synchroniser(self.desk)
        self.assertEqual(self.compte('reponses5671'), 250)
        self.assertEqual(self.etat('reponses5671')['state'], 'fresh')
        decided = [d[0] for d in drafts[:10]]                                     # dix réponses décidées : leur carte quitte la file « à décider »
        later = (now + timedelta(seconds=5)).isoformat()
        for key in decided:
            self.desk.db.execute('UPDATE manual_drafts SET data=?, updated=? WHERE mail_key=?', (json.dumps({'matter': MID, 'result': {'requires_decision': False}}), later, key))
        self.desk.db.commit()
        liv.synchroniser(self.desk)
        self.assertEqual(self.compte('reponses5671'), 240)
        self.desk.db.executemany('DELETE FROM manual_drafts WHERE mail_key=?', [(d[0],) for d in drafts[10:15]])     # cinq supprimées : retrait constaté par la relecture complète
        self.desk.db.commit()
        with patch.object(reg, 'RETRAIT_DELAI', timedelta(0)):
            liv.synchroniser(self.desk, fond=True)
        self.assertEqual(self.compte('reponses5671'), 235)
        self.assertEqual(self.etat('reponses5671')['state'], 'fresh')
        archived = self.desk.db.execute("SELECT COUNT(*) FROM prepared_items5689 WHERE source_service='reponses5671' AND lifecycle='archived' AND state_reason='retire_du_journal'").fetchone()[0]
        self.assertEqual(archived, 15)                                            # rien n'est effacé : la trace reste au registre

    def test_N03_T3_arret_au_milieu_d_un_lot_ancienne_ligne_modifiee_reprise_sans_perte_ni_doublon(self):
        rows = self.docreqs(0, 600)
        liv.synchroniser(self.desk)
        self.assertEqual(self.compte(), 200)
        real, calls = liv.observe, [0]

        def coupure(desk, x):
            calls[0] += 1
            if calls[0] == 50:
                raise RuntimeError('arrêt du serveur')
            return real(desk, x)
        with patch.object(liv, 'observe', coupure):
            liv.synchroniser(self.desk)             # arrêt au milieu de la deuxième page
        self.assertNotEqual(self.etat()['state'], 'fresh')
        moved = '/Dossiers/DEMO/PROJETS/Limite-00010 renommé.docx'
        self.desk.db.execute('UPDATE docreq520 SET path=?, updated=? WHERE id=?', (moved, _now().isoformat(), rows[10][0]))     # ancienne ligne, déjà lue
        self.desk.db.commit()
        passes = 0
        while self.etat()['state'] != 'fresh':
            self.assertFalse(liv.projection(self.desk, sync=False)['complet'])      # état partiel jusqu'à la fin
            liv.synchroniser(self.desk)
            passes += 1
            self.assertLess(passes, 10)
        self.assertEqual(self.compte(), 600)                                        # aucune perte, aucune carte en double
        docs = self.desk.db.execute("SELECT COUNT(DISTINCT document_id) FROM prepared_items5689 WHERE source_service='docrequest520' AND lifecycle='current'").fetchone()[0]
        self.assertEqual(docs, 600)
        self.assertTrue(self.visible(moved))
        self.assertFalse(self.visible(rows[10][5]))

    def test_N03_retrait_d_une_demande_annulee_et_curseur_persistant(self):
        rows = self.docreqs(0, 3)
        liv.synchroniser(self.desk)
        state = self.desk.db.execute("SELECT curseur, complet, signature FROM prepared_sources5689 WHERE source='docrequest520'").fetchone()
        self.assertEqual(json.loads(state['curseur'])['docrequest520'][1], rows[-1][0])      # (clé, identifiant) de la dernière ligne lue
        self.assertEqual(state['complet'], 1)
        self.desk.db.execute("UPDATE docreq520 SET status='annule', updated=? WHERE id=?", (_now().isoformat(), rows[0][0]))
        self.desk.db.commit()
        liv.synchroniser(self.desk)
        self.assertEqual(self.compte(), 3)          # un fichier déposé reste suivi par son fichier : un journal ne l'efface pas

    def test_N03_quatre_etats_distincts_vide_partiel_complet_indisponible(self):
        liv.synchroniser(self.desk)
        self.assertEqual(self.etat()['state'], 'empty')                            # vide certain
        self.assertEqual(self.etat('notes5666')['state'], 'empty')                 # deux journaux, l'un absent : lus en un affichage, pas « partiel »
        self.docreqs(0, 250)
        liv.synchroniser(self.desk)
        self.assertEqual(self.etat()['state'], 'partial')                          # lecture en cours
        liv.synchroniser(self.desk)
        self.assertEqual(self.etat()['state'], 'fresh')                            # tout lu
        self.assertTrue(liv.projection(self.desk, sync=False)['complet'])

        def panne(desk):
            raise RuntimeError('disque')
        src = dict(sources.SOURCES[0], signature=lambda desk: 'neuf', rows=panne)
        with patch.object(sources, 'SOURCES', (src,) + tuple(sources.SOURCES[1:])):
            liv.synchroniser(self.desk)
            self.assertEqual(self.etat()['state'], 'unavailable')                  # panne, jamais confondue avec un vide
        self.assertEqual(self.compte(), 250)                                         # les documents connus restent

    def test_N03_parcours_commun_a_tous_les_producteurs_sans_plafond(self):
        paged = [s['id'] for s in sources.SOURCES if getattr(s['rows'], 'parcours', None)]
        for sid in ('docrequest520', 'document_projects', 'word_legal', 'cessions5684', 'corporate5636', 'courrier5679', 'notes5666', 'facturation5653', 'missions5614', 'production420', 'reponses5671'):
            self.assertIn(sid, paged)
        self.docreqs(0, 2101)
        self.assertEqual(sum(1 for _ in sources.docreq_rows(self.desk)), 2101)     # la lecture complète historique n'a plus de plafond

    def test_N03_reparation_relance_la_collecte_et_repare_les_sources_fresh_a_tort(self):
        rows = self.docreqs(0, 2001)
        for ident, read in islice(sources.docreq_rows(self.desk), 2000):          # état laissé par l'ancienne collecte : 2 000 lignes, puis « fresh »
            for x in read():
                liv.observe(self.desk, x)
        sig = json.dumps(sources.SOURCES[0]['signature'](self.desk), default=str)
        now = _now().isoformat()
        self.desk.db.execute("INSERT INTO prepared_sources5689(source,state,message,signature,errors,rows,last_ok,last_try) VALUES('docrequest520','fresh','',?,0,2000,?,?)", (sig, now, now))
        self.desk.db.commit()
        first = self.desk.db.execute("SELECT document_id, version_id FROM prepared_items5689 WHERE path=?", (rows[0][5],)).fetchone()
        liv.relire(self.desk, first['document_id'], first['version_id'], 'relu', *AVOCAT)
        self.assertIn('docrequest520', [d['source'] for d in reg.diagnostic_sources(self.desk)])
        out = reg.migrer(self.desk)
        self.assertIn('docrequest520', out['sources']['plafond_atteint'])
        self.assertEqual(self.etat()['state'], 'partial')                           # plus jamais « à jour » sur une lecture tronquée
        self.assertIn('plafond', self.etat()['message'])
        liv.synchroniser(self.desk, fond=True)
        self.assertEqual((self.compte(), self.etat()['state']), (2001, 'fresh'))
        self.assertEqual(self.item(first['document_id'])['review_state'], 'reviewed')    # versions et relectures conservées
        reg.migrer(self.desk)
        liv.synchroniser(self.desk, fond=True)
        self.assertEqual(self.compte(), 2001)
        self.assertEqual((self.nc.ecritures, self.stats), ([], []))                 # aucune écriture, aucune requête au stockage


# ====================================================================================== N05
class N05(Registre):
    def test_N05_T1_40_absents_une_version_nouvelle_controlee_dans_les_deux_prochains_lots(self):
        docs = [self.demande('v%02d' % i, '/Dossiers/DEMO/PROJETS/Valide %02d.docx' % i, texte_docx('Contrat valide %d.' % i)) for i in range(30)]
        good = docs[-1]
        for _ in range(2):
            liv.synchroniser_fichiers(self.desk, self.nc)                       # tous contrôlés une fois
        self.desk.db.execute('UPDATE prepared_items5689 SET checked_at=? WHERE id=?', (_now().isoformat(), good['id']))     # pire cas : contrôlé le plus récemment
        for i in range(40):
            self.absent(i, due=True)
        self.desk.db.commit()
        self.nc.add(good['path'], texte_docx('Nouvelle correction humaine du contrat.'))
        new_sha = hashlib.sha256(self.nc.files[good['path']]).hexdigest()
        reports = []
        for _ in range(2):
            reports.append(liv.synchroniser_fichiers(self.desk, self.nc))
            if liv.charger(self.desk, good['document_id'])['sha256'] == new_sha:
                break
        self.assertEqual(liv.charger(self.desk, good['document_id'])['sha256'], new_sha)
        self.assertTrue(all(r['absents'] <= 20 for r in reports), reports)        # les absents n'occupent jamais tout le lot
        self.assertTrue(all(r['requetes'] <= r['borne'] for r in reports))
        failing = self.desk.db.execute("SELECT failure_count, next_check_at, check_error FROM prepared_items5689 WHERE source_service='essai_absent' AND failure_count>=2").fetchall()
        self.assertTrue(failing)
        for row in failing:                                                       # délai progressif, erreur structurée
            self.assertGreater(row['next_check_at'], (_now() + timedelta(minutes=25)).isoformat())
            self.assertEqual(json.loads(row['check_error'])['code'], 'fichier_introuvable')
        echecs = liv.projection(self.desk, etat='echec', limite=100, sync=False)
        self.assertEqual(echecs['counts']['total'], 40)                            # les échecs restent visibles
        self.assertEqual(echecs['items'][0]['controle']['etat'], 'incident')

    def test_N05_T2_panne_generale_essais_espaces_requetes_plafonnees_reprise_equitable(self):
        docs = [self.demande('p%02d' % i, '/Dossiers/DEMO/PROJETS/Panne %02d.docx' % i, texte_docx('Contrat %d.' % i)) for i in range(12)]
        liv.synchroniser_fichiers(self.desk, self.nc)
        self.nc.panne = 'http_503'
        first = liv.synchroniser_fichiers(self.desk, self.nc)
        self.assertTrue(first['panne'])
        self.assertLessEqual(first['requetes'], reg.PANNE_SEUIL)                  # trois requêtes, puis arrêt du passage
        second = liv.synchroniser_fichiers(self.desk, self.nc)
        self.assertEqual((second['requetes'], second['arret']), (0, 'stockage_en_attente'))
        t0 = _now()
        with patch.object(reg, 'maintenant', return_value=t0 + timedelta(minutes=11)):
            third = liv.synchroniser_fichiers(self.desk, self.nc)
        self.assertLessEqual(third['requetes'], reg.PANNE_SEUIL)
        wait = self.desk.settings(reg.PANNE_CLE)
        self.assertEqual(wait['essais'], 2)
        self.assertGreater(wait['jusqua'], (t0 + timedelta(minutes=11) + timedelta(minutes=19)).isoformat())      # attente doublée
        rows = self.desk.db.execute("SELECT failure_count, check_error FROM prepared_items5689 WHERE source_service='docrequest520'").fetchall()
        self.assertEqual({(r['failure_count'], r['check_error']) for r in rows}, {(0, '')})    # une panne n'est reprochée à aucun document
        self.nc.panne = None
        self.stats.clear()
        with patch.object(reg, 'maintenant', return_value=t0 + timedelta(hours=2)):
            for _ in range(3):
                liv.synchroniser_fichiers(self.desk, self.nc, limite=4)
        self.assertEqual(sorted(self.stats), sorted(d['path'] for d in docs))      # reprise équitable : chacun une fois, aucun deux fois
        self.assertEqual(self.desk.settings(reg.PANNE_CLE), {})

    def test_N05_T3_annulation_ou_pause_aucun_lot_enfant_reactive(self):
        for i in range(3):
            self.demande('t%d' % i, '/Dossiers/DEMO/PROJETS/Pause %d.docx' % i, texte_docx('Contrat %d.' % i))
        jid = travaux.schedule(self.desk, time.time())
        self.desk.cancel_job(jid)
        jobs = self.desk.db.execute('SELECT COUNT(*) FROM jobs').fetchone()[0]
        self.desk.active_job_id = jid
        self.stats.clear()
        with self.assertRaises(Stop):
            travaux.synchroniser(self.desk, self.nc)
        self.assertEqual(self.stats, [])                                          # aucune lecture du stockage
        self.assertEqual(self.desk.db.execute('SELECT status FROM jobs WHERE id=?', (jid,)).fetchone()[0], 'cancelled')
        self.assertEqual(self.desk.db.execute('SELECT COUNT(*) FROM jobs').fetchone()[0], jobs)     # aucun travail enfant
        # pause des automatismes posée pendant un passage : arrêt au point de contrôle suivant, rien n'est relancé
        self.desk.active_job_id = None
        job = self.desk.enqueue('synchro_livrables5689', {}, priority=60)
        self.desk.active_job_id = job
        real = self.nc.stat

        def stat_puis_pause(path):
            self.desk.setting('missions567:pause', True)
            return real(path)
        self.nc.stat = stat_puis_pause
        with self.assertRaises(Stop):
            liv.synchroniser_fichiers(self.desk, self.nc, pause=lambda: __import__('agent.pause5629', fromlist=['x']).checkpoint(self.desk))
        self.desk.active_job_id = None
        row = liv.charger(self.desk, liv.projection(self.desk, sync=False)['items'][0]['document_id'])
        before = self.desk.db.execute('SELECT COUNT(*) FROM jobs').fetchone()[0]
        reg.reessayer(self.desk, row['document_id'], row['version_id'], AVOCAT[0])     # Réessayer ne crée aucun travail : la pause reste respectée
        try:
            travaux.schedule(self.desk, time.time() + 3600)
        except Stop:
            pass
        self.assertEqual(self.desk.db.execute('SELECT COUNT(*) FROM jobs').fetchone()[0], before)

    def test_N05_actions_reessayer_retrouver_retirer_du_suivi(self):
        row = self.demande()
        liv.synchroniser_fichiers(self.desk, self.nc)
        raw = self.nc.files.pop(row['path'])
        fileid = self.nc.ids[row['path']]
        liv.synchroniser_fichiers(self.desk, self.nc)
        incident = self.item(row['document_id'])
        self.assertEqual((incident['failure_count'], json.loads(incident['check_error'])['code']), (1, 'fichier_introuvable'))
        self.stats.clear()
        self.assertEqual(liv.synchroniser_fichiers(self.desk, self.nc)['controles'], 0)      # en attente de son délai
        body = {'version_id': incident['version_id'], 'cle': 'cle-1'}
        out = self.post('registre/%s/reessayer' % row['document_id'], body)
        self.assertEqual(out['status'], '200 OK', out)
        self.assertEqual(self.stats, [])                                          # aucune requête au stockage depuis l'action
        liv.synchroniser_fichiers(self.desk, self.nc)
        again = self.item(row['document_id'])
        self.assertEqual(again['failure_count'], 2)
        self.assertGreater(again['next_check_at'], (_now() + timedelta(minutes=29)).isoformat())     # délai doublé
        self.assertEqual(self.post('registre/%s/retirer-suivi' % row['document_id'], body, who=ASSISTANT)['status'], '403 Forbidden')
        self.assertEqual(self.post('registre/%s/retirer-suivi' % row['document_id'], body)['status'], '200 OK')
        self.post('registre/%s/reessayer' % row['document_id'], {**body, 'cle': 'cle-2'})
        self.desk.db.execute("UPDATE prepared_items5689 SET suivi='retire' WHERE id=?", (again['id'],))
        self.desk.db.commit()
        self.stats.clear()
        liv.synchroniser_fichiers(self.desk, self.nc)
        self.assertEqual(self.stats, [])                                          # retiré du suivi : plus contrôlé
        card = next(c for c in liv.projection(self.desk, etat='echec', sync=False)['items'] if c['document_id'] == row['document_id'])
        self.assertEqual((card['controle']['etat'], card['controle']['code']), ('retire', 'fichier_introuvable'))    # l'incident reste visible
        self.assertEqual(self.post('registre/%s/reprendre-suivi' % row['document_id'], body)['status'], '200 OK')
        moved = '/Dossiers/DEMO/PROJETS/Contrat DURAND (retrouvé).docx'           # la personne a renommé le fichier : même identifiant
        self.nc.files[moved], self.nc.etags[moved], self.nc.ids[moved] = raw, '"e99"', fileid
        self.assertEqual(self.post('registre/%s/retrouver' % row['document_id'], body)['status'], '200 OK')
        liv.synchroniser_fichiers(self.desk, self.nc)
        found = self.item(row['document_id'])
        self.assertEqual((found['path'], found['check_error'], found['suivi'], found['failure_count']), (moved, '', 'actif', 0))
        self.assertEqual(self.post('registre/%s/reessayer' % row['document_id'], {'version_id': 'sha256:' + 'f' * 64, 'cle': 'c3'})['status'], '409 Conflict')
        self.assertEqual(self.post('registre/%s/reessayer' % row['document_id'], {'version_id': found['version_id']})['status'], '400 Bad Request')    # clé absente

    def test_N05_dossier_reserve_refuse_avant_toute_action(self):
        from agent import membres5650
        row = self.demande()
        membres5650.set_reserve(self.desk.c['state_dir'], MID, ['autre@example.test'])
        self.addCleanup(membres5650.clear)
        body = {'version_id': row['version_id'], 'cle': 'k'}
        self.assertEqual(self.post('registre/%s/reessayer' % row['document_id'], body, who=ASSISTANT)['status'], '403 Forbidden')
        self.assertEqual(self.item(row['document_id'])['next_check_at'], '')
        original = reg.reessayer
        with patch.object(rweb.reg, 'reessayer', side_effect=lambda desk, ident, version, owner, hidden, droits: original(desk, ident, version, owner, frozenset(), droits)):
            self.assertEqual(self.post('registre/%s/reessayer' % row['document_id'], body, who=ASSISTANT)['status'], '200 OK')    # mutation : sans la garde, l'action passerait

    def test_N05_anciennes_lignes_initialisees_par_lots(self):
        now = _now().isoformat()
        for i in range(7):
            liv.observe(self.desk, dict(service='ancien', object='o%d' % i, version='1', matter=MID, path='/Dossiers/DEMO/Ancien %d.docx' % i, title='Ancien', state='published', when=now))
        self.desk.db.execute("UPDATE prepared_items5689 SET production_state='partial', state_reason='fichier_introuvable_a_cet_emplacement' WHERE source_object_id IN ('o0','o1','o2','o3')")
        self.desk.db.commit()
        counts = [reg.initialiser_controles(self.desk, lot=3) for _ in range(4)]
        self.assertEqual(counts, [3, 3, 1, 0])
        rows = {r['source_object_id']: r for r in self.desk.db.execute("SELECT * FROM prepared_items5689 WHERE source_service='ancien'")}
        horizon = (_now() + timedelta(hours=24, minutes=1)).isoformat()
        for k in ('o0', 'o1', 'o2', 'o3'):                                     # incident conservé, échéance étalée sur 24 h
            self.assertEqual((rows[k]['failure_count'], json.loads(rows[k]['check_error'])['code']), (1, 'fichier_introuvable'))
            self.assertLess(rows[k]['next_check_at'], horizon)
            self.assertEqual(rows[k]['production_state'], 'partial')              # rien n'est certifié ni recréé
        self.assertEqual({rows[k]['check_error'] for k in ('o4', 'o5', 'o6')}, {''})
        self.assertEqual(self.nc.ecritures, [])

    def test_N05_charge_requetes_comptees_bornees_et_aucune_a_l_affichage(self):
        for i in range(20):
            self.demande('c%02d' % i, '/Dossiers/DEMO/PROJETS/Charge %02d.docx' % i, texte_docx('Contrat %d.' % i))
        calls = [0]
        for name in ('stat', 'download', 'list_folder'):
            real = getattr(self.nc, name)

            def counted(*a, _real=real):
                calls[0] += 1
                return _real(*a)
            setattr(self.nc, name, counted)
        out = liv.synchroniser_fichiers(self.desk, self.nc, borne=10)
        self.assertEqual((out['requetes'], out['arret']), (10, 'borne_requetes'))
        self.assertEqual(calls[0], out['requetes'])                                 # la mesure est la vraie
        self.assertEqual(reg.diagnostic(self.desk)['mesure']['requetes'], 10)
        calls[0] = 0
        row = liv.projection(self.desk)['items'][0]
        liv.a_relire(self.desk)
        web.section_html(self.desk, '/p', MID)
        self.js(self.api('registre/etat'))
        rweb.suivi_html(self.desk, liv.charger(self.desk, row['document_id']), '/p')
        self.assertEqual(calls[0], 0)                                               # aucune requête au stockage à l'affichage


# ====================================================================================== N06
class N06(Registre):
    def test_N06_T1_deux_fichiers_identiques_deux_cartes_deux_identites_relectures_independantes(self):
        raw = texte_docx('Contrat entre DURAND et MARTIN.')        # mêmes octets (l'archive porte l'heure de sa création)
        first = self.demande('original', '/Dossiers/DEMO/PROJETS/Contrat original.docx', raw)
        second = self.demande('copie', '/Dossiers/DEMO/PROJETS/Contrat copie.docx', raw)
        self.assertEqual(first['sha256'], second['sha256'])
        self.assertNotEqual(first['document_id'], second['document_id'])
        self.assertEqual(len(liv.projection(self.desk, sync=False)['items']), 2)
        liv.relire(self.desk, first['document_id'], first['version_id'], 'relu', *AVOCAT)
        self.assertEqual((self.item(first['document_id'])['review_state'], self.item(second['document_id'])['review_state']), ('reviewed', 'to_review'))
        suggestions = reg.doublons(self.desk, second['document_id'])
        self.assertEqual(len(suggestions), 1)                                      # une suggestion, jamais une fusion
        liv.synchroniser_fichiers(self.desk, self.nc)
        ids = {self.item(first['document_id'])['file_id'], self.item(second['document_id'])['file_id']}
        self.assertEqual(ids, {self.nc.ids[first['path']], self.nc.ids[second['path']]})
        out = self.post('registre/doublons/%s/ecarter' % suggestions[0]['id'], {'cle': 'd1'})
        self.assertEqual(out['status'], '200 OK', out)
        self.assertEqual(reg.doublons(self.desk), [])
        self.assertNotEqual(self.item(first['document_id'])['document_id'], self.item(second['document_id'])['document_id'])
        self.assertEqual(self.post('registre/doublons/%s/confirmer' % suggestions[0]['id'], {'cle': 'd2'}, who=ASSISTANT)['status'], '403 Forbidden')

    def test_N06_T2_renommage_identifiant_conserve_une_identite_historique_continu(self):
        row = self.demande()
        liv.synchroniser_fichiers(self.desk, self.nc)                              # l'identifiant de fichier est lu
        liv.relire(self.desk, row['document_id'], row['version_id'], 'relu', *AVOCAT)
        new = '/Dossiers/DEMO/PROJETS/Contrat DURAND signé.docx'
        self.nc.renommer(row['path'], new)
        out = liv.synchroniser_fichiers(self.desk, self.nc)
        self.assertEqual((out['verifies'], out['absents']), (1, 0))
        moved = self.item(row['document_id'])
        self.assertEqual((moved['path'], moved['review_state'], moved['version_number']), (new, 'reviewed', 1))
        self.nc.add(new, texte_docx('Contrat DURAND modifié après renommage.'))
        liv.synchroniser_fichiers(self.desk, self.nc)
        versions = self.desk.db.execute('SELECT version_number, lifecycle FROM prepared_items5689 WHERE document_id=? ORDER BY version_number', (row['document_id'],)).fetchall()
        self.assertEqual([tuple(v) for v in versions], [(1, 'replaced'), (2, 'current')])      # historique continu, une seule identité
        # contre-épreuve : un autre fichier au même contenu (autre identifiant) n'est pas un renommage
        other = self.demande('r2', '/Dossiers/DEMO/PROJETS/Bail ZORVAL.docx', texte_docx('Bail fictif ZORVAL.'))
        liv.synchroniser_fichiers(self.desk, self.nc)
        self.nc.add('/Dossiers/DEMO/PROJETS/Bail ZORVAL copie.docx', self.nc.files[other['path']])
        self.nc.files.pop(other['path'])
        out = liv.synchroniser_fichiers(self.desk, self.nc)
        self.assertEqual(out['absents'], 1)
        self.assertEqual(self.item(other['document_id'])['path'], other['path'])
        # même identifiant annoncé par un journal à un autre chemin : la même identité ; sous un autre stockage : une autre
        fid = self.item(row['document_id'])['file_id']
        same = liv.observe(self.desk, dict(service='autre_journal', object='j1', version='1', matter=MID, path=new, fileid=fid, sha256=self.item(row['document_id'])['sha256']))
        self.assertEqual(liv._item(self.desk, same)['document_id'], row['document_id'])
        with patch.object(reg, 'stockage', return_value='autre-serveur'):
            elsewhere = liv.observe(self.desk, dict(service='autre_journal', object='j2', version='1', matter=MID, path='/Dossiers/DEMO/PROJETS/Ailleurs.docx', fileid=fid, sha256='9' * 64))
        self.assertNotEqual(liv._item(self.desk, elsewhere)['document_id'], row['document_id'])

    def test_N06_T3_autre_format_ou_nouvelle_version_pas_de_carte_supplementaire(self):
        row = self.demande()
        liv.observe(self.desk, dict(service='export_essai', object='x1', version='1', matter=MID, path=row['path'][:-5] + '.pdf', sha256='d' * 64, title='Contrat DURAND'))
        self.desk.db.commit()
        cards = liv.projection(self.desk, sync=False)['items']
        self.assertEqual(len(cards), 1)
        self.assertEqual(cards[0]['formats'], ['docx', 'pdf'])
        again = self.demande(raw=texte_docx('Contrat DURAND, version corrigée.'))
        self.assertEqual(again['document_id'], row['document_id'])
        self.assertEqual(again['version_number'], 2)
        self.assertEqual(len(liv.projection(self.desk, sync=False)['items']), 1)
        self.assertEqual(reg.doublons(self.desk), [])

    def _fusion_ancienne(self, review_after_link=False, tie=False):
        """État laissé par l'ancien registre : la copie au même contenu avait rejoint le document de l'original, et la carte avait pris son chemin."""
        first = self.demande('original', '/Dossiers/DEMO/PROJETS/Contrat original.docx')
        copy = '/Dossiers/DEMO/PROJETS/Contrat copie.docx'
        raw = self.nc.files[first['path']]
        self.nc.add(copy, raw)
        sig_rows = self.desk.db.execute("SELECT result FROM docreq520 WHERE id='original'").fetchone()
        now = _now()
        self.desk.db.execute("INSERT INTO docreq520(id,matter,request,kind,status,path,result,created,updated) VALUES(?,?,?,?,?,?,?,?,?)",
                             ('copie', MID, 'Copie fictive', 'contrat', 'cree', copy, sig_rows['result'], now.isoformat(), now.isoformat()))
        review_at = (now + timedelta(seconds=1 if not review_after_link else 3)).isoformat()
        link_at = (now + timedelta(seconds=2)).isoformat()
        if tie:
            review_at = link_at
        if not review_after_link and not tie:
            liv.relire(self.desk, first['document_id'], first['version_id'], 'relu', *AVOCAT, at=review_at)
        self.desk.db.execute('INSERT INTO prepared_source_links5689(item_id,link_kind,source_service,source_object_id,source_version,label,created) VALUES(?,?,?,?,?,?,?)',
                             (first['id'], 'producteur', 'docrequest520', 'copie', first['sha256'], '', link_at))
        self.desk.db.execute('UPDATE prepared_items5689 SET path=? WHERE id=?', (copy, first['id']))
        self.desk.db.execute("UPDATE prepared_representations5689 SET path=? WHERE item_id=? AND role='principal'", (copy, first['id']))
        self.desk.db.commit()
        if review_after_link or tie:
            liv.relire(self.desk, first['document_id'], first['version_id'], 'relu', *AVOCAT, at=review_at)
        liv.synchroniser(self.desk, force=True)        # la source est lue : rien ne change tant que la réparation n'a pas scindé
        self.assertEqual(len(liv.projection(self.desk, sync=False)['items']), 1)
        return first, copy

    def test_N06_reparation_scinde_les_documents_fusionnes_sans_deplacer_de_fichier(self):
        first, copy = self._fusion_ancienne()
        files, writes = dict(self.nc.files), list(self.nc.ecritures)
        out = reg.scinder(self.desk)
        self.assertEqual(out['scindes'], 1)
        cards = {c['document_id']: c for c in liv.projection(self.desk, sync=False)['items']}
        self.assertEqual(len(cards), 2)
        by_path = {self.item(d)['path']: self.item(d) for d in cards}
        self.assertEqual(set(by_path), {first['path'], copy})
        self.assertEqual(by_path[first['path']]['review_state'], 'reviewed')        # la relecture suit le fichier que la personne a vu (l'original)
        self.assertEqual(by_path[copy]['review_state'], 'to_review')
        self.assertEqual((dict(self.nc.files), self.nc.ecritures), (files, writes))  # aucun fichier déplacé, écrit ni supprimé
        self.desk.setting('registre5693:scission', '')
        self.assertEqual(reg.scinder(self.desk)['scindes'], 0)                      # répétable
        liv.synchroniser(self.desk, force=True)
        self.assertEqual(len(liv.projection(self.desk, sync=False)['items']), 2)    # pas de nouvelle fusion

    def test_N06_reparation_relecture_ambigue_revient_a_relire(self):
        self._fusion_ancienne(tie=True)
        reg.scinder(self.desk)
        states = {c['review'] for c in liv.projection(self.desk, sync=False)['items']}
        self.assertEqual(states, {'to_review'})                                    # cas ambigu : à relire, jamais propagé aux deux copies
        self.assertTrue(self.desk.db.execute("SELECT 1 FROM prepared_identity5693 WHERE kind='relecture_ambigue'").fetchone())

    def test_N06_fiche_affiche_le_suivi_et_le_doublon(self):
        raw = texte_docx('Contrat entre DURAND et MARTIN.')
        first = self.demande('original', '/Dossiers/DEMO/PROJETS/Contrat original.docx', raw)
        self.demande('copie', '/Dossiers/DEMO/PROJETS/Contrat copie.docx', raw)
        shell = lambda title, body, *a: body
        env = {'HTTP_X_AXIORHUB_USER': AVOCAT[0], 'HTTP_X_AXIORHUB_ROLE': AVOCAT[1]}
        html = web.page_route(env, self.f.c, {'csrf': 'c'}, '/p', '/livrables/' + first['document_id'], {}, shell)['body']
        self.assertIn('Suivi du fichier', html)
        self.assertIn('m5689/registre/doublons/', html)
        self.assertIn('Ils restent distincts', html)
        self.assertNotIn(first['path'], html)
