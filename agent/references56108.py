"""5.6.108 (lot R) : petits modèles — le programme garde la main ; le petit modèle lit et rédige.

Constat du banc d'essai (18/11/2026, modèles Ollama du réseau privé du cabinet, situations fictives) : les consignes seules n'empêchent pas un
petit modèle de citer de mémoire des articles et des décisions absents des sources, ni de mal choisir les outils et connecteurs. Quatre mesures
structurelles, décidées par le cabinet, s'appliquent aux seuls **petits modèles** :

1. **Citer par numéro de source.** Les sources transmises sont numérotées ([S1], [S2]…) ; la référence exacte et le lien restent côté programme.
   Après génération, chaque [Sn] valide est remplacé par la référence et le lien de la source ; un [Sn] inconnu est retiré et compté.
2. **Barrière après rédaction.** Extraction déterministe des références du texte produit (articles de codes, « L. 1234-5 », « art. 1231-5
   C. civ. », pourvois, décisions et leurs dates), comparaison aux sources fournies ; une référence inconnue donne **une seule** reprise bornée
   par le modèle, puis ce qui reste est marqué « [référence non vérifiée] » dans le texte et dans la trace — jamais présenté comme vérifié.
3. **Recherche avant rédaction, faite par le programme.** Les articles et décisions repérés dans les pièces fournies (et la grille du lot I)
   sont recherchés d'office sur les connecteurs autorisés (requête minimale et anonymisée de ``juridique5649``) ; mode local strict : aucune
   requête, limite annoncée. Les textes trouvés deviennent des sources numérotées. Borné en nombre et en durée, annulable (pause).
4. **Besoins de recherche au lieu d'appels d'outils.** Une étape courte à sortie contrainte par schéma rend ``{"recherches": [...]}`` (au plus
   5) ; le programme valide chaque entrée (types et champs fermés), exécute lui-même la recherche, et la rédaction reçoit les résultats comme
   sources numérotées. Aucun nom d'outil, de script ni de connecteur n'est envoyé à un petit modèle.

« Petit modèle » : modèle **local** dont la fenêtre déclarée est de 16 384 jetons ou moins, ou dont ``parameter_size`` (Ollama ``/api/show``)
est de 14 B ou moins ; réglage administrateur ``references56108:petit_modele`` : ``auto`` (défaut), ``oui`` (toujours), ``non`` (jamais).
Les modèles plus capables gardent le fonctionnement actuel.

**Point d'entrée du banc d'essai** : ``produire(desk, kind, args, appeler_modele, connecteurs) -> dict`` enchaîne recherche d'office, besoins,
rédaction, barrière et reprise, avec un transport de modèle et des connecteurs injectés (voir sa documentation).
"""
from contextlib import contextmanager
from datetime import datetime, timezone
import json
from pathlib import Path
import re
import sqlite3
import threading
import time

from .common import Stop, fold

SETTING = 'references56108:petit_modele'
REGLAGES = ('auto', 'oui', 'non')
SEUIL_CONTEXTE = 16_384            # jetons
SEUIL_PARAMETRES = 14.0            # milliards de paramètres
MAX_BESOINS = 5
MAX_D_OFFICE = 5
MAX_RECHERCHES = 8                 # recherches exécutées par le programme pour un travail (d'office + besoins)
MAX_PAR_RECHERCHE = 2              # textes retenus par recherche
DELAI_RECHERCHES = 45.0            # secondes, pour l'ensemble des recherches d'un travail
TEXTE_SOURCE_MAX = 1500
MARQUE = '[référence non vérifiée]'
TYPES = ('article', 'decision', 'notion')
CHAMPS_BESOIN = {'article': ('type', 'code', 'numero'), 'decision': ('type', 'numero', 'termes'), 'notion': ('type', 'termes')}
CONSIGNE = ('Citations : cite une source seulement par son numéro entre crochets, par exemple [S2]. N’écris de mémoire aucun numéro '
            'd’article, de pourvoi ni de décision : le programme ajoute lui-même la référence exacte et le lien de chaque source citée. '
            'Si aucune source ne couvre un point de droit, écris-le sans référence.')
SYSTEME_BESOINS = ('Avant la rédaction, indique les textes et les décisions qu’il faudrait lire pour traiter la demande. Réponds uniquement '
                   'par un objet JSON {"recherches": [...]} d’au plus 5 entrées. Chaque entrée a un « type » : « article » (avec « code », le nom '
                   'du code, et « numero »), « decision » (avec « numero » de pourvoi ou de décision, ou « termes ») ou « notion » (avec « termes », '
                   'quelques mots). Aucune autre clé. Aucun nom de personne ni de société. Liste vide si rien n’est utile.')
SCHEMA_BESOINS = {'type': 'object', 'additionalProperties': False, 'required': ['recherches'], 'properties': {'recherches': {
    'type': 'array', 'maxItems': MAX_BESOINS, 'items': {'type': 'object', 'additionalProperties': False, 'required': ['type'], 'properties': {
        'type': {'type': 'string', 'enum': list(TYPES)}, 'code': {'type': 'string'}, 'numero': {'type': 'string'}, 'termes': {'type': 'string'}}}}}}
SYSTEME_REPRISE = ('Tu relis un texte juridique avant sa remise à l’avocat. Les références listées ci-dessous ne figurent dans aucune source '
                   'fournie. Réécris le texte en entier : retire chacune de ces références ou remplace-la par une formulation impersonnelle '
                   '(« selon la règle applicable », « la jurisprudence retient »), sans ajouter aucune autre référence. Garde tout le reste, '
                   'y compris les numéros de sources entre crochets comme [S1]. Réponds par le seul texte réécrit.')
_local = threading.local()


def _now():
    return datetime.now(timezone.utc).isoformat(timespec='seconds')


CONTROLE = frozenset(('mission_annulee', 'dossier_reserve', 'preparation_suspendue', 'qualite_insuffisante'))


def controle(ex):
    """Arrêt, pause, attente de budget, annulation ou retrait de droits : remonte tel quel (jamais transformé en « aucun résultat »)."""
    from .arret5643 import STOP_CODES
    from .budget_ledger5689 import ATTENTE_CODES
    code = str(ex).split(':')[0]
    return code in STOP_CODES or code in ATTENTE_CODES or code in CONTROLE


# ------------------------------------------------------------------------------------------------ petit modèle
def taille_parametres(valeur):
    """« 8.0B » → 8.0 ; « 500M » → 0.5 ; « 1.2T » → 1200.0 ; illisible → None (milliards de paramètres)."""
    m = re.fullmatch(r'\s*(\d+(?:[.,]\d+)?)\s*([KMBT])\s*', str(valeur or ''), re.I)
    if not m:
        return None
    n = float(m.group(1).replace(',', '.'))
    return n * {'K': 1e-6, 'M': 1e-3, 'B': 1.0, 'T': 1000.0}[m.group(2).upper()]


