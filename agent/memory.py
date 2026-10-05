"""Dossier-scoped examples from IMAP Sent. No weight updates or global legal rules."""
from datetime import datetime, timezone, timedelta
import difflib
import json
from pathlib import Path
import re
import sqlite3

from .common import Stop, digest, fold
from .mailbox import addresses, ids


def authored(text):
    lines = text.splitlines()
    end = len(lines)
    for i, line in enumerate(lines):
        if re.match(r'^\s*(?:[-_]{2,}\s*(?:message d.origine|original message|forwarded message)|On .+wrote:|Le .+écrit\s*:|>)', line, re.I):
            end = i
            break
        if re.match(r'^\s*(?:From|De)\s*:', line, re.I):
            nearby = '\n'.join(lines[i:i+12])
            if re.search(r'(?im)^\s*(?:To|À|A)\s*:', nearby) and re.search(r'(?im)^\s*(?:Date|Sent|Envoyé|Subject|Objet)\s*:', nearby):
                end = i
                break
    result = '\n'.join(lines[:end]).strip()
    if not 30 <= len(result) <= 6000:
        raise Stop('exemple_envoye_trop_court_ou_long')
    return result


ALLOWED_ROLES = frozenset(('client', 'confrere_adverse', 'tiers', 'prospect', 'juridiction', 'expert', 'administration',
                           'commissaire_justice', 'autre_partie'))     # « personnel » : jamais d'apprentissage
POLICY_VERSION = 'roles-separated-v2'


def audience(matter, targets):
    """Exact address/role bindings; mixed roles and conflicting registry entries fail closed."""
    bindings = []
    for email in sorted(set(targets)):
        roles = {p['role'] for p in matter.get('correspondents', [])
                 if p['email'].lower() == email}
        if len(roles) != 1 or not roles <= ALLOWED_ROLES:
            return ''
        bindings.append((email, next(iter(roles))))
    if not bindings or len({role for _, role in bindings}) != 1:
        return ''
    return json.dumps(bindings, ensure_ascii=False, separators=(',', ':'))


def scope_detail(sent, matters, own):
    if sent.sender not in own or len(sent.msg.get_all('From', [])) != 1:
        return None, [], 'expediteur_non_reconnu'
    if not sent.mid:
        return None, [], 'message_id_absent'
    if sent.msg.get('Bcc'):
        return None, [], 'copie_cachee'
    if any(f in sent.flags for f in ('\\Draft', '\\Deleted')):
        return None, [], 'brouillon_ou_supprime'
    if sent.msg.get('Auto-Submitted', 'no').lower() != 'no' or sent.msg.get('List-Id'):
        return None, [], 'message_automatique'
    targets = sorted(set(addresses(sent.msg.get('To','')) + addresses(sent.msg.get('Cc',''))) - own)
    if not targets or len(targets) > 8:
        return None, targets, 'destinataires_absents_ou_trop_nombreux'
    matches = [m for m in matters if audience(m, targets)]
    if len(matches) > 1:
        subject = fold(sent.subject)
        matches = [m for m in matches if any(len(r)>=4 and re.search(r'(?<!\w)'+re.escape(fold(r))+r'(?!\w)', subject)
                   for r in [m['id']]+m.get('references',[]))]
    if len(matches) == 1:
        return matches[0], targets, 'admissible'
    known = {p['email'].lower() for m in matters for p in m.get('correspondents', [])}
    reason = ('correspondant_non_associe' if set(targets)-known
              else 'dossier_ambigu_ou_roles_mixtes')
    return None, targets, reason


def scope(sent, matters, own):
    matter, targets, _ = scope_detail(sent, matters, own)
    return matter, targets


