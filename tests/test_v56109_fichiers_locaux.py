"""5.6.109, lot Q (F13) : rechercher et interroger les fichiers d'un répertoire autorisé du poste.

Services réels (``contexte56109``, ``dav.LectureSeule``, ``index.DocumentIndex``, ``documents.extract_pages``, ``recherche5695``,
Paramètres) sur un répertoire temporaire de FAUX fichiers (DURAND, ZORVAL, Valbrune) ; le poste est simulé par ``AXIORHUB_POSTE_HOME`` et
l'identité du poste du lot O ; le modèle de l'assistant est un FAUX modèle (aucun vrai modèle, aucun réseau). Jonctions Windows, volumes
amovibles, chemins UNC et vrais lecteurs Windows 11 / Ubuntu 24.04 ne sont pas vérifiés ici.
"""
import hashlib
import io
import json
import os
from pathlib import Path
import shutil
import socket
import sqlite3
import tempfile
import unittest
from unittest.mock import patch

from agent import contexte56109 as C, dav, recherche5695 as R
from agent.common import Stop, load_matters
from agent.desk import Desk
from agent.index import DocumentIndex
import test_agent as fx

PREFIX = '/agent-courriel'
DURAND = ('durand@example.test', 'avocat')
MARTIN = ('martin@example.test', 'avocat')
CLAUSE = 'Article 48 — Préavis : la résiliation du bail Valbrune exige un préavis de trois mois notifié à ZORVAL.'


def _refuse_reseau(*a, **k):
    raise AssertionError('aucun appel réseau réel dans ces essais')


def instantane(racine):
    out = {}
    for p in sorted(Path(racine).rglob('*')):
        st = p.stat()
        out[str(p.relative_to(racine))] = (p.is_dir(), st.st_size, st.st_mtime_ns,
                                           None if p.is_dir() or p.suffix == '.sqlite3' else hashlib.sha256(p.read_bytes()).hexdigest())
    return out


class FauxModele:
    def __init__(self, passage=None, ident=None):
        self.appels, self.passage, self.ident = [], passage, ident

    def complete(self, messages, temperature=None, max_tokens=None, schema=None):
        self.appels.append(messages)
        extraits = json.loads(messages[1]['content'])['extraits']
        return {'reponse': 'Le préavis est de trois mois (réponse fictive).', 'manquant': [],
                'citations': [{'id': self.ident or extraits[0]['id'], 'passage': self.passage or 'un préavis de trois mois notifié à ZORVAL'}]}


class Base(unittest.TestCase):
    def setUp(self):
        self.f = fx.EngineTests('test_observation_has_no_mail_write')
        self.f.setUp()
        self.addCleanup(self.f.doCleanups)
        for cible, nom in ((socket.socket, 'connect'), (socket, 'create_connection'), (socket, 'getaddrinfo')):
            p = patch.object(cible, nom, _refuse_reseau)
            p.start()
            self.addCleanup(p.stop)
        p = patch.dict(os.environ, {'AXIORHUB_POSTE_HOME': self.f.c['state_dir']})
        p.start()
        self.addCleanup(p.stop)
        self.d = Desk(self.f.c)
        self.addCleanup(self.d.db.close)
        self.racine = Path(tempfile.mkdtemp(prefix='lotq-travail-'))
        self.addCleanup(shutil.rmtree, self.racine, True)
        (self.racine / 'contrat-valbrune.txt').write_text('Bail commercial fictif entre DURAND et ZORVAL.\n' + 'Clause générale sans objet. ' * 6000 +
                                                          '\n' + CLAUSE + '\n', encoding='utf-8')
        (self.racine / 'sous-dossier').mkdir()
        (self.racine / 'sous-dossier' / 'note.md').write_text('# Note\nLe client MARTIN demande un calendrier des échéances.\n', encoding='utf-8')
        (self.racine / 'lancer.bat').write_text('echo pirate > piege.txt\n', encoding='utf-8')
        base = sqlite3.connect(str(self.racine / 'registre.sqlite3'))
        base.execute('CREATE TABLE t(x)')
        base.commit()
        self.base = base
        self.addCleanup(base.close)
        self.auth = {'origin': 'https://cabinet.example.test', 'csrf': 'test-csrf', 'prefix': PREFIX, 'poste': True}
        self.n = 0

    def cle(self):
        self.n += 1
        return 'cle-f13-essai-%04d' % self.n

    def emplacement(self, acteur=DURAND, chemin=None):
        return C.ajouter(self.d, acteur, str(chemin or self.racine), 'Travail', cle=self.cle(), auth=self.auth)

    def indexer(self, ident, acteur=DURAND, **kw):
        for _ in range(100):
            out = C.indexer(self.d, acteur, ident, checkpoint=lambda d: None, **kw)
            if out['complet']:
                return out
        raise AssertionError('indexation sans fin')

    def api(self, route, data=None, acteur=DURAND, methode='POST', args=None):
        raw = json.dumps(data or {}).encode()
        env = {'REQUEST_METHOD': methode, 'HTTP_ORIGIN': self.auth['origin'], 'HTTP_X_CSRF_TOKEN': 'test-csrf', 'CONTENT_TYPE': 'application/json',
               'CONTENT_LENGTH': str(len(raw)), 'wsgi.input': io.BytesIO(raw), 'HTTP_X_AXIORHUB_ROLE': acteur[1], 'HTTP_X_AXIORHUB_USER': acteur[0]}
        out = C.api(env, self.d, self.auth, PREFIX, C.PREFIXE + route, args or {}, methode)
        out['json'] = json.loads(out['body'])
        return out


