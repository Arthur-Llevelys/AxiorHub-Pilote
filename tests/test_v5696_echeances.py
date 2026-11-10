"""5.6.96, lot échéances (D01-D07) : qualification de l'acte avant calcul, aucune écriture pour une proposition, écarter avec motif
sur les deux écrans, décision interne séparée de l'agenda, traçabilité et empreinte, contrôle en lecture seule.

Données fictives (DURAND, MARTIN, ZORVAL, DOS-001, RG 24/01234, Valbrune, example.test), faux agenda, faux modèle : aucun réseau.
Les dates sont relatives au jour ; la seule date fixe est la frontière légale du régime des appels (1er septembre 2024).
"""
from datetime import date, timedelta
import hashlib
import json
import os
from pathlib import Path
import re
import sqlite3
import subprocess
import sys
import unittest
from unittest.mock import patch

from agent import autonomy480, contentieux_web5689 as cweb, deadlines450 as moteur, delais5689 as delais, echeances450 as ech, procedure568
from agent.common import Stop
from agent.notices440 import MONTH_NAMES
from test_v450_echeances import Base, FakeDAV

J = date.today()
MID = 'DOS-001'
AVOCAT = ('claire@example.test', 'avocat')
DECL = J - timedelta(days=90)                      # déclaration d'appel du dossier fictif
NOTIF = J - timedelta(days=20)                     # notification des conclusions de l'appelant
ARRET = date(DECL.year - 10, DECL.month, min(DECL.day, 28))   # arrêt cité, dix ans plus ancien que l'appel
COMPLET_909 = {'regime_commun': 'oui', 'siege_juridiction': 'metropole', 'lieu_partie_915_4': 'metropole', 'circuit': 'mise_en_etat',
               'interruption_915_3': 'non', 'aide_juridictionnelle': 'non', 'date_declaration_appel': DECL.isoformat()}
CH_FOND = '/Dossiers/DEMO/Notification conclusions appelant.pdf'
CH_INCIDENT = '/Dossiers/DEMO/Conclusions incident intimee.pdf'
CH_CALENDRIER = '/Dossiers/DEMO/Ordonnance calendrier.pdf'


def fr(d):
    return '%d %s %d' % (d.day, MONTH_NAMES[d.month - 1], d.year)


INCIDENT = ("CONCLUSIONS D'INCIDENT\nÀ Monsieur le conseiller de la mise en état de la cour d'appel de Valbrune\nRG 24/01234\n"
            "Pour la société ZORVAL, intimée, contre Madame DURAND, appelante.\n"
            "Le conseiller de la mise en état est saisi par des conclusions spécialement adressées. "
            "Les conclusions de l'appelant notifiées le %s seraient tardives au regard de l'arrêt du %s (Cass. 2e civ., n° 14-12.345)." % (fr(NOTIF), fr(ARRET)))
FOND = ("NOTIFICATION ENTRE AVOCATS\nCour d'appel de Valbrune, RG 24/01234.\n"
        "Les conclusions de l'appelant ont été notifiées par RPVA le %s à Maître MARTIN, avocat de l'intimée." % fr(NOTIF))
ARRET_CITE = ("CONCLUSIONS D'APPELANT\nCour d'appel de Valbrune, RG 24/01234.\n"
              "Les conclusions de l'appelant rappellent l'arrêt du %s (pourvoi n° 14-12.345) sur la caducité." % fr(ARRET))
HORS_PHRASE = ("Les conclusions de l'appelant ont été notifiées par RPVA. La réunion du cabinet a eu lieu le %s." % fr(J - timedelta(days=10)))
SANS_ACTION = ("Les conclusions de l'appelant, signées par Maître MARTIN, critiquent le jugement rendu le %s." % fr(J - timedelta(days=200)))
CALENDRIER = ("ORDONNANCE DU CONSEILLER DE LA MISE EN ÉTAT\nCour d'appel de Valbrune, RG 24/01234.\nLe calendrier de procédure est remplacé : "
              "les conclusions de l'appelant ont été notifiées le %s ; l'intimée conclura avant le %s." % (fr(NOTIF), fr(J + timedelta(days=60))))
REGLE_SPECIALE = FOND + " En application de l'art. 912 du code de procédure civile, le conseiller fixe les dates."
APPEL_INCIDENT = ("CONCLUSIONS D'INTIMÉ PORTANT APPEL INCIDENT\nCour d'appel de Valbrune, RG 24/01234.\n"
                  "Les conclusions de l'intimé portant appel incident ont été notifiées le %s à l'appelante." % fr(NOTIF))
BREF = "Avis de fixation à bref délai (article 905) reçu le %s par le cabinet." % fr(NOTIF)


class Agenda(FakeDAV):
    """Faux agenda : écritures et suppressions comptées, lecture avec ETag et calendrier d'origine, coupure et version simulables."""

    CAL = 'https://cloud.example.test/remote.php/dav/calendars/claire/agenda/'

    def __init__(self):
        super().__init__()
        self.deletes, self.lectures = [], 0
        self.panne = False
        self.etag_change = False

    def calendar_url(self, href):
        return self.CAL

    def events(self, urls, start, end, tz):
        self.lectures += 1
        if self.panne:
            raise Stop('http_503')
        return [dict(e, etag='"v1-%s"' % uid[9:17], source_url=self.CAL) for uid, e in self.store.items()]

    def delete_event(self, calendar, uid, etag=''):
        if self.panne:
            raise Stop('http_503')
        if self.etag_change:
            raise Stop('http_412')
        self.deletes.append((calendar, uid, etag))
        self.store.pop(uid, None)
        return uid


class ModeleFaux:
    """Répond une liste d'événements fixe ; compte les appels. Aucune sortie réseau."""

    def __init__(self, evenements):
        self.evenements, self.appels = evenements, 0

    def complete(self, messages, **kw):
        self.appels += 1
        return json.dumps({'evenements': self.evenements}, ensure_ascii=False)


