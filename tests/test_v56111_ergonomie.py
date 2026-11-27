"""5.6.111 (lot U, U01 et U02) : ergonomie — écarts mesurés corrigés, sans refonte ni fonction retirée.

- Dossiers : la fiche expose, à côté du responsable et de la prochaine action, les derniers résultats de l'IA (projection existante
  ``resultats56100``, droits du dossier et de l'objet) et les pièces sources (pièces relevées ``modele5651``) ; aucun « 0 » inventé quand
  rien n'est encore relevé ; registre illisible dit tel quel ; dossier réservé : rien ; la liste porte le dernier résultat de l'IA dans
  « Dernière activité » ; aucune écriture pendant l'affichage.
- À relire : « Envoyer… » raccordé à l'envoi existant (routes ``send/prepare`` puis ``send/confirm``, second clic), chargé sur la page et
  dans la fenêtre d'Aujourd'hui, distinct de Retenir et de Déposer dans Brouillons.
- Feuille ``v56111-ergonomie.css`` : déclarée dans le paquet, présente dans ``app520.css`` ; grille de la bibliothèque bornée à la largeur
  disponible (375 px) ; contrastes et cibles corrigés d'après les mesures (``MESURES-56111.md``).
- Textes nouveaux traduits en anglais, allemand et espagnol (« # » pour les nombres).
Données fictives (DURAND, MARTIN, DOS-001), aucun réseau, aucun modèle.
"""
import json
from pathlib import Path
import re
import unittest
from unittest.mock import patch

from agent import dossiers56100, dossiers_ui5697, resultats56100
import test_v56100_dossiers as D100

ROOT = Path(__file__).resolve().parents[1]
STATIC = ROOT / 'agent' / 'static'
LANGUES = ('en', 'de', 'es')


def setUpModule():
    D100.setUpModule()


def tearDownModule():
    D100.T.tearDownModule()


