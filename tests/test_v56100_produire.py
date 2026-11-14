"""5.6.100, lot E : Produire — documents prêts au premier plan, version exacte, correction IA ciblée, comparaison, téléchargement, export (P4, V100-05).

Données fictives (DURAND, MARTIN, ZORVAL, DOS-001, example.test) ; stockage simulé (``Stockage`` de ``test_v5689_livrables``), modèle factice, aucun
réseau, aucun éditeur réel. Ce qui exige un vrai EuroOffice (édition humaine réelle pendant une génération), un vrai Nextcloud (export réellement relu)
ou un vrai navigateur (suivi de l'export à l'écran, retour au filtre initial) n'est pas prouvé ici : ces tests vérifient le serveur.
"""
from datetime import datetime, timedelta, timezone
import json
import re
import unittest
from unittest.mock import patch
from urllib.parse import quote

import test_v5689_livrables as t89
from test_v5689_livrables import AVOCAT, MID, texte_docx
from agent import arret5643, interface56100, livrables5689 as liv, livrables_export5689 as export, produire56100 as p100, propositions5689 as patches
from agent import livrables_web5689 as web
from agent.common import Stop

PRODUCTION = '/p/production'


def q(valeur):
    return quote(valeur, safe='')


class Base(t89.Base):
    def section(self, args=None, who=AVOCAT):
        return p100.section_html(self.desk, '/p', who[0], who[1], args or {})

    def document(self, ident, titre='Conclusions fictives ZORVAL', kind='conclusions', state='verified', review='to_review', jours=0, matter=MID, path=''):
        stamp = (datetime.now(timezone.utc) - timedelta(days=jours, minutes=int(re.sub(r'\D', '', ident) or 0))).isoformat()
        self.desk.db.execute('INSERT INTO prepared_items5689(id,document_id,version_id,version_number,matter_id,kind,title,path,sha256,source_service,source_object_id,source_version,'
                             "production_state,lifecycle,review_state,source_at,created,updated) VALUES(?,?,?,1,?,?,?,?,?,'essai',?,'1',?,'current',?,?,?,?)",
                             (ident, 'doc-' + ident, 'v-' + ident, matter, kind, titre, path, '', ident, state, review, stamp, stamp, stamp))
        self.desk.db.commit()

    def base(self):
        return self.demande(raw=texte_docx('Conclusions pour DURAND contre MARTIN.', 'Le préjudice est évalué à mille euros.', 'Par ces motifs.'))

    def carte(self, html, doc):
        debut = html.index('data-livrable="%s"' % doc)
        return html[debut:html.index('</li>', debut)]


