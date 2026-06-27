breakdown algorithm notes

given original word and answer word
break each word down into sounds (not letters)
label each sound for each word
    original word sounds - 1.1, 2.1, 3.1, etc
    answer word sounds - 1.2, 2.2, 3.2, etc

algorithm steps
for each sound in original word
    compare to first available unmatched sound in answer word (first comparison should always be 1.1 to 1.2)
        if match, pair 1.1 and 1.2 as 'matched' and move on to next original word sound
        if not match, compare to next available unmatched sound in answer word (for example, 1.1 to 2.2)
            insert recusive loop to look for matches in answer word
                if no matches are found, move to next sound in original word

by the end of this loop, there should be sounds that are 'matched' and sounds that are not 'matched'

identify tokens by combining matched sounds and unmatched sounds:
substitution example
    1.1 - 1.2 are matched
    2.1 does not have a match
    2.2 does not have a match
    3.1 - 3.2 are matched
    therefore, 2.1 is the original word unique sound, and 2.2 is the answer word unique sound

2.1 and 2.2 can be passed to arthur so he can say "Now say 'spy' but instead of 'p' say 'k'

deletion example
    1.1 does not have a match
    2.1 - 1.2 are matched
    3.1 - 2.2 are matched
    therefore, 1.1 is the original word unique sound, and there is no answer word unique sound

1.1 can be passed to arthur so he can say "Now say 'can', but don't say 'c'"

matched sounds should be combined into one token, and likewise with unmatched soundd
    example
    1.1 and 1.2 are matched, 2.1 and 2.2 are matched, and 3.1 has no match
    for the frontend with tokens, 1.1 - 2.1 and 2.1 and 2.2 should be one token, and 3.1 should be another that fades out for a deletion exercise