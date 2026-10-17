"""5.6.53 (audit 5.6.52, § 4) : page « Facturation et honoraires » et routes /api440/m5653/….

Un point d'entrée unique : recherche client ou dossier, bouton « Créer » (devis, lettre de mission, convention, facture, avoir), vues
« À préparer », « Documents », « Règlements », chiffrage des honoraires, réglages et modèles. L'éditeur montre l'aperçu à côté du
formulaire (dessous sur mobile) ; les paramètres XML restent masqués ; les contrôles parlent le langage de l'avocat. Les clés des
prestataires restent dans Paramètres › Connexions.
"""
import base64
from html import escape as e
import json
import re
from urllib.parse import urlencode

from .common import Stop, load_matters, matter_display
from . import facturation5653 as fact
from . import honoraires5653 as fees
from . import modeles5653 as models

READ = ('documents', 'document', 'apercu', 'resume', 'propositions', 'modeles', 'bibliotheque/modeles', 'bibliotheque/diligences', 'estimation/calculer')
SETTINGS = ('cabinet/enregistrer', 'honoraires/reglages', 'logo/importer', 'bibliotheque/importer')
WRITE = ('client/enregistrer', 'client/siren', 'estimation/enregistrer', 'estimation/retenir', 'estimation/refuser', 'document/preparer', 'document/modifier',
         'document/controler', 'document/approuver', 'document/emettre', 'document/deposer', 'document/etat', 'document/abandonner', 'document/avoir',
         'document/reglement', 'document/restaurer', 'document/transmettre', 'proposition/decider', 'proposition/preparer', 'modele/importer', 'modele/choisir', 'epingler',
         'dossier/perimetre')


def allowed(role, route):
    route = route.split('?', 1)[0]
    if role not in ('administrateur', 'avocat', 'assistant'):
        return False
    if route in READ:
        return True
    if route in SETTINGS or route in WRITE:
        return role in ('administrateur', 'avocat')             # un(e) assistant(e) avec le droit « facturation » passe par le registre des droits
    return False


# ------------------------------------------------------------------------------------------------ formulaire → données
def _lines_from_form(data, existing):
    by_id = {l['id']: l for l in existing}
    out = []
    keys = sorted({int(m.group(1)) for k in data for m in [re.fullmatch(r'ligne_(\d{1,3})_libelle', str(k))] if m})
    for i in keys:
        if data.get('ligne_%d_retirer' % i) in (True, 'true', 'on', '1'):
            continue
        label = str(data.get('ligne_%d_libelle' % i) or '').strip()
        if not label:
            continue
        lid = str(data.get('ligne_%d_id' % i) or '')
        prev = by_id.get(lid, {})
        out.append({'id': lid or 'L%d' % (i + 1), 'nature': data.get('ligne_%d_nature' % i) or prev.get('nature') or 'honoraires', 'libelle': label,
                    'quantite': str(data.get('ligne_%d_quantite' % i) or '1'), 'unite': data.get('ligne_%d_unite' % i) or prev.get('unite') or 'forfait',
                    'prix_unitaire_ht': str(data.get('ligne_%d_prix' % i) or '0'), 'periode': prev.get('periode', ''), 'sources': prev.get('sources') or []})
    return out


def _changes_from_form(desk, d, data):
    changes = {}
    if any(str(k).startswith('ligne_') for k in data):
        changes['lignes'] = _lines_from_form(data, d['snapshot']['lignes'])
    texts = {k[len('texte_'):]: v for k, v in data.items() if str(k).startswith('texte_') and re.fullmatch(r'texte_[a-z0-9_]{2,60}', str(k))}
    if texts:
        changes['textes'] = texts
    if 'regime_doc' in data or 'taux_doc' in data:
        choice = {}
        if data.get('regime_doc') not in (None, '', 'auto'):
            choice['regime'] = data['regime_doc']
        if data.get('taux_doc'):
            choice['taux'] = data['taux_doc']
        choice['confirme'] = data.get('confirme_doc') in (True, 'true', 'on', '1')
        for k in ('fondement', 'mention', 'categorie'):
            if data.get(k + '_doc'):
                choice[k] = data[k + '_doc']
        changes['choix_fiscal'] = choice
    dates = {k: data['date_' + k] for k in ('prestation', 'echeance', 'validite') if 'date_' + k in data}
    if dates:
        changes['dates'] = dates
    if 'profil_secret' in data:
        changes['profil_secret'] = data['profil_secret']
    if data.get('template_id'):
        changes['template_id'] = data['template_id']
    return changes


def _b64(data, name='contenu'):
    raw = str(data.get(name) or '')
    if ',' in raw[:80]:
        raw = raw.split(',', 1)[1]
    try:
        return base64.b64decode(raw, validate=True)
    except (ValueError, TypeError):
        raise Stop('fichier_illisible') from None


# ------------------------------------------------------------------------------------------------ API
def api(env, desk, auth, prefix, name, args, method):
    from .web440 import json_out
    from .web567 import actor
    from .web5634 import _post
    owner, role = actor(env)
    route = name[len('m5653/'):].split('?', 1)[0]
    if not allowed(role, route):
        raise Stop('role_insuffisant')
    if method == 'GET':
        if route == 'documents':
            return json_out({'documents': fact.documents(desk, str(args.get('dossier') or ''))})
        if route == 'document':
            return json_out(fact.get(desk, str(args.get('id') or '')))
        if route == 'apercu':
            return json_out(fact.preview(desk, owner, role, str(args.get('id') or '')))
        if route == 'resume':
            return json_out(fact.matter_summary(desk, str(args.get('dossier') or '')))
        if route == 'propositions':
            return json_out({'propositions': fact.suggestions(desk, refresh=False)})
        if route == 'modeles':
            return json_out({'modeles': [{k: v for k, v in t.items() if k not in ('file', 'markdown')} for t in models.listing(desk)]})
        if route == 'bibliotheque/modeles':
            return json_out({'modeles': fees.models(desk)})
        if route == 'bibliotheque/diligences':
            return json_out({'diligences': fees.diligences_for(desk, str(args.get('modele') or ''), str(args.get('niveau') or 'standard'))})
        raise Stop('methode_refusee')
    data = _post(env, auth, 8_000_000)
    return json_out(post(desk, owner, role, route, data, prefix))


def _estimate_inputs(desk, data):
    rows = []
    keys = sorted({int(m.group(1)) for k in data for m in [re.fullmatch(r'dil_(\d{1,3})_libelle', str(k))] if m})
    for i in keys:
        if not str(data.get('dil_%d_libelle' % i) or '').strip():
            continue
        rows.append({'libelle_client': data['dil_%d_libelle' % i], 'minutes': data.get('dil_%d_minutes' % i) or 0, 'statut': data.get('dil_%d_statut' % i) or 'inclus',
                     'type': data.get('dil_%d_type' % i) or 'autre', 'taux': data.get('dil_%d_taux' % i) or None, 'source': data.get('dil_%d_source' % i) or 'avocat',
                     'niveau': data.get('dil_%d_niveau' % i) or 'standard',
                     'niveaux': json.loads(data['dil_%d_niveaux' % i]) if data.get('dil_%d_niveaux' % i) else None})
    if data.get('modele') and not rows:
        rows = fees.diligences_for(desk, str(data['modele']), str(data.get('niveau') or 'standard'))
    return {'mode': data.get('mode') or 'forfait', 'taux_horaire': data.get('taux_horaire') or None, 'diligences': rows,
            'forfait': data.get('forfait') if str(data.get('forfait') or '').strip() else None, 'gratuite': data.get('gratuite'),
            'motif_gratuite': data.get('motif_gratuite') or '', 'provision_pct': data.get('provision_pct') or None}


