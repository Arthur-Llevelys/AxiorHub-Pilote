"""5.6.102 (lot S) : signature effective des courriels préparés par AxiorHub — « celle de la messagerie », sinon celle de Paramètres.

Demande de l'avocat : dans le volet de relecture, la signature du cabinet n'apparaissait pas sous le courriel, parce que les réponses
préparées partaient sans elle. Décision : ajouter la signature, en prenant celle de la messagerie quand elle est lisible.

Priorité (``effective``) :
1. ``gmail`` : signature Gmail du compte d'envoi (``users.settings.sendAs``), **mise en cache par la synchronisation Gmail** (travail
   ``gmail5697``, jamais pendant l'affichage d'une page). Aucune portée OAuth n'est ajoutée : si Google refuse la lecture avec la portée
   actuelle (``gmail.readonly``), le refus est noté (« signature Gmail non lisible ») et le repli s'applique.
2. ``webmail`` : signature mise en forme saisie dans le webmail d'AxiorHub (``courrier5632:prefs``). IMAP n'ayant pas de signature
   normalisée, c'est « la signature de la messagerie » d'un compte IMAP.
3. ``parametres`` : ``mail.signature`` (texte) de Paramètres.
4. ``cabinet`` : identité du cabinet (``cabinet560.identity``).
5. ``aucune``.

Le HTML est toujours nettoyé par le nettoyage des courriels (``webmail5628.clean_html`` : liste fermée de balises, aucun script, aucun
attribut hormis les liens sûrs, aucune image) ; le texte est dérivé du HTML quand seul le HTML existe.

Composition (``composer`` / ``poser``) : une seule fonction pour tous les circuits de dépôt ; si le corps contient déjà la signature, elle
n'est pas répétée. La partie HTML (``multipart/alternative``) n'est ajoutée que lorsque la signature a sa propre mise en forme (Gmail ou
webmail) : la signature texte de Paramètres garde un message texte, comme avant. La signature est encadrée dans le HTML par deux
commentaires repères, ce qui permet à « Modifier » (``drafts440.build_edited``) de reconstruire la version HTML au lieu de la perdre.
"""
import re
import time
from datetime import datetime, timezone

from .common import Stop

CLE_GMAIL = 'signature56102:gmail'          # {connexion: {owner, adresse, envoi, html, etat, code, maj, essai}} écrit par la synchronisation
RAFRAICHIR_SECONDES = 6 * 3600              # une lecture de sendAs au plus toutes les six heures par connexion
PERIME_JOURS = 30                           # cache plus ancien : ignoré (connexion probablement arrêtée)
MAX_HTML = 20000
REPERE_DEBUT, REPERE_FIN = '<!--axh-signature-->', '<!--/axh-signature-->'
LIBELLES = {'gmail': 'messagerie Gmail', 'webmail': 'webmail', 'parametres': 'Paramètres', 'cabinet': 'identité du cabinet', 'aucune': 'aucune'}
ETATS_GMAIL = {'lue': 'Signature Gmail lue', 'vide': 'Aucune signature dans Gmail', 'refusee': 'Signature Gmail non lisible (refusée par Google avec '
               'l’autorisation actuelle)', 'erreur': 'Signature Gmail non lue (Google injoignable)'}
LIEN_REGLAGE = '/parametres?rubrique=messagerie#signature-webmail'   # réglage de la signature du webmail (Paramètres › Courriels et accueil)


def _nettoyer(html):
    from .webmail5628 import clean_html
    return clean_html(str(html or '')[:MAX_HTML * 4])[:MAX_HTML]


def _texte_de(html):
    from .webmail5628 import html_to_text
    return html_to_text(html)


def _html_de(texte):
    from .webmail5628 import text_to_html
    return text_to_html(texte)


def _vide(html):
    return not re.sub(r'<[^>]*>|&nbsp;|\s', '', str(html or ''))


