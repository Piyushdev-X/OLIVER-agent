# merge_config drops nested settings

`config.merge_config({"db": {"host": "localhost", "port": 5432}}, {"db": {"port": 6543}})`
returns `{"db": {"port": 6543}}` and loses `host`. Nested dictionaries must be merged recursively,
and the input dictionaries must not be modified.
