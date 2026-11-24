"""5.6.109, lot G2 (G03) : demandes au client et partage d'une version approuvée exacte, dans le dossier existant.

Données fictives (DURAND, MARTIN, ZORVAL, KERVILAC, DOS-001, DOS-002, example.test) ; stockage local temporaire, Nextcloud OCS simulé
(``FauxOCS`` de ``test_v5697_gestion``) ; tout appel réseau non simulé fait échouer l'essai ; aucun modèle. Le vrai partage Nextcloud et le vrai
navigateur ne sont pas qualifiés ici. Chaque garde-fou (cloisonnement, expiration, révocation, version exacte, action d'une personne) a un
essai qui échouerait s'il était retiré (mutation en mémoire par ``patch``, jamais de code laissé altéré).
"""
import base64
from datetime import date, datetime, timedelta, timezone
import hashlib
import io
import json
from pathlib import Path
import unittest
from unittest.mock import patch
import zipfile

import test_v5697_gestion as tg
from test_v5697_gestion import ADMIN, ASSISTANT, AUTRE, AVOCAT, CLIENT_MAIL, MID, MID2
from agent import acces5697, artefacts56109 as A, livrables5689 as liv, membres5650, partage_client56109 as P, remises5697 as rem
from agent.common import Stop

ROOT = Path(__file__).resolve().parent.parent


class Base(tg.Base):
    """Socle commun des essais du lot G2 : dossiers DURAND (DOS-001) et MARTIN (DOS-002, réservé à l'avocate), versions du registre."""

    def setUp(self):
        super().setUp()
        self.n = 0

    def cle(self):
        self.n += 1
        return 'cle-g2-%04d' % self.n

    def livrable(self, titre='Chronologie DURAND contre ZORVAL', mid=MID, approuver=True):
        out = A.creer_depuis_trame(self.desk, AVOCAT[0], 'avocat', frozenset(), None, 'chronologie-sourcee', mid, titre, self.cle())
        row = liv.charger(self.desk, out['document_id'])
        if approuver:
            liv.relire(self.desk, out['document_id'], row['version_id'], 'valide', AVOCAT[0], 'avocat')
        return out['document_id'], row['version_id']

    def nouvelle_revision(self, doc, titre='Chronologie modifiée après approbation'):
        lu = A.lire(self.desk, doc)
        payload = dict(lu['payload'], title=titre)
        out = A.ecrire(self.desk, AVOCAT[0], 'avocat', frozenset(), None, payload, self.cle(), lu['revision'], doc)
        return out['version_id']

    def livrable_fichier(self, rel='Dossiers/DURAND/Conclusions DURAND.docx', data=b'PK\x03\x04 conclusions fictives DURAND', mid=MID, approuver=True):
        path = self.fichier(rel, data)
        sha = hashlib.sha256(data).hexdigest()
        liv.ensure_schema(self.desk)
        item = liv.observe(self.desk, {'service': 'essai_g2', 'object': rel, 'version': sha[:8], 'matter': mid, 'kind': 'conclusions', 'title': Path(rel).stem,
                                       'path': path, 'sha256': sha, 'size': len(data), 'state': 'verified'})
        self.desk.db.commit()
        row = liv._item(self.desk, item)
        if approuver:
            liv.relire(self.desk, row['document_id'], row['version_id'], 'valide', AVOCAT[0], 'avocat')
        return row['document_id'], row['version_id'], path

    def expiration(self, jours=10):
        return (P.jour_cabinet(self.desk) + timedelta(days=jours)).isoformat()

    def http(self, module, route, body=None, who=AVOCAT, methode=None):
        methode = methode or ('POST' if body is not None else 'GET')
        raw = json.dumps(body or {}).encode()
        env = {'HTTP_X_AXIORHUB_USER': who[0], 'HTTP_X_AXIORHUB_ROLE': who[1], 'HTTP_ORIGIN': 'https://cabinet.example.test', 'HTTP_X_CSRF_TOKEN': 'jeton',
               'CONTENT_LENGTH': str(len(raw)), 'CONTENT_TYPE': 'application/json', 'wsgi.input': io.BytesIO(raw), 'REQUEST_METHOD': methode}
        out = module.api(env, self.desk, {'origin': 'https://cabinet.example.test', 'csrf': 'jeton'}, '/p', module.PREFIXE + route, {}, methode)
        out['json'] = json.loads(out['body']) if isinstance(out.get('body'), str) else None
        return out

    def arbre(self):
        return sorted(str(p.relative_to(self.root)) for p in self.root.rglob('*') if p.is_file())


