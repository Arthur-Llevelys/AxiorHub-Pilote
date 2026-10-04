"""Pseudonymisation réversible, obligatoire avant tout envoi à une IA non locale (AxiorHub 5.4.0).

Principe :
- avant l'envoi, chaque donnée identifiante est remplacée par un marqueur stable pour la requête : [PERSONNE_1], [SOCIETE_2],
  [ADRESSE_1], [COURRIEL_1], [TELEPHONE_1], [IBAN_1], [NUMERO_1], [DATE_NAISSANCE_1], [REFERENCE_1], [DOSSIER_1], [CHEMIN_1] ;
- le même nom reçoit toujours le même marqueur dans la requête, ce qui permet au modèle de raisonner (« [PERSONNE_1] a écrit à
  [PERSONNE_2] ») ;
- la table de correspondance reste en mémoire sur le serveur, le temps de la requête ; elle n'est ni envoyée, ni journalisée ;
- la réponse est rétablie localement (les marqueurs redeviennent les vrais noms) avant d'être utilisée.

Sources des données reconnues :
1. ce que le cabinet connaît : clients, alias, références et noms de dossiers, correspondants, parties saisies pour les conflits
   d'intérêts ;
2. des motifs : courriels, téléphones, IBAN, numéros de sécurité sociale, SIREN/SIRET, cartes, adresses postales, dates de naissance,
   sociétés (SAS, SARL, SCI…), noms précédés d'une civilité (M., Mme, Maître…), noms en MAJUSCULES, prénoms usuels suivis d'un nom.

Limite assumée : une pseudonymisation n'est pas une anonymisation ; un détail factuel peut encore permettre une réidentification.
L'aperçu (Paramètres › IA) montre exactement ce qui part.
"""
from datetime import datetime, timedelta, timezone
import json
from pathlib import Path
import re
import sqlite3
import time
import unicodedata

CATEGORIES = ('PERSONNE', 'SOCIETE', 'ADRESSE', 'COURRIEL', 'TELEPHONE', 'IBAN', 'NUMERO', 'DATE_NAISSANCE', 'REFERENCE', 'DOSSIER', 'CHEMIN')
LABELS = {'PERSONNE': 'Personnes', 'SOCIETE': 'Sociétés', 'ADRESSE': 'Adresses', 'COURRIEL': 'Courriels', 'TELEPHONE': 'Téléphones',
          'IBAN': 'IBAN', 'NUMERO': 'Numéros (sécurité sociale, SIRET, carte)', 'DATE_NAISSANCE': 'Dates de naissance',
          'REFERENCE': 'Références de dossier', 'DOSSIER': 'Noms de dossier', 'CHEMIN': 'Chemins de fichiers'}
TOKEN = re.compile(r'\[(%s)_(\d{1,4})\]' % '|'.join(CATEGORIES))
LOOSE_TOKEN = re.compile(r'\[?\b(%s)_(\d{1,4})\b\]?' % '|'.join(CATEGORIES))

UP = 'A-ZÀÂÄÇÉÈÊËÎÏÔÖÙÛÜŸ'
LOW = 'a-zàâäçéèêëîïôöùûüÿœæ'
WORD_CAP = r"[%s][%s'’]+(?:-[%s][%s'’]+)*" % (UP, LOW, UP, LOW)
WORD_UPPER = r"[%s][%s'’]+(?:-[%s][%s'’]+)*" % (UP, UP, UP, UP)

