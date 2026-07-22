# Activity data

`exercises.json` is the main activity catalog. It organizes activities by level,
sublevel, section, exercise number, and line. Each line supplies the original
`word`, the `answer`, its activity `type`, the printed `removedPart`, and the
printed `replacementPart` when applicable.

The backend uses Azure pronunciation assessment to identify the timed phonemes
in both words. For catalog activities, it infers the contiguous deleted or
substituted phoneme span by comparing those two sequences.

The smaller `deletion.json` and `substitution.json` files remain available for
focused testing. They may provide explicit IPA hints. Use one array entry per
phoneme; the IPA values in `test/phoneme.json` are the reference list.

Deletion example:

```json
{
  "type": "deletion",
  "word": "clamp",
  "answer": "camp",
  "deletedPhonemes": ["l"]
}
```

Substitution example:

```json
{
  "type": "substitution",
  "word": "plucking",
  "answer": "plumming",
  "oldPhonemes": ["k"],
  "newPhonemes": ["m"]
}
```

Multiple phonemes must be contiguous and listed in spoken order. For example,
use `["s", "p"]`, not `["sp"]`. Slashes around an individual IPA value are
accepted but are not needed.

The backend asks Azure pronunciation assessment for the phoneme sequence and
audio timing markers of both words. It validates that the declared change
transforms the original sequence into the answer sequence. If it does not, the
activity endpoint returns an error containing both detected sequences.
