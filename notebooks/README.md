# Evidence Carrier research notebook

[Open the notebook on GitHub](evidence_carrier_research.ipynb) · [Open in Colab](https://colab.research.google.com/github/beepboop2025/liquilens-evidence-carrier/blob/main/notebooks/evidence_carrier_research.ipynb) · [Launch on MyBinder](https://mybinder.org/v2/gh/beepboop2025/liquilens-evidence-carrier/main?urlpath=lab/tree/notebooks/evidence_carrier_research.ipynb)

This zero-product-account notebook uses embedded synthetic values to demonstrate
deterministic carrier issuance, verification, tamper rejection, rights-bounded
references, and public adapter projections. It requests no API key, account,
cookie, proprietary dataset, or user data. Its only runtime network operation is
the first cell's bounded download of the immutable 0.14.0 wheel from GitHub
Releases; every subsequent research cell is local.

The executable artifact is
`liquilens_evidence-0.14.0-py3-none-any.whl`, SHA-256
`f0162affab57307c8e20acf91dcefc33840f91e8cf9969a8d5ec8d8df860cd24`.
The corresponding source distribution has SHA-256
`bd7a0a61bdb99784071021f95c160b9baeb22e00054f80abc03445a6cf576567`.

MyBinder is the anonymous hosted execution path. Colab can load the notebook
directly from GitHub, but Google's current access policy may require a Google
session to attach an execution runtime. Neither path requires a LiquiLens
account.

To reproduce the clean-room test locally with Python 3.11:

```bash
python3 notebooks/tests/validate_notebook.py
```

The validator creates two isolated virtual environments, downloads and verifies
the release wheel independently in each one, executes every code cell without a
repository import path, and requires byte-for-byte identical textual receipts.
