"""5.6.108 (lot P) : pause de l'IA distincte de la suspension des envois programmés.

Demande du cabinet (18/11/2026) : « Séparer la pause de l'IA de la suspension des envois programmés. La 5.6.107 lie encore ces départs à
la pause générale ; faire évoluer ce comportement explicitement avec migration du réglage et visibilité des messages retenus. »
- P1 : deux réglages distincts ; la pause de l'IA ne retient plus un envoi validé et programmé ; la suspension retient les départs
  programmés (avant la réservation et juste avant le SMTP) ; l'envoi immédiat explicite reste possible ; ``pause5629.checkpoint`` inchangé ;
- P2 : migration du réglage (pause de l'avocat au moment de la mise à jour : même effet ; pause posée par la mise à jour : aucun effet),
  une seule fois, journalisée, aucune reprise silencieuse ;
- P3 : visibilité (en-tête d'Aujourd'hui, « Envois programmés »), messages retenus avec leur heure prévue ; à la reprise, décision
  explicite (« Envoyer maintenant », « Reprogrammer », « Annuler »), jamais de départ groupé ; routes, rôles, idempotence ;
- P4 : gardes de B05 (compte revu avant l'effet) et B03/B04 inchangées ;
- P5 : textes en anglais, allemand et espagnol par les dictionnaires des écrans.
Faux IMAP, faux SMTP, horloge simulée, annuaire fictif. Aucun réseau, aucun modèle."""
from datetime import datetime, timedelta
import json
from pathlib import Path
import re
import unittest
from unittest.mock import patch
from zoneinfo import ZoneInfo

from agent import envoi56106 as E, pause5629 as P
from agent.common import Stop
from fake_imap440 import FakeIMAP
from test_v490 import FakeSMTP
import test_v56106_envoi as base

ROOT = Path(__file__).resolve().parents[1]
STATIC = ROOT / 'agent' / 'static'
PARIS = ZoneInfo('Europe/Paris')


def plus_tard(jours=3, heure=9):
    """Heure de départ programmée, au format du formulaire, dans le fuseau du cabinet (jamais date.today())."""
    d = (datetime.now(PARIS) + timedelta(days=jours)).replace(hour=heure, minute=0, second=0, microsecond=0)
    return d.strftime('%Y-%m-%dT%H:%M')


class Base(base.Base):
    """Mêmes données fictives que les tests 5.6.106 (brouillon de DURAND, compte « durand » actif dans l'annuaire)."""
    appel = base.Routes.appel

    def programme(self, jours=3, heure=9):
        out, now = self.file(plus_tard(jours, heure))
        return out, now, self.ligne(out['id'])['depart']

    def nouveau_brouillon(self):
        """Deuxième brouillon fictif (autre UID) pour avoir plusieurs envois dans la file."""
        from test_v440_drafts import raw_draft
        self.n = getattr(self, 'n', 0) + 1
        return FakeIMAP.add('Drafts', raw_draft().replace(b'Bien re', ('Texte %d re' % self.n).encode()), [chr(92) + 'Draft'])

    def file_uid(self, uid, quand):
        from agent import send490
        p = send490.prepare(self.desk, uid, '7')
        import time
        now = time.time() + 2
        return E.mettre_en_file(self.desk, 'durand', 'avocat', p['token'], quand, now=now)

    def etats(self):
        return {r['id']: r['etat'] for r in self.desk.db.execute('SELECT id, etat FROM envoi_file56106')}

    def journal(self, action):
        return [json.loads(r[0]) for r in self.desk.db.execute('SELECT data FROM audit WHERE action=? ORDER BY id', (action,))]


