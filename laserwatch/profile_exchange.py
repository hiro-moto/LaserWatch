from __future__ import annotations
import json
from datetime import datetime,timezone
from pathlib import Path
PROFILE_FORMAT='LaserWatch camera profile'; PROFILE_VERSION=1

def build_profile_payload(camera,analysis,ui):
    return {'format':PROFILE_FORMAT,'profile_version':PROFILE_VERSION,'generated_at':datetime.now(timezone.utc).isoformat(),
        'camera_identity':{'friendly_name':getattr(camera,'friendly_name',''),'device_path':getattr(camera,'device_path',''),'vid':getattr(camera,'vid',''),'pid':getattr(camera,'pid',''),'instance_id':getattr(camera,'instance_id','')},
        'camera_settings':{'exposure_us':float(getattr(camera,'exposure_us',0.0)),'gain':float(getattr(camera,'gain',0.0)),'pixel_size_um_x':float(getattr(camera,'pixel_size_um_x',1.0)),'pixel_size_um_y':float(getattr(camera,'pixel_size_um_y',1.0)),'magnification':float(getattr(camera,'magnification',1.0))},
        'analysis_settings':{'roi':list(analysis.roi) if getattr(analysis,'roi',None) is not None else None,'threshold_fraction':float(getattr(analysis,'threshold_fraction',0.01)),'saturation_fraction':float(getattr(analysis,'saturation_fraction',0.98)),'low_signal_fraction':float(getattr(analysis,'low_signal_fraction',0.02)),'analysis_channel':str(getattr(analysis,'analysis_channel','AUTO')),'bit_depth_override':int(getattr(analysis,'bit_depth_override',0)),'spot_detection_enabled':bool(getattr(analysis,'spot_detection_enabled',True)),'spot_threshold_fraction':float(getattr(analysis,'spot_threshold_fraction',0.15)),'spot_min_area_px':int(getattr(analysis,'spot_min_area_px',1)),'spot_padding_px':int(getattr(analysis,'spot_padding_px',24))},
        'ui_settings':dict(ui)}

def validate_profile_payload(payload):
    if not isinstance(payload,dict): raise ValueError('Profile must be a JSON object')
    if payload.get('format')!=PROFILE_FORMAT: raise ValueError('Not a LaserWatch camera profile')
    if int(payload.get('profile_version',-1))!=PROFILE_VERSION: raise ValueError(f"Unsupported profile version: {payload.get('profile_version')}")
    cs=payload.get('camera_settings'); ans=payload.get('analysis_settings'); ui=payload.get('ui_settings')
    if not isinstance(cs,dict) or not isinstance(ans,dict) or not isinstance(ui,dict): raise ValueError('Profile is missing settings sections')
    for name in ('pixel_size_um_x','pixel_size_um_y','magnification'):
        if float(cs.get(name,0.0))<=0: raise ValueError(f'{name} must be positive')
    roi=ans.get('roi')
    if roi is not None:
        if not isinstance(roi,list) or len(roi)!=4: raise ValueError('ROI must be null or [x,y,w,h]')
        _,_,w,h=[int(v) for v in roi]
        if w<=0 or h<=0: raise ValueError('ROI width/height must be positive')
    channel=str(ans.get('analysis_channel','AUTO')).upper()
    if channel not in ('AUTO','GRAY','R','G','B'):
        raise ValueError(f'Unsupported analysis channel: {channel}')
    bits=int(ans.get('bit_depth_override',0))
    if bits not in (0,8,10,12,16):
        raise ValueError(f'Unsupported bit-depth override: {bits}')
    return payload

def write_profile_json(path,payload):
    payload=validate_profile_payload(payload); path=Path(path); path.parent.mkdir(parents=True,exist_ok=True); path.write_text(json.dumps(payload,ensure_ascii=False,indent=2),encoding='utf-8'); return path

def read_profile_json(path): return validate_profile_payload(json.loads(Path(path).read_text(encoding='utf-8')))

def identity_match(current_camera,payload):
    imported=payload.get('camera_identity') or {}; cp=getattr(current_camera,'device_path','') or ''; ip=imported.get('device_path','') or ''
    if cp and ip: return cp==ip
    ci=getattr(current_camera,'instance_id','') or ''; ii=imported.get('instance_id','') or ''
    if ci and ii: return ci==ii
    return ((getattr(current_camera,'vid','') or '')==(imported.get('vid','') or '') and (getattr(current_camera,'pid','') or '')==(imported.get('pid','') or '') and (getattr(current_camera,'friendly_name','') or '')==(imported.get('friendly_name','') or ''))
