"""5.6.89 : écrans du parcours de conseil. Onglet « Conseil » du dossier (missions, revue de contrat, accords, obligations) et section « Playbooks du cabinet » de la bibliothèque de modèles.

Formulaires ``m5-form`` traités par le script commun : pas de script propre, pas de style en ligne, le lot Carbon applique les jetons. Tout texte est échappé. Chaque formulaire porte une clé d'idempotence ou la version affichée : une réponse perdue
ne rejoue rien, une page périmée reçoit un 409. Le rôle n'ouvre ici que l'affichage d'un bouton : le service refait son contrôle à chaque appel.
"""
from datetime import datetime, timezone
from html import escape as e
import json
from pathlib import PurePosixPath
import uuid

from . import accords5689 as accords, assertions5689 as A, consultation5689 as conseil, playbook5689 as pb

REPRENABLES = ('failed', 'budget_wait', 'suspended', 'planned')
RISQUES = {'faible': 'Faible', 'moyen': 'Moyen', 'eleve': 'Élevé', 'bloquant': 'Bloquant'}
LOCALISATIONS = (('paragraph', 'Paragraphe'), ('pdf_page', 'Page du PDF'), ('bookmark', 'Signet'))


def _h(nom, valeur):
    return '<input type="hidden" name="%s" value="%s">' % (e(nom, quote=True), e(str(valeur), quote=True))


def _cle():
    return _h('cle', uuid.uuid4().hex)


def _form(api, corps, inline=False, confirm=''):
    return '<form class="m5-form%s" data-api="m5689/%s" data-reload="1"%s>%s</form>' % (' m5-inline' if inline else '', e(api, quote=True), (' data-confirm="%s"' % e(confirm, quote=True)) if confirm else '', corps)


def _bouton(libelle, ghost=True):
    return '<button type="submit" class="ax-btn%s">%s</button>' % (' ghost' if ghost else '', e(libelle))


def _champ(libelle, nom, requis=False, valeur='', genre='text', max_car=300):
    return '<label>%s<input type="%s" name="%s" value="%s"%s%s></label>' % (e(libelle), genre, e(nom, quote=True), e(str(valeur), quote=True), ' required' if requis else '', (' maxlength="%d"' % max_car) if genre == 'text' else '')


def _zone(libelle, nom, requis=False, valeur='', lignes=3):
    return '<label>%s<textarea name="%s" rows="%d"%s>%s</textarea></label>' % (e(libelle), e(nom, quote=True), lignes, ' required' if requis else '', e(str(valeur)))


def _choix(libelle, nom, options, requis=False):
    return '<label>%s<select name="%s"%s>%s</select></label>' % (e(libelle), e(nom, quote=True), ' required' if requis else '', ''.join('<option value="%s">%s</option>' % (e(str(k), quote=True), e(str(v))) for k, v in options))


def _liste(items, vide=''):
    if not items:
        return '<p class="fc-empty">%s</p>' % e(vide) if vide else ''
    return '<ul class="fc-list">%s</ul>' % ''.join('<li>%s</li>' % e(str(x)) for x in items)


def droits(desk, acteur, role):
    """Ce que l'écran propose. Le service reste seul juge."""
    from .membres5650 import effective_rights
    fins = effective_rights(desk.c['state_dir'], acteur, role)
    avocat = role in ('administrateur', 'avocat')
    return {'valider': avocat and bool(fins.get('valider')), 'modele': avocat and bool(fins.get('valider_modele'))}


def _sources_options(desk, mid, suffixes=None):
    rows = [v for v in A.versions(desk, mid) if v['categorie'] != 'saisie' and (not suffixes or PurePosixPath(v['path']).suffix.lower() in suffixes)]
    return [(v['version_id'], '%s (importé le %s)' % (PurePosixPath(v['path']).name, v['importe_le'][:10])) for v in rows]


