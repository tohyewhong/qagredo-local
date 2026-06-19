# QAGRedo Documentation Hub

Primary documentation home. User quick start: [`../README.md`](../README.md).

---

## Handover path (10 minutes)

Read in this order when onboarding a new maintainer:

1. This file — what exists and where
2. [`HANDOVER.md`](HANDOVER.md) — system purpose, code map, profiles
3. [`SERVER_MODEL_PROFILES.md`](SERVER_MODEL_PROFILES.md) — which server uses which profile
4. [`OFFLINE_SETUP_GUIDE.md`](OFFLINE_SETUP_GUIDE.md) §2–4 — what to copy to an offline host
5. [`QAGRedo_Management_Overview.html`](QAGRedo_Management_Overview.html) — stakeholder view
6. [`ALGORITHM_REPORT.md`](ALGORITHM_REPORT.md) — only when debugging schema or pipeline logic

---

## Start here by role

| Role | Read first | Then |
|---|---|---|
| New maintainer | `HANDOVER.md` | `SERVER_MODEL_PROFILES.md`, `OFFLINE_SETUP_GUIDE.md` |
| Offline operator | `OFFLINE_SETUP_GUIDE.md` | `Siteserver_vLLM_Change_Guide.md`, `ONLINE_SETUP_GUIDE.md` |
| Build/packaging owner | `ONLINE_SETUP_GUIDE.md` | `HANDOVER.md` |
| Kubeflow operator | `KUBEFLOW_DEPLOY.md` | `SERVER_MODEL_PROFILES.md` |
| Architecture reviewer | `architecture/NETWORK_DIAGRAM.md` | `ALGORITHM_REPORT.md` |
| Stakeholder / non-engineering | `QAGRedo_Management_Overview.html` | `QAGRedo_Pipeline_Flowchart_Drawn.html` |

---

## Command reference

### `run.sh` (host launcher)

| Goal | Command |
|------|---------|
| Show profile + paths | `bash run.sh --show-config` |
| Health / containers | `bash run.sh --status` |
| Stop stack | `bash run.sh --down` |
| **vLLM:** start generator | `bash run.sh --vllm-up generator` |
| **vLLM:** start judge | `bash run.sh --vllm-up judge` |
| **vLLM:** pipeline only | `bash run.sh --pipeline-only [--resume] [--num-documents N]` |
| **ollama/kubeflow:** run | `bash run.sh -- --num-documents N` |
| Resume (reuse run folder) | `bash run.sh -- --resume` or `bash run.sh --pipeline-only --resume` |
| Minimal output **during** run | `bash run.sh -- --minimal-qa-output` |
| Post-run minimal + good/bad | `bash run.sh --minimise [RUN_DIR]` |
| Good pairs only | `bash run.sh --minimise-good [RUN_DIR]` |
| Bad pairs only | `bash run.sh --minimise-bad [RUN_DIR]` |
| Run summary | `bash run.sh --summarize --latest [--json]` |

`--num-documents 0` = process all loaded records. With `--resume`, already-finished
docs still count toward the limit — use a large `N` to reach new inputs.

### `run_qa_pipeline.py` (inside container; `run.sh` injects `--config`)

| Flag | Purpose |
|------|---------|
| `--config` | Profile YAML (default: `config/config.<profile>.yaml` from `QAGREDO_PROFILE`; `run.sh` sets this automatically) |
| `--num-documents N` | Override `run.num_documents` |
| `--resume` | Reuse latest run folder; skip existing `*_analysis.json` |
| `--skip-existing-outputs` | Skip existing outputs; new folder unless `--resume` too |
| `--minimal-qa-output` | Save slim `*_analysis.json` (content + Q/A only) |
| `--input-file PATH` | Single JSON/JSONL input |

### Post-run tools (no LLM)

| Tool | Purpose |
|------|---------|
| `bash run.sh --minimise` | `*_analysis_minimal.json` + good/bad pair files |
| `bash run.sh --summarize` | Terminal + optional `run_summary.json` |
| `scripts/utils/export_analysis_minimal.py` | Minimal JSON only |
| `scripts/utils/export_analysis_training_jsonl.py` | Good/bad split only |

---

## Markdown runbooks

| File | Audience | Purpose |
|------|----------|---------|
| [`HANDOVER.md`](HANDOVER.md) | Maintainer | Onboarding index, code map, release checks |
| [`OFFLINE_SETUP_GUIDE.md`](OFFLINE_SETUP_GUIDE.md) | Offline operator | Archives to copy, install, first run |
| [`ONLINE_SETUP_GUIDE.md`](ONLINE_SETUP_GUIDE.md) | Build host | Local validation, `make_offline_tarballs.sh` |
| [`Siteserver_vLLM_Change_Guide.md`](Siteserver_vLLM_Change_Guide.md) | Siteserver | Qwen3.5 vLLM image, split startup |
| [`SERVER_MODEL_PROFILES.md`](SERVER_MODEL_PROFILES.md) | All operators | Server → profile → model mapping |
| [`KUBEFLOW_DEPLOY.md`](KUBEFLOW_DEPLOY.md) | Kubeflow | Single-image profile |
| [`ALGORITHM_REPORT.md`](ALGORITHM_REPORT.md) | Engineers | Pipeline algorithm, output schema |
| [`architecture/NETWORK_DIAGRAM.md`](architecture/NETWORK_DIAGRAM.md) | Engineers | Ports, URLs, compose layout |
| [`certbundle/README.md`](certbundle/README.md) | Build | Optional corporate CA for Docker |

