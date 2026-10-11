"""5.6.34 (B17) : guide d'installation de la synthèse vocale Kokoro selon le système (Windows, poste Ubuntu, serveur Ubuntu) et voix des
langues usuelles (français, anglais, allemand, espagnol).

AxiorHub dialogue avec Kokoro-FastAPI (API compatible OpenAI ``/v1/audio/speech``, port 8880). Faits vérifiés le 11 octobre 2026 sur
le dépôt du projet (github.com/remsky/Kokoro-FastAPI) : images Docker ``kokoro-fastapi-cpu`` et ``kokoro-fastapi-gpu`` ; lancement
sans Docker par ``start-cpu.ps1`` / ``start-gpu.ps1`` (Windows) ou ``start-cpu.sh`` / ``start-gpu.sh`` (Linux) ; langues : anglais (US/GB),
espagnol, français, hindi, italien, japonais, portugais du Brésil, chinois. L'ALLEMAND n'est pas proposé par Kokoro : la lecture en
allemand passe par eSpeak NG (installé avec AxiorHub sur le serveur) ou par Chatterbox, ce que le guide indique sans détour.
Les numéros de version des images doivent être fixés à l'installation (« :latest » est réservé à l'essai).
"""
from html import escape as e

VOICES = {'fr': ('ff_siwis', 'kokoro'), 'en': ('af_heart (US) · bf_emma (GB)', 'kokoro'), 'es': ('ef_dora · em_alex', 'kokoro'),
          'de': ('— (non proposé par Kokoro)', 'espeak')}
DOCKER_CPU = 'docker run -d --name kokoro --restart unless-stopped -p 127.0.0.1:8880:8880 ghcr.io/remsky/kokoro-fastapi-cpu:<version>'
DOCKER_GPU = 'docker run -d --name kokoro --restart unless-stopped --gpus all -p 127.0.0.1:8880:8880 ghcr.io/remsky/kokoro-fastapi-gpu:<version>'

