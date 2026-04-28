# QAGRedo Documentation Hub

This folder is the primary home for project documentation.

If you are looking for the main project README, it is at:
- `../README.md` (repo root)

## Start here by role

| Role | Read first | Then |
|---|---|---|
| New maintainer | `HANDOVER.md` | `SERVER_MODEL_PROFILES.md`, `OFFLINE_SETUP_GUIDE.md` |
| Offline operator | `OFFLINE_SETUP_GUIDE.md` | `ONLINE_SETUP_GUIDE.md` |
| Build/packaging owner | `ONLINE_SETUP_GUIDE.md` | `HANDOVER.md` |
| Kubeflow operator | `KUBEFLOW_DEPLOY.md` | `SERVER_MODEL_PROFILES.md` |
| Architecture reviewer | `architecture/NETWORK_DIAGRAM.md` | `ALGORITHM_REPORT.md` |
| Stakeholder/non-engineering | `QAGRedo_Management_Overview.html` | `QAGRedo_Pipeline_Flowchart_Drawn.html` |

## Documentation map

### Core runbooks
- `HANDOVER.md` - Maintainer onboarding index, code map, and profile map.
- `OFFLINE_SETUP_GUIDE.md` - Current profile-based offline setup guide.
- `OFFLINE_SETUP_GUIDE.md` - Practical offline operations and troubleshooting.
- `ONLINE_SETUP_GUIDE.md` - Build machine workflow and tarball generation.
- `KUBEFLOW_DEPLOY.md` - Kubeflow profile deployment details.

### System and model references
- `SERVER_MODEL_PROFILES.md` - Server-to-profile/model mapping.
- `ALGORITHM_REPORT.md` - Pipeline algorithm, reasoning, and schema details.
- `architecture/NETWORK_DIAGRAM.md` - Runtime ports, routes, and network layout.

### Visuals and diagram sources
- `QAGRedo_Management_Overview.html` - Stakeholder one-page overview.
- `QAGRedo_Pipeline_Flowchart_Drawn.html` - Browser-friendly pipeline SVG flow.
- `architecture/diagrams/QAGRedo_Sequence_Final_7step_VIEW_IN_BROWSER.html` - 7-step sequence HTML view.
- `architecture/diagrams/*.dot`, `architecture/diagrams/*.puml` - Diagram source-of-truth files.

## Conventions

- Keep user-facing quick start in `../README.md`.
- Keep maintainer/system navigation in `HANDOVER.md` and this hub.
- Prefer updating diagram sources (`.dot`, `.puml`, Mermaid) before rendered outputs.
- When adding a new doc, add it to this file and to `HANDOVER.md` if it changes onboarding flow.
