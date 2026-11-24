"""5.6.109 (lot G4, G10) : offre (lettre de mission ou devis) et son PDF joints au brouillon, depuis une instruction unique.

Recette du cabinet : « Réponds au courriel de Christophe Martin du 22 novembre 2026. Prépare une lettre de mission pour l'analyse de ses
conditions générales de vente au forfait de 800 euros HT et joins-la en PDF au brouillon. » Le service enchaîne, sans second registre :

1. **Instruction** (``interpreter``) : nom et jour sont des CRITÈRES de recherche, jamais une identité ; le montant est celui de l'instruction
   (centimes, HT exigé : un montant TTC ou ambigu est une question) ou, à défaut, l'engagement du dossier lu par ``catalogue56109.tarif_applicable``
   (convention signée, devis accepté, montant retenu) ; sinon « montant à préciser ». **Jamais un montant inventé** : le texte de la réponse
   est contrôlé par ``catalogue56109.montants_hors_tarif`` contre les seuls montants du document calculé.
2. **Message exact** dans la messagerie autorisée (``mailbox.Mailbox``) : un seul message de cet expéditeur ce jour-là (fuseau du cabinet),
   sinon une question précise (aucun, plusieurs) ; boîte, UIDVALIDITY, UID et Message-ID conservés.
3. **Client** résolu par le registre (fiche client de facturation, parties du dossier, correspondants confirmés) : l'adresse de l'expéditeur
   doit être celle du client d'UN dossier ; sinon une question. Aucun SIREN, mandat ou coordonnée deviné.
4. **Document** : ``facturation5653.prepare`` (ligne de forfait explicite, clé de requête dérivée de la commande) puis ``update`` des seuls
   textes de prix et de périmètre CALCULÉS par le serveur ; modèle du cabinet versionné (identifiant et version du modèle conservés).
5. **PDF** : ``facturation5653.preview_file`` (PDF interne). Sans convertisseur il rend un Word : **jamais un .docx annoncé comme PDF** —
   type, en-tête, fin de fichier, ouverture par le lecteur PDF et concordance (prix HT et périmètre) sont vérifiés ; sinon « PDF
   indisponible », rien n'est joint ni déposé. ``export_draft`` (publication soumise à approbation) n'est PAS utilisé pour cette préparation.
6. **Registre des livrables** : le PDF est une version du document canonique de ``livrables5689`` (même identité que la publication future
   du registre de facturation : service ``facturation5653``, même objet).
7. **Brouillon** dans le fil (``mailbox.make_draft``), dépôt par ``append_draft`` puis relecture ``verify_draft`` et comparaison des octets de
   la pièce jointe. Commande durable (``offres56109``) : même clé et même contenu → même reçu ; une réponse perdue → « à rapprocher » et
   recherche de la même clé avant toute nouvelle création (**jamais de doublon**) ; **aucun envoi** (aucun SMTP ici).

Droits relus à chaque étape (droit « facturation », dossier visible), version de la commande contrôlée (409), ``pause5629.checkpoint`` avant
chaque effet ; aucune IA n'est appelée : la fonction reste utilisable IA en pause. Routes : ``m56109/parcours/offre`` (``parcours56109.api``).
"""
from datetime import date, datetime, timedelta, timezone
import hashlib
import io
import json
import re
import secrets
import unicodedata

from .common import Stop

SCHEMA = '''CREATE TABLE IF NOT EXISTS offres56109(id TEXT PRIMARY KEY, acteur TEXT NOT NULL, cle TEXT NOT NULL, empreinte TEXT NOT NULL,
  etat TEXT NOT NULL, version INTEGER NOT NULL DEFAULT 1, instruction_json TEXT NOT NULL DEFAULT '{}', source_json TEXT NOT NULL DEFAULT '{}',
  dossier TEXT NOT NULL DEFAULT '', type_doc TEXT NOT NULL DEFAULT '', montant_ht_cents INTEGER, origine_montant TEXT NOT NULL DEFAULT '',
  document_id TEXT NOT NULL DEFAULT '', document_version INTEGER NOT NULL DEFAULT 0, modele_id TEXT NOT NULL DEFAULT '',
  modele_version TEXT NOT NULL DEFAULT '', pdf_nom TEXT NOT NULL DEFAULT '', pdf_sha256 TEXT NOT NULL DEFAULT '', pdf_taille INTEGER NOT NULL DEFAULT 0,
  livrable_item TEXT NOT NULL DEFAULT '', brouillon_mid TEXT NOT NULL DEFAULT '', recu_json TEXT NOT NULL DEFAULT '{}',
  questions_json TEXT NOT NULL DEFAULT '[]', erreur TEXT NOT NULL DEFAULT '', cree_le TEXT NOT NULL, maj_le TEXT NOT NULL, UNIQUE(acteur, cle));
CREATE INDEX IF NOT EXISTS offres56109_dossier ON offres56109(dossier, maj_le);'''
ETATS = {'question': 'Une précision est nécessaire', 'document_prepare': 'Document préparé', 'pdf_pret': 'PDF interne vérifié',
         'pdf_indisponible': 'PDF indisponible : rien n’est joint', 'depot_en_cours': 'Dépôt du brouillon en cours',
         'a_rapprocher': 'À rapprocher : résultat du dépôt incertain', 'depose': 'Brouillon déposé et vérifié (rien n’est envoyé)'}