TEXT = {
    'fr': {
        'intro': 'La synthèse vocale lit le résumé du briefing et les réponses de l’agent. Kokoro fonctionne en local : aucun texte ne quitte la machine. '
                 'Elle est facultative ; sans elle, eSpeak NG (voix synthétique simple) reste disponible.',
        'windows': 'Poste Windows', 'poste': 'Poste Ubuntu', 'serveur': 'Serveur Ubuntu (ou VPS)',
        'win_steps': ['Installez Docker Desktop (docker.com), puis ouvrez PowerShell.', 'Lancez : ' + DOCKER_CPU,
                      'Sans Docker : téléchargez le projet Kokoro-FastAPI (GitHub) puis exécutez .\\start-cpu.ps1 (ou .\\start-gpu.ps1 avec une carte NVIDIA).',
                      'Dans AxiorHub : Paramètres › Voix, fournisseur « kokoro », adresse http://127.0.0.1:8880, puis Tester.'],
        'poste_steps': ['Installez Docker (sudo apt install docker.io) et ajoutez votre compte au groupe docker.', 'Lancez : ' + DOCKER_CPU,
                        'Carte NVIDIA : ' + DOCKER_GPU, 'Dans AxiorHub : Paramètres › Voix, fournisseur « kokoro », adresse http://127.0.0.1:8880, puis Tester.'],
        'serveur_steps': ['Sur le serveur : ' + DOCKER_CPU + ' (le port reste limité à la machine, 127.0.0.1).',
                          'Installation Docker d’AxiorHub (docker compose) : le service « kokoro » est déjà prévu ; adresse http://kokoro:8880.',
                          'Dans AxiorHub : Paramètres › Voix, fournisseur « kokoro », puis Tester. N’exposez jamais le port 8880 sur Internet.'],
        'langs': 'Voix par langue', 'lang': 'Langue', 'voice': 'Voix', 'engine': 'Moteur',
        'de_note': 'Allemand : Kokoro ne propose pas de voix allemande. Choisissez eSpeak NG (voix « de ») ou Chatterbox si vous l’avez installé.',
        'version': 'Remplacez <version> par une version publiée du projet (évitez « latest » en production).',
        'names': {'fr': 'Français', 'en': 'Anglais', 'de': 'Allemand', 'es': 'Espagnol'}},
    'en': {
        'intro': 'Speech synthesis reads the briefing summary and the agent’s answers. Kokoro runs locally: no text leaves the machine. It is optional; '
                 'without it, eSpeak NG (a simple synthetic voice) remains available.',
        'windows': 'Windows workstation', 'poste': 'Ubuntu workstation', 'serveur': 'Ubuntu server (or VPS)',
        'win_steps': ['Install Docker Desktop (docker.com), then open PowerShell.', 'Run: ' + DOCKER_CPU,
                      'Without Docker: download the Kokoro-FastAPI project (GitHub) and run .\\start-cpu.ps1 (or .\\start-gpu.ps1 with an NVIDIA card).',
                      'In AxiorHub: Settings › Voice, provider “kokoro”, address http://127.0.0.1:8880, then Test.'],
        'poste_steps': ['Install Docker (sudo apt install docker.io) and add your account to the docker group.', 'Run: ' + DOCKER_CPU,
                        'NVIDIA card: ' + DOCKER_GPU, 'In AxiorHub: Settings › Voice, provider “kokoro”, address http://127.0.0.1:8880, then Test.'],
        'serveur_steps': ['On the server: ' + DOCKER_CPU + ' (the port stays limited to the machine, 127.0.0.1).',
                          'Docker installation of AxiorHub (docker compose): the “kokoro” service is already provided; address http://kokoro:8880.',
                          'In AxiorHub: Settings › Voice, provider “kokoro”, then Test. Never expose port 8880 to the Internet.'],
        'langs': 'Voices by language', 'lang': 'Language', 'voice': 'Voice', 'engine': 'Engine',
        'de_note': 'German: Kokoro offers no German voice. Choose eSpeak NG (voice “de”) or Chatterbox if installed.',
        'version': 'Replace <version> with a published release of the project (avoid “latest” in production).',
        'names': {'fr': 'French', 'en': 'English', 'de': 'German', 'es': 'Spanish'}},
    'de': {
        'intro': 'Die Sprachausgabe liest die Zusammenfassung des Briefings und die Antworten des Agenten vor. Kokoro läuft lokal: Kein Text verlässt den '
                 'Rechner. Sie ist optional; ohne sie bleibt eSpeak NG (einfache synthetische Stimme) verfügbar.',
        'windows': 'Windows-Arbeitsplatz', 'poste': 'Ubuntu-Arbeitsplatz', 'serveur': 'Ubuntu-Server (oder VPS)',
        'win_steps': ['Docker Desktop (docker.com) installieren, dann PowerShell öffnen.', 'Ausführen: ' + DOCKER_CPU,
                      'Ohne Docker: das Projekt Kokoro-FastAPI (GitHub) herunterladen und .\\start-cpu.ps1 ausführen (oder .\\start-gpu.ps1 mit NVIDIA-Karte).',
                      'In AxiorHub: Einstellungen › Stimme, Anbieter „kokoro“, Adresse http://127.0.0.1:8880, dann Testen.'],
        'poste_steps': ['Docker installieren (sudo apt install docker.io) und das eigene Konto der Gruppe docker hinzufügen.', 'Ausführen: ' + DOCKER_CPU,
                        'NVIDIA-Karte: ' + DOCKER_GPU, 'In AxiorHub: Einstellungen › Stimme, Anbieter „kokoro“, Adresse http://127.0.0.1:8880, dann Testen.'],
        'serveur_steps': ['Auf dem Server: ' + DOCKER_CPU + ' (der Port bleibt auf den Rechner beschränkt, 127.0.0.1).',
                          'Docker-Installation von AxiorHub (docker compose): Der Dienst „kokoro“ ist bereits vorgesehen; Adresse http://kokoro:8880.',
                          'In AxiorHub: Einstellungen › Stimme, Anbieter „kokoro“, dann Testen. Port 8880 niemals im Internet freigeben.'],
        'langs': 'Stimmen nach Sprache', 'lang': 'Sprache', 'voice': 'Stimme', 'engine': 'Engine',
        'de_note': 'Deutsch: Kokoro bietet keine deutsche Stimme. Wählen Sie eSpeak NG (Stimme „de“) oder, falls installiert, Chatterbox.',
        'version': 'Ersetzen Sie <version> durch eine veröffentlichte Version des Projekts (in der Produktion kein „latest“).',
        'names': {'fr': 'Französisch', 'en': 'Englisch', 'de': 'Deutsch', 'es': 'Spanisch'}},
    'es': {
        'intro': 'La síntesis de voz lee el resumen del briefing y las respuestas del agente. Kokoro funciona en local: ningún texto sale del equipo. '
                 'Es opcional; sin ella, eSpeak NG (voz sintética sencilla) sigue disponible.',
        'windows': 'Puesto Windows', 'poste': 'Puesto Ubuntu', 'serveur': 'Servidor Ubuntu (o VPS)',
        'win_steps': ['Instale Docker Desktop (docker.com) y abra PowerShell.', 'Ejecute: ' + DOCKER_CPU,
                      'Sin Docker: descargue el proyecto Kokoro-FastAPI (GitHub) y ejecute .\\start-cpu.ps1 (o .\\start-gpu.ps1 con una tarjeta NVIDIA).',
                      'En AxiorHub: Ajustes › Voz, proveedor «kokoro», dirección http://127.0.0.1:8880 y luego Probar.'],
        'poste_steps': ['Instale Docker (sudo apt install docker.io) y añada su cuenta al grupo docker.', 'Ejecute: ' + DOCKER_CPU,
                        'Tarjeta NVIDIA: ' + DOCKER_GPU, 'En AxiorHub: Ajustes › Voz, proveedor «kokoro», dirección http://127.0.0.1:8880 y luego Probar.'],
        'serveur_steps': ['En el servidor: ' + DOCKER_CPU + ' (el puerto queda limitado al equipo, 127.0.0.1).',
                          'Instalación Docker de AxiorHub (docker compose): el servicio «kokoro» ya está previsto; dirección http://kokoro:8880.',
                          'En AxiorHub: Ajustes › Voz, proveedor «kokoro» y luego Probar. No exponga nunca el puerto 8880 en Internet.'],
        'langs': 'Voces por idioma', 'lang': 'Idioma', 'voice': 'Voz', 'engine': 'Motor',
        'de_note': 'Alemán: Kokoro no ofrece voz alemana. Elija eSpeak NG (voz «de») o Chatterbox si lo ha instalado.',
        'version': 'Sustituya <version> por una versión publicada del proyecto (evite «latest» en producción).',
        'names': {'fr': 'Francés', 'en': 'Inglés', 'de': 'Alemán', 'es': 'Español'}},
}


