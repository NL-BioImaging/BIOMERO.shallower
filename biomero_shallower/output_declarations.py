"""Recognize run-bound label outputs; crate contents never define a deletion list."""
import json
from pathlib import PurePosixPath
from urllib.parse import unquote, urlsplit

from rocrate.model.entity import Entity
from rocrate.rocrate import ROCrate
from rocrate.utils import as_list


def _read_crate(path):
    metadata = json.loads(path.read_text(encoding='utf-8'))
    graph = metadata['@graph']
    # The reader resolves duplicate IDs by replacement; ambiguous evidence must
    # never authorize dropping original pixels.
    if not isinstance(graph, list) or len({entry['@id'] for entry in graph}) != len(graph):
        raise ValueError('Ambiguous RO-Crate entity IDs')
    return ROCrate(metadata)


def _references(entity, key):
    values = as_list(entity.get(key, []))
    if any(not isinstance(value, Entity) for value in values):
        raise ValueError(f'Unresolved RO-Crate reference: {key}')
    return values


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


def references_omitted_files(root, omitted):
    """Keep unsupported crates full instead of leaving dangling file entities.

    Standard label-output declarations reference retained files. A crate that
    inventories arrays being omitted needs a richer storage transformation;
    preserving the full artifact is the conservative supported fallback.
    """
    path = next((path for path in (root.parent / 'ro-crate-metadata.json',
                                  root / 'ro-crate-metadata.json') if path.is_file()), None)
    if path is None:
        return False
    try:
        crate = _read_crate(path)
        omitted_paths = [PurePosixPath(item.relative_to(path.parent).as_posix()) for item in omitted]
        for entry in crate.get_entities():
            for value in (entry.id, entry.get('contentUrl'), entry.get('url')):
                local = _local_path(value) if isinstance(value, str) else None
                if local is not None and any(PurePosixPath(local).is_relative_to(item)
                                             for item in omitted_paths):
                    return True
        return False
    except (OSError, ValueError, KeyError, TypeError, AttributeError):
        # An unreadable crate cannot establish that pruning preserves its links.
        return True


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
        crate = _read_crate(path)
        has_parts = {entity.id for entity in _references(crate.root_dataset, 'hasPart')}
        expected = {f'{root.name}/{label}' if path.parent == root.parent else label
                    for label in label_paths}
        matches = []
        for action in crate.get_entities():
            kinds = action.get('@type', [])
            kinds = [kinds] if isinstance(kinds, str) else kinds
            if 'CreateAction' not in kinds:
                continue
            # A crate that supplies our execution ID must agree with this run.
            if action.get('biomero:workflowId', str(manifest.workflow_id)) != str(manifest.workflow_id):
                continue
            outputs = _references(action, 'result')
            labels = [output for output in outputs if _local_path(output.id) in expected]
            if not labels:
                continue
            if any(output.id not in has_parts or not _references(output, 'exampleOfWork')
                   for output in labels):
                continue
            # A root/image output is derived data, even alongside declared labels.
            if any(_local_path(output.id) in {root.name, '.'} for output in outputs):
                continue
            objects = {_artifact_stem(value.id) for value in _references(action, 'object')
                       if _local_path(value.id) is not None}
            bound = [item for item in candidates if item.transfer_artifact
                     and _artifact_stem(item.transfer_artifact) in objects]
            if len(bound) == 1:
                matches.append(bound[0])
        return matches[0] if len(matches) == 1 else None
    except (OSError, ValueError, KeyError, TypeError, AttributeError):
        return None
