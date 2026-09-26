# word_count miscounts extra whitespace

`text_stats.word_count("a  b\nc")` returns `3` only by accident of formatting; `"one  two"`
returns `3` and `"   "` returns `4`. Words are separated by any run of whitespace.
