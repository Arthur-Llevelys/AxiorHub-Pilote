"""5.6.108 (lot B) : premiers ensembles de trames par droit applicable — À L'ÉTAT DE BROUILLON.

Pour chaque système prioritaire (FR, UK-EAW, DE, ES, CA-QC, CA-ON), un ensemble homogène : questionnaire de mission, note de consultation avec
options, accord de confidentialité, prestation B2B, lettre d'intention, suivi des obligations. Les trames de confidentialité, de prestation et
d'intention existent déjà pour FR, DE, ES et UK-EAW (``bibliotheque_fr5639``, ``bibliotheque_intl5639``) : seules les pièces manquantes sont ajoutées.

Règles de rédaction :
  - chaque trame est rédigée POUR son droit, dans une langue d'usage de ce droit ; aucune n'est la traduction d'un acte français ;
  - les références citées sont des sources de CONTRÔLE (Légifrance, legislation.gov.uk, Gesetze im Internet, BOE, LégisQuébec, lois de l'Ontario,
    lois fédérales du Canada), toujours « à vérifier » à la date de l'acte ; aucun texte protégé n'est reproduit ;
  - aucune trame n'est livrée « validée » : seule une personne habilitée, compétente dans le droit concerné, la fait passer à « à relire » puis
    « validé » (``modeles5684.move``) ; tant qu'elle est en brouillon, elle n'est jamais proposée automatiquement ;
  - conventions d'honoraires, formalités et actes contentieux étrangers : hors de ce premier ensemble ;
  - données fictives seulement (aucun nom, chiffre ou lieu réel).
"""

_R = lambda fr, en, de, es: {'fr': fr, 'en': en, 'de': de, 'es': es}   # noqa: E731
CONSEIL = 'conseil'


def _t(ident, droit, langue, categorie, type_acte, titre, resume, mots, variables, clauses, texte, sources, profils=(), incompatibilites=()):
    return {'id': ident, 'langue': langue, 'droit': droit, 'categorie': categorie, 'formes': (), 'titre': titre, 'resume': resume, 'mots': tuple(mots),
            'variables': list(variables), 'clauses': [(k, lab, on, '') for k, lab, on in clauses], 'fixes': {}, 'texte': list(texte),
            'type_acte': type_acte, 'profils': list(profils), 'sources': list(sources), 'incompatibilites': [list(p) for p in incompatibilites],
            'nouveau56108': True, 'portee': '%s — trame de travail à l’état de brouillon, à relire par un juriste compétent dans ce droit' % droit}


# ================================================================================================ FRANCE (pièces manquantes de l'ensemble)
FR = [
    _t('fr_questionnaire_mission', 'FR', 'fr', CONSEIL, 'questionnaire-mission', 'Questionnaire d’ouverture de mission',
       _R('Identification du client, vigilance, conflits d’intérêts, objet, droit envisagé, échéances et pièces avant la lettre de mission.',
          'Client intake questionnaire (French law): identification, due diligence, conflicts, scope, deadlines.',
          'Mandatsfragebogen (französisches Recht): Identifizierung, Sorgfaltspflichten, Interessenkonflikte, Fristen.',
          'Cuestionario de inicio del encargo (derecho francés): identificación, diligencia debida, conflictos, plazos.'),
       ('questionnaire d’ouverture de mission', 'questionnaire de mission', 'fiche d’ouverture de dossier'),
       [('client', 'Client (nom ou dénomination, forme, immatriculation)', ''), ('interlocuteur', 'Interlocuteur et qualité', ''),
        ('autres_parties', 'Autres parties et adversaires connus', ''), ('objet', 'Objet de la demande', ''),
        ('droit_envisage', 'Droit applicable envisagé', ''), ('juridiction', 'Juridiction ou autorité envisagée', ''),
        ('echeances', 'Échéances et délais connus', ''), ('documents', 'Documents remis', ''), ('date_acte', 'Date', '')],
       [('personne_morale', 'Client personne morale', True), ('international', 'Élément d’extranéité', False),
        ('contentieux', 'Affaire contentieuse ou précontentieuse', False)],
       ['# QUESTIONNAIRE D’OUVERTURE DE MISSION', '', 'Date : {{date_acte}}',
        '## Identification du client', 'Client : {{client}}', 'Interlocuteur et qualité : {{interlocuteur}}',
        '[[si personne_morale]]', 'Représentant légal, justificatif de pouvoir et extrait d’immatriculation récent : [À COMPLÉTER]',
        'Bénéficiaires effectifs déclarés : [À COMPLÉTER]', '[[fin]]',
        '## Vigilance', 'Pièces recueillies au titre des obligations de vigilance de l’avocat, lorsque la mission y est soumise (Code monétaire et '
        'financier, articles L. 561-1 et suivants, à vérifier selon la nature de la mission) : [À COMPLÉTER]',
        '## Conflits d’intérêts', 'Autres parties et adversaires connus : {{autres_parties}}',
        'Vérification des conflits d’intérêts faite le [À COMPLÉTER] par [À COMPLÉTER] ; résultat : [À COMPLÉTER].',
        '## Objet de la mission', '{{objet}}', 'Résultat attendu, contraintes de calendrier, de budget et de confidentialité : [À COMPLÉTER]',
        '## Droit applicable et juridiction', 'Droit applicable envisagé : {{droit_envisage}}. Juridiction ou autorité envisagée : {{juridiction}}.',
        '[[si international]]', 'Pays concernés, langue des actes et intervention d’un conseil local à confirmer : [À COMPLÉTER]', '[[fin]]',
        '[[si contentieux]]', '## Contentieux', 'Juridiction saisie ou à saisir, numéro de rôle, prochaine audience, actes reçus et délais de recours en '
        'cours : [À COMPLÉTER]', '[[fin]]',
        '## Échéances', '{{echeances}}',
        '## Documents', 'Documents remis : {{documents}}', 'Documents à obtenir : [À COMPLÉTER]',
        '## Suites', 'Ces réponses servent à établir la lettre de mission et, s’il y a lieu, la convention d’honoraires, qui font l’objet de documents '
        'distincts.'],
       ['Code monétaire et financier, art. L. 561-1 et s. — Légifrance', 'Règlement intérieur national de la profession d’avocat (conflits d’intérêts)'],
       ('client', 'cabinet')),
    _t('fr_note_consultation', 'FR', 'fr', CONSEIL, 'note-consultation', 'Note de consultation avec options',
       _R('Question, réponse courte, faits retenus, règles applicables, options comparées, recommandation et points à confirmer.',
          'Advice memorandum with options (French law).', 'Beratungsvermerk mit Handlungsoptionen (französisches Recht).',
          'Nota de consulta con opciones (derecho francés).'),
       ('note de consultation', 'consultation avec options', 'note d’analyse avec options'),
       [('destinataire', 'Destinataire', ''), ('auteur', 'Auteur', ''), ('reference', 'Référence du dossier', ''), ('objet', 'Objet', ''),
        ('question', 'Question posée', ''), ('faits', 'Faits retenus et hypothèses', ''), ('date_acte', 'Date', '')],
       [('option_c', 'Troisième option', False), ('urgence', 'Point d’urgence', False)],
       ['# NOTE DE CONSULTATION', '', 'Confidentiel — correspondance entre l’avocat et son client', 'Destinataire : {{destinataire}}',
        'Auteur : {{auteur}}', 'Date : {{date_acte}}', 'Référence : {{reference}}', 'Objet : {{objet}}',
        '## Question posée', '{{question}}',
        '## Réponse en bref', '[À COMPLÉTER : réponse en deux ou trois phrases, avec son degré de certitude]',
        '## Faits retenus et hypothèses', '{{faits}}', 'L’analyse repose sur ces faits et sur les pièces communiquées ; un fait nouveau peut la modifier.',
        '## Règles applicables', '[À COMPLÉTER : textes et décisions avec leur référence, vérifiés à la date de la note sur Légifrance ou la source '
        'officielle]',
        '## Options', 'Option A — [À COMPLÉTER] : avantages, risques, coût et délai estimés.', 'Option B — [À COMPLÉTER] : avantages, risques, coût et '
        'délai estimés.',
        '[[si option_c]]', 'Option C — [À COMPLÉTER] : avantages, risques, coût et délai estimés.', '[[fin]]',
        '## Recommandation', '[À COMPLÉTER : option recommandée, raisons, et circonstances qui rendraient une autre option préférable]',
        '[[si urgence]]', '## Point d’urgence', '[À COMPLÉTER : délai à respecter, mesure conservatoire et dernier jour utile]', '[[fin]]',
        '## Points à confirmer', '[À COMPLÉTER : informations et pièces manquantes]',
        '## Portée', 'La note est établie au regard du droit français en vigueur à sa date ; elle ne vaut pas avis sur un droit étranger.'],
       ['Légifrance — textes et décisions cités, vérifiés à la date de la note'], ('client', 'cabinet')),
    _t('fr_suivi_obligations', 'FR', 'fr', CONSEIL, 'suivi-obligations', 'Tableau de suivi des obligations',
       _R('Obligations du contrat, échéances, preuves d’exécution, renouvellement, garanties, prescription et manquements constatés.',
          'Contract obligations tracker (French law).', 'Übersicht der Vertragspflichten (französisches Recht).',
          'Seguimiento de obligaciones contractuales (derecho francés).'),
       ('suivi des obligations', 'tableau de suivi des obligations', 'suivi d’exécution du contrat'),
       [('contrat', 'Contrat suivi (intitulé, date, parties)', ''), ('parties', 'Parties', ''), ('responsable', 'Responsable du suivi au cabinet', ''),
        ('obligations', 'Obligations (une par ligne : débiteur — obligation — échéance — clause)', ''), ('date_acte', 'Mis à jour le', '')],
       [('renouvellement', 'Échéance de renouvellement ou de résiliation', True), ('garanties', 'Garanties et sûretés à suivre', False)],
       ['# TABLEAU DE SUIVI DES OBLIGATIONS', '', 'Contrat : {{contrat}}', 'Parties : {{parties}}', 'Responsable du suivi : {{responsable}}',
        'Mis à jour le : {{date_acte}}',
        '## Obligations à exécuter', '{{obligations}}', 'Pour chaque obligation : preuve d’exécution attendue, date du constat, statut (à faire, en cours, '
        'exécutée, contestée).',
        '[[si renouvellement]]', '## Renouvellement et résiliation', 'Échéance, préavis et forme de la notification : [À COMPLÉTER]. Rappel à prévoir '
        '[À COMPLÉTER] jours avant le dernier jour utile.', '[[fin]]',
        '[[si garanties]]', '## Garanties et sûretés', 'Garantie, bénéficiaire, montant, date d’expiration, formalités de maintien : [À COMPLÉTER]', '[[fin]]',
        '## Prescription', 'Délai de droit commun de cinq ans (Code civil, article 2224 ; entre commerçants, Code de commerce, article L. 110-4), sous '
        'réserve d’un délai spécial ou d’un aménagement contractuel ; point de départ à vérifier pour chaque obligation.',
        '## Manquements constatés', 'Date, obligation, preuve, mise en demeure envisagée : [À COMPLÉTER]',
        '## Règle de suivi', 'Aucune relance ni mise en demeure ne part de ce tableau : toute action externe est décidée et envoyée par l’avocat.'],
       ['Code civil, art. 2224 ; Code de commerce, art. L. 110-4 — Légifrance'], ('parties au contrat suivi',)),
]

