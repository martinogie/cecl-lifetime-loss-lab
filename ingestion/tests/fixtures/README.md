# Test fixtures

Every file here is **synthetic**: made-up loans in Freddie Mac's file layout (Release 48), written
for the unit tests. None of it comes from Freddie's data, which must never be committed.

| File | What it holds |
|---|---|
| `sample_orig_2023.txt` | 3 made-up origination rows (31 columns, pipe-delimited, no header) |
| `sample_perf_2023.txt` | 6 made-up performance rows (35 columns): current months, a disaster-forbearance month, a 90+ day delinquency, a voluntary payoff |