def reglage(desk=None, cfg=None):
    """Réglage administrateur : ``auto`` (défaut), ``oui`` ou ``non``. Lu sur le bureau, sinon en lecture seule dans la base du ``state_dir``
    de la configuration du modèle (appel au fournisseur, sans bureau). Illisible : ``auto``."""
    valeur = None
    try:
        if desk is not None:
            valeur = desk.settings(SETTING, None)
        else:
            cfg = cfg or {}
            politique = cfg.get('external_policy_config') if isinstance(cfg.get('external_policy_config'), dict) else {}
            etat = str(cfg.get('state_dir') or politique.get('state_dir') or '')
            base = Path(etat) / 'desk.sqlite3' if etat else None
            if base is not None and base.is_file():
                db = sqlite3.connect(base.resolve().as_uri() + '?mode=ro', uri=True, timeout=2)
                try:
                    ligne = db.execute('SELECT value FROM settings WHERE key=?', (SETTING,)).fetchone()
                finally:
                    db.close()
                valeur = json.loads(ligne[0]) if ligne else None
    except (sqlite3.Error, ValueError, TypeError, AttributeError, OSError):
        valeur = None
    return valeur if valeur in REGLAGES else 'auto'


def caracteristiques(modele=None, cfg=None):
    """Fournisseur, fenêtre déclarée (la plus petite connue : réglage ``num_ctx``/``contexte5683`` ou limite lue par ``/api/show``) et taille."""
    cfg = dict(cfg or getattr(modele, 'cfg', None) or {})
    fournisseur = str(getattr(modele, 'provider_type', '') or cfg.get('provider_type') or cfg.get('type') or 'ollama')
    fenetres = []
    for v in (cfg.get('num_ctx'), cfg.get('contexte5683'), getattr(modele, 'context_limit', None)):
        try:
            if v and int(v) > 0:
                fenetres.append(int(v))
        except (TypeError, ValueError):
            pass
    taille = taille_parametres(getattr(modele, 'parameter_size', None) or cfg.get('parameter_size'))
    return {'fournisseur': fournisseur, 'fenetre': min(fenetres) if fenetres else None, 'parametres': taille}


def decision_petit(modele=None, cfg=None, desk=None):
    """(petit, raison) : raison lisible, gardée dans la trace."""
    c = caracteristiques(modele, cfg)
    force = reglage(desk, cfg if cfg is not None else getattr(modele, 'cfg', None))
    if force == 'non':
        return False, 'réglage administrateur : jamais'
    if c['fournisseur'] != 'ollama':
        return False, 'modèle distant'
    if force == 'oui':
        return True, 'réglage administrateur : toujours (modèle local)'
    if c['fenetre'] is not None and c['fenetre'] <= SEUIL_CONTEXTE:
        return True, 'fenêtre de %d jetons' % c['fenetre']
    if c['parametres'] is not None and c['parametres'] <= SEUIL_PARAMETRES:
        return True, '%s B de paramètres' % ('%g' % c['parametres'])
    return False, 'modèle local capable'


def petit_modele(modele=None, cfg=None, desk=None):
    return decision_petit(modele, cfg, desk)[0]


# 5.6.108 (lot R, demande du cabinet du 19/11) : barrière des références proposée aussi aux grands modèles, par un réglage distinct du
# réglage « petit modèle » (qui garde son rôle : version courte, recherche par le programme, besoins au lieu d'outils).
SETTING_BARRIERE = 'references56108:barriere'
BARRIERE_DEFAUT = 'locaux'   # décision du cabinet du 19/11 : tous les modèles locaux ; les fournisseurs d'API seulement sur choix explicite
BARRIERES = {'petits': 'Petits modèles seulement', 'locaux': 'Tous les modèles locaux', 'tous': 'Tous les modèles, y compris les fournisseurs d’API'}


def reglage_barriere(desk=None, cfg=None):
    """``petits``, ``locaux`` (défaut, décision du cabinet du 19/11) ou ``tous`` ; lecture seule (bureau, sinon base du ``state_dir`` en lecture
    seule). Rien d'enregistré (installation existante ou nouvelle) : le défaut s'applique ; un choix explicite déjà enregistré est conservé."""
    valeur = None
    try:
        if desk is not None:
            valeur = desk.settings(SETTING_BARRIERE, None)
        else:
            cfg = cfg or {}
            politique = cfg.get('external_policy_config') if isinstance(cfg.get('external_policy_config'), dict) else {}
            etat = str(cfg.get('state_dir') or politique.get('state_dir') or '')
            base = Path(etat) / 'desk.sqlite3' if etat else None
            if base is not None and base.is_file():
                db = sqlite3.connect(base.resolve().as_uri() + '?mode=ro', uri=True, timeout=2)
                try:
                    ligne = db.execute('SELECT value FROM settings WHERE key=?', (SETTING_BARRIERE,)).fetchone()
                finally:
                    db.close()
                valeur = json.loads(ligne[0]) if ligne else None
    except (sqlite3.Error, ValueError, TypeError, AttributeError, OSError):
        valeur = None
    return valeur if valeur in BARRIERES else BARRIERE_DEFAUT


def decision_barriere(modele=None, cfg=None, desk=None):
    """(appliquée, raison) : un petit modèle a toujours la barrière ; un grand modèle seulement selon le réglage (local, ou aussi API)."""
    petit, raison = decision_petit(modele, cfg, desk)
    if petit:
        return True, 'petit modèle (%s)' % raison
    regle = reglage_barriere(desk, cfg if cfg is not None else getattr(modele, 'cfg', None))
    local = caracteristiques(modele, cfg)['fournisseur'] == 'ollama'
    if regle == 'tous':
        return True, 'réglage « %s »%s' % (BARRIERES['tous'], '' if local else ' : fournisseur d’API, reprise pseudonymisée et comptée dans le budget')
    if regle == 'locaux' and local:
        return True, 'réglage « %s »' % BARRIERES['locaux']
    return False, 'grand modèle hors barrière (réglage « %s »)' % BARRIERES[regle]


MENTION_COUT = ('Barrière des références : une reprise a été demandée au fournisseur d’API (texte pseudonymisé, appel compté dans le budget '
                'commun et dans le coût annoncé).')


def barriere_active(modele=None, cfg=None, desk=None):
    return decision_barriere(modele, cfg, desk)[0]


@contextmanager
def envoi(modele):
    """Juste avant l'assemblage de la méthode (``model._leaf``) : le chargeur de compétences sait si l'appel part vers un petit modèle (aucun nom
    d'outil, de script ni de connecteur dans ce qui lui est transmis)."""
    avant = getattr(_local, 'petit', None)
    try:
        _local.petit = petit_modele(modele)
    except Exception:   # noqa: BLE001 — détection impossible : on applique la règle la plus prudente
        _local.petit = True
    try:
        yield _local.petit
    finally:
        _local.petit = avant


def petit_en_cours():
    return bool(getattr(_local, 'petit', None))


@contextmanager
def sans_methode():
    """Étapes auxiliaires (besoins de recherche, reprise) : la méthode du travail n'y est pas jointe ; seule la rédaction la reçoit (et la trace)."""
    from . import competences5632 as c32
    ctx = c32._ctx
    avant = (getattr(ctx, 'text', ''), getattr(ctx, 'plan', None))
    ctx.text, ctx.plan = '', None
    try:
        yield
    finally:
        ctx.text, ctx.plan = avant


