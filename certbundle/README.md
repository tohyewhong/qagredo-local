## Corporate CA bundle (optional)

Some environments intercept TLS traffic (e.g., corporate proxies). In that case Docker builds can fail during `pip install` with errors like:

`SSLCertVerificationError: certificate verify failed: unable to get local issuer certificate`

### How to use

1. Put your corporate root CA certificate (PEM) at:

`certbundle/certbundle.crt`

2. Rebuild the image:

```bash
cd /home/tyewhong/qagredo
docker build -t qagredo-v1:latest .
```

### Notes

- Do **not** commit your corporate CA into git. This repo ignores `certbundle/*.crt` by default.
- The Dockerfile will automatically detect `certbundle/certbundle.crt` and install it into the container trust store.

