"""5.6.97 (lot gestion, § 18 à § 21) : sections de la fiche du dossier rendues par le serveur, prêtes à être placées dans les onglets du lot
dossiers (``dossiers_ui5697.ONGLETS``) : Procédure · Pièces (registre procédural, paquets de dépôt), Gestion (quatre indicateurs, forfait),
Correspondances (Pièces demandées, Remis au client) et « Préparer la clôture ».

Tout texte est échappé (``html.escape``) ; aucun script en ligne : les formulaires passent par le traitement commun (``form.m5-form[data-api]``
de ``v500.js``, fichiers lus par ``v5689-formulaires.js`` sur ``data-envoi-fichier``) vers ``/api440/m5689/gestion/*``. Chaque formulaire porte
une clé d'idempotence tirée au rendu. La garde commune est appliquée par les services : un dossier réservé ne rend qu'un texte neutre.
"""
from html import escape as e
from urllib.parse import urlencode
import uuid

from .common import Stop


def _cle():
    return '<input type="hidden" name="cle" value="g97-%s">' % uuid.uuid4().hex[:24]


def _cache(nom, valeur):
    return '<input type="hidden" name="%s" value="%s">' % (e(nom, quote=True), e(str(valeur), quote=True))


def _form(route, champs, bouton, confirmer='', fantome=False):
    return ('<form class="m5-form g97-form" data-api="m5689/gestion/%s" data-reload%s>%s%s<button class="ax-btn%s" type="submit">%s</button>'
            '<p role="status" class="m5-status"></p></form>') % (e(route, quote=True), (' data-confirm="%s"' % e(confirmer, quote=True)) if confirmer else '', _cle(),
                                                                 champs, ' ghost' if fantome else '', e(bouton))


def _champ(nom, libelle, type_='text', requis=False, valeur=''):
    return '<label>%s <input type="%s" name="%s"%s value="%s"></label>' % (e(libelle), type_, e(nom, quote=True), ' required' if requis else '', e(str(valeur), quote=True))


def _lien_api(prefix, route, **params):
    return e('%s/api440/m5689/gestion/%s?%s' % (prefix, route, urlencode(params)), quote=True)


def _neutre():
    return '<p class="ax-muted">Section indisponible pour ce dossier.</p>'