# ------------------------------------------------------------------------------------------------ mission
def _formulaire_mission(desk, prefix, mid):
    publies = [(('%s:%d' % (p['id'], p['version'])), '%s, %s, côté %s, version %d' % (p['domaine'], p['juridiction'], p['cote'], p['version'])) for p in pb.lister(desk) if p['etat'] == 'publie']
    contrats = _sources_options(desk, mid, ('.docx',))
    revue = ''
    if publies and contrats:
        revue = _choix('Playbook applicable', 'playbook_version', [('', 'Choisir')] + publies) + _choix('Contrat à relire', 'document_version', [('', 'Choisir')] + contrats)
    else:
        manque = [x for x, ok in (('un playbook publié (bibliothèque de modèles)', publies), ('un contrat Word importé depuis l’onglet Dossier structuré', contrats)) if not ok]
        revue = '<p class="fc-muted">Pour une revue de contrat, il manque : %s.</p>' % e(' et '.join(manque))
    aujourd = datetime.now(timezone.utc).date().isoformat()
    corps = (_h('matter_id', mid) + _cle() + _zone('Question posée', 'question', True) + _champ('Côté conseillé', 'advised_side', True) + _champ('Date d’analyse', 'as_of', True, aujourd, 'date')
             + _choix('Livrable', 'expected_output', (('consultation', 'Consultation'), ('contract_review', 'Revue de contrat'))) + _champ('Objectif d’une recherche hors du dossier (facultatif)', 'objectif_recherche', max_car=500)
             + '<details><summary>Revue de contrat</summary>%s</details>' % revue + _bouton('Préparer', False))
    return _form('missions', corps)


def _questions(vue, peut):
    if not vue['questions']:
        return ''
    if not peut['valider']:
        return '<h4>Questions pour l’avocat</h4>' + _liste([q['texte'] for q in vue['questions']])
    champs = ''.join(_zone(q['texte'], 'reponse_%d' % n, True, lignes=2) for n, q in enumerate(vue['questions']))
    return '<h4>Questions pour l’avocat</h4>' + _form('missions/%s/reponses' % vue['mission_id'], _h('revision', vue['revision']) + champs + _bouton('Enregistrer mes décisions', False))


def _revue(desk, vue, peut):
    revue = vue.get('revue')
    if not revue:
        return ''
    ident = vue['mission_id']
    corps = '<h4>Revue du contrat</h4>'
    if revue['incoherences']:
        corps += '<h5>Incohérences entre clauses</h5>' + _liste([x['detail'] for x in revue['incoherences']])
    conflits = {c['proposition']: c for c in revue['conflits']}
    lignes = ''
    for p in revue['propositions']:
        lignes += '<li class="d89-ligne"><strong>%s</strong> <span class="fc-muted">%s · règle %s · risque %s</span>' % (e(p['clause']), e(p['state_label']), e(p['regle']), e(RISQUES.get(p['risque'], p['risque'])))
        lignes += '<p>Position du playbook : %s</p><p>Texte proposé : %s</p><p>Motif pour l’autre partie : %s</p>' % (e(p['position']), e(p['texte_propose']), e(p['motif_externe'] or 'aucun'))
        if p['note_interne']:
            lignes += '<p><span class="d89-note">Interne</span> %s</p>' % e(p['note_interne'])
        if p['choix']:
            lignes += '<p><span class="d89-note">Décision à prendre</span> %s</p>' % e(p['choix'])
        if p['impacts']:
            lignes += '<p class="fc-muted">%d clause(s) dépendent de celle-ci.</p>' % len(p['impacts'])
        if peut['valider'] and p['state'] == 'proposee':
            for decision, libelle in (('accepter', 'Accepter'), ('refuser', 'Refuser')):
                lignes += _form('missions/propositions/%s/decider' % p['id'], _h('decision', decision) + _h('revision', p['revision']) + _bouton(libelle), True)
            lignes += '<details><summary>Adapter le texte</summary>%s</details>' % _form('missions/propositions/%s/decider' % p['id'], _h('decision', 'adapter') + _h('revision', p['revision']) + _zone('Texte retenu', 'texte', True, p['texte_propose']) + _bouton('Accepter ce texte', False))
        conflit = conflits.get(p['id'])
        if conflit:
            lignes += '<p><span class="d89-note">Clause modifiée après l’analyse</span> Texte actuel : %s</p>' % e(' '.join(conflit['texte_actuel']) or 'clause retirée')
            if peut['valider']:
                lignes += _form('missions/propositions/%s/resoudre' % p['id'], _h('choix', 'garder') + _h('revision', p['revision']) + _bouton('Garder le texte de l’avocat'), True)
                lignes += _form('missions/propositions/%s/resoudre' % p['id'], _h('choix', 'relancer') + _h('revision', p['revision']) + _bouton('Relancer l’analyse de cette clause'), True)
                if conflit['texte_actuel']:
                    lignes += '<details><summary>Reprendre la proposition sur le nouveau texte</summary>%s</details>' % _form('missions/propositions/%s/resoudre' % p['id'], _h('choix', 'reprendre') + _h('revision', p['revision']) + _zone('Texte proposé sur la nouvelle version', 'texte', True, p['texte_propose']) + _bouton('Reprendre', False))
        if peut['valider']:
            lignes += '<details><summary>Proposer une règle à partir de cette clause</summary>%s</details>' % _form('playbooks/%s/proposer-regle' % revue['playbook'], _cle() + _h('matter', vue['matter_id']) + _h('origine', 'revue') + _h('clause_type', 'autre') + _zone('Règle proposée', 'texte', True, p['texte_propose']) + _champ('Motif', 'motif', max_car=500) + _choix('Portée', 'portee', (('dossier', 'Ce dossier'), ('cabinet', 'Tout le cabinet'))) + _bouton('Soumettre à approbation'))
        lignes += '</li>'
    corps += '<ul class="fc-list">%s</ul>' % (lignes or '<li class="fc-empty">Aucune proposition sur ce contrat.</li>')
    if revue['negociation']:
        cellules = ''.join('<tr><td>%s</td><td>%s</td><td>%s</td><td>%s</td></tr>' % (e(x['clause']), e(x['texte_actuel']), e(x['texte_propose']), e(x['motif'])) for x in revue['negociation'])
        corps += '<h5>Tableau de négociation</h5><table class="fc-table"><thead><tr><th scope="col">Clause</th><th scope="col">Texte actuel</th><th scope="col">Texte proposé</th><th scope="col">Motif</th></tr></thead><tbody>%s</tbody></table>' % cellules
        corps += '<details><summary>Projet de message au cocontractant (non envoyé)</summary><p class="d89-texte">%s</p></details>' % e(revue['message'])
    actions = _form('missions/%s/actualiser' % ident, _bouton('Relire le contrat dans le dossier'), True)
    if peut['valider'] and any(p['state'] == 'acceptee' for p in revue['propositions']):
        actions += _form('missions/%s/reporter' % ident, _h('revision', revue['revision']) + _bouton('Reporter les propositions acceptées dans les documents', False), True, 'Les documents sont refaits avec les seules propositions acceptées. Rien n’est envoyé.')
    return corps + actions


