#!/usr/bin/env python3
"""Diagnostic sans secret du apprentissage métier et du routage hybride AxiorHub 4.1.0.

Le diagnostic ne contacte aucun fournisseur et n'affiche ni identifiant de dossier,
ni prompt, ni réponse, ni secret. Il peut donc être joint à un rapport de support.
"""
import json
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from agent.ai_gateway import public_providers, usage_snapshot
from agent.common import load_config
from agent.desk import Desk
from agent.hybrid400 import snapshot
from agent.learning410 import snapshot as learning_snapshot
from agent.evaluation410 import dashboard as evaluation_dashboard


def main():
    config_path = Path('/etc/axiorhub-mail-agent/config.json')
    try:
        desk = Desk(load_config(config_path))
        routing = snapshot(desk, 30)
        learning = learning_snapshot(desk, 30)
        evaluation = evaluation_dashboard(desk)
        rule = dict(routing['policy'])
        rule['excluded_matter_count'] = len(rule.pop('excluded_matters', []))
        reasons = {}
        statuses = {}
        for row in routing['decisions']:
            statuses[row['status']] = statuses.get(row['status'], 0) + 1
            for reason in row['reason_codes']:
                reasons[reason] = reasons.get(reason, 0) + 1
        report = {
            'version': '4.1.0',
            'policy': rule,
            'providers': public_providers(desk.c),
            'usage_current_month': usage_snapshot(desk),
            'decisions_last_30_days': routing['summary'],
            'decision_statuses': statuses,
            'routing_reasons': reasons,
            'business_learning': {
                'active_rules': learning['active_rules'],
                'corrections_30d': learning['correction_volume'],
                'changed_characters_30d': learning['changed_characters'],
                'trusted_documents': len(learning['trusted_documents']),
                'rule_uses_30d': learning['rule_uses'],
            },
            'legal_benchmark': {
                'anonymized_cases': len(evaluation['cases']),
                'measured_models': len(evaluation['leaderboard']),
                'runs': len(evaluation['runs']),
            },
            'content_logged': routing['content_logged'],
            'secrets_exposed': routing['secrets_exposed'],
            'network_call_performed': False,
        }
        print(json.dumps(report, ensure_ascii=False, indent=2))
    except PermissionError:
        print(json.dumps({
            'version': '4.1.0',
            'status': 'error',
            'error': 'permission_refusee',
            'command': ('sudo /usr/bin/python3 '
                        '/opt/axiorhub-mail-agent/current/diagnostic-v410.py'),
        }, ensure_ascii=False, indent=2))
        raise SystemExit(2)


if __name__ == '__main__':
    main()
