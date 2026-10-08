"""5.6.14 (M11) : registre daté des profils procéduraux.

Un profil combine juridiction, voie (fond, référé, exécution), représentation, matière et conditions d'emploi. Il porte ses
références officielles (à vérifier au moment utile : le registre ne fournit pas un en-tête juridique prêt à copier), les mentions
obligatoires contrôlées, les formalités, le modèle Word et les tests de recette. La qualification se fait sur des réponses
structurées (juridiction, voie, représentation, montant, dates), jamais sur un seul mot de l'instruction. Une qualification ambiguë
ou un cas hors couverture produit une décision ; aucune règle n'est devinée. Un profil n'est utilisable qu'après approbation
explicite de l'avocat (réglage « profils5614:approved »), avec sa version ; une modification révoque les contrôles qui le consomment.
"""
from datetime import date
import hashlib
import json

from .common import Stop

VERSION = '2026-10-08'
JURIDICTIONS = {'tj': 'Tribunal judiciaire', 'tc': 'Tribunal de commerce', 'tae': 'Tribunal des activités économiques', 'jex': 'Juge de l’exécution'}
VOIES = {'fond': 'au fond', 'refere': 'en référé', 'execution': 'procédure d’exécution'}
REPRESENTATION = {'avocat': 'représentation par avocat', 'sans': 'sans représentation obligatoire', 'inconnue': 'à déterminer'}
# Seuil de représentation obligatoire devant le TJ au fond et le TC (à vérifier : art. 761 et 853 CPC, versions en vigueur).
SEUIL_REPRESENTATION_CENTS = 1_000_000


def _ref(text, verify=True, note=''):
    return {'ref': text, 'verify': verify, 'note': note}


