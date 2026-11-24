"""Noyaux proposés 5.6.109, sans service de portail ni autorisation implicite.

Le service de confiance fournit les instantanés, droits, permis et documents.
Ne jamais construire ces objets depuis les arguments libres du LLM.
"""
from dataclasses import dataclass
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import re
import shutil
import tempfile

from .common import Stop

LECTURES = frozenset({'consulter', 'telecharger', 'preparer', 'televerser_pdf'})
ENGAGEMENTS = frozenset({'signer_inpi', 'payer_inpi', 'transmettre_inpi',
                         'acheter_document_infogreffe', 'demander_rib_carpa',
                         # 5.6.109 (lot G3, G20) : effets distincts des portails étrangers (profils déclaratifs, non qualifiés)
                         'signer_etranger', 'payer_etranger', 'transmettre_etranger'})
# Catalogue fermé : aucun virement, retrait, libération de fonds ni modification de coordonnées bancaires (G16).


def _entier(v, code, minimum=0):
    if type(v) is not int or v < minimum:
        raise Stop(code)
    return v


def disponibilite(*, pause, poste_paire, poste_en_ligne, session_interactive, authentifie):
    """État explicite ; cette fonction ne lance ni navigateur ni modèle."""
    if pause:
        return 'reportee_pause'
    if not poste_paire:
        return 'poste_a_associer'
    if not poste_en_ligne or not session_interactive:
        return 'attente_poste'
    if not authentifie:
        return 'attente_connexion'
    return 'pret'


def empreinte_action(snapshot):
    """Lie le permis au compte, dossier, données, PDF, prix et effet exacts.

    ``snapshot`` doit être figé par le service après contrôle de ces champs.
    Aucun secret ou contenu complet de pièce n'y figure ; utiliser leurs IDs/SHA.
    """
    required=('cabinet', 'utilisateur', 'compte', 'portail', 'dossier', 'action', 'operation')
    if not isinstance(snapshot, dict) or any(not isinstance(snapshot.get(k), str) or not snapshot[k].strip() for k in required):
        raise Stop('contexte_portail_incomplet')
    if snapshot['action'] not in LECTURES | ENGAGEMENTS:
        raise Stop('action_portail_non_prevue')
    try:
        raw=json.dumps(snapshot,ensure_ascii=False,sort_keys=True,separators=(',', ':'),allow_nan=False)
    except (TypeError, ValueError):
        raise Stop('instantane_portail_invalide') from None
    return hashlib.sha256(raw.encode()).hexdigest()


@dataclass(frozen=True)
class Permis:
    empreinte: str
    expire: datetime
    reference: str


def verifier_permis(snapshot, permis, maintenant, *, routine=False):
    """Contrôle d'un permis serveur vérifié ; ne remplace pas ACL/pause/lease.

    Les routines de collecte sont limitées à la préparation. La signature,
    le paiement et la soumission ne deviennent jamais un effet du minuteur.
    Le reçu/nonce du permis doit être réservé transactionnellement par l'appelant.
    """
    fp=empreinte_action(snapshot)
    if routine and snapshot['action'] in ENGAGEMENTS:
        raise Stop('routine_preparation_seulement')
    if not isinstance(permis, Permis) or not permis.reference or permis.empreinte != fp:
        raise Stop('autorisation_exacte_requise')
    if (not isinstance(maintenant, datetime) or maintenant.tzinfo is None
            or not isinstance(permis.expire, datetime) or permis.expire.tzinfo is None):
        raise Stop('instant_autorisation_invalide')
    if maintenant.astimezone(timezone.utc) >= permis.expire.astimezone(timezone.utc):
        raise Stop('autorisation_expiree')
    return fp


def verifier_panier(*, prix_minor, autorise_minor, plafond_minor, reserve_minor, devise='EUR', periodique=False):
    """Montant TTC observé, entier en centimes, réservé sous transaction."""
    for n in (prix_minor, autorise_minor, plafond_minor, reserve_minor):
        _entier(n, 'montant_panier_invalide')
    if devise != 'EUR' or periodique is not False:
        raise Stop('option_panier_non_autorisee')
    if prix_minor != autorise_minor:
        raise Stop('prix_panier_modifie')
    if reserve_minor + prix_minor > plafond_minor:
        raise Stop('budget_portail_depasse')
    return prix_minor


