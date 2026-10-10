# Essai llama.cpp avec Open WebUI — séparé d'AxiorHub Pilote 3.0

## Verdict

Il est possible d'essayer llama.cpp en Docker avec Vulkan pour vos cartes AMD.
Il n'est pas établi qu'il soit plus rapide que votre Ollama actuel. Comparez le
même modèle réel, la même quantification, le même contexte et les mêmes prompts.
Le nom du profil affiché dans Open WebUI ne prouve pas le modèle chargé.

Distinguez deux circuits :
- Open WebUI peut utiliser llama.cpp pour la conversation et les appels d'outils.
- AxiorHub Pilote 3.0 utilise encore le protocole natif Ollama pour ses générations
  internes (/api/show, /api/chat) et ses embeddings. Modifier seulement son URL
  vers llama.cpp provoquerait des erreurs. Ce paquet ne fait pas cette bascule.

## Préparer un test sans toucher à Ollama

Relevez les modèles réellement présents :
~~~bash
ollama list
ollama ps
~~~

Choisissez le fichier GGUF correspondant exactement à votre modèle, depuis une
source de confiance, et vérifiez que la version llama.cpp prend son architecture
en charge. « Qwen 3.8:27b » peut être un nom local : aucun lien de poids n'est
inventé ici. Un modèle multimodal peut aussi exiger un fichier mmproj adapté ;
le test ci-dessous porte uniquement sur du texte.

Placez les fichiers dans /opt/axiorhub-llama-test/models et copiez l'exemple
integrations/llama_cpp/compose.example.yml dans un nouveau répertoire de test.
Remplacez la variable LLAMA_MODEL par le nom exact du GGUF.

Téléchargez l'image officielle puis relevez son RepoDigest :
~~~bash
sudo docker pull ghcr.io/ggml-org/llama.cpp:server-vulkan
sudo docker image inspect ghcr.io/ggml-org/llama.cpp:server-vulkan --format '{{json .RepoDigests}}'
~~~

Utilisez le RepoDigest complet comme LLAMA_IMAGE, pas l'ImageID de docker inspect.
Le fichier .env local du test peut contenir :
~~~text
LLAMA_IMAGE=ghcr.io/ggml-org/llama.cpp@sha256:REMPLACER_PAR_LE_REPODIGEST
LLAMA_MODEL=REMPLACER_PAR_LE_MODELE.gguf
~~~

Le fichier compose attend aussi ./llama-token, une clé locale que vous choisissez
aléatoirement et gardez privée, lisible par le conteneur. Il n'écrase aucun fichier
et ne télécharge aucun modèle automatiquement.

Avant de charger un second 27B sur les mêmes GPU, choisissez une période sans
traitement AxiorHub Pilote et vérifiez la mémoire disponible. Une concurrence entre
Ollama et llama.cpp fausserait le test et pourrait provoquer des erreurs mémoire.
Ne tuez pas les traitements en cours pour effectuer ce test.

~~~bash
sudo docker compose -f compose.example.yml config --quiet
sudo docker compose -f compose.example.yml run --rm llama-test --list-devices
sudo docker compose -f compose.example.yml up -d
curl -fsS http://127.0.0.1:8081/health
~~~

Le test commence à 8192 tokens et une seule requête simultanée. Vérifiez dans les
logs que les GPU AMD sont effectivement utilisés. Le partage des couches entre
GPU ne constitue pas une mémoire unique de 32 Go ; caches et buffers consomment
aussi de la VRAM. Les pilotes ou une image Vulkan défectueuse peuvent conduire à
un échec ou un repli CPU. Les paramètres devront être ajustés sur votre matériel.

## Relier Open WebUI

Le Compose crée le réseau axiorhub-llama-test. Ajoutez ce réseau externe à votre
service Open WebUI existant via un override, en conservant ses autres réseaux.
Utilisez ensuite ses deux fichiers Compose lors des recréations futures.

Dans Open WebUI : Administration → Paramètres → Connexions → OpenAI compatible,
ajoutez http://axiorhub-llama-test:8080/v1 et la clé locale de llama-token.
Gardez la connexion Ollama. Si Open WebUI propose un type de fournisseur
llama.cpp, vous pouvez le sélectionner.

Un localhost dans un conteneur désigne ce conteneur, pas votre serveur : n'utilisez
pas 127.0.0.1:8081 depuis Open WebUI Docker. L'accès par réseau Docker évite de
publier le port sur Internet.

## Mesurer avant de décider

Sur données fictives, faites trois essais après préchauffage et comparez :
1. délai avant réponse et durée totale ;
2. vitesse avec un petit puis un long dossier, à contexte égal ;
3. validité du JSON et choix des outils ;
4. qualité du français, fidélité aux sources et absence d'invention ;
5. comportement lorsqu'une tâche est longue ou échoue.

Gardez modèle, quantification, nombre de tokens générés et réglage du raisonnement
comparables. Un gain sur une petite réponse ne démontre pas un gain sur un acte
de trente pages. Ne modifiez pas la configuration des générations internes
AxiorHub Pilote tant qu'un adaptateur spécifique n'a pas été développé et testé.

Arrêter le test :
~~~bash
sudo docker compose -f compose.example.yml down
~~~
Le dossier des poids monté en lecture seule reste conservé.

## Sources techniques consultées

- Docker et image Vulkan : https://github.com/ggml-org/llama.cpp/blob/master/docs/docker.md
- Serveur, authentification, contexte et partage GPU : https://github.com/ggml-org/llama.cpp/blob/master/tools/server/README.md
- Connexion Open WebUI : https://docs.openwebui.com/getting-started/quick-start/connect-a-provider/starting-with-llama-cpp/

Les exemples sont une configuration d'essai, non une mesure de performance de
votre serveur. Aucun conteneur llama.cpp n'a été lancé sur votre infrastructure.

