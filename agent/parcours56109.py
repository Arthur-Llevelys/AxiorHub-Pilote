"""Plans et contrôles déterministes 5.6.109 ; aucun effet externe dans ce module.

Les services appelants gardent les droits, le stockage et les reçus canoniques.
Ces fonctions ne sont ni un connecteur RPVA ni un moteur de délais légaux.
"""
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
import hashlib
import io
import re
import stat
from pathlib import PurePosixPath
from zipfile import ZipFile, BadZipFile
from .common import Stop


def _requis(value, code):
    if not isinstance(value, str) or not value.strip():
        raise Stop(code)
    return value


def _instant(value):
    if not isinstance(value, datetime) or value.utcoffset() is None:
        raise Stop('instant_ou_fuseau_manquant')
    return value.astimezone(timezone.utc)


@dataclass(frozen=True)
class Offre:
    commande: str
    compte: str
    message_id: str
    client_id: str
    dossier_id: str
    modele_id: str
    modele_version: str
    montant_ht_minor: int
    document: str = 'lettre_mission'
    prestation: str = 'analyse_cgv'
    devise: str = 'EUR'


def plan_offre(offre):
    for key in ('commande','compte','message_id','client_id','dossier_id','modele_id','modele_version'):
        _requis(getattr(offre, key), key + '_manquant')
    if offre.document not in {'devis','lettre_mission'}:
        raise Stop('type_document_a_preciser')
    if offre.prestation != 'analyse_cgv':
        raise Stop('prestation_hors_profil_cgv')
    if type(offre.montant_ht_minor) is not int or offre.montant_ht_minor <= 0:
        raise Stop('montant_ht_invalide')
    if offre.devise != 'EUR':
        raise Stop('devise_hors_profil_cgv')
    return {'commande': offre.commande, 'compte': offre.compte,
            'source_message': offre.message_id, 'client': offre.client_id, 'dossier': offre.dossier_id,
            'type': offre.document, 'modele': offre.modele_id, 'modele_version': offre.modele_version,
            'prestation': offre.prestation, 'forfait_ht_cents': offre.montant_ht_minor,
            'devise': offre.devise, 'tva': 'regime_a_relire_dans_le_registre',
            'actions': ('preparer_document','produire_pdf_interne','preparer_reponse','joindre_pdf','verifier_brouillon'),
            'emettre_facture': False, 'envoyer': False}


@dataclass(frozen=True)
class Message:
    portail: str
    compte: str
    mandant: str
    identifiant: str
    recu: datetime
    non_lu_initial: bool

    @property
    def cle(self):
        return (self.portail, self.compte, self.mandant, self.identifiant)


def selection_messages(messages, maintenant, jours=5, inventaire_complet=False):
    fin = _instant(maintenant)
    if type(jours) is not int or not 1 <= jours <= 90:
        raise Stop('periode_invalide')
    if type(inventaire_complet) is not bool:
        raise Stop('couverture_invalide')
    debut, vus, retenus = fin - timedelta(days=jours), {}, []
    for msg in messages:
        for v in msg.cle:
            _requis(v, 'identite_message_incomplete')
        recu = _instant(msg.recu)
        if type(msg.non_lu_initial) is not bool:
            raise Stop('etat_non_lu_inconnu')
        if msg.cle in vus:
            if vus[msg.cle] != msg:
                raise Stop('message_source_en_conflit')
            continue
        vus[msg.cle] = msg
        if debut <= recu <= fin and msg.non_lu_initial:
            retenus.append(msg)
    return {'debut_utc': debut.isoformat(), 'fin_utc': fin.isoformat(),
            'messages': tuple(retenus), 'couverture': 'complete' if inventaire_complet else 'partielle',
            'aucun_message_etabli': inventaire_complet and not retenus}


def dossier_unique(candidats):
    """Recevoir les correspondances autorisées du registre, pas les noms du LLM.

    Chaque candidat porte dossier, registre_version et correspondance_confirmee.
    Le service résolveur vérifie juridiction, instance, RG/Portalis et mandant.
    """
    ids = {}
    for c in candidats:
        if c.get('correspondance_confirmee') is not True:
            raise Stop('rattachement_a_preciser')
        ident = _requis(c.get('dossier'), 'dossier_manquant')
        version = _requis(c.get('registre_version'), 'version_registre_manquante')
        if ident in ids and ids[ident] != version:
            raise Stop('version_registre_en_conflit')
        ids[ident] = version
    if len(ids) != 1:
        raise Stop('rattachement_a_preciser')
    ident = next(iter(ids))
    return ident, ids[ident]


