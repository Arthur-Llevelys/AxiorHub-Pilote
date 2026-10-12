"""5.6.39 : bibliothèque de modèles — droit allemand, espagnol et anglais (England & Wales).

Mêmes conventions que bibliotheque_fr5639 (« ## » article numéroté, {{variable}}, [[si cle]] … [[fin]]). Chaque modèle est rédigé dans la
langue de son droit ; il porte l'avertissement « à faire valider par un conseil local » : ce sont des trames de travail, non des avis
sur un droit étranger. Aucune donnée réelle.
"""

_R = lambda fr, en, de, es: {'fr': fr, 'en': en, 'de': de, 'es': es}   # noqa: E731

# ================================================================================================ ALLEMAGNE
DE_SATZUNG_VARS = [
    ('firma', 'Firma der Gesellschaft', ''), ('sitz', 'Sitz der Gesellschaft (Stadt)', ''), ('gegenstand', 'Gegenstand des Unternehmens', ''),
    ('stammkapital', 'Stammkapital (EUR)', ''), ('gesellschafter', 'Gesellschafter und übernommene Geschäftsanteile (je Zeile: Name, Anzahl, Nennbetrag)', ''),
    ('geschaeftsjahr', 'Geschäftsjahr', 'das Kalenderjahr'), ('gruendungskosten', 'Gründungskosten bis (EUR)', ''),
    ('vorkaufsfrist', 'Frist Vorkaufsrecht (Wochen)', '4'), ('mehrheit_zustimmung', 'Mehrheit für die Zustimmung zur Abtretung', 'drei Viertel'),
    ('schwelle_mitverkauf', 'Schwelle Mitverkaufspflicht', '75 %'),
]
DE_SATZUNG_CLAUSES = [
    ('vinkulierung', 'Vinkulierung (Zustimmung zur Abtretung, § 15 Abs. 5 GmbHG)', True, ''),
    ('vorkaufsrecht', 'Vorkaufsrecht der Gesellschafter', True, ''),
    ('mitveraeusserung', 'Mitveräußerungsrecht (Tag-along)', False, ''),
    ('mitverkaufspflicht', 'Mitverkaufspflicht (Drag-along)', False, ''),
    ('einziehung', 'Einziehung von Geschäftsanteilen (§ 34 GmbHG)', True, ''),
    ('wettbewerbsverbot', 'Wettbewerbsverbot für Gesellschafter', False, ''),
    ('beirat', 'Beirat', False, ''),
]


def _de_satzung(ug=False):
    return [
        '# Gesellschaftsvertrag der {{firma}}' + (' (haftungsbeschränkt)' if ug else ''), '',
        '## Firma, Sitz', 'Die Firma der Gesellschaft lautet: {{firma}}' + (' UG (haftungsbeschränkt)' if ug else ' GmbH') + '. Sitz der Gesellschaft '
        'ist {{sitz}}.',
        '## Gegenstand des Unternehmens', 'Gegenstand des Unternehmens ist {{gegenstand}}. Die Gesellschaft ist zu allen Geschäften berechtigt, die '
        'dem Gegenstand des Unternehmens unmittelbar oder mittelbar zu dienen geeignet sind.',
        '## Stammkapital, Geschäftsanteile', 'Das Stammkapital der Gesellschaft beträgt {{stammkapital}} EUR. Hiervon übernehmen:', '{{gesellschafter}}',
        ('Die Einlagen sind sofort in voller Höhe in Geld zu leisten; Sacheinlagen sind ausgeschlossen (§ 5a Abs. 2 GmbHG). In der Bilanz ist eine '
         'gesetzliche Rücklage zu bilden, in die ein Viertel des um einen Verlustvortrag aus dem Vorjahr geminderten Jahresüberschusses einzustellen '
         'ist (§ 5a Abs. 3 GmbHG).' if ug else
         'Die Einlagen sind in Geld zu erbringen, und zwar sofort zur Hälfte, im Übrigen nach Einforderung durch die Geschäftsführung. Das '
         'Mindeststammkapital nach § 5 Abs. 1 GmbHG ist zu beachten; vor der Anmeldung muss die Mindesteinzahlung nach § 7 Abs. 2 GmbHG erfolgt sein.'),
        '## Geschäftsführung, Vertretung', 'Die Gesellschaft hat einen oder mehrere Geschäftsführer. Ist nur ein Geschäftsführer bestellt, vertritt er '
        'die Gesellschaft allein. Sind mehrere Geschäftsführer bestellt, wird die Gesellschaft durch zwei Geschäftsführer gemeinsam oder durch einen '
        'Geschäftsführer zusammen mit einem Prokuristen vertreten. Die Gesellschafterversammlung kann Einzelvertretungsbefugnis erteilen und von '
        'den Beschränkungen des § 181 BGB befreien.',
        'Die Geschäftsführer bedürfen für Geschäfte, die über den gewöhnlichen Geschäftsbetrieb hinausgehen, der vorherigen Zustimmung der '
        'Gesellschafterversammlung. [ZU ERGÄNZEN: Katalog zustimmungspflichtiger Geschäfte]',
        '[[si beirat]]', '## Beirat', 'Die Gesellschaft kann einen Beirat mit beratender Funktion bilden. Einzelheiten regelt eine von der '
        'Gesellschafterversammlung beschlossene Geschäftsordnung.', '[[fin]]',
        '## Gesellschafterversammlung, Beschlüsse', 'Gesellschafterversammlungen werden durch die Geschäftsführer mit eingeschriebenem Brief unter '
        'Mitteilung der Tagesordnung mit einer Frist von mindestens zwei Wochen einberufen. Beschlüsse können auch schriftlich, in Textform oder '
        'per Videokonferenz gefasst werden, wenn kein Gesellschafter widerspricht. Je 1 EUR eines Geschäftsanteils gewährt eine Stimme (§ 47 '
        'Abs. 2 GmbHG). Beschlüsse werden mit einfacher Mehrheit der abgegebenen Stimmen gefasst, soweit nicht Gesetz oder Satzung eine größere '
        'Mehrheit vorschreiben; Satzungsänderungen bedürfen einer Mehrheit von drei Vierteln der abgegebenen Stimmen und der notariellen '
        'Beurkundung (§ 53 GmbHG).',
        '## Verfügung über Geschäftsanteile',
        'Die Abtretung von Geschäftsanteilen bedarf eines notariell beurkundeten Vertrags (§ 15 Abs. 3 GmbHG).',
        '[[si vinkulierung]]', 'Die Abtretung oder Belastung von Geschäftsanteilen oder Teilen davon bedarf der Zustimmung der Gesellschaft, die nur '
        'aufgrund eines Gesellschafterbeschlusses mit einer Mehrheit von {{mehrheit_zustimmung}} der abgegebenen Stimmen erteilt werden darf '
        '(§ 15 Abs. 5 GmbHG); der veräußernde Gesellschafter ist stimmberechtigt.', '[[fin]]',
        '[[si vorkaufsrecht]]', '## Vorkaufsrecht', 'Will ein Gesellschafter seinen Geschäftsanteil ganz oder teilweise an einen Dritten verkaufen, '
        'steht den übrigen Gesellschaftern ein Vorkaufsrecht im Verhältnis ihrer Beteiligung zu. Das Vorkaufsrecht ist innerhalb von '
        '{{vorkaufsfrist}} Wochen nach Zugang der Mitteilung des Kaufvertrags auszuüben (§§ 463 ff. BGB entsprechend).', '[[fin]]',
        '[[si mitveraeusserung]]', '## Mitveräußerungsrecht', 'Veräußert ein Gesellschafter Geschäftsanteile, die zu einem Kontrollwechsel führen, '
        'können die übrigen Gesellschafter verlangen, dass der Erwerber ihre Geschäftsanteile zu denselben Bedingungen miterwirbt.', '[[fin]]',
        '[[si mitverkaufspflicht]]', '## Mitverkaufspflicht', 'Beabsichtigen Gesellschafter, die mindestens {{schwelle_mitverkauf}} des Stammkapitals '
        'halten, sämtliche Geschäftsanteile an einen Dritten zu veräußern, sind die übrigen Gesellschafter verpflichtet, ihre Geschäftsanteile zu '
        'denselben Bedingungen mitzuveräußern. [ZU PRÜFEN: Beurkundungserfordernis nach § 15 Abs. 4 GmbHG]', '[[fin]]',
        '[[si einziehung]]', '## Einziehung von Geschäftsanteilen', 'Die Einziehung von Geschäftsanteilen ist mit Zustimmung des betroffenen '
        'Gesellschafters jederzeit zulässig. Ohne Zustimmung ist sie zulässig, wenn über das Vermögen des Gesellschafters das Insolvenzverfahren '
        'eröffnet wird, in den Geschäftsanteil die Zwangsvollstreckung betrieben wird oder in der Person des Gesellschafters ein wichtiger Grund '
        'vorliegt (§ 34 GmbHG). Die Abfindung bemisst sich nach dem Verkehrswert [ZU ERGÄNZEN: Bewertungsmethode, Zahlungsmodalitäten]; sie darf '
        'nur aus freiem Vermögen gezahlt werden (§ 30 Abs. 1 GmbHG).', '[[fin]]',
        '[[si wettbewerbsverbot]]', '## Wettbewerbsverbot', 'Den Gesellschaftern ist es untersagt, während ihrer Beteiligung der Gesellschaft '
        'unmittelbar oder mittelbar Wettbewerb zu machen. Befreiungen erteilt die Gesellschafterversammlung.', '[[fin]]',
        '## Jahresabschluss, Ergebnisverwendung', 'Das Geschäftsjahr ist {{geschaeftsjahr}}. Die Geschäftsführer stellen den Jahresabschluss innerhalb '
        'der gesetzlichen Fristen auf. Über die Feststellung des Jahresabschlusses und die Ergebnisverwendung beschließt die Gesellschafterversammlung '
        '(§§ 42a, 46 Nr. 1 GmbHG).',
        '## Bekanntmachungen', 'Bekanntmachungen der Gesellschaft erfolgen im Bundesanzeiger.',
        '## Gründungskosten', 'Die Gesellschaft trägt die Kosten der Gründung (Notar, Registergericht, Bekanntmachung) bis zu einem Betrag von '
        '{{gruendungskosten}} EUR; darüber hinausgehende Kosten tragen die Gesellschafter.',
        '## Schlussbestimmungen', 'Sollte eine Bestimmung dieses Gesellschaftsvertrags unwirksam sein, bleibt die Wirksamkeit der übrigen '
        'Bestimmungen unberührt.', '',
        'Hinweis: Der Gesellschaftsvertrag bedarf der notariellen Beurkundung (§ 2 Abs. 1 GmbHG); für eine Gründung mit höchstens drei Gesellschaftern '
        'und einem Geschäftsführer kann das gesetzliche Musterprotokoll verwendet werden (§ 2 Abs. 1a GmbHG).',
    ]


