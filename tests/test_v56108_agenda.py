"""5.6.108, lot G : agenda local (création depuis un créneau, éditeur lisible, dates à vérifier), Produire (livrables prêts d'abord, lignes
denses) et Dossiers (derniers résultats réels de l'IA, fiche).

Données fictives (DURAND, MARTIN, ZORVAL, KERVILAC, DOS-001, RG 24/01234, example.test), faux services, aucun réseau, aucun modèle. Les jours
sont ceux du cabinet (Europe/Paris), jamais ``date.today()``. Le vrai navigateur (barre collée pendant le défilement, préremplissage par le
script, densité mesurée) est vérifié par ``scripts/parcours_p108g.py`` ; ces tests vérifient le serveur.
"""
from datetime import datetime, timedelta
import json
from pathlib import Path
import re
import unittest
from unittest.mock import patch
from zoneinfo import ZoneInfo

from agent import agenda56108, agenda5697, agenda_web5697, grille5697, interface56100, membres5650, rendezvous5697 as rdv
from agent import ecrans5632 as E
from agent.common import Stop
import test_v5697_agenda as A
import test_v56100_dossiers as D100
import test_v56100_produire as P100
import test_v56107_langues as L

ROOT = Path(__file__).resolve().parents[1]
STATIC = ROOT / 'agent' / 'static'
PARIS = ZoneInfo('Europe/Paris')
DML = re.compile(r'^\s*(INSERT|UPDATE|DELETE|REPLACE|CREATE|ALTER|DROP)\b', re.I)


def setUpModule():
    D100.setUpModule()


def jour_cabinet():
    return datetime.now(PARIS).date()


class Ecritures:
    """Relève toute écriture (DML et schéma) d'une connexion SQLite pendant un affichage (R05)."""

    def __init__(self, db):
        self.db, self.vues = db, []

    def __enter__(self):
        self.db.set_trace_callback(lambda sql: self.vues.append(sql[:120]) if DML.match(sql) else None)
        return self

    def __exit__(self, *exc):
        self.db.set_trace_callback(None)


