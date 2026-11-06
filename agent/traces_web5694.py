"""5.6.94 (lot traces, T04-T05) : « Où ça bloque » dans Paramètres › Confort et maintenance › Charge du serveur, téléchargement OTLP/JSON
et envoi vers un collecteur local.

Routes (déclarées par ``charge_web5693``, donc par ``web5689``) : ``GET m5689/charge/traces`` (téléchargement), ``POST
m5689/charge/traces/reglage`` (actif, rétention, plafond, collecteur ; révision lue par le formulaire, 409 si périmée) et ``POST
m5689/charge/traces/envoyer`` (clé d'idempotence). Toutes demandent le droit « Réglages » du cabinet ; les écritures passent par
``check_post``. Affichage en français clair, échappé ; aucune trace : « aucune trace sur la période », jamais un faux zéro."""
from html import escape as e
import uuid

from . import traces5694 as traces
from .common import Stop
from .ui_components5689 import case, etat_html, formulaire, texte

MESSAGES = {'collecteur_non_local': 'Adresse refusée : le collecteur doit être sur ce serveur ou sur le réseau du cabinet (127.0.0.1, localhost, ::1 '
                                    'ou un nom sans point). Aucune trace n’est envoyée vers un service en ligne.',
            'collecteur_invalide': 'Adresse du collecteur invalide : http:// ou https://, sans identifiant, sans paramètre (exemple : http://localhost:4318).',
            'collecteur_introuvable': 'Le nom du collecteur ne correspond à aucune machine connue de ce serveur. Rien n’a été envoyé.',
            'collecteur_injoignable': 'Le collecteur local n’a pas répondu. Vérifiez qu’il est démarré ; rien n’a été perdu, les traces restent ici.',
            'collecteur_non_configure': 'Aucun collecteur local n’est réglé.',
            'cle_idempotence_requise': 'Demande incomplète : rechargez la page puis recommencez.',
            'cle_idempotence_deja_utilisee': 'Cet envoi a déjà été fait. Rechargez la page pour en faire un nouveau.',
            'reglage_traces_invalide': 'Réglage invalide : conservation de 1 à 30 jours, de 1 000 à 200 000 étapes.',
            'traces_illisibles': 'Les traces n’ont pas pu être lues dans la base. Réessayez dans un instant.',
            'periode_invalide': 'Période invalide.'}
RESULTATS = {'done': 'terminé', 'error': 'erreur', 'suspended': 'suspendu', 'cancelled': 'annulé', 'remis_en_attente': 'remis en attente'}


def _libelle(kind):
    try:
        from .web import JOB_LABELS
        return JOB_LABELS.get(kind) or kind
    except Exception:   # noqa: BLE001 — libellés indisponibles : le nom technique reste lisible
        return kind


