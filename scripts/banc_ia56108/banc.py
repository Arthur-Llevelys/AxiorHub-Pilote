"""5.6.108 (lot E) : orchestration du banc d'essai de l'IA (avant/après sur les mêmes situations fictives).

Chaque arbre est mesuré dans son propre sous-processus (``travailleur.py``) avec ``PYTHONPATH`` réduit à cet arbre et
``PYTHONDONTWRITEBYTECODE`` : le paquet ``agent`` importé est bien celui de l'arbre, et rien n'est écrit dans l'arbre mesuré. La base est
temporaire. Le rapport n'est écrit que dans ``--sortie``, jamais dans un arbre mesuré.
"""
import argparse
from datetime import datetime
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
from zoneinfo import ZoneInfo

ICI = Path(__file__).resolve().parent
RACINE = ICI.parents[1]
SITUATIONS = RACINE / 'tests' / 'donnees' / 'banc_ia' / 'situations.json'

from . import mesures, ollama_local
from . import rapport as rapport_mod
from . import situations as situations_mod


def lancer_arbre(arbre, situations, python=None, delai=1800):
    """Sortie du travailleur pour un arbre : résultats bruts, erreurs, paquet « Assistant juridique » de l'arbre."""
    arbre = Path(arbre).resolve()
    if not (arbre / 'agent' / '__init__.py').is_file():
        raise SystemExit('Arbre invalide (pas de paquet agent) : %s' % arbre)
    with tempfile.TemporaryDirectory(prefix='banc-ia-E-') as tmp:
        entree, sortie = Path(tmp) / 'entree.json', Path(tmp) / 'sortie.json'
        entree.write_text(json.dumps({'situations': situations}, ensure_ascii=False), encoding='utf-8')
        env = {k: v for k, v in os.environ.items() if k not in ('PYTHONPATH', 'PYTHONHOME', 'PYTHONSTARTUP')}
        env.update({'PYTHONPATH': str(arbre), 'PYTHONDONTWRITEBYTECODE': '1', 'PYTHONUTF8': '1', 'PYTHONIOENCODING': 'utf-8'})
        proc = subprocess.run([python or sys.executable, str(ICI / 'travailleur.py'), str(entree), str(sortie)], cwd=tmp, env=env,
                              capture_output=True, text=True, encoding='utf-8', errors='replace', timeout=delai)
        if proc.returncode != 0 or not sortie.is_file():
            raise SystemExit('Le travailleur a échoué sur %s :\n%s' % (arbre, (proc.stderr or proc.stdout)[-3000:]))
        return json.loads(sortie.read_text(encoding='utf-8'))


def lancer_chaine(arbre, situations, conf, python=None, delai=7200):
    """5.6.108 (lot R) : mode « chaîne » d'un arbre, dans un sous-processus dont ``PYTHONPATH`` est l'arbre mesuré. ``disponible`` faux : l'arbre
    n'expose pas ``agent.references56108.produire`` (5.6.107) et le banc garde le rejeu simple. ``conf`` : modèle, hôte déjà validé, portée,
    pause, graine (``faux`` : client scripté des essais, aucun réseau)."""
    arbre = Path(arbre).resolve()
    with tempfile.TemporaryDirectory(prefix='banc-ia-R-') as tmp:
        entree, sortie = Path(tmp) / 'entree.json', Path(tmp) / 'sortie.json'
        entree.write_text(json.dumps({'situations': situations, 'chaine': conf}, ensure_ascii=False), encoding='utf-8')
        env = {k: v for k, v in os.environ.items() if k not in ('PYTHONPATH', 'PYTHONHOME', 'PYTHONSTARTUP')}
        env.update({'PYTHONPATH': str(arbre), 'PYTHONDONTWRITEBYTECODE': '1', 'PYTHONUTF8': '1', 'PYTHONIOENCODING': 'utf-8'})
        proc = subprocess.run([python or sys.executable, str(ICI / 'travailleur_chaine.py'), str(entree), str(sortie)], cwd=tmp, env=env,
                              capture_output=True, text=True, encoding='utf-8', errors='replace', timeout=delai)
        if proc.returncode != 0 or not sortie.is_file():
            masque = (proc.stderr or proc.stdout)[-3000:]
            hote = str(conf.get('hote') or '')
            if hote:   # l'adresse du serveur n'est jamais recopiée dans un message
                from urllib.parse import urlsplit
                masque = masque.replace(hote, '<hôte>').replace(urlsplit(hote).hostname or '\0', '<hôte>')
            raise SystemExit('Le travailleur de la chaîne a échoué sur %s :\n%s' % (arbre, masque))
        return json.loads(sortie.read_text(encoding='utf-8'))


