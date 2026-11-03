# Outil Open WebUI : suivi des opérations et remplacement de l’outil importé (5.6.93)

## Ce qui change

L’outil `integrations/openwebui/axiorhub_tool.py` suit une opération longue (rédaction, analyse, brouillon) par son numéro, sans jamais en créer une nouvelle pour obtenir le résultat de la précédente.

| État renvoyé par AxiorHub | Ce que l’outil répond |
| --- | --- |
| En file, en cours | Il attend, au plus `JOB_WAIT_SECONDS` (240 s par défaut), puis transmet le **dernier état constaté** et le numéro à suivre. |
| Suspendu (pause manuelle, dossier en pause, pause programmée) | Immédiatement : état, motif et reprise annoncée si elle est connue. |
| Attente de budget, de fournisseur ou de décision | Immédiatement, avec le motif. |
| Arrêt demandé | Il attend la confirmation (« Annulé »). |
| Annulé, échec, terminé | Immédiatement, avec le résultat ou le motif. |
| Serveur injoignable | « État inconnu », avec le dernier état connu ; jamais « le traitement continue ». |

Chaque réponse porte `outil_version`. La fonction `version_de_l_outil` compare cette version à celle du serveur.

## Remplacer l’outil déjà importé

Mettre à jour AxiorHub ne remplace pas la copie de l’outil enregistrée dans Open WebUI. Après chaque mise à jour :

1. Sur le serveur : `sudo python3 install-openwebui-tool.py` (prépare le fichier de l’outil à jour).
2. Dans Open WebUI : *Espace de travail › Outils*, ouvrir l’outil **AxiorHub Avocat** existant, remplacer tout son code par le fichier préparé, enregistrer. Ne pas créer un second outil.
3. Vérifier les *Valves* : le jeton `AXIORHUB_API_TOKEN` doit toujours être renseigné.
4. Dans une conversation, appeler `version_de_l_outil` : `outil_version` et `serveur_version` doivent être identiques. Sinon, la copie importée est encore l’ancienne.
5. Contrôle complet, sur le serveur : `sudo /usr/bin/python3 diagnostic-openwebui.py`.

Les opérations lancées avec un ancien outil restent consultables par leur numéro (`verifier_une_operation`).