def post(desk, owner, role, route, data, prefix=''):
    did = str(data.get('id') or '')
    if route == 'cabinet/enregistrer':
        fact.save_entity(desk, owner, role, data)
        return {'message': 'Fiche du cabinet enregistrée.'}
    if route == 'honoraires/reglages':
        fact._require(desk, owner, role, 'reglages')
        fees.save_settings(desk, data)
        return {'message': 'Réglages d’honoraires enregistrés.'}
    if route == 'logo/importer':
        fact._require(desk, owner, role, 'reglages')
        out = models.save_logo(desk, _b64(data))
        return {'message': 'Logo enregistré (%s, %d × %d).' % (out['type'].upper(), out['largeur'], out['hauteur'])}
    if route == 'bibliotheque/importer':
        fact._require(desk, owner, role, 'reglages')
        out = fees.import_library(desk, _b64(data))
        return {'message': 'Bibliothèque importée : %d diligences.' % out['diligences']}
    if route == 'client/enregistrer':
        mid = str(data.get('dossier') or '')
        fact.save_client(desk, owner, role, mid, data)
        changed = fact.recalculate_drafts(desk, owner, role, mid)   # 5.6.56 (§ 7.3) : brouillons du client recalculés, approbation périmée
        detail = ' ; '.join('%s : TVA %s → %s, TTC %s → %s' % (c['libelle'], _money_txt(c['tva_avant']), _money_txt(c['tva_apres']), _money_txt(c['ttc_avant']),
                                                               _money_txt(c['ttc_apres'])) for c in changed if c['change'])
        return {'message': ('Client enregistré. %d brouillon(s) recalculé(s)%s' % (len(changed), (' — ' + detail) if detail else '.')) if changed
                else 'Client enregistré.'}
    if route == 'client/siren':
        out = fact.lookup_siren(desk, owner, role, str(data.get('dossier') or ''), data.get('recherche'))
        rows = ' ; '.join(' — '.join(str(r.get(k)) for k in ('siren', 'nom', 'siege') if r.get(k)) for r in out['resultats'][:3])
        # 5.6.56 (§ 7.2) : candidats rendus au formulaire (« Utiliser cette entreprise » remplit la fiche ; l'enregistrement confirme)
        return {**out, 'noreload': True, 'candidats': [{k: r.get(k) for k in ('siren', 'siret', 'nom', 'siege', 'adresse_ligne1', 'code_postal', 'ville', 'etat')}
                                                       for r in out['resultats'][:5]],
                'message': ('%s (%s) : %s. %s' % (out['source'], out['date'][:10], rows or 'aucun résultat', out['note']))[:700]}
    if route == 'estimation/calculer':
        r = fees.estimate(desk, _estimate_inputs(desk, data))
        return {'resultat': fees.client_view(r), 'interne': r['interne'], 'noreload': True,
                'message': ' '.join(r['explication'] + r['avertissements'])[:900]}
    if route == 'estimation/enregistrer':
        fact._require(desk, owner, role, 'facturation')
        fact._matter(desk, str(data.get('dossier') or ''))
        est = fees.save_estimate(desk, owner, str(data['dossier']), _estimate_inputs(desk, data))
        return {'estimation': est['id'], 'goto': '?' + urlencode({'vue': 'honoraires', 'dossier': data['dossier'], 'estimation': est['id']}),
                'message': 'Estimation enregistrée (montant proposé : %s HT).' % fees.fmt(est['proposed_cents'])}
    if route == 'estimation/retenir':
        fact._require(desk, owner, role, 'facturation')
        est = fees.get_estimate(desk, did)
        fact._matter(desk, est['matter'])
        est = fees.retain(desk, owner, did, data.get('montant'), data.get('version') or 0)
        return {'message': 'Montant retenu : %s HT.' % fees.fmt(est['retained_cents'])}
    if route == 'estimation/refuser':
        fact._require(desk, owner, role, 'facturation')
        fact._matter(desk, fees.get_estimate(desk, did)['matter'])
        fees.refuse(desk, owner, did, data.get('version') or 0)
        return {'message': 'Estimation écartée.'}
    if route == 'document/preparer':
        d = fact.prepare(desk, owner, role, str(data.get('type') or ''), str(data.get('dossier') or data.get('matter') or ''), data,
                         str(data.get('request_key') or ''))
        return {'document': d['id'], 'goto': '?' + urlencode({'document': d['id']}), 'message': '%s préparé(e) en brouillon.' % d['kind_label']}
    if route == 'document/modifier':
        d = fact.get(desk, did)
        d = fact.update(desk, owner, role, did, _changes_from_form(desk, d, data), data.get('version') or 0)
        return {'message': 'Brouillon enregistré et recalculé (version %d).' % d['version']}
    if route == 'document/controler':
        r = fact.check(desk, owner, role, did)
        return {'controles': r, 'message': 'Aucun point bloquant.' if r['conforme'] else '%d point(s) bloquant(s).' % len(r['bloquants'])}
    if route == 'document/approuver':
        d = fact.approve(desk, owner, role, did, data.get('version') or 0, str(data.get('empreinte') or ''))
        return {'message': 'Version %d approuvée%s.' % (d['version'], (' — référence ' + d['number']) if d['number'] else '')}
    if route == 'document/emettre':
        d = fact.issue(desk, owner, role, did, str(data.get('cle') or ''))
        return {'message': '%s %s émise le %s%s.' % (d['kind_label'], d['number'], d['issue_date'],
                                                     '' if d['files_state'] == 'publies' else ' — fichiers à publier (nouvel essai possible)')}
    if route == 'document/deposer':
        d = fact.export_draft(desk, owner, role, did)
        return {'message': 'Document déposé dans FACTURATION.' if d['files_state'] == 'publies' else 'Dépôt incomplet : nouvel essai possible.'}
    if route == 'document/etat':
        d = fact.mark_engagement(desk, owner, role, did, str(data.get('etat') or ''), data.get('version') or 0, str(data.get('preuve') or ''))
        return {'message': 'État : %s.' % d['state_label']}
    if route == 'document/abandonner':
        fact.abandon(desk, owner, role, did, data.get('version') or 0)
        return {'goto': '?vue=documents', 'message': 'Brouillon abandonné (conservé dans l’historique).'}
    if route == 'document/avoir':
        d = fact.credit_note(desk, owner, role, did, str(data.get('motif') or ''))
        return {'document': d['id'], 'goto': '?' + urlencode({'document': d['id']}), 'message': 'Avoir préparé en brouillon.'}
    if route == 'document/reglement':
        d = fact.record_payment(desk, owner, role, did, data.get('montant'), str(data.get('date') or ''), str(data.get('moyen') or 'virement'),
                                str(data.get('reference') or ''), str(data.get('request_key') or ''))
        return {'message': 'Règlement enregistré : %s.' % d['payment_label']}
    if route == 'document/restaurer':                          # 5.6.56 (N24) : copie de l'original émis, rien n'est écrasé
        d = fact.restore_copy(desk, owner, role, did)
        return {'message': 'Original émis redéposé en copie dans FACTURATION.' if d['files_state'] == 'publies' else 'Restauration incomplète.'}
    if route == 'document/transmettre':
        out = fact.submit(desk, owner, role, did)
        return {'goto_url': prefix + '/operation?id=' + out['operation'], 'operation': out['operation'],
                'message': 'Transmission préparée : relisez la fiche d’opération avant de la valider.'}
    if route == 'proposition/decider':
        fact.decide_suggestion(desk, owner, role, str(data.get('cle') or ''), str(data.get('decision') or ''), str(data.get('jusqu_au') or ''))
        return {'message': 'Proposition mise à jour.'}
    if route == 'proposition/preparer':
        row = desk.db.execute('SELECT * FROM b53_suggestions WHERE key=?', (str(data.get('cle') or ''),)).fetchone()
        if not row:
            raise Stop('element_absent')
        kind, sub = {'sans_convention': ('convention', ''), 'temps_a_facturer': ('facture', 'temps'), 'provision_prevue': ('facture', 'provision'),
                     'budget_proche': ('devis', '')}[row['kind']]
        d = fact.prepare(desk, owner, role, kind, row['matter'], {'sous_type': sub or 'libre', 'mission': ''}, str(data.get('request_key') or ''))
        fact.decide_suggestion(desk, owner, role, row['key'], 'traitee')
        desk.db.execute('UPDATE b53_suggestions SET document=? WHERE key=?', (d['id'], row['key']))
        desk.db.commit()
        return {'document': d['id'], 'goto': '?' + urlencode({'document': d['id']}), 'message': '%s préparé(e) : relisez le brouillon.' % d['kind_label']}
    if route == 'modele/importer':
        fact._require(desk, owner, role, 'facturation')
        t = models.import_template(desk, owner, str(data.get('type') or ''), str(data.get('titre') or ''), _b64(data), str(data.get('nom') or ''))
        return {'message': 'Modèle importé : %d variable(s) détectée(s).' % len(t['variables'])}
    if route == 'modele/choisir':
        fact._require(desk, owner, role, 'facturation')
        models.choose(desk, str(data.get('type') or ''), str(data.get('modele') or ''))
        return {'message': 'Modèle choisi par défaut.'}
    if route == 'dossier/perimetre':                       # 5.6.55 : « Pas un dossier client » / « Réintégrer »
        from . import facturation5655 as f55
        decision = str(data.get('decision') or '')
        f55.set_scope(desk, owner, role, str(data.get('dossier') or ''), decision)
        return {'message': 'Dossier écarté de la facturation (réintégrable dans l’onglet Dossiers).' if decision == 'exclure' else 'Dossier réintégré.'}
    if route == 'epingler':
        pins = dict(desk.settings('facturation5653:epingle', {}) or {})
        pins[owner] = data.get('epingler') in (True, 'true', 'on', '1')
        desk.setting('facturation5653:epingle', pins)
        return {'message': 'Menu principal mis à jour.'}
    raise Stop('route_inconnue')


