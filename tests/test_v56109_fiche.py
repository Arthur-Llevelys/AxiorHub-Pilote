"""5.6.109 (lot D) : fiche Conseil / Contentieux — C10 (parties lues avec leur contexte), C11 (rôles contractuels sans adverse imposé),
F04 (projection commune ``projection56109``, faits / allégations / procédure / décisions distincts, corrections humaines durables,
personnalisation, routes ``m56109/fiche/…``).

Faux stockage en mémoire, aucun réseau (tout appel de socket échoue), aucun modèle réel (faux modèle en mémoire) ; données fictives
seulement (DURAND, MARTIN, BERNARD, ZORVAL, KERVILAC, ORLANDEC, DOS-001, RG 24/01234, Valbrune, example.test)."""
import io
import json
import socket
import unittest
import uuid
from unittest.mock import patch

from agent import dossiers5697 as D, entete5679 as E, fiche5698 as F, fiche_ui5698 as UI
from agent.common import Stop
from test_v5697_dossiers import ADMIN, AVOCATE, CAMILLE, CLAIRE, JETON, ORIGINE, Base


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


def noms(texte):
    return [p['nom'] for p in E.parties(texte)]


ASSIGNATION = ('TRIBUNAL JUDICIAIRE\nDE VALBRUNE\nRG 24/01234\nPOUR\nMonsieur Paul DURAND\ndemeurant 1 rue Fictive\nMadame Alice DURAND\n'
               'demeurant 1 rue Fictive\nDemandeurs\nAyant pour avocat Maître Jean BERNARD\nCONTRE\nLa société ZORVAL SAS\n'
               'représentée par son président Monsieur Luc KERVILAC\nDéfenderesse\n')
CONTRAT = ('CONTRAT DE CESSION DE PARTS SOCIALES\n\nENTRE LES SOUSSIGNÉS :\n\nLa société ZORVAL SAS, société par actions simplifiée au capital de 10 000 euros, '
           'dont le siège est à Valbrune, immatriculée sous le numéro 000 010 017, représentée par Monsieur Luc KERVILAC, en qualité de président,\n'
           'ci-après dénommée « le Cédant »,\nD’UNE PART,\n\nET :\n\nMadame Alice MARTIN, née le 1er janvier 1980, demeurant à Valbrune,\n'
           'ci-après dénommée « le Cessionnaire »,\nD’AUTRE PART,\n\nIL A ÉTÉ CONVENU CE QUI SUIT :\nArticle 1\nLe Cédant cède au Cessionnaire…\n'
           'Voir Cass. com., société ORLANDEC SARL c/ BERNARD.\n')


# ------------------------------------------------------------------------------------------------ C10 : en-tête lu avec son contexte
class C10Entete(unittest.TestCase):
    def test_C10_ville_seule_apres_l_intitule_n_est_pas_une_partie(self):
        texte = 'TRIBUNAL JUDICIAIRE DE\nVALBRUNE\nRG 24/01234\nMme Alice MARTIN\nDemanderesse\nCONTRE\nMonsieur Paul DURAND\nDéfendeur\n'
        self.assertEqual(noms(texte), ['Mme Alice MARTIN', 'Monsieur Paul DURAND'])

    def test_C10_intitule_sur_trois_lignes(self):
        texte = 'TRIBUNAL\nJUDICIAIRE DE VALBRUNE\nRG 24/01234\nMme Alice MARTIN\nDemanderesse\nCONTRE\nMonsieur Paul DURAND\nDéfendeur\n'
        self.assertEqual(noms(texte), ['Mme Alice MARTIN', 'Monsieur Paul DURAND'])

    def test_C10_formule_executoire_et_titre_ne_sont_pas_des_parties(self):
        texte = ('RÉPUBLIQUE FRANÇAISE\nAU NOM DU PEUPLE FRANÇAIS\nTRIBUNAL JUDICIAIRE DE VALBRUNE\nJUGEMENT DU 3 MARS 2025\nRG 24/01234\n'
                 'Madame Alice MARTIN\nDemanderesse\nCONTRE\nMonsieur Paul DURAND\nDéfendeur\n')
        self.assertEqual(noms(texte), ['Madame Alice MARTIN', 'Monsieur Paul DURAND'])

    def test_C10_section_du_conseil_de_prud_hommes(self):
        texte = "CONSEIL DE PRUD'HOMMES DE VALBRUNE\nSECTION COMMERCE\nRG 24/01234\nMadame Alice MARTIN\nDemanderesse\nCONTRE\nZORVAL SAS\nDéfenderesse\n"
        self.assertEqual(noms(texte), ['Madame Alice MARTIN', 'ZORVAL SAS'])

    def test_C10_un_avocat_en_majuscules_n_est_pas_une_partie(self):
        texte = "COUR D'APPEL DE VALBRUNE\nRG 24/01234\nAPPELANTE :\nMAITRE JEAN BERNARD\nLa société ZORVAL SAS\nINTIMÉ :\nMonsieur Paul DURAND\n"
        self.assertEqual(noms(texte), ['ZORVAL SAS', 'Monsieur Paul DURAND'])

    def test_C10_plusieurs_demandeurs_du_meme_bloc_conserves(self):
        parties = E.parties(ASSIGNATION)
        self.assertEqual([(p['nom'], p['qualite']) for p in parties],
                         [('Monsieur Paul DURAND', 'demandeur'), ('Madame Alice DURAND', 'demandeur'), ('ZORVAL SAS', 'defendeur')])
        self.assertEqual(parties[0]['avocats'], ['Maître Jean BERNARD'])
        # le dirigeant représente la société : il ne devient pas partie, il est gardé comme représentant
        self.assertNotIn('Monsieur Luc KERVILAC', [p['nom'] for p in parties])
        self.assertIn('KERVILAC', parties[2]['representant'])

    def test_C10_source_passage_et_position_conserves(self):
        p = E.parties(ASSIGNATION)[2]
        self.assertEqual(p['zone'], 'qualite')
        self.assertEqual(ASSIGNATION.split('\n')[p['ligne'] - 1].strip(), 'La société ZORVAL SAS')
        self.assertEqual(p['nom_ecrit'], 'La société ZORVAL SAS')
        self.assertEqual(p['nature_personne'], 'morale')
        self.assertIn('Défenderesse', p['extrait'])

    def test_C10_pour_et_contre_sans_qualite(self):
        texte = 'CONCLUSIONS\nTRIBUNAL JUDICIAIRE DE VALBRUNE\nRG 24/01234\nPOUR :\nMadame Alice MARTIN\nCONTRE :\nLa société ZORVAL SAS\nPLAISE AU TRIBUNAL\n'
        parties = E.parties(texte)
        self.assertEqual([(p['nom'], p['qualite'], p['cote']) for p in parties], [('Madame Alice MARTIN', '', 'pour'), ('ZORVAL SAS', '', 'contre')])
        # « POUR » ne dit pas que cette personne est le client du cabinet : aucune clé ne le prétend
        self.assertNotIn('client', parties[0])

    def test_C10_contrat_entre_les_soussignes(self):
        parties = E.contractants(CONTRAT)
        self.assertEqual([(p['nom'], p['qualite']) for p in parties], [('ZORVAL SAS', 'cédant'), ('Madame Alice MARTIN', 'cessionnaire')])
        self.assertEqual(parties[0]['zone'], 'soussignes')
        self.assertIn('KERVILAC', parties[0]['representant'])
        self.assertEqual(parties[0]['nature_personne'], 'morale')
        self.assertEqual(parties[1]['nature_personne'], 'physique')
        self.assertIn('Cessionnaire', parties[1]['extrait'])
        # la société citée dans une jurisprudence, plus bas, n'est pas une partie au contrat
        self.assertNotIn('ORLANDEC', ' '.join(p['nom'] for p in parties))

    def test_C10_contrat_lu_sans_qualite_de_procedure_inventee(self):
        self.assertNotIn('contractants', E.read(CONTRAT))   # ``read`` garde son contrat de 5.6.79
        lu = E.lire_acte(CONTRAT)
        self.assertEqual([p['nom'] for p in lu['contractants']], ['ZORVAL SAS', 'Madame Alice MARTIN'])
        self.assertEqual(lu['parties'], [])   # aucune qualité de procédure : rien n'est inventé

    def test_C10_patch_02_toujours_vrai(self):
        texte = 'TRIBUNAL JUDICIAIRE\nDE LYON\nRG 26/01234\nMme Alice MARTIN\nDemanderesse\nCONTRE\nMonsieur Paul DURAND\nDéfendeur\n'
        self.assertEqual(noms(texte), ['Mme Alice MARTIN', 'Monsieur Paul DURAND'])
        self.assertEqual(E.parties('POUR\nMonsieur de Lyon\nDemandeur\nCONTRE\nDURAND SAS\nDéfendeur')[0]['nom'], 'Monsieur de Lyon')

    def test_C10_mutation_sans_la_borne_de_contexte_la_formule_redevient_partie(self):
        """Garde-fou : sans la borne des lignes de structure, « RÉPUBLIQUE FRANÇAISE » redevient le demandeur."""
        texte = 'RÉPUBLIQUE FRANÇAISE\nTRIBUNAL JUDICIAIRE DE VALBRUNE\nRG 24/01234\nMadame Alice MARTIN\nDemanderesse\n'
        with patch.object(E, '_structure', lambda ligne, precedente='': False):
            self.assertEqual(noms(texte)[0], 'RÉPUBLIQUE FRANÇAISE')
        self.assertEqual(noms(texte), ['Madame Alice MARTIN'])