# ================================================================================================ ALLEMAGNE (pièces manquantes)
DE = [
    _t('de_mandatsfragebogen', 'DE', 'de', CONSEIL, 'questionnaire-mission', 'Mandatsfragebogen',
       _R('Questionnaire d’ouverture de mandat de droit allemand : mandant, identification, conflits, objet, délais.',
          'Client intake questionnaire (German law).', 'Mandatsfragebogen: Mandant, Identifizierung, Interessenkollision, Gegenstand, Fristen.',
          'Cuestionario de inicio del encargo (derecho alemán).'),
       ('mandatsfragebogen', 'fragebogen zur mandatsannahme'),
       [('mandant', 'Mandant (Name oder Firma, Rechtsform, Register)', ''), ('ansprechpartner', 'Ansprechpartner und Funktion', ''),
        ('gegner', 'Gegner und weitere Beteiligte', ''), ('gegenstand', 'Gegenstand des Mandats', ''), ('recht', 'Anwendbares Recht (vorläufig)', ''),
        ('gericht', 'Gericht oder Behörde', ''), ('fristen', 'Bekannte Fristen', ''), ('unterlagen', 'Übergebene Unterlagen', ''), ('datum', 'Datum', '')],
       [('juristische_person', 'Mandant ist eine juristische Person', True), ('streitig', 'Streitige Angelegenheit', False)],
       ['# Mandatsfragebogen', '', 'Datum: {{datum}}',
        '## Mandant', 'Mandant: {{mandant}}', 'Ansprechpartner und Funktion: {{ansprechpartner}}',
        '[[si juristische_person]]', 'Vertretungsberechtigte, aktueller Registerauszug und wirtschaftlich Berechtigte: [ZU ERGÄNZEN]', '[[fin]]',
        '## Identifizierung', 'Identifizierung nach dem Geldwäschegesetz, soweit das Mandat in dessen Anwendungsbereich fällt (§§ 10 ff. GwG, zu '
        'prüfen): [ZU ERGÄNZEN]',
        '## Interessenkollision', 'Gegner und weitere Beteiligte: {{gegner}}', 'Kollisionsprüfung (§ 43a Abs. 4 BRAO) am [ZU ERGÄNZEN] durch '
        '[ZU ERGÄNZEN]; Ergebnis: [ZU ERGÄNZEN].',
        '## Gegenstand des Mandats', '{{gegenstand}}', 'Ziel des Mandanten, Budget und Zeitrahmen: [ZU ERGÄNZEN]',
        '## Anwendbares Recht und Zuständigkeit', 'Anwendbares Recht (vorläufig): {{recht}}. Gericht oder Behörde: {{gericht}}.',
        '[[si streitig]]', '## Streitige Angelegenheit', 'Aktenzeichen, Verfahrensstand, nächster Termin, laufende Rechtsmittel- und Begründungsfristen: '
        '[ZU ERGÄNZEN]', '[[fin]]',
        '## Fristen', '{{fristen}}',
        '## Unterlagen', 'Übergeben: {{unterlagen}}', 'Noch anzufordern: [ZU ERGÄNZEN]',
        '## Weiteres Vorgehen', 'Mandatsvereinbarung und gegebenenfalls Vergütungsvereinbarung werden gesondert erstellt.'],
       ['Geldwäschegesetz (GwG) — Gesetze im Internet', 'Bundesrechtsanwaltsordnung (BRAO), § 43a — Gesetze im Internet'], ('Mandant', 'Kanzlei')),
    _t('de_beratungsvermerk', 'DE', 'de', CONSEIL, 'note-consultation', 'Beratungsvermerk mit Handlungsoptionen',
       _R('Note de consultation de droit allemand : question, réponse courte, appréciation, options, recommandation.',
          'Advice memorandum with options (German law).', 'Beratungsvermerk: Fragestellung, Kurzantwort, Würdigung, Handlungsoptionen, Empfehlung.',
          'Nota de consulta con opciones (derecho alemán).'),
       ('beratungsvermerk', 'vermerk mit handlungsoptionen', 'mandantenmemo'),
       [('empfaenger', 'Empfänger', ''), ('verfasser', 'Verfasser', ''), ('aktenzeichen', 'Aktenzeichen', ''), ('betreff', 'Betreff', ''),
        ('frage', 'Fragestellung', ''), ('sachverhalt', 'Sachverhalt und Annahmen', ''), ('datum', 'Datum', '')],
       [('option_c', 'Dritte Option', False), ('eilig', 'Eilbedürftigkeit', False)],
       ['# Beratungsvermerk', '', 'Vertraulich — anwaltliche Beratung', 'An: {{empfaenger}}', 'Von: {{verfasser}}', 'Datum: {{datum}}',
        'Aktenzeichen: {{aktenzeichen}}', 'Betreff: {{betreff}}',
        '## Fragestellung', '{{frage}}',
        '## Kurzantwort', '[ZU ERGÄNZEN: Ergebnis in zwei bis drei Sätzen, mit Grad der Sicherheit]',
        '## Sachverhalt und Annahmen', '{{sachverhalt}}', 'Die Würdigung beruht auf diesem Sachverhalt und den übergebenen Unterlagen; neue Tatsachen '
        'können sie ändern.',
        '## Rechtliche Würdigung', '[ZU ERGÄNZEN: Normen und Entscheidungen mit Fundstelle, zum Datum des Vermerks an der amtlichen Quelle geprüft]',
        '## Handlungsoptionen', 'Option A — [ZU ERGÄNZEN]: Vorteile, Risiken, Kosten und Zeitrahmen.', 'Option B — [ZU ERGÄNZEN]: Vorteile, Risiken, '
        'Kosten und Zeitrahmen.',
        '[[si option_c]]', 'Option C — [ZU ERGÄNZEN]: Vorteile, Risiken, Kosten und Zeitrahmen.', '[[fin]]',
        '## Empfehlung', '[ZU ERGÄNZEN: empfohlene Option und Gründe]',
        '[[si eilig]]', '## Eilbedürftigkeit', '[ZU ERGÄNZEN: einzuhaltende Frist, Sicherungsmaßnahme, letzter Tag]', '[[fin]]',
        '## Offene Punkte', '[ZU ERGÄNZEN: fehlende Informationen und Unterlagen]',
        '## Reichweite', 'Der Vermerk beruht auf deutschem Recht zum Zeitpunkt seiner Erstellung und enthält keine Aussage zu ausländischem Recht.'],
       ['Gesetze im Internet — zitierte Normen, zum Datum des Vermerks geprüft'], ('Mandant', 'Kanzlei')),
    _t('de_pflichtenuebersicht', 'DE', 'de', CONSEIL, 'suivi-obligations', 'Übersicht der Vertragspflichten',
       _R('Suivi des obligations d’un contrat de droit allemand : échéances, preuves, résiliation, sûretés, prescription.',
          'Contract obligations tracker (German law).', 'Pflichtenübersicht: Fristen, Erfüllungsnachweise, Kündigung, Sicherheiten, Verjährung.',
          'Seguimiento de obligaciones (derecho alemán).'),
       ('pflichtenubersicht', 'ubersicht der vertragspflichten', 'vertragsuberwachung'),
       [('vertrag', 'Vertrag (Bezeichnung, Datum, Parteien)', ''), ('parteien', 'Parteien', ''), ('verantwortlich', 'Verantwortlich in der Kanzlei', ''),
        ('pflichten', 'Pflichten (je Zeile: Schuldner — Pflicht — Fälligkeit — Klausel)', ''), ('datum', 'Stand', '')],
       [('kuendigung', 'Laufzeit und Kündigung', True), ('sicherheiten', 'Sicherheiten', False)],
       ['# Übersicht der Vertragspflichten', '', 'Vertrag: {{vertrag}}', 'Parteien: {{parteien}}', 'Verantwortlich: {{verantwortlich}}', 'Stand: {{datum}}',
        '## Zu erfüllende Pflichten', '{{pflichten}}', 'Je Pflicht: erwarteter Erfüllungsnachweis, Datum der Feststellung, Status (offen, in Bearbeitung, '
        'erfüllt, streitig).',
        '[[si kuendigung]]', '## Laufzeit und Kündigung', 'Vertragsende, Kündigungsfrist und Form der Erklärung: [ZU ERGÄNZEN]. Erinnerung [ZU ERGÄNZEN] '
        'Tage vor dem letzten möglichen Tag.', '[[fin]]',
        '[[si sicherheiten]]', '## Sicherheiten', 'Bürgschaft oder Garantie, Begünstigter, Betrag, Ablaufdatum: [ZU ERGÄNZEN]', '[[fin]]',
        '## Verjährung', 'Regelmäßige Verjährungsfrist von drei Jahren ab dem Schluss des Jahres, in dem der Anspruch entstanden ist und der Gläubiger '
        'Kenntnis erlangt hat (§§ 195, 199 BGB), vorbehaltlich besonderer Fristen und vertraglicher Regelungen; Fristberechnung nach §§ 187 ff. BGB — '
        'für jede Pflicht zu prüfen.',
        '## Festgestellte Pflichtverletzungen', 'Datum, Pflicht, Nachweis, beabsichtigte Mahnung oder Fristsetzung: [ZU ERGÄNZEN]',
        '## Regel', 'Aus dieser Übersicht wird weder eine Mahnung noch eine Erklärung versandt: jede Außenwirkung entscheidet und versendet der Anwalt.'],
       ['Bürgerliches Gesetzbuch (BGB), §§ 187 ff., 195, 199 — Gesetze im Internet'], ('Vertragsparteien',)),
]

# ================================================================================================ ESPAGNE (pièces manquantes)
ES = [
    _t('es_cuestionario_encargo', 'ES', 'es', CONSEIL, 'questionnaire-mission', 'Cuestionario de inicio del encargo',
       _R('Questionnaire d’ouverture de mission de droit espagnol : client, diligence, conflits, objet, délais.',
          'Client intake questionnaire (Spanish law).', 'Mandatsfragebogen (spanisches Recht).',
          'Cuestionario de inicio del encargo: cliente, diligencia debida, conflicto de intereses, objeto, plazos.'),
       ('cuestionario de inicio del encargo', 'cuestionario del encargo'),
       [('cliente', 'Cliente (nombre o denominación, forma, registro)', ''), ('contacto', 'Persona de contacto y cargo', ''),
        ('contrarios', 'Partes contrarias y otros intervinientes', ''), ('objeto', 'Objeto del encargo', ''), ('derecho', 'Derecho aplicable previsto', ''),
        ('organo', 'Juzgado, tribunal u organismo', ''), ('plazos', 'Plazos conocidos', ''), ('documentos', 'Documentación entregada', ''), ('fecha', 'Fecha', '')],
       [('persona_juridica', 'Cliente persona jurídica', True), ('litigio', 'Asunto contencioso', False)],
       ['# Cuestionario de inicio del encargo', '', 'Fecha: {{fecha}}',
        '## Cliente', 'Cliente: {{cliente}}', 'Persona de contacto y cargo: {{contacto}}',
        '[[si persona_juridica]]', 'Administradores o apoderados, poder de representación, información registral reciente y titular real: [A COMPLETAR]',
        '[[fin]]',
        '## Identificación y diligencia debida', 'Documentación de identificación y diligencia debida conforme a la Ley 10/2010, de prevención del '
        'blanqueo de capitales, en la medida en que el encargo esté sujeto a ella (a comprobar): [A COMPLETAR]',
        '## Conflicto de intereses', 'Partes contrarias y otros intervinientes: {{contrarios}}', 'Comprobación de conflicto de intereses realizada el '
        '[A COMPLETAR] por [A COMPLETAR]; resultado: [A COMPLETAR].',
        '## Objeto del encargo', '{{objeto}}', 'Objetivo del cliente, presupuesto y calendario: [A COMPLETAR]',
        '## Derecho aplicable y jurisdicción', 'Derecho aplicable previsto: {{derecho}}. Juzgado, tribunal u organismo: {{organo}}.',
        '[[si litigio]]', '## Asunto contencioso', 'Número de procedimiento, estado, próxima vista y plazos procesales en curso: [A COMPLETAR]', '[[fin]]',
        '## Plazos', '{{plazos}}',
        '## Documentación', 'Entregada: {{documentos}}', 'Pendiente de obtener: [A COMPLETAR]',
        '## Siguientes pasos', 'La hoja de encargo y, en su caso, el presupuesto de honorarios se elaboran por separado.'],
       ['Ley 10/2010, de prevención del blanqueo de capitales — BOE', 'Estatuto General de la Abogacía Española — BOE'], ('cliente', 'despacho')),
    _t('es_nota_consulta', 'ES', 'es', CONSEIL, 'note-consultation', 'Nota de consulta con opciones',
       _R('Note de consultation de droit espagnol : question, réponse courte, analyse, options, recommandation.',
          'Advice memorandum with options (Spanish law).', 'Beratungsvermerk mit Handlungsoptionen (spanisches Recht).',
          'Nota de consulta: cuestión, respuesta breve, análisis, opciones y recomendación.'),
       ('nota de consulta', 'nota con opciones', 'informe con opciones'),
       [('destinatario', 'Destinatario', ''), ('autor', 'Autor', ''), ('referencia', 'Referencia del asunto', ''), ('asunto', 'Asunto', ''),
        ('cuestion', 'Cuestión planteada', ''), ('hechos', 'Hechos y premisas', ''), ('fecha', 'Fecha', '')],
       [('opcion_c', 'Tercera opción', False), ('urgente', 'Urgencia', False)],
       ['# Nota de consulta', '', 'Confidencial — asesoramiento jurídico', 'Para: {{destinatario}}', 'De: {{autor}}', 'Fecha: {{fecha}}',
        'Referencia: {{referencia}}', 'Asunto: {{asunto}}',
        '## Cuestión planteada', '{{cuestion}}',
        '## Respuesta breve', '[A COMPLETAR: conclusión en dos o tres frases, con su grado de certeza]',
        '## Hechos y premisas', '{{hechos}}', 'El análisis se basa en estos hechos y en la documentación recibida; un hecho nuevo puede modificarlo.',
        '## Análisis jurídico', '[A COMPLETAR: normas y resoluciones con su referencia, comprobadas en la fuente oficial a la fecha de la nota]',
        '## Opciones', 'Opción A — [A COMPLETAR]: ventajas, riesgos, coste y plazo estimados.', 'Opción B — [A COMPLETAR]: ventajas, riesgos, coste y '
        'plazo estimados.',
        '[[si opcion_c]]', 'Opción C — [A COMPLETAR]: ventajas, riesgos, coste y plazo estimados.', '[[fin]]',
        '## Recomendación', '[A COMPLETAR: opción recomendada y motivos]',
        '[[si urgente]]', '## Urgencia', '[A COMPLETAR: plazo que debe respetarse, medida cautelar y último día]', '[[fin]]',
        '## Puntos pendientes', '[A COMPLETAR: información y documentos que faltan]',
        '## Alcance', 'La nota se refiere al Derecho español vigente a su fecha y no contiene opinión sobre un Derecho extranjero.'],
       ['BOE — normas citadas, comprobadas a la fecha de la nota'], ('cliente', 'despacho')),
    _t('es_seguimiento_obligaciones', 'ES', 'es', CONSEIL, 'suivi-obligations', 'Seguimiento de obligaciones contractuales',
       _R('Suivi des obligations d’un contrat de droit espagnol : échéances, preuves, résiliation, garanties, prescription.',
          'Contract obligations tracker (Spanish law).', 'Übersicht der Vertragspflichten (spanisches Recht).',
          'Seguimiento de obligaciones: vencimientos, prueba del cumplimiento, resolución, garantías y prescripción.'),
       ('seguimiento de obligaciones', 'cuadro de obligaciones'),
       [('contrato', 'Contrato (título, fecha, partes)', ''), ('partes', 'Partes', ''), ('responsable', 'Responsable en el despacho', ''),
        ('obligaciones', 'Obligaciones (una por línea: deudor — obligación — vencimiento — cláusula)', ''), ('fecha', 'Actualizado el', '')],
       [('vencimiento', 'Duración, prórroga y resolución', True), ('garantias', 'Garantías', False)],
       ['# Seguimiento de obligaciones contractuales', '', 'Contrato: {{contrato}}', 'Partes: {{partes}}', 'Responsable: {{responsable}}',
        'Actualizado el: {{fecha}}',
        '## Obligaciones pendientes', '{{obligaciones}}', 'Para cada obligación: prueba del cumplimiento esperada, fecha de constatación y estado '
        '(pendiente, en curso, cumplida, controvertida).',
        '[[si vencimiento]]', '## Duración, prórroga y resolución', 'Fecha de vencimiento, preaviso y forma de la comunicación: [A COMPLETAR]. Aviso '
        '[A COMPLETAR] días antes del último día hábil.', '[[fin]]',
        '[[si garantias]]', '## Garantías', 'Garantía, beneficiario, importe y fecha de vencimiento: [A COMPLETAR]', '[[fin]]',
        '## Prescripción', 'Plazo general de cinco años para las acciones personales sin plazo especial (artículo 1964 del Código Civil), salvo plazo '
        'especial o pacto; día inicial que debe comprobarse para cada obligación.',
        '## Incumplimientos constatados', 'Fecha, obligación, prueba y requerimiento previsto: [A COMPLETAR]',
        '## Regla', 'Desde este cuadro no se envía ningún requerimiento ni comunicación: toda actuación externa la decide y la envía el abogado.'],
       ['Código Civil, art. 1964 — BOE'], ('partes del contrato',)),
]

