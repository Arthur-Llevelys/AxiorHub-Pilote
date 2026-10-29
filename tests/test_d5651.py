"""5.6.51 : recette de la refonte de la page Dossier (audit 5.6.50, Dossier-D04 à D13) et des huit fonctions d'autonomie (A01 à A08).

Dossier entièrement fictif (domaine example.test) : deux personnes de même patronyme, une société avec alias, une procédure
historique et une procédure active, des écritures de chaque partie, des pièces de chaque partie, une jurisprudence citée, un
jugement du dossier, un document illisible. Aucun service externe ; le modèle est simulé quand il intervient.
"""
from datetime import date, timedelta
import io
import json
from pathlib import Path
import tempfile
import unittest
import zipfile

from agent import apprentissage5651 as ap, contexte5651, file5651, memoire5651 as mem, missions5651 as mi, modele5651 as md
from agent import relecture5651 as rl, synthese5632, taches5614, vue5651
from agent.common import Stop
from agent.dav import LocalFolder
from agent.desk import Desk
from agent.index import DocumentIndex
import test_agent as fixtures
import test_v5651 as t51

MID = 'DOS-001'
ROOT = '/Dossiers/DEMO'
DOCS = (   # chemin, texte, erreur
    (ROOT + '/Procedure/Assignation Alpha Fictive.pdf', 'ASSIGNATION devant le tribunal judiciaire de Toulon délivrée à la requête de la SARL Alpha Fictive.', ''),
    (ROOT + '/Procedure/Conclusions Dupont 2.pdf', 'CONCLUSIONS récapitulatives pour Madame Alice Dupont. RG 25/00456 (ancienne affaire citée).', ''),
    (ROOT + '/Pieces/Contrat de bail.pdf', 'CONTRAT DE BAIL commercial entre la SARL Alpha Fictive et Madame Alice Dupont.', ''),
    (ROOT + '/Pieces/Facture 2024-03.pdf', 'FACTURE de loyers.', ''),
    (ROOT + '/Jurisprudence/Cass civ 3e 2020.pdf', 'Cour de cassation, troisième chambre civile.', ''),
    (ROOT + '/Procedure/Jugement TJ Toulon.pdf', 'JUGEMENT du tribunal judiciaire de Toulon.', ''),
    (ROOT + '/Pieces/scan photo constat.pdf', '', 'pdf_scan_ocr_necessaire'),
    (ROOT + '/divers.pdf', 'Texte sans nature reconnaissable.', ''),
)


def _digest(desk, path, data, etag='e'):
    synthese5632.schema(desk)
    desk.db.execute('INSERT OR REPLACE INTO piece_digests5634 VALUES(?,?,?,?,?,?,?,?,?,?,?,?)',
                    (MID, path, 'sha-' + etag, 'm', etag, 'x', '', json.dumps(data, ensure_ascii=False), 'complet', 1, 1, '2026-10-01'))
    desk.db.commit()


class Base(unittest.TestCase):
    def setUp(self):
        self.f = fixtures.EngineTests('test_observation_has_no_mail_write')
        self.f.setUp()
        self.addCleanup(self.f.doCleanups)
        self.desk = Desk(self.f.c)
        self.addCleanup(self.desk.db.close)
        idx = DocumentIndex(self.f.c['state_dir'])
        for path, text, error in DOCS:
            idx.db.execute('INSERT INTO docs VALUES (?,?,?,?,?,?)', (MID, path, 'e-' + str(len(path)), '2026-09-01T08:00:00+00:00', text, error))
        idx.db.commit()
        idx.db.close()
        _digest(self.desk, DOCS[0][0], {
            'nature': 'Assignation', 'date_document': '2026-09-04',
            'parties': ['SARL Alpha Fictive', 'Madame Alice DUPONT', 'Mme Alice Dupont', 'Monsieur Jean MARTIN', 'Madame Claire MARTIN', 'Maître Paul Exemple'],
            'faits': [{'texte': 'Le bail commercial a été conclu le 3 janvier 2024.', 'extrait': 'conclu le 3 janvier 2024', 'date': '2024-01-03',
                       'date_precision': 'jour', 'statut': 'documente', 'page': 2},
                      {'texte': 'Les locaux sont affectés d’infiltrations ayant perturbé l’activité.', 'extrait': 'infiltrations', 'statut': 'a_verifier'},
                      {'texte': 'RG n° 26/01234', 'extrait': 'RG'}],
            'demandes': [{'texte': 'Condamner Madame Dupont au paiement des loyers impayés.', 'extrait': 'condamner'}],
            'montants': [{'montant': '1 000 €', 'devise': 'EUR', 'valeur': '1000', 'role': 'demande_client', 'objet': 'loyers impayés', 'extrait': '1 000'}]})
        _digest(self.desk, DOCS[1][0], {
            'nature': 'Conclusions', 'parties': ['Madame Alice Dupont'],
            'faits': [{'texte': 'La bailleresse n’a jamais reçu la demande de réparation.', 'extrait': 'jamais reçu', 'statut': 'a_verifier'}]})
        _digest(self.desk, DOCS[5][0], {'nature': 'Jugement', 'date_document': '2026-06-02', 'parties': ['SARL Alpha Fictive'], 'faits': []})


