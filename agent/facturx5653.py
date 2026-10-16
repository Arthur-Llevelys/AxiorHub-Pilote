"""5.6.53 (audit 5.6.52, § 5.2, § 5.3 et § 7) : factures structurées — Factur-X (CII), UBL et PDF/A-3.

- Le XML CII (profil EN 16931) et l'UBL 2.1 sont produits depuis le MÊME instantané approuvé que le rendu lisible ; aucune seconde
  saisie de montants. Un avoir utilise le type 381 et référence la facture d'origine.
- Profil « Avocat — secret professionnel » : les libellés transmis dans les données structurées sont génériques (« Prestations
  juridiques… ») ; le détail des diligences, les noms d'adversaires, les faits et l'objet du litige ne figurent ni dans le XML, ni dans
  les notes, ni dans le PDF structuré. Le relevé détaillé reste un document distinct remis au client.
- Contrôles internes : principales règles EN 16931 reproduites (sommes, bases, TVA par taux, net à payer, parties, identifiants) et
  concordance avec le rendu. Ce n'est PAS une validation XSD, Schematron ou PDF/A : le rapport dit exactement ce qui a été vérifié.
- PDF/A-3 : conversion LibreOffice (PDF/A-3b) puis ajout du fichier ``factur-x.xml`` (relation AF, métadonnées XMP Factur-X) par mise
  à jour incrémentale, sans dépendance externe. Sans LibreOffice, le PDF n'est pas produit et la facture ne peut pas être dite
  Factur-X.
Version de référence annoncée par le FNFE-MPE au 16 octobre 2026 : Factur-X 1.09.2 / ZUGFeRD 2.5.2 (profil effectivement accepté par
la plateforme retenue à qualifier en bac à sable).
"""
from datetime import datetime, timezone
from decimal import Decimal
import hashlib
import os
from pathlib import Path
import re
import subprocess
import tempfile
from xml.etree import ElementTree as ET

from .common import Stop

FACTURX_VERSION = '1.09.2 (annoncée par le FNFE-MPE) — profil EN 16931'
CII_NS = {'rsm': 'urn:un:unece:uncefact:data:standard:CrossIndustryInvoice:100',
          'ram': 'urn:un:unece:uncefact:data:standard:ReusableAggregateBusinessInformationEntity:100',
          'udt': 'urn:un:unece:uncefact:data:standard:UnqualifiedDataType:100',
          'qdt': 'urn:un:unece:uncefact:data:standard:QualifiedDataType:100'}
UBL_NS = {'inv': 'urn:oasis:names:specification:ubl:schema:xsd:Invoice-2', 'cn': 'urn:oasis:names:specification:ubl:schema:xsd:CreditNote-2',
          'cac': 'urn:oasis:names:specification:ubl:schema:xsd:CommonAggregateComponents-2',
          'cbc': 'urn:oasis:names:specification:ubl:schema:xsd:CommonBasicComponents-2'}
EXEMPTION = {'E': ('VATEX-FR-FRANCHISE', 'TVA non applicable, article 293 B du CGI'), 'AE': ('VATEX-EU-AE', 'Autoliquidation'),
             'O': ('VATEX-EU-O', 'Non soumis à la TVA française')}
GENERIC_LABEL = 'Prestations juridiques'


def _x(text):
    return (str(text if text is not None else '').replace('&', '&amp;').replace('<', '&lt;').replace('>', '&gt;').replace('"', '&quot;'))


def _amt(cents):
    return '%.2f' % (Decimal(int(cents)) / 100)


def _d102(iso):
    return str(iso or '')[:10].replace('-', '')


def public_label(line, profile_secret=True):
    """Libellé transmis dans les données structurées : générique sous le profil secret professionnel."""
    if not profile_secret:
        return str(line.get('libelle') or GENERIC_LABEL)[:300]
    nature = {'honoraires': GENERIC_LABEL, 'frais': 'Frais refacturés', 'debours': 'Débours'}.get(line.get('nature') or 'honoraires', GENERIC_LABEL)
    period = str(line.get('periode') or '').strip()
    return (nature + (' — ' + period if period else ''))[:300]


