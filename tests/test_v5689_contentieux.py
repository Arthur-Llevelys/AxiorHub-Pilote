"""5.6.89, lot contentieux : matrice, pièces par partie, acte par section, contrôles, dossier d'audience, candidats délais, paquet autorisé.

Données fictives (DURAND, ZORVAL, KERVILAC, DOS-001, RG 24/01234, Valbrune, example.test), stockage et modèle simulés : aucun réseau, aucun modèle réel, aucun service de dépôt.
Le contenu des règles de délai est vérifié ici dans sa forme (source, périmètre, statut non validé) ; sa justesse juridique reste à valider par un avocat.
"""
from datetime import date
import hashlib
import io
import json
from pathlib import Path
import re
import unittest
from unittest.mock import patch
import zipfile

import test_agent
from test_v5689_livrables import Stockage
from agent import assertions5689, contentieux5689 as base, contentieux_actes5689 as actes, contentieux_paquets5689 as paquets, contentieux_pieces5689 as pieces, contentieux_web5689 as web
from agent import deadlines450, delais5689 as delais, echeances450, export5688, legal_research, membres5650, memoire5651, modele5651, pieces520, procedure5632, web5689
from agent.budget_ledger5689 import AttenteBudget
from agent.common import Stop
from agent.desk import Desk

MID = 'DOS-001'
AVOCAT, ASSISTANT = ('avocate@example.test', 'avocat'), ('assistant@example.test', 'assistant')
TRIBUNAL = 'Tribunal judiciaire de Valbrune'
SPEC = Path(__file__).resolve().parent / 'contrats5689'
COMPLET = {'regime_commun': 'oui', 'recours_ouvert': 'oui', 'siege_juridiction': 'metropole', 'lieu_partie': 'metropole', 'matiere': 'contentieuse', 'mentions_680': 'oui', 'aide_juridictionnelle': 'non'}


class ModeleFaux:
    """Répond selon une fonction ; compte les appels. Aucune sortie réseau."""

    def __init__(self, reponse):
        self.reponse, self.appels = reponse, []

    def complete(self, messages, **kw):
        self.appels.append(messages)
        out = self.reponse(json.loads(messages[-1]['content']), len(self.appels))
        return out if isinstance(out, str) else json.dumps(out, ensure_ascii=False)


def redaction(donnees, numero):
    """Une section correcte : elle cite la première pièce autorisée par son jeton."""
    jeton = donnees['pieces'][0]['jeton'] if donnees['pieces'] else ''
    return {'texte': 'Il ressort du dossier que le solde reste dû %s.' % jeton, 'reserves': []}


class Base(unittest.TestCase):
    def setUp(self):
        self.f = test_agent.EngineTests('test_observation_has_no_mail_write')
        self.f.setUp()
        self.addCleanup(self.f.doCleanups)
        self.desk = Desk(self.f.c)
        self.addCleanup(self.desk.db.close)
        self.nc = Stockage()
        p = patch('agent.office440.dav_client', return_value=self.nc)
        p.start()
        self.addCleanup(p.stop)
        base.schema(self.desk)
        legal_research.ensure_schema(self.desk)
        modele5651.ensure_schema(self.desk)
        memoire5651.ensure_schema(self.desk)
        assertions5689.ensure_schema(self.desk)
        self.n = 0
        self.addCleanup(membres5650.clear)

    def cle(self):
        self.n += 1
        return 'cle-essai-%04d' % self.n

    def fichier(self, path, data):
        """Fichier présent au stockage et au registre des fichiers."""
        self.nc.add(path, data)
        self.desk.db.execute('INSERT OR REPLACE INTO exhibit_registry_v240 VALUES(?,?,?,?,?,?,?,?,?,?)', (MID, path, path.rsplit('/', 1)[-1], hashlib.sha256(data).hexdigest(), self.nc.etags[path], len(data), '', 'x', 'x', 'present'))
        self.desk.db.commit()
        return path

    def parties(self):
        pid = modele5651.save_procedure(self.desk, MID, 'a', {'juridiction': TRIBUNAL, 'rg': '24/01234', 'reference': True})
        durand = modele5651.add_party(self.desk, MID, 'a', 'Madame Alice DURAND', client=True)
        zorval = modele5651.add_party(self.desk, MID, 'a', 'Société ZORVAL')
        modele5651.refresh(self.desk, MID)
        for p in modele5651.parties(self.desk, MID):      # le client du registre des dossiers est aussi une partie
            self.desk.db.execute('INSERT OR REPLACE INTO d51_participations VALUES(?,?,?,?)', (pid, p['id'], 'défenderesse' if p['id'] == zorval else 'demanderesse', ''))
        self.desk.db.commit()
        self.assertTrue(durand)
        return pid

    def pretention(self, valider=True, montant='1500', titre='Paiement du solde'):
        return base.creer_pretention(self.desk, MID, {'cle': self.cle(), 'titre': titre, 'demande': 'Condamner ZORVAL à payer le solde', 'montant': montant, 'valider': valider}, *AVOCAT)

    def element(self, claim, kind, texte='Élément du dossier fictif', **autres):
        return base.creer_element(self.desk, MID, {'cle': self.cle(), 'claim_id': claim['id'], 'kind': kind, 'texte': texte, 'valider': True, **autres}, *AVOCAT)

    def piece(self, numero='1', cote='cabinet', partie='', titre='Facture fictive', data=None, **autres):
        path = self.fichier('/Dossiers/DEMO/Pieces/%s-%s-%s.pdf' % (cote, partie or 'x', numero.replace(' ', '')), data if data is not None else ('%s %s %s' % (cote, partie, numero)).encode())
        return pieces.ajouter(self.desk, MID, {'cle': self.cle(), 'cote': cote, 'partie': partie, 'numero': numero, 'titre': titre, 'path': path, **autres}, *AVOCAT)

    def matrice_complete(self):
        """Prétention validée, fait, argument, règle vérifiée, pièce du cabinet : de quoi rédiger sans réserve."""
        self.parties()
        claim, piece = self.pretention(), self.piece()
        fait = self.element(claim, 'fait', 'La facture reste impayée', exhibit_id=piece['id'])
        self.element(claim, 'argument', 'Le prix convenu est exigible')
        self.element(claim, 'regle', 'Code civil, article fictif', source_verifiee=True)
        self.element(claim, 'piece', 'Facture', exhibit_id=piece['id'])
        return claim, piece, fait

    def acte(self, **autres):
        return actes.creer(self.desk, MID, {'cle': self.cle(), 'type_acte': 'conclusions', 'titre': 'Conclusions DURAND', **autres}, *AVOCAT)

    def rediger(self, modele=None):
        a = self.acte()
        actes.travail(self.desk, {'acte': a['id'], 'matter': MID, 'owner': AVOCAT[0], 'role': AVOCAT[1]}, ModeleFaux(redaction) if modele is None else modele)
        return actes.vue(self.desk, a['id'])

    def section(self, ident, kind):
        return next(s for s in actes.vue(self.desk, ident)['sections'] if s['kind'] == kind)

    def controle(self, resultat, ident):
        return next(c for c in resultat['controles'] if c['id'] == ident)

    def api(self, name, args=None, body=None, who=AVOCAT):
        raw = json.dumps(body or {}).encode()
        env = {'HTTP_X_AXIORHUB_USER': who[0], 'HTTP_X_AXIORHUB_ROLE': who[1], 'HTTP_ORIGIN': 'https://cabinet.example.test', 'HTTP_X_CSRF_TOKEN': 'jeton', 'CONTENT_LENGTH': str(len(raw)), 'CONTENT_TYPE': 'application/json', 'wsgi.input': io.BytesIO(raw), 'REQUEST_METHOD': 'POST' if body is not None else 'GET'}
        return web5689.api(env, self.desk, {'origin': 'https://cabinet.example.test', 'csrf': 'jeton'}, '/p', 'm5689/' + name, args or {}, 'POST' if body is not None else 'GET')

    def js(self, out):
        return json.loads(out['body'])