# ------------------------------------------------------------------------------------------------ base : dossier du registre et extractions
DOC, DOC2 = '/Dossiers/DEMO/assignation.pdf', '/Dossiers/DEMO/conclusions adverses.pdf'


class FicheBase(Base):
    def setUp(self):
        super().setUp()
        from agent import modele5651, projection56109
        self.M, self.P = modele5651, projection56109
        self.M.ensure_schema(self.desk)
        self.did = self.creer(titre='DURAND c/ MARTIN', client='DURAND', nature='contentieux')['id']
        F.ensure_schema(self.desk)

    def document(self, chemin=DOC, nature='assignation', etag='v1'):
        self.desk.db.execute('INSERT OR REPLACE INTO d51_documents(matter,path,etag,name,nature,reading,text_sha,updated) VALUES(?,?,?,?,?,?,?,?)',
                             (self.did, chemin, etag, chemin.rsplit('/', 1)[-1], nature, 'lisible', 'sha-' + etag, '2026-11-02T10:00:00+00:00'))
        self.desk.db.commit()

    def procedure(self, juridiction='Tribunal judiciaire de Valbrune', rg='24/01234'):
        self.desk.db.execute("INSERT INTO d51_procedures(id,matter,juridiction,rg,origin,created,updated) VALUES('proc1',?,?,?,'entete','2026-11-02','2026-11-02')",
                             (self.did, juridiction, rg))
        self.desk.db.commit()
        return 'proc1'

    def extraite(self, nom, role='défendeur', source='assignation.pdf', origine='entete', client=False, procedure='proc1'):
        pid = self.M._party_for(self.desk, self.did, nom, origine, source, client=client, role=role, procedure_id=procedure)
        self.desk.db.commit()
        return pid

    def information(self, texte, kind='fait', proof='a_verifier', author='document', review='propose', sources=None, origin='releve'):
        ident = self.M._upsert_assertion(self.desk, self.did, kind, texte, origin, [{'path': DOC, 'page': 2, 'extrait': texte[:40]}] if sources is None else sources,
                                         author=author, proof=proof, review_state=review)
        self.desk.db.commit()
        return ident

    def api109(self, route, data=None, owner='cabinet', role='administrateur', args=None):
        from agent import web440
        corps = json.dumps(data or {}).encode()
        env = {'HTTP_X_AXIORHUB_USER': owner, 'HTTP_X_AXIORHUB_ROLE': role, 'HTTP_ORIGIN': ORIGINE, 'HTTP_X_CSRF_TOKEN': JETON, 'CONTENT_TYPE': 'application/json',
               'CONTENT_LENGTH': str(len(corps)), 'wsgi.input': io.BytesIO(corps), 'REQUEST_METHOD': 'GET' if data is None else 'POST'}
        out = web440.api(env, self.desk, {'origin': ORIGINE, 'csrf': JETON}, '/p', 'm56109/fiche/' + route, args or {}, env['REQUEST_METHOD'])
        self.assertIn(('Cache-Control', 'private, no-store'), out.get('headers') or [])
        return out['status'][:3], json.loads(out['body'])

    def fiche(self, acteur=ADMIN, profil=None):
        return self.P.fiche(self.desk, acteur, self.did, profil)

    def candidat(self, nom):
        return next(c for c in self.fiche()['candidats_a_verifier'] if c['nom'] == nom)

    def cle(self):
        return uuid.uuid4().hex


