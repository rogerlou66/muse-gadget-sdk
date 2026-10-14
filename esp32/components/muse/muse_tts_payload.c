/* SPDX-License-Identifier: Apache-2.0 */
#include "muse_tts.h"
#include "cJSON.h"
#include <stdlib.h>
#include <string.h>
#include <math.h>

/* Reply storage can end midway through UTF-8; send only complete characters. */
size_t muse_tts_utf8_prefix(const char *text)
{
    if(!text)return 0;
    const unsigned char *p=(const unsigned char *)text;
    size_t i=0;
    while (p[i]) {
        unsigned c=p[i], n=c<0x80?1:c>=0xc2&&c<=0xdf?2:c>=0xe0&&c<=0xef?3:c>=0xf0&&c<=0xf4?4:0;
        if (!n) break;
        bool valid=true;
        for (unsigned k=1;k<n;k++) if (!p[i+k] || (p[i+k]&0xc0)!=0x80) {valid=false;break;}
        if (!valid) break;
        if ((c==0xe0&&p[i+1]<0xa0)||(c==0xed&&p[i+1]>=0xa0)||
            (c==0xf0&&p[i+1]<0x90)||(c==0xf4&&p[i+1]>=0x90)) break;
        i+=n;
    }
    return i;
}

char *muse_tts_payload(const char *text, const char *model, const char *voice, double speed)
{
    if (!text || !model || !voice || !isfinite(speed) || speed<.25 || speed>4) return NULL;
    size_t n=muse_tts_utf8_prefix(text);
    if (!n) return NULL;
    char *clean=malloc(n+1);
    if (!clean) return NULL;
    memcpy(clean,text,n);clean[n]=0;
    cJSON *root=cJSON_CreateObject();
    bool ok=root && cJSON_AddStringToObject(root,"model",model) &&
        cJSON_AddStringToObject(root,"voice",voice) &&
        cJSON_AddStringToObject(root,"input",clean) &&
        cJSON_AddStringToObject(root,"response_format","mp3") &&
        cJSON_AddNumberToObject(root,"speed",speed);
    char *out=ok?cJSON_PrintUnformatted(root):NULL;
    cJSON_Delete(root);free(clean);
    return out;
}