# ================================================================================================ ANGLETERRE ET PAYS DE GALLES (pièces manquantes)
UK = [
    _t('uk_client_questionnaire', 'UK-EAW', 'en', CONSEIL, 'questionnaire-mission', 'Client Intake Questionnaire',
       _R('Questionnaire d’ouverture de mission (Angleterre et pays de Galles) : client, vigilance, conflits, objet, délais.',
          'Client intake questionnaire (England and Wales): client, due diligence, conflicts, scope, deadlines.',
          'Mandatsfragebogen (englisches Recht).', 'Cuestionario de inicio del encargo (derecho inglés).'),
       ('client intake questionnaire', 'new matter questionnaire'),
       [('client', 'Client (name, legal form, registered number)', ''), ('contact', 'Contact and role', ''),
        ('other_parties', 'Other parties and known opponents', ''), ('scope', 'Scope of the instructions', ''),
        ('governing_law', 'Expected governing law', ''), ('forum', 'Court, tribunal or authority', ''), ('deadlines', 'Known deadlines', ''),
        ('documents', 'Documents received', ''), ('date', 'Date', '')],
       [('entity', 'Client is a company or other entity', True), ('dispute', 'Contentious matter', False)],
       ['# Client Intake Questionnaire', '', 'Date: {{date}}',
        '## Client', 'Client: {{client}}', 'Contact and role: {{contact}}',
        '[[si entity]]', 'Directors or authorised signatories, evidence of authority, current registry extract and persons with significant control: '
        '[TO BE COMPLETED]', '[[fin]]',
        '## Customer due diligence', 'Identification and verification under the Money Laundering, Terrorist Financing and Transfer of Funds '
        '(Information on the Payer) Regulations 2017, where the instructions fall within their scope (to be checked): [TO BE COMPLETED]',
        '## Conflicts of interest', 'Other parties and known opponents: {{other_parties}}', 'Conflict check carried out on [TO BE COMPLETED] by '
        '[TO BE COMPLETED]; outcome: [TO BE COMPLETED].',
        '## Scope', '{{scope}}', 'Client objectives, budget and timetable: [TO BE COMPLETED]',
        '## Governing law and forum', 'Expected governing law: {{governing_law}}. Court, tribunal or authority: {{forum}}.',
        '[[si dispute]]', '## Contentious matter', 'Claim number, stage of proceedings, next hearing and procedural deadlines running: [TO BE COMPLETED]',
        '[[fin]]',
        '## Deadlines', '{{deadlines}}',
        '## Documents', 'Received: {{documents}}', 'Still to obtain: [TO BE COMPLETED]',
        '## Next steps', 'The client care letter and the terms of business are prepared separately.'],
       ['Money Laundering, Terrorist Financing and Transfer of Funds (Information on the Payer) Regulations 2017 — legislation.gov.uk',
        'SRA Standards and Regulations (conflicts of interest) — to be checked'], ('client', 'firm')),
    _t('uk_advice_note', 'UK-EAW', 'en', CONSEIL, 'note-consultation', 'Advice Note with Options',
       _R('Note de consultation (Angleterre et pays de Galles) : question, réponse courte, analyse, options, recommandation.',
          'Advice note (England and Wales): question, short answer, analysis, options and recommendation.',
          'Beratungsvermerk mit Handlungsoptionen (englisches Recht).', 'Nota de consulta con opciones (derecho inglés).'),
       ('advice note', 'advice memorandum', 'options paper'),
       [('to', 'To', ''), ('author', 'From', ''), ('reference', 'Matter reference', ''), ('subject', 'Subject', ''), ('question', 'Question', ''),
        ('facts', 'Facts and assumptions', ''), ('date', 'Date', '')],
       [('option_c', 'Third option', False), ('urgent', 'Urgent step', False)],
       ['# Advice Note', '', 'Privileged and confidential — legal advice', 'To: {{to}}', 'From: {{author}}', 'Date: {{date}}', 'Reference: {{reference}}',
        'Subject: {{subject}}',
        '## Question', '{{question}}',
        '## Short answer', '[TO BE COMPLETED: conclusion in two or three sentences, with the level of confidence]',
        '## Facts and assumptions', '{{facts}}', 'This advice relies on these facts and on the documents provided; new facts may change it.',
        '## Analysis', '[TO BE COMPLETED: legislation and case law with citations, checked against the official source at the date of this note]',
        '## Options', 'Option A — [TO BE COMPLETED]: benefits, risks, estimated cost and timing.', 'Option B — [TO BE COMPLETED]: benefits, risks, '
        'estimated cost and timing.',
        '[[si option_c]]', 'Option C — [TO BE COMPLETED]: benefits, risks, estimated cost and timing.', '[[fin]]',
        '## Recommendation', '[TO BE COMPLETED: recommended option and reasons]',
        '[[si urgent]]', '## Urgent step', '[TO BE COMPLETED: deadline, protective step and last day]', '[[fin]]',
        '## Points to confirm', '[TO BE COMPLETED: missing information and documents]',
        '## Scope', 'This note addresses the law of England and Wales at its date and expresses no view on any other law.'],
       ['legislation.gov.uk — legislation cited, checked at the date of the note'], ('client', 'firm')),
    _t('uk_obligations_tracker', 'UK-EAW', 'en', CONSEIL, 'suivi-obligations', 'Contract Obligations Tracker',
       _R('Suivi des obligations (Angleterre et pays de Galles) : échéances, preuves, résiliation, garanties, prescription.',
          'Contract obligations tracker (England and Wales): deadlines, evidence, termination, security, limitation.',
          'Übersicht der Vertragspflichten (englisches Recht).', 'Seguimiento de obligaciones (derecho inglés).'),
       ('obligations tracker', 'contract tracker', 'obligations schedule'),
       [('contract', 'Contract (title, date, parties)', ''), ('parties', 'Parties', ''), ('owner', 'Responsible person at the firm', ''),
        ('obligations', 'Obligations (one per line: obligor — obligation — due date — clause)', ''), ('date', 'Last updated', '')],
       [('renewal', 'Term, renewal and termination', True), ('security', 'Guarantees and security', False)],
       ['# Contract Obligations Tracker', '', 'Contract: {{contract}}', 'Parties: {{parties}}', 'Responsible: {{owner}}', 'Last updated: {{date}}',
        '## Obligations', '{{obligations}}', 'For each obligation: expected evidence of performance, date checked and status (open, in progress, '
        'performed, disputed).',
        '[[si renewal]]', '## Term, renewal and termination', 'Expiry date, notice period and method of service of notices: [TO BE COMPLETED]. Reminder '
        '[TO BE COMPLETED] days before the last day.', '[[fin]]',
        '[[si security]]', '## Guarantees and security', 'Instrument, beneficiary, amount, expiry and steps to maintain it: [TO BE COMPLETED]', '[[fin]]',
        '## Limitation', 'Six years for a claim in simple contract and twelve years for a claim on a deed (Limitation Act 1980, sections 5 and 8), '
        'subject to any special period or contractual provision; the date on which time starts must be checked for each obligation.',
        '## Breaches noted', 'Date, obligation, evidence and intended letter before action: [TO BE COMPLETED]',
        '## Rule', 'No reminder or notice is sent from this tracker: every external step is decided and sent by the lawyer.'],
       ['Limitation Act 1980, ss. 5 and 8 — legislation.gov.uk'], ('contracting parties',)),
]

# ================================================================================================ QUÉBEC (ensemble complet, en français)
_QC_LOIS = ('## Lois applicables et juridiction', 'Le présent document est régi par les lois en vigueur au Québec et les lois fédérales du Canada qui '
            's’y appliquent. Les parties élisent domicile dans le district judiciaire de {{district}}.',
            '## Langue', 'Les parties confirment avoir voulu que le présent document et les documents qui s’y rattachent soient rédigés en français '
            '[À VÉRIFIER : exigences de la Charte de la langue française selon la nature du contrat].')