Daily config: **`config/config.<profile>.yaml`** (`ollama` | `kubeflow` | `vllm`). `QAGREDO_PROFILE` in `.env` is required.

---

## HTML overviews

| File | Audience | Regenerate |
|------|----------|------------|
| [`QAGRedo_Management_Overview.html`](QAGRedo_Management_Overview.html) | Stakeholders | Hand-edit; keep in sync with `HANDOVER.md` |
| [`QAGRedo_Pipeline_Flowchart_Drawn.html`](QAGRedo_Pipeline_Flowchart_Drawn.html) | Engineers | `python3 scripts/utils/_rewrite_drawn_flowchart_html.py` |
| [`architecture/diagrams/QAGRedo_Sequence_Final_7step_VIEW_IN_BROWSER.html`](architecture/diagrams/QAGRedo_Sequence_Final_7step_VIEW_IN_BROWSER.html) | Engineers | Hand-edit; embed PNG from `.dot` |

---

## Diagram sources and renders

**Source of truth:** `.dot` and `.puml` under `docs/` and `docs/architecture/diagrams/`.

| Source | PNG output | Used by |
|--------|------------|---------|
| `architecture/diagrams/network_docker_compose_ollama.dot` | `..._ollama.png` | `NETWORK_DIAGRAM.md` |
| `architecture/diagrams/network_docker_compose.dot` | `...compose.png` | Architecture docs |
| `siteserver_vllm_change_flow.dot` | `siteserver_vllm_change_flow.png` | Siteserver guide |
| `qagredo_grading_test_flow.dot` | `qagredo_grading_test_flow.png` | Grading / tests |
| `architecture/diagrams/qagredo_sequence_final_7step.dot` | `..._16x9.png` | Sequence HTML |
| `architecture/diagrams/QAGRedo_Pipeline_Flowchart.puml` | optional PNG | Reference |

Regenerate PNG:

```bash
dot -Tpng docs/architecture/diagrams/network_docker_compose_ollama.dot \
  -o docs/architecture/diagrams/network_docker_compose_ollama.png
```

SVG files alongside `.dot` sources are alternate renders; update `.dot` first.

---

## Presentation catalog (PPTX)

Regenerate from repo root (`python3 scripts/utils/<script>.py`).

### Canonical (handover)

| Deck | Script | Audience |
|------|--------|----------|
| `docs/QAGRedo_Management_Overview.pptx` | `build_qagredo_management_overview_pptx.py` | Stakeholder first meeting |
| `docs/QAGRedo_Technical_Workflow_10slides.pptx` | `build_technical_workflow_ppt_10.py` | Engineer walkthrough |
| `docs/architecture/diagrams/QAGRedo_Pipeline_Flowchart_editable.pptx` | `build_qagredo_pipeline_flowchart_pptx.py` | Pipeline + slot loop |

### Supplementary

| Deck | Script |
|------|--------|
| `docs/QAGRedo_Executive_Overview_Tradeoffs.pptx` | `build_executive_qagredo_ppt.py` |
| `docs/QAGRedo_Technical_Workflow_20slides.pptx` | `build_technical_workflow_ppt.py` |
| `docs/QAGRedo_End_to_End_Workflow_Breakdown.pptx` | `build_workflow_breakdown_ppt.py` |
| `docs/architecture/diagrams/qagredo_workflow_current_editable.pptx` | `build_qagredo_workflow_current_pptx.py` |
| `docs/architecture/diagrams/qagredo_slide1_slot_flow_editable.pptx` | `build_qagredo_slide1_slot_flow_pptx.py` |
| `docs/QAGRedo_Output_Fields_Overview.pptx` | `build_qagredo_output_fields_ppt.py` |
| `docs/QAGRedo_Run_Summary_<doc>.pptx` | `build_analysis_run_summary_ppt.py` (per-run) |
| `QAGRedo_Output_Sample.json` | `generate_output_field_deliverables.py` |

Slide image assets: `docs/assets/` (vendored from builders).

---

## Conventions

- User quick start stays in [`../README.md`](../README.md).
- Maintainer navigation stays in [`HANDOVER.md`](HANDOVER.md) and this hub.
- Update diagram **sources** (`.dot`/`.puml`) before PNG/HTML exports.
- Offline archives default to `/data/tyewhong/qagredo/` (`QAGREDO_ARCHIVE_DIR`).
- When adding a doc, list it here and in `HANDOVER.md` if onboarding changes.
- Verify image links: `python3 scripts/verify_docs_links.py`
