"""5.6.101 (lot R · R01-R06, V101-09, V101-10) : « À relire » unique — brouillons Gmail, documents produits, notes de l'IA.

Données entièrement fictives (DOS-001, DOS-RES, DURAND, MARTIN, ZORVAL, adresses example.test) ; faux stockage (``test_v5689_livrables``),
faux Gmail au niveau de ``google5697.appeler`` (aucun appel réel à Google), aucun modèle, aucun réseau. Les dates viennent du jour du cabinet."""
from datetime import timedelta
import hashlib
import io
import json
from pathlib import Path
import re
import shutil
import sqlite3
import subprocess
import tempfile
import time
import unittest
from unittest.mock import patch

import dates_relatives as dr
import test_v5689_livrables as t89
from agent import google5697 as gg, gmail5697 as gm, livrables5689 as liv, membres5650, propositions5689 as patches
from agent import relecture56100 as rl, relecture56101 as r1
from agent.common import private_json

MID = t89.MID
RESERVE = 'DOS-RES'
ADMIN = ('cabinet', 'administrateur')
TITULAIRE = ('titulaire@example.test', 'avocat')
AVOCAT_B = ('avocat-b@example.test', 'avocat')
ASSISTANT = ('assistant@example.test', 'assistant')
ORIGINE, JETON = 'https://cabinet.example.test', 'jeton-csrf-fictif'
RACINE = Path(__file__).resolve().parents[1]
PORTEES = ' '.join((gg.PORTEES['gmail.lecture'], gg.PORTEES['gmail.brouillons']))


class FauxGmail:
    """API Gmail simulée (brouillons seulement) : ``drafts.create`` puis relecture ``format=raw``. ``panne`` : réponse perdue (503)."""

    def __init__(self):
        self.brouillons, self.appels, self.panne, self.envois = {}, [], None, []

    def __call__(self, desk, conn_id, methode, url, corps=None, capacite='', entetes=None):
        self.appels.append((methode, url.split('/users/me', 1)[-1]))
        if '/send' in url or '/messages/send' in url:
            self.envois.append(url)
            raise AssertionError('aucun envoi depuis « À relire »')
        if self.panne:
            raise gg.ErreurGoogle(self.panne, int(self.panne.rsplit('_', 1)[-1]))
        if methode == 'POST' and url.endswith('/drafts'):
            ident = 'brouillon-%d' % (len(self.brouillons) + 1)
            self.brouillons[ident] = corps['message']['raw']
            return {'id': ident, 'message': {'id': 'm-' + ident}}
        trouve = re.search(r'/drafts/([^?]+)\?format=raw', url)
        if methode == 'GET' and trouve and trouve.group(1) in self.brouillons:
            return {'id': trouve.group(1), 'message': {'id': 'm-' + trouve.group(1), 'raw': self.brouillons[trouve.group(1)]}}
        raise gg.ErreurGoogle('google_http_404', 404)