def procedure_html(desk, acteur, prefix, mid):
    """Procédure · Pièces : échéances (« À vérifier » distinctes des dates confirmées), suivi et rappels, paquets gelés et accusés."""
    from . import procedure5697
    try:
        reg = procedure5697.registre(desk, acteur, mid)
    except Stop:
        return _neutre()
    lignes = []
    for x in reg['echeances']:
        suivi = x.get('suivi') or {}
        actions = ''
        if x['status'] not in ('terminee', 'annulee'):
            if x['a_verifier']:
                actions += _form('echeances/decider', _cache('id', x['id']) + _cache('action', 'valider') + _cache('version', x['revision']), 'Valider la date')
            actions += _form('echeances/decider', _cache('id', x['id']) + _cache('action', 'accomplir') + _cache('version', x['revision'])
                             + _champ('preuve', 'Preuve de l’accomplissement', requis=True), 'Marquer accompli', fantome=True)
            actions += _form('echeances/decider', _cache('id', x['id']) + _cache('action', 'ecarter') + _cache('version', x['revision'])
                             + _champ('motif', 'Motif', requis=True), 'Écarter', 'Écarter cette échéance ?', fantome=True)
            if not x['a_verifier']:
                actions += _form('echeances/suivi', _cache('id', x['id']) + _cache('version_suivi', suivi.get('revision', ''))
                                 + _champ('responsable', 'Responsable', valeur=suivi.get('responsable', '')) + _champ('rappel_destinataire', 'Rappel à')
                                 + _champ('rappel_le', 'le', 'date'), 'Enregistrer le suivi', fantome=True)
        lignes.append('<tr><td>%s</td><td>%s</td><td><span class="g97-etat%s">%s</span></td><td>%s</td><td>%s</td><td>%s</td></tr>' % (
            e(x.get('due_label') or x.get('due') or 'date à compléter'), e(x.get('rule_label') or x['rule_id']), ' g97-a-verifier' if x['a_verifier'] else '',
            e(x['etat_libelle']), e(x.get('source_path') or ''), e(suivi.get('responsable') or '—'), actions))
    table = ('<table class="g97-table"><caption>Échéances du registre</caption><thead><tr><th scope="col">Date</th><th scope="col">Acte attendu</th>'
             '<th scope="col">État</th><th scope="col">Source</th><th scope="col">Responsable</th><th scope="col">Actions</th></tr></thead><tbody>%s</tbody></table>') % ''.join(lignes) \
        if lignes else '<p class="ax-muted">Aucune échéance au registre.</p>'
    saisie = _form('echeances/saisir', _cache('dossier', mid) + _champ('regle', 'Règle', requis=True) + _champ('depart', 'Événement de départ', 'date', True)
                   + _champ('source', 'Source et repère (pièce, page, courriel)', requis=True)
                   + '<label><input type="checkbox" name="depart_certain" value="true"> Départ prouvé</label>', 'Saisir une échéance sourcée', fantome=True)
    depots = []
    for d in reg['depots']:
        vue = procedure5697.vue_depot(desk, acteur, d['id'])
        fichiers = ''.join('<li><a href="%s">%s %s</a> <span class="ax-muted">SHA-256 %s…</span></li>' % (
            _lien_api(prefix, 'depots/fichier', id=d['id'], rang=f['rang']), e(('Pièce n° %s' % f['numero']) if f['numero'] else 'Acte'), e(f['titre']), e(f['sha256'][:12]))
            for f in vue['fichiers'])
        accuses = ''.join('<li>%s — %s%s</li>' % (e(a['nature_libelle']), e(a['etat_libelle']), (' : ' + e(a['motif'])) if a['motif'] else '') for a in vue['accuses'])
        actions = ''
        if vue['etat'] == 'gele':
            actions = _form('depots/declarer', _cache('id', d['id']) + _cache('version', vue['revision']) + _champ('envoi_ref', 'Identifiant d’envoi', requis=True)
                            + _champ('date_depot', 'Date du dépôt', 'date', True) + _champ('reference_affaire', 'Référence d’affaire', valeur=vue['reference_affaire']),
                            'Déclarer le dépôt')
        actions += _form('depots/accuse', _cache('id', d['id']) + '<label>Nature <select name="nature"><option value="accuse">Accusé original</option>'
                         '<option value="notification">Notification (alerte)</option><option value="rejet">Avis de rejet</option></select></label>'
                         + '<label>Fichier <input type="file" name="fichier" data-envoi-fichier required></label>' + _champ('reference', 'Référence lue')
                         + _champ('envoi_ref', 'Identifiant d’envoi lu') + _champ('date', 'Date lue', 'date'), 'Importer', fantome=True)
        depots.append('<li><strong>%s</strong> — %s <p class="ax-muted">%s</p><ul>%s</ul><ul>%s</ul>%s</li>' % (
            e(d['canal']), e(d['etat_libelle']), e(vue['mention']), fichiers, accuses, actions))
    return ('<section class="g97-section" aria-labelledby="g97-proc-t"><h2 id="g97-proc-t">Registre procédural</h2>%s%s<h3>Paquets de dépôt</h3>%s</section>') % (
        table, saisie, ('<ul class="g97-liste">%s</ul>' % ''.join(depots)) if depots else '<p class="ax-muted">Aucun paquet gelé.</p>')