TEMPLATES = [
    {'id': 'de_satzung_gmbh', 'langue': 'de', 'droit': 'DE', 'categorie': 'statuts', 'formes': ('GmbH',), 'titre': 'Gesellschaftsvertrag (Satzung) einer GmbH',
     'resume': _R('Statuts de GmbH allemande (GmbHG) : agrément, préemption, tag/drag-along, Einziehung.', 'German GmbH articles of association.',
                  'Satzung einer GmbH mit Vinkulierung, Vorkaufsrecht, Mitveräußerung, Einziehung.', 'Estatutos de GmbH alemana.'),
     'mots': ('gmbh', 'satzung', 'gesellschaftsvertrag'), 'variables': DE_SATZUNG_VARS, 'clauses': DE_SATZUNG_CLAUSES, 'fixes': {}, 'texte': _de_satzung()},
    {'id': 'de_satzung_ug', 'langue': 'de', 'droit': 'DE', 'categorie': 'statuts', 'formes': ('UG',), 'titre': 'Gesellschaftsvertrag einer UG (haftungsbeschränkt)',
     'resume': _R('Statuts d’UG (haftungsbeschränkt) allemande (§ 5a GmbHG).', 'German UG (limited liability) articles.',
                  'Satzung einer UG (haftungsbeschränkt) nach § 5a GmbHG.', 'Estatutos de UG alemana.'),
     'mots': ('ug haftungsbeschränkt', 'unternehmergesellschaft'), 'variables': DE_SATZUNG_VARS, 'clauses': DE_SATZUNG_CLAUSES, 'fixes': {},
     'texte': _de_satzung(ug=True)},
    {'id': 'de_gesellschaftervereinbarung', 'langue': 'de', 'droit': 'DE', 'categorie': 'pacte', 'formes': ('GmbH', 'UG'), 'titre': 'Gesellschaftervereinbarung',
     'resume': _R('Pacte d’associés de droit allemand.', 'Shareholders’ agreement (German law).', 'Gesellschaftervereinbarung (Beteiligungsvereinbarung).',
                  'Pacto de socios (derecho alemán).'),
     'mots': ('gesellschaftervereinbarung', 'beteiligungsvereinbarung'),
     'variables': [('parteien', 'Parteien (je Zeile: Name, Anschrift, Beteiligung)', ''), ('firma', 'Gesellschaft', ''), ('sitz', 'Sitz', ''),
                   ('hrb', 'Handelsregister (Amtsgericht, HRB-Nr.)', ''), ('laufzeit', 'Laufzeit', 'zehn Jahre'), ('gerichtsstand', 'Gerichtsstand', ''),
                   ('ort', 'Ort', ''), ('datum', 'Datum', '')],
     'clauses': [('zustimmung', 'Zustimmungsvorbehalte', True, ''), ('vorerwerb', 'Vorerwerbsrecht', True, ''), ('tag_along', 'Mitveräußerungsrecht', True, ''),
                 ('drag_along', 'Mitverkaufspflicht', True, ''), ('leaver', 'Good-/Bad-Leaver', False, ''), ('wettbewerb', 'Wettbewerbsverbot', False, '')],
     'fixes': {}, 'texte': [
         '# Gesellschaftervereinbarung', '', 'zwischen', '{{parteien}}', '', '– nachfolgend gemeinsam die „Parteien“ –', '',
         'betreffend die {{firma}}, Sitz {{sitz}}, eingetragen im Handelsregister {{hrb}} (die „Gesellschaft“).', '',
         '## Präambel', 'Die Parteien sind sämtliche Gesellschafter der Gesellschaft und wollen ihre Rechtsbeziehungen ergänzend zum '
         'Gesellschaftsvertrag regeln. Im Verhältnis zur Satzung gehen die Regelungen dieser Vereinbarung im Innenverhältnis vor; die Parteien '
         'verpflichten sich, ihr Stimmrecht entsprechend auszuüben.',
         '[[si zustimmung]]', '## Zustimmungspflichtige Maßnahmen', 'Folgende Maßnahmen bedürfen der vorherigen Zustimmung von Parteien, die mindestens '
         '75 % der Geschäftsanteile halten: Jahresbudget, Investitionen über [ZU ERGÄNZEN] EUR, Aufnahme von Krediten, Kapitalmaßnahmen, Erwerb und '
         'Veräußerung von Beteiligungen, Verträge mit Gesellschaftern.', '[[fin]]',
         '[[si vorerwerb]]', '## Vorerwerbsrecht', 'Beabsichtigt eine Partei, Geschäftsanteile zu veräußern, hat sie diese zunächst den übrigen Parteien '
         'anzubieten, die das Angebot binnen vier Wochen im Verhältnis ihrer Beteiligung annehmen können.', '[[fin]]',
         '[[si tag_along]]', '## Mitveräußerungsrecht', 'Bei einer Veräußerung, die zu einem Kontrollwechsel führt, können die übrigen Parteien '
         'verlangen, ihre Geschäftsanteile zu denselben Bedingungen mitzuveräußern.', '[[fin]]',
         '[[si drag_along]]', '## Mitverkaufspflicht', 'Nehmen Parteien mit mindestens 75 % der Geschäftsanteile ein Angebot eines Dritten für 100 % '
         'der Geschäftsanteile an, sind die übrigen Parteien verpflichtet, ihre Geschäftsanteile zu denselben Bedingungen zu veräußern.', '[[fin]]',
         '[[si leaver]]', '## Ausscheiden (Good-/Bad-Leaver)', 'Endet die Tätigkeit einer Partei für die Gesellschaft, sind die übrigen Parteien '
         'berechtigt, deren Geschäftsanteile zum Verkehrswert (Good Leaver) bzw. zum niedrigeren von Nennwert und Verkehrswert (Bad Leaver) zu '
         'erwerben. [ZU PRÜFEN: Sittenwidrigkeit von Abfindungsbeschränkungen]', '[[fin]]',
         '[[si wettbewerb]]', '## Wettbewerbsverbot', 'Während ihrer Beteiligung und für ein Jahr danach unterlassen die operativ tätigen Parteien jede '
         'Konkurrenztätigkeit im Geschäftsbereich der Gesellschaft.', '[[fin]]',
         '## Form', 'Soweit diese Vereinbarung Verpflichtungen zur Abtretung von Geschäftsanteilen enthält, bedarf sie der notariellen Beurkundung '
         '(§ 15 Abs. 4 GmbHG). Änderungen bedürfen der Schriftform, soweit nicht eine strengere Form vorgeschrieben ist.',
         '## Laufzeit', 'Diese Vereinbarung läuft {{laufzeit}} und endet für eine Partei mit deren Ausscheiden als Gesellschafter.',
         '## Schlussbestimmungen', 'Es gilt deutsches Recht. Gerichtsstand ist {{gerichtsstand}}. Salvatorische Klausel.', '',
         '{{ort}}, den {{datum}}', '', 'Unterschriften der Parteien']},
    {'id': 'de_anteilskaufvertrag', 'langue': 'de', 'droit': 'DE', 'categorie': 'operation', 'formes': ('GmbH', 'UG'),
     'titre': 'Geschäftsanteilskauf- und Abtretungsvertrag',
     'resume': _R('Cession de parts de GmbH (forme notariée, § 15 GmbHG), garanties, liste des associés.', 'German share purchase agreement (GmbH).',
                  'Kauf- und Abtretungsvertrag über GmbH-Geschäftsanteile.', 'Compraventa de participaciones de GmbH alemana.'),
     'mots': ('anteilskaufvertrag', 'geschäftsanteilskauf', 'abtretungsvertrag', 'share deal gmbh'),
     'variables': [('verkaeufer', 'Verkäufer', ''), ('kaeufer', 'Käufer', ''), ('firma', 'Gesellschaft', ''), ('hrb', 'Handelsregister', ''),
                   ('anteile', 'Verkaufte Geschäftsanteile (lfd. Nr., Nennbetrag)', ''), ('kaufpreis', 'Kaufpreis (EUR)', ''),
                   ('stichtag', 'Wirtschaftlicher Stichtag', ''), ('haftungshoechstbetrag', 'Haftungshöchstbetrag', ''), ('ort', 'Ort', ''), ('datum', 'Datum', '')],
     'clauses': [('garantien', 'Garantiekatalog', True, ''), ('steuerfreistellung', 'Steuerfreistellung', True, ''),
                 ('aufschiebend', 'Aufschiebende Bedingungen (Vollzug)', False, ''), ('wettbewerb', 'Wettbewerbsverbot des Verkäufers', False, '')],
     'fixes': {}, 'texte': [
         '# Geschäftsanteilskauf- und Abtretungsvertrag', '', '(notariell zu beurkunden, § 15 Abs. 3 und 4 GmbHG)', '', 'zwischen', '{{verkaeufer}} – „Verkäufer“ –',
         'und', '{{kaeufer}} – „Käufer“ –', '',
         '## Verkauf', 'Der Verkäufer verkauft an den Käufer die folgenden Geschäftsanteile an der {{firma}}, eingetragen im Handelsregister {{hrb}}: '
         '{{anteile}} (die „Geschäftsanteile“), mit allen Rechten und Pflichten einschließlich des Gewinnbezugsrechts ab dem {{stichtag}}.',
         '## Kaufpreis', 'Der Kaufpreis beträgt {{kaufpreis}} EUR und ist am Vollzugstag zur Zahlung fällig.',
         '[[si aufschiebend]]', '## Vollzugsbedingungen', 'Die Abtretung steht unter den aufschiebenden Bedingungen der vollständigen Zahlung des '
         'Kaufpreises und [ZU ERGÄNZEN: Fusionskontrollfreigabe, Investitionsprüfung, Zustimmungen].', '[[fin]]',
         '## Abtretung', 'Der Verkäufer tritt die Geschäftsanteile hiermit an den Käufer ab' + ' [[si aufschiebend]]– aufschiebend bedingt –[[fin]] ' +
         'der die Abtretung annimmt. Erforderliche Zustimmungen nach dem Gesellschaftsvertrag liegen vor bzw. werden beigebracht.',
         '[[si garantien]]', '## Garantien des Verkäufers', 'Der Verkäufer garantiert im Wege selbständiger Garantieversprechen (§ 311 Abs. 1 BGB): '
         'Bestand und lastenfreies Eigentum an den Geschäftsanteilen, ordnungsgemäße Jahresabschlüsse, Bestand wesentlicher Verträge, Einhaltung '
         'öffentlich-rechtlicher Vorschriften, keine Rechtsstreitigkeiten. Die Haftung ist auf {{haftungshoechstbetrag}} begrenzt; die gesetzlichen '
         'Gewährleistungsrechte werden ausgeschlossen, soweit gesetzlich zulässig.', '[[fin]]',
         '[[si steuerfreistellung]]', '## Steuerfreistellung', 'Der Verkäufer stellt den Käufer von Steuern frei, die auf Zeiträume bis zum Stichtag '
         'entfallen und nicht im Jahresabschluss zurückgestellt sind.', '[[fin]]',
         '[[si wettbewerb]]', '## Wettbewerbsverbot', 'Der Verkäufer unterlässt für zwei Jahre nach Vollzug jede Wettbewerbstätigkeit.', '[[fin]]',
         '## Gesellschafterliste', 'Der beurkundende Notar reicht nach Wirksamwerden der Abtretung eine aktualisierte Gesellschafterliste zum '
         'Handelsregister ein (§ 40 Abs. 2 GmbHG).',
         '## Kosten, Schlussbestimmungen', 'Die Kosten der Beurkundung trägt der Käufer. Es gilt deutsches Recht.', '', '{{ort}}, den {{datum}}']},
    {'id': 'de_absichtserklaerung', 'langue': 'de', 'droit': 'DE', 'categorie': 'operation', 'formes': (), 'titre': 'Absichtserklärung (Letter of Intent)',
     'resume': _R('Lettre d’intention de droit allemand (non contraignante sauf exclusivité et confidentialité).', 'Letter of intent (German law).',
                  'Unverbindliche Absichtserklärung mit verbindlicher Exklusivität und Vertraulichkeit.', 'Carta de intenciones (derecho alemán).'),
     'mots': ('absichtserklärung', 'letter of intent'),
     'variables': [('absender', 'Interessent', ''), ('empfaenger', 'Empfänger', ''), ('zielgesellschaft', 'Zielgesellschaft', ''), ('preis', 'Indikativer Kaufpreis', ''),
                   ('exklusivitaet', 'Exklusivitätsfrist', '8 Wochen'), ('ort', 'Ort', ''), ('datum', 'Datum', '')],
     'clauses': [('exklusiv', 'Exklusivität', True, '')], 'fixes': {}, 'texte': [
         '{{absender}}', '', 'An {{empfaenger}}', '', '{{ort}}, den {{datum}}', '', '# Absichtserklärung betreffend {{zielgesellschaft}}', '',
         '## Transaktion', 'Wir beabsichtigen, sämtliche Geschäftsanteile an {{zielgesellschaft}} zu erwerben. Der indikative Kaufpreis beträgt '
         '{{preis}} (cash-free/debt-free, normalisiertes Working Capital).',
         '## Due Diligence', 'Ein verbindliches Angebot setzt eine zufriedenstellende rechtliche, steuerliche und finanzielle Due Diligence voraus.',
         '[[si exklusiv]]', '## Exklusivität', 'Der Empfänger verpflichtet sich für {{exklusivitaet}}, keine Verhandlungen mit Dritten über eine '
         'vergleichbare Transaktion zu führen. Diese Verpflichtung ist rechtsverbindlich.', '[[fin]]',
         '## Vertraulichkeit', 'Die Parteien behandeln alle Informationen vertraulich. Diese Verpflichtung ist rechtsverbindlich.',
         '## Unverbindlichkeit', 'Mit Ausnahme der Regelungen zu Exklusivität, Vertraulichkeit, Kosten und anwendbarem Recht begründet dieses '
         'Schreiben keine Verpflichtung zum Abschluss eines Vertrags. Ein Abbruch der Verhandlungen begründet keine Haftung, außer nach den '
         'Grundsätzen der culpa in contrahendo (§ 311 Abs. 2 BGB).', '## Anwendbares Recht', 'Es gilt deutsches Recht.', '', '{{absender}}']},
    {'id': 'de_dienstleistungsvertrag', 'langue': 'de', 'droit': 'DE', 'categorie': 'contrat', 'formes': (), 'titre': 'Dienstleistungsvertrag',
     'resume': _R('Contrat de prestation de services de droit allemand (§§ 611 ff. BGB).', 'Services agreement (German law).',
                  'Dienstleistungsvertrag nach §§ 611 ff. BGB zwischen Unternehmern.', 'Contrato de servicios (derecho alemán).'),
     'mots': ('dienstleistungsvertrag', 'dienstvertrag'),
     'variables': [('auftragnehmer', 'Auftragnehmer', ''), ('auftraggeber', 'Auftraggeber', ''), ('leistungen', 'Leistungen', ''), ('verguetung', 'Vergütung', ''),
                   ('zahlungsziel', 'Zahlungsziel', '30 Tage nach Rechnungsstellung'), ('kuendigungsfrist', 'Kündigungsfrist', 'drei Monate zum Monatsende'),
                   ('gerichtsstand', 'Gerichtsstand', ''), ('ort', 'Ort', ''), ('datum', 'Datum', '')],
     'clauses': [('nutzungsrechte', 'Einräumung von Nutzungsrechten', True, ''), ('avv', 'Auftragsverarbeitung (Art. 28 DSGVO)', True, ''),
                 ('haftungsbegrenzung', 'Haftungsbegrenzung', True, '')],
     'fixes': {}, 'texte': [
         '# Dienstleistungsvertrag', '', 'zwischen {{auftraggeber}} – „Auftraggeber“ – und {{auftragnehmer}} – „Auftragnehmer“ –', '',
         '## Vertragsgegenstand', 'Der Auftragnehmer erbringt folgende Dienstleistungen: {{leistungen}}. Geschuldet ist die Erbringung der Dienste, '
         'nicht ein bestimmter Erfolg (§§ 611 ff. BGB).',
         '## Vergütung', 'Der Auftraggeber zahlt {{verguetung}} zzgl. gesetzlicher Umsatzsteuer, zahlbar {{zahlungsziel}}. Bei Verzug gelten die '
         'Verzugszinsen nach § 288 Abs. 2 BGB und die Pauschale nach § 288 Abs. 5 BGB.',
         '## Laufzeit, Kündigung', 'Der Vertrag läuft auf unbestimmte Zeit und kann mit einer Frist von {{kuendigungsfrist}} gekündigt werden. Das '
         'Recht zur außerordentlichen Kündigung aus wichtigem Grund (§ 626 BGB) bleibt unberührt.',
         '[[si haftungsbegrenzung]]', '## Haftung', 'Der Auftragnehmer haftet unbeschränkt bei Vorsatz, grober Fahrlässigkeit und Verletzung von Leben, '
         'Körper oder Gesundheit. Bei leicht fahrlässiger Verletzung wesentlicher Vertragspflichten ist die Haftung auf den vertragstypischen, '
         'vorhersehbaren Schaden begrenzt; im Übrigen ist sie ausgeschlossen.', '[[fin]]',
         '[[si nutzungsrechte]]', '## Nutzungsrechte', 'Der Auftragnehmer räumt dem Auftraggeber an den Arbeitsergebnissen ein ausschließliches, zeitlich '
         'und räumlich unbeschränktes Nutzungsrecht ein, mit vollständiger Zahlung der Vergütung.', '[[fin]]',
         '## Vertraulichkeit', 'Die Parteien behandeln vertrauliche Informationen und Geschäftsgeheimnisse im Sinne des GeschGehG vertraulich.',
         '[[si avv]]', '## Datenschutz', 'Soweit der Auftragnehmer personenbezogene Daten im Auftrag verarbeitet, schließen die Parteien einen Vertrag '
         'nach Art. 28 DSGVO.', '[[fin]]',
         '## Schlussbestimmungen', 'Es gilt deutsches Recht. Gerichtsstand ist {{gerichtsstand}}. Änderungen bedürfen der Textform.', '',
         '{{ort}}, den {{datum}}']},
    {'id': 'de_geheimhaltung', 'langue': 'de', 'droit': 'DE', 'categorie': 'contrat', 'formes': (), 'titre': 'Geheimhaltungsvereinbarung',
     'resume': _R('Accord de confidentialité de droit allemand (GeschGehG).', 'Non-disclosure agreement (German law).',
                  'Gegenseitige Geheimhaltungsvereinbarung (NDA) unter Berücksichtigung des GeschGehG.', 'Acuerdo de confidencialidad (derecho alemán).'),
     'mots': ('geheimhaltungsvereinbarung', 'vertraulichkeitsvereinbarung', 'nda'),
     'variables': [('partei_a', 'Partei A', ''), ('partei_b', 'Partei B', ''), ('zweck', 'Zweck', ''), ('dauer', 'Dauer', 'fünf Jahre'),
                   ('vertragsstrafe', 'Vertragsstrafe (EUR)', ''), ('ort', 'Ort', ''), ('datum', 'Datum', '')],
     'clauses': [('strafe', 'Vertragsstrafe', False, '')], 'fixes': {}, 'texte': [
         '# Geheimhaltungsvereinbarung', '', 'zwischen {{partei_a}} und {{partei_b}}', '',
         '## Zweck', 'Die Parteien tauschen im Zusammenhang mit {{zweck}} vertrauliche Informationen aus.',
         '## Vertrauliche Informationen', 'Vertraulich sind alle Informationen, die als vertraulich gekennzeichnet oder erkennbar sind, einschließlich '
         'Geschäftsgeheimnissen im Sinne des § 2 GeschGehG, mit Ausnahme offenkundiger oder bereits bekannter Informationen.',
         '## Pflichten', 'Die empfangende Partei nutzt die Informationen nur für den Zweck, schützt sie durch angemessene Geheimhaltungsmaßnahmen und '
         'gibt sie nur an zur Verschwiegenheit verpflichtete Personen weiter. Reverse Engineering ist untersagt.',
         '[[si strafe]]', '## Vertragsstrafe', 'Für jeden schuldhaften Verstoß ist eine Vertragsstrafe von {{vertragsstrafe}} EUR verwirkt; '
         'weitergehender Schadensersatz bleibt vorbehalten (unter Anrechnung).', '[[fin]]',
         '## Dauer', 'Die Pflichten gelten für {{dauer}} ab Unterzeichnung.', '## Recht', 'Es gilt deutsches Recht.', '', '{{ort}}, den {{datum}}']},
    {'id': 'de_agb_b2b', 'langue': 'de', 'droit': 'DE', 'categorie': 'contrat', 'formes': (), 'titre': 'Allgemeine Verkaufsbedingungen (B2B)',
     'resume': _R('Conditions générales de vente B2B de droit allemand (§§ 305 ff., 310 BGB).', 'B2B terms of sale (German law).',
                  'AGB für Verträge mit Unternehmern (§ 310 Abs. 1 BGB).', 'Condiciones generales B2B (derecho alemán).'),
     'mots': ('agb', 'allgemeine geschäftsbedingungen', 'verkaufsbedingungen'),
     'variables': [('verkaeufer', 'Verkäufer', ''), ('zahlungsziel', 'Zahlungsziel', '14 Tage netto'), ('gerichtsstand', 'Gerichtsstand', '')],
     'clauses': [('eigentumsvorbehalt', 'Verlängerter Eigentumsvorbehalt', True, '')], 'fixes': {}, 'texte': [
         '# Allgemeine Verkaufsbedingungen der {{verkaeufer}}', '',
         '## Geltungsbereich', 'Diese Bedingungen gelten ausschließlich gegenüber Unternehmern (§ 14 BGB). Abweichende Bedingungen des Käufers '
         'gelten nur bei ausdrücklicher schriftlicher Zustimmung.',
         '## Vertragsschluss', 'Angebote sind freibleibend. Ein Vertrag kommt mit unserer Auftragsbestätigung zustande.',
         '## Preise und Zahlung', 'Preise verstehen sich netto zzgl. Umsatzsteuer. Zahlung: {{zahlungsziel}}. Verzugszinsen nach § 288 Abs. 2 BGB.',
         '## Lieferung, Gefahrübergang', 'Lieferfristen sind unverbindlich, sofern nicht ausdrücklich vereinbart. Die Gefahr geht mit Übergabe an den '
         'Spediteur über.',
         '## Mängelrechte', 'Der Käufer hat die Ware unverzüglich zu untersuchen und Mängel zu rügen (§ 377 HGB). Die Verjährungsfrist für '
         'Mängelansprüche beträgt ein Jahr, außer bei Vorsatz und in den Fällen des § 438 Abs. 1 Nr. 2 BGB.',
         '[[si eigentumsvorbehalt]]', '## Eigentumsvorbehalt', 'Die Ware bleibt bis zur vollständigen Bezahlung aller Forderungen aus der '
         'Geschäftsbeziehung unser Eigentum. Der Käufer tritt Forderungen aus der Weiterveräußerung bereits jetzt an uns ab.', '[[fin]]',
         '## Haftung', 'Wir haften unbeschränkt bei Vorsatz und grober Fahrlässigkeit; im Übrigen nur bei Verletzung wesentlicher Vertragspflichten, '
         'begrenzt auf den vorhersehbaren Schaden.',
         '## Recht, Gerichtsstand', 'Es gilt deutsches Recht unter Ausschluss des UN-Kaufrechts. Gerichtsstand ist {{gerichtsstand}}.']},
    {'id': 'de_gesellschafterdarlehen', 'langue': 'de', 'droit': 'DE', 'categorie': 'contrat', 'formes': ('GmbH', 'UG'), 'titre': 'Gesellschafterdarlehensvertrag',
     'resume': _R('Prêt d’associé de droit allemand (§ 488 BGB, rang subordonné § 39 InsO).', 'Shareholder loan agreement (German law).',
                  'Gesellschafterdarlehen mit Rangrücktritt.', 'Préstamo de socio (derecho alemán).'),
     'mots': ('gesellschafterdarlehen', 'darlehensvertrag'),
     'variables': [('darlehensgeber', 'Darlehensgeber (Gesellschafter)', ''), ('firma', 'Gesellschaft', ''), ('betrag', 'Darlehensbetrag (EUR)', ''),
                   ('zins', 'Zinssatz p.a.', ''), ('laufzeit', 'Laufzeit', ''), ('ort', 'Ort', ''), ('datum', 'Datum', '')],
     'clauses': [('rangruecktritt', 'Qualifizierter Rangrücktritt', False, '')], 'fixes': {}, 'texte': [
         '# Darlehensvertrag', '', 'zwischen {{darlehensgeber}} – „Darlehensgeber“ – und {{firma}} – „Darlehensnehmerin“ –', '',
         '## Darlehen', 'Der Darlehensgeber gewährt der Darlehensnehmerin ein Darlehen über {{betrag}} EUR (§ 488 BGB).',
         '## Zinsen', 'Das Darlehen wird mit {{zins}} p.a. verzinst; die Zinsen sind jährlich nachträglich fällig. [ZU PRÜFEN: Fremdüblichkeit]',
         '## Laufzeit, Rückzahlung', 'Das Darlehen hat eine Laufzeit von {{laufzeit}}. Eine vorzeitige Rückzahlung ist jederzeit zulässig.',
         'Hinweis: Forderungen auf Rückgewähr von Gesellschafterdarlehen sind in der Insolvenz nachrangig (§ 39 Abs. 1 Nr. 5 InsO); Rückzahlungen '
         'im letzten Jahr vor dem Insolvenzantrag sind anfechtbar (§ 135 InsO).',
         '[[si rangruecktritt]]', '## Rangrücktritt', 'Der Darlehensgeber tritt mit seinen Forderungen im Rang hinter die Forderungen aller anderen '
         'Gläubiger zurück; Zahlungen dürfen nur aus künftigen Jahresüberschüssen, Liquidationsüberschüssen oder sonstigem freien Vermögen geleistet '
         'werden, solange eine Insolvenzreife andernfalls einträte.', '[[fin]]',
         '## Schlussbestimmungen', 'Es gilt deutsches Recht.', '', '{{ort}}, den {{datum}}']},
]

