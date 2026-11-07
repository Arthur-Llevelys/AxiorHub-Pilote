# Évaluation des brouillons de courriels (5.6.95)

Mesure, avant publication, de la qualité des projets de courriels préparés par AxiorHub, selon la règle décidée par le cabinet :

1. le courriel est rédigé **en entier** ;
2. là où la position de l'avocat manque (accepter ou refuser, montant, stratégie, concession), **2 ou 3 variantes rédigées** ;
3. « [À COMPLÉTER : …] » réservé à un **fait absent du dossier**, **au plus 2 par courriel**.

L'évaluation tourne **sur le poste de développement uniquement**, à la demande ou avant publication. Jamais sur le serveur du cabinet, jamais
dans la suite `unittest` ordinaire. Celle-ci n'exécute que la partie déterministe, sur un faux modèle (`tests/test_v5695_evaluation.py`,
aussi lancé par la recette GitHub). Aucune donnée réelle : les cas sont fictifs (`tests/data/redaction5695/cas.json`).

## Ce qui est mesuré

Chaque cas passe par le **vrai chemin de rédaction** de l'arbre mesuré :
- pour une réponse, `intelligence.prepare_reply` avec dépôt dans des Brouillons simulés ;
- pour un courriel nouveau, `maildraft5613.perform`.

Le rédacteur est le modèle local. Seuls les accès extérieurs sont simulés :
- la messagerie IMAP ;
- l'index des pièces, qui reçoit les pièces du cas ;
- la recherche juridique en ligne ;
- le contrôle des sociétés.

Un garde réseau refuse toute connexion hors de `127.0.0.1`.

Contrôles déterministes, sur le corps tel qu'il est déposé :

| Mesure | Définition |
|---|---|
| Schéma respecté | la sortie du modèle est acceptée par le schéma de l'arbre (aucune erreur `json_ia_invalide`) |
| Marqueurs par courriel | « [À COMPLÉTER … ] » dans le corps déposé, en-tête compris ; au plus 2 |
| Marqueurs de position | marqueur qui remplace une position ou un choix (interdit par la règle) |
| Variantes attendues présentes | pour chaque point où le cas exige une position, un bloc de 2 ou 3 variantes non vides et différentes |
| Faits absents signalés | les faits réellement absents du cas sont signalés (marqueur ou liste `a_completer`) |
| Pièces justes | aucune source citée inventée, et toutes les pièces attendues citées |
| Éléments interdits | engagement ou position prise hors variantes, montant absent des sources (sommes et différences tolérées), article ou décision de justice absents des sources |
| Décisions de l'avocat | aucune décision validée contredite (par exemple un plancher interne communiqué à l'adversaire) |

DeepEval, avec le juge local (`qwen2.5:7b` par Ollama) :
- fidélité aux sources (`FaithfulnessMetric`) ;
- pertinence de la réponse (`AnswerRelevancyMetric`) ;
- un critère G-Eval « livrable terminé », rédigé en français dans le script.

## Préparer le poste (une fois)

1. **Ollama local.** Lancer le serveur sur la boucle locale, même si la variable `OLLAMA_HOST` du poste désigne une autre machine :

   ```
   set OLLAMA_HOST=127.0.0.1:11434
   set OLLAMA_NO_CLOUD=1
   ollama serve
   ollama pull qwen2.5:7b
   ```

   `OLLAMA_NO_CLOUD=1` désactive les modèles en nuage d'Ollama. Sur un processeur AMD à carte graphique intégrée, `OLLAMA_VULKAN=1` accélère
   nettement la lecture des consignes.
2. **DeepEval dans un environnement virtuel dédié**, hors de l'arbre :

   ```
   python -m venv C:\chemin\venv-eval5695
   C:\chemin\venv-eval5695\Scripts\python -m pip install deepeval==4.3.3 ollama
   ```

   Aucun compte Confident AI, aucune clé. DeepEval n'entre ni dans `agent/`, ni dans un paquet, ni dans `scripts/windows-requirements.txt`.

## Lancer l'évaluation

Depuis l'arbre de la version à publier, pour chaque arbre à comparer :

