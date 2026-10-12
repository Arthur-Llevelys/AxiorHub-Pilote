"""5.6.36 : Corporate — secrétariat juridique des sociétés clientes, en une saisie.

- **Sociétés** : fiche (forme, capital, siège, RCS, objet, exercice), dirigeants, associés ; rattachée à un dossier du cabinet où les actes
  sont rangés (« JURIDIQUE/AAAA-MM-JJ - opération »).
- **Opérations** : approbation des comptes (convocation, procuration, feuille de présence, rapport de gestion établi à partir du PDF des
  comptes analysé par l'IA, annexe, rapport spécial, projet de résolutions, PV d'AG ou décisions de l'associé unique, extrait du PV
  pour l'affectation du résultat, pouvoir INPI) ; transfert de siège ; changement de dirigeant ; autre modification ; cession de titres ;
  création de société selon sa forme.
- **Une saisie, quatre effets** (cession) : données structurées ; actes générés (modèles du cabinet ou génériques) ; table de
  capitalisation mise à jour ; registres des mouvements de titres et des décisions complétés.
- **Formulaires modifiables** : champs personnalisés par opération (utilisables comme balises dans les modèles du cabinet) ; modèle Word
  du cabinet choisi document par document.
Aucun acte n'est signé ni déposé : les documents sont des projets à relire ; les formalités restent faites par le cabinet.
"""
from datetime import date, datetime, timezone
from decimal import Decimal, InvalidOperation
from html import escape as e
import base64
import csv
import hashlib
import io
import json
import re
import unicodedata
import uuid
from pathlib import Path

from .common import Stop, clean_path, fold, load_matters, matter_display
from .erreurs5634 import Qualified
from .corporate_modeles5636 import DOCS, OPERATIONS

FORMS = {
    'SAS': ('société par actions simplifiée', 'actions', 'Président', False),
    'SASU': ('société par actions simplifiée unipersonnelle', 'actions', 'Président', True),
    'SARL': ('société à responsabilité limitée', 'parts sociales', 'Gérant', False),
    'EURL': ('entreprise unipersonnelle à responsabilité limitée', 'parts sociales', 'Gérant', True),
    'SA': ('société anonyme', 'actions', 'Directeur général', False),
    'SCI': ('société civile immobilière', 'parts sociales', 'Gérant', False),
    'SNC': ('société en nom collectif', 'parts sociales', 'Gérant', False),
    'SELARL': ('société d’exercice libéral à responsabilité limitée', 'parts sociales', 'Gérant', False),
    'SELAS': ('société d’exercice libéral par actions simplifiée', 'actions', 'Président', False),
}
CONVENTIONS = {'SAS': 'L. 227-10', 'SASU': 'L. 227-10', 'SELAS': 'L. 227-10', 'SARL': 'L. 223-19', 'EURL': 'L. 223-19', 'SELARL': 'L. 223-19',
               'SA': 'L. 225-38'}
MONTHS = ('janvier', 'février', 'mars', 'avril', 'mai', 'juin', 'juillet', 'août', 'septembre', 'octobre', 'novembre', 'décembre')
SETTINGS_FIELDS = 'corporate5636:champs'
SETTINGS_TEMPLATES = 'corporate5636:modeles'
SETTINGS_REPORTS = 'corporate5636:rapports_modeles'   # dernier contrôle des balises de chaque modèle du cabinet
SETTINGS_OWN = 'corporate5636:actes_cabinet'   # « Mes actes » : [{'id', 'label', 'source', 'ops'}]
TAG = re.compile(r'\{\{\s*(\w+)\s*\}\}')       # {{balise}} ou {{ balise }}
WORD_PARTS = re.compile(r'word/(document|header\d*|footer\d*)\.xml')
# Société et opérations fictives : exemples affichés à côté de chaque variable (aucune donnée du cabinet).
SAMPLE_COMPANY = {'id': '', 'denomination': 'EXEMPLE SAS', 'forme': 'SAS', 'capital': '10000', 'nominal': '1', 'siege': '10 rue du Test, 69002 Lyon',
                  'rcs': 'Lyon', 'siren': '123456789', 'objet': 'Conseil (exemple)', 'duree': '99', 'date_cloture': '31/12', 'matter': ''}
_COMMON = {'mandataire': 'Cabinet Exemple', 'ville': 'Lyon'}
SAMPLES = {
    'agoa': {**_COMMON, 'exercice_clos': '2025-12-31', 'date_ag': '2026-06-15', 'heure_ag': '10:00', 'lieu_ag': '10 rue du Test, 69002 Lyon',
             'resultat': '20000', 'reserve_legale_existante': '0', 'dividendes': '5000', 'conventions': '',
             'dividendes_anterieurs': 'Il est rappelé qu’aucun dividende n’a été distribué au titre des trois exercices précédents.'},
    'siege': {**_COMMON, 'nouveau_siege': '5 place Fictive, 69003 Lyon', 'date_effet': '2026-11-02', 'organe': 'assemblee',
              'ancien_siege': '10 rue du Test, 69002 Lyon'},
    'dirigeant': {**_COMMON, 'fonction': 'Président', 'motif': 'demission', 'entrant': 'Claire EXEMPLE', 'entrant_prenom': 'Claire', 'entrant_nom': 'EXEMPLE',
                  'entrant_naissance': '1er janvier 1980 à Lyon', 'entrant_nationalite': 'française', 'entrant_adresse': '1 rue Fictive, 69001 Lyon',
                  'entrant_email': 'claire@example.test', 'duree_mandat': 'une durée indéterminée', 'date_effet': '2026-11-01', 'organe': 'assemblee',
                  'sortant_nom': 'Paul MARTIN'},
    'modification': {**_COMMON, 'nature': 'Changement de dénomination', 'champ': 'denomination', 'article': '3', 'ancienne_redaction': 'EXEMPLE SAS',
                     'nouvelle_redaction': 'EXEMPLE CONSEIL', 'date_effet': '2026-11-01', 'organe': 'assemblee'},
    'cession': {**_COMMON, 'cedant': 'Paul MARTIN', 'cedant_nom': 'Paul MARTIN', 'cessionnaire_nom': 'Lucie BERNARD', 'cessionnaire_email': 'lucie@example.test',
                'nombre': '1000', 'categorie': 'ordinaires', 'prix_unitaire': '2', 'date_cession': '2026-10-12', 'numero_mouvement': '3'},
    'diligence': {**_COMMON, 'nature': 'augmentation_capital', 'intitule': 'Augmentation de capital', 'date_effet': '2026-11-15', 'organe': 'assemblee',
                  'resolutions': '', 'nouveau_capital': '12000', 'emissions': 'Lucie BERNARD ; 2000', 'annulations': '', 'champ': '', 'nouvelle_valeur': ''},
    'creation': {**_COMMON, 'forme': 'SAS', 'denomination': 'EXEMPLE SAS', 'objet': 'Conseil (exemple)', 'siege': '10 rue du Test, 69002 Lyon',
                 'capital': '10000', 'nominal': '1', 'duree': '99', 'date_cloture': '31/12', 'liberation': 'intégralement libérées',
                 'associes': 'Paul MARTIN ; 6000 ; 6000\nAnne DUPONT ; 4000 ; 4000',
                 'associes_lignes': [['Paul MARTIN', '6000', 6000], ['Anne DUPONT', '4000', 4000]], 'dirigeant_prenom': 'Paul', 'dirigeant_nom': 'MARTIN',
                 'dirigeant_naissance': '2 février 1975 à Lyon', 'dirigeant_adresse': '3 rue Fictive, 69002 Lyon'},
}

# Champs des formulaires : (clé, libellé, type, valeur par défaut)
FIELDS = {
    'agoa': [('exercice_clos', 'Exercice clos le', 'date', ''), ('date_ag', 'Date de l’assemblée ou des décisions', 'date', ''),
             ('heure_ag', 'Heure', 'time', '10:00'), ('lieu_ag', 'Lieu', 'text', '{siege}'),
             ('resultat', 'Résultat de l’exercice (€, négatif si perte)', 'number', ''),
             ('reserve_legale_existante', 'Réserve légale déjà constituée (€)', 'number', '0'),
             ('reserve_legale', 'Dotation à la réserve légale (€ — vide : calcul automatique)', 'number', ''),
             ('dividendes', 'Dividendes distribués (€)', 'number', '0'),
             ('report_nouveau', 'Report à nouveau (€ — vide : le solde)', 'number', ''),
             ('conventions', 'Conventions réglementées (une par ligne ; vide : aucune)', 'textarea', ''),
             ('dividendes_anterieurs', 'Dividendes des trois exercices précédents (243 bis CGI)', 'textarea',
              'Il est rappelé qu’aucun dividende n’a été distribué au titre des trois exercices précédents. [À VÉRIFIER]'),
             ('cac', 'La société a un commissaire aux comptes', 'check', ''),
             ('comptes_pdf', 'Comptes annuels (PDF dans Nextcloud) pour le rapport de gestion', 'path', ''),
             ('mandataire', 'Mandataire pour les formalités', 'text', '{cabinet}'), ('ville', 'Fait à', 'text', '{ville}')],
    'siege': [('nouveau_siege', 'Nouveau siège', 'text', ''), ('date_effet', 'Date d’effet', 'date', ''),
              ('organe', 'Décision prise par', 'select:organe', ''), ('ressort_change', 'Changement de greffe (hors ressort)', 'check', ''),
              ('mandataire', 'Mandataire pour les formalités', 'text', '{cabinet}'), ('ville', 'Fait à', 'text', '{ville}')],
    'dirigeant': [('fonction', 'Fonction', 'select:fonction', '{fonction}'), ('sortant', 'Dirigeant sortant', 'select:officer', ''),
                  ('motif', 'Motif du départ', 'select:motif', 'demission'), ('entrant_prenom', 'Prénom du nouveau dirigeant', 'text', ''),
                  ('entrant_nom', 'Nom du nouveau dirigeant', 'text', ''), ('entrant_naissance', 'Date et lieu de naissance', 'text', ''),
                  ('entrant_nationalite', 'Nationalité', 'text', 'française'), ('entrant_adresse', 'Adresse', 'text', ''),
                  ('entrant_email', 'Courriel', 'text', ''), ('duree_mandat', 'Durée du mandat', 'text', 'une durée indéterminée'),
                  ('date_effet', 'Date d’effet', 'date', ''), ('organe', 'Décision prise par', 'select:organe', 'assemblee'),
                  ('mandataire', 'Mandataire pour les formalités', 'text', '{cabinet}'), ('ville', 'Fait à', 'text', '{ville}')],
    'modification': [('nature', 'Nature de la modification', 'text', ''), ('champ', 'Donnée de la fiche à mettre à jour', 'select:champ', ''),
                     ('article', 'Article des statuts', 'text', ''), ('ancienne_redaction', 'Ancienne rédaction', 'textarea', ''),
                     ('nouvelle_redaction', 'Nouvelle rédaction (ou nouvelle valeur)', 'textarea', ''), ('date_effet', 'Date d’effet', 'date', ''),
                     ('organe', 'Décision prise par', 'select:organe', 'assemblee'),
                     ('mandataire', 'Mandataire pour les formalités', 'text', '{cabinet}'), ('ville', 'Fait à', 'text', '{ville}')],
    'cession': [('cedant', 'Cédant', 'select:holder', ''), ('cessionnaire_nom', 'Cessionnaire (nom ou dénomination)', 'text', ''),
                ('cessionnaire_email', 'Courriel du cessionnaire', 'text', ''), ('nombre', 'Nombre de titres cédés', 'number', ''),
                ('categorie', 'Catégorie', 'text', 'ordinaires'), ('prix_unitaire', 'Prix par titre (€)', 'number', ''),
                ('date_cession', 'Date de la cession', 'date', ''), ('agrement', 'Agrément du cessionnaire requis', 'check', ''),
                ('ville', 'Fait à', 'text', '{ville}')],
    'diligence': [('nature', 'Diligence', 'select:diligence', 'augmentation_capital'),
                  ('intitule', 'Intitulé précis (repris dans les actes ; vide : celui de la diligence)', 'text', ''),
                  ('date_effet', 'Date de la décision', 'date', ''), ('organe', 'Décision prise par', 'select:organe', 'assemblee'),
                  ('resolutions', 'Résolutions : une par ligne, en commençant par le verbe (« décide de… ») ; vide : trame proposée', 'textarea', ''),
                  ('nouveau_capital', 'Nouveau capital (€, si la diligence le modifie)', 'number', ''),
                  ('emissions', 'Titres émis : « Nom ; nombre de titres » (une ligne par souscripteur)', 'textarea', ''),
                  ('annulations', 'Titres annulés : « Nom ; nombre de titres » (réduction de capital)', 'textarea', ''),
                  ('champ', 'Donnée de la fiche à mettre à jour', 'select:champ', ''), ('nouvelle_valeur', 'Nouvelle valeur', 'text', ''),
                  ('mandataire', 'Mandataire pour les formalités', 'text', '{cabinet}'), ('ville', 'Fait à', 'text', '{ville}')],
    'creation': [('forme', 'Forme', 'select:forme', 'SAS'), ('denomination', 'Dénomination', 'text', ''), ('objet', 'Objet social', 'textarea', ''),
                 ('siege', 'Siège social', 'text', ''), ('capital', 'Capital (€)', 'number', ''), ('nominal', 'Valeur nominale d’un titre (€)', 'number', '1'),
                 ('duree', 'Durée (années)', 'number', '99'), ('date_cloture', 'Clôture de l’exercice (JJ/MM)', 'text', '31/12'),
                 ('associes', 'Associés : « Nom ; apport en € ; nombre de titres » (une ligne par associé)', 'textarea', ''),
                 ('liberation', 'Libération des apports en numéraire', 'select:liberation', 'intégralement libérées'),
                 ('dirigeant_prenom', 'Prénom du premier dirigeant', 'text', ''), ('dirigeant_nom', 'Nom du premier dirigeant', 'text', ''),
                 ('dirigeant_naissance', 'Date et lieu de naissance', 'text', ''), ('dirigeant_adresse', 'Adresse', 'text', ''),
                 ('matter', 'Dossier du cabinet', 'select:matter', ''), ('mandataire', 'Mandataire pour les formalités', 'text', '{cabinet}'),
                 ('ville', 'Fait à', 'text', '{ville}')],
}
CHOICES = {
    'organe': [('dirigeant', 'Le dirigeant (si les statuts le permettent)'), ('assemblee', 'Les associés (assemblée ou décision collective)'),
               ('associe_unique', 'L’associé unique')],
    'motif': [('demission', 'Démission'), ('revocation', 'Révocation'), ('fin_mandat', 'Fin de mandat'), ('deces', 'Décès'), ('aucun', 'Aucun (nomination seule)')],
    'fonction': [(x, x) for x in ('Président', 'Directeur général', 'Directeur général délégué', 'Gérant', 'Cogérant', 'Commissaire aux comptes')],
    'champ': [('', 'Aucune'), ('denomination', 'Dénomination'), ('objet', 'Objet social'), ('capital', 'Capital'), ('duree', 'Durée')],
    'liberation': [('intégralement libérées', 'Intégralement libérées'), ('libérées de moitié', 'Libérées de la moitié (SAS/SARL)')],
    'forme': [(k, '%s — %s' % (k, v[0])) for k, v in FORMS.items()],
    'diligence': [('augmentation_capital', 'Augmentation de capital'), ('reduction_capital', 'Réduction de capital'),
                  ('dissolution', 'Dissolution anticipée et liquidation amiable'), ('transformation', 'Transformation de la société'),
                  ('objet', 'Modification de l’objet social'), ('denomination', 'Changement de dénomination'), ('duree', 'Prorogation de la durée'),
                  ('commissaire', 'Nomination ou renouvellement du commissaire aux comptes'), ('sommeil', 'Mise en sommeil'),
                  ('convention', 'Autorisation d’une convention réglementée'), ('autre', 'Autre diligence (à préciser)')],
}
# Trame de résolutions proposée pour chaque diligence (l'avocat la remplace ou la complète ; [À COMPLÉTER] signale l'information manquante).
RESOLUTIONS = {
    'augmentation_capital': ['décide d’augmenter le capital social d’une somme de [À COMPLÉTER] € pour le porter à {{nouveau_capital_fr}}, par émission '
                             'de [À COMPLÉTER] titres nouveaux de {{nominal_fr}} chacun, à souscrire et libérer en numéraire',
                             'constate la souscription et la libération des titres nouveaux, ainsi qu’il résulte du certificat du dépositaire',
                             'décide de modifier corrélativement l’article [À COMPLÉTER] des statuts'],
    'reduction_capital': ['décide de réduire le capital social de [À COMPLÉTER] € pour le ramener à {{nouveau_capital_fr}}, par voie de [À COMPLÉTER : '
                          'rachat et annulation de titres / réduction de la valeur nominale]',
                          'décide de modifier corrélativement l’article [À COMPLÉTER] des statuts'],
    'dissolution': ['décide la dissolution anticipée de la société à compter du {{date_effet_fr}} et sa mise en liquidation amiable',
                    'nomme en qualité de liquidateur [À COMPLÉTER], avec les pouvoirs les plus étendus pour réaliser l’actif et apurer le passif',
                    'fixe le siège de la liquidation à [À COMPLÉTER]'],
    'transformation': ['décide de transformer la société en [À COMPLÉTER : forme] à compter du {{date_effet_fr}}, sans création d’un être moral nouveau',
                       'adopte article par article les statuts de la société sous sa nouvelle forme', 'nomme [À COMPLÉTER] en qualité de [À COMPLÉTER : dirigeant]'],
    'objet': ['décide de modifier l’objet social, désormais rédigé comme suit : « {{nouvelle_valeur}} »', 'décide de modifier corrélativement l’article [À COMPLÉTER] des statuts'],
    'denomination': ['décide de remplacer la dénomination sociale par « {{nouvelle_valeur}} » à compter du {{date_effet_fr}}',
                     'décide de modifier corrélativement l’article [À COMPLÉTER] des statuts'],
    'duree': ['décide de proroger la durée de la société de [À COMPLÉTER] années, soit jusqu’au [À COMPLÉTER]', 'décide de modifier corrélativement l’article [À COMPLÉTER] des statuts'],
    'commissaire': ['nomme [À COMPLÉTER] en qualité de commissaire aux comptes pour une durée de six exercices, soit jusqu’à l’issue de la décision appelée à '
                    'statuer sur les comptes de l’exercice clos en [À COMPLÉTER]'],
    'sommeil': ['décide la cessation temporaire et totale d’activité de la société (mise en sommeil) à compter du {{date_effet_fr}}, pour une durée '
                'maximale de deux ans, l’immatriculation étant maintenue'],
    'convention': ['autorise la conclusion de la convention suivante : [À COMPLÉTER : parties, objet, conditions], l’associé intéressé ne prenant pas part au vote'],
    'autre': ['[À COMPLÉTER : texte de la décision]'],
}
ORDINALS = ('Première', 'Deuxième', 'Troisième', 'Quatrième', 'Cinquième', 'Sixième', 'Septième', 'Huitième', 'Neuvième', 'Dixième')