# ================================================================================================ ESPAGNE
ES_EST_VARS = [
    ('denominacion', 'Denominación social', ''), ('domicilio', 'Domicilio social', ''), ('objeto', 'Objeto social', ''),
    ('cnae', 'Código CNAE de la actividad principal', ''), ('capital', 'Capital social (euros)', ''), ('numero', 'Número de participaciones / acciones', ''),
    ('nominal', 'Valor nominal (euros)', ''), ('socios', 'Socios y aportaciones (uno por línea)', ''), ('ejercicio', 'Ejercicio social', 'el año natural'),
    ('plazo_preferente', 'Plazo de adquisición preferente (días)', '30'),
]
ES_EST_CLAUSES = [
    ('adquisicion_preferente', 'Derecho de adquisición preferente', True, ''), ('acompanamiento', 'Derecho de acompañamiento (tag-along)', False, ''),
    ('arrastre', 'Obligación de arrastre (drag-along)', False, ''), ('consejo', 'Consejo de administración', False, ''),
    ('retribucion', 'Cargo de administrador retribuido', False, ''), ('prestaciones_accesorias', 'Prestaciones accesorias', False, ''),
]


def _es_estatutos(sa=False):
    tit = 'acciones' if sa else 'participaciones sociales'
    return [
        '# Estatutos sociales de {{denominacion}}, ' + ('S.A.' if sa else 'S.L.'), '',
        '## Denominación', 'La sociedad se denomina {{denominacion}}, ' + ('Sociedad Anónima' if sa else 'Sociedad de Responsabilidad Limitada')
        + ', y se rige por el texto refundido de la Ley de Sociedades de Capital, aprobado por el Real Decreto Legislativo 1/2010, de 2 de julio '
        '(«LSC»), y por los presentes estatutos.',
        '## Objeto social', 'La sociedad tiene por objeto {{objeto}} (CNAE {{cnae}}).',
        '## Domicilio', 'El domicilio social se fija en {{domicilio}}. El órgano de administración podrá trasladarlo dentro del territorio nacional '
        '(art. 285.2 LSC).',
        '## Duración', 'La sociedad se constituye por tiempo indefinido y da comienzo a sus operaciones el día del otorgamiento de la escritura.',
        '## Capital social', 'El capital social es de {{capital}} euros, dividido en {{numero}} ' + tit + ' de {{nominal}} euros de valor nominal '
        'cada una, numeradas correlativamente, ' + ('totalmente suscritas y desembolsadas al menos en una cuarta parte (art. 79 LSC).' if sa else
                                                    'totalmente asumidas y desembolsadas.'),
        ('Las acciones se representarán por medio de títulos nominativos. [A COMPLETAR: títulos o anotaciones en cuenta]' if sa else
         'Las participaciones no podrán incorporarse a títulos valores ni representarse mediante anotaciones en cuenta. La sociedad llevará un Libro '
         'registro de socios (art. 104 LSC).'),
        '## Transmisión de las ' + tit,
        ('Las restricciones a la libre transmisibilidad de las acciones solo serán válidas cuando recaigan sobre acciones nominativas y no hagan '
         'prácticamente intransmisible la acción (art. 123 LSC). La transmisión a terceros requerirá la autorización de la sociedad, que solo podrá '
         'denegarla por causa justificada.' if sa else
         'Será libre la transmisión voluntaria por actos inter vivos entre socios, al cónyuge, ascendiente o descendiente del socio y a sociedades del '
         'mismo grupo. Las demás transmisiones se rigen por el artículo 107 LSC y por lo dispuesto a continuación. La transmisión se formalizará en '
         'documento público (art. 106 LSC).'),
        '[[si adquisicion_preferente]]', 'El socio que pretenda transmitir ' + tit + ' a un tercero lo comunicará por escrito al órgano de administración, '
        'indicando número, precio y adquirente. Los demás socios tendrán un derecho de adquisición preferente, a prorrata de su participación, '
        'durante {{plazo_preferente}} días; en su defecto, la sociedad podrá adquirirlas en los términos legales. El precio, a falta de acuerdo, '
        'será el valor razonable fijado por un experto independiente designado por el registrador mercantil.', '[[fin]]',
        '[[si acompanamiento]]', '## Derecho de acompañamiento', 'Si un socio pretende transmitir ' + tit + ' que confieran el control de la sociedad, '
        'los demás socios podrán exigir que el adquirente adquiera también las suyas en las mismas condiciones.', '[[fin]]',
        '[[si arrastre]]', '## Obligación de arrastre', 'Si socios titulares de al menos el 75 % del capital aceptan una oferta de un tercero por la '
        'totalidad del capital, los demás socios estarán obligados a transmitir sus ' + tit + ' en las mismas condiciones. [A VERIFICAR: '
        'incorporación estatutaria con el consentimiento de todos los socios, art. 108 y 291 LSC]', '[[fin]]',
        '[[si prestaciones_accesorias]]', '## Prestaciones accesorias', 'Los socios quedan obligados a las siguientes prestaciones accesorias '
        '(art. 86 LSC): [A COMPLETAR: contenido, carácter gratuito o retribuido].', '[[fin]]',
        '## Junta general', 'La junta general será convocada por el órgano de administración ' + (
            'mediante anuncio en la página web de la sociedad o, en su defecto, en el BORME y en un diario (art. 173 LSC), con al menos un mes de '
            'antelación (art. 176 LSC).' if sa else
            'mediante comunicación individual y escrita a cada socio en el domicilio que conste en el Libro registro de socios, con al menos quince '
            'días de antelación (arts. 173 y 176 LSC).') + ' Se admite la asistencia telemática (art. 182 LSC).',
        ('Los acuerdos se adoptarán por mayoría ordinaria de los votos válidamente emitidos; para los acuerdos del art. 194 LSC se requerirán el '
         'quórum y las mayorías del art. 201 LSC.' if sa else
         'Los acuerdos se adoptarán por mayoría de los votos válidamente emitidos, siempre que representen al menos un tercio de los votos '
         'correspondientes a las participaciones; las modificaciones estatutarias requerirán más de la mitad, y los acuerdos del art. 199.b LSC '
         'al menos dos tercios (arts. 198 y 199 LSC).'),
        '## Órgano de administración', '[[si consejo]]La sociedad será administrada por un consejo de administración de [A COMPLETAR] miembros.'
        '[[sinon]]La sociedad será administrada, a elección de la junta, por un administrador único, varios administradores solidarios o dos '
        'mancomunados (art. 210 LSC).[[fin]] ' + ('Los administradores ejercerán su cargo por un plazo de [A COMPLETAR] años, como máximo seis '
                                                   '(art. 221 LSC).' if sa else 'Los administradores ejercerán su cargo por tiempo indefinido (art. 221 LSC).'),
        '[[si retribucion]]', 'El cargo de administrador es retribuido: [A COMPLETAR: sistema de retribución, art. 217 LSC]. El importe máximo anual '
        'será aprobado por la junta general.', '[[sinon]]', 'El cargo de administrador es gratuito.', '[[fin]]',
        '## Ejercicio social y cuentas', 'El ejercicio social coincide con {{ejercicio}}. Las cuentas anuales se formularán en el plazo de tres meses '
        'desde el cierre y se someterán a la junta general dentro de los seis meses siguientes (arts. 253 y 164 LSC).',
        '## Reserva legal', 'Se destinará a reserva legal una cifra igual al 10 % del beneficio hasta alcanzar, al menos, el 20 % del capital social '
        '(art. 274 LSC).',
        '## Disolución y liquidación', 'La sociedad se disolverá por las causas legales. Los administradores pasarán a ser liquidadores, salvo acuerdo '
        'en contrario de la junta.', '',
        'Socios fundadores:', '{{socios}}', '',
        'Nota: la sociedad se constituye mediante escritura pública e inscripción en el Registro Mercantil (art. 20 LSC). '
        + ('El capital mínimo de la S.A. es de 60.000 euros (art. 4.3 LSC).' if sa else
           '[A VERIFICAR: régimen aplicable si el capital es inferior a 3.000 euros (art. 4 LSC).]'),
    ]


