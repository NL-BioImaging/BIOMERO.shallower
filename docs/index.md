# BIOMERO.shallower

BIOMERO.shallower is the shared filesystem-only result normalizer in the
BIOMERO 2.0 ecosystem. It compares returned OME-Zarr image and label identities
with the exact canonical inputs, references verified duplicate pixels, and
retains new or changed arrays. It requires no OMERO connection or credentials.

The initial adapter supports NGFF 0.4 / Zarr v2 Images and Plates. An unchanged
image may be shallow even without labels. Deduplication never suppresses
registration of the result in OMERO.

## Integration

The importer uses this package for local normalization. For remote
normalization, BIOMERO core runs its CPU-only image on Slurm before result
archiving and transfer. The workflow scripts forward trusted receipts, allowing
the importer to validate the completed work without hashing omitted pixels
again.

Shallow storage is optional; installing this package does not enable it. When
shallow storage is enabled, remote normalization is preferred unless the
administrator selects the local path. For deployment, feature flags, and image
acquisition, use the
[NL-BIOMERO administration guide](https://nl-bioimaging.github.io/NL-BIOMERO/master/sysadmin/remote-shallower.html).

## Guides

- [Installation and commands](commands.md): package extras, container use, and
  result verification.
- [Architecture and recovery](architecture.md): transaction durability,
  canonical references, and the receipt trust boundary.
- [Benchmarks](benchmarks.md): measuring stages without modifying canonical
  storage.
- [Releases and publication](releases.md): package and image publishing and
  repository configuration.
- [Python API](api.md): normalization and report validation interfaces.

Report formats are defined by
[BIOMERO Schema](https://nl-bioimaging.github.io/biomero-schema/remote-shallower-contracts/).
The package is licensed under Apache-2.0; included dependencies retain their
own licences and notices.