def _resultat(source, texte='', html='', propre=False, style='', note=''):
    texte = str(texte or '').replace('\r\n', '\n').strip()
    return {'texte': texte, 'html': html if html else (_html_de(texte) if texte else ''), 'source': source, 'libelle': LIBELLES[source],
            'html_propre': bool(propre and html), 'style': style, 'note': note}


# ------------------------------------------------------------------------------------------------ niveau 1 : Gmail (cache seulement)
def _cache_gmail(desk):
    try:
        valeur = desk.settings(CLE_GMAIL, {}) or {}
    except Exception:   # noqa: BLE001 — base illisible : aucun niveau Gmail, le repli s'applique
        return {}
    return valeur if isinstance(valeur, dict) else {}


def _gmail_actif(desk):
    try:
        return desk.settings('gmail5697:fournisseur', False) is True
    except Exception:   # noqa: BLE001
        return False


def _frais(entree):
    try:
        quand = datetime.fromisoformat(str(entree.get('maj') or ''))
    except ValueError:
        return False
    return (datetime.now(timezone.utc) - quand).days < PERIME_JOURS


def _entree_gmail(desk, owner, compte):
    """Entrée du cache pour le compte d'envoi : ``gmail:<connexion>`` désigne la connexion ; sinon la connexion Gmail dont l'adresse d'envoi
    est l'adresse d'expédition de la messagerie (celle de la personne d'abord). Lecture du seul réglage : aucun appel réseau."""
    if not _gmail_actif(desk):
        return None
    cache = _cache_gmail(desk)
    compte = str(compte or '')
    if compte.startswith('gmail:'):
        entree = cache.get(compte.split(':', 1)[1])
        return entree if isinstance(entree, dict) else None
    adresse = str((getattr(desk, 'c', {}) or {}).get('mail', {}).get('from_address') or '').strip().lower()
    if not adresse:
        return None
    candidates = [e for e in cache.values() if isinstance(e, dict) and str(e.get('envoi') or '').lower() == adresse]
    candidates.sort(key=lambda e: (str(e.get('owner') or '') != str(owner or ''), str(e.get('maj') or '')))
    return candidates[0] if candidates else None


# ------------------------------------------------------------------------------------------------ contrat d'interface (lot K)
def effective(desk, owner, compte=None):
    """Signature qui partira avec un courriel préparé : ``{'texte', 'html', 'source', 'libelle', 'html_propre', 'style', 'note'}``.

    ``source`` ∈ ``gmail``, ``webmail``, ``parametres``, ``cabinet``, ``aucune`` ; ``html`` est toujours nettoyé (dérivé du texte quand la source
    n'a pas de mise en forme, ``html_propre`` faux) ; ``texte`` est dérivé du HTML quand seul le HTML existe. ``note`` dit pourquoi un niveau
    supérieur a été sauté (« signature Gmail non lisible »). Lecture seule : réglages et configuration, jamais d'appel réseau ni d'écriture.
    ``compte`` : ``gmail:<connexion>`` pour un projet Gmail ; ``None`` pour un brouillon de la messagerie IMAP du cabinet."""
    note = ''
    style = ''
    try:
        from .courrier5632 import font_css
        style = font_css(desk)
    except Exception:   # noqa: BLE001 — réglage de police illisible : sans style
        style = ''
    entree = _entree_gmail(desk, owner, compte)
    if entree is not None:
        etat = str(entree.get('etat') or '')
        html = _nettoyer(entree.get('html')) if etat == 'lue' else ''
        if etat == 'lue' and not _vide(html) and _frais(entree):
            return _resultat('gmail', _texte_de(html), html, True, style)
        if etat in ('refusee', 'erreur') and not html:
            note = ETATS_GMAIL[etat]
        elif etat == 'lue' and not _frais(entree):
            note = 'Signature Gmail trop ancienne : non reprise'
    try:
        brut = desk.settings('courrier5632:prefs', None) or {}
    except Exception:   # noqa: BLE001
        brut = {}
    html = _nettoyer(brut.get('signature_html')) if isinstance(brut, dict) and brut.get('signature_html') is not None else ''
    if not _vide(html):
        return _resultat('webmail', _texte_de(html), html, True, style, note)
    texte = str((getattr(desk, 'c', {}) or {}).get('mail', {}).get('signature') or '').strip()
    if texte:
        return _resultat('parametres', texte, note=note)
    try:
        from .cabinet560 import identity
        texte = str(identity(desk).get('signature') or '').strip()
    except Exception:   # noqa: BLE001
        texte = ''
    if texte:
        return _resultat('cabinet', texte, note=note)
    return _resultat('aucune', note=note)