def pinned(desk, owner):
    return bool((desk.settings('facturation5653:epingle', {}) or {}).get(owner))


# ------------------------------------------------------------------------------------------------ page
def _fr_stamp(iso):
    """Horodatage UTC affiché à l'heure du cabinet (jj/mm/aaaa hh:mm)."""
    from datetime import datetime
    try:
        from zoneinfo import ZoneInfo
        dt = datetime.fromisoformat(iso).astimezone(ZoneInfo('Europe/Paris'))
    except Exception:   # noqa: BLE001
        return str(iso)[:16].replace('T', ' ')
    return dt.strftime('%d/%m/%Y %H:%M')


def _money(c):
    return fees.fmt(c) if c is not None else '—'


def _form(api, body, button, reload=True, extra='', cls='', confirm=''):
    return ('<form class="m5-form b53-form %s" data-api="m5653/%s"%s%s>%s<div class="b53-actions"><button class="ax-btn" type="submit">%s</button>%s</div>'
            '<p role="status" class="m5-status"></p></form>') % (cls, api, ' data-reload' if reload else '', (' data-confirm="%s"' % e(confirm, quote=True)) if confirm else '',
                                                               body, e(button), extra)


def _money_txt(cents):
    return fees.fmt(int(cents or 0))


def _hidden(**kw):
    return ''.join('<input type="hidden" name="%s" value="%s">' % (e(k), e(str(v), quote=True)) for k, v in kw.items())


def _matter_options(desk, selected=''):
    from .membres5650 import viewer_hidden
    hidden = viewer_hidden() or frozenset()
    opts = ''.join('<option value="%s"%s>%s%s</option>' % (e(str(m['id']), quote=True), ' selected' if str(m['id']) == str(selected) else '', e(matter_display(m)),
                                                         (' — ' + e(m.get('client_name') or '')) if m.get('client_name') else '')
                   for m in load_matters(desk.c) if str(m['id']) not in hidden)
    return '<option value="">Choisir un dossier…</option>' + opts


VIEWS = (('tableau', 'Tableau de bord'), ('documents', 'Documents'), ('dossiers', 'Dossiers'), ('honoraires', 'Chiffrer une mission'),
         ('reglages', 'Réglages et modèles'))
OLD_VIEWS = {'a-preparer': 'tableau', 'reglements': 'documents'}
TYPE_LABELS = {'echue': 'Échue', 'a_emettre': 'À émettre', 'a_envoyer': 'À envoyer', 'brouillon': 'Brouillon', 'temps_a_facturer': 'Proposition',
               'provision_prevue': 'Proposition', 'budget_proche': 'Alerte', 'sans_convention': 'Proposition'}
DOC_FILTERS = (('', 'Tous'), ('brouillons', 'Brouillons'), ('a-emettre', 'À émettre'), ('impayees', 'Impayées'), ('emises', 'Émises'),
               ('engagements', 'Devis et conventions'))
NEW_TYPES = (('facture', 'Facture', 'Honoraires, temps passé, provision ou solde'), ('devis', 'Devis', 'Proposition chiffrée avant accord'),
             ('convention', 'Convention d’honoraires', 'Forfait ou temps passé, à faire signer'), ('lettre_mission', 'Lettre de mission', 'Mission et conditions'))


def _client_options(desk, selected=''):
    """Dossiers CLIENTS seulement (pas les dossiers internes du cabinet), les plus actifs en premier ; recherche ajoutée par v5647.js."""
    from . import facturation5655 as f55
    try:
        recent = {r[0]: r[1] for r in desk.db.execute('SELECT matter,last_external_activity FROM matter_portfolio')}
    except Exception:   # noqa: BLE001
        recent = {}
    rows = sorted(f55.client_matters(desk), key=lambda m: (recent.get(str(m['id'])) or '', matter_display(m)), reverse=True)
    return '<option value="">Choisir un dossier client…</option>' + ''.join(
        '<option value="%s"%s>%s</option>' % (e(str(m['id']), quote=True), ' selected' if str(m['id']) == str(selected) else '', e(matter_display(m))) for m in rows)


def page(desk, auth, prefix, env, args):
    from .web567 import actor
    owner, role = actor(env)
    view = OLD_VIEWS.get(str(args.get('vue') or ''), str(args.get('vue') or 'tableau'))
    if str(args.get('vue') or '') == 'reglements' and not args.get('filtre'):
        args = {**args, 'filtre': 'impayees'}
    head = _header(desk, prefix, owner, args)
    if args.get('document'):
        try:
            body = document_html(desk, prefix, owner, role, str(args['document']))
        except Stop as ex:
            body = '<p class="b53-error">%s</p></div>' % e(_message(str(ex).split(':')[0]))
        return head + body
    tabs = ''.join('<a href="%s"%s>%s</a>' % (e('?' + urlencode({'vue': k}), quote=True), ' aria-current="page"' if k == view else '', e(v)) for k, v in VIEWS)
    head += '<nav class="b53-tabs" aria-label="Vues de la facturation">%s</nav>' % tabs
    try:
        if view == 'documents':
            body = documents_html(desk, prefix, args)
        elif view == 'dossiers':
            body = matters_html(desk, prefix, args)
        elif view == 'honoraires':
            body = estimate_html(desk, prefix, owner, role, args)
        elif view == 'reglages':
            body = settings_html(desk, prefix, owner, role)
        else:
            body = dashboard_html(desk, prefix)
    except Stop as ex:
        body = '<p class="b53-error">%s</p></div>' % e(_message(str(ex).split(':')[0]))
    return head + body


def _message(code):
    from .erreurs5634 import explain
    return explain(code)


def _header(desk, prefix, owner, args):
    from . import facturation5655 as f55
    types = ''.join('<label class="b55-type"><input type="radio" name="type" value="%s"%s required><span><strong>%s</strong><small>%s</small></span></label>' % (
        k, ' checked' if k == 'facture' else '', e(label), e(desc)) for k, label, desc in NEW_TYPES)
    create = _form('document/preparer', '<input type="hidden" name="request_key" data-r52-key value=""><fieldset class="b55-types"><legend>Type de document</legend>%s'
                   '</fieldset><label>Dossier<select name="matter" required>%s</select></label><label>Pour une facture<select name="sous_type">%s</select></label>' % (
                       types, _client_options(desk, args.get('dossier') or ''),
                       ''.join('<option value="%s">%s</option>' % (k, e(v)) for k, v in fact.INVOICE_TYPES.items())),
                   'Préparer le brouillon', reload=True)
    pin = _form('epingler', _hidden(epingler='false' if pinned(desk, owner) else 'true'),
                'Retirer du menu principal' if pinned(desk, owner) else 'Épingler au menu principal', cls='b53-inline ghost')
    missing = f55.setup_missing(desk)
    setup = ('<div class="b55-setup" role="status"><strong>Avant d’émettre une facture :</strong> complétez la fiche du cabinet (%s). '
             '<a class="ax-btn" href="?vue=reglages#b55-cabinet">Compléter la fiche</a></div>') % e(', '.join(missing)) if missing else ''
    return ('<div class="b53"><header class="b55-head"><div><h1>Facturation et honoraires</h1><p class="b53-note">Préparer, émettre et suivre les honoraires '
            'de vos dossiers clients. Aucun envoi, aucune signature ni émission sans votre validation.</p></div><div class="b55-head-actions">'
            '<details class="b53-create b55-new"><summary class="ax-btn">Nouveau document</summary>%s</details>%s</div></header>%s') % (create, pin, setup)


