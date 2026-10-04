"""Modèles d'actes et contrôle des mentions obligatoires (AxiorHub 4.7.0).

Le contrôle est déterministe : chaque mention est une liste de motifs recherchés dans le texte plié
(minuscules, sans accents). Une mention bloquante absente interdit le statut « prêt à relire ».
Les modèles sont personnalisables (mentions ajoutées, retirées, squelette) ; la personnalisation est
conservée dans les réglages du cabinet. Les références aux textes sont des repères : l'avocat reste seul
juge de la liste applicable à sa procédure.
"""
import json
import re

from .common import Stop, fold

BLOCK, WARN = 'bloquante', 'avertissement'


def m(id, label, patterns, severity=BLOCK, authority='', hint=''):
    return {'id': id, 'label': label, 'patterns': patterns, 'severity': severity, 'authority': authority, 'hint': hint}


COURT = r"\b(tribunal (judiciaire|de commerce|paritaire)|juge des contentieux|conseil de prud|cour d'appel|cour de cassation|president du tribunal|tribunal des activites economiques)"
PIECES = r"bordereau|pieces? (communiquees?|jointes?|n[°o]|numero)|liste des pieces|pieces? \d"
DISPOSITIF = r"par ces motifs|plaise au tribunal|plaise a la cour|il est demande|dispositif|demande(nt)? (au tribunal|a la cour)"
SIGNATURE = r"avocat au barreau|\bmaitre\b|signature|signe"

