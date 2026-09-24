# 2026-09-25 handoff audit

## Package and source scope

The reviewed synthetic Phase 2 ZIP has SHA-256 `3d998720cb88460ea2b45e5af7cc406ae79f220ebe6365637ccd7e138c80abab`. Its `MANIFEST.json` SHA-256 is `55fbc140ea783f8e0d825063e02815911483250102607e852811d844e3d5b5b4`; all 10,309 listed payload files passed path, size, and SHA-256 checks, with no missing or extra files.

The source review is based on public repository commit `0de7f2ee75622773c99a951ee968b671f51298e5`. The reviewed 19-file code delta comprises four business files, two scripts, and thirteen tests. The four business-file SHA-256 values are:

- `src/factory_monitor/runtime.py`: `93d1f2bdb1a03a2fa72963929b6ae7797194c581aa7714c30be292eefd3c0bd9`
- `src/factory_monitor/evidence.py`: `21ee9a39bf833fdc6c24771145d033504c184be92f1b8befce9072394fd7b321`
- `src/factory_monitor/store.py`: `1d4bd1349a424783ef3117213a1bc129100cb348ab5a79d917ca2caaa46281bb`
- `src/factory_monitor/gui/app.py`: `35b65139023efae06f8cecc9323c3575d0884ec6641f969ba0ec39aebca2a5c6`

This review branch includes the imported repairs and added timestamp/diagnostic work. It is a draft integration against the public base, not a copy of the latest Windows functional candidate. It does not replace `main` or the field installation.

## Evidence readback

The package's Windows regression record reports **147 passed, 2 skipped, 1 deselected**. The included stdout, run metadata, JUnit XML, and raw logs agree; the deselected test is the real-YOLO replay. This result covers the packaged synthetic candidate only.

An independent sequential OpenCV read decoded all 20 synthetic Q4a/Q4b MP4 files. All matched package SHA-256 entries, event frame counts, and 2 fps. Each decoded frame was 240×180, matching the configured per-camera crop. This verifies decoding, dimensions, count, frame rate, and file identity; it does not repeat the pixel-ID oracle check.

An initial local verification assertion used the full mosaic size (960×540) instead of the event crop size (240×180). The verifier was corrected against the documented crop configuration; the initial result was preserved separately as a verifier-scope error, not a media defect.

The ZIP does not contain the source, tests, or raw evidence behind the **216 passed** figure in the 2026-09-24 report. It also lacks the 2026-09-25 capture-diagnostic raw logs. The latest report records 248 display-capture callbacks in an eight-second authorized observation, while source freshness remains unknown; that one-time authorization has ended and does not authorize another capture.

## Gate

**Formal Gate: FAIL.** The ZIP is a synthetic Phase 2 handoff and leaves Q6 controller interruption and Q7 physical desktop acceptance open. It does not prove live-camera freshness, model performance, production readiness, or field acceptance.

For the current recovery findings and bounded next steps, see [Seetong recovery handoff](../SEETONG_RECOVERY_2026-09-25_zh.md).