# ---------------------------------------------------------------------------------------------------- D05 parties
class Parties(Base):
    def test_civilites_abregees_une_seule_fiche_avec_alias(self):
        md.rebuild(self.desk, MID)
        dupont = [p for p in md.parties(self.desk, MID) if 'dupont' in p['core']]
        self.assertEqual(len(dupont), 1)
        self.assertEqual({a['alias'] for a in dupont[0]['aliases']} >= {'Madame Alice DUPONT', 'Mme Alice Dupont'}, True)
        self.assertEqual(dupont[0]['kind'], 'personne')

    def test_homonymes_epoux_societe_et_dirigeant_jamais_reunis(self):
        cases = (('Monsieur Jean MARTIN', 'Madame Claire MARTIN'), ('Monsieur Paul Durand', 'Madame Paul Durand'),
                 ('SARL Martin Bâtiment', 'Monsieur Jean Martin'), ('SARL Alpha', 'SAS Alpha'), ('Alpha Fictive', 'Fictive Alpha'), ('M. Martin', 'Monsieur Jean Martin'))
        for a, b in cases:
            self.assertNotEqual(md.compare(md.identity(a), md.identity(b))[0], 'meme', (a, b))
        self.assertEqual(md.compare(md.identity('M. Martin'), md.identity('Monsieur Jean Martin'))[0], 'candidat')   # prénom manquant : à confirmer
        md.rebuild(self.desk, MID)
        names = [p['display_name'] for p in md.parties(self.desk, MID)]
        self.assertIn('Monsieur Jean MARTIN', names)
        self.assertIn('Madame Claire MARTIN', names)
        self.assertFalse(any('Exemple' in n for n in names))                       # un avocat n'est pas une partie

    def test_fusion_reversible_et_separation_durable(self):
        md.rebuild(self.desk, MID)
        a = md.add_party(self.desk, MID, 'avocat@example.test', 'M. Martin')
        cands = md.candidates(self.desk, MID)
        self.assertTrue(cands)
        pair = cands[0]
        md.separate(self.desk, MID, pair['a']['id'], pair['b']['id'], 'avocat@example.test', 'homonymes')
        md.rebuild(self.desk, MID, force=True)
        self.assertFalse([c for c in md.candidates(self.desk, MID) if {c['a']['id'], c['b']['id']} == {pair['a']['id'], pair['b']['id']}])
        keep = next(p for p in md.parties(self.desk, MID) if p['display_name'] == 'Monsieur Jean MARTIN')
        out = md.merge(self.desk, MID, keep['id'], a, 'avocat@example.test')
        self.assertNotIn(a, [p['id'] for p in md.parties(self.desk, MID)])
        self.assertIn('M. Martin', [al['alias'] for al in next(p for p in md.parties(self.desk, MID) if p['id'] == keep['id'])['aliases']])
        md.undo_merge(self.desk, MID, out['decision'], 'avocat@example.test')
        self.assertIn(a, [p['id'] for p in md.parties(self.desk, MID)])
        with self.assertRaisesRegex(Stop, 'fusion_personne_societe_refusee'):
            soc = next(p for p in md.parties(self.desk, MID) if p['kind'] == 'societe')
            md.merge(self.desk, MID, soc['id'], keep['id'], 'avocat@example.test')


# ---------------------------------------------------------------------------------------------------- D06 à D09
class Informations(Base):
    def test_attribution_metadonnees_et_fait_sans_date(self):
        md.rebuild(self.desk, MID)
        infos = md.assertions(self.desk, MID)
        texts = [a['text'] for a in infos]
        self.assertNotIn('RG n° 26/01234', texts)                                   # métadonnée : pas un fait du litige
        infiltr = next(a for a in infos if 'infiltrations' in a['text'])
        self.assertEqual(infiltr['date_value'], '')
        alpha = next(p for p in md.parties(self.desk, MID) if 'alpha' in p['core'])
        self.assertEqual(infiltr['author'], alpha['id'])                             # auteur : la partie qui assigne
        dup = next(p for p in md.parties(self.desk, MID) if 'dupont' in p['core'])
        adverse = next(a for a in infos if 'jamais reçu' in a['text'])
        self.assertEqual(adverse['author'], dup['id'])                               # écritures de Mme Dupont
        self.assertEqual(adverse['proof'], 'allegue')
        ctx = contexte5651.for_drafting(self.desk, MID)
        undated = [x['texte'] for x in ctx['faits_contexte_date_non_determinee']]
        self.assertIn('Les locaux sont affectés d’infiltrations ayant perturbé l’activité.', undated)
        selon = {x['texte']: x['selon'] for x in ctx['faits_contexte_date_non_determinee']}
        self.assertEqual(selon['La bailleresse n’a jamais reçu la demande de réparation.'], dup['display_name'])
        self.assertIn('Une affirmation de la partie adverse reste attribuée à cette partie ; elle n’est pas admise par le cabinet.', ctx['regles'])
        g = vue5651.structured_recital(self.desk, MID)
        self.assertTrue(any('infiltrations' in a['text'] for a in g['sans_date']))

    def test_validation_typee_refus_durable_et_version(self):
        md.rebuild(self.desk, MID)
        a = next(x for x in md.assertions(self.desk, MID) if 'jamais reçu' in x['text'])
        out = md.review(self.desk, MID, a['id'], 'avocat@example.test', 'enregistrer', {'version': a['version']})
        self.assertEqual((out['review'], out['proof']), ('enregistre', 'allegue'))   # enregistrée, pas « établie »
        with self.assertRaisesRegex(Stop, 'version_en_conflit'):
            md.review(self.desk, MID, a['id'], 'avocat@example.test', 'refuser', {'version': a['version']})
        b = next(x for x in md.assertions(self.desk, MID) if 'infiltrations' in x['text'])
        md.review(self.desk, MID, b['id'], 'avocat@example.test', 'hors_sujet', {'motif': 'autre lot'})
        md.rebuild(self.desk, MID, force=True)
        self.assertEqual(next(x for x in md.assertions(self.desk, MID) if x['id'] == b['id'])['review'], 'hors_sujet')
        self.assertNotIn('infiltrations', json.dumps(contexte5651.for_drafting(self.desk, MID), ensure_ascii=False))
        part = md.add_assertion(self.desk, MID, 'avocat@example.test', {'texte': 'Le preneur a quitté les lieux.', 'date': 'mars 2024'})
        row = next(x for x in md.assertions(self.desk, MID) if x['id'] == part)
        self.assertEqual(row['date_precision'], 'mois')                              # jamais complétée au 1er du mois
        self.assertNotRegex(row['date_value'], r'-01$')

    def test_procedure_de_reference_et_rg_cite(self):
        md.rebuild(self.desk, MID)
        pid = md.save_procedure(self.desk, MID, 'avocat@example.test', {'juridiction': 'Tribunal judiciaire de Toulon', 'rg': '26/01234', 'reference': True})
        md.save_procedure(self.desk, MID, 'avocat@example.test', {'juridiction': 'Tribunal judiciaire de Toulon', 'rg': '25/00456', 'statut': 'historique'})
        md.rebuild(self.desk, MID, force=True)
        ref = md.reference_procedure(self.desk, MID)
        self.assertEqual((ref['id'], ref['rg']), (pid, '26/01234'))
        self.assertEqual({p['rg']: p['statut'] for p in md.procedures(self.desk, MID)}['25/00456'], 'historique')

    def test_decisions_judiciaires_et_jurisprudence_citee(self):
        md.rebuild(self.desk, MID)
        js = md.judgments(self.desk, MID)
        self.assertEqual([j['path'] for j in js], [DOCS[5][0]])
        self.assertEqual(js[0]['examined'], 1)
        natures = {d['path']: d['nature'] for d in md.documents(self.desk, MID)}
        self.assertEqual(natures[DOCS[4][0]], 'jurisprudence')
        self.assertNotIn(DOCS[4][0], [j['path'] for j in js])