def de_configuration(cfg_mail):
    """Repli sans base (appelants historiques qui ne passent que la configuration de messagerie) : ``mail.signature`` seule."""
    texte = str((cfg_mail or {}).get('signature') or '').strip()
    return _resultat('parametres' if texte else 'aucune', texte)


def pour_config(cfg, owner=None, compte=None):
    """``effective`` pour un circuit qui n'a que la configuration complète (moteur automatique) : base ouverte puis refermée ; une base
    illisible donne la signature de Paramètres."""
    try:
        from .desk import Desk
        desk = Desk(cfg)
    except Exception:   # noqa: BLE001
        return de_configuration(cfg.get('mail'))
    try:
        return effective(desk, owner, compte)
    except Exception:   # noqa: BLE001
        return de_configuration(cfg.get('mail'))
    finally:
        try:
            desk.db.close()
        except Exception:   # noqa: BLE001
            pass


def etat(desk, owner, compte=None):
    """Ligne d'information de Paramètres : source effective, état de la lecture Gmail, lien vers le réglage du webmail. Lecture seule."""
    sig = effective(desk, owner, compte)
    gmail = _entree_gmail(desk, owner, compte)
    return {'source': sig['source'], 'libelle': sig['libelle'], 'note': sig['note'], 'texte': sig['texte'][:400],
            'gmail': ETATS_GMAIL.get(str((gmail or {}).get('etat') or ''), '') if gmail else '', 'lien_webmail': LIEN_REGLAGE}


def mention(sig):
    """Mention discrète du volet : « Signature : messagerie Gmail » (vide s'il n'y a pas de signature)."""
    if not sig or sig.get('source') == 'aucune' or not sig.get('texte'):
        return ''
    return 'Signature : %s' % sig['libelle']


# ------------------------------------------------------------------------------------------------ composition (un seul circuit)
def _lignes(texte):
    return [re.sub(r'\s+', ' ', l).strip() for l in str(texte or '').replace('\r\n', '\n').replace('\r', '\n').split('\n') if l.strip()]


def position(corps, sig):
    """``'fin'`` si le corps se termine déjà par la signature, ``'dedans'`` s'il la contient ailleurs, ``''`` sinon (lignes comparées sans
    tenir compte des espaces)."""
    s = _lignes((sig or {}).get('texte'))
    if not s:
        return ''
    c = _lignes(corps)
    if len(c) >= len(s) and c[-len(s):] == s:
        return 'fin'
    for i in range(len(c) - len(s) + 1):
        if c[i:i + len(s)] == s:
            return 'dedans'
    return ''


def _sans_fin(corps, sig):
    """Corps sans les dernières lignes qui forment la signature (pour la version HTML, où la signature est mise en forme)."""
    s = _lignes(sig.get('texte'))
    lignes = str(corps or '').replace('\r\n', '\n').replace('\r', '\n').rstrip().split('\n')
    reste = len(s)
    while lignes and reste:
        if lignes[-1].strip():
            reste -= 1
        lignes.pop()
    return '\n'.join(lignes).rstrip()


def _paragraphes(texte):
    from .volet56101 import paragraphes
    return paragraphes(texte)


