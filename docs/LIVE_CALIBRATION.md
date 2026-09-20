# Advanced live calibration contract

Live capture can start only after `source.calibrated=true`, an exact nonblank window title and a nonzero expected size are saved. That declaration is not camera-identity or playback-freshness proof. The runtime suspends live inference and evidence until the image checks below pass.

The current GUI records camera-label templates only. The heartbeat and distinct detail-layout template remain advanced manual configuration until the actual monitoring client is available. Automatic detail/grid control is not delivered: no configuration can enable the generic OS-input path safely, and the operator must change views manually. A target-client native hit-test/DPI adapter is mandatory remaining implementation.

## Safe schema example

All coordinates inside `roi`, `click` and `grid_action` are normalized to the selected captured window. `window_origin` and `window_size` are physical desktop pixels measured for the same capture calibration. Template paths must be local files under the runtime data directory; templates must not contain production images exported to a remote service.

```json
{
  "source": {
    "backend": "screencapturekit",
    "window_title": "Exact Monitoring Client Window",
    "expected_size": [1600, 900],
    "layout_version": 3,
    "calibrated": true
  },
  "switching": {
    "enabled": true,
    "calibrated": true,
    "detail_seconds": 15,
    "grid_seconds": 30,
    "blind_budget_seconds": 180,
    "verification": {
      "source_size": [1600, 900],
      "window_origin": [120, 80],
      "grid_action": [0.94, 0.06],
      "focus_guard": {
        "window_title": "Exact Monitoring Client Window",
        "window_origin": [120, 80],
        "window_size": [1600, 900]
      },
      "camera_heartbeats": {
        "CAM01": {
          "grid_roi": [0.72, 0.01, 0.26, 0.10],
          "detail_roi": [0.86, 0.01, 0.12, 0.05],
          "min_pixel_delta": 1.0,
          "max_unchanged_seconds": 10
        }
      },
      "grid_identities": {
        "CAM01": {
          "roi": [0.01, 0.01, 0.05, 0.03],
          "template_path": "/local/runtime/calibration/CAM01-grid-label.png",
          "min_score": 0.92,
          "min_margin": 0.02,
          "expected_label": "CAM01"
        }
      },
      "detail_identities": {
        "CAM01": {
          "roi": [0.01, 0.01, 0.08, 0.04],
          "template_path": "/local/runtime/calibration/CAM01-detail-label.png",
          "min_score": 0.92,
          "min_margin": 0.02,
          "expected_label": "CAM01",
          "layout_roi": [0.80, 0.00, 0.18, 0.08],
          "layout_template_path": "/local/runtime/calibration/CAM01-detail-layout-marker.png",
          "min_layout_score": 0.97
        }
      }
    }
  },
  "cameras": [
    {
      "id": "CAM01",
      "view_action": {"click": [0.125, 0.20]}
    }
  ]
}
```

`grid_identities` and `detail_identities` must contain exactly one entry for every enabled camera. The example abbreviates those mappings; a ten-camera configuration must contain CAM01 through CAM10. Extra, missing, blank, identical or ambiguous templates fail closed.

## What each proof means

- **Grid identity:** Every enabled camera-label ROI is compared with every saved grid label template. The expected camera must be the unique best match, meet `min_score`, and beat the runner-up by `min_margin`. This detects reordered tiles and same-size layout changes.
- **Detail identity:** The enlarged view must first match the selected camera label. It must also match `layout_template_path` at `layout_roi`. The second check must be a marker unique to the enlarged layout, so the ordinary grid cannot be accepted merely because CAM01 appears in both views.
- **Per-camera heartbeat:** `camera_heartbeats` must contain every enabled camera. `grid_roi` is normalized inside that camera's grid crop; `detail_roi` is normalized inside its verified full detail view. Each ROI must cover that camera's reliable changing source clock or playback indicator, not ordinary scene motion. A first frame is unproven. Only that camera becomes available after its ROI changes by `min_pixel_delta`; only that camera becomes unavailable after `max_unchanged_seconds`. A changing CAM01/client clock can never keep a frozen CAM02 observable.
- **Focus guard and current limitation:** The generic adapter can compare the foreground title, origin and size, but that does not prove the OS hit target under DPI scaling, client-area offsets or overlays. RuntimeController therefore keeps automatic click dispatch unconditionally unavailable in V1 before any click. No calibration/config value overrides this. The operator must change views manually. A future target-client native adapter must add hit-testing and DPI-safe coordinate proof, then retain both the pre-click and post-click image checks described here.
- **View actions:** Per-camera `view_action.click` and `grid_action` use the single serialized switching policy. Manual requests enter the same queue. Maximum detail dwell, minimum grid interval, blindness budget and verified grid return still apply.
- **Capture-time freshness:** ScreenCaptureKit carries the native frame timestamp and converts it to the Python monotonic clock. WGC timestamps the frame callback. A post-click readback is accepted only when its capture timestamp is later than the click, so a delayed pre-click queue frame cannot confirm the action.

## Failure behavior

An incomplete identity/heartbeat setup does not trigger scripted detections or unverified crop recording. The UI receives `mapping_invalid` or `unavailable` health plus an error reason; active events receive explicit evidence gaps. A verified detail view records the full frame only as supplementary evidence for that selected camera, marks other cameras blind, and suspends grid-relative geometric rules until the verified grid returns. Calls to `request_view`/`return_grid` currently return the explicit native hit-testing/DPI capability reason and send no OS input.

Windows display capture is available only when `source.backend="display"` is explicitly selected. `auto` and `wgc` require an exact window title and never silently fall back to the whole display.
