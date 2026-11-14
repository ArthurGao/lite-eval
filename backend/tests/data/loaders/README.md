# Loader samples

Hand-written, fictional files in the shape of the official benchmark files, for the loader tests.
No official data is committed here (LongMemEval is MIT but 3 GB; LoCoMo is CC BY-NC 4.0).

- `longmemeval_sample.json`: the shape of `longmemeval_s_cleaned.json` (a list of questions with
  `haystack_sessions`; `_abs` question ids are abstention questions).
- `locomo_sample.json`: the shape of `data/locomo10.json` (a list of samples with `qa` and
  `conversation`; category 5 has `adversarial_answer` and no `answer`; answers may be numbers).