class Fiche(D100.Base):
    def setUp(self):
        super().setUp()
        self.a = self.creer(titre='DURAND c/ MARTIN', client='DURAND', nature='contentieux', rg='24/01234')['id']
        self.r = self.creer(titre='KERVILAC — réservé', client='KERVILAC', confidentialite='reserve', membres=[D100.CAMILLE])['id']
        self.livrable(self.a, 'docA1', '10:00')
        self.livrable(self.a, 'docA2', '11:00')
        self.livrable(self.r, 'docR1', '13:00')

    def test_U01_fiche_resultats_ia_et_pieces_sources(self):
        resultats56100.rattraper(self.desk)
        with D100.Compteur(self.desk.db) as c:
            html = dossiers_ui5697.entete_fiche(self.desk, D100.AVOCATE, '/p', self.a)
        self.assertEqual(c.dml, [])                                                   # aucune écriture métier à l'affichage
        for libelle in ('Responsable', 'Prochaine action', 'Prochaine échéance confirmée', 'Dernier changement', 'Derniers résultats de l’IA', 'Pièces sources'):
            self.assertIn('<dt>%s</dt>' % libelle, html)
        self.assertEqual(re.findall(r'<dt>([^<]+)</dt>', html)[:6], ['Responsable', 'Prochaine action', 'Prochaine échéance confirmée', 'Dernier changement',
                                                                      'Derniers résultats de l’IA', 'Pièces sources'])   # les quatre champs historiques d'abord
        self.assertEqual(len(re.findall(r'<dt>', dossiers56100.reprise_html(self.desk, D100.AVOCATE, '/p', self.a))), 4)   # bloc historique inchangé
        bloc = html[html.index('<dt>Derniers résultats de l’IA</dt>'):html.index('<dt>Pièces sources</dt>')]
        self.assertIn('/livrables/docA2?version=ver-docA2', bloc)                      # le résultat exact, le plus récent d'abord
        self.assertLess(bloc.index('docA2'), bloc.index('docA1'))
        pieces = html[html.index('<dt>Pièces sources</dt>'):]
        self.assertIn('Aucune pièce relevée pour l’instant', pieces)                 # jamais un faux « 0 pièce »
        self.assertNotIn('0 pièce', pieces)
        self.assertIn('vue=fichiers', pieces)

    def test_U01_pieces_relevees_comptees_et_derniere_nommee(self):
        from agent import modele5651
        modele5651.ensure_schema(self.desk)
        for i, (nom, date) in enumerate((('Assignation DURAND.pdf', '2026-10-01'), ('Conclusions MARTIN.docx', '2026-11-02'))):
            self.desk.db.execute('INSERT INTO d51_documents(matter,path,name,doc_date,updated) VALUES(?,?,?,?,?)',
                                 (self.a, '/Dossiers/DOS-001/%s' % nom, nom, date, self.desk.now()))
        self.desk.db.commit()
        html = dossiers_ui5697.entete_fiche(self.desk, D100.AVOCATE, '/p', self.a)
        pieces = html[html.index('<dt>Pièces sources</dt>'):]
        self.assertIn('2 pièces relevées', pieces)
        self.assertIn('dernière : Conclusions MARTIN.docx', pieces)

    def test_U01_registre_illisible_dit_sans_faux_aucun(self):
        from agent import acces5697
        with patch.object(resultats56100, 'du_dossier', side_effect=RuntimeError('registre illisible')):
            champs = dossiers56100._champs_u111(self.desk, acces5697.acteur_de(D100.AVOCATE), '/p', self.a)
        html = ''.join(v for _, v in champs)
        self.assertEqual([t for t, _ in champs], ['Derniers résultats de l’IA', 'Pièces sources'])
        self.assertIn('Résultats de l’IA indisponibles pour le moment', html)
        self.assertNotIn('Aucun résultat enregistré par l’IA', html)

    def test_U01_reserve_et_aucun_resultat(self):
        resultats56100.rattraper(self.desk)
        self.assertEqual(dossiers_ui5697.entete_fiche(self.desk, D100.AVOCATE, '/p', self.r), '')   # dossier réservé : rien
        vide = self.creer(titre='MARTIN — conseil', client='MARTIN', nature='conseil')['id']
        html = dossiers_ui5697.entete_fiche(self.desk, D100.AVOCATE, '/p', vide)
        self.assertIn('Aucun résultat enregistré par l’IA', html)

    def test_U01_liste_dernier_resultat_ia_par_dossier(self):
        resultats56100.rattraper(self.desk)
        self.sans_historiques()
        with D100.Compteur(self.desk.db) as c:
            html = self.ecran(D100.AVOCATE)
        self.assertEqual(c.dml, [])
        ligne = re.search(r'<tr data-d97-ligne="%s">.*?</tr>' % re.escape(self.a), html, re.S).group(0)
        self.assertIn('data-u111-ia', ligne)
        self.assertIn('/livrables/docA2?version=ver-docA2', ligne)
        self.assertNotIn('docR1', html)                                              # dossier réservé invisible pour l'avocate
        self.assertIn('Responsable', html)
        self.assertIn('Prochaine action', html)
        out = dossiers56100.ia_recents_html(self.desk, D100.AVOCATE, '/p', [self.r])
        self.assertEqual(out, {})


