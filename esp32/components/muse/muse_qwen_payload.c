/* SPDX-License-Identifier: Apache-2.0 */
#include "muse_qwen_tts.h"
#include "cJSON.h"
#include "muse_tts.h"
#include <math.h>
#include <string.h>

static cJSON *event(const char *type,const char *id)
{
    if(!type || !id || !id[0])return NULL;
    cJSON *j=cJSON_CreateObject();
    if(!j || !cJSON_AddStringToObject(j,"event_id",id) || !cJSON_AddStringToObject(j,"type",type)) {
        cJSON_Delete(j);return NULL;
    }
    return j;
}
static char *print(cJSON *j,bool ok)
{
    char *out=ok?cJSON_PrintUnformatted(j):NULL;cJSON_Delete(j);return out;
}
char *muse_qwen_session(const char *voice,double speed,const char *id)
{
    if(!voice || !voice[0] || !isfinite(speed) || speed<.5 || speed>2)return NULL;
    cJSON *j=event("session.update",id);
    cJSON *s=j?cJSON_AddObjectToObject(j,"session"):NULL;
    bool ok=s && cJSON_AddStringToObject(s,"voice",voice) && cJSON_AddStringToObject(s,"mode","commit") &&
        cJSON_AddStringToObject(s,"language_type","Chinese") && cJSON_AddStringToObject(s,"response_format","mp3") &&
        cJSON_AddNumberToObject(s,"sample_rate",24000) && cJSON_AddNumberToObject(s,"speech_rate",speed);
    return print(j,ok);
}
char *muse_qwen_text(const char *text,const char *id)
{
    if(!text || !text[0])return NULL;
    size_t n=muse_tts_utf8_prefix(text);
    if(!n)return NULL;
    char *clean=cJSON_malloc(n+1);
    if(!clean)return NULL;
    memcpy(clean,text,n);clean[n]=0;
    cJSON *j=event("input_text_buffer.append",id);
    bool ok=j && cJSON_AddStringToObject(j,"text",clean);
    cJSON_free(clean);return print(j,ok);
}
char *muse_qwen_command(const char *type,const char *id)
{
    cJSON *j=event(type,id);return print(j,j!=NULL);
}
int muse_qwen_event(const char *json,char **audio)
{
    *audio=NULL;
    cJSON *j=cJSON_Parse(json);
    const char *type=cJSON_GetStringValue(cJSON_GetObjectItem(j,"type"));
    int result=QWEN_OTHER;
    if(!type)result=QWEN_ERROR;
    else if(!strcmp(type,"session.created"))result=QWEN_CREATED;
    else if(!strcmp(type,"session.updated")) {
        cJSON *s=cJSON_GetObjectItem(j,"session");
        const char *format=cJSON_GetStringValue(cJSON_GetObjectItem(s,"response_format"));
        result=format && !strcmp(format,"mp3")?QWEN_UPDATED:QWEN_ERROR;
    } else if(!strcmp(type,"response.audio.delta")) {
        const char *data=cJSON_GetStringValue(cJSON_GetObjectItem(j,"delta"));
        if(data && data[0]) {
            *audio=cJSON_malloc(strlen(data)+1);
            if(*audio) {strcpy(*audio,data);result=QWEN_AUDIO;} else result=QWEN_ERROR;
        } else result=data?QWEN_OTHER:QWEN_ERROR;
    } else if(!strcmp(type,"response.audio.done"))result=QWEN_AUDIO_DONE;
    else if(!strcmp(type,"session.finished"))result=QWEN_FINISHED;
    else if(!strcmp(type,"error"))result=QWEN_ERROR;
    cJSON_Delete(j);return result;
}