_ASSIGNATION = [
    m('juridiction', 'Juridiction saisie', [COURT], authority='CPC, art. 56', hint='Indiquer la juridiction devant laquelle la demande est portée.'),
    m('objet', 'Objet de la demande', [r"objet de (la|l')|aux fins de|a l'effet de|tendant a|demande (de|en|tendant)|afin de|en paiement|en reparation"],
      authority='CPC, art. 54', hint='Énoncer l’objet de la demande.'),
    m('demandeur', 'Identité du demandeur (dénomination, forme, siège ou domicile)', [r"demandeur|demanderesse|a la requete de|siege social|immatricul|domicili|rcs"],
      authority='CPC, art. 54', hint='Personne morale : forme, dénomination, siège, représentant ; personne physique : nom, prénoms, domicile.'),
    m('defendeur', 'Identité du défendeur', [r"defendeur|defenderesse|assign(e|ons|er)|a l'encontre de|contre (la societe|monsieur|madame|m\.)"],
      authority='CPC, art. 54', hint='Identifier le défendeur de la même façon.'),
    m('avocat', 'Constitution d’avocat et ministère d’avocat', [r"constitu(er|tion|e) (d')?avocat|ministere d'avocat|represent(e|ee|ation) par"],
      authority='CPC, art. 56', hint='Mentionner l’obligation de constituer avocat et l’avocat du demandeur.'),
    m('comparution', 'Délai pour comparaître / date d’audience', [r"comparaitre|comparution|delai de|audience (du|le|fixee)|date d'audience|dans un delai"],
      authority='CPC, art. 56', hint='Indiquer le délai pour comparaître ou la date d’audience.'),
    m('moyens_fait', 'Exposé des moyens en fait', [r"en fait|expose des faits|faits et procedure|rappel des faits|les faits"],
      authority='CPC, art. 56', hint='Exposer les faits.'),
    m('moyens_droit', 'Exposé des moyens en droit', [r"en droit|discussion|aux termes de l'article|sur le fondement|en application de"],
      authority='CPC, art. 56', hint='Exposer le fondement juridique.'),
    m('pieces', 'Pièces et bordereau', [PIECES], authority='CPC, art. 56', hint='Joindre ou viser le bordereau des pièces.'),
    m('amiable', 'Diligences en vue d’une résolution amiable (ou motif légitime)',
      [r"resolution amiable|mediation|conciliation|tentative (de|d')|amiable|motif legitime|procedure participative"],
      authority='CPC, art. 54', hint='Justifier des diligences amiables ou du motif légitime de s’en dispenser.'),
    m('dispositif', 'Demandes / dispositif', [DISPOSITIF], authority='CPC, art. 56', hint='Formuler les demandes.'),
    m('signature', 'Signature de l’avocat', [SIGNATURE], severity=WARN, hint='Prévoir la signature de l’avocat.'),
]
_REFERE = [
    m('fondement_refere', 'Fondement du référé (834, 835, 872 ou 873 CPC)', [r"\b(834|835|872|873)\b"],
      authority='CPC, art. 834, 835, 872, 873', hint='Viser l’article applicable (juridiction et nature de la mesure).'),
    m('urgence', 'Urgence, absence de contestation sérieuse, trouble ou dommage', [
        r"urgence|absence de contestation serieuse|obligation (n'est pas|non) serieusement contestable|trouble manifestement illicite|dommage imminent|contestation serieuse"],
      hint='Démontrer la condition du référé choisi.'),
    m('audience_refere', 'Date et heure d’audience', [r"\d{1,2} ?h ?\d{0,2}|heures?\b|audience du|audience de refere"], hint='Indiquer date, heure et lieu de l’audience.'),
    m('article_700', 'Demande au titre de l’article 700 du CPC', [r"\b700\b"], severity=WARN, authority='CPC, art. 700'),
    m('depens', 'Dépens', [r"depens"], severity=WARN),
]
_CONCLUSIONS = [
    m('parties', 'Désignation des parties', [r"conclusions (pour|de|recapitulatives|n[°o])|pour :|demandeur|defendeur|appelant|intime|partie"],
      authority='CPC, art. 768 et 954', hint='Désigner les parties et leur qualité.'),
    m('juridiction', 'Juridiction et numéro de RG', [r"\brg\b|r\.g\.|numero de role|n[°o] ?\d{2}/\d{3,6}"], hint='Indiquer le numéro de RG.'),
    m('expose', 'Exposé des faits et de la procédure', [r"faits et procedure|expose des faits|rappel des faits|en fait|procedure"],
      authority='CPC, art. 768', hint='Exposer distinctement les faits et la procédure.'),
    m('discussion', 'Discussion des prétentions et des moyens', [r"discussion|en droit|sur le fond|moyens"],
      authority='CPC, art. 768', hint='Discuter distinctement les prétentions et moyens.'),
    m('dispositif', 'Dispositif récapitulant les prétentions', [DISPOSITIF], authority='CPC, art. 768 et 954',
      hint='Terminer par un dispositif récapitulant les prétentions.'),
    m('bordereau', 'Bordereau récapitulatif des pièces', [PIECES], authority='CPC, art. 768', hint='Annexer ou viser le bordereau de pièces.'),
    m('anterieures', 'Reprise des prétentions et moyens antérieurs', [r"conclusions anterieures|reprend|maintient|precedentes? (ecritures|conclusions)"],
      severity=WARN, authority='CPC, art. 768', hint='À défaut de reprise, les prétentions antérieures sont réputées abandonnées.'),
    m('article_700', 'Demande au titre de l’article 700 du CPC', [r"\b700\b"], severity=WARN, authority='CPC, art. 700'),
    m('depens', 'Dépens', [r"depens"], severity=WARN),
    m('signature', 'Signature de l’avocat', [SIGNATURE], severity=WARN),
]
_CONSTITUTION = [
    m('avocat', 'Identité de l’avocat et barreau', [r"maitre .*avocat|avocat au barreau|avocat inscrit"], hint='Nom de l’avocat et barreau.'),
    m('partie', 'Partie représentée', [r"constitue pour|pour le compte de|se constitue|constitution (de|pour)|represente"], hint='Identifier la partie pour laquelle l’avocat se constitue.'),
    m('juridiction', 'Juridiction saisie', [COURT], hint='Indiquer la juridiction.'),
    m('rg', 'Numéro de RG', [r"\brg\b|r\.g\.|n[°o] ?\d{2}/\d{3,6}|numero de role"], hint='Indiquer le numéro de RG.'),
    m('domicile', 'Adresse / élection de domicile du cabinet', [r"elit.{0,12}domicile|domicile elu|cabinet|adresse|rue |avenue |place "], hint='Indiquer l’adresse du cabinet.'),
    m('date', 'Date', [r"\d{1,2}(er)? (janvier|fevrier|mars|avril|mai|juin|juillet|aout|septembre|octobre|novembre|decembre) \d{4}|\d{1,2}/\d{1,2}/\d{4}|fait a"], severity=WARN),
    m('signature', 'Signature de l’avocat', [SIGNATURE], severity=WARN),
]
_MISE_EN_DEMEURE = [
    m('destinataire', 'Destinataire identifié', [r"destinataire|a l'attention de|madame|monsieur|societe|sas |sarl "], hint='Identifier le destinataire.'),
    m('objet', 'Objet', [r"\bobjet\b"], hint='Mentionner « Objet : mise en demeure… ».'),
    m('mise_en_demeure', 'Terme « mise en demeure » ou interpellation suffisante', [r"mise en demeure|met(s|tons)? en demeure|met en demeure|vous somme|sommation"],
      authority='C. civ., art. 1344', hint='L’acte doit interpeller suffisamment le débiteur.'),
    m('fondement', 'Fondement (contrat, facture, texte)', [r"contrat|facture|article|en vertu|aux termes|bon de commande|convention"], hint='Rappeler l’origine de l’obligation.'),
    m('obligation', 'Somme ou obligation précise', [r"\d[\d  .,]*(€|euros?)|obligation de|executer|livrer|regler|payer"], hint='Chiffrer la somme ou préciser l’obligation.'),
    m('delai', 'Délai pour s’exécuter', [r"dans un delai (de|d'|maximum)|sous (huit|8|quinze|15|dix|10|trente|30|\d+) jours|delai de \d+|avant le \d"], hint='Fixer un délai précis.'),
    m('consequence', 'Conséquence à défaut d’exécution', [r"a defaut|faute de|sans reponse|serai(s|ent)? contraint|nous serons contraints|saisir|poursuite|assigner"], hint='Annoncer les suites en cas de défaut.'),
    m('envoi', 'Mode d’envoi (recommandée avec accusé de réception, etc.)', [r"recommandee|accuse de reception|\blrar?\b|commissaire de justice|huissier|par courriel"], severity=WARN),
    m('reserve', 'Réserve de tous droits', [r"reserve|tous droits"], severity=WARN),
    m('signature', 'Signature de l’avocat', [SIGNATURE], severity=WARN),
]
_CONFRERE = [
    m('mention', 'Mention « officielle » ou « confidentielle »', [r"\b(officiel(le)?|confidentiel(le)?)\b"], authority='RIN, art. 3 (correspondances entre avocats)',
      hint='La correspondance entre avocats doit porter l’une de ces mentions.'),
    m('interpellation', 'Formule d’interpellation du confrère', [r"cher confrere|chere consoeur|cher maitre|chere maitre|mon cher confrere|ma chere consoeur|maitre"], hint='Cher Confrère / Chère Consœur.'),
    m('reference', 'Référence du dossier', [r"(vos|nos|mes|votre|notre) ?ref|reference|dossier|\brg\b|affaire"], hint='Rappeler la référence ou l’affaire.'),
    m('qualite', 'Qualité en laquelle j’interviens', [r"mon client|ma cliente|mes clients|pour le compte|conseil de|avocat (de|des|du)"], hint='Indiquer le client représenté.'),
    m('politesse', 'Formule de politesse confraternelle', [r"confraternel|cordialement|salutations|bien confraternellement"], hint='Terminer par une formule confraternelle.'),
    m('signature', 'Signature de l’avocat', [SIGNATURE], severity=WARN),
]

