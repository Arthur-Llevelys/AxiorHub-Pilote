"""5.6.43 (cahier v9, § 21) : garde commune du sens des courriels et des destinataires, détection des marqueurs « À COMPLÉTER ».

- **Identités du cabinet** : ``own_addresses`` + adresse d'expédition + identifiant de connexion s'il est une adresse + alias déclarés
  (``mail.aliases``, ``mail.identities``). Normalisation par le parseur d'en-têtes (casse, espaces) ; aucun alias n'est fabriqué en retirant
  des points ou des suffixes.
- **Sens** d'un message : ``received`` (auteur extérieur), ``sent`` (auteur du cabinet, au moins un destinataire extérieur), ``internal``
  (auteur et destinataires du cabinet), ``unknown`` (auteur illisible). Seuls les en-têtes comptent : un client qui cite un ancien courriel du
  cabinet reste un message reçu.
- **Aucune réponse automatique à soi-même** : un message sortant ou interne ne déclenche aucun brouillon automatique ; un brouillon dont
  tous les destinataires principaux sont des adresses du cabinet (ou inexistants) est refusé au dépôt, quel que soit le circuit.
- **5.6.89 (objectif 1)** : ce module reste le service unique d'identification. Il lit les adresses confirmées ou retirées du registre par compte
  (``identity_aliases5689``, chargé avec la configuration) ; ``classer`` donne la classe métier : envoyé par le cabinet, copie à soi-même, tiers, ambigu.
  Aucun domaine n'est assimilé au cabinet : seule une adresse exacte compte, et un auteur du cabinet dont la réponse irait ailleurs reste ambigu.
"""
import re
import sqlite3
from email.utils import getaddresses
from pathlib import Path

from .common import Stop

MARKER = re.compile(r'\[\s*(?:à|a)\s+compl[ée]ter\b[^\]]*\]', re.I)
# 5.6.95 (D04) : mention « [À RELIRE …] » (en-tête du projet, objet) et blocs de variantes « ▸ Variante A … ▸ Fin des variantes » ;
# recherchés sans ancrage de ligne, pour qu'une partie HTML convertie en texte soit contrôlée comme le texte brut.
RELIRE = re.compile(r'\[\s*(?:à|a)\s+relire\b[^\]]*\]', re.I)
VARIANTES = re.compile(r'▸\s*(?:variante\s+[a-z]\b|fin\s+des\s+variantes|choix\s+(?:à|a)\s+faire|proposition\s+(?:à|a)\s+confirmer|'
                       r'fin\s+de\s+la\s+proposition)', re.I)   # v2 : « Proposition à confirmer » (une seule option) bloque aussi
SALUTATIONS = re.compile(r'^\s*(?:madame|monsieur|ma(?:î|i)tre|cher|ch(?:è|e)re|bonjour)[^\n]*$|'
                         r'je vous prie[^\n]*$|bien (?:à vous|cordialement)[^\n]*$|cordialement[^\n]*$', re.I | re.M)


def _norm(value):
    """Adresse normalisée ; une valeur « Nom <adresse> » est analysée (5.6.56), jamais réécrite (pas d'alias inventé)."""
    raw = str(value or '').strip()
    if '<' in raw:
        found = [a for _, a in getaddresses([raw]) if '@' in str(a or '')]
        raw = found[0] if found else raw
    return raw.strip().lower()


def identities(mail_cfg):
    """Adresses du cabinet et leur origine (affichées par le diagnostic)."""
    found = {}
    for value in mail_cfg.get('own_addresses', []) or []:
        if '@' in str(value):
            found.setdefault(_norm(value), 'own_addresses')
    for key in ('from_address', 'username'):
        value = _norm(mail_cfg.get(key))
        if '@' in value:
            found.setdefault(value, key)
    for key in ('aliases', 'identities'):
        for value in mail_cfg.get(key, []) or []:
            value = value.get('address') if isinstance(value, dict) else value
            if '@' in str(value or ''):
                found.setdefault(_norm(value), key)
    for value in mail_cfg.get('identites_confirmees', []) or []:     # 5.6.89 : registre par compte
        found.setdefault(_norm(value), 'confirmee')
    for value in retirees(mail_cfg):
        found.pop(value, None)
    return found