def _row_action(prefix, item):
    if item.get('proposition'):
        main = _form('proposition/preparer', _hidden(cle=item['proposition']) + '<input type="hidden" name="request_key" data-r52-key value="">', item['action'],
                     reload=True, cls='b53-inline')
        more = ('<details class="b55-more"><summary aria-label="Autres choix">…</summary><div>%s%s%s</div></details>') % (
            _form('proposition/decider', _hidden(cle=item['proposition'], decision='reportee'), 'Reporter d’une semaine', cls='b53-inline ghost'),
            _form('proposition/decider', _hidden(cle=item['proposition'], decision='refusee'), 'Ne plus proposer', cls='b53-inline ghost'),
            _form('dossier/perimetre', _hidden(dossier=item['matter'], decision='exclure'), 'Pas un dossier client', cls='b53-inline ghost'))
        return main + more
    return '<a class="ax-btn" href="%s">%s</a>' % (e('?' + urlencode({'document': item['document']}), quote=True), e(item['action']))


def _todo_row(prefix, item):
    anchor = (' id="prop-%s"' % e(item['proposition'], quote=True)) if item.get('proposition') else ''   # lien depuis « Aujourd'hui »
    return ('<li class="b55-row b55-%s"%s><span class="b55-badge">%s</span><div class="b55-main"><a class="b55-matter" href="%s">%s</a>'
            '<span class="b55-what">%s</span><span class="b53-note">%s</span></div><div class="b55-side"><span class="b55-amount">%s</span>%s</div></li>') % (
        e(item['type']), anchor, e(TYPE_LABELS.get(item['type'], '')), e(prefix + '/dossier?' + urlencode({'id': item['matter'], 'vue': 'travaux'}), quote=True),
        e(item['matter_name']), e(item['titre']), e(item.get('detail') or ''), _money(item.get('montant_cents')) if item.get('montant_cents') else '',
        _row_action(prefix, item))


def dashboard_html(desk, prefix):
    from . import facturation5655 as f55
    board = f55.dashboard(desk)
    items = f55.todo(desk)
    kpis = (('?vue=dossiers', 'À facturer', _money(board['a_facturer_cents']), 'temps validés non facturés'),
            ('?vue=documents&filtre=impayees', 'Impayés', _money(board['impayes_cents']),
             '%d facture%s, dont %d échue%s' % (board['impayes_nombre'], 's' if board['impayes_nombre'] > 1 else '', board['echus_nombre'],
                                                 's' if board['echus_nombre'] > 1 else '')),
            ('?vue=documents&filtre=brouillons', 'Brouillons en cours', str(board['brouillons']), 'à terminer ou à émettre'),
            ('?vue=documents&filtre=emises', 'Encaissé ce mois', _money(board['encaisse_mois_cents']), 'règlements enregistrés'))
    cards = ''.join('<a class="b55-kpi" href="%s"><span>%s</span><strong>%s</strong><small>%s</small></a>' % (e(h, quote=True), e(t), e(v), e(s)) for h, t, v, s in kpis)
    first, rest = items[:10], items[10:]
    todo = ''.join(_todo_row(prefix, i) for i in first)
    if rest:
        todo += '<li class="b55-rest"><details><summary>Afficher les %d autres</summary><ul class="b55-list">%s</ul></details></li>' % (
            len(rest), ''.join(_todo_row(prefix, i) for i in rest))
    recent = fact.documents(desk, limit=5)
    rec = ''.join('<li><a href="%s">%s %s</a> — %s · %s · %s</li>' % (e('?' + urlencode({'document': d['id']}), quote=True), e(d['kind_label']),
                                                                      e(d['number'] or '(brouillon)'), e(d['matter_name']), e(d['state_label']), _money(d['ttc_cents']))
                  for d in recent)
    partial = ('<p class="b53-warn">Lecture partielle (%d dossier%s) : montants à confirmer.</p>' % (len(board['lecture_partielle']),
                                                                                                   's' if len(board['lecture_partielle']) > 1 else '')
               if board['lecture_partielle'] else '')
    return ('<section class="b55-kpis" aria-label="Chiffres clés">%s</section>%s<section class="b55-section"><h2>À faire</h2>%s</section>'
            '<section class="b55-section"><h2>Derniers documents</h2><ul class="b53-list">%s</ul><p><a href="?vue=documents">Tous les documents</a></p></section>'
            '<p class="b53-note">Calculé le %s. Seuls les dossiers clients sont pris en compte ; les dossiers internes du cabinet sont écartés '
            '(<a href="?vue=dossiers#b55-ecartes">voir les dossiers écartés</a>).</p></div>') % (
        cards, partial, ('<ul class="b55-list">%s</ul>' % todo) if todo else '<p class="b53-empty">Rien à faire pour l’instant.</p>',
        rec or '<li>Aucun document.</li>', e(_fr_stamp(board['calcule_le'])))


def _doc_filter(d, f):
    if f == 'brouillons':
        return d['state'] in ('brouillon', 'a_controler')
    if f == 'a-emettre':
        return d['state'] == 'approuve' and d['kind'] in ('facture', 'avoir') and not d['number']
    if f == 'impayees':
        return d['kind'] == 'facture' and bool(d['number']) and d['payment_state'] in ('impayee', 'partielle')
    if f == 'emises':
        return d['kind'] in ('facture', 'avoir') and bool(d['number'])
    if f == 'engagements':
        return d['kind'] in ('devis', 'convention', 'lettre_mission')
    return True


def documents_html(desk, prefix, args):
    q = str(args.get('q') or '').strip().lower()
    f = str(args.get('filtre') or '')
    chips = ''.join('<a href="%s"%s>%s</a>' % (e('?' + urlencode({'vue': 'documents', 'filtre': k, 'q': args.get('q') or ''}), quote=True),
                                               ' aria-current="true"' if k == f else '', e(v)) for k, v in DOC_FILTERS)
    rows = [d for d in fact.documents(desk) if _doc_filter(d, f) and (not q or q in (d['matter'] + ' ' + d['matter_name'] + ' ' + d['client'] + ' ' + d['number']).lower())]
    body = ''.join('<tr><td><a href="%s">%s %s</a></td><td><strong>%s</strong></td><td>%s</td><td class="b55-num">%s</td><td>%s</td><td>%s</td></tr>' % (
        e('?' + urlencode({'document': d['id']}), quote=True), e(d['kind_label']), e(d['number'] or '(brouillon)'), e(d['matter_name']), e(d['state_label']),
        _money(d['ttc_cents']), e(d['payment_label'] if d['kind'] == 'facture' and d['number'] else ''), e(d['issue_date'] or d['updated'][:10])) for d in rows)
    return ('<div class="b55-filters"><nav class="cx52-families" aria-label="Filtres">%s</nav><form class="b55-search" method="get"><input type="hidden" name="vue" '
            'value="documents"><input type="hidden" name="filtre" value="%s"><label>Rechercher un client, un dossier ou un numéro<input name="q" value="%s" maxlength="80">'
            '</label></form></div><div class="b53-table-wrap"><table class="b53-table"><thead><tr><th>Document</th><th>Dossier</th><th>État</th><th>TTC</th>'
            '<th>Règlement</th><th>Date</th></tr></thead><tbody>%s</tbody></table></div></div>') % (
        chips, e(f, quote=True), e(args.get('q') or '', quote=True), body or '<tr><td colspan="6">Aucun document.</td></tr>')


