# Activity phoneme fields

Every activity supplies the original `word`, the `answer`, and the IPA phonemes
that change. Use one array entry per phoneme. The IPA values in
`test/phoneme.json` are the reference list.

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