class Relecture(unittest.TestCase):
    def test_U01_envoyer_raccorde_dans_le_lecteur(self):
        from agent import relecture56100, accueil56100, web440
        self.assertIn('/static/v56111-envoi-relecture.js', relecture56100.tete_html('/p'))
        self.assertIn('/static/v56100-lecteur.js', relecture56100.tete_html('/p'))
        self.assertIn('/static/v56111-envoi-relecture.js', accueil56100.lecteur_script('/p'))
        self.assertIn('/static/v56111-envoi-relecture.js', web440.STATIC)
        js = (STATIC / 'v56111-envoi-relecture.js').read_text(encoding='utf-8')
        routes = set(re.findall(r"'/api440/([a-z0-9/]+)", js))
        self.assertEqual(routes, {'send/status', 'send/prepare', 'send/confirm', 'm56100/relecture/'})   # envoi existant seulement
        self.assertIn('Confirmer l’envoi', js)                                       # second clic distinct
        self.assertLess(js.index("'/api440/send/prepare'"), js.index("'/api440/send/confirm'"))
        self.assertIn("if (!jeton) return;", js)                                     # aucune confirmation sans aperçu
        self.assertIn('X-CSRF-Token', js)
        self.assertNotIn('innerHTML', js)

    def test_U01_effets_distincts_dans_le_gabarit(self):
        from agent import relecture56100
        h = relecture56100.lecteur_html('/p')
        for action, libelle in (('retenir', 'Retenir'), ('deposer', 'Déposer dans Brouillons (sans envoi)'), ('enregistrer', 'Enregistrer')):
            self.assertIn('data-action="%s"' % action, h)
            self.assertIn('>%s</button>' % libelle, h)


class Feuille(unittest.TestCase):
    def test_U02_feuille_declaree_et_regeneree(self):
        from agent.shell501 import BUNDLE
        self.assertIn('v56111-ergonomie.css', BUNDLE)
        source = (STATIC / 'v56111-ergonomie.css').read_text(encoding='utf-8').rstrip()
        paquet = (STATIC / 'app520.css').read_text(encoding='utf-8')
        self.assertIn('/* ==== v56111-ergonomie.css ==== */\n' + source, paquet)

    def test_U01_bibliotheque_grille_bornee_a_375(self):
        css = (STATIC / 'v56111-ergonomie.css').read_text(encoding='utf-8')
        self.assertIn('.b5639-list{grid-template-columns:repeat(auto-fill,minmax(min(280px,100%),1fr))}', css)
        from agent.shell501 import BUNDLE
        self.assertGreater(BUNDLE.index('v56111-ergonomie.css'), BUNDLE.index('v5637.css'))   # après la règle d'origine : elle l'emporte

    def test_U02_aucune_cible_reduite_sous_24px(self):
        css = (STATIC / 'v56111-ergonomie.css').read_text(encoding='utf-8')
        for m in re.finditer(r'(?:min-)?(?:height|width):(\d+)px', css):
            if 'min-' in m.group(0):
                self.assertGreaterEqual(int(m.group(1)), 24, m.group(0))


class Traductions(unittest.TestCase):
    PHRASES = ('Derniers résultats de l’IA', 'Pièces sources', 'Aucun résultat enregistré par l’IA', 'Résultats de l’IA indisponibles pour le moment',
               'Aucune pièce relevée pour l’instant', '(le relevé des fichiers peut être en cours)', 'Pièces indisponibles pour le moment',
               '# pièces relevées', '# pièce relevée', 'Envoyer…', 'Envoyer depuis AxiorHub : à activer', 'Confirmer l’envoi', 'Annuler',
               'Pour envoyer : « Déposer dans Brouillons », puis « Envoyer… » sur le brouillon déposé.',
               'Préparation de l’aperçu d’envoi…', 'Vérifiez les destinataires et l’objet, puis confirmez. Rien n’est parti.',
               'Envoi annulé : rien n’est parti.', 'Enregistrez d’abord votre texte : c’est la version enregistrée dans Brouillons qui part.',
               'Brouillon introuvable dans la messagerie : rien n’est envoyé.', 'Envoi impossible pour le moment.', 'Envoi demandé.')

    def test_U01_phrases_nouvelles_traduites(self):
        for langue in LANGUES:
            table = json.loads((ROOT / 'agent' / 'locales' / ('ecrans_%s.json' % langue)).read_text(encoding='utf-8'))
            manquantes = [p for p in self.PHRASES if p not in table]
            self.assertEqual(manquantes, [], langue)
            for p in self.PHRASES:
                self.assertEqual(p.count('#'), table[p].count('#'), (langue, p))
                self.assertNotRegex(table[p], r'\d', (langue, p))                     # nombres écrits « # »


if __name__ == '__main__':
    unittest.main()
