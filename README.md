# synthetic-utah-to-nwb

A [NeuroConv](https://github.com/catalystneuro/neuroconv) conversion of a simulated walking session
into [NWB](https://www.nwb.org/): raw int16 recordings from four Utah arrays, hip and shoulder
angles, and gait-cycle annotations, written to one file.

The hard part is the channel mapping. Four 64-electrode arrays come out through two 128-channel
pedestal ports, and the rig has one 128-channel headstage that gets moved between ports from block
to block, so channel 1 of a block is a different electrode depending on the port. A wrong port
raises no error. The samples and timestamps are right, the file opens, and every channel sits on
the wrong electrode in the wrong brain area.

The recordings are simulated. The layout and the port problem come from electrophysiology work I've
done in a lab, but no lab data or code is in this repository.

## Input

One folder per session:

| File | Contents |
|---|---|
| `session.json` | Subject, session start, sampling rate, µV/count, and one entry per recording block: file name, port, start time, condition |
| `channel_map.csv` | NSP channel (1–256) → array (1–4), electrode (1–64) |
| `block001.dat`, ... | Raw int16, time-major `(n_samples, 128)`, 30 kHz |
| `kinematics.csv` | Hip and shoulder angles at 1 kHz, NaN where a marker was lost |
| `gait_cycles.csv` | Cycle start/stop times, walking condition, and the lab's manual `exclude` flag |

Port A carries NSP channels 1–128 and port B carries 129–256. The converter takes the port from
`session.json` and never infers it from the signal.

## Output

There is one interface per data stream, a `metadata.yaml` for experiment metadata, and
`convert_session.py` as the entry point. The file it writes:

```text
acquisition/
  ElectricalSeriesBlock001
  ElectricalSeriesBlock002

general/
  devices/
    UtahArray1-4
  extracellular_ephys/
    array1-4
    electrodes                 256 electrode rows

intervals/
  gait_cycles                 start, stop, condition, exclude

processing/
  behavior/
    BehavioralTimeSeries/
      joint_angles             hip and shoulder angles
```

Each electrical series keeps the int16 samples with `conversion = 0.25e-6` V/count, a rate of
30,000 Hz, and its block's start time in session time. The electrodes table has one row per
electrode with its array, electrode name, NSP channel, port, and brain area.

## Design decisions

### The port is required

In a lab analysis pipeline I work on, the channel-mapping code read the port from a per-session
metadata file. When the file was missing, it printed a warning and used port A. One session had no
metadata file and happened to be on port A, so nothing went wrong. A port B session in the same
state would have put all 128 channels on the wrong electrodes, and nothing downstream would have
failed.

Here `UtahBlockRecordingInterface` takes `port: Literal["A", "B"]` with no default, and a block with
a missing or invalid port stops the conversion before anything is written.

### Blocks on the same port share electrode rows

NeuroConv matches electrode rows on group name, electrode name, and channel name. The interface sets
those to the array, the electrode on that array, and the NSP channel, so two blocks recorded on port
A point at the same 128 rows instead of adding 128 more. Brain areas are set once per array in
`metadata.yaml` and copied onto that array's rows, so the ElectrodeGroups and the electrodes table
can't disagree.

### Samples stay int16

The conversion factor is stored on each series and nothing is rescaled on the way in, so the tests
can compare every stored sample with the `.dat` file it came from.

### Gaps and exclusions are kept

Where the tracking system lost a marker, the kinematics are NaN, and they stay NaN in the file.
Whether to interpolate depends on the analysis, so I left it to whoever runs one. Cycles the lab
marked `exclude = True` go into the interval table with the rest, because the curation is part of
the record.

### Block start times keep the exact rate

NeuroConv's default `set_aligned_starting_time` builds a timestamp vector and recomputes the rate
from it, and the second block came out at `30000.000000000004` Hz. The interface overrides it to
shift the SpikeInterface recording's start time instead, so every block is written at exactly
30000.0 Hz with its own `starting_time`. A test checks the rate with `==`.

## Tests

`pytest` simulates a session, converts it, reopens the file, and checks that:

- every int16 sample matches the source `.dat` file
- sampling rate, conversion factor, and block start times are exact
- each block's channels land on the electrodes its port is wired to
- blocks on the same port reference the same electrode rows
- a missing, empty, or invalid port fails before a file is written
- a channel map with a duplicated NSP channel is rejected
- kinematic NaNs are at the same sample positions after conversion
- gait-cycle times, conditions, and exclusion flags match the CSV
- NWB Inspector reports nothing, best-practice suggestions included

## Running the example

```bash
pip install -e ".[test]"
pytest

python -m synthetic_utah_to_nwb.convert_session \
    --out demo_data \
    --block-seconds 10
```

The last command simulates two 10 s blocks and writes
`demo_data/sub-M01_ses-S01_ecephys+behavior.nwb`. With NeuroConv's default compression, 153.6 MB of
int16 input became a 90.3 MB file. The simulated noise is Gaussian and compresses poorly, so real
recordings will come out differently.

## Not covered

- Real Blackrock `.ns6` files. The simulator writes flat binary without NS6 headers. A real
  conversion would start from NeuroConv's `BlackrockRecordingInterface`, and I'd have to check how
  real files represent channel IDs before reusing the port mapping.
- Spike sorting output and LFP.
- Recordings split into several segments. Each block here is its own input.
- DANDI upload and `dandi validate`.

## How it was built

I used Claude Code to write most of the implementation. The constraints it was written to (explicit
port, samples kept as recorded, NaN gaps left alone, curation flags kept) come from problems in the
lab pipeline I work on. Two bugs turned up. A test with 1 s blocks, too short for a single gait
cycle, crashed the simulator, which now returns NaN phase when there are no cycles. The first
converted file had the 30000.000000000004 Hz rate described above, which the exact
rate assertion now covers.

## License

MIT
