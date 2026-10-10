"""Assistant d'installation (AxiorHub 5.6.0) — distribution autonome (Docker / VPS), réservé à l'administrateur du cabinet.

Une seule page, six parties : cabinet, messagerie, Nextcloud et agenda, IA, fonctionnement, fin. Les mots de passe et clés sont écrits
dans des fichiers privés (0600) du dossier des secrets, jamais dans la configuration ni dans le journal. Les tests de connexion sont
lancés à la demande. Tant que l'installation n'est pas terminée, les autres membres voient « Installation en cours ».
"""
from datetime import datetime, timezone
from html import escape
import hmac
import json
import os
from pathlib import Path
import re

PROVIDERS = {'anthropic': ('Anthropic (Claude)', 'claude-sonnet-5-5'), 'openai': ('OpenAI', 'gpt-5'), 'mistral': ('Mistral AI (hébergement européen)', 'mistral-large-latest')}


def _cfg(auth):
    return json.loads(Path(auth.app.config_path).read_text(encoding='utf-8'))


def _write_cfg(auth, cfg):
    from .config567 import _path
    from .common import Stop
    path = _path({'axiorhub.config_path':auth.app.config_path})
    tmp = path.with_suffix('.tmp')
    try:
        tmp.write_text(json.dumps(cfg, ensure_ascii=False, indent=2) + '\n', encoding='utf-8')
        try:
            os.chmod(tmp, 0o600)
        except OSError:
            pass
        os.replace(tmp, path)
    except PermissionError:
        raise Stop('configuration_non_inscriptible') from None
    except OSError as error:   # 5.6.15 : /etc en lecture seule pour le service (lien de configuration remplacé)
        if error.errno in (30, 13, 1):raise Stop('configuration_non_inscriptible') from None
        raise


def _secrets_dir(cfg):
    current = (cfg.get('mail') or {}).get('password_file') or ''
    base = Path(current).parent if current else Path(cfg.get('state_dir', '/data/state')).parent / 'secrets'
    base.mkdir(parents=True, exist_ok=True, mode=0o700)
    try:
        os.chmod(base, 0o700)                 # 5.6.3 : dossier des secrets réservé au service
    except OSError:
        pass
    return base


def _secret(cfg, name, value):
    from .vault567 import write
    return write(_secrets_dir(cfg)/(name+'.secret'),value)


def _clean(value, maximum=200):
    return re.sub(r'\s+', ' ', str(value or '')).strip()[:maximum]


def _desk(auth):
    from .common import load_config
    from .desk import Desk
    return Desk(load_config(auth.app.config_path))


def save(auth, form):
    """Le formulaire initial utilise les mêmes validations que les connexions."""
    from .config567 import save as save_connections
    cfg = _cfg(auth)
    pairs = {'imap_host':'mail.host','imap_port':'mail.port','imap_user':'mail.username',
             'imap_drafts':'mail.drafts','imap_sent':'mail.sent','from_address':'mail.from_address',
             'from_name':'mail.from_name','imap_password':'mail.password_file',
             'nc_url':'nextcloud.url','nc_user':'nextcloud.username','nc_password':'nextcloud.password_file','local_path':'nextcloud.local_path',
             'ollama_url':'ollama.url','ollama_model':'ollama.model'}
    values = {key: form[field] for field,key in pairs.items() if form.get(field)}
    if form.get('nc_root'):
        root = '/' + str(form['nc_root']).strip('/')
        values.update({'nextcloud.roots':[root], 'nextcloud.matter_roots':[root]})
    notes = []
    desk = None
    try:
        desk = _desk(auth)
        if values:
            save_connections(desk, {'revision':cfg.get('config_revision567',0), 'values':values},
                             {'axiorhub.config_path':str(auth.app.config_path)})
    except Exception as ex:
        from .common import Stop
        message = str(ex) if isinstance(ex, Stop) else 'configuration_non_validee'
        return ['Réglages non enregistrés : ' + message.replace('_',' ') + '.']
    finally:
        if desk is not None: desk.db.close()
    cfg = _cfg(auth)
    cfg['mode'] = 'drafts' if form.get('mode') == 'drafts' else 'observe'
    ai = form.get('ai', 'local')
    cfg.setdefault('installation', {})['ai_choice'] = ai if ai in ('local','mixte','externe') else 'local'
    _write_cfg(auth, cfg)
    desk = _desk(auth)
    try:
        return _save_app(desk, cfg, form, ai, notes)
    finally:
        desk.db.close()


