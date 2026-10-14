#pragma once

#include <stdbool.h>
#include <stdint.h>

/* Debounced edges in, mutually disambiguated gestures out. Unsigned time
 * differences keep the deadlines valid across the millisecond counter wrap. */
enum {
    MUSE_GESTURE_SINGLE = 1u,
    MUSE_GESTURE_DOUBLE = 2u,
    MUSE_GESTURE_LONG = 4u,
    MUSE_GESTURE_DOUBLE_MS = 350,
    MUSE_GESTURE_LONG_MS = 1000,
};

typedef struct {
    bool down, pending, second, long_sent;
    uint32_t pressed_at, released_at;
} muse_button_gestures_t;

static inline unsigned muse_button_gestures_step(muse_button_gestures_t *s,
                                                 bool press, bool release, uint32_t now)
{
    unsigned action = 0;
    if (s->pending && !s->down && (uint32_t)(now - s->released_at) >= MUSE_GESTURE_DOUBLE_MS) {
        s->pending = false;
        action |= MUSE_GESTURE_SINGLE;
    }
    if (press && !s->down) {
        s->second = s->pending;
        s->pending = false;
        s->down = true;
        s->long_sent = false;
        s->pressed_at = now;
    }
    if (s->down && !s->long_sent && (uint32_t)(now - s->pressed_at) >= MUSE_GESTURE_LONG_MS) {
        s->long_sent = true;
        s->pending = s->second = false;
        action |= MUSE_GESTURE_LONG;
    }
    if (release && s->down) {
        s->down = false;
        if (!s->long_sent) {
            if (s->second) {
                s->second = false;
                action |= MUSE_GESTURE_DOUBLE;
            } else {
                s->pending = true;
                s->released_at = now;
            }
        }
    }
    return action;
}
