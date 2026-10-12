"""5.6.39 : bibliothèque de modèles — droit français (statuts, pactes, LOI, protocoles, cessions, contrats).

Syntaxe des trames (commune à toute la bibliothèque, voir bibliotheque5639) :
  « # Titre »            paragraphe en gras, non numéroté ;
  « ## Intitulé »        article numéroté automatiquement (« Article 3 — Intitulé ») après application des clauses retenues ;
  « - texte »            élément de liste ;
  {{variable}}           donnée de la société, des parties ou de l'opération ;
  [[si cle]] … [[sinon]] … [[fin]]  clause optionnelle (case à cocher), sur des lignes seules ; imbrication possible.

Ces modèles sont des trames de travail à relire et adapter par l'avocat : ils ne remplacent ni l'analyse du dossier, ni la vérification
des textes en vigueur à la date de l'acte. Aucune donnée réelle : les exemples sont fictifs.
"""

FR = 'fr'

# ------------------------------------------------------------------------------------------------ variables communes
V_SOCIETE = [
    ('denomination', 'Dénomination sociale', ''),
    ('siege', 'Adresse du siège social', ''),
    ('capital', 'Montant du capital (en euros, en chiffres)', ''),
    ('nombre_titres', 'Nombre de titres', ''),
    ('nominal', 'Valeur nominale d’un titre (en euros)', ''),
    ('objet', 'Objet social', ''),
    ('duree', 'Durée (années)', '99'),
    ('date_cloture', 'Date de clôture de l’exercice', '31 décembre'),
    ('premier_exercice', 'Date de clôture du premier exercice', ''),
    ('associes', 'Associés et apports (un par ligne : nom, adresse, apport, titres reçus)', ''),
    ('premier_dirigeant', 'Premier dirigeant (nom, adresse)', ''),
    ('rcs_ville', 'Greffe d’immatriculation (ville)', ''),
    ('ville', 'Lieu de signature', ''),
    ('date_acte', 'Date de l’acte', ''),
]
V_PROFESSION = [('profession', 'Profession libérale réglementée exercée', 'avocat'),
                ('autorite', 'Autorité professionnelle compétente (ordre, chambre, conseil)', 'Conseil de l’ordre des avocats du barreau de [À COMPLÉTER]')]

COMMUN_DEBUT = [
    '# {{denomination}}', '{{forme_long}} au capital de {{capital}} euros', 'Siège social : {{siege}}', '', '# STATUTS', '',
    'Le(s) soussigné(s) :', '{{associes}}', '',
    'a (ont) établi ainsi qu’il suit les statuts de la société qu’il(s) constitue(nt) entre eux.', '',
    '## Forme',
    'Il est formé entre les propriétaires des {{titres}} ci-après créé(e)s et de celles et ceux qui pourraient l’être ultérieurement une '
    '{{forme_long}} régie par les lois et règlements en vigueur, notamment {{textes}}, ainsi que par les présents statuts.',
    '## Objet', 'La société a pour objet, en France et à l’étranger : {{objet}}.',
    'Et, plus généralement, toutes opérations, de quelque nature qu’elles soient, se rattachant directement ou indirectement à cet objet ou '
    'susceptibles d’en faciliter le développement ou la réalisation.',
    '## Dénomination', 'La dénomination de la société est : {{denomination}}.',
    'Dans tous les actes et documents émanant de la société et destinés aux tiers, la dénomination est précédée ou suivie immédiatement des '
    'mots « {{forme_long}} » ou des initiales « {{sigle}} » et de l’énoncé du montant du capital social.',
    '## Siège social', 'Le siège social est fixé : {{siege}}.',
    'Il peut être transféré en tout autre endroit du territoire français par décision {{organe_siege}}, qui est alors habilité à modifier les '
    'statuts en conséquence.',
    '## Durée', 'La durée de la société est fixée à {{duree}} années à compter de son immatriculation au registre du commerce et des sociétés, '
    'sauf dissolution anticipée ou prorogation.',
    '## Apports', 'Lors de la constitution, il a été fait apport à la société :', '{{associes}}',
    '[[si apports_nature]]',
    'Les apports en nature ont été évalués au vu du rapport du commissaire aux apports [À COMPLÉTER : nom, date du rapport], annexé aux '
    'présents statuts, ou, à défaut, sous la responsabilité des associés dans les conditions prévues par la loi.',
    '[[fin]]',
    'Les sommes versées en numéraire ont été déposées au crédit d’un compte ouvert au nom de la société en formation auprès de '
    '[À COMPLÉTER : établissement dépositaire], ainsi qu’il résulte du certificat du dépositaire.',
    '## Capital social',
    'Le capital social est fixé à la somme de {{capital}} euros. Il est divisé en {{nombre_titres}} {{titres}} de {{nominal}} euros de valeur '
    'nominale chacune, {{liberation}}, attribuées aux associés en proportion de leurs apports.',
    '## Modification du capital', 'Le capital social peut être augmenté ou réduit dans les conditions prévues par la loi, par décision '
    '{{organe_capital}}.',
    '[[si comptes_courants]]',
    '## Comptes courants d’associés',
    'Chaque associé peut, avec l’accord {{organe_comptes_courants}}, mettre à la disposition de la société toutes sommes dont celle-ci peut avoir '
    'besoin. Les conditions de rémunération et de remboursement de ces avances sont fixées par une convention écrite, soumise le cas échéant à la '
    'procédure applicable aux conventions réglementées.',
    '[[fin]]',
]

COMMUN_FIN = [
    '## Exercice social',
    'L’exercice social commence le lendemain du {{date_cloture}} et se termine le {{date_cloture}} de chaque année. Par exception, le premier '
    'exercice commence au jour de l’immatriculation et sera clos le {{premier_exercice}}.',
    '## Comptes annuels',
    'Il est tenu une comptabilité régulière des opérations sociales. À la clôture de chaque exercice, {{organe_comptes}} établit les comptes '
    'annuels et, s’il y a lieu, le rapport de gestion, qui sont soumis à l’approbation {{organe_approbation}} dans les six mois de la clôture.',
    '## Affectation et répartition des résultats',
    'Sur le bénéfice de l’exercice diminué, le cas échéant, des pertes antérieures, il est prélevé 5 % au moins pour constituer la réserve '
    'légale ; ce prélèvement cesse d’être obligatoire lorsque la réserve atteint le dixième du capital social. Le bénéfice distribuable est '
    'réparti entre les associés proportionnellement à leurs droits dans le capital, sauf stipulation contraire des présents statuts.',
    '[[si commissaire_comptes]]',
    '## Commissaires aux comptes',
    'Un ou plusieurs commissaires aux comptes sont désignés lorsque la loi l’impose, notamment en cas de dépassement des seuils légaux, ou '
    'lorsque les associés le décident. Ils exercent leur mission conformément à la loi.',
    '[[fin]]',
    '## Dissolution — Liquidation',
    'À l’expiration de la société ou en cas de dissolution anticipée, la liquidation est effectuée conformément à la loi. Le boni de liquidation '
    'est réparti entre les associés proportionnellement à leurs droits dans le capital.',
    '## Contestations',
    'Les contestations relatives aux affaires sociales survenant pendant la durée de la société ou de sa liquidation, entre les associés ou '
    'entre la société et les associés, sont soumises aux tribunaux compétents du lieu du siège social.',
    '[[si mediation]]',
    'Préalablement à toute action, les parties s’efforcent de résoudre leur différend par une médiation, confiée à un médiateur désigné d’un '
    'commun accord ou, à défaut, par [À COMPLÉTER : centre de médiation].',
    '[[fin]]',
    '## Nomination du premier dirigeant',
    '{{premier_dirigeant}} est nommé(e) en qualité de {{fonction_dirigeant}} pour une durée {{duree_mandat}}. Il (elle) déclare accepter ces '
    'fonctions et n’être frappé(e) d’aucune incompatibilité ni d’aucune interdiction.',
    '## Actes accomplis pour le compte de la société en formation',
    'L’état des actes accomplis pour le compte de la société en formation, avec l’indication pour chacun d’eux de l’engagement qui en résulte, '
    'est annexé aux présents statuts. La signature des statuts emporte reprise de ces engagements par la société lorsqu’elle aura été '
    'immatriculée.',
    '## Pouvoirs — Frais',
    'Tous pouvoirs sont donnés au porteur d’un original ou d’une copie des présentes pour accomplir les formalités de publicité, de dépôt et '
    'd’immatriculation auprès du guichet unique des formalités des entreprises. Les frais de constitution sont repris par la société.',
    '',
    'Fait à {{ville}}, le {{date_acte}}, en [À COMPLÉTER] originaux.', '',
    'Signature de chaque associé, précédée de la mention manuscrite « Bon pour acceptation des fonctions » pour le dirigeant.',
]

# ------------------------------------------------------------------------------------------------ clauses de transmission (titres)
TRANSMISSION_ACTIONS = [
    '## Forme et transmission des actions',
    'Les actions sont nominatives. Elles sont inscrites en compte au nom de leur titulaire sur un registre des mouvements de titres tenu par la '
    'société. La transmission des actions s’opère par virement de compte à compte sur production d’un ordre de mouvement signé du cédant ou de '
    'son mandataire.',
    'Au sens des présents statuts, le terme « cession » vise toute opération, à titre onéreux ou gratuit, entraînant le transfert de la propriété '
    'ou d’un démembrement de la propriété des actions ou de valeurs mobilières donnant accès au capital, y compris par voie d’apport, de fusion, '
    'de scission, d’échange ou de vente aux enchères.',
    '[[si inalienabilite]]',
    '## Inaliénabilité temporaire',
    'Conformément à l’article L. 227-13 du Code de commerce, les actions souscrites lors de la constitution ne peuvent faire l’objet d’aucune '
    'cession pendant une durée de {{duree_inalienabilite}} à compter de l’immatriculation de la société, sauf accord unanime des associés. Toute '
    'cession effectuée en violation de cette clause est nulle. Cette clause ne peut être adoptée ou modifiée qu’à l’unanimité des associés '
    '(article L. 227-19 du Code de commerce).',
    '[[fin]]',
    '[[si preemption]]',
    '## Droit de préemption',
    'Toute cession d’actions à quelque personne que ce soit, y compris entre associés, est soumise au droit de préemption des autres associés. '
    'Le cédant notifie au Président et à chaque associé, par lettre recommandée avec demande d’avis de réception ou par courrier électronique '
    'avec accusé de réception, le nombre d’actions dont la cession est envisagée, l’identité du cessionnaire, le prix et les conditions de la '
    'cession.',
    'Chaque associé dispose d’un délai de {{delai_preemption}} jours à compter de la notification pour exercer son droit de préemption, au prix '
    'et aux conditions notifiés. Si les demandes excèdent le nombre d’actions offertes, celles-ci sont réparties entre les associés '
    'préempteurs au prorata de leur participation, dans la limite de leurs demandes. Le droit de préemption doit porter sur la totalité des '
    'actions offertes ; à défaut, le cédant est libre de céder au cessionnaire notifié, sous réserve, le cas échéant, de la procédure d’agrément.',
    '[[fin]]',
    '[[si agrement]]',
    '## Agrément des cessions',
    'Conformément à l’article L. 227-14 du Code de commerce, toute cession d’actions{{perimetre_agrement}} est soumise à l’agrément préalable de la '
    'collectivité des associés statuant à la majorité de {{majorite_agrement}} des voix des associés disposant du droit de vote, le cédant prenant '
    'part au vote.',
    'La demande d’agrément, notifiée au Président, indique les nom, prénoms et adresse (ou dénomination et siège) du cessionnaire, le nombre '
    'd’actions dont la cession est envisagée et le prix offert. La décision est notifiée au cédant dans un délai de trois mois à compter de la '
    'demande ; à défaut, l’agrément est réputé acquis.',
    'En cas de refus d’agrément, la société est tenue, dans un délai de trois mois à compter de la notification du refus, d’acquérir ou de faire '
    'acquérir les actions par un ou plusieurs associés ou tiers agréés, à un prix fixé d’un commun accord ou, à défaut, dans les conditions de '
    'l’article 1843-4 du Code civil. Le cédant peut renoncer à la cession. Si l’achat n’est pas réalisé dans le délai, l’agrément est réputé '
    'donné.',
    '[[fin]]',
    '[[si sortie_conjointe]]',
    '## Droit de sortie conjointe',
    'Si un ou plusieurs associés (le « Cédant ») envisagent de céder des actions ayant pour effet de transférer à un tiers ou à un associé '
    '{{seuil_sortie_conjointe}} du capital ou des droits de vote, chacun des autres associés bénéficie d’un droit de sortie conjointe lui '
    'permettant de céder au cessionnaire, aux mêmes prix et conditions, {{mode_sortie_conjointe}}.',
    'Le Cédant notifie le projet aux autres associés, qui disposent d’un délai de {{delai_sortie_conjointe}} jours pour faire connaître leur '
    'décision. Le Cédant ne peut réaliser la cession que si le cessionnaire acquiert également les actions des associés ayant exercé leur droit ; '
    'à défaut, la cession est inopposable à la société et le Président refuse l’inscription du transfert.',
    '[[fin]]',
    '[[si sortie_forcee]]',
    '## Obligation de sortie forcée',
    'Si un ou plusieurs associés détenant ensemble au moins {{seuil_sortie_forcee}} du capital et des droits de vote (les « Associés '
    'majoritaires ») acceptent une offre de bonne foi d’un tiers portant sur 100 % des actions de la société, ils peuvent exiger des autres '
    'associés qu’ils cèdent leurs actions à ce tiers, aux mêmes prix et conditions. La notification indique l’identité du tiers, le prix, les '
    'conditions et les garanties demandées, qui ne peuvent excéder, pour les associés minoritaires, la garantie de propriété et de capacité.',
    'Faute pour un associé de signer l’ordre de mouvement dans le délai de {{delai_sortie_forcee}} jours de la notification, le Président est '
    'habilité à inscrire le transfert au registre, le prix étant consigné à la disposition de l’associé défaillant. [À VÉRIFIER : opportunité '
    'de doubler cette clause d’une promesse de vente dans le pacte d’associés.]',
    '[[fin]]',
    '[[si changement_controle]]',
    '## Changement de contrôle d’un associé',
    'Conformément à l’article L. 227-17 du Code de commerce, l’associé personne morale dont le contrôle, au sens de l’article L. 233-3 du même '
    'code, vient à être modifié doit en informer le Président dans un délai de quinze jours. La collectivité des associés, statuant à la '
    'majorité de {{majorite_exclusion}} des voix, l’associé concerné ne prenant pas part au vote, peut décider dans un délai de trois mois de '
    'suspendre l’exercice de ses droits non pécuniaires et de l’exclure dans les conditions de l’article relatif à l’exclusion. Cette clause ne '
    'peut être adoptée ou modifiée qu’à l’unanimité des associés.',
    '[[fin]]',
    '[[si exclusion]]',
    '## Exclusion',
    'Conformément à l’article L. 227-16 du Code de commerce, peut être exclu tout associé : en cas de violation grave des statuts ou du pacte '
    'd’associés, de condamnation pénale définitive pour un crime ou un délit contre les biens, d’ouverture d’une procédure collective à son '
    'égard ou de cessation de toute fonction salariée ou de mandataire social au sein de la société ou d’une société qu’elle contrôle '
    '[À COMPLÉTER : causes retenues].',
    'L’exclusion est prononcée par la collectivité des associés statuant à la majorité de {{majorite_exclusion}} des voix des associés '
    'autres que l’associé concerné, après que celui-ci a été mis en mesure de présenter ses observations, avec un préavis d’au moins quinze '
    'jours. Les actions de l’associé exclu sont cédées dans un délai de {{delai_exclusion}} jours à toute personne désignée par la décision, à '
    'un prix fixé d’un commun accord ou, à défaut, dans les conditions de l’article 1843-4 du Code civil.',
    '[[fin]]',
    '[[si actions_preference]]',
    '## Actions de préférence',
    'La société peut créer des actions de préférence, avec ou sans droit de vote, assorties de droits particuliers de toute nature, à titre '
    'temporaire ou permanent, dans les conditions prévues aux articles L. 228-11 et suivants du Code de commerce. Les droits particuliers de '
    'chaque catégorie sont définis par la décision d’émission et intégrés aux présents statuts. [À COMPLÉTER : catégories et droits attachés]',
    '[[fin]]',
    '[[si dilution]]',
    '## Droit préférentiel et protection contre la dilution',
    'En cas d’augmentation de capital en numéraire, les actions sont réservées par préférence aux associés proportionnellement au nombre de '
    'leurs actions, dans les conditions légales. Toute suppression du droit préférentiel de souscription requiert une décision collective des '
    'associés prise à la majorité de {{majorite_extraordinaire}} des voix.',
    '[[fin]]',
]