def famille_document(nature, partie=''):
    """Renvoyer une catégorie ; les chemins viennent du registre du dossier."""
    if nature in {'assignation','conclusions','convocation','jugement','ordonnance','accuse_procedural'}:
        return 'procedure'
    if nature == 'message':
        return 'correspondances'
    if nature == 'piece' and partie == 'cabinet':
        return 'nos_pieces'
    if nature == 'piece' and partie == 'adverse':
        return 'pieces_adverses'
    return 'a_classer'


def lire_archive(raw, controle, max_fichiers=100, max_octets=100*1024*1024, max_ratio=200):
    """Lire sans extraire sur disque ; préserver les noms identiques par ordinal.

    L'adaptateur vérifiera encore types, droits, malware et publication. La
    fonction n'ouvre ni n'exécute les fichiers, et ne traite pas les ZIP imbriqués.
    """
    if (not callable(controle) or type(max_fichiers) is not int or not 1<=max_fichiers<=1000
            or type(max_octets) is not int or not 1<=max_octets<=200*1024*1024
            or type(max_ratio) is not int or not 1<=max_ratio<=500):
        raise Stop('budgets_archive_invalides')
    if not isinstance(raw, bytes) or len(raw) > max_octets:
        raise Stop('archive_trop_grande_ou_invalide')
    controle()
    total, out = 0, []
    try:
        with ZipFile(io.BytesIO(raw)) as z:
            infos = z.infolist()
            if len(infos) > max_fichiers:
                raise Stop('archive_trop_de_fichiers')
            for ordinal, entry in enumerate(infos):
                controle()
                nom = entry.filename
                chemin = PurePosixPath(nom.replace('\\','/'))
                if (not nom or '\x00' in nom or chemin.is_absolute()
                        or '..' in chemin.parts or ':' in nom
                        or stat.S_ISLNK(entry.external_attr >> 16)):
                    raise Stop('archive_chemin_refuse')
                if entry.is_dir():
                    continue
                if entry.flag_bits & 1:
                    raise Stop('archive_chiffree_a_traiter_manuellement')
                if (entry.file_size > max_octets-total
                        or entry.file_size > max(1,entry.compress_size)*max_ratio):
                    raise Stop('archive_decompression_hors_budget')
                with z.open(entry) as f:
                    chunks, lu = [], 0
                    while True:
                        controle()
                        chunk = f.read(min(65536, max_octets-total-lu+1))
                        if not chunk:
                            break
                        lu += len(chunk)
                        if total+lu > max_octets:
                            raise Stop('archive_decompression_hors_budget')
                        chunks.append(chunk)
                body = b''.join(chunks)
                total += len(body)
                out.append({'ordinal': ordinal, 'nom_source': nom,
                            'sha256': hashlib.sha256(body).hexdigest(), 'octets': body})
    except Stop:
        raise
    except (BadZipFile, RuntimeError, NotImplementedError):
        raise Stop('archive_illisible') from None
    controle()
    return tuple(out)


def plan_date(date_iso, nature, source_id, extrait, auteur_validation='',
              regle_version='', depart_source=''):
    """Préparer une inscription, sans calculer un délai ni écrire un agenda."""
    from datetime import date
    try:
        if not isinstance(date_iso,str) or not re.fullmatch(r'\d{4}-\d{2}-\d{2}',date_iso):
            raise ValueError
        date.fromisoformat(date_iso)
    except (ValueError, TypeError):
        raise Stop('date_a_preciser') from None
    _requis(source_id, 'source_date_manquante')
    _requis(extrait, 'extrait_date_manquant')
    if nature not in {'audience','calendrier_impose','delai_calcule'}:
        raise Stop('nature_date_a_preciser')
    if nature == 'delai_calcule' and not all((regle_version, depart_source, auteur_validation)):
        return {'date':date_iso, 'etat':'a_preciser', 'inscriptible':False, 'source':source_id}
    return {'date':date_iso, 'nature':nature, 'source':source_id,
            'etat':'confirmee' if auteur_validation else 'a_verifier', 'inscriptible':True}


def destinataires_information(contacts, adresses_cabinet):
    """Relations dossier déjà filtrées par le service, jamais Répondre à tous."""
    from email.utils import parseaddr
    propres = {str(x).strip().casefold() for x in adresses_cabinet}
    out = {}
    for c in contacts:
        if c.get('role') not in {'client','dominus_litis'}:
            continue
        if c.get('relation_confirmee') is not True or c.get('coordonnees_confirmees') is not True:
            raise Stop('destinataire_a_preciser')
        raw = c.get('email') or ''
        if not isinstance(raw, str) or any(x in raw for x in ('\r','\n',',',';')):
            raise Stop('destinataire_invalide')
        email = parseaddr(raw)[1]
        if email != raw.strip() or not re.fullmatch(r'[^\s<>@]+@[^\s<>@]+\.[^\s<>@]+',email):
            raise Stop('destinataire_invalide')
        if email.casefold() in propres:
            raise Stop('destinataire_identite_cabinet')
        _requis(c.get('id'), 'contact_id_manquant')
        precedent=out.get(email.casefold())
        if precedent and (precedent['contact_id']!=c['id'] or precedent['role']!=c['role']):
            raise Stop('relations_destinataire_en_conflit')
        out.setdefault(email.casefold(), {'contact_id':c['id'],'email':email,'role':c['role']})
    return tuple(out.values())



