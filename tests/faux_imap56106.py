"""5.6.106 (lot L) : faux serveur IMAP au niveau de la connexion ``imaplib`` (même API que ``imaplib.IMAP4``), plus complet que celui de la
5.6.100 : écritures (STORE, MOVE, COPY, EXPUNGE), dossiers (CREATE, RENAME, DELETE, SUBSCRIBE), séparateur choisi (« / » ou « . »),
dossiers spéciaux (RFC 6154), SEARCH par ensembles d'UID et critères (UNSEEN, FLAGGED, FROM, TO, SUBJECT, BODY, SINCE, BEFORE, HEADER,
OR, NOT), littéral ``CHARSET UTF-8`` (attribut ``literal`` d'imaplib) et SORT facultatif (RFC 5256).

Utilisé par ``tests/test_v56106_liste.py`` et ``scripts/parcours_p106l.py``. Aucun réseau ; messages fictifs (domaines example.test).
L'état du serveur est un objet ``Serveur`` partagé par toutes les connexions ouvertes sur lui ; chaque commande est notée dans
``serveur.journal`` (('UID', 'MOVE', ensemble, cible), ('CREATE', nom)…).
"""
from datetime import datetime, timedelta, timezone
from email import policy
from email.parser import BytesParser
import base64
import re

NOMS = ('DURAND', 'MARTIN', 'BERNARD', 'ZORVAL', 'KERVILAC', 'ORLANDEC')
OBJETS = ('Demande de pièces complémentaires', 'Calendrier de procédure', 'Relance sur la facture', 'Proposition transactionnelle',
          'Convocation à l’audience', 'Questions sur le bail commercial', 'Projet de protocole', 'Rendez-vous au cabinet')
DEBUT = datetime(2026, 11, 2, 8, 0, tzinfo=timezone.utc)


def _mot(valeur):
    return valeur if valeur.isascii() else '=?utf-8?b?' + base64.b64encode(valeur.encode('utf-8')).decode('ascii') + '?='


def message(n, objet=None, expediteur=None, destinataire='cabinet@example.test', corps=None, piece=False):
    """Octets RFC 822 d'un message fictif numéro ``n``."""
    nom = NOMS[n % len(NOMS)]
    objet = objet if objet is not None else '%s — %s %d' % (OBJETS[n % len(OBJETS)], nom, n)
    expediteur = expediteur or '%s <%s.%d@example.test>' % (nom.title(), nom.lower(), n)
    corps = corps if corps is not None else 'Bonjour Maître, message fictif numéro %d au sujet du dossier DOS-001.' % n
    entetes = ['From: ' + _mot(expediteur), 'To: ' + destinataire, 'Subject: ' + _mot(objet), 'Message-ID: <l106-%d@example.test>' % n,
               'Date: Mon, 02 Nov 2026 09:00:00 +0100', 'MIME-Version: 1.0']
    if piece:
        entetes.append('Content-Type: multipart/mixed; boundary="b%d"' % n)
        texte = ('--b%d\r\nContent-Type: text/plain; charset=utf-8\r\n\r\n%s\r\n--b%d\r\nContent-Type: application/pdf\r\n'
                 'Content-Disposition: attachment; filename="piece-%d.pdf"\r\n\r\n%%PDF-1.4 fictif\r\n--b%d--\r\n') % (n, corps, n, n, n)
    else:
        entetes.append('Content-Type: text/plain; charset=utf-8')
        texte = corps + '\r\n'
    return ('\r\n'.join(entetes) + '\r\n\r\n' + texte).encode('utf-8')


def mutf7(nom):
    """Nom IMAP en UTF-7 modifié (RFC 3501 § 5.1.3), pour semer des dossiers accentués."""
    from agent.mailbox import modified_utf7
    return modified_utf7(nom)