# ------------------------------------------------------------------------------------------------ extraction des références
_BARE = re.compile(r'(?<![\w.])([LRD])\.?\s?(\d{1,4}(?:-\d{1,4}){1,4})\b')
_CLAUSE = re.compile(r'\s*(?:,\s*)?(?:alin[ée]a\s*\d+\s*)?(?:du|de la|des|de l.)\s*(?:pr[ée]sent(?:e|s)?\s+)?(?:contrat|pr[ée]sentes|bail|convention|statuts|'
                     r'r[èe]glement|conditions|protocole|accord|acte|pacte|avenant|annexe|cahier|document|projet|devis|offre|lettre)\b', re.I)
_TETE = re.compile(r'^\s*(?:[#*>\-•]+\s*)?(?:\d+[.)]\s*)?$')


def _ligne_de(texte, pos):
    debut = texte.rfind('\n', 0, pos) + 1
    return texte[debut:pos]


def _code_apres(texte, fin):
    from .refs470 import ALIAS_RE, canonical_code
    m = re.match(r'\s*,?\s*(?:du|de la|de l.|des)?\s*(' + ALIAS_RE + r')', texte[fin:fin + 80], re.I)
    return (canonical_code(m.group(1)) or '', fin + m.end()) if m else ('', fin)


def extraire(texte):
    """Références juridiques d'un texte, sans service externe : ``[{cle, type, brut, debut, fin, code, numero}]`` triées par position.

    Faux positifs écartés : dates seules (une date n'est retenue qu'avec une juridiction), numéros de pièces, « RG » sans juridiction, et
    numérotation de clauses (« article 3 du contrat », « Article 1 — Objet » en tête de ligne, numéro d'une ou deux chiffres sans code)."""
    from .refs470 import extract_articles, extract_decisions
    texte = str(texte or '')
    out = []
    for r in extract_articles(texte):
        code, num = r.get('code') or '', str(r.get('number') or '')
        if not num or not re.match(r'^[LRD]?\d', num):
            continue
        if not code:
            suite = texte[r['end']:r['end'] + 60]
            if _CLAUSE.match(suite):
                continue
            if _TETE.match(_ligne_de(texte, r['start'])) and re.match(r'\s*(?:er\b)?\s*(?:[—–:.-]|\*\*|$)', suite):
                continue
            if not re.search(r'[LRD-]', num) and len(re.sub(r'\D', '', num)) < 3:
                continue
        out.append({'cle': 'art:%s:%s' % (code, num), 'type': 'article', 'brut': r['raw'], 'debut': r['start'], 'fin': r['end'], 'code': code, 'numero': num})
    pris = [(x['debut'], x['fin']) for x in out]
    for m in _BARE.finditer(texte):
        if any(a <= m.start() < b for a, b in pris):
            continue
        from .refs470 import normalize_number
        num = normalize_number(m.group(1) + m.group(2))
        code, fin = _code_apres(texte, m.end())
        out.append({'cle': 'art:%s:%s' % (code, num), 'type': 'article', 'brut': texte[m.start():fin], 'debut': m.start(), 'fin': fin, 'code': code, 'numero': num})
    for r in extract_decisions(texte):
        if r.get('pourvoi'):
            cle = 'pourvoi:' + r['pourvoi']
        elif r.get('ce_number'):
            cle = 'ce:' + r['ce_number']
        elif r.get('cc_number'):
            cle = 'cc:' + re.sub(r'\s+', '', r['cc_number']).upper()
        elif r.get('rg'):
            cle = 'rg:' + r['rg']
        elif r.get('date'):
            cle = 'date:%s:%s' % (r.get('court') or '', r['date'])
        else:
            continue
        brut = str(r.get('raw') or '')
        # La décision s'arrête à son dernier élément identifiant (numéro, date) : la marque se place juste après, pas au milieu de la phrase.
        from .refs470 import DATE_NUM, DATE_TEXT, POURVOI, RG
        bouts = [m.end() for rx in (DATE_TEXT, DATE_NUM, POURVOI, RG, re.compile(r'n[°o]\s*\d{4,7}(?:-\d{2,4}\s*[A-Z]{1,4})?')) for m in rx.finditer(brut)]
        brut = brut[:max(bouts)] if bouts else brut.rstrip(' ,;')
        fin = r['start'] + len(brut)
        out.append({'cle': cle, 'type': 'decision', 'brut': brut.rstrip(' ,;'), 'debut': r['start'], 'fin': fin, 'code': r.get('court') or '', 'numero': cle.split(':', 1)[1]})
    out.sort(key=lambda x: (x['debut'], x['fin']))
    return out


class Connues:
    """Références des sources fournies (et du contexte : consigne, dossier), normalisées pour la comparaison."""

    def __init__(self, textes):
        self.articles, self.numeros, self.decisions, self.dates = set(), set(), set(), set()
        self.brut = fold(' '.join(str(t or '') for t in textes))
        for t in textes:
            for r in extraire(t):
                if r['type'] == 'article':
                    self.articles.add((r['code'], r['numero']))
                    self.numeros.add(r['numero'])
                else:
                    self.decisions.add(r['cle'])
                    if r['cle'].startswith('date:'):
                        self.dates.add(r['cle'].rsplit(':', 1)[1])

    def connue(self, ref):
        if ref['type'] == 'article':
            if (ref['code'], ref['numero']) in self.articles:
                return True
            if not ref['code']:                       # cité sans code : le même numéro dans une source suffit
                return ref['numero'] in self.numeros
            return ('', ref['numero']) in self.articles   # source sans code, citation avec code : même numéro
        cle = ref['cle']
        if cle in self.decisions:
            return True
        if cle.startswith('date:'):                   # décision citée par sa date : même date dans une décision des sources
            return cle.rsplit(':', 1)[1] in self.dates
        if cle.startswith('rg:'):                     # numéro de rôle du dossier lui-même (consigne, fiche) : jamais une citation inventée
            return fold(ref['numero']) in self.brut
        return False


def inconnues(texte, connues):
    return [r for r in extraire(texte) if not connues.connue(r)]


def marquer(texte, refs):
    """« [référence non vérifiée] » juste après chaque occurrence d'une référence inconnue (une seule marque par emplacement)."""
    texte = str(texte or '')
    fins = sorted({r['fin'] for r in refs}, reverse=True)
    for fin in fins:
        if texte[fin:fin + len(MARQUE) + 1].lstrip().startswith(MARQUE):
            continue
        texte = texte[:fin] + ' ' + MARQUE + texte[fin:]
    return texte


# ------------------------------------------------------------------------------------------------ sources numérotées
_SN = re.compile(r'\[\s*([A-Z]\d{1,3}(?:\s*[,;]\s*[A-Z]?\d{1,3})*)\s*\]')