class Fichier:
    def __init__(self, texte):
        self.texte = texte

    def stat(self, path):
        return {'path': path, 'etag': '"f1"', 'size': len(self.texte)}

    def download(self, info):
        return self.texte.encode('utf-8')


class Lot(Base):
    def setUp(self):
        super().setUp()
        self.dav = Agenda()
        self.n = 0
        autonomy480.set_level(self.desk, 'agenda_echeance', 'agir', confirm='yes')

    def cle(self):
        self.n += 1
        return 'cle-essai-%04d' % self.n

    def analyse(self, texte, chemin=CH_FOND, etag='"e1"'):
        return ech.analyze(self.desk, {'matter': MID, 'path': chemin, 'etag': etag}, dav=self.dav, extractor=lambda raw, name: texte, today=J)

    def candidats(self, tous=False):
        return delais.liste(self.desk, MID, tous)

    def ancienne_proposition(self, texte=FOND, chemin=CH_FOND, start=None):
        """Ligne « à confirmer » créée par une version antérieure (avant 5.6.96, la détection écrivait au suivi, avec l'extrait de l'indice)."""
        trouves = ech.extract_start_events(texte, J)
        extrait = trouves[0]['context'] if trouves else texte[-300:]
        return ech.create_deadline(self.desk, MID, 'conclusions_intime', (start or NOTIF).isoformat(), regime_date=DECL.isoformat(),
                                   source_path=chemin, excerpt=extrait)

    def confirmee(self):
        return ech.create_manual(self.desk, self.dav, MID, 'conclusions_intime', NOTIF.isoformat(), regime_date=DECL.isoformat())

    def journal(self, ident):
        return [dict(r) for r in self.desk.db.execute('SELECT * FROM deadline_journal450 WHERE deadline_id=? ORDER BY id', (ident,))]