# ------------------------------------------------------------------------------------------------ C11 : rôles contractuels, provenance
class C11Roles(FicheBase):
    def test_C11_roles_contractuels_presents_et_jamais_adverse_par_defaut(self):
        self.assertIn('cocontractant', D.ROLES_PARTIES)
        self.assertIn('tiers', D.ROLES_PARTIES)
        self.assertEqual(F.GROUPE_DU_ROLE['cocontractant'], 'contractants')
        self.assertEqual(F.ROLES_AVOCATS, ('avocat_client', 'conseil_adverse', 'avocat_postulant'))
        html = UI.options_roles('')
        self.assertIn('<option value="" selected>Choisir le rôle</option>', html)
        self.assertIn('<option value="cocontractant">Cocontractant</option>', html)
        self.assertNotIn(' selected>Partie adverse', html)
        self.assertNotIn('value="adverse" selected', UI._corps_partie(None, []))   # sans groupe ni rôle : à choisir
        for qualite in ('vendeur', 'acquéreur', 'bailleur', 'preneur', 'cédant', 'cessionnaire', 'cocontractant', 'tiers'):
            self.assertIn(qualite, F.QUALITES)

    def test_C11_un_vendeur_s_inscrit_comme_cocontractant_sans_recherche_de_conflit_adverse(self):
        recu = F.ajouter_partie(self.desk, AVOCATE, self.did, {'nom': 'Monsieur Paul BERNARD', 'role': 'cocontractant', 'qualite': 'vendeur'}, self.cle())
        self.assertNotIn('conflits', recu)
        p = next(x for x in F.parties(self.desk, ADMIN, self.did)['parties'] if x['id'] == recu['id'])
        self.assertEqual((p['role'], p['qualite'], p['groupe'], p['role_par'], p['provenance']), ('cocontractant', 'vendeur', 'contractants', CLAIRE, 'manuel'))

    def test_C11_accepter_une_mention_exige_un_role_choisi(self):
        self.document()
        self.procedure()
        self.extraite('Monsieur Paul BERNARD', role='vendeur')
        c = self.candidat('Monsieur Paul BERNARD')
        self.assertEqual(c['role_suggere'], '')
        proposee = next(p for p in F.parties_proposees(self.desk, self.did, F.parties(self.desk, ADMIN, self.did)['parties']) if p['nom'] == 'Monsieur Paul BERNARD')
        self.assertEqual(proposee['role'], '')
        code, corps = self.api109('dossiers/%s/candidats/%s' % (self.did, c['id']), {'decision': 'accepter', 'nom': c['nom'], 'cle': self.cle()})
        self.assertEqual((code, corps['error']), ('422', 'champs_invalides'))
        self.assertIn('role', corps['champs'])
        self.assertEqual([p['nom'] for p in F.parties(self.desk, ADMIN, self.did)['parties']], ['DURAND'])

    def test_C11_role_humain_et_provenance_conserves(self):
        self.document()
        self.procedure()
        self.extraite('Monsieur Paul BERNARD', role='vendeur')
        c = self.candidat('Monsieur Paul BERNARD')
        cle = self.cle()
        corps_req = {'decision': 'accepter', 'nom': c['nom'], 'qualite': 'vendeur', 'role': 'cocontractant', 'version': c['version'], 'cle': cle}
        code, recu = self.api109('dossiers/%s/candidats/%s' % (self.did, c['id']), corps_req, owner=CLAIRE, role='avocat')
        self.assertEqual(code, '201', recu)
        p = next(x for x in F.parties(self.desk, ADMIN, self.did)['parties'] if x['id'] == recu['id'])
        self.assertEqual((p['role'], p['provenance'], p['role_par']), ('cocontractant', 'piece', CLAIRE))
        self.assertEqual(json.loads(p['source_json'])[0]['path'], DOC)
        self.assertEqual(self.fiche()['candidats_a_verifier'], [])
        code2, recu2 = self.api109('dossiers/%s/candidats/%s' % (self.did, c['id']), corps_req, owner=CLAIRE, role='avocat')   # rejeu : même reçu, aucun second effet
        self.assertEqual((code2, recu2['id'], recu2.get('rejoue')), ('200', recu['id'], True))
        self.assertEqual(len(F.parties(self.desk, ADMIN, self.did)['parties']), 2)
        # un rôle changé ensuite garde son nouvel auteur ; la provenance (lue dans une pièce) ne change pas
        F.modifier_partie(self.desk, ADMIN, self.did, p['id'], p['version'], {**{k: p[k] for k in F.CHAMPS_PARTIE}, 'role': 'tiers'}, self.cle())
        p2 = next(x for x in F.parties(self.desk, ADMIN, self.did)['parties'] if x['id'] == recu['id'])
        self.assertEqual((p2['role'], p2['provenance'], p2['role_par']), ('tiers', 'piece', 'cabinet'))

    def test_C11_formulaire_d_une_mention(self):
        self.document()
        self.procedure()
        self.extraite('Monsieur Paul BERNARD', role='vendeur')
        html = UI.parties_html(self.desk, ADMIN, '/p', {'id': self.did})
        self.assertIn('<option value="" selected>Choisir le rôle</option>', html)
        self.assertIn('name="role" required', html)
        self.assertIn('m56109/fiche/dossiers/%s/candidats/' % self.did, html)
        self.assertIn('Fusionner avec une personne inscrite', html)
        self.assertIn('Écarter', html)
        self.assertIn('<datalist id="d98-qualites">', html)
        self.assertIn('Cocontractants et tiers', html)


