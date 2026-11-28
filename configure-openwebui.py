#!/usr/bin/env python3
"""5.6.112 : régler automatiquement l'Open WebUI du cabinet d'après AxiorHub Pilote (``sudo axiorhub-mail openwebui``).

À lancer sur le serveur AxiorHub, en root (seul root lit le jeton de l'outil), après ``install-interface.py``. Demande une clé d'API d'un
administrateur d'Open WebUI (Open WebUI › Réglages › Compte › Clés d'API) : elle sert à ce seul réglage, n'est ni enregistrée ni affichée.
Ce qui est fait, étape par étape (détail dans ``agent/openwebui_reglage56112.py``) : outil AxiorHub et son jeton, connexions IA
(une clé de fournisseur n'est transmise qu'après accord), connecteurs MCP, compétences approuvées, modèle « AxiorHub Avocat » avec
l'invite système, invites. Relancer met à jour sans doublon. Enfin, l'adresse d'Open WebUI est enregistrée dans AxiorHub (page
« Conversations IA ») et le réglage d'affichage dans AxiorHub à poser dans Open WebUI est rappelé.

Options : ``--url URL`` (adresse d'Open WebUI), ``--oui`` (sans question, aucune clé transmise), ``--transmettre-cles`` (avec ``--oui`` :
clés des fournisseurs et jetons MCP transmis), ``--ollama URL`` (adresse d'Ollama vue depuis Open WebUI), ``--etapes a,b``.
"""
import argparse
import getpass
import json
import os
from pathlib import Path
import sqlite3
import subprocess
import sys

BASE = Path('/opt/axiorhub-mail-agent/current')
AUTH = Path('/etc/axiorhub-mail-agent/ui-auth.json')
TOKEN = Path('/etc/axiorhub-mail-agent/openwebui-api-token')
CONFIG = Path('/etc/axiorhub-mail-agent/config.json')
ETAPES = ('administration', 'permissions', 'outil', 'connexions', 'mcp', 'competences', 'parametres', 'modele', 'invites', 'voix')
SYMBOLES = {'fait': '✓', 'a_jour': '=', 'saute': '–', 'echec': '✗'}


def _reglages(state_dir):
    """Réglages d'exécution d'AxiorHub (fournisseurs IA, routage, extensions, raccourcis), base ouverte en lecture seule."""
    path = Path(state_dir) / 'desk.sqlite3'
    out = {}
    if not path.is_file():
        return out
    db = sqlite3.connect(path.resolve().as_uri() + '?mode=ro', uri=True, timeout=5)
    try:
        for key, value in db.execute("SELECT key,value FROM settings WHERE key LIKE 'ai:provider:%' OR key LIKE 'ai:route:%' "
                                     "OR key LIKE 'lawve:item:%' OR key='workstation:url:openwebui'"):
            try:
                out[key] = json.loads(value)
            except (ValueError, TypeError):
                continue
    finally:
        db.close()
    return out


def _secret(path):
    from agent.common import read_secret
    from agent.common import Stop
    if not path:
        return ''
    try:
        return read_secret(path)
    except (Stop, OSError):
        return ''


def config_fusionnee(config, reglages):
    """Configuration d'AxiorHub avec ses réglages d'exécution (fournisseurs, routes, extensions), comme le service la voit."""
    cfg = json.loads(json.dumps(config))
    for key, value in reglages.items():
        if not isinstance(value, dict):
            continue
        if key.startswith('ai:provider:'):
            cfg.setdefault('ai_providers', {})[key.split(':', 2)[-1]] = value
        elif key.startswith('ai:route:'):
            cfg.setdefault('model_routing', {})[key.split(':', 2)[-1]] = value
        elif key.startswith('lawve:item:'):
            cfg.setdefault('lawve_extensions', {})[key.split(':', 2)[-1]] = value
    return cfg


