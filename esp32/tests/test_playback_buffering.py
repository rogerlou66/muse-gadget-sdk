"""Live jitter buffering must not strand short clips, tails, or a new turn."""
from pathlib import Path
import subprocess
import tempfile
import unittest

class PlaybackBuffering(unittest.TestCase):
    def test_streaming_gaps_short_clips_and_cancelled_turn(self):
        root=Path(__file__).resolve().parents[1]
        with tempfile.TemporaryDirectory() as d:
            p=Path(d)
            (p/'gate.c').write_text('''
#include <assert.h>
#include "muse_playback_gate.h"
int main(void) {
    muse_playback_gate_t gate={0};
    assert(!muse_playback_ready(&gate,1,960,true,48000));
    assert(!muse_playback_ready(&gate,1,38399,true,48000));
    assert(muse_playback_ready(&gate,1,38400,true,48000));
    assert(muse_playback_ready(&gate,1,960,true,48000));
    assert(!muse_playback_ready(&gate,1,0,true,48000));
    assert(gate.refills==1);
    assert(!muse_playback_ready(&gate,1,48000,true,48000));
    assert(muse_playback_ready(&gate,1,72000,true,48000));
    assert(muse_playback_ready(&gate,1,240,false,48000));
    assert(!muse_playback_ready(&gate,2,960,true,48000));
    assert(gate.refills==0 && !gate.started);
    assert(muse_playback_ready(&gate,2,960,false,48000));
    assert(muse_playback_ready(&gate,2,0,false,48000));
}
''')
            subprocess.run(['cc','-std=c11','-Wall','-Werror','-I'+str(root/'components/muse'),
                str(p/'gate.c'),'-o',str(p/'gate')],check=True)
            subprocess.run([str(p/'gate')],check=True)