```
python scripts/evaluation-redaction.py rediger --arbre C:\chemin\src5694 --etiquette 5.6.94 --sortie C:\chemin\resultats
python scripts/evaluation-redaction.py rediger --arbre C:\chemin\src5695 --etiquette 5.6.95 --sortie C:\chemin\resultats
python scripts/evaluation-redaction.py juger --etiquette 5.6.94 --sortie C:\chemin\resultats --python-juge C:\chemin\venv-eval5695\Scripts\python.exe
python scripts/evaluation-redaction.py juger --etiquette 5.6.95 --sortie C:\chemin\resultats --python-juge C:\chemin\venv-eval5695\Scripts\python.exe
python scripts/evaluation-redaction.py rapport --sortie C:\chemin\resultats --etiquettes 5.6.94,5.6.95
```

- `--cas R01,R07` limite la mesure à quelques cas.
- Une mesure interrompue reprend au cas suivant : les résultats sont ajoutés cas par cas aux fichiers `.jsonl`.
- L'interpréteur du juge peut aussi être donné par la variable `AXIORHUB_EVAL_PYTHON`.
- Si DeepEval, l'interpréteur ou le modèle juge manquent, la partie DeepEval est ignorée avec un message ; la partie déterministe reste
  valable.

Fichiers produits dans le dossier de sortie, jamais dans `agent/` :
- `redaction-<étiquette>.jsonl` : corps et objet déposés, résultat du modèle, durée ;
- `juge-<étiquette>.jsonl` : scores et durées de DeepEval ;
- `rapport.json` ;
- `rapport.md` : tableau avant / après, puis détail par cas.

## Télémétrie et confidentialité

Le juge tourne dans un sous-processus dont l'environnement est filtré **avant tout import de DeepEval**. `importer_deepeval` refuse
l'import si un réglage manque ; un test le vérifie, avec une mutation qui retire la garde. Réglages posés (vérifiés dans le code de
DeepEval 4.3.3) :

| Réglage | Effet |
|---|---|
| `DEEPEVAL_TELEMETRY_OPT_OUT=YES` et `DEEPEVAL_TELEMETRY_ENABLED=0` | PostHog coupé ; le module de télémétrie passe en mode sans effet |
| `DEEPEVAL_UPDATE_WARNING_OPT_IN=0` | aucune requête vers pypi.org |
| `DEEPEVAL_DISABLE_DOTENV=1` et `DEEPEVAL_DISABLE_LEGACY_KEYFILE=1` | aucun fichier de clés relu |
| `DEEPEVAL_EVAL_MODE=llm` | jamais le modèle distant « System One » (Jev) |
| `CONFIDENT_TRACE_*` | traces coupées |
| `OLLAMA_HOST=127.0.0.1:11434` | le client `ollama` ne vise jamais une autre machine |

Les clés de services externes (Confident AI, OpenAI, Anthropic, Mistral, Google) et les relais (`HTTP_PROXY`…) sont retirés de
l'environnement. Le garde réseau s'applique aussi au juge.

## Limites du juge local

- Un modèle de 7 milliards de paramètres juge mal des nuances juridiques. Ses scores servent à **comparer deux versions sur les mêmes
  cas**, jamais à certifier un courriel.
- Les consignes de DeepEval sont en anglais, les courriels en français : la fidélité peut être sous-estimée sur une reformulation.
- Le même modèle rédige et juge : il peut être indulgent pour son propre style.
- Le juge peut rendre un JSON invalide. Le cas est alors noté sans score, jamais converti en note.
- La relecture d'un avocat reste nécessaire avant toute conclusion sur la qualité juridique.

## Utiliser la mesure dans le routage

Le routage mesuré (Paramètres › Modèles d’IA › Routage mesuré) peut tenir compte de cette évaluation pour la rédaction des courriels par le modèle local évalué : copier `rapport.json` dans `<état>/evaluation-redaction/rapport.json` sur le serveur. Un rapport mal formé est ignoré.

## Licences

| Composant | Licence | Usage |
|---|---|---|
| DeepEval 4.3.3 (Confident AI) | Apache-2.0 | outil de développement, non distribué |
| Qwen2.5-7B-Instruct (`qwen2.5:7b`, Alibaba Cloud, quantification Q4_K_M) | Apache-2.0 | modèle local de rédaction et de jugement, non distribué |
| Ollama | MIT | moteur local, déjà utilisé par AxiorHub |