QC = [
    _t('qc_questionnaire_mandat', 'CA-QC', 'fr', CONSEIL, 'questionnaire-mission', 'Questionnaire d’ouverture de mandat (Québec)',
       _R('Questionnaire d’ouverture de mandat de droit québécois : client, identification, conflits, objet, langue, délais.',
          'Client intake questionnaire (Québec law).', 'Mandatsfragebogen (Recht von Québec).', 'Cuestionario de inicio del encargo (derecho de Quebec).'),
       ('questionnaire d’ouverture de mandat', 'ouverture de mandat', 'questionnaire de mandat'),
       [('client', 'Client (nom ou dénomination, forme juridique, numéro d’entreprise du Québec)', ''), ('interlocuteur', 'Interlocuteur et fonction', ''),
        ('autres_parties', 'Autres parties et parties adverses connues', ''), ('objet', 'Objet du mandat', ''),
        ('province_matiere', 'Province et matière (droit civil québécois, droit fédéral, autre province)', ''),
        ('tribunal', 'Tribunal ou organisme envisagé', ''), ('delais', 'Délais connus', ''), ('documents', 'Documents remis', ''), ('date_acte', 'Date', '')],
       [('personne_morale', 'Client personne morale', True), ('litige', 'Dossier litigieux', False), ('anglais', 'Documents à produire aussi en anglais', False)],
       ['# QUESTIONNAIRE D’OUVERTURE DE MANDAT', '', 'Date : {{date_acte}}',
        '## Client', 'Client : {{client}}', 'Interlocuteur et fonction : {{interlocuteur}}',
        '[[si personne_morale]]', 'Administrateurs ou dirigeants autorisés, résolution ou preuve du pouvoir de signer, état de renseignements à jour '
        'au registre des entreprises : [À COMPLÉTER]', '[[fin]]',
        '## Identification et vérification', 'Identification et vérification de l’identité du client selon les règles du Barreau du Québec applicables '
        'au mandat (à vérifier) : [À COMPLÉTER]',
        '## Conflits d’intérêts', 'Autres parties et parties adverses connues : {{autres_parties}}', 'Vérification faite le [À COMPLÉTER] par '
        '[À COMPLÉTER] ; résultat : [À COMPLÉTER].',
        '## Objet du mandat', '{{objet}}', 'Résultat recherché, budget et échéancier : [À COMPLÉTER]',
        '## Droit applicable', 'Province et matière : {{province_matiere}}. Tribunal ou organisme envisagé : {{tribunal}}.',
        'Le droit applicable est confirmé avant le choix d’un modèle : droit civil du Québec, droit fédéral ou droit d’une autre province.',
        '[[si anglais]]', '## Langue des documents', 'Documents à produire aussi en anglais : lesquels, et version qui prévaut : [À COMPLÉTER]', '[[fin]]',
        '[[si litige]]', '## Dossier litigieux', 'Numéro de dossier de la cour, étape, prochaine présentation, protocole de l’instance et délais en '
        'cours : [À COMPLÉTER]', '[[fin]]',
        '## Délais', '{{delais}}',
        '## Documents', 'Documents remis : {{documents}}', 'Documents à obtenir : [À COMPLÉTER]',
        '## Suites', 'La lettre de mandat et l’entente d’honoraires sont préparées séparément.'],
       ['Code de déontologie des avocats — LégisQuébec (à vérifier)', 'Règles du Barreau du Québec sur l’identification des clients (à vérifier)'],
       ('client', 'cabinet')),
    _t('qc_note_consultation', 'CA-QC', 'fr', CONSEIL, 'note-consultation', 'Note de consultation avec options (Québec)',
       _R('Note de consultation de droit québécois : question, réponse courte, faits, règles, options, recommandation.',
          'Advice memorandum with options (Québec law).', 'Beratungsvermerk mit Handlungsoptionen (Recht von Québec).',
          'Nota de consulta con opciones (derecho de Quebec).'),
       ('note de consultation', 'avis juridique avec options', 'opinion avec options'),
       [('destinataire', 'Destinataire', ''), ('auteur', 'Auteur', ''), ('reference', 'Référence du dossier', ''), ('objet', 'Objet', ''),
        ('question', 'Question posée', ''), ('faits', 'Faits retenus et hypothèses', ''), ('date_acte', 'Date', '')],
       [('option_c', 'Troisième option', False), ('urgence', 'Point d’urgence', False)],
       ['# NOTE DE CONSULTATION', '', 'Confidentiel — secret professionnel de l’avocat', 'Destinataire : {{destinataire}}', 'Auteur : {{auteur}}',
        'Date : {{date_acte}}', 'Référence : {{reference}}', 'Objet : {{objet}}',
        '## Question', '{{question}}',
        '## Réponse courte', '[À COMPLÉTER : conclusion en deux ou trois phrases, avec son degré de certitude]',
        '## Faits et hypothèses', '{{faits}}', 'L’analyse repose sur ces faits et sur les documents remis ; un fait nouveau peut la modifier.',
        '## Règles applicables', '[À COMPLÉTER : dispositions du Code civil du Québec, lois particulières et décisions, avec leur référence, vérifiées '
        'sur LégisQuébec et les sources officielles à la date de la note ; droit fédéral pertinent le cas échéant]',
        '## Options', 'Option A — [À COMPLÉTER] : avantages, risques, coûts et délais estimés.', 'Option B — [À COMPLÉTER] : avantages, risques, coûts '
        'et délais estimés.',
        '[[si option_c]]', 'Option C — [À COMPLÉTER] : avantages, risques, coûts et délais estimés.', '[[fin]]',
        '## Recommandation', '[À COMPLÉTER : option recommandée et motifs]',
        '[[si urgence]]', '## Point d’urgence', '[À COMPLÉTER : délai, mesure conservatoire, dernier jour utile]', '[[fin]]',
        '## Points à confirmer', '[À COMPLÉTER : renseignements et documents manquants]',
        '## Portée', 'La note porte sur le droit applicable au Québec à sa date ; elle ne vaut pas avis sur le droit d’une autre province ou d’un autre '
        'pays.'],
       ['LégisQuébec — dispositions citées, vérifiées à la date de la note', 'Lois du Canada (Justice Laws) — droit fédéral pertinent'],
       ('client', 'cabinet')),
    _t('qc_entente_confidentialite', 'CA-QC', 'fr', 'contrat', 'confidentialite', 'Entente de confidentialité (Québec)',
       _R('Entente de confidentialité de droit québécois, réciproque ou non, avec renseignements personnels et bonne foi.',
          'Confidentiality agreement (Québec law).', 'Geheimhaltungsvereinbarung (Recht von Québec).', 'Acuerdo de confidencialidad (derecho de Quebec).'),
       ('entente de confidentialité', 'entente de non-divulgation', 'accord de confidentialité', 'nda'),
       [('partie_a', 'Partie A', ''), ('partie_b', 'Partie B', ''), ('projet', 'Projet ou fin permise', ''), ('duree', 'Durée de l’obligation', 'trois ans'),
        ('district', 'District judiciaire', ''), ('lieu', 'Lieu de signature', ''), ('date_acte', 'Date', '')],
       [('reciproque', 'Entente réciproque', True), ('renseignements_personnels', 'Renseignements personnels échangés', False),
        ('non_sollicitation', 'Non-sollicitation du personnel', False)],
       ['# ENTENTE DE CONFIDENTIALITÉ', '', '# ENTRE :', '{{partie_a}}', '# ET :', '{{partie_b}}', '',
        '## Objet', 'Dans le cadre de {{projet}} (le « Projet »), [[si reciproque]]chaque partie[[sinon]]la partie A[[fin]] communique des '
        'renseignements confidentiels. La présente entente fixe les conditions de leur protection.',
        '## Renseignements confidentiels', 'Sont confidentiels les renseignements de toute nature communiqués pour le Projet, sous quelque forme que ce '
        'soit, sauf ceux qui sont devenus publics sans faute du destinataire, qu’il connaissait déjà, qu’il a reçus d’un tiers libre de les '
        'communiquer ou qu’il a développés de façon indépendante.',
        '## Obligations du destinataire', 'Le destinataire n’utilise les renseignements qu’aux fins du Projet, les protège avec au moins le même soin '
        'que ses propres renseignements et ne les communique qu’aux personnes qui doivent les connaître et sont tenues à une obligation équivalente.',
        '[[si renseignements_personnels]]', '## Renseignements personnels', 'Les renseignements personnels échangés sont traités conformément à la Loi '
        'sur la protection des renseignements personnels dans le secteur privé ; le destinataire signale sans délai tout incident de confidentialité '
        '[À VÉRIFIER : obligations applicables à la date de l’entente].', '[[fin]]',
        '[[si non_sollicitation]]', '## Non-sollicitation', 'Pendant le Projet et douze mois après, aucune partie ne sollicite le personnel de l’autre '
        'affecté au Projet [À VÉRIFIER : caractère raisonnable de la durée et de la portée].', '[[fin]]',
        '## Durée', 'Les obligations de confidentialité durent {{duree}} à compter de la dernière communication.',
        '## Remise et destruction', 'À la demande de la partie qui les a communiqués, ou à la fin du Projet, le destinataire remet ou détruit les '
        'renseignements et leurs copies, sous réserve des obligations de conservation prévues par la loi.',
        '## Bonne foi et recours', 'Les parties agissent de bonne foi (Code civil du Québec, articles 6, 7 et 1375). Une contravention peut donner lieu, '
        'outre les dommages-intérêts, à une demande d’injonction.',
        '## Absence de licence', 'Aucune disposition n’emporte cession ou licence d’un droit de propriété intellectuelle.'] + list(_QC_LOIS) +
       ['', 'Signé à {{lieu}}, le {{date_acte}}.'],
       ['Code civil du Québec, art. 6, 7 et 1375 — LégisQuébec', 'Loi sur la protection des renseignements personnels dans le secteur privé — LégisQuébec',
        'Charte de la langue française — LégisQuébec'],
       ('partie divulgatrice', 'partie destinataire')),
    _t('qc_contrat_service', 'CA-QC', 'fr', 'contrat', 'prestation-b2b', 'Contrat de service entre entreprises (Québec)',
       _R('Contrat de service de droit québécois entre entreprises : services, prix et taxes, résiliation, propriété intellectuelle.',
          'Business services contract (Québec law).', 'Dienstleistungsvertrag zwischen Unternehmen (Recht von Québec).',
          'Contrato de servicios entre empresas (derecho de Quebec).'),
       ('contrat de service', 'contrat de services', 'contrat de prestation de services'),
       [('prestataire', 'Prestataire (dénomination, adresse, NEQ)', ''), ('client', 'Client (dénomination, adresse, NEQ)', ''), ('services', 'Services', ''),
        ('prix', 'Prix ou mode de calcul', ''), ('delai_paiement', 'Délai de paiement', 'trente jours suivant la facture'),
        ('taux_interet', 'Taux d’intérêt annuel sur les sommes en retard', ''), ('duree', 'Durée', ''), ('plafond', 'Plafond de responsabilité', ''),
        ('district', 'District judiciaire', ''), ('lieu', 'Lieu de signature', ''), ('date_acte', 'Date', '')],
       [('propriete_intellectuelle', 'Cession des droits sur les livrables', True), ('licence_seule', 'Licence d’utilisation seulement (sans cession)', False),
        ('renseignements_personnels', 'Traitement de renseignements personnels', False), ('sous_traitance', 'Sous-traitance permise', False)],
       ['# CONTRAT DE SERVICE', '', '# ENTRE :', '{{prestataire}}, ci-après appelé le « Prestataire »,', '# ET :', '{{client}}, ci-après appelé le « Client ».', '',
        '## Objet', 'Le Prestataire fournit au Client les services suivants : {{services}} (les « Services »), selon les règles du contrat de service '
        '(Code civil du Québec, articles 2098 et suivants).',
        '## Exécution', 'Le Prestataire choisit les moyens d’exécution, agit avec prudence et diligence et selon les usages et règles de son art ; '
        'il n’existe aucun lien de subordination entre les parties.',
        '## Prix et paiement', 'Le Client paie {{prix}}, plus les taxes applicables (TPS et TVQ), dans un délai de {{delai_paiement}}. Toute somme en '
        'retard porte intérêt au taux annuel de {{taux_interet}}.',
        '## Durée', 'Le contrat dure {{duree}}.',
        '## Résiliation', 'Le Client peut résilier unilatéralement le contrat (Code civil du Québec, article 2125) ; il paie alors les Services rendus '
        'et les frais engagés jusqu’à la résiliation, selon les règles du Code. Le Prestataire ne peut résilier que pour un motif sérieux et non à '
        'contretemps (article 2126) [À VÉRIFIER : portée de tout aménagement conventionnel de ces règles].',
        '[[si propriete_intellectuelle]]', '## Propriété intellectuelle', 'Sur paiement complet, le Prestataire cède au Client les droits de propriété '
        'intellectuelle sur les livrables créés pour lui et renonce, dans la mesure permise, à l’exercice de ses droits moraux [À VÉRIFIER : formalités '
        'de la Loi sur le droit d’auteur (Canada)].', '[[fin]]',
        '[[si licence_seule]]', '## Licence', 'Le Prestataire conserve ses droits sur les livrables et concède au Client une licence non exclusive, '
        'pour ses besoins internes, pendant la durée de protection.', '[[fin]]',
        '[[si renseignements_personnels]]', '## Renseignements personnels', 'Le Prestataire traite les renseignements personnels confiés par le Client '
        'uniquement pour les Services, conformément à la Loi sur la protection des renseignements personnels dans le secteur privé, et met en place '
        'des mesures de sécurité raisonnables.', '[[fin]]',
        '[[si sous_traitance]]', '## Sous-traitance', 'Le Prestataire peut confier une partie des Services à un tiers, sous sa responsabilité.', '[[fin]]',
        '## Responsabilité', 'Sous réserve des cas où la loi interdit d’exclure ou de limiter la responsabilité (notamment le préjudice corporel ou '
        'moral et la faute intentionnelle ou lourde, Code civil du Québec, article 1474), la responsabilité totale de chaque partie est limitée à '
        '{{plafond}}.',
        '## Confidentialité', 'Chaque partie garde confidentiels les renseignements de l’autre obtenus pour le contrat, pendant sa durée et trois ans '
        'après.'] + list(_QC_LOIS) + ['', 'Signé à {{lieu}}, le {{date_acte}}.'],
       ['Code civil du Québec, art. 1474, 2098 et s., 2125, 2126 — LégisQuébec',
        'Loi sur la protection des renseignements personnels dans le secteur privé — LégisQuébec', 'Loi sur le droit d’auteur (Canada) — Lois du Canada',
        'Charte de la langue française — LégisQuébec'],
       ('prestataire (entreprise)', 'client (entreprise)'), (('propriete_intellectuelle', 'licence_seule'),)),
    _t('qc_lettre_intention', 'CA-QC', 'fr', 'operation', 'lettre-intention', 'Lettre d’intention (Québec)',
       _R('Lettre d’intention de droit québécois : opération envisagée, conditions, exclusivité et confidentialité seules obligatoires.',
          'Letter of intent (Québec law).', 'Absichtserklärung (Recht von Québec).', 'Carta de intenciones (derecho de Quebec).'),
       ('lettre d’intention', 'lettre d’intentions', 'lettre d’entente préliminaire'),
       [('emetteur', 'Émetteur', ''), ('destinataire', 'Destinataire', ''), ('operation', 'Opération envisagée', ''), ('prix', 'Prix indicatif', ''),
        ('conditions', 'Conditions principales (vérification diligente, financement, autorisations)', ''), ('exclusivite', 'Durée de l’exclusivité', '60 jours'),
        ('district', 'District judiciaire', ''), ('lieu', 'Lieu', ''), ('date_acte', 'Date', '')],
       [('exclusivite_clause', 'Exclusivité (obligatoire)', True), ('frais', 'Partage des frais', False)],
       ['# LETTRE D’INTENTION', '', 'De : {{emetteur}}', 'À : {{destinataire}}', 'Date : {{date_acte}}', '',
        '## Opération envisagée', '{{operation}}, pour un prix indicatif de {{prix}}, sous réserve des conditions ci-dessous et de la signature d’une '
        'convention définitive.',
        '## Conditions', '{{conditions}}',
        '## Nature de la lettre', 'Sauf les sections Exclusivité, Confidentialité, Frais et Lois applicables, la présente lettre n’oblige aucune partie '
        'à conclure l’opération. Les parties poursuivent leurs négociations de bonne foi (Code civil du Québec, articles 6, 7 et 1375).',
        '[[si exclusivite_clause]]', '## Exclusivité', 'Pendant {{exclusivite}}, le destinataire ne sollicite ni ne négocie d’opération concurrente. '
        'Cette section lie les parties.', '[[fin]]',
        '## Confidentialité', 'Les renseignements échangés restent confidentiels selon l’entente de confidentialité conclue entre les parties ou, à '
        'défaut, selon la présente section, qui lie les parties.',
        '[[si frais]]', '## Frais', 'Chaque partie assume ses frais, sauf [À COMPLÉTER].', '[[fin]]'] + list(_QC_LOIS) +
       ['', 'Signé à {{lieu}}, le {{date_acte}}.'],
       ['Code civil du Québec, art. 6, 7 et 1375 — LégisQuébec', 'Charte de la langue française — LégisQuébec'], ('acquéreur', 'cédant')),
    _t('qc_suivi_obligations', 'CA-QC', 'fr', CONSEIL, 'suivi-obligations', 'Tableau de suivi des obligations (Québec)',
       _R('Suivi des obligations d’un contrat de droit québécois : échéances, preuves, mise en demeure, garanties, prescription.',
          'Contract obligations tracker (Québec law).', 'Übersicht der Vertragspflichten (Recht von Québec).',
          'Seguimiento de obligaciones (derecho de Quebec).'),
       ('suivi des obligations', 'tableau de suivi des obligations'),
       [('contrat', 'Contrat suivi (titre, date, parties)', ''), ('parties', 'Parties', ''), ('responsable', 'Responsable du suivi', ''),
        ('obligations', 'Obligations (une par ligne : débiteur — obligation — échéance — clause)', ''), ('date_acte', 'Mis à jour le', '')],
       [('renouvellement', 'Durée, renouvellement et résiliation', True), ('suretes', 'Sûretés et garanties', False)],
       ['# TABLEAU DE SUIVI DES OBLIGATIONS', '', 'Contrat : {{contrat}}', 'Parties : {{parties}}', 'Responsable : {{responsable}}',
        'Mis à jour le : {{date_acte}}',
        '## Obligations à exécuter', '{{obligations}}', 'Pour chaque obligation : preuve d’exécution attendue, date de vérification et état (à faire, en '
        'cours, exécutée, contestée).',
        '[[si renouvellement]]', '## Durée, renouvellement et résiliation', 'Échéance, préavis, mode d’avis : [À COMPLÉTER]. Rappel [À COMPLÉTER] jours '
        'avant le dernier jour utile.', '[[fin]]',
        '[[si suretes]]', '## Sûretés et garanties', 'Sûreté ou garantie, bénéficiaire, montant, inscription au registre et date d’échéance : '
        '[À COMPLÉTER]', '[[fin]]',
        '## Prescription', 'Délai de trois ans pour faire valoir un droit personnel (Code civil du Québec, article 2925), sous réserve d’un délai '
        'particulier ; point de départ à vérifier pour chaque obligation.',
        '## Manquements constatés', 'Date, obligation, preuve, mise en demeure envisagée (Code civil du Québec, articles 1594 et suivants) : '
        '[À COMPLÉTER]',
        '## Règle de suivi', 'Aucune mise en demeure ni aucun avis ne part de ce tableau : toute action externe est décidée et envoyée par l’avocat.'],
       ['Code civil du Québec, art. 1594 et s., 2925 — LégisQuébec'], ('parties au contrat suivi',)),
]

# ================================================================================================ ONTARIO (ensemble complet, en anglais)
_ON_LAW = ('## Governing law', 'This agreement is governed by the laws of the Province of Ontario and the federal laws of Canada applicable '
           'therein. The parties attorn to the non-exclusive jurisdiction of the courts of Ontario.')