def mesurer_chaine(brut, situations, juge=None, graine=42):
    """Mesures du mode chaîne alignées sur les situations (None pour une situation en erreur), et notes du juge éventuel sur le texte final."""
    from . import chaine
    par_id = {r['id']: r for r in brut.get('resultats') or []}
    reponses, notes, erreurs = [], [], []
    for s in situations:
        r = par_id.get(s['id'])
        if not r or r.get('erreur'):
            if r and r.get('erreur'):
                erreurs.append({'id': s['id'], 'erreur': 'chaîne : %s' % str(r['erreur'])[:200]})
            reponses.append(None)
            notes.append(None)
            continue
        m = chaine.mesurer(r, s)
        m['texte'] = str(r.get('texte') or '')
        reponses.append(m)
        notes.append(juger(juge, s, m['texte'], graine) if juge else None)
    return reponses, notes, erreurs


def mesurer_statique(brut, situations, textes=False):
    """Mesures statiques alignées sur les situations (None pour une situation en erreur)."""
    reps = mesures.reperes(brut.get('paquet'))
    par_id = {r['id']: r for r in brut.get('resultats') or []}
    out = []
    for s in situations:
        r = par_id.get(s['id'])
        if r is None:
            out.append(None)
            continue
        m = mesures.mesures_statiques(r, s, reps)
        if r.get('apercu') is not None:
            m['apercu'] = r['apercu']
        if textes:   # --textes : méthode exacte transmise, pour relecture (rapport.json seulement)
            m['methode_texte'] = r.get('methode') or ''
        out.append(m)
    return out, reps


def juger(juge, situation, reponse, graine):
    """Note de fond par un modèle local plus gros : avis d'un modèle, jamais une vérité."""
    consigne = ('Tu évalues la réponse d’un assistant pour un cabinet d’avocats, à partir des seules sources fournies. Réponds en JSON '
                '{"note": entier de 0 à 5, "motif": "une phrase"}. 5 = fidèle aux sources, droit applicable respecté, livrable adapté, '
                'aucune référence absente des sources ; 0 = inventions ou mauvais droit.')
    sources = '\n'.join('[%s] %s : %s' % (x['id'], x['titre'], x['texte']) for x in situation.get('sources') or [])
    contenu = 'Consigne : %s\nDroit du dossier : %s\nSources :\n%s\n\nRéponse évaluée :\n%s' % (
        situation['consigne'], (situation.get('droit') or {}).get('libelle', ''), sources, reponse)
    try:
        r = juge.chat([{'role': 'system', 'content': consigne}, {'role': 'user', 'content': contenu}], 16000, 300, graine, format_json=True)
        valeur = json.loads(r['texte'])
        note = valeur.get('note')
        return {'note': max(0, min(5, int(note))) if isinstance(note, (int, float)) else None, 'motif': str(valeur.get('motif') or '')[:300],
                'avertissement': 'Avis d’un modèle, jamais une vérité.'}
    except (ValueError, TypeError, OSError) as erreur:
        return {'note': None, 'erreur': juge.masquer(erreur)[:200]}


def mesurer_modele(client, brut, situations, graine, juge=None, journal=print, erreurs=None):
    par_id = {r['id']: r for r in brut.get('resultats') or []}
    reponses, notes = [], []
    for s in situations:
        r = par_id.get(s['id'])
        if not r or r.get('refus') or not r.get('messages_envoyes'):
            reponses.append(None)
            notes.append(None)
            continue
        journal('  %s (%s, %d jetons)…' % (s['id'], s['kind'], s['place']))
        try:
            sortie = client.chat(r['messages_envoyes'], s['place'], int((r.get('options') or {}).get('num_predict') or 1000), graine,
                                 requete=r.get('requete'))
        except ollama_local.Refus:
            raise
        except (OSError, ValueError) as erreur:   # modèle occupé, délai, réponse illisible : noté, la passe continue
            if erreurs is not None:
                erreurs.append({'id': s['id'], 'modele': client.modele, 'erreur': 'appel au modèle : %s' % client.masquer(erreur)})
            reponses.append(None)
            notes.append(None)
            continue
        m = mesures.mesures_reponse(sortie['texte'], s)
        m.update({k: sortie[k] for k in ('duree_s', 'jetons_entree', 'jetons_sortie', 'fin')})
        m['texte'] = sortie['texte']
        reponses.append(m)
        notes.append(juger(juge, s, sortie['texte'], graine) if juge else None)
    return reponses, notes


