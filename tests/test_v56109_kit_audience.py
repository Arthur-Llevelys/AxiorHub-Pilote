"""5.6.109, lot G2 (G08) : kit d'audience hors ligne depuis le dossier.

Données fictives (DURAND, ZORVAL, DOS-001, RG 24/01234, Valbrune, example.test) ; stockage local temporaire ; tout appel réseau non simulé fait
échouer l'essai et, pour l'ouverture « hors ligne », toute ouverture de connexion (``socket``) est interdite pendant l'essai. Le VRAI poste
déconnecté (réseau coupé physiquement, lecteur du poste) n'est pas qualifié ici. Le garde-fou « altération du kit détectée » et « droit relu à
la reconnexion » ont chacun un essai qui échouerait s'ils étaient retirés.
"""
import hashlib
import io
import json
from pathlib import Path
import socket
import unittest
from unittest.mock import patch
import zipfile

from test_v56109_partage_client import Base
from test_v5697_gestion import ASSISTANT, AUTRE, AVOCAT, MID
from agent import acces5697, audiences5688 as au, kit_audience56109 as K, membres5650, partage_client56109 as P
from agent.common import Stop
import garde_sqlite56109

ROOT = Path(__file__).resolve().parent.parent
PHRASE = 'phrase secrète fictive DURAND 2026'


def reseau_coupe(*args, **kwargs):
    raise AssertionError('connexion réseau tentée pendant une ouverture hors ligne')


class KitBase(Base):
    def setUp(self):
        super().setUp()
        self.hid = au.save(self.desk, {'matter': MID, 'type': 'plaidoiries', 'start': '2026-12-15T09:30', 'location': 'Tribunal judiciaire de Valbrune',
                                       'notes': 'Notes du cabinet avant audience.'}, AVOCAT[0])['id']
        self.conclusions = self.livrable(titre='Conclusions récapitulatives DURAND', approuver=False)
        self.bordereau = self.livrable(titre='Bordereau de communication de pièces')
        doc, ver, self.chemin_piece = self.livrable_fichier('Dossiers/DURAND/Pieces/Piece 1 - contrat.pdf', b'%PDF-1.4 contrat fictif ZORVAL')
        self.piece = (doc, ver)

    def kit(self, obligatoires=None, complementaires=(), phrase=PHRASE, who=AVOCAT, **extra):
        obligatoires = obligatoires if obligatoires is not None else [self.conclusions, self.bordereau, self.piece]
        return K.preparer(self.desk, who, MID, {'audience': self.hid, 'phrase': phrase, 'obligatoires': ['%s|%s' % x for x in obligatoires],
                                               'complementaires': ['%s|%s' % x for x in complementaires], **extra}, self.cle())

    def blob(self, k):
        return (Path(self.f.c['state_dir']) / 'kits56109' / (k['id'] + '.axhkit')).read_bytes()


