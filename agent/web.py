"""Authenticated WSGI cabinet console. Bind loopback and publish only over TLS.

 'ollama_generation_delai_depasse':'Ollama n’a pas fini dans le délai configuré. Vérifiez sa charge et le modèle ; aucune action externe effectuée.',
 'ollama_generation_injoignable':'Le service Ollama est injoignable. Vérifiez son service, son adresse et ses journaux.',
 'delai_http_depasse':'Un service n’a pas répondu dans le délai. Vérifiez la connexion et sa charge.',
        if path=='/dashboard':main+='</details>'
No remote assets or arbitrary file/command endpoints. Every mutation is POST + CSRF
and creates a bounded job executed by the service account, never by root.
"""
from .common import matter_display
import base64
from datetime import datetime, timezone, timedelta
from email.utils import parsedate_to_datetime
import hashlib
import hmac
from html import escape
import json
from pathlib import Path, PurePosixPath
import re
import threading
import time
from urllib.parse import parse_qs, urlencode, urlsplit, unquote

from .common import Stop, load_config, load_matters
from .desk import Desk, ROLES, report_for
from .index import DocumentIndex
from .memory import SentMemory
from .state import State
from .improvements36 import matter_option,matter_label

LABELS = {'review':'À vérifier','drafted':'Brouillon dans la messagerie',
          'ignored':'Ignoré','retry':'À reprendre','error':'Erreur',
          'observed':'Proposition en observation','appending':'Dépôt en cours',
          'append_uncertain':'Dépôt à vérifier', 'pending':'En attente',
          'running':'En cours','cancel_requested':'Annulation demandée',
          'done':'Terminé','cancelled':'Annulé'}
REASONS = {'contact_personnel_hors_dossier': 'Contact personnel : aucun brouillon n’est préparé à partir du dossier.',
           'quota_econome_journalier': 'Quota journalier des analyses automatiques atteint (régime économe) : différée au lendemain.',
           'expediteur_inconnu_sans_dossier': 'Expéditeur inconnu de tout dossier et aucun dossier identifié : à qualifier, aucun brouillon automatique.',
           'facture_deposee_non_verifiee': 'Facture déposée dans Invoice Ninja mais conformité non vérifiée : contrôlez-la avant tout envoi.',
           'invoice_ninja_ecriture_desactivee': 'Écriture Invoice Ninja désactivée : activez-la dans Paramètres › Connexions › Invoice Ninja.',
           'client_invoice_ninja_non_lie': 'Associez d’abord le dossier à un client Invoice Ninja.',
           'aucun_temps_a_facturer': 'Aucun temps validé restant à facturer pour ce dossier.',
           'taux_horaire_manquant': 'Un taux horaire est requis pour chaque temps validé (conditions du dossier).',
           'facture_deja_creee_verifier_invoice_ninja': 'Cette facture a déjà été créée : vérifiez dans Invoice Ninja avant toute nouvelle tentative.',
           'facture_incertaine_verifier_invoice_ninja': 'Création incertaine : vérifiez dans Invoice Ninja ; aucune nouvelle tentative automatique.',
           'facture_relecture_impossible': 'Facture créée mais relecture impossible : vérifiez dans Invoice Ninja.',
           'temps_deja_transmis': 'Ce temps a déjà été transmis à Invoice Ninja.',
           'temps_incertain_verifier_invoice_ninja': 'Transmission incertaine : vérifiez dans Invoice Ninja ; aucune nouvelle tentative automatique.',
           'confirmation_requise': 'Confirmation explicite requise.',
           'profil_modeles_indisponible': 'Ollama ne répond pas ou aucun modèle local n’est installé.',
           'profil_modeles_banc_requis': 'Testez d’abord le modèle (banc rapide) avant d’appliquer le profil économe.',
           'profil_modeles_banc_echoue': 'Le modèle n’a pas réussi le banc : il ne reçoit pas le tri ni le contrôle.',
           'banc_modele_local_requis': 'Le banc s’applique à un modèle local Ollama.',
 'intervention_avocat_ordali':'Décision ou analyse juridique nécessaire. Aucun transfert vers Ordali.',
 'autonomie_proposer_seulement':'Niveau d’autonomie « proposer seulement » : aucun brouillon écrit. Réglable dans la page Autonomie.',
 'confirmation_autonomie_requise':'Passer une tâche au niveau « agir » exige de cocher la confirmation.',
 'action_toujours_soumise_a_validation':'Cette action sort du cabinet : elle reste toujours soumise à votre validation.',
 'tache_autonomie_inconnue':'Tâche d’autonomie inconnue.',
 'niveau_autonomie_invalide':'Niveau non proposé pour cette tâche.',
 'agenda_non_configure':'L’agenda n’est pas configuré.',
 'correspondant_ou_dossier_a_confirmer':'Confirmer le dossier et le correspondant dans Associations.',
 'destinataires_multiples_a_verifier':'Vérifier le rôle des personnes en copie avant de relancer.',
 'corps_non_lisible':'Le texte du courriel n’a pas pu être extrait.',
 'divulgation_a_un_tiers_a_valider':'Le contenu destiné au tiers nécessite votre contrôle.',
 'controle_qualite_non_satisfait':'La vérification du projet a relevé un point à revoir.',
 'tri_ia_none':'Le tri estime qu’aucune réponse n’est nécessaire.',
 'brouillon_imap_cree':'Projet déposé dans le dossier Brouillons configuré.',
 'index_dossier_a_completer':'L’index du dossier doit être complété depuis la vue Dossiers.'}
REASONS.update({"mentions_obligatoires_manquantes": "Des mentions obligatoires manquent : le projet n’est pas prêt à relire. Complétez-le (voir la liste dans le projet).", "references_a_verifier_avant_validation": "Des références juridiques ne sont pas vérifiées. Contrôlez-les puis cochez la case de confirmation avant de valider.", "constat_sans_piece": "La relecture a écarté un constat qui ne citait aucune pièce.", "champ_reference_refuse": "Une référence juridique contient des caractères inattendus : elle n’a pas été transmise au service de sources.", "date_des_faits_invalide": "La date des faits doit être au format AAAA-MM-JJ.", "modele_inconnu": "Ce modèle d’acte n’existe pas.", "motif_mention_invalide": "Un motif de mention est invalide ou trop complexe.", "mention_personnalisee_invalide": "Une mention personnalisée est incomplète ou existe déjà.", "identifiant_piste_invalide": "L’identifiant PISTE est invalide.", "secret_piste_invalide": "Le secret PISTE est invalide.", "texte_vide": "Le texte à contrôler est vide.", "base_locale_illisible": "Le fichier de références du cabinet est illisible."})
REASONS.update({
 'contexte_trop_long':'Le contexte dépasse la capacité du modèle. Choisissez un dossier précis et posez une question plus ciblée.',
 'question_et_sources_essentielles_depassent_contexte':'La demande et les pièces indispensables dépassent la capacité du modèle. Réduisez la question ou joignez un document plus court.',
 'courriel_entrant_depasse_contexte_pour_reponse':'Le courriel entier dépasse la capacité disponible pour préparer et vérifier une réponse. Le brouillon est bloqué : examinez le message dans Roundcube.',
 'contexte_modele_insuffisant_pour_verification':'La capacité de contexte configurée ne permet pas la vérification d’une réponse. Vérifiez les paramètres du modèle.',
 'generation_ia_incomplete':'La génération a atteint sa limite avant la fin. Posez une question plus ciblée ou relancez avec moins de pièces.',
 'fragment_document_depasse_contexte':'Un fragment dépasse encore la capacité du modèle. Réduisez la taille des fragments dans Paramètres > Maintenance puis reprenez : les fragments déjà terminés restent en cache.',
 'analyse_document_couverture_incomplete':'Toutes les pages n’ont pas été validées. Relancez la même demande : l’analyse reprendra uniquement les fragments manquants.',
 'document_long_depasse_pages_autorisees':'Le document dépasse la limite de pages configurée pour les documents longs.',
 'document_long_depasse_taille_autorisee':'Le texte extrait dépasse la limite configurée pour les documents longs.',
 'previsualisation_libreoffice_poppler_requise':'Installer LibreOffice Writer et Poppler sur le serveur, puis réessayer la prévisualisation.',
 'rendu_word_indisponible':'La conversion Word locale a échoué ; contrôler LibreOffice et le modèle DOCX.',
 'modele_word_non_valide':'Ce modèle doit être approuvé après examen de toutes ses pages.',
 'version_source_modifiee':'Le fichier Nextcloud a changé depuis l’aperçu ; préparez une nouvelle version.',
 'nom_fichier_deja_existant':'Ce nom de fichier existe déjà ; préparez de nouveau le projet.',
 'fichier_courrier_non_verifie':'Le fichier créé n’a pas pu être relu ; vérifier la destination puis reprendre.',
 'projet_courrier_expire':'Le projet a expiré ; préparez un nouvel aperçu.',
 'confirmation_courrier_invalide':'Code de confirmation incorrect ; reprenez celui affiché lors de la préparation.'})
from .workstation import FRENCH_REASONS
REASONS.update(FRENCH_REASONS)
REASONS.update({
 'conflit_a_examiner':'Ce dossier vient d’être ouvert et sa recherche de conflits d’intérêts n’a pas encore été examinée : la production est suspendue pour lui seul. Ouvrez « Cabinet › Conflits d’intérêts », enregistrez votre décision (ou désactivez le blocage), puis relancez.',
 'dossier_decline_conflit':'Ce dossier a été décliné pour conflit d’intérêts : aucune production n’est lancée. La décision peut être modifiée dans « Cabinet › Conflits d’intérêts ».',
 'agenda_planification_non_configure':'Aucun calendrier de planification n’est configuré : exécutez configure-nextcloud-workflow.py sur le serveur.',
 'liste_taches_axiorhub_non_configuree':'Aucune liste de tâches Nextcloud n’est configurée : exécutez configure-nextcloud-workflow.py sur le serveur.',
 'tache_externe_lecture_seule':'Cette tâche n’a pas été créée par AxiorHub : elle reste en lecture seule ici (modifiez-la dans Nextcloud).',
 'evenement_externe_lecture_seule':'Cet événement n’a pas été créé par AxiorHub : il reste en lecture seule ici (modifiez-le dans Nextcloud).',
 'evenement_absent':'Cet événement n’existe plus dans l’agenda ; actualisez la page.',
 'titre_evenement_invalide':'Le titre doit comporter entre 3 et 200 caractères.',
 'creneau_evenement_invalide':'Date, heure ou durée invalide (durée de 5 minutes à 24 heures).',
 'confirmation_suppression_requise':'La suppression doit être confirmée.',
 'http_412':'L’élément a été modifié ailleurs depuis le dernier affichage : actualisez la page puis recommencez.',
 'http_403':'Nextcloud refuse l’écriture : le compte technique n’a pas les droits sur ce calendrier.',
 'http_404':'Nextcloud ne trouve plus cet élément ou ce calendrier.',
 'modification_agenda_personnel_desactivee':'La modification de vos agendas et tâches personnels est désactivée : activez-la dans l’onglet Agenda.',
 'evenement_recurrent_lecture_seule':'Événement récurrent : modifiez la série dans Nextcloud (AxiorHub ne réécrit pas les séries).',
 'objet_agenda_modifie_ailleurs':'L’élément a été modifié dans Nextcloud depuis le dernier affichage : actualisez puis recommencez.',
 'agenda_lecture_seule_pour_le_compte':'Le compte technique n’a qu’un accès en lecture à cet agenda : partagez-le en écriture ou modifiez-le dans Nextcloud.',
 'version_agenda_inconnue':'Version de l’élément inconnue : cliquez « Actualiser depuis Nextcloud » puis recommencez.',
 'objet_agenda_hors_calendrier':'Cet élément n’appartient pas à un agenda autorisé.',
 'objet_agenda_illisible':'Élément d’agenda illisible.',
 'aucune_modification':'Aucune modification demandée.',
 'adresse_cabinet_absente':'Adresse du cabinet absente de la configuration : le rappel par courriel est impossible.',
 'tache_absente':'Cette tâche n’existe plus ; actualisez depuis Nextcloud.',
 'heure_routine_invalide':'Heure invalide (format HH:MM).',
 'jours_routine_invalides':'Choisissez au moins un jour (1 = lundi … 7 = dimanche).',
 'signature_trop_longue':'Signature trop longue (600 caractères au plus).',
 'routine_inconnue':'Routine inconnue.'})
REASONS.update({
 'service_redemarre_verifier_avant_relance':'Le service a redémarré pendant ce travail. Contrôlez la destination avant de relancer pour éviter un doublon.',
 'depot_automatique_desactive':'Le dépôt automatique est désactivé dans vos réglages ; le projet reste à examiner.',
 'requete_en_cours_verifier_activite':'Cette demande est déjà réservée. Vérifiez son résultat dans le panneau Activité avant de la relancer.'})
JOB_LABELS = {'extract_facts460':'Recherche de faits dans les pièces du dossier','analyze_deadline450':'Analyse d’une pièce (événement de départ et échéance)','analyze_notice440':'Analyse d’un avis de procédure (dates, agenda, brouillon)','live_mail430':'Contrôle des courriels et préparation des réponses',
              'live_calendar430':'Surveillance des échéances et audiences',
              'live_documents430':'Surveillance des documents des dossiers',
              'sync':'Découverte des dossiers et correspondants','approve':'Confirmation d’association',
              'reject':'Rejet d’une proposition','associate':'Enregistrement d’un correspondant',
              'remove_contact':'Retrait d’une association','retry':'Reprise d’un courriel',
              'retry_matter':'Reprise des courriels du dossier','index':'Indexation du dossier',
              'review':'Préparation des points à décider','forget':'Oubli d’un exemple',
              'run':'Analyse des nouveaux courriels','learn':'Apprentissage des réponses envoyées',
              'discover':'Parcours des dossiers','browse':'Recherche dans Nextcloud',
              'register_matter':'Enregistrement du dossier','chat':'Réponse de l’assistant',
              'forget_chat':'Oubli de la conversation','index_all':'Indexation des dossiers enregistrés',
              'memory_all':'Initialisation progressive des mémoires juridiques',
              'sync_legal_memory':'Actualisation de la mémoire et de la chronologie',
              'validate_memory':'Confirmation d’une information juridique',
              'pin_memory':'Épinglage d’une information juridique',
              'dispute_memory':'Contestation d’une information juridique',
              'archive_memory':'Archivage d’une information juridique',
              'resolve_conflict':'Examen d’une contradiction',
              'refresh_brief':'Actualisation de la fiche vivante','validate_fact':'Validation d’une information',
              'pin_fact':'Épinglage d’une instruction','archive_fact':'Archivage d’une information',
              'attachment_review':'Analyse des pièces jointes','deadline_review':'Détection des échéances',
              'confirm_event':'Création confirmée dans l’agenda','confirm_task':'Création confirmée d’une relance',
              'ignore_deadline':'Date ignorée','prepare_draft':'Préparation d’un projet prudent',
              'prepare_reply':'Préparation et dépôt sécurisé de la réponse',
              'deposit_draft':'Dépôt confirmé dans Brouillons','feedback':'Évaluation du projet',
              'add_rule':'Ajout d’une règle locale','mark_handled':'Courriel traité sans réponse',
              'execute_actions':'Actions proposées exécutées sans envoi','memory_insight':'Analyse d’une correction',
              'memory_scope':'Portée d’une préférence','health':'Contrôle des connexions',
              'daily_digest':'Synthèse quotidienne','automation_setting':'Réglage d’un automatisme',
              'create_matter':'Création d’une affaire Nextcloud',
              'assistant_answer':'Réponse de l’assistant',
              'analyze_strategy':'Analyse stratégique du dossier',
              'build_matrix':'Construction de la matrice faits et pièces',
              'draft_act':'Rédaction d’un projet d’acte',
              'validate_strategy':'Validation d’une analyse stratégique',
              'archive_strategy':'Archivage d’une analyse stratégique',
              'validate_matrix_row':'Validation d’une ligne de preuve',
              'dispute_matrix_row':'Contestation d’une ligne de preuve',
              'archive_matrix_row':'Archivage d’une ligne de preuve',
              'validate_act':'Validation d’un projet de travail',
              'archive_act':'Archivage d’un projet de travail'}
JOB_LABELS.update({'monitor_matter':'Surveillance immédiate du dossier',
  'proactive34_cycle':'Analyse différentielle de quatre heures',
  'proactive34_briefing':'Briefing matinal',
  'proactive34_now':'Analyse immédiate des dossiers actifs',
  'monitor_all':'Surveillance progressive des dossiers',
  'build_daily_dashboard':'Actualisation du tableau quotidien',
  'ack_signal':'Signal marqué comme vu','snooze_signal':'Signal reporté',
  'resolve_signal':'Signal résolu','organize_cabinet':'Organisation automatique du cabinet',
  'classify_portfolio':'Classement du portefeuille','reconcile_inbox':'Nettoyage de la boîte À traiter',
  'set_matter_state':'État du dossier confirmé','confirm_matter':'Dossier du courriel confirmé',
  'reject_group':'Association groupée rejetée','propose_work_plan':'Préparation du programme de travail',
  'sync_caldav_tasks':'Synchronisation des tâches Nextcloud','apply_work_plan':'Création du programme Nextcloud',
  'update_work_task':'Mise à jour de la tâche Nextcloud','autonomy_mail_sweep':'Surveillance autonome des courriels',
  'edit_work_task':'Modification de la tâche Nextcloud','create_agenda_event':'Création d’un événement dans l’agenda','pieces_scan510':'Analyse des pièces du dossier (bordereau)','edit_personal_event':'Modification d’un événement de votre agenda','routine520':'Briefing, tri des courriels ou bilan de la semaine','docrequest520':'Préparation d’un document demandé','maildraft5613':'Préparation d’un brouillon de courriel','task5614':'Tâche d’une mission complexe','deck530_sync':'Synchronisation de Deck et des Tâches Nextcloud','style550_scan':'Analyse du style du cabinet','edit_personal_task':'Modification d’une tâche Nextcloud personnelle','pieces_create510':'Création du bordereau et des pièces numérotées','edit_agenda_event':'Modification d’un événement de l’agenda','cancel_agenda_event':'Suppression d’un événement de l’agenda','schedule_work_task':'Programmation de la tâche dans l’agenda',
  'refresh_operational_memory':'Actualisation de la mémoire opérationnelle',
  'review_autonomy_proposal':'Examen d’une proposition autonome',
  'prepare_document_project':'Préparation d’un projet documentaire',
  'create_document_files':'Création confirmée des fichiers documentaires',
  'legal_research':'Recherche juridique anonymisée',
  'verify_official_decision':'Vérification d’une décision officielle',
  'orchestrate_mail':'Analyse différentielle courriel–dossier',
  'orchestrator_mail_sweep':'Orchestration des nouveaux courriels',
  'review_orchestration_notification':'Revue d’une notification consolidée',
  'prepare_legal_opinion':'Préparation d’un avis et de simulations',
  'review_legal_opinion':'Revue d’un projet d’avis',
  'identify_latest_writings':'Identification des dernières écritures',
  'refresh_exhibit_registry':'Actualisation du registre des pièces',
  'identify_party_writings':'Identification des conclusions des parties',
  'compare_devices':'Comparaison des dispositifs',
  'prepare_hearing':'Préparation contradictoire d’audience',
  'create_hearing_files':'Création confirmée du dossier de plaidoirie',
  'prepare_word_project':'Préparation de la révision Word',
  'create_word_files':'Création confirmée des versions Word',
  'refresh_cabinet_pilotage':'Actualisation du pilotage du cabinet',
  'prepare_meeting':'Préparation du rendez-vous',
  'prepare_transcript_report':'Préparation du compte rendu de transcription',
  'review_cabinet_decision':'Décision de l’avocat enregistrée',
  'create_cabinet_confirmation_batch':'Préparation d’un lot de décisions',
  'approve_cabinet_confirmation_batch':'Confirmation groupée enregistrée',
  'record_provision':'Provision ajoutée au registre interne',
  'run_continuous_business_tests':'Tests métier continus',
  'production_cycle390':'Production interne automatique',
  'production_cycle391':'Production réelle observable','advance_playbooks391':'Poursuite des playbooks',
  'retry_production391':'Reprise d’un incident de production','review_output391':'Décision sur un livrable',
  'universal_command391':'Instruction transversale à AxiorHub',
  'advance_playbooks390':'Poursuite automatique des playbooks',
  'verify_deliverable420':'Vérification du livrable dans son service de destination',
  'retry_deliverable420':'Reprise d’un incident de production',
  'studio_prepare420':'Demande du Studio de production',
  'advance_matter420':'Travaux internes pour faire avancer le dossier',
  'snapshot_metrics420':'Mesure des livrables réellement produits'})

CONFIRM_REASONS = {
    'correspondant_ou_dossier_a_confirmer', 'destinataires_multiples_a_verifier',
    'divulgation_a_un_tiers_a_valider', 'index_dossier_a_completer',
    'demande_ambigue', 'aucun_document_exploitable',
}
JOB_LABELS.update({'prepare_cabinet_letter':'Prévisualisation du courrier Word',
  'prepare_cabinet_revision':'Prévisualisation de la nouvelle version Word',
  'create_cabinet_letter':'Création confirmée du courrier Word'})
JOB_LABELS.update({'coach_hearing35':'Coaching de plaidoirie',
  'prepare_call35':'Préparation de l’appel','record_call35':'Compte rendu d’appel',
  'calculate35':'Calcul contrôlable','billing_review35':'Facturation à relire',
  'classify_comparable35':'Comparaison de la jurisprudence'})


JOB_LABELS.update({'sent568_scan':'Lecture des envois et suivi des engagements',
  'document568_scan':'Recherche de nouveaux documents correspondant à vos règles',
  'document568_compile':'Interprétation de votre mission en règle modifiable',
  'document568_run':'Analyse, classement et préparation des suites du document',
  'received568_scan':'Vérification des réponses aux engagements',
  'followup568_prepare':'Préparation et relecture IMAP d’une relance neutre',
  'proactive568_cycle':'Progression des suites de missions',
  'talk568_prepare':'Préparation et contrôle d’un salon Talk privé',
  'news568_collect':'Veille des flux juridiques officiels'})


def inbox_bucket(status, reason):
    if status == 'drafted':
        return 'drafts'
    if status == 'ignored':
        return 'ignored'
    if reason in CONFIRM_REASONS:
        return 'confirm'
    return 'todo'


def simple_question(report):
    reason=report.get('reason','')
    sender=report.get('sender','ce correspondant')
    questions={
        'intervention_avocat_ordali':'Souhaitez-vous préparer une réponse prudente sans arrêter la décision juridique ?',
        'correspondant_ou_dossier_a_confirmer':'À quel dossier et sous quel rôle faut-il rattacher '+sender+' ?',
        'destinataires_multiples_a_verifier':'Les personnes en copie peuvent-elles recevoir la réponse ?',
        'divulgation_a_un_tiers_a_valider':'Quelles informations peuvent être communiquées à ce tiers ?',
        'corps_non_lisible':'Le contenu est illisible : faut-il vérifier la pièce ou ignorer ce message ?',
        'demande_ambigue':'Quelle réponse souhaitez-vous apporter à cette demande ambiguë ?',
        'index_dossier_a_completer':'Faut-il actualiser le dossier avant de répondre ?',
        'aucun_document_exploitable':'Faut-il répondre sans document du dossier ou compléter celui-ci ?',
        'controle_qualite_non_satisfait':'Souhaitez-vous faire préparer une nouvelle version plus prudente ?',
    }
    return questions.get(reason,'Souhaitez-vous préparer une réponse, ignorer le message ou corriger son dossier ?')


def e(value):
    return escape(str(value if value is not None else ''),quote=True)


def date(value):
    try:
        stamp=datetime.fromisoformat(value)
    except (ValueError,TypeError):
        try:stamp=parsedate_to_datetime(value)
        except (ValueError,TypeError):return str(value or '')
    if not stamp.tzinfo:stamp=stamp.replace(tzinfo=timezone.utc)
    return stamp.astimezone(timezone.utc).strftime('%d/%m/%Y %H:%M UTC')