def gestion_html(desk, acteur, prefix, mid):
    """Gestion : les quatre indicateurs avec leur définition, la source et sa fraîcheur, le forfait. Sans le droit « voir les honoraires » : rien."""
    from . import honoraires5697
    try:
        i = honoraires5697.indicateurs(desk, acteur, mid)
        f = honoraires5697.forfait(desk, acteur, mid)
    except Stop as ex:
        return '<p class="ax-muted">Les honoraires de ce dossier relèvent d’un droit que votre compte n’a pas.</p>' if str(ex) == 'role_insuffisant' else _neutre()
    cartes = ''.join('<div class="g97-carte"><h3>%s</h3><p class="g97-montant">%s %s</p><p class="ax-muted">%s</p></div>' % (
        e(i[k]['libelle']), e((i[k].get('ht') or i[k].get('ttc'))['montant']), 'HT' if 'ht' in i[k] else 'TTC', e(i[k]['definition']))
        for k in ('travail_a_facturer', 'facture_non_regle', 'impayes_echus', 'encaisse'))
    src = i['sources']
    etat = 'Source : %s%s' % (src['libelle'], (' · dernière synchronisation %s' % src['derniere_synchro'][:16]) if src.get('derniere_synchro') else '')
    anomalies = ''.join('<li>%s</li>' % e(a) for a in i['anomalies'])
    forfait = '<p>Heures validées : %s · en attente : %s%s%s</p><p class="ax-muted">%s</p>' % (
        e(f['heures_validees']), e(f['heures_en_attente']), (' · reste contractuel : %s € HT' % e(f['reste_contractuel_ht']['montant'])) if f['reste_contractuel_ht'] else '',
        (' · prochain jalon : %s' % e(f['prochain_jalon']['libelle'])) if f['prochain_jalon'] else '', e(f['mention']))
    actions = _form('honoraires/brouillon', _cache('dossier', mid), 'Préparer le brouillon de facture', fantome=True)
    return ('<section class="g97-section" aria-labelledby="g97-hon-t"><h2 id="g97-hon-t">Honoraires et encaissement</h2><div class="g97-cartes">%s</div>'
            '<p class="ax-muted">%s · calculé le %s</p>%s<h3>Forfait</h3>%s%s</section>') % (
        cartes, e(etat), e(i['calcule_le'][:16]), ('<ul class="g97-anomalies" role="status">%s</ul>' % anomalies) if anomalies else '', forfait, actions)


def correspondances_html(desk, acteur, prefix, mid):
    """Correspondances : Pièces demandées et Remis au client (version, canal, preuve)."""
    from . import remises5697
    try:
        demandes = remises5697.pieces_demandees(desk, acteur, mid)
        remises = remises5697.remises(desk, acteur, mid)
    except Stop:
        return _neutre()
    blocs = []
    for d in demandes:
        elements = ''.join('<li>%s — <span class="g97-etat">%s</span>%s</li>' % (
            e(x['libelle']), e(x['etat_libelle']), ''.join(_form('pieces/qualifier', _cache('id', d['id']) + _cache('item', x['item']) + _cache('decision', dec), lib, fantome=True)
                                                          for dec, lib in (('accepter', 'Accepter'), ('a_remplacer', 'À remplacer'), ('dispenser', 'Dispenser'))
                                                          if x['etat'] not in ('accepte', 'dispense') and (dec != 'accepter' or x['etat'] == 'recu_a_verifier')))
                           for x in d['elements'])
        blocs.append('<li><strong>Demande à %s</strong>%s · recontrôle le %s<ul>%s</ul></li>' % (
            e(d['destinataire']), ' · <span class="g97-a-relancer">à relancer</span>' if d['a_relancer'] else '', e(d['revoir_le'] or '—'), elements))
    demander = _form('pieces/demander', _cache('dossier', mid) + '<label>Pièces (une par ligne) <textarea name="elements_texte" required></textarea></label>'
                     + _champ('destinataire', 'Destinataire', 'email', True) + _champ('revoir_le', 'Recontrôler le', 'date', True), 'Demander des pièces', fantome=True)
    remis = []
    for r in remises:
        fichiers = ''.join('<li><a href="%s">%s</a> <span class="ax-muted">version %s</span></li>' % (
            _lien_api(prefix, 'remises/fichier', id=r['id'], rang=f['rang']), e(f['path'].rsplit('/', 1)[-1]), e(f['version_id'] or '—')) for f in r['fichiers'] if f.get('copie'))
        actions = ''
        if r['etat'] in ('copie_prete', 'partage_actif'):
            actions += _form('remises/transmission', _cache('id', r['id']) + _cache('version', r['revision']) + _champ('preuve', 'Preuve de remise', requis=True),
                             'Déclarer la transmission', fantome=True)
        if r['partage_actif']:
            actions += _form('remises/revoquer', _cache('id', r['id']), 'Révoquer l’accès', 'Révoquer cet accès ?', fantome=True)
        if r['etat'] == 'revocation_demandee':
            actions += _form('remises/verifier', _cache('id', r['id']), 'Vérifier la révocation', fantome=True)
        remis.append('<li><strong>%s</strong> — %s · %s<ul>%s</ul>%s</li>' % (e(r['destinataire']), e(r['canal_libelle']), e(r['etat_libelle']), fichiers, actions))
    return ('<section class="g97-section" aria-labelledby="g97-pd-t"><h2 id="g97-pd-t">Pièces demandées</h2>%s%s</section>'
            '<section class="g97-section" aria-labelledby="g97-rc-t"><h2 id="g97-rc-t">Remis au client</h2>%s</section>') % (
        ('<ul class="g97-liste">%s</ul>' % ''.join(blocs)) if blocs else '<p class="ax-muted">Aucune pièce demandée.</p>', demander,
        ('<ul class="g97-liste">%s</ul>' % ''.join(remis)) if remis else '<p class="ax-muted">Rien n’a encore été remis au client.</p>')