class Base(t89.Base):
    def setUp(self):
        super().setUp()
        self.addCleanup(membres5650.clear)
        matters = json.loads(Path(self.f.matter_file).read_text(encoding='utf-8'))
        matters.append({'id': RESERVE, 'client_name': 'Client ZORVAL', 'path': '/Dossiers/ZORVAL', 'references': [RESERVE], 'aliases': [], 'correspondents': []})
        private_json(self.f.matter_file, matters)
        membres5650.set_reserve(self.f.c['state_dir'], RESERVE, ['titulaire@example.test'])
        (Path(self.f.c['state_dir']) / 'reports').mkdir(exist_ok=True)
        gm.ensure_schema(self.desk)
        self.gmail = FauxGmail()
        p = patch.object(gg, 'appeler', self.gmail)
        p.start()
        self.addCleanup(p.stop)
        self.t0 = dr.maintenant_cabinet() - timedelta(days=2)
        self.n = 0

    # ------------------------------------------------------------------------------------------------ producteurs fictifs
    def iso(self, minutes):
        return (self.t0 + timedelta(minutes=minutes)).isoformat()

    def proposition(self, n, matter='', sujet='Demande de calendrier'):
        k = '%064x' % (0xdef0000 + n)
        cree = self.iso(n)
        data = {'result': {'body': 'Bonjour,\nProjet de réponse %d.' % n, 'requires_decision': True, 'limits': []}, 'matter': matter, 'key': k,
                'created_at': cree, 'recipients': ['client@example.test'], 'sources': []}
        private_json(Path(self.f.c['state_dir']) / 'reports' / (k + '.json'), {'subject': sujet, 'sender': 'client@example.test', 'matter': matter, 'received_at': cree})
        self.desk.db.execute('INSERT OR REPLACE INTO manual_drafts VALUES(?,?,?)', (k, json.dumps(data), cree))
        self.desk.db.commit()
        rl.projeter_cle(self.desk, k)
        return rl.id_proposition(k, data)

    def document(self, nom='Contrat DURAND', matter=MID, paragraphes=('Contrat entre DURAND et MARTIN.', 'Le prix est de mille euros.', 'Fin du contrat.')):
        self.n += 1
        row = self.demande('req-%d' % self.n, path='/Dossiers/DEMO/PROJETS/%s %d.docx' % (nom, self.n), raw=t89.texte_docx(*paragraphes))
        if matter != MID:
            self.desk.db.execute('UPDATE prepared_items5689 SET matter_id=? WHERE document_id=?', (matter, row['document_id']))
            self.desk.db.commit()
        r1.projeter_document(self.desk, row['document_id'])
        return r1.id_document(row['document_id']), liv.charger(self.desk, row['document_id'])

    def note(self, matter=MID, titre='Conclusions adverses n° 2', propositions=()):
        self.n += 1
        rid = 'dl-%024x' % self.n
        maintenant = self.iso(100 + self.n)
        self.desk.db.execute('INSERT INTO delta56100_resultats(id,cabinet,event_id,dossier,piece_id,version_id,sha256,baseline_id,partie,titre,etat,note,'
                             'propositions,created,updated) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)',
                             (rid, 'cabinet', 'ev-' + rid, matter, 'piece-%d' % self.n, 'v-%d' % self.n, 'a' * 64, 'base-1', 'défendeur', titre, 'a_relire',
                              'Note interne — nouvelles écritures reçues (à relire, rien n’a été appliqué au dossier)\n\nCe qui change : prix contesté.',
                              json.dumps(list(propositions)), maintenant, maintenant))
        self.desk.db.commit()
        r1.projeter_note(self.desk, rid)
        return r1.id_note(rid), rid

    def connexion(self, owner, etat='active'):
        cid = 'cx-' + hashlib.sha256(owner.encode()).hexdigest()[:10]
        gm.regler(self.desk, 'administrateur', True)
        self.desk.db.execute("INSERT OR REPLACE INTO google5697_connexions(id,owner,usage,adresse,etat,portees,generation,cree,maj) VALUES(?,?,?,?,?,?,1,?,?)",
                             (cid, owner, 'gmail', owner.split('@')[0] + '.gmail@example.test', etat, PORTEES, self.iso(0), self.iso(0)))
        self.desk.db.commit()
        return cid

    def gmail_projet(self, owner='titulaire@example.test', objet='Re: Bail commercial Valbrune', texte='Cher Monsieur,\nBien reçu.', matter=''):
        cid = self.connexion(owner)
        self.n += 1
        gid = '%016x' % (0x18c0000000000000 + self.n)
        self.desk.db.execute("INSERT INTO gmail5697_messages(connexion_id,gmail_id,thread_id,de,de_adresse,a,objet,recu,message_id,texte,sens,maj) "
                             "VALUES(?,?,?,?,?,?,?,?,?,?,?,?)", (cid, gid, gid, 'MARTIN <martin@example.test>', 'martin@example.test', owner, objet[4:],
                                                                 self.iso(self.n), '<m%d@example.test>' % self.n, 'Bonjour Maître, voici ma question.', 'recu', self.iso(self.n)))
        pid = 'gp%030d' % self.n
        self.desk.db.execute("INSERT INTO gmail5697_projets(id,connexion_id,owner,generation,matter,source_id,thread_id,a,objet,texte,in_reply_to,message_id,cree,maj,ia_etat) "
                             "VALUES(?,?,?,1,?,?,?,?,?,?,?,?,?,?,?)", (pid, cid, owner, matter, gid, gid, 'martin@example.test', objet, texte, '<m%d@example.test>' % self.n,
                                                                      '<r%d@example.test>' % self.n, self.iso(200 + self.n), self.iso(200 + self.n), 'pret'))
        self.desk.db.commit()
        r1.projeter_gmail(self.desk, pid)
        return r1.id_gmail(pid), pid

    # ------------------------------------------------------------------------------------------------ outils
    def cmd(self, acteur, ident, sous, **data):
        self.n += 1
        data.setdefault('cle', 'cle-lotr-%012d-%04d' % (int(time.time() * 1000) % 10 ** 12, self.n))
        return rl.commande(self.desk, acteur, ident, sous, data)

    def refus(self, f, *args, **kw):
        with self.assertRaises(rl.Refus) as ctx:
            f(*args, **kw)
        return ctx.exception

    def ids(self, acteur, **args):
        return [x['id'] for x in rl.liste(self.desk, acteur, args, '/p')['lignes']]

    def appel(self, nom, methode='GET', data=None, args=None, compte='cabinet', role='administrateur', jeton=JETON):
        from agent import web440
        brut = json.dumps(data or {}).encode()
        env = {'HTTP_ORIGIN': ORIGINE, 'HTTP_X_CSRF_TOKEN': jeton, 'CONTENT_TYPE': 'application/json', 'CONTENT_LENGTH': str(len(brut)),
               'wsgi.input': io.BytesIO(brut), 'HTTP_X_AXIORHUB_USER': compte, 'HTTP_X_AXIORHUB_ROLE': role, 'REQUEST_METHOD': methode}
        out = web440.api(env, self.desk, {'origin': ORIGINE, 'csrf': JETON}, '/p', nom, args or {}, methode)
        return out, json.loads(out['body'])

    def ecritures(self):
        return self.desk.db.total_changes