def _now():
    return datetime.now(timezone.utc).isoformat(timespec='seconds')


def schema(desk):
    if desk.db.in_transaction:   # 5.6.37 (audit F08) : executescript validerait l'opération en cours ; schéma créé avant BEGIN
        return
    _schema_tables(desk)
    have = {r[1] for r in desk.db.execute('PRAGMA table_info(operations5636)')}
    if 'idem' not in have:   # 5.6.37 (F10) : clé d'idempotence du formulaire
        desk.db.execute("ALTER TABLE operations5636 ADD COLUMN idem TEXT NOT NULL DEFAULT ''")
        # opérations 5.6.36 : leurs effets ont été inscrits dès la saisie (avant la réalisation) ; elles restent signalées comme telles
        desk.db.execute("UPDATE operations5636 SET data=json_set(data, '$.effets', 'appliques_5636') WHERE json_valid(data)")
    desk.db.execute("CREATE INDEX IF NOT EXISTS operations5636_idem ON operations5636(idem)")
    desk.db.commit()


def _schema_tables(desk):
    desk.db.executescript('''
    CREATE TABLE IF NOT EXISTS companies5636(id TEXT PRIMARY KEY, denomination TEXT NOT NULL, forme TEXT NOT NULL, capital TEXT NOT NULL DEFAULT '0',
      nominal TEXT NOT NULL DEFAULT '1', siege TEXT NOT NULL DEFAULT '', rcs TEXT NOT NULL DEFAULT '', siren TEXT NOT NULL DEFAULT '', objet TEXT NOT NULL DEFAULT '',
      duree TEXT NOT NULL DEFAULT '99', date_cloture TEXT NOT NULL DEFAULT '31/12', matter TEXT NOT NULL DEFAULT '', contact TEXT NOT NULL DEFAULT '',
      data TEXT NOT NULL DEFAULT '{}', archived INTEGER NOT NULL DEFAULT 0, created TEXT NOT NULL, updated TEXT NOT NULL);
    CREATE TABLE IF NOT EXISTS officers5636(id TEXT PRIMARY KEY, company TEXT NOT NULL, contact TEXT NOT NULL DEFAULT '', name TEXT NOT NULL, fonction TEXT NOT NULL,
      start TEXT NOT NULL DEFAULT '', end TEXT NOT NULL DEFAULT '', operation TEXT NOT NULL DEFAULT '');
    CREATE TABLE IF NOT EXISTS movements5636(id INTEGER PRIMARY KEY AUTOINCREMENT, company TEXT NOT NULL, numero INTEGER NOT NULL, day TEXT NOT NULL,
      kind TEXT NOT NULL, from_name TEXT NOT NULL DEFAULT '', from_contact TEXT NOT NULL DEFAULT '', to_name TEXT NOT NULL DEFAULT '',
      to_contact TEXT NOT NULL DEFAULT '', category TEXT NOT NULL DEFAULT 'ordinaires', quantity INTEGER NOT NULL, unit_price TEXT NOT NULL DEFAULT '',
      operation TEXT NOT NULL DEFAULT '', created TEXT NOT NULL);
    CREATE TABLE IF NOT EXISTS decisions5636(id TEXT PRIMARY KEY, company TEXT NOT NULL, day TEXT NOT NULL, organe TEXT NOT NULL, title TEXT NOT NULL,
      summary TEXT NOT NULL DEFAULT '', operation TEXT NOT NULL DEFAULT '', created TEXT NOT NULL);
    CREATE TABLE IF NOT EXISTS operations5636(id TEXT PRIMARY KEY, company TEXT NOT NULL, kind TEXT NOT NULL, data TEXT NOT NULL, docs TEXT NOT NULL,
      state TEXT NOT NULL, result TEXT NOT NULL DEFAULT '{}', job INTEGER, owner TEXT NOT NULL DEFAULT '', created TEXT NOT NULL, updated TEXT NOT NULL);''')


# ------------------------------------------------------------------------------------------------ formats
def _dec(value, code='montant_invalide'):
    raw = str(value if value is not None else '').strip().replace(' ', '').replace(' ', '').replace(',', '.').replace('€', '')
    if raw == '':
        return None
    try:
        out = Decimal(raw)
    except InvalidOperation:
        raise Stop(code) from None
    if not out.is_finite():
        raise Stop(code)
    return out


def money(value):
    d = _dec(value) or Decimal(0)
    s = '{:,.2f}'.format(abs(d)).replace(',', ' ').replace('.', ',')
    return ('-' if d < 0 else '') + s + ' €'


def date_fr(iso):
    try:
        d = date.fromisoformat(str(iso)[:10])
    except ValueError:
        return str(iso or '[À COMPLÉTER]')
    return '%s %s %d' % ('1er' if d.day == 1 else d.day, MONTHS[d.month - 1], d.year)


def _slug(text):
    t = unicodedata.normalize('NFKD', str(text)).encode('ascii', 'ignore').decode().lower()
    return re.sub(r'[^a-z0-9]+', '_', t).strip('_')[:40]


# ------------------------------------------------------------------------------------------------ sociétés
def company(desk, cid):
    schema(desk)
    row = desk.db.execute('SELECT * FROM companies5636 WHERE id=?', (cid,)).fetchone()
    if not row:
        raise Stop('societe_absente')
    out = dict(row)
    out['data'] = json.loads(row['data'] or '{}')
    return out


def companies(desk):
    schema(desk)
    return [dict(r) for r in desk.db.execute('SELECT * FROM companies5636 WHERE archived=0 ORDER BY lower(denomination)')]


def company_save(desk, data, owner='cabinet', commit=True):
    """5.6.37 (F08) : ``commit=False`` = écriture dans la transaction de l'opération (aucune validation intermédiaire ni journal)."""
    schema(desk)
    forme = str(data.get('forme') or 'SAS').upper()
    if forme not in FORMS:
        raise Stop('forme_societe_inconnue')
    denomination = re.sub(r'\s+', ' ', str(data.get('denomination') or '')).strip()[:200]
    if len(denomination) < 2:
        raise Stop('denomination_requise')
    # 5.6.37 (audit F07) : valeur par défaut réservée aux champs absents ; un zéro ou un négatif fourni est refusé
    capital = _dec(data.get('capital'), 'capital_invalide')
    nominal = _dec(data.get('nominal'), 'nominal_invalide')
    capital = Decimal(0) if capital is None else capital
    nominal = Decimal(1) if nominal is None else nominal
    if capital < 0 or (str(data.get('capital') or '').strip() and capital <= 0):
        raise Stop('capital_invalide')
    if nominal <= 0:
        raise Stop('nominal_invalide')
    siren = re.sub(r'\s', '', str(data.get('siren') or ''))
    if siren and not re.fullmatch(r'\d{9}', siren):
        raise Stop('contact_siren_invalide')
    mid = str(data.get('matter') or '')
    if mid and mid not in {m['id'] for m in load_matters(desk.c)}:
        raise Stop('dossier_absent')
    values = {'denomination': denomination, 'forme': forme, 'capital': str(capital), 'nominal': str(nominal),
              'siege': str(data.get('siege') or '').strip()[:300], 'rcs': str(data.get('rcs') or '').strip()[:80], 'siren': siren,
              'objet': str(data.get('objet') or '').strip()[:3000], 'duree': str(data.get('duree') or '99')[:6],
              'date_cloture': str(data.get('date_cloture') or '31/12')[:5], 'matter': mid}
    cid = str(data.get('id') or '')
    if cid:
        company(desk, cid)
        desk.db.execute('UPDATE companies5636 SET %s, updated=? WHERE id=?' % ','.join('%s=?' % k for k in values), list(values.values()) + [_now(), cid])
    else:
        cid = uuid.uuid4().hex
        from . import crm5636
        try:
            contact = crm5636.find_or_create(desk, {'kind': 'societe', 'category': 'client', 'name': denomination, 'siren': siren,
                                                    'address': values['siege'], **({'matter': mid, 'role': 'client'} if mid else {})}, source='corporate',
                                             commit=commit)['id']
        except Stop:
            contact = ''
        desk.db.execute('INSERT INTO companies5636(id,%s,contact,created,updated) VALUES(?,%s,?,?,?)' % (','.join(values), ','.join('?' for _ in values)),
                        [cid] + list(values.values()) + [contact, _now(), _now()])
    if not commit:
        return {'id': cid}
    desk.db.commit()
    desk.audit('corporate5636_societe', {'id': cid, 'owner': owner})
    return {'id': cid, 'goto': '?societe=' + cid, 'message': 'Société enregistrée.'}


def officers(desk, cid, current=True):
    schema(desk)
    sql = 'SELECT * FROM officers5636 WHERE company=?' + (" AND end=''" if current else '') + ' ORDER BY start'
    return [dict(r) for r in desk.db.execute(sql, (cid,))]


def holdings(desk, cid):
    """Table de capitalisation : titres détenus par associé et par catégorie, d'après le registre des mouvements."""
    schema(desk)
    acc = {}
    for m in desk.db.execute('SELECT * FROM movements5636 WHERE company=? ORDER BY numero', (cid,)):
        if m['from_name']:
            k = (m['from_contact'] or m['from_name'], m['category'])
            acc.setdefault(k, {'name': m['from_name'], 'contact': m['from_contact'], 'category': m['category'], 'quantity': 0})['quantity'] -= m['quantity']
        if m['to_name']:
            k = (m['to_contact'] or m['to_name'], m['category'])
            acc.setdefault(k, {'name': m['to_name'], 'contact': m['to_contact'], 'category': m['category'], 'quantity': 0})['quantity'] += m['quantity']
    rows = [v for v in acc.values() if v['quantity'] > 0]   # les positions négatives sont signalées par anomalies(), jamais masquées
    total = sum(v['quantity'] for v in rows) or 0
    for v in rows:
        v['pct'] = (Decimal(v['quantity']) * 100 / Decimal(total)).quantize(Decimal('0.01')) if total else Decimal(0)
    return sorted(rows, key=lambda v: (-v['quantity'], v['name'].casefold())), total


def anomalies(desk, cid):
    """5.6.37 (audit F06) : positions négatives du registre (opérations antérieures) : affichées comme anomalies à rapprocher."""
    acc = {}
    for m in desk.db.execute('SELECT * FROM movements5636 WHERE company=? ORDER BY numero', (cid,)):
        if m['from_name']:
            k = (m['from_contact'] or m['from_name'], m['category'])
            acc.setdefault(k, {'name': m['from_name'], 'category': m['category'], 'quantity': 0})['quantity'] -= m['quantity']
        if m['to_name']:
            k = (m['to_contact'] or m['to_name'], m['category'])
            acc.setdefault(k, {'name': m['to_name'], 'category': m['category'], 'quantity': 0})['quantity'] += m['quantity']
    return [v for v in acc.values() if v['quantity'] < 0]


def _movement(desk, cid, day, kind, qty, category, op, from_=('', ''), to=('', ''), price=''):
    n = (desk.db.execute('SELECT MAX(numero) FROM movements5636 WHERE company=?', (cid,)).fetchone()[0] or 0) + 1
    desk.db.execute('INSERT INTO movements5636(company,numero,day,kind,from_name,from_contact,to_name,to_contact,category,quantity,unit_price,operation,created) '
                    'VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?)', (cid, n, day, kind, from_[0], from_[1], to[0], to[1], category or 'ordinaires', int(qty), str(price),
                                                          op, _now()))
    return n


def _decision(desk, cid, day, organe, title, summary, op):
    desk.db.execute('INSERT INTO decisions5636 VALUES(?,?,?,?,?,?,?,?)', (uuid.uuid4().hex, cid, day, organe, title[:300], summary[:2000], op, _now()))


# ------------------------------------------------------------------------------------------------ contexte des actes
def _cabinet(desk):
    return (desk.c.get('mail') or {}).get('from_name') or (desk.c.get('cabinet') or {}).get('name') or 'le cabinet'


def _city(c):
    m = re.search(r'\d{5}\s+([A-ZÀ-Üa-zà-ü\-\' ]+)$', c.get('siege') or '')
    return m.group(1).strip() if m else '[À COMPLÉTER]'


def context(desk, c, kind, data):
    forme = FORMS.get(c['forme'], FORMS['SAS'])
    unique = forme[3]
    current = officers(desk, c['id'])
    head = next((o for o in current if o['fonction'] in ('Président', 'Gérant', 'Directeur général')), current[0] if current else None)
    rows, total = holdings(desk, c['id'])
    organe = str(data.get('organe') or ('associe_unique' if unique else 'assemblee'))
    head_fn = head['fonction'] if head else forme[2]
    ctx = {
        'denomination': c['denomination'], 'forme': c['forme'], 'forme_long': forme[0], 'capital_fr': money(c['capital']), 'siege': c['siege'] or '[À COMPLÉTER]',
        'rcs_mention': ('RCS %s %s' % (c['rcs'], ' '.join(re.findall(r'\d{3}', c['siren'])))).strip() if (c['rcs'] or c['siren']) else 'RCS [À COMPLÉTER]',
        'siren': c['siren'], 'objet': c['objet'] or '[À COMPLÉTER]', 'duree': c['duree'], 'nature_titres': forme[1], 'nature_titres_maj': forme[1].upper(),
        'dirigeant': head['name'] if head else '[À COMPLÉTER : dirigeant]', 'fonction_dirigeant': (head['fonction'] if head else forme[2]).lower(),
        'fonction_dirigeant_maj': (head['fonction'] if head else forme[2]).upper(), 'total_titres': str(total or '[À COMPLÉTER]'),
        'nominal_fr': money(c['nominal']), 'date_cloture_fr': c['date_cloture'], 'article_conventions': CONVENTIONS.get(c['forme'], '[sans objet]'),
        'date_acte_fr': date_fr(date.today().isoformat()), 'ville': str(data.get('ville') or _city(c)), 'mandataire': str(data.get('mandataire') or _cabinet(desk)),
        'decideur': 'L’associé unique' if unique else 'L’assemblée générale', 'decideur_min': 'il' if unique else 'elle',
        'organe_titre': 'PROCÈS-VERBAL DES DÉCISIONS DE L’ASSOCIÉ UNIQUE' if unique else 'PROCÈS-VERBAL DE L’ASSEMBLÉE GÉNÉRALE ORDINAIRE ANNUELLE',
        'organe_titre_court': 'DÉCISIONS DE L’ASSOCIÉ UNIQUE' if unique else 'ASSEMBLÉE GÉNÉRALE ORDINAIRE',
        'organe_titre_modif': {'dirigeant': 'DÉCISION DU %s' % head_fn.upper(), 'associe_unique': 'DÉCISIONS DE L’ASSOCIÉ UNIQUE'}.get(
            organe, 'PROCÈS-VERBAL DES DÉCISIONS COLLECTIVES DES ASSOCIÉS'),
        'decideur_modif': {'dirigeant': 'Le %s' % head_fn.lower(), 'associe_unique': 'L’associé unique'}.get(organe, 'La collectivité des associés'),
        'source_decision': {'dirigeant': 'd’une décision du %s' % head_fn.lower(), 'associe_unique': 'des décisions de l’associé unique'}.get(
            organe, 'des décisions collectives des associés'),
        'vote': '' if unique else 'Cette résolution est adoptée à [À COMPLÉTER : majorité / unanimité].',
        'signataires_pv': 'par l’associé unique' if unique else 'par le président de séance [et le secrétaire]',
        'preambule_pv': ('Le {{date_ag_fr}}, l’associé unique de la société, {{dirigeant}}, a pris les décisions suivantes.' if unique else
                         'Le {{date_ag_fr}} à {{heure_ag}}, les associés de la société se sont réunis en assemblée générale ordinaire annuelle, à {{lieu_ag}}, '
                         'sur convocation du {{fonction_dirigeant}}. Il a été établi une feuille de présence. Les associés présents ou représentés '
                         'détiennent [À COMPLÉTER] titres sur {{total_titres}}. L’assemblée est présidée par {{dirigeant}}.'),
        'tableau_associes': '\n'.join('%s — %s %s (%s) — %s %%' % (r['name'], r['quantity'], forme[1], r['category'], r['pct']) for r in rows)
        or '[À COMPLÉTER : associés]',
        'avertissement_ia': '',
    }
    for k, v in data.items():
        if isinstance(v, str) and k not in ctx:
            ctx[k] = v
    for k in [x for x in data if re.fullmatch(r'(date_(?:ag|effet|cession)|exercice_clos)', x)]:
        ctx[k + '_fr'] = date_fr(data[k]) if data[k] else '[À COMPLÉTER]'
    return ctx