def _party_cii(tag, p, scope):
    addr = p.get('adresse') or {}
    out = '<ram:%s><ram:Name>%s</ram:Name>' % (tag, _x(p.get('nom')))
    if p.get('siren') and scope != 'b2c':
        out += '<ram:SpecifiedLegalOrganization><ram:ID schemeID="0002">%s</ram:ID></ram:SpecifiedLegalOrganization>' % _x(p['siren'])
    out += ('<ram:PostalTradeAddress><ram:PostcodeCode>%s</ram:PostcodeCode><ram:LineOne>%s</ram:LineOne><ram:CityName>%s</ram:CityName>'
            '<ram:CountryID>%s</ram:CountryID></ram:PostalTradeAddress>') % (_x(addr.get('code_postal')), _x(addr.get('ligne1')), _x(addr.get('ville')),
                                                                               _x(addr.get('pays') or 'FR'))
    if p.get('email'):
        out += '<ram:URIUniversalCommunication><ram:URIID schemeID="EM">%s</ram:URIID></ram:URIUniversalCommunication>' % _x(p['email'])
    if p.get('tva'):
        out += '<ram:SpecifiedTaxRegistration><ram:ID schemeID="VA">%s</ram:ID></ram:SpecifiedTaxRegistration>' % _x(p['tva'])
    return out + '</ram:%s>' % tag