QUESTIONS = {'message_introuvable': 'Aucun courriel de cet expéditeur ce jour-là dans la messagerie autorisée : précisez le message.',
             'plusieurs_messages': 'Plusieurs courriels correspondent : choisissez le message exact.',
             'client_a_rattacher': 'L’adresse de l’expéditeur n’est celle du client d’aucun dossier : rattachez d’abord le client.',
             'plusieurs_dossiers': 'Cette adresse est celle du client de plusieurs dossiers : précisez le dossier.',
             'montant_a_preciser': 'Aucun montant dans l’instruction ni engagement du dossier : indiquez le montant HT.',
             'montant_ambigu': 'Plusieurs montants dans l’instruction : précisez le forfait HT.',
             'montant_ht_ou_ttc': 'Précisez si le montant est hors taxes (HT).',
             'expediteur_a_preciser': 'Précisez le nom de l’expéditeur du courriel.',
             'jour_a_preciser': 'Précisez la date du courriel.'}
MOIS = {'janvier': 1, 'fevrier': 2, 'mars': 3, 'avril': 4, 'mai': 5, 'juin': 6, 'juillet': 7, 'aout': 8, 'septembre': 9, 'octobre': 10,
        'novembre': 11, 'decembre': 12}
PREFERENCE = 'offre56109:type_par_defaut'      # préférence du cabinet quand l'instruction laisse « devis ou lettre de mission » ouvert
MISSION = 'Analyse des conditions générales de vente'
PERIMETRE_INCLUS = 'Analyse des conditions générales de vente communiquées par le client.'
PERIMETRE_EXCLU = ('Rédaction complète ou refonte des conditions générales, négociation avec des tiers, contentieux et toute mission '
                   'non mentionnée ci-dessus.')
BOITE = None          # essais : fabrique de la fausse messagerie (cfg -> boîte) ; production : ``mailbox.Mailbox``
MAX_PDF = 20 * 1024 * 1024


def _maintenant():
    return datetime.now(timezone.utc).isoformat(timespec='seconds')


def _sha(*parts):
    return hashlib.sha256('|'.join(str(p) for p in parts).encode('utf-8')).hexdigest()


def plier(texte):
    t = unicodedata.normalize('NFKD', str(texte or ''))
    return re.sub(r'\s+', ' ', ''.join(c for c in t if not unicodedata.combining(c))).casefold().strip()


def ensure_schema(desk):
    desk.db.executescript(SCHEMA)
    desk.db.commit()


def _zone(desk):
    from zoneinfo import ZoneInfo
    try:
        return ZoneInfo((desk.c.get('calendar') or {}).get('timezone') or 'Europe/Paris')
    except Exception:   # noqa: BLE001 — fuseau illisible : Paris, fuseau du cabinet par défaut
        return ZoneInfo('Europe/Paris')


def jour_cabinet(desk):
    return datetime.now(_zone(desk)).date()


