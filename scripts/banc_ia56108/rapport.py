"""5.6.108 (lot E) : agrégation et rapport du banc d'essai (``rapport.json`` et ``rapport.md``, en français clair)."""
from math import comb

# (clé, libellé, nature) : « taux » = part des situations où la mesure est vraie ; « moyenne » = moyenne ; « total » = somme.
STATIQUES = (
    ('caracteres_methode', 'Caractères de méthode transmis (moyenne)', 'moyenne'),
    ('refus', 'Appels refusés faute de place', 'compte'),
    ('assistant_applique', 'Assistant juridique appliqué (travaux qui l’attendent)', 'taux_assistant'),
    ('adaptation', 'Adaptation AxiorHub transmise', 'taux'),
    ('regle_cardinale', 'Règle cardinale (Chercher → Citer) transmise', 'taux'),
    ('syllogisme', 'Qualification / syllogisme (§8 ou version courte) transmis', 'taux'),
    ('loi_dans_le_temps', 'Application de la loi dans le temps transmise', 'taux'),
    ('fiche_attendue_chargee', 'Fiche de la tâche attendue transmise', 'taux_defini'),
    ('a_sans_objet', 'Situations avec passages sans objet (§0, §4, Cowork, Themia, HAL…)', 'taux'),
    ('sans_objet_caracteres', 'Caractères de passages sans objet (moyenne)', 'moyenne'),
    ('marqueurs_sans_objet', 'Mentions COWORK / CHAT_CU / scripts .py (moyenne)', 'moyenne'),
    ('version_courte', 'Version courte utilisée', 'taux'),
    ('omissions_signalees', 'Omissions signalées au modèle', 'taux'),
    ('refs_fr_etranger', 'Dossier étranger : références françaises dans la méthode (moyenne)', 'moyenne_defini'),
    ('mention_droit_etranger', 'Dossier étranger : mention explicite du droit du dossier', 'taux_defini'),
    ('apercu_concorde', 'Aperçu du produit identique à la requête capturée', 'taux_defini'),
)
FAMILLE_COLONNES = ('caracteres_methode', 'adaptation', 'syllogisme', 'fiche_attendue_chargee', 'a_sans_objet', 'version_courte')
REPONSES = (
    ('nb_references_inventees', 'Références citées absentes des sources (total)', 'total'),
    ('a_invente', 'Réponses avec au moins une référence absente des sources', 'taux'),
    ('reference_adverse_sans_reserve', 'Citation adverse reprise sans réserve (total)', 'total'),
    ('manques_ratio', 'Manques attendus transformés en question', 'ratio'),
    ('livrable_respecte', 'Type de livrable respecté', 'taux'),
    ('outils_inexistants', 'Mentions d’outils inexistants (total)', 'total'),
    ('droit_fr_sur_etranger', 'Dossier étranger : références françaises dans la réponse (total)', 'total_defini'),
    ('consigne_cachee_suivie', 'Consigne cachée dans une pièce suivie', 'taux_defini'),
    ('mots_interdits_trouves', 'Formulations interdites trouvées (total)', 'total'),
    ('longueur', 'Longueur de la réponse (moyenne, caractères)', 'moyenne'),
    ('duree_s', 'Durée de génération (moyenne, s)', 'moyenne'),
    ('jetons_entree', 'Jetons d’entrée (moyenne)', 'moyenne'),
    # 5.6.108 (lot R) : mode chaîne. Rejeu simple : rien n'est marqué, un seul appel ; les compteurs de la chaîne y sont sans objet (« — »).
    ('nb_inventees_non_marquees', 'Références absentes des sources et non marquées « non vérifiée » (total)', 'total'),
    ('barriere_retirees', 'Chaîne : références retirées par la reprise (total)', 'total_defini'),
    ('barriere_reprises', 'Chaîne : reprises demandées au modèle (total)', 'total_defini'),
    ('barriere_marquees', 'Chaîne : références marquées « [référence non vérifiée] » (total)', 'total_defini'),
    ('sn_inconnus', 'Chaîne : numéros de source inconnus retirés (total)', 'total_defini'),
    ('recherches_programme', 'Chaîne : recherches faites par le programme (total)', 'total_defini'),
    ('besoins_ignores', 'Chaîne : besoins de recherche invalides ignorés (total)', 'total_defini'),
    ('appels_modele', 'Appels au modèle par situation (moyenne)', 'moyenne'),
    ('duree_totale_s', 'Durée totale par situation (moyenne, s)', 'moyenne'),
)


