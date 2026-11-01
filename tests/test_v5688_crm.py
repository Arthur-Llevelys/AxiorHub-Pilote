"""5.6.88, lot crm : palette unique, registres publics, veille des sociétés, page Aujourd'hui personnelle, note commune, mémoire proposée.

Données fictives (DURAND, MARTIN, ZORVAL, KERVILAC, DOS-001, example.test). Aucun appel réseau : les registres répondent par un faux client,
dont la forme reprend des enregistrements relevés sur les sources publiques le 2026-11-01.
"""
from datetime import datetime, timedelta, timezone
import json
from pathlib import Path
import re
import unittest
from unittest.mock import patch

from agent import cockpit530, crm5636, crm5688, memoire5688, messagerie5650 as mx, membres5650 as mb, notes5688, palette5688, pseudo540, registres5688 as reg, tableau5688, web5688
from agent.common import Stop, private_json
from agent.desk import Desk
from agent.ia540 import apply_mixed
from agent.legal_memory import memory_records
from agent.model import Model, routed_config
import test_agent as fixtures
import test_v540 as t540
import test_v5648 as t48

STATIC = Path(crm5688.__file__).parent / 'static'
SIREN = '000010058'
AUTRE = '000010066'
BLANC = {'results': [], 'total_count': 0}
ANNUAIRE = {'results': [{'siren': SIREN, 'nom_complet': 'ZORVAL (ZV)', 'nom_raison_sociale': 'ZORVAL', 'sigle': 'ZV', 'etat_administratif': 'A', 'date_creation': '2018-03-02', 'nature_juridique': '5710',
                         'siege': {'siret': SIREN + '00012', 'adresse': '12 RUE DES LILAS 35000 VALBRUNE', 'numero_voie': '12', 'type_voie': 'RUE', 'libelle_voie': 'DES LILAS', 'code_postal': '35000', 'libelle_commune': 'VALBRUNE'},
                         'dirigeants': [{'nom': 'DURAND', 'prenoms': 'CLAIRE', 'qualite': 'Présidente', 'type_dirigeant': 'personne physique'},
                                        {'denomination': 'KERVILAC CONSEIL', 'siren': AUTRE, 'qualite': 'Commissaire aux comptes', 'type_dirigeant': 'personne morale'}]}], 'total_results': 1}


def rec(ident='B202602103408', famille='modification', lib='Modifications diverses', sirens=(SIREN,), **more):
    out = {'id': ident, 'dateparution': '2026-10-31', 'familleavis': famille, 'familleavis_lib': lib, 'typeavis': 'annonce', 'typeavis_lib': 'Avis initial', 'commercant': 'ZORVAL', 'ville': 'Valbrune',
           'tribunal': 'Greffe de Valbrune', 'registre': [x for s in sirens for x in (s, s[:3] + ' ' + s[3:6] + ' ' + s[6:])], 'url_complete': 'https://www.bodacc.fr/pages/annonces-commerciales-detail/?q.id=id:' + ident,
           'listepersonnes': None, 'jugement': None, 'modificationsgenerales': None, 'radiationaurcs': None, 'depot': None, 'acte': None, 'listeprecedentproprietaire': None, 'listeprecedentexploitant': None}
    out.update(more)
    return out


def pers(siren=SIREN, greffe='Valbrune', capital='25720.00'):
    spaced = siren[:3] + ' ' + siren[3:6] + ' ' + siren[6:]
    return json.dumps({'personne': {'typePersonne': 'pm', 'numeroImmatriculation': {'numeroIdentification': spaced, 'codeRCS': 'RCS', 'nomGreffeImmat': greffe}, 'denomination': 'ZORVAL',
                                    'formeJuridique': 'Société par actions simplifiée', 'capital': {'montantCapital': capital, 'devise': 'EUR'}}})


class Faux:
    """Client HTTP des registres : réponses par source, adresses conservées, aucune sortie réseau."""

    def __init__(self, bodacc=None, annuaire=None):
        self.bodacc, self.annuaire, self.urls = BLANC if bodacc is None else bodacc, ANNUAIRE if annuaire is None else annuaire, []

    def __call__(self, origin):
        return self

    def request(self, method, url, data=None, headers=None, limit=0):
        self.urls.append(url)
        reply = self.bodacc if 'bodacc' in url else self.annuaire
        if isinstance(reply, Exception):
            raise reply
        return reply if isinstance(reply, bytes) else json.dumps(reply).encode('utf-8')


class Base(unittest.TestCase):
    def setUp(self):
        self.f = fixtures.EngineTests('test_observation_has_no_mail_write')
        self.f.setUp()
        self.addCleanup(self.f.doCleanups)
        self.d = Desk(self.f.c)
        self.state = self.f.c['state_dir']
        self.http = Faux()
        p = patch.object(reg, 'HTTP_FACTORY', self.http)
        p.start()
        self.addCleanup(p.stop)
        self.addCleanup(mb.clear)
        crm5636.schema(self.d)
        crm5688.ensure_schema(self.d)
        self.members = {o: {'owner': o, 'name': n, 'role': r} for o, n, r in (('cabinet', 'Admin', 'administrateur'), ('avocate@example.test', 'Claire Durand', 'avocat'), ('assistant@example.test', 'Paul Martin', 'assistant'))}

    def matters(self):
        data = [{'id': 'DOS-001', 'client_name': 'DURAND', 'path': '/Dossiers/DURAND', 'references': ['RG 24/01234']}, {'id': 'DOS-002', 'client_name': 'ORLANDEC', 'path': '/Dossiers/ORLANDEC', 'references': []}]
        private_json(self.f.matter_file, data)

    def reserve(self, matter='DOS-002', owners=('avocate@example.test',)):
        self.matters()
        mb.set_reserve(self.state, matter, list(owners))

    def contact(self, **kw):
        return crm5636.save(self.d, {'name': 'ZORVAL', 'kind': 'societe', 'category': 'client', **kw})


# ------------------------------------------------------------------------------------------------ registres
class Registres(Base):
    def test_filtre_officiel_et_champs_json_decodes_avec_tolerance(self):
        self.http.bodacc = {'total_count': 2, 'results': [
            rec(jugement='{"nature": "Jugement d\'ouverture", "date": "2026-10-22"}', modificationsgenerales='pas du json', listepersonnes=pers()),
            rec('B2', sirens=(SIREN[:8] + '0',))]}
        out = reg.annonces(self.d, SIREN, familles=['collective', 'radiation'])
        where = re.search(r'where=([^&]+)', self.http.urls[0]).group(1)
        from urllib.parse import unquote_plus
        self.assertIn('registre like "%s"' % SIREN, unquote_plus(where))
        self.assertIn('familleavis in ("collective","radiation")', unquote_plus(where))
        self.assertEqual([a['id'] for a in out['annonces']], ['B202602103408'])   # le filtre de la source est approché : SIREN exact seulement
        a = out['annonces'][0]
        self.assertEqual(a['jugement']['nature'], "Jugement d'ouverture")
        self.assertEqual(a['modifications'], {'texte': 'pas du json'})
        self.assertEqual(a['personnes'][0]['denomination'], 'ZORVAL')
        self.assertEqual(a['radiation'], {})

    def test_panne_jamais_confondue_avec_rien_a_signaler(self):
        self.assertEqual(reg.annonces(self.d, SIREN)['annonces'], [])             # réponse vide bien formée : rien à signaler
        for reply in (OSError('délai'), Stop('http_503'), b'<html>maintenance</html>', {'erreur': 1}, {'results': 'x'}, {'results': [], 'total_count': 4}):
            self.http.bodacc = reply
            with self.assertRaises(reg.SourceIndisponible):
                reg.annonces(self.d, SIREN, depuis='2026-01-01', force=True)

    def test_panne_annuaire_distincte_de_entreprise_inconnue(self):
        self.http.annuaire = {'results': []}
        self.assertIsNone(reg.entreprise(self.d, SIREN))
        self.http.annuaire = OSError('délai')
        with self.assertRaises(reg.SourceIndisponible):
            reg.entreprise(self.d, SIREN, force=True)

    def test_cache_puis_lecture_forcee_et_panne_non_gardee(self):
        reg.annonces(self.d, SIREN)
        reg.annonces(self.d, SIREN)
        self.assertEqual(len(self.http.urls), 1)
        reg.annonces(self.d, SIREN, force=True)
        self.assertEqual(len(self.http.urls), 2)
        self.d.db.execute('DELETE FROM registres_cache5688')
        self.http.bodacc = OSError('délai')
        with self.assertRaises(reg.SourceIndisponible):
            reg.annonces(self.d, SIREN)
        self.assertEqual(self.d.db.execute('SELECT COUNT(*) FROM registres_cache5688').fetchone()[0], 0)

    def test_cache_expire_apres_un_jour_et_lignes_purgees(self):
        reg.annonces(self.d, SIREN)
        self.d.db.execute('UPDATE registres_cache5688 SET lu=lu-?', (reg.CACHE_TTL + 1,))
        reg.annonces(self.d, SIREN)
        self.assertEqual(len(self.http.urls), 2)
        self.d.db.execute('UPDATE registres_cache5688 SET lu=lu-?', (reg.CACHE_KEEP + 1,))
        reg.annonces(self.d, AUTRE)
        self.assertEqual(self.d.db.execute('SELECT COUNT(*) FROM registres_cache5688').fetchone()[0], 1)

    def test_siren_zero_du_bodacc_refuse_et_famille_inconnue(self):
        for bad in ('000000000', '12345', 'abc', '" or 1=1'):
            with self.assertRaises(Stop):
                reg.annonces(self.d, bad)
        with self.assertRaises(Stop):
            reg.annonces(self.d, SIREN, familles=['") or ("1"="1'])
        self.assertEqual(self.http.urls, [])

    def test_siret_ramene_au_siren(self):
        self.assertEqual(reg.norm_siren('000 010 058 00012'), SIREN)

    def test_seules_les_sources_publiques_sont_jointes(self):
        with self.assertRaises(Stop):
            reg._lire(self.d, 'bodacc', 'https://collecte.example.test/x?siren=' + SIREN, lambda d: True)
        self.assertEqual(self.http.urls, [])

    def test_lien_officiel_seul_repris(self):
        self.http.bodacc = {'total_count': 1, 'results': [rec(url_complete='https://piege.example.test/x')]}
        self.assertTrue(reg.annonces(self.d, SIREN)['annonces'][0]['url'].startswith('https://www.bodacc.fr/'))

    def test_capital_et_rcs_de_cette_societe_pas_de_l_autre_partie(self):
        self.http.bodacc = {'total_count': 2, 'results': [rec('B1', 'vente', 'Ventes et cessions', (SIREN, AUTRE), listepersonnes=pers(AUTRE, 'Rouen', '999')), rec('B2', listepersonnes=pers())]}
        ent = reg.entreprise(self.d, SIREN)
        self.assertEqual(ent['capital'], {'montant': '25720.00', 'devise': 'EUR'})
        self.assertEqual(ent['rcs'], 'RCS Valbrune 000 010 058')
        self.assertEqual(ent['annonce_id'], 'B2')
        self.assertEqual(ent['dirigeants'][0], {'nom': 'DURAND', 'prenoms': 'CLAIRE', 'qualite': 'Présidente', 'type': 'personne physique', 'siren': ''})

    def test_panne_du_complement_annoncee_sur_la_fiche(self):
        self.http.bodacc = OSError('délai')
        ent = reg.entreprise(self.d, SIREN)
        self.assertTrue(ent['complements_indisponibles'])
        self.assertIsNone(ent['capital'])