# ------------------------------------------------------------------------------------------------ D01 qualification avant calcul
class D01Qualification(Lot):
    def test_D01_D07_acte_incident_citant_un_arret_ancien_aucune_echeance_aucun_put(self):
        q = ech.qualifier_acte(INCIDENT, CH_INCIDENT)
        self.assertEqual((q['type_acte'], q['incident'], q['instance']), ('conclusions_incident', True, 'appel'))
        self.assertEqual(q['destinataire'], 'conseiller de la mise en etat')
        self.assertIn('24/01234', q['rg'])
        self.assertIn(ARRET.isoformat(), [d['date'] for d in q['decisions_citees']])
        self.assertEqual(ech.extract_start_events(INCIDENT, J, CH_INCIDENT), [])
        result = self.analyse(INCIDENT, CH_INCIDENT)
        self.assertEqual((result['propositions'], result['echeances_creees']), (0, 0))
        self.assertEqual(self.candidats(True), [])
        self.assertEqual(ech.listing(self.desk, include_closed=True), [])
        self.assertEqual((self.dav.puts, self.dav.deletes, autonomy480.pending_list(self.desk)), (0, [], []))
        actes = ech.actes_consignes(self.desk, MID)
        self.assertEqual([(a['path'], a['type_acte'], a['mention']) for a in actes], [(CH_INCIDENT, 'conclusions_incident', ech.MENTION_SANS_FOND)])
        self.assertIn(ech.MENTION_SANS_FOND, result['message'])

    def test_D01_arret_cite_dans_des_conclusions_n_est_jamais_un_evenement(self):
        self.assertEqual(ech.extract_start_events(ARRET_CITE, J), [])
        self.assertEqual(self.analyse(ARRET_CITE, '/Dossiers/DEMO/Conclusions appelant.pdf')['propositions'], 0)
        self.assertEqual(self.candidats(True), [])
        # Cass. puis date, sans « arrêt du » : la date de l'arrêt n'est pas reprise, la notification qui suit l'est
        texte = "Comme jugé (Cass. 2e civ., %s, n° 14-12.345), les conclusions de l'appelant notifiées le %s sont recevables." % (fr(ARRET), fr(NOTIF))
        self.assertEqual([(e['event'], e['date']) for e in ech.extract_start_events(texte, J)], [('conclusions_appelant', NOTIF)])

    def test_D01_date_cherchee_dans_la_phrase_de_l_indice_seulement(self):
        self.assertEqual(ech.extract_start_events(HORS_PHRASE, J), [])

    def test_D01_conclusions_exigent_l_action_de_notification(self):
        self.assertEqual(ech.extract_start_events(SANS_ACTION, J), [])
        for texte in ("Les conclusions de l'appelant doivent être notifiées avant le %s." % fr(NOTIF),
                      "Les conclusions de l'appelant n'ont pas été notifiées le %s." % fr(NOTIF)):
            self.assertEqual(ech.extract_start_events(texte, J), [], texte)
        found = ech.extract_start_events(FOND, J, CH_FOND)
        self.assertEqual([(e['event'], e['date'], e['nature_date']) for e in found], [('conclusions_appelant', NOTIF, 'notification')])
        actif = "L'appelant a notifié ses conclusions le %s." % fr(NOTIF)
        self.assertEqual([e['date'] for e in ech.extract_start_events(actif, J)], [NOTIF])
        self.assertTrue(ech.qualifier_acte(FOND, CH_FOND)['preuve_notification'])

    def test_D01_date_anterieure_a_la_declaration_connue_devient_a_verifier(self):
        procedure568.save_case(self.desk, {'matter': MID, 'declaration_date': DECL.isoformat()})
        avant = DECL - timedelta(days=30)
        texte = FOND.replace(fr(NOTIF), fr(avant))
        self.assertEqual(self.analyse(texte)['propositions'], 1)
        cand = self.candidats()[0]
        self.assertEqual((cand['statut'], cand['date_calculee']), ('a_verifier', ''))
        self.assertTrue(any('antérieure à la déclaration' in m for m in cand['calcul']['motifs']))

    def test_D01_D07_regle_speciale_incomplete_devient_a_verifier_avec_la_reference(self):
        q = ech.qualifier_acte(REGLE_SPECIALE, CH_FOND)
        self.assertEqual(q['references_non_calculees'], ['CPC art. 912'])
        self.analyse(REGLE_SPECIALE)
        cand = self.candidats()[0]
        self.assertEqual(cand['statut'], 'a_verifier')
        self.assertTrue(any('CPC art. 912' in m for m in cand['calcul']['motifs']))
        # une référence que le moteur applique (art. 909) ne rend rien « à vérifier » par elle-même
        self.assertEqual(ech.qualifier_acte(FOND + " Délai de l'art. 909.", CH_FOND)['references_non_calculees'], [])

    def test_D01_D07_calendrier_remplace_consigne_sans_aucun_delai_calcule(self):
        q = ech.qualifier_acte(CALENDRIER, CH_CALENDRIER)
        self.assertEqual((q['type_acte'], q['sans_calcul'], q['incident']), ('ordonnance_calendrier', True, False))
        result = self.analyse(CALENDRIER, CH_CALENDRIER)
        self.assertEqual((result['propositions'], self.dav.puts), (0, 0))
        actes = ech.actes_consignes(self.desk, MID)
        self.assertEqual([a['mention'] for a in actes], [ech.MENTION_CALENDRIER])
        # la date expresse du nouveau calendrier est proposée « à vérifier » avec son passage, jamais inscrite
        self.assertIn((J + timedelta(days=60)).isoformat(), [d['date'] for d in actes[0]['qualification']['dates_expresses']])
        self.assertIn('Date à vérifier : %s' % (J + timedelta(days=60)).isoformat(), cweb._section_delais(self.desk, '', MID, True))

    def test_D01_rg_incoherent_avec_le_dossier_devient_a_verifier(self):
        from agent import modele5651
        modele5651.ensure_schema(self.desk)
        modele5651.save_procedure(self.desk, MID, 'claire@example.test', {'juridiction': "Cour d'appel de Valbrune", 'rg': '25/09876', 'reference': True})
        self.analyse(FOND)
        cand = self.candidats()[0]
        self.assertEqual((cand['statut'], cand['date_calculee']), ('a_verifier', ''))
        self.assertTrue(any('RG de l’acte (24/01234)' in m for m in cand['calcul']['motifs']))

    def test_D01_D07_appel_incident_reel_n_est_pas_un_acte_d_incident(self):
        q = ech.qualifier_acte(APPEL_INCIDENT, '/Dossiers/DEMO/Conclusions intime appel incident.pdf')
        self.assertFalse(q['incident'])
        self.assertEqual(q['type_acte'], 'conclusions_fond')
        # les conclusions de l'intimé ne sont pas celles de l'appelant : aucun délai de réponse proposé à partir d'elles
        self.assertEqual(ech.extract_start_events(APPEL_INCIDENT, J), [])

    def test_D01_D07_bref_delai_propose_sans_date_tant_que_rien_n_est_valide(self):
        found = ech.extract_start_events(BREF, J)
        self.assertEqual([(e['event'], e['nature_date']) for e in found], [('avis_fixation_bref_delai', 'notification')])
        self.analyse(BREF, '/Dossiers/DEMO/Avis de fixation.pdf')
        cand = self.candidats()[0]
        self.assertEqual((cand['evenement'], cand['statut'], cand['date_calculee']), ('avis_fixation_bref_delai', 'a_verifier', ''))
        sans_reception = ech.extract_start_events("Avis de fixation à bref délai (article 905) du %s." % fr(NOTIF), J)
        self.assertEqual([e['nature_date'] for e in sans_reception], ['redaction'])
        self.assertEqual(self.dav.puts, 0)

    def test_D01_D07_changement_de_regime_reste_a_verifier(self):
        self.analyse(FOND)
        cand = self.candidats()[0]
        ancien = dict(COMPLET_909, date_declaration_appel='2024-05-02')     # appel formé avant le 1er septembre 2024
        c = delais.corriger(self.desk, cand['id'], {'cle': self.cle(), 'version': cand['revision'], 'inputs': ancien}, *AVOCAT)
        self.assertEqual((c['statut'], c['date_calculee']), ('a_verifier', ''))
        self.assertTrue(any('ancien régime' in m for m in c['calcul']['motifs']))

    def test_D01_qualification_partagee_par_les_agents_documentaires_et_le_modele(self):
        pages = [{'page': 1, 'label': 'p. 1', 'text': INCIDENT, 'extraction': 'native'}]
        out, warnings = procedure568.source_events(self.desk, pages, {'id': MID, 'client_name': 'Client DEMO'}, CH_INCIDENT, 'sha-incident', persist=True)
        self.assertFalse(any(x['derived'] for x in out))
        self.assertTrue(any(ech.MENTION_SANS_FOND in w for w in warnings))
        self.assertEqual(self.candidats(True), [])
        modele = ModeleFaux([{'evenement': 'conclusions_appelant', 'passage': "conclusions de l'appelant notifiées le %s" % fr(NOTIF), 'date': NOTIF.isoformat()}])
        bilan = delais.travail(self.desk, {'matter': MID, 'path': '/Dossiers/DEMO/Conclusions incident intimee.txt', 'owner': AVOCAT[0], 'role': AVOCAT[1]}, modele, Fichier(INCIDENT))
        self.assertEqual((bilan['etat'], bilan['crees'], modele.appels), ('acte_sans_calcul', 0, 0))     # l'acte d'incident ne va pas au modèle
        # conclusions au fond citant un arrêt : la date de l'arrêt proposée par le modèle est écartée, jamais un candidat
        modele = ModeleFaux([{'evenement': 'conclusions_appelant', 'passage': "rappellent l'arrêt du %s (pourvoi" % fr(ARRET), 'date': ARRET.isoformat()}])
        bilan = delais.travail(self.desk, {'matter': MID, 'path': '/Dossiers/DEMO/Conclusions appelant.txt', 'owner': AVOCAT[0], 'role': AVOCAT[1]}, modele, Fichier(ARRET_CITE))
        self.assertEqual((bilan['crees'], bilan['ecartes'], modele.appels), (0, 1, 1))
        self.assertEqual(self.candidats(True), [])


