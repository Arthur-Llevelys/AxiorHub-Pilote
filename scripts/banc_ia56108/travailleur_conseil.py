"""5.6.108 (lot K) : travailleur du mode « conseil » du banc, lancé dans un sous-processus dont ``PYTHONPATH`` désigne l'arbre mesuré.

Si l'arbre expose ``agent.conseil56108.deliberer`` (5.6.108 et suivantes), chaque situation passe par chaque membre seul (chaîne du lot R,
``agent.references56108.produire``) puis par le conseil du produit, avec le transport du modèle du banc (``ollama_local``, mêmes gardes) et des
connecteurs factices (corpus fictif). Sinon, la sortie l'indique (``disponible`` faux).

Rien n'est écrit dans l'arbre mesuré : ``PYTHONDONTWRITEBYTECODE`` est posé par le lanceur, la base vit dans un dossier temporaire.
Usage (interne) : ``python travailleur_conseil.py <entrée.json> <sortie.json>``.
"""
import json
import os
from pathlib import Path
import sys
import tempfile
import traceback

ICI = Path(__file__).resolve().parent
sys.path.append(str(ICI.parent))   # paquet ``banc_ia56108`` (après l'arbre mesuré, qui reste prioritaire pour ``agent``)


def clients_de(conf):
    """Clients des membres et du président : Ollama local ou du réseau privé (option explicite, revalidée ici) ; faux clients scriptés pour les
    essais (aucun réseau)."""
    from banc_ia56108 import conseil, ollama_local
    noms = list(conf['membres']) + [conf['president']]
    out = {}
    cadence = ollama_local.Cadence(int(conf.get('pause_ms') or 0))
    for nom in noms:
        if nom in out:
            continue
        if conf.get('faux'):
            out[nom] = conseil.ModeleScripteConseil(**dict((conf['faux'].get(nom) or {}), modele=nom))
        else:
            out[nom] = ollama_local.Ollama(ollama_local.verifier_modele(nom), conf['hote'], reseau_prive=bool(conf.get('reseau_prive')), cadence=cadence)
    return out


def principal(entree, sortie):
    demande = json.loads(Path(entree).read_text(encoding='utf-8'))
    conf = demande.get('conseil') or {}
    try:
        from agent import conseil56108   # noqa: F401
        disponible = hasattr(conseil56108, 'deliberer')
    except ImportError:
        disponible = False
    seuls, conseils, erreurs = {}, [], []
    if disponible:
        from banc_ia56108 import chaine, conseil, ollama_local, travailleur as T
        clients = clients_de(conf)
        membres = [clients[n] for n in conf['membres']]
        president = clients[conf['president']]
        graine = int(conf.get('graine') or 42)
        with tempfile.TemporaryDirectory(prefix='banc-ia-K-') as tmp:
            from agent.desk import Desk
            desk = Desk(T.config(Path(tmp), demande['situations']))
            try:
                from agent import competences5632 as c32
                c32.ensure_builtin(desk)
                deja = set()
                numero = 3000
                for situation in demande['situations']:
                    T.semer_dossier(desk, situation, deja)
                    s = conseil.situation_fenetre(situation)
                    mt = T.max_tokens(s['place'])
                    for client in membres:
                        numero += 1
                        try:
                            out = chaine.executer(desk, s, client, graine, numero, mt)
                            seuls.setdefault(client.modele, []).append(T._jsonable(out))
                        except ollama_local.Refus:
                            raise
                        except Exception as erreur:   # noqa: BLE001 — l'adresse n'est jamais recopiée
                            texte = client.masquer(erreur) if hasattr(client, 'masquer') else str(erreur)
                            erreurs.append({'id': situation.get('id'), 'qui': client.modele, 'erreur': ('%s: %s' % (type(erreur).__name__, texte))[:300]})
                    numero += 1
                    try:
                        out = conseil.executer(desk, s, membres, president, graine, numero, mt)
                        conseils.append(T._jsonable(out))
                    except ollama_local.Refus:
                        raise
                    except Exception as erreur:   # noqa: BLE001
                        texte = president.masquer(erreur) if hasattr(president, 'masquer') else str(erreur)
                        erreurs.append({'id': situation.get('id'), 'qui': 'conseil', 'erreur': ('%s: %s' % (type(erreur).__name__, texte))[:300]})
            finally:
                desk.db.close()
                try:
                    import gc
                    gc.collect()
                except Exception:   # noqa: BLE001
                    pass
    import agent
    Path(sortie).write_text(json.dumps({'arbre': str(Path(agent.__file__).resolve().parents[1]), 'version': getattr(agent, '__version__', ''),
                                        'disponible': disponible, 'seuls': seuls, 'conseil': conseils, 'erreurs': erreurs}, ensure_ascii=False), encoding='utf-8')


if __name__ == '__main__':
    os.environ.setdefault('PYTHONDONTWRITEBYTECODE', '1')
    sys.dont_write_bytecode = True
    try:
        principal(sys.argv[1], sys.argv[2])
    except Exception:   # noqa: BLE001
        traceback.print_exc()
        sys.exit(2)