def _agoa_context(desk, c, data, ctx):
    resultat = _dec(data.get('resultat'), 'resultat_invalide')
    if resultat is None:
        raise Stop('resultat_requis')
    capital = _dec(c['capital']) or Decimal(0)
    existing = _dec(data.get('reserve_legale_existante')) or Decimal(0)
    dividends = _dec(data.get('dividendes')) or Decimal(0)
    if resultat > 0:
        reserve = _dec(data.get('reserve_legale'))
        if reserve is None:
            reserve = max(Decimal(0), min((resultat * Decimal('0.05')).quantize(Decimal('0.01')), capital / 10 - existing))
        report = _dec(data.get('report_nouveau'))
        if report is None:
            report = resultat - reserve - dividends
        if report < 0 or abs(reserve + dividends + report - resultat) > Decimal('0.01'):
            raise Stop('affectation_incoherente')
        parts = ['à la réserve légale : %s' % money(reserve)] if reserve else []
        parts += ['à titre de dividendes : %s' % money(dividends)] if dividends else []
        parts += ['au compte « report à nouveau » : %s' % money(report)] if report else []
        ctx['texte_affectation'] = 'décide d’affecter le bénéfice de l’exercice, s’élevant à %s, de la manière suivante : %s.' % (money(resultat), ' ; '.join(parts))
        ctx['libelle_resultat'] = 'un bénéfice'
    else:
        if dividends:
            raise Stop('affectation_incoherente')
        ctx['texte_affectation'] = 'décide d’affecter la perte de l’exercice, s’élevant à %s, au compte « report à nouveau ».' % money(-resultat)
        ctx['libelle_resultat'] = 'une perte'
    ctx['resultat_fr'] = money(abs(resultat))
    conventions = [x.strip() for x in str(data.get('conventions') or '').splitlines() if x.strip()]
    if c['forme'] not in CONVENTIONS:
        ctx['texte_conventions'] = 'Sans objet : le régime des conventions réglementées ne s’applique pas à cette forme de société.'
        ctx['texte_conventions_resolution'] = 'prend acte de l’absence de convention réglementée.'
    elif conventions:
        ctx['texte_conventions'] = 'Les conventions suivantes sont intervenues au cours de l’exercice : ' + ' ; '.join(conventions) + '.'
        ctx['texte_conventions_resolution'] = ('après avoir entendu la lecture du rapport spécial, approuve les conventions qui y sont mentionnées '
                                               '(les associés intéressés ne prenant pas part au vote).')
    else:
        ctx['texte_conventions'] = 'Aucune convention visée à l’article %s du Code de commerce n’est intervenue au cours de l’exercice.' % CONVENTIONS[c['forme']]
        ctx['texte_conventions_resolution'] = 'prend acte de ce qu’aucune convention réglementée n’est intervenue au cours de l’exercice.'
    cac = bool(data.get('cac'))
    ctx['mention_cac'] = ' et du rapport du commissaire aux comptes' if cac else ''
    ctx['ligne_cac'] = '— rapport du commissaire aux comptes sur les comptes annuels ;' if cac else ''
    ctx['lieu_ag'] = str(data.get('lieu_ag') or c['siege'])
    ctx['heure_ag'] = str(data.get('heure_ag') or '10:00').replace(':', ' h ')
    ctx['intro_rapport'] = ('Conformément à la loi et aux statuts, nous vous rendons compte de la gestion de la société au cours de l’exercice clos le '
                            '%s et soumettons à votre approbation les comptes annuels de cet exercice.' % ctx.get('exercice_clos_fr', '[À COMPLÉTER]'))
    for k in ('activite', 'chiffres_cles', 'evenements', 'perspectives'):
        ctx.setdefault(k, '[À COMPLÉTER]')
    return ctx


ACCOUNTS_SCHEMA = {'type': 'object', 'additionalProperties': False, 'required': ['activite', 'chiffres', 'evenements', 'perspectives'],
                   'properties': {'activite': {'type': 'string', 'maxLength': 3000}, 'evenements': {'type': 'string', 'maxLength': 1500},
                                  'perspectives': {'type': 'string', 'maxLength': 1500},
                                  'chiffres': {'type': 'array', 'maxItems': 12, 'items': {
                                      'type': 'object', 'additionalProperties': False,
                                      'required': ['libelle', 'montant', 'devise', 'unite', 'exercice', 'extrait'],
                                      'properties': {'libelle': {'type': 'string', 'maxLength': 120}, 'montant': {'type': 'string', 'maxLength': 40},
                                                     'devise': {'type': 'string', 'enum': ['EUR', 'USD', 'GBP', 'CHF']},
                                                     'unite': {'type': 'string', 'enum': ['unites', 'milliers', 'millions']},
                                                     'exercice': {'type': 'string', 'maxLength': 10},
                                                     'extrait': {'type': 'string', 'maxLength': 300}}}}}}
# 5.6.37 (audit F05) : méthode de contrôle versionnée ; un rapport établi avec une méthode antérieure est à recontrôler.
METHODE_COMPTES = 3   # 5.6.39 (audit 5.6.37, F01) : preuve structurée, voir comptes5639
# Postes reconnus : un extrait ne justifie un chiffre que s'il porte sur le même poste (jamais le résultat net pour le chiffre d'affaires).
POSTES = (('chiffre d\'affaires', ('chiffre d\'affaires', 'chiffres d\'affaires', 'ventes de marchandises', 'production vendue')),
          ('resultat net', ('resultat net', 'benefice', 'perte de l\'exercice', 'resultat de l\'exercice')),
          ('resultat d\'exploitation', ('resultat d\'exploitation',)), ('resultat courant', ('resultat courant',)),
          ('total du bilan', ('total du bilan', 'total general', 'total actif', 'total passif')), ('capitaux propres', ('capitaux propres',)),
          ('endettement', ('dettes', 'emprunts', 'endettement')))
_UNITS = {'milliers': r'(en\s+milliers\s+d[\'’]euros|\bk\s?(?:€|eur)\b|\bkeur\b|en\s+k€)', 'millions': r'(en\s+millions\s+d[\'’]euros|\bm\s?€|\bmeur\b)'}
_NUMBER = re.compile(r'(\(\s*)?([-−]\s?)?(\d{1,3}(?:[  .]\d{3})+(?:,\d{1,2})?|\d+(?:,\d{1,2})?)(\s*\))?')


def _doc_unit(text):
    folded = fold(text)
    for unit, rx in _UNITS.items():
        if re.search(rx, folded):
            return unit
    return 'unites'


def _doc_currencies(text):
    from .preuve5634 import money_in_text
    found = {o['devise'] for o in money_in_text(text)}
    folded = fold(text)
    if re.search(r'\beuros?\b|€|\beur\b', folded):
        found.add('EUR')
    return found


def _numbers(passage):
    """Nombres d'un passage des comptes (séparateurs français, parenthèses = négatif), en Decimal signé."""
    out = []
    for m in _NUMBER.finditer(re.sub(r'\s+', ' ', str(passage or ''))):
        raw = m.group(3).replace(' ', '').replace(' ', '').replace('.', '').replace(',', '.')
        try:
            value = Decimal(raw)
        except InvalidOperation:
            continue
        negative = bool(m.group(2)) or (bool(m.group(1)) and bool(m.group(4)))
        out.append(-value if negative else value)
    return out


def check_figure(x, text, exercice=''):
    """Contrôle d'un chiffre du rapport de gestion. 5.6.39 (audit 5.6.37, F01) : preuve structurée (comptes5639) — poste, valeur, signe,
    devise, unité et exercice établis ensemble dans le même segment des comptes ou par leurs en-têtes."""
    from . import comptes5639
    return comptes5639.check(x, text, exercice)


def accounts_analysis(desk, c, raw, name, model=None, exercice=''):
    """Rapport de gestion : chiffres clés et commentaires établis à partir des comptes (PDF). 5.6.37 (audit F05) : chaque chiffre est contrôlé
    dans SON extrait (retrouvé dans les comptes, même poste, devise, unité, exercice, signe) ; à défaut il porte la réserve et sa cause."""
    from .documents import extract
    text = extract(raw, name, {**desk.c.get('documents', {}), 'max_document_chars': 120000})
    if model is None:
        from .docrequest520 import _model
        model = _model(desk, c['matter'])
    out = json.loads(model.complete([
        {'role': 'system', 'content': 'Tu prépares le rapport de gestion d’une société française à partir de ses comptes annuels. Relève les chiffres '
                                      'clés (chiffre d’affaires, résultat d’exploitation, résultat net, total du bilan, capitaux propres, endettement) avec, '
                                      'pour chacun, la devise, l’unité des comptes (unités, milliers, millions), l’exercice et l’extrait EXACT des comptes qui '
                                      'contient ce montant. Rédige ensuite des paragraphes sobres et factuels sur l’activité, les événements et les '
                                      'perspectives. N’invente rien : écris [À COMPLÉTER] si les comptes ne le disent pas. JSON conforme au schéma.'},
        {'role': 'user', 'content': text[:60000]}], temperature=0, max_tokens=4000, json_schema=ACCOUNTS_SCHEMA))
    lines, controls, unverified = [], [], 0
    for x in out.get('chiffres') or []:
        cause, detail = check_figure(x, text, exercice)
        unverified += cause is not None
        unit = {'milliers': ' (en milliers)', 'millions': ' (en millions)'}.get(x.get('unite'), '')
        from .preuve5634 import parse_amount
        shown = str(x.get('montant', ''))
        if (parse_amount(shown) or {}).get('devise') is None and x.get('devise'):
            shown += ' ' + str(x['devise'])
        lines.append('%s : %s%s%s' % (x.get('libelle', ''), shown, unit,
                                      '' if cause is None else ' [À VÉRIFIER dans les comptes : %s]' % cause.replace('_', ' ')))
        controls.append({'libelle': x.get('libelle', ''), 'montant': x.get('montant', ''), 'devise': x.get('devise', ''), 'unite': x.get('unite', ''),
                         'exercice': x.get('exercice', ''), 'statut': 'justifie' if cause is None else 'a_verifier', 'cause': cause or '', **detail})
    return {'activite': str(out.get('activite') or '[À COMPLÉTER]'), 'evenements': str(out.get('evenements') or '[À COMPLÉTER]'),
            'perspectives': str(out.get('perspectives') or '[À COMPLÉTER]'), 'chiffres_cles': '\n'.join(lines) or '[À COMPLÉTER]',
            'controle_chiffres': controls, 'methode_controle': METHODE_COMPTES,
            'avertissement_ia': ('Projet établi par AxiorHub à partir des comptes annuels (%s) : chiffres et commentaires à relire et valider par le '
                                 'dirigeant%s.') % (name, ' ; %d chiffre(s) non justifié(s) par leur extrait des comptes' % unverified if unverified else '')}


def _fill(template_lines, ctx):
    def sub(text):
        for _ in range(3):   # les balises peuvent en contenir d'autres (préambule du PV)
            text = TAG.sub(lambda m: str(ctx.get(m.group(1), '[À COMPLÉTER : %s]' % m.group(1))), text)
        return text
    out = []
    for line in template_lines:
        bold = line.startswith('# ')
        value = sub(line[2:] if bold else line)
        for part in value.split('\n'):
            if part.strip() or not value:
                out.append((part, bold))
    return out


def fill_docx(raw, ctx):
    """Modèle Word du cabinet : chaque {{balise}} remplacée, y compris quand Word l'a coupée entre plusieurs fragments."""
    import zipfile
    from xml.sax.saxutils import escape
    src, dst = io.BytesIO(raw), io.BytesIO()
    with zipfile.ZipFile(src) as zin, zipfile.ZipFile(dst, 'w', zipfile.ZIP_DEFLATED) as zout:
        for info in zin.infolist():
            data = zin.read(info.filename)
            if WORD_PARTS.fullmatch(info.filename):
                xml = data.decode('utf-8')

                def para(m):
                    p = m.group(0)
                    texts = re.findall(r'<w:t(?: [^>]*)?>([^<]*)</w:t>', p)
                    joined = ''.join(texts)
                    if '{{' not in joined:
                        return p
                    from html import unescape
                    replaced = TAG.sub(lambda t: str(ctx.get(t.group(1), '[À COMPLÉTER : %s]' % t.group(1))), unescape(joined))
                    first = [True]

                    def run(t):
                        if first[0]:
                            first[0] = False
                            return '<w:t xml:space="preserve">%s</w:t>' % escape(replaced).replace('\n', '</w:t><w:br/><w:t xml:space="preserve">')
                        return '<w:t xml:space="preserve"></w:t>'
                    return re.sub(r'<w:t(?: [^>]*)?>[^<]*</w:t>', run, p)
                xml = re.sub(r'<w:p[ >].*?</w:p>', para, xml, flags=re.S)
                data = xml.encode('utf-8')
            zout.writestr(info, data)
    return dst.getvalue()


def render(desk, doc_kind, ctx, dav=None):
    """Document final : « Mes actes » (modèle du cabinet obligatoire), sinon le modèle du cabinet choisi pour cet acte, sinon le
    modèle générique ; un modèle du cabinet illisible est signalé, jamais remplacé en silence."""
    if doc_kind.startswith('perso_'):
        deed = next((r for r in own_deeds(desk) if 'perso_' + r['id'] == doc_kind), None)
        if deed is None:
            raise Stop('acte_absent')
        raw = load_template(desk, deed['source'], dav)
        check_docx(raw)
        return fill_docx(raw, ctx), 'cabinet'
    mapping = desk.settings(SETTINGS_TEMPLATES, {}) or {}
    note = 'generique'
    if mapping.get(doc_kind):
        try:
            raw = load_template(desk, mapping[doc_kind], dav)
            check_docx(raw)
            return fill_docx(raw, ctx), 'cabinet'
        except Stop as exc:
            note = 'generique (modèle du cabinet non lu : %s)' % exc
    from .document_projects import _docx, _blank_docx
    title, lines = DOCS[doc_kind]
    return _docx(_blank_docx(), _fill(lines, ctx)), note


# ------------------------------------------------------------------------------------------------ modèles du cabinet
def _templates_dir(desk):
    return Path(desk.c['state_dir']) / 'corporate-modeles'


def check_docx(raw):
    """Modèle Word acceptable : fichier .docx lisible, 8 Mo au plus, sans macro."""
    import zipfile
    if not raw or len(raw) > 8_000_000:
        raise Stop('taille_modele_word_invalide')
    try:
        with zipfile.ZipFile(io.BytesIO(raw)) as z:
            names = set(z.namelist())
            if 'word/document.xml' not in names:
                raise Stop('modele_word_docx_invalide')
            if any(n.lower().endswith('vbaproject.bin') for n in names):
                raise Stop('modele_word_actif_refuse')
            for n in names:
                if WORD_PARTS.fullmatch(n):
                    z.read(n).decode('utf-8')
    except (zipfile.BadZipFile, UnicodeDecodeError, KeyError, ValueError):
        raise Stop('modele_word_docx_invalide') from None