def citation(source):
    """Référence et lien d'une source, au format des citations du cabinet : « (référence, lien) » ; une pièce garde son numéro."""
    ref = str(source.get('reference') or '').strip()
    lien = str(source.get('lien') or source.get('url') or '').strip()
    if not ref:
        return ''
    if lien and re.match(r'^https://[^\s<>"]+$', lien):
        return '(%s, %s)' % (ref, lien)
    return '(%s)' % ref


def remplacer_numeros(texte, table):
    """(texte, valides, inconnus) : chaque [Sn] valide d'une source juridique devient sa référence et son lien ; un [Sn] de pièce reste tel quel
    (la carte du travail liste les pièces) ; un [Sn] inconnu est retiré et compté."""
    valides, inconnus = [0], [0]

    def un(m):
        ids, garder, refs = [], [], []
        prefixe = ''
        for brut in re.split(r'\s*[,;]\s*', m.group(1)):
            ident = brut if re.match(r'^[A-Z]', brut) else (prefixe + brut)
            prefixe = re.match(r'^([A-Z])', ident).group(1)
            ids.append(ident)
        for ident in ids:
            s = table.get(ident)
            if s is None:
                inconnus[0] += 1
                continue
            valides[0] += 1
            c = citation(s)
            if c:
                refs.append(c)
            else:
                garder.append(ident)
        return (('[%s]' % ', '.join(garder)) if garder else '') + ((' ' if garder else '') + ' '.join(refs) if refs else '')
    if not _SN.search(str(texte or '')):
        return str(texte or ''), 0, 0
    out = _SN.sub(un, str(texte or ''))
    out = re.sub(r'[ \t]+([.,)])', r'\1', re.sub(r'[ \t]{2,}', ' ', out))   # espace laissée par un retrait ; « : » et « ; » gardent la leur
    return out, valides[0], inconnus[0]


def bloc_sources(table):
    """Sources présentées au modèle : numéro, titre et texte ; la référence n'est rappelée que si elle figure déjà dans le texte de la source."""
    lignes = []
    for ident, s in table.items():
        titre = str(s.get('titre') or '').strip()
        lignes.append('[%s] %s\n%s' % (ident, titre, str(s.get('texte') or '').strip()))
    return '\n\n'.join(lignes)


def numeroter(pieces, trouvees):
    """Table ordonnée {identifiant: source} : les pièces gardent leur identifiant (S1, D1…), les textes trouvés prennent les numéros suivants."""
    table = {}
    for p in pieces or []:
        ident = str(p.get('id') or '')
        if ident and ident not in table:
            table[ident] = dict(p, texte=str(p.get('texte') or p.get('excerpt') or ''))
    n = max([int(m.group(1)) for k in table for m in [re.match(r'^S(\d+)$', k)] if m] + [0])
    for t in trouvees or []:
        n += 1
        table['S%d' % n] = dict(t, id='S%d' % n)
    return table


# ------------------------------------------------------------------------------------------------ besoins de recherche
def _numero_article(valeur):
    from .refs470 import normalize_number
    v = re.sub(r'^(?:art(?:icle)?\.?\s*)', '', str(valeur or '').strip(), flags=re.I)
    if not re.fullmatch(r'[LRD]?\.?\s?\d{1,4}(?:\s?-\s?\d{1,4}){0,4}', v, re.I):
        return ''
    return normalize_number(v.replace(' ', ''))


def valider_besoins(data, maximum=MAX_BESOINS):
    """(besoins, ignorés) : seules les entrées aux types et champs fermés sont gardées ; tout le reste est ignoré et compté. Au plus ``maximum``."""
    from .refs470 import canonical_code
    brut = data.get('recherches') if isinstance(data, dict) else None
    if not isinstance(brut, list):
        return [], (1 if data not in (None, {}) else 0)
    out, ignores, vus = [], 0, set()
    for x in brut:
        if len(out) >= maximum:
            ignores += 1
            continue
        if not isinstance(x, dict) or x.get('type') not in TYPES or set(x) - set(CHAMPS_BESOIN[x['type']]):
            ignores += 1
            continue
        if any(not isinstance(v, str) for v in x.values()):
            ignores += 1
            continue
        termes = re.sub(r'\s+', ' ', str(x.get('termes') or '')).strip()
        if x['type'] == 'article':
            code, num = canonical_code(x.get('code') or ''), _numero_article(x.get('numero'))
            if not code or not num:
                ignores += 1
                continue
            entree = {'type': 'article', 'code': code, 'numero': num}
        elif x['type'] == 'decision':
            num = re.sub(r'\s+', '', str(x.get('numero') or ''))
            pourvoi = re.fullmatch(r'(\d{2})-?(\d{2})\.?(\d{3})', num)
            if pourvoi:
                entree = {'type': 'decision', 'numero': '%s-%s.%s' % pourvoi.groups()}
            elif re.fullmatch(r'\d{5,7}', num):
                entree = {'type': 'decision', 'numero': num}
            elif 3 <= len(termes) <= 120 and not num:
                entree = {'type': 'decision', 'termes': termes}
            else:
                ignores += 1
                continue
        else:
            if not (3 <= len(termes) <= 120) or not re.search(r'[A-Za-zÀ-ÿ]{3}', termes):
                ignores += 1
                continue
            entree = {'type': 'notion', 'termes': termes}
        cle = json.dumps(entree, sort_keys=True, ensure_ascii=False)
        if cle not in vus:
            vus.add(cle)
            out.append(entree)
    return out, ignores


def d_office(textes, maximum=MAX_D_OFFICE):
    """Articles (avec leur code) et décisions (avec leur numéro) cités dans les pièces fournies et la grille : recherchés d'office."""
    out, vus = [], set()
    for t in textes:
        for r in extraire(t):
            if r['type'] == 'article' and r['code']:
                entree = {'type': 'article', 'code': r['code'], 'numero': r['numero']}
            elif r['type'] == 'decision' and r['cle'].split(':')[0] in ('pourvoi', 'ce'):
                entree = {'type': 'decision', 'numero': r['numero']}
            else:
                continue
            cle = json.dumps(entree, sort_keys=True)
            if cle not in vus:
                vus.add(cle)
                out.append(entree)
            if len(out) >= maximum:
                return out
    return out


def requete(besoin):
    """Requête minimale d'un besoin (aucun nom : les termes passent ensuite par l'anonymisation de ``juridique5649``)."""
    if besoin['type'] == 'article':
        return 'article %s %s' % (besoin['numero'], besoin['code'])
    if besoin['type'] == 'decision' and besoin.get('numero'):
        return 'décision pourvoi %s' % besoin['numero'] if '-' in besoin['numero'] else 'décision n° %s' % besoin['numero']
    return str(besoin.get('termes') or '')


def textes_grille(grille):
    g = grille if isinstance(grille, dict) else {}
    return [str(x.get('texte') or '') for cle in ('faits', 'preuves') for x in g.get(cle) or [] if isinstance(x, dict)]