# ------------------------------------------------------------------------------------------------ fiche
class Fiche(Base):
    def champs(self, prop):
        return {r['champ']: r for r in prop['champs']}

    def setUp(self):
        super().setUp()
        self.http.bodacc = {'total_count': 1, 'results': [rec(listepersonnes=pers())]}

    def test_proposition_ne_modifie_rien_et_ne_donne_que_ce_que_la_source_donne(self):
        c = self.contact(address='1 place du Marché')
        self.http.bodacc = BLANC
        prop = crm5688.proposition(self.d, c['id'], SIREN)
        rows = self.champs(prop)
        self.assertEqual(rows['adresse']['etat'], 'different')
        self.assertEqual(rows['nom']['etat'], 'identique')
        self.assertEqual(rows['siren']['etat'], 'vide')
        self.assertNotIn('capital', rows)
        self.assertNotIn('rcs', rows)
        self.assertEqual(crm5636.get(self.d, c['id'])['address'], '1 place du Marché')

    def test_valeur_saisie_jamais_ecrasee_sans_remplacer(self):
        c = self.contact(address='1 place du Marché')
        prop = crm5688.proposition(self.d, c['id'], SIREN)
        out = crm5688.confirmer(self.d, 'avocate@example.test', c['id'], SIREN, {'adresse', 'siren', 'capital'}, set(), prop['jeton'])
        fiche = crm5636.get(self.d, c['id'])
        self.assertEqual(fiche['address'], '1 place du Marché')
        self.assertEqual(fiche['siren'], SIREN)
        self.assertEqual(out['ignores'], ['Adresse du siège'])
        prop = crm5688.proposition(self.d, c['id'], SIREN)
        crm5688.confirmer(self.d, 'avocate@example.test', c['id'], SIREN, {'adresse'}, {'adresse'}, prop['jeton'])
        self.assertEqual(crm5636.get(self.d, c['id'])['address'], '12 RUE DES LILAS 35000 VALBRUNE')

    def test_champ_non_coche_ignore(self):
        c = self.contact()
        prop = crm5688.proposition(self.d, c['id'], SIREN)
        crm5688.confirmer(self.d, 'cabinet', c['id'], SIREN, {'siren'}, set(), prop['jeton'])
        self.assertEqual(crm5636.get(self.d, c['id'])['address'], '')
        self.assertEqual(set(crm5688.provenance(self.d, c['id'])), {'siren'})

    def test_fiche_modifiee_entre_temps_refusee(self):
        c = self.contact()
        prop = crm5688.proposition(self.d, c['id'], SIREN)
        crm5636.save(self.d, {**{k: c[k] for k in crm5636.FIELDS}, 'id': c['id'], 'phone': '0100000000'})
        with self.assertRaises(Stop) as ctx:
            crm5688.confirmer(self.d, 'cabinet', c['id'], SIREN, {'siren'}, set(), prop['jeton'])
        self.assertEqual(str(ctx.exception), 'version_en_conflit')
        self.assertEqual(crm5636.get(self.d, c['id'])['siren'], '')

    def test_source_date_et_auteur_conserves(self):
        c = self.contact()
        prop = crm5688.proposition(self.d, c['id'], SIREN)
        crm5688.confirmer(self.d, 'avocate@example.test', c['id'], SIREN, {'capital', 'rcs', 'dirigeants'}, set(), prop['jeton'])
        saved = crm5688.provenance(self.d, c['id'])
        self.assertEqual(saved['capital']['valeur'], '25720.00 EUR')
        self.assertEqual(saved['rcs']['valeur'], 'RCS Valbrune 000 010 058')
        self.assertIn('Présidente : DURAND CLAIRE', saved['dirigeants']['valeur'])
        self.assertIn('Recherche d’entreprises', saved['capital']['source'])
        self.assertIn('BODACC', saved['capital']['source'])
        self.assertEqual(saved['capital']['confirme_par'], 'avocate@example.test')
        self.assertTrue(saved['capital']['lu_le'].startswith('20'))

    def test_nom_d_une_personne_va_dans_societe(self):
        c = crm5636.save(self.d, {'name': 'MARTIN', 'first_name': 'Paul', 'kind': 'personne', 'category': 'client'})
        prop = crm5688.proposition(self.d, c['id'], SIREN)
        crm5688.confirmer(self.d, 'cabinet', c['id'], SIREN, {'nom'}, set(), prop['jeton'])
        fiche = crm5636.get(self.d, c['id'])
        self.assertEqual((fiche['name'], fiche['company']), ('MARTIN', 'ZORVAL'))

    def test_fiche_d_un_dossier_reserve_refusee(self):
        self.reserve()
        c = self.contact(matter='DOS-002', role='client')
        mb.set_viewer({'DOS-002'})
        with self.assertRaises(Stop):
            crm5688.proposition(self.d, c['id'], SIREN)
        self.assertEqual(self.http.urls, [])

    def test_page_echappe_les_donnees_du_registre_et_signale_la_panne(self):
        c = self.contact()
        hostile = json.loads(json.dumps(ANNUAIRE))
        hostile['results'][0]['nom_raison_sociale'] = '<script>alert(1)</script>'
        self.http.annuaire = hostile
        html = crm5688.fiche_html(self.d, '', crm5636.get(self.d, c['id']), {'registre': SIREN})
        self.assertNotIn('<script>alert', html)
        self.assertIn('Confirmer la reprise', html)
        self.http.annuaire = OSError('délai')
        self.d.db.execute('DELETE FROM registres_cache5688')
        html = crm5688.fiche_html(self.d, '', crm5636.get(self.d, c['id']), {'registre': SIREN})
        self.assertIn('Le registre ne répond pas', html)

    def test_recherche_par_nom_propose_des_candidats(self):
        c = self.contact()
        html = crm5688.fiche_html(self.d, '', crm5636.get(self.d, c['id']), {'registre': 'ZORVAL'})
        self.assertIn('registre=' + SIREN, html)
        self.assertIn('Choisissez l’entreprise', html)


