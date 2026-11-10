"""Write a fake client hand-off folder for one walking session.

Layout (what the fictional lab sends us):

    session.json      subject, start time, sampling rate, and one entry per recording block
    channel_map.csv   nsp_channel (1-256) -> array (1-4), electrode (1-64)
    blockNNN.dat      int16, time-major (n_samples, 128), one headstage port per block
    kinematics.csv    time_s + joint angles at 1 kHz, NaN where markers dropped out
    gait_cycles.csv   start_s, stop_s, condition, exclude

Everything is simulated.
"""

import json
from pathlib import Path

import numpy as np
import pandas as pd

CHANNELS_PER_PORT = 128
ELECTRODES_PER_ARRAY = 64
PORT_OFFSET = {"A": 0, "B": 128}


def gait_phase(t, starts, stops):
    # phase in [0, 1) inside an annotated cycle, NaN outside one
    phase = np.full(t.shape, np.nan)
    if starts.size == 0:
        return phase
    idx = np.searchsorted(starts, t, side="right") - 1
    inside = (idx >= 0) & (t < stops[np.clip(idx, 0, None)])
    k = idx[inside]
    phase[inside] = (t[inside] - starts[k]) / (stops[k] - starts[k])
    return phase


def _channel_map(rng):
    # each port's pedestal carries two arrays; connector wiring is not in electrode order
    rows = []
    for port, offset in PORT_OFFSET.items():
        arrays = (1, 2) if port == "A" else (3, 4)
        pairs = np.column_stack(
            [np.repeat(arrays, ELECTRODES_PER_ARRAY), np.tile(np.arange(1, ELECTRODES_PER_ARRAY + 1), 2)]
        )[rng.permutation(CHANNELS_PER_PORT)]
        rows.append(
            pd.DataFrame(
                {
                    "nsp_channel": np.arange(1, CHANNELS_PER_PORT + 1) + offset,
                    "array": pairs[:, 0],
                    "electrode": pairs[:, 1],
                }
            )
        )
    return pd.concat(rows, ignore_index=True)


def _cycles(rng, t0, t1, condition):
    durations = rng.normal(1.1, 0.08, size=int((t1 - t0) / 0.8) + 2)
    edges = t0 + np.concatenate([[0.0], np.cumsum(durations)])
    edges = edges[edges <= t1]
    return pd.DataFrame({"start_s": edges[:-1], "stop_s": edges[1:], "condition": condition})


def _spike_waveform_uv(fs):
    k = np.arange(int(round(1e-3 * fs)))  # 1 ms
    return -60.0 * (np.exp(-(((k - 8) / 3.0) ** 2)) - 0.4 * np.exp(-(((k - 16) / 5.0) ** 2)))


def make_session(session_dir, ports=("A", "B"), block_s=2.0, gap_s=3.0, fs=30_000.0, uv_per_bit=0.25, seed=0):
    rng = np.random.default_rng(seed)
    session_dir = Path(session_dir)
    session_dir.mkdir(parents=True, exist_ok=True)

    _channel_map(rng).to_csv(session_dir / "channel_map.csv", index=False)

    conditions = ("treadmill_slow", "treadmill_fast", "overground", "obstacle")
    blocks = [
        dict(
            file=f"block{i + 1:03d}.dat",
            port=port,
            start_s=i * (block_s + gap_s),
            condition=conditions[i % len(conditions)],
        )
        for i, port in enumerate(ports)
    ]

    cycles = pd.concat(
        [_cycles(rng, b["start_s"], b["start_s"] + block_s, b["condition"]) for b in blocks], ignore_index=True
    )
    cycles["exclude"] = rng.random(len(cycles)) < 0.1  # stand-in for the lab's manual bad-cycle sheet
    cycles.to_csv(session_dir / "gait_cycles.csv", index=False)
    starts, stops = cycles["start_s"].to_numpy(), cycles["stop_s"].to_numpy()

    t_kin = np.arange(0.0, blocks[-1]["start_s"] + block_s, 1e-3)  # (n_kin,)
    phi = gait_phase(t_kin, starts, stops)
    walking = ~np.isnan(phi)
    hip = np.where(walking, 20 + 15 * np.cos(2 * np.pi * np.nan_to_num(phi)), 20.0) + rng.normal(0, 0.5, t_kin.size)
    shoulder = np.where(walking, 10 + 25 * np.sin(2 * np.pi * np.nan_to_num(phi)), 10.0) + rng.normal(
        0, 0.5, t_kin.size
    )
    hip[500:550] = np.nan  # 50 ms marker dropout
    pd.DataFrame({"time_s": t_kin, "hip_deg": hip, "shoulder_deg": shoulder}).to_csv(
        session_dir / "kinematics.csv", index=False, float_format="%.4f"
    )

    waveform = _spike_waveform_uv(fs)
    preferred_phase = rng.uniform(0, 2 * np.pi, CHANNELS_PER_PORT)  # (128,)
    n = int(block_s * fs)
    for b in blocks:
        phi_b = gait_phase(b["start_s"] + np.arange(n) / fs, starts, stops)[:, None]  # (n, 1)
        rate_hz = np.where(
            np.isnan(phi_b), 15.0, 15.0 * (1 + 0.8 * np.cos(2 * np.pi * np.nan_to_num(phi_b) - preferred_phase))
        )
        spikes = rng.random((n, CHANNELS_PER_PORT), dtype=np.float32) < rate_hz / fs  # (n, 128)
        uv = rng.normal(0, 8.0, (n, CHANNELS_PER_PORT)).astype(np.float32)
        for lag, w in enumerate(waveform):  # convolve spikes with the waveform, one lag at a time
            uv[lag:] += w * spikes[: n - lag]
        np.clip(np.round(uv / uv_per_bit), -32768, 32767).astype("<i2").tofile(session_dir / b["file"])

    info = dict(
        session_id="S01",
        subject_id="M01",
        session_start_time="2026-03-02T10:15:00-05:00",
        sampling_frequency_hz=fs,
        uv_per_bit=uv_per_bit,
        blocks=blocks,
    )
    (session_dir / "session.json").write_text(json.dumps(info, indent=2))
    return session_dir
