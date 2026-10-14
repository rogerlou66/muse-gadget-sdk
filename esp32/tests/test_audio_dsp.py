"""Signal-level tests for production conversion, gain and speaker protection."""
import ctypes as C
import math
from pathlib import Path
import subprocess
import tempfile
import unittest

ROOT=Path(__file__).resolve().parents[1]

class Fir(C.Structure):
    _fields_=[('history',C.c_float*63),('at',C.c_uint),('phase',C.c_uint)]
class Gain(C.Structure):
    _fields_=[('gain',C.c_float),('hp_x',C.c_float),('hp_y',C.c_float),('fade',C.c_float),('silent_frames',C.c_uint)]

def tone(rate,hz,n,amp=5000):
    return [round(amp*math.sin(2*math.pi*hz*i/rate)) for i in range(n)]
def rms(data):
    return math.sqrt(sum(x*x for x in data)/len(data))

def amplitude(data,rate,hz):
    c=sum(x*math.cos(2*math.pi*hz*i/rate) for i,x in enumerate(data))
    s=sum(x*math.sin(2*math.pi*hz*i/rate) for i,x in enumerate(data))
    return 2*math.hypot(c,s)/len(data)

class AudioDsp(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.tmp=tempfile.TemporaryDirectory();cls.addClassCleanup(cls.tmp.cleanup)
        binary=Path(cls.tmp.name)/'dsp.dylib'
        subprocess.run(['cc','-std=c11','-shared','-fPIC','-O2',str(ROOT/'components/muse/muse_audio_dsp.c'),'-lm','-o',str(binary)],check=True,capture_output=True)
        cls.lib=C.CDLL(str(binary))
        for name in ['muse_audio_decimate48','muse_audio_interpolate16']:
            f=getattr(cls.lib,name);f.argtypes=[C.POINTER(Fir),C.POINTER(C.c_int16),C.c_size_t,C.POINTER(C.c_int16)];f.restype=C.c_size_t
        cls.lib.muse_audio_input_gain.argtypes=[C.POINTER(Gain),C.POINTER(C.c_int16),C.c_size_t]
        cls.lib.muse_audio_output_process.argtypes=[C.POINTER(Gain),C.POINTER(C.c_int16),C.c_size_t,C.c_bool]

    def convert(self,name,data,parts=None):
        state=Fir();out=[];pos=0
        for size in parts or [len(data)]:
            buf=(C.c_int16*size)(*data[pos:pos+size]);result=(C.c_int16*(size*3+8))(*([12345]*(size*3+8)))
            got=getattr(self.lib,name)(C.byref(state),buf,size,result)
            self.assertTrue(all(x==12345 for x in result[got:]))
            out.extend(result[:got]);pos+=size
        self.assertEqual(pos,len(data));return out

    def test_capture_frame_count_and_fragment_continuity(self):
        data=tone(48000,1000,1200)
        whole=self.convert('muse_audio_decimate48',data)
        split=self.convert('muse_audio_decimate48',data,[1,2,77,320,800])
        self.assertEqual(len(whole),400);self.assertEqual(whole,split)

    def test_capture_rejects_aliasing(self):
        low=self.convert('muse_audio_decimate48',tone(48000,1000,4800))[128:]
        alias=self.convert('muse_audio_decimate48',tone(48000,12000,4800))[128:]
        self.assertGreater(rms(low),3300)
        self.assertLess(rms(alias)/rms(low),.02)

    def test_upsampling_continuity_and_image_rejection(self):
        data=tone(16000,1000,1600)
        out=self.convert('muse_audio_interpolate16',data)
        self.assertEqual(len(out),4800)
        self.assertEqual(out,self.convert('muse_audio_interpolate16',data,[3,1,320,1276]))
        stable=out[256:]
        self.assertGreater(amplitude(stable,48000,1000),4700)
        self.assertLess(amplitude(stable,48000,15000),100)

    def test_agc_does_not_amplify_quiet_noise(self):
        state=Gain()
        for _ in range(100):
            buf=(C.c_int16*160)(*([100]*160))
            self.lib.muse_audio_input_gain(C.byref(state),buf,160)
        self.assertEqual(list(buf),[100]*160)
        self.assertAlmostEqual(state.gain,1)

    def test_agc_boost_is_bounded_and_loud_input_cannot_wrap(self):
        state=Gain()
        for _ in range(100):
            buf=(C.c_int16*160)(*tone(16000,1000,160,1000))
            self.lib.muse_audio_input_gain(C.byref(state),buf,160)
        self.assertGreater(rms(buf),1800);self.assertLessEqual(state.gain,3)
        loud=(C.c_int16*160)(*([32767,-32768]*80))
        self.lib.muse_audio_input_gain(C.byref(state),loud,160)
        self.assertLess(max(abs(x) for x in loud),31701)
        self.assertTrue(all(loud[i]>0 and loud[i+1]<0 for i in range(0,160,2)))

    def output(self,data,mute=False,state=None):
        state=state or Gain();buf=(C.c_int16*len(data))(*data)
        self.lib.muse_audio_output_process(C.byref(state),buf,len(data),mute)
        return list(buf),state

    def test_output_keeps_high_frequency_music_and_reduces_rumble(self):
        high,_=self.output(tone(48000,10000,12000))
        rumble,_=self.output(tone(48000,20,12000))
        self.assertGreater(rms(high[1000:]),3300)
        self.assertLess(rms(rumble[1000:]),1300)

    def test_output_removes_dc_and_mute_fades_to_silence(self):
        dc,state=self.output([10000]*10000)
        self.assertLess(max(abs(x) for x in dc[-320:]),2)
        _,state=self.output(tone(48000,1000,960),state=state)
        muted,state=self.output(tone(48000,1000,4800),mute=True,state=state)
        self.assertEqual(muted[-320:],[0]*320)
        silence,state=self.output([0]*10000,state=state)
        self.assertLessEqual(state.silent_frames,480)
