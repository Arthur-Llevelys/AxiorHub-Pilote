"""5.6.107 (lot V) : vidéo de démonstration de 90 secondes d'AxiorHub Pilote (outil de développement, jamais dans un paquet ni sur un serveur).

- ``scenario.json`` : les huit plans, leur durée visée, la légende incrustée et les textes qui désignent les éléments à filmer ;
- ``voix.txt`` : texte de la voix off, une phrase par plan (soumis à l'avocat avant l'enregistrement final) ;
- ``reglages.json`` : image, voix, sous-titres, encodage ;
- ``texte`` (minutage, sous-titres), ``voix`` (synthèse locale), ``film`` (enregistrement Playwright), ``montage`` (ffmpeg) ;
- ``calque.js`` : curseur, légendes et carton de fin injectés par le script (pas par le produit).

Point d'entrée : ``python scripts/video-demo.py``.
"""