def matters_html(desk, prefix, args):
    from . import facturation5655 as f55
    rows = f55.overview(desk, args.get('q') or '')
    body = ''
    for r in rows:
        conv = r['convention']
        sub = 'temps' if r['a_facturer_cents'] else 'libre'
        body += ('<tr><td><a class="b55-matter" href="%s">%s</a></td><td>%s</td><td class="b55-num">%s</td><td class="b55-num">%s</td><td class="b55-num">%s</td>'
                 '<td class="b55-actions">%s<a class="ax-btn ghost" href="%s">Documents</a></td></tr>') % (
            e(prefix + '/dossier?' + urlencode({'id': r['id'], 'vue': 'travaux'}), quote=True), e(r['nom']),
            e(('%s (%s)' % (conv['numero'] or 'sans référence', conv['etat'])) if conv else 'aucune'), _money(r['a_facturer_cents']),
            _money(r['facture_ht_cents']), _money(r['solde_cents']),
            _form('document/preparer', _hidden(type='facture', matter=r['id'], sous_type=sub) + '<input type="hidden" name="request_key" data-r52-key value="">',
                  'Facturer', reload=True, cls='b53-inline'), e('?' + urlencode({'vue': 'documents', 'q': r['id']}), quote=True))
    out = ex = ''
    for x in f55.excluded(desk):
        ex += '<li><strong>%s</strong> — %s %s</li>' % (e(x['nom']), e(x['motif']), _form('dossier/perimetre', _hidden(dossier=x['id'], decision='inclure'),
                                                                                    'Réintégrer', cls='b53-inline ghost'))
    out = ('<form class="b55-search" method="get"><input type="hidden" name="vue" value="dossiers"><label>Rechercher un dossier client<input name="q" value="%s" '
           'maxlength="80"></label></form><div class="b53-table-wrap"><table class="b53-table"><thead><tr><th>Dossier client</th><th>Convention</th><th>À facturer</th>'
           '<th>Facturé HT</th><th>Solde</th><th></th></tr></thead><tbody>%s</tbody></table></div><details class="b55-section" id="b55-ecartes"><summary>Dossiers '
           'écartés de la facturation (%d)</summary><p class="b53-note">Dossiers internes du cabinet reconnus par leur nom, dossiers archivés et dossiers que vous avez '
           'exclus. Réintégrez un dossier client écarté par erreur.</p><ul class="b53-list">%s</ul></details></div>') % (
        e(args.get('q') or '', quote=True), body or '<tr><td colspan="6">Aucun dossier client.</td></tr>', ex.count('<li>'), ex or '<li>Aucun.</li>')
    return out


def estimate_html(desk, prefix, owner, role, args):
    mid = str(args.get('dossier') or '')
    cfg = fees.settings(desk)
    lib = ''.join('<option value="%s">%s — %s (%d diligences)</option>' % (e(m['modele'], quote=True), e(m['categorie']), e(m['modele']), m['diligences'])
                  for m in fees.models(desk))
    est_html = ''
    if mid:
        try:
            ests = fees.estimates_for(desk, mid)
            fact._matter(desk, mid)
        except Stop:
            ests = []
        for est in ests[:5]:
            r = est['result']
            est_html += ('<article class="b53-card"><h3>%s HT proposés · %s</h3><p>%s</p><p class="b53-note">Fourchette %s à %s · provision %s %% : %s · interne : coût %s, '
                         'taux effectif %s €/h</p>%s<div class="b53-actions">%s%s</div></article>') % (
                _money(est['proposed_cents']), e(fees.ESTIMATE_STATES.get(est['state'], est['state'])), e(' '.join(r['explication'])), _money(r['bas_ht_cents']),
                _money(r['haut_ht_cents']), e(r['provision_pct']), _money(r['provision_ht_cents']), _money(r['interne']['cout_previsible_cents']),
                e(r['interne']['taux_effectif']), ''.join('<p class="b53-warn">%s</p>' % e(w) for w in r['avertissements']),
                _form('estimation/retenir', _hidden(id=est['id'], version=est['version']) + '<label>Montant retenu (HT)<input name="montant" value="%s" inputmode="decimal" required></label>'
                      % e(str(fees.euros(est['retained_cents'] if est['retained_cents'] is not None else est['proposed_cents'])), quote=True), 'Retenir ce montant', cls='b53-inline'),
                _form('document/preparer', _hidden(type='convention', dossier=mid, estimation=est['id'], mode=r['mode']) +
                      '<input type="hidden" name="request_key" data-r52-key value=""><label>Mission<input name="mission" maxlength="300"></label>', 'Préparer la convention',
                      reload=True, cls='b53-inline') if est['state'] in ('retenue', 'signee') else '')
    form = ('<label>Dossier<select name="dossier" required>%s</select></label><label>Modèle de dossier (bibliothèque Honorium du cabinet)<select name="modele">'
            '<option value="">Diligences saisies ci-dessous</option>%s</select></label><label>Niveau<select name="niveau">%s</select></label>'
            '<label>Mode<select name="mode">%s</select></label><label>Taux horaire HT<input name="taux_horaire" value="%s" inputmode="decimal"></label>'
            '<label>Forfait HT (vide = à proposer)<input name="forfait" inputmode="decimal"></label><label class="r52-day"><input type="checkbox" name="gratuite"> Mission gratuite '
            'ou remise totale</label><label>Motif de la gratuité<input name="motif_gratuite" maxlength="200"></label><label>Provision (%%)<input name="provision_pct" value="%s" '
            'inputmode="decimal"></label><fieldset><legend>Diligences ajoutées (facultatif)</legend>%s</fieldset>') % (
        _matter_options(desk, mid), lib, ''.join('<option value="%s"%s>%s</option>' % (k, ' selected' if k == 'standard' else '', e(v)) for k, v in fees.LEVEL_LABELS.items()),
        ''.join('<option value="%s">%s</option>' % (k, e(v)) for k, v in fees.MODES.items()), e(cfg['taux_horaire'], quote=True), e(cfg['provision_pct'], quote=True),
        ''.join('<div class="b53-row"><input name="dil_%d_libelle" placeholder="Diligence (libellé client)" maxlength="200"><input name="dil_%d_minutes" placeholder="Minutes" '
                'inputmode="numeric"><select name="dil_%d_statut">%s</select></div>' % (i, i, i, ''.join('<option value="%s">%s</option>' % (k, e(v)) for k, v in fees.STATUSES.items()))
                for i in range(4)))
    return ('<p class="b53-note">Durées et coefficients : hypothèses du cabinet (bibliothèque Honorium), pas un barème de la profession. Le serveur calcule ; '
            'le montant PROPOSÉ se recalcule, le montant RETENU est votre décision, le montant SIGNÉ est figé par la convention.</p>%s%s</div>') % (
        _form('estimation/enregistrer', form, 'Calculer et enregistrer l’estimation', reload=True, extra='<button class="ax-btn ghost" type="button" data-b53-calc>Calculer sans enregistrer</button>'),
        est_html)