def entrees(config, auth, jeton, reglages, base_code=None):
    """Données à transmettre, sans aucune clé (elles ne sont lues qu'après accord)."""
    from agent import openwebui_reglage56112 as R
    base_code = base_code or BASE
    cfg = config_fusionnee(config, reglages)
    route = (cfg.get('model_routing') or {}).get('assistant') or {}
    connecteurs, ecartes = R.connecteurs_mcp(cfg.get('lawve_extensions') or {})
    try:
        from agent.dictee56110 import reglages_de
        dictee = reglages_de(cfg.get('state_dir'))
    except Exception:   # noqa: BLE001 — réglage illisible : dictée « équilibrée » par défaut
        dictee = {}
    return {'source': (base_code / 'integrations/openwebui/axiorhub_tool.py').read_text(encoding='utf-8'),
            'systeme': (base_code / 'integrations/openwebui/SYSTEM-PROMPT-AXIORHUB.md').read_text(encoding='utf-8'),
            'api_url': auth['origin'] + auth.get('prefix', '/agent-courriel') + '/api/v1', 'jeton': jeton, 'config': cfg, 'dictee': dictee,
            'fournisseurs': R.fournisseurs_compatibles(R.registre_fournisseurs(cfg)), 'connecteurs': connecteurs, 'ecartes_mcp': ecartes,
            'competences': R.competences_axiorhub(cfg.get('lawve_extensions') or {}),
            'modele_prefere': str(route.get('model') or '') if isinstance(route, dict) else '',
            'ollama_local': str((cfg.get('ollama') or {}).get('url') or '')}


def ollama_vu_depuis_openwebui(url):
    """Une adresse de bouclage d'AxiorHub n'est pas joignable depuis le conteneur d'Open WebUI : host.docker.internal à la place."""
    from urllib.parse import urlsplit
    p = urlsplit(url or '')
    if not p.hostname:
        return ''
    if p.hostname in ('127.0.0.1', 'localhost', '::1'):
        return '%s://host.docker.internal:%d' % (p.scheme or 'http', p.port or 11434)
    return url.rstrip('/')


def _oui(question, entree, defaut=False):
    reponse = entree(question + (' [O/n] ' if defaut else ' [o/N] ')).strip().lower()
    return defaut if not reponse else reponse in ('o', 'oui', 'y', 'yes')


def cle_admin_gardee(state_dir):
    """Clé d'administrateur gardée par AxiorHub (mode --auto des mises à jour) ; root la lit dans le coffre du service."""
    return _secret(str(Path(state_dir) / 'secrets' / 'openwebui56112' / 'administrateur.secret'))


def garder(url, cle, accord, sortie=print, inscription_fermee=False):
    """Adresse d'Open WebUI et clé d'administrateur rangées par le service lui-même (son coffre, ses droits)."""
    gestion = ['/usr/sbin/runuser', '-u', 'axiorhub-mail', '--', '/usr/bin/python3', str(BASE / 'manage.py')]
    if url.startswith('https://'):
        subprocess.run(gestion + ['set-url', 'openwebui', url], check=False, capture_output=True)
        r = subprocess.run(gestion + ['openwebui-admin'], input=json.dumps({'cle': cle, 'accord_cles': bool(accord), 'inscription_fermee': bool(inscription_fermee)}), text=True,
                           capture_output=True, check=False)
        sortie('Open WebUI relié à AxiorHub : comptes créés pour chacun et réglages reportés automatiquement.' if r.returncode == 0 else
               'Clé d’administrateur non rangée par AxiorHub : relancez cette commande.')