# ---------------------------------------------------------------------------------------------------- R02 documents produits
class Documents(Base):
    def test_R02_document_liste_apercu_version_exacte_et_valider(self):
        ident, row = self.document()
        self.assertIn(ident, self.ids(TITULAIRE, source='document'))
        self.assertIn(ident, self.ids(TITULAIRE))                                     # « Tout » : même liste que les propositions
        d = rl.detail(self.desk, TITULAIRE, ident, '/p')
        self.assertEqual((d['type'], d['etat'], d['version']['id']), ('document', 'a_relire', row['version_id']))
        self.assertIn('Le prix est de mille euros.', d['corps'])                       # aperçu de la version exacte
        self.assertIn('version 1', d['motif'])
        self.assertEqual(d['actions'][:2], ['valider', 'ecarter'])
        self.assertIn('corriger', d['actions'])
        self.assertTrue(d['ouvrir']['href'])                                          # Ouvrir dans l'éditeur
        recu = self.cmd(TITULAIRE, ident, 'decision', action='valider', revision=d['revision'])
        self.assertEqual(recu['etat'], 'retenu')
        self.assertEqual(liv.charger(self.desk, row['document_id'])['review_state'], 'reviewed')
        self.assertNotIn(ident, self.ids(TITULAIRE, source='document'))
        self.assertEqual(self.nc.ecritures, [])                                       # rien n'est écrit dans le stockage

    def test_R02_ecarter_retire_sans_relire_et_version_perimee_409(self):
        ident, row = self.document()
        d = rl.detail(self.desk, TITULAIRE, ident, '/p')
        self.demande('req-1', path=row['path'], raw=t89.texte_docx('Nouvelle version du contrat.'))   # nouvelle version au même chemin
        e = self.refus(self.cmd, TITULAIRE, ident, 'decision', action='ecarter', revision=d['revision'])
        self.assertEqual((e.status, e.code), (409, 'version_perimee'))
        r1.rattraper_sources(self.desk)
        d2 = rl.detail(self.desk, TITULAIRE, ident, '/p')
        self.assertNotEqual(d2['revision'], d['revision'])
        self.assertEqual(self.cmd(TITULAIRE, ident, 'decision', action='ecarter', revision=d2['revision'])['etat'], 'ecarte')
        self.assertEqual(liv.charger(self.desk, row['document_id'])['lifecycle'], 'archived')

    def test_R02_correction_ia_demandee_puis_conflit_sans_ecrasement(self):
        ident, row = self.document()
        d = rl.detail(self.desk, TITULAIRE, ident, '/p')
        recu = self.cmd(TITULAIRE, ident, 'corriger', revision=d['revision'], instruction='Porter le prix à deux mille euros.', zone='Le prix est de mille euros.')
        self.assertEqual((recu['etat'], recu['revision']), ('correction_demandee', d['revision']))
        self.assertTrue(recu['proposition'])
        demande = self.desk.db.execute('SELECT state,base_version_id,selection FROM document_patches5689 WHERE id=?', (recu['proposition'],)).fetchone()
        self.assertEqual((demande[0], demande[1], demande[2]), ('demandee', row['version_id'], 'Le prix est de mille euros.'))
        self.assertIn(ident, self.ids(TITULAIRE, source='document'))                  # l'élément reste à relire : rien n'est modifié
        # proposition de l'IA (avant/après), puis édition humaine concurrente : 409, le fichier humain reste
        p = patches.proposer(self.desk, row, {'cle': 'cle-prop-lotr-1', 'version_id': row['version_id'], 'sha256': row['sha256'],
                                              'operations': [{'ancre': 'Le prix est de mille euros.', 'texte': 'Le prix est de deux mille euros.', 'motif': 'Consigne'}]},
                             'cabinet', origin='ia')
        s = rl.section(self.desk, TITULAIRE, ident, d['revision'], 'propositions')
        prop = next(x for x in s['propositions'] if x['id'] == p['id'])
        self.assertEqual((prop['comparaison'][0]['avant'], prop['comparaison'][0]['apres']), ('Le prix est de mille euros.', 'Le prix est de deux mille euros.'))
        self.assertEqual(prop['actions'], ['accepter', 'refuser'])
        humain = t89.texte_docx('Contrat entre DURAND et MARTIN.', 'Le prix est de mille cinq cents euros.', 'Fin du contrat.')
        self.nc.add(row['path'], humain)
        e = self.refus(self.cmd, TITULAIRE, ident, 'proposition', revision=d['revision'], proposition=p['id'], decision='accepter')
        self.assertEqual((e.status, e.code), (409, 'conflit_edition_concurrente'))
        self.assertEqual(self.nc.files[row['path']], humain)                          # aucun écrasement
        d2 = rl.detail(self.desk, TITULAIRE, ident, '/p')                            # l'édition humaine est devenue la version courante
        self.assertNotEqual(d2['revision'], d['revision'])
        self.assertIn('mille cinq cents', d2['corps'])

    def test_R02_proposition_appliquee_nouvelle_version_a_relire(self):
        ident, row = self.document()
        d = rl.detail(self.desk, TITULAIRE, ident, '/p')
        p = patches.proposer(self.desk, row, {'cle': 'cle-prop-lotr-2', 'version_id': row['version_id'], 'sha256': row['sha256'],
                                              'operations': [{'ancre': 'Le prix est de mille euros.', 'texte': 'Le prix est de deux mille euros.', 'motif': 'Accord'}]},
                             'cabinet', origin='ia')
        e = self.refus(self.cmd, ASSISTANT, ident, 'proposition', revision=d['revision'], proposition=p['id'], decision='accepter')
        self.assertEqual(e.status, 403)                                               # décision réservée aux avocats (comme Produire)
        recu = self.cmd(TITULAIRE, ident, 'proposition', revision=d['revision'], proposition=p['id'], decision='accepter')
        self.assertEqual(recu['etat'], 'appliquee')
        d2 = rl.detail(self.desk, TITULAIRE, ident, '/p')
        self.assertEqual((d2['revision'], d2['etat']), (recu['revision'], 'a_relire'))
        self.assertIn('deux mille euros', d2['corps'])

    def test_R02_assistant_ne_valide_pas_et_document_reserve_404_neutre(self):
        ident, _row = self.document()
        d = rl.detail(self.desk, ASSISTANT, ident, '/p')
        self.assertNotIn('valider', d['actions'])
        self.assertEqual(self.refus(self.cmd, ASSISTANT, ident, 'decision', action='valider', revision=d['revision']).status, 403)
        cache, _ = self.document('Acte ZORVAL', matter=RESERVE)
        self.assertNotIn(cache, self.ids(AVOCAT_B))
        a = self.refus(rl.detail, self.desk, AVOCAT_B, cache, '/p')
        b = self.refus(rl.detail, self.desk, AVOCAT_B, 'd-' + '0' * 32, '/p')
        self.assertEqual((a.status, a.code), (b.status, b.code))
        self.assertEqual(a.status, 404)
        self.assertIn(cache, self.ids(TITULAIRE))


