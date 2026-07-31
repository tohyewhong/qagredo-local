# QAG Documentation Hub

Primary documentation home. User quick start: [`../README.md`](../README.md).

---

## Handover path (10 minutes)

Read in this order when onboarding a new maintainer:

1. This file — what exists and where
2. [`ARCHITECTURE.md`](ARCHITECTURE.md) — **technical lead overview** (system design, profiles, ML lifecycle)
3. [`HANDOVER.md`](HANDOVER.md) — system purpose, code map, profiles
4. [`SERVER_MODEL_PROFILES.md`](SERVER_MODEL_PROFILES.md) — which server uses which profile
4. [`REDSERVER_ONSITE_SETUP.md`](REDSERVER_ONSITE_SETUP.md) — **redserver step-by-step** (vLLM on-site)
5. [`OFFLINE_SETUP_GUIDE.md`](OFFLINE_SETUP_GUIDE.md) §2–4 — archive catalog + generic offline install
6. [`QAG_Management_Overview.html`](QAG_Management_Overview.html) — stakeholder view
7. [`ALGORITHM_REPORT.md`](ALGORITHM_REPORT.md) — only when debugging schema or pipeline logic
8. [`algorithm-baselines/README.md`](algorithm-baselines/README.md) — after upgrades; say **baseline now** in Cursor

---

## Start here by role

| Role | Read first | Then |
|---|---|---|
| New maintainer | `HANDOVER.md` | `SERVER_MODEL_PROFILES.md`, `OFFLINE_SETUP_GUIDE.md` |
| Offline operator | `REDSERVER_ONSITE_SETUP.md` | `OFFLINE_SETUP_GUIDE.md` §3 (Opserver vLLM + finetune §3.6) |
| Build/packaging owner | `ONLINE_SETUP_GUIDE.md` | `HANDOVER.md` |
| Kubeflow operator | `KUBEFLOW_DEPLOY.md` | `SERVER_MODEL_PROFILES.md` |
| **Technical lead / architect** | **`ARCHITECTURE.md`** | `architecture/NETWORK_DIAGRAM.md`, `ALGORITHM_REPORT.md` §1–§3 |
| Architecture reviewer | `architecture/NETWORK_DIAGRAM.md` | `ARCHITECTURE.md`, `ALGORITHM_REPORT.md` |
| Stakeholder / non-engineering | `QAG_Management_Overview.html` | `ARCHITECTURE.md` §1–§2, `QAG_Pipeline_Flowchart_Drawn.html` |

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
| **Host LoRA finetune** (adapter only) | `bash run.sh --finetune-lora [RUN_DIR]` |
| **DPO finetune** (after SFT) | `bash run.sh --finetune-dpo [RUN_DIR]` |

Redserver finetune walkthrough: [`REDSERVER_ONSITE_SETUP.md`](REDSERVER_ONSITE_SETUP.md) **§8.5**
(bundle includes `scripts/lora/`; copy HF weights + `.venv-lora` separately).

`--num-documents 0` = process all loaded records. With `--resume`, already-finished
docs still count toward the limit — use a large `N` to reach new inputs.

### `run_qa_pipeline.py` (inside container; `run.sh` injects `--config`)

| Flag | Purpose |
|------|---------|
| `--config` | Profile YAML (default: `config/config.<profile>.yaml` from `QAG_PROFILE`; `run.sh` sets this automatically) |
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

### Algorithm documentation baselines

After a pipeline upgrade, say **baseline now** in Cursor to code-audit the
implementation and snapshot verified docs. See
[`algorithm-baselines/README.md`](algorithm-baselines/README.md).

| Command | Purpose |
|---------|---------|
| `bash scripts/snapshot_algorithm_baseline.sh --create --summary "…"` | Verify links, copy bundle to `vN/` |
| `bash scripts/snapshot_algorithm_baseline.sh --list` | Show version index |
| `bash scripts/snapshot_algorithm_baseline.sh --diff v1 v2` | Line diff between versions |

---

## Markdown runbooks

| File | Audience | Purpose |
|------|----------|---------|
| [`ARCHITECTURE.md`](ARCHITECTURE.md) | Technical lead | System architecture, technology stack, deployment topology |
| [`HANDOVER.md`](HANDOVER.md) | Maintainer | Onboarding index, code map, release checks |
| [`architecture/README.md`](architecture/README.md) | Engineers | Architecture doc index and diagram sources |
| [`OFFLINE_SETUP_GUIDE.md`](OFFLINE_SETUP_GUIDE.md) | Offline operator | Archives to copy, install, first run |
| [`VIEWING_DIAGRAMS_OFFLINE.md`](VIEWING_DIAGRAMS_OFFLINE.md) | Offline operator | VS Code: Mermaid in all `.md` previews |
| [`ONLINE_SETUP_GUIDE.md`](ONLINE_SETUP_GUIDE.md) | Build host | Local validation, `make_offline_tarballs.sh` |
| [`Siteserver_vLLM_Change_Guide.md`](Siteserver_vLLM_Change_Guide.md) | Siteserver | Qwen3.5 vLLM image, split startup |
| [`SERVER_MODEL_PROFILES.md`](SERVER_MODEL_PROFILES.md) | All operators | Server → profile → model mapping |
| [`KUBEFLOW_DEPLOY.md`](KUBEFLOW_DEPLOY.md) | Kubeflow | Single-image profile |
| [`ALGORITHM_REPORT.md`](ALGORITHM_REPORT.md) | Engineers | Pipeline algorithm, output schema |
| [`algorithm-baselines/README.md`](algorithm-baselines/README.md) | Maintainer | Code-verified doc snapshots (`baseline now` in Cursor) |
| [`architecture/NETWORK_DIAGRAM.md`](architecture/NETWORK_DIAGRAM.md) | Engineers | Ports, URLs, compose layout |
| [`certbundle/README.md`](certbundle/README.md) | Build | Optional corporate CA for Docker |