# ================================================================================================ agenda local
class Agenda(A.Base):
    def setUp(self):
        super().setUp()
        p = patch('agent.membres5650.directory', side_effect=lambda desk, *a, **k: dict(A.MEMBRES))
        p.start()
        self.addCleanup(p.stop)

    def page(self, compte='durand@example.test', role='avocat', **args):
        membres5650.set_author(compte)
        membres5650.set_viewer_role(role)
        return grille5697.page(self.desk, {'view': 'week', **args}, lambda p, **kw: '/agent-courriel' + p + ('?' + '&'.join('%s=%s' % x for x in kw.items()) if kw else ''),
                               '/agent-courriel', 'personal')

    def editeur(self, html):
        return html[html.index('<dialog class="ag97-editeur"'):html.index('</dialog>')]

    def echeance(self, ident, matter, due, status):
        from agent import echeances450
        echeances450.ensure_schema(self.desk)
        self.desk.db.execute('INSERT INTO deadlines450(id,matter,rule_id,start_event,start_date,due,status,origin,created,updated) VALUES(?,?,?,?,?,?,?,?,?,?)',
                             (ident, matter, 'appel_908', 'notification', jour_cabinet().isoformat(), due, status, 'auto', '2026-11-18T08:00:00+00:00',
                              '2026-11-18T08:00:00+00:00'))
        self.desk.db.commit()

    # ------------------------------------------------------------------ 1. fenêtre préremplie et lisible
    def test_G1_editeur_duree_rappel_fuseau_responsable_recurrence_lisibles(self):
        html = self.editeur(self.page(date=A.mardi_prochain().isoformat()))
        for libelle in ('Durée', 'Rappel', 'Fuseau horaire', 'Responsable', 'Récurrence', 'Dossier (facultatif)'):
            self.assertIn(libelle, html)
        self.assertIn('<dd data-ag108-zone>Europe/Paris</dd>', html)                  # fuseau de saisie écrit en clair
        self.assertIn('Me DURAND', html)                                              # responsable : le compte qui inscrit l'événement
        self.assertIn('Aucune (événement unique)', html)
        self.assertRegex(html, r'<option value="1440,60" selected>La veille et 1 h avant</option>')   # défaut explicite (celui de l'Agenda personnel)
        self.assertIn('<option value="aucun">Aucun rappel</option>', html)
        self.assertIn('<option value="90">1 h 30</option>', html)
        self.assertIn('/static/v56108-agenda.js', self.page(date=A.mardi_prochain().isoformat()))

    def test_G1_creation_par_la_route_existante_avec_rappel_choisi(self):
        j = A.mardi_prochain()
        r = self.creer(debut='%sT15:00' % j, fin='%sT16:30' % j, rappels=[30], dossier='DOS-001')
        row = self.desk.db.execute('SELECT reminders, matter FROM agenda5634 WHERE id=?', (r['evenement_id'],)).fetchone()
        self.assertEqual((json.loads(row['reminders']), row['matter']), ([30], 'DOS-001'))
        aucun = self.creer(debut='%sT17:00' % j, fin='%sT17:30' % j, rappels=[])
        self.assertEqual(json.loads(self.desk.db.execute('SELECT reminders FROM agenda5634 WHERE id=?', (aucun['evenement_id'],)).fetchone()[0]), [])
        defaut = self.creer(debut='%sT18:00' % j, fin='%sT18:30' % j)                  # sans choix (ancienne présentation) : défaut inchangé
        self.assertEqual(json.loads(self.desk.db.execute('SELECT reminders FROM agenda5634 WHERE id=?', (defaut['evenement_id'],)).fetchone()[0]), [60, 1440])

    def test_G1_rappel_hors_liste_refuse_avant_toute_ecriture(self):
        avant = self.compte('agenda5634')
        for mauvais in ([7], [60, 'x'], [10, 30, 60, 120, 1440]):
            with self.assertRaises(Stop) as ctx:
                self.creer(rappels=mauvais)
            self.assertEqual(str(ctx.exception), 'rappel_invalide')
        self.assertEqual(self.compte('agenda5634'), avant)
        self.assertEqual(self.compte('agenda_ops5697'), 0)                            # la clé n'est pas consommée par un refus
        self.assertIn('rappel_invalide', agenda_web5697.MESSAGES)

    def test_G1_rappel_modifiable_avec_version_et_idempotence(self):
        r = self.creer()
        ev = agenda5697.evenement(self.desk, A.MOI, r['evenement_id'])
        self.assertEqual(ev['reminders'], [60, 1440])
        self.assertEqual(ev['responsable_nom'], 'Me DURAND')
        m = agenda5697.modifier(self.desk, A.MOI, {'cle': 'cle-modif-essai-0001', 'id': r['evenement_id'], 'version': ev['version'], 'rappels': [10]})
        self.assertEqual(agenda5697.evenement(self.desk, A.MOI, r['evenement_id'])['reminders'], [10])
        rejeu = agenda5697.modifier(self.desk, A.MOI, {'cle': 'cle-modif-essai-0001', 'id': r['evenement_id'], 'version': ev['version'], 'rappels': [10]})
        self.assertEqual(rejeu['version'], m['version'])                               # même clé, même contenu : le résultat initial
        with self.assertRaises(Stop) as ctx:                                          # ancienne version : refus 409, jamais une écriture aveugle
            agenda5697.modifier(self.desk, A.MOI, {'cle': 'cle-modif-essai-0002', 'id': r['evenement_id'], 'version': ev['version'], 'rappels': [30]})
        self.assertIn(str(ctx.exception), ('evenement_modifie_entre_temps', 'version_perimee'))
        self.assertEqual(agenda_web5697.STATUTS[str(ctx.exception)], '409 Conflict')

    def test_G1_creation_par_l_api_csrf_et_cle(self):
        """La fenêtre enregistre par la route existante : sans jeton CSRF, rien n'est créé ; la même clé rejouée ne crée qu'un événement."""
        from agent import web440
        j = A.mardi_prochain()
        corps = {'cle': 'cle-api-lot-g-0001', 'objet': 'evenement', 'titre': 'Rendez-vous ZORVAL', 'debut': '%sT11:00' % j, 'fin': '%sT11:45' % j,
                 'rappels': [1440], 'dossier': 'DOS-001', 'calendrier': 'local', 'zone': 'Europe/Paris'}
        env = {'REQUEST_METHOD': 'POST', 'HTTP_X_AXIORHUB_USER': 'durand@example.test', 'HTTP_X_AXIORHUB_ROLE': 'avocat'}
        auth = {'origin': 'https://pilote.example.test', 'csrf': 'jeton-essai-g1'}
        refus = agenda_web5697.api(dict(env, HTTP_ORIGIN='https://pilote.example.test'), self.desk, auth, '/p', 'm5689/agenda/evenements', {}, 'POST')
        self.assertEqual(refus['status'], '403 Forbidden')                            # sans jeton CSRF : refus, rien n'est lu ni écrit
        self.assertEqual(self.compte('agenda5634'), 0)
        statuts = []
        with patch.object(web440, 'check_post', return_value=dict(corps)):
            for _ in range(2):
                statuts.append(agenda_web5697.api(env, self.desk, auth, '/p', 'm5689/agenda/evenements', {}, 'POST')['status'])
        self.assertEqual(statuts, ['201 Created', '200 OK'])
        self.assertEqual(self.compte('agenda5634'), 1)
        self.assertEqual(json.loads(self.desk.db.execute('SELECT reminders FROM agenda5634').fetchone()[0]), [1440])

    # ------------------------------------------------------------------ 2. visio et Speakr seulement s'ils sont configurés
    def test_G2_talk_non_configure_non_propose_et_rendez_vous_possible(self):
        html = self.editeur(self.page(date=A.mardi_prochain().isoformat()))
        self.assertFalse(rdv.talk_disponible(self.desk)[0])
        self.assertNotIn('value="talk"', html)
        self.assertNotIn('Talk indisponible', html)
        self.assertIn('value="sans" checked', html)
        self.assertIn('Coller un lien HTTPS existant', html)
        self.assertNotIn('name="speakr"', html)                                        # Speakr non configuré : rien
        r = self.creer(visio='sans', nature='rendez_vous')
        self.assertEqual(r['http'], 201)

    def test_G2_talk_et_speakr_configures_proposes(self):
        rdv.speakr_regler(self.desk, A.ADMIN, 'cabinet', 'https://speakr.example.test')
        with patch.object(rdv, 'talk_disponible', return_value=(True, '')):
            html = self.editeur(self.page(date=A.mardi_prochain().isoformat()))
        self.assertIn('value="talk">', html)
        self.assertIn('name="speakr"', html)

    def test_G2_ancienne_presentation_inchangee(self):
        interface56100.regler_compte(self.desk, 'durand@example.test', False)
        page = self.page(date=A.mardi_prochain().isoformat())
        html = self.editeur(page)
        self.assertIn('Talk indisponible', html)                                       # présentation précédente : grisé avec son motif
        self.assertNotIn('data-ag108', html)
        self.assertNotIn('v56108-agenda.js', page)
        self.assertNotIn(' ag108"', page)

    # ------------------------------------------------------------------ 2. date extraite ambiguë : à vérifier, hors de l'agenda confirmé
    def test_G2_dates_a_verifier_a_part_de_la_grille(self):
        j = A.mardi_prochain()
        self.echeance('ech-verif-1', 'DOS-001', j.isoformat(), 'a_confirmer')
        self.echeance('ech-verif-2', 'DOS-001', '', 'a_completer')
        self.echeance('ech-conf', 'DOS-001', j.isoformat(), 'confirmee')
        self.echeance('ech-reserve', 'DOS-002', j.isoformat(), 'a_confirmer')
        self.reserver()                                                                # DOS-002 réservé à MARTIN
        page = self.page(date=j.isoformat())
        bandeau = re.search(r'<p class="ag108-verifier"[^>]*data-ag108-verifier="(\d+)"', page)
        self.assertEqual(bandeau.group(1), '2')                                        # droits appliqués avant le total
        self.assertIn('Dates extraites à vérifier (2)', page)
        self.assertNotIn('ech-reserve', page)
        grille = page[page.index('data-ag97-grille'):page.index('id="ag97-aide"')]
        self.assertNotIn('À vérifier', grille)                                         # jamais dans la grille confirmée
        self.assertLess(page.index('ag108-verifier'), page.index('data-ag97-grille'))
        martin = self.page(compte='martin@example.test', date=j.isoformat())
        self.assertIn('data-ag108-verifier="3"', martin)

    def test_G2_mutation_droits_retires_detectee(self):
        """Sans le filtre des dossiers réservés, l'échéance de DOS-002 serait comptée chez DURAND : le test le détecte."""
        self.echeance('ech-reserve', 'DOS-002', A.mardi_prochain().isoformat(), 'a_confirmer')
        self.reserver()
        acteur = ('durand@example.test', 'avocat')
        self.assertEqual(agenda56108.dates_a_verifier(self.desk, acteur, jour_cabinet())['total'], 0)
        with patch('agent.acces5697.masques', lambda desk, a: frozenset()):
            self.assertEqual(agenda56108.dates_a_verifier(self.desk, acteur, jour_cabinet())['total'], 1)

    def test_G2_registre_illisible_jamais_un_faux_zero(self):
        with patch('agent.acces5697.masques', side_effect=Stop('registre_illisible')):
            out = agenda56108.dates_a_verifier(self.desk, ('durand@example.test', 'avocat'), jour_cabinet())
        self.assertEqual(out['etat'], 'indisponible')
        with patch.object(agenda56108, 'dates_a_verifier', return_value={'etat': 'indisponible', 'total': 0, 'items': []}):
            bandeau, volet = agenda56108.a_verifier_html(self.desk, ('durand@example.test', 'avocat'), '/p', jour_cabinet())
        self.assertIn('indisponible pour l’instant', bandeau)
        self.assertNotIn('>0<', bandeau)

    def test_G2_affichage_sans_ecriture(self):
        self.echeance('ech-verif-1', 'DOS-001', A.mardi_prochain().isoformat(), 'a_confirmer')
        self.page(date=A.mardi_prochain().isoformat())                                 # premier affichage (tables du lot déjà prêtes)
        with Ecritures(self.desk.db) as w:
            self.page(date=A.mardi_prochain().isoformat())
        self.assertEqual([x for x in w.vues if 'agenda56108' in x or 'deadlines450' in x], [])

    # ------------------------------------------------------------------ script : aucun style en ligne, aucun HTML inséré, raccordé
    def test_G1_script_et_feuille_declares_sans_html_injecte(self):
        from agent import shell501, web440
        js = (STATIC / 'v56108-agenda.js').read_text(encoding='utf-8')
        self.assertNotIn('innerHTML', js)
        self.assertNotIn('insertAdjacentHTML', js)
        self.assertIn('/static/v56108-agenda.js', web440.STATIC)
        self.assertIn('/static/v56108-agenda.css', web440.STATIC)
        self.assertIn('v56108-agenda.css', shell501.BUNDLE)
        self.assertIn('v56108-agenda.css', (STATIC / 'app520.css').read_text(encoding='utf-8'))
        grille = (STATIC / 'v5697-agenda.js').read_text(encoding='utf-8')
        self.assertIn('window.AX108agenda.ouvert(form, detailOuvert)', grille)
        self.assertIn('rappels: c.rappels', grille)                                     # la modification transmet le rappel choisi