def moment(value):
    if not value:return datetime.min.replace(tzinfo=timezone.utc)
    try:stamp=datetime.fromisoformat(str(value))
    except (ValueError,TypeError):
        try:stamp=parsedate_to_datetime(str(value))
        except (ValueError,TypeError):return datetime.min.replace(tzinfo=timezone.utc)
    if not stamp.tzinfo:stamp=stamp.replace(tzinfo=timezone.utc)
    return stamp.astimezone(timezone.utc)


def block(text):
    return '<pre class="prose">'+e(text)+'</pre>'


def matter_overview(c,desk,state,matter):
    """Build one bounded, chronological matter view from local indexed evidence."""
    mid=matter['id'];index=DocumentIndex(c['state_dir'])
    documents=[]
    for row in index.db.execute('SELECT path,modified,text,error FROM docs WHERE matter=? ORDER BY modified DESC LIMIT 500',(mid,)):
        documents.append({'path':row[0],'modified':row[1],'text':row[2] or '',
                          'error':row[3] or '','name':PurePosixPath(row[0]).name})
    folders=sorted({str(PurePosixPath(d['path']).parent) for d in documents},key=lambda x:(x.count('/'),x.lower()))

    emails=[];groups={}
    for row in index.db.execute('''SELECT source_id,path,modified,kind,chunk_no,text,meta
        FROM knowledge_chunks WHERE matter=? AND kind IN ('email_received','email_history','email_sent')
        ORDER BY modified DESC,source_id,chunk_no''',(mid,)):
        item=groups.setdefault(row[0],{'source_id':row[0],'path':row[1],
            'modified':row[2],'kind':row[3],'parts':[],'meta':{}})
        item['parts'].append(row[5] or '')
        if row[4]==0:
            try:item['meta']=json.loads(row[6] or '{}')
            except (ValueError,TypeError):item['meta']={}
    seen=set()
    for item in groups.values():
        meta=item['meta'];direction='Envoyé' if item['kind']=='email_sent' else 'Reçu'
        identity=(direction,meta.get('message_id') or item['source_id'])
        if identity in seen:continue
        seen.add(identity)
        emails.append({'direction':direction,'subject':meta.get('subject') or PurePosixPath(item['path']).name,
            'sender':meta.get('sender',''),'message_id':meta.get('message_id',''),
            'modified':item['modified'],'text':'\n'.join(item['parts'])[:6000],
            'source_id':item['source_id']})
    emails.sort(key=lambda x:moment(x['modified']),reverse=True)

    report_keys={};drafts=[]
    for key,status,reason,stamp in state.rows(1000):
        try:report=report_for(c,key)
        except Stop:continue
        if report.get('matter')!=mid:continue
        message_id=report.get('incoming_message_id') or report.get('message_id')
        if message_id:report_keys[message_id]=key
        if report.get('draft_body'):
            drafts.append({'key':key,'subject':report.get('subject','Courriel'),
                'body':report['draft_body'],'modified':stamp,'state':status,
                'location':c['mail']['drafts'] if status=='drafted' else 'Projet intermédiaire'})
    for email in emails:email['key']=report_keys.get(email['message_id'],'')
    for row in desk.db.execute('SELECT mail_key,data,updated FROM manual_drafts ORDER BY updated DESC'):
        try:data=json.loads(row['data']);value=data.get('result',{})
        except (ValueError,TypeError):continue
        if data.get('matter')!=mid or not value.get('body'):continue
        if not any(x['key']==row['mail_key'] and x['body']==value['body'] for x in drafts):
            drafts.append({'key':row['mail_key'],'subject':data.get('subject','Projet de réponse'),
                'body':value['body'],'modified':row['updated'],'state':'review',
                'location':'En attente de votre décision' if value.get('requires_decision') else 'Prêt à déposer'})
    drafts.sort(key=lambda x:moment(x['modified']),reverse=True)

    tasks=[dict(row) for row in desk.db.execute('SELECT * FROM tasks WHERE matter=? ORDER BY due,created DESC',(mid,))]
    brief_row=desk.db.execute('SELECT * FROM case_briefs WHERE matter=? ORDER BY version DESC LIMIT 1',(mid,)).fetchone()
    brief=json.loads(brief_row['data']) if brief_row else None
    timeline=[]
    for d in documents:
        timeline.append({'at':d['modified'],'kind':'Document','title':d['name'],'text':d['path']})
    for mail in emails:
        timeline.append({'at':mail['modified'],'kind':'Courriel '+mail['direction'].lower(),
                         'title':mail['subject'],'text':mail['sender'],'key':mail['key']})
    for draft in drafts:
        timeline.append({'at':draft['modified'],'kind':'Brouillon','title':draft['subject'],
                         'text':draft['location'],'key':draft['key']})
    for task in tasks:
        timeline.append({'at':task['due'] or task['created'],'kind':'Tâche',
                         'title':task['title'],'text':task['status']})
    if brief:
        for item in brief.get('chronology',[]):
            timeline.append({'at':item.get('date',''),'kind':'Fait sourcé',
                             'title':item.get('text',''),'text':', '.join(item.get('source_ids',[]))})
    timeline.sort(key=lambda x:moment(x['at']),reverse=True)
    from .legal_memory import memory_records,conflicts as memory_conflicts,timeline as unified_timeline,memory_summary
    records=memory_records(desk,mid,None,300);conflict_rows=memory_conflicts(desk,mid)
    unified=unified_timeline(desk,mid,300)
    if unified:
        labels={'document':'Document','email_received':'Courriel reçu','email_sent':'Courriel envoyé',
          'draft':'Brouillon','task':'Tâche','calendar':'Agenda','fact':'Fait sourcé',
          'deadline':'Échéance','completed_action':'Diligence accomplie'}
        timeline=[{'at':row['event_at'],'kind':labels.get(row['event_type'],row['event_type']),
          'title':row['title'],'text':row['detail'] or row['source_path'],
          'source_path':row['source_path'],'source_id':row['source_id'],
          'confidence':row['confidence'],'status':row['status'],
          'key':row['source_id'] if (row['source_kind']=='mail_report' and
                 re.fullmatch(r'[a-f0-9]{64}',row['source_id'] or '')) else ''}
          for row in unified]
    return {'documents':documents,'folders':folders,'emails':emails,'drafts':drafts,
            'tasks':tasks,'brief':brief,'brief_row':brief_row,'timeline':timeline[:200],
            'document_errors':sum(bool(x['error']) for x in documents),
            'memory_records':records,'memory_conf':memory_summary(desk,mid),
            'memory_conflicts':conflict_rows}


def live_cursor(raw):
    """5.6.3 : identifiant du dernier événement reçu (en-tête Last-Event-ID) ; toute valeur non numérique vaut 0 au lieu d'une erreur 500."""
    try:
        return min(max(int(str(raw).strip()), 0), 2**63 - 1)
    except (TypeError, ValueError):
        return 0


class LiveStream:
    """5.6.3 : flux SSE dont la place est rendue à la fermeture, même si le navigateur coupe avant le premier envoi.

    Un générateur jamais démarré n'exécute pas son bloc « finally » : la place restait prise et, après quelques coupures, le serveur
    répondait « Flux actifs trop nombreux ». Le serveur WSGI appelle toujours close() : la place est rendue une seule fois.
    """

    def __init__(self, events, release):
        self.events, self.release, self.done = events, release, False

    def __iter__(self):
        return self

    def __next__(self):
        return next(self.events)

    def close(self):
        try:
            self.events.close()
        finally:
            if not self.done:
                self.done = True
                self.release()