TEMPLATES += [
    {'id': 'es_estatutos_sl', 'langue': 'es', 'droit': 'ES', 'categorie': 'statuts', 'formes': ('S.L.',), 'titre': 'Estatutos sociales de S.L.',
     'resume': _R('Statuts de S.L. espagnole (LSC) : adquisición preferente, tag/drag-along, administration.', 'Spanish S.L. articles of association.',
                  'Satzung einer spanischen S.L.', 'Estatutos de S.L. con adquisición preferente, acompañamiento y arrastre.'),
     'mots': ('estatutos', 'sociedad limitada', 's.l.', 'sl'), 'variables': ES_EST_VARS, 'clauses': ES_EST_CLAUSES, 'fixes': {}, 'texte': _es_estatutos()},
    {'id': 'es_estatutos_sa', 'langue': 'es', 'droit': 'ES', 'categorie': 'statuts', 'formes': ('S.A.',), 'titre': 'Estatutos sociales de S.A.',
     'resume': _R('Statuts de S.A. espagnole (LSC).', 'Spanish S.A. articles of association.', 'Satzung einer spanischen S.A.', 'Estatutos de S.A.'),
     'mots': ('estatutos', 'sociedad anónima', 's.a.'), 'variables': ES_EST_VARS, 'clauses': ES_EST_CLAUSES, 'fixes': {}, 'texte': _es_estatutos(sa=True)},
    {'id': 'es_pacto_socios', 'langue': 'es', 'droit': 'ES', 'categorie': 'pacte', 'formes': ('S.L.', 'S.A.'), 'titre': 'Pacto de socios',
     'resume': _R('Pacte d’associés de droit espagnol (art. 29 LSC).', 'Shareholders’ agreement (Spanish law).', 'Gesellschaftervereinbarung (spanisches Recht).',
                  'Pacto de socios parasocial (art. 29 LSC).'),
     'mots': ('pacto de socios', 'pacto parasocial', 'pacto de accionistas'),
     'variables': [('partes', 'Partes (una por línea)', ''), ('denominacion', 'Sociedad', ''), ('duracion', 'Duración', 'diez años'), ('lugar', 'Lugar', ''),
                   ('fecha', 'Fecha', '')],
     'clauses': [('materias_reservadas', 'Materias reservadas', True, ''), ('acompanamiento', 'Acompañamiento', True, ''), ('arrastre', 'Arrastre', True, ''),
                 ('lock_up', 'Intransmisibilidad temporal (lock-up)', False, ''), ('no_competencia', 'No competencia', False, '')],
     'fixes': {}, 'texte': [
         '# Pacto de socios de {{denominacion}}', '', 'Reunidos:', '{{partes}}', '',
         '## Naturaleza', 'El presente pacto es un pacto parasocial; conforme al artículo 29 LSC no es oponible a la sociedad, por lo que las partes se '
         'obligan a ejercer sus derechos de voto para darle cumplimiento y, en su caso, a incorporarlo a los estatutos.',
         '[[si materias_reservadas]]', '## Materias reservadas', 'Requerirán el voto favorable de socios que representen al menos el 75 % del capital: '
         'presupuesto anual, inversiones superiores a [A COMPLETAR] euros, endeudamiento, aumentos de capital, venta de activos esenciales.', '[[fin]]',
         '[[si lock_up]]', '## Intransmisibilidad', 'Ningún socio transmitirá sus participaciones durante [A COMPLETAR] años.', '[[fin]]',
         '[[si acompanamiento]]', '## Derecho de acompañamiento', 'En caso de transmisión que otorgue el control, los demás socios podrán vender en las '
         'mismas condiciones.', '[[fin]]',
         '[[si arrastre]]', '## Obligación de arrastre', 'Si socios titulares del 75 % aceptan una oferta por el 100 %, los demás deberán vender en las '
         'mismas condiciones.', '[[fin]]',
         '[[si no_competencia]]', '## No competencia', 'Los socios que desempeñen funciones ejecutivas no competirán con la sociedad durante su '
         'permanencia y [A COMPLETAR] meses después.', '[[fin]]',
         '## Duración', 'El pacto tendrá una duración de {{duracion}}.', '## Ley aplicable', 'El pacto se rige por la ley española.', '',
         'En {{lugar}}, a {{fecha}}.']},
    {'id': 'es_compraventa_participaciones', 'langue': 'es', 'droit': 'ES', 'categorie': 'operation', 'formes': ('S.L.',),
     'titre': 'Contrato de compraventa de participaciones sociales',
     'resume': _R('Cession de participaciones de S.L. espagnole (document public, art. 106 LSC), garanties.', 'Spanish share purchase agreement (S.L.).',
                  'Kaufvertrag über Geschäftsanteile einer spanischen S.L.', 'Compraventa de participaciones con manifestaciones y garantías.'),
     'mots': ('compraventa de participaciones', 'compraventa de acciones', 'cesión de participaciones'),
     'variables': [('vendedor', 'Vendedor', ''), ('comprador', 'Comprador', ''), ('denominacion', 'Sociedad', ''), ('participaciones', 'Participaciones (números)', ''),
                   ('precio', 'Precio (euros)', ''), ('lugar', 'Lugar', ''), ('fecha', 'Fecha', '')],
     'clauses': [('garantias', 'Manifestaciones y garantías', True, ''), ('condiciones', 'Condiciones suspensivas', False, '')], 'fixes': {}, 'texte': [
         '# Contrato de compraventa de participaciones sociales', '', 'De una parte, {{vendedor}} («Vendedor»); de otra, {{comprador}} («Comprador»).', '',
         '## Objeto', 'El Vendedor vende al Comprador las participaciones números {{participaciones}} de {{denominacion}}, libres de cargas.',
         '## Precio', 'El precio es de {{precio}} euros, que se paga a la firma de la escritura pública.',
         '[[si condiciones]]', '## Condiciones suspensivas', 'La eficacia queda sujeta a [A COMPLETAR].', '[[fin]]',
         '## Régimen de transmisión', 'Se ha observado el régimen estatutario y legal de transmisión (art. 107 LSC). La transmisión se formalizará en '
         'documento público (art. 106 LSC) y se comunicará a la sociedad para su inscripción en el Libro registro de socios (art. 104 LSC).',
         '[[si garantias]]', '## Manifestaciones y garantías', 'El Vendedor manifiesta la titularidad y libertad de cargas de las participaciones, la '
         'veracidad de las cuentas anuales, la inexistencia de litigios y contingencias fiscales o laborales no reveladas, y responderá de los daños '
         'derivados de su inexactitud hasta [A COMPLETAR] euros durante [A COMPLETAR] meses.', '[[fin]]',
         '## Ley y jurisdicción', 'Ley española; juzgados y tribunales de [A COMPLETAR].', '', 'En {{lugar}}, a {{fecha}}.']},
    {'id': 'es_carta_intenciones', 'langue': 'es', 'droit': 'ES', 'categorie': 'operation', 'formes': (), 'titre': 'Carta de intenciones',
     'resume': _R('Lettre d’intention de droit espagnol.', 'Letter of intent (Spanish law).', 'Absichtserklärung (spanisches Recht).', 'Carta de intenciones no vinculante.'),
     'mots': ('carta de intenciones', 'letter of intent'),
     'variables': [('emisor', 'Emisor', ''), ('destinatario', 'Destinatario', ''), ('sociedad', 'Sociedad objetivo', ''), ('precio', 'Precio indicativo', ''),
                   ('exclusividad', 'Periodo de exclusividad', '60 días'), ('lugar', 'Lugar', ''), ('fecha', 'Fecha', '')],
     'clauses': [('exclusiva', 'Exclusividad', True, '')], 'fixes': {}, 'texte': [
         '{{emisor}}', '', 'A la atención de {{destinatario}}', '', 'En {{lugar}}, a {{fecha}}', '', '# Carta de intenciones — {{sociedad}}', '',
         '## Operación', 'Manifestamos nuestro interés en adquirir el 100 % del capital de {{sociedad}} por un precio indicativo de {{precio}}, sujeto a '
         'una revisión legal, fiscal y financiera (due diligence).',
         '[[si exclusiva]]', '## Exclusividad', 'Durante {{exclusividad}}, el destinatario no negociará con terceros. Esta obligación es vinculante.', '[[fin]]',
         '## Confidencialidad', 'La información intercambiada es confidencial. Esta obligación es vinculante.',
         '## Carácter no vinculante', 'Salvo exclusividad, confidencialidad, gastos y ley aplicable, la presente carta no obliga a celebrar contrato '
         'alguno; las partes negociarán de buena fe (art. 7 del Código Civil).', '## Ley aplicable', 'Ley española.', '', '{{emisor}}']},
    {'id': 'es_prestacion_servicios', 'langue': 'es', 'droit': 'ES', 'categorie': 'contrat', 'formes': (), 'titre': 'Contrato de prestación de servicios',
     'resume': _R('Contrat de prestation de services de droit espagnol (art. 1544 Código Civil).', 'Services agreement (Spanish law).',
                  'Dienstleistungsvertrag (spanisches Recht).', 'Contrato de arrendamiento de servicios entre empresas.'),
     'mots': ('prestación de servicios', 'contrato de servicios'),
     'variables': [('prestador', 'Prestador', ''), ('cliente', 'Cliente', ''), ('servicios', 'Servicios', ''), ('precio', 'Precio', ''),
                   ('plazo_pago', 'Plazo de pago', '30 días desde la factura'), ('lugar', 'Lugar', ''), ('fecha', 'Fecha', '')],
     'clauses': [('propiedad_intelectual', 'Cesión de propiedad intelectual', True, ''), ('encargado', 'Encargado del tratamiento (art. 28 RGPD)', True, '')],
     'fixes': {}, 'texte': [
         '# Contrato de prestación de servicios', '', 'Entre {{prestador}} («Prestador») y {{cliente}} («Cliente»).', '',
         '## Objeto', 'El Prestador prestará los siguientes servicios: {{servicios}} (arrendamiento de servicios, art. 1544 del Código Civil).',
         '## Precio y pago', 'El Cliente abonará {{precio}} más IVA en un plazo de {{plazo_pago}}, dentro de los límites de la Ley 3/2004 de lucha contra '
         'la morosidad en las operaciones comerciales.',
         '## Independencia', 'El Prestador actúa con plena autonomía, sin relación laboral alguna.',
         '[[si propiedad_intelectual]]', '## Propiedad intelectual', 'El Prestador cede al Cliente, en exclusiva, los derechos de explotación sobre los '
         'resultados, para todo el mundo y por el plazo máximo legal.', '[[fin]]',
         '[[si encargado]]', '## Protección de datos', 'Cuando el Prestador trate datos personales por cuenta del Cliente, actuará como encargado del '
         'tratamiento conforme al art. 28 del RGPD y a la Ley Orgánica 3/2018.', '[[fin]]',
         '## Confidencialidad', 'Las partes guardarán confidencialidad conforme a la Ley 1/2019 de Secretos Empresariales.',
         '## Resolución', 'Cualquiera de las partes podrá resolver el contrato por incumplimiento grave no subsanado en 30 días (art. 1124 del Código Civil).',
         '## Ley y jurisdicción', 'Ley española; juzgados de [A COMPLETAR].', '', 'En {{lugar}}, a {{fecha}}.']},
    {'id': 'es_confidencialidad', 'langue': 'es', 'droit': 'ES', 'categorie': 'contrat', 'formes': (), 'titre': 'Acuerdo de confidencialidad',
     'resume': _R('Accord de confidentialité de droit espagnol (Ley 1/2019).', 'Non-disclosure agreement (Spanish law).',
                  'Geheimhaltungsvereinbarung (spanisches Recht).', 'Acuerdo de confidencialidad conforme a la Ley de Secretos Empresariales.'),
     'mots': ('acuerdo de confidencialidad', 'nda'),
     'variables': [('parte_a', 'Parte A', ''), ('parte_b', 'Parte B', ''), ('finalidad', 'Finalidad', ''), ('duracion', 'Duración', 'cinco años'),
                   ('lugar', 'Lugar', ''), ('fecha', 'Fecha', '')],
     'clauses': [('penalizacion', 'Cláusula penal', False, '')], 'fixes': {}, 'texte': [
         '# Acuerdo de confidencialidad', '', 'Entre {{parte_a}} y {{parte_b}}.', '',
         '## Finalidad', 'Las partes intercambiarán información confidencial con la finalidad de {{finalidad}}.',
         '## Información confidencial', 'Toda información no pública, incluidos los secretos empresariales en el sentido de la Ley 1/2019.',
         '## Obligaciones', 'Uso exclusivo para la finalidad, acceso restringido, medidas razonables de protección y devolución o destrucción a solicitud.',
         '[[si penalizacion]]', '## Cláusula penal', 'El incumplimiento dará lugar al pago de [A COMPLETAR] euros (art. 1152 del Código Civil), sin '
         'perjuicio de la indemnización de daños adicionales si así se pacta.', '[[fin]]',
         '## Duración', 'Las obligaciones subsistirán durante {{duracion}}.', '## Ley aplicable', 'Ley española.', '', 'En {{lugar}}, a {{fecha}}.']},
    {'id': 'es_condiciones_generales', 'langue': 'es', 'droit': 'ES', 'categorie': 'contrat', 'formes': (), 'titre': 'Condiciones generales de venta (B2B)',
     'resume': _R('Conditions générales de vente B2B de droit espagnol (Ley 7/1998).', 'B2B terms of sale (Spanish law).',
                  'Allgemeine Verkaufsbedingungen (spanisches Recht).', 'Condiciones generales de contratación entre empresas.'),
     'mots': ('condiciones generales', 'condiciones de venta'),
     'variables': [('vendedor', 'Vendedor', ''), ('plazo_pago', 'Plazo de pago', '30 días'), ('jurisdiccion', 'Jurisdicción', '')],
     'clauses': [('reserva_dominio', 'Reserva de dominio', True, '')], 'fixes': {}, 'texte': [
         '# Condiciones generales de venta de {{vendedor}}', '',
         '## Ámbito', 'Se aplican a las ventas a empresarios y profesionales y se incorporan al contrato conforme a la Ley 7/1998, sobre Condiciones '
         'Generales de la Contratación, siempre que el adherente haya podido conocerlas.',
         '## Pedidos y precios', 'Los pedidos se perfeccionan con nuestra confirmación. Los precios no incluyen IVA.',
         '## Pago', 'Plazo de pago: {{plazo_pago}}, dentro de los límites de la Ley 3/2004; el retraso devengará el interés de demora legal.',
         '[[si reserva_dominio]]', '## Reserva de dominio', 'La mercancía seguirá siendo propiedad del vendedor hasta su completo pago.', '[[fin]]',
         '## Responsabilidad', 'La responsabilidad del vendedor se limita al importe del pedido, salvo dolo o culpa grave.',
         '## Ley y jurisdicción', 'Ley española; juzgados de {{jurisdiccion}}.']},
    {'id': 'es_prestamo_socio', 'langue': 'es', 'droit': 'ES', 'categorie': 'contrat', 'formes': ('S.L.', 'S.A.'), 'titre': 'Contrato de préstamo de socio',
     'resume': _R('Prêt d’associé de droit espagnol (créance subordonnée en cas de concours).', 'Shareholder loan (Spanish law).',
                  'Gesellschafterdarlehen (spanisches Recht).', 'Préstamo de socio a la sociedad.'),
     'mots': ('préstamo de socio', 'préstamo participativo'),
     'variables': [('prestamista', 'Prestamista (socio)', ''), ('sociedad', 'Sociedad', ''), ('importe', 'Importe (euros)', ''), ('interes', 'Interés anual', ''),
                   ('plazo', 'Plazo', ''), ('lugar', 'Lugar', ''), ('fecha', 'Fecha', '')],
     'clauses': [], 'fixes': {}, 'texte': [
         '# Contrato de préstamo', '', 'Entre {{prestamista}} («Prestamista») y {{sociedad}} («Prestataria»).', '',
         '## Préstamo', 'El Prestamista entrega a la Prestataria la cantidad de {{importe}} euros (arts. 1740 y siguientes del Código Civil).',
         '## Intereses', 'El préstamo devenga un interés anual de {{interes}}. [A VERIFICAR: valor de mercado entre partes vinculadas]',
         '## Devolución', 'Se devolverá en un plazo de {{plazo}}.',
         'Nota: en caso de concurso, los créditos de socios con participación significativa pueden calificarse como subordinados conforme al texto '
         'refundido de la Ley Concursal. [A VERIFICAR]', '## Ley aplicable', 'Ley española.', '', 'En {{lugar}}, a {{fecha}}.']},
]

