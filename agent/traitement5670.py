"""5.6.70 : « Traiter avec l'IA » plusieurs courriels choisis (Boîte de réception, page Courriels, onglet Courriels du Dossier).

Demande du cabinet du 22 octobre 2026 : beaucoup de courriels sont écartés par l'agent (par exemple la demande d'un prospect pour la
relecture de conditions générales). L'avocat coche les courriels et demande leur traitement : pour chacun, une réponse est préparée
comme avec « Répondre avec l'IA » — dossier établi par l'agent s'il existe, sinon réponse « sans dossier » fondée sur le seul courriel
et ses pièces jointes (jamais un dossier deviné). Les réponses arrivent dans « À relire » ; rien n'est envoyé.
"""
from .common import Stop

MAX_ITEMS = 25


def _matter_of(desk, key):
    if not key:
        return ''
    try:
        from .desk import report_for
        matter = str(report_for(desk.c, key).get('matter') or '')
    except Stop:
        return ''
    from .common import load_matters
    return matter if any(str(m['id']) == matter for m in load_matters(desk.c, unfiltered=True)) else ''   # dossier réservé : détecté puis refusé


def traiter(desk, owner, items, instruction='', with_attachments=True):
    """items : [{'uid', 'dossier' (dossier IMAP), 'key'}] (Boîte) ou [{'key'}] (Dossier : courriel déjà examiné par l'agent)."""
    from .membres5650 import viewer_hidden
    if not isinstance(items, list) or not items:
        raise Stop('aucun_courriel_selectionne')
    if len(items) > MAX_ITEMS:
        raise Stop('vingt_cinq_courriels_maximum')
    hidden = viewer_hidden() or frozenset()
    done, errors = [], []
    from .mailbox import Mailbox
    box = Mailbox(desk.c['mail'])   # une seule connexion IMAP pour tout le lot
    try:
        _each(desk, owner, items, instruction, with_attachments, hidden, box, done, errors)
    finally:
        box.close()
    desk.audit('traitement5670_lot', {'demandes': len(items), 'lances': len(done), 'erreurs': len(errors), 'par': str(owner)[:80]})
    if not done:
        raise Stop(errors[0] if errors else 'aucun_courriel_selectionne')
    with_matter = sum(1 for d in done if d['dossier'])
    msg = ('%d courriel(s) confié(s) à l’agent : %d dans leur dossier, %d sans dossier (à partir du courriel seul). Les réponses arriveront dans '
           '« À relire » ; rien n’est envoyé.') % (len(done), with_matter, len(done) - with_matter)
    if errors:
        from .web440 import human
        msg += ' %d non traité(s) : %s.' % (len(errors), ', '.join(sorted({human(x) for x in errors}))[:300])
    return {'ok': True, 'lances': done, 'message': msg}


def _each(desk, owner, items, instruction, with_attachments, hidden, box, done, errors):
    from . import boite5625
    for raw in items:
        item = raw if isinstance(raw, dict) else {}
        key, uid, folder = str(item.get('key') or ''), str(item.get('uid') or ''), str(item.get('dossier') or '') or None
        if key and not uid:   # depuis le Dossier : le rapport de l'agent donne l'emplacement du courriel
            try:
                from .desk import report_for
                rep = report_for(desk.c, key)
                uid, folder = str(rep.get('source_uid') or ''), str(rep.get('source_mailbox') or '') or None
            except Stop:
                errors.append('courriel_introuvable')
                continue
        if not uid:
            errors.append('courriel_introuvable')
            continue
        matter = _matter_of(desk, key)
        if matter and matter in hidden:
            errors.append('dossier_reserve')
            continue
        try:
            out = boite5625.request_reply(desk, uid, matter, instruction, with_attachments, owner, box=box, folder=folder, without_matter=not matter)
            done.append({'uid': uid, 'job': out['job_id'], 'dossier': matter})
        except Stop as ex:
            errors.append(str(ex).split(':')[0])