# ------------------------------------------------------------------------------------------------ veille
class Veille(Base):
    def suivre(self, siren=SIREN, events=None, nom='ZORVAL'):
        return crm5688.suivre(self.d, 'avocate@example.test', {'siren': siren, 'nom': nom, 'evenements': events or list(crm5688.EVENEMENTS)})

    def passage(self, **kw):
        return crm5688.verifier(self.d, attente=lambda s: None, **kw)

    def reculer(self, heures=25):
        when = (datetime.now(timezone.utc) - timedelta(hours=heures)).isoformat(timespec='seconds')
        self.d.db.execute("UPDATE societes5688 SET dernier_ok=?,dernier_essai=?", (when, when))
        self.d.db.execute('DELETE FROM registres_cache5688')

    def test_classement_sur_le_champ_officiel_jamais_sur_le_nom(self):
        norm = lambda r: reg.annonce(r)
        self.assertEqual(crm5688.classer(norm(rec(famille='collective'))), ['procedure_collective'])
        self.assertEqual(crm5688.classer(norm(rec(famille='vente'))), ['cession'])
        self.assertEqual(crm5688.classer(norm(rec(famille='radiation'))), ['radiation'])
        self.assertEqual(crm5688.classer(norm(rec(famille='creation', commercant='RADIATION ET CESSION SAS'))), [])
        self.assertEqual(crm5688.classer(norm(rec(famille='dpc', commercant='LIQUIDATION DIRIGEANT'))), [])
        self.assertEqual(crm5688.classer(norm(rec(modificationsgenerales='{"descriptif": "transfert du siège social."}'))), ['siege'])
        self.assertEqual(crm5688.classer(norm(rec(modificationsgenerales='{"descriptif": "Modification survenue sur l\'administration."}'))), ['dirigeant'])
        self.assertEqual(crm5688.classer(norm(rec(modificationsgenerales='{"descriptif": "Modification survenue sur l\'administration, transfert du siège social."}'))), ['siege', 'dirigeant'])
        self.assertEqual(crm5688.classer(norm(rec(modificationsgenerales='{"descriptif": "Modification survenue sur le capital."}', commercant='SIEGE DIRIGEANT'))), [])
        self.assertEqual(crm5688.classer(norm(rec(modificationsgenerales='illisible'))), [])

    def test_une_seule_alerte_par_annonce_et_lien_officiel(self):
        self.suivre()
        self.http.bodacc = {'total_count': 1, 'results': [rec(famille='collective', lib='Procédures collectives', jugement='{"nature": "Jugement d\'ouverture de redressement judiciaire"}')]}
        self.assertEqual(self.passage()['nouvelles'], 1)
        self.reculer()
        self.assertEqual(self.passage()['nouvelles'], 0)
        rows = crm5688.alertes(self.d)
        self.assertEqual(len(rows), 1)
        self.assertTrue(rows[0]['url'].startswith('https://www.bodacc.fr/'))
        html = crm5688.today_html(self.d, '')
        self.assertIn('Voir l’annonce officielle', html)
        self.assertIn('href="https://www.bodacc.fr/', html)
        self.assertIn('Procédures collectives', html)

    def test_deux_parties_suivies_une_seule_alerte_visible_sur_les_deux_fiches(self):
        self.suivre()
        self.suivre(AUTRE, nom='KERVILAC')
        self.http.bodacc = {'total_count': 1, 'results': [rec(famille='vente', lib='Ventes et cessions', sirens=(SIREN, AUTRE))]}
        self.assertEqual(self.passage()['nouvelles'], 1)
        self.assertEqual(len(crm5688.alertes(self.d)), 1)
        self.assertEqual(len(crm5688.alertes(self.d, siren=SIREN)), 1)
        self.assertEqual(len(crm5688.alertes(self.d, siren=AUTRE)), 1)

    def test_evenement_non_choisi_sans_alerte(self):
        self.suivre(events=['radiation'])
        self.http.bodacc = {'total_count': 1, 'results': [rec(famille='collective')]}
        self.passage()
        self.assertEqual(crm5688.alertes(self.d), [])
        self.assertIn('familleavis', str(self.http.urls))

    def test_panne_sans_fausse_alerte_visible_et_periode_relue(self):
        self.suivre()
        self.http.bodacc = OSError('délai')
        out = self.passage()
        self.assertEqual((out['en_panne'], out['nouvelles']), (1, 0))
        self.assertEqual(crm5688.alertes(self.d), [])
        row = self.d.db.execute('SELECT * FROM societes5688').fetchone()
        self.assertEqual(row['dernier_ok'], '')
        self.assertEqual(row['echecs'], 1)
        self.assertIn('Source BODACC injoignable', crm5688.today_html(self.d, ''))
        self.assertIn('Source BODACC injoignable', crm5688.page_html(self.d, ''))
        self.http.bodacc = {'total_count': 1, 'results': [rec(famille='radiation')]}
        self.d.db.execute("UPDATE societes5688 SET dernier_essai=?", ((datetime.now(timezone.utc) - timedelta(hours=2)).isoformat(timespec='seconds'),))
        self.assertEqual(self.passage()['nouvelles'], 1)
        self.assertEqual(crm5688.etat_source(self.d)['en_panne'], 0)
        self.assertNotIn('injoignable', crm5688.today_html(self.d, ''))

    def test_panne_arrete_le_passage(self):
        for i in range(5):
            self.suivre('12345678%d' % i)
        self.http.bodacc = OSError('délai')
        self.passage()
        self.assertEqual(len(self.http.urls), crm5688.PANNES_AVANT_ARRET)

    def test_aucune_relecture_avant_un_jour_quel_que_soit_le_declencheur(self):
        self.suivre()
        self.passage()
        n = len(self.http.urls)
        self.assertEqual(self.passage()['lues'], 0)
        self.assertEqual(len(self.http.urls), n)

    def test_cadence_par_passage(self):
        with patch.object(crm5688, 'PAR_PASSAGE', 2):
            for i in range(5):
                self.suivre('12345678%d' % i)
            out = self.passage()
        self.assertEqual((out['lues'], out['reportees']), (2, 3))

    def test_pause_entre_deux_lectures(self):
        self.suivre()
        self.suivre(AUTRE)
        pauses = []
        crm5688.verifier(self.d, attente=pauses.append)
        self.assertEqual(pauses, [crm5688.PAUSE])

    def test_boucle_une_fois_par_jour_et_rien_sans_societe(self):
        t = datetime.now(timezone.utc).timestamp()
        self.assertIsNone(crm5688.schedule(self.d, t))
        self.suivre()
        self.assertTrue(crm5688.schedule(self.d, t))
        self.assertIsNone(crm5688.schedule(self.d, t + 3600))
        count = lambda: self.d.db.execute("SELECT COUNT(*) FROM jobs WHERE kind='veille5688'").fetchone()[0]
        self.assertEqual(count(), 1)

    def test_le_travail_tourne_sans_modele_et_ne_contacte_que_le_bodacc(self):
        self.suivre()
        with patch('agent.model.Model.complete', side_effect=AssertionError('modèle interdit')):
            out = self.d.perform('veille5688', {})
        self.assertEqual(out['lues'], 1)
        self.assertTrue(all(u.startswith(reg.BODACC) for u in self.http.urls), self.http.urls)

    def test_retention(self):
        self.suivre()
        self.http.bodacc = {'total_count': 1, 'results': [rec(famille='radiation')]}
        self.passage()
        old = (datetime.now(timezone.utc) - timedelta(days=crm5688.RETENTION_JOURS + 1)).isoformat(timespec='seconds')
        self.d.db.execute('UPDATE annonces5688 SET releve=?', (old,))
        self.assertEqual(crm5688.purger(self.d), 1)
        self.assertEqual(crm5688.alertes(self.d, inclure_vues=True), [])

    def test_premiere_lecture_limitee_et_chevauchement_ensuite(self):
        self.suivre()
        self.passage()
        first = self.http.urls[-1]
        self.reculer(48)
        self.passage()
        from urllib.parse import unquote_plus
        start = re.search(r"dateparution>=date'(\d{4}-\d{2}-\d{2})'", unquote_plus(self.http.urls[-1])).group(1)
        expected = (datetime.now(timezone.utc) - timedelta(hours=48, days=crm5688.MARGE_JOURS)).date().isoformat()
        self.assertEqual(start, expected)
        self.assertIn('dateparution', unquote_plus(first))

    def test_vu_retire_de_aujourdhui_mais_garde_sur_la_fiche(self):
        self.suivre()
        self.http.bodacc = {'total_count': 1, 'results': [rec(famille='radiation')]}
        self.passage()
        crm5688.vu(self.d, 'cabinet', 'B202602103408')
        self.assertEqual(crm5688.today_html(self.d, ''), '')
        self.assertEqual(len(crm5688.alertes(self.d, inclure_vues=True, siren=SIREN)), 1)

    def test_societe_d_un_dossier_reserve_soustraite_au_lecteur(self):
        self.reserve()
        c = self.contact(matter='DOS-002', role='client', siren=SIREN)
        crm5688.suivre(self.d, 'cabinet', {'contact': c['id'], 'evenements': ['radiation']})
        self.http.bodacc = {'total_count': 1, 'results': [rec(famille='radiation')]}
        self.passage()
        self.assertEqual(len(crm5688.alertes(self.d)), 1)                         # travail de l'agent et lecteur autorisé
        mb.set_viewer({'DOS-002'})
        self.assertEqual(crm5688.alertes(self.d), [])
        self.assertEqual(crm5688.today_html(self.d, ''), '')
        self.assertNotIn('ZORVAL', crm5688.page_html(self.d, ''))
        with self.assertRaises(Stop):
            crm5688.vu(self.d, 'assistant@example.test', 'B202602103408')

    def test_suivre_exige_un_evenement_et_un_siren(self):
        with self.assertRaises(Stop):
            crm5688.suivre(self.d, 'cabinet', {'siren': SIREN})
        with self.assertRaises(Stop):
            crm5688.suivre(self.d, 'cabinet', {'siren': '000000000', 'evenements': ['radiation']})

    def test_retirer_arrete_la_lecture(self):
        ident = self.suivre()['id']
        crm5688.retirer(self.d, ident)
        self.passage()
        self.assertEqual(self.http.urls, [])
        self.assertIsNone(crm5688.schedule(self.d, 10 ** 10))


