"""5.6.88 : accusé de paiement — chaque règlement enregistré propose un projet d'accusé, à relire.

- La proposition naît dans ``facturation5653.record_payment`` : une seule par règlement, jamais bloquante pour le règlement lui-même.
- Rien n'est envoyé : « Déposer dans Brouillons » écrit dans la messagerie (la messagerie l'envoie quand l'avocat le décide), « Écarter » ferme.
- Le solde cité est relu au dépôt ; un texte corrigé par l'avocat n'est jamais réécrit.
- Désactivable : Relances d'impayés › Réglages › « Proposer un accusé à chaque règlement ».
- Secret professionnel : ni le nom du dossier, ni l'adversaire, ni la nature des diligences dans le message.
"""
from datetime import datetime, timezone
from html import escape as e
import re
import uuid

from .common import Stop
from . import honoraires5653 as fees

SCHEMA = '''
CREATE TABLE IF NOT EXISTS accuses5688(id TEXT PRIMARY KEY, document TEXT NOT NULL, payment TEXT NOT NULL UNIQUE, matter TEXT NOT NULL, number TEXT NOT NULL,
  recipient TEXT NOT NULL DEFAULT '', subject TEXT NOT NULL, body TEXT NOT NULL, generated_body TEXT NOT NULL, amount_cents INTEGER NOT NULL, day TEXT NOT NULL,
  state TEXT NOT NULL, draft_uid TEXT NOT NULL DEFAULT '', created TEXT NOT NULL, updated TEXT NOT NULL);
CREATE INDEX IF NOT EXISTS accuses5688_etat ON accuses5688(state, document);
'''


def _now():
    return datetime.now(timezone.utc).isoformat()


def ensure_schema(desk):
    desk.db.executescript(SCHEMA)


def _sign(desk):
    return (desk.c.get('mail') or {}).get('from_name') or 'Le cabinet'


def _fr(day):
    return '/'.join(reversed(day.split('-'))) if re.fullmatch(r'\d{4}-\d{2}-\d{2}', day or '') else str(day or '')


def text(number, amount_cents, day, balance_cents, sign):
    subject = 'Accusé de réception de votre règlement — facture %s' % number
    rest = ('Cette facture est désormais intégralement réglée.' if balance_cents <= 0
            else 'Il reste %s à régler sur cette facture.' % fees.fmt(balance_cents))
    body = ('Madame, Monsieur,\n\nNous accusons réception de votre règlement de %s, enregistré à la date du %s, au titre de la facture n° %s. %s\n\n'
            'Nous vous en remercions et vous prions d’agréer, Madame, Monsieur, l’expression de nos salutations distinguées.\n\n%s') % (
        fees.fmt(amount_cents), _fr(day), number, rest, sign)
    return subject, body


def propose(desk, doc_id, payment_id):
    """Projet d'accusé pour un règlement ; None si la proposition est désactivée. Aucun envoi, aucun dépôt ici."""
    from . import facturation5653 as fact, relances5636 as relances
    if not relances.settings(desk).get('accuse_paiement', True):
        return None
    ensure_schema(desk)
    doc = desk.db.execute('SELECT matter,number FROM b53_documents WHERE id=?', (str(doc_id),)).fetchone()
    pay = desk.db.execute('SELECT amount_cents,day FROM b53_payments WHERE id=? AND document=?', (str(payment_id), str(doc_id))).fetchone()
    if not doc or not pay:
        return None
    balance = fact.invoice_balance(desk, doc_id)
    subject, body = text(doc['number'], pay['amount_cents'], pay['day'], balance, _sign(desk))
    aid, now = 'acc-' + uuid.uuid4().hex, _now()
    cur = desk.db.execute('INSERT OR IGNORE INTO accuses5688(id,document,payment,matter,number,recipient,subject,body,generated_body,amount_cents,day,state,created,updated) '
                          'VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?)', (aid, str(doc_id), str(payment_id), doc['matter'], doc['number'], relances.internal_recipient(desk, doc['matter']),
                                                                  subject, body, body, pay['amount_cents'], pay['day'], 'a_valider', now, now))
    desk.db.commit()
    if cur.rowcount:
        desk.audit('accuses5688_propose', {'document': str(doc_id), 'reglement': str(payment_id)})
        return aid
    return None


def _hidden():
    from .membres5650 import viewer_hidden
    return viewer_hidden() or frozenset()


def pending(desk, document=''):
    ensure_schema(desk)
    sql = "SELECT * FROM accuses5688 WHERE state='a_valider'" + (' AND document=?' if document else '') + ' ORDER BY created'
    hidden = _hidden()
    return [dict(r) for r in desk.db.execute(sql, (str(document),) if document else ()) if r['matter'] not in hidden]