# ------------------------------------------------------------------------------------------------ F04 : projection commune
class F04Parties(FicheBase):
    def setUp(self):
        super().setUp()
        self.document()
        self.document(DOC2, 'conclusions')
        self.procedure()

    def accepter(self, mention, role, **plus):
        c = self.candidat(mention)
        code, recu = self.api109('dossiers/%s/candidats/%s' % (self.did, c['id']), {'decision': 'accepter', 'nom': c['nom'], 'role': role, 'version': c['version'],
                                                                                 'cle': self.cle(), **plus})
        self.assertEqual(code, '201', recu)
        return c, recu

    def test_F04_les_mentions_non_rapprochees_ne_contaminent_pas_la_synthese(self):
        from agent import resume5659
        self.extraite('ZORVAL SAS', role='défendeur')
        noms_synthese = [p['nom'] for p in resume5659.representation(self.desk, self.did)]
        self.assertEqual(noms_synthese, ['DURAND'])                       # le client inscrit ; la mention reste « à vérifier »
        f = self.fiche()
        self.assertEqual([c['nom'] for c in f['candidats_a_verifier']], ['ZORVAL SAS'])
        self.assertEqual(f['en_bref']['a_verifier'], 1)
        self.assertEqual(f['candidats_a_verifier'][0]['sources'][0]['path'], DOC)

    def test_F04_mutation_sans_la_projection_la_mention_revient_dans_la_synthese(self):
        """Garde-fou : sans ``unifier_representation``, la mention non validée redevient une partie de la synthèse."""
        from agent import resume5659
        self.extraite('ZORVAL SAS', role='défendeur')
        with patch.object(self.P, 'unifier_representation', lambda desk, mid, extraites: extraites):
            self.assertIn('ZORVAL SAS', [p['nom'] for p in resume5659.representation(self.desk, self.did)])

    def test_F04_correction_humaine_visible_dans_toutes_les_vues_et_durable(self):
        """Corriger une partie puis réanalyser : la correction apparaît dans la synthèse, l'essentiel, les récits et la fiche ; rien ne revient."""
        from agent import dossier5679, recit5651, resume5659
        pid = self.extraite('Monsieur Paul MARTIN', role='défendeur', source='conclusions adverses.pdf')
        self.information('Le contrat a été résilié le 3 mars 2025.', proof='allegue', author=pid, sources=[{'path': DOC2, 'page': 4, 'extrait': 'résilié'}])
        c, recu = self.accepter('Monsieur Paul MARTIN', 'adverse', nom='Monsieur Paul MARTIN-ORLANDEC', qualite='défendeur')
        self.assertEqual(recu['decision'], 'corriger')
        for _ in range(2):   # seconde passe : après une nouvelle analyse des mêmes pièces
            rep = resume5659.representation(self.desk, self.did)
            self.assertEqual([(p['nom'], p['client']) for p in rep], [('DURAND', True), ('Monsieur Paul MARTIN-ORLANDEC', False)])
            self.assertIn('Monsieur Paul MARTIN-ORLANDEC', dossier5679.essentials_html(self.desk, '/p', {'id': self.did}))
            auteurs = {v['auteur'] for v in recit5651._items(self.desk, self.did, 'faits').values()}
            self.assertEqual(auteurs, {'Monsieur Paul MARTIN-ORLANDEC'})
            f = self.fiche()
            self.assertEqual(f['candidats_a_verifier'], [])
            self.assertEqual(f['sections']['allegations'][0]['auteur'], 'Monsieur Paul MARTIN-ORLANDEC')
            inscrite = next(p for p in f['parties_inscrites'] if p['id'] == recu['id'])
            self.assertEqual(inscrite['roles_par_instance'][0]['qualite'], 'défendeur')
            self.extraite('Monsieur Paul MARTIN', role='défendeur', source='conclusions adverses.pdf')   # réanalyse : même mention relue
        self.assertEqual(len(F.parties(self.desk, ADMIN, self.did)['parties']), 2)

    def test_F04_ecarter_est_durable_puis_revient_si_la_source_change(self):
        self.extraite('ZORVAL SAS', role='défendeur')
        c = self.candidat('ZORVAL SAS')
        cle = self.cle()
        code, recu = self.api109('dossiers/%s/candidats/%s' % (self.did, c['id']), {'decision': 'ecarter', 'version': c['version'], 'motif': 'société citée', 'cle': cle})
        self.assertEqual((code, recu['decision']), ('200', 'ecarter'))
        self.assertEqual(self.fiche()['candidats_a_verifier'], [])
        self.extraite('ZORVAL SAS', role='défendeur')                      # réanalyse : la mention ne revient pas
        self.assertEqual(self.fiche()['candidats_a_verifier'], [])
        self.assertEqual(self.desk.db.execute("SELECT status FROM d51_parties WHERE id=?", (c['id'],)).fetchone()[0], 'retiree')
        code2, recu2 = self.api109('dossiers/%s/candidats/%s' % (self.did, c['id']), {'decision': 'ecarter', 'version': c['version'], 'motif': 'société citée', 'cle': cle})
        self.assertEqual((code2, recu2.get('rejoue')), ('200', True))      # rejeu après disparition du candidat : même reçu
        self.document(etag='v2')                                           # la pièce a matériellement changé
        f = self.fiche()
        self.assertEqual([x['nom'] for x in f['candidats_a_verifier']], ['ZORVAL SAS'])
        self.assertIn('Source modifiée', f['candidats_a_verifier'][0]['revu'])
        self.assertEqual(f['a_revoir'], [c['id']])

    def test_F04_fusionner_n_ecrase_aucune_valeur_humaine(self):
        self.extraite('La société DURAND SARL', role='demandeur')          # le registre porte « DURAND » : doublon possible, jamais fusionné d'office
        c = self.candidat('DURAND SARL') if any(x['nom'] == 'DURAND SARL' for x in self.fiche()['candidats_a_verifier']) else self.fiche()['candidats_a_verifier'][0]
        client = F.parties(self.desk, ADMIN, self.did)['parties'][0]
        code, recu = self.api109('dossiers/%s/candidats/%s' % (self.did, c['id']), {'decision': 'fusionner', 'partie_id': client['id'], 'version': c['version'],
                                                                                 'cle': self.cle()})
        self.assertEqual((code, recu['decision']), ('200', 'fusionner'))
        apres = F.parties(self.desk, ADMIN, self.did)['parties'][0]
        self.assertEqual((apres['nom'], apres['role'], apres['version']), (client['nom'], client['role'], client['version']))
        f = self.fiche()
        self.assertEqual(f['candidats_a_verifier'], [])
        self.assertEqual(f['parties_inscrites'][0]['roles_par_instance'][0]['qualite'], 'demandeur')
        self.assertEqual(f['parties_inscrites'][0]['roles_par_instance'][0]['origine'], 'piece')

    def test_F04_homonymes_jamais_fusionnes_d_office(self):
        F.ajouter_partie(self.desk, ADMIN, self.did, {'nom': 'Monsieur Paul MARTIN', 'role': 'adverse'}, self.cle())
        F.ajouter_partie(self.desk, ADMIN, self.did, {'nom': 'Monsieur Paul MARTIN', 'role': 'tiers'}, self.cle())   # deux personnes du même nom
        self.extraite('M. Paul MARTIN', role='défendeur')
        f = self.fiche()
        self.assertEqual([c['nom'] for c in f['candidats_a_verifier']], ['M. Paul MARTIN'])
        self.assertEqual(len(f['candidats_a_verifier'][0]['doublons_possibles']), 2)

    def test_F04_qualite_par_instance(self):
        i1 = F.ajouter_instance(self.desk, ADMIN, self.did, {'degre': 'premiere_instance', 'juridiction': 'Tribunal judiciaire de Valbrune', 'rg': '24/01234'}, self.cle())
        i2 = F.ajouter_instance(self.desk, ADMIN, self.did, {'degre': 'appel', 'juridiction': 'Cour d’appel de Valbrune', 'rg': '25/00012'}, self.cle())
        client = F.parties(self.desk, ADMIN, self.did)['parties'][0]
        for instance, qualite in ((i1['id'], 'défendeur'), (i2['id'], 'appelant')):
            code, recu = self.api109('dossiers/%s/participations' % self.did, {'partie': client['id'], 'instance': instance, 'qualite': qualite, 'version': 0, 'cle': self.cle()})
            self.assertEqual((code, recu['version']), ('200', 1), recu)
        roles = {r['instance']: (r['qualite'], r['origine']) for r in self.fiche()['parties_inscrites'][0]['roles_par_instance']}
        self.assertEqual(roles, {i1['id']: ('défendeur', 'avocat'), i2['id']: ('appelant', 'avocat')})
        code, corps = self.api109('dossiers/%s/participations' % self.did, {'partie': client['id'], 'instance': i1['id'], 'qualite': 'demandeur', 'version': 0, 'cle': self.cle()})
        self.assertEqual((code, corps['error']), ('409', 'version_perimee'))   # deuxième poste, version périmée : rien n'est écrasé
        self.assertEqual({r['instance']: r['qualite'] for r in self.fiche()['parties_inscrites'][0]['roles_par_instance']}[i1['id']], 'défendeur')
        code, corps = self.api109('dossiers/%s/participations' % self.did, {'partie': 'inconnue', 'instance': i1['id'], 'qualite': 'x', 'version': 0, 'cle': self.cle()})
        self.assertEqual((code, 'partie' in corps['champs']), ('422', True))

    def test_C10_mentions_d_un_contrat_proposees_avec_leur_contexte(self):
        chemin = '/Dossiers/DEMO/cession de parts.docx'
        self.document(chemin, 'contrat')
        n = self.P.relever_mentions(self.desk, self.did, [{'path': chemin, 'etag': 'v1', 'sha256': 'sha-contrat', 'text': CONTRAT}])
        self.assertEqual(n, 2)
        self.assertEqual(self.P.relever_mentions(self.desk, self.did, [{'path': chemin, 'etag': 'v1', 'sha256': 'sha-contrat', 'text': CONTRAT}]), 2)   # rejouable
        f = self.fiche()
        cands = {c['nom']: c for c in f['candidats_a_verifier']}
        self.assertEqual(sorted(cands), ['Madame Alice MARTIN', 'ZORVAL SAS'])
        z = cands['ZORVAL SAS']
        self.assertEqual((z['qualite'], z['role_suggere'], z['nature_personne']), ('cédant', '', 'morale'))
        self.assertIn('KERVILAC', z['representant'])
        self.assertEqual(z['sources'][0]['path'], chemin)
        self.assertEqual(z['qualites'][0]['cadre'], 'Contrat : cession de parts.docx')
        self.assertEqual([p['nom'] for p in F.parties(self.desk, ADMIN, self.did)['parties']], ['DURAND'])   # rien n'est inscrit
        m = self.desk.db.execute("SELECT zone,ligne,nom_ecrit,empreinte FROM fiche_mentions56109 WHERE nom='ZORVAL SAS'").fetchone()
        self.assertEqual((m['zone'], m['nom_ecrit'], m['empreinte']), ('soussignes', 'La société ZORVAL SAS', 'sha-contrat'))
        # accepter la cessionnaire comme cocontractante sous un nom corrigé : la mention ne revient pas
        c = cands['Madame Alice MARTIN']
        code, recu = self.api109('dossiers/%s/candidats/%s' % (self.did, c['id']), {'decision': 'accepter', 'nom': 'Madame Alice MARTIN épouse BERNARD',
                                                                                 'role': 'cocontractant', 'cle': self.cle()})
        self.assertEqual(code, '201', recu)
        self.assertEqual([x['nom'] for x in self.fiche()['candidats_a_verifier']], ['ZORVAL SAS'])
        inscrite = next(p for p in self.fiche()['parties_inscrites'] if p['id'] == recu['id'])
        self.assertEqual((inscrite['qualite'], inscrite['provenance']), ('cessionnaire', 'piece'))

    def test_C10_nettoyage_propose_d_apres_la_provenance_jamais_en_masse(self):
        self.desk.db.execute("UPDATE d51_procedures SET juridiction='Tribunal judiciaire de Lyon' WHERE id='proc1'")
        fragment = self.extraite('DE LYON', role='demandeur')              # ancienne extraction fautive (avant 5.6.109)
        self.extraite('Monsieur Jean de Lyon', role='défendeur')           # un vrai patronyme : jamais proposé au nettoyage
        self.M.add_party(self.desk, self.did, 'cabinet', 'LYON SAS')       # saisie par l'avocat : jamais proposée au nettoyage
        cands = {c['nom']: c for c in self.fiche()['candidats_a_verifier']}
        self.assertIn('Tribunal judiciaire de Lyon', cands['DE LYON']['nettoyage'])
        self.assertEqual(cands['Monsieur Jean de Lyon']['nettoyage'], '')
        self.assertEqual(cands['LYON SAS']['nettoyage'], '')
        self.assertFalse(self.P.allowed('administrateur', 'POST', 'm56109/fiche/dossiers/%s/candidats' % self.did))   # aucune route « tout écarter »
        code, _ = self.api109('dossiers/%s/candidats/%s' % (self.did, fragment), {'decision': 'ecarter', 'version': cands['DE LYON']['version'], 'cle': self.cle()})
        self.assertEqual(code, '200')
        self.assertEqual(sorted(c['nom'] for c in self.fiche()['candidats_a_verifier']), ['LYON SAS', 'Monsieur Jean de Lyon'])   # une décision, une ligne