# ------------------------------------------------------------------------------------------------ D02 aucune écriture pour une proposition
class D02Propositions(Lot):
    def test_D02_detection_ne_cree_ni_echeance_ni_agenda_ni_rappel(self):
        result = self.analyse(FOND)
        self.assertEqual((result['propositions'], result['echeances_creees']), (1, 0))
        self.assertEqual(ech.listing(self.desk, include_closed=True), [])
        self.assertEqual((self.dav.puts, autonomy480.pending_list(self.desk)), (0, []))
        self.assertEqual(ech.run_reminders(self.desk, J), 0)
        self.assertEqual([(c['evenement'], c['date_evenement'], c['statut']) for c in self.candidats()], [('conclusions_appelant', NOTIF.isoformat(), 'a_verifier')])

    def test_D02_proposition_jamais_inscrite_ni_proposee_quel_que_soit_le_niveau(self):
        row = self.ancienne_proposition()
        self.assertEqual(row['status'], 'a_confirmer')
        for niveau in ('agir', 'propose'):
            autonomy480.set_level(self.desk, 'agenda_echeance', niveau, confirm='yes')
            self.assertEqual(ech.sync_calendar(self.desk, self.dav, row['id']), 'non_validee')
        self.assertEqual((self.dav.puts, self.dav.store, autonomy480.pending_list(self.desk)), (0, {}, []))
        self.assertEqual(ech.run_reminders(self.desk, date.fromisoformat(row['due']) - timedelta(days=1)), 0)

    def test_D02_D07_mutation_sans_la_garde_une_proposition_declenche_un_put(self):
        row = self.ancienne_proposition()
        with patch.object(ech, 'INSCRITES', ('a_confirmer', 'confirmee', 'manuel')):      # garde retirée, en mémoire seulement
            self.assertEqual(ech.sync_calendar(self.desk, self.dav, row['id']), 'cree')
        self.assertEqual(self.dav.puts, 1)                   # sans la garde, l'agenda est écrit : la garde est bien celle qui protège
        # même mutation sur la proposition d'agenda en attente (page Autonomie) : sans la garde, elle s'exécuterait
        start = '%sT09:00:00+01:00' % row['due']
        pid, _ = autonomy480.pending_add(self.desk, 'agenda_echeance', MID, 'Ancienne', {'uid': row['id'], 'title': 'É', 'start': start, 'end': start, 'deadline_id': row['id']})
        with patch.object(ech, 'INSCRITES', ('a_confirmer', 'confirmee', 'manuel')):
            self.assertEqual(autonomy480.pending_decide(self.desk, pid, 'execute', self.dav)['status'], 'done')

    def test_D02_echeance_confirmee_reste_inscrite_et_mise_a_jour(self):
        row = self.confirmee()
        self.assertEqual((row['status'], row['calendar']), ('confirmee', 'cree'))
        out = ech.correct(self.desk, self.dav, row['id'], 'Notification reçue un jour plus tard', start_date=(NOTIF + timedelta(days=1)).isoformat())
        self.assertEqual(out['calendar'], 'mis_a_jour')
        self.assertEqual((self.dav.puts, len(self.dav.store)), (2, 1))

    def test_D02_confirmer_une_detection_exige_la_preuve(self):
        sans_preuve = self.ancienne_proposition(SANS_ACTION, '/Dossiers/DEMO/Conclusions appelant.pdf', J - timedelta(days=200))
        with self.assertRaises(Stop) as refus:
            ech.confirm(self.desk, self.dav, sans_preuve['id'])
        self.assertEqual(str(refus.exception), 'preuve_notification_a_verifier')
        incident = self.ancienne_proposition(INCIDENT, CH_INCIDENT, NOTIF - timedelta(days=1))
        with self.assertRaises(Stop) as refus:
            ech.confirm(self.desk, self.dav, incident['id'])
        self.assertEqual(str(refus.exception), 'acte_incident_sans_delai_au_fond')
        self.assertEqual(self.dav.puts, 0)
        prouvee = self.ancienne_proposition(FOND, CH_FOND)
        out = ech.confirm(self.desk, self.dav, prouvee['id'])
        self.assertEqual((out['status'], out['calendar'], self.dav.puts), ('confirmee', 'cree', 1))

    def test_D02_proposition_d_agenda_en_attente_refusee_pour_une_echeance_non_validee(self):
        row = self.ancienne_proposition()
        start = '%sT09:00:00+01:00' % row['due']
        pid, _ = autonomy480.pending_add(self.desk, 'agenda_echeance', MID, 'Ancienne proposition', {'uid': row['id'], 'title': 'Échéance', 'start': start,
                                                                                                     'end': start, 'deadline_id': row['id']})
        with self.assertRaises(Stop) as refus:
            autonomy480.pending_decide(self.desk, pid, 'execute', self.dav)
        self.assertEqual(str(refus.exception), 'echeance_non_validee_ou_modifiee')
        self.assertEqual((self.dav.puts, autonomy480.pending_list(self.desk)), (0, []))
        # une prescription (suivi propre, absente des échéances) reste exécutable : pas de régression
        prescription = 'f' * 64
        pid, _ = autonomy480.pending_add(self.desk, 'agenda_echeance', MID, 'Prescription', {'uid': prescription, 'title': 'Prescription', 'start': start,
                                                                                            'end': start, 'deadline_id': prescription})
        self.assertEqual(autonomy480.pending_decide(self.desk, pid, 'execute', self.dav)['status'], 'done')
        self.assertEqual(self.dav.puts, 1)

    def test_D02_agents_documentaires_ne_creent_plus_d_echeance_persistante(self):
        procedure568.save_case(self.desk, {'matter': MID, 'circuit': 'long', 'side': 'intime', 'distance': 'none', 'ordinary_civil': True,
                                           'no_interruption_or_shortening': True, 'confirmed': True, 'declaration_date': DECL.isoformat()})
        pages = [{'page': 1, 'label': 'p. 1', 'text': FOND, 'extraction': 'native'}]
        out, _ = procedure568.source_events(self.desk, pages, {'id': MID, 'client_name': 'Client DEMO'}, CH_FOND, 'sha-fond', persist=True)
        derived = [x for x in out if x['derived']]
        self.assertTrue(derived)
        self.assertTrue(all(x['safe'] is False and 'deadline_id' not in x for x in derived))
        self.assertEqual(ech.listing(self.desk, include_closed=True), [])
        self.assertEqual([c['evenement'] for c in self.candidats()], ['conclusions_appelant'])

    def test_D02_D07_notification_prouvee_role_intime_909_inscrit_apres_validation_seulement(self):
        self.analyse(FOND)
        cand = self.candidats()[0]
        c = delais.corriger(self.desk, cand['id'], {'cle': self.cle(), 'version': cand['revision'], 'inputs': COMPLET_909}, *AVOCAT)
        attendu = moteur.compute('conclusions_intime', NOTIF, regime_date=DECL.isoformat())['due']
        self.assertEqual((c['statut'], c['date_calculee']), ('propose', attendu))       # calcul du moteur existant (art. 909)
        delais.valider_regle(self.desk, {'rule_id': 'conclusions_intime_909', 'empreinte': delais.empreinte_regle(delais.regle('conclusions_intime_909'))}, *AVOCAT)
        self.assertEqual((self.dav.puts, ech.listing(self.desk)), (0, []))               # rien avant la décision
        fait = delais.decider(self.desk, c['id'], {'action': 'valider', 'version': c['revision'], 'agenda': True}, *AVOCAT, self.dav)
        self.assertEqual((fait['statut'], fait['suivi']['due'], fait['suivi']['calendar']), ('valide', attendu, 'cree'))
        self.assertEqual(self.dav.puts, 1)
        self.assertEqual([r['status'] for r in ech.listing(self.desk)], ['confirmee'])