def template_tags(raw):
    """Balises présentes dans un modèle Word (corps, en-têtes, pieds de page), même coupées par Word en plusieurs fragments."""
    import zipfile
    from html import unescape
    tags = set()
    with zipfile.ZipFile(io.BytesIO(raw)) as z:
        for n in z.namelist():
            if WORD_PARTS.fullmatch(n):
                xml = z.read(n).decode('utf-8', 'replace')
                for para in re.findall(r'<w:p[ >].*?</w:p>', xml, flags=re.S):
                    tags |= set(TAG.findall(unescape(''.join(re.findall(r'<w:t(?: [^>]*)?>([^<]*)</w:t>', para)))))
    return tags


def load_template(desk, source, dav=None):
    """Modèle importé depuis le poste (« local:… », gardé sur le serveur) ou fichier Nextcloud."""
    source = str(source or '')
    if source.startswith('local:'):
        name = source[len('local:'):]
        if not re.fullmatch(r'[0-9a-f]{32}\.docx', name) or not (_templates_dir(desk) / name).is_file():
            raise Stop('modele_word_absent')
        return (_templates_dir(desk) / name).read_bytes()
    from .dav import DAV
    try:
        client = dav or DAV(desk.c['nextcloud'])
    except (KeyError, TypeError, OSError):
        raise Stop('nextcloud_non_configure') from None
    return client.download(client.stat(clean_path(source)))


def import_template(desk, data, owner='cabinet'):
    """Modèle Word envoyé depuis le poste : contrôlé, gardé sur le serveur (jamais dans le dépôt ni publié), référencé « local:… »."""
    name = re.sub(r'[\x00-\x1f/\\]', '', str(data.get('filename') or ''))[:150]
    if not name.lower().endswith('.docx'):
        raise Stop('type_modele_word_invalide')
    payload = str(data.get('data') or '')
    if payload.startswith('data:'):
        payload = payload.split(',', 1)[-1]
    try:
        raw = base64.b64decode(payload, validate=True)
    except (ValueError, TypeError):
        raise Stop('modele_word_docx_invalide') from None
    check_docx(raw)
    ident = hashlib.sha256(raw).hexdigest()[:32]
    folder = _templates_dir(desk)
    folder.mkdir(parents=True, exist_ok=True)
    (folder / (ident + '.docx')).write_bytes(raw)
    tags = sorted(template_tags(raw))
    desk.audit('corporate5636_modele_importe', {'tags': len(tags), 'owner': owner})
    return {'source': 'local:%s.docx' % ident, 'name': name, 'tags': tags,
            'message': 'Modèle « %s » importé (%d balise(s)) : cliquez « Enregistrer » pour l’utiliser.' % (name, len(tags))}


def variables(desk, kind):
    """Variables utilisables dans un modèle pour cette opération, avec leur valeur sur un exemple fictif."""
    d = dict(SAMPLES.get(kind, {}))
    for key, label in custom_fields(desk, kind):
        d.setdefault(key, '[%s]' % label)
    c = dict(SAMPLE_COMPANY)
    try:
        ctx = build_context(desk, c, kind, d)
    except Stop:
        ctx = context(desk, c, kind, d)
    out = {}
    for k, v in ctx.items():
        if isinstance(v, str) and k not in ('organe', 'motif', 'champ') and not (k == 'nature' and kind == 'diligence'):   # codes internes
            out[k] = ' / '.join(x for x, _ in _fill([v], ctx))[:160]
    return dict(sorted(out.items()))


def _tags_report(desk, source, kinds):
    """Compte rendu joint à l'enregistrement d'un modèle : balises reconnues, inconnues, ou modèle non lu."""
    try:
        raw = load_template(desk, source)
        check_docx(raw)
        tags = template_tags(raw)
    except Stop as exc:
        return ' Attention : modèle non lu pour l’instant (%s) ; vérifiez le chemin.' % exc
    known = set()
    for k in kinds:
        known |= set(variables(desk, k))
    unknown = sorted(tags - known)
    if not tags:
        return ' Attention : aucune balise {{…}} dans ce modèle.'
    if unknown:
        return ' %d balise(s) reconnue(s) ; inconnue(s), laissée(s) « [À COMPLÉTER] » dans l’acte : %s.' % (
            len(tags) - len(unknown), ', '.join('{{%s}}' % x for x in unknown))
    return ' %d balise(s) reconnue(s).' % len(tags)


def _source(value):
    v = str(value or '').strip()
    if v.startswith('local:'):
        if not re.fullmatch(r'local:[0-9a-f]{32}\.docx', v):
            raise Stop('modele_word_absent')
        return v
    if not v:
        raise Stop('modele_acte_requis')
    v = clean_path(v)
    if not v.lower().endswith('.docx'):
        raise Stop('type_modele_word_invalide')
    return v


def own_deeds(desk, kind=None):
    rows = [r for r in (desk.settings(SETTINGS_OWN, []) or []) if isinstance(r, dict) and r.get('id')]
    return [r for r in rows if kind is None or kind in (r.get('ops') or [])]


def save_own_deed(desk, data, owner='cabinet'):
    """« Mes actes » : un acte du cabinet (nom, modèle Word, opérations où il est proposé)."""
    label = re.sub(r'\s+', ' ', str(data.get('label') or '')).strip()[:120]
    if not label:
        raise Stop('nom_acte_requis')
    source = _source(data.get('path'))
    ops = [k for k in OPERATIONS if data.get('op_' + k) is True or str(data.get('op_' + k)) in ('1', 'on', 'true')]
    if not ops:
        raise Stop('operation_acte_requise')
    rows = own_deeds(desk)
    ident = re.sub(r'[^0-9a-f]', '', str(data.get('id') or ''))[:12]
    if ident and not any(r['id'] == ident for r in rows):
        raise Stop('acte_absent')
    ident = ident or uuid.uuid4().hex[:12]
    report = _tags_report(desk, source, ops).strip()
    rows = [r for r in rows if r['id'] != ident] + [{'id': ident, 'label': label, 'source': source, 'ops': ops, 'report': report}]
    desk.setting(SETTINGS_OWN, rows)
    desk.audit('corporate5636_acte_cabinet', {'id': ident, 'ops': ops, 'owner': owner})
    return {'id': ident, 'message': 'Acte « %s » enregistré pour : %s. %s' % (label, ', '.join(OPERATIONS[k][0] for k in ops), report)}


def delete_own_deed(desk, data, owner='cabinet'):
    ident = str(data.get('id') or '')
    rows = own_deeds(desk)
    if not any(r['id'] == ident for r in rows):
        raise Stop('acte_absent')
    desk.setting(SETTINGS_OWN, [r for r in rows if r['id'] != ident])
    desk.audit('corporate5636_acte_cabinet_retire', {'id': ident, 'owner': owner})
    return {'message': 'Acte retiré de la liste (les actes déjà préparés restent dans les dossiers).'}


# ------------------------------------------------------------------------------------------------ opérations
def custom_fields(desk, kind):
    return [(f['key'], f['label']) for f in (desk.settings(SETTINGS_FIELDS, {}) or {}).get(kind, []) if f.get('key')]


def save_custom_fields(desk, data, owner='cabinet'):
    kind = str(data.get('kind') or '')
    if kind not in OPERATIONS:
        raise Stop('operation_inconnue')
    all_fields = dict(desk.settings(SETTINGS_FIELDS, {}) or {})
    taken = {k for k, *_ in FIELDS[kind]}
    rows = []
    for label in [x.strip()[:120] for x in str(data.get('labels') or '').splitlines() if x.strip()][:30]:
        key = 'perso_' + _slug(label)
        if key not in taken and key != 'perso_':
            taken.add(key)
            rows.append({'key': key, 'label': label})
    all_fields[kind] = rows
    desk.setting(SETTINGS_FIELDS, all_fields)
    desk.audit('corporate5636_champs', {'kind': kind, 'count': len(rows), 'owner': owner})
    return {'fields': rows, 'message': '%d champ(s) personnalisé(s) : balises %s.' % (len(rows), ', '.join('{{%s}}' % r['key'] for r in rows) or '—')}


def save_template(desk, data, owner='cabinet'):
    doc = str(data.get('doc') or '')
    if doc not in DOCS:
        raise Stop('operation_inconnue')
    path = _source(data.get('path')) if str(data.get('path') or '').strip() else ''
    mapping = dict(desk.settings(SETTINGS_TEMPLATES, {}) or {})
    if path:
        mapping[doc] = path
    else:
        mapping.pop(doc, None)
    desk.setting(SETTINGS_TEMPLATES, mapping)
    desk.audit('corporate5636_modele', {'doc': doc, 'cabinet': bool(path), 'owner': owner})
    reports = dict(desk.settings(SETTINGS_REPORTS, {}) or {})
    if not path:
        reports.pop(doc, None)
        desk.setting(SETTINGS_REPORTS, reports)
        return {'message': 'Modèle générique rétabli.'}
    reports[doc] = _tags_report(desk, path, [k for k, v in OPERATIONS.items() if doc in v[1]]).strip()
    desk.setting(SETTINGS_REPORTS, reports)
    return {'message': 'Modèle du cabinet enregistré. ' + reports[doc]}


def _clean_data(desk, kind, data):
    out = {}
    for key, _, typ, _ in FIELDS[kind]:
        v = data.get(key)
        out[key] = bool(v) if typ == 'check' else re.sub(r'[\x00-\x08\x0b-\x1f]', '', str(v or '')).strip()[:4000]
    for key, _ in custom_fields(desk, kind):
        out[key] = re.sub(r'[\x00-\x08\x0b-\x1f]', '', str(data.get(key) or '')).strip()[:2000]
    return out


def _apply_effects(desk, c, kind, d, op, decision_day=None):
    """Effets de la saisie unique : fiche, dirigeants, table de capitalisation, registres (une seule transaction).
    5.6.39 (audit 5.6.37, F04) : ``decision_day`` = date de la décision confirmée par l'avocat, portée au registre des décisions ; la date
    d'effet (mouvements, prise de fonctions, transfert) reste distincte."""
    cid = c['id']
    dec_day = decision_day
    day = d.get('date_effet') or d.get('date_cession') or d.get('date_ag') or date.today().isoformat()
    from . import crm5636
    if kind == 'agoa':
        _agoa_context(desk, c, d, {})   # 5.6.37 (audit F09) : affectation contrôlée avant toute inscription
        _decision(desk, cid, dec_day or d.get('date_ag') or day, 'associe_unique' if FORMS[c['forme']][3] else 'assemblee',
                  'Approbation des comptes de l’exercice clos le %s' % date_fr(d.get('exercice_clos')), 'Comptes approuvés ; affectation du résultat ; conventions ; pouvoirs.', op)
    elif kind == 'siege':
        if not d.get('nouveau_siege'):
            raise Stop('nouveau_siege_requis')
        d['ancien_siege'] = c['siege'] or '[À COMPLÉTER]'
        desk.db.execute('UPDATE companies5636 SET siege=?, updated=? WHERE id=?', (d['nouveau_siege'], _now(), cid))
        _decision(desk, cid, dec_day or day, d.get('organe') or 'assemblee', 'Transfert du siège social', 'Du %s au %s.' % (d['ancien_siege'], d['nouveau_siege']), op)
    elif kind == 'dirigeant':
        name = ' '.join(x for x in (d.get('entrant_prenom'), d.get('entrant_nom', '').upper()) if x).strip()
        if not name:
            raise Stop('dirigeant_requis')
        if d.get('sortant'):
            row = desk.db.execute('SELECT * FROM officers5636 WHERE id=? AND company=?', (d['sortant'], cid)).fetchone()
            if row:
                desk.db.execute('UPDATE officers5636 SET end=? WHERE id=?', (day, row['id']))
                d['sortant_nom'] = row['name']
        contact = ''
        try:
            contact = crm5636.find_or_create(desk, {'name': d.get('entrant_nom') or name, 'first_name': d.get('entrant_prenom', ''), 'kind': 'personne',
                                                    'email': d.get('entrant_email', ''), 'address': d.get('entrant_adresse', ''), 'category': 'autre',
                                                    **({'matter': c['matter'], 'role': 'dirigeant'} if c['matter'] else {})}, source='corporate', commit=False)['id']
        except Stop:
            pass
        desk.db.execute('INSERT INTO officers5636 VALUES(?,?,?,?,?,?,?,?)', (uuid.uuid4().hex, cid, contact, name, d.get('fonction') or FORMS[c['forme']][2], day, '', op))
        d['entrant'] = name
        _decision(desk, cid, dec_day or day, d.get('organe') or 'assemblee', 'Nomination : %s (%s)' % (name, d.get('fonction')),
                  ('Remplace %s.' % d['sortant_nom']) if d.get('sortant_nom') else '', op)
    elif kind == 'modification':
        if not d.get('nature'):
            raise Stop('nature_modification_requise')
        if d.get('champ') in ('denomination', 'objet', 'capital', 'duree') and d.get('nouvelle_redaction'):
            value = d['nouvelle_redaction'] if d['champ'] != 'capital' else str(_dec(d['nouvelle_redaction'], 'montant_invalide'))
            desk.db.execute('UPDATE companies5636 SET %s=?, updated=? WHERE id=?' % d['champ'], (value, _now(), cid))
        _decision(desk, cid, dec_day or day, d.get('organe') or 'assemblee', d['nature'], d.get('nouvelle_redaction', '')[:500], op)
    elif kind == 'diligence':
        label = dict(CHOICES['diligence']).get(d.get('nature'), 'Autre diligence')
        d['intitule'] = d.get('intitule') or label
        if d.get('nature') not in dict(CHOICES['diligence']):
            raise Stop('operation_inconnue')
        lines = []
        for field, kind_mv in (('emissions', 'emission'), ('annulations', 'annulation')):
            for line in [x for x in str(d.get(field) or '').splitlines() if x.strip()]:
                parts = [p.strip() for p in line.split(';')]
                qty = _dec(parts[1] if len(parts) > 1 else '', 'nombre_titres_invalide')
                if len(parts) < 2 or not parts[0] or qty is None or qty <= 0 or qty != qty.to_integral():
                    raise Stop('titres_format')
                lines.append((kind_mv, parts[0][:200], int(qty)))
        rows, _ = holdings(desk, cid)
        # 5.6.37 (audit F06) : annulations CUMULÉES par associé, comparées aux titres détenus ; une ligne ambiguë (deux associés du même
        # nom) est refusée ; l'opération entière est refusée avant toute écriture
        wanted = {}
        for kind_mv, name, qty in lines:
            if kind_mv == 'annulation':
                matches = [r for r in rows if (r['name'] == name or r['contact'] == name) and r['category'] == 'ordinaires']
                if len({(r['contact'] or r['name']) for r in matches}) > 1:
                    raise Stop('associe_ambigu')
                key = (matches[0]['contact'] or matches[0]['name']) if matches else name
                wanted[key] = wanted.get(key, 0) + qty
                held = sum(r['quantity'] for r in matches)
                if held < wanted[key]:
                    raise Stop('cession_titres_insuffisants')
        if d.get('nouveau_capital'):
            capital = _dec(d['nouveau_capital'], 'montant_invalide')
            if capital is None or capital <= 0:
                raise Stop('capital_requis')
            desk.db.execute('UPDATE companies5636 SET capital=?, updated=? WHERE id=?', (str(capital), _now(), cid))
        for kind_mv, name, qty in lines:
            holder = next((r for r in rows if r['name'] == name or r['contact'] == name), None)
            who = (holder['name'], holder['contact']) if holder else (name, '')
            if kind_mv == 'emission':
                _movement(desk, cid, day, 'émission', qty, 'ordinaires', op, to=who, price=str(c['nominal']))
            else:
                _movement(desk, cid, day, 'annulation', qty, 'ordinaires', op, from_=who)
        if d.get('champ') in ('denomination', 'objet', 'duree') and d.get('nouvelle_valeur'):
            desk.db.execute('UPDATE companies5636 SET %s=?, updated=? WHERE id=?' % d['champ'], (d['nouvelle_valeur'][:3000], _now(), cid))
        _decision(desk, cid, dec_day or day, d.get('organe') or 'assemblee', d['intitule'], (d.get('resolutions') or '')[:500], op)
    elif kind == 'cession':
        qty = _dec(d.get('nombre'), 'nombre_titres_invalide')
        if qty is None or qty <= 0 or qty != qty.to_integral():
            raise Stop('nombre_titres_invalide')
        rows, _ = holdings(desk, cid)
        seller = next((r for r in rows if (r['contact'] or r['name']) == d.get('cedant') and r['category'] == (d.get('categorie') or 'ordinaires')), None)
        if not seller or seller['quantity'] < int(qty):
            raise Stop('cession_titres_insuffisants')
        buyer = d.get('cessionnaire_nom', '').strip()
        if not buyer:
            raise Stop('cessionnaire_requis')
        contact = ''
        try:
            contact = crm5636.find_or_create(desk, {'name': buyer, 'email': d.get('cessionnaire_email', ''), 'category': 'autre',
                                                    **({'matter': c['matter'], 'role': 'associe'} if c['matter'] else {})}, source='corporate', commit=False)['id']
        except Stop:
            pass
        price = _dec(d.get('prix_unitaire'), 'prix_invalide') or Decimal(0)
        if price < 0:
            raise Stop('prix_invalide')
        n = _movement(desk, cid, d.get('date_cession') or day, 'cession', int(qty), d.get('categorie'), op, (seller['name'], seller['contact']),
                      (buyer, contact), str(price))
        d.update(cedant_nom=seller['name'], numero_mouvement=str(n))
        _decision(desk, cid, dec_day or d.get('date_cession') or day, 'assemblee' if d.get('agrement') else 'dirigeant',
                  'Cession de %d titres : %s → %s' % (int(qty), seller['name'], buyer), 'Mouvement n° %d.%s' % (n, ' Agrément du cessionnaire.' if d.get('agrement') else ''), op)
    return d