class F04Elements(FicheBase):
    def setUp(self):
        super().setUp()
        self.document()
        self.document(DOC2, 'conclusions')
        self.procedure()
        self.adverse = self.extraite('ZORVAL SAS', role='défendeur', source='conclusions adverses.pdf')

    def test_F04_faits_allegations_procedure_et_decisions_distincts(self):
        constate = self.information('La facture n° 12 a été émise le 3 mars 2025.', proof='constate')
        allegue = self.information('La marchandise a été livrée conforme.', proof='allegue', author=self.adverse, review='enregistre',
                                   sources=[{'path': DOC2, 'page': 3, 'extrait': 'livrée conforme'}])
        self.information('Condamner le défendeur à payer 12 000 euros.', kind='demande', proof='allegue')
        self.information('Le document est au format DOCX.')                                             # purement technique : pas un fait de l'affaire
        enjeu = self.information('Le fichier DOCX a été modifié après signature : son authenticité est contestée.')
        self.information('Fait sans aucune source.', sources=[])
        self.desk.db.execute("INSERT INTO d51_judgments(id,matter,juridiction,date,nature,examined,sources,signature,created,updated) VALUES"
                             "('j1',?,'Tribunal judiciaire de Valbrune','2025-06-10','jugement',0,'[]','s1','2026-11-02','2026-11-02')", (self.did,))
        self.desk.db.execute("INSERT INTO d51_events(id,matter,nature,title,dates,sources,signature,origin,created,updated) VALUES"
                             "('e1',?,'assignation','Assignation délivrée','{\"acte\": \"2025-01-10\", \"signification\": \"2025-01-14\"}',?,'s2','releve','2026-11-02','2026-11-02')",
                             (self.did, json.dumps([{'path': DOC, 'page': 1}])))
        self.desk.db.commit()
        f = self.fiche()
        s = f['sections']
        self.assertEqual(sorted(x['id'] for x in s['faits']), sorted([constate, enjeu]))
        a = s['allegations'][0]
        # une allégation dont la transcription est validée reste attribuée et alléguée : elle n'est jamais rangée dans les faits
        self.assertEqual((a['id'], a['auteur'], a['attribue'], a['force_probante'], a['statut'], a['a_verifier']), (allegue, 'ZORVAL SAS', True, 'allegue', 'confirme', False))
        self.assertEqual(a['decision_humaine'], None)
        self.assertEqual([x['type'] for x in s['pretentions']], ['demande'])
        self.assertEqual(s['procedure'][0]['dates'], {'acte': '2025-01-10', 'signification': '2025-01-14'})
        self.assertEqual((s['decisions'][0]['statut'], s['decisions'][0]['original_examine']), ('rapportee', False))
        self.assertEqual(len(f['couverture']['limites']), 2)                                             # technique et sans source : dits, non repris
        self.assertEqual(s['faits'][0]['sources'][0]['version'], 'v1')
        self.assertEqual((s['faits'][0]['sources'][0]['page'], s['faits'][0]['sources'][0]['document']), (2, 'assignation.pdf'))

    def test_F04_source_retiree_element_a_verifier_et_extrait_masque(self):
        ident = self.information('La facture n° 12 a été émise le 3 mars 2025.', proof='constate', review='enregistre')
        self.assertFalse(next(x for x in self.fiche()['sections']['faits'] if x['id'] == ident)['a_verifier'])
        self.desk.db.execute('DELETE FROM d51_documents WHERE matter=? AND path=?', (self.did, DOC))
        self.desk.db.commit()
        x = next(x for x in self.fiche()['sections']['faits'] if x['id'] == ident)
        self.assertTrue(x['a_verifier'])
        self.assertTrue(x['sources'][0]['indisponible'])
        self.assertNotIn('extrait', x['sources'][0])
        self.assertNotIn('path', x['sources'][0])

    def test_F04_profils_conseil_contentieux_mixte(self):
        self.document('/Dossiers/DEMO/bail commercial.pdf', 'contrat')
        contentieux, conseil, mixte = self.fiche(profil='contentieux'), self.fiche(profil='conseil'), self.fiche(profil='mixte')
        self.assertIn('procedure', contentieux['sections'])
        self.assertNotIn('contrats', contentieux['sections'])
        self.assertIn('contrats', conseil['sections'])
        self.assertNotIn('procedure', conseil['sections'])
        self.assertIn('negociation', conseil['champs'])
        self.assertNotIn('negociation', contentieux['champs'])
        self.assertTrue({'procedure', 'contrats'} <= set(mixte['sections']))
        self.assertEqual(conseil['sections']['contrats'][0]['texte'], 'bail commercial.pdf')
        self.assertEqual(self.fiche()['profil'], 'contentieux')                                           # nature du registre
        code, recu = self.api109('dossiers/%s/profil' % self.did, {'profil': 'mixte', 'version': 0, 'cle': self.cle()})
        self.assertEqual((code, recu['version']), ('200', 1))
        self.assertEqual((self.fiche()['profil'], D.lire(self.desk, ADMIN, self.did)['nature']), ('mixte', 'contentieux'))   # le registre n'est pas modifié
        code, corps = self.api109('dossiers/%s/profil' % self.did, {'profil': 'conseil', 'version': 0, 'cle': self.cle()})
        self.assertEqual((code, corps['error']), ('409', 'version_perimee'))
        code, corps = self.api109('dossiers/%s/profil' % self.did, {'profil': 'atelier', 'version': 1, 'cle': self.cle()})
        self.assertEqual(code, '422')

    def test_F04_lecture_sans_ecriture_ni_modele(self):
        self.information('La facture n° 12 a été émise le 3 mars 2025.', proof='constate')
        self.fiche()                                                       # première lecture : schémas déjà créés au démarrage
        avant = self.desk.db.total_changes
        with patch('agent.docrequest520._model', side_effect=AssertionError('modèle appelé pendant une lecture')):
            self.fiche()
            code, _ = self.api109('dossiers/%s' % self.did, args={'profil': 'conseil'})
            html = UI.fiche_html(self.desk, ADMIN, '/p', {'id': self.did})
        self.assertEqual(code, '200')
        self.assertEqual(self.desk.db.total_changes, avant)
        self.assertIn('id="d109-fiche"', html)

    def test_F04_recit_attribution_fixee_par_le_logiciel(self):
        from agent import recit5651
        self.assertEqual([recit5651.attribution(p) for p in ('constate', 'corrobore', 'retenu_decision', 'allegue', 'conteste', 'a_verifier', '')],
                         ['direct', 'direct', 'direct', 'attribue', 'attribue', 'reserve', 'reserve'])
        self.information('La marchandise a été livrée conforme.', proof='allegue', author=self.adverse, review='enregistre',
                         sources=[{'path': DOC2, 'page': 3, 'extrait': 'livrée conforme'}])
        self.information('La facture n° 12 a été émise le 3 mars 2025.', proof='constate')
        self.information('Le courrier évoque un retard.', proof='a_verifier')
        items = recit5651._items(self.desk, self.did, 'faits')
        self.assertEqual(sorted((v['attribution'], v['controle']) for v in items.values()),
                         [('attribue', 'Enregistrée'), ('direct', 'Proposée'), ('reserve', 'Proposée')])
        prompt = recit5651.SYSTEM['faits']
        self.assertNotIn('Un fait dont l’auteur est « document » s’écrit directement', prompt)           # raccourci retiré
        self.assertIn('n’est jamais présenté comme établi', prompt)
        self.assertIn('« attribue »', prompt)