CAPABILITIES = {'imap': ('mail',), 'nextcloud': ('nextcloud',), 'agenda': ('nextcloud', 'calendar'), 'local': ('nextcloud.local_path', 'nextcloud.roots'), 'ia': ('ollama',)}


def _connection_fingerprint(cfg, what=None):
    """5.6.24 (F20) : empreinte des seuls réglages de la capacité testée ; modifier l'IA ne périme pas le test des dossiers."""
    import hashlib
    from .common import read_secret
    values = {}
    for name in (CAPABILITIES.get(what) or ('mail','nextcloud','ollama')):
        if '.' in name:
            section, key = name.split('.', 1)
            values[name] = (cfg.get(section) or {}).get(key)
            continue
        part = dict(cfg.get(name) or {})
        for key,value in list(part.items()):
            if key.endswith('_file'):
                try: part[key] = hashlib.sha256(read_secret(value).encode()).hexdigest()
                except Exception: part[key] = 'non-lisible'
        values[name] = part
    return hashlib.sha256(json.dumps(values,sort_keys=True).encode()).hexdigest()


def _receipt(auth, what, ok):
    cfg = _cfg(auth)
    cfg.setdefault('installation',{}).setdefault('connection_tests567',{})[what] = {
        'ok':bool(ok), 'fingerprint':_connection_fingerprint(cfg, what),
        'at':datetime.now(timezone.utc).isoformat()}
    _write_cfg(auth,cfg)


def _save_app(desk, cfg, form, ai, notes):
    from .pieces510 import save_profile
    save_profile(desk, {'prenom_avocat': form.get('prenom', ''), 'nom_avocat': form.get('nom', ''), 'ville_avocat': form.get('barreau', ''),
                        'numero_toque_avocat': form.get('toque', ''), 'adresse_cabinet_avocat': form.get('adresse', ''),
                        'numero_telephone_avocat': form.get('telephone', ''), 'email_avocat': form.get('email', '')})
    desk.setting('cabinet560:specialite', _clean(form.get('specialite', ''), 80))
    if ai in ('mixte', 'externe') and form.get('provider') in PROVIDERS:
        pid = form['provider']
        model = _clean(form.get('provider_model') or PROVIDERS[pid][1], 160)
        if form.get('consent') != 'yes':
            notes.append('IA externe : cochez l’autorisation d’envoi (données pseudonymisées) pour l’activer.')
        else:
            from .ai_gateway import save_provider
            from . import ia540
            try:
                save_provider(desk, pid, pid, '', model, form.get('provider_key', ''), True, True)
                if ai == 'mixte':
                    ia540.apply_mixed(desk, pid, model, (cfg.get('ollama') or {}).get('model', ''), test=False)
                else:
                    ia540.apply_external_all(desk, pid, model)
            except Exception as ex:
                notes.append('IA externe non activée : %s' % str(ex)[:100])
    elif ai == 'local':
        from . import ia540
        ia540.set_local(desk)
    desk.audit('installation560_enregistree', {'ai': ai, 'mode': cfg['mode']})
    return notes


