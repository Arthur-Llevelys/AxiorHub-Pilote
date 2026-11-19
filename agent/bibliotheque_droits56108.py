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