DROITS_ACTIONS = [
    '## Droits et obligations attachés aux actions',
    'Chaque action donne droit, dans la propriété de l’actif social, dans le partage des bénéfices et dans le boni de liquidation, à une part '
    'proportionnelle à la quotité du capital qu’elle représente, et donne droit à une voix lors des décisions collectives. Les associés ne '
    'supportent les pertes qu’à concurrence de leurs apports. La propriété d’une action emporte adhésion aux statuts et aux décisions '
    'régulièrement prises par les associés.',
    '## Indivisibilité des actions',
    'Les actions sont indivisibles à l’égard de la société. Les copropriétaires indivis sont représentés par un mandataire unique. Le droit de '
    'vote appartient à l’usufruitier pour les décisions concernant l’affectation des bénéfices et au nu-propriétaire pour toutes les autres '
    'décisions, sauf convention contraire notifiée à la société.',
]


def _sas(unipersonnelle=False, selas=False):
    """Statuts de SAS, SASU ou SELAS : président obligatoire, organes et majorités librement fixés, clauses propres aux SAS."""
    lines = list(COMMUN_DEBUT) + list(DROITS_ACTIONS)
    lines += [
        '## Président',
        'La société est représentée, dirigée et administrée par un Président, personne physique ou morale, associée ou non'
        + (', choisie parmi les associés exerçant la profession de {{profession}} au sein de la société' if selas else '') + '. Le Président est '
        'nommé {{mode_nomination_president}} pour une durée {{duree_mandat}}. Il peut être révoqué à tout moment par décision {{organe_revocation}}.',
        'Le Président est investi des pouvoirs les plus étendus pour agir en toutes circonstances au nom de la société, dans la limite de l’objet '
        'social et des pouvoirs expressément attribués par la loi et les présents statuts aux associés.',
        '[[si limitation_pouvoirs]]',
        'À titre de mesure d’ordre interne inopposable aux tiers, le Président ne peut, sans l’autorisation préalable {{organe_autorisation}}, '
        'réaliser les opérations suivantes : [À COMPLÉTER : acquisition ou cession de fonds ou de participations, emprunts au-delà de '
        '[montant], sûretés, recrutement au-delà de [montant], budget annuel].',
        '[[fin]]',
        '[[si directeur_general]]',
        '## Directeurs généraux',
        'Sur proposition du Président, la collectivité des associés peut nommer un ou plusieurs directeurs généraux, personnes physiques'
        + (' exerçant la profession au sein de la société' if selas else '') + ', disposant à l’égard des tiers des mêmes pouvoirs que le '
        'Président (article L. 227-6 du Code de commerce). La décision fixe la durée de leurs fonctions et leur rémunération.',
        '[[fin]]',
        '[[si comite_strategique]]',
        '## Comité stratégique',
        'Il est institué un comité stratégique composé de {{membres_comite}} membres, nommés par la collectivité des associés. Le comité donne un '
        'avis préalable sur les décisions importantes énumérées par son règlement intérieur ; il se réunit au moins {{reunions_comite}} fois par '
        'an. Ses membres sont tenus à une obligation de confidentialité.',
        '[[fin]]',
        '## Conventions réglementées',
        'Les conventions intervenues directement ou par personne interposée entre la société et son Président, l’un de ses dirigeants, l’un de ses '
        'associés disposant d’une fraction des droits de vote supérieure à 10 % ou, s’il s’agit d’une société associée, la société la contrôlant, '
        'sont soumises à la procédure prévue à l’article L. 227-10 du Code de commerce.',
    ]
    if unipersonnelle:
        lines += [
            '## Décisions de l’associé unique',
            'L’associé unique exerce les pouvoirs dévolus par la loi à la collectivité des associés ; il ne peut déléguer ces pouvoirs. Ses '
            'décisions sont répertoriées dans un registre coté et paraphé (article L. 227-9 du Code de commerce). Il approuve les comptes '
            'annuels dans les six mois de la clôture.',
            '[[si agrement]]',
            '## Pluralité d’associés',
            'En cas de pluralité d’associés, les décisions collectives sont prises en assemblée ou par consultation écrite, à la majorité des voix '
            'des associés présents ou représentés pour les décisions ordinaires et des deux tiers pour les décisions extraordinaires, et les '
            'clauses de transmission ci-après s’appliquent.',
            '[[fin]]',
        ]
    else:
        lines += [
            '## Décisions collectives',
            'Relèvent de la compétence de la collectivité des associés : l’augmentation, l’amortissement ou la réduction du capital, la fusion, '
            'la scission, la dissolution, la transformation, la nomination des commissaires aux comptes, l’approbation des comptes annuels et '
            'l’affectation des résultats, la nomination et la révocation du Président, la modification des statuts et toute décision que la loi '
            'ou les présents statuts lui réservent (article L. 227-9 du Code de commerce).',
            'Les décisions collectives sont prises, au choix de l’auteur de la convocation, en assemblée, y compris par des moyens de '
            'visioconférence ou de télécommunication permettant l’identification des associés, par consultation écrite ou électronique, ou '
            'dans un acte signé de tous les associés. L’assemblée est convoquée au moins {{delai_convocation}} jours à l’avance.',
            'Les décisions ordinaires sont adoptées à la majorité de {{majorite_ordinaire}} des voix des associés présents ou représentés ; les '
            'décisions extraordinaires, notamment toute modification des statuts, à la majorité de {{majorite_extraordinaire}} des voix. '
            'Sont prises à l’unanimité les décisions que la loi soumet à cette règle, notamment l’augmentation des engagements des associés '
            '(article 1836 du Code civil) et l’adoption ou la modification des clauses visées aux articles L. 227-13 et L. 227-17 du Code de '
            'commerce.',
            'Les décisions sont constatées par des procès-verbaux établis sur un registre spécial ou sur des feuillets mobiles numérotés.',
        ]
    if selas:
        lines += [
            '## Exercice de la profession',
            'Plus de la moitié du capital social et des droits de vote est détenue, directement ou par l’intermédiaire d’une société de '
            'participations financières de professions libérales, par des professionnels exerçant au sein de la société, sous réserve des '
            'dispositions propres à la profession (article 46 de l’ordonnance n° 2023-77 du 8 février 2023). Chaque associé professionnel exerce '
            'en toute indépendance, dans le respect des règles déontologiques de la profession de {{profession}}.',
            'La société ne peut être immatriculée qu’après son inscription ou son agrément par {{autorite}}. Toute cession ou modification ayant '
            'pour effet de faire perdre à la société le respect des conditions de détention du capital doit être régularisée dans le délai prévu '
            'par les textes applicables à la profession.',
        ]
    lines += TRANSMISSION_ACTIONS + COMMUN_FIN
    return lines


def _sarl(unipersonnelle=False, selarl=False):
    lines = list(COMMUN_DEBUT)
    lines += [
        '## Parts sociales',
        'Les parts sociales ne peuvent être représentées par des titres négociables. Chaque part donne droit à une fraction proportionnelle des '
        'bénéfices et de l’actif social et à une voix. La responsabilité des associés est limitée au montant de leurs apports.',
        '## Gérance',
        'La société est gérée par un ou plusieurs gérants, personnes physiques, associés ou non'
        + (', choisis parmi les associés exerçant la profession de {{profession}} au sein de la société' if selarl else '') + ', nommés '
        'par décision {{organe_nomination_gerant}} pour une durée {{duree_mandat}}. Dans les rapports avec les tiers, le gérant est investi des '
        'pouvoirs les plus étendus pour agir au nom de la société, dans la limite de l’objet social (article L. 223-18 du Code de commerce).',
        '[[si limitation_pouvoirs]]',
        'À titre de mesure d’ordre interne inopposable aux tiers, le gérant ne peut, sans autorisation préalable des associés statuant à la '
        'majorité ordinaire, réaliser les opérations suivantes : [À COMPLÉTER].',
        '[[fin]]',
        'Le gérant est révocable par décision des associés représentant plus de la moitié des parts sociales ; la révocation sans juste motif peut '
        'donner lieu à dommages-intérêts.',
        '## Conventions réglementées',
        'Les conventions entre la société et l’un de ses gérants ou associés sont soumises à la procédure prévue à l’article L. 223-19 du Code de '
        'commerce.',
    ]
    if unipersonnelle:
        lines += [
            '## Décisions de l’associé unique',
            'L’associé unique exerce les pouvoirs dévolus à l’assemblée des associés. Ses décisions sont répertoriées dans un registre (article '
            'L. 223-31 du Code de commerce). Lorsque l’associé unique est seul gérant, le dépôt au registre du commerce et des sociétés de '
            'l’inventaire et des comptes annuels dûment signés vaut approbation des comptes, dans les conditions prévues par ce texte.',
        ]
    else:
        lines += [
            '## Décisions collectives',
            'Les décisions collectives sont prises en assemblée, ou par consultation écrite lorsque les statuts le permettent pour les décisions '
            'autres que l’approbation des comptes, ou par acte signé de tous les associés. L’assemblée peut se tenir par visioconférence dans les '
            'conditions légales.',
            'Les décisions ordinaires sont adoptées par un ou plusieurs associés représentant plus de la moitié des parts sociales ; à défaut, sur '
            'seconde consultation, à la majorité des votes émis (article L. 223-29 du Code de commerce).',
            'Les décisions modifiant les statuts sont adoptées par les associés représentant au moins les deux tiers des parts détenues par les '
            'associés présents ou représentés, l’assemblée ne délibérant valablement que si les associés présents ou représentés possèdent au '
            'moins, sur première convocation, le quart des parts et, sur deuxième convocation, le cinquième (article L. 223-30 du Code de '
            'commerce).',
        ]
    lines += [
        '## Cession et transmission des parts',
        'Toute cession de parts est constatée par écrit. Elle est rendue opposable à la société par sa signification ou par son acceptation dans un '
        'acte authentique, ou par le dépôt d’un original de l’acte au siège social contre remise d’une attestation du gérant ; elle n’est '
        'opposable aux tiers qu’après accomplissement de ces formalités et publicité au registre du commerce et des sociétés.',
        '[[si agrement]]',
        'Les parts ne peuvent être cédées à des tiers étrangers à la société qu’avec le consentement de la majorité des associés représentant au '
        'moins {{majorite_agrement_sarl}} des parts sociales, dans les conditions et délais prévus à l’article L. 223-14 du Code de commerce.',
        '[[fin]]',
        '[[si agrement_associes]]',
        'Par dérogation à la libre cessibilité entre associés, les cessions entre associés sont également soumises à agrément dans les mêmes '
        'conditions (article L. 223-16 du Code de commerce).',
        '[[fin]]',
        '[[si agrement_famille]]',
        'Les cessions au profit du conjoint, d’ascendants ou de descendants du cédant sont soumises à agrément dans les mêmes conditions (article '
        'L. 223-13 du Code de commerce).',
        '[[fin]]',
        '[[si agrement_heritiers]]',
        'En cas de décès d’un associé, la société continue avec ses héritiers ou ayants droit, sous réserve de leur agrément par les associés dans '
        'les conditions ci-dessus.',
        '[[sinon]]',
        'En cas de décès d’un associé, la société continue de plein droit avec ses héritiers et ayants droit.',
        '[[fin]]',
    ]
    if selarl:
        lines += [
            '## Exercice de la profession',
            'Plus de la moitié du capital social et des droits de vote est détenue, directement ou par l’intermédiaire d’une société de '
            'participations financières de professions libérales, par des professionnels exerçant au sein de la société, sous réserve des '
            'dispositions propres à la profession (article 46 de l’ordonnance n° 2023-77 du 8 février 2023). Les gérants sont choisis parmi '
            'ces professionnels. La société ne peut être immatriculée qu’après son inscription ou son agrément par {{autorite}}.',
        ]
    lines += COMMUN_FIN
    return lines


