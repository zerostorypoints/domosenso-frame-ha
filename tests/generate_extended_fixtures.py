"""Generate fictional extended-route fixtures for available and unavailable sources."""
import json
from pathlib import Path
from test_assistant_extended import response_fixtures

for name, unavailable in [('assistant-extended.json', False), ('assistant-extended-unavailable.json', True)]:
    Path('tests/fixtures', name).write_text(json.dumps(response_fixtures(unavailable), ensure_ascii=False, indent=2) + '\n')
