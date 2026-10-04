"""A small in-memory IMAP server double with the imaplib calls AxiorHub uses."""
from datetime import datetime, timezone
import imaplib
import re


class FakeIMAP:
    """Shared state lives on the class so every Mailbox connection sees one server."""
    folders = {}
    validity = {}
    next_uid = {}
    log = []
    trash = 'Trash'

    @classmethod
    def reset(cls):
        cls.folders = {'INBOX': {}, 'Drafts': {}, 'Sent': {}, 'Trash': {}}
        cls.validity = {k: '7' for k in cls.folders}
        cls.next_uid = {k: 1 for k in cls.folders}
        cls.log = []

    @classmethod
    def add(cls, folder, raw, flags=(), when=None):
        uid = cls.next_uid[folder]
        cls.next_uid[folder] += 1
        cls.folders[folder][uid] = {'raw': raw, 'flags': set(flags),
                                    'date': when or datetime(2026, 10, 2, 9, 0, tzinfo=timezone.utc)}
        return uid

    def __init__(self, host, port=993, ssl_context=None, timeout=0):
        self.selected = None
        self.readonly = True
        self.capabilities = ('IMAP4REV1', 'UIDPLUS')

    def login(self, user, password):
        return 'OK', [b'ok']

    def logout(self):
        return 'BYE', [b'bye']

    @staticmethod
    def _name(value):
        value = value.strip('"')
        return value.replace('&-', '&')

    def select(self, folder, readonly=False):
        name = self._name(folder)
        if name not in self.folders:
            return 'NO', [b'missing']
        self.selected, self.readonly = name, readonly
        self.log.append(('EXAMINE' if readonly else 'SELECT', name))
        return 'OK', [b'1']

    def response(self, name):
        if name == 'UIDVALIDITY':
            return name, [self.validity[self.selected].encode()]
        return name, [None]

    def list(self):
        return 'OK', [b'(\\HasNoChildren) "/" "INBOX"', b'(\\HasNoChildren \\Drafts) "/" "Drafts"',
                      b'(\\HasNoChildren \\Trash) "/" "Trash"']

    def _msgs(self):
        return self.folders[self.selected]

    @staticmethod
    def _headers(raw):
        head = raw.split(b'\r\n\r\n', 1)[0].split(b'\n\n', 1)[0]
        return head.decode('utf-8', 'replace')

    def _match(self, uid, item, criteria):
        i = 0
        while i < len(criteria):
            c = criteria[i].upper() if isinstance(criteria[i], str) else criteria[i]
            if c == 'UNDELETED':
                if '\\Deleted' in item['flags']:
                    return False
            elif c == 'UNSEEN':
                if '\\Seen' in item['flags']:
                    return False
            elif c == 'HEADER':
                key, value = criteria[i + 1], criteria[i + 2].strip('"')
                found = re.search(r'(?im)^' + re.escape(key) + r':\s*(.*)$', self._headers(item['raw']))
                if not found or value.lower() not in found[1].lower():
                    return False
                i += 2
            elif c in ('SINCE', 'TO', 'OR'):
                i += 1 if c != 'OR' else 0
            i += 1
        return True

    def uid(self, command, *args):
        command = command.upper()
        self.log.append(('UID', command) + tuple(str(a)[:60] for a in args))
        if command == 'SEARCH':
            criteria = list(args[1:])
            found = [str(u) for u, item in sorted(self._msgs().items()) if self._match(u, item, criteria)]
            return 'OK', [' '.join(found).encode()]
        if command == 'FETCH':
            wanted = []
            for part in str(args[0]).split(','):
                if ':' in part:
                    a, b = part.split(':')
                    wanted += [u for u in self._msgs() if int(a) <= u <= (int(b) if b != '*' else 10**12)]
                else:
                    wanted.append(int(part))
            query = args[1]
            out = []
            for u in wanted:
                item = self._msgs().get(u)
                if not item:
                    continue
                stamp = item['date'].strftime('%d-%b-%Y %H:%M:%S %z')
                flags = ' '.join(sorted(item['flags']))
                if 'RFC822.SIZE' in query and 'BODY' not in query:
                    out.append(('%d (UID %d FLAGS (%s) INTERNALDATE "%s" RFC822.SIZE %d)' % (
                        u, u, flags, stamp, len(item['raw']))).encode())
                elif 'HEADER.FIELDS' in query:
                    head = self._headers(item['raw']).encode() + b'\r\n\r\n'
                    meta = ('%d (UID %d FLAGS (%s) INTERNALDATE "%s" RFC822.SIZE %d BODY[HEADER.FIELDS (X)] {%d}' % (
                        u, u, flags, stamp, len(item['raw']), len(head))).encode()
                    out += [(meta, head), b')']
                elif 'BODY.PEEK[HEADER]' in query:
                    head = self._headers(item['raw']).encode() + b'\r\n\r\n'
                    out += [(('%d (BODY[HEADER] {%d}' % (u, len(head))).encode(), head), b')']
                elif 'BODY.PEEK[]' in query:
                    out += [(('%d (BODY[] {%d}' % (u, len(item['raw']))).encode(), item['raw']), b')']
                else:
                    raise AssertionError('Unsupported FETCH ' + query)
            return 'OK', out
        if command == 'STORE':
            if self.readonly:
                return 'NO', [b'read-only']
            self._msgs()[int(args[0])]['flags'].add('\\Deleted')
            return 'OK', [b'done']
        if command == 'EXPUNGE':
            if self.readonly:
                return 'NO', [b'read-only']
            self._msgs().pop(int(args[0]), None)
            return 'OK', [b'done']
        if command == 'COPY':
            target = self._name(args[1])
            raw = self._msgs()[int(args[0])]['raw']
            self.add(target, raw, ('\\Seen',))
            return 'OK', [b'copied']
        raise AssertionError('Unsupported UID command ' + command)

    def append(self, folder, flags, stamp, raw):
        name = self._name(folder)
        uid = self.add(name, raw, [f for f in flags.strip('()').split() if f])
        self.log.append(('APPEND', name))
        return 'OK', [('[APPENDUID %s %d] done' % (self.validity[name], uid)).encode()]
