"""Compléments métier 5.6.109 : sélection déterministe, sans appel de modèle.

Les ressources courtes complètent la méthode du cabinet. Elles n'accordent aucun
droit ni capacité réseau et passent par le registre/budget/traçage existants.
"""
import json
import re
from html import escape
from .common import fold

# 5.6.109 (lot K, F06) : méthodes de CRÉATION d'un acte. En phase d'analyse, elles ne sont chargées que si la vraie consigne demande de
# préparer l'acte : « Analyser l'assignation reçue » ne charge pas la méthode qui sert à en rédiger une (mot « assignation » trop large).
CREATION = frozenset(('builtin.metier109.assignation_generale', 'builtin.metier109.assignation_jex', 'builtin.metier109.assignation_refere',
                      'builtin.metier109.assignation_commerce', 'builtin.metier109.requete_speciale'))
VERBES_CREATION = ('redige', 'rediger', 'redigez', 'prepare', 'preparer', 'preparez', 'etablis', 'etablir', 'ecris', 'ecrire', 'cree', 'creer',
                   'dresse', 'dresser', 'projet', 'trame', 'modele')
# Ce que fait chaque méthode, dit à l'avocat dans l'écran Compétences existant avant toute activation (texte du logiciel, pas du paquet).
DESCRIPTIONS = {
    'builtin.metier109.analyse_dossier': 'Inventaire des pièces, chronologie et contradictions du dossier ; sépare actes, preuves, allégations '
                                         'et données administratives, et dit ce qui n’a pas été lu.',
    'builtin.metier109.conclusions_adverses': 'Tableau contradictoire des conclusions adverses (prétention, moyen, pièce, faiblesse, réplique) ; '
                                              'mesure aussi la solidité adverse au lieu d’amplifier chaque faiblesse.',
    'builtin.metier109.strategie_contentieuse': 'Compare les options et les objections des deux parties ; recommandations conditionnelles, '
                                                'chacune reliée à ses sources.',
    'builtin.metier109.simulation_decision': 'Prépare un avis contradictoire sur l’issue possible ; les probabilités restent au service statistique, '
                                             'jamais un pourcentage inventé par le modèle.',
    'builtin.metier109.assignation_generale': 'Qualifie la demande avant la trame d’une assignation : juridiction, mentions et délais vérifiés ; '
                                              'aucune voie de saisine universelle.',
    'builtin.metier109.assignation_jex': 'Branche dédiée au juge de l’exécution : mesure, titre exécutoire et objet contesté, avec les textes utiles.',
    'builtin.metier109.assignation_refere': 'Distingue les fondements et conditions du référé judiciaire ; aucun référé n’est traité comme une urgence unique.',
    'builtin.metier109.assignation_commerce': 'Contentieux commercial et tribunal des activités économiques : juridiction, ressort, objet et régime '
                                              'identifiés avant les mentions.',
    'builtin.metier109.requete_speciale': 'Requêtes devant le juge des contentieux de la protection, les prud’hommes ou en tutelle : branches et '
                                          'pièces propres à chaque saisine.',
    'builtin.metier109.comptes_annuels': 'Contrôle des documents d’approbation des comptes selon la forme et les statuts ; chiffres contrôlés, '
                                         'modèles réellement installés.',
    'builtin.metier109.politique_rgpd': 'Cadre une politique de confidentialité à partir des traitements réels ; signale les ressources absentes.',
    'builtin.metier109.relecture_francaise': 'Corrections de langue ciblées ; préserve citations, montants, faits, qualifications et renvois. '
                                             'Ne certifie pas le contenu juridique.',
    'builtin.metier109.rentabilite': 'Budget et rentabilité d’un dossier à partir de la convention, du catalogue et des calculs du logiciel ; '
                                     'estimation, temps passé, facture et règlement restent distincts.',
}
PHASES_LIBELLES = {'decision': 'décision', 'analyse': 'analyse', 'redaction_courriel': 'rédaction d’un courriel',
                   'redaction_document': 'rédaction d’un document', 'controle': 'contrôle'}


def _consigne(text):
    return ' '+re.sub(r'[^a-z0-9]+',' ',fold(str(text or ''))).strip()+' '


