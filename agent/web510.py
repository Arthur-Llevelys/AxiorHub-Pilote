"""Interface 5.1.0 « Pièces et bordereaux » : page unique, rendue côté serveur, actions par l'API JSON protégée.

Toutes les valeurs affichées sont échappées ; l'aperçu du tampon est un SVG produit ici à partir du profil de l'avocat (texte échappé).
"""
import base64
from pathlib import PurePosixPath
from html import escape as e
import json

from .common import Stop
from . import pieces510 as p5, pieces520
from .web500 import _form, _field, _hidden, _opts, _badge

MESSAGES = {
    'bordereau_absent': 'Ce projet de bordereau n’existe plus.',
    'bordereau_deja_cree': 'Ce bordereau a déjà été créé : préparez-en un nouveau pour le modifier.',
    'bordereau_en_creation': 'La création est en cours : attendez la fin du traitement.',
    'bordereau_incomplet': 'Le bordereau n’est pas prêt : lancez les contrôles et corrigez les points bloquants.',
    'tableau_invalide': 'Le tableau envoyé ne correspond pas au projet : rechargez la page.',
    'date_piece_invalide': 'Une date de pièce est invalide (format AAAA-MM-JJ).',
    'date_bordereau_invalide': 'La date du bordereau est invalide.',
    'trop_de_pieces': 'Plus de 300 fichiers dans ce dossier : choisissez le sous-dossier des pièces.',
    'instruction_vide': 'Écrivez l’instruction (par exemple : retire la pièce 7, ajoute le constat en 9).',
    'plan_absent_ou_incomplet': 'Le plan contient des instructions non comprises : corrigez l’instruction avant de l’appliquer.',
    'confirmation_bordereau_absente': 'Demandez d’abord le code de confirmation.',
    'confirmation_bordereau_expiree': 'Le code a expiré : demandez-en un nouveau.',
    'code_confirmation_bordereau_invalide': 'Code incorrect : recopiez les six chiffres affichés.',
    'piece_modifiee_depuis_analyse': 'Une pièce a été modifiée dans Nextcloud depuis l’analyse : relancez l’analyse du dossier.',
    'piece_pdf_protegee': 'PDF protégé par un mot de passe : retirez la protection avant de l’ajouter.',
    'piece_pdf_illisible': 'PDF illisible ou endommagé.',
    'modele_bordereau_invalide': 'Le modèle doit être un fichier Word .docx (2 Mo au plus).',
    'modele_bordereau_sans_liste_de_pieces': 'Le modèle doit contenir au moins une ligne « {{ intitule_piece_01 }} ».',
    'conclusions_introuvables': 'Aucun fichier de conclusions trouvé dans le dossier : indiquez son chemin.',
    'limite_taille_invalide': 'Limite de taille invalide.',
    'texte_invalide': 'Texte invalide.',
    'adresse_invalide': 'Adresse électronique invalide.',
    'fichier_bordereau_non_verifie': 'Un fichier créé n’a pas pu être relu dans Nextcloud : vérifiez le dossier de destination.',
    'image_jpeg_invalide': 'Image JPEG illisible.',
    'premiere_piece_invalide': 'Le numéro de la première pièce doit être un nombre entre 1 et 999.',
    'image_tampon_invalide': 'L’image du tampon doit être un PNG ou un JPEG (190 Ko au plus).',
    'image_tampon_format_non_gere': 'PNG non pris en charge (8 bits non entrelacé uniquement) : enregistrez l’image en PNG standard ou en JPEG.',
    'confirmation_tampon_image_requise': 'Cochez la case autorisant l’apposition de votre tampon importé sur ce bordereau.',
    'bordereau_adverse_illisible': 'Aucune ligne « Pièce n° … » n’a été trouvée dans le bordereau adverse.',
    'conclusions_non_word': 'Les conclusions doivent être au format Word (.docx) pour corriger les renvois.',
    'page_plus_lourde_que_la_limite': 'Une page dépasse à elle seule la limite par envoi : utilisez e-Partage.',
    'connexion_imap_echouee': 'Connexion à la messagerie impossible : vérifiez le serveur, l’identifiant et le mot de passe (Paramètres › Connexions).',
    'dossier_brouillons_introuvable': 'Messagerie joignable, mais le dossier des brouillons configuré est introuvable.',
    'ocr_indisponible': 'OCR indisponible sur le serveur (Tesseract ou Poppler absent).',
    'generation_ia_delai_depasse': 'Le modèle d’IA n’a pas répondu à temps (plus de 4 minutes). Un modèle local trop lourd pour le serveur ralentit toute la file : choisissez un modèle plus léger (Paramètres › IA) ou le mode hybride.',
    'fournisseur_ia_injoignable': 'Le modèle d’IA est injoignable depuis le serveur : Ollama est-il démarré (sudo systemctl status ollama) ?',
    'fournisseur_ia_inactif': 'Le fournisseur d’IA choisi pour cette fonction est désactivé (Paramètres › IA).',
    'modele_fournisseur_absent': 'Aucun modèle d’IA n’est choisi pour cette fonction (Paramètres › IA).',
    'generation_ia_incomplete': 'Le modèle d’IA a rendu une réponse incomplète ; réessayez ou choisissez un modèle plus capable.',
    'reponse_ia_vide': 'Le modèle d’IA a rendu une réponse vide.',
    'json_ia_invalide': 'Le modèle d’IA n’a pas respecté le format demandé ; réessayez ou choisissez un modèle plus capable.',
    'contexte_trop_long': 'Trop de texte à transmettre au modèle d’IA : précisez la demande ou réduisez les pièces.',
    'jurisprudence_officielle_verifiee_absente': 'Aucune décision officielle vérifiée n’a été trouvée : l’avis n’est pas rédigé sans source vérifiée.',
    'demande_document_invalide': 'Décrivez le document demandé (8 caractères au moins).',
    'type_document_invalide': 'Type de document invalide.',
    'document_a_modifier_absent': 'Le document à modifier n’existe plus ou n’a pas été créé.',
    'redaction_document_impossible': 'Le modèle d’IA n’a pas pu rédiger le document : vérifiez-le dans « Pourquoi rien n’est produit ? ».',
    'document_vide': 'Le modèle a renvoyé un document vide.',
    'rappel_destinataire_refuse': 'Un rappel ne peut être déposé que dans la boîte du cabinet.',
}
STATUS = {'projet': 'Projet', 'a_confirmer': 'Code demandé', 'en_creation': 'Création en cours', 'cree': 'Créé', 'abandonne': 'Abandonné'}
ERRORS = {'piece_pdf_protegee': 'protégé', 'piece_pdf_illisible': 'illisible', 'piece_trop_volumineuse': 'trop lourd', 'image_jpeg_invalide': 'image illisible'}