# ------------------------------------------------------------------------------------------------ D03 écarter avec motif
class D03Ecarter(Lot):
    def test_D03_contentieux_ecarter_une_proposition_motif_obligatoire_une_seule_decision(self):
        self.analyse(FOND)
        cand = self.candidats()[0]
        with self.assertRaises(Stop) as vide:
            delais.decider(self.desk, cand['id'], {'action': 'rejeter', 'version': cand['revision'], 'motif': '  '}, *AVOCAT)
        self.assertEqual(str(vide.exception), 'motif_obligatoire')
        self.assertEqual(self.candidats()[0]['statut'], 'a_verifier')                    # aucun effet
        with self.assertRaises(Stop) as role:
            delais.decider(self.desk, cand['id'], {'action': 'rejeter', 'version': cand['revision'], 'motif': 'Arrêt cité'}, 'camille@example.test', 'assistant')
        self.assertEqual(str(role.exception), 'role_insuffisant')
        fait = delais.decider(self.desk, cand['id'], {'action': 'rejeter', 'version': cand['revision'], 'motif': 'Notification non prouvée'}, *AVOCAT)
        self.assertEqual((fait['statut'], fait['motif']), ('rejete', 'Notification non prouvée'))
        with self.assertRaises(Stop) as second:                                           # second onglet, page périmée
            delais.decider(self.desk, cand['id'], {'action': 'rejeter', 'version': cand['revision'], 'motif': 'Doublon'}, *AVOCAT)
        self.assertEqual(str(second.exception), 'version_en_conflit')
        self.assertTrue(cweb._erreur(second.exception)['status'].startswith('409'))
        rejets = self.desk.db.execute("SELECT COUNT(*) FROM contentieux_journal5689 WHERE action='rejet'").fetchone()[0]
        self.assertEqual((rejets, self.dav.puts, self.dav.deletes), (1, 0, []))

    def test_D03_page_contentieux_propose_d_ecarter_avec_motif_requis(self):
        self.analyse(FOND)
        html = cweb._section_delais(self.desk, '', MID, True)
        formulaire = re.search(r'<summary>Écarter la proposition</summary>(<form.*?</form>)', html, re.S)
        self.assertTrue(formulaire)
        champs = dict(re.findall(r'<input type="hidden" name="(\w+)" value="([^"]*)"', formulaire.group(1)))
        self.assertEqual((champs['action'], champs['id'], champs['version']), ('rejeter', self.candidats()[0]['id'], '1'))
        motif = re.search(r'<input name="motif"[^>]*>', formulaire.group(1)).group(0)
        self.assertIn('required', motif)
        self.assertIn('minlength="3"', motif)
        aide = re.search(r'aria-describedby="([^"]+)"', motif).group(1)
        self.assertIn('id="%s"' % aide, formulaire.group(1))
        self.assertNotIn('Écarter la proposition', cweb._section_delais(self.desk, '', MID, False))     # sans droit de valider : pas d'action

    def test_D03_echeances_motif_vide_aucun_effet(self):
        row = self.ancienne_proposition(ARRET_CITE)
        avant = self.journal(row['id'])
        for outcome, ecart in (('annulee', True), ('annulee', False)):
            with self.assertRaises(Stop) as refus:
                ech.close(self.desk, self.dav, row['id'], outcome, ' ', ecart=ecart)
            self.assertEqual(str(refus.exception), 'motif_obligatoire')
        with self.assertRaises(Stop):
            ech.correct(self.desk, self.dav, row['id'], '', new_due=(J + timedelta(days=30)).isoformat())
        self.assertEqual((ech._row(self.desk, row['id'])['status'], self.journal(row['id'])), ('a_confirmer', avant))
        self.assertEqual(ech.ecarts(self.desk), [])

    def test_D03_deux_soumissions_une_seule_decision(self):
        row = self.ancienne_proposition(ARRET_CITE, start=NOTIF - timedelta(days=4))
        cle = self.cle()
        premier = ech.close(self.desk, self.dav, row['id'], 'annulee', 'Arrêt cité', 'claire@example.test', 1, cle, True)
        second = ech.close(self.desk, self.dav, row['id'], 'annulee', 'Arrêt cité', 'claire@example.test', 1, cle, True)   # double clic
        self.assertEqual((premier['status'], second.get('deja_decide')), ('annulee', True))
        self.assertEqual([j['action'] for j in self.journal(row['id'])].count('ecartee'), 1)
        with self.assertRaises(Stop) as onglet:                                           # second onglet : autre clé, version vue périmée
            ech.correct(self.desk, self.dav, row['id'], 'Date reprise', new_due=(J + timedelta(days=10)).isoformat(), version=1, cle=self.cle())
        self.assertIn(str(onglet.exception), ('version_en_conflit', 'echeance_close'))
        with self.assertRaises(Stop) as perimee:
            ech.close(self.desk, self.dav, self.confirmee()['id'], 'terminee', 'Conclusions notifiées', version=7, cle=self.cle())
        self.assertEqual(str(perimee.exception), 'version_en_conflit')


