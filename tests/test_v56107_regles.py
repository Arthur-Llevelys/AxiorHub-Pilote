"""5.6.107 (lot X) : alerte de fraude et messages de juridiction reconnus en anglais, allemand et espagnol.

La démo existe dans ces trois langues ; les règles de contenu ne connaissaient que le français. Ce module vérifie :

- X1 : expressions de changement de coordonnées bancaires, de pression ou de secret et de demande de virement en anglais, allemand et
  espagnol, avec les mêmes poids que le français ; corpus fictif par langue (au moins 4 vrais positifs et 3 faux positifs à éviter, taux
  mesurés) ; le français garde exactement ses verdicts (corpus du lot F rejoué sans les règles étrangères : mêmes résultats).
- X2 : plateformes et formules des messages de juridiction (CE-File, beA, LexNET…), dates en toutes lettres et chiffrées de chaque langue,
  heure, nature, juridiction, chambre ; en cas de doute la date reste « à valider » ; le français garde exactement ses résultats.
- X3 : les courriels réels de la démo de chaque langue : le frauduleux détecté par les signaux de contenu (pas seulement par le nom usurpé),
  le message de juridiction proposé dans l'agenda par la routine sur message, puis, une fois validé, montré dans Aujourd'hui (« Vérifiée »).

Données fictives (DURAND, MARTIN, ZORVAL, ORLANDEC, Valbrune, domaines example.test) ; aucun réseau, aucun modèle réel.
Exécuter avec PYTHONPATH=<racine AxiorHub>:<racine AxiorHub>/tests.
"""
from datetime import date, datetime, timedelta, timezone
from email import policy
from email.message import EmailMessage
from email.parser import BytesParser
import json
import os
from pathlib import Path
import re
import shutil
import tempfile
import unittest
from unittest.mock import patch

import test_v56106_fraude as F
from agent import fraude56106 as fr, routine_message56106 as rm
from agent.mailbox import Mail

JEUDI = date(2026, 11, 19)          # « aujourd'hui » des essais de repérage (jour du cabinet fixé)
JAMAIS = re.compile(r'(?!x)x')      # motif qui ne trouve jamais rien (règles étrangères retirées, en mémoire seulement)


def courriel_simple(objet, corps, de='Valbrune County Court <civil@valbrune-county-court.example.test>'):
    m = EmailMessage()
    m['From'], m['To'], m['Subject'], m['Message-ID'] = de, 'cabinet@example.test', objet, '<x-%d@example.test>' % abs(hash((objet, corps)))
    m.set_content(corps)
    return Mail('1', '7', 'INBOX', set(), datetime.now(timezone.utc), BytesParser(policy=policy.default).parsebytes(m.as_bytes()))


# ================================================================================================ X1 : corpus de fraude par langue
LT = F.IBAN_ETRANGER