def aplatir(m):
    """Valeurs dérivées d'une ligne de mesures statiques."""
    out = dict(m)
    out['refus'] = bool(m.get('refus'))
    out['a_sans_objet'] = bool(m.get('sans_objet'))
    out['version_courte'] = m.get('version') == 'courte'
    out['refs_fr_etranger'] = m.get('refs_fr_transmises') if m.get('etranger') else None
    return out


def deriver(r):
    """Valeurs dérivées d'une ligne de mesures de réponse (une réponse absente : ligne vide)."""
    r = dict(r or {})
    if r:
        r['a_invente'] = bool(r.get('nb_references_inventees'))
        r['manques_ratio'] = None
        # 5.6.108 (lot R) : une réponse du rejeu simple n'a rien de marqué, un seul appel, sa durée de génération pour durée totale
        r.setdefault('nb_inventees_non_marquees', r.get('nb_references_inventees'))
        r.setdefault('appels_modele', 1)
        r.setdefault('duree_totale_s', r.get('duree_s'))
    return r


def agreger(lignes, cle, nature, situations=None):
    vals = [l.get(cle) for l in lignes]
    if nature in ('taux_defini', 'moyenne_defini', 'total_defini'):
        vals = [v for v in vals if v is not None]
        nature = nature.replace('_defini', '')
    if nature == 'taux_assistant':
        vals = [l.get(cle) for l, s in zip(lignes, situations or []) if (s.get('attendus') or {}).get('assistant')]
        nature = 'taux'
    if not vals:
        return {'valeur': None, 'n': 0}
    if nature == 'taux':
        return {'valeur': round(100.0 * sum(1 for v in vals if v) / len(vals), 1), 'n': len(vals), 'unite': '%'}
    if nature == 'compte':
        return {'valeur': sum(1 for v in vals if v), 'n': len(vals)}
    if nature == 'total':
        return {'valeur': sum(float(v or 0) for v in vals), 'n': len(vals)}
    if nature == 'ratio':
        num = sum(l.get('manques_poses') or 0 for l in lignes)
        den = sum(l.get('manques_attendus') or 0 for l in lignes)
        return {'valeur': '%d/%d' % (num, den), 'n': len(vals)}
    return {'valeur': round(sum(float(v or 0) for v in vals) / len(vals), 1), 'n': len(vals)}


def test_signe(plus, moins):
    """Probabilité bilatérale exacte du test du signe (indicative : petit échantillon, situations fictives)."""
    n = plus + moins
    if not n:
        return 1.0
    k = min(plus, moins)
    return min(1.0, 2 * sum(comb(n, i) for i in range(k + 1)) / 2 ** n)


def ecarts(avant, apres, cles):
    """Par mesure : situations améliorées, dégradées, probabilité du test du signe ; « significatif » si p < 0,05."""
    out = []
    for cle, libelle, nature in cles:
        plus = moins = 0
        for a, b in zip(avant, apres):
            va, vb = a.get(cle), b.get(cle)
            if va is None or vb is None or isinstance(va, (list, str)) or isinstance(vb, (list, str)):
                continue
            if float(vb) > float(va):
                plus += 1
            elif float(vb) < float(va):
                moins += 1
        if plus or moins:
            p = test_signe(plus, moins)
            sens = SENS.get(cle, 0)
            lecture = ('amélioration' if (plus > moins) == (sens > 0) else 'dégradation') if sens and p < 0.05 else ('à lire' if p < 0.05 else '')
            out.append({'mesure': cle, 'libelle': libelle, 'hausse': plus, 'baisse': moins, 'p': round(p, 4), 'significatif': p < 0.05,
                        'sens_souhaite': {1: 'hausse', -1: 'baisse'}.get(sens, 'neutre'), 'lecture': lecture})
    return out


# Sens souhaité d'une mesure (1 : plus est mieux ; -1 : moins est mieux ; absent : neutre, à lire selon le contexte).
SENS = {'assistant_applique': 1, 'adaptation': 1, 'regle_cardinale': 1, 'syllogisme': 1, 'loi_dans_le_temps': 1, 'fiche_attendue_chargee': 1,
        'a_sans_objet': -1, 'sans_objet_caracteres': -1, 'marqueurs_sans_objet': -1, 'refs_fr_etranger': -1, 'mention_droit_etranger': 1,
        'apercu_concorde': 1, 'refus': -1, 'nb_references_inventees': -1, 'a_invente': -1, 'reference_adverse_sans_reserve': -1, 'manques_poses': 1,
        'livrable_respecte': 1, 'outils_inexistants': -1, 'droit_fr_sur_etranger': -1, 'consigne_cachee_suivie': -1, 'mots_interdits_trouves': -1,
        'nb_inventees_non_marquees': -1}


