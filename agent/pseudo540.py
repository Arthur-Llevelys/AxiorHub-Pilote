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

5.6.109 (lot P) — ce qui change ici, sans second circuit ni second registre de pseudonymes :
- P01 : l'instantané des identités connues couvre le registre courant (dossiers, parties, alias, contacts, parties lues dans les pièces),
  il est comparé sans accent ni casse, dans les deux ordres, et le nom seul d'une personne du registre est masqué ; le même instantané
  sert au texte, à la lecture des images (``vision56106``) et au contrôle de l'enveloppe (``transmission56109``) ;
- P02 : révision durable du registre (table ``pseudo56109_revision``, déclencheurs sur les registres, ``invalider``) ; le cache ne sert que
  si cette révision est vérifiable, sinon le registre est relu ; un registre illisible est un refus, jamais une liste vide ;
- P03 : un marqueur écrit tel quel dans une source (« [PERSONNE_1] » dans un gabarit) n'est jamais confondu avec une substitution : son
  numéro est réservé, ou il part sous une forme distincte (« [LITTERAL_PERSONNE_1] ») quand le numéro est déjà attribué ;
- P04 : ``controle_retour`` relève les marqueurs inconnus ou altérés d'une réponse, sans rien deviner.
"""
from contextvars import ContextVar
from datetime import datetime, timedelta, timezone
import json
from pathlib import Path
import re
import sqlite3
import time
import unicodedata

from .pseudo5677 import CARD_FREE, CODES, NIR_WRAPPED, SIREN_FREE, iban_spans, luhn, nir_key_ok, siren_ok

CATEGORIES = ('PERSONNE', 'SOCIETE', 'ADRESSE', 'COURRIEL', 'TELEPHONE', 'IBAN', 'NUMERO', 'DATE_NAISSANCE', 'REFERENCE', 'DOSSIER', 'CHEMIN')
LABELS = {'PERSONNE': 'Personnes', 'SOCIETE': 'Sociétés', 'ADRESSE': 'Adresses', 'COURRIEL': 'Courriels', 'TELEPHONE': 'Téléphones',
          'IBAN': 'IBAN', 'NUMERO': 'Numéros (sécurité sociale, SIRET, carte)', 'DATE_NAISSANCE': 'Dates de naissance',
          'REFERENCE': 'Références de dossier', 'DOSSIER': 'Noms de dossier', 'CHEMIN': 'Chemins de fichiers'}
TOKEN = re.compile(r'\[(%s)_(\d{1,4})\]' % '|'.join(CATEGORIES))
LOOSE_TOKEN = re.compile(r'\[?\b(%s)_(\d{1,4})\b\]?' % '|'.join(CATEGORIES))
# 5.6.109 (P03) : marqueur littéral d'une source, parti sous une forme distincte. « [LITTERAL_PERSONNE_1] » n'est reconnu ni par TOKEN ni
# par LOOSE_TOKEN (pas de frontière de mot après « _ ») : les tables stables et les anciens formats restent valables tels quels.
ECHAPPE = re.compile(r'\[LITTERAL_((?:LITTERAL_)*(?:%s)_\d{1,4})\]' % '|'.join(CATEGORIES))
PROTEGE = re.compile(r'\[(?:LITTERAL_)*(?:%s)_\d{1,4}\]' % '|'.join(CATEGORIES))   # marqueur ou littéral échappé : jamais retouché par les règles
_RETOUR = re.compile(ECHAPPE.pattern + '|' + LOOSE_TOKEN.pattern)
# 5.6.109 (P04) : formes qu'un modèle donne à un marqueur en le recopiant mal (casse, accent, espace, tiret, zéros) ; la prose ordinaire
# (« le dossier 12 », « la personne 2 ») n'en fait pas partie : il faut des crochets, ou le tiret bas.
_CAT_LIBRE = (r'PERSONNE|SOCI[ÉE]T[ÉE]|ADRESSE|COURRIEL|T[ÉE]L[ÉE]PHONE|IBAN|NUM[ÉE]RO|DATE[ _\-]?(?:DE[ _\-]?)?NAISSANCE|R[ÉE]F[ÉE]RENCE|'
              r'DOSSIER|CHEMIN')
MARQUEUR_LIBRE = re.compile(r'\[\s*(?:LITT[ÉE]RAL[ _\-]*)*(?:%s)\s*[ _\-]?\s*\d{1,6}\s*\]|(?<![\w\[])(?:%s)_\d{1,6}(?![\w\]])' % (_CAT_LIBRE, _CAT_LIBRE),
                            re.I)
VERSION_DETECTEUR = '5.6.109'   # règles et registres lus : dans la clé du cache, les reçus d'image et le journal

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
'''.split()) | CODES   # 5.6.77 : codes utiles au raisonnement (IDCC, CSG, PMSS, ECLI…)
CIVILITY = r"(?:M\.|MM\.|Mme|Mmes|Mlle|Monsieur|Madame|Mademoiselle|Maître|Maitre|Me|Mr|Mrs|Ms|Dr|Docteur|Pr|Professeur|Feu|monsieur|madame|mademoiselle|maître|maitre|mme|mlle)"
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


FIRST_NAMES = {_fold(x) for x in FIRST_NAMES}     # 5.6.86 : comparés sans accent : Hélène, François, Éric n'étaient jamais reconnus

# 5.6.86 : dans un texte déjà sérialisé en JSON, un saut de ligne s'écrit « \\n » : le « n » collait au mot suivant, et un nom en début de ligne (une signature) n'était pas vu
JSON_BREAK = re.compile(r'(?<!\\)\\([nrt])')
JSON_CHAR = re.compile(r'(?<!\\)\\u([0-9a-fA-F]{4})')
BREAKS = {'n': '\x1c', 'r': '\x1d', 't': '\x1e'}
BREAKS_BACK = {v: '\\' + k for k, v in BREAKS.items()}


def _json_char(m):
    code = int(m.group(1), 16)
    return chr(code) if code >= 0x80 and not 0xD800 <= code <= 0xDFFF else m.group(0)


# ---------------------------------------------------------------------------------------- entités connues du cabinet
_CACHE = {'key': None, 'at': 0, 'items': []}
# 5.6.109 (P01) : identités de l'émission en cours. Posé par ``transmission56109.emission`` autour de ``Model._leaf`` : le texte, la lecture
# des images et le contrôle de l'enveloppe lisent la même liste, relue du registre pour cette émission (jamais du cache).
INSTANTANE = ContextVar('axiorhub_instantane56109', default=None)
TABLE_REVISION = 'pseudo56109_revision'
# Registres d'identités de la base du cabinet : chacun porte trois déclencheurs qui avancent la révision durable (P02).
REGISTRES = ('dossiers5697', 'dossier_parties5697', 'dossier_alias5697', 'conflicts500_parties', 'contacts5636', 'd51_parties', 'd51_aliases')
LIMITE_REGISTRE = 20000     # lignes lues par registre, les plus récentes d'abord ; au-delà, l'aperçu le dit (« registre tronqué »)
ILLISIBLE = 'registre_identites_illisible'
SCHEMA56109 = ('CREATE TABLE IF NOT EXISTS pseudo56109_revision(id INTEGER PRIMARY KEY CHECK(id=1), revision INTEGER NOT NULL DEFAULT 0, '
               "motif TEXT NOT NULL DEFAULT '', modifie_le TEXT NOT NULL DEFAULT '')")
# Colonnes ajoutées au journal des envois (P04, P05) : état réel, reçu sans contenu (empreinte et taille du corps exact), code du contrôle,
# réponse gardée « à vérifier » (encore pseudonymisée), révision du registre et version des règles.
COLONNES_JOURNAL = (('etat', "TEXT NOT NULL DEFAULT ''"), ('empreinte', "TEXT NOT NULL DEFAULT ''"), ('octets', 'INTEGER NOT NULL DEFAULT 0'),
                    ('code', "TEXT NOT NULL DEFAULT ''"), ('retour', "TEXT NOT NULL DEFAULT ''"), ('revision', 'INTEGER NOT NULL DEFAULT 0'),
                    ('detecteur', "TEXT NOT NULL DEFAULT ''"))


class Instantane(list):
    """[(texte, catégorie)] connus à un instant, avec la révision du registre lue au même moment."""
    revision = None      # révision durable (None : base sans table de révision)
    sur = False          # la révision est vérifiable : table présente, déclencheurs posés sur chaque registre présent
    lu_le = 0.0
    registres = {}       # registre -> lignes lues (aperçu de la couverture ; aucun nom)
    tronques = ()        # registres plus longs que la borne de lecture
    recherche = None     # structures de recherche, calculées une fois par instantané


def _declencheurs(table):
    return tuple('pseudo56109_%s_%s' % (table, e) for e in 'iud')


def _etat_registre(db):
    """(révision durable, registre sûr, noms des tables et déclencheurs). Sûr : la table de révision existe et chaque registre présent porte
    ses trois déclencheurs ; sinon rien ne prouve qu'une écriture ait avancé la révision, et le cache n'est pas employé."""
    noms = {r[0] for r in db.execute("SELECT name FROM sqlite_master WHERE type IN ('table','trigger')")}
    if TABLE_REVISION not in noms:
        return None, False, noms
    row = db.execute('SELECT revision FROM pseudo56109_revision WHERE id=1').fetchone()
    complet = all(t in noms for table in REGISTRES if table in noms for t in _declencheurs(table))
    return (int(row[0]) if row else None), bool(row) and complet, noms


def _base(state_dir):
    path = Path(state_dir) / 'desk.sqlite3' if state_dir else None
    return path if path is not None and path.is_file() else None


def revision(state_dir):
    """Révision durable du registre des identités (None si la base ou la table n'existe pas). Lecture seule."""
    path = _base(str(state_dir or ''))
    if path is None:
        return None
    try:
        db = sqlite3.connect(path, timeout=5)
        try:
            return _etat_registre(db)[0]
        finally:
            db.close()
    except sqlite3.Error:
        return None


def ensure_schema(desk):
    """Au démarrage (``Desk``) : table de la révision, déclencheurs sur les registres présents, colonnes du journal des envois. Rejouable ;
    n'écrit rien quand tout est en place (aucune écriture ni verrou au démarrage sans changement). Jamais pendant une lecture."""
    db = desk.db
    noms = {r[0] for r in db.execute("SELECT name FROM sqlite_master WHERE type IN ('table','trigger')")}
    manquants = [(table, evenement, nom) for table in REGISTRES if table in noms
                 for evenement, nom in zip(('INSERT', 'UPDATE', 'DELETE'), _declencheurs(table)) if nom not in noms]
    colonnes = {r[1] for r in db.execute('PRAGMA table_info(pseudo540_log)')} if 'pseudo540_log' in noms else None
    a_ajouter = [c for c in COLONNES_JOURNAL if colonnes is not None and c[0] not in colonnes]
    pret = TABLE_REVISION in noms and db.execute('SELECT 1 FROM pseudo56109_revision WHERE id=1').fetchone()
    if pret and not manquants and not a_ajouter:
        return
    if db.in_transaction:
        db.commit()
    db.execute(SCHEMA56109)
    db.execute('INSERT OR IGNORE INTO pseudo56109_revision(id, revision) VALUES(1, 0)')
    _poser_declencheurs(db, manquants)
    _colonnes_journal(db, a_ajouter)
    db.commit()


def _poser_declencheurs(db, manquants=None):
    """Déclencheurs manquants des registres présents (un registre créé après le démarrage n'en porte pas encore)."""
    if manquants is None:
        noms = {r[0] for r in db.execute("SELECT name FROM sqlite_master WHERE type IN ('table','trigger')")}
        manquants = [(table, evenement, nom) for table in REGISTRES if table in noms
                     for evenement, nom in zip(('INSERT', 'UPDATE', 'DELETE'), _declencheurs(table)) if nom not in noms]
    for table, evenement, nom in manquants:
        db.execute('CREATE TRIGGER IF NOT EXISTS %s AFTER %s ON %s BEGIN UPDATE pseudo56109_revision SET revision=revision+1 WHERE id=1; END'
                   % (nom, evenement, table))


def _colonnes_journal(db, a_ajouter):
    for nom, declaration in a_ajouter:
        try:
            db.execute('ALTER TABLE pseudo540_log ADD COLUMN %s %s' % (nom, declaration))
        except sqlite3.OperationalError as ex:   # autre processus plus rapide : la colonne existe
            if 'duplicate column' not in str(ex).lower():
                raise


def _avancer(db, motif):
    if not db.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name=?", (TABLE_REVISION,)).fetchone():
        return False      # table absente : aucun cache n'est employé pour cette base, le registre est relu à chaque émission
    db.execute('UPDATE pseudo56109_revision SET revision=revision+1, motif=?, modifie_le=? WHERE id=1',
               (str(motif or '')[:80], datetime.now(timezone.utc).isoformat(timespec='seconds')))
    return True


def invalider(cible=None, motif=''):
    """Une identité vient de changer (ajout, correction, retrait, import, synchronisation) : la révision durable avance et le cache de ce
    processus est vidé ; la prochaine émission relit le registre. ``cible`` : le bureau (``Desk`` : même connexion, donc même transaction
    que l'écriture en cours), une configuration ou un répertoire d'état. Ne lève jamais : une écriture métier n'échoue pas à cause de ce
    repère (sans lui, le registre est de toute façon relu à chaque émission externe)."""
    _CACHE.update(key=None, at=0, items=[])
    try:
        db = getattr(cible, 'db', None)
        if db is not None:
            ouverte = db.in_transaction
            fait = _avancer(db, motif)
            if fait:
                _poser_declencheurs(db)       # registre créé depuis le démarrage : ses écritures avancent désormais la révision
                if not ouverte:
                    db.commit()
            return fait
        path = _base(str((cible.get('state_dir') if isinstance(cible, dict) else cible) or ''))
        if path is None:
            return False
        db = sqlite3.connect(path, timeout=5)
        try:
            fait = _avancer(db, motif)
            db.commit()
            return fait
        finally:
            db.close()
    except (sqlite3.Error, OSError, AttributeError, TypeError, ValueError):
        return False


def _genre(nom, morale=False):
    return 'SOCIETE' if morale or re.search(r'\b%s\b' % COMPANY_FORMS, nom) else 'PERSONNE'


def _du_fichier_des_dossiers(matters_file, items):
    from .common import Stop
    try:
        matters = json.loads(Path(matters_file).read_text(encoding='utf-8'))
    except FileNotFoundError:
        return 0
    except (OSError, ValueError):      # fichier présent mais illisible : refus, jamais « aucun dossier connu »
        raise Stop(ILLISIBLE) from None
    avant = len(items)
    for m in matters if isinstance(matters, list) else []:
        if not isinstance(m, dict):
            continue
        name = str(m.get('client_name') or '').strip()
        if name:
            items.append((name, _genre(name)))
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
    return len(items) - avant


def _des_registres(db, noms, items):
    """Registres présents dans la base du cabinet → identités ; rend ({registre: lignes lues}, [registres tronqués]). Toute erreur SQLite
    remonte : l'appelant en fait un refus explicite (une liste vide laisserait partir les noms)."""
    compte, tronques = {}, []

    def lire(table, voulues):
        if table not in noms:
            return []
        presentes = {r[1] for r in db.execute('PRAGMA table_info(%s)' % table)}
        cols = [c for c in voulues if c in presentes]
        if not cols:
            return []
        rows = db.execute('SELECT %s FROM %s ORDER BY rowid DESC LIMIT %d' % (','.join(cols), table, LIMITE_REGISTRE + 1)).fetchall()
        if len(rows) > LIMITE_REGISTRE:
            tronques.append(table)
            rows = rows[:LIMITE_REGISTRE]
        compte[table] = len(rows)
        return [dict(zip(cols, r)) for r in rows]

    def champ(row, nom):
        return re.sub(r'\s+', ' ', str(row.get(nom) or '')).strip()

    def coordonnees(row, courriel, telephone, adresse, siren):
        if champ(row, courriel):
            items.append((champ(row, courriel), 'COURRIEL'))
        if len(champ(row, telephone)) >= 6:
            items.append((champ(row, telephone), 'TELEPHONE'))
        if 8 <= len(champ(row, adresse)) <= 160:
            items.append((champ(row, adresse), 'ADRESSE'))
        if len(champ(row, siren)) >= 9:
            items.append((champ(row, siren), 'NUMERO'))

    for row in lire('conflicts500_parties', ('name', 'email')):
        if champ(row, 'name'):
            items.append((champ(row, 'name'), _genre(champ(row, 'name'))))
        if champ(row, 'email'):
            items.append((champ(row, 'email'), 'COURRIEL'))
    for row in lire('dossier_parties5697', ('nom', 'type_personne', 'courriel', 'telephone', 'adresse', 'siren')):
        if champ(row, 'nom'):
            items.append((champ(row, 'nom'), _genre(champ(row, 'nom'), champ(row, 'type_personne') == 'morale')))
        coordonnees(row, 'courriel', 'telephone', 'adresse', 'siren')
    for row in lire('dossiers5697', ('ref', 'titre', 'client', 'rg')):
        if champ(row, 'client'):
            items.append((champ(row, 'client'), _genre(champ(row, 'client'))))
        if champ(row, 'ref'):
            items.append((champ(row, 'ref'), 'REFERENCE'))
        if len(champ(row, 'rg')) >= 5:
            items.append((champ(row, 'rg'), 'REFERENCE'))
        titre = champ(row, 'titre')
        if len(titre) >= 8 and len(titre.split()) >= 2:      # un intitulé d'un seul mot (« Divers ») masquerait un mot courant partout
            items.append((titre, 'DOSSIER'))
    for row in lire('dossier_alias5697', ('genre', 'alias')):
        alias = champ(row, 'alias')
        if alias:
            items.append((alias, 'REFERENCE' if champ(row, 'genre') == 'ref' else ('CHEMIN' if alias.startswith('/') else 'DOSSIER')))
    for row in lire('contacts5636', ('kind', 'name', 'first_name', 'company', 'email', 'phone', 'address', 'siren')):
        nom, prenom = champ(row, 'name'), champ(row, 'first_name')
        if nom:
            items.append(((prenom + ' ' + nom).strip(), _genre(nom, champ(row, 'kind') == 'societe' and not prenom)))
        if champ(row, 'company'):
            items.append((champ(row, 'company'), 'SOCIETE'))
        coordonnees(row, 'email', 'phone', 'address', 'siren')
    for row in lire('d51_parties', ('kind', 'display_name', 'counsel')):     # parties lues dans les pièces, sans IA (modele5651)
        if champ(row, 'display_name'):
            items.append((champ(row, 'display_name'), _genre(champ(row, 'display_name'), champ(row, 'kind') in ('morale', 'societe', 'pm'))))
        if champ(row, 'counsel'):
            items.append((champ(row, 'counsel'), 'PERSONNE'))
    for row in lire('d51_aliases', ('alias',)):
        if champ(row, 'alias'):
            items.append((champ(row, 'alias'), _genre(champ(row, 'alias'))))
    return compte, tronques


def _cle_connue(text):
    """Forme de comparaison d'un nom connu : sans accent ni casse, espaces et traits d'union équivalents."""
    return re.sub(r'[\s\-]+', ' ', _fold(text)).strip()


def known_entities(config, frais=False):
    """[(texte, catégorie)] connus du cabinet : fichier des dossiers, registre courant (dossiers, parties, alias), parties saisies pour les
    conflits, contacts, parties lues dans les pièces, racines du stockage. Rien n'est envoyé ni affiché.

    5.6.109 (P02) : le cache (5 minutes au plus) ne sert que si la révision durable du registre est vérifiable et inchangée ; sinon le
    registre est relu. ``frais`` : relecture imposée (émission externe). Un registre illisible lève ``Stop('registre_identites_illisible')`` :
    une panne de lecture ne devient jamais une liste vide qui laisserait partir les noms."""
    courant = INSTANTANE.get()
    if courant is not None and not frais:
        return courant
    from .common import Stop
    matters_file = str(config.get('matters_file') or '')
    state_dir = str(config.get('state_dir') or '')
    roots = tuple(str(r) for r in config.get('roots') or [])
    try:
        st = Path(matters_file).stat() if matters_file else None
        stamp = (st.st_mtime_ns, st.st_size) if st else 0
    except OSError:
        stamp = 0
    path = _base(state_dir)
    db = None
    try:
        rev, sur, noms = None, True, set()
        if path is not None:
            try:
                db = sqlite3.connect(path, timeout=5)
                rev, sur, noms = _etat_registre(db)
            except sqlite3.Error:
                raise Stop(ILLISIBLE) from None
        key = (matters_file, state_dir, stamp, roots, rev, bool(path), VERSION_DETECTEUR)
        if not frais and sur and _CACHE['key'] == key and time.time() - _CACHE['at'] < 300:
            return _CACHE['items']
        items, compte, tronques = [], {}, []
        if matters_file:
            compte['fichier_des_dossiers'] = _du_fichier_des_dossiers(matters_file, items)
        if db is not None:
            try:
                lus, tronques = _des_registres(db, noms, items)
                compte.update(lus)
            except sqlite3.Error:
                raise Stop(ILLISIBLE) from None
    finally:
        if db is not None:
            db.close()
    for root in roots:
        items.append((root.rstrip('/'), 'CHEMIN'))
    seen, clean = set(), Instantane()
    for text, cat in items:
        text = re.sub(r'\s+', ' ', text).strip()
        cle = _cle_connue(text)
        if len(text) < 3 or not cle or cle in seen or text.upper() in NOT_NAMES:
            continue
        seen.add(cle)
        clean.append((text, cat))
    clean.sort(key=lambda x: -len(x[0]))
    clean.revision, clean.sur, clean.lu_le, clean.registres, clean.tronques = rev, sur, time.time(), compte, tuple(tronques)
    if sur:
        _CACHE.update(key=key, at=time.time(), items=clean)
    else:
        _CACHE.update(key=None, at=0, items=[])
    return clean


def instantane(config):
    """Identités relues du registre pour UNE émission externe (jamais du cache) : voir ``INSTANTANE``."""
    return known_entities(config or {}, frais=True)


def couverture(config):
    """Ce que le masquage connaît, en nombres seulement (aperçu et Paramètres › IA) : aucun nom."""
    connues = known_entities(config or {})
    return {'identites': len(connues), 'registres': dict(getattr(connues, 'registres', {}) or {}), 'tronques': list(getattr(connues, 'tronques', ()) or ()),
            'revision': getattr(connues, 'revision', None), 'detecteur': VERSION_DETECTEUR}


# ---------------------------------------------------------------------------------------- motifs
PATTERNS = [
    ('COURRIEL', re.compile(r"(?<![\w.+-])[\w.+-]+@[\w-]+(?:\.[\w-]+)*\.[A-Za-z]{2,}(?![\w-])")),
    ('IBAN', re.compile(r'\b[A-Z]{2}\d{2}(?:[ ]?[A-Z0-9]{4}){3,7}(?:[ ]?[A-Z0-9]{1,3})?\b')),
    # NIR (5.6.77 : espace, point ou tiret ; masqué même si la clé est erronée)
    ('NUMERO', re.compile(r'(?<!\d)[1-478][ .\-]?\d{2}[ .\-]?(?:0[1-9]|1[0-2]|[2-9]\d)[ .\-]?(?:\d{2}|2[AB])[ .\-]?\d{3}[ .\-]?\d{3}'
                          r'(?:[ .\-]?\d{2})?(?!\d)')),
    ('NUMERO', re.compile(r'\b(?:\d{4}[ -]){3}\d{4}\b')),                                                                       # carte
    ('NUMERO', re.compile(r'\b\d{3}[ ]?\d{3}[ ]?\d{3}[ ]?\d{5}\b')),                                                            # SIRET
    ('NUMERO', re.compile(r'(?i)(?:siren|rcs[^\d\n]{0,30})\s*:?\s*(\d{3}[ ]?\d{3}[ ]?\d{3})\b')),                                # SIREN
    # 5.6.77 : RG, Portalis, RPPS, sinistre, immatriculation
    ('REFERENCE', re.compile(r'(?i)\bR\.?G\.?\s*(?:n[°o]\.?\s*)?:?\s*(\d{2}\s?/\s?\d{3,6})\b')),
    ('REFERENCE', re.compile(r'(?i)\bPortalis\s*:?\s*([A-Z0-9]{3,5}(?:-[A-Z0-9]{1,6}){2,4})\b')),
    ('NUMERO', re.compile(r'(?i)\bRPPS\s*(?:n[°o]\.?\s*)?:?\s*(\d{11})\b')),
    ('NUMERO', re.compile(r'(?i)\bsinistre\s+(?:AT(?:\s*/\s*MP)?\s+)?(?:n[°o]\.?|num[ée]ro)\s*:?\s*(\d[\d ./\-]{5,22}\d)')),
    ('NUMERO', re.compile(r'(?i:\bimmatricul[ée]e?s?|\bplaque(?: d.immatriculation)?)\s*(?:n[°o]\.?\s*)?:?\s*'
                          r'([A-Z]{2}[ \-]?\d{3}[ \-]?[A-Z]{2}|\d{1,4}[ \-]?[A-Z]{1,3}[ \-]?(?:\d{2}|2[AB]))\b')),
    ('NUMERO', re.compile(r'\b[A-HJ-NP-TV-Z]{2}-\d{3}-[A-HJ-NP-TV-Z]{2}\b')),                                                    # plaque SIV
    ('TELEPHONE', re.compile(r'(?<![\d+])(?:(?:\+|00)33[ .-]?[1-9]|0[1-9])(?:[ .-]?\d{2}){4}(?!\d)')),
    ('TELEPHONE', re.compile(r'(?<![\d+])\+(?!33)\d{2,3}(?:[ .-]?\d{2,4}){3,5}(?!\d)')),
    ('DATE_NAISSANCE', re.compile(r'(?i)\b(?:né|née|nés|nées|naissance)\s+le\s+(\d{1,2}(?:er)?[ /.\-]+(?:\d{1,2}|[a-zéû]+)[ /.\-]+\d{2,4})')),
    ('ADRESSE', re.compile(r"\b\d{1,4}(?:\s?(?:bis|ter|quater))?,?\s+(?i:rue|avenue|av\.|boulevard|bd|place|pl\.|chemin|allée|allee|impasse|quai|route|"
                           r"cours|square|passage|voie|résidence|residence|lotissement|lieu-dit|hameau|montée|montee|cité|cite)\b[^\n\x1c-\x1e,;()]{2,60}?"
                           r"(?=\s*(?:[,;()\n\x1c-\x1e]|$|\.\s|[–-]\s|à\s|\d{5}\b))"
                           r"(?:\s*[,–-]?\s*\d{5}\s+[%s][\w'’\-]+(?:\s[%s][\w'’\-]+){0,2})?" % (UP, UP))),
    ('CHEMIN', re.compile(r'(?<![\w/])/(?:CABINET|Dossiers?|Clients?|Affaires?|remote\.php)[^\s\]\[{}<>"\']{3,}', re.I)),
    ('SOCIETE', re.compile(r"\b%s\s+((?:[%s0-9][\w&'’\-]*)(?:[ ](?:[%s0-9&][\w&'’\-]*)){0,3})" % (COMPANY_FORMS, UP, UP))),
    # 5.6.77 : « la société ZORVAL »
    ('SOCIETE', re.compile(r"(?i:\bsoci[ée]t[ée])\s+(%s(?:[ \-]%s){0,3})" % (WORD_UPPER, WORD_UPPER))),
    ('PERSONNE', re.compile(r"\b(%s)\s+((?:%s|%s)(?:[ \-](?:%s|%s)){0,3})" % (CIVILITY, WORD_UPPER, WORD_CAP, WORD_UPPER, WORD_CAP))),
]
# 5.6.77 : « ZORVAL SAS » (forme sociale après le nom) ; titres professionnels jamais rapprochés d'un homonyme
COMPANY_AFTER = re.compile(r"\b((?:%s|%s)(?:[ ](?:%s|%s)){0,2})[ ](SASU|SAS|SARL|EURL|SCI|SNC|SELARL|SELAS|SELAFA|SA|GIE|SCP)\b"
                           % (WORD_UPPER, WORD_CAP, WORD_UPPER, WORD_CAP))
PROFESSIONAL = {'maître', 'maitre', 'me', 'dr', 'docteur', 'pr', 'professeur'}
UPPER_NAME = re.compile(r"\b(?:(%s)[ \-]+)?(%s(?:[ \-]%s){0,2})\b(?:[ \-]+(%s))?" % (WORD_CAP, WORD_UPPER, WORD_UPPER, WORD_CAP))
FIRST_LAST = re.compile(r"\b(%s)(?:[ \-](%s))?[ \t]+(%s|%s)\b" % (WORD_CAP, WORD_CAP, WORD_UPPER, WORD_CAP))
ANY_WORD = re.compile(r"\b(?:%s|%s)\b" % (WORD_UPPER, WORD_CAP))
BIRTH_PLACE = re.compile(r"(?:\[DATE_NAISSANCE_\d+\]|\b[Nn]ée?s?)\s+à\s+([%s][\w'’\-]+(?:[ \-][%s][\w'’\-]+){0,3})" % (UP, UP))


def entity_key(category, original):
    """Clé d'une entité : forme repliée ; pour une personne, mots triés (« Paul DURAND » = « DURAND Paul »). 5.6.77."""
    folded = re.sub(r'\s+', ' ', _fold(original))
    return ' '.join(sorted(folded.split(' '))) if category == 'PERSONNE' else folded


def surname_of(original):
    """Nom de famille d'un nom complet (« Paul DURAND » → « durand ») ; '' pour un nom seul ou sans prénom reconnu. 5.6.77."""
    words = str(original or '').split()
    if len(words) < 2:
        return ''
    upper = [w for w in words if len(w) >= 2 and w == w.upper() and re.search('[%s]' % UP, w)]
    rest = [w for w in words if w not in upper]
    if upper and rest and all(all(_fold(p) in FIRST_NAMES for p in w.split('-')) for w in rest):
        return _fold(' '.join(upper))
    if not upper and len(words) == 2 and all(_fold(p) in FIRST_NAMES for p in words[0].split('-')):
        return _fold(words[1])
    return ''


# 5.6.88 : noms lus dans les registres pour la question en cours ([(texte, catégorie)]) : masqués comme des noms connus du cabinet, quelle que soit leur casse
NOMS_DU_TOUR = ContextVar('axiorhub_noms5688', default=())

# ---------------------------------------------------------------------------------------- 5.6.109 (P01) : noms connus, sans accent ni casse
PARTICULES = frozenset(('de', 'du', 'des', 'd', 'la', 'le', 'les', 'l', 'van', 'von', 'der', 'den', 'ben', 'bin', 'el', 'al', 'di', 'da', 'dos',
                        'del', 'della', 'et', 'y', 'ep', 'epouse', 'nee', 'veuve', 'dit', 'dite'))
FORMES = frozenset(_fold(x) for x in ('SASU', 'SAS', 'SARL', 'EURL', 'SCI', 'SCP', 'SNC', 'SELARL', 'SELAS', 'SELAFA', 'SA', 'GIE', 'GAEC', 'EARL',
                                      'Société', 'Sté', 'Association', 'Fondation', 'Groupe', 'Cabinet', 'Établissements', 'Ets'))
_PLI = {}


def _plie(s):
    """(texte replié, rangs) : le repli de ``_fold`` caractère par caractère ; ``rangs[i]`` est le rang d'origine du i-ème caractère replié
    (None pour un texte ASCII : mêmes rangs). Les noms connus sont cherchés sur le texte replié, puis remplacés dans le texte d'origine."""
    if s.isascii():
        return s.lower(), None
    out, rangs = [], []
    for i, ch in enumerate(s):
        f = _PLI.get(ch)
        if f is None:
            f = ''.join(c for c in unicodedata.normalize('NFKD', "'" if ch == '’' else ch) if not unicodedata.combining(c)).lower()
            if len(_PLI) < 4096:
                _PLI[ch] = f
        for c in f:
            out.append(c)
            rangs.append(i)
    return ''.join(out), rangs


def _rangs(s, rangs, a, b):
    if rangs is None:
        return a, b
    debut, fin = rangs[a], rangs[b - 1] + 1
    while fin < len(s) and unicodedata.combining(s[fin]):       # accent décomposé du dernier caractère
        fin += 1
    return debut, fin


def _mots_isolables(mots):
    """Mots d'un nom de personne connu à masquer aussi quand ils sont écrits seuls : le nom en capitales (« Samira ZELVANI »), sinon les mots
    qui ne sont pas un prénom usuel (prénom rare ou étranger : protégé lui aussi), sinon le dernier mot (« Jean Martin »)."""
    def prenom(m):
        return all(_fold(p) in FIRST_NAMES for p in m.split('-'))
    candidats = [m for m in (x.strip('.,;:()"') for x in mots)
                 if len(m) >= 3 and _fold(m) not in PARTICULES and m.upper() not in NOT_NAMES and not any(c.isdigit() for c in m)
                 and re.fullmatch(r"[^\W\d_]+(?:[\-'’][^\W\d_]+)*", m)]
    majuscules = [m for m in candidats if m == m.upper()]
    if majuscules and len(majuscules) < len(candidats):
        choisis = majuscules
    else:
        choisis = [m for m in candidats if not prenom(m)] or candidats[-1:]
    out = []
    for m in choisis:
        out.append(m)
        if '-' in m:      # « Orlandec-Vasseur » : chaque partie seule aussi
            out += [p for p in m.split('-') if len(p) >= 3 and _fold(p) not in FIRST_NAMES and _fold(p) not in PARTICULES and p.upper() not in NOT_NAMES]
    return out


def _coeur_societe(nom):
    """Dénomination sans sa forme sociale (« Hôtel Bellevue SARL » → « Hôtel Bellevue ») ; '' si rien ne change."""
    mots = [m for m in nom.split() if _fold(m.strip('.,()')) not in FORMES]
    while mots and _fold(mots[0].rstrip("'’")) in PARTICULES:
        mots.pop(0)
    coeur = ' '.join(mots).strip(' ,.-')
    return coeur if coeur and _cle_connue(coeur) != _cle_connue(nom) else ''


def _motif_connu(cle):
    return r'[ \t\n\r\f\v\-]+'.join(re.escape(p) for p in cle.split(' '))


def _expression(cles):
    return re.compile(r'(?<![\w@])(?:%s)(?![\w@])' % '|'.join(_motif_connu(k) for k in sorted(cles, key=len, reverse=True))) if cles else None


def _recherche(connues):
    """(catégorie par nom replié, expression des noms connus sur le texte replié, mots à masquer seuls → catégorie)."""
    cat, isoles = {}, {}

    def ajouter(texte, c):
        cle = _cle_connue(texte)
        if len(cle) >= 3:
            cat.setdefault(cle, c)
    for texte, c in connues:
        ajouter(texte, c)
        if c == 'PERSONNE':
            mots = texte.split()
            if 2 <= len(mots) <= 5:
                ajouter(' '.join(mots[-1:] + mots[:-1]), c)       # « ZELVANI Samira » pour « Samira ZELVANI »
                ajouter(' '.join(mots[1:] + mots[:1]), c)         # et l'inverse
                for mot in _mots_isolables(mots):
                    isoles[_fold(mot)] = 'PERSONNE'
        elif c == 'SOCIETE':
            coeur = _coeur_societe(texte)
            if len(coeur.split()) >= 2:
                ajouter(coeur, c)
            elif len(coeur) >= 4 and coeur.upper() not in NOT_NAMES and re.fullmatch(r"[^\W\d_]+(?:[\-'’][^\W\d_]+)*", coeur):
                isoles.setdefault(_fold(coeur), 'SOCIETE')
    return cat, _expression(cat), isoles


_DERNIERE = {'cle': None, 'valeur': None}     # structures de recherche de la dernière liste de noms (le registre est relu à chaque émission)


def _chaines(valeur):
    if isinstance(valeur, str):
        yield valeur
    elif isinstance(valeur, dict):
        for v in valeur.values():
            yield from _chaines(v)
    elif isinstance(valeur, (list, tuple)):
        for v in valeur:
            yield from _chaines(v)


class Pseudonymizer:
    def __init__(self, config=None, known=None):
        self.known = known if known is not None else (known_entities(config or {}) if config else [])
        self.forward = {}      # (catégorie, clé : entity_key) -> marqueur
        self.back = {}         # marqueur -> texte d'origine
        self.counts = {c: 0 for c in CATEGORIES}
        self.surnames = {}     # 5.6.77 : nom de famille replié -> {clé du nom complet: texte d'origine}
        self.method = False    # 5.6.86 : texte d'une compétence en cours de traitement
        # 5.6.109 (P03) : marqueurs écrits tels quels dans les sources
        self.litteraux = set()         # numéros réservés : jamais attribués à une identité, laissés tels quels au retour
        self.echappes = set()          # formes distinctes émises (« [LITTERAL_PERSONNE_1] »)
        self.echapper_tout = False     # table stable d'un compte (connecteur Claude) : un littéral part toujours sous sa forme distincte
        self.tolere = frozenset()      # marqueurs inconnus écrits par le correspondant lui-même, renvoyés tels quels dans un message d'erreur
        self.plancher = {}             # catégorie -> dernier numéro déjà employé puis purgé : jamais réattribué
        self._requete = (0, None)
        self._compile()
        self.ajouter(NOMS_DU_TOUR.get())

    def _compile(self):
        recherche = getattr(self.known, 'recherche', None)
        if recherche is None:
            cle = tuple(self.known)
            if _DERNIERE['cle'] == cle:                # registre inchangé depuis la dernière émission : rien à recompiler
                recherche = _DERNIERE['valeur']
            else:
                recherche = _recherche(self.known)
                _DERNIERE.update(cle=cle, valeur=recherche)
            if isinstance(self.known, Instantane):
                self.known.recherche = recherche       # calculé une fois par instantané
        self.known_cat, self.known_rx, self.isoles = recherche

    def ajouter(self, noms):
        """5.6.88 : noms de plus pour cette requête ; les plus longs d'abord, comme ``known_entities``."""
        extra = [(re.sub(r'\s+', ' ', str(x)).strip(), c) for x, c in noms or ()]
        extra = [(x, c) for x, c in extra if len(x) >= 3 and _cle_connue(x) not in self.known_cat]
        if extra:
            self.known = sorted(list(self.known) + extra, key=lambda x: -len(x[0]))
            self._compile()

    # ------------------------------------------------------------------ marqueurs
    def _register(self, original):
        name = surname_of(original)
        if name:
            self.surnames.setdefault(name, {})[entity_key('PERSONNE', original)] = original

    def token(self, category, original, link=True):
        original = original.strip()
        if category == 'PERSONNE':
            if link and len(original.split()) == 1:
                # nom isolé : marqueur de la seule personne connue de ce nom ; homonymes → marqueur propre
                fulls = self.surnames.get(_fold(original)) or {}
                if len(fulls) == 1:
                    return self.token('PERSONNE', next(iter(fulls.values())), link=False)
            self._register(original)
        key = (category, entity_key(category, original))
        if key in self.forward:
            return self.forward[key]
        # 5.6.109 (P03) : jamais un numéro déjà attribué (table stable à trous), réservé par un littéral de la source, ni purgé
        n = max(sum(1 for k in self.forward if k[0] == category), self.plancher.get(category, 0)) + 1
        while '[%s_%d]' % (category, n) in self.back or '[%s_%d]' % (category, n) in self.litteraux:
            n += 1
        tok = '[%s_%d]' % (category, n)
        self.forward[key], self.back[tok] = tok, original
        self.counts[category] += 1
        return tok

    def adopt(self, tok, category, original):
        """Reprend un marqueur déjà attribué (table conservée d'un compte), sans le compter comme nouveau. 5.6.77."""
        self.forward[(category, entity_key(category, original))] = tok
        self.back[tok] = original
        if category == 'PERSONNE':
            self._register(original)

    def _outside(self, text, func):
        """Applique func aux seuls segments qui ne sont pas déjà des marqueurs (ni des littéraux échappés, 5.6.109)."""
        last, out = 0, []
        for m in PROTEGE.finditer(text):
            out.append(func(text[last:m.start()]))
            out.append(m.group(0))
            last = m.end()
        out.append(func(text[last:]))
        return ''.join(out)

    # ------------------------------------------------------------------ 5.6.109 (P03) : marqueurs littéraux des sources
    def reperer(self, valeur):
        """Marqueurs écrits tels quels dans TOUTES les sources d'une requête, relevés avant la première attribution : leur numéro est
        réservé, même si le gabarit arrive dans un message ultérieur."""
        if self.echapper_tout:
            return
        for s in _chaines(valeur):
            if '[' in s:
                for m in TOKEN.finditer(s):
                    if m.group(0) not in self.back:
                        self.litteraux.add(m.group(0))

    def _litteraux(self, s):
        """Dans une source : un marqueur dont le numéro est libre le reste (numéro réservé) ; un marqueur dont le numéro est déjà attribué,
        ou tout marqueur d'une table stable, part sous une forme distincte ; une forme distincte déjà présente gagne un cran, pour que le
        retour (un cran de moins) rende exactement la source."""
        def repl(m):
            tok = m.group(0)
            if tok.startswith('[LITTERAL_'):
                self.echappes.add('[LITTERAL_' + tok[1:])
                return '[LITTERAL_' + tok[1:]
            if tok in self.tolere:
                return tok
            if tok in self.back or (self.echapper_tout and tok not in self.litteraux):
                self.echappes.add('[LITTERAL_' + tok[1:])
                return '[LITTERAL_' + tok[1:]
            self.litteraux.add(tok)
            return tok
        return PROTEGE.sub(repl, s) if '[' in s else s

    def _connus(self, seg):
        """5.6.109 (P01) : noms connus du cabinet, cherchés sans accent ni casse (espaces et traits d'union équivalents)."""
        plie, rangs = _plie(seg)
        out, last = [], 0
        for m in self.known_rx.finditer(plie):
            a, b = _rangs(seg, rangs, m.start(), m.end())
            if a < last:
                continue
            out.append(seg[last:a])
            out.append(self.token(self.known_cat.get(_cle_connue(seg[a:b]), 'PERSONNE'), seg[a:b]))
            last = b
        out.append(seg[last:])
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

    def _spans(self, text, category, spans):
        """Remplace des intervalles [(début, fin)] d'un segment par des marqueurs."""
        out, last = [], 0
        for a, b in spans:
            out.append(text[last:a])
            out.append(self.token(category, text[a:b]))
            last = b
        out.append(text[last:])
        return ''.join(out)

    @staticmethod
    def _upper_span(m):
        """Partie à masquer d'une suite en MAJUSCULES (avec le prénom qui l'entoure), ou None pour un sigle."""
        words = re.split(r'[ \-]+', m.group(2))
        if all(w.strip("'’").upper() in NOT_NAMES or len(w) < 2 for w in words):
            return None
        use1 = bool(m.group(1)) and _fold(m.group(1)) in FIRST_NAMES        # prénom avant le NOM
        use3 = bool(m.group(3)) and _fold(m.group(3)) in FIRST_NAMES        # NOM suivi du prénom
        return (m.start(1) if use1 else m.start(2)), (m.end(3) if use3 else m.end(2))

    def _company_after(self, seg):
        """« ZORVAL SAS » : société ; les mots d'en-tête (« Contre », « LA ») ne font pas partie du nom. 5.6.77."""
        def repl(m):
            words = m.group(1).split(' ')
            while words and (words[0].upper() in NOT_NAMES or _fold(words[0]) in ('contre', 'entre', 'pour', 'chez', 'par')):
                words.pop(0)
            if not words:
                return m.group(0)
            name = ' '.join(words) + ' ' + m.group(2)
            return m.group(0)[:len(m.group(0)) - len(name)] + self.token('SOCIETE', name)
        return COMPANY_AFTER.sub(repl, seg)

    def _prescan(self, s):
        """5.6.77 : noms complets du texte relevés d'abord, pour qu'un nom isolé qui les précède soit rapproché correctement."""
        for m in UPPER_NAME.finditer(s):
            span = self._upper_span(m)
            if span:
                self._register(s[span[0]:span[1]])
        for m in FIRST_LAST.finditer(s):
            if _fold(m.group(1)) in FIRST_NAMES:
                self._register(m.group(0))

    # ------------------------------------------------------------------ texte
    def text(self, value):
        s = str(value)
        if not s.strip():
            return s
        s = JSON_BREAK.sub(lambda m: BREAKS[m.group(1)], JSON_CHAR.sub(_json_char, s))
        s = self._masked(self._litteraux(s))      # 5.6.109 (P03) : les marqueurs littéraux de la source d'abord
        for mark, back in BREAKS_BACK.items():
            s = s.replace(mark, back)
        return s

    def _same_names(self, seg):
        """5.6.86 : un nom masqué une fois l'est partout : autre casse, ordre inversé, nom de famille seul (« Madame Hélène KERVILAC » puis « Hélène Kervilac », « Kervilac »)."""
        for tok, original in list(self.back.items()):
            words = original.split()
            if tok.startswith('[PERSONNE_') and len(words) >= 2:
                for order in (words, words[::-1]):
                    seg = re.sub(r'(?<![\w@])' + r'[ \t\-]+'.join(re.escape(w) for w in order) + r'(?![\w@])', tok, seg, flags=re.I)
        return seg

    def _surnames_alone(self, seg):
        """Nom de famille seul d'une personne de la requête ; 5.6.109 (P01) : et mot seul d'une identité du registre (« Zelvani », « Zorval »).
        Le marqueur garde le mot tel qu'il est écrit : au retour, rien n'est complété d'après le registre."""
        def repl(m):
            mot = m.group(0)
            if mot.upper() in NOT_NAMES:
                return mot
            cle = _fold(mot)
            if cle in self.surnames:
                return self.token('PERSONNE', mot)
            cat = self.isoles.get(cle)
            return self.token(cat, mot) if cat else mot
        return ANY_WORD.sub(repl, seg)

    def _masked(self, s):
        self._prescan(s)
        if self.known_rx is not None:
            s = self._outside(s, self._connus)
        # 5.6.77 : numéros validés par leur clé (IBAN coupé sur deux lignes, NIR coupé, SIREN et carte par Luhn)
        s = self._outside(s, lambda seg: self._spans(seg, 'IBAN', iban_spans(seg)))
        s = self._outside(s, lambda seg: NIR_WRAPPED.sub(
            lambda m: self.token('NUMERO', m.group(0)) if nir_key_ok(''.join(m.groups())) else m.group(0), seg))
        for cat, rx in PATTERNS:
            if cat == 'PERSONNE' and rx.groups == 2:
                s = self._outside(s, lambda seg: rx.sub(lambda m: m.group(0)[:m.start(2) - m.start(0)] + self.token(
                    'PERSONNE', m.group(2), link=_fold(m.group(1)) not in PROFESSIONAL), seg))
                continue
            s = self._sub(rx, cat, s, 1 if rx.groups else 0)
            if cat == 'SOCIETE' and rx is PATTERNS[-3][1]:
                s = self._outside(s, self._company_after)
        s = self._outside(s, lambda seg: SIREN_FREE.sub(
            lambda m: self.token('NUMERO', m.group(0)) if siren_ok(m.group(1) + m.group(3) + m.group(4)) else m.group(0), seg))
        s = self._outside(s, lambda seg: CARD_FREE.sub(
            lambda m: self.token('NUMERO', m.group(0)) if luhn(m.group(0)) or len(m.group(0)) == 16 else m.group(0), seg))

        def upper(seg):
            def repl(m):
                span = self._upper_span(m)
                if not span:
                    return m.group(0)
                whole, base = m.group(0), m.start(0)
                start, end = span[0] - base, span[1] - base
                return whole[:start] + self.token('PERSONNE', whole[start:end]) + whole[end:]
            return UPPER_NAME.sub(repl, seg)
        if not self.method:
            s = self._outside(s, upper)

        def first_last(seg):
            def repl(m):
                if _fold(m.group(1)) not in FIRST_NAMES:
                    return m.group(0)
                return self.token('PERSONNE', m.group(0))
            return FIRST_LAST.sub(repl, seg)
        s = self._outside(s, first_last)
        s = BIRTH_PLACE.sub(lambda m: m.group(0)[:m.start(1) - m.start(0)] + self.token('ADRESSE', m.group(1)), s)
        return self._outside(self._outside(s, self._same_names), self._surnames_alone)

    def apply(self, value):
        self.reperer(value)      # 5.6.109 (P03) : littéraux de toute la valeur relevés avant la première attribution
        return self._apply(value)

    def _apply(self, value):
        if isinstance(value, dict):
            return {k: self._apply(v) for k, v in value.items()}
        if isinstance(value, list):
            return [self._apply(v) for v in value]
        if isinstance(value, str):
            return self.text(value)
        return value

    def apply_messages(self, messages, method_at=None):
        """``method_at`` : rang du texte d'une compétence : ses titres en majuscules ne sont pas des noms (5.6.86)."""
        self.reperer(messages)
        out = []
        for rank, m in enumerate(messages):
            if isinstance(m, dict) and isinstance(m.get('content'), str):
                self.method = rank == method_at
                out.append({**m, 'content': self.text(m['content'])})
                self.method = False
            else:
                out.append(self._apply(m))
        return out

    # ------------------------------------------------------------------ retour
    def restore(self, text, json_mode=False):
        if not isinstance(text, str) or (not self.back and 'LITTERAL_' not in text):
            return text

        def repl(m):
            if m.group(1) is not None:       # 5.6.109 (P03) : littéral parti sous sa forme distincte : un cran de moins, jamais un nom
                return '[' + m.group(1) + ']'
            tok = '[%s_%s]' % (m.group(2), m.group(3))
            if tok not in self.back:
                return m.group(0)
            original = self.back[tok]
            return json.dumps(original, ensure_ascii=False)[1:-1] if json_mode else original
        return _RETOUR.sub(repl, text)

    def controle_retour(self, text):
        """5.6.109 (P04) : ce qui, dans une réponse, ne peut pas être rétabli sans deviner. ``inconnus`` : marqueurs bien formés jamais
        attribués dans cette requête (ni littéraux de ses sources) ; ``alteres`` : marqueurs recopiés sous une autre forme (casse, accent,
        espace, tiret). Formes telles qu'écrites par le modèle, sans doublon. Rien n'est corrigé ici."""
        inconnus, alteres = [], []
        if not isinstance(text, str) or ('_' not in text and '[' not in text):
            return {'inconnus': inconnus, 'alteres': alteres}
        for m in _RETOUR.finditer(text):
            if m.group(1) is not None:
                if m.group(0) not in self.echappes and not self.echapper_tout and m.group(0) not in inconnus:
                    inconnus.append(m.group(0))
                continue
            tok = '[%s_%s]' % (m.group(2), m.group(3))
            if tok not in self.back and tok not in self.litteraux and m.group(0) not in inconnus:
                inconnus.append(m.group(0))
        for m in MARQUEUR_LIBRE.finditer(text):
            forme = m.group(0)
            if TOKEN.fullmatch(forme) or ECHAPPE.fullmatch(forme) or LOOSE_TOKEN.fullmatch(forme):
                continue                     # forme exacte : déjà classée ci-dessus
            if forme not in alteres:
                alteres.append(forme)
        return {'inconnus': inconnus[:20], 'alteres': alteres[:20]}

    # ------------------------------------------------------------------ 5.6.109 (P01, P05) : identité restante
    def _identites_requete(self):
        """Expression (sur texte replié) des identités reconnues dans CETTE requête : pour les parties techniques de l'enveloppe."""
        if self._requete[0] != len(self.back):
            cles = set()
            for tok, original in self.back.items():
                cle, cat = _cle_connue(original), tok[1:].rsplit('_', 1)[0]
                if len(cle) < 3 or (cat in ('ADRESSE', 'DATE_NAISSANCE', 'CHEMIN') and ' ' not in cle):
                    continue                 # « Lyon » (lieu de naissance) reste un mot ordinaire ailleurs
                cles.add(cle)
                if cat == 'PERSONNE' and ' ' in cle:
                    cles.add(' '.join(cle.split(' ')[::-1]))
            self._requete = (len(self.back), _expression(cles))
        return self._requete[1]

    def residuelle(self, texte, technique=False):
        """Vrai si ``texte`` contient encore une identité de cet instantané. Texte déjà masqué (messages) : noms connus du cabinet et mots
        protégés seuls — ce que le masquage garantit. ``technique`` (schéma, clé, nom, métadonnée, jamais passés par le masquage) : aussi
        les identités reconnues dans la requête ; un mot seul n'y compte que s'il porte une majuscule (« client », « source », « documents »
        restent des mots techniques)."""
        s = JSON_BREAK.sub(lambda m: BREAKS[m.group(1)], JSON_CHAR.sub(_json_char, str(texte or '')))
        if len(s) < 3:
            return False
        requete = self._identites_requete() if technique else None
        last = 0
        for m in list(PROTEGE.finditer(s)) + [None]:
            seg = s[last:m.start()] if m is not None else s[last:]
            last = m.end() if m is not None else last
            if len(seg) < 3:
                continue
            plie, rangs = _plie(seg)
            for rx in (self.known_rx, requete):
                for trouve in (rx.finditer(plie) if rx is not None else ()):
                    if not technique or not re.fullmatch(r'[^\W\d_]+', trouve.group(0)):
                        return True
                    if seg[_rangs(seg, rangs, trouve.start(), trouve.end())[0]].isupper():
                        return True
            for trouve in ANY_WORD.finditer(seg):
                mot = trouve.group(0)
                if mot.upper() not in NOT_NAMES and (_fold(mot) in self.isoles or (technique and _fold(mot) in self.surnames)):
                    return True
        return False

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


# 5.6.109 (P04, P05) : état réel d'une ligne du journal. « Préparé » n'est pas « Émis » : un refus avant le réseau ne prouve aucune
# transmission ; '' : ligne écrite avant la 5.6.109 (l'état n'était pas noté).
ETATS = {'prepare': 'Préparé, non émis', 'emis': 'Émis', 'refuse': 'Refusé avant l’envoi', 'a_verifier': 'Restitution incomplète : à vérifier',
         'echec': 'Émission tentée, sans réponse exploitable', '': 'État non noté'}


def _colonnes(db):
    return {r[1] for r in db.execute('PRAGMA table_info(pseudo540_log)')}


def log_transmission(cfg, ps, messages):
    """Journal local de ce qui est PRÉPARÉ pour un envoi : catégories remplacées et texte déjà pseudonymisé, conservé 7 jours. Rend
    l'identifiant de la ligne (None si rien n'est écrit) ; son état réel est noté ensuite par ``noter_etat`` (5.6.109)."""
    state = str(cfg.get('state_dir') or '')
    if not state or not Path(state).is_dir():
        return None
    sent = '\n\n'.join('[%s]\n%s' % (m.get('role', ''), m.get('content', '')) for m in messages if isinstance(m, dict))
    try:
        db = _db(state)
        try:
            presentes = _colonnes(db)
            _colonnes_journal(db, [c for c in COLONNES_JOURNAL if c[0] not in presentes])     # écriture : jamais pendant une lecture
            db.execute('DELETE FROM pseudo540_log WHERE at<?', ((datetime.now(timezone.utc) - timedelta(days=7)).isoformat(),))
            ident = db.execute('INSERT INTO pseudo540_log(at,provider,model,purpose,counts,characters,sample,etat,revision,detecteur) VALUES(?,?,?,?,?,?,?,?,?,?)',
                               (datetime.now(timezone.utc).isoformat(), str(cfg.get('provider_id') or cfg.get('id') or ''), str(cfg.get('model') or ''),
                                str(cfg.get('purpose') or ''), json.dumps(ps.summary()), len(sent), sent[:20000], 'prepare',
                                int(getattr(ps.known, 'revision', None) or 0), VERSION_DETECTEUR)).lastrowid
            db.execute('DELETE FROM pseudo540_log WHERE id NOT IN (SELECT id FROM pseudo540_log ORDER BY id DESC LIMIT 200)')
            db.commit()
            return ident
        finally:
            db.close()
    except sqlite3.Error:
        return None


def noter_etat(cfg, ident, etat, recu=None, code='', retour=''):
    """État réel d'une ligne du journal : ``emis`` avec le reçu du corps exact (empreinte et taille, aucun contenu), ``refuse`` avec le code
    du contrôle, ``a_verifier`` avec la réponse reçue (encore pseudonymisée : aucun nom rétabli n'y figure), ``echec``. Ne lève jamais."""
    state = str((cfg or {}).get('state_dir') or '')
    if not ident or not state or etat not in ETATS:
        return
    try:
        db = sqlite3.connect(Path(state) / 'desk.sqlite3', timeout=10)
        try:
            db.execute('UPDATE pseudo540_log SET etat=?, empreinte=?, octets=?, code=?, retour=? WHERE id=?',
                       (etat, str((recu or {}).get('sha256') or ''), int((recu or {}).get('octets') or 0), str(code or '')[:80], str(retour or '')[:20000], int(ident)))
            db.commit()
        finally:
            db.close()
    except (sqlite3.Error, ValueError, TypeError):
        pass


def recent(state_dir, limit=20):
    try:
        db = _db(state_dir)
        try:
            plus = [c for c in ('etat', 'empreinte', 'octets', 'code') if c in _colonnes(db)]
            rows = db.execute('SELECT %s FROM pseudo540_log ORDER BY id DESC LIMIT ?' % ','.join(['id', 'at', 'provider', 'model', 'purpose', 'counts', 'characters'] + plus),
                              (limit,)).fetchall()
        finally:
            db.close()
    except sqlite3.Error:
        return []
    out = []
    for r in rows:
        ligne = {'id': r[0], 'at': r[1], 'provider': r[2], 'model': r[3], 'purpose': r[4], 'counts': json.loads(r[5] or '{}'), 'characters': r[6],
                 'etat': '', 'empreinte': '', 'octets': 0, 'code': ''}
        ligne.update(zip(plus, r[7:]))
        out.append(ligne)
    return out


def sample(state_dir, ident):
    db = _db(state_dir)
    try:
        garde = 'retour' in _colonnes(db)
        row = db.execute('SELECT sample%s FROM pseudo540_log WHERE id=?' % (',retour' if garde else ''), (int(ident),)).fetchone()
    finally:
        db.close()
    if not row:
        return ''
    if garde and row[1]:     # 5.6.109 (P04) : réponse gardée « à vérifier », telle que reçue (marqueurs non rétablis)
        return row[0] + '\n\n--- Réponse reçue, gardée à vérifier (marqueurs non rétablis) ---\n' + row[1]
    return row[0]


# 5.6.109 (P01) : ce que le masquage ne sait pas reconnaître, dit tel quel partout où la pseudonymisation est annoncée.
LIMITES = (
    'Un nom que le cabinet ne connaît pas, écrit sans civilité ni majuscule, ou dans une langue que les règles ne couvrent pas, n’est pas reconnu : il part tel quel.',
    'Le nom seul d’une personne du registre est masqué quand il porte une majuscule ; un prénom usuel seul, un surnom ou une abréviation ne le sont pas.',
    'Les détails d’une affaire (dates, montants, lieux, fonctions, faits singuliers) ne sont pas masqués : ils peuvent suffire à reconnaître une personne.',
    'Sur une image, seuls les mots lus par la lecture locale sont noircis : un visage, une signature, un tampon, un code-barres ou un mot mal lu restent visibles.',
    'Un texte sans aucun remplacement signifie que rien n’a été reconnu, pas qu’il ne contient aucune donnée personnelle.',
)
NOTE_APERCU = ('Ce qui n’a pas été remplacé ci-dessus partirait tel quel : relisez le texte transmis. Un aperçu sans remplacement ne prouve pas '
               'l’absence de donnée personnelle.')


def preview(config, text, matter=''):
    """Aperçu : texte tel qu'il partirait, et tableau des remplacements (affiché à l'avocat seulement, jamais envoyé). 5.6.109 (P01) :
    l'aperçu dit aussi ce qu'il couvre (nombres seulement) et ce que le masquage ne sait pas reconnaître."""
    text = str(text or '')
    if not 1 <= len(text) <= 60000:
        from .common import Stop
        raise Stop('apercu_taille_invalide')
    ps = Pseudonymizer(config)
    sent = ps.text(text)
    table = [{'token': tok, 'original': original, 'category': LABELS.get(tok[1:].rsplit('_', 1)[0], '')} for tok, original in ps.back.items()]
    return {'sent': sent, 'replacements': table, 'counts': ps.summary(), 'restored_ok': ps.restore(sent) == text,
            'limites': list(LIMITES), 'note': NOTE_APERCU, 'litteraux': sorted(ps.litteraux | ps.echappes),
            'couverture': {'identites': len(ps.known), 'registres': dict(getattr(ps.known, 'registres', {}) or {}),
                           'tronques': list(getattr(ps.known, 'tronques', ()) or ()), 'revision': getattr(ps.known, 'revision', None),
                           'detecteur': VERSION_DETECTEUR}}