ON = [
    _t('on_client_questionnaire', 'CA-ON', 'en', CONSEIL, 'questionnaire-mission', 'Client Intake Questionnaire (Ontario)',
       _R('Questionnaire d’ouverture de mandat (Ontario) : client, identification, conflits, objet, délais.',
          'Client intake questionnaire (Ontario): client identification, conflicts, scope, deadlines.', 'Mandatsfragebogen (Recht von Ontario).',
          'Cuestionario de inicio del encargo (derecho de Ontario).'),
       ('client intake questionnaire', 'new matter questionnaire', 'intake questionnaire'),
       [('client', 'Client (name, legal form, corporation number)', ''), ('contact', 'Contact and role', ''),
        ('other_parties', 'Other parties and known opponents', ''), ('scope', 'Scope of the retainer', ''),
        ('jurisdiction', 'Province and area of law (Ontario, federal, other province)', ''), ('forum', 'Court, tribunal or agency', ''),
        ('deadlines', 'Known deadlines', ''), ('documents', 'Documents received', ''), ('date', 'Date', '')],
       [('organization', 'Client is an organization', True), ('dispute', 'Contentious matter', False)],
       ['# Client Intake Questionnaire', '', 'Date: {{date}}',
        '## Client', 'Client: {{client}}', 'Contact and role: {{contact}}',
        '[[si organization]]', 'Directors and officers, signing authority, current corporate profile and individuals with significant control: '
        '[TO BE COMPLETED]', '[[fin]]',
        '## Identification and verification', 'Client identification and verification under the Law Society of Ontario by-laws, where applicable '
        '(to be checked): [TO BE COMPLETED]',
        '## Conflicts of interest', 'Other parties and known opponents: {{other_parties}}', 'Conflict search carried out on [TO BE COMPLETED] by '
        '[TO BE COMPLETED]; result: [TO BE COMPLETED].',
        '## Scope of the retainer', '{{scope}}', 'Client objectives, budget and timetable: [TO BE COMPLETED]',
        '## Applicable law', 'Province and area of law: {{jurisdiction}}. Court, tribunal or agency: {{forum}}.',
        'The applicable law (Ontario, federal or another province) is confirmed before a precedent is chosen.',
        '[[si dispute]]', '## Contentious matter', 'Court file number, stage, next appearance and limitation or procedural deadlines running: '
        '[TO BE COMPLETED]', '[[fin]]',
        '## Deadlines', '{{deadlines}}',
        '## Documents', 'Received: {{documents}}', 'Still to obtain: [TO BE COMPLETED]',
        '## Next steps', 'The retainer agreement is prepared separately.'],
       ['Law Society of Ontario — by-laws and Rules of Professional Conduct (to be checked)', 'Ontario e-Laws — https://www.ontario.ca/laws'],
       ('client', 'firm')),
    _t('on_advice_memo', 'CA-ON', 'en', CONSEIL, 'note-consultation', 'Advice Memorandum with Options (Ontario)',
       _R('Note de consultation (Ontario) : question, réponse courte, analyse, options, recommandation.',
          'Advice memorandum (Ontario): question, short answer, analysis, options and recommendation.',
          'Beratungsvermerk mit Handlungsoptionen (Recht von Ontario).', 'Nota de consulta con opciones (derecho de Ontario).'),
       ('advice memorandum', 'opinion memo', 'options memo'),
       [('to', 'To', ''), ('author', 'From', ''), ('reference', 'File reference', ''), ('subject', 'Re', ''), ('question', 'Issue', ''),
        ('facts', 'Facts and assumptions', ''), ('date', 'Date', '')],
       [('option_c', 'Third option', False), ('urgent', 'Urgent step', False)],
       ['# Advice Memorandum', '', 'Privileged and confidential — solicitor-client communication', 'To: {{to}}', 'From: {{author}}', 'Date: {{date}}',
        'File: {{reference}}', 'Re: {{subject}}',
        '## Issue', '{{question}}',
        '## Short answer', '[TO BE COMPLETED: conclusion in two or three sentences, with the level of confidence]',
        '## Facts and assumptions', '{{facts}}', 'This advice relies on these facts and on the documents provided; new facts may change it.',
        '## Analysis', '[TO BE COMPLETED: Ontario statutes and regulations, applicable federal law and case law, with citations checked against the '
        'official sources at the date of this memorandum]',
        '## Options', 'Option A — [TO BE COMPLETED]: benefits, risks, estimated cost and timing.', 'Option B — [TO BE COMPLETED]: benefits, risks, '
        'estimated cost and timing.',
        '[[si option_c]]', 'Option C — [TO BE COMPLETED]: benefits, risks, estimated cost and timing.', '[[fin]]',
        '## Recommendation', '[TO BE COMPLETED: recommended option and reasons]',
        '[[si urgent]]', '## Urgent step', '[TO BE COMPLETED: limitation or deadline, protective step and last day]', '[[fin]]',
        '## Points to confirm', '[TO BE COMPLETED: missing information and documents]',
        '## Scope', 'This memorandum addresses the laws of Ontario and the federal laws of Canada applicable therein at its date; it expresses no view '
        'on the law of any other province or country.'],
       ['Ontario e-Laws — https://www.ontario.ca/laws', 'Justice Laws (Canada) — https://laws-lois.justice.gc.ca/'], ('client', 'firm')),
    _t('on_nda', 'CA-ON', 'en', 'contrat', 'confidentialite', 'Confidentiality Agreement (Ontario)',
       _R('Accord de confidentialité régi par le droit de l’Ontario, réciproque ou non.', 'Confidentiality agreement governed by Ontario law.',
          'Geheimhaltungsvereinbarung (Recht von Ontario).', 'Acuerdo de confidencialidad (derecho de Ontario).'),
       ('confidentiality agreement', 'non-disclosure agreement', 'nda'),
       [('party_a', 'Party A', ''), ('party_b', 'Party B', ''), ('purpose', 'Permitted purpose', ''), ('term', 'Term of the obligations', 'three years'),
        ('date', 'Date', '')],
       [('mutual', 'Mutual', True), ('personal_information', 'Personal information exchanged', False), ('non_solicit', 'Non-solicitation of employees', False)],
       ['# Confidentiality Agreement', '', 'This agreement is dated {{date}} between {{party_a}} and {{party_b}}.', '',
        '## Purpose', '[[si mutual]]Each party[[sinon]]Party A[[fin]] will disclose confidential information solely for {{purpose}} (the "Purpose").',
        '## Confidential information', 'Confidential information means all information disclosed for the Purpose in any form, except information that is '
        'or becomes public through no fault of the recipient, was already known to it, is received from a third party free to disclose it, or is '
        'independently developed.',
        '## Obligations', 'The recipient shall use confidential information only for the Purpose, protect it with at least reasonable care and '
        'disclose it only to its representatives who need to know it and are bound by equivalent obligations.',
        '[[si personal_information]]', '## Personal information', 'Personal information exchanged shall be handled in accordance with applicable privacy '
        'legislation, including the Personal Information Protection and Electronic Documents Act (Canada) where it applies [TO BE CHECKED].', '[[fin]]',
        '[[si non_solicit]]', '## Non-solicitation', 'During the Purpose and for twelve months after, neither party shall solicit the employees of the '
        'other assigned to the Purpose [TO BE CHECKED: reasonableness of scope and duration].', '[[fin]]',
        '## Term', 'The obligations continue for {{term}} after the last disclosure.',
        '## Return or destruction', 'On request, or at the end of the Purpose, the recipient shall return or destroy the confidential information, '
        'subject to legal retention requirements.',
        '## Remedies', 'Damages may not be an adequate remedy; the disclosing party may seek injunctive relief in addition to any other remedy.',
        '## No licence', 'Nothing in this agreement grants any licence or other right in the confidential information.', _ON_LAW[0], _ON_LAW[1]],
       ['Personal Information Protection and Electronic Documents Act — Justice Laws (Canada)', 'Ontario e-Laws — https://www.ontario.ca/laws'],
       ('disclosing party', 'receiving party')),
    _t('on_services_agreement', 'CA-ON', 'en', 'contrat', 'prestation-b2b', 'Services Agreement (Ontario)',
       _R('Contrat de prestation de services entre entreprises régi par le droit de l’Ontario.', 'Business-to-business services agreement (Ontario).',
          'Dienstleistungsvertrag zwischen Unternehmen (Recht von Ontario).', 'Contrato de servicios entre empresas (derecho de Ontario).'),
       ('services agreement', 'service agreement', 'consulting agreement'),
       [('supplier', 'Supplier', ''), ('customer', 'Customer', ''), ('services', 'Services', ''), ('fees', 'Fees', ''),
        ('payment_terms', 'Payment terms', '30 days from invoice'), ('interest_rate', 'Interest on overdue amounts (annual rate)', ''),
        ('term', 'Term', ''), ('cap', 'Liability cap', ''), ('date', 'Date', '')],
       [('ip_assignment', 'Assignment of rights in deliverables', True), ('licence_only', 'Licence only (no assignment)', False),
        ('personal_information', 'Processing of personal information', False)],
       ['# Services Agreement', '', 'This agreement is dated {{date}} between {{supplier}} (the "Supplier") and {{customer}} (the "Customer").', '',
        '## Services', 'The Supplier shall provide the following services in a professional and workmanlike manner: {{services}}.',
        '## Fees and payment', 'The Customer shall pay {{fees}} plus applicable HST within {{payment_terms}}. Overdue amounts bear interest at '
        '{{interest_rate}} per year [TO BE CHECKED: Interest Act (Canada)].',
        '## Term and termination', 'This agreement continues for {{term}}. Either party may terminate it on thirty days’ written notice if the other '
        'party fails to remedy a material breach within that period.',
        '[[si ip_assignment]]', '## Intellectual property', 'Upon full payment, the Supplier assigns to the Customer all rights in the deliverables created '
        'for the Customer and shall obtain waivers of moral rights from their authors [TO BE CHECKED: Copyright Act (Canada)].', '[[fin]]',
        '[[si licence_only]]', '## Licence', 'The Supplier retains ownership of the deliverables and grants the Customer a non-exclusive licence to use '
        'them for its internal business purposes.', '[[fin]]',
        '[[si personal_information]]', '## Personal information', 'The Supplier shall process personal information received from the Customer only to '
        'perform the services, protect it with appropriate safeguards and comply with applicable privacy legislation.', '[[fin]]',
        '## Limitation of liability', 'Except for liability that cannot be limited at law, each party’s total liability under this agreement shall not '
        'exceed {{cap}}, and neither party is liable for indirect or consequential loss.',
        '## Good faith', 'Each party shall perform its obligations honestly and in good faith.',
        '## Independent contractor', 'The Supplier is an independent contractor; nothing creates an employment, partnership or agency relationship.',
        _ON_LAW[0], _ON_LAW[1]],
       ['Ontario e-Laws — https://www.ontario.ca/laws', 'Interest Act; Copyright Act — Justice Laws (Canada)'],
       ('supplier (business)', 'customer (business)'), (('ip_assignment', 'licence_only'),)),
    _t('on_letter_of_intent', 'CA-ON', 'en', 'operation', 'lettre-intention', 'Letter of Intent (Ontario)',
       _R('Lettre d’intention régie par le droit de l’Ontario : opération envisagée, exclusivité et confidentialité seules obligatoires.',
          'Letter of intent (Ontario): proposed transaction, binding exclusivity and confidentiality only.', 'Absichtserklärung (Recht von Ontario).',
          'Carta de intenciones (derecho de Ontario).'),
       ('letter of intent', 'term sheet', 'heads of agreement'),
       [('buyer', 'Buyer', ''), ('seller', 'Seller', ''), ('transaction', 'Proposed transaction', ''), ('price', 'Indicative price', ''),
        ('conditions', 'Key conditions (due diligence, financing, approvals)', ''), ('exclusivity', 'Exclusivity period', '60 days'), ('date', 'Date', '')],
       [('exclusivity_clause', 'Exclusivity (binding)', True), ('costs', 'Costs', False)],
       ['# Letter of Intent', '', 'Date: {{date}}', 'Buyer: {{buyer}}', 'Seller: {{seller}}', '',
        '## Proposed transaction', '{{transaction}}, for an indicative price of {{price}}, subject to the conditions below and to a definitive agreement.',
        '## Conditions', '{{conditions}}',
        '## Non-binding nature', 'Except for the sections on exclusivity, confidentiality, costs and governing law, this letter does not create any '
        'binding obligation, and neither party is obliged to complete the transaction.',
        '[[si exclusivity_clause]]', '## Exclusivity', 'For {{exclusivity}}, the Seller shall not solicit or negotiate any competing transaction. This '
        'section is binding.', '[[fin]]',
        '## Confidentiality', 'Information exchanged remains confidential under the parties’ confidentiality agreement or, failing one, under this '
        'section, which is binding.',
        '[[si costs]]', '## Costs', 'Each party bears its own costs, except [TO BE COMPLETED].', '[[fin]]', _ON_LAW[0], _ON_LAW[1]],
       ['Ontario e-Laws — https://www.ontario.ca/laws'], ('buyer', 'seller')),
    _t('on_obligations_tracker', 'CA-ON', 'en', CONSEIL, 'suivi-obligations', 'Contract Obligations Tracker (Ontario)',
       _R('Suivi des obligations d’un contrat régi par le droit de l’Ontario : échéances, preuves, résiliation, sûretés, prescription.',
          'Contract obligations tracker (Ontario): deadlines, evidence, termination, security, limitation periods.',
          'Übersicht der Vertragspflichten (Recht von Ontario).', 'Seguimiento de obligaciones (derecho de Ontario).'),
       ('obligations tracker', 'contract tracker'),
       [('contract', 'Contract (title, date, parties)', ''), ('parties', 'Parties', ''), ('owner', 'Responsible person', ''),
        ('obligations', 'Obligations (one per line: obligor — obligation — due date — clause)', ''), ('date', 'Last updated', '')],
       [('renewal', 'Term, renewal and termination', True), ('security', 'Guarantees and security (PPSA registration)', False)],
       ['# Contract Obligations Tracker', '', 'Contract: {{contract}}', 'Parties: {{parties}}', 'Responsible: {{owner}}', 'Last updated: {{date}}',
        '## Obligations', '{{obligations}}', 'For each obligation: expected evidence of performance, date checked and status (open, in progress, '
        'performed, disputed).',
        '[[si renewal]]', '## Term, renewal and termination', 'Expiry date, notice period and method of notice: [TO BE COMPLETED]. Reminder '
        '[TO BE COMPLETED] days before the last day.', '[[fin]]',
        '[[si security]]', '## Guarantees and security', 'Instrument, beneficiary, amount, registration and expiry: [TO BE COMPLETED]', '[[fin]]',
        '## Limitation periods', 'Basic two-year limitation period from the day the claim is discovered and ultimate fifteen-year period (Limitations '
        'Act, 2002 (Ontario), sections 4 and 15), subject to any special period; the start date must be checked for each obligation.',
        '## Breaches noted', 'Date, obligation, evidence and intended demand letter: [TO BE COMPLETED]',
        '## Rule', 'No reminder or notice is sent from this tracker: every external step is decided and sent by the lawyer.'],
       ['Limitations Act, 2002 (Ontario), ss. 4 and 15 — https://www.ontario.ca/laws'], ('contracting parties',)),
]

TEMPLATES = FR + DE + ES + UK + QC + ON