def settings_html(desk, prefix, owner, role):
    ent = fact.entity(desk)
    ident, fis, terms = ent['identite'], ent['fiscal'], ent['conditions']
    field = lambda name, label, value='', kind='text': '<label>%s<input name="%s" type="%s" value="%s" maxlength="200"></label>' % (e(label), e(name), kind, e(str(value or ''), quote=True))
    cab = ''.join(field(k, l, ident.get(k)) for k, l in (('nom', 'Nom du cabinet ou de l’avocat'), ('barreau', 'Barreau'), ('adresse_ligne1', 'Adresse'),
                                                          ('code_postal', 'Code postal'), ('ville', 'Ville'), ('siren', 'SIREN'), ('siret', 'SIRET'), ('tva', 'N° de TVA'),
                                                          ('email', 'Courriel'), ('telephone', 'Téléphone'), ('iban', 'IBAN'), ('beneficiaire', 'Bénéficiaire du virement')))
    cab += ('<label>Régime de TVA du cabinet<select name="regime_cabinet"><option value="">À qualifier</option><option value="normal"%s>TVA française</option>'
            '<option value="franchise"%s>Franchise en base (article 293 B), choisie</option></select></label>%s<label>Taux par défaut<select name="taux_defaut">%s</select></label>'
            '<label class="r52-day"><input type="checkbox" name="tva_sur_debits"%s> Option pour la TVA d’après les débits</label>'
            '<label>Catégorie du cabinet<select name="categorie_entreprise">%s</select></label>') % (
        ' selected' if fis.get('regime_cabinet') == 'normal' else '', ' selected' if fis.get('regime_cabinet') == 'franchise' else '',
        field('justification_franchise', 'Justification de la franchise (seuils, date de vérification)', fis.get('justification_franchise')),
        ''.join('<option%s>%s</option>' % (' selected' if str(fis.get('taux_defaut') or '20') == r else '', r) for r in ('20', '10', '5.5', '2.1', '8.5')),
        ' checked' if fis.get('tva_sur_debits') else '',
        ''.join('<option value="%s"%s>%s</option>' % (k, ' selected' if fis.get('categorie_entreprise') == k else '', v) for k, v in (
            ('', 'À indiquer'), ('micro', 'Micro-entreprise'), ('pme', 'PME'), ('eti', 'ETI'), ('ge', 'Grande entreprise'))))
    cab += field('delai_paiement_jours', 'Délai de paiement (jours, 60 au plus)', terms.get('delai_paiement_jours', 30)) + field('modalites', 'Modalités de règlement', terms.get('modalites'))
    cab += field('escompte', 'Escompte', terms.get('escompte'))
    cab += '<label>Outil qui numérote les factures<select name="autorite">%s</select></label><label>Trajet de facturation électronique<select name="transport">%s</select></label>' % (
        ''.join('<option value="%s"%s>%s</option>' % (k, ' selected' if ent['autorite'] == k else '', e(v)) for k, v in fact.AUTHORITIES.items()),
        ''.join('<option value="%s"%s>%s%s</option>' % (k, ' selected' if ent['transport'] == k else '', e(v), '' if k in fact.QUALIFIED_TRANSPORTS else ' — à qualifier')
                for k, v in fact.TRANSPORTS.items()))
    cab += _hidden(version=ent['version'])
    cfg = fees.settings(desk)
    eco = cfg['economie']
    fee_form = ''.join(field(k, l, v) for k, l, v in (('taux_horaire', 'Taux horaire par défaut (HT)', cfg['taux_horaire']),
                                                      ('charges_structure', 'Charges de structure annuelles', eco['charges_structure']),
                                                      ('revenu_net', 'Revenu net annuel visé', eco['revenu_net']), ('cotisations', 'Cotisations annuelles', eco['cotisations']),
                                                      ('heures_facturables', 'Heures facturables par an', eco['heures_facturables']),
                                                      ('majoration_pct', 'Majoration sur le coût (%)', eco['majoration_pct']), ('provision_pct', 'Provision par défaut (%)', cfg['provision_pct'])))
    econ = fees.economics(cfg)
    tpl_rows = ''.join('<li><strong>%s</strong> — %s · version %s · %s%s</li>' % (e(models.KINDS.get(t['kind'], t['kind'])), e(t['title']), e(t['version']),
                                                                                  e('modèle de base' if t['origin'] == 'defaut' else 'importé par ' + t['author']),
                                                                                  _form('modele/choisir', _hidden(type=t['kind'], modele=t['id']), 'Utiliser par défaut',
                                                                                        cls='b53-inline ghost'))
                       for t in models.listing(desk))
    upload = ('<label>Type<select name="type">%s</select></label><label>Titre<input name="titre" maxlength="120"></label><label>Fichier Word (.docx)<input type="file" accept=".docx" '
              'data-b53-file required></label>') % ''.join('<option value="%s">%s</option>' % (k, e(v)) for k, v in models.KINDS.items())
    logo = '<label>Logo (PNG ou JPEG, 1 Mo au plus)<input type="file" accept="image/png,image/jpeg" data-b53-file required></label>'
    return ('<section class="b53-section" id="b55-cabinet"><h2>Cabinet émetteur et fiscalité</h2><p class="b53-note">%s</p><p class="b53-note">Un taux de 0 %% ne vaut jamais franchise : le régime est choisi ici et '
            'justifié. Ces réglages relèvent du droit « Réglages ».</p>%s</section><section class="b53-section"><h2>Honoraires (moteur Honorium)</h2><p class="b53-note">'
            'Coût de revient : %s €/h ; taux cible : %s €/h (majoration sur le coût, pas une marge sur le chiffre d’affaires).</p>%s'
            '<details><summary>Importer une bibliothèque Honorium (JSON)</summary>%s</details></section><section class="b53-section"><h2>Modèles et présentation</h2>'
            '<ul class="b53-list">%s</ul><details><summary>Importer un modèle Word</summary>%s</details><details><summary>Logo</summary>%s</details></section></div>') % (
        e(fact.emission_obligation(ent)), _form('cabinet/enregistrer', cab, 'Enregistrer la fiche du cabinet'), e(econ['cout_horaire'].replace('.', ',')), e(econ['taux_cible'].replace('.', ',')),
        _form('honoraires/reglages', fee_form, 'Enregistrer'), _form('bibliotheque/importer', '<label>Fichier JSON<input type="file" accept=".json" data-b53-file required></label>',
                                                                     'Importer'),
        tpl_rows, _form('modele/importer', upload, 'Importer le modèle'), _form('logo/importer', logo, 'Enregistrer le logo'))