# ------------------------------------------------------------------------------------------------ recherche par le programme
class ConnecteursProduit:
    """Connecteurs autorisés du cabinet, par le chemin existant (``juridique5649.chercher_reference`` : connecteurs MCP et connexions
    juridiques vérifiées, requête minimale anonymisée, politique des données, cache, journal). Mode local strict : aucune requête."""

    def __init__(self, desk, matter='', purpose='redaction'):
        self.desk, self.matter, self.purpose = desk, str(matter or ''), purpose

    def disponible(self):
        from .selection_sources5697 import local_strict
        if local_strict(self.desk):
            return False, 'mode local strict : aucune recherche externe, les textes non fournis restent à vérifier'
        from .juridique5649 import connectors
        try:
            from .connexions5652 import available_tools
            direct = any(t['outil'] in ('jurisprudence.rechercher', 'droit.rechercher_documentation', 'droit.rechercher_texte')
                         for t in available_tools(self.desk, 'cabinet'))
        except Exception:   # noqa: BLE001
            direct = False
        if not direct and not any(c['utilise'] for c in connectors(self.desk)):
            return False, 'aucun connecteur juridique utilisable : les textes non fournis restent à vérifier'
        return True, ''

    def chercher(self, besoin):
        from .juridique5649 import chercher_reference
        return chercher_reference(self.desk, self.matter, besoin, self.purpose)


def _disponible(connecteurs):
    f = getattr(connecteurs, 'disponible', None)
    if f is None:
        return True, ''
    try:
        r = f() if callable(f) else f
    except Exception as ex:   # noqa: BLE001
        return False, 'connecteurs indisponibles (%s)' % type(ex).__name__
    return (bool(r[0]), str(r[1] or '')) if isinstance(r, tuple) else (bool(r), '')


def rechercher(desk, besoins, connecteurs, maximum=MAX_RECHERCHES, delai=DELAI_RECHERCHES, horloge=time.monotonic):
    """Exécute les recherches (dans l'ordre : d'office, puis besoins), bornées en nombre et en durée ; annulable (``pause5629.checkpoint``).
    Rend ``(textes, bilan)`` ; une panne d'un connecteur est comptée, jamais présentée comme « aucun texte »."""
    bilan = {'demandees': len(besoins), 'faites': 0, 'echecs': 0, 'sans_resultat': 0, 'non_faites': 0, 'limites': []}
    ok, raison = _disponible(connecteurs) if connecteurs is not None else (False, 'aucun connecteur')
    if not ok:
        bilan['non_faites'] = len(besoins)
        if besoins:
            bilan['limites'].append('Recherche par le programme non faite : %s.' % raison)
        return [], bilan
    debut, textes, vues = horloge(), [], set()
    for i, b in enumerate(besoins):
        if i >= maximum or horloge() - debut > delai:
            bilan['non_faites'] += 1
            continue
        if desk is not None:
            from .pause5629 import checkpoint
            checkpoint(desk)                       # arrêt ou pause : la recherche s'interrompt comme tout travail
        try:
            trouves = connecteurs.chercher(b) or []
        except Stop as ex:
            if controle(ex):
                raise
            bilan['echecs'] += 1
            continue
        except Exception:   # noqa: BLE001 — une panne de connecteur n'arrête pas la rédaction
            bilan['echecs'] += 1
            continue
        bilan['faites'] += 1
        retenus = 0
        for t in trouves:
            if not isinstance(t, dict) or not str(t.get('texte') or t.get('titre') or '').strip():
                continue
            cle = fold(str(t.get('reference') or t.get('titre') or ''))
            if cle in vues or retenus >= MAX_PAR_RECHERCHE:
                continue
            vues.add(cle)
            retenus += 1
            textes.append({'titre': str(t.get('titre') or t.get('reference') or '')[:300], 'reference': str(t.get('reference') or '')[:200],
                           'lien': str(t.get('lien') or t.get('url') or '')[:500], 'texte': str(t.get('texte') or '')[:TEXTE_SOURCE_MAX],
                           'statut': str(t.get('statut') or 'piste_a_verifier'), 'nature': 'texte juridique trouvé par le programme'})
        if not retenus:
            bilan['sans_resultat'] += 1
    if bilan['non_faites']:
        bilan['limites'].append('%d recherche(s) non faite(s) : limite de nombre ou de durée atteinte.' % bilan['non_faites'])
    if bilan['echecs']:
        bilan['limites'].append('%d recherche(s) en échec chez le connecteur : les points concernés restent à vérifier.' % bilan['echecs'])
    return textes, bilan


# ------------------------------------------------------------------------------------------------ barrière après rédaction
def message_reprise(texte, refs):
    liste = '\n'.join('- ' + r['brut'] for r in refs[:20])
    return [{'role': 'system', 'content': SYSTEME_REPRISE}, {'role': 'user', 'content': 'Références à retirer :\n%s\n\nTexte :\n%s' % (liste, texte)}]


def barriere(texte, connues, reprendre=None):
    """Références inconnues des sources : une seule reprise (``reprendre(texte, refs) -> texte``), nouvelle extraction, puis marquage.
    Rend le texte et les compteurs (trouvées, inconnues avant, reprise, retirées par la reprise, marquées)."""
    texte = str(texte or '')
    trouvees = extraire(texte)
    avant = [r for r in trouvees if not connues.connue(r)]
    out = {'trouvees': len(trouvees), 'inconnues': len(avant), 'reprises': 0, 'reprise_rejetee': False, 'retirees': 0, 'marquees': 0,
           'refs_inconnues': sorted({r['brut'][:160] for r in avant}), 'refs_marquees': []}
    if avant and reprendre is not None:
        out['reprises'] = 1
        try:
            nouveau = str(reprendre(texte, avant) or '').strip()
        except Stop as ex:
            if controle(ex):
                raise
            nouveau = ''
        except Exception:   # noqa: BLE001 — reprise illisible : le texte d'origine est marqué, rien n'est perdu
            nouveau = ''
        # Une reprise vide ou tronquée (moins d'un tiers du texte) est écartée : le texte d'origine est marqué, rien n'est perdu.
        if nouveau and len(nouveau) >= len(texte) // 3:
            texte = nouveau
        else:
            out['reprise_rejetee'] = True
    reste = [r for r in extraire(texte) if not connues.connue(r)]
    out['retirees'] = max(0, len({r['cle'] for r in avant}) - len({r['cle'] for r in reste if r['cle'] in {a['cle'] for a in avant}}))
    out['marquees'] = len(reste)
    out['refs_marquees'] = [r['brut'][:160] for r in reste]
    out['texte'] = marquer(texte, reste)
    return out


# ------------------------------------------------------------------------------------------------ journal (trace)
def schema(desk):
    """Création idempotente, par le travail qui écrit (jamais par une lecture de page)."""
    desk.db.execute('CREATE TABLE IF NOT EXISTS references56108(id INTEGER PRIMARY KEY AUTOINCREMENT, job_id TEXT NOT NULL DEFAULT \'\', '
                    'mission TEXT NOT NULL DEFAULT \'\', etape TEXT NOT NULL DEFAULT \'\', petit INTEGER NOT NULL, raison TEXT NOT NULL DEFAULT \'\', '
                    'compteurs TEXT NOT NULL DEFAULT \'{}\', limites TEXT NOT NULL DEFAULT \'[]\', at TEXT NOT NULL)')
    desk.db.execute('CREATE INDEX IF NOT EXISTS references56108_job ON references56108(job_id, mission)')