class Serveur:
    """État partagé : ``boites`` {nom IMAP encodé: {'uids': {uid: [drapeaux, octets, date]}, 'validite': str, 'suivant': int, 'flags': str}}."""

    def __init__(self, separateur='/', capacites=('IMAP4REV1', 'UIDPLUS', 'MOVE'), speciaux=True):
        self.sep = separateur
        self.capacites = tuple(capacites)
        self.boites = {}
        self.journal = []
        self.abonnes = set()
        self.refus = set()              # commandes refusées (essais d'échec) : {'MOVE', 'STORE', 'CREATE'…}
        for nom, drapeau in (('INBOX', ''), ('Drafts', '\\Drafts'), ('Sent', '\\Sent'), ('Trash', '\\Trash'), ('Junk', '\\Junk'),
                             ('Archives', '\\Archive')):
            if speciaux or nom in ('INBOX', 'Drafts', 'Sent'):
                self.creer(nom, drapeau)

    def creer(self, nom, drapeau='', validite=None):
        self.boites[nom] = {'uids': {}, 'validite': str(validite or (7 + len(self.boites))), 'suivant': 1, 'flags': drapeau}
        return self.boites[nom]

    def ajouter(self, boite, brut, drapeaux=(), quand=None):
        b = self.boites[boite]
        uid = b['suivant']
        b['suivant'] += 1
        b['uids'][uid] = [set(drapeaux), brut, quand or DEBUT + timedelta(minutes=uid)]
        return uid

    def semer(self, n, boite='INBOX', debut=1):
        """``n`` messages fictifs : un sur trois non lu, un sur cinq suivi, un sur sept avec pièce jointe."""
        for i in range(debut, debut + n):
            drapeaux = set() if i % 3 == 0 else {'\\Seen'}
            if i % 5 == 0:
                drapeaux.add('\\Flagged')
            self.ajouter(boite, message(i, piece=(i % 7 == 0)), drapeaux, DEBUT + timedelta(hours=i))

    def connexion(self, *_a, **_k):
        return FauxIMAP(self)


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


