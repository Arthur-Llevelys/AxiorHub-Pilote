"""5.6.13, refondue en 5.6.14 (C13) : recette de production réelle.

Trois niveaux séparés : tests de connexion (Mise en service), transport (dépôt Nextcloud et brouillon IMAP relus), mission complète (le
vrai producteur de documents, le contrôle avec une erreur substantielle injectée, une interruption après écriture puis la reprise sans
second dépôt, un modèle Word réellement comparé). Chaque étape affiche réussi, échoué ou non testé ; un prérequis obligatoire non
testé (modèle du cabinet absent) empêche un succès global. Données fictives seulement ; rien n'est envoyé ; les fichiers et le brouillon
« recette » sont à supprimer par le cabinet.
"""
from datetime import datetime, timezone
from email import policy
from email.message import EmailMessage
from email.utils import formataddr, format_datetime
import hashlib
import io
import json
import re
import zipfile

from .common import Stop, clean_path, digest

RECETTE_FOLDER = '_RECETTE_AXIORHUB'
SETTING = 'recette5613:last'
DOC = {'titre': 'Recette AxiorHub — document fictif', 'nom_fichier': 'Recette', 'sources': [], 'a_completer': ['Donnée fictive à compléter'],
       'paragraphes': [{'style': 'intertitre', 'texte': 'Objet de la recette'},
                       {'style': 'texte', 'texte': 'Ce document est produit par la recette de production. Il ne concerne aucun dossier réel.'},
                       {'style': 'liste', 'texte': 'Modèle du cabinet, dépôt Nextcloud, reprise après interruption, brouillon IMAP.'}]}
# Texte de contrôle avec une erreur substantielle : le montant 4 800 € n'existe dans aucune source (la source dit 4 200 €) ; le dispositif reprend 4 800 €.
CONTROL_TEXT = ('RAPPEL DES FAITS\nPar contrat du 03/02/2026, la société fictive A a loué un local à la société fictive B pour un loyer de 4 200 euros.\n'
                'DISCUSSION\nLe loyer impayé s’élève à 4 800 euros.\nPAR CES MOTIFS\nCondamner la société B à payer 4 800 euros.')
CONTROL_SOURCE = {'id': 'f-recette', 'path': '/recette/Contrat fictif.pdf', 'text': 'Contrat de bail du 03/02/2026 entre la société A et la société B. Loyer mensuel : 4 200 euros.'}


def _step(steps, ident, label, ok, message):
    steps.append({'id': ident, 'label': label, 'ok': ok, 'message': message})


def _docx_parts(raw):
    with zipfile.ZipFile(io.BytesIO(raw)) as z:
        names = z.namelist()
        return names, z.read('word/document.xml').decode('utf-8', 'replace')


def _mailbox(desk):
    from .mailbox import Mailbox
    return Mailbox(desk.c['mail'])


