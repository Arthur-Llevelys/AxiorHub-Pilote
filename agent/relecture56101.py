"""5.6.101 (lot R · R01-R06, V101-09, V101-10) : « À relire » unique — brouillons Gmail, documents produits et notes de l'IA.

La file commune de la 5.6.100 (``relecture56100``, projection ``file56100_items``) reçoit trois nouvelles sources. Le **producteur** reste la
source de vérité du texte, de la version et des droits ; la projection ne garde que des références et des métadonnées.

- **Gmail** (``g-…``, R01) : projets locaux de ``gmail5697`` (préparés par l'avocat ou par l'IA), propres à leur auteur (colonne
  ``proprietaire`` : le projet d'une autre personne n'existe pas pour celle-ci, administrateur compris). Révision = version du projet. Mêmes
  commandes que les propositions : Enregistrer, Retenir (rien n'est déposé), Écarter (projet abandonné), Reporter, Déposer dans Brouillons.
  Le dépôt passe par l'API Gmail (``gmail5697.deposer``) après une intention durable ; « Déposé » seulement après relecture du brouillon chez
  Google (empreinte comparée par ``gmail5697``) ; un doute donne « À vérifier » et bloque un second dépôt. Droits relus sur le message réel
  (garde ``acces_courriel56100``) ; une connexion à renouveler ou un contrôle en panne masquent les éléments et le disent. Les brouillons Gmail
  sont lus par un appel séparé (comme les brouillons IMAP) : une panne Gmail ne bloque jamais la liste locale.
- **Documents** (``d-…``, R02) : livrables à relire de ``livrables5689`` (version courante publiée ou vérifiée, relecture « À relire »), hors
  courriels (déjà présents comme propositions), hors notes de l'IA (source suivante) et hors documents de facturation (droit « honoraires » :
  décision de gestion, R06). Révision = empreinte de l'identifiant de version : le lecteur montre l'aperçu et la version exacte. Valider la
  version, Écarter, Reporter, Demander une correction à l'IA (``propositions5689.demander_ia`` : version de base et empreinte exigées), décider
  d'une proposition (``propositions5689.decider`` : avant/après, conflit sans écrasement), Ouvrir dans l'éditeur.
- **Notes de l'IA** (``n-…``, R03) : notes « nouvelles écritures » de ``delta56100``. Valider la note (aucune proposition arguments–preuves
  appliquée), Rejeter avec motif, Enregistrer la note modifiée, Reporter, Ouvrir l'onglet du dossier. Décision par ``delta56100.decider``.

Garanties communes (R04) : révision attendue (409), clé d'idempotence (même clé et mêmes paramètres = même reçu ; autres paramètres = 409),
droits revérifiés au moment d'agir et avant de rendre un ancien reçu (objet absent ou interdit : même 404 neutre ; contrôle indisponible :
503), trace et reçu écrits ensemble. L'écriture du producteur est faite par son propre service (qui valide sa transaction) ; le reçu suit dans
une seconde transaction : un rejeu après une coupure entre les deux trouve une révision changée (409), jamais un second effet.

Aucune écriture pendant une lecture ; la projection est alimentée par les producteurs (écriture), le rattrapage du démarrage et la boucle des
automatismes (``rattraper_sources``, filigrane par source, chaque source isolée). Aucune action externe n'est déclenchée par un modèle ou un
automatisme : seul le geste de la personne dépose un brouillon ; rien n'est jamais envoyé depuis « À relire ».
"""
from datetime import datetime, timezone
from hashlib import sha256
import json
import re
import secrets
import sqlite3
from urllib.parse import urlencode

from .common import Stop
from . import relecture56100 as rl

Refus = rl.Refus
NOUVEAUX = ('gmail', 'document', 'note_ia')
PREFIXES = {'g-': 'gmail', 'd-': 'document', 'n-': 'note_ia'}
TYPES_CONTRAT = {'proposition': 'courriel', 'imap': 'courriel', 'gmail': 'gmail', 'document': 'document', 'note_ia': 'note_ia'}
SERVICES_EXCLUS = ('messagerie', 'delta56100')
ETATS_GMAIL_RELIRE = ('local', 'modifie_a_distance', 'depot_a_verifier', 'depot_en_cours')
ETATS_GMAIL_DEPOSE = ('depose', 'envoi_en_cours', 'envoye', 'envoi_a_verifier')
LIMITE_GMAIL = 100
FILTRES = ('tous', 'courriel', 'proposition', 'brouillon', 'gmail', 'document', 'note_ia')
LIBELLES_FILTRES = (('tous', 'Tout'), ('courriel', 'Courriels'), ('gmail', 'Gmail'), ('document', 'Documents'), ('note_ia', 'Notes de l’IA'))
SOUS_FILTRES = (('proposition', 'Propositions'), ('brouillon', 'Brouillons de la messagerie'))
KINDS_LOCAUX = {'tous': ('proposition', 'document', 'note_ia'), 'courriel': ('proposition',), 'proposition': ('proposition',),
                'document': ('document',), 'note_ia': ('note_ia',)}
ECRITURES = {'gmail': ('decision', 'enregistrer', 'deposer'), 'document': ('decision', 'corriger', 'proposition'), 'note_ia': ('decision', 'enregistrer')}
DECISIONS = {'gmail': ('retenir', 'ecarter', 'reporter'), 'document': ('valider', 'ecarter', 'reporter'), 'note_ia': ('valider', 'rejeter', 'reporter')}
SECTIONS = {'gmail': ('message', 'pieces', 'sources'), 'document': ('propositions',), 'note_ia': ('sources',)}
PROPOSITION = re.compile(r'^[A-Za-z0-9_-]{1,80}$')
INSTRUCTION_MIN, INSTRUCTION_MAX, ZONE_MAX, MOTIF_MIN, MOTIF_MAX, NOTE_MAX = 3, 2000, 4000, 3, 500, 20000
APERCU_MAX = 60000

MESSAGES = {'valide': 'Version validée. Rien n’est envoyé ni déposé.',
            'document_ecarte': 'Document retiré de la relecture. Le fichier reste dans le dossier.',
            'note_validee': 'Note validée. Aucune proposition arguments–preuves n’a été appliquée au dossier.',
            'note_rejetee': 'Note rejetée ; le motif est conservé au journal du dossier.',
            'note_enregistree': 'Note enregistrée ; elle reste à relire.',
            'correction': 'Correction demandée à l’IA : la proposition (avant/après) apparaîtra sous « Propositions de l’IA ». Rien n’est modifié sans votre décision.',
            'appliquee': 'Proposition appliquée : la nouvelle version du document est à relire.',
            'refusee': 'Proposition refusée. Le document n’a pas changé.',
            'editeur': 'Le document est ouvert dans l’éditeur : la proposition attend sa fermeture ; rien n’est remplacé pendant une coédition.',
            'gmail_ecarte': 'Projet Gmail abandonné. Un brouillon déjà déposé reste dans Gmail, où vous pouvez le supprimer.',
            'gmail_depose': 'Déposé dans les brouillons Gmail et relu chez Google. Rien n’est envoyé.',
            'gmail_a_verifier': 'Dépôt Gmail incertain : vérifiez dans Gmail (vue Gmail, « Vérifier ») avant toute nouvelle demande. Rien n’est envoyé.'}
TEXTES = {'conflit_edition_concurrente': 'Le document a changé pendant ce temps : la proposition n’a pas été appliquée et rien n’a été écrasé. '
                                         'Comparez les versions dans la visionneuse avant de choisir.',
          'gmail_indisponible': 'Gmail ne répond pas ou la connexion est à renouveler : rien n’a été déposé. Réessayez plus tard.',
          'consentement_brouillons_requis': 'Autorisez d’abord l’enregistrement des brouillons dans Gmail (Paramètres › Connexions › Gmail).',
          'destinataire_requis': 'Indiquez au moins un destinataire dans la vue Gmail avant de déposer.',
          'projet_perime': 'Un nouveau message est arrivé dans ce fil : relisez et enregistrez le projet avant de le déposer.',
          'brouillon_modifie_a_distance': 'Le brouillon a été modifié dans Gmail : comparez les deux versions dans la vue Gmail ; rien n’a été écrasé.',
          'instruction_invalide': 'Décrivez la correction souhaitée (3 à 2000 caractères).',
          'motif_requis': 'Indiquez le motif du rejet (3 à 500 caractères).',
          'note_invalide': 'La note doit compter de 3 à 20 000 caractères.',
          'correction_impossible': 'La correction assistée ne porte que sur un document Word vérifié, non protégé, en dernière version.',
          'proposition_deja_traitee': 'Cette proposition a déjà été traitée : rechargez le document.',
          'analyse_remplacee': 'Une analyse plus récente de cette pièce existe : ouvrez-la.',
          'file_attente_pleine': 'File de travail pleine : rien n’a été enregistré. Renvoyez la même demande dans quelques minutes.',
          'commande_invalide': 'Commande non disponible pour cet élément.'}