# ====================================================================================== P4 : documents prêts d'abord
class Liste(Base):
    def test_P4_documents_prets_familles_et_etat_filtres_dans_la_requete(self):
        for i in range(12):
            self.document('d%02d' % i, 'Conclusions fictives n° %d' % i)
        self.document('c01', 'Réponse au confrère MARTIN', kind='courriel', state='preparing')
        self.document('e01', 'Note en préparation', kind='note', state='preparing')
        html = self.section()
        self.assertEqual(len(re.findall(r'<li class="p100-carte"', html)), 10)
        self.assertNotIn('Réponse au confrère MARTIN', html)                            # les courriels gardent leur filtre et leur lecteur
        self.assertNotIn('Note en préparation', html)                                    # onglet « Prêts » d'abord
        self.assertIn('Prêts <span class="q51-n">12</span>', html)
        self.assertIn('En préparation <span class="q51-n">1</span>', html)
        self.assertIn('Tous <span class="q51-n">13</span>', html)
        suivant = re.search(r'href="/p/production\?docs=pret&amp;curseur=([^"]+)">Documents suivants', html)
        self.assertTrue(suivant)
        page2 = self.section({'docs': 'pret', 'curseur': suivant.group(1).replace('&amp;', '&')})
        self.assertEqual(len(re.findall(r'<li class="p100-carte"', page2)), 2)
        prep = self.section({'docs': 'en_cours'})
        self.assertIn('Note en préparation', prep)
        self.assertIn('Le téléchargement sera possible une fois le fichier vérifié', prep)
        self.assertNotIn('>Télécharger<', prep)
        self.assertIn('Documents prêts', self.section({'docs': 'pret', 'curseur': '!!!'}))   # curseur abîmé : première page, jamais une erreur

    def test_P4_carte_type_dossier_titre_version_etat_et_lien_de_la_version(self):
        row = self.base()
        carte = self.carte(self.section(), row['document_id'])
        self.assertIn('<span class="p100-type">Contrat</span>', carte)
        self.assertIn('Version 1', carte)
        self.assertIn('data-etat="a_relire">À relire</span>', carte)
        lien = '/p/livrables/%s?version=%s&amp;return_to=%%2Fp%%2Fproduction' % (row['document_id'], q(row['version_id']))
        self.assertIn('href="%s"' % lien, carte)

    def test_P4_etats_reels(self):
        cas = {('preparing', 'to_review', 0): 'En préparation', ('published', 'to_review', 0): 'En préparation', ('failed', 'to_review', 0): 'Échec',
               ('partial', 'to_review', 0): 'À compléter', ('verified', 'to_review', 1): 'À compléter', ('verified', 'changes_requested', 0): 'À compléter',
               ('verified', 'to_review', 0): 'À relire', ('verified', 'reviewed', 0): 'Prêt', ('verified', 'not_required', 0): 'Prêt'}
        for (production, revue, reserve), attendu in cas.items():
            self.assertEqual(p100.etat_reel({'production_state': production, 'review_state': revue, 'reserve': reserve})[1], attendu, (production, revue, reserve))

    def test_P4_actions_dans_l_ordre_et_selon_droits_et_capacites(self):
        row = self.base()
        carte = self.carte(self.section(), row['document_id'])
        ordre = [carte.index(x) for x in ('>Aperçu<', '>Modifier<', 'Demander une correction à l’IA', '>Télécharger<', '>Exporter<')]
        self.assertEqual(ordre, sorted(ordre))
        self.assertNotIn('>Comparer<', carte)                                          # une seule version : rien à comparer
        self.assertIn('telecharger?version=' + q(row['version_id']), carte)
        self.caps = {'editeur': False, 'convertisseur': False}
        carte = self.carte(self.section(), row['document_id'])
        self.assertNotIn('>Modifier<', carte)
        self.assertIn('PDF : conversion indisponible', carte)
        with patch('agent.membres5650.effective_rights', return_value={'exporter': False, 'valider': True}):
            carte = self.carte(self.section(), row['document_id'])
        self.assertNotIn('>Télécharger<', carte)
        self.assertNotIn('>Exporter<', carte)
        self.desk.db.execute("UPDATE prepared_items5689 SET protected='signe' WHERE id=?", (row['id'],))
        self.desk.db.commit()
        self.assertNotIn('Demander une correction à l’IA', self.carte(self.section(), row['document_id']))   # original signé : jamais modifié

    def test_P4_dossier_reserve_retire_avant_comptes(self):
        self.document('d01', 'Conclusions publiques')
        self.document('d02', 'Conclusions réservées KERVILAC', matter='DOS-006')
        with patch('agent.livrables5689.droits_requete', return_value=(frozenset({'DOS-006'}), {'voir_honoraires': True})):
            html = self.section()
        self.assertNotIn('KERVILAC', html)
        self.assertIn('Prêts <span class="q51-n">1</span>', html)

    def test_P4_affichage_sans_ecriture_ni_schema(self):
        self.base()
        self.section()                                                                   # base déjà préparée au démarrage (Desk)
        traces = []
        self.desk.db.set_trace_callback(lambda sql: traces.append(sql) if re.match(r'\s*(INSERT|UPDATE|DELETE|REPLACE|CREATE|ALTER|DROP)\b', sql, re.I) else None)
        self.addCleanup(self.desk.db.set_trace_callback, None)
        self.section()
        self.section({'docs': 'tout'})
        self.assertEqual(traces, [])
        lectures = self.nc.lectures
        self.section()
        self.assertEqual(self.nc.lectures, lectures)                                     # aucun parcours du stockage à l'affichage

    def test_P4_schema_prepare_au_demarrage(self):
        from agent.desk import Desk
        tables = {r[0] for r in self.desk.db.execute("SELECT name FROM sqlite_master WHERE type='table'")}
        self.assertTrue({'prepared_items5689', 'document_patches5689', 'prepared_exports5689'} <= tables)
        with open(Desk.__init__.__code__.co_filename, encoding='utf-8') as source:
            self.assertIn('produire56100', source.read())


