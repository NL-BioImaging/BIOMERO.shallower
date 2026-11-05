"""Output evidence is scoped to a run; it never authorizes dropping other results."""
import json

import pytest

from test_result_zarr import _make_image, _input, _manifest, _identity, IdentityProvider
from test_pixel_identity import make_image
from biomero_shallower.operations import normalize, validate_report
from biomero_shallower.result_zarr import evaluate_returned_zarr
from biomero_shallower.pixel_identity import IsccBioIdentityProvider, read_zarr_semantic_guard


def lazy_input(artifact='input.zarr'):
    item = _input(0, artifact)
    old = item.source.pixel_identity
    return item.model_copy(update={'source': item.source.model_copy(update={
        'pixel_identity': None, 'canonical_pixel_verified': False,
        'image_metadata': {'shape': list(old.shape), 'dtype': old.dtype,
                           'axes': list(old.axes), 'coordinateTransformations': []}})})


def crate(root, manifest, *, input_name='input.tif', wrong_run=False, root_result=False):
    output = f'{root.name}/labels/cells/'
    graph = [
        {'@id': './', '@type': 'Dataset', 'hasPart': [{'@id': output}]},
        {'@id': '#run', '@type': 'CreateAction',
         'object': [{'@id': input_name}],
         'result': [{'@id': output}] + ([{'@id': root.name}] if root_result else []),
         'biomero:workflowId': 'wrong' if wrong_run else str(manifest.workflow_id)},
        {'@id': output, '@type': 'Dataset', 'exampleOfWork': {'@id': '#output'}},
        {'@id': '#output', '@type': 'FormalParameter'},
        {'@id': input_name, '@type': 'File'},
    ]
    (root.parent / 'ro-crate-metadata.json').write_text(json.dumps({'@graph': graph}))


class LabelsOnly(IdentityProvider):
    def generate(self, root, **kwargs):
        assert kwargs['role'] == 'label', 'Source-linked paths must not hash originals'
        return super().generate(root, **kwargs)


def test_ro_crate_without_admin_toggle_skips_both_original_sweeps(tmp_path):
    root = tmp_path / 'result.zarr'
    _make_image(root, labels=('cells',))
    manifest = _manifest(lazy_input())
    crate(root, manifest)
    sidecar = root / 'measurement.csv'
    sidecar.write_text('n,42')
    provider = LabelsOnly(_identity())
    report = normalize(root, manifest, identity_provider=provider)
    assert report.result == 'normalized'
    binding = report.manifest.bindings.images[0]
    assert binding.decision_basis == 'ro-crate-output'
    assert binding.returned_pixel_identity is None
    assert binding.source.pixel_identity is None
    assert not (root / '0').exists()
    assert (root / 'labels/cells/0').is_dir()
    assert sidecar.read_text() == 'n,42'
    assert validate_report(root, manifest) == report


def test_admin_shortcut_without_crate_and_ro_crate_precedence(tmp_path):
    root = tmp_path / 'result.zarr'
    _make_image(root, labels=('cells',))
    manifest = _manifest(lazy_input('result.zarr')).model_copy(update={'original_pixels_not_required': True})
    decision = evaluate_returned_zarr(root, manifest, identity_provider=LabelsOnly(_identity()))
    assert decision.eligible and decision.decision_basis == 'workflow-setting'
    crate(root, manifest, input_name='result.tif')
    decision = evaluate_returned_zarr(root, manifest, identity_provider=LabelsOnly(_identity()))
    assert decision.decision_basis == 'ro-crate-output'


@pytest.mark.parametrize('invalid', ['wrong_run', 'root_result', 'wrong_input'])
def test_unusable_declaration_falls_back_without_dropping_pixels(tmp_path, invalid):
    root = tmp_path / 'result.zarr'
    _make_image(root, labels=('cells',))
    manifest = _manifest(lazy_input())
    crate(root, manifest, wrong_run=invalid == 'wrong_run', root_result=invalid == 'root_result',
          input_name='other.tif' if invalid == 'wrong_input' else 'input.tif')
    result = evaluate_returned_zarr(root, manifest, identity_provider=LabelsOnly(_identity()))
    assert not result.eligible
    assert (root / '0').is_dir()


def test_missing_profile_refreshes_original_backing_and_caches_even_on_mismatch(tmp_path):
    source = tmp_path / '.processed/Image-1.ome.zarr'
    result = tmp_path / 'result.zarr'
    make_image(source)
    returned, _ = make_image(result)
    returned['overview'][0, 0, 0, 0, 0] = 999
    guard = read_zarr_semantic_guard(source, '.')
    item = _input(0)
    stale = item.source.pixel_identity.model_copy(update={
        'shape': guard.shape, 'axes': guard.axes, 'coordinate_transformations': guard.coordinate_transformations,
        'comparison_profile': None})
    item = item.model_copy(update={'source': item.source.model_copy(update={'pixel_identity': stale})})
    provider = IsccBioIdentityProvider()
    calls = []
    generate = provider.generate

    def counted(path, **kwargs):
        calls.append(str(path))
        return generate(path, **kwargs)

    provider.generate = counted
    report = normalize(result, _manifest(item), identity_provider=provider,
                       storage_roots={'group-0-data': tmp_path})
    assert report.result == 'kept-full'
    refreshed = report.refreshed_inputs.inputs[0]
    assert refreshed.source.pixel_identity.is_current_profile
    assert refreshed.source.relative_path == item.source.relative_path
    assert calls.count(str(source)) == 1
    assert source.is_dir() and (result / 's0').is_dir()
    other = tmp_path / 'other.zarr'
    make_image(other)
    calls.clear()
    decision = evaluate_returned_zarr(other, _manifest(refreshed), identity_provider=provider)
    assert decision.eligible
    assert str(source) not in calls


def test_unbound_derived_image_is_retained_even_with_workflow_shortcut(tmp_path):
    root = tmp_path / 'result.zarr'
    _make_image(root, labels=('cells',))
    manifest = _manifest(lazy_input()).model_copy(update={'original_pixels_not_required': True})
    crate(root, manifest, root_result=True)
    decision = evaluate_returned_zarr(root, manifest, identity_provider=LabelsOnly(_identity()))
    assert decision.outcome == 'keep-full'
    assert decision.decision_basis == 'pixel-comparison'


def test_returned_extra_coarser_level_uses_compatible_cached_resolution(tmp_path):
    from test_pixel_identity import generate
    source = tmp_path / 'input.zarr'
    root = tmp_path / 'result.zarr'
    make_image(source)
    returned, data = make_image(root)
    returned.create_array('tiny', data=data[..., ::4, ::4])
    attrs = dict(returned.attrs)
    attrs['multiscales'][0]['datasets'].append({'path': 'tiny', 'coordinateTransformations': [
        {'type': 'scale', 'scale': [1, 1, 1, 4, 4]}]})
    returned.attrs.update(attrs)
    item = _input(0)
    item = item.model_copy(update={'source': item.source.model_copy(update={'pixel_identity': generate(source)})})
    result = evaluate_returned_zarr(root, _manifest(item))
    assert result.eligible
    assert result.image_identities[0].dataset_path == 'overview'


def test_plate_stops_reading_after_no_input_can_match(tmp_path):
    from test_result_zarr import _make_plate, _plate_input, NodeIdentityProvider
    root = tmp_path / 'plate.zarr'
    _make_plate(root)
    provider = NodeIdentityProvider({'A/1/0': _identity('ISCC:CHANGED', 'A/1/0')})
    result = evaluate_returned_zarr(root, _manifest(_plate_input()), identity_provider=provider)
    assert not result.eligible
    assert len(provider.calls) == 1