def cii(doc):
    """XML CII Factur-X (EN 16931) depuis l'instantané figé ``doc`` (voir facturation5653.snapshot)."""
    t = doc['totaux']
    fiscal = doc['fiscal']
    secret = doc.get('profil_secret', True)
    lines = ''
    for i, l in enumerate(doc['lignes'], 1):
        cat, rate = l.get('categorie_tva') or fiscal['categorie_tva'], l.get('taux_tva') if l.get('taux_tva') is not None else fiscal['taux']
        lines += ('<ram:IncludedSupplyChainTradeLineItem><ram:AssociatedDocumentLineDocument><ram:LineID>%d</ram:LineID></ram:AssociatedDocumentLineDocument>'
                  '<ram:SpecifiedTradeProduct><ram:Name>%s</ram:Name></ram:SpecifiedTradeProduct><ram:SpecifiedLineTradeAgreement><ram:NetPriceProductTradePrice>'
                  '<ram:ChargeAmount>%s</ram:ChargeAmount></ram:NetPriceProductTradePrice></ram:SpecifiedLineTradeAgreement><ram:SpecifiedLineTradeDelivery>'
                  '<ram:BilledQuantity unitCode="%s">%s</ram:BilledQuantity></ram:SpecifiedLineTradeDelivery><ram:SpecifiedLineTradeSettlement><ram:ApplicableTradeTax>'
                  '<ram:TypeCode>VAT</ram:TypeCode><ram:CategoryCode>%s</ram:CategoryCode>%s</ram:ApplicableTradeTax>'
                  '<ram:SpecifiedTradeSettlementLineMonetarySummation><ram:LineTotalAmount>%s</ram:LineTotalAmount>'
                  '</ram:SpecifiedTradeSettlementLineMonetarySummation></ram:SpecifiedLineTradeSettlement></ram:IncludedSupplyChainTradeLineItem>') % (
            i, _x(public_label(l, secret)), _amt(l['prix_unitaire_ht_cents']), 'HUR' if l.get('unite') == 'heure' else 'C62', _x(l['quantite']), cat,
            ('<ram:RateApplicablePercent>%s</ram:RateApplicablePercent>' % _x(rate)) if cat in ('S', 'Z') else '', _amt(l['montant_ht_cents']))
    taxes = ''
    for v in t['ventilation']:
        code = EXEMPTION.get(v['categorie'])
        taxes += ('<ram:ApplicableTradeTax><ram:CalculatedAmount>%s</ram:CalculatedAmount><ram:TypeCode>VAT</ram:TypeCode>%s<ram:BasisAmount>%s</ram:BasisAmount>'
                  '<ram:CategoryCode>%s</ram:CategoryCode>%s%s%s</ram:ApplicableTradeTax>') % (
            _amt(v['tva_cents']), ('<ram:ExemptionReason>%s</ram:ExemptionReason>' % _x(fiscal.get('mention') or code[1])) if code else '',
            _amt(v['base_ht_cents']), v['categorie'], ('<ram:ExemptionReasonCode>%s</ram:ExemptionReasonCode>' % code[0]) if code else '',
            '<ram:DueDateTypeCode>%s</ram:DueDateTypeCode>' % ('5' if fiscal.get('tva_sur_debits') else '72') if v['categorie'] == 'S' else '',
            ('<ram:RateApplicablePercent>%s</ram:RateApplicablePercent>' % _x(v['taux'])) if v['categorie'] in ('S', 'Z') else '')
    seller, buyer = doc['emetteur'], doc['client']
    type_code = {'facture': '380', 'avoir': '381', 'facture_acompte': '386'}.get(doc['type_fiscal'], '380')
    notes = ''
    if fiscal.get('mention'):
        notes += '<ram:IncludedNote><ram:Content>%s</ram:Content><ram:SubjectCode>REG</ram:SubjectCode></ram:IncludedNote>' % _x(fiscal['mention'])
    if doc.get('clause_retard'):
        notes += '<ram:IncludedNote><ram:Content>%s</ram:Content><ram:SubjectCode>PMD</ram:SubjectCode></ram:IncludedNote>' % _x(doc['clause_retard'])
    if fiscal.get('tva_sur_debits'):
        notes += '<ram:IncludedNote><ram:Content>Option pour le paiement de la taxe d’après les débits</ram:Content><ram:SubjectCode>AAI</ram:SubjectCode></ram:IncludedNote>'
    ref = ''
    if doc.get('reference_convention'):
        ref = '<ram:ContractReferencedDocument><ram:IssuerAssignedID>%s</ram:IssuerAssignedID></ram:ContractReferencedDocument>' % _x(doc['reference_convention'])
    origin = ''
    if doc.get('facture_origine'):
        origin = ('<ram:InvoiceReferencedDocument><ram:IssuerAssignedID>%s</ram:IssuerAssignedID><ram:FormattedIssueDateTime><qdt:DateTimeString format="102">%s'
                  '</qdt:DateTimeString></ram:FormattedIssueDateTime></ram:InvoiceReferencedDocument>') % (_x(doc['facture_origine']['numero']),
                                                                                                           _d102(doc['facture_origine']['date']))
    means = ''
    if seller.get('iban'):
        means = ('<ram:SpecifiedTradeSettlementPaymentMeans><ram:TypeCode>58</ram:TypeCode><ram:PayeePartyCreditorFinancialAccount><ram:IBANID>%s</ram:IBANID>'
                 '</ram:PayeePartyCreditorFinancialAccount></ram:SpecifiedTradeSettlementPaymentMeans>') % _x(seller['iban'].replace(' ', ''))
    due = ('<ram:SpecifiedTradePaymentTerms><ram:DueDateDateTime><udt:DateTimeString format="102">%s</udt:DateTimeString></ram:DueDateDateTime>'
           '</ram:SpecifiedTradePaymentTerms>') % _d102(doc['date_echeance']) if doc.get('date_echeance') else ''
    xml = ('<?xml version="1.0" encoding="UTF-8"?>\n<rsm:CrossIndustryInvoice xmlns:rsm="%(rsm)s" xmlns:ram="%(ram)s" xmlns:qdt="%(qdt)s" xmlns:udt="%(udt)s">'
           % CII_NS)
    xml += ('<rsm:ExchangedDocumentContext><ram:GuidelineSpecifiedDocumentContextParameter><ram:ID>urn:cen.eu:en16931:2017</ram:ID>'
            '</ram:GuidelineSpecifiedDocumentContextParameter></rsm:ExchangedDocumentContext>')
    xml += ('<rsm:ExchangedDocument><ram:ID>%s</ram:ID><ram:TypeCode>%s</ram:TypeCode><ram:IssueDateTime><udt:DateTimeString format="102">%s</udt:DateTimeString>'
            '</ram:IssueDateTime>%s</rsm:ExchangedDocument>') % (_x(doc['numero']), type_code, _d102(doc['date_emission']), notes)
    xml += '<rsm:SupplyChainTradeTransaction>' + lines
    xml += '<ram:ApplicableHeaderTradeAgreement>%s%s%s%s</ram:ApplicableHeaderTradeAgreement>' % (
        ('<ram:BuyerReference>%s</ram:BuyerReference>' % _x(doc['reference_acheteur'])) if doc.get('reference_acheteur') else '',
        _party_cii('SellerTradeParty', seller, 'b2b'), _party_cii('BuyerTradeParty', buyer, fiscal.get('perimetre')), ref)
    xml += ('<ram:ApplicableHeaderTradeDelivery><ram:ActualDeliverySupplyChainEvent><ram:OccurrenceDateTime><udt:DateTimeString format="102">%s</udt:DateTimeString>'
            '</ram:OccurrenceDateTime></ram:ActualDeliverySupplyChainEvent></ram:ApplicableHeaderTradeDelivery>') % _d102(doc.get('date_prestation') or doc['date_emission'])
    xml += ('<ram:ApplicableHeaderTradeSettlement><ram:InvoiceCurrencyCode>EUR</ram:InvoiceCurrencyCode>%s%s%s<ram:SpecifiedTradeSettlementHeaderMonetarySummation>'
            '<ram:LineTotalAmount>%s</ram:LineTotalAmount><ram:TaxBasisTotalAmount>%s</ram:TaxBasisTotalAmount><ram:TaxTotalAmount currencyID="EUR">%s</ram:TaxTotalAmount>'
            '<ram:GrandTotalAmount>%s</ram:GrandTotalAmount><ram:TotalPrepaidAmount>%s</ram:TotalPrepaidAmount><ram:DuePayableAmount>%s</ram:DuePayableAmount>'
            '</ram:SpecifiedTradeSettlementHeaderMonetarySummation>%s</ram:ApplicableHeaderTradeSettlement>') % (
        means, taxes, due, _amt(t['ht_cents']), _amt(t['ht_cents']), _amt(t['tva_cents']), _amt(t['ttc_cents']), _amt(t['deja_regle_cents']),
        _amt(t['net_a_payer_cents']), origin)
    xml += '</rsm:SupplyChainTradeTransaction></rsm:CrossIndustryInvoice>'
    return xml