def _societe_civile(kind):
    """SCI, SCM, SCP : sociétés civiles (articles 1832 et suivants, 1845 et suivants du Code civil)."""
    lines = list(COMMUN_DEBUT)
    if kind == 'SCI':
        lines += _sci_specific()
    if kind == 'SCM':
        lines += ['## Objet exclusif — Moyens communs',
                  'Conformément à l’article 38 de l’ordonnance n° 2023-77 du 8 février 2023, la société a pour objet exclusif de faciliter à chacun '
                  'de ses membres l’exercice de son activité professionnelle de {{profession}}, par la mise en commun des moyens utiles à cet '
                  'exercice (locaux, matériel, personnel, services), sans pouvoir elle-même exercer la profession. Chaque associé conserve sa '
                  'clientèle, son indépendance et sa responsabilité professionnelle.',
                  '## Répartition des charges',
                  'Les dépenses de fonctionnement sont réparties entre les associés selon la clé suivante : {{cle_repartition}}. Chaque associé '
                  'verse une provision mensuelle ; une régularisation est opérée à la clôture de l’exercice.']
    if kind == 'SCP':
        lines += ['## Exercice en commun de la profession',
                  'Conformément aux articles 5 et suivants de l’ordonnance n° 2023-77 du 8 février 2023, la société a pour objet l’exercice en '
                  'commun de la profession de {{profession}} par ses membres, personnes physiques exerçant cette profession. Elle ne peut être '
                  'immatriculée qu’après son agrément ou son inscription par {{autorite}}. Chaque associé consacre à la société toute son activité '
                  'professionnelle.',
                  '[[si parts_industrie]]',
                  'Les apports en industrie ne concourent pas à la formation du capital mais donnent lieu à l’attribution de parts ouvrant droit '
                  'au partage des bénéfices et à la participation aux décisions collectives, selon les modalités suivantes : [À COMPLÉTER].',
                  '[[fin]]']
    lines += [
        '## Responsabilité des associés',
        ('Les associés répondent indéfiniment des dettes sociales à l’égard des tiers ; les créanciers ne peuvent poursuivre un associé qu’après '
         'avoir vainement mis en demeure la société et à la condition de la mettre en cause (article 19 de l’ordonnance n° 2023-77 du 8 février '
         '2023). Chaque associé répond, sur l’ensemble de son patrimoine, des actes professionnels qu’il accomplit.' if kind == 'SCP' else
         'À l’égard des tiers, les associés répondent indéfiniment des dettes sociales à proportion de leur part dans le capital à la date de '
         'l’exigibilité ou au jour de la cessation des paiements (article 1857 du Code civil). Les créanciers ne peuvent poursuivre le paiement '
         'contre un associé qu’après avoir préalablement et vainement poursuivi la société (article 1858 du Code civil).'),
        '## Gérance',
        'La société est administrée par un ou plusieurs gérants, associés ou non' + (', choisis parmi les associés' if kind == 'SCP' else '')
        + ', nommés par décision des associés représentant plus de la moitié des parts sociales. Dans les rapports avec les tiers, le gérant '
        'engage la société par les actes entrant dans l’objet social (article 1849 du Code civil). Il est révocable dans les conditions de '
        'l’article 1851 du Code civil.',
        '[[si limitation_pouvoirs]]',
        'Dans les rapports entre associés, le gérant ne peut, sans l’accord préalable des associés statuant à la majorité de {{majorite_ordinaire}} '
        'des parts, acquérir, vendre, échanger ou hypothéquer un immeuble, consentir une sûreté, ni contracter un emprunt supérieur à '
        '[À COMPLÉTER : montant].',
        '[[fin]]',
        '## Décisions collectives',
        'Les décisions collectives sont prises en assemblée ou par consultation écrite. Les décisions ordinaires sont adoptées à la majorité de '
        '{{majorite_ordinaire}} des parts sociales ; les décisions modifiant les statuts à la majorité de {{majorite_extraordinaire}} des parts, '
        'l’unanimité restant requise pour augmenter les engagements des associés (article 1836 du Code civil). Les gérants rendent compte de leur '
        'gestion au moins une fois par an par un rapport écrit (article 1856 du Code civil).',
        '[[si demembrement]]',
        '## Parts démembrées',
        'En cas de démembrement, le droit de vote appartient au nu-propriétaire, sauf pour les décisions concernant l’affectation des bénéfices où '
        'il est réservé à l’usufruitier ; l’usufruitier et le nu-propriétaire ont le droit de participer aux décisions collectives.',
        '[[fin]]',
        '## Cession et transmission des parts',
        'La cession de parts est constatée par écrit et rendue opposable à la société dans les formes de l’article 1690 du Code civil ou par '
        'transfert sur les registres de la société ; elle n’est opposable aux tiers qu’après publication au registre du commerce et des sociétés '
        '(article 1865 du Code civil).',
        '[[si agrement]]',
        'Les parts ne peuvent être cédées, y compris entre associés' + (' et au profit des ascendants ou descendants' if kind != 'SCP' else '')
        + ', qu’avec l’agrément des associés représentant {{majorite_agrement_civile}} des parts sociales (article 1861 du Code civil). Le '
        'projet de cession est notifié à la société et à chaque associé. En cas de refus, les articles 1862 et 1863 du Code civil s’appliquent.',
        '[[fin]]',
        '[[si agrement_heritiers]]',
        'En cas de décès d’un associé, la société continue avec ses héritiers ou légataires, sous réserve de leur agrément dans les conditions '
        'ci-dessus (article 1870 du Code civil).',
        '[[fin]]',
    ]
    if kind in ('SCM', 'SCP'):
        lines += ['## Retrait — Exclusion',
                  'Tout associé peut se retirer de la société moyennant un préavis de {{preavis_retrait}} mois, notifié par lettre recommandée, ses '
                  'parts étant rachetées par la société ou les associés à leur valeur fixée d’un commun accord ou, à défaut, dans les conditions de '
                  'l’article 1843-4 du Code civil.',
                  '[[si exclusion]]',
                  'Peut être exclu, par décision des autres associés statuant à la majorité de {{majorite_extraordinaire}}, l’associé qui cesse '
                  'd’exercer sa profession, fait l’objet d’une sanction disciplinaire définitive emportant interdiction d’exercer, ou manque '
                  'gravement à ses obligations envers la société, après avoir été mis en mesure de présenter ses observations.',
                  '[[fin]]']
    lines += COMMUN_FIN
    return lines


def _sci_specific():
    return ['## Patrimoine social',
            'Le patrimoine social comprend notamment l’immeuble situé {{immeuble}}. La société a vocation à acquérir, gérer, administrer et donner '
            'en location, à l’exclusion de la location meublée habituelle, tous biens et droits immobiliers, et exceptionnellement à les aliéner, '
            'sans faire acte de commerce.']


COMPANY_CLAUSES_SAS = [
    ('agrement', 'Clause d’agrément (article L. 227-14 C. com.)', True, 'Les cessions sont soumises à l’accord des associés.'),
    ('preemption', 'Droit de préemption des associés', True, 'Les associés sont prioritaires pour racheter les actions cédées.'),
    ('sortie_conjointe', 'Droit de sortie conjointe (tag-along)', False, 'Les minoritaires peuvent sortir aux mêmes conditions que le cédant.'),
    ('sortie_forcee', 'Obligation de sortie forcée (drag-along)', False, 'Les majoritaires peuvent imposer la cession de 100 % des titres.'),
    ('inalienabilite', 'Inaliénabilité temporaire (L. 227-13, 10 ans au plus)', False, 'Adoption à l’unanimité.'),
    ('exclusion', 'Clause d’exclusion (L. 227-16)', False, 'Causes à définir précisément.'),
    ('changement_controle', 'Changement de contrôle d’un associé personne morale (L. 227-17)', False, 'Adoption à l’unanimité.'),
    ('actions_preference', 'Actions de préférence (L. 228-11)', False, ''),
    ('dilution', 'Droit préférentiel renforcé', False, ''),
    ('directeur_general', 'Directeur(s) général(aux)', False, ''),
    ('comite_strategique', 'Comité stratégique', False, ''),
    ('limitation_pouvoirs', 'Décisions importantes soumises à autorisation', True, ''),
    ('comptes_courants', 'Comptes courants d’associés', True, ''),
    ('apports_nature', 'Apports en nature', False, ''),
    ('commissaire_comptes', 'Article sur le commissaire aux comptes', True, ''),
    ('mediation', 'Médiation préalable', False, ''),
]
V_SAS = [
    ('mode_nomination_president', 'Mode de nomination du Président', 'par décision collective des associés statuant à la majorité ordinaire'),
    ('organe_revocation', 'Organe de révocation du Président', 'de la collectivité des associés statuant à la majorité ordinaire'),
    ('organe_autorisation', 'Organe d’autorisation des décisions importantes', 'de la collectivité des associés'),
    ('duree_mandat', 'Durée du mandat du dirigeant', 'indéterminée'),
    ('delai_convocation', 'Délai de convocation (jours)', '8'),
    ('majorite_ordinaire', 'Majorité ordinaire', 'plus de la moitié'),
    ('majorite_extraordinaire', 'Majorité extraordinaire', 'deux tiers'),
    ('majorite_agrement', 'Majorité d’agrément', 'deux tiers'),
    ('perimetre_agrement', 'Périmètre de l’agrément', ', y compris entre associés,'),
    ('delai_preemption', 'Délai de préemption (jours)', '30'),
    ('seuil_sortie_conjointe', 'Seuil déclenchant la sortie conjointe', 'le contrôle, au sens de l’article L. 233-3 du Code de commerce,'),
    ('mode_sortie_conjointe', 'Étendue de la sortie conjointe', 'la totalité de ses actions'),
    ('delai_sortie_conjointe', 'Délai d’exercice de la sortie conjointe (jours)', '30'),
    ('seuil_sortie_forcee', 'Seuil de la sortie forcée', '75 %'),
    ('delai_sortie_forcee', 'Délai de signature en sortie forcée (jours)', '30'),
    ('duree_inalienabilite', 'Durée d’inaliénabilité', 'cinq ans'),
    ('majorite_exclusion', 'Majorité d’exclusion', 'deux tiers'),
    ('delai_exclusion', 'Délai de rachat des actions de l’exclu (jours)', '60'),
    ('membres_comite', 'Nombre de membres du comité', '3'),
    ('reunions_comite', 'Réunions annuelles du comité', '4'),
    ('liberation', 'Libération des actions', 'intégralement souscrites et libérées'),
]
COMMON_SAS_FIXED = {'titres': 'actions', 'organe_siege': 'du Président', 'organe_capital': 'collective des associés',
                    'organe_comptes_courants': 'du Président', 'organe_comptes': 'le Président',
                    'organe_approbation': 'de la collectivité des associés', 'fonction_dirigeant': 'Président'}
V_SARL = [
    ('organe_nomination_gerant', 'Organe de nomination du gérant', 'des associés représentant plus de la moitié des parts sociales'),
    ('duree_mandat', 'Durée du mandat du gérant', 'indéterminée'),
    ('majorite_agrement_sarl', 'Majorité d’agrément (au moins la moitié des parts)', 'la moitié'),
    ('liberation', 'Libération des parts', 'intégralement souscrites et libérées'),
]
COMMON_SARL_FIXED = {'titres': 'parts sociales', 'organe_siege': 'du gérant, sous réserve de ratification par les associés',
                     'organe_capital': 'des associés statuant dans les conditions de modification des statuts', 'organe_comptes_courants': 'du gérant',
                     'organe_comptes': 'le gérant', 'organe_approbation': 'des associés', 'fonction_dirigeant': 'gérant'}
CLAUSES_SARL = [
    ('agrement', 'Agrément des cessions à des tiers (L. 223-14)', True, ''),
    ('agrement_associes', 'Agrément des cessions entre associés', False, ''),
    ('agrement_famille', 'Agrément des cessions au conjoint et à la famille', False, ''),
    ('agrement_heritiers', 'Agrément des héritiers', False, ''),
    ('limitation_pouvoirs', 'Décisions importantes soumises à autorisation', True, ''),
    ('comptes_courants', 'Comptes courants d’associés', True, ''),
    ('apports_nature', 'Apports en nature', False, ''),
    ('commissaire_comptes', 'Article sur le commissaire aux comptes', True, ''),
    ('mediation', 'Médiation préalable', False, ''),
]
V_CIVILE = [
    ('majorite_ordinaire', 'Majorité des décisions ordinaires', 'plus de la moitié'),
    ('majorite_extraordinaire', 'Majorité des modifications statutaires', 'trois quarts'),
    ('majorite_agrement_civile', 'Majorité d’agrément', 'l’unanimité'),
    ('duree_mandat', 'Durée du mandat du gérant', 'indéterminée'),
    ('liberation', 'Libération des parts', 'intégralement souscrites et libérées'),
]
COMMON_CIVILE_FIXED = {'titres': 'parts sociales', 'organe_siege': 'des associés statuant à la majorité requise pour modifier les statuts',
                       'organe_capital': 'des associés statuant à la majorité requise pour modifier les statuts', 'organe_comptes_courants': 'du gérant',
                       'organe_comptes': 'le gérant', 'organe_approbation': 'des associés', 'fonction_dirigeant': 'gérant'}
CLAUSES_CIVILE = [
    ('agrement', 'Agrément des cessions (article 1861 C. civ.)', True, ''),
    ('agrement_heritiers', 'Agrément des héritiers (article 1870 C. civ.)', True, ''),
    ('limitation_pouvoirs', 'Décisions importantes soumises à autorisation', True, ''),
    ('comptes_courants', 'Comptes courants d’associés', True, ''),
    ('apports_nature', 'Apports en nature', False, ''),
    ('commissaire_comptes', 'Article sur le commissaire aux comptes', False, ''),
    ('mediation', 'Médiation préalable', False, ''),
]