def _get(desk, aid):
    ensure_schema(desk)
    row = desk.db.execute('SELECT * FROM accuses5688 WHERE id=?', (str(aid),)).fetchone()
    if not row or row['matter'] in _hidden():
        raise Stop('accuse_absent')
    return row


def deposit(desk, owner, role, aid, data):
    """Dépôt dans Brouillons, sur décision de l'avocat ; le solde cité est relu. Le message n'est pas envoyé."""
    from . import facturation5653 as fact
    fact._require(desk, owner, role, 'deposer')
    row = _get(desk, aid)
    if row['state'] != 'a_valider':
        raise Stop('accuse_absent')
    to = str(data.get('recipient') if data.get('recipient') is not None else row['recipient']).strip().lower()
    subject = re.sub(r'[\r\n]+', ' ', str(data.get('subject') or row['subject'])).strip()[:200]
    body = str(data.get('body') or row['body'])[:8000]
    paid = desk.db.execute('SELECT 1 FROM b53_payments WHERE id=? AND verified=1', (row['payment'],)).fetchone()
    now = _now()
    if not paid:                                              # règlement retiré entre-temps : l'accusé n'a plus d'objet
        desk.db.execute("UPDATE accuses5688 SET state='perimee',updated=? WHERE id=?", (now, row['id']))
        desk.db.commit()
        raise Stop('accuse_perime')
    if body == row['generated_body']:                         # texte non corrigé : reste à payer actualisé (un autre règlement a pu arriver)
        subject_new, body = text(row['number'], row['amount_cents'], row['day'], fact.invoice_balance(desk, row['document']), _sign(desk))
        if subject == row['subject']:
            subject = subject_new
    from html import escape
    from .webmail5628 import save
    out = save(desk, {'mode': 'nouveau', 'to': to, 'subject': subject,
                      'html': ''.join('<p>%s</p>' % escape(p).replace('\n', '<br>') for p in body.split('\n\n'))}, owner)
    desk.db.execute("UPDATE accuses5688 SET state='deposee',recipient=?,subject=?,body=?,draft_uid=?,updated=? WHERE id=?",
                    (to, subject, body, str(out.get('uid') or ''), now, row['id']))
    desk.db.commit()
    desk.audit('accuses5688_depose', {'document': row['document'], 'reglement': row['payment'], 'owner': str(owner)[:80]})
    return {'id': row['id'], 'message': 'Accusé déposé dans Brouillons' + (' pour ' + to if to else ' (destinataire à compléter)') + ' : rien n’est envoyé.'}


def dismiss(desk, owner, role, aid):
    from . import facturation5653 as fact
    fact._require(desk, owner, role, 'facturation')
    row = _get(desk, aid)
    if row['state'] != 'a_valider':
        raise Stop('accuse_absent')
    desk.db.execute("UPDATE accuses5688 SET state='ecartee',updated=? WHERE id=?", (_now(), row['id']))
    desk.db.commit()
    desk.audit('accuses5688_ecarte', {'document': row['document'], 'reglement': row['payment'], 'owner': str(owner)[:80]})
    return {'id': row['id'], 'message': 'Accusé écarté.'}


def section_html(desk, document=''):
    """Projets d'accusé à relire (page de la facture ; tableau de bord de la facturation)."""
    from .web5653 import _form, _hidden as hidden
    rows = pending(desk, document)
    if not rows:
        return ''
    cards = ''.join(
        '<article class="f88-accuse"><h4>Facture %s — règlement de %s du %s</h4>%s</article>' % (
            e(r['number']), e(fees.fmt(r['amount_cents'])), e(_fr(r['day'])),
            _form('accuse/deposer', hidden(id=r['id']) + '<label>Destinataire<input type="email" name="recipient" value="%s" placeholder="adresse du client"></label>'
                  '<label>Objet<input name="subject" value="%s" maxlength="200"></label><label>Message<textarea name="body" rows="8">%s</textarea></label>' % (
                      e(r['recipient'], quote=True), e(r['subject'], quote=True), e(r['body'])),
                  'Déposer dans Brouillons', extra='<button class="ax-btn ghost" type="submit" data-extra="ecarter=1">Écarter</button>')) for r in rows)
    return ('<section class="ax-card f88-accuses"><h3>Accusés de paiement à relire</h3><p class="b53-note">Proposés à chaque règlement enregistré. Rien n’est envoyé : '
            'le dépôt crée un brouillon dans la messagerie.</p>%s</section>') % cards
