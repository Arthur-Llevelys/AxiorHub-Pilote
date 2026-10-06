"""Révision longue par fragments complets, résultats mis en cache et reprise bornée.

Une couverture des caractères n'est pas une garantie de justesse juridique : le
projet assemblé exige une relecture de cohérence. Aucune page Word n'est inventée.
"""
import hashlib
import json
from .common import Stop


def _sha(value):
    return hashlib.sha256(str(value).encode()).hexdigest()


def compact_context(desk, ctx, request, matter_id, model):
    raw=json.dumps(ctx,ensure_ascii=False,sort_keys=True)
    if len(raw)<=48000:return ctx
    from .long_documents365 import analyze_pages
    pages=[{'page':1,'label':'contexte logique, pas une page physique','text':raw,
            'extraction':'structured_context','citation_kind':'logical_section'}]
    items,coverage=analyze_pages(desk,pages,'context567-'+matter_id,'Contexte du dossier',request,model,68000,purpose='document_drafting')
    return {'dossier':ctx.get('dossier',''),'client':ctx.get('client',''),
            'syntheses_progressives':items,'couverture':coverage,
            'limites':'Le contexte entier a été analysé en fragments puis synthétisé. Les synthèses peuvent perdre des détails ; revenir aux sources avant validation.'}


def revise(desk, payload, previous, model, system, schema):
    from .long_documents365 import chunk_pages
    from .live430 import progress
    if len(previous)>2_000_000:raise Stop('revision_depasse_deux_millions_caracteres')
    chunks=chunk_pages([{'page':1,'label':'document Word, section logique','text':previous,
                       'extraction':'word','citation_kind':'logical_section'}],5200)
    if ''.join(c['excerpt'] for c in chunks)!=previous:raise Stop('revision_couverture_non_complete')
    desk.db.execute('''CREATE TABLE IF NOT EXISTS composition_cache_v567(
        fingerprint TEXT PRIMARY KEY, part INTEGER NOT NULL, source_sha256 TEXT NOT NULL,
        response TEXT NOT NULL, at TEXT NOT NULL)''');desk.db.commit()
    fingerprint=_sha(json.dumps({'payload':payload,'pipeline':'567.1','model':getattr(model,'cfg',{}),
                                'source_sha256':_sha(previous),'system':system},ensure_ascii=False,sort_keys=True,default=str))
    outputs=[];cached=0
    for n,chunk in enumerate(chunks,1):
        key=_sha(fingerprint+'|'+str(n))
        old=desk.db.execute('SELECT response,source_sha256 FROM composition_cache_v567 WHERE fingerprint=?',(key,)).fetchone()
        if old and old['source_sha256']==chunk['sha256']:
            out=json.loads(old['response']);cached+=1
        else:
            part={**payload,'document_actuel':chunk['excerpt'], 'revision_fragment':{
                'numero':n,'total':len(chunks),'character_start':chunk['char_start'],'character_end':chunk['char_end'],
                'citation_kind':'logical_section','document_sha256':_sha(previous)}}
            progress(desk,'Révision de la section %d/%d : tous les fragments seront assemblés.'%(n,len(chunks)),payload['matter'])
            instruction=('Révise intégralement CETTE section selon la demande. Conserve tout passage non visé, tous les chiffres, citations et réserves. '
                         'N’ajoute pas une introduction, un titre général ou une conclusion à chaque fragment. N’imite pas une lecture des autres sections. '
                         'Les changements affectant le plan global sont à signaler dans a_completer pour relecture. '
                         'Réponds en JSON : titre, nom_fichier, paragraphes, sources, a_completer.\n\n')
            out=json.loads(model.complete([{'role':'system','content':system},{'role':'user','content':instruction+json.dumps(part,ensure_ascii=False)}],
                                          temperature=0,max_tokens=7000,json_schema=schema))
            if not out.get('paragraphes') or any(not isinstance(p,dict) or not str(p.get('texte','')).strip() for p in out['paragraphes']):
                raise Stop('revision_fragment_vide')
            desk.db.execute('INSERT OR REPLACE INTO composition_cache_v567 VALUES(?,?,?,?,?)',
                            (key,n,chunk['sha256'],json.dumps(out,ensure_ascii=False),desk.now()));desk.db.commit()
        outputs.append(out)
    paragraphs=[p for out in outputs for p in out.get('paragraphes',[])]
    if not paragraphs or len(paragraphs)>5000:raise Stop('revision_resultat_depasse_paragraphes')
    incomplete=list(dict.fromkeys(str(s) for o in outputs for s in o.get('a_completer',[])))
    incomplete.append('Révision assemblée par sections : relire le plan, les renvois et la cohérence globale avant utilisation.')
    return {'titre':str(outputs[0].get('titre',''))[:200], 'nom_fichier':str(outputs[0].get('nom_fichier','Projet révisé'))[:120],
            'paragraphes':paragraphs,'sources':list(dict.fromkeys(str(s) for o in outputs for s in o.get('sources',[]))),
            'a_completer':incomplete,'revision_coverage':{'complete':True,'characters':len(previous),
                  'parts':len(chunks),'parts_from_cache':cached,'source_sha256':_sha(previous),
                  'semantic_verification':'relecture_avocat_requise','citation_kind':'logical_section'}}


def compact_sources(desk,sources,question,model,limit):
    """Rassembler les sources indispensables sans tronquer ou supprimer un document sélectionné."""
    from .context import payload_size
    from .long_context363 import _ask
    mandatory=[s for s in sources if s.get('kind')=='piece_jointe_locale']
    optional=[s for s in sources if s.get('kind')!='piece_jointe_locale']
    desk.db.execute('''CREATE TABLE IF NOT EXISTS composition_cache_v567(
        fingerprint TEXT PRIMARY KEY, part INTEGER NOT NULL, source_sha256 TEXT NOT NULL,
        response TEXT NOT NULL, at TEXT NOT NULL)''');desk.db.commit()
    level=0
    while payload_size({'sources':mandatory})>max(6000,limit-24000) and len(mandatory)>1:
        level+=1;merged=[]
        for n,start in enumerate(range(0,len(mandatory),5),1):
            group=mandatory[start:start+5]
            fingerprint=_sha(json.dumps([question,group,getattr(model,'cfg',{}),'source-merge567.1'],ensure_ascii=False,sort_keys=True,default=str))
            old=desk.db.execute('SELECT response FROM composition_cache_v567 WHERE fingerprint=?',(fingerprint,)).fetchone()
            if old:answer=json.loads(old['response'])['answer']
            else:
                answer=_ask(model,question,group,limit,'synthèse de sources sélectionnées')
                desk.db.execute('INSERT INTO composition_cache_v567 VALUES(?,?,?,?,?)',(fingerprint,n,fingerprint,json.dumps({'answer':answer}),desk.now()));desk.db.commit()
            merged.append({'id':'selected567-'+fingerprint[:16], 'kind':'piece_jointe_locale',
                           'path':'Synthèse de documents sélectionnés', 'excerpt':answer, 'partial':False,
                           'derived_from':list(dict.fromkeys(sid for x in group for sid in x.get('derived_from',[x['id']]))),
                           'source_paths':list(dict.fromkeys(path for x in group for path in x.get('source_paths',[x.get('path','')])) )})
        if len(merged)>=len(mandatory):raise Stop('synthese_sources_non_convergente')
        mandatory=merged
    return mandatory+optional
