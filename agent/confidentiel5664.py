"""5.6.64 : correspondances confidentielles entre avocats (article 66-5 de la loi n° 71-1130 du 31 décembre 1971).

Choix du cabinet (19 octobre 2026) : ces correspondances restent lisibles par l'avocat dans AxiorHub (fiche du dossier, assistant,
préparations internes), marquées « confidentiel — usage interne », mais ne sont JAMAIS citées comme source d'un document destiné à un
tiers (juridiction, adversaire, client) : elles sont retirées des sources transmises au modèle pour ces rédactions.

Reconnaissance volontairement prudente : le mot « confidentiel » dans le nom du fichier ou l'objet (sauf « non confidentiel »), ou une
formule propre à la correspondance entre avocats dans le texte. Le mot seul dans le corps d'un document (clause de confidentialité d'un
contrat) ne suffit pas.
"""
from pathlib import PurePosixPath
import re

NAME_RX = re.compile(r'(?<![a-z])confidenti[ae]l(?!it)', re.I)   # « confidentiel(le) », pas « confidentialité » (accord, clause)
NOT_RX = re.compile(r'non[\s_-]*confidenti[ae]l', re.I)
TEXT_RX = re.compile(r'(correspondance|lettre|courrier|courriel|message|pli)s?\s+confidentiel(le)?s?|confidentiel(le)?\s+entre\s+(avocats|confr[eè]res)'
                     r'|article\s+66-?5|66-5\s+de\s+la\s+loi|sous\s+(le\s+sceau\s+de\s+la\s+)?confidentialit[ée]\s+(entre\s+avocats|ordinale)', re.I)
# Aucun type de document produit n'est garanti interne (une « note / consultation » peut être remise au client) : ces correspondances ne
# servent qu'aux usages internes (assistant, préparation de rendez-vous, routines), jamais aux documents produits.
INTERNAL_TYPES = frozenset()
LABEL = 'confidentiel — usage interne'


def is_confidential(name='', text='', subject=''):
    head = ' '.join(str(x or '') for x in (PurePosixPath(str(name or '')).name, subject))
    if head.strip() and NAME_RX.search(head) and not NOT_RX.search(head):
        return True
    body = str(text or '')[:4000]
    return bool(body and TEXT_RX.search(body) and not NOT_RX.search(body[:400]))


# 5.6.65 (audit 5.6.64, R01-R04) : la confidentialité est une propriété de la SOURCE entière (nom, objet, début du texte), calculée
# avant le découpage et héritée par chaque fragment ; un fragment éloigné de l'en-tête ne la perd plus. Version de la règle : POLICY.
POLICY = 1
FLAG = 'correspondance_confidentielle'
# Natures publiques établies par le serveur (texte officiel récupéré et vérifié) : jamais confondues avec une correspondance, même si
# elles citent l'article 66-5. Une « piste » juridique non vérifiée reste soumise au contrôle.
PUBLIC_KINDS = frozenset(('official_legal_source',))
INTERNAL_KINDS = frozenset(('template',))   # trame du cabinet


def source_is_confidential_text(path='', text='', subject=''):
    """Statut d'une source entière, calculé sur son nom, son objet et le début de son texte (où figurent les mentions d'usage)."""
    return is_confidential(path, str(text or '')[:4000], subject)


def entry_is_confidential(s):
    if s.get('kind') in PUBLIC_KINDS or s.get('kind') in INTERNAL_KINDS:
        return False
    if s.get('kind') == 'base_document' and s.get('selection') == 'explicite':
        return False   # pièce désignée par l'avocat : gardée, avec la règle de non-citation transmise au modèle
    meta = s.get('metadata') or {}
    if s.get(FLAG) or meta.get(FLAG):
        return True
    if any(is_confidential(p) for p in s.get('source_paths') or ()):
        return True   # fait dérivé (mémoire du dossier) d'une correspondance confidentielle
    return is_confidential(s.get('path', ''), s.get('excerpt', ''), meta.get('subject', ''))


def filter_packet(packet):
    """Sources d'un document pour un tiers : correspondances confidentielles retirées. Le document de base n'est gardé que s'il a été
    désigné par l'avocat (5.6.65, R02) ; une source juridique officielle vérifiée n'est jamais retirée (R04)."""
    kept, removed = [], 0
    for s in packet:
        if entry_is_confidential(s):
            removed += 1
            continue
        kept.append(s)
    return kept, removed


def source_is_confidential(source):
    return is_confidential(source.get('path') or source.get('label') or '', source.get('extrait') or '', '')
