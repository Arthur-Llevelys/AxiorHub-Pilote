"""5.6.108 (lot S5) : rubrique « Synchronisation des postes » et indicateur traduits en anglais, allemand et espagnol par les dictionnaires des
écrans existants (``ecrans_*.json``, même mécanisme et même détecteur que ``test_v56107_langues``) ; français inchangé.

Harnais de ``test_v56108_sync`` ; données fictives.
"""
import json
import unittest

from agent import ecrans5632 as E, sync56108 as S, sync56108_missions as M, sync56108_web as W
from test_v56107_langues import visibles, reste_francais, LANGUES
from test_v56108_sync import Poste, relier

GET = {'HTTP_X_AXIORHUB_USER': 'cabinet', 'HTTP_X_AXIORHUB_ROLE': 'administrateur', 'REQUEST_METHOD': 'GET'}
AUTH = {'origin': 'http://127.0.0.1:8769', 'csrf': 'c' * 40, 'poste': True}


def ecrans(test):
    """La rubrique dans ses états principaux, l'en-tête et les libellés de l'indicateur."""
    out = {}
    a, b = Poste(test, 'Poste A', 'Me DURAND'), Poste(test, 'Poste B', 'Me MARTIN')
    out['poste seul'] = W.section_html(a.desk, AUTH, '/p', GET)
    S.ouvrir_rejoindre(b.desk, b.personne, 'cabinet')
    out['rejoindre'] = W.section_html(b.desk, AUTH, '/p', GET)
    relier(test, a, b)
    lid = a.dossier('Bail ZORVAL', 'ZOR-901')
    a.dossier('Affaire réservée', 'ZOR-902', confidentialite='reserve', membres=['cabinet'])
    M.attribuer(a.desk, 'Tri matinal des courriels', b.ident.id, routine='routine520:tri')
    b.sync()
    a.desk.db.execute("INSERT INTO sync56108_adhesions(poste,espace,personne,personne_nom,nom_poste,etat,recu_le) VALUES(?,?,?,?,?,'en_attente','x')",
                      ('c' * 32, S.espace_cabinet(a.desk)['id'], 'pers-' + 'c' * 32, 'Me BERNARD', 'Poste C'))
    a.desk.db.commit()
    from agent import liaison56103 as L, dossiers5697 as D
    from test_v56108_sync import ADMIN
    L.nouveau_code(a.desk)
    a.desk.db.execute("INSERT INTO liaison56103_decouverts(id,nom,adresse,port,vu_le) VALUES(?,?,?,?,?)",
                      ('d' * 32, 'Poste D', '.'.join(('10', '0', '0', '9')), 47862, S._maintenant()))
    a.desk.db.commit()
    out['espace'] = W.section_html(a.desk, AUTH, '/p', GET)
    b.dossier('Dossier local de B', 'ZOR-903')
    l3 = a.dossier('Dossier de A', 'ZOR-903')
    for x in (lid, l3):
        S.partager_dossier(a.desk, x)
    b.sync()
    a.sync()
    gid = a.objet('dossier', lid)['objet']
    lb = b.local(gid)
    D.modifier(a.desk, ADMIN, lid, 1, {'titre': 'Version de A'}, 'cle-langue-a-01')
    D.modifier(b.desk, ADMIN, lb, D.lire(b.desk, ADMIN, lb)['version'], {'titre': 'Version de B'}, 'cle-langue-b-01')
    S.journaliser(a.desk)
    b.sync()
    out['conflits B'] = W.section_html(b.desk, AUTH, '/p', GET)
    out['entete'] = W.indicateur_entete(a.c, '/p')
    S.desactiver(a.desk, True)
    out['desactivee'] = W.section_html(a.desk, AUTH, '/p', GET)
    out['libelles'] = ''.join('<p>%s</p>' % v for v in W.ETATS.values())
    out['libelles'] += ''.join('<p>%s</p>' % t for t in ('Poste absent : 3 changement(s) en attente', 'Conflit à résoudre (2)',
                                                         'Synchronisation en cours : 4 changement(s), 1 fichier(s) à recevoir',
                                                         'Connexion refusée : vérifier l’appairage ou les droits', 'Espace créé : aucun autre poste n’a encore rejoint',
                                                         'Poste en version ancienne : messages seulement, données non synchronisées',
                                                         'Rejoindre le cabinet : en attente d’acceptation'))
    out['messages'] = ''.join('<p>%s</p>' % v for v in W.MESSAGES.values())
    out['missions'] = ''.join('<p>%s</p>' % t for t in ('Attend le poste « Poste B »', 'Exécutée sur ce poste', 'Exécutée sur ce poste (reprise forcée)',
                                                       'Exécutée sur le poste « Poste B »', 'En pause (mission)', 'En pause (cabinet)', 'Terminée', 'Annulée'))
    return out


def ecrans_fr_temoin():
    return '<p>Rien n’est partagé sans votre choix. Un dossier partagé emporte ses tâches et son agenda.</p>'


class S5Langues(unittest.TestCase):
    def test_S5_rubrique_et_indicateur_traduits(self):
        for nom, html in ecrans(self).items():
            self.assertTrue(html, nom)
            for lang in LANGUES:
                traduit = E.translate_html(html, lang)
                self.assertNotEqual(traduit, html, '%s (%s)' % (nom, lang))          # la page change de langue
                restes = reste_francais(visibles(traduit))
                self.assertEqual(restes, [], '%s (%s)' % (nom, lang))
        self.assertTrue(reste_francais(visibles(W.section_html.__doc__ and ecrans_fr_temoin())))   # contre-exemple : le détecteur voit le français

    def test_S5_dictionnaires_memes_cles_dans_les_trois_langues(self):
        cles = [set(k for k in json.loads((E.ROOT / ('ecrans_%s.json' % l)).read_text(encoding='utf-8')) if 'postes du cabinet' in k.lower()
                    or 'espace partagé' in k.lower()) for l in LANGUES]
        self.assertTrue(cles[0])
        self.assertEqual(cles[0], cles[1])
        self.assertEqual(cles[0], cles[2])


if __name__ == '__main__':
    unittest.main()
