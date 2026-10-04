"""Bounded local extraction. Child commands are fixed and never supplied by AI."""
from email import policy
from email.parser import BytesParser
import io
import csv
import os
from pathlib import Path
import re
import resource
import subprocess
import tempfile
import time
import zipfile

from .common import Stop, xml_bytes
from .mailbox import body_text

SUPPORTED = {'.pdf', '.docx', '.odt', '.xlsx', '.txt', '.md', '.csv', '.eml', '.png', '.jpg', '.jpeg', '.tif', '.tiff'}


def _pdf_pages(raw, cfg):
    """Extract every PDF page independently so citations cannot drift."""
    started=time.monotonic()
    with tempfile.TemporaryDirectory(prefix='axiorhub-pages-') as td:
        source=Path(td)/'input.pdf';source.write_bytes(raw)
        info=command(['/usr/bin/pdfinfo',str(source)])
        match=re.search(r'^Pages:\s+(\d+)',info,re.M)
        maximum=int(cfg.get('max_pdf_pages_long',cfg.get('max_pdf_pages',60)))
        if not match or int(match[1])>maximum:raise Stop('pdf_trop_long_ou_protege')
        count=int(match[1]);pages=[]
        for number in range(1,count+1):
            if time.monotonic()-started>cfg.get('max_extraction_seconds_long',600):
                raise Stop('temps_extraction_depasse')
            page=command(['/usr/bin/pdftotext','-f',str(number),'-l',str(number),
                          '-layout','-enc','UTF-8',str(source),'-']).replace('\x00','').strip()
            method='text'
            if len(page)<30:
                if not cfg.get('ocr',True):raise Stop('pdf_scan_ocr_necessaire')
                stem=str(Path(td)/('page-'+str(number)))
                command(['/usr/bin/pdftoppm','-f',str(number),'-l',str(number),
                         '-scale-to','1800','-singlefile','-png',str(source),stem])
                page=ocr(stem+'.png').strip();method='ocr'
            if not page:raise Stop('page_pdf_sans_texte_exploitable')
            pages.append({'page':number,'label':'p. '+str(number),'text':page,
                          'extraction':method,'citation_kind':'page'})
        return pages


def extract_pages(raw, name, cfg):
    """Return complete page-aware text for long-document workflows.

    DOCX page numbers are obtained from a local LibreOffice PDF rendering.  If
    rendering is unavailable, the function returns one explicitly labelled
    logical section rather than inventing Word page numbers.
    """
    if len(raw)>cfg.get('max_file_bytes_long',20_000_000):raise Stop('piece_trop_volumineuse')
    ext=Path(name).suffix.lower()
    if ext=='.pdf':return _pdf_pages(raw,cfg)
    if ext=='.docx':
        with tempfile.TemporaryDirectory(prefix='axiorhub-docx-pages-') as td:
            source=Path(td)/'source.docx';source.write_bytes(raw)
            try:
                p=subprocess.run(['/usr/bin/libreoffice','--headless','--convert-to','pdf',
                    '--outdir',td,str(source)],stdout=subprocess.PIPE,stderr=subprocess.DEVNULL,
                    timeout=int(cfg.get('max_extraction_seconds_long',600)),check=False,
                    preexec_fn=limits,env={'PATH':'/usr/bin:/bin','LANG':'C.UTF-8','HOME':td})
            except (FileNotFoundError,subprocess.TimeoutExpired):p=None
            rendered=Path(td)/'source.pdf'
            if p is not None and p.returncode==0 and rendered.is_file():
                return _pdf_pages(rendered.read_bytes(),cfg)
        text=extract(raw,name,{**cfg,'max_document_chars':cfg.get('max_document_chars_long',2_000_000)})
        return [{'page':1,'label':'section logique 1','text':text,
                 'extraction':'docx_xml','citation_kind':'logical_section'}]
    text=extract(raw,name,{**cfg,'max_document_chars':cfg.get('max_document_chars_long',2_000_000)})
    return [{'page':1,'label':'section logique 1','text':text,
             'extraction':'native','citation_kind':'logical_section'}]


def limits():
    resource.setrlimit(resource.RLIMIT_CPU, (90, 90))
    resource.setrlimit(resource.RLIMIT_AS, (1800 * 1024**2, 1800 * 1024**2))
    resource.setrlimit(resource.RLIMIT_FSIZE, (70 * 1024**2, 70 * 1024**2))
    resource.setrlimit(resource.RLIMIT_NOFILE, (96, 96))


def command(args, timeout=90):
    try:
        p = subprocess.run(args, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL,
                           timeout=timeout, check=False, preexec_fn=limits,
                           env={'PATH': '/usr/bin:/bin', 'LANG': 'C.UTF-8', 'OMP_THREAD_LIMIT': '1'})
        if p.returncode: raise Stop('extraction_locale_echouee')
        if len(p.stdout) > 4_000_000: raise Stop('texte_extrait_trop_long')
        return p.stdout.decode('utf-8', 'replace')
    except (FileNotFoundError, subprocess.TimeoutExpired):
        raise Stop('extracteur_absent_ou_delai_depasse') from None