# ================================================================================ P1 : deux réglages distincts
class DeuxReglages(Base):
    def test_P1_01_pause_de_l_ia_ne_retient_plus_un_envoi_programme(self):
        out, now, depart = self.programme()
        self.desk.setting('missions567:pause', True)
        self.assertEqual(E.tick(self.desk, 'b1', depart + 1), [(out['id'], 'parti')])
        self.assertEqual(len(FakeSMTP.sent), 1)

    def test_P1_02_suspension_retient_le_depart_programme_ia_active(self):
        out, now, depart = self.programme()
        self.assertFalse(self.desk.settings('missions567:pause', False))
        E.suspendre(self.desk, 'durand', True, now=now)
        self.assertEqual(E.tick(self.desk, 'b1', depart - 30), [])                   # avant l'heure : rien
        self.assertEqual(E.tick(self.desk, 'b1', depart + 1), [(out['id'], 'suspendu')])
        self.assertEqual(E.tick(self.desk, 'b1', depart + 120), [])                  # retenu : plus examiné à chaque seconde
        self.assertEqual(FakeSMTP.sent, [])
        self.assertTrue(self.in_drafts())
        row = self.ligne(out['id'])
        self.assertEqual((row['etat'], row['motif'], row['etape']), ('suspendu', 'envois_suspendus', ''))
        self.assertEqual(self.desk.db.execute('SELECT COUNT(*) FROM send_log490').fetchone()[0], 0)
        self.assertFalse(self.desk.settings('missions567:pause', False))             # la pause de l'IA n'a pas bougé

    def test_P1_03_envoi_immediat_confirme_part_pendant_la_suspension(self):
        E.suspendre(self.desk, 'durand', True)
        out, now = self.file()                                                       # délai d'annulation de 10 s : envoi immédiat
        self.assertIn('part quand même', out['message'])
        self.assertTrue(out['suspension'])
        self.assertEqual(E.tick(self.desk, 'b1', now + 11), [(out['id'], 'parti')])

    def test_P1_04_suspension_posee_entre_la_reservation_et_le_smtp(self):
        out, now, depart = self.programme()
        appels = {'n': 0}
        reel = E._pause

        def second_appel(desk):   # deuxième contrôle = juste avant le SMTP : l'avocat suspend à cet instant
            appels['n'] += 1
            if appels['n'] == 2:
                P.ecrire_suspension_envois(desk, True, 'durand')
                desk.db.commit()
            return reel(desk)
        with patch.object(E, '_pause', second_appel):
            self.assertEqual(E.tick(self.desk, 'b1', depart + 1), [(out['id'], 'suspendu')])
        self.assertEqual(appels['n'], 2)
        self.assertEqual(FakeSMTP.sent, [])
        self.assertEqual(self.desk.db.execute('SELECT COUNT(*) FROM send_log490').fetchone()[0], 0)
        self.assertEqual(self.ligne(out['id'])['etat'], 'suspendu')

    def test_P1_05_contre_exemple_sans_controle_de_suspension_le_message_part(self):
        out, now, depart = self.programme()
        E.suspendre(self.desk, 'durand', True, now=now)
        with patch.object(P, 'checkpoint_envois', lambda desk: None):              # mutation : garde-fou retiré (en mémoire)
            self.assertEqual(E.tick(self.desk, 'b1', depart + 1), [(out['id'], 'parti')])
        self.assertEqual(len(FakeSMTP.sent), 1)

    def test_P1_06_contre_exemple_controle_avant_le_smtp_seulement_au_debut(self):
        out, now, depart = self.programme()
        reel = E._suspension
        appels = {'n': 0}

        def premier_seulement(desk, row):   # mutation : le contrôle juste avant le SMTP est retiré
            appels['n'] += 1
            if appels['n'] == 1:
                return reel(desk, row)

        def pause_puis_suspension(desk, n={'v': 0}):
            n['v'] += 1
            if n['v'] == 2:
                P.ecrire_suspension_envois(desk, True, 'durand')
                desk.db.commit()
        with patch.object(E, '_suspension', premier_seulement), patch.object(E, '_pause', pause_puis_suspension):
            self.assertEqual(E.tick(self.desk, 'b1', depart + 1), [(out['id'], 'parti')])
        self.assertEqual(len(FakeSMTP.sent), 1)                                      # sans le second contrôle, il serait parti

    def test_P1_07_checkpoint_garde_son_api_et_sa_semantique(self):
        import inspect
        self.assertEqual(list(inspect.signature(P.checkpoint).parameters), ['desk'])
        P.ecrire_suspension_envois(self.desk, True, 'durand')
        self.desk.db.commit()
        self.assertIsNone(P.checkpoint(self.desk))                                  # la suspension des envois n'entre pas dans checkpoint
        with self.assertRaisesRegex(Stop, 'envois_suspendus'):
            P.checkpoint_envois(self.desk)
        self.desk.setting('missions567:pause', True)                               # pause de l'IA : automatismes toujours refusés
        with self.assertRaisesRegex(Stop, 'automatismes_en_pause'):
            P.check_enqueue(self.desk, 'refresh_brief', {'matter': 'DOS-001', 'automatic': 'yes'}, 50, 'automatic')

    def test_P1_08_reglage_illisible_rien_ne_part(self):
        out, now, depart = self.programme()
        for brut in ('"oui"', '{"actif": "non"}', '[1]', 'pas du json'):
            self.desk.db.execute('INSERT OR REPLACE INTO settings VALUES (?,?)', (P.SUSPENSION_ENVOIS, brut))
            self.desk.db.commit()
            with self.assertRaisesRegex(Stop, 'controle_suspension_indisponible'):
                P.checkpoint_envois(self.desk)
            self.assertEqual(E.tick(self.desk, 'b1', depart + 1), [(out['id'], 'en_attente')])
            self.assertEqual(self.ligne(out['id'])['motif'], 'controle_suspension_indisponible')
            self.desk.db.execute("UPDATE envoi_file56106 SET prochain=0 WHERE id=?", (out['id'],))
            self.desk.db.commit()
            vue = E.liste(self.desk, 'durand', depart + 2)['suspension']
            self.assertTrue(vue['actif'] and vue['illisible'])                     # jamais affiché comme levé
        self.assertEqual(FakeSMTP.sent, [])

    def test_P1_09_le_fil_du_travailleur_ne_lit_plus_la_pause_de_l_ia(self):
        src = (ROOT / 'agent' / 'envoi56106.py').read_text(encoding='utf-8')
        depart = src[src.index('def _pause(desk):'):src.index('def _dans_envoyes(')]
        self.assertNotIn("settings('missions567:pause'", depart)
        self.assertIn('checkpoint_envois', (ROOT / 'agent' / 'pause5629.py').read_text(encoding='utf-8'))


