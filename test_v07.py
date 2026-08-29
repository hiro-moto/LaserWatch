import math,tempfile
from pathlib import Path
from types import SimpleNamespace
import numpy as np
from laserwatch.diagnostics import fourcc_to_text,human_bytes,build_diagnostic_payload,diagnostics_to_text
from laserwatch.models import CameraSettings,AnalysisSettings
from laserwatch.profile_exchange import build_profile_payload,read_profile_json,write_profile_json,identity_match
from laserwatch.report_export import build_html_report,write_html_report
from laserwatch.session_stats import StreamingBeamStats
from laserwatch.summary_export import build_measurement_summary

stats=StreamingBeamStats();xs=np.array([0.,1.,-1.,2.,-2.]);ys=np.array([1.,0.,-1.,1.5,-1.5]);ints=np.array([100.,105.,95.,110.,90.])
for i in range(len(xs)):
    stats.add_result(SimpleNamespace(cx_um=xs[i],cy_um=ys[i],d4sigma_x_um=100.+i,d4sigma_y_um=120.+2*i,integrated=ints[i]))
s=stats.statistics();assert s['count']==len(xs);assert abs(s['mean_x']-np.mean(xs))<1e-12;assert abs(s['sigma_x']-np.std(xs))<1e-12;assert abs(s['radial_rms']-math.sqrt(np.var(xs)+np.var(ys)))<1e-12;assert abs(s['ptp_x']-np.ptp(xs))<1e-12
camera=CameraSettings(name='TestCam',friendly_name='TestCam',device_path=r'\\?\usb#vid_1234&pid_5678#ABC',vid='1234',pid='5678',instance_id='ABC',exposure_us=7812.5,pixel_size_um_x=3.45,pixel_size_um_y=3.45)
analysis=AnalysisSettings(roi=(10,20,300,200));payload=build_profile_payload(camera,analysis,{'roi_enabled':True,'raw_segment_mb':2048})
with tempfile.TemporaryDirectory() as td:
    path=Path(td)/'profile.json';write_profile_json(path,payload);loaded=read_profile_json(path);assert loaded['analysis_settings']['roi']==[10,20,300,200];assert identity_match(camera,loaded)
other=CameraSettings(friendly_name='Other',device_path=r'\\?\usb#vid_9999&pid_8888#XYZ');assert not identity_match(other,payload)
mjpg=ord('M')|(ord('J')<<8)|(ord('P')<<16)|(ord('G')<<24);assert fourcc_to_text(mjpg)=='MJPG';assert 'MiB' in human_bytes(10*1024**2)
diag=build_diagnostic_payload(camera,{'backend':'DirectShow','actual_width':1920},{},acquisition={'acquisition_fps':30.0},analysis={'analysis_fps':29.5,'frames_dropped':2});txt=diagnostics_to_text(diag);assert '[camera]' in txt and 'DirectShow' in txt
summary=build_measurement_summary(camera,s,baseline={'x_um':0.0,'y_um':0.0},extra={'raw_frames_written':123,'raw_frames_dropped':1})
arrays={'t':[0.,1.,2.],'x':[0.,.5,-.5],'y':[0.,-.2,.2],'d4x':[100.,101.,99.],'d4y':[120.,121.,119.],'intensity':[1000.,1010.,990.]};psd={'f':[0.,1.,2.],'psd_x':[0.,1.,.2],'psd_y':[0.,.3,.1]}
html=build_html_report(summary,arrays,psd);assert 'LaserWatch Measurement Report' in html and '<svg' in html
with tempfile.TemporaryDirectory() as td:
    rp=write_html_report(Path(td)/'report.html',summary,arrays,psd);assert rp.exists() and rp.stat().st_size>1000
print('v0.7 streaming stats, profile exchange, diagnostics, and HTML report tests: PASS')
