"""Text/ASR requests retain the registered device identity for follow-up tools."""
from pathlib import Path
import subprocess
import tempfile
import unittest

ROOT=Path(__file__).resolve().parents[1]

class DeviceOrigin(unittest.TestCase):
    def test_chat_body_device_identity_and_optional_fallback(self):
        src=(ROOT/'components/muse/muse_chat_session.cpp').read_text()
        fn=src[src.index('static void send_chat('):src.index('static void post_chat(')]
        jsondir=ROOT/'managed_components/espressif__cjson/cJSON'
        code='''
#include <cassert>
#include <cstdint>
#include <cstring>
#include "cJSON.h"
#include "muse_chat_priv.h"
enum {P_WAIT_REPLY,K_CHAT};
#define CHAT_PART 4096
static struct {bool image_requested,chat_posted;int chat_id,phase;size_t body_sent;int64_t chat_us,last_event_us;} s_turn;
static const char *node;
static char received[4096];
static const char *muse_link_node_id() {return node;}
static int64_t now_us() {return 1;}
static int open_stream(int,const char *,const char *,const char *,const char *,const char *body,bool) {
    assert(body);snprintf(received,sizeof(received),"%s",body);return 1;
}
static bool send_body(int,const uint8_t *,size_t,bool) {return true;}
static void disconnect(const char *) {assert(false);}
static void turn_fail(const char *) {assert(false);}
''' + fn + '''
int main() {
    node="homelink-test";send_chat("画一只猫","text");
    cJSON *j=cJSON_Parse(received);
    assert(!strcmp(cJSON_GetStringValue(cJSON_GetObjectItem(j,"device_id")),node));
    assert(!strcmp(cJSON_GetStringValue(cJSON_GetObjectItem(j,"message")),"画一只猫"));
    assert(!strcmp(cJSON_GetStringValue(cJSON_GetObjectItem(j,"output_modality")),"text"));
    assert(s_turn.image_requested);cJSON_Delete(j);
    node=nullptr;send_chat("现在几点","text");j=cJSON_Parse(received);
    assert(!cJSON_GetObjectItem(j,"device_id")&&!s_turn.image_requested);cJSON_Delete(j);
}
'''
        # Compile the production cJSON as C; link it into the C++ request harness.
        with tempfile.TemporaryDirectory() as d:
            p=Path(d);(p/'origin.cpp').write_text('#include <cstdio>\n'+code)
            subprocess.run(['cc','-I'+str(jsondir),'-c',str(jsondir/'cJSON.c'),'-o',str(p/'json.o')],check=True)
            subprocess.run(['c++','-std=c++17','-I'+str(jsondir),'-I'+str(ROOT/'components/muse'),
                str(p/'origin.cpp'),str(p/'json.o'),'-o',str(p/'origin')],check=True)
            subprocess.run([str(p/'origin')],check=True)