class PartageBase(Base):
    def partage(self, mid=MID, destinataire=CLIENT_MAIL, ressources=None, validations=None, **extra):
        doc, ver = ressources or self.livrable(mid=mid)
        data = {'destinataire': destinataire, 'expiration': self.expiration(), 'ressources': [{'document_id': doc, 'version_id': ver}],
                'validations': validations if validations is not None else [{'document_id': doc, 'version_id': ver}],
                'pieces': ['Kbis de moins de trois mois'], 'questions': ['Date de la mise en demeure ?'], **extra}
        prep = P.preparer(self.desk, AVOCAT, mid, data, self.cle())
        return prep, doc, ver

    def confirmer(self, prep):
        out = P.confirmer(self.desk, AVOCAT, prep['id'], {'cle': self.cle(), 'version': prep['version']})
        z = zipfile.ZipFile(io.BytesIO(base64.b64decode(out['download']['base64'])))
        manifeste = json.loads(z.read('manifeste.json'))
        return out, z, manifeste

    def retour(self, manifeste, reponses, fichiers=None, jeton=None, pid=None):
        tampon = io.BytesIO()
        with zipfile.ZipFile(tampon, 'w') as z:
            z.writestr('reponse.json', json.dumps({'format': 'axh-reponse/1', 'partage_id': pid or manifeste['partage_id'],
                                                   'jeton_retour': jeton or manifeste['jeton_retour'], 'reponses': reponses}))
            for nom, raw in (fichiers or {}).items():
                z.writestr('fichiers/' + nom, raw)
        return {'nom': 'retour.zip', 'contenu': 'data:application/zip;base64,' + base64.b64encode(tampon.getvalue()).decode()}