def dans(chemin, parent):
    try:
        Path(chemin).resolve().relative_to(Path(parent).resolve())
        return True
    except ValueError:
        return False


def analyser(argv=None):
    p = argparse.ArgumentParser(prog='banc-ia.py', description='Banc d’essai avant/après de l’IA (situations fictives).')
    p.add_argument('--avant', help='arbre « avant » (lecture seule), par exemple ../src56107')
    p.add_argument('--apres', default=str(RACINE), help='arbre « après » (par défaut : cet arbre)')
    p.add_argument('--situations', default=str(SITUATIONS))
    p.add_argument('--ids', default='', help='identifiants de situations séparés par des virgules')
    p.add_argument('--famille', default='', help='une famille seulement (courriel, consultation, acte, pieces, recherche, audience, controle, question)')
    p.add_argument('--max', type=int, default=0, help='nombre maximal de situations')
    p.add_argument('--modele', action='append', default=[], help='mode modèle : ollama:<nom>, répétable (un tableau par modèle ; jamais « :cloud »)')
    p.add_argument('--juge', default='', help='note de fond par un modèle local : ollama:<nom> (avis d’un modèle)')
    p.add_argument('--hote', default=ollama_local.HOTE_DEFAUT, help='Ollama local (127.0.0.1, localhost ou ::1 seulement)')
    p.add_argument('--hote-reseau-prive', default='', help='Ollama du réseau privé du cabinet : http://<adresse IP privée>:<port> '
                   '(10/8, 172.16/12, 192.168/16, fc00::/7 ; jamais un nom ; jamais lu dans OLLAMA_HOST ; adresse jamais recopiée dans le rapport)')
    p.add_argument('--pause-ms', type=int, default=0, help='pause minimale entre deux appels au modèle, en millisecondes (défaut 0)')
    p.add_argument('--graine', type=int, default=42)
    p.add_argument('--sortie', default='', help='dossier où écrire rapport.json et rapport.md (hors des arbres mesurés)')
    p.add_argument('--python', default=sys.executable, help='interpréteur des sous-processus')
    p.add_argument('--textes', action='store_true', help='joindre à rapport.json le texte exact de la méthode transmise')
    p.add_argument('--chaine', action='store_true', help='mode chaîne (5.6.108) : pour un arbre qui expose agent.references56108.produire, '
                   'recherche d’office, besoins, rédaction, barrière et reprise par le produit, connecteurs factices (corpus fictif) ; '
                   'un arbre plus ancien garde le rejeu simple. Exige --modele.')
    p.add_argument('--barriere', choices=('petits', 'locaux', 'tous'), default='', help='mode chaîne : réglage « Barrière des références » '
                   'appliqué dans l’arbre « après » (petits : petits modèles seulement ; locaux : tous les modèles locaux, défaut du produit ; '
                   'tous : y compris les fournisseurs d’API). Absent : défaut du produit.')
    p.add_argument('--barriere-tous', dest='barriere', action='store_const', const='tous', help='raccourci de --barriere tous')
    p.add_argument('--conseil', default='', help='mode conseil (5.6.108, lot K) : ollama:<a>,ollama:<b>[,ollama:<c>] ; le conseil de modèles du produit '
                   'contre chaque membre seul (chaîne du lot R), familles consultation, acte, audience et recherche par défaut, connecteurs factices')
    p.add_argument('--president', default='', help='mode conseil : président ollama:<x> (par défaut, le premier membre)')
    return p.parse_args(argv)


def choisir(toutes, ids='', famille='', maximum=0):
    sel = list(toutes)
    if ids:
        voulu = [x.strip() for x in ids.split(',') if x.strip()]
        sel = [s for s in sel if s['id'] in voulu]
    if famille:
        sel = [s for s in sel if s.get('famille') == famille]
    return sel[:maximum] if maximum > 0 else sel


