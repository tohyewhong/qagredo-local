# QAGRedo Documentation Hub

This folder is the primary home for project documentation. For the main project quick start, use `../README.md`.

## Start here by role

| Role | Read first | Then |
|---|---|---|
| New maintainer | `HANDOVER.md` | `SERVER_MODEL_PROFILES.md`, `OFFLINE_SETUP_GUIDE.md` |
| Offline operator | `OFFLINE_SETUP_GUIDE.md` | `ONLINE_SETUP_GUIDE.md`, `Siteserver_vLLM_Change_Guide.md` |
| Build/packaging owner | `ONLINE_SETUP_GUIDE.md` | `HANDOVER.md` |
| Kubeflow operator | `KUBEFLOW_DEPLOY.md` | `SERVER_MODEL_PROFILES.md` |
| Architecture reviewer | `architecture/NETWORK_DIAGRAM.md` | `ALGORITHM_REPORT.md` |
| Stakeholder/non-engineering | `QAGRedo_Management_Overview.html` | `QAGRedo_Pipeline_Flowchart_Drawn.html` |

## Visuals first

- `QAGRedo_Management_Overview.html` - One-page stakeholder summary: what the system does, when to use it, and what it outputs.
- `QAGRedo_Pipeline_Flowchart_Drawn.html` - Browser-friendly pipeline view with failure and retry flow.
- `architecture/diagrams/QAGRedo_Sequence_Final_7step_VIEW_IN_BROWSER.html` - End-to-end 7-step sequence view.
- `architecture/diagrams/*.dot`, `architecture/diagrams/*.puml` - Diagram source-of-truth files; update these before rendered outputs.

## Core runbooks

- `HANDOVER.md` - Maintainer onboarding index, code map, and profile map.
- `OFFLINE_SETUP_GUIDE.md` - Current profile-based offline setup guide.
- `Siteserver_vLLM_Change_Guide.md` - Siteserver vLLM upgrade runbook (Qwen3.5 image, generator/judge split).
- `ONLINE_SETUP_GUIDE.md` - Build machine workflow and tarball generation.
- `KUBEFLOW_DEPLOY.md` - Kubeflow profile deployment details.

## Reference docs

- `SERVER_MODEL_PROFILES.md` - Server-to-profile/model mapping.
- `ALGORITHM_REPORT.md` - Pipeline algorithm, reasoning, and schema details.
- `architecture/NETWORK_DIAGRAM.md` - Runtime ports, routes, and network layout.

## `run.sh` modes (vllm profile)

| Mode | Commands |
|------|----------|
| Split (recommended offline) | `--vllm-up generator` → `--vllm-up judge` → `--pipeline-only` |
| All-in-one | `bash run.sh` or `bash run.sh -- --num-documents N` |
| Post-run (any profile) | `--minimise` — minimal JSON from latest run; no LLM/vLLM required |
| Resume / skip processed | `--resume` (reuse latest run folder + skip existing `*_analysis.json`); `--skip-existing-outputs` (skip only, new folder unless `--resume`) |
| Stakeholder HTML | `QAGRedo_Management_Overview.html` — profiles + resume section |
| Summarise (any profile) | `--summarize --latest` (optional) |

Full table: **`Siteserver_vLLM_Change_Guide.md`** Part D · **`OFFLINE_SETUP_GUIDE.md`** §8 · **`bash run.sh --help`**

## Conventions

- Keep user-facing quick start in `../README.md`.
- Keep maintainer/system navigation in `HANDOVER.md` and this hub.
- Prefer updating diagram sources (`.dot`, `.puml`, Mermaid) before rendered outputs.
- When adding a new doc, add it to this file and to `HANDOVER.md` if it changes onboarding flow.
