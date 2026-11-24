"""5.6.109, lot G2 (G07) : remplacements et postulation liés à la même audience.

Données fictives (DURAND, MARTIN, ZORVAL, KERVILAC, DOS-001, DOS-002, RG 24/01234, Valbrune, example.test) ; stockage local temporaire ; tout
appel réseau non simulé fait échouer l'essai ; aucun modèle. Rien n'est envoyé au confrère par le logiciel : l'envoi et l'acceptation sont
déclarés avec leur preuve. Le garde-fou « accès du confrère révoqué après annulation » a un essai qui échouerait s'il était retiré.
"""
import base64
import io
import json
import unittest
from unittest.mock import patch
import zipfile

from test_v56109_partage_client import Base
from test_v5697_gestion import ASSISTANT, AUTRE, AVOCAT, MID, MID2
from agent import audiences5688 as au, crm5636, partage_client56109 as P, remplacements56109 as R, taches5688
from agent.common import Stop

DEBUT = '2026-12-15T09:30'


class RemplacementsBase(Base):
    def setUp(self):
        super().setUp()
        crm5636.schema(self.desk)
        self.confrere = crm5636.save(self.desk, {'kind': 'personne', 'category': 'confrere', 'name': 'KERVILAC', 'first_name': 'Jeanne',
                                                 'email': 'kervilac@example.test', 'barreau': 'Valbrune'})
        self.hid = au.save(self.desk, {'matter': MID, 'type': 'plaidoiries', 'start': DEBUT, 'location': 'Tribunal judiciaire de Valbrune',
                                       'juridiction': 'Tribunal judiciaire de Valbrune', 'rg': '24/01234'}, AVOCAT[0])['id']

    def creer(self, role='substitution', hid=None, confrere=None, who=AVOCAT):
        return R.creer(self.desk, who, {'audience': hid or self.hid, 'role': role, 'confrere': confrere or self.confrere['id']}, self.cle())

    def instruire(self, r, documents=(), honoraire='', **extra):
        return R.instruire(self.desk, AVOCAT, r['id'], {'cle': self.cle(), 'version': r['version'], 'perimetre': 'Plaidoiries uniquement',
                                                       'instructions': 'Solliciter le renvoi si le confrère adverse est absent.',
                                                       'documents': ['%s|%s' % d for d in documents], 'honoraire': honoraire, **extra})

    def t(self, r, action, **data):
        return R.transition(self.desk, AVOCAT, r['id'], {'cle': self.cle(), 'version': r['version'], 'action': action, **data})

    def jusqu_a_acceptation(self, documents=()):
        r = self.instruire(self.creer(), documents)
        r = self.t(r, 'preparer')
        r = self.t(r, 'envoyer', preuve='Courriel au confrère du 23/11/2026, réf. ENV-0001')
        return self.t(r, 'accepter', preuve='Réponse écrite du confrère du 23/11/2026 : « J’accepte »', source='courriel', expresse=True)