def _prepare_creation(desk, d, owner):
    """Création : la société, ses associés (souscriptions) et son premier dirigeant sont enregistrés en une fois."""
    if not d.get('matter'):
        raise Stop('societe_sans_dossier')
    capital = _dec(d.get('capital'), 'montant_invalide')
    nominal = _dec(d.get('nominal'), 'nominal_invalide')
    nominal = Decimal(1) if nominal is None else nominal
    if capital is None or capital <= 0:
        raise Stop('capital_requis')
    if nominal <= 0:
        raise Stop('nominal_invalide')
    associates = []
    for i, line in enumerate([x for x in str(d.get('associes') or '').splitlines() if x.strip()], 1):
        parts = [p.strip() for p in line.split(';')]
        # 5.6.37 (audit F07) : nom requis, apport > 0, nombre entier > 0, apport en numéraire = nombre × nominal (ligne par ligne)
        if len(parts) < 3 or len(parts[0]) < 2:
            raise Qualified('associes_format', precision='ligne %d : « nom ; apport ; nombre de titres »' % i)
        apport, nombre = _dec(parts[1], 'associes_format'), _dec(parts[2], 'associes_format')
        if apport is None or nombre is None or apport <= 0 or nombre <= 0 or nombre != nombre.to_integral() or nombre > 10 ** 12:
            raise Qualified('associes_format', precision='ligne %d : apport et nombre de titres positifs, nombre entier' % i)
        if apport != nombre * nominal:
            raise Qualified('associes_format', precision='ligne %d : l’apport en numéraire doit être égal au nombre de titres × nominal (%s)' % (i, money(nominal)))
        associates.append((parts[0][:200], apport, int(nombre)))
    if not associates:
        raise Stop('associes_format')
    if sum(a[1] for a in associates) != capital or sum(a[2] for a in associates) * nominal != capital:
        raise Stop('capital_incoherent')
    if FORMS.get(str(d.get('forme') or '').upper(), FORMS['SAS'])[3] and len(associates) != 1:
        raise Stop('associe_unique_requis')
    cid = company_save(desk, {'denomination': d.get('denomination'), 'forme': d.get('forme'), 'capital': str(capital), 'nominal': str(nominal),
                              'siege': d.get('siege'), 'objet': d.get('objet'), 'duree': d.get('duree') or '99', 'date_cloture': d.get('date_cloture') or '31/12',
                              'matter': d.get('matter')}, owner, commit=False)['id']
    desk.db.execute("UPDATE companies5636 SET data=json_set(data, '$.statut', 'en_formation') WHERE id=?", (cid,))
    return cid, associates


def _apply_creation(desk, c, d, op, decision_day=None):
    """Réalisation d'une constitution : souscriptions, premier dirigeant et décision inscrits (dans la transaction de l'appelant)."""
    from . import crm5636
    day = decision_day or d.get('date_effet') or date.today().isoformat()
    for name, apport, nombre in d.get('associes_lignes') or []:
        contact = ''
        try:
            contact = crm5636.find_or_create(desk, {'name': name, 'category': 'autre', 'matter': c['matter'], 'role': 'associe'}, source='corporate',
                                             commit=False)['id']
        except Stop:
            pass
        _movement(desk, c['id'], day, 'souscription', int(nombre), 'ordinaires', op, to=(name, contact), price=str(c['nominal']))
    head = ' '.join(x for x in (d.get('dirigeant_prenom'), d.get('dirigeant_nom', '').upper()) if x).strip()
    if head:
        desk.db.execute('INSERT INTO officers5636 VALUES(?,?,?,?,?,?,?,?)', (uuid.uuid4().hex, c['id'], '', head, FORMS[c['forme']][2], day, '', op))
    _decision(desk, c['id'], day, 'associes', 'Constitution de la société', 'Statuts signés ; premier dirigeant : %s.' % (head or '—'), op)
    desk.db.execute("UPDATE companies5636 SET data=json_set(data, '$.statut', 'constituee'), updated=? WHERE id=?", (_now(), c['id']))
    return d


def operation_submit(desk, data, owner='cabinet'):
    """Saisie unique : contrôle, effets (fiche, dirigeants, table de capitalisation, registres), puis génération des actes en tâche de fond."""
    schema(desk)
    kind = str(data.get('kind') or '')
    if kind not in OPERATIONS:
        raise Stop('operation_inconnue')
    d = _clean_data(desk, kind, data)
    op = uuid.uuid4().hex
    docs = [x for x in OPERATIONS[kind][1] if data.get('doc_' + x, x not in ('demission', 'agrement'))]
    if kind == 'dirigeant' and d.get('motif') == 'demission' and 'demission' not in docs and data.get('doc_demission') is None:
        docs.insert(1, 'demission')
    if kind == 'cession' and d.get('agrement') and 'agrement' not in docs and data.get('doc_agrement') is None:
        docs.insert(2, 'agrement')
    docs += ['perso_' + r['id'] for r in own_deeds(desk, kind) if data.get('doc_perso_' + r['id'], True) not in (False, '', '0', 'false')]
    if not docs:
        raise Stop('aucun_document_choisi')
    # 5.6.37 (audit F10) : clé d'idempotence du formulaire : même clé + même contenu = même opération ; autre contenu = refus
    idem = re.sub(r'[^A-Za-z0-9_-]', '', str(data.get('idem') or ''))[:64]
    content = hashlib.sha256(json.dumps({'kind': kind, 'company': str(data.get('company') or ''), 'data': d, 'docs': docs}, sort_keys=True,
                                        ensure_ascii=False).encode()).hexdigest()
    if idem:
        prior = desk.db.execute('SELECT id,company,job,data FROM operations5636 WHERE idem=?', (idem,)).fetchone()
        if prior:
            if json.loads(prior['data']).get('_contenu') != content:
                raise Stop('cle_idempotence_deja_utilisee')
            return {'id': prior['id'], 'company': prior['company'], 'job': prior['job'], 'goto': '?societe=%s&vue=actes' % prior['company'],
                    'message': 'Opération déjà enregistrée.'}
    from . import crm5636, metier500
    crm5636.schema(desk)   # 5.6.37 (F08) : schémas créés AVANT la transaction (executescript la validerait)
    metier500.ensure_schema(desk)   # 5.6.39 (audit 5.6.37, F02) : contrôle des conflits appelé par la mise en file
    if not desk.db.in_transaction:
        desk.db.execute('BEGIN IMMEDIATE')
    try:
        if kind == 'creation':
            cid, associates = _prepare_creation(desk, d, owner)
            c = company(desk, cid)
            d['associes_lignes'] = [[a[0], str(a[1]), a[2]] for a in associates]
            d.setdefault('date_effet', date.today().isoformat())
        else:
            c = company(desk, str(data.get('company') or ''))
            if not c['matter']:
                raise Stop('societe_sans_dossier')
            # 5.6.37 (audit F09) : tous les contrôles et les valeurs calculées (ancien siège, n° de mouvement prévu…) sur un essai à blanc
            # annulé : la saisie prépare un PROJET ; registres, capital et dirigeants ne changent qu'à « Enregistrer la réalisation »
            desk.db.execute('SAVEPOINT projet5637')
            try:
                d = _apply_effects(desk, c, kind, dict(d), op)
            finally:
                desk.db.execute('ROLLBACK TO SAVEPOINT projet5637')
                desk.db.execute('RELEASE SAVEPOINT projet5637')
        d['effets'] = 'proposes'
        d['_contenu'] = content
        desk.db.execute('INSERT INTO operations5636(id,company,kind,data,docs,state,result,job,owner,created,updated,idem) VALUES(?,?,?,?,?,?,?,?,?,?,?,?)',
                        (op, c['id'], kind, json.dumps(d, ensure_ascii=False), json.dumps(docs), 'en_file', '{}', None, owner, _now(), _now(), idem))
        # 5.6.37 (audit F10) / 5.6.39 (audit 5.6.37, F02) : projet, travail et rattachement dans UNE transaction dont operation_submit est
        # le seul propriétaire ; la mise en file ne valide rien ; la notification suit la validation
        job = desk.enqueue('corporate5636', {'id': op, 'matter': c['matter']}, priority=35, transactional=True)
        desk.db.execute('UPDATE operations5636 SET job=? WHERE id=?', (job, op))
        desk.db.commit()
    except Exception:
        if desk.db.in_transaction:
            desk.db.rollback()
        raise
    desk.notify_queued(job, {'id': op, 'matter': c['matter']})
    desk.audit('corporate5636_operation', {'id': op, 'kind': kind, 'company': c['id'], 'docs': len(docs), 'owner': owner})
    return {'id': op, 'company': c['id'], 'job': job, 'goto': '?societe=%s&vue=actes' % c['id'],
            'message': '%s : projet enregistré, %d acte(s) en préparation. Les registres seront mis à jour quand vous enregistrerez la réalisation.'
            % (OPERATIONS[kind][0], len(docs))}


# 5.6.39 (audit 5.6.37, F03) : contrat de réalisation unique pour l'interface et l'API.
#  - parcours courant : projet dont les actes sont prêts (actes_prets) et dont tous les actes requis ont été produits ;
#  - parcours « actes externes » : décision prise avec des actes établis hors d'AxiorHub, confirmation explicite obligatoire, possible
#    tant que le projet n'est ni réalisé ni abandonné (préparation en cours, en échec ou à relancer).
REALISABLE = ('actes_prets',)
REALISABLE_EXTERNE = ('actes_prets', 'en_file', 'echec', 'mise_en_file_a_reprendre')
# date « de décision » prévue dans le projet, par opération (l'approbation des comptes se décide à l'assemblée)
DATE_DECISION = {'agoa': 'date_ag'}


def _missing_deliverables(row):
    res = json.loads(row['result'] or '{}')
    templates = res.get('templates') or {}
    required = [d for d in json.loads(row['docs'] or '[]') if not d.startswith('perso_')]
    return [d for d in required if d not in templates or str(templates.get(d, '')).startswith('échec')] if res.get('files') else required


def realization_state(row):
    """(réalisable, réalisable avec des actes externes, motif) pour un projet — partagé par l'écran et le service."""
    d = json.loads(row['data'] or '{}')
    if d.get('effets') != 'proposes' or row['state'] in ('realisee', 'abandonnee'):
        return False, False, 'operation_deja_close'
    external = row['state'] in REALISABLE_EXTERNE
    if row['state'] not in REALISABLE:
        return False, external, {'en_file': 'actes_en_preparation', 'echec': 'preparation_en_echec',
                                 'mise_en_file_a_reprendre': 'preparation_a_relancer'}.get(row['state'], 'operation_non_realisable')
    if _missing_deliverables(row):
        return False, external, 'actes_requis_manquants'
    return True, external, ''


def realize(desk, data, owner='cabinet', externe=False):
    """5.6.37 (audit F09) : l'avocat constate que la décision a été prise (AG tenue, acte signé) : effets inscrits d'un bloc, avec la date
    de la décision et le justificatif ; contrôles refaits sur l'état actuel des registres.
    5.6.39 (audit 5.6.37) : F03 — le parcours courant exige des actes prêts ; le parcours « actes externes » est distinct et exige une
    confirmation ; état et version relus dans la transaction, transition conditionnelle. F04 — la date confirmée est la date de la décision
    inscrite au registre ; si elle diffère de la date prévue dans les actes, l'avocat doit le confirmer explicitement."""
    schema(desk)
    from . import crm5636
    crm5636.schema(desk)
    op = str(data.get('id') or '')
    day = str(data.get('date') or '')[:10]
    try:
        date.fromisoformat(day)
    except ValueError:
        raise Stop('date_invalide') from None
    proof = re.sub(r'[\x00-\x08\x0b-\x1f]', '', str(data.get('justificatif') or '')).strip()[:500]
    if len(proof) < 3:
        raise Stop('justificatif_requis')
    if externe and str(data.get('actes_externes') or '') not in ('1', 'true', 'on', 'True'):
        raise Stop('confirmation_actes_externes_requise')
    if not desk.db.in_transaction:
        desk.db.execute('BEGIN IMMEDIATE')
    try:
        row = desk.db.execute('SELECT * FROM operations5636 WHERE id=?', (op,)).fetchone()   # relu DANS la transaction
        if not row:
            raise Stop('operation_inconnue')
        ok, ok_external, reason = realization_state(row)
        if not (ok_external if externe else ok):
            raise Stop(reason or 'operation_non_realisable')
        d = json.loads(row['data'])
        planned_key = DATE_DECISION.get(row['kind'])
        planned = str(d.get(planned_key) or '')[:10] if planned_key else ''
        if planned and planned != day and str(data.get('confirmer_date') or '') not in ('1', 'true', 'on', 'True'):
            raise Qualified('date_decision_differente', precision='Date prévue dans les actes : %s ; date saisie : %s. Cochez « La décision a été '
                                                                 'prise à cette date » pour confirmer, ou corrigez la date.' % (planned, day))
        if planned and planned != day:
            d[planned_key + '_prevue'] = planned
            d[planned_key] = day
        c = company(desk, row['company'])
        for key in ('date_effet', 'date_cession'):
            if key in d and not d.get(key):
                d[key] = day
        d = (_apply_creation(desk, c, d, op, decision_day=day) if row['kind'] == 'creation'
             else _apply_effects(desk, c, row['kind'], d, op, decision_day=day))
        d.update(effets='realises', date_decision=day, realise_le=date.today().isoformat(), realise_par=owner, justificatif=proof,
                 mode_realisation='actes_externes' if externe else 'actes_prepares')
        cur = desk.db.execute('UPDATE operations5636 SET state=?, data=?, updated=? WHERE id=? AND state=? AND updated=?',
                              ('realisee', json.dumps(d, ensure_ascii=False), _now(), op, row['state'], row['updated']))
        if not cur.rowcount:
            raise Stop('operation_deja_close')
        desk.db.commit()
    except Exception:
        if desk.db.in_transaction:
            desk.db.rollback()
        raise
    desk.audit('corporate5636_realisation', {'id': op, 'kind': row['kind'], 'company': row['company'], 'owner': owner,
                                            'mode': 'actes_externes' if externe else 'actes_prepares'})
    return {'id': op, 'message': 'Réalisation enregistrée : fiche, registres et table de capitalisation mis à jour.'}


def abandon(desk, data, owner='cabinet'):
    """Projet abandonné : aucun effet ; une constitution abandonnée laisse la société « en formation » archivée."""
    schema(desk)
    op = str(data.get('id') or '')
    row = desk.db.execute('SELECT * FROM operations5636 WHERE id=?', (op,)).fetchone()
    if not row:
        raise Stop('operation_inconnue')
    if json.loads(row['data']).get('effets') != 'proposes' or row['state'] in ('realisee', 'abandonnee'):
        raise Stop('operation_deja_close')
    cur = desk.db.execute("UPDATE operations5636 SET state='abandonnee', updated=? WHERE id=? AND state NOT IN ('realisee','abandonnee')", (_now(), op))
    if not cur.rowcount:
        desk.db.rollback()
        raise Stop('operation_deja_close')
    if row['kind'] == 'creation':
        desk.db.execute("UPDATE companies5636 SET archived=1, updated=? WHERE id=? AND json_extract(data, '$.statut')='en_formation'", (_now(), row['company']))
    if row['job']:
        try:
            desk.cancel_job(row['job'])
        except Stop:
            pass
    desk.db.commit()
    desk.audit('corporate5636_abandon', {'id': op, 'owner': owner})
    return {'id': op, 'message': 'Projet abandonné : aucun registre modifié.'}