# ================================================================================================ ANGLETERRE ET PAYS DE GALLES
EN_ART_VARS = [
    ('company_name', 'Company name', ''), ('registered_office', 'Registered office (England and Wales)', ''),
    ('shareholders', 'Subscribers and shares (one per line)', ''), ('notice_days', 'Pre-emption offer period (days)', '21'),
    ('drag_threshold', 'Drag-along threshold', '75%'),
]


def _en_articles():
    return [
        '# The Companies Act 2006', '# Private company limited by shares', '# Articles of Association of {{company_name}} Limited', '',
        '## Preliminary', 'The model articles for private companies limited by shares contained in Schedule 1 to the Companies (Model Articles) '
        'Regulations 2008 (SI 2008/3229) (the "Model Articles") apply to the Company, except in so far as they are modified or excluded by these '
        'articles.',
        '## Liability of members', 'The liability of the members is limited to the amount, if any, unpaid on the shares held by them.',
        '## Directors', 'The minimum number of directors is one. Unless otherwise determined by ordinary resolution, there is no maximum. A director '
        'may participate in a meeting by any means of communication by which all participants can hear each other.',
        '[[si investor_director]]', '## Investor director', 'The holders of a majority of the [A] shares may appoint and remove one director (the '
        '"Investor Director") by notice to the Company.', '[[fin]]',
        '## Allotment of shares', 'Sections 561 and 562 of the Companies Act 2006 apply to any allotment of equity securities, save as disapplied by '
        'special resolution in accordance with section 569 or 570 of that Act.',
        '[[si pre_emption]]', '## Pre-emption on transfer', 'A shareholder wishing to transfer shares (other than a permitted transfer) shall give a '
        'transfer notice to the Company specifying the shares, the proposed price and the proposed transferee. The shares shall be offered to the '
        'other shareholders pro rata to their holdings, who may accept within {{notice_days}} days. Shares not taken up may be transferred to the '
        'proposed transferee at a price not lower than the price specified, within 60 days.', '[[fin]]',
        '[[si board_refusal]]', '## Refusal to register', 'The directors may, in their absolute discretion, refuse to register the transfer of a share, '
        'and shall give the transferee notice of refusal together with reasons within two months (section 771 of the Companies Act 2006).', '[[fin]]',
        '[[si tag_along]]', '## Tag-along', 'No transfer resulting in a person obtaining control of the Company shall be registered unless the '
        'proposed transferee has offered to buy all other shares on the same terms.', '[[fin]]',
        '[[si drag_along]]', '## Drag-along', 'If holders of at least {{drag_threshold}} of the shares accept a bona fide offer for all the shares, '
        'they may require all other shareholders to transfer their shares on the same terms. Any shareholder failing to do so appoints any director '
        'as its agent to execute the necessary transfer.', '[[fin]]',
        '[[si leaver]]', '## Compulsory transfer (leavers)', 'A shareholder who ceases to be an employee or director shall be deemed to have served a '
        'transfer notice; the price shall be fair value for a good leaver and the lower of fair value and subscription price for a bad leaver.', '[[fin]]',
        '## General meetings and resolutions', 'Ordinary resolutions require a simple majority (section 282) and special resolutions a majority of '
        'not less than 75% (section 283). Written resolutions may be passed in accordance with Chapter 2 of Part 13 of the Companies Act 2006.',
        '## Amendment', 'These articles may be amended by special resolution (section 21 of the Companies Act 2006).', '',
        'Subscribers:', '{{shareholders}}', '',
        'Note: on incorporation, the Company must keep registers including a register of people with significant control (Part 21A of the Companies '
        'Act 2006); directors and persons with significant control are subject to identity verification under the Economic Crime and Corporate '
        'Transparency Act 2023. [TO BE CHECKED: current Companies House requirements]',
    ]