# ====================================================================================== P4 : correction IA ciblée
class Correction(Base):
    def demander(self, row, cle='cle-p100-0001', **champs):
        corps = {'cle': cle, 'instruction': 'Porter le préjudice à deux mille euros', 'version_id': row['version_id'], 'sha256': row['sha256'], **champs}
        return self.api('livrables/%s/modifier-ia' % row['document_id'], body=corps)

    def modele(self, pendant=None):
        class Modele:
            def complete(inner, messages, **kw):
                if pendant:
                    pendant()
                payload = json.loads(messages[1]['content'])
                index = next(p['index'] for p in payload['paragraphes'] if 'mille' in p['texte'])
                return json.dumps({'propositions': [{'index': index, 'texte': 'Le préjudice est évalué à deux mille euros.', 'motif': 'Consigne', 'sources': ['Pièce n° 3 fictive']}]})
        return Modele()

    def test_P4_modifier_ia_version_de_base_zone_cle_et_avant_apres_sources(self):
        row = self.base()
        p = self.js(self.demander(row, zone='Le préjudice est évalué'))
        self.assertEqual(p['state'], 'demandee')
        self.assertEqual(self.js(self.demander(row, zone='Le préjudice est évalué'))['id'], p['id'])     # même clé : même demande, un seul travail
        stored = self.desk.db.execute('SELECT selection, base_version_id, base_sha256 FROM document_patches5689 WHERE id=?', (p['id'],)).fetchone()
        self.assertEqual(tuple(stored), ('Le préjudice est évalué', row['version_id'], row['sha256']))
        with patch('agent.contexte5651.for_drafting', return_value={}):
            out = patches.travail_ia(self.desk, {'proposition': p['id']}, self.modele(), self.nc)
        self.assertEqual(out['state'], 'proposee')
        prop = self.js(self.api('documents/%s/propositions' % row['document_id']))['propositions'][0]
        self.assertEqual(prop['comparaison'][0]['avant'], 'Le préjudice est évalué à mille euros.')
        self.assertEqual(prop['comparaison'][0]['apres'], 'Le préjudice est évalué à deux mille euros.')
        self.assertEqual(prop['sources'], ['Pièce n° 3 fictive'])
        self.assertEqual(self.nc.ecritures, [])                                          # rien n'est écrit sans décision

    def test_P4_base_perimee_refusee_409(self):
        row = self.base()
        self.nc.add(row['path'], texte_docx('Autre texte.'))
        liv.synchroniser_fichiers(self.desk, self.nc)
        self.assertEqual(self.demander(row)['status'], '409 Conflict')

    def test_P4_document_modifie_pendant_la_generation_conflit_sans_ecrasement(self):
        row = self.base()
        humain = texte_docx('Conclusions pour DURAND contre MARTIN.', 'Le préjudice est évalué à mille cinq cents euros.', 'Par ces motifs.')
        p = self.js(self.demander(row))
        with patch('agent.contexte5651.for_drafting', return_value={}):
            out = patches.travail_ia(self.desk, {'proposition': p['id']}, self.modele(pendant=lambda: self.nc.add(row['path'], humain)), self.nc)
        self.assertEqual(out['state'], 'conflit')
        self.assertEqual(self.nc.files[row['path']], humain)                            # la nouvelle version n'est jamais écrasée
        self.assertEqual(self.nc.ecritures, [])
        courant = liv.charger(self.desk, row['document_id'])
        self.assertEqual(courant['version_number'], 2)                                   # l'édition humaine devient une version
        prop = self.js(self.api('documents/%s/propositions' % row['document_id']))['propositions'][0]
        self.assertEqual(prop['state'], 'conflit')
        self.assertTrue(prop['comparaison'])                                             # la proposition reste lisible pour comparer
        _titre, detail = web._detail_page(self.desk, {'HTTP_X_AXIORHUB_USER': AVOCAT[0], 'HTTP_X_AXIORHUB_ROLE': AVOCAT[1]}, {'csrf': 'jeton'}, '/p', row['document_id'], {}, lambda t, b, *a, **k: (t, b))
        self.assertIn('Comparer avec la version de base', detail)
        self.assertIn('Relancer sur la version actuelle', detail)
        self.assertEqual(self.decider_ia(prop)['status'], '409 Conflict')               # accepter quand même : refusé, rien d'écrit
        self.assertEqual(self.nc.files[row['path']], humain)

    def decider_ia(self, p):
        return self.api('documents/propositions/%s/decider' % p['id'], body={'decision': 'accepter', 'version_id': p['base_version_id'], 'sha256': p['base_sha256']})

    def test_P4_document_modifie_avant_la_lecture_rien_transmis_au_modele(self):
        row = self.base()
        p = self.js(self.demander(row))
        self.nc.add(row['path'], texte_docx('Édition humaine.'))
        appels = []

        class Modele:
            def complete(inner, messages, **kw):
                appels.append(messages)
                return '{}'
        out = patches.travail_ia(self.desk, {'proposition': p['id']}, Modele(), self.nc)
        self.assertEqual((out['state'], appels), ('conflit', []))