def test(auth, what):
    desk = None
    try:
        desk = _desk(auth)
        if what == 'imap':
            from .web520 import check_imap
            message = check_imap(desk)['message']
            _receipt(auth, what, True)
            return True, message
        if what == 'local':   # 5.6.24 (F20) : dossier local seul — lecture et création vérifiées, aucun appel CalDAV
            import os, tempfile
            from .common import Stop
            from .dav import LocalFolder
            cfg = dict(desk.c['nextcloud'])
            if not cfg.get('local_path'):
                raise Stop('dossier_local_non_renseigne')
            client = LocalFolder(cfg)
            root = cfg['roots'][0]
            try:
                items = client.list_folder(root)
            except Stop as ex:
                if str(ex) == 'http_404':
                    raise Stop('dossier_des_dossiers_absent_indiquez_slash_si_les_dossiers_clients_sont_a_la_racine') from None
                raise
            target = client._fs(root)[1]
            fd, probe = tempfile.mkstemp(dir=str(target), prefix='.axiorhub-test-')
            os.close(fd); os.unlink(probe)
            _receipt(auth, what, True)
            return True, 'Dossier local lisible et inscriptible : %d élément(s) dans %s.' % (len(items), root)
        if what == 'nextcloud':
            from .dav import DAV
            client = DAV(desk.c['nextcloud'])
            client.list_folder(desk.c['nextcloud']['roots'][0])
            _receipt(auth, what, True)
            return True, 'Nextcloud joignable ; dossier des dossiers lisible. Les agendas se testent séparément (facultatif).'
        if what == 'agenda':
            from .dav import DAV
            cals = DAV(desk.c['nextcloud']).calendars()
            urls = [c['url'] for c in cals if 'VEVENT' in c['components']]
            _receipt(auth, what, True)
            return True, '%d agenda(s) découvert(s). Choisissez les agendas autorisés dans les connexions ; aucun n’a été retenu automatiquement.' % len(urls)
        if what == 'ia':
            from .queue521 import check_ai
            message = check_ai(desk)['message']
            _receipt(auth, what, True)
            return True, message
    except Exception as ex:
        _receipt(auth, what, False)
        from .web440 import human
        return False, 'Échec : %s' % human(str(ex)) if str(ex) else 'Échec du test.'
    finally:
        if desk is not None:
            desk.db.close()
    return False, 'Test inconnu.'


def finish(auth, user):
    """5.6.24 (F20) : seules les capacités minimales du parcours choisi sont exigées — dossiers (dossier local OU Nextcloud) et IA ;
    la messagerie n'est exigée (et testée) que si elle est renseignée. Chaque reçu est lié à l'empreinte de sa seule connexion."""
    cfg = _cfg(auth)
    nc = cfg.get('nextcloud') or {}
    local = bool(nc.get('local_path'))
    remote = 'example.com' not in nc.get('url', 'example.com')
    mail = 'example.com' not in (cfg.get('mail') or {}).get('host', 'example.com')
    if not (local or remote):
        return False, 'À compléter avant de terminer : dossiers du cabinet (dossier local ou Nextcloud).'
    from datetime import timedelta
    tests = cfg.get('installation',{}).get('connection_tests567',{})
    cutoff = (datetime.now(timezone.utc) - timedelta(minutes=15)).isoformat()
    required = ['local' if local else 'nextcloud', 'ia'] + (['imap'] if mail else [])
    untested = [key for key in required if not tests.get(key,{}).get('ok')
                or tests[key].get('fingerprint') != _connection_fingerprint(cfg, key) or tests[key].get('at','') < cutoff]
    if untested:
        return False, 'Tests récents à effectuer avant de terminer : ' + ', '.join(untested) + '. Une adresse renseignée ne prouve pas que la connexion fonctionne.'
    cfg.setdefault('installation', {}).update({'done': True, 'at': datetime.now(timezone.utc).isoformat(), 'by': user['email']})
    _write_cfg(auth, cfg)
    return True, 'Installation terminée.'