class Pieces(Base):
    def test_nature_et_usage_independants(self):
        md.rebuild(self.desk, MID)
        data = md.pieces(self.desk, MID)
        listed = [d['path'] for d in data['nos_pieces'] + data['a_numeroter'] + data['a_classer']]
        self.assertNotIn(DOCS[0][0], listed)                                         # acte sans lien de bordereau : absent de Pièces
        self.assertNotIn(DOCS[4][0], listed)                                         # jurisprudence : absente de Pièces
        self.assertIn(DOCS[2][0], [d['path'] for d in data['a_numeroter']])          # contrat : pièce probable à numéroter
        self.assertIn(DOCS[7][0], [d['path'] for d in data['a_classer']])
        doc = next(d for d in md.documents(self.desk, MID) if d['path'] == DOCS[0][0])
        md.classify(self.desk, MID, DOCS[0][0], 'avocat@example.test', piece=True, piece_party='cabinet', piece_number='1', version=doc['version'])
        doc = next(d for d in md.documents(self.desk, MID) if d['path'] == DOCS[0][0])
        self.assertEqual((doc['nature'], doc['usage_piece']), ('assignation', 'oui'))  # nature conservée
        c = next(d for d in md.documents(self.desk, MID) if d['path'] == DOCS[2][0])
        md.classify(self.desk, MID, DOCS[2][0], 'avocat@example.test', piece=True, piece_party='SARL Alpha Fictive', piece_number='1', version=c['version'])
        data = md.pieces(self.desk, MID)
        self.assertEqual([d['piece_number'] for d in data['nos_pieces']], ['1'])
        self.assertEqual([d['piece_number'] for d in data['adverses']['SARL Alpha Fictive']], ['1'])   # deux pièces n° 1 distinctes
        md.rebuild(self.desk, MID, force=True)                                      # le classement de l'avocat survit au rescan
        self.assertEqual(next(d for d in md.documents(self.desk, MID) if d['path'] == DOCS[2][0])['piece_party'], 'SARL Alpha Fictive')
        with self.assertRaisesRegex(Stop, 'version_en_conflit'):
            md.classify(self.desk, MID, DOCS[2][0], 'avocat@example.test', nature='facture', version=c['version'])

    def test_couverture_et_etat_jamais_a_jour_avec_un_illisible(self):
        md.rebuild(self.desk, MID)
        cov = vue5651.coverage(self.desk, MID)
        self.assertEqual((cov['inventories'], cov['erreurs'], cov['categories']), (len(DOCS), 1, {'scan': 1}))
        self.assertNotEqual(vue5651.analysis_state(self.desk, MID)['state'], 'a_jour')
        gaps = mem.gaps(self.desk, MID)
        self.assertTrue(any(g['kind'] == 'fichier_illisible' and g['blocking'] == 'facultatif' for g in gaps))   # n'arrête pas le reste