def retirees(mail_cfg):
    """5.6.89 : adresses retirées du registre : une reprise ancienne erronée se corrige sans toucher au fichier de configuration."""
    return {_norm(value) for value in mail_cfg.get('identites_retirees', []) or []}


def _lignes(rows):
    confirmees = sorted({r[0] for r in rows if r[1] == 'confirmee'})
    return confirmees, sorted({r[0] for r in rows if r[1] == 'retiree'} - set(confirmees))


def _poser(config, rows):
    mail = config.setdefault('mail', {})
    mail['identites_confirmees'], mail['identites_retirees'] = _lignes(rows)
    mail['etat5689'] = str(config.get('state_dir') or '')


def load_config_identites(config):
    """5.6.89 : registre lu avec la configuration (même chemin que les réglages d'IA) ; base absente ou registre pas encore créé : rien ne change."""
    path = Path(str(config.get('state_dir') or '')) / 'desk.sqlite3'
    if not config.get('state_dir') or not path.is_file():
        return
    db = None
    try:
        db = sqlite3.connect(path.resolve().as_uri() + '?mode=ro', uri=True, timeout=2)
        _poser(config, db.execute('SELECT address,status FROM identity_aliases5689').fetchall())
    except sqlite3.Error:
        return
    finally:
        if db is not None:
            db.close()


def appliquer_identites(desk):
    """5.6.89 : registre rechargé dans la configuration de la requête ou du travail en cours : un changement vaut tout de suite."""
    try:
        _poser(desk.c, desk.db.execute('SELECT address,status FROM identity_aliases5689').fetchall())
    except sqlite3.OperationalError:
        return


def _addresses(header):
    return [_norm(a) for _, a in getaddresses([str(header or '')]) if '@' in str(a or '')]


def direction(mail, mail_cfg):
    get_all = getattr(mail.msg, 'get_all', None)
    authors = [a for value in (get_all('From', []) if get_all else [mail.msg.get('From', '')]) for a in _addresses(value)]
    if len(set(authors)) > 1:
        return 'unknown'                                          # 5.6.89 : plusieurs auteurs : identité ambiguë, jamais une demande à traiter
    author = authors[0] if authors else _norm(getattr(mail, 'sender', ''))
    sens = classify(author, mail.msg.get('To', ''), mail.msg.get('Cc', ''), mail_cfg)
    if sens in ('sent', 'internal'):
        own = identities(mail_cfg)
        if any(a not in own for a in _addresses(mail.msg.get('Reply-To', ''))):
            return 'unknown'                                      # 5.6.89 : auteur du cabinet, réponse attendue ailleurs : à regarder, ni envoi certain ni demande
    return sens


def classify(author, to, cc, mail_cfg):
    """Sens d'un message d'après ses SEULS en-têtes (jamais d'après une citation dans le corps)."""
    own = identities(mail_cfg)
    author = _norm(author)
    if '@' not in author:
        return 'unknown'
    if author not in own:
        return 'received'
    recipients = _addresses(to) + _addresses(cc)
    if recipients and all(r in own for r in recipients):
        return 'internal'
    return 'sent'


CLASSES = {'received': 'tiers', 'sent': 'envoye_cabinet', 'internal': 'copie_soi', 'unknown': 'ambigu'}
CLASSES_LIBELLES = {'tiers': 'Tiers', 'envoye_cabinet': 'Envoyé par le cabinet', 'copie_soi': 'Copie à soi-même', 'ambigu': 'Identité ambiguë'}


def classer(mail, mail_cfg):
    """5.6.89 : classe métier d'un message : ``tiers``, ``envoye_cabinet``, ``copie_soi`` ou ``ambigu``. Seul ``tiers`` peut appeler une réponse."""
    return CLASSES[direction(mail, mail_cfg)]


ORIGINS = {'own_addresses': 'Autres adresses du cabinet (réglage)', 'from_address': 'Adresse d’expédition', 'username': 'Identifiant de connexion',
           'aliases': 'Alias', 'identities': 'Identité déclarée', 'confirmee': 'Adresse confirmée par le cabinet'}