def cloture_html(desk, acteur, prefix, mid):
    """« Préparer la clôture » : points recalculés, liens, décisions motivées pour les points levables ; archivé : réouverture motivée."""
    from . import cloture5697
    try:
        etat = cloture5697.etat(desk, acteur, mid)
        if etat['etat'] == 'archive':
            return ('<section class="g97-section" aria-labelledby="g97-clo-t"><h2 id="g97-clo-t">Dossier archivé</h2><p>%s</p>%s</section>') % (
                e('Clôturé le %s.' % (etat['historique'][0]['at'][:10] if etat['historique'] else '')),
                _form('cloture/rouvrir', _cache('dossier', mid) + _cache('version', etat['version']) + _champ('motif', 'Motif de la réouverture', requis=True),
                      'Rouvrir le dossier', 'Rouvrir ce dossier ? Aucun partage, envoi ni rendez-vous ne sera recréé.'))
        prep = cloture5697.preparer(desk, acteur, mid)
    except Stop:
        return _neutre()
    noms = {'bloquant': 'Bloquant', 'avertissement': 'À vérifier', 'ok': 'Réglé', 'leve': 'Levé par décision'}
    points = ''.join('<li><strong>%s</strong> — <span class="g97-etat g97-%s">%s</span> <span class="ax-muted">%s</span><ul>%s</ul></li>' % (
        e(c['libelle']), e(c['etat']), e(noms.get(c['etat'], c['etat'])), e(c['action']), ''.join('<li>%s</li>' % e(x['libelle']) for x in c['elements'][:20]))
        for c in prep['controles'])
    decisions = ''.join(_champ('decisions.%s' % k, 'Décision motivée : %s' % cloture5697.LIBELLES[k]) for k in cloture5697.LEVABLES)
    return ('<section class="g97-section" aria-labelledby="g97-clo-t"><h2 id="g97-clo-t">Préparer la clôture</h2><p class="ax-muted">Le dossier reste actif '
            'pendant le contrôle ; les points sont recalculés au moment de clôturer.</p><ul class="g97-liste">%s</ul>%s</section>') % (
        points, _form('cloture/cloturer', _cache('dossier', mid) + _cache('version', prep['version']) + _champ('motif', 'Motif', requis=True) + decisions
                      + _champ('suivi_financier.responsable', 'Suivi financier : responsable') + _champ('suivi_financier.prochaine_date', 'prochaine date', 'date'),
                      'Clôturer et archiver', 'Clôturer et archiver ce dossier ?'))