def corpus_etranger():
    """{langue: [(nom, message, niveau minimal attendu ou '')]} ; mêmes contacts que le corpus du lot F (DURAND confrère, MARTIN client)."""
    c, OK, dmarc_ko = F.courriel, F.OK, F.ar('spf=fail smtp.mailfrom=orlandec.example.test', 'dkim=none', 'dmarc=fail header.from=orlandec.example.test')
    lettre = [OK, ('List-Unsubscribe', '<https://editions-bernard.example.test/stop>'), ('Return-Path', '<rebond@envoi-tiers.example.test>')]
    html_lettre = '<p><a href="https://clic.envoi-tiers.example.test/r?x=1">www.editions-bernard.example.test</a></p>'
    return {
        'en': [
            ('EP1 new bank details, foreign IBAN, pressure and secrecy (unknown sender)', c(
                'Accounts Department <accounts@supplier-payments.example.test>', 'Updated bank details',
                'Please note our new bank details: IBAN %s. Please transfer the outstanding amount today without fail and keep this confidential.' % LT),
             'danger'),
            ('EP2 client impersonated (DMARC fail), change of bank', c(
                'Sophie MARTIN <%s>' % F.MARTIN, 'Change of bank details',
                'Dear Ms DURAND, following a change of bank, our bank account has changed: please use IBAN %s.' % F.IBAN_FR_AUTRE,
                entetes=[dmarc_ko]), 'suspect'),
            ('EP3 CEO fraud, urgent wire transfer, keep it between us', c(
                'Claire ZORVAL <dirigeante.cabinet@exemple-mail.test>', 'Payment',
                'I am in a meeting. I need you to make an urgent wire transfer this morning. Please keep this between us and do not copy anyone.'),
             'suspect'),
            ('EP4 authenticated client, updated account, IBAN not the confirmed one', c(
                'Sophie MARTIN <%s>' % F.MARTIN, 'Invoice', 'Hello, please use our updated account details: IBAN %s.' % F.IBAN_FR_AUTRE,
                entetes=[OK]), 'suspect'),
            ('EP5 old account closed, wire the balance, secrecy (content only)', c(
                'Finance <finance@new-vendor.example.test>', 'Confidential',
                'Our old account has been closed. Please wire transfer the balance as soon as possible. Do not discuss this with anyone.'),
             'suspect'),
            ('EP6 mailbox full, confirm your account, link to an unknown site', c(
                'IT Support <support@mail-quota.example.test>', 'Your mailbox is full', 'Your mailbox is full. Confirm your account to keep receiving mail.',
                html='<p>Your mailbox is full.</p><p><a href="https://quota-reset.example.test/login">Confirm your account</a></p>'), 'attention'),
            ('EN1 legitimate newsletter (urgent: register)', c(
                'The Business Law Letter <lettre@editions-bernard.example.test>', 'Commercial leases — November',
                'In this issue: commercial leases. Urgent: register now for our webinar.', html=html_lettre, entetes=lettre), ''),
            ('EN2 known client, usual IBAN, confidentiality footer', c(
                'Sophie MARTIN <%s>' % F.MARTIN, 'Invoice ORLANDEC',
                'Dear Ms DURAND, please pay the invoice to our usual account %s.\n\nThis email is confidential and may be legally privileged.'
                % F.IBAN_FR_CONNU, entetes=[OK]), ''),
            ('EN3 first contact, appointment request, confidentiality footer', c(
                'John BERNARD <j.bernard@private-mail.example.test>', 'Appointment request',
                'Dear Ms DURAND, I would like an appointment about a dispute with my landlord.\n\nThis message is confidential.'), ''),
            ('EN4 known colleague asks for the client account transfer', c(
                'Paul DURAND <%s>' % F.DURAND, 'Settlement funds', 'Urgent: please transfer the settlement funds to the client account today.',
                entetes=[OK]), ''),
        ],
        'de': [
            ('DP1 neue Bankverbindung, ausländische IBAN, Druck und Vertraulichkeit', c(
                'Buchhaltung <buchhaltung@lieferant-zahlungen.example.test>', 'Neue Bankverbindung',
                'Bitte beachten Sie unsere neue Bankverbindung: IBAN %s. Bitte überweisen Sie den offenen Betrag noch heute und behandeln Sie dies '
                'streng vertraulich.' % LT), 'danger'),
            ('DP2 Mandantin imitiert (DMARC), Kontoänderung', c(
                'Sophie MARTIN <%s>' % F.MARTIN, 'Kontoänderung',
                'Sehr geehrte Damen und Herren, aufgrund eines Bankwechsels hat sich unsere Bankverbindung geändert: IBAN %s.' % F.IBAN_FR_AUTRE,
                entetes=[dmarc_ko]), 'suspect'),
            ('DP3 Chef-Masche, dringende Überweisung, mit niemandem sprechen', c(
                'Claire ZORVAL <dirigeante.cabinet@exemple-mail.test>', 'Überweisung',
                'Ich bin in einer Besprechung. Bitte veranlassen Sie heute noch eine dringende Überweisung. Sprechen Sie mit niemandem darüber '
                'und setzen Sie niemanden in Kopie.'), 'suspect'),
            ('DP4 authentifizierte Mandantin, geänderte Bankverbindung, andere IBAN', c(
                'Sophie MARTIN <%s>' % F.MARTIN, 'Rechnung', 'Guten Tag, bitte verwenden Sie unsere geänderte Bankverbindung: IBAN %s.' % F.IBAN_FR_AUTRE,
                entetes=[OK]), 'suspect'),
            ('DP5 altes Konto aufgelöst, Überweisung, absolute Vertraulichkeit (nur Inhalt)', c(
                'Finanzabteilung <finanzen@neuer-lieferant.example.test>', 'Dringend',
                'Unser altes Konto wurde aufgelöst. Bitte veranlassen Sie umgehend die Überweisung des Restbetrags. Absolute Vertraulichkeit.'),
             'suspect'),
            ('DP6 Postfach voll, Konto bestätigen, Link zu unbekannter Seite', c(
                'IT-Service <service@postfach-speicher.example.test>', 'Ihr Postfach ist voll',
                'Ihr Postfach ist voll. Bestätigen Sie Ihr Konto, um weiter E-Mails zu erhalten.',
                html='<p>Ihr Postfach ist voll.</p><p><a href="https://speicher-zuruecksetzen.example.test/anmelden">Konto bestätigen</a></p>'), 'attention'),
            ('DN1 seriöser Newsletter (dringend: anmelden)', c(
                'Wirtschaftsrecht aktuell <lettre@editions-bernard.example.test>', 'Gewerbemiete — November',
                'In dieser Ausgabe: Gewerbemiete. Dringend: jetzt zum Webinar anmelden.', html=html_lettre, entetes=lettre), ''),
            ('DN2 bekannte Mandantin, bekanntes Konto, Vertraulichkeitshinweis', c(
                'Sophie MARTIN <%s>' % F.MARTIN, 'Rechnung ORLANDEC',
                'Sehr geehrte Frau Durand, bitte überweisen Sie den Rechnungsbetrag auf unser bekanntes Konto %s.\n\nDiese E-Mail ist vertraulich.'
                % F.IBAN_FR_CONNU, entetes=[OK]), ''),
            ('DN3 Erstkontakt, Terminanfrage, Vertraulichkeitshinweis', c(
                'Jan BERNARD <j.bernard@privat-post.example.test>', 'Terminanfrage',
                'Sehr geehrte Frau Durand, ich bitte um einen Termin wegen eines Streits mit meinem Vermieter.\n\nDiese Nachricht ist vertraulich.'), ''),
            ('DN4 bekannter Kollege, Vergleichsbetrag auf das Anderkonto', c(
                'Paul DURAND <%s>' % F.DURAND, 'Vergleichsbetrag', 'Dringend: bitte überweisen Sie den Vergleichsbetrag heute noch auf das Anderkonto.',
                entetes=[OK]), ''),
        ],
        'es': [
            ('SP1 nuevos datos bancarios, IBAN extranjero, urgencia y discreción', c(
                'Contabilidad <contabilidad@proveedor-pagos.example.test>', 'Nuevos datos bancarios',
                'Le informamos de nuestros nuevos datos bancarios: IBAN %s. Le rogamos que transfiera hoy mismo el importe pendiente, con total '
                'discreción.' % LT), 'danger'),
            ('SP2 clienta suplantada (DMARC), cambio de banco', c(
                'Sophie MARTIN <%s>' % F.MARTIN, 'Cambio de cuenta',
                'Estimados señores: debido a un cambio de banco, nuestros datos bancarios han cambiado. Nuevo IBAN: %s.' % F.IBAN_FR_AUTRE,
                entetes=[dmarc_ko]), 'suspect'),
            ('SP3 fraude del CEO, transferencia urgente, no lo comente con nadie', c(
                'Claire ZORVAL <dirigeante.cabinet@exemple-mail.test>', 'Transferencia',
                'Estoy en una reunión. Necesito que haga una transferencia urgente esta mañana. No lo comente con nadie y no ponga a nadie en copia.'),
             'suspect'),
            ('SP4 clienta autenticada, actualización de datos bancarios, otro IBAN', c(
                'Sophie MARTIN <%s>' % F.MARTIN, 'Factura',
                'Buenos días, le rogamos que tome nota de la actualización de nuestros datos bancarios: IBAN %s.' % F.IBAN_FR_AUTRE, entetes=[OK]),
             'suspect'),
            ('SP5 cuenta anterior cancelada, transferencia, operación confidencial (solo contenido)', c(
                'Tesorería <tesoreria@nuevo-proveedor.example.test>', 'Urgente',
                'Nuestra cuenta anterior ha sido cancelada. Le rogamos que realice cuanto antes la transferencia del saldo. Operación confidencial.'),
             'suspect'),
            ('SP6 buzón lleno, verifique su cuenta, enlace a un sitio desconocido', c(
                'Soporte <soporte@buzon-cuota.example.test>', 'Su buzón está lleno', 'Su buzón está lleno. Verifique su cuenta para seguir recibiendo correo.',
                html='<p>Su buzón está lleno.</p><p><a href="https://restablecer-cuota.example.test/acceso">Verificar mi cuenta</a></p>'), 'attention'),
            ('SN1 boletín legítimo (urgente: inscríbase)', c(
                'Boletín Mercantil <lettre@editions-bernard.example.test>', 'Arrendamientos — noviembre',
                'En este número: arrendamientos. Urgente: inscríbase ya al seminario.', html=html_lettre, entetes=lettre), ''),
            ('SN2 clienta conocida, cuenta habitual, aviso de confidencialidad', c(
                'Sophie MARTIN <%s>' % F.MARTIN, 'Factura ORLANDEC',
                'Estimada Sra. Durand: le rogamos que abone la factura en nuestra cuenta habitual %s.\n\nEste mensaje es confidencial.'
                % F.IBAN_FR_CONNU, entetes=[OK]), ''),
            ('SN3 primer contacto, solicitud de cita, aviso de confidencialidad', c(
                'Juan BERNARD <j.bernard@correo-privado.example.test>', 'Solicitud de cita',
                'Estimada Sra. Durand: quisiera una cita por un litigio con mi casero.\n\nEste mensaje es confidencial.'), ''),
            ('SN4 compañero conocido, importe del acuerdo a la cuenta de consignación', c(
                'Paul DURAND <%s>' % F.DURAND, 'Acuerdo', 'Urgente: le rogamos que transfiera hoy mismo el importe del acuerdo a la cuenta de consignación.',
                entetes=[OK]), ''),
        ],
    }


