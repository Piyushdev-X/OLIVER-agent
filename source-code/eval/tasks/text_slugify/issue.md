# slugify() produces broken slugs

`text_utils.slugify("Hello, World!")` returns `"Hello,-World!"`. Slugs must be lowercase,
contain only letters and digits separated by single hyphens, and ignore extra whitespace
(`"  Ready   Set  Go "` should become `"ready-set-go"`).
