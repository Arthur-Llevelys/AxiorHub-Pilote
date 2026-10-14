"""5.6.34 (B15, O01) : erreurs qualifiées au point de leur origine.

Une erreur garde son code public (compatibilité : ``str(erreur) == 'http_404'``) mais porte un détail structuré : service, opération,
étape, statut distant, possibilité de reprise, identifiant de corrélation et ressource ANONYMISÉE (jamais d'URL signée, de mot de
passe, de jeton ni de contenu de document). Le message montré à l'avocat dépend de la cause réelle, pas du seul numéro 404.
"""
from contextlib import contextmanager
import hashlib
import re
import uuid
from urllib.parse import urlsplit

from .common import Stop

STAGES = {'inventaire': 'inventaire des pièces', 'extraction': 'extraction du texte', 'lecture_fichier': 'lecture d’un fichier',
          'releves': 'relevés des pièces', 'modele_show': 'initialisation du modèle (/api/show)', 'generation': 'génération (/api/chat)',
          'enregistrement': 'enregistrement du résultat', 'publication': 'publication du résultat'}
SERVICES = {'nextcloud': 'Nextcloud (WebDAV)', 'stockage_local': 'dossier de travail local', 'ollama': 'moteur Ollama', 'index': 'index des pièces',
            'base': 'base locale', 'fournisseur_ia': 'fournisseur d’IA externe'}
RETRYABLE = {'delai_http_depasse', 'connexion_http_indisponible', 'http_429', 'http_502', 'http_503', 'http_504', 'moteur_ollama_injoignable',
             'generation_ia_delai_depasse', 'fournisseur_ia_injoignable', 'index_documents_verrouille', 'contexte_ia_insuffisant'}


class Qualified(Stop):
    """Stop dont ``str()`` reste le code public, avec un détail structuré et sans secret."""

    def __init__(self, code, **detail):
        super().__init__(code)
        self.detail = {k: v for k, v in detail.items() if v not in (None, '')}


def anonymize(resource):
    """Ressource anonymisée : schéma et hôte, deux premiers segments, puis une empreinte courte du reste (aucun nom de client)."""
    raw = str(resource or '')
    if not raw:
        return ''
    p = urlsplit(raw)
    path = p.path if p.scheme else raw
    segments = [s for s in path.split('/') if s]
    head = '/'.join(segments[:2]) if p.scheme else ''
    tail = hashlib.sha256(path.encode('utf-8')).hexdigest()[:10]
    prefix = (p.scheme + '://' + (p.hostname or '') + (':%d' % p.port if p.port else '')) if p.scheme else ''
    return '%s/%s%s#%s' % (prefix, head, '/…' if len(segments) > 2 or not p.scheme else '', tail)


def http_error(code, method, url):
    """Erreur HTTP structurée (appelée par common.HTTP) : statut, opération, hôte et ressource anonymisée."""
    status = int(code)
    return Qualified('http_%d' % status, service=urlsplit(url).hostname or '', operation=method, remote_status=status,
                     resource=anonymize(url), retryable=('http_%d' % status) in RETRYABLE)


def local_missing(path):
    return Qualified('http_404', service='stockage_local', operation='lecture', remote_status=404, resource=anonymize(path), retryable=False,
                     cause='fichier_local_absent')


def detail_of(error):
    return dict(getattr(error, 'detail', None) or {})


@contextmanager
def stage(name, service=''):
    """Encadre une étape : toute erreur en sort qualifiée (étape, service, corrélation), son code public inchangé."""
    try:
        yield
    except Stop as error:
        if getattr(error, 'detail', {}).get('stage'):
            raise
        detail = detail_of(error)
        detail.setdefault('service', service)
        if detail.get('service') in ('', None):
            detail['service'] = service
        if service == 'stockage_local' or detail.get('service') == 'stockage_local':
            detail['service'] = 'stockage_local'
        detail.update(stage=name, correlation_id=uuid.uuid4().hex[:12], retryable=detail.get('retryable', str(error) in RETRYABLE))
        raise Qualified(str(error), **detail) from None


