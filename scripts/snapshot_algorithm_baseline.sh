#!/usr/bin/env bash
set -euo pipefail

# Snapshot the algorithm documentation bundle into docs/algorithm-baselines/vN/.
# Code audit and doc edits are done by the agent before --create; this script
# verifies links, copies files, and writes manifest.json.

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
BASE_DIR="${QAG_ALGORITHM_BASELINES_DIR:-$ROOT/docs/algorithm-baselines}"
INDEX="$BASE_DIR/INDEX.md"
SUMMARY=""

usage() {
  cat <<USAGE
Usage: bash scripts/snapshot_algorithm_baseline.sh <command> [options]

Commands:
  --create [--summary "text"]   Verify doc links, copy bundle, write manifest
  --list                        Print version index
  --diff vX vY                  Recursive diff of two baseline folders

Environment:
  QAG_ALGORITHM_BASELINES_DIR   Override output dir (default: docs/algorithm-baselines)
USAGE
}

die() {
  echo "snapshot_algorithm_baseline.sh: $*" >&2
  exit 1
}

next_version() {
  local max=0 n
  for d in "$BASE_DIR"/v[0-9]*; do
    [[ -d "$d" ]] || continue
    n="${d##*/v}"
    [[ "$n" =~ ^[0-9]+$ ]] || continue
    (( n > max )) && max="$n"
  done
  echo $((max + 1))
}

git_commit() {
  if git -C "$ROOT" rev-parse HEAD >/dev/null 2>&1; then
    git -C "$ROOT" rev-parse HEAD
  else
    echo "null"
  fi
}

sha256_file() {
  sha256sum "$1" | awk '{print $1}'
}

bundle_copy() {
  local dest="$1"
  mkdir -p "$dest/diagrams/architecture/diagrams"

  cp "$ROOT/docs/ALGORITHM_REPORT.md" "$dest/"
  cp "$ROOT/docs/HANDOVER.md" "$dest/"

  for f in \
    qag_grading_test_flow.dot \
    qag_grading_test_flow.svg; do
    if [[ -f "$ROOT/docs/$f" ]]; then
      cp "$ROOT/docs/$f" "$dest/diagrams/"
    fi
  done

  if [[ -f "$ROOT/docs/qag_input_prep_explained_16x9.png" ]]; then
    cp "$ROOT/docs/qag_input_prep_explained_16x9.png" "$dest/diagrams/"
  fi

  for f in \
    QAG_Pipeline_Flowchart.puml \
    QAG_Pipeline_Flowchart.svg \
    qag_sequence_final_7step.dot \
    qag_sequence_final_7step.svg; do
    if [[ -f "$ROOT/docs/architecture/diagrams/$f" ]]; then
      cp "$ROOT/docs/architecture/diagrams/$f" \
        "$dest/diagrams/architecture/diagrams/"
    fi
  done
}

write_manifest() {
  local dest="$1" ver="$2" label="$3" commit="$4"
  local manifest="$dest/manifest.json"
  local files=() rel sha

  while IFS= read -r -d '' f; do
    rel="${f#"$dest"/}"
    sha="$(sha256_file "$f")"
    files+=("    {\"path\": \"$rel\", \"sha256\": \"$sha\"}")
  done < <(find "$dest" -type f ! -name manifest.json ! -name code_audit.json \
    -print0 | sort -z)

  {
    echo '{'
    echo "  \"version\": $ver,"
    echo "  \"label\": \"$label\","
    echo "  \"created\": \"$(date -u +%Y-%m-%dT%H:%M:%SZ)\","
    echo "  \"summary\": $(python3 -c "import json,sys; print(json.dumps(sys.argv[1]))" "$SUMMARY"),"
    echo "  \"git_commit\": $( [[ "$commit" == "null" ]] && echo null || python3 -c "import json,sys; print(json.dumps(sys.argv[1]))" "$commit" ),"
    echo '  "files": ['
    local first=1
    for entry in "${files[@]}"; do
      [[ $first -eq 1 ]] && first=0 || echo ','
      echo -n "$entry"
    done
    echo
    echo '  ]'
    echo '}'
  } >"$manifest"
}

update_index() {
  local ver="$1" label="$2" commit="$3" file_count="$4"
  local date_str short_commit="$commit"
  date_str="$(date -u +%Y-%m-%d)"
  mkdir -p "$BASE_DIR"

  if [[ "$commit" != "null" && ${#commit} -gt 8 ]]; then
    short_commit="${commit:0:8}"
  fi

  if [[ ! -f "$INDEX" ]]; then
    cat >"$INDEX" <<EOF
# Algorithm documentation baselines

Code-verified snapshots of the algorithm documentation bundle. Say **baseline
now** in Cursor to audit code, sync docs, and create the next version.

| Version | Date | Summary | Git commit | Files |
|---------|------|---------|------------|-------|
| $label | $date_str | $SUMMARY | $short_commit | $file_count |

Latest: **$label**
EOF
    return
  fi

  if grep -q '_(none yet)_' "$INDEX"; then
    sed -i "/^| _(none yet)_ /d" "$INDEX"
  fi

  sed -i "/^Latest:/i\\| $label | $date_str | $SUMMARY | $short_commit | $file_count |" "$INDEX"
  sed -i "s/^Latest:.*/Latest: **$label**/" "$INDEX"
}

cmd_create() {
  while [[ $# -gt 0 ]]; do
    case "$1" in
      --summary)
        shift
        SUMMARY="${1:-}"
        ;;
      *)
        die "unknown --create option: $1"
        ;;
    esac
    shift
  done

  echo "Verifying doc image links..."
  python3 "$ROOT/scripts/verify_docs_links.py"

  local ver label dest commit file_count
  ver="$(next_version)"
  label="v${ver}"
  dest="$BASE_DIR/$label"
  [[ -d "$dest" ]] && die "$dest already exists; will not overwrite"

  commit="$(git_commit)"
  mkdir -p "$dest"
  bundle_copy "$dest"
  write_manifest "$dest" "$ver" "$label" "$commit"
  file_count="$(find "$dest" -type f ! -name manifest.json ! -name code_audit.json | wc -l | tr -d ' ')"
  update_index "$ver" "$label" "$commit" "$file_count"

  echo "Created $dest ($file_count files)."
  echo "Add code_audit.json from the agent audit before treating this as verified."
}

cmd_list() {
  if [[ -f "$INDEX" ]]; then
    cat "$INDEX"
  else
    echo "No baselines yet ($BASE_DIR)."
  fi
}

cmd_diff() {
  local a="${1:-}" b="${2:-}"
  [[ -n "$a" && -n "$b" ]] || die "usage: --diff vX vY"
  [[ "$a" != v* ]] && a="v$a"
  [[ "$b" != v* ]] && b="v$b"
  local da="$BASE_DIR/$a" db="$BASE_DIR/$b"
  [[ -d "$da" ]] || die "missing $da"
  [[ -d "$db" ]] || die "missing $db"
  diff -ru "$da" "$db" || true
}

if [[ $# -lt 1 ]]; then
  usage
  exit 0
fi

case "$1" in
  --create)
    shift
    cmd_create "$@"
    ;;
  --list)
    cmd_list
    ;;
  --diff)
    shift
    cmd_diff "$@"
    ;;
  -h|--help)
    usage
    ;;
  *)
    die "unknown command: $1 (try --help)"
    ;;
esac
