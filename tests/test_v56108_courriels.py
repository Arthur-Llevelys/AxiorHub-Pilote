"""5.6.108 (lot C) : courriels — B01 à B07 de l'audit de la 5.6.107.

Faux IMAP (``fake_imap440``), faux SMTP (``test_v490.FakeSMTP``), annuaires SQLite fictifs, horloge simulée (paramètre ``now``). Aucun réseau,
aucun compte réel, aucun modèle. Identifiants : B01 enregistrement automatique, B02 génération des brouillons, B03 acceptation partielle, B04
résultat incertain, B05 compte revu juste avant le départ (et identité des travaux différés), B06 décodage PNG borné, B07 périmètre de l'export.
"""
from email import policy
from email.parser import BytesParser
import io
import json
import os
from pathlib import Path
import re
import shutil
import smtplib
import sqlite3
import struct
import subprocess
import tempfile
import time
import unittest
from unittest.mock import patch
import zipfile
import zlib

import test_v56106_envoi as te
from agent import comptes56108 as C, envoi56106 as E, send490, vision56106 as V, webmail5628 as W
from agent.common import Stop

ROOT = Path(__file__).resolve().parents[1]
STATIC = ROOT / 'agent' / 'static'
ORIGINE, JETON = te.ORIGINE, te.JETON


def appel(desk, route, corps=None, owner='durand', role='avocat', method='POST', args=None):
    env = {'HTTP_X_AXIORHUB_USER': owner, 'HTTP_X_AXIORHUB_ROLE': role}
    if method == 'POST':
        brut = json.dumps(corps or {}).encode()
        env.update({'HTTP_ORIGIN': ORIGINE, 'HTTP_X_CSRF_TOKEN': JETON, 'CONTENT_TYPE': 'application/json',
                    'CONTENT_LENGTH': str(len(brut)), 'wsgi.input': io.BytesIO(brut)})
    out = E.api(env, desk, {'origin': ORIGINE, 'csrf': JETON}, '/p', E.PREFIXE + route, args or {}, method)
    return out['status'], json.loads(out['body']), out


class Base(te.Base):
    """Brouillon fictif à deux destinataires (À et Cc) ; « durand » : compte actif de la version serveur (annuaire fictif)."""

    def setUp(self):
        super().setUp()
        item = te.FakeIMAP.folders['Drafts'][self.uid]
        msg = BytesParser(policy=policy.SMTP).parsebytes(item['raw'])
        msg['Cc'] = 'second@example.test'
        item['raw'] = msg.as_bytes()

    def annuaire(self, sql, *valeurs):
        db = sqlite3.connect(str(Path(self.f.c['state_dir']) / 'comptes5648.sqlite3'))
        db.execute(sql, valeurs)
        db.commit()
        db.close()

    def journal(self):
        return [dict(r) for r in self.desk.db.execute('SELECT * FROM send_log490 ORDER BY id')]

    def destinataires_du_brouillon(self):
        msg = BytesParser(policy=policy.SMTP).parsebytes(te.FakeIMAP.folders['Drafts'][self.uid]['raw'])
        return [a.strip() for a in (str(msg['To']) + ',' + str(msg['Cc'])).split(',') if a.strip()]


def partiel(refuse='second@example.test', journal=None):
    """Faux serveur : refuse ``refuse``, accepte les autres (et les note)."""
    def envoyer(client, message, from_addr=None, to_addrs=None):
        to = list(to_addrs)
        acceptes = [a for a in to if a != refuse]
        (journal if journal is not None else []).append({'to': acceptes, 'mid': str(message['Message-ID'])})
        if acceptes:
            te.FakeSMTP.sent.append({'message': message, 'from': from_addr, 'to': acceptes})
        return {refuse: (550, b'5.1.1 adresse fictive inconnue')} if refuse in to else {}
    return envoyer