class Remplacements(RemplacementsBase):
    def test_G07_instruction_et_acceptation_versionnees_et_prouvees(self):
        r = self.instruire(self.creer(), honoraire='150,00')
        self.assertEqual((r['instruction_version'], r['honoraire_minor'], r['honoraire_statut']), (1, 15000, 'convenu'))
        self.assertEqual(r['instruction']['contenu']['audience']['audience_id'], self.hid)
        r = self.t(r, 'preparer')
        with self.assertRaisesRegex(Stop, 'transition_impossible'):
            self.t(r, 'accepter', preuve='Accord oral', expresse=True)              # pas d'acceptation avant l'envoi
        r = self.t(r, 'envoyer', preuve='Courriel au confrère, réf. ENV-0002')
        for source, expresse in (('accuse_automatique', True), ('ouverture_lien', True), ('courriel', False)):
            with self.assertRaisesRegex(Stop, 'acceptation_non_prouvee'):
                self.t(r, 'accepter', preuve='Accusé de réception automatique du serveur', source=source, expresse=expresse)
        r = self.t(r, 'accepter', preuve='Réponse du confrère : « Je confirme ma présence »', source='courriel', expresse=True)
        self.assertEqual((r['etat'], r['acceptee_version'], r['acceptation_valide']), ('acceptee', 1, True))
        natures = [(p['nature'], p['instruction_version']) for p in r['preuves']]
        self.assertEqual(natures, [('envoi', 1), ('acceptation', 1)])
        self.assertTrue(all(len(p['sha256']) == 64 for p in r['preuves']))

    def test_G07_audience_deplacee_nouvelle_version_a_confirmer(self):
        r = self.jusqu_a_acceptation()
        h = au.get(self.desk, self.hid)
        au.save(self.desk, {'id': self.hid, 'version': h['version'], 'start': '2026-12-16T14:00'}, AVOCAT[0])
        v = R.vue(self.desk, AVOCAT, r['id'])
        self.assertIn('starts', v['ecarts_audience'])                               # changement signalé (lecture seule)
        self.assertEqual(v['etat'], 'acceptee')
        r2 = self.instruire(v)
        self.assertEqual((r2['etat'], r2['instruction_version'], r2['acceptation_valide']), ('preparee', 2, False))
        self.assertEqual(r2['ecarts_audience'], [])
        r2 = self.t(r2, 'preparer') if r2['etat'] == 'brouillon' else r2
        with self.assertRaisesRegex(Stop, 'transition_impossible'):
            self.t(r2, 'accepter', preuve='Ancienne acceptation', source='courriel', expresse=True)
        r3 = self.t(r2, 'envoyer', preuve='Nouvelle instruction envoyée, réf. ENV-0003')
        r3 = self.t(r3, 'accepter', preuve='Accord du confrère sur le nouvel horaire', source='courriel', expresse=True)
        self.assertEqual(r3['acceptee_version'], 2)
        self.assertEqual(len(au.listing(self.desk, matter=MID)), 1)                # toujours une seule audience, aucun événement recopié

    def test_G07_acces_du_confrere_limite_expirant_et_revoque_apres_annulation_et_mutation(self):
        retenu = self.livrable(titre='Conclusions récapitulatives DURAND')
        autre = self.livrable(titre='Note interne de stratégie')
        r = self.jusqu_a_acceptation([retenu])
        r = self.t(r, 'partager')
        self.assertEqual(r['etat'], 'pieces_partagees')
        z = zipfile.ZipFile(io.BytesIO(base64.b64decode(r['download']['base64'])))
        man = json.loads(z.read('manifeste.json'))
        self.assertEqual([(x['document_id'], x['version_id']) for x in man['ressources']], [retenu])
        self.assertEqual(man['destinataire'], 'kervilac@example.test')
        self.assertEqual(man['permissions'], {'lire': True, 'telecharger': True, 'deposer': False, 'repondre': False})
        self.assertLessEqual(man['expire_le'][:10], '2026-12-17')                  # au lendemain de l'audience (fuseau du cabinet)
        P.acceder(self.desk, r['partage_id'], man['jeton_retour'], *retenu)
        with self.assertRaisesRegex(Stop, 'introuvable'):
            P.acceder(self.desk, r['partage_id'], man['jeton_retour'], *autre)       # seules les versions retenues
        annule = self.t(r, 'annuler', motif='Audience renvoyée par le greffe')
        self.assertEqual(annule['etat'], 'annulee')
        self.assertEqual(annule['partage']['etat'], 'revoque')
        with self.assertRaisesRegex(Stop, 'introuvable'):
            P.acceder(self.desk, r['partage_id'], man['jeton_retour'], *retenu)
        self.assertIn('annulation', [p['nature'] for p in annule['preuves']])       # historique conservé
        self.assertIn('envoi', [p['nature'] for p in annule['preuves']])
        # garde retirée : sans révocation à l'annulation, le confrère garderait l'accès
        r2 = self.jusqu_a_acceptation([retenu])
        r2 = self.t(r2, 'partager')
        man2 = json.loads(zipfile.ZipFile(io.BytesIO(base64.b64decode(r2['download']['base64']))).read('manifeste.json'))
        with patch.object(P, 'revoquer_interne', lambda *a, **k: None):
            self.t(r2, 'annuler', motif='Essai de la garde')
        self.assertTrue(P.acceder(self.desk, r2['partage_id'], man2['jeton_retour'], *retenu)[0])

    def test_G07_alias_du_cabinet_jamais_destinataire(self):
        alias = crm5636.save(self.desk, {'kind': 'personne', 'category': 'confrere', 'name': 'Moi-même', 'email': AVOCAT[0]})
        with self.assertRaisesRegex(Stop, 'alias_du_cabinet'):
            self.creer(confrere=alias['id'])
        sans = crm5636.save(self.desk, {'kind': 'personne', 'category': 'confrere', 'name': 'ORLANDEC'})
        with self.assertRaisesRegex(Stop, 'adresse_confrere_requise'):
            self.creer(confrere=sans['id'])

    def test_G07_compte_rendu_et_cout_suivis_honoraire_distinct_de_la_facture(self):
        tables = [t for (t,) in self.desk.db.execute("SELECT name FROM sqlite_master WHERE type='table' AND (name LIKE '%factur%' OR name LIKE '%frais%' "
                                                     "OR name LIKE '%debours%' OR name LIKE 'invoice%' OR name LIKE 'b53%')")]
        avant = {t: self.desk.db.execute('SELECT COUNT(*) FROM %s' % t).fetchone()[0] for t in tables}
        r = self.jusqu_a_acceptation()
        r = self.t(r, 'realiser')
        self.assertTrue(r['tache_compte_rendu'])
        taches = taches5688.of_origin(self.desk, 'audience', self.hid)
        self.assertEqual([t['id'] for t in taches], [r['tache_compte_rendu']])     # file existante, une seule tâche
        r = self.t(r, 'compte_rendu', texte='Audience tenue, affaire mise en délibéré au 15 janvier 2027.')
        item = self.desk.db.execute("SELECT * FROM prepared_items5689 WHERE source_service='remplacements56109'").fetchone()
        self.assertEqual((item['matter_id'], item['document_id']), (MID, r['compte_rendu_document_id']))
        r = self.t(r, 'honoraire', honoraire='180,50', devise='EUR')
        self.assertEqual((r['honoraire_minor'], r['qualification']), (18050, 'a_qualifier'))   # jamais « débours » sans décision
        self.assertIn('distinct de la facture du client', r['honoraire']['mention'])
        r = self.t(r, 'a_rapprocher')
        with self.assertRaisesRegex(Stop, 'preuve_requise'):
            self.t(r, 'rapprocher')
        r = self.t(r, 'rapprocher', reference='Facture du confrère n° KV-2026-001')
        self.assertEqual((r['etat'], r['honoraire_statut']), ('cloturee', 'rapproche'))
        self.assertEqual({t: self.desk.db.execute('SELECT COUNT(*) FROM %s' % t).fetchone()[0] for t in tables}, avant)   # aucune facture, aucun frais

    def test_G07_second_clic_ne_renvoie_pas_et_meme_audience_partout(self):
        r = self.instruire(self.creer())
        r = self.t(r, 'preparer')
        corps = {'cle': self.cle(), 'version': r['version'], 'action': 'envoyer', 'preuve': 'Courriel réf. ENV-0009'}
        a = R.transition(self.desk, AVOCAT, r['id'], corps)
        b = R.transition(self.desk, AVOCAT, r['id'], corps)                          # double clic
        self.assertTrue(b['rejoue'])
        self.assertEqual([p['nature'] for p in b['preuves']], ['envoi'])
        self.assertEqual([x['id'] for x in R.remplacements_audience(self.desk, AVOCAT, self.hid)], [a['id']])
        self.assertEqual([x['id'] for x in R.remplacements_dossier(self.desk, AVOCAT, MID)], [a['id']])
        html = R.bloc_dossier(self.desk, '/p', {'id': MID}, AVOCAT)
        self.assertIn('Remplacements et postulation', html)
        self.assertIn('kervilac@example.test', html)

    def test_G07_roles_et_dossier_reserve(self):
        out = self.http(R, 'creer', {'audience': self.hid, 'role': 'postulation', 'confrere': self.confrere['id'], 'cle': self.cle()}, who=ASSISTANT)
        self.assertEqual(out['status'], '403 Forbidden')
        hid2 = au.save(self.desk, {'matter': MID2, 'type': 'mise_en_etat', 'start': DEBUT}, AVOCAT[0])['id']
        r = self.creer(hid=hid2)
        for ident in (r['id'], 'rp-inexistant'):
            self.assertEqual(self.http(R, ident, who=AUTRE)['status'], '404 Not Found')
        self.assertEqual(self.http(R, 'audience/' + hid2, who=AUTRE)['status'], '404 Not Found')
        avant = self.desk.db.total_changes
        self.assertEqual(self.http(R, 'dossier/' + MID)['status'], '200 OK')
        self.assertEqual(self.desk.db.total_changes, avant)