# ================================================================================ P2 : migration du réglage
class Migration(Base):
    def ancienne_installation(self, pause=True, par_mise_a_jour=False):
        """État d'une base 5.6.107 : pas de repère de migration ni de suspension ; pause de l'IA posée ; messages retenus par l'ancienne
        pause (motif « automatismes_en_pause »)."""
        self.desk.db.execute('DELETE FROM settings WHERE key IN (?,?)', (P.MIGRATION_ENVOIS, P.SUSPENSION_ENVOIS))
        self.desk.db.commit()
        self.desk.setting('missions567:pause', pause)
        if par_mise_a_jour:
            self.desk.setting('maj5681:pause', {'version': '9.9.99', 'le': '2026-11-18T01:00:00+00:00'})
        prog, now, depart = self.programme()
        uid = self.nouveau_brouillon()
        imm = self.file_uid(uid, '')
        for ident in (prog['id'], imm['id']):
            self.desk.db.execute("UPDATE envoi_file56106 SET motif='automatismes_en_pause', prochain=? WHERE id=?", (now + 30, ident))
        self.desk.db.execute('DELETE FROM settings WHERE key IN (?,?)', (P.MIGRATION_ENVOIS, P.SUSPENSION_ENVOIS))
        self.desk.db.commit()
        return prog, imm, depart

    def test_P2_01_pause_de_l_avocat_conservee_comme_suspension(self):
        prog, imm, depart = self.ancienne_installation()
        rapport = E.migrer_suspension(self.desk, now=depart + 5)
        self.assertEqual((rapport['pause_ia'], rapport['pause_posee_par_la_mise_a_jour'], rapport['envois_suspendus']), (True, False, True))
        self.assertEqual(rapport['retenus'], 2)                                      # heure passée : décision, pas de reprise silencieuse
        etat = P.suspension_envois(self.desk)
        self.assertEqual((etat['actif'], etat['origine']), (True, 'migration'))
        self.assertEqual(self.etats(), {prog['id']: 'suspendu', imm['id']: 'suspendu'})
        self.assertEqual(self.journal('envoi56108_migration')[-1]['retenus'], 2)    # journal de la migration
        self.assertEqual(json.loads(self.desk.db.execute('SELECT value FROM settings WHERE key=?', (P.MIGRATION_ENVOIS,)).fetchone()[0])['retenus'], 2)
        self.desk.setting('missions567:pause', False)                               # l'avocat relance l'IA : les envois restent suspendus
        self.assertEqual(E.tick(self.desk, 'b1', depart + 3600), [])
        self.assertEqual(FakeSMTP.sent, [])
        self.assertTrue(P.suspension_envois(self.desk))
        texte = E.liste(self.desk, 'durand', depart + 3601)['suspension']['texte']
        self.assertIn('mise à jour', texte)

    def test_P2_02_pause_posee_par_la_mise_a_jour_sans_suspension(self):
        prog, imm, depart = self.ancienne_installation(par_mise_a_jour=True)
        rapport = E.migrer_suspension(self.desk, now=depart - 3600)
        self.assertEqual((rapport['pause_posee_par_la_mise_a_jour'], rapport['envois_suspendus'], rapport['retenus']), (True, False, 0))
        self.assertIsNone(P.suspension_envois(self.desk))
        self.assertEqual(self.ligne(prog['id'])['motif'], '')                       # ancien motif effacé : il partira à son heure
        self.assertEqual(self.ligne(prog['id'])['etat'], 'en_attente')

    def test_P2_03_sans_pause_rien_et_migration_une_seule_fois(self):
        prog, imm, depart = self.ancienne_installation(pause=False)
        self.assertFalse(E.migrer_suspension(self.desk, now=depart - 3600)['envois_suspendus'])
        journal = len(self.journal('envoi56108_migration'))
        self.desk.setting('missions567:pause', True)                                # posée après la migration : sans effet sur les envois
        self.assertIsNone(E.migrer_suspension(self.desk))
        E.ensure_schema(self.desk)
        self.assertIsNone(P.suspension_envois(self.desk))
        self.assertEqual(len(self.journal('envoi56108_migration')), journal)        # une seule fois : rien de plus au journal
        self.assertEqual(sorted(E.tick(self.desk, 'b1', depart + 1)), sorted([(prog['id'], 'parti'), (imm['id'], 'parti')]))

    def test_P2_04_migration_au_demarrage_jamais_pendant_une_lecture(self):
        self.ancienne_installation()
        avant = self.desk.db.total_changes
        E.liste(self.desk, 'durand')
        from agent import accueil5689
        accueil5689.pause_html(self.desk, '/p', 'durand')
        self.assertEqual(self.desk.db.total_changes, avant)                         # R05 : aucune écriture pendant une lecture
        self.assertIsNone(self.desk.settings(P.MIGRATION_ENVOIS, None))
        E.ensure_schema(self.desk)                                                   # démarrage (Desk._prepare)
        self.assertTrue(P.suspension_envois(self.desk))
        src = (ROOT / 'agent' / 'desk.py').read_text(encoding='utf-8')
        self.assertIn('ensure_envoi56106_schema(self)', src)

    def test_P2_05_contre_exemple_migration_naive_reprise_silencieuse(self):
        prog, imm, depart = self.ancienne_installation()
        with patch.object(P, 'migrer_suspension_envois', lambda desk, now=None: {'envois_suspendus': False, 'pause_ia': True}):
            E.migrer_suspension(self.desk, now=depart + 5)                          # mutation : la pause de l'avocat n'est pas reprise
        self.assertEqual(sorted(x for _, x in E.tick(self.desk, 'b1', depart + 6)), ['parti', 'parti'])