def _statuts(ident, forme, forme_long, sigle, textes, lines, variables, clauses, fixed, resume, mots):
    return {'id': ident, 'langue': FR, 'droit': 'FR', 'categorie': 'statuts', 'formes': (forme,), 'titre': 'Statuts de %s' % forme,
            'resume': resume, 'mots': mots, 'variables': V_SOCIETE + variables, 'clauses': clauses,
            'fixes': {**fixed, 'forme_long': forme_long, 'sigle': sigle, 'textes': textes}, 'texte': lines}


_R = lambda fr, en, de, es: {'fr': fr, 'en': en, 'de': de, 'es': es}   # noqa: E731

TEMPLATES = [
    _statuts('statuts_sas', 'SAS', 'société par actions simplifiée', 'SAS', 'les articles L. 227-1 et suivants du Code de commerce', _sas(),
             V_SAS, COMPANY_CLAUSES_SAS, COMMON_SAS_FIXED,
             _R('Statuts de SAS avec clauses d’agrément, de préemption, de sortie conjointe et forcée, d’exclusion, d’inaliénabilité…',
                'French SAS articles with approval, pre-emption, tag-along, drag-along, exclusion and lock-up clauses.',
                'Satzung einer französischen SAS mit Zustimmungs-, Vorkaufs-, Mitveräußerungs- und Mitverkaufsklauseln.',
                'Estatutos de SAS francesa con cláusulas de aprobación, adquisición preferente, acompañamiento y arrastre.'),
             ('statuts sas', 'société par actions simplifiée', 'sas')),
    _statuts('statuts_sasu', 'SASU', 'société par actions simplifiée unipersonnelle', 'SASU', 'les articles L. 227-1 et suivants du Code de commerce',
             _sas(unipersonnelle=True), V_SAS, [c for c in COMPANY_CLAUSES_SAS if c[0] in (
                 'agrement', 'preemption', 'directeur_general', 'limitation_pouvoirs', 'comptes_courants', 'apports_nature', 'commissaire_comptes',
                 'actions_preference')], COMMON_SAS_FIXED,
             _R('Statuts de SASU (associé unique, registre des décisions), clauses activables en cas d’arrivée d’associés.',
                'French single-shareholder SAS articles.', 'Satzung einer französischen Einpersonen-SAS.', 'Estatutos de SASU francesa (socio único).'),
             ('statuts sasu', 'sasu', 'associé unique sas')),
    _statuts('statuts_selas', 'SELAS', 'société d’exercice libéral par actions simplifiée', 'SELAS',
             'l’ordonnance n° 2023-77 du 8 février 2023, les articles L. 227-1 et suivants du Code de commerce et les règles de la profession',
             _sas(selas=True), V_SAS + V_PROFESSION, COMPANY_CLAUSES_SAS, COMMON_SAS_FIXED,
             _R('Statuts de SELAS (profession libérale réglementée, ordonnance du 8 février 2023) avec clauses propres aux SAS.',
                'French SELAS (regulated profession) articles.', 'Satzung einer französischen SELAS (freier Beruf).', 'Estatutos de SELAS francesa (profesión regulada).'),
             ('statuts selas', 'selas', 'société d’exercice libéral par actions')),
    _statuts('statuts_sarl', 'SARL', 'société à responsabilité limitée', 'SARL', 'les articles L. 223-1 et suivants du Code de commerce', _sarl(),
             V_SARL, CLAUSES_SARL, COMMON_SARL_FIXED,
             _R('Statuts de SARL : gérance, majorités légales, agrément des cessions (L. 223-14), héritiers.',
                'French SARL articles.', 'Satzung einer französischen SARL.', 'Estatutos de SARL francesa.'),
             ('statuts sarl', 'société à responsabilité limitée', 'sarl')),
    _statuts('statuts_eurl', 'EURL', 'entreprise unipersonnelle à responsabilité limitée', 'EURL', 'les articles L. 223-1 et suivants du Code de commerce',
             _sarl(unipersonnelle=True), V_SARL, CLAUSES_SARL, COMMON_SARL_FIXED,
             _R('Statuts d’EURL (associé unique, registre des décisions).', 'French single-member SARL (EURL) articles.',
                'Satzung einer französischen EURL.', 'Estatutos de EURL francesa.'),
             ('statuts eurl', 'eurl', 'entreprise unipersonnelle')),
    _statuts('statuts_selarl', 'SELARL', 'société d’exercice libéral à responsabilité limitée', 'SELARL',
             'l’ordonnance n° 2023-77 du 8 février 2023, les articles L. 223-1 et suivants du Code de commerce et les règles de la profession',
             _sarl(selarl=True), V_SARL + V_PROFESSION, CLAUSES_SARL, COMMON_SARL_FIXED,
             _R('Statuts de SELARL (profession libérale réglementée).', 'French SELARL (regulated profession) articles.',
                'Satzung einer französischen SELARL.', 'Estatutos de SELARL francesa.'),
             ('statuts selarl', 'selarl', 'société d’exercice libéral à responsabilité limitée')),
    _statuts('statuts_sci', 'SCI', 'société civile immobilière', 'SCI', 'les articles 1832 et suivants et 1845 et suivants du Code civil',
             _societe_civile('SCI'),
             V_CIVILE + [('immeuble', 'Immeuble apporté ou à acquérir (adresse, désignation)', '')],
             CLAUSES_CIVILE + [('demembrement', 'Parts démembrées (usufruit / nue-propriété)', False, '')], COMMON_CIVILE_FIXED,
             _R('Statuts de SCI : gérance, agrément (article 1861 C. civ.), démembrement, héritiers.', 'French real-estate civil company (SCI) articles.',
                'Satzung einer französischen Immobilien-Gesellschaft (SCI).', 'Estatutos de SCI francesa (sociedad civil inmobiliaria).'),
             ('statuts sci', 'société civile immobilière', 'sci')),
    _statuts('statuts_scm', 'SCM', 'société civile de moyens', 'SCM', 'l’article 38 de l’ordonnance n° 2023-77 du 8 février 2023 et les articles 1832 et '
             'suivants du Code civil', _societe_civile('SCM'),
             V_CIVILE + V_PROFESSION + [('cle_repartition', 'Clé de répartition des charges', 'à parts égales'),
                                        ('preavis_retrait', 'Préavis de retrait (mois)', '6')],
             CLAUSES_CIVILE + [('exclusion', 'Exclusion', True, '')], COMMON_CIVILE_FIXED,
             _R('Statuts de SCM : mise en commun de moyens entre professionnels libéraux, répartition des charges, retrait, exclusion.',
                'French resource-sharing company (SCM) articles.', 'Satzung einer französischen Bürogemeinschaft (SCM).', 'Estatutos de SCM francesa.'),
             ('statuts scm', 'société civile de moyens', 'scm')),
    _statuts('statuts_scp', 'SCP', 'société civile professionnelle', 'SCP', 'les articles 5 et suivants de l’ordonnance n° 2023-77 du 8 février 2023, le décret '
             'propre à la profession et les articles 1832 et suivants du Code civil', _societe_civile('SCP'),
             V_CIVILE + V_PROFESSION + [('preavis_retrait', 'Préavis de retrait (mois)', '6')],
             CLAUSES_CIVILE + [('exclusion', 'Exclusion', True, ''), ('parts_industrie', 'Parts d’industrie', False, '')], COMMON_CIVILE_FIXED,
             _R('Statuts de SCP : exercice en commun d’une profession libérale réglementée (ordonnance du 8 février 2023).',
                'French professional civil partnership (SCP) articles.', 'Satzung einer französischen Berufsgesellschaft (SCP).', 'Estatutos de SCP francesa.'),
             ('statuts scp', 'société civile professionnelle', 'scp')),
]

# ------------------------------------------------------------------------------------------------ pactes
PACTE_COMMUN = [
    '# {{titre_pacte}}', '', '# ENTRE LES SOUSSIGNÉS :', '', '{{parties}}', '', 'ci-après ensemble les « Parties »,', '',
    '# EN PRÉSENCE DE :', '', '{{denomination}}, {{forme_long}} au capital de {{capital}} euros, dont le siège est {{siege}}, immatriculée au registre '
    'du commerce et des sociétés de {{rcs_ville}} sous le numéro {{siren}}, ci-après la « Société »,', '',
    '# IL A ÉTÉ PRÉALABLEMENT EXPOSÉ CE QUI SUIT :', '{{expose}}', '',
    'Les Parties ont souhaité organiser leurs relations au sein de la Société et définir les règles applicables au transfert de leurs titres, '
    'dans le respect des statuts, qui prévalent à l’égard des tiers. Le présent pacte est un contrat régi par les articles 1103 et suivants du '
    'Code civil.', '',
    '# IL A ÉTÉ CONVENU CE QUI SUIT :', '',
    '## Définitions',
    '« Titres » : les {{titres}} et toutes valeurs mobilières donnant accès, immédiatement ou à terme, au capital ou aux droits de vote de la '
    'Société. « Transfert » : toute opération, à titre onéreux ou gratuit, entraînant le transfert de la propriété, de la nue-propriété ou de '
    'l’usufruit de Titres. « Contrôle » : au sens de l’article L. 233-3 du Code de commerce.',
    '[[si gouvernance]]',
    '## Gouvernance',
    'Les Parties s’engagent à voter de manière à ce que {{gouvernance}}. Les décisions suivantes sont soumises à l’accord préalable des Parties '
    'détenant ensemble au moins {{majorite_decisions_importantes}} des Titres : adoption du budget annuel, investissements supérieurs à '
    '[À COMPLÉTER : montant], emprunts, cessions d’actifs significatifs, émission de Titres, modification des statuts, conventions avec une '
    'Partie. [À VÉRIFIER : articulation avec les pouvoirs statutaires du dirigeant.]',
    '[[fin]]',
    '[[si information]]',
    '## Droit d’information',
    'Chaque Partie détenant au moins {{seuil_information}} des Titres reçoit, dans les délais suivants : les comptes semestriels et annuels, le '
    'budget annuel, un reporting trimestriel de l’activité et de la trésorerie, et toute information raisonnablement demandée.',
    '[[fin]]',
    '[[si inalienabilite]]',
    '## Inaliénabilité',
    'Chaque Partie s’interdit de Transférer ses Titres pendant une durée de {{duree_inalienabilite}} à compter de la signature, sauf Transfert '
    'libre au profit d’une entité qu’elle contrôle et qui adhère au pacte, ou accord unanime des Parties. Cette durée est limitée et justifiée '
    'par l’intérêt de la Société.',
    '[[fin]]',
    '[[si preemption]]',
    '## Droit de préemption',
    'Tout projet de Transfert est notifié aux autres Parties, qui disposent d’un délai de {{delai_preemption}} jours pour exercer un droit de '
    'préemption sur la totalité des Titres offerts, au prix et aux conditions notifiés, au prorata de leur participation. À défaut d’exercice '
    'portant sur la totalité, le cédant peut céder au cessionnaire notifié dans un délai de {{delai_realisation}} jours, au prix et aux '
    'conditions notifiés, sous réserve de l’adhésion du cessionnaire au pacte.',
    '[[fin]]',
    '[[si sortie_conjointe]]',
    '## Sortie conjointe',
    '[[si sortie_conjointe_totale]]',
    'En cas de Transfert entraînant un changement de Contrôle de la Société, chaque Partie non cédante peut céder au cessionnaire la totalité de '
    'ses Titres, aux mêmes prix et conditions.',
    '[[sinon]]',
    'En cas de Transfert par une Partie, chacune des autres Parties peut céder au cessionnaire une quote-part de ses Titres égale à la proportion '
    'des Titres cédés par le cédant, aux mêmes prix et conditions.',
    '[[fin]]',
    'Le droit s’exerce dans un délai de {{delai_sortie_conjointe}} jours de la notification du projet. Le cédant se porte fort de l’acquisition '
    'par le cessionnaire des Titres des Parties ayant exercé leur droit ; à défaut, il les acquiert lui-même aux mêmes conditions.',
    '[[fin]]',
    '[[si sortie_forcee]]',
    '## Sortie forcée',
    'Si des Parties détenant au moins {{seuil_sortie_forcee}} des Titres acceptent une offre de bonne foi d’un tiers portant sur 100 % des Titres, '
    'les autres Parties s’engagent irrévocablement à céder leurs Titres au tiers, aux mêmes prix et conditions. Chaque Partie consent à cet '
    'effet une promesse unilatérale de vente (article 1124 du Code civil). Les garanties exigées des Parties minoritaires sont limitées à la '
    'propriété des Titres et à leur capacité, au prorata du prix perçu.',
    '[[fin]]',
    '[[si bad_leaver]]',
    '## Départ d’un associé dirigeant ou salarié (good leaver / bad leaver)',
    'En cas de cessation des fonctions d’une Partie au sein de la Société, celle-ci consent une promesse de vente de ses Titres au profit des '
    'autres Parties, exerçable pendant {{delai_leaver}} mois. Le prix est égal à la valeur de marché en cas de départ « good leaver » (décès, '
    'invalidité, révocation sans faute) et à {{prix_bad_leaver}} en cas de départ « bad leaver » (démission, révocation pour faute grave, '
    'violation du pacte). [À VÉRIFIER : proportionnalité de la décote, articulation avec le droit du travail.]',
    '[[fin]]',
    '[[si non_concurrence]]',
    '## Non-concurrence et non-sollicitation',
    'Chaque Partie {{parties_non_concurrence}} s’interdit, pendant la durée du pacte et {{duree_non_concurrence}} après la perte de sa qualité '
    'd’associé, d’exercer directement ou indirectement une activité concurrente de celle de la Société sur {{territoire_non_concurrence}}, ainsi '
    'que de solliciter ses salariés ou clients. Cette obligation est limitée dans le temps et l’espace et proportionnée aux intérêts de la '
    'Société.',
    '[[fin]]',
    '[[si anti_dilution]]',
    '## Anti-dilution',
    'En cas d’émission de Titres à un prix inférieur au prix payé par une Partie investisseur, celle-ci bénéficie d’un ajustement selon la '
    'méthode {{methode_anti_dilution}}, par voie d’émission de bons de souscription d’actions ou de tout autre mécanisme approprié.',
    '[[fin]]',
    '[[si liquidite]]',
    '## Liquidité',
    'À défaut de cession de la Société ou d’introduction en bourse dans un délai de {{delai_liquidite}} ans, les Parties s’engagent à mandater '
    'une banque d’affaires afin de rechercher un acquéreur pour 100 % des Titres.',
    '[[fin]]',
    '[[si cession_premiere]]',
    '## Répartition préférentielle du prix (liquidation preference)',
    'En cas de cession de 100 % des Titres ou de liquidation, le prix ou le boni est réparti en priorité au profit des Parties titulaires de '
    'Titres de catégorie [À COMPLÉTER] à hauteur de {{multiple_preference}} fois leur investissement, le solde étant réparti au prorata.',
    '[[fin]]',
    '## Adhésion',
    'Tout Transfert au profit d’un tiers est subordonné à l’adhésion préalable du cessionnaire au présent pacte.',
    '## Confidentialité',
    'Les Parties s’engagent à garder confidentiels le présent pacte et les informations relatives à la Société, sauf obligation légale.',
    '## Durée',
    'Le présent pacte est conclu pour une durée de {{duree_pacte}} à compter de sa signature. Il cesse à l’égard d’une Partie qui ne détient plus '
    'aucun Titre.',
    '## Sanctions',
    'Tout Transfert réalisé en violation du présent pacte est inopposable aux Parties ; la Partie défaillante est redevable d’une indemnité '
    '[À COMPLÉTER]. Les Parties reconnaissent que l’exécution forcée en nature des promesses consenties pourra être demandée (articles 1221 '
    'et 1124 du Code civil).',
    '## Droit applicable — Litiges',
    'Le présent pacte est régi par le droit français. Tout différend relève de la compétence exclusive du tribunal des activités économiques '
    '[À VÉRIFIER : juridiction compétente selon le lieu et la date] de {{tribunal}}'
    '[[si arbitrage]] ou, à défaut d’accord amiable, d’un arbitrage selon le règlement de [À COMPLÉTER][[fin]].',
    '',
    'Fait à {{ville}}, le {{date_acte}}, en autant d’originaux que de Parties, plus un pour la Société.', '', 'Signatures des Parties et de la Société.',
]
V_PACTE = [
    ('parties', 'Parties (une par ligne : nom, adresse, nombre de titres)', ''),
    ('denomination', 'Dénomination de la Société', ''), ('forme_long', 'Forme sociale', ''), ('siege', 'Siège', ''), ('capital', 'Capital (euros)', ''),
    ('rcs_ville', 'Greffe (ville)', ''), ('siren', 'Numéro SIREN', ''),
    ('expose', 'Exposé préalable', 'La Société exerce une activité de [À COMPLÉTER]. Les Parties en détiennent ensemble l’intégralité du capital.'),
    ('gouvernance', 'Engagement de vote sur la gouvernance', 'le Président soit choisi parmi les candidats proposés par [À COMPLÉTER]'),
    ('majorite_decisions_importantes', 'Majorité des décisions importantes', '75 %'),
    ('seuil_information', 'Seuil du droit d’information', '5 %'),
    ('duree_inalienabilite', 'Durée d’inaliénabilité', 'trois ans'),
    ('delai_preemption', 'Délai de préemption (jours)', '30'), ('delai_realisation', 'Délai de réalisation de la cession (jours)', '60'),
    ('delai_sortie_conjointe', 'Délai de sortie conjointe (jours)', '30'), ('seuil_sortie_forcee', 'Seuil de sortie forcée', '75 %'),
    ('delai_leaver', 'Délai d’exercice leaver (mois)', '6'), ('prix_bad_leaver', 'Prix bad leaver', 'la valeur nominale ou, si elle est inférieure, la valeur de marché diminuée de 30 %'),
    ('parties_non_concurrence', 'Parties tenues à non-concurrence', 'exerçant des fonctions opérationnelles'),
    ('duree_non_concurrence', 'Durée post-sortie de la non-concurrence', 'douze mois'),
    ('territoire_non_concurrence', 'Territoire de la non-concurrence', 'le territoire français'),
    ('methode_anti_dilution', 'Méthode anti-dilution', 'de la moyenne pondérée large (broad-based weighted average)'),
    ('delai_liquidite', 'Délai de liquidité (années)', '7'), ('multiple_preference', 'Multiple de préférence', '1'),
    ('duree_pacte', 'Durée du pacte', 'dix ans'), ('tribunal', 'Ville du tribunal', ''), ('ville', 'Lieu de signature', ''), ('date_acte', 'Date', ''),
]
C_PACTE = [
    ('gouvernance', 'Gouvernance et décisions importantes', True, ''), ('information', 'Droit d’information renforcé', True, ''),
    ('inalienabilite', 'Inaliénabilité', False, ''), ('preemption', 'Préemption', True, ''), ('sortie_conjointe', 'Sortie conjointe (tag-along)', True, ''),
    ('sortie_conjointe_totale', 'Sortie conjointe totale (seulement en cas de changement de contrôle)', True, ''),
    ('sortie_forcee', 'Sortie forcée (drag-along)', True, ''), ('bad_leaver', 'Good / bad leaver', False, ''),
    ('non_concurrence', 'Non-concurrence et non-sollicitation', False, ''), ('anti_dilution', 'Anti-dilution', False, ''),
    ('liquidite', 'Clause de liquidité', False, ''), ('cession_premiere', 'Liquidation preference', False, ''), ('arbitrage', 'Arbitrage', False, ''),
]

