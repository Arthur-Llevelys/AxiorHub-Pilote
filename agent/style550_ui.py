"""Page « Mon style » (AxiorHub 5.5.0) et API m550/."""
from html import escape as e

from .common import Stop
from . import style550


def _habit_card(h, pending):
    label = style550.TYPES.get(h['doc_type'], h['doc_type'])
    if pending:
        actions = ('<label class="s550-sr" for="s550-t-%s">Texte de l’habitude</label><textarea id="s550-t-%s" rows="2" maxlength="600">%s</textarea>'
                   '<div class="s550-actions"><button type="button" class="ax-btn" data-habit="%s" data-action="valider">Valider</button>'
                   '<button type="button" class="ax-btn ghost" data-habit="%s" data-action="ecarter">Écarter</button></div>') % (
            e(h['id']), e(h['id']), e(h['text']), e(h['id'], quote=True), e(h['id'], quote=True))
        body = ''
    else:
        actions = '<div class="s550-actions"><button type="button" class="ax-btn ghost" data-habit="%s" data-action="retirer">Retirer</button></div>' % e(h['id'], quote=True)
        body = '<p>%s</p>' % e(h['text'])
    return '<article class="s550-habit"><div class="s550-row"><span class="c530-type">%s</span><span class="s550-ev">%s</span></div>%s%s</article>' % (
        e(label), e(h['evidence']), body, actions)


