# Contrat moteur ↔ application Nextcloud — `/integration/v1` (version de contrat 1.2)

Introduit en 5.6.34 (cahier des charges, chapitre 9.6), révisé en 5.6.35 (audit F08–F13) et en 5.6.36 (comptes reliés personnellement,
instruction au Pilote, recherche). Désactivé par défaut. Implémentation : `agent/integration5634.py`, `agent/integration5636.py` ;
client : `nextcloud-app/axiorhub_pilote/lib/Service/EngineClient.php`.

## Activation (moteur)

### Comptes reliés personnellement (5.6.36, recommandé)

Dans AxiorHub, Paramètres › Connexions › Application Nextcloud : « Autoriser la liaison des comptes depuis Nextcloud ». Chaque avocat
relie ensuite son compte dans Nextcloud (Paramètres personnels › AxiorHub Pilote) :

- `POST /integration/v1/pair` (seule route non signée qui écrit) : `{"login", "password", "label"}` ; identifiants vérifiés une fois
  (compte du serveur `ui-auth.json`, ou comptes du cabinet en distribution autonome), 5 échecs au plus en 15 minutes par adresse et par
  identifiant ; réponse `{"key_id", "secret", "user", "role", "contract"}`. Le mot de passe n'est conservé nulle part ;
- ensuite chaque requête porte `X-AxiorHub-Key: <key_id>` et un contexte signé avec le `secret` de la clé ; le périmètre est celui du
  compte AxiorHub (tout le cabinet) ; `POST /integration/v1/unpair` ou « Révoquer » (AxiorHub) invalident la clé.

### Secret d'instance (mode 5.6.34, toujours accepté)


```json
"integration5634": {"enabled": true, "instance_id": "cabinet-exemple", "secret_file": "/etc/axiorhub-mail-agent/integration.secret"}
```

Le secret (8 caractères au moins, même règle que l'application Nextcloud ; 32 caractères aléatoires recommandés) est lisible par le seul
compte de service (droits 600). Le même identifiant d'instance et le même
secret sont saisis dans Nextcloud (Paramètres d'administration › Paramètres supplémentaires › AxiorHub). Le moteur doit être joignable
depuis le serveur Nextcloud (HTTPS, ou adresse locale explicitement autorisée).

## Authentification de chaque requête

En-tête `X-AxiorHub-Context: <charge>.<mac>` :

- `charge` = base64url (sans remplissage) d'un objet JSON :
  `{"instance": "...", "user": "<utilisateur Nextcloud>", "scope": ["<dossier>", ...], "op": "<MÉTHODE /chemin>",
  "exp": "<ISO 8601 avec fuseau, au plus 5 minutes>", "nonce": "<16 à 64 caractères, usage unique>"}` ;
- `op` doit être exactement la méthode et le chemin appelés (par exemple `POST /integration/v1/jobs`) : un contexte signé pour lire un
  travail ne permet ni d'en créer, ni d'en annuler, ni d'approuver un résultat ;
- tout champ mal typé, une expiration sans fuseau ou un JSON qui n'est pas un objet donnent une erreur 4xx structurée ;
- `mac` = HMAC-SHA256 (hexadécimal) de `charge` avec le secret d'instance.

L'utilisateur et le périmètre sont déduits côté serveur Nextcloud (jamais transmis par le navigateur). Le moteur refuse une signature
invalide, un contexte expiré, une autre instance, un nonce déjà vu (rejeu) et tout dossier hors du périmètre ; les droits sont
revérifiés à chaque lecture de travail ou de résultat.

## Opérations

| Méthode et chemin | Entrée | Réponse |
|---|---|---|
| `GET /integration/v1/health` | — | `version`, `contract`, `operations`, `status` (sans secret ; aucune signature requise) |
| `GET /integration/v1/matters` | périmètre signé `["*"]` (administrateur Nextcloud) ou clé personnelle | `matters` : `id`, `label`, `archived` (aucune pièce) |
| `GET /integration/v1/me` | — | `user`, `role`, `mode` (`personnel` ou `instance`), `version`, `contract`, `operations` |
| `GET /integration/v1/search?q=…&matter=…` | requête (2 à 300 caractères), dossier facultatif | `results` : `kind`, `title`, `matter`, `date`, `excerpt` ; `partial` (délai atteint) |
| `POST /integration/v1/unpair` | — | `unpaired` (clé personnelle révoquée) |
| `POST /integration/v1/source-manifests` | `matter`, `sources` (`file_id`, `etag`, `size`, `sha256`, `name`) | `manifest_id`, `accepted`, `errors`, `coverage` ; chaque source est rapprochée d'une pièce de l'index (ETag, puis nom exact unique) ; une source introuvable est signalée, jamais remplacée |
| `POST /integration/v1/jobs` | `operation` (`fiche_dossier`, `recherche_juridique`, `instruction`), `matter`, `question`, `intent` (instruction : `auto`, `question`, `analysis`, `document`, `mail`), `manifest_id`, `idempotency_key` | HTTP 202, `job_id`, `state` (`pending` ou `existing`), `status_url` ; 409 si la clé a déjà servi pour un autre contenu ; avec `manifest_id`, seules les pièces choisies sont analysées |
| `GET /integration/v1/jobs/{id}` | — | `state` (`pending`, `running`, `succeeded`, `failed`, `cancelled`, `cancel_requested`), `stage`, `errors` |
| `POST /integration/v1/jobs/{id}/cancel` | — | `state` (`cancelled`, `cancel_requested`, `already_finished`), `message` ; annulation durable |
| `GET /integration/v1/results/{id}` | — | `version` (empreinte), `content` (figé à la première lecture : version de fiche produite par CE travail), `validations_attendues`, `decisions` (historique complet, plus récente d’abord : `version`, `user`, `decision`, `at`, `comment`) |
| `POST /integration/v1/results/{id}/approvals` | `version`, `decision` (`approuve`, `refuse`, `correction_demandee`), `comment` | décision tracée sur cette version ; HTTP 409 si la version ne correspond pas |

Une `instruction` crée une mission du Pilote (même chemin que le bouton robot : réponse, analyse, projet Word déposé dans le dossier ou brouillon déposé dans Brouillons — jamais d'envoi) ; son résultat porte `reponse`, `sources`, `livrable`.

Base momentanément occupée : réponse JSON `503` `moteur_occupe` (`retryable`), jamais la page HTML d'attente ; le client réessaie une lecture, jamais une écriture.

La clé d'idempotence est unique par instance et par utilisateur et liée à l'empreinte du contenu : deux requêtes identiques rejoignent
le même travail ; une demande refusée ne réserve rien ; une réservation interrompue avant sa mise en file est reprise ou close. Un travail accepté ne signifie pas un document produit ; une approbation ne vaut que pour la version approuvée.

## Erreurs

```json
{"code": "dossier_hors_perimetre", "service": "axiorhub", "stage": "controle", "retryable": false, "correlation_id": "3f2a91c0de12",
 "message": "Ce dossier n’entre pas dans votre périmètre."}
```

Aucun secret, jeton, URL signée ni contenu de pièce dans les réponses d'erreur ou les journaux.
