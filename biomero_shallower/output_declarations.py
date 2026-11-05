"""Recognize run-bound label outputs; crate contents never define a deletion list."""
import json
from pathlib import PurePosixPath
from urllib.parse import unquote, urlsplit


def _ids(value):
    values = value if isinstance(value, list) else [value]
    return [item.get('@id') for item in values if isinstance(item, dict)
            and isinstance(item.get('@id'), str)]


def _local_path(value):
    parsed = urlsplit(value)
    if parsed.scheme or parsed.netloc or parsed.query or parsed.fragment:
        return None
    path = PurePosixPath(unquote(parsed.path))
    if path.is_absolute() or '..' in path.parts or '\\' in str(path):
        return None
    return path.as_posix().rstrip('/')


def _artifact_stem(value):
    # BIOMERO conversion preserves the input basename while changing format.
    name = PurePosixPath(value).name
    for suffix in ('.ome.zarr', '.ome.tiff', '.ome.tif', '.zarr', '.tiff', '.tif'):
        if name.lower().endswith(suffix):
            return name[:-len(suffix)]
    return name


def same_input_artifact(left, right):
    return bool(left and right and _artifact_stem(left) == _artifact_stem(right))


def declared_label_input(root, manifest, candidates, label_paths):
    """Return one bound input only for explicit, consistent label-level outputs.

    Root-level output declarations or an ambiguous input/action are insufficient.
    Other results and sidecars are outside this decision and always retained.
    """
    paths = (root.parent / 'ro-crate-metadata.json', root / 'ro-crate-metadata.json')
    path = next((path for path in paths if path.is_file()), None)
    if path is None:
        return None
    try:
        graph = json.loads(path.read_text(encoding='utf-8'))['@graph']
        if not isinstance(graph, list):
            return None
        entities = {entry['@id']: entry for entry in graph if isinstance(entry, dict)}
        if len(entities) != len(graph):
            return None
        crate = entities.get('./', entities.get('.', {}))
        has_parts = set(_ids(crate.get('hasPart')))
        expected = {f'{root.name}/{label}' if path.parent == root.parent else label
                    for label in label_paths}
        matches = []
        for action in graph:
            kinds = action.get('@type', [])
            kinds = [kinds] if isinstance(kinds, str) else kinds
            if 'CreateAction' not in kinds:
                continue
            # A crate that supplies our execution ID must agree with this run.
            if action.get('biomero:workflowId', str(manifest.workflow_id)) != str(manifest.workflow_id):
                continue
            outputs = _ids(action.get('result'))
            labels = [output for output in outputs if _local_path(output) in expected]
            if not labels:
                continue
            parameters = [parameter for output in labels
                          for parameter in _ids(entities.get(output, {}).get('exampleOfWork'))]
            if (any(output not in has_parts or not _ids(entities.get(output, {}).get('exampleOfWork'))
                    for output in labels)
                    or any(parameter not in entities for parameter in parameters)):
                continue
            # A root/image output is derived data, even alongside declared labels.
            if any(_local_path(output) in {root.name, '.'} for output in outputs):
                continue
            objects = {_artifact_stem(value) for value in _ids(action.get('object'))
                       if _local_path(value) is not None}
            bound = [item for item in candidates if item.transfer_artifact
                     and _artifact_stem(item.transfer_artifact) in objects]
            if len(bound) == 1:
                matches.append(bound[0])
        return matches[0] if len(matches) == 1 else None
    except (OSError, ValueError, KeyError, TypeError):
        return None