KINDS = {
    'constitution': {'label': 'Constitution d’avocat', 'mentions': _CONSTITUTION},
    'conclusions': {'label': 'Conclusions', 'mentions': _CONCLUSIONS},
    'assignation': {'label': 'Assignation', 'mentions': _ASSIGNATION},
    'assignation_refere': {'label': 'Assignation en référé', 'mentions': _ASSIGNATION + _REFERE},
    'mise_en_demeure': {'label': 'Mise en demeure', 'mentions': _MISE_EN_DEMEURE},
    'courrier_confrere': {'label': 'Courrier au confrère', 'mentions': _CONFRERE},
}

SKELETONS = {
    'constitution': "[Juridiction] — RG n° [numéro]\nCONSTITUTION D'AVOCAT\nMaître [Nom], avocat au barreau de [Barreau], [adresse du cabinet], se constitue pour [partie représentée] dans l'affaire [parties].\nDomicile élu au cabinet de l'avocat.\nFait à [lieu], le [date].\n[Signature]",
    'conclusions': "[Juridiction] — RG n° [numéro]\nCONCLUSIONS pour [partie], demandeur / défendeur\nI. FAITS ET PROCÉDURE\nII. DISCUSSION\nIII. PAR CES MOTIFS — dispositif récapitulant les prétentions (art. 700 CPC, dépens)\nBordereau des pièces communiquées.\n[Signature de l'avocat]",
    'assignation': "ASSIGNATION devant le [juridiction]\nDemandeur : [identité complète]. Défendeur : [identité complète].\nObjet de la demande.\nDiligences en vue d'une résolution amiable.\nEXPOSÉ DES FAITS ; DISCUSSION (en droit) ; PAR CES MOTIFS ; bordereau des pièces.\nConstitution d'avocat obligatoire. Délai pour comparaître. [Signature]",
    'assignation_refere': "ASSIGNATION EN RÉFÉRÉ devant le président du [juridiction] — audience du [date] à [heure]\nDemandeur / Défendeur : [identités]. Objet. Fondement : articles [834/835 ou 872/873] du CPC.\nUrgence / absence de contestation sérieuse / trouble manifestement illicite.\nFaits ; droit ; diligences amiables ; PAR CES MOTIFS (art. 700, dépens) ; bordereau des pièces. [Signature]",
    'mise_en_demeure': "[Lettre recommandée avec accusé de réception]\nDestinataire : [identité]\nObjet : mise en demeure\nEn vertu de [contrat / facture n° / article], vous devez [somme chiffrée / obligation].\nJe vous mets en demeure de [exécuter] dans un délai de [x] jours à compter de la réception.\nÀ défaut, mon client saisira la juridiction compétente. Tous droits réservés.\n[Signature de l'avocat]",
    'courrier_confrere': "OFFICIEL (ou CONFIDENTIEL)\nVos réf. : [ ] — Nos réf. : [ ]\nCher Confrère / Chère Consœur,\nJ'interviens pour [mon client] dans l'affaire [ ].\n[Objet du courrier]\nBien confraternellement.\n[Signature]",
}

