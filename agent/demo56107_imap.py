"""5.6.107 (lot M) : boîte de démonstration locale — un serveur IMAP simulé, sans réseau, lu dans le dossier de la démo.

Adapté du faux serveur des tests (``tests/faux_imap56106.py``) et déplacé côté produit : même API que ``imaplib.IMAP4`` pour ce dont
``agent.mailbox`` et les pages de messagerie se servent (LOGIN, LIST, SELECT/EXAMINE, STATUS, UID SEARCH/SORT/FETCH/STORE/MOVE/COPY/
EXPUNGE, APPEND, CREATE, RENAME, DELETE, SUBSCRIBE).

Différences avec le faux des tests :
- l'état est **persistant** et partagé entre les processus de la démo (interface et travailleur) : ``<boite>/etat.json`` (dossiers,
  UID, drapeaux, date) et ``<boite>/messages/<sha256>.eml`` (octets des messages). Chaque commande relit l'état ; chaque écriture le
  relit, l'applique et le réécrit atomiquement sous un verrou de fichier ;
- APPEND rend ``[APPENDUID validité uid]`` ; un message déposé dans le dossier « Envoyés (démo) » (drapeau ``\\Sent``) reçoit l'en-tête
  ``X-AxiorHub-Demonstration`` et, en tête de son texte, la mention « Démonstration : rien n'a été envoyé » ;
- une commande inconnue lève ``imaplib.IMAP4.error`` (jamais d'appel réseau de repli).

Aucune socket n'est ouverte : ``connexion(cfg)`` remplace ``agent.mailbox.connect`` pour la configuration de la démo seulement
(``demo56107.armer``). Données fictives uniquement.
"""
from contextlib import contextmanager
from datetime import datetime, timezone
from email import policy
from email.parser import BytesParser
import hashlib
import imaplib
import json
import os
from pathlib import Path
import re
import secrets
import threading

MENTION_ENVOI = 'Démonstration : rien n’a été envoyé.'
ENTETE_DEMO = 'X-AxiorHub-Demonstration'
_VERROU = threading.RLock()


def _horodatage(valeur):
    return datetime.fromisoformat(valeur) if isinstance(valeur, str) else valeur


class Boite:
    """État persistant de la boîte de démonstration (un dossier sur le disque)."""

    def __init__(self, dossier):
        self.dossier = Path(dossier)
        self.messages = self.dossier / 'messages'
        self.chemin_etat = self.dossier / 'etat.json'
        self.boites = {}
        self.journal = []                 # commandes de la connexion (contrôle des essais) ; jamais écrit sur le disque
        self.capacites = ('IMAP4REV1', 'UIDPLUS', 'MOVE')
        self.sep = '/'

    # ------------------------------------------------------------------ disque
    def charger(self):
        try:
            brut = json.loads(self.chemin_etat.read_text(encoding='utf-8'))
        except (OSError, ValueError):
            brut = {'boites': {}}
        self.boites = {}
        for nom, b in (brut.get('boites') or {}).items():
            self.boites[nom] = {'validite': str(b.get('validite') or '1'), 'suivant': int(b.get('suivant') or 1), 'flags': str(b.get('flags') or ''),
                                'uids': {int(u): [set(e[0]), str(e[1]), _horodatage(e[2])] for u, e in (b.get('uids') or {}).items()}}
        return self

    def enregistrer(self):
        self.dossier.mkdir(parents=True, exist_ok=True)
        data = {'boites': {nom: {'validite': b['validite'], 'suivant': b['suivant'], 'flags': b['flags'],
                                 'uids': {str(u): [sorted(e[0]), e[1], e[2].isoformat()] for u, e in sorted(b['uids'].items())}}
                           for nom, b in self.boites.items()}}
        tmp = self.chemin_etat.with_name('etat.%s.tmp' % secrets.token_hex(4))
        tmp.write_text(json.dumps(data, ensure_ascii=False), encoding='utf-8')
        os.replace(tmp, self.chemin_etat)

    @contextmanager
    def transaction(self, ecriture=False):
        """Relecture de l'état ; en écriture, verrou de fichier tenu jusqu'à l'enregistrement (interface et travailleur)."""
        from .portable import fcntl
        with _VERROU:
            self.dossier.mkdir(parents=True, exist_ok=True)
            with open(self.dossier / '.verrou', 'a') as verrou:
                if fcntl is not None:
                    fcntl.flock(verrou, fcntl.LOCK_EX)
                try:
                    self.charger()
                    yield self
                    if ecriture:
                        self.enregistrer()
                finally:
                    if fcntl is not None:
                        fcntl.flock(verrou, fcntl.LOCK_UN)

    def octets(self, sha):
        return (self.messages / (sha + '.eml')).read_bytes()

    def ranger(self, brut):
        sha = hashlib.sha256(brut).hexdigest()
        self.messages.mkdir(parents=True, exist_ok=True)
        cible = self.messages / (sha + '.eml')
        if not cible.exists():
            cible.write_bytes(brut)
        return sha

    # ------------------------------------------------------------------ construction (demo56107.creer)
    def creer(self, nom, drapeau='', validite=None):
        self.boites[nom] = {'uids': {}, 'validite': str(validite or (7 + len(self.boites))), 'suivant': 1, 'flags': drapeau}
        return self.boites[nom]

    def ajouter(self, boite, brut, drapeaux=(), quand=None):
        b = self.boites[boite]
        uid = b['suivant']
        b['suivant'] += 1
        b['uids'][uid] = [set(drapeaux), self.ranger(brut), quand or datetime.now(timezone.utc)]
        return uid


