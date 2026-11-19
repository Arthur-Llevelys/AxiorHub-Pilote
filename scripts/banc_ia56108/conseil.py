"""5.6.108 (lot K) : mode « conseil » du banc d'essai de l'IA.

``python scripts/banc-ia.py --conseil ollama:<a>,ollama:<b>[,ollama:<c>] [--president ollama:<x>] [--hote-reseau-prive http://<adresse>:11434]
--sortie <dossier>`` mesure, sur les mêmes situations fictives (familles consultation, acte, audience, recherche par défaut), le **conseil de
modèles** du produit (``agent.conseil56108.deliberer`` : réponses indépendantes, revue croisée anonyme, synthèse du président, barrière des
références) contre **chaque membre seul** passé par la chaîne du lot R (``agent.references56108.produire``), et désigne le meilleur membre seul.

- Transport : ``chaine.Relais`` relie ``model.Model._leaf`` du produit au client ``ollama_local.Ollama`` du banc (mêmes gardes : Ollama local, ou
  réseau privé par option explicite, jamais un modèle « :cloud », adresse jamais recopiée) ; connecteurs factices (corpus fictif des situations).
- Fenêtre : le conseil et chaque membre seul reçoivent la même fenêtre, au moins ``FENETRE_MIN`` jetons (la revue croisée relit plusieurs
  réponses) ; la comparaison reste équitable.
- Mesures sur le texte final : références absentes des sources (et non marquées), références marquées, manques posés en question, argument
  adverse présent (mots-clés ou rubrique de la synthèse), désaccords signalés, type de livrable respecté, nombre d'appels, durée totale.
"""
import json
import re
import time

from . import chaine
from . import rapport as rapport_mod

FAMILLES = ('consultation', 'acte', 'audience', 'recherche')
FENETRE_MIN = 16000
ADVERSE = re.compile(r'argument adverse|partie adverse|l.adversaire|adversaire|objection|en d[ée]fense|pourrait (?:soutenir|objecter|opposer|invoquer)|'
                     r'soutiendra|contestera|thèse contraire|th[eè]se adverse', re.I)
RUBRIQUE_ADVERSE = re.compile(r'argument adverse le plus s[ée]rieux\s*[»:*#\s]*\n?\s*(\S.{10,})', re.I)


def argument_adverse(texte):
    """Argument adverse présent : rubrique de la synthèse remplie, ou mots-clés dans le texte final."""
    t = str(texte or '')
    return bool(RUBRIQUE_ADVERSE.search(t) or ADVERSE.search(t))


class ModeleScripteConseil(chaine.ModeleScripte):
    """Faux client des essais (aucun réseau) : réponse, revue (JSON), synthèse, reprise et chaîne du lot R selon le message système."""

    def __init__(self, modele='faux-a:8b', reponse='Réponse indépendante.', revue='{"non_soutenues": [], "references_douteuses": [], '
                 '"argument_adverse_manquant": [], "accords": [], "classement": []}', synthese='Synthèse.', redaction='Réponse seule.', reprise=None,
                 besoins='{"recherches": []}', parametres='8.0B', echec=False, grille='{"faits": [], "manques": []}'):
        super().__init__(besoins=besoins, redaction=redaction, reprise=reprise, modele=modele, parametres=parametres)
        self.reponse, self.revue, self.synthese, self.echec = reponse, revue, synthese, echec
        self.grille = grille

    def _post(self, chemin, corps):
        systeme = str((corps.get('messages') or [{}])[0].get('content') or '')
        if self.echec and 'Tu es membre d’un conseil' in systeme:
            self.requetes.append(corps)
            raise OSError('modèle indisponible (essai)')
        if 'Tu es membre d’un conseil' in systeme:
            texte = self.reponse
        elif systeme.startswith('Tu relis les réponses d’autres membres'):
            texte = self.revue
        elif systeme.startswith('Tu présides un conseil'):
            texte = self.synthese
        elif systeme.startswith('Tu prépares la grille'):
            texte = self.grille
        else:
            return super()._post(chemin, corps)
        self.requetes.append(corps)
        return {'done': True, 'done_reason': 'stop', 'message': {'role': 'assistant', 'content': texte}, 'prompt_eval_count': 100, 'eval_count': 20}


def situation_fenetre(situation):
    return dict(situation, place=max(int(situation['place']), FENETRE_MIN))