def ubl(doc):
    """UBL 2.1 (Invoice ou CreditNote) depuis le même instantané ; adaptateur distinct, pas un renommage du CII."""
    credit = doc['type_fiscal'] == 'avoir'
    root = 'CreditNote' if credit else 'Invoice'
    t, fiscal, secret = doc['totaux'], doc['fiscal'], doc.get('profil_secret', True)

    def party(p, tag):
        addr = p.get('adresse') or {}
        tax = ('<cac:PartyTaxScheme><cbc:CompanyID>%s</cbc:CompanyID><cac:TaxScheme><cbc:ID>VAT</cbc:ID></cac:TaxScheme></cac:PartyTaxScheme>' % _x(p['tva'])
               if p.get('tva') else '')
        legal = '<cac:PartyLegalEntity><cbc:RegistrationName>%s</cbc:RegistrationName>%s</cac:PartyLegalEntity>' % (
            _x(p.get('nom')), ('<cbc:CompanyID schemeID="0002">%s</cbc:CompanyID>' % _x(p['siren'])) if p.get('siren') else '')
        return ('<cac:%s><cac:Party><cac:PostalAddress><cbc:StreetName>%s</cbc:StreetName><cbc:CityName>%s</cbc:CityName><cbc:PostalZone>%s</cbc:PostalZone>'
                '<cac:Country><cbc:IdentificationCode>%s</cbc:IdentificationCode></cac:Country></cac:PostalAddress>%s%s</cac:Party></cac:%s>') % (
            tag, _x(addr.get('ligne1')), _x(addr.get('ville')), _x(addr.get('code_postal')), _x(addr.get('pays') or 'FR'), tax, legal, tag)

    def category(cat, rate):
        code = EXEMPTION.get(cat)
        return ('<cac:%%s><cbc:ID>%s</cbc:ID>%s%s<cac:TaxScheme><cbc:ID>VAT</cbc:ID></cac:TaxScheme></cac:%%s>' % (
            cat, ('<cbc:Percent>%s</cbc:Percent>' % _x(rate)) if cat in ('S', 'Z') else '',
            ('<cbc:TaxExemptionReasonCode>%s</cbc:TaxExemptionReasonCode><cbc:TaxExemptionReason>%s</cbc:TaxExemptionReason>' % (code[0], _x(fiscal.get('mention') or code[1])))
            if code else ''))
    xml = '<?xml version="1.0" encoding="UTF-8"?>\n<%s xmlns="%s" xmlns:cac="%s" xmlns:cbc="%s">' % (root, UBL_NS['cn' if credit else 'inv'], UBL_NS['cac'], UBL_NS['cbc'])
    xml += '<cbc:CustomizationID>urn:cen.eu:en16931:2017</cbc:CustomizationID><cbc:ID>%s</cbc:ID><cbc:IssueDate>%s</cbc:IssueDate>' % (
        _x(doc['numero']), _x(str(doc['date_emission'])[:10]))
    if doc.get('date_echeance') and not credit:
        xml += '<cbc:DueDate>%s</cbc:DueDate>' % _x(str(doc['date_echeance'])[:10])
    xml += '<cbc:%s>%s</cbc:%s>' % ('CreditNoteTypeCode' if credit else 'InvoiceTypeCode', '381' if credit else ('386' if doc['type_fiscal'] == 'facture_acompte' else '380'),
                                    'CreditNoteTypeCode' if credit else 'InvoiceTypeCode')
    if fiscal.get('mention'):
        xml += '<cbc:Note>%s</cbc:Note>' % _x(fiscal['mention'])
    xml += '<cbc:DocumentCurrencyCode>EUR</cbc:DocumentCurrencyCode>'
    if doc.get('reference_acheteur'):
        xml += '<cbc:BuyerReference>%s</cbc:BuyerReference>' % _x(doc['reference_acheteur'])
    if doc.get('facture_origine'):
        xml += '<cac:BillingReference><cac:InvoiceDocumentReference><cbc:ID>%s</cbc:ID><cbc:IssueDate>%s</cbc:IssueDate></cac:InvoiceDocumentReference></cac:BillingReference>' % (
            _x(doc['facture_origine']['numero']), _x(str(doc['facture_origine']['date'])[:10]))
    if doc.get('reference_convention'):
        xml += '<cac:ContractDocumentReference><cbc:ID>%s</cbc:ID></cac:ContractDocumentReference>' % _x(doc['reference_convention'])
    xml += party(doc['emetteur'], 'AccountingSupplierParty') + party(doc['client'], 'AccountingCustomerParty')
    if doc['emetteur'].get('iban'):
        xml += '<cac:PaymentMeans><cbc:PaymentMeansCode>58</cbc:PaymentMeansCode><cac:PayeeFinancialAccount><cbc:ID>%s</cbc:ID></cac:PayeeFinancialAccount></cac:PaymentMeans>' % _x(
            doc['emetteur']['iban'].replace(' ', ''))
    xml += '<cac:TaxTotal><cbc:TaxAmount currencyID="EUR">%s</cbc:TaxAmount>' % _amt(t['tva_cents'])
    for v in t['ventilation']:
        xml += ('<cac:TaxSubtotal><cbc:TaxableAmount currencyID="EUR">%s</cbc:TaxableAmount><cbc:TaxAmount currencyID="EUR">%s</cbc:TaxAmount>' % (
            _amt(v['base_ht_cents']), _amt(v['tva_cents']))) + (category(v['categorie'], v['taux']) % ('TaxCategory', 'TaxCategory')) + '</cac:TaxSubtotal>'
    xml += '</cac:TaxTotal>'
    xml += ('<cac:LegalMonetaryTotal><cbc:LineExtensionAmount currencyID="EUR">%s</cbc:LineExtensionAmount><cbc:TaxExclusiveAmount currencyID="EUR">%s</cbc:TaxExclusiveAmount>'
            '<cbc:TaxInclusiveAmount currencyID="EUR">%s</cbc:TaxInclusiveAmount><cbc:PrepaidAmount currencyID="EUR">%s</cbc:PrepaidAmount>'
            '<cbc:PayableAmount currencyID="EUR">%s</cbc:PayableAmount></cac:LegalMonetaryTotal>') % (
        _amt(t['ht_cents']), _amt(t['ht_cents']), _amt(t['ttc_cents']), _amt(t['deja_regle_cents']), _amt(t['net_a_payer_cents']))
    line_tag, qty_tag = ('CreditNoteLine', 'CreditedQuantity') if credit else ('InvoiceLine', 'InvoicedQuantity')
    for i, l in enumerate(doc['lignes'], 1):
        cat, rate = l.get('categorie_tva') or fiscal['categorie_tva'], l.get('taux_tva') if l.get('taux_tva') is not None else fiscal['taux']
        xml += ('<cac:%s><cbc:ID>%d</cbc:ID><cbc:%s unitCode="%s">%s</cbc:%s><cbc:LineExtensionAmount currencyID="EUR">%s</cbc:LineExtensionAmount>'
                '<cac:Item><cbc:Name>%s</cbc:Name>%s</cac:Item><cac:Price><cbc:PriceAmount currencyID="EUR">%s</cbc:PriceAmount></cac:Price></cac:%s>') % (
            line_tag, i, qty_tag, 'HUR' if l.get('unite') == 'heure' else 'C62', _x(l['quantite']), qty_tag, _amt(l['montant_ht_cents']),
            _x(public_label(l, secret)), category(cat, rate) % ('ClassifiedTaxCategory', 'ClassifiedTaxCategory'), _amt(l['prix_unitaire_ht_cents']), line_tag)
    return xml + '</%s>' % root