def _sans_regles_etrangeres():
    """Règles anglaises, allemandes et espagnoles retirées (en mémoire) : la référence du français tel qu'il était."""
    return [patch.object(fr, nom, JAMAIS) for nom in ('R_RIB_ETR', 'R_URGENCE_ETR', 'R_SECRET_ETR', 'R_VIREMENT_ETR', 'R_CONNEXION_ETR')]


class Fraude(F.Base):
    def taux(self, cas):
        positifs = [x for x in cas if x[2]]
        negatifs = [x for x in cas if not x[2]]
        detectes, au_niveau, faux, lignes = [], [], [], []
        for nom, msg, attendu in cas:
            v = self.verdict(msg)
            lignes.append('%-70s attendu=%-9s obtenu=%-9s score=%2d %s' % (nom[:70], attendu or '-', v['niveau'] or '-', v['score'],
                                                                           ','.join(s['id'] for s in v['signaux'])))
            if attendu:
                detectes += [nom] if v['niveau'] else []
                au_niveau += [nom] if F.au_moins(v['niveau'], attendu) else []
            elif v['niveau']:
                faux.append(nom)
        return positifs, negatifs, detectes, au_niveau, faux, lignes

    def test_X1_01_corpus_par_langue_detection_et_faux_positifs(self):
        bilan = []
        for langue, cas in corpus_etranger().items():
            with self.subTest(langue=langue):
                positifs, negatifs, detectes, au_niveau, faux, lignes = self.taux(cas)
                self.assertGreaterEqual(len(positifs), 4)
                self.assertGreaterEqual(len(negatifs), 3)
                bilan += [''] + lignes + ['%s — détection : %d/%d (%.0f %%) ; au niveau attendu ou plus : %d/%d (%.0f %%) ; faux positifs : %d/%d (%.0f %%)' % (
                    langue, len(detectes), len(positifs), 100 * len(detectes) / len(positifs), len(au_niveau), len(positifs),
                    100 * len(au_niveau) / len(positifs), len(faux), len(negatifs), 100 * len(faux) / len(negatifs))]
                self.assertEqual(faux, [], 'faux positifs')
                self.assertEqual(len(detectes), len(positifs), [l for l in lignes if 'obtenu=-' in l and 'attendu=-' not in l])
                self.assertEqual(len(au_niveau), len(positifs), lignes)
        print('\n'.join(bilan))

    def test_X1_02_signaux_de_contenu_dans_chaque_langue_memes_poids(self):
        attendus = {'changement_rib': (3, 'fort'), 'demande_virement': (2, 'moyen'), 'pression': (3, 'fort')}
        for langue, n in (('en', 0), ('de', 0), ('es', 0)):
            with self.subTest(langue=langue):
                v = self.verdict(corpus_etranger()[langue][n][1])
                sig = {s['id']: (s['poids'], s['force']) for s in v['signaux']}
                for ident, poids in attendus.items():
                    self.assertEqual(sig.get(ident), poids, (ident, v['signaux']))
        # la même phrase française donne les mêmes poids (référence)
        v = self.verdict(F.courriel('Comptabilité <compta@fournisseur-paiements.example.test>', 'Nouveau RIB',
                                    'Voici notre nouveau RIB %s. Merci de procéder au virement aujourd’hui même, en toute discrétion.' % LT))
        sig = {s['id']: (s['poids'], s['force']) for s in v['signaux']}
        for ident, poids in attendus.items():
            self.assertEqual(sig.get(ident), poids)

    def test_X1_03_mention_de_confidentialite_seule_jamais_un_signal(self):
        for texte in ('This email is confidential and may be privileged.', 'Diese E-Mail ist vertraulich.', 'Este mensaje es confidencial.',
                      'Strictly private and confidential: privileged communication.', 'Vertrauliche Informationen nur für den Empfänger.'):
            with self.subTest(texte=texte):
                self.assertIsNone(fr.R_SECRET_ETR.search(fr.fold(texte)))
                self.assertIsNone(fr.R_SECRET.search(fr.fold(texte)))

    def test_X1_04_mutation_sans_regles_etrangeres_le_contenu_n_est_plus_vu(self):
        msg = corpus_etranger()['en'][4][1]                  # EP5 : contenu seulement, aucun nom usurpé
        self.assertIn(self.verdict(msg)['niveau'], ('suspect', 'danger'))
        gardes = _sans_regles_etrangeres()
        for g in gardes:
            g.start()
        try:
            v = self.verdict(msg)
        finally:
            for g in gardes:
                g.stop()
        self.assertEqual(v['niveau'], '', v['signaux'])      # sans les règles du lot X, ce courriel passait inaperçu

    def test_X1_05_francais_exactement_inchange(self):
        """Corpus du lot F (et ses variantes) : verdicts identiques avec et sans les règles étrangères."""
        francais = [m for _n, m, _a in F.corpus()] + [
            F.courriel('Me X <x@exemple-mail.test>', 'Urgent', 'Merci de me rappeler ASAP, c’est urgent.'),
            F.courriel('Y <y@inconnu.example.test>', 'Info', 'Le délai est court ; la conférence du 14 janvier 2027 est confidentielle.'),
            F.courriel('Z <z@inconnu.example.test>', 'Banque', 'Notre banque a changé de nom ; merci de régler la facture par virement.'),
        ]
        avec = [json.dumps(self.verdict(m), sort_keys=True, ensure_ascii=False) for m in francais]
        gardes = _sans_regles_etrangeres()
        for g in gardes:
            g.start()
        try:
            sans = [json.dumps(self.verdict(m), sort_keys=True, ensure_ascii=False) for m in francais]
        finally:
            for g in gardes:
                g.stop()
        self.assertEqual(avec, sans)