# ================================================================================ B03 : acceptation partielle
class B03Partiel(Base):
    def depart_partiel(self):
        with patch.object(te.FakeSMTP, 'send_message', partiel()):
            out, now = self.file()
            self.assertEqual(E.tick(self.desk, 'b1', now + 11), [(out['id'], 'a_verifier')])
        return out, now

    def test_B03_01_detail_par_destinataire_et_message_id_au_journal(self):
        out, now = self.depart_partiel()
        log = self.journal()[-1]
        self.assertEqual(log['status'], 'partiel')
        self.assertTrue(log['message_id'].startswith('<'))
        self.assertEqual(log['message_id'], self.ligne(out['id'])['message_id'])
        detail = {d['adresse']: d for d in json.loads(log['destinataires'])}
        self.assertEqual(detail['second@example.test']['etat'], 'refuse')
        self.assertEqual(detail['second@example.test']['code'], 550)
        self.assertIn('adresse fictive', detail['second@example.test']['reponse'])
        self.assertTrue(detail['second@example.test']['date'])
        acceptes = [a for a, d in detail.items() if d['etat'] == 'accepte']
        self.assertEqual(len(acceptes), 1)
        self.assertTrue(self.in_drafts())                                   # rien n'est retiré tant que l'avocat n'a pas décidé
        self.assertEqual(self.ligne(out['id'])['motif'], 'smtp_envoi_partiel')

    def test_B03_02_ecran_de_rapprochement_lecture_seule(self):
        out, now = self.depart_partiel()
        avant = self.desk.db.total_changes
        status, r, raw = appel(self.desk, 'rapprochement', method='GET', args={'id': out['id']})
        self.assertEqual(status, '200 OK', r)
        self.assertIn(('Cache-Control', 'private, no-store'), raw['headers'])
        self.assertEqual(self.desk.db.total_changes, avant)                  # R05 : aucune écriture
        self.assertEqual((r['nature'], r['acceptes'], r['refuses']), ('partiel', 1, 1))
        self.assertEqual(r['decisions'], ['renvoyer_refuses', 'confirmer_parti'])
        self.assertTrue(r['message_id'])
        self.assertIn('ni la réception finale', r['note'])
        self.assertIn('absence ne prouve pas', r['envoyes']['texte'])     # copie jamais faite après un départ partiel
        self.assertEqual([d['etat'] for d in r['destinataires']].count('refuse'), 1)
        self.assertTrue(any('commencée' in t['etape'] for t in r['trace']))
        autre = appel(self.desk, 'rapprochement', owner='martin', method='GET', args={'id': out['id']})
        absent = appel(self.desk, 'rapprochement', method='GET', args={'id': 'inexistant'})
        self.assertEqual((autre[0], autre[1]), (absent[0], absent[1]))    # même 404 neutre
        self.assertEqual(absent[0], '404 Not Found')

    def test_B03_03_renvoi_aux_seuls_refuses_meme_message_id(self):
        out, now = self.depart_partiel()
        mid, version, adresses = self.ligne(out['id'])['message_id'], self.ligne(out['id'])['version'], self.destinataires_du_brouillon()
        status, r, _ = appel(self.desk, 'renvoyer_refuses', {'id': out['id'], 'version': version, 'cle': 'cle-refuses-0001'})
        self.assertEqual(status, '200 OK', r)
        self.assertEqual(r['destinataires'], ['second@example.test'])
        bis = appel(self.desk, 'renvoyer_refuses', {'id': out['id'], 'version': version, 'cle': 'cle-refuses-0001'})
        self.assertEqual(bis[1], r)                                          # même clé : même reçu, aucun second effet
        envoyes_avant = len(te.FakeSMTP.sent)
        self.assertEqual(E.tick(self.desk, 'b1', time.time() + 30), [(out['id'], 'parti')])
        self.assertEqual(len(te.FakeSMTP.sent), envoyes_avant + 1)
        dernier = te.FakeSMTP.sent[-1]
        self.assertEqual(dernier['to'], ['second@example.test'])              # jamais le destinataire déjà accepté
        self.assertEqual(str(dernier['message']['Message-ID']), mid)
        statuts = [x['status'] for x in self.journal()]
        self.assertEqual(statuts, ['partiel_complete', 'envoye'])
        blob = json.dumps(self.journal())
        for adresse in adresses:                                               # rapprochement terminé : plus aucune adresse au journal
            self.assertNotIn(adresse, blob)
        self.assertEqual({d['etat'] for d in json.loads(self.journal()[0]['destinataires'])}, {'accepte', 'refuse'})
        self.assertFalse(self.in_drafts())
        self.assertEqual(len(te.FakeIMAP.folders['Sent']), 1)
        self.assertEqual(E.tick(self.desk, 'b1', time.time() + 60), [])

    def test_B03_04_aucune_reprise_globale_ni_automatique(self):
        out, now = self.depart_partiel()
        for t in (now + 40, now + 4000, now + 90000):
            self.assertEqual(E.tick(self.desk, 'b1', t), [])
        self.assertNotIn('maintenant', E.etat(self.desk, 'durand', out['id'])['actions'])
        with self.assertRaisesRegex(Stop, 'action_impossible'):
            E.maintenant(self.desk, 'durand', out['id'], now=now + 50)
        with self.assertRaisesRegex(Stop, 'rapprochement_impossible'):
            E.renvoyer_quand_meme(self.desk, 'durand', out['id'], doublon=True, now=now + 50)
        self.assertEqual(len(te.FakeSMTP.sent), 1)

    def test_B03_05_mutation_renvoi_vers_un_destinataire_accepte_refuse(self):
        out, now = self.depart_partiel()
        E.renvoyer_refuses(self.desk, 'durand', out['id'], now=now + 20)
        options = json.loads(self.ligne(out['id'])['options'])
        accepte = [a for a in self.destinataires_du_brouillon() if a != 'second@example.test'][0]
        options['seuls'] = [accepte]                                         # falsification : une adresse déjà acceptée
        self.desk.db.execute('UPDATE envoi_file56106 SET options=? WHERE id=?', (json.dumps(options), out['id']))
        self.desk.db.commit()
        self.assertEqual(E.tick(self.desk, 'b1', now + 30), [(out['id'], 'echec')])
        self.assertEqual(self.ligne(out['id'])['motif'], 'relance_invalide')
        self.assertEqual(len(te.FakeSMTP.sent), 1)                            # le destinataire accepté ne reçoit rien de plus

    def test_B03_06_je_confirme_qu_il_est_parti(self):
        out, now = self.depart_partiel()
        status, r, _ = appel(self.desk, 'confirmer_parti', {'id': out['id'], 'version': self.ligne(out['id'])['version'], 'cle': 'cle-confirme-0001'})
        self.assertEqual(status, '200 OK', r)
        self.assertEqual((self.ligne(out['id'])['etat'], self.ligne(out['id'])['motif']), ('parti', 'confirme_par_avocat'))
        log = self.journal()[-1]
        self.assertEqual(log['status'], 'envoye_confirme')
        self.assertEqual(json.loads(log['decision'])['action'], 'confirmer_parti')
        self.assertNotIn('second@example.test', log['destinataires'])
        with self.assertRaisesRegex(Stop, 'deja_envoye'):
            send490._duplicate_check(self.desk, log['fingerprint'])

    def test_B03_07_tous_refuses_echec_certain_puis_nouvel_essai(self):
        def tous(client, message, from_addr=None, to_addrs=None):
            raise smtplib.SMTPRecipientsRefused({a: (550, b'refus fictif') for a in to_addrs})
        with patch.object(te.FakeSMTP, 'send_message', tous):
            out, now = self.file()
            self.assertEqual(E.tick(self.desk, 'b1', now + 11), [(out['id'], 'echec')])
        log = self.journal()[-1]
        self.assertEqual((log['status'], log['destinataires']), ('echec', ''))   # échec franc : aucune adresse gardée au journal
        self.assertIn('maintenant', E.etat(self.desk, 'durand', out['id'])['actions'])
        self.assertIn('rien n’est parti', E.message('smtp_destinataire_refuse'))

    def test_B03_08_routes_roles_et_idempotence(self):
        from agent import membres5650
        from agent.standalone_auth import allowed
        for route in ('renvoyer_refuses', 'confirmer_parti', 'renvoyer_quand_meme'):
            self.assertTrue(allowed('avocat', 'POST', '/api440/' + E.PREFIXE + route))
            self.assertFalse(allowed('assistant', 'POST', '/api440/' + E.PREFIXE + route))
            self.assertEqual(membres5650.category('POST', '/api440/' + E.PREFIXE + route), 'envoyer')
        self.assertTrue(allowed('avocat', 'GET', '/api440/' + E.PREFIXE + 'rapprochement'))
        status, body, _ = appel(self.desk, 'rapprochement', role='assistant', method='GET', args={'id': 'x'})
        self.assertEqual((status, body['error']), ('403 Forbidden', 'role_insuffisant'))