# ---------------------------------------------------------------------------------------------------- R03 notes de l'IA
class Notes(Base):
    def test_R03_valider_la_note_sans_appliquer_de_proposition(self):
        ident, rid = self.note(propositions=[{'kind': 'fait', 'texte': 'Le prix est contesté.', 'claim_id': 'c1', 'ancres': []}])
        d = rl.detail(self.desk, TITULAIRE, ident, '/p')
        self.assertEqual((d['type'], d['etat']), ('note_ia', 'a_relire'))
        self.assertIn('prix contesté', d['corps'])
        self.assertIn('/contentieux?', d['ouvrir']['href'])
        self.assertEqual(set(d['actions']), {'valider', 'rejeter', 'enregistrer', 'reporter'})
        avant = self.desk.db.execute("SELECT COUNT(*) FROM sqlite_master WHERE name='contentieux_elements5689'").fetchone()[0]
        recu = self.cmd(TITULAIRE, ident, 'decision', action='valider', revision=d['revision'])
        self.assertEqual(recu['etat'], 'retenu')
        r = self.desk.db.execute('SELECT etat,decisions FROM delta56100_resultats WHERE id=?', (rid,)).fetchone()
        self.assertEqual(r[0], 'retenu')
        self.assertEqual(json.loads(r[1])[-1]['elements'], [])                        # aucune proposition appliquée seule
        if avant:
            self.assertEqual(self.desk.db.execute("SELECT COUNT(*) FROM contentieux_elements5689").fetchone()[0], 0)
        self.assertNotIn(ident, self.ids(TITULAIRE, source='note_ia'))

    def test_R03_rejeter_exige_un_motif_et_assistant_refuse(self):
        ident, rid = self.note()
        d = rl.detail(self.desk, TITULAIRE, ident, '/p')
        self.assertEqual(self.refus(self.cmd, TITULAIRE, ident, 'decision', action='rejeter', revision=d['revision']).code, 'motif_requis')
        self.assertEqual(self.refus(self.cmd, ASSISTANT, ident, 'decision', action='rejeter', revision=d['revision'], motif='Hors sujet').status, 403)
        recu = self.cmd(TITULAIRE, ident, 'decision', action='rejeter', revision=d['revision'], motif='Pièce déjà analysée.')
        self.assertEqual(recu['etat'], 'ecarte')
        r = self.desk.db.execute('SELECT etat,decisions FROM delta56100_resultats WHERE id=?', (rid,)).fetchone()
        self.assertEqual((r[0], json.loads(r[1])[-1]['motif']), ('ecarte', 'Pièce déjà analysée.'))

    def test_R03_enregistrer_la_note_et_decision_dans_l_onglet_suivie(self):
        from agent import delta56100
        ident, rid = self.note()
        d = rl.detail(self.desk, TITULAIRE, ident, '/p')
        recu = self.cmd(TITULAIRE, ident, 'enregistrer', revision=d['revision'], corps='Note relue : le prix est contesté.')
        self.assertEqual(recu['etat'], 'enregistre')
        self.assertIn('Note relue', rl.detail(self.desk, TITULAIRE, ident, '/p')['corps'])
        delta56100.decider(self.desk, TITULAIRE, rid, {'action': 'ecarter', 'cle': 'cle-onglet-0001', 'revision': 2})   # décision prise dans l'onglet
        self.assertNotIn(ident, self.ids(TITULAIRE))                                  # la liste suit le producteur