def cle_par_connexion(url, courriel, mot, sortie=print):
    """Instance sans clé d'API à portée : connexion de l'administrateur (session), clés d'API activées si besoin, clé existante reprise
    ou créée. Le mot de passe sert à cette seule connexion."""
    from agent import openwebui_reglage56112 as R
    session = R.Client(url, 'session-provisoire-0000')
    statut, rep = session.appel('POST', '/api/v1/auths/signin', {'email': courriel, 'password': mot})
    if statut != 200 or not isinstance(rep, dict) or not rep.get('token'):
        raise RuntimeError('Connexion refusée par Open WebUI (courriel ou mot de passe).')
    if rep.get('role') != 'admin':
        raise RuntimeError('Ce compte n’est pas administrateur d’Open WebUI.')
    admin = R.Client(url, rep['token'])
    conf = admin.exiger('GET', '/api/v1/auths/admin/config') or {}
    if not conf.get('ENABLE_API_KEYS'):
        conf = dict(conf)
        conf['ENABLE_API_KEYS'] = True
        admin.exiger('POST', '/api/v1/auths/admin/config', conf)
        sortie('Clés d’API activées dans Open WebUI.')
    statut, existante = admin.appel('GET', '/api/v1/auths/api_key')
    if statut == 200 and isinstance(existante, dict) and existante.get('api_key'):
        return existante['api_key']          # clé déjà en usage ailleurs : reprise, jamais remplacée
    cree = admin.exiger('POST', '/api/v1/auths/api_key') or {}
    if not cree.get('api_key'):
        raise RuntimeError('Open WebUI n’a pas créé de clé d’API.')
    sortie('Clé d’API de l’administrateur créée (gardée chiffrée par AxiorHub).')
    return cree['api_key']