def usage_admis(manifest, text, phase=''):
    """Ne pas choisir un parcours de création pour une simple pièce citée.

    Filtre de méthode seulement ; l'autorisation d'action reste dans le service.
    Le texte est la consigne réelle, pas le contenu du document examiné.
    5.6.109 (lot K) : en phase d'analyse, une méthode de création (``CREATION``) exige une consigne de préparation.
    """
    sid=manifest.get('id','')
    if sid in CREATION and phase == 'analyse':
        hay = _consigne(text)
        if not any(' ' + v + ' ' in hay for v in VERBES_CREATION):
            return False
    if not sid.startswith('builtin.parcours109.'):
        return True
    hay=' '+re.sub(r'[^a-z0-9]+',' ',fold(str(text))).strip()+' '
    if sid.endswith(('inpi', 'infogreffe', 'carpa_rib', 'pappers_documents', 'international')):
        # Demande de parcours explicite, jamais un mandat lu dans une pièce.
        verbs = ('connecte', 'connecter', 'prepare', 'preparer', 'effectue', 'effectuer',
                 'recupere', 'recuperer', 'telecharge', 'telecharger', 'obtiens', 'obtenir',
                 'demande', 'demander', 'commande', 'commander', 'remplis', 'remplir')
        if sid.endswith('international'):
            verbs+=('consulte', 'consulter', 'recherche', 'rechercher')
        return any(' '+v+' ' in hay for v in verbs)
    verbs=(('prepare','preparer','redige','rediger','reponds','repondre','cree','creer','joins','joindre','propose','proposer')
           if sid.endswith('offre_brouillon') else
           ('connecte','connecter','recupere','recuperer','telecharge','telecharger','consulte','consulter','collecte','collecter'))
    return any(' '+v+' ' in hay for v in verbs)


def candidats(desk, text, phase, kind, rows):
    from . import competences5632 as c
    normaliser=lambda x:re.sub(r'[^a-z0-9]+',' ',fold(str(x))).strip()
    hay = ' ' + normaliser(text or '') + ' '
    scored = []
    for s in rows:
        if not s['id'].startswith(('builtin.metier109.', 'builtin.parcours109.')):
            continue
        m = json.loads(c._row(desk, s['id'], s['version'])['manifest'])
        if (m.get('role') != 'specialisation' or c.perimee(m)
                or phase not in (m.get('allowed_phases') or []) or not usage_admis(m,text,phase)):
            continue
        tags = [normaliser(x) for x in m.get('auto_select_tags') or [] if x]
        hits = sorted({t for t in tags if re.search(r'(?<![a-z0-9])'+re.escape(t)+r'(?![a-z0-9])', hay)})
        cible = kind in (m.get('target_kinds') or [])
        if not cible and not hits:
            continue
        # Les expressions longues/spécifiques l'emportent sur « assignation ».
        score = (int(cible), max((len(t.split()) for t in hits), default=0), len(hits))
        raison = 'Complément du type de travail « '+kind+' »' if cible else 'Expressions métier : '+', '.join(hits[:5])
        scored.append((score, s['id'], {'id':s['id'],'version':s['version'],
                       'mode':'specialisation','reason':raison}))
    scored.sort(key=lambda x:(tuple(-v for v in x[0]), x[1]))
    return [x[2] for x in scored]


def carte(s):
    """5.6.109 (lot K, F06) : bloc de la carte d'une méthode complémentaire dans l'écran Compétences existant (``competences5632.carte56108``).

    Dit ce qu'elle fait, à quelles étapes elle peut servir, qu'elle complète la méthode générale sans la remplacer et qu'elle est désactivée
    tant que l'avocat ne l'active pas (bouton « Activer » de la carte). Texte échappé ; aucune écriture, aucun script."""
    if s.get('role') != 'specialisation':
        return ''
    sid = str(s.get('id') or '')
    fait = DESCRIPTIONS.get(sid) or str(s.get('description') or '')
    phases = ', '.join(PHASES_LIBELLES.get(p, p) for p in s.get('phases_permises') or [])
    etat = ('Activée par le cabinet : elle peut compléter la méthode générale aux étapes indiquées.' if s.get('enabled') else
            ('Proposée à l’activation, désactivée : rien n’est transmis au modèle tant que vous ne l’activez pas.' if s.get('activation_suggeree') else
             'Désactivée : à activer seulement après relecture, pour les dossiers concernés.'))
    creation = ('<p class="f5632-hint">Méthode de création d’un acte : en analyse, elle ne sert que si la consigne demande de préparer l’acte.</p>'
                if sid in CREATION else '')
    return ('<div class="k56109-methode"><p><strong>Ce qu’elle fait :</strong> <span>%s</span></p><p class="f5632-hint">Étapes : %s.</p>'
            '<p class="f5632-hint">Elle complète la méthode générale du cabinet (Assistant juridique par défaut) sans la remplacer ; deux méthodes au plus '
            'par travail. Elle n’accorde aucun droit, aucun accès ni aucune action.</p>%s<p class="k5632-partial">%s</p></div>') % (
        escape(fait), escape(phases or '—'), creation, escape(etat))