def executer(desk, situation, clients, president, graine, numero, max_tokens):
    """Le conseil du produit sur une situation : membres et président par ``Model._leaf`` relié au banc, connecteurs factices."""
    from agent import competences5632 as c32
    from agent import conseil56108 as K
    from agent import references56108 as R
    from agent.common import Stop
    from .travailleur import LIBELLES
    place = int(situation['place'])
    modeles = {}
    for c in list(clients) + [president]:
        if c.modele not in modeles:
            modeles[c.modele] = chaine.modele_produit(c, place, graine)

    def decrire(c):
        m = modeles[c.modele][0]
        return {'fournisseur': 'ollama', 'type': 'ollama', 'modele': c.modele, 'local': True, 'famille': K.famille(c.modele), 'version': '',
                'petit': R.petit_modele(cfg=dict(m.cfg, parameter_size=m.parameter_size))}

    def appeler(membre, messages, mt, schema=None):
        return modeles[membre['modele']][0]._leaf(messages, 0, mt, schema)
    droit = situation.get('droit') or {}
    args = {'instruction': situation['consigne'], 'matter': situation.get('dossier', ''), 'owner': 'cabinet'}
    row = {'id': numero, 'kind': situation['kind'], 'args': json.dumps(args), 'parent_job_id': None}
    cadre = ('Tu travailles pour un cabinet d’avocats. Travail demandé : %s. Livrable attendu : %s. Dossier : %s — juridiction : %s — droit applicable '
             'déclaré : %s.' % (LIBELLES.get(situation['kind'], situation['kind']), situation.get('livrable', ''), situation.get('dossier', ''),
                                droit.get('juridiction', ''), droit.get('libelle', '')))
    connecteurs = chaine.ConnecteursCorpus(situation.get('corpus') or [])
    debut = time.monotonic()
    with c32.for_job(desk, row, args):
        try:
            out = K.deliberer(situation['consigne'], situation.get('sources') or [], [decrire(c) for c in clients], decrire(president), appeler,
                              connecteurs, desk=desk, contexte=[situation.get('dossier', ''), 'RG %s' % droit['rg'] if droit.get('rg') else ''], graine=graine,
                              cadre=cadre, livrable=situation.get('livrable') or 'consultation',
                              calculer_grille=place <= 16384)   # même règle que la mission simple : grille pour un modèle à petit contexte (lot I)
        except Stop as erreur:
            return {'id': situation['id'], 'erreur': str(erreur), 'duree_totale_s': round(time.monotonic() - debut, 2)}
    appels = [a for _m, relais in modeles.values() for a in relais.appels]
    out.update(id=situation['id'], appels_relais=appels, recherches_corpus=connecteurs.requetes, duree_totale_s=round(time.monotonic() - debut, 2))
    out.pop('synthese_modele', None)
    return out


def mesurer_conseil(sortie, situation):
    """Mesures du conseil sur le texte final (mêmes mesures que la chaîne), plus désaccords, argument adverse et membres en échec."""
    c = sortie.get('compteurs') or {}
    vue = dict(sortie, appels=sortie.get('appels_relais') or [], appels_modele=int(sortie.get('appels') or 0),
               compteurs={k: c.get(k, 0) for k in ('inconnues', 'reprises', 'retirees', 'marquees', 'sn_inconnus', 'sn_valides', 'recherches', 'sources_ajoutees')})
    m = chaine.mesurer(vue, situation)
    m.update(argument_adverse=argument_adverse(sortie.get('texte')), desaccords=int(c.get('desaccords', 0)), desaccords_livrable=int(c.get('desaccords_livrable', 0)),
             points_retires=int(c.get('points_retires', 0)), questions_livrable=int(c.get('questions_livrable', 0)),
             questions_retirees=int(c.get('questions_retirees', 0)), pourcentages_retires=int(c.get('pourcentages_retires', 0)),
             membres_en_echec=int(c.get('membres_en_echec', 0)), mode=str(sortie.get('mode') or ''), fin='conseil')
    return m


def mesurer_seul(sortie, situation):
    m = chaine.mesurer(sortie, situation)
    m.update(argument_adverse=argument_adverse(sortie.get('texte')), desaccords=None, desaccords_livrable=None, points_retires=None, questions_livrable=None, questions_retirees=None, membres_en_echec=None)
    return m


