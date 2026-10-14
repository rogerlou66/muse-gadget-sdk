/* SPDX-License-Identifier: Apache-2.0 */
#include "muse_asr.h"
#include "cJSON.h"
#include <stdio.h>
#include <string.h>
#include <ctype.h>

static bool safe_field(const char *s)
{
    return s && !strchr(s,'\r') && !strchr(s,'\n') && !strstr(s,MUSE_ASR_BOUNDARY);
}

int muse_asr_form(const char *model,const char *language,const char *prompt,char *out,size_t cap)
{
    if (!safe_field(model)||!safe_field(language)||!safe_field(prompt)||!model[0]||!language[0]) return -1;
    int n=snprintf(out,cap,
        "--" MUSE_ASR_BOUNDARY "\r\nContent-Disposition: form-data; name=\"model\"\r\n\r\n%s\r\n"
        "--" MUSE_ASR_BOUNDARY "\r\nContent-Disposition: form-data; name=\"response_format\"\r\n\r\njson\r\n"
        "--" MUSE_ASR_BOUNDARY "\r\nContent-Disposition: form-data; name=\"%s\"\r\n\r\n%s\r\n"
        "--" MUSE_ASR_BOUNDARY "\r\nContent-Disposition: form-data; name=\"prompt\"\r\n\r\n%s\r\n"
        "--" MUSE_ASR_BOUNDARY "\r\nContent-Disposition: form-data; name=\"file\"; filename=\"recording.wav\"\r\nContent-Type: audio/wav\r\n\r\n",
        model,!strcmp(model,"gpt-transcribe")?"languages[]":"language",language,prompt);
    return n<0||(size_t)n>=cap?-1:n;
}

bool muse_asr_parse(const char *json,char *text,size_t cap)
{
    if (!text || !cap) return false;
    text[0]=0;
    cJSON *root=cJSON_Parse(json);
    const char *p=cJSON_GetStringValue(cJSON_GetObjectItem(root,"text"));
    if (p) while (*p && isspace((unsigned char)*p)) p++;
    size_t n=p?strlen(p):0;
    while(n && isspace((unsigned char)p[n-1])) n--;
    bool ok=n>0 && n<cap;
    if (ok) {memcpy(text,p,n);text[n]=0;}
    cJSON_Delete(root);
    return ok;
}

static uint32_t le32(const uint8_t *p) {return (uint32_t)p[0]|(uint32_t)p[1]<<8|(uint32_t)p[2]<<16|(uint32_t)p[3]<<24;}
bool muse_asr_text_empty(const char *json)
{
    cJSON *root=cJSON_Parse(json);
    const char *text=cJSON_GetStringValue(cJSON_GetObjectItem(root,"text"));
    bool empty=text!=NULL;
    if(text)for(const unsigned char *p=(const unsigned char *)text;*p;p++) {
        if(!isspace(*p)) {empty=false;break;}
    }
    cJSON_Delete(root);return empty;
}
bool muse_asr_valid_wav(const uint8_t *p,size_t n)
{
    return p && n>=44+9600 && n<=44+16000*2*30 && !(n&1) && !memcmp(p,"RIFF",4) &&
        !memcmp(p+8,"WAVEfmt ",8) && le32(p+4)==n-8 && le32(p+16)==16 &&
        p[20]==1 && p[21]==0 && p[22]==1 && p[23]==0 && le32(p+24)==16000 &&
        le32(p+28)==32000 && p[32]==2 && p[33]==0 && p[34]==16 && p[35]==0 &&
        !memcmp(p+36,"data",4) && le32(p+40)==n-44;
}