# Sigles, mots juridiques et intertitres en majuscules qui ne sont pas des noms.
NOT_NAMES = set('''
A AU AUX AR AFNOR ANSSI APE ARS AT BIC BODACC BOFIP CA CAA CAF CASS CC CCH CCP CDD CDI CE CEDH CESEDA CGI CGV CGU CHSCT CIV CJUE CNB CNIL CNAV
COM CONTRE CP CPAM CPC CPH CPI CRIM CSE CSP CSS CT DE DES DGFIP DIRECCTE DOCX DREETS DU EARL EI EIRL EN ET EUR EURL FAITS GIE HT I II III INPI
INSEE IV IX JAF JCP JEX JLD JO JORF LA LE LES LRAR LRE MSA NAF NB NCPC NIR OK OPQ OU PACS PAR PDF PIECE PIECES PIÈCE PIÈCES PJ POUR PROCEDURE
PROCÉDURE QPC RCS RG RGPD RIB RIN RN RPVA RSA SA SARL SAS SASU SCI SCP SELARL SELAS SIREN SIRET SMIC SNC SUR TA TC TGI TI TJ TTC TVA UE URSSAF
USD V VI VII VIII X XI XII OBJET MOTIFS DISCUSSION DISPOSITIF ATTENDU CONCLUSIONS ASSIGNATION BORDEREAU REQUETE REQUÊTE JUGEMENT ARRET ARRÊT
ORDONNANCE TRIBUNAL COUR CONSEIL SOMMATION COMMUNICATION RECAPITULATIVES RÉCAPITULATIVES NOTE PROJET CONFIDENTIEL URGENT IMPORTANT RAPPEL
COMPLETER COMPLÉTER À ANNEXE ANNEXES ARTICLE ARTICLES CHAPITRE TITRE SECTION PREAMBULE PRÉAMBULE ENTRE SOUSSIGNES SOUSSIGNÉS IL ETE ÉTÉ
CONVENU SUIT CE QUI QUE LUI ELLE ILS ELLES NOUS VOUS MONSIEUR MADAME MAITRE MAÎTRE ME SOCIETE SOCIÉTÉ DEMANDEUR DEFENDEUR DÉFENDEUR
DEMANDERESSE DEFENDERESSE DÉFENDERESSE APPELANT INTIME INTIMÉ INTIMEE INTIMÉE PRESIDENT PRÉSIDENT GREFFE GREFFIER JUGE AVOCAT AVOCATS
BARREAU ORDRE LYON PARIS FRANCE RE TR FW FWD NA ND NC SMS MMS URL HTML JSON XML API IA AI IBAN CES CET CETTE SES LEUR LEURS NON OUI
SANS AVEC DANS VIA VU VUS CONSIDERANT CONSIDÉRANT IN LIMINE FINE PRINCIPAL SUBSIDIAIREMENT TRES TRÈS RESERVE RÉSERVE EXECUTION EXÉCUTION
PROVISOIRE DEPENS DÉPENS CONDAMNER DEBOUTER DÉBOUTER DIRE JUGER CONSTATER ORDONNER RECEVOIR DECLARER DÉCLARER RAPPELER FIXER EXPOSE
EXPOSÉ ETANT ÉTANT NOTAMMENT SUIVANTS SOMMAIRE PRESENTEES PRÉSENTÉES TABLE MATIERES MATIÈRES MISE DEMEURE LETTRE RECOMMANDEE
RECOMMANDÉE ACCUSE ACCUSÉ RECEPTION RÉCEPTION AVIS RECEPISSE CONVOCATION AUDIENCE DATE HEURE LIEU SALLE CHAMBRE PREMIERE PREMIÈRE
DEUXIEME DEUXIÈME TROISIEME TROISIÈME SECONDE INSTANCE APPEL CASSATION REFERE RÉFÉRÉ FOND MISE ETAT ÉTAT GENERAL GÉNÉRAL TOTAL
MONTANT SOMME EUROS PRIX FACTURE DEVIS CONTRAT AVENANT STATUTS PROTOCOLE ACCORD TRANSACTION CONVENTION HONORAIRES PROVISION
NB PS CC CCI BCC CCE ISO GMT UTC PM AM NO NUM N°
'''.split())
CIVILITY = r"(?:M\.|MM\.|Mme|Mmes|Mlle|Monsieur|Madame|Mademoiselle|Maître|Maitre|Me|Mr|Mrs|Ms|Dr|Docteur|Pr|Professeur|Feu)"
COMPANY_FORMS = r"(?:SASU|SAS|SARL|EURL|SCI|SCP|SNC|SELARL|SELAS|SELAFA|SA|GIE|GAEC|EARL|Société|Societe|Sté|Ste|Association|Fondation|Groupe|Cabinet|Etablissements|Établissements|Ets)"
FIRST_NAMES = set('''
adam adrien agathe agnès alain alexandra alexandre alexis alice aline amandine amélie anaïs andré andrée angélique anne annie antoine antonin
arnaud arthur audrey aurélie aurélien axel baptiste benjamin benoît bernadette bernard béatrice bruno camille capucine caroline catherine
cécile céline chantal charles charlotte chloé christelle christian christine christophe claire claude clément clémence colette corinne
cyril damien daniel danielle david delphine denis denise didier dominique dylan édouard élisabeth élise éloïse émilie emma emmanuel
emmanuelle éric estelle étienne eugénie eva évelyne fabien fabienne fabrice fanny florence florian francis franck françois françoise
frédéric frédérique gabriel gabrielle gaël gaëlle geneviève georges gérard ghislaine gilles gilbert grégoire guillaume guy hélène henri
hervé hugo hugues inès irène isabelle jacqueline jacques jean jeanne jérémy jérôme joël joëlle jonathan jordan joseph josiane julia julie
julien juliette justine karine kevin laetitia laura laure laurence laurent léa léon léonie lionel louis louise lucas luc lucie lucien
ludovic lydie madeleine maël manon marc marcel margaux margot marguerite maria marianne marie marine marion martin martine mathieu
mathilde matthieu maurice maxime mélanie michel michèle michelle mickaël mireille monique morgane muriel myriam nadine nathalie nathan
nicolas nicole noël noémie océane odile olivier pascal pascale patrice patricia patrick paul paule pauline philippe pierre quentin
raphaël raymond régis rémi renaud renée richard robert roger romain rose sabine samuel sandrine sarah sébastien serge simon simone
solène sophie stéphane stéphanie suzanne sylvain sylvie thérèse thibault thierry thomas timothée tristan valentin valérie
vanessa véronique victor victoria vincent virginie xavier yann yannick yves yvette yvonne zoé
'''.split())


