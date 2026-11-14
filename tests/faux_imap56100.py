"""5.6.100 (lot F) : faux serveur IMAP au niveau de la connexion ``imaplib`` (même API que ``imaplib.IMAP4``), pour compter les commandes.

Utilisé par ``tests/test_v56100_reactivite.py`` et ``scripts/mesure56100.py``. Aucun réseau : les messages sont fictifs (domaines
example.test), produits à la demande. Chaque commande est notée dans ``journal`` (``EXAMINE``, ``SELECT``, ``SEARCH``, ``FETCH``, ``LIST``…) ;
``corps_lus`` compte les messages dont le corps a été demandé (``BODY.PEEK[]`` ou ``RFC822``). ``retard_s`` simule la latence du serveur
(chaque commande), ``retard_connexion_s`` celle de la connexion.
"""
from datetime import datetime, timedelta, timezone
import re
import time

NOMS = ('DURAND', 'MARTIN', 'BERNARD', 'ZORVAL', 'KERVILAC', 'ORLANDEC')
OBJETS = ('Demande de pièces complémentaires', 'Calendrier de procédure', 'Relance sur la facture', 'Proposition transactionnelle',
          'Convocation à l’audience', 'Questions sur le bail commercial', 'Projet de protocole', 'Rendez-vous au cabinet')


def message(n, objet=None, expediteur=None, piece=False):
    """Octets RFC 822 d'un message fictif numéro ``n``."""
    nom = NOMS[n % len(NOMS)]
    objet = objet if objet is not None else '%s — %s %d' % (OBJETS[n % len(OBJETS)], nom, n)
    expediteur = expediteur or '%s <%s.%d@example.test>' % (nom.title(), nom.lower(), n)
    entetes = ['From: ' + expediteur, 'To: cabinet@example.test', 'Subject: ' + objet, 'Message-ID: <m%d@example.test>' % n,
               'Date: Mon, 02 Nov 2026 09:00:00 +0100', 'MIME-Version: 1.0', 'X-Bruit: ' + 'x' * 400]
    if piece:
        entetes.append('Content-Type: multipart/mixed; boundary="b%d"' % n)
        corps = '--b%d\r\nContent-Type: text/plain; charset=utf-8\r\n\r\nBonjour %d\r\n--b%d--\r\n' % (n, n, n)
    else:
        entetes.append('Content-Type: text/plain; charset=utf-8')
        corps = 'Bonjour, message fictif %d.\r\n' % n + 'Texte du corps. ' * 200
    entetes = [_mot_encode(ligne) for ligne in entetes]
    return ('\r\n'.join(entetes) + '\r\n\r\n' + corps).encode('utf-8')


def _mot_encode(ligne):
    """En-tête non ASCII encodé en RFC 2047 (un seul mot encodé, sans repli de ligne), comme le transmet un vrai serveur."""
    nom, _, valeur = ligne.partition(': ')
    if valeur.isascii():
        return ligne
    import base64
    return nom + ': =?utf-8?b?' + base64.b64encode(valeur.encode('utf-8')).decode('ascii') + '?='


def _encode_entete(brut):
    return brut.split(b'\r\n\r\n', 1)[0].decode('ascii').split('\r\n')