# ---------------------------------------------------------------------------------------------------- A03 changements
class Changes(Base):
    def test_meme_version_un_seul_evenement_et_pas_de_boucle(self):
        first = md.rebuild(self.desk, MID)
        self.assertTrue(md.rebuild(self.desk, MID).get('inchange'))                 # dossier inchangé : aucune réanalyse
        self.assertIsNotNone(mem.record_change(self.desk, MID, 'nouveau_courriel', 'courriel', 'm1', 'v1', 'Courriel'))
        self.assertIsNone(mem.record_change(self.desk, MID, 'nouveau_courriel', 'courriel', 'm1', 'v1', 'Courriel'))
        self.assertEqual(first.get('inchange'), None)

    def test_nouvelle_version_mise_en_forme_seule_et_montant_modifie(self):
        md.rebuild(self.desk, MID)
        mem.mark_read(self.desk, MID, 'avocat@example.test')
        mem.index_draft(self.desk, MID, 'travail-1', [{'texte': 'Le preneur doit 1 000 € de loyers impayés.'}, {'texte': 'Sans montant.'}])
        idx = DocumentIndex(self.f.c['state_dir'])
        idx.db.execute("UPDATE docs SET etag='e-nouveau' WHERE matter=? AND path=?", (MID, DOCS[2][0]))   # même texte : technique
        idx.db.commit()
        idx.db.close()
        _digest(self.desk, DOCS[3][0], {'nature': 'Facture', 'montants': [{'montant': '1 250 €', 'devise': 'EUR', 'valeur': '1250', 'role': 'demande_client',
                                                                           'objet': 'loyers impayés', 'extrait': '1 250'}]}, 'f2')
        md.rebuild(self.desk, MID)
        news = mem.since_last_read(self.desk, MID, 'avocat@example.test')
        kinds = [c['kind'] for g in news['groups'] for c in g['items']]
        self.assertIn('montant_modifie', kinds)
        self.assertIn('projet_a_recontroler', kinds)
        self.assertNotIn('nouvelle_version', kinds)                                  # mise en forme seule : journal technique
        self.assertIn('nouvelle_version', [c['kind'] for c in mem.changes(self.desk, MID, 0, True)])
        proj = next(c for g in news['groups'] for c in g['items'] if c['kind'] == 'projet_a_recontroler')
        self.assertEqual(proj['detail']['projets'][0]['passages'][0]['paragraphe'], 1)
        self.assertEqual(len(news['groups']), 1)                                     # arrivées rapprochées : un seul bilan


# ---------------------------------------------------------------------------------------------------- A02, A04
class Memory(Base):
    def test_argument_abandonne_et_versions_des_decisions(self):
        md.rebuild(self.desk, MID)
        d1 = mem.save_decision(self.desk, MID, 'avocat@example.test', {'kind': 'argument_abandonne', 'objet': 'clause pénale excessive',
                                                                       'texte': 'Ne plus soutenir la réduction de la clause pénale excessive', 'motif': 'jurisprudence défavorable'})
        self.assertIsNotNone(mem.is_abandoned(self.desk, MID, 'La clause pénale est manifestement excessive et doit être réduite'))
        ctx = contexte5651.for_drafting(self.desk, MID)
        self.assertEqual(ctx['arguments_abandonnes_ne_pas_reprendre'][0]['motif'], 'jurisprudence défavorable')
        c1 = mem.save_decision(self.desk, MID, 'avocat@example.test', {'kind': 'consigne', 'objet': 'ton', 'texte': 'Ton conciliant avec le bailleur'})
        mem.save_decision(self.desk, MID, 'avocat@example.test', {'kind': 'consigne', 'objet': 'ton', 'texte': 'Ton ferme'})
        self.assertEqual(len(mem.conflicts(self.desk, MID)), 1)                      # présenté, jamais arbitré en silence
        new = mem.save_decision(self.desk, MID, 'avocat@example.test', {'kind': 'consigne', 'texte': 'Ton ferme mais courtois', 'remplace': c1, 'version': 1})
        rows = {d['id']: d for d in mem.decisions(self.desk, MID, active_only=False)}
        self.assertEqual((rows[c1]['status'], rows[new]['version'], rows[new]['replaces']), ('remplacee', 2, c1))
        with self.assertRaisesRegex(Stop, 'version_en_conflit'):
            mem.save_decision(self.desk, MID, 'avocat@example.test', {'kind': 'consigne', 'texte': 'Autre', 'remplace': c1, 'version': 1})
        md.rebuild(self.desk, MID, force=True)
        self.assertIn(d1, [d['id'] for d in mem.decisions(self.desk, MID)])          # une réanalyse n'efface rien

    def test_lacunes_bornees(self):
        g = mem.open_gap(self.desk, MID, 'Décompte définitif des loyers', 'nécessaire au dispositif', blocking='paragraphe')
        self.assertEqual(mem.open_gap(self.desk, MID, 'décompte définitif des loyers'), g)   # une seule lacune
        self.assertEqual(mem.gaps(self.desk, MID)[0]['attempts'], 1)
        mem.resolve_gap(self.desk, MID, g, 'avocat@example.test', '1 250 €')
        self.assertEqual(mem.gaps(self.desk, MID), [])
        with self.assertRaisesRegex(Stop, 'portee_lacune_invalide'):
            mem.open_gap(self.desk, MID, 'x' * 5, blocking='tout')


# ---------------------------------------------------------------------------------------------------- A06 file unique
class Queue(Base):
    def test_un_objet_une_decision_revision_controlee(self):
        md.rebuild(self.desk, MID)
        md.add_party(self.desk, MID, 'avocat@example.test', 'M. Martin')
        items = file5651.items(self.desk, '', MID)
        classes = [i['class'] for i in items]
        self.assertEqual(classes.count('classement'), 1)                             # un élément groupé, pas une carte par document
        self.assertEqual(classes.count('informations'), 1)
        ident = next(i for i in items if i['class'] == 'identite')
        self.assertEqual(ident['key'], next(i for i in file5651.items(self.desk, '', None) if i['class'] == 'identite')['key'])   # même objet partout
        info = next(i for i in items if i['class'] == 'informations')
        with self.assertRaisesRegex(Stop, 'version_en_conflit'):
            file5651.decide(self.desk, 'avocat@example.test', info['key'], 'approuve', 'ancienne-revision')
        file5651.decide(self.desk, 'avocat@example.test', info['key'], 'approuve', info['revision'])
        self.assertNotIn(info['key'], [i['key'] for i in file5651.items(self.desk, '', MID)])
        a = md.assertions(self.desk, MID)[0]
        md.review(self.desk, MID, a['id'], 'avocat@example.test', 'a_verifier', {})
        again = next(i for i in file5651.items(self.desk, '', MID) if i['class'] == 'informations')   # objet modifié : nouvelle révision
        self.assertNotEqual(again['revision'], info['revision'])
        with self.assertRaisesRegex(Stop, 'selection_heterogene'):
            file5651.decide_batch(self.desk, 'avocat@example.test', [{'key': ident['key'], 'revision': ident['revision']},
                                                                     {'key': again['key'], 'revision': again['revision']}], 'approuve')
        self.assertTrue(file5651.history(self.desk, MID))