def document_html(desk, prefix, owner, role, doc_id):
    d = fact.get(desk, doc_id)
    snap = d['snapshot']
    t = snap['totaux']
    prev = fact.preview(desk, owner, role, doc_id)
    checks = prev['controles']
    editable = d['state'] in fact.EDITABLE
    m = fact._matter(desk, d['matter'])
    cli = fact.client(desk, d['matter'])
    lines = ''
    for i, l in enumerate(snap['lignes'] + ([{}] if editable else [])):
        lines += ('<div class="b53-row">%s<input name="ligne_%d_libelle" value="%s" placeholder="Prestation" maxlength="300"%s><input name="ligne_%d_quantite" value="%s" '
                  'inputmode="decimal" aria-label="Quantité"%s><select name="ligne_%d_unite"%s>%s</select><input name="ligne_%d_prix" value="%s" inputmode="decimal" '
                  'aria-label="Prix unitaire HT"%s><select name="ligne_%d_nature"%s>%s</select>%s</div>') % (
            _hidden(**{'ligne_%d_id' % i: l.get('id', '')}), i, e(l.get('libelle', ''), quote=True), '' if editable else ' disabled', i, e(l.get('quantite', '1'), quote=True),
            '' if editable else ' disabled', i, '' if editable else ' disabled',
            ''.join('<option value="%s"%s>%s</option>' % (k, ' selected' if l.get('unite') == k else '', v) for k, v in fact.UNITS.items()), i,
            e(str(fees.euros(l['prix_unitaire_ht_cents'])) if l else '', quote=True), '' if editable else ' disabled', i, '' if editable else ' disabled',
            ''.join('<option value="%s"%s>%s</option>' % (k, ' selected' if l.get('nature') == k else '', e(v)) for k, v in fact.NATURES.items()),
            ('<label class="r52-day"><input type="checkbox" name="ligne_%d_retirer"> retirer</label>' % i) if editable and l else '')
    missing = ''.join('<label>%s<input name="texte_%s" value="%s" maxlength="2000"></label>' % (e(v.replace('_', ' ').capitalize()), e(v), e(snap['textes'].get(v, ''), quote=True))
                      for v in checks['variables_manquantes'] if v not in ('numero_facture', 'numero_avoir', 'date_emission', 'date_echeance'))
    fis = snap['fiscal']
    fiscal_form = ('<fieldset><legend>TVA de ce document — %s</legend><label>Régime<select name="regime_doc"><option value="auto">Selon le cabinet et le client</option>'
                   '<option value="autre_qualifie"%s>Autre régime qualifié par l’avocat</option></select></label><label>Taux<select name="taux_doc">%s</select></label>'
                   '<label class="r52-day"><input type="checkbox" name="confirme_doc"%s> Je confirme la qualification demandée</label>'
                   '<label>Fondement (autre régime)<input name="fondement_doc" maxlength="200"></label><label>Mention exacte (autre régime)<input name="mention_doc" maxlength="300"></label>'
                   '</fieldset>') % (e(fis['regime_libelle']), ' selected' if fis['regime'] == 'autre_qualifie' else '',
                                     ''.join('<option%s>%s</option>' % (' selected' if str(fis.get('taux')) == r else '', r) for r in ('20', '10', '5.5', '2.1', '8.5', '0')),
                                     ' checked' if (snap.get('choix_fiscal') or {}).get('confirme') else '')
    editor = ''
    if editable:
        editor = _form('document/modifier', _hidden(id=d['id'], version=d['version']) + '<fieldset><legend>Lignes</legend>%s</fieldset>%s%s<label>Date de la prestation'
                       '<input type="date" name="date_prestation" value="%s"></label><label class="r52-day"><input type="checkbox" name="profil_secret"%s> Profil « secret '
                       'professionnel » (libellés génériques dans les données structurées)</label>' % (
                           lines, fiscal_form, ('<fieldset><legend>Informations à compléter</legend>%s</fieldset>' % missing) if missing else '',
                           e(snap['dates'].get('prestation') or '', quote=True), ' checked' if snap.get('profil_secret', True) else ''),
                       'Enregistrer et recalculer')
    client_form = ''
    if editable:
        client_form = ('<details class="b53-client"%s><summary>Client cocontractant : %s</summary>%s%s</details>') % (
            ' open' if not cli.get('kind') else '', e(cli.get('nom') or '(à compléter)'),
            _form('client/enregistrer', _hidden(dossier=d['matter'], version=cli.get('version') or 0) + ''.join(
                '<label>%s<input name="%s" value="%s" maxlength="200"></label>' % (e(l), k, e(str(cli.get(k) or ''), quote=True)) for k, l in (
                    ('nom', 'Nom ou raison sociale'), ('adresse_ligne1', 'Adresse'), ('code_postal', 'Code postal'), ('ville', 'Ville'), ('country', 'Pays (code)'),
                    ('siren', 'SIREN (professionnel)'), ('vat_number', 'N° TVA intracommunautaire'), ('email', 'Courriel'), ('representant', 'Représentant'),
                    ('qualite_representant', 'Qualité du signataire'))) +
                  '<label>Qualité<select name="kind"><option value="">À qualifier</option>%s</select></label>' % ''.join(
                      '<option value="%s"%s>%s</option>' % (k, ' selected' if cli.get('kind') == k else '', e(v)) for k, v in fact.fiscal.CLIENT_KINDS.items()),
                  'Enregistrer le client'),
            _form('client/siren', _hidden(dossier=d['matter']) + '<label>SIREN ou nom<input name="recherche" maxlength="120"></label>', 'Rechercher (données publiques)',
                  reload=False, cls='b53-inline b56-siren', extra='<div class="b56-candidates" aria-live="polite"></div>'))
    actions = []
    if editable:
        actions.append(_form('document/controler', _hidden(id=d['id']), 'Contrôler', cls='b53-inline ghost'))
        if not checks['bloquants'] and not (d['state'] == 'approuve' and d['approved_hash'] == fact.approval_hash(desk, d)):
            actions.append(_form('document/approuver', _hidden(id=d['id'], version=d['version'], empreinte=fact.approval_hash(desk, d)) +
                                 '<label class="r52-day"><input type="checkbox" required> J’ai relu ce document exact et ses montants</label>', 'Approuver cette version',
                                 cls='b53-inline'))
        actions.append(_form('document/abandonner', _hidden(id=d['id'], version=d['version']), 'Abandonner le brouillon', cls='b53-inline ghost',
                             confirm='Abandonner ce brouillon ? Il reste dans l’historique.'))
    if d['kind'] in ('facture', 'avoir') and d['state'] == 'approuve' and not d['number']:
        actions.append(_form('document/emettre', _hidden(id=d['id']) + '<input type="hidden" name="cle" data-r52-key value="">',
                             'Émettre (numéro définitif)', cls='b53-inline', confirm='Émettre maintenant ? Le numéro attribué ne sera jamais réutilisé.'))
    if d['kind'] in ('facture', 'avoir') and d['number'] and d['files_state'] == 'conflit':
        actions.append('<p class="b53-note ko" role="status">Un fichier de ce document a été modifié dans FACTURATION après l’émission. L’original émis est '
                       'conservé dans AxiorHub ; le fichier modifié n’est ni écrasé ni supprimé.</p>')
        actions.append(_form('document/restaurer', _hidden(id=d['id']), 'Déposer une copie de l’original émis', cls='b53-inline'))
    if d['kind'] in ('facture', 'avoir') and d['number'] and d['files_state'] == 'a_publier':
        actions.append(_form('document/emettre', _hidden(id=d['id'], cle=d['issue_key'] or ''), 'Publier les fichiers (nouvel essai)', cls='b53-inline'))
    if d['kind'] in ('devis', 'convention', 'lettre_mission') and d['state'] in ('approuve', 'envoye', 'en_signature', 'accepte', 'signe'):
        actions.append(_form('document/deposer', _hidden(id=d['id']), 'Déposer dans FACTURATION', cls='b53-inline'))
        nxt = {'devis': (('envoye', 'Marquer envoyé'), ('accepte', 'Accord reçu'), ('refuse', 'Refusé')),
               'convention': (('envoye', 'Marquer envoyée'), ('en_signature', 'En signature'), ('signe', 'Signée')),
               'lettre_mission': (('envoye', 'Marquer envoyée'), ('signe', 'Signée'))}[d['kind']]
        actions.append(_form('document/etat', _hidden(id=d['id'], version=d['version']) + '<label>Nouvel état<select name="etat">%s</select></label><label>Preuve (document '
                             'signé, courriel d’accord confirmé)<input name="preuve" maxlength="300"></label>' % ''.join('<option value="%s">%s</option>' % (k, e(v)) for k, v in nxt),
                             'Enregistrer l’état', cls='b53-inline'))
    if d['kind'] == 'facture' and d['number']:
        actions.append(_form('document/reglement', _hidden(id=d['id']) + '<label>Montant reçu<input name="montant" inputmode="decimal" required></label><label>Date<input type="date" '
                             'name="date" required></label><label>Moyen<select name="moyen"><option>virement</option><option>chèque</option><option>prélèvement</option>'
                             '<option>carte</option></select></label><label>Référence<input name="reference" maxlength="120"></label>'
                             '<input type="hidden" name="request_key" data-r52-key value="">', 'Enregistrer un règlement', cls='b53-inline'))
        actions.append(_form('document/avoir', _hidden(id=d['id']) + '<label>Motif de l’avoir<input name="motif" maxlength="300" required></label>', 'Préparer un avoir',
                             reload=True, cls='b53-inline ghost'))
        if fact.entity(desk)['transport'] != 'aucun' and not d['delivery_op']:
            actions.append(_form('document/transmettre', _hidden(id=d['id']), 'Transmettre par le trajet configuré', reload=False, cls='b53-inline'))
    if d['delivery_op']:
        actions.append('<a class="ax-btn ghost" href="%s">Fiche de transmission</a>' % e(prefix + '/operation?id=' + d['delivery_op'], quote=True))
    files = ''.join('<li>%s <span class="b53-note">%s</span></li>' % (e(v['chemin'].rsplit('/', 1)[-1]), e(v['sha256'][:12])) for v in d['files'].values())
    structure = d['checks'].get('structure') or {}
    struct_html = ''
    if structure.get('controles'):
        struct_html = '<details><summary>Contrôles de la facture structurée (%s)</summary><ul class="b53-list">%s</ul><p class="b53-note">Non vérifié ici : %s.</p><p>%s</p></details>' % (
            'réussis' if structure.get('ok') else 'en échec', ''.join('<li class="%s">%s %s</li>' % ('ok' if c['ok'] else 'ko', '✓' if c['ok'] else '✗', e(c['regle']))
                                                                         for c in structure['controles']), e(', '.join(structure.get('non_verifie') or [])), e(structure.get('facturx') or ''))
    issues = ''.join('<li class="ko">%s</li>' % e(b) for b in checks['bloquants']) + ''.join('<li class="warn">%s</li>' % e(w) for w in checks['avertissements'])
    fmt = ('contrôles internes %s ; XSD et Schematron officiels non exécutés' % ('réussis' if structure.get('ok') else 'en échec')) if structure.get('controles') else 'non vérifié'
    transmission = {'transmission_en_cours': 'en cours', 'deposee': 'déposée', 'rejetee': 'rejetée'}.get(d['state'], 'non effectuée' if d['number'] else '—')
    files_label = {'publies': 'publiés et relus', 'a_publier': 'à publier', 'conflit': 'modifiés dans le stockage'}.get(d['files_state'], '—')
    states = ('<dl class="b56-states"><div><dt>Calculs</dt><dd>vérifiés par le serveur</dd></div><div><dt>Données</dt><dd>%s</dd></div>'
              '<div><dt>Format structuré</dt><dd>%s</dd></div><div><dt>Fichiers</dt><dd>%s</dd></div><div><dt>Transmission</dt><dd>%s</dd></div></dl>') % (
        e('%d point(s) à compléter' % len(checks['bloquants']) if checks['bloquants'] else 'complètes'), e(fmt), e(files_label), e(transmission))
    preview_text = prev['texte']
    if not d['number'] and d['kind'] in ('facture', 'avoir'):
        for k in ('numero facture', 'numero avoir', 'date emission', 'date echeance'):
            preview_text = preview_text.replace('[à compléter : %s]' % k, '(attribué à l’émission)')
    return ('<article class="b53-doc"><header class="b53-doc-head"><h2>%s %s</h2><p class="b53-matter"><a href="%s">%s</a> — %s</p><p class="b53-state">%s%s · version %d</p>'
            '<p class="b53-totals">HT %s · TVA %s · TTC %s%s · <strong>net à payer %s</strong></p></header><div class="b53-grid"><div class="b53-edit">%s%s'
            '<ul class="b53-issues">%s</ul>' + states + '<div class="b53-actions b53-doc-actions">%s</div>%s%s</div><div class="b53-preview-wrap"><p class="b53-note">Aperçu textuel %s '
            '— mise en page indicative : le Word et le PDF produits font foi.</p>'
            '<section class="b53-preview%s" translate="no" lang="fr">%s</section></div></div></article></div>') % (
        e(d['kind_label']), e(d['number'] or '(brouillon)'), e(prefix + '/dossier?' + urlencode({'id': d['matter'], 'vue': 'travaux'}), quote=True), e(matter_display(m)),
        e(cli.get('nom') or 'client à compléter'), e(d['state_label']), (' · ' + e(d['payment_label'])) if d['kind'] == 'facture' and d['number'] else '', d['version'],
        _money(t['ht_cents']), _money(t['tva_cents']), _money(t['ttc_cents']), (' · provisions facturées déduites ' + _money(t['deja_regle_cents'])) if t['deja_regle_cents'] else '',
        _money(t['net_a_payer_cents']), client_form, editor, issues or '<li class="ok">Aucun point bloquant.</li>', ''.join(actions),
        ('<h3>Fichiers dans FACTURATION</h3><ul class="b53-list">%s</ul>' % files) if files else '', struct_html,
        'non fiscal (brouillon)' if not d['number'] or d['state'] in fact.EDITABLE else 'du document émis', '' if d['number'] and d['kind'] in ('facture', 'avoir') else ' b53-draft',
        markdown_html(preview_text))


