# AxiorHub Pilote — démo en ligne (variante Docker du lot H, 5.6.107). Construite par l'avocat sur son serveur :
#   docker compose -f deploy/demo/compose.yml build
# Le répartiteur et les instances des visiteurs tournent dans ce seul conteneur, sans réseau (network_mode: none) ; le mandataire
# HTTPS de l'hôte les joint par le socket Unix partagé (dossier ./socket).
FROM python:3.12-slim
RUN pip install --no-cache-dir waitress==3.0.2 cryptography==44.0.0 \
 && useradd --system --uid 10001 --user-group --no-create-home --home-dir /var/lib/axiorhub-demo --shell /usr/sbin/nologin axiorhub-demo
WORKDIR /app
# Code appartenant à root, en lecture seule pour le service ; ni dépôt Git ni essais dans l'image.
COPY --chown=root:root . /app
RUN rm -rf /app/.git /app/tests /app/data /app/.env && chmod -R a-w,a+rX /app
ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUTF8=1 LANG=C.UTF-8 TZ=Europe/Paris AXIORHUB_DEMO_REGLAGES=/etc/axiorhub-demo/demo.json
USER 10001:10001
ENTRYPOINT ["python3", "-E", "-s", "-m", "agent.demo_hote56107", "servir"]