# ------------------------------------------------------------------------------------------------ contrôles internes
def _num(el):
    return int((Decimal(el.text.strip()) * 100).to_integral_value()) if el is not None and el.text else None


def check_cii(xml, doc, visible_text=''):
    """Contrôles reproduits (sous-ensemble des règles EN 16931 et concordance) ; le rapport distingue vérifié / non vérifié."""
    checks = []

    def add(rule, ok, detail=''):
        checks.append({'regle': rule, 'ok': bool(ok), 'detail': detail})
    try:
        root = ET.fromstring(xml.encode('utf-8'))
    except ET.ParseError as ex:
        return {'ok': False, 'controles': [{'regle': 'XML bien formé', 'ok': False, 'detail': str(ex)[:200]}], 'non_verifie': NOT_VERIFIED}
    ns = CII_NS
    q = lambda path: root.find(path, ns)
    add('XML bien formé', True)
    add('BT-1 numéro de facture', (q('rsm:ExchangedDocument/ram:ID').text or '') == doc['numero'] and bool(doc['numero']))
    add('BT-2 date d’émission', bool(q('.//ram:IssueDateTime/udt:DateTimeString') is not None))
    lines = root.findall('.//ram:IncludedSupplyChainTradeLineItem', ns)
    line_sum = sum(_num(l.find('.//ram:LineTotalAmount', ns)) or 0 for l in lines)
    s = 'rsm:SupplyChainTradeTransaction/ram:ApplicableHeaderTradeSettlement/ram:SpecifiedTradeSettlementHeaderMonetarySummation/'
    total_lines, basis, tax, grand = _num(q(s + 'ram:LineTotalAmount')), _num(q(s + 'ram:TaxBasisTotalAmount')), _num(q(s + 'ram:TaxTotalAmount')), _num(q(s + 'ram:GrandTotalAmount'))
    prepaid, payable = _num(q(s + 'ram:TotalPrepaidAmount')) or 0, _num(q(s + 'ram:DuePayableAmount'))
    add('BR-CO-10 somme des lignes = total des lignes', line_sum == total_lines, '%s / %s' % (line_sum, total_lines))
    add('BR-CO-13 base taxable = total des lignes (sans remise ni charge au niveau document)', basis == total_lines)
    add('BR-CO-15 total TTC = base + TVA', grand == (basis or 0) + (tax or 0))
    add('BR-CO-16 net à payer = TTC − déjà réglé', payable == (grand or 0) - prepaid)
    vat_sum = 0
    for tt in root.findall('.//ram:ApplicableHeaderTradeSettlement/ram:ApplicableTradeTax', ns):
        b, c = _num(tt.find('ram:BasisAmount', ns)), _num(tt.find('ram:CalculatedAmount', ns))
        cat = tt.find('ram:CategoryCode', ns).text
        rate_el = tt.find('ram:RateApplicablePercent', ns)
        vat_sum += c or 0
        if cat == 'S':
            expected = int((Decimal(b) * Decimal(rate_el.text) / 100).quantize(Decimal('1'), rounding='ROUND_HALF_UP'))
            add('BR-S-09 TVA du taux %s %% = base × taux' % rate_el.text, abs(expected - c) <= 1, '%s / %s' % (expected, c))
        else:
            add('BR-%s-09 TVA nulle et motif d’exonération' % cat, c == 0 and tt.find('ram:ExemptionReason', ns) is not None)
    add('BR-CO-14 total TVA = somme des TVA par taux', vat_sum == tax)
    add('Concordance avec l’instantané approuvé (HT, TVA, TTC, net)', (total_lines, tax, grand, payable) == (
        doc['totaux']['ht_cents'], doc['totaux']['tva_cents'], doc['totaux']['ttc_cents'], doc['totaux']['net_a_payer_cents']))
    seller = q('.//ram:SellerTradeParty/ram:Name')
    buyer = q('.//ram:BuyerTradeParty/ram:Name')
    add('BT-27 / BT-44 noms du vendeur et de l’acheteur', bool(seller is not None and seller.text and buyer is not None and buyer.text))
    if doc['fiscal'].get('perimetre') == 'b2b_fr':
        add('Règle française : SIREN de l’acheteur assujetti établi en France',
            q('.//ram:BuyerTradeParty/ram:SpecifiedLegalOrganization/ram:ID') is not None)
    add('SIREN du vendeur', q('.//ram:SellerTradeParty/ram:SpecifiedLegalOrganization/ram:ID') is not None)
    leaked = [w for w in doc.get('termes_confidentiels') or [] if w and len(w) > 3 and w.lower() in xml.lower()]
    add('Secret professionnel : aucun terme confidentiel du dossier dans le XML', not leaked, ', '.join(leaked[:5]))
    if visible_text:
        from .honoraires5653 import fmt
        for label, key in (('HT', 'ht_cents'), ('TTC', 'ttc_cents'), ('net à payer', 'net_a_payer_cents')):
            add('Concordance rendu lisible / XML : %s' % label, fmt(doc['totaux'][key]) in visible_text)
        add('Concordance rendu lisible / XML : numéro', doc['numero'] in visible_text)
    return {'ok': all(c['ok'] for c in checks), 'controles': checks, 'non_verifie': NOT_VERIFIED}