def markdown_html(text):
    """Aperçu lisible du texte rendu : titres, tableau des lignes et paragraphes (contenu échappé ; aucun HTML du modèle n'est interprété)."""
    out, table = [], []

    def flush():
        if table:
            rows = [r for r in table if not re.fullmatch(r'\|[-|\s]+\|', r)]
            cells = [[c.strip() for c in r.strip('|').split('|')] for r in rows]
            if cells:
                out.append('<table class="b53-ptable"><thead><tr>%s</tr></thead><tbody>%s</tbody></table>' % (
                    ''.join('<th>%s</th>' % e(c) for c in cells[0]), ''.join('<tr>%s</tr>' % ''.join('<td>%s</td>' % e(c) for c in r) for r in cells[1:])))
            table.clear()
    for line in text.split('\n'):
        if line.startswith('|'):
            table.append(line)
            continue
        flush()
        if line.startswith('## '):
            out.append('<h4>%s</h4>' % e(line[3:]))
        elif line.startswith('# '):
            out.append('<h3>%s</h3>' % e(line[2:]))
        elif line.strip():
            out.append('<p>%s</p>' % e(line).replace('[à compléter', '<mark>[à compléter').replace(']', ']</mark>') if '[à compléter' in line else '<p>%s</p>' % e(line))
    flush()
    return ''.join(out)


# ------------------------------------------------------------------------------------------------ bloc du dossier
def matter_block_html(desk, prefix, mid, owner='cabinet', role='avocat'):
    """Bloc « Honoraires et facturation » de la page Dossier (onglet Travaux) ; une lecture partielle est signalée comme telle."""
    try:
        s = fact.matter_summary(desk, mid)
    except Stop as ex:
        return '<section class="d51-block b53-matter-block"><h3>Honoraires et facturation</h3><p class="b53-note">%s</p></section>' % e(_message(str(ex).split(':')[0]))
    conv = s['convention']
    rows = [('Convention applicable', ('%s (%s, version %d) — %s HT' % (conv['numero'] or 'sans référence', conv['etat'], conv['version'], _money(conv['montant_ht_cents'])))
             if conv else 'aucune convention enregistrée'),
            ('Budget', _money(s['budget_cents']) if s['budget_cents'] else 'non fixé'), ('Temps validés', _money(s['temps_valides_cents'])),
            ('Déjà facturé (HT)', _money(s['deja_facture_ht_cents'])), ('Provisions encaissées', _money(s['provisions_encaissees_cents'])),
            ('Reste à facturer (HT)', _money(s['reste_a_facturer_ht_cents'])), ('Solde connu', _money(s['solde_connu_cents']))]
    docs = ''.join('<li><a href="%s">%s %s</a> — %s · %s</li>' % (e(prefix + '/facturation?' + urlencode({'document': d['id']}), quote=True), e(d['kind_label']),
                                                                   e(d['number'] or '(brouillon)'), e(d['state_label']), _money(d['ttc_cents'])) for d in s['documents'])
    partial = ('<p class="b53-warn">Lecture partielle (%s) : ce n’est pas une absence d’impayé.</p>' % e(', '.join(s['lecture_partielle']))) if s['lecture_partielle'] else ''
    from . import facturation5655 as f55
    m = fact._matter(desk, mid)
    kind, why = f55.classify(desk, m)
    scope = (_form('dossier/perimetre', _hidden(dossier=mid, decision='exclure'), 'Pas un dossier client', cls='b53-inline ghost') if kind == 'client' else
             '<p class="b53-note">Écarté de la facturation : %s.</p>%s' % (e(why), _form('dossier/perimetre', _hidden(dossier=mid, decision='inclure'),
                                                                                    'Réintégrer dans la facturation', cls='b53-inline ghost')))
    return ('<section class="d51-block b53-matter-block"><h3>Honoraires et facturation</h3><dl class="b53-dl">%s</dl>%s<p class="b53-note">Lu le %s.</p>'
            '<ul class="b53-list">%s</ul><p><a class="ax-btn" href="%s">Ouvrir la facturation du dossier</a> <a class="ax-btn ghost" href="%s">Chiffrer une mission</a></p>'
            '%s</section>') % (
        ''.join('<dt>%s</dt><dd>%s</dd>' % (e(k), e(v)) for k, v in rows), partial, e(_fr_stamp(s['synchronise_le'])), docs or '<li>Aucun document.</li>',
        e(prefix + '/facturation?' + urlencode({'vue': 'documents', 'q': str(mid)}), quote=True), e(prefix + '/facturation?' + urlencode({'vue': 'honoraires', 'dossier': mid}), quote=True), scope)