# ====================================================================================== P4 : comparer
class Comparer(Base):
    def deux_versions(self):
        row = self.base()
        from agent import documents5697
        documents5697.schema(self.desk)
        self.desk.db.execute('INSERT INTO documents5697_textes(document_id,version_number,item_id,texte,auteur,le) VALUES(?,?,?,?,?,?)',
                             (row['document_id'], 1, row['id'], 'Conclusions pour DURAND contre MARTIN.\nLe préjudice est évalué à mille euros.\nPar ces motifs.', 'banc', '2026-11-01'))
        self.desk.db.commit()
        self.nc.add(row['path'], texte_docx('Conclusions pour DURAND contre MARTIN.', 'Le préjudice est évalué à deux mille euros.', 'Par ces motifs.'))
        liv.synchroniser_fichiers(self.desk, self.nc)
        return row, liv.charger(self.desk, row['document_id'])

    def test_P4_comparer_deux_versions_lues_chacune_pour_elle_meme(self):
        v1, v2 = self.deux_versions()
        self.assertEqual(v2['version_number'], 2)
        out = self.js(self.api('livrables/%s/comparer' % v2['document_id'], args={'version': v2['version_id'], 'de': v1['version_id']}))
        self.assertEqual((out['de']['version'], out['a']['version'], out['changements']), (1, 2, 2))
        self.assertEqual([x['op'] for x in out['lignes'] if x['op'] != 'egal'], ['retrait', 'ajout'])
        carte = self.carte(self.section(), v2['document_id'])
        self.assertIn('comparer=' + q(v1['version_id']), carte)
        html = p100.comparaison_html(self.desk, v2, v1['version_id'])
        self.assertIn('Retiré :</span> <del>Le préjudice est évalué à mille euros.</del>', html)
        self.assertIn('Ajouté :</span> <ins>Le préjudice est évalué à deux mille euros.</ins>', html)

    def test_P4_ancienne_version_illisible_signalee_jamais_remplacee_par_la_derniere(self):
        v1, v2 = self.deux_versions()
        self.desk.db.execute('DELETE FROM documents5697_textes')
        self.desk.db.commit()
        out = p100.comparer(self.desk, v2, v1['version_id'], client=self.nc)
        self.assertEqual((out['indisponible'], out['lignes']), ('de', []))
        html = p100.comparaison_html(self.desk, v2, v1['version_id'], client=self.nc)
        self.assertIn('aucune autre version n’est montrée à sa place', html)
        self.assertNotIn('deux mille', html)

    def test_P4_comparer_respecte_le_dossier_reserve(self):
        v1, v2 = self.deux_versions()
        with patch('agent.membres5650.hidden_for', return_value=frozenset({MID})):
            out = self.api('livrables/%s/comparer' % v2['document_id'], args={'de': v1['version_id']})
        self.assertTrue(out['status'].startswith(('403', '404')))
        self.assertNotIn('mille', out['body'])