# ================================================================================================ X2 : messages de juridiction et dates
class Juridiction(unittest.TestCase):
    def ext(self, texte, objet='Avis'):
        return rm.extraire_evenement(objet, texte, JEUDI)

    def quand(self, x):
        return (x['date'], x['heure'], x['certitude'])

    def test_X2_01_dates_anglaises(self):
        for texte, attendu in (
                ('The hearing is listed for 14 January 2027 at 10:00.', ('2027-01-14', '10:00', 'sure')),
                ('The court has listed the trial on Thursday, 14 January 2027 at 10.00 am.', ('2027-01-14', '10:00', 'sure')),
                ('Hearing listed for January 14, 2027 at 2 pm.', ('2027-01-14', '14:00', 'sure')),
                ('Hearing on the 14th of January 2027 at 12 pm.', ('2027-01-14', '12:00', 'sure')),
                ('Listed for a case management conference on 14/01/2027 at 9.30 am.', ('2027-01-14', '09:30', 'sure')),
                ('Hearing listed for 3 Mar. 2027 from 9:45.', ('2027-03-03', '09:45', 'sure'))):
            with self.subTest(texte=texte):
                self.assertEqual(self.quand(self.ext(texte)), attendu)

    def test_X2_02_dates_allemandes(self):
        for texte, attendu in (
                ('Termin zur mündlichen Verhandlung am 14. Januar 2027 um 10:00 Uhr.', ('2027-01-14', '10:00', 'sure')),
                ('Verkündungstermin: Donnerstag, den 14. Januar 2027, 10 Uhr, Saal 214.', ('2027-01-14', '10:00', 'sure')),
                ('Termin wird bestimmt auf den 14.01.2027, 9:30 Uhr.', ('2027-01-14', '09:30', 'sure')),
                ('Termin am 3. März 2027 um 9.30 Uhr.', ('2027-03-03', '09:30', 'sure'))):
            with self.subTest(texte=texte):
                self.assertEqual(self.quand(self.ext(texte)), attendu)

    def test_X2_03_dates_espagnolas(self):
        for texte, attendu in (
                ('Se señala la vista el 14 de enero de 2027 a las 10:00.', ('2027-01-14', '10:00', 'sure')),
                ('Se cita a las partes el jueves, 14 de enero de 2027 a las 9:30 horas.', ('2027-01-14', '09:30', 'sure')),
                ('Señalamiento: el día 14/01/2027 a las 10.00 h.', ('2027-01-14', '10:00', 'sure')),
                ('Audiencia previa el 3 de marzo del 2027 a las 12:15.', ('2027-03-03', '12:15', 'sure'))):
            with self.subTest(texte=texte):
                self.assertEqual(self.quand(self.ext(texte)), attendu)

    def test_X2_04_en_cas_de_doute_a_valider(self):
        cas = (
            ('The hearing is listed for 14 January 2027.', 'heure absente'),                                       # heure absente
            ('Termin zur mündlichen Verhandlung am 14. Januar 2027 um 10:00 Uhr. Verkündungstermin am 21. Januar 2027 um 11:00 Uhr.', 'plusieurs dates'),
            ('Case management conference: 10 December 2026 at 9.30 am.\nTrial: 17 December 2026 at 2 pm.', 'plusieurs dates'),
            ('Audiencia previa: 10 de diciembre de 2026 a las 9:30.\nJuicio: 17 de diciembre de 2026 a las 14:00.', 'plusieurs dates'),
            ('Vista celebrada el 2 de noviembre de 2026 a las 10:00.', 'date passée seulement'),                    # date passée
            ('Hearing listed for Monday, 14 January 2027 at 10:00.', 'aucune date'),                                # jour contraire (jeudi)
            ('Termin am Montag, 14. Januar 2027 um 10 Uhr.', 'aucune date'),
            ('Vista el lunes 14 de enero de 2027 a las 10:00.', 'aucune date'),
        )
        for texte, motif in cas:
            with self.subTest(texte=texte):
                x = self.ext(texte)
                self.assertEqual(x['certitude'], 'incertaine', x)
                self.assertTrue(any(motif in m for m in x['motifs']), x['motifs'])
        # un champ « Time: » / « Uhrzeit: » / « Hora: » complète une date sans heure
        for texte in ('Hearing date: 14 January 2027.\nTime: 10:00', 'Termin: 14. Januar 2027.\nUhrzeit: 10:00 Uhr', 'Vista: 14 de enero de 2027.\nHora: 10:00'):
            with self.subTest(texte=texte):
                self.assertEqual(self.quand(self.ext(texte)), ('2027-01-14', '10:00', 'sure'))
        # date d'envoi (référence) écartée au profit de la date d'audience
        x = self.ext('Notice dated 18 November 2026, issued on 18/11/2026. The hearing is listed for 14 January 2027 at 10:00.')
        self.assertEqual(self.quand(x), ('2027-01-14', '10:00', 'sure'))
        x = self.ext('Zugestellt am 20.11.2026. Termin zur mündlichen Verhandlung am 14. Januar 2027 um 10:00 Uhr.')
        self.assertEqual(self.quand(x), ('2027-01-14', '10:00', 'sure'))

    def test_X2_05_natures(self):
        for texte, nature in (
                ('The hearing has been adjourned to 14 January 2027 at 10:00.', 'renvoi'),
                ('Judgment will be handed down on 14 January 2027 at 10:00.', 'delibere'),
                ('Case management conference on 14 January 2027 at 10:00.', 'mise_en_etat'),
                ('Trial listed for 14 January 2027 at 10:00.', 'plaidoiries'),
                ('Hearing listed for 14 January 2027 at 10:00.', 'audience'),
                ('Der Termin wird verlegt auf den 14. Januar 2027, 10 Uhr.', 'renvoi'),
                ('Verkündungstermin am 14. Januar 2027 um 10 Uhr.', 'delibere'),
                ('Termin zur mündlichen Verhandlung am 14. Januar 2027 um 10 Uhr.', 'audience'),
                ('Se suspende la vista y se fija nuevo señalamiento el 14 de enero de 2027 a las 10:00.', 'renvoi'),
                ('Audiencia previa el 14 de enero de 2027 a las 10:00.', 'mise_en_etat'),
                ('Juicio oral el 14 de enero de 2027 a las 10:00.', 'plaidoiries'),
                ('Se cita a las partes a la vista el 14 de enero de 2027 a las 10:00.', 'audience')):
            with self.subTest(texte=texte):
                self.assertEqual(self.ext(texte)['nature'], nature)

    def test_X2_06_juridiction_et_chambre(self):
        for texte, juridiction, chambre in (
                ('Valbrune County Court — Multi-track list\nHearing listed for 14 January 2027 at 10:00.', 'Valbrune County Court', ''),
                ('Landgericht Valbrune — 2. Zivilkammer\nTermin am 14. Januar 2027 um 10 Uhr.', 'Landgericht Valbrune', '2. Zivilkammer'),
                ('Juzgado de Primera Instancia n.º 3 de Valbrune\nSala de lo Civil\nVista el 14 de enero de 2027 a las 10:00.',
                 'Juzgado de Primera Instancia n.º 3 de Valbrune', 'Sala de lo Civil')):
            with self.subTest(texte=texte):
                x = self.ext(texte)
                self.assertEqual((x['juridiction'], x['chambre']), (juridiction, chambre))
        self.assertEqual(rm.juridiction_du_texte('KERVILAC Enforcement\nHigh Court Enforcement Officers'), 'High Court')   # jamais d'un mot de la ligne d'avant

    def test_X2_07_message_de_juridiction_et_plateforme(self):
        for objet, corps, plateforme in (
                ('[CE-File] Notice of hearing', 'Claim No. VB24C01234.', 'CE-File'),
                ('[beA] Terminsladung', 'Az. 2 O 1234/24.', 'beA'),
                ('[LexNET] Notificación de señalamiento', 'Procedimiento 1234/2024.', 'LexNET'),
                ('Hearing', 'The court has listed the claim for a hearing.', 'RPVA'),
                ('Ladung', 'Termin zur mündlichen Verhandlung wird bestimmt.', 'RPVA'),
                ('Citación', 'Se cita a las partes para la vista.', 'RPVA')):
            with self.subTest(objet=objet):
                mail = courriel_simple(objet, corps, de='Greffe <notifications@courts.example.test>')
                self.assertTrue(rm.message_de_juridiction(mail))
                self.assertEqual(rm.plateforme_de(objet + ' ' + corps), plateforme)
        for objet, corps in (('Thank you', 'Thank you for the note, see you next week.'), ('Vielen Dank', 'Danke für den Vermerk.'),
                             ('Gracias', 'Gracias por la nota.'), ('Court', 'Le délai est court.')):
            with self.subTest(objet=objet):
                self.assertFalse(rm.message_de_juridiction(courriel_simple(objet, corps, de='Client <client@example.test>')))

    def test_X2_08_francais_exactement_inchange(self):
        """Textes français (corpus du lot O et pièges) : mêmes résultats avec et sans les règles étrangères (retirées en mémoire)."""
        import test_v56106_routine as O
        textes = [O.CORPS_SUR, O.CORPS_SANS_HEURE, O.CORPS_DEUX_DATES, O.CORPS_INCONNU,
                  "Audience du 02/11/2026 tenue. Prochaine audience le 05 février 2027.\nHeure : 14h00", 'Audience du 02/11/2026 à 9h.',
                  'N° RG 24/01234 sans date.', "L'affaire est appelée à l'audience de mise en état du 26/11/2026 à 9 h 30.",
                  'Audience le jeudi 14 mai 2027 à 10 h. Notifié le mercredi 13 mai 2027.', 'Plaidoiries le 3 mars 2027 à 9.30 ; délibéré au 7 avril 2027.',
                  'Conférence du 14/01/2027 ; renvoi au 21/01/2027 à 10h, suspendu.', 'Audience du lundi 14 janvier 2027 à 10 h.',
                  'Le délai est court : audience le 14 janvier 2027 à 10 am.', 'Tribunal correctionnel de Valbrune — audience le 14/01/2027 à 14h00.']
        objets = ['RPVA - Avis de renvoi - RG 24/01234', 'Avis de fixation', 'Calendrier']
        avec = [(rm.extraire_evenement(o, t, JEUDI), rm.message_de_juridiction(courriel_simple(o, t, de='Greffe <g@tj.example.test>')))
                for t in textes for o in objets]
        retraits = [patch.object(rm, 'DATES_ETR', ()), patch.object(rm, 'HEURES_ETR', ()), patch.object(rm, 'NATURE_MOTS_ETR', ()),
                    patch.object(rm, 'JURIDICTION_ETR', ()), patch.object(rm, 'HEURE_CHAMP_ETR', JAMAIS)]
        retraits += [patch.object(rm, n, JAMAIS) for n in ('CIBLE_ETR', 'RENVOI_CIBLE_ETR', 'ANCIEN_ETR', 'JURIDICTION_MOTS_ETR', 'CHAMBRE_ETR')]
        for r in retraits:
            r.start()
        try:
            sans = [(rm.extraire_evenement(o, t, JEUDI), rm.message_de_juridiction(courriel_simple(o, t, de='Greffe <g@tj.example.test>')))
                    for t in textes for o in objets]
        finally:
            for r in retraits:
                r.stop()
        self.assertEqual(avec, sans)

    def test_X2_10_tri_du_matin_reconnait_les_messages_de_juridiction(self):
        from agent import routines520
        for objet, corps in (('[CE-File] Notice of hearing — Claim No. VB24C01234', 'The claim is listed for a hearing on 14/01/2027 at 10:00.'),
                             ('[beA] Terminsladung — Az. 2 O 1234/24', 'Termin zur mündlichen Verhandlung am 14.01.2027, 10:00 Uhr.'),
                             ('[LexNET] Notificación de señalamiento', 'Se cita a las partes a la vista el 14/01/2027 a las 10:00.')):
            with self.subTest(objet=objet):
                mail = courriel_simple(objet, corps, de='Notifications <notifications@courts.example.test>')
                sig = routines520._signals(mail, mail.text)
                self.assertIn('greffe', sig)
                self.assertIn('procedure', sig)
                self.assertEqual(routines520.categorize({'signals': sig, 'answered': False, 'excerpt': corps, 'subject': objet}), 'urgent')
        # français : mêmes signaux avec et sans les mots étrangers (retirés en mémoire)
        francais = [courriel_simple('RPVA - Avis de renvoi', 'Le délai est court ; audience renvoyée.', de='Greffe <g@tj.example.test>'),
                    courriel_simple('Rendez-vous', 'La deadline est courte, merci de me rappeler.', de='Client <c@example.test>'),
                    courriel_simple('Conférence', 'Vista sur la conférence de terminale.', de='Client <c@example.test>')]
        avec = [routines520._signals(m, m.text) for m in francais]
        with patch.object(routines520, 'PROCEDURE_ETR', JAMAIS), patch.object(rm, 'JURIDICTION_MOTS_ETR', JAMAIS):
            self.assertEqual([routines520._signals(m, m.text) for m in francais], avec)

    def test_X2_09_mutation_sans_regles_etrangeres_la_date_n_est_plus_lue(self):
        texte = 'Termin zur mündlichen Verhandlung am 14. Januar 2027 um 10:00 Uhr.'
        self.assertEqual(self.quand(self.ext(texte)), ('2027-01-14', '10:00', 'sure'))
        with patch.object(rm, 'DATES_ETR', ()):
            self.assertEqual(self.ext(texte)['date'], '')        # garde-fou : sans les dates allemandes, rien n'est repéré