# ------------------------------------------------------------------------------------------------ 1. instruction
def interpreter(texte, aujourd_hui):
    """Instruction en clair → critères. Déterministe ; aucune donnée n'est complétée : ce qui manque est rendu ``None``.

    ``{'nom', 'jour', 'montant_ht_cents', 'type', 'pdf', 'prestation', 'ambiguites'}`` ; ``ambiguites`` porte les codes de question."""
    brut = str(texte or '')[:4000]
    p = plier(brut)
    out = {'nom': None, 'jour': None, 'montant_ht_cents': None, 'type': None, 'pdf': 'pdf' in p, 'prestation': None, 'ambiguites': []}
    m = re.search(r"(?:courriel|mail|message|e-mail)\s+(?:de|d['’])\s*((?:[A-ZÉÈÀÂÎÔÛÇ][\w'’-]+)(?:\s+[A-ZÉÈÀÂÎÔÛÇ][\w'’-]+){0,3})", brut)
    if not m:
        m = re.match(r"\s*((?:[A-ZÉÈÀÂÎÔÛÇ][\w'’-]+)(?:\s+[A-ZÉÈÀÂÎÔÛÇ][\w'’-]+){1,3})\s*,", brut)
    if m:
        out['nom'] = re.sub(r'\s+', ' ', m.group(1)).strip()
    d = re.search(r'\b(\d{1,2})(?:er)?\s+(' + '|'.join(MOIS) + r')\b(?:\s+(\d{4}))?', p)
    if d:
        try:
            an = int(d.group(3)) if d.group(3) else aujourd_hui.year
            jour = date(an, MOIS[d.group(2)], int(d.group(1)))
            if not d.group(3) and jour > aujourd_hui:
                jour = date(an - 1, jour.month, jour.day)
            out['jour'] = jour.isoformat()
        except ValueError:
            out['ambiguites'].append('jour_a_preciser')
    from .catalogue56109 import _MONTANT
    montants = []
    for mm in _MONTANT.finditer(brut):
        valeur = int(re.sub(r'\D', '', mm.group(1))) * 100 + int((mm.group(2) or '0').ljust(2, '0'))
        suite = plier(brut[mm.end():mm.end() + 8])
        montants.append((valeur, 'ht' if re.match(r'\s*h\.?\s*t\b', suite) else 'ttc' if re.match(r'\s*ttc\b', suite) else ''))
    distincts = sorted({v for v, _ in montants})
    if len(distincts) > 1:
        out['ambiguites'].append('montant_ambigu')
    elif distincts:
        if all(q == 'ht' for v, q in montants):
            out['montant_ht_cents'] = distincts[0]
        else:
            out['ambiguites'].append('montant_ht_ou_ttc')
    if 'lettre de mission' in p:
        out['type'] = 'lettre_mission'
    elif re.search(r'\bdevis\b', p) and 'lettre de mission' not in p:
        out['type'] = 'devis'
    if re.search(r'\bcgv\b|conditions generales de vente', p):
        out['prestation'] = 'analyse_cgv'
    return out


# ------------------------------------------------------------------------------------------------ 2. message exact
def _boite(desk):
    if BOITE is not None:
        return BOITE(desk.c['mail'])
    from .mailbox import Mailbox
    return Mailbox(desk.c['mail'])


def _nom_affiche(msg):
    from email.utils import getaddresses
    valeurs = getaddresses([str(msg.get('From', ''))])
    return (valeurs[0][0] if valeurs else '') or ''


def candidats(desk, box, nom, jour):
    """Messages de la boîte de réception reçus ce jour-là (fuseau du cabinet) dont l'expéditeur porte TOUS les mots du nom."""
    from .mailbox import imap_quote
    cfg = desk.c['mail']
    folder = cfg.get('inbox') or 'INBOX'
    j = date.fromisoformat(jour)
    depuis, avant = (j - timedelta(days=1)).strftime('%d-%b-%Y'), (j + timedelta(days=2)).strftime('%d-%b-%Y')
    mots = [w for w in plier(nom).split(' ') if len(w) > 1]
    if not mots:
        raise Stop('expediteur_a_preciser')
    uids = box.search(folder, 'SINCE', depuis, 'BEFORE', avant, 'FROM', imap_quote(mots[-1]))
    if len(uids) > 200:
        raise Stop('trop_de_messages_a_controler')
    zone = _zone(desk)
    out = []
    for uid in uids:
        mail = box.fetch(folder, uid)
        etiquette = plier(_nom_affiche(mail.msg) + ' ' + (mail.sender or '').replace('.', ' ').replace('@', ' '))
        if all(w in etiquette.split(' ') or w in etiquette for w in mots) and mail.timestamp.astimezone(zone).date() == j:
            out.append(mail)
    return out


# ------------------------------------------------------------------------------------------------ 3. client du registre
def dossiers_du_client(desk, adresse):
    """Dossiers dont le client confirmé a cette adresse (fiche client de facturation, parties du dossier, correspondants « client »)."""
    a = str(adresse or '').strip().casefold()
    if not a:
        return []
    trouves = set()
    try:
        for r in desk.db.execute('SELECT matter, data_json FROM b53_clients'):
            if str(json.loads(r['data_json'] or '{}').get('email') or '').strip().casefold() == a:
                trouves.add(str(r['matter']))
    except Exception:   # noqa: BLE001 — registre de facturation pas encore créé
        pass
    try:
        for r in desk.db.execute("SELECT dossier_id FROM dossier_parties5697 WHERE role='client' AND lower(courriel)=?", (a,)):
            trouves.add(str(r['dossier_id']))
    except Exception:   # noqa: BLE001
        pass
    try:
        from .common import load_matters
        for m in load_matters(desk.c):
            if any(str(c.get('email') or '').casefold() == a and c.get('role') == 'client' for c in m.get('correspondents') or []):
                trouves.add(str(m['id']))
    except Stop:
        pass
    return sorted(trouves)