class Matrice(Base):
    def test_proposition_ia_reste_proposee_sans_action_de_validation(self):
        claim = self.pretention()
        lien = base.creer_element(self.desk, MID, {'cle': self.cle(), 'claim_id': claim['id'], 'kind': 'argument', 'texte': 'Argument proposé par le modèle', 'valider': True, 'statut': 'valide'}, *AVOCAT, origine='ia')
        self.assertEqual(lien['statut'], 'propose')
        self.assertEqual(lien['auteur_type'], 'ia')
        with self.assertRaises(Stop) as refus:
            base.decider(self.desk, MID, {'objet': 'element', 'id': lien['id'], 'action': 'valider', 'version': 1}, *ASSISTANT)
        self.assertEqual(str(refus.exception), 'role_insuffisant')
        self.assertEqual(base._lien(self.desk, MID, lien['id'])['statut'], 'propose')
        fait = base.decider(self.desk, MID, {'objet': 'element', 'id': lien['id'], 'action': 'valider', 'version': 1}, *AVOCAT)
        self.assertEqual((fait['statut'], fait['valide_par']), ('valide', AVOCAT[0]))
        trace = self.desk.db.execute("SELECT * FROM contentieux_journal5689 WHERE objet_id=? AND action='valider'", (lien['id'],)).fetchone()
        self.assertEqual(trace['acteur'], AVOCAT[0])

    def test_matrice_connu_conteste_manquant_avec_justification_et_auteur(self):
        claim = self.pretention()
        piece = self.piece()
        prouve = self.element(claim, 'fait', 'Fait prouvé', exhibit_id=piece['id'], justification='La facture le montre')
        sans_preuve = self.element(claim, 'fait', 'Fait sans pièce')
        conteste = self.element(claim, 'fait', 'Fait discuté', exhibit_id=piece['id'])
        self.element(claim, 'objection', 'ZORVAL conteste la livraison', parent_id=conteste['id'], affirme_par='Société ZORVAL')
        regle = self.element(claim, 'regle', 'Article cité sans lecture')
        vue = base.matrice(self.desk, MID)[0]
        etats = {x['id']: x['etat'] for x in vue['elements']['fait'] + vue['elements']['regle']}
        self.assertEqual(etats[prouve['id']], 'connu')
        self.assertEqual(etats[sans_preuve['id']], 'manquant')
        self.assertEqual(etats[conteste['id']], 'conteste')
        self.assertEqual(etats[regle['id']], 'manquant')       # citée, jamais lue : pas « connue »
        self.assertEqual(vue['elements']['fait'][0]['justification'], 'La facture le montre')
        self.assertEqual(vue['elements']['fait'][0]['auteur'], AVOCAT[0])
        self.assertEqual(vue['elements']['objection'][0]['affirme_par'], 'Société ZORVAL')
        self.assertEqual(len(vue['objections_sans_reponse']), 1)
        self.assertIn('Argument', [m['libelle'] for m in vue['manques']])

    def test_memoire_typee_source_ouvrable_et_contestation_suivie(self):
        ref = assertions5689.evenement_manuel(self.desk, MID, 'avocat', 'Livraison le 3 mars 2026')
        element = assertions5689.ajouter(self.desk, MID, {'cle': self.cle(), 'kind': 'fact', 'statement': 'Livraison le 3 mars 2026', 'asserted_by': 'Madame Alice DURAND', 'source_ref': ref}, *AVOCAT)
        claim = self.pretention()
        lien = self.element(claim, 'fait', 'Livraison effectuée', assertion_ref=element['assertion_id'])
        self.assertEqual(lien['affirme_par'], 'Madame Alice DURAND')
        vue = base.matrice(self.desk, MID)[0]['elements']['fait'][0]
        self.assertEqual(vue['source']['sha256'], ref['sha256'])
        self.assertEqual(vue['source']['version_id'], ref['version_id'])
        self.desk.db.execute("UPDATE case_assertions5689 SET status='disputed' WHERE id=?", (element['assertion_id'],))
        self.desk.db.commit()
        self.assertEqual(base.matrice(self.desk, MID)[0]['elements']['fait'][0]['etat'], 'conteste')
        with self.assertRaises(Stop) as absent:
            self.element(claim, 'fait', 'Référence inventée', assertion_ref='inconnue')
        self.assertEqual(str(absent.exception), 'information_absente')

    def test_source_d_une_version_connue_controlee_par_la_memoire(self):
        claim = self.pretention()
        ref = assertions5689.evenement_manuel(self.desk, MID, 'avocat', 'Saisie fictive')
        with self.assertRaises(Stop) as faux:
            self.element(claim, 'fait', 'Fait', source_ref={**ref, 'sha256': 'a' * 64})
        self.assertEqual(str(faux.exception), 'empreinte_source_differente')
        self.assertEqual(self.element(claim, 'fait', 'Fait', source_ref=ref)['statut'], 'valide')

    def test_propositions_ia_reference_inventee_ecartee_et_rien_de_valide(self):
        claim = self.pretention()
        piece = self.piece()

        def reponse(donnees, numero):
            self.assertEqual({x['id'] for x in donnees['pieces']}, {piece['id']})
            return {'elements': [{'claim_id': claim['id'], 'kind': 'fait', 'texte': 'Fait appuyé', 'justification': 'Lien', 'exhibit_id': piece['id'], 'assertion_ref': ''},
                                 {'claim_id': claim['id'], 'kind': 'piece', 'texte': 'Pièce inventée', 'justification': '', 'exhibit_id': 'f' * 32, 'assertion_ref': ''},
                                 {'claim_id': 'pretention-inconnue', 'kind': 'fait', 'texte': 'Hors matrice', 'justification': '', 'exhibit_id': '', 'assertion_ref': ''}]}
        bilan = base.travail_matrice(self.desk, {'matter': MID, 'owner': AVOCAT[0], 'role': AVOCAT[1]}, ModeleFaux(reponse))
        self.assertEqual((bilan['creees'], bilan['ecartees']), (1, 2))
        self.assertEqual(bilan['etat'], 'attente_decision')
        lignes = self.desk.db.execute('SELECT statut,auteur_type FROM contentieux_links5689').fetchall()
        self.assertEqual([tuple(x) for x in lignes], [('propose', 'ia')])

    def test_regle_citee_par_le_modele_jamais_verifiee(self):
        claim = self.pretention()
        lien = base.creer_element(self.desk, MID, {'cle': self.cle(), 'claim_id': claim['id'], 'kind': 'regle', 'texte': 'Article 9999 inventé', 'source_verifiee': True}, *AVOCAT, origine='ia')
        self.assertEqual(lien['source_regle'], 'non_verifiee')

    def test_version_perimee_et_idempotence(self):
        claim = self.pretention(valider=False)
        with self.assertRaises(Stop) as vieux:
            base.decider(self.desk, MID, {'objet': 'pretention', 'id': claim['id'], 'action': 'valider', 'version': 9}, *AVOCAT)
        self.assertEqual(str(vieux.exception), 'version_en_conflit')
        donnees = {'cle': 'cle-unique-matrice', 'titre': 'Dommages', 'demande': 'Réparer'}
        un = base.creer_pretention(self.desk, MID, donnees, *AVOCAT)
        self.assertEqual(base.creer_pretention(self.desk, MID, donnees, *AVOCAT)['id'], un['id'])
        with self.assertRaises(Stop) as autre:
            base.creer_pretention(self.desk, MID, {**donnees, 'titre': 'Autre demande'}, *AVOCAT)
        self.assertEqual(str(autre.exception), 'cle_idempotence_deja_utilisee')

    def test_remplacer_garde_l_ancien_element(self):
        claim = self.pretention()
        vieux = self.element(claim, 'argument', 'Première rédaction')
        neuf = base.decider(self.desk, MID, {'objet': 'element', 'id': vieux['id'], 'action': 'remplacer', 'version': vieux['revision'], 'texte': 'Seconde rédaction', 'cle': self.cle()}, *AVOCAT)
        self.assertEqual(neuf['remplace'], vieux['id'])
        self.assertEqual(base._lien(self.desk, MID, vieux['id'])['statut'], 'remplace')
        self.assertEqual([x['texte'] for x in base.matrice(self.desk, MID)[0]['elements']['argument']], ['Seconde rédaction'])

    def test_amorcer_depuis_la_memoire_une_seule_fois_sans_valider(self):
        adverse = modele5651.add_party(self.desk, MID, 'a', 'Société KERVILAC')
        modele5651.add_party(self.desk, MID, 'a', 'Madame Alice DURAND', client=True)
        modele5651._upsert_assertion(self.desk, MID, 'demande', 'Paiement de la somme réclamée', 'test', [{'path': '/Dossiers/DEMO/a.pdf'}], amount='2500', currency='EUR', proof='corrobore')
        self.desk.db.commit()
        self.assertTrue(adverse)
        self.assertEqual(base.amorcer(self.desk, MID, *AVOCAT)['creees'], 1)
        self.assertEqual(base.amorcer(self.desk, MID, *AVOCAT)['creees'], 0)
        ligne = self.desk.db.execute('SELECT * FROM contentieux_claims5689').fetchone()
        self.assertEqual((ligne['statut'], ligne['montant'], ligne['auteur_type']), ('propose', '2500.00', 'memoire'))

    def test_contrat_litigation_packet(self):
        claim, piece, fait = self.matrice_complete()
        contrat = json.loads((SPEC / 'LitigationPacket.schema.json').read_text(encoding='utf-8'))
        paquet = base.litigation_packet(self.desk, MID)
        self.assertEqual(set(paquet), set(contrat['properties']))
        self.assertEqual(paquet['claims'], [claim['id']])
        self.assertIn(fait['id'], paquet['evidence_links'])
        exhibit = paquet['exhibits'][0]
        self.assertEqual(set(exhibit), set(contrat['$defs']['Exhibit']['properties']))
        self.assertEqual((exhibit['party_side'], exhibit['display_number']), ('cabinet', '1'))
        self.assertRegex(exhibit['source_ref']['sha256'], r'^[0-9a-f]{64}$')