DIRECTIONS = {'received': 'reçu (un projet de réponse peut être préparé)', 'sent': 'envoyé par le cabinet (aucune réponse automatique)',
              'internal': 'interne ou copie personnelle (aucune réponse)', 'unknown': 'identité ambiguë ou expéditeur illisible (aucune réponse automatique)'}


def identities_html(mail_cfg, args=None):
    """5.6.56 (audit 5.6.55, § 3.1) : « Vos adresses reconnues par l'agent », leur provenance, et un test de classification."""
    from html import escape as e
    rows = ''.join('<li><strong>%s</strong> — %s</li>' % (e(a), e(ORIGINS.get(o, o))) for a, o in sorted(identities(mail_cfg).items()))
    args = args or {}
    test = ''
    if args.get('test_de'):
        verdict = classify(str(args.get('test_de'))[:200], str(args.get('test_a') or '')[:400], '', mail_cfg)
        test = '<p role="status">Message de %s à %s : <strong>%s</strong>.</p>' % (e(str(args['test_de'])[:200]), e(str(args.get('test_a') or '—')[:400]),
                                                                              e(DIRECTIONS.get(verdict, verdict)))
    return ('<section class="ax-card" id="identites5656"><h2>Vos adresses reconnues par l’agent</h2><p class="ax-muted">Un message écrit depuis l’une de '
            'ces adresses est un envoi du cabinet : l’agent n’y répond jamais automatiquement et ne la propose jamais comme correspondant d’un dossier. '
            'Un confrère du même domaine n’est pas assimilé au cabinet. Les adresses se règlent dans Connexions › Courriels.</p><ul>%s</ul>'
            '<form method="get" class="crm-search"><input type="hidden" name="rubrique" value="messagerie"><label>Expéditeur<input name="test_de" '
            'type="email" maxlength="200" required></label><label>Destinataire<input name="test_a" type="email" maxlength="200"></label>'
            '<button class="ax-btn ghost" type="submit">Tester la classification</button></form>%s</section>') % (
        rows or '<li class="ax-muted">Aucune adresse du cabinet configurée.</li>', test)


def check_recipients(to, cc, mail_cfg):
    """Destinataires principaux d'un brouillon : au moins une adresse extérieure, sinon refus explicite."""
    own = identities(mail_cfg)
    primary = _addresses(to)
    if not primary:
        raise Stop('destinataire_absent')
    if all(a in own for a in primary):
        raise Stop('reponse_a_soi_meme_refusee')
    return primary


def blocage_envoi(subject, *texts):
    """5.6.95 (D04, D06) : motif de refus d'envoi, ou chaîne vide. Marqueur « [À COMPLÉTER » (ancien ou nouveau), bloc de variantes non
    choisi, mention « [À RELIRE » : dans l'objet OU dans n'importe quel texte du message. Retirer la mention de l'objet ne suffit donc jamais."""
    every = [str(subject or '')] + [str(t or '') for t in texts]
    if any(MARKER.search(t) for t in every):
        return 'brouillon_a_completer'
    if any(VARIANTES.search(t) for t in every):
        return 'brouillon_variante_a_choisir'
    if any(RELIRE.search(t) for t in every):
        return 'brouillon_a_relire'
    return ''


def redaction_status(body):
    """'complete', 'partial' (marqueurs ponctuels) ou 'needs_validation' (le fond lui-même est un marqueur).
    5.6.95 : un bloc de variantes non choisi n'est pas « complet » : 'partial' (le reste du courriel est rédigé)."""
    text = str(body or '')
    markers = MARKER.findall(text)
    if not markers:
        return 'partial' if VARIANTES.search(text) else 'complete'
    rest = MARKER.sub('', text)
    rest = SALUTATIONS.sub('', rest)
    substantive = re.sub(r'\s+', ' ', rest).strip()
    if len(substantive) < 160 or any(re.search(r'fond|réponse|reponse', m, re.I) for m in markers):
        return 'needs_validation'
    return 'partial'


CABINET_NOTE = 'Message envoyé par le cabinet — pas de réponse automatique.'
