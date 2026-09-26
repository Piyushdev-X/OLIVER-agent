"""OLIVER configuration: layered settings and model selection.

Settings are merged from, lowest to highest precedence:
  1. built-in defaults         orchestrator.DEFAULT_SETTINGS
  2. the configuration file    configuration-files/oliver.toml (or OLIVER_CONFIG)
  3. environment overrides     OLIVER_MODEL, OLIVER_API_BASE
  4. command-line flags        --model, --test-command
Unknown keys and wrong value types in the file are errors, so a typo cannot
silently change a run.

The credential is not a setting: foundation_model.api_key() reads the
AI_API_KEY environment variable whenever a request is made, so it never lands
in settings, run artifacts or logs.
"""

import os

try:
    import tomllib
except ModuleNotFoundError:
    import tomli as tomllib

from oliver import foundation_model
from oliver import orchestrator

SOURCE_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
PROJECT_ROOT = os.path.dirname(SOURCE_ROOT)
DEFAULT_CONFIG = os.path.join(PROJECT_ROOT, 'configuration-files', 'oliver.toml')
RUNS_DIR = os.path.join(PROJECT_ROOT, 'runs')
WORKSPACE_DIR = os.path.join(PROJECT_ROOT, 'workspace')

AUTO_MODEL = 'auto'
AUTO_TABLE = 'auto_models'
ENVIRONMENT_OVERRIDES = [('OLIVER_MODEL', 'model'), ('OLIVER_API_BASE', 'api_base')]
NOT_IN_FILE = ['verbose']

# Key prefixes that identify the provider which issued a key (LiteLLM provider names).
KEY_PREFIXES = [
    ('sk-ant-', 'anthropic'),
    ('sk-or-', 'openrouter'),
    ('sk-proj-', 'openai'),
    ('sk-svcacct-', 'openai'),
    ('AIza', 'gemini'),
    ('gsk_', 'groq'),
    ('xai-', 'xai'),
    ('csk-', 'cerebras'),
]
# Older OpenAI keys start with a bare "sk-", which other providers use too:
# good enough to pick an auto model, too weak to warn about a mismatch.
FALLBACK_PREFIXES = [('sk-', 'openai')]


def environment_value(name):
    return os.environ[name].strip() if name in os.environ else ''


def config_path(explicit):
    if explicit:
        return os.path.abspath(explicit)
    override = environment_value('OLIVER_CONFIG')
    return os.path.abspath(override) if override else DEFAULT_CONFIG


def display_path(path):
    """Paths inside the repository are shown relative to its root."""
    absolute = os.path.abspath(path)
    if absolute == PROJECT_ROOT or absolute.startswith(PROJECT_ROOT + os.sep):
        return os.path.relpath(absolute, PROJECT_ROOT)
    return absolute


def read_config_file(path):
    """Parse the TOML file into (overrides, auto_models)."""
    if not os.path.isfile(path):
        raise ValueError('configuration file not found: ' + path)
    try:
        with open(path, 'rb') as handle:
            data = tomllib.load(handle)
    except tomllib.TOMLDecodeError as error:
        raise ValueError('{0}: {1}'.format(path, error))
    overrides = {}
    auto_models = {}
    for key in data:
        value = data[key]
        if key == AUTO_TABLE:
            if not isinstance(value, dict) or not all(isinstance(value[name], str) for name in value):
                raise ValueError(path + ': [' + AUTO_TABLE + '] must map provider names to model ids')
            auto_models = dict(value)
        elif key in orchestrator.DEFAULT_SETTINGS and key not in NOT_IN_FILE:
            overrides[key] = checked_value(path, key, value)
        else:
            raise ValueError('{0}: unknown setting "{1}"'.format(path, key))
    return overrides, auto_models


def checked_value(path, key, value):
    default = orchestrator.DEFAULT_SETTINGS[key]
    if isinstance(default, bool):
        valid = isinstance(value, bool)
    elif isinstance(default, int):
        valid = isinstance(value, int) and not isinstance(value, bool)
    elif isinstance(default, float):
        valid = isinstance(value, (int, float)) and not isinstance(value, bool)
        if valid:
            value = float(value)
    else:
        valid = isinstance(value, str)
    if not valid:
        raise ValueError('{0}: "{1}" has the wrong type; expected a value like {2!r}'.format(path, key, default))
    return value


def detect_provider(key, include_fallback=True):
    """The provider that issued a key, from its prefix; '' when unknown."""
    prefixes = KEY_PREFIXES + (FALLBACK_PREFIXES if include_fallback else [])
    for prefix, provider in prefixes:
        if key.startswith(prefix):
            return provider
    return ''


def load_settings(path, cli_overrides):
    """Merge every layer and resolve the model. Returns (settings, info).

    info['problem'] is non-empty when no task can run (for example model =
    "auto" without AI_API_KEY); info['warnings'] lists likely mistakes."""
    file_overrides, auto_models = read_config_file(path)
    merged = dict(file_overrides)
    model_source = display_path(path)
    for variable, key in ENVIRONMENT_OVERRIDES:
        value = environment_value(variable)
        if value:
            merged[key] = value
            if key == 'model':
                model_source = variable
    for key in cli_overrides:
        merged[key] = cli_overrides[key]
        if key == 'model':
            model_source = '--model'
    settings = orchestrator.build_settings(merged)

    key = foundation_model.api_key()
    info = {
        'config_path': path,
        'configured_model': settings['model'],
        'model_source': model_source,
        'key_set': bool(key),
        'key_provider': detect_provider(key),
        'problem': '',
        'warnings': [],
    }
    if foundation_model.is_mock_model(settings['model']):
        return settings, info
    if settings['model'] == AUTO_MODEL:
        provider = info['key_provider']
        if not key:
            info['problem'] = ('model = "auto" chooses the model from the provider of AI_API_KEY, '
                               'and AI_API_KEY is not set')
        elif provider not in auto_models:
            info['problem'] = ('the provider of AI_API_KEY is not recognised, so model = "auto" cannot choose; '
                               'set model in ' + display_path(path))
        else:
            settings['model'] = auto_models[provider]
            info['model_source'] += ' ([' + AUTO_TABLE + '] ' + provider + ')'
        return settings, info
    if not key:
        info['problem'] = 'AI_API_KEY is not set'
    elif not settings['api_base']:
        key_provider = detect_provider(key, include_fallback=False)
        model_provider = settings['model'].split('/', 1)[0] if '/' in settings['model'] else ''
        if key_provider and model_provider and key_provider != model_provider:
            info['warnings'].append('AI_API_KEY looks like a {0} key, but the model is {1}'.format(
                key_provider, settings['model']))
    return settings, info