# ================================================================================ B04 : résultat incertain
class B04Incertain(Base):
    def depart_incertain(self):
        def perte(*args, **kwargs):
            raise smtplib.SMTPServerDisconnected('réponse perdue simulée')
        with patch.object(te.FakeSMTP, 'send_message', perte):
            out, now = self.file()
            self.assertEqual(E.tick(self.desk, 'b1', now + 11), [(out['id'], 'a_verifier')])
        return out, now

    def test_B04_01_incertain_jamais_echec_certain_ni_reessai_automatique(self):
        out, now = self.depart_incertain()
        log = self.journal()[-1]
        self.assertEqual(log['status'], 'a_verifier')
        self.assertEqual({d['etat'] for d in json.loads(log['destinataires'])}, {'inconnu'})
        for t in (now + 40, now + 7200, now + 3 * 86400):                    # le temps qui passe ne rend jamais l'envoi réessayable
            self.assertEqual(E.tick(self.desk, 'b1', t), [])
        with self.assertRaisesRegex(Stop, 'envoi_incertain'):
            send490._duplicate_check(self.desk, log['fingerprint'])
        r = E.rapprochement(self.desk, 'durand', out['id'], now=now + 50)
        self.assertEqual((r['nature'], r['decisions']), ('incertain', ['confirmer_parti', 'renvoyer_quand_meme']))
        self.assertIn('deux fois', r['doublon'])

    def test_B04_02_renvoyer_quand_meme_exige_l_avertissement(self):
        out, now = self.depart_incertain()
        status, body, _ = appel(self.desk, 'renvoyer_quand_meme', {'id': out['id'], 'cle': 'cle-doublon-0001'})
        self.assertEqual(body['error'], 'avertissement_doublon_requis')
        self.assertEqual(self.ligne(out['id'])['etat'], 'a_verifier')
        status, body, _ = appel(self.desk, 'renvoyer_quand_meme', {'id': out['id'], 'doublon': True, 'cle': 'cle-doublon-0002'})
        self.assertEqual(status, '200 OK', body)
        self.assertEqual(self.journal()[-1]['status'], 'a_verifier_renvoye')
        self.assertEqual(json.loads(self.journal()[-1]['decision'])['action'], 'renvoyer_quand_meme')
        self.assertEqual(E.tick(self.desk, 'b1', time.time() + 30), [(out['id'], 'parti')])
        self.assertEqual(len(te.FakeSMTP.sent), 1)

    def test_B04_03_confirmer_parti_bloque_tout_doublon(self):
        out, now = self.depart_incertain()
        E.confirmer_parti(self.desk, 'durand', out['id'], now=now + 20)
        self.assertEqual(self.journal()[-1]['status'], 'envoye_confirme')
        p = send490.prepare(self.desk, self.uid, '7')
        with self.assertRaisesRegex(Stop, 'deja_envoye'):
            E.mettre_en_file(self.desk, 'durand', 'avocat', p['token'], now=time.time() + 2)
        self.assertEqual(te.FakeSMTP.sent, [])

    def test_B04_04_recherche_dans_envoyes_trouve_le_message_id(self):
        out, now = self.depart_incertain()
        mid = self.ligne(out['id'])['message_id']
        copie = b'Message-ID: ' + mid.encode() + b'\r\nSubject: copie fictive\r\n\r\ncorps\r\n'
        te.FakeIMAP.add('Sent', copie, ['\\Seen'])
        with patch.object(E, '_dans_envoyes', return_value=True):
            r = E.rapprochement(self.desk, 'durand', out['id'], now=now + 20)
        self.assertTrue(r['envoyes']['trouve'])
        with patch.object(E, '_dans_envoyes', return_value=None):
            r = E.rapprochement(self.desk, 'durand', out['id'], now=now + 20)
        self.assertIsNone(r['envoyes']['trouve'])
        self.assertIn('ne répond pas', r['envoyes']['texte'])

    def test_B04_05_mutation_sans_garde_doublon_double_envoi(self):
        """Contre-épreuve : sans le contrôle des envois incertains, un nouveau départ du même message aurait lieu."""
        out, now = self.depart_incertain()
        self.desk.db.execute("UPDATE envoi_file56106 SET etat='echec' WHERE id=?", (out['id'],))   # état falsifié dans la base de test
        self.desk.db.commit()
        E.maintenant(self.desk, 'durand', out['id'], now=now + 20)
        self.assertEqual(E.tick(self.desk, 'b1', now + 21), [(out['id'], 'a_verifier')])   # garde du journal : bloqué
        self.assertEqual(te.FakeSMTP.sent, [])
        self.desk.db.execute("UPDATE envoi_file56106 SET etat='en_attente', depart=? WHERE id=?", (now + 40, out['id']))
        self.desk.db.commit()
        with patch.object(send490, '_duplicate_check', lambda desk, fp: None):
            self.assertEqual(E.tick(self.desk, 'b1', now + 41), [(out['id'], 'parti')])
        self.assertEqual(len(te.FakeSMTP.sent), 1)