# ---------------------------------------------------------------------------------------------------- A01, A07 missions
class Missions(Base):
    def test_double_clic_rejoint_et_nouvelle_tentative_apres_arret(self):
        data = {'matter': MID, 'objectif': 'audience', 'consigne': 'Préparer l’audience du 20 novembre', 'click_key': 'a' * 32}
        m1 = mi.start(self.desk, 'avocat@example.test', data)
        m2 = mi.start(self.desk, 'avocat@example.test', data)
        self.assertEqual(m1['id'], m2['id'])
        self.assertTrue(m2['rejointe'])
        m3 = mi.start(self.desk, 'avocat@example.test', {**data, 'click_key': 'b' * 32})   # même demande, mission active : rejointe
        self.assertEqual(m3['id'], m1['id'])
        self.assertEqual(m1['parcours'], 'audience')
        taches5614.control(self.desk, {'id': m1['id'], 'action': 'cancel'}, 'avocat@example.test')
        m4 = mi.start(self.desk, 'avocat@example.test', {**data, 'click_key': 'c' * 32})
        self.assertNotEqual(m4['id'], m1['id'])
        self.assertEqual(m4['tentative'], 2)
        with self.assertRaisesRegex(Stop, 'fichier_hors_dossier'):
            mi.start(self.desk, 'avocat@example.test', {**data, 'fichiers': ['/Dossiers/AUTRE/x.pdf'], 'click_key': 'd' * 32})
        self.assertIn('Prendre en charge une mission', mi.panel_html(MID, [{'path': DOCS[0][0], 'name': 'Assignation'}]))

    def test_preparation_proactive_report_rattrapage_pause(self):
        from agent import echeances450 as ech
        ech.ensure_schema(self.desk) if hasattr(ech, 'ensure_schema') else None
        today = date.today()
        due = (today + timedelta(days=5)).isoformat()
        self.desk.db.execute("INSERT INTO deadlines450(id,matter,rule_id,start_event,start_date,due,status,origin,created,updated) VALUES(?,?,?,?,?,?,?,?,?,?)",
                             ('dl1', MID, 'appel', 'jugement', today.isoformat(), due, 'confirmee', 'manuel', today.isoformat(), today.isoformat()))
        self.desk.db.commit()
        self.assertEqual(mi.run_due(self.desk, MID), [])                              # « À la demande » : rien d'automatique
        mi.set_mode(self.desk, MID, 'avocat@example.test', {'mode': 'preparation_automatique', 'paliers': '14,7,2'})
        launched = mi.run_due(self.desk, MID)
        self.assertEqual(len(launched), 1)                                           # J-14 et J-7 échus : un seul rattrapage
        states = {p['palier']: p['state'] for p in mi.plan(self.desk, MID)}
        self.assertEqual(states, {14: 'sautee', 7: 'lancee', 2: 'prevue'})
        self.assertEqual(mi.run_due(self.desk, MID), [])                              # aucun double effet
        self.desk.db.execute("UPDATE deadlines450 SET due=? WHERE id='dl1'", ((today + timedelta(days=30)).isoformat(),))   # report
        self.desk.db.commit()
        preps = mi.plan(self.desk, MID)
        self.assertTrue(all(p['state'] in ('annulee', 'lancee', 'sautee') for p in preps if p['event_date'] == due))
        self.assertTrue(any(p['state'] == 'prevue' and p['event_date'] != due for p in preps))
        self.assertEqual(sum(1 for p in preps if p['state'] == 'lancee'), 1)       # résultat déjà produit conservé
        from agent import pause5629
        self.desk.db.execute("UPDATE deadlines450 SET due=? WHERE id='dl1'", ((today + timedelta(days=1)).isoformat(),))
        self.desk.db.commit()
        pause5629.set_pause(self.desk, MID, True)
        mi.run_due(self.desk, MID)
        self.assertIn('bloquee', [p['state'] for p in mi.plan(self.desk, MID)])


# ---------------------------------------------------------------------------------------------------- A05 relecture Word
W = 'xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main"'


def _docx(paragraphs_xml):
    # 5.6.55 : date fixe dans l'archive ; deux appels à cheval sur une seconde paire donnaient des octets différents (recette 5.6.54)
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, 'w') as z:
        z.writestr(zipfile.ZipInfo('[Content_Types].xml', (2026, 1, 1, 0, 0, 0)),
                   '<?xml version="1.0"?><Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types"/>')
        z.writestr(zipfile.ZipInfo('word/document.xml', (2026, 1, 1, 0, 0, 0)),
                   '<?xml version="1.0"?><w:document %s><w:body>%s</w:body></w:document>' % (W, paragraphs_xml))
    return buf.getvalue()


P1 = '<w:p><w:pPr><w:pStyle w:val="Titre1"/><w:numPr><w:ilvl w:val="0"/></w:numPr></w:pPr><w:r><w:rPr><w:b/></w:rPr><w:t>Le preneur doit 1 000 euros.</w:t></w:r></w:p>'
P2 = '<w:p><w:r><w:t>Le bailleur conteste.</w:t></w:r></w:p>'
P3 = '<w:tbl><w:tr><w:tc><w:p><w:r><w:rPr><w:i/></w:rPr><w:t>Cellule à revoir</w:t></w:r></w:p></w:tc></w:tr></w:tbl>'
P4 = '<w:p><w:r><w:t>Voir note</w:t></w:r><w:r><w:footnoteReference w:id="1"/></w:r></w:p>'