def repair(desk):
    """5.6.37 (audit F10) : opération sans travail (interruption entre les deux écritures des versions antérieures) : rattachée au
    travail existant, sinon signalée « mise en file à reprendre » (bouton Relancer) ; jamais laissée « en préparation »."""
    schema(desk)
    for row in desk.db.execute("SELECT id FROM operations5636 WHERE job IS NULL AND state='en_file'").fetchall():
        found = desk.db.execute("SELECT id FROM jobs WHERE kind='corporate5636' AND json_extract(args, '$.id')=? ORDER BY id DESC", (row['id'],)).fetchone()
        if found:
            desk.db.execute('UPDATE operations5636 SET job=? WHERE id=?', (found['id'], row['id']))
        else:
            desk.db.execute("UPDATE operations5636 SET state='mise_en_file_a_reprendre', updated=? WHERE id=?", (_now(), row['id']))
    desk.db.commit()


def relaunch(desk, data, owner='cabinet'):
    """Nouvelle mise en file d'une opération dont la préparation n'a pas pu être lancée ou a échoué (un seul travail)."""
    schema(desk)
    op = str(data.get('id') or '')
    row = desk.db.execute('SELECT * FROM operations5636 WHERE id=?', (op,)).fetchone()
    recheck = (row is not None and row['kind'] == 'agoa' and row['state'] == 'actes_prets'
               and json.loads(row['result'] or '{}').get('methode_controle') != METHODE_COMPTES)   # 5.6.37 (F05) : rapport à recontrôler
    if not row or (row['state'] not in ('mise_en_file_a_reprendre', 'echec') and not recheck):
        raise Stop('operation_deja_close')
    c = company(desk, row['company'])
    from . import metier500
    metier500.ensure_schema(desk)
    if not desk.db.in_transaction:
        desk.db.execute('BEGIN IMMEDIATE')
    try:
        desk.db.execute("UPDATE operations5636 SET state='en_file', updated=? WHERE id=?", (_now(), op))
        args = {'id': op, 'matter': c['matter'], 'reprise': _now()}
        job = desk.enqueue('corporate5636', args, priority=35, transactional=True)
        desk.db.execute('UPDATE operations5636 SET job=? WHERE id=?', (job, op))
        desk.db.commit()
    except Exception:
        if desk.db.in_transaction:
            desk.db.rollback()
        raise
    desk.notify_queued(job, args)
    desk.audit('corporate5636_relance', {'id': op, 'owner': owner})
    return {'id': op, 'job': job, 'message': 'Préparation relancée.'}


def build_context(desk, c, kind, d, docs=(), client=None, model=None):
    """Valeurs des balises d'un acte (société, opération, textes calculés). Sans ``client``, les comptes PDF ne sont pas lus."""
    ctx = context(desk, c, kind, d)
    if kind == 'agoa':
        ctx = _agoa_context(desk, c, d, ctx)
        ctx['objet_formalite'] = 'l’approbation des comptes de l’exercice clos le %s (dépôt des comptes annuels)' % ctx.get('exercice_clos_fr')
        if 'rapport_gestion' in docs and d.get('comptes_pdf') and client is not None:
            try:
                raw = client.download(client.stat(clean_path(d['comptes_pdf'])))
                ctx.update(accounts_analysis(desk, c, raw, d['comptes_pdf'].rsplit('/', 1)[-1], model, exercice=str(d.get('exercice_clos') or '')[:4]))
            except Stop as exc:
                ctx['avertissement_ia'] = 'Comptes non analysés (%s) : rapport à compléter.' % exc
    elif kind == 'siege':
        ctx['objet_formalite'] = 'le transfert du siège social'
        ctx['mention_ressort'] = 'La société sera immatriculée au RCS de [À COMPLÉTER].' if d.get('ressort_change') else ''
    elif kind == 'dirigeant':
        ctx['objet_formalite'] = 'la nomination de %s en qualité de %s' % (d.get('entrant'), d.get('fonction', '').lower())
        ctx['fonction'] = d.get('fonction', '').lower()
        ctx['sortant'] = d.get('sortant_nom') or '[À COMPLÉTER : dirigeant sortant]'
        ctx['entrant_identite'] = ', '.join(x for x in (d.get('entrant'), ('né(e) le ' + d['entrant_naissance']) if d.get('entrant_naissance') else '',
                                                         ('de nationalité ' + d['entrant_nationalite']) if d.get('entrant_nationalite') else '',
                                                         ('demeurant ' + d['entrant_adresse']) if d.get('entrant_adresse') else '') if x)
        motif = {'demission': 'prend acte de la démission de %s de ses fonctions de %s, à effet du %s',
                 'revocation': 'décide de révoquer %s de ses fonctions de %s, à effet du %s',
                 'fin_mandat': 'constate l’arrivée du terme du mandat de %s en qualité de %s, le %s', 'deces': 'prend acte du décès de %s, %s, survenu le %s'}
        ctx['texte_sortant'] = ('%s %s.' % (ctx['decideur_modif'], motif[d['motif']] % (ctx['sortant'], ctx['fonction'], ctx.get('date_effet_fr', '')))
                                if d.get('motif') in motif and d.get('sortant_nom') else '')
        ctx['mention_remplacement'] = (', en remplacement de %s' % d['sortant_nom']) if d.get('sortant_nom') else ''
    elif kind == 'modification':
        ctx['objet_formalite'] = 'la modification suivante : %s' % d.get('nature')
    elif kind == 'cession':
        qty = int(_dec(d.get('nombre')) or 0)
        price = _dec(d.get('prix_unitaire')) or Decimal(0)
        ctx.update(cedant=d.get('cedant_nom', ''), cessionnaire=d.get('cessionnaire_nom', ''), nombre=str(qty), prix_unitaire_fr=money(price),
                   prix_total_fr=money(price * qty), mention_agrement='Le Cessionnaire a été agréé par décision du %s.' % ctx.get('date_acte_fr')
                   if d.get('agrement') else '', numero_mouvement=d.get('numero_mouvement', ''))
    elif kind == 'diligence':
        ctx['objet_formalite'] = d.get('intitule') or 'la décision du %s' % ctx.get('date_effet_fr', '')
        ctx['nouveau_capital_fr'] = money(d['nouveau_capital']) if d.get('nouveau_capital') else '[À COMPLÉTER]'
        ctx['nouvelle_valeur'] = d.get('nouvelle_valeur') or '[À COMPLÉTER]'
        texts = [x.strip() for x in str(d.get('resolutions') or '').splitlines() if x.strip()] or RESOLUTIONS.get(d.get('nature'), RESOLUTIONS['autre'])
        decideur = ctx['decideur_modif']
        ctx['texte_resolutions'] = '\n'.join('%s résolution — %s %s.%s' % (ORDINALS[i] if i < len(ORDINALS) else '%de' % (i + 1), decideur,
                                                                          _fill([x.rstrip('.')], ctx)[0][0], (' ' + ctx['vote']) if ctx['vote'] else '')
                                            for i, x in enumerate(texts[:20]))
        ctx['preambule_diligence'] = ('Le {{date_effet_fr}}, %s a pris les décisions suivantes, après avoir pris connaissance des documents utiles.' %
                                      decideur.lower() if not decideur.startswith('Le ') else 'Le {{date_effet_fr}}, le %s a pris les décisions suivantes.'
                                      % ctx['fonction_dirigeant'])
        mentions = []
        if d.get('nouveau_capital'):
            mentions.append('Le capital social est désormais fixé à %s.' % money(d['nouveau_capital']))
        if d.get('champ') and d.get('nouvelle_valeur'):
            mentions.append('Nouvelle mention : %s.' % d['nouvelle_valeur'])
        ctx['mention_annonce'] = ' '.join(mentions) or '[À COMPLÉTER : mentions modifiées]'
    elif kind == 'creation':
        forme = FORMS[c['forme']]
        ctx['objet_formalite'] = 'l’immatriculation de la société'
        lines = d.get('associes_lignes') or []
        ctx['total_titres'] = str(sum(int(q) for _, _, q in lines)) if lines else '[À COMPLÉTER]'
        ctx['tableau_associes'] = '\n'.join('%s — apport : %s — %s %s' % (n, money(a), q, forme[1]) for n, a, q in lines)
        ctx['texte_apports'] = 'Les associés ont fait apport en numéraire des sommes suivantes : ' + ' ; '.join('%s : %s' % (n, money(a)) for n, a, _ in lines) + \
            '. Ces sommes ont été déposées sur un compte ouvert au nom de la société en formation [À COMPLÉTER : banque], ainsi qu’il résulte du certificat du dépositaire.'
        ctx['liberation'] = d.get('liberation') or 'intégralement libérées'
        ctx['debut_exercice'] = '[À COMPLÉTER : premier jour de l’exercice]'
        ctx['preambule_statuts'] = ('La soussignée / Le soussigné : %s' if forme[3] else 'Les soussignés : %s') % '; '.join(n for n, _, _ in lines)
        head = ' '.join(x for x in (d.get('dirigeant_prenom'), d.get('dirigeant_nom', '').upper()) if x).strip() or '[À COMPLÉTER]'
        ctx['dirigeant'] = head
        ctx['fonction_dirigeant'] = forme[2].lower()
        ctx['entrant'] = head
        ctx['entrant_identite'] = ', '.join(x for x in (head, ('né(e) le ' + d['dirigeant_naissance']) if d.get('dirigeant_naissance') else '',
                                                         ('demeurant ' + d['dirigeant_adresse']) if d.get('dirigeant_adresse') else '') if x)
        ctx['texte_direction'] = ('La société est dirigée par un %s, personne physique ou morale, désigné par décision collective des associés, qui la '
                                  'représente à l’égard des tiers et dispose des pouvoirs les plus étendus dans la limite de l’objet social.') % forme[2].lower()
        ctx['texte_decisions'] = ('L’associé unique exerce les pouvoirs dévolus par la loi à la collectivité des associés.' if forme[3] else
                                  'Les décisions collectives sont prises en assemblée ou par consultation écrite, à la majorité prévue par la loi et les présents statuts '
                                  '[À COMPLÉTER : règles de majorité].')
        ctx['texte_transmission'] = ('Les %s se transmettent par virement de compte à compte sur production d’un ordre de mouvement. [À COMPLÉTER : agrément, préemption]'
                                     % forme[1]) if forme[1] == 'actions' else ('Les parts sociales ne peuvent être cédées à des tiers qu’avec le consentement des '
                                                                                'associés dans les conditions prévues par la loi. [À COMPLÉTER]')
        ctx['organe_affectation'] = 'de l’associé unique' if forme[3] else 'collective des associés'
        ctx['texte_premier_dirigeant'] = 'Le premier %s de la société, nommé pour une durée indéterminée, est : %s.' % (forme[2].lower(), ctx['entrant_identite'])
        ctx['mention_direction_annonce'] = '%s : %s.' % (forme[2], ctx['entrant_identite'])
        ctx['signatures_associes'] = '\n'.join(n for n, _, _ in lines)
        ctx['date_cloture_fr'] = d.get('date_cloture') or '31/12'
    return ctx


PREPARABLE = ('en_file', 'mise_en_file_a_reprendre')


def generate(desk, op_id, dav=None, model=None):
    """Travail de fond : actes de l'opération rangés dans « JURIDIQUE/AAAA-MM-JJ - opération » du dossier de la société."""
    from .dav import DAV
    schema(desk)
    row = desk.db.execute('SELECT * FROM operations5636 WHERE id=?', (op_id,)).fetchone()
    if not row:
        raise Stop('operation_inconnue')
    if row['state'] not in PREPARABLE:   # 5.6.39 (audit 5.6.37, F07) : projet abandonné, réalisé ou déjà prêt : rien n'est préparé
        return {**json.loads(row['result'] or '{}'), 'interrompu': row['state']}
    c = company(desk, row['company'])
    d, docs = json.loads(row['data']), json.loads(row['docs'])
    matter = next((m for m in load_matters(desk.c) if m['id'] == c['matter']), None)
    if not matter:
        raise Stop('societe_sans_dossier')
    client = dav or DAV(desk.c['nextcloud'])
    ctx = build_context(desk, c, row['kind'], d, docs, client, model)
    folder = '%s/JURIDIQUE/%s - %s' % (matter['path'].rstrip('/'), date.today().isoformat(), OPERATIONS[row['kind']][0])
    client.ensure_folder(folder, matter['path'])
    files, sources = [], {}
    own = {'perso_' + r['id']: r for r in own_deeds(desk)}
    stopped = ''
    for doc in docs:
        state = desk.db.execute('SELECT state FROM operations5636 WHERE id=?', (op_id,)).fetchone()['state']
        if state not in PREPARABLE:   # abandon pendant la préparation : les actes suivants ne sont pas déposés
            stopped = state
            break
        try:
            raw, source = render(desk, doc, ctx, client)
        except Stop as exc:
            if doc not in own and doc in DOCS:
                raise
            sources[doc] = 'échec : %s' % exc   # un acte du cabinet illisible n'empêche pas les autres
            continue
        name = '%s - %s.docx' % (own[doc]['label'] if doc in own else DOCS[doc][0], c['denomination'])
        name = re.sub(r'[\\/:*?"<>|]', '_', name)[:150]
        stem, n = name[:-5], 1
        while True:
            target = folder + '/' + (name if n == 1 else '%s (%d).docx' % (stem, n))
            try:
                client.put_file(target, raw, 'application/vnd.openxmlformats-officedocument.wordprocessingml.document')
                files.append(target)
                # dépôt tracé aussitôt (un abandon ou un arrêt ultérieur n'efface jamais la trace d'un fichier réellement créé)
                desk.db.execute("UPDATE operations5636 SET result=json_set(result, '$.files', json(?)) WHERE id=?",
                                (json.dumps(files, ensure_ascii=False), op_id))
                desk.db.commit()
                break
            except Stop as exc:
                if str(exc) != 'http_412' or n > 20:
                    raise
                n += 1
        sources[doc] = source
    result = {'folder': folder, 'files': files, 'templates': sources}
    if stopped:
        result['arret'] = 'abandon_pendant_la_preparation'
    # transition conditionnelle : un abandon concurrent n'est jamais remplacé par « actes prêts »
    cur = desk.db.execute("UPDATE operations5636 SET state='actes_prets', result=?, updated=? WHERE id=? AND state IN ('en_file','mise_en_file_a_reprendre')",
                          (json.dumps(result, ensure_ascii=False), _now(), op_id))
    if not cur.rowcount:
        desk.db.execute('UPDATE operations5636 SET result=? WHERE id=?', (json.dumps(result, ensure_ascii=False), op_id))
    desk.db.commit()
    desk.audit('corporate5636_actes', {'id': op_id, 'files': len(files)})
    return result


def job(desk, args):
    try:
        return generate(desk, str(args.get('id') or ''))
    except Stop as exc:
        # 5.6.39 (audit 5.6.37, F07) : un état clos (abandonné, réalisé) n'est jamais remplacé par « échec » ; fichiers déjà déposés conservés
        desk.db.execute("UPDATE operations5636 SET state='echec', result=json_set(result, '$.erreur', ?), updated=? "
                        "WHERE id=? AND state IN ('en_file','mise_en_file_a_reprendre')", (str(exc), _now(), str(args.get('id') or '')))
        desk.db.commit()
        raise


# ------------------------------------------------------------------------------------------------ registres
def registers(desk, cid):
    schema(desk)
    moves = [dict(r) for r in desk.db.execute('SELECT * FROM movements5636 WHERE company=? ORDER BY numero', (cid,))]
    decisions = [dict(r) for r in desk.db.execute('SELECT * FROM decisions5636 WHERE company=? ORDER BY day, created', (cid,))]
    return moves, decisions


def registers_csv(desk, cid, which):
    c = company(desk, cid)
    moves, decisions = registers(desk, cid)
    buf = io.StringIO()
    w = csv.writer(buf, delimiter=';')
    if which == 'mouvements':
        w.writerow(['N°', 'Date', 'Nature', 'De', 'À', 'Catégorie', 'Nombre', 'Prix unitaire'])
        for m in moves:
            w.writerow([m['numero'], m['day'], m['kind'], m['from_name'], m['to_name'], m['category'], m['quantity'], m['unit_price']])
    else:
        w.writerow(['Date', 'Organe', 'Décision', 'Détail'])
        for x in decisions:
            w.writerow([x['day'], x['organe'], x['title'], x['summary']])
    return {'download': {'filename': 'registre-%s-%s.csv' % (which, _slug(c['denomination'])), 'mime': 'text/csv', 'text': '﻿' + buf.getvalue()}}


# ------------------------------------------------------------------------------------------------ pages
ORGANES = {'assemblee': 'Associés', 'associe_unique': 'Associé unique', 'dirigeant': 'Dirigeant', 'associes': 'Associés fondateurs'}