TEMPLATES += [
    {'id': 'pacte_associes', 'langue': FR, 'droit': 'FR', 'categorie': 'pacte', 'formes': ('SARL', 'SCI', 'SCM', 'SCP', 'SELARL', 'EURL'),
     'titre': 'Pacte d’associés', 'resume': _R('Pacte entre associés de SARL ou de société civile : gouvernance, information, préemption, sortie conjointe, '
                                             'non-concurrence.', 'Partners’ agreement (French law).', 'Gesellschaftervereinbarung (französisches Recht).',
                                             'Pacto de socios (derecho francés).'),
     'mots': ('pacte d’associés', 'pacte d\'associés', 'pacte associés'), 'variables': V_PACTE,
     'clauses': [c for c in C_PACTE if c[0] not in ('anti_dilution', 'liquidite', 'cession_premiere', 'sortie_forcee')],
     'fixes': {'titre_pacte': 'PACTE D’ASSOCIÉS', 'titres': 'parts sociales'}, 'texte': PACTE_COMMUN},
    {'id': 'pacte_actionnaires', 'langue': FR, 'droit': 'FR', 'categorie': 'pacte', 'formes': ('SAS', 'SASU', 'SELAS', 'SA'),
     'titre': 'Pacte d’actionnaires', 'resume': _R('Pacte d’actionnaires de SAS : gouvernance, préemption, tag-along, drag-along, leaver, anti-dilution, '
                                                 'liquidité, liquidation preference.', 'Shareholders’ agreement (French law).',
                                                 'Aktionärsvereinbarung (französisches Recht).', 'Pacto de accionistas (derecho francés).'),
     'mots': ('pacte d’actionnaires', 'pacte d\'actionnaires', 'pacte actionnaires', 'shareholders agreement'), 'variables': V_PACTE, 'clauses': C_PACTE,
     'fixes': {'titre_pacte': 'PACTE D’ACTIONNAIRES', 'titres': 'actions'}, 'texte': PACTE_COMMUN},
]