# ================================================================================ B05 : compte revu juste avant le départ
class B05Compte(Base):
    def quand(self, jours=2):
        from datetime import datetime, timedelta
        d = (datetime.now(te.PARIS) + timedelta(days=jours)).replace(hour=9, minute=0, second=0, microsecond=0)
        return d.strftime('%Y-%m-%dT%H:%M'), d.timestamp()

    def test_B05_01_compte_desactive_apres_programmation_rien_ne_part(self):
        texte, ts = self.quand()
        out, now = self.file(texte)
        self.annuaire('UPDATE users SET active=0 WHERE ident=?', 'durand')
        self.assertEqual(E.tick(self.desk, 'b1', ts + 1), [(out['id'], 'retenu')])
        self.assertEqual(te.FakeSMTP.sent, [])
        self.assertTrue(self.in_drafts())
        item = E.etat(self.desk, 'durand', out['id'])
        self.assertEqual((item['etat'], item['motif']), ('retenu', 'compte_desactive'))
        self.assertIn('désactivé', item['motif_libelle'])
        self.assertEqual(item['actions'], ['maintenant', 'annuler', 'retirer'])
        self.assertEqual(self.journal(), [])                                # aucune tentative SMTP journalisée
        # réactivation : rien ne repart tout seul
        self.annuaire('UPDATE users SET active=1 WHERE ident=?', 'durand')
        self.assertEqual(E.tick(self.desk, 'b1', ts + 3600), [])
        self.assertEqual(te.FakeSMTP.sent, [])
        E.maintenant(self.desk, 'durand', out['id'], now=ts + 3700, role='avocat')   # reprise explicite, contrôlée au départ
        self.assertEqual(E.tick(self.desk, 'b1', ts + 3701), [(out['id'], 'parti')])
        self.assertEqual(len(te.FakeSMTP.sent), 1)

    def test_B05_02_reactivation_ne_ressuscite_pas_un_message_annule(self):
        texte, ts = self.quand()
        out, now = self.file(texte)
        self.annuaire('UPDATE users SET active=0 WHERE ident=?', 'durand')
        E.tick(self.desk, 'b1', ts + 1)
        E.annuler(self.desk, 'durand', out['id'], now=ts + 2)
        self.annuaire('UPDATE users SET active=1 WHERE ident=?', 'durand')
        for t in (ts + 10, ts + 86400):
            self.assertEqual(E.tick(self.desk, 'b1', t), [])
        with self.assertRaisesRegex(Stop, 'action_impossible'):
            E.maintenant(self.desk, 'durand', out['id'], now=ts + 20)
        self.assertEqual(te.FakeSMTP.sent, [])

    def test_B05_03_role_change_droit_retire_dossier_retire(self):
        texte, ts = self.quand()
        out, now = self.file(texte)
        self.annuaire("UPDATE users SET role='assistant' WHERE ident=?", 'durand')
        self.assertEqual(E.tick(self.desk, 'b1', ts + 1), [(out['id'], 'retenu')])
        self.assertEqual(self.ligne(out['id'])['motif'], 'role_retire')
        self.annuaire("UPDATE users SET role='avocat' WHERE ident=?", 'durand')
        E.maintenant(self.desk, 'durand', out['id'], now=ts + 5, role='avocat')
        with patch('agent.membres5650.allowed', return_value=False):        # droit « envoyer » retiré au compte
            self.assertEqual(E.tick(self.desk, 'b1', ts + 6), [(out['id'], 'echec')])
        self.assertEqual(self.ligne(out['id'])['motif'], 'droit_envoi_retire')
        E.maintenant(self.desk, 'durand', out['id'], now=ts + 7, role='avocat')
        with patch('agent.membres5650.hidden_for', return_value=frozenset({'DEMO'})):   # dossier réservé à d'autres depuis
            self.assertEqual(E.tick(self.desk, 'b1', ts + 8), [(out['id'], 'echec')])
        self.assertEqual(self.ligne(out['id'])['motif'], 'droit_envoi_retire')
        self.assertEqual(te.FakeSMTP.sent, [])

    def test_B05_04_annuaire_illisible_ou_profil_inconnu(self):
        texte, ts = self.quand()
        out, now = self.file(texte)
        chemin = Path(self.f.c['state_dir']) / 'comptes5648.sqlite3'
        sauve = chemin.read_bytes()
        chemin.write_bytes(b'pas une base sqlite' * 100)
        self.assertEqual(E.tick(self.desk, 'b1', ts + 1), [(out['id'], 'retenu')])
        self.assertEqual(self.ligne(out['id'])['motif'], 'annuaire_illisible')
        chemin.write_bytes(sauve)
        # compte retiré de l'annuaire : jamais remplacé par une identité de session
        self.annuaire('DELETE FROM users WHERE ident=?', 'durand')
        E.maintenant(self.desk, 'durand', out['id'], now=ts + 5, role='avocat')
        self.assertEqual(E.tick(self.desk, 'b1', ts + 6), [(out['id'], 'retenu')])
        self.assertEqual(self.ligne(out['id'])['motif'], 'compte_introuvable')
        self.assertEqual(te.FakeSMTP.sent, [])

    def test_B05_05_identite_nextcloud_fenetre_de_session(self):
        texte, ts = self.quand()
        out, now = self.file(texte, owner='zorval')                          # aucun annuaire local ne connaît ce compte (Nextcloud)
        self.assertEqual(json.loads(self.ligne(out['id'])['auteur'])['profil'], 'session')
        self.assertEqual(E.tick(self.desk, 'b1', ts + 1), [(out['id'], 'retenu')])
        self.assertEqual(self.ligne(out['id'])['motif'], 'compte_non_verifiable')
        self.assertEqual(te.FakeSMTP.sent, [])
        E.maintenant(self.desk, 'zorval', out['id'], now=ts + 10, role='avocat')   # geste explicite dans sa session
        self.assertEqual(E.tick(self.desk, 'b1', ts + 11), [(out['id'], 'parti')])
        self.assertEqual(len(te.FakeSMTP.sent), 1)

    def test_B05_06_delai_d_annulation_court_reste_possible_pour_nextcloud(self):
        out, now = self.file(owner='zorval')
        self.assertEqual(E.tick(self.desk, 'b1', now + 11), [(out['id'], 'parti')])

    def test_B05_07_compte_revu_juste_avant_le_smtp(self):
        out, now = self.file()
        appels = []

        def garde_puis_desactivation(desk, acteur, cfg, ref):
            appels.append(acteur)
            self.annuaire('UPDATE users SET active=0 WHERE ident=?', 'durand')   # désactivé après le contrôle du départ, avant le SMTP
        with patch('agent.acces_courriel56100.garde_envoi', garde_puis_desactivation):
            self.assertEqual(E.tick(self.desk, 'b1', now + 11), [(out['id'], 'retenu')])
        self.assertEqual(self.ligne(out['id'])['motif'], 'compte_desactive')
        self.assertTrue(appels)
        self.assertEqual(te.FakeSMTP.sent, [])
        self.assertEqual(self.journal(), [])

    def test_B05_08_mutation_role_garde_en_file_aurait_envoye(self):
        """Contre-épreuve : si le compte n'était pas relu (rôle gardé en file), le compte désactivé aurait envoyé."""
        texte, ts = self.quand()
        out, now = self.file(texte)
        self.annuaire('UPDATE users SET active=0 WHERE ident=?', 'durand')
        with patch.object(C, 'compte_actuel', lambda desk, owner, attendu=None: {'owner': owner, 'role': 'avocat', 'profil': 'serveur'}):
            self.assertEqual(E.tick(self.desk, 'b1', ts + 1), [(out['id'], 'parti')])
        self.assertEqual(len(te.FakeSMTP.sent), 1)

    def test_B05_09_distribution_autonome_lecture_seule(self):
        racine = Path(tempfile.mkdtemp(dir=self.f.base))
        annuaire = racine / 'users.sqlite3'
        with sqlite3.connect(annuaire) as db:
            db.execute('CREATE TABLE users(email TEXT, name TEXT, role TEXT, active INTEGER)')
            db.execute("INSERT INTO users VALUES('kervilac@example.test','KERVILAC','avocat',1)")
        db.close()
        with patch.dict(os.environ, {'AXIORHUB_AUTH_STATE': str(racine)}):
            self.assertEqual(C.compte_actuel(self.desk, 'kervilac@example.test')['role'], 'avocat')
            with self.assertRaisesRegex(Stop, 'compte_introuvable'):
                C.compte_actuel(self.desk, 'cabinet')                       # « cabinet » n'est pas une personne de la distribution autonome
            with sqlite3.connect(annuaire) as db:
                db.execute('UPDATE users SET active=0')
            db.close()
            with self.assertRaisesRegex(Stop, 'compte_desactive'):
                C.compte_actuel(self.desk, 'kervilac@example.test')
            annuaire.unlink()
            self.assertFalse(C._autonome())
        absent = racine / 'absent' / 'users.sqlite3'
        with self.assertRaisesRegex(Stop, 'annuaire_illisible'):
            C._lire(absent, 'SELECT 1', ())
        self.assertFalse(absent.exists())                                     # un annuaire absent n'est jamais créé

    def test_B05_10_installation_et_travaux_differes(self):
        self.assertEqual(C.verifier_auteur(self.desk, {'owner': 'cabinet'})['role'], 'administrateur')
        for job in (None, {}, {'args': '{}'}, {'args': json.dumps({'texte': 'x'})}, {'args': 'pas du json'}):
            with self.assertRaisesRegex(Stop, 'auteur_non_verifiable'):
                C.verifier_auteur(self.desk, job)
        auteur = C.noter_auteur(self.desk, 'durand', 'avocat', maintenant=1000)
        ligne = {'id': 7, 'kind': 'assistance', 'args': json.dumps({'matter': 'DEMO', C.CLE: auteur})}
        self.assertEqual(C.verifier_auteur(self.desk, ligne, maintenant=999999)['role'], 'avocat')   # annuaire : pas de fenêtre
        self.assertEqual(C.verifier_auteur(self.desk, {'args': {'owner': 'durand'}})['owner'], 'durand')   # ancien travail : propriétaire seul
        self.annuaire('UPDATE users SET active=0 WHERE ident=?', 'durand')
        with self.assertRaisesRegex(Stop, 'compte_desactive'):
            C.verifier_auteur(self.desk, ligne)
        session = {'args': json.dumps({C.CLE: C.noter_auteur(self.desk, 'zorval', 'avocat', maintenant=1000)})}
        self.assertEqual(C.verifier_auteur(self.desk, session, maintenant=1050)['profil'], 'session')
        with self.assertRaisesRegex(Stop, 'compte_non_verifiable'):
            C.verifier_auteur(self.desk, session, maintenant=1000 + C.FENETRE_SESSION + 1)
        self.assertEqual(C.verifier_auteur(self.desk, session, fenetre=3600, maintenant=4000)['owner'], 'zorval')
        with self.assertRaisesRegex(Stop, 'role_retire'):
            C.verifier_auteur(self.desk, {'args': json.dumps({C.CLE: C.noter_auteur(self.desk, 'zorval', 'assistant', 1000)})}, maintenant=1001)
        with patch('agent.membres5650.hidden_for', return_value=frozenset({'DOS-001'})):
            with self.assertRaisesRegex(Stop, 'dossier_reserve'):
                C.verifier_auteur(self.desk, session, dossier='DOS-001', maintenant=1001)
        with patch('agent.membres5650.allowed', return_value=False):
            with self.assertRaisesRegex(Stop, 'droit_retire'):
                C.verifier_auteur(self.desk, session, methode='POST', chemin='/api440/x', maintenant=1001)
        for code in C.CODES:
            self.assertTrue(C.message(code))

    def test_B05_11_ancien_envoi_sans_auteur_note(self):
        texte, ts = self.quand()
        out, now = self.file(texte)
        self.desk.db.execute("UPDATE envoi_file56106 SET auteur='{}' WHERE id=?", (out['id'],))   # file d'avant la 5.6.108
        self.desk.db.commit()
        self.assertEqual(E.tick(self.desk, 'b1', ts + 1), [(out['id'], 'parti')])   # compte de l'annuaire relu : vérifiable
        self.assertEqual(len(te.FakeSMTP.sent), 1)


