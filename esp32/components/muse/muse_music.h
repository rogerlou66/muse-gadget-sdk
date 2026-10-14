/* SPDX-License-Identifier: Apache-2.0 */
#pragma once
#include <stdbool.h>
#include <stddef.h>
#include <stdint.h>
#include "sdkconfig.h"
#define MUSE_MUSIC_SONGS 64
#define MUSE_MUSIC_NAME 128

typedef enum { MUSE_MUSIC_SCAN, MUSE_MUSIC_PLAY, MUSE_MUSIC_TOGGLE,
               MUSE_MUSIC_STOP, MUSE_MUSIC_NEXT, MUSE_MUSIC_PREVIOUS } muse_music_action_t;
typedef struct {
    unsigned count, track;
    bool mounted, playing, paused;
    uint32_t revision;
    char name[MUSE_MUSIC_NAME], error[48];
} muse_music_status_t;
#if CONFIG_MUSE_BOARD_ATK_DNESP32S3_BOX0
/* init/poll/stop_owned run only on the voice task; requests/status are thread-safe. */
bool muse_music_init(void);
bool muse_music_request(muse_music_action_t action, unsigned track);
/* Raw middle press: quiet immediately; keep the file for menu resume. */
void muse_music_suspend(void);
void muse_music_interrupt(void);
void muse_music_stop_owned(void);
bool muse_music_busy(void);
size_t muse_music_poll(const int16_t **pcm);
bool muse_music_take_reset(void);
void muse_music_status(muse_music_status_t *out);
void muse_music_name(unsigned track, char *out, size_t cap);
#endif