PROFILES = {
    'jex': {
        'label': 'Juge de l’exécution', 'conditions': {'juridiction': 'jex', 'voie': 'execution', 'representation': ('avocat', 'sans')},
        'summary': 'Compétence du JEX (contestations relatives à l’exécution forcée, mesures conservatoires) ; procédure du CPCE avec renvois au CPC ; '
                   'représentation obligatoire sauf exceptions depuis 2020.',
        'references': [_ref('COJ, art. L.213-6 (compétence du JEX)'), _ref('CPCE, art. L.121-4 (représentation)'), _ref('CPCE, art. R.121-6 à R.121-22 (procédure devant le JEX)'),
                       _ref('CPC, art. 54 et 56 (mentions de l’assignation)'), _ref('CPC, art. 648 (mentions des actes de commissaire de justice)')],
        'mentions_kind': 'assignation', 'template_kind': 'assignation',
        'formalities': ['Vérifier la compétence du JEX et le titre ou la mesure en cause', 'Délai de comparution et modalités propres au CPCE', 'Représentation : vérifier l’exception applicable'],
        'required_fields': ['demandeur', 'defendeur', 'juridiction_siege', 'titre_ou_mesure', 'pretentions'],
        'tests': ['En-tête JEX distinct du référé', 'Mentions art. 54/56 présentes', 'Absence d’invention de date de signification'],
    },
    'refere_tj': {
        'label': 'Président du tribunal judiciaire en référé', 'conditions': {'juridiction': 'tj', 'voie': 'refere', 'representation': ('avocat', 'sans')},
        'summary': 'Voie de référé devant le président du TJ : pouvoirs invoqués (urgence, absence de contestation sérieuse, trouble manifestement illicite, dommage imminent, provision).',
        'references': [_ref('CPC, art. 834 et 835 (référé devant le président du TJ)'), _ref('CPC, art. 485 à 492-1 (procédure de référé)'),
                       _ref('CPC, art. 54 et 56 (mentions)'), _ref('CPC, art. 760 et 761 (représentation devant le TJ)')],
        'mentions_kind': 'assignation_refere', 'template_kind': 'assignation',
        'formalities': ['Date et heure d’audience obtenues du greffe (jamais inventées)', 'Fondement de référé explicité (834 ou 835)', 'Comparution et représentation selon la matière et le montant'],
        'required_fields': ['demandeur', 'defendeur', 'juridiction_siege', 'date_audience', 'fondement_refere', 'pretentions'],
        'tests': ['Le fondement 834/835 figure dans l’en-tête', 'Date d’audience : champ manquant visible si absente'],
    },
    'refere_tc': {
        'label': 'Président du tribunal de commerce en référé', 'conditions': {'juridiction': 'tc', 'voie': 'refere', 'representation': ('avocat', 'sans')},
        'summary': 'Référé commercial : compétence commerciale et pouvoirs du président du TC.',
        'references': [_ref('CPC, art. 872 et 873 (référé devant le président du TC)'), _ref('CPC, art. 485 à 492-1'), _ref('CPC, art. 853 à 855 (représentation et assignation devant le TC)'),
                       _ref('CPC, art. 54 et 56')],
        'mentions_kind': 'assignation_refere', 'template_kind': 'assignation',
        'formalities': ['Compétence commerciale vérifiée (qualité des parties, acte de commerce)', 'Date d’audience obtenue', 'Représentation selon le montant (seuil à vérifier)'],
        'required_fields': ['demandeur', 'defendeur', 'juridiction_siege', 'date_audience', 'fondement_refere', 'pretentions'],
        'tests': ['Fondement 872/873 et non 834/835', 'Nom exact de la juridiction'],
    },
    'refere_tae': {
        'label': 'Président du tribunal des activités économiques en référé', 'conditions': {'juridiction': 'tae', 'voie': 'refere', 'representation': ('avocat', 'sans')},
        'summary': 'Expérimentation TAE (loi 2023-1059 du 20 novembre 2023, décret 2024-674 du 3 juillet 2024) : seules les juridictions désignées ; '
                   'ne pas remplacer systématiquement « tribunal de commerce ».',
        'references': [_ref('Loi n° 2023-1059 du 20 novembre 2023, art. 26 (expérimentation TAE)'), _ref('Décret n° 2024-674 du 3 juillet 2024 (juridictions désignées)'),
                       _ref('CPC, art. 872 et 873'), _ref('CPC, art. 853 à 855')],
        'mentions_kind': 'assignation_refere', 'template_kind': 'assignation',
        'formalities': ['Vérifier que le siège relève d’un TAE désigné', 'Compétence effective du TAE pour la matière', 'Date d’audience obtenue'],
        'required_fields': ['demandeur', 'defendeur', 'juridiction_siege', 'date_audience', 'fondement_refere', 'pretentions'],
        'tests': ['Juridiction TAE seulement si siège désigné ; sinon profil TC'],
    },
    'tj_fond_avocat': {
        'label': 'Tribunal judiciaire au fond, représentation obligatoire', 'conditions': {'juridiction': 'tj', 'voie': 'fond', 'representation': ('avocat',)},
        'summary': 'Procédure écrite ordinaire devant le TJ : constitution d’avocat, saisine par remise au greffe, mentions spécifiques.',
        'references': [_ref('CPC, art. 750-1 (tentative amiable préalable, conditions)'), _ref('CPC, art. 752 à 754 (assignation et remise au greffe)'),
                       _ref('CPC, art. 760 et 761 (représentation obligatoire et dispenses)'), _ref('CPC, art. 54 et 56'), _ref('CPC, art. 768 (structure des écritures)')],
        'mentions_kind': 'assignation', 'template_kind': 'assignation',
        'formalities': ['Constitution d’avocat du demandeur', 'Délai de comparution (quinze jours) et remise au greffe dans le délai', 'Tentative amiable préalable si exigée'],
        'required_fields': ['demandeur', 'defendeur', 'juridiction_siege', 'avocat_constitue', 'pretentions', 'fondement_competence'],
        'tests': ['Mention de la constitution d’avocat', 'Délai de comparution rappelé sans date inventée'],
    },
    'tj_fond_sans_avocat': {
        'label': 'Tribunal judiciaire au fond, sans représentation obligatoire', 'conditions': {'juridiction': 'tj', 'voie': 'fond', 'representation': ('sans',)},
        'summary': 'Exception effectivement applicable (montant ≤ seuil ou matière dispensée) : procédure orale, comparution en personne possible.',
        'references': [_ref('CPC, art. 761 (dispense de représentation)'), _ref('CPC, art. 817 à 829 (procédure orale devant le TJ)'), _ref('CPC, art. 54 et 56')],
        'mentions_kind': 'assignation', 'template_kind': 'assignation',
        'formalities': ['Vérifier l’exception (montant, matière) avant de retenir ce profil', 'Modalités de comparution orale'],
        'required_fields': ['demandeur', 'defendeur', 'juridiction_siege', 'pretentions', 'motif_dispense'],
        'tests': ['Le profil n’est retenu que si la dispense est établie'],
    },
    'tc_fond': {
        'label': 'Tribunal de commerce ou TAE au fond', 'conditions': {'juridiction': ('tc', 'tae'), 'voie': 'fond', 'representation': ('avocat', 'sans')},
        'summary': 'Procédure devant le TC (ou le TAE désigné) : compétence commerciale, nom exact de la juridiction, représentation selon le montant.',
        'references': [_ref('CPC, art. 853 à 855 (représentation, assignation devant le TC)'), _ref('CPC, art. 861-1 à 871'), _ref('CPC, art. 54 et 56'),
                       _ref('Décret n° 2024-674 du 3 juillet 2024 (TAE)')],
        'mentions_kind': 'assignation', 'template_kind': 'assignation',
        'formalities': ['Compétence et nom exact de la juridiction', 'Représentation selon le seuil (à vérifier)', 'Délai de comparution'],
        'required_fields': ['demandeur', 'defendeur', 'juridiction_siege', 'pretentions', 'fondement_competence'],
        'tests': ['TAE seulement si désigné ; sinon TC'],
    },
}