class App:
    def __init__(self, config_path, auth_path):
        self.config_path, self.auth_path = config_path, auth_path
        self.auth_cache={};self.failures=[];self.mutex=threading.Lock()
        self.live_slots=threading.BoundedSemaphore(8)

    def authenticate(self, environ, auth):
        raw=environ.get('HTTP_AUTHORIZATION','')
        client=(environ.get('HTTP_X_FORWARDED_FOR','') or environ.get('REMOTE_ADDR','')).split(',')[0].strip()[:64]
        if len(raw)>2048:
            return False
        key=hashlib.sha256((raw+auth['hash']).encode()).hexdigest()
        with self.mutex:
            if self.auth_cache.get(key,0)>time.time():
                return True
            self.failures=[x for x in self.failures if x[0]>time.time()-60]
            if len([x for x in self.failures if x[1]==client])>=10 or len(self.failures)>=200:
                return False
        if raw.startswith('Bearer ') and auth.get('api_token_sha256'):
            return hmac.compare_digest(hashlib.sha256(raw[7:].encode()).hexdigest(),
                                       auth['api_token_sha256'])
        try:
            if not raw.startswith('Basic '):
                return False
            user,password=base64.b64decode(raw[6:],validate=True).decode().split(':',1)
            hashed=hashlib.scrypt(password.encode(),salt=bytes.fromhex(auth['salt']),n=16384,r=8,p=1).hex()
            good=hmac.compare_digest(user,auth['username']) and hmac.compare_digest(hashed,auth['hash'])
        except (ValueError,UnicodeError):
            good=False
        with self.mutex:
            if good:
                if len(self.auth_cache)>40:self.auth_cache.clear()
                self.auth_cache[key]=time.time()+60
            else:self.failures.append((time.time(),client))
        return good

    def __call__(self, env, start_response):
        headers=[('Cache-Control','no-store'),('X-Content-Type-Options','nosniff'),
                 # Native form POSTs use navigation mode. Under no-referrer,
                 # Fetch serializes their Origin as null, which we must reject.
                 ('Referrer-Policy','same-origin'),('X-Frame-Options','DENY'),
                 ('Permissions-Policy','camera=(), microphone=(self), geolocation=()'),
                 ('Content-Security-Policy',"default-src 'none'; script-src 'self'; connect-src 'self'; img-src 'self'; style-src 'self'; font-src 'self' data:; media-src 'self' blob:; form-action 'self'; frame-src 'self'; frame-ancestors 'none'; base-uri 'none'")]
        status='200 OK';kind='text/html; charset=utf-8'
        try:
            auth=json.loads(Path(self.auth_path).read_text(encoding='utf-8'))
            from .web567 import actor
            auth['owner']=actor(env)[0]   # 5.6.9 : utilisateur courant pour les blocs personnels des pages (« À décider »)
            origin=urlsplit(auth['origin'])
            if env.get('HTTP_HOST','')!=origin.netloc:
                raise Stop('hote_refuse')
            if origin.scheme=='https' and env.get('HTTP_X_FORWARDED_PROTO')!='https' and env.get('wsgi.url_scheme')!='https':
                raise Stop('https_obligatoire')
            _prefix=auth.get('prefix','/agent-courriel');_path=env.get('PATH_INFO','/')
            if _path.startswith(_prefix+'/'):_path=_path[len(_prefix):]
            _public=None
            if _path.startswith('/office/'):
                from .web440 import public as _pub440
                _public=_pub440(env,load_config(self.config_path),_path,env['REQUEST_METHOD'])
            elif _path in ('/reception567/twilio','/reception567/whatsapp'):
                from .reception567 import webhook
                _desk567=Desk(load_config(self.config_path))
                try:
                    _public=webhook(_desk567,env,_path)
                except Stop as ex:
                    code=str(ex)
                    _public={'status':'403 Forbidden' if code=='signature_accueil_refusee' else '400 Bad Request','kind':'application/json','body':json.dumps({'error':code})}
                except (KeyError,ValueError,TypeError,UnicodeError):
                    _public={'status':'400 Bad Request','kind':'application/json','body':json.dumps({'error':'accueil_configuration_ou_message_invalide'})}
                finally:_desk567.db.close()
            elif _path=='/pwa/manifest.webmanifest' and env['REQUEST_METHOD'] in ('GET','HEAD'):
                # Le manifeste ne contient aucune donnée de dossier : nom, icône, adresses de démarrage.
                from .mobile490 import manifest as _manifest490
                _public={'status':'200 OK','kind':'application/manifest+json; charset=utf-8',
                         'body':json.dumps(_manifest490(_prefix),ensure_ascii=False) if env['REQUEST_METHOD']=='GET' else '',
                         'csp':"default-src 'none'"}
            if _public is not None:
                status=_public['status'];kind=_public['kind'];body=_public['body']
                headers=[(n,v) for n,v in headers if n!='Content-Security-Policy']+[('Content-Security-Policy',_public.get('csp',"default-src 'none'"))]+list(_public.get('headers',[]))
            elif not self.authenticate(env,auth):
                status='401 Unauthorized';headers.append(('WWW-Authenticate','Basic realm="AxiorHub Cabinet", charset="UTF-8"'))
                body='<p>Connectez-vous avec le compte défini à l’installation.</p>'
                if _path.startswith(('/api','/live/')):
                    kind='application/json; charset=utf-8';body=json.dumps({'error':'authentification_requise'})
            else:
                cfg=load_config(self.config_path)
                # The iframe origin is configuration, never a compiled example
                # domain. Keep the CSP narrow while allowing the selected local
                # Open WebUI instance.
                from .workstation import external_links
                configured_openwebui=external_links(Desk(cfg)).get('openwebui','')
                openwebui_parts=urlsplit(configured_openwebui)
                frame_origin=(openwebui_parts.scheme+'://'+openwebui_parts.netloc
                  if openwebui_parts.scheme=='https' and openwebui_parts.netloc else '')
                csp="default-src 'none'; script-src 'self'; connect-src 'self'; img-src 'self'; style-src 'self'; font-src 'self' data:; media-src 'self' blob:; manifest-src 'self'; worker-src 'self'; form-action 'self'; frame-src 'self'"+((' '+frame_origin) if frame_origin else '')+"; frame-ancestors 'none'; base-uri 'none'"
                headers=[(name,csp if name=='Content-Security-Policy' else value) for name,value in headers]
                prefix=auth.get('prefix','/agent-courriel')
                path=env.get('PATH_INFO','/')
                if path.startswith(prefix+'/'):path=path[len(prefix):]
                args={k:v[0] for k,v in parse_qs(env.get('QUERY_STRING',''),max_num_fields=30).items()}
                env['axiorhub.config_path']=self.config_path
                from .web440 import route as _route440
                _r440=_route440(env,cfg,auth,prefix,path,args,env['REQUEST_METHOD'])
                if _r440 is not None:
                    status=_r440['status'];kind=_r440['kind'];body=_r440['body']
                    headers.extend(_r440.get('headers',[]))
                    if _r440.get('csp'):
                        headers=[(n,_r440['csp'] if n=='Content-Security-Policy' else v) for n,v in headers]
                elif path=='/live/events' and env['REQUEST_METHOD']=='GET':
                    from .live430 import stream
                    after=live_cursor(env.get('HTTP_LAST_EVENT_ID') or args.get('after') or 0)
                    live_desk=Desk(cfg);live_desk.db.close()
                    if not self.live_slots.acquire(blocking=False):
                        start_response('429 Too Many Requests',headers+[('Content-Type','text/plain'),('Retry-After','15')]);return [b'Flux actifs trop nombreux.']
                    start_response('200 OK',headers+[('Content-Type','text/event-stream; charset=utf-8'),('X-Accel-Buffering','no')])
                    return LiveStream(stream(cfg,after),self.live_slots.release)
                elif path=='/live/snapshot' and env['REQUEST_METHOD']=='GET':
                    from .live430 import snapshot
                    live_desk=Desk(cfg)
                    try:body=json.dumps(snapshot(live_desk,int(args.get('after',0))),ensure_ascii=False)
                    finally:live_desk.db.close()
                    kind='application/json; charset=utf-8'
                elif path=='/live/panel' and env['REQUEST_METHOD']=='GET':
                    from .live430 import panel
                    live_desk=Desk(cfg)
                    try:body=panel(live_desk,prefix,auth['csrf'])
                    finally:live_desk.db.close()
                elif path=='/live/board' and env['REQUEST_METHOD']=='GET':
                    from .production420_ui import today_page
                    live_desk=Desk(cfg)
                    def live_form(action,label,fields=None,extra=''):
                        hidden={'csrf':auth['csrf'],'action':action,**(fields or {})}
                        return '<form method="post" action="'+e(prefix)+'/action">'+''.join('<input type="hidden" name="'+e(k)+'" value="'+e(v)+'">' for k,v in hidden.items())+extra+'<button>'+e(label)+'</button></form>'
                    def live_link(p,label,**kw):return '<a href="'+e(prefix+p+('?' + urlencode(kw) if kw else ''))+'">'+e(label)+'</a>'
                    from .live430 import enhance_forms
                    try:body=enhance_forms(today_page(live_desk,live_form,live_link),prefix)
                    finally:live_desk.db.close()
                elif path=='/templates/upload' and env['REQUEST_METHOD']=='POST':
                    kind='application/json; charset=utf-8'
                    if env.get('HTTP_ORIGIN')!=auth['origin'] or not hmac.compare_digest(env.get('HTTP_X_CSRF_TOKEN',''),auth['csrf']):
                        raise Stop('origine_ou_csrf_refuse')
                    length=int(env.get('CONTENT_LENGTH','0') or 0)
                    if not 0<length<=8_000_000:raise Stop('taille_modele_word_invalide')
                    if env.get('CONTENT_TYPE','').split(';')[0]!='application/vnd.openxmlformats-officedocument.wordprocessingml.document':
                        raise Stop('type_modele_word_invalide')
                    from .cabinet_docs33 import import_template
                    filename=unquote(env.get('HTTP_X_TEMPLATE_NAME',''),encoding='utf-8',errors='strict')
                    body=json.dumps(import_template(Desk(cfg),env['wsgi.input'].read(length),filename),ensure_ascii=False)
                elif path=='/assistant/attachment' and env['REQUEST_METHOD']=='POST':
                    kind='application/json; charset=utf-8'
                    if env.get('HTTP_ORIGIN')!=auth['origin'] or not hmac.compare_digest(env.get('HTTP_X_CSRF_TOKEN',''),auth['csrf']):
                        raise Stop('origine_ou_csrf_refuse')
                    length=int(env.get('CONTENT_LENGTH','0') or 0)
                    if not 0<length<=8_000_000:raise Stop('taille_piece_assistant_invalide')
                    if env.get('CONTENT_TYPE','').split(';')[0]!='application/octet-stream':
                        raise Stop('type_piece_assistant_invalide')
                    from .improvements36 import upload_attachment
                    filename=unquote(env.get('HTTP_X_ATTACHMENT_NAME',''),encoding='utf-8',errors='strict')
                    body=json.dumps(upload_attachment(Desk(cfg),env['wsgi.input'].read(length),filename,
                        env.get('HTTP_X_ATTACHMENT_MATTER',''),env.get('HTTP_X_ATTACHMENT_KEY','')),ensure_ascii=False)
                elif path=='/hearing/upload' and env['REQUEST_METHOD']=='POST':
                    kind='application/json; charset=utf-8'
                    if env.get('HTTP_ORIGIN')!=auth['origin'] or not hmac.compare_digest(env.get('HTTP_X_CSRF_TOKEN',''),auth['csrf']):
                        raise Stop('origine_ou_csrf_refuse')
                    length=int(env.get('CONTENT_LENGTH','0') or 0)
                    if not 0<length<=20_000_000:raise Stop('taille_conclusions_invalide')
                    if env.get('CONTENT_TYPE','').split(';')[0]!='application/octet-stream':
                        raise Stop('type_conclusions_invalide')
                    from .hearing import upload_writing
                    filename=unquote(env.get('HTTP_X_HEARING_NAME',''),encoding='utf-8',errors='strict')
                    body=json.dumps(upload_writing(Desk(cfg),env['wsgi.input'].read(length),filename,
                        env.get('HTTP_X_HEARING_MATTER',''),env.get('HTTP_X_HEARING_SIDE','')),ensure_ascii=False)
                elif path=='/extensions/upload' and env['REQUEST_METHOD']=='POST':
                    kind='application/json; charset=utf-8'
                    if env.get('HTTP_ORIGIN')!=auth['origin'] or not hmac.compare_digest(env.get('HTTP_X_CSRF_TOKEN',''),auth['csrf']):
                        raise Stop('origine_ou_csrf_refuse')
                    length=int(env.get('CONTENT_LENGTH','0') or 0)
                    if not 0<length<=12_000_000:raise Stop('taille_archive_extension_invalide')
                    if env.get('CONTENT_TYPE','').split(';')[0] not in ('application/zip','application/octet-stream'):
                        raise Stop('type_archive_extension_invalide')
                    from .extensions364 import import_archive
                    filename=unquote(env.get('HTTP_X_EXTENSION_NAME',''),encoding='utf-8',errors='strict')
                    body=json.dumps(import_archive(Desk(cfg),env['HTTP_X_EXTENSION_ID'],
                      env['wsgi.input'].read(length),filename),ensure_ascii=False)
                elif path=='/dictation' and env['REQUEST_METHOD']=='POST':
                    kind='application/json; charset=utf-8'
                    if env.get('HTTP_ORIGIN')!=auth['origin'] or not hmac.compare_digest(env.get('HTTP_X_CSRF_TOKEN',''),auth['csrf']):
                        raise Stop('origine_ou_csrf_refuse')
                    length=int(env.get('CONTENT_LENGTH','0') or 0)
                    if not 0<length<=8000000:raise Stop('dictee_trop_volumineuse')
                    from .audio import dictate
                    body=json.dumps(dictate(cfg,env['wsgi.input'].read(length),env.get('CONTENT_TYPE','').split(';')[0]))
                elif path.startswith('/api/v1'):
                    from .api import dispatch,openapi
                    kind='application/json; charset=utf-8'
                    api_path=path[len('/api/v1'):] or '/'
                    if api_path=='/openapi.json' and env['REQUEST_METHOD']=='GET':
                        result=openapi(auth['origin'],prefix)
                    else:
                        payload={}
                        if env['REQUEST_METHOD']=='POST':
                            length=int(env.get('CONTENT_LENGTH','0') or 0)
                            maximum=(130000 if (re.fullmatch(r'/legal-research/[a-f0-9]{64}/mcp-results',api_path)
                              or api_path=='/ai/chat/completions') else 30000)
                            if length>maximum:raise Stop('requete_api_trop_longue')
                            if length:
                                if env.get('CONTENT_TYPE','').split(';')[0]!='application/json':raise Stop('json_requis')
                                try:payload=json.loads(env['wsgi.input'].read(length).decode())
                                except (ValueError,UnicodeError):raise Stop('json_invalide') from None
                                if not isinstance(payload,dict):raise Stop('objet_json_requis')
                        # Resolve the small set of path parameters without exposing
                        # a generic router or filesystem path.
                        if re.fullmatch(r'/matters/[A-Za-z0-9_-]{1,80}',api_path):
                            from .api import matter_detail
                            result=matter_detail(Desk(cfg),api_path.rsplit('/',1)[-1])
                        elif re.fullmatch(r'/threads/[a-f0-9]{32}',api_path):
                            from .integration import thread_messages
                            result={'messages':thread_messages(Desk(cfg),api_path.rsplit('/',1)[-1])}
                        elif re.fullmatch(r'/jobs/\d+',api_path) and env['REQUEST_METHOD']=='GET':
                            jid=int(api_path.rsplit('/',1)[-1]);row=Desk(cfg).db.execute(
                                'SELECT id,kind,status,priority,created,finished,result FROM jobs WHERE id=?',(jid,)).fetchone()
                            if not row:raise Stop('operation_absente')
                            result=dict(row)
                        elif re.fullmatch(r'/jobs/\d+/cancel',api_path) and env['REQUEST_METHOD']=='POST':
                            result=Desk(cfg).cancel_job(api_path.split('/')[-2])
                        else:result=dispatch(Desk(cfg),api_path,env['REQUEST_METHOD'],payload,args)
                    body=json.dumps(result,ensure_ascii=False)
                elif env['REQUEST_METHOD']=='POST':
                    length=int(env.get('CONTENT_LENGTH','0') or 0)
                    if length<=0 or length>130000:raise Stop('formulaire_trop_long')
                    if env.get('CONTENT_TYPE','').split(';')[0]!='application/x-www-form-urlencoded':raise Stop('formulaire_invalide')
                    parsed_form=parse_qs(env['wsgi.input'].read(length).decode(),max_num_fields=100)
                    form={k:(v if k in ('purposes','source_paths') else v[0]) for k,v in parsed_form.items()}
                    if not hmac.compare_digest(form.pop('csrf',''),auth['csrf']):raise Stop('formulaire_expire')
                    if env.get('HTTP_ORIGIN')!=auth['origin']:raise Stop('origine_refusee')
                    if path!='/action':raise Stop('action_inconnue')
                    action=form.pop('action','');desk_action=Desk(cfg)
                    live_token=form.pop('live_token','')
                    live_fields={**form,'action':action}
                    if live_token:
                        from .live430 import reserve_request
                        recorded=reserve_request(desk_action,live_token,live_fields)
                        if recorded:
                            payload={'job_id':recorded['job_id'],'url':prefix+recorded['target'],'fingerprint':recorded['fingerprint']}
                            start_response('200 OK',headers+[('Content-Type','text/html; charset=utf-8'),('HX-Trigger',json.dumps({'live-action':payload})),('Content-Length','0')]);return [b'']
                    if action=='save_live430':
                        desk_action.setting('live430:enabled',form.get('enabled')=='yes');job=0
                    elif action=='assistant_ask':
                        from .integration import submit_question
                        submitted=submit_question(desk_action,form.get('question',''),form.get('matter',''),
                                                  form.get('key',''),form.get('thread',''),form.get('attachment_id',''))
                        job=submitted['job_id'];form['thread']=submitted['thread_id']
                    elif action=='create_local_task':
                        from .improvements36 import create_task
                        create_task(desk_action,form.get('title',''),form.get('matter',''),form.get('due',''))
                        job=0
                    elif action=='delegate_local_task':
                        from .improvements36 import delegate_task
                        submitted=delegate_task(desk_action,form.get('task_id',''))
                        job=submitted['job_id'];form['thread']=submitted['thread_id']
                    elif action=='update_local_task':
                        from .improvements36 import update_local_task
                        update_local_task(desk_action,form.get('task_id',''),
                          form.get('title') if 'title' in form else None,
                          form.get('matter') if 'matter' in form else None,
                          form.get('due') if 'due' in form else None,
                          form.get('status','open'));job=0
                    elif action=='cancel_local_task':
                        from .improvements36 import cancel_local_task
                        cancel_local_task(desk_action,form.get('task_id',''),form.get('confirm',''));job=0
                    elif action=='approve_work_plan':
                        from .workplan import approve_plan
                        submitted=approve_plan(desk_action,form.get('proposal',''),form.get('confirmation_code',''))
                        job=submitted['job_id'];form['proposal']=submitted['proposal_id']
                    elif action=='reject_work_plan':
                        from .workplan import reject_plan
                        reject_plan(desk_action,form.get('proposal',''));job=0
                    elif action=='reject_document_project':
                        from .document_projects import reject_project
                        reject_project(desk_action,form.get('project_id',''));job=0
                    elif action=='reject_hearing':
                        from .hearing import reject_hearing
                        reject_hearing(desk_action,form.get('project_id',''));job=0
                    elif action=='reject_word_project':
                        from .word_legal import reject_word_project
                        reject_word_project(desk_action,form.get('project_id',''));job=0
                    elif action=='cancel_job':
                        result=desk_action.cancel_job(form.get('job',''));job=int(form.get('job',''))
                    elif action=='retry_job393':
                        from .reliability393 import retry_job
                        result=retry_job(desk_action,form.get('job',''));job=result['job_id']
                    elif action=='run_system_checks393':
                        from .reliability393 import run_checks
                        run_checks(desk_action,include_external=False);job=0
                    elif action=='test_openrouter393':
                        from .reliability393 import test_openrouter
                        test_openrouter(desk_action);job=0
                    elif action=='save_business_rule410':
                        from .learning410 import save_rule
                        save_rule(desk_action,form.get('scope',''),form.get('scope_value',''),
                          form.get('purpose','all'),form.get('rule_type','other'),
                          form.get('instruction',''),'manual','interface');job=0
                    elif action=='set_business_rule410':
                        from .learning410 import set_rule_status
                        set_rule_status(desk_action,form.get('rule_id',''),form.get('status',''));job=0
                    elif action=='trust_document410':
                        from .learning410 import trust_document
                        trust_document(desk_action,form.get('scope',''),form.get('scope_value',''),
                          form.get('path',''),form.get('sha256',''),form.get('trust_level',''),
                          form.get('reason',''));job=0
                    elif action=='save_legal_benchmark_case410':
                        from .evaluation410 import save_case
                        keys=('dates','citations','adverse_arguments','pieces','mail_required',
                              'mail_forbidden','word_markers','permitted_claims')
                        expected={'matter_id':form.get('matter_id','')}
                        expected.update({key:[x.strip() for x in str(form.get(key,'')).splitlines() if x.strip()] for key in keys})
                        save_case(desk_action,form.get('name',''),form.get('task_kind',''),
                          form.get('prompt',''),expected,form.get('anonymized')=='yes');job=0
                    elif action=='run_legal_benchmark410':
                        if form.get('confirm_cost')!='yes':raise Stop('confirmation_cout_evaluation_requise')
                        job=desk_action.enqueue('run_legal_benchmark410',{'models':form.get('models','')},priority=0)
                    elif action=='save_workspace_mapping':
                        from .workstation import save_workspace_mapping
                        save_workspace_mapping(desk_action,form.get('matter',''),form.get('folder_id',''),
                                               form.get('folder_name',''),form.get('folder_url',''));job=0
                    elif action=='save_external_url':
                        from .workstation import save_external_url
                        save_external_url(desk_action,form.get('key',''),form.get('value',''));job=0
                    elif action=='save_local_model':
                        from .preferences363 import save_model
                        save_model(desk_action,form.get('model',''),form.get('role',''));job=0
                    elif action=='save_ai_provider':
                        from .ai_gateway import save_provider
                        save_provider(desk_action,form.get('provider_id',''),form.get('provider_type',''),
                          form.get('url',''),form.get('model',''),form.get('api_key',''),
                          form.get('enabled')=='yes',form.get('allow_external')=='yes',
                          form.get('zdr_required')=='yes',form.get('monthly_budget_usd','0'),
                          form.get('per_request_budget_usd','0'),form.get('input_usd_per_million','0'),
                          form.get('output_usd_per_million','0'));job=0
                    elif action=='test_ai_provider':
                        from .ai_gateway import test_provider
                        test_provider(desk_action,form.get('provider_id',''));job=0
                    elif action=='save_ai_route':
                        from .ai_gateway import save_route
                        save_route(desk_action,form.get('purpose',''),form.get('provider_id',''),form.get('model',''));job=0
                    elif action=='save_hybrid_policy400':
                        from .hybrid400 import save_policy
                        excluded=[x.strip() for x in str(form.get('excluded_matters','')).splitlines() if x.strip()]
                        save_policy(desk_action,form.get('mode','local'),form.get('external_provider','openrouter'),
                          form.get('threshold','65'),form.get('purposes',[]),
                          form.get('external_client_data_approved')=='yes',
                          form.get('fallback_local_on_error')=='yes',
                          form.get('max_external_characters','120000'),
                          form.get('escalate_after_local_failure')=='yes',
                          form.get('anonymize_external')=='yes',excluded);job=0
                    elif action=='simulate_hybrid400':
                        from .hybrid400 import simulate
                        result=simulate(desk_action,form.get('purpose','assistant'),form.get('stage',''),
                          form.get('input_characters','0'),form.get('source_count','0'),
                          form.get('document_count','0'),form.get('max_tokens','3500'))
                        desk_action.setting('ai:hybrid:last_simulation',result);job=0
                    elif action=='preview_hybrid400':
                        from .hybrid400 import preview,store_preview
                        result=preview(desk_action,form.get('purpose','assistant'),form.get('stage','chat'),
                          form.get('text',''),form.get('matter',''),form.get('max_tokens','3500'))
                        form['preview']=store_preview(desk_action,result);job=0
                    elif action=='register_lawve_extension':
                        from .extensions364 import register_item
                        register_item(desk_action,form.get('source_url',''),form.get('name',''),
                          form.get('endpoint',''),form.get('auth_type','none'),form.get('token',''),
                          form.get('allow_external')=='yes',form.get('purposes',[]),form.get('license',''));job=0
                    elif action=='test_lawve_extension':
                        from .extensions364 import test_item
                        test_item(desk_action,form.get('extension_id',''));job=0
                    elif action=='set_lawve_extension':
                        from .extensions364 import set_enabled
                        set_enabled(desk_action,form.get('extension_id',''),form.get('enabled')=='yes',
                          form.get('reviewed')=='yes');job=0
                    elif action=='save_routines520':
                        from .routines520 import save_settings as save_routines
                        save_routines(desk_action,form);job=0
                    elif action=='set_agenda_personal_edit':
                        from .agenda520 import set_enabled as set_personal
                        set_personal(desk_action,form.get('enabled')=='yes');job=0
                    elif action=='save_reminders520':
                        from .reminders520 import save as save_reminders
                        save_reminders(desk_action,form.get('push')=='yes',form.get('email')=='yes');job=0
                    elif action=='save_cabinet_profile':
                        from .preferences363 import save_profile
                        save_profile(desk_action,form.get('name',''),form.get('style',''),form.get('guidance',''));job=0
                    elif action=='save_update_policy420':
                        from .update420 import save_policy
                        save_policy(desk_action,form.get('channel',''),form.get('metadata_url',''));job=0
                    elif action=='save_matter_profile':
                        from .preferences363 import save_matter_profile
                        save_matter_profile(desk_action,form.get('matter',''),form.get('client_name',''),form.get('references',''));job=0
                    elif action=='link_invoice_client':
                        from .workstation import link_invoice_client
                        link_invoice_client(desk_action,form.get('matter',''),form.get('client_id',''));job=0
                    elif action=='approve_cabinet_template':
                        from .cabinet_docs33 import approve_template
                        approve_template(desk_action,form.get('template_id',''),form.get('template_sha256',''),form.get('ack',''))
                        job=0
                    elif action=='automation_setting':
                        # 5.2.1 : réglage appliqué tout de suite ; une demande identique restée en file est retirée.
                        from .operations import perform as automation_perform
                        automation_perform(desk_action,'automation_setting',form)
                        desk_action.db.execute("UPDATE jobs SET status='cancelled',finished=?,result=? WHERE kind='automation_setting' AND status='pending' AND args LIKE ?",
                          (desk_action.now(),json.dumps({'message':'Remplacé par un réglage appliqué immédiatement.'}),'%"key": '+json.dumps(form.get('key',''))+'%'))
                        desk_action.db.commit();job=0
                    else:job=desk_action.enqueue(action,form,priority=0 if action=='review_cabinet_decision' else None)
                    target='/?job='+str(job)
                    if action in ('assistant_ask','delegate_local_task'):
                        target='/assistant?'+urlencode({'job':job,'thread':form.get('thread','')})
                    elif action in ('create_local_task','update_local_task','cancel_local_task','edit_work_task','schedule_work_task','update_work_task'):
                        target='/planning?'+urlencode({'vue':'taches','job':job})
                    elif action in ('routine520','save_routines520'):
                        target='/aujourdhui?'+urlencode({'job':job} if job else {})
                    elif action in ('set_agenda_personal_edit','save_reminders520'):
                        target='/planning?vue=agenda'
                    elif action=='edit_personal_task':
                        target='/planning?'+urlencode({'vue':'taches','job':job})
                    elif action in ('create_agenda_event','edit_agenda_event','cancel_agenda_event','edit_personal_event'):
                        target='/planning?'+urlencode({'vue':'agenda','job':job})
                    elif action in ('prepare_cabinet_letter','prepare_cabinet_revision','create_cabinet_letter','approve_cabinet_template'):
                        target='/modeles-word?'+urlencode({'job':job,'project':form.get('project_id',''),
                                                          'template':form.get('template_id','')})
                    elif action in ('cancel_job','retry_job393','run_system_checks393','test_openrouter393'):
                        if action in ('run_system_checks393','test_openrouter393'):
                            target='/etat-systeme'
                        else:
                            target=('/etat-systeme?'+urlencode({'job':job}) if form.get('back')=='system' or action!='cancel_job'
                                    else '/administration?'+urlencode({'job':job}))
                    elif action in ('save_workspace_mapping','link_invoice_client'):
                        target='/matter?'+urlencode({'id':form.get('matter','')})
                    elif action=='save_external_url':
                        target='/parametres?tab=connexions' if form.get('back')=='settings' else '/administration'
                    elif action in ('associate','remove_contact') and form.get('back')=='contacts':
                        target='/contacts?'+urlencode({'job':job})
                    elif action=='sync_caldav_tasks':
                        target='/planning?'+urlencode({'vue':'taches','job':job})
                    elif action in ('propose_work_plan','approve_work_plan'):
                        target='/planning?'+urlencode({'vue':'organiser','job':job,'proposal':form.get('proposal','')})
                    elif action=='reject_work_plan':
                        target='/planning?vue=organiser'
                    elif action=='reject_document_project':
                        target='/projets'
                    elif action in ('chat','forget_chat','execute_actions'):
                        target='/assistant?'+urlencode({'job':job,'matter':form.get('matter',''),'key':form.get('key','')})
                    elif action in ('browse','register_matter','create_matter','discover','index_all','memory_all'):
                        target='/dossiers?'+urlencode({'job':job,'q':form.get('path','')})
                    elif action in ('save_local_model','save_ai_provider','test_ai_provider','save_ai_route','save_cabinet_profile',
                                    'register_lawve_extension','test_lawve_extension','set_lawve_extension'):
                        target=('/mcp' if 'lawve_extension' in action else '/parametres?'+urlencode({'tab':'cabinet' if action=='save_cabinet_profile' else 'ia'}))
                    elif action in ('save_hybrid_policy400','simulate_hybrid400','preview_hybrid400'):
                        target='/routage-hybride'+(('?'+urlencode({'preview':form.get('preview','')})) if form.get('preview') else '')
                    elif action in ('save_mail_rule370','delete_mail_rule370','record_correction370','set_automation_level370'):
                        target='/regles?'+urlencode({'job':job})
                    elif action=='review_action380':
                        target='/aujourdhui?'+urlencode({'job':job})
                    elif action=='refresh_matter_graph380':
                        target='/matter?'+urlencode({'job':job,'id':form.get('matter','')})
                    elif action in ('start_playbook380','advance_playbook380','complete_playbook_step380'):
                        target='/playbooks?'+urlencode({'job':job,'run':form.get('run_id',''),'matter':form.get('matter','')})
                    elif action in ('save_ecosystem_service380','prepare_ecosystem_action380'):
                        target='/ecosysteme?'+urlencode({'job':job})
                    elif action=='run_business_evaluation380':
                        target='/evaluations?'+urlencode({'job':job})
                    elif action in ('production_cycle390','advance_playbooks390','production_cycle391',
                                     'advance_playbooks391','retry_production391','review_output391'):
                        target='/production?'+urlencode({'job':job})
                    elif action=='studio_prepare420':
                        target='/studio?'+urlencode({'job':job,'matter':form.get('matter',''),
                          'type':form.get('deliverable_kind','')})
                    elif action in ('retry_deliverable420','snapshot_metrics420'):
                        target='/production?'+urlencode({'job':job})
                    elif action=='advance_matter420':
                        target='/matter?'+urlencode({'job':job,'id':form.get('matter','')})
                    elif action=='save_update_policy420':
                        target='/parametres?'+urlencode({'tab':'maintenance','saved':'yes'})
                    elif action=='set_learning_rule392':
                        target='/apprentissage?'+urlencode({'job':job})
                    elif action in ('save_business_rule410','set_business_rule410','trust_document410'):
                        target='/apprentissage?'+urlencode({'job':job})
                    elif action in ('save_legal_benchmark_case410','run_legal_benchmark410'):
                        target='/evaluations?'+urlencode({'job':job})
                    elif action=='save_matter_profile':
                        target='/matter?'+urlencode({'id':form.get('matter','')})
                    elif action in ('refresh_brief','validate_fact','pin_fact','archive_fact','index','retry_matter','associate','remove_contact',
                                  'sync_legal_memory','validate_memory','pin_memory','dispute_memory','archive_memory','resolve_conflict',
                                  'set_matter_state') and form.get('matter'):
                        target='/matter?'+urlencode({'job':job,'id':form.get('matter','')})
                    elif action in ('analyze_strategy','build_matrix','draft_act','validate_strategy','archive_strategy',
                                    'validate_matrix_row','dispute_matrix_row','archive_matrix_row','validate_act','archive_act') and form.get('matter'):
                        target='/strategy?'+urlencode({'job':job,'matter':form.get('matter','')})
                    elif action=='prepare_reply':
                        target='/?'+urlencode({'job':job,'view':'todo'})
                    elif action=='mark_handled' and form.get('back')=='inbox':
                        target='/?'+urlencode({'job':job,'view':'todo'})
                    elif action=='associate' and form.get('back')=='inbox':
                        target='/?'+urlencode({'job':job,'view':'confirm'})
                    elif action=='confirm_matter':
                        target='/?'+urlencode({'job':job,'view':'todo'})
                    elif action=='reject_group':
                        target='/associations?'+urlencode({'job':job})
                    elif action in ('prepare_draft','deposit_draft','attachment_review','deadline_review','confirm_event','confirm_task','ignore_deadline','feedback','add_rule','mark_handled','retry','review') and form.get('key'):
                        target='/mail?'+urlencode({'job':job,'key':form.get('key','')})
                    elif action in ('learn','forget','memory_insight','memory_scope'):
                        target='/memoire?'+urlencode({'job':job})
                    elif action in ('health','daily_digest','automation_setting','organize_cabinet','classify_portfolio'):
                        destination=('/parametres?tab='+('connexions' if action=='health' else 'automatismes')+'&'
                                     if form.get('back')=='settings' else
                                     '/accueil?' if action=='automation_setting' and form.get('back')=='home' else '/dashboard?')
                        target=destination+urlencode({'job':job})
                    elif action=='reconcile_inbox':
                        target='/?'+urlencode({'job':job,'view':'todo'})
                    elif action in ('monitor_all','build_daily_dashboard','ack_signal','snooze_signal','resolve_signal'):
                        target='/surveillance?'+urlencode({'job':job})
                    elif action=='proactive34_now':
                        target='/preparation-proactive?'+urlencode({'job':job})
                    elif action in ('coach_hearing35','prepare_call35','record_call35','calculate35',
                                    'billing_review35','classify_comparable35'):
                        target='/assistance-metier?'+urlencode({'job':job,'matter':form.get('matter','')})
                    elif action in ('autonomy_mail_sweep','review_autonomy_proposal'):
                        target='/projets?'+urlencode({'job':job})
                    elif action in ('orchestrator_mail_sweep','orchestrate_mail','prepare_legal_opinion'):
                        target='/orchestrateur-avis?'+urlencode({'job':job})
                    elif action in ('orchestrator_mail_sweep','orchestrate_mail','prepare_legal_opinion'):
                        target='/orchestrateur-avis?'+urlencode({'job':job})
                    elif action in ('legal_research','verify_official_decision','identify_latest_writings','refresh_exhibit_registry'):
                        target='/recherche-juridique?'+urlencode({'job':job,'matter':form.get('matter','')})
                    elif action=='create_document_files':
                        target='/projets?'+urlencode({'job':job,'project':form.get('project_id','')})
                    elif action in ('identify_party_writings','compare_devices','prepare_hearing','create_hearing_files',
                                    'prepare_word_project','create_word_files','reject_hearing','reject_word_project'):
                        target='/audiences-word?'+urlencode({'job':job,'hearing':form.get('project_id',''),
                          'word':form.get('project_id',''),'matter':form.get('matter','')})
                    elif action in ('refresh_cabinet_pilotage','prepare_meeting','prepare_transcript_report',
                                    'review_cabinet_decision','create_cabinet_confirmation_batch',
                                    'approve_cabinet_confirmation_batch','record_provision',
                                    'run_continuous_business_tests'):
                        target='/pilotage?'+urlencode({'job':job})
                    elif action=='monitor_matter' and form.get('matter'):
                        target='/matter?'+urlencode({'job':job,'id':form.get('matter','')})
                    if live_token:
                        from .live430 import finish_request
                        finish_request(desk_action,live_token,job,target)
                    if env.get('HTTP_HX_REQUEST')=='true':
                        from .live430 import fingerprint
                        payload={'job_id':job,'url':prefix+target,'fingerprint':fingerprint(live_fields)}
                        headers.append(('HX-Trigger',json.dumps({'live-action':payload})))
                        body='Action enregistrée.'
                    else:
                        status='303 See Other';headers.append(('Location',prefix+target));body='Action enregistrée.'
                elif env['REQUEST_METHOD']!='GET':
                    status='405 Method Not Allowed';body='Méthode refusée.'
                elif path=='/openwebui':
                    # A direct route avoids loading the entire dashboard and does
                    # not depend on an iframe policy on the remote application.
                    target=external_links(Desk(cfg)).get('openwebui','')
                    if not target:raise Stop('openwebui_non_configure')
                    status='302 Found';headers.append(('Location',target))
                    body='<p>Ouverture d’Open WebUI.</p>'
                elif re.fullmatch(r'/documents/preview/[a-f0-9]{32}/(?:original|sample|prepared)/(?:[1-9]|1[0-9]|2[0-5])\.png',path):
                    from .cabinet_docs33 import preview_png
                    _,_,_,oid,variant,page=path.split('/')
                    body=preview_png(Desk(cfg),oid,variant,int(page[:-4]));kind='image/png'
                elif path in ('/static/style.css','/static/v15.css','/static/v151.css','/static/v160.css','/static/v170.css','/static/v180.css','/static/v190.css','/static/v210.css','/static/v211.css','/static/v230.css','/static/v300.css','/static/v320.css','/static/v330.css','/static/v360.css','/static/v363.css','/static/v364.css','/static/v365.css','/static/v370.css','/static/v380.css','/static/v390.css','/static/v391.css','/static/v392.css','/static/v393.css','/static/v400.css','/static/v410.css','/static/v420.css','/static/v300.js','/static/v310.js','/static/v320.js','/static/v330.js','/static/v360.js','/static/v363.js','/static/v364.js','/static/v365.js','/static/v370.js','/static/v420.js'):
                    body=(Path(__file__).parent/'static'/path.rsplit('/',1)[-1]).read_text(encoding='utf-8');kind=('text/javascript' if path.endswith('.js') else 'text/css')+'; charset=utf-8'
                elif path in ('/static/v430.js','/static/v430.css','/static/htmx.min.js'):
                    body=(Path(__file__).parent/'static'/path.rsplit('/',1)[-1]).read_text(encoding='utf-8');kind=('text/javascript' if path.endswith('.js') else 'text/css')+'; charset=utf-8'
                elif path in ('/static/axiorhub-icon.png','/favicon.ico','/static/axiorhub-icon-192.png','/static/axiorhub-icon-512.png','/static/axiorhub-icon-maskable.png'):
                    # 5.6.9 : /favicon.ico est demandé d'office par les navigateurs ; 5.6.11 : vraies tailles d'icônes PWA (192, 512, maskable).
                    name='axiorhub-icon.png' if path=='/favicon.ico' else path.rsplit('/',1)[-1]
                    body=(Path(__file__).parent/'static'/name).read_bytes();kind='image/png'
                else:
                    body=self.page(cfg,auth,path,args)
        except Stop as ex:
            status='400 Bad Request'
            if kind.startswith('application/json'):
                body=json.dumps({'error':str(ex)},ensure_ascii=False)
            else:
                body='<h1>Action non effectuée</h1><p>'+e(REASONS.get(str(ex),str(ex).replace('_',' ')))+'</p>'
                if str(ex) in ('conflit_a_examiner','dossier_decline_conflit'):
                    body+='<p><a href="'+e((locals().get('prefix') or '/agent-courriel')+'/conflits')+'">Ouvrir les conflits d’intérêts à examiner</a></p>'
                if str(ex)=='origine_refusee':
                    body+='<p>Ouvrez à nouveau l’interface à son adresse habituelle, rechargez la page (Ctrl+F5), puis relancez le bouton. Ne renvoyez pas l’ancien formulaire.</p>'
                else:body+='<p>Revenez à la page précédente.</p>'
        except Exception:
            status='500 Internal Server Error'
            body=(json.dumps({'error':'interface_indisponible'}) if kind.startswith('application/json')
                  else '<h1>Interface indisponible</h1><p>Vérifiez les services et les fichiers de configuration locaux.</p>')
        raw=body if isinstance(body,bytes) else body.encode('utf-8')
        start_response(status,headers+[('Content-Type',kind),('Content-Length',str(len(raw)))])
        return [raw]

    def page(self,c,auth,path,args):
        prefix=auth.get('prefix','/agent-courriel')
        url=lambda p,**kw:prefix+p+('?' + urlencode(kw) if kw else '')
        def form(action,label,fields=None,extra=''):
            if action=='associate':
                extra+='<label>Références supplémentaires dans les objets<input name="references" placeholder="Ex. CLIENT-A, ADVERSE-A"></label>'
            hidden={'csrf':auth['csrf'],'action':action,**(fields or {})}
            return '<form method="post" enctype="application/x-www-form-urlencoded" action="'+e(url('/action'))+'">'+''.join('<input type="hidden" name="'+e(k)+'" value="'+e(v)+'">' for k,v in hidden.items())+extra+'<button>'+e(label)+'</button></form>'
        def link(p,label,**kw):
            return '<a href="'+e(url(p,**kw))+'">'+e(label)+'</a>'
        desk=Desk(c);matters=load_matters(c);by_id={m['id']:m for m in matters}
        state=State(c['state_dir']);counts=state.counts()
        if path in ('/agenda','/taches'):
            args={**args,'vue':'agenda' if path=='/agenda' else 'taches'};path='/planning'
        title={'/':'Messagerie','/rechercher':'Rechercher','/contacts':'Contacts','/taches':'Tâches','/agenda':'Agenda','/parametres':'Paramètres',
               '/dashboard':'Tableau de bord','/administration':'Administration','/etat-systeme':'État du système','/associations':'Associations proposées',
               '/dossiers':'Dossiers','/memoire':'Mémoire','/qualite':'Qualité','/regles':'Règles et autonomie','/mcp':'Connecteurs MCP',
               '/mail':'Courriel','/matter':'Dossier','/assistant':'Assistant','/strategy':'Stratégie',
               '/surveillance':'Surveillance','/openwebui':'Assistant Open WebUI','/planning':'Agenda et tâches',
               '/projets':'Projets en attente','/recherche-juridique':'Recherche juridique',
               '/audiences-word':'Audiences et Word','/orchestrateur-avis':'Orchestrateur et avis',
               '/pilotage':'Pilotage du cabinet','/accueil':'Tableau de bord · Mon cabinet','/guide':'Guide pratique',
               '/modeles-word':'Documents du cabinet','/aujourdhui':'Aujourd’hui',
               '/production':'Produire','/studio':'Studio de production','/apprentissage':'Apprentissage métier',
               '/playbooks':'Playbooks','/ecosysteme':'Écosystème AxiorHub','/evaluations':'Évaluations métier',
               '/preparation-proactive':'Préparation proactive',
               '/assistance-metier':'Assistance métier','/routage-hybride':'Routage hybride'}.get(path)
        if not title:raise Stop('page_absente')
        from . import shell501
        nav=shell501.nav_html(prefix,path)
        from .workstation import CABINET_SITES, external_links
        cabinet_shortcuts=external_links(desk)
        main=shell501.topbar_html(prefix,title)+('' if path=='/aujourdhui' and not args.get('vue') else  # 5.3.0 : le poste de pilotage a son propre en-tête
             '<div class="page-title"><div><p class="eyebrow">Cabinet · poste de travail</p><h1>'+e(title)+'</h1></div>'+link(path,'Actualiser',**args)+'</div>')
        if args.get('job'):
            try:job_id=int(args['job'])
            except ValueError:raise Stop('operation_invalide') from None
            job=desk.db.execute('SELECT status,result FROM jobs WHERE id=?',(job_id,)).fetchone()
            if not job:raise Stop('operation_absente')
            if job['status'] in ('pending','running','cancel_requested'):
                main+='<p class="notice job-progress" role="status" data-job="'+str(job_id)+'">Demande n° '+str(job_id)+' enregistrée — '+('en attente du worker' if job['status']=='pending' else 'traitement en cours')+'. Le suivi se met à jour automatiquement ; vos champs ne seront pas effacés.</p>'
            elif job['status']=='error':
                try:error=json.loads(job['result'] or '{}').get('erreur','action_interrompue')
                except ValueError:error='action_interrompue'
                main+='<p class="notice">Action non terminée : '+e(REASONS.get(error,error))+'</p>'
            else:
                try:result=json.loads(job['result'] or '{}')
                except ValueError:result={}
                message=result.get('message')
                if result.get('brouillon_imap')=='verifie':message='1 brouillon enregistré et relu dans '+c['mail']['drafts']+'.'
                elif result.get('brouillon_imap')=='cree':message='1 brouillon ajouté dans '+c['mail']['drafts']+'.'
                elif result.get('association')=='enregistree':message='Association confirmée pour le dossier '+str(result.get('dossier',''))+'.'
                elif result.get('correspondant')=='retire_du_registre_web':message='Correspondant retiré du dossier indiqué.'
                elif result.get('etat')=='traite_sans_reponse':message='Message classé sans réponse.'
                elif result.get('association')=='enregistree':message='Association confirmée. Les courriels non lus concernés vont être réanalysés.'
                main+='<p class="success">'+e(message or 'Action terminée.')+'</p>'
                created=result.get('created_files',[]) if isinstance(result,dict) else []
                if created:
                    main+='<section class="notification-ready"><h3>Nouveaux fichiers créés sans écrasement</h3><ul>'
                    for item in created:
                        edit=str(item.get('edit_url',''))
                        label=e(str(item.get('path','')).rsplit('/',1)[-1])
                        if urlsplit(edit).scheme=='https':
                            main+='<li>'+label+' — <a href="'+e(edit)+'" target="_blank" rel="noopener noreferrer">Modifier dans OnlyOffice depuis Nextcloud</a></li>'
                        else:main+='<li>'+label+' — ouvrir le dossier Nextcloud pour le modifier.</li>'
                    main+='</ul><p>OnlyOffice enregistre dans le même fichier Nextcloud nouvellement créé. Les versions antérieures n’ont pas été écrasées.</p></section>'
        if path=='/rechercher':
            from .workstation32_ui import search_page
            main+=search_page(desk,args,link,url)
        elif path=='/contacts':
            from .workstation32_ui import contacts_page
            main+=contacts_page(desk,args,form,link,url)
        elif path=='/taches':
            from .workstation32_ui import tasks_page
            main+=tasks_page(desk,link,form)
        elif path=='/parametres':
            from .workstation32_ui import settings_page
            from .interface410_ui import settings_hub
            main+=settings_hub(link)+settings_page(desk,args,form,link)
        elif path=='/regles':
            from .relevance370_ui import rules_page
            main+=rules_page(desk,matters,form)
        elif path=='/mcp':
            from .relevance370_ui import mcp_page
            main+=mcp_page(desk,form)
        elif path=='/aujourdhui':
            if args.get('vue')=='detail':
                from .production420_ui import today_page
                main+='<p><a href="'+e(url('/aujourdhui'))+'">← Retour au cockpit</a></p><div id="ws-live-board">'+today_page(desk,form,link)+'</div>'
                from .echeances450 import today_block
                main+=today_block(desk,url('/echeances'))
            elif args.get('vue')=='cockpit':
                from .web460 import cockpit_html
                main+='<p><a href="'+e(url('/aujourdhui'))+'">← Retour à l’essentiel du jour</a></p>'+cockpit_html(desk,prefix)
            elif args.get('vue')=='essentiel':
                from .web520 import today_html
                main+='<p><a href="'+e(url('/aujourdhui'))+'">← Retour au poste de pilotage</a></p>'+today_html(desk,prefix,form)
            else:
                # 5.3.0 : poste de pilotage (conversation avec l'agent, à relire, activité, journée Nextcloud).
                from .cockpit530 import page as cockpit_page
                main+=(cockpit_page(desk,prefix,owner=str(auth.get('owner') or 'cabinet'))+'<div id="ax-toast" role="status" aria-live="polite"></div>'
                       '<script defer src="'+e(prefix)+'/static/v500.js"></script><script defer src="'+e(prefix)+'/static/v530.js"></script>'
                       '<script defer src="'+e(prefix)+'/static/v550.js"></script><script defer src="'+e(prefix)+'/static/v569.js"></script>')
        elif path=='/studio':
            from .production420_ui import studio_page
            main+=studio_page(desk,args,form,link)
        elif path=='/production':
            if args.get('output'):
                from .experience392_ui import review_panel
                main+=review_panel(desk,args.get('output'),form,link)
            else:
                from .production420_ui import production_page
                from .conflicts500 import banner_html
                main+=banner_html(desk,prefix)+production_page(desk,args,form,link)
        elif path=='/apprentissage':
            from .learning410_ui import page as learning_page
            main+=learning_page(desk,form)
        elif path=='/etat-systeme':
            from .reliability393_ui import page as reliability_page
            main+=reliability_page(desk,args,form,link,REASONS,JOB_LABELS)
        elif path=='/routage-hybride':
            from .hybrid400_ui import page as hybrid_page
            main+=hybrid_page(desk,args,form)
        elif path=='/playbooks':
            from .operating380_ui import playbooks_page
            main+=playbooks_page(desk,matters,args,form,link)
        elif path=='/ecosysteme':
            from .operating380_ui import ecosystem_page
            main+=ecosystem_page(desk,matters,form)
        elif path=='/evaluations':
            from .evaluation410_ui import page as evaluation_page
            main+=evaluation_page(desk,form)
        elif path=='/modeles-word':
            from .cabinet_docs33_ui import page as cabinet_word_page
            main+=cabinet_word_page(desk,args,form,link,url)
        elif path=='/accueil':
            main+='<p class="notice">'+link('/assistance-metier','Ouvrir plaidoirie, simulations, appels, calculs et facturation préparée')+'</p>'
            main+='<p class="notice">'+link('/preparation-proactive','Voir le briefing de 7 h 30 et les préparations J−14')+'</p>'
            main+='<section class="ws-sites"><h2>Sites et outils du cabinet</h2><div class="ws-cards">'
            for key,label,_ in CABINET_SITES:
                if cabinet_shortcuts.get(key):
                    main+='<article><h3>'+e(label)+'</h3><a href="'+e(cabinet_shortcuts[key])+'" target="_blank" rel="noopener noreferrer">Ouvrir le site ↗</a></article>'
            main+='</div></section>'
            from .workstation32_ui import home as home32
            from .guided_ui import home as previous_home
            main+=home32(desk,form,link)+previous_home(desk,form,link,url)+'</details>'
        elif path in ('/guide','/dashboard','/pilotage'):
            from .guided_ui import home
            main+=home(desk,form,link,url)
        if path=='/dashboard':
            main+='<details class="advanced-panel"><summary>Tableau détaillé et indicateurs</summary>'
            from .integration import dashboard
            from .proactive import latest_dashboard
            daily=latest_dashboard(desk)
            info=dashboard(desk);wc=info['counts']
            pc=info['portfolio']['counts']
            main+='<section class="daily-hero portfolio-hero"><div><p class="eyebrow">VOTRE JOURNÉE</p><h2>'+e(daily['day'])+'</h2><p>Seuls les dossiers actifs alimentent vos priorités. L’indexation d’une archive ne la réactive jamais.</p></div>'+form('organize_cabinet','✨ Organiser automatiquement mon cabinet',{'restart':'yes'})+'</section>'
            main+='<div class="portfolio-stats"><a href="'+e(url('/dossiers',state='active'))+'"><strong>'+str(pc.get('active',0))+'</strong><span>🟢 Actifs</span></a><a href="'+e(url('/dossiers',state='dormant'))+'"><strong>'+str(pc.get('dormant',0))+'</strong><span>🟡 En sommeil</span></a><a href="'+e(url('/dossiers',state='archived'))+'"><strong>'+str(pc.get('archived',0))+'</strong><span>⚪ Archivés</span></a><a href="'+e(url('/dossiers',state='to_confirm'))+'"><strong>'+str(pc.get('to_confirm',0))+'</strong><span>🟠 À confirmer</span></a></div>'
            main+='<p class="summary-line"><strong>'+str(info['portfolio']['automatic_mail_links'])+' courriels associés automatiquement</strong> · <strong>'+str(info['portfolio']['grouped_confirmations'])+' confirmations groupées nécessaires</strong></p>'
            today=daily.get('today',{})
            main+='<section class="today-summary"><h2>☀️ Aujourd’hui</h2><p><strong>'+str(today.get('mail_needing_reply',0))+'</strong> courriel(s) nécessitent probablement une réponse · <strong>'+str(today.get('drafts_prepared',0))+'</strong> brouillon(s) préparé(s) · <strong>'+str(today.get('deadlines_within_7_days',0))+'</strong> échéance(s) sous sept jours · <strong>'+str(today.get('association_groups_to_confirm',0))+'</strong> association(s) groupée(s) à confirmer · <strong>'+str(today.get('commercial_or_automatic_ignored',0))+'</strong> notification(s) ignorée(s).</p></section>'
            if daily.get('recommended_actions'):
                main+='<h2>🎯 Action recommandée</h2><ul class="recommended-actions">'
                for action in daily['recommended_actions'][:6]:
                    target=link('/matter',action.get('matter_name') or action.get('matter'),id=action['matter']) if action.get('matter') else ''
                    main+='<li><strong>'+e(action.get('reason','À examiner'))+'</strong> — '+e(action.get('label','Examiner.'))+(' · '+target if target else '')+'</li>'
                main+='</ul>'
            if daily['priorities']:
                main+='<h2>🚨 Priorités à examiner</h2><div class="priority-list">'
                for signal in daily['priorities'][:8]:
                    main+='<article class="signal '+e(signal['severity'])+'"><div><small>'+e(signal['severity'].upper())+' · '+e(signal['matter_name'])+'</small><h3>'+e(signal['title'])+'</h3><p>'+e(signal['detail'])+'</p>'+link('/matter','Ouvrir le dossier',id=signal['matter'])+'</div><div class="signal-actions">'+form('ack_signal','✓ Vu',{'signal':signal['id']})+form('snooze_signal','⏰ Demain',{'signal':signal['id'],'hours':'24'})+form('resolve_signal','✓ Résolu',{'signal':signal['id']})+'</div></article>'
                main+='</div>'
            else:main+='<p class="success">Aucun signal prioritaire ouvert dans les données surveillées.</p>'
            cards=[('needs_action','Courriels à traiter','📬'),('needs_confirmation','Décisions à confirmer','❓'),
                   ('draft_ready','Brouillons prêts','✍️'),('processing','Actions en cours','⏳')]
            main+='<div class="stats">'+''.join('<article><strong>'+str(wc.get(k,0))+'</strong><span>'+icon+' '+e(label)+'</span></article>' for k,label,icon in cards)+'</div>'
            main+='<h2>✨ Actions proposées</h2><div class="dashboard-actions">'
            for item in info['proposed_actions']:
                target=(url('/strategy') if ('matrices' in item['label'] or 'projets d’actes' in item['label']) else
                        url('/dossiers') if ('informations' in item['label'] or 'contradictions' in item['label']) else
                        url('/',view='confirm' if 'dossiers proposés' in item['label'] else ('drafts' if 'brouillons' in item['label'] else 'todo')))
                main+='<a class="action-card" href="'+e(target)+'"><strong>'+str(item['count'])+'</strong><span>'+e(item['label'])+'</span></a>'
            main+='</div><h2>🗂️ Dossiers récemment actifs</h2>'
            if info['recent_matters']:
                for item in info['recent_matters']:
                    main+='<article class="matter-row"><div><h3>'+link('/matter',item['name']+' · '+item['id'],id=item['id'])+'</h3><p>'+e(item['summary'] or 'Synthèse non encore préparée.')+'</p>'+link('/strategy','Ouvrir l’espace stratégique',matter=item['id'])+'</div><small>'+e(date(item['last_activity']))+' · '+str(item['documents'])+' document(s) · '+str(item['open_tasks'])+' tâche(s) · '+str(item['memory_to_confirm'])+' information(s) à confirmer · '+str(item['memory_conflicts'])+' contradiction(s) · '+str(item['strategy_count'])+' analyse(s) · '+str(item['act_projects'])+' projet(s) d’acte</small></article>'
            else:main+='<p class="empty">Aucun dossier actif. Lancez l’organisation automatique ou activez manuellement un dossier.</p>'
            main+='<h2>💬 Discussions récentes</h2>'
            if info['conversations']:
                main+=''.join('<p>'+link('/assistant',x['title'],thread=x['id'])+' · '+e(date(x['updated']))+'</p>' for x in info['conversations'])
            else:main+='<p class="empty">Aucune discussion. Vous pouvez interroger immédiatement tout le cabinet dans Assistant.</p>'
            if info['jobs']:
                main+='<p class="notice">'+str(len(info['jobs']))+' opération(s) active(s). Les demandes de réponse et les questions passent avant l’indexation.</p>'
        elif path=='/planning':
            vue=args.get('vue','')
            if vue not in ('agenda','taches','organiser'):vue='organiser' if (args.get('proposal') or args.get('job')) else 'agenda'
            main+='<nav class="ws-tabs" aria-label="Rubriques de l’agenda">'+''.join(
                '<a'+(' aria-current="page"' if vue==key else '')+' href="'+e(url('/planning',vue=key))+'">'+e(label)+'</a>'
                for key,label in (('agenda','Agenda'),('taches','Tâches'),('organiser','Organiser la semaine')))+'</nav>'
            from .workplan import get_plan,list_tasks
            workflow=c.get('nextcloud_workflow',{})
            if workflow.get('enabled'):
                main+='<p class="success">✅ Compte Nextcloud technique actif : '+e(workflow.get('username',''))+'. Les écritures sont limitées aux calendriers choisis.</p>'
            else:
                main+='<p class="notice">⚠️ L’agenda reste lisible, mais la création de tâches et de créneaux est désactivée. Exécutez <code>sudo python3 configure-nextcloud-workflow.py</code> après avoir partagé les calendriers avec le compte technique.</p>'
            if vue=='agenda':
                from .agenda36 import page as agenda_page
                main+=agenda_page(desk,args,link,url,form)
            elif vue=='taches':
                from .workstation32_ui import tasks_page
                try:last_sync=desk.db.execute('SELECT MAX(synced) FROM work_tasks_v211').fetchone()[0] or ''
                except Exception:last_sync=''
                main+=('<section class="ws-heading"><h2>Tâches Nextcloud</h2><p>Dernière synchronisation : <strong>'+(e(date(last_sync)) if last_sync else 'jamais')+
                  '</strong>. Seules les tâches créées par AxiorHub peuvent être modifiées ici ; les autres listes choisies à la configuration restent en lecture seule.</p>'+
                  form('sync_caldav_tasks','↻ Actualiser depuis Nextcloud')+'</section>')
                main+=tasks_page(desk,link,form)
            elif vue=='organiser':
                today=datetime.now(timezone.utc).date();finish=today+timedelta(days=7)
                main+='<section class="daily-hero"><div><h2>📅 Organiser une semaine réaliste</h2><p>Collez votre liste. AxiorHub lit tous les événements de la période, repère les dossiers et les dépendances, puis propose un programme sans rien créer.</p><p>Après relecture, une confirmation groupée permet de créer ce programme dans Nextcloud.</p></div></section>'
                main+=form('propose_work_plan','✨ Proposer le programme',extra=
                  '<label>Liste des tâches<textarea required name="task_text" rows="12" maxlength="20000" placeholder="- MARTINEAU : actualiser les conclusions (2h30)\n- NLD : répondre au mail client (1h30)"></textarea></label>'
                  '<div class="planning-fields"><label>Du<input type="date" required name="period_start" value="'+today.isoformat()+'"></label>'
                  '<label>Au (inclus)<input type="date" required name="period_end" value="'+finish.isoformat()+'"></label>'
                  '<label>Charge maximale quotidienne<input type="number" min="120" max="480" step="30" name="max_daily_minutes" value="'+str(c.get('planning',{}).get('max_daily_minutes',360))+'"></label></div>')
                proposal_id=args.get('proposal','');proposal_code='';proposal_result=None
                if args.get('job'):
                    try:
                        jrow=desk.db.execute('SELECT status,result FROM jobs WHERE id=?',(int(args['job']),)).fetchone()
                        if jrow and jrow['status']=='done':
                            parsed=json.loads(jrow['result'] or '{}')
                            if parsed.get('proposal_id'):
                                proposal_id=parsed['proposal_id'];proposal_code=parsed.get('confirmation_code','')
                    except (ValueError,TypeError):pass
                if proposal_id:
                    try:proposal_result=get_plan(desk,proposal_id)
                    except Stop:proposal_result=None
                if proposal_result:
                    pdata=proposal_result['data'];main+='<section class="plan-review"><h2>Programme proposé</h2><p><strong>'+str(len(pdata['tasks']))+' tâche(s)</strong> · <strong>'+str(len(pdata['slots']))+' créneau(x)</strong> · charge maximale '+str(pdata['max_daily_minutes'])+' min/jour.</p>'
                    ac=pdata.get('agenda_counts',{});main+='<p>Agenda contrôlé : '+str(ac.get('all',0))+' événement(s), dont '+str(ac.get('unlinked',0))+' sans dossier identifié. Ces événements ont tout de même été réservés.</p>'
                    if pdata.get('daily_minutes'):
                        main+='<div class="week-load">'+''.join('<article><strong>'+e(day)+'</strong><span>'+str(minutes)+' min planifiées</span></article>' for day,minutes in pdata['daily_minutes'].items())+'</div>'
                    for task in pdata['tasks']:
                        label=(task['matter'] or ('Personnel' if task['category']=='personal' else 'Sans dossier'))
                        main+='<article class="plan-task"><strong>'+str(task['position'])+'. '+e(task['title'])+'</strong><small>'+e(label)+' · '+str(task['duration_minutes'])+' min · '+e(task['action_type'])+'</small>'
                        if task['blocked_by']:main+='<p class="notice">Bloquée : '+e(task['blocked_by'])+'</p>'
                        if task['forbidden_steps']:main+='<p>Action externe non exécutée : '+e(', '.join(task['forbidden_steps']))+'.</p>'
                        slots=[x for x in pdata['slots'] if x['position']==task['position']]
                        if slots:main+='<ul>'+''.join('<li>'+e(date(x['start']))+' → '+e(date(x['end']))+'</li>' for x in slots)+'</ul>'
                        main+='</article>'
                    if pdata.get('unscheduled'):
                        main+='<p class="notice">'+str(len(pdata['unscheduled']))+' tâche(s) ou partie(s) non planifiée(s), faute de capacité ou en raison d’un blocage.</p>'
                    if proposal_result['status']=='pending':
                        if proposal_code:main+='<p class="approval-code">Code de confirmation : <strong>'+e(proposal_code)+'</strong></p>'
                        main+='<div class="actions">'+form('approve_work_plan','✅ Créer ce programme dans Nextcloud',{'proposal':proposal_id},'<label>Recopier le code à six chiffres<input required name="confirmation_code" pattern="[0-9]{6}" maxlength="6"></label>')+form('reject_work_plan','Refuser le programme',{'proposal':proposal_id})+'</div>'
                    else:main+='<p class="success">État du programme : '+e(proposal_result['status'])+'.</p>'
                    main+='</section>'
                main+='<section><div class="section-heading"><div><h2>✅ Tâches synchronisées</h2><p>Les tâches créées par AxiorHub peuvent être mises à jour ici. Les autres restent en lecture seule.</p></div>'+form('sync_caldav_tasks','↻ Actualiser depuis Nextcloud')+'</div>'
                rows=list_tasks(desk,'todo,in_progress,blocked','',100)
                for task in rows:
                    main+='<article class="task-row"><div><strong>'+e(task['title'])+'</strong><small>'+e(task['matter'] or 'Sans dossier')+' · '+e(task['status'])+' · '+e(date(task['due']))+'</small></div>'
                    if task['external_uid'].startswith('axiorhub-'):
                        main+='<div class="actions">'+form('update_work_task','Commencer',{'task':task['id'],'status':'in_progress'})+form('update_work_task','Terminer',{'task':task['id'],'status':'completed'})+'</div>'
                    main+='</article>'
                if not rows:main+='<p class="empty">Aucune tâche synchronisée pour le moment.</p>'
                main+='</section>'
        elif path=='/pilotage':
            from .cabinet_pilot import dashboard as cabinet_dashboard
            pilot=cabinet_dashboard(desk,args.get('state','pending'),300)
            risk_labels={'critical':'Critique','high':'Élevé','medium':'Moyen','low':'Faible'}
            main+='<section class="daily-hero"><div><p class="eyebrow">VERSION 3.4.0 · AUTONOMIE CONTRÔLÉE</p><h2>Décisions de l’avocat</h2><p>Facturation préparée, travaux potentiellement non facturés, rendez-vous, charge, provisions et dossiers inactifs sont réunis ici. Aucune facture définitive, relance, écriture, invitation, envoi ou dépôt automatique.</p></div><div class="actions">'+form('refresh_cabinet_pilotage','↻ Actualiser les détections')+form('run_continuous_business_tests','✓ Exécuter les tests métier')+'</div></section>'
            main+='<div class="stats">'+''.join('<article><strong>'+str(pilot['counts'][risk])+'</strong><span>'+e(risk_labels[risk])+'</span></article>' for risk in ('critical','high','medium','low'))+'</div>'
            if args.get('job'):
                try:
                    result_row=desk.db.execute('SELECT status,result FROM jobs WHERE id=?',(int(args['job']),)).fetchone()
                    result_data=json.loads(result_row['result'] or '{}') if result_row and result_row['status']=='done' else {}
                    if result_data.get('confirmation_code'):
                        main+='<section class="confirmation-panel"><h3>Lot prêt à confirmer</h3><p class="approval-code">Code à recopier : <strong>'+e(result_data['confirmation_code'])+'</strong></p>'+form('approve_cabinet_confirmation_batch','Confirmer ce lot',{'batch_id':result_data['batch_id']},'<label>Recopier le code à six chiffres<input required name="confirmation_code" pattern="[0-9]{6}" maxlength="6"></label>')+'</section>'
                except (ValueError,TypeError,json.JSONDecodeError):pass
            eligible=[x['id'] for x in pilot['decisions'] if x['risk'] in ('low','medium')]
            if eligible:main+='<section><h2>Confirmation groupée</h2><p>Ce lot contient uniquement les décisions faibles et moyennes actuellement affichées. Son empreinte sera revérifiée au moment de la confirmation.</p>'+form('create_cabinet_confirmation_batch','Préparer le lot',{'decision_ids':','.join(eligible)})+'</section>'
            main+='<section><h2>File unique</h2>'
            for item in pilot['decisions']:
                matter_name=matter_display(by_id[item['matter']]) if item['matter'] in by_id else (item['matter'] or 'Cabinet')
                fields={'decision_id':item['id']}
                approve_label=('Préparer ce rendez-vous' if item['action_kind']=='prepare_meeting' else
                  'Accepter cette estimation — aucune facture créée' if item['action_kind']=='review_billing' else
                  'Prendre connaissance — aucune action externe')
                approve_fields=fields|{'status':'approved','confirm_risk':'yes' if item['risk'] in ('high','critical') else 'no'}
                main+='<article class="proposal-row"><div><small>'+e(risk_labels[item['risk']].upper())+' · '+e(item['action_kind'])+' · '+e(matter_name)+'</small><h3>'+e(item['title'])+'</h3><p>'+e(item['summary'])+'</p><details><summary>Données et garde-fous</summary>'+block(json.dumps(item['payload'],ensure_ascii=False,indent=2))+'</details></div><div class="actions">'+form('review_cabinet_decision',approve_label,approve_fields)+form('review_cabinet_decision','Me le rappeler dans 24 h',fields|{'status':'snoozed','snooze_hours':'24'})+form('review_cabinet_decision','Rejeter / écarter cette proposition',fields|{'status':'rejected'})+'</div></article>'
            if not pilot['decisions']:main+='<p class="empty">Aucune décision dans cet état.</p>'
            main+='</section><section><h2>Suivi des provisions</h2><p class="notice">Registre interne sourcé uniquement : aucun paiement, aucune facture et aucune relance automatique.</p>'+form('record_provision','Enregistrer la provision',extra='<label>Dossier exact<input required name="matter" maxlength="80"></label><label>Libellé<input required name="label" maxlength="500"></label><label>Montant demandé en centimes<input required type="number" min="1" name="requested_cents"></label><label>Montant reçu en centimes<input type="number" min="0" name="paid_cents" value="0"></label><input type="hidden" name="currency" value="EUR"><label>Échéance ISO avec fuseau<input required name="due" placeholder="2026-10-01T00:00:00+02:00"></label><label>Source exacte<input required name="source_ref" maxlength="2000"></label>')
            for provision in pilot['provisions'][:30]:main+='<article class="proposal-row"><div><small>'+e(provision['status'])+' · '+e(provision['matter'])+' · '+e(provision['due'])+'</small><h3>'+e(provision['label'])+'</h3><p>'+format(provision['paid_cents']/100,'.2f')+' / '+format(provision['requested_cents']/100,'.2f')+' '+e(provision['currency'])+'</p></div></article>'
            main+='</section><section><h2>Préparations ciblées</h2>'+form('prepare_meeting','Préparer le rendez-vous',extra='<label>Identifiant exact de l’événement<input required name="event_id" pattern="[a-f0-9]{64}"></label>')+form('prepare_transcript_report','Préparer le compte rendu',extra='<label>Dossier exact<input required name="matter" maxlength="80"></label><label>Source indexée (préférée)<input name="source_ref" maxlength="2000"></label><label>Ou transcription à contrôler<textarea name="transcript_text" maxlength="100000" rows="8"></textarea></label>')+'</section>'
            test=pilot.get('last_business_test');main+='<section><h2>Contrôles continus et audit</h2><p>Chaîne d’audit : <strong>'+('valide' if pilot['audit']['valid'] else 'ROMPUE')+'</strong> · '+str(pilot['audit']['count'])+' événement(s).</p>'
            if test:
                try:test_data=json.loads(test['data']);main+='<details><summary>Dernier rapport : '+e(test['status'])+'</summary>'+block(json.dumps(test_data,ensure_ascii=False,indent=2))+'</details>'
                except (ValueError,TypeError):pass
            main+='</section>'
        elif path=='/orchestrateur-avis':
            from .orchestrator import notifications,preview as orchestration_preview
            from .opinions import list_projects,preview as opinion_preview
            notices=notifications(desk,'all',100)['notifications'];opinions=list_projects(desk,status='all',limit=100)['projects']
            main+='<section class="daily-hero"><div><p class="eyebrow">VERSION 2.6.0 · SUPERVISÉE</p><h2>Orchestrateur courriel–dossier</h2><p>Un courriel, une analyse différentielle et une seule notification. Les réponses, diligences, facturations et projets restent des propositions internes.</p></div>'+form('orchestrator_mail_sweep','↻ Analyser les nouveaux courriels',{'limit':'20'})+'</section>'
            main+='<div class="stats"><article><strong>'+str(sum(x['status']=='unread' for x in notices))+'</strong><span>Notifications à lire</span></article><article><strong>'+str(sum(x['orchestration_status']=='blocked' for x in notices))+'</strong><span>Associations ou analyses bloquées</span></article><article><strong>'+str(sum(x['status']=='pending_review' for x in opinions))+'</strong><span>Avis à contrôler</span></article><article><strong>'+str(sum(x['status']=='blocked' for x in opinions))+'</strong><span>Avis bloqués</span></article></div>'
            selected=args.get('orchestration','')
            if selected:
                try:
                    shown=orchestration_preview(desk,selected);main+='<section><h2>Notification consolidée</h2><article class="project-preview"><small>'+e(shown['status'])+' · '+e(shown['matter'])+'</small><h3>'+e(shown['differential'].get('summary') or shown['classification'].get('reason',''))+'</h3><p><strong>Projet adapté :</strong> '+e(shown['triggered_project_kind'] or 'aucun')+'</p><p><strong>Réponse client proposée :</strong></p>'+block(shown['reply_proposal'].get('body','') or 'Aucune.')+'<details><summary>Analyse différentielle et propositions</summary>'+block(json.dumps({'changes':shown['differential'].get('changes',[]),'diligences':shown['diligence_proposals'],'billing':shown['billing_proposals'],'blockers':shown['differential'].get('blocking_reasons',[])},ensure_ascii=False,indent=2))+'</details><p class="notice">Aucun envoi, document, temps définitif, facture ou événement n’a été créé.</p></article></section>'
                except Stop:main+='<p class="notice">Analyse indisponible.</p>'
            main+='<section><h2>Notifications courriel–dossier</h2>'
            for item in notices:
                main+='<article class="proposal-row"><div><small>'+e(item['status'])+' · '+e(item['orchestration_status'])+' · '+e(date(item['updated']))+'</small><h3>'+e(item['title'])+'</h3><p>'+e(item['summary'])+'</p></div><div class="actions">'+link('/orchestrateur-avis','Prévisualiser',orchestration=item['orchestration_id'])+'</div></article>'
            if not notices:main+='<p class="empty">Aucune notification consolidée.</p>'
            main+='</section><section><h2>Préparer un avis et des simulations</h2>'+form('prepare_legal_opinion','Préparer sans action externe',extra='<label>Identifiant exact du dossier<input required name="matter" maxlength="80"></label><label>Question juridique<textarea required name="question" maxlength="8000" rows="5"></textarea></label><input type="hidden" name="providers" value="openlegi,goodlegal,pappers"><input type="hidden" name="run_research" value="yes"><label>Critiquer une décision de première instance ?<select name="critique_first_instance"><option value="no">Non</option><option value="yes">Oui</option></select></label><label>Chemin exact du jugement ou de l’ordonnance si oui<input name="judgment_source" maxlength="2000"></label>')+'</section>'
            selected_opinion=args.get('opinion','')
            if selected_opinion:
                try:
                    shown=opinion_preview(desk,selected_opinion);main+='<section><h2>Avis et simulations</h2><article class="project-preview"><small>'+e(shown['status'])+' · '+e(shown['matter']['id'])+'</small><h3>'+e(shown['opinion']['title'])+'</h3><p>'+e(shown['opinion']['executive_summary'])+'</p><p><strong>Contrôle :</strong> '+e(shown['control']['status'])+'</p><details><summary>Projet complet, scénarios et sources</summary>'+block(json.dumps({'opinion':shown['opinion'],'control':shown['control'],'sources':shown['sources'],'research':shown.get('research'),'case_law_comparison':shown.get('case_law_comparison'),'improvement_actions':shown.get('improvement_actions',[])},ensure_ascii=False,indent=2))+'</details><p class="notice">Calibration qualitative seulement. Ce projet ne prédit pas la décision et requiert la validation de l’avocat.</p></article></section>'
                except Stop:main+='<p class="notice">Projet d’avis indisponible.</p>'
            main+='<section><h2>Projets d’avis récents</h2>'
            for item in opinions:
                main+='<article class="proposal-row"><div><small>'+e(item['status'])+' · '+e(date(item['updated']))+'</small><h3>'+e(item['matter'])+'</h3><p>'+e(item['question'])+'</p></div><div class="actions">'+link('/orchestrateur-avis','Prévisualiser',opinion=item['id'])+'</div></article>'
            if not opinions:main+='<p class="empty">Aucun projet d’avis.</p>'
            main+='</section>'
        elif path=='/projets':
            from .autonomy import pending_dashboard
            from .document_projects import preview as document_preview
            pending=pending_dashboard(desk);counts_pending=pending['counts']
            main+='<section class="daily-hero autonomy-hero"><div><p class="eyebrow">AUTONOMIE SUPERVISÉE</p><h2>Travail préparé automatiquement</h2><p>AxiorHub surveille, indexe et propose. Aucun fichier, courriel, événement, paiement ou facture n’est créé depuis cette page.</p></div>'+form('autonomy_mail_sweep','↻ Analyser les nouveaux courriels')+'</section>'
            main+='<div class="stats"><article><strong>'+str(counts_pending['document_projects'])+'</strong><span>Projets documentaires</span></article><article><strong>'+str(counts_pending['diligences'])+'</strong><span>Diligences proposées</span></article><article><strong>'+str(counts_pending['billing'])+'</strong><span>Facturations proposées</span></article><article><strong>'+str(counts_pending['blocked_controls'])+'</strong><span>Contrôles bloquants</span></article></div>'
            main+='<section><h2>📄 Prévisualisations documentaires</h2>'
            selected=args.get('project','')
            if selected:
                try:
                    shown=document_preview(desk,selected)
                    main+='<article class="project-preview"><small>'+e(shown['status'])+' · '+e(shown['document_type'])+'</small><h3>'+e(matter_display(by_id.get(shown['matter'].get('id'),shown['matter'])))+'</h3><p><strong>Source :</strong> '+e(shown['source_file']['path'])+'</p><p><strong>Destination :</strong> '+e(shown['destination_folder'])+'</p><p><strong>Fichiers futurs :</strong></p><ul>'+''.join('<li>'+e(x)+'</li>' for x in shown['future_files'])+'</ul>'
                    control=shown.get('control',{});main+='<p class="'+('success' if control.get('status')=='approved_for_confirmation' else 'notice')+'"><strong>Contrôle indépendant :</strong> '+e(control.get('status','non exécuté'))+' · score '+str(control.get('score',0))+' %.</p>'
                    if control.get('blocking_reasons'):main+='<p class="notice">Blocages : '+e(' · '.join(control['blocking_reasons']))+'</p>'
                    main+='<details><summary>Arguments proposés et sources</summary>'+block('\n\n'.join(x['heading']+'\n'+x['body']+'\nSources : '+', '.join(x['source_ids']) for x in shown['proposed_arguments']))+'</details></article>'
                    pending_item=next((x for x in pending['projects'] if x['id']==shown['project_id']),{})
                    if (shown['status']=='pending' and
                        control.get('status')=='approved_for_confirmation' and
                        pending_item.get('confirmation_code')):
                        main+='<section class="confirmation-panel"><h3>Création supervisée</h3><p>Source et destination exactes affichées ci-dessus. La création produit seulement de nouveaux fichiers.</p><p class="approval-code">Code à recopier : <strong>'+e(pending_item['confirmation_code'])+'</strong></p>'+form('create_document_files','Créer les nouveaux fichiers',{'project_id':shown['project_id'],'source_path':shown['source_file']['path'],'destination_folder':shown['destination_folder']},'<label>Recopier le code à six chiffres<input required name="confirmation_code" pattern="[0-9]{6}" maxlength="6"></label>')+'</section>'
                    if shown['status'] in ('pending','blocked'):
                        main+='<div class="actions">'+form('reject_document_project','Rejeter ce projet',{'project_id':shown['project_id']})+'</div>'
                except Stop:main+='<p class="notice">Cette prévisualisation n’est plus disponible.</p>'
            if pending['projects']:
                for item in pending['projects']:
                    badge='automatique' if item['automatic'] else 'demandé';control=item.get('control',{})
                    confidence=(' · confiance dossier '+str(item.get('confidence',0))+' %' if item['automatic'] else '')
                    main+='<article class="proposal-row"><div><small>'+e(badge)+' · '+e(item['status'])+' · '+e(item['document_type'])+e(confidence)+'</small><h3>'+e(item['matter_name'])+' — '+e(item.get('action_proposed',''))+'</h3><p><strong>Déclencheur :</strong> '+e(item.get('trigger_source',''))+'</p><p><strong>Source de base :</strong> '+e(item['source_path'])+'</p><p><strong>Fichiers futurs :</strong> '+str(len(item.get('future_files',[])))+' · <strong>Contrôle :</strong> '+e(control.get('status','non exécuté'))+' · '+str(control.get('score',0))+' %</p></div><div class="actions">'+link('/projets','Prévisualiser',project=item['id'])+form('reject_document_project','Rejeter',{'project_id':item['id']})+'</div></article>'
            else:main+='<p class="empty">Aucun projet documentaire en attente.</p>'
            main+='</section><section><h2>✅ Diligences proposées</h2>'
            for item in pending['diligences']:
                fields={'proposal_kind':'diligence','proposal':item['id']}
                main+='<article class="proposal-row"><div><small>'+e(item['matter_name'])+' · estimation '+str(item['estimated_minutes'])+' min à contrôler</small><h3>'+e(item['title'])+'</h3><p>'+e(item['description'])+'</p></div><div class="actions">'+form('review_autonomy_proposal','Conserver',fields|{'status':'accepted'})+form('review_autonomy_proposal','Écarter',fields|{'status':'dismissed'})+'</div></article>'
            if not pending['diligences']:main+='<p class="empty">Aucune diligence proposée.</p>'
            main+='</section><section><h2>💶 Facturation proposée</h2><p class="notice">Les durées sont des estimations à confirmer. Aucun montant et aucune facture Invoice Ninja ne sont créés automatiquement.</p>'
            for item in pending['billing']:
                fields={'proposal_kind':'billing','proposal':item['id']}
                main+='<article class="proposal-row"><div><small>'+e(item['matter_name'])+' · '+str(item['estimated_minutes'])+' min estimées</small><h3>'+e(item['label'])+'</h3><p>Temps réellement passé et caractère facturable à confirmer.</p></div><div class="actions">'+form('review_autonomy_proposal','Conserver',fields|{'status':'accepted'})+form('review_autonomy_proposal','Écarter',fields|{'status':'dismissed'})+'</div></article>'
            if not pending['billing']:main+='<p class="empty">Aucune facturation proposée.</p>'
            main+='</section>'
        elif path=='/recherche-juridique':
            mid=args.get('matter','');main+='<section class="daily-hero"><div><p class="eyebrow">VERSION 2.4.0</p><h2>Recherche et traçabilité</h2><p>Les passerelles reçoivent seulement une question anonymisée. Une piste n’est citable qu’après récupération du texte officiel, vérification de l’identifiant et présence mot pour mot de l’extrait.</p></div></section>'
            main+=form('legal_research','Rechercher sans données client',extra='<label>Identifiant exact du dossier<input required name="matter" maxlength="80" value="'+e(mid)+'"></label><label>Question juridique anonymisable<textarea required name="question" maxlength="3000" rows="5"></textarea></label><input type="hidden" name="providers" value="openlegal,openlegi,goodlegal,pappers"><input type="hidden" name="limit" value="8">')
            main+='<div class="dashboard-actions">'+form('identify_latest_writings','Identifier les dernières conclusions',{'matter':mid,'document_type':'conclusions'})+form('refresh_exhibit_registry','Empreinter les pièces du dossier',{'matter':mid})+'</div>'
            if args.get('job'):
                try:
                    row=desk.db.execute('SELECT status,result FROM jobs WHERE id=?',(int(args['job']),)).fetchone()
                    if row and row['status']=='done':main+='<h2>Résultat vérifiable</h2>'+block(json.dumps(json.loads(row['result']),ensure_ascii=False,indent=2))
                except (ValueError,TypeError,json.JSONDecodeError):pass
            main+='<section><h2>Registres du dossier</h2>'
            if mid:
                try:
                    from .legal_research import authorities,exhibits
                    jurisprudence=authorities(desk,mid);pieces=exhibits(desk,mid)
                    main+='<p><strong>'+str(len(jurisprudence['authorities']))+'</strong> décision(s) enregistrée(s), dont '+str(sum(x['citable'] for x in jurisprudence['authorities']))+' citable(s). <strong>'+str(len(pieces['exhibits']))+'</strong> pièce(s) empreintée(s), '+str(len(pieces['exact_duplicates']))+' groupe(s) de doublons exacts.</p>'
                    for item in jurisprudence['authorities'][:20]:main+='<article class="proposal-row"><div><small>'+e(item['verification_status'])+' · '+e(item['court'])+' · '+e(item['decision_date'])+'</small><h3>'+e(item['title'] or item['identifier'])+'</h3><p>'+e(item['exact_excerpt'][:600])+'</p><small>SHA-256 officiel : '+e(item['official_text_sha256'])+'</small></div></article>'
                except Stop:main+='<p class="notice">Saisissez l’identifiant exact d’un dossier pour consulter ses registres.</p>'
            main+='</section>'
        elif path=='/preparation-proactive':
            from .proactive34_ui import page as preparation_page
            main+=preparation_page(desk,e,link,form)
        elif path=='/assistance-metier':
            from .assistance35_ui import page as assistance_page
            main+=assistance_page(desk,e,link,form,args)
        elif path=='/audiences-word':
            from .hearing import preview_hearing
            from .word_legal import preview_word_project
            mid=args.get('matter','')
            main+='<section class="daily-hero"><div><p class="eyebrow">VERSION 2.5.0 · SUPERVISÉE</p><h2>Audience, contradictoire et moteur Word</h2><p>La préparation identifie les écritures des parties, compare leurs dispositifs et produit des projets contrôlables. Les sources restent inchangées ; aucun dépôt RPVA, envoi, signature ou écrasement.</p></div></section>'
            q=args.get('q','').strip()
            main+='<form method="get" action="'+e(url('/audiences-word'))+'"><label>Rechercher un dossier par nom complet ou référence<input name="q" maxlength="150" value="'+e(q)+'" placeholder="MARTIN, 2018091201…"></label><button>Rechercher</button></form>'
            if q:
                choices=[x for x in matters if q.casefold() in (matter_option(x)+' '+x['path']).casefold()]
                main+='<section><h3>'+str(len(choices))+' dossier(s) correspondant(s)</h3>'
                for item in choices[:50]:main+='<p>'+link('/audiences-word',matter_option(item),matter=item['id'],q=q)+'</p>'
                if len(choices)>50:main+='<p>Précisez la recherche pour voir les autres dossiers.</p>'
                main+='</section>'
            if mid in by_id:main+='<p class="success">Dossier sélectionné : '+e(matter_option(by_id[mid]))+'</p>'
            pdf_link=external_links(desk).get('pdf_tools','https://pdf.example.com/')
            main+='<section class="ws-hearing"><h2>Préparer une plaidoirie à partir des dernières conclusions</h2><p>Recherchez d’abord l’affaire ci-dessus. Ajoutez les dernières conclusions des deux parties, puis indiquez l’audience, votre objectif et les points à défendre. Les sources téléversées sont enregistrées dans le dossier choisi, sans écraser les originaux.</p><p><a class="button secondary" target="_blank" rel="noopener noreferrer" href="'+e(pdf_link)+'">Ouvrir l’atelier PDF pour numéroter, nommer ou tamponner les pièces</a></p>'
            main+='<div class="ws-hearing-uploads" data-hearing-matter="'+e(mid)+'">'
            for side,label in (('ours','Nos dernières conclusions'),('opponent','Dernières conclusions adverses')):
                main+='<label>'+label+' · DOCX ou PDF, 20 Mo maximum<input type="file" accept=".docx,.pdf" data-hearing-upload="'+side+'"'+('' if mid in by_id else ' disabled')+'></label><p role="status" data-hearing-status="'+side+'">'+('Sélectionnez un dossier avant le téléversement.' if mid not in by_id else 'Aucun nouveau document téléversé.')+'</p>'
            main+='</div>'
            main+=form('prepare_hearing','Analyser et préparer la plaidoirie',extra='<label>Dossier sélectionné<input required readonly name="matter" maxlength="80" value="'+e(mid if mid in by_id else '')+'"></label><label>Objectif, date, durée et points à défendre<textarea required name="instruction" maxlength="5000" rows="5" placeholder="Audience, prétentions principales, durée de parole, arguments adverses à traiter…"></textarea></label><label>Nos conclusions (chemin ajouté automatiquement après téléversement)<input name="our_source_path" maxlength="2000"></label><label>Conclusions adverses (facultatif pour analyse provisoire)<input name="opponent_source_path" maxlength="2000"></label>')
            main+='<p class="notice">Le traitement des documents longs parcourt toutes les pages, reprend au dernier fragment validé et cite les pages. Les connecteurs MCP juridiques activés reçoivent seulement une question anonymisée ; seules les décisions revérifiées sur un site officiel sont citables. L’atelier PDF s’ouvre séparément : AxiorHub ne lui transmet aucun dossier ni secret sans API authentifiée documentée.</p></section>'
            main+='<section><h2>Préparer une révision Word</h2>'+form('prepare_word_project','Analyser le DOCX sans le modifier',extra='<label>Identifiant exact du dossier<input required name="matter" maxlength="80" value="'+e(mid)+'"></label><label>Modifications souhaitées<textarea required name="instruction" maxlength="5000" rows="4"></textarea></label><label>DOCX source (chemin exact, recommandé)<input name="source_path" maxlength="2000"></label>')+'</section>'
            selected_h=args.get('hearing','');selected_w=args.get('word','')
            if selected_h:
                try:
                    shown=preview_hearing(desk,selected_h);prep=shown['preparation'];coverage=shown.get('coverage',{});ours_cov=coverage.get('our_writing',{});opp_cov=coverage.get('opponent_writing',{});main+='<section class="ws-hearing"><h2>Prévisualisation de la plaidoirie</h2><article class="project-preview"><p><strong>Nos écritures :</strong> '+e(shown['writings']['ours']['path'])+'</p><p><strong>Écritures adverses :</strong> '+e(shown['writings']['opponent']['path'] or 'non fournies')+'</p><p><strong>Destination :</strong> '+e(shown['destination_folder'])+'</p><p><strong>Contrôle :</strong> '+e(shown['control']['status'])+'</p><div class="ws-coverage-grid"><p><strong>Nos conclusions</strong><br>'+str(ours_cov.get('pages_analyzed',0))+'/'+str(ours_cov.get('pages_total',0))+' pages · '+str(ours_cov.get('chunks_analyzed',ours_cov.get('parts_analyzed',0)))+' fragments · '+e(ours_cov.get('status',''))+'</p><p><strong>Conclusions adverses</strong><br>'+str(opp_cov.get('pages_analyzed',0))+'/'+str(opp_cov.get('pages_total',0))+' pages · '+str(opp_cov.get('chunks_analyzed',opp_cov.get('parts_analyzed',0)))+' fragments · '+e(opp_cov.get('status',''))+'</p></div><h3>'+e(prep.get('title',''))+'</h3><p>'+e(prep.get('procedural_context',''))+'</p><h3>Matrice contradictoire</h3>'+''.join('<article class="card"><h4>'+e(item.get('issue',''))+'</h4><p>Notre position : '+e(item.get('our_position',''))+'</p><p>Position adverse : '+e(item.get('opponent_position',''))+'</p><p>Réponse proposée : '+e(item.get('proposed_response',''))+'</p><small>Sources : '+e(', '.join(item.get('our_source_ids',[])+item.get('opponent_source_ids',[])+item.get('authority_source_ids',[])))+'</small></article>' for item in prep.get('argument_matrix',[]))+'<h3>Limites et vérifications</h3><ul>'+''.join('<li>'+e(x)+'</li>' for x in prep.get('limits',[]))+'</ul><h3>Projets disponibles après confirmation</h3><ul>'+''.join('<li>'+e(x)+'</li>' for x in shown['future_files'])+'</ul></article></section>'
                except Stop:pass
            if selected_w:
                try:
                    shown=preview_word_project(desk,selected_w);main+='<section><h2>Prévisualisation Word</h2><article class="project-preview"><p><strong>Source intacte :</strong> '+e(shown['source_file']['path'])+'</p><p><strong>Destination :</strong> '+e(shown['destination_folder'])+'</p><p><strong>Modifications :</strong> '+str(len(shown['edit_plan'].get('edits',[])))+'</p><ul>'+''.join('<li>'+e(x)+'</li>' for x in shown['future_files'])+'</ul></article></section>'
                except Stop:pass
            hearings=desk.db.execute("SELECT id,matter,status,created FROM hearing_projects_v250 ORDER BY created DESC LIMIT 30").fetchall()
            words=desk.db.execute("SELECT id,matter,status,created FROM word_projects_v250 ORDER BY created DESC LIMIT 30").fetchall()
            main+='<section><h2>Préparations d’audience récentes</h2>'+''.join('<article class="proposal-row"><div><small>'+e(x['status'])+' · '+e(date(x['created']))+'</small><h3>'+e(x['matter'])+'</h3></div><div class="actions">'+link('/audiences-word','Prévisualiser',hearing=x['id'])+'</div></article>' for x in hearings)
            if not hearings:main+='<p class="empty">Aucune préparation d’audience.</p>'
            main+='</section><section><h2>Révisions Word récentes</h2>'+''.join('<article class="proposal-row"><div><small>'+e(x['status'])+' · '+e(date(x['created']))+'</small><h3>'+e(x['matter'])+'</h3></div><div class="actions">'+link('/audiences-word','Prévisualiser',word=x['id'])+'</div></article>' for x in words)
            if not words:main+='<p class="empty">Aucune révision Word.</p>'
            main+='</section>'
        elif path=='/surveillance':
            from .proactive import signals
            selected=args.get('state','open')
            if selected not in ('open','acknowledged','snoozed','resolved','all'):selected='open'
            rows=signals(desk,selected,limit=200)
            main+='<section class="daily-hero"><div><h2>Surveillance proactive</h2><p>L’agent détecte et propose. Il ne valide aucune échéance, ne choisit aucune stratégie et n’exécute aucune action externe.</p></div><div class="actions">'+form('monitor_all','🔭 Surveiller tous les dossiers')+form('build_daily_dashboard','🧭 Recalculer le tableau')+'</div></section>'
            tabs=[('open','Ouverts'),('acknowledged','Vus'),('snoozed','Reportés'),('resolved','Résolus'),('all','Tous')]
            main+='<nav class="tabs">'+''.join('<a class="'+('active' if selected==key else '')+'" href="'+e(url('/surveillance',state=key))+'">'+e(label)+'</a>' for key,label in tabs)+'</nav>'
            if rows:
                for signal in rows:
                    m=by_id.get(signal['matter'],{'client_name':signal['matter']})
                    main+='<article class="signal '+e(signal['severity'])+'"><div><small>'+e(signal['severity'].upper())+' · '+e(signal['category'])+' · '+e(date(signal['last_seen']))+'</small><h2>'+e(signal['title'])+'</h2><p>'+e(signal['detail'])+'</p><p><strong>Action proposée :</strong> '+e(signal['proposed_action'])+'</p>'+link('/matter','📁 '+(matter_display(by_id[signal['matter']]) if signal['matter'] in by_id else signal['matter']),id=signal['matter'])
                    if signal['source_ids']:main+='<details><summary>Sources et identifiants</summary>'+block('\n'.join(signal['source_ids']))+'</details>'
                    main+='</div><div class="signal-actions">'
                    if signal['state']!='acknowledged':main+=form('ack_signal','✓ Vu',{'signal':signal['id']})
                    if signal['state']!='snoozed':main+=form('snooze_signal','⏰ Reporter 24 h',{'signal':signal['id'],'hours':'24'})
                    if signal['state']!='resolved':main+=form('resolve_signal','✓ Résoudre',{'signal':signal['id']})
                    main+='</div></article>'
            else:main+='<p class="empty">Aucun signal dans cette rubrique.</p>'
        elif path=='/openwebui':
            from .supervision import requests
            pending=requests(desk,'pending',100);recent=requests(desk,'',20)
            openwebui=external_links(desk).get('openwebui','')
            if urlsplit(openwebui).scheme=='https':
                openwebui_mail=openwebui.rstrip('/')+'/mail'
                main+='<p class="success">Ouvrir directement <a rel="noopener noreferrer" target="_blank" href="'+e(openwebui)+'">Open WebUI</a>. La messagerie Open WebUI se trouve dans <a rel="noopener noreferrer" target="_blank" href="'+e(openwebui_mail)+'">Courrier</a>.</p>'
            main+='<section class="daily-hero"><div><p class="eyebrow">INTERFACE CONVERSATIONNELLE PRINCIPALE</p><h2>🤖 AxiorHub dans Open WebUI</h2><p>Posez des questions générales ou par dossier, obtenez la réponse et ses sources dans la même conversation, puis confirmez séparément les actions engageantes.</p></div>'+link('/assistant','Assistant intégré de secours')+'</section>'
            main+='<div class="stats"><article><strong>'+str(len(pending))+'</strong><span>Confirmations en attente</span></article><article><strong>'+str(len([x for x in recent if x['status']=='approved']))+'</strong><span>Confirmées récemment</span></article><article><strong>0</strong><span>Envoi automatique autorisé</span></article></div>'
            main+='<section><h2>Parcours supervisé</h2><ol><li>Demandez une recherche, une analyse, une matrice ou un projet : la réponse revient dans le chat avec ses sources.</li><li>Pour un brouillon, l’assistant affiche le courriel visé, l’instruction et un code à six chiffres.</li><li>Relisez puis recopiez ce code dans un nouveau message. Sans cette saisie, aucune préparation n’est lancée.</li><li>Le résultat est un brouillon dans '+e(c['mail']['drafts'])+' seulement si les contrôles réussissent. Aucun envoi.</li></ol></section>'
            main+='<section><h2>Demandes supervisées récentes</h2>'
            if recent:
                for item in recent:
                    main+='<article class="card"><small>'+e(item['status'])+' · '+e(date(item['created']))+'</small><h3>'+e(item['summary'])+'</h3><p>Demande '+e(item['id'])+((' · opération #'+str(item['job_id'])) if item['job_id'] else '')+'</p></article>'
            else:main+='<p class="empty">Aucune demande supervisée. Commencez dans Open WebUI.</p>'
            main+='</section><p class="notice">Les outils n’exécutent ni shell, ni envoi SMTP, ni signature ou dépôt d’acte, ni modification de document Nextcloud.</p>'
        elif path=='/administration':
            from .operations import exceptions
            x=exceptions(desk)
            cards=[('brouillons','Brouillons disponibles'),('a_verifier','Courriels à vérifier'),
                   ('associations','Associations à confirmer'),('dates_a_confirmer','Dates à confirmer'),
                   ('sans_reponse_24h','À vérifier depuis 24 h'),('sans_reponse_48h','À vérifier depuis 48 h'),
                   ('pieces_illisibles','Pièces illisibles'),('reponses_non_apprises','Réponses non apprises'),
                   ('documents_en_erreur','Indexations en erreur'),('dossiers_sans_index','Dossiers sans index')]
            main+='<div class="stats">'+''.join('<article><strong>'+str(x[k])+'</strong><span>'+e(label)+'</span></article>' for k,label in cards)+'</div>'
            main+='<p>Cette page concentre les exceptions. L’analyse des non-lus et l’apprentissage des réponses envoyées restent exécutés par la minuterie toutes les cinq minutes.</p>'
            main+='<div class="actions">'+form('run','Analyser maintenant')+form('health','Tester les connexions')+'</div>'
            main+='<p class="empty compact">Les synthèses techniques quotidiennes sont désactivées : elles ne créent plus de faux brouillons dans votre messagerie.</p>'
            main+='<h2>Automatismes</h2>'
            auto=c.get('automation',{})
            for key,label,default in [('health_enabled','Contrôle des connexions',True),('sync_enabled','Découverte et associations',True),
                                      ('reconcile_inbox_enabled','Nettoyage de la boîte À traiter',True),
                                      ('classify_portfolio_enabled','Classement du portefeuille actif',True),
                                      ('nextcloud_tasks_enabled','Synchronisation Agenda et Tâches Nextcloud',False),
                                      ('index_all_enabled','Indexation progressive des dossiers actifs',False)]:
                active=desk.settings('automation:'+key,auto.get(key,default))
                main+='<div class="contact"><span>'+e(label)+' · '+('actif' if active else 'inactif')+'</span>'+form('automation_setting','Désactiver' if active else 'Activer',{'key':key,'value':'no' if active else 'yes'})+'</div>'
            proactive=c.get('proactive',{})
            for key,label,default in [('proactive_enabled','Surveillance proactive des dossiers',True),('daily_dashboard_enabled','Tableau de bord quotidien',True)]:
                active=desk.settings('automation:'+key,proactive.get('enabled' if key=='proactive_enabled' else key,default))
                main+='<div class="contact"><span>'+e(label)+' · '+('actif' if active else 'inactif')+'</span>'+form('automation_setting','Désactiver' if active else 'Activer',{'key':key,'value':'no' if active else 'yes'})+'</div>'
            autonomy=c.get('autonomy',{})
            for key,label,setting,default in [('autonomy_enabled','Orchestrateur autonome de préparation','enabled',True),('automatic_document_previews_enabled','Prévisualisations documentaires automatiques','automatic_document_previews_enabled',True),('automatic_internal_files_enabled','Création automatique des fichiers dans AxiorHub_Brouillons','automatic_internal_files_enabled',True),('document_control_enabled','Contrôle documentaire et juridique indépendant','document_control_enabled',True),('diligence_proposals_enabled','Propositions automatiques de diligences','diligence_proposals_enabled',True),('billing_proposals_enabled','Propositions automatiques de facturation','billing_proposals_enabled',True)]:
                active=desk.settings('automation:'+key,autonomy.get(setting,default))
                main+='<div class="contact"><span>'+e(label)+' · '+('actif' if active else 'inactif')+'</span>'+form('automation_setting','Désactiver' if active else 'Activer',{'key':key,'value':'no' if active else 'yes'})+'</div>'
            orchestrator=c.get('orchestrator',{})
            for key,label in [('automatic_mail_drafts_enabled','Dépôt automatique des réponses sûres dans Brouillons'),
                              ('automatic_legal_projects_enabled','Préparation automatique des projets d’actes')]:
                active=desk.settings('automation:'+key,orchestrator.get(key,True))
                main+='<div class="contact"><span>'+e(label)+' · '+('actif' if active else 'en pause')+'</span>'+form('automation_setting','Mettre en pause' if active else 'Reprendre',{'key':key,'value':'no' if active else 'yes'})+'</div>'
            production=c.get('production',{})
            active=desk.settings('automation:production_enabled',production.get('enabled',True))
            main+='<div class="contact"><span>Production autonome contrôlée · '+('active' if active else 'en pause')+'</span>'+form('automation_setting','Mettre en pause' if active else 'Reprendre',{'key':'production_enabled','value':'no' if active else 'yes'})+'</div>'
            main+='<h2>Connexions</h2>'+block(json.dumps(x.get('sante') or {'etat':'Contrôle pas encore exécuté'},ensure_ascii=False,indent=2))
            from .workstation import external_links,unpaid_summary
            links=external_links(desk);unpaid=unpaid_summary(desk)
            main+='<section><h2>Poste de travail et raccourcis</h2><p>Ces boutons ouvrent les applications dans un nouvel onglet ; AxiorHub ne transmet ni mot de passe ni contenu de dossier par ces liens.</p>'
            for key,label in (('roundcube','Roundcube'),('roundcube_drafts','Brouillons Roundcube'),('openwebui','Open WebUI'),('nextcloud','Nextcloud / OnlyOffice'),('onlyoffice','Accueil OnlyOffice'),('invoice_ninja','Invoice Ninja')):
                current=links.get(key,'')
                main+=form('save_external_url','Enregistrer '+label,{'key':key},'<label>'+label+' — URL HTTPS<input type="url" name="value" maxlength="1000" value="'+e(current)+'"></label>')
            main+='</section><section><h2>Factures impayées — lecture seule</h2><p>'+((str(unpaid['count'])+' facture(s) en cache · '+e(unpaid['balance_display'])+' · dernière synchronisation '+e(unpaid['last_sync'])+'.') if unpaid['synchronized'] else 'Aucune synchronisation Invoice Ninja vérifiée : nombre et solde inconnus.')+' AxiorHub utilise uniquement GET et ne crée ni facture, ni paiement, ni relance.</p>'
            if c.get('invoice_ninja',{}).get('enabled'):main+=form('refresh_unpaid_invoices','Actualiser les impayés')
            else:main+='<p class="notice">Configurez l’URL et le fichier de jeton Invoice Ninja dans config.json, puis activez cette connexion. Le bouton d’application seul n’active pas l’API.</p>'
            main+='</section>'
            main+='<h2>Exceptions récentes</h2>'
            shown=0
            for key,status,reason,stamp in state.rows(300):
                if status not in ('review','error','append_uncertain'):continue
                try:r=report_for(c,key)
                except Stop:continue
                main+='<p>'+link('/mail',r.get('subject','Courriel'),key=key)+' · '+e(REASONS.get(reason,reason))+' · '+e(date(stamp))+'</p>';shown+=1
                if shown>=25:break
            if not shown:main+='<p class="empty">Aucune exception de courriel.</p>'
            main+='<h2>Réglages et contrôles avancés</h2><div class="admin-links">'+link('/associations','Associations proposées')+link('/memoire','Mémoire et apprentissage')+link('/qualite','Qualité et statistiques')+link('/dossiers','Indexation des dossiers')+'</div>'
        elif path=='/':
            all_rows=[];bucket_counts={'todo':0,'drafts':0,'confirm':0,'backlog':0,'ignored':0,'closed':0}
            bucket_states={'todo':('needs_action','drafting','processing'),
                           'drafts':('draft_ready',),'confirm':('needs_confirmation',),
                           'backlog':('backlog',),'ignored':('ignored',),'closed':('handled','archived')}
            manual_keys={row[0] for row in desk.db.execute('SELECT mail_key FROM manual_drafts')}
            from .integration import sync_work_items
            sync_work_items(desk)
            for business,total in desk.db.execute('SELECT state,COUNT(*) FROM work_items GROUP BY state'):
                for bucket,states in bucket_states.items():
                    if business in states:bucket_counts[bucket]+=total;break
            view=args.get('view','todo')
            if view not in bucket_counts:view='todo'
            try:page=max(1,min(int(args.get('page',1)),10000))
            except (ValueError,TypeError):page=1
            batch=50;offset=(page-1)*batch
            marks=','.join('?' for _ in bucket_states[view])
            rows=desk.db.execute('SELECT * FROM work_items WHERE state IN ('+marks+') '
                'ORDER BY updated DESC,mail_key LIMIT ? OFFSET ?',
                (*bucket_states[view],batch,offset)).fetchall()
            for item in rows:
                key=item['mail_key'];reason=item['source_reason'];stamp=item['updated'];business=item['state']
                status=item['source_status']
                try:r=report_for(c,key)
                except Stop:continue
                all_rows.append((key,status,reason,stamp,r,view))
            tabs=[('todo','À traiter'),('drafts','Brouillons prêts'),('confirm','À confirmer'),
                  ('backlog','Arriéré'),('ignored','Ignorés'),('closed','Clos')]
            main+='<nav class="tabs">'+''.join('<a class="'+('active' if view==key else '')+'" href="'+e(url('/',view=key))+'">'+e(label)+' <strong>'+str(bucket_counts[key])+'</strong></a>' for key,label in tabs)+'</nav>'
            from .workstation import reason_fr
            ignored_reasons=list(state.db.execute("SELECT reason,COUNT(*) FROM messages WHERE status='ignored' GROUP BY reason ORDER BY COUNT(*) DESC LIMIT 12"))
            main+='<details class="technical"><summary>Pourquoi des courriels sont ignorés ? '+str(counts.get('ignored',0))+' dans le registre IMAP</summary>'
            main+='<p>Le total des courriels analysés et le nombre d’actions ne sont pas identiques. Les messages lus, déjà traités, automatiques ou jugés sans réponse peuvent rester ignorés. Contrôlez le motif avant toute reprise.</p>'
            main+=''.join('<p><strong>'+str(n)+'</strong> · '+e(reason_fr(reason))+' <small>('+e(reason or 'sans motif')+')</small></p>' for reason,n in ignored_reasons)
            main+=link('/administration','Voir les erreurs et la file des opérations')+'</details>'
            from .workstation import external_links
            links=external_links(desk);webmail=links.get('roundcube','')
            openwebui_base=links.get('openwebui','')
            openwebui_mail=openwebui_base.rstrip('/')+'/mail' if openwebui_base else ''
            main+='<section class="ws-heading ws-roundcube-container"><h2>Messagerie</h2><p>Consultez le courrier dans Open WebUI. Le webmail Roundcube demeure accessible dans un onglet séparé.</p>'
            if openwebui_mail:
                main+='<details class="ws-mail-fold"><summary>Afficher ou masquer le courrier Open WebUI</summary><iframe class="ws-roundcube-frame" title="Courrier Open WebUI" src="'+e(openwebui_mail)+'" loading="lazy" referrerpolicy="no-referrer" allow="clipboard-read; clipboard-write"></iframe></details>'
                main+='<p><a href="'+e(openwebui_mail)+'" rel="noopener noreferrer" target="_blank">Ouvrir le courrier Open WebUI directement</a>'
            else:
                main+='<p class="notice">Open WebUI n’est pas configuré. Enregistrez son URL dans Paramètres → Connexions.</p><p>'
            if urlsplit(webmail).scheme=='https':main+=' · <a href="'+e(webmail)+'" rel="noopener noreferrer" target="_blank">Ouvrir Roundcube</a>'
            main+='</p><p class="muted">Si l’intégration est refusée par la politique de sécurité d’Open WebUI, utilisez le lien direct.</p></section>'

            verified_drafts=desk.db.execute("SELECT COUNT(*) FROM work_items WHERE state='draft_ready' AND source_status='drafted'").fetchone()[0]
            prepared_drafts=desk.db.execute('SELECT COUNT(*) FROM manual_drafts').fetchone()[0]
            main+='<p class="summary-line"><strong>'+str(verified_drafts)+' brouillon'+('s' if verified_drafts!=1 else '')+' vérifié'+('s' if verified_drafts!=1 else '')+' en IMAP</strong> · <strong>'+str(prepared_drafts)+' projet'+('s' if prepared_drafts!=1 else '')+' à relire ou déposer</strong> · <strong>'+str(bucket_counts['confirm'])+' élément'+('s' if bucket_counts['confirm']!=1 else '')+' à confirmer</strong></p>'
            if verified_drafts:
                main+='<p class="success">'+str(verified_drafts)+' brouillon'+('s' if verified_drafts!=1 else '')+' retrouvé'+('s' if verified_drafts!=1 else '')+' dans <strong>'+e(c['mail']['drafts'])+'</strong>.</p>'
            else:
                main+='<p class="empty compact">Aucun dépôt AxiorHub vérifié actuellement dans '+e(c['mail']['drafts'])+'. Un projet interne n’est pas présenté comme un brouillon déposé.</p>'
            main+='<div class="top-actions">'+form('run','Rechercher les nouveaux courriels')+'</div>'
            shown=0
            for key,status,reason,stamp,r,bucket in all_rows:
                if bucket!=view:continue
                shown+=1
                proposed=''
                scores=r.get('matter_scores') or []
                best=next((x for x in scores if x.get('matter') in by_id),None)
                selected=r.get('matter') if r.get('matter') in by_id else (best.get('matter') if best else '')
                selected_role=r.get('recipient_role') or (best.get('role') if best else '') or ''
                if selected:
                    proposed='<p class="proposed">Dossier proposé : <strong>'+e(matter_option(by_id[selected]))+'</strong></p>'
                shown_status='Clos' if bucket=='closed' else LABELS.get(status,status)
                main+='<article class="mail-item"><div class="mail-head"><div><h2>'+link('/mail',r.get('subject') or 'Sans objet',key=key)+'</h2><p>'+e(r.get('sender',''))+' · '+e(date(r.get('received_at') or stamp))+'</p></div><span class="badge '+e(status)+'">'+e(shown_status)+'</span></div>'+proposed
                if key in manual_keys:main+='<p class="success inline">Une réponse prudente est préparée dans la fiche de ce message et attend votre décision.</p>'
                if bucket in ('todo','confirm'):
                    main+='<p class="question">'+e(simple_question(r))+'</p><div class="primary-actions">'
                    if selected and selected_role:
                        main+=form('prepare_reply','Préparer une réponse',{'key':key})
                    elif selected:
                        main+=form('prepare_draft','Préparer une réponse neutre',{'key':key,'style':'clarification'})
                    else:
                        main+='<span class="disabled-action">Préparer une réponse après choix du dossier</span>'
                    main+=form('mark_handled','Ignorer',{'key':key,'back':'inbox'})
                    opts='<option value="">Choisir…</option>'+''.join('<option value="'+e(m['id'])+'"'+(' selected' if m['id']==selected else '')+'>'+e(matter_option(m))+'</option>' for m in matters)
                    chooser='<label>Dossier<select required name="matter">'+opts+'</select></label><p>Le rôle du correspondant pourra être confirmé séparément.</p>'
                    main+='<details class="chooser"><summary>📁 Choisir le dossier</summary>'+form('confirm_matter','Confirmer le dossier et retraiter',{'mail_key':key},chooser)+'<p>'+link('/dossiers','Dossier absent de la liste ? Chercher ou créer dans Nextcloud',state='all',mail_key=key)+'</p></details></div>'
                elif bucket=='drafts':
                    if status=='drafted':main+='<p class="success inline">Le brouillon a été relu dans le dossier IMAP configuré. Aucun courriel n’a été envoyé.</p>'
                    else:main+='<p class="notice inline">Un brouillon existant a été détecté, mais ce dépôt n’a pas été vérifié par AxiorHub. Ouvrez le message et contrôlez le dossier IMAP configuré.</p>'
                elif bucket=='closed':
                    main+='<p>Courriel clos dans le suivi. Le détail de son traitement est indiqué dans sa fiche.</p>'
                else:
                    main+='<p>'+e(REASONS.get(reason,reason))+'</p>'
                main+='</article>'
            if not shown:main+='<p class="empty">Aucun message dans cette rubrique.</p>'
            if bucket_counts[view]>batch:
                main+='<nav class="tabs" aria-label="Pages des courriels">'
                if page>1:main+=link('/','← Précédents',view=view,page=page-1)
                main+='<span>Page '+str(page)+' sur '+str((bucket_counts[view]+batch-1)//batch)+'</span>'
                if page*batch<bucket_counts[view]:main+=link('/','Suivants →',view=view,page=page+1)
                main+='</nav>'
        elif path=='/mail':
            key=args.get('key','');r=report_for(c,key);row=state.get(key)
            main+='<h2>'+e(r.get('subject',''))+'</h2><p>'+e(r.get('sender',''))+' · '+e(date(r.get('received_at')))+' </p>'
            if row and row[0]=='drafted' and r.get('draft_verified'):
                verified=r['draft_verified']
                main+='<p class="success">Brouillon enregistré et relu dans '+e(verified.get('folder',c['mail']['drafts']))+' · UID '+e(verified.get('uid',''))+' · '+e(date(verified.get('verified_at')))+'. Aucun envoi.</p>'
            elif row and row[0]=='append_uncertain':
                main+='<p class="notice">Dépôt à contrôler : '+e(REASONS.get(row[1],'Consulter le journal privé.'))+' Ne relancez pas le dépôt avant de vérifier Roundcube.</p>'
            main+='<p class="question">'+e(simple_question(r))+'</p>'
            scores=r.get('matter_scores') or [];best=next((x for x in scores if x.get('matter') in by_id),None)
            selected=r.get('matter') if r.get('matter') in by_id else (best.get('matter') if best else '')
            selected_role=r.get('recipient_role') or (best.get('role') if best else '') or ''
            opts='<option value="">Choisir…</option>'+''.join('<option value="'+e(m['id'])+'"'+(' selected' if m['id']==selected else '')+'>'+e(matter_option(m))+'</option>' for m in matters)
            roles='<option value="">Choisir le rôle…</option>'+''.join('<option value="'+e(k)+'"'+(' selected' if k==selected_role else '')+'>'+e(v)+'</option>' for k,v in ROLES.items())
            chooser='<label>Dossier<select required name="matter">'+opts+'</select></label><label>Rôle<select required name="role">'+roles+'</select></label>'
            main+='<div class="primary-actions">'
            if selected and selected_role:main+=form('prepare_reply','Préparer une réponse',{'key':key})
            elif selected:main+=form('prepare_draft','Préparer une réponse neutre',{'key':key,'style':'clarification'})
            else:main+='<span class="disabled-action">Préparer une réponse après choix du dossier</span>'
            main+=form('mark_handled','Ignorer',{'key':key,'back':'inbox'})
            chooser_matter='<label>Dossier<select required name="matter">'+opts+'</select></label><p>Cette action confirme seulement le dossier ; le rôle reste indépendant.</p>'
            main+='<details class="chooser"><summary>📁 Choisir le dossier</summary>'+form('confirm_matter','Confirmer le dossier et retraiter',{'mail_key':key},chooser_matter)+'<p>'+link('/dossiers','Dossier absent de la liste ? Chercher ou créer dans Nextcloud',state='all',mail_key=key)+'</p></details></div>'
            manual=desk.db.execute('SELECT data FROM manual_drafts WHERE mail_key=?',(key,)).fetchone()
            if manual:
                draft=json.loads(manual[0]);value=draft['result']
                main+='<section class="note"><h2>Réponse préparée</h2>'+block(value['body'] or 'Aucun texte fiable ne peut être proposé sans votre décision.')
                if value['limits']:main+='<p class="notice">'+e(' · '.join(value['limits']))+'</p>'
                if value['body'] and not value['requires_decision']:
                    main+=form('deposit_draft','Ajouter dans Brouillons',{'key':key,'confirm':'yes'})
                elif value['body']:
                    main+='<p><strong>Votre décision reste nécessaire avant tout dépôt dans Brouillons.</strong></p>'
                main+='</section>'
            main+='<p>'+link('/assistant','Discuter de ce courriel avec l’IA',key=key,matter=r.get('matter') if r.get('matter') in by_id else '')+'</p>'
            main+='<details class="technical"><summary>Afficher les options et détails techniques</summary>'
            main+='<p class="notice">'+e(REASONS.get(r.get('reason'),r.get('reason','')))+'<br>État : '+e(LABELS.get(row[0],row[0]) if row else 'Rapport seul')+'</p>'
            main+='<div class="actions">'+form('retry','Relancer si toujours non lu',{'key':key})+form('review','Préparer les points à décider',{'key':key})+link('/associations','Vérifier les associations')+'</div>'
            main+='<h2>Variantes et contrôles</h2><div class="actions">'
            for style,label in [('prudent','Préparer quand même un projet prudent'),('shorter','Régénérer plus court'),
                                ('direct','Régénérer plus direct'),('diplomatic','Régénérer plus diplomatique'),
                                ('clarification','Demande de précisions seulement'),('slots','Proposer trois créneaux')]:
                main+=form('prepare_draft',label,{'key':key,'style':style})
            main+=form('attachment_review','Analyser les pièces jointes',{'key':key})
            main+=form('attachment_review','Comparer avec la version précédente',{'key':key})
            main+=form('attachment_review','Proposer un classement dans le dossier',{'key':key})
            main+=form('prepare_draft','Préparer un accusé de réception prudent',{'key':key,'style':'prudent'})
            main+=form('deadline_review','Repérer échéances et relances',{'key':key})
            main+=form('mark_handled','Marquer traité sans réponse',{'key':key})+'</div>'
            scores=r.get('matter_scores',[])
            if scores:
                main+='<h2>Rattachement proposé</h2><div class="table"><table><thead><tr><th>Dossier</th><th>Confiance</th><th>Rôle</th><th>Indices</th></tr></thead><tbody>'
                for score in scores:
                    evidence=' · '.join(x.get('signal','indice')+' : '+str(x.get('value','')) for x in score['reasons'])
                    main+='<tr><td>'+e(matter_display(by_id.get(score['matter'],{'id':score['matter'],'client_name':score['client_name']})))+'</td><td>'+str(score['score'])+' %</td><td>'+e(ROLES.get(score.get('role'),score.get('role') or 'à confirmer'))+'</td><td>'+e(evidence)+'</td></tr>'
                main+='</tbody></table></div>'
            options=''.join('<option value="'+e(m['id'])+'"'+(' selected' if m['id']==r.get('matter') else '')+'>'+e(matter_display(m))+'</option>' for m in matters)
            main+='<details><summary>Toujours rattacher cet expéditeur à un dossier</summary>'+form('associate','Confirmer et relancer le courriel',
                {'email':r.get('sender',''),'retry':'yes'},'<label>Dossier<select required name="matter"><option value="">Choisir…</option>'+options+'</select></label><label>Rôle<select required name="role"><option value="">Choisir…</option>'+''.join('<option value="'+e(k)+'">'+e(v)+'</option>' for k,v in ROLES.items())+'</select></label>')+'</details>'
            main+='<details><summary>Ne jamais répondre à ce type de message</summary>'+form('add_rule','Ajouter la règle locale',{'key':key},'<label>Portée<select required name="rule"><option value="sender">Cet expéditeur exact</option><option value="domain">Tout ce domaine</option><option value="subject">Cet objet exact</option></select></label>')+'</details>'
            main+='<p>'+link('/dossiers','Rechercher un autre dossier ou créer une affaire',q=r.get('subject',''))+'</p>'
            triage=r.get('triage',{})
            if triage.get('reason'):main+='<h2>Analyse du tri</h2>'+block(triage['reason'])
            proposal=r.get('draft_body') or r.get('proposal',{}).get('body')
            if proposal:
                main+='<h2>'+('Brouillon déposé' if row and row[0]=='drafted' else 'Proposition intermédiaire — non déposée')+'</h2>'+block(proposal)
            else:main+='<p>Aucun texte de réponse déposé par ce traitement.</p>'
            source_rows=[]
            for s in r.get('sources',[]):
                source_rows.append('<details><summary>'+e(s.get('path') or s.get('filename') or s.get('id'))+'</summary><small>'+e(s.get('modified') or s.get('sent_at') or s.get('received_at',''))+'</small>'+block(s.get('excerpt') or s.get('text') or s.get('final_sent_text') or (json.dumps(s.get('content'),ensure_ascii=False,indent=2) if s.get('content') else 'Source mentionnée dans le rapport.'))+'</details>')
            main+='<h2>Sources du traitement</h2>'+(''.join(source_rows) or '<p>Aucune pièce utilisée à cette étape.</p>')
            attachment=desk.db.execute('SELECT data FROM attachment_reviews WHERE mail_key=?',(key,)).fetchone()
            if attachment:
                a=json.loads(attachment[0]);main+='<h2>Analyse technique des pièces jointes</h2>'
                for item in a['result']['items']:
                    main+='<details><summary>'+e(item['filename'])+' · '+e(item['quality'])+'</summary>'+block(item['summary'])+'<p>Classement suggéré : '+e(item['suggested_folder'])+'</p><ul>'+''.join('<li>'+e(x)+'</li>' for x in item['cautions'])+'</ul></details>'
                for error in a.get('errors',[]):main+='<p class="notice">'+e(error['filename']+' : '+error['error'])+'</p>'
            dates=desk.db.execute('SELECT * FROM deadline_proposals WHERE mail_key=? ORDER BY created DESC',(key,)).fetchall()
            if dates:
                main+='<h2>Dates proposées — aucune création automatique</h2>'
                for d in dates:
                    item=json.loads(d['data']);agenda=('déjà présente à cette heure dans l’agenda' if item.get('already_in_calendar') else ('absente de l’agenda contrôlé' if item.get('calendar_checked') and item.get('start') else 'agenda non vérifié'))
                    main+='<article class="card"><h3>'+e(item['title'])+'</h3><p>'+e(item['kind']+' · '+item['start']+' · confiance '+item['confidence']+' · '+agenda)+'</p>'+block(item['reason'])
                    if d['status']=='pending':
                        main+='<div class="actions">'+form('confirm_event','Ajouter à l’agenda après confirmation',{'proposal':d['id'],'confirm':'yes','key':key})+form('confirm_task','Créer une relance après confirmation',{'proposal':d['id'],'confirm':'yes','key':key})+form('ignore_deadline','Ignorer cette date',{'proposal':d['id'],'key':key})+'</div>'
                    else:main+='<p>État : '+e(d['status'])+'</p>'
                    main+='</article>'
            main+='<h2>Évaluer ce traitement</h2><div class="actions">'+''.join(form('feedback',label,{'key':key,'category':category}) for category,label in [('good','Bon projet'),('shorter','À raccourcir'),('wrong_matter','Mauvais dossier'),('fabricated_fact','Fait inventé'),('wrong_recipient','Mauvais destinataire'),('unnecessary','Réponse inutile'),('legal_decision','Décision juridique non autorisée')])+'</div>'
            note=desk.db.execute('SELECT data FROM notes WHERE key=?',(key,)).fetchone()
            if note:
                n=json.loads(note[0]);v=n['result']
                main+='<section class="note"><h2>Projet de travail interne</h2><p class="notice">'+e(n['warning'])+' '+e(n['scope'])+'</p><small>'+e(date(n['created_at']))+'</small>'+block(v['summary'])
                for k,label in [('decisions','Vos décisions'),('questions','Précisions à demander'),('limits','Limites')]:
                    main+='<h3>'+label+'</h3><ul>'+''.join('<li>'+e(x)+'</li>' for x in v[k])+'</ul>'
                if v['partial_reply']:main+='<h3>Texte partiel à relire et copier manuellement</h3>'+block(v['partial_reply'])
                main+='</section>'
            main+='</details>'
        elif path=='/associations':
            main+='<p>Une confirmation vaut pour tous les échanges du même correspondant et du même dossier. Le dossier et le rôle restent deux décisions distinctes.</p>'
            main+=form('organize_cabinet','✨ Rechercher et regrouper automatiquement',{'restart':'yes'})
            from .portfolio import grouped_associations
            groups=grouped_associations(desk,'pending',200)+grouped_associations(desk,'conflict',100)
            for group in groups:
                roles='<option value="">Choisir le rôle…</option>'+''.join('<option value="'+k+'">'+e(v)+'</option>' for k,v in ROLES.items())
                fields='<label>Adresse<input readonly name="email" type="email" value="'+e(group['email'])+'"></label><label>Rôle<select required name="role">'+roles+'</select></label>'
                evidence=''.join('<li>'+e(x.get('subject','Sans objet'))+'<small>'+e(date(x.get('date','')))+' · '+e(x.get('source',''))+'</small></li>' for x in group['evidence'])
                main+='<article class="card association-group"><small>'+e(group['level'])+' · '+str(group['confidence'])+' % · '+str(group['message_count'])+' échange(s)</small><h2>'+e(group['email'])+'</h2><p>Dossier proposé : <strong>'+e(group['matter_name']+' · '+group['matter'])+'</strong></p><details><summary>Pourquoi cette proposition ?</summary><ul>'+evidence+'</ul></details><div class="actions">'+form('associate','Confirmer le rôle et relancer le groupe',{'matter':group['matter']},fields)+form('reject_group','Rejeter cette association',{'group':group['id']})+'</div></article>'
            grouped_keys={(x['matter'],x['email']) for x in groups}
            legacy=desk.db.execute("SELECT * FROM proposals WHERE status='pending' ORDER BY updated DESC LIMIT 200").fetchall()
            for proposal in legacy:
                if (proposal['matter'],proposal['email']) in grouped_keys:continue
                matter=by_id.get(proposal['matter'])
                if not matter:continue
                roles='<option value="">Choisir le rôle…</option>'+''.join('<option value="'+k+'">'+e(v)+'</option>' for k,v in ROLES.items())
                fields='<label>Adresse<input readonly name="email" type="email" value="'+e(proposal['email'])+'"></label><label>Rôle<select required name="role">'+roles+'</select></label>'
                main+='<article class="card association-group"><small>Proposition antérieure à regrouper</small><h2>'+e(proposal['email'])+'</h2><p>Dossier proposé : <strong>'+e(matter_display(matter))+'</strong></p><div class="actions">'+form('associate','Confirmer le rôle et relancer le groupe',{'matter':matter['id'],'proposal':proposal['key']},fields)+form('reject','Rejeter cette proposition',{'key':proposal['key']})+'</div></article>'
            if not groups and not legacy:main+='<p class="empty">Aucune confirmation groupée nécessaire. Les rapprochements certains sont appliqués automatiquement.</p>'
            main+='<h2>Associer directement une adresse</h2><p>Ouvrez un dossier pour ajouter ou corriger un correspondant sans utiliser le terminal.</p>'+link('/dossiers','Ouvrir les dossiers')
        elif path=='/dossiers':
            roots=c['nextcloud'].get('matter_roots') or c['nextcloud']['roots']
            from .portfolio import portfolio_rows,portfolio_summary
            summary=portfolio_summary(desk);pc=summary['counts'];query=args.get('q','')
            selected_state=args.get('state','all' if query else 'active')
            if selected_state not in ('active','dormant','archived','to_confirm','all'):selected_state='active'
            main+='<section class="portfolio-head"><div><h2>Portefeuille du cabinet</h2><p>La découverte est limitée à <strong>'+e(', '.join(roots))+'</strong>. Une indexation technique ne rend jamais un ancien dossier actif.</p></div>'+form('organize_cabinet','✨ Organiser automatiquement mon cabinet',{'restart':'yes'})+'</section>'
            tabs=[('active','Actifs',pc.get('active',0)),('dormant','En sommeil',pc.get('dormant',0)),('to_confirm','À confirmer',pc.get('to_confirm',0)),('archived','Archivés',pc.get('archived',0)),('all','Tous',sum(pc.values()))]
            main+='<nav class="tabs portfolio-tabs">'+''.join('<a class="'+('active' if selected_state==key else '')+'" href="'+e(url('/dossiers',state=key))+'">'+e(label)+' <strong>'+str(count)+'</strong></a>' for key,label,count in tabs)+'</nav>'
            main+='<details class="technical"><summary>Indexation et mémoire de maintenance</summary>'+form('index_all','Indexer progressivement tous les dossiers enregistrés')+form('memory_all','Construire progressivement les mémoires des dossiers déjà indexés')+'<p>Ces opérations sont découpées en petits lots annulables. Pour obtenir rapidement un dossier, ouvrez sa fiche et cliquez sur « Actualiser les données du dossier ».</p></details>'
            main+='<form method="get" enctype="application/x-www-form-urlencoded"><input type="hidden" name="state" value="all"><label>Rechercher dans tous les dossiers<input name="q" value="'+e(query)+'" placeholder="Nom, référence ou chemin"></label><button>Rechercher</button></form>'
            from .workspace_ui import directory_panel
            main+='<details class="technical"'+(' open' if args.get('mail_key') else '')+'><summary>Créer ou enregistrer un dossier Nextcloud absent de la liste</summary>'+directory_panel(c,desk,matters,args,form,link)+'</details>'
            index=DocumentIndex(c['state_dir']);rows=[]
            listed=portfolio_rows(desk,None if selected_state=='all' else [selected_state],limit=5000,refresh_missing=False)
            for item in listed:
                m=by_id[item['matter']]
                if fold_for_search(query) not in fold_for_search(m['path']+' '+m['id']):continue
                n=index.db.execute("SELECT COUNT(*) FROM docs WHERE matter=? AND error='' AND text<>''",(m['id'],)).fetchone()[0]
                state_label={'active':'🟢 Actif','dormant':'🟡 En sommeil','archived':'⚪ Archivé','to_confirm':'🟠 À confirmer'}[item['state']]
                rows.append('<tr><td>'+link('/matter',matter_label(m),id=m['id'])+'<small>'+e(m['id'])+'</small></td><td><span class="portfolio-state '+e(item['state'])+'">'+state_label+'</span></td><td>'+e(date(item['last_external_activity']))+'</td><td>'+str(len(m.get('correspondents',[])))+'</td><td>'+str(n)+'</td></tr>')
            main+='<div class="table"><table><thead><tr><th>Dossier</th><th>État</th><th>Dernière activité sourcée</th><th>Correspondants</th><th>Textes indexés</th></tr></thead><tbody>'+''.join(rows)+'</tbody></table></div>'
            discovery=desk.settings('last_discovery')
            if discovery:main+='<h2>Avancement du parcours Nextcloud</h2>'+block(json.dumps(discovery,ensure_ascii=False,indent=2))
            sync=desk.settings('last_sync')
            if sync:main+='<h2>Dernière découverte</h2>'+block(json.dumps(sync,ensure_ascii=False,indent=2))
        elif path=='/matter':
            m=by_id.get(args.get('id'))
            if not m:raise Stop('dossier_absent')
            from .portfolio import matter_status
            portfolio_item=matter_status(desk,m)
            data=matter_overview(c,desk,state,m)
            state_label={'active':'🟢 Actif','dormant':'🟡 En sommeil','archived':'⚪ Archivé','to_confirm':'🟠 À confirmer'}[portfolio_item['state']]
            main+='<section class="matter-hero"><div><span class="portfolio-state '+e(portfolio_item['state'])+'">'+state_label+'</span><h2>'+e(matter_label(m))+'</h2><p>'+e(m['id']+' · '+m['path'])+'</p></div><div class="primary-actions">'+form('index','Actualiser les données du dossier',{'matter':m['id']})+form('monitor_matter','🔭 Surveiller maintenant',{'matter':m['id']})+link('/fiche','Fiche du dossier',matter=m['id'])+link('/chronologie','Chronologie',matter=m['id'])+link('/modeles-word','Créer un courrier Word',matter=m['id'])+link('/assistant','Discuter avec l’IA',matter=m['id'])+link('/strategy','Analyser la stratégie',matter=m['id'])+'</div></section>'
            from .production420_ui import smart_matter_section
            main+=smart_matter_section(desk,m,form,link)
            main+='<nav class="ws-matter-nav ws-matter-nav392" aria-label="Sections du dossier"><a href="#ws-matter-summary"><span>1</span> Essentiel</a><a href="#ws-matter-work"><span>2</span> Travail préparé</a><a href="#ws-matter-arguments"><span>3</span> Arguments et chronologie</a><a href="#ws-matter-communications"><span>4</span> Échanges et réglages</a></nav><span class="sr-only">Vue d’ensemble · Chronologie · Arguments et preuves · Travail · Documents · Honoraires</span>'
            from .learning392 import learning_context
            learned=learning_context(desk,m['id'],'assistant',record=False)
            main+='<p class="matter-learning392"><strong>Apprentissage métier :</strong> '+str(len(learned['corrections']))+' préférence(s) active(s) pour ce dossier · '+link('/apprentissage','Contrôler')+'</p>'
            main+='<section id="ws-matter-communications" class="ws-matter-contacts"><h3>Courriels et contacts du dossier</h3>'
            for person in m.get('correspondents',[]):main+='<p>'+e(person['email'])+' · '+e(ROLES.get(person['role'],person['role']))+'</p>'
            if not m.get('correspondents'):main+='<p>Aucun correspondant confirmé pour ce dossier.</p>'
            main+=link('/contacts','Voir et corriger les associations')+'</section>'
            main+='<details class="technical"><summary>Corriger le nom et les références du dossier</summary><p>Cette correction est conservée dans le registre du cabinet. L’identifiant et le chemin Nextcloud restent affichés ci-dessus pour vérification.</p>'+form('save_matter_profile','Enregistrer les corrections',{'matter':m['id']},'<label>Nom complet du client ou de l’affaire<input required name="client_name" maxlength="200" value="'+e(m.get('client_name',''))+'"></label><label>Autres noms et références (séparés par une virgule)<textarea name="references" rows="3" maxlength="2200">'+e(', '.join(x for x in m.get('references',[]) if x!=m['id']))+'</textarea></label>')+'</details>'
            main+='<details class="technical"'+(' open' if portfolio_item['state']=='to_confirm' else '')+'><summary>Vérifier ou corriger l’état du dossier</summary><p>« À confirmer » signifie que le classement du dossier attend votre vérification. Chemin et référence : '+e(m['path']+' · '+m['id'])+'. Motifs : '+e(', '.join(portfolio_item['reasons']))+'. Choisissez un état ci-dessous après contrôle des données. Pour corriger ses correspondants, ouvrez Contacts ; pour corriger une association de courriel, ouvrez ce courriel dans Messagerie.</p><div class="actions">'+form('set_matter_state','✓ Confirmer : dossier actif',{'matter':m['id'],'state':'active'})+form('set_matter_state','Confirmer : en sommeil',{'matter':m['id'],'state':'dormant'})+form('set_matter_state','Confirmer : archivé',{'matter':m['id'],'state':'archived'})+form('set_matter_state','À revoir plus tard',{'matter':m['id'],'state':'to_confirm'})+'</div>'+link('/contacts','Corriger les correspondants')+'</details>'
            main+='<div class="stats"><article><strong>'+str(len(data['documents']))+'</strong><span>Fichiers</span></article><article><strong>'+str(len(data['emails']))+'</strong><span>Courriels indexés</span></article><article><strong>'+str(len(data['drafts']))+'</strong><span>Brouillons</span></article><article><strong>'+str(len([x for x in data['tasks'] if x['status']=='open']))+'</strong><span>Tâches ouvertes</span></article></div>'
            from .proactive import signals
            matter_signals=signals(desk,'open',m['id'],20)
            if matter_signals:
                main+='<section><h2>🔭 Points signalés par la surveillance</h2>'
                for signal in matter_signals:
                    main+='<article class="signal '+e(signal['severity'])+'"><div><small>'+e(signal['severity'].upper())+'</small><h3>'+e(signal['title'])+'</h3><p>'+e(signal['detail'])+'</p><p><strong>Action proposée :</strong> '+e(signal['proposed_action'])+'</p></div><div class="signal-actions">'+form('ack_signal','✓ Vu',{'signal':signal['id']})+form('snooze_signal','⏰ Demain',{'signal':signal['id'],'hours':'24'})+form('resolve_signal','✓ Résolu',{'signal':signal['id']})+'</div></article>'
                main+='</section>'
            current=data['brief']
            if current:
                main+='<section id="ws-matter-summary" class="note"><h2>Résumé du dossier</h2><small>Mis à jour '+e(date(data['brief_row']['created']))+'</small>'+block(current.get('summary',''))
                main+='<p><strong>Juridiction :</strong> '+e(current.get('jurisdiction') or 'non établie')+' · <strong>RG :</strong> '+e(current.get('case_number') or 'non établi')+'</p>'
                for field,label in [('latest_instructions','Dernières instructions'),('open_questions','Points à décider'),('negotiation_positions','Positions de négociation'),('deadlines','Échéances')]:
                    values=current.get(field,[])
                    if values:main+='<h3>'+label+'</h3><ul>'+''.join('<li>'+e(x if isinstance(x,str) else json.dumps(x,ensure_ascii=False))+'</li>' for x in values)+'</ul>'
                if current.get('limits'):main+='<p class="notice">'+e(' · '.join(current['limits']))+'</p>'
                main+='</section>'
            else:main+='<section id="ws-matter-summary"><h2>Résumé du dossier</h2><p class="empty">Le dossier n’a pas encore de résumé. Cliquez sur « Actualiser les données du dossier » ; l’indexation puis la synthèse s’enchaînent.</p></section>'

            memory=data['memory_conf'];mc=memory['counts']
            main+='<section><div class="section-heading"><div><h2>🧠 Mémoire juridique structurée</h2><p>Informations séparées, versionnées et reliées à leurs sources. « Proposé » ne signifie pas exact.</p></div>'+form('sync_legal_memory','Actualiser la chronologie',{'matter':m['id']})+'</div>'
            main+='<div class="stats"><article><strong>'+str(mc.get('validated',0)+mc.get('pinned',0))+'</strong><span>Confirmées</span></article><article><strong>'+str(mc.get('suggested',0))+'</strong><span>À vérifier</span></article><article><strong>'+str(memory['open_conflicts'])+'</strong><span>Contradictions</span></article><article><strong>'+str(memory['timeline_events'])+'</strong><span>Événements</span></article></div>'
            if data['memory_conflicts']:
                main+='<h3>⚠️ Contradictions à examiner</h3>'
                for conflict in data['memory_conflicts']:
                    main+='<article class="memory-card conflict"><strong>'+e(conflict['reason'])+'</strong><p>'+e(conflict['record_type'])+'</p>'+form('resolve_conflict','Marquer comme examinée',{'matter':m['id'],'conflict':conflict['id']})+'</article>'
            type_labels={'party':'Partie','claim':'Prétention','instruction':'Instruction client',
              'negotiation':'Position de négociation','amount':'Montant','deadline':'Échéance',
              'event':'Événement','jurisdiction':'Juridiction','case_number':'Numéro RG',
              'completed_action':'Diligence accomplie','planned_action':'Action envisagée',
              'open_question':'Question ouverte','document':'Document','other':'Autre'}
            active=[x for x in data['memory_records'] if x['status'] not in ('archived','disputed')]
            for record in active[:100]:
                badge={'validated':'Confirmé','pinned':'Épinglé','suggested':'Proposé'}.get(record['status'],record['status'])
                main+='<article class="memory-card '+e(record['status'])+'"><small>'+e(type_labels.get(record['record_type'],record['record_type']))+' · '+e(badge)+' · confiance '+e(str(round(record['confidence'],2)))+'</small><h3>'+e(record['title'])+'</h3>'+block(record['content'])
                if record['actor']:main+='<p><strong>Auteur de l’affirmation :</strong> '+e(record['actor'])+'</p>'
                if record['event_date']:main+='<p><strong>Date indiquée :</strong> '+e(record['event_date'])+'</p>'
                if record['source_snapshot']:
                    main+='<details><summary>Sources ('+str(len(record['source_snapshot']))+')</summary>'
                    for source in record['source_snapshot']:
                        main+='<p><strong>'+e(source.get('path') or source['id'])+'</strong><br><small>'+e(source.get('modified',''))+' · '+e(source['id'])+'</small></p>'+block(source.get('excerpt',''))
                    main+='</details>'
                fields={'matter':m['id'],'record':record['id']}
                main+='<div class="actions">'+form('validate_memory','Confirmer',fields)+form('pin_memory','Épingler',fields)+form('dispute_memory','Contester',fields)+form('archive_memory','Archiver',fields)+'</div>'
                main+='<details><summary>Corriger avant confirmation</summary>'+form('validate_memory','Enregistrer la correction',fields,'<label>Information corrigée<textarea required name="text" rows="3" maxlength="8000">'+e(record['content'])+'</textarea></label><label>Note de validation<input name="note" maxlength="1000"></label>')+'</details></article>'
            if not active:main+='<p class="empty">Aucune information structurée. Actualisez le résumé après l’indexation du dossier.</p>'
            main+='</section>'

            from .operating380_ui import matter_graph_section
            main+=matter_graph_section(desk,m,form,link)

            main+='<section id="ws-matter-timeline"><h2>Chronologie unifiée</h2><p>Courriels, documents, brouillons, tâches et faits sourcés, du plus récent au plus ancien.</p><div class="timeline">'
            for item in data['timeline']:
                heading=link('/mail',item['title'],key=item['key']) if item.get('key') else e(item['title'])
                source=(' · '+item.get('source_path','')) if item.get('source_path') else ''
                main+='<article><time>'+e(date(item['at']))+'</time><span class="kind">'+e(item['kind'])+'</span><strong>'+heading+'</strong><small>'+e(item.get('text','')+source)+'</small></article>'
            main+=('</div></section>' if data['timeline'] else '<p class="empty">Aucun élément daté n’est encore indexé.</p></div></section>')

            main+='<section id="ws-matter-work"><h2>Travail préparé — Tâches et diligences</h2>'
            open_tasks=[x for x in data['tasks'] if x['status']=='open']
            main+=(''.join('<article class="card"><strong>'+e(x['title'])+'</strong><small>Échéance : '+e(date(x['due']))+'</small></article>' for x in open_tasks) or '<p class="empty">Aucune tâche ouverte enregistrée.</p>')
            if current:
                for field,label in [('actions_completed','Réalisées et sourcées'),('actions_planned','Envisagées')]:
                    if current.get(field):main+='<h3>'+label+'</h3><ul>'+''.join('<li>'+e(x)+'</li>' for x in current[field])+'</ul>'
            main+='</section>'

            main+='<section id="ws-matter-drafts"><h2>Brouillons de courriels</h2>'
            for draft in data['drafts'][:30]:
                title=link('/mail',draft['subject'],key=draft['key']) if draft.get('key') else e(draft['subject'])
                main+='<details><summary>'+title+' · '+e(draft['location'])+'</summary><small>'+e(date(draft['modified']))+'</small>'+block(draft['body'][:6000])+'</details>'
            if not data['drafts']:main+='<p class="empty">Aucun brouillon lié à ce dossier.</p>'
            main+='</section><section><h2>Courriels du dossier</h2>'
            for mail in data['emails'][:100]:
                title=link('/mail',mail['subject'],key=mail['key']) if mail.get('key') else e(mail['subject'])
                main+='<details><summary>'+e(mail['direction'])+' · '+title+'</summary><small>'+e(date(mail['modified']))+' · '+e(mail['sender'])+'</small>'+block(mail['text'][:3500])+'</details>'
            if not data['emails']:main+='<p class="empty">Aucun courriel n’est encore indexé dans ce dossier.</p>'
            main+='</section><section id="ws-matter-files"><h2>Fichiers Nextcloud</h2><p>'+str(len(data['folders']))+' sous-dossier(s), '+str(len(data['documents']))+' fichier(s), dont '+str(data['document_errors'])+' erreur(s) d’extraction.</p>'
            nc=c['nextcloud'].get('url','').rstrip('/')
            if urlsplit(nc).scheme=='https':main+='<p><a rel="noreferrer" target="_blank" href="'+e(nc+'/index.php/apps/files/?'+urlencode({'dir':m['path']}))+'">Ouvrir ce dossier dans Nextcloud</a></p>'
            if data['folders']:main+='<details><summary>Arborescence des dossiers</summary><ul>'+''.join('<li>'+e(x)+'</li>' for x in data['folders'])+'</ul></details>'
            for doc in sorted(data['documents'],key=lambda x:moment(x['modified']),reverse=True)[:200]:
                main+='<details><summary>'+e(doc['name'])+'</summary><small>'+e(date(doc['modified']))+' · '+e(doc['path'])+'</small>'+block((doc['error'] or doc['text'])[:1800])+'</details>'
            if not data['documents']:main+='<p class="empty">Aucun fichier indexé. Actualisez les données du dossier.</p>'
            from .operating380_ui import matter_fees_section
            main+='</section>'+matter_fees_section(desk,m,link)+'<details id="ws-matter-admin" class="technical"><summary><span id="ws-matter-actions">Actions, automatisations et correspondants</span></summary><div class="actions">'+form('refresh_brief','Recalculer seulement le résumé',{'matter':m['id']})+form('retry_matter','Relancer les courriels non lus associés',{'matter':m['id']})+'</div>'
            from .workstation import workspace_mapping,unpaid_summary
            mapping=workspace_mapping(desk,m['id']);invoices=unpaid_summary(desk,m['id'])
            main+='<h3>Correspondance Open WebUI</h3>'
            if mapping:main+='<p>Dossier lié : <a target="_blank" rel="noopener noreferrer" href="'+e(mapping['folder_url'])+'">'+e(mapping['folder_name'])+'</a>.</p>'
            main+=form('save_workspace_mapping','Enregistrer la correspondance',{'matter':m['id']},'<label>Identifiant du dossier Open WebUI<input required name="folder_id" maxlength="200" value="'+e(mapping['folder_id'] if mapping else '')+'"></label><label>Nom affiché<input required name="folder_name" maxlength="300" value="'+e(mapping['folder_name'] if mapping else '')+'"></label><label>URL exacte du dossier Open WebUI<input type="url" required name="folder_url" maxlength="1000" value="'+e(mapping['folder_url'] if mapping else '')+'"></label>')
            main+='<h3>Correspondance Invoice Ninja</h3><p>'+((str(invoices['count'])+' facture(s) impayée(s) liée(s), '+e(invoices['balance_display'])+' dans le cache du '+e(invoices['last_sync'])+'.') if invoices['synchronized'] and invoices['linked'] else 'Factures non déterminées : '+('ce dossier n’est relié à aucun client Invoice Ninja.' if not invoices['linked'] else 'aucune synchronisation vérifiée.'))+'</p>'+form('link_invoice_client','Lier le client Invoice Ninja',{'matter':m['id']},'<label>Identifiant client Invoice Ninja<input required name="client_id" maxlength="100"></label>')
            main+='<h3>Correspondants</h3>'
            for p in m.get('correspondents',[]):main+='<div class="contact"><span>'+e(p['email'])+' · '+e(ROLES[p['role']])+'</span>'+form('remove_contact','Retirer',{'matter':m['id'],'email':p['email']},'<label class="ws-check"><input required type="checkbox" name="confirm" value="yes">Confirmer le retrait de ce dossier</label>')+'</div>'
            main+='<h3>Ajouter ou corriger un correspondant</h3>'+form('associate','Enregistrer',{'matter':m['id']},'<label>Adresse<input type="email" required name="email"></label><label>Rôle<select required name="role"><option value="">Choisir…</option>'+''.join('<option value="'+k+'">'+e(v)+'</option>' for k,v in ROLES.items())+'</select></label>')
            facts=desk.db.execute("SELECT * FROM case_facts WHERE matter=? AND status<>'archived' ORDER BY updated DESC LIMIT 80",(m['id'],)).fetchall()
            if facts:
                main+='<h3>Informations extraites</h3>'
                for fact in facts:
                    fields={'fact':fact['id'],'matter':m['id']}
                    main+='<article class="card"><small>'+e(fact['category']+' · '+fact['status']+' · sources '+fact['sources'])+'</small>'+block(fact['text'])+'<div class="actions">'+form('validate_fact','Valider',fields)+form('pin_fact','Épingler',fields)+form('archive_fact','Archiver',fields)+'</div></article>'
            main+='</details>'
        elif path=='/strategy':
            from .strategic_ui import panel
            main+=panel(c,desk,matters,args,form,link)
        elif path=='/assistant':
            from .workspace_ui import assistant_panel
            main+=assistant_panel(c,desk,matters,args,form,link)
        elif path=='/memoire':
            memory=SentMemory(c);info=memory.status()
            main+='<div class="stats"><article><strong>'+str(info['examples'])+'</strong><span>Exemples conservés</span></article><article><strong>'+str(info['correction_pairs'])+'</strong><span>Corrections rapprochées</span></article><article><strong>'+str(info.get('accepted_preferences',0))+'</strong><span>Préférences confirmées</span></article></div>'
            main+='<p>Apprentissage '+('activé' if info['enabled'] else 'désactivé')+' depuis '+e(info['sent_folder'])+'. Les exemples sont séparés par dossier, rôle et destinataires exacts.</p>'+form('learn','Actualiser les réponses envoyées')
            rows=memory.db.execute('SELECT key,matter,recipients,sent_at,body,draft,stats,audience FROM examples_v2 WHERE account=? ORDER BY sent_at DESC LIMIT 100',(memory.account(),)).fetchall()
            for key,mid,targets,stamp,body,draft,stats,audience in rows:
                main+='<details><summary>'+e(date(stamp))+' · '+e(mid.split('@')[0])+' · '+e(', '.join(json.loads(targets)))+'</summary><p>'+e(ROLES.get(json.loads(audience)[0][1],''))+'</p><h3>Réponse envoyée</h3>'+block(body)
                if draft:main+='<h3>Brouillon initial</h3>'+block(draft)+'<p>Comparaison textuelle : '+e(stats)+'. Ce chiffre ne mesure pas la qualité.</p>'
                review=memory.db.execute('SELECT data FROM example_reviews WHERE key=?',(key,)).fetchone()
                if review:
                    insight=json.loads(review[0])['result'];main+='<h3>Ce que l’agent propose de retenir</h3>'+block(insight['summary'])
                    main+='<ul>'+''.join('<li>'+e(x)+'</li>' for x in insight['style_preferences']+insight['useful_corrections'])+'</ul>'
                    if insight['do_not_generalize']:main+='<p class="notice">À ne pas généraliser : '+e(' · '.join(insight['do_not_generalize']))+'</p>'
                    main+='<div class="actions">'+form('memory_scope','Appliquer comme préférence générale',{'key':key,'scope':'general'})+form('memory_scope','Limiter à ce rôle',{'key':key,'scope':'role'})+form('memory_scope','Limiter à ce dossier',{'key':key,'scope':'matter'})+form('memory_scope','Limiter à ce correspondant',{'key':key,'scope':'recipient'})+form('memory_scope','Correction exceptionnelle',{'key':key,'scope':'exceptional'})+'</div>'
                else:main+=form('memory_insight','Afficher ce que l’agent peut retenir',{'key':key})
                main+='<div class="actions">'+form('memory_scope','Ne pas apprendre de cette réponse',{'key':key,'scope':'never'})+form('forget','Oublier',{'key':key})+'</div></details>'
            if not rows:main+='<p class="empty">La mémoire est vide. Consultez les associations proposées et la synchronisation des Envoyés.</p>'
            preferences=memory.db.execute("SELECT scope,text,created FROM preferences WHERE account=? AND status='accepted' ORDER BY created DESC LIMIT 50",(memory.account(),)).fetchall()
            if preferences:main+='<h2>Préférences actives</h2>'+''.join('<details><summary>'+e(p[0]+' · '+date(p[2]))+'</summary>'+block(p[1])+'</details>' for p in preferences)
            if info.get('last_learning'):main+='<h2>Dernier passage d’apprentissage</h2>'+block(json.dumps(info['last_learning'],ensure_ascii=False,indent=2))
        elif path=='/qualite':
            from .relevance370_ui import quality_summary
            main+=quality_summary(desk)
            total=sum(counts.values());drafted=counts.get('drafted',0);ignored=counts.get('ignored',0)
            durations=[]
            for key,status,_,stamp in state.rows(1000):
                if status!='drafted':continue
                try:
                    started=datetime.fromisoformat(report_for(c,key)['started_at']);finished=datetime.fromisoformat(stamp)
                    durations.append(max(0,(finished-started).total_seconds()))
                except (Stop,ValueError,KeyError,TypeError):pass
            main+='<p>Ces indicateurs décrivent les traitements observés ; ils ne constituent pas une mesure de justesse juridique. Les boutons d’évaluation rendent progressivement les chiffres plus représentatifs.</p>'
            main+='<div class="stats"><article><strong>'+str(total)+'</strong><span>Courriels traités</span></article><article><strong>'+str(round(100*drafted/total,1) if total else 0)+' %</strong><span>Brouillons créés</span></article><article><strong>'+str(round(100*ignored/total,1) if total else 0)+' %</strong><span>Messages ignorés</span></article><article><strong>'+str(round(sum(durations)/len(durations),1) if durations else 0)+' s</strong><span>Délai moyen de création</span></article></div>'
            feedback_rows=desk.db.execute('SELECT category,COUNT(*) FROM feedback GROUP BY category ORDER BY COUNT(*) DESC').fetchall()
            main+='<h2>Évaluations explicites</h2>'+('<div class="table"><table><tbody>'+''.join('<tr><td>'+e(category)+'</td><td>'+str(n)+'</td></tr>' for category,n in feedback_rows)+'</tbody></table></div>' if feedback_rows else '<p class="empty">Aucune évaluation encore enregistrée.</p>')
            reasons={}
            for _,status,reason,_ in state.rows(1000):reasons[reason]=reasons.get(reason,0)+1
            main+='<h2>Motifs de blocage ou de classement</h2><div class="table"><table><tbody>'+''.join('<tr><td>'+e(REASONS.get(reason,reason))+'</td><td>'+str(n)+'</td></tr>' for reason,n in sorted(reasons.items(),key=lambda x:-x[1])[:20])+'</tbody></table></div>'
            memory=SentMemory(c);stats=[]
            for row in memory.db.execute("SELECT stats FROM examples_v2 WHERE account=? AND provenance='matched_draft_and_sent'",(memory.account(),)):
                try:
                    value=json.loads(row[0]);stats.append(value['similarity'])
                except (ValueError,KeyError,TypeError):pass
            strong=len([x for x in stats if x<0.5]);sent_rate=round(100*len(stats)/drafted,1) if drafted else 0
            main+='<h2>Corrections des brouillons envoyés</h2><p>'+str(len(stats))+' rapprochements ('+str(sent_rate)+' % des brouillons créés), dont '+str(strong)+' fortement corrigé(s) ; similarité textuelle moyenne : '+e(round(sum(stats)/len(stats),3) if stats else 'non mesurable')+'. Une faible similarité signale une forte correction, pas nécessairement un mauvais projet.</p>'
            useful={}
            for key,_,_,_ in state.rows(500):
                try:r=report_for(c,key)
                except Stop:continue
                for source in r.get('sources',[]):
                    if source.get('path'):useful[source['path']]=useful.get(source['path'],0)+1
            main+='<h2>Sources les plus souvent fournies à la rédaction</h2>'+(''.join('<p>'+e(path)+' · '+str(n)+' utilisation(s)</p>' for path,n in sorted(useful.items(),key=lambda x:-x[1])[:15]) or '<p class="empty">Aucune source documentaire utilisée.</p>')
        jobs=desk.db.execute('SELECT * FROM jobs ORDER BY id DESC LIMIT 8').fetchall()
        if jobs and path=='/administration':
            main+='<section class="operations"><h2>Opérations récentes</h2>'
            for j in jobs:
                main+='<details><summary>#'+str(j['id'])+' · '+e(JOB_LABELS.get(j['kind'],j['kind']))+' · '+e(LABELS.get(j['status'],j['status']))+'</summary><small>'+e(date(j['created']))+' · priorité '+str(j['priority'])+'</small>'+block(j['result'] or 'En attente du service de traitement.')
                if j['status'] in ('pending','running'):main+=form('cancel_job','Annuler cette opération',{'job':j['id']})
                main+='</details>'
            main+='</section>'
        if path=='/parametres':
            main+='<p class="notice"><a href="'+e(url('/atelier/reglages'))+'"><strong>Éditeur de documents et avis de procédure</strong></a> — relier OnlyOffice / Euro-Office, choisir votre rôle par dossier.</p>'
            from .live430 import settings_html
            if args.get('tab')=='automatismes':main+=settings_html(desk,prefix,auth['csrf'])
        toast=''
        active=desk.db.execute("SELECT kind,status FROM jobs WHERE status IN ('pending','running','cancel_requested') ORDER BY CASE status WHEN 'running' THEN 0 WHEN 'cancel_requested' THEN 1 ELSE 2 END,priority,id LIMIT 1").fetchone()
        if active:
            waiting=desk.db.execute("SELECT COUNT(*) FROM jobs WHERE status='pending'").fetchone()[0]
            verb='En cours' if active['status']=='running' else 'En attente'
            toast='<div class="task-toast"><span class="pulse"></span><div><strong>'+e(verb+' : '+JOB_LABELS.get(active['kind'],active['kind']))+'</strong><small>'+str(waiting)+' opération'+('s' if waiting!=1 else '')+' en attente · '+link(path,'Actualiser',**args)+'</small></div></div>'
        from .ui_helpers import enhance
        selected_matter=str(args.get('id') or args.get('matter') or '')
        return enhance('<!doctype html><html lang="fr"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"><title>'+e(title)+' · AxiorHub Pilote</title><link rel="stylesheet" href="'+e(url('/static/app520.css'))+'?v='+e(_ax_version())+'"></head><body>'+toast+shell501.sidebar_html(prefix,path)+'<main id="ws-main">'+main+'</main></body></html>',prefix,auth['csrf'],bool(c.get('audio',{}).get('enabled')),matters,path,selected_matter)


def fold_for_search(value):
    from .common import fold
    return fold(value)


def serve(config_path,auth_path):
    from waitress import serve as waitress_serve
    # send_bytes=1 : chaque écriture part tout de suite (sinon Waitress retient ~18 Ko et les événements du flux SSE restent en attente).
    waitress_serve(App(config_path,auth_path),host='127.0.0.1',port=8769,threads=24,send_bytes=1,
                   max_request_body_size=20_100_000,trusted_proxy='127.0.0.1',
                   trusted_proxy_headers={'x-forwarded-proto','x-forwarded-for'},clear_untrusted_proxy_headers=True)


def _ax_version():
    from . import __version__
    return __version__