def ocr(path):
    tsv = command(['/usr/bin/tesseract',str(path),'stdout','-l','fra+eng','tsv'])
    words = [r for r in csv.DictReader(io.StringIO(tsv),delimiter='\t')
             if r.get('text','').strip() and float(r.get('conf','-1')) >= 0]
    if not words: raise Stop('ocr_sans_texte_reconnu')
    confidence = sum(float(r['conf'])*len(r['text']) for r in words)/sum(len(r['text']) for r in words)
    if confidence < 70: raise Stop('ocr_qualite_insuffisante')
    lines = {}
    for r in words:
        key = tuple(r[k] for k in ('page_num','block_num','par_num','line_num'))
        lines.setdefault(key,[]).append(r['text'])
    return '[Texte OCR local : vérifier les noms, montants et dates sur l’original]\n'+'\n'.join(' '.join(w) for w in lines.values())


def extract(raw, name, cfg):
    started = time.monotonic()
    if len(raw) > cfg.get('max_file_bytes', 15_000_000): raise Stop('piece_trop_volumineuse')
    ext = Path(name).suffix.lower()
    if ext not in SUPPORTED: raise Stop('format_a_convertir_' + re.sub(r'[^a-z0-9]', '', ext)[:8])
    if ext in {'.txt', '.md', '.csv'}:
        try: text = raw.decode('utf-8-sig')
        except UnicodeDecodeError: text = raw.decode('cp1252', errors='replace')
    elif ext in {'.docx', '.odt', '.xlsx'}:
        try:
            with zipfile.ZipFile(io.BytesIO(raw)) as z:
                if sum(i.file_size for i in z.infolist()) > 60_000_000:
                    raise Stop('archive_decompressee_trop_grosse')
                paths = ['word/document.xml'] if ext == '.docx' else ['content.xml']
                if ext == '.xlsx':
                    paths = sorted(n for n in z.namelist() if re.fullmatch(r'xl/worksheets/sheet\d+\.xml',n))
                    shared=[]
                    if 'xl/sharedStrings.xml' in z.namelist():
                        shared_root=xml_bytes(z.read('xl/sharedStrings.xml'))
                        shared=[''.join(node.itertext()) for node in shared_root if node.tag.rsplit('}',1)[-1]=='si']
                if ext == '.docx':
                    paths += [n for n in ('word/footnotes.xml', 'word/endnotes.xml') if n in z.namelist()]
                out = []
                for p in paths:
                    root = xml_bytes(z.read(p))
                    if ext == '.xlsx':
                        out.append('\n[Feuille '+p.rsplit('/',1)[-1]+']\n')
                        for row in root.iter():
                            if row.tag.rsplit('}',1)[-1]!='row':continue
                            values=[]
                            for cell in row:
                                if cell.tag.rsplit('}',1)[-1]!='c':continue
                                raw_value=next((n.text or '' for n in cell.iter() if n.tag.rsplit('}',1)[-1] in ('v','t')),'')
                                if cell.attrib.get('t')=='s' and raw_value.isdigit() and int(raw_value)<len(shared):raw_value=shared[int(raw_value)]
                                values.append(raw_value)
                            if any(values):out.append('\t'.join(values)+'\n')
                        continue
                    for node in root.iter():
                        tag = node.tag.rsplit('}', 1)[-1]
                        if ext == '.docx' and tag in ('t', 'tab', 'br', 'p'):
                            out.append((node.text or '') if tag == 't' else '\n')
                        elif ext == '.odt' and tag in ('p', 'h'):
                            out.append(''.join(node.itertext()) + '\n')
                text = ''.join(out)
        except (zipfile.BadZipFile, KeyError, RuntimeError):
            raise Stop('document_office_non_lisible') from None
    elif ext == '.eml':
        text = body_text(BytesParser(policy=policy.default).parsebytes(raw))
    else:
        with tempfile.TemporaryDirectory(prefix='axiorhub-extract-') as td:
            source = Path(td) / ('input' + ext)
            source.write_bytes(raw)
            if ext != '.pdf':
                if not cfg.get('ocr', True): raise Stop('ocr_desactive')
                text = ocr(source)
            else:
                info = command(['/usr/bin/pdfinfo', str(source)])
                match = re.search(r'^Pages:\s+(\d+)', info, re.M)
                if not match or int(match[1]) > cfg.get('max_pdf_pages', 60):
                    raise Stop('pdf_trop_long_ou_protege')
                count = int(match[1])
                native = command(['/usr/bin/pdftotext', '-layout', '-enc', 'UTF-8', str(source), '-'])
                pages = native.split('\f')
                out = []
                for i in range(count):
                    if time.monotonic()-started > cfg.get('max_extraction_seconds',120):
                        raise Stop('temps_extraction_depasse')
                    page = pages[i] if i < len(pages) else ''
                    if len(page.strip()) < 30:
                        if not cfg.get('ocr', True): raise Stop('pdf_scan_ocr_necessaire')
                        stem = str(Path(td) / 'page')
                        command(['/usr/bin/pdftoppm', '-f', str(i+1), '-l', str(i+1),
                                 '-scale-to', '1800', '-singlefile', '-png', str(source), stem])
                        page = ocr(stem + '.png')
                    out.append('[Page ' + str(i+1) + ']\n' + page)
                text = '\n'.join(out)
    text = text.replace('\x00', '').strip()
    if not text: raise Stop('document_sans_texte_exploitable')
    if len(text) > cfg.get('max_document_chars', 100000):
        raise Stop('document_trop_long_pour_analyse')
    return text