def _mission(desk, prefix, vue, peut):
    ident = vue['mission_id']
    ouvert = vue['state'] in ('waiting_decision', 'failed') or bool((vue.get('revue') or {}).get('conflits'))
    etapes = ''.join('<li>%s : %s%s</li>' % (e(x['label']), e(x['state_label']), (' (%s)' % e(x['erreur'])) if x['erreur'] else '') for x in vue['etapes'])
    corps = '<h4>Étapes</h4><ol class="fc-list">%s</ol>' % etapes
    if vue['limites']:
        corps += '<h4>Limites</h4>' + _liste(vue['limites'])
    corps += _questions(vue, peut)
    if vue['references']:
        statuts = {'verifiee': 'Vérifiée', 'a_verifier': 'À vérifier'}
        corps += '<h4>Références</h4>' + _liste(['%s : %s' % (x['reference'], statuts.get(x['statut'], x['statut'])) for x in vue['references']])
    if vue['livrables']:
        from . import livrables5689 as liv
        liens = []
        for ident_doc in vue['livrables']:
            try:
                titre = liv.charger(desk, ident_doc)['title']
            except Exception:   # noqa: BLE001 — un document retiré n'empêche pas d'afficher le reste
                titre = 'Document'
            liens.append('<li><a href="%s">%s</a></li>' % (e('%s/livrables/%s' % (prefix, ident_doc), quote=True), e(titre)))
        corps += '<h4>Documents préparés</h4><ul class="fc-list">%s</ul>' % ''.join(liens)
    corps += _revue(desk, vue, peut)
    if vue['state'] in REPRENABLES:
        corps += _form('missions/%s/reprendre' % ident, _h('revision', vue['revision']) + _bouton('Reprendre ce qui manque'), True)
    if vue['state'] not in ('cancelled', 'completed'):
        corps += _form('missions/%s/annuler' % ident, _bouton('Annuler la mission'), True, 'Annuler définitivement cette mission ? Les documents déjà déposés restent dans le dossier.')
    return '<details class="d89-bloc"%s><summary><strong>%s</strong> <span class="fc-muted">%s · %s · date d’analyse %s</span></summary>%s</details>' % (' open' if ouvert else '', e(vue['question'][:200]), e(conseil.TYPES[vue['mission_type']]), e(vue['state_label']), e(vue['as_of']), corps)