class Pieces(Base):
    def test_IMP04A_deux_pieces_numero_1_restent_distinctes_et_bien_citees(self):
        self.parties()
        claim = self.pretention()
        cabinet = self.piece('1')
        adverse = self.piece('1', 'adverse', 'ZORVAL', titre='Facture fictive')
        self.assertNotEqual(cabinet['id'], adverse['id'])
        self.assertTrue(adverse['recue'])
        self.assertEqual(pieces.citation(cabinet), 'pièce n° 1')
        self.assertEqual(pieces.citation(adverse), 'pièce adverse n° 1 (ZORVAL)')
        lignes = [pieces._piece(self.desk, MID, x['id']) for x in (cabinet, adverse)]
        self.assertEqual(pieces.resoudre(lignes, {'numero': '1', 'adverse': False, 'partie': ''})[0]['id'], cabinet['id'])
        self.assertEqual(pieces.resoudre(lignes, {'numero': '1', 'adverse': True, 'partie': 'ZORVAL'})[0]['id'], adverse['id'])
        fait = self.element(claim, 'fait', 'Fait', exhibit_id=adverse['id'])
        vue = base.matrice(self.desk, MID)[0]['elements']['fait'][0]
        self.assertEqual((vue['id'], vue['citation']), (fait['id'], 'pièce adverse n° 1 (ZORVAL)'))
        groupes = {g['libelle']: [p['id'] for p in g['pieces']] for g in pieces.liste(self.desk, MID)['groupes']}
        self.assertEqual(groupes, {'Cabinet': [cabinet['id']], 'Partie adverse : ZORVAL': [adverse['id']]})

    def test_deux_adverses_meme_numero_exigent_la_partie(self):
        z, k = self.piece('1', 'adverse', 'ZORVAL'), self.piece('1', 'adverse', 'KERVILAC')
        lignes = [pieces._piece(self.desk, MID, x['id']) for x in (z, k)]
        self.assertEqual(pieces.resoudre(lignes, {'numero': '1', 'adverse': True, 'partie': ''})[1], 'partie_a_preciser')
        self.assertEqual(pieces.resoudre(lignes, {'numero': '1', 'adverse': True, 'partie': 'KERVILAC'})[0]['id'], k['id'])
        self.assertEqual(pieces.resoudre(lignes, {'numero': '1', 'adverse': False, 'partie': ''})[1], 'numero_d_une_autre_partie')

    def test_numero_1_bis_reste_du_texte_et_se_trie(self):
        un, bis, deux = self.piece('1'), self.piece('1 bis'), self.piece('2')
        self.assertEqual(bis['numero'], '1 bis')
        self.assertEqual(pieces.citation(bis), 'pièce n° 1 bis')
        self.assertEqual([p['numero'] for p in pieces.liste(self.desk, MID)['pieces']], ['1', '1 bis', '2'])
        self.assertEqual([c['numero'] for c in pieces.citations_du_texte('voir la pièce n° 1 bis et la pièce n° 2')], ['1 bis', '2'])
        self.assertEqual(pieces.cle_numero('n° 1bis'), pieces.cle_numero('1 bis'))
        self.assertNotEqual(pieces.cle_numero('1 bis'), pieces.cle_numero('1'))
        self.assertTrue(un and deux)
        with self.assertRaises(Stop):
            self.piece('1BIS', titre='Autre')          # même numéro pour la même partie : refus, jamais d'écrasement

    def test_doublon_par_contenu_et_provenance_jamais_par_titre(self):
        self.piece('1', titre='Même titre', data=b'contenu A', provenance='Client')
        self.piece('2', titre='Même titre', data=b'contenu B', provenance='Client')
        self.assertEqual(pieces.doublons(self.desk, MID), [])              # mêmes titres, contenus différents : aucune alerte
        self.piece('3', titre='Autre titre', data=b'contenu A', provenance='Client')
        groupes = pieces.doublons(self.desk, MID)
        self.assertEqual([g['nature'] for g in groupes], ['meme_provenance'])
        self.piece('1', 'adverse', 'ZORVAL', titre='Titre différent', data=b'contenu A')
        groupes = pieces.doublons(self.desk, MID)
        self.assertEqual([g['nature'] for g in groupes], ['provenances_differentes'])
        self.assertEqual(len(pieces.liste(self.desk, MID)['pieces']), 4)  # rien n'est supprimé

    def test_nouvelle_version_ne_passe_pas_pour_un_doublon(self):
        v1 = self.piece('1', titre='Contrat fictif', data=b'version 1')
        path = v1['path']
        self.nc.add(path, b'version 2')
        sha = hashlib.sha256(b'version 2').hexdigest()
        v2 = pieces.nouvelle_version(self.desk, MID, v1['id'], {'cle': self.cle(), 'sha256': sha, 'version': v1['revision']}, *AVOCAT)
        self.assertEqual((v2['numero'], v2['version'], v2['titre']), ('1', 2, 'Contrat fictif'))
        self.assertEqual(pieces._piece(self.desk, MID, v1['id'])['statut'], 'remplacee')
        self.assertEqual(pieces.doublons(self.desk, MID), [])
        with self.assertRaises(Stop) as meme:
            pieces.nouvelle_version(self.desk, MID, v2['id'], {'cle': self.cle(), 'sha256': sha, 'version': v2['revision']}, *AVOCAT)
        self.assertEqual(str(meme.exception), 'meme_contenu_pas_une_version')

    def test_renumerotation_proposee_pour_le_cabinet_et_appliquee_sur_decision(self):
        self.parties()
        a, b = self.piece('1'), self.piece('2')
        adverse = self.piece('1', 'adverse', 'ZORVAL')
        claim = self.pretention()
        self.element(claim, 'fait', 'Fait', exhibit_id=a['id'])
        acte = self.acte()
        self.desk.db.execute("UPDATE contentieux_sections5689 SET texte='Voir la pièce n° 1, la pièce n° 2 et la pièce adverse n° 1 (ZORVAL).',etat='modifiee' WHERE acte_id=? AND kind='expose'", (acte['id'],))
        self.desk.db.commit()
        avant_fichiers = list(self.nc.ecritures)
        plan = pieces.proposer_renumerotation(self.desk, MID, {'cle': self.cle(), 'nouveaux': {a['id']: '2', b['id']: '1'}}, *AVOCAT)
        self.assertEqual(plan['etat'], 'proposee')
        self.assertEqual(pieces._piece(self.desk, MID, a['id'])['numero'], '1')        # proposition : rien n'a bougé
        self.assertIn('la pièce n° 2, la pièce n° 1', plan['plan']['references'][0]['apres'])
        with self.assertRaises(Stop):
            pieces.appliquer_renumerotation(self.desk, MID, {'id': plan['id']}, *ASSISTANT)
        pieces.appliquer_renumerotation(self.desk, MID, {'id': plan['id']}, *AVOCAT)
        self.assertEqual(pieces._piece(self.desk, MID, a['id'])['numero'], '2')
        self.assertEqual(pieces._piece(self.desk, MID, a['id'])['numero_precedent'], '1')
        self.assertEqual(self.section(acte['id'], 'expose')['texte'], 'Voir la pièce n° 2, la pièce n° 1 et la pièce adverse n° 1 (ZORVAL).')
        self.assertEqual(pieces._piece(self.desk, MID, adverse['id'])['numero'], '1')
        self.assertEqual(self.nc.ecritures, avant_fichiers)                            # aucun fichier réécrit

    def test_piece_recue_jamais_renumerotee(self):
        adverse = self.piece('1', 'adverse', 'ZORVAL')
        recue = self.piece('9', recue=True)
        for piece in (adverse, recue):
            with self.assertRaises(Stop) as refus:
                pieces.proposer_renumerotation(self.desk, MID, {'cle': self.cle(), 'nouveaux': {piece['id']: '5'}}, *AVOCAT)
            self.assertEqual(str(refus.exception), 'piece_recue_non_renumerotable')
        self.assertEqual(pieces._piece(self.desk, MID, adverse['id'])['numero'], '1')

    def test_empreinte_du_registre_prevaut(self):
        path = self.fichier('/Dossiers/DEMO/Pieces/x.pdf', b'contenu')
        with self.assertRaises(Stop) as ecart:
            pieces.ajouter(self.desk, MID, {'cle': self.cle(), 'cote': 'cabinet', 'numero': '4', 'path': path, 'sha256': 'a' * 64}, *AVOCAT)
        self.assertEqual(str(ecart.exception), 'empreinte_differente_du_registre')

    def _document(self, name, nature='a_classer', usage='inconnu', party='', number=''):
        self.desk.db.execute('INSERT INTO d51_documents(matter,path,name,nature,usage_piece,piece_party,piece_number,updated) VALUES(?,?,?,?,?,?,?,?)', (MID, '/Dossiers/DEMO/' + name, name, nature, usage, party, number, 'x'))
        self.desk.db.commit()

    def test_migration_documents_categorie_proposee_classement_conserve(self):
        self._document('assignation.pdf', 'assignation', 'oui', 'cabinet', '3')
        self._document('note strategie.docx', 'note_interne', 'oui')
        self._document('facture.pdf', 'facture', 'oui', 'ZORVAL', '1 bis')
        self._document('mystere.pdf')
        self.assertEqual(pieces.migrer_categories(self.desk, MID)['creees'], 4)
        self.assertEqual(pieces.migrer_categories(self.desk, MID)['creees'], 0)            # répétable
        lignes = {c['nom']: c for c in pieces.categories(self.desk, MID)}
        self.assertEqual(lignes['assignation.pdf']['categorie'], 'acte_procedure')
        self.assertEqual(lignes['note strategie.docx']['categorie'], 'projet')
        self.assertEqual(lignes['facture.pdf']['categorie'], 'piece')
        self.assertEqual(lignes['mystere.pdf']['categorie'], 'a_reclasser')
        self.assertTrue(all(c['statut'] == 'propose' for c in lignes.values()))            # rien n'est certifié
        self.assertEqual(lignes['assignation.pdf']['classement_anterieur'], {'nature': 'assignation', 'nature_source': 'regle', 'usage_piece': 'oui', 'piece_party': 'cabinet', 'piece_number': '3'})
        ligne = lignes['assignation.pdf']
        with self.assertRaises(Stop):
            pieces.decider_categorie(self.desk, MID, {'path': ligne['path'], 'version': 1}, *ASSISTANT)
        fait = pieces.decider_categorie(self.desk, MID, {'path': ligne['path'], 'version': 1}, *AVOCAT)
        self.assertEqual((fait['statut'], fait['classement_anterieur']['usage_piece']), ('valide', 'oui'))
        self.assertEqual(self.desk.db.execute('SELECT usage_piece FROM d51_documents WHERE name=?', ('assignation.pdf',)).fetchone()[0], 'non')
        pieces.migrer_categories(self.desk, MID)
        self.assertEqual([c['statut'] for c in pieces.categories(self.desk, MID) if c['nom'] == 'assignation.pdf'], ['valide'])    # décision jamais refaite

    def test_migration_du_cabinet_repetable_sans_effet_externe(self):
        self._document('facture.pdf', 'facture', 'oui', 'ZORVAL', '1 bis')
        ecritures, lectures = list(self.nc.ecritures), self.nc.lectures
        self.assertEqual(pieces.migrer(self.desk), {'dossiers': 1, 'categories': 1, 'pieces': 1, 'echecs': 0})
        self.assertEqual(pieces.migrer(self.desk), {'dossiers': 1, 'categories': 0, 'pieces': 0, 'echecs': 0})
        self.assertEqual((self.nc.ecritures, self.nc.lectures), (ecritures, lectures))

    def test_import_conserve_numero_et_partie_sans_supposer_le_cabinet(self):
        self._document('a.pdf', 'facture', 'oui', 'ZORVAL', '1 bis')
        self._document('b.pdf', 'facture', 'oui', '', '7')
        self._document('c.pdf', 'facture', 'oui', 'cabinet', '1')
        pieces520.record(self.desk, MID, 'b1', {'numero_bordereau': '1', 'date_du_bordereau': '2026-01-05'}, [{'sha256': 'b' * 64, 'title': 'Devis fictif', 'path': '/Dossiers/DEMO/devis.pdf'}], 12)
        self.assertEqual(pieces.importer_registre(self.desk, MID)['reprises'], 4)
        self.assertEqual(pieces.importer_registre(self.desk, MID)['reprises'], 0)
        lignes = {r['numero']: r for r in self.desk.db.execute('SELECT * FROM contentieux_exhibits5689')}
        self.assertEqual((lignes['1 bis']['cote'], lignes['1 bis']['partie']), ('adverse', 'ZORVAL'))
        self.assertEqual(lignes['7']['cote'], 'inconnu')
        self.assertEqual(lignes['1']['cote'], 'cabinet')
        self.assertEqual((lignes['12']['cote'], lignes['12']['sha256']), ('cabinet', 'b' * 64))
        self.assertTrue(all(r['a_confirmer'] for r in lignes.values()))


