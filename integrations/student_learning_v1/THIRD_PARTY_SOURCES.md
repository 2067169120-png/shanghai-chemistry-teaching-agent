# Local non-text image detector dependency sources and licenses

All active runtime files are stored under
`integrations/student_learning_v1/.runtime/`. Every downloaded file is SHA-256
verified before use. Student-image text recognition is permanently disabled:
the production bootstrap neither downloads nor probes Tesseract or language
data. Any artifacts left by an older installation are historical, read-only,
and are never used as new evidence or model input.

| Component | Pinned version | Purpose | Source | License |
| --- | --- | --- | --- | --- |
| OpenCV headless wheel | 4.12.0.88 | QR detection and bundled frontal-face Haar cascade | PyPI `opencv-python-headless`; OpenCV project | OpenCV Apache-2.0; packaging scripts MIT; bundled third-party notices apply |
| zxing-cpp wheel | 3.1.1 | Local QR and 1D/2D barcode decoding/localization | PyPI `zxing-cpp`; zxing-cpp project | Apache-2.0 |

The exact URLs, wheel filenames, file sizes where applicable, versions, and
SHA-256 digests are frozen in `dependency-lock.json`. If a remote file changes,
bootstrap stops on the hash mismatch. `bootstrap --offline` never connects to the
network and stops if the verified cache is incomplete. Bootstrap also verifies
every installed wheel member by size and ZIP CRC, repairs missing/mismatched
members without replacing already-matching loaded DLLs, and probes `cv2`,
`zxingcpp`, and the Haar face cascade in a fresh isolated Python process. Probe
stdout must be exactly one JSON document, so failed native imports cannot pollute
Gateway or acceptance JSON stdout.

Commands:

```powershell
python integrations\student_learning_v1\bootstrap_runtime.py bootstrap
python integrations\student_learning_v1\bootstrap_runtime.py --offline bootstrap
python integrations\student_learning_v1\bootstrap_runtime.py check
```