# ------------------------------------------------------------------------------------------------ palette
class Palette(Base):
    def setUp(self):
        super().setUp()
        self.matters()

    def noms(self, out, groupe):
        return [r['titre'] for g in out['groupes'] if g['id'] == groupe for r in g['resultats']]

    def facture(self, matter):
        from agent.facturation5653 import SCHEMA
        self.d.db.executescript(SCHEMA)
        self.d.db.execute("INSERT INTO b53_documents(id,kind,matter,state,number,snapshot_json,created_by,created,updated) VALUES(?,?,?,?,?,?,?,?,?)",
                          ('f-' + matter, 'facture', matter, 'emise', 'FA-2026-' + matter[-1], '{}', 'cabinet', '2026-10-01', '2026-10-01'))
        self.d.db.commit()

    def test_resultats_groupes_dans_l_ordre(self):
        self.contact(name='DURAND', first_name='Claire', kind='personne')
        out = palette5688.search(self.d, 'durand')
        ids = [g['id'] for g in out['groupes']]
        self.assertEqual(ids, [k for k, _ in palette5688.GROUPES if k in ids])
        self.assertIn('dossiers', ids)
        self.assertIn('contacts', ids)

    def test_dossier_reserve_retire_cote_serveur_pour_la_personne(self):
        self.reserve()
        out = palette5688.search(self.d, 'orlandec', 'assistant@example.test', 'assistant')
        self.assertEqual(self.noms(out, 'dossiers'), [])
        out = palette5688.search(self.d, 'orlandec', 'avocate@example.test', 'avocat')
        self.assertTrue(self.noms(out, 'dossiers'))

    def test_contact_d_un_dossier_reserve_retire_avant_la_limite(self):
        self.reserve()
        for i in range(palette5688.PAR_GROUPE * 2):
            self.contact(name='MARTIN%d' % i, matter='DOS-002', role='client')
        self.contact(name='MARTIN visible')
        out = palette5688.search(self.d, 'martin', 'assistant@example.test', 'assistant')
        self.assertEqual(self.noms(out, 'contacts'), ['MARTIN visible'])

    def test_factures_reservees_au_droit_honoraires(self):
        self.facture('DOS-001')
        self.assertEqual(self.noms(palette5688.search(self.d, 'FA-2026', 'assistant@example.test', 'assistant'), 'factures'), [])
        self.assertTrue(self.noms(palette5688.search(self.d, 'FA-2026', 'avocate@example.test', 'avocat'), 'factures'))
        mb.set_rights(self.state, 'cabinet', 'avocate@example.test', 'avocat', {'facturation': False})
        self.assertEqual(self.noms(palette5688.search(self.d, 'FA-2026', 'avocate@example.test', 'avocat'), 'factures'), [])
        mb.set_rights(self.state, 'cabinet', 'assistant@example.test', 'assistant', {'facturation': True})
        self.assertTrue(self.noms(palette5688.search(self.d, 'FA-2026', 'assistant@example.test', 'assistant'), 'factures'))

    def test_facture_d_un_dossier_reserve_retiree(self):
        self.reserve(owners=('assistant@example.test',))
        self.facture('DOS-002')
        self.assertEqual(self.noms(palette5688.search(self.d, 'FA-2026', 'avocate@example.test', 'avocat'), 'factures'), [])

    def test_courriels_indexes_filtres_sans_rafraichissement(self):
        from agent import search490
        self.reserve()
        db = search490.connect(self.d)
        search490._upsert(db, 'courriel', 'suivi|1', 'DOS-001', 'Relance KERVILAC visible', '2026-10-01', 'corps', {'label': 'x'})
        search490._upsert(db, 'courriel', 'suivi|2', 'DOS-002', 'Relance KERVILAC cachée', '2026-10-01', 'corps', {'label': 'x'})
        db.commit()
        db.close()
        with patch.object(search490, 'refresh_local', side_effect=AssertionError('pas de rafraîchissement dans la palette')):
            out = palette5688.search(self.d, 'kervilac', 'assistant@example.test', 'assistant')
        self.assertEqual(self.noms(out, 'courriels'), ['Relance KERVILAC visible'])

    def test_sans_modele_ni_reseau(self):
        with patch('agent.model.Model.complete', side_effect=AssertionError('modèle interdit')):
            palette5688.search(self.d, 'durand')
        self.assertEqual(self.http.urls, [])

    def test_saisie_courte_et_groupes_vides_retires(self):
        self.assertEqual(palette5688.search(self.d, 'd')['groupes'], [])
        out = palette5688.search(self.d, 'zzzqqq')
        self.assertEqual(out['groupes'], [])

    def test_identite_prise_sur_la_requete(self):
        calls = {}
        with patch('agent.palette5688.search', side_effect=lambda d, q, o, r: calls.update(o=o, r=r) or {'groupes': []}):
            web5688.api({'HTTP_X_AXIORHUB_USER': 'assistant@example.test', 'HTTP_X_AXIORHUB_ROLE': 'assistant'}, self.d, {}, '', 'm5688/palette', {'q': 'x', 'owner': 'cabinet', 'role': 'administrateur'}, 'GET')
        self.assertEqual(calls, {'o': 'assistant@example.test', 'r': 'assistant'})

    def test_un_seul_ecouteur_et_un_seul_champ(self):
        names = [p for p in STATIC.glob('*.js') if not re.search(r'pdf|carbon|htmx|core', p.name)]
        listeners = [p.name for p in names if re.search(r"\.key(\.toLowerCase\(\))?\s*===?\s*'[kK]'", p.read_text(encoding='utf-8', errors='replace'))]
        self.assertEqual(listeners, ['v420.js'])
        src = (STATIC / 'v420.js').read_text(encoding='utf-8')
        self.assertIn('m5688/palette', src)
        self.assertNotIn('m82-quick-q', (STATIC / 'v5682.js').read_text(encoding='utf-8'))
        self.assertIn('data-m82-quick', src)                                      # le bouton de la barre du haut ouvre la même palette


# ------------------------------------------------------------------------------------------------ page personnelle
GABARIT = ('<div class="c530" id="c530"><!--t88-haut--><div class="c530-split c530-split--top"><section class="c530-card" id="c530-routines">R</section><!--/b88:routines-->'
           '<section class="c530-card" id="c530-style">S</section><!--/b88:style--></div><!--/t88-haut--><!--t88-bas--><div class="c530-cols c5657-cols"><div class="c5662-file" id="c5656-file">F</div><!--/b88:file-->'
           '<section class="c530-card" id="c530-feed">A</section><!--/b88:activite--><section class="c530-card" id="c530-day">J</section><!--/b88:journee--></div>'
           '<section class="c530-card c5656-wide" id="c5632-veille">V</section><!--/b88:veille--><!--t88-extra--><div class="c5656-wide c5662-reprise">P</div><!--/b88:reprise--><!--/t88-bas--><!--t88-panneau--></div>')


def luminance(value):
    value = value.lstrip('#')
    value = ''.join(c * 2 for c in value) if len(value) == 3 else value
    parts = [int(value[i:i + 2], 16) / 255 for i in (0, 2, 4)]
    a, b, c = [x / 12.92 if x <= .03928 else ((x + .055) / 1.055) ** 2.4 for x in parts]
    return .2126 * a + .7152 * b + .0722 * c


def contraste(a, b):
    x, y = sorted((luminance(a), luminance(b)), reverse=True)
    return (x + .05) / (y + .05)