def _fold(text):
    text = unicodedata.normalize('NFKD', str(text or '').replace('’', "'"))
    return ''.join(c for c in text if not unicodedata.combining(c)).lower().strip()


# ---------------------------------------------------------------------------------------- entités connues du cabinet
_CACHE = {'key': None, 'at': 0, 'items': []}


def known_entities(config):
    """[(texte, catégorie)] connus du cabinet. Mis en cache 5 minutes."""
    matters_file = str(config.get('matters_file') or '')
    state_dir = str(config.get('state_dir') or '')
    try:
        stamp = Path(matters_file).stat().st_mtime if matters_file else 0
    except OSError:
        stamp = 0
    key = (matters_file, state_dir, stamp)
    if _CACHE['key'] == key and time.time() - _CACHE['at'] < 300:
        return _CACHE['items']
    items = []
    if matters_file:
        try:
            matters = json.loads(Path(matters_file).read_text(encoding='utf-8'))
        except (OSError, ValueError):
            matters = []
        for m in matters if isinstance(matters, list) else []:
            if not isinstance(m, dict):
                continue
            name = str(m.get('client_name') or '').strip()
            if name:
                items.append((name, 'SOCIETE' if re.search(r'\b%s\b' % COMPANY_FORMS, name) else 'PERSONNE'))
            for alias in m.get('aliases') or []:
                items.append((str(alias), 'PERSONNE'))
            for ref in [m.get('id', '')] + list(m.get('references') or []):
                items.append((str(ref), 'REFERENCE'))
            folder = str(m.get('path') or '').rstrip('/').rsplit('/', 1)[-1]
            if folder:
                items.append((folder, 'DOSSIER'))
            for person in m.get('correspondents') or []:
                if isinstance(person, dict):
                    if person.get('name'):
                        items.append((str(person['name']), 'PERSONNE'))
                    if person.get('email'):
                        items.append((str(person['email']), 'COURRIEL'))
    if state_dir and (Path(state_dir) / 'desk.sqlite3').is_file():
        try:
            db = sqlite3.connect(Path(state_dir) / 'desk.sqlite3', timeout=2)
            try:
                for name, email in db.execute('SELECT name,email FROM conflicts500_parties LIMIT 5000'):
                    if name:
                        items.append((str(name), 'SOCIETE' if re.search(r'\b%s\b' % COMPANY_FORMS, str(name)) else 'PERSONNE'))
                    if email:
                        items.append((str(email), 'COURRIEL'))
            finally:
                db.close()
        except sqlite3.Error:
            pass
    for root in config.get('roots') or []:
        items.append((str(root).rstrip('/'), 'CHEMIN'))
    seen, clean = set(), []
    for text, cat in items:
        text = re.sub(r'\s+', ' ', text).strip()
        if len(text) < 3 or _fold(text) in seen or text.upper() in NOT_NAMES:
            continue
        seen.add(_fold(text))
        clean.append((text, cat))
    clean.sort(key=lambda x: -len(x[0]))
    _CACHE.update(key=key, at=time.time(), items=clean)
    return clean


