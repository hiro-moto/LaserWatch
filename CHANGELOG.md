# Changelog

## 0.8.4

- Added click-to-select **Fixed target** mode.
- Click snaps to the nearest local optical spot within a configurable radius.
- Added blue fixed-target anchor overlay.
- Fixed target remains inside a fixed ROI and does not reacquire remote reflections.
- Spot detection inside fixed ROI prefers the component nearest the selected target.

## 0.8.3

- Added live X/Y centroid cross-section Profile tab.
- Added measured and Gaussian-equivalent profile curves.
- Added on-screen overlay legend.
- Removed duplicate UVC capability query found during review.

## 0.8.2

- Fixed nanosecond timestamp transport through Qt signals.
- Fixed rolling plot time axis and apparent repeated overdraw caused by timestamp wrapping.
- Redesigned Auto ROI as SEARCHING/TRACKING/reacquisition state machine.
- Added spot detection and `BEAM_NOT_FOUND` handling.
- Added dark-corrected live display.

## 0.8.1

- Made the settings pane scrollable.
- Replaced vertically stacked plots with tabs.
- Reduced GUI minimum-size constraints for laptop displays.

## 0.8.0

- Added Mono/Color source handling.
- Added Gray/R/G/B analysis selection.
- Added RGB-safe saturation and exposure metrics.
- Added selectable effective 8/10/12/16-bit interpretation.