class FakeModel:
    def __init__(self, props):
        self.props = props

    def complete(self, messages, temperature=None, max_tokens=0, json_schema=None):
        payload = json.loads(messages[-1]['content'])
        wanted = {p['index'] for p in payload['paragraphes']}
        return json.dumps({'propositions': [p for p in self.props if p['index'] in wanted]})


class Review(Base):
    def setUp(self):
        super().setUp()
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        root = Path(self.tmp.name)
        (root / 'Dossiers/DEMO/Travaux').mkdir(parents=True)
        (root / 'Dossiers/DEMO/Travaux/Conclusions.docx').write_bytes(_docx(P1 + P2 + P3 + P4))
        self.client = LocalFolder({'local_path': str(root), 'roots': ['/Dossiers']})
        self.path = ROOT + '/Travaux/Conclusions.docx'

    def test_revisions_natives_mise_en_forme_conservee(self):
        stamp, counter = '2026-10-15T10:00:00Z', [1]
        new = rl.tracked_paragraph(P1, 'Le preneur doit 1 250 euros.', 'Me Exemple', stamp, counter)
        self.assertIn('<w:pStyle w:val="Titre1"/><w:numPr>', new)                    # style et numérotation conservés
        self.assertIn('<w:del ', new)
        self.assertIn('<w:delText xml:space="preserve">000 </w:delText>', new)        # seuls les mots qui changent
        self.assertIn('<w:t xml:space="preserve">250 </w:t>', new)
        self.assertIn('<w:b/>', new)
        self.assertIsNone(rl.tracked_paragraph(P4, 'Voir la note', 'Me', stamp, counter))   # renvoi de note : jamais réécrit
        data, applied, manual, stale = rl.apply_to_docx(_docx(P1 + P3), [
            {'id': 'x', 'anchor': rl.paragraphs(_docx(P1 + P3))[1]['anchor'], 'occurrence': 0, 'expected': 'Cellule à revoir', 'replacement': 'Cellule revue'}])
        self.assertEqual((applied, manual, stale), (['x'], [], []))
        xml = zipfile.ZipFile(io.BytesIO(data)).read('word/document.xml').decode()
        self.assertIn('<w:tc><w:p><w:r><w:rPr><w:i/></w:rPr>', xml)                 # cellule et mise en forme conservées

    def test_parcours_complet_accepter_refuser_modifier_appliquer(self):
        md.rebuild(self.desk, MID)
        s = rl.start(self.desk, MID, 'avocat@example.test', self.path, 'Actualiser le montant et adoucir le ton', client=self.client)
        self.assertEqual(rl.start(self.desk, MID, 'avocat@example.test', self.path, 'Actualiser le montant et adoucir le ton', client=self.client)['id'], s['id'])
        model = FakeModel([{'index': 0, 'remplacement': 'Le preneur doit 1 250 euros.', 'motif': 'nouvelle facture', 'nature': 'fait', 'sources': ['Facture 2024-03.pdf']},
                           {'index': 1, 'remplacement': 'Le bailleur conteste formellement.', 'motif': 'ton', 'nature': 'style'},
                           {'index': 2, 'remplacement': 'Cellule revue', 'motif': 'précision', 'nature': 'style'},
                           {'index': 99, 'remplacement': 'hors périmètre', 'motif': 'x', 'nature': 'style'}])
        out = rl.run(self.desk, {'session': s['id']}, model=model, client=self.client)
        self.assertEqual(out['propositions'], 3)                                     # l'index inconnu est écarté
        props = {p['para_index']: p for p in rl.session(self.desk, MID, s['id'])['proposals']}
        rl.decide(self.desk, MID, props[0]['id'], 'avocat@example.test', 'accepter', props[0]['version'])
        rl.decide(self.desk, MID, props[1]['id'], 'avocat@example.test', 'refuser', props[1]['version'])
        rl.decide(self.desk, MID, props[2]['id'], 'avocat@example.test', 'accepter', props[2]['version'])
        edited = rl.decide(self.desk, MID, props[2]['id'], 'avocat@example.test', 'modifier', props[2]['version'], 'Cellule revue et complétée')
        self.assertEqual((edited['state'], edited['accepted_version']), ('proposee', 0))   # modification : ancienne acceptation invalidée
        with self.assertRaisesRegex(Stop, 'version_en_conflit'):
            rl.decide(self.desk, MID, props[2]['id'], 'avocat@example.test', 'accepter', props[2]['version'])
        result = rl.apply(self.desk, MID, s['id'], 'avocat@example.test', client=self.client)
        self.assertEqual(result['applied'], 1)
        self.assertTrue(result['path'].startswith(ROOT + '/Travaux/Conclusions - relecture AxiorHub'))
        original = self.client.download({'path': self.path})
        self.assertEqual(original, _docx(P1 + P2 + P3 + P4))                          # original intact
        xml = zipfile.ZipFile(io.BytesIO(self.client.download({'path': result['path']}))).read('word/document.xml').decode()
        self.assertIn('Le bailleur conteste.', xml)                                  # refus : passage d'origine conservé
        self.assertIn('<w:ins ', xml)
        states = {p['para_index']: p['state'] for p in rl.session(self.desk, MID, s['id'])['proposals']}
        self.assertEqual(states, {0: 'acceptee', 1: 'refusee', 2: 'proposee'})   # 5.6.52 (F07) : la décision reste distincte de l'export
        self.assertEqual(rl.session(self.desk, MID, s['id'])['proposals'][0]['exports'], [1])
        self.assertTrue(rl.apply(self.desk, MID, s['id'], 'avocat@example.test', client=self.client)['deja_cree'])   # second clic idempotent

    def test_texte_modifie_entre_temps_proposition_perimee(self):
        accepted = [{'id': 'p', 'anchor': rl.paragraphs(_docx(P2))[0]['anchor'], 'occurrence': 0, 'expected': 'Le bailleur conteste.', 'replacement': 'Autre'}]
        _, applied, manual, stale = rl.apply_to_docx(_docx('<w:p><w:r><w:t>Le bailleur conteste vivement.</w:t></w:r></w:p>'), accepted)
        self.assertEqual((applied, stale), ([], ['p']))