# ============================================================================================== préparer, aperçu, version exacte
class Preparation(PartageBase):
    def test_G03_preparer_n_envoie_rien_et_rend_l_apercu_exact(self):
        prep, doc, ver = self.partage()
        self.assertEqual(prep['etat'], 'prepare')
        self.assertEqual([(r['document_id'], r['version_id']) for r in prep['ressources']], [(doc, ver)])
        contenu = self.desk.db.execute('SELECT sha256 FROM livrables_contenus56109 WHERE document_id=?', (doc,)).fetchone()['sha256']
        self.assertEqual(prep['ressources'][0]['sha256'], contenu)                    # empreinte de la version exacte
        self.assertEqual({d['genre'] for d in prep['demandes']}, {'piece', 'question', 'validation'})
        self.assertTrue(prep['permissions']['deposer'] and prep['permissions']['repondre'])   # droits ajoutés : visibles dans l'aperçu
        self.assertEqual(self.desk.db.execute("SELECT secret_hash FROM partages_client56109 WHERE id=?", (prep['id'],)).fetchone()[0], '')
        self.assertNotIn('download', prep)

    def test_G03_version_non_approuvee_refusee_et_mutation(self):
        doc, ver = self.livrable(approuver=False)
        with self.assertRaisesRegex(Stop, 'version_non_approuvee'):
            self.partage(ressources=(doc, ver), validations=[])
        with patch.object(P, 'est_approuvee', lambda desk, row: True):              # garde retirée : la version passerait
            prep, _, _ = self.partage(ressources=(doc, ver), validations=[])
        self.assertEqual(prep['etat'], 'prepare')

    def test_G03_destinataire_hors_dossier_exige_une_verification(self):
        doc, ver = self.livrable()
        with self.assertRaisesRegex(Stop, 'destinataire_non_verifie'):
            self.partage(ressources=(doc, ver), destinataire='inconnu@example.test')
        prep, _, _ = self.partage(ressources=(doc, ver), destinataire='inconnu@example.test', identite_verifiee=True)
        self.assertIn('vérifiée par', prep['destinataire_verifie_par'])

    def test_G03_version_d_un_autre_dossier_refusee(self):
        doc, ver = self.livrable(mid=MID2)
        with self.assertRaisesRegex(Stop, 'version_hors_dossier'):
            self.partage(mid=MID, ressources=(doc, ver), validations=[])

    def test_G03_paquet_contient_la_version_exacte_et_la_reponse_reste_sur_l_ancienne(self):
        prep, doc, ver = self.partage()
        out, z, manifeste = self.confirmer(prep)
        self.assertEqual(out['etat'], 'confirme')
        res = manifeste['ressources'][0]
        brut = z.read(res['fichier'])
        self.assertEqual(hashlib.sha256(brut).hexdigest(), res['sha256'])
        self.assertEqual((res['document_id'], res['version_id']), (doc, ver))
        nouvelle = self.nouvelle_revision(doc)                                       # document modifié APRÈS l'approbation et le partage
        self.assertNotEqual(nouvelle, ver)
        raw, _ = P.acceder(self.desk, prep['id'], manifeste['jeton_retour'], doc, ver)
        self.assertEqual(raw, brut)                                                 # le client lit toujours la version partagée
        with self.assertRaisesRegex(Stop, 'introuvable'):
            P.acceder(self.desk, prep['id'], manifeste['jeton_retour'], doc, nouvelle)   # la nouvelle version n'entre pas dans le partage
        val = next(d for d in manifeste['demandes'] if d['genre'] == 'validation')
        sha_nouvelle = self.desk.db.execute('SELECT sha256 FROM livrables_contenus56109 WHERE version_id=?', (nouvelle,)).fetchone()[0]
        with self.assertRaisesRegex(Stop, 'version_non_conforme'):
            P.importer_retour(self.desk, AVOCAT, prep['id'], {'paquet': self.retour(manifeste, [{'demande_id': val['id'], 'validation': {
                'document_id': doc, 'version_id': nouvelle, 'sha256': sha_nouvelle, 'decision': 'approuve'}}])})
        self.assertEqual(self.desk.db.execute('SELECT COUNT(*) FROM partages_client_reponses56109').fetchone()[0], 0)
        ok = P.importer_retour(self.desk, AVOCAT, prep['id'], {'paquet': self.retour(manifeste, [{'demande_id': val['id'], 'validation': {
            'document_id': doc, 'version_id': ver, 'sha256': res['sha256'], 'decision': 'approuve'}}])})
        rep = ok['reponses'][0]
        self.assertEqual((rep['document_id'], rep['version_id'], rep['sha256'], rep['decision']), (doc, ver, res['sha256'], 'approuve'))
        self.assertIn('pas une signature électronique qualifiée', ' '.join(ok['avertissements']))

    def test_G03_version_modifiee_dans_le_stockage_rien_n_est_partage(self):
        doc, ver, path = self.livrable_fichier()
        prep, _, _ = self.partage(ressources=(doc, ver), validations=[])
        (self.root / path.lstrip('/')).write_bytes(b'PK\x03\x04 conclusions remaniees')
        with self.assertRaisesRegex(Stop, 'version_modifiee_depuis_l_approbation'):
            P.confirmer(self.desk, AVOCAT, prep['id'], {'cle': self.cle(), 'version': prep['version']})
        self.assertEqual(P.vue(self.desk, AVOCAT, prep['id'])['etat'], 'prepare')


