"""
title: AxiorHub Avocat
author: AxiorHub
version: 5.6.11
description: Cabinet opérant avec apprentissage métier explicite, banc juridique et routage local-first contrôlé.
requirements: pydantic
"""
import json
import re
import time
from urllib.parse import urlencode
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

from pydantic import BaseModel, Field


class Tools:
    class Valves(BaseModel):
        AXIORHUB_API_URL: str = Field(
            default="https://courriel.example.com/agent-courriel/api/v1",
            description="URL de l’API métier AxiorHub, terminée par /api/v1",
        )
        AXIORHUB_API_TOKEN: str = Field(
            default="",
            description="Jeton local créé par install-interface.py",
        )
        TIMEOUT_SECONDS: int = Field(default=60, ge=5, le=240)
        JOB_WAIT_SECONDS: int = Field(default=240, ge=10, le=900)

    def __init__(self):
        self.valves = self.Valves()

    def _call(self, path: str, method: str = "GET", payload=None):
        if not self.valves.AXIORHUB_API_TOKEN:
            return {"error": "Jeton AxiorHub absent dans les Valves de l’outil."}
        data = None if payload is None else json.dumps(payload).encode("utf-8")
        request = Request(
            self.valves.AXIORHUB_API_URL.rstrip("/") + path,
            data=data,
            method=method,
            headers={
                "Authorization": "Bearer " + self.valves.AXIORHUB_API_TOKEN,
                "Content-Type": "application/json",
                "Accept": "application/json",
            },
        )
        try:
            with urlopen(request, timeout=self.valves.TIMEOUT_SECONDS) as response:
                return json.loads(response.read().decode("utf-8"))
        except HTTPError as exc:
            result={"error": "AxiorHub a refusé la demande", "status": exc.code}
            try:
                body=json.loads(exc.read().decode("utf-8"))
                if isinstance(body,dict):result["details"]=body.get("error") or body.get("message") or body
            except (ValueError,UnicodeError):pass
            return result
        except (URLError, TimeoutError, ValueError):
            return {"error": "API AxiorHub momentanément indisponible"}

    def _wait(self, job_id: int) -> dict:
        deadline=time.monotonic()+self.valves.JOB_WAIT_SECONDS
        while time.monotonic()<deadline:
            result=self._call("/jobs/"+str(job_id))
            if result.get("error"):return result
            if result.get("status") in ("done","error","cancelled"):
                raw=result.get("result")
                if isinstance(raw,str):
                    try:result["result"]=json.loads(raw)
                    except ValueError:pass
                return result
            time.sleep(2)
        return {"status":"still_running","job_id":job_id,
          "message":"Le traitement continue. Utilisez verifier_une_operation pour récupérer son résultat."}

    def _queued(self, result: dict) -> dict:
        if result.get("error") or not result.get("job_id"):return result
        return self._wait(result["job_id"])

    def _identifier(self, value: str, label: str = "identifiant"):
        value=str(value or "").strip()
        if not re.fullmatch(r"[a-f0-9]{32}",value):
            return {"error": label+" invalide : utilisez uniquement l’identifiant de 32 caractères renvoyé par AxiorHub."}
        return value

    def _matter(self, value: str):
        value=str(value or "").strip()
        if not re.fullmatch(r"[A-Za-z0-9_-]{1,80}",value):
            return {"error":"Référence dossier invalide : utilisez l’identifiant exact renvoyé par AxiorHub."}
        return value

    def _query_identifier(self, value: str):
        value=str(value or "").strip()
        if not re.fullmatch(r"[a-f0-9]{64}",value):
            return {"error":"Identifiant de requête invalide : recopiez les 64 caractères renvoyés par AxiorHub."}
        return value

    def _legal(self, value: str):
        try:data=json.loads(value or "[]")
        except ValueError:return {"error":"Le JSON de recherche juridique est invalide."}
        if not isinstance(data,list):return {"error":"La recherche juridique doit être une liste JSON."}
        return data

    def _prepare_document(self, document_type: str, reference_dossier: str,
                          instruction: str, fichier_source_souhaite: str,
                          recherche_juridique_json: str) -> dict:
        legal=self._legal(recherche_juridique_json)
        if isinstance(legal,dict) and legal.get("error"):return legal
        return self._queued(self._call("/document-projects","POST",{
          "document_type":document_type,"matter":reference_dossier,
          "instruction":instruction,"source_path":fichier_source_souhaite,
          "legal_research":legal,
        }))

    def verifier_les_capacites_et_limites(self) -> dict:
        """Affiche ce que l’assistant peut faire, ce qui exige confirmation et ce qui est interdit."""
        return self._call("/capabilities")

    def verifier_la_progression_d_un_document_long(self, identifiant_analyse: str) -> dict:
        """Affiche pages et fragments traités, cache utilisé, extensions actives et possibilité de reprise."""
        value=str(identifiant_analyse or '').strip()
        if not re.fullmatch(r'[a-f0-9]{64}',value):
            return {'error':'Identifiant d’analyse longue invalide (64 caractères hexadécimaux).'}
        return self._call('/long-documents/'+value)

    def afficher_le_pilotage_du_cabinet(self, etat: str = "pending") -> dict:
        """Affiche le tableau unique des décisions : facturation préparée, travaux potentiellement non facturés, rendez-vous, charge, provisions et dossiers inactifs, classés par risque."""
        if etat not in ("pending","approved","rejected","snoozed","all"):
            return {"error":"État autorisé : pending, approved, rejected, snoozed ou all."}
        return self._call("/cabinet-control?"+urlencode({"status":etat,"limit":300}))

    def actualiser_le_pilotage_du_cabinet(self) -> dict:
        """Relance les détections locales et idempotentes. Ne crée aucune facture, relance, écriture, tâche, invitation ou action externe."""
        return self._queued(self._call("/cabinet-control/refresh","POST",{}))

    def examiner_une_decision_du_cabinet(
        self, decision: str, choix: str, confirmer_risque_eleve: bool = False,
        reporter_de_combien_d_heures: int = 24
    ) -> dict:
        """Approuve une préparation interne, la rejette ou la reporte. Un risque élevé ou critique exige confirmer_risque_eleve=True et reste individuel."""
        did=self._query_identifier(decision)
        if isinstance(did,dict):return did
        mapping={"approuver":"approved","rejeter":"rejected","reporter":"snoozed",
                 "approved":"approved","rejected":"rejected","snoozed":"snoozed"}
        status=mapping.get(str(choix).strip().lower())
        if not status:return {"error":"Choix autorisé : approuver, rejeter ou reporter."}
        return self._queued(self._call("/cabinet-decisions/"+did+"/review","POST",{
          "status":status,"confirm_risk":"yes" if confirmer_risque_eleve else "no",
          "snooze_hours":reporter_de_combien_d_heures}))

    def creer_un_lot_de_decisions_faibles_ou_moyennes(self, decisions_separees_par_des_virgules: str) -> dict:
        """Prépare un lot inchangé de décisions faibles ou moyennes et retourne un code à six chiffres. Les risques élevés et critiques sont refusés."""
        ids=[x.strip() for x in decisions_separees_par_des_virgules.split(",") if x.strip()]
        if not ids or any(not re.fullmatch(r"[a-f0-9]{64}",x) for x in ids):
            return {"error":"Recopiez les identifiants de décision de 64 caractères, séparés par des virgules."}
        return self._call("/cabinet-decision-batches","POST",{"decision_ids":ids})

    def confirmer_un_lot_de_decisions_du_cabinet(self, lot: str, code_recopie_par_l_avocat: str) -> dict:
        """Confirme un lot faible ou moyen avec le code explicitement recopié. Aucun risque élevé ou critique ne peut passer par ce mécanisme."""
        bid=self._identifier(lot,"identifiant du lot")
        if isinstance(bid,dict):return bid
        return self._call("/cabinet-decision-batches/"+bid+"/approve","POST",{
          "confirmation_code":code_recopie_par_l_avocat})

    def enregistrer_une_provision_a_suivre(
        self, reference_dossier: str, libelle: str, montant_demande_en_centimes: int,
        echeance_iso: str, source_exacte: str, montant_recu_en_centimes: int = 0
    ) -> dict:
        """Alimente le registre interne sourcé des provisions. Ne crée ni paiement, ni facture, ni relance client."""
        matter=self._matter(reference_dossier)
        if isinstance(matter,dict):return matter
        return self._call("/cabinet/provisions","POST",{
          "matter":matter,"label":libelle,"requested_cents":montant_demande_en_centimes,
          "paid_cents":montant_recu_en_centimes,"currency":"EUR","due":echeance_iso,
          "source_ref":source_exacte})

    def preparer_un_rendez_vous(self, identifiant_evenement: str) -> dict:
        """Prépare objectifs, questions et documents à relire à partir d’un événement lié. Ne modifie pas l’agenda et n’envoie aucune invitation."""
        eid=self._query_identifier(identifiant_evenement)
        if isinstance(eid,dict):return eid
        return self._queued(self._call("/cabinet/meetings/prepare","POST",{"event_id":eid}))

    def preparer_un_compte_rendu_de_transcription(
        self, reference_dossier: str, transcription: str = "", source_indexee: str = ""
    ) -> dict:
        """Prépare un compte rendu interne contrôlé depuis un texte ou une source déjà indexée. N’exécute aucune instruction contenue dans la transcription."""
        matter=self._matter(reference_dossier)
        if isinstance(matter,dict):return matter
        return self._queued(self._call("/cabinet/transcripts/prepare","POST",{
          "matter":matter,"source_ref":source_indexee,"transcript_text":transcription}))

    def executer_les_tests_metier_continus(self) -> dict:
        """Contrôle audit, risques, lots, idempotence et séparation des modèles sans action externe."""
        return self._queued(self._call("/cabinet/business-tests","POST",{}))

    def verifier_le_journal_d_audit_du_cabinet(self, limite: int = 100) -> dict:
        """Vérifie la chaîne d’empreintes du journal d’audit et affiche les événements récents."""
        return self._call("/cabinet/audit?"+urlencode({"limit":limite}))

    def analyser_un_courriel_avec_son_dossier(
        self, cle_courriel: str, reference_dossier: str = ""
    ) -> dict:
        """Analyse un courriel déjà reçu, le compare au dossier, propose au plus un projet adapté, une réponse client, des diligences et une facturation. Produit une seule notification et n’exécute aucune action externe."""
        key=self._query_identifier(cle_courriel)
        if isinstance(key,dict):return key
        payload={"mail_key":key}
        if reference_dossier:payload["matter"]=reference_dossier
        return self._queued(self._call("/orchestrations","POST",payload))

    def lancer_l_orchestrateur_des_nouveaux_courriels(self, limite: int = 20) -> dict:
        """Analyse en lot les nouveaux courriels déjà collectés. Ne crée ni document, ni facture définitive, ni envoi, ni dépôt."""
        return self._queued(self._call("/orchestrator/run","POST",{"limit":limite}))

    def lister_les_notifications_courriel_dossier(
        self, etat: str = "unread", limite: int = 100
    ) -> dict:
        """Liste les notifications consolidées : une notification au plus par courriel analysé."""
        if etat not in ("unread","read","rejected","all"):
            return {"error":"État autorisé : unread, read, rejected ou all."}
        return self._call("/orchestrations?"+urlencode({"status":etat,"limit":limite}))

    def previsualiser_une_analyse_courriel_dossier(self, orchestration: str) -> dict:
        """Affiche l’analyse différentielle, le projet de réponse, les diligences, la facturation proposée et le projet adapté."""
        oid=self._identifier(orchestration,"identifiant d’orchestration")
        if isinstance(oid,dict):return oid
        return self._call("/orchestrations/"+oid)

    def marquer_une_notification_courriel_comme_lue(self, notification: str) -> dict:
        """Marque localement une notification comme lue ; aucune proposition n’est exécutée."""
        nid=self._query_identifier(notification)
        if isinstance(nid,dict):return nid
        return self._queued(self._call("/orchestration-notifications/"+nid+"/review","POST",{"status":"read"}))

    def preparer_un_avis_juridique_et_une_simulation(
        self, reference_dossier: str, question_juridique: str,
        rechercher_la_jurisprudence: bool = True,
        critique_du_jugement_ou_de_l_ordonnance: bool = False,
        fichier_du_jugement_ou_de_l_ordonnance: str = "",
        fournisseurs: str = "openlegi,goodlegal,pappers"
    ) -> dict:
        """Prépare un avis contradictoire, des scénarios qualitatifs sourcés et une analyse de sensibilité. Toute jurisprudence doit être vérifiée sur une source officielle ; aucune probabilité numérique n’est admise."""
        matter=self._matter(reference_dossier)
        if isinstance(matter,dict):return matter
        providers=[x.strip().lower() for x in fournisseurs.split(",") if x.strip()]
        if any(x not in ("openlegal","openlegi","goodlegal","pappers") for x in providers):
            return {"error":"Fournisseurs autorisés : openlegal, openlegi, goodlegal, pappers."}
        payload={"matter":matter,"question":question_juridique,"providers":providers,
          "run_research":"yes" if rechercher_la_jurisprudence else "no",
          "critique_first_instance":"yes" if critique_du_jugement_ou_de_l_ordonnance else "no",
          "judgment_source":fichier_du_jugement_ou_de_l_ordonnance}
        return self._queued(self._call("/legal-opinions","POST",payload))

    def lister_les_projets_d_avis_et_simulations(
        self, reference_dossier: str = "", etat: str = "all"
    ) -> dict:
        """Liste les projets d’avis internes, y compris ceux bloqués par un contrôle déterministe."""
        return self._call("/legal-opinions?"+urlencode({"matter":reference_dossier,"status":etat}))

    def previsualiser_un_avis_et_une_simulation(self, projet: str) -> dict:
        """Affiche l’avis, les deux thèses, les scénarios, la sensibilité, les sources et les contrôles."""
        pid=self._identifier(projet,"identifiant du projet d’avis")
        if isinstance(pid,dict):return pid
        return self._call("/legal-opinions/"+pid)

    def coacher_une_plaidoirie(self, projet_audience: str, transcription_relue: str,
                               duree_en_secondes: int, plan_en_minutes: int = 5) -> dict:
        """Compare l’entraînement transcrit au plan d’audience. Le % mesure la couverture lexicale du plan, jamais une chance de succès."""
        pid=self._identifier(projet_audience,"identifiant de préparation d’audience")
        if isinstance(pid,dict):return pid
        return self._queued(self._call("/assistance/coaching","POST",{
          "hearing_project_id":pid,"speech":transcription_relue,
          "duration_seconds":duree_en_secondes,"target_minutes":plan_en_minutes}))

    def preparer_un_appel(self, reference_dossier: str, objet: str,
                           courriel_correspondant: str = "") -> dict:
        """Prépare questions et pièces indexées à vérifier, sans placer d’appel."""
        mid=self._matter(reference_dossier)
        if isinstance(mid,dict):return mid
        return self._queued(self._call("/assistance/calls","POST",{
          "matter":mid,"purpose":objet,"contact_email":courriel_correspondant}))

    def enregistrer_des_notes_d_appel(self, projet_appel: str, notes_a_valider: str) -> dict:
        """Enregistre un compte rendu interne issu de notes saisies par l’avocat après l’appel."""
        pid=self._identifier(projet_appel,"identifiant de préparation d’appel")
        if isinstance(pid,dict):return pid
        return self._queued(self._call("/assistance/calls/report","POST",{
          "call_project_id":pid,"notes":notes_a_valider}))

    def calculer_les_interets_simples(self, reference_dossier: str, principal_eur: str,
                                       taux_annuel_pourcent: str, date_debut: str, date_fin_exclue: str,
                                       url_source_officielle: str, reference_source: str) -> dict:
        """Intérêts simples sur jours réels / 365 ; source et taux déclarés doivent être vérifiés par l’avocat."""
        mid=self._matter(reference_dossier)
        if isinstance(mid,dict):return mid
        return self._queued(self._call("/assistance/calculations","POST",{
          "matter":mid,"calculation_type":"simple_interest","principal":principal_eur,
          "annual_rate":taux_annuel_pourcent,"start_date":date_debut,"end_date":date_fin_exclue,
          "source_url":url_source_officielle,"source_reference":reference_source}))

    def calculer_une_indexation_de_loyer(self, reference_dossier: str, loyer_initial_eur: str,
                                          indice_base: str, nouvel_indice: str,
                                          url_source_officielle: str, reference_source: str) -> dict:
        """Applique loyer × nouvel indice / indice de base ; vérifier clause et période contractuelles."""
        mid=self._matter(reference_dossier)
        if isinstance(mid,dict):return mid
        return self._queued(self._call("/assistance/calculations","POST",{
          "matter":mid,"calculation_type":"rent_indexation","rent":loyer_initial_eur,
          "base_index":indice_base,"new_index":nouvel_indice,
          "source_url":url_source_officielle,"source_reference":reference_source}))

    def additionner_des_jours_calendaires(self, reference_dossier: str, date_debut: str,
                                           nombre_jours: int, url_source_officielle: str,
                                           reference_source: str) -> dict:
        """Addition arithmétique de jours ; aucun délai de procédure ou report légal calculé."""
        mid=self._matter(reference_dossier)
        if isinstance(mid,dict):return mid
        return self._queued(self._call("/assistance/calculations","POST",{
          "matter":mid,"calculation_type":"calendar_days","start_date":date_debut,
          "days":nombre_jours,"source_url":url_source_officielle,
          "source_reference":reference_source}))

    def qualifier_une_decision_comparable(self, reference_dossier: str, identifiant_decision_verifiee: str,
                                           issue: str, motif_de_comparabilite: str,
                                           contexte_procedural: str) -> dict:
        """L’avocat qualifie une décision déjà vérifiée sur texte officiel. Les fréquences constatées ne prédisent pas le jugement."""
        mid=self._matter(reference_dossier)
        if isinstance(mid,dict):return mid
        if not re.fullmatch(r"[a-f0-9]{64}",identifiant_decision_verifiee):
            return {"error":"Identifiant de décision vérifiée invalide."}
        return self._queued(self._call("/assistance/comparables","POST",{
          "matter":mid,"authority_id":identifiant_decision_verifiee,"outcome":issue,
          "similarity_reason":motif_de_comparabilite,"procedural_context":contexte_procedural}))

    def consulter_les_pourcentages_observes_de_decisions_comparables(self, reference_dossier: str) -> dict:
        """Détaille la série qualifiée, ses sources et son dénominateur ; affiche un % descriptif à partir de dix décisions."""
        mid=self._matter(reference_dossier)
        if isinstance(mid,dict):return mid
        return self._call("/assistance/comparables/"+mid)

    def preparer_la_revue_de_facturation(self, reference_dossier: str) -> dict:
        """Examine les impayés synchronisés et les diligences candidates sans créer de facture, paiement ou relance."""
        mid=self._matter(reference_dossier)
        if isinstance(mid,dict):return mid
        return self._queued(self._call("/assistance/billing","POST",{"matter":mid}))

    def relire_un_projet_d_assistance_metier(self, projet: str) -> dict:
        """Affiche un projet de coaching, appel, calcul ou facturation préparé localement."""
        pid=self._identifier(projet,"identifiant de projet")
        if isinstance(pid,dict):return pid
        return self._call("/assistance/projects/"+pid)

    def refuser_un_avis_et_une_simulation(self, projet: str) -> dict:
        """Rejette le projet d’avis sans créer de fichier ni exécuter d’action."""
        pid=self._identifier(projet,"identifiant du projet d’avis")
        if isinstance(pid,dict):return pid
        return self._call("/legal-opinions/"+pid+"/reject","POST",{})

    def identifier_les_dernieres_ecritures(
        self, reference_dossier: str, type_de_document: str = "conclusions"
    ) -> dict:
        """Classe les écritures du dossier par date, version, état et format. Bloque si deux dernières versions restent indiscernables. Ne modifie aucun fichier."""
        matter=self._matter(reference_dossier)
        if isinstance(matter,dict):return matter
        return self._queued(self._call("/matters/"+matter+"/latest-writings","POST",{
          "document_type":type_de_document,
        }))

    def rechercher_la_jurisprudence_anonymisee(
        self, reference_dossier: str, question_juridique: str,
        fournisseurs: str = "openlegal,openlegi,goodlegal,pappers", limite: int = 8
    ) -> dict:
        """Anonymise localement la question, interroge les passerelles configurées et distingue les pistes des décisions réellement vérifiées. Aucun contenu du dossier ni nom de client n’est transmis."""
        matter=self._matter(reference_dossier)
        if isinstance(matter,dict):return matter
        providers=[x.strip().lower() for x in fournisseurs.split(",") if x.strip()]
        if any(x not in ("openlegal","openlegi","goodlegal","pappers") for x in providers):
            return {"error":"Fournisseurs autorisés : openlegal, openlegi, goodlegal, pappers."}
        return self._queued(self._call("/legal-research","POST",{
          "matter":matter,"question":question_juridique,"providers":providers,"limit":limite,
        }))

    def preparer_une_requete_mcp_juridique_anonymisee(
        self, reference_dossier: str, question_juridique: str,
        fournisseurs: str = "openlegal,openlegi,goodlegal,pappers"
    ) -> dict:
        """Anonymise localement une question avant tout appel MCP. Utiliser ensuite exclusivement anonymized_query avec le MCP ; ne jamais transmettre la question d’origine ni les documents du dossier."""
        matter=self._matter(reference_dossier)
        if isinstance(matter,dict):return matter
        providers=[x.strip().lower() for x in fournisseurs.split(",") if x.strip()]
        if any(x not in ("openlegal","openlegi","goodlegal","pappers") for x in providers):
            return {"error":"Fournisseurs autorisés : openlegal, openlegi, goodlegal, pappers."}
        return self._call("/legal-research/prepare","POST",{
          "matter":matter,"question":question_juridique,"providers":providers,
        })

    def enregistrer_et_verifier_les_resultats_mcp_juridiques(
        self, requete: str, fournisseur: str, resultats_json: str,
        requete_anonymisee: str = ""
    ) -> dict:
        """Importe les pistes renvoyées par un MCP après anonymisation locale. AxiorHub retélécharge lui-même la source officielle et refuse toute citation dont l’identifiant ou l’extrait exact ne concorde pas."""
        checked=self._query_identifier(requete)
        if isinstance(checked,dict):return checked
        provider=str(fournisseur or "").strip().lower()
        if provider not in ("openlegal","openlegi","goodlegal","pappers"):
            return {"error":"Fournisseur MCP non autorisé."}
        try:results=json.loads(resultats_json or "[]")
        except ValueError:return {"error":"Le JSON des résultats MCP est invalide."}
        return self._queued(self._call("/legal-research/"+checked+"/mcp-results","POST",{
          "provider":provider,"anonymized_query":requete_anonymisee,
          "results":results,"limit":20,
        }))

    def verifier_une_decision_sur_source_officielle(
        self, reference_dossier: str, url_officielle: str, identifiant_decision: str,
        extrait_exact_a_verifier: str, juridiction: str = "", date_decision: str = "",
        ecli: str = "", titre: str = ""
    ) -> dict:
        """Télécharge la page Légifrance/Judilibre ou une autre source officielle autorisée, vérifie l’identifiant et recherche mot pour mot l’extrait. Sans concordance, la décision reste non citable."""
        matter=self._matter(reference_dossier)
        if isinstance(matter,dict):return matter
        return self._queued(self._call("/legal-research/verify","POST",{
          "matter":matter,"official_url":url_officielle,"identifier":identifiant_decision,
          "exact_excerpt":extrait_exact_a_verifier,"court":juridiction,
          "date":date_decision,"ecli":ecli,"title":titre,"provider":"manual",
        }))

    def consulter_le_registre_des_jurisprudences(
        self, reference_dossier: str, etat: str = "all"
    ) -> dict:
        """Affiche les décisions du dossier, leur texte officiel empreinté, l’extrait exact et leur état vérifié ou rejeté."""
        matter=self._matter(reference_dossier)
        if isinstance(matter,dict):return matter
        return self._call("/matters/"+matter+"/authorities?"+urlencode({"status":etat,"limit":200}))

    def actualiser_le_registre_des_pieces(self, reference_dossier: str) -> dict:
        """Calcule les empreintes SHA-256 des pièces du dossier et signale uniquement les doublons octet pour octet. Ne déplace, renomme ni supprime rien."""
        matter=self._matter(reference_dossier)
        if isinstance(matter,dict):return matter
        return self._queued(self._call("/matters/"+matter+"/exhibits/refresh","POST",{}))

    def consulter_le_registre_des_pieces(self, reference_dossier: str) -> dict:
        """Affiche les chemins, empreintes et doublons exacts déjà enregistrés pour les pièces du dossier."""
        matter=self._matter(reference_dossier)
        if isinstance(matter,dict):return matter
        return self._call("/matters/"+matter+"/exhibits?limit=500")

    def consulter_la_provenance_des_paragraphes(self, projet: str) -> dict:
        """Affiche pour chaque paragraphe proposé son empreinte, ses sources figées et les extraits exacts conservés."""
        checked=self._identifier(projet,"Identifiant de projet")
        if isinstance(checked,dict):return checked
        return self._call("/document-projects/"+checked+"/provenance")

    def consulter_le_controle_deterministe(self, projet: str) -> dict:
        """Affiche les contrôles non probabilistes : sources connues, citations officielles, nombres et dates sourcés, pièces, chemins et absence de prétendue exécution."""
        checked=self._identifier(projet,"Identifiant de projet")
        if isinstance(checked,dict):return checked
        return self._call("/document-projects/"+checked+"/deterministic-control")

    def verifier_une_operation(self, numero: int) -> dict:
        """Récupère l’état et le résultat d’une opération longue déjà lancée."""
        return self._call("/jobs/"+str(numero))

    def rechercher_dans_le_cabinet(self, question: str, limite: int = 12) -> dict:
        """Recherche les documents et courriels dans tous les dossiers, avec chemins sources."""
        return self._call("/search", "POST", {"query": question, "limit": limite})

    def afficher_le_tableau_de_bord(self) -> dict:
        """Affiche uniquement l’état général du cabinet : compteurs, dossiers récents, courriels, confirmations et opérations. Ne pas utiliser pour les priorités, urgences, échéances ou alertes du jour ; utiliser alors afficher_les_priorites_du_jour."""
        return self._call("/dashboard")

    def afficher_les_projets_autonomes_en_attente(self) -> dict:
        """Affiche les prévisualisations documentaires, contrôles bloquants, diligences et facturations à valider. Aucun élément affiché n’est exécuté."""
        return self._call("/autonomy/pending")

    def declencher_la_surveillance_autonome(self) -> dict:
        """Analyse immédiatement les courriels déjà ingérés et leurs pièces jointes, puis prépare seulement des prévisualisations et propositions internes."""
        return self._queued(self._call("/autonomy/run","POST",{}))

    def consulter_la_memoire_operationnelle_du_dossier(self, reference_dossier: str) -> dict:
        """Retourne la mémoire structurée du dossier. Au premier appel, AxiorHub initialise automatiquement l’instantané. Une mémoire vide n’interdit jamais d’appeler l’outil documentaire spécialisé."""
        matter=self._matter(reference_dossier)
        if isinstance(matter,dict):return matter
        return self._call("/matters/"+matter+"/operational-memory")

    def identifier_les_dernieres_conclusions_des_parties(
        self, reference_dossier: str, nos_conclusions: str = "",
        conclusions_adverses: str = ""
    ) -> dict:
        """Identifie les dernières conclusions de chaque partie. En cas d’ambiguïté, bloque et exige les deux chemins exacts. Ne crée aucun fichier."""
        matter=self._matter(reference_dossier)
        if isinstance(matter,dict):return matter
        return self._queued(self._call("/hearing/writings","POST",{
          "matter":matter,"our_source_path":nos_conclusions,
          "opponent_source_path":conclusions_adverses,
        }))

    def comparer_les_dispositifs_des_parties(
        self, reference_dossier: str, nos_conclusions: str = "",
        conclusions_adverses: str = ""
    ) -> dict:
        """Extrait le dernier dispositif de chaque partie et compare les demandes. Utiliser des chemins confirmés si AxiorHub signale une ambiguïté."""
        matter=self._matter(reference_dossier)
        if isinstance(matter,dict):return matter
        return self._queued(self._call("/hearing/devices/compare","POST",{
          "matter":matter,"our_source_path":nos_conclusions,
          "opponent_source_path":conclusions_adverses,
        }))

    def preparer_une_audience(
        self, reference_dossier: str, instruction: str,
        nos_conclusions: str = "", conclusions_adverses: str = ""
    ) -> dict:
        """Prépare la matrice contradictoire, les plans de plaidoirie 5/10/20 minutes, les questions probables et les pièces à emporter. Aucun fichier n’est créé."""
        matter=self._matter(reference_dossier)
        if isinstance(matter,dict):return matter
        return self._queued(self._call("/hearing-projects","POST",{
          "matter":matter,"instruction":instruction,
          "our_source_path":nos_conclusions,"opponent_source_path":conclusions_adverses,
        }))

    def previsualiser_la_preparation_d_audience(self, projet_audience: str) -> dict:
        """Relit les écritures retenues, la comparaison des dispositifs, la préparation orale, les contrôles et les futurs fichiers."""
        checked=self._identifier(projet_audience,"Identifiant de préparation d’audience")
        if isinstance(checked,dict):return checked
        return self._call("/hearing-projects/"+checked)

    def confirmer_la_creation_du_dossier_de_plaidoirie(
        self, projet_audience: str, code_recopie_par_l_avocat: str,
        nos_conclusions_confirmees: str, conclusions_adverses_confirmees: str,
        dossier_destination_confirme: str
    ) -> dict:
        """Après confirmation explicite des deux écritures, du dossier et du code, crée uniquement de nouveaux fichiers d’audience. Aucun dépôt, envoi ou écrasement."""
        checked=self._identifier(projet_audience,"Identifiant de préparation d’audience")
        if isinstance(checked,dict):return checked
        return self._queued(self._call("/hearing-projects/"+checked+"/confirm","POST",{
          "confirmation_code":code_recopie_par_l_avocat,
          "our_source_path":nos_conclusions_confirmees,
          "opponent_source_path":conclusions_adverses_confirmees,
          "destination_folder":dossier_destination_confirme,
        }))

    def refuser_la_preparation_d_audience(self, projet_audience: str) -> dict:
        """Rejette la préparation d’audience sans créer ni modifier de fichier."""
        checked=self._identifier(projet_audience,"Identifiant de préparation d’audience")
        if isinstance(checked,dict):return checked
        return self._call("/hearing-projects/"+checked+"/reject","POST",{})

    def preparer_une_revision_word(
        self, reference_dossier: str, instruction: str,
        fichier_word_source: str = ""
    ) -> dict:
        """Prépare une révision structurée du DOCX : insertion au bon emplacement, styles, numérotation, renvois, bordereau par empreinte et suivi des modifications. La source reste intacte."""
        matter=self._matter(reference_dossier)
        if isinstance(matter,dict):return matter
        return self._queued(self._call("/word-projects","POST",{
          "matter":matter,"instruction":instruction,"source_path":fichier_word_source,
        }))

    def previsualiser_la_revision_word(self, projet_word: str) -> dict:
        """Relit les ancres, styles, modifications, sources, renvois internes et chemins des futures versions Word."""
        checked=self._identifier(projet_word,"Identifiant de révision Word")
        if isinstance(checked,dict):return checked
        return self._call("/word-projects/"+checked)

    def confirmer_la_creation_des_versions_word(
        self, projet_word: str, code_recopie_par_l_avocat: str,
        fichier_source_confirme: str, dossier_destination_confirme: str
    ) -> dict:
        """Après confirmation exacte, crée la version propre, la version comparée, le bordereau par empreinte et le rapport. Le fichier source n’est jamais écrasé."""
        checked=self._identifier(projet_word,"Identifiant de révision Word")
        if isinstance(checked,dict):return checked
        return self._queued(self._call("/word-projects/"+checked+"/confirm","POST",{
          "confirmation_code":code_recopie_par_l_avocat,
          "source_path":fichier_source_confirme,
          "destination_folder":dossier_destination_confirme,
        }))

    def refuser_la_revision_word(self, projet_word: str) -> dict:
        """Rejette une révision Word sans créer ni modifier de fichier."""
        checked=self._identifier(projet_word,"Identifiant de révision Word")
        if isinstance(checked,dict):return checked
        return self._call("/word-projects/"+checked+"/reject","POST",{})

    def lister_les_propositions_de_diligences_et_facturation(self, reference_dossier: str = "") -> dict:
        """Liste les diligences et pistes de facturation proposées. Aucun temps, montant, tâche ou facture n’est créé automatiquement."""
        if reference_dossier:
            matter=self._matter(reference_dossier)
            if isinstance(matter,dict):return matter
        else:matter=""
        query=urlencode({"status":"pending","matter":matter,"limit":100})
        return {"diligences":self._call("/autonomy/diligences?"+query).get("proposals",[]),
          "facturation":self._call("/autonomy/billing?"+query).get("proposals",[]),
          "warning":"Propositions internes uniquement : aucune facture et aucune tâche ne sont créées."}

    def afficher_le_portefeuille_actif(self, etats: str = "active,to_confirm") -> dict:
        """Liste les dossiers par état métier. Utiliser active pour le travail courant et all seulement pour une recherche d’archives."""
        return self._call("/portfolio?" + urlencode({"states": etats, "limit": 300}))

    def organiser_automatiquement_le_cabinet(self) -> dict:
        """Lance le parcours unique : dossiers Nextcloud, fils mail 18 mois, associations certaines, portefeuille et nettoyage. L’opération est découpée et annulable."""
        result=self._call("/portfolio/organize", "POST", {})
        if result.get("job_id"):
            result["message"]="Organisation lancée en arrière-plan. Le numéro permet d’en suivre ou d’en annuler l’avancement."
        return result

    def nettoyer_la_boite_a_traiter(self) -> dict:
        """Rapproche la file avec l’IMAP : réponses, lectures, déplacements, suppressions, brouillons et arriéré."""
        return self._call("/portfolio/reconcile", "POST", {})

    def lister_les_associations_a_confirmer(self) -> dict:
        """Liste uniquement les rapprochements ambigus regroupés par correspondant et dossier."""
        return self._call("/association-groups?status=pending&limit=100")

    def lire_l_agenda_sur_une_periode(
        self, debut_iso: str, fin_iso: str, reference_dossier: str = ""
    ) -> dict:
        """Lit tous les événements Nextcloud entre deux dates ISO, y compris ceux sans dossier identifié. Fonction obligatoire pour toute question d’agenda, audience, rendez-vous ou disponibilité."""
        return self._call("/calendar/events?" + urlencode({
            "start": debut_iso, "end": fin_iso, "matter": reference_dossier
        }))

    def lister_les_taches_nextcloud(
        self, etats: str = "todo,in_progress,blocked", reference_dossier: str = ""
    ) -> dict:
        """Liste les tâches VTODO synchronisées avec Nextcloud et leurs dossiers, échéances, blocages et durées."""
        return self._call("/tasks?" + urlencode({
            "status": etats, "matter": reference_dossier, "limit": 300
        }))

    def synchroniser_les_taches_nextcloud(self) -> dict:
        """Actualise la copie locale des tâches Nextcloud. Ne crée, ne modifie et ne supprime aucune tâche."""
        return self._queued(self._call("/tasks/sync", "POST", {}))

    def proposer_un_programme_de_travail(
        self, liste_des_taches: str, debut_iso: str, fin_iso: str,
        charge_maximale_par_jour_minutes: int = 360
    ) -> dict:
        """Extrait une liste de tâches et propose une répartition réaliste selon l’agenda. Ne crée encore rien. Afficher intégralement le résumé, les blocages et le code à l’avocat."""
        return self._queued(self._call("/planning/proposals", "POST", {
            "task_text": liste_des_taches,
            "period_start": debut_iso,
            "period_end": fin_iso,
            "max_daily_minutes": charge_maximale_par_jour_minutes,
        }))

    def confirmer_un_programme_nextcloud(
        self, proposition: str, code_recopie_par_l_avocat: str
    ) -> dict:
        """Après recopie humaine du code, crée en groupe les tâches et créneaux proposés. Ne jamais appeler sans validation explicite de l’avocat."""
        checked=self._identifier(proposition,"Identifiant de proposition")
        if isinstance(checked,dict):return checked
        approved=self._call(
            "/planning/proposals/" + checked + "/approve", "POST",
            {"confirmation_code": code_recopie_par_l_avocat},
        )
        return self._queued(approved)

    def refuser_un_programme_nextcloud(self, proposition: str) -> dict:
        """Refuse une proposition de planning sans créer de tâche ni d’événement."""
        checked=self._identifier(proposition,"Identifiant de proposition")
        if isinstance(checked,dict):return checked
        return self._call("/planning/proposals/" + checked + "/reject", "POST", {})

    def preparer_un_projet_de_conclusions(
        self, reference_dossier: str, instruction: str,
        fichier_source_souhaite: str = "", recherche_juridique_json: str = "[]"
    ) -> dict:
        """Outil obligatoire dès que l’avocat demande de préparer ou actualiser des conclusions. Inventorie directement le dossier Nextcloud, identifie la dernière version substantielle, analyse les sources disponibles et prépare conclusions et bordereau. Une mémoire opérationnelle vide n’est pas un motif pour éviter cet appel. Aucun fichier, envoi ni dépôt."""
        return self._prepare_document("conclusions",reference_dossier,instruction,fichier_source_souhaite,recherche_juridique_json)

    def preparer_un_projet_d_assignation(
        self, reference_dossier: str, instruction: str,
        fichier_source_souhaite: str = "", recherche_juridique_json: str = "[]"
    ) -> dict:
        """Prépare une nouvelle assignation interne sourcée sans créer de fichier ni accomplir d’acte de procédure."""
        return self._prepare_document("assignation",reference_dossier,instruction,fichier_source_souhaite,recherche_juridique_json)

    def preparer_un_projet_de_cgv(
        self, reference_dossier: str, instruction: str,
        fichier_source_souhaite: str = "", recherche_juridique_json: str = "[]"
    ) -> dict:
        """Prépare de nouvelles CGV internes sourcées, sans création de fichier avant confirmation."""
        return self._prepare_document("cgv",reference_dossier,instruction,fichier_source_souhaite,recherche_juridique_json)

    def preparer_un_projet_de_contrat(
        self, reference_dossier: str, instruction: str,
        fichier_source_souhaite: str = "", recherche_juridique_json: str = "[]"
    ) -> dict:
        """Prépare un nouveau projet de contrat interne sourcé, sans signature ni fichier Nextcloud."""
        return self._prepare_document("contrat",reference_dossier,instruction,fichier_source_souhaite,recherche_juridique_json)

    def preparer_un_projet_de_charte_RGPD(
        self, reference_dossier: str, instruction: str,
        fichier_source_souhaite: str = "", recherche_juridique_json: str = "[]"
    ) -> dict:
        """Prépare une charte RGPD interne sourcée sans créer de fichier avant confirmation."""
        return self._prepare_document("charte_rgpd",reference_dossier,instruction,fichier_source_souhaite,recherche_juridique_json)

    def preparer_un_projet_de_bcp(
        self, reference_dossier: str, instruction: str,
        fichier_source_souhaite: str = "", recherche_juridique_json: str = "[]"
    ) -> dict:
        """Prépare un bordereau de communication de pièces, avec doublons et numérotation à contrôler. Aucun fichier n’est encore créé."""
        return self._prepare_document("bcp",reference_dossier,instruction,fichier_source_souhaite,recherche_juridique_json)

    def preparer_un_projet_de_courrier(
        self, reference_dossier: str, instruction: str,
        fichier_source_souhaite: str = "", recherche_juridique_json: str = "[]"
    ) -> dict:
        """Prépare un courrier interne sans l’envoyer et sans créer de fichier avant confirmation."""
        return self._prepare_document("courrier",reference_dossier,instruction,fichier_source_souhaite,recherche_juridique_json)

    def preparer_un_projet_de_document(
        self, reference_dossier: str, instruction: str,
        fichier_source_souhaite: str = "", recherche_juridique_json: str = "[]"
    ) -> dict:
        """Prépare un document interne générique, sourcé et sans mutation Nextcloud."""
        return self._prepare_document("document",reference_dossier,instruction,fichier_source_souhaite,recherche_juridique_json)

    def previsualiser_les_fichiers_du_projet(self, projet: str) -> dict:
        """Relit la source, les courriels, pièces, arguments, jurisprudences, incertitudes et chemins d’un projet. N’écrit rien."""
        checked=self._identifier(projet,"Identifiant de projet documentaire")
        if isinstance(checked,dict):return checked
        return self._call("/document-projects/"+checked)

    def confirmer_la_creation_des_fichiers_nextcloud(
        self, projet: str, code_recopie_par_l_avocat: str,
        fichier_source_confirme: str, dossier_destination_confirme: str
    ) -> dict:
        """Seule fonction documentaire autorisée à écrire : après recopie du code et confirmation exacte source/destination, crée de nouveaux fichiers exclusivement. Aucun écrasement, envoi, signature ou dépôt."""
        checked=self._identifier(projet,"Identifiant de projet documentaire")
        if isinstance(checked,dict):return checked
        return self._queued(self._call("/document-projects/"+checked+"/confirm","POST",{
          "confirmation_code":code_recopie_par_l_avocat,
          "source_path":fichier_source_confirme,
          "destination_folder":dossier_destination_confirme,
        }))

    def refuser_un_projet_documentaire(self, projet: str) -> dict:
        """Rejette une prévisualisation documentaire sans créer, déplacer, supprimer ni envoyer quoi que ce soit."""
        checked=self._identifier(projet,"Identifiant de projet documentaire")
        if isinstance(checked,dict):return checked
        return self._call("/document-projects/"+checked+"/reject","POST",{})

    def modifier_le_statut_d_une_tache(self, identifiant: str, statut: str) -> dict:
        """Met à jour une tâche créée par AxiorHub : todo, in_progress, completed ou cancelled. Les tâches externes restent en lecture seule."""
        return self._queued(self._call(
            "/tasks/" + identifiant + "/status", "POST", {"status": statut}
        ))

    def afficher_les_priorites_du_jour(self) -> dict:
        """Fonction obligatoire pour les priorités, urgences, échéances, alertes ou actions du jour. Retourne une synthèse compacte avec dossiers, motifs, dates et sources."""
        data = self._call("/daily-dashboard")
        if not isinstance(data, dict) or data.get("error"):
            return data

        signal_fields = (
            "id", "matter", "matter_name", "severity", "category", "title",
            "detail", "proposed_action", "due", "source_ids",
        )
        change_fields = (
            "matter", "matter_name", "last_checked", "last_change", "last_result",
        )

        def compact(items, fields, limit):
            return [
                {
                    key: item[key]
                    for key in fields
                    if key in item and item[key] not in (None, "", [], {})
                }
                for item in items[:limit]
                if isinstance(item, dict)
            ]

        return {
            "day": data.get("day"),
            "generated_at": data.get("generated_at"),
            "signal_counts": data.get("signal_counts", {}),
            "mail_counts": data.get("mail_counts", {}),
            "portfolio": data.get("portfolio", {}),
            "today": data.get("today", {}),
            "recommended_actions": data.get("recommended_actions", [])[:6],
            "priorities": compact(data.get("priorities", []), signal_fields, 8),
            "deadlines": compact(data.get("deadlines", []), signal_fields, 8),
            "recent_changes": compact(data.get("recent_changes", []), change_fields, 5),
            "agenda_next_7_days": data.get("agenda_next_7_days", [])[:12],
            "open_nextcloud_tasks": data.get("open_nextcloud_tasks", [])[:12],
            "limits": data.get("limits", []),
            "display_limits": {"priorities": 8, "deadlines": 8, "recent_changes": 5},
        }

    def lister_les_signaux_proactifs(self, etat: str = "open", reference: str = "") -> dict:
        """Liste les signaux vérifiables, éventuellement limités à un dossier."""
        return self._call("/signals?" + urlencode({"state": etat, "matter": reference}))

    def surveiller_un_dossier(self, reference: str) -> dict:
        """Met la surveillance immédiate d’un dossier en file, sans action externe."""
        return self._call("/matters/" + reference + "/monitor", "POST", {})

    def marquer_un_signal_comme_vu(self, identifiant: str) -> dict:
        """Marque un signal proactif comme vu sans le déclarer juridiquement résolu."""
        return self._call("/signals/" + identifiant + "/acknowledge", "POST", {})

    def ouvrir_un_dossier(self, reference: str) -> dict:
        """Retourne la synthèse, les documents récents et les tâches d’un dossier exact."""
        return self._call("/matters/" + reference)

    def lire_la_memoire_juridique(self, reference: str) -> dict:
        """Retourne les faits proposés/validés, leurs sources et contradictions d’un dossier."""
        return self._call("/matters/" + reference + "/memory")

    def lire_la_chronologie(self, reference: str) -> dict:
        """Retourne la chronologie unifiée des mails, pièces, tâches et événements du dossier."""
        return self._call("/matters/" + reference + "/timeline")

    def lire_l_analyse_strategique(self, reference: str) -> dict:
        """Retourne la dernière analyse stratégique, ses options, limites et sources."""
        return self._call("/matters/" + reference + "/strategy")

    def lire_la_matrice_de_preuve(self, reference: str) -> dict:
        """Retourne la matrice faits, pièces, prétentions et preuves manquantes."""
        return self._call("/matters/" + reference + "/matrix")

    def lister_les_projets_d_actes(self, reference: str) -> dict:
        """Liste les projets d’actes internes préparés pour un dossier."""
        return self._call("/matters/" + reference + "/act-projects")

    def demander_une_analyse_strategique(self, reference: str, objectif: str) -> dict:
        """Attend l’analyse stratégique puis restitue le résultat sourcé, sans action externe."""
        queued=self._call("/matters/" + reference + "/strategy", "POST", {"objective": objectif})
        job=self._queued(queued)
        if job.get("status")=="done":return self._call("/matters/"+reference+"/strategy")
        return job

    def construire_la_matrice_de_preuve(self, reference: str, objectif: str) -> dict:
        """Met la construction d’une matrice de preuve en file prioritaire."""
        queued=self._call("/matters/" + reference + "/matrix", "POST", {"objective": objectif})
        job=self._queued(queued)
        if job.get("status")=="done":return self._call("/matters/"+reference+"/matrix")
        return job

    def preparer_un_projet_d_acte(self, reference: str, type_acte: str, instruction: str) -> dict:
        """Prépare un projet interne sourcé ; ne dépose, ne signe et n’envoie rien."""
        queued=self._call("/matters/" + reference + "/act-projects", "POST", {
            "act_type": type_acte, "instruction": instruction
        })
        job=self._queued(queued)
        if job.get("status")=="done":return self._call("/matters/"+reference+"/act-projects")
        return job

    def lister_les_courriels_a_traiter(self) -> dict:
        """Liste les réponses nécessaires, confirmations et brouillons prêts."""
        return self._call("/work-items?states=needs_action,needs_confirmation,draft_ready,backlog")

    def demander_une_analyse_axiorhub(
        self, question: str, reference_dossier: str = "", cle_courriel: str = ""
    ) -> dict:
        """Pose une question, attend la réponse et la restitue avec ses sources dans Open WebUI."""
        submitted=self._call("/assistant", "POST", {
            "question": question, "matter": reference_dossier, "mail_key": cle_courriel
        })
        if submitted.get("error") or not submitted.get("job_id"):return submitted
        job=self._wait(submitted["job_id"])
        if job.get("status")!="done":return job
        thread=self._call("/threads/"+submitted["thread_id"])
        messages=thread.get("messages",[])
        answer=next((m for m in reversed(messages) if m.get("role")=="assistant"),None)
        if not answer:return {"error":"Réponse terminée mais introuvable","thread_id":submitted["thread_id"]}
        raw=answer.get("sources",[])
        if isinstance(raw,str):
            try:raw=json.loads(raw)
            except ValueError:raw=[]
        return {"answer":answer.get("content",""),"sources":raw,
          "thread_id":submitted["thread_id"],"job_id":submitted["job_id"],
          "proposed_actions":(job.get("result") or {}).get("proposed_actions",[]),
          "supervision":"Aucune action proposée n’a été exécutée."}

    def continuer_une_conversation(self, conversation: str, question: str,
                                   reference_dossier: str = "", cle_courriel: str = "") -> dict:
        """Continue une conversation AxiorHub existante et attend la nouvelle réponse."""
        submitted=self._call("/assistant","POST",{"question":question,"matter":reference_dossier,
          "mail_key":cle_courriel,"thread_id":conversation})
        if submitted.get("error") or not submitted.get("job_id"):return submitted
        job=self._wait(submitted["job_id"])
        if job.get("status")!="done":return job
        return self._call("/threads/"+conversation)

    def preparer_un_brouillon_de_reponse(self, cle_courriel: str, instruction: str) -> dict:
        """Crée une demande supervisée et un code. Ne prépare rien avant confirmation humaine."""
        return self._call("/supervision/drafts", "POST", {
            "mail_key": cle_courriel, "instruction": instruction
        })

    def confirmer_un_brouillon(self, demande: str, code_recopie_par_l_avocat: str) -> dict:
        """Confirme avec le code explicitement recopié par l’avocat, puis attend le brouillon. Ne jamais appeler sans cette saisie humaine."""
        approved=self._call("/supervision/"+demande+"/approve","POST",{
          "confirmation_code":code_recopie_par_l_avocat})
        if approved.get("error") or not approved.get("job_id"):return approved
        return self._wait(approved["job_id"])

    def refuser_un_brouillon(self, demande: str) -> dict:
        """Refuse une demande supervisée sans créer de brouillon."""
        return self._call("/supervision/"+demande+"/reject","POST",{})

    def annuler_une_operation(self, numero: int) -> dict:
        """Annule une opération en attente ; un lot commencé finit sans être relancé."""
        return self._call("/jobs/" + str(numero) + "/cancel", "POST", {})

    def afficher_l_etat_du_systeme(self) -> dict:
        """Affiche services, permissions, incidents, fraîcheur de l’index et dernières preuves de destination."""
        return self._call("/system/status")

    def controler_les_destinations(self) -> dict:
        """Relit les services locaux, les brouillons IMAP et les fichiers Nextcloud ; ne contacte pas OpenRouter."""
        return self._call("/system/checks/run", "POST", {})

    def tester_openrouter_sans_donnee_de_dossier(self) -> dict:
        """Teste seulement l’authentification et GET /models ; aucun prompt, dossier ou document n’est transmis."""
        return self._call("/system/openrouter-test", "POST", {})

    def relancer_une_operation_echouee(self, numero: int) -> dict:
        """Relance une copie bornée d’une opération échouée, au maximum trois fois."""
        return self._call("/jobs/" + str(numero) + "/retry", "POST", {})

    def afficher_le_routage_hybride(self) -> dict:
        """Affiche la politique locale/OpenRouter, les budgets et les décisions sans contenu confidentiel."""
        return self._call("/ai/routing")

    def estimer_un_routage_sans_transmettre(self, fonction: str, caracteres: int,
                                            sources: int = 0, documents: int = 0,
                                            reponse_maximale: int = 3500) -> dict:
        """Calcule le modèle et le coût avant traitement, sans envoyer de donnée à OpenRouter."""
        return self._call("/ai/routing/simulate", "POST", {
            "purpose": fonction, "input_characters": caracteres,
            "source_count": sources, "document_count": documents,
            "max_tokens": reponse_maximale})

    def voir_les_donnees_avant_openrouter(self, fonction: str, texte: str,
                                          reference_dossier: str = "",
                                          etape: str = "chat") -> dict:
        """Retourne l’aperçu anonymisé et le coût ; cette action ne contacte jamais OpenRouter."""
        return self._call("/ai/routing/preview", "POST", {
            "purpose": fonction, "text": texte, "matter": reference_dossier,
            "stage": etape})

    def afficher_l_apprentissage_metier(self) -> dict:
        """Affiche les règles explicites, corrections et documents fiables sans exposer de secret."""
        return self._call("/learning/business-rules")

    def enregistrer_une_regle_metier(self, portee: str, fonction: str,
                                     nature: str, instruction: str,
                                     valeur_de_portee: str = "") -> dict:
        """Enregistre une règle explicite et réversible approuvée par l’avocat ; n’entraîne aucun modèle."""
        return self._call("/learning/business-rules", "POST", {
            "scope": portee, "scope_value": valeur_de_portee,
            "purpose": fonction, "rule_type": nature,
            "instruction": instruction})

    def afficher_le_banc_juridique(self) -> dict:
        """Affiche les scores du cabinet, les hallucinations, les délais, coûts et volumes de correction."""
        return self._call("/evaluations/legal")
