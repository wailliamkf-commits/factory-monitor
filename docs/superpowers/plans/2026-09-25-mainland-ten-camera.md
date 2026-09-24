# Mainland ten-camera implementation plan

Goal: 可执行的共享检测、本地视觉复核与真实边界清楚的双平台监控候选。
Spec: ../specs/2026-09-25-mainland-ten-camera-design.md

- [ ] T1 Shared detector (inference.py, runtime.py, inference/runtime tests): regression first for CUDA auto, exactly one network load and independent camera tracking; share YOLO predict batch, each camera ByteTrack; reset unobservable streams. Existing detect wrapper remains compatible. Only owning worker edits these files.
- [ ] T2 Capture/alerts (windows.py, gui/app.py, native/GUI tests): first demonstrate dequeue wall-clock mismatch and absent supported/dismissed notification; preserve capture wall/monotonic values and deliver clickable existing event notifications for completed review.
- [ ] T3 Runnable measurement (new scripts and tests): local dependencies/model preflight, fixed ten-camera detector batch latency, timed local Qwen burst with queue-inclusive deadlines and error accounting; no camera imagery uploaded.
- [ ] T4 Integration: baseline and focused regression, actual existing local model inference, review source/license facts and hardware budget, concentrated independent review, draft GitHub PR readback.

Ruling: 用户已授予技术架构与执行权，计划由主控自主定案执行，不重复要求批准。隔离工作树不改原项目未提交资料。不改变旧15秒验收阈值。当前基础环境Qt平台插件缺失，先诊断修复隔离环境，不把跳过GUI算全套通过。
