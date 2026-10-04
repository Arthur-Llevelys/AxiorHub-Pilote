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
    path = Path(auth.app.config_path)
    tmp = path.with_suffix('.tmp')
    tmp.write_text(json.dumps(cfg, ensure_ascii=False, indent=2) + '\n', encoding='utf-8')
    try:
        os.chmod(tmp, 0o600)
    except OSError:
        pass
    os.replace(tmp, path)


def _secrets_dir(cfg):
    current = (cfg.get('mail') or {}).get('password_file') or ''
    base = Path(current).parent if current else Path(cfg.get('state_dir', '/data/state')).parent / 'secrets'
    base.mkdir(parents=True, exist_ok=True)
    return base


def _secret(cfg, name, value):
    path = _secrets_dir(cfg) / (name + '.secret')
    path.write_text(value.strip() + '\n', encoding='utf-8')
    try:
        os.chmod(path, 0o600)
    except OSError:
        pass
    return str(path)


def _clean(value, maximum=200):
    return re.sub(r'\s+', ' ', str(value or '')).strip()[:maximum]


def _desk(auth):
    from .common import load_config
    from .desk import Desk
    return Desk(load_config(auth.app.config_path))


def save(auth, form):
    """Enregistre les six parties ; renvoie la liste des remarques."""
    cfg = _cfg(auth)
    notes = []
    mail = cfg.setdefault('mail', {})
    for key, field in (('host', 'imap_host'), ('username', 'imap_user'), ('drafts', 'imap_drafts'), ('sent', 'imap_sent'),
                       ('from_address', 'from_address'), ('from_name', 'from_name')):
        if form.get(field):
            mail[key] = _clean(form[field], 200)
    if form.get('imap_port'):
        try:
            mail['port'] = max(1, min(int(form['imap_port']), 65535))
        except ValueError:
            notes.append('Port IMAP invalide : 993 conservé.')
    if form.get('imap_password'):
        mail['password_file'] = _secret(cfg, 'imap', form['imap_password'])
    if mail.get('from_address'):
        mail['own_addresses'] = sorted(set([mail['from_address']] + [a for a in mail.get('own_addresses', []) if a and 'example.com' not in a]))
    if mail.get('drafts') in (mail.get('inbox'), mail.get('sent')):
        notes.append('Le dossier des brouillons doit être distinct de la boîte de réception et des messages envoyés.')
    nc = cfg.setdefault('nextcloud', {})
    if form.get('nc_url'):
        url = _clean(form['nc_url'], 300).rstrip('/')
        if not url.startswith('https://'):
            notes.append('L’adresse Nextcloud doit commencer par https://')
        else:
            nc['url'] = url
    if form.get('nc_user'):
        nc['username'] = _clean(form['nc_user'], 120)
    if form.get('nc_password'):
        nc['password_file'] = _secret(cfg, 'nextcloud', form['nc_password'])
    if form.get('nc_root'):
        root = '/' + _clean(form['nc_root'], 300).strip('/')
        nc['roots'] = [root]
        nc['matter_roots'] = [root]
    cfg['mode'] = 'drafts' if form.get('mode') == 'drafts' else 'observe'
    ai = form.get('ai', 'local')
    if form.get('ollama_url'):
        cfg.setdefault('ollama', {})['url'] = _clean(form['ollama_url'], 300)
    if form.get('ollama_model'):
        cfg.setdefault('ollama', {})['model'] = _clean(form['ollama_model'], 120)
    cfg.setdefault('installation', {})['ai_choice'] = ai if ai in ('local', 'mixte', 'externe') else 'local'
    _write_cfg(auth, cfg)
    # profil de l'avocat, IA : réglages applicatifs (base d'AxiorHub)
    try:
        desk = _desk(auth)
    except Exception as ex:                                   # configuration encore incomplète
        notes.append('Configuration incomplète pour l’instant (%s) : complétez la messagerie.' % str(ex)[:80])
        return notes
    try:
        return _save_app(desk, cfg, form, ai, notes)
    finally:
        desk.db.close()


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
            return True, check_imap(desk)['message']
        if what == 'nextcloud':
            from .dav import DAV
            client = DAV(desk.c['nextcloud'])
            client.list_folder(desk.c['nextcloud']['roots'][0])
            cals = client.calendars()
            urls = [c['url'] for c in cals if 'VEVENT' in c['components']]
            cfg = _cfg(auth)
            cfg.setdefault('calendar', {})['urls'] = urls
            _write_cfg(auth, cfg)
            return True, 'Nextcloud joignable ; dossier des dossiers lisible ; %d agenda(s) trouvé(s) et retenu(s).' % len(urls)
        if what == 'ia':
            from .queue521 import check_ai
            return True, check_ai(desk)['message']
    except Exception as ex:
        from .web440 import human
        return False, 'Échec : %s' % human(str(ex)) if str(ex) else 'Échec du test.'
    finally:
        if desk is not None:
            desk.db.close()
    return False, 'Test inconnu.'


def finish(auth, user):
    cfg = _cfg(auth)
    missing = []
    if 'example.com' in (cfg.get('mail') or {}).get('host', 'example.com'):
        missing.append('messagerie')
    if 'example.com' in (cfg.get('nextcloud') or {}).get('url', 'example.com'):
        missing.append('Nextcloud')
    if missing:
        return False, 'À compléter avant de terminer : ' + ', '.join(missing) + '.'
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
            '<fieldset><legend>3 · Nextcloud (dossiers et agenda)</legend><div class="grid"><label>Adresse<input name="nc_url" value="%s" placeholder="https://cloud.votre-cabinet.fr"></label>'
            '<label>Compte (compte technique conseillé)<input name="nc_user" value="%s"></label><label>Mot de passe d’application<input name="nc_password" type="password" autocomplete="new-password"></label>'
            '<label>Dossier des dossiers clients<input name="nc_root" value="%s" placeholder="/Dossiers"></label></div>'
            '<button name="op" value="test_nextcloud" class="ghost">Tester Nextcloud et trouver les agendas</button></fieldset>'
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