MESURES = (
    ('nb_references_inventees', 'Références absentes des sources dans le texte final (total)', 'total', -1),
    ('nb_inventees_non_marquees', 'Références absentes des sources et non marquées (total)', 'total', -1),
    ('barriere_marquees', 'Références marquées « [référence non vérifiée] » (total)', 'total', 0),
    ('manques_ratio', 'Manques attendus posés en question', 'ratio', 1),
    ('argument_adverse', 'Argument adverse présent', 'taux', 1),
    ('questions_livrable', 'Questions « Informations à obtenir » dans le livrable (total)', 'total_defini', 0),
    ('questions_retirees', 'Questions retirées, sans manque réel (total)', 'total_defini', -1),
    ('desaccords_livrable', 'Points de désaccord signalés dans le livrable (total)', 'total_defini', 0),
    ('points_retires', 'Points de désaccord retirés, sans critique réelle (total)', 'total_defini', -1),
    ('desaccords', 'Critiques de la revue croisée, dans la trace (total)', 'total_defini', 0),
    ('livrable_respecte', 'Type de livrable respecté', 'taux', 1),
    ('membres_en_echec', 'Membres en échec ou hors délai (total)', 'total_defini', -1),
    ('appels_modele', 'Appels au modèle par situation (moyenne)', 'moyenne', -1),
    ('duree_totale_s', 'Durée totale par situation (moyenne, s)', 'moyenne', -1),
)


def _lignes(liste):
    out = []
    for r in liste:
        if r:
            r = dict(r)
            r['manques_ratio'] = None
            out.append(r)
    return out


def score(lignes):
    """Classement d'un membre seul : moins de références absentes non marquées, puis plus de manques posés, de livrables respectés, d'arguments
    adverses présents (indicatif)."""
    ls = [x for x in lignes if x]
    return (sum(x.get('nb_inventees_non_marquees') or 0 for x in ls), -sum(x.get('manques_poses') or 0 for x in ls),
            -sum(1 for x in ls if x.get('livrable_respecte')), -sum(1 for x in ls if x.get('argument_adverse')), -len(ls))


def ecarts(meilleur, conseil):
    out = []
    for cle, libelle, _nature, sens in MESURES:
        plus = moins = 0
        for a, b in zip(meilleur, conseil):
            if not a or not b:
                continue
            va, vb = a.get(cle), b.get(cle)
            if va is None or vb is None:
                continue
            if float(vb) > float(va):
                plus += 1
            elif float(vb) < float(va):
                moins += 1
        if plus or moins:
            p = rapport_mod.test_signe(plus, moins)
            lecture = ('amélioration' if (plus > moins) == (sens > 0) else 'dégradation') if sens and p < 0.05 else ('à lire' if p < 0.05 else '')
            out.append({'mesure': cle, 'libelle': libelle, 'hausse': plus, 'baisse': moins, 'p': round(p, 4), 'significatif': p < 0.05,
                        'sens_souhaite': {1: 'hausse', -1: 'baisse'}.get(sens, 'neutre'), 'lecture': lecture})
    return out


def construire(situations, seuls, conseil, meta):
    """``seuls`` : {modèle: [mesures alignées sur les situations]} ; ``conseil`` : idem pour le conseil."""
    bloc = {}
    for nom, liste in list(seuls.items()) + [('conseil', conseil)]:
        lignes = _lignes(liste)
        bloc[nom] = {cle: rapport_mod.agreger(lignes, cle, nature) for cle, _l, nature, _s in MESURES}
    meilleur = min(seuls, key=lambda k: score(seuls[k])) if seuls else ''
    familles = {}
    for fam in sorted({s.get('famille', '') for s in situations}):
        idx = [i for i, s in enumerate(situations) if s.get('famille', '') == fam]
        familles[fam] = {'n': len(idx)}
        for nom, liste in ([(meilleur, seuls[meilleur])] if meilleur else []) + [('conseil', conseil)]:
            lignes = _lignes([liste[i] for i in idx])
            familles[fam][nom] = {cle: rapport_mod.agreger(lignes, cle, nature) for cle, _l, nature, _s in MESURES
                                  if cle in ('nb_inventees_non_marquees', 'argument_adverse', 'livrable_respecte', 'appels_modele')}
    return {'meta': meta, 'colonnes': list(seuls) + ['conseil'], 'meilleur_membre_seul': meilleur, 'mesures': bloc, 'familles': familles,
            'ecarts': ecarts(seuls.get(meilleur) or [], conseil) if meilleur else [],
            'situations': [{'id': s['id'], 'famille': s.get('famille', ''), 'kind': s['kind'],
                            'conseil': {k: (conseil[i] or {}).get(k) for k in ('nb_inventees_non_marquees', 'barriere_marquees', 'desaccords_livrable', 'desaccords', 'argument_adverse',
                                                                               'livrable_respecte', 'appels_modele', 'duree_totale_s', 'mode')} if conseil[i] else None}
                           for i, s in enumerate(situations)]}