# ================================================================================ B02 : génération des brouillons et des origines
class B02Generation(Base):
    def sauvegarder(self, validite, acteur=('durand', 'administrateur')):
        return W.save(self.desk, {'mode': 'reprise', 'to': 'dest@example.test', 'html': '<p>Version</p>', 'draft_uid': str(self.uid),
                                  'draft_uidvalidity': validite, 'reprise_uid': str(self.uid)}, owner='durand', acteur=acteur)

    def test_B02_01_uid_reutilise_dans_une_autre_generation_aucune_ecriture(self):
        avant = dict(te.FakeIMAP.folders['Drafts'])
        te.FakeIMAP.validity['Drafts'] = '8'
        with self.assertRaises(Stop):
            self.sauvegarder('7')
        self.assertEqual(te.FakeIMAP.folders['Drafts'], avant)
        self.assertFalse([x for x in te.FakeIMAP.log if x[0] == 'APPEND'])
        self.assertFalse([x for x in te.FakeIMAP.log if x[:2] in (('UID', 'STORE'), ('UID', 'EXPUNGE'))])

    def test_B02_02_generation_manquante_refusee_texte_garde(self):
        for validite in ('', None, 'abc'):
            with self.assertRaisesRegex(Stop, 'uidvalidity_requise'):
                self.sauvegarder(validite)
        self.assertTrue(self.in_drafts())
        self.assertFalse([x for x in te.FakeIMAP.log if x[0] == 'APPEND'])
        from agent.web import REASONS
        self.assertIn('Votre texte reste', REASONS['uidvalidity_requise'])

    def test_B02_03_remplacement_normal_uid_et_generation_ensemble(self):
        out = self.sauvegarder('7')
        self.assertEqual(out['uidvalidity'], '7')
        self.assertNotEqual(str(out['uid']), str(self.uid))
        self.assertFalse(self.in_drafts())

    def test_B02_04_origine_sans_generation_refusee_en_session(self):
        source = te.FakeIMAP.add('INBOX', te.raw_draft().replace(b'Bien re', b'Origine re'))
        donnees = {'mode': 'repondre', 'source_uid': str(source), 'to': 'x@example.test', 'subject': 'Re', 'html': '<p>x</p>'}
        with self.assertRaisesRegex(Stop, 'uidvalidity_requise'):
            W.save(self.desk, donnees, 'durand', acteur=('durand', 'administrateur'))
        self.assertFalse([x for x in te.FakeIMAP.log if x[0] == 'APPEND'])
        self.assertFalse([x for x in te.FakeIMAP.log if 'BODY.PEEK[]' in ' '.join(x)])   # corps de l'origine jamais lu
        with self.assertRaisesRegex(Stop, 'uidvalidity_requise'):
            W.template(self.desk, 'repondre', str(source), 'INBOX', acteur=('durand', 'administrateur'))
        self.assertTrue(W.template(self.desk, 'repondre', str(source), 'INBOX', acteur=('durand', 'administrateur'), validity='7')['source_uidvalidity'])
        out = W.save(self.desk, {**donnees, 'source_uidvalidity': '7'}, 'durand', acteur=('durand', 'administrateur'))
        self.assertEqual(out['uidvalidity'], '7')

    def test_B02_05_origine_generation_changee_appel_interne(self):
        source = te.FakeIMAP.add('INBOX', te.raw_draft())
        te.FakeIMAP.validity['INBOX'] = '9'
        with self.assertRaisesRegex(Stop, 'uidvalidity_modifiee'):
            W.save(self.desk, {'mode': 'transferer', 'source_uid': str(source), 'source_uidvalidity': '7', 'to': 'x@example.test',
                               'subject': 'TR', 'html': '<p>x</p>', 'keep': ['0']}, 'durand')
        self.assertFalse([x for x in te.FakeIMAP.log if x[0] == 'APPEND'])

    def test_B02_06_appenduid_absent_original_garde_tant_que_non_identifie(self):
        from agent import drafts440
        with patch.object(drafts440, '_append_uid', return_value=''), patch.object(drafts440, '_search_uids', return_value=[]):
            with self.assertRaisesRegex(Stop, 'brouillon_enregistre_non_retrouve'):
                self.sauvegarder('7')
        self.assertTrue(self.in_drafts())                                      # l'original reste tant que le dépôt n'est pas identifié
        with patch.object(drafts440, '_append_uid', return_value=''):
            out = self.sauvegarder('7')                                        # retrouvé par son marqueur dans la même génération
        self.assertEqual(out['uidvalidity'], '7')
        self.assertFalse(self.in_drafts())

    def test_B02_07_clients_de_l_api_transmettent_la_generation(self):
        v5628 = (STATIC / 'v5628.js').read_text(encoding='utf-8')
        self.assertIn("q.set('uidvalidity'", v5628)
        self.assertIn('draft_uidvalidity: state.draftValidity', v5628)
        self.assertIn('source_uidvalidity: t.source_uidvalidity', v5628)
        self.assertIn('state.draftValidity = r.uidvalidity', v5628)
        self.assertNotRegex(v5628, r"draftUid = r\.uid;(?![^\n]*\n\s*state\.draftValidity)")
        for nom in ('v5628.js', 'v56106-redaction.js', 'v56106-liste.js', 'v5625.js'):
            texte = (STATIC / nom).read_text(encoding='utf-8')
            self.assertNotRegex(texte, r"draft_uidvalidity\s*:\s*['\"]['\"]", nom)   # jamais une génération vide forcée