# ------------------------------------------------------------------------------------------------ commande durable
def _row(desk, ident):
    row = desk.db.execute('SELECT * FROM offres56109 WHERE id=?', (str(ident or ''),)).fetchone()
    if not row:
        raise Stop('introuvable')
    return row


def _avancer(desk, row, **champs):
    """Écriture conditionnelle sur la version lue : une autre requête a avancé la commande → ``version_perimee`` (409)."""
    champs['maj_le'] = _maintenant()
    cur = desk.db.execute('UPDATE offres56109 SET %s, version=version+1 WHERE id=? AND version=?' % ', '.join(k + '=?' for k in champs),
                          tuple(champs.values()) + (row['id'], row['version']))
    if not cur.rowcount:
        desk.db.rollback()
        raise Stop('version_perimee')
    desk.db.commit()
    return _row(desk, row['id'])


def _droits(desk, acteur, dossier=None):
    """Relus à CHAQUE étape : droit « facturation » du registre des droits et dossier visible (absent ou réservé : même 404 neutre)."""
    from . import acces5697
    from .facturation5653 import _require
    a = acces5697.acteur_de(acteur)
    if a.role not in ('administrateur', 'avocat', 'assistant'):
        raise Stop('role_insuffisant')
    _require(desk, a.compte, a.role, 'facturation')
    if dossier:
        acces5697.exiger(desk, a, dossier_id=dossier)
    return a


def _question(desk, row, code, extra=None):
    q = [{'code': code, 'question': QUESTIONS.get(code, code), **(extra or {})}]
    return _avancer(desk, row, etat='question', questions_json=json.dumps(q, ensure_ascii=False), erreur=code)


# ------------------------------------------------------------------------------------------------ 5. PDF réellement PDF
def texte_pdf(data):
    """Texte du PDF par le lecteur PDF du produit (pypdf livré) ; ``None`` si le fichier ne s'ouvre pas."""
    try:
        from ._vendor.pypdf import PdfReader
        reader = PdfReader(io.BytesIO(data), strict=False)
        if not reader.pages:
            return None
        return '\n'.join((p.extract_text() or '') for p in reader.pages)
    except Exception:   # noqa: BLE001 — fichier illisible : ce n'est pas un PDF exploitable
        return None


def verifier_pdf(apercu, attendus):
    """Garde-fou : un vrai PDF lisible, jamais un Word annoncé comme PDF ; prix HT et périmètre présents dans le texte rendu.

    ``attendus`` : liste de fragments (déjà formatés) qui doivent figurer dans le texte. Rend ``(sha256, taille)`` ou lève ``Stop``."""
    data = apercu.get('data') if isinstance(apercu, dict) else None
    if apercu.get('mime') != 'application/pdf' or not str(apercu.get('name') or '').lower().endswith('.pdf'):
        raise Stop('pdf_indisponible')
    if type(data) is not bytes or not 0 < len(data) <= MAX_PDF or not data.startswith(b'%PDF-') or b'%%EOF' not in data[-2048:]:
        raise Stop('pdf_indisponible')
    if data[:4] == b'PK\x03\x04':
        raise Stop('pdf_indisponible')
    texte = texte_pdf(data)
    if texte is None:
        raise Stop('pdf_illisible')
    plie = re.sub(r'[\s \xa0]+', ' ', plier(texte))
    for fragment in attendus:
        if re.sub(r'[\s \xa0]+', ' ', plier(fragment)) not in plie:
            raise Stop('pdf_non_concordant')
    return hashlib.sha256(data).hexdigest(), len(data)


# ------------------------------------------------------------------------------------------------ service
def _montant(desk, a, dossier, interprete):
    """Montant de l'instruction, sinon engagement du dossier (catalogue du cabinet, lecture) ; jamais inventé."""
    if interprete.get('montant_ht_cents'):
        return interprete['montant_ht_cents'], 'instruction'
    from . import catalogue56109 as C
    try:
        tarif = C.tarif_applicable(desk, a, dossier)
    except Stop as ex:
        if str(ex) == 'introuvable':
            return None, ''
        raise
    if tarif.get('origine') in ('convention_signee', 'proposition_acceptee', 'derogation', 'montant_retenu') and type(tarif.get('montant_ht_cents')) is int \
            and tarif['montant_ht_cents'] > 0:
        return tarif['montant_ht_cents'], 'catalogue:' + tarif['origine']
    return None, ''


