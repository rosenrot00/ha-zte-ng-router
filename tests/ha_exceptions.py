"""Minimal HA exception types for offline entity tests."""


class HomeAssistantError(Exception):
    def __init__(self, *args, translation_domain=None, translation_key=None,
                 translation_placeholders=None):
        super().__init__(*args or (translation_key,))
        self.translation_domain = translation_domain
        self.translation_key = translation_key
        self.translation_placeholders = translation_placeholders


class ServiceValidationError(HomeAssistantError):
    pass