# ================================================================================ B01 : enregistrement automatique (composant réel sous Node)
NODE = shutil.which('node')
SCRIPT_B01 = r'''
'use strict';
const assert = require('node:assert/strict');
const fs = require('node:fs');
const vm = require('node:vm');
const ecoute = {};
function node() {
  const n = {textContent: '', parentNode: null, classList: {add() {}, remove() {}}, setAttribute() {}, appendChild() {},
    querySelector() {return null;}, querySelectorAll() {return [];}, addEventListener() {}};
  return n;
}
let dansLaPage = true;
const minuteurs = [];
const context = {window: {addEventListener(t, f) { ecoute[t] = f; }, removeEventListener(t) { delete ecoute[t]; }},
  document: {querySelector() {return null;}, createElement: node, contains() {return dansLaPage;}},
  setInterval(f) { minuteurs.push(f); return minuteurs.length; }, clearInterval() {}, WeakMap, Map, Uint8Array, Date, Promise};
vm.createContext(context);
vm.runInContext(fs.readFileSync(process.argv[2], 'utf8'), context);
let appels = 0, echec = false;
const form = node();
const auto = context.window.axh56106r.auto(form, () => { appels += 1; return echec ? Promise.reject(new Error('panne fictive')) : Promise.resolve(); }, () => true);
(async () => {
  // 1. frappe pendant l'enregistrement : la réponse ancienne n'acquitte pas la frappe suivante
  auto.marquer(); const v1 = auto.revision(); auto.marquer();
  auto.enregistre(v1); assert.equal(auto.sale(), true);
  auto.enregistre(); assert.equal(auto.sale(), true);
  auto.enregistre(auto.revision()); assert.equal(auto.sale(), false);
  // 2. fermeture : avertissement tant que la saisie courante n'est pas acquittée
  auto.marquer();
  const ev = {preventDefault() { this.bloque = true; }, returnValue: undefined};
  ecoute.beforeunload(ev); assert.equal(ev.bloque, true);
  auto.enregistre(auto.revision());
  const ev2 = {preventDefault() { this.bloque = true; }};
  ecoute.beforeunload(ev2); assert.equal(ev2.bloque, undefined);
  // 3. erreur : texte gardé (aucun acquittement), heure du prochain essai annoncée
  echec = true; auto.marquer(); minuteurs[minuteurs.length - 1]();
  await new Promise((r) => setTimeout(r, 10));
  assert.equal(auto.sale(), true);
  // 4. changement de message : les dernières frappes partent avant le remplacement ; « arreter » (envoi confirmé) n'enregistre plus rien
  echec = false; const avant = appels; auto.quitter(); await new Promise((r) => setTimeout(r, 10));
  assert.equal(appels, avant + 1);
  auto.marquer(); auto.arreter(); auto.quitter(); dansLaPage = false; minuteurs[minuteurs.length - 1]();
  await new Promise((r) => setTimeout(r, 10));
  assert.equal(appels, avant + 1);
  assert.equal(ecoute.beforeunload, undefined);
  console.log('B01 OK');
})().catch((e) => { console.error(e); process.exit(1); });
'''


@unittest.skipUnless(NODE, 'Node.js absent')
class B01Enregistrement(unittest.TestCase):
    def test_B01_01_revisions_fermeture_erreur_et_changement_de_message(self):
        with tempfile.TemporaryDirectory() as d:
            script = Path(d) / 'b01.cjs'
            script.write_text(SCRIPT_B01, encoding='utf-8')
            r = subprocess.run([NODE, str(script), str(STATIC / 'v56106-redaction.js')], capture_output=True, text=True, timeout=60)
        self.assertEqual(r.returncode, 0, r.stderr + r.stdout)
        self.assertIn('B01 OK', r.stdout)

    def test_B01_02_textes_visibles_et_appelants(self):
        js = (STATIC / 'v56106-redaction.js').read_text(encoding='utf-8')
        for texte in ('Enregistrement du brouillon…', 'Modifications à enregistrer…', "'Enregistré à '", 'nouvel essai à', 'Votre texte reste ici'):
            self.assertIn(texte, js)
        v5628 = (STATIC / 'v5628.js').read_text(encoding='utf-8')
        self.assertIn('var revisionAuto = auto ? auto.revision() : 0;', v5628)
        self.assertIn('auto.enregistre(revisionAuto)', v5628)
        self.assertIn('form.axhAuto = auto', v5628)
        self.assertIn('avant.axhAuto.quitter()', v5628)
        self.assertRegex(v5628, r'function fini\(x\) \{ if \(auto\) auto\.arreter\(\);')
        self.assertIn('!inflight.save && !inflight.send', v5628)
        for nom in os.listdir(STATIC):                                          # aucun acquittement sans révision
            if nom.endswith('.js') and nom.startswith('v5'):
                self.assertNotRegex((STATIC / nom).read_text(encoding='utf-8', errors='replace'), r'auto\.enregistre\(\)', nom)


# ================================================================================ B06 : décodage PNG borné
def png_source(w, h, prof, type_, lignes, palette=None, entrelace=0, ihdr=None, idat=None):
    def bloc(nom, raw):
        return struct.pack('>I', len(raw)) + nom + raw + struct.pack('>I', zlib.crc32(nom + raw) & 0xffffffff)
    out = bytes([137, 80, 78, 71, 13, 10, 26, 10]) + bloc(b'IHDR', ihdr if ihdr is not None else struct.pack('>IIBBBBB', w, h, prof, type_, 0, 0, entrelace))
    if palette is not None:
        out += bloc(b'PLTE', palette)
    return out + bloc(b'IDAT', idat if idat is not None else zlib.compress(lignes)) + bloc(b'IEND', b'')