class Emplacements(Base):
    def test_F13_ajout_sur_le_poste_seulement(self):
        with patch.dict(os.environ, {'AXIORHUB_POSTE_HOME': ''}):
            with self.assertRaisesRegex(Stop, 'poste_requis'):
                C.ajouter(self.d, DURAND, str(self.racine), 'Travail', cle=self.cle(), auth={'origin': 'x'})
        e = self.emplacement()
        self.assertEqual((e['etat'], e['corpus']), ('jamais', 'emplacement_local'))

    def test_F13_chemins_refuses(self):
        for chemin in ('relatif/travail', str(Path(self.racine.anchor)), str(Path.home()), self.f.c['state_dir'],
                       str(Path(self.f.c['state_dir']) / 'sous')):
            with self.assertRaises(Stop, msg=chemin):
                C.valider_chemin(self.d, chemin)
        lien = self.racine.parent / ('lotq-lien-%d' % os.getpid())
        try:
            os.symlink(self.racine, lien, target_is_directory=True)
        except (OSError, NotImplementedError):
            return                                                         # liens non autorisés sur ce poste : vérifié ailleurs
        self.addCleanup(lambda: os.path.lexists(lien) and os.unlink(lien))
        with self.assertRaisesRegex(Stop, 'chemin_refuse'):
            C.valider_chemin(self.d, str(lien))

    def test_F13_corpus_hors_affaire_explicite(self):
        avant = load_matters(self.f.c)
        e = self.emplacement()
        self.indexer(e['id'])
        self.assertEqual(load_matters(self.f.c), avant)                    # aucun faux dossier client créé
        with self.assertRaisesRegex(Stop, 'dossier_requis'):
            R.chercher(self.d, '', 'préavis')
        passages, etat = R.chercher_corpus(self.d, {'type': 'emplacement_local', 'ids': [e['id']]}, 'préavis résiliation bail Valbrune',
                                           acteur=DURAND[0], role=DURAND[1])
        self.assertEqual(etat['corpus'], 'emplacement_local')
        self.assertTrue(passages)
        principal = DocumentIndex(self.f.c['state_dir'])
        self.addCleanup(principal.db.close)
        globaux, _ = principal.global_sources(['préavis', 'Valbrune', 'ZORVAL'])
        self.assertFalse([g for g in globaux if str(g['matter']).startswith('local56109')])   # la recherche du cabinet ne lit pas l'emplacement

    def test_F13_association_a_un_dossier_lisible_seulement(self):
        from agent import membres5650
        membres5650.set_reserve(self.f.c['state_dir'], 'DOS-001', [MARTIN[0]])
        with self.assertRaisesRegex(Stop, 'introuvable'):
            C.ajouter(self.d, DURAND, str(self.racine), 'Travail', dossier='DOS-001', cle=self.cle(), auth=self.auth)
        e = C.ajouter(self.d, MARTIN, str(self.racine), 'Travail', dossier='DOS-001', cle=self.cle(), auth=self.auth)
        self.assertEqual(e['dossier_associe'], 'DOS-001')