class FauxIMAP:
    def __init__(self, serveur):
        self.s = serveur
        self.selection = ''
        self.lecture_seule = True
        self.literal = None
        self._dernier_litteral = None
        self._reponses = {}

    @property
    def capabilities(self):
        return self.s.capacites

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

    def list(self, *_):
        self._note('LIST')
        lignes = []
        noms = sorted(self.s.boites)
        for nom in noms:
            enfants = any(n.startswith(nom + self.s.sep) for n in noms)
            drapeaux = ['\\HasChildren' if enfants else '\\HasNoChildren'] + ([self.s.boites[nom]['flags']] if self.s.boites[nom]['flags'] else [])
            lignes.append(('(%s) "%s" "%s"' % (' '.join(drapeaux), self.s.sep.replace('\\', '\\\\'), nom.replace('"', '\\"'))).encode())
        return 'OK', lignes

    def select(self, boite, readonly=False):
        nom = self._nom(boite)
        self._note('EXAMINE' if readonly else 'SELECT', nom)
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
        if 'CREATE' in self.s.refus or nom in self.s.boites or not nom:
            return 'NO', [b'[ALREADYEXISTS] existe']
        self.s.creer(nom)
        return 'OK', [b'created']

    def rename(self, ancien, nouveau):
        a, n = self._nom(ancien), self._nom(nouveau)
        self._note('RENAME', a, n)
        if a not in self.s.boites or n in self.s.boites or a == 'INBOX':
            return 'NO', [b'refuse']
        for nom in sorted(self.s.boites):
            if nom == a or nom.startswith(a + self.s.sep):
                self.s.boites[n + nom[len(a):]] = self.s.boites.pop(nom)
        return 'OK', [b'renamed']

    def delete(self, boite):
        nom = self._nom(boite)
        self._note('DELETE', nom)
        if nom not in self.s.boites or nom == 'INBOX' or 'DELETE' in self.s.refus:
            return 'NO', [b'refuse']
        if any(n.startswith(nom + self.s.sep) for n in self.s.boites):
            return 'NO', [b'[INUSE] has children']
        del self.s.boites[nom]
        return 'OK', [b'deleted']

    def subscribe(self, boite):
        self.s.abonnes.add(self._nom(boite))
        return 'OK', [b'']

    def unsubscribe(self, boite):
        self.s.abonnes.discard(self._nom(boite))
        return 'OK', [b'']

    def status(self, boite, elements):
        nom = self._nom(boite)
        self._note('STATUS', nom)
        if nom not in self.s.boites:
            return 'NO', [b'inconnu']
        b = self.s.boites[nom]
        non_lus = sum(1 for d, _, _ in b['uids'].values() if '\\Seen' not in d)
        return 'OK', [('"%s" (MESSAGES %d UNSEEN %d UIDVALIDITY %s)' % (nom, len(b['uids']), non_lus, b['validite'])).encode()]

    # ------------------------------------------------------------------ recherche
    def _correspond(self, uid, element, criteres, i):
        """Évalue le critère commençant en ``i`` ; rend (vrai/faux, indice suivant)."""
        drapeaux, brut, quand = element
        c = str(criteres[i]).upper()
        valeur = lambda k: self._nom(criteres[i + k])
        if c == 'ALL':
            return True, i + 1
        if c in ('UNDELETED', 'DELETED', 'SEEN', 'UNSEEN', 'FLAGGED', 'UNFLAGGED'):
            table = {'UNDELETED': ('\\Deleted', False), 'DELETED': ('\\Deleted', True), 'SEEN': ('\\Seen', True), 'UNSEEN': ('\\Seen', False),
                     'FLAGGED': ('\\Flagged', True), 'UNFLAGGED': ('\\Flagged', False)}[c]
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
        if c in ('SINCE', 'BEFORE'):
            jour = quand.astimezone(timezone.utc).date()
            return (jour >= _date(valeur(1))) if c == 'SINCE' else (jour < _date(valeur(1))), i + 2
        if c in ('SUBJECT', 'FROM', 'TO', 'CC', 'BODY', 'TEXT'):
            if i + 1 >= len(criteres):                      # littéral CHARSET UTF-8 : valeur transmise par ``literal``
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
        raise AssertionError('critère non pris en charge : %s' % c)

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
        boite = self._boite()
        if commande in self.s.refus:
            return 'NO', [b'refuse']
        if commande == 'SEARCH':
            return 'OK', [' '.join(str(u) for u in self._chercher(list(args[1:]) if args and args[0] is None else list(args))).encode()]
        if commande == 'SORT':
            if 'SORT' not in self.s.capacites:
                return 'BAD', [b'SORT inconnu']
            cle = str(args[0]).strip('()').split()
            uids = self._chercher(list(args[2:]))
            inverse = cle[0] == 'REVERSE'
            champ = cle[-1]

            def valeur(u):
                if champ in ('DATE', 'ARRIVAL'):
                    return boite[u][2]
                m = self._entetes(boite[u][1])
                return str(m.get({'FROM': 'From', 'SUBJECT': 'Subject'}[champ], '')).casefold()
            return 'OK', [' '.join(str(u) for u in sorted(uids, key=lambda u: (valeur(u), u), reverse=inverse)).encode()]
        if commande == 'FETCH':
            ensemble, elements = args[0], args[1]
            data = []
            for u in _ensemble(ensemble, list(boite)):
                if u not in boite:
                    continue
                drapeaux, brut, quand = boite[u]
                meta = 'UID %d FLAGS (%s) INTERNALDATE "%s"' % (u, ' '.join(sorted(drapeaux)), quand.strftime('%d-%b-%Y %H:%M:%S +0000'))
                if 'RFC822.SIZE' in elements:
                    meta += ' RFC822.SIZE %d' % len(brut)
                champs = re.search(r'BODY\.PEEK\[HEADER\.FIELDS \(([^)]*)\)\]', elements)
                if champs:
                    noms = {c.lower() for c in champs.group(1).split()}
                    lignes = [ligne for ligne in brut.split(b'\r\n\r\n', 1)[0].decode('ascii').split('\r\n') if ligne.split(':', 1)[0].lower() in noms]
                    morceau = ('\r\n'.join(lignes) + '\r\n\r\n').encode()
                    data += [(('%d (%s BODY[HEADER.FIELDS (%s)] {%d}' % (u, meta, champs.group(1), len(morceau))).encode(), morceau), b')']
                elif 'BODY.PEEK[HEADER]' in elements:
                    morceau = brut.split(b'\r\n\r\n', 1)[0] + b'\r\n\r\n'
                    data += [(('%d (%s BODY[HEADER] {%d}' % (u, meta, len(morceau))).encode(), morceau), b')']
                elif 'BODY.PEEK[]' in elements:
                    data += [(('%d (%s BODY[] {%d}' % (u, meta, len(brut))).encode(), brut), b')']
                else:
                    data.append(('%d (%s)' % (u, meta)).encode())
            return 'OK', data or [None]
        if self.lecture_seule:
            return 'NO', [b'[READ-ONLY] boite en lecture seule']
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
            if commande == 'MOVE' and 'MOVE' not in self.s.capacites:
                return 'BAD', [b'MOVE inconnu']
            cible = self._nom(args[1])
            if cible not in self.s.boites:
                return 'NO', [b'[TRYCREATE] cible absente']
            for u in _ensemble(args[0], list(boite)):
                if u in boite:
                    drapeaux, brut, quand = boite[u]
                    self.s.ajouter(cible, brut, drapeaux - {'\\Deleted'}, quand)
                    if commande == 'MOVE':
                        del boite[u]
            return 'OK', [b'done']
        if commande == 'EXPUNGE':
            for u in _ensemble(args[0], list(boite)):
                if u in boite and '\\Deleted' in boite[u][0]:
                    del boite[u]
            return 'OK', [b'done']
        raise AssertionError('commande UID non prise en charge : ' + commande)

    def expunge(self):
        boite = self._boite()
        for u in [u for u, e in boite.items() if '\\Deleted' in e[0]]:
            del boite[u]
        return 'OK', [b'']