# ================================================================================ P3 : visibilité, décisions, routes
class Visibilite(Base):
    def test_P3_01_liste_des_retenus_avec_leur_heure_prevue(self):
        out, now, depart = self.programme()
        futur = self.file_uid(self.nouveau_brouillon(), plus_tard(5, 10))
        E.suspendre(self.desk, 'durand', True, now=now)
        E.tick(self.desk, 'b1', depart + 1)
        vue = E.liste(self.desk, 'durand', depart + 2)
        self.assertTrue(vue['suspension']['actif'])
        self.assertEqual(vue['suspension']['retenus'], 1)
        par_id = {x['id']: x for x in vue['elements']}
        retenu, a_venir = par_id[out['id']], par_id[futur['id']]
        self.assertEqual((retenu['etat'], retenu['suspension'], retenu['actions']), ('suspendu', True, ['maintenant', 'reprogrammer', 'annuler']))
        self.assertTrue(retenu['depart_libelle'] and retenu['prevu_libelle'])
        self.assertIn('suspendus', retenu['motif_libelle'])
        self.assertTrue(a_venir['suspension'])                                      # sera retenu à son heure si la suspension dure
        self.assertEqual(a_venir['etat'], 'en_attente')
        self.assertTrue(vue['suspension']['texte'].startswith('Envois programmés suspendus depuis le '))
        self.assertIn('pause_ia', vue)

    def test_P3_02_reprise_heure_passee_decision_jamais_de_depart_groupe(self):
        a, now, depart = self.programme(jours=3, heure=9)
        b = self.file_uid(self.nouveau_brouillon(), plus_tard(3, 10))
        c = self.file_uid(self.nouveau_brouillon(), plus_tard(9, 10))
        dep_b, dep_c = self.ligne(b['id'])['depart'], self.ligne(c['id'])['depart']
        E.suspendre(self.desk, 'durand', True, now=now)
        E.tick(self.desk, 'b1', depart + 1)                                         # a retenu ; b dû mais pas encore examiné
        out = E.suspendre(self.desk, 'durand', False, now=dep_b + 60)
        self.assertEqual(out['a_decider'], 2)
        self.assertIn('rien n’est parti automatiquement', out['message'])
        self.assertEqual(self.etats(), {a['id']: 'suspendu', b['id']: 'suspendu', c['id']: 'en_attente'})
        self.assertEqual(E.tick(self.desk, 'b1', dep_b + 61), [])                   # aucun départ groupé
        self.assertEqual(FakeSMTP.sent, [])
        item = E.etat(self.desk, 'durand', a['id'], dep_b + 62)
        self.assertEqual(item['motif'], 'decision_apres_suspension')
        self.assertIn('Choisissez', item['motif_libelle'])
        self.assertEqual(E.tick(self.desk, 'b1', dep_c + 1), [(c['id'], 'parti')])  # un envoi encore à venir part à son heure
        self.assertEqual(self.journal('envoi56108_suspension')[-1]['a_decider'], 2)

    def test_P3_03_reprise_change_la_version_une_passe_commencee_avant_ne_reserve_pas(self):
        out, now, depart = self.programme()
        E.suspendre(self.desk, 'durand', True, now=now)
        vue_avant = self.ligne(out['id'])                                          # ligne lue par une passe avant la reprise
        E.suspendre(self.desk, 'durand', False, now=depart + 5)
        self.assertFalse(E._reserver(self.desk, vue_avant, 'autre', depart + 6))
        self.assertEqual(FakeSMTP.sent, [])

    def test_P3_04_decisions_envoyer_reprogrammer_annuler(self):
        a, now, depart = self.programme()
        b = self.file_uid(self.nouveau_brouillon(), plus_tard(3, 10))
        c = self.file_uid(self.nouveau_brouillon(), plus_tard(3, 11))
        E.suspendre(self.desk, 'durand', True, now=now)
        fin = self.ligne(c['id'])['depart'] + 60
        E.suspendre(self.desk, 'durand', False, now=fin)
        self.assertEqual(set(self.etats().values()), {'suspendu'})
        with self.assertRaisesRegex(Stop, 'action_impossible'):                     # un retenu se décide, il ne se retire pas
            E.retirer(self.desk, 'durand', a['id'])
        E.maintenant(self.desk, 'durand', a['id'], now=fin + 1)                    # Envoyer maintenant
        r = E.modifier(self.desk, 'durand', b['id'], plus_tard(6, 9), now=fin + 1)   # Reprogrammer
        self.assertIn('Envoi programmé le', r['message'])
        annule = E.annuler(self.desk, 'durand', c['id'], now=fin + 1)              # Annuler : le brouillon reste
        self.assertTrue(annule['annule'])
        self.assertEqual(E.tick(self.desk, 'b1', fin + 2), [(a['id'], 'parti')])
        self.assertEqual(self.etats()[b['id']], 'en_attente')
        self.assertEqual(E.tick(self.desk, 'b1', self.ligne(b['id'])['depart'] + 1), [(b['id'], 'parti')])
        self.assertEqual(len(FakeSMTP.sent), 2)

    def test_P3_05_envoi_immediat_pendant_la_suspension_demande_une_confirmation(self):
        out, now, depart = self.programme()
        self.appel('suspendre', {'actif': True})
        refus = self.appel('maintenant', {'id': out['id'], 'version': out['version']})
        self.assertEqual((refus[0], refus[1]['error']), ('409 Conflict', 'confirmation_suspension_requise'))
        self.assertEqual(self.ligne(out['id'])['etat'], 'en_attente')               # rien n'a changé
        ok = self.appel('maintenant', {'id': out['id'], 'version': out['version'], 'hors_suspension': True})
        self.assertEqual(ok[0], '200 OK', ok[1])
        self.assertTrue(json.loads(self.ligne(out['id'])['options'])['immediat'])
        import time
        self.assertEqual(E.tick(self.desk, 'b1', time.time() + 1), [(out['id'], 'parti')])   # la suspension ne le retient pas

    def test_P3_06_contre_exemple_sans_confirmation_de_plus(self):
        out, now, depart = self.programme()
        self.appel('suspendre', {'actif': True})
        with patch.object(E, 'IMMEDIATS', ()):                                       # mutation : garde retirée (en mémoire)
            self.assertEqual(self.appel('maintenant', {'id': out['id'], 'version': out['version']})[0], '200 OK')

    def test_P3_07_route_suspendre_roles_droit_idempotence(self):
        from agent import membres5650
        from agent.standalone_auth import allowed
        self.assertTrue(allowed('avocat', 'POST', '/api440/' + E.PREFIXE + 'suspendre'))
        self.assertTrue(allowed('administrateur', 'POST', '/api440/' + E.PREFIXE + 'suspendre'))
        self.assertFalse(allowed('assistant', 'POST', '/api440/' + E.PREFIXE + 'suspendre'))
        self.assertFalse(E.allowed('avocat', 'GET', E.PREFIXE + 'suspendre'))
        self.assertEqual(self.appel('suspendre', method='GET')[0], '404 Not Found')
        self.assertEqual(membres5650.category('POST', '/api440/' + E.PREFIXE + 'suspendre'), 'envoyer')
        status, body, raw = self.appel('suspendre', {'actif': True, 'cle': 'cle-suspendre-0001'})
        self.assertEqual((status, body['actif']), ('200 OK', True))
        self.assertIn(('Cache-Control', 'private, no-store'), raw['headers'])
        self.assertEqual(self.appel('suspendre', {'actif': True, 'cle': 'cle-suspendre-0001'})[1], body)   # même clé : même reçu
        self.assertEqual(len(self.journal('envoi56108_suspension')), 1)
        self.assertEqual(self.appel('suspendre', {'actif': False, 'cle': 'cle-suspendre-0001'})[0], '409 Conflict')
        for mauvais in ('oui', 1, None):
            st, err, _ = self.appel('suspendre', {'actif': mauvais})
            self.assertEqual((st, err['error']), ('400 Bad Request', 'suspension_invalide'))
        self.assertEqual(self.appel('suspendre', {'actif': True}, role='assistant')[0], '403 Forbidden')
        self.assertTrue(P.suspension_envois(self.desk))
        self.assertFalse(self.desk.settings('missions567:pause', False))            # distinct de la pause de l'IA

    def test_P3_08_lecture_sans_ecriture(self):
        out, now, depart = self.programme()
        E.suspendre(self.desk, 'durand', True, now=now)
        E.tick(self.desk, 'b1', depart + 1)
        avant = self.desk.db.total_changes
        for _ in range(2):
            self.assertEqual(self.appel('liste', method='GET')[0], '200 OK')
            self.assertEqual(self.appel('etat', method='GET', args={'id': out['id']})[0], '200 OK')
        self.assertEqual(self.desk.db.total_changes, avant)

    def test_P3_09_en_tete_d_aujourd_hui_deux_commandes_et_leur_etat(self):
        from agent import accueil5689
        html = accueil5689.pause_html(self.desk, '/p', 'durand')
        for attendu in ('id="a92-pause"', 'Mettre l’IA en pause', 'id="p108-envois"', 'data-actif="0"', 'Suspendre les envois programmés',
                        'aria-pressed="false"', 'id="p108-retenus"'):
            self.assertIn(attendu, html)
        self.assertRegex(html, r'id="p108-retenus"[^>]*hidden')
        out, now, depart = self.programme()
        E.suspendre(self.desk, 'durand', True, now=now)
        E.tick(self.desk, 'b1', depart + 1)
        self.desk.setting('missions567:pause', True)
        html = accueil5689.pause_html(self.desk, '/p', 'durand')
        for attendu in ('IA en pause — reprendre', 'data-actif="1"', 'Envois programmés suspendus — reprendre', '1 envoi(s) retenu(s) à décider',
                        '/p/courriels?vue=boite#envois-programmes'):
            self.assertIn(attendu, html)
        self.assertEqual(accueil5689.pause_html(self.desk, '/p', 'martin').count('retenu(s)'), 1)   # cloisonnement : 0 pour un autre compte
        self.assertRegex(accueil5689.pause_html(self.desk, '/p', 'martin'), r'id="p108-retenus"[^>]*hidden')

    def test_P3_10_page_aujourd_hui_montre_les_deux_commandes(self):
        from agent import cockpit530
        html = cockpit530.page(self.desk, '/p', 'durand', 'avocat')
        tete = html[html.index('<header class="c530-top'):html.index('</header>')]
        self.assertIn('id="a92-pause"', tete)
        self.assertIn('id="p108-envois"', tete)

    def test_P3_11_scripts(self):
        js = (STATIC / 'v56106-envoi.js').read_text(encoding='utf-8')
        for attendu in ("closest('#p108-envois')", "'m56106/envoi/suspendre'", "'m568/automatismes/pause'", 'hors_suspension',
                        "'reprogrammer'", '#envois-programmes', 'p108-retenus-liste'):
            self.assertIn(attendu, js)
        self.assertNotIn('innerHTML', js)
        v5614 = (STATIC / 'v5614.js').read_text(encoding='utf-8')
        self.assertIn("x.id === 'a92-pause' ? 'Mettre l’IA en pause' : 'Pause de l’IA'", v5614)
        css = (STATIC / 'v56106-envoi.css').read_text(encoding='utf-8')
        for sel in ('.p108-pauses', '.p108-retenus-liste', ':root[data-theme="dark"] .p108-retenus', 'focus-visible'):
            self.assertIn(sel, css)

    def test_P3_12_programmer_pendant_la_suspension_annonce_la_retenue(self):
        E.suspendre(self.desk, 'durand', True)
        out, now = self.file(plus_tard())
        self.assertIn('attendra votre décision', out['message'])