def corps_reponse(type_doc, ht, tva, ttc, regime):
    from .honoraires5653 import fmt
    libelle = 'notre lettre de mission' if type_doc == 'lettre_mission' else 'notre devis'
    prix = 'au forfait de %s HT' % fmt(ht)
    if regime == 'normal' and tva:
        prix += ' (TVA %s, soit %s TTC)' % (fmt(tva), fmt(ttc))
    return ('Bonjour,\n\nNous vous remercions de votre message. Vous trouverez ci-joint, au format PDF, %s pour l’analyse de vos conditions '
            'générales de vente, %s.\n\nLe périmètre de la mission est limité à cette analyse ; toute autre prestation ferait l’objet d’un accord '
            'distinct.\n\nNous restons à votre disposition pour toute question.' % (libelle, prix))


def _cle_requete(row):
    return 'of-' + _sha(row['id'], row['acteur'])[:40]


def _cle_brouillon(row):
    return _sha('offre56109', row['id'])


def preparer(desk, acteur, data):
    """Commande « préparer l'offre et le brouillon » ; idempotente par (acteur, clé). Voir l'en-tête du module."""
    ensure_schema(desk)
    a = _droits(desk, acteur)
    cle = str((data or {}).get('cle') or '')
    if not re.fullmatch(r'[A-Za-z0-9_.:-]{8,80}', cle):
        raise Stop('cle_requise')
    instruction = re.sub(r'[ \t]+', ' ', str((data or {}).get('instruction') or '')).strip()[:4000]
    if len(instruction) < 8:
        raise Stop('instruction_requise')
    choix = {k: str((data or {}).get(k) or '')[:200] for k in ('dossier', 'message') if (data or {}).get(k)}
    empreinte = _sha(json.dumps({'i': instruction, 'c': choix}, sort_keys=True, ensure_ascii=False))
    row = desk.db.execute('SELECT * FROM offres56109 WHERE acteur=? AND cle=?', (a.compte, cle)).fetchone()
    if row and row['empreinte'] != empreinte:
        raise Stop('cle_reutilisee')
    if not row:
        ident = 'of-' + secrets.token_hex(10)
        quand = _maintenant()
        desk.db.execute('INSERT INTO offres56109(id,acteur,cle,empreinte,etat,instruction_json,cree_le,maj_le) VALUES(?,?,?,?,?,?,?,?)',
                        (ident, a.compte, cle, empreinte, 'nouvelle', json.dumps({'texte': instruction, 'choix': choix}, ensure_ascii=False), quand, quand))
        desk.db.commit()
        row = _row(desk, ident)
    return poursuivre(desk, a, row)


def poursuivre(desk, acteur, row):
    """Reprend la commande là où elle s'est arrêtée : une étape faite n'est jamais refaite (document, PDF, brouillon)."""
    a = _droits(desk, acteur, row['dossier'] or None)
    if row['etat'] in ('depose', 'pdf_indisponible'):
        return vue(desk, a, row['id'])
    if row['etat'] in ('depot_en_cours', 'a_rapprocher'):
        row = _rapprocher(desk, a, row)
        return vue(desk, a, row['id'])
    instr = json.loads(row['instruction_json'])
    interprete = interpreter(instr['texte'], jour_cabinet(desk))
    for code in interprete['ambiguites']:
        _question(desk, row, code)
        return vue(desk, a, row['id'])
    if row['etat'] in ('nouvelle', 'question'):
        if not interprete['nom']:
            _question(desk, row, 'expediteur_a_preciser')
            return vue(desk, a, row['id'])
        if not interprete['jour']:
            _question(desk, row, 'jour_a_preciser')
            return vue(desk, a, row['id'])
        box = _boite(desk)
        try:
            trouves = candidats(desk, box, interprete['nom'], interprete['jour'])
        finally:
            _fermer(box)
        voulu = instr['choix'].get('message')
        if voulu:
            trouves = [m for m in trouves if m.mid == voulu]
        if not trouves:
            _question(desk, row, 'message_introuvable')
            return vue(desk, a, row['id'])
        if len(trouves) > 1:
            _question(desk, row, 'plusieurs_messages', {'candidats': [{'message': m.mid, 'objet': m.subject[:120], 'recu': m.timestamp.isoformat()}
                                                                       for m in trouves[:10]]})
            return vue(desk, a, row['id'])
        mail = trouves[0]
        dossiers = dossiers_du_client(desk, mail.sender)
        if instr['choix'].get('dossier'):
            dossiers = [d for d in dossiers if d == instr['choix']['dossier']]
        if not dossiers:
            _question(desk, row, 'client_a_rattacher')
            return vue(desk, a, row['id'])
        if len(dossiers) > 1:
            _question(desk, row, 'plusieurs_dossiers', {'candidats': [{'dossier': d} for d in dossiers[:10]]})
            return vue(desk, a, row['id'])
        dossier = dossiers[0]
        _droits(desk, a, dossier)
        montant, origine = _montant(desk, a, dossier, interprete)
        if not montant:
            _question(desk, row, 'montant_a_preciser')
            return vue(desk, a, row['id'])
        type_doc = interprete['type'] or (desk.settings(PREFERENCE, 'lettre_mission') if desk.settings(PREFERENCE, 'lettre_mission') in ('devis', 'lettre_mission') else 'lettre_mission')
        from . import parcours56109
        source = {'boite': mail.mailbox, 'uidvalidity': mail.uidvalidity, 'uid': mail.uid, 'message_id': mail.mid, 'expediteur': mail.sender,
                  'objet': mail.subject[:300], 'recu': mail.timestamp.isoformat(), 'compte': desk.c['mail'].get('username', '')}
        parcours56109.plan_offre(parcours56109.Offre(row['cle'], source['compte'] or 'messagerie', mail.mid, mail.sender, dossier, 'modele-du-cabinet',
                                                     'courant', montant, type_doc, interprete['prestation'] or '', 'EUR'))
        row = _avancer(desk, row, etat='client_resolu', source_json=json.dumps(source, ensure_ascii=False), dossier=dossier, type_doc=type_doc,
                       montant_ht_cents=montant, origine_montant=origine, questions_json='[]', erreur='')
    if row['etat'] == 'client_resolu':
        row = _document(desk, a, row)
    if row['etat'] == 'document_prepare':
        row = _pdf(desk, a, row)
    if row['etat'] == 'pdf_pret':
        row = _deposer(desk, a, row)
    return vue(desk, a, row['id'])