# ================================================================================================ 5.6.111 (lot K, F06) : familles Conseil manquantes, droit français
# Trames de travail À L'ÉTAT DE BROUILLON (``nouveau56108`` : jamais proposées automatiquement), rédigées pour le droit français à partir des
# questionnaires proposés pour la 5.6.111, relus et ramenés au format de la bibliothèque. Les références citées sont des sources de CONTRÔLE, à
# vérifier à la date de l'acte ; elles ne valent pas validation. Liste distincte de ``TEMPLATES`` (ensemble 5.6.108 inchangé).
_FIN = ['', 'Fait à {{ville}}, le {{date_acte}}, en deux exemplaires.']
FR56111 = [
    _t('fr_note_analyse_contrat', 'FR', 'fr', CONSEIL, 'analyse-contrat', 'Note d’analyse et de négociation d’un contrat',
       _R('Contrat reçu, partie assistée, objectifs, points critiques avec propositions et positions de repli, clauses à ajouter, stratégie.',
          'Contract review and negotiation memo (French law).', 'Vermerk zur Vertragsprüfung und Verhandlung (französisches Recht).',
          'Nota de análisis y negociación de un contrato (derecho francés).'),
       ('analyse de contrat', 'note d’analyse de contrat', 'revue de contrat', 'points de négociation'),
       [('destinataire', 'Destinataire', ''), ('auteur', 'Auteur', ''), ('reference', 'Référence du dossier', ''),
        ('contrat_analyse', 'Contrat analysé (intitulé, version reçue, date, émetteur)', ''), ('partie_assistee', 'Partie assistée par le cabinet', ''),
        ('contrepartie', 'Cocontractant', ''), ('objectifs', 'Objectifs et limites fixés par le client', ''), ('date_acte', 'Date', '')],
       [('tableau_modifications', 'Tableau des modifications proposées joint', True), ('urgence', 'Date limite de négociation', False)],
       ['# NOTE D’ANALYSE ET DE NÉGOCIATION', '', 'Confidentiel — correspondance entre l’avocat et son client', 'Destinataire : {{destinataire}}',
        'Auteur : {{auteur}}', 'Date : {{date_acte}}', 'Référence : {{reference}}',
        '## Contrat analysé', '{{contrat_analyse}}', 'L’analyse porte sur cette version ; une nouvelle version appelle une nouvelle lecture.',
        '## Partie assistée et objectifs', 'Partie assistée : {{partie_assistee}}. Cocontractant : {{contrepartie}}.', 'Objectifs et limites : {{objectifs}}',
        '## Synthèse', '[À COMPLÉTER : appréciation d’ensemble en quelques phrases et points bloquants]',
        '## Points critiques', '[À COMPLÉTER : pour chaque clause — référence, risque pour le client, proposition de rédaction, position de repli]',
        '## Clauses absentes ou à ajouter', '[À COMPLÉTER : stipulations utiles au client qui manquent au projet]',
        '## Règles impératives à vérifier', '[À COMPLÉTER : dispositions d’ordre public susceptibles d’écarter une clause, vérifiées sur Légifrance à la '
        'date de la note]',
        '[[si urgence]]', '## Date limite', '[À COMPLÉTER : date imposée par le cocontractant ou le calendrier de l’opération]', '[[fin]]',
        '## Stratégie de négociation', '[À COMPLÉTER : ordre de présentation des demandes, concessions possibles, points non négociables]',
        '[[si tableau_modifications]]', '## Tableau des modifications', 'Le tableau joint reprend, clause par clause, la rédaction reçue, la rédaction '
        'proposée et sa justification.', '[[fin]]',
        '## Points à confirmer', '[À COMPLÉTER : informations attendues du client]',
        '## Portée', 'La note est établie au regard du droit français en vigueur à sa date et des seuls documents communiqués.'],
       ['Légifrance — textes cités, vérifiés à la date de la note'], ('client', 'cabinet')),
    _t('fr_cgv_consommateurs', 'FR', 'fr', 'contrat', 'cgv-b2c', 'Conditions générales de vente aux consommateurs',
       _R('CGV destinées aux consommateurs : information, commande, prix, livraison, rétractation, garanties légales, médiation.',
          'Consumer terms and conditions of sale (French law).', 'Allgemeine Verkaufsbedingungen für Verbraucher (französisches Recht).',
          'Condiciones generales de venta a consumidores (derecho francés).'),
       ('conditions générales de vente aux consommateurs', 'cgv consommateurs', 'cgv grand public'),
       [('vendeur', 'Vendeur (dénomination, forme, capital, siège, RCS, coordonnées)', ''), ('produits', 'Produits ou services vendus', ''),
        ('modes_paiement', 'Moyens de paiement acceptés', ''), ('delai_livraison', 'Délai de livraison annoncé', ''),
        ('service_client', 'Service client (adresse, courriel, téléphone)', ''), ('mediateur', 'Médiateur de la consommation (nom, site internet)', '')],
       [('distance', 'Vente à distance ou hors établissement (droit de rétractation)', True), ('garantie_commerciale', 'Garantie commerciale', False),
        ('reserve_propriete', 'Réserve de propriété', False)],
       ['# CONDITIONS GÉNÉRALES DE VENTE', '{{vendeur}}',
        '## Champ d’application', 'Les présentes conditions s’appliquent aux ventes de {{produits}} conclues avec des consommateurs et des '
        'non-professionnels. Elles sont communiquées avant la commande et acceptées lors de celle-ci.',
        '## Information précontractuelle', 'Avant la commande, l’acheteur reçoit les informations prévues aux articles L. 111-1 et suivants du Code de '
        'la consommation (caractéristiques essentielles, prix, délai de livraison, garanties, coordonnées du vendeur) [À VÉRIFIER : informations '
        'propres à la vente à distance].',
        '## Prix', 'Les prix sont indiqués en euros toutes taxes comprises ; les frais de livraison sont précisés avant la validation de la commande.',
        '## Commande', 'La commande est définitive après récapitulatif, possibilité de corriger les erreurs et confirmation par l’acheteur ; le vendeur '
        'en accuse réception sans délai [À VÉRIFIER : commande par voie électronique, articles 1127-1 et 1127-2 du Code civil].',
        '## Paiement', 'Moyens de paiement acceptés : {{modes_paiement}}.',
        '[[si reserve_propriete]]', '## Réserve de propriété', 'Le vendeur reste propriétaire des biens jusqu’au paiement complet du prix.', '[[fin]]',
        '## Livraison', 'Les biens sont livrés dans un délai de {{delai_livraison}}. À défaut d’indication, la livraison intervient au plus tard trente '
        'jours après la conclusion du contrat (article L. 216-1 du Code de la consommation) [À VÉRIFIER : texte en vigueur à la date des CGV]. Le '
        'risque de perte ou d’endommagement est transféré à l’acheteur lorsqu’il prend physiquement possession des biens (article L. 216-4).',
        '[[si distance]]', '## Droit de rétractation', 'L’acheteur dispose d’un délai de quatorze jours pour se rétracter, sans motif (articles L. 221-18 '
        'et suivants du Code de la consommation), sous réserve des exceptions de l’article L. 221-28. Le formulaire type de rétractation est annexé '
        '[À COMPLÉTER : modalités de retour et frais].', '[[fin]]',
        '## Garanties légales', 'Le vendeur répond des défauts de conformité (articles L. 217-3 et suivants du Code de la consommation) et des vices '
        'cachés (articles 1641 et suivants du Code civil) dans les conditions de la loi [À VÉRIFIER : mentions à reproduire].',
        '[[si garantie_commerciale]]', '## Garantie commerciale', 'Une garantie commerciale est consentie : [À COMPLÉTER : contenu, durée, étendue, '
        'conditions de mise en œuvre]. Elle s’ajoute aux garanties légales.', '[[fin]]',
        '## Service client et réclamations', 'Réclamations : {{service_client}}.',
        '## Médiation', 'En cas de litige, l’acheteur peut recourir gratuitement au médiateur de la consommation : {{mediateur}} (articles L. 612-1 et '
        'L. 616-1 du Code de la consommation).',
        '## Données personnelles', 'Les données sont traitées selon la politique de confidentialité du vendeur, conformément au règlement (UE) '
        '2016/679.',
        '## Droit applicable — Litiges', 'Les présentes conditions sont soumises au droit français, sans priver l’acheteur de la protection des '
        'dispositions impératives du pays de sa résidence habituelle lorsque celles-ci s’appliquent (règlement (CE) n° 593/2008, article 6). '
        'L’acheteur peut saisir, à son choix, l’une des juridictions territorialement compétentes ou celle du lieu où il demeurait lors de la '
        'conclusion du contrat (article R. 631-3 du Code de la consommation).'],
       ['Code de la consommation, art. L. 111-1, L. 216-1, L. 216-4, L. 217-3 et s., L. 221-18, L. 221-28, L. 612-1, L. 616-1, R. 631-3 — Légifrance',
        'Code civil, art. 1127-1, 1127-2, 1641 et s. — Légifrance', 'Règlement (CE) n° 593/2008 (Rome I), art. 6 — EUR-Lex'],
       ('vendeur professionnel', 'consommateurs et non-professionnels')),
    _t('fr_cgu_service', 'FR', 'fr', 'contrat', 'cgu', 'Conditions générales d’utilisation d’un service en ligne',
       _R('CGU d’un site ou d’un service en ligne : accès, compte, obligations, contenus publiés, propriété intellectuelle, responsabilité.',
          'Terms of use of an online service (French law).', 'Nutzungsbedingungen eines Online-Dienstes (französisches Recht).',
          'Condiciones generales de uso de un servicio en línea (derecho francés).'),
       ('conditions générales d’utilisation', 'cgu du site', 'cgu du service'),
       [('editeur', 'Éditeur (dénomination, forme, siège, RCS, directeur de la publication)', ''), ('service', 'Service et adresse du site', ''),
        ('hebergeur', 'Hébergeur (nom, adresse, téléphone)', ''), ('contact', 'Contact des utilisateurs', '')],
       [('compte', 'Accès par compte personnel', True), ('payant', 'Service payant', False), ('contenus', 'Contenus publiés par les utilisateurs', False),
        ('consommateurs', 'Service ouvert aux consommateurs', True)],
       ['# CONDITIONS GÉNÉRALES D’UTILISATION', '{{service}}',
        '## Éditeur et hébergeur', 'Éditeur : {{editeur}}. Hébergeur : {{hebergeur}} [À VÉRIFIER : mentions de l’article 6 de la loi n° 2004-575 du '
        '21 juin 2004].',
        '## Objet et acceptation', 'Les présentes conditions régissent l’accès et l’utilisation du service. Leur acceptation est un préalable à '
        'l’utilisation.',
        '[[si compte]]', '## Compte', 'L’utilisateur fournit des informations exactes, garde ses identifiants confidentiels et signale sans délai tout '
        'usage non autorisé de son compte.', '[[fin]]',
        '[[si payant]]', '## Prix', 'Les fonctions payantes sont soumises aux conditions générales de vente, qui prévalent sur ce point.', '[[fin]]',
        '## Obligations de l’utilisateur', 'L’utilisateur respecte la loi et les droits des tiers et n’entrave pas le fonctionnement du service.',
        '[[si contenus]]', '## Contenus publiés', 'L’utilisateur reste responsable des contenus qu’il publie. L’éditeur retire promptement un contenu '
        'manifestement illicite qui lui est notifié [À VÉRIFIER : régime applicable à l’hébergeur et procédure de notification à la date des CGU].',
        '[[fin]]',
        '## Propriété intellectuelle', 'Le service et ses éléments restent la propriété de l’éditeur ou de ses concédants ; aucune cession n’est '
        'consentie à l’utilisateur.',
        '## Données personnelles', 'Les données sont traitées selon la politique de confidentialité de l’éditeur, conformément au règlement (UE) '
        '2016/679.',
        '## Responsabilité', '[[si consommateurs]]La responsabilité de l’éditeur envers un consommateur ne peut être exclue ni limitée au-delà de ce '
        'que permet la loi (article R. 212-1 du Code de la consommation).[[sinon]]La responsabilité de l’éditeur est limitée aux dommages directs, '
        'sauf faute lourde ou dolosive.[[fin]]',
        '## Modification et fin', 'L’éditeur informe les utilisateurs de toute modification avant son entrée en vigueur. L’utilisateur peut cesser '
        'd’utiliser le service et clôturer son compte à tout moment ; contact : {{contact}}.',
        '## Droit applicable', 'Les présentes conditions sont soumises au droit français [À VÉRIFIER : dispositions impératives protectrices des '
        'consommateurs résidant à l’étranger].'],
       ['Loi n° 2004-575 du 21 juin 2004, art. 6 — Légifrance', 'Code de la consommation, art. R. 212-1 — Légifrance',
        'Règlement (UE) 2016/679 — EUR-Lex'], ('éditeur du service', 'utilisateurs')),
    _t('fr_cga_achat', 'FR', 'fr', 'contrat', 'cga', 'Conditions générales d’achat',
       _R('CGA d’un acheteur professionnel : commande, prix, délai de paiement plafonné, livraison, réception, garanties.',
          'General terms of purchase (French law).', 'Allgemeine Einkaufsbedingungen (französisches Recht).',
          'Condiciones generales de compra (derecho francés).'),
       ('conditions générales d’achat', 'cga'),
       [('acheteur', 'Acheteur (dénomination, forme, siège, RCS)', ''), ('achats', 'Biens ou prestations achetés', ''),
        ('delai_paiement', 'Délai de paiement', ''), ('lieu_livraison', 'Lieu de livraison', ''), ('tribunal', 'Ville du tribunal', '')],
       [('penalites_livraison', 'Pénalités de retard de livraison', False), ('garantie_contractuelle', 'Garantie contractuelle', True),
        ('confidentialite', 'Confidentialité', False)],
       ['# CONDITIONS GÉNÉRALES D’ACHAT', '{{acheteur}}',
        '## Champ d’application', 'Les présentes conditions s’appliquent aux achats de {{achats}} auprès de fournisseurs professionnels, dans le cadre '
        'de la négociation commerciale ; les conditions générales de vente du fournisseur en constituent le socle (article L. 441-1 du Code de '
        'commerce). Les clauses incompatibles des conditions générales des parties sont sans effet (article 1119 du Code civil).',
        '## Commande', 'Toute commande fait l’objet d’un bon de commande écrit ; elle est ferme à réception de son acceptation par le fournisseur.',
        '## Prix', 'Les prix sont fermes et non révisables, sauf stipulation écrite contraire.',
        '## Paiement', 'Les factures sont payables dans un délai de {{delai_paiement}}, dans les limites de l’article L. 441-10 du Code de commerce.',
        '## Livraison et réception', 'Les biens sont livrés à {{lieu_livraison}}. La réception est prononcée après contrôle ; les réserves sont notifiées '
        'au fournisseur [À COMPLÉTER : délai de contrôle].',
        '[[si penalites_livraison]]', '## Retard de livraison', 'Tout retard donne lieu à des pénalités de [À COMPLÉTER] par jour de retard, dans la '
        'limite de [À COMPLÉTER] [À VÉRIFIER : article L. 442-1 du Code de commerce, pénalités disproportionnées].', '[[fin]]',
        '[[si garantie_contractuelle]]', '## Garantie', 'Le fournisseur garantit la conformité des biens à la commande pendant [À COMPLÉTER] mois, '
        'sans préjudice des garanties légales.', '[[fin]]',
        '[[si confidentialite]]', '## Confidentialité', 'Le fournisseur garde confidentielles les informations reçues pour la commande.', '[[fin]]',
        '## Droit applicable — Litiges', 'Les présentes conditions sont soumises au droit français ; tout litige entre professionnels relève des '
        'juridictions de {{tribunal}}.'],
       ['Code de commerce, art. L. 441-1, L. 441-10, L. 442-1 — Légifrance', 'Code civil, art. 1119 — Légifrance'],
       ('acheteur professionnel', 'fournisseurs professionnels')),
    _t('fr_agence_commerciale', 'FR', 'fr', 'contrat', 'agence-commerciale', 'Contrat d’agent commercial',
       _R('Mandat d’agent commercial : produits, territoire, commission, obligations réciproques, préavis, indemnité de fin de contrat.',
          'Commercial agency agreement (French law).', 'Handelsvertretervertrag (französisches Recht).', 'Contrato de agencia comercial (derecho francés).'),
       ('contrat d’agent commercial', 'contrat d’agence commerciale', 'agent commercial'),
       [('mandant', 'Mandant (dénomination, forme, siège, RCS)', ''), ('agent', 'Agent commercial (nom ou dénomination, immatriculation)', ''),
        ('produits', 'Produits ou services à négocier', ''), ('territoire', 'Territoire ou clientèle', ''), ('commission', 'Commission (taux, assiette, '
        'exigibilité)', ''), ('duree', 'Durée', ''), ('tribunal', 'Ville du tribunal', ''), ('ville', 'Lieu', ''), ('date_acte', 'Date', '')],
       [('exclusivite', 'Exclusivité sur le territoire', False), ('non_concurrence', 'Non-concurrence après la fin du contrat', False)],
       ['# CONTRAT D’AGENT COMMERCIAL', '', '# ENTRE :', '{{mandant}}, ci-après le « Mandant »,', '# ET :', '{{agent}}, ci-après l’« Agent »,', '',
        '## Objet', 'Le Mandant confie à l’Agent, qui l’accepte, le mandat de négocier et, le cas échéant, de conclure en son nom et pour son compte '
        'des contrats portant sur {{produits}} (article L. 134-1 du Code de commerce).',
        '## Statut', 'L’Agent exerce en professionnel indépendant ; il justifie de son immatriculation au registre spécial des agents commerciaux '
        '[À VÉRIFIER : article R. 134-6 du Code de commerce].',
        '## Territoire', 'Le mandat s’exerce sur : {{territoire}}.',
        '[[si exclusivite]]', 'Le Mandant s’interdit de désigner un autre agent sur ce territoire.', '[[fin]]',
        '## Obligations des parties', 'Les rapports entre les parties sont régis par une obligation de loyauté et un devoir réciproque d’information '
        '(article L. 134-4 du Code de commerce).',
        '## Commission', 'L’Agent perçoit une commission : {{commission}} (articles L. 134-5 et suivants du Code de commerce). Le Mandant remet un '
        'relevé des commissions dues [À VÉRIFIER : article R. 134-3].',
        '## Durée et préavis', 'Le contrat est conclu pour {{duree}}. Un contrat à durée indéterminée peut être résilié moyennant un préavis d’un mois '
        'pour la première année, de deux mois pour la deuxième et de trois mois à partir de la troisième année (article L. 134-11 du Code de '
        'commerce).',
        '## Fin du contrat', 'En cas de cessation de ses relations avec le Mandant, l’Agent a droit à une indemnité compensatrice, sauf dans les cas '
        'prévus à l’article L. 134-13 (article L. 134-12 du Code de commerce).',
        '[[si non_concurrence]]', '## Non-concurrence', 'Après la fin du contrat, l’Agent s’interdit, pendant [À COMPLÉTER : deux ans au plus], de '
        'représenter des produits concurrents sur le territoire et la clientèle confiés (article L. 134-14 du Code de commerce).', '[[fin]]',
        '## Droit applicable — Litiges', 'Le contrat est régi par le droit français ; tout litige relève des juridictions de {{tribunal}} [À VÉRIFIER : '
        'dispositions impératives de l’article L. 134-16 du Code de commerce].'] + _FIN,
       ['Code de commerce, art. L. 134-1 à L. 134-16, R. 134-3, R. 134-6 — Légifrance'], ('mandant', 'agent commercial')),
    _t('fr_licence_logiciel', 'FR', 'fr', 'contrat', 'licence-logiciel', 'Contrat de licence de logiciel',
       _R('Licence d’utilisation d’un logiciel ou accès en ligne : droits concédés, restrictions, redevances, maintenance, données, fin.',
          'Software licence agreement (French law).', 'Softwarelizenzvertrag (französisches Recht).', 'Contrato de licencia de software (derecho francés).'),
       ('contrat de licence de logiciel', 'licence de logiciel', 'contrat saas'),
       [('concedant', 'Concédant (dénomination, forme, siège, RCS)', ''), ('licencie', 'Licencié (dénomination, forme, siège, RCS)', ''),
        ('logiciel', 'Logiciel ou service (nom, version)', ''), ('perimetre', 'Utilisateurs ou postes autorisés', ''), ('redevance', 'Redevance et '
        'périodicité', ''), ('duree', 'Durée', ''), ('tribunal', 'Ville du tribunal', ''), ('ville', 'Lieu', ''), ('date_acte', 'Date', '')],
       [('acces_en_ligne', 'Accès en ligne au logiciel hébergé par le concédant', False), ('installation', 'Installation chez le licencié', True),
        ('maintenance', 'Maintenance et assistance', True), ('donnees', 'Données du licencié hébergées par le concédant', False)],
       ['# CONTRAT DE LICENCE DE LOGICIEL', '', '# ENTRE :', '{{concedant}}, ci-après le « Concédant »,', '# ET :', '{{licencie}}, ci-après le « Licencié »,', '',
        '## Objet', 'Le Concédant concède au Licencié un droit d’utilisation de {{logiciel}} (le « Logiciel »), non exclusif et non cessible, dans les '
        'limites suivantes : {{perimetre}}.',
        '[[si installation]]', '## Installation', 'Le Logiciel est installé sur les équipements du Licencié, qui peut en faire une copie de sauvegarde '
        '(article L. 122-6-1 du Code de la propriété intellectuelle).', '[[fin]]',
        '[[si acces_en_ligne]]', '## Accès en ligne', 'Le Logiciel est hébergé par le Concédant et accessible en ligne ; la disponibilité visée figure '
        'en annexe [À COMPLÉTER].', '[[fin]]',
        '## Restrictions', 'Toute utilisation non prévue au contrat, toute cession et toute sous-licence sont exclues, sous réserve des droits que la loi '
        'reconnaît à l’utilisateur légitime (articles L. 122-6 et L. 122-6-1 du Code de la propriété intellectuelle).',
        '## Redevance', 'Le Licencié paie {{redevance}}.',
        '[[si maintenance]]', '## Maintenance', 'Le Concédant assure la maintenance corrective et l’assistance selon l’annexe [À COMPLÉTER : délais '
        'd’intervention].', '[[fin]]',
        '[[si donnees]]', '## Données', 'Pour les données personnelles hébergées, le Concédant agit en qualité de sous-traitant au sens de l’article 28 '
        'du règlement (UE) 2016/679 ; les stipulations requises figurent en annexe. À la fin du contrat, les données sont restituées puis effacées '
        '[À COMPLÉTER : format et délai].', '[[fin]]',
        '## Propriété', 'Le Concédant conserve tous les droits de propriété intellectuelle sur le Logiciel.',
        '## Garantie et responsabilité', 'Le Concédant garantit la conformité du Logiciel à sa documentation [À COMPLÉTER : durée]. Sa responsabilité est '
        'limitée aux dommages directs, dans la limite de [À COMPLÉTER], sauf faute lourde ou dolosive.',
        '## Durée', 'Le contrat est conclu pour {{duree}}.',
        '## Droit applicable — Litiges', 'Le contrat est régi par le droit français ; tout litige relève des juridictions de {{tribunal}}.'] + _FIN,
       ['Code de la propriété intellectuelle, art. L. 122-6, L. 122-6-1 — Légifrance', 'Règlement (UE) 2016/679, art. 28 — EUR-Lex'],
       ('concédant', 'licencié'), (('acces_en_ligne', 'installation'),)),
    _t('fr_bail_commercial', 'FR', 'fr', 'contrat', 'bail-commercial', 'Bail commercial',
       _R('Bail soumis au statut des baux commerciaux : locaux, destination, durée de neuf ans, loyer et révision, charges, renouvellement.',
          'Commercial lease (French law).', 'Gewerbemietvertrag (französisches Recht).', 'Arrendamiento comercial (derecho francés).'),
       ('bail commercial', 'bail de locaux commerciaux'),
       [('bailleur', 'Bailleur (nom ou dénomination, adresse)', ''), ('preneur', 'Preneur (dénomination, forme, siège, RCS)', ''),
        ('locaux', 'Locaux (adresse, désignation, surface)', ''), ('destination', 'Destination (activités autorisées)', ''),
        ('date_effet', 'Date de prise d’effet', ''), ('loyer', 'Loyer annuel hors taxes et hors charges', ''), ('indice', 'Indice de révision (ILC ou ILAT)', ''),
        ('charges', 'Répartition des charges, impôts et travaux', ''), ('ville', 'Lieu', ''), ('date_acte', 'Date', '')],
       [('depot_garantie', 'Dépôt de garantie', True), ('echelle_mobile', 'Clause d’échelle mobile', False),
        ('sous_location', 'Sous-location autorisée', False)],
       ['# BAIL COMMERCIAL', '', '# ENTRE :', '{{bailleur}}, ci-après le « Bailleur »,', '# ET :', '{{preneur}}, ci-après le « Preneur »,', '',
        '## Statut', 'Le présent bail est soumis aux articles L. 145-1 et suivants du Code de commerce.',
        '## Désignation', 'Le Bailleur donne à bail les locaux suivants : {{locaux}}.',
        '## Destination', 'Les locaux sont destinés exclusivement à : {{destination}}.',
        '## Durée', 'Le bail est conclu pour neuf ans à compter du {{date_effet}}. Le Preneur peut donner congé à l’expiration de chaque période '
        'triennale, dans les formes et délais de l’article L. 145-9 (article L. 145-4 du Code de commerce) [À VÉRIFIER : exceptions prévues à cet '
        'article].',
        '## Loyer', 'Le loyer annuel est fixé à {{loyer}} hors taxes et hors charges, payable [À COMPLÉTER : périodicité et terme].',
        '## Révision', 'Le loyer peut être révisé dans les conditions des articles L. 145-37 et L. 145-38 du Code de commerce ; indice de référence : '
        '{{indice}}.',
        '[[si echelle_mobile]]', '## Échelle mobile', 'Le loyer est indexé chaque année sur la variation de l’indice {{indice}} [À VÉRIFIER : '
        'articles L. 145-39 du Code de commerce et L. 112-1 du Code monétaire et financier].', '[[fin]]',
        '[[si depot_garantie]]', '## Dépôt de garantie', 'Le Preneur verse un dépôt de garantie de [À COMPLÉTER], restitué en fin de bail déduction faite '
        'des sommes dues.', '[[fin]]',
        '## Charges, impôts et travaux', '{{charges}}', 'Un inventaire précis et limitatif des catégories de charges, impôts, taxes et redevances, avec '
        'leur répartition, est annexé (article L. 145-40-2 du Code de commerce) ; certaines charges ne peuvent être imputées au Preneur '
        '(article R. 145-35).',
        '## État des lieux et annexes', 'Un état des lieux est établi à la prise de possession et à la restitution (article L. 145-40-1 du Code de '
        'commerce). [À VÉRIFIER : annexes et diagnostics requis selon les locaux].',
        '[[si sous_location]]', '## Sous-location', 'La sous-location est autorisée dans les conditions de l’article L. 145-31 du Code de commerce.',
        '[[fin]]',
        '## Renouvellement', 'Le Preneur bénéficie du droit au renouvellement et, en cas de refus, de l’indemnité d’éviction, dans les conditions des '
        'articles L. 145-8 et suivants et L. 145-14 du Code de commerce.'] + _FIN,
       ['Code de commerce, art. L. 145-1 et s., L. 145-4, L. 145-8, L. 145-9, L. 145-14, L. 145-31, L. 145-37 à L. 145-40-2, R. 145-35 — Légifrance',
        'Code monétaire et financier, art. L. 112-1 — Légifrance'], ('bailleur', 'preneur')),
]
TEMPLATES56111 = FR56111