def change_stats(draft, final):
    # Statistics, not a claim that a semantic change has been understood.
    a, b = draft.split(), final.split()
    matcher = difflib.SequenceMatcher(None, a, b, autojunk=True)
    inserted = deleted = 0
    for tag, i, j, k, l in matcher.get_opcodes():
        if tag in ('replace','delete'): deleted += j-i
        if tag in ('replace','insert'): inserted += l-k
    return {'similarity': round(matcher.ratio(),4), 'words_added': inserted,
            'words_removed': deleted, 'draft_characters':len(draft), 'sent_characters':len(final)}


class SentMemory:
    def __init__(self, config):
        self.c = config
        self.cfg = config.get('memory',{})
        self.db = sqlite3.connect(Path(config['state_dir'])/'sent-memory.sqlite3', timeout=10)
        self.db.execute('CREATE TABLE IF NOT EXISTS seen (key TEXT PRIMARY KEY)')
        self.db.execute('CREATE TABLE IF NOT EXISTS forgotten (key TEXT PRIMARY KEY)')
        self.db.execute('''CREATE TABLE IF NOT EXISTS examples_v2 (
            key TEXT PRIMARY KEY, matter TEXT, recipients TEXT, sent_at TEXT,
            body TEXT, draft TEXT, stats TEXT, provenance TEXT, account TEXT, audience TEXT)''')
        self.db.execute('CREATE TABLE IF NOT EXISTS memory_meta (key TEXT PRIMARY KEY, value TEXT)')
        self.db.execute('''CREATE TABLE IF NOT EXISTS example_reviews(
            key TEXT PRIMARY KEY, data TEXT, created TEXT)''')
        self.db.execute('''CREATE TABLE IF NOT EXISTS preferences(
            id TEXT PRIMARY KEY, account TEXT, scope TEXT, matter TEXT,
            recipients TEXT, role TEXT, text TEXT, status TEXT,
            source_key TEXT, created TEXT)''')
        self.db.execute('''CREATE TABLE IF NOT EXISTS excluded_examples(
            key TEXT PRIMARY KEY, reason TEXT, created TEXT)''')
        # Old examples remain in their old table for rollback. Only the client scope
        # is eligible for migration; new role bindings are checked at every retrieval.
        old = self.db.execute("SELECT name FROM sqlite_master WHERE type='table' AND name='examples'").fetchone()
        if old and not self.db.execute("SELECT 1 FROM memory_meta WHERE key='migration_v2'").fetchone():
            for row in self.db.execute('SELECT * FROM examples').fetchall():
                targets = json.loads(row[2])
                binding = json.dumps([(x, 'client') for x in sorted(targets)], ensure_ascii=False, separators=(',', ':'))
                if not self.db.execute('SELECT 1 FROM forgotten WHERE key=?', (row[0],)).fetchone():
                    self.db.execute('INSERT OR IGNORE INTO examples_v2 VALUES (?,?,?,?,?,?,?,?,?,?)', (*row, binding))
            self.db.execute("INSERT INTO memory_meta VALUES ('migration_v2', 'done')")
        self.db.commit()

    def account(self):
        return digest(self.c['mail']['username']+'@'+self.c['mail']['host'])

    def proposals(self):
        found = []
        # Reports are local and contain the exact validated proposal and source MID.
        for path in (Path(self.c['state_dir'])/'reports').glob('*.json'):
            try:
                r = json.loads(path.read_text(encoding='utf-8'))
                if r.get('status')=='drafted' and r.get('account_key')==self.account() and r.get('incoming_message_id') and r.get('draft_body'):
                    found.append(r)
            except (ValueError, OSError):
                continue
        return found

    def ingest(self, sent, matters, proposals):
        own = {x.lower() for x in self.c['mail']['own_addresses']}
        matter, recipients = scope(sent, matters, own)
        if not matter:
            return 'scope_excluded'
        text = authored(sent.text)
        key = digest(self.account()+sent.mid)
        if self.db.execute('SELECT 1 FROM forgotten WHERE key=?',(key,)).fetchone():
            return 'forgotten'
        if self.db.execute('SELECT 1 FROM examples_v2 WHERE key=?',(key,)).fetchone():
            return 'already_stored'
        direct = ids(sent.msg.get('In-Reply-To',''))
        matches=[];linked=[]
        for r in proposals:
            if r.get('matter_path') != matter['path'] or r.get('matter') != matter['id']:
                continue
            if sent.timestamp < datetime.fromisoformat(r['started_at']):
                continue
            if sent.mid == r.get('draft_message_id') or direct == [r['incoming_message_id']]:
                try:
                    old_roles={x[1] for x in json.loads(r.get('memory_audience',''))}
                    new_roles={x[1] for x in json.loads(audience(matter,recipients))}
                except (ValueError,TypeError):old_roles=new_roles=set()
                if not old_roles or old_roles!=new_roles:continue
                linked.append(r)
                if (r.get('memory_audience')==audience(matter,recipients) and
                    set(r.get('reply_recipients',[]))==set(recipients)):matches.append(r)
        draft = ''
        provenance = 'sent_example'
        stats = {}
        if len(matches)==1:
            draft = matches[0]['draft_body']
            if len(draft)<=7000:
                stats = change_stats(draft, text)
                provenance = 'matched_draft_and_sent'
            else:
                draft = ''
        elif len(matches)>1:
            provenance = 'sent_example_ambiguous_draft_link'
        elif len(linked)==1:
            before=set(linked[0].get('reply_recipients',[]));after=set(recipients)
            stats={'recipients_added':sorted(after-before),'recipients_removed':sorted(before-after),
                   'comparison':'Texte du brouillon non conservé car les destinataires ont changé.'}
            provenance='matched_recipient_change'
        elif len(linked)>1:provenance='sent_example_ambiguous_draft_link'
        self.db.execute('INSERT INTO examples_v2 VALUES (?,?,?,?,?,?,?,?,?,?)',
                        (key, matter['id']+'@'+digest(matter['path']), json.dumps(recipients), sent.timestamp.isoformat(),
                         text, draft, json.dumps(stats), provenance, self.account(), audience(matter, recipients)))
        self.db.commit()
        if provenance=='matched_draft_and_sent' and draft:
            from . import learning480
            triage=matches[0].get('triage') if isinstance(matches[0].get('triage'),dict) else {}
            learning480.record_match(self.c,key,sent.timestamp.isoformat(),audience(matter,recipients),
                                     str(triage.get('intent','')),draft,text)
        # Sent correspondence is also searchable in the same matter-only RAG.
        from .index import DocumentIndex
        from .documents import extract
        index=DocumentIndex(self.c['state_dir'],self.c.get('rag'),self.c.get('ollama'))
        base='imap://'+self.account()+'/sent/'+digest(sent.mid)
        index.put_source(matter,base,sent.text,digest(sent.mid+'|'+sent.text),sent.timestamp.isoformat(),
                         'email_sent',{'sender':sent.sender,'subject':sent.subject,
                         'message_id':sent.mid,'confidentiality':'matter'})
        for number,part in enumerate(sent.msg.iter_attachments()):
            if number>=6:break
            name=part.get_filename() or 'piece_sans_extension';raw=part.get_payload(decode=True)
            try:
                if not isinstance(raw,bytes):raise Stop('piece_jointe_non_lisible')
                content=extract(raw,name,self.c['documents'])
                index.put_source(matter,base+'/'+digest(name)[:12]+'-'+name,content,digest(raw),
                    sent.timestamp.isoformat(),'attachment_sent',{'sender':sent.sender,
                    'subject':sent.subject,'filename':name,'confidentiality':'matter'})
            except Stop:continue
        index.ensure_embeddings(matter)
        return provenance

    def collect(self, box, matters):
        days = int(self.cfg.get('retention_days',90))
        cutoff = datetime.now(timezone.utc)-timedelta(days=days)
        self.db.execute('DELETE FROM examples_v2 WHERE sent_at<?',(cutoff.isoformat(),))
        if self.db.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='examples'").fetchone():
            self.db.execute('DELETE FROM examples WHERE sent_at<?', (cutoff.isoformat(),))
        self.db.commit()
        folder = self.c['mail']['sent']
        uids = box.search(folder, 'UNDELETED', 'SINCE', cutoff.strftime('%d-%b-%Y'))
        validity = box.validity
        epoch = digest(json.dumps([POLICY_VERSION, matters, self.c['mail']['own_addresses']], sort_keys=True))
        prefix = '|'.join([self.account(),folder,validity,epoch])
        proposals = self.proposals()
        counts = {'sent_candidates':len(uids), 'examined':0, 'stored':0, 'matched':0, 'skipped':0, 'errors':0, 'reasons':{}}
        for uid in reversed(uids):
            scan_key = digest(prefix+'|'+uid)
            if self.db.execute('SELECT 1 FROM seen WHERE key=?',(scan_key,)).fetchone():
                continue
            if counts['examined']>=self.cfg.get('max_messages_per_run',30):
                break
            counts['examined']+=1
            try:
                head = box.fetch(folder,uid,headers_only=True)
                matter, _, reason = scope_detail(head, matters, {x.lower() for x in self.c['mail']['own_addresses']})
                outcome = reason
                if matter:
                    sent = box.fetch(folder,uid)
                    outcome = self.ingest(sent, matters, proposals)
                counts['reasons'][outcome] = counts['reasons'].get(outcome, 0) + 1
                if outcome in ('sent_example','matched_draft_and_sent','matched_recipient_change','sent_example_ambiguous_draft_link'):
                    counts['stored']+=1
                    try:
                        from .desk import Desk
                        Desk(self.c).enqueue('memory_insight',{'key':digest(self.account()+sent.mid)})
                    except (Stop,OSError):pass
                else:
                    counts['skipped']+=1
                if outcome in ('matched_draft_and_sent','matched_recipient_change'): counts['matched']+=1
            except Stop as e:
                counts['errors']+=1
                reason = 'texte_trop_court_ou_long' if str(e)=='exemple_envoye_trop_court_ou_long' else 'lecture_impossible'
                counts['reasons'][reason] = counts['reasons'].get(reason, 0) + 1
                if str(e)=='exemple_envoye_trop_court_ou_long':
                    self.db.execute('INSERT OR IGNORE INTO seen VALUES (?)',(scan_key,))
                    self.db.commit()
                continue  # Network or extraction failure can be retried next run.
            self.db.execute('INSERT OR IGNORE INTO seen VALUES (?)',(scan_key,))
            self.db.commit()
        self.db.execute('INSERT OR REPLACE INTO memory_meta VALUES (?,?)',
                        ('last_collect:'+self.account(), json.dumps({'at':datetime.now(timezone.utc).isoformat(), **counts})))
        self.db.commit()
        return counts

    def examples(self, matter, recipients):
        if not matter:
            return []
        own_targets = set(recipients)
        binding = audience(matter, own_targets)
        if not binding:
            return []
        cutoff = (datetime.now(timezone.utc)-timedelta(days=int(self.cfg.get('retention_days',90)))).isoformat()
        rows = self.db.execute('SELECT key,recipients,sent_at,body,draft,stats,provenance,audience FROM examples_v2 WHERE matter=? AND account=? AND sent_at>=? ORDER BY sent_at DESC LIMIT 100',
                               (matter['id']+'@'+digest(matter['path']), self.account(), cutoff))
        result = []
        for key, targets, stamp, body, draft, stats, provenance, saved_binding in rows:
            # A private reply to one client is not reused in a reply to a wider group.
            if set(json.loads(targets)) != own_targets or binding != saved_binding:
                continue
            if self.db.execute('SELECT 1 FROM excluded_examples WHERE key=?',(key,)).fetchone():
                continue
            result.append({'id':'sent-'+key, 'kind':'sent_example', 'sent_at':stamp,
                           'final_sent_text':body, 'earlier_ai_draft':draft,
                           'edit_statistics':json.loads(stats), 'provenance':provenance,
                           'recipient_role':json.loads(saved_binding)[0][1],
                           'usage':'Style et exemple passé dans ce dossier seulement. Ne prouve pas la situation actuelle.'})
            if len(result)>=self.cfg.get('max_examples',2): break
        return result

    def preference_sources(self, matter, recipients):
        """Return only style guidance explicitly accepted by the lawyer."""
        binding=audience(matter,recipients)
        if not binding:return []
        role=json.loads(binding)[0][1];targets=json.dumps(sorted(set(recipients)))
        matter_key=matter['id']+'@'+digest(matter['path'])
        rows=self.db.execute('''SELECT id,scope,text,created FROM preferences
          WHERE account=? AND status='accepted' AND
          (scope='general' OR (scope='role' AND role=?) OR
           (scope='matter' AND matter=?) OR
           (scope='recipient' AND matter=? AND recipients=?))
          ORDER BY created DESC LIMIT 8''',(self.account(),role,matter_key,matter_key,targets))
        result=[];seen=set()
        for row in rows:
            if row[1] in seen:continue
            seen.add(row[1]);result.append({'id':'preference-'+row[0], 'kind':'sent_preference',
                'scope':row[1], 'guidance':row[2],
                'usage':'Préférence confirmée récente ; ne prouve aucun fait du dossier.'})
        return result

    def insight(self,key):
        if not re.fullmatch('[0-9a-f]{64}',key):raise Stop('cle_invalide')
        row=self.db.execute('''SELECT body,draft,stats,matter,recipients,audience
          FROM examples_v2 WHERE key=? AND account=?''',(key,self.account())).fetchone()
        if not row:raise Stop('exemple_absent')
        from .model import Model,MEMORY_INSIGHT,routed_config,validate
        result=Model(routed_config(self.c,'attachment_review')).ask('memory_insight',{
          'final_sent_text':row[0], 'earlier_ai_draft':row[1],
          'edit_statistics':json.loads(row[2]),
          'scope':'Analyse interne. Aucun fait ou choix du dossier ne doit devenir une règle.'})
        validate(result,MEMORY_INSIGHT)
        data={'result':result,'matter':row[3],'recipients':json.loads(row[4]),
              'audience':row[5],'created_at':datetime.now(timezone.utc).isoformat()}
        self.db.execute('INSERT OR REPLACE INTO example_reviews VALUES (?,?,?)',
                        (key,json.dumps(data,ensure_ascii=False),data['created_at']))
        self.db.commit();return {'analyse_memoire':'disponible','preference_appliquee':False}

    def set_scope(self,key,chosen):
        allowed={'general','role','matter','recipient','exceptional','never'}
        if chosen not in allowed:raise Stop('portee_memoire_invalide')
        if chosen=='never':self.forget(key);return {'exemple':'oublie'}
        review=self.db.execute('SELECT data FROM example_reviews WHERE key=?',(key,)).fetchone()
        row=self.db.execute('''SELECT matter,recipients,audience FROM examples_v2
          WHERE key=? AND account=?''',(key,self.account())).fetchone()
        if not row:raise Stop('exemple_absent')
        if chosen=='exceptional':
            self.db.execute("UPDATE preferences SET status='exceptional' WHERE source_key=?",(key,))
            self.db.commit();return {'exemple':'exceptionnel','utilise_a_l_avenir':False}
        if not review:raise Stop('analyser_avant_de_choisir_la_portee')
        insight=json.loads(review[0])['result']
        values=(insight['style_preferences'] if chosen in ('general','role') else
                insight['style_preferences']+insight['useful_corrections'])
        text='\n'.join('- '+x.strip() for x in values if x.strip())[:4000]
        if not text:raise Stop('aucune_preference_reutilisable')
        role=json.loads(row[2])[0][1];targets=json.dumps(sorted(json.loads(row[1])))
        pid=digest(self.account()+'|'+chosen+'|'+key)
        self.db.execute('''INSERT OR REPLACE INTO preferences VALUES (?,?,?,?,?,?,?,?,?,?)''',
            (pid,self.account(),chosen,row[0] if chosen in ('matter','recipient') else '',
             targets if chosen=='recipient' else '',role if chosen=='role' else '',text,
             'accepted',key,datetime.now(timezone.utc).isoformat()))
        self.db.commit();return {'preference':'appliquee','portee':chosen}

    def manage(self,kind,args):
        if kind=='memory_insight':return self.insight(args.get('key',''))
        if kind=='memory_scope':return self.set_scope(args.get('key',''),args.get('scope',''))
        raise Stop('action_inconnue')

    def apply_feedback(self,example_ids,category):
        stamp=datetime.now(timezone.utc).isoformat()
        if category in ('shorter','wrong_matter','fabricated_fact','wrong_recipient','unnecessary','legal_decision'):
            for value in example_ids:
                key=value[5:] if value.startswith('sent-') else ''
                if re.fullmatch('[0-9a-f]{64}',key):
                    self.db.execute('INSERT OR REPLACE INTO excluded_examples VALUES (?,?,?)',(key,category,stamp))
        if category=='shorter':
            pid=digest(self.account()+'|feedback|shorter')
            self.db.execute('''INSERT OR REPLACE INTO preferences VALUES (?,?,?,?,?,?,?,?,?,?)''',
                (pid,self.account(),'general','','','','- Privilégier une réponse plus courte et supprimer les répétitions.','accepted','feedback',stamp))
        self.db.commit()

    def status(self):
        last = self.db.execute('SELECT value FROM memory_meta WHERE key=?', ('last_collect:'+self.account(),)).fetchone()
        roles = {}
        for binding, count in self.db.execute('SELECT audience, COUNT(*) FROM examples_v2 WHERE account=? GROUP BY audience', (self.account(),)):
            role = json.loads(binding)[0][1]
            roles[role] = roles.get(role, 0) + count
        return {'version': '4.2.0', 'enabled':bool(self.cfg.get('enabled')),
                'examples_by_role':roles, 'last_learning':json.loads(last[0]) if last else None, 'sent_folder':self.c['mail']['sent'],
                'examples':self.db.execute('SELECT COUNT(*) FROM examples_v2 WHERE account=?',(self.account(),)).fetchone()[0],
                'correction_pairs':self.db.execute("SELECT COUNT(*) FROM examples_v2 WHERE account=? AND provenance IN ('matched_draft_and_sent','matched_recipient_change')",(self.account(),)).fetchone()[0],
                'accepted_preferences':self.db.execute("SELECT COUNT(*) FROM preferences WHERE account=? AND status='accepted'",(self.account(),)).fetchone()[0],
                'recent_keys':[r[0] for r in self.db.execute('SELECT key FROM examples_v2 WHERE account=? ORDER BY sent_at DESC LIMIT 10',(self.account(),))]}

    def forget(self, key):
        if not re.fullmatch('[0-9a-f]{64}',key): raise Stop('cle_invalide')
        from . import learning480
        learning480.forget(self.c,key)
        self.db.execute('DELETE FROM examples_v2 WHERE key=?',(key,))
        self.db.execute('DELETE FROM example_reviews WHERE key=?',(key,))
        self.db.execute('DELETE FROM preferences WHERE source_key=?',(key,))
        self.db.execute('DELETE FROM excluded_examples WHERE key=?',(key,))
        if self.db.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='examples'").fetchone():
            self.db.execute('DELETE FROM examples WHERE key=?', (key,))
        self.db.execute('INSERT OR IGNORE INTO forgotten VALUES (?)',(key,))
        self.db.commit()