# ---------------------------------------------------------------------------------------------------- R01 Gmail
class Gmail(Base):
    def test_R01_brouillon_gmail_dans_la_liste_de_son_auteur_seulement(self):
        ident, pid = self.gmail_projet()
        page = rl.liste(self.desk, TITULAIRE, {'source': 'gmail'}, '/p')
        self.assertEqual(([x['id'] for x in page['lignes']], page['sources']['gmail']['etat']), ([ident], 'a_jour'))
        self.assertEqual(page['lignes'][0]['type_libelle'], 'Brouillon Gmail')
        self.assertEqual(rl.liste(self.desk, ADMIN, {'source': 'gmail'}, '/p')['lignes'], [])     # projet d'une autre personne : inexistant
        self.assertEqual(self.refus(rl.detail, self.desk, ADMIN, ident, '/p').status, 404)
        self.assertNotIn(ident, self.ids(TITULAIRE))                                  # appel séparé : jamais dans la page locale
        d = rl.detail(self.desk, TITULAIRE, ident, '/p')
        self.assertEqual((d['type'], d['corps'], d['destinataires']['a']), ('gmail', 'Cher Monsieur,\nBien reçu.', ['martin@example.test']))
        self.assertEqual(d['actions'], ['enregistrer', 'retenir', 'ecarter', 'reporter', 'deposer'])
        self.assertNotIn('envoyer', json.dumps(d))
        self.assertIn('voici ma question', rl.section(self.desk, TITULAIRE, ident, d['revision'], 'message')['texte'])

    def test_R01_enregistrer_retenir_puis_deposer_constate_chez_google(self):
        ident, pid = self.gmail_projet()
        d = rl.detail(self.desk, TITULAIRE, ident, '/p')
        recu = self.cmd(TITULAIRE, ident, 'decision', action='retenir', revision=d['revision'], corps='Cher Monsieur,\nBien reçu, merci.')
        self.assertEqual(recu['etat'], 'retenu')
        self.assertEqual(self.gmail.appels, [])                                       # retenir ne dépose rien
        self.assertEqual(gm.projet(self.desk, TITULAIRE[0], pid)['texte'], 'Cher Monsieur,\nBien reçu, merci.')
        d2 = rl.detail(self.desk, TITULAIRE, ident, '/p')
        self.assertEqual((d2['etat'], d2['actions']), ('retenu', ['deposer']))
        c = 'cle-depot-gmail-0001'
        recu = self.cmd(TITULAIRE, ident, 'deposer', revision=d2['revision'], cle=c)
        self.assertEqual(recu['etat'], 'depose')
        self.assertEqual([x[0] for x in self.gmail.appels], ['POST', 'GET'])          # dépôt puis relecture chez Google
        self.assertEqual(gm.projet(self.desk, TITULAIRE[0], pid)['etat'], 'depose')
        self.assertEqual(self.cmd(TITULAIRE, ident, 'deposer', revision=d2['revision'], cle=c), recu)   # rejeu : même reçu
        self.assertEqual(len(self.gmail.brouillons), 1)
        self.assertEqual(self.gmail.envois, [])

    def test_V101_10_gmail_en_panne_depot_a_verifier_jamais_depose(self):
        ident, pid = self.gmail_projet()
        d = rl.detail(self.desk, TITULAIRE, ident, '/p')
        self.gmail.panne = 'google_http_503'
        recu = self.cmd(TITULAIRE, ident, 'deposer', revision=d['revision'])
        self.assertEqual(recu['etat'], 'a_verifier')                                  # jamais « Déposé » sans constat
        self.gmail.panne = None
        self.assertEqual(self.refus(self.cmd, TITULAIRE, ident, 'deposer', revision=d['revision']).status, 409)   # version d'avant le dépôt
        d2 = rl.detail(self.desk, TITULAIRE, ident, '/p')
        self.assertNotIn('deposer', d2['actions'])                                   # dépôt à vérifier : aucun nouveau dépôt proposé
        again = self.cmd(TITULAIRE, ident, 'deposer', revision=d2['revision'])       # autre clé : intention « à vérifier » bloquante
        self.assertEqual(again['etat'], 'a_verifier')
        self.assertEqual(self.gmail.brouillons, {})
        self.assertNotEqual(self.desk.db.execute('SELECT etat FROM file56100_items WHERE id=?', (ident,)).fetchone()[0], 'depose')

    def test_V101_10_connexion_a_renouveler_ou_controle_en_panne_masque_et_dit(self):
        self.proposition(1, sujet='Proposition locale')
        ident, _pid = self.gmail_projet()
        self.connexion('titulaire@example.test', etat='a_reconnecter')
        page = rl.liste(self.desk, TITULAIRE, {'source': 'gmail'}, '/p')
        self.assertEqual((page['lignes'], page['sources']['gmail']['etat']), ([], 'indisponible'))
        self.assertIn('renouveler', page['sources']['gmail']['raison'])
        self.connexion('titulaire@example.test')
        with patch.object(rl, '_masques', side_effect=rl.Refus(503, 'controle_indisponible')):
            panne = rl.liste(self.desk, TITULAIRE, {'source': 'gmail'}, '/p')
        self.assertEqual((panne['lignes'], panne['sources']['gmail']['etat']), ([], 'indisponible'))
        self.assertIn('masqués', panne['sources']['gmail']['raison'])
        self.assertEqual(rl.liste(self.desk, TITULAIRE, {}, '/p')['total'], 1)       # la liste locale reste utilisable

    def test_V101_10_droit_retire_entre_liste_et_action_404_sans_fuite(self):
        ident, pid = self.gmail_projet(owner='avocat-b@example.test', matter=MID)
        d = rl.detail(self.desk, AVOCAT_B, ident, '/p')
        membres5650.set_reserve(self.f.c['state_dir'], MID, ['titulaire@example.test'])   # droit retiré après l'affichage
        e = self.refus(self.cmd, AVOCAT_B, ident, 'decision', action='ecarter', revision=d['revision'])
        absent = self.refus(self.cmd, AVOCAT_B, 'g-' + '1' * 32, 'decision', action='ecarter', revision=d['revision'])
        self.assertEqual((e.status, e.code), (absent.status, absent.code))
        self.assertEqual(e.status, 404)
        self.assertEqual(gm.projet(self.desk, AVOCAT_B[0], pid)['etat'], 'local')    # aucun effet
        out, corps = self.appel('m56100/relecture/' + ident, compte=AVOCAT_B[0], role='avocat')
        self.assertTrue(out['status'].startswith('404'))
        self.assertNotIn('Valbrune', json.dumps(corps, ensure_ascii=False))


