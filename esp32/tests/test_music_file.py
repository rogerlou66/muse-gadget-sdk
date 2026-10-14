"""Decode generated MP3s incrementally with production file code, under sanitizers."""
from pathlib import Path
import math
import os
import shutil
import struct
import subprocess
import tempfile
import unittest
ROOT=Path(__file__).resolve().parents[1]

class MusicFile(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.tmp=tempfile.TemporaryDirectory();cls.addClassCleanup(cls.tmp.cleanup)
        cls.work=Path(cls.tmp.name)
        ffmpeg=shutil.which('ffmpeg')
        if not ffmpeg:raise unittest.SkipTest('ffmpeg required to generate MP3 fixtures')
        for rate,channels in [(8000,1),(44100,2)]:
            subprocess.run([ffmpeg,'-loglevel','error','-f','lavfi','-i',f'sine=frequency=440:sample_rate={rate}:duration=0.6','-ac',str(channels),'-codec:a','libmp3lame','-b:a','32k' if rate==8000 else '128k',str(cls.work/f'{rate}.mp3')],check=True)
        (cls.work/'main.c').write_text(r'''
#include <assert.h>
#include <stdint.h>
#include <stdio.h>
#include <stdlib.h>
#include "muse_music_file.h"
int main(int argc,char **argv) {
 assert(argc==3);size_t cap=strtoul(argv[2],NULL,10);assert(cap>0&&cap<=960);
 muse_music_file_t *f=muse_music_file_open(argv[1],48000);if(!f)return 2;
 int16_t out[976];int n;
 while(1) {
  out[cap]=12345;n=muse_music_file_read(f,out,cap);assert(out[cap]==12345&&n<=(int)cap);
  if(n<=0)break;
  assert(fwrite(out,2,n,stdout)==(size_t)n);
 }
 muse_music_file_close(f);return n<0?3:0;
}
''')
        src=ROOT/'components/muse';cls.binary=cls.work/'decode'
        subprocess.run(['cc','-std=c11','-O1','-fsanitize=address,undefined','-I'+str(src),'-I'+str(ROOT/'components/minimp3/include'),str(cls.work/'main.c'),str(src/'muse_music_file.c'),'-lm','-o',str(cls.binary)],check=True)

    def decode(self,path,frames=960):
        return subprocess.run([str(self.binary),str(path),str(frames)],capture_output=True)

    def test_stereo_and_low_rate_chunk_continuity(self):
        for rate in [8000,44100]:
            path=self.work/f'{rate}.mp3';original=path.read_bytes()
            whole=self.decode(path);split=self.decode(path,17)
            self.assertEqual(whole.returncode,0,whole.stderr.decode())
            self.assertEqual(split.returncode,0,split.stderr.decode())
            self.assertEqual(whole.stdout,split.stdout)
            samples=struct.unpack('<'+'h'*(len(whole.stdout)//2),whole.stdout)
            self.assertGreater(len(samples),48000//2)
            self.assertGreater(math.sqrt(sum(x*x for x in samples)/len(samples)),500)
            self.assertEqual(path.read_bytes(),original)

    def test_large_cover_is_skipped_and_invalid_tag_rejected(self):
        data=(self.work/'44100.mp3').read_bytes();n=1024*1024
        size=bytes([(n>>21)&127,(n>>14)&127,(n>>7)&127,n&127])
        cover=self.work/'cover.mp3';cover.write_bytes(b'ID3\x04\x00\x00'+size+b'\0'*n+data)
        original=self.decode(self.work/'44100.mp3');r=self.decode(cover)
        self.assertEqual(r.returncode,0,r.stderr.decode());self.assertEqual(r.stdout,original.stdout)
        bad=self.work/'tag.mp3';bad.write_bytes(b'ID3\x04\x00\x00'+size+b'short')
        self.assertEqual(self.decode(bad).returncode,2)

    def test_invalid_empty_missing_files_fail_without_hanging(self):
        for name,data in [('empty.mp3',b''),('noise.mp3',b'z'*100000)]:
            p=self.work/name;p.write_bytes(data);r=self.decode(p)
            self.assertEqual(r.returncode,3,r.stderr.decode())
        self.assertEqual(self.decode(self.work/'missing.mp3').returncode,2)