def listing(desk):
    approved = desk.settings('profils5614:approved', {}) or {}
    out = []
    for pid, p in PROFILES.items():
        sig = signature(pid)
        out.append({'id': pid, 'label': p['label'], 'summary': p['summary'], 'version': VERSION, 'signature': sig,
                    'approved': approved.get(pid) == sig, 'approved_signature': approved.get(pid, ''), 'references': p['references'],
                    'formalities': p['formalities'], 'required_fields': p['required_fields'], 'tests': p['tests'],
                    'conditions': {k: (list(v) if isinstance(v, tuple) else v) for k, v in p['conditions'].items()}})
    return out


def signature(pid):
    p = PROFILES[pid]
    return hashlib.sha256(json.dumps({'id': pid, 'version': VERSION, 'conditions': p['conditions'], 'references': p['references'],
                                      'formalities': p['formalities'], 'required_fields': p['required_fields']}, sort_keys=True, default=str).encode()).hexdigest()[:16]


def approve(desk, pid, ack=''):
    if pid not in PROFILES:
        raise Stop('profil_procedural_absent')
    if str(ack or '').strip().lower() not in ('yes', 'oui', '1', 'true'):
        raise Stop('approbation_profil_requise')
    approved = dict(desk.settings('profils5614:approved', {}) or {})
    approved[pid] = signature(pid)
    desk.setting('profils5614:approved', approved)
    desk.audit('profil5614_approuve', {'profile': pid, 'signature': approved[pid], 'version': VERSION})
    return {'id': pid, 'approved': True, 'signature': approved[pid]}


def revoke(desk, pid):
    approved = dict(desk.settings('profils5614:approved', {}) or {})
    approved.pop(pid, None)
    desk.setting('profils5614:approved', approved)
    desk.audit('profil5614_revoque', {'profile': pid})
    return {'id': pid, 'approved': False}


def is_approved(desk, pid):
    return (desk.settings('profils5614:approved', {}) or {}).get(pid) == signature(pid)


def _matches(cond, value):
    if value in (None, ''):
        return None
    allowed = cond if isinstance(cond, tuple) else (cond,)
    return value in allowed