def _size(n):
    return '%.1f Mo' % (n / 1e6) if n >= 100_000 else '%d Ko' % max(1, n // 1000)


def _profile_html(desk):
    prof = p5.profile(desk)
    image = pieces520.stamp_image_path(desk)
    complete = prof['nom_avocat'] and prof['ville_avocat']
    fields = ''.join(_field(label, key, prof[key], 'email' if key == 'email_avocat' else 'text', 'maxlength="200"') for key, label in p5.PROFILE_FIELDS)
    return ('<details class="p5-section"%s><summary><h2>Profil de l’avocat et tampon</h2></summary>'
            '<p class="vf-note">Ces informations remplissent l’en-tête du bordereau et le tampon apposé sur les pièces.</p>'
            '<div class="p5-profile">%s<figure class="p5-stamp"><div class="p5-stamp-img">%s</div>'
            '<figcaption>Aperçu du tampon (pièce n° 12). <button type="button" class="ax-btn ghost p5-export" data-api="m510/stamp.svg">Télécharger l’image (SVG)</button></figcaption></figure></div>'
            '<h3>Tampon importé (facultatif)</h3><p class="vf-note">Tampon scanné ou signature, en PNG ou JPEG. Il ne remplace le tampon généré que si l’option est cochée pour un '
            'bordereau, et une case de confirmation est exigée à chaque création.</p><div class="p5-row">%s<label class="m5-field">Importer une image<input type="file" class="p5-upload-image" accept=".png,.jpg,.jpeg"></label>%s</div></details>') % (
        '' if complete else ' open', _form('m510/profile', fields, 'Enregistrer le profil'), p5.stamp_svg(prof, 12),
        ('<p>Image importée : <strong>%s</strong>.</p>' % e(image.name)) if image else '<p>Aucune image importée.</p>',
        _form('m510/stamp-image/remove', '', 'Supprimer l’image', cls='m5-inline m5-mini', confirm='Supprimer l’image du tampon ?') if image else '')


def _template_html(desk):
    raw, origin = p5.template_bytes(desk)
    try:
        fields = p5.template_fields(raw)
    except Exception:
        fields = []
    scalar = [f for f in fields if not f.startswith('intitule_piece_')]
    return ('<details class="p5-section"><summary><h2>Modèle de bordereau</h2></summary>'
            '<p>Modèle utilisé : <strong>%s</strong>. Balises reconnues : %s. La liste « Pièce n°XX » est étendue automatiquement au nombre réel de pièces.</p>'
            '<div class="p5-row"><label class="m5-field">Remplacer par un modèle Word (.docx)<input type="file" class="p5-upload" accept=".docx"></label>%s</div>'
            '<p class="vf-note">Balises possibles : %s.</p></details>') % (
        'modèle du cabinet' if origin == 'cabinet' else 'modèle fourni avec AxiorHub', e(', '.join(scalar) or 'aucune'),
        _form('m510/template/reset', '', 'Revenir au modèle fourni', cls='m5-inline m5-mini', confirm='Supprimer le modèle importé ?') if origin == 'cabinet' else '',
        e(', '.join('{{ %s }}' % k for k, _ in p5.PROFILE_FIELDS + p5.CASE_FIELDS) + ', {{ intitule_piece_01 }}'))


def _jobs_html(desk, matter):
    rows = desk.db.execute("SELECT id,status,args,result FROM jobs WHERE kind='pieces_scan510' ORDER BY id DESC LIMIT 8").fetchall()
    out = []
    for r in rows:
        try:
            if json.loads(r['args'] or '{}').get('matter') != matter:
                continue
        except ValueError:
            continue
        if r['status'] in ('pending', 'running'):
            out.append('<p class="m5-confirm" role="status">Analyse n° %d %s… <a href="">Actualiser</a></p>' % (r['id'], 'en attente' if r['status'] == 'pending' else 'en cours'))
        elif r['status'] == 'error':
            try:
                code = json.loads(r['result'] or '{}').get('erreur', '')
            except ValueError:
                code = ''
            out.append('<p class="m5-warn">Analyse n° %d interrompue : %s</p>' % (r['id'], e(MESSAGES.get(code, code.replace('_', ' ')))))
        break
    return ''.join(out)


def _draft_list(desk, prefix, matter, current):
    rows = p5.drafts(desk, matter, 12)
    if not rows:
        return '<p class="vf-note">Aucun bordereau pour ce dossier.</p>'
    return '<ul class="p5-drafts">' + ''.join(
        '<li%s><a href="%s">%s</a> %s</li>' % (' aria-current="true"' if r['id'] == current else '', e('%s/pieces?matter=%s&draft=%s' % (prefix, r['matter'], r['id']), quote=True),
                                               e(r['updated'][:16].replace('T', ' ')), _badge('muted' if r['status'] != 'cree' else 'ok', STATUS.get(r['status'], r['status'])))
        for r in rows) + '</ul>'


def _case_html(draft):
    data = draft['data']
    case, opts = data['case'], data['options']
    fields = ''.join(_field(label, key, case.get(key, ''), 'date' if key in ('date_du_bordereau',) else 'text', 'maxlength="300"') for key, label in p5.CASE_FIELDS)
    fields += _field('Conclusions à contrôler (chemin Nextcloud, facultatif)', 'conclusions_path', data.get('conclusions_path', ''), 'text', 'maxlength="500" placeholder="Vide : les conclusions les plus récentes du dossier"')

    def sel(name, value, choices):
        return '<label class="m5-field">%s<select name="%s">%s</select></label>' % (e(name), e(choices[0]), ''.join(
            '<option value="%s"%s>%s</option>' % (e(k), ' selected' if k == value else '', e(v)) for k, v in choices[1]))

    def chk(key, label):
        return '<label class="m5-check"><input type="checkbox" name="%s"%s> %s</label>' % (e(key), ' checked' if opts.get(key) else '', e(label))
    options = (sel('Tampon de l’avocat', opts['stamp'], ('stamp', (('first', 'Sur la première page de chaque pièce'), ('all', 'Sur toutes les pages'), ('none', 'Aucun tampon')))) +
               sel('Emplacement', opts['position'], ('position', (('haut_droite', 'En haut à droite'), ('haut_gauche', 'En haut à gauche'), ('bas_droite', 'En bas à droite'), ('bas_gauche', 'En bas à gauche')))) +
               chk('number_each_page', '« Pièce n° X – p. 2/5 » sur les autres pages') + chk('continuous', 'Pagination continue de toutes les pièces') +
               chk('single_pdf', 'Un seul PDF avec signets (au lieu d’un fichier par pièce)') + chk('bordereau_first', 'Bordereau en tête du PDF unique') +
               chk('ocr', 'OCR des pièces numérisées (texte cherchable, intitulés et dates)') +
               chk('fix_conclusions', 'Conclusions Word : renvois corrigés en suivi de modifications') +
               chk('stamp_image', 'Utiliser mon tampon importé (confirmation à la création)') +
               chk('continue_numbering', 'Numéroter à la suite des bordereaux précédents') +
               chk('deposit', 'Préparer le dépôt e-Barreau (envois sous la limite)') +
               _field('Limite par envoi e-Barreau (Mo)', 'deposit_mb', str(opts['deposit_mb']), 'number', 'min="1" max="1000"') +
               _field('Limite par fichier PDF (Mo)', 'max_file_mb', str(opts['max_file_mb']), 'number', 'min="1" max="500"') +
               _field('Limite totale (Mo)', 'max_total_mb', str(opts['max_total_mb']), 'number', 'min="1" max="5000"'))
    return ('<details class="p5-section"><summary><h2>En-tête du bordereau et options</h2></summary><p class="vf-note">%s</p>%%s</details>' % e(pieces520.DEPOSIT_NOTE) %
            _form('m510/case', _hidden(draft=draft['id']) + '<div class="p5-grid">' + fields + '</div><h3>Options des pièces</h3><div class="p5-grid">' + options + '</div>',
                  'Enregistrer l’en-tête et les options'))


def _table_html(draft):
    data = draft['data']
    rows, n = [], p5.first_number(data) - 1
    for r in data['rows']:
        if r.get('include'):
            n += 1
        flags = []
        if not r['readable']:
            flags.append(_badge('bad', ERRORS.get(r['error'], 'illisible')))
        if r.get('scan'):
            flags.append(_badge('warn', 'scan sans texte'))
        if r.get('duplicate_of'):
            flags.append(_badge('warn', 'doublon'))
        if r['kind'] == 'jpeg':
            flags.append(_badge('muted', 'photo convertie'))
        if r.get('ocr'):
            flags.append(_badge('muted', 'texte OCR'))
        if r.get('communicated'):
            c = r['communicated']
            flags.append(_badge('warn', 'déjà communiquée : n° %s, bordereau %s du %s' % (c['number'], c['bordereau'], c['day'])))
        rows.append(('<tr data-key="%s"><td class="p5-move"><button type="button" class="p5-up" aria-label="Monter">↑</button><button type="button" class="p5-down" aria-label="Descendre">↓</button></td>'
                     '<td><input type="checkbox" class="p5-inc" aria-label="Retenir"%s></td><td class="p5-num">%s</td>'
                     '<td><input class="p5-title" maxlength="300" value="%s" aria-label="Intitulé"></td>'
                     '<td><input class="p5-date" type="date" value="%s" aria-label="Date"></td>'
                     '<td class="p5-file" title="%s">%s<br><small>%d p. · %s</small> %s</td></tr>') % (
            e(r['key'], quote=True), ' checked' if r.get('include') else '', n if r.get('include') else '–', e(r['title'], quote=True), e(r.get('date', ''), quote=True),
            e(r['path'], quote=True), e(r['name']), r['pages'], _size(r['size']), ''.join(flags)))
    order = data.get('order', 'nom')
    others = ''.join('<li>%s — %s</li>' % (e(o['path'].rsplit('/', 1)[-1]), e(o['reason'])) for o in data.get('others', [])[:40])
    return ('<section class="p5-section"><h2>Pièces (%d retenue(s) sur %d)</h2>'
            '<p class="vf-note">Cochez les pièces à communiquer, corrigez les intitulés et les dates, réordonnez avec ↑ ↓, puis enregistrez. Rien n’est écrit dans Nextcloud à ce stade. '
            'Analyse du %s, dossier « %s ».</p>'
            '<div class="p5-scroll" data-first="%d"><div class="p5-row"><label class="m5-field">Classer selon<select class="p5-order">%s</select></label>'
            '<button type="button" class="ax-btn p5-save" data-draft="%s">Enregistrer le tableau</button></div>'
            '<table class="vf-table p5-table"><thead><tr><th></th><th>Retenir</th><th>N°</th><th>Intitulé</th><th>Date</th><th>Fichier</th></tr></thead><tbody>%s</tbody></table></div>'
            '%s</section>') % (
        len(p5.included(data)), len(data['rows']), e(data['scan']['at'][:16].replace('T', ' ')), e(data['scan']['folder']), p5.first_number(data),
        ''.join('<option value="%s"%s>%s</option>' % (k, ' selected' if k == order else '', v) for k, v in (('nom', 'numéro ou nom de fichier'), ('chronologique', 'date (chronologique)'), ('conclusions', 'ordre de citation dans les conclusions'))),
        e(draft['id'], quote=True), ''.join(rows) or '<tr><td colspan="6">Aucun PDF trouvé.</td></tr>',
        ('<details><summary>Fichiers non retenus faute de format PDF (%d)</summary><ul>%s</ul></details>' % (len(data.get('others', [])), others)) if others else '')


def _checks_html(draft):
    checks = draft['data'].get('checks') or {}
    body = _form('m510/check', _hidden(draft=draft['id']), 'Lancer les contrôles', cls='m5-inline')
    if checks:
        level = {'bloquant': ('m5-alert', 'Bloquant'), 'attention': ('m5-warn', 'À vérifier'), 'info': ('m5-confirm', 'Information')}
        items = ''.join('<li class="%s"><strong>%s</strong> — %s</li>' % (level[i['level']][0], level[i['level']][1], e(i['text'])) for i in checks['issues'])
        body += '<p class="vf-note">Contrôle du %s%s.</p><ul class="p5-issues">%s</ul>' % (
            e(checks['at'][:16].replace('T', ' ')), (' — conclusions : ' + e(checks['conclusions'])) if checks.get('conclusions') else '',
            items or '<li class="m5-confirm">Aucun point relevé.</li>')
    return '<section class="p5-section"><h2>Contrôles</h2><p class="vf-note">Pièce citée dans les conclusions mais absente, pièce jamais citée, doublons, PDF protégés ou illisibles, poids.</p>%s</section>' % body


def _plan_html(draft):
    data = draft['data']
    plan = data.get('plan') or {}
    html = ('<section class="p5-section"><h2>Modifier par instruction</h2><p class="vf-note">Exemples : « retire la pièce 7 », « ajoute le constat d’huissier en 9 », '
            '« place la pièce 3 en 1 », « renomme la pièce 2 en Contrat de bail du 3 mars 2024 ». Le plan est montré avant toute modification.</p>%s') % _form(
        'm510/plan', _hidden(draft=draft['id']) + '<label class="m5-field">Instruction<textarea name="instruction" rows="3" maxlength="2000">%s</textarea></label>' % e(plan.get('instruction', '')),
        'Préparer le plan')
    if plan:
        html += '<div class="p5-plan"><h3>Plan proposé</h3>'
        if plan['errors']:
            html += '<div class="m5-alert"><ul>%s</ul></div>' % ''.join('<li>%s</li>' % e(x) for x in plan['errors'])
        html += '<ol>%s</ol>' % ''.join('<li>%s</li>' % e(x) for x in plan['steps'])
        html += '<table class="vf-table"><thead><tr><th>Avant</th><th>Après</th><th>Intitulé</th></tr></thead><tbody>%s</tbody></table>' % ''.join(
            '<tr><td>%s</td><td><strong>%d</strong></td><td>%s</td></tr>' % ('nouvelle' if m['old'] is None else m['old'], m['new'], e(m['title'])) for m in plan['mapping'])
        if plan.get('references'):
            html += '<h3>Renvois à corriger dans les conclusions</h3><p class="vf-note">%s — à corriger par vous dans le document : AxiorHub ne modifie pas vos conclusions.</p><ul>%s</ul>' % (
                e(plan.get('conclusions', '')), ''.join('<li><strong>pièce n° %d → %s</strong> : « %s »</li>' % (
                    r['old'], 'retirée' if r['new'] is None else 'n° %d' % r['new'], e(r['excerpt'])) for r in plan['references']))
        if not plan['errors']:
            html += _form('m510/plan/apply', _hidden(draft=draft['id']), 'Appliquer ce plan au projet', cls='m5-inline')
        html += '</div>'
    return html + '</section>'


def _create_html(desk, draft, prefix):
    status = draft['status']
    if status == 'cree':
        res = draft['result']
        files = ''.join('<li>%s%s</li>' % (e(f['path'].rsplit('/', 1)[-1]), (' — <a href="%s" target="_blank" rel="noopener noreferrer">ouvrir dans Nextcloud</a>' % e(f['url'], quote=True)) if str(f.get('url', '')).startswith('https://') else '') for f in res.get('files', []))
        miss = ('<p class="m5-warn">Champs laissés « [à compléter] » dans le bordereau : %s.</p>' % e(', '.join(res['missing_fields']))) if res.get('missing_fields') else ''
        pdf = '' if res.get('bordereau_pdf') else '<p class="m5-warn">Bordereau PDF non produit (LibreOffice absent du serveur) : seul le DOCX a été créé.</p>'
        extra = ''
        if res.get('ocr_pages'):
            extra += '<p class="m5-confirm">%d page(s) numérisée(s) rendues cherchables par OCR.</p>' % res['ocr_pages']
        rv = res.get('renvois') or {}
        if rv.get('error'):
            extra += '<p class="m5-warn">Renvois des conclusions non corrigés : %s</p>' % e(MESSAGES.get(rv['error'], rv['error'].replace('_', ' ')))
        elif rv:
            extra += '<p class="m5-confirm">Conclusions : %d renvoi(s) corrigé(s) en suivi de modifications dans une nouvelle version (original intact).</p>' % rv.get('applied', 0)
            if rv.get('manual'):
                extra += '<div class="m5-warn"><p>À revoir à la main dans vos conclusions :</p><ul>%s</ul></div>' % ''.join(
                    '<li>pièce %s : « %s »</li>' % (e(m['piece']), e(m['excerpt'])) for m in rv['manual'])
        dep = res.get('deposit')
        if dep:
            extra += '<p class="m5-confirm">Dépôt e-Barreau préparé : %d envoi(s) dans le sous-dossier « Dépôt e-Barreau » (voir LISEZMOI.txt).</p>' % dep['envois']
            extra += ''.join('<p class="m5-warn">%s</p>' % e(w) for w in dep.get('warnings', []))
        return '<section class="p5-section"><h2>Fichiers créés</h2><p>Dossier : <strong>%s</strong> — %d pièce(s), %d page(s), numérotées à partir de %s.</p>%s%s%s<ul>%s</ul></section>' % (
            e(res.get('folder', '')), res.get('pieces', 0), res.get('pages', 0), res.get('first', 1), miss, pdf, extra, files)
    if status == 'en_creation':
        job = desk.db.execute('SELECT status,result FROM jobs WHERE id=?', (draft['job_id'] or 0,)).fetchone()
        return '<section class="p5-section"><h2>Création</h2><p class="m5-confirm" role="status">Création en cours (demande n° %s, %s). <a href="">Actualiser</a></p></section>' % (
            draft['job_id'], 'en attente du service' if not job or job['status'] == 'pending' else 'traitement')
    last = ''
    if draft['job_id']:
        job = desk.db.execute('SELECT status,result FROM jobs WHERE id=?', (draft['job_id'],)).fetchone()
        if job and job['status'] == 'error':
            try:
                code = json.loads(job['result'] or '{}').get('erreur', '')
            except ValueError:
                code = ''
            last = '<p class="m5-alert">Dernière création interrompue : %s</p>' % e(MESSAGES.get(code, code.replace('_', ' ')))
    return ('<section class="p5-section"><h2>Créer le bordereau et les pièces</h2>%s'
            '<p class="vf-note">Les fichiers sont créés dans un <strong>nouveau</strong> dossier « %s / Bordereau n°… » du dossier client. Les originaux ne sont ni modifiés ni déplacés. '
            'Chaque pièce est relue dans Nextcloud ; une pièce modifiée depuis l’analyse arrête la création.</p>'
            '<div class="p5-row"><button type="button" class="ax-btn ghost p5-export" data-api="m510/preview.docx?draft=%s">Aperçu du bordereau (DOCX)</button>'
            '<button type="button" class="ax-btn p5-request" data-draft="%s">Demander le code de confirmation</button></div><div id="p5-code" class="m5-result" role="status"></div>'
            '<form class="m5-form m5-inline" data-api="m510/create/confirm" data-reload="1">%s<label class="m5-field">Code à six chiffres<input name="code" inputmode="numeric" pattern="[0-9]{6}" maxlength="6" required></label>%s'
            '<button class="ax-btn" type="submit">Créer dans Nextcloud</button></form>%s</section>') % (
        last, e(p5.OUTPUT_FOLDER), e(draft['id'], quote=True), e(draft['id'], quote=True), _hidden(draft=draft['id']),
        ('<label class="m5-check"><input type="checkbox" name="confirm_image" required> J’autorise l’apposition de mon tampon importé sur les pièces de ce bordereau</label>'
         if draft['data']['options']['stamp'] != 'none' and draft['data']['options'].get('stamp_image') else ''),
        _form('m510/abandon', _hidden(draft=draft['id']), 'Abandonner ce projet', cls='m5-inline m5-mini', confirm='Abandonner ce projet de bordereau ?'))


def _history_html(desk, matter):
    rows = pieces520.history(desk, matter)
    if not rows:
        return ('<details class="p5-section"><summary><h2>Historique des communications</h2></summary>'
                '<p class="vf-note">Aucune pièce communiquée par AxiorHub pour ce dossier.</p></details>')
    body = ''.join('<tr><td>%d</td><td>%s</td><td>n° %s du %s</td><td>%s</td></tr>' % (
        r['number'], e(r['title']), e(r['bordereau']), e(r['day']), e(r['recipient'] or '—')) for r in rows)
    return ('<details class="p5-section"><summary><h2>Historique des communications (%d pièce(s), dernier n° %d)</h2></summary>'
            '<p class="vf-note">Un nouveau bordereau commence par défaut au numéro suivant ; une pièce identique déjà communiquée est signalée et décochée.</p>'
            '<div class="p5-scroll"><table class="vf-table"><thead><tr><th>N°</th><th>Pièce</th><th>Bordereau</th><th>Destinataire</th></tr></thead><tbody>%s</tbody></table></div></details>') % (
        len(rows), max(r['number'] for r in rows), body)


def _adverse_html(desk, matter):
    last = desk.settings('pieces520:adverse:' + matter, None)
    form = _form('m510/adverse', _hidden(matter=matter) +
                 _field('Bordereau adverse (chemin Nextcloud du PDF ou Word)', 'bordereau_path', (last or {}).get('bordereau', ''), 'text', 'required maxlength="500"') +
                 _field('Dossier des pièces reçues (facultatif : celui du bordereau)', 'folder', (last or {}).get('folder', ''), 'text', 'maxlength="500"') +
                 _field('Conclusions adverses (facultatif)', 'conclusions_path', (last or {}).get('conclusions', ''), 'text', 'maxlength="500"'),
                 'Lire et rapprocher')
    out = ''
    if last:
        out += '<p class="vf-note">Lecture du %s : %d pièce(s) annoncée(s).</p>' % (e(last['at'][:16].replace('T', ' ')), last['announced'])
        if last['missing']:
            out += '<div class="m5-alert"><p><strong>Annoncées mais non reçues :</strong></p><ul>%s</ul></div>' % ''.join(
                '<li>Pièce n° %d — %s</li>' % (m['number'], e(m['title'])) for m in last['missing'])
        if last['cited_absent']:
            out += '<p class="m5-warn">Citées dans les conclusions adverses mais absentes de leur bordereau : n° %s.</p>' % e(', '.join(str(n) for n in last['cited_absent']))
        if last['extra']:
            out += '<p class="vf-note">Fichiers reçus non rattachés : %s.</p>' % e(', '.join(PurePosixPath(p).name for p in last['extra'][:20]))
        out += '<details><summary>Pièces rapprochées (%d)</summary><ul>%s</ul></details>' % (len(last['matched']), ''.join(
            '<li>n° %d — %s → %s (%s)</li>' % (m['number'], e(m['title']), e(PurePosixPath(m['file']).name), e(m['how'])) for m in last['matched']))
    return ('<details class="p5-section"%s><summary><h2>Bordereau adverse reçu</h2></summary>'
            '<p class="vf-note">AxiorHub lit le bordereau adverse (OCR s’il est numérisé), le rapproche des fichiers reçus et des conclusions adverses.</p>%s%s</details>') % (
        ' open' if last and (last['missing'] or last['cited_absent']) else '', form, out)


def page(desk, auth, prefix, args, shell):
    p5.ensure_schema(desk)
    matter = args.get('matter', '')
    did = args.get('draft', '')
    draft = None
    if did:
        try:
            draft = p5.get(desk, did)
            matter = draft['matter']
        except Stop:
            draft = None
    html = ('<h1>Pièces et bordereaux</h1><p class="ax-muted">AxiorHub lit les PDF du dossier, propose le bordereau, numérote et tamponne les pièces dans de '
            '<strong>nouveaux</strong> fichiers après votre confirmation. Traitement local : aucun modèle d’IA ni service externe.</p>')
    html += _profile_html(desk) + _template_html(desk)
    html += ('<section class="p5-section"><h2>Préparer un bordereau</h2>%s%s%s</section>') % (
        _form('m510/scan', '<label class="m5-field">Dossier<select name="matter" required>%s</select></label>'
                           '<label class="m5-field">Classer selon<select name="order"><option value="nom">numéro ou nom de fichier</option><option value="chronologique">date (chronologique)</option>'
                           '<option value="conclusions">ordre de citation dans les conclusions</option></select></label>%s' % (
                               _opts(desk, matter, 'Choisir un dossier'), _field('Sous-dossier des pièces (facultatif)', 'source_folder', '', 'text', 'maxlength="300" placeholder="Ex. 03 - Pièces"')),
              'Analyser les PDF du dossier', cls='m5-inline'),
        _jobs_html(desk, matter) if matter else '', _draft_list(desk, prefix, matter, did) if matter else '')
    if matter:
        html += _history_html(desk, matter) + _adverse_html(desk, matter)
    if draft:
        html += '<h2 class="p5-draft-title">Bordereau n° %s — %s %s</h2>' % (e(draft['data']['case'].get('numero_bordereau', '')), e(draft['data']['case'].get('Dossier', '')),
                                                                         _badge('muted', STATUS.get(draft['status'], draft['status'])))
        if draft['status'] in ('projet', 'a_confirmer'):
            html += _case_html(draft) + _table_html(draft) + _checks_html(draft) + _plan_html(draft)
        html += _create_html(desk, draft, prefix)
    head = '<link rel="stylesheet" href="%s/static/v510.css"><script defer src="%s/static/v510.js"></script>' % (e(prefix), e(prefix))
    return shell('Pièces et bordereaux', html, prefix, auth['csrf'], '/pieces', head)


def _b64_download(name, mime, raw):
    return {'download': {'filename': name, 'mime': mime, 'base64': base64.b64encode(raw).decode()}}


def handle(desk, name, data, method='POST', args=None):
    args = args or {}
    n = name[len('m510/'):]
    if method == 'GET':
        if n == 'stamp.svg':
            return {'download': {'filename': 'tampon-avocat.svg', 'mime': 'image/svg+xml', 'text': p5.stamp_svg(p5.profile(desk), 12)}}
        if n == 'preview.docx':
            raw, missing = p5.preview_docx(desk, args.get('draft', ''))
            return _b64_download('apercu-bordereau.docx', 'application/vnd.openxmlformats-officedocument.wordprocessingml.document', raw)
        if n == 'draft':
            return p5.get(desk, args.get('draft', ''))
        raise Stop('route_inconnue')
    s = lambda k: str(data.get(k, '') if data.get(k) is not None else '')
    if n == 'profile':
        return p5.save_profile(desk, data)
    if n == 'template/upload':
        try:
            raw = base64.b64decode(s('data'), validate=True)
        except ValueError:
            raise Stop('modele_bordereau_invalide') from None
        return p5.save_template(desk, raw)
    if n == 'template/reset':
        return p5.reset_template(desk)
    if n == 'scan':
        p5._matter(desk, s('matter'))
        job = desk.enqueue('pieces_scan510', {'matter': s('matter'), 'order': s('order'), 'source_folder': s('source_folder')[:300]}, priority=0)
        return {'job_id': job}
    if n == 'table':
        return p5.update_table(desk, s('draft'), data.get('rows'), order=s('order') or None)
    if n == 'case':
        case = {k: s(k) for k, _ in p5.CASE_FIELDS}
        case['conclusions_path'] = s('conclusions_path')
        opts = {k: data.get(k) for k in p5.DEFAULT_OPTIONS if k in data or isinstance(p5.DEFAULT_OPTIONS[k], bool)}
        return p5.update_table(desk, s('draft'), None, case=case, opts=opts)
    if n == 'check':
        return p5.check(desk, s('draft'))
    if n == 'plan':
        return p5.plan_instructions(desk, s('draft'), s('instruction'))
    if n == 'plan/apply':
        return p5.apply_plan(desk, s('draft'))
    if n == 'create/request':
        return p5.request_creation(desk, s('draft'))
    if n == 'create/confirm':
        return p5.confirm_creation(desk, s('draft'), s('code'), data.get('confirm_image'))
    if n == 'stamp-image/upload':
        try:
            raw = base64.b64decode(s('data'), validate=True)
        except ValueError:
            raise Stop('image_tampon_invalide') from None
        return pieces520.save_stamp_image(desk, raw)
    if n == 'stamp-image/remove':
        return pieces520.remove_stamp_image(desk)
    if n == 'adverse':
        return pieces520.adverse_check(desk, s('matter'), s('bordereau_path'), s('folder'), s('conclusions_path'))
    if n == 'abandon':
        return p5.abandon(desk, s('draft'))
    raise Stop('route_inconnue')