Daily config: **`config/config.<profile>.yaml`** (`ollama` | `kubeflow` | `vllm`). `QAG_PROFILE` in `.env` is required.

---

## HTML overviews

| File | Audience | Regenerate |
|------|----------|------------|
| [`QAG_Management_Overview.html`](QAG_Management_Overview.html) | Stakeholders | Hand-edit; keep in sync with `HANDOVER.md` |
| [`QAG_Pipeline_Flowchart_Drawn.html`](QAG_Pipeline_Flowchart_Drawn.html) | Engineers | `python3 scripts/utils/_rewrite_drawn_flowchart_html.py` |
| [`architecture/diagrams/QAG_Sequence_Final_7step_VIEW_IN_BROWSER.html`](architecture/diagrams/QAG_Sequence_Final_7step_VIEW_IN_BROWSER.html) | Engineers | Hand-edit; embed PNG from `.dot` |

---

## Diagram sources and renders

**Source of truth:** `.dot` and `.puml` under `docs/` and `docs/architecture/diagrams/`.

| Source | PNG output | Used by |
|--------|------------|---------|
| `architecture/diagrams/network_docker_compose_ollama.dot` | `..._ollama.png` | `NETWORK_DIAGRAM.md` |
| `architecture/diagrams/network_docker_compose.dot` | `...compose.png` | Architecture docs |
| `siteserver_vllm_change_flow.dot` | `siteserver_vllm_change_flow.png` | Siteserver guide |
| `qag_grading_test_flow.dot` | `qag_grading_test_flow.png` | Grading / tests |
| `architecture/diagrams/qag_sequence_final_7step.dot` | `..._16x9.png` | Sequence HTML |
| `architecture/diagrams/QAG_Pipeline_Flowchart.puml` | optional PNG | Reference |

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
| `docs/QAG_Management_Overview.pptx` | `build_qag_management_overview_pptx.py` | Stakeholder first meeting |
| `docs/QAG_Technical_Workflow_10slides.pptx` | `build_technical_workflow_ppt_10.py` | Engineer walkthrough |
| `docs/architecture/diagrams/QAG_Pipeline_Flowchart_editable.pptx` | `build_qag_pipeline_flowchart_pptx.py` | Pipeline + slot loop |

### Supplementary

| Deck | Script |
|------|--------|
| `docs/QAG_Executive_Overview_Tradeoffs.pptx` | `build_executive_qag_ppt.py` |
| `docs/QAG_Technical_Workflow_20slides.pptx` | `build_technical_workflow_ppt.py` |
| `docs/QAG_End_to_End_Workflow_Breakdown.pptx` | `build_workflow_breakdown_ppt.py` |
| `docs/architecture/diagrams/qag_workflow_current_editable.pptx` | `build_qag_workflow_current_pptx.py` |
| `docs/architecture/diagrams/qag_slide1_slot_flow_editable.pptx` | `build_qag_slide1_slot_flow_pptx.py` |
| `docs/QAG_Output_Fields_Overview.pptx` | `build_qag_output_fields_ppt.py` |
| `docs/QAG_Run_Summary_<doc>.pptx` | `build_analysis_run_summary_ppt.py` (per-run) |
| `QAG_Output_Sample.json` | `generate_output_field_deliverables.py` |

Slide image assets: `docs/assets/` (vendored from builders).

---

## Conventions

- User quick start stays in [`../README.md`](../README.md).
- Maintainer navigation stays in [`HANDOVER.md`](HANDOVER.md) and this hub.
- **Doc diagram layers** (use together; keep in sync with code):
  1. **Overview sketch** — ASCII or simple LR Mermaid (stages only; e.g.
     `ALGORITHM_REPORT.md` §1.1, `HANDOVER.md` top chart).
  2. **Behavioral flowchart** — Mermaid `flowchart TD` with decisions, retries,
     and failure paths (§2.5–§7 in `ALGORITHM_REPORT.md`).
  3. **Exported visuals** — `.dot`/`.puml` → PNG, HTML, PPTX for stakeholders
     (regenerate from sources; do not hand-edit PNG only).
- Update diagram **sources** (`.dot`/`.puml`) before PNG/HTML exports.
- Offline archives default to `/data/tyewhong/qag/` (`QAG_ARCHIVE_DIR`). Active
  profile `.tar` / `.tar.gz` stay in that **root**; retired archives go in
  `zz_old_qag/`. Legacy `/data/tyewhong/qagredo/` is deprecated.
- Repo folder is **`/home/tyewhong/qag`**; bundle extract creates `qag_host/`.
  Docker services: `qag-vllm`, `qag-vllm-judge`, `qag-runner`; compose project
  `qag_offline`. Env var: `QAG_PROFILE` (not `QAGREDO_PROFILE`).
- When adding a doc, list it here and in `HANDOVER.md` if onboarding changes.
- After algorithm or pipeline upgrades, run **baseline now** in Cursor; see
  [`algorithm-baselines/README.md`](algorithm-baselines/README.md).
- Verify image links: `python3 scripts/verify_docs_links.py`