def page(auth, user, notes=(), result=''):
    cfg = _cfg(auth)
    try:
        desk = _desk(auth)
        from .pieces510 import profile
        prof = profile(desk)
        spec = desk.settings('cabinet560:specialite', '') or ''
        desk.db.close()
    except Exception:
        prof, spec = {}, ''
    mail, nc, ol = cfg.get('mail') or {}, cfg.get('nextcloud') or {}, cfg.get('ollama') or {}
    v = lambda x: escape(str(x or ''), quote=True)
    ex = lambda x: '' if 'example.com' in str(x) or x in ('user', 'user@example.com') else x
    csrf = escape(auth._csrf(user), quote=True)
    ai = (cfg.get('installation') or {}).get('ai_choice', 'local')
    providers = ''.join('<option value="%s">%s</option>' % (k, escape(lbl)) for k, (lbl, _) in PROVIDERS.items())
    radio = lambda val, label, hint: '<label><input type="radio" name="ai" value="%s"%s style="width:auto"> %s</label><p class="muted">%s</p>' % (
        val, ' checked' if ai == val else '', escape(label), escape(hint))
    msgs = ''.join('<p class="warn">%s</p>' % escape(n) for n in notes) + result
    body = (msgs + '<p class="muted">Bienvenue. Renseignez le cabinet et ses connexions ; vous pourrez tout modifier ensuite dans Paramètres. Les mots de passe '
            'laissés vides sont conservés. Rien n’est envoyé ni écrit dans la messagerie pendant l’installation.</p>'
            '<form method="post" action="/installation"><input type="hidden" name="csrf" value="%s">'
            '<fieldset><legend>1 · Cabinet</legend><div class="grid"><label>Prénom<input name="prenom" value="%s" required></label><label>Nom<input name="nom" value="%s" required></label>'
            '<label>Barreau (ville)<input name="barreau" value="%s" required></label><label>Toque<input name="toque" value="%s"></label>'
            '<label>Adresse du cabinet<input name="adresse" value="%s"></label><label>Téléphone<input name="telephone" value="%s"></label>'
            '<label>Courriel professionnel<input name="email" type="email" value="%s"></label><label>Domaine d’activité (facultatif)<input name="specialite" value="%s" placeholder="droit des affaires"></label></div>'
            '<p class="muted">Ces informations composent votre signature et la présentation de l’avocat donnée à l’IA.</p></fieldset>'
            '<fieldset><legend>2 · Messagerie (IMAP, lecture et brouillons)</legend><div class="grid"><label>Serveur IMAP<input name="imap_host" value="%s" placeholder="imap.votre-hebergeur.fr"></label>'
            '<label>Port<input name="imap_port" value="%s"></label><label>Identifiant<input name="imap_user" value="%s"></label>'
            '<label>Mot de passe (ou mot de passe d’application)<input name="imap_password" type="password" autocomplete="new-password"></label>'
            '<label>Dossier des brouillons<input name="imap_drafts" value="%s"></label><label>Dossier des messages envoyés<input name="imap_sent" value="%s"></label>'
            '<label>Adresse d’expédition<input name="from_address" type="email" value="%s"></label><label>Nom d’expéditeur<input name="from_name" value="%s"></label></div>'
            '<button name="op" value="test_imap" class="ghost">Tester la messagerie</button></fieldset>'
            '<fieldset><legend>3 · Dossiers du cabinet : dossier local OU Nextcloud (agenda facultatif)</legend>'
            '<div class="grid"><label>Dossier de travail local (poste ou partage monté ; vide = Nextcloud)<input name="local_path" value="__LOCAL_PATH__" placeholder="~/Cabinet ou /mnt/cabinet"></label></div>'
            '<button name="op" value="test_local" class="ghost">Tester le dossier local</button>'
            '<div class="grid"><label>Adresse<input name="nc_url" value="%s" placeholder="https://cloud.votre-cabinet.fr"></label>'
            '<label>Compte (compte technique conseillé)<input name="nc_user" value="%s"></label><label>Mot de passe d’application<input name="nc_password" type="password" autocomplete="new-password"></label>'
            '<label>Dossier des dossiers clients<input name="nc_root" value="%s" placeholder="/Dossiers"></label></div>'
            '<button name="op" value="test_nextcloud" class="ghost">Tester Nextcloud</button> <button name="op" value="test_agenda" class="ghost">Trouver les agendas (facultatif)</button></fieldset>'
            '<fieldset><legend>4 · Intelligence artificielle</legend>%s%s%s<div class="grid"><label>Ollama (adresse)<input name="ollama_url" value="%s"></label>'
            '<label>Modèle local<input name="ollama_model" value="%s"></label><label>Fournisseur externe<select name="provider">%s</select></label>'
            '<label>Clé API<input name="provider_key" type="password" autocomplete="new-password"></label><label>Modèle externe (vide = conseillé)<input name="provider_model"></label></div>'
            '<label><input type="checkbox" name="consent" value="yes" style="width:auto"> J’autorise l’envoi au fournisseur externe des données nécessaires, '
            '<strong>pseudonymisées sur le serveur</strong> (noms, adresses, numéros remplacés par des marqueurs).</label>'
            '<button name="op" value="test_ia" class="ghost">Tester l’IA</button></fieldset>'
            '<fieldset><legend>5 · Fonctionnement</legend><label><input type="radio" name="mode" value="observe"%s style="width:auto"> Observation : l’agent lit et propose, '
            'sans écrire de brouillon (conseillé les premiers jours)</label><label><input type="radio" name="mode" value="drafts"%s style="width:auto"> Brouillons : l’agent '
            'dépose des projets de réponse dans votre dossier Brouillons (jamais d’envoi)</label></fieldset>'
            '<button name="op" value="save">Enregistrer</button> <button name="op" value="finish" class="ghost">Enregistrer et terminer l’installation</button></form>'
            '<p class="muted">6 · Ensuite : créez les comptes des membres du cabinet dans <a href="/comptes">Comptes</a>.</p>') % (
        csrf, v(prof.get('prenom_avocat')), v(prof.get('nom_avocat')), v(prof.get('ville_avocat')), v(prof.get('numero_toque_avocat')),
        v(prof.get('adresse_cabinet_avocat')), v(prof.get('numero_telephone_avocat')), v(prof.get('email_avocat')), v(spec),
        v(ex(mail.get('host'))), v(mail.get('port', 993)), v(ex(mail.get('username'))), v(mail.get('drafts', 'Drafts')), v(mail.get('sent', 'Sent')),
        v(ex(mail.get('from_address'))), v(mail.get('from_name') if mail.get('from_name') != 'Cabinet exemple' else ''),
        v(ex(nc.get('url'))), v(ex(nc.get('username'))), v((nc.get('roots') or [''])[0] if (nc.get('roots') or [''])[0] != '/Dossiers' else ''),
        radio('local', 'Tout en local (Ollama)', 'Rien ne sort du serveur ; nécessite un serveur assez puissant (carte graphique conseillée).'),
        radio('mixte', 'Mixte', 'Tri et lecture des pièces en local, rédaction et analyse via l’API choisie, avec pseudonymisation.'),
        radio('externe', 'Tout via l’API', 'Pour un VPS sans carte graphique : toutes les fonctions via le fournisseur choisi, avec pseudonymisation.'),
        v(ol.get('url')), v(ol.get('model')), providers, ' checked' if cfg.get('mode') != 'drafts' else '', ' checked' if cfg.get('mode') == 'drafts' else '')
    body = body.replace('__LOCAL_PATH__', v(nc.get('local_path', '')))
    body += '<p>Pour une IA externe, renseignez ses tarifs, ses plafonds et les fonctions autorisées avant le test : <a href="/ia-externe">Fournisseurs et budgets</a> · <a href="/routage-hybride">Politique de routage</a>. Ces écrans sont accessibles à l’administrateur avant la fin de l’installation.</p>'
    return auth._page('Installation d’AxiorHub Pilote', body, wide=True)


def handle(auth, env, start, user):
    if env.get('REQUEST_METHOD') != 'POST':
        return auth._respond(start, '200 OK', page(auth, user))
    form = auth._form(env, 40000)
    if not auth._same_origin(env) or not hmac.compare_digest(form.get('csrf', ''), auth._csrf(user)):
        return auth._respond(start, '400 Bad Request', auth._page('Installation', '<p class="warn">Formulaire expiré : rechargez la page.</p>'))
    notes = save(auth, form)
    op = form.get('op', 'save')
    result = '<p class="ok">Enregistré.</p>'
    if op.startswith('test_'):
        ok, message = test(auth, op[5:])
        result = '<p class="%s">%s</p>' % ('ok' if ok else 'warn', escape(message))
    elif op == 'finish':
        ok, message = finish(auth, user)
        if ok and not notes:
            return auth._respond(start, '303 See Other', '', [('Location', '/')])
        result = '<p class="%s">%s</p>' % ('ok' if ok else 'warn', escape(message))
    return auth._respond(start, '200 OK', page(auth, user, notes, result))