# ================================================================================================ X3 : courriels réels de la démo
LANGUES = ('en', 'de', 'es')


class Demo(unittest.TestCase):
    """Une instance de démo par langue (création ≈ 3 s chacune) ; boîte locale de la démo, garde réseau armé, aucun modèle réel."""

    @classmethod
    def setUpClass(cls):
        from agent import demo56107 as D
        cls.D = D
        cls.tmp = Path(tempfile.mkdtemp(prefix='x107-'))
        cls.racines = {l: Path(D.creer(cls.tmp / l, graine=11, langue=l)).parent for l in LANGUES + ('fr',)}

    @classmethod
    def tearDownClass(cls):
        cls.D.desarmer()
        shutil.rmtree(cls.tmp, ignore_errors=True)

    def setUp(self):
        self.addCleanup(self.D.desarmer)

    def desk(self, langue):
        from agent.common import load_config
        from agent.desk import Desk
        e = patch.dict(os.environ, self.D.environnement(self.racines[langue]))
        e.start()
        self.addCleanup(e.stop)
        d = Desk(load_config(str(self.racines[langue] / 'config.json')))
        self.addCleanup(d.db.close)
        return d

    def courriel(self, d, debut_objet):
        """(Mail, clé canonique) du courriel de la boîte de démo dont l'objet commence ainsi (lu par la boîte réelle de l'application)."""
        from agent import mailbox as mbx
        box = mbx.Mailbox(d.c['mail'])
        try:
            uids = box.search('INBOX', 'ALL')
            for m in box.entetes('INBOX', uids).values():
                if m.subject.startswith(debut_objet):
                    complet = box.fetch('INBOX', m.uid, expected_validity=m.uidvalidity)
                    return complet, complet.key(rm._compte(d))
        finally:
            box.close()
        self.fail('courriel introuvable : ' + debut_objet)

    def jour_audience(self, d, langue):
        cab = self.D._cabinet(langue)
        a = next(a for a in cab['agenda'] if a['dossier'] == cab['dossiers'][0]['id'] and a['nature'] == 'audience')
        return self.D.jour_ouvre(date.fromisoformat(d.settings('demo56107:jour')), a['jour']), cab['dossiers'][0]['id']

    OBJETS = {'en': ('Change of bank details', '[CE-File] Notice of hearing'), 'de': ('Änderung der Bankverbindung', '[beA] Terminsladung'),
              'es': ('Cambio de cuenta bancaria', '[LexNET] Notificación de señalamiento')}

    def test_X3_01_courriel_frauduleux_detecte_par_le_contenu(self):
        for langue in LANGUES:
            with self.subTest(langue=langue):
                d = self.desk(langue)
                mail, cle = self.courriel(d, self.OBJETS[langue][0])
                ctx = fr.contexte(d)
                v = fr.analyser(mail.msg, ctx, cle)
                ids = {s['id'] for s in v['signaux']}
                self.assertIn(v['niveau'], ('suspect', 'danger'), v)
                self.assertTrue({'changement_rib', 'pression', 'demande_virement'} <= ids, ids)
                ctx['noms'] = {}                                    # aucun nom connu : le nom usurpé ne compte plus
                v2 = fr.analyser(mail.msg, ctx, cle)
                self.assertNotIn('nom_usurpe', {s['id'] for s in v2['signaux']})
                self.assertIn(v2['niveau'], ('suspect', 'danger'), v2)       # les seuls signaux de contenu suffisent
                self.D.desarmer()

    def test_X3_02_message_de_juridiction_repere(self):
        for langue in LANGUES:
            with self.subTest(langue=langue):
                d = self.desk(langue)
                mail, _cle = self.courriel(d, self.OBJETS[langue][1])
                from agent.acces5683 import today_of
                jour, _ = today_of(d, 'cabinet')
                attendu, dossier = self.jour_audience(d, langue)
                self.assertTrue(rm.message_de_juridiction(mail))
                x = rm.extraire_evenement(mail.subject, mail.text, jour)
                self.assertEqual((x['date'], x['heure'], x['certitude']), (attendu.isoformat(), '09:30', 'sure'), x)
                self.assertTrue(x['juridiction'])
                self.assertEqual(rm.dossier_du_courriel(d, mail, rm._compte(d), rgs=x['rgs'])[0::2], (dossier, True))
                self.D.desarmer()

    def test_X3_03_routine_sur_message_propose_dans_l_agenda_puis_aujourdhui(self):
        from agent import accueil56100, agenda5634
        for langue in LANGUES:
            with self.subTest(langue=langue):
                d = self.desk(langue)
                mail, cle = self.courriel(d, self.OBJETS[langue][1])
                attendu, dossier = self.jour_audience(d, langue)
                # l'audience semée par la démo est retirée : le message arrive avant qu'elle soit dans l'agenda (cas réel)
                for r in d.db.execute("SELECT id FROM agenda5634 WHERE status='actif' AND kind='audience' AND matter=?", (dossier,)).fetchall():
                    agenda5634.cancel(d, r['id'])
                acteur = ('cabinet', 'administrateur')
                rm.creer(d, acteur, {'titre': 'Juridiction vers agenda', 'criteres': {'expediteur': mail.sender}, 'actions': ['evenement'],
                                     'mode': 'proposer', 'cle': 'x107-routine-%s-0001' % langue})
                self.assertTrue(rm.a_l_arrivee(d, cle, {'source_mailbox': 'INBOX', 'source_uid': str(mail.uid), 'source_uidvalidity': mail.uidvalidity,
                                                         'incoming_message_id': mail.mid}))
                rm.job(d, {})
                [e] = [dict(r) for r in d.db.execute("SELECT * FROM routines56106_elements WHERE genre='evenement'")]
                donnees = json.loads(e['donnees_json'])
                self.assertEqual((e['etat'], e['matter'], e['certitude']), ('a_decider', dossier, 'sure'), e)
                self.assertEqual((donnees['date'], donnees['heure']), (attendu.isoformat(), '09:30'))
                self.assertTrue(donnees['juridiction'])
                self.assertEqual(d.db.execute("SELECT COUNT(*) FROM agenda5634 WHERE status='actif' AND matter=? AND kind='audience'", (dossier,)).fetchone()[0], 0)
                # validation par une personne : l'événement est créé à l'heure du cabinet, puis montré dans Aujourd'hui comme audience vérifiée
                rm.valider(d, acteur, {'id': e['id'], 'version': e['version'], 'cle': 'x107-valider-%s-01' % langue})
                [ev] = [dict(r) for r in d.db.execute("SELECT * FROM agenda5634 WHERE status='actif' AND matter=? AND kind='audience'", (dossier,))]
                local = datetime.fromisoformat(ev['starts']).astimezone(agenda5634._tz(d))
                self.assertEqual(local.strftime('%Y-%m-%d %H:%M'), '%s 09:30' % attendu.isoformat())
                avant = d.db.total_changes
                actions, _complet = accueil56100.classer(d, '/agent-courriel', 'cabinet', 'administrateur', frozenset(),
                                                         maintenant=datetime.combine(attendu - timedelta(days=1), datetime.min.time(), timezone.utc))
                self.assertEqual(d.db.total_changes, avant)                  # R05 : Aujourd'hui n'écrit rien
                aud = [a for a in actions if a['nature'] == 'audience' and a['cle']
                       and d.db.execute('SELECT 1 FROM audiences5688 WHERE id=? AND event_id=?', (a['cle'], ev['id'])).fetchone()]
                self.assertEqual([(a['etat'], a['bouton']) for a in aud], [('Vérifiée', 'Voir l’audience')])
                # même message relu par une seconde routine : l'audience déjà présente est signalée, jamais recréée
                rm.creer(d, acteur, {'titre': 'Juridiction (bis)', 'criteres': {'objet': mail.subject[:40]}, 'actions': ['evenement'],
                                     'mode': 'proposer', 'cle': 'x107-routine-%s-0002' % langue})
                d.db.execute("UPDATE routines56106_arrivees SET etat='a_traiter'")
                d.db.commit()
                rm.job(d, {})
                genres = [r['genre'] for r in d.db.execute('SELECT genre FROM routines56106_elements ORDER BY created')]
                self.assertEqual(genres, ['evenement', 'doublon'])
                self.assertEqual(d.db.execute("SELECT COUNT(*) FROM agenda5634 WHERE status='actif' AND matter=? AND kind='audience'", (dossier,)).fetchone()[0], 1)
                self.D.desarmer()

    def test_X4_01_lecteur_agenda_preremplit_date_et_heure_dans_les_quatre_langues(self):
        """Bouton « → Agenda » du lecteur : message de juridiction réel de la démo, date et heure préremplies (fuseau du cabinet)."""
        from agent import lecteur56106
        objets = dict({l: o[1] for l, o in self.OBJETS.items()}, fr='[RPVA] Avis de fixation')
        for langue in ('fr',) + LANGUES:
            with self.subTest(langue=langue):
                d = self.desk(langue)
                mail, _cle = self.courriel(d, objets[langue])
                attendu, _dossier = self.jour_audience(d, langue)
                p = lecteur56106.proposition(d, lecteur56106._compte_boite(d), mail)
                self.assertEqual((p['evenement']['date'], p['evenement']['heure'], p['reperage']['etat']), (attendu.isoformat(), '09:30', 'sure'), p)
                self.assertIn('9', p['reperage']['expression'])
                self.assertEqual(p['tache']['echeance'], attendu.isoformat())
                self.D.desarmer()

    def test_X4_02_mutation_sans_complement_etranger_l_heure_manque(self):
        from agent import lecteur56106
        d = self.desk('de')
        mail, _cle = self.courriel(d, self.OBJETS['de'][1])
        jour, _ = __import__('agent.acces5683', fromlist=['today_of']).today_of(d, 'cabinet')
        texte = mail.subject + '\n' + mail.text
        self.assertEqual(lecteur56106.reperer_date(texte, jour)['etat'], 'sure')
        origine = rm.extraire_evenement
        with patch.object(rm, 'extraire_evenement', lambda *a, **k: dict(origine(*a, **k), etranger=False)):
            self.assertEqual(lecteur56106.reperer_date(texte, jour)['etat'], 'aucune')     # « Kein sicheres Datum erkannt » sans le lot X


