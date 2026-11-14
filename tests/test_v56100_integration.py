"""5.6.100 (intégration) : raccords entre lots, vérifiés de bout en bout sur données fictives.

- V100-07 : une note « Nouvelles écritures » (lot G) arrive dans la file commune « À décider / À relire » (lot B pour les courriels,
  ``file5651`` pour Aujourd'hui, Produire et la page du dossier), sur le bon dossier, avec le lien vers l'onglet où l'on décide ; elle en sort
  une fois retenue ; elle n'apparaît jamais à un compte qui n'a pas le dossier.
- R05 / § 24 : ouvrir Aujourd'hui n'écrit plus « dossier vu » pour chaque dossier (1 800 écritures par ouverture pour 900 dossiers).
"""
import json
import unittest

import test_v56100_ia as ia
from agent import delta56100 as D, file5651, membres5650

MID, AVOCAT = ia.MID, ia.AVOCAT


class FileCommune(ia.Base):
    def note_a_relire(self):
        self.base_validee()
        self.version('conclusions-zorval-1.txt', ia.ANCIENNES, source_id='src-zorval')
        v2 = self.version('conclusions-zorval-2.txt', ia.NOUVELLES, source_id='src-zorval-2')
        eid = self.recevoir(v2)
        self.calculer(eid, ia.ModeleFaux(ia.analyse_complete))
        return self.resultats()[0]

    def file(self, compte=AVOCAT):
        return [i for i in file5651.items(self.desk, '/p', None, '', frozenset(), actor=compte[0], role=compte[1])
                if str(i.get('object') or '').startswith('delta56100:')]

    def test_V100_07_note_dans_la_file_commune_puis_retiree_apres_decision(self):
        r = self.note_a_relire()
        cartes = self.file()
        self.assertEqual(len(cartes), 1)
        carte = cartes[0]
        self.assertEqual((carte['matter'], carte['class']), (MID, 'livrable'))
        self.assertIn('livrable', file5651.FILTERS['relire'])                     # rangée dans « À relire »
        self.assertIn('/contentieux?dossier=%s&onglet=ecritures#delta-%s' % (MID, r['id']), carte['href'])
        self.assertTrue(carte['title'].startswith('Nouvelles écritures'))
        self.assertNotIn('Source à traiter', carte['title'])
        D.decider(self.desk, AVOCAT, r['id'], {'action': 'retenir', 'cle': self.cle(), 'revision': r['revision'], 'propositions': [0]})
        self.assertEqual(self.file(), [])                                          # retenue : quitte la file

    def test_V100_07_dossier_reserve_aucune_carte(self):
        self.note_a_relire()
        membres5650.set_reserve(self.desk.c['state_dir'], MID, ['autre@example.test'])
        self.assertEqual(self.file(), [])
        self.assertEqual(D.queue_items(self.desk, '/p', AVOCAT[0], AVOCAT[1]), [])  # le producteur filtre aussi, avant la borne


class AujourdhuiSansEcriture(unittest.TestCase):
    def test_R05_suggestions_de_facturation_en_lecture_n_ecrivent_rien(self):
        import test_v5653 as base53
        cas = base53.Billing(methodName='setUp')
        cas.setUp()
        self.addCleanup(cas.doCleanups)
        from agent import facturation5653 as fact, facturation5655 as f55
        desk = cas.desk
        f55.note_seen(desk)                                   # référence posée une fois (premier passage)
        avant = desk.db.execute('SELECT COUNT(*) FROM b55_matter_seen').fetchone()[0]
        ecritures = []
        desk.db.set_trace_callback(lambda sql: ecritures.append(sql) if sql.lstrip().upper().startswith(('INSERT', 'UPDATE', 'DELETE')) else None)
        try:
            fact.suggestions(desk, refresh=False)
            f55.note_seen(desk)                               # rien de nouveau : aucune écriture
        finally:
            desk.db.set_trace_callback(None)
        self.assertEqual([s for s in ecritures if 'b55_matter_seen' in s], [])
        self.assertEqual(desk.db.execute('SELECT COUNT(*) FROM b55_matter_seen').fetchone()[0], avant)



class MobileSansCompteurHorsDroits(ia.Base):
    def test_R04_notification_mobile_compte_seulement_le_perimetre(self):
        from agent import echeances450 as ech, mobile490
        ech.ensure_schema(self.desk)
        for i, matter in enumerate((MID, '')):
            self.desk.db.execute("INSERT OR REPLACE INTO work_items(mail_key,state,matter,subject) VALUES(?,?,?,?)",
                                 ('%064x' % (i + 1), 'draft_ready', matter, 'Objet fictif'))
        jour = mobile490.date.today()
        for i, matter in enumerate((MID, 'DOS-002')):
            self.desk.db.execute("INSERT INTO deadlines450(id,matter,rule_id,start_event,start_date,due,status,origin,note,created,updated) "
                                 "VALUES(?,?,'r','e',?,?,'confirmee','main','n','x','x')", ('e%d' % i, matter, jour.isoformat(), jour.isoformat()))
        self.desk.db.commit()
        self.assertEqual(mobile490.counts(self.desk, jour)['drafts'], 2)
        membres5650.set_viewer({MID})
        self.addCleanup(membres5650.set_viewer, None)
        c = mobile490.counts(self.desk, jour)
        self.assertEqual((c['drafts'], c['deadlines']), (1, 1))


if __name__ == '__main__':
    unittest.main()