class Index(Base):
    def test_F13_passage_en_fin_de_document_sans_reimporter(self):
        e = self.emplacement()
        premier = self.indexer(e['id'])
        self.assertGreaterEqual(premier['relus'], 2)
        resultats, etat = C.chercher(self.d, DURAND, [e['id']], 'préavis résiliation bail Valbrune')
        self.assertEqual(resultats[0]['nom'], 'contrat-valbrune.txt')
        self.assertIn('trois mois', resultats[0]['extrait'])
        self.assertEqual(resultats[0]['localisateur'], {'type': 'section_logique', 'libelle': 'section logique 1'})   # pas de page inventée
        self.assertTrue(resultats[0]['version'])
        self.assertFalse(etat['recherche_exhaustive'])
        with patch('agent.documents.extract_pages', side_effect=AssertionError('aucune relecture')):
            second = self.indexer(e['id'])
        self.assertEqual((second['relus'], second['inchanges']), (0, 2))

    def test_F13_garde_originaux_jamais_ecrits_ni_code_execute(self):
        avant = instantane(self.racine)
        e = self.emplacement()
        self.indexer(e['id'])
        C.chercher(self.d, DURAND, [e['id']], 'préavis')
        with patch('agent.contexte56109._modele', return_value=FauxModele()):
            C.interroger(self.d, DURAND, [e['id']], 'Quel préavis pour le bail Valbrune ?', self.cle())
        self.assertEqual(instantane(self.racine), avant)                  # aucun fichier créé, modifié ou supprimé dans le répertoire
        self.assertFalse((self.racine / 'piege.txt').exists())            # le script n'a pas été lancé
        etats = {r['chemin_relatif']: r['etat'] for r in self.d.db.execute('SELECT * FROM local56109_documents')}
        self.assertEqual(etats['/lancer.bat'], 'non_pris_en_charge')
        self.assertEqual(etats['/registre.sqlite3'], 'base_non_lue')
        dossier = C._dossier(C._ligne(self.d, e['id']))
        with self.assertRaisesRegex(Stop, 'ecriture_interdite_emplacement_local'):
            dossier.put_file('/nouveau.txt', b'ecriture')
        with self.assertRaisesRegex(Stop, 'ecriture_interdite_emplacement_local'):
            dossier.replace_file('/sous-dossier/note.md', b'x', '"etag"')
        self.assertFalse((self.racine / 'nouveau.txt').exists())
        # mutation : sans la garde de lecture seule, le même appel écrirait dans le répertoire (essai sur une copie)
        copie = Path(tempfile.mkdtemp(prefix='lotq-copie-'))
        self.addCleanup(shutil.rmtree, copie, True)
        dav.LocalFolder({'local_path': str(copie), 'roots': ['/']}).put_file('/nouveau.txt', b'ecriture')
        self.assertTrue((copie / 'nouveau.txt').exists())

    def test_F13_base_sqlite_jamais_ouverte_par_un_descripteur(self):
        e = self.emplacement()
        vrai, ouverts = os.open, []

        def espion(p, *a, **k):
            ouverts.append(str(p))
            return vrai(p, *a, **k)
        with patch('agent.dav.os.open', espion):
            self.indexer(e['id'])
        self.assertFalse([p for p in ouverts if p.endswith('registre.sqlite3')])
        with self.assertRaisesRegex(Stop, 'fichier_base_sqlite_non_lu'):
            C._dossier(C._ligne(self.d, e['id'])).download({'path': '/registre.sqlite3', 'size': 1})

    def test_F13_fichier_modifie_revalide_avant_affichage(self):
        e = self.emplacement()
        self.indexer(e['id'])
        paquet, resultats, _ = C.contexte(self.d, DURAND, [e['id']], 'préavis résiliation bail Valbrune')
        r = resultats[0]
        cible = self.racine / 'contrat-valbrune.txt'
        cible.write_text(cible.read_text(encoding='utf-8').replace('trois mois', 'six mois'), encoding='utf-8')
        cites = C.controler_citations(self.d, DURAND, [e['id']], paquet, [{'id': paquet['extraits'][0]['citation'],
                                                                            'passage': 'un préavis de trois mois notifié à ZORVAL'}])
        self.assertEqual((cites[0]['etat'], cites[0]['motif']), ('a_verifier', 'source_modifiee'))
        ouvert = C.passage(self.d, DURAND, e['id'], r['doc_id'], r['version'], r['page'], r['extrait_no'])
        self.assertEqual(ouvert['etat'], 'source_modifiee')                 # rien d'ancien n'est servi
        self.indexer(e['id'])
        nouveaux, _ = C.chercher(self.d, DURAND, [e['id']], 'préavis résiliation bail Valbrune')
        self.assertIn('six mois', nouveaux[0]['extrait'])
        self.assertNotEqual(nouveaux[0]['version'], r['version'])

    def test_F13_fichier_supprime_retire_de_l_index(self):
        e = self.emplacement()
        self.indexer(e['id'])
        (self.racine / 'contrat-valbrune.txt').unlink()
        self.indexer(e['id'])
        self.assertFalse(C.chercher(self.d, DURAND, [e['id']], 'préavis résiliation bail Valbrune')[0])

    def test_F13_emplacement_indisponible_ni_vide_ni_recree(self):
        e = self.emplacement()
        self.indexer(e['id'])
        ailleurs = self.racine.with_name(self.racine.name + '-deplace')
        self.base.close()
        self.racine.rename(ailleurs)
        self.addCleanup(shutil.rmtree, ailleurs, True)
        with self.assertRaisesRegex(Stop, 'emplacement_indisponible'):
            C.indexer(self.d, DURAND, e['id'], checkpoint=lambda d: None)
        self.assertEqual(C.etat(self.d, DURAND)['emplacements'][0]['etat'], 'indisponible')
        self.assertFalse(self.racine.exists())

    def test_F13_pause_pendant_l_indexation_reprise_au_curseur(self):
        e = self.emplacement()

        def pause(_d):
            raise Stop('operation_annulee')
        with self.assertRaisesRegex(Stop, 'operation_en_pause'):
            C.indexer(self.d, DURAND, e['id'], checkpoint=pause)
        self.assertEqual(self.d.db.execute('SELECT COUNT(*) FROM local56109_documents').fetchone()[0], 0)
        self.assertEqual(self.indexer(e['id'])['complet'], True)