class Lecteur(unittest.TestCase):
    """Repérage du lecteur (« → Agenda ») : complément étranger seulement ; le français exactement inchangé ; le doute laisse le champ vide."""

    def test_X4_03_textes_du_lecteur_francais_inchanges(self):
        from agent import lecteur56106 as L
        source = (Path(__file__).resolve().parent / 'test_v56106_lecteur.py').read_text(encoding='utf-8')
        textes = list(dict.fromkeys(m.group(2).replace('\\n', '\n') for m in re.finditer(r"""(['"])([^'"\n]{6,500})\1""", source)))
        textes += ["L'affaire est appelée à l'audience de mise en état du 26/11/2026 à 9 h 30.", 'Réunion le 26.11.2026 à 9h30.',
                   'Rendez-vous le 14 janvier 2027 à 10 am.', 'Audience le 3 mars 2027 à 9.30.']
        self.assertGreater(len(textes), 100)
        for jour in (JEUDI, date(2026, 12, 30)):
            for t in textes:
                if L._reperer_date_fr(t, jour) != L.reperer_date(t, jour):
                    self.assertTrue(rm.extraire_evenement('', t, jour)['etranger'], t)   # seul un texte étranger peut être complété

    def test_X4_04_doute_champ_vide_et_cas_par_langue(self):
        from agent import lecteur56106 as L
        for texte, attendu in (
                ('Termin wird bestimmt auf den 26.11.2026, 9:30 Uhr.', ('2026-11-26', '09:30', 'sure')),
                ('The claim is listed for a case management conference on 26/11/2026 at 9.30 am.', ('2026-11-26', '09:30', 'sure')),
                ('Se señala la audiencia previa para el día 26/11/2026 a las 9:30 horas.', ('2026-11-26', '09:30', 'sure')),
                ("L'affaire est appelée à l'audience de mise en état du 26/11/2026 à 9 h 30.", ('2026-11-26', '09:30', 'sure')),
                ('The hearing is listed for 14 January 2027.', ('', '', 'aucune')),                       # heure absente : vide
                ('Termin zur mündlichen Verhandlung am 14.01.2027.', ('', '', 'aucune')),
                ('Case management conference: 10 December 2026 at 9.30 am.\nTrial: 17 December 2026 at 2 pm.', ('', '', 'aucune')),
                ('Gesendet am 20.11.2026 um 09:00 Uhr.', ('', '', 'aucune'))):                            # date d'envoi : une référence
            with self.subTest(texte=texte):
                r = L.reperer_date(texte, JEUDI)
                self.assertEqual((r['date'], r['heure'], r['etat']), attendu, r)


if __name__ == '__main__':
    unittest.main()