class D03Page(unittest.TestCase):
    """Page et routes Échéances à travers l'application WSGI réelle : quatre décisions, formulaires voisins, 409, auteur au journal."""
    from test_v440_web import WebWorkshopTests as _W
    PATH = _W.PATH
    call = _W.call
    _base_setup = _W.setUp

    def setUp(self):
        self._base_setup()
        self.cal = Agenda()
        p = patch('agent.office440.dav_client', lambda desk: self.cal)
        p.start()
        self.addCleanup(p.stop)

    def post(self, name, body):
        r = self.call('/api440/' + name, 'POST', body)
        return r, (json.loads(r['body']) if r['body'][:1] == b'{' else {})

    def test_D03_quatre_actions_distinctes_et_formulaire_a_cote_du_bouton(self):
        row = ech.create_deadline(self.desk, MID, 'appel_jugement_contentieux', (J - timedelta(days=5)).isoformat(), source_path=CH_FOND, excerpt=SANS_ACTION)
        page = self.call('/echeances')['body'].decode()
        article = re.search(r'<article class="ex-item[^"]*" id="e-%s".*?</article>' % row['id'], page, re.S).group(0)
        boutons = re.findall(r'<button type="button" class="ax-btn \w+ ex-open" data-kind="(\w+)" aria-expanded="false" aria-controls="([^"]+)">([^<]+)</button>', article)
        self.assertEqual([b[2] for b in boutons], ['Corriger la date', 'Écarter une fausse détection', 'Marquer accomplie', 'Annuler une échéance devenue sans objet'])
        for kind, box, _ in boutons:
            bloc = re.search(r'<div class="ex-box" id="%s"[^>]*hidden>(.*?)</div></div></div>' % re.escape(box), article, re.S)
            self.assertTrue(bloc, kind)
            motif = re.search(r'<input type="text" class="ex-reason"[^>]*>', bloc.group(1)).group(0)
            erreur = re.search(r'aria-describedby="([^"]+)"', motif).group(1)
            self.assertIn('id="%s"' % erreur, bloc.group(1))                                # erreur liée au champ Motif
            self.assertIn('class="ax-btn ghost ex-back">Annuler', bloc.group(1))
            self.assertIn('ex-send">Confirmer', bloc.group(1))
        self.assertIn('data-version="1"', article)
        self.assertEqual(len(re.findall(r'<option value="[^"]+"></option>', re.search(r'<datalist id="ex-motifs">(.*?)</datalist>', page, re.S).group(1))), 4)

    def test_D03_route_motif_vide_puis_ecart_puis_409_et_auteur_au_journal(self):
        row = ech.create_deadline(self.desk, MID, 'appel_jugement_contentieux', (J - timedelta(days=5)).isoformat(), source_path=CH_FOND, excerpt=SANS_ACTION)
        r, d = self.post('deadline/close', {'id': row['id'], 'outcome': 'annulee', 'ecart': True, 'reason': '', 'version': 1, 'cle': 'cle-onglet-a1'})
        self.assertTrue(r['status'].startswith('400'))
        self.assertEqual((d['error'], ech._row(self.desk, row['id'])['status']), ('motif_obligatoire', 'a_confirmer'))
        r, d = self.post('deadline/close', {'id': row['id'], 'outcome': 'annulee', 'ecart': True, 'reason': 'Arrêt cité', 'version': 1, 'cle': 'cle-onglet-a1'})
        self.assertTrue(r['status'].startswith('200'), r['body'])
        r2, d2 = self.post('deadline/close', {'id': row['id'], 'outcome': 'annulee', 'ecart': True, 'reason': 'Arrêt cité', 'version': 1, 'cle': 'cle-onglet-a1'})
        self.assertTrue(r2['status'].startswith('200'))                                   # double clic : même décision, rien de plus
        r3, d3 = self.post('deadline/correct', {'id': row['id'], 'reason': 'Autre onglet', 'new_due': (J + timedelta(days=9)).isoformat(), 'version': 1, 'cle': 'cle-onglet-b1'})
        self.assertTrue(r3['status'].startswith(('409', '400')))
        journal = [dict(x) for x in self.desk.db.execute("SELECT * FROM deadline_journal450 WHERE deadline_id=? AND action='ecartee'", (row['id'],))]
        self.assertEqual(len(journal), 1)
        self.assertTrue(journal[0]['actor'])                                               # auteur de la session, jamais du corps
        self.assertEqual((json.loads(journal[0]['before'])['status'], json.loads(journal[0]['after'])['status'], journal[0]['reason']), ('a_confirmer', 'annulee', 'Arrêt cité'))
        self.assertEqual((self.cal.puts, self.cal.deletes), (0, []))

    def test_D03_version_perimee_renvoie_409(self):
        row = ech.create_deadline(self.desk, MID, 'appel_jugement_contentieux', (J - timedelta(days=5)).isoformat(), status='confirmee', origin='manuel')
        r, d = self.post('deadline/close', {'id': row['id'], 'outcome': 'terminee', 'reason': 'Acte notifié', 'version': 3, 'cle': 'cle-onglet-c1'})
        self.assertTrue(r['status'].startswith('409'), r['status'])
        self.assertEqual((d['error'], ech._row(self.desk, row['id'])['status']), ('version_en_conflit', 'confirmee'))