def composer(corps, sig, ajouter=True):
    """``{'texte', 'html'}`` du message : texte = corps + signature (une seule fois) ; html seulement si la signature a sa mise en forme.
    ``ajouter=False`` (« Modifier ») : la signature n'est jamais rajoutée au texte ; le HTML ne la porte que si le texte la contient encore."""
    corps = str(corps or '').replace('\r\n', '\n').strip()
    sig = sig or {}
    texte_sig = str(sig.get('texte') or '').strip()
    pos = position(corps, sig) if texte_sig else ''
    if texte_sig and not pos and ajouter:
        texte = corps + '\n\n' + texte_sig + '\n'
    else:
        texte = corps + '\n'
    html = ''
    if sig.get('html_propre') and sig.get('html') and pos != 'dedans' and (ajouter or pos == 'fin'):
        base = _sans_fin(corps, sig) if pos == 'fin' else corps
        interieur = _paragraphes(base) + REPERE_DEBUT + '<div>' + sig['html'] + '</div>' + REPERE_FIN
        style = str(sig.get('style') or '').replace('"', '&quot;')
        html = '<!doctype html><html><body><div style="%s">%s</div></body></html>' % (style, interieur)
    return {'texte': texte, 'html': html}


def _pieces(attachments):
    """Pièces jointes facultatives : ``[(nom, octets, type)]`` (format de ``fichiers5670.attachments``) ou ``[{'nom'|'name', 'data', 'type'}]`` ;
    10 fichiers et 15 Mo au plus, comme le webmail."""
    out, total = [], 0
    for p in attachments or ():
        if isinstance(p, dict):
            nom, data, kind = p.get('nom') or p.get('name') or p.get('filename'), p.get('data'), p.get('type') or p.get('mime')
        else:
            nom, data, kind = p
        if not isinstance(data, (bytes, bytearray)):
            raise Stop('piece_jointe_illisible')
        nom = re.sub(r'[\\/\r\n\x00]+', '_', str(nom or 'piece'))[:150] or 'piece'
        main, _, sub = str(kind or 'application/octet-stream').partition('/')
        if not re.fullmatch(r'[a-z]+', main or '') or not re.fullmatch(r'[a-z0-9.+-]+', sub or ''):
            main, sub = 'application', 'octet-stream'
        total += len(data)
        out.append((nom, bytes(data), main, sub))
    if len(out) > 10:
        raise Stop('dix_pieces_jointes_maximum')
    if total > 15 * 1024 * 1024:
        raise Stop('pieces_jointes_trop_lourdes_15_mo_maximum')
    return out


def poser(msg, corps, sig, attachments=None, ajouter=True):
    """Corps du message : partie texte (avec la signature), partie HTML si la signature est mise en forme, puis les pièces. Rend ``composer``."""
    pieces = _pieces(attachments)
    c = composer(corps, sig, ajouter)
    msg.set_content(c['texte'])
    if c['html']:
        msg.add_alternative(c['html'], subtype='html')
    for nom, data, main, sub in pieces:
        msg.add_attachment(data, maintype=main, subtype=sub, filename=nom)
    return c


def html_webmail(corps_html, sig):
    """Webmail (``initiatives5636._draft``) : HTML du corps suivi de la signature, sauf si le texte du corps la contient déjà."""
    if not sig or not sig.get('texte') or position(_texte_de(corps_html), sig):
        return corps_html
    return corps_html + '<p><br></p>' + (sig.get('html') or _html_de(sig['texte']))


def _contenu(part):
    try:
        valeur = part.get_content()
    except (LookupError, UnicodeError):
        return ''
    return valeur if isinstance(valeur, str) else ''


def reconstructible(msg):
    """Signature d'un brouillon composé ici (repères présents, HTML identique à celui que ``composer`` produit depuis le texte) ; ``None``
    sinon (HTML venu d'ailleurs : sa mise en forme serait perdue par « Modifier »)."""
    partie_html = msg.get_body(preferencelist=('html',))
    partie_texte = msg.get_body(preferencelist=('plain',))
    if partie_html is None or partie_texte is None:
        return None
    html = _contenu(partie_html).replace('\r\n', '\n')   # fins de ligne du transport SMTP
    m = re.match(r'(?s)^<!doctype html><html><body><div style="([^"]*)">.*' + re.escape(REPERE_DEBUT) + r'<div>(.*)</div>' + re.escape(REPERE_FIN)
                 + r'</div></body></html>\s*$', html)
    if not m:
        return None
    sig_html = m.group(2)
    if _nettoyer(sig_html) != sig_html:
        return None
    sig = {'texte': _texte_de(sig_html), 'html': sig_html, 'html_propre': True, 'style': m.group(1).replace('&quot;', '"'), 'source': 'brouillon'}
    if composer(_contenu(partie_texte), sig, ajouter=False)['html'].strip() != html.strip():
        return None
    return sig


