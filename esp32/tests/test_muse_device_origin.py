"""Verify main-chat routing, device identity and BOX0-only spoken instructions."""
from pathlib import Path
import subprocess
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]


class DeviceOrigin(unittest.TestCase):
    def test_chat_body_device_identity_and_optional_fallback(self):
        src = (ROOT / 'components/muse/muse_chat_session.cpp').read_text()
        fn = src[src.index('static void send_chat('):src.index('static void post_chat(')]
        jsondir = ROOT / 'managed_components/espressif__cjson/cJSON'
        code = r'''
#include <cassert>
#include <cstdint>
#include <cstdio>
#include <cstdlib>
#include <cstring>
#include <string>
#include "cJSON.h"
#include "muse_chat_priv.h"
enum {P_WAIT_REPLY,K_CHAT};
#define CHAT_PART 4096
#define MUSE_BIG_CAPS 0
static struct {bool image_requested,chat_posted;int chat_id,phase;size_t body_sent;int64_t chat_us,last_event_us;} s_turn;
static const char *node;
static std::string received;
static int requests, chunks, failures;
static bool fail_prefix;
static const char *muse_link_node_id() {return node;}
static void *heap_caps_malloc(size_t n,int) {return fail_prefix ? nullptr : malloc(n);}
static int64_t now_us() {return 1;}
static int open_stream(int,const char *verb,const char *path,const char *,const char *,const char *body,bool end) {
    assert(!strcmp(verb,"POST")&&!strcmp(path,"/chat/stream"));
    requests++;received=body?body:"";assert(end==bool(body));return 1;
}
static bool send_body(int,const uint8_t *bytes,size_t n,bool) {
    assert(n<=CHAT_PART);chunks++;received.append(reinterpret_cast<const char *>(bytes),n);return true;
}
static void disconnect(const char *) {}
static void turn_fail(const char *) {failures++;}
''' + fn + r'''
static void verify(const char *original) {
    cJSON *j=cJSON_Parse(received.c_str());assert(j);
    const char *message=cJSON_GetStringValue(cJSON_GetObjectItem(j,"message"));assert(message);
#if CONFIG_MUSE_BOARD_ATK_DNESP32S3_BOX0
    assert(!strncmp(message,"【仅本次设备回复的表达要求】",strlen("【仅本次设备回复的表达要求】")));
    assert(strstr(message,"不要保存为全局偏好"));
    const char *raw=strstr(message,"【用户原话】\n");assert(raw);
    assert(!strcmp(raw+strlen("【用户原话】\n"),original));
#else
    assert(!strcmp(message,original));
#endif
    assert(!cJSON_GetObjectItem(j,"session_id")&&!cJSON_GetObjectItem(j,"system_prompt"));
    assert(!strcmp(cJSON_GetStringValue(cJSON_GetObjectItem(j,"output_modality")),"text"));
    if(node)assert(!strcmp(cJSON_GetStringValue(cJSON_GetObjectItem(j,"device_id")),node));
    else assert(!cJSON_GetObjectItem(j,"device_id"));
    assert(s_turn.body_sent==received.size());cJSON_Delete(j);
}
static void *no_memory(size_t) {return nullptr;}
int main() {
    node="homelink-test";send_chat("画一只猫","text");verify("画一只猫");
    assert(s_turn.image_requested);
    node=nullptr;send_chat("今天\"天气\"如何？\n用一句话回答。","text");
    verify("今天\"天气\"如何？\n用一句话回答。");assert(!s_turn.image_requested);
    std::string long_text(8192,'x');long_text+="末尾中文";
    send_chat(long_text.c_str(),"text");verify(long_text.c_str());assert(chunks>=3);
    int before=requests;
#if CONFIG_MUSE_BOARD_ATK_DNESP32S3_BOX0
    fail_prefix=true;send_chat("测试","text");assert(failures==1&&requests==before);fail_prefix=false;
#endif
    cJSON_Hooks hooks={no_memory,free};cJSON_InitHooks(&hooks);
    send_chat("测试","text");assert(failures>0&&requests==before);
    cJSON_InitHooks(nullptr);
    return 0;
}
'''
        with tempfile.TemporaryDirectory() as d:
            p = Path(d)
            (p / 'origin.cpp').write_text(code)
            subprocess.run(['cc', '-fsanitize=address,undefined', '-I'+str(jsondir),
                '-c', str(jsondir/'cJSON.c'), '-o', str(p/'json.o')], check=True)
            for box0 in [0, 1]:
                with self.subTest(box0=box0):
                    subprocess.run(['c++', '-std=c++17', '-fsanitize=address,undefined',
                        '-DCONFIG_MUSE_BOARD_ATK_DNESP32S3_BOX0='+str(box0),
                        '-I'+str(jsondir), '-I'+str(ROOT/'components/muse'),
                        str(p/'origin.cpp'), str(p/'json.o'), '-o', str(p/'origin')], check=True)
                    subprocess.run([str(p/'origin')], check=True)
