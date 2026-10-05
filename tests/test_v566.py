"""5.6.6 : l'état du système ne se recharge plus en boucle et ne signale plus de faux incidents (jeton Invoice Ninja d'une intégration
désactivée, Ollama lancé hors de ollama.service, dossier de brouillons supprimé présenté comme « dossier illisible »)."""
from datetime import datetime, timezone
import json
from pathlib import Path
import unittest
from unittest.mock import patch

from agent import reliability393, reliability393_ui
from agent.common import Stop
import test_v530 as t530

ROOT = Path(__file__).resolve().parents[1]


class Reload(unittest.TestCase):
    def test_page_reloads_only_after_the_lawyer_s_own_submission(self):
        js = (ROOT / 'agent' / 'static' / 'v430.js').read_text(encoding='utf-8')
        self.assertIn("if(fresh===true&&form.querySelector('[name=\"back\"]'))setTimeout(()=>window.location.reload(),900);", js)
        self.assertIn("data.url,true);}", js)                                  # seul l'envoi de l'avocat est « frais »
        self.assertIn("const job=byId.get(item.job_id);if(!job)continue;", js)  # ancienne demande d'un bouton : rien à rejouer
        self.assertEqual(js.count('window.location.reload()'), 1)


class SystemState(t530.Base):
    def test_disabled_invoice_ninja_token_is_optional(self):
        self.desk.c['invoice_ninja'] = {'enabled': False, 'api_token_file': '/nonexistent/invoice-ninja-api-token'}
        rows = {r['path'].replace(chr(92), '/'): r for r in reliability393.permission_health(self.desk)['files']}
        self.assertEqual(rows['/nonexistent/invoice-ninja-api-token']['status'], 'optional')
        self.desk.c['invoice_ninja']['enabled'] = True
        rows = {r['path'].replace(chr(92), '/'): r for r in reliability393.permission_health(self.desk)['files']}
        self.assertEqual(rows['/nonexistent/invoice-ninja-api-token']['status'], 'error')

    def test_ollama_running_outside_systemd_is_not_an_alert(self):
        class Result:
            def __init__(self, out): self.stdout, self.stderr, self.returncode = out, '', 0
        runner = lambda cmd, **k: Result('inactive\n' if cmd[-1] == 'ollama.service' else 'active\n')
        with patch('agent.reliability393._ollama_answers', return_value=True):
            report = reliability393.service_health(self.desk, runner=runner)
        self.assertEqual(report['status'], 'verified')
        with patch('agent.reliability393._ollama_answers', return_value=False):
            report = reliability393.service_health(self.desk, runner=runner)
        self.assertEqual(report['status'], 'warning')                         # réellement arrêté : signalé

    def test_deleted_drafts_folder_is_missing_files_not_unreadable_folder(self):
        stamp = datetime.now(timezone.utc).isoformat()
        paths = ['/Dossiers/DEMO/90_AxiorHub_Brouillons/Courrier.docx', '/Dossiers/DEMO/90_AxiorHub_Brouillons/Rapport.pdf']
        for i in range(2):                                                      # deux productions déclarant les mêmes fichiers
            self.desk.db.execute('INSERT INTO production_outputs_v390 VALUES(?,?,?,?,?,?,?,?,?,?,?,?)',
                                 ('out%d' % i, i + 1, 'create_document_files', 'document_files', 'DOS-001', 'source', 'delivered', 'Courrier',
                                  json.dumps(paths), '{}', stamp, stamp))
        self.desk.db.commit()

        class Gone:
            def __init__(self, c): pass
            def list_folder(self, parent): raise Stop('http_404')
        with patch('agent.reliability393.DAV', Gone):
            report = reliability393.verify_nextcloud_outputs(self.desk)
        self.assertEqual(report['status'], 'warning')
        self.assertEqual(report['evidence']['errors'] if 'evidence' in report else [], [])
        html = reliability393_ui._detail(self.desk, 'nextcloud_outputs', report, {})
        self.assertNotIn('dossier illisible', html)
        self.assertNotIn('Erreur Nextcloud', html)
        self.assertEqual(html.count('Courrier.docx'), 1)                       # une ligne par fichier, plus de doublons

        class Broken(Gone):
            def list_folder(self, parent): raise Stop('http_503')
        with patch('agent.reliability393.DAV', Broken):
            report = reliability393.verify_nextcloud_outputs(self.desk)
        self.assertEqual(report['status'], 'error')
        self.assertIn('lecture Nextcloud impossible', reliability393_ui._detail(self.desk, 'nextcloud_outputs', report, {}))


if __name__ == '__main__':
    unittest.main()
