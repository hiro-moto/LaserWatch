from laserwatch.windows_uvc import ExposureRange, parse_usb_identity

info = ExposureRange(
    min_raw=-13,
    max_raw=-1,
    step_raw=1,
    default_raw=-6,
    current_raw=-6,
    current_flags=2,
    capability_flags=3,
)

raw, actual = info.quantize_us(10_000.0)
assert raw == -7, (raw, actual)
assert abs(actual - 7812.5) < 1e-9

raw, actual = info.quantize_us(16_000.0)
assert raw == -6
assert abs(actual - 15625.0) < 1e-9

path = r"\\?\usb#vid_1234&pid_ABCD&mi_00#6&123456&0&0000#{some-guid}"
vid, pid, instance_id = parse_usb_identity(path)
assert vid == "1234"
assert pid == "ABCD"
assert instance_id == "6&123456&0&0000"

print("UVC exposure conversion and USB identity parsing: PASS")