# ------------------------------------------------------------------------------------------------------------- outils
def kind_de(ident):
    return PREFIXES.get(str(ident or '')[:2], '')


def _id(prefixe, genre, ref):
    return prefixe + sha256(('%s|%s' % (genre, ref)).encode()).hexdigest()[:32]


def _table(desk, nom):
    return bool(desk.db.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name=?", (nom,)).fetchone())


def _upsert(desk, ligne):
    desk.db.execute('INSERT INTO file56100_items(cabinet,id,kind,matter,mail_key,ref_source,revision,titre,search_norm,updated,etat,proprietaire) '
                    'VALUES(?,?,?,?,?,?,?,?,?,?,?,?) ON CONFLICT(cabinet,id) DO UPDATE SET kind=excluded.kind,matter=excluded.matter,'
                    'mail_key=excluded.mail_key,ref_source=excluded.ref_source,revision=excluded.revision,titre=excluded.titre,'
                    'search_norm=excluded.search_norm,updated=excluded.updated,etat=excluded.etat,proprietaire=excluded.proprietaire',
                    (rl.CABINET, ligne['id'], ligne['kind'], ligne['matter'], ligne['mail_key'], ligne['ref_source'], ligne['revision'],
                     ligne['titre'], ligne['search_norm'], int(ligne['updated']), ligne['etat'], ligne.get('proprietaire') or ''))


def _retirer(desk, ident):
    """Producteur disparu ou élément sorti de la relecture sans passer par le lecteur : « Remplacé », jamais une décision inventée."""
    desk.db.execute("UPDATE file56100_items SET etat='remplace' WHERE cabinet=? AND id=? AND etat IN ('a_relire','retenu')", (rl.CABINET, ident))


def _ref(ligne):
    return str(ligne.get('ref_source') or '').split(':', 1)[-1]


def _droits(desk, a):
    from .membres5650 import effective_rights
    try:
        return effective_rights(desk.c['state_dir'], a.compte, a.role)
    except Stop:
        return {}


def _peut_valider(desk, a):
    """Valider ou écarter une version, valider ou rejeter une note : administrateur ou droit « valider » (membres5650)."""
    return a.role == 'administrateur' or bool(_droits(desk, a).get('valider'))


def _cle_service(a, cle, prefixe='r101-'):
    """Clé transmise au service producteur, dérivée de la clé du lecteur et du compte (même geste = même clé, d'un compte à l'autre jamais)."""
    return prefixe + sha256(('%s|%s' % (a.compte, cle)).encode()).hexdigest()[:40]


def _libelle_dossier(libelles, mid):
    return libelles.get(mid, mid) if mid else 'Sans dossier'


# ------------------------------------------------------------------------------------------------------------- Gmail : projection
def id_gmail(projet_id):
    return _id('g-', 'gmail', projet_id)


def revision_gmail(p):
    return 'g%d' % int(p['version'])


def _version_gmail(revision):
    trouve = re.fullmatch(r'g(\d{1,12})', str(revision or ''))
    if not trouve:
        raise Refus(409, 'version_perimee')
    return int(trouve.group(1))


def _etat_gmail(p, existant=None):
    texte = str(p.get('texte') or '')
    if p['etat'] == 'abandonne':
        return 'ecarte'
    if p['etat'] in ETATS_GMAIL_DEPOSE:
        return 'depose'
    if p['etat'] in ETATS_GMAIL_RELIRE and texte.strip():
        if existant and existant['etat'] == 'retenu' and existant['revision'] == revision_gmail(p):
            return 'retenu'                           # « Retenir » est une décision locale : elle vaut pour la version relue
        return 'a_relire'
    return None                                       # préparation par l'IA en cours, texte vide : pas encore à relire


def _ligne_gmail(p, libelles, existant=None):
    from .messages5697 import cle_gmail
    etat = _etat_gmail(p, existant)
    if etat is None:
        return None
    mid = str(p.get('matter') or '')
    objet = str(p.get('objet') or '') or '(sans objet)'
    return {'id': id_gmail(p['id']), 'kind': 'gmail', 'matter': mid, 'mail_key': cle_gmail(p['connexion_id'], p['source_id']),
            'ref_source': 'gmail5697:' + p['id'], 'revision': revision_gmail(p), 'titre': objet[:200],
            'search_norm': rl.normaliser(' '.join((objet, str(p.get('a') or ''), libelles.get(mid, mid))))[:1500],
            'updated': rl._ms(p.get('cree')), 'etat': etat, 'proprietaire': str(p['owner'])}


def projeter_gmail(desk, projet_id, commit=True, libelles=None):
    if not _table(desk, 'gmail5697_projets'):
        return None
    ident = id_gmail(projet_id)
    row = desk.db.execute('SELECT * FROM gmail5697_projets WHERE id=?', (str(projet_id),)).fetchone()
    existant = rl._ligne_projetee(desk, ident)
    ligne = _ligne_gmail(dict(row), libelles if libelles is not None else rl._libelles(desk), existant) if row else None
    if ligne:
        _upsert(desk, ligne)
    elif existant:
        _retirer(desk, ident)
    if commit:
        desk.db.commit()
    return ligne


def projeter_gmail_compte(desk, owner, limite=200):
    """Après une écriture de la vue Gmail (route ``m5689/gmail/…``) : projets de la personne remis à jour (quelques lignes)."""
    if not _table(desk, 'gmail5697_projets'):
        return 0
    libelles = rl._libelles(desk)
    rows = desk.db.execute('SELECT id FROM gmail5697_projets WHERE owner=? ORDER BY maj DESC LIMIT ?', (str(owner), int(limite))).fetchall()
    for r in rows:
        projeter_gmail(desk, r[0], commit=False, libelles=libelles)
    desk.db.commit()
    return len(rows)


# ------------------------------------------------------------------------------------------------------------- documents : projection
def id_document(document_id):
    return _id('d-', 'document', document_id)


def revision_document(row):
    return 'v' + sha256(str(row['version_id']).encode()).hexdigest()[:24]


def admissible(row):
    """Livrable relu ici : ni courriel (proposition), ni note de l'IA (source dédiée), ni document de facturation (R06 : décision de gestion)."""
    return (row['kind'] != 'courriel' and row['source_service'] not in SERVICES_EXCLUS and not row['required_right']
            and not row['response_id'])


def _etat_document(row):
    if row['lifecycle'] == 'archived':
        return 'ecarte'
    if row['lifecycle'] != 'current':
        return None
    if row['review_state'] == 'to_review' and row['production_state'] in ('published', 'verified'):
        return 'a_relire'
    if row['review_state'] == 'reviewed':
        return 'retenu'
    return None


def _courant_document(desk, document_id):
    row = desk.db.execute("SELECT * FROM prepared_items5689 WHERE document_id=? AND lifecycle IN ('current','archived') ORDER BY version_number DESC LIMIT 1",
                          (str(document_id),)).fetchone()
    return dict(row) if row else None


def _ligne_document(row, libelles):
    if not admissible(row):
        return None
    etat = _etat_document(row)
    if etat is None:
        return None
    from .livrables5689 import NATURES
    mid = str(row['matter_id'] or '')
    titre = str(row['title'] or '') or 'Document'
    return {'id': id_document(row['document_id']), 'kind': 'document', 'matter': mid, 'mail_key': '', 'ref_source': 'livrables5689:' + row['document_id'],
            'revision': revision_document(row), 'titre': titre[:200],
            'search_norm': rl.normaliser(' '.join((titre, libelles.get(mid, mid), NATURES.get(row['kind'], ''))))[:1500],
            'updated': rl._ms(row['source_at'] or row['updated']), 'etat': etat, 'proprietaire': ''}


def projeter_document(desk, document_id, commit=True, libelles=None):
    if not _table(desk, 'prepared_items5689'):
        return None
    ident = id_document(document_id)
    row = _courant_document(desk, document_id)
    ligne = _ligne_document(row, libelles if libelles is not None else rl._libelles(desk)) if row else None
    if ligne:
        _upsert(desk, ligne)
    elif rl._ligne_projetee(desk, ident):
        _retirer(desk, ident)
    if commit:
        desk.db.commit()
    return ligne


# ------------------------------------------------------------------------------------------------------------- notes de l'IA : projection
def id_note(resultat_id):
    return _id('n-', 'note_ia', resultat_id)


def revision_note(r):
    return 'n%d' % int(r['revision'])


def _etat_note(r):
    if r['remplace_par']:
        return 'remplace'
    return {'a_relire': 'a_relire', 'a_completer': 'a_relire', 'retenu': 'retenu', 'ecarte': 'ecarte'}.get(r['etat'])


def _ligne_note(r, libelles):
    etat = _etat_note(r)
    if etat is None:
        return None
    mid = str(r['dossier'] or '')
    titre = 'Nouvelles écritures : ' + str(r['titre'] or 'pièce reçue')
    return {'id': id_note(r['id']), 'kind': 'note_ia', 'matter': mid, 'mail_key': '', 'ref_source': 'delta56100:' + r['id'],
            'revision': revision_note(r), 'titre': titre[:200], 'search_norm': rl.normaliser(' '.join((titre, libelles.get(mid, mid))))[:1500],
            'updated': rl._ms(r['created']), 'etat': etat, 'proprietaire': ''}


def projeter_note(desk, resultat_id, commit=True, libelles=None):
    if not _table(desk, 'delta56100_resultats'):
        return None
    ident = id_note(resultat_id)
    row = desk.db.execute('SELECT * FROM delta56100_resultats WHERE id=?', (str(resultat_id),)).fetchone()
    ligne = _ligne_note(dict(row), libelles if libelles is not None else rl._libelles(desk)) if row else None
    if ligne:
        _upsert(desk, ligne)
    elif rl._ligne_projetee(desk, ident):
        _retirer(desk, ident)
    if commit:
        desk.db.commit()
    return ligne


PROJETEURS = {'gmail': projeter_gmail, 'document': projeter_document, 'note_ia': projeter_note}


def projeter_apres_ecriture(desk, kind, ref):
    """Point d'appel des producteurs (gmail5697, livrables5689, delta56100) : ne casse jamais l'écriture qui vient de réussir."""
    try:
        PROJETEURS[kind](desk, ref)
    except (sqlite3.Error, OSError, ValueError, KeyError, TypeError, Stop):
        try:
            desk.db.rollback()
        except sqlite3.Error:
            pass


# ------------------------------------------------------------------------------------------------------------- rattrapage
def _filigrane(desk, nom):
    fil = desk.settings('relecture56101:filigrane:' + nom, None)
    return fil if isinstance(fil, list) and len(fil) == 2 else ['', '']


def _rattraper(desk, nom, table, requete, projeter, limite, libelles):
    if not _table(desk, table):
        return 0
    fil = _filigrane(desk, nom)
    rows = desk.db.execute(requete, (str(fil[0]), str(fil[1]), int(limite))).fetchall()
    for r in rows:
        projeter(desk, r[2], commit=False, libelles=libelles)
    desk.db.commit()
    if rows:
        desk.setting('relecture56101:filigrane:' + nom, [rows[-1][1], rows[-1][0]])
    return len(rows)


def rattraper_sources(desk, limite=300):
    """Rattrapage relançable et borné des trois sources (démarrage, boucle des automatismes) ; chaque source est isolée : une table illisible
    n'arrête pas les autres et ne fait rien disparaître. Une base inchangée n'écrit rien."""
    libelles = rl._libelles(desk)
    out = {}
    plans = (('gmail', 'gmail5697_projets', "SELECT id,COALESCE(maj,''),id FROM gmail5697_projets WHERE (COALESCE(maj,''),id)>(?,?) ORDER BY 2,1 LIMIT ?", projeter_gmail),
             ('document', 'prepared_items5689', "SELECT id,COALESCE(updated,''),document_id FROM prepared_items5689 WHERE (COALESCE(updated,''),id)>(?,?) ORDER BY 2,1 LIMIT ?",
              projeter_document),
             ('note_ia', 'delta56100_resultats', "SELECT id,COALESCE(updated,''),id FROM delta56100_resultats WHERE (COALESCE(updated,''),id)>(?,?) ORDER BY 2,1 LIMIT ?",
              projeter_note))
    for nom, table, requete, projeter in plans:
        try:
            out[nom] = _rattraper(desk, nom, table, requete, projeter, limite, libelles)
        except (sqlite3.Error, OSError, ValueError, KeyError, TypeError, Stop):
            try:
                desk.db.rollback()
            except sqlite3.Error:
                pass
            out[nom] = 'erreur'
    return out


# ------------------------------------------------------------------------------------------------------------- filtres mémorisés (R05)
def _cle_filtre(compte):
    return 'relecture56101:filtre:' + sha256(str(compte).encode()).hexdigest()[:24]


def filtre_memorise(desk, compte):
    """Filtre choisi par la personne (lecture seule)."""
    try:
        valeur = desk.settings(_cle_filtre(compte), '')
    except sqlite3.Error:
        return ''
    return valeur if valeur in FILTRES else ''


def memoriser_filtre(desk, acteur, data):
    """``POST …/relecture/filtres`` : préférence de la personne (aucune écriture pendant une lecture ; même valeur : rien n'est réécrit)."""
    a = rl._acteur(acteur)
    source = data.get('source') if isinstance(data, dict) else None
    if source not in FILTRES:
        raise Refus(422, 'source_invalide')
    if filtre_memorise(desk, a.compte) != source:
        desk.setting(_cle_filtre(a.compte), source)
    return {'source': source, 'message': 'Filtre mémorisé pour vous.'}


# ------------------------------------------------------------------------------------------------------------- Gmail : liste séparée
def _connexion_gmail(desk, compte):
    return desk.db.execute("SELECT id,etat FROM google5697_connexions WHERE cabinet_id='cabinet' AND owner=? AND usage='gmail'", (str(compte),)).fetchone()


def liste_gmail(desk, acteur, q='', maintenant=None):
    """Brouillons Gmail de la personne (appel séparé de la liste locale) : propriétaire et droits appliqués AVANT la sortie ; une connexion à
    renouveler ou un contrôle en panne masquent les éléments et le disent (jamais un faux zéro, jamais un accès élargi)."""
    a = rl._acteur(acteur)
    maintenant = int(rl._maintenant() if maintenant is None else maintenant)
    lu = datetime.now(timezone.utc).isoformat()
    try:
        from . import gmail5697 as gm
        if not (_table(desk, 'gmail5697_projets') and _table(desk, 'google5697_connexions')) or not gm.actif(desk):
            return [], {'etat': 'desactive', 'lu_le': lu}
        conn = _connexion_gmail(desk, a.compte)
        if not conn:
            return [], {'etat': 'non_connecte', 'lu_le': lu}
        rows = [dict(r) for r in desk.db.execute(
            "SELECT i.id,i.kind,i.matter,i.mail_key,i.titre,i.revision,i.updated,i.etat,i.search_norm FROM file56100_items i WHERE i.cabinet=? "
            "AND i.kind='gmail' AND i.proprietaire=? AND i.etat='a_relire' AND NOT EXISTS (SELECT 1 FROM file56100_reports r WHERE r.cabinet=i.cabinet "
            "AND r.item=i.id AND r.revision=i.revision AND r.acteur=? AND r.jusqu_a>?) ORDER BY i.updated DESC,i.id DESC LIMIT ?",
            (rl.CABINET, a.compte, a.compte, maintenant, LIMITE_GMAIL)).fetchall()]
        if conn['etat'] != 'active':
            return [], {'etat': 'indisponible', 'masques': len(rows), 'lu_le': lu,
                        'raison': 'Connexion Gmail à renouveler : les brouillons Gmail sont masqués tant qu’elle n’est pas rétablie.'}
        ids = rl.visibles(desk, a, rows)
    except Refus as ex:
        if ex.status != 503:
            raise
        return [], {'etat': 'indisponible', 'lu_le': lu, 'raison': 'Contrôle des droits indisponible : les brouillons Gmail sont masqués.'}
    except (sqlite3.Error, Stop, OSError, ValueError, KeyError):
        return [], {'etat': 'indisponible', 'lu_le': lu, 'raison': 'Gmail est momentanément illisible : les brouillons Gmail sont masqués.'}
    lignes = [x for x in rows if x['id'] in ids]
    if q:
        lignes = [x for x in lignes if q in x['search_norm']]
    return lignes, {'etat': 'a_jour', 'lu_le': lu, 'complet': len(rows) < LIMITE_GMAIL}


# ------------------------------------------------------------------------------------------------------------- accès à l'objet réel
def _ligne(desk, a, ident, kind):
    ligne = rl._ligne_projetee(desk, ident)
    if ligne is None or ligne['kind'] != kind:
        raise Refus(404, 'introuvable')
    if kind == 'gmail' and ligne.get('proprietaire') != a.compte:
        raise Refus(404, 'introuvable')               # projet Gmail d'une autre personne : il n'existe pas pour celle-ci
    rl.exiger(desk, a, ligne)
    return ligne


def _gmail_courant(desk, a, ligne):
    """Projet Gmail relu chez son producteur, droits relus sur le message réel (garde commune) AVANT toute lecture du texte."""
    from . import gmail5697 as gm
    from .acces_courriel56100 import exiger as garde
    pid = _ref(ligne)
    try:
        garde(desk, (a.compte, a.role), {'genre': 'gmail', 'projet': pid})
    except Stop as ex:
        raise (Refus(404, 'introuvable') if str(ex).split(':')[0] in ('introuvable', 'role_insuffisant') else Refus(503, 'controle_indisponible')) from None
    try:
        return gm, gm.projet(desk, a.compte, pid)
    except Stop:
        raise Refus(404, 'introuvable') from None


def _document_courant(desk, a, ligne):
    from . import livrables5689 as liv
    caches = rl._masques(desk, a)
    try:
        row = liv.charger(desk, _ref(ligne), frozenset(caches), droits=_droits(desk, a))
    except Stop as ex:
        raise (Refus(503, 'controle_indisponible') if str(ex).split(':')[0] == 'controle_indisponible' else Refus(404, 'introuvable')) from None
    if not admissible(row):
        raise Refus(404, 'introuvable')
    if str(row['matter_id'] or '') != ligne['matter']:          # dossier changé depuis la projection : le producteur fait foi
        rl.exiger(desk, a, {**ligne, 'matter': str(row['matter_id'] or '')})
    return liv, row


def _note_courante(desk, a, ligne):
    from . import delta56100 as dl
    try:
        return dl, dl.vue(desk, (a.compte, a.role), _ref(ligne))
    except Stop as ex:
        code = str(ex).split(':')[0]
        raise (Refus(404, 'introuvable') if code in ('introuvable', 'dossier_reserve', 'role_insuffisant') else Refus(503, 'controle_indisponible')) from None


# ------------------------------------------------------------------------------------------------------------- détail
def detail(desk, acteur, ident, prefix=''):
    a = rl._acteur(acteur)
    kind = kind_de(ident)
    if kind not in NOUVEAUX or not rl.IDENT.fullmatch(str(ident or '')):
        raise Refus(404, 'introuvable')
    ligne = _ligne(desk, a, ident, kind)
    return {'gmail': _detail_gmail, 'document': _detail_document, 'note_ia': _detail_note}[kind](desk, a, ligne, prefix)


def _detail_gmail(desk, a, ligne, prefix):
    gm, p = _gmail_courant(desk, a, ligne)
    etat = _etat_gmail(p, ligne) or ('ecarte' if p['etat'] == 'abandonne' else 'remplace')
    libelles = rl._libelles(desk)
    peut = rl.peut_deposer(desk, a)
    modifiable = p['etat'] in ('local', 'depose', 'modifie_a_distance')
    deposable = p['etat'] == 'local' and not p.get('perime') and bool(p.get('a'))
    actions = []
    if etat == 'a_relire':
        if peut:
            actions += (['enregistrer', 'retenir'] if modifiable else []) + ['ecarter']
        actions.append('reporter')
        if peut and deposable:
            actions.append('deposer')
    elif etat == 'retenu' and peut and deposable:
        actions.append('deposer')
    motifs = [gm.ETATS.get(p['etat'], '')]
    if p.get('ia_etat'):
        motifs.append(gm.IA_ETATS.get(p['ia_etat'], ''))
    if p['etat'] == 'modifie_a_distance':
        motifs.append('Comparez les deux versions dans la vue Gmail avant tout dépôt.')
    if not p.get('a'):
        motifs.append('Aucun destinataire : complétez-le dans la vue Gmail.')
    compte = ''
    try:
        compte = str(desk.db.execute('SELECT adresse FROM google5697_connexions WHERE id=?', (p['connexion_id'],)).fetchone()[0] or '')
    except (sqlite3.Error, TypeError):
        pass
    fil = gm.fil_de(p['connexion_id'], p['thread_id'])
    return {'id': ligne['id'], 'type': 'gmail', 'type_libelle': rl.TYPES['gmail'], 'revision': revision_gmail(p), 'etat': etat,
            'etat_libelle': rl.LIBELLES_ETAT.get(etat, etat), 'sujet': p['objet'] or '(sans objet)', 'corps': str(p['texte'] or ''),
            'dossier': {'id': p['matter'], 'libelle': _libelle_dossier(libelles, p['matter'])},
            'destinataires': {'a': [x.strip() for x in str(p['a'] or '').split(',') if x.strip()], 'cc': [x.strip() for x in str(p['cc'] or '').split(',') if x.strip()]},
            'expediteur': compte, 'motif': ' · '.join(x for x in motifs if x)[:600], 'actions': actions, 'sections': list(SECTIONS['gmail']),
            'editable': etat == 'a_relire' and 'enregistrer' in actions,
            'libelles': {'deposer': 'Déposer dans les brouillons Gmail (sans envoi)'},
            'liens': {'pleine_page': prefix + '/courriels?' + urlencode({'vue': 'assistante', 'fil': fil})}}


def _apercu(desk, row):
    """Texte de la version exacte (texte conservé, sinon fichier portant exactement l'empreinte de cette version ; jamais une autre version)."""
    from .produire56100 import MOTIFS, _texte_version
    try:
        texte = '\n'.join(str(x) for x in _texte_version(desk, row))
    except Stop as ex:
        code = str(ex).split(':')[0]
        return '', MOTIFS.get(code) or 'Aperçu non disponible pour ce format : ouvrez la version dans la visionneuse.'
    except Exception:   # noqa: BLE001 — stockage injoignable : la relecture reste possible dans la visionneuse
        return '', 'Aperçu momentanément indisponible (stockage injoignable) : ouvrez la version dans la visionneuse.'
    note = 'Aperçu du texte de la version %d ; la mise en page exacte s’obtient dans l’éditeur.' % int(row['version_number'])
    if len(texte) > APERCU_MAX:
        texte, note = texte[:APERCU_MAX], note + ' Début du texte seulement.'
    return texte, note


def _detail_document(desk, a, ligne, prefix):
    from .livrables5689 import NATURES
    _liv, row = _document_courant(desk, a, ligne)
    etat = _etat_document(row) or 'remplace'
    texte, note = _apercu(desk, row)
    try:
        from . import livrables_apercu5689 as apercu
        vue = apercu.vue(desk, row, a.compte, a.role)
    except Exception:   # noqa: BLE001 — capacités illisibles : ni éditeur ni correction proposés, la lecture reste possible
        vue = None
    actions = []
    if etat == 'a_relire':
        if _peut_valider(desk, a):
            actions += ['valider', 'ecarter']
        if vue and vue['actions']['ai_edit']:
            actions.append('corriger')
        actions.append('reporter')
    doc, numero = row['document_id'], int(row['version_number'])
    liens = {'pleine_page': prefix + '/livrables/' + doc + '?' + urlencode({'version': row['version_id'], 'return_to': prefix + '/courriels?item=' + ligne['id']})}
    ouvrir = None
    if vue and vue['actions']['editor']:
        from .fiche460 import doc_href
        liens['editeur'] = prefix + doc_href(row['path'], row['matter_id'])
        ouvrir = {'href': liens['editeur'], 'libelle': 'Ouvrir dans l’éditeur'}
    libelles = rl._libelles(desk)
    meta = ['%s, version %d (empreinte %s)' % (NATURES.get(row['kind'], 'Document'), numero, str(row['sha256'] or '')[:12] or 'non vérifiée'), note]
    return {'id': ligne['id'], 'type': 'document', 'type_libelle': rl.TYPES['document'], 'revision': revision_document(row), 'etat': etat,
            'etat_libelle': rl.LIBELLES_ETAT.get(etat, etat), 'sujet': row['title'] or 'Document', 'corps': texte,
            'dossier': {'id': row['matter_id'], 'libelle': _libelle_dossier(libelles, row['matter_id'])}, 'destinataires': {'a': [], 'cc': []},
            'motif': ' · '.join(x for x in meta if x)[:600], 'actions': actions, 'sections': list(SECTIONS['document']), 'editable': False,
            'version': {'numero': numero, 'id': row['version_id'], 'empreinte': str(row['sha256'] or '')[:16]},
            'onglets': [['projet', 'Aperçu'], ['propositions', 'Propositions de l’IA']], 'libelle_texte': 'Aperçu de la version %d (lecture seule)' % numero,
            'libelles': {'valider': 'Valider la version %d' % numero, 'ecarter': 'Écarter'}, 'ouvrir': ouvrir, 'liens': liens}


def _detail_note(desk, a, ligne, prefix):
    dl, v = _note_courante(desk, a, ligne)
    etat = {'a_completer': 'a_relire'}.get(v['etat'], v['etat'])
    actions = []
    if etat == 'a_relire':
        if a.role in dl.VALIDEURS and _peut_valider(desk, a):
            actions += ['valider', 'rejeter']
        actions += ['enregistrer', 'reporter']
    libelles = rl._libelles(desk)
    onglet = prefix + '/contentieux?' + urlencode({'dossier': v['dossier'], 'onglet': 'ecritures'}) + '#delta-' + v['id']
    meta = ['Partie : %s' % v['partie'], 'pièce version %s' % v['version'], 'à compléter' if v['etat'] == 'a_completer' else '',
            '%d proposition(s) arguments–preuves, non appliquée(s) : à décider dans l’onglet du dossier' % len(v['propositions']) if v['propositions'] else '',
            v['avertissement']]
    return {'id': ligne['id'], 'type': 'note_ia', 'type_libelle': rl.TYPES['note_ia'], 'revision': 'n%d' % int(v['revision']), 'etat': etat,
            'etat_libelle': rl.LIBELLES_ETAT.get(etat, v['etat_libelle']), 'sujet': 'Nouvelles écritures : ' + str(v['titre'] or ''), 'corps': v['note'],
            'dossier': {'id': v['dossier'], 'libelle': _libelle_dossier(libelles, v['dossier'])}, 'destinataires': {'a': [], 'cc': []},
            'motif': ' · '.join(x for x in meta if x)[:600], 'actions': actions, 'sections': list(SECTIONS['note_ia']),
            'editable': etat == 'a_relire', 'onglets': [['projet', 'Note'], ['sources', 'Sources']],
            'libelle_texte': 'Note de l’IA (rien n’est appliqué au dossier sans décision)', 'libelles': {'valider': 'Valider la note', 'enregistrer': 'Enregistrer la note'},
            'ouvrir': {'href': onglet, 'libelle': 'Ouvrir l’onglet du dossier'}, 'liens': {'pleine_page': onglet}}


# ------------------------------------------------------------------------------------------------------------- sections
def section(desk, acteur, ident, revision, sous):
    a = rl._acteur(acteur)
    kind = kind_de(ident)
    if kind not in NOUVEAUX or sous not in SECTIONS[kind] or not rl.IDENT.fullmatch(str(ident or '')):
        raise Refus(404, 'introuvable')
    if not rl.REVISION.fullmatch(str(revision or '')):
        raise Refus(422, 'version_requise')
    ligne = _ligne(desk, a, ident, kind)
    if kind == 'gmail':
        _gm, p = _gmail_courant(desk, a, ligne)
        if revision_gmail(p) != revision:
            raise Refus(409, 'version_perimee')
        return _section_gmail(desk, ident, revision, sous, p)
    if kind == 'document':
        _liv, row = _document_courant(desk, a, ligne)
        if revision_document(row) != revision:
            raise Refus(409, 'version_perimee')
        return _section_propositions(desk, a, ident, revision, row)
    _dl, v = _note_courante(desk, a, ligne)
    if 'n%d' % int(v['revision']) != revision:
        raise Refus(409, 'version_perimee')
    sources = []
    for pt in v['points']:
        for x in pt.get('passages') or []:
            cote = 'Nouvelle pièce' if x.get('cote') == 'nouvelle' else 'Base'
            sources.append({'id': str(x.get('ancre') or '')[:80], 'type': str(pt.get('type_libelle') or '')[:40],
                            'titre': ('%s %s : « %s »' % (cote, x.get('emplacement') or '', str(x.get('extrait') or '')[:240].replace('\n', ' ')))[:400]})
    limites = [str(q)[:400] for q in v['questions']][:20] + ['Date relevée, à vérifier : %s' % d.get('date', '') for d in v['dates'] if isinstance(d, dict)][:10]
    if v['propositions']:
        limites.append('%d proposition(s) arguments–preuves non appliquée(s) : à décider dans l’onglet du dossier.' % len(v['propositions']))
    return {'id': ident, 'revision': revision, 'section': 'sources', 'sources': sources[:60], 'limites': limites}


def _section_gmail(desk, ident, revision, sous, p):
    tete = {'id': ident, 'revision': revision, 'section': sous}
    if sous == 'sources':
        try:
            limites = [str(x)[:400] for x in json.loads(p.get('ia_limites') or '[]')][:20]
        except (TypeError, ValueError):
            limites = []
        return {**tete, 'sources': [], 'limites': limites}
    m = desk.db.execute('SELECT objet,de,recu,texte,tronque,pieces_json FROM gmail5697_messages WHERE connexion_id=? AND gmail_id=? AND supprime=0',
                        (p['connexion_id'], p['source_id'])).fetchone()
    if not m:
        return {**tete, 'indisponible': True, 'raison': 'Le message d’origine n’est plus dans la copie locale de Gmail.'}
    if sous == 'pieces':
        try:
            pieces = json.loads(m['pieces_json'] or '[]')
        except (TypeError, ValueError):
            pieces = []
        return {**tete, 'pieces': [{'nom': str(x.get('nom') or x.get('filename') or 'pièce')[:200], 'type': str(x.get('type') or x.get('mime') or '')[:80],
                                    'taille': int(x.get('taille') or x.get('size') or 0)} for x in pieces if isinstance(x, dict)][:50]}
    return {**tete, 'objet': str(m['objet'] or ''), 'expediteur': str(m['de'] or ''), 'recu_le': str(m['recu'] or ''), 'texte': str(m['texte'] or '')[:40000],
            'tronque': bool(m['tronque'])}


def _propositions(desk, document_id, limite=10):
    """Propositions du document, lues telles quelles (aucun rapprochement écrit pendant une lecture)."""
    if not _table(desk, 'document_patches5689'):
        return []
    return [dict(r) for r in desk.db.execute('SELECT * FROM document_patches5689 WHERE document_id=? ORDER BY created DESC LIMIT ?',
                                             (str(document_id), int(limite))).fetchall()]


def _section_propositions(desk, a, ident, revision, row):
    from . import propositions5689 as patches
    decideur = a.role in ('administrateur', 'avocat')
    out = []
    for p in _propositions(desk, row['document_id']):
        try:
            ops = json.loads(p['operations'] or '[]')
        except (TypeError, ValueError):
            ops = []
        courante = p['base_version_id'] == row['version_id']
        etat = p['state']
        actions = ['accepter', 'refuser'] if decideur and courante and etat in ('proposee', 'conflit') else []
        note = ''
        if etat == 'conflit':
            note = TEXTES['conflit_edition_concurrente']
        elif not courante and etat in ('proposee', 'demandee'):
            note = 'Proposition établie sur une version précédente : elle ne s’applique pas à la version affichée.'
        elif etat == 'demandee':
            note = 'Proposition en préparation par l’IA (elle attend la reprise si l’IA est en pause).'
        out.append({'id': p['id'], 'etat': etat, 'titre': ('Proposition de l’IA' if p['origin'] == 'ia' else 'Proposition') + ' — ' + patches.STATES.get(etat, etat),
                    'instruction': str(p.get('instruction') or '')[:2000], 'motif': str(p.get('motif') or '')[:1000],
                    'comparaison': [{'avant': c['avant'], 'apres': c['apres'], 'motif': c['motif']} for c in patches.comparaison(ops)][:40],
                    'actions': actions, 'note': note})
    return {'id': ident, 'revision': revision, 'section': 'propositions', 'propositions': out,
            'note': 'Comparaison de texte établie par AxiorHub ; rien n’est appliqué sans votre décision.'}


# ------------------------------------------------------------------------------------------------------------- commandes
def _valider(kind, sous, data, maintenant):
    if sous not in ECRITURES[kind]:
        raise Refus(422, 'commande_invalide')
    action = data.get('action') if sous == 'decision' else sous
    if sous == 'decision' and action not in DECISIONS[kind]:
        raise Refus(422, 'commande_invalide')
    revision = data.get('revision')
    if not isinstance(revision, str) or not rl.REVISION.fullmatch(revision):
        raise Refus(422, 'version_requise')
    cle = data.get('cle')
    if not isinstance(cle, str) or not rl.CLE_IDEMPOTENCE.fullmatch(cle):
        raise Refus(422, 'cle_invalide')
    v = {'action': action, 'revision': revision, 'cle': cle, 'corps': None, 'jusqu_a': None, 'motif': None, 'instruction': None, 'zone': None,
         'proposition': None, 'decision': None}
    corps = data.get('corps')
    if action in ('enregistrer',) or (kind == 'gmail' and action in ('retenir', 'deposer') and corps is not None):
        if not isinstance(corps, str) or not corps.strip() or '\x00' in corps or len(corps) > rl.MAX_CORPS_IMAP:
            raise Refus(422, 'texte_invalide')
        v['corps'] = corps.replace('\r\n', '\n').replace('\r', '\n')
        if kind == 'note_ia' and not MOTIF_MIN <= len(v['corps'].strip()) <= NOTE_MAX:
            raise Refus(422, 'note_invalide')
    elif corps is not None:
        raise Refus(422, 'saisie_non_enregistree')
    jusqu_a = data.get('jusqu_a')
    if action == 'reporter':
        if type(jusqu_a) is not int or not maintenant < jusqu_a <= maintenant + rl.REPORT_MAX:
            raise Refus(422, 'report_invalide')
        v['jusqu_a'] = jusqu_a
    elif jusqu_a is not None:
        raise Refus(422, 'report_invalide')
    if action == 'rejeter':
        motif = data.get('motif')
        if not isinstance(motif, str) or not MOTIF_MIN <= len(motif.strip()) <= MOTIF_MAX:
            raise Refus(422, 'motif_requis')
        v['motif'] = motif.strip()
    if action == 'corriger':
        instruction = re.sub(r'\s+', ' ', str(data.get('instruction') or '')).strip()
        if not INSTRUCTION_MIN <= len(instruction) <= INSTRUCTION_MAX:
            raise Refus(422, 'instruction_invalide')
        zone = str(data.get('zone') or '')
        if len(zone) > ZONE_MAX or '\x00' in zone:
            raise Refus(422, 'instruction_invalide')
        v['instruction'], v['zone'] = instruction, zone
    if action == 'proposition':
        if not PROPOSITION.fullmatch(str(data.get('proposition') or '')) or data.get('decision') not in ('accepter', 'refuser'):
            raise Refus(422, 'commande_invalide')
        v['proposition'], v['decision'] = str(data['proposition']), data['decision']
    return v


def commande(desk, acteur, ident, sous, data, maintenant=None):
    """``POST …/{id}/decision|enregistrer|deposer|corriger|proposition`` des nouvelles sources : décision confirmée ou refus sans effet."""
    a = rl._acteur(acteur)
    maintenant = int(rl._maintenant() if maintenant is None else maintenant)
    kind = kind_de(ident)
    if kind not in NOUVEAUX or not rl.IDENT.fullmatch(str(ident or '')) or not isinstance(data, dict):
        raise Refus(404, 'introuvable')
    v = _valider(kind, sous, data, maintenant)
    sig = rl.empreinte((ident, sous, v['action'], v['revision'], v['corps'], v['jusqu_a'], v['motif'], v['instruction'], v['zone'], v['proposition'], v['decision']))
    ligne = _ligne(desk, a, ident, kind)          # accès revérifié au moment d'agir, et avant de rendre un ancien reçu
    recu = rl._recu_anterieur(desk, a, v['cle'], sig)
    if recu is not None:
        return recu
    if kind == 'gmail':
        return _commande_gmail(desk, a, ligne, v, sig, maintenant)
    if kind == 'document':
        return _commande_document(desk, a, ligne, v, sig, maintenant)
    return _commande_note(desk, a, ligne, v, sig, maintenant)


def _conclure(desk, a, ligne, v, sig, maintenant, recu, projeter_ref=None, etat_force=None, trace=None):
    """Projection, trace et reçu dans UNE transaction, après l'écriture confirmée du producteur."""
    db = desk.db
    db.commit()
    db.execute('BEGIN IMMEDIATE')
    try:
        if projeter_ref is not None:
            PROJETEURS[ligne['kind']](desk, projeter_ref, commit=False)
        if etat_force is not None:
            db.execute('UPDATE file56100_items SET etat=?,revision=? WHERE cabinet=? AND id=?', (etat_force, recu['revision'], rl.CABINET, ligne['id']))
        if trace is not None:
            rl._tracer(desk, a, ligne['id'], recu['revision'], v['action'], trace, maintenant)
        rl._noter_recu(desk, a, v['cle'], sig, recu, maintenant)
        db.commit()
    except BaseException:
        db.rollback()
        raise
    if recu.get('etat') not in ('reporte', 'enregistre'):
        desk.audit('relecture56101_' + str(v['action']), {'id': ligne['id'], 'type': ligne['kind'], 'etat': recu.get('etat'), 'par': a.compte[:80]})
    return recu


def _reporter(desk, a, ligne, v, sig, maintenant, revision_courante):
    if revision_courante != v['revision']:
        raise Refus(409, 'version_perimee')
    db = desk.db
    db.commit()
    db.execute('BEGIN IMMEDIATE')
    try:
        db.execute('INSERT INTO file56100_reports VALUES(?,?,?,?,?) ON CONFLICT(cabinet,acteur,item) DO UPDATE SET revision=excluded.revision,'
                   'jusqu_a=excluded.jusqu_a', (rl.CABINET, a.compte, ligne['id'], v['revision'], v['jusqu_a']))
        if ligne['revision'] != v['revision']:                     # projection en retard sur le producteur : réalignée
            db.execute('UPDATE file56100_items SET revision=? WHERE cabinet=? AND id=?', (v['revision'], rl.CABINET, ligne['id']))
        recu = {'id': ligne['id'], 'etat': 'reporte', 'revision': v['revision'], 'jusqu_a': v['jusqu_a'],
                'jusqu_a_local': rl._local_jusqu_a(desk, a.compte, v['jusqu_a']), 'message': rl.MESSAGES['reporte']}
        rl._noter_recu(desk, a, v['cle'], sig, recu, maintenant)
        db.commit()
    except BaseException:
        db.rollback()
        raise
    return recu


# ------------------------------------------------------------------------------------------------------------- Gmail : commandes
REFUS_GMAIL = {'projet_absent': (404, 'introuvable'), 'introuvable': (404, 'introuvable'), 'dossier_reserve': (404, 'introuvable'),
               'version_perimee': (409, 'version_perimee'), 'etat_incompatible': (409, 'version_perimee'), 'compte_modifie': (409, 'version_perimee'),
               'brouillon_modifie_a_distance': (409, 'brouillon_modifie_a_distance'), 'depot_a_verifier': (409, 'a_verifier'),
               'role_insuffisant': (403, 'role_insuffisant'), 'geste_reserve_a_la_personne': (403, 'role_insuffisant'),
               'controle_indisponible': (503, 'controle_indisponible'), 'gmail_desactive': (503, 'gmail_indisponible'),
               'gmail_non_connecte': (503, 'gmail_indisponible'), 'google_reconnexion_requise': (503, 'gmail_indisponible'),
               'google_connexion_absente': (503, 'gmail_indisponible'), 'consentement_brouillons_requis': (422, 'consentement_brouillons_requis'),
               'google_capacite_non_accordee': (422, 'consentement_brouillons_requis'), 'destinataire_requis': (422, 'destinataire_requis'),
               'projet_perime': (422, 'projet_perime'), 'adresse_invalide': (422, 'destinataire_requis')}


def _refus_gmail(ex):
    code = str(ex).split(':')[0]
    statut, public = REFUS_GMAIL.get(code, (503 if code.startswith('google_http_5') else 422, 'gmail_indisponible' if code.startswith('google_http') else code))
    return Refus(statut, public)


def _commande_gmail(desk, a, ligne, v, sig, maintenant):
    gm, p = _gmail_courant(desk, a, ligne)
    if v['action'] == 'reporter':
        return _reporter(desk, a, ligne, v, sig, maintenant, revision_gmail(p))
    if not rl.peut_deposer(desk, a):
        raise Refus(403, 'role_insuffisant')
    if _version_gmail(v['revision']) != int(p['version']):
        raise Refus(409, 'version_perimee')
    etat = _etat_gmail(p, ligne)
    if v['action'] == 'deposer':
        if etat not in ('a_relire', 'retenu'):
            raise Refus(409, 'version_perimee')
        return _deposer_gmail(desk, a, ligne, v, sig, maintenant, gm, p)
    if etat != 'a_relire':
        raise Refus(409, 'version_perimee')
    pid = p['id']
    try:
        if v['action'] == 'ecarter':
            gm.abandonner(desk, a.compte, pid, int(p['version']))
            p2 = gm.projet(desk, a.compte, pid)
            recu = {'id': ligne['id'], 'etat': 'ecarte', 'revision': revision_gmail(p2), 'message': MESSAGES['gmail_ecarte']}
            return _conclure(desk, a, ligne, v, sig, maintenant, recu, projeter_ref=pid,
                             trace={'objet': p['objet'], 'a': p['a'], 'courriel': ligne['mail_key'], 'corps': p['texte']})
        p2 = p
        if v['corps'] is not None and v['corps'].strip() != str(p['texte'] or '').strip():
            gm.modifier(desk, a.compte, pid, int(p['version']), {'texte': v['corps']})
            p2 = gm.projet(desk, a.compte, pid)
    except Stop as ex:
        raise _refus_gmail(ex) from None
    if v['action'] == 'enregistrer':
        recu = {'id': ligne['id'], 'etat': 'enregistre', 'revision': revision_gmail(p2), 'corps': str(p2['texte'] or ''), 'message': rl.MESSAGES['enregistre']}
        return _conclure(desk, a, ligne, v, sig, maintenant, recu, projeter_ref=pid)
    recu = {'id': ligne['id'], 'etat': 'retenu', 'revision': revision_gmail(p2), 'message': rl.MESSAGES['retenu']}       # retenir : rien n'est déposé
    return _conclure(desk, a, ligne, v, sig, maintenant, recu, projeter_ref=pid, etat_force='retenu',
                     trace={'corps': p2['texte'], 'a': p2['a'], 'courriel': ligne['mail_key']})


def _deposer_gmail(desk, a, ligne, v, sig, maintenant, gm, p):
    """Déposer dans les brouillons Gmail : intention durable écrite d'abord, dépôt par l'API Gmail hors transaction, « Déposé » seulement après
    relecture du brouillon chez Google ; un doute donne « À vérifier » et bloque un second dépôt. Rien n'est envoyé."""
    db = desk.db
    db.commit()
    db.execute('BEGIN IMMEDIATE')
    try:
        active = rl._intention_active(desk, ligne['id'], 'deposer')
        if active is not None and active['etat'] == 'a_verifier' and p['etat'] not in ('depot_a_verifier', 'depot_en_cours'):
            rl._clore_intention(desk, active['id'], 'echec', {'raison': 'verifie_dans_gmail', 'etat_gmail': p['etat']}, maintenant)
            active = None                         # doute levé dans la vue Gmail (« Vérifier ») : un nouveau dépôt redevient possible
        if active is not None:
            db.commit()
            etat = active['etat']
            return {'id': ligne['id'], 'etat': etat, 'revision': active['revision'], 'message': MESSAGES['gmail_a_verifier'] if etat == 'a_verifier' else rl.MESSAGES['en_cours']}
        intention = 'g-' + secrets.token_hex(12)
        db.execute("INSERT INTO file56100_intentions VALUES(?,?,?,?,?,?,?,?,'en_cours','{}',?,?)",
                   (rl.CABINET, intention, ligne['id'], a.compte, 'deposer', v['revision'], v['cle'], sig, maintenant, maintenant))
        db.commit()
    except BaseException:
        db.rollback()
        raise
    try:
        version = int(p['version'])
        if v['corps'] is not None and v['corps'].strip() != str(p['texte'] or '').strip():
            gm.modifier(desk, a.compte, p['id'], version, {'texte': v['corps']})      # texte relu à l'écran, enregistré avant le dépôt
            version = int(gm.projet(desk, a.compte, p['id'])['version'])
        from .pause5629 import checkpoint
        checkpoint(desk)
        sortie = gm.deposer(desk, a.compte, a.role, p['id'], version, _cle_service(a, v['cle']))
    except Stop as ex:
        _fin_depot_gmail(desk, a, ligne, intention, 'echec', {'raison': str(ex).split(':')[0]}, v, sig, maintenant)
        raise _refus_gmail(ex) from None
    except Exception:   # noqa: BLE001 — réponse perdue : jamais rejoué en aveugle
        _fin_depot_gmail(desk, a, ligne, intention, 'a_verifier', {'raison': 'reponse_perdue'}, v, sig, maintenant)
        return {'id': ligne['id'], 'etat': 'a_verifier', 'revision': v['revision'], 'message': MESSAGES['gmail_a_verifier']}
    resultat = str(sortie.get('resultat') or '')
    etat = 'depose' if resultat == 'depose' and sortie.get('etat') == 'depose' and sortie.get('empreinte_depot') else 'a_verifier'
    return _fin_depot_gmail(desk, a, ligne, intention, etat, {'draft': bool(sortie.get('draft')), 'resultat': resultat}, v, sig, maintenant, p=p)


def _fin_depot_gmail(desk, a, ligne, intention, etat, preuve, v, sig, maintenant, p=None):
    db = desk.db
    try:
        db.rollback()
    except sqlite3.Error:
        pass
    db.execute('BEGIN IMMEDIATE')
    try:
        rl._clore_intention(desk, intention, 'fait' if etat == 'depose' else etat, preuve, maintenant)
        recu = {'id': ligne['id'], 'etat': etat, 'revision': v['revision'],
                'message': MESSAGES['gmail_depose'] if etat == 'depose' else (MESSAGES['gmail_a_verifier'] if etat == 'a_verifier' else '')}
        projeter_gmail(desk, _ref(ligne), commit=False)
        if etat == 'depose':
            rl._tracer(desk, a, ligne['id'], v['revision'], 'deposer', {'corps': (p or {}).get('texte', ''), 'courriel': ligne['mail_key'], 'preuve': preuve}, maintenant)
        if etat in ('depose', 'a_verifier'):
            rl._noter_recu(desk, a, v['cle'], sig, recu, maintenant)       # même clé : même réponse, aucun nouveau dépôt
        db.commit()
    except BaseException:
        db.rollback()
        raise
    desk.audit('relecture56101_depot_gmail', {'id': ligne['id'], 'etat': etat, 'par': a.compte[:80]})
    return recu


# ------------------------------------------------------------------------------------------------------------- documents : commandes
def _refus_document(ex):
    code = str(ex).split(':')[0]
    if code in ('livrable_absent', 'version_inconnue', 'dossier_reserve', 'introuvable'):
        return Refus(404, 'introuvable')
    if code in ('version_perimee', 'version_plus_disponible', 'cle_idempotence_deja_utilisee', 'operation_encore_active'):
        return Refus(409, 'version_perimee')
    if code in ('conflit_edition_concurrente', 'version_nextcloud_modifiee'):
        return Refus(409, 'conflit_edition_concurrente')
    if code == 'proposition_deja_traitee':
        return Refus(409, 'proposition_deja_traitee')
    if code in ('role_insuffisant', 'validation_avocat_requise', 'compte_desactive'):
        return Refus(403, 'role_insuffisant')
    if code in ('file_attente_pleine', 'controle_indisponible'):
        return Refus(503, code)
    if code in ('original_protege', 'format_non_modifiable', 'parcours_dedie_facturation', 'fichier_pas_encore_cree'):
        return Refus(422, 'correction_impossible')
    return Refus(422, code)


def _commande_document(desk, a, ligne, v, sig, maintenant):
    liv, row = _document_courant(desk, a, ligne)
    revision = revision_document(row)
    if v['action'] == 'reporter':
        return _reporter(desk, a, ligne, v, sig, maintenant, revision)
    action = v['action']
    if action in ('valider', 'ecarter') and not _peut_valider(desk, a):
        raise Refus(403, 'role_insuffisant')
    if action == 'proposition' and a.role not in ('administrateur', 'avocat'):
        raise Refus(403, 'role_insuffisant')
    if revision != v['revision']:
        raise Refus(409, 'version_perimee')
    if _etat_document(row) != 'a_relire':
        raise Refus(409, 'version_perimee')
    doc = row['document_id']
    if action in ('valider', 'ecarter'):
        try:
            liv.relire(desk, doc, row['version_id'], 'valide' if action == 'valider' else 'ecarte', a.compte, a.role)
        except Stop as ex:
            raise _refus_document(ex) from None
        recu = {'id': ligne['id'], 'etat': 'retenu' if action == 'valider' else 'ecarte', 'revision': revision,
                'message': MESSAGES['valide'] if action == 'valider' else MESSAGES['document_ecarte']}
        return _conclure(desk, a, ligne, v, sig, maintenant, recu, projeter_ref=doc,
                         trace={'document': doc, 'version': row['version_id'], 'numero': row['version_number']})
    from . import propositions5689 as patches
    if action == 'corriger':
        try:
            from . import livrables_apercu5689 as apercu
            if not apercu.vue(desk, row, a.compte, a.role)['actions']['ai_edit']:
                raise Refus(422, 'correction_impossible')
            out = patches.demander_ia(desk, row, {'cle': v['cle'], 'version_id': row['version_id'], 'sha256': row['sha256'],
                                                  'instruction': v['instruction'], 'selection': v['zone']}, a.compte, a.role)
        except Stop as ex:
            raise _refus_document(ex) from None
        recu = {'id': ligne['id'], 'etat': 'correction_demandee', 'revision': revision, 'proposition': str(out.get('id') or ''),
                'message': MESSAGES['correction'] + ((' ' + str(out['message'])) if out.get('message') and out.get('state') != 'demandee' else '')}
        return _conclure(desk, a, ligne, v, sig, maintenant, recu)
    patch = next((p for p in _propositions(desk, doc, 200) if p['id'] == v['proposition']), None)
    if patch is None:
        raise Refus(404, 'introuvable')
    if patch['base_version_id'] != row['version_id']:
        raise Refus(409, 'version_perimee')
    try:
        out = patches.decider(desk, patch['id'], v['decision'], a.compte, a.role, version_id=row['version_id'], sha256=row['sha256'])
    except Stop as ex:
        projeter_apres_ecriture(desk, 'document', doc)       # une édition humaine constatée devient une version : la liste la reflète
        raise _refus_document(ex) from None
    etat = {'appliquee': 'appliquee', 'refusee': 'refusee', 'editeur': 'editeur'}.get(out.get('state'), str(out.get('state') or ''))
    recu = {'id': ligne['id'], 'etat': etat, 'proposition': patch['id'], 'message': MESSAGES.get(etat, '')}
    if etat == 'appliquee':
        nouvelle = _courant_document(desk, doc)
        recu['revision'] = revision_document(nouvelle) if nouvelle else revision
        recu['version'] = int(nouvelle['version_number']) if nouvelle else int(row['version_number'])
    else:
        recu['revision'] = revision
    return _conclure(desk, a, ligne, v, sig, maintenant, recu, projeter_ref=doc,
                     trace={'document': doc, 'proposition': patch['id'], 'decision': v['decision'], 'version_de_base': row['version_id']})


# ------------------------------------------------------------------------------------------------------------- notes : commandes
def _refus_note(ex):
    code = str(ex).split(':')[0]
    if code in ('introuvable', 'dossier_reserve', 'dossier_absent'):
        return Refus(404, 'introuvable')
    if code in ('version_en_conflit', 'version_requise', 'cle_idempotence_deja_utilisee'):
        return Refus(409, 'version_perimee')
    if code == 'analyse_remplacee':
        return Refus(409, 'analyse_remplacee')
    if code in ('role_insuffisant', 'acteur_requis'):
        return Refus(403, 'role_insuffisant')
    if code in ('controle_acces_indisponible', 'controle_indisponible'):
        return Refus(503, 'controle_indisponible')
    return Refus(422, code)


def _commande_note(desk, a, ligne, v, sig, maintenant):
    dl, vue = _note_courante(desk, a, ligne)
    revision = 'n%d' % int(vue['revision'])
    if v['action'] == 'reporter':
        return _reporter(desk, a, ligne, v, sig, maintenant, revision)
    if v['action'] in ('valider', 'rejeter') and (a.role not in dl.VALIDEURS or not _peut_valider(desk, a)):
        raise Refus(403, 'role_insuffisant')
    if revision != v['revision']:
        raise Refus(409, 'version_perimee')
    if vue['etat'] not in ('a_relire', 'a_completer'):
        raise Refus(409, 'version_perimee')
    action = {'valider': 'valider', 'rejeter': 'ecarter', 'enregistrer': 'modifier'}[v['action']]
    data = {'action': action, 'cle': _cle_service(a, v['cle'], 'r101:'), 'revision': int(vue['revision'])}
    if action == 'ecarter':
        data['motif'] = v['motif']
    if action == 'modifier':
        data['note'] = v['corps']
    try:
        out = dl.decider(desk, (a.compte, a.role), vue['id'], data)
    except Stop as ex:
        raise _refus_note(ex) from None
    nouvelle = 'n%d' % int(out.get('revision') or int(vue['revision']) + 1)
    if action == 'modifier':
        recu = {'id': ligne['id'], 'etat': 'enregistre', 'revision': nouvelle, 'corps': v['corps'].strip(), 'message': MESSAGES['note_enregistree']}
        return _conclure(desk, a, ligne, v, sig, maintenant, recu, projeter_ref=vue['id'])
    recu = {'id': ligne['id'], 'etat': 'retenu' if action == 'valider' else 'ecarte', 'revision': nouvelle,
            'message': MESSAGES['note_validee'] if action == 'valider' else MESSAGES['note_rejetee']}
    return _conclure(desk, a, ligne, v, sig, maintenant, recu, projeter_ref=vue['id'],
                     trace={'note': vue['id'], 'dossier': vue['dossier'], 'motif': v['motif'] or ''})
