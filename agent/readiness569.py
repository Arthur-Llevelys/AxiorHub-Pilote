"""5.6.9 : mise en service — un seul écran (et une commande) qui contrôle les services réels du cabinet et dit quoi corriger.

Chaque contrôle est local au serveur ou lit un service déjà configuré (IMAP, Nextcloud, Ollama, Invoice Ninja, Talk, synthèse
vocale) sans rien écrire ni envoyer. Le micro et le HTTPS sont vérifiés dans le navigateur, sans transmission d'audio.
"""
from html import escape as e
import time

from .common import HTTP, Stop


def _item(key, label, ok, message, fix=''):
    return {'key': key, 'label': label, 'ok': ok, 'message': str(message)[:400], 'fix': fix}


def _connector(desk, kind):
    from .config567 import test
    out = test(desk, {'connector': kind})
    return bool(out.get('ok')), str(out.get('message') or out.get('code') or '')


def run(desk):
    """Exécute tous les contrôles (jusqu'à une minute) et mémorise le rapport. ok : True, False, ou None (non activé)."""
    checks = []
    # 1. services systemd et workers
    try:
        from .reliability393 import service_health
        s = service_health(desk)
        failed = [x['label'] for x in s['services'] if x['required'] and not x['verified']]
        checks.append(_item('services', 'Services AxiorHub', not failed,
                            'Tous les services requis sont actifs.' if not failed else 'Inactifs : ' + ', '.join(failed),
                            'sudo systemctl restart axiorhub-mail-ui axiorhub-mail-desk-worker axiorhub-mail-watch' if failed else ''))
    except Exception as ex:
        checks.append(_item('services', 'Services AxiorHub', None, 'État systemd non lisible ici (%s) : normal sous Docker.' % str(ex)[:80]))
    try:
        rows = desk.db.execute("SELECT name,heartbeat FROM live_services_v430 WHERE name LIKE 'worker-%'").fetchall()
        alive = any(time.time() - float(r[1]) < 90 for r in rows)
        checks.append(_item('workers', 'Moteur de traitements', alive, 'Un worker a donné signe de vie depuis moins de 90 s.' if alive else
                            'Aucun worker récent : les demandes resteront en attente.', '' if alive else 'Pourquoi rien n’est produit ? › Services'))
    except Exception:
        checks.append(_item('workers', 'Moteur de traitements', None, 'Table de surveillance absente (première installation).'))
    # 2. connecteurs réels
    for kind, label, fix in (('imap', 'Messagerie IMAP', 'Paramètres › Connexions › IMAP'), ('nextcloud', 'Nextcloud et agendas', 'Paramètres › Connexions › Nextcloud'),
                             ('ollama', 'Modèle local Ollama', 'Paramètres › Connexions › Ollama')):
        try:
            ok, message = _connector(desk, kind)
            checks.append(_item(kind, label, ok, message, '' if ok else fix))
        except Stop as ex:
            checks.append(_item(kind, label, False, str(ex), fix))
    sent = str(desk.c.get('mail', {}).get('sent') or '')
    checks.append(_item('sent', 'Dossier Envoyés (engagements)', bool(sent), ('Dossier « %s » configuré.' % sent) if sent else
                        'Non configuré : le suivi des promesses lit le dossier Envoyés réel.', '' if sent else 'Paramètres › Connexions › IMAP › dossier Envoyés'))
    # 3. voix
    audio = desk.c.get('audio', {})
    if audio.get('enabled'):
        try:
            HTTP(str(audio.get('bridge_base_url') or 'http://127.0.0.1:9011'), local_only=True, local_hosts=audio.get('local_hosts', ()), timeout=4).request('GET', str(audio.get('bridge_base_url') or 'http://127.0.0.1:9011').rstrip('/') + '/', limit=20000)
            checks.append(_item('dictation', 'Transcription locale (dictée, conversation)', True, 'Passerelle vocale joignable.'))
        except Stop as ex:
            reachable = str(ex).startswith('http_') and str(ex) not in ('http_502', 'http_503', 'http_504')
            checks.append(_item('dictation', 'Transcription locale (dictée, conversation)', reachable,
                                'Passerelle joignable (réponse %s).' % str(ex) if reachable else 'Passerelle injoignable : ' + str(ex),
                                '' if reachable else 'sudo systemctl status axiorhub-vocal-bridge ; python3 install-vocal-bridge.py'))
    else:
        checks.append(_item('dictation', 'Transcription locale (dictée, conversation)', None, 'Non activée : la conversation vocale requiert la passerelle Whisper locale.',
                            'Paramètres › Connexions › Voix'))
    try:
        from .voice568 import diagnostic
        d = diagnostic(desk)
        ok = bool(d.get('ok', d.get('local_available', True)))
        checks.append(_item('tts', 'Synthèse vocale (lecture)', ok, str(d.get('message') or d.get('provider') or d)[:300], '' if ok else 'sudo apt-get install -y espeak-ng'))
    except Exception as ex:
        checks.append(_item('tts', 'Synthèse vocale (lecture)', False, str(ex)[:200], 'sudo apt-get install -y espeak-ng'))
    # 4. intégrations facultatives
    invoice = desk.c.get('invoice_ninja', {})
    if invoice.get('enabled'):
        try:
            ok, message = _connector(desk, 'invoice_ninja')
            checks.append(_item('invoice_ninja', 'Invoice Ninja', ok, message + (' Écriture activée (brouillons, temps).' if invoice.get('write_enabled') else ' Lecture seule.'),
                                '' if ok else 'Paramètres › Connexions › Invoice Ninja'))
        except Stop as ex:
            checks.append(_item('invoice_ninja', 'Invoice Ninja', False, str(ex), 'Paramètres › Connexions › Invoice Ninja'))
    else:
        checks.append(_item('invoice_ninja', 'Invoice Ninja', None, 'Non activé.'))
    g = desk.c.get('google_calendar568', {})
    configured = bool(g.get('client_id') and g.get('client_secret_file') and str(g.get('redirect_uri') or '').startswith('https://'))
    checks.append(_item('google', 'Google Calendar (OAuth)', True if configured else None,
                        'Client OAuth configuré ; autorisez le compte dans Agendas et procédure.' if configured else 'Non configuré (facultatif).',
                        '' if configured else 'Paramètres › Connexions › Google Calendar'))
    try:
        from .settings568 import profile
        from .web567 import actor
        talk = profile(desk, 'cabinet').get('talk_enabled')
    except Exception:
        talk = False
    if talk:
        try:
            from .talk568 import diagnostic as talk_diag
            d = talk_diag(desk)
            ok = bool(d.get('ok', True))
            checks.append(_item('talk', 'Nextcloud Talk', ok, str(d.get('message') or d)[:300], '' if ok else 'Initiatives et voix › Rendez-vous Nextcloud Talk'))
        except Exception as ex:
            checks.append(_item('talk', 'Nextcloud Talk', False, str(ex)[:200], 'Initiatives et voix › Rendez-vous Nextcloud Talk'))
    else:
        checks.append(_item('talk', 'Nextcloud Talk', None, 'Non activé (facultatif).'))
    try:
        from .reception567 import capabilities
        cap = capabilities(desk)
        if cap['telephone'] or cap['whatsapp']:
            mode = 'conversationnel' if cap['phone_conversation'] == 'administrative_speech' else 'par touches'
            checks.append(_item('reception', 'Accueil téléphonique / WhatsApp', True, 'Téléphone %s · WhatsApp %s (accueil %s).' % (
                'prêt' if cap['telephone'] else 'non activé', 'prêt' if cap['whatsapp'] else 'non activé', mode)))
        else:
            checks.append(_item('reception', 'Accueil téléphonique / WhatsApp', None, 'Non activé (facultatif).', 'Paramètres › Connexions › Accueil administratif'))
    except Exception as ex:
        checks.append(_item('reception', 'Accueil téléphonique / WhatsApp', False, str(ex)[:200]))
    # 5. régime économe
    try:
        from . import economie569
        checks.append(_item('economy', 'Régime économe', True, 'Activé : %d analyses automatiques par jour au plus.' % economie569.quota(desk)
                            if economie569.enabled(desk) else 'Désactivé : régime complet.'))
    except Exception:
        pass
    report = {'at': desk.now(), 'checks': checks, 'ok': all(x['ok'] is not False for x in checks)}
    desk.setting('readiness569:last', report)
    desk.audit('mise_en_service569', {'ok': report['ok'], 'failed': [x['key'] for x in checks if x['ok'] is False], 'writes': 0})
    return report