# ======================================================================================== 5.6.109 (lot G4) : routes m56109/parcours/
# Offre et PDF joint (G10, ``offre56109``), collecte supervisée et routine (G11-G12, ``collecte_portail56109``), documents publics Pappers
# (G17, ``pappers_documents56109``). Lectures sans écriture ; écritures : ``check_post`` (origine, jeton), corps borné, clé d'idempotence,
# objet et dossier relus par le service (absent ou interdit : même 404 neutre), 409 si version périmée, ``Cache-Control: private, no-store``.
PREFIXE = 'm56109/parcours/'
LECTURES = ('offre', 'collecte', 'collecte-profils', 'collecte-dates', 'pappers')
ECRITURES = {'offre': ('administrateur', 'avocat', 'assistant'), 'collecte-profil': ('administrateur',), 'collecte-qualifier': ('administrateur',),
             'collecte-lancer': ('administrateur', 'avocat'), 'collecte-deposer': ('administrateur', 'avocat'), 'collecte-routine': ('administrateur', 'avocat'),
             'collecte-rattacher': ('administrateur', 'avocat'), 'pappers-politique': ('administrateur',), 'pappers-dossier': ('administrateur', 'avocat'),
             'pappers-autoriser': ('administrateur', 'avocat'), 'pappers-collecter': ('administrateur', 'avocat')}
MAX_POST = 16000
STATUTS = {'introuvable': '404 Not Found', 'role_insuffisant': '403 Forbidden', 'origine_ou_csrf_refuse': '403 Forbidden', 'cle_reutilisee': '409 Conflict',
           'version_perimee': '409 Conflict', 'version_en_conflit': '409 Conflict', 'operation_en_cours': '409 Conflict', 'requete_trop_longue': '413 Payload Too Large',
           'controle_acces_indisponible': '503 Service Unavailable'}
MESSAGES = {'introuvable': 'Élément introuvable, ou action non permise.', 'role_insuffisant': 'Votre rôle ne permet pas cette action.',
            'cle_reutilisee': 'Cette demande a déjà servi pour une autre action : rechargez la page.', 'cle_requise': 'Demande incomplète : rechargez la page.',
            'version_perimee': 'L’élément a changé depuis l’affichage : rechargez la page.', 'operation_en_cours': 'Cette action est déjà en cours.',
            'requete_trop_longue': 'Demande trop longue.', 'origine_ou_csrf_refuse': 'Session expirée ou origine refusée : rechargez la page.',
            'controle_acces_indisponible': 'Contrôle des accès indisponible : réessayez dans un instant.',
            'instruction_requise': 'Écrivez l’instruction (expéditeur, jour, prestation, forfait HT).',
            'prestation_hors_profil_cgv': 'Cette préparation automatique couvre l’analyse de conditions générales de vente ; préparez les autres offres depuis Honoraires.',
            'poste_requis': 'La collecte supervisée se lance depuis l’application du poste.',
            'portail_inconnu': 'Portail inconnu.', 'type_compte_non_professionnel': 'Seul un compte professionnel peut être déclaré.'}


def allowed(role, method, api):
    reste = str(api or '').split('?', 1)[0]
    reste = reste[len(PREFIXE):].strip('/') if reste.startswith(PREFIXE) else ''
    if role not in ('administrateur', 'avocat', 'assistant'):
        return False
    if method in ('GET', 'HEAD'):
        return reste in LECTURES
    return method == 'POST' and role in ECRITURES.get(reste, ())


def _json(data, status='200 OK'):
    import json
    return {'status': status, 'kind': 'application/json; charset=utf-8', 'body': json.dumps(data, ensure_ascii=False, default=str),
            'headers': [('Cache-Control', 'private, no-store')]}


def _fond(cfg, mission_id, acteur):
    """Collecte hors de la réponse HTTP (elle attend l'avocat et les téléchargements) ; base ouverte et fermée par le fil."""
    from .desk import Desk
    from . import collecte_portail56109 as C
    desk = Desk(cfg)
    try:
        C.executer(desk, mission_id, acteur)
    except Exception as ex:   # noqa: BLE001 — l'état de la mission porte déjà le motif ; rien n'est relancé ici
        try:
            desk.audit('collecte56109_fond_interrompu', {'mission': mission_id, 'cause': type(ex).__name__})
        except Exception:   # noqa: BLE001
            pass
    finally:
        desk.db.close()


