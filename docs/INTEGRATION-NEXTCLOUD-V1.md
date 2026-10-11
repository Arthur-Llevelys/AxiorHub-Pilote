# Contrat moteur ↔ application Nextcloud — `/integration/v1` (version de contrat 1.0)

Introduit en 5.6.34 (cahier des charges, chapitre 9.6). Désactivé par défaut. Implémentation : `agent/integration5634.py` ;
client : `nextcloud-app/axiorhub_pilote/lib/Service/EngineClient.php`.

## Activation (moteur)

```json
"integration5634": {"enabled": true, "instance_id": "cabinet-exemple", "secret_file": "/etc/axiorhub-mail-agent/integration.secret"}
```

Le secret (32 caractères au moins, aléatoire) est lisible par le seul compte de service. Le même identifiant d'instance et le même
secret sont saisis dans Nextcloud (Paramètres d'administration › Paramètres supplémentaires › AxiorHub). Le moteur doit être joignable
depuis le serveur Nextcloud (HTTPS, ou adresse locale explicitement autorisée).

## Authentification de chaque requête

En-tête `X-AxiorHub-Context: <charge>.<mac>` :

- `charge` = base64url (sans remplissage) d'un objet JSON :
  `{"instance": "...", "user": "<utilisateur Nextcloud>", "scope": ["<dossier>", ...], "op": "<méthode et chemin>",
  "exp": "<ISO 8601 UTC, au plus 5 minutes>", "nonce": "<16 à 64 caractères, usage unique>"}` ;
- `mac` = HMAC-SHA256 (hexadécimal) de `charge` avec le secret d'instance.

L'utilisateur et le périmètre sont déduits côté serveur Nextcloud (jamais transmis par le navigateur). Le moteur refuse une signature
invalide, un contexte expiré, une autre instance, un nonce déjà vu (rejeu) et tout dossier hors du périmètre ; les droits sont
revérifiés à chaque lecture de travail ou de résultat.

## Opérations

| Méthode et chemin | Entrée | Réponse |
|---|---|---|
| `GET /integration/v1/health` | — | `version`, `contract`, `operations`, `status` (sans secret ; aucune signature requise) |
| `POST /integration/v1/source-manifests` | `matter`, `sources` (`file_id`, `etag`, `size`, `sha256`, `name`) | `manifest_id`, `accepted`, `errors`, `coverage` |
| `POST /integration/v1/jobs` | `operation` (`fiche_dossier`, `recherche_juridique`), `matter`, `question`, `manifest_id`, `idempotency_key` | HTTP 202, `job_id`, `state` (`pending` ou `existing`), `status_url` |
| `GET /integration/v1/jobs/{id}` | — | `state` (`pending`, `running`, `succeeded`, `failed`, `cancelled`, `cancel_requested`), `stage`, `errors` |
| `POST /integration/v1/jobs/{id}/cancel` | — | `state`, `message` (un résultat déjà produit est conservé) |
| `GET /integration/v1/results/{id}` | — | `version` (empreinte), `content`, `validations_attendues` |
| `POST /integration/v1/results/{id}/approvals` | `version`, `decision` (`approuve`, `refuse`) | décision tracée ; HTTP 409 si le résultat a changé |

La clé d'idempotence est unique par instance et par utilisateur (contrainte en base) : deux requêtes concurrentes rejoignent le même
travail. Un travail accepté ne signifie pas un document produit ; une approbation ne vaut que pour la version approuvée.

## Erreurs

```json
{"code": "dossier_hors_perimetre", "service": "axiorhub", "stage": "controle", "retryable": false, "correlation_id": "3f2a91c0de12",
 "message": "Ce dossier n’entre pas dans votre périmètre."}
```

Aucun secret, jeton, URL signée ni contenu de pièce dans les réponses d'erreur ou les journaux.
