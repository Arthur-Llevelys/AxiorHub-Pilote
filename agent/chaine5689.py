"""5.6.89 (chapitre 47) : ordre commun d'un appel au modèle pour les parcours du dossier.

Authentifier et autoriser ; lire la politique et les pauses ; constituer le contexte autorisé ; qualifier le modèle ; réserver le budget ; exécuter l'étape ; valider la sortie ; recontrôler l'état avant effet.
Publier, vérifier et émettre l'événement restent à l'appelant, qui connaît l'effet. La réservation du budget, le routage, la politique juste avant l'envoi et le bouclier des sources vivent dans ``model.Model`` :
ils ne sont pas refaits ici, et aucun parcours n'appelle un fournisseur autrement. Une attente de budget lève ``AttenteBudget`` (ou un code de ``ATTENTE_CODES``) : l'appelant la distingue d'un échec.

Un même adaptateur lit la réponse de tous les fournisseurs : un JSON pur, dans un bloc de code ou entouré de texte. Les capacités réelles de chaque modèle restent à qualifier au banc d'essai.
"""
import json
import re

from .common import Stop


def extraire_json(reponse):
    """Objet JSON d'une réponse de modèle. Tout le reste est refusé : une sortie invalide n'est jamais interprétée."""
    if isinstance(reponse, dict):
        return reponse
    texte = str(reponse or '').strip()
    bloc = re.search(r'```(?:json)?\s*(.*?)```', texte, re.S)
    if bloc:
        texte = bloc.group(1).strip()
    elif not texte.startswith('{'):
        debut, fin = texte.find('{'), texte.rfind('}')
        texte = texte[debut:fin + 1] if 0 <= debut < fin else ''
    try:
        data = json.loads(texte)
    except ValueError:
        raise Stop('reponse_modele_invalide') from None
    if not isinstance(data, dict):
        raise Stop('reponse_modele_invalide')
    return data


def modele_pour(desk, matter, purpose='legal_analysis', mission_budget=''):
    """Modèle choisi par le routeur commun : il reste dans les limites de données et de budget et n'élargit aucun droit si le modèle préféré manque."""
    from .model import Model, routed_config
    cfg = routed_config(desk.c, purpose)
    cfg['external_context'] = {'matter': matter}
    if mission_budget:
        cfg['mission5682'] = str(mission_budget)
    return Model(cfg)


def destination(modele):
    cfg = getattr(modele, 'cfg', None) or {}
    kind = cfg.get('provider_type') or cfg.get('type') or 'ollama'
    local = kind == 'ollama'
    return ('ollama' if local else str(cfg.get('provider_id') or kind)), local


def appeler(desk, etape, systeme, charge, matter, mission_id='', modele=None, purpose='legal_analysis', max_tokens=4000, schema=None, consigne='', owner='cabinet', role='avocat', budget='', temperature=0, labels=('dossier',)):
    """Une étape de bout en bout. Rend l'objet JSON validé. Avant et après le calcul, l'autorité de la mission et les pauses sont relues."""
    from . import competences5632, data_policy5689 as dp, missions567, pause5629
    missions567.check_authority(desk, {'mission_id': mission_id} if mission_id else {})
    pause5629.checkpoint(desk)
    modele = modele or modele_pour(desk, matter, purpose, budget)
    cible, local = destination(modele)
    dp.exiger(desk.c, 'llm', cible, finalite=etape, dossiers=(matter,) if matter else (), labels=labels if matter else (), acteur=owner, role=role, local=local, mission=str(budget or ''))
    messages = [{'role': 'system', 'content': systeme}, {'role': 'user', 'content': json.dumps(charge, ensure_ascii=False)}]
    with competences5632.pour_etape(desk, budget or mission_id, etape, consigne or etape, owner):
        reponse = modele.complete(messages, temperature=temperature, max_tokens=max_tokens, json_schema=schema)
    data = extraire_json(reponse)
    missions567.check_authority(desk, {'mission_id': mission_id} if mission_id else {})
    pause5629.checkpoint(desk)
    return data
