# Shared implementation interfaces (v1, freeze)

Use JSON-compatible dictionaries at subsystem boundaries. T1 owns config.py, rules.py, store.py, switching.py, evaluation.py. T2 owns runtime.py, capture/, evidence.py, inference.py and their tests. T3 owns gui/, GUI tests, launch scripts and desktop build configuration. Root coordinates packaging/docs/acceptance. Do not modify other owners' files without a message.

## Configuration (T1)
`default_config() -> dict`, `validate_config(config) -> None` (raises ValueError), `load_config(path) -> dict`, `save_config(config,path) -> None` (validated atomic write).
Required structure:
```
schema_version: 1
source: {backend: "auto", window_title: "", display_index: 0, expected_size: [0,0], layout_version: 1, calibrated: false}
detection: {model_path: "models/yolo11n.pt", device: "cpu", fps: 5, confidence: 0.35}
review: {enabled: true, endpoint: "http://127.0.0.1:11434", model: "qwen3-vl:2b-instruct", timeout_seconds: 15, cloud_enabled: false}
evidence: {pre_seconds: 30, post_seconds: 60, preview_seconds: 30, retain_completed: 20, max_inflight: 10, max_disk_mb: 10240}
switching: {enabled: false, calibrated: false, detail_seconds: 15, grid_seconds: 30, blind_budget_seconds: 180, verification: {}}
cameras: [ {id: "CAM01", name: "摄像头01", enabled: true, crop: [x,y,w,h], material_roi: [[x,y],...], station_roi: [[x,y],...], exit_line: [[x,y],[x,y]], absence_seconds: 300, schedule: {days: [0..6], active: [["00:00","24:00"]], breaks: []}, map_position: [x,y], heading: 0, related: [], view_action: {}} ]
floorplan: {image_path: "", zones: []}
```
All camera crop coordinates normalized to full captured frame. All ROIs/line/people bboxes normalized to that camera crop. Floorplan positions normalized. Default 10 camera crops in a 4x3 mosaic. Empty ROIs/line disable those rules; never imply a real camera is configured. Default cameras can be enabled but source.calibrated=false prevents live inference. Replay/demo explicitly identifies synthetic/calibrated fixture setup.

## Observations/rules (T1)
`RuleEngine(config)`, `observe(observation:dict) -> list[dict]`, `reset(camera_id=None)`.
Observation: `{camera_id, timestamp: float epoch seconds, health: "observable"|"blind"|"frozen"|"mapping_invalid"|"unavailable", layout_version:int, people:[{track_id:int,bbox:[x1,y1,x2,y2],confidence:float}]}`.
Emit candidates on person crossing exit_line or entering material_roi; track the person's feet/bbox bottom center. No wire detection claim. Station absence only with configured station_roi and active schedule; continuity resets on unobserved/inactive/invalid layout/time gaps. Dedupe repeat material track/region events and repeated absence until reset/reoccupation.
Event: `{id:str,camera_id,kind:"material_candidate"|"station_absence",triggered_at:float,status:"candidate",reason:str,layout_version:int}`. Event kinds are observations, never accusations.

## Persistence (T1)
`EventStore(db_path)`, `create_event(event)`, `update_event(event_id, **fields)`, `get_event(event_id)->dict|None`, `list_events(limit=100)->list[dict]`, `prune_completed(retain=20)->list[dict]`, `close()`.
Columns/payload can include `analysis_status` (pending/supported/dismissed/uncertain/timeout/error), `analysis`, `recording_status` (recording/complete/incomplete/error), `evidence_path`, `preview_path`, `gaps`, `review_label` (pending/confirmed/false_alarm), `completed_at`, `latency_ms`. Event `status` candidate/recording/complete/incomplete/error. Prune only finalized events (non-null completed_at), never in-flight; runtime owns deletion of returned paths after safe validation. SQLite WAL; parameterized writes; readback and crash recovery tests.

## Switching policy (T1) / actuator (T2)
`SwitchPolicy(config)` with `enqueue(camera_id,kind,now)`, `next_action(now)->dict|None` (`{action:"detail",camera_id}` or `{action:"grid"}`), `confirm(camera_id_or_none, now, verified:bool)`, `health(camera_id,now)->str`, `snapshot(now)->dict`. Single global queue with material priority, only enabled+calibrated. Actual IDs/layout MUST be verified by a local adapter before confirm(true). Inability to verify disables automation and makes coverage unknown; never return automatically observable after a failed grid return. View action execution remains disabled without operator calibration/readback. Policy budget includes switching/return uncertainty, not only intended dwell.

## Runtime boundary (T2) used by GUI/CLI
`RuntimeController(config:dict, data_dir:Path)`
- `start(source="demo"|"video"|"live", input_path:str|None=None)`
- `stop()` (idempotent, graceful joins)
- `poll()->list[dict]` events with `type`: frame/health/candidate/analysis/event_updated/error/stats/status; frame may contain `camera_id` and `image` ndarray; all others JSON serializable. Poll is nonblocking. UI must surface errors.
- `request_view(camera_id)->dict` `{ok:bool,reason:str}`
- `return_grid()->dict` same shape.
- `status()->dict` includes running/source/field_verified:false and worker health.
Paths inside data_dir: events.sqlite3, evidence/, cache/, logs/. No global daemon or public bind. Capture records independent from detection/review. Demo is visibly SYNTHETIC and cannot call cloud/live automation. A missing model/capture permission produces a real surfaced failure, never fallback simulated detections in live mode.

## GUI boundary (T3)
`factory_monitor.gui.app.run_gui(config_path:Path, data_dir:Path, source="demo", input_path=None)` launches QApplication.
Main UI: 10 tile preview, clear synthetic/live and unavailable status, Start/Stop, capture source and calibration configuration, floorplan image/positions/related views, per-camera ROI/line/schedule editor, event list with evidence playback and human label, diagnostics. Configuration changes require stopped runtime; validated atomic save. Live source not calibrated cannot start. UI never implies fixed crops are actual camera identity calibration.

## Team constraints
Use a target-platform Python 3.12 installation and the project-local .venv. Root owns .venv and dependency installs; workers request deps via message. Use stdlib unittest for pure tests; pytest is available via dev requirements. Tests before new stateful production behavior, record red/green commands in reports/<task>.md. No commits until root collects changes. No paid API request or real screen capture without explicitly selecting a test window. Synthetic fixture may draw stick people with scripted detections ONLY in demo source; video/live always actual detector or explicit failure. Integration must not count demo scripted detections as ML evidence.