def qualify(desk, answers):
    """Profil(s) compatibles avec des réponses structurées. Retourne le profil retenu, ou les options et champs manquants."""
    answers = answers or {}
    juridiction = str(answers.get('juridiction') or '').lower()
    voie = str(answers.get('voie') or '').lower()
    representation = str(answers.get('representation') or '').lower()
    for key, allowed in (('juridiction', JURIDICTIONS), ('voie', VOIES), ('representation', REPRESENTATION)):
        value = {'juridiction': juridiction, 'voie': voie, 'representation': representation}[key]
        if value and value not in allowed:
            raise Stop('reponse_profil_invalide_' + key)
    if representation in ('', 'inconnue') and answers.get('montant_cents') not in (None, ''):
        try:
            montant = int(answers['montant_cents'])
        except (TypeError, ValueError):
            raise Stop('montant_invalide') from None
        if juridiction == 'tj' and voie == 'fond':
            representation = 'avocat' if montant > SEUIL_REPRESENTATION_CENTS else ''
    missing = [k for k, v in (('juridiction', juridiction), ('voie', voie)) if not v]
    if juridiction == 'jex' and not voie:
        voie = 'execution'
        missing = [m for m in missing if m != 'voie']
    candidates = []
    for pid, p in PROFILES.items():
        c = p['conditions']
        checks = [_matches(c['juridiction'], juridiction), _matches(c['voie'], voie), _matches(c['representation'], representation if representation != 'inconnue' else '')]
        if any(x is False for x in checks):
            continue
        candidates.append(pid)
    out = {'profile': '', 'candidates': candidates, 'missing': missing, 'unsupported': False, 'ambiguous': False, 'reasons': [],
           'answers': {'juridiction': juridiction, 'voie': voie, 'representation': representation or 'inconnue'}, 'version': VERSION}
    if not candidates:
        out['unsupported'] = True
        out['reasons'].append('Aucun profil du registre ne couvre cette combinaison (%s, %s, %s) : procédure déclarée hors couverture.' % (
            JURIDICTIONS.get(juridiction, juridiction or '?'), VOIES.get(voie, voie or '?'), REPRESENTATION.get(representation or 'inconnue')))
        return out
    if len(candidates) == 1 and not missing:
        pid = candidates[0]
        out['profile'] = pid
        out['approved'] = is_approved(desk, pid)
        out['label'] = PROFILES[pid]['label']
        out['reasons'].append('Profil « %s » retenu d’après juridiction, voie et représentation.' % PROFILES[pid]['label'])
        if not out['approved']:
            out['reasons'].append('Profil non encore approuvé par l’avocat (Paramètres › Profils procéduraux) : rédaction possible, contrôle « profil non approuvé ».')
        return out
    out['ambiguous'] = True
    if missing:
        out['reasons'].append('Champs à préciser : ' + ', '.join(missing) + '.')
    if juridiction == 'tj' and voie == 'fond' and representation in ('', 'inconnue'):
        out['reasons'].append('Représentation obligatoire ou dispense : indiquer le montant de la demande ou la matière (conséquence : constitution d’avocat et procédure écrite, ou procédure orale).')
    out['options'] = [{'id': pid, 'label': PROFILES[pid]['label'], 'consequence': PROFILES[pid]['summary']} for pid in candidates]
    return out


def profile(pid):
    if pid not in PROFILES:
        raise Stop('profil_procedural_absent')
    return {'id': pid, 'version': VERSION, 'signature': signature(pid), **PROFILES[pid]}


def header_requirements(pid):
    """Champs et mentions que l'en-tête doit alimenter ; les références restent à vérifier (verify=True)."""
    p = profile(pid)
    return {'required_fields': p['required_fields'], 'mentions_kind': p['mentions_kind'], 'template_kind': p['template_kind'],
            'references': p['references'], 'formalities': p['formalities'], 'date_checked': date.today().isoformat()}


def section_html(desk, prefix):
    from html import escape as e
    rows = ''
    for p in listing(desk):
        refs = ''.join('<li>%s%s</li>' % (e(r['ref']), ' <small>(à vérifier)</small>' if r['verify'] else '') for r in p['references'])
        action = ('<form class="m5-form m5-inline" data-api="m568/profils/revoke" data-reload="1"><input type="hidden" name="id" value="%s">'
                  '<button type="submit" class="ax-btn ghost">Révoquer</button></form>' % e(p['id'], quote=True)) if p['approved'] else (
            '<form class="m5-form m5-inline" data-api="m568/profils/approve" data-reload="1" data-confirm="Approuver ce profil (version %s) après vérification des références ?">'
            '<input type="hidden" name="id" value="%s"><input type="hidden" name="ack" value="yes"><button type="submit" class="ax-btn">Approuver</button></form>' % (e(p['version']), e(p['id'], quote=True)))
        rows += ('<details class="ax-card"><summary><strong>%s</strong> · %s</summary><p>%s</p><ul>%s</ul><p>Formalités : %s</p><p>Champs requis : %s</p>%s</details>' % (
            e(p['label']), 'approuvé (%s)' % e(p['signature']) if p['approved'] else 'non approuvé', e(p['summary']), refs,
            e(' ; '.join(p['formalities'])), e(', '.join(p['required_fields'])), action))
    return ('<section class="ax-card" id="profils5614"><h2>Profils procéduraux (registre daté %s)</h2>'
            '<p>Un profil n’est utilisé qu’une fois approuvé après vérification de ses références. La qualification repose sur juridiction, voie, '
            'représentation et montant, jamais sur un seul mot de l’instruction ; une combinaison non couverte est déclarée hors couverture.</p>%s</section>') % (e(VERSION), rows)