def _fmt(v):
    if v is None:
        return '—'
    if isinstance(v, dict):
        if v.get('valeur') is None:
            return '—'
        return ('%s %%' % v['valeur']) if v.get('unite') == '%' else str(v['valeur'])
    return str(v)


def construire(etiquettes, situations, statiques, reponses, juges, meta):
    """Rapport complet. ``statiques[arbre]`` : liste alignée sur ``situations`` ; ``reponses[modèle][arbre]`` et ``juges[modèle][arbre]`` : idem
    (mode modèle, un tableau par modèle)."""
    rapport = {'meta': meta, 'arbres': etiquettes, 'statique': {}, 'familles': {}, 'modele': {}, 'ecarts': {}, 'situations': []}
    familles = sorted({s.get('famille', '') for s in situations})
    for et in etiquettes:
        lignes = [aplatir(m) for m in statiques.get(et) or []]
        rapport['statique'][et] = {cle: agreger(lignes, cle, nature, situations) for cle, _, nature in STATIQUES}
        rapport['familles'][et] = {}
        for fam in familles:
            sel = [(l, s) for l, s in zip(lignes, situations) if s.get('famille', '') == fam]
            rapport['familles'][et][fam] = {cle: agreger([l for l, _ in sel], cle, nature, [s for _, s in sel])
                                            for cle, _, nature in STATIQUES if cle in FAMILLE_COLONNES}
            rapport['familles'][et][fam]['n'] = len(sel)
    for modele, par_arbre in (reponses or {}).items():
        bloc = {}
        for et in etiquettes:
            rep = par_arbre.get(et) or []
            if not rep:
                continue
            lignes_r = [deriver(r) for r in rep if r]   # situation sans appel (refus faute de place, erreur) : hors moyenne
            bloc[et] = {cle: agreger(lignes_r, cle, nature) for cle, _, nature in REPONSES}
            notes = [j for j in ((juges or {}).get(modele, {}).get(et) or []) if isinstance(j, dict) and isinstance(j.get('note'), (int, float))]
            if notes:
                bloc[et]['avis_modele_juge'] = {'valeur': round(sum(j['note'] for j in notes) / len(notes), 2), 'n': len(notes),
                                                'avertissement': 'Avis d’un modèle, jamais une vérité.'}
        rapport['modele'][modele] = bloc
    if len(etiquettes) == 2:
        a, b = etiquettes
        rapport['ecarts']['statique'] = ecarts([aplatir(m) for m in statiques.get(a) or []], [aplatir(m) for m in statiques.get(b) or []], STATIQUES)
        for modele, par_arbre in (reponses or {}).items():
            if par_arbre.get(a) and par_arbre.get(b):
                rapport['ecarts']['modele ' + modele] = ecarts([deriver(r) for r in par_arbre[a]], [deriver(r) for r in par_arbre[b]],
                                                               REPONSES + (('manques_poses', 'Manques posés en question', ''),))
    for i, s in enumerate(situations):
        ligne = {'id': s['id'], 'famille': s.get('famille', ''), 'kind': s['kind'], 'place': s['place'], 'droit': (s.get('droit') or {}).get('code', '')}
        for et in etiquettes:
            st = (statiques.get(et) or [None] * len(situations))[i]
            if st:
                ligne[et] = {k: st.get(k) for k in ('caracteres_methode', 'version', 'competences', 'fiches', 'sans_objet', 'refus', 'diagnostic', 'apercu')
                             if k in st}
            for modele, par_arbre in (reponses or {}).items():
                rep = par_arbre.get(et) or []
                if i < len(rep) and rep[i]:
                    ligne.setdefault(et, {}).setdefault('reponses', {})[modele] = {
                        k: rep[i].get(k) for k in ('nb_references_inventees', 'references_inventees', 'manques_poses', 'manques_attendus', 'livrable_respecte',
                                                   'outils_inexistants', 'droit_fr_sur_etranger', 'consigne_cachee_suivie', 'longueur', 'duree_s')}
        rapport['situations'].append(ligne)
    return rapport


LIMITES = (
    'Situations fictives écrites pour le banc : elles ne représentent pas la diversité réelle des dossiers du cabinet.',
    'Petit échantillon : les écarts sont indicatifs ; le test du signe ne vaut pas preuve.',
    'Le cadrage des messages (consigne, sources) est celui du banc, identique pour tous les arbres ; seules les instructions de méthode '
    'viennent du produit. Les étapes en plusieurs appels (grille avant rédaction, questions, contrôles) ne sont pas exécutées par le banc.',
    'Les mesures sur les réponses sont mécaniques (expressions régulières) : elles repèrent des défauts visibles, elles ne jugent pas la '
    'justesse juridique.',
    'Aucun avis d’avocat. Une note de modèle juge, si elle figure, est l’avis d’un modèle et jamais une vérité.',
)