# ================================================================================================ Produire
class Produire(P100.Base):
    def test_G3_livrables_prets_d_abord_dossier_type_version_etat_date(self):
        for i in range(3):
            self.document('g%02d' % i, 'Conclusions fictives ZORVAL n° %d' % i, review='reviewed')
        self.document('p01', 'Note en préparation', kind='note', state='preparing')
        html = self.section()
        carte = self.carte(html, 'doc-g00')
        for attendu in ('class="p100-type"', 'class="p100-dossier"', 'Version 1', 'data-etat="pret"', '>Aperçu<'):
            self.assertIn(attendu, carte)
        self.assertRegex(carte, r'<span>\d{2}/\d{2}/\d{4}</span>')
        self.assertNotIn('Note en préparation', html)                                  # une opération en cours n'occupe pas la place d'un prêt

    def test_G3_limite_commune_dite_une_fois(self):
        for i in range(3):
            self.document('h%02d' % i, 'Conclusions fictives n° %d' % i)
        commune = 'Éditeur EuroOffice non relié : téléchargez le fichier pour le modifier.'
        vue = {'actions': {'editor': False, 'ai_edit': False, 'download': False, 'export': False}, 'allowed_formats': [], 'destinations': {},
               'unavailable_reasons': [commune]}
        with patch('agent.livrables_apercu5689.vue', return_value=vue):
            html = self.section()
        self.assertEqual(html.count(commune), 1)
        self.assertIn('<p class="p108-limites">', html)
        self.assertNotIn('class="p100-limites"', html)
        with patch('agent.livrables_apercu5689.vue', side_effect=[dict(vue), dict(vue, unavailable_reasons=[]), dict(vue)]):
            html = self.section()
        self.assertEqual(html.count(commune), 2)                                       # pas commune à toutes : dite sur chaque ligne concernée
        self.assertNotIn('class="p108-limites"', html)

    def test_G3_pagination_conservee(self):
        for i in range(12):
            self.document('k%02d' % i, 'Conclusions fictives n° %d' % i)
        html = self.section()
        self.assertEqual(len(re.findall(r'<li class="p100-carte"', html)), 10)
        self.assertIn('Documents suivants', html)


