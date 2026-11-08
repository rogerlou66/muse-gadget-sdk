"""BOX0 remote volume uses the same settings as its local menu and codec."""
from pathlib import Path
import subprocess
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]


class MuseVolumeCommand(unittest.TestCase):
    def test_query_set_and_invalid_inputs(self):
        source = (ROOT / 'main/muse_glue.c').read_text()
        start = source.index('cJSON *muse_glue_voice_configure(')
        end = source.index('\n}\n', start) + 3
        code = r'''
#include <assert.h>
#include <stdbool.h>
#include <string.h>
#include "cJSON.h"
#define ESP_LOGI(...) ((void)0)
#define ESP_LOGW(...) ((void)0)
static int current=55, writes;
static bool speaker=false;
static void muse_settings_set_volume(int v) { current=v; writes++; }
static int muse_settings_volume(void) { return current; }
static bool muse_settings_speaker_on(void) { return speaker; }
''' + source[start:end] + r'''
static void check(const char *text, bool ok, int expected) {
    cJSON *p=text ? cJSON_Parse(text) : NULL;
    assert(!text || p);
    int before=writes;
    cJSON *r=muse_glue_voice_configure(p); assert(r);
    assert(cJSON_IsTrue(cJSON_GetObjectItem(r,"ok"))==ok);
    if(ok) {
        assert(cJSON_GetObjectItem(r,"volume")->valueint==expected);
        assert(cJSON_IsTrue(cJSON_GetObjectItem(r,"speaker"))==speaker);
        if(!p || !cJSON_GetObjectItem(p,"volume")) assert(writes==before);
        else assert(writes==before+1);
    } else {
        assert(writes==before);
        assert(!strcmp(cJSON_GetStringValue(cJSON_GetObjectItem(cJSON_GetObjectItem(r,"error"),"code")),"invalid_params"));
    }
    assert(current==expected);
    cJSON_Delete(r); cJSON_Delete(p);
}
int main(void) {
    check(NULL,true,55); check("{}",true,55);
    check("{\"volume\":60}",true,60);
    check("{\"volume\":0}",true,0);
    speaker=true; check("{\"volume\":100}",true,100);
    const char *bad[]={"[]","null","10","{\"volume\":-1}","{\"volume\":101}",
        "{\"volume\":59.5}","{\"volume\":\"60\"}","{\"volume\":null}","{\"volume\":true}","{\"volume\":1e309}"};
    for(unsigned i=0;i<sizeof(bad)/sizeof(*bad);i++) check(bad[i],false,100);
    return 0;
}
'''
        jsondir = ROOT / 'managed_components/espressif__cjson/cJSON'
        with tempfile.TemporaryDirectory() as tmp:
            p = Path(tmp)
            (p / 'volume.c').write_text(code)
            result = subprocess.run(['cc', '-std=c11', '-fsanitize=address,undefined',
                '-I'+str(jsondir), str(p/'volume.c'), str(jsondir/'cJSON.c'),
                '-o', str(p/'volume')], capture_output=True, text=True)
            self.assertEqual(result.returncode, 0, result.stderr)
            result = subprocess.run([str(p/'volume')], capture_output=True, text=True)
            self.assertEqual(result.returncode, 0, result.stderr)

    def test_box0_registers_and_routes_command_without_homehub_voice(self):
        register = (ROOT / 'main/noise_control.cpp').read_text()
        app = (ROOT / 'main/app.c').read_text()
        self.assertIn('#if CONFIG_HOMEHUB_VOICE || CONFIG_MUSE_BOARD_ATK_DNESP32S3_BOX0\n'
                      '    cJSON *volume_optional', register)
        self.assertIn('#if CONFIG_MUSE_BOARD_ATK_DNESP32S3_BOX0\n'
                      '    if (strcmp(command, "voice.configure") == 0) {\n'
                      '        return muse_glue_voice_configure(params);', app)