def connexion(cfg):
    """Remplace ``agent.mailbox.connect`` pour la boîte de démonstration (aucune socket)."""
    return FauxIMAP(Boite(cfg['demo56107_boite']))


def _ensemble(texte, existants):
    out = []
    for morceau in str(texte).split(','):
        morceau = morceau.strip()
        if not morceau:
            continue
        if ':' in morceau:
            a, b = morceau.split(':')
            haut = max(existants) if existants else 0
            a = haut if a == '*' else int(a)
            b = haut if b == '*' else int(b)
            out.extend(u for u in sorted(existants) if min(a, b) <= u <= max(a, b))
        else:
            out.append(int(morceau))
    return out


def _date(texte):
    return datetime.strptime(texte.strip('"'), '%d-%b-%Y').date()


def _avec_mention(brut, mention=MENTION_ENVOI):
    """Copie d'un message « envoyé » en démonstration : en-tête et mention en tête du premier texte."""
    m = BytesParser(policy=policy.default).parsebytes(brut)
    if m.get(ENTETE_DEMO):
        return brut
    m[ENTETE_DEMO] = mention
    try:
        part = m.get_body(('plain',))
        if part is not None:
            texte = part.get_content()
            part.set_content(mention + '\n\n' + texte)
    except (LookupError, KeyError, TypeError, ValueError):
        pass
    return m.as_bytes(policy=policy.SMTP)