class Actes(Base):
    def test_plan_depuis_demandes_validees_seulement(self):
        with self.assertRaises(Stop) as vide:
            self.acte()
        self.assertEqual(str(vide.exception), 'aucune_pretention_validee')
        self.parties()
        retenue, ecartee = self.pretention(), self.pretention(False, titre='Dommages proposés')
        self.element(retenue, 'fait', 'Fait validé')
        base.creer_element(self.desk, MID, {'cle': self.cle(), 'claim_id': retenue['id'], 'kind': 'argument', 'texte': 'Argument de l’IA'}, *AVOCAT, origine='ia')
        vue = self.acte()
        self.assertEqual([s['kind'] for s in vue['sections']], ['entete', 'expose', 'discussion', 'demandes', 'bordereau'])
        self.assertEqual(vue['sections'][2]['claim_id'], retenue['id'])
        self.assertEqual(vue['juridiction'], TRIBUNAL)
        self.assertTrue(any('Dommages proposés' in x for x in vue['reserves']))
        self.assertTrue(any('Argument de l’IA' in x for x in vue['reserves']))
        self.assertNotIn(ecartee['id'], vue['plan']['pretentions'])

    def test_redaction_section_par_section_le_modele_ne_recoit_que_le_valide(self):
        claim, piece, _ = self.matrice_complete()
        base.creer_element(self.desk, MID, {'cle': self.cle(), 'claim_id': claim['id'], 'kind': 'argument', 'texte': 'Argument non validé secret'}, *AVOCAT, origine='ia')
        modele = ModeleFaux(redaction)
        vue = self.rediger(modele)
        self.assertEqual(vue['etat'], 'reserves')               # l'élément non validé reste une réserve
        self.assertEqual(len(modele.appels), 2)                 # exposé et discussion ; entête, demandes et bordereau sans modèle
        self.assertNotIn('Argument non validé secret', json.dumps(modele.appels, ensure_ascii=False))
        demandes = self.section(vue['id'], 'demandes')
        self.assertEqual(demandes['auteur'], 'serveur')
        self.assertIn('1 500,00 €', demandes['texte'])
        self.assertIn('Soit un total de 1 500,00 €.', demandes['texte'])
        self.assertIn('pièce n° 1', self.section(vue['id'], 'discussion')['rendu'])
        self.assertIn('pièce n° 1', self.section(vue['id'], 'bordereau')['rendu'])

    def test_reprise_apres_interruption_ne_repaie_pas_le_fait(self):
        self.matrice_complete()
        a = self.acte()

        def coupure(donnees, numero):
            if numero == 2:
                raise Stop('modele_indisponible')
            return redaction(donnees, numero)
        modele = ModeleFaux(coupure)
        args = {'acte': a['id'], 'matter': MID, 'owner': AVOCAT[0], 'role': AVOCAT[1]}
        with self.assertRaises(Stop):
            actes.travail(self.desk, args, modele)
        apres = actes.vue(self.desk, a['id'])
        self.assertEqual({s['kind']: s['etat'] for s in apres['sections']}['expose'], 'redigee')
        self.assertEqual({s['kind']: s['etat'] for s in apres['sections']}['discussion'], 'a_rediger')
        texte = self.section(a['id'], 'expose')['texte']
        suite = ModeleFaux(redaction)
        actes.travail(self.desk, args, suite)
        self.assertEqual(len(suite.appels), 1)                 # seule la discussion manquait
        self.assertEqual(self.section(a['id'], 'expose')['texte'], texte)
        self.assertEqual(actes.vue(self.desk, a['id'])['etat'], 'a_relire')

    def test_attente_de_budget_n_est_pas_un_echec(self):
        self.matrice_complete()
        a = self.acte()

        def plafond(donnees, numero):
            raise AttenteBudget('plafond_atteint', 'mois')
        with self.assertRaises(AttenteBudget):
            actes.travail(self.desk, {'acte': a['id'], 'matter': MID, 'owner': AVOCAT[0], 'role': AVOCAT[1]}, ModeleFaux(plafond))
        vue = actes.vue(self.desk, a['id'])
        self.assertEqual(vue['etat'], 'attente_budget')
        self.assertEqual({s['kind']: s['etat'] for s in vue['sections']}['expose'], 'a_rediger')

    def test_sortie_refusee_section_a_reprendre_les_autres_avancent(self):
        self.matrice_complete()

        def reponse(donnees, numero):
            if 'Exposé' in donnees['section']:
                return {'texte': 'Voir la pièce n° 1 pour le détail complet.', 'reserves': []}        # numéro écrit par le modèle
            return redaction(donnees, numero)
        vue = self.rediger(ModeleFaux(reponse))
        etats = {s['kind']: s for s in vue['sections']}
        self.assertEqual((etats['expose']['etat'], etats['expose']['erreur']), ('bloquee', 'numero_de_piece_ecrit_par_le_modele'))
        self.assertEqual(etats['discussion']['etat'], 'redigee')
        self.assertEqual(vue['etat'], 'bloque')

    def test_piece_hors_liste_autorisee_refusee(self):
        self.matrice_complete()
        autre = self.piece('5')

        def reponse(donnees, numero):
            return {'texte': 'Il ressort de [[piece:%s]] que le solde est dû.' % autre['id'], 'reserves': []}
        vue = self.rediger(ModeleFaux(reponse))
        self.assertEqual({s['kind']: s['erreur'] for s in vue['sections']}['discussion'], 'piece_non_autorisee')

    def test_IMP04B_piece_citee_absente_bloque_et_apparait_dans_les_reserves(self):
        claim, piece, _ = self.matrice_complete()
        vue = self.rediger()
        self.assertEqual(vue['etat'], 'a_relire')
        pieces.retirer(self.desk, MID, piece['id'], {'version': pieces._piece(self.desk, MID, piece['id'])['revision'], 'motif': 'Pièce écartée'}, *AVOCAT)
        bilan = actes.controler(self.desk, vue['id'])
        self.assertEqual(bilan['etat'], 'bloque')
        self.assertEqual(self.controle(bilan, 'pieces')['etat'], 'bloquant')
        self.assertTrue(any('absente du registre' in x for x in bilan['reserves']))
        self.assertTrue(any('absente du registre' in x for x in actes.vue(self.desk, vue['id'])['reserves']))
        self.assertIn(actes.ABSENTE, self.section(vue['id'], 'discussion')['rendu'])    # jamais le numéro d'une autre pièce

    def test_AV07_actes_notes_et_pieces_separes_mauvais_numero_jamais_verifie(self):
        claim, piece, _ = self.matrice_complete()
        adverse = self.piece('8', 'adverse', 'ZORVAL')
        vue = self.rediger()
        section = self.section(vue['id'], 'expose')
        actes.modifier_section(self.desk, {'section': section['id'], 'version': section['revision'], 'texte': 'Voir la pièce n° 7, puis la pièce n° 8.'}, *AVOCAT)
        bilan = actes.controler(self.desk, vue['id'])
        details = ' '.join(self.controle(bilan, 'pieces')['bloquants'])
        self.assertIn('pièce n° 7 citée, absente du registre', details)
        self.assertIn('le n° 8 n’existe que pour une autre partie', details)     # le numéro adverse n'est pas pris pour une pièce du cabinet
        self.assertEqual(bilan['etat'], 'bloque')
        self.assertTrue(adverse)

    def test_acte_cite_les_deux_numeros_1_sans_confusion(self):
        claim, piece, _ = self.matrice_complete()
        adverse = self.piece('1', 'adverse', 'ZORVAL')
        self.element(claim, 'piece', 'Facture de ZORVAL', exhibit_id=adverse['id'])

        def reponse(donnees, numero):
            jetons = [x['jeton'] for x in donnees['pieces']]
            return {'texte': 'Selon %s, et contrairement à %s, le solde est dû.' % (jetons[0], jetons[-1]), 'reserves': []}
        vue = self.rediger(ModeleFaux(reponse))
        rendu = self.section(vue['id'], 'discussion')['rendu']
        self.assertIn('pièce n° 1,', rendu)
        self.assertIn('pièce adverse n° 1 (ZORVAL)', rendu)
        bordereau = self.section(vue['id'], 'bordereau')['rendu']
        self.assertIn('pièce n° 1', bordereau)
        self.assertNotIn('adverse', bordereau)                   # le bordereau du cabinet ne liste que ses pièces
        self.assertEqual(self.controle(actes.controler(self.desk, vue['id']), 'pieces')['etat'], 'ok')

    def test_montants_calcul_deterministe(self):
        self.parties()
        claim = self.pretention(montant='1500')
        piece = self.piece()
        un = modele5651._upsert_assertion(self.desk, MID, 'montant', 'Facture de 1000 euros', 'test', [{'path': '/Dossiers/DEMO/a.pdf'}], amount='1000', currency='EUR')
        deux = modele5651._upsert_assertion(self.desk, MID, 'montant', 'Facture de 400 euros', 'test', [{'path': '/Dossiers/DEMO/b.pdf'}], amount='400', currency='EUR')
        self.desk.db.commit()
        self.element(claim, 'fait', 'Première facture', assertion_ref=un, exhibit_id=piece['id'])
        self.element(claim, 'fait', 'Seconde facture', assertion_ref=deux, exhibit_id=piece['id'])
        vue = self.rediger()
        bilan = actes.controler(self.desk, vue['id'])
        self.assertIn('ne fait pas la somme', ' '.join(self.controle(bilan, 'montants')['bloquants']))
        self.assertEqual(actes.totaux([{'montant': '1500.10', 'devise': 'EUR'}, {'montant': '0.20', 'devise': 'EUR'}])['EUR'], __import__('decimal').Decimal('1500.30'))

    def test_montant_invente_par_le_modele_signale_jamais_retenu(self):
        self.matrice_complete()

        def reponse(donnees, numero):
            return {'texte': 'Le préjudice atteint 99 999,00 € selon nos calculs %s.' % donnees['pieces'][0]['jeton'], 'reserves': []}
        vue = self.rediger(ModeleFaux(reponse))
        bilan = vue['controles']
        self.assertEqual(self.controle(bilan, 'montants')['etat'], 'bloquant')
        self.assertTrue(any('Montant non issu du dossier' in x for x in vue['reserves']))

    def test_controles_parties_et_juridiction(self):
        self.matrice_complete()
        self.desk.db.execute("DELETE FROM d51_participations WHERE role='défenderesse'")
        self.desk.db.commit()
        vue = self.rediger()
        bilan = actes.controler(self.desk, vue['id'])
        self.assertIn('Qualité non renseignée pour Société ZORVAL.', self.controle(bilan, 'parties')['bloquants'])
        autre = self.acte(juridiction='Tribunal judiciaire de Kervilac')
        self.assertIn('diffère', ' '.join(self.controle(actes.controler(self.desk, autre['id']), 'juridiction')['bloquants']))

    def test_juridiction_validee_dans_la_memoire_fait_foi(self):
        self.matrice_complete()
        self.desk.db.execute("UPDATE d51_procedures SET juridiction='Cour d’appel de Valbrune'")
        ref = assertions5689.evenement_manuel(self.desk, MID, 'avocat', TRIBUNAL)
        element = assertions5689.ajouter(self.desk, MID, {'cle': self.cle(), 'kind': 'metadata', 'statement': TRIBUNAL, 'asserted_by': 'avocat', 'source_ref': ref, 'cle_valeur': 'juridiction', 'valeur': TRIBUNAL}, *AVOCAT)
        self.desk.db.execute("UPDATE case_assertions5689 SET status='validated',cle_valeur='juridiction',valeur=? WHERE id=?", (TRIBUNAL, element['assertion_id']))
        self.desk.db.commit()
        acte = self.acte(juridiction=TRIBUNAL)
        self.assertEqual(self.controle(actes.controler(self.desk, acte['id']), 'juridiction')['bloquants'], [])

    def test_references_croisees_entre_exposé_discussion_demandes_et_bordereau(self):
        claim, piece, _ = self.matrice_complete()
        vue = self.rediger()
        autre = self.piece('6')
        demandes = self.section(vue['id'], 'demandes')
        actes.modifier_section(self.desk, {'section': demandes['id'], 'version': demandes['revision'], 'texte': 'Il est demandé de statuer ce que de droit.'}, *AVOCAT)
        bordereau = self.section(vue['id'], 'bordereau')
        actes.modifier_section(self.desk, {'section': bordereau['id'], 'version': bordereau['revision'], 'texte': '[[piece:%s]] : Autre' % autre['id']}, *AVOCAT)
        ctrl = self.controle(actes.controler(self.desk, vue['id']), 'references')
        joint = ' '.join(ctrl['bloquants'] + ctrl['reserves'])
        self.assertIn('Prétention absente des demandes finales', joint)
        self.assertIn('Pièce citée absente du bordereau', joint)
        self.assertIn('Pièce au bordereau jamais citée', joint)

    def test_pretention_retiree_apres_redaction_se_voit(self):
        claim, _, _ = self.matrice_complete()
        vue = self.rediger()
        base.decider(self.desk, MID, {'objet': 'pretention', 'id': claim['id'], 'action': 'rejeter', 'version': base._claim(self.desk, MID, claim['id'])['revision']}, *AVOCAT)
        ctrl = self.controle(actes.controler(self.desk, vue['id']), 'references')
        self.assertIn('n’est plus validée', ' '.join(ctrl['bloquants']))

    def test_etat_des_sources_regle_non_verifiee_et_fichier_change(self):
        claim, piece, _ = self.matrice_complete()
        self.element(claim, 'regle', 'Article à relire')
        vue = self.rediger()
        self.fichier(piece['path'], b'contenu remplace')
        bilan = actes.controler(self.desk, vue['id'])
        ctrl = self.controle(bilan, 'sources')
        self.assertIn('Règle non vérifiée : Article à relire.', ctrl['reserves'])
        self.assertIn('le fichier a changé', ' '.join(ctrl['bloquants']))

    def test_correction_humaine_prime_sur_la_reprise(self):
        self.matrice_complete()
        vue = self.rediger()
        section = self.section(vue['id'], 'expose')
        actes.modifier_section(self.desk, {'section': section['id'], 'version': section['revision'], 'texte': 'Texte corrigé par l’avocat.'}, *AVOCAT)
        with self.assertRaises(Stop) as vieux:
            actes.modifier_section(self.desk, {'section': section['id'], 'version': section['revision'], 'texte': 'Écrasement'}, *AVOCAT)
        self.assertEqual(str(vieux.exception), 'version_en_conflit')
        modele = ModeleFaux(redaction)
        actes.travail(self.desk, {'acte': vue['id'], 'matter': MID, 'owner': AVOCAT[0], 'role': AVOCAT[1]}, modele)
        self.assertEqual(modele.appels, [])
        self.assertEqual(self.section(vue['id'], 'expose')['texte'], 'Texte corrigé par l’avocat.')

    def test_section_signalee_a_reprendre_si_la_matrice_change(self):
        claim, _, _ = self.matrice_complete()
        vue = self.rediger()
        self.assertFalse(self.section(vue['id'], 'discussion')['a_reprendre'])
        self.element(claim, 'argument', 'Argument nouveau')
        self.assertTrue(self.section(vue['id'], 'discussion')['a_reprendre'])

    def test_annulation_terminale(self):
        self.matrice_complete()
        a = self.acte()
        with self.assertRaises(Stop):
            actes.annuler(self.desk, a['id'], *ASSISTANT)
        actes.annuler(self.desk, a['id'], *AVOCAT)
        modele = ModeleFaux(redaction)
        self.assertEqual(actes.travail(self.desk, {'acte': a['id'], 'matter': MID, 'owner': AVOCAT[0], 'role': AVOCAT[1]}, modele)['etat'], 'annule')
        self.assertEqual(modele.appels, [])
        with self.assertRaises(Stop):
            actes.lancer(self.desk, a['id'], *AVOCAT)

    def test_annulation_pendant_la_redaction_arrete_avant_la_section_suivante(self):
        self.matrice_complete()
        a = self.acte()

        def annule(donnees, numero):
            actes.annuler(self.desk, a['id'], *AVOCAT)
            return redaction(donnees, numero)
        modele = ModeleFaux(annule)
        out = actes.travail(self.desk, {'acte': a['id'], 'matter': MID, 'owner': AVOCAT[0], 'role': AVOCAT[1]}, modele)
        self.assertEqual(out['etat'], 'annule')
        self.assertEqual(len(modele.appels), 1)
        self.assertNotEqual(self.section(a['id'], 'expose')['etat'], 'redigee')

    def test_dossier_reserve_relu_dans_le_travail(self):
        self.matrice_complete()
        a = self.acte()
        with patch('agent.membres5650.hidden_for', return_value=frozenset({MID})):
            with self.assertRaises(Stop) as refus:
                actes.travail(self.desk, {'acte': a['id'], 'matter': MID, 'owner': ASSISTANT[0], 'role': ASSISTANT[1]}, ModeleFaux(redaction))
        self.assertEqual(str(refus.exception), 'dossier_reserve')

    def test_publication_au_registre_commun_reserves_hors_de_l_acte(self):
        claim, piece, _ = self.matrice_complete()
        self.element(claim, 'regle', 'Article à relire')
        vue = self.rediger()
        self.assertEqual(vue['etat'], 'reserves')
        publie = actes.publier(self.desk, vue['id'], *AVOCAT)
        self.assertTrue(publie['path'].endswith('.docx'))
        self.assertEqual(publie['sha256'], hashlib.sha256(self.nc.files[publie['path']]).hexdigest())
        ligne = self.desk.db.execute('SELECT * FROM prepared_items5689 WHERE path=?', (publie['path'],)).fetchone()
        self.assertEqual(ligne['matter_id'], MID)
        with zipfile.ZipFile(io.BytesIO(self.nc.files[publie['path']])) as z:
            xml = z.read('word/document.xml').decode()
        self.assertIn('Par ces motifs', xml)
        self.assertNotIn('Règle non vérifiée', xml)
        self.assertEqual(actes.publier(self.desk, vue['id'], *AVOCAT)['path'], publie['path'])     # même texte : aucun nouveau fichier