TEMPLATES += [
    {'id': 'en_articles_ltd', 'langue': 'en', 'droit': 'EN', 'categorie': 'statuts', 'formes': ('Ltd',), 'titre': 'Articles of Association (private limited company)',
     'resume': _R('Statuts de private limited company anglaise (Companies Act 2006, Model Articles) : pre-emption, tag/drag-along, leavers.',
                  'English Ltd articles based on the Model Articles, with pre-emption, tag-along, drag-along and leaver provisions.',
                  'Satzung einer englischen Ltd.', 'Estatutos de una Ltd inglesa.'),
     'mots': ('articles of association', 'private limited company', 'ltd'), 'variables': EN_ART_VARS,
     'clauses': [('pre_emption', 'Pre-emption on transfer', True, ''), ('board_refusal', 'Directors may refuse registration', False, ''),
                 ('tag_along', 'Tag-along', True, ''), ('drag_along', 'Drag-along', True, ''), ('leaver', 'Good / bad leaver', False, ''),
                 ('investor_director', 'Investor director', False, '')],
     'fixes': {}, 'texte': _en_articles()},
    {'id': 'en_shareholders_agreement', 'langue': 'en', 'droit': 'EN', 'categorie': 'pacte', 'formes': ('Ltd',), 'titre': 'Shareholders’ Agreement',
     'resume': _R('Pacte d’actionnaires de droit anglais.', 'Shareholders’ agreement (English law): reserved matters, information, transfers, deadlock.',
                  'Gesellschaftervereinbarung (englisches Recht).', 'Pacto de socios (derecho inglés).'),
     'mots': ('shareholders agreement', 'shareholders’ agreement'),
     'variables': [('parties', 'Parties (one per line)', ''), ('company_name', 'Company', ''), ('company_number', 'Company number', ''),
                   ('date', 'Date', '')],
     'clauses': [('reserved_matters', 'Reserved matters', True, ''), ('information', 'Information rights', True, ''), ('deadlock', 'Deadlock (Russian roulette)', False, ''),
                 ('restrictive_covenants', 'Restrictive covenants', False, '')],
     'fixes': {}, 'texte': [
         '# Shareholders’ Agreement', '', 'This agreement is dated {{date}} between:', '{{parties}}', '', 'relating to {{company_name}} Limited (company number '
         '{{company_number}}) (the "Company").', '',
         '## Relationship with the articles', 'If there is any conflict between this agreement and the articles, this agreement prevails as between '
         'the shareholders, who shall exercise their votes to amend the articles accordingly.',
         '[[si reserved_matters]]', '## Reserved matters', 'The Company shall not, without the prior written consent of shareholders holding at least '
         '75% of the shares: adopt or amend the business plan; incur borrowing above [AMOUNT]; issue shares; acquire or dispose of any business; '
         'enter into any transaction with a shareholder.', '[[fin]]',
         '[[si information]]', '## Information', 'The Company shall supply each shareholder with monthly management accounts within 20 days of month '
         'end and annual audited accounts within four months of year end.', '[[fin]]',
         '## Transfers', 'Transfers of shares are subject to the pre-emption, tag-along and drag-along provisions of the articles.',
         '[[si deadlock]]', '## Deadlock', 'If a deadlock persists after escalation to the chairs of the shareholders, either party may serve a notice '
         'offering to buy the other’s shares or sell its own at a stated price (Russian roulette).', '[[fin]]',
         '[[si restrictive_covenants]]', '## Restrictive covenants', 'Each founder shareholder shall not, for 12 months after ceasing to hold shares, '
         'compete with or solicit customers or employees of the Company. These restrictions are reasonable and necessary to protect the goodwill of '
         'the Company. [TO BE CHECKED: enforceability]', '[[fin]]',
         '## Confidentiality', 'Each party shall keep confidential the terms of this agreement and information relating to the Company.',
         '## Governing law', 'This agreement and any non-contractual obligations arising out of it are governed by the law of England and Wales; the '
         'courts of England have exclusive jurisdiction.']},
    {'id': 'en_spa', 'langue': 'en', 'droit': 'EN', 'categorie': 'operation', 'formes': ('Ltd',), 'titre': 'Share Purchase Agreement',
     'resume': _R('Cession d’actions de Ltd anglaise : prix, completion, warranties, limitations, stamp duty.', 'Share purchase agreement (English law).',
                  'Anteilskaufvertrag (englisches Recht).', 'Compraventa de acciones (derecho inglés).'),
     'mots': ('share purchase agreement', 'spa'),
     'variables': [('seller', 'Seller', ''), ('buyer', 'Buyer', ''), ('company_name', 'Company', ''), ('shares', 'Sale shares', ''), ('price', 'Consideration', ''),
                   ('cap', 'Warranty cap', ''), ('date', 'Date', '')],
     'clauses': [('conditions', 'Conditions precedent', False, ''), ('warranties', 'Warranties and limitations', True, ''), ('tax_covenant', 'Tax covenant', True, ''),
                 ('restrictive', 'Seller restrictive covenants', False, '')],
     'fixes': {}, 'texte': [
         '# Share Purchase Agreement', '', 'This agreement is dated {{date}} between {{seller}} (the "Seller") and {{buyer}} (the "Buyer").', '',
         '## Sale and purchase', 'The Seller shall sell with full title guarantee and the Buyer shall buy {{shares}} in {{company_name}} Limited, free from '
         'all encumbrances.',
         '## Consideration', 'The consideration is {{price}}, payable in cash at completion.',
         '[[si conditions]]', '## Conditions', 'Completion is conditional on [CONDITIONS], to be satisfied or waived by the long-stop date [DATE].', '[[fin]]',
         '## Completion', 'At completion the Seller shall deliver duly executed stock transfer forms, share certificates and resignations of directors, '
         'and the Buyer shall pay the consideration. Stamp duty on the transfer is payable by the Buyer [TO BE CHECKED: current rate and HMRC process].',
         '[[si warranties]]', '## Warranties', 'The Seller gives the warranties in Schedule [X], qualified by the matters fairly disclosed in the '
         'disclosure letter. The Seller’s aggregate liability shall not exceed {{cap}}; claims must be notified within [24] months (tax claims: '
         '[seven] years); a de minimis and basket apply.', '[[fin]]',
         '[[si tax_covenant]]', '## Tax covenant', 'The Seller covenants to pay the Buyer an amount equal to any tax liability of the Company arising '
         'from events before completion and not provided for in the accounts.', '[[fin]]',
         '[[si restrictive]]', '## Restrictive covenants', 'The Seller shall not compete with the business of the Company for [24] months after '
         'completion.', '[[fin]]',
         '## Governing law', 'This agreement is governed by the law of England and Wales; the courts of England have exclusive jurisdiction.']},
    {'id': 'en_heads_of_terms', 'langue': 'en', 'droit': 'EN', 'categorie': 'operation', 'formes': (), 'titre': 'Letter of Intent / Heads of Terms',
     'resume': _R('Lettre d’intention de droit anglais (subject to contract).', 'Heads of terms, subject to contract, with binding exclusivity.',
                  'Absichtserklärung (englisches Recht).', 'Carta de intenciones (derecho inglés).'),
     'mots': ('heads of terms', 'letter of intent', 'term sheet'),
     'variables': [('buyer', 'Buyer', ''), ('seller', 'Seller', ''), ('target', 'Target', ''), ('price', 'Indicative price', ''), ('exclusivity', 'Exclusivity period', '8 weeks'),
                   ('date', 'Date', '')],
     'clauses': [('exclusivity_clause', 'Exclusivity (binding)', True, '')], 'fixes': {}, 'texte': [
         '# Heads of Terms — Subject to Contract', '', 'Date: {{date}}', 'Buyer: {{buyer}}', 'Seller: {{seller}}', 'Target: {{target}}', '',
         '## Transaction', 'Acquisition of the entire issued share capital of the Target for an indicative price of {{price}} on a cash-free, debt-free '
         'basis, subject to due diligence and contract.',
         '[[si exclusivity_clause]]', '## Exclusivity', 'For {{exclusivity}} the Seller shall not solicit or negotiate any alternative transaction. '
         'This paragraph is legally binding.', '[[fin]]',
         '## Confidentiality', 'The parties’ existing confidentiality undertakings apply. This paragraph is legally binding.',
         '## Status', 'Save for the paragraphs stated to be binding, costs and governing law, these heads of terms are not intended to create legally '
         'binding obligations. English law does not imply a general duty to negotiate in good faith.',
         '## Governing law', 'Law of England and Wales.']},
    {'id': 'en_services_agreement', 'langue': 'en', 'droit': 'EN', 'categorie': 'contrat', 'formes': (), 'titre': 'Services Agreement',
     'resume': _R('Contrat de prestation de services de droit anglais.', 'Business-to-business services agreement (English law).',
                  'Dienstleistungsvertrag (englisches Recht).', 'Contrato de servicios (derecho inglés).'),
     'mots': ('services agreement', 'service agreement', 'consultancy agreement'),
     'variables': [('supplier', 'Supplier', ''), ('customer', 'Customer', ''), ('services', 'Services', ''), ('charges', 'Charges', ''),
                   ('payment_terms', 'Payment terms', '30 days from invoice'), ('cap', 'Liability cap', 'the charges paid in the preceding 12 months'), ('date', 'Date', '')],
     'clauses': [('ip_assignment', 'Assignment of IP in deliverables', True, ''), ('data_processing', 'Data processing (UK GDPR art. 28)', True, '')],
     'fixes': {}, 'texte': [
         '# Services Agreement', '', 'This agreement is dated {{date}} between {{supplier}} (the "Supplier") and {{customer}} (the "Customer").', '',
         '## Services', 'The Supplier shall provide the following services with reasonable skill and care: {{services}}.',
         '## Charges', 'The Customer shall pay {{charges}} plus VAT within {{payment_terms}}. Interest on late payment accrues under the Late Payment of '
         'Commercial Debts (Interest) Act 1998.',
         '[[si ip_assignment]]', '## Intellectual property', 'The Supplier assigns to the Customer, with full title guarantee, all intellectual property '
         'rights in the deliverables upon payment.', '[[fin]]',
         '[[si data_processing]]', '## Data protection', 'Where the Supplier processes personal data on behalf of the Customer, it shall do so in '
         'accordance with Article 28 of the UK GDPR and the Data Protection Act 2018.', '[[fin]]',
         '## Limitation of liability', 'Nothing limits liability for death or personal injury caused by negligence, fraud or any liability that cannot '
         'be limited by law. Subject to that, each party’s total liability shall not exceed {{cap}}, and neither party is liable for indirect or '
         'consequential loss. These limitations are subject to the reasonableness test of the Unfair Contract Terms Act 1977.',
         '## Term and termination', 'Either party may terminate on three months’ written notice, or immediately for material breach not remedied '
         'within 30 days.',
         '## Governing law', 'Law of England and Wales; exclusive jurisdiction of the English courts.']},
    {'id': 'en_nda', 'langue': 'en', 'droit': 'EN', 'categorie': 'contrat', 'formes': (), 'titre': 'Non-Disclosure Agreement',
     'resume': _R('Accord de confidentialité de droit anglais.', 'Mutual non-disclosure agreement (English law).', 'Geheimhaltungsvereinbarung (englisches Recht).',
                  'Acuerdo de confidencialidad (derecho inglés).'),
     'mots': ('non-disclosure agreement', 'nda', 'confidentiality agreement'),
     'variables': [('party_a', 'Party A', ''), ('party_b', 'Party B', ''), ('purpose', 'Purpose', ''), ('term', 'Term', 'five years'), ('date', 'Date', '')],
     'clauses': [('mutual', 'Mutual', True, '')], 'fixes': {}, 'texte': [
         '# Non-Disclosure Agreement', '', 'This agreement is dated {{date}} between {{party_a}} and {{party_b}}.', '',
         '## Purpose', '[[si mutual]]Each party[[sinon]]Party A[[fin]] will disclose confidential information for the purpose of {{purpose}}.',
         '## Obligations', 'The recipient shall use confidential information only for the purpose, keep it secure and disclose it only to its '
         'representatives who need to know it and are bound by equivalent obligations.',
         '## Exceptions', 'Information which is or becomes public other than through breach, was already known, is received from a third party free to '
         'disclose it, or is independently developed.',
         '## Term', 'The obligations last for {{term}} from the date of this agreement.', '## Remedies', 'Damages alone may not be an adequate remedy; '
         'the discloser may seek injunctive relief.', '## Governing law', 'Law of England and Wales.']},
    {'id': 'en_terms_sale', 'langue': 'en', 'droit': 'EN', 'categorie': 'contrat', 'formes': (), 'titre': 'Terms and Conditions of Sale (B2B)',
     'resume': _R('Conditions générales de vente B2B de droit anglais.', 'B2B terms and conditions of sale (English law).',
                  'Verkaufsbedingungen B2B (englisches Recht).', 'Condiciones generales de venta (derecho inglés).'),
     'mots': ('terms and conditions', 'terms of sale', 't&cs'),
     'variables': [('seller', 'Seller', ''), ('payment_terms', 'Payment terms', '30 days from invoice')],
     'clauses': [('retention', 'Retention of title', True, '')], 'fixes': {}, 'texte': [
         '# Terms and Conditions of Sale of {{seller}}', '',
         '## Application', 'These terms apply to all contracts for the sale of goods by the Seller to business customers to the exclusion of any other '
         'terms, including those the Customer seeks to impose.',
         '## Price and payment', 'Payment is due within {{payment_terms}}. Statutory interest applies under the Late Payment of Commercial Debts '
         '(Interest) Act 1998.',
         '## Delivery and risk', 'Delivery dates are approximate. Risk passes on delivery.',
         '[[si retention]]', '## Title', 'Title to the goods shall not pass until the Seller has received payment in full.', '[[fin]]',
         '## Liability', 'The Seller’s liability is limited to the price of the goods, save for liability which cannot be limited by law; these '
         'limitations are subject to the Unfair Contract Terms Act 1977.', '## Governing law', 'Law of England and Wales.']},
    {'id': 'en_shareholder_loan', 'langue': 'en', 'droit': 'EN', 'categorie': 'contrat', 'formes': ('Ltd',), 'titre': 'Shareholder Loan Agreement',
     'resume': _R('Prêt d’actionnaire de droit anglais.', 'Shareholder loan agreement (English law).', 'Gesellschafterdarlehen (englisches Recht).',
                  'Préstamo de socio (derecho inglés).'),
     'mots': ('shareholder loan', 'loan agreement'),
     'variables': [('lender', 'Lender', ''), ('borrower', 'Borrower (company)', ''), ('amount', 'Amount', ''), ('interest', 'Interest rate', ''),
                   ('repayment', 'Repayment date', ''), ('date', 'Date', '')],
     'clauses': [('subordination', 'Subordination to bank debt', False, '')], 'fixes': {}, 'texte': [
         '# Shareholder Loan Agreement', '', 'Dated {{date}} between {{lender}} (the "Lender") and {{borrower}} (the "Borrower").', '',
         '## Loan', 'The Lender makes available to the Borrower a loan of {{amount}}.', '## Interest', 'Interest accrues at {{interest}} per annum. '
         '[TO BE CHECKED: transfer pricing and withholding tax]', '## Repayment', 'The Borrower shall repay the loan on {{repayment}} and may prepay '
         'without penalty.',
         '[[si subordination]]', '## Subordination', 'The loan is subordinated to the Borrower’s senior debt on the terms of an intercreditor deed.', '[[fin]]',
         '## Governing law', 'Law of England and Wales.']},
]