# ------------------------------------------------------------------------------------------------ accords
def _formulaire_accord(desk, mid):
    sources = _sources_options(desk, mid, ('.docx', '.pdf'))
    if not sources:
        return '<p class="fc-empty">Importez d’abord la version signée depuis l’onglet Dossier structuré.</p>'
    corps = (_h('dossier', mid) + _cle() + _choix('Version signée', 'version_id', sources, True) + _champ('Intitulé', 'titre', max_car=200) + _champ('Date de signature', 'date_signature', True, genre='date') + _champ('Parties, séparées par des virgules', 'parties', True, max_car=500)
             + _choix('Preuve de la signature', 'preuve_nature', [(k, v) for k, v in accords.PREUVES.items()], True) + _champ('Référence chez le prestataire de signature', 'preuve_ref')
             + '<label class="m50-check"><input type="checkbox" name="attestation"> J’atteste que cette version a été signée par les parties</label><p class="fc-muted">Le nom du fichier ne prouve rien : indiquez la référence du prestataire, ou attestez vous-même.</p>' + _bouton('Enregistrer l’accord', False))
    return _form('accords', corps)


def _obligation(o, sources, peut):
    regle = o['regle']
    calcul = ('%s %s après %s' % (regle.get('delai'), regle.get('unite'), 'l’événement' if o['declencheur'] else 'la signature')) if regle else (o['date_fixe'] or 'sans date')
    ligne = '<li><strong>%s</strong> <span class="fc-muted">%s · clause %s · %s%s</span>' % (e(o['intitule']), e(o['responsable']), e(o['clause']), e(calcul), (' · ' + e(o['recurrence'])) if o['recurrence'] else '')
    if o['declencheur'] and not o['evenement_date']:
        ligne += '<p><span class="d89-note">En attente de son événement</span> %s</p>' % e(o['declencheur'])
        if peut['valider'] and sources:
            ligne += '<details><summary>Déclarer l’événement</summary>%s</details>' % _form('accords/obligations/%s/evenement' % o['id'], _champ('Date de l’événement', 'date', True, genre='date') + _choix('Document qui le prouve', 'version_id', sources, True) + _choix('Emplacement', 'locator_kind', LOCALISATIONS) + _champ('Numéro du paragraphe, de la page ou nom du signet', 'locator', True, max_car=60) + _bouton('Enregistrer l’événement', False))
    return ligne + '</li>'


def _occurrence(x, peut):
    ligne = '<li>%s <span class="fc-muted">%s</span>%s' % (e(x['echeance']), e(x['etat_label']), (' %s' % e(x['motif'])) if x['motif'] else '')
    if peut['valider'] and x['etat'] in ('proposee', 'mise_a_jour'):
        ligne += _form('accords/occurrences/%s/decider' % x['id'], _h('decision', 'inscrire') + _h('version', x['version']) + _bouton('Inscrire à l’agenda et aux tâches'), True) + _form('accords/occurrences/%s/decider' % x['id'], _h('decision', 'refuser') + _h('version', x['version']) + _bouton('Refuser'), True)
    return ligne + '</li>'


