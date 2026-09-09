# Benchmark protocol

The supplied 846-image / 154,198-file baseline is:

| Stage | Baseline |
|---|---:|
| Returned bytes | 8,968,870,301 |
| Deflate ZIP bytes | 7,696,668,188 |
| ZIP creation | 132.3 s |
| SCP | approximately 321.1 s |
| Worker copy and ZIP extraction | 1 h 11 m 40 s |
| Local identity evaluation | 42 m 55 s |
| Local normalization | 22 m 33 s |
| Existing shallow retained storage | approximately 7–10% |
| Full Zstandard level 3 archive | 7,686,925,055 bytes / 26.4 s |
| Full tar.zst extraction | 39 m 16.8 s |

These are supplied measurements, not new benchmark results. The acceptance
target is to transfer already-normalized output and eliminate local duplicate
pixel hashing/normalization while preserving registration and scientific content.

Use a disposable returned-result copy; normalization intentionally removes
verified duplicate arrays. Never benchmark against canonical storage.

```sh
/usr/bin/time -v biomero-shallower normalize \
  --returned-zarr /benchmark/result.zarr \
  --canonical-inputs /benchmark/input.json --contract-version 1 \
  --identity-workers 4 --failure-policy keep-full \
  --report /benchmark/report.json --benchmark-bytes
du -sb /benchmark/result.zarr
/usr/bin/time -v 7z a -tzip /benchmark/shallow.zip /benchmark/result.zarr
stat -c %s /benchmark/shallow.zip
```

Record remote report identity/normalization timings, archive bytes/time, SCP,
extraction, and importer order timestamps separately. Compare pixel identities,
retained labels, OMERO object counts, hierarchy, metadata, and registration
targets. Production runs omit recursive byte measurements. The small mounted
smoke test is a correctness check and cannot predict full-screen throughput.
