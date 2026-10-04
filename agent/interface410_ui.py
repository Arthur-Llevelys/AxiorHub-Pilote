"""Hubs de navigation simplifiés de la version 4.1."""
from html import escape


def e(value): return escape(str(value if value is not None else ''), quote=True)


def _cards(items, link):
    return '<div class="hub410-grid">'+''.join(
      '<article><span aria-hidden="true">'+e(icon)+'</span><h2>'+e(title)+'</h2><p>'+e(text)+'</p>'+link(path, action)+'</article>'
      for path,icon,title,text,action in items)+'</div>'


def produce_hub(link, pdf_url=''):
    items=[
      ('/','✉','Courriers et courriels','Préparer, relire et déposer les projets dans les brouillons.','Ouvrir'),
      ('/projets','§','Conclusions et contrats','Produire et relire des documents à partir du dossier et des modèles Word.','Produire'),
      ('/audiences-word','⚖','Audiences','Comparer les conclusions, préparer le dossier et la note de plaidoirie.','Préparer'),
      ('/recherche-juridique','⌕','Recherche juridique','Préparer une recherche anonymisée et vérifier les sources officielles.','Rechercher'),
      ('/pieces','▧','Pièces PDF et bordereaux','Bordereau de communication, pièces numérotées et tamponnées, contrôles avec les conclusions.','Ouvrir'),
      ('/assistance-metier','€','Facturation','Contrôler les diligences, provisions et éléments de facturation.','Contrôler')]
    out='<section class="hub410-hero"><p class="eyebrow">4.1 · PRODUIRE</p><h1>Un point d’entrée par résultat attendu</h1><p>Le dossier ouvert et les documents sélectionnés sont transmis automatiquement à l’assistant transversal.</p></section>'+_cards(items,link)
    if pdf_url:
        out+='<p class="hub410-external"><a target="_blank" rel="noopener noreferrer" href="'+e(pdf_url)+'">Ouvrir l’atelier PDF AxiorHub ↗</a></p>'
    return out


def settings_hub(link):
    items=[
      ('/apprentissage','↻','Apprentissage métier','Règles, corrections, formulations et documents fiables.','Configurer'),
      ('/evaluations','✓','Banc juridique','Comparer les modèles sur les cas anonymisés du cabinet.','Évaluer'),
      ('/routage-hybride','⇄','Routage hybride','Local, externe contrôlé, budgets et contrôle contradictoire.','Configurer'),
      ('/mcp','⌁','Connecteurs MCP','Installer, tester et limiter les connecteurs métier.','Gérer'),
      ('/regles','⚙','Règles et autonomie','Courriels, niveaux d’autonomie et actions autorisées.','Gérer'),
      ('/etat-systeme','●','État du système','Services, files de tâches, secrets et livrables vérifiés.','Diagnostiquer')]
    return '<section class="hub410-hero"><p class="eyebrow">4.1 · PARAMÈTRES</p><h1>Réglages du cabinet</h1><p>Les fonctions avancées restent accessibles ici sans surcharger la navigation principale.</p></section>'+_cards(items,link)