class Recital(Base):
    def test_rappel_redige_cite_seulement_des_informations_connues(self):
        from agent import recit5651
        md.rebuild(self.desk, MID)
        known = list(recit5651._items(self.desk, MID, 'faits'))

        class M:
            name = 'modele-fictif'

            def complete(self, messages, **kw):
                return json.dumps({'paragraphes': [{'texte': 'Selon la SARL Alpha Fictive, le bail a été conclu le 3 janvier 2024.', 'informations': [known[0], 'inconnu']},
                                                   {'texte': 'Paragraphe inventé sans source.', 'informations': ['X999']}]})
        out = recit5651.run(self.desk, {'matter': MID, 'kind': 'faits'}, model=M())
        self.assertEqual(out['paragraphes'], 1)                                      # paragraphe sans information identifiée écarté
        rec = vue5651.narrative(self.desk, MID, 'faits')
        self.assertEqual(rec['paragraphs'][0]['informations'], [known[0]])
        self.assertFalse(rec['stale'])
        md.add_assertion(self.desk, MID, 'avocat@example.test', {'texte': 'Nouvelle information fictive.'})
        self.assertTrue(vue5651.narrative(self.desk, MID, 'faits')['stale'])         # informations modifiées : à actualiser


# ---------------------------------------------------------------------------------------------------- A08 apprentissage
class Learning(Base):
    def test_portees_categories_expurgation_desactivation(self):
        md.rebuild(self.desk, MID)
        with self.assertRaisesRegex(Stop, 'correction_factuelle_hors_style'):
            ap.learn(self.desk, 'avocat@example.test', {'portee': 'style', 'categorie': 'exactitude', 'matter': MID, 'apres': 'x'})
        style = ap.learn(self.desk, 'avocat@example.test', {'portee': 'style', 'categorie': 'style', 'matter': MID,
                                                            'avant': 'Madame Alice Dupont doit 1 000 euros depuis le 03/01/2024.',
                                                            'apres': 'Madame Alice Dupont reste redevable de 1 000 euros depuis le 03/01/2024.'})
        self.assertNotIn('Dupont', style['after'])
        self.assertNotIn('1 000', style['after'])
        self.assertEqual(style['matter'], '')                                        # l'exemple de style quitte le dossier
        local = ap.learn(self.desk, 'avocat@example.test', {'portee': 'dossier', 'categorie': 'strategie', 'matter': MID,
                                                            'consigne': 'Ne pas invoquer la force majeure dans ce dossier'})
        self.assertTrue(local['rule410'])
        rules = [r['consigne'] for r in ap.rules_for(self.desk, MID, 'avocat@example.test')]
        self.assertIn('Ne pas invoquer la force majeure dans ce dossier', rules)
        self.assertEqual(ap.rules_for(self.desk, 'DOS-AUTRE', 'autre@example.test'), [])   # un autre dossier, un autre compte : rien
        with self.assertRaisesRegex(Stop, 'regle_absente'):                         # 5.6.52 (F01) : dossier attendu obligatoire
            ap.set_status(self.desk, local['id'], 'avocat@example.test', 'desactivee', expected_matter='DOS-AUTRE')
        ap.set_status(self.desk, local['id'], 'avocat@example.test', 'desactivee', expected_matter=MID)
        self.assertNotIn('Ne pas invoquer la force majeure dans ce dossier', [r['consigne'] for r in ap.rules_for(self.desk, MID, 'avocat@example.test')])
        status = self.desk.db.execute('SELECT status FROM business_rules_v410 WHERE id=?', (local['rule410'],)).fetchone()[0]
        self.assertEqual(status, 'paused')
        with self.assertRaisesRegex(Stop, 'role_insuffisant'):
            ap.set_status(self.desk, style['id'], 'autre@example.test', 'desactivee')

    def test_campagnes_comparables_sur_les_memes_cas(self):
        for i in range(10):
            ap.add_case(self.desk, 'exactitude', 'faits', 'Entrée %d' % i, 'Le loyer est de %d euros' % (1000 + i), reserved=True)
        good = ap.evaluate(self.desk, 'modèle A', lambda c: c['expected'], 'modele-a', {'temperature': 0})
        bad = ap.evaluate(self.desk, 'modèle B', lambda c: 'rien', 'modele-b', {'temperature': 0})
        self.assertEqual((good['passed'], bad['passed']), (10, 0))
        runs = [r[0] for r in self.desk.db.execute('SELECT id FROM d51_eval_runs ORDER BY id')]
        cmp = ap.compare_runs(self.desk, runs[0], runs[1])
        self.assertEqual((cmp['common_cases'], len(cmp['regressions'])), (10, 10))