class B06Png(unittest.TestCase):
    def ligne(self, octets, h=2):
        return (b'\x00' + octets) * h

    def test_B06_01_profondeurs_gris(self):
        for prof, octets in ((1, b'\x80'), (2, b'\xc0'), (4, b'\xf0'), (8, b'\xff\x00'), (16, b'\xff\xff\x00\x00')):
            w = 2 if prof >= 8 else 1
            with self.subTest(prof=prof):
                out = V.png_lire(png_source(w, 2, prof, 0, self.ligne(octets)))
                self.assertEqual(out[:3], (w, 2, 1))
                self.assertEqual(out[3][0], 255)
                self.assertEqual(len(out[3]), w * 2)

    def test_B06_02_palette_rvb_et_alpha(self):
        palette = bytes([10, 20, 30, 200, 100, 50])
        for prof, octets in ((1, b'\x40'), (2, b'\x10'), (4, b'\x10'), (8, b'\x01\x00')):
            w = 2
            with self.subTest(palette=prof):
                out = V.png_lire(png_source(w, 1, prof, 3, self.ligne(octets, 1), palette=palette))
                self.assertEqual((out[2], len(out[3])), (3, 6))
        self.assertEqual(V.png_lire(png_source(1, 1, 8, 2, self.ligne(b'\x01\x02\x03', 1)))[3], bytearray(b'\x01\x02\x03'))
        self.assertEqual(V.png_lire(png_source(1, 1, 16, 2, self.ligne(b'\x01\x00\x02\x00\x03\x00', 1)))[3], bytearray(b'\x01\x02\x03'))
        self.assertEqual(V.png_lire(png_source(1, 1, 8, 6, self.ligne(b'\x00\x00\x00\x00', 1)))[3], bytearray(b'\xff\xff\xff'))   # transparent sur blanc
        self.assertEqual(V.png_lire(png_source(1, 1, 8, 4, self.ligne(b'\x00\xff', 1)))[3], bytearray(b'\x00'))

    def test_B06_03_fichiers_incoherents_refuses_sans_exception_brute(self):
        cas = {
            'entrelace': png_source(1, 1, 8, 0, self.ligne(b'\x00', 1), entrelace=1),
            'surplus': png_source(1, 1, 8, 0, bytes(4 * 1024 * 1024)),
            'trop_court': png_source(1, 1, 8, 0, b'\x00'),
            'donnees_apres_le_flux': png_source(1, 1, 8, 0, b'', idat=zlib.compress(b'\x00\x00') + b'reste'),
            'ihdr_tronque': png_source(1, 1, 8, 0, b'', ihdr=b'\x00\x00\x00\x01\x00\x00'),
            'rvb_sur_4_bits': png_source(1, 1, 4, 2, self.ligne(b'\x00\x00', 1)),
            'palette_absente': png_source(1, 1, 8, 3, self.ligne(b'\x00', 1)),
            'palette_16_bits': png_source(1, 1, 16, 3, self.ligne(b'\x00\x00', 1), palette=b'\x00\x00\x00'),
            'filtre_inconnu': png_source(1, 1, 8, 0, b'\x07\x00'),
            'non_png': b'GIF89a' + bytes(40),
        }
        complet = png_source(4, 4, 8, 0, self.ligne(b'\x00' * 4, 4))
        cas['tronque_au_milieu'] = complet[:len(complet) - 20]
        cas['tronque_dans_ihdr'] = complet[:20]
        for nom, raw in cas.items():
            with self.subTest(nom):
                with self.assertRaisesRegex(Stop, '^image_illisible$'):
                    V.png_lire(raw)
        self.assertIn('Image illisible ou incohérente', V.MESSAGES['image_illisible'])
        self.assertIn('original est gardé', V.MESSAGES['image_illisible'])

    def test_B06_04_decompression_bornee_par_les_dimensions(self):
        bombe = png_source(1, 1, 8, 0, bytes(16 * 1024 * 1024))
        vus = []
        vraie = zlib.decompressobj

        def espion(*a, **k):
            obj = vraie(*a, **k)

            class Borne:
                def decompress(self, data, limite=0):
                    vus.append(limite)
                    return obj.decompress(data, limite)

                def __getattr__(self, nom):
                    return getattr(obj, nom)
            return Borne()
        with patch.object(V.zlib, 'decompressobj', espion):
            with self.assertRaises(Stop):
                V.png_lire(bombe)
        self.assertEqual(vus, [3])                                                   # 1 pixel : 2 octets attendus + 1