# ------------------------------------------------------------------------------------------------ D04 décision interne, agenda à part
class D04Agenda(Lot):
    def annulee(self):
        row = self.confirmee()
        self.assertEqual(len(self.dav.store), 1)
        puts = self.dav.puts
        out = ech.close(self.desk, self.dav, row['id'], 'annulee', 'Appel finalement non formé', 'claire@example.test')
        self.assertEqual((out['calendar'], self.dav.puts, self.dav.deletes, self.dav.lectures), ('retrait_propose', puts, [], 0))
        return out

    def test_D04_annuler_ne_touche_pas_l_agenda_et_propose_le_retrait(self):
        out = self.annulee()
        self.assertNotIn('ANNULÉE', list(self.dav.store.values())[0]['summary'])
        self.assertIn('agenda_a_reconcilier', [a['kind'] for a in ech.cross_check(self.desk, None, J)['anomalies']])
        self.assertEqual([r['id'] for r in ech.a_decider_agenda(self.desk)], [out['id']])

    def test_D04_retrait_verifie_uid_calendrier_et_version(self):
        out = self.annulee()
        uid = 'axiorhub-%s@mail-agent.local' % out['id']
        fait = ech.retirer_agenda(self.desk, self.dav, out['id'], 'claire@example.test', out['revision'], self.cle())
        self.assertEqual(fait['calendar'], 'retiree')
        self.assertEqual(self.dav.deletes, [('/calendar/', uid, '"v1-%s"' % uid[9:17])])  # calendrier configuré, UID propre, ETag relu
        self.assertEqual((fait['calendar_uid'], self.dav.store), ('', {}))

    def test_D04_aucun_uid_zero_put_zero_delete(self):
        row = self.ancienne_proposition(ARRET_CITE)
        out = ech.close(self.desk, self.dav, row['id'], 'annulee', 'Arrêt cité', ecart=True)
        self.assertEqual(out['calendar'], 'non_inscrite')
        self.assertEqual(ech.retirer_agenda(self.desk, self.dav, row['id'])['calendar'], 'non_inscrite')
        self.assertEqual((self.dav.puts, self.dav.deletes, self.dav.lectures), (0, [], 0))

    def test_D04_uid_d_une_autre_entree_jamais_efface(self):
        out = self.annulee()
        autre = 'axiorhub-%s@mail-agent.local' % ('a' * 64)
        self.dav.store[autre] = dict(list(self.dav.store.values())[0], uid=autre)
        self.desk.db.execute('UPDATE deadlines450 SET calendar_uid=? WHERE id=?', (autre, out['id']))
        self.desk.db.commit()
        fait = ech.retirer_agenda(self.desk, self.dav, out['id'])
        self.assertEqual((fait['calendar'], self.dav.deletes, len(self.dav.store)), ('a_reconcilier', [], 2))
        # mutation en mémoire : sans la vérification de l'UID, l'événement d'une autre entrée serait effacé
        self.desk.db.execute("UPDATE deadlines450 SET calendar_state='retrait_propose' WHERE id=?", (out['id'],))
        self.desk.db.commit()
        with patch.object(ech, '_uid_propre', lambda row: True):
            ech.retirer_agenda(self.desk, self.dav, out['id'])
        self.assertEqual([d[1] for d in self.dav.deletes], [autre])

    def test_D04_evenement_modifie_depuis_la_lecture_rien_n_est_efface(self):
        out = self.annulee()
        self.dav.etag_change = True
        self.assertEqual(ech.retirer_agenda(self.desk, self.dav, out['id'])['calendar'], 'a_reconcilier')
        self.assertEqual(len(self.dav.store), 1)
        # la personne reprend l'agenda elle-même : décision notée, rien n'est lu ni écrit, la ligne quitte la liste « à décider »
        lectures = self.dav.lectures
        self.assertEqual(ech.retirer_agenda(self.desk, None, out['id'], 'claire@example.test', verifie_main=True)['calendar'], 'verifie_a_la_main')
        self.assertEqual((self.dav.lectures, len(self.dav.store), ech.a_decider_agenda(self.desk)), (lectures, 1, []))

    def test_D04_coupure_caldav_etat_local_juste_et_relance_bornee(self):
        out = self.annulee()
        self.dav.panne = True
        fait = ech.retirer_agenda(self.desk, self.dav, out['id'])
        self.assertEqual((fait['status'], fait['calendar'], fait['calendar_tries']), ('annulee', 'synchronisation_a_reprendre', 1))
        for _ in range(5):
            ech.reprendre_retraits(self.desk, self.dav)
        self.assertEqual(ech._row(self.desk, out['id'])['calendar_tries'], ech.MAX_ESSAIS_AGENDA)
        lectures = self.dav.lectures
        self.assertEqual(ech.reprendre_retraits(self.desk, self.dav), 0)                    # au-delà de trois essais : la main reprend
        self.assertEqual(self.dav.lectures, lectures)
        self.dav.panne = False
        self.assertEqual(ech.retirer_agenda(self.desk, self.dav, out['id'])['calendar'], 'retiree')   # « Réessayer » à la main

    def test_D04_ecarter_une_erreur_ne_cree_jamais_un_evenement(self):
        for texte in (ARRET_CITE, SANS_ACTION):
            row = self.ancienne_proposition(texte, start=J - timedelta(days=len(texte)))
            ech.close(self.desk, self.dav, row['id'], 'annulee', 'Fausse détection', ecart=True)
        ech.daily_check(self.desk, self.dav, J)
        self.assertEqual((self.dav.puts, self.dav.store, self.dav.deletes), (0, {}, []))


