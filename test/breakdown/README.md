# Audio Breakdown Prototype

This prototype compares two generated word recordings using audio only.

The typed words are only used to create TTS WAV files. After that, the script
does not use spelling rules or letter-to-sound rules. It analyzes the waveform,
aligns the two recordings, and clips detected regions.

Run substitution breakdowns with two spoken words:

```powershell
python .\substitution_breakdown.py cat bat
python .\substitution_breakdown.py spy sky
python .\substitution_breakdown.py plucking plumming
```

Run deletion breakdowns with the full original word and the answer after the
sound is removed:

```powershell
python .\deletion_breakdown.py spill pill
python .\deletion_breakdown.py sailboat sail
python .\deletion_breakdown.py heading head
```

Or run either script with no arguments and type words into the prompts:

```powershell
python .\substitution_breakdown.py
python .\deletion_breakdown.py
```

Outputs go to:

```text
test\breakdown\output
```

Current approach:

- Uses Windows built-in TTS to generate full-word WAV files.
- Extracts audio features from short frames.
- Uses dynamic time warping style alignment to compare the recordings.
- Splits the alignment into numbered sound regions.
- Matching regions write one shared clip, such as `sound1.wav`.
- Discrepancy regions write one clip for each word with the same sound number,
  such as `cat_sound1.wav` and `bat_sound1.wav`.
- Deletion regions write the omitted original audio, such as
  `sailboat_sound2.wav`.

This is a rough experiment. It works best when the generated TTS clearly speaks
the sound being removed or changed. Very short final consonants can still be
hard to detect because the TTS may barely pronounce them.
