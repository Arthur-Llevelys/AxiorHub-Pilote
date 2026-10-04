"""Small, stable catalogue for the human-facing AxiorHub action guide."""


def catalogue():
    return {
      'version':'4.2.0',
      'title':'Que voulez-vous faire ?',
      'safety':'AxiorHub analyse et prépare. Toute création, tout envoi, tout dépôt et toute facturation définitive restent soumis à confirmation.',
      'journeys':[
        {'id':'routing','icon':'⇄','title':'Choisir le routage IA','summary':'Local par défaut, OpenRouter uniquement sous contrôle de confidentialité et de budget.',
         'href':'/routage-hybride','prompt':'Simule le routage de cette tâche, estime son coût et montre les données anonymisées avant toute transmission externe.'},
        {'id':'system','icon':'🩺','title':'Contrôler l’exploitation','summary':'Services, traitements bloqués, destinations, index et modèles relus depuis leur source.',
         'href':'/etat-systeme','prompt':'Affiche l’état du système AxiorHub, les traitements en erreur, les brouillons IMAP et fichiers Nextcloud effectivement retrouvés, puis indique les incidents à corriger.'},
        {'id':'mail','icon':'📬','title':'Traiter un courriel','summary':'Analyser le message, le rapprocher du dossier et préparer une réponse.',
         'href':'/orchestrateur-avis','prompt':'Analyse le dernier courriel reçu pour le dossier [RÉFÉRENCE]. Compare-le au dossier, propose une réponse, les diligences et la facturation éventuelle. Ne crée et n’envoie encore rien.'},
        {'id':'matter','icon':'📁','title':'Comprendre un dossier','summary':'Synthèse, chronologie, pièces, risques, manques et prochaines actions.',
         'href':'/dossiers','prompt':'Ouvre le dossier [RÉFÉRENCE]. Donne-moi une synthèse sourcée, la chronologie, les prétentions, les pièces importantes, les contradictions, les échéances et les prochaines diligences. Signale ce qui reste incertain.'},
        {'id':'document','icon':'📝','title':'Rédiger un document','summary':'Conclusions, assignation, contrat, CGV, BCP, consultation ou courrier.',
         'href':'/projets','prompt':'Dans le dossier [RÉFÉRENCE], prépare un projet de [TYPE DE DOCUMENT] à partir de la dernière version substantielle et des nouveaux courriels et pièces. Montre les sources et les modifications. Ne crée encore aucun fichier.'},
        {'id':'hearing','icon':'⚖️','title':'Préparer une audience','summary':'Contradictoire, dispositif, plans de plaidoirie et pièces à emporter.',
         'href':'/audiences-word','prompt':'Dans le dossier [RÉFÉRENCE], identifie les dernières conclusions des parties et prépare l’audience : matrice contradictoire, points forts et faibles, plans de plaidoirie de 5, 10 et 20 minutes, questions probables et pièces à emporter. Ne crée aucun fichier.'},
        {'id':'practice','icon':'📊','title':'Assistance métier','summary':'Coaching, simulations sourcées, appels, calculs et revue de facturation.',
         'href':'/assistance-metier','prompt':'Pour le dossier [RÉFÉRENCE], prépare une simulation contradictoire à partir de décisions officielles vérifiées ; indique les sources, les points manquants et les pistes d’amélioration. Si une série de dix décisions distinctes est qualifiée par l’avocat, présente uniquement sa fréquence observée et son dénominateur.'},
        {'id':'planning','icon':'📅','title':'Organiser mon travail','summary':'Lire l’agenda, proposer une répartition et créer seulement après confirmation.',
         'href':'/planning','prompt':'Lis mon agenda du [DÉBUT] au [FIN], puis répartis ces tâches avec une charge maximale de cinq heures par jour. Affiche audiences, blocages et éléments non planifiables. Ne crée encore rien : [TÂCHES].'},
        {'id':'audio','icon':'🎙️','title':'Transcrire une réunion','summary':'Déposer l’audio dans SpeakR, transcrire localement avec Vocal, puis préparer le compte rendu.',
         'href':'/guide?section=audio','external_href':'https://speakr.example.com/',
         'prompt':'À partir de la transcription SpeakR [SOURCE], prépare pour le dossier [RÉFÉRENCE] un compte rendu sourcé, les décisions à confirmer, les tâches proposées, les échéances et les points juridiques à contrôler. N’exécute aucune action.'},
      ],
      'rules':[
        'Commencer par une référence de dossier exacte.',
        'Demander une prévisualisation avant toute création.',
        'Contrôler les sources, les incertitudes et le dossier de destination.',
        'Recopier le code uniquement si le résultat affiché est conforme.',
      ],
      'audio':{
        'speakr_url':'https://speakr.example.com/',
        'vocal_url':'https://vocal.example.com/',
        'workflow':['Ouvrir SpeakR et déposer ou enregistrer l’audio.',
                    'Choisir la transcription locale large-v3-turbo.',
                    'Relire les noms, dates et termes juridiques signalés.',
                    'Demander à AxiorHub le compte rendu pour le dossier exact.'],
        'privacy':'Le pont audio ne journalise ni fichier, ni nom, ni transcription : seulement l’empreinte SHA-256, la taille, la durée et l’état technique.'
      }
    }