# ------------------------------------------------------------------------------------------------ D05 traçabilité et empreinte
class D05Trace(Lot):
    def test_D05_journal_complet_et_rappels_lies_fermes(self):
        autonomy480.set_level(self.desk, 'agenda_echeance', 'propose')
        row = self.confirmee()
        self.assertEqual(row['calendar'], 'a_valider')
        self.assertEqual(len(autonomy480.pending_list(self.desk)), 1)
        ech.close(self.desk, self.dav, row['id'], 'annulee', 'Désistement de l’appelant', 'claire@example.test', row['revision'], self.cle())
        self.assertEqual(autonomy480.pending_list(self.desk), [])                           # proposition d'agenda liée close
        j = [x for x in self.journal(row['id']) if x['action'] == 'annulee'][0]
        self.assertEqual((j['reason'], j['actor'], j['calendar']), ('Désistement de l’appelant', 'claire@example.test', 'non_inscrite'))
        self.assertEqual((json.loads(j['before'])['status'], json.loads(j['after'])['status']), ('confirmee', 'annulee'))
        self.assertTrue(j['at'] and j['cle'])
        self.assertEqual(ech.run_reminders(self.desk, J), 0)
        self.assertEqual(self.dav.puts, 0)

    def test_D05_candidat_ecarte_jamais_repropose_preuve_nouvelle_presentee(self):
        self.analyse(FOND)
        cand = self.candidats()[0]
        delais.decider(self.desk, cand['id'], {'action': 'rejeter', 'version': cand['revision'], 'motif': 'Doublon'}, *AVOCAT)
        self.analyse(FOND, etag='"e2"')                                                     # nouvelle version, même passage
        self.analyse(FOND, '/Dossiers/DEMO/Copie notification.pdf', etag='"e3"')           # autre source : preuve nouvelle
        autre_date = FOND.replace(fr(NOTIF), fr(NOTIF + timedelta(days=2)))
        self.analyse(autre_date, etag='"e4"')                                               # autre date : preuve nouvelle
        ouverts = sorted((c['source_path'], c['date_evenement']) for c in self.candidats())
        self.assertEqual(ouverts, sorted([('/Dossiers/DEMO/Copie notification.pdf', NOTIF.isoformat()), (CH_FOND, (NOTIF + timedelta(days=2)).isoformat())]))
        self.assertEqual([(e['motif'], e['origine']) for e in ech.ecarts(self.desk, MID)], [('Doublon', 'candidat')])

    def test_D05_echeance_ecartee_memorisee_et_visible_apres_relecture(self):
        row = self.ancienne_proposition(FOND, CH_FOND)
        ech.close(self.desk, self.dav, row['id'], 'annulee', 'Notification non prouvée', 'claire@example.test', ecart=True)
        self.assertEqual(self.analyse(FOND, etag='"e9"')['propositions'], 0)               # même faux positif : pas reproposé
        self.assertEqual(self.candidats(True), [])
        relu = ech._row(self.desk, row['id'])
        self.assertEqual((relu['status'], relu['note']), ('annulee', 'Écartée : Notification non prouvée'))
        self.assertEqual([j['action'] for j in ech.journal(self.desk, row['id'])][0], 'ecartee')


# ------------------------------------------------------------------------------------------------ D06 contrôle en lecture seule
class D06Audit(Lot):
    def test_D06_script_lit_sans_ecrire_et_liste_les_constats(self):
        procedure568.save_case(self.desk, {'matter': MID, 'declaration_date': DECL.isoformat()})
        inscrite = self.ancienne_proposition(FOND, CH_FOND)
        self.desk.db.execute("UPDATE deadlines450 SET calendar_uid=?,calendar_state='cree' WHERE id=?", ('axiorhub-%s@mail-agent.local' % inscrite['id'], inscrite['id']))
        incident = ech.create_deadline(self.desk, MID, 'conclusions_intime', (NOTIF - timedelta(days=1)).isoformat(), regime_date=DECL.isoformat(),
                                       source_path=CH_INCIDENT, excerpt=INCIDENT[-300:], status='confirmee')
        avant = self.ancienne_proposition(FOND, '/Dossiers/DEMO/Ancienne notification.pdf', DECL - timedelta(days=40))
        sans_preuve = self.ancienne_proposition(SANS_ACTION, '/Dossiers/DEMO/Conclusions appelant.pdf', NOTIF - timedelta(days=3))
        self.desk.db.commit()
        self.desk.db.execute('PRAGMA wal_checkpoint(TRUNCATE)')
        base = Path(self.desk.c['state_dir']) / 'desk.sqlite3'
        self.assertTrue(base.exists())
        empreinte, mtime = hashlib.sha256(base.read_bytes()).hexdigest(), base.stat().st_mtime_ns
        script = Path(__file__).resolve().parents[1] / 'scripts' / 'audit-echeances-5696.py'
        env = dict(os.environ, PYTHONIOENCODING='utf-8', PYTHONUTF8='1')
        sortie = subprocess.run([sys.executable, str(script), str(base), '--json'], capture_output=True, text=True, encoding='utf-8', env=env, timeout=120)
        self.assertEqual(sortie.returncode, 0, sortie.stderr)
        rapport = json.loads(sortie.stdout)
        constats = {x['id']: set(x['constats']) for x in rapport['constats']}
        self.assertIn('proposition_inscrite_agenda', constats[inscrite['id']])
        self.assertIn('fond_depuis_incident', constats[incident['id']])
        self.assertIn('depart_avant_declaration', constats[avant['id']])
        self.assertIn('notification_non_prouvee', constats[sans_preuve['id']])
        self.assertEqual((rapport['ecritures'], hashlib.sha256(base.read_bytes()).hexdigest(), base.stat().st_mtime_ns), (0, empreinte, mtime))
        self.assertTrue(rapport['limites'])
        lisible = subprocess.run([sys.executable, str(script), str(base)], capture_output=True, text=True, encoding='utf-8', env=env, timeout=120)
        self.assertIn('ne prouve pas', lisible.stdout)
        self.assertEqual(hashlib.sha256(base.read_bytes()).hexdigest(), empreinte)
        with sqlite3.connect(base) as db:                                                   # aucune décision prise à la place de l'avocat
            self.assertEqual(db.execute("SELECT COUNT(*) FROM deadlines450 WHERE status='annulee'").fetchone()[0], 0)


if __name__ == '__main__':
    unittest.main()