def lancer_conseil(arbre, situations, conf, python=None, delai=14400):
    """5.6.108 (lot K) : mode « conseil » d'un arbre, dans un sous-processus dont ``PYTHONPATH`` est l'arbre mesuré. ``disponible`` faux : l'arbre
    n'expose pas ``agent.conseil56108.deliberer``."""
    arbre = Path(arbre).resolve()
    if not (arbre / 'agent' / '__init__.py').is_file():
        raise SystemExit('Arbre invalide (pas de paquet agent) : %s' % arbre)
    with tempfile.TemporaryDirectory(prefix='banc-ia-K-') as tmp:
        entree, sortie = Path(tmp) / 'entree.json', Path(tmp) / 'sortie.json'
        entree.write_text(json.dumps({'situations': situations, 'conseil': conf}, ensure_ascii=False), encoding='utf-8')
        env = {k: v for k, v in os.environ.items() if k not in ('PYTHONPATH', 'PYTHONHOME', 'PYTHONSTARTUP')}
        env.update({'PYTHONPATH': str(arbre), 'PYTHONDONTWRITEBYTECODE': '1', 'PYTHONUTF8': '1', 'PYTHONIOENCODING': 'utf-8'})
        proc = subprocess.run([python or sys.executable, str(ICI / 'travailleur_conseil.py'), str(entree), str(sortie)], cwd=tmp, env=env,
                              capture_output=True, text=True, encoding='utf-8', errors='replace', timeout=delai)
        if proc.returncode != 0 or not sortie.is_file():
            masque = (proc.stderr or proc.stdout)[-3000:]
            hote = str(conf.get('hote') or '')
            if hote:   # l'adresse du serveur n'est jamais recopiée dans un message
                from urllib.parse import urlsplit
                masque = masque.replace(hote, '<hôte>').replace(urlsplit(hote).hostname or '\0', '<hôte>')
            raise SystemExit('Le travailleur du conseil a échoué sur %s :\n%s' % (arbre, masque))
        return json.loads(sortie.read_text(encoding='utf-8'))