def api(env, desk, auth, prefix, name, args, method):
    import threading
    from .web567 import actor
    from . import collecte_portail56109 as C, offre56109 as O, pappers_documents56109 as PP
    compte, role = actor(env)
    reste = str(name).split('?', 1)[0][len(PREFIXE):].strip('/')
    try:
        if reste not in LECTURES and reste not in ECRITURES:
            raise Stop('introuvable')
        if not allowed(role, method, name):
            raise Stop('role_insuffisant' if method == 'POST' and reste in ECRITURES else 'introuvable')
        if method in ('GET', 'HEAD'):
            if reste == 'offre':
                return _json(O.vue(desk, (compte, role), str(args.get('id') or '')))
            if reste == 'collecte':
                row = C._mission(desk, str(args.get('id') or ''))
                if row['acteur'] not in (compte, 'cabinet') and role != 'administrateur':
                    raise Stop('introuvable')
                return _json(C.vue_mission(desk, row['id']))
            if reste == 'collecte-profils':
                return _json({'profils': C.profils(desk)})
            if reste == 'collecte-dates':
                return _json({'dates': C.propositions_dates(desk, (compte, role), str(args.get('d') or ''))})
            return _json({'documents': PP.documents(desk, (compte, role), str(args.get('d') or '')), 'politique': PP.politique_dossier(desk, str(args.get('d') or ''))})
        try:
            longueur = int(env.get('CONTENT_LENGTH') or 0)
        except ValueError:
            longueur = MAX_POST + 1
        if longueur > MAX_POST:
            raise Stop('requete_trop_longue')
        from .web440 import check_post
        d = check_post(env, auth)
        if reste == 'offre':
            out = O.preparer(desk, (compte, role), d)
            return _json({**out, 'message': out['etat_libelle'] + ('' if not out['questions'] else ' — ' + out['questions'][0]['question'])})
        if reste == 'collecte-profil':
            return _json({**C.enregistrer_profil(desk, compte, role, d), 'message': 'Profil enregistré (à qualifier par un essai réel sur le poste).'})
        if reste == 'collecte-qualifier':
            return _json({**C.qualifier_profil(desk, compte, role, d.get('id'), d.get('version') or 0, d.get('preuve')), 'message': 'Preuve d’essai enregistrée.'})
        if reste == 'collecte-lancer':
            row = C.lancer(desk, compte, role, d)
            if row['etat'] == 'prete':
                threading.Thread(target=_fond, args=(desk.c, row['id'], compte), name='collecte56109', daemon=True).start()
            return _json({**C.vue_mission(desk, row['id']), 'message': C.ETATS_MISSION.get(row['etat'], row['etat'])})
        if reste == 'collecte-deposer':
            out = C.deposer_brouillons(desk, compte, role, d.get('mission') or None, d.get('d') or None)
            return _json({**out, 'message': '%d brouillon(s) déposé(s) et vérifié(s) ; %d à rapprocher. Rien n’est envoyé.' % (out['deposes'], out['a_rapprocher'])})
        if reste == 'collecte-routine':
            return _json(C.creer_routine(desk, compte, role, d))
        if reste == 'collecte-rattacher':
            return _json(C.confirmer_correspondance(desk, compte, role, d.get('portail'), d.get('compte'), d.get('portail_dossier'), str(d.get('d') or '')))
        if reste == 'pappers-politique':
            return _json({**PP.enregistrer_politique(desk, compte, role, d), 'message': 'Politique enregistrée.'})
        if reste == 'pappers-dossier':
            return _json({**PP.restreindre_dossier(desk, (compte, role), str(d.get('d') or ''), d.get('mode'), d.get('credits_max') or ''),
                          'message': 'Règle du dossier enregistrée.'})
        if reste == 'pappers-autoriser':
            try:
                maximum = int(d.get('max_documents') or 0)
            except (TypeError, ValueError):
                maximum = 0
            ident = PP.autoriser(desk, (compte, role), str(d.get('d') or ''), d.get('sirens') or [], d.get('types') or [], maximum, d.get('credits_max') or '0')
            return _json({'autorisation': ident, 'message': 'Autorisation enregistrée.'})
        out = PP.collecter(desk, (compte, role), str(d.get('d') or ''), d.get('cle'))
        return _json({**out, 'message': out.get('etat_libelle', '')})
    except Stop as ex:
        code = str(ex).split(':')[0]
        if code in ('dossier_reserve', 'dossier_absent', 'acteur_requis', 'contact_absent', 'occurrence_absente', 'routine_absente'):
            code = 'introuvable'
        return _json({'error': code, 'message': MESSAGES.get(code, 'Demande refusée.')}, STATUTS.get(code, '422 Unprocessable Entity'))