NOT_VERIFIED = ['Validation XSD officielle du format', 'Schematron EN 16931 complet et règles françaises de la plateforme',
                'Validation PDF/A (veraPDF ou équivalent)', 'Acceptation par la plateforme agréée retenue (bac à sable puis production)']


# ------------------------------------------------------------------------------------------------ PDF/A-3 et fichier joint
def office_pdf(docx_bytes):
    """PDF/A-3b par LibreOffice (aucun réseau) ; None si LibreOffice est absent."""
    from . import portable
    office = portable.which('libreoffice')
    if not office:
        return None
    with tempfile.TemporaryDirectory(prefix='facturx-', ignore_cleanup_errors=portable.WINDOWS) as temp:
        p = Path(temp)
        src = p / 'facture.docx'
        src.write_bytes(docx_bytes)
        env = os.environ.copy()
        env['HOME'] = str(p)
        env['TMPDIR'] = str(p)
        filt = 'pdf:writer_pdf_Export:{"SelectPdfVersion":{"type":"long","value":"3"}}'
        pdf = p / 'facture.pdf'
        for _attempt in (1, 2):
            proc = subprocess.run([office, '-env:UserInstallation=' + (p / 'profile').as_uri(), '--headless', '--convert-to', filt, '--outdir', str(p), str(src)],
                                  env=env, capture_output=True, timeout=120, check=False)
            if pdf.is_file() and pdf.stat().st_size:
                break
        if not pdf.is_file() or not pdf.stat().st_size:
            raise Stop('pdf_a_indisponible')
        return portable.read_output(pdf)