def last(desk):
    return desk.settings('readiness569:last', None)


def text(report):
    lines = ['Mise en service AxiorHub — ' + str(report.get('at', ''))[:19]]
    for x in report['checks']:
        mark = {True: 'OK', False: 'KO', None: '--'}[x['ok']]
        lines.append('[%s] %s — %s' % (mark, x['label'], x['message']) + ((' → ' + x['fix']) if x['fix'] and x['ok'] is False else ''))
    lines.append('Résultat : ' + ('prêt' if report['ok'] else 'des corrections sont nécessaires'))
    return '\n'.join(lines)


def page(desk, auth, prefix, env):
    from .web440 import shell
    report = last(desk)
    rows = ''
    if report:
        for x in report['checks']:
            cls = {True: 'ok', False: 'bad', None: 'muted'}[x['ok']]
            rows += ('<li class="r569-%s"><strong>%s</strong> — %s%s</li>' % (
                cls, e(x['label']), e(x['message']), (' <em>Correctif : %s</em>' % e(x['fix'])) if x['fix'] and x['ok'] is False else ''))
    body = ('<h1>Mise en service</h1><p>Un seul écran pour vérifier les services réels du cabinet, le micro et le démarrage. '
            'Les contrôles lisent les services configurés ; ils n’écrivent rien et n’envoient rien.</p>'
            '<div class="actions"><button type="button" class="ax-btn" data-readiness569-run>Lancer les contrôles (jusqu’à une minute)</button>'
            '<a class="ax-btn ghost" href="%s/parametres/connexions">Connexions et secrets</a><a class="ax-btn ghost" href="%s/diagnostic">Pourquoi rien n’est produit ?</a></div>'
            '<p id="readiness569-status" role="status"></p>'
            '<section class="ax-card"><h2>Dernier rapport%s</h2>%s</section>'
            '<section class="ax-card" id="readiness569-mic"><h2>Micro et HTTPS (ce navigateur)</h2>'
            '<p>Le test écoute le micro deux secondes et mesure le niveau sur place ; aucun son n’est transmis.</p>'
            '<button type="button" class="ax-btn ghost" data-mic-test>Tester le micro</button><p data-mic-result role="status"></p></section>'
            '<section class="ax-card"><h2>Docker</h2><p>Installation Docker : <code>docker compose ps</code> doit montrer le service <code>axiorhub</code> '
            '« healthy » ; <code>docker compose exec axiorhub python3 docker/health.py</code> affiche la raison si ce n’est pas le cas, et '
            '<code>docker compose exec axiorhub python3 manage.py readiness</code> imprime ce rapport. Option vocale : '
            '<code>docker compose -f docker-compose.yml -f docker-compose.voice568.yml up -d --build</code>.</p></section>'
            '<script defer src="%s/static/v569.js"></script>') % (
        e(prefix), e(prefix), (' (%s)' % e(str(report['at'])[:16].replace('T', ' '))) if report else '',
        ('<ul class="r569-list">%s</ul>' % rows) if report else '<p class="vf-note">Aucun contrôle lancé pour l’instant.</p>', e(prefix))
    return shell('Mise en service', body, prefix, auth['csrf'], '/parametres')
