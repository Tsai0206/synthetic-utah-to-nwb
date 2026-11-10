# synthetic-utah-to-nwb

A [NeuroConv](https://github.com/catalystneuro/neuroconv) conversion of one simulated walking
session, recorded with four Utah arrays, into [NWB](https://www.nwb.org/). It is laid out the
way a lab-to-NWB conversion repo usually is: one interface per data stream, a metadata YAML, a
`convert_session` entry point, and tests that check the written file against the source.

The data are simulated because the recordings this is modeled on belong to the lab that collected
them and are not public. The hand-off format and the way it fails are modeled on that real setup.

## The hand-off

The fictional client lab sends one folder per session:

| File | Contents |
|---|---|
| `session.json` | subject, session start time, sampling rate, µV per bit, and one entry per recording block (file, **port**, start time, condition) |
| `channel_map.csv` | `nsp_channel` (1–256) → `array` (1–4), `electrode` (1–64) |
| `block001.dat`, … | int16, time-major `(n_samples, 128)`, 30 kHz |
| `kinematics.csv` | `time_s`, `hip_deg`, `shoulder_deg` at 1 kHz; NaN where markers dropped out |
| `gait_cycles.csv` | `start_s`, `stop_s`, `condition`, `exclude` (the lab's manual curation flag) |

The awkward part is the hardware. Four 64-electrode arrays come out through two 128-channel
pedestal ports, but the rig has one 128-channel headstage, which gets moved between ports from
block to block. Every block file therefore has local channels 1–128, and which electrodes those
are depends on the port. Port B adds 128 to the acquisition-system channel number.

## What ends up in the file

```
acquisition/
  ElectricalSeriesBlock001   int16 (n, 128), conversion 2.5e-7 V/count, starting_time 0.0
  ElectricalSeriesBlock002   int16 (n, 128), starting_time = block start in session time
general/
  devices/                   UtahArray1-4, each linked to a UtahArray8x8 DeviceModel
  extracellular_ephys/
    array1-4                 ElectrodeGroups, one per array, with location
    electrodes               256 rows: group, location, electrode_name, channel_name, port, nsp_channel
intervals/
  gait_cycles                start/stop, condition, exclude
processing/behavior/
  BehavioralTimeSeries/joint_angles   (n, 2) degrees, 1 kHz, NaN gaps kept
```

## Design decisions

**The port is required and never inferred.** `UtahBlockRecordingInterface` takes
`port: Literal["A", "B"]` with no default, and `session_to_nwb` names the file and refuses to run if
a block's port is missing or invalid. A wrong port does not crash anything. It puts all 128 channels on
the wrong electrodes, in the wrong brain area, and every downstream figure is still drawn. In the lab
pipeline I maintain, the port came from a per-session metadata file and fell back to port A, with a
printed warning, when the file was missing. One session had no metadata file and was on port A anyway,
so nothing went wrong, but nothing in the code would have noticed if it had been on port B.

**Electrode rows are shared across blocks.** NeuroConv decides whether a channel already has a row in
the electrodes table from `(group_name, electrode_name, channel_name)`. Setting those to the array, the
electrode on that array, and the acquisition-system channel means two blocks recorded on the same port
reference the same 128 rows instead of creating 256. `test_same_port_shares_electrodes`
checks this.

**Location is set per array, once.** Each array's brain area is written in `metadata.yaml` on its
ElectrodeGroup and copied to that array's electrode rows at write time, so the group and the table
cannot disagree.

**Raw counts, not volts.** Traces stay int16 with `conversion = 0.25e-6`, and the tests compare
every sample with the source file bit for bit.

**Gaps stay gaps.** Marker dropouts are written as NaN, and the series description records how many
samples are missing per joint. Interpolating them is an analysis choice, and the converter should not
make it for the lab.

**Gait cycles are their own interval table.** They are not task trials, so they go in
`intervals/gait_cycles` rather than `trials`. Cycles the lab marked as bad keep their row with
`exclude = True` instead of being dropped, because the curation itself is data.

**Block start times without re-derived rates.** NeuroConv's default `set_aligned_starting_time`
builds a full timestamp vector and computes the rate back from it. Block 2 came out at
30000.000000000004 Hz. The interface overrides it to shift SpikeInterface's `t_start` instead, so
every block is written with `rate = 30000.0` and a `starting_time`.

## Checks

`pytest` builds a session, converts it, and checks:

- every sample of every block matches its `.dat` file; `conversion`, `rate` and `starting_time` are exact
- each block's electrodes are the ones its port is wired to, row by row against `channel_map.csv`
- two blocks on the same port share electrode rows (128 rows, not 256)
- a block with a missing, empty, or unknown port stops the conversion before a file is written
- the interface has no default port
- a channel map with a duplicated NSP channel is rejected
- NaN gaps in the kinematics are in the same places after conversion
- gait-cycle times, conditions and curation flags match the CSV
- NWB Inspector reports nothing, including best-practice suggestions

## Running it

```bash
pip install -e ".[test]"
pytest
python -m synthetic_utah_to_nwb.convert_session --out demo_data --block-seconds 10
```

The last command simulates a session with two 10 s blocks and writes
`demo_data/sub-M01_ses-S01_ecephys+behavior.nwb`. With NeuroConv's default compression, 153.6 MB of
int16 input became a 90.3 MB file. The simulated noise is Gaussian and compresses badly, so real
recordings will compress differently.

## Not covered

- Real Blackrock `.ns6` files. A real conversion would start from NeuroConv's
  `BlackrockRecordingInterface`; this one reads a flat binary because the simulator does not write NS6
  headers. The port and channel-map handling would carry over unchanged.
- Spike sorting output (`Units`), LFP, and files split into several segments.
- DANDI upload and `dandi validate`.

## How this was built

The code was drafted with Claude Code, a coding agent. The constraints (explicit port, lossless
counts, NaN gaps left alone, curation flags kept) come from an audit of the real pipeline described
above. The test suite caught one bug in the agent's code: the simulator crashed when a block was
shorter than one gait cycle. The rate drift was found by reading the first converted file; it comes
from NeuroConv's base class, and an exact-rate assertion now pins the fix.

## License

MIT
