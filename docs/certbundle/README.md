## Corporate CA bundle (optional)

Project docs index: **`docs/HANDOVER.md`**.

Some environments intercept TLS traffic (e.g., corporate proxies). In that case Docker builds can fail during `pip install` with errors like:

`SSLCertVerificationError: certificate verify failed: unable to get local issuer certificate`

### How to use

1. Put your corporate root CA certificate (PEM) at:

`certbundle/certbundle.crt`

2. Rebuild the image (offline-safe default):

```bash
cd /home/tyewhong/qagredo
docker compose -f docker-compose.yml build qagredo
```

After a successful build, confirm dependencies (optional but recommended):

```bash
docker run --rm --entrypoint "" qagredo-v1:latest \
  python /workspace/scripts/docker_verify_requirements.py /workspace/requirements.txt
```

### Notes

- Do **not** commit your corporate CA into git. This repo ignores `certbundle/*.crt` by default.
- The Dockerfile will automatically detect `certbundle/certbundle.crt` and install it into the container trust store.