def duree(ms):
    """Durée lisible : « 850 ms », « 4,2 s », « 3 min 05 s », « 1 h 02 min ». ``None`` : « non mesuré »."""
    if ms is None:
        return 'non mesuré'
    ms = int(ms)
    if ms < 1000:
        return '%d ms' % ms
    if ms < 60000:
        return ('%.1f s' % (ms / 1000)).replace('.', ',')
    minutes, secondes = divmod(ms // 1000, 60)
    if minutes < 60:
        return '%d min %02d s' % (minutes, secondes)
    heures, minutes = divmod(minutes, 60)
    return '%d h %02d min' % (heures, minutes)


def _octets(n):
    n = int(n or 0)
    return ('%d octets' % n) if n < 1024 else (('%.0f Ko' % (n / 1024)) if n < 1024 * 1024 else ('%.1f Mo' % (n / 1048576)).replace('.', ','))


def _refus(refus):
    if not refus:
        return 'aucun'
    return ' ; '.join('%s : %d' % (traces.LIBELLES_CAUSES.get(c, c), n) for c, n in sorted(refus.items()))


def _etape(etape):
    a = etape['attributs']
    if etape['genre'] == 'modele':
        texte_ = 'Appel au modèle (%s · %s%s)' % (a.get('axiorhub.fournisseur.id', '?'), a.get('gen_ai.request.model', '?'),
                                                 (', ' + a['axiorhub.modele.etape']) if a.get('axiorhub.modele.etape') else '')
        if a.get('axiorhub.modele.relais'):
            texte_ += ', relais vers un autre fournisseur'
        elif a.get('axiorhub.modele.nouvel_essai'):
            texte_ += ', nouvel essai'
        if a.get('gen_ai.usage.input_tokens') or a.get('gen_ai.usage.output_tokens'):
            texte_ += ', %d jetons envoyés, %d reçus' % (a.get('gen_ai.usage.input_tokens', 0), a.get('gen_ai.usage.output_tokens', 0))
    elif etape['nom'] == 'lot_nextcloud':
        texte_ = 'Lecture du stockage Nextcloud (%d requêtes, %s)' % (a.get('axiorhub.lot.requetes', 0), _octets(a.get('axiorhub.lot.octets')))
    elif etape['nom'] == 'lot_imap':
        texte_ = 'Messagerie IMAP (%d commandes, %s)' % (a.get('axiorhub.lot.requetes', 0), _octets(a.get('axiorhub.lot.octets')))
    else:
        texte_ = etape['nom']
    if etape['statut'] == traces.STATUT_ERREUR:
        texte_ += ' — échec (%s)' % (etape['code'] or 'motif non noté')
    return texte_


def _frise(mission):
    """Frise d'un travail : barres SVG (attributs, pas de style en ligne, compatible avec la CSP) et la même information en texte."""
    total = max(1, mission['duree_ms'])
    hauteur = 12 + 16 * len(mission['etapes'])
    barres = ['<rect class="t5694-travail" x="0" y="2" width="1000" height="8"></rect>']
    lignes = []
    for i, etape in enumerate(mission['etapes']):
        x = min(1000, int(1000 * etape['decalage_ms'] / total))
        largeur = max(3, min(1000 - x, int(1000 * etape['duree_ms'] / total)))
        classe = 't5694-erreur' if etape['statut'] == traces.STATUT_ERREUR else ('t5694-modele' if etape['genre'] == 'modele' else 't5694-lot')
        barres.append('<rect class="%s" x="%d" y="%d" width="%d" height="12"></rect>' % (classe, x, 14 + 16 * i, largeur))
        lignes.append('<li>%s : %s, à partir de %s</li>' % (e(_etape(etape)), e(duree(etape['duree_ms'])), e(duree(etape['decalage_ms']))))
    resultat = RESULTATS.get(mission['resultat'], mission['resultat'] or 'inconnu')
    resume = '%s — %s (attente avant démarrage : %s) — %s' % (_libelle(mission['kind']), duree(mission['duree_ms']), duree(mission['attente_ms']), resultat)
    if mission['code'] and mission['resultat'] != 'done':
        resume += ' (%s)' % mission['code']
    detail = ('<ol class="t5694-etapes">%s</ol>' % ''.join(lignes)) if lignes else '<p class="ax-muted">Aucune étape détaillée : ni appel au modèle, ni requête au stockage ou à la messagerie.</p>'
    return ('<details class="t5694-mission"><summary>%s</summary><svg class="t5694-frise" viewBox="0 0 1000 %d" preserveAspectRatio="none" role="img" '
            'aria-label="%s" focusable="false">%s</svg>%s</details>') % (e(resume), hauteur, e('Frise : ' + resume, quote=True), ''.join(barres), detail)


def section_html(desk, prefix, ecriture):
    """Sous-section de « Charge du serveur ». Une erreur de lecture affiche un état d'erreur, jamais des zéros."""
    r = traces.reglage(desk)
    corps = ['<h3 id="traces5694-titre">Où ça bloque (dernières 24 heures)</h3>',
             '<p>Pour chaque type de travail : combien de temps il prend, combien de temps il attend avant de démarrer, ses erreurs, ses nouveaux '
             'essais et les refus rencontrés (pause, horaires, fournisseur non autorisé, plafond de dépense, dossier réservé). Les étapes sont '
             'notées sans aucun contenu : ni texte, ni nom de document, ni dossier en clair, ni adresse.</p>']
    if not r['actif']:
        corps.append(etat_html('non_configure', 'Traces désactivées', 'Aucune étape n’est notée tant que les traces sont désactivées (réglage ci-dessous).'))
    else:
        s = traces.synthese(desk, 24)
        if s is None:
            corps.append(etat_html('erreur', 'Traces illisibles', 'La base n’a pas pu être lue. Aucun chiffre n’est affiché plutôt qu’un faux zéro.'))
        elif not s['travaux']:
            corps.append(etat_html('vide', 'Aucune trace sur la période', 'Aucun travail n’a été tracé depuis 24 heures (les traces commencent avec la 5.6.94).'))
        else:
            lignes = ''.join('<tr><th scope="row">%s</th><td>%d</td><td>%s</td><td>%s</td><td>%s</td><td>%d</td><td>%d</td><td>%s</td></tr>' % (
                e(_libelle(t['kind'])), t['n'], e(duree(t['mediane_ms'])), e(duree(t['p95_ms'])), e(duree(t['attente_mediane_ms'])),
                t['erreurs'], t['essais'], e(_refus(t['refus']))) for t in s['par_type'][:40])
            corps.append('<table class="vf-table t5694-table"><caption>Par type de travail (%d travaux tracés)</caption><thead><tr><th scope="col">Travail</th>'
                         '<th scope="col">Exécutions</th><th scope="col">Durée habituelle (médiane)</th><th scope="col">95 %% des travaux en moins de</th>'
                         '<th scope="col">Attente avant démarrage (médiane)</th><th scope="col">Erreurs</th><th scope="col">Nouveaux essais</th>'
                         '<th scope="col">Refus</th></tr></thead><tbody>%s</tbody></table>' % (s['travaux'], lignes))
            corps.append('<h4>Les dix travaux les plus lents</h4><p class="ax-muted">Chaque frise montre les étapes d’un travail dans le temps : '
                         'appels au modèle, lectures du stockage et de la messagerie.</p>' + ''.join(_frise(m) for m in s['lents']))
    if ecriture:
        corps.append('<p><a class="ax-btn ghost" href="%s">Télécharger les traces des dernières 24 heures (OTLP/JSON)</a></p>' % e(
            prefix + '/api440/m5689/charge/traces?heures=24', quote=True))
        corps.append(formulaire('m5689/charge/traces/reglage', '<fieldset><legend>Réglage des traces</legend>%s%s%s%s<input type="hidden" name="revision" value="%d">'
                                '</fieldset><button class="ax-btn" type="submit">Enregistrer</button>' % (
                                    case('actif', 'Noter les étapes des travaux', r['actif'], 'Désactivé : rien n’est noté ; les traces existantes sont gardées jusqu’à leur échéance.'),
                                    texte('retention_jours', 'Durée de conservation (jours, de 1 à 30)', str(r['retention_jours']), True, genre='number'),
                                    texte('plafond', 'Nombre maximal d’étapes conservées (de 1 000 à 200 000)', str(r['plafond']), True, genre='number'),
                                    texte('collecteur', 'Collecteur OpenTelemetry local (facultatif)', r['collecteur'], False,
                                          'Adresse sur ce serveur ou le réseau du cabinet seulement : 127.0.0.1, localhost, ::1 ou un nom sans point '
                                          '(par exemple http://localhost:4318). Toute adresse publique est refusée.', max_car=300), r['revision']), recharge=True))
        if r['collecteur']:
            corps.append(formulaire('m5689/charge/traces/envoyer', '<input type="hidden" name="cle" value="%s"><input type="hidden" name="heures" value="24">'
                                    '<button class="ax-btn ghost" type="submit">Envoyer les traces des dernières 24 heures au collecteur local</button>' % uuid.uuid4().hex))
    else:
        corps.append('<p class="ax-note">Téléchargement, envoi et réglage des traces : droit « Réglages » du cabinet.</p>')
    return '<div class="t5694" id="traces5694" role="region" aria-labelledby="traces5694-titre">%s</div>' % ''.join(corps)


def section_sure(desk, prefix, ecriture):
    """Jamais d'exception vers la page « Charge du serveur » : un état d'erreur à la place de la sous-section."""
    try:
        return section_html(desk, prefix, ecriture)
    except Exception:   # noqa: BLE001 — la page reste lisible
        return '<div class="t5694" id="traces5694">%s</div>' % etat_html('erreur', 'Traces indisponibles', 'La synthèse des traces n’a pas pu être calculée.')


def telecharger(desk, args):
    try:
        heures = int(str((args or {}).get('heures') or 24))
    except ValueError:
        raise Stop('periode_invalide') from None
    nom, contenu, _ = traces.export(desk, heures)
    return {'status': '200 OK', 'kind': 'application/json; charset=utf-8', 'body': contenu,
            'headers': [('Content-Disposition', 'attachment; filename="%s"' % nom), ('Cache-Control', 'no-store')]}


def ecrire(desk, route, data, owner):
    if route == 'charge/traces/reglage':
        return traces.enregistrer_reglage(desk, data, owner)
    if route == 'charge/traces/envoyer':
        try:
            heures = int(str(data.get('heures') or 24))
        except ValueError:
            raise Stop('periode_invalide') from None
        return traces.envoyer(desk, data.get('cle'), owner, heures)
    raise Stop('route_inconnue')