# ====================================================================================== P4 : export, téléchargement, IA en pause
class Export(Base):
    def lancer(self, row, fmt='docx', dest='dest-exports', cle='cle-p100-export1'):
        return self.js(self.api('livrables/%s/exporter' % row['document_id'], body={'format': fmt, 'destination': dest, 'cle': cle, 'version_id': row['version_id']}))

    def test_P4_export_format_et_destination_explicites_etats_affiches(self):
        row = self.base()
        carte = self.carte(self.section(), row['document_id'])
        self.assertIn('<select name="format" required><option value="">Choisir un format…</option>', carte)
        self.assertIn('<select name="destination" required><option value="">Choisir une destination…</option>', carte)
        self.assertIn('L’export n’envoie aucun message au client', carte)
        self.assertEqual(self.api('livrables/%s/exporter' % row['document_id'], body={'format': '', 'destination': 'dest-exports', 'cle': 'cle-p100-vide01', 'version_id': row['version_id']})['status'],
                         '400 Bad Request')
        out = self.lancer(row)
        self.assertIn('Export en attente', self.carte(self.section(), row['document_id']))
        export.executer(self.desk, {'export': out['id']}, self.nc)
        self.assertIn('Export vérifié', self.carte(self.section(), row['document_id']))
        with patch('agent.pieces510.docx_to_pdf', side_effect=Stop('conversion_indisponible')):
            pdf = self.lancer(row, 'pdf', cle='cle-p100-export2')
            with self.assertRaises(Stop):
                export.executer(self.desk, {'export': pdf['id']}, self.nc)
        self.assertIn('Conversion indisponible', self.carte(self.section(), row['document_id']))

    def test_P4_export_lie_a_la_version_exacte(self):
        row = self.base()
        self.nc.add(row['path'], texte_docx('Version suivante fictive.'))
        liv.synchroniser_fichiers(self.desk, self.nc)
        out = self.api('livrables/%s/exporter' % row['document_id'], body={'format': 'docx', 'destination': 'dest-exports', 'cle': 'cle-p100-ancien', 'version_id': row['version_id']})
        self.assertEqual(out['status'], '409 Conflict')                                  # la version affichée n'est plus la version courante

    def test_P4_ia_en_pause_ouvrir_editer_telecharger_et_suivre_un_export(self):
        row = self.base()
        self.desk.setting('missions567:pause', True)
        html = self.section()
        self.assertIn('IA en pause : ouvrir, modifier à la main, télécharger et suivre un export restent possibles', html)
        carte = self.carte(html, row['document_id'])
        for action in ('>Aperçu<', '>Modifier<', '>Télécharger<', '>Exporter<', 'la demande sera enregistrée et traitée à la reprise'):
            self.assertIn(action, carte)
        dl = self.api('livrables/%s/telecharger' % row['document_id'], args={'version': row['version_id']})
        self.assertEqual(dl['status'], '200 OK')
        out = self.lancer(row)
        self.desk.active_job_id = out['job_id']                                          # exécuté comme par le worker, pause en vigueur
        self.addCleanup(setattr, self.desk, 'active_job_id', None)
        self.assertEqual(export.executer(self.desk, {'export': out['id']}, self.nc)['state'], 'exporte')
        self.assertEqual(self.js(self.api('livrables/exports/' + out['id']))['state'], 'exporte')
        # mutation en mémoire : si l'export n'était plus un travail sans modèle, la pause l'arrêterait (le test le verrait)
        out2 = self.lancer(row, cle='cle-p100-export3')
        self.desk.active_job_id = out2['job_id']
        with patch.object(arret5643, 'DETERMINISTIC', arret5643.DETERMINISTIC - {'export_livrable5689'}), patch.object(arret5643, 'SANS_MODELE', arret5643.SANS_MODELE - {'export_livrable5689'}):
            with self.assertRaisesRegex(Stop, 'ia_en_pause'):
                export.executer(self.desk, {'export': out2['id']}, self.nc)
        demande = self.api('livrables/%s/modifier-ia' % row['document_id'], body={'cle': 'cle-p100-pause1', 'instruction': 'Corriger la date', 'version_id': row['version_id'], 'sha256': row['sha256']})
        self.assertEqual(self.js(demande)['state'], 'demandee')                          # enregistrée, traitée à la reprise


