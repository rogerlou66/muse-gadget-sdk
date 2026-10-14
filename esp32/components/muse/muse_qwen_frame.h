/* SPDX-License-Identifier: Apache-2.0 */
#pragma once
#include <stdbool.h>
#include <stddef.h>
#include <string.h>
typedef struct {
    size_t used,left;
    bool open,fin;
} muse_qwen_frame_t;
/* Reassemble transport read chunks and RFC6455 continuation frames. Control
 * frames are handled by ESP-IDF's transport and must not enter this function. */
static inline int muse_qwen_frame_add(muse_qwen_frame_t *s,int opcode,bool fin,size_t frame_len,
    const char *data,size_t n,char *out,size_t cap)
{
    if(!s->left) {
        if(opcode==1) {if(s->open)return -1;s->open=true;s->used=0;}
        else if(opcode!=0 || !s->open)return -1;
        s->left=frame_len;s->fin=fin;
    }
    if(n>s->left || s->used+n>=cap)return -1;
    memcpy(out+s->used,data,n);s->used+=n;s->left-=n;
    if(!s->left && s->fin) {out[s->used]=0;s->open=false;return 1;}
    return 0;
}