class Procedure(Base):
    def test_start_targets_compose_consomment_la_matrice(self):
        claim, _, _ = self.matrice_complete()
        mission = procedure5632.start(self.desk, AVOCAT[0], MID, [], 'Préparer les conclusions', acte={'type_acte': 'conclusions', 'titre': 'Conclusions DURAND'})
        self.assertEqual(mission['events'], [])
        self.assertEqual(mission['acte']['etat'], 'plan')
        self.assertEqual(procedure5632.start(self.desk, AVOCAT[0], MID, [], 'Préparer les conclusions', acte={'type_acte': 'conclusions', 'titre': 'Conclusions DURAND'})['acte']['id'], mission['acte']['id'])     # reprise, sans doublon
        cibles = procedure5632.targets(self.desk, AVOCAT[0], MID)
        self.assertEqual([(c['provider'], c['etat']) for c in cibles if c['provider'] == 'matrice'], [('matrice', 'valide')])
        self.assertNotIn('matrice', [c['provider'] for c in procedure5632.targets(self.desk, AVOCAT[0])])     # sans dossier : les agendas seuls, comme avant
        actes.travail(self.desk, {'acte': mission['acte']['id'], 'matter': MID, 'owner': AVOCAT[0], 'role': AVOCAT[1]}, ModeleFaux(redaction))
        titre, texte = procedure5632.compose(self.desk, AVOCAT[0], mission['id'], 'acte', '')
        self.assertEqual(titre, 'Conclusions DURAND')
        self.assertIn('Exposé du litige', texte)
        self.assertIn('Soit un total de', texte)
        self.assertIn('pièce n° 1', texte)

    def test_acte_jamais_envoye_par_les_brouillons_et_rien_de_la_matrice_dans_une_lettre(self):
        self.matrice_complete()
        mission = procedure5632.start(self.desk, AVOCAT[0], MID, [], 'Actes', acte={'type_acte': 'conclusions'})
        with self.assertRaises(Stop) as refus:
            procedure5632.drafts(self.desk, AVOCAT[0], mission['id'], [{'role': 'acte', 'email': 'confrere@example.test'}])
        self.assertEqual(str(refus.exception), 'role_destinataire_invalide')
        with self.assertRaises(Stop) as refus:
            procedure5632.decide(self.desk, AVOCAT[0], mission['id'], 'x', 'add', ['pretention:' + base.matrice(self.desk, MID)[0]['id']])
        self.assertEqual(str(refus.exception), 'agenda_cible_requis')     # une prétention n'est pas un agenda

    def test_sans_acte_le_parcours_reste_celui_d_avant(self):
        with self.assertRaises(Stop) as refus:
            procedure5632.start(self.desk, AVOCAT[0], MID, [])
        self.assertEqual(str(refus.exception), 'documents_a_lire_requis')
        pages = [{'label': '1', 'text': 'Audience fictive', 'extraction': 'texte'}]
        vue = procedure5632.start(self.desk, AVOCAT[0], MID, ['/Dossiers/DEMO/a.pdf'], '', reader=lambda d, p: (pages, 'a' * 64, 'e'))
        self.assertIsNone(vue['acte'])
        with self.assertRaises(Stop) as refus:
            procedure5632.compose(self.desk, AVOCAT[0], vue['id'], 'acte', '')
        self.assertEqual(str(refus.exception), 'acte_absent')


class Audience(Base):
    def test_dossier_fige_a_sa_preparation_indicateur_de_modification(self):
        claim, piece, _ = self.matrice_complete()
        self.element(claim, 'objection', 'La livraison est contestée', affirme_par='Société ZORVAL')
        v1 = paquets.preparer_audience(self.desk, MID, {'cle': self.cle()}, *AVOCAT)
        self.assertFalse(v1['modifie_depuis'])
        for rubrique in ('chronologie', 'demandes', 'points_discutes', 'preuves', 'objections', 'decisions_attendues', 'pieces_a_ouvrir', 'notes_internes'):
            self.assertIn(rubrique, v1['contenu'])
        self.assertEqual(v1['contenu']['pieces_a_ouvrir'][0]['citation'], 'pièce n° 1')
        figee = json.dumps(v1['contenu'], sort_keys=True)
        self.element(claim, 'objection', 'Objection nouvelle')
        apres = paquets.vue_audience(self.desk, v1['id'])
        self.assertTrue(apres['modifie_depuis'])
        self.assertEqual(apres['parties_modifiees'], ['Objections'])
        self.assertEqual(json.dumps(apres['contenu'], sort_keys=True), figee)      # le contenu figé ne bouge pas
        v2 = paquets.preparer_audience(self.desk, MID, {'cle': self.cle()}, *AVOCAT)
        self.assertEqual(v2['version'], 2)
        self.assertEqual(len(v2['contenu']['objections']), 2)
        self.assertEqual(json.dumps(paquets.vue_audience(self.desk, v1['id'])['contenu'], sort_keys=True), figee)

    def test_notes_internes_identifiees_et_exclues_de_la_version_communicable(self):
        self.matrice_complete()
        memoire5651.save_decision(self.desk, MID, 'avocat', {'kind': 'consigne', 'subject': 'Stratégie', 'texte': 'Ne pas évoquer le second contrat'})
        v = paquets.preparer_audience(self.desk, MID, {'cle': self.cle()}, *AVOCAT)
        note = v['contenu']['notes_internes'][0]
        self.assertTrue(note['interne'])
        sortie = json.dumps(paquets.audience_communicable(v['contenu']), ensure_ascii=False)
        self.assertNotIn('Ne pas évoquer', sortie)

    def test_audience_d_un_autre_dossier_refusee(self):
        self.matrice_complete()
        with self.assertRaises(Stop) as refus:
            paquets.preparer_audience(self.desk, MID, {'cle': self.cle(), 'audience_id': 'inconnue'}, *AVOCAT)
        self.assertEqual(str(refus.exception), 'audience_absente')