# ================================================================================ B07 : périmètre de l'export
class B07Export(Base):
    def exporter(self, **options):
        from agent import export5688 as X
        dest = Path(self.f.base) / ('export-%d.zip' % len(os.listdir(self.f.base)))
        meta, _ = X.construire(self.desk, dest, {'documents': False, 'dossiers_reserves': 'exclure', **options})
        with zipfile.ZipFile(dest) as z:
            fichiers = {n: z.read(n) for n in z.namelist()}
        return dest, meta, fichiers

    def test_B07_01_modele_de_reponse_exporte_manifeste_valide(self):
        from agent import export5688 as X, redaction56106 as R
        R.creer(self.desk, 'durand', 'MODELE_A_CONSERVER_108', '<p>FORMULE_A_CONSERVER_108</p>')
        dest, meta, fichiers = self.exporter()
        self.assertTrue(X.verifier_manifeste(dest)['ok'])
        self.assertIn(b'FORMULE_A_CONSERVER_108', fichiers['modeles/modeles_de_reponse.json'])

    def test_B07_02_routines_versions_propositions_et_relecture(self):
        from agent import export5688 as X, membres5650
        maintenant = '2026-11-18T09:00:00+00:00'
        db = self.desk.db
        for ident, matter in (('rout-visible', 'DEMO'), ('rout-reservee', 'AUTRE')):
            db.execute("INSERT INTO routines56106(id,owner,scope,title,criteres_json,actions_json,matter,created_by,created_at,updated_at) "
                       "VALUES(?,?,?,?,?,?,?,?,?,?)", (ident, 'durand', 'personnel', 'ROUTINE_' + ident, '{}', '[]', matter, 'durand', maintenant, maintenant))
            db.execute('INSERT INTO routines56106_versions VALUES(?,?,?,?,?,?)', (ident, 1, json.dumps({'titre': 'VERSION_' + ident}), 'durand', 'creer', maintenant))
            db.execute("INSERT INTO routines56106_elements(id,passage_id,routine_id,mail_key,genre,matter,etat,created,updated) VALUES(?,?,?,?,?,?,?,?,?)",
                       ('el-' + ident, 'p', ident, 'k', 'tache', matter, 'propose', maintenant, maintenant))
            db.execute("INSERT INTO file56100_items(cabinet,id,kind,matter,ref_source,revision,titre,search_norm,updated) VALUES(?,?,?,?,?,?,?,?,?)",
                       ('cabinet', 'it-' + ident, 'decision', matter, 'r', '1', 'ITEM_' + ident, 'x', 1))
            db.execute('INSERT INTO file56100_historique VALUES(?,?,?,?,?,?,?)', ('cabinet', 'it-' + ident, '1', 'retenir', 'durand', 1, '{"note":"DECISION_%s"}' % ident))
        db.commit()
        membres5650.set_reserve(self.f.c['state_dir'], 'AUTRE', ['martin'])
        dest, meta, fichiers = self.exporter()
        tout = b'\n'.join(fichiers.values())
        for marque in (b'ROUTINE_rout-visible', b'VERSION_rout-visible', b'el-rout-visible', b'ITEM_rout-visible', b'DECISION_rout-visible'):
            self.assertIn(marque, tout)
        for marque in (b'ROUTINE_rout-reservee', b'VERSION_rout-reservee', b'el-rout-reservee', b'ITEM_rout-reservee', b'DECISION_rout-reservee'):
            self.assertNotIn(marque, tout)
        bilan = meta['perimetre']['domaines']['routines']
        self.assertEqual((bilan['attendu'], bilan['exporte'], bilan['exclu']), (6, 3, 3))
        self.assertTrue(X.verifier_manifeste(dest)['ok'])

    def test_B07_03_table_non_couverte_interdit_l_etat_complet(self):
        from agent import export5688 as X
        self.desk.db.execute('CREATE TABLE IF NOT EXISTS donnees_nouvelles_108(id TEXT, valeur TEXT)')
        self.desk.db.execute("INSERT INTO donnees_nouvelles_108 VALUES('1','VALEUR_NON_COUVERTE_108')")
        self.desk.db.commit()
        dest, meta, fichiers = self.exporter()
        self.assertFalse(meta['perimetre']['complet'])
        self.assertIn('donnees_nouvelles_108', meta['perimetre']['non_couvertes'])
        self.assertNotIn(b'VALEUR_NON_COUVERTE_108', b''.join(fichiers.values()))   # liste fermée : jamais de copie aveugle
        controle = X.verifier_manifeste(dest)
        self.assertTrue(controle['ok'])
        self.assertFalse(controle['complet'])
        self.assertIn('donnees_nouvelles_108', controle['non_couvertes'])
        self.assertIn('INCOMPLET', fichiers['LISEZMOI.txt'].decode('utf-8'))
        for table in ('jobs', 'settings', 'envoi_file56106', 'routines56106_recus', 'live_events_v430', 'exports5688', 'connexions5652'):
            self.assertTrue(X.motif_exclusion(table), table)
            self.assertNotIn(table, meta['perimetre']['non_couvertes'])
        self.assertTrue(all(set(x) == {'motif', 'tables'} for x in meta['perimetre']['tables_exclues']))   # motifs comptés, sans noms techniques
        # toutes les tables de données couvertes (registre complété) : l'export peut se dire complet
        origine = X.tables_exclues
        with patch.object(X, 'tables_exclues', lambda: {**origine(), **{t: 'couverte par le test' for t in meta['perimetre']['non_couvertes']}}):
            dest2, meta2, fichiers2 = self.exporter()
        self.assertTrue(meta2['perimetre']['complet'])
        self.assertTrue(X.verifier_manifeste(dest2)['complet'])
        self.assertIn('COMPLET', fichiers2['LISEZMOI.txt'].decode('utf-8'))

    def test_B07_04_mutation_bilan_falsifie_detecte(self):
        from agent import export5688 as X
        from agent import redaction56106 as R
        R.creer(self.desk, 'durand', 'MODELE_B07', '<p>texte</p>')
        dest, meta, fichiers = self.exporter()
        manifeste = json.loads(fichiers['MANIFEST.json'])
        manifeste['perimetre']['domaines']['modeles']['exporte'] += 5
        falsifie = Path(self.f.base) / 'falsifie.zip'
        with zipfile.ZipFile(falsifie, 'w') as z:
            for nom, data in fichiers.items():
                z.writestr(nom, json.dumps(manifeste) if nom == 'MANIFEST.json' else data)
        controle = X.verifier_manifeste(falsifie)
        self.assertFalse(controle['ok'])
        self.assertIn('bilan du domaine différent des fichiers présents', [a['motif'] for a in controle['anomalies']])

    def test_B07_05_declarations_des_modules_et_liste_fermee(self):
        from agent import export5688 as X
        noms = [t[2] for t in X.tables_exportees()]
        self.assertEqual(len(noms), len(set(noms)))
        self.assertIn('modeles_reponse56106', noms)
        self.assertIn('routines56106_versions', noms)
        try:
            from agent import assistance35
        except ImportError:
            assistance35 = None
        if assistance35 is not None and getattr(assistance35, 'EXPORT', None):
            self.assertIn('assistance_projects_v350', noms)                     # point de montage du lot T (module déclarant)
        for interdite in ('users', 'sessions', 'jobs', 'settings', 'audit', 'envoi_file56106', 'envoi_recus56106', 'send_log490', 'send_pending490'):
            self.assertNotIn(interdite, noms)
        self.assertEqual({t[3] for t in X.tables_exportees()}, {X.DESK})
        for t in X.tables_exportees():
            self.assertTrue(t[4] is None or isinstance(t[4], str) or (isinstance(t[4], tuple) and len(t[4]) == 4), t)
        self.assertEqual(X.motif_exclusion('pilote56102_recus'), X.FAMILLES_EXCLUES[0][1])
        self.assertEqual(X.motif_exclusion('table_inconnue_108'), '')

    def test_B07_06_reversibilite_affiche_le_perimetre(self):
        from agent import reversibilite5688 as rv
        ligne = {'id': 'a' * 32, 'statut': 'pret', 'taille': 10, 'sha256': 'b' * 64, 'etape': 'Prêt', 'progression': 100, 'erreur': '',
                 'options': json.dumps({'perimetre': {'complet': False, 'non_couvertes': 12}})}
        self.assertIn('INCOMPLET : 12 table(s)', rv._export_ligne('/p', ligne))
        ligne['options'] = json.dumps({'perimetre': {'complet': True, 'non_couvertes': 0}})
        self.assertIn('Périmètre complet', rv._export_ligne('/p', ligne))


# ================================================================================ écrans
class Ecrans(unittest.TestCase):
    def test_B03_B04_ecran_de_rapprochement(self):
        js = (STATIC / 'v56106-envoi.js').read_text(encoding='utf-8')
        for texte in ('Vérifier et décider', 'Renvoyer aux seuls destinataires refusés', 'Je confirme qu’il est parti', 'Renvoyer quand même',
                      'm56106/envoi/rapprochement?id=', 'go.disabled = !coche.checked', 'Relancer le départ'):
            self.assertIn(texte, js)
        self.assertNotIn('innerHTML', js)
        css = (STATIC / 'v56106-envoi.css').read_text(encoding='utf-8')
        self.assertIn('.e108-rapprochement', css)
        self.assertIn(':root[data-theme="dark"] .e108-refuse', css)


if __name__ == '__main__':
    unittest.main(verbosity=2)