# ====================================================================================== V100-05 : même projet sur trois écrans, interrupteur
class Ecrans(Base):
    def test_V100_05_meme_projet_trois_ecrans_meme_version(self):
        row = self.base()
        from agent import accueil5689
        produire = self.section()
        aujourdhui = accueil5689.travail_html(self.desk, '/p')
        dossier = web.section_html(self.desk, '/p', MID, 8, '/p/dossier?id=' + MID)
        for html in (produire, aujourdhui, dossier):
            self.assertIn('/p/livrables/' + row['document_id'], html)
        self.assertIn('version=' + q(row['version_id']), produire)

    def test_V100_05_styles(self):
        from agent import shell501, web440
        css = (t89.Path(web.__file__).parent / 'static' / 'v56100-produire.css').read_text(encoding='utf-8')
        self.assertIn(':focus-visible', css)
        self.assertIn('@media (max-width: 40rem)', css)
        self.assertIn('v56100-produire.css', shell501.BUNDLE)
        self.assertIn('/static/v56100-produire.css', web440.STATIC)


class Pages(unittest.TestCase):
    """Application réelle (WSGI), compte administrateur d'essai : /production et /aujourdhui?vue=file servis par ``web.py``."""
    import test_v5648 as _base
    PATH = _base.Base.PATH
    setUp = _base.Base.setUp
    call = _base.Base.call

    def page(self, path, query=''):
        r = self.call(path, query=query)
        self.assertTrue(r['status'].startswith('200'), (path, r['status']))
        return r['body'].decode('utf-8')

    def semer(self):
        from test_v5689_carbon import document
        document(self.desk, 'p100a', title='Conclusions fictives ZORVAL')

    def test_V100_05_production_documents_prets_avant_la_file_et_interrupteur(self):
        self.semer()
        html = self.page('/production')
        self.assertIn('id="p100-documents"', html)
        self.assertIn('Conclusions fictives ZORVAL', html)
        self.assertLess(html.index('id="p100-documents"'), html.index('class="q51'))     # documents prêts avant la file
        self.assertNotIn('id="l89-section"', html)                                        # pas de seconde liste du même registre
        self.assertIn('aria-current="page"', html)                                        # onglets de Produire (navigation5643) inchangés
        interface56100.regler_cabinet(self.desk, False)
        ancien = self.page('/production')
        self.assertNotIn('id="p100-documents"', ancien)
        self.assertIn('id="l89-section"', ancien)

    def test_V100_04_vue_file_servie_avant_le_poste_de_pilotage(self):
        from test_v5695_aujourdhui import MESURE
        from agent import echeances450
        echeances450.ensure_schema(self.desk)
        MESURE.echeance(self.desk, 'ech-p100', MID, datetime.now(timezone.utc).date().isoformat(), 'a_confirmer')
        self.desk.db.commit()
        html = self.page('/aujourdhui', 'vue=file&file=a_verifier')
        self.assertIn('id="f100-file"', html)
        self.assertIn('data-f96-ligne="echeance:ech-p100"', html)
        self.assertNotIn('id="c530"', html)                                               # pas le poste de pilotage complet
        self.assertIn('id="c530"', self.page('/aujourdhui', 'file=decider'))              # lien historique : poste de pilotage, file filtrée
        interface56100.regler_cabinet(self.desk, False)
        self.assertIn('class="q51', self.page('/aujourdhui', 'vue=file&file=decider'))   # interrupteur éteint : file historique


if __name__ == '__main__':
    unittest.main()