# ================================================================================ P4 : gardes B05, B03, B04 inchangées
class GardesInchangees(Base):
    def annuaire(self, sql, *params):
        from agent import connexion5648
        db = connexion5648.connect(self.f.c['state_dir'])
        db.execute(sql, params)
        db.commit()
        db.close()

    def test_P4_01_compte_revu_avant_l_effet_apres_la_decision(self):
        out, now, depart = self.programme()
        E.suspendre(self.desk, 'durand', True, now=now)
        E.tick(self.desk, 'b1', depart + 1)
        E.suspendre(self.desk, 'durand', False, now=depart + 10)
        E.maintenant(self.desk, 'durand', out['id'], now=depart + 11)
        self.annuaire('UPDATE users SET active=0 WHERE ident=?', 'durand')
        self.assertEqual(E.tick(self.desk, 'b1', depart + 12), [(out['id'], 'retenu')])
        self.assertEqual(self.ligne(out['id'])['motif'], 'compte_desactive')
        self.assertEqual(FakeSMTP.sent, [])

    def test_P4_02_rapprochement_inchange_par_la_suspension(self):
        src = (ROOT / 'agent' / 'envoi56106.py').read_text(encoding='utf-8')
        self.assertIn("if _nature(row) != 'partiel' or log is None or log['status'] != 'partiel':", src)
        self.assertIn("if doublon is not True:", src)
        self.assertIn("if etape == 'reserve' and code in COMPTE:", src)
        self.assertEqual(E.IMMEDIATS, ('maintenant', 'renvoyer_refuses', 'renvoyer_quand_meme'))

    def test_P4_03_point_de_controle_commun_toujours_appele(self):
        out, now, depart = self.programme()
        appels = []

        def checkpoint(desk):
            appels.append(1)
            raise Stop('controle_pause_indisponible')
        with patch('agent.pause5629.checkpoint', checkpoint):
            self.assertEqual(E.tick(self.desk, 'b1', depart + 1), [(out['id'], 'en_attente')])
        self.assertTrue(appels)
        self.assertEqual(FakeSMTP.sent, [])


