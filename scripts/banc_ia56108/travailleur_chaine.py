"""5.6.108 (lot R) : travailleur du mode « chaîne » du banc, lancé dans un sous-processus dont ``PYTHONPATH`` désigne l'arbre mesuré.

Si l'arbre expose ``agent.references56108.produire`` (5.6.108 et suivantes), chaque situation passe par la chaîne du produit (recherche
d'office, besoins, rédaction, barrière, reprise) avec le transport du modèle du banc (``ollama_local``, mêmes gardes) et des connecteurs factices
(corpus fictif de la situation). Sinon, la sortie l'indique (``disponible`` faux) et le banc garde le rejeu simple (arbre 5.6.107).

Rien n'est écrit dans l'arbre mesuré : ``PYTHONDONTWRITEBYTECODE`` est posé par le lanceur, la base vit dans un dossier temporaire.
Usage (interne) : ``python travailleur_chaine.py <entrée.json> <sortie.json>``.
"""
import json
import os
from pathlib import Path
import sys
import tempfile
import traceback

ICI = Path(__file__).resolve().parent
sys.path.append(str(ICI.parent))   # paquet ``banc_ia56108`` (après l'arbre mesuré, qui reste prioritaire pour ``agent``)


def client_de(conf):
    """Client du modèle : Ollama local, ou du réseau privé (option explicite, validée par le lanceur et revalidée ici) ; faux client scripté
    pour les essais (aucun réseau)."""
    from banc_ia56108 import chaine, ollama_local
    if conf.get('faux'):
        return chaine.ModeleScripte(**conf['faux'])
    return ollama_local.Ollama(ollama_local.verifier_modele(conf['modele']), conf['hote'], reseau_prive=bool(conf.get('reseau_prive')),
                               cadence=ollama_local.Cadence(int(conf.get('pause_ms') or 0)))


def principal(entree, sortie):
    demande = json.loads(Path(entree).read_text(encoding='utf-8'))
    conf = demande.get('chaine') or {}
    try:
        from agent import references56108   # noqa: F401 — arbre qui n'expose pas la chaîne : rejeu simple côté banc
        disponible = hasattr(references56108, 'produire')
    except ImportError:
        disponible = False
    resultats, erreurs = [], []
    if disponible:
        from banc_ia56108 import chaine, ollama_local, travailleur as T
        client = client_de(conf)
        with tempfile.TemporaryDirectory(prefix='banc-ia-R-') as tmp:
            from agent.desk import Desk
            desk = Desk(T.config(Path(tmp), demande['situations']))
            try:
                from agent import competences5632 as c32
                c32.ensure_builtin(desk)
                if conf.get('barriere') and hasattr(references56108, 'SETTING_BARRIERE'):   # --barriere : réglage du produit dans cet arbre
                    desk.setting(references56108.SETTING_BARRIERE, str(conf['barriere']))
                deja = set()
                for numero, situation in enumerate(demande['situations'], start=2001):
                    T.semer_dossier(desk, situation, deja)
                    try:
                        out = chaine.executer(desk, situation, client, int(conf.get('graine') or 42), numero, T.max_tokens(situation['place']))
                        resultats.append(T._jsonable(out))
                    except ollama_local.Refus:
                        raise
                    except Exception as erreur:   # noqa: BLE001 — une situation en erreur n'arrête pas le banc ; l'adresse n'est jamais recopiée
                        texte = client.masquer(erreur) if hasattr(client, 'masquer') else str(erreur)
                        erreurs.append({'id': situation.get('id'), 'erreur': 'chaîne : %s: %s' % (type(erreur).__name__, texte)[:300]})
            finally:
                desk.db.close()
                try:
                    import gc
                    gc.collect()
                except Exception:   # noqa: BLE001
                    pass
    import agent
    Path(sortie).write_text(json.dumps({'arbre': str(Path(agent.__file__).resolve().parents[1]), 'version': getattr(agent, '__version__', ''),
                                        'disponible': disponible, 'resultats': resultats, 'erreurs': erreurs}, ensure_ascii=False), encoding='utf-8')


if __name__ == '__main__':
    os.environ.setdefault('PYTHONDONTWRITEBYTECODE', '1')
    sys.dont_write_bytecode = True
    try:
        principal(sys.argv[1], sys.argv[2])
    except Exception:   # noqa: BLE001
        traceback.print_exc()
        sys.exit(2)