XMP_FX = '''<rdf:Description xmlns:fx="urn:factur-x:pdfa:CrossIndustryDocument:invoice:1p0#" rdf:about="">
<fx:DocumentType>INVOICE</fx:DocumentType><fx:DocumentFileName>factur-x.xml</fx:DocumentFileName><fx:Version>1.0</fx:Version>
<fx:ConformanceLevel>EN 16931</fx:ConformanceLevel></rdf:Description>
<rdf:Description xmlns:pdfaExtension="http://www.aiim.org/pdfa/ns/extension/" xmlns:pdfaSchema="http://www.aiim.org/pdfa/ns/schema#"
 xmlns:pdfaProperty="http://www.aiim.org/pdfa/ns/property#" rdf:about=""><pdfaExtension:schemas><rdf:Bag><rdf:li rdf:parseType="Resource">
<pdfaSchema:schema>Factur-X PDFA Extension Schema</pdfaSchema:schema><pdfaSchema:namespaceURI>urn:factur-x:pdfa:CrossIndustryDocument:invoice:1p0#</pdfaSchema:namespaceURI>
<pdfaSchema:prefix>fx</pdfaSchema:prefix><pdfaSchema:property><rdf:Seq>
<rdf:li rdf:parseType="Resource"><pdfaProperty:name>DocumentFileName</pdfaProperty:name><pdfaProperty:valueType>Text</pdfaProperty:valueType><pdfaProperty:category>external</pdfaProperty:category><pdfaProperty:description>Nom du fichier XML joint</pdfaProperty:description></rdf:li>
<rdf:li rdf:parseType="Resource"><pdfaProperty:name>DocumentType</pdfaProperty:name><pdfaProperty:valueType>Text</pdfaProperty:valueType><pdfaProperty:category>external</pdfaProperty:category><pdfaProperty:description>INVOICE</pdfaProperty:description></rdf:li>
<rdf:li rdf:parseType="Resource"><pdfaProperty:name>Version</pdfaProperty:name><pdfaProperty:valueType>Text</pdfaProperty:valueType><pdfaProperty:category>external</pdfaProperty:category><pdfaProperty:description>Version du schéma</pdfaProperty:description></rdf:li>
<rdf:li rdf:parseType="Resource"><pdfaProperty:name>ConformanceLevel</pdfaProperty:name><pdfaProperty:valueType>Text</pdfaProperty:valueType><pdfaProperty:category>external</pdfaProperty:category><pdfaProperty:description>Profil</pdfaProperty:description></rdf:li>
</rdf:Seq></pdfaSchema:property></rdf:li></rdf:Bag></pdfaExtension:schemas></rdf:Description>
'''


def _obj(pdf, num):
    m = None
    for m in re.finditer(rb'(?<![0-9])%d\s+0\s+obj\b(.*?)endobj' % num, pdf, re.S):
        pass                                                   # dernière définition (mises à jour incrémentales)
    return m.group(1) if m else None