def _field(desk, c, kind, key, label, typ, default, labels):
    if default.startswith('{') and default.endswith('}'):
        default = {'siege': (c or {}).get('siege', ''), 'cabinet': _cabinet(desk), 'ville': _city(c or {}),
                   'fonction': FORMS.get((c or {}).get('forme', 'SAS'), FORMS['SAS'])[2]}.get(default[1:-1], '')
    if typ == 'check':
        return '<label class="e5636-switch"><input type="checkbox" name="%s"> %s</label>' % (key, e(label))
    if typ == 'textarea':
        return '<label>%s<textarea name="%s" rows="3" data-no-dictation>%s</textarea></label>' % (e(label), key, e(default))
    if typ.startswith('select:'):
        src = typ[7:]
        if src == 'officer':
            opts = [('', 'Aucun')] + [(o['id'], '%s (%s)' % (o['name'], o['fonction'])) for o in officers(desk, c['id'])]
        elif src == 'holder':
            opts = [(r['contact'] or r['name'], '%s — %d titres (%s)' % (r['name'], r['quantity'], r['category'])) for r in holdings(desk, c['id'])[0]]
        elif src == 'matter':
            opts = [('', 'Choisir…')] + sorted(labels.items(), key=lambda kv: kv[1].casefold())
        else:
            opts = CHOICES[src]
            if src == 'organe' and not default:
                default = 'associe_unique' if FORMS.get(c['forme'], FORMS['SAS'])[3] else 'assemblee'
        return '<label>%s<select name="%s">%s</select></label>' % (e(label), key, ''.join(
            '<option value="%s"%s>%s</option>' % (e(v, quote=True), ' selected' if v == default else '', e(t)) for v, t in opts))
    kinds = {'date': 'date', 'time': 'time', 'number': 'text" inputmode="decimal'}
    hint = ' placeholder="/Clients/…/Comptes 2025.pdf"' if typ == 'path' else ''
    return '<label>%s<input type="%s" name="%s" value="%s"%s></label>' % (e(label), kinds.get(typ, 'text'), key, e(default, quote=True), hint)


STATES = {'en_file': 'actes en préparation', 'actes_prets': 'projets prêts — réalisation à enregistrer', 'echec': 'échec de la préparation',
          'mise_en_file_a_reprendre': 'préparation à relancer', 'realisee': 'réalisée', 'abandonnee': 'abandonnée'}


def operations_html(desk, prefix, cid):
    """5.6.37 (audit F09, F10) : chaque opération est un PROJET tant que l'avocat n'a pas enregistré sa réalisation (date, justificatif) ;
    seuls les effets réalisés modifient fiche, registres et table de capitalisation."""
    ops = desk.db.execute('SELECT * FROM operations5636 WHERE company=? ORDER BY created DESC LIMIT 30', (cid,)).fetchall()
    items = []
    for o in ops:
        d, res = json.loads(o['data'] or '{}'), json.loads(o['result'] or '{}')
        files = [f for f in res.get('files', []) if f]
        body = '<strong>%s</strong> — %s · <span class="c5637-state">%s</span>' % (e(OPERATIONS[o['kind']][0]), e(o['created'][:10]), e(STATES.get(o['state'], o['state'])))
        if files:
            body += '<br><small>%s</small><details><summary>%d fichier(s)</summary><ul>%s</ul></details>' % (
                e(res.get('folder', '')), len(files), ''.join('<li>%s</li>' % e(f.rsplit('/', 1)[-1]) for f in files))
        if o['state'] == 'echec':
            body += ' — ' + e(res.get('erreur', ''))
        if res.get('controle_chiffres'):   # 5.6.37 (audit F05) : chiffres du rapport de gestion et leur justification
            body += '<details><summary>Contrôle des chiffres (%d à vérifier)</summary><ul>%s</ul></details>' % (
                sum(1 for x in res['controle_chiffres'] if x.get('statut') != 'justifie'), ''.join(
                    '<li>%s : %s %s — %s%s%s</li>' % (e(x.get('libelle', '')), e(x.get('montant', '')), e(x.get('devise', '')),
                                                    'justifié' if x.get('statut') == 'justifie' else 'à vérifier (%s)' % e(x.get('cause', '').replace('_', ' ')),
                                                    (' · page %s' % e(str(x['page']))) if x.get('page') else '',
                                                    ('<br><small>« %s »</small>' % e(x['extrait'])) if x.get('extrait') else '')
                    for x in res['controle_chiffres']))
        recheck = (o['kind'] == 'agoa' and o['state'] == 'actes_prets' and 'rapport_gestion' in json.loads(o['docs'] or '[]')
                   and res.get('methode_controle') != METHODE_COMPTES and bool(d.get('comptes_pdf')))
        if recheck:
            body += ('<br><small>Rapport de gestion établi avec l’ancien contrôle des chiffres : à recontrôler.</small>'
                     '<form class="m5-form m5-inline" data-api="m5636/corporate/relancer" data-reload="1"><input type="hidden" name="id" value="%s">'
                     '<button class="ax-btn ghost" type="submit">Relancer la préparation</button></form>') % e(o['id'], quote=True)
        if res.get('arret'):
            body += '<br><small>Abandonné pendant la préparation : %d fichier(s) déjà déposé(s), conservé(s) ; les suivants n’ont pas été créés.</small>' % len(files)
        if d.get('effets') == 'realises':
            body += '<br><small>%s</small>' % e(_dates_line(d))
        elif d.get('effets') == 'appliques_5636':
            body += '<br><small>Registres mis à jour dès la saisie (version 5.6.36)%s.</small>' % (
                ' : opération en échec, vérifiez la fiche et les registres' if o['state'] == 'echec' else '')
        if d.get('effets') == 'proposes' and o['state'] not in ('realisee', 'abandonnee'):
            body += _realize_forms(o, d)
        if o['state'] in ('mise_en_file_a_reprendre', 'echec'):
            body += ('<form class="m5-form m5-inline" data-api="m5636/corporate/relancer" data-reload="1"><input type="hidden" name="id" value="%s">'
                     '<button class="ax-btn ghost" type="submit">Relancer la préparation</button></form>') % e(o['id'], quote=True)
        items.append('<li>%s</li>' % body)
    return ('<section class="ax-card"><h3>Actes préparés</h3><p class="ax-muted">Une opération reste un projet tant que vous n’avez pas enregistré sa '
            'réalisation : la fiche, les registres et la table de capitalisation ne changent qu’à ce moment.</p><ul class="o5636-list">%s</ul></section>') % (
        ''.join(items) or '<li class="ax-muted">Aucun acte.</li>')


BLOCKED = {'actes_en_preparation': 'Les actes sont en préparation : la réalisation pourra être enregistrée quand ils seront prêts.',
           'preparation_en_echec': 'La préparation des actes a échoué : relancez-la avant d’enregistrer la réalisation.',
           'preparation_a_relancer': 'La préparation n’a pas pu être lancée : relancez-la avant d’enregistrer la réalisation.',
           'actes_requis_manquants': 'Un acte requis n’a pas été produit : relancez la préparation avant d’enregistrer la réalisation.'}


def _dates_line(d):
    parts = []
    for key, label in (('date_ag_prevue', 'date prévue'), ('date_decision', 'décision du'), ('date_effet', 'effet au'), ('date_cession', 'cession au'),
                       ('realise_le', 'enregistrée le')):
        if d.get(key):
            parts.append('%s %s' % (label, d[key]))
    return 'Réalisation : %s — %s%s' % (', '.join(parts), d.get('justificatif', ''),
                                         ' (actes établis hors d’AxiorHub)' if d.get('mode_realisation') == 'actes_externes' else '')


def _realize_forms(o, d):
    """5.6.39 (audit 5.6.37, F03, F04) : formulaire de réalisation présenté selon le même contrat que le service ; date de décision
    préremplie (date d'assemblée prévue pour une approbation des comptes) ; parcours « actes externes » distinct."""
    ok, ok_external, reason = realization_state(o)
    planned = str(d.get(DATE_DECISION.get(o['kind'], ''), '') or '')[:10]
    ident = e(o['id'], quote=True)
    fields = ('<input type="hidden" name="id" value="%s"><label>Date de la décision<input type="date" name="date" value="%s" required></label>'
              '<label>Justificatif<input name="justificatif" required placeholder="PV signé, acte signé, décision de l’associé unique…"></label>%s') % (
        ident, e(planned, quote=True), ('<label class="e5636-switch"><input type="checkbox" name="confirmer_date" value="1"> La décision a été prise à '
                                        'cette date (différente de la date prévue, %s)</label>' % e(planned)) if planned else '')
    html = ''
    if ok:
        html += ('<form class="m5-form m5-inline c5637-realise" data-api="m5636/corporate/realiser" data-reload="1">%s'
                 '<button class="ax-btn" type="submit">Enregistrer la réalisation</button></form>') % fields
    else:
        html += '<p class="ax-muted c5639-blocage">%s</p>' % e(BLOCKED.get(reason, 'Réalisation impossible dans cet état.'))
    if ok_external:
        html += ('<details class="c5639-externe"><summary>Décision déjà prise avec des actes établis hors d’AxiorHub ?</summary>'
                 '<form class="m5-form m5-inline" data-api="m5636/corporate/realiser-externe" data-reload="1">%s<label class="e5636-switch"><input type="checkbox" '
                 'name="actes_externes" value="1" required> Je confirme que la décision a été prise et signée avec d’autres actes que ceux préparés ici</label>'
                 '<button class="ax-btn ghost" type="submit">Enregistrer la décision (actes externes)</button></form></details>') % fields
    html += ('<form class="m5-form m5-inline" data-api="m5636/corporate/abandonner" data-reload="1" data-confirm="Abandonner ce projet ? Aucun registre ne sera modifié.">'
             '<input type="hidden" name="id" value="%s"><button class="ax-btn ghost" type="submit">Abandonner le projet</button></form>') % ident
    return html


def operation_form(desk, prefix, c, kind, labels):
    title, docs = OPERATIONS[kind]
    fields = ''.join(_field(desk, c, kind, k, lab, typ, default, labels) for k, lab, typ, default in FIELDS[kind])
    fields += ''.join(_field(desk, c, kind, k, lab, 'text', '', labels) for k, lab in custom_fields(desk, kind))
    mapping = desk.settings(SETTINGS_TEMPLATES, {}) or {}
    checks = ''.join('<label class="e5636-switch"><input type="checkbox" name="doc_%s"%s> %s <small>(%s)</small></label>' % (
        d, '' if d in ('demission', 'agrement') else ' checked', e(DOCS[d][0]), 'modèle du cabinet' if mapping.get(d) else 'modèle générique') for d in docs)
    checks += ''.join('<label class="e5636-switch"><input type="checkbox" name="doc_perso_%s" checked> %s <small>(mes actes)</small></label>' % (
        e(r['id'], quote=True), e(r['label'])) for r in own_deeds(desk, kind))
    checks += variables_html(desk, kind, prefix, (c or {}).get('id', ''))
    return ('<section class="ax-card c5636-op"><h2>%s</h2><form class="m5-form" data-api="m5636/corporate/operation" data-reload="1">'
            '<input type="hidden" name="kind" value="%s"><input type="hidden" name="company" value="%s"><input type="hidden" name="idem" value="%s">'
            '<div class="c5636-fields">%s</div>'
            '<fieldset><legend>Actes à préparer</legend>%s</fieldset><div class="ax-actions"><button class="ax-btn" type="submit">Enregistrer et préparer les actes</button>'
            '</div><p class="ax-muted">Une seule saisie : les actes (projets Word) sont préparés dans le dossier de la société. La fiche, les registres et la '
            'table de capitalisation sont mis à jour quand vous enregistrez la réalisation (onglet « Actes préparés »). Rien n’est signé ni déposé.</p></form></section>') % (
        e(title), kind, e((c or {}).get('id', ''), quote=True), uuid.uuid4().hex, fields, checks)


def _company_form(c, labels):
    c = c or {}
    forms = ''.join('<option value="%s"%s>%s</option>' % (k, ' selected' if k == c.get('forme', 'SAS') else '', e(k + ' — ' + v[0])) for k, v in FORMS.items())
    mats = '<option value="">Choisir…</option>' + ''.join('<option value="%s"%s>%s</option>' % (e(k, quote=True), ' selected' if k == c.get('matter') else '', e(v))
                                                          for k, v in sorted(labels.items(), key=lambda kv: kv[1].casefold()))
    return ('<form class="m5-form c5636-company" data-api="m5636/corporate/societe" data-reload="1"><input type="hidden" name="id" value="%s">'
            '<label>Dénomination<input name="denomination" required value="%s"></label><label>Forme<select name="forme">%s</select></label>'
            '<label>Capital (€)<input name="capital" inputmode="decimal" value="%s"></label><label>Valeur nominale (€)<input name="nominal" inputmode="decimal" value="%s"></label>'
            '<label>Siège social<input name="siege" value="%s"></label><label>Greffe (RCS de…)<input name="rcs" value="%s"></label>'
            '<label>SIREN<input name="siren" inputmode="numeric" value="%s"></label><label>Clôture (JJ/MM)<input name="date_cloture" value="%s"></label>'
            '<label>Objet social<textarea name="objet" rows="2" data-no-dictation>%s</textarea></label><label>Dossier du cabinet (actes rangés dans « JURIDIQUE »)<select name="matter">%s</select></label>'
            '<button class="ax-btn" type="submit">Enregistrer la société</button></form>') % (
        e(c.get('id', ''), quote=True), e(c.get('denomination', ''), quote=True), forms, e(c.get('capital', ''), quote=True), e(c.get('nominal', '1'), quote=True),
        e(c.get('siege', ''), quote=True), e(c.get('rcs', ''), quote=True), e(c.get('siren', ''), quote=True), e(c.get('date_cloture', '31/12'), quote=True),
        e(c.get('objet', '')), mats)


def _template_input(value, placeholder):
    return ('<span class="c5636-tplpick"><input name="path" value="%s" placeholder="%s" data-no-dictation>'
            '<button type="button" class="ax-btn ghost" data-c5636-pick>📁 Choisir dans Nextcloud</button>'
            '<label class="ax-btn ghost c5636-upload">⬆ Importer depuis le poste<input type="file" accept=".docx" data-c5636-upload hidden></label>'
            '<small class="c5636-tplmsg" role="status"></small></span>') % (e(value, quote=True), e(placeholder, quote=True))


def _report_html(report):
    if not report:
        return ''
    warn = 'inconnue' in report or 'Attention' in report
    return '<br><small class="%s">%s</small>' % ('c5636-warn' if warn else 'ax-muted', e(report))


def _shown_path(source):
    return source   # « local:… » reste lisible : le champ garde la référence exacte du modèle importé


def _shown_source(source):
    return 'modèle importé depuis le poste' if str(source).startswith('local:') else source


def variables_html(desk, kind, prefix='', cid=''):
    rows = variables(desk, kind)
    return ('<details class="c5636-vars"><summary>Variables utilisables dans vos modèles Word (%d)</summary><p class="ax-muted">Écrivez-les entre doubles '
            'accolades, avec ou sans espaces : <code>{{denomination}}</code> ou <code>{{ denomination }}</code>. Exemples tirés d’une société fictive.</p>'
            '<div class="o5636-scroll"><table class="o5636-grid"><thead><tr><th>Variable</th><th>Exemple</th></tr></thead><tbody>%s</tbody></table></div></details>') % (
        len(rows), ''.join('<tr><td><code>{{%s}}</code></td><td>%s</td></tr>' % (e(k), e(v) or '—') for k, v in rows.items()))