# ---------------------------------------------------------------------------------------------------- page et API (application réelle)
class Page(t51.Web):
    def setUp(self):
        super().setUp()
        self.admin = self.admin_cookie()
        from agent import membres5650 as mb
        mb.clear()
        self.addCleanup(mb.clear)

    def test_six_onglets_bandeau_et_droits(self):
        desk = Desk(self.f.c)
        self.addCleanup(desk.db.close)
        idx = DocumentIndex(self.f.c['state_dir'])
        idx.db.execute('INSERT OR REPLACE INTO docs VALUES (?,?,?,?,?,?)', ('DOS-001', '/Dossiers/DEMO/Contrat.pdf', 'e1', '2026-09-01', 'CONTRAT DE BAIL', ''))
        idx.db.commit()
        idx.db.close()
        page = self.request('/dossier', query='id=DOS-001', cookie=self.admin)['body']
        # 5.6.79 : cinq onglets (Synthèse, Documents, Faits et procédure, Échanges, Travail) ; anciennes vues toujours servies
        for label in ('Synthèse', 'Documents', 'Faits et procédure', 'Échanges', 'Travail', 'Prendre en charge une mission',
                      'Depuis votre dernière lecture', 'À décider / À relire'):
            self.assertIn(label, page)
        self.assertIn('Nos objectifs et consignes', self.request('/dossier', query='id=DOS-001&vue=faits', cookie=self.admin)['body'])
        self.assertNotIn('Ancienne page (surveillance, mémoire, réglages)', page)
        for view in ('faits', 'procedure', 'pieces', 'correspondances', 'travaux', 'fiche', 'courriels'):
            r = self.request('/dossier', query='id=DOS-001&vue=' + view, cookie=self.admin)
            self.assertEqual(r['status'], '200 OK', view)
        assistant = self.add(self.admin, 'assistant@example.test', 'assistant')
        doc = md.documents(desk, 'DOS-001')[0]
        ok = self.api('m5651/document/classer', {'matter': 'DOS-001', 'path': doc['path'], 'nature': 'contrat', 'version': doc['version']}, assistant)
        self.assertTrue(ok['status'].startswith('200'), ok['body'])
        refused = self.api('m5651/information/ajouter', {'matter': 'DOS-001', 'texte': 'Information fictive', 'kind': 'fait'}, assistant)
        self.assertTrue(refused['status'].startswith('403'), refused['body'])
        lawyer = self.add(self.admin, 'avocat@example.test', 'avocat')
        added = self.api('m5651/information/ajouter', {'matter': 'DOS-001', 'texte': 'Information fictive', 'kind': 'fait'}, lawyer)
        self.assertTrue(added['status'].startswith('200'), added['body'])
        info = json.loads(added['body'])['id']
        stale = self.api('m5651/information', {'matter': 'DOS-001', 'id': info, 'action': 'refuser', 'version': 99}, lawyer)
        self.assertTrue(stale['status'].startswith('409'), stale['body'])
        from agent import membres5650 as mb
        mb.set_reserve(str(self.state), 'DOS-001', ['autre@example.test'])
        blocked = self.api('m5651/information', {'matter': 'DOS-001', 'id': info, 'action': 'refuser', 'version': 1}, lawyer)
        self.assertIn(blocked['status'][:3], ('403', '404'))
        self.assertNotIn('Information fictive', self.request('/dossier', query='id=DOS-001&vue=faits', cookie=lawyer)['body'])

    def test_mission_par_l_api_et_file_sur_aujourdhui(self):
        lawyer = self.add(self.admin, 'avocat@example.test', 'avocat')
        body = {'matter': 'DOS-001', 'objectif': 'autre', 'consigne': 'Note de synthèse fictive', 'click_key': 'f' * 32, 'fichiers': ''}
        first = json.loads(self.api('m5651/mission', body, lawyer)['body'])
        second = json.loads(self.api('m5651/mission', body, lawyer)['body'])
        self.assertEqual(first['mission'], second['mission'])
        self.assertTrue(second['rejointe'])
        assistant = self.add(self.admin, 'assistant@example.test', 'assistant')
        self.assertTrue(self.api('m5651/mission', {**body, 'click_key': 'e' * 32}, assistant)['status'].startswith('403'))
        desk = Desk(self.f.c)
        self.addCleanup(desk.db.close)
        md.ensure_schema(desk)
        md.add_assertion(desk, 'DOS-001', 'avocat@example.test', {'texte': 'Information fictive à examiner'})
        desk.db.execute("UPDATE d51_assertions SET review='propose'")
        desk.db.commit()
        today = self.request('/aujourdhui', cookie=self.admin)['body']
        self.assertIn('À décider / À relire', today)
        # 5.6.64 : la tenue des fiches n'encombre plus Aujourd'hui ; résumée en bas de la file, traitée depuis le dossier
        self.assertNotIn('information(s) proposée(s) à examiner', today)
        self.assertIn('point(s) de fiches de dossiers', today)
        dossier = self.request('/dossier', query='id=DOS-001', cookie=self.admin)['body']
        self.assertIn('information(s) proposée(s) à examiner', dossier)

    def test_pagination_au_dela_de_500_informations(self):
        desk = Desk(self.f.c)
        self.addCleanup(desk.db.close)
        md.ensure_schema(desk)
        for i in range(520):
            md.add_assertion(desk, 'DOS-001', 'avocat@example.test', {'texte': 'Information fictive numéro %d' % i})
        body = self.request('/dossier', query='id=DOS-001&vue=informations', cookie=self.admin)['body']   # 5.6.79 : liste détaillée
        self.assertIn('Page 1 / 11 (520 éléments)', body)
        last = self.request('/dossier', query='id=DOS-001&vue=informations&page=11', cookie=self.admin)['body']
        self.assertIn('Page 11 / 11', last)
        self.assertEqual(last.count('class="d51-info"'), 20)


if __name__ == '__main__':
    unittest.main()