class F04Champs(FicheBase):
    def setUp(self):
        super().setUp()
        self.document()

    def valeur(self, champ='mission'):
        return self.fiche()['champs'][champ]

    def test_F04_valeur_de_l_avocat_jamais_remplacee_sans_confirmation(self):
        code, recu = self.api109('dossiers/%s/valeurs/mission' % self.did, {'valeur': 'Défendre M. DURAND en première instance.', 'version': 0, 'cle': self.cle()},
                                 owner=CLAIRE, role='avocat')
        self.assertEqual((code, recu['version']), ('200', 1))
        v = self.valeur()
        self.assertEqual((v['valeur'], v['origine'], v['modifie_par']), ('Défendre M. DURAND en première instance.', 'avocat', CLAIRE))
        pid = self.P.deposer_proposition(self.desk, self.did, 'mission', 'Négocier une transaction.', [{'path': DOC, 'page': 1, 'extrait': 'transaction'}], 'Lu en page 1.')
        prop = self.valeur()['propositions'][0]
        self.assertEqual((prop['id'], prop['base_version'], prop['sources'][0]['path']), (pid, 1, DOC))
        corps = {'decision': 'accepter', 'version_attendue': 1, 'base_version': 1}
        code, refus = self.api109('dossiers/%s/propositions/%s' % (self.did, pid), {**corps, 'cle': self.cle()})
        self.assertEqual((code, refus['error']), ('409', 'validation_expresse_requise'))
        self.assertIn('Défendre M. DURAND', refus['champs']['remplacement'])                              # l'ancienne valeur est montrée
        self.assertEqual(self.valeur()['valeur'], 'Défendre M. DURAND en première instance.')
        code, recu = self.api109('dossiers/%s/propositions/%s' % (self.did, pid), {**corps, 'remplacement': True, 'cle': self.cle()}, owner=CLAIRE, role='avocat')
        self.assertEqual((code, recu['version']), ('200', 2))
        v = self.valeur()
        self.assertEqual((v['valeur'], v['origine'], v['sources'][0]['path'], v['propositions']), ('Négocier une transaction.', 'ia_validee', DOC, []))
        histo = self.desk.db.execute('SELECT version,origine,par FROM fiche_valeurs_historique56109 WHERE dossier_id=? ORDER BY version', (self.did,)).fetchall()
        self.assertEqual([tuple(r) for r in histo], [(1, 'avocat', CLAIRE), (2, 'ia_validee', CLAIRE)])
        code, corps = self.api109('dossiers/%s/propositions/%s' % (self.did, pid), {'decision': 'ecarter', 'cle': self.cle()})
        self.assertEqual((code, corps['error']), ('409', 'proposition_deja_decidee'))

    def test_F04_mutation_sans_le_garde_fou_la_proposition_ecraserait_la_saisie(self):
        """Garde-fou : si l'origine « avocat » n'était plus relue, l'acceptation simple remplacerait la valeur humaine."""
        self.P.enregistrer_valeur(self.desk, ADMIN, self.did, 'mission', 'Valeur de l’avocat', 0, self.cle())
        pid = self.P.deposer_proposition(self.desk, self.did, 'mission', 'Valeur proposée', [{'path': DOC}])
        self.desk.db.execute("UPDATE fiche_valeurs56109 SET origine='ia_validee' WHERE dossier_id=?", (self.did,))   # mutation en base de test seulement
        self.desk.db.commit()
        self.P.decider_proposition(self.desk, ADMIN, self.did, pid, {'decision': 'accepter', 'version_attendue': 1, 'base_version': 1}, self.cle())
        self.assertEqual(self.valeur()['valeur'], 'Valeur proposée')

    def test_F04_proposition_ecartee_jamais_reproposee_sauf_source_modifiee(self):
        sources = [{'path': DOC, 'page': 1}]
        pid = self.P.deposer_proposition(self.desk, self.did, 'situation', 'Assignation délivrée.', sources)
        self.assertIsNone(self.P.deposer_proposition(self.desk, self.did, 'situation', 'Assignation délivrée.', sources))   # déjà en attente
        code, recu = self.api109('dossiers/%s/propositions/%s' % (self.did, pid), {'decision': 'ecarter', 'motif': 'inexact', 'cle': self.cle()})
        self.assertEqual((code, recu['decision']), ('200', 'ecarter'))
        self.assertIsNone(self.P.deposer_proposition(self.desk, self.did, 'situation', 'Assignation délivrée.', sources))   # refus durable
        self.assertEqual(self.valeur('situation')['propositions'], [])
        self.document(etag='v2')                                           # la source a matériellement changé
        self.assertIsNotNone(self.P.deposer_proposition(self.desk, self.did, 'situation', 'Assignation délivrée.', sources))
        # sans source résolue dans le dossier, ou pour un champ hors de la liste fermée : rien n'est déposé
        self.assertIsNone(self.P.deposer_proposition(self.desk, self.did, 'situation', 'Autre', [{'path': '/Dossiers/AUTRE/piece.pdf'}]))
        with self.assertRaises(D.Refus):
            self.P.deposer_proposition(self.desk, self.did, 'dossiers5697.titre', 'Injection', sources)

    def test_F04_deux_postes_aucune_saisie_perdue(self):
        self.assertEqual(self.api109('dossiers/%s/valeurs/attendu' % self.did, {'valeur': 'Poste A', 'version': 0, 'cle': self.cle()})[0], '200')
        code, corps = self.api109('dossiers/%s/valeurs/attendu' % self.did, {'valeur': 'Poste B', 'version': 0, 'cle': self.cle()}, owner=CLAIRE, role='avocat')
        self.assertEqual((code, corps['error']), ('409', 'version_perimee'))
        self.assertIn('Poste A', corps['champs']['valeur'])               # la valeur actuelle est rendue ; la saisie reste dans le formulaire
        self.assertEqual(self.valeur('attendu')['valeur'], 'Poste A')
        # proposition fondée sur une version dépassée : refusée, les deux valeurs sont montrées
        pid = self.P.deposer_proposition(self.desk, self.did, 'attendu', 'Proposition', [{'path': DOC}])
        self.api109('dossiers/%s/valeurs/attendu' % self.did, {'valeur': 'Poste A, corrigé', 'version': 1, 'cle': self.cle()})
        code, corps = self.api109('dossiers/%s/propositions/%s' % (self.did, pid), {'decision': 'accepter', 'version_attendue': 2, 'base_version': 1,
                                                                                 'remplacement': True, 'cle': self.cle()})
        self.assertEqual((code, corps['error']), ('409', 'version_perimee'))
        self.assertIn('Poste A, corrigé', corps['champs']['valeur'])
        self.assertIn('Proposition', corps['champs']['valeur'])

    def test_F04_idempotence_et_cle_reutilisee(self):
        cle = self.cle()
        corps = {'valeur': 'Mission', 'version': 0, 'cle': cle}
        un = self.api109('dossiers/%s/valeurs/mission' % self.did, corps)
        deux = self.api109('dossiers/%s/valeurs/mission' % self.did, corps)
        self.assertEqual((un[0], deux[0], deux[1].get('rejoue'), deux[1]['version']), ('200', '200', True, 1))
        code, autre = self.api109('dossiers/%s/valeurs/mission' % self.did, {**corps, 'valeur': 'Autre'})
        self.assertEqual((code, autre['error']), ('409', 'cle_idempotence_deja_utilisee'))
        code, sans = self.api109('dossiers/%s/valeurs/mission' % self.did, {'valeur': 'x', 'version': 1})
        self.assertEqual(code, '422')
        self.assertEqual(self.valeur()['version'], 1)

    def test_F04_champs_types_et_liste_fermee(self):
        code, corps = self.api109('dossiers/%s/valeurs/titre' % self.did, {'valeur': 'x', 'version': 0, 'cle': self.cle()})
        self.assertEqual((code, 'champ' in corps['champs']), ('422', True))
        self.api109('dossiers/%s/profil' % self.did, {'profil': 'conseil', 'version': 0, 'cle': self.cle()})
        code, _ = self.api109('dossiers/%s/valeurs/negociation' % self.did, {'valeur': 'Prix plafonné\n- Garantie de passif\n', 'version': 0, 'cle': self.cle()})
        self.assertEqual(code, '200')
        self.assertEqual(self.valeur('negociation')['valeur'], ['Prix plafonné', 'Garantie de passif'])
        definition = {'type': 'montant'}
        self.assertEqual(self.P.valider_valeur(definition, '1 250,50 €'), {'cents': 125050, 'devise': 'EUR'})
        for mauvais in ('abc', 'NaN', '1e400'):
            with self.assertRaises(D.Refus):
                self.P.valider_valeur(definition, mauvais)
        self.assertEqual(self.P.valider_valeur({'type': 'date'}, '2026-11-23'), '2026-11-23')
        with self.assertRaises(D.Refus):
            self.P.valider_valeur({'type': 'date'}, '23/11/2026')
        with self.assertRaises(D.Refus):
            self.P.valider_valeur({'type': 'choix', 'choix': ['a']}, 'b')
        self.assertIs(self.P.valider_valeur({'type': 'oui_non'}, 'non'), False)
        self.assertIsNone(self.P.valider_valeur({'type': 'oui_non'}, ''))  # inconnu reste inconnu, jamais « non »

    def test_F04_presentation_personnelle_modele_du_cabinet_et_champs_metier(self):
        base = {'portee': 'utilisateur', 'profil': 'contentieux', 'version': 0}
        code, recu = self.api109('presentation', {**base, 'carte_arguments_masquee': True, 'carte_en_bref_masquee': True, 'carte_procedure_ordre': 1,
                                                  'carte_faits_repliee': True, 'html': '<script>alert(1)</script>', 'cle': self.cle()}, owner=CLAIRE, role='avocat')
        self.assertEqual((code, recu['version']), ('200', 1))
        cartes = {c['id']: c for c in self.fiche(AVOCATE)['presentation']['cartes']}
        ordre = [c['id'] for c in self.fiche(AVOCATE)['presentation']['cartes']]
        self.assertEqual(ordre[:2], ['en_bref', 'procedure'])              # la première vue reste en tête
        self.assertTrue(cartes['arguments']['masquee'])
        self.assertFalse(cartes['en_bref']['masquee'])                     # carte non masquable
        self.assertTrue(cartes['faits']['repliee'])
        self.assertNotIn('script', self.desk.db.execute('SELECT config_json FROM fiche_preferences56109').fetchone()[0])
        self.assertFalse({c['id']: c for c in self.fiche(ADMIN)['presentation']['cartes']}['arguments']['masquee'])   # préférence d'un compte, pas du cabinet
        self.assertIn('arguments', self.fiche(AVOCATE)['sections'])       # masquer une carte ne retire aucune donnée
        html = UI.fiche_html(self.desk, AVOCATE, '/p', {'id': self.did})
        self.assertNotIn('id="d109-arguments"', html)
        self.assertIn('Revenir à la présentation du cabinet', html)
        code, recu = self.api109('presentation', {**base, 'version': 1, 'reinitialiser': True, 'cle': self.cle()}, owner=CLAIRE, role='avocat')
        self.assertEqual((code, recu['version']), ('200', 0))
        self.assertFalse({c['id']: c for c in self.fiche(AVOCATE)['presentation']['cartes']}['arguments']['masquee'])
        # modèle du cabinet : droit « réglages » (une avocate sans ce droit : 403) ; champ métier typé
        modele = {'portee': 'cabinet', 'profil': 'contentieux', 'version': 0, 'champ_1_libelle': 'Enjeu financier', 'champ_1_type': 'montant'}
        code, corps = self.api109('presentation', {**modele, 'cle': self.cle()}, owner=CLAIRE, role='avocat')
        self.assertEqual((code, corps['error']), ('403', 'role_insuffisant'))
        code, recu = self.api109('presentation', {**modele, 'cle': self.cle()})
        self.assertEqual(code, '200', recu)
        champ = next(c for c in self.fiche()['champs'].values() if c['carte'] == 'champs')
        self.assertEqual((champ['id'], champ['type']), ('c_enjeu_financier', 'montant'))
        code, corps = self.api109('dossiers/%s/valeurs/c_enjeu_financier' % self.did, {'valeur': 'beaucoup', 'version': 0, 'cle': self.cle()})
        self.assertEqual(code, '422')
        code, _ = self.api109('dossiers/%s/valeurs/c_enjeu_financier' % self.did, {'valeur': '12 000', 'version': 0, 'cle': self.cle()})
        self.assertEqual((code, self.valeur('c_enjeu_financier')['valeur']), ('200', {'cents': 1200000, 'devise': 'EUR'}))
        code, corps = self.api109('presentation', {'portee': 'cabinet', 'profil': 'contentieux', 'version': 1, 'champ_1_libelle': 'X', 'champ_1_type': 'python', 'cle': self.cle()})
        self.assertEqual(code, '422')