# ================================================================================ P5 : langues
class Langues(unittest.TestCase):
    def test_P5_textes_traduits_en_de_es(self):
        from agent import ecrans5632 as T
        import test_v56107_langues as L
        tables = {l: T.dictionary(l) for l in L.LANGUES}
        phrases = [v for k, v in E.MESSAGES.items() if k in ('envois_suspendus', 'decision_apres_suspension', 'controle_suspension_indisponible',
                                                              'confirmation_suspension_requise', 'suspension_invalide')]
        phrases += [E.ETATS['suspendu'], 'Mettre l’IA en pause', 'Pause de l’IA et envois programmés', 'Suspendre les envois programmés',
                    'Envois programmés suspendus — reprendre', '2 envoi(s) retenu(s) à décider', 'Envois programmés actifs.',
                    'Envois programmés suspendus.', 'Envois programmés repris : les prochains départs auront lieu à leur heure.',
                    'Envois programmés repris. 2 message(s) dont l’heure est passée pendant la suspension attendent votre décision dans '
                    '« Envois programmés » : rien n’est parti automatiquement.', 'Reprogrammer',
                    'Envoi dans 10 s. Les envois programmés sont suspendus ; cet envoi immédiat part quand même.']
        phrases += [s for _, s in L.litteraux_js(STATIC / 'v56106-envoi.js') if L.texte_visible_js(s)]
        manquants = ['%s (%s)' % (s, l) for s in phrases for l, t in tables.items() if T.phrase(s, t) is None]
        self.assertEqual(manquants, [])

    def test_P5_en_tete_traduit_sans_francais(self):
        from agent import ecrans5632 as T
        import test_v56107_langues as L
        html = ('<span class="p108-pauses" role="group" aria-label="Pause de l’IA et envois programmés"><button>Mettre l’IA en pause</button>'
                '<button title="Envois programmés actifs.">Suspendre les envois programmés</button><a hidden>3 envoi(s) retenu(s) à décider</a></span>')
        for lang in L.LANGUES:
            self.assertEqual(L.reste_francais(L.visibles(T.translate_html(html, lang))), [], lang)
        self.assertTrue(L.reste_francais(L.visibles(html)))                         # contre-exemple : le détecteur voit le français


if __name__ == '__main__':
    unittest.main()
