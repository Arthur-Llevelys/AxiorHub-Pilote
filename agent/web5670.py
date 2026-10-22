"""5.6.70 : routes ``/api440/m5670/...``.

Lectures (GET, tous les rôles ; contrôles métier dans les modules) :
- ``efacture/fichier?id=`` : fichier d'une facture reçue (droit « facturation ») ;
- ``efacture/export?du=&au=&pdf=1`` : ZIP pour le comptable (droit « facturation ») ;
- ``fichiers/parcourir?chemin=`` et ``fichiers/chercher?q=`` : documents Nextcloud à joindre à un courriel.
Écritures (POST) :
- ``courriels/traiter`` : « Traiter avec l'IA » les courriels cochés (droit « déposer », comme « Répondre avec l'IA ») ;
- ``efacture/actualiser``, ``efacture/enregistrer``, ``efacture/dossier`` : facturation électronique (avocat, administrateur, droit « facturation ») ;
- ``visio/lien`` : lien d'une nouvelle salle (tous) ; ``visio/reglage``, ``visio/tester`` : administrateur ;
- ``bandeau/masquer`` : masquer le bandeau du plafond de l'IA jusqu'au prochain changement d'état (tous) ;
- ``securite/lever|bloquer|confiance|retirer`` : blocages des accès suspects (administrateur).
"""
from .common import Stop

WORK = ('courriels/traiter', 'visio/lien', 'bandeau/masquer')
LAWYER = ('efacture/actualiser', 'efacture/enregistrer', 'efacture/dossier')
ADMIN = ('visio/reglage', 'visio/tester', 'securite/lever', 'securite/bloquer', 'securite/confiance', 'securite/retirer')


def allowed(role, route):
    route = route.split('?', 1)[0]
    if route in WORK:
        return role in ('administrateur', 'avocat', 'assistant')
    if route in LAWYER:
        return role in ('administrateur', 'avocat')
    return role == 'administrateur' and route in ADMIN


def api(env, desk, auth, prefix, name, args, method):
    from .web440 import json_out
    from .web567 import actor
    owner, role = actor(env)
    route = name[len('m5670/'):].split('?', 1)[0]
    if method == 'GET':
        if route == 'efacture/fichier':
            from .efacture5670 import download
            return json_out(download(desk, owner, role, str(args.get('id') or '')))
        if route == 'efacture/export':
            from .efacture5670 import export
            return json_out(export(desk, owner, role, str(args.get('du') or ''), str(args.get('au') or ''), str(args.get('pdf') or '') in ('1', 'oui', 'true')))
        if route == 'fichiers/parcourir':
            from .fichiers5670 import browse
            return json_out(browse(desk, str(args.get('chemin') or '')))
        if route == 'fichiers/chercher':
            from .fichiers5670 import search
            return json_out(search(desk, str(args.get('q') or '')))
        raise Stop('route_inconnue')
    if not allowed(role, route):
        raise Stop('role_insuffisant')
    if method != 'POST':
        raise Stop('methode_refusee')
    from .web5634 import _post
    data = _post(env, auth, 20_000)
    if route == 'courriels/traiter':
        from .traitement5670 import traiter
        return json_out(traiter(desk, owner, data.get('items') or [], str(data.get('instruction') or '')[:4000]))
    if route == 'efacture/actualiser':
        from .efacture5670 import sync
        from .facturation5653 import _require
        _require(desk, owner, role, 'facturation')
        out = sync(desk, owner)
        msg = '%d facture(s) reçue(s), %d événement(s) de suivi, %d compte(s) rendu(s) relus.' % (out['recues'], out['suivis'], out['rapports'])
        if out['erreurs']:
            msg += ' Incidents : ' + ' ; '.join(out['erreurs'][:4])
        return json_out({'ok': True, 'message': msg, **out})
    if route == 'efacture/enregistrer':
        from .efacture5670 import store
        return json_out(store(desk, owner, role, str(data.get('id') or '')))
    if route == 'efacture/dossier':
        from .efacture5670 import save_folder
        return json_out(save_folder(desk, owner, role, str(data.get('chemin') or '')))
    if route == 'visio/lien':
        from .visio5670 import room
        out = room(desk)
        return json_out({'ok': True, **out, 'message': 'Lien de visioconférence : ' + out['url']})
    if route == 'visio/reglage':
        from .visio5670 import save
        return json_out(save(desk, owner, str(data.get('url') or ''), str(data.get('enabled') or '') in ('1', 'true', 'on', 'oui') or data.get('enabled') is True))
    if route == 'visio/tester':
        from .visio5670 import check
        return json_out(check(desk))
    if route.startswith('securite/'):
        from .garde5670 import open_garde
        import ipaddress
        ip = str(data.get('ip') or '').strip()[:64]
        try:
            ipaddress.ip_address(ip)
        except ValueError:
            raise Stop('adresse_ip_invalide') from None
        g = open_garde(desk.c['state_dir'])
        try:
            if route == 'securite/lever':
                g.lift(ip, owner)
                msg = 'Blocage levé pour %s.' % ip
            elif route == 'securite/bloquer':
                g.ban(ip, 'manuel', owner, seconds=86400)
                msg = '%s bloquée 24 heures.' % ip
            elif route == 'securite/confiance':
                g.allow(ip, str(data.get('note') or ''), owner)
                msg = '%s ajoutée aux adresses de confiance (jamais bloquée).' % ip
            else:
                g.disallow(ip)
                msg = '%s retirée des adresses de confiance.' % ip
        finally:
            g.db.close()
        desk.audit('securite5670_' + route.split('/', 1)[1], {'ip': ip, 'par': str(owner)[:80]})
        return json_out({'ok': True, 'message': msg})
    if route == 'bandeau/masquer':
        from .budget5668 import dismiss
        return json_out(dismiss(desk, owner))
    raise Stop('route_inconnue')