def journaliser(desk, compteurs, job_id='', mission='', etape='', petit=True, raison='', limites=()):
    """Journal : références trouvées, reprises, marquées, recherches, besoins (aucun texte, aucune requête)."""
    try:
        schema(desk)
        desk.db.execute('INSERT INTO references56108(job_id,mission,etape,petit,raison,compteurs,limites,at) VALUES(?,?,?,?,?,?,?,?)',
                        (str(job_id or ''), str(mission or ''), str(etape or '')[:60], 1 if petit else 0, str(raison or '')[:120],
                         json.dumps({k: v for k, v in (compteurs or {}).items() if isinstance(v, (int, float, bool))}),
                         json.dumps(list(limites or [])[:12], ensure_ascii=False), _now()))
        desk.db.commit()
    except sqlite3.Error:   # journal indisponible : le travail continue, rien n'est affirmé
        pass


def phrase(c, raison=''):
    """« Références : … » en français clair, pour le suivi de la mission (à côté de « Méthode transmise ») ; ``raison`` : pourquoi la barrière
    a été appliquée (petit modèle, ou réglage pour un grand modèle)."""
    morceaux = ['%d référence(s) repérée(s) dans le texte produit' % int(c.get('trouvees', 0))]
    if c.get('inconnues'):
        morceaux.append('%d absente(s) des sources' % int(c['inconnues']))
    if c.get('reprises'):
        morceaux.append('une reprise par le modèle%s' % (' (écartée)' if c.get('reprise_rejetee') else ''))
    if c.get('marquees'):
        morceaux.append('%d marquée(s) « %s »' % (int(c['marquees']), MARQUE))
    if c.get('sn_inconnus'):
        morceaux.append('%d numéro(s) de source inconnu(s) retiré(s)' % int(c['sn_inconnus']))
    if c.get('recherches') or c.get('recherches_demandees'):
        morceaux.append('recherches faites par le programme : %d (%d texte(s) ajouté(s) comme sources)' % (int(c.get('recherches', 0)), int(c.get('sources_ajoutees', 0))))
    if c.get('besoins') or c.get('besoins_ignores'):
        morceaux.append('besoins de recherche du modèle : %d retenu(s), %d ignoré(s)' % (int(c.get('besoins', 0)), int(c.get('besoins_ignores', 0))))
    tete = 'Références (barrière appliquée : %s) : ' % raison if raison else 'Références : '
    return tete + ', '.join(morceaux) + '. Aucune référence n’est présentée comme vérifiée par cette étape.'


def lignes_trace(desk, job_id='', mission='', limite=4):
    """Lecture seule (sans table, rien n'est affirmé)."""
    try:
        if not desk.db.execute("SELECT 1 FROM sqlite_master WHERE name='references56108'").fetchone():
            return []
        rows = desk.db.execute('SELECT * FROM references56108 WHERE (job_id=? AND job_id<>\'\') OR (mission=? AND mission<>\'\') ORDER BY id DESC LIMIT ?',
                               (str(job_id or ''), str(mission or ''), int(limite))).fetchall()
    except sqlite3.Error:
        return []
    out = []
    for r in rows:
        try:
            c = json.loads(r['compteurs'] or '{}')
        except ValueError:
            continue
        if r['petit'] or c.get('barriere'):
            out.append(phrase(c, r['raison']))
    return out


# ------------------------------------------------------------------------------------------------ chaîne complète (petit modèle)
def _texte_besoins(consigne, table):
    extraits = '\n\n'.join('[%s] %s\n%s' % (k, s.get('titre', ''), str(s.get('texte') or '')[:800]) for k, s in table.items())
    return 'Demande :\n%s\n\nSources déjà fournies :\n%s' % (consigne, extraits or '(aucune)')


def etape_besoins(appeler, consigne, table, max_tokens=400):
    """Étape courte à sortie contrainte par schéma : (besoins, ignorés, appel_fait). Une sortie illisible donne zéro besoin, comptée."""
    messages = [{'role': 'system', 'content': SYSTEME_BESOINS}, {'role': 'user', 'content': _texte_besoins(consigne, table)}]
    try:
        brut = appeler(messages, max_tokens, SCHEMA_BESOINS)
    except Stop as ex:
        if controle(ex):
            raise
        return [], 1, True
    except Exception:   # noqa: BLE001 — étape illisible : aucun besoin, compté ; la rédaction continue
        return [], 1, True
    try:
        from .chaine5689 import extraire_json
        data = extraire_json(brut)
    except Stop:
        return [], 1, True
    besoins, ignores = valider_besoins(data)
    return besoins, ignores, True


