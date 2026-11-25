# IEEE manuscript source

`main.tex` is a standalone IEEEtran two-column source for the Assessment Task 3
group report. The final PDF is `main.pdf` (12 pages). Author names, institution
and email are intentionally placeholders pending the group's submission details;
no quantitative placeholder remains.

## Ownership and evidence

| Section | Status |
| --- | --- |
| I Introduction | Contribution list and roadmap written from the implemented chain |
| II Architecture, III Edge DSP, IV Transport | Written from the edge implementation and saved logs |
| V Related Work | Broker-centric, flow-centric and platform-centric options compared |
| VI-A Cloud provisioning and triage dashboard | Eight widgets, availability timers, artifact verification |
| VI-B Alarm rules and hysteresis | Nine import files, WQI chain, benchmark pipeline, test coverage |
| VII Tool selection | Comparison table and tenant-specific trade-offs |
| VIII Results | Log-derived figures, two-run metrics, metric definitions, limitations |
| IX--X Conclusion, future work, discussion | Written from the measured envelope |
| Appendices | Payload contract, reproduction steps, evidence and alarm inventories |

Log-derived figures in `figures/` are generated from saved logs and measured batch
data by `python tools/build_paper_figures.py` and converted with `rsvg-convert`. The
`device_profile_assignment.png` and `wqi_server_telemetry.png` images in the same
directory are tenant captures copied from `../evidence/` so that the paper directory
is self-contained. All captions identify whether a figure is a tenant capture or a
log-derived reconstruction.

## Compilation

Use IEEEtran from Overleaf, or provide `IEEEtran.cls` and `IEEEtran.bst` locally.
A complete local chain is:

```bash
export TEXINPUTS=/path/to/ieee:
export BSTINPUTS=/path/to/ieee:
pdflatex main.tex
bibtex main
pdflatex main.tex
pdflatex main.tex
```

The same sequence was run through the `latex-paper-en` compile helper. The final
build exits successfully, resolves all cross-references and citations, and emits
no overfull boxes.

## Evidence rule

No simulated number is presented as a measurement. The batch-size values are
host-only results from `tools/batch_results.csv` and are labelled as such. The six
mandatory cloud metrics come from `cloud/benchmark/metrics.csv`, which combines
`metrics_outage.json` and `metrics_live.json` produced against the deployed
tenant. Missing tenant screenshots are represented by explicitly labelled
log-derived figures rather than invented captures.