# ------------------------------------------------------------------------------------------------ opérations
TEMPLATES += [
    {'id': 'lettre_intention', 'langue': FR, 'droit': 'FR', 'categorie': 'operation', 'formes': (), 'titre': 'Lettre d’intention (LOI)',
     'resume': _R('Lettre d’intention d’acquisition : périmètre, prix indicatif, audit, exclusivité, confidentialité, caractère non engageant.',
                  'Letter of intent (French law).', 'Absichtserklärung (französisches Recht).', 'Carta de intenciones (derecho francés).'),
     'mots': ('lettre d’intention', 'lettre d\'intention', 'loi', 'letter of intent'),
     'variables': [('emetteur', 'Émetteur de la lettre (acquéreur)', ''), ('destinataire', 'Destinataire (cédant)', ''),
                   ('cible', 'Société ou fonds visé', ''), ('perimetre', 'Périmètre de l’opération', '100 % des titres de la société cible'),
                   ('prix_indicatif', 'Prix indicatif', ''), ('mode_prix', 'Mode de détermination du prix',
                                                              'valeur d’entreprise, ajustée de la dette nette et du besoin en fonds de roulement normatif à la date de réalisation'),
                   ('financement', 'Financement', 'fonds propres de l’acquéreur et financement bancaire [À COMPLÉTER]'),
                   ('duree_audit', 'Durée de l’audit (semaines)', '6'), ('duree_exclusivite', 'Durée de l’exclusivité', '60 jours'),
                   ('date_signature_cible', 'Date cible de signature', ''), ('ville', 'Lieu', ''), ('date_acte', 'Date', '')],
     'clauses': [('exclusivite', 'Exclusivité', True, ''), ('garantie', 'Garantie d’actif et de passif envisagée', True, ''),
                 ('accompagnement', 'Accompagnement du cédant', False, ''), ('earn_out', 'Complément de prix (earn-out)', False, ''),
                 ('conditions', 'Conditions suspensives envisagées', True, '')],
     'fixes': {}, 'texte': [
         '{{emetteur}}', '', 'À l’attention de {{destinataire}}', '', 'Fait à {{ville}}, le {{date_acte}}', '',
         '# Objet : lettre d’intention — {{cible}}', '', 'Madame, Monsieur,', '',
         'Nous avons le plaisir de vous faire part de notre intérêt pour l’acquisition de {{perimetre}} (l’« Opération »), aux conditions '
         'indicatives et non engageantes exposées ci-après.',
         '## Prix', 'Sur la base des informations communiquées à ce jour, le prix indicatif est de {{prix_indicatif}}, déterminé sur la base de '
         '{{mode_prix}}.',
         '[[si earn_out]]', 'Un complément de prix pourra être versé en fonction de [À COMPLÉTER : indicateur, période, plafond].', '[[fin]]',
         '## Financement', 'L’Opération serait financée par {{financement}}.',
         '## Audit', 'Notre offre ferme sera remise à l’issue d’un audit comptable, financier, juridique, fiscal et social d’une durée d’environ '
         '{{duree_audit}} semaines, sur la base d’une data room.',
         '[[si garantie]]', '## Garanties', 'L’acte définitif comportera une garantie d’actif et de passif usuelle, assortie d’une garantie de '
         'paiement [À COMPLÉTER : montant, durée, franchise, plafond].', '[[fin]]',
         '[[si conditions]]', '## Conditions', 'La réalisation de l’Opération serait notamment subordonnée à : l’obtention du financement, '
         'l’absence de changement défavorable significatif, l’agrément des organes compétents, et, le cas échéant, l’information préalable des '
         'salariés et les autorisations administratives requises.', '[[fin]]',
         '[[si accompagnement]]', '## Accompagnement', 'Le cédant accompagnerait l’acquéreur pendant une période de [À COMPLÉTER] mois afin '
         'd’assurer la transition.', '[[fin]]',
         '[[si exclusivite]]', '## Exclusivité', 'En contrepartie du temps et des frais engagés, vous vous engagez, pendant {{duree_exclusivite}} '
         'à compter de votre acceptation, à ne pas solliciter, encourager ni poursuivre de discussions avec un tiers relatives à une opération '
         'similaire. Cet engagement est ferme.', '[[fin]]',
         '## Confidentialité', 'Les informations échangées sont confidentielles et ne peuvent être utilisées qu’aux fins de l’Opération (article '
         '1112-2 du Code civil). Cet engagement est ferme.',
         '## Nature de la présente lettre', 'À l’exception des stipulations relatives à l’exclusivité, à la confidentialité, aux frais et au droit '
         'applicable, la présente lettre ne constitue pas une offre ferme et ne crée aucune obligation de conclure. Les parties mèneront les '
         'négociations de bonne foi (article 1112 du Code civil), la rupture des pourparlers ne pouvant engager la responsabilité que d’une partie '
         'fautive. Chaque partie supporte ses propres frais.',
         '## Droit applicable', 'La présente lettre est régie par le droit français.',
         '', 'Nous envisageons une signature de la documentation définitive vers le {{date_signature_cible}}. Si ces termes recueillent votre '
         'accord, nous vous remercions de nous retourner un exemplaire contresigné.', '', '{{emetteur}}', '', 'Bon pour accord, {{destinataire}}']},
    {'id': 'protocole_conditions', 'langue': FR, 'droit': 'FR', 'categorie': 'operation', 'formes': (), 'titre': 'Protocole d’accord sous conditions suspensives',
     'resume': _R('Protocole d’accord de cession sous conditions suspensives (articles 1304 et suivants du Code civil), date butoir, renonciation.',
                  'Conditional agreement (French law).', 'Vereinbarung unter aufschiebenden Bedingungen (französisches Recht).',
                  'Protocolo bajo condiciones suspensivas (derecho francés).'),
     'mots': ('protocole', 'conditions suspensives', 'condition suspensive', 'protocole d’accord'),
     'variables': [('partie_a', 'Partie A (cédant / promettant)', ''), ('partie_b', 'Partie B (acquéreur / bénéficiaire)', ''),
                   ('objet_operation', 'Objet de l’opération', 'la cession de [À COMPLÉTER] titres de la société [À COMPLÉTER]'),
                   ('prix', 'Prix', ''), ('date_butoir', 'Date butoir de réalisation des conditions', ''),
                   ('date_realisation', 'Date prévue de réalisation', ''), ('sequestre', 'Séquestre', ''),
                   ('montant_acompte', 'Montant de l’acompte / dépôt de garantie', ''), ('tribunal', 'Ville du tribunal', ''),
                   ('ville', 'Lieu', ''), ('date_acte', 'Date', '')],
     'clauses': [('cs_financement', 'Condition : obtention d’un financement', True, ''), ('cs_agrement', 'Condition : agrément des organes sociaux', True, ''),
                 ('cs_autorisation', 'Condition : autorisation administrative (concurrence, investissements étrangers)', False, ''),
                 ('cs_audit', 'Condition : résultats satisfaisants de l’audit', False, ''), ('cs_bail', 'Condition : renouvellement ou cession du bail', False, ''),
                 ('acompte', 'Acompte ou dépôt de garantie séquestré', True, ''), ('gestion_interim', 'Engagements de gestion intérimaire', True, ''),
                 ('clause_penale', 'Clause pénale', False, '')],
     'fixes': {}, 'texte': [
         '# PROTOCOLE D’ACCORD SOUS CONDITIONS SUSPENSIVES', '', '# ENTRE :', '{{partie_a}}', '', '# ET :', '{{partie_b}}', '',
         '# IL A ÉTÉ CONVENU CE QUI SUIT :', '',
         '## Objet', 'Les parties conviennent de {{objet_operation}} (l’« Opération »), sous les conditions suspensives ci-après.',
         '## Prix', 'L’Opération est consentie et acceptée moyennant le prix de {{prix}}, payable le jour de la réalisation.',
         '## Conditions suspensives',
         'L’Opération est subordonnée à la réalisation, au plus tard le {{date_butoir}} (la « Date butoir »), des conditions suivantes :',
         '[[si cs_financement]]', '- l’obtention par la Partie B d’un ou plusieurs prêts d’un montant total maximal de [À COMPLÉTER] euros, d’une durée '
         'de [À COMPLÉTER] ans, au taux maximal de [À COMPLÉTER] %, la Partie B s’engageant à déposer ses demandes dans les quinze jours ;', '[[fin]]',
         '[[si cs_agrement]]', '- l’agrément de la Partie B par les organes compétents de la société, dans les conditions statutaires ;', '[[fin]]',
         '[[si cs_autorisation]]', '- l’obtention de l’autorisation de [À COMPLÉTER : autorité] ;', '[[fin]]',
         '[[si cs_audit]]', '- l’absence, à l’issue de l’audit, de révélation d’un élément affectant de manière significative la valeur de '
         '[À COMPLÉTER] de plus de [À COMPLÉTER] euros ;', '[[fin]]',
         '[[si cs_bail]]', '- l’accord du bailleur à la cession du bail ou la conclusion d’un nouveau bail aux conditions [À COMPLÉTER] ;', '[[fin]]',
         'Conformément aux articles 1304 et suivants du Code civil, chaque partie s’interdit d’empêcher la réalisation des conditions ; la condition '
         'est réputée accomplie si la partie qui y avait intérêt en a empêché l’accomplissement (article 1304-3). La partie dans l’intérêt exclusif '
         'de laquelle une condition est stipulée peut y renoncer tant que celle-ci n’est pas accomplie ou défaillie (article 1304-4).',
         'Si toutes les conditions sont réalisées ou ont fait l’objet d’une renonciation au plus tard à la Date butoir, l’Opération sera réalisée le '
         '{{date_realisation}}. À défaut, le présent protocole sera caduc de plein droit, sans indemnité de part ni d’autre, sauf faute.',
         '[[si acompte]]', '## Acompte', 'À la signature, la Partie B verse la somme de {{montant_acompte}} entre les mains de {{sequestre}}, '
         'séquestre, qui la remettra à la Partie A le jour de la réalisation, à valoir sur le prix, ou la restituera à la Partie B en cas de '
         'défaillance d’une condition non imputable à celle-ci.', '[[fin]]',
         '[[si gestion_interim]]', '## Gestion jusqu’à la réalisation', 'Jusqu’à la réalisation, la Partie A s’engage à faire gérer la société ou '
         'le fonds en bon père de famille, dans le cours normal des affaires, et à ne prendre aucune décision excédant ce cours normal sans '
         'l’accord préalable écrit de la Partie B.', '[[fin]]',
         '## Réalisation', 'La réalisation donne lieu à la signature de l’acte définitif, au paiement du prix et à la remise des documents '
         '[À COMPLÉTER : liste].',
         '[[si clause_penale]]', '## Clause pénale', 'Si, toutes les conditions étant réalisées, l’une des parties refuse de réaliser l’Opération, '
         'elle versera à l’autre une indemnité forfaitaire de [À COMPLÉTER] euros, sans préjudice de l’exécution forcée (article 1231-5 du Code '
         'civil).', '[[fin]]',
         '## Confidentialité', 'Le présent protocole et son contenu sont confidentiels.',
         '## Droit applicable — Litiges', 'Le présent protocole est régi par le droit français. Tout litige relève de la compétence des juridictions '
         'de {{tribunal}}.', '', 'Fait à {{ville}}, le {{date_acte}}, en deux originaux.', '', 'Partie A                                Partie B']},
    {'id': 'compromis_fonds', 'langue': FR, 'droit': 'FR', 'categorie': 'operation', 'formes': (),
     'titre': 'Compromis de vente de fonds de commerce sous conditions suspensives',
     'resume': _R('Compromis de cession de fonds de commerce : éléments cédés, bail, salariés (information préalable), conditions suspensives, '
                  'séquestre et oppositions.', 'Conditional sale of a business (fonds de commerce, French law).',
                  'Vorvertrag über den Verkauf eines Handelsgeschäfts (französisches Recht).', 'Precontrato de compraventa de fondo de comercio (derecho francés).'),
     'mots': ('compromis de vente de fonds de commerce', 'compromis de cession de fonds de commerce', 'promesse de vente de fonds de commerce',
              'promesse de cession de fonds de commerce'),
     'variables': [('cedant', 'Cédant (dénomination, siège, RCS, représentant)', ''), ('cessionnaire', 'Cessionnaire (dénomination, siège, RCS, représentant)', ''),
                   ('fonds', 'Désignation du fonds (activité, enseigne, adresse)', ''), ('rcs_fonds', 'Immatriculation du fonds (RCS)', ''),
                   ('elements_corporels', 'Éléments corporels (matériel, mobilier)', 'selon inventaire annexé'),
                   ('prix', 'Prix total', ''), ('prix_incorporels', 'Prix des éléments incorporels', ''), ('prix_corporels', 'Prix des éléments corporels', ''),
                   ('prix_stock', 'Prix du stock', 'à déterminer par inventaire contradictoire au jour de la réalisation'),
                   ('bail', 'Bail commercial (bailleur, date, durée, loyer)', ''), ('chiffres', 'Chiffres d’affaires et résultats des trois derniers exercices', ''),
                   ('date_butoir', 'Date butoir', ''), ('sequestre', 'Séquestre du prix', ''), ('depot', 'Dépôt de garantie versé', ''),
                   ('salaries', 'Salariés transférés (nombre, postes)', ''), ('ville', 'Lieu', ''), ('date_acte', 'Date', '')],
     'clauses': [('cs_financement', 'Condition : financement', True, ''), ('cs_bail', 'Condition : accord du bailleur / renouvellement', True, ''),
                 ('cs_preemption_commune', 'Condition : non-exercice du droit de préemption de la commune', False, ''),
                 ('cs_licence', 'Condition : transfert de licence ou d’autorisation', False, ''),
                 ('information_salaries', 'Information préalable des salariés (L. 141-23)', True, ''),
                 ('non_concurrence', 'Non-concurrence du cédant', True, ''), ('accompagnement', 'Accompagnement du cédant', False, '')],
     'fixes': {}, 'texte': [
         '# COMPROMIS DE CESSION DE FONDS DE COMMERCE SOUS CONDITIONS SUSPENSIVES', '', '# ENTRE :', '{{cedant}}, ci-après le « Cédant »,', '',
         '# ET :', '{{cessionnaire}}, ci-après le « Cessionnaire »,', '', '# IL A ÉTÉ CONVENU CE QUI SUIT :', '',
         '## Objet', 'Le Cédant s’engage à céder au Cessionnaire, qui s’engage à acquérir, sous les conditions suspensives ci-après, le fonds de '
         'commerce de {{fonds}}, immatriculé {{rcs_fonds}} (le « Fonds »).',
         '## Désignation', 'Le Fonds comprend : les éléments incorporels (clientèle, achalandage, enseigne, nom commercial, droit au bail, '
         'lignes téléphoniques, site internet et noms de domaine, contrats transmissibles) ; les éléments corporels : {{elements_corporels}} ; '
         'les marchandises en stock. Sont exclus : les créances, les dettes, la trésorerie et les livres de commerce, sauf stipulation contraire.',
         '## Bail', 'Le Fonds est exploité dans des locaux loués aux termes de {{bail}}. Le Cédant déclare que le bail est en cours, que les loyers '
         'sont payés et qu’aucun commandement ni congé n’a été délivré. La cession du droit au bail sera réalisée dans les formes prévues au bail.',
         '## Chiffres d’affaires et résultats', 'Le Cédant déclare que les chiffres d’affaires et résultats d’exploitation des trois derniers '
         'exercices sont les suivants : {{chiffres}}. Le Cédant remet au Cessionnaire les livres de comptabilité tenus durant ces exercices, qui '
         'sont visés par les parties.',
         '## Prix', 'La cession est consentie moyennant le prix de {{prix}}, s’appliquant aux éléments incorporels pour {{prix_incorporels}} et aux '
         'éléments corporels pour {{prix_corporels}}. Le stock sera payé en sus, {{prix_stock}}.',
         '## Conditions suspensives', 'Le présent compromis est conclu sous les conditions suivantes, qui devront être réalisées au plus tard le '
         '{{date_butoir}} :',
         '[[si cs_financement]]', '- obtention par le Cessionnaire d’un prêt de [À COMPLÉTER] euros sur [À COMPLÉTER] ans au taux maximal de '
         '[À COMPLÉTER] % ;', '[[fin]]',
         '[[si cs_bail]]', '- accord du bailleur à la cession du bail ou absence d’opposition de sa part, et, le cas échéant, renouvellement du bail '
         'aux conditions [À COMPLÉTER] ;', '[[fin]]',
         '[[si cs_preemption_commune]]', '- non-exercice par la commune de son droit de préemption sur le Fonds (article L. 214-1 du Code de '
         'l’urbanisme), la déclaration préalable étant déposée par le Cédant ;', '[[fin]]',
         '[[si cs_licence]]', '- transfert au Cessionnaire de [À COMPLÉTER : licence, autorisation] ;', '[[fin]]',
         '- absence d’inscription de privilèges ou de nantissements d’un montant supérieur au prix, sauf mainlevée.',
         'La partie dans l’intérêt exclusif de laquelle une condition est stipulée peut y renoncer. À défaut de réalisation à la date butoir, le '
         'compromis est caduc et le dépôt de garantie restitué au Cessionnaire, sauf faute de celui-ci (articles 1304 et suivants du Code civil).',
         '[[si information_salaries]]', '## Information des salariés', 'Si le Cédant emploie moins de deux cent cinquante salariés, il informe '
         'les salariés de son intention de céder le Fonds au plus tard deux mois avant la cession, afin de leur permettre de présenter une offre '
         '(articles L. 141-23 et suivants du Code de commerce). Les contrats de travail en cours sont transférés au Cessionnaire (article '
         'L. 1224-1 du Code du travail) : {{salaries}}.', '[[fin]]',
         '## Dépôt de garantie — Séquestre', 'Le Cessionnaire a versé ce jour {{depot}} entre les mains de {{sequestre}}. Le prix payé lors de la '
         'réalisation sera séquestré entre les mains de {{sequestre}} pendant le délai d’opposition des créanciers, à compter de la publication '
         'de la cession (articles L. 141-12 et L. 141-14 du Code de commerce), et remis au Cédant après mainlevée des oppositions éventuelles.',
         '[[si non_concurrence]]', '## Non-concurrence', 'Le Cédant s’interdit d’exploiter directement ou indirectement un fonds similaire dans un '
         'rayon de [À COMPLÉTER] kilomètres pendant [À COMPLÉTER] ans à compter de la réalisation.', '[[fin]]',
         '[[si accompagnement]]', '## Accompagnement', 'Le Cédant présentera le Cessionnaire à la clientèle et aux fournisseurs et l’assistera '
         'pendant [À COMPLÉTER] jours.', '[[fin]]',
         '## Réalisation — Formalités', 'L’acte de cession définitif sera signé au plus tard [À COMPLÉTER] jours après la réalisation de la dernière '
         'condition. Le Cessionnaire procédera à l’enregistrement de l’acte et à sa publication dans un support d’annonces légales et au BODACC '
         'dans les quinze jours de sa date. Les droits d’enregistrement sont à la charge du Cessionnaire.',
         '## Jouissance', 'Le Cessionnaire aura la propriété et la jouissance du Fonds à compter du jour de la signature de l’acte définitif.',
         '', 'Fait à {{ville}}, le {{date_acte}}, en trois originaux.', '', 'Le Cédant                                Le Cessionnaire']},
    {'id': 'cession_titres', 'langue': FR, 'droit': 'FR', 'categorie': 'operation', 'formes': ('SAS', 'SASU', 'SARL', 'EURL', 'SELAS', 'SELARL', 'SCI', 'SCM', 'SCP'),
     'titre': 'Acte de cession de titres', 'resume': _R('Acte de cession d’actions ou de parts sociales : prix, ajustement, déclarations, garantie '
                                                      'd’actif et de passif, agrément, formalités et droits d’enregistrement.',
                                                      'Share transfer deed (French law).', 'Anteilsübertragungsvertrag (französisches Recht).',
                                                      'Contrato de cesión de participaciones (derecho francés).'),
     'mots': ('cession de titres', 'cession d’actions', 'cession de parts', 'cession d\'actions', 'acte de cession'),
     'variables': [('cedant', 'Cédant', ''), ('cessionnaire', 'Cessionnaire', ''), ('denomination', 'Société', ''), ('forme_long', 'Forme sociale', ''),
                   ('capital', 'Capital (euros)', ''), ('siege', 'Siège', ''), ('rcs_ville', 'Greffe (ville)', ''), ('siren', 'SIREN', ''),
                   ('nombre', 'Nombre de titres cédés', ''), ('titres', 'Nature des titres', 'actions'), ('prix_unitaire', 'Prix par titre', ''),
                   ('prix_total', 'Prix total', ''), ('modalites_paiement', 'Modalités de paiement', 'comptant, par virement, le jour de la signature'),
                   ('date_jouissance', 'Date de jouissance', 'la date des présentes'), ('ville', 'Lieu', ''), ('date_acte', 'Date', '')],
     'clauses': [('garantie', 'Garantie d’actif et de passif', True, ''), ('garantie_bancaire', 'Garantie bancaire de la garantie de passif', False, ''),
                 ('ajustement', 'Ajustement du prix (comptes de référence)', False, ''), ('earn_out', 'Complément de prix', False, ''),
                 ('agrement', 'Mention de l’agrément obtenu', True, ''), ('parts_sociales', 'Formalités propres aux parts sociales', False, ''),
                 ('non_concurrence', 'Non-concurrence du cédant', False, ''), ('demission', 'Démission du cédant de ses mandats', False, '')],
     'fixes': {}, 'texte': [
         '# ACTE DE CESSION DE {{titres}}', '', '# ENTRE LES SOUSSIGNÉS :', '{{cedant}}, ci-après le « Cédant »,', '', '# ET :',
         '{{cessionnaire}}, ci-après le « Cessionnaire »,', '', '# IL A ÉTÉ CONVENU CE QUI SUIT :', '',
         '## Cession', 'Le Cédant cède au Cessionnaire, qui accepte, {{nombre}} {{titres}} (les « Titres ») de la société {{denomination}}, '
         '{{forme_long}} au capital de {{capital}} euros, dont le siège est {{siege}}, immatriculée au registre du commerce et des sociétés de '
         '{{rcs_ville}} sous le numéro {{siren}} (la « Société »).',
         '## Prix', 'La cession est consentie moyennant le prix de {{prix_unitaire}} par titre, soit un prix total de {{prix_total}}, payé '
         '{{modalites_paiement}}, dont quittance sous réserve d’encaissement.',
         '[[si ajustement]]', 'Le prix sera ajusté, à la hausse ou à la baisse, de la différence entre les capitaux propres ressortant de la situation '
         'comptable arrêtée au [À COMPLÉTER] et ceux des comptes de référence au [À COMPLÉTER].', '[[fin]]',
         '[[si earn_out]]', 'Un complément de prix sera dû selon les modalités suivantes : [À COMPLÉTER : indicateur, période, plafond, '
         'information du Cédant].', '[[fin]]',
         '## Propriété — Jouissance', 'Le Cessionnaire est propriétaire des Titres à compter de ce jour et en a la jouissance à compter de '
         '{{date_jouissance}}. Il a droit aux dividendes mis en distribution postérieurement.',
         '## Déclarations du Cédant', 'Le Cédant déclare : avoir la capacité de céder ; être propriétaire des Titres, libres de tout nantissement, '
         'gage, promesse ou droit quelconque ; que la Société n’est pas en état de cessation des paiements et ne fait l’objet d’aucune procédure '
         'collective ; que les comptes communiqués sont sincères.',
         '[[si agrement]]', '## Agrément', 'La cession a été agréée par décision de [À COMPLÉTER : organe] en date du [À COMPLÉTER], conformément '
         'aux statuts.', '[[fin]]',
         '[[si garantie]]', '## Garantie d’actif et de passif', 'Le Cédant garantit le Cessionnaire contre toute augmentation de passif ou '
         'diminution d’actif ayant une cause antérieure à la date de référence et non révélée, dans les conditions de la convention de garantie '
         'annexée : franchise de [À COMPLÉTER] euros, plafond de [À COMPLÉTER] euros, durée expirant [À COMPLÉTER], procédure de notification des '
         'réclamations et conduite des litiges.',
         '[[si garantie_bancaire]]', 'Le paiement des sommes dues au titre de la garantie est garanti par une garantie bancaire à première demande '
         'de [À COMPLÉTER] euros émise par [À COMPLÉTER].', '[[fin]]', '[[fin]]',
         '[[si non_concurrence]]', '## Non-concurrence', 'Le Cédant s’interdit, pendant [À COMPLÉTER] ans et sur [À COMPLÉTER], toute activité '
         'concurrente de celle de la Société, cette interdiction étant limitée et proportionnée.', '[[fin]]',
         '[[si demission]]', '## Mandats', 'Le Cédant remet ce jour sa démission de ses fonctions de [À COMPLÉTER], à effet de ce jour.', '[[fin]]',
         '## Formalités — Frais',
         '[[si parts_sociales]]', 'La cession sera rendue opposable à la Société par le dépôt d’un original au siège contre remise d’une '
         'attestation du gérant, ou par signification, et aux tiers après dépôt au registre du commerce et des sociétés des statuts modifiés.',
         '[[sinon]]', 'Le transfert des Titres sera inscrit au registre des mouvements de titres de la Société sur présentation d’un ordre de '
         'mouvement signé du Cédant, remis ce jour.', '[[fin]]',
         'Le présent acte sera enregistré dans le mois de sa date. Les droits d’enregistrement sont à la charge du Cessionnaire. [À VÉRIFIER : '
         'taux applicable selon la nature des titres et la situation de la Société.]',
         '## Droit applicable', 'Le présent acte est régi par le droit français.', '',
         'Fait à {{ville}}, le {{date_acte}}, en [À COMPLÉTER] originaux.', '', 'Le Cédant                                Le Cessionnaire']},
    {'id': 'cession_fonds', 'langue': FR, 'droit': 'FR', 'categorie': 'operation', 'formes': (), 'titre': 'Acte de cession de fonds de commerce',
     'resume': _R('Acte définitif de cession de fonds de commerce : désignation, origine de propriété, bail, prix ventilé, séquestre, publicité, '
                  'oppositions, salariés.', 'Deed of sale of a business (French law).', 'Kaufvertrag über ein Handelsgeschäft (französisches Recht).',
                  'Escritura de compraventa de fondo de comercio (derecho francés).'),
     'mots': ('cession de fonds de commerce', 'vente de fonds de commerce', 'acte de cession de fonds'),
     'variables': [('cedant', 'Cédant', ''), ('cessionnaire', 'Cessionnaire', ''), ('fonds', 'Désignation du fonds', ''), ('rcs_fonds', 'Immatriculation du fonds', ''),
                   ('origine', 'Origine de propriété', ''), ('bail', 'Bail commercial', ''), ('prix', 'Prix total', ''),
                   ('prix_incorporels', 'Prix des incorporels', ''), ('prix_corporels', 'Prix des corporels', ''), ('prix_stock', 'Prix du stock', ''),
                   ('chiffres', 'Chiffres d’affaires et résultats', ''), ('sequestre', 'Séquestre', ''), ('salaries', 'Salariés transférés', ''),
                   ('ville', 'Lieu', ''), ('date_acte', 'Date', '')],
     'clauses': [('non_concurrence', 'Non-concurrence du cédant', True, ''), ('privilege', 'Privilège du vendeur (prix payable à terme)', False, ''),
                 ('contrats', 'Transfert de contrats', True, ''), ('accompagnement', 'Accompagnement', False, '')],
     'fixes': {}, 'texte': [
         '# ACTE DE CESSION DE FONDS DE COMMERCE', '', '# ENTRE :', '{{cedant}}, ci-après le « Cédant »,', '', '# ET :',
         '{{cessionnaire}}, ci-après le « Cessionnaire »,', '', '# IL A ÉTÉ CONVENU CE QUI SUIT :', '',
         '## Cession', 'Le Cédant cède au Cessionnaire, qui accepte, le fonds de commerce de {{fonds}}, immatriculé {{rcs_fonds}} (le « Fonds »), '
         'comprenant les éléments incorporels (clientèle, achalandage, enseigne, nom commercial, droit au bail), les éléments corporels désignés à '
         'l’inventaire annexé, et les marchandises en stock.',
         '## Origine de propriété', '{{origine}}',
         '## Bail', 'Le droit au bail résulte de {{bail}}. Le bailleur a été appelé à l’acte ou la cession lui sera signifiée conformément aux '
         'stipulations du bail.',
         '## Chiffres d’affaires et résultats', '{{chiffres}}', 'Les livres de comptabilité des trois derniers exercices ont été visés par les '
         'parties et seront tenus à la disposition du Cessionnaire pendant trois ans.',
         '## Prix', 'La cession est consentie moyennant le prix de {{prix}}, s’appliquant : aux éléments incorporels pour {{prix_incorporels}} ; '
         'aux éléments corporels pour {{prix_corporels}} ; au stock pour {{prix_stock}}.',
         '[[si privilege]]', 'Le solde du prix, payable à terme, est garanti par le privilège du vendeur, inscrit dans les trente jours de l’acte '
         '(articles L. 141-5 et suivants du Code de commerce), ainsi que par l’action résolutoire.', '[[fin]]',
         '## Séquestre — Oppositions', 'Le prix est versé entre les mains de {{sequestre}}, séquestre, qui le remettra au Cédant à l’expiration du '
         'délai d’opposition des créanciers suivant la publication au BODACC (article L. 141-14 du Code de commerce), après mainlevée des '
         'oppositions et paiement des créanciers inscrits.',
         '## Salariés', 'Les contrats de travail des salariés attachés au Fonds sont transférés au Cessionnaire (article L. 1224-1 du Code du '
         'travail) : {{salaries}}. Le Cédant déclare avoir satisfait à l’obligation d’information préalable des salariés lorsqu’elle s’applique '
         '(articles L. 141-23 et suivants du Code de commerce).',
         '[[si contrats]]', '## Contrats', 'Sont transférés au Cessionnaire, sous réserve de l’accord des cocontractants lorsqu’il est requis, '
         'les contrats suivants : [À COMPLÉTER].', '[[fin]]',
         '[[si non_concurrence]]', '## Non-concurrence', 'Le Cédant s’interdit d’exploiter, directement ou indirectement, un fonds similaire dans '
         'un rayon de [À COMPLÉTER] kilomètres pendant [À COMPLÉTER] ans.', '[[fin]]',
         '[[si accompagnement]]', '## Accompagnement', 'Le Cédant assistera le Cessionnaire pendant [À COMPLÉTER] jours.', '[[fin]]',
         '## Propriété — Jouissance', 'Le Cessionnaire est propriétaire du Fonds et en a la jouissance à compter de ce jour.',
         '## Charges — Impôts', 'Le Cessionnaire acquitte à compter de ce jour les charges d’exploitation. Les impôts sont répartis prorata '
         'temporis. Le Cédant déclare sa cessation d’activité dans les délais fiscaux ; l’attention des parties a été attirée sur la solidarité '
         'fiscale du Cessionnaire et la nécessité du séquestre [À VÉRIFIER : article 1684 du Code général des impôts].',
         '## Publicité — Enregistrement', 'Le présent acte sera enregistré et publié, à la diligence du Cessionnaire, dans un support habilité à '
         'recevoir des annonces légales et au BODACC dans les quinze jours de sa date (article L. 141-12 du Code de commerce). Les droits '
         'd’enregistrement sont à la charge du Cessionnaire.',
         '## Élection de domicile', 'Pour la réception des oppositions, domicile est élu chez {{sequestre}}.', '',
         'Fait à {{ville}}, le {{date_acte}}, en [À COMPLÉTER] originaux.', '', 'Le Cédant                                Le Cessionnaire']},
]