# ================================================================================================ 5.6.111 (lot K, F07) : questionnaires des familles Conseil
# Données des questionnaires natifs (``questionnaires56108.deriver`` les applique ; aucun second format) : pour chaque trame, le rangement de ses
# variables dans les sections du formulaire, des aides sobres, des questions ajoutées (forme native : ``id``, ``libelle``, ``type``, ``groupe``,
# ``bloquant_signature``, ``actif_si``…) et, s'il y a lieu, la rédaction par l'IA réservée aux sections qui portent une mention à compléter.
# Les libellés suivent la langue de la trame ; rien ici n'examine un régime juridique : les questions propres à un droit restent à valider par
# un juriste compétent dans ce droit. Un questionnaire ne change jamais l'état juridique de la trame.
_SIGNATAIRES = {'fr': ('Signataires et pouvoirs pour cet acte (%s)', 'Nom, qualité et justificatif du pouvoir de chaque signataire.'),
                'de': ('Unterzeichner und Vertretungsnachweise (%s)', 'Name, Funktion und Nachweis der Vertretungsbefugnis.'),
                'es': ('Firmantes y facultades (%s)', 'Nombre, cargo y acreditación de las facultades de cada firmante.'),
                'en': ('Signatories and authority (%s)', 'Name, capacity and evidence of authority of each signatory.')}


def _signataires(langue, titre):
    libelle, aide = _SIGNATAIRES[langue]
    return {'id': 'signataires', 'libelle': (libelle % titre)[:200], 'aide': aide, 'type': 'texte', 'groupe': 'pouvoirs', 'multiligne': True,
            'bloquant_signature': True, 'confirmation_requise': True}


def _q(groupes=None, aides=None, ajouts=(), ia=False, roles=None, parties=()):
    """``parties`` : variables désignant une partie, que la dérivation automatique (mots français) ne reconnaît pas dans les autres langues :
    rangées dans « Parties », bloquantes pour la signature et à confirmer, comme les parties des trames françaises."""
    groupes = {**{k: 'parties' for k in parties}, **dict(groupes or {})}
    return {'groupes': groupes, 'aides': dict(aides or {}), 'ajouts': list(ajouts), 'ia_mentions': bool(ia), 'roles': dict(roles or {}),
            'parties': list(parties)}


_NOTE = {'fr': ('Date de remise attendue par le client', 'Contraintes du client (budget, calendrier, confidentialité)'),
         'de': ('Vom Mandanten erwarteter Termin', 'Vorgaben des Mandanten (Budget, Zeitplan, Vertraulichkeit)'),
         'es': ('Fecha de entrega esperada por el cliente', 'Condiciones del cliente (presupuesto, calendario, confidencialidad)'),
         'en': ('Date expected by the client', 'Client constraints (budget, timetable, confidentiality)')}


def _note(langue, destinataire, auteur, reference, objet, question, faits, date_):
    a, b = _NOTE[langue]
    return _q({destinataire: 'parties', auteur: 'parties', reference: 'operation', objet: 'operation', question: 'operation', faits: 'operation',
               date_: 'operation'},
              {faits: {'fr': 'Faits tenus pour acquis et hypothèses : ils bornent la portée de la note.', 'de': 'Zugrunde gelegter Sachverhalt und '
                       'Annahmen.', 'es': 'Hechos y premisas en que se basa la nota.', 'en': 'Facts and assumptions on which the advice relies.'}[langue]},
              [{'id': 'echeance_client', 'libelle': a, 'type': 'date', 'groupe': 'duree'},
               {'id': 'contraintes', 'libelle': b, 'type': 'texte', 'groupe': 'obligations', 'multiligne': True, 'longueur_max': 2000}], ia=True)


def _mission(client, contact, autres, objet, droit, forum, delais, documents, date_):
    return _q({client: 'parties', contact: 'pouvoirs', autres: 'parties', objet: 'operation', droit: 'litiges', forum: 'litiges', delais: 'duree',
               documents: 'conditions', date_: 'operation'}, ia=True, roles={client: 'client', autres: 'adverse'})