# ---------------------------------------------------------------------------------------- motifs
PATTERNS = [
    ('COURRIEL', re.compile(r"(?<![\w.+-])[\w.+-]+@[\w-]+(?:\.[\w-]+)*\.[A-Za-z]{2,}(?![\w-])")),
    ('IBAN', re.compile(r'\b[A-Z]{2}\d{2}(?:[ ]?[A-Z0-9]{4}){3,7}(?:[ ]?[A-Z0-9]{1,3})?\b')),
    ('NUMERO', re.compile(r'\b[12][ ]?\d{2}[ ]?(?:0[1-9]|1[0-2]|[2-9]\d)[ ]?(?:\d{2}|2[AB])[ ]?\d{3}[ ]?\d{3}(?:[ ]?\d{2})\b')),   # NIR
    ('NUMERO', re.compile(r'\b(?:\d{4}[ -]){3}\d{4}\b')),                                                                       # carte
    ('NUMERO', re.compile(r'\b\d{3}[ ]?\d{3}[ ]?\d{3}[ ]?\d{5}\b')),                                                            # SIRET
    ('NUMERO', re.compile(r'(?i)(?:siren|rcs[^\d\n]{0,30})\s*:?\s*(\d{3}[ ]?\d{3}[ ]?\d{3})\b')),                                # SIREN
    ('TELEPHONE', re.compile(r'(?<![\d+])(?:(?:\+|00)33[ .-]?[1-9]|0[1-9])(?:[ .-]?\d{2}){4}(?!\d)')),
    ('TELEPHONE', re.compile(r'(?<![\d+])\+(?!33)\d{2,3}(?:[ .-]?\d{2,4}){3,5}(?!\d)')),
    ('DATE_NAISSANCE', re.compile(r'(?i)\b(?:né|née|nés|nées|naissance)\s+le\s+(\d{1,2}(?:er)?[ /.\-]+(?:\d{1,2}|[a-zéû]+)[ /.\-]+\d{2,4})')),
    ('ADRESSE', re.compile(r"\b\d{1,4}(?:\s?(?:bis|ter|quater))?,?\s+(?i:rue|avenue|av\.|boulevard|bd|place|pl\.|chemin|allée|allee|impasse|quai|route|"
                           r"cours|square|passage|voie|résidence|residence|lotissement|lieu-dit|hameau|montée|montee|cité|cite)\b[^\n,;()]{2,60}?"
                           r"(?=\s*(?:[,;()\n]|$|\.\s|[–-]\s|à\s|\d{5}\b))"
                           r"(?:\s*[,–-]?\s*\d{5}\s+[%s][\w'’\-]+(?:\s[%s][\w'’\-]+){0,2})?" % (UP, UP))),
    ('CHEMIN', re.compile(r'(?<![\w/])/(?:CABINET|Dossiers?|Clients?|Affaires?|remote\.php)[^\s\]\[{}<>"\']{3,}', re.I)),
    ('SOCIETE', re.compile(r"\b%s\s+((?:[%s0-9][\w&'’\-]*)(?:[ ](?:[%s0-9&][\w&'’\-]*)){0,3})" % (COMPANY_FORMS, UP, UP))),
    ('PERSONNE', re.compile(r"\b%s\s+((?:%s|%s)(?:[ \-](?:%s|%s)){0,3})" % (CIVILITY, WORD_UPPER, WORD_CAP, WORD_UPPER, WORD_CAP))),
]
UPPER_NAME = re.compile(r"\b(?:(%s)[ \-]+)?(%s(?:[ \-]%s){0,2})\b(?:[ \-]+(%s))?" % (WORD_CAP, WORD_UPPER, WORD_UPPER, WORD_CAP))
FIRST_LAST = re.compile(r"\b(%s)(?:[ \-](%s))?\s+(%s|%s)\b" % (WORD_CAP, WORD_CAP, WORD_UPPER, WORD_CAP))