def rapprochement(*, soumis, recu_verifie, reference_distante=''):
    """Une écriture incertaine ne doit pas être rejouée comme un simple échec."""
    if recu_verifie is True and reference_distante:
        return 'confirme'
    if soumis:
        return 'a_rapprocher'
    return 'non_soumis'


class DepotPDF:
    """Copie privée d'un PDF canonique autorisé, sans conversion de l'original.

    Le dossier parent doit être privé (ACL du compte sur Windows). La primitive
    CDP n'atteste pas la réception serveur. Garder la copie jusqu'au reçu ou à
    l'annulation rapprochée. Pas d'API générale d'upload ni chemin libre au LLM.
    """
    def __init__(self, parent, *, document_id, version, nom, contenu, sha256):
        if not document_id or not version or not re.fullmatch(r'[^/\\\x00-\x1f:]{1,180}\.pdf',nom,re.I):
            raise Stop('document_televersement_invalide')
        if (type(contenu) is not bytes or not 0 < len(contenu) <= 32*1024*1024
                or not contenu.startswith(b'%PDF-') or b'%%EOF' not in contenu[-2048:]):
            raise Stop('pdf_televersement_invalide')
        if hashlib.sha256(contenu).hexdigest() != sha256:
            raise Stop('version_document_modifiee')
        self.ticket={'document_id':str(document_id),'version':str(version),'nom':nom,
                     'sha256':sha256,'octets':len(contenu)}
        self._directory=Path(tempfile.mkdtemp(prefix='depot-axh-',dir=parent))
        try:
            os.chmod(self._directory,0o700)
            self._path=self._directory/nom
            fd=os.open(self._path,os.O_WRONLY|os.O_CREAT|os.O_EXCL,0o600)
            with os.fdopen(fd,'wb') as f:f.write(contenu)
        except BaseException:
            shutil.rmtree(self._directory)
            raise
        self.etat='prepare'

    def televerser(self, tab, ref, controle):
        """controle(ticket) doit vérifier droits, pause, epoch, domaine et mission.

        Le repère doit provenir du dernier DOM observé. Aucun JavaScript libre
        ni chemin fourni par le modèle. Un appel parti reste à rapprocher même
        si la réponse CDP est perdue ; ne pas réessayer automatiquement.
        """
        if not callable(controle):
            raise Stop('controle_televersement_requis')
        if self.etat != 'prepare':
            raise Stop('televersement_deja_tente')
        controle(dict(self.ticket))
        backend=tab.refs.backend(ref)
        if backend is None:
            raise Stop('repere_absent_de_la_page')
        node=tab.cdp.send('DOM.describeNode',{'backendNodeId':backend},session=tab.session).get('node') or {}
        attrs=node.get('attributes') or []
        if len(attrs)%2:
            raise Stop('champ_fichier_invalide')
        attr={str(attrs[i]).casefold():str(attrs[i+1]).casefold() for i in range(0,len(attrs),2)}
        if (node.get('localName')!='input' or attr.get('type')!='file'
                or any(k in attr for k in ('disabled','webkitdirectory','directory'))):
            raise Stop('champ_fichier_invalide')
        if hashlib.sha256(self._path.read_bytes()).hexdigest()!=self.ticket['sha256']:
            raise Stop('copie_privee_modifiee')
        controle(dict(self.ticket))
        self.etat='a_rapprocher'
        tab.cdp.send('DOM.setFileInputFiles',{'backendNodeId':backend,'files':[str(self._path)]},session=tab.session)
        controle(dict(self.ticket))
        return {**self.ticket,'etat':self.etat}

    def liberer(self, *, rapproche=False):
        if self.etat=='a_rapprocher' and rapproche is not True:
            raise Stop('reception_ou_annulation_a_rapprocher')
        shutil.rmtree(self._directory)
        self.etat='ferme'