# ============================================================================================== cloisonnement entre clients
class Cloisonnement(PartageBase):
    def test_G03_client_A_ne_lit_rien_de_B_et_mutation(self):
        pa, doc_a, ver_a = self.partage(mid=MID)
        pb, doc_b, ver_b = self.partage(mid=MID2, destinataire='martin@example.test')
        _, _, man_a = self.confirmer(pa)
        _, _, man_b = self.confirmer(pb)
        for pid, jeton, doc, ver in ((pa['id'], man_a['jeton_retour'], doc_b, ver_b), (pb['id'], man_a['jeton_retour'], doc_b, ver_b)):
            with self.assertRaisesRegex(Stop, 'introuvable'):
                P.acceder(self.desk, pid, jeton, doc, ver)
        with self.assertRaisesRegex(Stop, 'paquet_autre_partage'):                   # retour de B importé dans A (ou jeton de A pour B)
            P.importer_retour(self.desk, AVOCAT, pa['id'], {'paquet': self.retour(man_b, [])})
        with self.assertRaisesRegex(Stop, 'paquet_autre_partage'):
            P.importer_retour(self.desk, AVOCAT, pb['id'], {'paquet': self.retour(man_b, [], jeton=man_a['jeton_retour'])})
        with patch.object(P, '_jeton_valide', lambda row, jeton: True):              # garde retirée : le jeton de A ouvrirait B
            raw, _ = P.acceder(self.desk, pb['id'], man_a['jeton_retour'], doc_b, ver_b)
        self.assertTrue(raw)

    def test_G03_partage_d_un_dossier_reserve_meme_refus_qu_un_absent(self):
        pb, _, _ = self.partage(mid=MID2, destinataire='martin@example.test')
        for ident in (pb['id'], 'pc-inexistant'):
            out = self.http(P, ident, who=AUTRE)
            self.assertEqual(out['status'], '404 Not Found')
            self.assertEqual(out['json']['error'], 'introuvable')
        self.assertEqual(self.http(P, 'dossier/' + MID2, who=AUTRE)['status'], '404 Not Found')
        self.assertNotIn(pb['id'], json.dumps(self.http(P, 'dossier/' + MID, who=AUTRE)['json']))


# ============================================================================================== expiration et révocation
class ExpirationRevocation(PartageBase):
    def test_G03_lien_expire_et_mutation(self):
        prep, doc, ver = self.partage()
        _, _, man = self.confirmer(prep)
        apres = (datetime.now(timezone.utc) + timedelta(days=30)).isoformat(timespec='seconds')
        P.acceder(self.desk, prep['id'], man['jeton_retour'], doc, ver)
        with self.assertRaisesRegex(Stop, 'introuvable'):
            P.acceder(self.desk, prep['id'], man['jeton_retour'], doc, ver, quand=apres)
        with patch.object(P, 'etat_effectif', lambda row, quand=None, desk=None: row['etat']):  # garde retirée : l'expiration ne fermerait rien
            P.acceder(self.desk, prep['id'], man['jeton_retour'], doc, ver, quand=apres)
        self.assertEqual(self.http(P, prep['id'])['status'], '200 OK')

    def test_G03_revocation_ferme_l_acces_futur_et_garde_l_historique(self):
        prep, doc, ver = self.partage()
        _, _, man = self.confirmer(prep)
        val = next(d for d in man['demandes'] if d['genre'] == 'question')
        P.importer_retour(self.desk, AVOCAT, prep['id'], {'paquet': self.retour(man, [{'demande_id': val['id'], 'texte': 'Le 3 mars, par lettre recommandée.'}])})
        v = P.vue(self.desk, AVOCAT, prep['id'])
        r = P.revoquer(self.desk, AVOCAT, prep['id'], {'cle': self.cle(), 'version': v['version'], 'motif': 'Mission terminée'})
        self.assertEqual(r['etat'], 'revoque')
        self.assertIn('ne peut pas être retirée à distance', r['message'])
        self.assertEqual(len(r['reponses']), 1)                                    # réponses et reçus conservés
        with self.assertRaisesRegex(Stop, 'introuvable'):
            P.acceder(self.desk, prep['id'], man['jeton_retour'], doc, ver)
        with self.assertRaisesRegex(Stop, 'partage_revoque'):
            P.importer_retour(self.desk, AVOCAT, prep['id'], {'paquet': self.retour(man, [{'demande_id': val['id'], 'texte': 'Autre réponse tardive'}])})


