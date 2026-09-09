"""Exercise the actual submission ledger shell with a tiny fake scheduler."""
import importlib.util
import os
from pathlib import Path
import subprocess
from types import SimpleNamespace

spec = importlib.util.spec_from_file_location('normalizer', '/core/biomero/result_normalizer.py')
normalizer = importlib.util.module_from_spec(spec)
spec.loader.exec_module(normalizer)
root = Path('/fixture')
root.mkdir(exist_ok=True)
bin_dir = root / 'bin'
bin_dir.mkdir()
(bin_dir / 'sbatch').write_text('#!/bin/sh\necho submitted >> /fixture/submissions\necho 123\n')
(bin_dir / 'sacct').write_text('#!/bin/sh\necho 123\n')
for path in bin_dir.iterdir():
    path.chmod(0o755)
os.environ['PATH'] = str(bin_dir) + ':' + os.environ['PATH']


def execute(commands):
    process = subprocess.run(commands[0], shell=True, capture_output=True, text=True)
    return SimpleNamespace(ok=process.returncode == 0, stdout=process.stdout)


client = SimpleNamespace(run_commands=execute)
command = 'sbatch --parsable --job-name=biomero-normalizer-smoke --wrap=true'
state = '/fixture/normalize'
assert normalizer._submit_once(client, command, state) == 123
assert normalizer._submit_once(client, command, state) == 123
Path(state + '.job').unlink()
assert normalizer._submit_once(client, command, state) == 123
assert (root / 'submissions').read_text().splitlines() == ['submitted']
(bin_dir / 'sbatch').write_text('#!/bin/sh\nexit 1\n')
assert normalizer._submit_once(client, command, '/fixture/rejected') is None
assert not Path('/fixture/rejected.intent').exists()
print('Submission ledger: initial submit, job adoption, intent reconciliation, and rejected submission passed')
