#!/bin/sh
set -eu
python -m pip install --quiet pytest-cov==6.2.1 flake8==7.3.0
python -m pytest tests/unittests/ --cov=biomero_importer --cov-report=term-missing -q
python -m flake8 biomero_importer tests/unittests --count --select=E9,F63,F7,F82 --show-source --statistics
python -m flake8 biomero_importer tests/unittests --count --exit-zero --max-complexity=10 --max-line-length=79 --statistics