def _accord(desk, ident, mid, peut):
    a = accords.vue(desk, ident)
    sources = _sources_options(desk, mid)
    corps = '<p class="fc-muted">%s %s · version %s · parties : %s</p>' % (e(a['preuve']), e(a['preuve_ref']), e(a['version_id'][:8]), e(', '.join(a['parties'])))
    corps += '<h4>Obligations</h4><ul class="fc-list">%s</ul>' % (''.join(_obligation(o, sources, peut) for o in a['obligations']) or '<li class="fc-empty">Aucune obligation relevée.</li>')
    a_decider = [x for x in a['occurrences'] if x['etat'] in ('proposee', 'mise_a_jour')]
    autres = [x for x in a['occurrences'] if x['etat'] not in ('proposee', 'mise_a_jour')]
    corps += '<h4>Propositions d’agenda et de tâches</h4><ul class="fc-list">%s</ul>' % (''.join(_occurrence(x, peut) for x in a_decider[:36]) or '<li class="fc-empty">Aucune proposition à décider.</li>')
    if len(a_decider) > 36:
        corps += '<p class="fc-muted">%d autre(s) proposition(s) apparaîtront ensuite.</p>' % (len(a_decider) - 36)
    if autres:
        corps += '<details><summary>Historique (%d)</summary><ul class="fc-list">%s</ul></details>' % (len(autres), ''.join(_occurrence(x, peut) for x in autres[:100]))
    if a['statut'] == 'actif':
        corps += _form('accords/%s/extraire' % ident, _bouton('Relever les obligations de la version signée'), True)
        if peut['valider']:
            corps += '<details><summary>Ajouter une obligation</summary>%s</details>' % _form('accords/%s/obligations' % ident, _champ('Clause', 'clause', True, max_car=200) + _champ('Obligation', 'intitule', True) + _champ('Responsable', 'responsable', True, max_car=200) + _champ('Date fixe (facultatif)', 'date_fixe', genre='date')
                                                                                              + _champ('Délai (facultatif)', 'delai', genre='number') + _choix('Unité', 'unite', [(u, u) for u in accords.UNITES]) + _choix('Récurrence', 'recurrence', [('', 'Aucune')] + [(k, k) for k in accords.PAS]) + _champ('Événement déclencheur (facultatif)', 'declencheur') + _bouton('Ajouter', False))
    corps += _form('accords/%s/revalider' % ident, _bouton('Recontrôler le document signé'), True)
    return '<details class="d89-bloc"%s><summary><strong>%s</strong> <span class="fc-muted">signé le %s · %s · %d obligation(s) · %d à décider</span></summary>%s</details>' % (' open' if a_decider else '', e(a['titre']), e(a['date_signature']), e(a['statut_label']), len(a['obligations']), len(a_decider), corps)


def onglet_html(desk, prefix, mid, acteur, role):
    """Onglet Conseil : nouvelle mission, missions avec leur revue, accords signés et leurs obligations."""
    accords.schema(desk)
    pb.schema(desk)
    conseil.schema(desk)
    peut = droits(desk, acteur, role)
    missions = conseil.lister(desk, mid, acteur, role)
    blocs = ''.join(_mission(desk, prefix, conseil.vue(desk, x['mission_id'], acteur, role), peut) for x in missions[:12]) or '<p class="fc-empty">Aucune mission de conseil sur ce dossier.</p>'
    liste_accords = accords.lister(desk, mid, acteur, role)
    blocs_accords = ''.join(_accord(desk, a['id'], mid, peut) for a in liste_accords[:12]) or '<p class="fc-empty">Aucun accord signé enregistré.</p>'
    nouveau = ('<details><summary>Enregistrer un accord signé</summary>%s</details>' % _formulaire_accord(desk, mid)) if peut['valider'] else ''
    return ('<section class="ax-card fc-rubrique" id="rubrique-conseil"><h2>Conseil</h2><h3>Nouvelle mission</h3>%s<p class="fc-muted">Le serveur fixe les droits et les actions permises. Aucun envoi ni signature dans ce parcours.</p>'
            '<h3>Missions</h3>%s<h3>Accords signés</h3>%s%s<p><a href="%s">Playbooks du cabinet</a></p></section>') % (_formulaire_mission(desk, prefix, mid), blocs, nouveau, blocs_accords, e(prefix + '/corporate?vue=bibliotheque#playbooks', quote=True))


# ------------------------------------------------------------------------------------------------ playbooks du cabinet
def _regle(r, p, peut):
    ligne = '<li><strong>%s</strong> <span class="fc-muted">%s · risque %s</span><p>Position : %s</p>' % (e(r['cle']), e(r['clause_type']), e(RISQUES.get(r['risque'], r['risque'])), e(r['position']))
    for libelle, cle in (('Solution acceptable', 'acceptable'), ('Repli', 'repli'), ('Motif', 'motif'), ('Seuil de décision', 'seuil')):
        if r[cle]:
            ligne += '<p>%s : %s</p>' % (libelle, e(r[cle]))
    if peut['modele']:
        if p['etat'] == 'brouillon':
            ligne += _form('playbooks/%s/retirer-regle' % p['id'], _h('regle', r['cle']) + _bouton('Retirer du brouillon'), True)
        elif p['etat'] == 'publie':
            ligne += _form('playbooks/%s/version' % p['id'], _cle() + _h('retirer', r['cle']) + _bouton('Retirer cette règle (nouvelle version)'), True, 'Une nouvelle version en brouillon sera créée sans cette règle. Les missions passées gardent la leur.')
    return ligne + '</li>'