def principal_conseil(a, journal=print, ouvrir=None, faux_conseil=None):
    """5.6.108 (lot K) : conseil de modèles contre chaque membre seul. Mêmes gardes que les autres modes : hôte admis (local, ou réseau privé
    par option explicite, adresse jamais recopiée), aucun modèle « :cloud », sortie hors de l'arbre mesuré."""
    from . import conseil as conseil_mod
    if a.modele or a.chaine or a.avant or a.juge:
        raise ollama_local.Refus('options incompatibles : --conseil se lance seul (sans --modele, --chaine, --avant ni --juge)')
    if a.pause_ms < 0:
        raise ollama_local.Refus('pause refusée : --pause-ms doit être positif ou nul')
    prive = bool(a.hote_reseau_prive)
    if prive and a.hote != ollama_local.HOTE_DEFAUT:
        raise ollama_local.Refus('options incompatibles : --hote et --hote-reseau-prive')
    hote = a.hote_reseau_prive if prive else a.hote
    hote = ollama_local.verifier_hote_prive(hote) if prive else ollama_local.verifier_hote(hote)
    membres = []
    for cible in [x.strip() for x in a.conseil.split(',') if x.strip()]:
        nom = ollama_local.analyser_cible(cible)
        if nom not in membres:
            membres.append(nom)
    if not 2 <= len(membres) <= 3:
        raise ollama_local.Refus('mode conseil : deux ou trois membres distincts, par exemple --conseil ollama:<a>,ollama:<b>')
    president = ollama_local.analyser_cible(a.president) if a.president else membres[0]
    if a.sortie and dans(a.sortie, a.apres):
        raise ollama_local.Refus('sortie refusée : le rapport ne s’écrit jamais dans un arbre mesuré (%s)' % a.sortie)
    toutes = situations_mod.charger(a.situations)
    situations = choisir(toutes, a.ids, a.famille)
    if not a.ids and not a.famille:
        situations = [s for s in situations if s.get('famille') in conseil_mod.FAMILLES]
    if a.max > 0:   # la limite s'applique APRÈS le choix des familles de jugement (sinon --max retenait surtout des courriels, écartés ensuite)
        situations = situations[:a.max]
    if not situations:
        raise SystemExit('Aucune situation retenue.')
    conf = {'membres': membres, 'president': president, 'hote': hote, 'reseau_prive': prive, 'pause_ms': a.pause_ms, 'graine': a.graine}
    if faux_conseil is not None:
        conf['faux'] = faux_conseil
    journal('Conseil de modèles : %s ; président %s ; %d situations…' % (', '.join(membres), president, len(situations)))
    brut = lancer_conseil(a.apres, situations, conf, a.python)
    if not brut.get('disponible'):
        raise SystemExit('Cet arbre n’expose pas le conseil de modèles (agent.conseil56108.deliberer) : %s' % a.apres)
    par_id = {nom: {r['id']: r for r in liste} for nom, liste in (brut.get('seuls') or {}).items()}
    conseil_par_id = {r['id']: r for r in brut.get('conseil') or []}
    erreurs = list(brut.get('erreurs') or [])
    seuls = {}
    for nom in membres:
        lignes = []
        for s in situations:
            r = par_id.get(nom, {}).get(s['id'])
            if r and r.get('erreur'):
                erreurs.append({'id': s['id'], 'qui': nom, 'erreur': 'chaîne : %s' % str(r['erreur'])[:200]})
            lignes.append(conseil_mod.mesurer_seul(r, conseil_mod.situation_fenetre(s)) if r and not r.get('erreur') else None)
        seuls[nom] = lignes
    lignes_conseil = []
    for s in situations:
        r = conseil_par_id.get(s['id'])
        if r and r.get('erreur'):
            erreurs.append({'id': s['id'], 'qui': 'conseil', 'erreur': str(r['erreur'])[:200]})
        lignes_conseil.append(conseil_mod.mesurer_conseil(r, conseil_mod.situation_fenetre(s)) if r and not r.get('erreur') else None)
    meta = {'date': datetime.now(ZoneInfo('Europe/Paris')).strftime('%d/%m/%Y %H:%M'), 'n': len(situations), 'chemin': brut.get('arbre', a.apres),
            'version': brut.get('version', ''), 'membres': membres, 'president': president, 'graine': a.graine, 'pause_ms': a.pause_ms,
            'familles': sorted({s.get('famille', '') for s in situations}),
            # Portée de l'hôte seulement : l'adresse du serveur n'est jamais recopiée dans le rapport.
            'hote': 'réseau privé' if prive else 'local', 'erreurs': erreurs, 'conseil': True}
    rap = conseil_mod.construire(situations, seuls, lignes_conseil, meta)
    texte = conseil_mod.markdown(rap)
    if a.sortie:
        dossier = Path(a.sortie)
        dossier.mkdir(parents=True, exist_ok=True)
        complet = dict(rap, mesures_seuls=seuls, mesures_conseil=lignes_conseil,
                       traces=[{k: r.get(k) for k in ('id', 'texte', 'mode', 'membres', 'president', 'reponses', 'critiques', 'desaccords', 'etapes', 'compteurs',
                                                      'appels', 'duree_s', 'limites', 'refs_marquees')} for r in brut.get('conseil') or []])
        (dossier / 'rapport-conseil.json').write_text(json.dumps(complet, ensure_ascii=False, indent=1), encoding='utf-8')
        (dossier / 'rapport-conseil.md').write_text(texte, encoding='utf-8')
        journal('Rapport écrit : %s' % dossier)
    else:
        journal(texte)
    return rap