class Kit(KitBase):
    def test_G08_kit_rouvert_reseau_coupe_toutes_les_pieces_lisibles(self):
        k = self.kit()
        self.assertEqual(k['etat'], 'pret')
        self.assertEqual(k['couverture'], {'choisis': 3, 'presents': 3, 'obligatoires_manquants': []})
        blob = self.blob(k)
        with patch.object(socket.socket, 'connect', reseau_coupe), patch('socket.create_connection', reseau_coupe):
            ouvert = K.ouvrir(blob, PHRASE)                                        # aucune base, aucun réseau, aucun modèle
            sur_le_poste = K.ouvrir_kit(self.desk, AVOCAT, k['id'], PHRASE)
        self.assertTrue(ouvert['complet'])
        self.assertEqual(len(ouvert['pieces']), 3)
        brut = (self.root / self.chemin_piece.lstrip('/')).read_bytes()
        rang = next(e['rang'] for e in ouvert['manifeste']['elements'] if e['document_id'] == self.piece[0])
        self.assertEqual(ouvert['pieces'][rang], brut)
        for el in ouvert['manifeste']['elements']:
            self.assertEqual(hashlib.sha256(ouvert['pieces'][el['rang']]).hexdigest(), el['sha256'])
            self.assertTrue(el['version_id'] and el['document_id'])
        self.assertEqual(sur_le_poste['audience']['id'], self.hid)
        self.assertEqual(sur_le_poste['notes']['texte'], 'Notes du cabinet avant audience.')
        self.assertIn('ne peut pas être retirée à distance', sur_le_poste['limite'])
        raw, nom = K.lire_piece(self.desk, AVOCAT, k['id'], PHRASE, rang)
        self.assertEqual((raw, nom), (brut, '%02d Piece 1 - contrat.pdf' % rang))

    def test_G08_alteration_detectee_contenant_manifeste_et_piece(self):
        k = self.kit()
        blob = self.blob(k)
        abime = bytearray(blob)
        abime[-20] ^= 0x01
        with self.assertRaisesRegex(Stop, 'kit_altere'):
            K.ouvrir(bytes(abime), PHRASE)                                          # un octet du contenant
        with self.assertRaisesRegex(Stop, 'kit_altere'):
            K.ouvrir(blob, 'mauvaise phrase secrète, assez longue')
        tete, contenu = K.desceller(blob, PHRASE)
        z = zipfile.ZipFile(io.BytesIO(contenu))
        manifeste = json.loads(z.read('manifeste.json'))
        # pièce remplacée, manifeste inchangé, contenant rescellé avec la bonne phrase : la pièce ne correspond plus au manifeste
        tampon = io.BytesIO()
        with zipfile.ZipFile(tampon, 'w') as sortie:
            sortie.writestr('manifeste.json', z.read('manifeste.json'))
            for el in manifeste['elements']:
                sortie.writestr(el['fichier'], b'%PDF-1.4 piece substituee' if el['document_id'] == self.piece[0] else z.read(el['fichier']))
        with self.assertRaisesRegex(Stop, 'piece_alteree'):
            K.ouvrir(K.sceller(tampon.getvalue(), PHRASE, {'format': K.FORMAT, 'kit_id': k['id'], 'manifeste_sha256': tete['manifeste_sha256']}), PHRASE)
        # manifeste remplacé (empreintes recalculées) et rescellé : l'en-tête ne correspond plus, et le poste refuse un contenant inconnu
        faux = dict(manifeste, elements=[dict(el, titre='Pièce substituée') for el in manifeste['elements']])
        sha_faux = hashlib.sha256(P.canon(faux).encode()).hexdigest()
        tampon = io.BytesIO()
        with zipfile.ZipFile(tampon, 'w') as sortie:
            sortie.writestr('manifeste.json', json.dumps(faux))
            for el in manifeste['elements']:
                sortie.writestr(el['fichier'], z.read(el['fichier']))
        forge = K.sceller(tampon.getvalue(), PHRASE, {'format': K.FORMAT, 'kit_id': k['id'], 'manifeste_sha256': sha_faux})
        self.assertTrue(K.ouvrir(forge, PHRASE)['complet'])                       # cohérent en lui-même…
        (Path(self.f.c['state_dir']) / 'kits56109' / (k['id'] + '.axhkit')).write_bytes(forge)
        with self.assertRaisesRegex(Stop, 'kit_altere'):
            K.ouvrir_kit(self.desk, AVOCAT, k['id'], PHRASE)                        # … mais ce n'est pas le kit enregistré
        with patch.object(K, 'conteneur', lambda desk, a, kid: (self.desk.db.execute('SELECT * FROM kits_audience56109 WHERE id=?', (kid,)).fetchone(), forge)):
            with self.assertRaisesRegex(Stop, 'kit_altere'):
                K.ouvrir_kit(self.desk, AVOCAT, k['id'], PHRASE)                    # empreinte du manifeste enregistrée : seconde garde

    def test_G08_element_manquant_kit_incomplet_jamais_complet(self):
        (self.root / self.chemin_piece.lstrip('/')).write_bytes(b'%PDF-1.4 contrat remanie apres le choix')
        k = self.kit()
        self.assertEqual(k['etat'], 'incomplet')
        manquant = next(e for e in k['elements'] if e['document_id'] == self.piece[0])
        self.assertEqual((manquant['present'], manquant['motif']), (0, 'version_modifiee_depuis_l_approbation'))
        ouvert = K.ouvrir(self.blob(k), PHRASE)
        self.assertFalse(ouvert['complet'])
        self.assertEqual([e['document_id'] for e in ouvert['manquants']], [self.piece[0]])
        complementaire = self.kit(obligatoires=[self.conclusions], complementaires=[self.piece])
        self.assertEqual(complementaire['etat'], 'pret')                           # un complément absent est nommé, sans bloquer

    def test_G08_aucune_copie_de_travail_concurrente_ni_base_copiee(self):
        avant_arbre = self.arbre()
        avant_registre = self.desk.db.execute('SELECT COUNT(*) FROM prepared_items5689').fetchone()[0]
        k = self.kit()
        self.assertEqual(self.arbre(), avant_arbre)                                 # rien dans le dossier ni dans le stockage du cabinet
        self.assertEqual(self.desk.db.execute('SELECT COUNT(*) FROM prepared_items5689').fetchone()[0], avant_registre)
        ouvert = K.ouvrir(self.blob(k), PHRASE)
        self.assertFalse([n for n in zipfile.ZipFile(io.BytesIO(K.desceller(self.blob(k), PHRASE)[1])).namelist() if n.endswith(('.sqlite3', '.sqlite', '.db'))])
        self.assertEqual(set(ouvert['manifeste']), {'format', 'kit_id', 'cabinet', 'dossier', 'audience', 'cree_par', 'cree_le', 'version', 'expire_le',
                                                     'derniere_synchronisation', 'elements', 'notes', 'couverture', 'avertissements'})
        for nom in ('kit_audience56109', 'partage_client56109', 'appels56109', 'remplacements56109'):
            source = (ROOT / 'agent' / (nom + '.py')).read_text(encoding='utf-8')
            self.assertEqual(garde_sqlite56109.ouvertures_directes(source, nom), [], nom)

    def test_G08_conflit_de_notes_preserve_a_la_reconnexion(self):
        k = self.kit()
        K.ajouter_note(self.desk, AVOCAT, k['id'], {'texte': 'Affaire retenue, délibéré au 15 janvier 2027.'})
        au.ajouter_note(self.desk, self.hid, 'Le greffe a appelé : salle changée.', 'assistant@example.test')   # le cabinet modifie entre-temps
        v = K.reconnecter(self.desk, AVOCAT, k['id'])
        note = v['notes'][0]
        self.assertEqual(note['etat'], 'conflit')
        K.publier_note(self.desk, AVOCAT, k['id'], note['id'], {'version': note['version']})
        notes = au.get(self.desk, self.hid)['notes']
        self.assertIn('Notes du cabinet avant audience.', notes)                   # rien n'est écrasé
        self.assertIn('Le greffe a appelé : salle changée.', notes)
        self.assertIn('Note concurrente prise hors ligne', notes)
        self.assertIn('délibéré au 15 janvier 2027', notes)
        k2 = self.kit()
        K.ajouter_note(self.desk, AVOCAT, k2['id'], {'texte': 'Sans concurrence.'})
        self.assertEqual(K.reconnecter(self.desk, AVOCAT, k2['id'])['notes'][0]['etat'], 'a_publier')

    def test_G08_droit_relu_a_la_reconnexion_et_mutation(self):
        k = self.kit()
        K.ajouter_note(self.desk, AVOCAT, k['id'], {'texte': 'Note confidentielle après audience.'})
        membres5650.set_reserve(self.f.c['state_dir'], MID, ['autre.collaborateur@example.test'])   # le dossier est réservé pendant le déplacement
        with self.assertRaisesRegex(Stop, 'droits_retires'):
            K.reconnecter(self.desk, AVOCAT, k['id'])
        etat = self.desk.db.execute('SELECT etat FROM kits_audience_notes56109 WHERE kit_id=?', (k['id'],)).fetchone()[0]
        self.assertEqual(etat, 'bloquee')                                           # conservée sur le poste, jamais publiée
        membres5650.set_reserve(self.f.c['state_dir'], MID, [])
        self.assertNotIn('Note confidentielle', au.get(self.desk, self.hid)['notes'])
        membres5650.set_reserve(self.f.c['state_dir'], MID, ['autre.collaborateur@example.test'])
        with patch.object(acces5697, 'exiger', lambda *a, **kw: acces5697.acteur_de(a[1])):   # garde retirée : la reconnexion passerait
            v = K.reconnecter(self.desk, AVOCAT, k['id'])
        self.assertEqual(v['notes'][0]['etat'], 'a_publier')

    def test_G08_protection_exigee_aucun_repli_en_clair(self):
        zone = Path(self.f.c['state_dir']) / 'kits56109'
        avant = sorted(zone.glob('*')) if zone.exists() else []
        with self.assertRaisesRegex(Stop, 'phrase_trop_courte'):
            self.kit(phrase='court')
        with patch.object(K, '_cle_phrase', side_effect=Stop('protection_indisponible')):
            with self.assertRaisesRegex(Stop, 'protection_indisponible'):
                self.kit()
        self.assertEqual(sorted(zone.glob('*')) if zone.exists() else [], avant)  # aucun fichier, ni chiffré ni en clair
        self.assertEqual(self.desk.db.execute('SELECT COUNT(*) FROM kits_audience56109').fetchone()[0], 0)
        k = self.kit()
        self.assertNotIn(b'%PDF-1.4 contrat fictif ZORVAL', self.blob(k))        # le contenu n'apparaît pas en clair
        self.assertNotIn(PHRASE, json.dumps([list(map(str, r)) for r in self.desk.db.execute('SELECT * FROM kits_audience56109')]))

    def test_G08_versions_nouvelles_signalees_et_expiration(self):
        k = self.kit()
        self.nouvelle_revision(self.bordereau[0], 'Bordereau complété')
        v = K.vue(self.desk, AVOCAT, k['id'])
        self.assertEqual([x['titre'] for x in v['versions_nouvelles']], ['Bordereau de communication de pièces'])
        with self.assertRaisesRegex(Stop, 'kit_expire'):
            K.ouvrir(self.blob(k), PHRASE, quand='2030-01-01T00:00:00+00:00')

    def test_G08_routes_droits_et_bloc_du_dossier(self):
        k = self.kit()
        out = self.http(K, '%s/piece' % k['id'], {'phrase': PHRASE, 'rang': 1})
        self.assertEqual(out['status'], '200 OK', out['json'])
        self.assertIn('download', out['json'])
        mauvais = self.http(K, '%s/ouvrir' % k['id'], {'phrase': 'phrase incorrecte mais longue'})
        self.assertEqual((mauvais['status'], mauvais['json']['error']), ('409 Conflict', 'kit_altere'))
        avant = self.desk.db.total_changes
        tele = self.http(K, '%s/telecharger' % k['id'])
        self.assertEqual(tele['body'], self.blob(k))
        self.assertEqual(self.http(K, '%s/etat' % k['id'])['status'], '200 OK')
        self.assertEqual(self.desk.db.total_changes, avant)                        # R05
        membres5650.set_reserve(self.f.c['state_dir'], MID, [AVOCAT[0]])
        self.assertEqual(self.http(K, '%s/etat' % k['id'], who=AUTRE)['status'], '404 Not Found')
        membres5650.set_reserve(self.f.c['state_dir'], MID, [])
        html = K.bloc_dossier(self.desk, '/p', {'id': MID}, ASSISTANT)
        self.assertIn('Préparer mon audience', html)
        self.assertIn('Instantané du', html)