def produire(desk, kind, args, appeler_modele, connecteurs=None):
    """Point d'entrée de la chaîne (produit et banc d'essai) : recherche d'office, besoins, rédaction, barrière et reprise.

    ``args`` : ``consigne`` (demande de l'avocat), ``sources`` (pièces fournies : ``id``, ``titre``, ``texte``, éventuellement ``reference`` et
    ``lien``), ``cadre`` (message système de cadrage), ``entete`` (ligne du dossier, facultative), ``contexte`` (textes connus du dossier : numéro
    de rôle…), ``droit`` (``{'code': 'FR'}`` par défaut), ``grille`` (lot I, facultative), ``max_tokens``, ``petit_modele`` (vrai/faux ; absent :
    détecté par ``cfg``) et ``cfg`` (configuration du modèle : ``num_ctx``, ``parameter_size``…).
    ``appeler_modele(messages, max_tokens, schema=None) -> str`` : transport du modèle (le produit : ``Model`` par la politique, la pause et le
    budget ; le banc : Ollama local ou du réseau privé, mêmes gardes). ``connecteurs`` : objet avec ``chercher(besoin) -> [{reference, titre,
    texte, lien}]`` et facultativement ``disponible() -> (bool, raison)`` ; ``None`` : aucun connecteur.

    Rend ``{'texte', 'texte_modele', 'petit_modele', 'raison', 'sources', 'compteurs', 'appels_modele', 'duree_s', 'limites', 'refs_marquees'}``.
    Un grand modèle garde sa méthode et ses outils (ni consigne de citation, ni recherche par le programme, ni besoins) ; la barrière seule
    s'y ajoute si le réglage « Barrière des références » la prévoit (``barriere`` : vrai/faux imposé par l'appelant, sinon le réglage) :
    une seule reprise, puis marquage. Hors barrière : un seul appel, références mesurées, texte inchangé."""
    debut = time.monotonic()
    args = dict(args or {})
    appels = [0]

    def appeler(messages, max_tokens, schema=None):
        appels[0] += 1
        return appeler_modele(messages, max_tokens, schema)
    consigne = str(args.get('consigne') or '')
    pieces = [dict(s) for s in args.get('sources') or [] if isinstance(s, dict)]
    max_tokens = int(args.get('max_tokens') or 1500)
    if args.get('petit_modele') is None:
        petit, raison = decision_petit(cfg=args.get('cfg') or {}, desk=desk)
    else:
        petit, raison = bool(args['petit_modele']), 'indiqué par l’appelant'
    if args.get('barriere') is not None:
        avec_barriere, raison_barriere = bool(args['barriere']), 'indiquée par l’appelant'
    elif petit:
        avec_barriere, raison_barriere = True, 'petit modèle (%s)' % raison
    else:
        avec_barriere, raison_barriere = decision_barriere(cfg=args.get('cfg') or {}, desk=desk)
    etranger = str((args.get('droit') or {}).get('code') or 'FR').upper() != 'FR'
    c = {'trouvees': 0, 'inconnues': 0, 'reprises': 0, 'retirees': 0, 'marquees': 0, 'sn_valides': 0, 'sn_inconnus': 0, 'besoins': 0,
         'besoins_ignores': 0, 'd_office': 0, 'recherches_demandees': 0, 'recherches': 0, 'recherches_echec': 0, 'sources_ajoutees': 0}
    limites = []
    trouvees = []
    if petit:
        textes_pieces = [str(p.get('texte') or '') for p in pieces] + textes_grille(args.get('grille'))
        offices = [] if etranger else d_office(textes_pieces)
        table0 = numeroter(pieces, [])
        besoins, ignores = [], 0
        if not etranger:
            with sans_methode():
                besoins, ignores, _ = etape_besoins(appeler, consigne, table0)
        else:
            limites.append('Dossier relevant d’un autre droit : aucune recherche d’office sur les sources françaises ; ce droit reste à rechercher.')
        c.update(d_office=len(offices), besoins=len(besoins), besoins_ignores=ignores)
        a_faire, vus = [], set()
        for b in offices + besoins:
            k = json.dumps(b, sort_keys=True, ensure_ascii=False)
            if k not in vus:
                vus.add(k)
                a_faire.append(b)
        trouvees, bilan = rechercher(desk, a_faire, connecteurs) if a_faire else ([], {'faites': 0, 'echecs': 0, 'limites': []})
        limites += bilan['limites']
        c.update(recherches_demandees=len(a_faire), recherches=bilan['faites'], recherches_echec=bilan['echecs'], sources_ajoutees=len(trouvees))
    table = numeroter(pieces, trouvees)
    cadre = str(args.get('cadre') or 'Tu travailles pour un cabinet d’avocats. Réponds en français.')
    systeme = cadre + ('\n\n' + CONSIGNE if petit else '')
    entete = str(args.get('entete') or '')
    utilisateur = 'Consigne de l’avocat :\n%s\n\n%sSources fournies :\n%s' % (consigne, (entete + '\n\n') if entete else '', bloc_sources(table) or '(aucune)')
    texte = appeler([{'role': 'system', 'content': systeme}, {'role': 'user', 'content': utilisateur}], max_tokens, None)
    brut = str(texte or '')
    corpus = [consigne] + [str(x) for x in args.get('contexte') or []] + [
        '%s\n%s\n%s' % (s.get('titre', ''), s.get('reference', ''), s.get('texte', '')) for s in table.values()]
    connues = Connues(corpus)
    refs_marquees = []
    if avec_barriere:   # sources présentées numérotées ([S1]…) : numéros inconnus retirés, valides remplacés après la barrière
        texte, _valides0, inconnus = remplacer_numeros_inconnus(brut, table)
        c['sn_inconnus'] = inconnus

        def reprendre(t, refs):
            with sans_methode():
                return appeler(message_reprise(t, refs), max_tokens, None)
        b = barriere(texte, connues, reprendre)
        c.update(trouvees=b['trouvees'], inconnues=b['inconnues'], reprises=b['reprises'], retirees=b['retirees'], marquees=b['marquees'],
                 reprise_rejetee=b['reprise_rejetee'])
        refs_marquees = b['refs_marquees']
        texte, valides, inconnus2 = remplacer_numeros(b['texte'], table)
        c.update(sn_valides=valides, sn_inconnus=c['sn_inconnus'] + inconnus2)
        if b['marquees']:
            limites.append('%d référence(s) absente(s) des sources, marquée(s) « %s » : à vérifier avant tout usage.' % (b['marquees'], MARQUE))
    else:
        refs = extraire(brut)
        c.update(trouvees=len(refs), inconnues=sum(1 for r in refs if not connues.connue(r)))
    c['appels_modele'] = appels[0]
    c['barriere'] = 1 if avec_barriere else 0
    duree = round(time.monotonic() - debut, 2)
    if desk is not None:
        journaliser(desk, c, args.get('job_id') or '', args.get('mission') or '', kind, petit, raison_barriere if avec_barriere else raison, limites)
    return {'texte': texte, 'texte_modele': brut, 'petit_modele': petit, 'raison': raison, 'barriere': avec_barriere, 'raison_barriere': raison_barriere,
            'compteurs': c, 'appels_modele': appels[0], 'duree_s': duree,
            'limites': limites, 'refs_marquees': refs_marquees,
            'sources': [{'id': k, 'titre': s.get('titre', ''), 'reference': s.get('reference', ''), 'lien': s.get('lien', ''), 'texte': s.get('texte', ''),
                         'trouvee_par_le_programme': s.get('nature') == 'texte juridique trouvé par le programme'} for k, s in table.items()]}


def remplacer_numeros_inconnus(texte, table):
    """Avant la barrière : seuls les [Sn] inconnus sont retirés (les valides restent pour la reprise)."""
    inconnus = [0]

    def un(m):
        ids = [x for x in re.split(r'\s*[,;]\s*', m.group(1))]
        gardes = [x for x in ids if x in table]
        inconnus[0] += len(ids) - len(gardes)
        return ('[%s]' % ', '.join(gardes)) if gardes else ''
    if not _SN.search(str(texte or '')):
        return str(texte or ''), None, 0
    out = _SN.sub(un, str(texte or ''))
    return re.sub(r'[ \t]+([.,)])', r'\1', out), None, inconnus[0]


# ------------------------------------------------------------------------------------------------ montages dans le produit
def preparer_mission(desk, modele, sources, consigne, grille, matter, appeler_besoins, connecteurs=None):
    """Mission d'assistance (``ui_ai5689``) confiée à un petit modèle : recherche d'office et besoins, exécutés par le programme. Rend
    ``{'ajoutees': [sources S…], 'compteurs', 'limites'}`` ; les sources ajoutées portent leur référence et leur lien (gardés côté programme)."""
    connecteurs = connecteurs or ConnecteursProduit(desk, matter, 'redaction')
    pieces = [{'id': s.get('id'), 'titre': s.get('titre', ''), 'texte': s.get('excerpt', '')} for s in sources or []]
    textes = [p['texte'] for p in pieces] + textes_grille(grille)
    offices = d_office(textes)
    with sans_methode():
        besoins, ignores, _ = etape_besoins(appeler_besoins, consigne, numeroter(pieces, []))
    a_faire, vus = [], set()
    for b in offices + besoins:
        k = json.dumps(b, sort_keys=True, ensure_ascii=False)
        if k not in vus:
            vus.add(k)
            a_faire.append(b)
    trouvees, bilan = rechercher(desk, a_faire, connecteurs) if a_faire else ([], {'faites': 0, 'echecs': 0, 'limites': []})
    table = numeroter(pieces, trouvees)
    ajoutees = [dict(s, excerpt=s['texte'], version='', emplacement='Recherche par le programme', matter=str(matter or ''), path='')
                for k, s in table.items() if s.get('nature') == 'texte juridique trouvé par le programme']
    return {'ajoutees': ajoutees, 'limites': bilan['limites'],
            'compteurs': {'d_office': len(offices), 'besoins': len(besoins), 'besoins_ignores': ignores, 'recherches_demandees': len(a_faire),
                          'recherches': bilan['faites'], 'recherches_echec': bilan['echecs'], 'sources_ajoutees': len(ajoutees)}}