class Droits(Base):
    def test_F13_garde_refus_distinct_de_aucun_document(self):
        e = self.emplacement()
        self.indexer(e['id'])
        resultats, etat = C.chercher(self.d, MARTIN, [e['id']], 'préavis')
        self.assertEqual((resultats, etat['refus']), ([], 'acces_non_autorise'))
        self.assertIn('Absence d’autorisation', etat['message'])
        self.assertNotIn('aucun document', etat['message'].lower().replace('absence de documents', ''))
        route = self.api('chercher', methode='GET', acteur=MARTIN, args={'ids': e['id'], 'q': 'préavis'})
        inexistant = self.api('chercher', methode='GET', acteur=MARTIN, args={'ids': 'E-inexistant', 'q': 'préavis'})
        self.assertEqual((route['status'], route['json']), (inexistant['status'], inexistant['json']))     # même réponse neutre
        self.assertEqual(route['status'], '404 Not Found')
        vide, etat = C.chercher(self.d, DURAND, [e['id']], 'cassation hypothécaire maritime')
        self.assertEqual((vide, etat['etat']), ([], 'aucun_passage_trouve_dans_le_perimetre_consulte'))
        # mutation : un contrôle qui ignorerait le propriétaire laisserait MARTIN lire l'emplacement de DURAND
        with patch('agent.contexte56109.lisible', side_effect=lambda desk, acteur, ident: C._ligne(desk, ident)):
            fuite, _ = C.chercher(self.d, MARTIN, [e['id']], 'préavis résiliation bail Valbrune')
        self.assertTrue(fuite)

    def test_F13_revocation_retire_acces_et_passages(self):
        e = self.emplacement()
        self.indexer(e['id'])
        C.revoquer(self.d, DURAND, e['id'])
        self.assertEqual(C.chercher(self.d, DURAND, [e['id']], 'préavis')[1]['refus'], 'acces_non_autorise')
        index = DocumentIndex(self.f.c['state_dir'], fichier=Path(self.f.c['state_dir']) / C.RACINE / 'documents.sqlite3')
        self.addCleanup(index.db.close)
        self.assertEqual(index.db.execute('SELECT COUNT(*) FROM knowledge_chunks WHERE matter=?', (C._partition(e['id']),)).fetchone()[0], 0)
        self.assertEqual(C.etat(self.d, DURAND)['emplacements'][0]['libelle_etat'], 'Accès retiré')

    def test_F13_autre_poste_refuse(self):
        e = self.emplacement()
        with patch('agent.contexte56109._poste', return_value='autre-poste'):
            self.assertEqual(C.chercher(self.d, DURAND, [e['id']], 'préavis')[1]['refus'], 'acces_non_autorise')

    def test_F13_routes_roles_idempotence(self):
        e = self.api('ajouter', {'chemin': str(self.racine), 'libelle': 'Travail', 'cle': 'cle-f13-route-01'})
        self.assertEqual(e['status'], '200 OK')
        self.assertIn(('Cache-Control', 'private, no-store'), e['headers'])
        self.assertEqual(self.api('ajouter', {'chemin': str(self.racine), 'cle': 'cle-f13-route-02'}, acteur=('a@example.test', 'assistant'))['json']['error'],
                         'role_insuffisant')
        ident = e['json']['id']
        self.assertEqual(self.api('indexer', {'id': ident, 'cle': 'cle-f13-route-03'})['status'], '200 OK')
        self.assertEqual(self.api('revoquer', {'id': ident, 'version': 99, 'cle': 'cle-f13-route-04'})['status'], '409 Conflict')
        self.assertEqual(self.api('revoquer', {'id': ident, 'cle': 'cle-f13-route-05'}, acteur=MARTIN)['status'], '404 Not Found')


