# VS Code diagram support (offline bundle)

Ships with `qag_bundle.tar.gz` so redserver can render **Mermaid flowcharts**
in **any** `docs/*.md` Markdown preview.

| File | Purpose |
|------|---------|
| `bierner.markdown-mermaid-*.vsix` | Extension (fetch on build host; see below) |
| `extensions.json` | Workspace recommendation (copied to `qag_host/.vscode/`) |
| `settings.json` | Enables built-in Mermaid when VS Code supports it |

**Build host (online, once per extension version):**

```bash
bash scripts/offline/fetch_vscode_mermaid_vsix.sh
bash scripts/make_qag_bundle.sh
```

**Redserver (offline, once per user):**

```bash
cd /home/tyewhong/qag/qag_host
bash scripts/offline/install_vscode_diagram_preview.sh
```

Full steps: [`../VIEWING_DIAGRAMS_OFFLINE.md`](../VIEWING_DIAGRAMS_OFFLINE.md).