class Delais(Base):
    def proposer(self, **autres):
        return delais.proposer(self.desk, MID, {'cle': self.cle(), 'evenement': 'signification_jugement', 'date_evenement': '2026-03-12', 'inputs': dict(COMPLET), **autres}, *AVOCAT)

    def valider_regle(self, rule_id='appel_contentieux'):
        return delais.valider_regle(self.desk, {'rule_id': rule_id, 'empreinte': delais.empreinte_regle(delais.regle(rule_id))}, *AVOCAT)

    def test_IMP04C_sans_date_de_depart_aucune_echeance_et_parametres_affiches(self):
        x = self.proposer(date_evenement='')
        self.assertEqual((x['statut'], x['date_calculee'], x['certaine']), ('a_verifier', '', False))
        self.assertEqual([m['champ'] for m in x['manquants']], ['date_evenement'])
        with self.assertRaises(Stop) as refus:
            delais.decider(self.desk, x['id'], {'action': 'valider', 'version': x['revision']}, *AVOCAT)
        self.assertEqual(str(refus.exception), 'date_non_certaine_a_completer')
        self.assertFalse(self.desk.db.execute("SELECT 1 FROM sqlite_master WHERE name='deadlines450'").fetchone() and self.desk.db.execute('SELECT 1 FROM deadlines450').fetchone())

    def test_IMP04C_regle_non_couverte_a_verifier(self):
        x = delais.proposer(self.desk, MID, {'cle': self.cle(), 'evenement': 'avis_fixation_bref_delai', 'date_evenement': '2026-03-12'}, *AVOCAT)
        self.assertEqual((x['statut'], x['date_calculee'], x['rule_id']), ('a_verifier', '', ''))
        self.assertIn('Aucune règle', x['calcul']['motifs'][0])

    def test_AV08_donnee_determinante_manquante_decision_visible_sans_date_fabriquee(self):
        donnees = dict(COMPLET)
        donnees.pop('lieu_partie')
        x = self.proposer(inputs=donnees)
        self.assertEqual((x['statut'], x['date_calculee']), ('a_verifier', ''))
        self.assertEqual([m['champ'] for m in x['manquants']], ['lieu_partie'])
        attente = delais.a_decider(self.desk, MID)
        self.assertEqual(attente[0]['id'], x['id'])
        self.assertIn('Où demeure la partie', attente[0]['questions'][0])
        corrige = delais.corriger(self.desk, x['id'], {'cle': self.cle(), 'version': x['revision'], 'inputs': {'lieu_partie': 'metropole'}}, *AVOCAT)
        self.assertEqual(corrige['statut'], 'propose')
        self.assertEqual(corrige['date_calculee'], '2026-04-13')       # 12 avril 2026 est un dimanche : report au lundi
        self.assertTrue(any('Dernier' in s or 'report' in s for s in corrige['calcul']['etapes']))
        self.assertFalse(corrige['certaine'])

    def test_hors_perimetre_jamais_de_date(self):
        for champ, valeur in (('regime_commun', 'non'), ('siege_juridiction', 'outre_mer'), ('matiere', 'gracieuse'), ('mentions_680', 'non'), ('aide_juridictionnelle', 'oui'), ('recours_ouvert', 'non')):
            res = delais.calculer('appel_contentieux', 'signification_jugement', '2026-03-12', {**COMPLET, champ: valeur})
            self.assertEqual((res['etat'], res['date']), ('a_verifier', ''), champ)
            self.assertTrue(res['motifs'], champ)

    def test_moteur_etapes_quantieme_report_distance(self):
        res = delais.calculer('appel_contentieux', 'signification_jugement', '2026-01-31', COMPLET)
        self.assertEqual(res['date'], '2026-03-02')                     # 28 février 2026, samedi : lundi 2 mars
        self.assertTrue(any('dernier jour du mois' in s for s in res['etapes']))
        self.assertTrue(any('jour non ouvrable' in s for s in res['etapes']))
        self.assertEqual(res['rule_version'], 1)
        loin = delais.calculer('appel_contentieux', 'signification_jugement', '2026-03-12', {**COMPLET, 'lieu_partie': 'etranger'})
        self.assertEqual(loin['date'], '2026-06-12')                    # un mois + deux mois (CPC 643)
        self.assertEqual(delais.calculer('appel_refere', 'signification_ordonnance_refere', '2026-03-12', {k: v for k, v in COMPLET.items() if k != 'matiere'})['date'], '2026-03-27')
        self.assertEqual(delais.calculer('pourvoi_civil', 'signification_decision_cassation', '2026-03-12', COMPLET)['date'], '2026-05-12')

    def test_alsace_moselle_change_les_jours_feries(self):
        # Vendredi saint 3 avril 2026 : férié en Alsace-Moselle seulement. Signification un 3 mars.
        ailleurs = delais.calculer('appel_contentieux', 'signification_jugement', '2026-03-03', COMPLET)
        alsace = delais.calculer('appel_contentieux', 'signification_jugement', '2026-03-03', {**COMPLET, 'siege_juridiction': 'alsace_moselle'})
        self.assertEqual(ailleurs['date'], '2026-04-03')
        self.assertEqual(alsace['date'], '2026-04-07')                  # puis week-end et lundi de Pâques

    def test_regles_d_appel_regime_et_circuit(self):
        commun = {'regime_commun': 'oui', 'siege_juridiction': 'metropole', 'lieu_partie_915_4': 'metropole', 'circuit': 'mise_en_etat', 'interruption_915_3': 'non', 'aide_juridictionnelle': 'non'}
        self.assertEqual(delais.calculer('conclusions_appelant_908', 'declaration_appel', '2025-01-15', commun)['date'], '2025-04-15')
        ancien = delais.calculer('conclusions_appelant_908', 'declaration_appel', '2024-05-02', commun)
        self.assertEqual((ancien['etat'], ancien['date']), ('a_verifier', ''))
        self.assertEqual(delais.calculer('conclusions_appelant_908', 'declaration_appel', '2025-01-15', {**commun, 'circuit': 'bref_delai'})['etat'], 'a_verifier')
        self.assertEqual(delais.calculer('conclusions_appelant_908', 'declaration_appel', '2025-01-15', {**commun, 'lieu_partie_915_4': 'etranger'})['etat'], 'a_verifier')     # art. 915-4 : non calculé
        intime = delais.calculer('conclusions_intime_909', 'conclusions_appelant', '2025-02-10', commun)
        self.assertEqual([m['champ'] for m in intime['manquants']], ['date_declaration_appel'])
        self.assertEqual(delais.calculer('conclusions_intime_909', 'conclusions_appelant', '2025-02-10', {**commun, 'date_declaration_appel': '2025-01-15'})['date'], '2025-05-12')
        self.assertEqual(delais.calculer('signification_da_902', 'avis_greffe_circuit', '2025-02-10', {**commun, 'date_declaration_appel': '2025-01-15'})['date'], '2025-03-10')

    def test_ressource_chaque_regle_a_sa_source_son_perimetre_et_aucune_n_est_livree_validee(self):
        data = delais.ressource()
        self.assertEqual(data['statut'], 'a_valider_avocat')
        self.assertTrue(data['regles'])
        for r in data['regles']:
            self.assertEqual(r['statut'], 'a_valider_avocat', r['id'])
            self.assertTrue(r['perimetre'], r['id'])
            self.assertTrue(r['sources'], r['id'])
            for s in r['sources']:
                self.assertTrue(s['texte'] and s['reference'], r['id'])
                self.assertRegex(s['url'], r'^https://www\.legifrance\.gouv\.fr/codes/article_lc/LEGIARTI\d{12}$')
            for c in r['conditions']:
                self.assertIn(c, data['conditions'])
            deadlines450.rule(r['base'])
            self.assertIn(r['evenement'], data['evenements'])
            self.assertEqual(deadlines450.rule(r['base'])['start_event'], r['evenement'])
        self.assertFalse(self.desk.db.execute("SELECT 1 FROM sqlite_master WHERE name='deadline_rule_validations5689'").fetchone() and self.desk.db.execute('SELECT 1 FROM deadline_rule_validations5689').fetchone())
        self.assertTrue(all(not r['validee'] for r in delais.regles_publiques(self.desk)['regles']))

    def test_regle_a_valider_par_un_avocat_avant_toute_date_certaine(self):
        x = self.proposer()
        self.assertEqual((x['statut'], x['certaine']), ('propose', False))
        with self.assertRaises(Stop) as refus:
            delais.decider(self.desk, x['id'], {'action': 'valider', 'version': x['revision']}, *AVOCAT)
        self.assertEqual(str(refus.exception), 'regle_a_valider')
        with self.assertRaises(Stop) as refus:
            delais.valider_regle(self.desk, {'rule_id': 'appel_contentieux', 'empreinte': delais.empreinte_regle(delais.regle('appel_contentieux'))}, *ASSISTANT)
        self.assertEqual(str(refus.exception), 'role_insuffisant')
        self.valider_regle()
        with self.assertRaises(Stop):
            delais.decider(self.desk, x['id'], {'action': 'valider', 'version': x['revision']}, *ASSISTANT)
        fait = delais.decider(self.desk, x['id'], {'action': 'valider', 'version': x['revision']}, *AVOCAT)
        self.assertEqual((fait['statut'], fait['certaine'], fait['valide_par']), ('valide', True, AVOCAT[0]))
        self.assertEqual(fait['suivi']['due'], x['date_calculee'])

    def test_regle_modifiee_perd_sa_validation(self):
        self.valider_regle()
        x = self.proposer()
        r = delais.regle('appel_contentieux')
        with patch.dict(r, {'perimetre': r['perimetre'] + ' Modifié.'}):
            self.assertIsNone(delais.validation(self.desk, r))
            with self.assertRaises(Stop) as refus:
                delais.decider(self.desk, x['id'], {'action': 'valider', 'version': x['revision']}, *AVOCAT)
            self.assertEqual(str(refus.exception), 'regle_modifiee_recalculer')

    def test_llm_propose_l_evenement_seulement(self):
        texte = 'Le jugement a été signifié le 12 mars 2026 à la société ZORVAL. La partie adverse réclame une somme.'
        self.nc.add('/Dossiers/DEMO/signification.txt', texte.encode())

        def reponse(donnees, numero):
            return {'evenements': [{'evenement': 'signification_jugement', 'passage': 'Le jugement a été signifié le 12 mars 2026', 'date': '2026-03-12', 'rule_id': 'appel_refere', 'delai': '2 jours', 'date_echeance': '2030-01-01'},
                                   {'evenement': 'signification_jugement', 'passage': 'La partie adverse réclame une somme', 'date': '2026-05-01'},
                                   {'evenement': 'signification_jugement', 'passage': 'Un passage inventé par le modèle', 'date': '2026-01-01'},
                                   {'evenement': 'evenement_inconnu', 'passage': 'Le jugement a été signifié', 'date': '2026-03-12'}]}
        with patch('agent.documents.extract', return_value=texte):
            bilan = delais.travail(self.desk, {'matter': MID, 'path': '/Dossiers/DEMO/signification.txt', 'owner': AVOCAT[0], 'role': AVOCAT[1]}, ModeleFaux(reponse), self.nc)
        self.assertEqual((bilan['crees'], bilan['ecartes']), (2, 2))
        lignes = {x['citation']: x for x in delais.liste(self.desk, MID)}
        cite = lignes['Le jugement a été signifié le 12 mars 2026']
        self.assertEqual((cite['origine'], cite['date_evenement'], cite['rule_id'], cite['inputs']), ('ia', '2026-03-12', 'appel_contentieux', {}))
        self.assertEqual(cite['statut'], 'a_verifier')                  # le modèle ne fournit aucun paramètre : les données restent à donner
        self.assertTrue(cite['manquants'])
        sans = lignes['La partie adverse réclame une somme']
        self.assertEqual((sans['date_evenement'], sans['statut'], sans['date_calculee']), ('', 'a_verifier', ''))    # date absente du passage : non retenue

    def test_regle_et_parametres_venus_d_un_modele_ignores(self):
        x = delais.proposer(self.desk, MID, {'cle': self.cle(), 'evenement': 'signification_jugement', 'date_evenement': '2026-03-12', 'rule_id': 'appel_refere', 'inputs': dict(COMPLET)}, *AVOCAT, origine='ia')
        self.assertEqual((x['rule_id'], x['inputs'], x['statut'], x['date_calculee']), ('appel_contentieux', {}, 'a_verifier', ''))

    def test_correction_recalcule_conserve_la_decision_et_alerte(self):
        self.valider_regle()
        x = self.proposer()
        fait = delais.decider(self.desk, x['id'], {'action': 'valider', 'version': x['revision']}, *AVOCAT)
        suivi = fait['deadline_id']
        corrige = delais.corriger(self.desk, x['id'], {'cle': self.cle(), 'version': fait['revision'], 'date_evenement': '2026-03-20'}, *AVOCAT)
        self.assertEqual((corrige['statut'], corrige['date_calculee']), ('propose', '2026-04-20'))
        self.assertEqual(corrige['decision_precedente']['valide_par'], AVOCAT[0])
        self.assertEqual(corrige['decision_precedente']['date'], '2026-04-13')
        self.assertEqual(delais._ligne(self.desk, x['id'])['statut'], 'remplace')
        self.assertEqual(echeances450._row(self.desk, suivi)['due'], '2026-04-13')     # le suivi n'est pas réécrit en silence
        alerte = delais.alertes(self.desk, MID)
        self.assertIn('2026-04-13', alerte[0]['message'])
        self.assertIn('2026-04-20', alerte[0]['message'])
        delais.decider(self.desk, corrige['id'], {'action': 'valider', 'version': corrige['revision']}, *AVOCAT)
        self.assertEqual(echeances450._row(self.desk, suivi)['due'], '2026-04-20')     # même échéance : pas de doublon
        self.assertEqual(self.desk.db.execute('SELECT COUNT(*) FROM deadlines450').fetchone()[0], 1)
        self.assertEqual(delais.alertes(self.desk, MID), [])

    def test_suivi_et_agendas_distincts_et_coherents(self):
        self.valider_regle()
        x = self.proposer()

        class Agenda:
            ecritures = []

            def put_event(self, url, uid, *a, **k):
                self.ecritures.append(uid)
                return uid

            def events(self, *a, **k):
                return []
        dav = Agenda()
        self.desk.setting('automation:deadline_calendar450', True)
        self.assertEqual(dav.ecritures, [])                                             # candidat seul : aucun agenda touché
        with patch('agent.autonomy480.allows', return_value=True):
            fait = delais.decider(self.desk, x['id'], {'action': 'valider', 'version': x['revision']}, *AVOCAT, dav)
            self.assertEqual(dav.ecritures, [])                                         # validation : suivi seulement
            coherence = delais.coherence(self.desk, MID)
            self.assertFalse(coherence['agenda_verifie'])
            self.assertIn('non relu', coherence['limite'])
            self.assertEqual(coherence['lignes'][0]['date_suivi'], '2026-04-13')
            self.assertEqual(coherence['lignes'][0]['agenda_en_ligne'], 'non inscrit')
            y = self.proposer(date_evenement='2027-05-04')
            delais.decider(self.desk, y['id'], {'action': 'valider', 'version': y['revision'], 'agenda': True}, *AVOCAT, dav)
            self.assertEqual(len(dav.ecritures), 1)                                     # inscription demandée expressément
            lue = delais.coherence(self.desk, MID, dav)
            self.assertTrue(lue['agenda_verifie'])
            self.assertTrue(any('n’est pas dans l’agenda' in e for l in lue['lignes'] for e in l['ecarts']))     # agenda relu : écart réel signalé
        self.assertTrue(fait)

    def test_correction_apres_inscription_agenda_a_reprendre(self):
        self.valider_regle()
        x = self.proposer()
        fait = delais.decider(self.desk, x['id'], {'action': 'valider', 'version': x['revision']}, *AVOCAT)
        self.desk.db.execute("UPDATE deadlines450 SET calendar_uid='u1',calendar_state='cree' WHERE id=?", (fait['deadline_id'],))
        self.desk.db.commit()
        c = delais.corriger(self.desk, x['id'], {'cle': self.cle(), 'version': fait['revision'], 'date_evenement': '2026-03-20'}, *AVOCAT)
        delais.decider(self.desk, c['id'], {'action': 'valider', 'version': c['revision']}, *AVOCAT)
        ligne = delais.coherence(self.desk, MID)['lignes'][0]
        self.assertEqual(ligne['agenda_en_ligne'], 'a_resynchroniser')
        self.assertTrue(any('ancienne date' in e for e in ligne['ecarts']))

    def test_detection_par_motifs_devient_un_candidat_sans_regle_choisie_pour_lui(self):
        texte = 'Le jugement du 2 mars 2026 a été signifié le 12 mars 2026 à ZORVAL.'
        self.nc.add('/Dossiers/DEMO/signification jugement.txt', texte.encode())
        self.nc.stat('/Dossiers/DEMO/signification jugement.txt')
        with patch.object(echeances450, '_calendar_urls', return_value=([], 'Europe/Paris')):
            echeances450.analyze(self.desk, {'matter': MID, 'path': '/Dossiers/DEMO/signification jugement.txt', 'etag': 'e'}, dav=self.nc, extractor=lambda d, n: texte, today=date(2026, 3, 20))
        ligne = delais.liste(self.desk, MID)[0]
        self.assertEqual((ligne['origine'], ligne['evenement'], ligne['date_evenement'], ligne['statut']), ('regles', 'signification_jugement', '2026-03-12', 'a_verifier'))
        self.assertTrue(ligne['manquants'])

    def test_rejet_motive_et_decision_gardee(self):
        x = self.proposer()
        with self.assertRaises(Stop):
            delais.decider(self.desk, x['id'], {'action': 'rejeter', 'version': x['revision']}, *AVOCAT)
        fait = delais.decider(self.desk, x['id'], {'action': 'rejeter', 'version': x['revision'], 'motif': 'Mauvais acte'}, *AVOCAT)
        self.assertEqual((fait['statut'], fait['motif']), ('rejete', 'Mauvais acte'))