# ---------------------------------------------------------------------------------------------------- R04, R05, R06 et recette
class Commun(Base):
    def test_R04_idempotence_meme_recu_ou_409_et_suivant(self):
        ident, _row = self.document()
        d = rl.detail(self.desk, TITULAIRE, ident, '/p')
        c = 'cle-idem-doc-000001'
        recu = self.cmd(TITULAIRE, ident, 'decision', action='valider', revision=d['revision'], cle=c)
        self.assertEqual(self.cmd(TITULAIRE, ident, 'decision', action='valider', revision=d['revision'], cle=c), recu)
        self.assertEqual(self.refus(self.cmd, TITULAIRE, ident, 'decision', action='ecarter', revision=d['revision'], cle=c).code, 'cle_reutilisee')

    def test_R04_mutation_garde_retiree_detectee(self):
        """Contre-épreuve : sans le garde (mutation en mémoire), un document et une note d'un dossier réservé apparaîtraient à l'avocat B."""
        doc, _ = self.document('Acte ZORVAL', matter=RESERVE)
        note, _ = self.note(matter=RESERVE)
        with patch.object(rl, 'visibles', lambda desk, acteur, lignes: {x['id'] for x in lignes}), \
                patch('agent.acces5697.Perimetre.sql', lambda self, col: ('1=1', ())):
            fuite = self.ids(AVOCAT_B)
        self.assertTrue({doc, note} <= set(fuite))
        self.assertFalse({doc, note} & set(self.ids(AVOCAT_B)))
        self.assertEqual(rl.liste(self.desk, AVOCAT_B, {}, '/p')['total'], 0)        # totaux après les droits

    def test_R05_filtres_par_type_et_memorises_par_personne_sans_ecriture_au_get(self):
        p = self.proposition(1)
        doc, _ = self.document()
        note, _ = self.note()
        self.assertEqual(set(self.ids(TITULAIRE)), {p, doc, note})
        self.assertEqual(self.ids(TITULAIRE, source='courriel'), [p])
        self.assertEqual(self.ids(TITULAIRE, source='document'), [doc])
        self.assertEqual(self.ids(TITULAIRE, source='note_ia'), [note])
        avant = self.ecritures()
        rl.page_html(self.desk, '/p', TITULAIRE, {})
        rl.detail(self.desk, TITULAIRE, doc, '/p')
        rl.detail(self.desk, TITULAIRE, note, '/p')
        self.assertEqual(self.ecritures(), avant)                                     # aucune écriture pendant une lecture
        out, corps = self.appel('m56100/relecture/filtres', 'POST', {'source': 'document'}, compte=TITULAIRE[0], role='avocat')
        self.assertEqual((out['status'], corps['source']), ('200 OK', 'document'))
        self.assertIn(('Cache-Control', 'private, no-store'), out['headers'])
        html = rl.page_html(self.desk, '/p', TITULAIRE, {})
        self.assertIn('data-source="document"', html)
        self.assertIn('data-source="tous"', rl.page_html(self.desk, '/p', AVOCAT_B, {}))   # préférence propre à la personne
        out, _ = self.appel('m56100/relecture/filtres', 'POST', {'source': 'tout-et-rien'}, compte=TITULAIRE[0], role='avocat')
        self.assertTrue(out['status'].startswith('422'))
        out, _ = self.appel('m56100/relecture/filtres', 'GET', compte=TITULAIRE[0], role='avocat')
        self.assertTrue(out['status'].startswith('404'))

    def test_R06_facturation_et_decisions_de_gestion_hors_de_a_relire(self):
        raw = t89.texte_docx('Facture fictive DURAND.')
        path = '/Dossiers/DEMO/FACTURATION/Facture 1.docx'
        self.nc.add(path, raw)
        res = liv.reserve(self.desk, 'facturation_essai', 'f1', 1, matter=MID, kind='autre_document', title='Facture fictive', path=path,
                          sha256=hashlib.sha256(raw).hexdigest(), right='voir_honoraires')
        liv.constater(self.desk, res, self.nc, path, raw)
        self.assertEqual(liv.charger(self.desk, res['document_id'])['required_right'], 'voir_honoraires')
        r1.rattraper_sources(self.desk)
        self.assertNotIn(r1.id_document(res['document_id']), self.ids(ADMIN))
        _note, rid = self.note()
        from agent import delta56100
        delta56100._diffuser(self.desk, rid)                                          # la note entre aussi au registre des livrables
        r1.rattraper_sources(self.desk)
        types = [x['type'] for x in rl.liste(self.desk, ADMIN, {}, '/p')['lignes']]
        self.assertEqual(types.count('note_ia'), 1)
        self.assertEqual(types.count('document'), 0)                                  # pas de doublon de la note en « document »

    def test_V101_09_dix_elements_quatre_types_traites_a_la_suite(self):
        props = [self.proposition(n, sujet='Proposition %d' % n) for n in range(3)]
        docs = [self.document('Contrat %d' % n)[0] for n in range(3)]
        notes = [self.note(titre='Pièce %d' % n)[0] for n in range(2)]
        gmails = [self.gmail_projet(objet='Re: Objet %d' % n)[0] for n in range(2)]
        actions = {props[0]: ('decision', {'action': 'retenir', 'corps': 'Texte relu.'}), props[1]: ('decision', {'action': 'ecarter'}),
                   props[2]: ('decision', {'action': 'ecarter'}), docs[0]: ('decision', {'action': 'valider'}), docs[1]: ('decision', {'action': 'ecarter'}),
                   docs[2]: ('decision', {'action': 'valider'}), notes[0]: ('decision', {'action': 'valider'}),
                   notes[1]: ('decision', {'action': 'rejeter', 'motif': 'Doublon.'}), gmails[0]: ('decision', {'action': 'retenir'}),
                   gmails[1]: ('decision', {'action': 'ecarter'})}
        with patch('smtplib.SMTP', side_effect=AssertionError('aucun envoi')):
            for ident, (sous, data) in actions.items():
                out, d = self.appel('m56100/relecture/' + ident, compte=TITULAIRE[0], role='avocat')
                self.assertEqual(out['status'], '200 OK', d)
                out, recu = self.appel('m56100/relecture/%s/%s' % (ident, sous), 'POST', {**data, 'revision': d['revision'], 'cle': 'cle-v10109-' + ident[:20]},
                                       compte=TITULAIRE[0], role='avocat')
                self.assertEqual(out['status'], '200 OK', recu)
        self.assertEqual(rl.liste(self.desk, TITULAIRE, {}, '/p')['lignes'], [])
        self.assertEqual(rl.liste(self.desk, TITULAIRE, {'source': 'gmail'}, '/p')['lignes'], [])
        self.assertEqual((self.gmail.appels, self.nc.ecritures), ([], []))           # aucun effet externe

    def test_R04_routes_roles_et_csrf_des_nouvelles_commandes(self):
        from agent import standalone_auth
        self.assertTrue(standalone_auth.allowed('avocat', 'POST', '/api440/m56100/relecture/d-abc/corriger'))
        self.assertTrue(standalone_auth.allowed('avocat', 'POST', '/api440/m56100/relecture/d-abc/proposition'))
        self.assertTrue(standalone_auth.allowed('assistant', 'POST', '/api440/m56100/relecture/filtres'))
        self.assertFalse(standalone_auth.allowed('avocat', 'POST', '/api440/m56100/relecture/g-abc/envoyer'))
        self.assertTrue(rl.autoriser_route('avocat', 'GET', 'm56100/relecture/d-abc/propositions'))
        self.assertFalse(rl.autoriser_route('avocat', 'GET', 'm56100/relecture/filtres/x'))
        p = self.proposition(1)
        e = self.refus(self.cmd, TITULAIRE, p, 'corriger', revision='r1', instruction='Corriger.')
        self.assertEqual(e.status, 422)                                               # une proposition ne se « corrige » pas par l'IA
        ident, _ = self.document()
        d = rl.detail(self.desk, TITULAIRE, ident, '/p')
        out, _ = self.appel('m56100/relecture/%s/decision' % ident, 'POST', {'action': 'valider', 'revision': d['revision'], 'cle': 'cle-csrf-doc-0001'},
                            compte=TITULAIRE[0], role='avocat', jeton='autre')
        self.assertTrue(out['status'].startswith('403'))

    def test_R04_migration_5_6_100_sans_perte(self):
        base = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, base, True)
        db = sqlite3.connect(str(base / 'ancien.db'))
        db.row_factory = sqlite3.Row
        db.executescript(rl.SCHEMA.replace(rl.TABLE_ITEMS, '''(
  cabinet TEXT NOT NULL, id TEXT NOT NULL,
  kind TEXT NOT NULL CHECK (kind IN ('proposition','imap','gmail','document','decision')),
  matter TEXT NOT NULL DEFAULT '', mail_key TEXT NOT NULL DEFAULT '', ref_source TEXT NOT NULL, revision TEXT NOT NULL,
  titre TEXT NOT NULL, search_norm TEXT NOT NULL, updated INTEGER NOT NULL,
  etat TEXT NOT NULL DEFAULT 'a_relire' CHECK (etat IN ('a_relire','retenu','ecarte','remplace','depose')),
  PRIMARY KEY(cabinet,id))'''))
        db.execute("INSERT INTO file56100_items(cabinet,id,kind,ref_source,revision,titre,search_norm,updated) VALUES('cabinet','p-1','proposition','x','r1','Ancien','ancien',1)")
        db.commit()

        class Faux:
            pass
        faux = Faux()
        faux.db = db
        rl.ensure_schema(faux)
        rl.ensure_schema(faux)                                                        # idempotent
        self.assertEqual(db.execute("SELECT titre,proprietaire FROM file56100_items WHERE id='p-1'").fetchone()[:], ('Ancien', ''))
        db.execute("INSERT INTO file56100_items(cabinet,id,kind,ref_source,revision,titre,search_norm,updated) VALUES('cabinet','n-1','note_ia','x','n1','Note','note',2)")
        self.assertEqual(db.execute("SELECT COUNT(*) FROM sqlite_master WHERE name='file56100_page'").fetchone()[0], 1)
        db.close()