# ------------------------------------------------------------------------------------------------ cache Gmail (synchronisation seulement)
def rafraichir_gmail(desk, row, forcer=False):
    """Appelé par la synchronisation Gmail (travail de fond, jamais pendant un GET) : lit ``sendAs`` au plus toutes les six heures et met la
    signature du compte d'envoi en cache. Un refus de Google (403 avec ``gmail.readonly``) est noté ``refusee`` ; une panne garde la valeur
    précédente. Ne lève jamais : la synchronisation des courriels ne dépend pas de la signature."""
    try:
        from . import google5697 as gg
        conn = str(row['id'])
        cache = _cache_gmail(desk)
        avant = cache.get(conn) if isinstance(cache.get(conn), dict) else {}
        maintenant = time.time()
        if not forcer and avant and maintenant - float(avant.get('essai') or 0) < RAFRAICHIR_SECONDES:
            return avant
        adresse = str(row.get('adresse') or '').lower()
        entree = {'owner': str(row.get('owner') or ''), 'adresse': adresse, 'envoi': avant.get('envoi') or adresse, 'html': avant.get('html') or '',
                  'etat': avant.get('etat') or '', 'code': '', 'maj': avant.get('maj') or '', 'essai': maintenant}
        try:
            out = gg.appeler(desk, conn, 'GET', 'https://gmail.googleapis.com/gmail/v1/users/me/settings/sendAs', capacite='gmail.lecture')
            liste = [x for x in out.get('sendAs') or [] if isinstance(x, dict)]
            choisi = (next((x for x in liste if str(x.get('sendAsEmail') or '').lower() == adresse), None)
                      or next((x for x in liste if x.get('isDefault')), None) or next((x for x in liste if x.get('isPrimary')), None))
            html = _nettoyer((choisi or {}).get('signature') or '')
            entree.update({'envoi': str((choisi or {}).get('sendAsEmail') or adresse).lower(), 'html': html,
                           'etat': 'lue' if not _vide(html) else 'vide', 'maj': datetime.now(timezone.utc).isoformat(timespec='seconds')})
        except gg.ErreurGoogle as error:
            if 400 <= error.statut < 500 and error.statut not in (408, 429):   # refus certain (403 attendu avec gmail.readonly) ; 408/429/5xx : panne
                entree.update({'etat': 'refusee', 'html': '', 'code': str(error).split(':')[0], 'maj': datetime.now(timezone.utc).isoformat(timespec='seconds')})
            else:
                entree.update({'code': str(error).split(':')[0], 'etat': entree['etat'] or 'erreur'})
        except Stop as error:
            code = str(error).split(':')[0]
            if code in ('google_capacite_non_accordee', 'google_reconnexion_requise'):
                entree.update({'etat': 'refusee', 'html': '', 'code': code, 'maj': datetime.now(timezone.utc).isoformat(timespec='seconds')})
            else:
                entree.update({'code': code, 'etat': entree['etat'] or 'erreur'})
        cache = _cache_gmail(desk)
        cache[conn] = entree
        desk.setting(CLE_GMAIL, cache)
        return entree
    except Exception:   # noqa: BLE001 — jamais bloquant pour la synchronisation
        return None


def oublier_gmail(desk, conn_id):
    """Connexion arrêtée ou déconnectée : sa signature n'est plus reprise (appelé par la synchronisation, hors GET)."""
    try:
        cache = _cache_gmail(desk)
        if str(conn_id) in cache:
            cache.pop(str(conn_id), None)
            desk.setting(CLE_GMAIL, cache)
    except Exception:   # noqa: BLE001
        pass
