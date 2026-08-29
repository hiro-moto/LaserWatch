from __future__ import annotations
import math

class RunningScalar:
    def __init__(self): self.reset()
    def reset(self):
        self.n=0; self.mean=0.0; self.m2=0.0
        self.minimum=math.inf; self.maximum=-math.inf
    def add(self,value):
        try: x=float(value)
        except Exception: return
        if not math.isfinite(x): return
        self.n += 1
        d=x-self.mean; self.mean += d/self.n; d2=x-self.mean; self.m2 += d*d2
        self.minimum=min(self.minimum,x); self.maximum=max(self.maximum,x)
    @property
    def variance(self): return self.m2/self.n if self.n else float('nan')
    @property
    def sigma(self):
        v=self.variance
        return math.sqrt(max(v,0.0)) if math.isfinite(v) else float('nan')
    @property
    def ptp(self): return self.maximum-self.minimum if self.n else float('nan')

class StreamingBeamStats:
    def __init__(self): self.reset()
    def reset(self):
        self.x=RunningScalar(); self.y=RunningScalar(); self.d4x=RunningScalar(); self.d4y=RunningScalar(); self.intensity=RunningScalar(); self.valid_xy_count=0
    def add_result(self,result):
        try: x=float(result.cx_um); y=float(result.cy_um)
        except Exception: x=y=float('nan')
        if math.isfinite(x) and math.isfinite(y):
            self.x.add(x); self.y.add(y); self.valid_xy_count += 1
        self.d4x.add(getattr(result,'d4sigma_x_um',float('nan')))
        self.d4y.add(getattr(result,'d4sigma_y_um',float('nan')))
        self.intensity.add(getattr(result,'integrated',float('nan')))
    def statistics(self):
        if self.valid_xy_count==0: return {'count':0}
        radial_rms=math.sqrt(max(self.x.variance,0.0)+max(self.y.variance,0.0))
        int_cv=(100.0*self.intensity.sigma/abs(self.intensity.mean) if self.intensity.n and abs(self.intensity.mean)>1e-15 else float('nan'))
        return {
            'count':int(self.valid_xy_count),'mean_x':self.x.mean,'mean_y':self.y.mean,
            'sigma_x':self.x.sigma,'sigma_y':self.y.sigma,'ptp_x':self.x.ptp,'ptp_y':self.y.ptp,'radial_rms':radial_rms,
            'd4x_mean':self.d4x.mean if self.d4x.n else float('nan'),'d4x_sigma':self.d4x.sigma,'d4x_ptp':self.d4x.ptp,
            'd4y_mean':self.d4y.mean if self.d4y.n else float('nan'),'d4y_sigma':self.d4y.sigma,'d4y_ptp':self.d4y.ptp,
            'intensity_mean':self.intensity.mean if self.intensity.n else float('nan'),'intensity_sigma':self.intensity.sigma,
            'intensity_ptp':self.intensity.ptp,'intensity_cv_percent':int_cv,
        }
