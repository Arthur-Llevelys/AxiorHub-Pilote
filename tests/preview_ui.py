"""Local visual test with fictitious data only; never used by the installed service."""
import json
from pathlib import Path
import sys
from wsgiref.simple_server import make_server

sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from test_desk import WebTests
import test_agent as fixtures
from agent.common import private_json
from agent.desk import Desk, now
from agent.memory import SentMemory

f=WebTests();f.setUp()
auth=json.loads(f.auth.read_text());auth['origin']='http://127.0.0.1:8871';private_json(f.auth,auth)
f.f.model.intent='legal'
key=f.f.engine.process(fixtures.mail(subject='Dossier DEMO — Proposition de règlement'))
d=Desk(f.f.c)
d.propose(f.f.matters[0],'conseil@example.test',{'type':'courriel','source':'DOS-001 — Proposition de règlement',
    'date':now(),'indice':'Objet contenant : DOS-001','limite':'Présence dans un échange ; rôle à confirmer.'})
d.db.execute('INSERT INTO notes VALUES (?,?,?)',(key,json.dumps({'result':{'summary':'Le client demande votre position sur une proposition de règlement.',
    'decisions':['Déterminer les instructions à demander avant toute réponse au conseil adverse.'],
    'questions':['Le client souhaite-t-il poursuivre les échanges amiables ?'],
    'partial_reply':'Cher Monsieur,\n\nPouvez-vous préciser les conditions que vous souhaitez voir retenues dans la discussion ?',
    'limits':['La pièce annoncée n’a pas été analysée.'],'source_ids':['incoming']},
    'created_at':now(),'scope':'Courriel uniquement ; aucune pièce jointe ni recherche documentaire.',
    'warning':'Projet interne non validé. Relire avant toute utilisation.'}),now()))
d.db.commit();d.enqueue('sync')
print('Preview fictive http://127.0.0.1:8871/agent-courriel/ — key '+key,flush=True)
try:
    make_server('127.0.0.1',8871,f.app).serve_forever()
finally:f.doCleanups()