DOCUMENT_TYPE_TO_KIND = {'conclusions': 'conclusions', 'assignation': 'assignation', 'assignation_refere': 'assignation_refere',
                         'constitution': 'constitution', 'mise_en_demeure': 'mise_en_demeure', 'courrier_confrere': 'courrier_confrere'}


def kind_for_document(document_type):
    return DOCUMENT_TYPE_TO_KIND.get(document_type, '')


# ---------------------------------------------------------------- personnalisation

def _valid_pattern(pattern):
    pattern = str(pattern)
    if not pattern or len(pattern) > 160 or re.search(r'\([^)]*[+*][^)]*\)[+*]', pattern):
        raise Stop('motif_mention_invalide')
    try:
        re.compile(pattern)
    except re.error:
        raise Stop('motif_mention_invalide') from None
    return pattern


def customization(desk, kind):
    return (desk.settings('templates470:' + kind, {}) or {}) if desk is not None else {}


def save_customization(desk, kind, data):
    if kind not in KINDS:
        raise Stop('modele_inconnu')
    known = {x['id'] for x in KINDS[kind]['mentions']}
    disabled = [str(x) for x in (data.get('disabled') or []) if str(x) in known]
    extra = []
    for item in (data.get('extra') or [])[:20]:
        ident = re.sub(r'[^a-z0-9_]', '', fold(str(item.get('id') or item.get('label') or '')).replace(' ', '_'))[:40]
        label = str(item.get('label') or '').strip()[:160]
        if not ident or not label or ident in known:
            raise Stop('mention_personnalisee_invalide')
        patterns = [_valid_pattern(p) for p in (item.get('patterns') or [])][:6]
        if not patterns:
            raise Stop('motif_mention_invalide')
        severity = BLOCK if item.get('severity') != WARN else WARN
        extra.append(m(ident, label, patterns, severity, str(item.get('authority') or '')[:80], str(item.get('hint') or '')[:240]))
    skeleton = str(data.get('skeleton') or '')[:4000]
    value = {'disabled': disabled, 'extra': extra, 'skeleton': skeleton}
    desk.setting('templates470:' + kind, value)
    desk.audit('modele_470_personnalise', {'kind': kind, 'disabled': len(disabled), 'extra': len(extra)})
    return value


