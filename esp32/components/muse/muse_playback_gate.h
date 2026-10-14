/* SPDX-License-Identifier: Apache-2.0 */
#pragma once
#include <stdbool.h>
#include <stddef.h>
#include <stdint.h>

typedef struct {
    uint32_t gen;
    bool initialized, buffering, started;
    unsigned refills;
    size_t target;
} muse_playback_gate_t;

/* Hold live audio until a reserve exists; a completed short clip or final tail
 * must drain even when it is smaller than the reserve. */
static inline bool muse_playback_ready(muse_playback_gate_t *g,uint32_t gen,size_t available,bool pending,int rate)
{
    if(!g->initialized || g->gen!=gen) {
        *g=(muse_playback_gate_t){.gen=gen,.initialized=true,.buffering=true,
            .started=false,.refills=0,.target=(size_t)rate*8/10};
    }
    if(!available && pending && g->started && !g->buffering) {
        g->buffering=true;g->target=(size_t)rate*3/2;g->refills++;
    }
    if(g->buffering && pending && available<g->target) return false;
    if(available) {g->buffering=false;g->started=true;}
    return true;
}
