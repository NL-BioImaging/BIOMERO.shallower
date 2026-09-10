"""Export only the installed CPU identity dependency closure on Linux."""
from importlib.metadata import distribution
from pathlib import Path
from packaging.requirements import Requirement
from packaging.markers import default_environment

pending = [Requirement('iscc-bio[ome-zarr]'), Requirement('numpy'), Requirement('biomero-schema')]
seen = set()
versions = {}
while pending:
    request = pending.pop()
    key = (request.name.lower(), tuple(sorted(request.extras)))
    if key in seen:
        continue
    seen.add(key)
    package = distribution(request.name)
    versions[package.metadata['Name']] = package.version
    for raw in package.requires or ():
        dependency = Requirement(raw)
        if dependency.marker is None or any(dependency.marker.evaluate(
                dict(default_environment(), extra=extra))
                for extra in ('', *request.extras)):
            pending.append(dependency)
versions.pop('biomero-schema', None)
Path('/shallower/requirements.lock').write_text(
    '# Python 3.12 Linux CPU identity runtime; exact transitive versions.\n' +
    ''.join(f'{name}=={version}\n' for name, version in sorted(versions.items(), key=lambda item: item[0].lower())))