def effective(desk, kind):
    if kind not in KINDS:
        raise Stop('modele_inconnu')
    custom = customization(desk, kind)
    disabled = set(custom.get('disabled') or [])
    mentions = [x for x in KINDS[kind]['mentions'] if x['id'] not in disabled] + list(custom.get('extra') or [])
    return {'kind': kind, 'label': KINDS[kind]['label'], 'mentions': mentions,
            'skeleton': custom.get('skeleton') or SKELETONS[kind], 'disabled': sorted(disabled)}


# ---------------------------------------------------------------- contrôle

def check(desk, kind, text, header_chars=700):
    """Retourne les mentions présentes, manquantes bloquantes et manquantes à signaler."""
    model = effective(desk, kind)
    folded = fold(str(text or '')).replace('’', "'")
    present, missing, warnings = [], [], []
    for mention in model['mentions']:
        zone = folded[:header_chars] if mention['id'] == 'mention' and kind == 'courrier_confrere' else folded
        found = any(re.search(p, zone) for p in mention['patterns'])
        row = {k: mention[k] for k in ('id', 'label', 'severity', 'authority', 'hint')}
        if found:
            present.append(row)
        elif mention['severity'] == BLOCK:
            missing.append(row)
        else:
            warnings.append(row)
    return {'kind': kind, 'label': model['label'], 'ok': not missing, 'present': present, 'missing': missing,
            'warnings': warnings, 'total': len(model['mentions'])}


def packet_entry(desk, kind):
    """Pièce de contexte pour le rédacteur : les mentions à faire figurer. Jamais une source de faits."""
    model = effective(desk, kind)
    lines = ['- ' + x['label'] + (' (' + x['authority'] + ')' if x['authority'] else '') for x in model['mentions']]
    return {'id': 'modele-' + kind, 'kind': 'template', 'path': 'modele_interne:' + kind, 'etag': '', 'modified': '',
            'excerpt': 'Mentions obligatoires à faire figurer dans ' + model['label'] + ' :\n' + '\n'.join(lines) +
                       '\n\nTrame indicative :\n' + model['skeleton'], 'source_kind': 'template'}


def listing(desk):
    out = []
    for kind in KINDS:
        model = effective(desk, kind)
        out.append({'kind': kind, 'label': model['label'], 'mentions': len(model['mentions']),
                    'customized': bool(customization(desk, kind))})
    return out