LIMITES = (
    'Situations fictives écrites pour le banc ; petit échantillon : les écarts sont indicatifs, le test du signe ne vaut pas preuve.',
    'Le meilleur membre seul est désigné par un classement mécanique (références absentes non marquées, manques posés, livrable, argument '
    'adverse) : il ne dit rien de la justesse juridique.',
    'Un prompt différent sur le même modèle n’est pas un contrôle indépendant ; deux modèles différents peuvent répéter une erreur : la valeur '
    'vient des preuves. Aucun avis d’avocat.',
    'Le conseil et chaque membre seul reçoivent la même fenêtre (au moins %d jetons).' % FENETRE_MIN,
)


def markdown(rap):
    m = rap['meta']
    cols = rap['colonnes']
    out = ['# Banc d’essai de l’IA — conseil de modèles', '',
           'Exécuté le %s ; %d situations (familles : %s) ; arbre : %s (version %s).' % (m.get('date', ''), m.get('n', 0), ', '.join(m.get('familles') or []),
                                                                                      m.get('chemin', ''), m.get('version', '')),
           'Membres : %s ; président : %s ; hôte : %s ; pause entre deux appels : %s ms.' % (', '.join(m.get('membres') or []), m.get('president', ''),
                                                                                          m.get('hote', ''), m.get('pause_ms', 0)),
           'Meilleur membre seul (chaîne du lot R, classement mécanique) : %s.' % (rap.get('meilleur_membre_seul') or '—'), '',
           '## Conseil contre chaque membre seul', '', '| Mesure | ' + ' | '.join(('%s (meilleur seul)' % c) if c == rap.get('meilleur_membre_seul') else c
                                                                                  for c in cols) + ' |', '|---|' + '---|' * len(cols)]
    for cle, libelle, _n, _s in MESURES:
        out.append('| %s | %s |' % (libelle, ' | '.join(rapport_mod._fmt(rap['mesures'][c].get(cle)) for c in cols)))
    out += ['', '## Par famille (meilleur membre seul → conseil)', '', '| Famille | n | Réf. absentes non marquées | Argument adverse | Livrable | Appels |',
            '|---|---|---|---|---|---|']
    best = rap.get('meilleur_membre_seul')
    for fam, v in sorted(rap['familles'].items()):
        def paire(cle):
            a = rapport_mod._fmt((v.get(best) or {}).get(cle)) if best else '—'
            return '%s → %s' % (a, rapport_mod._fmt((v.get('conseil') or {}).get(cle)))
        out.append('| %s | %d | %s | %s | %s | %s |' % (fam, v['n'], paire('nb_inventees_non_marquees'), paire('argument_adverse'), paire('livrable_respecte'),
                                                        paire('appels_modele')))
    out += ['', '## Écarts du conseil par rapport au meilleur membre seul', '',
            '| Mesure | Hausse | Baisse | p (test du signe) | Significatif | Sens souhaité | Lecture |', '|---|---|---|---|---|---|---|']
    out += ['| %s | %d | %d | %s | %s | %s | %s |' % (x['libelle'], x['hausse'], x['baisse'], x['p'], 'oui' if x['significatif'] else 'non', x['sens_souhaite'],
                                                     x['lecture']) for x in rap['ecarts']] or ['| (aucun écart) | | | | | | |']
    if m.get('erreurs'):
        out += ['', '## Anomalies du banc', ''] + ['- %s (%s) : %s' % (x.get('id'), x.get('qui', ''), x.get('erreur')) for x in m['erreurs']]
    out += ['', '## Limites', ''] + ['- ' + x for x in LIMITES] + ['']
    return '\n'.join(out)
