# Legacy acoustic breakdown

This package contains the original waveform-change segmentation and matching
pipeline. It is intentionally preserved for experimentation, but it is no
longer called by `activity_breakdown.prepare_activity()`.

The active implementation is `app/services/phoneme_breakdown.py`. It uses
Azure pronunciation-assessment phoneme timings plus the explicit phoneme fields
from the activity JSON files.