class Pseudonymizer:
    def __init__(self, config=None, known=None):
        self.known = known if known is not None else (known_entities(config or {}) if config else [])
        self.forward = {}      # (catégorie, forme repliée) -> marqueur
        self.back = {}         # marqueur -> texte d'origine
        self.counts = {c: 0 for c in CATEGORIES}
        self.known_cat = {_fold(x): c for x, c in self.known}
        self.known_rx = (re.compile(r'(?<![\w@])(?:%s)(?![\w@])' % '|'.join(re.escape(x) for x, _ in self.known), re.I)
                         if self.known else None)

    # ------------------------------------------------------------------ marqueurs
    def token(self, category, original):
        original = original.strip()
        key = (category, _fold(original))
        if key in self.forward:
            return self.forward[key]
        n = sum(1 for k in self.forward if k[0] == category) + 1
        tok = '[%s_%d]' % (category, n)
        self.forward[key], self.back[tok] = tok, original
        self.counts[category] += 1
        return tok

    def _outside(self, text, func):
        """Applique func aux seuls segments qui ne sont pas déjà des marqueurs."""
        parts, last, out = [], 0, []
        for m in TOKEN.finditer(text):
            out.append(func(text[last:m.start()]))
            out.append(m.group(0))
            last = m.end()
        out.append(func(text[last:]))
        return ''.join(out)

    def _sub(self, regex, category, text, group=0):
        def repl(m):
            value = m.group(group)
            if not value or not value.strip():
                return m.group(0)
            start = m.start(group) - m.start(0) + (len(value) - len(value.lstrip()))
            value = value.strip()
            end = start + len(value)
            whole = m.group(0)
            return whole[:start] + self.token(category, value) + whole[end:]
        return self._outside(text, lambda seg: regex.sub(repl, seg))

    # ------------------------------------------------------------------ texte
    def text(self, value):
        s = str(value)
        if not s.strip():
            return s
        if self.known_rx is not None:
            s = self._outside(s, lambda seg: self.known_rx.sub(
                lambda m: self.token(self.known_cat.get(_fold(m.group(0)), 'PERSONNE'), m.group(0)), seg))
        for cat, rx in PATTERNS:
            s = self._sub(rx, cat, s, 1 if rx.groups else 0)

        def upper(seg):
            def repl(m):
                words = re.split(r'[ \-]+', m.group(2))
                if all(w.strip("'’").upper() in NOT_NAMES or len(w) < 2 for w in words):
                    return m.group(0)
                whole, base = m.group(0), m.start(0)
                use1 = bool(m.group(1)) and _fold(m.group(1)) in FIRST_NAMES        # prénom avant le NOM
                use3 = bool(m.group(3)) and _fold(m.group(3)) in FIRST_NAMES        # NOM suivi du prénom
                start = (m.start(1) if use1 else m.start(2)) - base
                end = (m.end(3) if use3 else m.end(2)) - base
                return whole[:start] + self.token('PERSONNE', whole[start:end]) + whole[end:]
            return UPPER_NAME.sub(repl, seg)
        s = self._outside(s, upper)

        def first_last(seg):
            def repl(m):
                if _fold(m.group(1)) not in FIRST_NAMES:
                    return m.group(0)
                return self.token('PERSONNE', m.group(0))
            return FIRST_LAST.sub(repl, seg)
        return self._outside(s, first_last)

    def apply(self, value):
        if isinstance(value, dict):
            return {k: self.apply(v) for k, v in value.items()}
        if isinstance(value, list):
            return [self.apply(v) for v in value]
        if isinstance(value, str):
            return self.text(value)
        return value

    def apply_messages(self, messages):
        out = []
        for m in messages:
            if isinstance(m, dict) and isinstance(m.get('content'), str):
                out.append({**m, 'content': self.text(m['content'])})
            else:
                out.append(self.apply(m))
        return out

    # ------------------------------------------------------------------ retour
    def restore(self, text, json_mode=False):
        if not isinstance(text, str) or not self.back:
            return text

        def repl(m):
            tok = '[%s_%s]' % (m.group(1), m.group(2))
            if tok not in self.back:
                return m.group(0)
            original = self.back[tok]
            return json.dumps(original, ensure_ascii=False)[1:-1] if json_mode else original
        return LOOSE_TOKEN.sub(repl, text)

    def restore_value(self, value):
        if isinstance(value, dict):
            return {k: self.restore_value(v) for k, v in value.items()}
        if isinstance(value, list):
            return [self.restore_value(v) for v in value]
        if isinstance(value, str):
            return self.restore(value)
        return value

    def summary(self):
        return {c: n for c, n in self.counts.items() if n}