class F04Droits(FicheBase):
    def test_F04_dossier_reserve_et_absent_meme_404_avant_toute_lecture(self):
        self.document()
        self.procedure()
        self.extraite('ZORVAL SAS')
        self.P.enregistrer_valeur(self.desk, ADMIN, self.did, 'mission', 'Mission confidentielle', 0, self.cle())
        self.reserver(self.did, [CLAIRE])
        interdit = self.api109('dossiers/%s' % self.did, owner=CAMILLE, role='avocat')
        absent = self.api109('dossiers/%s' % 'inconnu', owner=CAMILLE, role='avocat')
        self.assertEqual(interdit, absent)
        self.assertEqual(interdit[0], '404')
        self.assertNotIn('ZORVAL', json.dumps(interdit[1]))
        c = self.candidat('ZORVAL SAS')
        for route, corps in (('dossiers/%s/valeurs/mission' % self.did, {'valeur': 'x', 'version': 1}),
                             ('dossiers/%s/candidats/%s' % (self.did, c['id']), {'decision': 'ecarter'}),
                             ('dossiers/%s/participations' % self.did, {'partie': 'x', 'instance': 'y'}),
                             ('dossiers/%s/profil' % self.did, {'profil': 'conseil'}),
                             ('dossiers/%s/relire' % self.did, {})):
            code, rep = self.api109(route, {**corps, 'cle': self.cle()}, owner=CAMILLE, role='avocat')
            self.assertEqual((code, rep['error']), ('404', 'introuvable'), route)
        self.assertEqual(self.api109('dossiers/%s' % self.did, owner=CLAIRE, role='avocat')[0], '200')
        self.assertEqual(UI.fiche_html(self.desk, (CAMILLE, 'avocat'), '/p', {'id': self.did}), '')

    def test_F04_mutation_sans_la_garde_le_dossier_reserve_serait_lu(self):
        from agent import acces5697
        self.P.enregistrer_valeur(self.desk, ADMIN, self.did, 'mission', 'Mission confidentielle', 0, self.cle())
        self.reserver(self.did, [CLAIRE])
        with patch.object(acces5697, 'peut_lire_dossier', lambda desk, a, d: True):
            self.assertEqual(self.api109('dossiers/%s' % self.did, owner=CAMILLE, role='avocat')[0], '200')
        self.assertEqual(self.api109('dossiers/%s' % self.did, owner=CAMILLE, role='avocat')[0], '404')

    def test_F04_roles_csrf_et_routes(self):
        from agent import standalone_auth
        for role in ('avocat', 'assistant'):
            self.assertTrue(standalone_auth.allowed(role, 'POST', '/api440/m56109/fiche/dossiers/x/valeurs/mission'))
            self.assertTrue(standalone_auth.allowed(role, 'GET', '/api440/m56109/fiche/dossiers/x'))
        self.assertFalse(self.P.allowed('inconnu', 'GET', 'm56109/fiche/dossiers/x'))
        self.assertFalse(self.P.allowed('avocat', 'POST', 'm56109/fiche/dossiers/x/valeurs'))
        self.assertFalse(self.P.allowed('avocat', 'DELETE', 'm56109/fiche/dossiers/x/valeurs/mission'))
        from agent import web440
        corps = json.dumps({'valeur': 'x', 'version': 0, 'cle': self.cle()}).encode()
        env = {'HTTP_X_AXIORHUB_USER': 'cabinet', 'HTTP_X_AXIORHUB_ROLE': 'administrateur', 'HTTP_ORIGIN': 'https://ailleurs.example.test', 'HTTP_X_CSRF_TOKEN': JETON,
               'CONTENT_TYPE': 'application/json', 'CONTENT_LENGTH': str(len(corps)), 'wsgi.input': io.BytesIO(corps), 'REQUEST_METHOD': 'POST'}
        out = web440.api(env, self.desk, {'origin': ORIGINE, 'csrf': JETON}, '/p', 'm56109/fiche/dossiers/%s/valeurs/mission' % self.did, {}, 'POST')
        self.assertEqual(out['status'][:3], '403')
        self.assertEqual(self.fiche()['champs']['mission']['version'], 0)

    def test_F04_relire_les_actes_fonctionne_sans_ia(self):
        chemin = '/Dossiers/DEMO/cession de parts.docx'
        self.document(chemin, 'contrat')
        with patch('agent.synthese5632.inventory', lambda desk, mid: [{'path': chemin, 'etag': 'v1', 'sha256': 'sha-contrat', 'text': CONTRAT}]), \
                patch('agent.docrequest520._model', side_effect=AssertionError('modèle appelé')):
            code, recu = self.api109('dossiers/%s/relire' % self.did, {'cle': self.cle()}, owner=CLAIRE, role='assistant')
        self.assertEqual((code, recu['mentions']), ('200', 2))
        self.assertEqual(len(self.fiche()['candidats_a_verifier']), 2)