class Reponses(Base):
    def setUp(self):
        super().setUp()
        self.e = self.emplacement()
        self.indexer(self.e['id'])

    def test_F13_citation_relue_puis_verifiee(self):
        modele = FauxModele()
        with patch('agent.contexte56109._modele', return_value=modele):
            out = C.interroger(self.d, DURAND, [self.e['id']], 'Quel préavis pour le bail Valbrune ?', 'cle-f13-q-0001')
        self.assertEqual(out['citations'][0]['etat'], 'verifiee')
        self.assertFalse(out['recherche_exhaustive'])
        envoye = json.loads(modele.appels[0][1]['content'])
        self.assertNotIn(str(self.racine), json.dumps(envoye, ensure_ascii=False))       # aucun chemin du poste donné au modèle
        with patch('agent.contexte56109._modele', side_effect=AssertionError('pas de second appel')):
            again = C.interroger(self.d, DURAND, [self.e['id']], 'Quel préavis pour le bail Valbrune ?', 'cle-f13-q-0001')
        self.assertEqual(again, out)

    def test_F13_garde_citation_inventee_ou_inconnue_jamais_verifiee(self):
        with patch('agent.contexte56109._modele', return_value=FauxModele(passage='un préavis de six mois sans notification')):
            out = C.interroger(self.d, DURAND, [self.e['id']], 'Quel préavis pour le bail Valbrune ?', self.cle())
        self.assertEqual((out['citations'][0]['etat'], out['citations'][0]['motif']), ('a_verifier', 'passage_non_retrouve'))
        with patch('agent.contexte56109._modele', return_value=FauxModele(ident='S-inconnu')):
            out = C.interroger(self.d, DURAND, [self.e['id']], 'Quel préavis pour le bail Valbrune ?', self.cle())
        self.assertEqual((out['citations'][0]['etat'], out['citations'][0]['motif']), ('a_verifier', 'citation_inconnue'))

    def test_F13_ia_en_pause_passages_sources_sans_modele(self):
        from agent.pause5629 import PAUSE_IA
        self.d.setting(PAUSE_IA, True)
        with patch('agent.contexte56109._modele', side_effect=AssertionError('aucun appel pendant la pause')):
            out = C.interroger(self.d, DURAND, [self.e['id']], 'Quel préavis pour le bail Valbrune ?', self.cle())
        self.assertEqual(out['etat'], 'ia_en_pause')
        self.assertTrue(out['passages'])

    def test_F13_citation_ouvrable(self):
        r = C.chercher(self.d, DURAND, [self.e['id']], 'préavis résiliation bail Valbrune')[0][0]
        ouvert = self.api('passage', methode='GET', args={'emplacement': self.e['id'], 'doc': r['doc_id'], 'version': r['version'], 'page': r['page'],
                                                          'extrait': r['extrait_no']})
        self.assertEqual(ouvert['json']['etat'], 'a_jour')
        self.assertIn('trois mois', ouvert['json']['texte'])