MESSAGES = {
    'ollama_non_configure': 'Adresse ou modèle Ollama non renseigné : Paramètres › Connexions › Intelligence artificielle.',
    'conflit_de_correction': 'Cette rubrique a été corrigée entre-temps (autre onglet ou autre poste) : rechargez la page ; aucune correction n’a été écrasée.',
    'correction_absente': 'Aucune correction active dans cette rubrique.',
    'demande_annulee': 'Cette demande a été annulée : elle ne repart pas. Créez une nouvelle demande si nécessaire.',
    'mission_annulee': 'Cette mission a été annulée : elle ne repart pas.',
    'annulation_non_prise_en_charge': 'Ce type de décision ne s’annule pas ici : ouvrez son détail.',
    'decision_absente': 'Cette demande est introuvable ou ne vous est pas accessible.',
    'type_decision_inconnu': 'Type de demande inconnu.',
    'document_absent_du_dossier': 'Ce document n’appartient pas au dossier choisi (ou n’est plus indexé) : choisissez-le de nouveau.',
    'document_modifie_depuis_la_selection': 'Le document a changé depuis votre sélection : rechargez la page et choisissez la nouvelle version.',
    'format_document_incorrect': 'Format non accepté pour cette action (DOCX ou PDF selon le cas).',
    'nos_ecritures_requises': 'Choisissez nos dernières écritures pour préparer la plaidoirie.',
    'aucune_source_juridique_active': 'Aucune source juridique active : configurez et testez une source (Paramètres › Sources juridiques).',
    'fichier_ics_invalide': 'Ce fichier n’est pas un agenda iCalendar (.ics).',
    'fichier_ics_trop_volumineux': 'Agenda trop volumineux (8 Mo au plus).',
    'adresse_ics_https_requise': 'Indiquez une adresse iCal commençant par https:// (ou webcal://).',
    'evenement_deja_present': 'Cet événement figure déjà dans l’agenda.',
    'evenement_absent': 'Événement introuvable.',
    'evenement_annule': 'Cet événement a été annulé.',
    'rappel_invalide': 'Rappels possibles : à l’heure, 10 min, 30 min, 1 h, 2 h, 1 jour, 2 jours, 1 semaine (quatre au plus).',
    'type_evenement_invalide': 'Type d’événement inconnu.',
    'creneau_evenement_invalide': 'Date, heure ou durée invalide (5 minutes à 24 heures).',
    'titre_evenement_invalide': 'Indiquez un intitulé (2 à 200 caractères).',
    'sauvegarde_invalide': 'Sauvegarde inconnue.',
    'sauvegarde_absente': 'Sauvegarde introuvable.',
    'profil_modele_invalide': 'Profil inconnu : Juridique ou Personnalisé.',
    'mode_contexte_invalide': 'Fenêtre de contexte : automatique ou fixée.',
    'reglage_nombre_invalide': 'Valeur numérique invalide (la virgule décimale est acceptée).',
    'aucune_action_a_annuler': 'Aucune action à annuler sur cette alerte.',
    'rapprochement_invalide': 'Rapprochement invalide.',
    'index_documents_indisponible': 'Incident de l’index des pièces (base locale indisponible) : ce n’est pas un dossier vide.',
    'piece_absente_de_l_index': 'Cette pièce n’est plus dans l’index du dossier.',
    'integration_desactivee': 'Liaison Nextcloud désactivée sur ce moteur.',
    'moteur_poste_non_pris_en_charge': 'AxiorHub installé sur un poste de travail : utilisable seulement en mode découverte, depuis l’ordinateur où il est installé (Nextcloud sur ce même ordinateur, ou lien « Mon AxiorHub local »). Pour le cabinet, installez la version serveur (Linux, HTTPS).',   # 5.6.41
    'dossier_hors_perimetre': 'Ce dossier n’entre pas dans votre périmètre.',
    'operation_annulee': 'Travail arrêté à votre demande ; les fichiers déjà enregistrés et vérifiés sont conservés.',   # 5.6.43
    'ia_en_pause': 'L’IA est en pause : ce travail reprendra quand vous cliquerez sur « IA en pause — reprendre ».',   # 5.6.43
    'automatismes_en_pause': 'L’IA est en pause : aucun travail automatique ne démarre avant la reprise.',   # 5.6.43
    'controle_pause_indisponible': 'L’état de pause n’a pas pu être vérifié : le travail est suspendu par précaution.',   # 5.6.43
    'origine_travail_invalide': 'Origine du travail invalide.',   # 5.6.43
    'reponse_a_soi_meme_refusee': 'Aucun brouillon adressé au cabinet lui-même : vérifiez les destinataires du message.',   # 5.6.43
    'destinataire_absent': 'Aucun destinataire extérieur pour ce brouillon.',   # 5.6.43
    'action_de_classement_a_choisir': 'Choisissez au moins une action : enregistrer le courriel ou ses pièces jointes.',   # 5.6.43
    'aucune_piece_jointe_a_enregistrer': 'Aucune pièce jointe à enregistrer pour ce courriel.',   # 5.6.43
    'piece_jointe_inconnue': 'Cette pièce jointe ne fait pas partie du courriel lu : rechargez le message.',   # 5.6.43
    'proposition_absente': 'Préparation introuvable : relancez « Enregistrer… maintenant ».',   # 5.6.43
    'proposition_obsolete': 'Le classement a changé depuis l’affichage : rechargez le message ; vos choix restent à refaire.',   # 5.6.43
    'confirmation_invalide': 'Confirmation de classement invalide.',   # 5.6.43
    'proposition_autre_utilisateur': 'Cette préparation appartient à un autre utilisateur.',   # 5.6.43
    'courriel_source_deplace_ou_absent': 'Le courriel a été déplacé ou supprimé de la messagerie depuis la préparation : rouvrez-le à son nouvel emplacement.',   # 5.6.43
    'courriel_source_modifie_depuis_la_preparation': 'Le courriel lu n’a plus les mêmes octets que lors de la préparation : rien n’a été enregistré.',   # 5.6.43
    'uidvalidity_modifiee': 'La boîte de messagerie a été renumérotée par le serveur : rouvrez le courriel avant de le classer.',   # 5.6.43
    'uidvalidity_requise': 'Identifiant de boîte manquant : rouvrez le courriel.',   # 5.6.43
    'message_trop_volumineux_pour_conservation': 'Courriel trop volumineux pour la conservation (limite de la configuration « archive_max_message_bytes »).',   # 5.6.43
    'octets_imap_incomplets': 'Le serveur de messagerie n’a pas transmis le message complet : réessayez.',   # 5.6.43
    'taille_imap_absente': 'Le serveur de messagerie n’indique pas la taille du message.',   # 5.6.43
    'archive_trop_volumineuse': 'Fichier trop volumineux pour la conservation.',   # 5.6.43
    'conflit_fichier_existant': 'Un fichier différent existe déjà à cet emplacement : rien n’a été écrasé.',   # 5.6.43
    'empreinte_depot_differente': 'Le fichier relu après dépôt ne correspond pas : enregistrement non validé, réessayez.',   # 5.6.43
    'relecture_depot_impossible': 'Le dépôt n’a pas pu être relu : son état est incertain ; il sera vérifié avant toute nouvelle copie.',   # 5.6.43
    'original_non_conserve': 'L’original de ce courriel n’est pas (encore) conservé.',   # 5.6.43
    'original_altere': 'L’original conservé ne correspond plus à son empreinte : vérifiez le fichier dans le dossier.',   # 5.6.43
    'classement_absent': 'Classement introuvable.',   # 5.6.43
    'classement_modifie_entre_temps': 'Ce classement a été modifié ailleurs : rechargez la page.',   # 5.6.43
    'annotation_invalide': 'Note vide ou trop longue (20 000 caractères au plus).',   # 5.6.43
    'autorisation_invalide': 'Choix d’autorisation invalide.',   # 5.6.43
    'aucune_piece_enregistree': 'Aucune pièce jointe n’a pu être enregistrée : voir le détail par fichier.',   # 5.6.43
    'source_du_courriel_absente': 'La source de ce courriel n’est plus connue : rouvrez-le depuis la messagerie.',   # 5.6.43
    'reglage_modifie_entre_temps': 'Ce réglage a été modifié ailleurs : rechargez la page.',   # 5.6.43
    'version_invalide': 'Version de réglage invalide.',   # 5.6.43
    'adresse_talk_invalide': 'Adresse de Talk invalide : utilisez l’adresse HTTPS de l’application Talk de votre Nextcloud (…/apps/spreed/).',   # 5.6.43
    'adresse_talk_hors_nextcloud': 'L’adresse de Talk doit être sur la même instance Nextcloud que la connexion configurée.',   # 5.6.43
    'parcours_annule': 'Ce parcours est annulé : démarrez-en un nouveau.',   # 5.6.43
    'etape_non_reprenable': 'Seule une étape en erreur ou annulée peut être reprise.',   # 5.6.43
    'evenement_deja_reparti': 'Cet événement a déjà été réparti.',   # 5.6.43
    'decision_agenda_invalide': 'Choix invalide pour cet ancien événement.',   # 5.6.43
    'courriel_invalide': 'Courriel invalide : rouvrez-le depuis la messagerie.',   # 5.6.43
    'instantane_modele_introuvable': 'La trame choisie pour cette rédaction est introuvable : choisissez de nouveau le modèle dans la bibliothèque. La rédaction ne se fera pas sur une autre version.',   # 5.6.42
    'instantane_modele_altere': 'La trame enregistrée pour cette rédaction ne correspond plus à son empreinte : choisissez de nouveau le modèle.',   # 5.6.42
    'modele_word_modifie': 'Le modèle Word a été modifié depuis son enregistrement : rechargez la page, vérifiez les informations et les clauses, puis relancez.',   # 5.6.42
    'modele_modifie_depuis_affichage': 'Le modèle a changé depuis l’affichage de cette page : rechargez-la, vérifiez les options et relancez l’aperçu.',   # 5.6.42
    'modele_word_absent': 'Aucun modèle Word n’est enregistré pour cette trame.',   # 5.6.42
    'mot_de_passe_actuel_incorrect': 'Mot de passe actuel incorrect.',   # 5.6.42
    'mot_de_passe_trop_court': 'Le nouveau mot de passe doit compter 16 caractères au moins.',   # 5.6.42
    'mots_de_passe_differents': 'Les deux saisies du nouveau mot de passe sont différentes.',   # 5.6.42
    'mot_de_passe_inchange': 'Le nouveau mot de passe est identique à l’actuel.',   # 5.6.42
    'identifiant_invalide': 'Identifiant invalide : 3 à 64 caractères (lettres, chiffres, point, tiret, @).',   # 5.6.42
    'comptes_du_cabinet_dans_la_page_comptes': 'Les comptes du cabinet se gèrent dans la page Comptes.',   # 5.6.42
    'compte_interface_absent': 'Compte de l’interface introuvable : relancez l’installation de l’interface.',   # 5.6.42
    # 5.6.35 : contrat /integration/v1 (application Nextcloud)
    'operation_non_signee': 'La requête ne correspond pas à l’opération autorisée par Nextcloud : elle est refusée.',
    'contexte_sans_fuseau': 'Contexte Nextcloud invalide : l’expiration doit comporter un fuseau horaire.',
    'requete_invalide': 'Requête de l’application Nextcloud mal formée : elle est refusée sans effet.',
    'secret_integration_trop_court': 'Le secret partagé avec Nextcloud doit compter au moins 8 caractères (32 aléatoires recommandés).',
    'liste_reservee_administrateur': 'La liste des dossiers du moteur est réservée à l’administrateur Nextcloud.',
    'manifeste_absent_ou_hors_perimetre': 'Sélection de pièces introuvable ou établie pour un autre utilisateur ou un autre dossier.',
    'manifeste_perime': 'Sélection de pièces trop ancienne : choisissez de nouveau les pièces.',
    'aucune_piece_selectionnee_lisible': 'Aucune des pièces choisies n’est lisible dans l’index du dossier : actualisez le dossier ou changez la sélection.',
    'manifeste_non_applicable': 'Une recherche juridique ne porte pas sur une sélection de pièces.',
    'cle_idempotence_deja_utilisee': 'Cette demande porte la clé d’une demande différente : relancez-la depuis l’écran.',
    'version_produite_introuvable': 'La version de fiche produite par ce travail est introuvable.',
    'commentaire_invalide': 'Commentaire trop long.',
    'correction_a_preciser': 'Précisez la correction demandée.',
    'piece_absente_de_l_index_du_dossier': 'Pièce absente de l’index du dossier côté moteur.',
    'reservation_sans_travail': 'La demande n’a pas pu être mise en file : relancez-la.',
    'requete_rejouee': 'Requête déjà reçue (protection contre le rejeu).',
    'contexte_expire': 'Requête expirée : réessayez.',
    'signature_invalide': 'Signature de la requête invalide.',
    'resultat_indisponible': 'Le résultat n’est pas encore disponible.',
    'resultat_modifie_depuis_lecture': 'Le résultat a changé depuis votre lecture : rechargez-le avant d’approuver.',
}


