/* SPDX-License-Identifier: Apache-2.0 */
#include "muse_audio_dsp.h"
#include <math.h>

/* Hamming-windowed low-pass, normalized DC gain 1; cutoff 6.5 kHz at 48 kHz. */
static const float fir[63] = {
    0.0007777023208f, 0.0003343509003f, -0.0004340016129f, -0.001109907119f, -0.00114940093f, -0.0002201620493f,
    0.001363505723f, 0.002532351263f, 0.002035185076f, -0.0004879633222f, -0.003738328669f, -0.005186209835f,
    -0.00281693435f, 0.002873534492f, 0.008337792267f, 0.008900562191f, 0.002331579985f, -0.008436681715f,
    -0.0160078585f, -0.0131226122f, 0.001405131673f, 0.01977766181f, 0.02849328416f, 0.01704472062f,
    -0.01299224264f, -0.04496365032f, -0.05378619604f, -0.01982456505f, 0.05769325064f, 0.1562776768f,
    0.2387076231f, 0.2707816026f, 0.2387076231f, 0.1562776768f, 0.05769325064f, -0.01982456505f,
    -0.05378619604f, -0.04496365032f, -0.01299224264f, 0.01704472062f, 0.02849328416f, 0.01977766181f,
    0.001405131673f, -0.0131226122f, -0.0160078585f, -0.008436681715f, 0.002331579985f, 0.008900562191f,
    0.008337792267f, 0.002873534492f, -0.00281693435f, -0.005186209835f, -0.003738328669f, -0.0004879633222f,
    0.002035185076f, 0.002532351263f, 0.001363505723f, -0.0002201620493f, -0.00114940093f, -0.001109907119f,
    -0.0004340016129f, 0.0003343509003f, 0.0007777023208f
};

static float filter(muse_audio_fir_t *s, float x)
{
    s->history[s->at] = x;
    float y = 0;
    unsigned p = s->at;
    for (unsigned k=0; k<63; k++) {
        y += fir[k] * s->history[p];
        p = p ? p-1 : 62;
    }
    s->at = (s->at+1)%63;
    return y;
}

static int16_t sample(float x)
{
    if (x > 32767) x = 32767;
    if (x < -32768) x = -32768;
    return (int16_t)lrintf(x);
}

static float limit(float x)
{
    const float threshold=27000, room=4700;
    float a=fabsf(x);
    if (a > threshold) {
        float e=a-threshold;
        a=threshold+room*e/(room+e);
        x=copysignf(a,x);
    }
    return x;
}

size_t muse_audio_decimate48(muse_audio_fir_t *s, const int16_t *in, size_t n, int16_t *out)
{
    size_t got=0;
    for (size_t i=0;i<n;i++) {
        float y=filter(s,in[i]);
        if (++s->phase == 3) { s->phase=0; out[got++]=sample(y); }
    }
    return got;
}

size_t muse_audio_interpolate16(muse_audio_fir_t *s, const int16_t *in, size_t n, int16_t *out)
{
    for (size_t i=0;i<n;i++) {
        out[3*i]=sample(3*filter(s,in[i]));
        out[3*i+1]=sample(3*filter(s,0));
        out[3*i+2]=sample(3*filter(s,0));
    }
    return n*3;
}

void muse_audio_input_gain(muse_audio_gain_t *s, int16_t *pcm, size_t n)
{
    if (!n) return;
    double energy=0;
    for (size_t i=0;i<n;i++) energy+=(double)pcm[i]*pcm[i];
    float rms=sqrtf((float)(energy/n));
    if (!s->gain) s->gain=1;
    /* Don't chase quiet noise. Slow gain increases avoid pumping; attenuation
     * reacts faster. Maximum gain is 3x and the output never hard-clips. */
    float target=rms>200 ? 2600/rms : 1;
    if (target>3) target=3;
    if (target<.5f) target=.5f;
    float rate=target<s->gain ? .4f : .03f;
    s->gain += (target-s->gain)*rate;
    for (size_t i=0;i<n;i++) pcm[i]=sample(limit(pcm[i]*s->gain));
}

void muse_audio_output_process(muse_audio_gain_t *s, int16_t *pcm, size_t n, bool mute)
{
    /* One-pole high-pass at 70 Hz/48 kHz. Ramping is 5 ms, independent of the
     * UI volume; silence resets the next sound's fade-in. */
    const float a=.9909205f, fade_step=.004158f;
    for (size_t i=0;i<n;i++) {
        float x=pcm[i];
        float y=a*(s->hp_y+x-s->hp_x);
        s->hp_x=x;s->hp_y=y;
        if (!pcm[i] && s->silent_frames<480) s->silent_frames++;
        else if (pcm[i]) s->silent_frames=0;
        if (s->silent_frames>=480) s->fade=0;
        float target=mute ? 0 : 1;
        s->fade+=(target-s->fade)*fade_step;
        pcm[i]=sample(limit(y)*s->fade);
    }
}
