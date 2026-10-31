"""5.6.83 (SK01) : lecture sûre de l'en-tête YAML d'une compétence.

Sous-ensemble strict et sans exécution : correspondances imbriquées, listes, listes en ligne, chaînes entre guillemets, blocs ``|`` et ``>``
avec leurs variantes ``-`` et ``+``. Ancres, alias, étiquettes, fusion ``<<`` et clés dupliquées sont refusés : une fiche d'en-tête n'en a pas
besoin, et un fichier importé n'a pas à décider de ce que le lecteur exécute. Taille et profondeur sont bornées.
"""
import re

from .common import Stop

MAX_BYTES = 64_000
MAX_DEPTH = 8
MAX_KEYS = 400
KEY = re.compile(r'^(?:"([^"]+)"|\'([^\']+)\'|([^\s:#\[\]{}&*!|>\'"%@`][^:#]*?))\s*:(?:\s+(.*))?$')


class _Parser:
    def __init__(self, text):
        self.lines = text.split('\n')
        self.i = 0
        self.keys = 0

    def peek(self):
        while self.i < len(self.lines):
            raw = self.lines[self.i]
            if not raw.strip() or raw.lstrip().startswith('#'):
                self.i += 1
                continue
            if '\t' in raw[:len(raw) - len(raw.lstrip())]:
                raise Stop('entete_competence_invalide')
            return len(raw) - len(raw.lstrip(' ')), raw.strip()
        return None

    def block(self, indent, depth):
        if depth > MAX_DEPTH:
            raise Stop('entete_competence_trop_profond')
        head = self.peek()
        if head is None or head[0] < indent:
            return {}
        return self.sequence(head[0], depth) if head[1] == '-' or head[1].startswith('- ') else self.mapping(head[0], depth)

    def mapping(self, indent, depth):
        out = {}
        while True:
            head = self.peek()
            if head is None or head[0] < indent:
                return out
            if head[0] > indent:
                raise Stop('entete_competence_invalide')
            m = KEY.match(head[1])
            if not m:
                raise Stop('entete_competence_invalide')
            key = (m.group(1) or m.group(2) or m.group(3)).strip()
            if key == '<<' or key in out:
                raise Stop('entete_competence_cle_dupliquee' if key in out else 'entete_competence_invalide')
            self.keys += 1
            if self.keys > MAX_KEYS:
                raise Stop('entete_competence_trop_grand')
            self.i += 1
            out[key] = self.value((m.group(4) or '').strip(), indent, depth)

    def sequence(self, indent, depth):
        out = []
        while True:
            head = self.peek()
            if head is None or head[0] != indent or not (head[1] == '-' or head[1].startswith('- ')):
                if head is not None and head[0] > indent:
                    raise Stop('entete_competence_invalide')
                return out
            rest = head[1][1:].strip()
            if KEY.match(rest) and not rest.startswith(('"', "'")):
                self.lines[self.i] = ' ' * (indent + 2) + rest    # élément de liste qui ouvre une correspondance
                out.append(self.mapping(indent + 2, depth + 1))
            else:
                self.i += 1
                out.append(self.value(rest, indent, depth))

    def value(self, text, indent, depth):
        if text and text[0] in '&*!%@`':
            raise Stop('entete_competence_invalide')
        scalar = self.block_scalar(text, indent)
        if scalar is not None:
            return scalar
        text = strip_comment(text)
        if not text:
            head = self.peek()
            if head is None or head[0] <= indent and not (head[0] == indent and head[1].startswith('- ')):
                return ''
            return self.block(head[0], depth + 1)
        if text[0] == '{':
            raise Stop('entete_competence_invalide')
        if text[0] == '[':
            if not text.endswith(']'):
                raise Stop('entete_competence_invalide')
            inner = text[1:-1].strip()
            return [unquote(x.strip()) for x in split_inline(inner)] if inner else []
        return folded_tail(self, unquote(text), text, indent)

    def block_scalar(self, text, indent):
        m = re.fullmatch(r'([|>])([+-]?)(\d?)([+-]?)(?:\s+#.*)?', text)
        if not m:
            return None
        lines = []
        base = None
        while self.i < len(self.lines):
            raw = self.lines[self.i]
            if raw.strip():
                width = len(raw) - len(raw.lstrip(' '))
                if width <= indent:
                    break
                base = width if base is None else base
                lines.append(raw[base:])
            else:
                lines.append('')
            self.i += 1
        while lines and not lines[-1].strip():
            lines.pop()
        if m.group(1) == '|':
            return '\n'.join(lines)
        out, para = [], []
        for line in lines:
            if line.strip():
                para.append(line.strip())
            else:
                out.append(' '.join(para))
                para = []
        out.append(' '.join(para))
        return '\n'.join(x for x in out if x)


def folded_tail(parser, value, text, indent):
    """Chaîne ordinaire poursuivie sur les lignes suivantes, plus indentées (forme admise par YAML, fréquente dans les descriptions)."""
    if text[0] in '"\'':
        return value
    parts = [value]
    while parser.i < len(parser.lines):
        raw = parser.lines[parser.i]
        width = len(raw) - len(raw.lstrip(' '))
        if not raw.strip() or width <= indent:
            break
        parts.append(raw.strip())
        parser.i += 1
    return ' '.join(parts)


def strip_comment(text):
    quote = ''
    for i, ch in enumerate(text):
        if quote:
            if ch == quote:
                quote = ''
        elif ch in '"\'' and (i == 0 or text[i - 1] in ' [,'):
            quote = ch
        elif ch == '#' and i and text[i - 1] == ' ':
            return text[:i].rstrip()
    return text


def split_inline(text):
    parts, quote, start = [], '', 0
    for i, ch in enumerate(text):
        if quote:
            quote = '' if ch == quote else quote
        elif ch in '"\'':
            quote = ch
        elif ch == ',':
            parts.append(text[start:i])
            start = i + 1
    parts.append(text[start:])
    return parts


def unquote(text):
    if len(text) >= 2 and text[0] == text[-1] == '"':
        return text[1:-1].replace('\\"', '"').replace('\\n', '\n').replace('\\\\', '\\')
    if len(text) >= 2 and text[0] == text[-1] == "'":
        return text[1:-1].replace("''", "'")
    if text[:1] in '"\'':
        raise Stop('entete_competence_invalide')
    return text


def parse(text):
    if len(str(text).encode('utf-8')) > MAX_BYTES:
        raise Stop('entete_competence_trop_grand')
    value = _Parser(str(text).replace('\r\n', '\n')).block(0, 0)
    if not isinstance(value, dict):
        raise Stop('entete_competence_invalide')
    return value


def frontmatter(markdown):
    """(en-tête, corps). Sans en-tête : ({}, texte complet)."""
    text = str(markdown or '').replace('\r\n', '\n').lstrip('﻿')
    m = re.match(r'^---[ \t]*\n(.*?)\n---[ \t]*(?:\n|$)', text, re.S)
    if not m:
        return {}, text
    return parse(m.group(1)), text[m.end():]