class FauxIMAP:
    """Connexion simulée ; chaque commande relit l'état persistant de la boîte."""

    def __init__(self, boite):
        self.s = boite
        self.selection = ''
        self.lecture_seule = True
        self.literal = None
        self._dernier_litteral = None
        self._reponses = {}

    @property
    def capabilities(self):
        return self.s.capacites

    def __getattr__(self, nom):
        if nom.startswith('_'):
            raise AttributeError(nom)

        def inconnue(*_a, **_k):
            raise imaplib.IMAP4.error('commande non prise en charge par la boîte de démonstration : ' + nom)
        return inconnue

    # ------------------------------------------------------------------ outils
    def _note(self, *x):
        self.s.journal.append(tuple(str(v)[:200] for v in x))

    @staticmethod
    def _nom(valeur):
        valeur = str(valeur)
        if valeur.startswith('"') and valeur.endswith('"'):
            valeur = valeur[1:-1].replace('\\"', '"').replace('\\\\', '\\')
        return valeur

    def _boite(self):
        return self.s.boites.get(self.selection, {'uids': {}})['uids']

    def _brut(self, element):
        return self.s.octets(element[1])

    @staticmethod
    def _entetes(brut):
        return BytesParser(policy=policy.default).parsebytes(brut.split(b'\r\n\r\n', 1)[0] + b'\r\n\r\n')

    @staticmethod
    def _texte(brut):
        m = BytesParser(policy=policy.default).parsebytes(brut)
        part = m.get_body(('plain',)) if m.is_multipart() else m
        try:
            return part.get_content() if part is not None else ''
        except (LookupError, KeyError):
            return ''

    # ------------------------------------------------------------------ connexion
    def login(self, *_):
        self._note('LOGIN')
        return 'OK', [b'Logged in']

    def logout(self):
        self._note('LOGOUT')
        return 'BYE', [b'']

    def noop(self):
        return 'OK', [b'']

    def close(self):
        return 'OK', [b'']

    def starttls(self, *_a, **_k):
        return 'OK', [b'']

    def list(self, *_):
        self._note('LIST')
        with self.s.transaction():
            lignes = []
            noms = sorted(self.s.boites)
            for nom in noms:
                enfants = any(n.startswith(nom + self.s.sep) for n in noms)
                drapeaux = ['\\HasChildren' if enfants else '\\HasNoChildren'] + ([self.s.boites[nom]['flags']] if self.s.boites[nom]['flags'] else [])
                lignes.append(('(%s) "%s" "%s"' % (' '.join(drapeaux), self.s.sep, nom.replace('"', '\\"'))).encode())
        return 'OK', lignes

    lsub = list

    def select(self, boite, readonly=False):
        nom = self._nom(boite)
        self._note('EXAMINE' if readonly else 'SELECT', nom)
        with self.s.transaction():
            if nom not in self.s.boites:
                return 'NO', [b'inconnu']
            self.selection, self.lecture_seule = nom, readonly
            self._reponses['UIDVALIDITY'] = [self.s.boites[nom]['validite'].encode()]
            return 'OK', [str(len(self.s.boites[nom]['uids'])).encode()]

    def response(self, code):
        return code, self._reponses.pop(code, [None])

    def create(self, boite):
        nom = self._nom(boite)
        self._note('CREATE', nom)
        with self.s.transaction(True):
            if nom in self.s.boites or not nom:
                return 'NO', [b'[ALREADYEXISTS] existe']
            self.s.creer(nom)
        return 'OK', [b'created']

    def rename(self, ancien, nouveau):
        a, n = self._nom(ancien), self._nom(nouveau)
        self._note('RENAME', a, n)
        with self.s.transaction(True):
            if a not in self.s.boites or n in self.s.boites or a == 'INBOX':
                return 'NO', [b'refuse']
            for nom in sorted(self.s.boites):
                if nom == a or nom.startswith(a + self.s.sep):
                    self.s.boites[n + nom[len(a):]] = self.s.boites.pop(nom)
        return 'OK', [b'renamed']

    def delete(self, boite):
        nom = self._nom(boite)
        self._note('DELETE', nom)
        with self.s.transaction(True):
            if nom not in self.s.boites or nom == 'INBOX' or self.s.boites[nom]['flags']:
                return 'NO', [b'refuse']
            if any(n.startswith(nom + self.s.sep) for n in self.s.boites):
                return 'NO', [b'[INUSE] has children']
            del self.s.boites[nom]
        return 'OK', [b'deleted']

    def subscribe(self, boite):
        return 'OK', [b'']

    def unsubscribe(self, boite):
        return 'OK', [b'']

    def status(self, boite, elements):
        nom = self._nom(boite)
        self._note('STATUS', nom)
        with self.s.transaction():
            if nom not in self.s.boites:
                return 'NO', [b'inconnu']
            b = self.s.boites[nom]
            non_lus = sum(1 for d, _, _ in b['uids'].values() if '\\Seen' not in d)
            return 'OK', [('"%s" (MESSAGES %d UNSEEN %d UIDVALIDITY %s UIDNEXT %d)' % (nom, len(b['uids']), non_lus, b['validite'], b['suivant'])).encode()]

    def append(self, boite, drapeaux, quand, brut):
        nom = self._nom(boite)
        self._note('APPEND', nom)
        if isinstance(brut, str):
            brut = brut.encode('utf-8')
        brut = bytes(brut).replace(b'\r\n', b'\n').replace(b'\n', b'\r\n')
        with self.s.transaction(True):
            if nom not in self.s.boites:
                return 'NO', [b'[TRYCREATE] cible absente']
            if self.s.boites[nom]['flags'] == '\\Sent':
                from .demo56107 import mention_envoi   # mention dans la langue de l'instance (marqueur du dossier de la démo)
                brut = _avec_mention(brut, mention_envoi(self.s.dossier.parent))
            liste = [f for f in str(drapeaux or '').strip('()').split() if f]
            uid = self.s.ajouter(nom, brut, liste, datetime.now(timezone.utc))
            validite = self.s.boites[nom]['validite']
        return 'OK', [('[APPENDUID %s %d] done' % (validite, uid)).encode()]

    # ------------------------------------------------------------------ recherche
    def _correspond(self, uid, element, criteres, i):
        drapeaux, _, quand = element
        c = str(criteres[i]).upper()
        valeur = lambda k: self._nom(criteres[i + k])   # noqa: E731
        if c == 'ALL':
            return True, i + 1
        if c in ('UNDELETED', 'DELETED', 'SEEN', 'UNSEEN', 'FLAGGED', 'UNFLAGGED', 'ANSWERED', 'UNANSWERED', 'DRAFT'):
            table = {'UNDELETED': ('\\Deleted', False), 'DELETED': ('\\Deleted', True), 'SEEN': ('\\Seen', True), 'UNSEEN': ('\\Seen', False),
                     'FLAGGED': ('\\Flagged', True), 'UNFLAGGED': ('\\Flagged', False), 'ANSWERED': ('\\Answered', True),
                     'UNANSWERED': ('\\Answered', False), 'DRAFT': ('\\Draft', True)}[c]
            return (table[0] in drapeaux) == table[1], i + 1
        if c == 'NOT':
            ok, j = self._correspond(uid, element, criteres, i + 1)
            return not ok, j
        if c == 'OR':
            a, j = self._correspond(uid, element, criteres, i + 1)
            b, k = self._correspond(uid, element, criteres, j)
            return a or b, k
        if c == 'UID':
            return uid in _ensemble(valeur(1), list(self._boite())), i + 2
        if c in ('SINCE', 'BEFORE', 'ON', 'SENTSINCE', 'SENTBEFORE'):
            jour = quand.astimezone(timezone.utc).date()
            cible = _date(valeur(1))
            ok = jour >= cible if c in ('SINCE', 'SENTSINCE') else (jour < cible if c in ('BEFORE', 'SENTBEFORE') else jour == cible)
            return ok, i + 2
        brut = self._brut(element)
        if c in ('SUBJECT', 'FROM', 'TO', 'CC', 'BODY', 'TEXT'):
            if i + 1 >= len(criteres):
                terme = (self._dernier_litteral or b'').decode('utf-8')
                suivant = i + 1
            else:
                terme, suivant = valeur(1), i + 2
            m = self._entetes(brut)
            champ = {'SUBJECT': str(m.get('Subject', '')), 'FROM': str(m.get('From', '')), 'TO': str(m.get('To', '')), 'CC': str(m.get('Cc', '')),
                     'BODY': self._texte(brut), 'TEXT': str(m) + self._texte(brut)}[c]
            return terme.casefold() in champ.casefold(), suivant
        if c == 'HEADER':
            m = self._entetes(brut)
            return valeur(2).casefold() in str(m.get(valeur(1), '')).casefold(), i + 3
        if re.fullmatch(r'[\d,:*]+', c):
            return uid in _ensemble(c, list(self._boite())), i + 1
        raise imaplib.IMAP4.error('critère non pris en charge : %s' % c)

    def _chercher(self, criteres):
        criteres = [a for a in criteres if a is not None]
        if criteres and str(criteres[0]).upper() == 'CHARSET':
            criteres = criteres[2:]
        uids = []
        for uid, element in sorted(self._boite().items()):
            i, ok = 0, True
            while i < len(criteres) and ok:
                ok, i = self._correspond(uid, element, criteres, i)
            if ok:
                uids.append(uid)
        return uids

    # ------------------------------------------------------------------ UID
    def uid(self, commande, *args):
        commande = commande.upper()
        self._dernier_litteral, self.literal = self.literal, None
        self._note('UID', commande, *args)
        ecriture = commande in ('STORE', 'MOVE', 'COPY', 'EXPUNGE')
        if ecriture and self.lecture_seule:
            return 'NO', [b'[READ-ONLY] boite en lecture seule']
        with self.s.transaction(ecriture):
            return self._uid(commande, args)

    def _uid(self, commande, args):
        boite = self._boite()
        if commande == 'SEARCH':
            return 'OK', [' '.join(str(u) for u in self._chercher(list(args[1:]) if args and args[0] is None else list(args))).encode()]
        if commande == 'SORT':
            cle = str(args[0]).strip('()').split()
            uids = self._chercher(list(args[2:]))
            inverse = cle[0] == 'REVERSE'
            champ = cle[-1]

            def valeur(u):
                if champ in ('DATE', 'ARRIVAL'):
                    return boite[u][2]
                m = self._entetes(self._brut(boite[u]))
                return str(m.get({'FROM': 'From', 'SUBJECT': 'Subject', 'TO': 'To', 'SIZE': 'Subject'}.get(champ, 'Subject'), '')).casefold()
            return 'OK', [' '.join(str(u) for u in sorted(uids, key=lambda u: (valeur(u), u), reverse=inverse)).encode()]
        if commande == 'FETCH':
            ensemble, elements = args[0], args[1]
            data = []
            for u in _ensemble(ensemble, list(boite)):
                if u not in boite:
                    continue
                drapeaux, _, quand = boite[u]
                brut = self._brut(boite[u])
                meta = 'UID %d FLAGS (%s) INTERNALDATE "%s"' % (u, ' '.join(sorted(drapeaux)), quand.strftime('%d-%b-%Y %H:%M:%S +0000'))
                if 'RFC822.SIZE' in elements:
                    meta += ' RFC822.SIZE %d' % len(brut)
                champs = re.search(r'BODY\.PEEK\[HEADER\.FIELDS \(([^)]*)\)\]', elements)
                if champs:
                    noms = {c.lower() for c in champs.group(1).split()}
                    lignes, courante = [], None
                    for ligne in brut.split(b'\r\n\r\n', 1)[0].decode('utf-8', 'replace').split('\r\n'):
                        if ligne[:1] in (' ', '\t'):
                            if courante is not None:
                                courante.append(ligne)
                            continue
                        courante = [ligne] if ligne.split(':', 1)[0].lower() in noms else None
                        if courante is not None:
                            lignes.append(courante)
                    morceau = ('\r\n'.join('\r\n'.join(x) for x in lignes) + '\r\n\r\n').encode('utf-8')
                    data += [(('%d (%s BODY[HEADER.FIELDS (%s)] {%d}' % (u, meta, champs.group(1), len(morceau))).encode(), morceau), b')']
                elif 'BODY.PEEK[HEADER]' in elements or 'RFC822.HEADER' in elements:
                    morceau = brut.split(b'\r\n\r\n', 1)[0] + b'\r\n\r\n'
                    data += [(('%d (%s BODY[HEADER] {%d}' % (u, meta, len(morceau))).encode(), morceau), b')']
                elif 'BODY.PEEK[]' in elements or 'BODY[]' in elements or re.search(r'RFC822(?![.A-Z])', elements):
                    data += [(('%d (%s BODY[] {%d}' % (u, meta, len(brut))).encode(), brut), b')']
                else:
                    data.append(('%d (%s)' % (u, meta)).encode())
            return 'OK', data or [None]
        if commande == 'STORE':
            ensemble, mode, drapeaux = args[0], str(args[1]).upper(), set(str(args[2]).strip('()').split())
            for u in _ensemble(ensemble, list(boite)):
                if u in boite:
                    if mode.startswith('+'):
                        boite[u][0] |= drapeaux
                    elif mode.startswith('-'):
                        boite[u][0] -= drapeaux
                    else:
                        boite[u][0] = set(drapeaux)
            return 'OK', [b'done']
        if commande in ('MOVE', 'COPY'):
            cible = self._nom(args[1])
            if cible not in self.s.boites:
                return 'NO', [b'[TRYCREATE] cible absente']
            for u in _ensemble(args[0], list(boite)):
                if u in boite:
                    drapeaux, sha, quand = boite[u]
                    self.s.ajouter(cible, self.s.octets(sha), drapeaux - {'\\Deleted'}, quand)
                    if commande == 'MOVE':
                        del boite[u]
            return 'OK', [b'done']
        if commande == 'EXPUNGE':
            for u in _ensemble(args[0], list(boite)):
                if u in boite and '\\Deleted' in boite[u][0]:
                    del boite[u]
            return 'OK', [b'done']
        raise imaplib.IMAP4.error('commande UID non prise en charge : ' + commande)

    def expunge(self):
        if self.lecture_seule:
            return 'NO', [b'[READ-ONLY]']
        with self.s.transaction(True):
            boite = self._boite()
            for u in [u for u, e in boite.items() if '\\Deleted' in e[0]]:
                del boite[u]
        return 'OK', [b'']