class Paquets(Base):
    def paquet(self, **autres):
        self.matrice_complete()
        self.vue_acte = self.rediger()
        actes.publier(self.desk, self.vue_acte['id'], *AVOCAT)
        return paquets.creer(self.desk, MID, {'cle': self.cle(), 'acte_id': self.vue_acte['id'], 'canal': 'communication_confrere', 'destinataires': [{'nom': 'Maître KERVILAC', 'qualite': 'Conseil adverse'}], **autres}, *AVOCAT, self.nc)

    def autoriser(self, p):
        return paquets.autoriser(self.desk, p['id'], {'manifeste_sha': p['manifeste_sha'], 'action': 'envoi'}, *AVOCAT, self.nc)

    def test_manifeste_complet_ouvrable_et_autorisation_sur_l_empreinte(self):
        p = self.paquet()
        self.assertEqual({f['role'] for f in p['fichiers']}, {'acte', 'piece'})
        self.assertTrue(all(re.fullmatch(r'[0-9a-f]{64}', f['sha256']) and f['version_id'] and f['path'] for f in p['fichiers']))
        self.assertEqual([b['numero'] for b in p['bordereau']], ['1'])
        self.assertEqual(p['manifeste']['destinataires'][0]['nom'], 'Maître KERVILAC')
        self.assertEqual(p['manifeste']['depot'], {'effectue': False, 'preuve': None})
        self.assertEqual(p['manifeste_sha'], base.empreinte(p['manifeste']))
        with self.assertRaises(Stop) as autre:
            paquets.autoriser(self.desk, p['id'], {'manifeste_sha': 'a' * 64, 'action': 'envoi'}, *AVOCAT, self.nc)
        self.assertEqual(str(autre.exception), 'version_en_conflit')
        with self.assertRaises(Stop) as role:
            paquets.autoriser(self.desk, p['id'], {'manifeste_sha': p['manifeste_sha'], 'action': 'envoi'}, *ASSISTANT, self.nc)
        self.assertEqual(str(role.exception), 'role_insuffisant')
        ok = self.autoriser(p)
        self.assertEqual((ok['etat'], ok['autorisation']['manifeste_sha'], ok['autorisation']['acteur']), ('autorise', p['manifeste_sha'], AVOCAT[0]))
        self.assertTrue(paquets.exiger_autorisation(self.desk, p['id'], self.nc))

    def test_IMP04D_fichier_modifie_invalide_l_autorisation(self):
        p = self.paquet()
        self.autoriser(p)
        piece = next(f for f in p['fichiers'] if f['role'] == 'piece')
        self.nc.add(piece['path'], b'contenu change apres accord')
        with self.assertRaises(Stop) as refus:
            paquets.exiger_autorisation(self.desk, p['id'], self.nc)
        self.assertEqual(str(refus.exception), 'autorisation_absente_ou_perimee')
        vue = paquets.vue(self.desk, p['id'])
        self.assertEqual((vue['etat'], vue['autorisation']['etat']), ('invalide', 'invalidee'))
        self.assertIn('Fichier modifié', vue['autorisation']['cause'])
        with self.assertRaises(Stop):                                              # l'ancien accord ne revient pas si l'état affiché est forcé
            self.desk.db.execute("UPDATE contentieux_paquets5689 SET etat='autorise' WHERE id=?", (p['id'],))
            self.desk.db.commit()
            paquets.archive(self.desk, p['id'], AVOCAT[0], self.nc)

    def test_IMP04D_destinataire_modifie_invalide_l_autorisation(self):
        p = self.paquet()
        self.autoriser(p)
        neuf = paquets.modifier(self.desk, p['id'], {'version': p['version'], 'destinataires': [{'nom': 'Maître ORLANDEC'}]}, *AVOCAT, self.nc)
        self.assertEqual((neuf['etat'], neuf['version']), ('brouillon', 2))
        self.assertNotEqual(neuf['manifeste_sha'], p['manifeste_sha'])
        self.assertEqual(neuf['autorisation']['etat'], 'invalidee')
        with self.assertRaises(Stop) as refus:
            paquets.exiger_autorisation(self.desk, p['id'], self.nc)
        self.assertEqual(str(refus.exception), 'autorisation_absente_ou_perimee')
        with self.assertRaises(Stop) as vieux:
            paquets.autoriser(self.desk, p['id'], {'manifeste_sha': p['manifeste_sha'], 'action': 'envoi'}, *AVOCAT, self.nc)
        self.assertEqual(str(vieux.exception), 'version_en_conflit')       # l'ancienne empreinte ne se réutilise pas
        self.assertEqual(self.autoriser(neuf)['etat'], 'autorise')

    def test_notes_confidentielles_et_projets_exclus_et_listes(self):
        note = self.fichier('/Dossiers/DEMO/note de strategie.docx', b'note interne fictive')
        lettre = self.fichier('/Dossiers/DEMO/lettre confidentielle entre avocats.pdf', b'courrier fictif')
        annexe = self.fichier('/Dossiers/DEMO/annexe.pdf', b'annexe fictive')
        self.desk.db.execute("INSERT INTO d51_documents(matter,path,name,nature,updated) VALUES(?,?,?,?,?)", (MID, note, 'note de strategie.docx', 'note_interne', 'x'))
        self.desk.db.commit()
        p = self.paquet(annexes=[note, lettre, annexe])
        chemins = [f['path'] for f in p['fichiers']]
        self.assertIn(annexe, chemins)
        self.assertNotIn(note, chemins)
        self.assertNotIn(lettre, chemins)
        self.assertEqual({x['path'] for x in p['exclus']}, {note, lettre})
        self.assertNotIn('note interne fictive', json.dumps(p['manifeste'], ensure_ascii=False))

    def test_piece_du_bordereau_devenue_interne_bloque(self):
        p = self.paquet()
        piece = next(f for f in p['fichiers'] if f['role'] == 'piece')
        pieces.migrer_categories(self.desk, MID)
        self.desk.db.execute('INSERT OR REPLACE INTO contentieux_categories5689(matter,path,categorie,created,updated) VALUES(?,?,?,?,?)', (MID, piece['path'], 'note_recherche', 'x', 'x'))
        self.desk.db.commit()
        neuf = paquets.modifier(self.desk, p['id'], {'version': p['version']}, *AVOCAT, self.nc)
        self.assertEqual(neuf['controles']['etat'], 'bloquant')
        self.assertNotIn(piece['path'], [f['path'] for f in neuf['fichiers']])
        with self.assertRaises(Stop) as refus:
            self.autoriser(neuf)
        self.assertEqual(str(refus.exception), 'controles_bloquants')

    def test_controles_a_refaire_bloquent_l_autorisation(self):
        p = self.paquet()
        self.piece('2')               # le registre change : le contrôle de l'acte ne vaut plus
        neuf = paquets.modifier(self.desk, p['id'], {'version': p['version']}, *AVOCAT, self.nc)
        self.assertEqual(neuf['controles']['etat'], 'bloquant')
        self.assertIn('à refaire', neuf['controles']['lignes'][0]['detail'])

    def test_archive_n_est_pas_une_preuve_de_depot(self):
        p = self.paquet()
        with self.assertRaises(Stop) as sans:
            paquets.archive(self.desk, p['id'], AVOCAT[0], self.nc)
        self.assertEqual(str(sans.exception), 'autorisation_absente_ou_perimee')
        self.autoriser(p)
        raw, nom = paquets.archive(self.desk, p['id'], AVOCAT[0], self.nc)
        with zipfile.ZipFile(io.BytesIO(raw)) as z:
            manifeste = json.loads(z.read('manifeste.json'))
            self.assertEqual(len(z.namelist()), 1 + len(p['fichiers']))
        self.assertEqual(manifeste['mention'], paquets.MENTION)
        self.assertEqual(manifeste['depot'], {'effectue': False, 'preuve': None})
        self.assertTrue(nom.endswith('.zip'))
        vue = paquets.vue(self.desk, p['id'])
        self.assertNotIn(vue['etat'], ('depose', 'deposee', 'envoye'))
        self.assertEqual(vue['recus'], [])
        self.assertEqual(paquets.CANAUX_INTEGRES, frozenset())
        with self.assertRaises(Stop) as canal:
            paquets.enregistrer_recu(self.desk, p['id'], 'communication_confrere', 'REF-1')
        self.assertEqual(str(canal.exception), 'canal_non_integre')
        self.assertNotIn('rpva', ' '.join(paquets.CANAUX).lower())

    def test_acte_republie_ecart_local_visible_sans_reseau(self):
        p = self.paquet()
        self.autoriser(p)
        self.desk.db.execute("UPDATE contentieux_actes5689 SET sha256='autre' WHERE id=?", (self.vue_acte['id'],))
        self.desk.db.commit()
        lectures = self.nc.lectures
        vue = paquets.vue(self.desk, p['id'])
        self.assertEqual(self.nc.lectures, lectures)
        self.assertIn('L’acte a été republié depuis ce manifeste.', vue['ecarts_locaux'])