QUESTIONS = {
    # ---------------------------------------------------------------- consultations avec options (six droits)
    'fr_note_consultation': _note('fr', 'destinataire', 'auteur', 'reference', 'objet', 'question', 'faits', 'date_acte'),
    'qc_note_consultation': _note('fr', 'destinataire', 'auteur', 'reference', 'objet', 'question', 'faits', 'date_acte'),
    'de_beratungsvermerk': _note('de', 'empfaenger', 'verfasser', 'aktenzeichen', 'betreff', 'frage', 'sachverhalt', 'datum'),
    'es_nota_consulta': _note('es', 'destinatario', 'autor', 'referencia', 'asunto', 'cuestion', 'hechos', 'fecha'),
    'uk_advice_note': _note('en', 'to', 'author', 'reference', 'subject', 'question', 'facts', 'date'),
    'on_advice_memo': _note('en', 'to', 'author', 'reference', 'subject', 'question', 'facts', 'date'),
    # ---------------------------------------------------------------- questionnaires d'ouverture de mission (six droits)
    'fr_questionnaire_mission': _mission('client', 'interlocuteur', 'autres_parties', 'objet', 'droit_envisage', 'juridiction', 'echeances', 'documents', 'date_acte'),
    'qc_questionnaire_mandat': _mission('client', 'interlocuteur', 'autres_parties', 'objet', 'province_matiere', 'tribunal', 'delais', 'documents', 'date_acte'),
    'de_mandatsfragebogen': _mission('mandant', 'ansprechpartner', 'gegner', 'gegenstand', 'recht', 'gericht', 'fristen', 'unterlagen', 'datum'),
    'es_cuestionario_encargo': _mission('cliente', 'contacto', 'contrarios', 'objeto', 'derecho', 'organo', 'plazos', 'documentos', 'fecha'),
    'uk_client_questionnaire': _mission('client', 'contact', 'other_parties', 'scope', 'governing_law', 'forum', 'deadlines', 'documents', 'date'),
    'on_client_questionnaire': _mission('client', 'contact', 'other_parties', 'scope', 'jurisdiction', 'forum', 'deadlines', 'documents', 'date'),
    # ---------------------------------------------------------------- accords de confidentialité (six droits)
    'accord_confidentialite': _q({'projet': 'operation', 'duree': 'duree', 'tribunal': 'litiges'},
                                 {'projet': 'Projet ou négociation pour lequel les informations sont échangées : il fixe l’usage permis.',
                                  'duree': 'Durée après la dernière communication, par exemple « cinq ans ».',
                                  'reciproque': 'Oui si chaque partie communique des informations ; non si seule la partie A en communique.'},
                                 [_signataires('fr', 'accord de confidentialité')]),
    'qc_entente_confidentialite': _q({'projet': 'operation', 'duree': 'duree', 'district': 'litiges'},
                                     {'reciproque': 'Oui si chaque partie communique des renseignements ; non si seule la partie A en communique.'},
                                     [_signataires('fr', 'entente de confidentialité')]),
    'de_geheimhaltung': _q(parties=('partei_a', 'partei_b'), groupes={'zweck': 'operation', 'dauer': 'duree', 'vertragsstrafe': 'economie'}, ajouts=[_signataires('de', 'Geheimhaltungsvereinbarung')]),
    'es_confidencialidad': _q(parties=('parte_a', 'parte_b'), groupes={'finalidad': 'operation', 'duracion': 'duree'}, ajouts=[_signataires('es', 'acuerdo de confidencialidad')]),
    'en_nda': _q(parties=('party_a', 'party_b'), groupes={'purpose': 'operation', 'term': 'duree'}, ajouts=[_signataires('en', 'non-disclosure agreement')]),
    'on_nda': _q(parties=('party_a', 'party_b'), groupes={'purpose': 'operation', 'term': 'duree'}, ajouts=[_signataires('en', 'confidentiality agreement')]),
    # ---------------------------------------------------------------- prestations entre professionnels (six droits)
    'contrat_prestation': _q({'prestations': 'obligations', 'prix': 'economie', 'delai_paiement': 'economie', 'duree': 'duree', 'preavis': 'duree',
                              'plafond': 'responsabilite', 'tribunal': 'litiges'},
                             {'prestations': 'Décrivez les prestations et, s’il y a lieu, les livrables et leur recette.',
                              'plafond': 'Montant ou mode de calcul du plafond, par exemple « le prix payé sur les douze derniers mois ».'},
                             [_signataires('fr', 'contrat de prestation'),
                              {'id': 'donnees_personnelles', 'libelle': 'Le prestataire traitera-t-il des données personnelles pour le client ?', 'type': 'booleen',
                               'groupe': 'donnees'},
                              {'id': 'donnees_precisions', 'libelle': 'Données et personnes concernées, durée du traitement', 'type': 'texte', 'groupe': 'donnees',
                               'multiligne': True, 'longueur_max': 2000, 'actif_si': {'champ': 'donnees_personnelles', 'egal': True}},
                              {'id': 'annexes', 'libelle': 'Annexes à joindre (devis, cahier des charges, niveaux de service)', 'type': 'texte',
                               'groupe': 'conditions', 'multiligne': True, 'longueur_max': 2000}]),
    'qc_contrat_service': _q({'services': 'obligations', 'prix': 'economie', 'delai_paiement': 'economie', 'taux_interet': 'economie', 'duree': 'duree',
                              'plafond': 'responsabilite', 'district': 'litiges'}, ajouts=[_signataires('fr', 'contrat de service')]),
    'de_dienstleistungsvertrag': _q(parties=('auftragnehmer', 'auftraggeber'), groupes={'leistungen': 'obligations', 'verguetung': 'economie', 'zahlungsziel': 'economie', 'kuendigungsfrist': 'duree',
                                     'gerichtsstand': 'litiges'}, ajouts=[_signataires('de', 'Dienstleistungsvertrag')]),
    'es_prestacion_servicios': _q(parties=('prestador', 'cliente'), groupes={'servicios': 'obligations', 'precio': 'economie', 'plazo_pago': 'economie'},
                                  ajouts=[_signataires('es', 'contrato de prestación de servicios')]),
    'en_services_agreement': _q(parties=('supplier', 'customer'), groupes={'services': 'obligations', 'charges': 'economie', 'payment_terms': 'economie', 'cap': 'responsabilite'},
                                ajouts=[_signataires('en', 'services agreement')]),
    'on_services_agreement': _q(parties=('supplier', 'customer'), groupes={'services': 'obligations', 'fees': 'economie', 'payment_terms': 'economie', 'interest_rate': 'economie', 'term': 'duree',
                                 'cap': 'responsabilite'}, ajouts=[_signataires('en', 'services agreement')]),
    # ---------------------------------------------------------------- conditions générales de vente entre professionnels (quatre droits)
    'cgv_b2b': _q({'produits': 'obligations', 'delai_paiement': 'economie', 'escompte': 'economie', 'tribunal': 'litiges'},
                  {'delai_paiement': 'Délai convenu, dans les plafonds de l’article L. 441-10 du Code de commerce (à vérifier).',
                   'produits': 'CGV destinées aux seuls professionnels ; pour des consommateurs, utilisez le modèle « CGV aux consommateurs ».'}),
    'de_agb_b2b': _q(parties=('verkaeufer',), groupes={'zahlungsziel': 'economie', 'gerichtsstand': 'litiges'}),
    'es_condiciones_generales': _q(parties=('vendedor',), groupes={'plazo_pago': 'economie', 'jurisdiccion': 'litiges'}),
    'en_terms_sale': _q(parties=('seller',), groupes={'payment_terms': 'economie'}),
    # ---------------------------------------------------------------- pactes (quatre droits)
    'pacte_associes': _q({'denomination': 'parties', 'forme_long': 'parties', 'siege': 'parties', 'capital': 'parties', 'rcs_ville': 'parties', 'siren': 'parties',
                          'expose': 'operation', 'gouvernance': 'obligations', 'majorite_decisions_importantes': 'obligations', 'seuil_information': 'obligations',
                          'duree_inalienabilite': 'duree', 'delai_preemption': 'duree', 'delai_realisation': 'duree', 'delai_sortie_conjointe': 'duree',
                          'seuil_sortie_forcee': 'obligations', 'delai_leaver': 'duree', 'prix_bad_leaver': 'economie', 'parties_non_concurrence': 'obligations',
                          'duree_non_concurrence': 'duree', 'territoire_non_concurrence': 'obligations', 'methode_anti_dilution': 'economie',
                          'delai_liquidite': 'duree', 'multiple_preference': 'economie', 'duree_pacte': 'duree', 'tribunal': 'litiges'},
                         ajouts=[_signataires('fr', 'pacte d’associés')]),
    'de_gesellschaftervereinbarung': _q({'firma': 'parties', 'sitz': 'parties', 'hrb': 'parties', 'laufzeit': 'duree', 'gerichtsstand': 'litiges'},
                                        ajouts=[_signataires('de', 'Gesellschaftervereinbarung')]),
    'es_pacto_socios': _q({'denominacion': 'parties', 'duracion': 'duree'}, ajouts=[_signataires('es', 'pacto de socios')]),
    'en_shareholders_agreement': _q({'company_name': 'parties', 'company_number': 'parties'}, ajouts=[_signataires('en', 'shareholders’ agreement')]),
    # ---------------------------------------------------------------- cessions de titres (quatre droits)
    'cession_titres': _q({'denomination': 'operation', 'forme_long': 'operation', 'capital': 'operation', 'siege': 'operation', 'rcs_ville': 'operation',
                          'siren': 'operation', 'nombre': 'operation', 'titres': 'operation', 'prix_unitaire': 'economie', 'prix_total': 'economie',
                          'modalites_paiement': 'economie', 'date_jouissance': 'operation'},
                         {'agrement': 'Cochez si les statuts soumettent la cession à agrément : la clause rappelle la décision obtenue.'},
                         [_signataires('fr', 'cession de titres')]),
    'de_anteilskaufvertrag': _q(parties=('verkaeufer', 'kaeufer'), groupes={'firma': 'operation', 'hrb': 'operation', 'anteile': 'operation', 'kaufpreis': 'economie', 'stichtag': 'operation',
                                 'haftungshoechstbetrag': 'responsabilite'}, ajouts=[_signataires('de', 'Anteilskaufvertrag')]),
    'es_compraventa_participaciones': _q(parties=('vendedor', 'comprador'), groupes={'denominacion': 'operation', 'participaciones': 'operation', 'precio': 'economie'},
                                         ajouts=[_signataires('es', 'compraventa de participaciones')]),
    'en_spa': _q(parties=('seller', 'buyer'), groupes={'company_name': 'operation', 'shares': 'operation', 'price': 'economie', 'cap': 'responsabilite'},
                 ajouts=[_signataires('en', 'share purchase agreement')]),
    # ---------------------------------------------------------------- lettre de mission et honoraires (France)
    'lettre_mission': _q({'perimetre_mission': 'obligations', 'exclusions': 'obligations'},
                         {'exclusions': 'Ce qui n’est pas compris dans la mission : à écrire, même brièvement.'}, roles={'partie_conseillee': 'client'}),
    'convention_honoraires': _q({'perimetre_mission': 'obligations', 'honoraires': 'economie', 'taux_horaire': 'economie', 'modalites_paiement': 'economie'},
                                {'resultat_a_qualifier': 'Un honoraire de résultat complète un honoraire fixe ; il ne peut être le seul mode de rémunération '
                                 '(loi n° 71-1130 du 31 décembre 1971, article 10, à vérifier).'},
                                [_signataires('fr', 'convention d’honoraires')], roles={'partie_conseillee': 'client'}),
    # ---------------------------------------------------------------- familles ajoutées en 5.6.111 (France)
    'fr_note_analyse_contrat': _q({'destinataire': 'parties', 'auteur': 'parties', 'partie_assistee': 'parties', 'contrepartie': 'parties',
                                   'contrat_analyse': 'operation', 'objectifs': 'obligations'},
                                  {'contrat_analyse': 'Indiquez la version exacte reçue (date, émetteur) : la note ne vaut que pour elle.'},
                                  [{'id': 'points_non_negociables', 'libelle': 'Points que le client refuse de céder', 'type': 'texte', 'groupe': 'obligations',
                                    'multiligne': True, 'longueur_max': 2000},
                                   {'id': 'echeance_negociation', 'libelle': 'Date limite de la négociation', 'type': 'date', 'groupe': 'redaction',
                                    'actif_si': {'champ': 'urgence', 'egal': True}}], ia=True, roles={'partie_assistee': 'client'}),
    'fr_cgv_consommateurs': _q({'produits': 'obligations', 'modes_paiement': 'economie', 'delai_livraison': 'obligations', 'service_client': 'litiges',
                                'mediateur': 'litiges'},
                               {'mediateur': 'Le professionnel doit permettre le recours gratuit à un médiateur de la consommation ; indiquez celui auquel il a adhéré.',
                                'distance': 'Oui pour une vente en ligne, par téléphone ou hors établissement.'},
                               [{'id': 'vente_en_ligne', 'libelle': 'Les commandes sont-elles passées en ligne ?', 'type': 'booleen', 'groupe': 'operation'},
                                {'id': 'site_internet', 'libelle': 'Adresse du site de vente', 'type': 'texte', 'groupe': 'operation',
                                 'actif_si': {'champ': 'vente_en_ligne', 'egal': True}}], ia=True),
    'fr_cgu_service': _q({'service': 'operation', 'hebergeur': 'parties', 'contact': 'litiges'},
                         {'consommateurs': 'Oui si le service est accessible à des particuliers : la clause de responsabilité est alors encadrée.'},
                         [{'id': 'donnees_traitees', 'libelle': 'Données personnelles collectées par le service', 'type': 'texte', 'groupe': 'donnees',
                           'multiligne': True, 'longueur_max': 2000}], ia=True),
    'fr_cga_achat': _q({'achats': 'obligations', 'delai_paiement': 'economie', 'lieu_livraison': 'obligations', 'tribunal': 'litiges'},
                       {'delai_paiement': 'Délai convenu, dans les plafonds de l’article L. 441-10 du Code de commerce (à vérifier).'}, ia=True),
    'fr_agence_commerciale': _q({'produits': 'obligations', 'territoire': 'obligations', 'commission': 'economie', 'duree': 'duree', 'tribunal': 'litiges'},
                                {'duree': 'Durée déterminée ou indéterminée ; le préavis légal dépend de l’ancienneté du contrat.',
                                 'agent': 'Indiquez son immatriculation au registre spécial des agents commerciaux.'},
                                [_signataires('fr', 'contrat d’agent commercial'),
                                 {'id': 'duree_non_concurrence', 'libelle': 'Durée de la non-concurrence après la fin du contrat', 'type': 'texte',
                                  'groupe': 'duree', 'aide': 'Deux ans au plus (article L. 134-14 du Code de commerce, à vérifier).'}], ia=True),
    'fr_licence_logiciel': _q({'logiciel': 'operation', 'perimetre': 'obligations', 'redevance': 'economie', 'duree': 'duree', 'tribunal': 'litiges'},
                              {'acces_en_ligne': 'Logiciel hébergé par le concédant et utilisé en ligne ; incompatible avec l’installation chez le licencié.'},
                              [_signataires('fr', 'licence de logiciel'),
                               {'id': 'localisation_donnees', 'libelle': 'Lieu d’hébergement des données du licencié', 'type': 'texte', 'groupe': 'redaction',
                                'actif_si': {'champ': 'donnees', 'egal': True}}], ia=True),
    'fr_bail_commercial': _q({'locaux': 'operation', 'destination': 'obligations', 'date_effet': 'duree', 'loyer': 'economie', 'indice': 'economie',
                              'charges': 'economie'},
                             {'destination': 'Activités autorisées : leur rédaction conditionne l’usage des locaux et leur déspécialisation.',
                              'indice': 'Indice des loyers commerciaux (ILC) ou des activités tertiaires (ILAT), selon l’activité.'},
                             [_signataires('fr', 'bail commercial')], ia=True),
}
# Le pacte d'actionnaires reprend les variables du pacte d'associés : mêmes sections, signataires propres à cet acte.
QUESTIONS['pacte_actionnaires'] = {**QUESTIONS['pacte_associes'], 'ajouts': [_signataires('fr', 'pacte d’actionnaires')]}