def run(desk, dav=None, box=None, stamp=None):
    from .docrequest520 import build_docx
    from . import pilote5613, controle5614
    steps = []
    stamp = stamp or datetime.now(timezone.utc).strftime('%Y%m%d-%H%M%S')
    mandatory_untested = []
    # 1. producteur réel : Word générique lisible, notes internes hors du corps
    data = b''
    try:
        data = build_docx(DOC, {}, 'Recette de production')
        names, xml = _docx_parts(data)
        ok = 'word/document.xml' in names and 'Objet de la recette' in xml and 'Projet préparé par AxiorHub' not in xml and 'Sources utilisées' not in xml
        _step(steps, 'word', 'Projet Word (producteur réel, corps sans note interne)', ok, 'Document construit et relu (%d octets), aucune note technique dans le corps.' % len(data) if ok else 'Document illisible ou note interne présente dans le corps.')
    except Exception as ex:
        _step(steps, 'word', 'Projet Word (producteur réel)', False, 'Construction impossible : %s' % str(ex)[:120])
    # 2. modèle du cabinet réellement comparé (en-têtes/pieds identiques, balises remplies ou marquées, corps inséré)
    try:
        row, raw = pilote5613.template_for_kind(desk, 'courrier')
        if row is None:
            _step(steps, 'template', 'Modèle Word du cabinet', None, 'Aucun modèle approuvé pour les courriers : non testé (prérequis obligatoire — importer un modèle dans Documents du cabinet).')
            mandatory_untested.append('template')
        else:
            built, info = pilote5613.build_from_template_ex(desk, raw, DOC, {}, 'Recette', {'id': 'RECETTE', 'client_name': 'Recette'},
                                                            fields={'destinataire': 'Société fictive', 'qualite': 'destinataire fictif', 'adresse': '1 rue Fictive, 00000 Ville', 'envoi': 'Lettre fictive'})
            tnames, txml = _docx_parts(raw)
            bnames, bxml = _docx_parts(built)
            heads = [n for n in tnames if re.fullmatch(r'word/(?:header|footer)\d+\.xml', n)]
            with zipfile.ZipFile(io.BytesIO(raw)) as zt, zipfile.ZipFile(io.BytesIO(built)) as zb:
                identical = all(n in bnames and zt.read(n) == zb.read(n) for n in heads)
            ok = identical and 'OBJET DE LA RECETTE' in bxml.upper() and '{{' not in bxml and not info['missing_fields']
            _step(steps, 'template', 'Modèle Word du cabinet', ok, ('Modèle « %s » respecté : %d en-tête(s)/pied(s) identiques, balises remplies, corps inséré.' % (row['label'], len(heads))) if ok
                  else 'Modèle « %s » : %s' % (row['label'], ('champs manquants ' + ', '.join(info['missing_fields'])) if info['missing_fields'] else 'en-têtes ou pieds de page modifiés ou balise non remplie'))
            if ok:
                data = built
    except Exception as ex:
        _step(steps, 'template', 'Modèle Word du cabinet', False, 'Modèle non exploitable : %s' % str(ex)[:120])
    # 3. contrôle avec erreur substantielle injectée (déterministe, sans modèle)
    try:
        report = controle5614.review(desk, CONTROL_TEXT, [CONTROL_SOURCE], 'assignation', '', pieces=None)
        flagged = any('4800' in str(d.get('avant', '')).replace(' ', '').replace(' ', '') for d in report['defects'])
        ok = flagged and report['outcome'] in ('reserves', 'bloque')
        _step(steps, 'control', 'Contrôle avec erreur substantielle', ok, ('Erreur de montant détectée ; aucun badge global « vérifié » (%s).' % report['summary']) if ok
              else 'L’erreur de montant injectée n’a pas été détectée (%s).' % report['summary'])
    except Exception as ex:
        _step(steps, 'control', 'Contrôle avec erreur substantielle', False, 'Contrôle impossible : %s' % str(ex)[:120])
    # 4. dépôt Nextcloud relu
    client, path = None, ''
    try:
        from .document_projects import _dav
        client = dav or _dav(desk)
        roots = [r for r in (desk.c.get('nextcloud', {}).get('matter_roots') or desk.c.get('nextcloud', {}).get('roots') or []) if r]
        if not roots or not data:
            raise Stop('racine_nextcloud_absente')
        folder = clean_path(roots[0].rstrip('/') + '/' + RECETTE_FOLDER)
        client.ensure_folder(folder, roots[0])
        path = clean_path(folder + '/recette-%s.docx' % stamp)
        client.put_file(path, data, 'application/vnd.openxmlformats-officedocument.wordprocessingml.document')
        back = client.download(client.stat(path))
        ok = hashlib.sha256(back).hexdigest() == hashlib.sha256(data).hexdigest()
        _step(steps, 'deposit', 'Dépôt Nextcloud relu', ok, ('Fichier déposé et relu à l’identique : %s' % path) if ok else 'Le fichier relu diffère du fichier déposé : %s' % path)
    except Exception as ex:
        _step(steps, 'deposit', 'Dépôt Nextcloud relu', False, 'Dépôt impossible : %s' % str(ex)[:120])
    # 5. interruption après écriture, avant confirmation, puis reprise par le journal d'opérations (aucun second dépôt)
    try:
        if client is None or not data:
            raise Stop('depot_prealable_echoue')
        from . import taches5614
        taches5614.ensure_schema(desk)
        from .deposits567 import staged_path
        mission_id, task_id = digest('recette5614|' + stamp)[:32], digest('recette5614-task|' + stamp)[:32]
        sha = hashlib.sha256(data).hexdigest()
        key = digest('op5614|' + mission_id + '|' + task_id + '|' + sha)[:32]
        resume = clean_path(path.rsplit('/', 1)[0] + '/recette-%s-reprise.docx' % stamp)
        staged_path(desk, key).write_bytes(data)
        now = desk.now()
        client.put_file(resume, data, 'application/vnd.openxmlformats-officedocument.wordprocessingml.document')   # écrit par un worker « interrompu » avant confirmation
        desk.db.execute('INSERT OR REPLACE INTO ops5614 VALUES(?,?,?,?,?,?,?,?,?,?)', (key, mission_id, task_id, 'depot_word', 'en_cours', sha, '', json.dumps({'path': resume}), now, now))
        desk.db.commit()
        puts_before = len(client.puts) if hasattr(client, 'puts') else None
        meta = client.stat(resume)                               # reprise : rapprochement de l'effet réel avant toute répétition
        back = client.download(meta)
        if hashlib.sha256(back).hexdigest() != sha:
            raise Stop('contenu_document_non_verifie_conflit')
        desk.db.execute("UPDATE ops5614 SET state='confirme',remote_ref=?,detail=?,updated=? WHERE id=?", (resume, json.dumps({'path': resume, 'sha256': sha}), desk.now(), key))
        desk.db.commit()
        staged_path(desk, key).unlink(missing_ok=True)
        puts_after = len(client.puts) if hasattr(client, 'puts') else None
        state = desk.db.execute('SELECT state FROM ops5614 WHERE id=?', (key,)).fetchone()['state']
        ok = state == 'confirme' and (puts_before is None or puts_after == puts_before)
        _step(steps, 'resume', 'Reprise après interruption (journal d’opérations)', ok, ('Écriture interrompue rapprochée puis confirmée sans second dépôt : %s' % resume) if ok else 'Reprise incorrecte (état %s).' % state)
    except Exception as ex:
        _step(steps, 'resume', 'Reprise après interruption', False, 'Reprise impossible : %s' % str(ex)[:120])
    # 6. brouillon IMAP déposé puis relu intégralement
    try:
        cfg = desk.c['mail']
        own = str(cfg.get('from_address') or '').strip()
        if not own:
            raise Stop('adresse_cabinet_absente')
        msg = EmailMessage(policy=policy.SMTP)
        msg['From'] = formataddr((str(cfg.get('from_name') or 'Cabinet'), own))
        msg['To'] = own
        msg['Subject'] = 'Recette AxiorHub %s — brouillon de test (à supprimer)' % stamp
        msg['Date'] = format_datetime(datetime.now(timezone.utc))
        msg['Message-ID'] = '<axiorhub-recette-%s@mail-agent.local>' % stamp
        msg['X-AxiorHub-Draft-Key'] = 'recette:' + stamp
        msg.set_content('Brouillon créé par la recette de production AxiorHub. Aucun envoi. À supprimer.\n')
        mailbox = box or _mailbox(desk)
        try:
            mailbox.append_draft(msg)
            proof = mailbox.verify_draft(msg)
        finally:
            try:
                mailbox.close()
            except Exception:
                pass
        _step(steps, 'draft', 'Brouillon IMAP relu intégralement', True, 'Brouillon déposé et retrouvé dans « %s » (UID %s), sujet, expéditeur et corps conformes. À supprimer manuellement.' % (proof.get('folder', ''), proof.get('uid', '')))
    except Exception as ex:
        _step(steps, 'draft', 'Brouillon IMAP relu', False, 'Brouillon non vérifié : %s' % str(ex)[:120])
    failed = [s['id'] for s in steps if s['ok'] is False]
    report = {'at': desk.now(), 'stamp': stamp, 'ok': not failed and not mandatory_untested, 'incomplete': bool(mandatory_untested), 'untested': mandatory_untested,
              'failed': failed, 'steps': steps, 'path': path, 'levels': {'connexion': 'Mise en service', 'transport': ['deposit', 'draft'], 'mission': ['word', 'template', 'control', 'resume']}}
    desk.setting(SETTING, report)
    desk.audit('recette5613', {'ok': report['ok'], 'failed': failed, 'untested': mandatory_untested})
    return report