def guide_html(lang='fr', mode='serveur'):
    """Étape du guide : le système en cours d'abord (ouvert), les autres repliés ; tableau des voix par langue."""
    t = TEXT.get(lang) or TEXT['fr']
    order = [mode] + [m for m in ('windows', 'poste_linux', 'serveur') if m != mode]
    key = {'windows': ('windows', 'win_steps'), 'poste_linux': ('poste', 'poste_steps'), 'serveur': ('serveur', 'serveur_steps')}
    blocks = ''
    for i, m in enumerate(order):
        title, steps = key[m]
        blocks += '<details class="k34-os"%s><summary>%s</summary><ol>%s</ol></details>' % (
            ' open' if i == 0 else '', e(t[title]), ''.join('<li>%s</li>' % e(s) for s in t[steps]))
    rows = ''.join('<tr><td>%s</td><td>%s</td><td>%s</td></tr>' % (e(t['names'][code]), e(VOICES[code][0]), e('Kokoro' if VOICES[code][1] == 'kokoro' else 'eSpeak NG / Chatterbox'))
                   for code in ('fr', 'en', 'de', 'es'))
    return ('<div class="k34"><p>%s</p>%s<p class="k34-note">%s</p><h3>%s</h3><table class="k34-voices"><thead><tr><th>%s</th><th>%s</th><th>%s</th></tr></thead>'
            '<tbody>%s</tbody></table><p class="k34-note">%s</p></div>') % (e(t['intro']), blocks, e(t['version']), e(t['langs']), e(t['lang']), e(t['voice']),
                                                                         e(t['engine']), rows, e(t['de_note']))