# ============================================================================================== réception contrôlée, quarantaine
class Reception(PartageBase):
    def _confirme(self):
        prep, doc, ver = self.partage()
        _, _, man = self.confirmer(prep)
        piece = next(d for d in man['demandes'] if d['genre'] == 'piece')
        return prep, man, piece

    def test_G03_fichiers_controles_puis_quarantaine_hors_du_dossier(self):
        prep, man, piece = self._confirme()
        avant = self.arbre()
        for fichiers, code in (({'kbis.pdf': b'pas un pdf'}, 'type_fichier_refuse'), ({'kbis.pdf': b'MZ\x90\x00exe'}, 'type_fichier_refuse'),
                               ({'outil.exe': b'%PDF-1.4'}, 'type_fichier_refuse')):
            with self.assertRaisesRegex(Stop, code):
                P.importer_retour(self.desk, AVOCAT, prep['id'], {'paquet': self.retour(man, [{'demande_id': piece['id'], 'fichiers': list(fichiers)}], fichiers)})
        tampon = io.BytesIO()
        with zipfile.ZipFile(tampon, 'w') as z:
            z.writestr('reponse.json', '{}')
            z.writestr('../evasion.pdf', b'%PDF-1.4')
        with self.assertRaisesRegex(Stop, 'paquet_invalide'):
            P.importer_retour(self.desk, AVOCAT, prep['id'], {'paquet': {'nom': 'x.zip', 'contenu_b64': base64.b64encode(tampon.getvalue()).decode()}})
        self.assertEqual(self.desk.db.execute('SELECT COUNT(*) FROM partages_client_quarantaine56109').fetchone()[0], 0)
        pdf = b'%PDF-1.4 kbis fictif DURAND'
        nom = 'K<b>is "DURAND"?*.pdf'
        out = P.importer_retour(self.desk, AVOCAT, prep['id'], {'paquet': self.retour(man, [{'demande_id': piece['id'], 'fichiers': [nom]}], {nom: pdf})})
        q = out['quarantaine'][0]
        self.assertEqual(q['etat'], 'quarantaine')
        self.assertNotIn('<', q['nom_neutralise'])
        self.assertNotIn('/', q['nom_neutralise'])
        self.assertEqual(q['sha256'], hashlib.sha256(pdf).hexdigest())
        self.assertEqual(self.arbre(), avant)                                      # rien dans le dossier avant la décision de l'avocat
        self.assertTrue((Path(self.f.c['state_dir']) / 'quarantaine56109').is_dir())
        c = P.classer(self.desk, AVOCAT, q['id'], {'cle': self.cle(), 'version': q['version'], 'destination': 'nos_pieces'})
        depose = self.root / c['chemin'].lstrip('/')
        self.assertEqual(depose.read_bytes(), pdf)
        self.assertTrue(c['chemin'].startswith('/Dossiers/DURAND/'))
        with self.assertRaisesRegex(Stop, 'deja_decide'):
            P.classer(self.desk, AVOCAT, q['id'], {'cle': self.cle(), 'version': q['version'] + 1})

    def test_G03_meme_retour_importe_deux_fois_meme_recu(self):
        prep, man, piece = self._confirme()
        paquet = self.retour(man, [{'demande_id': piece['id'], 'fichiers': ['kbis.pdf']}], {'kbis.pdf': b'%PDF-1.4 kbis'})
        a = P.importer_retour(self.desk, AVOCAT, prep['id'], {'paquet': paquet})
        b = P.importer_retour(self.desk, AVOCAT, prep['id'], {'paquet': paquet})
        self.assertEqual(a['recu'], b['recu'])
        self.assertTrue(b['rejoue'])
        self.assertEqual(self.desk.db.execute('SELECT COUNT(*) FROM partages_client_reponses56109').fetchone()[0], 1)
        self.assertEqual(self.desk.db.execute('SELECT COUNT(*) FROM partages_client_quarantaine56109').fetchone()[0], 1)

    def test_G03_refus_d_un_fichier_reste_hors_du_dossier(self):
        prep, man, piece = self._confirme()
        out = P.importer_retour(self.desk, AVOCAT, prep['id'], {'paquet': self.retour(man, [{'demande_id': piece['id'], 'fichiers': ['a.txt']}], {'a.txt': b'texte'})})
        q = out['quarantaine'][0]
        with self.assertRaisesRegex(Stop, 'motif_requis'):
            P.refuser_fichier(self.desk, AVOCAT, q['id'], {'version': q['version']})
        P.refuser_fichier(self.desk, AVOCAT, q['id'], {'version': q['version'], 'motif': 'Document illisible'})
        self.assertEqual(P.vue(self.desk, AVOCAT, prep['id'])['quarantaine'][0]['etat'], 'refuse')