class F04Page(FicheBase):
    def page(self, **args):
        from agent import dossier5679
        return dossier5679.page(self.desk, {'csrf': JETON, 'owner': 'cabinet'}, '/p', {'id': self.did, **args}, lambda titre, corps, *a: corps + (a[3] if len(a) > 3 else ''))

    def test_F04_fiche_dans_la_synthese_et_ancienne_presentation_derriere_l_interrupteur(self):
        from agent import interface56100
        self.document()
        self.procedure()
        pid = self.extraite('ZORVAL SAS')
        self.information('La marchandise a été livrée conforme.', proof='allegue', author=pid, review='enregistre')
        self.P.deposer_proposition(self.desk, self.did, 'mission', 'Défendre le client.', [{'path': DOC, 'page': 1}], 'Lu dans l’assignation.')
        html = self.page()
        self.assertIn('id="d109-fiche"', html)
        self.assertNotIn('d79-warn', html)
        for attendu in ('Mission confiée au cabinet', 'Situation actuelle', 'Dernier élément qui change l’analyse', 'Décision ou action attendue',
                        'Prochaine échéance confirmée', '1 personne(s) lue(s) dans les pièces à vérifier', 'Selon ZORVAL SAS', 'Allégué',
                        'Proposition à vérifier', 'Valeur actuelle', 'Accepter', 'Corriger', 'Écarter', 'Personnaliser la fiche', 'Profil de la fiche'):
            self.assertIn(attendu, html)
        self.assertNotIn('<script>', html.split('id="d109-fiche"')[1].split('</section>')[0])   # aucun script en ligne dans la fiche
        interface56100.regler_cabinet(self.desk, False)
        self.assertNotIn('id="d109-fiche"', self.page())                  # ancienne présentation inchangée
        self.assertEqual(self.api109('dossiers/%s' % self.did)[0], '200')  # les droits et les routes ne dépendent pas de l'interrupteur

    def test_F04_schema_rejouable_et_contrat_d_export(self):
        from agent import export56108
        self.P.ensure_schema(self.desk)
        self.P.ensure_schema(self.desk)
        tables = {r[0] for r in self.desk.db.execute("SELECT name FROM sqlite_master WHERE type='table' AND name LIKE 'fiche_%56109'")}
        self.assertEqual(len(tables), 10)
        declarees = {ligne[2] for ligne in export56108.EXPORT} | {t for t, _ in export56108.EXPORT_EXCLUES}
        self.assertTrue(tables <= declarees, tables - declarees)
        exclues = dict(export56108.EXPORT_EXCLUES)
        self.assertEqual(exclues['fiche_preferences56109'], export56108.MOTIFS['personnel'])              # préférence personnelle : jamais une règle du cabinet
        colonnes = {r[1] for r in self.desk.db.execute('PRAGMA table_info(dossier_parties5697)')}
        self.assertTrue({'provenance', 'source_json', 'role_par', 'role_le'} <= colonnes)
        self.assertIn('/static/v56109-fiche.css', __import__('agent.web440', fromlist=['STATIC']).STATIC)


if __name__ == '__main__':
    unittest.main()