class LecturesSansEcriture(KitBase):
    """H1 (relecture de l'intégrateur) : aucune DDL, aucune écriture, aucun commit pendant une lecture des quatre modules (routes GET et
    blocs rendus dans la page du dossier ou la fiche contact) ; empreinte de la base inchangée."""

    def empreinte(self):
        return hashlib.sha256(chr(10).join(self.desk.db.iterdump()).encode('utf-8')).hexdigest()

    def test_H1_aucune_ecriture_ni_ddl_pendant_les_lectures(self):
        from agent import appels56109 as AP, crm5636, crm5676, remplacements56109 as R
        crm5636.schema(self.desk)
        confrere = crm5636.save(self.desk, {'kind': 'personne', 'category': 'confrere', 'name': 'KERVILAC', 'email': 'kervilac@example.test'})
        rp = R.creer(self.desk, AVOCAT, {'audience': self.hid, 'role': 'substitution', 'confrere': confrere['id']}, self.cle())
        k = self.kit()
        ap = AP.consigner(self.desk, AVOCAT, {'sens': 'entrant', 'texte': 'Appel du client.', 'dossier': MID, 'contact': confrere['id']}, self.cle())
        doc, ver = self.livrable(titre='Version partagée')
        pc = P.preparer(self.desk, AVOCAT, MID, {'destinataire': 'durand@example.test', 'expiration': self.expiration(),
                                                 'ressources': [{'document_id': doc, 'version_id': ver}]}, self.cle())
        lectures = [(P, 'dossier/' + MID), (P, pc['id']), (AP, 'dossier/' + MID), (AP, 'contact/' + confrere['id']), (AP, ap['id']),
                    (R, 'audience/' + self.hid), (R, 'dossier/' + MID), (R, rp['id']), (K, 'dossier/' + MID), (K, k['id'] + '/etat'),
                    (K, k['id'] + '/telecharger')]
        blocs = [lambda: P.bloc_dossier(self.desk, '/p', {'id': MID}, AVOCAT), lambda: AP.bloc_dossier(self.desk, '/p', {'id': MID}, AVOCAT),
                 lambda: R.bloc_dossier(self.desk, '/p', {'id': MID}, AVOCAT), lambda: K.bloc_dossier(self.desk, '/p', {'id': MID}, AVOCAT),
                 lambda: crm5676.bloc_contact(self.desk, '/p', confrere)]
        ecrit = []
        mots = ('CREATE', 'INSERT', 'UPDATE', 'DELETE', 'ALTER', 'DROP', 'REPLACE', 'COMMIT', 'BEGIN')
        avant = self.empreinte()
        self.desk.db.set_trace_callback(lambda sql: ecrit.append(sql) if sql.lstrip().split(' ', 1)[0].upper() in mots else None)
        try:
            for module, route in lectures:
                out = module.api({'HTTP_X_AXIORHUB_USER': AVOCAT[0], 'HTTP_X_AXIORHUB_ROLE': AVOCAT[1], 'REQUEST_METHOD': 'GET'}, self.desk,
                                 {'origin': 'https://cabinet.example.test', 'csrf': 'jeton'}, '/p', module.PREFIXE + route, {}, 'GET')
                self.assertEqual(out['status'], '200 OK', (route, out.get('body')))
            for bloc in blocs:
                self.assertTrue(bloc())
        finally:
            self.desk.db.set_trace_callback(None)
        self.assertEqual(ecrit, [])
        self.assertEqual(self.empreinte(), avant)

if __name__ == '__main__':
    unittest.main()