# ============================================================================================== action d'une personne, rôles, secrets
class Personne(PartageBase):
    def test_G03_jamais_declenche_par_un_modele_ni_un_automatisme_et_mutation(self):
        prep, _, _ = self.partage()
        for acteur in (acces5697.AGENT, ('modele', 'avocat')):
            with self.assertRaisesRegex(Stop, 'action_reservee_a_une_personne'):
                P.confirmer(self.desk, acteur, prep['id'], {'cle': self.cle(), 'version': prep['version']})
        self.desk.origin_context = 'automatic'
        try:
            with self.assertRaisesRegex(Stop, 'action_reservee_a_une_personne'):
                P.confirmer(self.desk, AVOCAT, prep['id'], {'cle': self.cle(), 'version': prep['version']})
            with patch.object(P, 'personne', lambda desk, a: None):              # garde retirée : un automatisme produirait le paquet
                out = P.confirmer(self.desk, AVOCAT, prep['id'], {'cle': self.cle(), 'version': prep['version']})
            self.assertEqual(out['etat'], 'confirme')
        finally:
            self.desk.origin_context = None

    def test_G03_assistant_ne_partage_pas_routes_et_lectures_sans_ecriture(self):
        doc, ver = self.livrable()
        corps = {'cle': self.cle(), 'destinataire': CLIENT_MAIL, 'expiration': self.expiration(), 'ressources': ['%s|%s' % (doc, ver)], 'permissions.lire': True}
        self.assertEqual(self.http(P, 'dossier/%s/preparer' % MID, corps, who=ASSISTANT)['status'], '403 Forbidden')
        out = self.http(P, 'dossier/%s/preparer' % MID, corps)
        self.assertEqual(out['status'], '200 OK', out['json'])
        self.assertEqual(out['json']['permissions'], {'lire': True, 'telecharger': False, 'deposer': False, 'repondre': False})
        self.assertIn(('Cache-Control', 'private, no-store'), out['headers'])
        avant = self.desk.db.total_changes
        self.assertEqual(self.http(P, 'dossier/' + MID)['status'], '200 OK')
        self.assertEqual(self.desk.db.total_changes, avant)                        # R05 : aucune écriture pendant une lecture
        rejoue = self.http(P, 'dossier/%s/preparer' % MID, corps)
        self.assertEqual(rejoue['json']['id'], out['json']['id'])                   # même clé : même reçu
        self.assertEqual(self.http(P, 'dossier/%s/preparer' % MID, dict(corps, expiration=self.expiration(20)))['status'], '409 Conflict')

    def test_G03_aucun_jeton_en_clair_dans_la_base_ni_dans_l_export(self):
        from agent import export5688
        prep, _, _ = self.partage()
        _, _, man = self.confirmer(prep)
        jeton = man['jeton_retour']
        for (table,) in self.desk.db.execute("SELECT name FROM sqlite_master WHERE type='table' AND name LIKE 'partages_client%'").fetchall():
            for row in self.desk.db.execute('SELECT * FROM %s' % table):
                self.assertNotIn(jeton, json.dumps([str(x) for x in row]), table)
        self.assertTrue(export5688._secrete('secret_hash'))
        from agent import export56108
        declarees = {t for _, _, t, _, _ in export56108.EXPORT} | {t for t, _ in export56108.EXPORT_EXCLUES}
        self.assertTrue(set(P.TABLES) <= declarees)