def principal(argv=None, journal=print, ouvrir=None, faux_chaine=None, faux_conseil=None):
    """``ouvrir`` : transport HTTP de remplacement (tests : aucun appel réseau). ``faux_chaine`` : réponses scriptées du client du mode chaîne,
    passées au sous-processus (tests : aucun appel réseau). ``faux_conseil`` : {modèle: réponses scriptées} du mode conseil (tests)."""
    a = analyser(argv)
    if a.conseil:   # 5.6.108 (lot K) : mode conseil, indépendant des autres modes (inchangés)
        return principal_conseil(a, journal, ouvrir, faux_conseil)
    if a.chaine and not a.modele:
        raise ollama_local.Refus('mode chaîne : --modele ollama:<nom> est requis')
    if a.barriere and not a.chaine:
        raise ollama_local.Refus('--barriere ne vaut qu’en mode chaîne (--chaine)')
    arbres = [('avant', a.avant), ('apres', a.apres)] if a.avant else [('apres', a.apres)]
    # Contrôles avant tout travail : hôte admis, modèles non distants, sortie hors des arbres mesurés. L'hôte vient de la seule ligne de
    # commande : OLLAMA_HOST et les variables de proxy ne sont jamais lus.
    if a.pause_ms < 0:
        raise ollama_local.Refus('pause refusée : --pause-ms doit être positif ou nul')
    prive = bool(a.hote_reseau_prive)
    if prive and a.hote != ollama_local.HOTE_DEFAUT:
        raise ollama_local.Refus('options incompatibles : --hote et --hote-reseau-prive')
    hote = a.hote_reseau_prive if prive else a.hote
    cadence = ollama_local.Cadence(a.pause_ms)
    clients = []
    for cible in a.modele:
        nom = ollama_local.analyser_cible(cible)
        if nom not in [c.modele for c in clients]:
            clients.append(ollama_local.Ollama(nom, hote, ouvrir=ouvrir, reseau_prive=prive, cadence=cadence))
    juge = ollama_local.Ollama(ollama_local.analyser_cible(a.juge), hote, ouvrir=ouvrir, reseau_prive=prive, cadence=cadence) if a.juge else None
    if a.sortie and any(dans(a.sortie, chemin) for _, chemin in arbres):
        raise ollama_local.Refus('sortie refusée : le rapport ne s’écrit jamais dans un arbre mesuré (%s)' % a.sortie)
    toutes = situations_mod.charger(a.situations)
    situations = choisir(toutes, a.ids, a.famille, a.max)
    if not situations:
        raise SystemExit('Aucune situation retenue.')
    statiques, chemins, versions, erreurs = {}, {}, {}, []
    reponses = {c.modele: {} for c in clients}    # {modèle: {arbre: [mesures alignées sur les situations]}}
    juges = {c.modele: {} for c in clients}
    for etiquette, chemin in arbres:
        journal('Arbre %s : %s (%d situations)…' % (etiquette, chemin, len(situations)))
        brut = lancer_arbre(chemin, situations, a.python)
        chemins[etiquette], versions[etiquette] = brut.get('arbre', chemin), brut.get('version', '')
        erreurs += [dict(e, arbre=etiquette) for e in brut.get('erreurs') or []]
        statiques[etiquette], _ = mesurer_statique(brut, situations, a.textes)
        for client in clients:
            journal(' modèle %s…' % client.modele)
            if a.chaine:   # 5.6.108 (lot R) : chaîne du produit si l'arbre l'expose, sinon rejeu simple (ci-dessous)
                conf = {'modele': client.modele, 'hote': hote, 'reseau_prive': prive, 'pause_ms': a.pause_ms, 'graine': a.graine}
                if a.barriere and etiquette == 'apres':   # réglage du produit appliqué dans l'arbre « après » seulement
                    conf['barriere'] = a.barriere
                if faux_chaine is not None:
                    conf['faux'] = dict(faux_chaine, modele=client.modele)
                brut_c = lancer_chaine(chemin, situations, conf, a.python)
                if brut_c.get('disponible'):
                    journal('  chaîne du produit (%d situations)' % len(situations))
                    reponses[client.modele][etiquette], juges[client.modele][etiquette], err = mesurer_chaine(brut_c, situations, juge, a.graine)
                    erreurs += [dict(e, arbre=etiquette, modele=client.modele) for e in (brut_c.get('erreurs') or []) + err]
                    continue
                journal('  arbre sans chaîne : rejeu simple')
            erreurs_modele = []
            reponses[client.modele][etiquette], juges[client.modele][etiquette] = mesurer_modele(client, brut, situations, a.graine, juge, journal,
                                                                                                 erreurs_modele)
            erreurs += [dict(e, arbre=etiquette) for e in erreurs_modele]
    meta = {'date': datetime.now(ZoneInfo('Europe/Paris')).strftime('%d/%m/%Y %H:%M'), 'n': len(situations), 'chemins': chemins, 'versions': versions,
            'modeles': [c.modele for c in clients], 'juge': juge.modele if juge else '', 'graine': a.graine, 'pause_ms': a.pause_ms,
            # Portée de l'hôte seulement : l'adresse du serveur n'est jamais recopiée dans le rapport.
            'hote': ('réseau privé' if prive else 'local') if clients or juge else '', 'erreurs': erreurs, 'chaine': bool(a.chaine), 'barriere': a.barriere or ''}
    etiquettes = [e for e, _ in arbres]
    rap = rapport_mod.construire(etiquettes, situations, statiques, reponses, juges, meta)
    texte = rapport_mod.markdown(rap)
    if a.sortie:
        dossier = Path(a.sortie)
        dossier.mkdir(parents=True, exist_ok=True)
        complet = dict(rap, mesures_statiques=statiques, reponses=reponses, juges=juges)
        (dossier / 'rapport.json').write_text(json.dumps(complet, ensure_ascii=False, indent=1), encoding='utf-8')
        (dossier / 'rapport.md').write_text(texte, encoding='utf-8')
        journal('Rapport écrit : %s' % dossier)
    else:
        journal(texte)
    return rap