def explain(code, detail=None):
    """Message propre à la cause (étape, service, statut) ; à défaut, le message générique historique."""
    d = detail or {}
    code = str(code or '')
    st, svc = d.get('stage', ''), d.get('service', '')
    where = (' — étape : %s' % STAGES.get(st, st)) if st else ''
    ref = (' (réf. %s)' % d['correlation_id']) if d.get('correlation_id') else ''
    if code == 'modele_ollama_absent':
        return 'Le modèle choisi n’est pas installé sur le moteur Ollama : installez-le ou choisissez un modèle présent (Paramètres › IA). Les dossiers ne sont pas en cause.' + ref
    if code in ('moteur_ollama_injoignable', 'fournisseur_ia_injoignable'):
        return 'Le moteur d’IA ne répond pas : vérifiez qu’Ollama est démarré et que son adresse est correcte (Paramètres › IA › Tester). Aucune pièce n’est perdue ; le travail pourra reprendre.' + ref
    if code == 'contexte_ia_insuffisant':
        return 'Le dossier dépasse la fenêtre du modèle même après compression : le travail se poursuit par étapes (relevés des pièces) ; relancez une fois les relevés terminés.' + ref
    if code in ('index_documents_indisponible', 'index_documents_verrouille'):
        return 'Incident de l’index des pièces (base locale indisponible ou occupée) : ce n’est pas un dossier vide. Réessayez dans quelques minutes ; si l’incident persiste, consultez État du logiciel.' + ref
    if code == 'http_404':
        if svc == 'stockage_local' or d.get('cause') == 'fichier_local_absent':
            return 'Fichier introuvable dans le dossier de travail local : il a été déplacé, renommé ou supprimé%s. Réindexez le dossier.%s' % (where, ref)
        if st in ('modele_show', 'generation') or svc == 'ollama':
            return 'Le moteur d’IA a répondu « introuvable » (404)%s : l’adresse configurée ne correspond pas à un serveur Ollama ou le modèle a été retiré. Vérifiez Paramètres › IA › Tester.%s' % (where, ref)
        if st == 'lecture_fichier':
            return 'Une pièce a été déplacée ou supprimée sur Nextcloud pendant la lecture (404)%s : réindexez le dossier puis relancez.%s' % (where, ref)
        if st == 'inventaire':
            return 'Le dossier du cabinet est introuvable sur Nextcloud (404)%s : il a pu être renommé ou déplacé. Vérifiez son emplacement dans le registre des dossiers ; rien n’a été effacé.%s' % (where, ref)
        if st in ('enregistrement', 'publication'):
            return 'Le dossier de destination est introuvable (404)%s : le résultat n’a pas été publié ; la version précédente reste valable.%s' % (where, ref)
    if code == 'http_403':
        return 'Accès refusé (403)%s : le compte utilisé par le moteur n’a pas le droit nécessaire sur %s.%s' % (where, SERVICES.get(svc, svc or 'ce service'), ref)
    if code == 'http_401':
        return 'Identifiants refusés (401)%s par %s : renouvelez le mot de passe d’application ou le jeton.%s' % (where, SERVICES.get(svc, svc or 'le service'), ref)
    if code in ('delai_http_depasse', 'generation_ia_delai_depasse'):
        return 'Délai dépassé%s : le service n’a pas répondu à temps ; le travail peut être repris.%s' % (where, ref)
    if code.startswith('reglage_hors_plage_'):
        return 'Valeur hors de la plage autorisée pour %s.' % code[len('reglage_hors_plage_'):]
    if code in MESSAGES:
        return MESSAGES[code] + (where + ref if (where or ref) else '')
    from .web440 import human
    base = human(code)
    return base + (where + ref if (where or ref) else '')


def public(detail):
    """Détail affichable (section technique repliée) : aucune donnée confidentielle n'y figure par construction."""
    keep = ('service', 'operation', 'stage', 'remote_status', 'retryable', 'correlation_id', 'resource', 'cause')
    return {k: detail[k] for k in keep if k in detail}


def safe_text(text):
    """Dernier filet : retire d'un texte d'erreur tout ce qui ressemble à un secret (jeton, mot de passe, URL signée)."""
    t = re.sub(r'(?i)(authorization|bearer|token|password|passwd|secret|signature|sig)=?[:\s]*[A-Za-z0-9._~+/=-]{6,}', r'\1=***', str(text or ''))
    return re.sub(r'https?://[^\s]*[?&][^\s]*', lambda m: anonymize(m.group(0)), t)