# ================================================================================================ Dossiers
class Dossiers(D100.Jeu):
    def test_G4_trois_derniers_dossiers_resultats_reels_echec_exclu(self):
        from agent import resultats56100
        resultats56100.rattraper(self.desk)
        cartes = resultats56100.recents(self.desk, D100.AVOCATE, 3)
        self.assertEqual(len(cartes), 3)
        self.assertEqual(len({c['dossier_canonique'] for c in cartes}), 3)
        self.assertNotIn('docC2', ' '.join(c['lien'] for c in cartes))               # livrable en échec plus récent : ne compte pas
        page = self.ecran(D100.AVOCATE)
        self.assertEqual(page.count('Voir le résultat'), 3)

    def test_G4_tache_ia_en_erreur_ne_compte_pas(self):
        """Une tentative en erreur (travail « error » de la file, livrable « failed ») n'est jamais un résultat : aucune carte."""
        from agent import resultats56100
        self.desk.db.execute("INSERT INTO jobs(kind,args,status,created,finished,result) VALUES('ask_matter',?, 'error', ?, ?, '{}')",
                             (json.dumps({'matter': self.b}), '2026-11-12T15:00:00+00:00', '2026-11-12T15:00:00+00:00'))
        self.desk.db.execute("DELETE FROM fiche5627")
        self.desk.db.commit()
        resultats56100.rattraper(self.desk)
        cartes = resultats56100.recents(self.desk, D100.AVOCATE, 3)
        self.assertNotIn(self.b, [c['dossier_canonique'] for c in cartes])

    def test_G4_fiche_resultats_enregistres_avec_lien(self):
        from agent import dossier5679, resultats56100
        resultats56100.rattraper(self.desk)
        membres5650.set_author(D100.CLAIRE)
        membres5650.set_viewer_role('avocat')
        self.addCleanup(membres5650.clear)
        html = dossier5679.ai_work_html(self.desk, '/p', {'id': self.a})
        self.assertIn('Résultats enregistrés', html)
        self.assertIn('href="/p/livrables/docA2?version=ver-docA2"', html)
        self.assertLess(html.index('docA2'), html.index('docA1'))                      # du plus récent au plus ancien
        self.assertNotIn('pas encore travaillé', html)
        c = dossier5679.ai_work_html(self.desk, '/p', {'id': self.c})
        self.assertNotIn('docC2', c)                                                   # l'échec n'est pas un résultat
        nouveau = self.creer(titre='MARTIN — dossier sans travail', client='MARTIN', nature='conseil')['id']
        vide = dossier5679.ai_work_html(self.desk, '/p', {'id': nouveau})
        self.assertIn('Aucun résultat enregistré par l’IA sur ce dossier.', vide)

    def test_G4_fiche_dossier_reserve_aucun_resultat(self):
        from agent import resultats56100
        resultats56100.rattraper(self.desk)
        self.assertEqual(resultats56100.du_dossier(self.desk, D100.AVOCATE, self.r), [])
        self.assertTrue(resultats56100.du_dossier(self.desk, (D100.CAMILLE, 'avocat'), self.r))
        with patch('agent.acces5697.masques', lambda desk, a: frozenset()):        # mutation : sans le contrôle, le réservé fuirait
            self.assertTrue(resultats56100.du_dossier(self.desk, D100.AVOCATE, self.r))

    def test_G4_fiche_ancienne_presentation_inchangee(self):
        from agent import dossier5679, resultats56100
        resultats56100.rattraper(self.desk)
        membres5650.set_author(D100.CLAIRE)
        membres5650.set_viewer_role('avocat')
        self.addCleanup(membres5650.clear)
        interface56100.regler_compte(self.desk, D100.CLAIRE, False)
        html = dossier5679.ai_work_html(self.desk, '/p', {'id': self.a})
        self.assertNotIn('Résultats enregistrés', html)

    def test_G4_fiche_commence_par_les_quatre_champs(self):
        html = D100.dossiers56100.reprise_html(self.desk, D100.AVOCATE, '/p', self.a)
        self.assertEqual(re.findall(r'<dt>([^<]+)</dt>', html),
                         ['Responsable', 'Prochaine action', 'Prochaine échéance confirmée', 'Dernier changement'])
        self.assertIn('d100-reprise', html)

    def test_G4_lecture_sans_ecriture(self):
        from agent import dossier5679, resultats56100
        resultats56100.rattraper(self.desk)
        membres5650.set_author(D100.CLAIRE)
        membres5650.set_viewer_role('avocat')
        self.addCleanup(membres5650.clear)
        dossier5679.ai_work_html(self.desk, '/p', {'id': self.a})
        with Ecritures(self.desk.db) as w:
            dossier5679.ai_work_html(self.desk, '/p', {'id': self.a})
        self.assertEqual(w.vues, [])
        with Ecritures(self.desk.db) as w:
            self.ecran(D100.AVOCATE)
        self.assertEqual([x for x in w.vues if not x.lstrip().upper().startswith('CREATE TABLE IF NOT EXISTS')], [])   # aucune écriture métier