class Tableau(Base):
    def test_sans_reglage_la_disposition_d_origine_est_gardee(self):
        html = tableau5688.appliquer(self.d, GABARIT, 'cabinet', {'societes': '<section id="x">X</section>'})
        self.assertIn('c5657-cols', html)
        self.assertNotIn('t88-grid', html)
        self.assertNotIn('<!--', html)
        self.assertIn('id="x"', html)
        self.assertLess(html.index('c5632-veille'), html.index('id="x"'))
        self.assertLess(html.index('id="x"'), html.index('c5662-reprise'))

    def test_ordre_range_par_le_serveur_et_masquage(self):
        tableau5688.enregistrer(self.d, 'cabinet', {'ordre': 'journee,routines,file', 'voir:style': False, 'accent': ''})
        html = tableau5688.appliquer(self.d, GABARIT, 'cabinet', {})
        self.assertLess(html.index('c530-day'), html.index('c530-routines'))
        self.assertLess(html.index('c530-routines'), html.index('c5656-file'))
        self.assertNotIn('id="c530-style"', html)
        self.assertIn('1 bloc(s) masqué(s)', html)
        self.assertNotIn('<!--', html)
        for ident in ('c530-day', 'c530-routines', 'c5656-file', 'c530-feed', 'c5632-veille'):
            self.assertEqual(html.count('id="%s"' % ident), 1, ident)

    def test_une_personne_ne_change_pas_la_page_d_une_autre(self):
        tableau5688.enregistrer(self.d, 'avocate@example.test', {'ordre': ['journee'], 'accent': 'vert', 'voir:style': False})
        self.assertFalse(tableau5688.charger(self.d, 'cabinet')['perso'])
        self.assertEqual(tableau5688.charger(self.d, 'cabinet')['accent'], '')

    def test_retour_aux_reglages_d_origine(self):
        tableau5688.enregistrer(self.d, 'cabinet', {'ordre': ['journee'], 'accent': 'vert'})
        tableau5688.reinitialiser(self.d, 'cabinet')
        prefs = tableau5688.charger(self.d, 'cabinet')
        self.assertEqual((prefs['perso'], prefs['accent'], prefs['ordre']), (False, '', list(tableau5688.IDS)))

    def test_couleur_hors_palette_refusee(self):
        for bad in ('#ff0000', 'jaune', 'vert;background:url(x)'):
            with self.assertRaises(Stop):
                tableau5688.enregistrer(self.d, 'cabinet', {'accent': bad})

    def test_reglage_abime_ou_bloc_inconnu_sans_effet(self):
        tableau5688.ensure_schema(self.d)
        self.d.db.execute('INSERT OR REPLACE INTO tableau5688 VALUES(?,?,?)', ('cabinet', '{abîmé', 'x'))
        self.assertFalse(tableau5688.charger(self.d, 'cabinet')['perso'])
        tableau5688.enregistrer(self.d, 'cabinet', {'ordre': 'inconnu,journee,journee', 'accent': ''})
        self.assertEqual(tableau5688.charger(self.d, 'cabinet')['ordre'][0], 'journee')
        self.assertEqual(len(tableau5688.charger(self.d, 'cabinet')['ordre']), len(tableau5688.IDS))

    def test_accent_pose_et_echappe(self):
        tableau5688.enregistrer(self.d, 'cabinet', {'accent': 'bordeaux'})
        self.assertIn('id="c530" data-accent="bordeaux"', tableau5688.appliquer(self.d, GABARIT, 'cabinet', {}))

    def test_clavier_et_souris(self):
        panel = tableau5688.panneau(tableau5688.charger(self.d, 'cabinet'))
        self.assertIn('data-t88-move="-1"', panel)
        self.assertIn('aria-label="Monter : Ma journée"', panel)
        self.assertIn('draggable="true"', panel)
        self.assertIn('Revenir aux réglages d’origine', panel)
        js = (STATIC / 'v5688-crm.js').read_text(encoding='utf-8')
        self.assertIn('dragstart', js)
        self.assertIn('data-t88-move', js)

    def test_contraste_de_chaque_couleur_en_clair_et_en_sombre(self):
        clairs = ('#ffffff', '#f7f8fa')                                           # --ws-surface, --ws-paper
        sombres = ('#202329', '#111318', '#1a2029', '#292d34')                    # surfaces du thème sombre
        for name, (label, clair, sombre) in tableau5688.ACCENTS.items():
            if not name:
                continue
            accent, survol, doux = clair
            for fond in clairs + (doux,):
                self.assertGreaterEqual(contraste(accent, fond), 4.5, (name, 'clair', fond))
            self.assertGreaterEqual(contraste('#ffffff', accent), 4.5, name)
            self.assertGreaterEqual(contraste('#ffffff', survol), 4.5, name)
            accent, survol, doux = sombre
            for fond in sombres + (doux,):
                self.assertGreaterEqual(contraste(accent, fond), 4.5, (name, 'sombre', fond))
            self.assertGreaterEqual(contraste(tableau5688.ENCRE_SOMBRE, accent), 4.5, name)
            self.assertGreaterEqual(contraste(tableau5688.ENCRE_SOMBRE, survol), 4.5, name)

    def test_feuille_de_style_conforme_a_la_palette(self):
        css = (STATIC / 'v5688-crm.css').read_text(encoding='utf-8')
        for name, (label, clair, sombre) in tableau5688.ACCENTS.items():
            if not name:
                continue
            self.assertIn('.c530[data-accent=%s] { --ws-accent: %s; --ws-accent-hover: %s; --ws-accent-soft: %s; --ax-accent: %s; --ax-accent-ink: #fff; }' % (name, *clair, clair[0]), css)
            dark = '--ws-accent: %s; --ws-accent-hover: %s; --ws-accent-soft: %s; --ax-accent: %s; --ax-accent-ink: %s; }' % (*sombre, sombre[0], tableau5688.ENCRE_SOMBRE)
            self.assertIn(':root[data-theme=dark] .c530[data-accent=%s] { %s' % (name, dark), css)
            self.assertIn(':root:not([data-theme=light]) .c530[data-accent=%s] { %s' % (name, dark), css)
            self.assertIn('[data-swatch=%s] { --t88-swatch: %s; }' % (name, clair[0]), css)

    def test_page_reelle_personnalisee(self):
        tableau5688.enregistrer(self.d, 'cabinet', {'ordre': ['journee', 'activite'], 'voir:routines': False, 'accent': 'ardoise'})
        html = cockpit530.page(self.d, '', owner='cabinet', queue='<p>file</p>')
        self.assertLess(html.index('id="c530-day"'), html.index('id="c530-feed"'))
        self.assertNotIn('id="c530-routines"', html)
        self.assertIn('data-accent="ardoise"', html)
        self.assertIn('id="t88-panel"', html)
        self.assertNotIn('<!--t88', html)
        self.assertNotIn('b88:', html)
        self.assertIn('data-legacy-composer', html)

    def test_page_reelle_sans_reglage_inchangee(self):
        html = cockpit530.page(self.d, '', owner='cabinet', queue='<p>file</p>')
        self.assertIn('<div class="c530-cols c5657-cols">', html)
        self.assertLess(html.index('id="c530-routines"'), html.index('data-legacy-composer'))
        self.assertLess(html.index('data-legacy-composer'), html.index('id="c5656-file"'))
        self.assertNotIn('<!--', html.split('id="c5632-veille"')[0][-200:])