def models_html(desk, prefix, cid):
    """Onglet « Modèles et formulaires » : mes actes, modèles du cabinet par acte, champs personnalisés, variables par opération."""
    mapping = desk.settings(SETTINGS_TEMPLATES, {}) or {}
    reports = desk.settings(SETTINGS_REPORTS, {}) or {}
    ops_checks = ''.join('<label class="e5636-switch"><input type="checkbox" name="op_%s"> %s</label>' % (k, e(v[0])) for k, v in OPERATIONS.items())
    deeds = own_deeds(desk)
    html = ('<section class="ax-card"><h3>Mes actes</h3><p class="ax-muted">Ajoutez vos propres actes : un nom, votre modèle Word et les opérations où il '
            'doit être proposé. Il apparaît alors dans « Actes à préparer » de ces opérations.</p><ul class="o5636-list">%s</ul>'
            '<details%s><summary>Ajouter un acte</summary><form class="m5-form" data-api="m5636/corporate/acte" data-reload="1">'
            '<label>Nom de l’acte<input name="label" required maxlength="120" placeholder="Ex. : Lettre de mission corporate"></label>'
            '<label>Modèle Word%s</label><fieldset><legend>Proposé pour</legend>%s</fieldset>'
            '<button class="ax-btn" type="submit">Enregistrer l’acte</button></form></details></section>') % (
        ''.join('<li><strong>%s</strong> — %s <small>(%s)</small>%s <form class="m5-form m5-inline m5-mini" data-api="m5636/corporate/acte/retirer" '
                'data-reload="1" data-confirm="Retirer cet acte de la liste ?"><input type="hidden" name="id" value="%s"><button class="ax-btn ghost" '
                'type="submit">Retirer</button></form></li>' % (e(r['label']), e(', '.join(OPERATIONS[k][0] for k in r.get('ops', []) if k in OPERATIONS)),
                                                               e(_shown_source(r['source'])), _report_html(r.get('report')), e(r['id'], quote=True)) for r in deeds)
        or '<li class="ax-muted">Aucun acte du cabinet pour l’instant.</li>', '' if deeds else ' open', _template_input('', '/Modèles/Mon acte.docx'), ops_checks)
    html += ('<section class="ax-card"><h3>Modèles du cabinet</h3><p class="ax-muted">Pour chaque acte proposé, choisissez votre modèle Word : les '
             'variables (liste ci-dessous) et celles de vos champs personnalisés y sont remplacées. Vide : modèle générique.</p>%s</section>') % ''.join(
        '<form class="m5-form c5636-tpl" data-api="m5636/corporate/modele" data-reload="1"><input type="hidden" name="doc" value="%s"><label>%s%s</label>%s'
        '<button class="ax-btn ghost" type="submit">Enregistrer</button></form>' % (
            d, e(DOCS[d][0]), _template_input(_shown_path(mapping.get(d, '')), 'modèle générique'),
            _report_html(reports.get(d)) if mapping.get(d) else '') for d in DOCS)
    html += '<section class="ax-card"><h3>Variables par opération</h3>%s</section>' % ''.join(
        '<h4>%s</h4>%s' % (e(v[0]), variables_html(desk, k, prefix, cid)) for k, v in OPERATIONS.items())
    return html


def page_html(desk, prefix, args):
    schema(desk)
    try:
        repair(desk)   # 5.6.37 (audit F10)
    except Exception:   # noqa: BLE001 — l'affichage ne dépend pas de la réparation
        pass
    labels = {}
    try:
        labels = {m['id']: matter_display(m) for m in load_matters(desk.c)}
    except Stop:
        pass
    cid, view, op_kind = str(args.get('societe') or ''), str(args.get('vue') or 'fiche'), str(args.get('operation') or '')
    parcours = str(args.get('parcours') or '')
    rows = companies(desk)
    q = ('&societe=' + cid) if cid else ''
    actions = ('<nav class="c5636-actions" aria-label="Secrétariat juridique"><a class="c5636-big%s" href="%s"><strong>📋 Approbation des comptes</strong>'
               '<small>Effectuer le secrétariat juridique annuel d’une société : convocation, rapport de gestion, PV, extrait, dépôt.</small></a>'
               '<a class="c5636-big%s" href="%s"><strong>⚖️ Autre diligence en droit des sociétés</strong><small>Siège, dirigeants, cession de titres, '
               'capital, dissolution, transformation, objet, dénomination…</small></a>'
               '<a class="c5636-big%s" href="%s"><strong>📚 Bibliothèque de modèles</strong><small>Statuts, pactes, LOI, protocoles, cessions et '
               'contrats (droit français, allemand, espagnol, anglais), personnalisables.</small></a></nav>') % (
        ' on' if parcours == 'agoa' or op_kind == 'agoa' else '', e(prefix + '/corporate?parcours=agoa' + q, quote=True),
        ' on' if parcours == 'diligences' or op_kind in ('siege', 'dirigeant', 'modification', 'cession', 'diligence') else '',
        e(prefix + '/corporate?parcours=diligences' + q, quote=True),
        ' on' if view == 'bibliotheque' else '', e(prefix + '/corporate?vue=bibliotheque' + q, quote=True))
    side = '<nav class="c5636-side" aria-label="Sociétés"><a class="ax-btn" href="%s">＋ Créer une société</a><a class="ax-btn ghost" href="%s">Ajouter une société existante</a><ul>%s</ul></nav>' % (
        e(prefix + '/corporate?operation=creation', quote=True), e(prefix + '/corporate?vue=nouvelle', quote=True),
        ''.join('<li><a%s href="%s">%s <small>%s</small></a></li>' % (' aria-current="page"' if r['id'] == cid else '', e(prefix + '/corporate?societe=' + r['id'], quote=True),
                                                                   e(r['denomination']), e(r['forme'])) for r in rows) or '<li class="ax-muted">Aucune société.</li>')
    intro = ('<h1>Corporate</h1><p class="ax-muted">Secrétariat juridique : une saisie par opération, les actes préparés (modèles du cabinet ou génériques), '
             'la table de capitalisation et les registres tenus à jour.</p>')
    if parcours in ('agoa', 'diligences') and not cid:
        # étape 1 du parcours : choisir la société (ou en ajouter / créer une)
        target = 'vue=operations&operation=agoa' if parcours == 'agoa' else 'parcours=diligences'
        main = ('<section class="ax-card"><h2>%s</h2><p>Choisissez la société concernée :</p><ul class="c5636-pick">%s</ul><p class="ax-muted">'
                'Société absente : <a href="%s">ajoutez-la</a>%s.</p></section>') % (
            'Approbation des comptes' if parcours == 'agoa' else 'Autre diligence en droit des sociétés',
            ''.join('<li><a class="ax-btn ghost" href="%s">%s <small>%s</small></a></li>' % (
                e(prefix + '/corporate?societe=%s&%s' % (r['id'], target), quote=True), e(r['denomination']), e(r['forme'])) for r in rows)
            or '<li class="ax-muted">Aucune société enregistrée.</li>', e(prefix + '/corporate?vue=nouvelle', quote=True),
            '' if parcours == 'agoa' else (' ou <a href="%s">créez une société</a>' % e(prefix + '/corporate?operation=creation', quote=True)))
        return intro + actions + '<div class="c5636-layout">%s<div class="c5636-main">%s</div></div>' % (side, main)
    if parcours == 'diligences' and cid and not op_kind:
        view = 'operations'
    if view == 'bibliotheque' and not cid:   # 5.6.39 : bibliothèque de modèles, sans société choisie
        from . import bibliotheque5639
        return intro + actions + '<div class="c5636-layout">%s<div class="c5636-main">%s</div></div>' % (
            side, bibliotheque5639.page_html(desk, prefix, args))
    if op_kind == 'creation':
        main = operation_form(desk, prefix, {'id': '', 'forme': 'SAS', 'siege': ''}, 'creation', labels)
    elif view == 'nouvelle' or not cid:
        main = '<section class="ax-card"><h2>Ajouter une société existante</h2>%s</section>' % _company_form(None, labels) if view == 'nouvelle' else (
            '<section class="ax-card"><h2>Bienvenue</h2><p>Choisissez une société à gauche, ajoutez une société existante, ou créez-en une : les statuts et '
            'les pièces d’immatriculation sont préparés à partir d’une seule saisie.</p></section>')
    else:
        c = company(desk, cid)
        tabs = (('fiche', 'Fiche'), ('operations', 'Opérations'), ('capital', 'Table de capitalisation'), ('registres', 'Registres'), ('actes', 'Actes préparés'),
                ('modeles', 'Modèles et formulaires'), ('bibliotheque', 'Bibliothèque de modèles'))
        main = '<header class="c5636-head"><h2>%s <small>%s</small></h2><nav class="tabs">%s</nav></header>' % (
            e(c['denomination']) + (' (en formation)' if c['data'].get('statut') == 'en_formation' else ''), e(FORMS.get(c['forme'], ('',))[0]),
            ''.join('<a class="%s" href="%s">%s</a>' % (
                'active' if k == view else '', e(prefix + '/corporate?societe=%s&vue=%s' % (cid, k), quote=True), e(v)) for k, v in tabs))
        if view == 'operations' or op_kind:
            if op_kind in OPERATIONS and op_kind != 'creation':
                main += operation_form(desk, prefix, c, op_kind, labels)
            else:
                main += '<div class="c5636-ops">%s</div>' % ''.join('<a class="ax-card c5636-opbtn" href="%s"><strong>%s</strong><small>%s</small></a>' % (
                    e(prefix + '/corporate?societe=%s&vue=operations&operation=%s' % (cid, k), quote=True), e(v[0]), e(', '.join(DOCS[d][0] for d in v[1][:4]) + '…'))
                    for k, v in OPERATIONS.items() if k != 'creation' and not (parcours == 'diligences' and k == 'agoa'))
        elif view == 'capital':
            rows_h, total = holdings(desk, cid)
            main += ('<section class="ax-card"><h3>Table de capitalisation</h3><div class="o5636-scroll"><table class="o5636-grid"><thead><tr><th>Associé</th><th>Catégorie</th><th>Titres</th>'
                     '<th>%% du capital</th></tr></thead><tbody>%s</tbody><tfoot><tr><th>Total</th><td></td><td>%d</td><td>100 %%</td></tr></tfoot></table></div>'
                     '<details><summary>Inscrire une souscription ou une situation de départ</summary><form class="m5-form" data-api="m5636/corporate/souscription" data-reload="1">'
                     '<input type="hidden" name="company" value="%s"><label>Associé<input name="name" required></label><label>Nombre de titres<input name="quantity" inputmode="numeric" required></label>'
                     '<label>Catégorie<input name="category" value="ordinaires"></label><label>Date<input type="date" name="day"></label>'
                     '<button class="ax-btn ghost" type="submit">Inscrire</button></form></details></section>') % (
                ''.join('<tr><td>%s</td><td>%s</td><td>%d</td><td>%s %%</td></tr>' % (e(r['name']), e(r['category']), r['quantity'], r['pct']) for r in rows_h)
                or '<tr><td colspan="4" class="ax-muted">Aucun titre inscrit.</td></tr>', total, e(cid, quote=True))
        elif view == 'registres':
            moves, decisions = registers(desk, cid)
            wrong = anomalies(desk, cid)
            if wrong:   # 5.6.37 (audit F06) : jamais masquées ; à rapprocher par une écriture correctrice validée par l'avocat
                main += ('<section class="ax-card c5637-alert" role="alert"><h3>Anomalie du registre</h3><p>Position négative : %s. Une opération antérieure a '
                         'annulé ou cédé plus de titres que l’associé n’en détenait. Vérifiez le registre et inscrivez une écriture correctrice ; l’historique '
                         'est conservé.</p></section>') % e(' ; '.join('%s (%s) : %d' % (w['name'], w['category'], w['quantity']) for w in wrong))
            main += ('<section class="ax-card"><h3>Registre des mouvements de titres</h3><button type="button" class="ax-btn ghost m5-export" data-api="m5636/corporate/registre?societe=%s&type=mouvements">'
                     'Exporter</button><div class="o5636-scroll"><table class="o5636-grid"><thead><tr><th>N°</th><th>Date</th><th>Nature</th><th>De</th><th>À</th><th>Catégorie</th><th>Nombre</th></tr></thead>'
                     '<tbody>%s</tbody></table></div></section>') % (e(cid, quote=True), ''.join('<tr><td>%d</td><td>%s</td><td>%s</td><td>%s</td><td>%s</td><td>%s</td><td>%d</td></tr>' % (
                         m['numero'], e(m['day']), e(m['kind']), e(m['from_name'] or '—'), e(m['to_name'] or '—'), e(m['category']), m['quantity']) for m in moves)
                         or '<tr><td colspan="7" class="ax-muted">Registre vide.</td></tr>')
            main += ('<section class="ax-card"><h3>Registre des décisions</h3><button type="button" class="ax-btn ghost m5-export" data-api="m5636/corporate/registre?societe=%s&type=decisions">'
                     'Exporter</button><div class="o5636-scroll"><table class="o5636-grid"><thead><tr><th>Date</th><th>Organe</th><th>Décision</th><th>Détail</th></tr></thead><tbody>%s</tbody></table></div></section>') % (
                e(cid, quote=True), ''.join('<tr><td>%s</td><td>%s</td><td>%s</td><td>%s</td></tr>' % (e(x['day']), e(ORGANES.get(x['organe'], x['organe'])), e(x['title']), e(x['summary']))
                                            for x in decisions) or '<tr><td colspan="4" class="ax-muted">Registre vide.</td></tr>')
        elif view == 'actes':
            main += operations_html(desk, prefix, cid)
        elif view == 'bibliotheque':   # 5.6.39 : variables pré-remplies depuis cette société
            from . import bibliotheque5639
            main += bibliotheque5639.page_html(desk, prefix, args, cid)
        elif view == 'modeles':
            main += models_html(desk, prefix, cid)
            main += '<section class="ax-card"><h3>Formulaires : champs personnalisés</h3>%s</section>' % ''.join(
                '<form class="m5-form" data-api="m5636/corporate/champs" data-reload="1"><input type="hidden" name="kind" value="%s"><label>%s — un libellé par ligne'
                '<textarea name="labels" rows="2" data-no-dictation>%s</textarea></label><small>%s</small><button class="ax-btn ghost" type="submit">Enregistrer</button></form>' % (
                    k, e(v[0]), e('\n'.join(lab for _, lab in custom_fields(desk, k))), e(' '.join('{{%s}}' % key for key, _ in custom_fields(desk, k))))
                for k, v in OPERATIONS.items())
        else:
            heads = officers(desk, cid)
            main += '<section class="ax-card">%s<h3>Dirigeants en fonction</h3><ul>%s</ul></section>' % (_company_form(c, labels), ''.join(
                '<li>%s — %s <small>depuis le %s</small></li>' % (e(o['name']), e(o['fonction']), e(o['start'] or '—')) for o in heads)
                or '<li class="ax-muted">Aucun dirigeant inscrit : utilisez « Opérations › Changement de dirigeant ».</li>')
    return intro + actions + '<div class="c5636-layout">%s<div class="c5636-main">%s</div></div>' % (side, main)


def subscription(desk, data, owner='cabinet'):
    """Situation de départ d'une société existante : titres déjà détenus inscrits au registre (souscription ou reprise d'historique)."""
    c = company(desk, str(data.get('company') or ''))
    qty = _dec(data.get('quantity'), 'nombre_titres_invalide')
    name = re.sub(r'\s+', ' ', str(data.get('name') or '')).strip()[:200]
    if not name or qty is None or qty <= 0 or qty != qty.to_integral():
        raise Stop('nombre_titres_invalide')
    n = _movement(desk, c['id'], str(data.get('day') or date.today().isoformat())[:10], 'souscription', int(qty), str(data.get('category') or 'ordinaires')[:60],
                  '', to=(name, ''))
    desk.db.commit()
    desk.audit('corporate5636_souscription', {'company': c['id'], 'owner': owner})
    return {'numero': n, 'message': 'Inscription n° %d portée au registre des mouvements.' % n}


def api_post(desk, route, data, owner):
    if route == 'corporate/societe':
        return company_save(desk, data, owner)
    if route == 'corporate/operation':
        return operation_submit(desk, data, owner)
    if route == 'corporate/realiser':   # 5.6.37 (audit F09) : réservé à l'avocat et à l'administrateur
        return realize(desk, data, owner)
    if route == 'corporate/realiser-externe':   # 5.6.39 (audit 5.6.37, F03) : décision prise avec des actes établis hors d'AxiorHub
        return realize(desk, data, owner, externe=True)
    if route == 'corporate/abandonner':
        return abandon(desk, data, owner)
    if route == 'corporate/relancer':
        return relaunch(desk, data, owner)
    if route == 'corporate/souscription':
        return subscription(desk, data, owner)
    if route == 'corporate/champs':
        return save_custom_fields(desk, data, owner)
    if route == 'corporate/modele':
        return save_template(desk, data, owner)
    if route == 'corporate/import':
        return import_template(desk, data, owner)
    if route == 'corporate/acte':
        return save_own_deed(desk, data, owner)
    if route == 'corporate/acte/retirer':
        return delete_own_deed(desk, data, owner)
    return None


def api_get(desk, route, args):
    if route == 'corporate/registre':
        return registers_csv(desk, str(args.get('societe') or ''), 'mouvements' if args.get('type') == 'mouvements' else 'decisions')
    if route == 'corporate/societes':
        return {'societes': [{k: r[k] for k in ('id', 'denomination', 'forme')} for r in companies(desk)]}
    if route == 'corporate/variables':
        kind = str(args.get('operation') or '')
        if kind not in OPERATIONS:
            raise Stop('operation_inconnue')
        return {'variables': variables(desk, kind)}
    if route == 'corporate/capital':
        rows, total = holdings(desk, str(args.get('societe') or ''))
        return {'total': total, 'rows': [{**r, 'pct': str(r['pct'])} for r in rows]}
    return None