# ================================================================================================ densité et langues
class Densite(unittest.TestCase):
    def test_G5_regles_denses_seulement_en_densite_compacte(self):
        css = (STATIC / 'v56108-agenda.css').read_text(encoding='utf-8')
        for regle in re.findall(r'^[^/@\n{}][^{}]*\{', css, re.M):
            if '.p100-' in regle or '.d97-table' in regle or '.d100-carte' in regle:
                self.assertIn('body[data-densite=compacte]', regle, regle)            # présentation confortable (ancienne) inchangée
        tailles = [float(x) for x in re.findall(r'font-size:\s*([\d.]+)rem', css)]
        self.assertGreaterEqual(min(tailles), .75)                                     # 12 px au plus petit (badges), texte à 13 px
        self.assertNotIn('!important', css)


class Langues(L.Base):
    def test_G6_textes_du_lot_traduits_en_de_es(self):
        tables = {lang: E.dictionary(lang) for lang in L.LANGUES}
        phrases = [lib for _v, lib in agenda56108.RAPPELS] + ['Autre durée', 'Aucune (événement unique)', 'Récurrence', 'Fuseau horaire', 'Durée', 'Rappel',
                                                               'Dates extraites à vérifier', 'Vérifier les dates', 'Autre activité', 'Résultats enregistrés',
                                                               'Aucun résultat enregistré par l’IA sur ce dossier.', 'Rappel non reconnu : choisissez un rappel proposé.']
        js = (STATIC / 'v56108-agenda.js').read_text(encoding='utf-8')
        phrases += re.findall(r"var [A-Z_]+ = '([^']+)';", js)
        for lang, table in tables.items():
            for p in phrases:
                cle = re.sub(r'\d+', '#', p)
                self.assertTrue(table.get(p) or table.get(cle), '%s : %s' % (lang, p))

    def test_G6_editeur_et_bandeau_sans_francais_restant(self):
        from agent.acces5697 import Acteur
        html = agenda56108.editeur_complements_html(self.desk, Acteur('cabinet', 'administrateur'), 'Europe/Paris')
        html += ('<p class="ag108-verifier"><span class="ag108-marque">À vérifier</span> <strong>2</strong> <span>date(s) extraite(s), hors de l’agenda confirmé</span> '
                 '<a href="/p/echeances">Vérifier les dates</a></p>')
        for lang in L.LANGUES:
            self.assertEqual(L.reste_francais(L.visibles(E.translate_html(html, lang))), [], lang)



if __name__ == '__main__':
    unittest.main()