# ---------------------------------------------------------------------------------------------------- contrat lecteur → mesure (Node)
class Lecteur(unittest.TestCase):
    def node(self, racine):
        return subprocess.run([shutil.which('node'), str(RACINE / 'tests' / 'lecteur56101.cjs'), str(racine)], capture_output=True, text=True,
                              encoding='utf-8', timeout=60)

    @unittest.skipUnless(shutil.which('node'), 'Node.js indisponible')
    def test_V101_09_contrat_ax101_et_dix_decisions_sans_rechargement_node(self):
        r = self.node(RACINE)
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        self.assertRegex(r.stdout.strip().splitlines()[-1], r'^lecteur56101 : ok (\d+)/\1$')

    @unittest.skipUnless(shutil.which('node'), 'Node.js indisponible')
    def test_V101_09_mutation_sans_evenement_decide_detectee(self):
        source = (RACINE / 'agent/static/v56100-lecteur.js').read_text(encoding='utf-8')
        ligne = "if (type) emettre('ax101:decide', { type: type, commande: commande });"
        self.assertIn(ligne, source)
        with tempfile.TemporaryDirectory() as tmp:
            statique = Path(tmp) / 'agent' / 'static'
            statique.mkdir(parents=True)
            (statique / 'v56100-lecteur.js').write_text(source.replace(ligne, ''), encoding='utf-8')
            r = self.node(tmp)
        self.assertNotEqual(r.returncode, 0, r.stdout)

    def test_R04_gabarits_serveur_et_script_alignes_sans_html_recu(self):
        js = (RACINE / 'agent/static/v56100-lecteur.js').read_text(encoding='utf-8')
        html = rl.lecteur_html('/p', 'x')
        self.assertEqual(js.count('innerHTML'), 1)
        for marque in ('data-action="valider"', 'data-action="rejeter"', 'data-action="corriger"', 'data-section="propositions"', 'data-ax101-ouvrir',
                       'data-ax101-motif-texte', 'data-ax101-instruction', 'data-ax101-zone', 'data-ax101-demander-correction', 'data-ax101-confirmer-rejet'):
            self.assertIn(marque, html)
            self.assertIn(marque, js)
        self.assertNotIn('envoyer', html.lower().replace('rien n’est envoyé', ''))
        self.assertIn("emettre('ax101:ouvert'", js)


if __name__ == '__main__':
    unittest.main()
