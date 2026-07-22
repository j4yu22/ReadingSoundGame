from __future__ import annotations

import unittest

from app.services.breakdown.models import AudioData
from app.services.phoneme_breakdown import (
    TimedPhoneme,
    WordPhonemeBreakdown,
    build_deletion_regions,
    build_substitution_regions,
    infer_deletion_phonemes,
    infer_substitution_phonemes,
    marker_payload,
    normalize_phoneme,
    parse_phoneme_sequence,
    timed_phonemes_from_details,
)
from app.services.text_to_speech import DialogueError


def word_breakdown(word: str, symbols: list[str]) -> WordPhonemeBreakdown:
    sample_rate = 1000
    first_sample = 100
    samples_per_phoneme = 100
    last_sample = first_sample + len(symbols) * samples_per_phoneme
    audio = AudioData(
        label=word,
        wav_bytes=b"",
        sample_rate=sample_rate,
        samples=[0.0] * (last_sample + 100),
        trim_start=first_sample,
        trim_end=last_sample,
    )
    phonemes = tuple(
        TimedPhoneme(
            symbol=symbol,
            start_sample=first_sample + index * samples_per_phoneme,
            end_sample=first_sample + (index + 1) * samples_per_phoneme,
        )
        for index, symbol in enumerate(symbols)
    )
    return WordPhonemeBreakdown(word=word, audio=audio, phonemes=phonemes)


class PhonemeBreakdownTests(unittest.TestCase):
    def test_parses_slash_wrapped_phoneme_sequences(self) -> None:
        self.assertEqual(
            parse_phoneme_sequence(["/k/", "/s/"], "oldPhonemes"),
            ("k", "s"),
        )

    def test_normalizes_common_ipa_aliases(self) -> None:
        self.assertEqual(normalize_phoneme("g"), normalize_phoneme("ɡ"))
        self.assertEqual(normalize_phoneme("r"), normalize_phoneme("ɹ"))

    def test_builds_three_regions_for_middle_deletion(self) -> None:
        original = word_breakdown("clamp", ["k", "l", "æ", "m", "p"])
        answer = word_breakdown("camp", ["k", "æ", "m", "p"])

        regions = build_deletion_regions(original, answer, ("l",))

        self.assertEqual([region.role for region in regions], ["match", "deletion", "match"])
        self.assertEqual(regions[0].original_sound_labels, ("k",))
        self.assertEqual(regions[1].original_sound_labels, ("l",))
        self.assertEqual(regions[1].answer_sound_labels, ())
        self.assertEqual(regions[2].original_sound_labels, ("æ", "m", "p"))

    def test_builds_three_regions_for_middle_substitution(self) -> None:
        original = word_breakdown("plucking", ["p", "l", "ʌ", "k", "ɪ", "ŋ"])
        answer = word_breakdown("plumming", ["p", "l", "ʌ", "m", "ɪ", "ŋ"])

        regions = build_substitution_regions(original, answer, ("k",), ("m",))

        self.assertEqual(
            [region.role for region in regions],
            ["match", "discrepancy", "match"],
        )
        self.assertEqual(regions[0].original_sound_labels, ("p", "l", "ʌ"))
        self.assertEqual(regions[1].original_sound_labels, ("k",))
        self.assertEqual(regions[1].answer_sound_labels, ("m",))
        self.assertEqual(regions[2].original_sound_labels, ("ɪ", "ŋ"))

    def test_infers_deleted_phonemes_from_word_sequences(self) -> None:
        original = word_breakdown("birthday", ["b", "er", "th", "d", "ay"])
        answer = word_breakdown("day", ["d", "ay"])

        self.assertEqual(
            infer_deletion_phonemes(original, answer),
            ("b", "er", "th"),
        )

    def test_infers_substitution_phonemes_from_word_sequences(self) -> None:
        original = word_breakdown("wood", ["w", "oo", "d"])
        answer = word_breakdown("good", ["g", "oo", "d"])

        self.assertEqual(
            infer_substitution_phonemes(original, answer),
            (("w",), ("g",)),
        )

    def test_supports_deletions_at_word_edges(self) -> None:
        cases = (
            ("spill", ["s", "p", "ɪ", "l"], "pill", ["p", "ɪ", "l"], "s"),
            ("blurt", ["b", "l", "ɝ", "t"], "blur", ["b", "l", "ɝ"], "t"),
        )

        for original_word, original_symbols, answer_word, answer_symbols, deleted in cases:
            with self.subTest(original=original_word, answer=answer_word):
                regions = build_deletion_regions(
                    word_breakdown(original_word, original_symbols),
                    word_breakdown(answer_word, answer_symbols),
                    (deleted,),
                )
                self.assertEqual(len(regions), 2)
                self.assertIn("deletion", [region.role for region in regions])

    def test_supports_substitutions_at_word_edges(self) -> None:
        cases = (
            ("cat", ["k", "æ", "t"], "bat", ["b", "æ", "t"], "k", "b"),
            ("bat", ["b", "æ", "t"], "bag", ["b", "æ", "ɡ"], "t", "ɡ"),
        )

        for original_word, original_symbols, answer_word, answer_symbols, old, new in cases:
            with self.subTest(original=original_word, answer=answer_word):
                regions = build_substitution_regions(
                    word_breakdown(original_word, original_symbols),
                    word_breakdown(answer_word, answer_symbols),
                    (old,),
                    (new,),
                )
                self.assertEqual(len(regions), 2)
                self.assertIn("discrepancy", [region.role for region in regions])

    def test_rejects_hint_that_does_not_transform_words(self) -> None:
        original = word_breakdown("cat", ["k", "æ", "t"])
        answer = word_breakdown("bat", ["b", "æ", "t"])

        with self.assertRaises(DialogueError):
            build_substitution_regions(original, answer, ("t",), ("b",))

    def test_reads_azure_offsets_as_audio_markers(self) -> None:
        audio = AudioData(
            label="at",
            wav_bytes=b"",
            sample_rate=1000,
            samples=[0.0] * 500,
            trim_start=50,
            trim_end=350,
        )
        details = {
            "NBest": [
                {
                    "Words": [
                        {
                            "Phonemes": [
                                {"Phoneme": "æ", "Offset": 500_000, "Duration": 1_000_000},
                                {"Phoneme": "t", "Offset": 1_500_000, "Duration": 2_000_000},
                            ]
                        }
                    ]
                }
            ]
        }

        phonemes = timed_phonemes_from_details(details, audio)
        breakdown = WordPhonemeBreakdown("at", audio, phonemes)

        self.assertEqual(
            [(item.symbol, item.start_sample, item.end_sample) for item in phonemes],
            [("æ", 50, 150), ("t", 150, 350)],
        )
        self.assertEqual(
            marker_payload(breakdown),
            [
                {"phoneme": "æ", "startMs": 50, "endMs": 150},
                {"phoneme": "t", "startMs": 150, "endMs": 350},
            ],
        )


if __name__ == "__main__":
    unittest.main()