# ------------------------------------------------------------------------------------------------ note commune
class Note(Base):
    def setUp(self):
        super().setUp()
        self.matters()
        self.avocate, self.assistant = 'avocate@example.test', 'assistant@example.test'

    def ecrire(self, me, texte, base, hidden=frozenset(), matter='DOS-001'):
        return notes5688.enregistrer(self.d, me, self.members[me]['name'], matter, texte, base, self.members, hidden)

    def test_versions_et_auteurs_conserves(self):
        self.ecrire(self.avocate, 'Premier jet', 0)
        self.ecrire(self.assistant, 'Second jet', 1)
        note = notes5688.lire(self.d, 'DOS-001')
        self.assertEqual((note['version'], note['texte'], note['auteur_nom']), (2, 'Second jet', 'Paul Martin'))
        self.assertEqual([v['auteur'] for v in notes5688.versions(self.d, 'DOS-001')], [self.assistant, self.avocate])
        self.assertEqual(notes5688.version(self.d, 'DOS-001', 1)['texte'], 'Premier jet')

    def test_conflit_detecte_rien_n_est_ecrase(self):
        self.ecrire(self.avocate, 'Base commune', 0)
        self.ecrire(self.avocate, 'Version de Claire', 1)
        with self.assertRaises(notes5688.NoteConflit) as ctx:
            self.ecrire(self.assistant, 'Version de Paul', 1)
        self.assertEqual(ctx.exception.actuelle['texte'], 'Version de Claire')
        self.assertEqual(ctx.exception.actuelle['version'], 2)
        self.assertEqual(notes5688.lire(self.d, 'DOS-001')['texte'], 'Version de Claire')
        self.assertEqual(len(notes5688.versions(self.d, 'DOS-001')), 2)

    def test_deux_premieres_ecritures_simultanees(self):
        self.ecrire(self.avocate, 'Claire la première', 0)
        with self.assertRaises(notes5688.NoteConflit):
            self.ecrire(self.assistant, 'Paul aussi', 0)
        self.assertEqual(notes5688.lire(self.d, 'DOS-001')['texte'], 'Claire la première')

    def test_reponse_http_409_avec_le_texte_en_place(self):
        out = web5688._erreur(notes5688.NoteConflit({'version': 2, 'texte': 'x', 'auteur_nom': 'Paul Martin', 'modifie': ''}))
        self.assertEqual(out['status'], '409 Conflict')
        self.assertIn('Paul Martin', out['body'] if isinstance(out['body'], str) else out['body'].decode('utf-8'))

    def test_rien_de_nouveau_pas_de_version(self):
        self.ecrire(self.avocate, 'Texte', 0)
        self.assertEqual(self.ecrire(self.avocate, 'Texte', 1)['version'], 1)
        self.assertEqual(len(notes5688.versions(self.d, 'DOS-001')), 1)

    def test_retention_des_versions(self):
        with patch.object(notes5688, 'MAX_VERSIONS', 3):
            for i in range(6):
                self.ecrire(self.avocate, 'Texte %d' % i, i)
        rows = notes5688.versions(self.d, 'DOS-001')
        self.assertEqual([r['version'] for r in rows], [6, 5, 4])

    def test_dossier_reserve_refuse(self):
        self.reserve()
        hidden = mb.hidden_for(self.state, self.assistant, 'assistant')
        for call in (lambda: notes5688.lire(self.d, 'DOS-002', hidden), lambda: self.ecrire(self.assistant, 'x', 0, hidden, 'DOS-002'), lambda: notes5688.version(self.d, 'DOS-002', 1, hidden),
                     lambda: notes5688.candidats(self.d, self.members, self.assistant, 'DOS-002', '', hidden)):
            with self.assertRaises(Stop) as ctx:
                call()
            self.assertEqual(str(ctx.exception), 'dossier_reserve')

    def test_mention_notifie_et_seulement_la_premiere_fois(self):
        out = self.ecrire(self.avocate, 'Merci de relire @assistant.', 0)
        self.assertEqual(out['mentions'], [self.assistant])
        self.assertEqual(mx.unread_total(self.d, self.assistant), 1)
        self.assertEqual(self.ecrire(self.avocate, 'Merci de relire @assistant. Et encore.', 1)['mentions'], [])
        self.assertEqual(mx.unread_total(self.d, self.assistant), 1)

    def test_mention_d_une_personne_sans_acces_ne_notifie_pas_et_le_dit_a_l_auteur(self):
        self.reserve('DOS-001')
        out = self.ecrire(self.avocate, 'Pour avis @assistant', 0)
        self.assertEqual((out['mentions'], out['sans_acces']), ([], [self.assistant]))
        self.assertEqual(mx.unread_total(self.d, self.assistant), 0)
        self.assertEqual(self.d.db.execute('SELECT COUNT(*) FROM messages5650').fetchone()[0], 0)
        self.assertIn('Paul Martin', out['message'])

    def test_adresse_de_courriel_n_est_pas_une_mention(self):
        self.assertEqual(self.ecrire(self.avocate, 'Écrire à contact@assistant.example.test', 0)['mentions'], [])

    def test_autocompletion_limitee_aux_personnes_qui_voient_le_dossier(self):
        self.assertEqual([p['nom'] for p in notes5688.candidats(self.d, self.members, self.avocate, 'DOS-001', 'pau')], ['Paul Martin'])
        self.assertEqual(notes5688.candidats(self.d, self.members, self.avocate, 'DOS-001', 'pau')[0]['jeton'], 'assistant')
        self.reserve('DOS-001')
        self.assertEqual(notes5688.candidats(self.d, self.members, self.avocate, 'DOS-001', 'pau'), [])
        self.assertNotIn(self.avocate, [p['nom'] for p in notes5688.candidats(self.d, self.members, self.avocate, '', '')])

    def test_jeton_complet_si_homonymes(self):
        members = {**self.members, 'assistant@autre.example.test': {'owner': 'assistant@autre.example.test', 'name': 'Autre', 'role': 'assistant'}}
        jetons = {p['jeton'] for p in notes5688.candidats(self.d, members, self.avocate)}
        self.assertIn('assistant@autre.example.test', jetons)

    def tache(self, title, matter='DOS-001', ident='t1', description=''):
        from agent.workplan import ensure_schema
        ensure_schema(self.d)
        cols = [r[1] for r in self.d.db.execute('PRAGMA table_info(work_tasks_v211)')]
        values = {c: '' for c in cols}
        values.update({'id': ident, 'title': title, 'description': description, 'matter': matter, 'status': 'todo', 'duration_minutes': 0, 'priority': 0})
        self.d.db.execute('INSERT INTO work_tasks_v211(%s) VALUES(%s)' % (','.join(cols), ','.join('?' * len(cols))), [values[c] for c in cols])
        self.d.db.commit()

    def test_mention_dans_une_tache_notifie_une_fois(self):
        self.tache('Relire les pièces @assistant')
        self.assertEqual(notes5688.scanner_taches(self.d, 1000, self.members), 1)
        self.assertEqual(mx.unread_total(self.d, self.assistant), 1)
        self.assertEqual(notes5688.scanner_taches(self.d, 2000, self.members), 0)
        self.assertEqual(mx.unread_total(self.d, self.assistant), 1)

    def test_cadence_du_balayage_des_taches(self):
        self.tache('Relire @assistant')
        notes5688.scanner_taches(self.d, 1000, self.members)
        self.tache('Autre @avocate', ident='t2')
        self.assertEqual(notes5688.scanner_taches(self.d, 1100, self.members), 0)
        self.assertEqual(notes5688.scanner_taches(self.d, 1400, self.members), 1)

    def test_tache_d_un_dossier_reserve_ne_notifie_pas_sans_acces(self):
        self.reserve('DOS-001')
        self.tache('Relire @assistant')
        self.assertEqual(notes5688.scanner_taches(self.d, 1000, self.members), 0)
        self.assertEqual(mx.unread_total(self.d, self.assistant), 0)

    def test_tache_sans_dossier_notifie_directement(self):
        self.tache('Appeler le greffe @assistant', matter='')
        self.assertEqual(notes5688.scanner_taches(self.d, 1000, self.members), 1)
        self.assertEqual(mx.unread_total(self.d, self.assistant), 1)

    def test_carte_du_dossier(self):
        self.ecrire(self.avocate, '<b>texte</b>', 0)
        html = notes5688.card_html(self.d, 'DOS-001')
        self.assertIn('&lt;b&gt;texte', html)
        self.assertIn('data-mention', html)
        self.assertIn('data-version="1"', html)
        self.assertIn('id="n88-conflit"', html)