def page(desk, prefix, shell, csrf):
    from .docrequest520 import folders
    o = style550.overview(desk)
    s = style550.settings(desk)
    f = folders(desk)
    total = o['files'] + o['mails']
    rate = ''
    try:
        from . import learning480
        t = learning480.dashboard(desk, months=1)['total']
        if t['total']:
            rate = '<p class="s550-big"><strong>%s %%</strong> des brouillons du mois envoyés sans retouche (%d rapproché(s) de vos envois).</p>' % (
                ('%g' % t['pct']['tel_quel']) if t['pct'].get('tel_quel') is not None else '—', t['total'])
    except Exception:
        rate = ''
    counts = ''.join('<li><strong>%d</strong> %s</li>' % (n, e(style550.TYPES.get(k, k))) for k, n in sorted(o['counts'].items(), key=lambda kv: -kv[1]))
    last = o['last']
    last_txt = ('Dernière analyse : %s — %d écrit(s), %d courriel(s).' % (e(str(last.get('at', ''))[:16].replace('T', ' ')), last.get('documents', 0), last.get('mails', 0))
                if last.get('at') else 'Pas encore d’analyse.')
    pending = ''.join(_habit_card(h, True) for h in o['pending'][:40])
    validated = ''.join(_habit_card(h, False) for h in o['validated'])
    skeletons = ''
    for t in style550.TYPES:
        row = desk.db.execute("SELECT skeleton FROM style550_docs WHERE doc_type=? AND skeleton<>'' ORDER BY analysed DESC LIMIT 1", (t,)).fetchone()
        if row:
            skeletons += '<details><summary>%s</summary><pre class="ia540-sent">%s</pre></details>' % (e(style550.TYPES[t]), e(row['skeleton']))
    body = ('<div class="s550"><h1>Mon style</h1><p class="ax-muted">L’agent apprend votre manière d’écrire à partir de vos écrits définitifs et de vos '
            'courriels envoyés. Il <strong>propose</strong> des habitudes ; vous les validez (en corrigeant le texte si besoin) ou les écartez. Une habitude '
            'validée s’applique aux brouillons de courriel et aux documents que l’agent prépare ; vous pouvez la retirer à tout moment.</p>'
            '<div class="s550-grid"><section class="ax-card"><h2>Corpus analysé</h2><p class="s550-big"><strong>%d</strong> écrit(s) : %d document(s) des dossiers, '
            '%d courriel(s) envoyé(s).</p>%s<p class="vf-note">%s</p>'
            '<div class="ax-actions"><form class="m5-form m5-inline" data-api="m550/scan" data-reload="1"><button class="ax-btn" type="submit">Analyser maintenant</button></form>'
            '<form class="m5-form m5-inline" data-api="m550/refine" data-reload="1" data-confirm="L’IA du cabinet reçoit seulement des statistiques et des plans types '
            '(pseudonymisés si l’IA est externe). Continuer ?"><button class="ax-btn ghost" type="submit">Affiner avec l’IA</button></form></div></section>'
            '<section class="ax-card"><h2>Mesure</h2>%s<p class="vf-note">Objectif : que vos corrections diminuent à mesure que les habitudes sont validées.</p>'
            '<p><a href="%s">Apprentissage détaillé (règles, corrections, tons)</a></p></section></div>'
            '<section class="ax-card"><h2>Habitudes à valider <span class="c530-count">%d</span></h2>%s</section>'
            '<section class="ax-card"><h2>Habitudes appliquées <span class="c530-count s550-ok">%d</span></h2>%s</section>'
            '<section class="ax-card"><h2>Plans types</h2><p class="vf-note">Intertitres et première phrase de chaque partie, données identifiantes retirées : '
            'l’agent s’en inspire pour structurer ses projets.</p>%s</section>'
            '<section class="ax-card"><h2>Réglages</h2><form class="m5-form" data-api="m550/settings" data-reload="1">'
            '<p class="vf-note">Sous-dossiers lus dans chaque dossier client (et utilisés pour ranger les documents produits).</p><div class="p5-grid">'
            '<label class="m5-field">Actes de procédure<input name="folder_procedure" value="%s"></label>'
            '<label class="m5-field">Correspondances<input name="folder_correspondance" value="%s"></label>'
            '<label class="m5-field">Projets (contrats, actes)<input name="folder_projets" value="%s"></label>'
            '<label class="m5-field">Livrables (notes, consultations)<input name="folder_livrables" value="%s"></label></div>'
            '<label class="m5-check"><input type="checkbox" name="mails"%s> Apprendre aussi de mes courriels envoyés</label>'
            '<label class="m5-check"><input type="checkbox" name="auto"%s> Analyse automatique une fois par jour (par petits lots)</label>'
            '<button class="ax-btn" type="submit">Enregistrer</button></form>'
            '<p class="vf-note">Lecture seule : rien n’est modifié dans vos dossiers. Les documents produits par AxiorHub sont exclus. Aucune phrase contenant un nom, '
            'un numéro ou une adresse n’est retenue comme formule.</p></section></div><script defer src="%s"></script>') % (
        total, o['files'], o['mails'], ('<ul class="s550-counts">%s</ul>' % counts) if counts else '', last_txt,
        rate or '<p class="c530-empty">Pas encore assez d’envois rapprochés de brouillons.</p>', e(prefix + '/progres'),
        len(o['pending']), pending or '<p class="c530-empty">Aucune habitude à valider. Lancez une analyse (au moins 3 écrits d’un même type).</p>',
        len(o['validated']), validated or '<p class="c530-empty">Aucune habitude validée pour l’instant.</p>',
        skeletons or '<p class="c530-empty">Disponibles après l’analyse.</p>',
        e(f['procedure'], quote=True), e(f['correspondance'], quote=True), e(f['projets'], quote=True), e(f['livrables'], quote=True),
        ' checked' if s['mails'] else '', ' checked' if s['auto'] else '', e(prefix + '/static/v550.js'))
    return shell('Mon style', body, prefix, csrf, '/mon-style')


def handle(desk, name, data, method='POST', args=None):
    n = name[len('m550/'):]
    if method == 'GET' and n == 'overview':
        o = style550.overview(desk)
        return {'files': o['files'], 'mails': o['mails'], 'pending': len(o['pending']), 'validated': len(o['validated'])}
    if method != 'POST':
        raise Stop('route_inconnue')
    if n == 'scan':
        job = desk.enqueue('style550_scan', {}, priority=0)
        return {'message': 'Analyse lancée (travail n° %d). Les habitudes proposées apparaîtront ici.' % job}
    if n == 'refine':
        job = desk.enqueue('style550_scan', {'refine': True}, priority=0)
        return {'message': 'Suggestions de l’IA demandées (travail n° %d).' % job}
    if n == 'habit':
        return style550.decide(desk, data)
    if n == 'settings':
        return style550.save_settings(desk, data)
    raise Stop('route_inconnue')