def _fermer(box):
    try:
        box.close()
    except Exception:   # noqa: BLE001
        pass


def _document(desk, a, row):
    """Étape 4 : document financier versionné (prepare + textes de prix et de périmètre calculés par le serveur)."""
    from . import facturation5653 as fact, modeles5653 as models
    from .honoraires5653 import fmt
    from .pause5629 import checkpoint
    _droits(desk, a, row['dossier'])
    checkpoint(desk)
    ht = int(row['montant_ht_cents'])
    doc = fact.prepare(desk, a.compte, a.role, row['type_doc'], row['dossier'], {
        'mission': MISSION, 'perimetre_inclus': PERIMETRE_INCLUS, 'perimetre_exclu': PERIMETRE_EXCLU,
        'lignes': [{'nature': 'honoraires', 'libelle': MISSION, 'quantite': '1', 'unite': 'forfait', 'prix_unitaire_ht_cents': ht}]},
        request_key=_cle_requete(row))
    t = doc['snapshot']['totaux']
    if t['ht_cents'] != ht or len(doc['snapshot']['lignes']) != 1 or doc['snapshot']['lignes'][0]['montant_ht_cents'] != ht:
        raise Stop('montant_document_non_concordant')     # le document calculé doit reprendre exactement le montant attendu
    if doc['state'] not in fact.EDITABLE:
        raise Stop('document_emis_non_modifiable')
    regime = (doc['snapshot'].get('fiscal') or {}).get('regime')
    prix = 'Forfait de %s HT pour l’analyse des conditions générales de vente.' % fmt(ht)
    tva = ('TVA %s ; total %s TTC.' % (fmt(t['tva_cents']), fmt(t['ttc_cents']))) if regime == 'normal' else 'Régime de TVA à relire dans la fiche du cabinet.'
    if (doc['snapshot'].get('textes') or {}).get('mode_et_montant_honoraires') != prix:
        doc = fact.update(desk, a.compte, a.role, doc['id'], {'textes': {'mode_et_montant_honoraires': prix, 'mentions_tva_et_ttc': tva}}, doc['version'])
    modele = models.get(desk, doc['template_id'])
    return _avancer(desk, row, etat='document_prepare', document_id=doc['id'], document_version=int(doc['version']), modele_id=modele['id'],
                    modele_version=str(modele.get('version') or ''))