# ------------------------------------------------------------------------------------------------ mémoire
class Memoire(Base):
    def setUp(self):
        super().setUp()
        self.matters()
        memoire5688.ensure_schema(self.d)
        self.d.db.execute("INSERT INTO assistant_threads(id,scope,matter,mail_key,title,created,updated) VALUES('f1','matter','DOS-001','','t','x','x')")
        self.msg = self.d.db.execute("INSERT INTO assistant_messages(thread_id,role,content,status,sources,job_id,created,updated) VALUES('f1','user','Retiens que le client préfère un règlement amiable dans ce dossier.','done','[]',NULL,'x','x')").lastrowid
        self.d.db.commit()

    def echange(self, question, matter='DOS-001', auteur='avocate@example.test', purpose='assistant'):
        return memoire5688.apres_echange(self.d, auteur, 'f1', self.msg, matter, question, purpose)

    def lignes(self):
        return [dict(r) for r in self.d.db.execute('SELECT * FROM memoire5688')]

    def test_reperage_sans_modele(self):
        self.assertEqual(memoire5688.reperer('Quel est le délai ?'), [])
        self.assertEqual(memoire5688.reperer('Prépare un courrier pour le greffe.'), [])
        self.assertEqual(memoire5688.reperer('Retiens que le gérant a démissionné en mars.', 'DOS-001'), [('fait', 'dossier', 'Le gérant a démissionné en mars')])
        self.assertEqual(memoire5688.reperer('Désormais je préfère des courriers courts.')[0][:2], ('preference', 'personne'))
        self.assertEqual(memoire5688.reperer('À l’avenir, notre cabinet signe toujours par Maître.')[0][:2], ('preference', 'cabinet'))
        self.assertEqual(memoire5688.reperer('Retiens toujours ce format dans ce dossier.', 'DOS-001')[0][:2], ('preference', 'dossier'))

    def test_aucun_appel_au_modele_par_defaut_et_rien_de_retenu(self):
        with patch('agent.model.Model.complete', side_effect=AssertionError('modèle interdit')):
            out = self.echange('Retiens que le gérant a démissionné en mars.')
        self.assertEqual((out['propositions'], out['job']), (1, 0))
        self.assertEqual(self.d.db.execute("SELECT COUNT(*) FROM jobs WHERE kind='memoire5688'").fetchone()[0], 0)
        self.assertEqual(memory_records(self.d, 'DOS-001'), [])                   # les faits « suggérés » sont lus par l'assistant : rien n'y entre avant validation
        self.assertEqual(self.lignes()[0]['etat'], 'proposee')

    def test_retenir_un_fait_le_valide_dans_la_memoire_du_dossier(self):
        self.echange('Retiens que le gérant a démissionné en mars.')
        ident = self.lignes()[0]['id']
        memoire5688.decider(self.d, 'avocate@example.test', 'avocat', ident, 'retenir')
        rows = memory_records(self.d, 'DOS-001')
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]['status'], 'validated')
        self.assertEqual(rows[0]['origin'], 'memoire5688')
        self.assertEqual(self.lignes()[0]['etat'], 'retenue')

    def test_ecartee_ne_revient_pas(self):
        self.echange('Retiens que le gérant a démissionné en mars.')
        memoire5688.decider(self.d, 'avocate@example.test', 'avocat', self.lignes()[0]['id'], 'ecarter')
        self.assertEqual(self.echange('Retiens que le gérant a démissionné en mars.')['propositions'], 0)
        self.assertEqual(memory_records(self.d, 'DOS-001'), [])
        self.assertEqual(memoire5688.en_attente(self.d, 'avocate@example.test'), [])

    def test_preference_personnelle_propre_a_son_auteur_et_expurgee(self):
        from agent import apprentissage5651
        self.echange('Désormais je préfère écrire DURAND en petites capitales pour DOS-001, 12 pages au plus.', matter='DOS-001')
        row = self.lignes()[0]
        self.assertEqual(row['portee'], 'personne')
        with self.assertRaises(Stop):
            memoire5688.decider(self.d, 'assistant@example.test', 'assistant', row['id'], 'retenir')
        memoire5688.decider(self.d, 'avocate@example.test', 'avocat', row['id'], 'retenir')
        rules = apprentissage5651.rules(self.d, 'avocate@example.test')
        self.assertEqual(rules[0]['scope'], 'style')
        self.assertNotIn('DURAND', rules[0]['instruction'])
        self.assertEqual(memory_records(self.d, 'DOS-001'), [])

    def test_preference_de_cabinet_passe_par_a_decider(self):
        from agent import pilote5613
        self.echange('À l’avenir, notre cabinet signe toujours par Maître.', matter='')
        row = self.lignes()[0]
        self.assertEqual((row['portee'], row['etat']), ('cabinet', 'transmise'))
        props = pilote5613.rule_proposals(self.d)
        self.assertEqual(len(props), 1)
        self.assertIn('signe toujours', props[0]['instruction'])
        rules = self.d.db.execute("SELECT COUNT(*) FROM sqlite_master WHERE name='business_rules_v410'").fetchone()[0]
        if rules:
            self.assertEqual(self.d.db.execute('SELECT COUNT(*) FROM business_rules_v410').fetchone()[0], 0)   # aucun effet avant adoption
        self.assertEqual(memoire5688.en_attente(self.d, 'avocate@example.test'), [])

    def test_validation_d_un_fait_exige_le_droit_et_l_acces(self):
        self.echange('Retiens que le gérant a démissionné en mars.')
        ident = self.lignes()[0]['id']
        with self.assertRaises(Stop) as ctx:
            memoire5688.decider(self.d, 'assistant@example.test', 'assistant', ident, 'retenir')
        self.assertEqual(str(ctx.exception), 'role_insuffisant')
        with self.assertRaises(Stop):
            memoire5688.decider(self.d, 'avocate@example.test', 'avocat', ident, 'retenir', frozenset({'DOS-001'}))
        self.assertEqual(memory_records(self.d, 'DOS-001'), [])

    def test_proposition_d_un_dossier_reserve_invisible(self):
        self.echange('Retiens que le gérant a démissionné en mars.')
        self.assertEqual(memoire5688.panel_html(self.d, 'assistant@example.test', '', frozenset({'DOS-001'})), '')
        self.assertIn('démissionné', memoire5688.panel_html(self.d, 'avocate@example.test'))

    def test_reglage_explicite_retient_sans_validation(self):
        self.d.setting('memoire5688:auto', True)
        self.echange('Retiens que le gérant a démissionné en mars.')
        self.assertEqual(memory_records(self.d, 'DOS-001')[0]['status'], 'validated')
        row = self.lignes()[0]
        self.assertEqual((row['etat'], row['decide_par']), ('retenue', 'automatique'))

    def test_desactive_et_sans_auteur_et_voix(self):
        self.assertEqual(self.echange('Retiens que le gérant a démissionné en mars.', auteur='')['propositions'], 0)
        self.assertEqual(self.echange('Retiens que le gérant a démissionné en mars.', purpose='voice_conversation')['propositions'], 0)
        self.d.setting('memoire5688:actif', False)
        self.assertEqual(self.echange('Retiens que le gérant a démissionné en mars.')['propositions'], 0)
        self.assertEqual(self.lignes(), [])

    def test_modele_plafonne_et_en_tache_de_fond(self):
        self.d.setting('memoire5688:modele', True)
        self.d.setting('memoire5688:plafond', 2)
        question = 'Une question suffisamment longue pour justifier un repérage éventuel.'
        for _ in range(5):
            self.echange(question)
        self.assertTrue(self.d.db.execute("SELECT 1 FROM jobs WHERE kind='memoire5688' AND priority>=50").fetchone())
        self.assertEqual(self.d.settings(memoire5688._appels_du_jour(self.d), 0), 2)

    def test_plafond_zero_aucun_appel(self):
        self.d.setting('memoire5688:modele', True)
        self.d.setting('memoire5688:plafond', 0)
        self.assertEqual(self.echange('Une question suffisamment longue pour justifier un repérage éventuel.')['job'], 0)

    def test_proposition_du_modele_jamais_appliquee_meme_en_automatique(self):
        class Faux_:
            def complete(self, messages, **kw):
                self.vu = messages
                return json.dumps({'propositions': [{'nature': 'fait', 'portee': 'dossier', 'texte': 'Le client a tout avoué au téléphone.'}]})
        self.d.setting('memoire5688:modele', True)
        self.d.setting('memoire5688:auto', True)
        model = Faux_()
        out = memoire5688.extraire(self.d, {'fil': 'f1', 'message': self.msg, 'matter': 'DOS-001', 'auteur': 'avocate@example.test'}, model)
        self.assertEqual(out['propositions'], 1)
        self.assertEqual(memory_records(self.d, 'DOS-001'), [])
        self.assertEqual(self.lignes()[0]['etat'], 'proposee')
        self.assertEqual(self.lignes()[0]['origine'], 'modele')

    def test_reponse_illisible_du_modele_sans_effet(self):
        class Faux_:
            def complete(self, messages, **kw):
                return 'pas du json'
        self.d.setting('memoire5688:modele', True)
        self.assertEqual(memoire5688.extraire(self.d, {'fil': 'f1', 'message': self.msg, 'matter': 'DOS-001'}, Faux_())['propositions'], 0)

    def test_modele_desactive_entre_la_file_et_l_execution(self):
        self.assertTrue(memoire5688.extraire(self.d, {'fil': 'f1', 'message': self.msg}, None)['desactive'])

    def test_crochet_de_fin_de_reponse(self):
        from agent import integration
        args = {'message': self.msg, 'thread': 'f1', 'matter': 'DOS-001', 'auteur': 'avocate@example.test'}
        self.d.db.execute("UPDATE assistant_messages SET status='queued' WHERE id=?", (self.msg,))
        with patch('agent.workspace.chat', return_value={'answer': 'Bien noté.', 'sources': []}), patch('agent.integration.Stop', Stop), patch('agent.model.Model', lambda c: None), patch('agent.dav.DAV', lambda c: None):
            out = integration.answer_question(self.d, args)
        self.assertEqual(out['status'], 'done')
        self.assertEqual(self.lignes()[0]['portee'], 'dossier')
        with patch('agent.memoire5688.apres_echange', side_effect=RuntimeError('panne')), patch('agent.workspace.chat', return_value={'answer': 'Bien noté.', 'sources': []}), patch('agent.model.Model', lambda c: None), patch('agent.dav.DAV', lambda c: None):
            self.d.db.execute("UPDATE assistant_messages SET status='queued' WHERE id=?", (self.msg,))
            self.assertEqual(integration.answer_question(self.d, args)['status'], 'done')   # une panne de la mémoire n'empêche jamais la réponse

    def test_retention(self):
        self.echange('Retiens que le gérant a démissionné en mars.')
        old = (datetime.now(timezone.utc) - timedelta(days=memoire5688.GARDE_PROPOSEES + 1)).isoformat(timespec='seconds')
        self.d.db.execute('UPDATE memoire5688 SET cree=?', (old,))
        self.assertEqual(memoire5688.purger(self.d), 1)

    def test_reglages_controles(self):
        memoire5688.regler(self.d, {'actif': True, 'modele': True, 'auto': False, 'plafond': 7}, 'cabinet')
        self.assertEqual(memoire5688.reglages(self.d), {'actif': True, 'modele': True, 'auto': False, 'plafond': 7})
        for bad in (-1, 1000, 'x'):
            with self.assertRaises(Stop):
                memoire5688.regler(self.d, {'plafond': bad}, 'cabinet')

    def test_reglage_reserve_a_l_administrateur(self):
        self.assertFalse(web5688.allowed('avocat', 'memoire/reglage'))
        self.assertTrue(web5688.allowed('administrateur', 'memoire/reglage'))
        self.assertTrue(web5688.allowed('assistant', 'note/enregistrer'))
        self.assertFalse(web5688.allowed('inconnu', 'note/enregistrer'))