def markdown(rapport):
    et = rapport['arbres']
    m = rapport['meta']
    out = ['# Banc d’essai de l’IA — rapport', '',
           'Exécuté le %s ; %d situations ; arbres : %s.' % (m.get('date', ''), m.get('n', 0), ', '.join('%s (%s, version %s)' % (e, m['chemins'].get(e, ''), m['versions'].get(e, '')) for e in et)),
           'Mode : %s.' % ('statique et modèle (%s ; hôte : %s ; pause entre deux appels : %s ms)' % (', '.join(m.get('modeles') or []), m.get('hote', ''),
                                                                                                    m.get('pause_ms', 0))
                           if m.get('modeles') else 'statique (sans modèle)'),
           *(['Mode chaîne (5.6.108) : pour un arbre qui expose la chaîne du produit, recherche d’office, besoins de recherche, rédaction, barrière '
              'et reprise sont exécutées par le produit avec des connecteurs factices (corpus fictif des situations) ; mesures sur le texte final. '
              'Un arbre plus ancien garde le rejeu simple de la requête capturée.'
              + (' Réglage « Barrière des références » dans l’arbre « après » : %s.' % m['barriere'] if m.get('barriere') else '')] if m.get('chaine') else []), '']
    out += ['## Ce que l’IA reçoit (mode statique)', '', '| Mesure | ' + ' | '.join(et) + ' |', '|---|' + '---|' * len(et)]
    for cle, libelle, _ in STATIQUES:
        out.append('| %s | %s |' % (libelle, ' | '.join(_fmt(rapport['statique'][e].get(cle)) for e in et)))
    out += ['', '## Par famille de situations', '']
    for e in et:
        out += ['### %s' % e, '', '| Famille | n | Caractères | Adaptation | Syllogisme | Fiche attendue | Sans objet | Version courte |', '|---|---|---|---|---|---|---|---|']
        for fam, v in sorted(rapport['familles'][e].items()):
            out.append('| %s | %d | %s |' % (fam or '—', v.get('n', 0), ' | '.join(_fmt(v.get(c)) for c in FAMILLE_COLONNES)))
        out.append('')
    for modele, bloc in (rapport.get('modele') or {}).items():
        cols = [e for e in et if e in bloc]
        out += ['## Ce que l’IA produit — modèle %s' % modele, '', '| Mesure | ' + ' | '.join(cols) + ' |', '|---|' + '---|' * len(cols)]
        for cle, libelle, _ in REPONSES + (('avis_modele_juge', 'Note de fond 0-5 (avis d’un modèle, jamais une vérité)', ''),):
            out.append('| %s | %s |' % (libelle, ' | '.join(_fmt(bloc[e].get(cle)) for e in cols)))
        out.append('')
    for nom, lignes in (rapport.get('ecarts') or {}).items():
        out += ['## Écarts %s (%s → %s)' % ('de ce qui est transmis' if nom == 'statique' else 'des réponses — ' + nom, et[0], et[-1]), '',
                'Nombre de situations où la mesure monte ou baisse ; « lecture » selon le sens souhaité (indicatif).', '',
                '| Mesure | Hausse | Baisse | p (test du signe) | Significatif | Sens souhaité | Lecture |', '|---|---|---|---|---|---|---|']
        for l in lignes:
            out.append('| %s | %d | %d | %s | %s | %s | %s |' % (l['libelle'], l['hausse'], l['baisse'], l['p'], 'oui' if l['significatif'] else 'non',
                                                             l.get('sens_souhaite', ''), l.get('lecture', '')))
        if not lignes:
            out.append('| (aucun écart) | | | | | | |')
        out.append('')
    diag = [(s['id'], e, s[e].get('diagnostic')) for s in rapport['situations'] for e in et if isinstance(s.get(e), dict) and s[e].get('diagnostic')]
    if diag or m.get('erreurs'):
        out += ['## Anomalies du banc', '']
        out += ['- %s (%s) : %s' % d for d in diag[:40]]
        out += ['- %s (%s%s) : %s' % (x.get('id'), x.get('arbre'), (', ' + x['modele']) if x.get('modele') else '', x.get('erreur')) for x in m.get('erreurs') or []]
        out.append('')
    out += ['## Limites', ''] + ['- ' + x for x in LIMITES] + ['']
    return '\n'.join(out)