def last(desk):
    return desk.settings(SETTING, None)


def section_html(desk):
    from html import escape as e
    report = last(desk)
    rows = ''
    if report:
        for s in report['steps']:
            cls = {True: 'ok', False: 'bad', None: 'muted'}[s['ok']]
            rows += '<li class="r569-%s"><strong>%s</strong> <span class="vf-badge muted">%s</span> — %s</li>' % (cls, e(s['label']), {True: 'réussi', False: 'échoué', None: 'non testé'}[s['ok']], e(s['message']))
    verdict = ''
    if report:
        if report['ok']:
            verdict = '<p class="ok">Recette réussie.</p>'
        elif report.get('failed'):
            verdict = '<p class="notice">Recette en échec : %s.</p>' % e(', '.join(report['failed']))
        else:
            verdict = '<p class="notice">Recette incomplète : prérequis non testé (%s).</p>' % e(', '.join(report.get('untested', [])))
    return ('<section class="ax-card" id="recette5613"><h2>Recette de production réelle%s</h2>'
            '<p>Trois niveaux : connexion (contrôles ci-dessus), transport (dépôt Nextcloud et brouillon IMAP relus) et mission (vrai producteur Word, modèle du cabinet comparé, '
            'contrôle avec erreur injectée, interruption après écriture puis reprise sans second dépôt). Données fictives dans <code>%s</code> ; rien n’est envoyé ; les fichiers et le brouillon « recette » sont à supprimer ensuite. '
            'Un prérequis non testé empêche un succès global.</p>'
            '<button type="button" class="ax-btn" data-recette5613-run data-confirm="Lancer la recette : deux fichiers de recette seront créés dans Nextcloud et un brouillon dans Brouillons ?">Lancer la recette de production</button>'
            '<p id="recette5613-status" role="status"></p>%s%s</section>') % (
        (' (%s)' % e(str(report['at'])[:16].replace('T', ' '))) if report else '', e(RECETTE_FOLDER), verdict,
        ('<ul class="r569-list">%s</ul>' % rows) if report else '<p class="vf-note">Aucune recette lancée pour l’instant.</p>')