# ---------------------------------------------------------------------------------------- journal et aperçu
def _db(state_dir):
    db = sqlite3.connect(Path(state_dir) / 'desk.sqlite3', timeout=10)
    db.execute('''CREATE TABLE IF NOT EXISTS pseudo540_log(id INTEGER PRIMARY KEY, at TEXT NOT NULL, provider TEXT NOT NULL, model TEXT NOT NULL,
                  purpose TEXT NOT NULL, counts TEXT NOT NULL, characters INTEGER NOT NULL, sample TEXT NOT NULL)''')
    return db


def log_transmission(cfg, ps, messages):
    """Journal local de ce qui est parti : catégories remplacées et texte TEL QU'ENVOYÉ (déjà pseudonymisé), conservé 7 jours."""
    state = str(cfg.get('state_dir') or '')
    if not state or not Path(state).is_dir():
        return
    sent = '\n\n'.join('[%s]\n%s' % (m.get('role', ''), m.get('content', '')) for m in messages if isinstance(m, dict))
    try:
        db = _db(state)
        try:
            db.execute('DELETE FROM pseudo540_log WHERE at<?', ((datetime.now(timezone.utc) - timedelta(days=7)).isoformat(),))
            db.execute('INSERT INTO pseudo540_log(at,provider,model,purpose,counts,characters,sample) VALUES(?,?,?,?,?,?,?)',
                       (datetime.now(timezone.utc).isoformat(), str(cfg.get('provider_id') or cfg.get('id') or ''), str(cfg.get('model') or ''),
                        str(cfg.get('purpose') or ''), json.dumps(ps.summary()), len(sent), sent[:20000]))
            db.execute('DELETE FROM pseudo540_log WHERE id NOT IN (SELECT id FROM pseudo540_log ORDER BY id DESC LIMIT 200)')
            db.commit()
        finally:
            db.close()
    except sqlite3.Error:
        pass


def recent(state_dir, limit=20):
    try:
        db = _db(state_dir)
        try:
            rows = db.execute('SELECT id,at,provider,model,purpose,counts,characters FROM pseudo540_log ORDER BY id DESC LIMIT ?', (limit,)).fetchall()
        finally:
            db.close()
    except sqlite3.Error:
        return []
    return [{'id': r[0], 'at': r[1], 'provider': r[2], 'model': r[3], 'purpose': r[4], 'counts': json.loads(r[5] or '{}'), 'characters': r[6]} for r in rows]


def sample(state_dir, ident):
    db = _db(state_dir)
    try:
        row = db.execute('SELECT sample FROM pseudo540_log WHERE id=?', (int(ident),)).fetchone()
    finally:
        db.close()
    return row[0] if row else ''


def preview(config, text, matter=''):
    """Aperçu : texte tel qu'il partirait, et tableau des remplacements (affiché à l'avocat seulement, jamais envoyé)."""
    text = str(text or '')
    if not 1 <= len(text) <= 60000:
        from .common import Stop
        raise Stop('apercu_taille_invalide')
    ps = Pseudonymizer(config)
    sent = ps.text(text)
    table = [{'token': tok, 'original': original, 'category': LABELS.get(tok[1:].rsplit('_', 1)[0], '')} for tok, original in ps.back.items()]
    return {'sent': sent, 'replacements': table, 'counts': ps.summary(), 'restored_ok': ps.restore(sent) == text}