def _pdf(desk, a, row):
    """Étape 5 : PDF interne du MÊME instantané ; vrai PDF exigé, rouvert et concordant ; inscrit au registre des livrables."""
    from . import facturation5653 as fact, livrables5689 as liv
    from .honoraires5653 import fmt
    _droits(desk, a, row['dossier'])
    doc = fact.get(desk, row['document_id'])
    if int(doc['version']) != int(row['document_version']):
        raise Stop('version_perimee')                       # document modifié entre les étapes : préparer un successeur, jamais l'ancien PDF
    apercu = fact.preview_file(desk, a.compte, a.role, row['document_id'])
    try:
        sha, taille = verifier_pdf(apercu, [fmt(int(row['montant_ht_cents'])), 'conditions générales de vente'])
    except Stop as ex:
        code = str(ex).split(':')[0]
        return _avancer(desk, row, etat='pdf_indisponible', erreur=code,
                        questions_json=json.dumps([{'code': code, 'question': 'Le PDF n’a pas pu être produit ou vérifié (%s) : aucune pièce jointe '
                                                    'n’est déposée. Le document reste consultable dans Honoraires et facturation.' % code}], ensure_ascii=False))
    liv.ensure_schema(desk)
    item = liv.observe(desk, {'service': 'facturation5653', 'object': row['document_id'], 'version': 'apercu:' + sha, 'matter': row['dossier'],
                              'kind': 'convention', 'title': '%s — %s' % (fact.KINDS.get(row['type_doc'], 'Document'), MISSION), 'mime': 'application/pdf',
                              'sha256': sha, 'size': taille, 'owner': a.compte, 'right': 'voir_honoraires', 'state': 'verified',
                              'evidence': 'PDF interne rouvert et concordant (offre56109) ; joint au brouillon, aucun fichier publié',
                              'provenance': {'producteur': 'offre56109', 'document_financier': row['document_id'], 'version': row['document_version'],
                                             'modele': row['modele_id'], 'modele_version': row['modele_version'], 'commande': row['id']}})
    desk.db.commit()
    return _avancer(desk, row, etat='pdf_pret', pdf_nom=str(apercu['name'])[:200], pdf_sha256=sha, pdf_taille=taille, livrable_item=str(item or ''))


def _mail_source(box, src):
    folder = src['boite']
    mail = box.fetch(folder, src['uid'])
    if str(mail.uidvalidity) != str(src['uidvalidity']) or mail.mid != src['message_id']:
        raise Stop('message_source_change')
    return mail


def _brouillon(desk, a, row, box):
    """Même message rendu à chaque fois (même clé, même texte, même PDF) : sert au dépôt et au rapprochement."""
    from . import facturation5653 as fact
    from .catalogue56109 import montants_hors_tarif
    from .mailbox import make_draft
    from .signature56102 import effective
    src = json.loads(row['source_json'])
    mail = _mail_source(box, src)
    doc = fact.get(desk, row['document_id'])
    if int(doc['version']) != int(row['document_version']):
        raise Stop('version_perimee')
    t = doc['snapshot']['totaux']
    corps = corps_reponse(row['type_doc'], t['ht_cents'], t['tva_cents'], t['ttc_cents'], (doc['snapshot'].get('fiscal') or {}).get('regime'))
    if montants_hors_tarif(corps, [t['ht_cents'], t['tva_cents'], t['ttc_cents']]):
        raise Stop('montant_hors_tarif')                    # garde-fou : aucun montant qui ne vient pas du document calculé
    apercu = fact.preview_file(desk, a.compte, a.role, row['document_id'])
    if apercu.get('mime') != 'application/pdf' or hashlib.sha256(apercu.get('data') or b'').hexdigest() != row['pdf_sha256']:
        raise Stop('pdf_modifie_depuis_verification')
    if mail.sender.casefold() != src['expediteur'].casefold():
        raise Stop('destinataire_non_confirme')
    msg = make_draft(mail, desk.c['mail'], corps, _cle_brouillon(row), to=mail.sender, signature=effective(desk, a.compte),
                     attachments=[(row['pdf_nom'], apercu['data'], 'application/pdf')])
    return msg


def _piece_conforme(box, desk, recu, sha):
    saved = box.fetch(desk.c['mail']['drafts'], recu['uid'])
    pieces = [p for p in saved.msg.iter_attachments() if p.get_content_type() == 'application/pdf']
    return len(pieces) == 1 and hashlib.sha256(pieces[0].get_payload(decode=True) or b'').hexdigest() == sha


def _deposer(desk, a, row):
    """Étape 7 : dépôt UNIQUE dans Brouillons (rien n'est envoyé) ; reçu durable ; réponse perdue → à rapprocher."""
    from .pause5629 import checkpoint
    _droits(desk, a, row['dossier'])
    box = _boite(desk)
    try:
        msg = _brouillon(desk, a, row, box)
        mid = str(msg['Message-ID'])
        if box.find_own_draft(mid):                         # déjà là (autre onglet, reprise) : on rapproche, on ne recrée pas
            row = _avancer(desk, row, etat='depot_en_cours', brouillon_mid=mid)
            return _rapprocher(desk, a, row, box)
        checkpoint(desk)
        row = _avancer(desk, row, etat='depot_en_cours', brouillon_mid=mid)
        try:
            box.append_draft(msg)
            recu = box.verify_draft(msg)
        except Exception as ex:   # noqa: BLE001 — réponse perdue ou relecture impossible : jamais de second dépôt aveugle
            code = str(ex).split(':')[0] if isinstance(ex, Stop) else 'verification_imap_indisponible'
            return _avancer(desk, row, etat='a_rapprocher', erreur=code)
        if not _piece_conforme(box, desk, recu, row['pdf_sha256']):
            return _avancer(desk, row, etat='a_rapprocher', erreur='piece_jointe_non_conforme')
        desk.audit('offre56109_brouillon_depose', {'offre': row['id'], 'dossier': row['dossier'], 'envoi': False})
        return _avancer(desk, row, etat='depose', erreur='', recu_json=json.dumps({**recu, 'envoi': False, 'piece_sha256': row['pdf_sha256']}))
    finally:
        _fermer(box)


