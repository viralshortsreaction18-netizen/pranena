"""Numerical prototype of the on-device MDX-Net STFT/iSTFT/overlap-add pipeline.
Mirrors, line for line, the Kotlin that will ship in SimpMusic."""
import numpy as np

N_FFT, HOP, DIM_F, DIM_T = 6144, 1024, 2048, 256
TRIM = N_FFT // 2
CHUNK = HOP * (DIM_T - 1)          # 261120
GEN = CHUNK - 2 * TRIM             # 254976
OVERLAP = 0.25
STEP = int((1 - OVERLAP) * CHUNK)  # 195840

def hann(n, periodic=True):
    if periodic:
        return 0.5 - 0.5 * np.cos(2 * np.pi * np.arange(n) / n)
    return np.hanning(n)

# ---- radix-2 FFT (mirror of Fft.kt) ----
def fft_pow2(x):
    n = len(x)
    if n == 1:
        return x.astype(np.complex128)
    ev = fft_pow2(x[0::2]); od = fft_pow2(x[1::2])
    t = np.exp(-2j * np.pi * np.arange(n // 2) / n) * od
    return np.concatenate([ev + t, ev - t])

# ---- 6144 = 3 * 2048, one mixed-radix stage (mirror of MdxFft.kt) ----
def fft6144(x):
    N, M = N_FFT, N_FFT // 3
    A = [fft_pow2(x[j::3]) for j in range(3)]
    k = np.arange(N)
    out = np.zeros(N, dtype=np.complex128)
    for j in range(3):
        out += np.exp(-2j * np.pi * j * k / N) * A[j][k % M]
    return out

def ifft6144(x):
    return np.conj(fft6144(np.conj(x))) / N_FFT

# sanity of the mixed-radix FFT against numpy
rng = np.random.default_rng(0)
t = rng.standard_normal(N_FFT) + 1j * rng.standard_normal(N_FFT)
print("fft6144 max err:", np.abs(fft6144(t) - np.fft.fft(t)).max())
print("ifft6144 max err:", np.abs(ifft6144(fft6144(t)) - t).max())

# ---- STFT: center=True, hann periodic -> [4, DIM_F, DIM_T] like the model input ----
win = hann(N_FFT)
def stft(wave):                       # wave: [2, CHUNK]
    pad = N_FFT // 2
    p = np.zeros((2, CHUNK + 2 * pad)); p[:, pad:pad + CHUNK] = wave
    frames = 1 + CHUNK // HOP
    assert frames == DIM_T, frames
    out = np.zeros((4, DIM_F, frames))
    for f in range(frames):
        s = f * HOP
        for c in range(2):
            spec = fft6144(p[c, s:s + N_FFT] * win)[:DIM_F]
            out[2 * c, :, f] = spec.real
            out[2 * c + 1, :, f] = spec.imag
    out[:, :3, :] = 0.0               # audio-separator zeroes the first 3 bins
    return out

def istft(spec):                      # spec: [4, DIM_F, DIM_T] -> [2, CHUNK]
    frames = spec.shape[2]
    n_bins = N_FFT // 2 + 1
    pad = np.zeros((4, n_bins - DIM_F, frames)); spec = np.concatenate([spec, pad], axis=1)
    out = np.zeros((2, CHUNK))
    for c in range(2):
        comp = spec[2 * c] + 1j * spec[2 * c + 1]
        # istft with center=True, hop, hann, wsq=False, normalised by window sum
        buf = np.zeros(CHUNK + 2 * (N_FFT // 2))
        wsum = np.zeros_like(buf)
        for f in range(frames):
            fr = np.fft.irfft(comp[:, f], n=N_FFT)
            buf[f * HOP:f * HOP + N_FFT] += fr * win
            wsum[f * HOP:f * HOP + N_FFT] += win ** 2
        buf = np.where(wsum > 1e-8, buf / np.maximum(wsum, 1e-8), 0.0)
        out[c, :] = buf[N_FFT // 2:N_FFT // 2 + CHUNK]
    return out

# ---- streaming chunker (mirror of MdxVocalExtractor) ----
def separate_stream(mix, model):
    """mix: [2, n]. model: [4, DIM_F, DIM_T] -> [4, DIM_F, DIM_T]"""
    n = mix.shape[1]
    pad = GEN + TRIM - (n % GEN)
    mixture = np.concatenate([np.zeros((2, TRIM)), mix, np.zeros((2, pad))], axis=1)
    result = np.zeros((2, mixture.shape[1]))
    divider = np.zeros((2, mixture.shape[1]))
    for i in range(0, mixture.shape[1], STEP):
        end = min(i + CHUNK, mixture.shape[1])
        chunk = mixture[:, i:end]
        if end - i < CHUNK:
            chunk = np.concatenate([chunk, np.zeros((2, CHUNK - (end - i)))], axis=1)
        w = np.hanning(end - i)
        pred = istft(model(stft(chunk)))
        pred[:, :end - i] *= w
        divider[:, i:end] += w
        result[:, i:end] += pred[:, :end - i]
    out = result / np.maximum(divider, 1e-8)
    out = out[:, TRIM:out.shape[1] - TRIM]
    return out[:, :n]

# ---- verification 1: identity model must give the mix back ----
sr = 44100
n = 44100 * 8
tt = np.arange(n) / sr
mix = np.stack([0.5 * np.sin(2 * np.pi * 440 * tt) + 0.3 * np.sin(2 * np.pi * 660 * tt),
                0.5 * np.sin(2 * np.pi * 440 * tt) + 0.2 * np.sin(2 * np.pi * 990 * tt)])
identity = lambda spec: spec
rec = separate_stream(mix, identity)
err = np.abs(rec - mix).max()
print("identity roundtrip max err:", round(float(err), 6), " rel:", round(float(err / np.abs(mix).max()), 8))
mid = slice(4410, n - 4410)
print("identity roundtrip err, middle only:", round(float(np.abs(rec[:, mid] - mix[:, mid]).max()), 8),
      " rms:", round(float(np.sqrt(((rec[:, mid] - mix[:, mid]) ** 2).mean())), 8))
print("first/last chunk-ish errors:", round(float(np.abs(rec[:, :2000] - mix[:, :2000]).max()), 5),
      round(float(np.abs(rec[:, -2000:] - mix[:, -2000:]).max()), 5))

# ---- verification 2: a band-pass 'model' removes the low music tone ----
def bandpass_model(spec):
    # multiply each complex bin by a mask: keep 300..4000 Hz, kill below 200 Hz
    freqs = np.arange(DIM_F) * (sr / N_FFT)
    m = ((freqs >= 300) & (freqs <= 4000)).astype(float)
    out = spec.copy()
    for c in range(2):
        out[2 * c] *= m[:, None]; out[2 * c + 1] *= m[:, None]
    return out
music = 0.6 * np.sin(2 * np.pi * 110 * tt)          # bass line
vocal = 0.5 * np.sin(2 * np.pi * 700 * tt) * (0.5 + 0.5 * np.sin(2 * np.pi * 2 * tt))
mix2 = np.stack([music + vocal, music + vocal])
out2 = separate_stream(mix2, bandpass_model)
print("110Hz music energy in ->", round(float(np.abs(np.fft.rfft(mix2[0])[np.argmin(abs(np.fft.rfftfreq(n, 1/sr)-110))])), 2),
      "| out ->", round(float(np.abs(np.fft.rfft(out2[0])[np.argmin(abs(np.fft.rfftfreq(n, 1/sr)-110))])), 2))
print("700Hz vocal energy in ->", round(float(np.abs(np.fft.rfft(mix2[0])[np.argmin(abs(np.fft.rfftfreq(n, 1/sr)-700))])), 2),
      "| out ->", round(float(np.abs(np.fft.rfft(out2[0])[np.argmin(abs(np.fft.rfftfreq(n, 1/sr)-700))])), 2))