def _form_regle(p):
    types = [(k, k) for k, _ in pb.TYPES_CLAUSE] + [('autre', 'autre')]
    return _form('playbooks/%s/regle' % p['id'], _champ('Nom court de la règle', 'regle_cle', True, max_car=60) + _choix('Type de clause', 'clause_type', types) + _zone('Position souhaitée', 'position', True) + _zone('Solution acceptable', 'acceptable', lignes=2) + _zone('Repli', 'repli', lignes=2) + _zone('Motif', 'motif', lignes=2)
                 + _choix('Risque', 'risque', list(RISQUES.items())) + _champ('Seuil de décision (interne)', 'seuil', max_car=500) + _bouton('Ajouter la règle', False))


def _playbook(desk, resume, peut):
    p = pb.vue(desk, resume['id'])
    regles = ''.join(_regle(r, p, peut) for r in p['regles'])
    corps = '<ul class="fc-list">%s</ul>' % (regles or '<li class="fc-empty">Aucune règle.</li>')
    if peut['modele'] and p['etat'] == 'brouillon':
        corps += '<details><summary>Ajouter une règle</summary>%s</details>' % _form_regle(p)
        corps += _form('playbooks/%s/publier' % p['id'], _h('version', p['version']) + _bouton('Publier cette version', False), True, 'Une version publiée est figée et remplace la précédente pour les nouvelles missions.')
    elif peut['modele'] and p['etat'] == 'publie':
        corps += _form('playbooks/%s/version' % p['id'], _cle() + _bouton('Créer une nouvelle version'), True)
    return '<details class="d89-bloc"%s><summary><strong>%s</strong> <span class="fc-muted">%s · %s · version %d · %s · %d règle(s)</span></summary>%s</details>' % (' open' if p['etat'] == 'brouillon' else '', e('%s, côté %s' % (p['domaine'], p['cote'])), e(p['titre']), e(p['juridiction']), p['version'], e(p['etat_label']), len(p['regles']), corps)


def _proposition_regle(x, peut):
    ligne = '<li><strong>%s</strong> <span class="fc-muted">%s · portée : %s · %s</span>' % (e(x['texte'][:300]), e(x['clause_type']), e('tout le cabinet' if x['portee'] == 'cabinet' else 'ce dossier'), e(x['playbook']))
    if x['motif']:
        ligne += '<p>Motif : %s</p>' % e(x['motif'])
    if peut['modele']:
        ligne += _form('playbooks/propositions/%s/decider' % x['id'], _h('decision', 'approuver') + _champ('Nom de la règle', 'cle_regle', True, x['clause_type'], max_car=60) + _bouton('Approuver dans une nouvelle version'), True)
        ligne += _form('playbooks/propositions/%s/decider' % x['id'], _h('decision', 'refuser') + _bouton('Refuser'), True)
    return ligne + '</li>'


def playbooks_html(desk, acteur, role, hidden=frozenset()):
    """Section de la bibliothèque de modèles. Un dossier réservé ne laisse rien paraître de ses propositions."""
    pb.schema(desk)
    peut = droits(desk, acteur, role)
    blocs = ''.join(_playbook(desk, p, peut) for p in pb.lister(desk)[:40]) or '<p class="fc-empty">Aucun playbook pour l’instant.</p>'
    propositions = [x for x in pb.propositions_regles(desk) if x['matter'] not in hidden]
    creation = ''
    if peut['modele']:
        creation = '<details><summary>Créer un playbook</summary>%s</details>' % _form('playbooks', _cle() + _champ('Domaine', 'domaine', True, max_car=120) + _champ('Juridiction', 'juridiction', True, max_car=120) + _champ('Côté conseillé', 'cote', True, max_car=120) + _champ('Intitulé', 'titre', max_car=200) + _bouton('Créer le brouillon', False))
    bloc_prop = '<h3>Propositions de règles</h3><ul class="fc-list">%s</ul>' % (''.join(_proposition_regle(x, peut) for x in propositions) or '<li class="fc-empty">Aucune proposition à décider.</li>')
    return ('<section class="ax-card" id="playbooks"><h2>Playbooks du cabinet</h2><p class="fc-muted">Une version par domaine, juridiction et côté conseillé. Une version publiée est figée : une correction propose une règle à approuver, rien n’apprend tout seul.</p>%s%s%s</section>') % (creation, blocs, bloc_prop)
