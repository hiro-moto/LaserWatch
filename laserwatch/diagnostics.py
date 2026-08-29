from __future__ import annotations
from dataclasses import dataclass
from pathlib import Path
import os, shutil, time

def fourcc_to_text(value):
    try:
        v=int(round(float(value))); chars=[chr((v>>(8*i))&0xFF) for i in range(4)]; text=''.join(chars)
        return text if all(32<=ord(c)<=126 for c in text) else f'0x{v:08X}'
    except Exception: return 'unknown'

def human_bytes(value):
    if value is None: return '-'
    try: n=float(value)
    except Exception: return '-'
    units=['B','KiB','MiB','GiB','TiB']; i=0
    while abs(n)>=1024.0 and i<len(units)-1: n/=1024.0; i+=1
    return f'{n:.2f} {units[i]}'

def disk_free_bytes(path):
    p=Path(path); p.mkdir(parents=True,exist_ok=True); return int(shutil.disk_usage(p).free)

def process_rss_bytes():
    try:
        import psutil
        return int(psutil.Process(os.getpid()).memory_info().rss)
    except Exception: return None

@dataclass
class RateMeter:
    last_count:int=0; last_time:float=0.0; rate:float=0.0
    def update(self,count,now=None):
        now=time.monotonic() if now is None else float(now); count=int(count)
        if self.last_time>0 and now>self.last_time: self.rate=max(0.0,(count-self.last_count)/(now-self.last_time))
        self.last_count=count; self.last_time=now; return self.rate

def build_diagnostic_payload(camera,capture_info,exposure_info=None,acquisition=None,analysis=None,recording=None,runtime=None):
    payload={'camera':{
        'name':getattr(camera,'name',''),'friendly_name':getattr(camera,'friendly_name',''),'device_path':getattr(camera,'device_path',''),
        'vid':getattr(camera,'vid',''),'pid':getattr(camera,'pid',''),'instance_id':getattr(camera,'instance_id',''),
        'requested_width':getattr(camera,'width',None),'requested_height':getattr(camera,'height',None),'requested_fps':getattr(camera,'fps',None),
        'pixel_size_um_x':getattr(camera,'pixel_size_um_x',None),'pixel_size_um_y':getattr(camera,'pixel_size_um_y',None),'magnification':getattr(camera,'magnification',None),
    },'capture':dict(capture_info or {}),'acquisition':dict(acquisition or {}),'analysis':dict(analysis or {}),'recording':dict(recording or {}),'runtime':dict(runtime or {})}
    if exposure_info is not None:
        try:
            payload['exposure_control']={'min_raw':exposure_info.min_raw,'max_raw':exposure_info.max_raw,'step_raw':exposure_info.step_raw,
                'default_raw':exposure_info.default_raw,'current_raw':exposure_info.current_raw,'min_us':exposure_info.min_us,'max_us':exposure_info.max_us,
                'current_us':exposure_info.current_us,'supports_auto':exposure_info.supports_auto,'supports_manual':exposure_info.supports_manual}
        except Exception: payload['exposure_control']={'error':'serialization failed'}
    return payload

def diagnostics_to_text(payload):
    lines=[]
    for section,values in payload.items():
        lines.append(f'[{section}]')
        if isinstance(values,dict): lines.extend(f'{k}: {v}' for k,v in values.items())
        else: lines.append(str(values))
        lines.append('')
    return '\n'.join(lines).rstrip()+'\n'
