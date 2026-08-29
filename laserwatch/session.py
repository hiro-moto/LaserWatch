from __future__ import annotations
import csv,json,logging,time
from dataclasses import asdict
from datetime import datetime
from pathlib import Path
from typing import Optional
from .models import BeamResult,CameraSettings
log=logging.getLogger(__name__)
class SessionLogger:
    def __init__(self,flush_every_rows=30,flush_interval_s=1.0):
        self.dir:Optional[Path]=None; self._fp=None; self._writer=None
        self.flush_every_rows=max(1,int(flush_every_rows)); self.flush_interval_s=max(0.1,float(flush_interval_s)); self.rows_written=0; self._rows_since_flush=0; self._last_flush_time=0.0
    @property
    def active(self): return self._fp is not None
    def start(self,base_dir,camera):
        self.stop()
        try:
            stamp=datetime.now().strftime('%Y_%m_%d_%H_%M_%S'); safe=''.join(c if c.isalnum() or c in '-_' else '_' for c in camera.name)
            self.dir=Path(base_dir)/f'{stamp}_{safe}'; self.dir.mkdir(parents=True,exist_ok=False)
            (self.dir/'config.json').write_text(json.dumps(asdict(camera),ensure_ascii=False,indent=2),encoding='utf-8')
            self._fp=(self.dir/'measurement.csv').open('w',newline='',encoding='utf-8'); fields=list(BeamResult.__dataclass_fields__.keys()); self._writer=csv.DictWriter(self._fp,fieldnames=fields); self._writer.writeheader(); self._fp.flush()
            self.rows_written=0; self._rows_since_flush=0; self._last_flush_time=time.monotonic(); log.info('Measurement session started: %s',self.dir); return self.dir
        except Exception:
            log.exception('Failed to start measurement session'); self.stop(); raise
    def write(self,result):
        if self._writer is None or self._fp is None: return
        try:
            self._writer.writerow(result.asdict()); self.rows_written+=1; self._rows_since_flush+=1; now=time.monotonic()
            if self._rows_since_flush>=self.flush_every_rows or now-self._last_flush_time>=self.flush_interval_s:
                self._fp.flush(); self._rows_since_flush=0; self._last_flush_time=now
        except Exception:
            log.exception('Measurement write failed'); self.stop(); raise
    def flush(self):
        if self._fp is not None: self._fp.flush(); self._rows_since_flush=0; self._last_flush_time=time.monotonic()
    def stop(self):
        if self._fp is not None:
            try: self._fp.flush(); self._fp.close()
            except Exception: log.exception('Failed to close session file')
        self._fp=None; self._writer=None