class EcheanceDeLAcces(RemplacementsBase):
    """C2 (relecture de l'intégrateur) : l'accès du confrère ne vaut jamais au-delà du lendemain de l'audience COURANTE."""

    def test_G07_C2_audience_avancee_l_acces_suit_la_nouvelle_date(self):
        retenu = self.livrable(titre='Conclusions récapitulatives DURAND')
        r = self.t(self.jusqu_a_acceptation([retenu]), 'partager')
        man = json.loads(zipfile.ZipFile(io.BytesIO(base64.b64decode(r['download']['base64']))).read('manifeste.json'))
        h = au.get(self.desk, self.hid)
        au.save(self.desk, {'id': self.hid, 'version': h['version'], 'start': '2026-12-01T09:30'}, AVOCAT[0])   # audience avancée de deux semaines
        with self.assertRaisesRegex(Stop, 'introuvable'):
            P.acceder(self.desk, r['partage_id'], man['jeton_retour'], *retenu, quand='2026-12-05T12:00:00+00:00')   # relu à la lecture
        P.acceder(self.desk, r['partage_id'], man['jeton_retour'], *retenu, quand='2026-12-01T20:00:00+00:00')
        v = R.vue(self.desk, AVOCAT, r['id'])
        self.assertLessEqual(v['partage']['expire_le'][:10], '2026-12-02')                                    # échéance affichée recalculée
        r2 = self.t(v, 'realiser')                                                                             # une étape recalcule l'échéance enregistrée
        stocke = self.desk.db.execute('SELECT expire_le FROM partages_client56109 WHERE id=?', (r2['partage_id'],)).fetchone()[0]
        self.assertLessEqual(stocke[:10], '2026-12-02')

if __name__ == '__main__':
    unittest.main()