class Contrats(unittest.TestCase):
    """Contrats de ``reference/contexte_poste109.py`` rejoués sur la reprise produit."""

    def setUp(self):
        self.versions = {'A': 'v1', 'B': 'v1'}
        self.lisibles = {'A', 'B'}
        self.pauses = 0

    def run_assembler(self, candidats, **kw):
        return C.assembler(candidats, perimetre={'A', 'B'}, peut_lire=lambda s: s in self.lisibles, version_courante=lambda s: self.versions[s],
                           lire_extrait=lambda c: 'Texte fictif de ' + c.source_id + ' ' * 10, checkpoint=lambda: None, **kw)

    def test_F13_contexte_versionne_hors_perimetre_revoque_modifie(self):
        p = self.run_assembler([C.Candidat('A', 'v1', 'p. 1')])
        self.assertEqual(p['documents_cites'], 1)
        for cand, motif in ((C.Candidat('Z', 'v1', 'p. 1'), 'source_hors_perimetre'), (C.Candidat('B', 'v0', 'p. 1'), 'source_modifiee')):
            with self.assertRaisesRegex(C.ContexteInvalide, motif):
                self.run_assembler([cand])
        self.lisibles = {'A'}
        with self.assertRaisesRegex(C.ContexteInvalide, 'source_revoquee'):
            self.run_assembler([C.Candidat('B', 'v1', 'p. 1')])
        with self.assertRaisesRegex(C.ContexteInvalide, 'citation_inconnue'):
            C.revalider(p, ['S-inconnu'], perimetre={'A'}, peut_lire=lambda s: True, version_courante=lambda s: 'v1', checkpoint=lambda: None)

    def test_F13_budget_et_troncature_signales(self):
        p = self.run_assembler([C.Candidat('A', 'v1', 'p. 1'), C.Candidat('B', 'v1', 'p. 2')], maximum_caracteres=128)
        self.assertFalse(p['recherche_exhaustive'])
        with self.assertRaises(ValueError):
            self.run_assembler([C.Candidat('A', 'v1', 'p. 1')], maximum_caracteres=10)


class Ecran(Base):
    def test_F13_section_des_parametres(self):
        from agent import parametres5621
        self.emplacement()

        class Cap:
            head = ''
        env = {'HTTP_X_AXIORHUB_USER': DURAND[0], 'HTTP_X_AXIORHUB_ROLE': DURAND[1], 'QUERY_STRING': ''}
        html = parametres5621.BUILDERS['poste'](self.d, self.auth, PREFIX, env, Cap())
        self.assertIn('Fichiers de ce poste', html)
        self.assertIn('Jamais indexé', html)
        self.assertNotIn(str(self.racine), html)                           # le chemin privé n'est pas affiché dans la page


if __name__ == '__main__':
    unittest.main()
