# QAG architecture documentation

Start with the system overview:

| Document | Audience | Contents |
|----------|----------|----------|
| [**`../ARCHITECTURE.md`**](../ARCHITECTURE.md) | Technical lead / architect | Full system design, profiles, data flow, ML lifecycle |
| [`NETWORK_DIAGRAM.md`](NETWORK_DIAGRAM.md) | Engineers | Docker networks, ports, URLs, troubleshooting |
| [`../ALGORITHM_REPORT.md`](../ALGORITHM_REPORT.md) | Engineers | Slot loop, grading, output schema (deep dive) |
| [`../SERVER_MODEL_PROFILES.md`](../SERVER_MODEL_PROFILES.md) | Operators | Server → profile → model mapping |

## Diagram sources

| Source | Render |
|--------|--------|
| `diagrams/network_docker_compose.dot` | vLLM compose topology |
| `diagrams/network_docker_compose_ollama.dot` | Ollama host-network runner |
| `diagrams/qag_sequence_final_7step.dot` | 7-step sequence |
| `diagrams/QAG_Pipeline_Flowchart.puml` | Pipeline reference |
| `../redserver_vllm_external.dot` | Redserver + gpuserver |
| `../qag_grading_test_flow.dot` | Grading / gate flow |

Regenerate PNG:

```bash
dot -Tpng docs/architecture/diagrams/network_docker_compose.dot \
  -o docs/architecture/diagrams/network_docker_compose.png
python3 scripts/verify_docs_links.py
```

HTML walkthrough: [`diagrams/QAG_Sequence_Final_7step_VIEW_IN_BROWSER.html`](diagrams/QAG_Sequence_Final_7step_VIEW_IN_BROWSER.html)