class Pseudonymise(unittest.TestCase):
    """Vraie pile d'appel (``model.py``) jusqu'à un fournisseur externe simulé : le texte part pseudonymisé."""

    def setUp(self):
        self.f = fixtures.EngineTests('test_observation_has_no_mail_write')
        self.f.setUp()
        self.addCleanup(self.f.doCleanups)
        self.d = Desk(self.f.c)
        secret = Path(self.f.c['state_dir']) / 'k.secret'
        secret.write_text('cle-test\n')
        secret.chmod(0o600)
        self.f.c.setdefault('ai_providers', {})['openai'] = {'id': 'openai', 'type': 'openai', 'url': 'https://api.openai.com/v1', 'model': 'gpt-5', 'enabled': True, 'external_data_allowed': True,
                                                             'secret_file': str(secret), 'state_dir': self.f.c['state_dir']}
        t540.FakeHTTP.calls, t540.FakeHTTP.payloads, t540.FakeHTTP.headers_seen, t540.FakeHTTP.answer = [], [], [], None
        pseudo540._CACHE['key'] = None
        p = patch('agent.model.HTTP', t540.FakeHTTP)
        p.start()
        self.addCleanup(p.stop)
        memoire5688.ensure_schema(self.d)

    def test_texte_pseudonymise_vers_l_ia_externe_et_retabli_localement(self):
        with patch('agent.ia540.Model', create=True):
            apply_mixed(self.d, 'openai', 'gpt-5', 'test-local', test=False)
        from support567 import authorize_external
        authorize_external(self.f.c)
        self.d.db.execute("INSERT INTO assistant_threads(id,scope,matter,mail_key,title,created,updated) VALUES('f2','matter','DOS-001','','t','x','x')")
        msg = self.d.db.execute("INSERT INTO assistant_messages(thread_id,role,content,status,sources,job_id,created,updated) VALUES('f2','user','Client DEMO (client@example.test) préfère un règlement amiable.','done','[]',NULL,'x','x')").lastrowid
        self.d.db.commit()
        self.d.setting('memoire5688:modele', True)
        t540.FakeHTTP.answer = json.dumps({'propositions': [{'nature': 'preference', 'portee': 'personne', 'texte': '[PERSONNE_1] préfère un règlement amiable.'}]})
        real = routed_config
        with patch('agent.model.routed_config', lambda c, purpose: real(c, 'mail_drafting')):   # en mode mixte, le tri reste local : la route externe est imposée ici
            out = memoire5688.extraire(self.d, {'fil': 'f2', 'message': msg, 'matter': 'DOS-001', 'auteur': 'avocate@example.test'})
        sent = json.dumps([p for path, p in t540.FakeHTTP.payloads if path == '/chat/completions'][-1], ensure_ascii=False)
        self.assertNotIn('Client DEMO', sent)
        self.assertNotIn('client@example.test', sent)
        self.assertEqual(out['propositions'], 1)
        self.assertIn('Client DEMO', self.d.db.execute('SELECT texte FROM memoire5688').fetchone()[0])


class Application(t48.Base):
    """Application réelle (WSGI), compte d'essai : routes, droits et pages, sans réseau."""

    def add(self, admin, ident, role):
        self.api('m5636/comptes/ajouter', {'ident': ident, 'name': ident.split('@')[0].title(), 'role': role, 'password': 'provisoire-fictif-1'}, admin)
        cookie = self.token(self.login(ident, 'provisoire-fictif-1'))
        self.request('/connexion/changer', 'POST', {'password': 'personnel-fictif-22', 'confirm': 'personnel-fictif-22'}, cookie=cookie)
        return self.token(self.login(ident, 'personnel-fictif-22'))

    def get(self, path, cookie, query=''):
        return self.request(path, cookie=cookie, query=query)

    def test_parcours_note_conflit_palette_page(self):
        admin = self.admin_cookie()
        aide = self.add(admin, 'assistant@example.test', 'assistant')
        one = self.api('m5688/note/enregistrer', {'matter': 'DOS-001', 'base': 0, 'texte': 'Première version'}, admin)
        self.assertTrue(one['status'].startswith('200'), one['body'])
        two = self.api('m5688/note/enregistrer', {'matter': 'DOS-001', 'base': 0, 'texte': 'Écrit en même temps'}, aide)
        self.assertTrue(two['status'].startswith('409'), two['body'])
        self.assertEqual(json.loads(two['body'])['actuelle']['texte'], 'Première version')
        page = self.get('/dossier', admin, 'id=DOS-001')
        self.assertEqual(page['status'], '200 OK', page['body'][:300])
        self.assertIn('id="n88-note"', page['body'])
        self.assertIn('v5688-crm.js', page['body'])
        found = self.get('/api440/m5688/palette', aide, 'q=demo')
        self.assertEqual(found['status'], '200 OK', found['body'][:300])
        self.assertIn('groupes', json.loads(found['body']))
        today = self.get('/aujourdhui', admin)
        self.assertEqual(today['status'], '200 OK', today['body'][:300])
        self.assertIn('id="t88-panel"', today['body'])
        self.assertNotIn('<!--t88', today['body'])

    def test_page_personnelle_par_compte_et_veille_visible(self):
        admin = self.admin_cookie()
        aide = self.add(admin, 'assistant@example.test', 'assistant')
        saved = self.api('m5688/tableau/enregistrer', {'ordre': 'journee,activite', 'accent': 'vert', 'voir:style': False}, aide)
        self.assertTrue(saved['status'].startswith('200'), saved['body'])
        mine = self.get('/aujourdhui', aide)['body']
        self.assertIn('data-accent="vert"', mine)
        self.assertLess(mine.index('id="c530-day"'), mine.index('id="c530-feed"'))
        self.assertNotIn('data-accent', self.get('/aujourdhui', admin)['body'])
        refused = self.api('m5688/tableau/enregistrer', {'accent': '#123456'}, aide)
        self.assertTrue(refused['status'].startswith('400'), refused['body'])
        self.api('m5688/veille/suivre', {'siren': SIREN, 'nom': 'ZORVAL', 'evt:radiation': True}, admin)
        page = self.get('/contacts-crm', aide, 'vue=veille')
        self.assertEqual(page['status'], '200 OK', page['body'][:300])
        self.assertIn('ZORVAL', page['body'])
        self.assertIn('Veille des sociétés', page['body'])

    def test_reglage_de_la_memoire_refuse_a_l_avocat(self):
        admin = self.admin_cookie()
        avocate = self.add(admin, 'avocate@example.test', 'avocat')
        refused = self.api('m5688/memoire/reglage', {'modele': True, 'plafond': 5}, avocate)
        self.assertEqual(refused['status'][:3], '403', refused['body'])
        self.assertTrue(self.api('m5688/memoire/reglage', {'actif': True, 'plafond': 5}, admin)['status'].startswith('200'))


class Ressources(unittest.TestCase):
    def test_declarees(self):
        from agent import shell501, web440
        self.assertIn('/static/v5688-crm.js', web440.STATIC)
        self.assertIn('v5688-crm.css', shell501.BUNDLE)
        self.assertIn('v5688-crm.js', Path(crm5688.__file__).with_name('ui_helpers.py').read_text(encoding='utf-8'))

    def test_module_registres_sans_veille_ni_interface(self):
        src = Path(reg.__file__).read_text(encoding='utf-8')
        for word in ('escape', '<section', 'societes5688', 'annonces5688'):
            self.assertNotIn(word, src)
        others = [p.name for p in Path(reg.__file__).parent.glob('*5688.py') if p.name != 'registres5688.py' and ('opendatasoft' in p.read_text(encoding='utf-8') or 'recherche-entreprises' in p.read_text(encoding='utf-8'))]
        self.assertEqual(others, [])

    def test_travaux_declares(self):
        from agent.desk import JOBS
        from agent.web import JOB_LABELS
        for kind in ('veille5688', 'memoire5688'):
            self.assertIn(kind, JOBS)
            self.assertIn(kind, JOB_LABELS)


if __name__ == '__main__':
    unittest.main()
