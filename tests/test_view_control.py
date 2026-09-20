import numpy as np
import cv2

from factory_monitor.capture.control import CalibratedViewController, perceptual_signature, verify_grid_mapping


class _Clicks:
    def __init__(self):
        self.points = []

    def click(self, x, y):
        self.points.append((x, y))


def test_click_control_requires_calibration_and_readback(tmp_path):
    clicks = _Clicks()
    image = np.zeros((100, 200, 3), dtype=np.uint8)
    cv2.putText(image, "CAM01", (10, 55), cv2.FONT_HERSHEY_SIMPLEX, 1, (255, 255, 255), 2)
    template = tmp_path / "cam01.png"
    cv2.imwrite(str(template), image)
    verification = {
        "source_size": [200, 100],
        "detail_identities": {"CAM01": {"roi": [0, 0, 1, 1], "template_path": str(template), "layout_roi": [0, 0, 1, 1], "layout_template_path": str(template)}},
        "grid_identities": {"CAM01": {"roi": [0, 0, 1, 1], "template_path": str(template)}},
    }
    controller = CalibratedViewController(
        source_size=(200, 100),
        actions={"CAM01": {"click": [0.25, 0.5]}},
        verification=verification,
        capture=lambda _after: image,
        clicker=clicks,
        calibrated=True,
    )

    result = controller.request_view("CAM01")

    assert result["ok"] is True
    assert clicks.points == [(50, 50)]


def test_dimension_change_invalidates_mapping_before_click():
    clicks = _Clicks()
    controller = CalibratedViewController(
        source_size=(201, 100),
        actions={"CAM01": {"click": [0.25, 0.5]}},
        verification={"source_size": [200, 100], "camera_signatures": {}},
        capture=lambda _after: np.zeros((100, 201, 3), dtype=np.uint8),
        clicker=clicks,
        calibrated=True,
    )

    result = controller.request_view("CAM01")

    assert result == {"ok": False, "reason": "source dimensions changed; mapping invalid"}
    assert clicks.points == []


def test_live_grid_mapping_requires_all_configured_camera_readbacks():
    image = np.zeros((100, 200, 3), dtype=np.uint8)
    signature = perceptual_signature(image)
    configured = [{"id": "CAM01", "enabled": True}, {"id": "CAM02", "enabled": True}]

    ok, reason = verify_grid_mapping(
        image,
        (200, 100),
        {"CAM01": {"roi": [0, 0, 0.5, 1], "signature": signature}},
        configured,
    )

    assert ok is False
    assert "CAM02" in reason


def test_grid_identity_rejects_wrong_one_digit_camera_label(tmp_path):
    cam01 = np.zeros((40, 140, 3), dtype=np.uint8)
    cam02 = np.zeros((40, 140, 3), dtype=np.uint8)
    cv2.putText(cam01, "CAM01", (4, 30), cv2.FONT_HERSHEY_SIMPLEX, 1, (255, 255, 255), 2)
    cv2.putText(cam02, "CAM02", (4, 30), cv2.FONT_HERSHEY_SIMPLEX, 1, (255, 255, 255), 2)
    path01, path02 = tmp_path / "CAM01.png", tmp_path / "CAM02.png"
    cv2.imwrite(str(path01), cam01)
    cv2.imwrite(str(path02), cam02)
    observed = np.concatenate([cam02, cam01], axis=1)
    specs = {
        "CAM01": {"roi": [0, 0, 0.5, 1], "template_path": str(path01), "min_score": 0.9, "min_margin": 0.02},
        "CAM02": {"roi": [0.5, 0, 0.5, 1], "template_path": str(path02), "min_score": 0.9, "min_margin": 0.02},
    }

    ok, reason = verify_grid_mapping(
        observed,
        (280, 40),
        specs,
        [{"id": "CAM01", "enabled": True}, {"id": "CAM02", "enabled": True}],
    )

    assert ok is False
    assert "CAM01" in reason
    assert "identity" in reason