class Pages(Base):
    def test_routes_declarees_roles_et_inconnue_refusee(self):
        self.assertTrue(web5689.allowed('assistant', 'contentieux/matrice'))
        self.assertTrue(web5689.allowed('assistant', 'contentieux/matrice/pretention'))
        self.assertFalse(web5689.allowed('assistant', 'contentieux/matrice/decision'))
        self.assertFalse(web5689.allowed('assistant', 'contentieux/paquets/autoriser'))
        self.assertFalse(web5689.allowed('assistant', 'contentieux/delais/regle'))
        self.assertTrue(web5689.allowed('avocat', 'contentieux/paquets/autoriser'))
        self.assertFalse(web5689.allowed('avocat', 'contentieux/inconnue'))
        self.assertEqual(membres5650.category('POST', '/api440/m5689/contentieux/paquets/autoriser'), 'valider')
        self.assertIn('exporter', membres5650.fine_rights('GET', '/api440/m5689/contentieux/paquets/export'))

    def test_ecriture_refusee_sans_jeton_et_identite_du_serveur(self):
        raw = b'{}'
        env = {'HTTP_X_AXIORHUB_USER': AVOCAT[0], 'HTTP_X_AXIORHUB_ROLE': AVOCAT[1], 'HTTP_ORIGIN': 'https://ailleurs.example.test', 'HTTP_X_CSRF_TOKEN': 'faux', 'CONTENT_LENGTH': '2', 'CONTENT_TYPE': 'application/json', 'wsgi.input': io.BytesIO(raw)}
        out = web5689.api(env, self.desk, {'origin': 'https://cabinet.example.test', 'csrf': 'jeton'}, '/p', 'm5689/contentieux/matrice/pretention', {}, 'POST')
        self.assertEqual(self.js(out)['error'], 'origine_ou_csrf_refuse')
        fait = self.js(self.api('contentieux/matrice/pretention', body={'dossier': MID, 'cle': self.cle(), 'titre': 'Paiement', 'auteur': 'pirate', 'role': 'administrateur'}, who=ASSISTANT))
        self.assertEqual(fait['auteur'], ASSISTANT[0])
        refus = self.api('contentieux/matrice/pretention', body={'dossier': MID, 'cle': self.cle(), 'titre': 'Paiement validé', 'valider': True}, who=ASSISTANT)
        self.assertEqual(refus['status'], '403 Forbidden')

    def test_version_perimee_409(self):
        c = self.pretention(False)
        out = self.api('contentieux/matrice/decision', body={'dossier': MID, 'objet': 'pretention', 'id': c['id'], 'action': 'valider', 'version': 7})
        self.assertEqual(out['status'], '409 Conflict')

    def test_dossier_reserve_par_identifiant_d_objet(self):
        self.matrice_complete()
        acte = self.acte()
        membres5650.set_viewer(frozenset({MID}))
        out = self.api('contentieux/actes/vue', {'id': acte['id']}, who=ASSISTANT)
        self.assertEqual(out['status'], '403 Forbidden')
        self.assertNotIn('Conclusions DURAND', out['body'])
        membres5650.set_viewer(frozenset())

    def test_routes_de_lecture_repondent(self):
        self.matrice_complete()
        for route in ('matrice', 'pieces', 'doublons', 'categories', 'actes', 'delais', 'coherence', 'audiences', 'paquets', 'paquet-contentieux'):
            self.assertEqual(self.api('contentieux/' + route, {'dossier': MID})['status'], '200 OK', route)
        self.assertTrue(self.js(self.api('contentieux/regles'))['regles'])

    def test_candidat_par_formulaire_a_plat(self):
        out = self.js(self.api('contentieux/delais/candidat', body={'dossier': MID, 'cle': self.cle(), 'evenement': 'signification_jugement', 'date_evenement': '2026-03-12', **{'c_' + k: v for k, v in COMPLET.items()}}))
        self.assertEqual((out['statut'], out['date_calculee']), ('propose', '2026-04-13'))

    def test_page_du_dossier_tous_onglets(self):
        self.matrice_complete()
        self.rediger()
        delais.proposer(self.desk, MID, {'cle': self.cle(), 'evenement': 'signification_jugement', 'date_evenement': ''}, *AVOCAT)
        paquets.preparer_audience(self.desk, MID, {'cle': self.cle()}, *AVOCAT)
        from agent.web440 import shell
        env = {'HTTP_X_AXIORHUB_USER': AVOCAT[0], 'HTTP_X_AXIORHUB_ROLE': AVOCAT[1]}
        for onglet, attendu in (('matrice', 'Paiement du solde'), ('pieces', 'Facture fictive'), ('actes', 'Conclusions DURAND'), ('delais', 'À vérifier : aucune date certaine'), ('audience', 'Version 1'), ('paquets', 'AxiorHub ne dépose rien'), ('categories', 'Classement des documents')):
            html = web.page(self.desk, env, {'csrf': 'jeton'}, '/p', {'dossier': MID, 'onglet': onglet}, shell)
            self.assertIn(attendu, html, onglet)
            self.assertNotIn('<script>', html, onglet)
        self.assertIn('<select name="dossier">', web.page(self.desk, env, {'csrf': 'jeton'}, '/p', {}, shell))

    def test_page_servie_par_la_route_commune(self):
        from agent import web440
        env = {'HTTP_X_AXIORHUB_USER': AVOCAT[0], 'HTTP_X_AXIORHUB_ROLE': AVOCAT[1], 'REQUEST_METHOD': 'GET'}
        out = web440.route(env, self.f.c, {'csrf': 'jeton', 'origin': 'https://cabinet.example.test'}, '/p', '/contentieux', {'dossier': MID, 'onglet': 'delais'}, 'GET')
        self.assertEqual(out['status'], '200 OK')
        self.assertIn('Règles disponibles', out['body'])
        absent = web440.route(env, self.f.c, {'csrf': 'jeton', 'origin': 'https://cabinet.example.test'}, '/p', '/contentieux', {'dossier': 'DOS-999'}, 'GET')
        self.assertEqual(absent['status'], '404 Not Found')

    def test_valeurs_echappees_dans_la_page(self):
        claim = self.pretention(titre='<script>alert(1)</script> paiement')
        self.element(claim, 'fait', '<img src=x onerror=alert(1)>')
        from agent.web440 import shell
        html = web.page(self.desk, {'HTTP_X_AXIORHUB_USER': AVOCAT[0], 'HTTP_X_AXIORHUB_ROLE': AVOCAT[1]}, {'csrf': 'jeton'}, '/p', {'dossier': MID}, shell)
        self.assertNotIn('<script>alert', html)
        self.assertNotIn('<img src=x', html)

    def test_tables_a_l_export(self):
        declarees = set(re.findall(r'CREATE TABLE IF NOT EXISTS (\w+)', base.SCHEMA + delais.SCHEMA))
        exportees = {t[2] for t in export5688.TABLES}
        self.assertFalse(declarees - exportees)
        self.assertEqual(next(t for t in export5688.TABLES if t[2] == 'contentieux_sections5689')[4], ('acte_id', 'contentieux_actes5689', 'id', 'matter'))

    def test_acces_depuis_le_dossier_et_fonction_declaree(self):
        from agent import fonctions5631, navigation5643
        self.assertIn('contentieux', [f[0] for f in fonctions5631.FUNCTIONS])
        self.assertEqual(navigation5643.PARENT['contentieux'], 'dossiers')
        self.assertNotIn('contentieux', navigation5643.PANEL)


class Mutations(Base):
    """Chaque garde-fou : un test qui échouerait s'il était retiré. Le retrait se fait en mémoire, jamais dans l'arbre."""

    def test_sans_relecture_des_fichiers_un_fichier_change_passerait(self):
        p = Paquets.paquet(self)
        Paquets.autoriser(self, p)
        piece = next(f for f in p['fichiers'] if f['role'] == 'piece')
        self.nc.add(piece['path'], b'contenu change')
        with patch.object(paquets, 'verifier', return_value={'conforme': True, 'ecarts': []}):
            self.assertTrue(paquets.exiger_autorisation(self.desk, p['id'], self.nc))        # garde-fou retiré : l'ancien accord passe
        with self.assertRaises(Stop):
            paquets.exiger_autorisation(self.desk, p['id'], self.nc)

    def test_sans_note_interne_retenue_une_note_partirait(self):
        note = self.fichier('/Dossiers/DEMO/note de strategie.docx', b'note fictive')
        self.desk.db.execute("INSERT INTO d51_documents(matter,path,name,nature,updated) VALUES(?,?,?,?,?)", (MID, note, 'note de strategie.docx', 'note_interne', 'x'))
        self.desk.db.commit()
        with patch.object(pieces, 'est_interne', return_value=False):
            fichiers = paquets._contenu(self.desk, MID, None, [note], self.nc)[0]
        self.assertEqual([f['path'] for f in fichiers], [note])
        self.assertEqual(paquets._contenu(self.desk, MID, None, [note], self.nc)[0], [])

    def test_sans_controle_de_regle_une_date_deviendrait_certaine(self):
        x = delais.proposer(self.desk, MID, {'cle': self.cle(), 'evenement': 'signification_jugement', 'date_evenement': '2026-03-12', 'inputs': dict(COMPLET)}, *AVOCAT)
        with patch.object(delais, 'validation', return_value={'par': 'personne'}):
            self.assertTrue(delais.decider(self.desk, x['id'], {'action': 'valider', 'version': x['revision']}, *AVOCAT)['certaine'])
        y = delais.proposer(self.desk, MID, {'cle': self.cle(), 'evenement': 'signification_jugement', 'date_evenement': '2026-03-13', 'inputs': dict(COMPLET)}, *AVOCAT)
        with self.assertRaises(Stop):
            delais.decider(self.desk, y['id'], {'action': 'valider', 'version': y['revision']}, *AVOCAT)

    def test_sans_garde_de_role_un_assistant_validerait(self):
        c = self.pretention(False)
        with patch.object(base, 'garde_role', return_value=None):
            self.assertEqual(base.decider(self.desk, MID, {'objet': 'pretention', 'id': c['id'], 'action': 'valider', 'version': 1}, *ASSISTANT)['statut'], 'valide')
        d = self.pretention(False, titre='Seconde prétention')
        with self.assertRaises(Stop):
            base.decider(self.desk, MID, {'objet': 'pretention', 'id': d['id'], 'action': 'valider', 'version': 1}, *ASSISTANT)

    def test_sans_filtre_des_jetons_une_piece_inventee_passerait(self):
        section = {'autorisees': '[]'}
        with patch.object(pieces, 'JETON', re.compile(r'(?!x)x')):
            texte, _ = actes._valider_sortie({'texte': 'Il ressort de [[piece:%s]] que oui.' % ('a' * 32), 'reserves': []}, section, set())
            self.assertIn('[[piece:', texte)                          # garde-fou retiré : la pièce inventée passe
        with self.assertRaises(Stop):
            actes._valider_sortie({'texte': 'Il ressort de [[piece:%s]] que oui.' % ('a' * 32), 'reserves': []}, section, set())


if __name__ == '__main__':
    unittest.main()
