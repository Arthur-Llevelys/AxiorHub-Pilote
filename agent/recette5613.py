"""5.6.13 : recette de production réelle.

Les tests de connexion disent qu'un service répond ; cette recette vérifie qu'AxiorHub produit effectivement, sur les services réels du
cabinet et avec des données fictives : un projet Word générique lisible, le respect du modèle du cabinet (en-têtes et pieds de page
conservés), un dépôt Nextcloud relu octet pour octet dans un dossier de recette dédié, la reprise d'un dépôt interrompu, et un
brouillon IMAP déposé puis retrouvé dans Brouillons. Rien n'est envoyé. Les fichiers créés sont nommés « recette » et sont à supprimer
par le cabinet (le brouillon de courriel reste dans Brouillons jusqu'à sa suppression manuelle).
"""
from datetime import datetime, timezone
from email import policy
from email.message import EmailMessage
from email.utils import formataddr, format_datetime
import hashlib
import io
import re
import zipfile

from .common import Stop, clean_path

RECETTE_FOLDER = '_RECETTE_AXIORHUB'
SETTING = 'recette5613:last'
DOC = {'titre': 'Recette AxiorHub — document fictif', 'nom_fichier': 'Recette', 'sources': [], 'a_completer': ['Donnée fictive à compléter'],
       'paragraphes': [{'style': 'intertitre', 'texte': 'Objet de la recette'},
                       {'style': 'texte', 'texte': 'Ce document est produit par la recette de production. Il ne concerne aucun dossier réel.'},
                       {'style': 'liste', 'texte': 'Modèle du cabinet, dépôt Nextcloud, reprise après interruption, brouillon IMAP.'}]}


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
    from . import pilote5613
    steps = []
    stamp = stamp or datetime.now(timezone.utc).strftime('%Y%m%d-%H%M%S')
    # 1. projet Word générique lisible
    try:
        data = build_docx(DOC, {}, 'Recette de production')
        names, xml = _docx_parts(data)
        ok = 'word/document.xml' in names and 'Objet de la recette' in xml
        _step(steps, 'word', 'Projet Word générique', ok, 'Document Word construit et relu (%d octets).' % len(data) if ok else 'Document Word illisible.')
    except Exception as ex:
        data = b''
        _step(steps, 'word', 'Projet Word générique', False, 'Construction impossible : %s' % str(ex)[:120])
    # 2. modèle du cabinet
    try:
        row, raw = pilote5613.template_for_kind(desk, 'courrier')
        if row is None:
            _step(steps, 'template', 'Modèle Word du cabinet', None, 'Aucun modèle approuvé pour les courriers : projet générique utilisé (Documents du cabinet pour en importer un).')
        else:
            built = pilote5613.build_from_template(desk, raw, DOC, {}, 'Recette', {'id': 'RECETTE', 'client_name': 'Recette'})
            tnames, _ = _docx_parts(raw)
            bnames, bxml = _docx_parts(built)
            heads = [n for n in tnames if re.fullmatch(r'word/(?:header|footer)\d+\.xml', n)]
            ok = all(n in bnames for n in heads) and 'Objet de la recette'.upper() in bxml.upper() and '{{' not in bxml
            _step(steps, 'template', 'Modèle Word du cabinet', ok, ('Modèle « %s » respecté : %d en-tête(s)/pied(s) de page conservé(s), corps inséré.' % (row['label'], len(heads))) if ok
                  else 'Le document produit ne reprend pas intégralement le modèle « %s ».' % row['label'])
            if ok:
                data = built
    except Exception as ex:
        _step(steps, 'template', 'Modèle Word du cabinet', False, 'Modèle non exploitable : %s' % str(ex)[:120])
    # 3. dépôt Nextcloud relu, dans un dossier de recette
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
    # 4. reprise après interruption : contenu mis en attente localement, dépôt « interrompu » avant le PUT, puis repris
    try:
        if client is None or not data:
            raise Stop('depot_prealable_echoue')
        from .deposits567 import staged_path
        rid = hashlib.sha256(('recette5613|' + stamp).encode()).hexdigest()[:32]
        staged = staged_path(desk, rid)
        staged.write_bytes(data)
        resume = clean_path(path.rsplit('/', 1)[0] + '/recette-%s-reprise.docx' % stamp)
        try:
            client.stat(resume)
            raise Stop('fichier_reprise_deja_present')
        except Stop as ex:
            if str(ex) not in ('http_404', 'fichier_nextcloud_introuvable'):
                raise
        local = staged.read_bytes()
        if hashlib.sha256(local).hexdigest() != hashlib.sha256(data).hexdigest():
            raise Stop('contenu_depot_local_altere')
        client.put_file(resume, local, 'application/vnd.openxmlformats-officedocument.wordprocessingml.document')
        back = client.download(client.stat(resume))
        staged.unlink(missing_ok=True)
        ok = hashlib.sha256(back).hexdigest() == hashlib.sha256(data).hexdigest()
        _step(steps, 'resume', 'Reprise après interruption', ok, ('Contenu conservé localement puis déposé et relu : %s' % resume) if ok else 'Reprise incomplète : %s' % resume)
    except Exception as ex:
        _step(steps, 'resume', 'Reprise après interruption', False, 'Reprise impossible : %s' % str(ex)[:120])
    # 5. brouillon IMAP déposé puis retrouvé
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
        _step(steps, 'draft', 'Brouillon IMAP relu', True, 'Brouillon déposé et retrouvé dans « %s » (UID %s). À supprimer manuellement.' % (proof.get('folder', ''), proof.get('uid', '')))
    except Exception as ex:
        _step(steps, 'draft', 'Brouillon IMAP relu', False, 'Brouillon non vérifié : %s' % str(ex)[:120])
    report = {'at': desk.now(), 'stamp': stamp, 'ok': all(s['ok'] is not False for s in steps), 'steps': steps, 'path': path}
    desk.setting(SETTING, report)
    desk.audit('recette5613', {'ok': report['ok'], 'failed': [s['id'] for s in steps if s['ok'] is False]})
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
            rows += '<li class="r569-%s"><strong>%s</strong> — %s</li>' % (cls, e(s['label']), e(s['message']))
    return ('<section class="ax-card" id="recette5613"><h2>Recette de production réelle%s</h2>'
            '<p>Vérifie, avec des données fictives et sur vos services réels : projet Word lisible, respect du modèle du cabinet, dépôt Nextcloud relu '
            'dans le dossier <code>%s</code> de la première racine, reprise d’un dépôt interrompu, brouillon IMAP déposé et retrouvé. Rien n’est envoyé ; '
            'les fichiers et le brouillon « recette » sont à supprimer ensuite.</p>'
            '<button type="button" class="ax-btn" data-recette5613-run data-confirm="Lancer la recette : deux fichiers de recette seront créés dans Nextcloud et un brouillon dans Brouillons ?">Lancer la recette de production</button>'
            '<p id="recette5613-status" role="status"></p>%s</section>') % (
        (' (%s)' % e(str(report['at'])[:16].replace('T', ' '))) if report else '', e(RECETTE_FOLDER),
        ('<ul class="r569-list">%s</ul>' % rows) if report else '<p class="vf-note">Aucune recette lancée pour l’instant.</p>')