def verrouiller_sortie(sortie, sources, contexte, reprendre):
    """Sortie normalisée d'une tâche (``ui_ai5689.normaliser``) : [Sn] inconnus retirés, barrière (une reprise, puis marquage) sur le texte et
    le corps du projet, [Sn] des textes juridiques remplacés par leur référence et leur lien. Rend (sortie, compteurs)."""
    table = numeroter([{'id': s.get('id'), 'titre': s.get('titre', ''), 'texte': s.get('excerpt', ''), 'reference': s.get('reference', ''),
                        'lien': s.get('lien', '')} for s in sources or []], [])
    connues = Connues([str(x) for x in contexte or []] + ['%s\n%s\n%s' % (s.get('titre', ''), s.get('reference', ''), s.get('texte', ''))
                                                          for s in table.values()])
    total = {'trouvees': 0, 'inconnues': 0, 'reprises': 0, 'retirees': 0, 'marquees': 0, 'sn_valides': 0, 'sn_inconnus': 0}
    sortie = dict(sortie)
    limites = list(sortie.get('limites') or [])
    champs = [('texte', sortie)]
    if isinstance(sortie.get('objet'), dict):
        sortie['objet'] = dict(sortie['objet'])
        champs.append(('corps', sortie['objet']))
    for cle, porteur in champs:
        texte, _x, inconnus = remplacer_numeros_inconnus(porteur.get(cle) or '', table)
        b = barriere(texte, connues, reprendre)
        texte, valides, inconnus2 = remplacer_numeros(b['texte'], table)
        porteur[cle] = texte
        for k in ('trouvees', 'inconnues', 'reprises', 'retirees', 'marquees'):
            total[k] += int(b[k])
        total['sn_valides'] += valides
        total['sn_inconnus'] += inconnus + inconnus2
    if total['marquees']:
        limites.append('%d référence(s) absente(s) des sources, marquée(s) « %s » : à vérifier avant tout usage.' % (total['marquees'], MARQUE))
    if total['sn_inconnus']:
        limites.append('%d numéro(s) de source inconnu(s) retiré(s) du texte.' % total['sn_inconnus'])
    sortie['limites'] = limites
    return sortie, total


def besoins_vers_outils(desk, owner, role, question, matter, model, tools):
    """``connexions5652.agent_research`` pour un petit modèle : aucun nom d'outil ne lui est transmis. Il exprime des besoins (schéma fermé) ;
    le programme choisit l'outil de lecture vérifié correspondant et l'exécute par le chemin existant (``call_tool``). Mode local strict :
    aucune requête. Les sources rendues ne portent ni nom d'outil ni nom de connecteur."""
    from .selection_sources5697 import local_strict
    if local_strict(desk) or not tools:
        return []
    from .juridique5649 import neutral_query

    def appeler(messages, max_tokens, schema=None):
        with sans_methode():
            return model.complete(messages, temperature=0, max_tokens=max_tokens, json_schema=schema)
    besoins, _ignores, _ = etape_besoins(appeler, neutral_query(str(question)[:2000]), {})
    permis = {t['outil'] for t in tools}
    from .connexions5652 import call_tool
    sources = []
    for i, b in enumerate(besoins[:3]):
        outil, params = choisir_outil(b, permis)
        if not outil:
            continue
        try:
            out = call_tool(desk, owner, role, outil, params, matter)
        except Stop as ex:
            sources.append({'id': 'T%d' % (i + 1), 'kind': 'recherche_programme', 'erreur': str(ex).split(':')[0]})
            continue
        sources.append({'id': 'T%d' % (i + 1), 'kind': 'recherche_programme', 'date': out['date_lecture'],
                        'resultat': json.dumps(out['resultat'], ensure_ascii=False)[:6000]})
    return sources


def choisir_outil(besoin, permis):
    """(outil, paramètres) choisis par le programme pour un besoin validé ; (None, {}) si aucun outil vérifié ne convient."""
    from .juridique5649 import neutral_query
    if besoin['type'] == 'article':
        for outil in ('droit.lire_article', 'droit.verifier_article'):
            if outil in permis:
                return outil, {'code': besoin['code'], 'number': besoin['numero']}
        if 'droit.rechercher_texte' in permis:
            return 'droit.rechercher_texte', {'query': requete(besoin)}
    elif besoin['type'] == 'decision' and besoin.get('numero'):
        if 'jurisprudence.verifier' in permis and '-' in besoin['numero']:
            return 'jurisprudence.verifier', {'pourvoi': besoin['numero']}
        if 'jurisprudence.rechercher' in permis:
            return 'jurisprudence.rechercher', {'query': requete(besoin)}
    else:
        q = neutral_query(requete(besoin))[:200]
        for outil in ('jurisprudence.rechercher', 'droit.rechercher_documentation', 'droit.rechercher_texte'):
            if outil in permis:
                return outil, {'query': q}
    return None, {}


def verrouiller_reponse(desk, modele, texte, contexte, etape='assistant'):
    """Réponse de l'assistant (``workspace.chat``) : barrière après rédaction quand elle s'applique à ce modèle (petit modèle, ou grand modèle selon
    le réglage « Barrière des références ») : une reprise par le même modèle, sans méthode jointe (budget, politique des données et
    pseudonymisation pour un fournisseur d'API, pause : appliqués par ``Model``), puis marquage. Rend (texte, limites) ; hors barrière : inchangé."""
    if not isinstance(texte, str):
        return texte, []
    actif, raison = decision_barriere(modele, desk=desk)
    if not actif:
        return texte, []

    def reprendre(t, refs):
        with sans_methode():
            return modele.complete(message_reprise(t, refs), temperature=0, max_tokens=1500)
    b = barriere(texte, Connues([str(x) for x in contexte or []]), reprendre)
    c = {k: b[k] for k in ('trouvees', 'inconnues', 'reprises', 'retirees', 'marquees')}
    c['barriere'] = 1
    journaliser(desk, c, getattr(desk, 'active_job_id', '') or '', '', etape, petit_modele(modele, desk=desk), raison)
    limites = ['%d référence(s) absente(s) des sources, marquée(s) « %s » : à vérifier avant tout usage.' % (b['marquees'], MARQUE)] if b['marquees'] else []
    if b['reprises'] and caracteristiques(modele)['fournisseur'] != 'ollama':
        limites.append(MENTION_COUT)
    return b['texte'], limites