class FauxIMAP:
    """Connexion ``imaplib`` simulée. ``boites`` : {nom: {uid(int): (drapeaux(set), octets)}} ; ``validites`` : {nom: UIDVALIDITY}."""

    def __init__(self, boites=None, validites=None, retard_s=0.0, retard_connexion_s=0.0):
        self.boites = boites if boites is not None else {'INBOX': {}}
        self.validites = validites or {}
        self.retard_s = retard_s
        self.journal = []
        self.corps_lus = 0
        self.selection = ''
        self._reponses = {}
        if retard_connexion_s:
            time.sleep(retard_connexion_s)

    # ------------------------------------------------------------------ outils
    @classmethod
    def boite_de(cls, n, debut=1, piece_tous=7, **kw):
        """Boîte de réception de ``n`` messages fictifs (UID ``debut``…``debut+n-1``, un sur ``piece_tous`` avec pièce jointe)."""
        instant = datetime(2026, 11, 2, 8, 0, tzinfo=timezone.utc)
        inbox = {}
        for i in range(debut, debut + n):
            inbox[i] = ({'\\Seen'} if i % 3 else set(), message(i, piece=(i % piece_tous == 0)), instant + timedelta(minutes=i))
        return cls({'INBOX': inbox, 'Drafts': {}, 'Sent': {}}, **kw)

    def compte(self, commande):
        return sum(1 for c in self.journal if c == commande)

    def _pause(self, nom):
        self.journal.append(nom)
        if self.retard_s:
            time.sleep(self.retard_s)

    # ------------------------------------------------------------------ API imaplib
    def login(self, *_):
        self._pause('LOGIN')
        return 'OK', [b'Logged in']

    def logout(self):
        self.journal.append('LOGOUT')
        return 'BYE', [b'']

    def list(self, *_):
        self._pause('LIST')
        lignes = []
        for nom in self.boites:
            drapeau = {'Drafts': '\\Drafts', 'Sent': '\\Sent'}.get(nom, '')
            lignes.append(('(\\HasNoChildren%s) "." "%s"' % ((' ' + drapeau) if drapeau else '', nom)).encode())
        return 'OK', lignes

    def select(self, boite, readonly=False):
        self._pause('EXAMINE' if readonly else 'SELECT')
        nom = boite[1:-1] if boite.startswith('"') else boite
        if nom not in self.boites:
            return 'NO', [b'inconnu']
        self.selection = nom
        self._reponses['UIDVALIDITY'] = [str(self.validites.get(nom, 7)).encode()]
        return 'OK', [str(len(self.boites[nom])).encode()]

    def response(self, code):
        return code, self._reponses.pop(code, [None])

    def uid(self, commande, *args):
        commande = commande.upper()
        self._pause(commande)
        boite = self.boites.get(self.selection, {})
        if commande == 'SEARCH':
            criteres = [a for a in args[1:] if a is not None]
            uids = sorted(boite)
            if 'SUBJECT' in criteres:
                terme = criteres[criteres.index('SUBJECT') + 1].strip('"').lower()
                uids = [u for u in uids if terme in self._entete(boite[u][1]).lower()]
            return 'OK', [' '.join(str(u) for u in uids).encode()]
        if commande == 'FETCH':
            ensemble, elements = args[0], args[1]
            voulus = []
            for morceau in str(ensemble).split(','):
                if ':' in morceau:
                    a, b = morceau.split(':')
                    voulus.extend(u for u in sorted(boite) if int(a) <= u <= int(b))
                elif morceau.strip():
                    voulus.append(int(morceau))
            data = []
            for u in voulus:
                if u not in boite:
                    continue
                drapeaux, brut, quand = (boite[u] + (datetime(2026, 11, 2, 8, 0, tzinfo=timezone.utc),))[:3]
                meta = 'UID %d FLAGS (%s) INTERNALDATE "%s"' % (u, ' '.join(sorted(drapeaux)), quand.strftime('%d-%b-%Y %H:%M:%S +0000'))
                if 'RFC822.SIZE' in elements:
                    meta += ' RFC822.SIZE %d' % len(brut)
                champs = re.search(r'BODY\.PEEK\[HEADER\.FIELDS \(([^)]*)\)\]', elements)
                if champs:
                    noms = {c.lower() for c in champs.group(1).split()}
                    lignes = [ligne for ligne in _encode_entete(brut) if ligne.split(':', 1)[0].lower() in noms]
                    morceau = ('\r\n'.join(lignes) + '\r\n\r\n').encode()
                    data += [(('%d (%s BODY[HEADER.FIELDS (%s)] {%d}' % (u, meta, champs.group(1), len(morceau))).encode(), morceau), b')']
                elif 'BODY.PEEK[HEADER]' in elements:
                    morceau = brut.split(b'\r\n\r\n', 1)[0] + b'\r\n\r\n'
                    data += [(('%d (%s BODY[HEADER] {%d}' % (u, meta, len(morceau))).encode(), morceau), b')']
                elif 'BODY.PEEK[]' in elements or 'RFC822)' in elements:
                    self.corps_lus += 1
                    data += [(('%d (%s BODY[] {%d}' % (u, meta, len(brut))).encode(), brut), b')']
                else:
                    data.append(('%d (%s)' % (u, meta)).encode())
            return 'OK', data or [None]
        return 'OK', [b'']

    def noop(self):
        self._pause('NOOP')
        return 'OK', [b'']

    @staticmethod
    def _entete(brut):
        from email.parser import BytesParser
        from email import policy
        m = BytesParser(policy=policy.default).parsebytes(brut.split(b'\r\n\r\n', 1)[0] + b'\r\n\r\n')
        return str(m.get('Subject', '')) + ' ' + str(m.get('From', ''))