def _rapprocher(desk, a, row, box=None):
    """Dépôt incertain : on cherche la MÊME clé ; trouvée et conforme → déposé ; absente → reste « à rapprocher » (aucune recréation)."""
    propre = box is None
    box = box or _boite(desk)
    try:
        msg = _brouillon(desk, a, row, box)
        if not box.find_own_draft(str(msg['Message-ID'])):
            return row if row['etat'] == 'a_rapprocher' else _avancer(desk, row, etat='a_rapprocher', erreur=row['erreur'] or 'brouillon_non_retrouve')
        try:
            recu = box.verify_draft(msg)
        except Stop as ex:
            return _avancer(desk, row, etat='a_rapprocher', erreur=str(ex).split(':')[0])
        if not _piece_conforme(box, desk, recu, row['pdf_sha256']):
            return _avancer(desk, row, etat='a_rapprocher', erreur='piece_jointe_non_conforme')
        return _avancer(desk, row, etat='depose', erreur='', recu_json=json.dumps({**recu, 'envoi': False, 'piece_sha256': row['pdf_sha256'], 'rapproche': True}))
    finally:
        if propre:
            _fermer(box)


def vue(desk, acteur, ident):
    """Lecture seule (aucune écriture) : état, source, prix, version, PDF, brouillon et questions."""
    from . import acces5697
    a = acces5697.acteur_de(acteur)
    row = _row(desk, ident)
    if row['acteur'] != a.compte and a.role != 'administrateur':
        raise Stop('introuvable')
    if row['dossier']:
        acces5697.exiger(desk, a, dossier_id=row['dossier'])
    from .honoraires5653 import fmt
    src = json.loads(row['source_json'] or '{}')
    return {'id': row['id'], 'etat': row['etat'], 'etat_libelle': ETATS.get(row['etat'], row['etat']), 'version': row['version'], 'dossier': row['dossier'],
            'type': row['type_doc'], 'montant_ht': fmt(row['montant_ht_cents']) if row['montant_ht_cents'] else None, 'origine_montant': row['origine_montant'],
            'source': {k: src.get(k) for k in ('message_id', 'objet', 'recu', 'expediteur')} if src else {}, 'document': row['document_id'],
            'document_version': row['document_version'], 'modele': row['modele_id'], 'modele_version': row['modele_version'],
            'pdf': {'nom': row['pdf_nom'], 'sha256': row['pdf_sha256'], 'taille': row['pdf_taille']} if row['pdf_sha256'] else None,
            'livrable': row['livrable_item'], 'brouillon': json.loads(row['recu_json'] or '{}'), 'questions': json.loads(row['questions_json'] or '[]'),
            'erreur': row['erreur'], 'envoi': False}


def carte_html(desk, prefix, role):
    """Carte « Offre et PDF joint depuis un courriel » (Mes routines | Mes agents IA) ; formulaire protégé, clé d'idempotence."""
    from html import escape as e
    import uuid
    if role not in ('administrateur', 'avocat', 'assistant'):
        return ''
    return ('<div class="c5614-routine r52-card g4-carte" id="g4-offre"><div class="r52-head"><strong>Offre et PDF joint depuis un courriel</strong></div>'
            '<span class="r52-meta">Une instruction suffit (expéditeur, jour, prestation, forfait HT). Le PDF est vérifié, le brouillon est déposé '
            'dans le fil ; rien n’est envoyé.</span>'
            '<form class="m5-form" data-api="m56109/parcours/offre" data-reload><input type="hidden" name="cle" value="%s">'
            '<label>Instruction<textarea name="instruction" rows="3" maxlength="4000" required placeholder="%s"></textarea></label>'
            '<label>Dossier (facultatif)<input name="dossier" maxlength="200"></label>'
            '<button class="ax-btn" type="submit">Préparer l’offre et le brouillon</button><p role="status" class="m5-status"></p></form></div>') % (
        uuid.uuid4().hex, e('Réponds au courriel de … du 22 novembre : lettre de mission pour l’analyse de ses CGV au forfait de … € HT, PDF joint.', quote=True))
