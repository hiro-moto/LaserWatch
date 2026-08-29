# Hardware acceptance checklist

Use this checklist when validating a new UVC camera model with LaserWatch.

## Camera identification

- Friendly name is correct.
- DevicePath / VID / PID are stable across restart.
- Multiple identical cameras can be distinguished sufficiently for the intended setup.

## Capture format

- Requested and actual resolution match expectations.
- Actual FPS is acceptable.
- FOURCC is recorded from Diagnostics.
- MONO/COLOR detection is correct.
- NumPy dtype is correct.
- Effective bit depth is set correctly for uint16 containers carrying 10/12-bit data.

## Exposure and saturation

- Manual exposure changes the image monotonically.
- Actual exposure value is plausible.
- Auto Optimize Exposure converges without saturation.
- Color cameras report independent R/G/B peaks and detect single-channel saturation.

## Beam measurement

- Spot detection selects the intended optical spot.
- Fixed target does not jump to remote reflections.
- Auto ROI reacquires after loss.
- Dark subtraction changes numerical analysis as expected.
- Dark corrected display is visually plausible.
- X/Y cross-section profiles pass through the measured centroid.

## Timing and stability

- 60 s trend view remains `-60 ... 0 s` after several minutes.
- Acquisition FPS and analysis FPS remain stable.
- Analysis drop count is understood/acceptable.
- PSD frequency axis is plausible for a known modulation if available.

## Long-run test

Recommended before routine use:

- 8–24 h continuous capture/logging.
- Camera unplug/replug test.
- Low disk-space guard test for raw HDF5 recording.
- Multi-camera USB bandwidth test.
- Review `LaserWatch.log` after the run.