def main(argv=None, entree=input, motdepasse=getpass.getpass, sortie=print, racine=None, cle=None):
    p = argparse.ArgumentParser(description='Régler automatiquement Open WebUI d’après AxiorHub Pilote')
    p.add_argument('--url', default='')
    p.add_argument('--oui', action='store_true')
    p.add_argument('--auto', action='store_true', help='sans question, avec la clé gardée par AxiorHub (mises à jour)')
    p.add_argument('--transmettre-cles', action='store_true')
    p.add_argument('--ollama', default=None)
    p.add_argument('--etapes', default=','.join(ETAPES))
    args = p.parse_args(argv)
    if (os.geteuid() if racine is None else racine) != 0:
        raise RuntimeError('Exécuter avec sudo axiorhub-mail openwebui')
    from agent import openwebui_reglage56112 as R
    from agent.common import load_config
    config = load_config(str(CONFIG))
    auth = json.loads(AUTH.read_text(encoding='utf-8'))
    jeton = TOKEN.read_text(encoding='utf-8').strip()
    import hashlib
    if len(jeton) < 40 or hashlib.sha256(jeton.encode()).hexdigest() != auth.get('api_token_sha256'):
        raise RuntimeError('Le jeton de l’outil ne correspond pas à l’interface : relancer sudo python3 install-interface.py.')
    reglages = _reglages(config['state_dir'])
    e = entrees(config, auth, jeton, reglages)
    url = args.url or str(reglages.get('workstation:url:openwebui') or (config.get('interfaces') or {}).get('openwebui') or '')
    from urllib.parse import urlsplit
    if not url or (urlsplit(url).hostname or '').endswith(('example.com', 'example.org', 'example.net')):   # adresse d'exemple : non réglée
        url = '' if (args.oui or args.auto) else entree('Adresse d’Open WebUI (https://…) : ').strip()
    if args.auto:
        cle = cle or cle_admin_gardee(config['state_dir'])
        if not url or not cle:
            sortie('Open WebUI n’est pas relié à AxiorHub : rien à synchroniser.')
            return 0
        accord = bool((reglages.get('openwebui56112:accord') or {}).get('cles'))
    else:
        if not url:
            raise RuntimeError('Adresse d’Open WebUI requise (--url).')
        cle = cle or motdepasse('Clé d’API d’un administrateur d’Open WebUI (rien ne s’affiche ; laissez vide pour vous connecter avec '
                                'le courriel et le mot de passe de l’administrateur) : ').strip()
        if not cle:
            cle = cle_par_connexion(url, entree('Courriel de l’administrateur d’Open WebUI : ').strip(),
                                    motdepasse('Mot de passe (rien ne s’affiche, rien n’est enregistré) : '), sortie)
        accord = None
    client = R.Client(url, cle)
    R.verifier_admin(client)
    sortie('Open WebUI joint, clé d’administrateur acceptée.')
    # Clés (fournisseurs d'IA, connecteurs MCP, voix ElevenLabs) : jamais lues ni transmises sans accord, demandé une fois.
    avec_cle = [f for f in e['fournisseurs'] if f['secret_file']]
    jetons_mcp = [c for c in e['connecteurs'] if c['auth'] == 'bearer' and c['secret_file']]
    voix = e['config'].get('speech568') or {}
    voix_cle = str(voix.get('api_key_file') or '') if voix.get('provider') == 'elevenlabs' else ''
    noms = [f['id'] for f in avec_cle] + [c['nom'] for c in jetons_mcp] + (['voix ElevenLabs'] if voix_cle else [])
    if accord is None:
        accord = bool(noms) and (args.transmettre_cles if args.oui else _oui(
            'Transmettre à Open WebUI les clés de : ' + ', '.join(noms) + ' ? Elles y seront enregistrées (réglages administrateur) ; '
            'sans elles, ces connexions sont ajoutées désactivées.', entree))
    e['cles_fournisseurs'] = {f['id']: s for f in avec_cle if (s := _secret(f['secret_file']))} if accord else {}
    e['cles_mcp'] = {c['id']: s for c in jetons_mcp if (s := _secret(c['secret_file']))} if accord else {}
    if args.auto:
        e['fermer_inscription'] = bool((reglages.get('openwebui56112:accord') or {}).get('inscription_fermee'))
    else:
        ouverte = bool((client.exiger('GET', '/api/v1/auths/admin/config') or {}).get('ENABLE_SIGNUP'))
        e['fermer_inscription'] = ouverte and (args.oui or _oui('L’inscription publique d’Open WebUI est ouverte : la fermer ? AxiorHub crée '
                                                                  'désormais les comptes du cabinet.', entree, defaut=True))
    e['tts'], e['stt'], e['notes_voix'] = R.voix_axiorhub(e['config'], e['dictee'], 1.0, _secret(voix_cle) if (accord and voix_cle) else '')
    proposee = ollama_vu_depuis_openwebui(e['ollama_local'])
    if args.ollama is not None:
        e['ollama_url'] = args.ollama.strip()
    elif proposee and not (args.oui or args.auto):
        e['ollama_url'] = entree('Adresse d’Ollama vue depuis Open WebUI [%s] (« - » pour ne pas l’ajouter) : ' % proposee).strip() or proposee
        if e['ollama_url'] == '-':
            e['ollama_url'] = ''
    else:
        e['ollama_url'] = proposee
    etapes = tuple(x for x in args.etapes.split(',') if x in ETAPES)
    if not (args.oui or args.auto) and not _oui('Appliquer ces réglages à Open WebUI (%s) ?' % ', '.join(etapes), entree, defaut=True):
        sortie('Rien n’a été modifié.')
        return 1
    resultats = R.regler(client, e, etapes, journal=lambda r: sortie('%s %s : %s' % (SYMBOLES.get(r['etat'], '?'), r['etape'], r['detail'])))
    if not args.auto:
        garder(url, cle, accord, sortie, bool(e.get('fermer_inscription')))
    sortie('Affichage dans AxiorHub : la configuration d’Open WebUI doit contenir CONTENT_SECURITY_POLICY="frame-ancestors \'self\' %s" '
           '(déjà posé si Open WebUI a été installé par install-openwebui.py).' % auth['origin'])
    return 0 if all(r['etat'] != 'echec' for r in resultats) else 2


if __name__ == '__main__':
    try:
        sys.exit(main())
    except RuntimeError as ex:
        sys.exit(str(ex))
    except Exception as ex:   # noqa: BLE001 — erreur d'Open WebUI rendue lisible, jamais une trace avec des valeurs
        from agent.openwebui_reglage56112 import ErreurOpenWebUI
        if isinstance(ex, ErreurOpenWebUI):
            sys.exit({'cle_admin_refusee': 'Open WebUI refuse cette clé : il faut la clé d’API d’un administrateur.',
                      'openwebui_injoignable': 'Open WebUI ne répond pas à cette adresse.',
                      'adresse_openwebui_invalide': 'Adresse d’Open WebUI invalide (https://…).',
                      'cle_admin_invalide': 'Clé d’API mal formée.'}.get(str(ex), 'Open WebUI a refusé : %s' % ex))
        raise