# ------------------------------------------------------------------------------------------------ contrats
TEMPLATES += [
    {'id': 'contrat_prestation', 'langue': FR, 'droit': 'FR', 'categorie': 'contrat', 'formes': (), 'titre': 'Contrat de prestation de services',
     'resume': _R('Contrat de prestation de services entre professionnels : périmètre, prix, responsabilité, propriété intellectuelle, données '
                  'personnelles.', 'Services agreement (French law).', 'Dienstleistungsvertrag (französisches Recht).', 'Contrato de servicios (derecho francés).'),
     'mots': ('contrat de prestation', 'prestation de services', 'contrat de services'),
     'variables': [('prestataire', 'Prestataire', ''), ('client', 'Client', ''), ('prestations', 'Description des prestations', ''),
                   ('prix', 'Prix et modalités de facturation', ''), ('delai_paiement', 'Délai de paiement', '30 jours à compter de la date de la facture'),
                   ('duree', 'Durée', 'un an, renouvelable tacitement par périodes d’un an'), ('preavis', 'Préavis de résiliation', 'trois mois'),
                   ('plafond', 'Plafond de responsabilité', 'le montant des sommes payées au titre des douze derniers mois'),
                   ('tribunal', 'Ville du tribunal', ''), ('ville', 'Lieu', ''), ('date_acte', 'Date', '')],
     'clauses': [('pi', 'Cession des droits de propriété intellectuelle', True, ''), ('rgpd', 'Clause sous-traitance de données (article 28 RGPD)', True, ''),
                 ('non_sollicitation', 'Non-sollicitation du personnel', False, ''), ('sous_traitance', 'Sous-traitance autorisée', False, ''),
                 ('niveaux_service', 'Niveaux de service (SLA)', False, '')],
     'fixes': {}, 'texte': [
         '# CONTRAT DE PRESTATION DE SERVICES', '', '# ENTRE :', '{{prestataire}}, ci-après le « Prestataire »,', '# ET :', '{{client}}, ci-après le « Client »,', '',
         '## Objet', 'Le Prestataire s’engage à réaliser pour le Client les prestations suivantes : {{prestations}} (les « Prestations »).',
         '## Obligations du Prestataire', 'Le Prestataire exécute les Prestations avec diligence et selon les règles de l’art ; il est tenu d’une '
         'obligation de moyens, sauf stipulation contraire. Il informe le Client de toute difficulté.',
         '[[si niveaux_service]]', 'Les niveaux de service et pénalités figurent en annexe [À COMPLÉTER].', '[[fin]]',
         '## Obligations du Client', 'Le Client communique en temps utile les informations et documents nécessaires et collabore de bonne foi.',
         '## Prix — Paiement', 'En contrepartie des Prestations, le Client paie {{prix}}. Les factures sont payables dans un délai de {{delai_paiement}}. '
         'Tout retard entraîne de plein droit des pénalités au taux prévu à l’article L. 441-10 du Code de commerce et une indemnité forfaitaire '
         'pour frais de recouvrement de quarante euros.',
         '## Durée — Résiliation', 'Le contrat est conclu pour {{duree}}. Chaque partie peut y mettre fin moyennant un préavis de {{preavis}}. En cas '
         'de manquement grave non réparé dans les trente jours d’une mise en demeure, l’autre partie peut résilier le contrat (articles 1224 et '
         'suivants du Code civil).',
         '## Responsabilité', 'La responsabilité de chaque partie est limitée aux dommages directs et prévisibles, dans la limite de {{plafond}}, '
         'sauf faute lourde ou dolosive et dommages corporels.',
         '[[si pi]]', '## Propriété intellectuelle', 'Le Prestataire cède au Client, au fur et à mesure de leur réalisation et sous réserve du '
         'complet paiement, les droits patrimoniaux sur les livrables (reproduction, représentation, adaptation), pour le monde entier et la durée '
         'légale des droits, le prix de cette cession étant compris dans le prix des Prestations. [À VÉRIFIER : exigences de l’article L. 131-3 du '
         'Code de la propriété intellectuelle.]', '[[fin]]',
         '## Confidentialité', 'Chaque partie garde confidentielles les informations reçues de l’autre pendant le contrat et cinq ans après.',
         '[[si rgpd]]', '## Données personnelles', 'Lorsque le Prestataire traite des données personnelles pour le compte du Client, il agit en '
         'qualité de sous-traitant au sens de l’article 28 du règlement (UE) 2016/679 et respecte les stipulations de l’annexe « protection des '
         'données ».', '[[fin]]',
         '[[si non_sollicitation]]', '## Non-sollicitation', 'Chaque partie s’interdit de solliciter le personnel de l’autre affecté au contrat '
         'pendant sa durée et douze mois après.', '[[fin]]',
         '[[si sous_traitance]]', '## Sous-traitance', 'Le Prestataire peut sous-traiter une partie des Prestations, en restant responsable de '
         'leur exécution.', '[[fin]]',
         '## Indépendance', 'Les parties agissent en professionnels indépendants ; le contrat ne crée aucun lien de subordination.',
         '## Droit applicable — Litiges', 'Le contrat est régi par le droit français. À défaut d’accord amiable, tout litige relève de la compétence '
         'des juridictions de {{tribunal}}.', '', 'Fait à {{ville}}, le {{date_acte}}, en deux exemplaires.']},
    {'id': 'accord_confidentialite', 'langue': FR, 'droit': 'FR', 'categorie': 'contrat', 'formes': (), 'titre': 'Accord de confidentialité',
     'resume': _R('Accord de confidentialité (NDA) unilatéral ou réciproque, secret des affaires.', 'Non-disclosure agreement (French law).',
                  'Geheimhaltungsvereinbarung (französisches Recht).', 'Acuerdo de confidencialidad (derecho francés).'),
     'mots': ('accord de confidentialité', 'nda', 'confidentialité', 'non-disclosure'),
     'variables': [('partie_a', 'Partie A', ''), ('partie_b', 'Partie B', ''), ('projet', 'Projet', ''), ('duree', 'Durée de l’obligation', 'cinq ans'),
                   ('tribunal', 'Ville du tribunal', ''), ('ville', 'Lieu', ''), ('date_acte', 'Date', '')],
     'clauses': [('reciproque', 'Accord réciproque', True, ''), ('non_sollicitation', 'Non-sollicitation', False, ''), ('clause_penale', 'Clause pénale', False, '')],
     'fixes': {}, 'texte': [
         '# ACCORD DE CONFIDENTIALITÉ', '', '# ENTRE :', '{{partie_a}}', '# ET :', '{{partie_b}}', '',
         '## Objet', 'Dans le cadre de {{projet}} (le « Projet »), '
         '[[si reciproque]]chaque partie[[sinon]]la Partie A[[fin]] est amenée à communiquer des informations confidentielles. Le présent accord '
         'fixe les conditions de leur protection, notamment au titre du secret des affaires (articles L. 151-1 et suivants du Code de commerce).',
         '## Informations confidentielles', 'Sont confidentielles toutes informations de toute nature, communiquées par écrit, oralement ou par tout '
         'autre moyen, à l’exception de celles qui sont dans le domaine public sans faute du destinataire, étaient déjà connues de lui, lui ont été '
         'communiquées par un tiers libre de le faire, ou ont été développées indépendamment.',
         '## Obligations', 'Le destinataire s’engage à n’utiliser les informations qu’aux fins du Projet, à ne les communiquer qu’aux personnes qui '
         'ont besoin d’en connaître et sont tenues à une obligation équivalente, et à les protéger avec le même soin que ses propres informations.',
         '## Restitution', 'À première demande, ou à la fin du Projet, le destinataire restitue ou détruit les informations et leurs copies, sous '
         'réserve des obligations légales de conservation.',
         '## Durée', 'Les obligations de confidentialité demeurent en vigueur pendant {{duree}} à compter de la dernière communication.',
         '[[si non_sollicitation]]', '## Non-sollicitation', 'Pendant la durée du Projet et douze mois après, aucune partie ne sollicite le personnel '
         'de l’autre.', '[[fin]]',
         '[[si clause_penale]]', '## Clause pénale', 'Toute violation donne lieu au paiement d’une indemnité forfaitaire de [À COMPLÉTER] euros, '
         'sans préjudice de dommages-intérêts complémentaires (article 1231-5 du Code civil).', '[[fin]]',
         '## Absence de licence', 'Aucune stipulation n’emporte cession ou licence de droits.',
         '## Droit applicable', 'Le présent accord est régi par le droit français ; tout litige relève des juridictions de {{tribunal}}.', '',
         'Fait à {{ville}}, le {{date_acte}}, en deux exemplaires.']},
    {'id': 'cgv_b2b', 'langue': FR, 'droit': 'FR', 'categorie': 'contrat', 'formes': (), 'titre': 'Conditions générales de vente (professionnels)',
     'resume': _R('CGV entre professionnels (article L. 441-1 du Code de commerce) : commandes, prix, paiement, pénalités, réserve de propriété.',
                  'B2B terms of sale (French law).', 'Allgemeine Verkaufsbedingungen B2B (französisches Recht).', 'Condiciones generales de venta B2B (derecho francés).'),
     'mots': ('conditions générales de vente', 'cgv', 'conditions générales'),
     'variables': [('vendeur', 'Vendeur (dénomination, siège, RCS)', ''), ('produits', 'Produits ou services', ''), ('delai_paiement', 'Délai de paiement',
                                                                                                                'trente jours à compter de la date de la facture'),
                   ('escompte', 'Escompte pour paiement anticipé', 'aucun escompte'), ('tribunal', 'Ville du tribunal', '')],
     'clauses': [('reserve_propriete', 'Réserve de propriété', True, ''), ('garantie', 'Garantie commerciale', False, ''),
                 ('force_majeure', 'Force majeure', True, '')],
     'fixes': {}, 'texte': [
         '# CONDITIONS GÉNÉRALES DE VENTE', '{{vendeur}}', '',
         '## Champ d’application', 'Les présentes conditions générales s’appliquent à toutes les ventes de {{produits}} conclues avec des '
         'professionnels. Elles constituent le socle unique de la négociation commerciale (article L. 441-1 du Code de commerce) et prévalent sur '
         'les conditions d’achat, sauf accord écrit.',
         '## Commandes', 'Les commandes ne sont définitives qu’après acceptation écrite du vendeur.',
         '## Prix', 'Les prix sont ceux du tarif en vigueur au jour de la commande, hors taxes.',
         '## Paiement', 'Les factures sont payables dans un délai de {{delai_paiement}}, dans les limites de l’article L. 441-10 du Code de commerce. '
         'Escompte : {{escompte}}. Tout retard entraîne de plein droit des pénalités égales au taux de la Banque centrale européenne majoré de dix '
         'points et une indemnité forfaitaire pour frais de recouvrement de quarante euros.',
         '## Livraison', 'Les délais de livraison sont indicatifs. Les risques sont transférés à la livraison.',
         '[[si reserve_propriete]]', '## Réserve de propriété', 'Le vendeur conserve la propriété des biens jusqu’au complet paiement du prix en '
         'principal et accessoires (articles 2367 et suivants du Code civil).', '[[fin]]',
         '[[si garantie]]', '## Garantie', 'Les produits bénéficient d’une garantie contractuelle de [À COMPLÉTER] mois, sans préjudice des '
         'garanties légales applicables entre professionnels.', '[[fin]]',
         '## Responsabilité', 'La responsabilité du vendeur est limitée au montant de la commande concernée, sauf faute lourde ou dolosive.',
         '[[si force_majeure]]', '## Force majeure', 'Aucune partie n’est responsable d’un manquement résultant d’un cas de force majeure au sens de '
         'l’article 1218 du Code civil.', '[[fin]]',
         '## Droit applicable — Litiges', 'Les ventes sont régies par le droit français ; tout litige relève de la compétence des juridictions de '
         '{{tribunal}}.']},
    {'id': 'compte_courant', 'langue': FR, 'droit': 'FR', 'categorie': 'contrat', 'formes': ('SAS', 'SASU', 'SARL', 'EURL', 'SCI', 'SELAS', 'SELARL'),
     'titre': 'Convention de compte courant d’associé', 'resume': _R('Convention d’avance en compte courant : montant, rémunération (limite fiscale), '
                                                                   'blocage, remboursement, conventions réglementées.',
                                                                   'Shareholder current-account agreement (French law).', 'Gesellschafterdarlehen (französisches Recht).',
                                                                   'Préstamo de socio en cuenta corriente (derecho francés).'),
     'mots': ('compte courant', 'avance en compte courant', 'compte courant d’associé'),
     'variables': [('associe', 'Associé prêteur', ''), ('denomination', 'Société', ''), ('siege', 'Siège', ''), ('montant', 'Montant de l’avance', ''),
                   ('taux', 'Taux d’intérêt', 'le taux maximal fiscalement déductible prévu à l’article 39, 1, 3° du Code général des impôts'),
                   ('duree_blocage', 'Durée de blocage', ''), ('ville', 'Lieu', ''), ('date_acte', 'Date', '')],
     'clauses': [('blocage', 'Blocage des sommes', False, ''), ('remuneration', 'Avance rémunérée', True, ''), ('postposition', 'Postposition', False, '')],
     'fixes': {}, 'texte': [
         '# CONVENTION DE COMPTE COURANT D’ASSOCIÉ', '', '# ENTRE :', '{{associe}}, ci-après l’« Associé »,', '# ET :',
         '{{denomination}}, dont le siège est {{siege}}, ci-après la « Société »,', '',
         '## Objet', 'L’Associé met à la disposition de la Société, en compte courant, la somme de {{montant}}.',
         '[[si remuneration]]', '## Rémunération', 'Les sommes portent intérêt au taux de {{taux}}, payable annuellement. [À VÉRIFIER : libération '
         'du capital et limites de déductibilité.]', '[[sinon]]', '## Rémunération', 'L’avance n’est pas rémunérée.', '[[fin]]',
         '[[si blocage]]', '## Blocage', 'L’Associé s’interdit de demander le remboursement des sommes avant {{duree_blocage}}.', '[[fin]]',
         '## Remboursement', 'Hors période de blocage, le remboursement peut être demandé moyennant un préavis de [À COMPLÉTER] mois, sous réserve de '
         'la trésorerie disponible.',
         '[[si postposition]]', '## Postposition', 'L’Associé accepte que le remboursement soit subordonné au désintéressement préalable des '
         'créanciers bancaires désignés en annexe.', '[[fin]]',
         '## Conventions réglementées', 'La présente convention est soumise, s’il y a lieu, à la procédure des conventions réglementées applicable à '
         'la forme de la Société.', '', 'Fait à {{ville}}, le {{date_acte}}, en deux exemplaires.']},
    {'id': 'convention_management', 'langue': FR, 'droit': 'FR', 'categorie': 'contrat', 'formes': ('SAS', 'SARL', 'SELAS', 'SELARL'),
     'titre': 'Convention de prestations de services intragroupe (management fees)',
     'resume': _R('Convention de management fees entre une holding animatrice et sa filiale : services, rémunération de pleine concurrence, '
                  'documentation.', 'Intra-group management services agreement (French law).',
                  'Konzerninterner Dienstleistungsvertrag (französisches Recht).', 'Contrato de servicios intragrupo (derecho francés).'),
     'mots': ('management fees', 'convention de management', 'prestations intragroupe', 'holding animatrice'),
     'variables': [('holding', 'Société prestataire (holding)', ''), ('filiale', 'Société bénéficiaire (filiale)', ''),
                   ('services', 'Services fournis', 'direction générale, stratégie, finance, juridique, ressources humaines'),
                   ('remuneration', 'Rémunération', 'coûts engagés majorés d’une marge de [À COMPLÉTER] %'), ('ville', 'Lieu', ''), ('date_acte', 'Date', '')],
     'clauses': [('reporting', 'Reporting annuel des services rendus', True, '')],
     'fixes': {}, 'texte': [
         '# CONVENTION DE PRESTATIONS DE SERVICES INTRAGROUPE', '', '# ENTRE :', '{{holding}}, ci-après le « Prestataire »,', '# ET :',
         '{{filiale}}, ci-après le « Bénéficiaire »,', '',
         '## Objet', 'Le Prestataire fournit au Bénéficiaire les services suivants : {{services}}, sans s’immiscer dans sa gestion ni se substituer à '
         'ses organes sociaux.',
         '## Rémunération', 'Les services sont rémunérés selon la méthode suivante : {{remuneration}}, conforme au principe de pleine concurrence. '
         '[À VÉRIFIER : documentation des prix de transfert, réalité et intérêt des services pour le Bénéficiaire.]',
         '[[si reporting]]', '## Justification', 'Le Prestataire remet chaque année un rapport détaillant les services rendus et le temps consacré.',
         '[[fin]]',
         '## Durée', 'La convention est conclue pour une durée d’un an, renouvelable tacitement, résiliable moyennant un préavis de trois mois.',
         '## Conventions réglementées', 'La présente convention est soumise, s’il y a lieu, à la procédure des conventions réglementées.', '',
         'Fait à {{ville}}, le {{date_acte}}, en deux exemplaires.']},
]
