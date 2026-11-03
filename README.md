# Nightshift Core

An offline Python library that orders tasks against deadlines, dependencies, and available capacity.

## Download

<p><a href="https://github.com/Exlumina/nightshift-core/releases/download/v0.1.0/nightshift-core_0.1.0.zip"><img src=".github/downloads/download-zip.svg" width="240" height="48" alt="Download Nightshift Core ZIP (v0.1.0)"></a></p>

v0.1.0 · ZIP developer preview<br>
Python 3.10+ with no additional packages.

[v0.1.0 release notes](https://github.com/Exlumina/nightshift-core/releases/tag/v0.1.0) · [SHA-256 checksum](https://github.com/Exlumina/nightshift-core/releases/download/v0.1.0/SHA256SUMS)

This package is a planner library, not the full supervisor. It launches no workers, renderers, models, browser, cloud jobs, trackers or background services. It does not install or activate the internal Nightshift. Its output is a recommendation, not proof of authorization, resource availability, sandboxing or job completion.

## Try a synthetic plan

Extract the ZIP and run these commands from the extracted folder.

```sh
python3 -m nightshift_core examples/tasks.json --remaining-seconds 3600 --now 1000
python3 -m unittest discover -s tests -v
```

Tasks declare status, priority, owner availability, authorization, estimates, target paths, dependencies and required capabilities. The caller supplies fresh capacity measurements. The deterministic result separates ready, ranked, excluded and deferred records, preserving the input. The example contains no real tasks or paths.

The library checks deadline/verification/stop reserves, dependencies, target conflicts, concurrent/heavy slots and supplied memory/disk limits. Missing caller measurements are not independently discovered or verified. A task's boolean authorization field and title-pattern filter are advisory data, not a security boundary or a replacement for permission enforcement.

Date-only task-board deadlines default explicitly to UTC end-of-day; pass an IANA timezone when another civil-day boundary is intended. Explicit timestamp deadlines must include a timezone.

## Extraction boundary

The public core omits workload-specific rendering qualification, source fingerprints, private policies, providers, credentials, runtime databases, machine services and process control. It never bypasses a caller's heavy-work pressure policy using private receipt types. The full internal supervisor and its source/runtime remain separate.

Tests cover deterministic ordering, propagation, dependency handling, conflict/capacity checks, malformed inputs, human-dependent tasks and date resolution on synthetic data. These are library checks, not evidence that an actual unattended shift or shutdown succeeded.

[MIT](LICENSE), copyright 2026 Exlumina contributors. Report bugs with synthetic task records. No third-party code, assets or external services are bundled.