def embed_xml(pdf, xml_bytes, name='factur-x.xml'):
    """Ajoute le XML (relation /Alternative, profil EN 16931) au PDF/A-3 par mise à jour incrémentale ; lève Stop si le PDF n'est pas
    modifiable sans réécriture (catalogue compressé)."""
    tail = pdf[-2048:]
    m = re.search(rb'startxref\s+(\d+)\s+%%EOF\s*$', tail)
    if not m:
        raise Stop('pdf_non_modifiable')
    prev = int(m.group(1))
    trailer = pdf[prev:prev + 4096]
    root_m = re.search(rb'/Root\s+(\d+)\s+0\s+R', trailer) or re.search(rb'/Root\s+(\d+)\s+0\s+R', pdf[-8192:])
    size_m = re.search(rb'/Size\s+(\d+)', trailer)
    if not root_m or not size_m:
        raise Stop('pdf_non_modifiable')
    root_num, size = int(root_m.group(1)), int(size_m.group(1))
    info_m = re.search(rb'/Info\s+(\d+\s+0\s+R)', trailer)
    id_m = re.search(rb'/ID\s*(\[[^\]]*\])', trailer)
    catalog = _obj(pdf, root_num)
    if catalog is None or b'/Type' not in catalog:
        raise Stop('pdf_non_modifiable')
    body = catalog.strip()
    if not (body.startswith(b'<<') and body.endswith(b'>>')):
        raise Stop('pdf_non_modifiable')
    meta_m = re.search(rb'/Metadata\s+(\d+)\s+0\s+R', body)
    xmp = b''
    if meta_m:
        meta_obj = _obj(pdf, int(meta_m.group(1))) or b''
        sm = re.search(rb'stream\r?\n(.*?)\r?\nendstream', meta_obj, re.S)
        if sm and b'/Filter' not in meta_obj.split(b'stream')[0]:
            xmp = sm.group(1)
    if not xmp or b'</rdf:RDF>' not in xmp:
        raise Stop('pdf_non_modifiable')
    xmp = xmp.replace(b'</rdf:RDF>', XMP_FX.encode('utf-8') + b'</rdf:RDF>', 1)
    n_file, n_spec, n_meta = size, size + 1, size + 2
    now = datetime.now(timezone.utc).strftime("D:%Y%m%d%H%M%S+00'00'")
    md5 = hashlib.md5(xml_bytes).hexdigest()                   # somme de contrôle exigée par /Params (pas un usage de sécurité)
    objs = {
        n_file: b'<< /Type /EmbeddedFile /Subtype /text#2Fxml /Params << /Size %d /ModDate (%s) /CheckSum <%s> >> /Length %d >>\nstream\n' % (
            len(xml_bytes), now.encode(), md5.encode(), len(xml_bytes)) + xml_bytes + b'\nendstream',
        n_spec: b'<< /Type /Filespec /F (%s) /UF (%s) /AFRelationship /Alternative /Desc (Factur-X) /EF << /F %d 0 R /UF %d 0 R >> >>' % (
            name.encode(), name.encode(), n_file, n_file),
        n_meta: b'<< /Type /Metadata /Subtype /XML /Length %d >>\nstream\n' % len(xmp) + xmp + b'\nendstream',
    }
    body = re.sub(rb'/Metadata\s+\d+\s+0\s+R', b'', body)
    body = re.sub(rb'/AF\s*\[[^\]]*\]', b'', body)
    if b'/Names' in body:
        raise Stop('pdf_non_modifiable')                       # arbre de noms existant : réécriture non prise en charge sans bibliothèque
    body = body[:-2] + b' /Metadata %d 0 R /AF [%d 0 R] /Names << /EmbeddedFiles << /Names [(%s) %d 0 R] >> >> >>' % (n_meta, n_spec, name.encode(), n_spec)
    objs[root_num] = body
    out = bytearray(pdf if pdf.endswith(b'\n') else pdf + b'\n')
    offsets = {}
    for num in sorted(objs):
        offsets[num] = len(out)
        out += b'%d 0 obj\n' % num + objs[num] + b'\nendobj\n'
    xref_at = len(out)
    out += b'xref\n'
    for num in sorted(offsets):
        out += b'%d 1\n%010d 00000 n \n' % (num, offsets[num])
    out += b'trailer\n<< /Size %d /Root %d 0 R%s%s /Prev %d >>\nstartxref\n%d\n%%%%EOF\n' % (
        n_meta + 1, root_num, (b' /Info ' + info_m.group(1)) if info_m else b'', (b' /ID ' + id_m.group(1)) if id_m else b'', prev, xref_at)
    return bytes(out)


def embedded_xml(pdf, name='factur-x.xml'):
    """Relecture : XML réellement joint (contrôle après écriture)."""
    m = re.search(rb'/F \(%s\).*?/EF << /F (\d+) 0 R' % re.escape(name.encode()), pdf, re.S)
    if not m:
        return None
    obj = _obj(pdf, int(m.group(1)))
    s = re.search(rb'stream\n(.*)\nendstream', obj or b'', re.S)
    return s.group(1) if s else None