# ============================================================================================== adaptateur Nextcloud (facultatif)
class Nextcloud(PartageBase):
    def test_G03_sans_serveur_le_paquet_suffit_et_nextcloud_reste_facultatif(self):
        doc, ver, _ = self.livrable_fichier()
        with self.assertRaisesRegex(Stop, 'partage_nextcloud_desactive'):
            self.partage(ressources=(doc, ver), validations=[], canal='nextcloud')
        prep, _, _ = self.partage(ressources=(doc, ver), validations=[])
        out, _, _ = self.confirmer(prep)
        self.assertEqual(out['canal'], 'paquet')
        self.assertNotIn('nuage.example.test', json.dumps(out))                     # aucune URL fictive

    def test_G03_lien_nextcloud_lecture_seule_puis_revocation_relue(self):
        rem.enregistrer_reglages(self.desk, ADMIN, {'actif': True, 'lien_public': True})
        doc, ver, _ = self.livrable_fichier()
        sdoc, sver = self.livrable()
        with self.assertRaisesRegex(Stop, 'nextcloud_fichier_requis'):
            self.partage(ressources=(sdoc, sver), validations=[], canal='nextcloud')
        prep, _, _ = self.partage(ressources=(doc, ver), validations=[], canal='nextcloud')
        out = P.confirmer(self.desk, AVOCAT, prep['id'], {'cle': self.cle(), 'version': prep['version']}, ocs=rem.PartagesOCS(self.ocs))
        self.assertEqual(out['etat'], 'confirme')
        remise = rem.vue_remise(self.desk, AVOCAT, out['remise_id'])
        self.assertEqual(remise['etat'], 'partage_actif')
        self.assertEqual(self.ocs.vue_destinataire(remise['share_id']), ['01 Conclusions DURAND.docx'])
        P.revoquer(self.desk, AVOCAT, prep['id'], {'cle': self.cle(), 'version': out['version']}, ocs=rem.PartagesOCS(self.ocs))
        self.assertIsNone(self.ocs.vue_destinataire(remise['share_id']))


# ============================================================================================== bloc du dossier
class Ecran(PartageBase):
    def test_G03_bloc_du_dossier_dans_les_correspondances(self):
        self.partage()
        html = P.bloc_dossier(self.desk, '/p', {'id': MID}, AVOCAT)
        self.assertIn('Partager avec le client', html)
        self.assertIn('Préparer l’aperçu', html)
        self.assertIn('ne peut pas être retirée à distance', html)
        self.assertNotIn('Préparer l’aperçu', P.bloc_dossier(self.desk, '/p', {'id': MID}, ASSISTANT))
        from agent import dossier5679
        self.assertIn('partage_client56109.bloc_dossier', Path(dossier5679.__file__).read_text(encoding='utf-8'))



class FichierHorsPartageActif(PartageBase):
    """C1 (relecture de l'intégrateur) : un fichier n'entre en quarantaine que pour un partage confirmé, non expiré, non révoqué."""

    def test_G03_C1_recevoir_refuse_partage_non_confirme_expire_ou_revoque(self):
        fichier = {'nom': 'kbis.pdf', 'contenu_b64': base64.b64encode(b'%PDF-1.4 kbis').decode()}
        prep, _, _ = self.partage()
        codes = []
        with self.assertRaises(Stop) as ex:
            P.recevoir_fichier(self.desk, AVOCAT, prep['id'], {'fichier': fichier})               # pas encore confirmé
        codes.append(str(ex.exception))
        self.confirmer(prep)
        self.desk.db.execute("UPDATE partages_client56109 SET expire_le='2020-01-01T00:00:00+00:00' WHERE id=?", (prep['id'],))
        self.desk.db.commit()
        with self.assertRaises(Stop) as ex:
            P.recevoir_fichier(self.desk, AVOCAT, prep['id'], {'fichier': fichier})               # expiré
        codes.append(str(ex.exception))
        p2, _, _ = self.partage()
        self.confirmer(p2)
        v = P.vue(self.desk, AVOCAT, p2['id'])
        P.revoquer(self.desk, AVOCAT, p2['id'], {'cle': self.cle(), 'version': v['version']})
        with self.assertRaises(Stop) as ex:
            P.recevoir_fichier(self.desk, AVOCAT, p2['id'], {'fichier': fichier})                 # révoqué
        codes.append(str(ex.exception))
        self.assertEqual(len(set(codes)), 1, codes)                                               # même réponse neutre
        self.assertEqual(self.desk.db.execute('SELECT COUNT(*) FROM partages_client_quarantaine56109').fetchone()[0], 0)
        p3, _, _ = self.partage()
        self.confirmer(p3)
        out = P.recevoir_fichier(self.desk, AVOCAT, p3['id'], {'fichier': fichier})               # partage actif : accepté en quarantaine
        self.assertEqual(out['quarantaine'][0]['etat'], 'quarantaine')

if __name__ == '__main__':
    unittest.main()
